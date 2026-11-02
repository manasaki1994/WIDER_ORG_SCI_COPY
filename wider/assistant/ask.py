"""Ask WIDER's engine (guide-assistant D1, D7, D15, D22): one question in, one checked answer out. It reads WIDER's
documents and the context of context.py, sends them to the project's query-creation backend through the usual request
loop, usage log and consent check, keeps only what the model may name (sections that were sent, buttons the guide
knows) and stores the turn under its author. It never reads a record."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ..core import usage
from ..core.project import Project
from ..guide import NO_PAGE, NOT_SHOWN, button_registry, guide_text, methods, pages
from ..guide.methods import Section
from . import store
from .context import ANSWER_TOKENS, GENERAL, Context, TooLong, build_context
from .retrieve import retrieve
from .sections import all_sections

EMPTY_QUESTION = "Type a question first."
NO_AUTHOR = "Enter your name first: your conversation is kept under your name."
NO_MODEL = "Ask WIDER needs a model. Set one on Model for query creation."
MALFORMED = "The model did not give a usable answer. Ask again, or try a larger model."
UNREACHABLE = ("WIDER could not reach {backend} ({model}). Check that it is running and its address on the Model page, "
               "then ask again.")
NO_DOCS = "WIDER's documents do not explain this."
BUSY = "Another question is running: wait for it or press Stop."
NOTE_UNSENT = "The answer cited a section it was not given; it is not shown."
NOTE_BUTTON = "The answer named a button WIDER does not know ({name}); it is not shown."
UNREVIEWED_MARK = "(not yet reviewed by the author)"
DOCS_CHANGED = "Answered before WIDER's documents changed."
FORGOTTEN = "Your conversation was deleted while this question was answered, so the answer was not kept."
NOT_KEPT = ("You allowed this question once, without remembering your choice, so neither the consent nor this question "
            "and answer is stored. It stays on screen until you leave or reload the page.")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "show": {"type": "array", "items": {"type": "object", "properties": {
            "page": {"type": "string"}, "button": {"type": "string"}}, "required": ["page", "button"]}},
        "drafts": {"type": "array", "items": {"type": "object", "properties": {
            "for": {"type": "string"}, "text": {"type": "string"}}, "required": ["for", "text"]}},
    },
    "required": ["answer", "sources", "show", "drafts"],
}


def is_assistant_schema(schema: dict | None) -> bool:
    """Whether a request carries the answer schema (FakeProvider and the mock server answer it, D20)."""
    return bool(schema) and set(schema.get("properties") or {}) == set(SCHEMA["properties"])


class Refusal(ValueError):
    """Nothing is sent or stored; the message is the spec's sentence (red on both surfaces, CLI exit 1)."""


class NoModel(Refusal):
    def __init__(self):
        super().__init__(NO_MODEL)


def assistant_provider(project: Project):
    """The backend of ``config.stage_providers["construct"]`` (D1), checked to write text; ``NoModel`` without that
    entry even when a default backend exists. No consent is checked here."""
    from ..providers.registry import require_text

    name = project.config.stage_providers.get("construct")
    if not name:
        raise NoModel()
    try:
        cfg = project.provider(name)
    except KeyError:
        raise NoModel() from None
    try:
        return require_text(cfg)
    except ValueError as e:
        raise Refusal(str(e)) from None


def cli_command(s: Section) -> str:
    """The command that prints a section: ``wider guide methods <topic>``, ``wider guide methods glossary`` or
    ``wider guide <page>``."""
    if s.kind == "methods":
        return f"wider guide methods {s.topic}"
    if s.kind == "glossary":
        return "wider guide methods glossary"
    return f"wider guide {s.pages[0]}"


def reviewed(s: Section) -> bool:
    """Whether a methods or glossary section's text is reviewed by the author now (D22); guide sections are."""
    try:
        if s.kind == "methods":
            return methods.topic(s.topic)["review"]["status"] == "reviewed"
        if s.kind == "glossary":
            return methods.glossary_review()["status"] == "reviewed"
    except (KeyError, TypeError):
        return False
    return True


def based_on(ids: list[str], by_id: dict[str, Section], unreviewed: list[str] = ()) -> list[dict[str, Any]]:
    """Each source with its title, the "Open" anchor, the CLI command and whether it is unreviewed; a section that is
    gone since keeps its id as title and has no anchor."""
    out = []
    for i in ids:
        s = by_id.get(i)
        out.append({"id": i, "title": s.title if s else i, "anchor": s.anchor if s else "",
                    "command": cli_command(s) if s else "", "unreviewed": i in unreviewed})
    return out


def _fold(s: str) -> str:
    """A name as compared: quotes, a final full stop and a trailing "button" dropped, spaces folded, lower case."""
    s = re.sub(r"\s+", " ", s).strip().strip("\"'“”‘’*").rstrip(".").strip()
    return re.sub(r"\s+button$", "", s, flags=re.I).casefold()


def known_button(given_page: str, name: str, page: str) -> dict[str, str] | None:
    """``{page, button}`` with the guide's own spelling when ``name`` is a known button of the page the model gave
    (its key or title, any case) or, failing that, of the asked page; else None (M123, 31 Oct: a known label was
    refused when the model wrote the page or the name a little differently)."""
    known = button_registry()
    want = given_page.strip().casefold()
    pg = next((p for p in known if want in (p.casefold(), guide_text(p)["title"].casefold())), None) if want else None
    for p in dict.fromkeys(x for x in (pg, page) if x):
        b = next((b for b in known.get(p, []) if _fold(b) == _fold(name)), None)
        if b and b not in NOT_SHOWN:
            return {"page": p, "button": b}
    return None


def clean(parsed: Any, ctx: Context, page: str) -> tuple[dict[str, Any], list[str]]:
    """What may be shown of the model's answer (D7): sources among the sections sent (all of them when none is kept),
    buttons the guide knows, drafts as text. ``Refusal(MALFORMED)`` for an empty answer."""
    if not isinstance(parsed, dict) or not str(parsed.get("answer") or "").strip():
        raise Refusal(MALFORMED)
    notes: list[str] = []
    sent = [s.id for s in ctx.sections]
    cited = [str(x) for x in parsed.get("sources") or [] if isinstance(x, (str, int))]
    kept = [i for i in dict.fromkeys(cited) if i in sent]
    if any(i not in sent for i in cited):
        notes.append(NOTE_UNSENT)
    show = []
    for e in [] if page == GENERAL else parsed.get("show") or []:   # a general question names no button (D23)
        if not isinstance(e, dict):
            continue
        name = str(e.get("button") or "").strip()
        if not name:
            continue
        hit = known_button(str(e.get("page") or ""), name, page)
        if hit:
            if hit not in show:
                show.append(hit)
        else:
            notes.append(NOTE_BUTTON.format(name=name))
    drafts = [{"for": str(d.get("for") or ""), "text": str(d.get("text") or "")}
              for d in parsed.get("drafts") or [] if isinstance(d, dict) and str(d.get("text") or "").strip()]
    return {"answer": str(parsed["answer"]), "sources": kept or sent, "show": show, "drafts": drafts}, notes


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check(project: Project, page: str, question: str, author: str | None) -> None:
    if not (question or "").strip():
        raise Refusal(EMPTY_QUESTION)
    if page != GENERAL and page not in pages():
        raise Refusal(NO_PAGE.format(page=page))
    if not (author or "").strip():
        raise Refusal(NO_AUTHOR)


def ask(project: Project, page: str, question: str, author: str | None, depth: str | None = None,
        allow_once: str | bool | None = None, progress=None, keep: bool = True) -> dict[str, Any]:
    """Answer one question and store the turn. ``allow_once``: contact a backend without stored consent for this one
    question (``True``: the current fingerprint, as ``--allow-once``; a string: the fingerprint the dialog showed);
    then nothing is stored, the turn either (``turn`` 0, ``kept`` false), as the consent dialog promises; ``keep``
    false does the same for a question the page allowed once (its allowance is entered by the caller).
    ``progress`` (the job's callback) is called just before the turn is stored, so a run stopped meanwhile raises
    there and stores nothing. Raises ``Refusal`` (nothing sent or stored) and ``BackendConsentRequired`` (nothing
    sent)."""
    from ..core.backend_consent import allow_once as once
    from ..providers.base import ChatRequest, ProviderError
    from ..providers.registry import research_provider

    _check(project, page, question, author)
    cfg = assistant_provider(project)
    snapshot = all_sections()                 # one snapshot per question (D22)
    by_id = {s.id: s for s in snapshot}
    problems = [methods.problem_line(f, p) for f, p in methods.problems()]
    ret = retrieve(question, project=project, sections=snapshot, depth=depth, page=page)
    turn: dict[str, Any] = {"at": _now(), "author": author, "page": page, "depth": depth, "question": question}
    since = store.started(project, author)    # a forget while this question runs is not undone by it

    if not ret.sections:                      # no model call: nothing is sent (the turn lists it as unanswered)
        turn.update(answer=NO_DOCS, sources=[], unreviewed=[], show=[], drafts=[], notes=problems + ret.notes,
                    context=[], read="", retrieval=ret.retrieval, backend=None, model=None, place=None, usage={},
                    unhelpful=False, docs={})
        if progress:
            progress(1, 1)
        n = _keep(project, author, turn, since, allow_once or not keep)
        return {**turn, "turn": n, "kept": bool(n), "based_on": [], "read": "", "consent": ret.consent,
                "answered_by": "", "notes": turn["notes"] + ([] if n else [NOT_KEPT if allow_once or not keep else FORGOTTEN])}

    try:
        ctx = build_context(project, page, question, author, depth=depth, retrieval=ret, cfg=cfg, sections=snapshot)
    except TooLong as e:
        raise Refusal(str(e)) from None
    req = ChatRequest(system=ctx.system(), user=ctx.user(), schema=SCHEMA, max_tokens=ANSWER_TOKENS,
                      context_tokens=ctx.budget)
    unreachable = UNREACHABLE.format(backend=cfg.name, model=cfg.model or "no model set")
    try:
        if allow_once:
            with once(cfg, "assistant", fingerprint=allow_once if isinstance(allow_once, str) else None):
                provider = research_provider(project, cfg, operation="assistant")
        else:
            provider = research_provider(project, cfg, operation="assistant")
    except ProviderError:
        raise Refusal(unreachable) from None
    with usage.track(project, "assistant"):
        try:
            res = provider.complete_sync(req)
        except Exception:                     # a fixed sentence: the exception text could hold the question (D15)
            raise Refusal(unreachable) from None
    if not res.ok:
        err = res.error or ""
        if err.startswith("invalid structured output") or (res.text.strip() and res.parsed is None):
            raise Refusal(MALFORMED)
        raise Refusal(unreachable)
    out, notes = clean(res.parsed, ctx, page)
    unreviewed = [i for i in out["sources"] if i in by_id and not reviewed(by_id[i])]
    from ..core.summary import provider_locality
    turn.update(**out, unreviewed=unreviewed, notes=notes + problems + ret.notes, context=ctx.summary(),
                read=ctx.read_line(), retrieval=ret.retrieval, backend=cfg.name, model=cfg.model,
                place=provider.locality or provider_locality(cfg.kind, cfg.base_url), usage=dict(res.usage or {}),
                unhelpful=False, docs=ctx.docs())
    if progress:
        progress(1, 1)
    n = _keep(project, author, turn, since, allow_once or not keep)
    if not n:
        turn["notes"] = turn["notes"] + [NOT_KEPT if allow_once or not keep else FORGOTTEN]
    return {**turn, "turn": n, "kept": bool(n), "based_on": based_on(out["sources"], by_id, unreviewed), "consent": ret.consent,
            "answered_by": answered_by(turn)}


def _keep(project: Project, author: str, turn: dict[str, Any], since, allow_once) -> int:
    """Store the turn and return its number; 0 when it was allowed once (nothing is stored) or forgotten meanwhile."""
    if allow_once:
        return 0
    return store.append(project, author, turn, since=since)


def answered_by(turn: dict[str, Any]) -> str:
    """"Answered by <backend> (<model>)" for a turn the model answered, else ""."""
    if not turn.get("backend"):
        return ""
    return f"Answered by {turn['backend']} ({turn.get('model') or 'no model set'})"


def history(project: Project, author: str) -> list[dict[str, Any]]:
    """This author's turns, each with ``docs_changed`` (computed now, D22), ``based_on`` from the current files and
    ``answered_by``: the fields `ask` returns, apart from the transient ``consent``."""
    snapshot = all_sections()
    by_id = {s.id: s for s in snapshot}
    return [{"read": "", **t, "docs_changed": store.docs_changed(t, by_id), "answered_by": answered_by(t),
             "based_on": based_on(t.get("sources") or [], by_id, t.get("unreviewed") or [])}
            for t in store.turns(project, author)]
