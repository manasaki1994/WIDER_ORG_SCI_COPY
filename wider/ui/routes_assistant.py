"""Ask WIDER's routes (guide-assistant D8, D14, D15): ``POST /api/assistant/ask`` runs one question as a job,
``GET``/``DELETE /api/assistant/history`` read and forget the reviewer's conversation, ``POST /api/assistant/unhelpful``
marks a turn, ``GET``/``POST /api/assistant/budget`` the context budget. Registered once from ``app.py``. The author
is the page's name (``X-Wider-Author``); the send notices come from ``/api/send-notice/assistant`` and
``/assistant_embed``.

The job's stored label is "Ask WIDER", its params ``{page, depth}`` and its result ``{turn, consent}``; the question
reaches the job only in memory and a failure stores a fixed sentence, so the jobs table never holds what was typed
or answered. The page reads the answer from the conversation."""
from __future__ import annotations

from contextlib import ExitStack

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from ..assistant import ask as A
from ..core import backend_consent as bc
from ..core.journal import current_author
from .jobs import JobCancelled

LABEL = "Ask WIDER"
FAILED = "Ask WIDER could not answer. Ask again."
INDEXING = "Updating the index of WIDER's documents…"


class AskBody(BaseModel):
    question: str
    page: str
    depth: str | None = None


class UnhelpfulBody(BaseModel):
    turn: int
    value: bool = True


class BudgetBody(BaseModel):
    value: int | None = None


class AskFailed(Exception):
    """A failed question: ``job_message`` is the spec's sentence, stored as the job's error as is."""

    def __init__(self, message: str):
        super().__init__(message)
        self.job_message = message


def _once(stack: ExitStack, request: Request, cfg, emb) -> bool:
    """Enter the one-question allowances of the ``X-Wider-Consent-Once`` header for this question's backends (D8):
    the job thread copies this context, and ``allow_once`` refuses a fingerprint that no longer matches. True when
    any was entered: the question was allowed once, so its turn is not stored either."""
    entered = False
    for kind, name, fp in bc.parse_once(request.headers.get("x-wider-consent-once")):
        if kind == "llm" and name == cfg.name:
            stack.enter_context(bc.allow_once(cfg, "assistant", fp))
            entered = True
        elif kind == "embedding" and emb is not None and name == emb.name:
            stack.enter_context(bc.allow_once(emb, "assistant", fp, embedding=True))
            entered = True
    return entered


# a turn allowed once is not stored, so the job's result carries what the panel shows of it (history() has it not)
UNKEPT_FIELDS = ("question", "answer", "page", "depth", "sources", "unreviewed", "show", "drafts", "notes", "read",
                 "based_on", "answered_by", "docs")


def register(app: FastAPI, P) -> None:
    """``P()`` returns a freshly loaded Project (the app's accessor)."""

    def _author() -> str:
        author = current_author()
        if not author:
            raise HTTPException(400, A.NO_AUTHOR)
        return author

    @app.post("/api/assistant/ask")
    def post_ask(body: AskBody, request: Request):
        if body.depth not in (None, "simpler", "detail"):
            raise HTTPException(400, "depth takes simpler or detail")
        p = P()
        try:
            try:
                A._check(p, body.page, body.question, current_author())
                cfg = A.assistant_provider(p)
            except A.Refusal as e:
                raise HTTPException(400, str(e)) from None
            try:
                emb = p.embedding()
            except (KeyError, TypeError):
                emb = None
            root = p.root
        finally:
            p.close()
        author, question, page, depth = current_author(), body.question, body.page, body.depth

        def run(q, progress):
            from ..assistant import retrieve as R
            from ..assistant.sections import all_sections, docs_key

            m, _, _ = R.meaning_backend(q)
            if m is not None and not R.index_path(q, m, docs_key(all_sections())).exists():
                progress(0, None, INDEXING)
            try:
                r = A.ask(q, page, question, author, depth=depth, progress=progress, keep=not once)
            except A.Refusal as e:
                raise AskFailed(str(e)) from None
            except bc.BackendConsentRequired:
                raise AskFailed(FAILED) from None
            except JobCancelled:
                raise
            except Exception:     # noqa: BLE001  any other text could hold the question
                raise AskFailed(FAILED) from None
            return {"turn": r["turn"], "consent": r.get("consent"), "kept": r.get("kept", True),
                    **({} if r.get("kept", True) else {k: r.get(k) for k in UNKEPT_FIELDS})}

        with ExitStack() as stack:
            once = _once(stack, request, cfg, emb)
            q = P()
            try:
                bc.require_backend_consent(q, cfg, "assistant")   # a refusal is the 403 dialog, not a failed job
            finally:
                q.close()
            try:
                job = app.state.jobs.start(root, "assistant", "assistant", LABEL, {"page": page, "depth": depth}, run)
            except RuntimeError:
                raise HTTPException(409, A.BUSY) from None
        return {"job_id": job, "waiting": f"Waiting for {cfg.name} ({cfg.model or 'no model set'})…"}

    @app.get("/api/assistant/history")
    def get_history():
        author = current_author()
        if not author:
            return {"author": "", "turns": []}
        p = P()
        try:
            return {"author": author, "turns": A.history(p, author)}
        finally:
            p.close()

    @app.delete("/api/assistant/history")
    def delete_history():
        from ..assistant import store

        author = _author()
        p = P()
        try:
            return {"deleted": store.forget(p, author)}
        finally:
            p.close()

    @app.post("/api/assistant/unhelpful")
    def post_unhelpful(body: UnhelpfulBody):
        from ..assistant import store

        author = _author()
        p = P()
        try:
            try:
                store.mark_unhelpful(p, author, body.turn, body.value)
            except KeyError as e:
                raise HTTPException(404, str(e).strip("'\"")) from None
            return {"turn": body.turn, "unhelpful": body.value}
        finally:
            p.close()

    def _budget(p):
        from ..assistant import context as C

        try:
            cfg = A.assistant_provider(p)
        except A.Refusal:
            cfg = None
        return {"value": p.config.assistant.context_tokens, "budget": C.budget(p, cfg),
                "default": C.default_budget(p, cfg), "min": C.BUDGET_MIN, "max": C.BUDGET_MAX}

    @app.get("/api/assistant/budget")
    def get_budget():
        p = P()
        try:
            return _budget(p)
        finally:
            p.close()

    @app.post("/api/assistant/budget")
    def post_budget(body: BudgetBody):
        from ..assistant import context as C

        p = P()
        try:
            try:
                C.set_budget(p, body.value, current_author() or None)
            except ValueError as e:
                raise HTTPException(400, str(e)) from None
            return _budget(p)
        finally:
            p.close()
