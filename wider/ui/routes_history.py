"""History and undo routes, and the restore route for artifact versions."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def name_as_stored(author: str | None) -> str | None:
    """Changes are journaled with the first 80 characters of a name (``app.py``), so a filter is cut the same way."""
    return author.strip()[:80] if author is not None else None


def register(app: FastAPI, P) -> None:
    from ..core import journal

    def guarded(p, fn, hide=False):
        """``hide``: a refusal is worded as History shows it while Blind is on, without the sample (risk-checker R2)."""
        from ..construct.artifacts import SeenChoiceNeeded

        try:
            return fn()
        except SeenChoiceNeeded as e:   # nothing done: the page asks the researcher first (unlock-seen-choice D6)
            raise HTTPException(400, e.notice) from e
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from e
        except ValueError as e:
            raise HTTPException(409, journal.sample_name_hider(p)(str(e)) if hide else str(e)) from e
        finally:
            p.close()

    def blinded(p, c, hide):
        """An undo or redo answer as the History page shows it while Blind is on: no sample beside a record (37e6)."""
        if hide and isinstance(c, dict):
            h = journal.sample_name_hider(p)
            c = {**c, **{k: h(c[k]) for k in ("summary", "notice") if c.get(k)}}
        return c

    @app.get("/api/history")
    def get_history(kind: str | None = None, record_id: int | None = None, author: str | None = None,
                    blind: bool = False, hide_samples: bool = False, limit: int = 100):
        """Changes newest first; ``blind`` with ``author`` shows only that reviewer's own changes (labelling view);
        ``hide_samples`` (and ``blind``) leave out which sample a labelled record is in."""
        author = name_as_stored(author)
        p = P()
        if blind and not author:
            raise HTTPException(400, "blind history needs the reviewer's name")
        try:
            return journal.changes(p, kind, record_id, author if (blind or author) else None, limit,
                                   hide_samples=hide_samples or blind)
        except ValueError as e:   # an unknown kind is the request's fault
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/history/kinds")
    def get_history_kinds():
        """The Show list's choices, from the engine (`wider history kinds` prints the same)."""
        return journal.kinds()

    @app.post("/api/history/{change_id}/undo")
    def post_undo(change_id: int, seen_choice: str | None = None, other: bool = False, hide_samples: bool = False):
        """Undo one change; another person's change answers 409 with its ``author`` until ``other`` (the page's
        dialog, D7), and 400 when the page sends no name."""
        from fastapi.responses import JSONResponse

        from ..construct.artifacts import SeenChoiceNeeded
        from ..construct.artifacts import seen_choice as check_choice

        try:
            check_choice(seen_choice)
        except ValueError as e:   # a bad value is the request's fault, not a conflict
            raise HTTPException(400, str(e)) from e
        p = P()

        def go():
            try:
                return blinded(p, journal.undo(p, change_id, seen_choice=seen_choice, other=other), hide_samples)
            except journal.OtherAuthor as e:   # nothing undone: the page names the author and asks
                return JSONResponse({"detail": str(e), "author": e.author}, status_code=409)
            except journal.NeedName as e:
                raise HTTPException(400, str(e)) from e
            except SeenChoiceNeeded as e:   # nothing undone: the page asks the researcher first (D21, Q2)
                raise HTTPException(400, e.notice) from e
        return guarded(p, go, hide_samples)

    @app.post("/api/history/undo-last")
    def post_undo_last(author: str | None = None, seen_choice: str | None = None, hide_samples: bool = False):
        """Undo ``author``'s newest change that is not yet undone; repeated calls go further back (409 when none).
        Without ``author`` the page's name header signs (D7)."""
        author = author if (author or "").strip() else journal.AUTHOR.get()
        if not (author or "").strip():
            raise HTTPException(400, "undo-last needs the reviewer's name")
        p = P()
        return guarded(p, lambda: blinded(p, journal.undo_last(p, name_as_stored(author), seen_choice=seen_choice), hide_samples), hide_samples)

    @app.post("/api/history/redo-last")
    def post_redo_last(author: str | None = None, hide_samples: bool = False):
        """Redo ``author``'s newest undo still in effect (Ctrl/Cmd+Shift+Z); repeated calls walk back through the undos
        (409 when none or refused). Without ``author`` the page's name header signs (D4)."""
        author = author if (author or "").strip() else journal.AUTHOR.get()
        if not (author or "").strip():
            raise HTTPException(400, "redo-last needs the reviewer's name")
        p = P()
        return guarded(p, lambda: blinded(p, journal.redo_last(p, name_as_stored(author)), hide_samples), hide_samples)

    @app.post("/api/construct/{kind}/{version}/restore")
    def post_restore(kind: str, version: int, seen_choice: str | None = None):
        from ..construct.artifacts import restore_version

        p = P()
        return guarded(p, lambda: restore_version(p, kind, version, confirm_revision=True, seen_choice=seen_choice))
