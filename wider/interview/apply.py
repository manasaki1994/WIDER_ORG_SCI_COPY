"""How a refinement answer is applied (vision change 2b, D12), always on an existing path and inside the one
``answer`` change :func:`wider.core.questions.answer` records (F3):

* name-add, noise-term, variant, context-AND and the seed-not-retrieved edits write a new draft synonyms version
  through 2a's ``diagnostics.edited_data`` and ``artifacts.save_new_version`` (origin ``answer``, the note naming
  the question); a save that reopened a frozen query adds an ``unlocked`` item, so the undo is refused (F7);
* same-construct changes nothing here: the name map reads the ledger row;
* boundary-record is 2a's cluster mark, limit 2a's platform limits, screening-budget the protocol's
  ``record_budget``, the seed's indexing verdict 2a's ``seed_indexing``;
* round-stop moves the interview round or pauses the questions; "freeze this round" only opens the Freeze step.

``keep``, ``reject`` and ``leave`` change nothing beyond the ledger row.
"""
from __future__ import annotations

from typing import Any

from ..core import journal
from ..core.project import Project
from . import bank


def _label(q: dict, choice: str) -> str:
    return next((o.get("label") or choice for o in q["options"] if o["value"] == choice), choice)


def _check(data: dict, change: dict) -> None:
    """Refuse an edit the newest version no longer takes (the question was asked about an older one)."""
    from ..search.diagnostics import _norm_term
    from ..search.evaluate import Query, term_label

    query = Query.model_validate(data)
    blk = next((b for b in query.blocks if b.key == change["block"]), None)
    if blk is None:
        raise ValueError(f"block {change['block']} is no longer in the newest synonyms version")
    held = {_norm_term(term_label(t)) for t in blk.terms}
    if change["op"] == "add_term" and _norm_term(change["term"]) in held:
        raise ValueError(f"{change['term']} is already a term of block {change['block']}")
    if change["op"] in ("drop_term", "replace_piece") and _norm_term(change["term"]) not in held:
        raise ValueError(f"{change['term']} is no longer a term of block {change['block']}")


def write_version(project: Project, q: dict, choice: str, by: str) -> str:
    """Save the answer's edit as a new draft synonyms version; returns the applied text (the unlock line first)."""
    from ..construct import artifacts
    from ..search.diagnostics import edited_data

    change = ((q["context"] or {}).get("changes") or {}).get(choice)
    if not change:
        raise ValueError(f"'{choice}' carries no edit for question {q['question_id']}")
    row, data = artifacts.load(project, "synonyms")
    if row is None:
        raise ValueError("no synonyms version to edit")
    try:
        _check(data, change)
    except ValueError:   # asked about an older version that already took this edit: nothing to save (ui-tester F1)
        if change["op"] == "add_term" and change["block"] in {b.get("key") for b in data.get("blocks", [])}:
            return f"{change['term']} is already in block {change['block']} of synonyms v{row['version']}: nothing saved"
        raise
    note = f"answer to question {q['question_id']} ({q['kind'].replace('_', ' ')}): {_label(q, choice)}"
    before = project.store.scalar("SELECT MAX(change_id) FROM history WHERE kind='artifact_unlock'")
    new = artifacts.save_new_version(project, "synonyms", edited_data(data, change), origin="answer", note=note,
                                     parent_version=row["version"], by=journal.current_author(by))
    if new.get("notice"):
        cid = project.store.scalar("SELECT MAX(change_id) FROM history WHERE kind='artifact_unlock'")
        if cid != before:
            journal.side_effect({"t": "unlocked", "change_id": cid})
    text = f"synonyms v{new['version']} saved: {note}"
    return f"{new['notice']}; {text}" if new.get("notice") else text


def _nothing(choice: str) -> str:
    return {"keep": "kept as it is", "reject": "not a name for this construct: not asked again",
            "leave": "left as it is"}.get(choice, "nothing changed")


def name_add(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    return write_version(project, q, choice, by) if choice == "add" else _nothing(choice)


def same_construct(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    a, b = (q["context"] or {}).get("evidence", {}).get("names", ["?", "?"])
    return {"same": f"{a} and {b} are one group of the name map", "different": f"{a} is a look-alike of {b} in the "
            "name map"}.get(choice, _nothing(choice))


def edit(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    """Noise-term (drop), variant (change), context-AND (add): the default writes a version."""
    return write_version(project, q, choice, by) if choice in ((q["context"] or {}).get("changes") or {}) else _nothing(choice)


def seed_not_retrieved(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    from ..search import indexing

    ctx = q["context"] or {}
    if choice in (ctx.get("changes") or {}):
        return write_version(project, q, choice, by)
    if choice in ("not_indexed", "indexed"):
        bid, out = indexing.check_verdict(project, ctx["seed"], ctx["platform"], choice)
        indexing._apply_verdict(project, bid, ctx["platform"], choice, journal.current_author(by))
        return f"{bid} on {ctx['platform']}: {out['diagnosis']}"
    return _nothing(choice)


def boundary_record(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    from ..search.diagnostics import _apply_mark

    if choice not in ("relevant", "noise"):
        return _nothing(choice)
    ctx = q["context"] or {}
    _apply_mark(project, ctx["platform"], ctx["diag_version"], ctx["signature"], choice, journal.current_author(by))
    return f"{ctx['platform']} v{ctx['diag_version']} cluster \"{ctx['signature']}\" marked {choice}"


def limit(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    from ..search import limits

    ctx = q["context"] or {}
    year = (ctx.get("years") or {}).get(choice)
    if year is None:
        return _nothing(choice)
    plat, new = limits._new_limits(project, ctx["platform"], dates=f"{year}..",
                                   reason=f"answer to question {q['question_id']}: records before the seeds")
    limits._apply_limits(project, plat, new)
    return f"{ctx['platform']} {limits.limits_line(new)}"


def screening_budget(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    if choice != "raise":
        return _nothing(choice) if choice == "leave" else "budget kept; narrow the query to fit it"
    up = int((q["context"] or {})["raise_to"])
    project.config.protocol.identification.record_budget = up
    project.save()
    return f"record budget set to {up}"


def round_stop(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    st = project.store
    rnd = bank.interview_round(project)
    if choice == "stop":
        st.set_meta(bank.PAUSE_KEY, "1")
        return "no refinement question until the next dry run or import"
    st.set_meta(bank.ROUND_KEY, str(rnd + 1))
    st.set_meta(bank.PAUSE_KEY, "0")
    if choice == "freeze":
        return (f"interview round {rnd} closed; freeze the query on the Construct workbench (Freeze) or with "
                "`wider construct freeze synonyms <version>`: nothing was frozen by this answer")
    return f"interview round {rnd + 1} started"


APPLY: dict[str, Any] = {
    "refine_name_add": name_add, "refine_same_construct": same_construct, "refine_noise_term": edit,
    "refine_variant": edit, "refine_context_and": edit, "refine_seed_not_retrieved": seed_not_retrieved,
    "refine_boundary_record": boundary_record, "refine_limit": limit, "refine_screening_budget": screening_budget,
    "refine_round_stop": round_stop,
}


def watch(project: Project, rec, q: dict) -> None:
    """Everything a refinement answer can change, so one undo puts it all back."""
    from ..construct import artifacts

    ctx = q["context"] or {}
    newest = max((r["version"] for r in artifacts.list_versions(project, "synonyms")), default=0)
    rec.rows("artifacts", "kind='synonyms' AND version>?", [newest])
    rec.rows("query_rounds", "round_no>?", [newest])
    rec.rows("decisions", "dedupe_key=?", [q["dedupe_key"]])
    if q["kind"] == "refine_boundary_record":
        rec.rows("cluster_marks", "platform=? AND version=? AND signature=?",
                 [ctx.get("platform"), ctx.get("diag_version"), ctx.get("signature")])
    elif q["kind"] == "refine_seed_not_retrieved":
        rec.rows("seed_indexing", "benchmark_id=? AND platform=?", [ctx.get("seed"), ctx.get("platform")])
    elif q["kind"] == "refine_round_stop":
        rec.rows("meta", "key IN (?,?)", [bank.ROUND_KEY, bank.PAUSE_KEY])
