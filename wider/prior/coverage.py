"""Prior-review coverage: which of the studies included in prior reviews each export (or the deduplicated corpus)
retrieved. **Local matching only** (DOI → exact normalised title → Jaro–Winkler ≥ ``jw_benchmark``).

A descriptive second benchmark next to the seed papers: it is not gated by the synonyms freeze, because the
prior reviews were not used to tune the query.
"""
from __future__ import annotations

from typing import Any

from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow
from . import reviews


def check(project: Project, scope: str = "platforms") -> dict[str, Any]:
    """``scope``: ``platforms`` (each platform's raw records) or ``corpus`` (deduplicated records).

    Returns ``{scope, n_papers, found, reviews: [{review_id, label, n, found, unmatchable, rows, missed}]}`` where
    ``rows`` has one entry per scope (``{scope, found, n, share}``) and ``missed`` lists the papers no scope holds.
    """
    if scope not in ("platforms", "corpus"):
        raise ValueError("scope must be 'platforms' or 'corpus'")
    papers = reviews.papers(project)
    st = project.store
    scopes: dict[str, list[CorpusRecord]] = {}
    if scope == "corpus":
        scopes["corpus"] = [CorpusRecord(str(r["record_id"]), r["title"] or "", r["doi"])
                            for r in st.query("SELECT record_id, title, doi FROM records")]
    else:
        for r in st.query("SELECT raw_id, platform, title, doi FROM raw_records"):
            scopes.setdefault(f"platform:{r['platform']}", []).append(CorpusRecord(str(r["raw_id"]), r["title"] or "", r["doi"]))
    if not any(scopes.values()):
        raise ValueError("nothing imported yet" if scope == "platforms" else "no deduplicated corpus yet (run wider dedup run)")
    bench = [(str(p["paper_id"]), p["title"] or "", p["doi"]) for p in papers if p["title"] or p["doi"]]
    thr = project.config.thresholds.jw_benchmark
    now = utcnow()
    found_in: dict[str, dict[str, dict]] = {}  # paper_id -> scope -> match
    with st.tx() as c:
        for sc, corpus in scopes.items():
            for m in locate_benchmark(bench, corpus, jw_threshold=thr):
                c.execute("INSERT INTO prior_review_matches(paper_id,scope,record_ref,method,score,checked_at) VALUES(?,?,?,?,?,?) "
                          "ON CONFLICT(paper_id,scope) DO UPDATE SET record_ref=excluded.record_ref, method=excluded.method, "
                          "score=excluded.score, checked_at=excluded.checked_at",
                          (int(m.benchmark_id), sc, int(m.record_id) if m.record_id else None, m.method, m.score, now))
                found_in.setdefault(m.benchmark_id, {})[sc] = {"method": m.method, "score": round(m.score, 3),
                                                              "record_ref": m.record_id, "needs_confirmation": m.needs_confirmation}
    out_reviews = []
    total_found = 0
    for rv in reviews.list_reviews(project):
        mine = [p for p in papers if p["review_id"] == rv["review_id"]]
        matchable = [p for p in mine if p["title"] or p["doi"]]
        rows = []
        for sc in scopes:
            n_found = sum(1 for p in matchable if found_in.get(str(p["paper_id"]), {}).get(sc, {}).get("method", "none") != "none")
            rows.append({"scope": sc, "found": n_found, "n": len(matchable), "share": (n_found / len(matchable)) if matchable else 0.0})
        hit = [p for p in matchable if any(m["method"] != "none" for m in found_in.get(str(p["paper_id"]), {}).values())]
        missed = [{"paper_id": p["paper_id"], "position": p["position"], "title": p["title"], "doi": p["doi"], "year": p["year"]}
                  for p in matchable if p not in hit]
        total_found += len(hit)
        out_reviews.append({"review_id": rv["review_id"], "label": rv["label"], "n": len(matchable), "found": len(hit),
                            "unmatchable": len(mine) - len(matchable), "rows": rows, "missed": missed,
                            "fuzzy": sum(1 for p in hit if all(m["method"] in ("fuzzy", "none") for m in found_in.get(str(p["paper_id"]), {}).values()))})
    return {"scope": scope, "scopes": list(scopes), "n_papers": len(bench), "found": total_found, "reviews": out_reviews}
