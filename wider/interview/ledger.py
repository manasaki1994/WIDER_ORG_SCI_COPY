"""The decision ledger (vision change 2b, D12): every refinement answer, accepted default and dismissal, and every
name admission, as one row of the ``decisions`` table with the kind, the dedupe key, the evidence, the options, the
default, the chosen option, the answer mode (``chosen``, ``default``, ``dismissed``), the consequence, the author,
the time and the round (the synonyms version, D8).

Evidence from a seed PDF is held as references (seed id, section, offset, the quote's sha256), never the quote's
text (Q3). :func:`export` writes ``runs/query/decisions.jsonl`` and ``decisions.csv``; 2c copies them into the
package. :func:`prompt_texts` turns the decisions into the "Decisions from the researcher" lines of the synonyms
proposal and mining prompts (the newest 30, oldest first): the name or term, the reason, the choice and the
researcher's note, never a quote.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow

MODES = ("chosen", "default", "dismissed")
PROMPT_LIMIT = 30
COLUMNS = ["decision_id", "kind", "dedupe_key", "mode", "default", "chosen", "consequence", "author", "at",
           "round_no", "evidence_json", "options_json"]


def record(project: Project, kind: str, dedupe_key: str, evidence: dict, options: list, default: str | None,
           chosen: str | None, mode: str, consequence: str, author: str, round_no: int | None) -> int:
    """Insert one ledger row (inside the caller's journalled change, which watches the row)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    cur = project.store.execute(
        'INSERT INTO decisions(kind, dedupe_key, evidence_json, options_json, "default", chosen, mode, consequence, '
        "author, at, round_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (kind, dedupe_key, json.dumps(evidence, ensure_ascii=False, default=str), json.dumps(options, ensure_ascii=False),
         default, chosen, mode, consequence, author, utcnow(), round_no))
    return int(cur.lastrowid)


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
    d["options"] = json.loads(d.pop("options_json") or "[]")
    d["text"] = text(d)
    return d


def rows(project: Project, round_no: int | None = None) -> list[dict[str, Any]]:
    """The ledger, oldest first (``round_no`` keeps one round)."""
    sql, params = "SELECT * FROM decisions", []
    if round_no is not None:
        sql += " WHERE round_no=?"
        params.append(round_no)
    return [_row(r) for r in project.store.query(sql + " ORDER BY decision_id", params)]


def _label(options: list, value: str | None) -> str:
    for o in options:
        if isinstance(o, dict) and o.get("value") == value:
            return o.get("label") or value or ""
    return value or ""


def text(d: dict[str, Any], author: bool = True) -> str:
    """One line for a decision: what it was about, why it was asked, what was decided and the researcher's note.
    ``author=False`` leaves the researcher's name out (model prompts, researcher-attribution D9)."""
    ev = d.get("evidence") or {}
    if d["kind"] == "name_admit":
        cons = d["consequence"] or ""
        if not author and d.get("author"):
            cons = cons.replace(f"accepted by {d['author']}", "accepted with a note")
        return f"name {ev.get('name') or d['dedupe_key'].split(':', 1)[-1]} admitted to block {d['chosen']}: {cons}"
    about = ev.get("subject") or d["dedupe_key"]
    why = f" ({ev['why']})" if ev.get("why") else ""
    if d["mode"] == "dismissed":
        return f"{about}{why}: dismissed"
    got = _label(d.get("options") or [], d.get("chosen"))
    note = f"; note: {ev['note']}" if ev.get("note") else ""
    return f"{about}{why}: {got}{' (the default)' if d['mode'] == 'default' else ''}{note}"


def prompt_texts(project: Project, limit: int = PROMPT_LIMIT) -> list[str]:
    """The decisions as clarifications of scope ``query``: the newest ``limit`` not dismissed, oldest first."""
    if "decisions" not in project.store.tables():
        return []
    rs = project.store.query("SELECT * FROM decisions WHERE mode != 'dismissed' ORDER BY decision_id DESC LIMIT ?",
                             (limit,))
    return [text(_row(r), author=False) for r in reversed(rs)]


def lines(rs: list[dict[str, Any]]) -> list[str]:
    """`wider decisions list` as text; the Questions page's "Decisions" list shows the same lines."""
    if not rs:
        return ["no decisions yet"]
    return [f"{d['decision_id']}. round {d['round_no'] if d['round_no'] is not None else '-'} · {_kind_words(d['kind'])} · "
            f"{MODE_WORDS.get(d['mode'], d['mode'])} {'by ' + d['author'] if d['author'] else '(no name given)'}: {d['text']}"
            for d in rs]


MODE_WORDS = {"chosen": "answered", "default": "default accepted", "dismissed": "dismissed"}


def _kind_words(kind: str) -> str:
    """``refine_noise_term`` → "noise term"; ``name_admit`` → "name admitted"."""
    return "name admitted" if kind == "name_admit" else kind.removeprefix("refine_").replace("_", " ")


def export_texts(project: Project, attributor: Any = None) -> tuple[str, str]:
    """``(decisions.jsonl, decisions.csv)`` as text; with ``attributor`` (the package's copy) the author and the
    consequence are written as the attribution setting says (researcher-attribution D5)."""
    rs = [dict(r) for r in project.store.query("SELECT * FROM decisions ORDER BY decision_id")]
    if attributor is not None:
        for r in rs:
            own = [r["author"]] if r["author"] else []
            r["consequence"] = attributor.text(r["consequence"], own)
            r["author"] = attributor.who(r["author"])
            ev = json.loads(r["evidence_json"] or "{}")
            if isinstance(ev, dict) and isinstance(ev.get("note"), str):
                ev["note"] = attributor.text(ev["note"], own)
            r["evidence_json"] = json.dumps(ev, ensure_ascii=False)
    jl = "".join(json.dumps({**{k: r[k] for k in COLUMNS if not k.endswith("_json")},
                             "evidence": json.loads(r["evidence_json"] or "{}"),
                             "options": json.loads(r["options_json"] or "[]")}, ensure_ascii=False) + "\n" for r in rs)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rs:
        w.writerow([r[k] if r[k] is not None else "" for k in COLUMNS])
    return jl, buf.getvalue()


def export(project: Project) -> dict[str, Any]:
    """Write ``runs/query/decisions.jsonl`` (one decision per line) and ``decisions.csv``, working files that keep
    names as recorded; returns their paths."""
    jl, cs = export_texts(project)
    d = project.root / "runs" / "query"
    d.mkdir(parents=True, exist_ok=True)
    (d / "decisions.jsonl").write_text(jl, encoding="utf-8")
    (d / "decisions.csv").write_text(cs, encoding="utf-8")
    return {"decisions": len(jl.splitlines()), "files": ["runs/query/decisions.jsonl", "runs/query/decisions.csv"]}
