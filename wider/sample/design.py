"""Sampling for human labelling.

Probability sample: rank bands (S1 census, lower bands systematic with known inclusion probability),
frame = ranked records minus thin abstracts minus benchmark papers; sorted by cluster then rank inside the
clustered region so the draw spreads across topics. Weight = N_h / n_h. Only this sample feeds the recall estimate.

Purposive development set: likely includes per cell, near misses found by the near_miss artifact texts,
topical coverage per cluster (medoid + 1), thin records, tail records. Used to build the prompt only.

Both are labelled in one shuffled queue with rank, score, cell, cluster and set hidden until the labels are locked.
"""
from __future__ import annotations

import hashlib
import json
import random

import numpy as np

from ..construct import artifacts
from ..construct.adjacent import approved_literatures, embedding_text
from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow
from ..rank.corpus import embed_texts, load_corpus_embeddings


def _ranking(project: Project, ranking_name: str | None):
    """The ranking new work uses: the default, or the newest version of a base name (ui-redesign D5)."""
    from ..rank.versions import resolve

    st = project.store
    name = resolve(project, ranking_name)
    r = st.one("SELECT * FROM rankings WHERE name=?", (name,))
    if r is None:
        raise ValueError("no ranking; run wider rank run first")
    return dict(r)


def _frame(project: Project, rk: dict) -> tuple[list[dict], set[int], dict[int, int]]:
    st = project.store
    rows = [dict(x) for x in st.query(
        "SELECT rr.record_id, rr.rank, rr.best_cell, rr.cell_scores, m.thin FROM ranking_rows rr JOIN records m ON m.record_id=rr.record_id "
        "WHERE rr.ranking_id=? ORDER BY rr.rank", (rk["ranking_id"],))]
    bench = project.read_benchmark()
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in st.query("SELECT record_id,title,doi FROM records")]
    bench_ids = {int(m.record_id) for m in locate_benchmark([(b.benchmark_id, b.title, b.doi) for b in bench], corpus,
                                                            project.config.thresholds.jw_benchmark) if m.record_id}
    return rows, bench_ids, cluster_labels(project, rk["name"])


def _cluster_set(project: Project, ranking_name: str) -> dict | None:
    """The stored clustering of a ranking (meta ``clusters:<ranking>:<k>``); with several, the last key in meta order,
    the one draws have always used. A ranking version without its own clustering uses its base name's (ui-redesign
    D5); the result's ``ranking`` names the version it was made on."""
    from ..rank.versions import base_of

    st = project.store
    keys = [x["key"] for x in st.query("SELECT key FROM meta WHERE key LIKE 'clusters:%'")]
    for name in dict.fromkeys((ranking_name, base_of(project, ranking_name))):
        found = None
        for k in keys:
            if k.startswith(f"clusters:{name}:"):
                found = k
        if found:
            return json.loads(st.get_meta(found))
    return None


def clustering_note(project: Project, ranking_name: str) -> str | None:
    """When a draw uses the clustering of another version of its ranking: which version it was made on."""
    from ..rank.versions import version_label

    res = _cluster_set(project, ranking_name)
    made_on = (res or {}).get("ranking")
    if not made_on or made_on == ranking_name:
        return None
    return (f"The research areas come from the clustering made on {version_label(project, made_on)} of this ranking "
            f"(this draw uses {version_label(project, ranking_name)}).")


def cluster_labels(project: Project, ranking_name: str) -> dict[int, int]:
    """record id -> research area (cluster number) of a ranking's clustering; empty when the ranking has none."""
    res = _cluster_set(project, ranking_name)
    return {int(a): b for a, b in res["labels"].items()} if res else {}


def cluster_terms(project: Project, ranking_name: str) -> dict[int, list[str]]:
    """cluster number -> its top terms, where the clustering stored them (design D4)."""
    res = _cluster_set(project, ranking_name) or {}
    return {int(c["cluster"]): list(c.get("terms") or []) for c in res.get("clusters") or [] if "cluster" in c}


def sys_sample(ids: list, n: int | None, rng: random.Random) -> list:
    N = len(ids)
    if n is None or n >= N:
        return list(ids)
    k = N / n
    start = rng.uniform(0, k)
    return [ids[int(start + i * k)] for i in range(n)]


def probability_frame(project: Project, rk: dict, exclude=()) -> dict:
    """The frame of the probability sample (one helper for the draw, the plan, the extension and the fresh sample,
    so they count the same N_h): the ranked records minus thin abstracts, benchmark papers and the ids in
    ``exclude``. Returns {rows (all ranked rows), frame, bench_ids, clusters, n_ranked, thin_excluded,
    excluded}."""
    rows, bench_ids, clusters = _frame(project, rk)
    ex = {int(x) for x in exclude}
    base = [x for x in rows if not x["thin"] and x["record_id"] not in bench_ids]
    frame = [x for x in base if x["record_id"] not in ex]
    return {"rows": rows, "frame": frame, "bench_ids": bench_ids, "clusters": clusters, "n_ranked": len(rows),
            "thin_excluded": sum(1 for x in rows if x["thin"]), "excluded": len(base) - len(frame)}


def resolve_bands(bands, n_ranked: int) -> tuple[list[dict], list[str]]:
    """Bands as ranks: [{name, start, end, n, start_pct, end_pct}] and the notes for dropped bands. A band given as
    percentiles starts at floor(start_pct/100 x n)+1 and ends at floor(end_pct/100 x n), the last at n; one that
    resolves to no rank is dropped with a note. Rank bands pass through (end None = to the end)."""
    out, notes = [], []
    for i, b in enumerate(bands):
        if getattr(b, "is_pct", False):
            start = int(b.start_pct / 100 * n_ranked) + 1
            end = n_ranked if i == len(bands) - 1 else int(b.end_pct / 100 * n_ranked)
            if end < start:
                notes.append(f"band {b.name} ({b.start_pct:g}–{b.end_pct:g}%) holds no rank of {n_ranked} and is "
                             "left out")
                continue
        else:
            start, end = b.start, b.end
        out.append({"name": b.name, "start": start, "end": end, "n": b.n,
                    "start_pct": getattr(b, "start_pct", None), "end_pct": getattr(b, "end_pct", None)})
    return out, notes


def in_band(rank: int, b: dict) -> bool:
    return rank >= b["start"] and (b["end"] is None or rank <= b["end"])


FRESH_ONLY = "--name and --fresh-from draw a probability sample only; use `wider sample draw probability`"
IN_USE = "{name} is now the probability sample used for recall (protocol.sampling.probability_sample)"
READS = "recall reads {name} (protocol.sampling.probability_sample)"
REDRAW_KEEPS = "{name} was drawn fresh from {used}; the redraw leaves out the same records"
DRAW_LABELLED = "{name} already has labels; its draw cannot be undone"


FRAME_EXCLUDED = "frame_excluded"


def frame_excluded(project: Project, sample: str) -> set[int]:
    """The records the draw or the last extension of probability sample ``sample`` left out of its frame
    (recall-final-check-fixes D1-D3): its cells count the band minus these, so N equals the design's N_h. A sample
    stored without the list takes it from the samples that existed then (D3): every other sample created at or before
    the last extension, else the purposive samples and its fresh chain created before it; nothing is written."""
    st = project.store
    s = st.one("SELECT sample_id, design, created_at FROM samples WHERE name=?", (sample,))
    if s is None:
        return set()
    design = json.loads(s["design"] or "{}")
    if FRAME_EXCLUDED in design:
        return {int(x) for x in design[FRAME_EXCLUDED]}
    ext = design.get("extensions") or []
    if ext:
        sids = [r["sample_id"] for r in st.query("SELECT sample_id FROM samples WHERE sample_id<>? AND created_at<=?",
                                                 (s["sample_id"], ext[-1]["at"]))]
    else:
        chain = set(fresh_chain(project, design["fresh_from"])) if design.get("fresh_from") else set()
        sids = [r["sample_id"] for r in st.query(
            "SELECT sample_id, name, kind FROM samples WHERE sample_id<>? AND (created_at<? OR "
            "(created_at=? AND sample_id<?))", (s["sample_id"], s["created_at"], s["created_at"], s["sample_id"]))
            if r["kind"] == "purposive" or r["name"] in chain]
    if not sids:
        return set()
    return {int(r["record_id"]) for r in st.query(
        f"SELECT DISTINCT record_id FROM sample_rows WHERE sample_id IN ({','.join('?' * len(sids))})", sids)}


def development_records(project: Project) -> set[int]:
    """The records of every purposive (development) sample."""
    return {int(r["record_id"]) for r in project.store.query(
        "SELECT DISTINCT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        "WHERE s.kind='purposive'")}


def fresh_chain(project: Project, used: str) -> list[str]:
    """``used`` and every sample it was itself drawn fresh from (D9), nearest first."""
    chain, cur = [], used
    while cur and cur not in chain:
        s = project.store.one("SELECT kind, design FROM samples WHERE name=?", (cur,))
        if s is None:
            raise KeyError(f"no sample {cur}")
        if s["kind"] != "probability":
            raise ValueError(f"{cur} is not a probability sample: a fresh sample replaces a probability sample")
        chain.append(cur)
        cur = json.loads(s["design"] or "{}").get("fresh_from")
    return chain


def draw_probability(project: Project, ranking_name: str | None = None, name: str | None = None, seed: int | None = None,
                     cluster_max_rank: int | None = None, fresh_from: str | None = None,
                     author: str | None = None) -> dict:
    """The band-stratified probability sample (systematic within each band). Every draw leaves out the development
    records (Q5); ``fresh_from`` (D9) also leaves out that sample and its chain of predecessors and makes the new
    sample the one recall reads. A redraw onto a fresh sample keeps its chain. Journalled as ``sample_draw``."""
    from ..core import journal

    sp = project.config.protocol.sampling
    name = name or sp.probability_sample
    cluster_max_rank = cluster_max_rank or project.config.protocol.ranking.cluster_top
    seed = project.config.seed if seed is None else seed
    st = project.store
    messages = []
    if fresh_from is None:   # a redraw onto a fresh sample keeps its exclusions
        old = st.one("SELECT design FROM samples WHERE name=? AND kind='probability'", (name,))
        prev = json.loads(old["design"] or "{}").get("fresh_from") if old else None
        if prev:
            fresh_from = prev
            messages.append(REDRAW_KEEPS.format(name=name, used=prev))
    elif fresh_from == name:
        raise ValueError(f"a fresh sample needs a new name, not {name}: use --name")
    chain = fresh_chain(project, fresh_from) if fresh_from else []
    if name in chain:
        raise ValueError(f"{name} is in the chain of {fresh_from}: use a new name")
    used = {int(r["record_id"]) for r in st.query(
        f"SELECT DISTINCT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        f"WHERE s.name IN ({','.join('?' * len(chain))})", chain)} if chain else set()
    dev = development_records(project)
    rng = random.Random(seed)
    rk = _ranking(project, ranking_name)
    pf = probability_frame(project, rk, dev | used)
    base = probability_frame(project, rk)["frame"]
    rows, bench_ids, clusters, frame = pf["rows"], pf["bench_ids"], pf["clusters"], pf["frame"]
    bands, band_notes = resolve_bands(project.config.bands, len(rows))
    picked = []
    log_bands = []
    for b in bands:
        f = [x for x in frame if in_band(x["rank"], b)]
        if b["end"] is not None and b["end"] <= cluster_max_rank and clusters:
            f.sort(key=lambda x: (clusters.get(x["record_id"], 10**6), x["rank"]))
        else:
            f.sort(key=lambda x: x["rank"])
        got = sys_sample([x["record_id"] for x in f], b["n"], rng)
        w = len(f) / len(got) if got else None
        picked += [(rid, b["name"], w, next(x["rank"] for x in f if x["record_id"] == rid)) for rid in got]
        entry = {"band": b["name"], "start": b["start"], "end": b["end"], "N_h": len(f), "n_h": len(got), "weight": w}
        if b["start_pct"] is not None:
            entry.update(start_pct=b["start_pct"], end_pct=b["end_pct"])
        log_bands.append(entry)
    ids = {x["record_id"] for x in base}
    log = {"bands": log_bands, "frame": len(frame), "thin_excluded": sum(x["thin"] for x in rows),
           "benchmark_excluded": len(bench_ids), "development_excluded": len(ids & dev),
           "sorted_by": "cluster then rank inside the clustered region" if clusters else "rank",
           **({"band_notes": band_notes} if band_notes else {})}
    if chain:
        log.update(fresh_from=fresh_from, chain=chain, excluded_used=len(ids & (used - dev)))
    log[FRAME_EXCLUDED] = sorted(ids & (dev | used))   # the cells' population (recall-final-check-fixes D1); never shown
    summary = f"drew {name} fresh from {fresh_from}" if fresh_from else f"drew {name}"
    with journal.recording(project, "sample_draw", summary, author=author) as rec:
        rec.rows("samples", "name=?", [name])
        rec.rows("sample_rows", "sample_id IN (SELECT sample_id FROM samples WHERE name=?)", [name])
        rec.file("project.yaml")
        out = _store_sample(project, name, "probability", rk, picked, seed, log)
        if chain and sp.probability_sample != name:
            project.config.protocol = project.config.protocol.set("sampling.probability_sample", name)
            project.save()
    if chain:
        messages.append(IN_USE.format(name=name))
    elif name != project.config.protocol.sampling.probability_sample:
        messages.append(READS.format(name=project.config.protocol.sampling.probability_sample))
    out["design"] = {k: v for k, v in out["design"].items() if k != FRAME_EXCLUDED}
    return {**out, "development_excluded": log["development_excluded"], "messages": messages,
            "change_id": rec.change_id}


def draw_block(project: Project, items: list) -> str | None:
    """Why a draw (D9) cannot be undone now: the drawn sample has a label or an adjudication."""
    for it in items:
        if it["t"] == "rows" and it.get("table") == "samples" and it["where"] == "name=?":
            s = project.store.one("SELECT sample_id FROM samples WHERE name=?", it["params"])
            if s and any(project.store.scalar(f"SELECT COUNT(*) FROM {t} WHERE sample_id=?", (s["sample_id"],))
                         for t in ("labels", "adjudications")):
                return DRAW_LABELLED.format(name=it["params"][0])
    return None


def draw_development(project: Project, ranking_name: str | None = None, name: str | None = None, seed: int | None = None,
                     per_cell: int | None = None, per_near_miss: int | None = None, near_miss_pool: int | None = None,
                     near_miss_max_rank: int | None = None, per_cluster: int | None = None, n_thin: int | None = None,
                     n_tail: int | None = None, cluster_max_rank: int | None = None, exclude_sample: str | None = None,
                     per_adjacent: int | None = None, per_boundary: int | None = None) -> dict:
    """Purposive development set; every quota defaults to ``protocol.sampling`` (the original review's pools).
    ``per_adjacent`` marginal cases are added per approved adjacent literature (pool ``adjacent:<key>``); none without one.
    Last, ``per_boundary`` boundary cases per near-miss entry and approved literature (D8): drawn after every other
    pool from the same ``rng``, so the other pools keep their records for a seed."""
    sp = project.config.protocol.sampling
    name = name or sp.development_sample
    exclude_sample = exclude_sample or sp.probability_sample
    per_cell = sp.per_cell if per_cell is None else per_cell
    per_near_miss = sp.per_near_miss if per_near_miss is None else per_near_miss
    near_miss_pool = sp.near_miss_pool if near_miss_pool is None else near_miss_pool
    near_miss_max_rank = sp.near_miss_max_rank if near_miss_max_rank is None else near_miss_max_rank
    per_cluster = sp.per_cluster if per_cluster is None else per_cluster
    n_thin = sp.n_thin if n_thin is None else n_thin
    n_tail = sp.n_tail if n_tail is None else n_tail
    per_adjacent = sp.per_adjacent if per_adjacent is None else per_adjacent
    per_boundary = sp.per_boundary if per_boundary is None else per_boundary
    cluster_max_rank = cluster_max_rank or project.config.protocol.ranking.cluster_top
    seed = project.config.seed if seed is None else seed
    rng = random.Random(seed + 1)
    rk = _ranking(project, ranking_name)
    rows, bench_ids, clusters = _frame(project, rk)
    st = project.store
    taken = set(bench_ids)
    taken |= {x["record_id"] for x in st.query(   # every probability sample, not only the one in use (D9)
        "SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        "WHERE s.kind='probability' OR s.name=?", (exclude_sample,))}
    by_id = {x["record_id"]: x for x in rows}
    picked: list[tuple] = []

    def take(ids, pool, sub):
        for rid in ids:
            if rid not in taken:
                taken.add(rid)
                picked.append((rid, f"{pool}:{sub}", None, by_id[rid]["rank"]))

    cells = list(json.loads(rows[0]["cell_scores"]).keys()) if rows else []
    for cell in cells:
        cand = sorted((x for x in rows if not x["thin"] and x["record_id"] not in taken),
                      key=lambda x: -json.loads(x["cell_scores"])[cell])[:sp.pool_size]
        own = [x["record_id"] for x in cand if x["best_cell"] == cell]
        rest = [x["record_id"] for x in cand if x["best_cell"] != cell]
        rng.shuffle(own)
        rng.shuffle(rest)
        take((own + rest)[:per_cell], "likely_include", cell)
    nm_row, nm = artifacts.load(project, "near_miss")
    nm_log = None
    entries: list[tuple[str, str, dict]] = []   # (kind, key, record id -> similarity) for the boundary pool
    if nm:
        texts = [e["text"] for e in nm["near_miss"]]
        NM = embed_texts(project, rk["set_name"], texts)
        ids, E = load_corpus_embeddings(project, rk["set_name"])
        pos = {int(i): j for j, i in enumerate(ids)}
        S = E @ NM.T
        for j, e in enumerate(nm["near_miss"]):
            cand = [x for x in rows if x["rank"] <= near_miss_max_rank and not x["thin"] and x["record_id"] not in taken]
            cand.sort(key=lambda x: -S[pos[x["record_id"]], j])
            pool = [x["record_id"] for x in cand[:near_miss_pool]]
            take(rng.sample(pool, min(per_near_miss, len(pool))), "near_miss", e["key"])
            entries.append(("near_miss", e["key"], {rid: float(S[k, j]) for rid, k in pos.items()}))
        nm_log = {"version": nm_row["version"], "categories": [e["key"] for e in nm["near_miss"]]}
    adj_log = None
    # similarities to approved literatures serve the adjacent pool and the boundary pool alike (D8)
    lits = approved_literatures(project) if per_adjacent > 0 or per_boundary > 0 else []
    if lits:  # marginal cases per approved adjacent literature: nearest records to its description within the near-miss depth
        A = embed_texts(project, rk["set_name"], [embedding_text(e) for e in lits])
        ids, E = load_corpus_embeddings(project, rk["set_name"])
        pos = {int(i): j for j, i in enumerate(ids)}
        S = E @ A.T
        for j, e in enumerate(lits):
            entries.append(("adjacent", e["key"], {rid: float(S[k, j]) for rid, k in pos.items()}))
            if per_adjacent <= 0:
                continue
            cand = [x for x in rows if x["rank"] <= near_miss_max_rank and not x["thin"] and x["record_id"] not in taken]
            cand.sort(key=lambda x: -S[pos[x["record_id"]], j])
            take([x["record_id"] for x in cand[:per_adjacent]], "adjacent", e["key"])
        if per_adjacent > 0:
            adj_row, _ = artifacts.load(project, "adjacent")
            adj_log = {"version": adj_row["version"], "keys": [e["key"] for e in lits], "max_rank": near_miss_max_rank}
    if clusters:
        for c in sorted(set(clusters.values())):
            mem = sorted((x for x in rows if clusters.get(x["record_id"]) == c and not x["thin"] and x["record_id"] not in taken),
                         key=lambda x: x["rank"])
            if not mem or per_cluster < 1:   # a quota of 0 takes no cluster
                continue
            medoid = mem[0]["record_id"]
            others = [x["record_id"] for x in mem[1:]]
            take([medoid] + rng.sample(others, min(per_cluster - 1, len(others))), "topical_coverage", str(c))
    thin_ids = [x["record_id"] for x in rows if x["thin"] and x["record_id"] not in taken]
    take(rng.sample(thin_ids, min(n_thin, len(thin_ids))), "no_abstract", "thin")
    tail = [x["record_id"] for x in rows if x["rank"] > cluster_max_rank and not x["thin"] and x["record_id"] not in taken]
    take(rng.sample(tail, min(n_tail, len(tail))), "clearly_irrelevant", "tail")
    got = draw_boundary(rows, taken, entries, per_boundary, near_miss_pool, near_miss_max_rank, rng)
    picked += [(rid, pool, None, by_id[rid]["rank"]) for rid, pool in got]
    note = clustering_note(project, rk["name"]) if clusters else None
    out = _store_sample(project, name, "purposive", rk, picked, seed,
                         {"per_cell": per_cell, "per_near_miss": per_near_miss, "near_miss_pool": near_miss_pool,
                          "per_cluster": per_cluster, "n_thin": n_thin, "n_tail": n_tail, "near_miss": nm_log,
                          "per_adjacent": per_adjacent, "adjacent": adj_log, "per_boundary": per_boundary,
                          "boundary": boundary_log(entries, per_boundary, got),
                          "clusters": len(set(clusters.values())) if clusters else 0,
                          **({"clustering_note": note} if note else {})})
    out["messages"] = ([EMPTY_DEVELOPMENT] if not out["n"] else []) + ([note] if note else [])
    return out


# a small corpus can leave nothing once the validation sample and the seed papers are out (inspection 1 Nov 2026)
EMPTY_DEVELOPMENT = ("The development sample is empty: no record was left once the validation sample and the seed "
                     "papers were taken out, so there is nothing to try the model on before the whole corpus. Screening "
                     "goes on without it (trying the model on a development sample is optional). To have one, set "
                     "fewer records per band for the validation sample and draw both samples again, or draw the "
                     "development sample first (wider sample draw development), which the validation sample then "
                     "leaves out.")

NO_BOUNDARY_ENTRIES = "no near-miss or adjacent entries"


def _percentile(values: list[float]) -> np.ndarray:
    """Rank percentile in (0, 1] of each value among ``values`` (the highest gets 1; ties keep their order)."""
    order = np.argsort(np.argsort(np.asarray(values, dtype=float), kind="stable"), kind="stable")
    return (order + 1) / max(len(values), 1)


def draw_boundary(rows: list[dict], taken: set, entries: list[tuple[str, str, dict]], per_boundary: int,
                  near_miss_pool: int, near_miss_max_rank: int, rng: random.Random) -> list[tuple[int, str]]:
    """D8: per entry (near-miss entry or approved adjacent literature), the untaken records with an abstract within
    ``near_miss_max_rank``, scored by the lower of two rank percentiles (best cell score, similarity to the entry);
    ``per_boundary`` at random from the ``near_miss_pool`` best. Adds the picks to ``taken``; returns (record id, pool)."""
    out: list[tuple[int, str]] = []
    if per_boundary <= 0:
        return out
    best = {x["record_id"]: max(json.loads(x["cell_scores"]).values(), default=0.0) for x in rows}
    for kind, key, sim in entries:
        cand = [x for x in rows if x["rank"] <= near_miss_max_rank and not x["thin"] and x["record_id"] not in taken
                and x["record_id"] in sim]
        if not cand:
            continue
        score = np.minimum(_percentile([best[x["record_id"]] for x in cand]),
                           _percentile([sim[x["record_id"]] for x in cand]))
        top = sorted(range(len(cand)), key=lambda i: (-score[i], cand[i]["rank"]))[:near_miss_pool]
        for rid in rng.sample([cand[i]["record_id"] for i in top], min(per_boundary, len(top))):
            taken.add(rid)
            out.append((rid, f"boundary:{kind}:{key}"))
    return out


def boundary_log(entries: list, per_boundary: int, got: list[tuple[int, str]]):
    """The sampling log's ``boundary`` entry: the reason nothing was drawn, or the entries and records per pool."""
    if per_boundary <= 0:
        return "per_boundary is 0"
    if not entries:
        return NO_BOUNDARY_ENTRIES
    pools: dict[str, int] = {}
    for _, pool in got:
        pools[pool] = pools.get(pool, 0) + 1
    return {"near_miss": [k for kind, k, _ in entries if kind == "near_miss"],
            "adjacent": [k for kind, k, _ in entries if kind == "adjacent"], "pools": pools}


def _store_sample(project, name, kind, rk, picked, seed, design) -> dict:
    st = project.store
    old = st.one("SELECT sample_id, locked FROM samples WHERE name=?", (name,))
    if old and old["locked"]:
        raise ValueError(f"sample {name!r} is locked")
    if old and st.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=?", (old["sample_id"],)):
        raise ValueError(f"sample {name!r} already has labels; use a new name")
    design = {**design, "ranking": rk["name"], "ranking_md5": rk["md5"], "seed": seed, "drawn_at": utcnow()}
    rng = random.Random(seed + 7)
    order = list(range(len(picked)))
    rng.shuffle(order)
    with st.tx() as c:
        if old:   # redraw of an unlabelled sample: same row and id, new records, design, seed and size
            sid = old["sample_id"]
            c.execute("DELETE FROM sample_rows WHERE sample_id=?", (sid,))
            c.execute("UPDATE samples SET kind=?, ranking_id=?, design=?, seed=?, n=?, created_at=?, locked=0 WHERE sample_id=?",
                      (kind, rk["ranking_id"], json.dumps(design), seed, len(picked), utcnow(), sid))
        else:
            cur = c.execute("INSERT INTO samples(name,kind,ranking_id,design,seed,n,created_at,locked) VALUES(?,?,?,?,?,?,?,0)",
                            (name, kind, rk["ranking_id"], json.dumps(design), seed, len(picked), utcnow()))
            sid = cur.lastrowid
        c.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) VALUES(?,?,?,?,?,?,?)",
                      [(sid, rid, order[i] + 1, band_or_pool if kind == "probability" else None, w,
                        band_or_pool if kind == "purposive" else None, rank)
                       for i, (rid, band_or_pool, w, rank) in enumerate(picked)])
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_samples
    record(project, summarize_samples([dict(r) for r in st.query("SELECT name, kind, n, locked FROM samples ORDER BY sample_id")]))
    return {"sample_id": sid, "name": name, "kind": kind, "n": len(picked), "design": design}


class SampleLocked(ValueError):
    """Extending a locked sample (HTTP 409)."""


class ExtendNeedsConfirm(ValueError):
    """Extending a purposive sample after a corpus round was not confirmed (ui-redesign D4; HTTP 409 with
    ``confirm_needed``): the message says what the added records hide and refuse until labelled. Nothing changed."""

    def __init__(self, message: str, n: int):
        super().__init__(message)
        self.n = n


EXTEND_HIDES = ("Until the {n} added record{s} {are} labelled, development model output is hidden and screening runs on "
                "those records are refused.")


ALL_LABELLED = "all {n} earlier records are labelled: the added records are recognisable as the boundary pool"
ALREADY = "{name} already has the boundary pool for every entry"
NO_ENTRY = "no near-miss or adjacent entry: no boundary records to add"
ZERO = "per_boundary is 0: no boundary records to add"
NO_RANKING = "sample {name} was imported without a ranking: no boundary records can be drawn"
LOCKED = "sample {name} is locked; wider sample unlock {name} first"
PROBABILITY = "sample {name} is a probability sample: only a purposive sample can be extended; use wider validation extend"


def _corpus_screened(project: Project) -> bool:
    from ..screen.progress import rounds

    return any(r["kind"] == "corpus" for r in rounds(project))


def extend_development(project: Project, name: str, pool: str = "boundary", author: str | None = None,
                       before_embedding=None, confirm=False) -> dict:
    """D9: add the boundary pool to an unlocked purposive sample, for the entries that have no boundary pool in it yet.
    The new records are shuffled with the sample's unlabelled ones over those positions and new ones after the
    current maximum; labelled records keep theirs. ``before_embedding(set_name)`` runs before any embedding call (the
    consent check). Once a corpus round exists, the added records hide development model output and refuse
    screening runs on them until labelled (D4): ``confirm`` (True, or a callable given the sentence that returns
    whether to go on) must agree, else :class:`ExtendNeedsConfirm` is raised before anything changes. The result
    gives counts per pool and names no record (the shuffle's purpose)."""
    from ..core import journal
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_samples

    if pool != "boundary":
        raise ValueError("only the boundary pool can be added for now: --pool boundary")
    st = project.store
    s = st.one("SELECT * FROM samples WHERE name=?", (name,))
    if s is None:
        raise KeyError(f"no sample {name}")
    s = dict(s)
    if s["kind"] == "probability":
        raise ValueError(PROBABILITY.format(name=name))
    if s["locked"]:
        raise SampleLocked(LOCKED.format(name=name))
    rk = st.one("SELECT * FROM rankings WHERE ranking_id=?", (s["ranking_id"],)) if s["ranking_id"] else None
    if rk is None:
        raise ValueError(NO_RANKING.format(name=name))
    rk = dict(rk)
    sp = project.config.protocol.sampling
    sid = s["sample_id"]
    have = {r["pool"] for r in st.query("SELECT DISTINCT pool FROM sample_rows WHERE sample_id=?", (sid,)) if r["pool"]}
    out = {"name": name, "pool": pool, "added": 0, "pools": {}, "n": s["n"], "messages": []}
    if sp.per_boundary <= 0:
        out["messages"].append(ZERO)
        return out
    _, nm = artifacts.load(project, "near_miss")
    nm_entries = [e["key"] for e in (nm or {}).get("near_miss", [])]
    lits = approved_literatures(project)
    wanted = [("near_miss", k, text) for k, text in zip(nm_entries, [e["text"] for e in (nm or {}).get("near_miss", [])])]
    wanted += [("adjacent", e["key"], embedding_text(e)) for e in lits]
    if not wanted:
        out["messages"].append(NO_ENTRY)
        return out
    todo = [w for w in wanted if f"boundary:{w[0]}:{w[1]}" not in have]
    if not todo:
        out["messages"].append(ALREADY.format(name=name))
        return out
    if before_embedding is not None:
        before_embedding(rk["set_name"])
    rows, bench_ids, _ = _frame(project, rk)
    taken = set(bench_ids) | {r["record_id"] for r in st.query("SELECT record_id FROM sample_rows")}
    V = embed_texts(project, rk["set_name"], [t for _, _, t in todo])
    ids, E = load_corpus_embeddings(project, rk["set_name"])
    S = E @ V.T
    entries = [(kind, key, {int(i): float(S[k, j]) for k, i in enumerate(ids)}) for j, (kind, key, _) in enumerate(todo)]
    seed = project.config.seed if s["seed"] is None else s["seed"]
    rng = random.Random(seed + 13)
    got = draw_boundary(rows, taken, entries, sp.per_boundary, sp.near_miss_pool, sp.near_miss_max_rank, rng)
    if not got:
        out["messages"].append("no record left to draw for the boundary pool")
        return out
    if confirm is not True and _corpus_screened(project):
        said = EXTEND_HIDES.format(n=len(got), s="" if len(got) == 1 else "s", are="is" if len(got) == 1 else "are")
        if not (callable(confirm) and confirm(said)):
            raise ExtendNeedsConfirm(said, len(got))
    by_id = {x["record_id"]: x for x in rows}
    cur = [dict(r) for r in st.query("SELECT record_id, queue_pos FROM sample_rows WHERE sample_id=? ORDER BY queue_pos", (sid,))]
    labelled = {r["record_id"] for r in st.query("SELECT DISTINCT record_id FROM labels WHERE sample_id=?", (sid,))}
    free = [r for r in cur if r["record_id"] not in labelled]
    top = max((r["queue_pos"] for r in cur), default=0)
    positions = sorted([r["queue_pos"] for r in free] + list(range(top + 1, top + 1 + len(got))))
    movers = [("old", r["record_id"]) for r in free] + [("new", rid) for rid, _ in got]
    rng.shuffle(movers)
    pools: dict[str, int] = {}
    for _, p_ in got:
        pools[p_] = pools.get(p_, 0) + 1
    design = json.loads(s["design"])
    design.setdefault("extensions", []).append({"pool": pool, "n": len(got), "pools": pools, "at": utcnow(),
                                                "author": journal.current_author(author)})
    pool_of = dict(got)
    summary = f"{name}: {len(got)} boundary record(s) added (" + ", ".join(f"{k} {v}" for k, v in pools.items()) + ")"
    with journal.recording(project, "sample_extend", summary, author=author) as rec:
        rec.rows("samples", "sample_id=?", [sid])
        rec.rows("sample_rows", "sample_id=?", [sid])
        with st.tx() as c:
            for (what, rid), pos in zip(movers, positions):
                if what == "old":
                    c.execute("UPDATE sample_rows SET queue_pos=? WHERE sample_id=? AND record_id=?", (pos, sid, rid))
                else:
                    c.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) "
                              "VALUES(?,?,?,NULL,NULL,?,?)", (sid, rid, pos, pool_of[rid], by_id[rid]["rank"]))
            c.execute("UPDATE samples SET n=?, design=? WHERE sample_id=?", (s["n"] + len(got), json.dumps(design), sid))
    record(project, summarize_samples([dict(r) for r in st.query("SELECT name, kind, n, locked FROM samples ORDER BY sample_id")]))
    out.update(added=len(got), pools=pools, n=s["n"] + len(got), change_id=rec.change_id)
    if cur and not free:
        out["messages"].append(ALL_LABELLED.format(n=len(cur)))
    return out


def extension_block(project: Project, items: list) -> str | None:
    """Why undoing (or redoing) an extension is refused now, or None: the sample is locked, or a record the extension
    added has a label by any reviewer or an adjudication."""
    for it in items:
        if it["t"] == "rows" and it.get("table") == "sample_rows" and it["where"] == "sample_id=?":
            sid = it["params"][0]
            s = project.store.one("SELECT name, locked FROM samples WHERE sample_id=?", (sid,))
            if s is not None and s["locked"]:
                return f"sample {s['name']!r} is locked: unlock it to undo its extension"
            before = {r["record_id"] for r in it["before"]}
            after = {r["record_id"] for r in it["after"]}
            added = sorted((after - before) | (before - after))
            if added:
                q = ",".join("?" * len(added))
                for table in ("labels", "adjudications"):
                    if project.store.scalar(f"SELECT COUNT(*) FROM {table} WHERE sample_id=? AND record_id IN ({q})",
                                            [sid, *added]):
                        return "a record added by this extension is labelled"
    return None


def thin_census(project: Project, ranking_name: str | None = None) -> list[dict]:
    rk = _ranking(project, ranking_name)
    return [dict(x) for x in project.store.query(
        "SELECT m.record_id, m.title, m.year, m.journal, m.doi, rr.rank FROM records m JOIN ranking_rows rr ON rr.record_id=m.record_id "
        "WHERE rr.ranking_id=? AND m.thin=1 ORDER BY m.year DESC", (rk["ranking_id"],))]


def sampling_log(project: Project) -> dict:
    out, use = {}, in_use(project)
    for s in project.store.query("SELECT * FROM samples"):
        d = {k: v for k, v in json.loads(s["design"]).items() if k != FRAME_EXCLUDED}   # record ids stay inside
        out[s["name"]] = {"kind": s["kind"], "n": s["n"], "locked": bool(s["locked"]), **d,
                          **({"in_use": True} if s["name"] == use and s["kind"] == "probability" else {})}
    return out


def in_use(project: Project) -> str:
    """The probability sample recall reads (D9): ``protocol.sampling.probability_sample``."""
    return project.config.protocol.sampling.probability_sample


def boundary_line(d: dict) -> str | None:
    """The boundary pool of a purposive sample, as `wider sample status` and the Sampling page give it: the draw's
    pools plus every extension's, else the draw's reason (D8, D9); None for a sample without a boundary log."""
    if d.get("kind") != "purposive" or ("boundary" not in d and not d.get("extensions")):
        return None
    b = d.get("boundary")
    pools = dict(b.get("pools") or {}) if isinstance(b, dict) else {}
    for e in d.get("extensions") or []:
        for k, v in (e.get("pools") or {}).items():
            pools[k] = pools.get(k, 0) + v
    if pools:
        return ", ".join(f"{k} {v}" for k, v in pools.items())
    return b if isinstance(b, str) else "none drawn"


def queue_hash(project: Project) -> str:
    rows = project.store.query("SELECT sample_id, record_id, queue_pos FROM sample_rows ORDER BY sample_id, queue_pos")
    return hashlib.sha256(json.dumps([tuple(r) for r in rows]).encode()).hexdigest()


def frame_summary(project: Project, ranking_name: str | None = None) -> dict:
    rk = _ranking(project, ranking_name)
    pf = probability_frame(project, rk)
    bands, notes = resolve_bands(project.config.bands, pf["n_ranked"])
    return {"ranking": rk["name"], "records": pf["n_ranked"], "thin": pf["thin_excluded"],
            "benchmark": len(pf["bench_ids"]), "frame": len(pf["frame"]),
            "clusters": len(set(pf["clusters"].values())) if pf["clusters"] else 0,
            "bands": bands, "band_notes": notes, "mean_weight_dummy": None}


def nonnull(a: np.ndarray) -> int:
    return int(np.count_nonzero(~np.isnan(a)))


# ------------------------------------------------------------------------- extending the probability sample (D6)
PURPOSIVE_HERE = "only a probability sample can be extended here; use wider sample extend for a purposive sample"
RANKING_CHANGED = "the ranking changed since {name} was drawn; draw a fresh sample instead"
FEW_LEFT = "{band} has only {n} frame records left"
REPLACED = "{name} was replaced by {fresh} (a fresh sample); extend {fresh} instead"
RECOGNISABLE = "every earlier record of {band} is labelled, so the {m} added records are recognisable as added"
AFTER_LOOK = "{name} was extended after a recall result was seen ({day}); the interval does not allow for that decision"
NOTHING_SUGGESTED = "no stored suggestion for {name}: estimate recall first"
NOTHING_TO_ADD = "nothing to add: every suggested record of {name} is already in the sample, unlabelled"


class SampleConflict(ValueError):
    """An extension refused because of the sample's state (HTTP 409): locked, ranking changed, replaced."""


def other_samples(project: Project, sid: int) -> set[int]:
    """E of D6: the records of every other sample, read as the samples stand now."""
    return {int(r["record_id"]) for r in project.store.query(
        "SELECT DISTINCT record_id FROM sample_rows WHERE sample_id<>?", (sid,))}


def fresh_successor(project: Project, name: str) -> str | None:
    """The sample drawn fresh from ``name`` (D9), if any."""
    for s in project.store.query("SELECT name, design FROM samples WHERE name<>?", (name,)):
        if json.loads(s["design"] or "{}").get("fresh_from") == name:
            return s["name"]
    return None


def band_weights(project: Project, sid: int, bands: list[dict], frame: list[dict], E: set[int]) -> dict:
    """Per band: N'_h (frame records outside E), the sample's records outside E (n'_h) and in E (d_h), and the
    weight N'_h/n'_h those get (D6); records in E get none."""
    rows = [dict(r) for r in project.store.query("SELECT record_id, band FROM sample_rows WHERE sample_id=?", (sid,))]
    out = {}
    for b in bands:
        Np = sum(1 for x in frame if in_band(x["rank"], b) and x["record_id"] not in E)
        mine = [r["record_id"] for r in rows if r["band"] == b["band"]]
        inside = [r for r in mine if r not in E]
        out[b["band"]] = {"N": Np, "n": len(inside), "d": len(mine) - len(inside),
                          "weight": Np / len(inside) if inside else None}
    return out


def _frame_ids(project: Project, ranking_id) -> set[int]:
    """The probability frame's records of the ranking a sample was drawn from (empty without one)."""
    rk = project.store.one("SELECT * FROM rankings WHERE ranking_id=?", (ranking_id,)) if ranking_id else None
    return {x["record_id"] for x in probability_frame(project, dict(rk))["frame"]} if rk else set()


def scope_lines(project: Project, sample: str) -> list[str]:
    """The development records the probability sample does not cover, and those in it left out of the estimate (D6,
    D13)."""
    st = project.store
    s = st.one("SELECT sample_id, kind, ranking_id FROM samples WHERE name=?", (sample,))
    if s is None or s["kind"] != "probability":
        return []
    sid = s["sample_id"]
    dev = {int(r["record_id"]) for r in st.query(
        "SELECT DISTINCT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        "WHERE s.kind='purposive' AND s.sample_id<>?", (sid,))}
    if not dev:
        return []
    mine = {int(r["record_id"]) for r in st.query("SELECT record_id FROM sample_rows WHERE sample_id=?", (sid,))}
    out = []
    # drawn after the sample: in its frame and so in its bands' counts (recall-final-check-fixes D5)
    later = (dev - mine - frame_excluded(project, sample)) & _frame_ids(project, s["ranking_id"])
    if dev - mine - later:
        out.append(f"{len(dev - mine - later)} records of the development sample are not covered.")
    if later:
        out.append(f"{len(later)} records of the development sample were drawn after {sample}; its estimate and "
                   f"bounds count them as records of their bands that {sample} did not label.")
    if dev & mine:
        out.append(f"{len(dev & mine)} records of {sample} are in the development sample and are left out of the "
                   "estimate.")
    return out


def extend_probability(project: Project, name: str, bands, author: str | None = None) -> dict:
    """D6: add to bands of an unlocked probability sample a seeded simple random sample of its frame records outside
    E (every other sample). ``bands`` is {band: m} or "suggested" (the suggestion stored with the last look, minus the
    band's unlabelled sample records). Weights become N'_h/n'_h for the records outside E and NULL for those in E.
    The new records are shuffled with the unlabelled ones; labelled records keep their positions. Journalled as
    ``sample_extend`` with undo. The result names no record."""
    from ..core import journal
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_samples

    st = project.store
    s = st.one("SELECT * FROM samples WHERE name=?", (name,))
    if s is None:
        raise KeyError(f"no sample {name}")
    s = dict(s)
    if s["kind"] != "probability":
        raise ValueError(PURPOSIVE_HERE)
    if s["locked"]:
        raise SampleConflict(LOCKED.format(name=name))
    design = json.loads(s["design"])
    rk = st.one("SELECT * FROM rankings WHERE ranking_id=?", (s["ranking_id"],)) if s["ranking_id"] else None
    if rk is None or not all("start" in b for b in design.get("bands", [])):
        raise ValueError(NO_RANKING.format(name=name).replace("boundary records", "records"))
    rk = dict(rk)
    if design.get("ranking_md5") != rk["md5"]:
        raise SampleConflict(RANKING_CHANGED.format(name=name))
    fresh = fresh_successor(project, name)
    if fresh:
        raise SampleConflict(REPLACED.format(name=name, fresh=fresh))
    sid = s["sample_id"]
    labelled = {r["record_id"] for r in st.query("SELECT DISTINCT record_id FROM labels WHERE sample_id=?", (sid,))}
    rows = [dict(r) for r in st.query("SELECT record_id, band, queue_pos FROM sample_rows WHERE sample_id=? "
                                      "ORDER BY queue_pos", (sid,))]
    names = [b["band"] for b in design["bands"]]
    if bands == "suggested":
        from .plan import stored_suggestion

        key = _suggestion_key(project, sid)
        sg = stored_suggestion(project, name, key) if key else None
        if not sg or not sg.get("given"):
            raise ValueError(NOTHING_SUGGESTED.format(name=name))
        part = sg.get("target") or sg.get("precision")
        if not sg.get("settles", True) or not part:
            raise ValueError((sg.get("lines") or [NOTHING_SUGGESTED.format(name=name)])[0])
        unl = {b: sum(1 for r in rows if r["band"] == b and r["record_id"] not in labelled) for b in names}
        bands = {b: max(0, int(m) - unl.get(b, 0)) for b, m in part["by_band"].items()}
        bands = {b: m for b, m in bands.items() if m > 0}
        if not bands:
            raise ValueError(NOTHING_TO_ADD.format(name=name))
    bands = {str(b): int(m) for b, m in (bands or {}).items() if int(m) != 0}
    if not bands:
        raise ValueError("name at least one band and a number of records: --band S2=20")
    for b, m in bands.items():
        if b not in names:
            raise ValueError(f"{name} has no band {b}; its bands are {', '.join(names)}")
        if m < 0:
            raise ValueError(f"a band takes a positive number of records, not {m}")
    E = other_samples(project, sid)
    frame = probability_frame(project, rk)["frame"]
    mine = {r["record_id"] for r in rows}
    n_ext = len(design.get("extensions", [])) + 1
    rng = random.Random(f"{project.config.seed if s['seed'] is None else s['seed']}:extend:{n_ext}")
    added = []
    for b in design["bands"]:
        m = bands.get(b["band"])
        if not m:
            continue
        pool = sorted(x["record_id"] for x in frame
                      if in_band(x["rank"], b) and x["record_id"] not in E and x["record_id"] not in mine)
        if m > len(pool):
            raise ValueError(FEW_LEFT.format(band=b["band"], n=len(pool)))
        rank_of = {x["record_id"]: x["rank"] for x in frame}
        added += [(rid, b["band"], rank_of[rid]) for rid in rng.sample(pool, m)]
    # the weights after the extension, over the records outside E
    after = {r["record_id"]: r["band"] for r in rows} | {rid: band for rid, band, _ in added}
    w = {}
    for b in design["bands"]:
        Np = sum(1 for x in frame if in_band(x["rank"], b) and x["record_id"] not in E)
        inside = [rid for rid, band in after.items() if band == b["band"] and rid not in E]
        w[b["band"]] = {"N": Np, "n": len(inside), "weight": Np / len(inside) if inside else None}
    free = [r for r in rows if r["record_id"] not in labelled]
    top = max((r["queue_pos"] for r in rows), default=0)
    positions = sorted([r["queue_pos"] for r in free] + list(range(top + 1, top + 1 + len(added))))
    movers = [("old", r["record_id"]) for r in free] + [("new", rid) for rid, _, _ in added]
    rng.shuffle(movers)
    look_at = st.scalar("SELECT MAX(first_at) FROM validation_look_states WHERE sample_id=?", (sid,))
    who = journal.current_author(author)
    for e in design["bands"]:
        if e["band"] in w:
            e.update(N_h=w[e["band"]]["N"], n_h=w[e["band"]]["n"], weight=w[e["band"]]["weight"])
    design[FRAME_EXCLUDED] = sorted(E & {x["record_id"] for x in frame})   # the cells keep matching N'_h (D2)
    design.setdefault("extensions", []).append({"at": utcnow(), "author": who, "bands": bands,
                                                "after_look": look_at})
    messages = []
    for b, m in bands.items():
        earlier = [r for r in rows if r["band"] == b]
        if earlier and all(r["record_id"] in labelled for r in earlier):
            messages.append(RECOGNISABLE.format(band=b, m=m))
    if look_at:
        messages.append(AFTER_LOOK.format(name=name, day=utcnow()[:10]))
    summary = (f"{name}: {len(added)} record(s) added (" + ", ".join(f"{b} {m}" for b, m in bands.items()) + ")"
               + "".join(f"; {x}" for x in messages))
    rank_new = {rid: rk_ for rid, _, rk_ in added}
    band_new = {rid: band for rid, band, _ in added}
    with journal.recording(project, "sample_extend", summary, author=author) as rec:
        rec.rows("samples", "sample_id=?", [sid])
        rec.rows("sample_rows", "sample_id=?", [sid])
        with st.tx() as c:
            for (what, rid), pos in zip(movers, positions):
                if what == "old":
                    c.execute("UPDATE sample_rows SET queue_pos=? WHERE sample_id=? AND record_id=?", (pos, sid, rid))
                else:
                    c.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) "
                              "VALUES(?,?,?,?,NULL,NULL,?)", (sid, rid, pos, band_new[rid], rank_new[rid]))
            for band, v in w.items():
                c.execute("UPDATE sample_rows SET weight=? WHERE sample_id=? AND band=?", (v["weight"], sid, band))
            for rid in E & set(after):
                c.execute("UPDATE sample_rows SET weight=NULL WHERE sample_id=? AND record_id=?", (sid, rid))
            c.execute("UPDATE samples SET n=?, design=? WHERE sample_id=?", (s["n"] + len(added), json.dumps(design), sid))
    record(project, summarize_samples([dict(r) for r in st.query("SELECT name, kind, n, locked FROM samples ORDER BY sample_id")]))
    lines = [f"{name}: {len(added)} records added (" + ", ".join(f"{b} {m}" for b, m in bands.items())
             + f"); {s['n'] + len(added)} in all, the new ones shuffled with the unlabelled ones."] + messages
    return {"name": name, "added": len(added), "bands": bands, "n": s["n"] + len(added),
            "weights": {b: {"N_h": v["N"], "n_h": v["n"], "weight": v["weight"]} for b, v in w.items()},
            "change_id": rec.change_id, "messages": messages, "lines": lines}


WEIGHTS_EXACT = "Weights exact given the records of other samples fixed before the draw."
WEIGHTS_APPROX = "Weights approximate: the development sample was drawn around {name}."


def weights_basis(project: Project, name: str) -> str | None:
    """D6: for an extended probability sample, whether its weights are exact (every other sample with records was
    drawn before it) or approximate (one was drawn after it, around it)."""
    st = project.store
    s = st.one("SELECT sample_id, design, created_at FROM samples WHERE name=? AND kind='probability'", (name,))
    if s is None or not json.loads(s["design"] or "{}").get("extensions"):
        return None
    later = st.scalar("SELECT COUNT(*) FROM samples o WHERE o.sample_id<>? AND (o.created_at>? OR o.sample_id>?) "
                      "AND EXISTS (SELECT 1 FROM sample_rows r WHERE r.sample_id=o.sample_id)",
                      (s["sample_id"], s["created_at"], s["sample_id"]))
    return WEIGHTS_APPROX.format(name=name) if later else WEIGHTS_EXACT


def _suggestion_key(project: Project, sid: int) -> str | None:
    """The result set the last look's suggestion was made under."""
    return project.store.scalar("SELECT set_key FROM validation_look_states WHERE sample_id=? "
                                "ORDER BY look_index DESC LIMIT 1", (sid,))
