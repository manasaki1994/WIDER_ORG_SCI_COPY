"""Researcher list and names in shared files: /api/researchers/* (card "Names in shared files" on the Recall page;
researcher-attribution D8). Names go in the body, never the URL."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class NameBody(BaseModel):
    name: str


class JoinBody(BaseModel):
    name: str
    into: str


class ModeBody(BaseModel):
    mode: str


def register(app: FastAPI, P) -> None:
    from ..core import researchers as rs

    def guarded(fn):
        p = P()
        try:
            return fn(p)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    def state(p) -> dict:
        mode = rs.mode_of(p)
        return {"researchers": rs.entries(p), "mode": mode, "mode_line": rs.MODE_LINES[mode],
                "not_verified": rs.NOT_VERIFIED}

    @app.get("/api/researchers")
    def get_researchers():
        """The list (as ``wider researchers list --json``), the mode and its line."""
        return guarded(state)

    @app.post("/api/researchers/confirm")
    def post_confirm(body: NameBody):
        return guarded(lambda p: {"message": rs.confirm(p, body.name), **state(p)})

    @app.post("/api/researchers/join")
    def post_join(body: JoinBody):
        return guarded(lambda p: {"message": rs.join(p, body.name, body.into), **state(p)})

    @app.post("/api/researchers/attribution")
    def post_attribution(body: ModeBody):
        return guarded(lambda p: {"message": rs.set_attribution(p, body.mode), **state(p)})
