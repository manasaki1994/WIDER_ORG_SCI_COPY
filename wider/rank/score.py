"""Anchor-based ranking: score = mean of each record's top-N cell cosines. Sorts, never filters."""
from __future__ import annotations

import hashlib
import json

import numpy as np

from ..construct import artifacts
from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow
from .corpus import corpus_texts, embed_texts, load_corpus_embeddings


def score_matrix(E: np.ndarray, A: np.ndarray, top_n: int) -> tuple[np.ndarray, np.ndarray]:
    S = E @ A.T
    k = min(top_n, S.shape[1])
    top = -np.partition(-S, k - 1, axis=1)[:, :k] if k < S.shape[1] else S
    return S, top.mean(axis=1)


def rank(project: Project, set_name: str | None = None, anchors_version: int | None = None,
         top_n: int | None = None, name: str | None = None, current: bool = True) -> dict:
    """Rank the corpus. A re-rank adds a version and never changes an earlier one (ui-redesign D5): version 1 is
    stored under ``name``, later ones as ``<name>@v<N>``, and a version with the same name and input hash is reused,
    so samples keep the version they were drawn from and an unchanged rerun adds nothing. ``current=False``
    (ablation) leaves the default ranking and the rank stage result alone, and a changed ablation goes under an
    ablation-only name (:func:`wider.rank.versions.store_version`)."""
    from . import versions as rv

    set_name = set_name or project.config.default_embedding
    row, data = artifacts.load(project, "anchors", anchors_version)
    if row is None:
        raise ValueError("no anchors artifact; propose or import one first")
    if top_n is None:  # the protocol default is capped at the number of cells; a value the researcher gives must fit
        top_n = min(project.config.thresholds.top_n_cells, len(data["anchors"]))
    if not 1 <= top_n <= len(data["anchors"]):
        raise ValueError(f"top-N is the number of cell similarities averaged per record: 1 to {len(data['anchors'])} "
                         f"for these anchors (got {top_n})")
    if row["status"] == "draft":
        pass  # ranking with a draft is allowed for ablation; the status is recorded
    cells = list(data["anchors"].keys())
    ids, E = load_corpus_embeddings(project, set_name)
    A = embed_texts(project, set_name, [data["anchors"][c] for c in cells])
    S, score = score_matrix(E, A, top_n)
    best = S.argmax(axis=1)
    order = np.argsort(-score, kind="stable")
    ranks = np.empty(len(order), dtype=np.int64)
    ranks[order] = np.arange(1, len(order) + 1)
    base = name or f"{set_name}__anchors_v{row['version']}__top{top_n}"
    md5 = hashlib.md5(np.ascontiguousarray(score).tobytes()).hexdigest()
    inputs = rv.ranking_parts(project, set_name, row["version"])
    ih = rv.input_hash(inputs)
    st = project.store
    with st.tx() as c:   # the check and the insert in one transaction
        rid, name, version, reused = rv.store_version(c, base, ih, current, {
            "set_name": set_name, "anchors_version": row["version"], "top_n": top_n, "n": len(ids), "md5": md5,
            "created_at": utcnow(), "note": f"anchors {row['status']}", "embedding_model": inputs["model"]})
        if not reused:
            c.executemany("INSERT INTO ranking_rows(ranking_id,record_id,rank,score,best_cell,cell_scores) VALUES(?,?,?,?,?,?)",
                          [(rid, int(ids[i]), int(ranks[i]), float(score[i]), cells[int(best[i])],
                            json.dumps({cells[j]: round(float(S[i, j]), 4) for j in range(len(cells))})) for i in range(len(ids))])
            c.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                      (rv.INPUTS.format(rid=rid), json.dumps(inputs, default=str)))
    earlier = [v for v in rv.versions(project, rv.base_of(project, name)) if v["ranking_id"] != rid and v["samples"]]
    kept = [{"version": v["version"], "samples": v["samples"]} for v in earlier]
    out = {"ranking_id": rid, "name": name, "base_name": rv.base_of(project, name), "version": version,
           "reused": reused, "n": len(ids), "cells": cells, "md5": md5, "top_n": top_n,
           "anchors_version": row["version"], "set_name": set_name, "samples_keep": kept}
    if current:
        st.set_meta("default_ranking", name)
        from ..core.stage_result import record
        from ..core.stage_summaries import summarize_rank
        record(project, summarize_rank(out))
    return out


def keep_note(res: dict) -> str | None:
    """The note a re-rank prints when samples were drawn from earlier versions: they keep their version."""
    if not res.get("samples_keep"):
        return None
    parts = [f"{' and '.join(k['samples'])} keep{'s' if len(k['samples']) == 1 else ''} version {k['version']}"
             for k in res["samples_keep"]]
    return "Samples keep the version they were drawn from: " + "; ".join(parts) + "."


def pct_hits(ranks: list[int], n_set: int, n_rows: int, pcts) -> list[dict]:
    """How many of the ranks fall within the top ``pct`` percent of a ranking of ``n_rows`` (rank floor(pct/100 * n),
    at least 1), as "k of n" with n the set's size (validation-sample-planner D3)."""
    out = []
    for pct in pcts:
        depth = max(1, int(pct * n_rows // 100))
        out.append({"pct": float(pct), "rank": depth, "k": sum(1 for r in ranks if r <= depth), "n": n_set})
    return out


def validate(project: Project, ranking_name: str | None = None, top_n_compare=None, include_validation: bool | None = None,
             seen_choice: str | None = None, members: list | None = None) -> dict:
    """Where each benchmark paper lands. Validation papers only when the set is unlocked (or asked for explicitly).

    Asking for them while the set is hidden, with ``validation_after_freeze`` on, is a look as in coverage
    (:func:`wider.search.coverage.look`): before the first counting unlock it needs ``seen_choice`` (raises
    ``SeenChoiceNeeded``, nothing recorded); the result says ``validation_peeked`` and gives the ``notice``. The
    ablation passes the ``members`` it read before its one look (:func:`wider.search.coverage.set_members`), so its
    rankings are validated on the same papers and no further look is recorded."""
    rp = project.config.protocol.ranking
    top_n_compare = tuple(top_n_compare or rp.top_n_compare)
    from ..search.coverage import USED, former_ids, look, row_flags, set_members, validation_unlocked
    from .versions import resolve

    st = project.store
    name = resolve(project, ranking_name)
    r = st.one("SELECT * FROM rankings WHERE name=?", (name,))
    if r is None:
        raise KeyError("no ranking; run wider rank run first")
    unlocked = validation_unlocked(project)
    if include_validation is None:
        include_validation = unlocked
    peeked = bool(members is None and include_validation and not unlocked
                  and project.config.protocol.identification.validation_after_freeze)
    if members is None:
        members = set_members(project, include_validation)   # the sets as they were when asked for (D3)
    notice = look(project, seen_choice) if peeked else None
    # the used validation papers get a summary entry of their own, apart (validation-reuse D1)
    sets = ["Development"] + (["Validation"] if include_validation else []) + [USED]
    former = former_ids(project)
    group = {b.benchmark_id: s for s, b in members}
    papers = [b for _, b in members]
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in st.query("SELECT record_id,title,doi FROM records")]
    matches = {m.benchmark_id: m for m in locate_benchmark([(b.benchmark_id, b.title, b.doi) for b in papers], corpus,
                                                            project.config.thresholds.jw_benchmark)}
    # recompute alternative top-N ranks from stored cell scores
    rows = st.query("SELECT record_id, rank, score, best_cell, cell_scores FROM ranking_rows WHERE ranking_id=? ORDER BY rank", (r["ranking_id"],))
    cell_mat = np.array([list(json.loads(x["cell_scores"]).values()) for x in rows])
    rec_ids = [x["record_id"] for x in rows]
    alt_ranks = {}
    n_cells = cell_mat.shape[1]
    # a comparison above the number of cells is scored as the mean of all cells, and the result says so
    notes = [f"top-{n} is more than the {n_cells} cells of these anchors, so it is compared as top-{n_cells}"
             for n in top_n_compare if n > n_cells]
    for n in top_n_compare:
        k = min(n, cell_mat.shape[1])
        sc = -np.partition(-cell_mat, k - 1, axis=1)[:, :k].mean(axis=1) if k < cell_mat.shape[1] else cell_mat.mean(axis=1)
        order = np.argsort(-sc, kind="stable")
        rk = np.empty(len(order), dtype=np.int64)
        rk[order] = np.arange(1, len(order) + 1)
        alt_ranks[n] = dict(zip(rec_ids, rk.tolist()))
    by_id = {x["record_id"]: x for x in rows}
    out_rows = []
    for b in papers:
        m = matches[b.benchmark_id]
        s = group[b.benchmark_id]
        head = {"benchmark_id": b.benchmark_id, "set": s, **row_flags(s, b, former)}
        if m.record_id is None:
            out_rows.append({**head, "title": b.title, "found": False})
            continue
        rid = int(m.record_id)
        x = by_id[rid]
        out_rows.append({**head, "title": b.title, "found": True, "matched_by": m.method,
                         "record_id": rid, "rank": x["rank"], "pct": round(x["rank"] / len(rows), 4), "best_cell": x["best_cell"],
                         **{f"rank_top{n}": alt_ranks[n][rid] for n in top_n_compare}})
    found = [x for x in out_rows if x["found"]]
    summary = {}
    for s in sets:
        ss = [x for x in found if x["set"] == s]
        if ss:
            rks = sorted(x["rank"] for x in ss)
            summary[s] = {"n": sum(1 for x in out_rows if x["set"] == s), "found": len(ss), "median_rank": float(np.median(rks)),
                          "max_rank": max(rks), **{f"recall_at_{k}": sum(1 for x in rks if x <= k) for k in rp.recall_at},
                          "recall_at_pct": pct_hits(rks, sum(1 for x in out_rows if x["set"] == s), len(rows),
                                                    rp.recall_at_pct)}
    return {"ranking": name, "n": len(rows), "validation_included": include_validation, "validation_peeked": peeked,
            "notice": notice, "rows": out_rows, "summary": summary,
            "notes": notes,
            "top_n_compare": {n: float(np.median([alt_ranks[n][x["record_id"]] for x in found])) if found else None for n in top_n_compare}}


def explorer(project: Project, ranking_name: str | None = None, offset: int = 0, limit: int = 50, cell: str | None = None) -> dict:
    from .versions import ABLATION, base_of, page_review, resolve, versions

    st = project.store
    name = resolve(project, ranking_name)
    r = st.one("SELECT * FROM rankings WHERE name=?", (name,))
    if r is None:
        return {"ranking": None, "rows": [], "n_rows": 0}
    sql = ("SELECT rr.rank, rr.score, rr.best_cell, rr.cell_scores, m.record_id, m.title, m.year, m.journal, m.doi, m.abstract_words, m.thin, m.platforms "
           "FROM ranking_rows rr JOIN records m ON m.record_id=rr.record_id WHERE rr.ranking_id=? ")
    params: list = [r["ranking_id"]]
    if cell:
        sql += "AND rr.best_cell=? "
        params.append(cell)
    n_rows = st.scalar("SELECT COUNT(*) FROM ranking_rows rr WHERE rr.ranking_id=? " + ("AND rr.best_cell=?" if cell else ""), params)
    sql += "ORDER BY rr.rank LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = [dict(x) for x in st.query(sql, params)]
    for x in rows:
        x["cell_scores"] = json.loads(x["cell_scores"])
    counts = {x["best_cell"]: x["n"] for x in st.query("SELECT best_cell, COUNT(*) n FROM ranking_rows WHERE ranking_id=? AND rank<=? GROUP BY best_cell", (r["ranking_id"], project.config.protocol.ranking.cluster_top))}
    one = st.one("SELECT cell_scores FROM ranking_rows WHERE ranking_id=? LIMIT 1", (r["ranking_id"],))
    cells = {c: counts.get(c, 0) for c in (json.loads(one["cell_scores"]) if one else {})}
    cells.update({c: n for c, n in counts.items() if c not in cells})
    return {"ranking": dict(r), "rows": rows, "n_rows": n_rows, "best_cell_top3000": cells, "top_pool": min(project.config.protocol.ranking.cluster_top, r["n"]),
            "rankings": [dict(x) for x in st.query("SELECT * FROM rankings ORDER BY created_at")],
            # ui-redesign D5: the base names new work can choose (each its newest version) and this one's history
            "bases": [x["base_name"] for x in st.query(
                "SELECT base_name, MIN(ranking_id) first FROM rankings WHERE base_name IS NOT NULL "
                f"AND base_name NOT LIKE '%{ABLATION}' GROUP BY base_name ORDER BY first")],
            "versions": (vs := versions(project, base_of(project, name))),
            # the versions that need review, and the note a re-rank prints, said before the run
            "review": page_review(project),
            "keep_before": keep_note({"samples_keep": [{"version": v["version"], "samples": v["samples"]}
                                                       for v in vs if v["samples"]]})}


def corpus_overview(project: Project) -> dict:
    ids, texts, thin = corpus_texts(project)
    return {"records": len(ids), "thin": sum(thin), "sets": [dict(r) for r in project.store.query("SELECT * FROM embedding_sets")]}
