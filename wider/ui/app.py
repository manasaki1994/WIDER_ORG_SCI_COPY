"""Local FastAPI app. Pages are added per stage; this shell has the setup page and probes."""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from urllib.parse import unquote

import yaml
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core.journal import last_change_id
from ..core.project import Project
from ..core.stage_result import last_result

STATIC = Path(__file__).parent / "static"


# Request bodies live at module level: with `from __future__ import annotations`, a model defined inside
# create_app() cannot be resolved by FastAPI and the route reads the body as a missing query parameter.
def with_result(p: Project, stage: str, raw):
    """Attach the stage's freshly recorded result to a run endpoint's reply (dicts only; lists are wrapped)."""
    res = last_result(p, stage)
    if isinstance(raw, dict):
        return {**raw, "stage_result": res}
    return {"items": raw, "stage_result": res}


class ArtifactText(BaseModel):
    text: str
    note: str = ""
    seen_choice: str | None = None   # "count" | "keep" when the save reopens the query (unlock-seen-choice D6)

class ImportText(BaseModel):
    text: str
    note: str = ""
    platform: str = "ebsco"
    seen_choice: str | None = None   # "count" | "keep" when the save reopens the query (unlock-seen-choice D6)

class ReviewRun(BaseModel):
    platform: str | None = None
    readers: list[str] | None = None
    version: int | None = None

class DedupDecision(BaseModel):
    raw_a: int
    raw_b: int
    decision: str
    by: str = ""
    keep: int | None = None

def _correction_needed(e: Exception):
    """409 for a change on a locked sample or a sealed record without a reason; the page then asks for one (D2)."""
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(e), "correction_needed": True}, status_code=409)


class LabelBody(BaseModel):
    sample_id: int | None = None
    record_id: int | None = None
    item: str | None = None      # an opaque re-check or second-reviewer queue item (D12)
    recheck: bool = False
    reviewer: str
    decision: str
    reason: str = ""
    labels: dict = {}
    note: str = ""
    correction_reason: str | None = None   # a change on a locked sample or a sealed record (D2)

class IndependenceBody(BaseModel):
    sample: str
    reviewer: str
    basis: str
    by: str | None = None

class AdjBody(BaseModel):
    sample: str
    record_id: int
    decision: str
    by: str = ""
    reason: str = ""
    correction_reason: str | None = None
    note: str = ""

class RankKeep(BaseModel):
    version: int | None = None   # the ranking version to keep (needed when several need review)


class ScreenRun(BaseModel):
    scope: str
    pass_name: str = "A"
    provider: str | None = None
    prompt_version: int | None = None
    limit: bool | int | float | str | None = None   # checked by the route: a fraction, a word or true is refused
    mode: str = "pass"  # pass | protocol | second
    protocol: str = "main"
    backend_change: str | None = None   # continue | new, after a 409 backend_change
    new_set: bool = False
    set_label: str | None = None
    from_set: int | None = None         # second screen: the source set (default: the active set)
    reuse_from: int | None = None
    no_reuse: bool = False
    reuse_evaluations: bool = False
    provider_b: str | None = None       # protocol: pass B's backend (second screen: read S2); default the stage key
    provider_c: str | None = None       # protocol: the judge's backend (second screen: read S3)


def screen_research(p, body) -> None:
    """Consent for every distinct backend the run's passes use (design D10); 403 before anything starts."""
    from ..core.backend_consent import require_research
    from ..screen.protocol import pass_backend_names

    for name in pass_backend_names(p, body.mode, body.provider, getattr(body, "provider_b", None),
                                   getattr(body, "provider_c", None)):
        require_research(p, name, "screen")


def screen_kwargs(body: ScreenRun, via: str) -> dict:
    """Keyword arguments of the engine call a ScreenRun body asks for (shared by the job and the synchronous route)."""
    common = dict(provider_name=body.provider, prompt_version=body.prompt_version, limit=body.limit,
                  backend_change=body.backend_change, confirmed_via=via, reuse_evaluations=body.reuse_evaluations)
    passes = {"provider_b": body.provider_b, "provider_c": body.provider_c}
    if body.mode == "protocol":
        return {**common, **passes, "new_set": body.new_set, "set_label": body.set_label,
                "reuse_from": body.reuse_from, "no_reuse": body.no_reuse}
    if body.mode == "second":
        return {**common, **passes, "new_set": body.new_set, "set_label": body.set_label, "from_set": body.from_set}
    return {**common, "pass_name": body.pass_name}


def _new_set_estimate(p, body: ScreenRun, plan: dict) -> dict:
    """The cost range of the new-set dialog: screening every record in scope, and screening only the records a reuse
    leaves (``limit`` = that count; an approximation, as the reused records are not the first ones)."""
    from ..core.estimate import estimate_screen

    def est(limit):
        try:
            r = estimate_screen(p, body.mode, scope=body.scope, protocol=body.protocol, provider_name=body.provider,
                                prompt_version=body.prompt_version, limit=limit, provider_b=body.provider_b,
                                provider_c=body.provider_c)
            return {k: r[k] for k in ("min_usd", "max_usd", "priced", "calls")}
        except (ValueError, KeyError, TypeError):
            return None
    out = {"all": est(body.limit)}
    if plan.get("reusable") and plan.get("to_screen") is not None:
        out["reuse"] = est(plan["to_screen"]) if plan["to_screen"] > 0 else {"min_usd": 0, "max_usd": 0, "priced": True,
                                                                            "calls": 0}
    return out


def screen_preflight(p, body: ScreenRun):
    """None when the run may start; else the response: 409 (backend_change, new_set_needed, scope_needed), 200
    already_done, or an HTTPException 400/404."""
    from fastapi.responses import JSONResponse

    from ..screen.devgate import DevSampleIncomplete, check_run
    from ..screen.preflight import preflight_screen, refusal_body

    try:   # ui-redesign D4: before consent, a job, a run folder or a call
        check_run(p, body.mode, body.scope, body.protocol)
    except DevSampleIncomplete as e:
        return JSONResponse(refusal_body(e), status_code=409)
    screen_research(p, body)   # 403 first, as in the engine (BackendConsentRequired), for every pass backend
    try:
        r = preflight_screen(p, body.mode, scope=body.scope, protocol=body.protocol, pass_name=body.pass_name,
                             provider_name=body.provider, prompt_version=body.prompt_version, limit=body.limit,
                             backend_change=body.backend_change, new_set=body.new_set, reuse_from=body.reuse_from,
                             no_reuse=body.no_reuse, from_set=body.from_set, provider_b=body.provider_b,
                             provider_c=body.provider_c)
    except (ValueError, KeyError) as e:
        rb = refusal_body(e)
        if rb is not None:
            if "new_set_needed" in rb:
                rb["new_set_needed"]["estimate"] = _new_set_estimate(p, body, rb["new_set_needed"])
            return JSONResponse(rb, status_code=409)
        raise HTTPException(404 if isinstance(e, KeyError) else 400, str(e).strip("'\"")[:400]) from e
    if r["status"] == "already_done":
        d = r["already_done"]
        return {"already_done": d, "message": f"already done under model {d.get('model')}: run {d['run_id']} is "
                                              "finished; add another backend name for a separate run"}
    return None


class SetPatch(BaseModel):
    label: str | None = None
    scope: str | None = None


class SetupPatch(BaseModel):
    title: str | None = None
    questions: list[str] | None = None
    include_notes: str | None = None
    exclude_notes: str | None = None
    platforms: list[str] | None = None
    default_provider: str | None = None
    default_embedding: str | None = None
    prior_reviews: dict | None = None  # policy switches, see PriorReviewPolicy


def create_app(project: Project, recent_file: Path | None = None) -> FastAPI:
    """``recent_file``: where the home page's recent-projects list is kept; None keeps it in memory (tests)."""
    from ..core.recent import RecentProjects

    app = FastAPI(title="WIDER", docs_url=None, redoc_url=None)
    app.state.project = project

    @app.exception_handler(OverflowError)
    async def id_too_large(request, exc: OverflowError):
        """An id in the address too large for the store (e.g. 99999999999999999999999) names nothing: 404, not 500."""
        from fastapi.responses import JSONResponse

        if "SQLite INTEGER" in str(exc):
            return JSONResponse(status_code=404, content={"detail": "no such id: the number is too large to be one"})
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    from ..core.project import FileChangedSince, ProjectBroken

    @app.exception_handler(FileChangedSince)
    async def changed_since(request, exc: FileChangedSince):
        """Another window or command saved project.yaml or benchmark.csv in between: 409, nothing overwritten."""
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ProjectBroken)
    async def project_broken(request, exc: ProjectBroken):
        """A damaged project.yaml or store: the sentence naming the file, not a bare 500."""
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=500, content={"detail": str(exc)})

    from ..screen.devgate import DevSampleIncomplete

    @app.exception_handler(DevSampleIncomplete)
    async def dev_incomplete(request, exc: DevSampleIncomplete):
        """ui-redesign D4: a run, evaluation, comparison or export refused while the development sample is not
        complete: 409 with the sentence and the counts."""
        from fastapi.responses import JSONResponse

        from ..screen.preflight import refusal_body

        return JSONResponse(status_code=409, content=refusal_body(exc))

    @app.middleware("http")
    async def author_from_page(request, call_next):
        """The page sends the reviewer name it remembers; journaled changes are attributed to it."""
        from ..core.journal import AUTHOR, INFERRED

        # the page percent-encodes the name (a header holds Latin-1 only, so "Łucja" or a Persian name would not fit)
        raw = request.headers.get("x-wider-author") or ""
        try:
            name = unquote(raw, errors="strict")
        except UnicodeDecodeError:  # not what the page sends: no name rather than a replacement character
            name = ""
        name = "".join(ch for ch in name if ch.isprintable())
        if request.method != "GET":   # every change from the page, settings saves included (researcher-attribution)
            from ..core.journal import check_name
            try:
                check_name(name.strip()[:80])
            except ValueError as e:
                from fastapi.responses import JSONResponse
                return JSONResponse(status_code=400, content={"detail": str(e)})
        token = AUTHOR.set(name.strip()[:80])  # "" = no name, not $USER
        inferred = INFERRED.set({})   # names read from an imported file in this request (researcher-attribution D2)
        try:
            return await call_next(request)
        finally:
            INFERRED.reset(inferred)
            AUTHOR.reset(token)
    recent = RecentProjects(recent_file)
    recent.add(project.root)

    def proj() -> Project:
        return Project.load(app.state.project.root)  # re-read so CLI edits show up

    @app.get("/api/project")
    def get_project():
        p = proj()
        cfg = p.config.model_dump(mode="json")
        for pr in cfg["providers"]:
            pr["key_set"] = bool(pr["api_key_env"] and os.environ.get(pr["api_key_env"]))
        for e in cfg["embeddings"]:
            e["key_set"] = bool(e["api_key_env"] and os.environ.get(e["api_key_env"]))
        bm = p.read_benchmark()
        counts = {k: v for k, v in p.store.counts().items() if v}
        from ..prior.reviews import summary as prior_summary
        prior = prior_summary(p)
        from ..search.coverage import (
            development_papers,
            query_results_papers,
            used_papers,
            validation_papers,
            validation_unlocked,
        )
        unlocked = validation_unlocked(p)   # the Ranking page's "Include the validation set" checkbox
        # D3: papers from the query's results in neither set; validation-reuse D1: used validation papers apart
        sets = {"development": len(development_papers(p, bm)), "validation": len(validation_papers(p, bm)),
                "used": len(used_papers(p, bm)), "not_counted": len(query_results_papers(p, bm))}
        p.store.close()
        return {"root": str(p.root), "config": cfg, "validation_unlocked": unlocked,
                "env_reviewer": os.environ.get("WIDER_REVIEWER", "").strip(),
                "benchmark": {"n": len(bm), **sets},
                "prior_reviews": prior, "store": counts}

    @app.patch("/api/project")
    def patch_project(patch: SetupPatch):
        from ..core.models import Platform
        from ..search.catalogue import platform_labels

        if patch.questions is not None:
            patch.questions = [x.strip() for x in patch.questions if x.strip()]
            if not patch.questions:   # the question goes into every prompt and the report: never saved away silently
                raise HTTPException(400, "a project needs at least one research question; nothing was saved")
        p = proj()
        c = p.config
        for f in ("title", "questions", "include_notes", "exclude_notes"):
            v = getattr(patch, f)
            if v is not None:
                setattr(c, f, v)
        if patch.platforms is not None:
            old = {pl.key: pl for pl in c.platforms}  # keep label/syntax of entries that stay (custom platforms)
            known = platform_labels()
            c.platforms = [Platform(key=k, label=(old[k].label if k in old else known.get(k, k)),
                                    syntax=old[k].syntax if k in old else None,
                                    role="development" if i == 0 else "validation")
                           for i, k in enumerate(k.strip().lower() for k in patch.platforms if k.strip())]
        if patch.default_provider is not None:
            if patch.default_provider not in {x.name for x in c.providers}:
                raise HTTPException(400, "unknown provider")
            c.default_provider = patch.default_provider
        if patch.default_embedding is not None:
            if patch.default_embedding not in {x.name for x in c.embeddings}:
                raise HTTPException(400, "unknown embedding")
            c.default_embedding = patch.default_embedding
        if patch.prior_reviews is not None:
            from ..prior.reviews import set_policy
            try:
                set_policy(p, **patch.prior_reviews)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, str(e)) from e
        p.save()
        return {"ok": True, "change_id": last_change_id()}

    @app.get("/api/stages")
    def get_stages():
        """Every stage with its resolved state (done / ready / blocked / extension), section, unmet requirements and
        the next ready stage, plus the sections, the project title and ``project_key`` (the first 16 hex characters
        of the sha256 of the resolved project folder: it names the folder in the browser's storage without the
        path) and ``construct_backend`` (the model for query creation, or null: the project then opens on the model
        page) with ``construct_default`` (the project default, used for query creation until a model is chosen, or
        null). The sidebar, stepper, page headings and the last page read this."""
        import hashlib

        from ..core.stages import load_sections, next_stage, route_sentence, stage_states

        p = proj()
        try:
            from ..core.stage_result import all_last_results

            states = stage_states(p)
            nxt = next_stage(p, states)
            last = all_last_results(p)
            # a stage not done yet that already holds something (a run result or an artifact of the kind it
            # produces, any status) reads "in progress", never "not started" (UI only; the CLI keeps its codes)
            kinds = {r["kind"] for r in p.store.query("SELECT DISTINCT kind FROM artifacts")} \
                if "artifacts" in p.store.tables() else set()
            for s in states:
                arts = {c.get("name") for c in (s.get("done_when") or []) if c.get("check") == "artifact"}
                s["started"] = s["state"] in ("ready", "blocked") and (s["key"] in last or bool(arts & kinds))
            key = hashlib.sha256(str(Path(p.root).resolve()).encode("utf-8")).hexdigest()[:16]
            return {"stages": states, "next": nxt["key"] if nxt else None, "last_results": last,
                    "sections": load_sections(), "title": p.config.title or p.config.name, "project_key": key,
                    "construct_backend": p.config.stage_providers.get("construct"),
                    # the project default, which query creation uses from the command line until a model is chosen
                    "construct_default": (p.config.default_provider
                                          if any(x.name == p.config.default_provider for x in p.config.providers)
                                          else None),
                    "route": route_sentence(p, states)}   # the Overview's route sentence (ui-redesign D8)
        finally:
            p.store.close()

    @app.get("/api/stages/{stage}/results")
    def get_stage_results(stage: str, limit: int = 30):
        """Earlier results of one stage, newest first (the run selector)."""
        from ..core.stage_result import history

        p = proj()
        try:
            return history(p, stage, limit)
        finally:
            p.close()

    @app.post("/api/providers/{name}/probe")
    async def probe_provider(name: str):
        from ..core.backend_consent import require_backend_consent
        from ..core.backends import record_probe
        from ..providers.registry import probe

        p = proj()
        try:
            cfg = p.provider(name)
            info = require_backend_consent(p, cfg, "probe")   # before any request: 403 when consent is missing
        except KeyError:
            raise HTTPException(404, "unknown provider")
        finally:
            p.close()
        try:
            r = await probe(cfg, locality=(info["locality"], info["source"]))   # a failed probe is a 200 with ok false
        except Exception as e:   # anything the probe did not turn into a failed probe: 502 with its message
            r = None
            err = f"{type(e).__name__}: {e}"[:400]
        p = proj()
        try:
            record_probe(p, "llm", cfg, bool(r and r.get("ok")), info["locality"], r.get("error") if r else err)
        finally:
            p.close()
        if r is None:
            raise HTTPException(502, err)
        return r

    @app.post("/api/embeddings/{name}/probe")
    def probe_embedding(name: str, allow_download: bool = False):
        from ..core.backends import backend_place as place_of
        from ..core.backends import is_downloaded, record_probe
        from ..embeddings.registry import probe

        p = proj()
        try:
            try:
                cfg = p.embedding(name)
            except KeyError:
                raise HTTPException(404, "unknown embedding")
            if cfg.kind == "sentence_transformers" and not allow_download and is_downloaded(cfg) is False:
                return {"name": name, "ok": False, "skipped": True,
                        "error": "model weights are not downloaded; allow the download to probe this model"}
            r = probe(cfg, p.path("embeddings", "cache.sqlite"), project=p)   # BackendConsentRequired -> 403
            record_probe(p, "embedding", cfg, bool(r.get("ok")), place_of(p, cfg)["locality"], r.get("error"))
            return r
        finally:
            p.close()

    # ----- benchmark -----
    @app.get("/api/benchmark")
    def get_benchmark():
        from ..construct.benchmark import sync

        p = proj()
        try:
            return sync(p)
        finally:
            p.store.close()

    @app.post("/api/benchmark/fetch")
    def post_benchmark_fetch(all: bool = False):
        from ..construct.benchmark import fetch

        p = proj()
        try:
            rows = fetch(p, only_missing=not all)
            return {"rows": rows, "with_abstract": sum(1 for r in rows if (r.get("abstract_words") or 0) >= p.config.protocol.corpus.thin_words)}
        finally:
            p.store.close()

    # ----- construct artifacts -----
    @app.get("/api/construct")
    def get_construct():
        from ..construct import artifacts

        p = proj()
        try:
            return {k: artifacts.list_versions(p, k) for k in artifacts.KINDS}
        finally:
            p.store.close()

    @app.get("/api/construct/{kind}/{version:int}")  # int converter so /{kind}/prompt reaches its own route
    def get_artifact(kind: str, version: int):
        from ..construct import artifacts

        p = proj()
        try:
            row, data = artifacts.load(p, kind, version)
            if row is None:
                raise HTTPException(404, "no such version")
            return {"row": row, "text": artifacts.dump_yaml(data), "data": data}
        finally:
            p.store.close()

    @app.post("/api/construct/{kind}/save")
    def post_artifact_save(kind: str, body: ArtifactText):
        from ..construct import artifacts

        p = proj()
        try:
            return artifacts.save_edit(p, kind, body.text, note=body.note or "edited in the UI", confirm_revision=True,
                                       seen_choice=body.seen_choice)
        except artifacts.SeenChoiceNeeded as e:   # nothing saved: the page asks the researcher (unlock-seen-choice D6)
            raise HTTPException(400, e.notice)
        except (ValueError, yaml.YAMLError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.store.close()

    @app.post("/api/construct/{kind}/{version}/status/{status}")
    def post_artifact_status(kind: str, version: int, status: str, seen_choice: str | None = None):
        from ..construct import artifacts

        p = proj()
        try:
            row = artifacts.set_status(p, kind, version, status, seen_choice)
            return {**with_result(p, "query" if kind == "synonyms" and status == "frozen" else "construct", row),
                    "change_id": last_change_id()}
        except artifacts.SeenChoiceNeeded as e:   # nothing frozen: the page asks the researcher (D2, Q3)
            raise HTTPException(400, e.notice)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.store.close()

    @app.get("/api/construct/{kind}/diff/{a}/{b}")
    def get_artifact_diff(kind: str, a: int, b: int):
        from ..construct import artifacts

        p = proj()
        try:
            return {"diff": artifacts.diff(p, kind, a, b)}
        except KeyError as e:
            raise HTTPException(404, str(e))
        finally:
            p.store.close()

    @app.get("/api/construct/{kind}/prompt")
    def get_artifact_prompt(kind: str):
        from ..construct.propose import ProposalError, build_prompt

        p = proj()
        try:
            prompt, info = build_prompt(p, kind)
            return {"prompt": prompt, "info": info}
        except ProposalError as e:
            raise HTTPException(400, str(e))
        finally:
            p.store.close()

    @app.get("/api/construct/{kind}/outline")
    def get_artifact_outline(kind: str):
        """A starting text to write the artifact by hand (`wider construct outline`)."""
        from ..construct import artifacts

        try:
            return {"text": artifacts.outline(kind)}
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'"))

    @app.post("/api/construct/{kind}/propose")
    async def post_artifact_propose(kind: str, provider: str | None = None, seen_choice: str | None = None):
        from ..construct.artifacts import SeenChoiceNeeded, save_seen_choice
        from ..construct.propose import ProposalError, propose_async

        p = proj()
        try:
            with save_seen_choice(seen_choice):
                return with_result(p, "construct", await propose_async(p, kind, provider_name=provider,
                                                                        confirm_revision=True))
        except SeenChoiceNeeded as e:   # no model call, nothing saved (unlock-seen-choice D6)
            raise HTTPException(400, e.notice)
        except (ProposalError, KeyError, ValueError) as e:
            raise HTTPException(400, str(e)[:600])
        finally:
            p.store.close()

    # ----- query -----
    @app.get("/api/query/platforms")
    def get_platforms():
        """Catalogue entries plus the project's custom platforms (described by their syntax family)."""
        from ..search.render import platforms

        out = {k: dict(v) for k, v in platforms().items()}
        p = proj()
        for pl in p.config.platforms:
            if pl.key not in out:
                fam = out.get(pl.syntax or "", {})
                out[pl.key] = {"label": pl.label or pl.key, "style": pl.syntax, "custom": True,
                               "paste": f"Custom platform rendered with the {fam.get('label', pl.syntax)} syntax. "
                                        + (fam.get("paste") or "")}
        return out

    @app.get("/api/query/render")
    def get_query_render(version: int | None = None, compact: bool = False, platform: str | None = None):
        from ..construct import artifacts
        from ..search.catalogue import project_platforms, render_project
        from ..search.ir import Query, count_terms
        from ..search.lint import lint, lint_style
        from ..search.render import block_list, notes_for, render_all
        from ..search.render.reduced import gscholar_notes
        from ..search.term_yield import block_terms
        from ..search.translation import ledger

        p = proj()
        try:
            from ..search.rounds import default_version
            row, data = artifacts.load(p, "synonyms", version if version is not None else default_version(p))
            if row is None:
                if version is not None:
                    raise HTTPException(404, f"no synonyms version {version}")
                return {"row": None, "message": "no synonyms artifact yet"}   # an empty project is no failed request
            q = Query.model_validate(data)
            if p.config.platforms:  # only the chosen platforms, custom ones through their syntax family
                try:
                    strings = render_project(p, q, pretty=not compact)
                except ValueError as e:
                    raise HTTPException(400, str(e))
            else:
                strings = render_all(q, ["ebsco", "scopus", "wos", "proquest", "ovid"], pretty=not compact)
            chosen = project_platforms(p)
            styles = {c["key"]: c["style"] for c in chosen} if p.config.platforms else {}
            notes = {k: gscholar_notes(q)["text"] for k in strings if styles.get(k) == "gscholar"}
            # field-code notes per string (2a, task 2.3); a project without a list renders the catalogue styles
            field_notes = {k: notes_for(q, styles.get(k, k)) for k in strings}
            ledgers = {k: ledger(q, styles.get(k, k)) for k in strings}   # D13: under each string
            try:   # the linter with the chosen platform's limits (D0b); none: the strictest
                checked = lint(q, lint_style(p, platform or None))
            except ValueError as e:
                raise HTTPException(400, str(e))
            return {"row": row, "counts": count_terms(q), "lint": checked, "strings": strings,
                    "platforms": chosen, "notes": notes, "field_notes": field_notes, "ledgers": ledgers,
                    "blocks": block_list(q), "yield_terms": block_terms(q),   # D16: the Yield column
                    "terms_allowed": p.config.online_lookups.terms_allowed}
        finally:
            p.close()

    def checked_import(p, body: ImportText, notes: str):
        """The string check and the parse, with the CLI's words on an error so the page and the command line read
        alike."""
        from ..search.lint import lint_style
        from ..search.string_check import checked_parse

        try:
            return checked_parse(body.text, lint_style(p, body.platform or "ebsco"), notes=notes)
        except ValueError as e:
            raise ValueError(f"could not parse: {e}") from e

    @app.get("/api/query/review")
    def get_query_review(readers: list[str] | None = Query(None)):
        """The second reader's readers before a run (name, model, family, where each runs, a refusal), and the last
        review. Nothing is sent."""
        from ..search import review

        p = proj()
        try:
            return {**review.reader_rows(p, readers or None), "last": review.last_review(p)}
        finally:
            p.close()

    @app.post("/api/query/review")
    def post_query_review(body: ReviewRun):
        """Twin of ``wider query review``: a refused reader is 409, a missing consent 403, both before anything is
        sent."""
        from ..providers.base import ProviderError
        from ..search import review

        p = proj()
        try:
            return review.second_read(p, body.version, body.platform or None, body.readers or None)
        except review.ReaderRefused as e:
            raise HTTPException(409, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        except ProviderError as e:
            raise HTTPException(502, str(e)[:400]) from e
        finally:
            p.close()

    @app.post("/api/query/parse")
    def post_query_parse(body: ImportText):
        """Preview: the findings and the blocks the string is read as; nothing is saved."""
        from ..search.render import block_list
        from ..search.string_check import save_refusal

        p = proj()
        try:
            q, findings = checked_import(p, body, "")
            return {"findings": findings, "blocks": block_list(q), "logic": q.model_dump(mode="json")["logic"],
                    "save_refusal": save_refusal(q)}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/import")
    def post_query_import(body: ImportText):
        from ..construct import artifacts

        p = proj()
        try:
            q, findings = checked_import(p, body, body.note or "imported in the UI")
            try:
                row = artifacts.save_new_version(p, "synonyms", q.model_dump(mode="json", exclude_defaults=True),
                                                 origin="import", note=body.note or "imported in the UI",
                                                 confirm_revision=True,   # the page confirmed the save notice first
                                                 seen_choice=body.seen_choice)
            except artifacts.SeenChoiceNeeded:
                raise
            except ValueError as e:
                raise ValueError(f"could not save: {e}") from e
            return {**with_result(p, "query", row), "findings": findings}
        except artifacts.SeenChoiceNeeded as e:   # nothing saved: the page asks the researcher (unlock-seen-choice D6)
            raise HTTPException(400, e.notice)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    # ----- import / coverage / dedup -----
    @app.get("/api/import")
    def get_import():
        from ..core.provenance import sha256_file
        from ..ingest.importer import final_at, ris_files, summary

        p = proj()
        try:  # imported means these bytes were imported (as the importer decides), whatever the file is called now
            imported = {r["sha256"]: r for r in p.store.query(
                "SELECT sha256, source_id, included, n_records, sample_of FROM sources")}

            def row(pl, f):
                hit = imported.get(sha256_file(f))
                out = {"platform": pl, "file": f.name, "imported": hit is not None}
                if hit is not None:   # the "Enters deduplication" switch addresses the imported file by its id (D5)
                    out.update(source_id=hit["source_id"], included=bool(hit["included"]), records=hit["n_records"],
                               sample_of=hit["sample_of"])
                return out
            return {"summary": summary(p), "root": str(p.path("inputs")), "final_at": final_at(p),
                    "files": [row(pl, f) for pl, f in ris_files(p)]}
        finally:
            p.close()

    @app.post("/api/import/run")
    def post_import_run(seen_choice: str | None = None, round: int | None = None):
        from ..construct.artifacts import SeenChoiceNeeded
        from ..ingest.importer import import_all

        p = proj()
        try:
            notices: list[str] = []
            files = import_all(p, seen_choice=seen_choice, notices=notices, round_no=round)
            return {"files": files, "notice": " ".join(notices) or None,
                    "recorded": next((f["recorded"] for f in files if f.get("recorded")), None),
                    "stage_result": last_result(p, "import")}
        except SeenChoiceNeeded as e:   # nothing imported: the researcher chooses first (D21, Q2)
            raise HTTPException(400, e.notice) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/coverage")
    def get_coverage(scope: str = "platforms", validation: bool = False, seen_choice: str | None = None):
        from ..construct.artifacts import SeenChoiceNeeded
        from ..construct.artifacts import seen_choice as check_choice
        from ..search.coverage import check

        try:
            check_choice(seen_choice)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        p = proj()
        try:
            return check(p, scope=scope, include_validation=True if validation else None, seen_choice=seen_choice or None)
        except SeenChoiceNeeded as e:   # nothing shown or recorded: the card asks the researcher first (D3, Q1)
            return {"choice_needed": True, "notice": e.notice, "validation_included": False, "scopes": {}}
        finally:
            p.close()

    @app.get("/api/coverage/independent")
    def get_coverage_independent(protocol: str | None = None, result_set: int | None = None):
        """Recall on independent papers and Chapman's estimate (D25); never a look at the hidden validation set."""
        from ..recall.independent import check as independent_check

        p = proj()
        try:
            return independent_check(p, protocol or None, result_set)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e).strip("'\"")) from e
        finally:
            p.close()

    @app.get("/api/dedup")
    def get_dedup():
        from ..dedup.engine import decided_since_run, flow_counts, prisma_rows, queue_all, removed_line

        p = proj()
        try:
            summary = p.store.unj(p.store.get_meta("dedup_summary")) or {}
            flow = flow_counts(p, summary)
            flow["removed_line"] = removed_line(flow)
            q = queue_all(p)   # the last run's queue, open and decided, with the status of each pair now
            # the record numbers a refusal names ("records 12 and 40 now merge into one"): each pair side's unique
            # record, so the pair view shows the same ids (inspection 1 Nov, item 2)
            rec = {r["raw_id"]: r["record_id"] for r in p.store.query(
                "SELECT c.raw_id, m.record_id FROM dedup_clusters c JOIN records m ON m.cluster_id = c.cluster_id")}
            for r in q[:200]:
                r["record_a"], r["record_b"] = rec.get(r.get("raw_a")), rec.get(r.get("raw_b"))
            return {"summary": summary, "run_at": p.store.get_meta("dedup_run_at"), "prisma": prisma_rows(p) if summary else [],
                    "queue": q[:200], "pending": flow["to_decide"], "decided_since_run": decided_since_run(p), "flow": flow}
        finally:
            p.close()

    @app.get("/api/dedup/merged")
    def get_dedup_merged(limit: int = 200):
        from ..dedup.engine import merged_pairs

        p = proj()
        try:
            return merged_pairs(p, limit=max(1, min(limit, 5000)))
        finally:
            p.close()

    @app.get("/api/dedup/records")
    def get_dedup_records(limit: int = 200):
        from ..dedup.engine import unique_records

        p = proj()
        try:
            total = p.store.one("SELECT COUNT(*) n FROM records")["n"]
            return {"rows": unique_records(p, limit=max(1, min(limit, 5000))), "total": total}
        finally:
            p.close()

    @app.post("/api/dedup/run")
    def post_dedup_run():
        from ..dedup.engine import DedupRerunError, run

        p = proj()
        try:
            return with_result(p, "dedup", run(p))
        except DedupRerunError as e:
            raise HTTPException(409, str(e)) from e
        finally:
            p.close()

    @app.post("/api/dedup/decide")
    def post_dedup_decide(body: DedupDecision):
        from ..dedup.engine import decide

        p = proj()
        try:
            decide(p, body.raw_a, body.raw_b, body.decision, by=body.by, keep=body.keep)
            return {"ok": True, "change_id": last_change_id()}
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    # ----- enrichment -----
    @app.get("/api/enrich")
    def get_enrich():
        from ..enrich.fetch import summary

        p = proj()
        try:
            return summary(p)
        finally:
            p.close()

    @app.get("/api/enrich/records")
    def get_enrich_records(group: str = "waiting", limit: int = 50):
        from ..enrich.fetch import records_by_state

        p = proj()
        try:
            return records_by_state(p, group, limit=limit)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.post("/api/enrich/run")
    def post_enrich_run(limit: int = 100, retry: bool = False):
        from ..enrich.fetch import enrich, summary

        if limit < 1:
            raise HTTPException(400, "the batch size (limit) must be at least 1")
        p = proj()
        try:
            stats = enrich(p, limit=limit, retry=retry)   # ConsentRequired -> 403 (routes_lookups)
            return {"stats": stats, "summary": summary(p), "stage_result": last_result(p, "enrich")}
        finally:
            p.close()

    # ----- ranking -----
    @app.get("/api/rank")
    def get_rank(ranking: str | None = None, offset: int = 0, limit: int = 50, cell: str | None = None):
        from ..rank.score import corpus_overview, explorer

        p = proj()
        try:
            d = explorer(p, ranking, offset=offset, limit=limit, cell=cell)
            if cell and d["ranking"] and cell not in d["best_cell_top3000"]:   # as `wider rank show --cell`
                raise HTTPException(404, f"no cell {cell}; cells: {', '.join(d['best_cell_top3000'])}")
            return {**d, "corpus": corpus_overview(p)}
        finally:
            p.close()

    @app.post("/api/rank/embed")
    def post_rank_embed(embedding: str | None = None, allow_download: bool = False):
        from ..core.backends import is_downloaded
        from ..rank.corpus import embed_corpus

        p = proj()
        try:
            cfg = p.embedding(embedding)
            if cfg.kind == "sentence_transformers" and not allow_download and is_downloaded(cfg) is False:
                return {"skipped": True, "error": f"{cfg.model} is not downloaded"}
            return with_result(p, "rank", embed_corpus(p, embedding))   # BackendConsentRequired -> 403
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/rank/run")
    def post_rank_run(embedding: str | None = None, anchors_version: int | None = None, top_n: str | None = None):
        from ..rank.score import rank

        if top_n is not None and not re.fullmatch(r"-?\d+", top_n.strip()):
            raise HTTPException(400, f"top-N must be a whole number from 1 to the number of cells (got {top_n[:20]!r})")
        from ..core.backend_consent import require_embedding_research

        p = proj()
        try:
            require_embedding_research(p, embedding)   # the anchors are embedded: 403 before any other check
            return with_result(p, "rank", rank(p, set_name=embedding, anchors_version=anchors_version,
                                               top_n=None if top_n is None else int(top_n)))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/send-notice/{action}")
    def get_send_notice(action: str, backend: str | None = None):
        """What a sending action sends, where, why and the consent state, beside its button (ui-redesign D17); the
        CLI prints the same sentence before the run. Makes no request."""
        from ..core.send_notice import ACTIONS, notice

        if action not in ACTIONS:
            raise HTTPException(404, f"no sending action {action!r}")
        p = proj()
        try:
            return notice(p, action, backend)
        finally:
            p.close()

    @app.post("/api/rank/keep")
    def post_rank_keep(body: RankKeep):
        """``wider rank keep [--version N]`` (ui-redesign D5): a signed, undoable History change; nothing reruns."""
        from ..core.journal import last_change_id
        from ..rank.versions import keep

        p = proj()
        try:
            return {**keep(p, body.version), "change_id": last_change_id()}
        except ValueError as e:
            raise HTTPException(409, str(e)[:400]) from e
        finally:
            p.close()

    @app.get("/api/rank/validate")
    def get_rank_validate(ranking: str | None = None, validation: bool = False, seen_choice: str | None = None):
        from ..construct.artifacts import SeenChoiceNeeded
        from ..construct.artifacts import seen_choice as check_choice
        from ..rank.score import validate

        try:
            check_choice(seen_choice)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        p = proj()
        try:
            return validate(p, ranking, include_validation=True if validation else None, seen_choice=seen_choice or None)
        except SeenChoiceNeeded as e:   # nothing shown or recorded: the page asks the researcher first (D3, Q1)
            return {"choice_needed": True, "notice": e.notice, "validation_included": False, "rows": [], "summary": {}}
        except KeyError as e:
            raise HTTPException(400, e.args[0]) from e
        finally:
            p.close()

    @app.get("/api/rank/ablate")
    def get_rank_ablate(validation: bool = False, seen_choice: str | None = None):
        from ..construct.artifacts import SeenChoiceNeeded
        from ..construct.artifacts import seen_choice as check_choice
        from ..core.backend_consent import ablate_embeddings, require_embedding_research
        from ..rank.ablate import ablation, choice_needed, column_label

        try:
            check_choice(seen_choice)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        p = proj()
        try:
            notice = None if seen_choice else choice_needed(p, validation)
            if notice:   # nothing ranked or recorded: the page asks the researcher first (D3, Q1)
                return {"choice_needed": True, "notice": notice, "validation_included": False, "rows": []}
            for name in ablate_embeddings(p):   # every set is checked before the first ranking
                require_embedding_research(p, name)
            res = ablation(p, include_validation=True if validation else None, seen_choice=seen_choice or None)
            # keyed by the readable headers of `wider rank ablate`, in the same order
            return {**res, "rows": [{column_label(k): v for k, v in r.items()} for r in res["rows"]]}
        except SeenChoiceNeeded as e:
            return {"choice_needed": True, "notice": e.notice, "validation_included": False, "rows": []}
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/rank/cluster")
    def get_rank_cluster(k: int | None = None, top: int | None = None):  # default: protocol.ranking.cluster_k/_top
        from ..rank.cluster import cluster

        p = proj()
        try:
            res = cluster(p, k=k, top=top)
            res.pop("labels", None)
            return res
        except KeyError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    # ----- samples and labelling -----
    @app.get("/api/samples")
    def get_samples():
        from ..sample.design import sampling_log
        from ..sample.labels import (
            criteria_counts,
            criteria_report,
            progress,
            reviewers,
            revisions,
            sample_by_name,
            status_keys,
            unlock_warning,
        )
        from ..screen.review import reviewed_line, reviewed_of_sample

        p = proj()
        try:
            logs = sampling_log(p)
            # the criteria counts (D11) only for probability samples, twin of `wider sample status --json`
            seen = {k: reviewed_of_sample(p, k) for k in logs}
            return {"samples": {k: {**v, "progress": progress(p, k),
                                    "reviewed_with_llm_reads": seen[k], "reviewed_line": reviewed_line(seen[k]),
                                    "unlock_warning": unlock_warning(p, sample_by_name(p, k)), **status_keys(p, k),
                                    **({"criteria": criteria_counts(p, k), "criteria_change": criteria_report(p, k),
                                       "revisions": revisions(p, k)} if v.get("kind") == "probability" else {})}
                                for k, v in logs.items()}, "reviewers": reviewers(p),
                    # the name the command line signs with; the Labelling page says when it differs (D10)
                    "env_reviewer": os.environ.get("WIDER_REVIEWER", "").strip()}
        finally:
            p.close()

    @app.post("/api/samples/draw")
    def post_samples_draw(which: str = "both", seed: int | None = None, name: str | None = None,
                          fresh_from: str | None = None, author: str | None = None):
        from ..core.backend_consent import ranking_embedding, require_embedding_research
        from ..sample.design import FRESH_ONLY, draw_development, draw_probability

        if (name or fresh_from) and which != "probability":
            raise HTTPException(400, FRESH_ONLY)
        p = proj()
        try:
            set_name = ranking_embedding(p, None) if which in ("development", "both") else None
            if set_name:   # checked before the probability sample is drawn, so a refusal changes nothing
                require_embedding_research(p, set_name)
            out = {}
            if which in ("probability", "both"):
                r = draw_probability(p, name=name, seed=seed, fresh_from=fresh_from, author=author)
                out[r["name"]] = r
            if which in ("development", "both"):
                out["development"] = draw_development(p, seed=seed)
            return {"samples": out, "stage_result": last_result(p, "sample")}
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/samples/{name}/lock")
    def post_sample_lock(name: str, unlock: bool = False, yes: bool = False):
        """Twin of `wider sample lock|unlock NAME [--yes]`, signed with the page's author header. An unlock whose
        recall result was seen needs ``yes`` (409 with the warning otherwise)."""
        from ..core.journal import AUTHOR
        from ..sample.labels import lock as do_lock
        from ..sample.labels import unlock as do_unlock

        if not (AUTHOR.get() or "").strip():   # risk-checker R2: History and the methods sentence need who locked
            raise HTTPException(400, "a reviewer name is needed: a lock or unlock is signed with your name")
        p = proj()
        try:
            if not unlock:
                do_lock(p, name)
                return {"ok": True}
            r = do_unlock(p, name, yes=yes)
            if r["warning"] and not r["unlocked"]:
                raise HTTPException(409, r["warning"])
            return {"ok": True, **r}
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        finally:
            p.close()

    @app.post("/api/samples/{name}/extend")
    def post_sample_extend(name: str, body: dict | None = Body(None), pool: str = "boundary"):
        """D9: add the boundary pool to an unlocked purposive sample (CLI twin: wider sample extend); the pool comes
        from the body's `pool` or the query, default boundary."""
        from fastapi.responses import JSONResponse

        from ..core.backend_consent import require_embedding_research
        from ..sample.design import ExtendNeedsConfirm, SampleLocked, extend_development

        p = proj()
        try:
            pool = str((body.get("pool") if isinstance(body, dict) else None) or pool)
            ok = isinstance(body, dict) and body.get("confirm") is True
            r = extend_development(p, name, pool, before_embedding=lambda s: require_embedding_research(p, s), confirm=ok)
            return {**r, "stage_result": last_result(p, "sample")}
        except ExtendNeedsConfirm as e:   # after a corpus round: the page asks, then posts again with confirm: true
            return JSONResponse({"detail": str(e), "confirm_needed": True, "n": e.n}, status_code=409)
        except SampleLocked as e:
            raise HTTPException(409, str(e)[:400])
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/labels/queue")
    def get_label_queue(reviewer: str, samples: str | None = None, unlabelled: bool = False, blind: bool = True,
                        mode: str | None = None, recheck: bool = False):
        from ..sample.labels import queue_left_out, recheck_queue, second_reviewer_queue

        # a GET writes nothing: the reviewer is registered by the first saved label (POST /api/labels, label())
        if mode and mode not in ("recheck", "second"):
            raise HTTPException(400, "mode must be recheck or second")
        p = proj()
        try:
            if mode in ("recheck", "second"):   # always blind: whitelist items, no sample or record (D12)
                from ..core.journal import AUTHOR

                # only for the name the page signs with: another reviewer's re-check queue would show their labels
                if reviewer.strip() != AUTHOR.get():
                    raise HTTPException(400, "this queue is for the name you sign with")
                names = samples.split(",") if samples else [None]
                if len(names) > 1:
                    raise HTTPException(400, "give one sample or none")
                try:
                    if mode == "recheck":
                        return recheck_queue(p, reviewer, names[0])
                    return second_reviewer_queue(p, reviewer, names[0], recheck=recheck)
                except ValueError as e:
                    raise HTTPException(400, str(e))
                except KeyError as e:
                    raise HTTPException(404, str(e).strip('"'))
            rows, left_out = queue_left_out(p, reviewer, samples.split(",") if samples else None,
                                            only_unlabelled=unlabelled, blind=blind)
            if not left_out:
                return rows
            # the list body stays (the drawer and the tests read it); the count of unlabelled records left out of a
            # mixed blind queue goes in a header, naming no sample (label-completion-and-sealing D8)
            from fastapi.encoders import jsonable_encoder
            from fastapi.responses import JSONResponse
            return JSONResponse(jsonable_encoder(rows), headers={"X-Left-Out": str(left_out)})
        finally:
            p.close()

    @app.post("/api/labels")
    def post_label(body: LabelBody):
        from ..sample.labels import CorrectionNeeded, label, label_item

        by_item = body.item is not None and body.sample_id is None and body.record_id is None
        if not by_item and (body.item is not None or body.sample_id is None or body.record_id is None):
            raise HTTPException(400, "give either a queue item or a sample and record")
        if body.recheck and body.item is None:
            raise HTTPException(400, "recheck is only for a re-check queue item")
        p = proj()
        try:
            if body.item is not None:
                label_item(p, body.item, body.reviewer, body.decision, body.reason, body.labels, body.note,
                           recheck=body.recheck, correction_reason=body.correction_reason)
                return {"ok": True, "change_id": last_change_id()}
            s = p.store.one("SELECT name FROM samples WHERE sample_id=?", (body.sample_id,))
            if s is None:
                raise HTTPException(404, "unknown sample")
            # a post with a sample and record comes from the unblinded queue, which shows the band and rank (D11)
            label(p, s["name"], body.record_id, body.reviewer, body.decision, body.reason, body.labels, body.note,
                  made_in="unblinded queue", correction_reason=body.correction_reason)
            return {"ok": True, "change_id": last_change_id()}
        except CorrectionNeeded as e:
            return _correction_needed(e)
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.get("/api/labels/agreement")
    def get_agreement(sample: str, a: str, b: str):
        from ..sample.labels import agreement

        p = proj()
        try:
            return agreement(p, sample, a, b)
        finally:
            p.close()

    @app.get("/api/labels/final")
    def get_final(sample: str, primary: str | None = None):
        from ..sample.labels import final_labels

        p = proj()
        try:
            s = p.store.one("SELECT locked FROM samples WHERE name=?", (sample,))
            if s is None:
                raise HTTPException(404, "unknown sample")
            rows = final_labels(p, sample, primary)
            if not s["locked"]:
                for r in rows:  # key stays hidden until the sample is locked
                    for k in ("band", "weight", "pool", "rank"):
                        r[k] = None
            return {"locked": bool(s["locked"]), "rows": rows}
        finally:
            p.close()

    @app.post("/api/labels/adjudicate")
    def post_adjudicate(body: AdjBody):
        from ..sample.labels import CorrectionNeeded, adjudicate

        p = proj()
        try:
            adjudicate(p, body.sample, body.record_id, body.decision, body.by, body.reason, note=body.note,
                       correction_reason=body.correction_reason)
            return {"ok": True, "change_id": last_change_id()}
        except CorrectionNeeded as e:
            return _correction_needed(e)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.post("/api/labels/independence")
    def post_label_independence(body: IndependenceBody):
        """The twin of `wider label provenance` (label-completion-and-sealing D9, D11)."""
        from ..sample.labels import declare_independent

        p = proj()
        try:
            return declare_independent(p, body.sample, body.reviewer, body.basis, by=body.by)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e))
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.get("/api/codebook")
    def get_codebook():
        from ..construct import artifacts

        p = proj()
        try:
            row, data = artifacts.load(p, "codebook")
            return {"row": row, "codebook": data}
        finally:
            p.close()

    # ----- screening -----
    @app.get("/api/screen")
    def get_screen():
        from ..construct import artifacts
        from ..screen.decisions import protocols
        from ..screen.engine import runs

        p = proj()
        try:
            calls = {r["run_id"]: dict(r) for r in p.store.query(
                "SELECT run_id, SUM(ok) ok, COUNT(*) n, SUM(rule_decision='include') inc, SUM(rule_decision='maybe') may, "
                "SUM(rule_decision='exclude') exc FROM screen_calls GROUP BY run_id")}
            from ..core.backends import provider_rows
            from ..core.usage import per_run
            usage = per_run(p)
            kinds = {r["name"]: r["locality"] for r in provider_rows(p)}   # no request, not even to 127.0.0.1
            from ..screen.engine import EXPERIMENTAL_NOTE, run_experimental, run_mixed
            from ..screen.progress import screen_progress, unusual
            prog = screen_progress(p)   # where screening stands; the rounds' readable names (screening-page-tasks)
            named = {r["run_id"]: r for r in prog.pop("rounds")}
            from ..screen.devgate import HIDDEN_SOME, hidden_runs

            for rid in hidden_runs(p):          # ui-redesign D4: no decision counts on development records
                if rid in calls:
                    calls[rid].update(inc=None, may=None, exc=None, hidden=HIDDEN_SOME)
            keep = ("name", "kind", "kind_label", "test", "unusual", "phrase", "protocol", "set", "pass")
            return {"runs": [{**r, **calls.get(r["run_id"], {}), "usage": usage.get(r["run_id"]),
                              "locality": kinds.get(r["provider"]), "mixed": run_mixed(p, r["run_id"]),
                              "experimental": EXPERIMENTAL_NOTE if run_experimental(p, r["run_id"]) else None,
                              **{f"round_{k}" if k in ("protocol", "set", "pass") else k: named[r["run_id"]][k]
                                 for k in keep if r["run_id"] in named}}
                             for r in runs(p)], "progress": prog,
                    # a protocol set that excluded or included everything, or is mostly maybe, says so too
                    "protocols": [{**d, "unusual": unusual(d["n"] or 0, d["include"] or 0, d["maybe"] or 0,
                                                           d["exclude"] or 0)} for d in protocols(p)],
                    "prompts": artifacts.list_versions(p, "prompts"),
                    "samples": [dict(r) for r in p.store.query("SELECT name, kind, n FROM samples")]}
        finally:
            p.close()

    @app.post("/api/screen/build-prompt")
    def post_screen_build_prompt():
        from ..screen.codebook import build, save_prompt_artifact

        p = proj()
        try:
            b = build(p)
            return save_prompt_artifact(p, b)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/screen/run")
    async def post_screen_run(body: ScreenRun):
        from ..screen.engine import screen_async, whole_limit
        from ..screen.protocol import run_protocol_async, second_screen_async

        p = proj()
        try:
            try:
                body.limit = whole_limit(body.limit)   # the same refusals as the job route (a fraction, a word, true)
            except ValueError as e:
                raise HTTPException(400, str(e)[:400]) from e
            refused = screen_preflight(p, body)
            if refused is not None:
                return refused
            kw = screen_kwargs(body, "ui")
            if body.mode == "protocol":
                res = await run_protocol_async(p, body.scope, body.protocol, **kw)
            elif body.mode == "second":
                res = await second_screen_async(p, body.protocol, **kw)
            else:
                res = await screen_async(p, body.scope, **kw)
            return with_result(p, "screen", res)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/screen/progress/{run_id}")
    def get_screen_progress(run_id: str):
        p = proj()
        try:
            run = p.store.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
            if run is None:
                raise HTTPException(404, "no such run")
            from ..screen.devgate import LEFT_OUT, hidden_ids

            hid = sorted(hidden_ids(p))     # the hidden records' decisions are left out of the counts (D4)
            out = "" if not hid else f" AND record_id NOT IN ({','.join('?' * len(hid))})"
            c = p.store.one("SELECT COUNT(*) n, SUM(ok) ok FROM screen_calls WHERE run_id=?", (run_id,))
            d = p.store.one("SELECT SUM(rule_decision='include') inc, SUM(rule_decision='maybe') may, "
                            f"SUM(rule_decision='exclude') exc FROM screen_calls WHERE run_id=?{out}", (run_id, *hid))
            left = bool(hid) and p.store.scalar(f"SELECT 1 FROM screen_calls WHERE run_id=? AND record_id IN "
                                                f"({','.join('?' * len(hid))}) LIMIT 1", (run_id, *hid))
            return {**dict(run), **dict(c), **dict(d), "hidden": LEFT_OUT if left else None}
        finally:
            p.close()

    @app.get("/api/screen/evaluate/{run_id}")
    def get_screen_evaluate(run_id: str, sample: str = "development"):
        from ..screen.devgate import DevSampleIncomplete
        from ..screen.evaluate import evaluate, write_report

        p = proj()
        try:
            ev = evaluate(p, run_id, sample)
            write_report(p, ev)
            return ev
        except (ValueError, KeyError) as e:
            if isinstance(e, DevSampleIncomplete):
                raise   # 409 with its counts (D4)
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/screen/passes")
    def get_screen_passes(mode: str = "protocol", provider: str | None = None, provider_b: str | None = None,
                          provider_c: str | None = None):
        """The pass pickers' defaults, the passes that do not run and the shared-model sentences: the lines
        ``wider screen protocol`` and ``wider screen second`` print before a run (design D13, D14). Sends nothing."""
        from ..screen.protocol import pass_view

        p = proj()
        try:
            return pass_view(p, mode, provider, provider_b, provider_c)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)[:400]) from e
        finally:
            p.close()

    @app.get("/api/screen/models")
    def get_screen_models(protocol: str | None = None, run_id: str | None = None, result_set: int | None = None):
        """The models summary (same as ``wider screen models``)."""
        from ..screen.models_summary import by_model

        p = proj()
        try:
            return by_model(p, protocol=None if run_id else (protocol or "main"), run_id=run_id, result_set=result_set)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)[:400]) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        finally:
            p.close()

    @app.post("/api/screen/export")
    def post_screen_export(protocol: str = "main", result_set: int | None = None):
        """Write the decisions and evaluations CSVs (same as ``wider screen export``); download them through
        ``GET /api/outputs/file?path=…``."""
        from ..screen.devgate import DevSampleIncomplete
        from ..screen.export import export_protocol

        p = proj()
        try:
            return export_protocol(p, protocol, result_set)
        except DevSampleIncomplete:
            raise
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)[:400]) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        finally:
            p.close()

    def _sets_call(fn):
        p = proj()
        try:
            return fn(p)
        except KeyError as e:
            raise HTTPException(404, str(e.args[0] if e.args else e)[:400]) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        finally:
            p.close()

    @app.get("/api/screen/sets")
    def get_screen_sets(protocol: str = "main"):
        """Every result set of a protocol (same as ``wider screen sets list``)."""
        from ..screen import result_sets as rs

        return _sets_call(lambda p: {"protocol": protocol, "sets": rs.sets(p, protocol)})

    @app.post("/api/screen/sets/{protocol}/{n}/use")
    def post_screen_set_use(protocol: str, n: int, with_source: bool = False):
        from ..screen import result_sets as rs

        return _sets_call(lambda p: rs.use(p, protocol, n, with_source=with_source))

    @app.patch("/api/screen/sets/{protocol}/{n}")
    def patch_screen_set(protocol: str, n: int, body: SetPatch):
        from ..screen import result_sets as rs

        def go(p):
            if body.label is None and body.scope is None:
                raise ValueError("give a label or a scope")
            r = None
            if body.label is not None:
                r = rs.label(p, protocol, n, body.label)
            if body.scope is not None:
                r = rs.set_scope(p, protocol, n, body.scope)
            return r
        return _sets_call(go)

    @app.get("/api/screen/sets/{protocol}/compare")
    def get_screen_sets_compare(protocol: str, a: int, b: int, models_only: bool = False):
        from ..screen import result_sets as rs

        return _sets_call(lambda p: rs.compare(p, protocol, a, b, models_only=models_only))

    @app.get("/api/screen/calls/{run_id}")
    def get_screen_calls(run_id: str, limit: int = 100, decision: str | None = None):
        p = proj()
        try:
            sql = ("SELECT c.record_id, c.raw_decision, c.rule_decision, c.parsed, c.error, m.title FROM screen_calls c "
                   "JOIN records m ON m.record_id=c.record_id WHERE c.run_id=? ")
            params: list = [run_id]
            if decision:
                sql += "AND c.rule_decision=? "
                params.append(decision)
            sql += "ORDER BY c.record_id LIMIT ?"
            params.append(limit)
            from ..screen.devgate import hidden_ids

            hide = hidden_ids(p)                # ui-redesign D4: no development record's reads while incomplete
            out, left = [], False
            for r in p.store.query(sql, params):
                if r["record_id"] in hide:
                    left = True
                    continue
                d = dict(r)
                d["parsed"] = p.store.unj(d["parsed"])
                out.append(d)
            if not left:
                return out
            # the list body stays; that records were left out goes in a header, naming no count (blinding)
            from fastapi.encoders import jsonable_encoder
            from fastapi.responses import JSONResponse

            from ..screen.devgate import LEFT_OUT_LIST
            return JSONResponse(jsonable_encoder(out), headers={"X-Hidden": LEFT_OUT_LIST})
        finally:
            p.close()

    # ----- recall and report -----
    @app.get("/api/recall")
    def get_recall(protocol: str, sample: str | None = None, B: int | None = None, result_set: int | None = None,
                   compare: int | None = None, set_: int | None = Query(None, alias="set"), choice: str | None = None):
        """Recall of ``protocol``'s active set, or of set ``result_set`` (``set`` is the same, as in ``--set``). A set
        that needs the choice of D8 answers 200 with ``choice_needed`` and the notice, and no estimate, until
        ``choice=used|not_informed`` is sent. A validation sample not yet complete answers 200 with
        ``labels_incomplete``, the notice and the completeness counts (label-completion-and-sealing D4)."""
        from ..recall.estimate import estimate
        from ..recall.validation import LabelsIncomplete, SampleChoiceNeeded, incomplete_answer
        from ..sample.labels import ui_wording

        result_set = result_set if result_set is not None else set_
        p = proj()
        sample = sample or p.config.protocol.sampling.probability_sample
        try:
            return ui_wording(with_result(p, "recall", estimate(p, sample, protocol, B=B, result_set=result_set,
                                                                compare=compare, choice=choice)))
        except SampleChoiceNeeded as e:   # nothing estimated or recorded: the page asks the researcher first (D8)
            return {"choice_needed": True, "notice": e.notice, "sample": sample, "protocol": protocol}
        except LabelsIncomplete as e:   # nothing estimated or recorded until every sampled record is final (D4)
            return incomplete_answer(e, protocol)
        except ValueError as e:  # unknown protocol or sample
            raise HTTPException(404, str(e)[:400])
        except KeyError as e:   # an unknown result set is not found; other key errors are bad requests
            msg = str(e.args[0]) if e.args else str(e)
            raise HTTPException(404 if msg.startswith("no result set") else 400, msg[:400])
        finally:
            p.close()

    def _read_report(p):
        import json

        md, js = p.path("exports", "report.md"), p.path("exports", "report.json")
        if not (md.exists() and js.exists()):
            raise HTTPException(404, "no report written yet")
        return {"report": json.loads(js.read_text(encoding="utf-8")), "markdown": md.read_text(encoding="utf-8")}

    @app.get("/api/report")
    def get_report():
        """The last written report; writes and records nothing (404 before any report)."""
        p = proj()
        try:
            return _read_report(p)
        finally:
            p.close()

    def _pending(p) -> dict:
        import json

        last = json.loads(p.store.get_meta("last_report") or "{}")
        return {"choice_needed": True, "notice": last.get("notice")} if last.get("choice_needed") else {}

    @app.post("/api/report")
    def post_report(protocol: str | None = None, sample: str | None = None, result_set: int | None = None,
                    choice: str | None = None):
        """Build and write the report once, recording one recall/report stage result; a set waiting for the D8
        choice is written with the pending section and answers ``choice_needed``."""
        from ..recall.report import write_report

        p = proj()
        try:
            write_report(p, protocol=protocol, sample=sample, result_set=result_set, choice=choice)
            return {**_read_report(p), "stage_result": last_result(p, "recall"), **_pending(p)}
        except ValueError as e:  # unknown protocol or sample
            raise HTTPException(404, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/report/bundle")
    def post_report_bundle(protocol: str | None = None, sample: str | None = None, result_set: int | None = None,
                           choice: str | None = None):
        from ..core.sanitize import SecretFound
        from ..recall.report import share_bundle
        from ..screen.devgate import DevSampleIncomplete

        p = proj()
        try:
            return {"path": str(share_bundle(p, protocol=protocol, sample=sample, result_set=result_set, choice=choice)),
                    "stage_result": last_result(p, "recall"), **_pending(p)}
        except SecretFound as e:  # the bundle was removed; the message names file, line and kind, never the value
            raise HTTPException(400, str(e)[:1000])
        except DevSampleIncomplete:
            raise
        except ValueError as e:  # unknown protocol or sample
            raise HTTPException(404, str(e)[:400])
        finally:
            p.close()

    # ----- setup page: backends, seed papers, databases, protocol (wider/ui/routes_setup.py) -----
    from .routes_setup import register as register_setup

    register_setup(app, proj)

    # query refinement: unlock, the frozen-query notices (wider/ui/routes_refine.py)
    from .routes_refine import register as register_refine
    register_refine(app, proj)

    from .routes_seeds import register as register_seeds  # seed PDFs, name mining, streams: /api/seeds/*
    register_seeds(app, proj)
    from .routes_adjacent import register as register_adjacent  # adjacent-literature loop: /api/adjacent/*
    register_adjacent(app, proj)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    from .routes_import import register as register_import
    register_import(app, proj)
    from .routes_prior import register as register_prior  # prior reviews: /api/prior-reviews/*
    register_prior(app, proj)
    from .routes_home import register as register_home  # home page: summary, switcher, backend check
    register_home(app, proj, recent)
    from .routes_records import register as register_records  # record drawer: notes, tags, comparison
    register_records(app, proj)
    from .jobs import JobRunner
    from .routes_jobs import register as register_jobs  # background runs: progress, ETA, cost so far, cancel
    app.state.jobs = JobRunner()
    register_jobs(app, proj, app.state.jobs)
    from .routes_compare import register as register_compare  # configuration comparison: /api/screen/compare
    register_compare(app, proj, app.state.jobs)
    from .routes_assistant import register as register_assistant  # Ask WIDER: /api/assistant/*
    register_assistant(app, proj)
    from .routes_validation import register as register_validation  # sample planner: /api/validation/*
    register_validation(app, proj)
    from .routes_outputs import register as register_outputs  # PRISMA, methods, references, package, archive
    register_outputs(app, proj)
    from .routes_questions import register as register_questions  # doubts: questions and answers
    register_questions(app, proj)
    from .routes_history import register as register_history  # version history and undo
    register_history(app, proj)
    from .routes_reporting import register as register_reporting  # PRISMA reporting: /api/reporting/*
    register_reporting(app, proj)
    from .routes_researchers import register as register_researchers  # researcher list, names in shared files
    register_researchers(app, proj)
    from .routes_review import register as register_review  # Screening review: /api/review/*
    register_review(app, proj)
    from .routes_blinding import register as register_blinding  # review packages: /api/blinding/*
    register_blinding(app, proj)
    from .routes_notebook import register as register_notebook  # step notebook and pair history
    register_notebook(app, proj)
    from .routes_lookups import register as register_lookups  # lookup consent and log; 403 handler
    register_lookups(app, proj)
    from .routes_backend_consent import register as register_backend_consent  # backend consent; 403 handler
    register_backend_consent(app, proj)

    # Python 3.11 has no .woff2 type, so the shipped font would get whatever the machine's mime.types says
    mimetypes.add_type("font/woff2", ".woff2")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
