"""UI routes for the adjacent-literature loop (/api/adjacent/*). Registered once from app.py."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..construct.propose import ProposalError


class AdjacentIn(BaseModel):
    name: str
    description: str = ""
    differs_from_construct: str = ""
    discriminating_cue: str = ""
    example_phrases: list[str] = []
    key: str | None = None
    status: str = "approved"


class AdjacentEdit(BaseModel):
    name: str | None = None
    description: str | None = None
    differs_from_construct: str | None = None
    discriminating_cue: str | None = None
    example_phrases: list[str] | None = None


class FindIn(BaseModel):
    ranking: str | None = None
    provider: str | None = None
    k: int | None = None
    top: int | None = None
    min_size: int = 3
    max_clusters: int | None = None


def register(app: FastAPI, P) -> None:
    from ..construct import adjacent as adj

    @app.get("/api/adjacent")
    def get_adjacent():
        p = P()
        try:
            return adj.summary(p)
        finally:
            p.close()

    @app.post("/api/adjacent/add")
    def post_adjacent_add(body: AdjacentIn):
        p = P()
        try:
            return adj.add(p, body.name, description=body.description, differs_from_construct=body.differs_from_construct,
                           discriminating_cue=body.discriminating_cue, example_phrases=body.example_phrases, key=body.key, status=body.status)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/adjacent/seed")
    def post_adjacent_seed():
        p = P()
        try:
            return adj.seed_from_codebook(p)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/adjacent/propose")
    def post_adjacent_propose(provider: str | None = None):
        p = P()
        try:
            r = adj.propose_from_question(p, provider_name=provider)
            return {k: v for k, v in r.items() if k != "call"}
        except (ValueError, RuntimeError, KeyError, ProposalError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/adjacent/find")
    def post_adjacent_find(body: FindIn | None = None):
        from ..rank.adjacent import find_candidates

        body = body or FindIn()
        p = P()
        try:
            r = find_candidates(p, body.ranking, provider_name=body.provider, k=body.k, top=body.top, min_size=body.min_size,
                                max_clusters=body.max_clusters)
            return {k: v for k, v in r.items() if k != "calls"}
        except (ValueError, RuntimeError, KeyError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/adjacent/{key}/status/{status}")
    def post_adjacent_status(key: str, status: str):
        p = P()
        try:
            return adj.set_status(p, key, status)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/adjacent/{key}/edit")
    def post_adjacent_edit(key: str, body: AdjacentEdit):
        p = P()
        try:
            return adj.update_entry(p, key, **{k: v for k, v in body.model_dump().items() if v is not None})
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/adjacent/cases/{run_id}")
    def get_adjacent_cases(run_id: str, sample: str = "development"):
        from ..screen.devgate import DevSampleIncomplete
        from ..screen.evaluate import evaluate

        p = P()
        try:
            ev = evaluate(p, run_id, sample)
            return {"run_id": run_id, "sample": sample, "by_adjacent": ev["by_adjacent"], "cases": ev["adjacent_cases"]}
        except (ValueError, KeyError) as e:
            if isinstance(e, DevSampleIncomplete):
                raise   # 409 with its counts (D4)
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
