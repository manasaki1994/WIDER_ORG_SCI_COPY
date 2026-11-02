"""Backend consent routes (design Decision 17): ``/api/providers/consent`` and ``/api/embeddings/consent`` with their
per-backend POST and DELETE. Registered once from ``app.py``. Also installs the app-wide handler that turns
:class:`~wider.core.backend_consent.BackendConsentRequired` into HTTP 403 ``{"detail": {"message",
"backend_consent_required": {backend, kind, operation, destination, locality, sent, warning, changed}}}``; the page's
``api()`` shows the consent dialog for it and retries once after "Allow and continue".
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..core import backend_consent as bc


class ConsentBody(BaseModel):
    operations: list[str]


def register(app: FastAPI, P) -> None:
    """``P()`` returns a freshly loaded Project (the app's accessor)."""

    @app.exception_handler(bc.BackendConsentRequired)
    async def backend_consent_required(request: Request, exc: bc.BackendConsentRequired):
        return JSONResponse(status_code=403, content={"detail": exc.payload()})

    def _get(embedding: bool):
        p = P()
        try:
            return bc.status(p, embedding=embedding, probe=False)
        finally:
            p.close()

    def _allow(name: str, body: ConsentBody, embedding: bool):
        p = P()
        try:
            try:
                return bc.allow_backend(p, name, body.operations, embedding=embedding)
            except KeyError:
                raise HTTPException(404, f"unknown {'embedding' if embedding else 'provider'} {name}")
            except ValueError as e:
                raise HTTPException(400, str(e))
        finally:
            p.close()

    def _revoke(name: str, operation: str | None, embedding: bool):
        p = P()
        try:
            try:
                bc.revoke_backend(p, name, [operation] if operation else None, embedding=embedding)
            except ValueError as e:
                raise HTTPException(400, str(e))
            return bc.status(p, embedding=embedding, probe=False)
        finally:
            p.close()

    @app.get("/api/providers/consent")
    def get_provider_consent():
        return _get(False)

    @app.post("/api/providers/{name}/consent")
    def post_provider_consent(name: str, body: ConsentBody):
        return _allow(name, body, False)

    @app.delete("/api/providers/{name}/consent")
    def delete_provider_consent(name: str, operation: str | None = None):
        return _revoke(name, operation, False)

    @app.get("/api/embeddings/consent")
    def get_embedding_consent():
        return _get(True)

    @app.post("/api/embeddings/{name}/consent")
    def post_embedding_consent(name: str, body: ConsentBody):
        return _allow(name, body, True)

    @app.delete("/api/embeddings/{name}/consent")
    def delete_embedding_consent(name: str, operation: str | None = None):
        return _revoke(name, operation, True)
