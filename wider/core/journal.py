"""Version history and undo for the researcher's own changes.

Every manual change is recorded with what it touched, before and after: rows of the store (labels, adjudications,
duplicate decisions, artifact statuses, notes, tags, answered questions and what the answers changed) or project
files (``project.yaml`` for every setting and protocol edit, ``benchmark.csv``). :func:`undo` puts the "before" back
only when the touched rows or file still look exactly like the "after"; otherwise it refuses and names what changed
since, so an undo never overwrites later work. An undo is itself a change and can be undone (redo).

Not undoable by design: freezing an artifact (the validation set is tested once), looking at the validation set
before the freeze (the peek stays on record) and saving an artifact version (versions are immutable; restore an older
one as a new version instead). Automated runs (dedup, screening passes,
enrichment) are not journaled; they are reproducible from their inputs.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import yaml

from .provenance import utcnow

if TYPE_CHECKING:
    from .project import Project

MAX_AUTHOR = 80  # names are kept to their first 80 characters (notes, tags and the history alike)
AUTHOR: contextvars.ContextVar[str | None] = contextvars.ContextVar("wider_author", default=None)
_ACTIVE: contextvars.ContextVar[Recorder | None] = contextvars.ContextVar("wider_recorder", default=None)
_LAST: contextvars.ContextVar[int | None] = contextvars.ContextVar("wider_last_change", default=None)

KIND_LABELS = {"label": "label", "adjudication": "adjudication", "dedup_decision": "duplicate decision",
               "artifact_status": "artifact status", "note": "note", "tag": "tag", "answer": "answered question",
               "config": "settings", "undo": "undo", "dismiss": "dismissed question",
               "validation_peek": "validation peek", "result_set": "result set",
               "uploads_final": "uploads marked final", "benchmark_source": "seed paper source",
               "benchmark_keywords": "seed paper keywords", "benchmark_keywords_import": "seed keywords from an import",
               "artifact_unlock": "query unlocked", "source_included": "file in deduplication",
               "source_sample": "file marked as a sample",
               "cluster_mark": "cluster marked", "platform_limits": "platform limits",
               "seed_indexing": "indexing verdict"}
KIND_LABELS |= {"round_count": "round count", "round_accept": "round accepted"}   # query rounds (D8)
KIND_LABELS |= {"dedup_set_aside": "screening set aside by a merge"}
KIND_LABELS |= {"name_admit": "name admitted", "stream_label": "stream labelled"}   # names from seed papers (2b)
KIND_LABELS |= {"recall_target": "recall target", "sample_look": "recall seen on a validation sample",   # D4, D7
                "sample_used": "validation sample used", "sample_not_informed": "change not informed by validation",
                "validation_stop": "validation stopped", "sample_draw": "probability sample drawn",
                "criteria_assess": "criteria change assessed"}
KIND_LABELS |= {"validation_used": "validation papers recorded as used",   # validation-reuse D5
                "validation_moved": "validation paper moved to Development", "benchmark_set": "seed paper set changed"}
KIND_LABELS |= {"candidate_import": "candidates imported from a file",   # papers beyond the query D23
                "candidate_import_dismiss": "candidate import dismissed"}
KIND_LABELS |= {"sample_extend": "sample extended"}   # screening-config-comparison D9
KIND_LABELS |= {"review_decision": "review decision", "review_qc_draw": "quality-control sample"}   # manual-review D3, D11
KIND_LABELS |= {"sample_lock": "sample locked", "sample_unlock": "sample unlocked"}   # manual-review D5
KIND_LABELS |= {"label_provenance": "labels declared independent"}   # label-completion-and-sealing D11
KIND_LABELS |= {"label_correction": "label correction"}   # label-completion-and-sealing D2
KIND_LABELS |= {"review_package": "review package made", "package_import": "review package imported",   # blinding D2-D6
                "package_withdraw": "review package withdrawn"}
KIND_LABELS |= {"report_text": "PRISMA reporting text"}   # prisma-reporting-assistant D11, D21


def kinds() -> list[dict[str, str]]:
    """Every choice of the History page's Show list and of `history list --kind`, by label; ``uploads_reopened`` is
    the uploads half of ``artifact_unlock`` (D2)."""
    return sorted([{"kind": k, "label": v} for k, v in (KIND_LABELS | {"uploads_reopened": "uploads reopened"}).items()],
                  key=lambda d: d["label"])


KIND_LABELS |= {"researcher_confirm": "researcher confirmed", "researcher_join": "researchers joined"}   # attribution D3
KIND_LABELS |= {"ranking_kept": "ranking kept"}   # ui-redesign D5
KIND_LABELS |= {"decisions_import": "decisions imported"}   # import-history-and-model-selection D1
KIND_LABELS |= {"adjudication_import": "adjudicator decisions imported"}   # partial-adjudication-import D7
KIND_LABELS |= {"report_text": "PRISMA reporting text"}   # prisma-reporting-assistant D6

# names this command or request took from the environment or read from an imported file, with their source
# (researcher-attribution D2); reset by the CLI root callback and the request middleware
INFERRED: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar("wider_inferred", default=None)


PSEUDONYM_FORM = re.compile(r"researcher\s*\d+", re.IGNORECASE)


def _clean(name: str) -> str:
    """A name as stored: printable characters only (a tab or newline is dropped, as the page header drops them),
    trimmed, cut to ``MAX_AUTHOR``."""
    return "".join(ch for ch in (name or "") if ch.isprintable()).strip()[:MAX_AUTHOR]


def check_name(name: str) -> None:
    """Refuse a name of the form of a pseudonym: shared files write researchers as Researcher 1, Researcher 2 ...,
    and a researcher called that could not be told apart from one (researcher-attribution)."""
    if name and PSEUDONYM_FORM.fullmatch(name):
        raise ValueError(f"the name {name} has the form of the pseudonyms shared files use (Researcher 1, Researcher "
                         f"2 ...); give another name")


def note_inferred(name: str, source: str) -> None:
    """Record that ``name`` was not given by a person in this command or request (``WIDER_REVIEWER``, ``USER`` or
    ``imported``); a change signed with it registers an inferred entry."""
    name = _clean(name)
    if not name:
        return
    d = INFERRED.get()
    if d is None:
        d = {}
        INFERRED.set(d)
    d.setdefault(name, source)


def current_author(explicit: str | None = None) -> str:
    page = AUTHOR.get()
    if page is not None:  # a request from the page: only the name the page sent, never the server's login name
        return _clean(explicit or page)
    if explicit:
        return _clean(explicit)
    for var in ("WIDER_REVIEWER", "USER"):
        value = os.environ.get(var)
        if value:
            name = _clean(value)
            note_inferred(name, var)   # the fallback is where the source is known, not where the value arrives
            return name
    return ""


NAME_NEEDED = "a reviewer name is needed: give --by (or --author) or set $WIDER_REVIEWER; the login name is never used"


def named_author(explicit: str | None = None) -> str:
    """Who signs work a person must own (review decisions, independence declarations, review packages, undo and
    redo from the command line): the given name, else $WIDER_REVIEWER, never the login name ($USER), so a
    missing name is refused (:data:`NAME_NEEDED`) instead of signed with the computer's account."""
    page = AUTHOR.get()
    if page is not None or explicit:
        return current_author(explicit)
    value = _clean(os.environ.get("WIDER_REVIEWER") or "")
    if value:
        note_inferred(value, "WIDER_REVIEWER")
    return value


def source_of(name: str) -> str:
    """How ``name`` (a value :func:`current_author` returned) was given in this command or request: its noted
    source (``WIDER_REVIEWER``, ``USER``, ``imported``), else ``page`` on a page request, else ``option``."""
    if not name:
        return ""
    noted = (INFERRED.get() or {}).get(name)
    if noted:
        return noted
    return "page" if AUTHOR.get() is not None else "option"


def author_with_source(explicit: str | None = None) -> tuple[str, str]:
    """``(name, source)`` as a change would be signed now (D2)."""
    name = current_author(explicit)
    return name, source_of(name)


def last_change_id() -> int | None:
    """The change recorded last in this request or command (routes return it so the page can offer Undo)."""
    return _LAST.get()


def _rows(project: Project, table: str, where: str, params: list) -> list[dict]:
    return [dict(r) for r in project.store.query(f"SELECT * FROM {table} WHERE {where}", params)]


def _canon(rows: list[dict]) -> list[str]:
    """Rows as compared for "changed": a label saved again with the same value only moves its ``updated_at``, which
    is not a change (else it depends on whether the clock passed a second)."""
    return sorted(json.dumps({k: v for k, v in r.items() if k != "updated_at"}, sort_keys=True, default=str) for r in rows)


class Recorder:
    def __init__(self, project: Project):
        self.project = project
        self.items: list[dict[str, Any]] = []
        self.change_id: int | None = None

    def rows(self, table: str, where: str, params: list | tuple) -> Recorder:
        """Watch the rows matching ``where``; call before the change."""
        params = list(params)
        self.items.append({"t": "rows", "table": table, "where": where, "params": params,
                           "before": _rows(self.project, table, where, params)})
        return self

    def file(self, name: str) -> Recorder:
        """Watch a file in the project folder; call before the change."""
        f = self.project.root / name
        if not any(i["t"] == "file" and i["path"] == name for i in self.items):
            self.items.append({"t": "file", "path": name, "before": f.read_text(encoding="utf-8") if f.exists() else None})
        return self

    def extra(self, item: dict[str, Any]) -> Recorder:
        """A side effect undo must reverse explicitly (e.g. a line appended to a run's response log)."""
        self.items.append(item)
        return self

    def _finish(self) -> list[dict[str, Any]]:
        out = []
        for it in self.items:
            if it["t"] == "rows":
                it["after"] = _rows(self.project, it["table"], it["where"], it["params"])
                if _canon(it["before"]) == _canon(it["after"]):
                    continue
            elif it["t"] == "file":
                f = self.project.root / it["path"]
                it["after"] = f.read_text(encoding="utf-8") if f.exists() else None
                if it["before"] == it["after"]:
                    continue
            out.append(it)
        return out


def side_effect(item: dict[str, Any]) -> None:
    """Attach a side effect to the change being recorded (no-op outside one)."""
    rec = _ACTIVE.get()
    if rec is not None:
        rec.extra(item)


def _store_change(project: Project, kind: str, summary: str, items: list, author: str, record_id: int | None,
                  undo_of: int | None = None) -> int:
    cur = project.store.execute(
        "INSERT INTO history(at,author,kind,summary,record_id,items,undo_of) VALUES(?,?,?,?,?,?,?)",
        (utcnow(), author, kind, summary[:400], record_id, json.dumps(items, default=str), undo_of))
    cid = int(cur.lastrowid)
    _LAST.set(cid)
    from . import researchers
    researchers.register(project, author, source_of(author))
    return cid


@contextlib.contextmanager
def recording(project: Project, kind: str, summary: str, author: str | None = None,
              record_id: int | None = None) -> Iterator[Recorder]:
    """Record one change: watch what it touches inside the block, before mutating. Nothing is recorded when the block
    raises or changes nothing. Changes to project.yaml inside the block join this change."""
    check_name(current_author(author))
    rec = Recorder(project)
    token = _ACTIVE.set(rec)
    try:
        yield rec
    finally:
        _ACTIVE.reset(token)
    items = rec._finish()
    if items:
        rec.change_id = _store_change(project, kind, summary, items, current_author(author), record_id)


def _flat(d: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(d, list) and d and all(isinstance(x, dict) and "name" in x for x in d):
        for x in d:
            out.update(_flat(x, f"{prefix}[{x['name']}]"))
    elif isinstance(d, list) and d and all(isinstance(x, dict) and "key" in x for x in d):
        out[prefix] = [x["key"] for x in d]
        for x in d:
            out.update(_flat(x, f"{prefix}[{x['key']}]"))
    else:
        out[prefix] = d
    return out


def _effective(text: str | None) -> dict[str, Any]:
    """The settings a project.yaml means, not how it is written: an older layout (e.g. top-level ``bands``) that the
    loader moves into ``protocol`` must not read as a change when it is first saved in the current layout."""
    raw = yaml.safe_load(text or "") or {}
    try:
        from .models import ProjectConfig
        return ProjectConfig.model_validate(raw).model_dump(mode="json")
    except Exception:  # noqa: BLE001 - an unreadable file is still summarised by its raw keys
        return raw


def config_diff(before: str | None, after: str | None, limit: int = 6) -> str:
    try:
        a, b = _flat(_effective(before)), _flat(_effective(after))
    except yaml.YAMLError:
        return "project.yaml changed"
    keys = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    show = lambda v: "unset" if v is None else (json.dumps(v)[:40] if not isinstance(v, str) else repr(v[:40]))  # noqa: E731
    parts = [f"{k}: {show(a.get(k))} → {show(b.get(k))}" for k in keys[:limit]]
    return "; ".join(parts) + (f"; and {len(keys) - limit} more" if len(keys) > limit else "") if parts else "project.yaml rewritten"


def config_saved(project: Project, before: str | None, after: str) -> None:
    """Called by Project.save: joins the active change, or records a settings change of its own."""
    if before == after or before is None:
        return
    rec = _ACTIVE.get()
    if rec is not None:
        if not any(i["t"] == "file" and i["path"] == "project.yaml" for i in rec.items):
            rec.items.append({"t": "file", "path": "project.yaml", "before": before})
        return
    if not (project.root / "store.sqlite").exists():
        return
    item = {"t": "file", "path": "project.yaml", "before": before, "after": after}
    _store_change(project, "config", config_diff(before, after), [item], current_author(), None)


# ------------------------------------------------------------------------------------------------- reading
def _public(project: Project, r) -> dict[str, Any]:
    d = dict(r)
    items = json.loads(d.pop("items"))
    d["kind_label"] = KIND_LABELS.get(d["kind"], d["kind"])
    from . import researchers
    d["author_inferred"] = researchers.is_inferred(project, d.get("author") or "")
    if d["kind"] == "artifact_unlock" and str(d.get("summary") or "").startswith("uploads reopened:"):
        d["kind_label"] = "uploads reopened"
    d["touches"] = sorted({it.get("table") or it.get("path") or it["t"] for it in items
                           if it["t"] not in ("recall_seen", "while_locked", "blind")})
    ok, why = _check(project, d, items)
    d["undoable"], d["why_not"] = ok, why
    d["undone_by"] = _undone_now(project, d)   # a change put back by Redo shows as in effect
    plan = _mark_undo(project, items) if ok else None   # the History page asks first (D21, Q2)
    d["undo_choice"], d["undo_notice"] = bool(plan and plan["choice"]), (plan or {}).get("asking")
    return d


def changes(project: Project, kind: str | None = None, record_id: int | None = None, author: str | None = None,
            limit: int = 100, hide_samples: bool = False) -> list[dict[str, Any]]:
    """Newest first. Items (row contents) are not returned: label decisions stay out of view in blind labelling.
    ``hide_samples`` (Blind on) shows "in a sample" for the sample a record was labelled in (owner answer 37e6)."""
    sql, params = "SELECT * FROM history WHERE 1=1", []
    known = [k["kind"] for k in kinds()]
    if kind and kind not in known:
        raise ValueError(f"unknown kind {kind!r}; known kinds: {', '.join(sorted(known))}")
    if kind in ("artifact_unlock", "uploads_reopened"):   # one change kind, two entries: the query, or the uploads (D2)
        sql += " AND kind='artifact_unlock' AND summary " + ("NOT " if kind == "artifact_unlock" else "") + "LIKE 'uploads reopened:%'"
    elif kind:
        sql += " AND kind=?"
        params.append(kind)
    if record_id is not None:
        sql += " AND record_id=?"
        params.append(record_id)
    if author and author.strip():   # cut like a stored name, so a whole long name finds its changes (CLI and page)
        sql += " AND author=?"
        params.append(author.strip()[:MAX_AUTHOR])
    rows = project.store.query(sql + " ORDER BY change_id DESC LIMIT ?", (*params, limit))
    out = [_public(project, r) for r in rows]
    if hide_samples:
        hide = sample_name_hider(project)
        for d in out:
            if d.get("record_id") is not None:   # a whole sample's entries (draw, lock, extend) tie no record to it
                d["summary"] = hide(d.get("summary") or "")
            for k in ("why_not", "undo_notice"):
                if d.get(k):
                    d[k] = hide(d[k])
    return out


def sample_name_hider(project: Project):
    """Turns "record 12 in sample development" into "record 12 in a sample" (also "record 12 of development" and
    "sample 'development'"), so a blind reader cannot tell development from validation records. Only what History
    shows changes; the stored entry keeps its words (owner answer 37e6)."""
    names = sorted((r["name"] for r in project.store.query("SELECT name FROM samples")), key=len, reverse=True)
    if not names:
        return lambda text: text
    alt, end = "(?:" + "|".join(re.escape(n) for n in names) + ")", r"(?=$|[\s:;,.)])"
    subs = [(re.compile(r"\bin sample " + alt + end), "in a sample"),
            (re.compile(r"\bsample '" + alt + "'"), "a sample"),
            (re.compile(r"\b(record \d+) of " + alt + end), r"\1 of a sample")]

    def hide(text: str) -> str:
        for pat, to in subs:
            text = pat.sub(to, text)
        return text
    return hide


IMPORT_FINAL = "imported decisions stay on record as their own result set; make another set active instead"


def _permanent(ch: dict, items: list) -> str | None:
    """Why a change can never be undone, or None."""
    if ch["kind"] == "artifact_status":
        for it in items:
            if it["t"] == "rows" and any(r.get("status") == "frozen" for r in it["after"]):
                return "a freeze is final: the validation set is tested once (save a new version instead)"
    if ch["kind"] == "dedup_set_aside":
        return ("screening rows set aside by a merge stay on record; to split the records again, decide the pair "
                "Not duplicates and run deduplication again")
    if ch["kind"] == "validation_peek":
        return "a look at the hidden validation set stays on record"
    if ch["kind"] in ("artifact_unlock", "sample_unlock"):
        return "an unlock is final"
    if ch["kind"] == "validation_used":
        return "validation papers recorded as used stay on record"
    if ch["kind"] == "validation_moved":
        return "a move of a shown validation paper is final"
    if ch["kind"] == "sample_look":
        return "a recall result seen on a validation sample stays on record"
    if ch["kind"] == "sample_used":
        return "a validation sample recorded as used stays on record"
    if ch["kind"] == "sample_not_informed":
        return "a change recorded as not informed by validation stays on record"
    if ch["kind"] == "validation_stop":
        return "a decision to stop validation stays on record"
    if ch["kind"] == "decisions_import":
        return IMPORT_FINAL
    if ch["kind"] == "review_package":
        from ..sample.package import PERMANENT
        return PERMANENT
    for it in items:
        if it["t"] == "unlocked":
            return f"it unlocked the frozen query (change {it['change_id']}); an unlock is final"
        if it["t"] == "validation_used":
            return (f"it recorded validation papers as used (change {it['change_id']}); that record stays")
    return None


LOCK_TABLES = ("labels", "adjudications", "label_corrections", "label_independence")
BLIND_LOCKED = "this label was locked or sealed after this change: correct it instead, with a reason"


def _root_change(project: Project, ch: dict | None) -> tuple[int | None, list]:
    """The id and items of the change an undo (or a chain of them) goes back to, read through ``undo_of``."""
    if ch is None:
        return None, []
    cid, undo_of = ch.get("change_id"), ch.get("undo_of")
    seen = 0
    while undo_of and seen < 1000:
        r = project.store.one("SELECT change_id, undo_of FROM history WHERE change_id=?", (undo_of,))
        if r is None:
            break
        cid, undo_of, seen = r["change_id"], r["undo_of"], seen + 1
    r = project.store.one("SELECT items FROM history WHERE change_id=?", (cid,)) if cid is not None else None
    return cid, (json.loads(r["items"]) if r else [])


def _locked_sample(project: Project, items: list, ch: dict | None = None) -> str | None:
    """Why a change to a locked sample's labels cannot be undone (or redone) now, or None (D7 of
    label-completion-and-sealing): a change made before the sample's current lock is corrected instead; one made
    while locked (the ``while_locked`` item of the original change, and no lock of the sample after it) undoes. A
    change to a sealed record whose original change id is not above the seal's ``last_change_id`` is refused too."""
    from wider.sample.labels import sealed_records  # noqa: I001 (labels imports this module)

    root_id, root_items = _root_change(project, ch)
    after_lock = any(x.get("t") == "while_locked" for x in root_items)
    blind = any(x.get("t") == "blind" for x in root_items) or _older_blind_save(project, root_id)
    if root_id is not None:   # a sealed record's text comes first, also when its sample is locked
        for it in items:
            if it["t"] == "rows" and it.get("table") in LOCK_TABLES and it["where"].startswith("sample_id=? AND record_id=?"):
                sid, rid = it["params"][0], it["params"][1]
                s = project.store.one("SELECT * FROM samples WHERE sample_id=?", (sid,))
                seal = sealed_records(project, dict(s)).get(rid) if s is not None else None   # older samples sealed first
                if seal is not None and seal["last_change_id"] is not None and root_id <= seal["last_change_id"]:
                    if blind:
                        return BLIND_LOCKED
                    return f"record {rid} of {s['name']} was sealed after this change: correct the label instead, with a reason"
    for it in items:
        if it["t"] == "rows" and it.get("table") in LOCK_TABLES and it["where"].startswith("sample_id=?"):
            sid = it["params"][0]
            s = project.store.one("SELECT name, locked FROM samples WHERE sample_id=?", (sid,))
            if s is None or not s["locked"]:
                continue
            if after_lock and root_id is not None and not _locked_since(project, sid, root_id):
                continue
            return BLIND_LOCKED if blind else f"sample {s['name']!r} was locked after this change: correct the label instead, with a reason"
    return None


def _older_blind_save(project: Project, change_id: int | None) -> bool:
    """Whether a label change made before label-completion-and-sealing (no ``blind`` item) came from a queue: those
    saves are summarised without their sample, an unblinded save "... in sample NAME" (risk-checker R1)."""
    if change_id is None:
        return False
    r = project.store.one("SELECT kind, summary FROM history WHERE change_id=?", (change_id,))
    return r is not None and r["kind"] == "label" and " in sample " not in (r["summary"] or "")


def _locked_since(project: Project, sample_id: int, change_id: int) -> bool:
    """Whether a ``sample_lock`` change of the sample is newer than ``change_id``."""
    for r in project.store.query("SELECT items FROM history WHERE kind='sample_lock' AND change_id>?", (change_id,)):
        if any(x["t"] == "rows" and x.get("table") == "samples" and x["params"][0] == sample_id
               for x in json.loads(r["items"])):
            return True
    return False


def _extension(project: Project, items: list) -> str | None:
    """Why a sample extension (the only change journalling ``sample_rows``) cannot be undone or redone now (D9)."""
    if not any(it["t"] == "rows" and it.get("table") == "sample_rows" for it in items):
        return None
    from ..sample.design import draw_block, extension_block
    return draw_block(project, items) or extension_block(project, items)


def _adjudicated_since(project: Project, ch: dict, items: list) -> str | None:
    """Why a review-package import cannot be undone (M105): an adjudication made since, and still in effect, rests on
    a label it brought in. Undoing the import would leave that adjudication without the disagreement it settled, and
    undoing the adjudication with it would take away another person's signed decision, so the adjudication is named
    and undone first, by its own change."""
    if ch.get("kind") != "package_import":
        return None
    pairs = {(it["params"][0], it["params"][1]) for it in items
             if it["t"] == "rows" and it.get("table") == "labels" and it["where"].startswith("sample_id=? AND record_id=?")}
    if not pairs:
        return None
    for r in project.store.query("SELECT * FROM history WHERE change_id>? AND kind IN ('adjudication','label_correction') "
                                 "ORDER BY change_id", (ch["change_id"],)):
        if undone_now(project, dict(r)):
            continue
        for it in json.loads(r["items"]):
            if it["t"] == "rows" and it.get("table") == "adjudications" and tuple(it["params"][:2]) in pairs:
                sid, rid = it["params"][0], it["params"][1]
                name = project.store.scalar("SELECT name FROM samples WHERE sample_id=?", (sid,)) or sid
                return (f"record {rid} of sample {name} was adjudicated since by {r['author'] or 'someone'} "
                        f"(change {r['change_id']}), on a label this import brought in: undo that adjudication first")
    return None


def _lock_seen(project: Project, ch: dict, items: list) -> str | None:
    """Why a sample lock cannot be undone (manual-review D5): a recall result on that sample was seen after it."""
    if ch.get("kind") != "sample_lock":
        return None
    for it in items:
        if it["t"] == "rows" and it.get("table") == "samples":
            sid = it["params"][0]
            if project.store.scalar("SELECT COUNT(*) FROM validation_looks WHERE sample_id=? AND n>0 AND last_at>=?",
                                    (sid, ch["at"])):
                name = project.store.scalar("SELECT name FROM samples WHERE sample_id=?", (sid,))
                return f"recall on {name} was seen after the lock; unlock it instead, which is recorded"
            for r in project.store.query("SELECT items FROM history WHERE change_id>? AND kind='sample_unlock'",
                                         (ch["change_id"],)):   # risk-checker R4: undoing it would undo a later lock
                if any(x["t"] == "rows" and x.get("table") == "samples" and x["params"][0] == sid
                       for x in json.loads(r["items"])):
                    name = project.store.scalar("SELECT name FROM samples WHERE sample_id=?", (sid,))
                    return f"{name} was unlocked after this lock; the later lock is the one to undo"
    return None


def _qc_draw_decided(project: Project, ch: dict, items: list) -> str | None:
    """Why a quality-control draw cannot be undone now (manual-review D11): a record of it has a review decision or an
    answer recorded after the draw and not undone. Checked here, not in ``_permanent``, so ``undo_last`` stops at it."""
    if ch.get("kind") != "review_qc_draw":
        return None
    ids = {r["record_id"] for it in items if it["t"] == "rows" and it.get("table") == "review_qc"
           for r in (it["after"] or it["before"])}
    if not ids:
        return None
    q = ",".join("?" * len(ids))
    for r in project.store.query(f"SELECT * FROM history WHERE change_id>? AND kind IN ('review_decision','answer') "
                                 f"AND record_id IN ({q})", (ch["change_id"], *sorted(ids))):
        if not undone_now(project, dict(r)):
            return "a record of this quality-control sample has a review decision; undo that decision first"
    return None


def _final_mark(project: Project, items: list) -> str | None:
    """Why the uploads cannot be marked final again now (a Redo of an undone mark), or None: the same checks as
    marking them, so a file saved but not imported meanwhile stops it."""
    from ..ingest.importer import FINAL_KEY, final_blocker

    for it in items:
        if it["t"] == "rows" and it.get("table") == "meta" and it.get("params") == [FINAL_KEY] and it["before"] \
                and not it["after"]:
            why = final_blocker(project)
            return f"the uploads cannot be marked final again: {why}" if why else None
    return None


def undone_now(project: Project, ch: dict) -> int | None:
    """The undo that keeps ``ch`` undone, or None when it is in effect: a Redo (an undo of that undo) puts it back,
    so an even chain of undos leaves the change in effect."""
    chain, cur = [], ch.get("undone_by")
    while cur and len(chain) < 1000:
        chain.append(cur)
        cur = project.store.scalar("SELECT undone_by FROM history WHERE change_id=?", (cur,))
    return chain[0] if len(chain) % 2 else None


_undone_now = undone_now   # the old private name, kept for its callers


def _check(project: Project, ch: dict, items: list) -> tuple[bool, str | None]:
    done = _undone_now(project, ch)
    if done:
        return False, f"already undone (change {done})"
    why = (_permanent(ch, items) or _set_in_use(project, items) or _locked_sample(project, items, ch) or _extension(project, items)
           or _qc_draw_decided(project, ch, items) or _lock_seen(project, ch, items) or _adjudicated_since(project, ch, items))
    if why:
        return False, why
    for it in items:
        if it["t"] == "rows":
            if _canon(_rows(project, it["table"], it["where"], it["params"])) != _canon(it["after"]):
                return False, _imported_since(project, it) or f"{it['table']} changed since; undo the later change first"
        elif it["t"] == "file":
            f = project.root / it["path"]
            now = f.read_text(encoding="utf-8") if f.exists() else None
            if now != it["after"]:
                return False, f"{it['path']} changed since; undo the later change first"
    why = _final_mark(project, items) or _shown_move(project, items) or _sample_pool(project, items)
    if why:
        return False, why
    return True, None


def _sample_pool(project: Project, items: list) -> str | None:
    """Why putting a file's sample mark back would make its same-N sample hold N records or more, or None: the
    refusal of ``importer.set_sample`` (D4 of openalex-profile-and-sampled-exports) holds for undo and redo too."""
    for it in items:
        if it["t"] != "rows" or it.get("table") != "sources":
            continue
        for r in it["before"]:
            of = r.get("sample_of")
            if of is None or not r.get("included"):
                continue
            held = r["n_records"] + (project.store.scalar(
                "SELECT SUM(n_records) FROM sources WHERE platform=? AND included=1 AND sample_of=? "
                "AND query_version IS ? AND source_id<>?",
                (r["platform"], of, r["query_version"], r["source_id"])) or 0)
            if held > r["n_records"] and held >= of:
                return (f"file {r['source_id']} ({r['file_name']}) and the other files marked a random sample of "
                        f"{of:,} results on {r['platform']} would hold {held:,} records, not fewer than {of:,}: "
                        "clear one of those marks first")
    return None


def _shown_move(project: Project, items: list) -> str | None:
    """Why putting ``benchmark.csv`` back would move a validation paper shown since to Development (an undo or a redo
    of a set change, D3 of validation-reuse), or None: that move needs the researcher's confirmation."""
    import csv
    import io

    for it in items:
        if it["t"] != "file" or it["path"] != "benchmark.csv" or not it["before"] or not it["after"]:
            continue
        sets = lambda text: {r.get("benchmark_id"): r.get("set") for r in csv.DictReader(io.StringIO(text))}  # noqa: E731
        now, back = sets(it["after"]), sets(it["before"])
        going = [i for i, s in now.items() if s == "Validation" and back.get(i) == "Development"]
        if going:
            from ..search.coverage import _shown_ids

            papers = project.read_benchmark()
            shown = _shown_ids(project, papers)
            for i in going:
                if i in shown:
                    return (f"{i} has been shown as a validation paper since; move it with wider benchmark set {i} "
                            "Development --confirm")
    return None


def _set_in_use(project: Project, items: list) -> str | None:
    """Why putting a protocol's result sets back would delete a set that something uses since (a recall look, a
    quality-control draw, a question or a second screen), or None (partial-adjudication-import D8)."""
    st = project.store
    for it in items:
        if it["t"] != "rows" or it.get("table") != "screen_result_sets" or it.get("where") != "protocol=?":
            continue
        protocol = it["params"][0]
        had = {r["result_set"] for r in it["before"]}
        for r in st.query("SELECT result_set FROM screen_result_sets WHERE protocol=? ORDER BY result_set", (protocol,)):
            n = r["result_set"]
            if n in had:
                continue
            keys = (protocol if n == 1 else f"{protocol}#{n}",)
            q = ",".join("?" * len(keys))
            if (st.scalar(f"SELECT 1 FROM validation_looks WHERE result_set_key IN ({q}) LIMIT 1", keys)
                    or st.scalar(f"SELECT 1 FROM validation_look_states WHERE set_key IN ({q}) LIMIT 1", keys)):
                return f"recall of set {n} was seen on a validation sample; that look stays on record"
            if st.scalar(f"SELECT 1 FROM review_qc WHERE set_key IN ({q}) LIMIT 1", keys):
                return f"a quality-control sample was drawn from set {n}"
            qid = st.scalar(f"SELECT MIN(question_id) FROM questions WHERE run_ref IN ({q})", keys)
            if qid:
                return f"set {n} has questions (#{qid})"
            other = st.one("SELECT protocol, result_set FROM screen_result_sets WHERE source_protocol=? AND source_set=? "
                           "ORDER BY protocol, result_set LIMIT 1", (protocol, n))
            if other:
                ok = other["protocol"] if other["result_set"] == 1 else f"{other['protocol']}#{other['result_set']}"
                return f"a second screen was built on set {n} ({ok})"
    return None


def _imported_since(project: Project, it: dict) -> str | None:
    """Why a change to a protocol's result sets cannot be undone once a set was imported into it since: putting the
    rows back would delete that set, and an import is final (import-history-and-model-selection D2)."""
    if it.get("table") != "screen_result_sets" or it.get("where") != "protocol=?":
        return None
    had = {r["result_set"] for r in it["after"]}
    new = [r["result_set"] for r in project.store.query(
        "SELECT result_set FROM screen_result_sets WHERE protocol=? AND import_sha256 IS NOT NULL "
        "AND source <> 'adjudication' ORDER BY result_set",
        it["params"]) if r["result_set"] not in had]
    if not new:
        return None
    sets = ", ".join(str(n) for n in new)
    return (f"set {sets} was imported into {it['params'][0]} since, and {IMPORT_FINAL.split(';')[0]}; choose the set "
            f"to use with wider screen sets use {it['params'][0]} <set> instead")


def _touch_keys(items: list) -> set[tuple]:
    keys = {(it["t"], it.get("table"), it.get("where"), json.dumps(it.get("params"), default=str), it.get("path"))
            for it in items if it["t"] in ("rows", "file")}
    for it in items:   # an import adds a set row of its protocol, so it blocks an earlier set switch (D2)
        if it["t"] == "decisions_import" and it.get("protocol"):
            keys.add(("rows", "screen_result_sets", "protocol=?", json.dumps([it["protocol"]]), None))
        if it["t"] == "rows" and it.get("table") == "screen_decisions" and it.get("where") == "protocol=?":
            keys |= _set_keys([it])   # a whole set's decisions: any later change to one of them blocks (D8)
    return keys


def _set_keys(items: list) -> set[tuple]:
    """The result sets whose screen decisions ``items`` touch, as :func:`_touch_keys` keys (D8)."""
    return {("rows", "screen_decisions", "set", json.dumps(it["params"][0]), None) for it in items
            if it["t"] == "rows" and it.get("table") == "screen_decisions" and it.get("where", "").startswith("protocol=?")}


def _blocker(project: Project, ch: dict, items: list) -> dict | None:
    """The newest later change that touched the same rows or file as ``ch`` (what blocks its undo), if journalled."""
    keys = _touch_keys(items)
    for r in project.store.query("SELECT * FROM history WHERE change_id>? ORDER BY change_id DESC", (ch["change_id"],)):
        later = json.loads(r["items"])
        if keys & (_touch_keys(later) | _set_keys(later)):
            return dict(r)
    return None


def _mark_undo(project: Project, items: list) -> dict | None:
    """What undoing ``items`` would record when they take away the uploads-final mark while the validation set was
    shown (``validation_after_freeze`` on, at least one paper in it; D21, F4), or None: ``at`` (the mark's time),
    ``choice`` (always: the researcher chooses whether the papers count as seen, Q2, with or without a frozen query,
    unlock-seen-choice D3) and ``asking`` (that notice)."""
    from ..ingest.importer import FINAL_KEY

    if not any(it["t"] == "rows" and it.get("table") == "meta" and it.get("params") == [FINAL_KEY] and it["after"]
               and not it["before"] for it in items):
        return None
    at = project.store.get_meta(FINAL_KEY)
    if not at or not project.config.protocol.identification.validation_after_freeze:
        return None
    from ..construct import artifacts

    n = artifacts._n_validation(project)
    if not n:
        return None
    fq = artifacts.frozen_query(project)
    stands = f" and the frozen query v{fq['version']} stands" if fq is not None else ""
    asking = (f"The uploads were marked final on {at[:10]}{stands}; undoing the mark reopens them; "
              f"{artifacts.papers_phrase(n, 'has been shown', 'have been shown')}. {artifacts.CHOICE_TAIL}")
    return {"at": at, "n": n, "choice": True, "asking": asking, "frozen": fq["version"] if fq else None}


def undo_plan(project: Project, change_id: int) -> dict | None:
    """``{choice, asking}`` when undoing ``change_id`` now records an unlock (the History page's confirm), else None."""
    r = project.store.one("SELECT items FROM history WHERE change_id=?", (change_id,))
    return _mark_undo(project, json.loads(r["items"])) if r else None


def undo_last(project: Project, author: str, seen_choice: str | None = None) -> dict[str, Any]:
    """Undo ``author``'s newest change that is not an undo or redo, not undone and not permanent, so repeated calls go
    further back; a change put back by Redo counts as in effect. A later change blocking it stops the walk (ValueError naming that change); "nothing to undo" when none
    is left, naming any name that differs only in case or spacing (D7). Redo stays explicit: undo the 'undo' change by its id.
    ``seen_choice`` goes to :func:`undo` (the uploads-final mark after papers were shown asks, unlock-seen-choice D4)."""
    who = (author or "").strip()
    if not who:
        raise ValueError("undo-last needs the reviewer's name")
    for r in project.store.query("SELECT * FROM history WHERE author=? AND kind!='undo' ORDER BY change_id DESC", (who,)):
        ch, items = dict(r), json.loads(r["items"])
        if _permanent(ch, items) or _undone_now(project, ch):   # a change put back by Redo is in effect again
            continue
        ok, why = _check(project, ch, items)
        if not ok and _locked_sample(project, items, ch):   # the lock, not a later change, is what stops it
            raise ValueError(f"{who}'s last change {ch['change_id']} ({ch['summary']}) cannot be undone: {why}")
        if not ok:
            b = _blocker(project, ch, items)
            by = f"; blocked by change {b['change_id']} by {b['author'] or 'someone'}: {b['summary']}" if b else ""
            raise ValueError(f"{who}'s last change {ch['change_id']} ({ch['summary']}) cannot be undone: {why}{by}")
        return undo(project, ch["change_id"], author=who, seen_choice=seen_choice)
    near = _near_names(project, who)
    raise ValueError(f"nothing to undo by {who}" + (f"; there are changes by {near}" if near else ""))


def redo_last(project: Project, author: str) -> dict[str, Any]:
    """Redo ``author``'s newest undo (an "undo: ..." change, never a "redo:") that is not undone now and is newer than
    the name's newest ordinary change, so repeated calls walk back through the undos; a later change blocking it stops
    the walk (ValueError naming that change); "nothing to redo by NAME" when none is left (accessibility-and-naming D4)."""
    who = (author or "").strip()
    if not who:
        raise ValueError("redo-last needs the reviewer's name")
    st = project.store
    newest = st.scalar("SELECT MAX(change_id) FROM history WHERE author=? AND kind!='undo'", (who,)) or 0
    for r in st.query("SELECT * FROM history WHERE author=? AND kind='undo' AND summary LIKE 'undo: %' AND change_id>? "
                      "ORDER BY change_id DESC", (who, newest)):
        ch, items = dict(r), json.loads(r["items"])
        if _undone_now(project, ch):
            continue
        ok, why = _check(project, ch, items)
        if not ok:
            b = _blocker(project, ch, items)
            by = f"; blocked by change {b['change_id']} by {b['author'] or 'someone'}: {b['summary']}" if b else ""
            raise ValueError(f"{who}'s last undo {ch['change_id']} ({ch['summary']}) cannot be redone: {why}{by}")
        return undo(project, ch["change_id"], author=who)
    raise ValueError(f"nothing to redo by {who}")


def _near_names(project: Project, who: str) -> str:
    """Other names that differ from ``who`` only in case or spacing, quoted (D7: named, never merged)."""
    def norm(s):
        return " ".join(s.split()).casefold()
    names = [r["author"] for r in project.store.query(
        "SELECT DISTINCT author FROM history WHERE author IS NOT NULL AND author!=? ORDER BY author", (who,))
        if norm(r["author"]) == norm(who)]
    return ", ".join(f"'{n}'" for n in names)


class OtherAuthor(ValueError):
    """Undo by id of a change signed with another name than the undoer's, without ``other`` (manual-review D7)."""

    def __init__(self, message: str, author: str):
        super().__init__(message)
        self.author = author


class NeedName(ValueError):
    """Undo of another person's change with no undoer name (manual-review D7; the route answers 400)."""


def _ask_other(ch: dict, author: str | None, other: bool) -> str | None:
    """The undoer's name; refuses a change signed with another name unless ``other`` (asked first: inform, not
    control). An unsigned change, or one an older version signed "ui", is undone without asking."""
    signed = (ch["author"] or "").strip()
    who = current_author(author)
    if not signed or signed == "ui" or signed == who:
        return who or None
    if not who:
        raise NeedName(f"give your name to undo {signed}'s change")
    if not other:
        raise OtherAuthor(f"change {ch['change_id']} was made by {signed}; add --other to undo it as {who}", signed)
    return who


def undo(project: Project, change_id: int | None = None, author: str | None = None,
         seen_choice: str | None = None, other: bool = False) -> dict[str, Any]:
    """Undo one change (default: the newest change that can be undone); returns the new 'undo' change. Undoing the
    uploads-final mark after the validation set was shown records an unlock in the same transaction (``notice``);
    under a frozen query that needs ``seen_choice`` (``count`` | ``keep``; else ``SeenChoiceNeeded``, nothing
    undone). A Redo of the mark stamps it now, with the latest unlock number, so it opens the set again (F10).
    A change signed with another name asks first (``OtherAuthor`` unless ``other``; D7)."""
    st = project.store
    check_name(current_author(author))
    if change_id is None:
        for r in st.query("SELECT * FROM history ORDER BY change_id DESC LIMIT 50"):   # _check skips undone ones
            if _check(project, dict(r), json.loads(r["items"]))[0]:
                change_id = r["change_id"]
                break
        if change_id is None:
            raise ValueError("nothing to undo")
    r = st.one("SELECT * FROM history WHERE change_id=?", (change_id,))
    if r is None:
        raise KeyError(f"no change {change_id}")
    ch, items = dict(r), json.loads(r["items"])
    ok, why = _check(project, ch, items)
    if not ok:
        raise ValueError(f"change {change_id} cannot be undone: {why}")
    author = _ask_other(ch, author, other) or author
    from ..ingest.importer import FINAL_KEY, UNLOCK_NO_KEY
    plan, choice = _mark_undo(project, items), None
    if plan:
        from ..construct import artifacts
        choice = artifacts.seen_choice(seen_choice)
        if plan["choice"] and choice is None:
            raise artifacts.SeenChoiceNeeded(plan["asking"])
    reverse, notice = [], None
    with st.tx():   # the row restores land together or not at all
        st.execute("PRAGMA defer_foreign_keys=ON")   # a parent row is deleted and re-inserted: check at commit
        for it in reversed(items):
            if it["t"] == "rows":
                st.execute(f"DELETE FROM {it['table']} WHERE {it['where']}", it["params"])
                for row in it["before"]:
                    cols = list(row)
                    st.execute(f"INSERT INTO {it['table']}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                               [row[c] for c in cols])
                after = it["before"]
                if it.get("table") == "meta" and it.get("params") in ([FINAL_KEY], [UNLOCK_NO_KEY]) \
                        and it["before"] and not it["after"]:   # a Redo of the mark: stamped now, opening the set
                    from ..search.coverage import latest_unlock_no
                    value = utcnow() if it["params"] == [FINAL_KEY] else str(latest_unlock_no(project))
                    st.execute("UPDATE meta SET value=? WHERE key=?", (value, it["params"][0]))
                    after = _rows(project, "meta", it["where"], it["params"])
                reverse.append({**it, "before": it["after"], "after": after})
            elif it["t"] == "file":
                f = project.root / it["path"]
                if it["before"] is None:
                    f.unlink(missing_ok=True)
                else:
                    from .project import write_atomic
                    write_atomic(f, it["before"])
                reverse.append({**it, "before": it["after"], "after": it["before"]})
            elif it["t"] == "jsonl_human":   # a human read appended to a run's log: append a retraction (or restore it)
                from . import jsonl
                retract = not it.get("retracted")
                jsonl.append(project.root / it["path"], {"record_id": it["record_id"], "pass": it["pass"],
                                                         "question_id": it["question_id"], "human_retracted": retract,
                                                         "at": utcnow()})
                reverse.append({**it, "retracted": retract})
        if plan:   # the set was shown while the mark stood: an unlock records what was seen (D21, F4)
            from ..construct import artifacts
            artifacts.record_unlock(project, "uploads", None, None, author, choice=choice,
                                    detail="final mark undone", final_before=plan["at"], n_shown=plan["n"])
            notice = (f"the frozen query v{plan['frozen']} still stands. " if plan["frozen"] else "") + \
                artifacts.outcome_line(choice, plan["n"])
    if any(it["t"] == "file" and it["path"] == "project.yaml" for it in items):
        from .project import Project as P
        project.config = P.load(project.root).config
    if any(it["t"] == "file" and it["path"] == "benchmark.csv" for it in items):
        from ..construct.benchmark import sync
        sync(project)
    import re
    base = re.sub(r"^(?:(?:undo|redo): )+", "", re.sub(r" \(change \d+\)$", "", ch["summary"]))
    verb = "redo" if ch["kind"] == "undo" and ch["summary"].startswith("undo: ") else "undo"
    new_id = _store_change(project, "undo", f"{verb}: {base} (change {change_id})", list(reversed(reverse)),
                           current_author(author), ch["record_id"], undo_of=change_id)
    st.execute("UPDATE history SET undone_by=? WHERE change_id=?", (new_id, change_id))
    return {**_public(project, st.one("SELECT * FROM history WHERE change_id=?", (new_id,))), "notice": notice}
