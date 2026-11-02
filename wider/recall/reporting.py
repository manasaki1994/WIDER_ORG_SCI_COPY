"""PRISMA reporting assistant (prisma-reporting-assistant): the PRISMA 2020 and PRISMA-S items as data
(``prisma_items.yaml``, design D1), the evidence lines WIDER recorded for each with the elements they cover (D2), the
status computed from that evidence only (D3) and what is still missing (D4).

Evidence is recomputed on every view and export and never stored; viewing records nothing (D16): no function here
calls ``build_report`` or ``estimate``, and only ``export`` draws the diagram with ``write_prisma`` (D10).
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..core.project import Project

CHECKLISTS = ("prisma_2020", "prisma_s")
TITLES = {"prisma_2020": "PRISMA 2020", "prisma_s": "PRISMA-S"}
SOURCES = {"prisma_2020": "Page et al. 2021", "prisma_s": "Rethlefsen et al. 2021"}
AUTO, PART, INPUT, NA = ("Automatically documented", "Partially documented", "Researcher input needed",
                         "Not applicable")
STATUSES = (AUTO, PART, INPUT, NA)
NOT_RECORDED = "WIDER does not record this"
PART_SAY = "WIDER records part of this"
OUTSIDE = "WIDER does not run this stage; write what you did, or mark the item not applicable."
NOT_IMPORTED = "nothing imported yet; the checklist starts from the database exports"
_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{2,})(?![\w])")


# ------------------------------------------------------------------------------------------------ catalogue (D1)
@lru_cache(maxsize=1)
def catalogue() -> dict[str, list[dict[str, Any]]]:
    """Both checklists in PRISMA order. Item numbers are strings on both (``"16a"``, ``"1"``); each PRISMA-S item gets
    ``related`` (its PRISMA 2020 items) from the reverse of the PRISMA 2020 links."""
    raw = yaml.safe_load((Path(__file__).parent / "prisma_items.yaml").read_text(encoding="utf-8"))
    out: dict[str, list[dict[str, Any]]] = {}
    for cl in CHECKLISTS:
        out[cl] = [{"no": str(it["no"]), "section": it["section"], "topic": it["topic"], "asks": it["asks"],
                    "official": it.get("official") or "", "elements": [dict(e) for e in it["elements"]],
                    "outside_wider": bool(it.get("outside_wider")), "draft": bool(it.get("draft")),
                    "related": [str(r) for r in it.get("related") or []]} for it in raw[cl]]
    back: dict[str, list[str]] = {}
    for it in out["prisma_2020"]:
        for r in it["related"]:
            back.setdefault(r, []).append(it["no"])
    for it in out["prisma_s"]:
        it["related"] = back.get(it["no"], [])
    return out


def checklist_key(name: str) -> str:
    """``prisma-2020``, ``prisma_2020``, ``prisma-s`` and ``prisma_s`` name the two checklists."""
    k = (name or "").strip().lower().replace("-", "_")
    if k not in CHECKLISTS:
        raise ValueError(f"unknown checklist {name!r}; one of prisma-2020, prisma-s")
    return k


def item(checklist: str, no: str) -> dict[str, Any]:
    cl = checklist_key(checklist)
    for it in catalogue()[cl]:
        if it["no"] == str(no).strip().lower():
            return it
    raise ValueError(f"unknown item {no!r} in {TITLES[cl]}")


def other(checklist: str) -> str:
    return "prisma_s" if checklist_key(checklist) == "prisma_2020" else "prisma_2020"


# ------------------------------------------------------------------------------------------------ lines (D2)
def numbers(text: str) -> list[str]:
    """The numbers of a text as D8 reads them: integers of two or more digits or with a thousands separator, and
    decimals, with the separators removed ("1,204" -> "1204")."""
    return [m.replace(",", "") for m in _NUM.findall(text or "")]


def line(text: str, source: str, covers: dict[str, str] | None = None, hash_text: str | None = None) -> dict[str, Any]:
    """One evidence line; empty ``covers`` makes it context. ``hash_text`` replaces the text in the evidence hash
    when the text holds what changes on every build (D7)."""
    d = {"text": text, "source": source, "covers": dict(covers or {}), "numbers": numbers(text)}
    if hash_text is not None:
        d["hash_text"] = hash_text
    return d


def _short(key: str) -> str:
    """A platform as reporting names it (D12): the short ``name`` of a multi-database platform, else its label."""
    from ..search.render import OTHER_SOURCES, platforms

    if key in OTHER_SOURCES:
        return OTHER_SOURCES[key]
    p = platforms().get(key) or {}
    return p.get("name") or p.get("label") or key


def _single_db(key: str) -> bool:
    """A catalogue platform that searches one database (D12)."""
    from ..search.render import platforms

    p = platforms().get(key)
    return bool(p) and not p.get("multi_database")


# ------------------------------------------------------------------------------------------------ context (D2, D16)
def context(project: Project, protocol: str | None = None, result_set: int | None = None,
            packaged: bool = False) -> dict[str, Any]:
    """What the evidence functions read, built once per view: the set (None before any screening), the imports, the
    PRISMA counts and the stored recall estimate. ValueError before any import."""
    from ..screen.result_sets import row_by_key
    from . import prisma_s
    from .outputs import _default_protocol, prisma_counts

    if not project.store.scalar("SELECT COUNT(*) FROM sources"):
        raise ValueError(NOT_IMPORTED)
    info = _default_protocol(project, protocol, result_set)
    if info:
        info = {**info, **{k: v for k, v in (row_by_key(project, info["key"]) or {}).items()
                           if k in ("settings", "settings_hash")}}
    c = prisma_counts(project, info["protocol"] if info else None, info["result_set"] if info else None)
    return {"info": info, "counts": c, "sources": prisma_s._sources(project), "packaged": packaged,
            "recall": _stored_recall(project, info), "dedup_stale": _dedup_stale(project)}


def _dedup_stale(project: Project) -> bool:
    """Deduplication ran before the newest import."""
    from ..core.stage_result import last_result

    r = last_result(project, "dedup")
    newest = project.store.scalar("SELECT MAX(imported_at) FROM sources")
    return bool(r and newest and (r.get("ran_at") or "") < newest)


def _stored_recall(project: Project, info: dict | None) -> dict[str, Any] | None:
    """The stored recall estimate of the protocol's probability sample for this set, read as the Results dashboard
    reads it (never estimated here, D16): ``{lines, outcome}``, or None when there is none, it is for another set or
    it is stale."""
    if not info:
        return None
    from ..screen.result_sets import active_set
    from .dashboard import _curve, _recall

    sample = project.config.protocol.sampling.probability_sample
    act = active_set(project, info["protocol"])
    active = act["result_set"] if act else None
    try:
        rec, last = _recall(project, info, sample, sample, active)
        if rec["state"] != "current":
            return None
        chart = _curve(project, info, sample, rec, last, active)
    except (ValueError, KeyError):
        return None
    if rec.get("stale") or chart.get("state") == "stale":
        return None
    keep = [ln for ln in rec["lines"] if not ln.startswith(("Label the", "Estimate recall"))]
    return {"lines": keep, "outcome": rec.get("outcome"), "warning_lines": rec.get("warning_lines") or []}


RECALL_GAP = "recall estimate: not recorded by WIDER"


# ------------------------------------------------------------------------------------------------ PRISMA 2020 evidence
def _p4(project, ctx):
    return [line(f"research question: {q}", "project settings", {"question": "part"})
            for q in project.config.questions if q.strip()]


def _p5(project, ctx):
    from .prisma_2020 import _eligibility

    out = []
    for ln in _eligibility(project, ctx["info"]):
        if ln.startswith("research question:"):
            out.append(line(ln, "project settings"))
        else:
            out.append(line(ln, "codebook", {"criteria": "full"}))
    return out


def _db_sources(ctx):
    from ..search.render import OTHER_SOURCES

    return [s for s in ctx["sources"] if s["platform"] not in OTHER_SOURCES]


def _chain_sources(ctx):
    from ..search.render import OTHER_SOURCES

    return [s for s in ctx["sources"] if s["platform"] in OTHER_SOURCES]


def _db_line(s: dict) -> str:
    from .prisma_s import sample_note

    return (f"{_short(s['platform'])}: export {s['file_name']}, {s['n_records']:,} records{sample_note(s)}"
            + ("" if s["included"] else " (left out of deduplication)"))


def _p6(project, ctx):
    from . import prisma_s

    dbs = _db_sources(ctx)
    level = "full" if dbs and all(_single_db(s["platform"]) for s in dbs) else "part"
    out = [line(_db_line(s), "imports", {"databases": level}) for s in dbs]
    out += [line(ln, "seed chaining runs", {"other_sources": "part"}) for ln in prisma_s._chaining(project)]
    out += [line(f"citation searching: export {s['file_name']}, {s['n_records']:,} records", "imports",
                 {"other_sources": "part"}) for s in _chain_sources(ctx)]
    out += [line(f"{_short(s['platform'])}: imported {s['imported_at'][:10]} (import date; search date not recorded)",
                 "imports") for s in ctx["sources"]]
    return out


def _p6_notes(project, ctx):
    dbs = _db_sources(ctx)
    multi = [s for s in dbs if not _single_db(s["platform"])]
    return {"databases": [f"which databases were searched on {', '.join(dict.fromkeys(_short(s['platform']) for s in multi))}"
                          " is not recorded"] if multi else [],
            "other_sources": ["only citation searching is recorded"]}


def _archived(project) -> dict[str, dict]:
    from ..search.archive import entries

    out: dict[str, dict] = {}
    for e in entries(project):
        out[e["platform"]] = e   # the newest string of each platform
    return out


def _p7(project, ctx):
    from ..search.archive import NO_LEDGER
    from ..search.limits import limits, limits_line

    arch = _archived(project)
    out = [line(f"{_short(k)} (synonyms v{e['synonyms_version']}, archived {e['archived_at'][:10]}): "
                f"search_strings/{e['file']}; ledger: " + (f"search_strings/{e['ledger']}" if e["ledger"] else NO_LEDGER),
                "search strings archive", {"strategies": "part"}) for k, e in arch.items()]
    lim = limits(project)
    out += [line(f"{_short(k)}: {limits_line(v)} (not confirmed as applied)", "project settings, limits",
                 {"filters_limits": "part"}) for k, v in lim.items() if v]
    return out


def _p7_notes(project, ctx):
    from ..search.limits import limits

    arch = _archived(project)
    plats = list(dict.fromkeys(s["platform"] for s in _db_sources(ctx)))
    no_str = [_short(k) for k in plats if k not in arch]
    lim = limits(project)
    no_lim = [_short(k) for k in plats if not lim.get(k)]
    return {"strategies": ([f"no archived string for {', '.join(no_str)}"] if no_str else [])
            + ["not confirmed as run as archived"],
            "filters_limits": ([f"no limits recorded for {', '.join(no_lim)}"] if no_lim else [])
            + ["not confirmed as applied"]}


def _p8(project, ctx):
    from ..screen.protocol import pass_models
    from .prisma_2020 import _selection

    info = ctx["info"]
    if not info:
        return []
    pm = pass_models(project, info["key"])
    shared = list(pm.get("shared") or [])
    out = []
    for ln in _selection(project, info):
        if ln.startswith("pass "):
            out.append(line(ln, "screening runs", {"automation": "full"}))
        elif ln.startswith(("one pass per record", "two independent reads")):
            out.append(line(f"{ln}; codebook-based decisions", "screening protocol",
                            {"method": "full", "reviewers_per_record": "full"}))
        elif ln.startswith("records without a majority"):
            out.append(line(ln, "screening decisions"))
        else:
            out.append(line(ln, "samples"))
    if len(pm["models"]) >= 2:
        if shared:
            out += [line(s, "screening runs", {"independence": "part"}) for s in shared]
        else:
            out.append(line("the passes used different models", "screening runs", {"independence": "full"}))
    return out


def _p8_notes(project, ctx):
    return {"independence": ["passes sharing one model are not independent readers"]}


def _p16a(project, ctx):
    from .prisma_2020 import _study_selection

    c = ctx["counts"]
    out = [line(ln, "prisma_2020.json counts", {"counts": "part"}) for ln in _study_selection(c)]
    if out:
        out.append(line("PRISMA 2020 diagram drawn from these counts (exports/prisma_2020.svg)", "PRISMA 2020 diagram",
                        {"flow_diagram": "full"}))
    if c["provisional"]:
        out.append(line(c["provisional_line"], "deduplication"))
    if ctx["dedup_stale"]:
        out.append(line("deduplication ran before the newest import; run it again", "deduplication"))
    return out


def _p16a_notes(project, ctx):
    return {"counts": ["full-text assessment and the included studies are not recorded"]}


def _p23c(project, ctx):
    from ..screen.protocol import pass_models
    from ..search.coverage import validity_lines, validity_note

    out = []
    if project.read_benchmark():
        out += [line(ln, "search validation note", {"process_limits": "part"})
                for ln in validity_lines(validity_note(project))]
    rec = ctx["recall"]
    if rec:
        out += [line(ln, "recall estimate", {"process_limits": "part"}) for ln in rec["lines"]]
    else:
        out.append(line(RECALL_GAP, "recall estimate"))
    if ctx["info"]:
        out += [line(s, "screening runs", {"process_limits": "part"})
                for s in pass_models(project, ctx["info"]["key"]).get("shared") or []]
    return out


def _p24b(project, ctx):
    from .prisma_2020 import SETTINGS_LABEL, _settings_hash

    return [line(f"{SETTINGS_LABEL}: hash {_settings_hash(project, ctx['info'])}", "screening settings")]


def _p27(project, ctx):
    from .prisma_2020 import PACKAGED

    if ctx["packaged"]:
        return [line(PACKAGED, "replication package", {"materials": "part"},
                     hash_text="replication package built")]
    zips = sorted(project.path("exports").glob("replication_package_*.zip"))
    if not zips:
        return []
    return [line(f"replication package exports/{zips[-1].name} (MANIFEST.txt, manifest.json)", "replication package",
                 {"materials": "part"}, hash_text="replication package built")]


# ------------------------------------------------------------------------------------------------ PRISMA-S evidence
def _s1(project, ctx):
    return [line(_db_line(s), "imports", {"database_and_platform": "full" if _single_db(s["platform"]) else "part"})
            for s in _db_sources(ctx)]


def _s1_notes(project, ctx):
    multi = list(dict.fromkeys(_short(s["platform"]) for s in _db_sources(ctx) if not _single_db(s["platform"])))
    return {"database_and_platform": [f"which databases on {', '.join(multi)}: not recorded by WIDER"] if multi else []}


def _s2(project, ctx):
    from ..search.render import platforms

    multi = list(dict.fromkeys(s["platform"] for s in _db_sources(ctx)
                               if (platforms().get(s["platform"]) or {}).get("multi_database")))
    if not multi:
        return [line("no multi-database platform imported; mark the item not applicable if none was used", "imports")]
    return [line(f"multi-database platform used: {_short(k)}", "imports") for k in multi]


def _s5(project, ctx):
    from . import prisma_s

    out = [line(ln, "seed chaining runs", {"directions": "full", "procedure": "part"})
           for ln in prisma_s._chaining(project)]
    dirs = {r["direction"]: r["n"] for r in project.store.query(
        "SELECT direction, COUNT(*) n FROM citation_chain GROUP BY direction ORDER BY direction")}
    for s in _chain_sources(ctx):
        d = ", ".join(f"{n:,} {k}" for k, n in dirs.items())
        out.append(line(f"citation searching: export {s['file_name']}, {s['n_records']:,} records"
                        + (f" (directions recorded: {d})" if d else ""), "imports",
                        {"directions": "part", "procedure": "part"}))
    return out


def _s7(project, ctx):
    from . import prisma_s

    return [line(ln, "benchmark papers", {"other_methods": "part"}) for ln in prisma_s._other_methods(project)]


def _s8(project, ctx):
    return [dict(ln, covers={"exact_strings": "part"}) for ln in _p7(project, ctx) if "strategies" in ln["covers"]]


def _s8_notes(project, ctx):
    return {"exact_strings": _p7_notes(project, ctx)["strategies"]}


def _s9(project, ctx):
    return [dict(ln, covers={"limits": "part"}) for ln in _p7(project, ctx) if "filters_limits" in ln["covers"]]


def _s11(project, ctx):
    from . import prisma_s

    return [line(f"prior review: {ln} (seed papers, not a reused search strategy)", "prior reviews")
            for ln in prisma_s._prior(project)]


def _s13(project, ctx):
    return [line(f"{_short(s['platform'])}: imported {s['imported_at'][:10]} (import date; search date not recorded)",
                 "imports") for s in ctx["sources"]]


def _s14(project, ctx):
    from . import prisma_s

    return [line(ln, "search review runs", {"peer_review": "part"}) for ln in prisma_s._peer_review(project)]


def _s15(project, ctx):
    srcs = ctx["sources"]
    if not srcs:
        return []
    out = [line(f"{_short(s['platform'])}: {s['n_records']:,} records ({s['file_name']})", "imports",
                {"totals": "full"}) for s in srcs]
    return out + [line(f"total: {sum(s['n_records'] for s in srcs):,} records from {len(srcs)} export(s)", "imports",
                       {"totals": "full"})]


def _s16(project, ctx):
    from . import prisma_s

    lines = prisma_s._dedup(project)
    if not lines:
        return []
    final = not ctx["counts"]["provisional"] and not ctx["dedup_stale"]
    out = [line(lines[0], "deduplication stage result", {"dedup_process": "full" if final else "part",
                                                          "software": "full"})]
    out += [line(ln, "deduplication stage result") for ln in lines[1:]]
    if ctx["counts"]["provisional"]:
        out.append(line(ctx["counts"]["provisional_line"], "deduplication"))
    if ctx["dedup_stale"]:
        out.append(line("deduplication ran before the newest import; run it again", "deduplication"))
    return out


EVIDENCE = {
    "prisma_2020": {"4": _p4, "5": _p5, "6": _p6, "7": _p7, "8": _p8, "16a": _p16a, "23c": _p23c, "24b": _p24b,
                    "27": _p27},
    "prisma_s": {"1": _s1, "2": _s2, "5": _s5, "7": _s7, "8": _s8, "9": _s9, "11": _s11, "13": _s13, "14": _s14,
                 "15": _s15, "16": _s16},
}
NOTES = {"prisma_2020": {"6": _p6_notes, "7": _p7_notes, "8": _p8_notes, "16a": _p16a_notes},
         "prisma_s": {"1": _s1_notes, "8": _s8_notes}}
NO_STAGE = {("prisma_2020", n) for n in ("1", "2", "3", "16b")}


def evidence(project: Project, checklist: str, no: str, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """The item's evidence lines (D2)."""
    cl = checklist_key(checklist)
    it = item(cl, no)
    fn = EVIDENCE[cl].get(it["no"])
    return fn(project, ctx) if fn else []


def coverage(it: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, str | None]:
    """Each element's coverage: "full" when every line covering it covers it fully, "part" when any covers it in part,
    None when none covers it."""
    out: dict[str, str | None] = {}
    for e in it["elements"]:
        levels = [ln["covers"][e["key"]] for ln in lines if e["key"] in ln["covers"]]
        out[e["key"]] = None if not levels else ("part" if "part" in levels else "full")
    return out


def status(it: dict[str, Any], lines: list[dict[str, Any]]) -> str:
    """D3: from WIDER's evidence only; "Not applicable" is the researcher's mark, applied by the caller."""
    cov = coverage(it, lines).values()
    if cov and all(v == "full" for v in cov):
        return AUTO
    return PART if any(cov) else INPUT


def missing(project: Project, checklist: str, it: dict[str, Any], lines: list[dict[str, Any]],
            ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """The elements not covered fully, each with what WIDER lacks (D4)."""
    cl = checklist_key(checklist)
    cov = coverage(it, lines)
    fn = NOTES[cl].get(it["no"])
    notes = fn(project, ctx) if fn else {}
    out = []
    for e in it["elements"]:
        c = cov[e["key"]]
        if c == "full":
            continue
        if c == "part":
            say = PART_SAY + (f": {'; '.join(notes.get(e['key']) or [])}"
                                                   if notes.get(e["key"]) else "")
        else:
            say = NOT_RECORDED
        out.append({"key": e["key"], "text": e["text"], "coverage": c, "say": say,
                    "outside_wider": bool(e.get("outside_wider") or it["outside_wider"])})
    return out


def view(project: Project, checklist: str, no: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """One item as ``show`` and the page give it, without the researcher's text: what PRISMA asks, what WIDER
    recorded, what is still missing and the item's status from evidence."""
    cl = checklist_key(checklist)
    it = item(cl, no)
    lines = evidence(project, cl, it["no"], ctx)
    return {"checklist": cl, "no": it["no"], "section": it["section"], "topic": it["topic"],
            "official": it["official"], "source": SOURCES[cl], "asks": it["asks"], "elements": it["elements"],
            "related_nos": it["related"], "outside_wider": it["outside_wider"], "outside_sentence": OUTSIDE if it["outside_wider"] else None,
            "evidence": lines, "coverage": coverage(it, lines), "missing": missing(project, cl, it, lines, ctx),
            "wider_status": status(it, lines)}


def show(project: Project, checklist: str, no: str, ctx: dict[str, Any], cache: dict | None = None) -> dict[str, Any]:
    """``view`` plus the researcher's side (D5-D8) and the related items of the other checklist with their topic,
    status and text (D14): what ``show``, the routes and the page give."""
    v = view(project, checklist, no, ctx)
    yours = text_state(project, v["checklist"], v["no"], ctx, v["evidence"], cache)
    for m in v["missing"]:
        m["covered_by_you"] = m["key"] in yours["covers"] and bool(yours["text"].strip())
    v["yours"] = yours
    v["status"] = NA if yours["not_applicable"] else v["wider_status"]
    v["has_evidence"] = HAS_EVIDENCE if yours["not_applicable"] and any(ln["covers"] for ln in v["evidence"]) else None
    ocl = other(v["checklist"])
    v["related"] = [_related_row(project, ocl, n, ctx, cache) for n in v["related_nos"]]
    v["related_heading"] = related_heading(v["checklist"], v["related_nos"])
    return v


RELATE = {"prisma_2020": "PRISMA-S, the PRISMA extension for reporting literature searches, details items 6, 7 and 16a "
                         "(sources, search strategies, records found); both checklists read the same WIDER records.",
          "prisma_s": "An extension to the PRISMA Statement for reporting literature searches; its items detail "
                      "PRISMA 2020 items 6, 7 and 16a."}


def related_heading(checklist: str, nos: list[str]) -> str | None:
    """The heading of an item's related-items block (D14): "PRISMA-S reports this in detail:" on PRISMA 2020 items,
    "Part of PRISMA 2020 item 7:" on PRISMA-S items; None without related items."""
    if not nos:
        return None
    if checklist_key(checklist) == "prisma_2020":
        return "PRISMA-S reports this in detail:"
    return f"Part of PRISMA 2020 item{'s' if len(nos) > 1 else ''} {', '.join(nos)}:"


def _related_row(project: Project, checklist: str, no: str, ctx: dict[str, Any], cache: dict | None) -> dict[str, Any]:
    it = item(checklist, no)
    lines = evidence(project, checklist, it["no"], ctx)
    rec = read_text(project, checklist, it["no"]) or {}
    text = "" if rec.get("unreadable") else (rec.get("text") or "")
    return {"checklist": checklist, "no": it["no"], "topic": it["topic"],
            "status": NA if rec.get("not_applicable") else status(it, lines), "text": text,
            "first_line": text.strip().splitlines()[0] if text.strip() else ""}


def status_rows(project: Project, checklist: str, protocol: str | None = None, result_set: int | None = None,
                to_check: bool = False, only: str | None = None, ctx: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """One row per item in PRISMA order (``wider report prisma status``, the page's table, D3): item, section, topic,
    status (Not applicable when marked), WIDER's status and the researcher's text state; ``to_check`` and ``only``
    (a status) filter the rows."""
    cl = checklist_key(checklist)
    if only is not None and only not in STATUSES:
        raise ValueError(f"unknown status {only!r}; one of {', '.join(STATUSES)}")
    ctx = ctx or context(project, protocol, result_set)
    cache: dict = {}
    rows = []
    for it in catalogue()[cl]:
        lines = evidence(project, cl, it["no"], ctx)
        ws = status(it, lines)
        y = text_state(project, cl, it["no"], ctx, lines, cache)
        rows.append({"checklist": cl, "no": it["no"], "section": it["section"], "topic": it["topic"],
                     "status": NA if y["not_applicable"] else ws, "wider_status": ws,
                     "not_applicable": y["not_applicable"], "your_text": y["your_text"],
                     "your_text_label": y["your_text_label"], "gaps_left": y["gaps_left"],
                     "saved_for": y["saved_for"], "to_check": y["to_check"], "where": y["where"]})
    if to_check:
        rows = [x for x in rows if x["to_check"]]
    if only:
        rows = [x for x in rows if x["status"] == only]
    return rows


# ------------------------------------------------------------------------------------------------ your text (D5, D6)
REPORT_DIR = "reporting"
SET_ITEMS = {"5", "8", "16a", "23c", "24b"}      # PRISMA 2020 items whose evidence follows the set (D7)
MAX_VERSIONS = 20
UNREADABLE = "could not read your text for this item"
SET_GONE = "the set this text was saved for no longer exists"
COVERED_BY_YOU = "you marked as covered by your text"
HAS_EVIDENCE = "WIDER recorded evidence for this item"
_GAP = re.compile(r"\[[^\[\]]*(?:not recorded by WIDER|researcher input needed)\]")


def item_label(checklist: str, no: str) -> str:
    return f"{TITLES[checklist_key(checklist)]} item {no}"


def _path(checklist: str, no: str) -> str:
    return f"{REPORT_DIR}/{checklist}/{no}.json"


def _set_dependent(checklist: str, no: str) -> bool:
    return checklist == "prisma_2020" and no in SET_ITEMS


def read_text(project: Project, checklist: str, no: str) -> dict[str, Any] | None:
    """The item's file, None when there is none, ``{"unreadable": True}`` when it is not valid JSON (D6)."""
    f = project.root / _path(checklist_key(checklist), item(checklist, no)["no"])
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"unreadable": True}
    return d if isinstance(d, dict) else {"unreadable": True}


def evidence_hash(lines: list[dict[str, Any]]) -> str:
    """SHA-256 over the lines' text (or ``hash_text``) and covers only (D7)."""
    canon = [{"text": ln.get("hash_text", ln["text"]), "covers": ln["covers"]} for ln in lines]
    return hashlib.sha256(json.dumps(canon, sort_keys=True).encode("utf-8")).hexdigest()


def _snapshot(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"text": ln["text"], "source": ln["source"], "numbers": ln["numbers"]} for ln in lines]


def _ensure_gitignore(project: Project) -> None:
    """An existing project's ``.gitignore`` gains ``reporting/`` at the first reporting save (D25, Q7)."""
    gi = project.root / ".gitignore"
    if not gi.exists():
        return
    lines = gi.read_text(encoding="utf-8").splitlines()
    if f"{REPORT_DIR}/" not in (x.strip() for x in lines):
        gi.write_text("\n".join(lines + [f"{REPORT_DIR}/"]) + "\n", encoding="utf-8")


def _saved_lines(project: Project, checklist: str, no: str, rec: dict[str, Any], ctx: dict[str, Any],
                 cache: dict | None = None) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """The item's evidence now, for the protocol and set stored with the save, never packaged (D7); (None, None)
    when that set no longer exists."""
    np_ctx = {**ctx, "packaged": False} if ctx.get("packaged") else ctx
    if _set_dependent(checklist, no) and rec.get("protocol"):
        info = ctx.get("info") or {}
        if (info.get("protocol"), info.get("result_set")) != (rec["protocol"], rec.get("result_set")):
            key = (rec["protocol"], rec.get("result_set"))
            cache = {} if cache is None else cache
            if key not in cache:
                try:
                    cache[key] = context(project, *key)
                except ValueError:
                    cache[key] = None
            np_ctx = cache[key]
            if np_ctx is None:
                return None, None
    return evidence(project, checklist, no, np_ctx), np_ctx


def _shape(text: str) -> str:
    """A line with its numbers masked, so "total: 22 records from 2 export(s)" pairs with "total: 33 ... 3 ..."."""
    return re.sub(r"\d[\d,.]*", "#", text)


def _inconsistencies(text: str, snapshot: list[dict[str, Any]], now: list[dict[str, Any]]) -> list[str]:
    """D8: numbers of the text that the saved evidence held and the current evidence no longer does, each with the
    number at the same position of the paired line now: the current line of the same shape, else the line at the
    same position among the lines of the same source."""
    current = {n for ln in now for n in ln["numbers"]}
    out, seen = [], set()
    for n in numbers(text):
        if n in seen or n in current:
            continue
        seen.add(n)
        i = next((i for i, ln in enumerate(snapshot) if n in ln["numbers"]), None)
        if i is None:
            continue
        old = snapshot[i]
        pair = next((ln for ln in now if _shape(ln["text"]) == _shape(old["text"])), None)
        if pair is None:
            order = sum(1 for x in snapshot[:i] if x["source"] == old["source"])
            same = [ln for ln in now if ln["source"] == old["source"]]
            pair = same[order] if order < len(same) else None
        pos = old["numbers"].index(n)
        m = pair["numbers"][pos] if pair is not None and pos < len(pair["numbers"]) else "not in WIDER's evidence"
        out.append(f"Your text says {n}; WIDER's evidence for this item no longer contains {n} (now: {m})")
    return out


def text_state(project: Project, checklist: str, no: str, ctx: dict[str, Any], lines: list[dict[str, Any]] | None = None,
               cache: dict | None = None) -> dict[str, Any]:
    """The researcher's side of one item (D5, D7, D8): ``your_text`` (none, saved, outdated, unreadable), its label,
    ``gaps_left``, ``saved_for``, ``to_check`` and, when outdated, the saved and current lines and the
    possible inconsistencies."""
    cl = checklist_key(checklist)
    it = item(cl, no)
    rec = read_text(project, cl, it["no"])
    base = {"your_text": "none", "your_text_label": "none", "gaps_left": 0, "saved_for": None, "to_check": False,
            "text": "", "covers": [], "where": "", "not_applicable": None, "from_draft": False, "from_item": None,
            "versions": 0, "outdated": None}
    if rec is None:
        return base
    if rec.get("unreadable"):
        return {**base, "your_text": "unreadable", "your_text_label": UNREADABLE, "to_check": True,
                "unreadable": UNREADABLE}
    out = {**base, "text": rec.get("text") or "", "covers": list(rec.get("covers") or []),
           "where": rec.get("where") or "", "not_applicable": rec.get("not_applicable"),
           "from_draft": bool(rec.get("from_draft")), "from_item": rec.get("from_item"),
           "versions": len(rec.get("versions") or []),
           "saved_for": f"{rec['protocol']}, set {rec.get('result_set')}" if rec.get("protocol") else None}
    if not out["text"].strip():
        return out
    out["gaps_left"] = len(_GAP.findall(out["text"]))
    info = ctx.get("info") or {}
    same_set = not _set_dependent(cl, it["no"]) or not rec.get("protocol") or \
        (info.get("protocol"), info.get("result_set")) == (rec["protocol"], rec.get("result_set"))
    if same_set and lines is not None and not ctx.get("packaged"):
        now, now_ctx = lines, ctx
    else:
        now, now_ctx = _saved_lines(project, cl, it["no"], rec, ctx, cache)
    if now is None:
        out.update(your_text="outdated", your_text_label="outdated", to_check=True,
                   outdated={"reason": SET_GONE, "saved": rec.get("evidence_snapshot") or [], "current": [],
                             "inconsistencies": []})
        return out
    if evidence_hash(now) != rec.get("evidence_hash"):
        snap = rec.get("evidence_snapshot") or []
        out.update(your_text="outdated", your_text_label="outdated", to_check=True,
                   outdated={"reason": None, "saved": snap, "current": _snapshot(now),
                             "inconsistencies": _inconsistencies(out["text"], snap, now)})
        return out
    gaps = [m["key"] for m in missing(project, cl, it, now, now_ctx)]
    covered = bool(gaps) and set(gaps) <= set(out["covers"])
    out.update(your_text="saved", your_text_label="written, covers what was missing" if covered else "written",
               to_check=out["gaps_left"] > 0)
    return out


def _target(checklist: str, no: str, ctx: dict[str, Any]) -> tuple[str | None, int | None]:
    info = ctx.get("info")
    if _set_dependent(checklist, no) and info:
        return info["protocol"], info["result_set"]
    return None, None


def _write(project: Project, checklist: str, no: str, new: dict[str, Any], what: str, by: str | None) -> int | None:
    from ..core import journal

    _ensure_gitignore(project)
    path = _path(checklist, no)
    with journal.recording(project, "report_text", f"{item_label(checklist, no)}: {what}", by) as rec:
        rec.file(path)
        f = project.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec.change_id


def _related_text(project: Project, checklist: str, no: str, ref: str) -> str:
    """The text of the related item ``ref`` ("prisma-s:13") for ``--from-item`` (D14)."""
    cl_ref, _, no_ref = (ref or "").partition(":")
    ocl = checklist_key(cl_ref)
    if ocl == checklist or str(no_ref).strip().lower() not in item(checklist, no)["related"]:
        raise ValueError("not a related item")
    rec = read_text(project, ocl, no_ref) or {}
    text = "" if rec.get("unreadable") else (rec.get("text") or "")
    if not text.strip():
        raise ValueError(f"{item_label(ocl, item(ocl, no_ref)['no'])} has no text")
    return text


def save_text(project: Project, checklist: str, no: str, text: str | None = None, covers: list[str] | None = None,
              where: str | None = None, from_draft: bool = False, from_item: str | None = None, keep: bool = False,
              protocol: str | None = None, result_set: int | None = None, by: str | None = None) -> dict[str, Any]:
    """Save the researcher's text for one item (D6). ``None`` leaves a field as saved; nothing is recorded when
    nothing changed. Returns ``{"changed", "change_id"}``."""
    from ..core import journal

    cl = checklist_key(checklist)
    it = item(cl, no)
    no = it["no"]
    keys = [e["key"] for e in it["elements"]]
    if covers is not None:
        covers = [c.strip() for c in covers if c and c.strip()]
        bad = [c for c in covers if c not in keys]
        if bad:
            raise ValueError(f"unknown element {bad[0]!r} in {item_label(cl, no)}; one of {', '.join(keys)}")
    ctx = context(project, protocol, result_set)
    if from_draft:
        if not it["draft"]:
            raise ValueError("no draft for this item")
        text = draft(project, cl, no, ctx)["text"]
    elif from_item:
        text = _related_text(project, cl, no, from_item)
    old = read_text(project, cl, no) or {}
    unreadable = bool(old.get("unreadable"))
    if unreadable:
        old = {}
    if keep and not (old.get("text") or "").strip():
        raise ValueError(f"{item_label(cl, no)} has no text to keep")
    lines = evidence(project, cl, no, {**ctx, "packaged": False})
    prot, rs = _target(cl, no, ctx)
    new_text = (old.get("text") or "") if text is None else text.strip()
    # a location or covers change leaves the text as it was saved: its evidence fingerprint, snapshot and set stay,
    # so outdated text stays flagged until Keep or a new text (risk-checker R1)
    same = bool(new_text) and new_text == (old.get("text") or "") and not keep and "evidence_hash" in old
    ev_hash = old["evidence_hash"] if same else evidence_hash(lines)
    if same:
        prot, rs = old.get("protocol"), old.get("result_set")
    new = {"text": new_text, "covers": sorted(set(old.get("covers") or []) if covers is None else set(covers)),
           "where": (old.get("where") or "") if where is None else where.strip(),
           "not_applicable": old.get("not_applicable"),
           "from_draft": bool(old.get("from_draft")) if text is None and not from_draft else from_draft,
           "from_item": old.get("from_item") if text is None and not from_item else from_item,
           "protocol": prot, "result_set": rs, "evidence_hash": ev_hash}
    fields = ("text", "covers", "where", "not_applicable", "protocol", "result_set", "evidence_hash")
    if not unreadable and old and all(old.get(k) == new[k] for k in fields) and not keep:
        return {"changed": False, "change_id": None}
    if not unreadable and not old and not new_text and not new["where"] and not new["covers"]:
        return {"changed": False, "change_id": None}
    text_changed = new_text != (old.get("text") or "")
    versions = list(old.get("versions") or [])
    if text_changed and (old.get("text") or "").strip():
        versions.insert(0, {"text": old["text"], "saved_by": old.get("saved_by"), "saved_at": old.get("saved_at")})
    if keep:
        what = "text kept for the current evidence"
    elif not text_changed and new["where"] != (old.get("where") or "") and new["covers"] == sorted(old.get("covers") or []):
        what = "location saved"
    else:
        what = "text saved"
    new.update(saved_by=journal.current_author(by), saved_at=_now(),
               evidence_snapshot=old.get("evidence_snapshot") if same else _snapshot(lines),
               versions=versions[:MAX_VERSIONS])
    cid = _write(project, cl, no, new, what, by)
    return {"changed": True, "change_id": cid}


def clear(project: Project, checklist: str, no: str, by: str | None = None) -> dict[str, Any]:
    """Empty the text, covers and location; versions and the not-applicable mark stay (D6)."""
    cl = checklist_key(checklist)
    it = item(cl, no)
    context(project)
    old = read_text(project, cl, it["no"]) or {}
    if old.get("unreadable"):
        old = {"versions": []}
    elif not ((old.get("text") or "").strip() or old.get("covers") or old.get("where")):
        return {"changed": False, "change_id": None}
    versions = list(old.get("versions") or [])
    if (old.get("text") or "").strip():
        versions.insert(0, {"text": old["text"], "saved_by": old.get("saved_by"), "saved_at": old.get("saved_at")})
    new = {**old, "text": "", "covers": [], "where": "", "from_draft": False, "from_item": None,
           "versions": versions[:MAX_VERSIONS]}
    return {"changed": True, "change_id": _write(project, cl, it["no"], new, "text cleared", by)}


def mark_not_applicable(project: Project, checklist: str, no: str, reason: str | None = None, remove: bool = False,
                        by: str | None = None) -> dict[str, Any]:
    """Mark the item not applicable with a reason, or remove the mark (D3, D6)."""
    cl = checklist_key(checklist)
    it = item(cl, no)
    if not remove and not (reason or "").strip():
        raise ValueError("a reason is required")
    context(project)
    old = read_text(project, cl, it["no"]) or {}
    if old.get("unreadable"):
        old = {}
    mark = None if remove else {"reason": reason.strip()}
    if old.get("not_applicable") == mark:
        return {"changed": False, "change_id": None}
    new = {"text": "", "covers": [], "where": "", "versions": [], **old, "not_applicable": mark}
    what = "not-applicable mark removed" if remove else "marked not applicable"
    return {"changed": True, "change_id": _write(project, cl, it["no"], new, what, by)}


def _now() -> str:
    from ..core.provenance import utcnow

    return utcnow()


# ------------------------------------------------------------------------------------------------ drafts (D9)
def _gap(what: str, need: str = "not recorded by WIDER") -> str:
    return f"[{what}: {need}]"


def _input(what: str) -> str:
    return _gap(what, "researcher input needed")


def _end(text: str) -> str:
    """A sentence ending once: recorded text that already ends in . ? or ! gets no second stop."""
    return text if text.rstrip().endswith((".", "?", "!")) else text + "."


def _recall_draft(ctx: dict[str, Any]) -> list[str]:
    """The stored estimate's outcome lines and bounds (instruction lines already left out, D16), else the gap."""
    rec = ctx["recall"]
    return list(rec["lines"]) if rec and rec["lines"] else [_gap("recall estimate")]


def _d4(project, ctx):
    qs = [q.strip() for q in project.config.questions if q.strip()]
    if not qs:
        return [_input("review question")]
    return [_end(f"The review addressed the question{'s' if len(qs) > 1 else ''}: " + "; ".join(qs)),
            _input("population, intervention or exposure, comparison and outcomes, if they apply")]


def _d5(project, ctx):
    from .prisma_2020 import _eligibility

    cb = [ln for ln in _eligibility(project, ctx["info"]) if not ln.startswith("research question:")]
    s = [f"Records were screened against the {cb[0]}." if cb else _gap("eligibility criteria")]
    return s + [_input("how studies were grouped for the syntheses")]


def _d6(project, ctx):
    s = []
    for x in _db_sources(ctx):
        s.append(f"We searched {_short(x['platform'])}; the export was imported into WIDER on {x['imported_at'][:10]} "
                 + _gap("search date") + ".")
    multi = list(dict.fromkeys(_short(x["platform"]) for x in _db_sources(ctx) if not _single_db(x["platform"])))
    s += [_gap(f"databases searched on {m}") for m in multi]
    for x in _chain_sources(ctx):
        s.append(f"Citation searching added {x['n_records']:,} records (export imported {x['imported_at'][:10]}) "
                 + _gap("search date") + ".")
    return s + [_input("registers, websites, organisations and other sources consulted")]


def _d7(project, ctx):
    from ..search.coverage import validity_note
    from ..search.limits import limits, limits_line
    from .outputs import search_sentence

    note = validity_note(project) if project.read_benchmark() else None
    first = search_sentence(project, ctx["counts"], note)
    s = [first] if first else []
    arch = _archived(project)
    s += [f"The full search string for {_short(k)} is archived as search_strings/{e['file']}." for k, e in arch.items()]
    s += [f"Limits recorded for {_short(k)}: {limits_line(v)}." for k, v in limits(project).items() if v]
    no_str = [_short(k) for k in dict.fromkeys(x["platform"] for x in _db_sources(ctx)) if k not in arch]
    s += [_gap(f"search string for {k}") for k in no_str]
    return s + [_input("confirmation that each strategy was run as archived")]


def _d8(project, ctx):
    from ..sample.design import sampling_log
    from ..screen.agreement import pass_agreement, set_rules
    from ..screen.protocol import pass_models

    info = ctx["info"]
    if not info:
        return [_gap("screening of titles and abstracts"), _input("full-text screening")]
    key = info["key"]
    rules, pm = set_rules(project, key), pass_models(project, key)
    passes = "; ".join(f"pass {p}: {', '.join(ms)}" for p, ms in pm["models"].items())
    if rules["passes"] == 1:
        s = [f"Titles and abstracts were screened in one pass per record by a language model ({passes}) against "
             "the codebook."]
    else:
        third = "a blind third read on disagreement" if rules["blind_third_read"] else "no third read"
        s = [f"Titles and abstracts were read twice, independently, by language models ({passes}) against the "
             f"codebook, with {third}; decisions were combined by a {rules['majority']}-of-3 majority."]
        routes = {r["route"]: r["count"] for r in pass_agreement(project, key)["routes"]}
        s.append(f"Records without a majority were kept at maybe for the researcher's review "
                 f"({routes['split']} open, {routes['researcher']} decided by the researcher).")
        if not rules["recorded"]:
            s.append(_gap("the rule this set was screened with (the protocol's current rule is shown)"))
    s += list(pm.get("shared") or [])
    for name, v in sampling_log(project).items():
        if v["kind"] == "probability":
            s.append(f"A probability sample ({name}) of {v['n']} records was labelled by human reviewers.")
    return s + _recall_draft(ctx) + [_input("full-text screening: reviewers per report and how they worked")]


def _d16a(project, ctx):
    c = ctx["counts"]
    s = [f"The searches identified {c['identified']:,} records ({c['identified_databases']:,} from databases, "
         f"{c['identified_other']:,} from other methods); after {c['duplicates_removed']:,} duplicates were removed, "
         f"{c['unique']:,} records remained."]
    if c["provisional"] or ctx["dedup_stale"]:
        s.append(_input("final number of duplicates, once deduplication is final"))
    if c["protocol"]:
        s.append(f"Of {c['screened']:,} records screened on title and abstract, {c['excluded']:,} were excluded, "
                 f"{c['included_ta']:,} included and {c['maybe_ta']:,} kept for full-text reading.")
    else:
        s.append(_gap("screening of titles and abstracts"))
    return s + [_input("reports assessed in full text, reasons for exclusion and studies included")]


def _d23c(project, ctx):
    from ..screen.protocol import pass_models
    from ..search.coverage import validity_lines, validity_note

    s = [" ".join(validity_lines(validity_note(project)))] if project.read_benchmark() else []
    s += _recall_draft(ctx)
    if ctx["info"]:
        s += list(pass_models(project, ctx["info"]["key"]).get("shared") or [])
    return s + [_input("other limitations of the review process")]


def _d27(project, ctx):
    ev = _p27(project, ctx)
    if not ev:
        return [_input("which materials are available and where")]
    return [f"Search strings, screening decisions and settings are available in the WIDER {ev[0]['text']}.",
            _input("where the package is shared")]


def _ds1(project, ctx):
    s = [f"We searched {_short(x['platform'])}." for x in _db_sources(ctx)]
    multi = list(dict.fromkeys(_short(x["platform"]) for x in _db_sources(ctx) if not _single_db(x["platform"])))
    return s + [_gap(f"databases searched on {m}") for m in multi]


def _ds5(project, ctx):
    from . import prisma_s

    s = [_end(ln[0].upper() + ln[1:]) for ln in prisma_s._chaining(project)]
    s += [f"Citation searching added {x['n_records']:,} records (export {x['file_name']})." for x in _chain_sources(ctx)]
    return (s or [_gap("citation searching")]) + [_input("how the cited and citing references were searched")]


def _ds7(project, ctx):
    from . import prisma_s

    s = [_end(ln[0].upper() + ln[1:]) for ln in prisma_s._other_methods(project)]
    return s + [_input("other search methods used")]


def _ds8(project, ctx):
    return [x for x in _d7(project, ctx) if x.startswith(("The full search string", "[search string for"))] or [
        _gap("search strings")]


def _ds9(project, ctx):
    return [x for x in _d7(project, ctx) if x.startswith("Limits recorded")] or [_input("limits and filters used")]


def _ds13(project, ctx):
    return [f"{_short(x['platform'])}: export imported {x['imported_at'][:10]} " + _gap("search date") + "."
            for x in ctx["sources"]]


def _ds14(project, ctx):
    from . import prisma_s

    s = [_end(ln) for ln in prisma_s._peer_review(project)]
    return s or [_input("peer review of the search strategies")]


def _ds15(project, ctx):
    srcs = ctx["sources"]
    s = [f"{_short(x['platform'])} returned {x['n_records']:,} records." for x in srcs]
    return s + [f"In total, {sum(x['n_records'] for x in srcs):,} records were imported from {len(srcs)} "
                f"export{'' if len(srcs) == 1 else 's'}."]


def _ds16(project, ctx):
    from . import prisma_s

    ln = prisma_s._dedup(project)
    s = [_end(ln[0])] if ln else [_gap("deduplication")]
    if ctx["counts"]["provisional"] or ctx["dedup_stale"]:
        s.append(_input("final deduplication, once it is finished"))
    return s


DRAFTS = {"prisma_2020": {"4": _d4, "5": _d5, "6": _d6, "7": _d7, "8": _d8, "16a": _d16a, "23c": _d23c, "27": _d27},
          "prisma_s": {"1": _ds1, "5": _ds5, "7": _ds7, "8": _ds8, "9": _ds9, "13": _ds13, "14": _ds14,
                       "15": _ds15, "16": _ds16}}


def draft(project: Project, checklist: str, no: str, ctx: dict[str, Any]) -> dict[str, Any] | None:
    """The template draft of an item (D9): recorded facts in sentences and bracketed gaps, no model; None for an item
    without a template. ``gaps`` counts the bracketed gaps."""
    fn = DRAFTS.get(checklist_key(checklist), {}).get(str(no))
    if fn is None:
        return None
    text = " ".join(x for x in fn(project, ctx) if x)
    return {"text": text, "gaps": len(_GAP.findall(text))}


# ------------------------------------------------------------------------------------------------ flow counts (D21)
FLOW_DIR = "prisma_2020_flow"
ENTERED = {"registers": "Records identified from registers", "automation_removed": "Records removed by automation tools",
           "not_retrieved": "Reports not retrieved", "studies": "Studies included in review",
           "reports": "Reports of included studies"}
WIDER_BOXES = {"identified_databases": "Records identified from databases",
               "identified_other": "Records identified via other methods",
               "duplicates_removed": "Duplicate records removed",
               "excluded_file_records": "Records of files kept out of deduplication",
               "screened": "Records screened", "not_screened": "Records not yet screened",
               "excluded": "Records excluded", "maybe_ta": "Records marked maybe",
               "sought": "Reports sought for retrieval"}
NO_SCREENING = "no screening yet"
INPUT_NEEDED = "researcher input needed"
BAD_COUNT = "counts are whole numbers of 0 or more"
BAD_ROW = "each excluded row needs a reason and a count"
FLOW_OUTDATED = "WIDER's counts changed since you saved these counts: check them."


def _flow_path(protocol: str, result_set: int) -> str:
    return f"{REPORT_DIR}/{FLOW_DIR}/{protocol}_{result_set}.json"


def _flow_label(protocol: str, result_set: int) -> str:
    return f"PRISMA 2020 flow, {protocol} set {result_set}"


def _wider_counts(c: dict[str, Any]) -> dict[str, Any]:
    """The numeric box counts only, so renaming a set or relabelling a platform changes nothing (D21)."""
    return {**{k: c[k] for k in WIDER_BOXES}, "databases": {d["key"]: d["n"] for d in c["databases"]}}


def _counts_hash(wc: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(wc, sort_keys=True).encode("utf-8")).hexdigest()


def _count(v: Any) -> int:
    if isinstance(v, bool):
        raise ValueError(BAD_COUNT)
    if isinstance(v, str):
        v = v.strip()
        if not re.fullmatch(r"\d+", v):
            raise ValueError(BAD_COUNT)
        return int(v)
    if isinstance(v, int) and v >= 0:
        return v
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    raise ValueError(BAD_COUNT)


def _read_flow(project: Project, protocol: str, result_set: int) -> dict[str, Any] | None:
    f = project.root / _flow_path(protocol, result_set)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"unreadable": True}
    return d if isinstance(d, dict) else {"unreadable": True}


def _flow_target(project: Project, protocol: str | None, result_set: int | None) -> dict[str, Any]:
    ctx = context(project, protocol, result_set)
    if not ctx["info"]:
        raise ValueError(NO_SCREENING)
    return ctx


def _write_flow(project: Project, protocol: str, result_set: int, new: dict[str, Any] | None, what: str,
                by: str | None) -> int | None:
    from ..core import journal

    _ensure_gitignore(project)
    path = _flow_path(protocol, result_set)
    with journal.recording(project, "report_text", f"{_flow_label(protocol, result_set)}: {what}", by) as rec:
        rec.file(path)
        f = project.root / path
        if new is None:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return rec.change_id


def save_flow(project: Project, protocol: str | None = None, result_set: int | None = None,
              counts: dict[str, Any] | None = None, excluded: list[Any] | None = None,
              by: str | None = None) -> dict[str, Any]:
    """Merge the researcher's flow counts for one protocol and set (D21): each NAME given replaces that count, a
    ``None`` or empty value removes it; ``excluded`` given at all replaces the reason list. Nothing is written when
    a value is refused or nothing changed. Returns ``{"changed", "change_id"}``."""
    from ..core import journal

    given: dict[str, int | None] = {}
    for name, v in (counts or {}).items():
        name = str(name).strip()
        if name in WIDER_BOXES:
            raise ValueError(f"{WIDER_BOXES[name]} is recorded by WIDER and cannot be entered")
        if name not in ENTERED:
            raise ValueError(f"unknown count {name}; one of {', '.join(ENTERED)}")
        given[name] = None if v is None or (isinstance(v, str) and not v.strip()) else _count(v)
    rows = None
    if excluded is not None:
        rows = []
        for x in excluded:
            reason, n = (x.get("reason"), x.get("n")) if isinstance(x, dict) else x
            reason = (reason or "").strip()
            blank = n is None or (isinstance(n, str) and not n.strip())
            if not reason and blank:
                continue                                             # an empty row on the page
            if not reason or blank:
                raise ValueError(BAD_ROW)
            rows.append({"reason": reason, "n": _count(n)})
    ctx = _flow_target(project, protocol, result_set)
    prot, rs = ctx["info"]["protocol"], ctx["info"]["result_set"]
    old = _read_flow(project, prot, rs) or {}
    if old.get("unreadable"):
        old = {}
    merged = dict(old.get("counts") or {})
    for name, v in given.items():
        if v is None:
            merged.pop(name, None)
        else:
            merged[name] = v
    merged = {k: merged[k] for k in ENTERED if k in merged}
    new_rows = list(old.get("excluded") or []) if rows is None else rows
    wc = _wider_counts(ctx["counts"])
    if old and merged == old.get("counts") and new_rows == old.get("excluded"):
        return {"changed": False, "change_id": None}
    if not old and not merged and not new_rows:
        return {"changed": False, "change_id": None}
    versions = list(old.get("versions") or [])
    if old:
        versions.insert(0, {k: old.get(k) for k in ("counts", "excluded", "saved_by", "saved_at")})
    new = {"protocol": prot, "result_set": rs, "counts": merged, "excluded": new_rows, "wider_counts": wc,
           "wider_counts_hash": _counts_hash(wc), "saved_by": journal.current_author(by), "saved_at": _now(),
           "versions": versions[:MAX_VERSIONS]}
    return {"changed": True, "change_id": _write_flow(project, prot, rs, new, "counts saved", by)}


def clear_flow(project: Project, protocol: str | None = None, result_set: int | None = None,
               by: str | None = None) -> dict[str, Any]:
    """Remove the researcher's flow counts for one protocol and set; undoable (D21)."""
    ctx = _flow_target(project, protocol, result_set)
    prot, rs = ctx["info"]["protocol"], ctx["info"]["result_set"]
    if not (project.root / _flow_path(prot, rs)).exists():
        return {"changed": False, "change_id": None}
    return {"changed": True, "change_id": _write_flow(project, prot, rs, None, "counts cleared", by)}


def _flow_checks(c: dict[str, Any], entered: dict[str, int], rows: list[dict[str, Any]]) -> list[str]:
    out = []
    if "registers" in entered or "automation_removed" in entered:
        ident = c["identified_databases"] + c["identified_other"] + entered.get("registers", 0)
        removed = c["duplicates_removed"] + entered.get("automation_removed", 0) + c["excluded_file_records"]
        held = c["screened"] + c["not_screened"]
        if ident - removed != held:
            out.append(f"Records identified ({ident:,}) minus records removed ({removed:,}) is {ident - removed:,}, "
                       f"but WIDER holds {held:,} records after removal, screened or not yet screened (registers and "
                       f"automation-tool records are not in WIDER).")
    if "not_retrieved" in entered and entered["not_retrieved"] > c["sought"]:
        out.append(f"Reports not retrieved ({entered['not_retrieved']:,}) exceed reports sought for retrieval "
                   f"({c['sought']:,}).")
    if "reports" in entered:
        assessed = c["sought"] - entered.get("not_retrieved", 0)
        gone = sum(x["n"] for x in rows)
        if assessed - gone != entered["reports"]:
            out.append(f"Reports assessed ({assessed:,}) minus reports excluded ({gone:,}) is {assessed - gone:,}, "
                       f"but reports of included studies is {entered['reports']:,}.")
    if "studies" in entered and "reports" in entered and entered["studies"] > entered["reports"]:
        out.append("More studies than reports of included studies.")
    return out


def _flow_changed(saved: dict[str, Any], now: dict[str, Any]) -> list[dict[str, Any]]:
    out = [{"box": k, "label": WIDER_BOXES[k], "saved": saved.get(k), "now": now[k]}
           for k in WIDER_BOXES if saved.get(k) != now[k]]
    for key in sorted(set(saved.get("databases") or {}) | set(now["databases"])):
        a, b = (saved.get("databases") or {}).get(key), now["databases"].get(key)
        if a != b:
            out.append({"box": f"database:{key}", "label": f"Records identified from {_short(key)}", "saved": a,
                        "now": b})
    return out


def flow_state(project: Project, protocol: str | None = None, result_set: int | None = None,
               ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every flow box with its count and source (wider, researcher, derived or missing), the reason rows, the red
    checks and the outdated line, for one protocol and set (D21). Reads only; writes nothing."""
    ctx = ctx or context(project, protocol, result_set)
    c, info = ctx["counts"], ctx["info"]
    rec = (_read_flow(project, info["protocol"], info["result_set"]) or {}) if info else {}
    unreadable = bool(rec.get("unreadable"))
    entered = {} if unreadable else dict(rec.get("counts") or {})
    rows = [] if unreadable else list(rec.get("excluded") or [])

    def box(name, label, n, source):
        return {"name": name, "label": label, "n": n, "source": source,
                "text": f"{n:,}" if n is not None else INPUT_NEEDED}

    boxes = [box("identified_databases", WIDER_BOXES["identified_databases"], c["identified_databases"], "wider")]
    boxes += [box(f"database:{d['key']}", d["label"], d["n"], "wider") for d in c["databases"]]
    for name in ("identified_other", "registers", "duplicates_removed", "automation_removed", "excluded_file_records",
                 "screened", "not_screened", "excluded", "maybe_ta", "sought", "not_retrieved"):
        if name in ENTERED:
            boxes.append(box(name, ENTERED[name], entered.get(name), "researcher" if name in entered else "missing"))
        else:
            boxes.append(box(name, WIDER_BOXES[name], c[name] if info or name in (
                "identified_databases", "identified_other", "duplicates_removed", "excluded_file_records") else None,
                "wider"))
    assessed = c["sought"] - entered["not_retrieved"] if info and "not_retrieved" in entered else None
    boxes.append(box("assessed", "Reports assessed for eligibility", assessed, "derived" if assessed is not None
                     else "missing"))
    boxes.append(box("reports_excluded", "Reports excluded", sum(x["n"] for x in rows) if rows else None,
                     "researcher" if rows else "missing"))
    for name in ("studies", "reports"):
        boxes.append(box(name, ENTERED[name], entered.get(name), "researcher" if name in entered else "missing"))
    checks = ([c["provisional_line"]] if c.get("provisional_line") else []) + (_flow_checks(c, entered, rows) if info
                                                                              else [])
    outdated = None
    if info and rec and not unreadable and rec.get("wider_counts_hash") != _counts_hash(_wider_counts(c)):
        outdated = {"line": FLOW_OUTDATED, "changed": _flow_changed(rec.get("wider_counts") or {}, _wider_counts(c))}
        checks.append(FLOW_OUTDATED)
    others = []
    if info:
        folder = project.root / REPORT_DIR / FLOW_DIR
        for f in sorted(folder.glob(f"{info['protocol']}_*.json")) if folder.exists() else []:
            rs = f.stem[len(info["protocol"]) + 1:]
            if rs.isdigit() and int(rs) != info["result_set"]:
                others.append(f"{info['protocol']}, set {int(rs)}")
    return {"protocol": info["protocol"] if info else None, "result_set": info["result_set"] if info else None,
            "message": None if info else NO_SCREENING, "boxes": boxes, "entered": entered, "excluded": rows,
            "checks": checks, "outdated": outdated, "unreadable": unreadable, "exports_stale": _stale_exports(project),
            "other_sets": f"Counts were entered for {'; '.join(others)}." if others else None,
            "saved_at": rec.get("saved_at") if rec and not unreadable else None}


def _stale_exports(project: Project) -> str | None:
    """One line when a file of ``exports/prisma_reporting/`` was written before the review's data last changed: the
    files are marked, never rewritten on their own."""
    from .dashboard import REPORT_EXPORT, _when, last_data_change

    folder = project.root / "exports" / EXPORT_DIR
    changed = last_data_change(project)
    old = [f for f in sorted(folder.iterdir()) if f.is_file() and changed and _when(f) < changed] \
        if folder.is_dir() else []
    return (f"The PRISMA reporting files in exports/{EXPORT_DIR}/ were written before the review's data last changed "
            f"(a decision, an undo or a new run) and may be out of date; write them again with Export checklists "
            f"({REPORT_EXPORT}).") if old else None


def excluded_split(project: Project, counts: dict[str, Any]) -> dict[str, int] | None:
    """"Records excluded" by the models and by a researcher (the template footnote on automation tools, D19), from the
    decision pathways the Results dashboard counts; None before screening or when the parts do not add up."""
    from .selection import select_records

    if not counts.get("result_set_key") or not counts.get("excluded"):
        return None
    sel = select_records(project, counts["result_set_key"], "exclude")
    bp = sel["by_pathway"]
    split = {"models": bp["agreed"] + bp["third_read"] + bp["split"], "researcher": bp["researcher"]}
    if sel["other_route"]:
        split["other"] = sel["other_route"]
    return split if sum(split.values()) == counts["excluded"] else None


def write_flow(project: Project, out: Path, ctx: dict[str, Any]) -> list[str]:
    """``prisma_2020_flow.svg`` and ``prisma_2020_flow.json`` (D19), and ``prisma_2020_flow.docx`` (D20) when
    python-docx can be imported, into ``out``; the file names written."""
    from . import flow_layout, reporting_docx

    st = flow_state(project, ctx=ctx)
    layout = flow_layout.flow_boxes(ctx["counts"], st["entered"], st["excluded"],
                                    excluded_split(project, ctx["counts"]))
    (out / f"{FLOW_FILE}.svg").write_text(flow_layout.flow_svg(layout), encoding="utf-8")
    js = flow_layout.flow_json(layout, st["checks"], st["protocol"], st["result_set"])
    (out / f"{FLOW_FILE}.json").write_text(json.dumps(js, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    names = [f"{FLOW_FILE}.svg", f"{FLOW_FILE}.json"]
    if reporting_docx.available():
        reporting_docx.flow_docx(layout, out / f"{FLOW_FILE}.docx")
        names.append(f"{FLOW_FILE}.docx")
    return names


# ------------------------------------------------------------------------------------------------ exports (D10, D11)
EXPORT_DIR = "prisma_reporting"
FLOW_FILE = "prisma_2020_flow"
WIDER_DRAFT = "WIDER draft, not reviewed by the researcher"
NOT_REPORTED = "Not reported yet"
CSV_COLUMNS = ("item", "section", "topic", "status", "your text state", "saved for", "where reported", "WIDER evidence",
               "researcher text", "not applicable reason")
FILE_STEM = {"prisma_2020": "prisma_2020_checklist", "prisma_s": "prisma_s_checklist"}
COUNT_WORDS = ((AUTO, "automatically documented"), (PART, "partially documented"), (INPUT, "need your input"),
               (NA, "not applicable"))


def _not_recorded(checklist: str) -> str:
    from . import prisma_2020, prisma_s

    return prisma_2020.NOT_RECORDED if checklist == "prisma_2020" else prisma_s.NOT_RECORDED


def _item_names(checklist: str) -> dict[str, str]:
    from . import prisma_2020, prisma_s

    if checklist == "prisma_2020":
        return {no: name for no, _section, name in prisma_2020.ITEMS}
    return {str(no): name for no, name in prisma_s.ITEMS}


def _researcher(y: dict[str, Any]) -> dict[str, Any]:
    """The researcher's side of one item as the files give it: the text as typed (D25), its state, the set it was
    saved for, where it is reported and the not-applicable reason; never who saved it."""
    na = y["not_applicable"]
    return {"text": y["text"], "your_text": y["your_text_label"],
            "saved_for": f"written for {y['saved_for']}" if y["saved_for"] else None, "where": y["where"],
            "not_applicable": (na or {}).get("reason") if na else None}


def checklist_doc(project: Project, checklist: str, ctx: dict[str, Any], att: Any = None) -> dict[str, Any]:
    """One checklist as its files give it (D10, D11): per item WIDER's lines (through ``att``, an ``Attributor``),
    its status, what is missing and, apart under ``researcher``, the researcher's text as typed. ``filled`` is
    "status is Automatically or Partially documented"; an item with no line says it is not recorded, and one that
    needs the researcher's input starts with that line. PRISMA-S keeps ``no`` as a number and has no ``section``."""
    cl = checklist_key(checklist)
    nr, names, cache = _not_recorded(cl), _item_names(cl), {}
    wtext = att.text if att is not None else (lambda s: s)
    items = []
    for it in catalogue()[cl]:
        lines = evidence(project, cl, it["no"], ctx)
        ws = status(it, lines)
        y = text_state(project, cl, it["no"], ctx, lines, cache)
        st = NA if y["not_applicable"] else ws
        wl = [wtext(ln["text"]) for ln in lines]
        wl = wl if wl and ws != INPUT else [nr] + wl
        row = {"no": int(it["no"]) if cl == "prisma_s" else it["no"], "section": it["section"],
               "name": names.get(it["no"], it["topic"]), "filled": st in (AUTO, PART), "lines": wl, "status": st,
               "missing": [wtext(f"{m['text']}: {m['say']}") for m in missing(project, cl, it, lines, ctx)],
               "researcher": _researcher(y)}
        if cl == "prisma_s":
            del row["section"]
        items.append(row)
    c = ctx["counts"]
    head = {"protocol": c["protocol"], "result_set": c["result_set"], "set_name": c["set_name"]} \
        if cl == "prisma_2020" else {}
    return {"citation": _citation(cl), **head, "items": items}


def _citation(checklist: str) -> str:
    from . import prisma_2020, prisma_s

    return prisma_2020.CITATION if checklist == "prisma_2020" else prisma_s.CITATION


def status_counts(doc: dict[str, Any]) -> str:
    """"3 automatically documented, 5 partially documented, 34 need your input, 0 not applicable" (D11)."""
    return ", ".join(f"{sum(it['status'] == s for it in doc['items'])} {w}" for s, w in COUNT_WORDS)


def checklist_markdown(checklist: str, doc: dict[str, Any], title: str) -> str:
    """The D10 layout: per item its status, "WIDER recorded" (and what is still missing), then "Researcher text"
    with the text as typed; the two are never merged."""
    cl = checklist_key(checklist)
    sub = "#" * (4 if cl == "prisma_2020" else 3)
    out = [f"# {TITLES[cl]} checklist: {title}" + (f" (protocol {doc['set_name']})" if doc.get("set_name") else ""), "",
           f"Checklist: {doc['citation']}", "",
           f"WIDER's lines come from the project store; an item WIDER does not record says \"{_not_recorded(cl)}\". "
           "The researcher's text is kept apart under \"Researcher text\", exactly as typed.", ""]
    section = None
    for it in doc["items"]:
        if cl == "prisma_2020" and it["section"] != section:
            section = it["section"]
            out += [f"## {section}", ""]
        r = it["researcher"]
        out += [f"{'###' if cl == 'prisma_2020' else '##'} {it['no']}. {it['name']}", "", f"Status: {it['status']}", "",
                f"{sub} WIDER recorded", ""] + [f"- {ln}" for ln in it["lines"]] + [""]
        if it["missing"]:
            out += ["Still missing:", ""] + [f"- {m}" for m in it["missing"]] + [""]
        out += [f"{sub} Researcher text", ""]
        if r["not_applicable"]:
            out += [f"Not applicable: {r['not_applicable']}", ""]
        out += [r["text"].strip() if r["text"].strip() else "None yet.", ""]
        if r["text"].strip():
            out += [f"Your text: {r['your_text']}" + (f"; {r['saved_for']}" if r["saved_for"] else ""), ""]
        if r["where"]:
            out += [f"Where reported: {r['where']}", ""]
    return "\n".join(out)


def checklist_csv(doc: dict[str, Any]) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for it in doc["items"]:
        r = it["researcher"]
        w.writerow([it["no"], it.get("section", ""), it["name"], it["status"], r["your_text"], r["saved_for"] or "",
                    r["where"], "\n".join(it["lines"]), r["text"], r["not_applicable"] or ""])
    return buf.getvalue()


def _methods_block(project: Project, checklist: str, it: dict[str, Any], ctx: dict[str, Any], att: Any) -> list[str]:
    r = it["researcher"]
    if r["not_applicable"]:
        return [f"Not applicable: {r['not_applicable']}"]
    if r["text"].strip():
        return [r["text"].strip()]
    if r["your_text"] == UNREADABLE:
        return [UNREADABLE]
    d = draft(project, checklist, str(it["no"]), ctx)
    if d and d["text"].strip():
        return [f"*{WIDER_DRAFT}*", "", att.text(d["text"])]
    return [NOT_REPORTED]


def methods_text(project: Project, docs: dict[str, dict[str, Any]], ctx: dict[str, Any], att: Any, title: str) -> str:
    """``methods_text.md`` (D10): the Methods items of PRISMA 2020 and every PRISMA-S item in order, each the
    researcher's text, else WIDER's draft under its label, else "Not reported yet"."""
    out = [f"# Methods text: {title}", "",
           f"Each item gives the researcher's text as typed; a WIDER draft is marked \"{WIDER_DRAFT}\".", ""]
    for cl in CHECKLISTS:
        for it in docs[cl]["items"]:
            if cl == "prisma_2020" and it["section"] != "Methods":
                continue
            out += [f"## {item_label(cl, str(it['no']))}: {it['name']}", ""] + _methods_block(project, cl, it, ctx, att) + [""]
    return "\n".join(out)


WRITTEN = ".written.json"
DOCX_FILES = {"prisma_2020": "prisma_2020_checklist.docx", "prisma_s": "prisma_s_checklist.docx"}
METHODS_DOCX = "methods_documentation.docx"
NO_WORD = "Word files not written: python-docx is not installed (pip install -e .)."
LOCKED = "close {name} in Word and export again"
NO_STRING = "[search string: not archived by WIDER]"
SEARCH_DATE = "search date: not recorded by WIDER"


def checklist_rows(checklist: str, doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The rows of the official Word checklist (D17): section rows, then per item its topic, number, published
    wording and the researcher's "where reported" note or not-applicable reason; no status."""
    cl = checklist_key(checklist)
    by_no = {str(it["no"]): it for it in doc["items"]}
    rows, section = [], None
    for it in catalogue()[cl]:
        if it["section"] != section:
            section = it["section"]
            rows.append({"section": section})
        r = by_no[it["no"]]["researcher"]
        rows.append({"topic": it["topic"], "no": it["no"], "official": it["official"], "where": r["where"],
                     "not_applicable": r["not_applicable"]})
    return rows


def _strategies(project: Project, ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """One block per database platform (D12, D18): the newest archived string verbatim (each distinct string once),
    the record count of each import and the search date WIDER does not record; the same facts as PRISMA-S 8 and 15."""
    from ..search.archive import archive_dir

    arch, dbs = _archived(project), _db_sources(ctx)
    seen: dict[str, str] = {}
    out = []
    for key in dict.fromkeys([s["platform"] for s in dbs] + list(arch)):
        e, string, no_string = arch.get(key), None, NO_STRING
        if e:
            if e["sha256"] in seen:
                no_string = f"the same search string as {seen[e['sha256']]}"
            else:
                seen[e["sha256"]] = _short(key)
                text = (archive_dir(project) / e["file"]).read_text(encoding="utf-8")
                string = "\n".join(ln for i, ln in enumerate(text.rstrip("\n").split("\n"))
                                   if not (i < 3 and ln.startswith("# ")))
        lines = [f"export {s['file_name']}: {s['n_records']:,} records" for s in dbs if s["platform"] == key]
        out.append({"platform": _short(key), "string": string, "no_string": no_string,
                    "lines": lines + [f"[{SEARCH_DATE}]"]})
    return out


def methods_data(project: Project, docs: dict[str, dict[str, Any]], ctx: dict[str, Any], att: Any) -> dict[str, Any]:
    """What ``methods_documentation.docx`` holds (D18), in the order and with the rule of ``methods_text.md``."""
    from .. import __version__
    from ..core.provenance import utcnow

    methods = []
    for cl in CHECKLISTS:
        for it in docs[cl]["items"]:
            if cl == "prisma_2020" and it["section"] != "Methods":
                continue
            block = _methods_block(project, cl, it, ctx, att)
            if block[0] == f"*{WIDER_DRAFT}*":
                blocks = [{"label": WIDER_DRAFT, "text": "\n".join(block[2:])}]
            elif it["researcher"]["text"].strip() and not it["researcher"]["not_applicable"]:
                blocks = [{"label": "Researcher text", "text": block[0]}]
            else:
                blocks = [{"label": None, "text": block[0]}]
            methods.append({"heading": f"{item_label(cl, str(it['no']))}: {it['name']}", "blocks": blocks})
    status = [(f"{TITLES[cl]} checklist",
               [{"item": str(it["no"]), "status": it["status"], "your_text": it["researcher"]["your_text"],
                 "missing": "; ".join(it["missing"])} for it in docs[cl]["items"]]) for cl in CHECKLISTS]
    return {"methods": methods, "strategies": _strategies(project, ctx), "status": status,
            "footer": f"Prepared with WIDER {__version__} from project records on {utcnow()[:10]}; "
                      "drafts are not reviewed unless marked."}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _edited_name(path: Path, stamp: str) -> Path:
    base = f"{path.stem} (edited {stamp})"
    cand, k = path.with_name(base + path.suffix), 2
    while cand.exists():
        cand, k = path.with_name(f"{base} ({k}){path.suffix}"), k + 1
    return cand


def keep_edited(out: Path, names: list[str]) -> list[str]:
    """Before an export writes ``names`` into ``out`` (D17): every existing target is opened ``r+b`` first, so a file
    open in Word refuses the export before anything is written; a target whose bytes differ from what
    ``.written.json`` recorded, or that has no recorded hash, is renamed "<name> (edited <time>)" and kept."""
    from datetime import datetime

    targets = [out / n for n in names if (out / n).exists()]
    for t in targets:
        try:
            with open(t, "r+b"):
                pass
        except PermissionError:
            raise ValueError(LOCKED.format(name=t.name)) from None
    try:
        hashes = json.loads((out / WRITTEN).read_text(encoding="utf-8"))
        hashes = hashes if isinstance(hashes, dict) else {}
    except (OSError, ValueError):
        hashes = {}
    stamp, kept = datetime.now().strftime("%Y-%m-%d %H%M%S"), []
    for t in targets:
        if hashes.get(t.name) == _sha(t):
            continue
        dest = _edited_name(t, stamp)
        try:
            t.rename(dest)
        except PermissionError:
            raise ValueError(LOCKED.format(name=t.name)) from None
        kept.append(dest.name)
    return kept


def export(project: Project, protocol: str | None = None, result_set: int | None = None, folder: Path | None = None,
           packaged: bool = False) -> dict[str, Any]:
    """Write ``exports/prisma_reporting/`` (D10): both checklists as Markdown, CSV and JSON and ``methods_text.md``,
    and redraw ``exports/prisma_2020.svg`` with ``write_prisma``. With ``packaged`` only the CSV files and
    ``methods_text.md`` go to ``folder`` and the diagram is not redrawn. No researcher name; refused before any
    import."""
    from ..core.provenance import utcnow
    from ..core.researchers import Attributor
    from . import reporting_docx
    from .outputs import write_prisma

    ctx = context(project, protocol, result_set, packaged=packaged)
    att = Attributor(project)
    title = project.config.title or project.config.name
    docs = {cl: checklist_doc(project, cl, ctx, att) for cl in CHECKLISTS}
    out = folder or project.path("exports") / EXPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    word = reporting_docx.available()
    tracked = [f"{FLOW_FILE}.svg"] + ([*DOCX_FILES.values(), METHODS_DOCX, f"{FLOW_FILE}.docx"] if word else [])
    kept = [] if packaged else keep_edited(out, tracked)
    texts: dict[str, str] = {}
    for cl, doc in docs.items():
        stem = FILE_STEM[cl]
        texts[f"{stem}.csv"] = checklist_csv(doc)
        if not packaged:
            texts[f"{stem}.md"] = checklist_markdown(cl, doc, title)
            texts[f"{stem}.json"] = json.dumps({**doc, "written_at": utcnow()}, indent=1, ensure_ascii=False) + "\n"
    texts["methods_text.md"] = methods_text(project, docs, ctx, att, title)
    for name, text in texts.items():
        (out / name).write_text(text, encoding="utf-8")
    names = list(texts)
    if word:
        for cl, doc in docs.items():
            reporting_docx.checklist_docx(checklist_rows(cl, doc), cl, doc["citation"], out / DOCX_FILES[cl])
            names.append(DOCX_FILES[cl])
        reporting_docx.methods_docx(methods_data(project, docs, ctx, att), _GAP, out / METHODS_DOCX)
        names.append(METHODS_DOCX)
    names += write_flow(project, out, ctx)
    if not packaged:
        (out / WRITTEN).write_text(json.dumps({n: _sha(out / n) for n in tracked if n in names}, indent=1) + "\n",
                                   encoding="utf-8")
    rel = (lambda p: str(p)) if folder else (lambda p: str(p.relative_to(project.root)))
    files = [rel(out / n) for n in names]
    if not packaged:
        write_prisma(project, protocol, result_set)
        files.append("exports/prisma_2020.svg")
    c = ctx["counts"]
    return {"protocol": c["protocol"], "result_set": c["result_set"], "set_name": c["set_name"], "files": files,
            "counts": {cl: status_counts(d) for cl, d in docs.items()},
            "message": f"PRISMA reporting exports written: {len(files)} files"
                       + ("" if folder else f" in exports/{EXPORT_DIR}/ and the diagram exports/prisma_2020.svg")
                       + f"; the flow diagram is {FLOW_FILE}.svg with its counts in {FLOW_FILE}.json"
                       + (f" and an editable Word copy in {FLOW_FILE}.docx" if f"{FLOW_FILE}.docx" in names else "")
                       + (f"; your edited copies kept as {', '.join(kept)}" if kept else "")
                       + ("" if word else f"; {NO_WORD}"),
            "kept": kept}
