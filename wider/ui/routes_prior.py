"""Prior-review routes (/api/prior-reviews/*). Registered once from ``app.py``.

Upload, paste, list, remove and policy are local. ``resolve`` and ``chain`` (OpenAlex) return 403 while the
project's lookup switch is off; ``coverage`` is local. The engines live in ``wider/prior``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..construct.artifacts import SeenChoiceNeeded
from ..prior.reviews import PRIOR_SUFFIXES, PolicyError, safe_filename


class TextIn(BaseModel):
    text: str
    label: str = "pasted list"
    filename: str = ""


class PolicyIn(BaseModel):
    none_known: bool | None = None
    allow_openalex_lookup: bool | None = None
    allow_llm_excerpts: bool | None = None
    max_citing_per_paper: int | None = None


def register(app: FastAPI, P) -> None:
    """``P()`` returns a freshly loaded Project (the app's accessor)."""
    from ..prior import reviews

    def _run(fn):
        p = P()
        try:
            return fn(p)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        except SeenChoiceNeeded as e:   # nothing run: the page asks the researcher first (D21, Q2)
            raise HTTPException(400, e.notice) from e
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)) from e
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/prior-reviews")
    def get_prior_reviews():
        def go(p):
            from ..ingest.importer import upload_plan
            plan = upload_plan(p, "the chaining file")
            return {"reviews": reviews.list_reviews(p), **reviews.summary(p), "chain_choice": plan["choice"],
                    "chain_notice": plan["asking"]}
        return _run(go)

    @app.post("/api/prior-reviews/policy")
    def post_prior_policy(body: PolicyIn):
        return _run(lambda p: reviews.set_policy(p, **body.model_dump()))

    @app.post("/api/prior-reviews/upload")
    async def post_prior_upload(file: UploadFile = File(...), label: str = Form("")):
        name = safe_filename(file.filename or "")
        if Path(name).suffix.lower() not in PRIOR_SUFFIXES:
            raise HTTPException(400, f"unsupported file type; use one of {', '.join(sorted(PRIOR_SUFFIXES))}")
        data = await file.read()

        def go(p):
            tmp = p.path("prior_reviews", ".upload")
            tmp.mkdir(parents=True, exist_ok=True)
            src = tmp / name
            try:
                src.write_bytes(data)
                return reviews.add_file(p, src, label=label.strip() or None)
            finally:
                src.unlink(missing_ok=True)
        return _run(go)

    @app.post("/api/prior-reviews/text")
    def post_prior_text(body: TextIn):
        return _run(lambda p: reviews.add_text(p, body.text, body.label.strip() or "pasted list", body.filename))

    @app.delete("/api/prior-reviews/{review_id}")
    def delete_prior_review(review_id: int):
        def go(p):
            reviews.remove(p, review_id)
            return {"ok": True}
        return _run(go)

    @app.get("/api/prior-reviews/{review_id}/papers")
    def get_prior_papers(review_id: int):
        def go(p):
            review = reviews.get(p, review_id)
            return {"review": review, "papers": reviews.papers(p, review_id)}
        return _run(go)

    @app.post("/api/prior-reviews/resolve")
    def post_prior_resolve(limit: int | None = None):
        def go(p):
            from ..prior import chain
            return chain.resolve(p, limit=limit)
        return _run(go)

    @app.get("/api/prior-reviews/coverage")
    def get_prior_coverage(scope: str = "platforms"):
        def go(p):
            from ..prior import coverage
            return coverage.check(p, scope=scope)
        return _run(go)

    @app.post("/api/prior-reviews/chain")
    def post_prior_chain(direction: str = "both", limit: int | None = None, seen_choice: str | None = None):
        def go(p):
            from ..prior import chain
            return chain.chain_and_export(p, direction=direction, limit=limit, seen_choice=seen_choice)
        return _run(go)
