"""Reviewer labels on a sample: blind mode, second reviewer, adjudication, agreement, lock; CSV import/export."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow

DECISIONS = ("include", "exclude", "maybe")
RAYYAN_DECISION = {"included": "include", "excluded": "exclude", "maybe": "maybe", "include": "include",
                   "exclude": "exclude", "uncertain": "maybe"}


def add_reviewer(project: Project, reviewer_id: str, name: str | None = None) -> None:
    project.store.execute("INSERT OR IGNORE INTO reviewers(reviewer_id,name,created_at) VALUES(?,?,?)",
                          (reviewer_id, name or reviewer_id, utcnow()))


def reviewers(project: Project) -> list[dict]:
    return [dict(r) for r in project.store.query("SELECT * FROM reviewers ORDER BY created_at")]


def sample_by_name(project: Project, name: str) -> dict:
    r = project.store.one("SELECT * FROM samples WHERE name=?", (name,))
    if r is None:
        raise KeyError(f"no sample {name!r}")
    return dict(r)


def queue(project: Project, reviewer_id: str, sample_names: list[str] | None = None, only_unlabelled: bool = False,
          blind: bool = True, items: bool = True) -> list[dict]:
    """The shuffled queue for one reviewer. Blind: no rank, score, cell, band, pool or set; no other reviewer's decision.
    A blind row carries an opaque ``item`` in place of its ``sample_id`` (D6), unless ``items`` is false (the CSV
    export, which writes neither)."""
    return queue_left_out(project, reviewer_id, sample_names, only_unlabelled, blind, items)[0]


def queue_left_out(project: Project, reviewer_id: str, sample_names: list[str] | None = None,
                   only_unlabelled: bool = False, blind: bool = True, items: bool = True) -> tuple[list[dict], int]:
    """:func:`queue` and the number of records the reader has not labelled that a blind merged queue left out: a
    locked sample's records beside an open sample's, and sealed records beside records that are not sealed, as a
    change there needs a reason, which would tell how the record was drawn (label-completion-and-sealing D8)."""
    st = project.store
    samples = [sample_by_name(project, n) for n in sample_names] if sample_names else [dict(r) for r in st.query("SELECT * FROM samples")]
    rows = []
    for s in samples:
        sealed = sealed_ids(project, s)
        for r in st.query(
                "SELECT sr.queue_pos, sr.record_id, sr.band, sr.pool, sr.rank, m.title, m.year, m.journal, m.doi, m.abstract, m.authors, "
                "l.decision, l.reason, l.labels, l.note, l.updated_at FROM sample_rows sr JOIN records m ON m.record_id=sr.record_id "
                "LEFT JOIN labels l ON l.sample_id=sr.sample_id AND l.record_id=sr.record_id AND l.reviewer_id=? "
                "WHERE sr.sample_id=? ORDER BY sr.queue_pos", (reviewer_id, s["sample_id"])):
            d = dict(r)
            d["sample"] = s["name"]
            d["sample_id"] = s["sample_id"]
            d["locked"] = bool(s["locked"])   # for the page; a locked sample's labels stay open (D1)
            d["_guarded"] = d["locked"] or d["record_id"] in sealed
            if blind:
                for k in ("band", "pool", "rank"):
                    d.pop(k, None)
                d.pop("sample", None)
            if only_unlabelled and d["decision"]:
                continue
            rows.append(d)
    # a blind queue of several samples that mixes locked or sealed records with open ones leaves the former out: a
    # change there asks for a reason, which would tell the reviewer how the record was drawn (D8); a queue of one
    # sample keeps them
    left_out = 0
    if blind and len(samples) > 1 and len({d["_guarded"] for d in rows}) > 1:
        left_out = sum(1 for d in rows if d["_guarded"] and not d["decision"])
        rows = [d for d in rows if not d["_guarded"]]
    for d in rows:
        d.pop("_guarded")
        if blind and len(samples) > 1:   # the flag would tell which rows share a sample (risk-checker R2)
            d.pop("locked")
    # one merged queue in a stable interleaving by queue position
    rows.sort(key=lambda d: (d["queue_pos"], d["sample_id"]))
    if blind and items and reviewer_id:
        for d in rows:
            d["item"] = _token(project, d.pop("sample_id"), d["record_id"], "blind", reviewer_id)
    return rows, left_out


def criteria_state(project: Project, sample: dict) -> tuple | None:
    """The criteria state a label on a probability sample records (D11): (codebook_version, criteria_frozen,
    after_look). The version is the latest frozen codebook, else the latest approved, else None (a draft is never
    recorded); None for other samples."""
    if sample.get("kind") != "probability":
        return None
    from ..construct.artifacts import frozen_query, latest, latest_frozen
    lf = latest_frozen(project, "codebook")
    v = lf or latest(project, "codebook", "approved")
    fq = frozen_query(project, "codebook")
    frozen = int(bool(lf and fq and fq["version"] == lf["version"]))
    after = int(bool(project.store.scalar("SELECT COUNT(*) FROM validation_looks WHERE sample_id=?", (sample["sample_id"],))))
    return (v["version"] if v else None, frozen, after)


def criteria_version(project: Project) -> tuple:
    """The version part of :func:`criteria_state` for any sample: (codebook_version, criteria_frozen), the latest
    frozen codebook, else the latest approved (frozen 0), else (None, None)."""
    from ..construct.artifacts import frozen_query, latest, latest_frozen
    lf = latest_frozen(project, "codebook")
    v = lf or latest(project, "codebook", "approved")
    fq = frozen_query(project, "codebook")
    return (v["version"] if v else None, int(bool(lf and fq and fq["version"] == lf["version"])) if v else None)


def _save_state(project: Project, s: dict) -> tuple:
    """The criteria state an ordinary save writes: :func:`criteria_state` on a probability sample; on any other sample
    the codebook version only (reviewer-roles-and-blinding D2: a review package carries the version development
    labels used), with no frozen state and no look, so the counts of :func:`criteria_counts` stay as they were."""
    st = criteria_state(project, s)
    return st if st is not None else (criteria_version(project)[0], None, None)


def label(project: Project, sample_name: str, record_id: int, reviewer_id: str, decision: str, reason: str = "",
          labels: dict | None = None, note: str = "", recheck: bool = False, queue_mode: str | None = None,
          made_in: str = "blind queue", basis: str | None = None, correction_reason: str | None = None) -> None:
    """Save a reviewer's label. An ordinary save over an existing label keeps its first criteria state (D11); a
    re-check save (``recheck``) records the frozen state, ``recheck_from`` and a ``label_revisions`` row. A save from
    a queue item (``queue_mode``) journals neither the sample nor the record (D12). The label records how it was made
    (``made_in``, label-completion-and-sealing D11: ``blind queue``, ``unblinded queue`` or ``file import``; a save
    over a label that is not independent never makes it independent, see :func:`_save_value`) and the abstract version
    it was saved on. ``basis`` (an import's ``--independent``) states the new label's independence in the same change.
    On a locked sample or a sealed record a change to a stored label is a correction (label-completion-and-sealing D2):
    it needs ``correction_reason`` (else :class:`CorrectionNeeded`, before anything is written), an equal save writes
    nothing, and a re-check save is exempt."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    if made_in not in SAVE_VALUES:
        raise ValueError(f"made_in must be one of {SAVE_VALUES}")
    s = sample_by_name(project, sample_name)
    from ..core.journal import check_name
    check_name(reviewer_id.strip())
    old = project.store.one("SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                            (s["sample_id"], record_id, reviewer_id))
    guarded = _guarded(project, s, record_id)
    if guarded and old is not None and _equal(old, decision, reason, labels, note):
        return   # an equal save on a locked sample or a sealed record writes nothing (D2)
    why = None
    if guarded and old is not None and not recheck:
        why = _correction_why(correction_reason, _correction_text(s, record_id, queue_mode))
    add_reviewer(project, reviewer_id)
    from ..core.journal import MAX_AUTHOR, recording
    # the summary leaves the decision out: the history is visible to every reviewer, labelling may be blind;
    # the name in it is cut to 80 characters like the entry's signature
    who = reviewer_id[:MAX_AUTHOR]
    summary = f"{who} {SAVE_SUMMARY[queue_mode].format(record_id=record_id)}" if queue_mode else f"{who} labelled record {record_id} in sample {sample_name}"
    # a blind save keeps its record id: the reviewer's own queue row already shows the record (D6)
    hide = queue_mode in ("recheck", "second")
    kind = "label"
    if why:   # a correction from any queue item names neither sample nor record (D2, round 3 F9)
        kind, hide = "label_correction", hide or bool(queue_mode)
        summary = f"{who} corrected a label" if queue_mode else f"{who} corrected record {record_id} in sample {sample_name}"
    value = _save_value(project, s, record_id, reviewer_id, made_in)
    with recording(project, kind, summary, author=reviewer_id, record_id=None if hide else record_id) as rec:
        rec.rows("labels", "sample_id=? AND record_id=? AND reviewer_id=?", (s["sample_id"], record_id, reviewer_id))
        _guard_items(rec, s, queue_mode)
        if recheck:
            _recheck_save(project, s, record_id, reviewer_id, decision, reason, labels, note, rec, made_in=value)
            return
        if why:
            _correction_row(project, rec, s, record_id, "label", reviewer_id, old, decision, reason, labels, note, why,
                            reviewer_id)
            _follow_reference(project, rec, s, record_id, reviewer_id, decision, reason, labels, reviewer_id)
        _save_label(project, s, record_id, reviewer_id, decision, reason, labels, note, rec, made_in=value)
        if basis:
            _state_basis(project, s["sample_id"], record_id, reviewer_id, basis, reviewer_id)


# ----------------------------------------------------------------- corrections (label-completion-and-sealing D2)
MAX_CORRECTION = 500
BLIND_CORRECTION = "a change to a saved label here needs a reason for the correction"


class CorrectionNeeded(ValueError):
    """A change to a stored label or adjudication on a locked sample or a sealed record, without a reason (D2)."""


def _sealed(project: Project, s: dict, record_id: int) -> bool:
    """Whether ``record_id`` is sealed on the probability sample ``s`` (D5)."""
    return s.get("kind") == "probability" and record_id in sealed_ids(project, s)


def sealed_ids(project: Project, s: dict) -> set[int]:
    """The sealed records of a probability sample (D5); an empty set for other samples."""
    return set(sealed_records(project, s))


def sealed_records(project: Project, s: dict) -> dict[int, dict]:
    """The seal rows of a probability sample by record id (D5); an empty dict for other samples. An older sample
    without seal rows (looks recorded before seals were, or the project's ``recall_looks_before_v7``) gets them here
    first, through :func:`_seal_older`."""
    if s.get("kind") != "probability":
        return {}
    st = project.store
    rows = st.query("SELECT * FROM label_seals WHERE sample_id=?", (s["sample_id"],))
    if not rows:
        _seal_older(project, s)
        rows = st.query("SELECT * FROM label_seals WHERE sample_id=?", (s["sample_id"],))
    return {r["record_id"]: dict(r) for r in rows}


def _record_changes(project: Project, sample_id: int) -> dict[int, int]:
    """The earliest History change id per record of a sample on its label tables, tied by the params of the ``rows``
    items (``sample_id``, ``record_id``), never by ``history.record_id``, which blind saves leave empty (D5)."""
    from ..core.journal import LOCK_TABLES
    out: dict[int, int] = {}
    for h in project.store.query("SELECT change_id, items FROM history WHERE items LIKE '%\"rows\"%' ORDER BY change_id"):
        for it in json.loads(h["items"]):
            if it.get("t") != "rows" or it.get("table") not in LOCK_TABLES:
                continue
            ps = it.get("params") or []
            if (len(ps) > 1 and it.get("where", "").startswith("sample_id=? AND record_id=?") and ps[0] == sample_id
                    and isinstance(ps[1], int)):
                out.setdefault(ps[1], h["change_id"])
    return out


def _change_at(project: Project, at: str) -> int:
    """The largest History change id at or before the time ``at`` (0 when none)."""
    return int(project.store.scalar("SELECT COALESCE(MAX(change_id),0) FROM history WHERE at<=?", (at,)))


def _seal_older(project: Project, s: dict) -> None:
    """Seal rows for a sample whose looks came before seals were written (D5, Q4). "yes" (a recorded look): the first
    change id is the sample's first ``sample_look`` History entry, ``c`` the last change at or before its latest look;
    "maybe" (looks taken before they were recorded): ``c`` is the last change at or before that time, for a sample
    created by then. Sealed are the records with a final label and no open disagreement whose label tables History
    first touched at or before ``c``, or never (a sample import, labels older than History). Written outside History."""
    st, sid = project.store, s["sample_id"]
    looks = st.one("SELECT MAX(last_at) AS a, MAX(n) AS n FROM validation_looks WHERE sample_id=?", (sid,))
    if looks and looks["n"]:
        first = st.one("SELECT MIN(first_at) AS a FROM validation_looks WHERE sample_id=? AND n>0", (sid,))["a"]
        latest = max(x for x in (looks["a"], st.scalar(
            "SELECT MAX(first_at) FROM validation_look_states WHERE sample_id=?", (sid,))) if x)
        c = _change_at(project, latest)
        fc = next((h["change_id"] for h in st.query("SELECT change_id, items FROM history WHERE kind='sample_look' "
                                                     "ORDER BY change_id")
                   if any(i.get("sample") == s["name"] for i in json.loads(h["items"]))), c)
        at, look = first or latest, 1
        last_look = st.scalar("SELECT MAX(look_index) FROM validation_look_states WHERE sample_id=?", (sid,)) or 1
    else:
        at = st.get_meta("recall_looks_before_v7")
        if at is None or (s.get("created_at") or "") > at:
            return
        c = fc = _change_at(project, at)
        look = last_look = None
    touched = _record_changes(project, sid)
    rows = [r for r in _final_rows(project, s, None) if r["decision"] is not None and not r["open_disagreement"]
            and touched.get(r["record_id"], 0) <= c]
    st.executemany(
        "INSERT OR IGNORE INTO label_seals(sample_id,record_id,first_look,sealed_at,first_change_id,last_look,"
        "last_change_id,decision,reason,labels,source,sealed_decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(sid, r["record_id"], look, at, fc, last_look, c, r["decision"], r["reason"], json.dumps(r["labels"]),
          r["source"], r["decision"]) for r in rows])


def seal_look(project: Project, sample_name: str, record_ids, look: int, primary: str | None = None) -> None:
    """At a look (D5): a seal row, with the record's final label as its reference, for each of ``record_ids`` without
    one; every seal row of the sample takes this look and change id as its last. Written outside History, so no undo
    removes a seal and an unlock keeps it. The change id is the largest in History now."""
    s = sample_by_name(project, sample_name)
    if s.get("kind") != "probability":
        return
    st, sid = project.store, s["sample_id"]
    have = set(sealed_records(project, s))
    c = int(st.scalar("SELECT COALESCE(MAX(change_id),0) FROM history"))
    want = set(record_ids) - have
    now = utcnow()
    st.executemany(
        "INSERT OR IGNORE INTO label_seals(sample_id,record_id,first_look,sealed_at,first_change_id,last_look,"
        "last_change_id,decision,reason,labels,source,sealed_decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [(sid, r["record_id"], look, now, c, look, c, r["decision"], r["reason"], json.dumps(r["labels"]), r["source"],
          r["decision"]) for r in _final_rows(project, s, primary) if r["record_id"] in want and r["decision"]])
    st.execute("UPDATE label_seals SET last_look=?, last_change_id=? WHERE sample_id=?", (look, c, sid))


def _guarded(project: Project, s: dict, record_id: int) -> bool:
    """A change here is a correction: the sample is locked or the record sealed (D2)."""
    return bool(s["locked"]) or _sealed(project, s, record_id)


def _equal(old, decision: str, reason: str, labels: dict | None, note: str) -> bool:
    """A save equal to the stored row (decision, reason, ticks, note), which on a locked sample writes nothing (D2)."""
    try:
        ticks = json.loads(old["labels"] or "{}")
    except ValueError:
        ticks = old["labels"]
    return (old["decision"] == decision and (old["reason"] or "") == (reason or "") and ticks == (labels or {})
            and (old["note"] or "") == (note or ""))


def _correction_text(s: dict, record_id: int, queue_mode: str | None) -> str:
    """The refusal of a change without a reason; a blind save names no sample (D2)."""
    if queue_mode:
        return BLIND_CORRECTION
    where = f"sample {s['name']!r} is locked" if s["locked"] else f"record {record_id} of {s['name']} is sealed"
    return f"{where}: a change to a saved label needs a reason for the correction"


def _correction_why(reason: str | None, refusal: str) -> str:
    """The cleaned correction reason, or :class:`CorrectionNeeded` with ``refusal``."""
    why = (reason or "").strip()
    if not why:
        raise CorrectionNeeded(refusal)
    if len(why) > MAX_CORRECTION:
        raise ValueError(f"a correction reason is at most {MAX_CORRECTION} characters")
    return why


def needs_correction(project: Project, s: dict, record_id: int, reviewer_id: str, decision: str, reason: str = "",
                     labels: dict | None = None, note: str = "") -> bool:
    """Whether saving this label is a correction (a change on a locked sample or a sealed record), so a file import
    can refuse before it saves any row."""
    if not _guarded(project, s, record_id):
        return False
    old = project.store.one("SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                            (s["sample_id"], record_id, reviewer_id))
    return old is not None and not _equal(old, decision, reason, labels, note)


def _guard_items(rec, s: dict, queue_mode: str | None) -> None:
    """The History items a save adds (D2, D7): ``while_locked`` on a locked sample, ``blind`` from a blind queue item."""
    if s["locked"]:
        rec.extra({"t": "while_locked"})
    if queue_mode:
        rec.extra({"t": "blind"})


def _correction_row(project: Project, rec, s: dict, record_id: int, target: str, reviewer: str | None, old, decision: str,
                    reason: str, labels: dict | None, note: str, why: str, author: str) -> None:
    """One ``label_corrections`` row inside the save's recording, so an undo removes it with the save (D2)."""
    rec.rows("label_corrections", "sample_id=? AND record_id=? AND target=? AND reviewer IS ?",
             (s["sample_id"], record_id, target, reviewer))
    project.store.execute(
        "INSERT INTO label_corrections(sample_id,record_id,target,reviewer,old_decision,old_reason,old_labels,old_note,"
        "old_by,new_decision,new_reason,new_labels,new_note,why,author,at,locked,sealed) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (s["sample_id"], record_id, target, reviewer, old["decision"], old["reason"], old["labels"], old["note"],
         old["decided_by"] if target == "adjudication" else reviewer, decision, reason, json.dumps(labels or {}), note,
         why, author, utcnow(), int(bool(s["locked"])), int(_sealed(project, s, record_id))))


def _follow_reference(project: Project, rec, s: dict, record_id: int, reviewer: str | None, decision: str, reason: str,
                      labels: dict | None, by: str) -> None:
    """A correction of a sealed record's reference (D5): an adjudication (``reviewer`` None) always, a reviewer's
    label when it is the reference's ``source``. The reference follows inside the correction's recording, so an undo
    of the correction brings the earlier reference back; ``sealed_decision`` keeps the decision first sealed."""
    ref = sealed_records(project, s).get(record_id)
    if ref is None or (reviewer is not None and ref["source"] != reviewer):
        return
    rec.rows("label_seals", "sample_id=? AND record_id=?", (s["sample_id"], record_id))
    project.store.execute(
        "UPDATE label_seals SET decision=?, reason=?, labels=?, source=?, corrected_at=?, corrected_by=? "
        "WHERE sample_id=? AND record_id=?",
        (decision, reason, json.dumps(labels or {}), reviewer or "adjudication", utcnow(), by, s["sample_id"],
         record_id))


def sealed_change_line(project: Project, sample_name: str | None) -> str | None:
    """The line of D6: how many sealed records' reference decision now differs from the decision first sealed (only a
    documented correction can cause it), with the earliest seal date; None when none does. Counts and a date only."""
    r = project.store.one("SELECT * FROM samples WHERE name=?", (sample_name,)) if sample_name else None
    seals = sealed_records(project, dict(r)) if r else {}
    n = sum(r["decision"] != r["sealed_decision"] for r in seals.values())
    if not n:
        return None
    day = str(min(r["sealed_at"] for r in seals.values() if r["sealed_at"]))[:10]
    rec = f"1 record of {sample_name} had its labels" if n == 1 else f"{n} records of {sample_name} had their labels"
    return (f"{rec} changed after its recall was first revealed on {day}; results revealed before then used the "
            f"earlier labels (wider label revisions {sample_name}).")


def corrected_since(project: Project, sample_name: str | None) -> int:
    """How many sealed records' references were corrected since the latest look that used them (a ``label_seals``
    change in History above the seal's ``last_change_id``, not undone; D6). Change ids, not times: a correction in the
    same second as the look still counts."""
    s = project.store.one("SELECT * FROM samples WHERE name=?", (sample_name,)) if sample_name else None
    seals = sealed_records(project, dict(s)) if s else {}
    todo = {rid: r["last_change_id"] or 0 for rid, r in seals.items() if r["corrected_at"]}
    if not todo:
        return 0
    found = set()
    for h in project.store.query("SELECT change_id, items FROM history WHERE change_id>? AND items LIKE '%label_seals%'",
                                 (min(todo.values()),)):
        for it in json.loads(h["items"]):
            ps = it.get("params") or []
            if (it.get("t") == "rows" and it.get("table") == "label_seals" and len(ps) > 1 and ps[0] == s["sample_id"]
                    and ps[1] in todo and h["change_id"] > todo[ps[1]]):
                found.add(ps[1])
    return len(found)


_COMPARED = ("decision", "reason", "labels", "note")


def _label_key(table: str, row: dict) -> tuple:
    return (row["record_id"], table, row.get("reviewer_id"))


def changed_after(project: Project, s: dict, at: str | None) -> int:
    """How many records of sample ``s`` have labels or an adjudication that differ, in decision, reason, ticks or note,
    from what they were at ``at`` (D6, a net difference): for each label and adjudication, the before-image of the
    earliest History change strictly after ``at`` that touched it, against its current row. So an equal re-save, an
    undone change and a redo chain that ends where it began are not counted."""
    if not at:
        return 0
    sid = s["sample_id"]
    before: dict[tuple, dict | None] = {}
    for h in project.store.query("SELECT items FROM history WHERE at>? AND (items LIKE '%\"labels\"%' OR "
                                 "items LIKE '%\"adjudications\"%') ORDER BY change_id", (at,)):
        for it in json.loads(h["items"]):
            if it.get("t") != "rows" or it.get("table") not in ("labels", "adjudications"):
                continue
            old = {_label_key(it["table"], r): r for r in it.get("before") or [] if r.get("sample_id") == sid}
            for r in [*(it.get("before") or []), *(it.get("after") or [])]:
                if r.get("sample_id") == sid:
                    before.setdefault(_label_key(it["table"], r), old.get(_label_key(it["table"], r)))
    if not before:
        return 0
    now = {_label_key("labels", dict(r)): dict(r)
           for r in project.store.query("SELECT * FROM labels WHERE sample_id=?", (sid,))}
    now.update({_label_key("adjudications", dict(r)): dict(r)
                for r in project.store.query("SELECT * FROM adjudications WHERE sample_id=?", (sid,))})

    def seen(r):
        return None if r is None else tuple(r.get(k) or "" for k in _COMPARED)

    return len({k[0] for k, old in before.items() if seen(old) != seen(now.get(k))})


def changed_after_line(project: Project, sample_name: str, at: str | None, what: str, tail: bool) -> str | None:
    """The development line of D6, or None: ``what`` is "run R" or "this comparison"; ``tail`` False drops the
    "uses the current labels" part beside the unlocked sentences."""
    try:
        s = sample_by_name(project, sample_name)
    except KeyError:
        return None
    if s["kind"] == "probability":
        return None
    n = changed_after(project, s, at)
    if not n:
        return None
    head = (f"{n} record{'' if n == 1 else 's'} of {sample_name} had {'its' if n == 1 else 'their'} labels changed "
            f"after {what} ran ({at[:10]})")
    if not tail:
        return head + "."
    return head + ("; this evaluation uses the current labels." if what.startswith("run ")
                   else "; its figures use the current labels.")


def corrections(project: Project, sample_name: str) -> list[dict]:
    """The corrections of a sample's labels and adjudications, oldest first (D2)."""
    s = sample_by_name(project, sample_name)
    return [dict(r) for r in project.store.query(
        "SELECT * FROM label_corrections WHERE sample_id=? ORDER BY correction_id", (s["sample_id"],))]


# ----------------------------------------------------------------- label provenance (label-completion-and-sealing D11)
# what a save may name: the blind queues (the default workflow), the page's unblinded queue, a label file
SAVE_VALUES = ("blind queue", "unblinded queue", "file import")
# values a stated basis may cover (``wider``: every main-queue label saved before provenance was recorded)
DECLARABLE = ("wider", "file import", "sample import")
MAX_BASIS = 500


def _basis(project: Project, sample_id: int, record_id: int, reviewer_id: str) -> str | None:
    return project.store.scalar("SELECT basis FROM label_independence WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                                (sample_id, record_id, reviewer_id))


def _independent(project: Project, lab: dict, basis: bool | None = None) -> bool:
    """Whether a label (a ``labels`` row) is independent of model output: made in a blind queue, or in a review
    package on an item that is not a repeat, or on a repeat item whose replaced label was independent, or any value
    but ``unblinded queue`` with a stated basis. ``basis`` replaces the lookup of a stated basis (for a replaced label
    read from History, whose basis row is in the same before-image)."""
    m = lab.get("made_in") or "wider"
    if m == "unblinded queue":
        return False
    if m == "blind queue":
        return True
    if basis is None:
        basis = _basis(project, lab["sample_id"], lab["record_id"], lab["reviewer_id"]) is not None
    if basis:
        return True
    if m.startswith("p-"):
        rep = project.store.scalar("SELECT repeat FROM review_package_items WHERE package_id=? AND sample_id=? AND "
                                   "record_id=?", (m, lab["sample_id"], lab["record_id"]))
        if not rep:
            return True
        from .package import _replaced
        old = _replaced(project, m, lab["sample_id"], lab["record_id"], lab["reviewer_id"])
        # a repeat review does not restore independence: only a replaced label that was independent keeps it
        return old is not None and (old["made_in"] or "wider") != m and _independent(project, old, old["_basis"])
    return False


def _save_value(project: Project, s: dict, record_id: int, reviewer_id: str, wanted: str) -> str:
    """The ``made_in`` a save writes (D11): what the caller names, except that a blind or re-check save over a stored
    label of the same reviewer that is not independent keeps a value that is not independent either (the stored one,
    or over a repeat package label the value of the label it replaced). Only another reviewer's blind or package
    label, or a stated basis, makes a record independent."""
    if wanted != "blind queue":
        return wanted   # neither ``unblinded queue`` nor ``file import`` is independent
    old = project.store.one("SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                            (s["sample_id"], record_id, reviewer_id))
    if old is None or _independent(project, dict(old)):
        return wanted
    m = old["made_in"] or "wider"
    if m.startswith("p-"):
        from .package import _replaced
        r = _replaced(project, m, s["sample_id"], record_id, reviewer_id)
        m = (r["made_in"] or "wider") if r is not None else "wider"
        return "wider" if m.startswith("p-") or m == "blind queue" else m
    return m


def _drop_basis(project: Project, s: dict, record_id: int, reviewer_id: str, rec) -> None:
    """Every save of a label removes its stated basis in the same recording: a basis never outlives the label it
    was stated for."""
    where, params = "sample_id=? AND record_id=? AND reviewer_id=?", (s["sample_id"], record_id, reviewer_id)
    rec.rows("label_independence", where, params)
    project.store.execute(f"DELETE FROM label_independence WHERE {where}", params)


def _state_basis(project: Project, sample_id: int, record_id: int, reviewer_id: str, basis: str, by: str) -> None:
    project.store.execute(
        "INSERT INTO label_independence(sample_id,record_id,reviewer_id,basis,by,at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(sample_id,record_id,reviewer_id) DO UPDATE SET basis=excluded.basis, by=excluded.by, at=excluded.at",
        (sample_id, record_id, reviewer_id, basis, by, utcnow()))


def _clean_basis(basis: str | None) -> str:
    b = " ".join((basis or "").split())
    if not b:
        raise ValueError("give the basis of independence: how these labels were made without seeing the model's "
                         "output (for example: screened before the ranking existed)")
    if len(b) > MAX_BASIS:
        raise ValueError(f"the basis is {len(b)} characters; keep it to {MAX_BASIS}")
    return b


def declare_independent(project: Project, sample_name: str, reviewer_id: str, basis: str, by: str | None = None) -> dict:
    """State that ``reviewer_id``'s labels on the sample were made independently of model output (D11, owner Q8):
    labels imported or saved before provenance was recorded (``wider``); never ``unblinded queue`` labels, and labels
    already independent need nothing. One journalled, undoable change of kind ``label_provenance``; the twin of
    ``wider label provenance`` and ``POST /api/labels/independence``."""
    from ..core.journal import MAX_AUTHOR, check_name, current_author, last_change_id, recording
    s = sample_by_name(project, sample_name)
    reviewer_id = (reviewer_id or "").strip()
    if not reviewer_id:
        raise ValueError("give the reviewer whose labels are declared independent")
    b = _clean_basis(basis)
    who = current_author(by)
    check_name(who)
    rows = [dict(r) for r in project.store.query(
        "SELECT * FROM labels WHERE sample_id=? AND reviewer_id=? ORDER BY record_id", (s["sample_id"], reviewer_id))]
    if not rows:
        raise ValueError(f"{reviewer_id} has no labels on {s['name']}")
    todo = [r for r in rows if (r["made_in"] or "wider") in DECLARABLE and not _independent(project, r)]
    unblinded = sum(1 for r in rows if r["made_in"] == "unblinded queue")
    lines = []
    if todo:
        summary = (f"{who[:MAX_AUTHOR]} declared {len(todo)} label{'s' if len(todo) != 1 else ''} of "
                   f"{reviewer_id[:MAX_AUTHOR]} on {s['name']} independent")
        with recording(project, "label_provenance", summary, author=who) as rec:
            rec.rows("label_independence", "sample_id=? AND reviewer_id=?", (s["sample_id"], reviewer_id))
            for r in todo:
                _state_basis(project, s["sample_id"], r["record_id"], reviewer_id, b, who)
        lines.append(f"{len(todo)} label{'s' if len(todo) != 1 else ''} of {reviewer_id} on {s['name']} now count as "
                     f"independent: {b}")
    else:
        lines.append(f"no label of {reviewer_id} on {s['name']} needs a declaration")
    if unblinded:
        lines.append(f"{unblinded} label{'s were' if unblinded != 1 else ' was'} made in the unblinded queue, which "
                     f"shows the model's band and rank; {'they stay' if unblinded != 1 else 'it stays'} not independent")
    return {"sample": s["name"], "reviewer": reviewer_id, "declared": len(todo), "unblinded": unblinded,
            "lines": lines, "change_id": last_change_id() if todo else None}


def provenance(project: Project, sample_name: str) -> dict:
    """Counts of the sample's labels by ``made_in`` (package ids counted as ``review package``) and by independence
    (D11), for ``wider sample status --json`` and ``GET /api/samples``."""
    s = sample_by_name(project, sample_name)
    by: Counter = Counter()
    undeclared: Counter = Counter()
    ind = 0
    rows = [dict(r) for r in project.store.query("SELECT * FROM labels WHERE sample_id=?", (s["sample_id"],))]
    for r in rows:
        m = r["made_in"] or "wider"
        by["review package" if m.startswith("p-") else m] += 1
        one = _independent(project, r)
        ind += one
        if not one and m in DECLARABLE and s["kind"] == "probability":
            undeclared[r["reviewer_id"]] += 1
    # ``undeclared``: per reviewer, the labels a declaration would make independent (the card's "Declare labels
    # independent" line, D9); an unblinded-queue label cannot be declared and is not counted
    return {"by_made_in": dict(sorted(by.items())), "independent": ind, "not_independent": len(rows) - ind,
            "undeclared": dict(sorted(undeclared.items())),
            "declared": project.store.scalar("SELECT COUNT(*) FROM label_independence WHERE sample_id=?",
                                             (s["sample_id"],))}


def _save_label(project: Project, s: dict, record_id: int, reviewer_id: str, decision: str, reason: str,
                labels: dict | None, note: str, rec, made_in: str = "blind queue", state: tuple | None = None,
                abstract_version: int | None = None, tags: list | None = None) -> None:
    """The write of one label inside the caller's recording ``rec`` (``label()`` and a package import share it). An
    ordinary save keeps the first codebook version and frozen state over an existing label (D11); a save with
    ``state`` (a package import) writes the package's state. ``abstract_version`` defaults to the current version;
    ``tags`` (a list) replaces the reviewer's tags, None keeps them. The label's stated basis of independence goes
    with the save (D11)."""
    from ..enrich.abstracts import current_version
    _drop_basis(project, s, record_id, reviewer_id, rec)
    keep = state is None
    state = state or _save_state(project, s)
    av = abstract_version if abstract_version is not None else current_version(project.store, record_id)
    project.store.execute(
        "INSERT INTO labels(sample_id,record_id,reviewer_id,decision,reason,labels,note,updated_at,"
        "codebook_version,criteria_frozen,after_look,made_in,abstract_version,tags) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(sample_id,record_id,reviewer_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason, "
        "labels=excluded.labels, note=excluded.note, updated_at=excluded.updated_at, after_look=excluded.after_look, "
        "made_in=excluded.made_in, abstract_version=excluded.abstract_version, tags=COALESCE(excluded.tags, labels.tags)"
        + ("" if keep else ", codebook_version=excluded.codebook_version, criteria_frozen=excluded.criteria_frozen"),
        (s["sample_id"], record_id, reviewer_id, decision, reason, json.dumps(labels or {}), note, utcnow(),
         *state, made_in, av, json.dumps(tags) if tags is not None else None))


def _recheck_save(project: Project, s: dict, record_id: int, reviewer_id: str, decision: str, reason: str,
                  labels: dict | None, note: str, rec, made_in: str = "blind queue") -> None:
    """A save from the re-check queue (D11, D12): the new criteria state, ``recheck_from`` the earlier decision, and
    the original and revised label with both codebook versions in ``label_revisions``, inside the same recording."""
    old = project.store.one("SELECT * FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                            (s["sample_id"], record_id, reviewer_id))
    if old is None:
        raise ValueError("nothing to re-check: this reviewer has no label on the record")
    state = criteria_state(project, s) or (None, None, None)
    a = latest_assessment(project, s["name"])
    note = note or old["note"] or ""   # the blind queue never shows the note, so an empty one keeps it
    rec.rows("label_revisions", "label_id=?", (old["label_id"],))
    _drop_basis(project, s, record_id, reviewer_id, rec)
    from ..enrich.abstracts import current_version
    project.store.execute(
        "UPDATE labels SET decision=?, reason=?, labels=?, note=?, updated_at=?, codebook_version=?, criteria_frozen=1, "
        "after_look=?, recheck_from=?, made_in=?, abstract_version=? WHERE label_id=?",
        (decision, reason, json.dumps(labels or {}), note, utcnow(), state[0], state[2],
         old["recheck_from"] if old["recheck_from"] is not None else old["decision"], made_in,
         current_version(project.store, record_id), old["label_id"]))
    project.store.execute(
        "INSERT INTO label_revisions(label_id,sample_id,record_id,reviewer,old_decision,old_reason,old_labels,"
        "old_codebook_version,new_decision,new_reason,new_labels,new_codebook_version,assessment_id,at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (old["label_id"], s["sample_id"], record_id, reviewer_id, old["decision"], old["reason"], old["labels"],
         old["codebook_version"], decision, reason, json.dumps(labels or {}), state[0],
         a["assessment_id"] if a else None, utcnow()))


def progress(project: Project, sample_name: str) -> dict:
    s = sample_by_name(project, sample_name)
    st = project.store
    out = {"sample": sample_name, "n": s["n"], "locked": bool(s["locked"]), "reviewers": {}}
    for r in st.query("SELECT reviewer_id, decision, COUNT(*) n FROM labels WHERE sample_id=? GROUP BY reviewer_id, decision", (s["sample_id"],)):
        out["reviewers"].setdefault(r["reviewer_id"], {})[r["decision"]] = r["n"]
    for rid, d in out["reviewers"].items():
        d["done"] = sum(d.values())
    out["adjudicated"] = st.scalar("SELECT COUNT(*) FROM adjudications WHERE sample_id=?", (s["sample_id"],))
    return out


def criteria_counts(project: Project, sample_name: str) -> dict:
    """The labels of a sample by criteria state (D11): before the criteria were frozen (``criteria_frozen`` 0, a
    version other than the latest frozen one, or a re-check pending), after, not recorded (saved before this
    version), saved after a look, and the lines that say so."""
    s = sample_by_name(project, sample_name)
    from ..construct.artifacts import latest_frozen
    lf = latest_frozen(project, "codebook")
    fv = lf["version"] if lf else None
    rows = [dict(r) for r in project.store.query(
        "SELECT codebook_version, criteria_frozen, after_look, recheck_from FROM labels WHERE sample_id=?", (s["sample_id"],))]
    out = {"n": len(rows), "before": 0, "after": 0, "not_recorded": 0, "after_look": 0, "rechecked": 0, "changed": None,
           "lines": []}
    early = Counter()
    for r in rows:
        out["after_look"] += bool(r["after_look"])
        out["rechecked"] += r["recheck_from"] is not None
        if r["criteria_frozen"] is None:
            out["not_recorded"] += 1
        elif not r["criteria_frozen"] or r["codebook_version"] != fv or r["recheck_from"] is not None:
            out["before"] += 1
            if r["criteria_frozen"] and r["codebook_version"] is not None and r["codebook_version"] != fv:
                early[r["codebook_version"]] += 1
        else:
            out["after"] += 1
    if not rows:
        return out
    recorded = out["before"] + out["after"]
    if recorded:
        line = f"{recorded} labels: {out['before']} before the criteria were frozen, {out['after']} after"
        if early and fv is not None:
            va = min(early)
            out["changed"] = {"n": sum(early.values()), "from": va, "to": fv}
            line += f"; the criteria changed after {sum(early.values())} labels (v{va} to v{fv})"
        out["lines"].append(line)
    if out["after_look"]:
        out["lines"].append(f"{out['after_look']} labels were saved after a recall result on this sample was seen")
    if out["not_recorded"]:
        out["lines"].append(f"{out['not_recorded']} labels: criteria state not recorded (saved before this version)")
    return out


def agreement(project: Project, sample_name: str, a: str, b: str, only_made_in: str | None = None,
              repeats: bool = True) -> dict:
    """Cohen's kappa between two reviewers on records both labelled (3 categories). Labels a review package's
    ``exclude`` choice leaves out are not counted (n, kappa, table, disagreements) and are returned as ``left_out``
    {n, packages, line} (reviewer-roles-and-blinding D7). Each disagreement carries ``adjudicated`` ({record_id,
    decision, decided_by} or None); ``hints`` (when any) are :func:`reviewer_hints`' lines. ``only_made_in`` keeps only ``b``'s labels made in that
    package, and ``repeats`` false also drops its re-reviewed items (the blinded-review agreement)."""
    from .package import left_out, left_out_line
    s = sample_by_name(project, sample_name)
    st = project.store
    out_ids = left_out(project, s["sample_id"])
    hints = reviewer_hints(project, sample_name, [a, b])   # a mistyped name says so (case, spelling)
    rows = {who: [dict(r) for r in st.query("SELECT label_id, record_id, decision, made_in FROM labels WHERE sample_id=? "
                                            "AND reviewer_id=?", (s["sample_id"], who))] for who in (a, b)}
    dropped = [r for who in (a, b) for r in rows[who] if r["label_id"] in out_ids]
    la = {r["record_id"]: r["decision"] for r in rows[a] if r["label_id"] not in out_ids}
    keep_b = [r for r in rows[b] if r["label_id"] not in out_ids and (only_made_in is None or r["made_in"] == only_made_in)]
    if only_made_in is not None and not repeats:
        reps = {r[0] for r in st.query("SELECT record_id FROM review_package_items WHERE package_id=? AND sample_id=? "
                                       "AND repeat=1", (only_made_in, s["sample_id"]))}
        keep_b = [r for r in keep_b if r["record_id"] not in reps]
    lb = {r["record_id"]: r["decision"] for r in keep_b}
    common = sorted(set(la) & set(lb))
    n = len(common)
    extra: dict = {"hints": hints} if hints else {}
    if dropped:
        pk = sorted({out_ids[r["label_id"]] for r in dropped})
        extra["left_out"] = {"n": len(dropped), "packages": pk, "line": left_out_line(len(dropped), pk)}
    if n == 0:
        return {"n": 0, "kappa": None, "agreement": None, "disagreements": [], **extra}
    table = Counter((la[r], lb[r]) for r in common)
    po = sum(v for (x, y), v in table.items() if x == y) / n
    pa = Counter(la[r] for r in common)
    pb = Counter(lb[r] for r in common)
    pe = sum(pa[c] * pb[c] for c in DECISIONS) / (n * n)
    kappa = (po - pe) / (1 - pe) if pe < 1 else 1.0
    adj = {r["record_id"]: dict(r) for r in st.query("SELECT record_id, decision, decided_by FROM adjudications "
                                                       "WHERE sample_id=?", (s["sample_id"],))}
    # each disagreement says whether it is settled, as the page's adjudicated column does (CLI-UI parity)
    dis = [{"record_id": r, a: la[r], b: lb[r], "adjudicated": adj.get(r)} for r in common if la[r] != lb[r]]
    return {"n": n, "agreement": round(po, 3), "kappa": round(kappa, 3), "table": {f"{x}|{y}": v for (x, y), v in table.items()},
            "disagreements": dis, **extra}


def reviewer_hints(project: Project, sample_name: str, names: list[str]) -> list[str]:
    """For each name with no labels in the sample, one line: the reviewer it nearly matches (another case or spelling)
    or, with no near match, the reviewers who have labels there. Names are kept as typed; nothing is refused."""
    import difflib
    s = sample_by_name(project, sample_name)
    known = [r[0] for r in project.store.query("SELECT DISTINCT reviewer_id FROM labels WHERE sample_id=? "
                                                "ORDER BY reviewer_id", (s["sample_id"],))]
    out = []
    for name in dict.fromkeys(names):
        if name in known:
            continue
        near = [k for k in known if k.casefold() == name.casefold()] or \
            difflib.get_close_matches(name.casefold(), [k.casefold() for k in known], n=1, cutoff=0.8)
        near = [k for k in known if k.casefold() in {n.casefold() for n in near}][:1]
        if near:
            out.append(f"{name!r} has no labels in sample {sample_name}; did you mean {near[0]!r}?")
        else:
            have = ", ".join(known) if known else "nobody yet"
            out.append(f"{name!r} has no labels in sample {sample_name}; reviewers with labels: {have}")
    return out


def _check_disagreement(project: Project, s: dict, record_id: int) -> None:
    """A first adjudication needs a record of the sample whose labels disagree, or a sealed record with a label that
    differs from its reference; anything else is refused with a ValueError that says why."""
    st, name = project.store, s["name"]
    if st.one("SELECT 1 FROM sample_rows WHERE sample_id=? AND record_id=?", (s["sample_id"], record_id)) is None:
        raise ValueError(f"record {record_id} is not in sample {name}; nothing was adjudicated")
    labs = [r["decision"] for r in st.query("SELECT decision FROM labels WHERE sample_id=? AND record_id=?",
                                             (s["sample_id"], record_id))]
    decisions = set(labs)
    if len(decisions) > 1:
        return
    ref = sealed_records(project, s).get(record_id)
    if ref is not None and decisions - {ref["decision"]}:
        return
    have = ("it has no labels yet" if not labs else f"its only label is {labs[0]}" if len(labs) == 1
            else f"all {len(labs)} labels say {labs[0]}")
    raise ValueError(f"record {record_id} in sample {name} has no disagreement to adjudicate ({have}); "
                     "nothing was adjudicated")


def adjudicate(project: Project, sample_name: str, record_id: int, decision: str, by: str, reason: str = "",
               labels: dict | None = None, note: str = "", correction_reason: str | None = None) -> None:
    """Store an adjudication. On a locked sample or a sealed record a change to a stored one is a correction (D2); a
    first adjudication on a sealed record corrects the reference and needs a reason too (D5)."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)} (got {decision!r})")
    by = (by or "").strip()
    if not by:
        raise ValueError("the adjudicator's name is needed")
    s = sample_by_name(project, sample_name)
    old = project.store.one("SELECT * FROM adjudications WHERE sample_id=? AND record_id=?", (s["sample_id"], record_id))
    if old is None:
        _check_disagreement(project, s, record_id)
    guarded = _guarded(project, s, record_id)
    if guarded and old is not None and _equal(old, decision, reason, labels, note):
        return
    why = None
    if guarded and (old is not None or _sealed(project, s, record_id)):
        why = _correction_why(correction_reason, _correction_text(s, record_id, None))
    from ..core.journal import MAX_AUTHOR, recording
    kind, summary = "adjudication", f"{by[:MAX_AUTHOR]} adjudicated record {record_id} in sample {sample_name}: {decision}"
    if why and old is not None:   # the correction's summary holds neither decision nor reason (D2)
        kind, summary = "label_correction", f"{by[:MAX_AUTHOR]} corrected the adjudication of record {record_id} in sample {sample_name}"
    elif why:   # a first adjudication on a sealed record: it corrects the reference, not an adjudication (M105)
        kind, summary = "label_correction", (f"{by[:MAX_AUTHOR]} adjudicated record {record_id} in sample {sample_name}, "
                                             "correcting its sealed reference")
    with recording(project, kind, summary, author=by, record_id=record_id) as rec:
        rec.rows("adjudications", "sample_id=? AND record_id=?", (s["sample_id"], record_id))
        _guard_items(rec, s, None)
        if why:
            empty = {"decision": None, "reason": None, "labels": None, "note": None, "decided_by": None}
            _correction_row(project, rec, s, record_id, "adjudication", None, old if old is not None else empty,
                            decision, reason, labels, note, why, by)
            _follow_reference(project, rec, s, record_id, None, decision, reason, labels, by)
        project.store.execute(
            "INSERT INTO adjudications(sample_id,record_id,decision,reason,labels,note,decided_by,decided_at,"
            "codebook_version,criteria_frozen,after_look) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(sample_id,record_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason, labels=excluded.labels, "
            "note=excluded.note, decided_by=excluded.decided_by, decided_at=excluded.decided_at, after_look=excluded.after_look",
            (s["sample_id"], record_id, decision, reason, json.dumps(labels or {}), note, by, utcnow(),
             *(criteria_state(project, s) or (None, None, None))))


def sealed_disagreements(project: Project, sample_name: str | None) -> int:
    """How many sealed records of a probability sample have a label whose decision differs from the record's reference,
    not left out by a package's ``exclude`` choice and saved after the record's adjudication if it has one
    (recall-final-check-fixes D6). Sealing keeps such a label from changing recall and from counting as open."""
    from .package import left_out

    s = project.store.one("SELECT * FROM samples WHERE name=?", (sample_name,)) if sample_name else None
    seals = sealed_records(project, dict(s)) if s else {}
    if not seals:
        return 0
    st, sid = project.store, s["sample_id"]
    out_ids = left_out(project, sid)
    adj = {r["record_id"]: r["decided_at"] for r in st.query(
        "SELECT record_id, decided_at FROM adjudications WHERE sample_id=?", (sid,))}
    found = set()
    for lab in st.query("SELECT label_id, record_id, decision, updated_at FROM labels WHERE sample_id=?", (sid,)):
        rid = lab["record_id"]
        if (rid in seals and lab["decision"] != seals[rid]["decision"] and lab["label_id"] not in out_ids
                and (rid not in adj or (lab["updated_at"] or "") > (adj[rid] or ""))):
            found.add(rid)
    return len(found)


def sealed_disagreement_line(project: Project, sample_name: str | None, commands: bool = True) -> str | None:
    """D6's line, counts only: sealed records with another label that disagrees with their reference; None when none.
    ``commands`` false gives the first sentence alone (the methods draft)."""
    n = sealed_disagreements(project, sample_name)
    if not n:
        return None
    head = (f"1 record of {sample_name} has another label that disagrees with its reference label" if n == 1 else
            f"{n} records of {sample_name} have another label that disagrees with their reference label")
    if not commands:
        return f"{head}; recall uses the reference labels."
    return (f"{head}; recall uses the reference labels. List them with wider label agreement {sample_name} REVIEWER "
            f"OTHER_REVIEWER; change a reference with wider label adjudicate {sample_name} RECORD DECISION --by NAME "
            "--correction-reason WHY.")


_SEALED_CLI = re.compile(r"List them with wider label agreement \S+ REVIEWER OTHER_REVIEWER; change a reference with "
                         r"wider label adjudicate \S+ RECORD DECISION --by NAME --correction-reason WHY\.")
SEALED_UI = ("List them in the agreement table (Samples and labelling, Second reviewer and adjudication); change a "
             "reference there, with a correction reason.")


def ui_wording(obj):
    """The UI routes' copy of a reply: the sealed-disagreement line points to the agreement table instead of the CLI
    commands (the CLI line names `wider label agreement`, which lists the records)."""
    if isinstance(obj, str):
        return _SEALED_CLI.sub(SEALED_UI, obj)
    if isinstance(obj, list):
        return [ui_wording(x) for x in obj]
    if isinstance(obj, dict):
        return {k: ui_wording(v) for k, v in obj.items()}
    return obj


def final_labels(project: Project, sample_name: str, primary: str | None = None) -> list[dict]:
    """One decision per record: adjudication if any, else the primary reviewer's, else the first stored label (lowest
    label_id; a reviewer's re-label keeps the id, the unique index alone would order by reviewer name). ``source``
    says whose; ``open_disagreement`` is true when reviewers disagree on the record and nobody adjudicated it. A
    sealed record of a probability sample gives its reference (D5): later labels never change it."""
    s = sample_by_name(project, sample_name)
    seals = sealed_records(project, s)
    out = _final_rows(project, s, primary)
    for r in out:
        ref = seals.get(r["record_id"])
        r["sealed"] = ref is not None
        if ref is not None:
            r.update({"decision": ref["decision"], "reason": ref["reason"], "labels": json.loads(ref["labels"] or "{}"),
                      "source": ref["source"], "open_disagreement": False})
    return out


def _final_rows(project: Project, s: dict, primary: str | None) -> list[dict]:
    """The final label of each record from the stored labels and adjudications, seals left aside."""
    st = project.store
    adj = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM adjudications WHERE sample_id=?", (s["sample_id"],))}
    labs = [dict(r) for r in st.query("SELECT * FROM labels WHERE sample_id=? ORDER BY label_id", (s["sample_id"],))]
    by_rec: dict[int, list[dict]] = {}
    for lab in labs:
        by_rec.setdefault(lab["record_id"], []).append(lab)
    out = []
    for r in st.query("SELECT sr.*, m.title FROM sample_rows sr JOIN records m ON m.record_id=sr.record_id WHERE sr.sample_id=? ORDER BY sr.queue_pos", (s["sample_id"],)):
        rid = r["record_id"]
        chosen, source, open_dis = None, None, False
        if rid in adj:
            chosen, source = adj[rid], "adjudication"
        elif rid in by_rec:
            cands = by_rec[rid]
            pick = next((c for c in cands if c["reviewer_id"] == primary), None) if primary else None
            chosen = pick or cands[0]
            source = chosen["reviewer_id"]
            open_dis = len({c["decision"] for c in cands}) > 1   # reviewers disagree and nobody adjudicated
        out.append({"record_id": rid, "title": r["title"], "band": r["band"], "weight": r["weight"], "pool": r["pool"], "rank": r["rank"],
                    "decision": chosen["decision"] if chosen else None, "reason": chosen["reason"] if chosen else None,
                    "labels": json.loads(chosen["labels"] or "{}") if chosen else {}, "source": source,
                    "open_disagreement": open_dis})
    return out


PACKAGE_RULE_KINDS = ("probability", "purposive")   # kinds whose open review packages keep a sample incomplete (D3;
                                                   # purposive since ui-redesign D4)
NO_RECORDS = "the sample has no records"
NOT_INDEPENDENT = "(made in an unblinded queue, imported, or saved before provenance was recorded)"


def _record_final(labels: list[dict], adjudicated: bool, need: int, independent: bool) -> bool:
    """D3's per-record rule. ``labels`` carry ``decision``, ``reviewer_id`` and ``independent``; ``need`` is the
    number of distinct reviewers a record needs when nobody adjudicated it; ``independent`` asks for one independent
    label (probability samples)."""
    if independent and not any(lab["independent"] for lab in labels):
        return False
    if adjudicated:
        return True
    return (len({lab["reviewer_id"] for lab in labels}) >= max(need, 1)
            and len({lab["decision"] for lab in labels}) == 1)


def _open_packages(project: Project, s: dict) -> list[str]:
    """Reviewers of the packages of ``s``'s kind (validation or development) on sample ``s`` still open: not withdrawn, with an item on ``s`` that no
    import decided. An item counts as decided when the reviewer has a label on it made in the package, or it is not
    a repeat and they have any label, or their label was saved after the package's last import (D3); an item whose
    record left the sample is decided."""
    from . import package as pk

    st = project.store
    out = []
    for pkg in pk.packages(project):
        if pkg["withdrawn_at"] or pkg["kind"] != pk.KIND_WORD[s["kind"]] or s["name"] not in [x["name"] for x in pkg["samples"]]:
            continue
        for it in pk.items(project, pkg["package_id"]):
            if it["sample_id"] != s["sample_id"] or not pk._in_sample(project, it["sample_id"], it["record_id"]):
                continue
            r = st.one("SELECT made_in, updated_at FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?",
                       (it["sample_id"], it["record_id"], pkg["reviewer"]))
            if r is not None and (r["made_in"] == pkg["package_id"] or not it["repeat"]
                                  or (pkg["imported_at"] and (r["updated_at"] or "") > pkg["imported_at"])):
                continue
            out.append(pkg["reviewer"])
            break
    return out


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def completeness(project: Project, sample_name: str, min_reviewers: int | None = None) -> dict:
    """Whether every record of a sample has a final label (D3; the contract with ui-redesign). A sealed record is
    final through its reference (D5). Any other record is final when it is adjudicated, or when its labels, from at
    least ``min_reviewers`` distinct reviewers, all agree; on a probability sample it also needs one independent
    label (D11), and an open validation package keeps the sample incomplete. Labels a package's ``exclude`` choice
    leaves out of agreement are left out here too. The id lists are in queue order; KeyError for an unknown sample.
    ``min_reviewers`` left out: ``sampling.development_reviewers`` on a purposive sample, else one (ui-redesign D4),
    and an open development package keeps a purposive sample incomplete too."""
    from .package import left_out

    s = sample_by_name(project, sample_name)
    st = project.store
    sid, kind = s["sample_id"], s["kind"]
    prob = kind == "probability"
    if min_reviewers is None:
        min_reviewers = project.config.protocol.sampling.development_reviewers if kind == "purposive" else 1
    out_ids = left_out(project, sid)
    adj = {r[0] for r in st.query("SELECT record_id FROM adjudications WHERE sample_id=?", (sid,))}
    sealed_ids = set(sealed_records(project, s))
    by_rec: dict[int, list[dict]] = {}
    for r in st.query("SELECT * FROM labels WHERE sample_id=? ORDER BY label_id", (sid,)):
        lab = dict(r)
        if lab["label_id"] in out_ids:
            continue
        lab["independent"] = _independent(project, lab) if prob else True
        by_rec.setdefault(lab["record_id"], []).append(lab)
    rows = [r[0] for r in st.query("SELECT record_id FROM sample_rows WHERE sample_id=? ORDER BY queue_pos", (sid,))]
    missing, no_label, not_ind, open_, sealed, few = [], [], [], [], [], []
    for rid in rows:
        labs = by_rec.get(rid, [])
        if rid in sealed_ids:
            sealed.append(rid)
            continue
        if _record_final(labs, rid in adj, min_reviewers, prob):
            continue
        missing.append(rid)
        if not labs:
            no_label.append(rid)
            continue
        if prob and not any(lab["independent"] for lab in labs):
            not_ind.append(rid)
        if rid not in adj and len({lab["decision"] for lab in labs}) > 1:
            open_.append(rid)
        elif rid not in adj and len({lab["reviewer_id"] for lab in labs}) < min_reviewers:
            few.append(rid)
    packages_out = _open_packages(project, s) if kind in PACKAGE_RULE_KINDS else []
    total = len(rows)
    lines = []
    if not total:
        lines.append(NO_RECORDS)
    if no_label:
        lines.append(f"{len(no_label)} of {total} records have no label")
    if not_ind:
        lines.append(f"{_plural(len(not_ind), 'record has', 'records have')} no independent label {NOT_INDEPENDENT}")
    if open_:
        lines.append(f"{_plural(len(open_), 'record has labels that disagree and is', 'records have labels that disagree and are')}"
                     " not adjudicated")
    if few:
        lines.append(f"{_plural(len(few), 'record has', 'records have')} labels of fewer than {min_reviewers} "
                     f"reviewers and {'is' if len(few) == 1 else 'are'} not adjudicated")
    for who in packages_out:
        lines.append(f"{who}'s review package on {sample_name} is not finished")
    need = f"labels of at least {min_reviewers} reviewers that agree" if min_reviewers > 1 else "labels that agree"
    rule = (f"A record is final when it is adjudicated or has {need}"
            + ("; on a validation sample it also needs one independent label, a sealed record is final through its "
               "reference, and an open review package keeps the sample incomplete." if prob
               else "; an open review package keeps the sample incomplete." if kind in PACKAGE_RULE_KINDS else "."))
    return {"sample": sample_name, "kind": kind, "complete": bool(total) and not missing and not packages_out,
            "total": total, "final": total - len(missing), "missing": missing, "no_label": no_label,
            "not_independent": not_ind, "open": open_, "few": few, "sealed": sealed, "packages_out": packages_out,
            "rule": rule, "lines": lines}


REVEAL_ONCE = "Recall on {sample} is revealed once every sampled record has a final label."


def incomplete_notice(project: Project, c: dict) -> str:
    """The sentence of D4 for an incomplete probability sample (``c`` from :func:`completeness`): what is left, then
    for each reviewer with declarable labels (DECLARABLE) on a record without an independent label, both ways to
    declare them. ``unblinded queue`` labels cannot be declared (D11) and get no hint."""
    parts = list(c["lines"])
    if c["not_independent"]:
        from shlex import quote

        from .package import left_out

        sid = sample_by_name(project, c["sample"])["sample_id"]
        out_ids, ids = left_out(project, sid), set(c["not_independent"])
        n: dict[str, int] = {}
        for r in project.store.query("SELECT * FROM labels WHERE sample_id=? ORDER BY label_id", (sid,)):
            if r["record_id"] in ids and r["label_id"] not in out_ids and (r["made_in"] or "wider") in DECLARABLE:
                n[r["reviewer_id"]] = n.get(r["reviewer_id"], 0) + 1
        for who, k in n.items():
            parts.append(f"{_plural(k, 'label', 'labels')} by {who} {'has' if k == 1 else 'have'} no stated independence: "
                         f"declare it on the sample card (Declare labels independent) or with wider label provenance "
                         f"{quote(c['sample'])} --reviewer {quote(who)} --independent BASIS")
    return (REVEAL_ONCE.format(sample=c["sample"]) + " Not yet: " + "; ".join(parts)
            + ". Nothing was estimated or recorded.")


def completeness_counts(c: dict) -> dict:
    """``completeness()`` without the id lists, for the page and ``wider sample status --json`` (D3, round 4 F3)."""
    lists = ("missing", "no_label", "not_independent", "open", "few", "sealed")
    return {**{k: v for k, v in c.items() if k not in lists}, **{f"n_{k}": len(c[k]) for k in lists}}


def status_keys(project: Project, sample_name: str) -> dict:
    """``completeness`` (counts, no ids), ``provenance`` and the number of ``corrections`` of a sample, the same in
    ``wider sample status --json`` and ``GET /api/samples`` (D2, D3, D11); never a correction's decision or reason,
    as a blind second reviewer may open the page."""
    s = sample_by_name(project, sample_name)
    return {"completeness": completeness_counts(completeness(project, sample_name)),
            "provenance": provenance(project, sample_name),
            "corrections": project.store.scalar("SELECT COUNT(*) FROM label_corrections WHERE sample_id=?",
                                                (s["sample_id"],))}


def lock(project: Project, sample_name: str, by: str | None = None) -> None:
    """Lock a sample: a signed ``sample_lock`` change, undoable until a recall result on it is seen (D5). KeyError
    for an unknown sample; a sample already locked records nothing."""
    from ..core.journal import recording

    s = sample_by_name(project, sample_name)
    with recording(project, "sample_lock", f"sample {sample_name} locked", by) as rec:
        rec.rows("samples", "sample_id=?", [s["sample_id"]])
        project.store.execute("UPDATE samples SET locked=1 WHERE sample_id=?", (s["sample_id"],))


def recall_seen(project: Project, sample: dict) -> str | None:
    """"yes" when a recall result on ``sample`` was recorded as seen, "maybe" when the project's looks were taken
    before looks were recorded (``recall_looks_before_v7``) and it is a probability sample, else None."""
    if project.store.scalar("SELECT COUNT(*) FROM validation_looks WHERE sample_id=? AND n>0", (sample["sample_id"],)):
        return "yes"
    if sample.get("kind") == "probability" and project.store.get_meta("recall_looks_before_v7") is not None:
        return "maybe"
    return None


def unlock_warning(project: Project, sample: dict) -> str | None:
    """The sentence an unlock of a locked ``sample`` shows when its recall result was (or may have been) seen."""
    seen = recall_seen(project, sample) if sample.get("locked") else None
    if seen is None:
        return None
    verb = "was seen" if seen == "yes" else "may have been seen"
    return f"The recall estimate on {sample['name']} is no longer blind: its result {verb} before this unlock."


def unlock(project: Project, sample_name: str, by: str | None = None, yes: bool = False) -> dict:
    """Unlock a sample: a signed ``sample_unlock`` change, which is final (D5). When its recall result was seen the
    unlock needs ``yes``; without it nothing changes. Returns ``{unlocked, warning}``. KeyError for an unknown sample."""
    from ..core.journal import recording

    s = sample_by_name(project, sample_name)
    warning = unlock_warning(project, s)
    if not s["locked"] or (warning and not yes):
        return {"unlocked": False, "warning": warning}
    with recording(project, "sample_unlock", f"sample {sample_name} unlocked", by) as rec:
        rec.rows("samples", "sample_id=?", [s["sample_id"]])
        rec.extra({"t": "recall_seen", "sample": sample_name, "value": recall_seen(project, s) or "no"})
        project.store.execute("UPDATE samples SET locked=0 WHERE sample_id=?", (s["sample_id"],))
    return {"unlocked": True, "warning": warning}


def unlocks(project: Project) -> list[dict]:
    """Every sample unlock in History, oldest first: sample, at, by and recall_seen ("yes", "maybe" or "no")."""
    out = []
    for r in project.store.query("SELECT at, author, items FROM history WHERE kind='sample_unlock' ORDER BY change_id"):
        note = next((it for it in json.loads(r["items"] or "[]") if it.get("t") == "recall_seen"), None)
        if note:
            out.append({"sample": note["sample"], "at": r["at"], "by": r["author"], "recall_seen": note["value"]})
    return out


def unlock_sentences(project: Project, who=None) -> list[str]:
    """One methods-draft sentence per sample unlock (D5): sample, date, author and whether its recall result was seen.
    ``who`` writes the author as shared files name it (researcher-attribution); "not shown" leaves the author out."""
    tail = {"yes": " after its recall result had been seen.", "maybe": "; its recall result may have been seen before.",
            "no": " before any recall result on it was seen."}
    out = []
    for u in unlocks(project):
        by = who(u["by"]) if who else u["by"]
        by = "" if by == "not shown" else f" by {by}"
        out.append(f"The {u['sample']} sample was unlocked on {u['at'][:10]}{by}{tail[u['recall_seen']]}")
    return out


# ----------------------------------------------------------------- CSV round trip
def export_csv(project: Project, sample_name: str, path: str | Path, reviewer_id: str | None = None, blind: bool = True) -> int:
    rows = queue(project, reviewer_id or "", [sample_name], blind=blind, items=False)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # read_only is kept for files already written, always "no" now that a lock keeps labels open (D8);
        # import_csv ignores it (D6)
        cols = ["queue_pos", "record_id", "title", "authors", "year", "journal", "doi", "abstract", "decision", "reason",
                "labels", "note", "read_only"]
        w.writerow(cols)
        for r in rows:
            r = {**r, "labels": r.get("labels") or "", "read_only": "no"}
            w.writerow([r.get(c, "") for c in cols])
    return len(rows)


def _parse_rayyan_notes(notes: str) -> tuple[dict[str, str], str, str]:
    """Rayyan exports decisions, labels and reasons inside the notes column."""
    decisions = {}
    m = re.search(r"RAYYAN-INCLUSION:\s*\{([^}]*)\}", notes or "")
    if m:
        for who, dec in re.findall(r'"([^"]+)"\s*=>\s*"([^"]+)"', m.group(1)):
            decisions[who] = RAYYAN_DECISION.get(dec.strip().lower(), dec.strip().lower())
    labels = (re.search(r"RAYYAN-LABELS:\s*([^|]*)", notes or "") or [None, ""])[1].strip()
    reasons = (re.search(r"RAYYAN-EXCLUSION-REASONS:\s*([^|]*)", notes or "") or [None, ""])[1].strip()
    return decisions, labels, reasons


def import_csv(project: Project, sample_name: str, path: str | Path, reviewer_id: str | None = None, overwrite: bool = False,
               independent: str | None = None, correction_reason: str | None = None) -> dict:
    """Import decisions from this tool's export, from a Rayyan CSV export, or from a CSV with decision/title/doi columns.
    Records are matched by record_id, else by DOI, else by normalised title. The labels are ``file import``;
    ``independent`` states their basis of independence (D11)."""
    s = sample_by_name(project, sample_name)
    basis = _clean_basis(independent) if independent is not None else None
    st = project.store
    members = {r["record_id"]: dict(r) for r in st.query(
        "SELECT m.record_id, m.title, m.doi FROM sample_rows sr JOIN records m ON m.record_id=sr.record_id WHERE sr.sample_id=?", (s["sample_id"],))}
    corpus = [CorpusRecord(str(rid), m["title"] or "", m["doi"]) for rid, m in members.items()]
    n = Counter()
    saves = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    from ..core.journal import PSEUDONYM_FORM, check_name
    if reviewer_id:
        check_name(reviewer_id.strip())
    else:   # every reviewer the file names, before any row is saved: a refusal leaves nothing imported
        named = {w.strip() for row in rows for w in _parse_rayyan_notes(
            {(k or "").strip().lower(): (v or "") for k, v in row.items()}.get("notes", ""))[0]}
        bad = sorted(w for w in named if PSEUDONYM_FORM.fullmatch(w))
        if bad:
            raise ValueError(f"the file names the reviewer {', '.join(bad)}, which has the form of the pseudonyms shared "
                             f"files use (Researcher 1, Researcher 2 ...); rename the reviewer in the file or give "
                             f"--reviewer; nothing was imported")
    for row in rows:
        row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        rid = None
        if row.get("record_id", "").isdigit() and int(row["record_id"]) in members:
            rid = int(row["record_id"])
        else:
            m = locate_benchmark([("x", row.get("title", ""), row.get("doi") or None)], corpus, project.config.protocol.identification.jw_confirm)[0]
            rid = int(m.record_id) if m.record_id else None
        if rid is None:
            n["unmatched"] += 1
            continue
        decisions, rlabels, rreasons = _parse_rayyan_notes(row.get("notes", ""))
        if not decisions and row.get("decision"):
            decisions = {reviewer_id or "import": RAYYAN_DECISION.get(row["decision"].strip().lower(), row["decision"].strip().lower())}
        if not decisions:
            n["no_decision"] += 1
            continue
        labels = {}
        for part in (row.get("labels") or rlabels or row.get("rayyan_labels", "")).split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                labels[k.strip()] = v.strip()
        reason = row.get("reason") or rreasons or row.get("rayyan_exclusion_reasons", "")
        for who, dec in decisions.items():
            if dec not in DECISIONS:
                n["bad_decision"] += 1
                continue
            if not reviewer_id:   # a reviewer id read from the file, not given by a person (researcher-attribution D2)
                from ..core.journal import note_inferred
                note_inferred(who, "imported")
            who = reviewer_id or who
            existing = st.one("SELECT 1 FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id=?", (s["sample_id"], rid, who))
            if existing and not overwrite:
                n["kept_existing"] += 1
                continue
            saves.append((rid, who, dec, reason, labels, row.get("note", "")))
    # a change of a saved label on a locked sample or a sealed record needs a reason, asked before any row is saved (D2)
    changes = sum(needs_correction(project, s, *x) for x in saves)
    if changes and (correction_reason or "").strip():
        _correction_why(correction_reason, "")   # too long: refused before any row is saved (risk-checker R4)
    if changes and not (correction_reason or "").strip():
        where = f"the locked sample {sample_name}" if s["locked"] else f"sealed records of {sample_name}"
        raise CorrectionNeeded(f"{_plural(changes, '1 row changes', f'{changes} rows change')} a saved label on "
                               f"{where}; give --correction-reason")
    for rid, who, dec, reason, labels, note in saves:
        label(project, sample_name, rid, who, dec, reason=reason, labels=labels, note=note,
              made_in="file import", basis=basis, correction_reason=correction_reason)
        n["imported"] += 1
    return dict(n)


# ----------------------------------------------------------------- import a labelled sample drawn elsewhere
def import_sample_csv(project: Project, name: str, path: str | Path, kind: str = "probability", reviewer_id: str = "original",
                      ranking_name: str | None = None, independent: str | None = None, by: str | None = None) -> dict:
    """Create a sample from a CSV of records labelled outside this tool (for example the original review's
    validation set): columns title and/or doi, decision, and for a probability sample band, N_h, n_h or weight.
    Records are matched to the corpus by DOI then normalised title. Labels go to ``reviewer_id`` as ``sample import``;
    ``independent`` states their basis of independence, signed by ``by`` (D11)."""
    from ..core.journal import check_name, current_author
    check_name((reviewer_id or "").strip())
    basis = _clean_basis(independent) if independent is not None else None
    signer = current_author(by) if basis else None
    st = project.store
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in st.query("SELECT record_id,title,doi FROM records")]
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{("big_n_h" if (k or "").strip() == "N_h" else (k or "").strip().lower()): (v or "").strip() for k, v in r.items()}
                for r in csv.DictReader(f)]
    bench = [(str(i), r.get("title", ""), r.get("doi") or None) for i, r in enumerate(rows)]
    matches = locate_benchmark(bench, corpus, project.config.protocol.identification.jw_confirm)
    from ..rank.versions import resolve

    rk = st.one("SELECT * FROM rankings WHERE name=?", (resolve(project, ranking_name),))
    ranks = {x["record_id"]: x["rank"] for x in st.query("SELECT record_id, rank FROM ranking_rows WHERE ranking_id=?", (rk["ranking_id"],))} if rk else {}
    old = st.one("SELECT sample_id FROM samples WHERE name=?", (name,))
    if old and _added_in_wider(project, old["sample_id"]):
        raise ValueError(f"{name} has labels or results added in WIDER since it was imported; import the file under "
                         "another name")
    n = Counter()
    picked = []
    seen = set()
    for r, m in zip(rows, matches):
        if m.record_id is None:
            n["unmatched"] += 1
            continue
        rid = int(m.record_id)
        if rid in seen:
            n["duplicate"] += 1
            continue
        seen.add(rid)
        w = None
        if r.get("weight"):
            w = float(r["weight"])
        elif r.get("n_h") and r.get("n_h") != "0" and r.get("big_n_h"):
            w = float(r["big_n_h"]) / float(r["n_h"])
        picked.append((rid, r.get("band") or None, w, (r.get("pool") or "") + (":" + r["subpool"] if r.get("subpool") else "") or None,
                       ranks.get(rid), r))
        n["matched"] += 1
    with st.tx() as c:
        if old:
            c.execute("DELETE FROM labels WHERE sample_id=?", (old["sample_id"],))
            c.execute("DELETE FROM adjudications WHERE sample_id=?", (old["sample_id"],))
            for table in ("label_independence", "label_corrections", "label_seals"):
                c.execute(f"DELETE FROM {table} WHERE sample_id=?", (old["sample_id"],))
            c.execute("DELETE FROM sample_rows WHERE sample_id=?", (old["sample_id"],))
            c.execute("DELETE FROM samples WHERE sample_id=?", (old["sample_id"],))
        design = {"imported_from": Path(path).name, "imported_at": utcnow(), "ranking": rk["name"] if rk else None,
                  "bands": _bands_from_rows(rows)}
        cur = c.execute("INSERT INTO samples(name,kind,ranking_id,design,seed,n,created_at,locked) VALUES(?,?,?,?,NULL,?,?,1)",
                        (name, kind, rk["ranking_id"] if rk else None, json.dumps(design), len(picked), utcnow()))
        sid = cur.lastrowid
        c.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) VALUES(?,?,?,?,?,?,?)",
                      [(sid, rid, i + 1, band, w, pool, rank) for i, (rid, band, w, pool, rank, _) in enumerate(picked)])
        c.execute("INSERT OR IGNORE INTO reviewers(reviewer_id,name,created_at) VALUES(?,?,?)", (reviewer_id, reviewer_id, utcnow()))
        for rid, band, w, pool, rank, r in picked:
            dec = RAYYAN_DECISION.get((r.get("decision") or "").lower(), (r.get("decision") or "").lower())
            if dec not in DECISIONS:
                n["no_decision"] += 1
                continue
            labels = {}
            for part in (r.get("labels") or r.get("rayyan_labels") or "").split(","):
                if ":" in part:
                    k, v = part.split(":", 1)
                    labels[k.strip()] = v.strip()
            c.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,reason,labels,note,updated_at,made_in) "
                      "VALUES(?,?,?,?,?,?,?,?,'sample import')",
                      (sid, rid, reviewer_id, dec, r.get("reason") or r.get("rayyan_exclusion_reasons") or "", json.dumps(labels),
                       r.get("note", ""), utcnow()))
            if basis:
                c.execute("INSERT INTO label_independence(sample_id,record_id,reviewer_id,basis,by,at) VALUES(?,?,?,?,?,?)",
                          (sid, rid, reviewer_id, basis, signer, utcnow()))
            n["labelled"] += 1
        from ..core import researchers
        from ..core.journal import source_of
        researchers.register(project, reviewer_id, source_of(reviewer_id))   # "original" is a placeholder, not a person
    return {"sample_id": sid, "name": name, **dict(n)}


def _added_in_wider(project: Project, sample_id: int) -> bool:
    """Whether WIDER added work to a sample since it was imported (D1): a label not from the import, an adjudication,
    a correction, a review package, a recall look, or any History change on its label tables (a provenance
    declaration)."""
    st = project.store
    for sql in ("SELECT COUNT(*) FROM labels WHERE sample_id=? AND COALESCE(made_in,'')<>'sample import'",
                "SELECT COUNT(*) FROM adjudications WHERE sample_id=?",
                "SELECT COUNT(*) FROM label_corrections WHERE sample_id=?",
                "SELECT COUNT(*) FROM review_package_items WHERE sample_id=?",   # risk-checker R3
                "SELECT COUNT(*) FROM validation_looks WHERE sample_id=? AND n>0"):
        if st.scalar(sql, (sample_id,)):
            return True
    from ..core.journal import LOCK_TABLES
    for h in st.query("SELECT items FROM history WHERE items LIKE '%\"rows\"%'"):
        if any(it.get("t") == "rows" and it.get("table") in LOCK_TABLES and (it.get("params") or [None])[0] == sample_id
               and it.get("where", "").startswith("sample_id=?") for it in json.loads(h["items"])):
            return True
    return False


def _bands_from_rows(rows: list[dict]) -> list[dict]:
    out = {}
    for r in rows:
        b = r.get("band")
        if not b:
            continue
        d = out.setdefault(b, {"band": b, "n_rows": 0})
        d["n_rows"] += 1
        for k, out_k in (("big_n_h", "N_h"), ("n_h", "n_h"), ("weight", "weight")):
            if r.get(k):
                d[out_k] = float(r[k])
    return list(out.values())


# ---------------------------------------------------------------- criteria-change assessment (D11, owner's Q6)
ELEMENT_FIELDS = ("rule", "counts", "does_not_count")
REASON_FIELDS = ("text", "element")
NO_FROZEN = "no frozen codebook: freeze the criteria first (wider construct freeze codebook VERSION)"


def _codebook_diff(old: dict, new: dict) -> dict:
    """What changed from codebook ``old`` to ``new``: elements and exclusion reasons added, removed or changed (the
    fields that decide eligibility), and every other changed part counted as "other changes"."""
    oe = {e["id"]: e for e in old.get("elements") or []}
    ne = {e["id"]: e for e in new.get("elements") or []}
    orr = {r["code"]: r for r in old.get("exclusion_reasons") or []}
    nr = {r["code"]: r for r in new.get("exclusion_reasons") or []}
    el_changed = sorted(k for k in oe.keys() & ne.keys() if any(oe[k].get(f) != ne[k].get(f) for f in ELEMENT_FIELDS))
    rs_changed = sorted(k for k in orr.keys() & nr.keys() if any(orr[k].get(f) != nr[k].get(f) for f in REASON_FIELDS))
    other = sum(old.get(k) != new.get(k) for k in (set(old) | set(new)) - {"elements", "exclusion_reasons"})
    other += sum(oe[k].get("name") != ne[k].get("name") for k in oe.keys() & ne.keys())
    return {"elements_added": sorted(ne.keys() - oe.keys()), "elements_removed": sorted(oe.keys() - ne.keys()),
            "elements_changed": el_changed, "reasons_added": sorted(nr.keys() - orr.keys()),
            "reasons_removed": sorted(orr.keys() - nr.keys()), "reasons_changed": rs_changed, "other_changes": other,
            "n_old_elements": len(oe)}


def _early_labels(project: Project, s: dict, fv: int | None) -> list[dict]:
    """The labels of the sample made before the latest freeze (or with no recorded state), each with the codebook
    version it was first made under (a re-checked label keeps its first revision's version)."""
    rows = [dict(r) for r in project.store.query(
        "SELECT l.label_id, l.record_id, l.reviewer_id, l.decision, l.reason, l.codebook_version, l.criteria_frozen, "
        "l.recheck_from, (SELECT old_codebook_version FROM label_revisions v WHERE v.label_id=l.label_id "
        "ORDER BY revision_id LIMIT 1) first_version, (SELECT old_decision FROM label_revisions v "
        "WHERE v.label_id=l.label_id ORDER BY revision_id LIMIT 1) first_decision, (SELECT old_reason FROM label_revisions v "
        "WHERE v.label_id=l.label_id ORDER BY revision_id LIMIT 1) first_reason "
        "FROM labels l WHERE l.sample_id=? ORDER BY l.label_id", (s["sample_id"],))]
    out = []
    for r in rows:
        early = r["criteria_frozen"] is None or not r["criteria_frozen"] or r["codebook_version"] != fv \
            or r["recheck_from"] is not None
        if early:
            if r["recheck_from"] is not None:   # judged against the version and decision it was first made under
                r["made_under"], r["made_decision"], r["made_reason"] = r["first_version"], r["first_decision"], r["first_reason"]
            else:
                r["made_under"], r["made_decision"], r["made_reason"] = r["codebook_version"], r["decision"], r["reason"]
            out.append(r)
    return out


def _sample_or_default(project: Project, sample_name: str | None) -> str:
    return sample_name or project.config.protocol.sampling.probability_sample


def criteria_change(project: Project, sample_name: str | None = None) -> dict:
    """The difference between each early label's codebook version and the latest frozen one, the labels it may
    affect (includes and maybes under a version with an element or reason change, excludes whose reason was removed
    or changed or names a changed element), those that cannot be identified, and the recommendation (D11)."""
    from ..construct.artifacts import latest_frozen, load
    sample_name = _sample_or_default(project, sample_name)
    s = sample_by_name(project, sample_name)
    lf = latest_frozen(project, "codebook")
    out = {"sample": sample_name, "to_version": lf["version"] if lf else None, "early": 0, "versions": {},
           "affected": [], "not_identifiable": [], "recommended": "none", "recommendation": None, "lines": []}
    if lf is None:
        out["lines"].append(NO_FROZEN)
        return out
    fv = lf["version"]
    new = load(project, "codebook", fv)[1] or {}
    early = _early_labels(project, s, fv)
    out["early"] = len(early)
    diffs: dict = {}
    for r in early:
        v = r["made_under"]
        if v not in diffs:
            old = load(project, "codebook", v)[1] if v is not None else None
            diffs[v] = _codebook_diff(old, new) if old is not None else None
        d = diffs[v]
        if d is None:
            out["not_identifiable"].append(r["label_id"])
            continue
        els = set(d["elements_changed"]) | set(d["elements_removed"]) | set(d["elements_added"])
        any_change = bool(els or d["reasons_added"] or d["reasons_removed"] or d["reasons_changed"])
        if not any_change:
            continue
        if r["made_decision"] in ("include", "maybe"):
            out["affected"].append(r["label_id"])
        elif not r["made_reason"]:
            out["not_identifiable"].append(r["label_id"])
        else:
            old = load(project, "codebook", v)[1] or {}
            reason = next((x for x in old.get("exclusion_reasons") or [] if x["code"] == r["made_reason"]), None)
            if r["made_reason"] in d["reasons_removed"] or r["made_reason"] in d["reasons_changed"] \
                    or (reason and reason.get("element") in els):
                out["affected"].append(r["label_id"])
    out["versions"] = {("none" if k is None else f"v{k}"): v for k, v in diffs.items()}
    if not early:
        return out
    known = [d for d in diffs.values() if d is not None]
    tot = lambda key: len({x for d in known for x in d[key]})   # noqa: E731
    n_el = tot("elements_changed") + tot("elements_removed") + tot("elements_added")
    n_rs = tot("reasons_changed") + tot("reasons_removed") + tot("reasons_added")
    other = sum(d["other_changes"] for d in known)
    out["counts"] = {"other_changes": other, "elements_changed": n_el, "reasons_changed": n_rs}
    vs = sorted(k for k in diffs if k is not None)
    out["from_versions"] = vs
    out["lines"].append(f"other changes: {other}; elements changed: {n_el}; reasons changed: {n_rs}")
    large = not known or any(d["n_old_elements"] and (len(d["elements_changed"]) + len(d["elements_removed"]))
                             > d["n_old_elements"] / 2 for d in known)
    n, a, k = len(early), len(out["affected"]), len(out["not_identifiable"])
    if not n_el and not n_rs and not k:
        out["recommended"], out["recommendation"] = "none", "keep the labels; the eligibility criteria did not change"
    elif large:
        out["recommended"], out["recommendation"] = "all", f"re-check all {n} early labels"
    else:
        rec = f"re-check the {a} affected labels"
        if k:
            rec += f" ({k} cannot be identified; re-check all {n} to cover them)"
        out["recommended"], out["recommendation"] = "affected", rec
    out["lines"].append(f"Recommended: {out['recommendation']}")
    return out


def latest_assessment(project: Project, sample_name: str | None = None) -> dict | None:
    """The newest assessment of the sample for the latest frozen codebook version, or None (a new freeze makes the
    earlier ones stale)."""
    from ..construct.artifacts import latest_frozen
    s = sample_by_name(project, _sample_or_default(project, sample_name))
    lf = latest_frozen(project, "codebook")
    if lf is None:
        return None
    r = project.store.one("SELECT * FROM criteria_assessments WHERE sample_id=? AND to_version=? "
                          "ORDER BY assessment_id DESC LIMIT 1", (s["sample_id"], lf["version"]))
    return dict(r) if r else None


def assess_criteria(project: Project, sample_name: str | None, substantive: str, scope: str | None = None,
                    note: str = "", author: str | None = None) -> dict:
    """Record the researcher's assessment of the criteria change (D11): ``substantive`` yes or no; ``scope`` none,
    affected or all (default the recommendation; no forces none). Only an assessment makes labels due."""
    substantive = (substantive or "").strip().lower()
    if substantive not in ("yes", "no"):
        raise ValueError("--assess must be yes or no")
    if scope is not None and scope not in ("none", "affected", "all"):
        raise ValueError("--scope must be affected or all")
    author = (author or "").strip()
    if not author:
        raise ValueError("the assessor's name is needed")
    ch = criteria_change(project, sample_name)
    if ch["to_version"] is None:
        raise ValueError(NO_FROZEN)
    if not ch["early"]:
        raise ValueError(f"no label of sample {ch['sample']!r} was made before the criteria were frozen at v{ch['to_version']}")
    scope = "none" if substantive == "no" else (scope or (ch["recommended"] if ch["recommended"] != "none" else "all"))
    s = sample_by_name(project, ch["sample"])
    from ..core.journal import MAX_AUTHOR, recording
    with recording(project, "criteria_assess", f"{author[:MAX_AUTHOR]} assessed a criteria change", author=author) as rec:
        rec.rows("criteria_assessments", "sample_id=?", (s["sample_id"],))
        project.store.execute(
            "INSERT INTO criteria_assessments(sample_id,from_versions,to_version,substantive,scope,recommended,n_affected,"
            "n_unidentifiable,note,author,at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (s["sample_id"], json.dumps(ch.get("from_versions", [])), ch["to_version"], substantive, scope,
             ch["recommended"], len(ch["affected"]), len(ch["not_identifiable"]), note or "", author, utcnow()))
    return {**latest_assessment(project, ch["sample"]), "change": ch, "due": len(due_labels(project, ch["sample"]))}


def due_labels(project: Project, sample_name: str | None = None) -> list[int]:
    """The label ids due for re-check: the latest assessment's scope (affected or all early labels), less those
    already re-checked under the frozen version. Without an assessment nothing is due."""
    a = latest_assessment(project, sample_name)
    if a is None or a["scope"] == "none":
        return []
    ch = criteria_change(project, sample_name)
    ids = ch["affected"] if a["scope"] == "affected" else [r for r in _early_ids(project, ch)]
    done = _rechecked(project, ch)
    return [i for i in ids if i not in done]


def _early_ids(project: Project, ch: dict) -> list[int]:
    s = sample_by_name(project, ch["sample"])
    return [r["label_id"] for r in _early_labels(project, s, ch["to_version"])]


def _rechecked(project: Project, ch: dict) -> set[int]:
    s = sample_by_name(project, ch["sample"])
    return {r["label_id"] for r in project.store.query(
        "SELECT label_id FROM labels WHERE sample_id=? AND recheck_from IS NOT NULL AND codebook_version=?",
        (s["sample_id"], ch["to_version"]))}


def criteria_lines(project: Project, sample_name: str | None = None) -> list[str]:
    """The sentences every recall output carries about a criteria change (D11): unassessed, assessed as not
    substantive, or the scope and the labels not re-checked under the frozen version."""
    ch = criteria_change(project, sample_name)
    if ch["to_version"] is None or not ch["early"]:
        return []
    fv, n = ch["to_version"], ch["early"]
    nothing = ch["recommended"] == "none" and not ch["counts"]["other_changes"]
    if nothing:
        return []
    vs = ch.get("from_versions") or []
    span = f"(v{vs[0]} to v{fv})" if vs else f"(before v{fv})"
    a = latest_assessment(project, sample_name)
    if a is None:
        return [f"The criteria changed after {n} labels {span} and the change has not been assessed: "
                f"wider label criteria {ch['sample']}"]
    if a["substantive"] == "no":
        return [f"Criteria changed after {n} labels {span}; assessed as not substantive by {a['author']}"
                + (f": {a['note']}" if a["note"] else "")]
    lines = [f"Criteria changed after {n} labels {span}; assessed as substantive by {a['author']}, re-check scope "
             f"{a['scope']} (recommended {a['recommended']})" + (f": {a['note']}" if a["note"] else "")]
    k = n - len(_rechecked(project, ch) & set(_early_ids(project, ch)))
    if k:
        lines.append(f"{k} of the {n} early labels were not re-checked under v{fv}; they are not independently "
                     "verified against the frozen criteria.")
    return lines


def revisions(project: Project, sample_name: str) -> list[dict]:
    """The re-check revisions of a sample: the original and revised label, both codebook versions, the assessment."""
    s = sample_by_name(project, sample_name)
    return [dict(r) for r in project.store.query(
        "SELECT v.*, a.note assessment_note FROM label_revisions v LEFT JOIN criteria_assessments a "
        "ON a.assessment_id=v.assessment_id WHERE v.sample_id=? ORDER BY v.revision_id", (s["sample_id"],))]


def criteria_report(project: Project, sample_name: str | None = None) -> dict:
    """What `wider label criteria` and GET /api/labels/criteria show: the change, the latest assessment, the due
    count and the output lines."""
    ch = criteria_change(project, sample_name)
    a = latest_assessment(project, ch["sample"]) if ch["to_version"] is not None else None
    due = due_labels(project, ch["sample"])
    lines = list(ch["lines"]) + criteria_lines(project, ch["sample"])
    if a is not None and a["scope"] != "none":
        lines.append(f"{len(due)} labels due for re-check" if due else "nothing to re-check: no label is due after the assessment")
    return {**ch, "assessment": a, "due": len(due), "lines": lines}


# ----------------------------------------------------------------- the re-check and second-reviewer queues (D12)
VALIDATION_ONLY = "This queue holds validation records only, so a reviewer can tell they are validation records."
BLIND_QUEUE_ONLY = "Blind to the queue only: the History page still lists earlier labels."
QUEUE_MODES = ("recheck", "second")
SAVE_SUMMARY = {"recheck": "saved a re-check label", "second": "saved a second-reviewer label",
                "blind": "labelled record {record_id}"}
CLOSED = "labelling of this record is closed; its labels can no longer change"


def _probability_samples(project: Project, sample_name: str | None) -> list[dict]:
    if sample_name:
        s = sample_by_name(project, sample_name)
        if s["kind"] != "probability":
            raise ValueError(f"{sample_name} is not a probability sample")
        return [s]
    return [dict(r) for r in project.store.query("SELECT * FROM samples WHERE kind='probability' ORDER BY sample_id")]


def _token(project: Project, sample_id: int, record_id: int, mode: str, reviewer: str) -> str:
    """The opaque item of one record in one reviewer's queue; the same record keeps its token, so an exported CSV
    still resolves after the queue is shown again."""
    import secrets
    st = project.store
    t = st.scalar("SELECT token FROM queue_items WHERE sample_id=? AND record_id=? AND mode=? AND reviewer=?",
                  (sample_id, record_id, mode, reviewer))
    if t:
        return t
    t = secrets.token_urlsafe(12)
    st.execute("INSERT INTO queue_items(token,sample_id,record_id,mode,reviewer,created_at) VALUES(?,?,?,?,?,?)",
               (t, sample_id, record_id, mode, reviewer, utcnow()))
    return t


def resolve_item(project: Project, token: str) -> dict:
    r = project.store.one("SELECT q.*, s.name sample FROM queue_items q JOIN samples s ON s.sample_id=q.sample_id "
                          "WHERE q.token=?", (token or "",))
    if r is None:
        raise ValueError("unknown queue item")
    return dict(r)


def _items(project: Project, pairs: list[tuple[int, int]], mode: str, reviewer: str) -> list[dict]:
    """The whitelist items {item, title, abstract}, shuffled with the project seed (D12)."""
    import random
    pairs = sorted(set(pairs))
    random.Random(f"{project.config.seed}:{mode}:{reviewer}").shuffle(pairs)
    out = []
    for sid, rid in pairs:
        m = project.store.one("SELECT title, abstract FROM records WHERE record_id=?", (rid,))
        out.append({"item": _token(project, sid, rid, mode, reviewer), "title": m["title"] if m else "",
                    "abstract": m["abstract"] if m else ""})
    return out


def recheck_queue(project: Project, reviewer: str, sample_name: str | None = None) -> dict:
    """The reviewer's labels on probability samples made due by the latest assessment (D11), any decision, no
    development records, blind and shuffled. Locked samples are served too: a re-check save is documented by its
    revision row (label-completion-and-sealing D2, D8)."""
    from .design import development_records
    dev = development_records(project)
    pairs, lines, per = [], [], {}
    for s in _probability_samples(project, sample_name):
        due = set(due_labels(project, s["name"]))
        rows = [r for r in project.store.query("SELECT label_id, record_id FROM labels WHERE sample_id=? AND reviewer_id=?",
                                               (s["sample_id"], reviewer)) if r["label_id"] in due and r["record_id"] not in dev]
        pairs += [(s["sample_id"], r["record_id"]) for r in rows]
        if rows:
            per[s["name"]] = len(rows)
    items = _items(project, pairs, "recheck", reviewer)
    if not items:
        lines.insert(0, f"nothing to re-check: no label of {reviewer} is due after the assessment")
    else:
        lines = [VALIDATION_ONLY, BLIND_QUEUE_ONLY] + lines
        for name, n in per.items():
            lines.append(f"An independent second reviewer can re-check the same {n} records: "
                         f"wider label second-reviewer {name} --reviewer R2 --recheck")
    return {"mode": "recheck", "reviewer": reviewer, "items": items, "n": len(items), "lines": lines}


def _second_pairs(project: Project, reviewer: str, s: dict, dev: set | None = None) -> dict:
    """The second-reviewer selection on one probability sample, shared by the blind queue and a review package
    (reviewer-roles-and-blinding D2), so the two cannot drift: the includes and maybes whose first label is someone
    else's, plus as many first-label excludes drawn with the project seed; never records the reviewer labelled, never
    development records. Returns {pairs, n_pos, n_exc, self_first, first} (``first``: record id to its first label)."""
    import random

    if dev is None:
        from .design import development_records
        dev = development_records(project)
    labs = [dict(r) for r in project.store.query("SELECT * FROM labels WHERE sample_id=? ORDER BY label_id", (s["sample_id"],))]
    first, mine = {}, set()
    for lab in labs:
        first.setdefault(lab["record_id"], lab)
        if lab["reviewer_id"] == reviewer:
            mine.add(lab["record_id"])
    pos = [r for r, f in first.items() if f["decision"] in ("include", "maybe") and r not in dev]
    self_first = sum(1 for r in pos if first[r]["reviewer_id"] == reviewer)
    pos = [r for r in pos if r not in mine]
    exc = sorted(r for r, f in first.items() if f["decision"] == "exclude" and r not in mine and r not in dev)
    k = min(len(pos), len(exc))
    exc = random.Random(f"{project.config.seed}:second:{reviewer}:{s['sample_id']}").sample(exc, k)
    return {"pairs": [(s["sample_id"], r) for r in pos + exc], "n_pos": len(pos), "n_exc": k, "self_first": self_first,
            "first": first}


def second_reviewer_queue(project: Project, reviewer: str, sample_name: str | None = None, recheck: bool = False) -> dict:
    """A blind queue for an independent second reviewer: the includes and maybes whose first label is someone else's,
    enriched with up to as many first-label excludes (so it does not show the first decisions), or with ``recheck``
    the records due for re-check; never records the reviewer labelled, never development records, seeded."""
    from .design import development_records
    dev = development_records(project)
    st = project.store
    pairs, lines, self_first, n_pos, n_exc = [], [], 0, 0, 0
    for s in _probability_samples(project, sample_name):   # locked samples too (D8)
        if recheck:
            labs = [dict(r) for r in st.query("SELECT * FROM labels WHERE sample_id=? ORDER BY label_id", (s["sample_id"],))]
            mine = {lab["record_id"] for lab in labs if lab["reviewer_id"] == reviewer}
            due = set(due_labels(project, s["name"]))
            recs = sorted({lab["record_id"] for lab in labs if lab["label_id"] in due} - mine - dev)
            pairs += [(s["sample_id"], r) for r in recs]
            continue
        sp = _second_pairs(project, reviewer, s, dev)
        pairs += sp["pairs"]
        self_first, n_pos, n_exc = self_first + sp["self_first"], n_pos + sp["n_pos"], n_exc + sp["n_exc"]
    if not recheck:
        if not n_pos and self_first:
            raise ValueError(f"{reviewer} made the first labels; choose another reviewer")
        if self_first:
            lines.append(f"{self_first} records you labelled first are left out")
        if n_pos:
            lines.append("This queue is enriched with first-label excludes, so it does not show the first reviewer's decisions.")
        if n_exc < n_pos:
            lines.append("this queue has fewer excludes than includes and maybes, so it is not evenly mixed")
    items = _items(project, pairs, "second", reviewer)
    if items:
        lines = [VALIDATION_ONLY, BLIND_QUEUE_ONLY] + lines
    else:
        lines.insert(0, "nothing for a second reviewer: no record to label")
    return {"mode": "second", "reviewer": reviewer, "recheck": recheck, "items": items, "n": len(items), "lines": lines}


def label_item(project: Project, token: str, reviewer: str | None, decision: str, reason: str = "",
               labels: dict | None = None, note: str = "", recheck: bool | None = None,
               correction_reason: str | None = None) -> dict:
    """Save a label from a queue item: sample, record and reviewer come from the token; a re-check token saves with
    ``recheck``. The History entry names no sample, and for a re-check or second-reviewer item no record (D6, D12)."""
    it = resolve_item(project, token)
    if not reviewer:
        raise ValueError("give your reviewer name to save a queue item")
    if reviewer != it["reviewer"]:
        raise ValueError("this queue item belongs to another reviewer")
    if recheck and it["mode"] != "recheck":
        raise ValueError("recheck is only for a re-check queue item")
    label(project, it["sample"], it["record_id"], it["reviewer"], decision, reason, labels, note,
          recheck=it["mode"] == "recheck", queue_mode=it["mode"], made_in="blind queue",
          correction_reason=correction_reason)
    return it


def export_queue_csv(queue_out: dict, path: str | Path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["item", "title", "abstract", "decision", "reason", "labels", "note"])
        for r in queue_out["items"]:
            w.writerow([r["item"], r["title"] or "", r["abstract"] or "", "", "", "", ""])
    return len(queue_out["items"])


def import_queue_csv(project: Project, path: str | Path, reviewer: str | None = None,
                     correction_reason: str | None = None) -> dict:
    """Import a filled queue export: each token resolved across samples and saved as the drawer does; a row with an
    unknown token is skipped and counted. A --reviewer other than a token's own is refused before anything is saved."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    n = Counter()
    todo = []
    for row in rows:
        try:
            it = resolve_item(project, row.get("item", ""))
        except ValueError:
            n["unknown_item"] += 1
            continue
        if reviewer and reviewer != it["reviewer"]:
            raise ValueError("this queue item belongs to another reviewer")
        todo.append(row)
    saves = []
    for row in todo:
        dec = row.get("decision", "").lower()
        if not dec:
            n["no_decision"] += 1
            continue
        if dec not in DECISIONS:
            n["bad_decision"] += 1
            continue
        labs = {}
        for part in row.get("labels", "").split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                labs[k.strip()] = v.strip()
        saves.append((row, dec, labs))
    # a change of a saved label on a locked sample or a sealed record needs a reason, asked before any row is saved;
    # the text names no sample, as the queue is blind (D2)
    changes = 0
    for row, dec, labs in saves:
        it = resolve_item(project, row["item"])
        s = sample_by_name(project, it["sample"])
        if it["mode"] != "recheck" and needs_correction(project, s, it["record_id"], it["reviewer"], dec,
                                                        row.get("reason", ""), labs, row.get("note", "")):
            changes += 1
    if changes and (correction_reason or "").strip():
        _correction_why(correction_reason, "")   # too long: refused before any row is saved (risk-checker R4)
    if changes and not (correction_reason or "").strip():
        raise CorrectionNeeded(f"{_plural(changes, '1 row changes', f'{changes} rows change')} a saved label here; "
                               f"give --correction-reason")
    for row, dec, labs in saves:
        # a queue file is the reviewer's own export: without --reviewer, its items are signed as the queue's reviewer
        label_item(project, row["item"], reviewer or resolve_item(project, row["item"])["reviewer"], dec,
                   row.get("reason", ""), labs, row.get("note", ""), correction_reason=correction_reason)
        n["imported"] += 1
    return dict(n)
