"""Find adjacent literatures in the ranking: clusters of top-ranked records that hold no benchmark paper.

After ranking and clustering, every cluster within the cluster depth whose members score high on the anchors but
that contains none of the benchmark papers is a candidate adjacent literature. For each, the model is shown the
cluster's frequent terms and its most central titles and abstracts and asked to name the literature, say how it
differs from the target studies and give the discriminating cue, with verbatim quotes from the records. Candidates
whose quotes do not verify against the record text are dropped. Accepted candidates are merged into the ``adjacent``
artifact as ``origin: tool, status: proposed`` for the researcher to approve, edit or reject.
"""
from __future__ import annotations

import asyncio
import json

from ..construct import adjacent as adj
from ..construct.artifacts import dump_yaml
from ..construct.propose import ProposalError, _artifact_or_none, _env, benchmark_context
from ..core.matching import quote_verified
from ..core.project import Project
from ..core.provenance import RunSpec, sha256_json, sha256_text
from ..core.text import truncate_words
from ..providers import ChatRequest, research_provider
from ..providers.registry import require_text
from ..sample.design import _frame, _ranking
from .cluster import cluster as run_cluster

CANDIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_adjacent": {"type": "boolean"},
        "already_listed": {"type": "boolean"},
        "key": {"type": "string", "pattern": "^([a-z][a-z0-9_]*)?$"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "differs_from_construct": {"type": "string"},
        "discriminating_cue": {"type": "string"},
        "example_phrases": {"type": "array", "items": {"type": "string"}},
        "quotes": {"type": "array", "items": {"type": "object", "properties": {
            "record_id": {"type": "integer"}, "quote": {"type": "string"}}, "required": ["record_id", "quote"]}},
    },
    "required": ["is_adjacent", "already_listed", "key", "name", "description", "differs_from_construct",
                 "discriminating_cue", "example_phrases", "quotes"],
}
MAX_ABSTRACT_WORDS = 250


def _clusters(project: Project, rk: dict, k: int | None, top: int | None) -> dict:
    """The stored clustering of this ranking, else of its base name (a later version without its own, ui-redesign
    D5); recomputed on this version when none matches the requested k."""
    from .versions import base_of

    st = project.store
    for name in dict.fromkeys((rk["name"], base_of(project, rk["name"]))):
        keys = [x["key"] for x in st.query("SELECT key FROM meta WHERE key LIKE ?", (f"clusters:{name}:%",))]
        if k is None and keys:  # reuse the clustering the rank page stored for this ranking (last one written)
            stored = json.loads(st.get_meta(keys[-1]))
            if top is None or stored.get("top") == top:
                return stored
        stored = st.get_meta(f"clusters:{name}:{k or project.config.protocol.ranking.cluster_k}")
        if stored:
            res = json.loads(stored)
            if top is None or res.get("top") == top:
                return res
    return run_cluster(project, rk["name"], k=k or project.config.protocol.ranking.cluster_k, top=top)


def candidate_clusters(project: Project, ranking_name: str | None = None, k: int | None = None, top: int | None = None,
                       min_size: int = 3, n_examples: int = 5) -> dict:
    """Clusters within the cluster depth that hold no benchmark paper, with their most central records (no model call)."""
    rk = _ranking(project, ranking_name)
    res = _clusters(project, rk, k, top)
    rows, bench_ids, _ = _frame(project, rk)
    by_id = {x["record_id"]: x for x in rows}
    labels = {int(a): int(b) for a, b in res["labels"].items()}
    members: dict[int, list[int]] = {}
    for rid, c in labels.items():
        members.setdefault(c, []).append(rid)
    st = project.store
    out = []
    for c in res["clusters"]:
        ids = members.get(c["cluster"], [])
        hits = [i for i in ids if i in bench_ids]
        if hits or len(ids) < min_size:
            continue
        ranked = sorted(ids, key=lambda i: by_id[i]["rank"] if i in by_id else 10**9)
        pick = []
        if c.get("medoid"):
            pick.append(c["medoid"]["record_id"])
        pick += [i for i in ranked if i not in pick][: max(0, n_examples - len(pick))]
        recs = []
        for rid in pick:
            r = st.one("SELECT record_id, title, abstract FROM records WHERE record_id=?", (rid,))
            if r:
                recs.append({"record_id": rid, "rank": by_id.get(rid, {}).get("rank"), "title": r["title"] or "",
                             "abstract": truncate_words(r["abstract"] or "", MAX_ABSTRACT_WORDS)})
        out.append({"cluster": c["cluster"], "n": len(ids), "median_rank": c.get("median_rank"), "terms": c.get("terms", []),
                    "benchmark_hits": 0, "records": recs})
    out.sort(key=lambda c: (c["median_rank"] if c["median_rank"] is not None else 10**9))
    return {"ranking": rk["name"], "k": res["k"], "top": res["top"], "n_clusters": len(res["clusters"]),
            "with_benchmark": len(res["clusters"]) - len(out), "candidates": out}


def _system_prompt(project: Project) -> tuple[str, dict]:
    cfg = project.config
    bench = benchmark_context(project)
    if not bench:
        raise ProposalError("benchmark.csv has no Development papers; add them first (wider benchmark fetch)")
    _, lits = adj.load_list(project)
    crow, cb = _artifact_or_none(project, "codebook")
    ctx = {"questions": cfg.questions or [cfg.title or cfg.name], "include_notes": cfg.include_notes,
           "exclude_notes": cfg.exclude_notes, "benchmark": bench, "existing": lits,
           "codebook_yaml": dump_yaml({k: cb[k] for k in ("target_description", "elements") if k in cb}) if cb else ""}
    return _env().get_template("adjacent_cluster.md.j2").render(**ctx), {"benchmark_n": len(bench), "existing": [e["key"] for e in lits],
                                                                         "codebook_version": crow["version"] if crow else None}


def cluster_message(c: dict) -> str:
    lines = [f"# Cluster {c['cluster']}: {c['n']} records, median rank {c['median_rank']}", "",
             "Frequent terms: " + ", ".join(c["terms"]), "", "# Most central records", ""]
    for r in c["records"]:
        lines += [f"## [record {r['record_id']}] {r['title']}", "", r["abstract"] or "(no abstract)", ""]
    lines.append("Name this literature now as one JSON object matching the schema, with verbatim quotes.")
    return "\n".join(lines)


def verify_quotes(quotes: list[dict], records: list[dict], min_chars: int) -> list[dict]:
    texts = {r["record_id"]: f"{r['title']} {r['abstract']}" for r in records}
    out = []
    for q in quotes or []:
        try:
            rid = int(q.get("record_id"))
        except (TypeError, ValueError):
            continue
        if rid in texts and quote_verified(q.get("quote"), texts[rid], min_chars):
            out.append({"record_id": rid, "quote": q["quote"]})
    return out


async def find_candidates_async(project: Project, ranking_name: str | None = None, provider_name: str | None = None,
                                k: int | None = None, top: int | None = None, min_size: int = 3, max_clusters: int | None = None,
                                merge: bool = True, note: str = "") -> dict:
    """Ask the model to name every benchmark-free cluster; merge grounded answers into the adjacent artifact."""
    pcfg = require_text(project.provider(provider_name) if provider_name else project.provider_for("construct"))  # refused before any work
    provider = research_provider(project, pcfg)  # consent checked before any work
    cand = candidate_clusters(project, ranking_name, k=k, top=top, min_size=min_size)
    clusters = cand["candidates"][:max_clusters] if max_clusters else cand["candidates"]
    system, info = _system_prompt(project)
    qmin = project.config.protocol.screening.quote_min_chars
    run_dir = project.run_dir("construct", RunSpec(kind="construct").stamp + "_adjacent_find")
    (run_dir / "prompt_system.md").write_text(system, encoding="utf-8")
    calls, accepted, skipped = [], [], []

    async def one(c: dict):
        user = cluster_message(c)
        req = ChatRequest(system=system, user=user, schema=CANDIDATE_SCHEMA, max_tokens=3000)
        r = await provider.complete(req)
        tag = f"cluster{c['cluster']}"
        (run_dir / f"prompt_{tag}.md").write_text(user, encoding="utf-8")
        (run_dir / f"response_{tag}.txt").write_text(r.text or "", encoding="utf-8")
        calls.append({"tag": tag, "ok": r.ok, "attempts": r.attempts, "error": r.error, "usage": r.usage,
                      "input_sha256": r.input_sha256, "elapsed_s": round(r.elapsed_s, 1)})
        return c, r

    from ..core.usage import track
    with track(project, "construct", run_dir.name):
        results = await asyncio.gather(*[one(c) for c in clusters])
    for c, r in results:
        base = {"cluster": c["cluster"], "n": c["n"], "median_rank": c["median_rank"], "terms": c["terms"]}
        if not r.ok:
            skipped.append({**base, "why": f"call failed: {r.error}"})
            continue
        p = r.parsed or {}
        if not p.get("is_adjacent"):
            skipped.append({**base, "why": "model: not an adjacent literature", "note": p.get("differs_from_construct", "")})
            continue
        if p.get("already_listed"):
            skipped.append({**base, "why": f"already listed as {p.get('key')}"})
            continue
        quotes = verify_quotes(p.get("quotes"), c["records"], qmin)
        if not quotes:
            skipped.append({**base, "why": "no verbatim quote verified against the records", "name": p.get("name")})
            continue
        accepted.append({"key": p.get("key") or adj.slug(p.get("name", "")), "name": p.get("name", ""), "description": p.get("description", ""),
                         "differs_from_construct": p.get("differs_from_construct", ""), "discriminating_cue": p.get("discriminating_cue", ""),
                         "example_phrases": p.get("example_phrases", []), "origin": "tool", "status": "proposed",
                         "evidence": {**base, "quotes": quotes, "record_ids": [x["record_id"] for x in c["records"]]}})
    spec = RunSpec(kind="construct", inputs={"prompt": sha256_text(system), "schema": sha256_json(CANDIDATE_SCHEMA)},
                   settings={"artifact": adj.KIND, "mode": "find", "ranking": cand["ranking"], "k": cand["k"], "top": cand["top"],
                             "clusters": [c["cluster"] for c in clusters], "context": info},
                   provider=provider.identity(), notes=note)
    spec.write(run_dir)
    (run_dir / "calls.json").write_text(json.dumps(calls, indent=1), encoding="utf-8")
    (run_dir / "candidates.json").write_text(json.dumps({"accepted": accepted, "skipped": skipped}, indent=1), encoding="utf-8")
    out = {"ranking": cand["ranking"], "k": cand["k"], "top": cand["top"], "clusters_considered": len(clusters),
           "with_benchmark": cand["with_benchmark"], "candidates": accepted, "skipped": skipped, "calls": calls,
           "run_dir": str(run_dir), "version": None, "added": []}
    if merge and accepted:
        merged = adj.merge_candidates(project, accepted, origin="tool", status="proposed",
                                      note=note or f"found in ranking {cand['ranking']} by {pcfg.name}/{pcfg.model}; run {run_dir.name}")
        out["version"], out["added"] = merged.get("version"), merged.get("added", [])
    return out


def find_candidates(project: Project, ranking_name: str | None = None, **kw) -> dict:
    return asyncio.run(find_candidates_async(project, ranking_name, **kw))
