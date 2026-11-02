"""Background-run routes: start a long stage as a job, list jobs with progress, ETA and cost so far, cancel.

The synchronous run endpoints stay (scripts and tests use them); the pages start these jobs instead.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from ..core.stage_result import last_result
from .app import (
    ScreenRun,  # module level: FastAPI must resolve the body model by name (see app.py)
    screen_kwargs,
    screen_preflight,
    screen_research,
)
from .jobs import JobRunner


def register(app: FastAPI, P, runner: JobRunner) -> None:
    def root():
        return app.state.project.root

    def start(kind: str, stage: str, label: str, params: dict, fn) -> dict:
        try:
            return {"job_id": runner.start(root(), kind, stage, label, params, fn)}
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e

    def _research(provider: str | None, stage: str, text: bool = False) -> None:
        """Consent (and, for construct, a text-writing backend) is checked here, so a refusal is an answer and not
        a failed job."""
        from ..core.backend_consent import require_research
        from ..providers.registry import require_text
        p = P()
        try:
            if text:
                try:
                    require_text(p.provider(provider) if provider else p.provider_for(stage))
                except (KeyError, TypeError):
                    pass
                except ValueError as e:
                    raise HTTPException(400, str(e)) from e
            require_research(p, provider, stage)
        finally:
            p.close()

    def _screen_estimate(body) -> dict | None:
        from ..core.estimate import estimate_screen
        p = P()
        try:
            r = estimate_screen(p, body.mode, scope=body.scope, protocol=body.protocol, provider_name=body.provider,
                                prompt_version=body.prompt_version, limit=body.limit,
                                reuse_evaluations=body.mode != "pass" and bool(getattr(body, "reuse_evaluations", False)),
                                provider_b=body.provider_b, provider_c=body.provider_c)
            return {k: r[k] for k in ("min_usd", "max_usd", "priced", "calls")}
        except (ValueError, KeyError, TypeError):
            return None
        finally:
            p.close()

    def with_stage(p, stage: str, raw: dict) -> dict:
        return {**raw, "stage_result": last_result(p, stage)}

    @app.post("/api/jobs/screen")
    def job_screen(body: ScreenRun):
        from ..screen.engine import check_protocol_name, check_run_args, screen_async, whole_limit
        from ..screen.protocol import run_protocol_async, second_protocol_name, second_screen_async

        try:
            body.limit = whole_limit(body.limit)
            check_run_args(body.pass_name if body.mode == "pass" else "A", body.limit)
            if body.mode in ("protocol", "second"):   # refused before a job starts
                if body.mode == "protocol":
                    check_protocol_name(body.protocol)
                else:
                    second_protocol_name(body.protocol)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

        def screen_progress(progress):
            def cb(n_done, n_total, record_id=None, decision=None):
                progress(n_done, n_total, f"last: record {record_id}, {decision}" if record_id is not None else None)
            return cb

        async def fn(p, progress_raw):
            kw = {**screen_kwargs(body, "ui"), "progress": screen_progress(progress_raw)}
            if body.mode == "protocol":
                res = await run_protocol_async(p, body.scope, body.protocol, **kw)
            elif body.mode == "second":
                res = await second_screen_async(p, body.protocol, **kw)
            else:
                res = await screen_async(p, body.scope, **kw)
            return with_stage(p, "screen", res)

        if body.mode == "second":   # refused up front, so no job is started for a switched-off second screen
            from ..screen.protocol import check_second_screen
            p = P()
            try:
                check_second_screen(p)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            finally:
                p.close()
        p = P()
        try:   # 409 backend_change / new_set_needed / scope_needed and 200 already_done: no job starts
            refused = screen_preflight(p, body)
        finally:
            p.close()
        if refused is not None:
            return refused
        p = P()
        try:   # 403 backend_consent_required before a job starts, for every pass backend
            screen_research(p, body)
        finally:
            p.close()
        what = {"protocol": f"protocol {body.protocol}", "second": f"second screen of {body.protocol}"}.get(body.mode, f"pass {body.pass_name}")
        params = body.model_dump()
        params["estimate"] = _screen_estimate(body)   # the projection's bounds and call count (Decision 15)
        return start("screen", "screen", f"Screening: {what} on {body.scope}", params, fn)

    @app.post("/api/jobs/embed")
    def job_embed(embedding: str | None = None, allow_download: bool = False):
        from ..core.backend_consent import require_backend_consent
        from ..core.backends import is_downloaded
        from ..rank.corpus import embed_corpus

        p = P()
        try:
            cfg = p.embedding(embedding)
            if cfg.kind == "sentence_transformers" and not allow_download and is_downloaded(cfg) is False:
                return {"skipped": True, "error": f"{cfg.model} is not downloaded"}
            # before the job starts: BackendConsentRequired -> 403, no job listed
            require_backend_consent(p, cfg, "research", embedding=True)
        except KeyError as e:
            raise HTTPException(400, str(e).strip("'")) from e
        finally:
            p.close()

        def fn(p, progress):
            return with_stage(p, "rank", embed_corpus(p, cfg.name, progress=progress))

        return start("embed", "rank", f"Embedding the corpus with {cfg.name}", {"embedding": cfg.name}, fn)

    @app.post("/api/jobs/rank-update")
    def job_rank_update(use_older: bool = False, allow_download: bool = False, confirm: bool = False):
        """"Update ranking" (``wider rank update``, ui-redesign D5). When the embeddings were made from older texts
        and the researcher has not confirmed yet, the answer is the check (``confirm_needed``: what is sent, where and
        at what cost) and no job starts; consent is asked by the usual 403 before a job starts."""
        from ..core.backend_consent import require_backend_consent
        from ..rank.update import check, update

        p = P()
        try:
            chk = check(p)
            if not chk["embeddings_match"] and not use_older:
                if not confirm:
                    return {**chk, "confirm_needed": True}
                if chk["needs_download"] and not allow_download:
                    return {"skipped": True, "error": f"{chk['model']} is not downloaded"}
                require_backend_consent(p, p.embedding(None), "research", embedding=True)
            require_backend_consent(p, p.embedding(None), "research", embedding=True)   # the anchors are embedded
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e).strip("'")) from e
        finally:
            p.close()

        def fn(p, progress):
            return with_stage(p, "rank", update(p, use_older=use_older, progress=progress))

        what = "Ranking from the older embeddings" if use_older else "Updating the ranking"
        return start("rank-update", "rank", what, {"use_older": use_older}, fn)

    @app.post("/api/jobs/enrich")
    def job_enrich(limit: int | None = 100, retry: bool = False):
        from ..core.lookups import require_consent
        from ..enrich.fetch import enrich, summary

        if limit is not None and limit < 1:
            raise HTTPException(400, "the batch size (limit) must be at least 1")
        p = P()
        try:
            require_consent(p, "enrich")   # before the job starts: ConsentRequired -> 403, no job listed
        finally:
            p.close()

        def fn(p, progress):
            stats = enrich(p, limit=limit, progress=progress, retry=retry)
            return {"stats": stats, "summary": summary(p), "stage_result": last_result(p, "enrich")}

        what = "Retrying failed lookups" if retry else "Fetching abstracts"
        return start("enrich", "enrich", f"{what} (up to {limit})" if limit else what, {"limit": limit, "retry": retry}, fn)

    @app.post("/api/jobs/propose/{kind}")
    def job_propose(kind: str, provider: str | None = None, seen_choice: str | None = None):
        from ..construct.artifacts import SeenChoiceNeeded, need_save_choice, save_seen_choice
        from ..construct.propose import ProposalError, check_kind, propose_async

        try:
            check_kind(kind)
        except ProposalError as e:
            raise HTTPException(400, str(e))
        p = P()
        try:   # before the job starts: no job and no model call without the choice (unlock-seen-choice D6)
            seen_choice = need_save_choice(p, kind, seen_choice) if kind == "synonyms" else None
        except SeenChoiceNeeded as e:
            raise HTTPException(400, e.notice) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        finally:
            p.close()

        _research(provider, "construct", text=True)

        async def fn(p, progress):
            progress(0, 1, f"asking {provider or 'the default backend'}")
            with save_seen_choice(seen_choice):
                row = await propose_async(p, kind, provider_name=provider, confirm_revision=True)
            progress(1, 1)
            stage = "query" if kind == "synonyms" and row.get("origin") == "import" else "construct"
            return with_stage(p, stage, row)

        return start("propose", "construct", f"Proposing {kind}", {"kind": kind, "provider": provider}, fn)

    @app.get("/api/jobs")
    def list_jobs(active: bool = False, limit: int = 20):
        p = P()
        try:
            return runner.list(p, active_only=active, limit=limit)
        finally:
            p.close()

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        p = P()
        try:
            j = runner.get(p, job_id)
            if j is None:
                raise HTTPException(404, "no such job")
            return j
        finally:
            p.close()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        if not runner.cancel(job_id):
            raise HTTPException(409, "this run is not running")
        return {"ok": True}
