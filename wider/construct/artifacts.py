"""Versioned, human-approved construct artifacts stored as YAML under artifacts/<kind>/v<N>.yaml.

Kinds: cells, codebook, synonyms (query IR), anchors, near_miss, adjacent. Status: draft → approved → frozen.
Freezing a query (synonyms) is what unlocks the validation benchmark set in the coverage check. "The frozen query" is
the version frozen most recently (``frozen_no``) unless an unlock or a save after it reopened it; every unlock (an
unlock, a save after a freeze, freezing another version, a file added while the uploads are final) is listed in
``query_unlocks`` and is final (D2).
"""
from __future__ import annotations

import contextlib
import contextvars
import difflib
import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ..core.project import Project
from ..core.provenance import next_version, sha256_text, utcnow

KINDS = ("cells", "codebook", "synonyms", "anchors", "near_miss", "adjacent", "prompts")
SCHEMA_DIR = Path(__file__).parent / "schemas"
UNLOCK_CAUSES = ("unlock", "save", "freeze", "uploads")
REOPENING = ("unlock", "save", "freeze")   # causes whose row reopens the freeze it names (``frozen_no``)
SEEN_CHOICES = ("count", "keep")
CHOOSE_LINE = "Choose one: --count-seen or --keep-validation. Nothing was done."
CHOICE_TAIL = ("Count them as seen: they no longer validate the search; only papers added to Validation later can. "
               "Keep them as validation: they still count, and every output states that they were shown. "
               "The choice is final.")


class SeenChoiceNeeded(ValueError):
    """The action needs the researcher's choice on whether the validation papers shown count as seen (D2, D21):
    nothing was done; ``notice`` states both outcomes (CLI exit 2, HTTP 400)."""

    def __init__(self, notice: str):
        super().__init__(notice)
        self.notice = notice


class RevisionNeedsConfirm(ValueError):
    """Saving a new synonyms version now revises the query after a look that kept the papers as validation (D2 of
    validation-reuse): nothing was saved; ``notice`` says the papers will be recorded as used (CLI exit 2)."""

    def __init__(self, notice: str):
        super().__init__(notice)
        self.notice = notice


REVISION_CONFIRM = "Add --confirm to save it. Nothing was done."
_REVISION_CONFIRMED: contextvars.ContextVar[bool] = contextvars.ContextVar("wider_revision_confirmed", default=False)


@contextlib.contextmanager
def revision_confirmed(yes: bool = True):
    """Inside the block every synonyms save counts as confirmed (for callers that reach the save through a table of
    appliers, such as an answered question)."""
    token = _REVISION_CONFIRMED.set(bool(yes) or _REVISION_CONFIRMED.get())
    try:
        yield
    finally:
        _REVISION_CONFIRMED.reset(token)


_SAVE_SEEN_CHOICE: contextvars.ContextVar[str | None] = contextvars.ContextVar("wider_save_seen_choice", default=None)


@contextlib.contextmanager
def save_seen_choice(value: str | None):
    """Inside the block every synonyms save and unlock that asks whether the validation papers shown count as seen
    takes ``value`` (``count`` | ``keep``; None keeps the outer one), for callers that reach the save through a table
    of appliers (an answered question, an applied edit, an admitted seed, a proposal; unlock-seen-choice D4)."""
    v = seen_choice(value)
    token = _SAVE_SEEN_CHOICE.set(v or _SAVE_SEEN_CHOICE.get())
    try:
        yield
    finally:
        _SAVE_SEEN_CHOICE.reset(token)


def papers_phrase(n: int, verb_one: str = "", verb_many: str = "") -> str:
    """'1 validation paper' / '14 validation papers', with the verb agreeing when given."""
    word = "paper" if n == 1 else "papers"
    verb = (verb_one if n == 1 else verb_many)
    return f"{n} validation {word}" + (f" {verb}" if verb else "")


def outcome_line(choice: str, n: int) -> str:
    """The line after a choice: what it did to the papers shown so far."""
    if choice == "count":
        return f"Counted as seen: the {papers_phrase(n, 'shown so far no longer validates', 'shown so far no longer validate')} the search."
    return f"Kept as validation: the {papers_phrase(n, 'shown so far still counts', 'shown so far still count')}; every output states this choice."


def seen_choice(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if value not in SEEN_CHOICES:
        raise ValueError("seen_choice must be count or keep")
    return value


_valid_choice = seen_choice   # for functions whose ``seen_choice`` parameter hides it


def _tx(project: Project):
    """One transaction, unless the caller already holds one (an unlock saving its copy)."""
    return contextlib.nullcontext() if project.store.conn.in_transaction else project.store.tx()


def schema_for(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{kind}.json").read_text(encoding="utf-8"))


def validate(kind: str, data: Any) -> str | None:
    try:
        jsonschema.validate(data, schema_for(kind))
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        return f"{path}: {e.message}"[:400]
    if kind == "synonyms":
        from ..search.ir import Query
        try:
            Query.model_validate(data)
        except ValueError as e:
            return str(e)[:400]
    if kind == "anchors":
        if not data.get("anchors"):
            return "no anchors"
    return None


def artifact_path(project: Project, kind: str, version: int) -> Path:
    return project.artifact_dir(kind) / f"v{version}.yaml"


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110)


def outline(kind: str) -> str:
    """A starting text to write ``kind`` by hand (after a failed proposal, or instead of one): the fields its format
    needs, with each list holding as many empty items as it needs at least. ``wider construct outline`` prints it;
    the Concepts page puts it under Show as text."""
    if not (SCHEMA_DIR / f"{kind}.json").exists():
        raise KeyError(f"unknown kind {kind!r}")

    def fill(node: dict) -> Any:
        t = node.get("type")
        if t == "object":
            props = node.get("properties", {})
            keys = node.get("required") or list(props)
            return {k: fill(props[k]) for k in keys if k in props}
        if t == "array":
            return [fill(node.get("items", {})) for _ in range(max(1, node.get("minItems", 1)))]
        if "enum" in node:
            return node["enum"][0]
        if t in ("integer", "number"):
            return 0
        if t == "boolean":
            return False
        return ""

    return (f"# {kind}: fill in every empty value, then save. Lists can hold more items.\n"
            + dump_yaml(fill(schema_for(kind))))


def list_versions(project: Project, kind: str) -> list[dict]:
    rows = project.store.query("SELECT * FROM artifacts WHERE kind=? ORDER BY version", (kind,))
    return [dict(r) for r in rows]


def latest(project: Project, kind: str, status: str | None = None) -> dict | None:
    """Newest version, optionally restricted to a status rank (approved means approved or frozen)."""
    rows = list_versions(project, kind)
    if status == "approved":
        rows = [r for r in rows if r["status"] in ("approved", "frozen")]
    elif status:
        rows = [r for r in rows if r["status"] == status]
    return rows[-1] if rows else None


def latest_frozen(project: Project, kind: str = "synonyms") -> dict | None:
    """The version frozen most recently (highest ``frozen_no``), or None."""
    rows = [r for r in list_versions(project, kind) if r["status"] == "frozen"]
    return max(rows, key=lambda r: (r.get("frozen_no") or 0, r["version"])) if rows else None


def reopening_unlock(project: Project, kind: str = "synonyms") -> dict | None:
    """The first unlock row that reopened the latest freeze (causes unlock, save, freeze), or None."""
    lf = latest_frozen(project, kind)
    if lf is None or kind != "synonyms":
        return None
    r = project.store.one(f"SELECT * FROM query_unlocks WHERE cause IN ({','.join('?' * len(REOPENING))}) "
                          "AND frozen_no=? ORDER BY unlock_no LIMIT 1", (*REOPENING, lf.get("frozen_no")))
    return dict(r) if r else None


def query_reopened(project: Project, kind: str = "synonyms") -> bool:
    return reopening_unlock(project, kind) is not None


def frozen_query(project: Project, kind: str = "synonyms") -> dict | None:
    """The frozen query: the latest freeze unless an unlock, a save or another freeze reopened it."""
    lf = latest_frozen(project, kind)
    return None if lf is None or query_reopened(project, kind) else lf


def _n_validation(project: Project) -> int:
    from ..search.coverage import validation_papers
    return len(validation_papers(project))


def record_unlock(project: Project, cause: str, from_version: int | None, new_version: int | None,
                  by: str | None = None, choice: str | None = None, detail: str = "",
                  final_before: str | None = None, n_shown: int | None = None) -> dict:
    """Write one ``query_unlocks`` row (D2) inside one ``artifact_unlock`` change (final, never undone): every
    benchmark paper as seen at this moment, the uploads-final mark's time (then cleared) and whether the row counts
    as seen. Whenever the validation set holds papers, every cause asks: ``choice`` (``count`` | ``keep``) is the
    researcher's answer, stored as ``seen_choice``, and the row counts only for ``count`` (unlock-seen-choice D1;
    without it :class:`SeenChoiceNeeded`, a guard: callers check their plan first and raise it with their notice). With an
    empty set nothing was shown: the row is written as before (0 for cause ``uploads`` while a frozen query stands,
    else 1) with no choice. Returns the row with ``n``, the validation papers counted just before it. Call it after
    the action's input is validated. ``final_before`` and ``n_shown`` name the mark's time and the papers shown when
    the caller already cleared the mark (an undo of it), which hides the set."""
    if cause not in UNLOCK_CAUSES:
        raise ValueError(f"unknown unlock cause {cause!r}")
    from ..core import journal
    from ..core.text import clean_doi, norm_title
    from ..ingest.importer import FINAL_KEY, UNLOCK_NO_KEY

    lf, fq = latest_frozen(project), frozen_query(project)
    n = _n_validation(project) if n_shown is None else n_shown
    asked = n > 0
    choice = seen_choice(choice)
    if asked and choice is None:   # a guard: callers ask first; a paper shown since then still reaches the dialog
        raise SeenChoiceNeeded(f"Reopening now: {papers_phrase(n, 'has been shown', 'have been shown')}. {CHOICE_TAIL}")
    counts = (1 if choice == "count" else 0) if asked else (0 if cause == "uploads" and fq is not None else 1)
    frozen_no = lf.get("frozen_no") if (cause in REOPENING and lf) else None
    if cause == "uploads" and fq is not None and from_version is None:
        from_version = fq["version"]   # the frozen query that still stands, named by the validity note
    from ..search.coverage import set_members
    # each paper with the group it was listed in at this moment: "Validation" names a paper shown as a validation
    # paper (D3 of validation-reuse)
    seen = [{"benchmark_id": b.benchmark_id, "doi": clean_doi(b.doi) or "", "title_norm": norm_title(b.title) or "",
             "set": g} for g, b in set_members(project, True)]
    st = project.store
    suffix = f" ({'counted as seen' if counts else 'kept as validation'})" if asked else ""
    summary = {"unlock": f"query unlocked: v{from_version} copied into v{new_version}",
               "save": (f"query reopened: v{new_version} saved after v{from_version} was frozen" if frozen_no is not None
                        else f"uploads reopened: v{new_version} saved while the uploads were final"),
               "freeze": f"query reopened: v{new_version} frozen in place of v{from_version}",
               "uploads": f"uploads reopened: {detail or 'a file added'}"}[cause] + suffix
    with _tx(project), journal.recording(project, "artifact_unlock", summary, author=by) as rec:
        no = int(st.scalar("SELECT COALESCE(MAX(unlock_no), 0) FROM query_unlocks") or 0) + 1
        rec.rows("query_unlocks", "unlock_no=?", [no])
        rec.rows("meta", "key=?", [FINAL_KEY])
        rec.rows("meta", "key=?", [UNLOCK_NO_KEY])
        row = {"unlock_no": no, "cause": cause, "from_version": from_version, "new_version": new_version,
               "frozen_no": frozen_no, "by": journal.current_author(by), "at": utcnow(),
               "seen_json": json.dumps(seen, ensure_ascii=False),
               "final_before": final_before if final_before is not None else (st.get_meta(FINAL_KEY) or ""),
               "counts_as_seen": counts, "n_shown": n, "seen_choice": choice if asked else None}
        st.execute("INSERT INTO query_unlocks(unlock_no,cause,from_version,new_version,frozen_no,by,at,seen_json,"
                   "final_before,counts_as_seen,n_shown,seen_choice) VALUES(:unlock_no,:cause,:from_version,"
                   ":new_version,:frozen_no,:by,:at,:seen_json,:final_before,:counts_as_seen,:n_shown,:seen_choice)", row)
        st.execute("DELETE FROM meta WHERE key IN (?,?)", (FINAL_KEY, UNLOCK_NO_KEY))
    return {**row, "n": n, "change_id": rec.change_id}


def _asking(head: str, n: int) -> str:
    """'<head>; 2 validation papers have been shown. Count them as seen: …' (unlock-seen-choice D3)."""
    return f"{head}; {papers_phrase(n, 'has been shown', 'have been shown')}. {CHOICE_TAIL}"


def _chosen(asking: str, choice: str, n: int) -> str:
    """The notice after the choice: the asking line without the choice text, then the outcome line."""
    return asking.replace(" " + CHOICE_TAIL, "") + "\n" + outcome_line(choice, n)


def _save_reopens(project: Project, new_version: int) -> tuple[int | None, str, str, int] | None:
    """What saving synonyms ``new_version`` now would record: ``(from_version, notice, case, n)``, or None (D2).
    ``case`` is "frozen" (a frozen query stands), "uploads" (the uploads are final) or "look" (a look kept papers as
    validation: they are recorded as used, D2 of validation-reuse); ``n`` is the papers in the validation set. For
    "frozen" and "uploads" with ``n`` > 0 the notice asks whether they count as seen (unlock-seen-choice D3)."""
    fq = frozen_query(project)
    n = _n_validation(project)
    if fq is not None:
        head = f"v{fq['version']} is frozen. Saving v{new_version} reopens the query"
        return fq["version"], (_asking(head, n) if n else f"{head}; no validation paper has been seen yet."), "frozen", n
    if not project.config.protocol.identification.validation_after_freeze:
        return None
    from ..search.coverage import revision_notice as look_notice
    from ..search.coverage import validation_unlocked
    if not validation_unlocked(project):
        notice = look_notice(project, new_version)
        return (None, notice, "look", n) if notice else None
    newest = latest(project, "synonyms")
    head = f"The uploads are final. Saving v{new_version} reopens them"
    return (newest["version"] if newest else None), (
        _asking(head, n) if n else f"{head}; no validation paper has been shown yet."), "uploads", n


def save_plan(project: Project, kind: str = "synonyms") -> tuple[int | None, str, str, bool] | None:
    """What a save of a new ``kind`` version would record now: ``(from_version, notice, case, choice_needed)``, or
    None. ``choice_needed`` is True when the save reopens the frozen query or the final uploads while the validation
    set holds papers: the researcher must choose whether they count as seen (unlock-seen-choice D4)."""
    if kind != "synonyms":
        return None
    hit = _save_reopens(project, next_version(project.artifact_dir(kind), ".yaml"))
    if hit is None:
        return None
    return hit[0], hit[1], hit[2], hit[2] in ("frozen", "uploads") and hit[3] > 0


def save_choice(project: Project, kind: str = "synonyms") -> bool:
    """True when a save of a new ``kind`` version now needs the seen choice (the page shows three buttons)."""
    plan = save_plan(project, kind)
    return bool(plan and plan[3])


def need_save_choice(project: Project, kind: str = "synonyms", value: str | None = None) -> str | None:
    """The choice a save now uses (``value``, else the one of the enclosing :func:`save_seen_choice`), or None when
    the save asks nothing; raises :class:`SeenChoiceNeeded` with the notice when it asks and no choice is given.
    Callers that do work before the save (a model call) check with it first."""
    choice = seen_choice(value) or _SAVE_SEEN_CHOICE.get()
    plan = save_plan(project, kind)
    if not (plan and plan[3]):
        return None
    if choice is None:
        raise SeenChoiceNeeded(plan[1])
    return choice


def save_notice(project: Project, kind: str = "synonyms") -> str | None:
    """The notice a save of a new synonyms version would give now (the page shows it in a confirm step first)."""
    if kind != "synonyms":
        return None
    hit = _save_reopens(project, next_version(project.artifact_dir(kind), ".yaml"))
    return hit[1] if hit else None


def revision_notice(project: Project, kind: str = "synonyms") -> str | None:
    """The warning a save of a new synonyms version needs a confirmation for (the "look" case alone, D2 of
    validation-reuse), or None. CLI commands that save ask it before doing anything else."""
    from ..search.coverage import kept_unused_looks
    if kind != "synonyms" or not kept_unused_looks(project):   # the usual case reads no version number
        return None
    hit = _save_reopens(project, next_version(project.artifact_dir(kind), ".yaml"))
    return hit[1] if hit and hit[2] == "look" else None


def load(project: Project, kind: str, version: int | None = None) -> tuple[dict | None, Any]:
    row = latest(project, kind) if version is None else next(
        (r for r in list_versions(project, kind) if r["version"] == version), None)
    if row is None:
        return None, None
    p = project.root / row["path"]
    return row, yaml.safe_load(p.read_text(encoding="utf-8"))


def save_new_version(project: Project, kind: str, data: Any, origin: str, note: str = "",
                     parent_version: int | None = None, status: str = "draft", by: str | None = None,
                     reopen: bool = True, confirm_revision: bool = False, seen_choice: str | None = None) -> dict:
    """Write a new version. A synonyms save while a frozen query stands (or while the uploads are final with the
    validation set gated) records an unlock with cause ``save`` in the same transaction; the row returned then has
    ``notice`` (D2). When the validation set holds papers that save needs ``seen_choice`` (``count`` | ``keep``, or
    the enclosing :func:`save_seen_choice`); without it :class:`SeenChoiceNeeded` is raised before anything is
    written (unlock-seen-choice D4). After a look that kept papers as validation it records them as used (a final
    ``validation_used`` change), and without ``confirm_revision`` raises :class:`RevisionNeedsConfirm` before writing
    anything (D2 of validation-reuse). ``reopen=False`` only for :func:`unlock`, which records its own row."""
    if kind not in KINDS:
        raise ValueError(f"unknown artifact kind {kind!r}")
    err = validate(kind, data)
    if err:
        raise ValueError(f"{kind} does not validate: {err}")
    if kind == "synonyms" and reopen and not (confirm_revision or _REVISION_CONFIRMED.get()):
        warn = revision_notice(project, kind)
        if warn:
            raise RevisionNeedsConfirm(warn)
    choice = need_save_choice(project, kind, seen_choice) if (kind == "synonyms" and reopen) else None
    d = project.artifact_dir(kind)
    d.mkdir(parents=True, exist_ok=True)
    v = next_version(d, ".yaml")
    text = dump_yaml(data)
    while True:  # two saves at once (a double click, the page and the CLI) each claim their own file, never the same version
        p = d / f"v{v}.yaml"
        try:
            with p.open("x", encoding="utf-8") as fh:
                fh.write(text)
            break
        except FileExistsError:
            v += 1
    rel = str(p.relative_to(project.root))
    if parent_version is None:
        prev = latest(project, kind)
        parent_version = prev["version"] if prev else None
    row = {"kind": kind, "version": v, "path": rel, "sha256": sha256_text(text), "status": status,
           "origin": origin, "parent_version": parent_version, "note": note, "created_at": utcnow()}
    notice, used_change = None, None
    try:
        with _tx(project):
            hit = _save_reopens(project, v) if (kind == "synonyms" and reopen) else None
            if hit and hit[2] == "look":
                from ..search.coverage import mark_used
                used_change = mark_used(project, v, by)
                notice = hit[1]
            elif hit:
                if hit[3] > 0 and choice is None:   # the set changed between the check and the write
                    raise SeenChoiceNeeded(hit[1])
                record_unlock(project, "save", hit[0], v, by, choice=choice)
                notice = _chosen(hit[1], choice, hit[3]) if hit[3] > 0 else hit[1]
            project.store.execute(
                "INSERT INTO artifacts(kind,version,path,sha256,status,origin,parent_version,note,created_at) "
                "VALUES(:kind,:version,:path,:sha256,:status,:origin,:parent_version,:note,:created_at)", row)
            if kind == "synonyms":   # every saved synonyms version is a round (D8)
                from ..search.rounds import register
                register(project, v, row["created_at"])
    except Exception:
        p.unlink(missing_ok=True)   # no version row, no file: the save did not happen
        raise
    if used_change:   # on the change enclosing this save, if any: it cannot be undone without the used mark
        from ..core import journal
        journal.side_effect({"t": "validation_used", "change_id": used_change})
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_artifact
    record(project, summarize_artifact(row, "propose" if origin.startswith("llm") else ("import" if origin == "import" else "save")))
    row["notice"] = notice
    return row


def save_edit(project: Project, kind: str, text: str, note: str = "", by: str | None = None,
              confirm_revision: bool = False, seen_choice: str | None = None) -> dict:
    """A human edit: parse YAML text and store it as a new manual version."""
    data = yaml.safe_load(text)
    return save_new_version(project, kind, data, origin="manual", note=note, by=by, confirm_revision=confirm_revision,
                            seen_choice=seen_choice)


def _swap_clauses(project: Project, kind: str, version: int) -> str:
    """'v2 is now the frozen query; draft v3 is kept and is not the searched query.' (one clause per newer version)."""
    parts = []
    for r in list_versions(project, kind):
        if r["version"] > version:
            parts.append(f"v{r['version']} stays frozen" if r["status"] == "frozen" else f"{r['status']} v{r['version']} is kept")
    return f"v{version} is now the frozen query" + "".join(f"; {c} and is not the searched query" for c in parts) + "."


def freeze_plan(project: Project, kind: str, version: int) -> dict:
    """What freezing ``kind`` v``version`` would do now (D2): ``already`` (it is the frozen query), ``choice`` (the
    researcher must choose whether the validation papers shown count as seen, Q3), ``asking`` (that notice),
    ``clauses`` (the swap line, or None for a plain first freeze), ``n`` and the frozen query's version."""
    row = next((r for r in list_versions(project, kind) if r["version"] == version), None)
    if row is None:
        raise KeyError(f"{kind} v{version} does not exist")
    out = {"already": False, "choice": False, "asking": None, "clauses": None, "n": 0, "replaced": None}
    if kind != "synonyms":
        return out
    fq = frozen_query(project, kind)
    if fq is not None and fq["version"] == version:
        return {**out, "already": True}
    n = _n_validation(project) if fq is not None else 0
    out["n"] = n
    if fq is not None:
        out["replaced"] = fq["version"]
    if fq is not None and n > 0:
        out["choice"] = True
        out["asking"] = (f"v{fq['version']} is the frozen query. Freezing v{version} instead changes the searched query; "
                         f"{papers_phrase(n, 'has been shown', 'have been shown')}. {CHOICE_TAIL}")
    newer = any(r["version"] > version for r in list_versions(project, kind))
    if newer or row["status"] == "frozen" or fq is not None:
        out["clauses"] = _swap_clauses(project, kind, version)
    return out


def lint_line(project: Project, kind: str, version: int, done: bool = False) -> str | None:
    """The warning a freeze of synonyms v``version`` shows when the syntax check finds errors (inform, never block):
    how many, and the first one; None when there are none or the version is not a query. ``done``: said after the
    freeze (the query was frozen with them) instead of before it."""
    if kind != "synonyms":
        return None
    from ..search.ir import Query
    from ..search.lint import lint

    try:
        q = Query.model_validate(load(project, kind, version)[1])
    except Exception:
        return None
    errs = [f for f in lint(q) if f["level"] == "error"]
    if not errs:
        return None
    first = errs[0]
    where = f"block {first['block']}" + (f", {first['term']}" if first["term"] else "")
    n = len(errs)
    it = "them" if n != 1 else "it"
    head = (f"Warning: the syntax check finds {n} error{'s' if n != 1 else ''} in v{version} (first: {first['message']}"
            f" in {where}). ")
    if done:
        return head + (f"v{version} was frozen with {it}, and the search strings carry {it} as written; to fix "
                       f"{it}, save a new version in the editor and freeze that.")
    return head + f"The search strings carry {it} as written; you can freeze anyway or fix {it} in the editor first."


def freeze_notice(project: Project, kind: str, version: int) -> str | None:
    """The text the freeze confirm step shows (the syntax-check warning, then the asking notice when a choice is
    needed), or None."""
    plan = freeze_plan(project, kind, version)
    if plan["already"]:
        return f"v{version} is already the frozen query"
    lines = [x for x in (lint_line(project, kind, version), plan["asking"], plan["clauses"]) if x]
    return "\n".join(lines) or None


def set_status(project: Project, kind: str, version: int, status: str, seen_choice_value: str | None = None,
               by: str | None = None) -> dict:
    """Change a version's status; a freeze takes the next ``frozen_no``. Freezing another synonyms version while a
    frozen query stands with papers in its validation set needs ``seen_choice_value`` (``count`` | ``keep``; else
    :class:`SeenChoiceNeeded`, nothing done) and records an unlock with cause ``freeze`` in the same transaction.
    The row returned has ``notice`` (the lines to show, or None)."""
    if status not in ("draft", "approved", "frozen"):
        raise ValueError("status must be draft, approved or frozen")
    choice = seen_choice(seen_choice_value)
    row = next((r for r in list_versions(project, kind) if r["version"] == version), None)
    if row is None:
        raise KeyError(f"{kind} v{version} does not exist")
    if row["status"] == "frozen" and status != "frozen":
        raise ValueError(f"{kind} v{version} is frozen; create a new version instead")
    p = project.root / row["path"]
    if sha256_text(p.read_text(encoding="utf-8")) != row["sha256"]:
        raise ValueError(f"{row['path']} was edited on disk after it was saved; save it as a new version first")
    plan = freeze_plan(project, kind, version) if status == "frozen" else None
    if plan and plan["already"]:
        row["notice"] = f"v{version} is already the frozen query"
        return row
    if plan and plan["choice"] and choice is None:
        raise SeenChoiceNeeded(plan["asking"])
    refreeze = status == "frozen" and (row["status"] != "frozen" or kind == "synonyms")
    if row["status"] == status and not refreeze:
        row["notice"] = None
        return row
    from ..core.journal import recording
    with _tx(project):
        if plan and plan["choice"]:
            record_unlock(project, "freeze", plan["replaced"], version, by, choice=choice)
        with recording(project, "artifact_status", f"{kind} v{version}: {row['status']} → {status}", author=by) as rec:
            rec.rows("artifacts", "kind=? AND version=?", (kind, version))
            if refreeze:
                no = int(project.store.scalar("SELECT COALESCE(MAX(frozen_no), 0) FROM artifacts WHERE kind=?", (kind,)) or 0) + 1
                project.store.execute("UPDATE artifacts SET status=?, frozen_no=? WHERE kind=? AND version=?",
                                      (status, no, kind, version))
                row["frozen_no"] = no
            else:
                project.store.execute("UPDATE artifacts SET status=? WHERE kind=? AND version=?", (status, kind, version))
        if kind == "synonyms" and status == "frozen":   # a freeze needs no accepted round; it accepts its own (D0, D8)
            from ..search.rounds import accept_by_freeze
            row["accepted_line"] = accept_by_freeze(project, version, by)
    row["status"] = status
    lines = []
    if plan and plan["choice"]:
        lines.append(outcome_line(choice, plan["n"]))
    if plan and plan["clauses"]:
        lines.append(plan["clauses"])
    if status == "frozen" and (warn := lint_line(project, kind, version, done=True)):
        lines.append(warn)
    row["notice"] = "\n".join(lines) or None
    if kind == "synonyms" and status == "frozen":   # the strings that will run go to the search-strings archive
        from ..search.archive import archive
        try:
            archive(project, version, reason="synonyms frozen")
        except ValueError:
            pass
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_artifact
    record(project, summarize_artifact(dict(row), "status"))
    return row


def _date(at: str | None) -> str:
    return (at or "")[:10]


def unlock_plan(project: Project, kind: str, version: int, new_version: int | None = None) -> dict:
    """What unlocking ``kind`` v``version`` would do now: ``asking`` (the notice), ``choice`` (True when the
    validation set holds papers and the unlock reopens the query: the researcher must choose whether they count as
    seen, unlock-seen-choice D4) and ``n``. Refuses like :func:`unlock` (ValueError, KeyError)."""
    if kind != "synonyms":
        raise ValueError("only synonyms can be unlocked")
    row = next((r for r in list_versions(project, kind) if r["version"] == version), None)
    if row is None:
        raise KeyError(f"{kind} v{version} does not exist")
    if row["status"] != "frozen":
        raise ValueError(f"v{version} is a draft; edit and save it, no unlock needed" if row["status"] == "draft"
                         else f"v{version} is {row['status']}, not frozen; edit and save it, no unlock needed")
    new = new_version or next_version(project.artifact_dir(kind), ".yaml")
    again = reopening_unlock(project, kind)
    if again:
        return {"asking": f"the query is already reopened (unlock {again['unlock_no']} on {_date(again['at'])}); "
                          f"v{new} is a copy of v{version}", "choice": False, "n": 0}
    n = _n_validation(project)
    fq = frozen_query(project, kind)
    if fq is not None and fq["version"] != version:
        head = f"v{fq['version']} is the frozen query; unlocking v{version} reopens it and copies v{version} into v{new}"
        return {"asking": _asking(head, n) if n else head, "choice": n > 0, "n": n}
    head = f"Unlocking v{version} reopens the query"
    return {"asking": _asking(head, n) if n else f"{head}; no validation paper has been seen yet.", "choice": n > 0,
            "n": n}


def unlock_notice(project: Project, kind: str, version: int, new_version: int | None = None) -> str:
    """The unlock's notice (D2); refuses like :func:`unlock` (ValueError, KeyError)."""
    return unlock_plan(project, kind, version, new_version)["asking"]


def unlock_choice(project: Project, version: int, kind: str = "synonyms") -> bool:
    """True when unlocking ``version`` now needs the seen choice (the page shows three buttons)."""
    try:
        return unlock_plan(project, kind, version)["choice"]
    except (KeyError, ValueError):
        return False


def unlock(project: Project, kind: str, version: int, by: str | None = None, seen_choice: str | None = None) -> dict:
    """Copy frozen ``version`` into a new draft "unlocked from vN" and, unless the query is already reopened, record
    an unlock with cause ``unlock`` (D2). When the validation set holds papers it needs ``seen_choice`` (``count`` |
    ``keep``, or the enclosing :func:`save_seen_choice`); without it :class:`SeenChoiceNeeded`, nothing written
    (unlock-seen-choice D4). The frozen version stays frozen. Returns the new row with ``notice``."""
    plan = unlock_plan(project, kind, version)   # the refusals, before anything is written
    choice = _valid_choice(seen_choice) or _SAVE_SEEN_CHOICE.get()
    if plan["choice"] and choice is None:
        raise SeenChoiceNeeded(plan["asking"])
    _, data = load(project, kind, version)
    again = reopening_unlock(project, kind) is not None
    with _tx(project):
        new = save_new_version(project, kind, data, origin="unlock", note=f"unlocked from v{version}", by=by, reopen=False)
        now = unlock_plan(project, kind, version, new["version"])   # before the row: what was seen until now
        notice = _chosen(now["asking"], choice, now["n"]) if now["choice"] else now["asking"]
        try:
            if now["choice"] and choice is None:
                raise SeenChoiceNeeded(now["asking"])
            if not again:
                record_unlock(project, "unlock", version, new["version"], by, choice=choice if now["choice"] else None)
        except Exception:
            (project.root / new["path"]).unlink(missing_ok=True)
            raise
    new["notice"] = notice
    return new


def diff(project: Project, kind: str, a: int, b: int) -> str:
    ra, _ = load(project, kind, a)
    rb, _ = load(project, kind, b)
    if ra is None or rb is None:
        raise KeyError("version not found")
    ta = (project.root / ra["path"]).read_text(encoding="utf-8").splitlines()
    tb = (project.root / rb["path"]).read_text(encoding="utf-8").splitlines()
    return "\n".join(difflib.unified_diff(ta, tb, f"{kind} v{a}", f"{kind} v{b}", lineterm=""))


def import_file(project: Project, kind: str, path: str | Path, note: str = "", by: str | None = None,
                confirm_revision: bool = False, seen_choice: str | None = None) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) if str(path).endswith((".yaml", ".yml")) else json.loads(text)
    return save_new_version(project, kind, data, origin="import", note=note or f"imported from {Path(path).name}", by=by,
                            confirm_revision=confirm_revision, seen_choice=seen_choice)


def restore_version(project: Project, kind: str, version: int, note: str = "", by: str | None = None,
                    confirm_revision: bool = False, seen_choice: str | None = None) -> dict:
    """Undo for artifacts: versions are never changed or deleted, so an older version comes back as the newest draft."""
    row, data = load(project, kind, version)
    if row is None:
        raise KeyError(f"{kind} v{version} does not exist")
    newest = latest(project, kind)
    if newest and newest["version"] == version:
        raise ValueError(f"{kind} v{version} is already the newest version")
    return save_new_version(project, kind, data, origin="restore", note=note or f"restored from v{version}",
                            parent_version=newest["version"] if newest else None, by=by,
                            confirm_revision=confirm_revision, seen_choice=seen_choice)
