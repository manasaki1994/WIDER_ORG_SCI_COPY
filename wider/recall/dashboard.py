"""The Results dashboard (results-dashboard-and-ris, design D1-D9): one result set's agreement, the configuration
comparisons apart, the stored recall lines and relevant-found series, PRISMA counts, reference sets and documents.

Read-only: it computes no estimate, records no look, stage result or History entry, and never makes a set active.
``wider results`` prints :func:`dashboard_lines`; ``GET /api/results`` returns :func:`dashboard`.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from ..core.project import Project
from ..screen.devgate import FILE_WAITS, file_waits

HEADINGS = {"agreement": "Agreement between the passes", "comparisons": "Configuration comparisons (development sample)",
            "recall": "Recall", "curve": "Relevant found along the ranking", "counts": "PRISMA counts",
            "reference_sets": "Reference files", "documents": "Documents"}
SECTIONS = tuple(HEADINGS)
PASS_LABEL = {"A": "pass A", "B": "pass B", "C": "third read", "S1": "read S1", "S2": "read S2", "S3": "third read S3"}
CURVE_LABEL = "Estimate from the validation sample; no stopping rule."
CURVE_LEGEND = ("Estimated relevant", "Estimated relevant kept by screening", "Estimated missed by screening")
CURVE_TITLE = "Estimated relevant records along the ranking"
CURVE_AXES = {"x": "Position in the ranking (1 = highest score)", "y": "Cumulative estimated relevant records"}
CURVE_POINTS = ("Points are at band ends; the lines only join them, since the sample says nothing about where in a "
                "band the relevant records sit.")
CURVE_RANGES = ("Ranges: exact bounds valid at every look ({level}%), summed over the bands up to each end; "
                "conservative.")
CURVE_NO_RANGES = "No ranges: the chart's labels are not those of the exact bounds, so it shows point estimates only."
CURVE_RANGE_LEGEND = "Exact range (valid at every look)"
CURVE_OLD = ("Ranges and band details are stored with newer estimates; estimate recall again to show them (this "
             "records a look).")
CURVE_OUTSIDE = ("The estimate lies outside its range here. Both are right: the estimate and the range are worked out "
                 "in two different ways, which can disagree when few relevant records were found.")
CURVE_IN_OTHER = ("{n} of these records were drawn into another sample after this one; the estimate and the range both "
                  "count them as records not labelled here.")
# a series stored before recall-final-check-fixes (D9): its range left those records out, and it says so
CURVE_IN_OTHER_OLD = ("{n} more records of these ranks were drawn into another sample after this one: the estimate "
                      "counts them, the range leaves them out, so the estimate can lie outside its range.")
CURVE_OUTSIDE_OTHER_OLD = ("Where an estimate lies outside its range, records of those ranks were drawn into another sample "
                       "after this one: the estimate counts them, the range leaves them out.")
CURVE_METHOD = ("How the estimate and the range are made",
                "The estimate weights each band's relevant labelled records by the band's records per labelled record "
                "and adds the bands up. The range bounds the kept and the not-kept records of each band apart, at a "
                "level that holds for all of them together at every look, and adds those bounds up, so it is wider "
                "and need not be centred on the estimate.")
CURVE_AGAIN = "Estimate recall again"
CURVE_LABEL_LINK = "Label them on Samples and labelling"
FEW_RELEVANT = 3   # fewer relevant observed in a band marks it (D10; named in guide.md)
FEW = "few relevant observed"
CURVE_STALE = ("The labels or screening decisions changed since the last recall estimate; estimate recall again to "
               "redraw the chart.")
CURVE_MISSING = "No chart is stored with this estimate; estimate recall again to draw it."
CURVE_NO_BANDS = "This sample has no score bands, so no chart is drawn."
CURVE_NO_RELEVANT = "The labelled sample holds no relevant record, so no chart is drawn."
SET_WORDS = {"include": "include", "maybe": "maybe", "exclude": "exclude", "include+maybe": "include+maybe",
             "agreed": "agreed by both passes", "third_read": "settled by the third read",
             "split": "no majority (left at maybe)", "researcher": "decided by the researcher"}
SET_ORDER = ("include", "maybe", "exclude", "include+maybe", "agreed", "third_read", "split", "researcher")
NOT_WRITTEN = "not written yet"
# (label, path under the project, command that writes it); methods.md is the researcher's text in the project folder
REPORT_EXPORT = "wider report prisma export"
DOCUMENTS = (("Report", "exports/report.md", "wider report"),
             ("PRISMA 2020 diagram", "exports/prisma_2020.svg", "wider export prisma"),
             ("PRISMA 2020 counts", "exports/prisma_2020.json", "wider export prisma"),
             ("PRISMA 2020 checklist", "exports/prisma_2020_checklist.md", "wider export prisma-checklist"),
             ("PRISMA-S", "exports/prisma_s.md", "wider export prisma-s"),
             ("Methods text", "methods.md", "wider export methods --save"),
             # the PRISMA reporting files (prisma-reporting-assistant D10, D17-D20)
             ("PRISMA reporting checklist", "exports/prisma_reporting/prisma_2020_checklist.md", REPORT_EXPORT),
             ("PRISMA 2020 checklist (Word)", "exports/prisma_reporting/prisma_2020_checklist.docx", REPORT_EXPORT),
             ("PRISMA-S checklist (Word)", "exports/prisma_reporting/prisma_s_checklist.docx", REPORT_EXPORT),
             ("Methods documentation (Word)", "exports/prisma_reporting/methods_documentation.docx", REPORT_EXPORT),
             ("PRISMA 2020 flow diagram (Word)", "exports/prisma_reporting/prisma_2020_flow.docx", REPORT_EXPORT))
NO_WORD = "not written: python-docx is not installed"
# a file written before the review's data last changed (a decision, an undo, a new run) is marked, never rewritten
STALE = "may be out of date"
STALE_LINE = ("Files marked \"may be out of date\" were written before the review's data last changed (a decision, an "
              "undo or a new run); WIDER does not rewrite them on its own.")
# history kinds that change no count, text or decision an output file holds, and stage runs that only write files
NOT_DATA_KINDS = ("note", "tag", "answer", "dismiss", "validation_peek", "review_package")
FILE_RUNS = ("selection", "audit", "export_package", "prisma", "references", "corpus_export", "package", "report",
             "bundle")


def last_data_change(project: Project) -> str | None:
    """When the review's data last changed: the newest change in the history (an undo included) or stage run, leaving
    out notes and the runs that only write output files; an iso UTC time like ``_when``'s, or None."""
    marks = ",".join("?" * len(NOT_DATA_KINDS))
    h = project.store.query(f"SELECT MAX(at) AS at FROM history WHERE kind NOT IN ({marks})", NOT_DATA_KINDS)
    runs = ",".join("?" * len(FILE_RUNS))
    r = project.store.query(f"SELECT MAX(ran_at) AS at FROM stage_results WHERE NOT (stage='recall' AND action IN "
                            f"({runs})) AND NOT (stage='screen' AND action='compare')", FILE_RUNS)
    times = [x[0]["at"] for x in (h, r) if x and x[0]["at"]]
    return max(times) if times else None


def stale_text(command: str | None) -> str:
    return f"{STALE}; write it again with {command}" if command else STALE


def _when(path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


def _set_args(info: dict[str, Any], active: int | None) -> str:
    return f" --set {info['result_set']}" if info["result_set"] != active else ""


def _choices(project: Project, protocol: str) -> dict[str, Any]:
    """What the page's three selects offer: the protocols, the chosen protocol's sets, the samples."""
    from ..screen.decisions import protocols

    st = project.store
    sets = [{"result_set": r["result_set"], "label": r["label"], "active": bool(r["active"])} for r in st.query(
        "SELECT result_set, label, active FROM screen_result_sets WHERE protocol=? ORDER BY result_set", (protocol,))]
    return {"protocols": [p["protocol"] for p in protocols(project)], "sets": sets,
            "samples": [{"name": r["name"], "kind": r["kind"]} for r in
                        st.query("SELECT name, kind FROM samples ORDER BY sample_id")]}


def _heading(project: Project, info: dict[str, Any]) -> dict[str, Any]:
    from ..screen.protocol import pass_models

    pm = pass_models(project, info["key"])
    parts = [f"{PASS_LABEL.get(p, p)}: {', '.join(ms) if ms else 'not recorded'}" for p, ms in pm["models"].items()]
    models = "; ".join(parts)
    return {"title": f"{info['protocol']} (set {info['result_set']})", "set_name": info.get("label"),
            "models": (models[0].upper() + models[1:]) if models else "No model is recorded for this set's passes.",
            "role_note": info.get("role_note"), "shared": pm["shared"]}


def _recall_headline(last: dict[str, Any]) -> str | None:
    """The stored estimate in one line above its lines: the point estimate and the exact bounds valid at every look."""
    rec, b = last.get("recall"), last.get("bounds") or {}
    if rec is None:
        return None
    head = f"Recall ({last['variant']})" if last.get("variant") else "Recall"
    if b.get("low") is None or b.get("high") is None:
        return f"{head}: {rec:.1%}"
    level = f" ({b['level']:.0%})" if b.get("level") is not None else ""
    return f"{head}: {rec:.1%}; exact bounds valid at every look{level}: {b['low']:.1%} to {b['high']:.1%}"


def _recall(project: Project, info: dict[str, Any], sample: str, default_sample: str,
            active: int | None) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The Recall section (D4) and the stored lines' record (or None) for the chart."""
    from .validation import status

    out: dict[str, Any] = {"sample": sample, "state": None, "sentence": None, "as_of": None, "headline": None,
                           "lines": [], "outcome": None, "warning_lines": [], "stale": False}
    row = project.store.one("SELECT kind FROM samples WHERE name=?", (sample,))
    if row is None:
        return {**out, "state": "no_sample", "sentence": f"No sample {sample}."}, None
    if row["kind"] != "probability":
        return {**out, "state": "purposive",
                "sentence": f"Recall is estimated on probability samples only; {sample} is purposive."}, None
    cmd = (f"wider recall {info['protocol']}{_set_args(info, active)}"
           + (f" --sample {sample}" if sample != default_sample else ""))
    try:
        last = json.loads(project.store.get_meta(f"last_validation_lines:{sample}") or "{}")
    except ValueError:
        last = {}
    if not last:
        from .curve import _band_ends
        if _band_ends(project, sample) is None:   # an imported sample: estimates on it store no lines (no exact bounds)
            return {**out, "state": "no_bands", "sentence": (
                f"{sample} has no score bands, so the dashboard keeps no recall lines for it; its recall is shown on "
                f"the Recall and report page ({cmd}).")}, None
        return {**out, "state": "none", "sentence": (
            f"No recall estimate on {sample} yet. Estimate recall on the Recall and report page ({cmd}).")}, None
    if last.get("result_set_key") != info["key"]:
        from ..screen.result_sets import row_by_key

        other = row_by_key(project, last.get("result_set_key") or "") or {}
        if other.get("protocol") == info["protocol"]:
            was, now = f"{other['protocol']} (set {other.get('result_set', 1)})", f"set {info['result_set']}"
        else:
            was = (f"{other['protocol']} (set {other.get('result_set', 1)})" if other
                   else str(last.get("result_set_key")))
            now = f"{info['protocol']} (set {info['result_set']})"
        return {**out, "state": "other_set", "sentence": (
            f"The last recall estimate on {sample} is for {was}, not {now}. Estimate recall for {now} on the Recall "
            f"and report page ({cmd}); this records a look on {now}.")}, None
    st = status(project, sample)
    oc = st["outcome"] or {}   # none when the stored verdict was judged against another target (audit C2)
    out.update(state="current", as_of=last.get("at"), headline=_recall_headline(last), lines=st["lines"],
               outcome=oc.get("outcome"),
               warning_lines=list(oc.get("lines") or []) if oc.get("outcome") == "not_met" else [])   # an
               # inconclusive result is not a warning: it is not evidence of a miss (recall-certification-workload D2)
    return out, last


def _num(x) -> str:
    """A count as the chart and table print it: whole numbers without ".0", others to one decimal."""
    x = float(x)
    return f"{round(x):,}" if abs(x - round(x)) < 0.05 else f"{x:,.1f}"


def _span(lo, hi) -> str:
    return f"range {_num(lo)}-{_num(hi)}"


def _outside(e: dict[str, Any]) -> bool:
    """A cumulative point (relevant, kept or missed) outside its own summed range (D14); never clamped."""
    return any(f"{p}_low" in e and not (e[f"{p}_low"] - 1e-6 <= e[p] <= e[f"{p}_high"] + 1e-6)
               for p in ("rel", "kept", "missed"))


def _band_rows(bands: list[dict], ends: list[dict], stop: int | None, old: bool = False) -> list[dict[str, Any]]:
    """One table row per band, the tooltip text of each estimated band (D10), the observed and estimated values.
    ``old``: a series stored before recall-final-check-fixes, whose range left out records drawn into another sample."""
    rows = []
    for i, (b, e) in enumerate(zip(bands, ends)):
        ranks = f"{b['first']}-{b['last']}"
        few = b["relevant"] < FEW_RELEVANT
        rec = "–" if b.get("records") is None else _num(b["records"])
        obs = (f"Ranks {ranks}: {rec} records, {b['sampled']:,} sampled, {b['labelled']:,} labelled, "
               f"{b['relevant']:,} relevant observed" + (f" ({FEW})" if few else "") + ".")
        if b.get("in_other"):
            obs += " " + (CURVE_IN_OTHER_OLD if old else CURVE_IN_OTHER).format(n=_num(b["in_other"]))
        row: dict[str, Any] = {"band": b["band"], "ranks": ranks, "end": b["last"], "records": b.get("records"),
                               "sampled": b["sampled"], "labelled": b["labelled"], "relevant": b["relevant"],
                               "few": few, "estimated": stop is None or i < stop, "note": None, "outside": False,
                               "tooltip": None, "in_other": b.get("in_other"), "old_population": old}
        if not row["estimated"]:
            if b["labelled"] == 0:
                row["note"] = "no labelled record: not estimated"
            elif b["labelled"] < b["sampled"]:
                row["note"] = f"{b['sampled'] - b['labelled']:,} of {b['sampled']:,} sampled unlabelled: not estimated"
            else:
                row["note"] = "not estimated: an earlier band is not fully labelled"
            rows.append(row)
            continue
        has = "rel_low" in b
        band_rel = _num(b["rel"]) + " relevant" + (f" ({_span(b['rel_low'], b['rel_high'])})" if has else "")
        cum = [f"{_num(e[p])} {w}" + (f" ({_span(e[p + '_low'], e[p + '_high'])})" if f"{p}_low" in e else "")
               for p, w in (("rel", "relevant"), ("kept", "kept"), ("missed", "missed"))]
        row.update(rel=b["rel"], kept=b["kept"], missed=round(b["rel"] - b["kept"], 6),
                   rel_low=b.get("rel_low"), rel_high=b.get("rel_high"),
                   cum={p: e[p] for p in ("rel", "kept", "missed")},
                   cum_low={p: e.get(f"{p}_low") for p in ("rel", "kept", "missed")},
                   cum_high={p: e.get(f"{p}_high") for p in ("rel", "kept", "missed")}, outside=_outside(e))
        text = (f"{obs} Estimated in this band: {band_rel}, {_num(b['kept'])} kept, "
                f"{_num(b['rel'] - b['kept'])} missed. Up to rank {b['last']}: {', '.join(cum)}.")
        row["tooltip"] = text + (f" {CURVE_OUTSIDE}" if row["outside"] and not (old and row["in_other"]) else "")
        rows.append(row)
    return rows


def _stop(bands: list[dict]) -> tuple[int | None, str | None]:
    """The first band whose sampled records are not all labelled (answer 867a), with its sentence."""
    for i, b in enumerate(bands):
        if b["labelled"] < b["sampled"]:
            ranks = f"Ranks {b['first']}-{b['last']}"
            if b["labelled"] == 0:
                return i, (f"{ranks} hold no labelled record, so nothing is estimated from rank {b['first']} on; "
                           "label that band's sampled records and estimate recall again.")
            return i, (f"{ranks}: {b['sampled'] - b['labelled']:,} of {b['sampled']:,} sampled records are not "
                       f"labelled yet, so nothing is estimated from rank {b['first']} on; label them and estimate "
                       "recall again.")
    return None, None


def _curve(project: Project, info: dict[str, Any], sample: str, recall: dict[str, Any],
           last: dict[str, Any] | None, active: int | None = None) -> dict[str, Any]:
    """The chart section (D5, D9-D11, D14): the stored series when it belongs to the shown lines and what they read
    is unchanged, with its bands, ranges and sentences; the stop at a band not fully labelled."""
    from . import curve

    out: dict[str, Any] = {"state": "none", "sentence": None, "label": CURVE_LABEL, "legend": list(CURVE_LEGEND),
                           "variant": None, "points": [], "ends": [], "rows": [], "title": CURVE_TITLE, "desc": None,
                           "axes": dict(CURVE_AXES), "notes": [], "has_bounds": False, "level": None,
                           "range_legend": None, "old_series": False, "stop": None, "outside": False,
                           "method": None, "estimate_again": None}
    if last is None:
        return {**out, "state": "no_bands", "sentence": CURVE_NO_BANDS} if recall["state"] == "no_bands" else out
    rec = curve.stored(project, sample)
    if (not rec or not last.get("at") or rec.get("result_set_key") != info["key"]
            or rec.get("at") != last.get("at")):
        return {**out, "state": "missing", "sentence": CURVE_MISSING}
    if rec.get("checksum") != curve.checksum(project, sample, info["key"], rec.get("primary")):
        at = last.get("at")
        recall.update(stale=True, sentence=(f"The labels or screening decisions changed since this estimate ({at}); "
                                            "estimate recall again to update it."))
        # the same request as the Recall page's Estimate recall, which always names the set
        again = {"protocol": info["protocol"], "sample": sample, "result_set": info["result_set"]}
        return {**out, "state": "stale", "sentence": CURVE_STALE, "estimate_again": again,
                "again_label": CURVE_AGAIN, "again_command": f"wider recall {info['protocol']}"
                f"{_set_args(info, active)} --sample {sample}"}
    if not rec.get("bands"):
        return {**out, "state": "no_bands", "sentence": CURVE_NO_BANDS}
    pts = rec.get("points") or []
    if not pts or pts[-1][1] <= 0:   # no relevant record: no point estimate, and a flat line at zero would mislead
        from .uncertainty import NO_RELEVANT
        recall.update(sentence=f"Recall could not be estimated: {NO_RELEVANT}.")
    bands = rec.get("band_details")
    if bands is None:   # stored before band details (D11): the lines only, no ranges, no tooltips
        if not pts or pts[-1][1] <= 0:
            return {**out, "state": "no_relevant", "sentence": CURVE_NO_RELEVANT}
        ends = [{"end": e, "rel": r, "kept": k, "missed": round(r - k, 6)} for e, r, k in pts]
        rows = [{"end": e["end"], "estimated": True, "cum": {p: e[p] for p in ("rel", "kept", "missed")}}
                for e in ends]
        return {**out, "state": "current", "variant": rec.get("variant"), "points": pts, "ends": ends, "rows": rows,
                "old_series": True, "desc": _desc(ends[-1]), "notes": [CURVE_POINTS, CURVE_OLD]}
    stop, stop_sentence = _stop(bands)
    if stop is None and (not pts or pts[-1][1] <= 0):
        return {**out, "state": "no_relevant", "sentence": CURVE_NO_RELEVANT}
    ends = rec.get("ends") or []
    shown = ends if stop is None else ends[:stop]
    has = bool(rec.get("has_bounds"))
    level = rec.get("level")
    old = rec.get("population") != curve.POPULATION
    rows = _band_rows(bands, ends, stop, old)
    notes = [CURVE_POINTS, CURVE_RANGES.format(level=_level(level)) if has else CURVE_NO_RANGES]
    if old and any(b.get("in_other") for b in bands):
        from .validation import POPULATION_STALE
        notes.append(POPULATION_STALE.format(sample=sample))
    res = {**out, "state": "current", "variant": rec.get("variant"), "points": pts if stop is None else pts[:stop],
           "ends": shown, "rows": rows, "has_bounds": has, "level": level,
           "range_legend": CURVE_RANGE_LEGEND if has else None, "desc": _desc(shown[-1]) if shown else None,
           "notes": notes, "outside": any(r["outside"] for r in rows),
           # the plain sentence of D14; on an old series, the other sample's when that explains every point outside
           "outside_text": (CURVE_OUTSIDE_OTHER_OLD if old and not any(r["outside"] and not r["in_other"] for r in rows)
                            else CURVE_OUTSIDE),
           "method": {"summary": CURVE_METHOD[0], "text": CURVE_METHOD[1]} if has else None}
    if stop is not None:
        res["stop"] = {"band": bands[stop]["band"], "sentence": stop_sentence, "link": CURVE_LABEL_LINK,
                       "href": "#sample", "commands": [f"wider label export {sample} <file.csv>",
                                                       f"wider label import {sample} <file.csv>"]}
    return res


def _level(level) -> str:
    v = float(level or 0.95) * 100
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:g}"


def _desc(e: dict[str, Any]) -> str:
    return (f"Up to rank {e['end']}: {_num(e['rel'])} estimated relevant, {_num(e['kept'])} kept by screening, "
            f"{_num(e['missed'])} missed. The table below gives the same numbers.")


def _reference_sets(project: Project, info: dict[str, Any], active: int | None) -> dict[str, Any]:
    """The records per export preset and per pathway (D1, D2), counted as `wider export selection --dry-run` does."""
    from .selection import PATHWAY_WORDS, PATHWAYS, PRESET_WORDS, pattern_line, select_records

    sets = []
    for w in ("include", "include+maybe", "maybe", "exclude"):
        sel = select_records(project, info["key"], w)
        sets.append({"set": w, "kind": "preset", "label": PRESET_WORDS[w], "records": sel["n"],
                     "unresolved": sel["unresolved"]})
    every = select_records(project, info["key"], "every")
    sets += [{"set": p, "kind": "pathway", "label": PATHWAY_WORDS[p], "records": every["by_pathway"][p],
              "unresolved": None} for p in PATHWAYS]
    args = f" --protocol {info['protocol']}{_set_args(info, active)}"
    return {"sets": sets, "pattern_line": pattern_line(every), "command": f"wider export selection{args}",
            "all_command": f"wider export references --all{args}"}


def _set_line(s: dict[str, Any]) -> str:
    extra = f" (of which {s['unresolved']:,} unresolved)" if s["set"] == "include+maybe" else ""
    return f"{s['label']}: {s['records']:,}{extra}"


def _ref_name(stem: str) -> re.Pattern:
    """The file names (without suffix) the Export records card and `wider export selection` / `references --all`
    write for this set (D2, D7, D8); another protocol whose name starts with this one's never matches."""
    from .selection import PATHWAYS, PATTERNS, PRESETS

    alt = lambda xs: "|".join(re.escape(x) for x in sorted(xs, key=len, reverse=True))   # noqa: E731
    presets = alt(p.replace("+", "_and_") for p in PRESETS)
    sel = (f"(?:{presets})(?:_by_(?:{alt(PATHWAYS)})(?:_(?:{alt(PATHWAYS)}))*)?"
           f"(?:_reads_(?:{alt(PATTERNS)})(?:_(?:{alt(PATTERNS)}))*)?(?:_final)?(?:_picked)?")
    return re.compile(f"{re.escape(stem)}_(?:{sel}|{alt(PATHWAYS)}|index|audit|export_package_\\d{{8}}T\\d{{6}}(?:_\\d+)?)")


def _ref_label(stem: str, name: str) -> str:
    rest = name[len(stem) + 1:]
    return {"index": "Reference index", "audit": "Audit table"}.get(
        rest, "Export package" if rest.startswith("export_package_") else "Reference file")


def _documents(project: Project, info: dict[str, Any], active: int | None) -> list[dict[str, Any]]:
    """Each output file with its time, or "not written yet" and its command (D9); links only for files in exports/."""
    from importlib.util import find_spec

    from .outputs import _stem

    out = []
    no_word = find_spec("docx") is None
    for label, rel, cmd in DOCUMENTS:
        f = project.root / rel
        word = rel.endswith(".docx") and no_word
        out.append({"label": label, "path": rel, "exists": f.is_file(), "at": _when(f) if f.is_file() else None,
                    "link": rel.startswith("exports/") and f.is_file(), "rewrite": cmd,
                    "command": None if f.is_file() or word else cmd,
                    "missing": None if f.is_file() else NO_WORD if word else NOT_WRITTEN})
    refs = project.path("exports", "references")
    stem = _stem(info["key"])   # only this set's files: a protocol "main_v2" also matches "main_*"
    pat = _ref_name(stem)
    files = sorted(f for f in refs.glob(f"{stem}_*") if f.is_file() and pat.fullmatch(f.stem)) if refs.is_dir() else []
    for f in files:
        out.append({"label": _ref_label(stem, f.stem), "path": str(f.relative_to(project.root)), "exists": True,
                    "at": _when(f), "link": True, "command": None,
                    "rewrite": _reference_sets(project, info, active)["command"]})
    if not files:
        out.append({"label": f"Reference files of {info['protocol']} (set {info['result_set']})",
                    "path": "exports/references/", "exists": False, "at": None, "link": False,
                    "command": _reference_sets(project, info, active)["command"]})
    zips = sorted(project.path("exports").glob("replication_package_*.zip"))
    for f in zips:
        out.append({"label": "Replication package", "path": str(f.relative_to(project.root)), "exists": True,
                    "at": _when(f), "link": True, "command": None, "rewrite": "wider export package"})
    if not zips:
        out.append({"label": "Replication package", "path": "exports/replication_package_*.zip", "exists": False,
                    "at": None, "link": False, "command": "wider export package"})
    changed = last_data_change(project)
    for d in out:   # an earlier file with model decisions waits while the development sample is open (D4)
        d["stale"] = bool(d["exists"] and changed and d["at"] < changed)
        d["stale_text"] = stale_text(d.get("rewrite")) if d["stale"] else None
        d["waits"] = bool(d["exists"] and d["path"].startswith("exports/") and file_waits(project, d["path"]))
        d["waits_text"] = FILE_WAITS if d["waits"] else None
        if d["waits"]:
            d["link"] = False
    return out


def dashboard(project: Project, protocol: str | None = None, result_set: int | None = None,
              sample: str | None = None) -> dict[str, Any]:
    """Everything the Results dashboard shows for ``protocol``'s set ``result_set`` (default: its active set; without
    a protocol the most recently screened one) and ``sample`` (default ``protocol.sampling.probability_sample``).
    ValueError "no screening decisions yet" before any decision, like ``export_references``."""
    from ..screen.agreement import markdown, pass_agreement
    from ..screen.compare import report_lines
    from ..screen.result_sets import active_set
    from .outputs import _set_info

    info = _set_info(project, protocol, result_set)
    act = active_set(project, info["protocol"])
    active = act["result_set"] if act else None
    default_sample = project.config.protocol.sampling.probability_sample
    sample = sample or default_sample
    ag = pass_agreement(project, info["key"])
    recall, last = _recall(project, info, sample, default_sample, active)
    chart = _curve(project, info, sample, recall, last, active)
    if chart["state"] == "current":   # the page's table prints the CLI's own cells (D10)
        chart.update(head=table_head(chart), cells=[table_cells(r) for r in chart["rows"]])
    from ..sample.package import dashboard_line
    blinded = dashboard_line(project)   # review packages (reviewer-roles-and-blinding D11); None without one
    docs = _documents(project, info, active)
    return {**({"blinded_review": blinded} if blinded else {}), "protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": info["key"],
            "active_set": active, "sample": sample, "choices": _choices(project, info["protocol"]),
            "headings": HEADINGS,
            "heading": _heading(project, info),
            "agreement": {**ag, "lines": markdown(ag)},
            "comparisons": {"lines": report_lines(project)},
            "recall": recall, "curve": chart,
            "counts": _counts(project, info),
            "count_lines": [list(x) for x in COUNT_LINES],
            "reference_sets": _reference_sets(project, info, active),
            "documents": docs, "documents_stale_line": STALE_LINE if any(x["stale"] for x in docs) else None}


def provisional_line(k: dict[str, Any]) -> str | None:
    """The warning above the PRISMA counts while they may still change (D32 F4)."""
    line = k.get("provisional_line")   # the diagrams' sentence (outputs.provisional_line), then what to do
    return f"{line} Finish deduplication before you report these counts." if line else None


def _counts(project: Project, info: dict[str, Any]) -> dict[str, Any]:
    from .outputs import prisma_counts

    k = prisma_counts(project, info["protocol"], info["result_set"])
    return {**k, "provisional_line": provisional_line(k)}


COUNT_LINES = (("identified", "Records identified"), ("duplicates_removed", "Duplicates removed"),
               ("not_checked", "Not yet checked for duplicates"),
               ("unique", "Records after deduplication"), ("screened", "Records screened"),
               ("excluded", "Records excluded"), ("included_ta", "Included at title and abstract"),
               ("maybe_ta", "Maybe at title and abstract"), ("sought", "Reports sought for retrieval"),
               ("not_screened", "Records not screened"))


def dashboard_lines(d: dict[str, Any]) -> list[str]:
    """The dashboard as text, section by section in the page's order (``wider results``)."""
    h = d["heading"]
    out = ([d["blinded_review"]["line"], ""] if d.get("blinded_review") else [])   # above the sections, once
    out += [h["title"], h["models"]] + ([h["role_note"]] if h.get("role_note") else []) + list(h.get("shared") or [])
    out += ["", HEADINGS["agreement"], *d["agreement"]["lines"]]
    out += ["", HEADINGS["comparisons"], *d["comparisons"]["lines"]]
    r = d["recall"]
    out += ["", HEADINGS["recall"]]
    if r["state"] == "current":
        if r["sentence"]:
            out.append(r["sentence"])
        if r.get("headline"):
            out.append(r["headline"])
        if r["as_of"]:
            out.append(f"as of {r['as_of']}")
        out += r["lines"]
    else:
        out.append(r["sentence"])
    c = d["curve"]
    out += ["", HEADINGS["curve"]]
    if c["state"] == "current":
        out += [c["label"], *_table_lines(c), *c["notes"]]
        if c["stop"]:
            out += [c["stop"]["sentence"], *(f"Label them: {cmd}" for cmd in c["stop"]["commands"])]
        if c["outside"]:
            out.append(c["outside_text"])
        if c["method"]:
            out += [f"{c['method']['summary']}: {c['method']['text']}"]
    elif c["state"] == "stale":
        out += [c["sentence"], f"{c['again_label']}: {c['again_command']}"]
    elif c["sentence"]:
        out.append(c["sentence"])
    else:
        out.append("No chart: no recall estimate is shown.")
    k = d["counts"]
    out += ["", HEADINGS["counts"]] + ([k["provisional_line"]] if k.get("provisional_line") else [])
    out += [f"{label}: {k[key]:,}" for key, label in COUNT_LINES if key != "not_checked" or k.get(key)]
    rs = d["reference_sets"]
    out += ["", HEADINGS["reference_sets"]] + [_set_line(s) for s in rs["sets"]] + [rs["pattern_line"]]
    out += [f"Export records: {rs['command']}", f"Every set at once: {rs['all_command']}"]
    out += ["", HEADINGS["documents"]] + ([d["documents_stale_line"]] if d.get("documents_stale_line") else [])
    for doc in d["documents"]:
        out.append(f"{doc['label']}: {doc['path']}, {FILE_WAITS}" if doc.get("waits")
                   else f"{doc['label']}: {doc['path']}, {doc['at']}"
                        + (f"; {doc['stale_text']}" if doc.get("stale") else "") if doc["exists"]
                   else f"{doc['label']}: {doc.get('missing') or NOT_WRITTEN}"
                        + (f" ({doc['command']})" if doc["command"] else ""))
    return out


TABLE_HEAD = ("ranks", "records", "sampled", "labelled", "relevant", "relevant (band)", "kept (band)", "missed (band)",
              "relevant up to end", "kept up to end", "missed up to end")


def table_cells(row: dict[str, Any]) -> list[str]:
    """One band's table cells, the same on the page and in `wider results` (D10): observed, then estimated."""
    if "ranks" not in row:   # an old series: band ends only
        return [str(row["end"]), *(_num(row["cum"][p]) for p in ("rel", "kept", "missed"))]
    obs = [row["ranks"], "–" if row["records"] is None else _num(row["records"]), f"{row['sampled']:,}",
           f"{row['labelled']:,}", f"{row['relevant']:,}" + (f" ({FEW})" if row["few"] else "")]
    other = ([(CURVE_IN_OTHER_OLD if row.get("old_population") else CURVE_IN_OTHER).format(n=_num(row["in_other"]))]
             if row.get("in_other") else [])
    if not row["estimated"]:
        return obs + [row["note"]] + other
    band = [_num(row["rel"]) + (f" ({_span(row['rel_low'], row['rel_high'])})" if row["rel_low"] is not None else ""),
            _num(row["kept"]), _num(row["missed"])]
    cum = [_num(row["cum"][p]) + (f" ({_span(row['cum_low'][p], row['cum_high'][p])})"
                                  if row["cum_low"][p] is not None else "") for p in ("rel", "kept", "missed")]
    return obs + band + cum + ([CURVE_OUTSIDE] if row["outside"] and not (other and row.get("old_population")) else []) + other


def table_head(c: dict[str, Any]) -> list[str]:
    return ["band end", "relevant", "kept", "missed"] if c["old_series"] else list(TABLE_HEAD)


def _table_lines(c: dict[str, Any]) -> list[str]:
    head = table_head(c)
    groups = "" if c["old_series"] else f"observed: {' | '.join(head[:5])} || estimated: {' | '.join(head[5:])}"
    return ([groups] if groups else [" | ".join(head)]) + [" | ".join(table_cells(r)) for r in c["rows"]]
