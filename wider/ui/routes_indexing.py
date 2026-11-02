"""Indexing check routes (vision change 2a, D10, task 2.10): the known-item strings per missed seed paper and the
researcher's verdict. The CLI twin is ``wider/cli_indexing.py``; both call :mod:`wider.search.indexing`."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class VerdictBody(BaseModel):
    benchmark_id: str
    platform: str
    verdict: str
    by: str | None = None


def register(app: FastAPI, proj) -> None:
    @app.get("/api/query/indexing")
    def get_indexing(version: int | None = None, platform: str | None = None):
        """The seed papers each platform did not retrieve, with the known-item string and the verdict so far."""
        from ..search import indexing

        p = proj()
        try:
            return indexing.known_item_strings(p, version, platform)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/indexing")
    def post_indexing(body: VerdictBody):
        """Store one verdict (journalled ``seed_indexing``, undoable); 400 with the reason when refused."""
        from ..search import indexing

        p = proj()
        try:
            return indexing.set_verdict(p, body.benchmark_id, body.platform, body.verdict, body.by)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
