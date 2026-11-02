"""PRISMA 2020 checklist (results-dashboard-and-ris, D7): the 27 items and sub-items of PRISMA 2020, each filled from
the reporting engine (prisma-reporting-assistant D11) or saying "not recorded by WIDER".

Page MJ, McKenzie JE, Bossuyt PM, et al. The PRISMA 2020 statement: an updated guideline for reporting systematic
reviews. BMJ 2021;372:n71. doi:10.1136/bmj.n71. The item names and sections are checked against the published
checklist by hand.

Each item's lines are the engine's evidence lines; nothing here is written by a model, and no abstract or benchmark
paper is named.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow

CITATION = ("Page MJ, McKenzie JE, Bossuyt PM, et al. The PRISMA 2020 statement: an updated guideline for reporting "
            "systematic reviews. BMJ 2021;372:n71. doi:10.1136/bmj.n71")
NOT_RECORDED = "not recorded by WIDER"
PACKAGED = "this replication package (MANIFEST.txt, manifest.json)"
SETTINGS_LABEL = "WIDER screening settings (not a review protocol)"
# (number, section, topic) as published; a sub-item keeps its topic's name
ITEMS = (
    ("1", "Title", "Title"), ("2", "Abstract", "Abstract"),
    ("3", "Introduction", "Rationale"), ("4", "Introduction", "Objectives"),
    ("5", "Methods", "Eligibility criteria"), ("6", "Methods", "Information sources"),
    ("7", "Methods", "Search strategy"), ("8", "Methods", "Selection process"),
    ("9", "Methods", "Data collection process"), ("10a", "Methods", "Data items"), ("10b", "Methods", "Data items"),
    ("11", "Methods", "Study risk of bias assessment"), ("12", "Methods", "Effect measures"),
    *((f"13{x}", "Methods", "Synthesis methods") for x in "abcdef"),
    ("14", "Methods", "Reporting bias assessment"), ("15", "Methods", "Certainty assessment"),
    ("16a", "Results", "Study selection"), ("16b", "Results", "Study selection"),
    ("17", "Results", "Study characteristics"), ("18", "Results", "Risk of bias in studies"),
    ("19", "Results", "Results of individual studies"),
    *((f"20{x}", "Results", "Results of syntheses") for x in "abcd"),
    ("21", "Results", "Reporting biases"), ("22", "Results", "Certainty of evidence"),
    *((f"23{x}", "Discussion", "Discussion") for x in "abcd"),
    *((f"24{x}", "Other information", "Registration and protocol") for x in "abc"),
    ("25", "Other information", "Support"), ("26", "Other information", "Competing interests"),
    ("27", "Other information", "Availability of data, code and other materials"),
)


def _eligibility(project: Project, info: dict | None) -> list[str]:
    """Item 5: the research questions and the codebook the protocol screened with (version and elements)."""
    from ..construct import artifacts
    from ..screen.codebook import load_prompt
    from .outputs import set_name

    out = [f"research question: {q}" for q in project.config.questions if q.strip()]
    settings = ((info or {}).get("settings") or {}) if info else {}
    pv = settings.get("prompt_version") if isinstance(settings, dict) else None
    try:
        _row, pdata = load_prompt(project, pv) if pv else (None, None)
    except ValueError:
        pdata = None
    if pdata and pdata.get("codebook_version"):
        cv, els = pdata["codebook_version"], pdata.get("elements") or []
        crow = next((r for r in artifacts.list_versions(project, "codebook") if r["version"] == cv), None)
    else:
        crow, cb = artifacts.load(project, "codebook")
        cv, els = (crow or {}).get("version"), [e.get("id") for e in (cb or {}).get("elements", []) if isinstance(e, dict)]
    if cv:
        used = f" used by protocol {set_name(info)}" + (f" (prompt v{pv})" if pv else "") if info else ""
        out.append(f"codebook v{cv}" + (f" ({crow['status']})" if crow else "") + used
                   + (f"; elements: {', '.join(str(e) for e in els)}" if els else ""))
    return out


def _selection(project: Project, info: dict | None) -> list[str]:
    """Item 8: each pass's model, the third read, the majority rule, records without a majority, the probability
    sample with its size and labellers."""
    from ..sample.design import sampling_log
    from ..sample.labels import progress
    from ..screen.agreement import pass_agreement, set_rules
    from ..screen.protocol import pass_models

    out: list[str] = []
    if info:
        key = info["key"]
        rules = set_rules(project, key)
        out += [f"pass {p}: {', '.join(ms)}" for p, ms in pass_models(project, key)["models"].items()]
        if rules["passes"] == 1:
            out.append("one pass per record")
        else:
            out.append("two independent reads; " + ("a blind third read on disagreement" if rules["blind_third_read"]
                                                     else "no third read") + f"; majority {rules['majority']} of 3"
                       + ("" if rules["recorded"] else " (the protocol's current rule; the set recorded none)"))
            routes = {r["route"]: r["count"] for r in pass_agreement(project, key)["routes"]}
            out.append(f"records without a majority stay at maybe for the researcher's review: {routes['split']} "
                       f"open, {routes['researcher']} decided by the researcher")
    prob = [(k, v) for k, v in sampling_log(project).items() if v["kind"] == "probability"]
    for name, v in prob:
        who = list(progress(project, name)["reviewers"])
        out.append(f"probability sample {name}: {v['n']} records" + (
            f", labelled by {', '.join(who)}" if who else ", not labelled yet"))
    return out


def _study_selection(c: dict) -> list[str]:
    """Item 16a: the counts of the PRISMA 2020 diagram for the set (``prisma_2020.json``)."""
    out = [f"records identified: {c['identified']:,} (databases {c['identified_databases']:,}, other methods "
           f"{c['identified_other']:,})",
           f"duplicates removed: {c['duplicates_removed']:,}" + (" (provisional)" if c["provisional"] else ""),
           f"records after duplicates removed: {c['unique']:,}"]
    if c.get("not_checked"):
        out.append(f"records not yet checked for duplicates: {c['not_checked']:,}")
    if c["excluded_file_records"]:
        out.append(f"records of files kept out of deduplication: {c['excluded_file_records']:,}")
    if c["protocol"]:
        out += [f"records screened (title and abstract, protocol {c['set_name']}): {c['screened']:,}; not screened: "
                f"{c['not_screened']:,}",
                f"records excluded: {c['excluded']:,}; included: {c['included_ta']:,}; maybe: {c['maybe_ta']:,}",
                f"reports sought for full-text reading: {c['sought']:,}; full-text assessment {NOT_RECORDED}"]
    return out


def _settings_hash(project: Project, info: dict | None) -> str:
    h = (info or {}).get("settings_hash") if info else None
    return (h or project.config.protocol.hash())[:12]


def build(project: Project, protocol: str | None = None, result_set: int | None = None,
          packaged: bool = False, attributor: Any = None) -> dict[str, Any]:
    """The items from the reporting engine (prisma-reporting-assistant D11), each {no, section, name, filled, lines,
    status, missing, researcher}. Refused before any import."""
    from . import reporting

    ctx = reporting.context(project, protocol, result_set, packaged=packaged)
    return reporting.checklist_doc(project, "prisma_2020", ctx, attributor)


def markdown(d: dict[str, Any], title: str) -> str:
    from .reporting import checklist_markdown

    return checklist_markdown("prisma_2020", d, title)


def write(project: Project, protocol: str | None = None, result_set: int | None = None, folder: Path | None = None,
          packaged: bool = False) -> dict[str, Any]:
    """Write ``prisma_2020_checklist.md`` and ``.json`` to ``exports/`` (or ``folder``, as the package does with the
    packaged item 27). WIDER's lines follow ``exports.attribution`` through one ``Attributor``; the researcher's text
    is written as typed (D25). Nothing is written when the checklist is refused."""
    from ..core.researchers import Attributor
    from .reporting import status_counts

    d = build(project, protocol, result_set, packaged=packaged, attributor=Attributor(project))
    out = folder or project.path("exports")
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / "prisma_2020_checklist.md", out / "prisma_2020_checklist.json"
    js.write_text(json.dumps({**d, "written_at": utcnow()}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md.write_text(markdown(d, project.config.title or project.config.name), encoding="utf-8")
    n = sum(it["filled"] for it in d["items"])
    rel = (lambda p: str(p)) if folder else (lambda p: str(p.relative_to(project.root)))
    return {**d, "path": rel(md), "json_path": rel(js), "n_filled": n,
            "message": f"PRISMA 2020 checklist written: {status_counts(d)}"}
