"""Online-lookup routes (/api/lookups*): the per-project consent and the local log of outside requests. Registered once
from ``app.py``. Also installs the app-wide handler that turns :class:`~wider.core.lookups.ConsentRequired` into
HTTP 403 ``{"detail": {"message", "consent_required": {purpose, services, fields}}}``; the page's ``api()`` shows the
consent dialog for it and retries once after "Allow and continue".
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..core import lookups


def register(app: FastAPI, P) -> None:
    """``P()`` returns a freshly loaded Project (the app's accessor)."""

    @app.exception_handler(lookups.ConsentRequired)
    async def consent_required(request: Request, exc: lookups.ConsentRequired):
        return JSONResponse(status_code=403, content={"detail": exc.payload()})

    def _status(p):
        return {**lookups.status(p), "log": lookups.log(p, 5)}

    @app.get("/api/lookups")
    def get_lookups():
        p = P()
        try:
            return _status(p)
        finally:
            p.close()

    @app.post("/api/lookups/allow")
    def post_allow():
        p = P()
        try:
            lookups.allow(p)
            return _status(p)
        except OSError as e:   # e.g. a read-only project.yaml: the page's consent dialog shows this and stays open
            raise HTTPException(500, lookups.not_stored_message(e))
        finally:
            p.close()

    @app.post("/api/lookups/revoke")
    def post_revoke():
        p = P()
        try:
            lookups.revoke(p)
            return _status(p)
        finally:
            p.close()

    @app.post("/api/lookups/terms/allow")
    def post_terms_allow():
        p = P()
        try:
            lookups.allow_terms(p)
            return _status(p)
        except OSError as e:   # e.g. a read-only project.yaml: the page's consent dialog shows this and stays open
            raise HTTPException(500, lookups.not_stored_message(e))
        finally:
            p.close()

    @app.post("/api/lookups/terms/revoke")
    def post_terms_revoke():
        p = P()
        try:
            lookups.revoke_terms(p)
            return _status(p)
        finally:
            p.close()

    @app.delete("/api/lookups/cache")
    def delete_cache():
        p = P()
        try:
            return {"removed": lookups.clear_cache(p), **_status(p)}
        finally:
            p.close()

    @app.get("/api/lookups/log")
    def get_log(limit: int = 50):
        if limit < 1:
            raise HTTPException(400, "limit must be at least 1")
        p = P()
        try:
            return lookups.log(p, limit)
        finally:
            p.close()
