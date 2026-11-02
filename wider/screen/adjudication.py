"""Importing an outside adjudicator's decisions for some records of a set (partial-adjudication-import).

The file is a CSV (the Results export or any CSV with a record id, DOI or title) with one decision column. The import
builds a new set from a finished set: every decision is copied as it is, and only the records whose row has a decision
change, each decided by ``researcher`` and naming the adjudicator. Rows the import cannot use are listed with their
reason; nothing changes on a guess. The new set becomes active; the set it was built from stays unchanged. The import
is one ``adjudication_import`` History change, undone by the generic row restore (D7, D8)."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.text import norm_title
from . import result_sets as rs
from .agreement import _origin, _votes, reads
from .decisions import ALIASES, DECISIONS, set_decision
from .review import NOTHING, Refusal, _hidden, _hidden_text, _state, _target, answered_wait

KIND = "adjudication_import"
NEED_NAME = "an adjudicator name is needed (--adjudicator NAME)"
# the keys of the rows left out, in the order the result lists their counts (D4)
LEFT_OUT = ("unreadable", "unmatched", "title_differs", "similar_title", "not_in_set", "hidden", "failed_read",
            "failed_answered", "duplicate")


def _read_csv(data: bytes, file_name: str) -> tuple[list[str], list[dict[str, str]]]:
    bad = Refusal(f"{file_name} is not a CSV file WIDER can read (UTF-8, comma separated, a header row)", 400, 1)
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = [(h or "").strip().lower() for h in (reader.fieldnames or [])]
        rows = [{(k or "").strip().lower(): (v or "").strip() if isinstance(v, str) else "" for k, v in r.items()}
                for r in reader]
    except (UnicodeDecodeError, csv.Error):
        raise bad from None
    if not [h for h in header if h] or "\x00" in text:
        raise bad
    return header, rows


def _file_sets(rows: list[dict[str, str]]) -> set[tuple[str, str]]:
    """The ``(protocol, result_set)`` pairs the file's rows name (the Results export carries both)."""
    return {(r.get("protocol", ""), r.get("result_set", "")) for r in rows if r.get("protocol") or r.get("result_set")}


def import_adjudication(project: Project, protocol: str, data: bytes, file_name: str, adjudicator: str | None = None,
                        column: str = "adjudication", result_set: int | None = None, author: str | None = None,
                        dry_run: bool = False) -> dict[str, Any]:
    """Import the adjudicator's decisions in ``column`` of the CSV ``data`` into a new set built from set
    ``result_set`` of ``protocol`` (default: its active set). ``file_name`` is shown in the result only, never stored.
    Raises :class:`~wider.screen.review.Refusal` (``code`` 1 for the file, the column, no usable row or an unfinished
    set; 2 for a missing or refused name). The dry run returns the same counts and lists and writes nothing."""
    from ..core import journal, researchers
    from ..core.provenance import sha256_bytes

    st = project.store
    col = (column or "adjudication").strip().lower()
    header, rows = _read_csv(data, file_name)
    if col not in header:
        raise Refusal(f"column {column} not found; the file has: {', '.join(h for h in header if h)}", 400, 1)
    try:
        t = _target(project, protocol, result_set)
    except KeyError as e:
        raise Refusal(str(e.args[0]) if e.args else str(e), 400, 1) from None
    if t is None or not t["row"]:
        raise Refusal(f"{NOTHING} for {protocol}", 400, 1)
    base, b = t["row"], t["set"]
    if rs.finished(project, base) is False:
        n_scope = len(rs._scope_ids(project, base["scope"]))
        missing = n_scope - int(st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (t["key"],)))
        raise Refusal(f"set {b} of {protocol} is not finished ({missing} of {n_scope} records have no decision); finish "
                      f"screening it first (wider screen protocol {protocol}), then import the adjudication", 400, 1)
    states, decisions = _state(project, t)
    hid = _hidden(project) & set(states)                    # devgate: open development sample, open validation reads
    titles = {r["record_id"]: r["title"] or "" for r in st.query("SELECT record_id, title FROM records")}

    # match: a record id the corpus knows (with a title check), else DOI or the exact title; never a similar title
    jw = project.config.protocol.identification.jw_confirm
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in
              st.query("SELECT record_id, title, doi FROM records")]
    by_id: dict[int, int] = {}
    to_locate = []
    for i, r in enumerate(rows):
        v = r.get("record_id", "")
        if v.isdigit() and int(v) in titles:
            by_id[i] = int(v)
        elif r.get(col):
            to_locate.append(i)
    located = dict(zip(to_locate, locate_benchmark([(str(i), rows[i].get("title", ""), rows[i].get("doi") or None)
                                                    for i in to_locate], corpus, jw))) if to_locate else {}

    n = {k: 0 for k in ("blank", *LEFT_OUT)}
    left: list[dict[str, Any]] = []
    first: dict[int, tuple[int, str]] = {}   # record id -> (row number, decision) of the row that counts
    used: list[dict[str, Any]] = []

    def leave(key: str, row_no: int, text: str, rid: int | None = None, **extra) -> None:
        n[key] += 1
        left.append({"row": row_no, "reason": key, "record_id": rid, "text": f"row {row_no}: {text}", **extra})

    for i, r in enumerate(rows):
        row_no = i + 2   # row 1 is the header
        raw = r.get(col, "")
        if not raw:
            n["blank"] += 1
            continue
        dec = ALIASES.get(raw.lower(), raw.lower())
        if dec not in DECISIONS:
            leave("unreadable", row_no, f'"{raw}" is not include, exclude or maybe')
            continue
        if i in by_id:
            rid = by_id[i]
            if r.get("title") and norm_title(r["title"]) != norm_title(titles[rid]):
                leave("title_differs", row_no, f"record {rid}'s title differs from this project's; the file may come "
                      "from another project", rid)
                continue
        else:
            m = located.get(i)
            if m is None or m.record_id is None:
                leave("unmatched", row_no, "no record of this project matches its record id, DOI or title")
                continue
            rid = int(m.record_id)
            if m.method not in ("doi", "title"):
                leave("similar_title", row_no, f'only a similar title matches record {rid} (similarity {m.score:.2f}): '
                      f'"{r.get("title", "")}" and "{titles[rid]}"; add its record_id to the row to use it', rid,
                      score=round(m.score, 2), file_title=r.get("title", ""), title=titles[rid])
                continue
        st_ = states.get(rid)
        if rid in hid:
            leave("hidden", row_no, f"record {rid}: {_hidden_text(project, len(hid))}; a review decision waits until "
                  "then", rid)
            continue
        if st_ and st_["failed"]:
            qid = f" {st_['question_id']}" if st_["question_id"] else " ID"
            leave("failed_read", row_no, f"record {rid} has a failed read; answer its question on the Questions page "
                  f"(wider questions answer{qid})", rid)
            continue
        if st_ and st_["failed_answered"]:
            leave("failed_answered", row_no, f"record {rid}: {answered_wait(st_['failed_by'])}", rid)
            continue
        if rid not in decisions:
            leave("not_in_set", row_no, f"record {rid} has no decision in set {b} of {protocol}", rid)
            continue
        if rid in first:
            kept_row, kept = first[rid]
            if dec != kept:
                leave("duplicate", row_no, f"record {rid} is {dec} here but {kept} in row {kept_row}; row {kept_row} "
                      "counts", rid, conflict=True)
            else:
                leave("duplicate", row_no, f"a second row for record {rid}; row {kept_row} counts", rid)
            continue
        first[rid] = (row_no, dec)
        used.append({"row": row_no, "record_id": rid, "decision": dec, "by": r.get("adjudicator", ""),
                     "note": r.get("note", "")})

    if not used:
        raise Refusal(f"no row of {file_name} has a usable decision in column {column}; nothing was imported", 400, 1)
    given = (adjudicator or "").strip()
    if any(not u["by"] for u in used) and not given:
        raise Refusal(NEED_NAME, 400, 2)
    for u in used:
        u["from_file"] = bool(u["by"])
        u["by"] = u["by"] or given
    try:
        for name in {u["by"] for u in used}:
            journal.check_name(name)
    except ValueError as e:
        raise Refusal(str(e), 400, 2) from None

    changed = [u for u in used if decisions[u["record_id"]]["decision"] != u["decision"]]
    replaced = sorted(u["record_id"] for u in used if decisions[u["record_id"]]["decided_by"] == "researcher")
    open_q = sorted(u["record_id"] for u in used if states[u["record_id"]]["split_question"])
    # an open question of a record the file leaves alone is answered into the base set only (risk-check R2)
    open_left = sorted({r for r, x in states.items() if x.get("split_question")} - hid - {u["record_id"] for u in used})
    prev = rs.active_set(project, protocol)
    prev_n = prev["result_set"] if prev else b
    new_n = rs.next_number(project, protocol)
    new_key = rs.set_key(protocol, new_n)
    digest = sha256_bytes(data)
    counts = {"imported": len(used), "changed": len(changed), "confirmed": len(used) - len(changed),
              "replaced_researcher": len(replaced), **n}
    seconds = [dict(r) for r in st.query("SELECT key FROM screen_result_sets WHERE source_protocol=? AND source_set=? "
                                         "ORDER BY key", (protocol, prev_n))]
    out: dict[str, Any] = {
        "protocol": protocol, "base_set": b, "base_key": t["key"], "result_set": new_n, "key": new_key, "file": file_name,
        "sha256": digest, "column": column, "dry_run": dry_run, "counts": counts, "left_out": left,
        "conflicts": [x for x in left if x.get("conflict")], "replaced_researcher": replaced, "open_questions": open_q,
        "open_questions_left": open_left,
        "out_of_step": [s["key"] for s in seconds] if prev_n != new_n else [], "previous_active": prev_n,
        "adjudicators": sorted({u["by"] for u in used}), "written": False}
    other = sorted(f"set {s} of {p}" for p, s in _file_sets(rows) if (p, s) != (protocol, str(b)) and p and s)
    out["other_set_line"] = (f"This file was exported from {', '.join(other)}; it was imported into set {b}"
                             + (" (the active set)" if prev_n == b else "") + ". Records whose decision changed between "
                             "the sets keep the adjudicator's decision.") if other else None
    out["lines"] = _lines(out)
    if dry_run:
        return out

    summary = (f"adjudicator decisions imported into {protocol} set {new_n} (file sha256 {digest[:12]}): "
               f"{counts['changed']} changed, {counts['confirmed']} confirmed")
    source = "page" if journal.AUTHOR.get() is not None else "option"
    with st.tx(), journal.recording(project, KIND, summary, author=author) as rec:
        rec.rows("screen_result_sets", "protocol=?", (protocol,))
        rec.rows("screen_decisions", "protocol=?", (new_key,))
        if rs.next_number(project, protocol) != new_n:   # another set was added since the count (risk-check R1)
            raise Refusal(f"another set of {protocol} was added while the file was read; nothing was imported; "
                          "import the file again", 409, 1)
        rs.new_set(project, protocol, "adjudication", settings=base.get("settings") or None,
                   label=f"adjudicated from set {b}", scope=base.get("scope"),
                   derived_from={"set": b, "kind": "adjudication", "changed": counts["changed"],
                                 "confirmed": counts["confirmed"]},
                   import_file=None, import_sha256=digest, author=author)
        st.execute("UPDATE screen_result_sets SET settings_hash=?, runs=?, role=?, mixed=?, source_protocol=?, "
                   "source_set=? WHERE key=?",
                   (base.get("settings_hash"), json.dumps(base.get("runs") or []), base.get("role"),
                    int(base.get("mixed") or 0), base.get("source_protocol"), base.get("source_set"), new_key))
        st.execute("INSERT INTO screen_decisions(protocol,record_id,decision,votes,cell,labels,reason,decided_at,"
                   "vote_calls,decided_by,origin) SELECT ?,record_id,decision,votes,cell,labels,reason,decided_at,"
                   "vote_calls,decided_by,origin FROM screen_decisions WHERE protocol=?", (new_key, t["key"]))
        for u in used:
            d = decisions[u["record_id"]]
            if u["from_file"]:
                researchers.register(project, u["by"], "adjudication file")
            else:
                researchers.register(project, u["by"], source)
            origin = {"kind": "adjudication", "before_review": d["decision"],
                      "adjudication": {"sha256": digest, "row": u["row"], "by": u["by"], "base_set": b,
                                       "base_origin": _origin(d["origin"])}}
            set_decision(project, new_key, u["record_id"], u["decision"],
                         votes=reads(_votes(d["votes"])) + [f"researcher:{u['decision']}"], cell=d["cell"],
                         labels=json.loads(d["labels"]) if d.get("labels") else None,
                         reason=f"researcher ({u['by']}) decided as adjudicator" + (f": {u['note']}" if u["note"] else ""),
                         decided_by="researcher", origin=origin)
        st.execute("UPDATE screen_result_sets SET active=(result_set=?) WHERE protocol=?", (new_n, protocol))
    out.update(written=True, change_id=journal.last_change_id())
    out["lines"] = _lines(out)
    return out


def _lines(res: dict[str, Any]) -> list[str]:
    """The result in the CLI's and the card's words: the count line, the rows left out, then what changed."""
    c, p, b, n = res["counts"], res["protocol"], res["base_set"], res["result_set"]
    left = sum(c[k] for k in LEFT_OUT)
    head = "Dry run, nothing written: " if res["dry_run"] else ""
    out = [f"{head}{res['file']}: {c['imported']} rows used ({c['changed']} changed, {c['confirmed']} confirmed), "
           f"{c['blank']} blank rows (those records keep their decision), {left} rows left out"]
    out += [x["text"] for x in res["left_out"]]
    if res["dry_run"]:
        out.append(f"Import would make set {n} of {p} active (adjudicated from set {b}); set {b} stays unchanged.")
    else:
        pv = res["previous_active"]
        out.append(f"Set {n} of {p} is now active (adjudicated from set {b}: {c['changed']} changed, {c['confirmed']} "
                   f"confirmed). Set {b} is unchanged; " + (f"wider screen sets use {p} {pv} makes it active again." if
                                                            pv == b else f"set {pv} was active before; wider screen "
                                                            f"sets use {p} {pv} makes it active again."))
    if res["replaced_researcher"]:
        rr = res["replaced_researcher"]
        out.append(f"Record{'s' if len(rr) > 1 else ''} {', '.join(map(str, rr))} had a researcher's decision in set {b}; "
                   f"the adjudicator's decision {'would replace' if res['dry_run'] else 'replaces'} it in set {n}.")
    out += [f"Record {rid} still has an open question in set {b}; it is settled in set {n}."
            for rid in res["open_questions"]]
    if res.get("open_questions_left"):
        ids = res["open_questions_left"]
        out.append(f"Record{'s' if len(ids) > 1 else ''} {', '.join(map(str, ids))} of set {b} still "
                   f"{'have' if len(ids) > 1 else 'has'} an open question the file does not decide; an answer changes "
                   f"set {b} only, so set {n} keeps maybe. Answer {'them' if len(ids) > 1 else 'it'} before importing, "
                   f"or decide {'them' if len(ids) > 1 else 'it'} in set {n} on the Review page.")
    pv = res["previous_active"]
    out += [f"Second screen {k} was built on set {pv} of {p}, which {'would no longer be' if res['dry_run'] else 'is no longer'} "
            f"active; its recall, report and export are refused until set {pv} is active again." for k in res["out_of_step"]]
    if res["other_set_line"]:
        out.append(res["other_set_line"])
    return out
