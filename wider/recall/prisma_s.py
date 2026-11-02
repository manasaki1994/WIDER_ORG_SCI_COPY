"""PRISMA-S search report (change 2c, D17): the 16 items of PRISMA-S, each filled from the store by a fixed map or
saying "not recorded".

Rethlefsen ML, Kirtley S, Waffenschmidt S, et al. PRISMA-S: an extension to the PRISMA Statement for Reporting
Literature Searches in Systematic Reviews. Systematic Reviews 2021;10:39. doi:10.1186/s13643-020-01542-z. The item
names are checked against the published checklist by hand.

Nothing here is written by a model: the peer-review item counts the second reader's findings and names its readers,
never a finding's text. No benchmark paper is named (counts only), so a locked validation set stays unnamed.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow

CITATION = ("Rethlefsen ML, Kirtley S, Waffenschmidt S, et al. PRISMA-S: an extension to the PRISMA Statement for "
            "Reporting Literature Searches in Systematic Reviews. Systematic Reviews 2021;10:39. "
            "doi:10.1186/s13643-020-01542-z")
NOT_RECORDED = "not recorded"
DATES_LABEL = "import date; search date not recorded"
PEER_LABEL = "a model second reader, not a human peer review"
ITEMS = ((1, "Database name"), (2, "Multi-database searching"), (3, "Study registries"),
         (4, "Online resources and browsing"), (5, "Citation searching"), (6, "Contacts"), (7, "Other methods"),
         (8, "Full search strategies"), (9, "Limits and restrictions"), (10, "Search filters"), (11, "Prior work"),
         (12, "Updates"), (13, "Dates of searches"), (14, "Peer review"), (15, "Total records"),
         (16, "Deduplication"))


def _sources(project: Project) -> list[dict[str, Any]]:
    return [dict(r) for r in project.store.query(
        "SELECT source_id, platform, file_name, n_records, imported_at, included, sample_of FROM sources "
        "ORDER BY source_id")]


def sample_note(s: dict) -> str:
    """", a random sample of 650,000 results" for a file marked as a sample (openalex-profile-and-sampled-exports D8)."""
    return f", a random sample of {s['sample_of']:,} results" if s.get("sample_of") else ""


def _label(project: Project, key: str) -> str:
    from ..search.render import OTHER_SOURCES, platforms

    if key in OTHER_SOURCES:
        return OTHER_SOURCES[key]
    return (platforms().get(key) or {}).get("label") or key


def _chaining(project: Project) -> list[str]:
    runs = [dict(r) for r in project.store.query(
        "SELECT at, service, n_items, n_failed FROM external_requests WHERE purpose='seed_chain' ORDER BY id")]
    if not runs:
        return []
    out = [f"seed chaining run of {r['at'][:10]} on the Development seed papers ({r['service']}, {r['n_items']} "
           "item(s) sent" + (f", {r['n_failed']} failed request(s)" if r["n_failed"] else "") + ")" for r in runs]
    rows = [dict(r) for r in project.store.query("SELECT direction, status FROM seed_chain_candidates")]
    if rows:
        by_dir = Counter(r["direction"] or "unknown" for r in rows)
        by_status = Counter(r["status"] or "open" for r in rows)
        out.append("candidates: " + ", ".join(f"{n} {d}" for d, n in sorted(by_dir.items()))
                   + "; " + ", ".join(f"{n} {s}" for s, n in sorted(by_status.items())))
    return out


def _other_methods(project: Project) -> list[str]:
    papers = project.read_benchmark()
    if not papers:
        return []
    c = Counter((b.source.split(":", 1)[0] or "researcher", b.set) for b in papers)
    return ["benchmark papers (counts only): " + ", ".join(f"{n} {s} from {src}" for (src, s), n in sorted(c.items()))]


def _prior(project: Project) -> list[str]:
    return [f"{r['label']} ({r['kind']}, {r['n_papers']} paper(s), added {r['added_at'][:10]})"
            for r in project.store.query("SELECT label, kind, n_papers, added_at FROM prior_reviews ORDER BY review_id")]


def _peer_review(project: Project) -> list[str]:
    from ..search.review import RUN_KIND

    base = project.root / "runs" / RUN_KIND
    out = []
    for f in sorted(base.glob("*/review.json")) if base.exists() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        readers = ", ".join(f"{x.get('name')} ({x.get('model') or 'no model'})" for x in r.get("readers") or [])
        failed = r.get("failed") or 0
        out.append(f"{PEER_LABEL}: run {f.parent.name} on synonyms v{r.get('synonyms_version')}, readers {readers}; "
                   f"{r.get('agreed', 0)} agreed and {r.get('unsure', 0)} unsure finding(s)"
                   + (f"; {failed} call(s) failed, so the run is incomplete" if failed else ""))
    return out


def _totals(project: Project, srcs: list[dict]) -> list[str]:
    if not srcs:
        return []
    out = [f"{_label(project, s['platform'])}: {s['n_records']:,} records ({s['file_name']}){sample_note(s)}"
           for s in srcs]
    return out + [f"total: {sum(s['n_records'] for s in srcs):,} records from {len(srcs)} export(s)"]


def _dedup(project: Project) -> list[str]:
    from ..core.stage_result import last_result

    r = last_result(project, "dedup")
    if not r:
        return []
    out = [f"WIDER deduplication ({(r.get('ran_at') or '')[:10]}): {r.get('headline', '')}"]
    m = r.get("metrics") or {}
    return out + ([" · ".join(f"{k}: {v}" for k, v in m.items())] if m else [])


def build(project: Project, attributor: Any = None) -> dict[str, Any]:
    """The 16 items from the reporting engine (prisma-reporting-assistant D11), each {no, name, filled, lines, status,
    missing, researcher}. Before any import every item reads "Researcher input needed" with the one line "nothing
    imported yet", so the replication package still builds."""
    from . import reporting

    try:
        ctx = reporting.context(project)
    except ValueError:
        return {"citation": CITATION, "items": [
            {"no": no, "name": name, "filled": False, "lines": ["nothing imported yet"], "status": reporting.INPUT,
             "missing": [], "researcher": {"text": "", "your_text": "none", "saved_for": None, "where": "",
                                           "not_applicable": None}} for no, name in ITEMS]}
    return reporting.checklist_doc(project, "prisma_s", ctx, attributor)


def markdown(report: dict[str, Any], title: str) -> str:
    from .reporting import checklist_markdown

    return checklist_markdown("prisma_s", report, title)


def write(project: Project) -> dict[str, Any]:
    """Write ``exports/prisma_s.md`` and ``exports/prisma_s.json``; WIDER's lines through one ``Attributor``, the
    researcher's text as typed (D25)."""
    from ..core.researchers import Attributor
    from .reporting import status_counts

    rep = build(project, Attributor(project))
    d = project.path("exports")
    d.mkdir(exist_ok=True)
    (d / "prisma_s.json").write_text(json.dumps({**rep, "written_at": utcnow()}, indent=1, ensure_ascii=False) + "\n",
                                     encoding="utf-8")
    (d / "prisma_s.md").write_text(markdown(rep, project.config.title or project.config.name), encoding="utf-8")
    n = sum(it["filled"] for it in rep["items"])
    return {**rep, "path": "exports/prisma_s.md", "json_path": "exports/prisma_s.json", "n_filled": n,
            "message": f"PRISMA-S written: {status_counts(rep)}"}
