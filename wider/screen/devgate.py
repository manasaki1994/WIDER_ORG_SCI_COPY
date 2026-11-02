"""The development sample is complete before any model output on it (ui-redesign D4).

Every purposive sample (a development sample, or one imported with ``--kind purposive``) must be complete under the
review protocol for the development sample (``sampling.development_reviewers``; completeness from
:func:`wider.sample.labels.completeness`, an open development review package included) before a screening run,
evaluation, comparison or replay that includes its records. While it is not, those are refused with
:class:`DevSampleIncomplete` (exit 1, API 409), and earlier model output on its records is hidden per record."""
from __future__ import annotations

from collections.abc import Iterable

from ..core.project import Project

HIDDEN = "Model decisions on {n} development records are hidden until every record has a final label"
HIDDEN_SOME = "Model decisions on development records are hidden until every record has a final label"
LEFT_OUT = HIDDEN_SOME + "; the include, maybe and exclude counts leave them out"
LEFT_OUT_LIST = HIDDEN_SOME + "; the table and the list of records leave them out"
FILE_WAITS = "waits until the development sample is complete"
EXPORT_WAIT = "Exports with model decisions wait until the development sample is complete: {n} {records} a final label"
REPORT_HIDDEN = ("Comparison figures are hidden until the development sample is complete, so they cannot sway the "
                 "labels still open")
STORE_LEFT_OUT = "the label and history counts are left out until the development sample is complete"
PACKAGE_OUT = "a review package with development records is still out"
GATE_TEXT = ("screening runs that include development records wait until the development sample is complete, so "
             "model decisions cannot sway the labels still open; earlier model decisions on its records are hidden "
             "until then, not deleted")


def export_wait(n: int) -> str:
    return EXPORT_WAIT.format(n=n, records="record needs" if n == 1 else "records need")


class DevSampleIncomplete(ValueError):
    """A run, evaluation, comparison or export refused while a purposive sample is not complete (D4). ``counts`` is
    the body the API returns with 409: ``samples`` (name, missing, packages_out) and ``missing`` (all samples)."""

    def __init__(self, message: str, counts: dict):
        super().__init__(message)
        self.counts = counts


def _purposive(project: Project) -> list[str]:
    return [r[0] for r in project.store.query("SELECT name FROM samples WHERE kind='purposive' ORDER BY sample_id")]


def open_samples(project: Project, min_reviewers: int | None = None) -> list[dict]:
    """``completeness()`` of every purposive sample that is not complete, in draw order."""
    from ..sample.labels import completeness

    out = []
    for name in _purposive(project):
        c = completeness(project, name, min_reviewers)
        if not c["complete"] and c["total"]:   # an empty development sample has nothing to label: it waits for nothing
            out.append(c)
    return out


def _records(project: Project, names: Iterable[str]) -> set[int]:
    names = list(names)
    if not names:
        return set()
    q = ",".join("?" * len(names))
    return {r[0] for r in project.store.query(
        f"SELECT r.record_id FROM sample_rows r JOIN samples s ON s.sample_id=r.sample_id WHERE s.name IN ({q})", names)}


def hidden_ids(project: Project) -> set[int]:
    """Record ids whose model output is hidden now: every record of an incomplete purposive sample."""
    return _records(project, [c["sample"] for c in open_samples(project)])


def hidden_runs(project: Project) -> set[str]:
    """Runs with a call on a record whose model output is hidden now: their include/maybe/exclude counts are not
    shown (round card, `wider screen status`); no count of the hidden records is named (blinding)."""
    ids = hidden_ids(project)
    if not ids:
        return set()
    q = ",".join("?" * len(ids))
    return {r[0] for r in project.store.query(f"SELECT DISTINCT run_id FROM screen_calls WHERE record_id IN ({q})",
                                              tuple(ids))}


def _sentence(c: dict) -> str:
    """The refusal for one sample: the labels left (with two reviewers, how many lack a second label and how many
    disagreements wait), and the package still out."""
    name = c["sample"]
    what = "the development sample" if name == "development" else f"the development sample {name}"
    n, few, op = len(c["missing"]), len(c.get("few", [])), len(c["open"])
    out = []
    if n:
        bits = ([f"{few} lack a second reviewer's label"] if few else []) + \
               ([f"{op} {'disagreement waits' if op == 1 else 'disagreements wait'} for adjudication"] if op else [])
        out.append(f"{what} has {n} record{'' if n == 1 else 's'} without a final label"
                   + (f" ({', '.join(bits)})" if bits else "") + "; label them all first")
    if c["packages_out"]:
        out.append(PACKAGE_OUT)
    return "; ".join(out)


def check(project: Project, record_ids: Iterable[int] | None = None) -> None:
    """Raise :class:`DevSampleIncomplete` when a run on ``record_ids`` (None: the whole corpus) would include a
    record of an incomplete purposive sample. Nothing is stored or sent before this check."""
    opened = open_samples(project)
    if not opened:
        return
    if record_ids is not None:
        ids = set(record_ids)
        opened = [c for c in opened if _records(project, [c["sample"]]) & ids]
        if not opened:
            return
    raise DevSampleIncomplete("; ".join(_sentence(c) for c in opened), counts(opened))


def check_scope(project: Project, scope: str, skip_ids=None) -> None:
    """:func:`check` for a screening scope (``sample:``, ``ids:``, ``maybe:`` or the corpus); a limit is not applied,
    as the rest of the scope follows in later runs."""
    if not open_samples(project):
        return
    from .engine import record_set

    try:
        ids = {r["record_id"] for r in record_set(project, scope)} - set(skip_ids or ())
    except (KeyError, ValueError):
        return   # a bad scope is refused by the run with its own message
    check(project, ids)


def check_run(project: Project, mode: str, scope: str, protocol: str | None = None) -> None:
    """The gate for a screening request: a pass or protocol on ``scope``; a second screen on ``protocol``'s maybes."""
    check_scope(project, f"maybe:{protocol or 'main'}" if mode == "second" else scope)


def counts(opened: list[dict]) -> dict:
    return {"samples": [{"sample": c["sample"], "missing": len(c["missing"]), "packages_out": c["packages_out"]}
                        for c in opened],
            "missing": sum(len(c["missing"]) for c in opened)}


def check_export(project: Project) -> None:
    """Exports with model output wait while any purposive sample is incomplete (D4). A project that holds no model
    decision yet (stored or imported) has none to export, so its exports go ahead."""
    if not project.store.scalar("SELECT 1 FROM screen_decisions LIMIT 1"):
        return
    opened = open_samples(project)
    if opened:
        n = sum(len(c["missing"]) for c in opened)
        raise DevSampleIncomplete(export_wait(n), counts(opened))


def reviewers_effect(project: Project, n: int) -> str:
    """The line beside ``sampling.development_reviewers`` (page: for the other value, before saving; ``wider protocol
    set``: for the value just set): which purposive samples ``n`` reviewers leave incomplete, and the model output
    that is then hidden."""
    word = "one reviewer" if n == 1 else "two reviewers"
    names = _purposive(project)
    if not names:
        return f"With {word}: no purposive sample yet; the setting applies to every development sample drawn or imported."
    opened = open_samples(project, n)
    if not opened:
        return f"With {word}, every purposive sample is complete; no model output is hidden."
    st = project.store
    ids = _records(project, [c["sample"] for c in opened])
    q = ",".join("?" * len(ids)) or "NULL"
    k = st.scalar(f"SELECT COUNT(DISTINCT record_id) FROM screen_decisions WHERE record_id IN ({q})", tuple(ids)) or 0
    left = ", ".join(f"{c['sample']} ({len(c['missing'])} of {c['total']} records without a final label"
                     + (f"; {', '.join(c['packages_out'])}'s review package still out" if c["packages_out"] else "") + ")"
                     for c in opened)
    hid = (f"model decisions on {k} of their records would be hidden" if k else "no model output exists on them yet, so none would be hidden")
    return (f"With {word}, {left} {'is' if len(opened) == 1 else 'are'} not complete: {hid}, and screening runs that "
            "include their records are refused until they are labelled.")


def file_waits(project: Project, rel: str) -> bool:
    """Whether an earlier file under ``exports/`` holds model decisions and so waits while a purposive sample is
    incomplete: screening exports, decision-set references and selections (not the plain corpus export), the share
    bundle, replication packages, and a report whose ``report.json`` does not say its comparisons are hidden."""
    import json
    from pathlib import PurePosixPath

    parts = PurePosixPath(rel.replace("\\", "/")).parts
    if len(parts) < 2 or parts[0] != "exports" or not project.store.scalar("SELECT 1 FROM screen_decisions LIMIT 1") \
            or not open_samples(project):
        return False
    top, name = parts[1], parts[-1]
    if top in ("screening", "share_bundle"):
        return True
    if top == "references":
        from ..recall.outputs import CORPUS_STEM

        return not name.startswith(f"{CORPUS_STEM}.")
    if len(parts) == 2 and name.startswith("replication_package_"):
        return True
    if len(parts) == 2 and name in ("report.md", "report.json"):
        try:
            rep = json.loads(project.path("exports", "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return not rep.get("comparisons_hidden")
    return False


def gate_item(project: Project) -> dict | None:
    """The Needs attention item of a project upgraded to D4 (Overview, `wider status`): a purposive sample is not
    complete and model decisions exist on its records. Counts are left out (no development count, blinding)."""
    ids = hidden_ids(project)
    if not ids:
        return None
    q = ",".join("?" * len(ids))
    if not project.store.scalar(f"SELECT 1 FROM screen_decisions WHERE record_id IN ({q}) LIMIT 1", tuple(ids)):
        return None
    return {"stage": "sample", "title": "Samples and labelling", "kind": "dev_gate", "text": GATE_TEXT}
