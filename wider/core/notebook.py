"""A step's notebook: every change to the step, newest first, dated and signed, read from what is already stored
(runs in ``stage_results``, decisions and their undos in ``history``). It stores nothing (design-pass-a1-dedup D10)."""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from .journal import undone_now
from .provenance import local_time

if TYPE_CHECKING:
    from .project import Project

STAGES = ("dedup",)   # in A1 only Deduplication has a notebook

DECISION_WORDS = {"duplicate": "Duplicate", "not duplicate": "Not duplicate", "uncertain": "Unsure"}
# the fields of the pair view, in its order, with their names in a sentence (plural ones take "differ")
FIELDS = [("title", "title"), ("authors", "authors"), ("year", "year"), ("journal", "journal"),
          ("volume", "volume"), ("pages", "pages"), ("doi", "DOI"), ("abstract", "abstract")]
PLURAL = {"authors", "pages"}
SHOWN = {"differs", "nearly", "only_a", "only_b"}   # never "spelling" or "missing"


def _named_field(reason: str | None) -> str:
    """The field a candidate reason names: the DOI for a DOI reason, else the title."""
    return "doi" if (reason or "").startswith("exact_doi") or reason == "verified_doi_alias_full_citation" else "title"


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:]


def kept_reason(project: Project, a: int, b: int, reason: str | None) -> str:
    """"same title, but the journal differs": the candidate reason in words, then the first other field of the pair
    view whose match word is "differs", "nearly" or one side only."""
    from ..dedup.engine import match_word, reason_words

    cols = ", ".join(f for f, _ in FIELDS)
    rows = {r["raw_id"]: dict(r) for r in project.store.query(
        f"SELECT raw_id, {cols} FROM raw_records WHERE raw_id IN (?,?)", (a, b))}
    words = _lower_first(reason_words(reason))
    if a not in rows or b not in rows:
        return words
    skip = _named_field(reason)
    for field, name in FIELDS:
        if field != skip and match_word(rows[a][field], rows[b][field]) in SHOWN:
            return f"{words}, but the {name} {'differ' if field in PLURAL else 'differs'}"
    return words


def _run_entries(project: Project, limit: int) -> list[dict[str, Any]]:
    out = []
    for r in project.store.query("SELECT result_id, ran_at, result FROM stage_results WHERE stage='dedup' "
                                 "ORDER BY result_id DESC LIMIT ?", (limit,)):
        res = json.loads(r["result"])
        head = (res.get("headline") or "").replace("duplicates merged", "duplicates removed")
        out.append({"at": r["ran_at"], "who": "WIDER", "kind": "run", "text": f"Deduplication ran: {head}".strip(),
                    "undone": False, "change_id": None, "result_id": r["result_id"],
                    "result": {k: res.get(k) for k in ("headline", "metrics", "warnings", "outputs", "ran_at")}})
    return out


_PAIR = re.compile(r"^raw records (\d+) and (\d+): (.+)$")


def _change_text(row: dict) -> str:
    if row["kind"] == "dedup_decision":
        m = _PAIR.match(row["summary"])
        if m:
            dec, _, kept = m[3].partition(", keep ")   # "duplicate, keep raw record 4" (P8)
            return f"Raw records {m[1]} and {m[2]}: {DECISION_WORDS.get(dec, dec)}" + (f", keep {kept}" if kept else "")
        return row["summary"]
    s = row["summary"]
    return s[:1].upper() + s[1:]


def _change_entries(project: Project, pair: tuple[int, int] | None) -> list[dict[str, Any]]:
    """Decisions (of ``pair`` only, when given) and the undos of them, and the undos of those undos (the redos)."""
    keep: set[int] = set()
    out = []
    prefix = f"raw records {pair[0]} and {pair[1]}:" if pair else None
    for r in project.store.query("SELECT change_id, at, author, kind, summary, undone_by, undo_of FROM history "
                                 "WHERE kind IN ('dedup_decision','undo') ORDER BY change_id"):
        row = dict(r)
        if row["kind"] == "dedup_decision":
            if prefix and not row["summary"].startswith(prefix):
                continue
        elif row["undo_of"] not in keep:
            continue
        keep.add(row["change_id"])
        out.append({"at": row["at"], "who": row["author"] or "", "kind": "decision" if row["kind"] == "dedup_decision"
                    else ("redo" if row["summary"].startswith("redo: ") else "undo"),
                    "text": _change_text(row), "undone": undone_now(project, row) is not None,
                    "change_id": row["change_id"], "result_id": None})
    return out


def _pair_run_entries(project: Project, a: int, b: int) -> list[dict[str, Any]]:
    """What the last run did with the raw pair, signed "WIDER": kept it for the researcher, removed a record."""
    from ..dedup.engine import MANUAL_METHODS, reason_words, run_queue

    st = project.store
    run_at = st.get_meta("dedup_run_at")
    if not run_at:
        return []
    out = []

    def entry(text, at, seq):
        return {"at": at or run_at, "who": "WIDER", "kind": "run", "text": text, "undone": False, "change_id": None,
                "result_id": None, "_seq": seq}

    v = run_queue(project)
    if v is not None:
        for e in v.get("queue", []):
            if (e["a"], e["b"]) == (a, b):
                out.append(entry(f"Left for you to decide: {kept_reason(project, a, b, e.get('reason'))}", run_at, 0))
        for e in v.get("merged", []):
            if (e["a"], e["b"]) == (a, b):
                manual = e["match_method"] in MANUAL_METHODS.values()
                out.append(entry("Removed by your decision" if manual
                                 else f"Removed: {_lower_first(reason_words(e['match_method']))}", run_at, 1))
        return out
    r = st.one("SELECT stage, decision, diagnostics FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b))
    if r is None:   # a run stored before WIDER kept its queue: read the pair's own row
        return out
    d = json.loads(r["diagnostics"] or "{}")
    if d.get("queue"):
        out.append(entry(f"Left for you to decide: {kept_reason(project, a, b, r['stage'])}", run_at, 0))
    if d.get("auto_merge") and d.get("match_method"):
        out.append(entry(f"Removed: {_lower_first(reason_words(d['match_method']))}", run_at, 1))
    elif r["decision"] == "manual_same" and d.get("queue"):
        out.append(entry("Removed by your decision", run_at, 1))
    return out


def step_notebook(project: Project, stage: str, pair: tuple[int, int] | None = None,
                  limit: int = 50) -> list[dict[str, Any]]:
    """Entries ``{at, who, kind, text, undone, change_id, result_id}`` newest first (a run entry also carries its
    stored ``result``). With ``pair`` (either order), only that raw pair's decisions and their undos, and what the
    last run did with it."""
    if stage not in STAGES:
        raise KeyError(f"no notebook for the step {stage!r}")
    limit = max(1, int(limit))
    if pair is not None:
        a, b = sorted((int(pair[0]), int(pair[1])))
        entries = _pair_run_entries(project, a, b) + _change_entries(project, (a, b))
    else:
        entries = _run_entries(project, limit) + _change_entries(project, None)
    for e in entries:
        e.setdefault("_seq", 0)
    # newest first; times have whole seconds, and in the same second a run is taken to follow the decisions (the
    # researcher decides, then presses "Apply decisions and check again")
    entries.sort(key=lambda e: (str(e["at"] or "")[:19], e["kind"] == "run", e["change_id"] or 0,
                                e.get("result_id") or 0, e["_seq"]), reverse=True)
    for e in entries:
        e.pop("_seq", None)
    return entries[:limit]


def entry_line(e: dict[str, Any]) -> str:
    """One entry as the CLI prints it: date, signer, words, "(undone)"."""
    who = e["who"] or "signer not recorded"
    tail = " (undone)" if e["undone"] else ""
    ref = f"  [change {e['change_id']}]" if e.get("change_id") else (f"  [result {e['result_id']}]" if e.get("result_id") else "")
    return f"{local_time(e['at'], seconds=True)}  {who}  {e['text']}{tail}{ref}"
