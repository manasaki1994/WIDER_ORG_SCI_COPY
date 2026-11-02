"""Questions the tool raised when in doubt: list, answer (applied at once), dismiss, and the queue-or-ask mode."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class AnswerBody(BaseModel):
    choice: str
    text: str = ""
    by: str = ""
    seen_choice: str | None = None   # "count" | "keep" when the answer's save reopens the query (unlock-seen-choice D6)


class ByBody(BaseModel):
    by: str = ""


class ModeBody(BaseModel):
    mode: str


def register(app: FastAPI, P) -> None:
    from ..core import questions as qs

    def guarded(p, fn):
        from ..construct.artifacts import SeenChoiceNeeded

        try:
            return fn()
        except SeenChoiceNeeded as e:   # nothing recorded: the page asks the researcher first (unlock-seen-choice D6)
            raise HTTPException(400, e.notice) from e
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/questions")
    def get_questions(status: str = "open", limit: int = 200, page: str | None = None):
        """``page`` keeps one page's questions (``setup`` also every ``setup?task=…``), as `questions list --page`."""
        p = P()
        return guarded(p, lambda: {"questions": qs.list_questions(p, None if status == "all" else status, limit, page),
                                   "counts": qs.counts(p), "mode": p.config.doubts,
                                   "hidden": qs.hidden_line(p)})

    @app.get("/api/questions/summary")
    def get_question_summary():
        p = P()
        return guarded(p, lambda: {"open": qs.counts(p).get("open", 0), "mode": p.config.doubts,
                                   "latest_open": max((q["question_id"] for q in qs.list_questions(p, "open", 10**6)), default=None)})

    @app.post("/api/questions/{question_id}/answer")
    def post_answer(question_id: int, body: AnswerBody):
        p = P()
        from ..construct.artifacts import save_seen_choice
        from ..core.journal import last_change_id

        def go():
            with save_seen_choice(body.seen_choice):
                return {**qs.answer(p, question_id, body.choice, body.text, body.by, confirm_revision=True),
                        "change_id": last_change_id()}
        return guarded(p, go)

    @app.post("/api/questions/{question_id}/dismiss")
    def post_dismiss(question_id: int, body: ByBody):
        p = P()
        return guarded(p, lambda: qs.dismiss(p, question_id, body.by))

    @app.post("/api/questions/mode")
    def post_mode(body: ModeBody):
        p = P()
        return guarded(p, lambda: {"mode": qs.set_mode(p, body.mode)})

    @app.get("/api/decisions")
    def get_decisions(round: int | None = None):   # noqa: A002 - the CLI's --round
        """The decision ledger of the refinement interview (`wider decisions list`); ``lines`` is the CLI's text."""
        from ..interview import ledger

        p = P()

        def run():
            rs = ledger.rows(p, round)
            return {"decisions": rs, "lines": ledger.lines(rs)}
        return guarded(p, run)

    @app.post("/api/decisions/export")
    def post_decisions_export():
        """Write runs/query/decisions.jsonl and decisions.csv (`wider decisions export`)."""
        from ..interview import ledger

        p = P()
        return guarded(p, lambda: ledger.export(p))
