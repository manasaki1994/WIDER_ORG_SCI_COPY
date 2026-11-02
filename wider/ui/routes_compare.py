"""Configuration comparison routes (change screening-config-comparison, D16): list, show, estimate, a synchronous run
and a background job. Each calls the engine function the CLI calls (``wider/screen/compare.py``). An unlocked sample,
a different list under a stored name and a changed or removed backend answer 409; a missing sample, a missing
configuration and the probability sample answer 400; a backend without consent answers 403 before anything is stored
(the handler in ``routes_backend_consent.py``)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class CompareBody(BaseModel):   # module level: FastAPI must resolve the body model by name
    name: str
    sample: str | None = None
    configs: list[str] = Field(default_factory=list)
    repeats: int = 3
    reuse_evaluations: bool = False
    author: str | None = None


def _refusal(e: Exception) -> Exception:
    from ..screen.compare import Conflict
    from ..screen.devgate import DevSampleIncomplete

    if isinstance(e, DevSampleIncomplete):
        return e   # 409 with its counts, from the app's handler
    if isinstance(e, KeyError):
        return HTTPException(404, str(e.args[0]) if e.args else str(e))
    return HTTPException(409 if isinstance(e, Conflict) else 400, str(e))


def register(app: FastAPI, P, runner) -> None:
    from ..screen import compare

    @app.get("/api/screen/compare")
    def compare_list():
        p = P()
        try:
            return {"comparisons": compare.list_comparisons(p)}
        finally:
            p.close()

    @app.get("/api/screen/compare/{name}")
    def compare_show(name: str):
        p = P()
        try:
            return compare.results(p, name)
        except (ValueError, KeyError) as e:
            raise _refusal(e) from e
        finally:
            p.close()

    @app.post("/api/screen/compare/estimate")
    def compare_estimate(body: CompareBody):
        p = P()
        try:
            return compare.estimate(p, body.name, body.sample, body.configs, body.repeats,
                                    reuse_evaluations=body.reuse_evaluations)
        except (ValueError, KeyError) as e:
            raise _refusal(e) from e
        finally:
            p.close()

    def _check(body: CompareBody) -> None:
        """Every refusal and the consent of each distinct backend, before a row is stored or a job is listed."""
        from ..core.backend_consent import require_backend_consent

        p = P()
        try:
            prep = compare.prepare(p, body.name, body.sample, body.configs, body.repeats)
            for prov in dict.fromkeys(c["provider"] for c in prep["configs"]):
                require_backend_consent(p, p.provider(prov), "research")   # BackendConsentRequired -> 403
        except (ValueError, KeyError) as e:
            raise _refusal(e) from e
        finally:
            p.close()

    def _kwargs(body: CompareBody) -> dict:
        return {"repeats": body.repeats, "reuse_evaluations": body.reuse_evaluations, "author": body.author}

    @app.post("/api/screen/compare")
    async def compare_run(body: CompareBody):
        _check(body)
        p = P()
        try:
            return await compare.run_async(p, body.name, body.sample, body.configs, **_kwargs(body))
        except (ValueError, KeyError) as e:
            raise _refusal(e) from e
        finally:
            p.close()

    @app.post("/api/jobs/compare")
    def job_compare(body: CompareBody):
        _check(body)
        current = {"what": ""}

        async def fn(p, progress):
            def on_run(c, k, run_id):
                current["what"] = f"{c['label']} repeat {k}"
                progress(0, None, current["what"])

            def prog(n_done, n_total=None, *rest):
                progress(n_done, n_total, current["what"], *rest)

            return await compare.run_async(p, body.name, body.sample, body.configs, progress=prog, on_run=on_run,
                                           **_kwargs(body))

        try:
            job_id = runner.start(app.state.project.root, "compare", "screen",
                                  f"Comparing configurations: {body.name} on {body.sample or 'the development sample'}",
                                  body.model_dump(), fn)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"job_id": job_id}
