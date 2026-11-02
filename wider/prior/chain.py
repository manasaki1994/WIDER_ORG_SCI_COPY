"""Resolve prior-review papers on OpenAlex and chain their citations (backward = works they cite, forward = works
citing them). **Every function starts with** :func:`~wider.prior.reviews.require_lookup`: nothing is sent unless the
project's lookup switch is on. Fetched works are stored in ``citation_chain`` and exported as a CSV in
``inputs/citation_chain/`` that the ordinary importer ingests as platform ``citation_chain``.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

import httpx

from ..construct.seed import openalex_search
from ..core.lookups import lookup_client
from ..core.matching import jaro_winkler
from ..core.project import Project
from ..core.provenance import stamp, utcnow
from ..core.text import clean_doi, norm_title
from ..enrich.sources import _mailto, inverted_abstract
from .reviews import require_lookup

WORKS = "https://api.openalex.org/works"
SELECT = "id,doi,title,publication_year,authorships,primary_location,abstract_inverted_index"
PLATFORM = "citation_chain"
HEADERS = ["Title", "Abstract", "DOI", "Year", "Authors", "Journal", "ID", "Chain Source", "Chain Direction"]


def _get_json(client: httpx.Client, url: str, params: dict | None = None, retries: int = 3) -> dict | None:
    """GET JSON with a polite pause and backoff on 429/5xx; ``None`` on a final failure."""
    params = dict(params or {})
    if _mailto():
        params["mailto"] = _mailto()
    delay = 1.0
    for attempt in range(retries + 1):
        time.sleep(0.1)
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200:
            return r.json()
        if r is not None and r.status_code not in (429, 500, 502, 503, 504):
            return None
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    return None


def _short_id(openalex_id: str | None) -> str | None:
    if not openalex_id:
        return None
    return openalex_id.rsplit("/", 1)[-1]


def _work_row(d: dict) -> dict[str, Any]:
    auths = "; ".join(a.get("author", {}).get("display_name") or "" for a in (d.get("authorships") or []) if a.get("author"))
    loc = (d.get("primary_location") or {}).get("source") or {}
    return {"openalex_id": _short_id(d.get("id")), "doi": clean_doi(d.get("doi")), "title": (d.get("title") or "").strip(),
            "year": d.get("publication_year"), "authors": auths, "journal": loc.get("display_name") or "",
            "abstract": inverted_abstract(d)}


# ----------------------------------------------------------------- resolve
def resolve(project: Project, client: httpx.Client | None = None, limit: int | None = None) -> dict[str, Any]:
    """Fill ``openalex_id`` for unresolved papers: DOIs in batches of 50, then title search for the rest."""
    require_lookup(project)
    st = project.store
    todo = [dict(r) for r in st.query("SELECT paper_id, title, title_norm, doi_norm FROM prior_review_papers "
                                      "WHERE openalex_id IS NULL ORDER BY paper_id")]
    if limit:
        todo = todo[:limit]
    thr = project.config.thresholds.jw_benchmark
    resolved = 0
    warnings: list[str] = []
    with lookup_client(project, "prior_resolve", client) as client:   # logs the requests; its own switch is checked above
        with_doi = [p for p in todo if p["doi_norm"]]
        found: dict[str, dict] = {}
        for i in range(0, len(with_doi), 50):
            batch = with_doi[i:i + 50]
            d = _get_json(client, WORKS, {"filter": "doi:" + "|".join(p["doi_norm"] for p in batch),
                                           "select": "id,doi,title,publication_year", "per-page": 50})
            if d is None:
                warnings.append(f"OpenAlex did not answer for a batch of {len(batch)} DOIs")
                continue
            for w in d.get("results", []):
                if (doi := clean_doi(w.get("doi"))):
                    found[doi] = w
        now = utcnow()
        for p in todo:
            w = found.get(p["doi_norm"]) if p["doi_norm"] else None
            if w is None and p["title_norm"]:
                for cand in openalex_search(p["title"], client):
                    if jaro_winkler(norm_title(cand.get("title")), p["title_norm"]) >= thr:
                        w = {"id": cand.get("openalex_id") or cand.get("id"), "title": cand.get("title"),
                             "publication_year": cand.get("year"), "doi": cand.get("doi")}
                        break
            if w and w.get("id"):
                st.execute("UPDATE prior_review_papers SET openalex_id=?, resolved_at=?, year=COALESCE(year,?), "
                           "title=CASE WHEN title IS NULL OR title='' THEN ? ELSE title END, "
                           "title_norm=CASE WHEN title_norm IS NULL THEN ? ELSE title_norm END WHERE paper_id=?",
                           (_short_id(w["id"]), now, w.get("publication_year"), (w.get("title") or "").strip() or None,
                            norm_title(w.get("title")) or None, p["paper_id"]))
                resolved += 1
            else:
                st.execute("UPDATE prior_review_papers SET resolved_at=? WHERE paper_id=?", (now, p["paper_id"]))
    total = int(st.scalar("SELECT COUNT(*) FROM prior_review_papers") or 0)
    unresolved = int(st.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE openalex_id IS NULL") or 0)
    return {"attempted": len(todo), "resolved": resolved, "unresolved": unresolved, "total": total, "warnings": warnings}


# ----------------------------------------------------------------- chain
def _fetch_related(client: httpx.Client, openalex_id: str, direction: str, cap: int | None) -> tuple[list[dict], bool]:
    """Works that ``openalex_id`` cites (backward) or that cite it (forward); ``(rows, capped)``."""
    flt = ("cited_by" if direction == "backward" else "cites") + f":{openalex_id}"
    rows: list[dict] = []
    cursor = "*"
    while cursor:
        d = _get_json(client, WORKS, {"filter": flt, "select": SELECT, "per-page": 200, "cursor": cursor})
        if d is None:
            break
        rows.extend(_work_row(w) for w in d.get("results", []))
        cursor = (d.get("meta") or {}).get("next_cursor")
        if cap and len(rows) >= cap:
            return rows[:cap], bool(cursor)
    return rows, False


def chain(project: Project, direction: str = "both", limit: int | None = None,
          client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch the references and/or citing works of every resolved paper not yet chained in that direction."""
    require_lookup(project)
    if direction not in ("both", "backward", "forward"):
        raise ValueError("direction must be both, backward or forward")
    dirs = ["backward", "forward"] if direction == "both" else [direction]
    st = project.store
    cap = project.config.prior_reviews.max_citing_per_paper
    n_new = {"backward": 0, "forward": 0}
    chained: set[int] = set()
    warnings: list[str] = []
    with lookup_client(project, "prior_chain", client) as client:   # logs the requests; its own switch is checked above
        for d in dirs:
            col = f"chained_{d}_at"
            todo = [dict(r) for r in st.query(f"SELECT paper_id, openalex_id, title FROM prior_review_papers "
                                              f"WHERE openalex_id IS NOT NULL AND {col} IS NULL ORDER BY paper_id")]
            if limit:
                todo = todo[:limit]
            for p in todo:
                rows, capped = _fetch_related(client, p["openalex_id"], d, cap if d == "forward" else None)
                if capped:
                    warnings.append(f"forward chaining of '{(p['title'] or p['openalex_id'])[:60]}' stopped at the cap of {cap} citing works")
                now = utcnow()
                with st.tx() as c:
                    for w in rows:
                        if not w["openalex_id"]:
                            continue
                        cur = c.execute(
                            "INSERT OR IGNORE INTO citation_chain(paper_id,direction,openalex_id,doi,title,year,authors,journal,"
                            "abstract,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (p["paper_id"], d, w["openalex_id"], w["doi"], w["title"], w["year"], w["authors"], w["journal"],
                             w["abstract"], now))
                        n_new[d] += cur.rowcount
                    c.execute(f"UPDATE prior_review_papers SET {col}=? WHERE paper_id=?", (now, p["paper_id"]))
                chained.add(p["paper_id"])
    unresolved = int(st.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE openalex_id IS NULL") or 0)
    if unresolved:
        warnings.append(f"{unresolved} papers are not resolved on OpenAlex and were skipped (run resolve first)")
    return {"papers_chained": len(chained), "n_backward": n_new["backward"], "n_forward": n_new["forward"],
            "n_new": n_new["backward"] + n_new["forward"], "warnings": warnings}


def export_chain(project: Project) -> dict[str, Any]:
    """Write the not-yet-exported chained works (deduplicated by OpenAlex id) to ``inputs/citation_chain/chain_<stamp>.csv``."""
    st = project.store
    # a work already written to an earlier chain file is not written again (it would duplicate a record already
    # imported), but its new rows are still marked exported so they stop counting as pending
    rows = st.query("SELECT c.*, r.label FROM citation_chain c JOIN prior_review_papers p ON p.paper_id=c.paper_id "
                    "JOIN prior_reviews r ON r.review_id=p.review_id WHERE c.exported_at IS NULL AND c.openalex_id NOT IN "
                    "(SELECT openalex_id FROM citation_chain WHERE exported_at IS NOT NULL) ORDER BY c.openalex_id")
    if not rows:
        st.execute("UPDATE citation_chain SET exported_at=? WHERE exported_at IS NULL", (utcnow(),))
        return {"file": None, "n_exported": 0}
    merged: dict[str, dict] = {}
    for r in rows:
        m = merged.setdefault(r["openalex_id"], {"row": dict(r), "sources": [], "directions": set()})
        m["sources"].append(f"{r['label']}#{r['paper_id']}")
        m["directions"].add(r["direction"])
    out_dir = project.path("inputs", PLATFORM)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"chain_{stamp()}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        for oid, m in merged.items():
            r = m["row"]
            w.writerow([r["title"] or "", r["abstract"] or "", r["doi"] or "", r["year"] or "", r["authors"] or "",
                        r["journal"] or "", oid, "; ".join(sorted(set(m["sources"]))), "+".join(sorted(m["directions"]))])
    now = utcnow()
    st.execute("UPDATE citation_chain SET exported_at=? WHERE exported_at IS NULL", (now,))
    return {"file": str(path.relative_to(project.root)), "path": str(path), "n_exported": len(merged)}


def chain_and_export(project: Project, direction: str = "both", limit: int | None = None,
                     client: httpx.Client | None = None, by: str | None = None,
                     seen_choice: str | None = None) -> dict[str, Any]:
    """``chain`` then ``export_chain``; records the stage result (setup → next: import). A chain file saved while the
    uploads are marked final reopens them (``notice``); under a frozen query with papers in its validation set the
    researcher's ``seen_choice`` is needed before any request (else ``SeenChoiceNeeded``, nothing run)."""
    from ..construct import artifacts
    from ..ingest.importer import reopen_if_final, upload_plan

    plan = upload_plan(project, "the chaining file")
    if plan["choice"] and artifacts.seen_choice(seen_choice) is None:
        raise artifacts.SeenChoiceNeeded(plan["asking"])
    res = chain(project, direction=direction, limit=limit, client=client)
    exp = export_chain(project)
    out = {**res, **exp}
    out["notice"] = reopen_if_final(project, by, Path(exp["file"]).name, seen_choice) if exp.get("file") else None
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_chain
    out["stage_result"] = record(project, summarize_chain(out)).as_dict()
    return out


__all__ = ["resolve", "chain", "export_chain", "chain_and_export"]
