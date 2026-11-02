"""Record exports chosen on the Export records card (design D1-D8 of decision-set-exports-and-found-chart): a preset,
pathways, first-two-read patterns, final only and records chosen one by one; the count before export; RIS by default,
BibTeX and CSV on request, several formats in one ZIP; the audit CSV of the whole set; and the export package.

Everything here reads the store only (no decision, set or question changes) and writes under ``exports/references/``,
on this computer. The writers are those of the older per-set export (:mod:`wider.recall.outputs`), so both give the same
ids, metadata and unresolved mark.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.provenance import local_time, utcnow
from .outputs import SETS, _all_rows, _set_info, _stem, set_name, write_format

if TYPE_CHECKING:
    from ..core.project import Project

PRESETS = ("include", "include+maybe", "maybe", "exclude", "unresolved", "every")
PRESET_WORDS = {"include": "Include", "include+maybe": "Include + Maybe", "maybe": "Maybe only", "exclude": "Exclude",
                "unresolved": "Unresolved only", "every": "Every decision"}
PATHWAYS = ("agreed", "third_read", "split", "researcher")
PATHWAY_WORDS = {"agreed": "both configurations agreed", "third_read": "resolved by the third model",
                 "split": "unresolved disagreement", "researcher": "decided by a researcher"}
PATTERNS = ("include_exclude", "include_maybe", "maybe_exclude", "agreed")
PATTERN_WORDS = {"include_exclude": "include / exclude", "include_maybe": "include / maybe",
                 "maybe_exclude": "maybe / exclude", "agreed": "both reads the same"}
FORMATS = ("ris", "bib", "csv")
FORMAT_WORDS = {"ris": "RIS", "bib": "BibTeX", "csv": "CSV"}
OTHER_ROUTE = "by another route (imported, carried, one pass or older)"
PACKAGE_ADD = ("include", "maybe", "exclude", "unresolved", "selection")
PACKAGE_EMPTY = "no records, so no file is written"
ABSTRACTS_SENTENCE = "Abstracts are the publishers' text; leave them out if you pass these files to someone else."
AUDIT_COLUMNS = ("record_id", "title", "year", "doi", "final_decision", "status", "screen_call", "pathway",
                 "pathway_label", "pattern", "decided_by", "pass_1_read", "pass_1_model", "pass_2_read", "pass_2_model",
                 "pass_3_read", "pass_3_model", "researcher_decision", "researcher_note", "origin", "protocol",
                 "result_set", "majority", "third_read", "settings_hash")


def _list(value, allowed: tuple[str, ...], what: str) -> list[str]:
    """``value`` (a comma list or a sequence) as the allowed keys in their fixed order; an unknown key is refused."""
    if value is None:
        return []
    items = [x.strip() for x in (value.split(",") if isinstance(value, str) else value) if x and str(x).strip()]
    bad = [x for x in items if x not in allowed]
    if bad:
        raise ValueError(f"unknown {what} {', '.join(bad)}; choose from {', '.join(allowed)}")
    return [x for x in allowed if x in items]


def _ids(value) -> list[int] | None:
    if value is None or value == "":
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    try:
        return [int(x) for x in items if str(x).strip()]
    except ValueError:
        raise ValueError("records must be record ids separated by commas, e.g. 3,7,12") from None


def check_formats(formats) -> list[str]:
    fs = _list(formats if formats is not None else "ris", FORMATS, "format")
    if not fs:
        raise ValueError("choose at least one format: ris, bib, csv")
    return fs


def name_of(stem: str, preset: str, pathways: list[str], patterns: list[str], final_only: bool, picked: bool) -> str:
    """``<stem>_<preset>[_by_<paths>][_reads_<patterns>][_final][_picked]`` (design D2); preset ``every`` with one
    pathway and nothing else keeps the older route set's name ``<stem>_<pathway>``."""
    if preset == "every" and len(pathways) == 1 and not (patterns or final_only or picked):
        return f"{stem}_{pathways[0]}"
    name = f"{stem}_{preset.replace('+', '_and_')}"
    if pathways:
        name += "_by_" + "_".join(pathways)
    if patterns:
        name += "_reads_" + "_".join(patterns)
    if final_only:
        name += "_final"
    if picked:
        name += "_picked"
    return name


def _words(preset: str, pathways: list[str], patterns: list[str], final_only: bool) -> str:
    return ", ".join([PRESET_WORDS[preset], *(PATHWAY_WORDS[p] for p in pathways),
                      *(f"reads {PATTERN_WORDS[p]}" for p in patterns), *(["unresolved left out"] if final_only else [])])


def _records_word(n: int) -> str:
    return f"{n:,} record" + ("" if n == 1 else "s")


def _format_words(formats: list[str]) -> str:
    words = " + ".join(FORMAT_WORDS[f] for f in formats)
    return words + (" in one ZIP" if len(formats) > 1 else "")


def select_records(project: Project, key: str, preset: str = "include+maybe", pathways=None, patterns=None,
                   final_only: bool = False, records=None) -> dict[str, Any]:
    """The records of set ``key`` the choices select, with the counts (design D1, D2). ``filtered`` is the selection
    before records are chosen one by one; ``rows`` what is exported. An id outside the selection is refused."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset}; choose from {', '.join(PRESETS)}")
    paths, pats, ids = _list(pathways, PATHWAYS, "pathway"), _list(patterns, PATTERNS, "pattern"), _ids(records)
    rows = _all_rows(project, key)
    if preset in SETS:
        rows = [r for r in rows if r["decision"] in SETS[preset]]
    elif preset == "unresolved":
        rows = [r for r in rows if r["status"] == "unresolved"]
    if paths:   # any of them; `split` selects by status, so carried or imported unresolved records are in it (D2)
        rows = [r for r in rows if any(r["status"] == "unresolved" if p == "split" else r["route"] == p for p in paths)]
    if pats:
        rows = [r for r in rows if r["pattern"] in pats]
    left_out = sum(r["status"] == "unresolved" for r in rows) if final_only else 0
    if final_only:
        rows = [r for r in rows if r["status"] != "unresolved"]
    filtered = rows
    picked = False
    if ids is not None:
        have = {r["record_id"] for r in filtered}
        outside = [i for i in ids if i not in have]
        if outside:
            raise ValueError(f"record{'s' if len(outside) > 1 else ''} {', '.join(map(str, outside))} "
                             f"{'are' if len(outside) > 1 else 'is'} not in this selection")
        want = set(ids)
        rows = [r for r in filtered if r["record_id"] in want]
        picked = len(rows) < len(filtered)
    by_path = {p: sum((r["status"] == "unresolved") if p == "split" else r["route"] == p for r in rows) for p in PATHWAYS}
    other = sum(r["route"] not in PATHWAYS and r["status"] != "unresolved" for r in rows)
    by_pattern = {p: sum(r["pattern"] == p for r in rows) for p in PATTERNS}
    return {"key": key, "preset": preset, "pathways": paths, "patterns": pats, "final_only": final_only,
            "picked": picked, "rows": rows, "filtered": filtered, "n": len(rows), "n_filtered": len(filtered),
            "unresolved": sum(r["status"] == "unresolved" for r in rows), "unresolved_left_out": left_out,
            "by_pathway": by_path, "other_route": other, "by_pattern": by_pattern,
            "words": _words(preset, paths, pats, final_only)}


def count_line(sel: dict[str, Any], formats: list[str]) -> str:
    """"5 records (of which 1 unresolved: no majority) · RIS", "3 records (2 unresolved left out) · RIS" or "3 of 5
    records chosen · RIS" (design D2, D4)."""
    if sel["picked"]:
        head = f"{sel['n']:,} of {_records_word(sel['n_filtered'])} chosen"
    else:
        head = _records_word(sel["n"])
    if sel["unresolved_left_out"]:
        head += f" ({sel['unresolved_left_out']:,} unresolved left out)"
    elif sel["unresolved"]:
        head += f" (of which {sel['unresolved']:,} unresolved: no majority)"
    return f"{head} · {_format_words(formats)}"


def pathway_line(sel: dict[str, Any]) -> str:
    parts = [f"{sel['by_pathway'][p]:,} {PATHWAY_WORDS[p]}" for p in PATHWAYS if sel["by_pathway"][p]]
    if sel["other_route"]:
        parts.append(f"{sel['other_route']:,} {OTHER_ROUTE}")
    return "By how the decision was reached: " + ("; ".join(parts) if parts else "none")


def pattern_line(sel: dict[str, Any]) -> str:
    parts = [f"{sel['by_pattern'][p]:,} {PATTERN_WORDS[p]}" for p in PATTERNS if sel["by_pattern"][p]]
    rest = sel["n"] - sum(sel["by_pattern"].values())
    if rest:
        parts.append(f"{rest:,} with fewer than two reads")
    return "First two reads: " + ("; ".join(parts) if parts else "none")


def _record_list(rows: list[dict]) -> list[dict[str, Any]]:
    return [{"record_id": r["record_id"], "title": r["title"], "year": r["year"], "decision": r["decision"],
             "status": r["status"], "pathway": r["route"]} for r in rows]


def _write_atomic(path: Path, write) -> None:
    """Call ``write(tmp_path)`` and move the result into place, so a failure leaves no half file (design D4)."""
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    try:
        write(Path(tmp))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _bytes_of(fmt: str, rows: list[dict], info: dict[str, Any], abstracts: bool) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / f"x.{fmt}"
        write_format(fmt, f, rows, info, abstracts)
        return f.read_bytes()


def _write_selection(project: Project, info: dict[str, Any], name: str, rows: list[dict], formats: list[str],
                     abstracts: bool) -> Path:
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    if len(formats) == 1:
        path = out / f"{name}.{formats[0]}"
        _write_atomic(path, lambda t: write_format(formats[0], t, rows, info, abstracts))
        return path
    path = out / f"{name}.zip"

    def zipped(t: Path) -> None:
        with zipfile.ZipFile(t, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for fmt in formats:
                z.writestr(f"{name}.{fmt}", _bytes_of(fmt, rows, info, abstracts))
    _write_atomic(path, zipped)
    return path


def _target(name: str, formats: list[str]) -> str:
    return f"{name}.{formats[0]}" if len(formats) == 1 else f"{name}.zip"


def _stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def export_selection(project: Project, protocol: str | None = None, result_set: int | None = None,
                     preset: str = "include+maybe", pathways=None, patterns=None, final_only: bool = False,
                     records=None, formats=None, abstracts: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Export the selection (design D1-D4) to ``exports/references/``: one loose file for one format, one ZIP for
    several. The dry run counts and names the file without writing or recording anything. An empty selection writes
    nothing and leaves an earlier file of the same name as it was."""
    fs = check_formats(formats)
    info = _set_info(project, protocol, result_set)
    sel = select_records(project, info["key"], preset, pathways, patterns, final_only, records)
    name = name_of(_stem(info["key"]), sel["preset"], sel["pathways"], sel["patterns"], final_only, sel["picked"])
    target = _target(name, fs)
    res: dict[str, Any] = {"protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": info["key"],
                           "set_name": set_name(info), "preset": sel["preset"], "pathways": sel["pathways"],
                           "patterns": sel["patterns"], "final_only": final_only, "formats": fs, "abstracts": abstracts,
                           "n": sel["n"], "n_filtered": sel["n_filtered"], "unresolved": sel["unresolved"],
                           "unresolved_left_out": sel["unresolved_left_out"], "by_pathway": sel["by_pathway"],
                           "other_route": sel["other_route"], "by_pattern": sel["by_pattern"],
                           "count_line": count_line(sel, fs), "pathway_line": pathway_line(sel),
                           "pattern_line": pattern_line(sel), "file": f"exports/references/{target}",
                           "records": _record_list(sel["filtered"]), "picked": sel["picked"], "written": False,
                           "abstracts_sentence": ABSTRACTS_SENTENCE}
    if not sel["rows"]:
        msg = f"No records match {sel['words']}; nothing was written."
        earlier = project.path("exports", "references") / target
        if earlier.exists():
            msg += f" The earlier file {target} ({_stamp(earlier)}) no longer matches this selection."
        res["message"] = msg
        return res
    if dry_run:
        res["message"] = f"{res['count_line']}; would write {res['file']}"
        return res
    path = _write_selection(project, info, name, sel["rows"], fs, abstracts)
    rel = str(path.relative_to(project.root))
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="selection", headline=(
        f"{_records_word(sel['n'])} ({sel['words']}, protocol {set_name(info)}) exported as {_format_words(fs)}: "
        f"{path.name}" + ("" if abstracts else ", without abstracts") + "."),
        metrics={"records": sel["n"], "unresolved": sel["unresolved"]}, outputs=[rel], next_stage=None, warnings=[]))
    res.update(written=True, file=rel, message=f"{res['count_line']}; wrote {rel}")
    return res


# ----------------------------------------------------------------------------------------------- audit CSV (D7)
def _calls(project: Project, ids: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    uniq = sorted(set(ids))
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        out.update({r["call_id"]: dict(r) for r in project.store.query(
            "SELECT c.call_id, c.pass, c.provider, COALESCE(c.model, r.model) model FROM screen_calls c JOIN runs r "
            f"ON r.run_id=c.run_id WHERE c.call_id IN ({','.join('?' * len(chunk))})", chunk)})
    return out


def _call_ids(raw) -> list[int | None]:
    from ..screen.agreement import _votes
    return [int(c) if isinstance(c, int) or str(c).isdigit() else None for c in _votes(raw)]


def _origin_text(raw) -> str:
    from ..screen.agreement import _origin
    o = _origin(raw)
    if not o:
        return ""
    return f"{o.get('kind') or ''} set {o.get('set')}".strip() if o.get("set") is not None else str(o.get("kind") or "")


def audit_rows(project: Project, info: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per record in the set's scope (design D7): decided, failed (a call that did not succeed) or with no call
    (``none``). No abstract and no researcher's name."""
    from ..screen import result_sets as rs
    from ..screen.agreement import ROUTE_LABELS, _votes, reads, set_rules
    from ..screen.engine import record_set
    key = info["key"]
    row = rs.row_by_key(project, key) or {}
    rules = set_rules(project, key)
    decided = {r["record_id"]: r for r in _all_rows(project, key)}
    scope = row.get("scope")
    ids = set(decided)
    if scope and scope not in ("unknown", "mixed"):
        try:
            ids |= {r["record_id"] for r in record_set(project, scope)}
        except (KeyError, ValueError):
            pass
    runs = rs.run_ids(row)
    failed: set[int] = set()
    if runs:
        q = ",".join("?" * len(runs))
        failed = {r["record_id"] for r in project.store.query(
            f"SELECT DISTINCT record_id FROM screen_calls WHERE run_id IN ({q}) AND ok=0", runs)}
    answers: dict[int, dict] = {}
    for r in project.store.query("SELECT record_id, context, answer FROM questions WHERE kind='screen_split' AND "
                                 "status='answered' ORDER BY question_id", ()):
        ctx = json.loads(r["context"]) if r["context"] else {}
        if ctx.get("protocol") == key and r["record_id"] is not None:
            answers[r["record_id"]] = json.loads(r["answer"]) if r["answer"] else {}
    calls = _calls(project, [c for d in decided.values() for c in _call_ids(d["vote_calls"]) if c is not None])
    meta = {rid: decided[rid] for rid in decided}
    rest = sorted(ids - set(decided))
    for i in range(0, len(rest), 500):
        chunk = rest[i:i + 500]
        meta.update({r["record_id"]: dict(r) for r in project.store.query(
            f"SELECT record_id, title, year, doi FROM records WHERE record_id IN ({','.join('?' * len(chunk))})", chunk)})
    out = []
    for rid in sorted(ids):
        m, d = meta.get(rid, {}), decided.get(rid)
        base = {"record_id": rid, "title": m.get("title") or "", "year": m.get("year") or "", "doi": m.get("doi") or "",
                "protocol": info["protocol"], "result_set": info["result_set"], "majority": rules["majority"],
                "third_read": "blind" if rules["blind_third_read"] else "open",
                "settings_hash": row.get("settings_hash") or ""}
        if d is None:
            out.append({**dict.fromkeys(AUDIT_COLUMNS, ""), **base, "status": "not screened",
                        "screen_call": "failed" if rid in failed else "none"})
            continue
        votes, vc = _votes(d["votes"]), _call_ids(d["vote_calls"])
        own = [(v, vc[i] if i < len(vc) else None) for i, v in enumerate(votes) if v in reads([v])]  # researcher's out
        passes = {}
        for i in range(3):
            v, cid = own[i] if i < len(own) else ("", None)
            c = calls.get(cid) if cid is not None else None
            passes[f"pass_{i + 1}_read"] = v
            passes[f"pass_{i + 1}_model"] = ("researcher" if c and c.get("provider") == "researcher" else (c or {}).get("model") or "")
        ans = answers.get(rid) or {}
        route = d["route"]
        decided_by = d["decided_by"] or ""
        out.append({**base, **passes, "final_decision": d["decision"], "status": d["status"], "screen_call": "ok",
                    "pathway": route, "pathway_label": ROUTE_LABELS.get(route, route), "pattern": d["pattern"] or "",
                    "decided_by": decided_by,
                    "researcher_decision": ans.get("choice") or ("" if route != "researcher" else d["decision"]),
                    "researcher_note": ans.get("text") or "", "origin": _origin_text(d["origin"]) or (
                        decided_by if decided_by.startswith(("carried from", "imported from", "absent from")) else "")})
    return out


def _audit_bytes(rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=AUDIT_COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def write_audit(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict[str, Any]:
    """``exports/references/<stem>_audit.csv`` (design D7); one stage result."""
    info = _set_info(project, protocol, result_set)
    rows = audit_rows(project, info)
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{_stem(info['key'])}_audit.csv"
    data = _audit_bytes(rows)
    _write_atomic(path, lambda t: t.write_bytes(data))
    rel = str(path.relative_to(project.root))
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="audit", headline=(
        f"Audit CSV of protocol {set_name(info)} written: {_records_word(len(rows))} with each pass's read and model."),
        metrics={"records": len(rows)}, outputs=[rel], next_stage=None, warnings=[]))
    return {"protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": info["key"],
            "n": len(rows), "file": rel, "message": f"wrote {rel} ({_records_word(len(rows))})"}


# ----------------------------------------------------------------------------------------------- export package (D8)
def _summary(project: Project, info: dict[str, Any], files: list[dict], abstracts: bool) -> str:
    from ..screen.agreement import set_rules
    from ..screen.protocol import pass_models
    every = select_records(project, info["key"], "every")
    dec = {d: sum(r["decision"] == d for r in every["rows"]) for d in ("include", "maybe", "exclude")}
    rules = set_rules(project, info["key"])
    models = pass_models(project, info["key"]).get("models") or {}
    lines = [f"# WIDER export package: protocol {set_name(info)}", "", f"Created {local_time(utcnow())}.",
             "", "## Decisions", "", f"- include: {dec['include']:,}",
             f"- maybe: {dec['maybe']:,} (of which {every['unresolved']:,} unresolved: no majority)",
             f"- exclude: {dec['exclude']:,}", "", "## How the decisions were reached", ""]
    lines += [f"- {PATHWAY_WORDS[p]}: {every['by_pathway'][p]:,}" for p in PATHWAYS]
    lines += [f"- {OTHER_ROUTE}: {every['other_route']:,}", "", "## Rules and models", "",
              f"- passes: {rules['passes']}, majority: {rules['majority']} of 3, third read: "
              f"{'blind' if rules['blind_third_read'] else 'open'}"]
    if isinstance(models, dict):
        lines += [f"- pass {k}: {', '.join(v) if isinstance(v, list) else v}" for k, v in models.items()]
    lines += ["", "## Files", ""]
    lines += [f"- {f['name']}: {f['label']}" for f in files]
    lines += ["", "Abstracts: " + ("included (the publishers' text; leave them out before passing these files on)."
                                   if abstracts else "left out."),
              "This package stays on this computer; WIDER sends it nowhere.", ""]
    return "\n".join(lines)


def write_records_package(project: Project, protocol: str | None = None, result_set: int | None = None, add=None,
                          formats=None, abstracts: bool = True, dry_run: bool = False,
                          selection: dict[str, Any] | None = None) -> dict[str, Any]:
    """``exports/references/<stem>_export_package_<timestamp>.zip`` (design D8): by default the Include + Maybe RIS
    (unresolved marked), the audit CSV, ``summary.md`` and ``MANIFEST.txt``; ``add`` any of include, maybe, exclude,
    unresolved and selection (the card's choices in ``selection``), in ``formats``. The dry run lists the files."""
    fs = check_formats(formats)
    adds = _list(add, PACKAGE_ADD, "package part")
    sel_args = {k: v for k, v in (selection or {}).items() if v not in (None, "", [], False)}
    if sel_args and "selection" not in adds:
        raise ValueError("a filter or records with --package needs --add selection")
    info = _set_info(project, protocol, result_set)
    stem = _stem(info["key"])
    parts: list[tuple[str, dict[str, Any]]] = [("include+maybe", select_records(project, info["key"]))]
    for a in adds:
        if a == "selection":
            s = select_records(project, info["key"], **{"preset": "include+maybe", **sel_args})
        else:
            s = select_records(project, info["key"], a)
        parts.append((a, s))
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _a, s in parts:
        name = name_of(stem, s["preset"], s["pathways"], s["patterns"], s["final_only"], s["picked"])
        for fmt in fs:
            fname = f"{name}.{fmt}"
            if fname in seen:
                continue
            seen.add(fname)
            # a part that holds no record is listed but writes no empty file that would look like an export
            files.append({"name": fname, "fmt": fmt, "rows": s["rows"], "n": s["n"], "empty": not s["n"],
                          "label": (PACKAGE_EMPTY if not s["n"] else f"{_records_word(s['n'])}"
                                    + (f", {s['unresolved']:,} marked unresolved" if s["unresolved"] else ""))})
    audit = audit_rows(project, info)
    files.append({"name": f"{stem}_audit.csv", "fmt": "audit", "rows": audit, "n": len(audit),
                  "label": f"audit, {_records_word(len(audit))}"})
    listing = [{"name": f["name"], "records": f["n"], "label": f["label"]} for f in files]
    listing += [{"name": "summary.md", "records": None, "label": "counts, models, rules, set and date"},
                {"name": "MANIFEST.txt", "records": None, "label": "SHA-256 of every file"}]
    n_files = len(listing) - sum(1 for f in files if f.get("empty"))
    stamp = utcnow()[:19].replace(":", "").replace("-", "")
    zname, n = f"{stem}_export_package_{stamp}.zip", 2
    while project.path("exports", "references", zname).exists():   # an earlier package is kept (design D8)
        zname, n = f"{stem}_export_package_{stamp}_{n}.zip", n + 1
    res: dict[str, Any] = {"protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": info["key"],
                           "files": listing, "file": f"exports/references/{zname}", "abstracts": abstracts,
                           "formats": fs, "add": adds, "written": False,
                           "lines": [f"{x['name']}: {x['label']}" for x in listing]}
    if dry_run:
        res["message"] = f"would write {res['file']} with {n_files} files"
        return res
    blobs: dict[str, bytes] = {}
    for f in (f for f in files if not f.get("empty")):
        blobs[f["name"]] = _audit_bytes(f["rows"]) if f["fmt"] == "audit" else _bytes_of(f["fmt"], f["rows"], info, abstracts)
    blobs["summary.md"] = _summary(project, info, files, abstracts).encode("utf-8")
    manifest = [f"WIDER export package, protocol {set_name(info)}", f"created {utcnow()}",
                "Stays on this computer." + (" Holds abstracts." if abstracts else " Holds no abstracts."), "",
                "sha256  file"] + [f"{hashlib.sha256(b).hexdigest()}  {n}" for n, b in blobs.items()]
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    path = out / zname

    def zipped(t: Path) -> None:
        with zipfile.ZipFile(t, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for n, b in blobs.items():
                z.writestr(n, b)
            z.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    _write_atomic(path, zipped)
    rel = str(path.relative_to(project.root))
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="export_package", headline=(
        f"Export package of protocol {set_name(info)} written with {n_files} files: {path.name}."),
        metrics={"files": n_files}, outputs=[rel], next_stage=None, warnings=[]))
    res.update(written=True, file=rel, message=f"wrote {rel} ({n_files} files)")
    return res


def run_selection(project: Project, protocol: str | None = None, result_set: int | None = None, preset=None,
                  pathways=None, patterns=None, final_only: bool = False, records=None, formats=None,
                  abstracts: bool = True, dry_run: bool = False, audit: bool = False, package: bool = False,
                  add=None) -> dict[str, Any]:
    """The one entry of ``wider export selection`` and ``/api/outputs/selection`` (design D5), so both refuse the same
    choices: ``audit`` covers the whole set and takes no selection option; ``package`` takes ``add``."""
    from ..screen.devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    if audit and package:
        raise ValueError("choose --audit or --package, not both")
    if add and not package:
        raise ValueError("--add belongs to --package")
    if audit:
        given = [o for o, v in (("--preset", preset), ("--pathway", pathways), ("--pattern", patterns),
                                ("--final-only", final_only), ("--records", records), ("--format", formats),
                                ("--no-abstracts", not abstracts)) if v not in (None, False, "", [])]
        if given:
            raise ValueError(f"--audit covers the whole set; leave out {', '.join(given)}")
        if dry_run:
            info = _set_info(project, protocol, result_set)
            n = len(audit_rows(project, info))
            return {"n": n, "file": f"exports/references/{_stem(info['key'])}_audit.csv", "written": False,
                    "message": f"would write exports/references/{_stem(info['key'])}_audit.csv ({_records_word(n)})"}
        return write_audit(project, protocol, result_set)
    if package:
        return write_records_package(project, protocol, result_set, add, formats, abstracts, dry_run, selection={
            "preset": preset, "pathways": pathways, "patterns": patterns, "final_only": final_only, "records": records})
    return export_selection(project, protocol, result_set, preset or "include+maybe", pathways, patterns, final_only,
                            records, formats, abstracts, dry_run)
