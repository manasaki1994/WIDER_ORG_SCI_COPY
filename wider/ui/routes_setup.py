"""Setup-page routes: LLM and embedding backends, seed papers, the database catalogue and the review protocol.

Registered once from ``app.py`` with ``register(app, P)`` where ``P()`` returns a freshly loaded ``Project``.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel


class ProviderBody(BaseModel):
    """`wider providers add`. Without ``preset`` the kind defaults to ``openai_compat``; with it every unset field comes
    from the catalogue and an empty name takes the preset id or the first free ``<id>-N``."""
    name: str | None = None
    kind: str | None = None
    preset: str | None = None
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    command: list[str] | str | None = None
    extra: dict[str, Any] | None = None
    set_default: bool = False
    replace: bool = False
    accept_experimental: bool = False
    temperature: float | None = None
    timeout_s: int | None = None
    max_concurrency: int | None = None
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_cached_input_per_mtok: float | None = None
    price_cache_write_per_mtok: float | None = None


class ProviderPatch(BaseModel):
    """`wider providers edit NAME`: ``None`` leaves a field unchanged; ``extra`` merges key by key (an empty value
    removes the key)."""
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    command: list[str] | str | None = None
    extra: dict[str, Any] | None = None
    temperature: float | None = None
    timeout_s: int | None = None
    max_concurrency: int | None = None
    price_input_per_mtok: float | None = None
    price_output_per_mtok: float | None = None
    price_cached_input_per_mtok: float | None = None
    price_cache_write_per_mtok: float | None = None


class EmbeddingBody(BaseModel):
    name: str | None = None
    kind: str | None = None
    preset: str | None = None
    model: str = ""
    dim: int | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    set_default: bool = False
    replace: bool = False
    batch_size: int | None = None
    price_input_per_mtok: float | None = None


class EmbeddingPatch(BaseModel):
    """`wider embed edit NAME`: ``None`` leaves a field unchanged."""
    model: str | None = None
    dim: int | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    batch_size: int | None = None
    price_input_per_mtok: float | None = None


class EstimateBody(BaseModel):
    """The range `--estimate-only` prints: ``stage`` is screen (``mode`` pass, protocol or second), construct
    (``kind``) or embed (``embedding``)."""
    stage: str
    mode: str = "pass"
    scope: str | None = None
    protocol: str = "main"
    provider: str | None = None
    prompt_version: int | None = None
    limit: int | None = None
    kind: str | None = None
    embedding: str | None = None
    reuse_evaluations: bool = False
    provider_b: str | None = None   # screen protocol/second: pass B's (S2's) backend
    provider_c: str | None = None   # the judge's (S3's) backend


class ModelsBody(BaseModel):
    preset: str | None = None
    kind: str | None = None          # a form filled in by hand (no preset)
    base_url: str | None = None


class NameBody(BaseModel):
    name: str


class StageBody(BaseModel):
    stage: str
    name: str | None = None


class SeedAdd(BaseModel):
    text: str
    set: str = "Development"
    filename: str | None = None  # set when ``text`` is the content of an uploaded CSV / RIS file
    source: str = "researcher"


class ReferencesBody(BaseModel):
    benchmark_id: str | None = None   # one Development seed; all of them when empty
    route: str = "auto"               # auto | openalex | pdf
    tier: str | None = None           # grobid | crossref | model


class ChainBody(BaseModel):
    direction: str = "both"
    limit: int | None = None
    from_candidates: bool = False   # a snowball round: only the Development seeds taken from candidates


class CandidateAdd(BaseModel):
    set: str = "Development"


class SplitBody(BaseModel):
    dev_share: float | None = None
    seed: int | None = None
    apply: bool = False
    confirm: bool = False


class AssignBody(BaseModel):
    benchmark_id: str
    set: str
    confirm: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


class SourceBody(BaseModel):
    benchmark_id: str
    source: str


class KeywordsBody(BaseModel):
    benchmark_id: str
    keywords: str = ""


class PlatformAdd(BaseModel):
    key: str
    label: str | None = None
    syntax: str | None = None
    role: str | None = None


class OrderBody(BaseModel):
    keys: list[str]


class RoleBody(BaseModel):
    key: str
    role: str


class ProtocolSet(BaseModel):
    path: str
    value: Any


class ProtocolReset(BaseModel):
    path: str | None = None


def register(app: FastAPI, P) -> None:
    from ..core import backends

    # ----------------------------------------------------------------- providers
    @app.get("/api/providers")
    def get_providers():
        p = P()
        return {"providers": backends.provider_rows(p), "default": p.config.default_provider,
                "stage_providers": p.config.stage_providers, "kinds": list(backends.PROVIDER_KINDS),
                "stages": list(backends.STAGE_ROWS)}

    @app.get("/api/providers/presets")
    def get_provider_presets():
        from ..providers.presets import preset_row, presets
        return {"presets": [preset_row(x) for x in presets()]}

    @app.get("/api/backends/detect")
    def get_backends_detect():
        from ..core.detect import detect
        return {"rows": detect()}

    @app.get("/api/providers/{name}/models")
    def get_provider_models(name: str):
        """The CLI twin is `wider providers models NAME`; a network, cloud or unknown backend answers 403
        ``backend_consent_required`` until it is allowed for ``models``."""
        from ..core.detect import list_models
        p = P()
        try:
            try:
                cfg = p.provider(name)
            except KeyError:
                raise HTTPException(404, f"unknown provider {name}")
            try:
                return {"models": list_models(cfg, project=p)}
            except ValueError as e:
                raise HTTPException(502, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/providers/models")
    def post_preset_models(body: ModelsBody):
        """`wider providers models --preset ID | --kind KIND [--base-url]`: local servers and CLI catalogue lists
        only."""
        from ..core.detect import NeedsSavedBackend, list_models
        try:
            return {"models": list_models(preset_id=body.preset, base_url=body.base_url, kind=body.kind)}
        except KeyError:
            raise HTTPException(404, f"unknown preset {body.preset}")
        except NeedsSavedBackend as e:
            raise HTTPException(400, str(e))
        except ValueError as e:
            raise HTTPException(502, str(e)[:400])

    def _add(add, body, label: str, **kw):
        """Shared by both POST routes: 409 with the fields a replace would reset when the name exists and ``replace``
        is false (nothing saved), 400 for a refusal; the answer carries ``reset_fields`` after a replace."""
        p = P()
        try:
            args = body.model_dump(exclude={"replace"})
            name = (args.pop("name") or "").strip() or None
            if name and not body.replace and name in [x.name for x in getattr(p.config, label + "s")]:
                _cfg, reset = add(p, name, replace=True, dry_run=True, **args, **kw)
                raise HTTPException(409, {"error": f"{label}_exists", "name": name, "reset_fields": reset,
                                          "message": f"{name} already exists; replacing it resets "
                                                     f"{', '.join(reset) or 'nothing'}. Use Edit to keep the rest."})
            cfg, reset = add(p, name, replace=body.replace, **args, **kw)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
        return {**cfg.model_dump(mode="json"), "reset_fields": reset}

    @app.post("/api/providers")
    def post_provider(body: ProviderBody):
        return _add(backends.add_provider, body, "provider")

    @app.patch("/api/providers/{name}")
    def patch_provider(name: str, body: ProviderPatch):
        p = P()
        try:
            cfg = backends.update_provider(p, name, **body.model_dump())
        except KeyError:
            raise HTTPException(404, f"unknown provider {name}")
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
        return cfg.model_dump(mode="json")

    @app.delete("/api/providers/{name}")
    def delete_provider(name: str):
        p = P()
        try:
            backends.remove_provider(p, name)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        return {"ok": True, "default": p.config.default_provider}

    @app.post("/api/providers/default")
    def post_provider_default(body: NameBody):
        p = P()
        try:
            backends.set_default_provider(p, body.name)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        return {"ok": True, "default": body.name}

    @app.post("/api/providers/stage")
    def post_provider_stage(body: StageBody):
        p = P()
        try:
            backends.set_stage_provider(p, body.stage, body.name)
        except KeyError:
            raise HTTPException(404, "unknown provider")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "stage_providers": p.config.stage_providers}

    # ----------------------------------------------------------------- embeddings
    @app.get("/api/embeddings")
    def get_embeddings():
        p = P()
        return {"embeddings": backends.embedding_rows(p), "default": p.config.default_embedding,
                "kinds": list(backends.EMBEDDING_KINDS)}

    @app.post("/api/embeddings")
    def post_embedding(body: EmbeddingBody):
        return _add(backends.add_embedding, body, "embedding")

    @app.patch("/api/embeddings/{name}")
    def patch_embedding(name: str, body: EmbeddingPatch):
        p = P()
        try:
            cfg = backends.update_embedding(p, name, **body.model_dump())
        except KeyError:
            raise HTTPException(404, f"unknown embedding {name}")
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
        return cfg.model_dump(mode="json")

    # ----------------------------------------------------------------- cost estimate
    @app.post("/api/estimate")
    def post_estimate(body: EstimateBody):
        """The range and lines `--estimate-only` prints, before any call (Decision 15); 400 when it cannot be made.
        The pages call it when they open, so it makes no request: the backend is placed as its row is."""
        from ..construct.propose import ProposalError
        from ..core.estimate import estimate_construct, estimate_embed, estimate_screen, range_text

        p = P()
        try:
            if body.stage == "screen":
                r = estimate_screen(p, body.mode, scope=body.scope, protocol=body.protocol, provider_name=body.provider,
                                    prompt_version=body.prompt_version, limit=body.limit,
                                    reuse_evaluations=body.reuse_evaluations, probe=False,
                                    provider_b=body.provider_b, provider_c=body.provider_c)
            elif body.stage == "construct":
                r = estimate_construct(p, body.kind or "", body.provider, probe=False)
            elif body.stage == "embed":
                r = estimate_embed(p, body.embedding)
            else:
                raise HTTPException(400, f"unknown stage {body.stage!r}; choose screen, construct or embed")
        except (ValueError, KeyError, TypeError, ProposalError) as e:
            raise HTTPException(400, str(e).strip("'\"")[:400])
        finally:
            p.close()
        return {**r, "lines": range_text(r)}

    @app.delete("/api/embeddings/{name}")
    def delete_embedding(name: str):
        p = P()
        try:
            backends.remove_embedding(p, name)
        except KeyError:
            raise HTTPException(404, "unknown embedding")
        return {"ok": True, "default": p.config.default_embedding}

    @app.post("/api/embeddings/default")
    def post_embedding_default(body: NameBody):
        p = P()
        try:
            backends.set_default_embedding(p, body.name)
        except KeyError:
            raise HTTPException(404, "unknown embedding")
        return {"ok": True, "default": body.name}

    @app.post("/api/embeddings/{name}/download")
    def post_embedding_download(name: str):
        """Download a local model's weights. Only on this explicit request; never on page load or probe."""
        p = P()
        try:
            return backends.download_embedding(p, name)
        except KeyError:
            raise HTTPException(404, "unknown embedding")

    # ----------------------------------------------------------------- seed papers
    @app.get("/api/plan")
    def get_plan():
        """The Review plan's task cards (`wider plan --json`), with the Plan summary line."""
        from ..core import plan

        p = P()
        try:
            cards = plan.tasks(p)
            return {"tasks": cards, "summary": plan.summary_line(cards), "ready": plan.ready(cards)}
        finally:
            p.close()

    @app.get("/api/benchmark/seed")
    def get_seed():
        from ..construct import seed
        from ..search import coverage

        p = P()
        try:
            papers = p.read_benchmark()
            # used papers were seen already, so their count is no look; the invite follows a move of them (D4)
            return {"rows": seed.status(p), "rule": seed.SPLIT_RULE, "sources": list(seed.SOURCES),
                    "dev_share": p.config.protocol.identification.development_share, "seed": p.config.seed,
                    "n_used": len(coverage.used_papers(p, papers)),
                    "invite": seed.ADD_INVITE if coverage.invite_due(p, papers) else None}
        finally:
            p.close()

    @app.post("/api/benchmark/add")
    def post_seed_add(body: SeedAdd):
        from ..construct import seed

        p = P()
        try:
            skipped: list[dict] = []   # rows already seeds (or repeated) are reported, never dropped silently (M8)
            if body.filename:
                added = seed.add_from_text(p, body.text, body.filename, body.set, body.source, skipped)
            else:
                added = seed.add_lines(p, body.text, body.set, body.source, skipped)
            return {"added": [a.model_dump(mode="json") for a in added],
                    "skipped": skipped, "skipped_summary": seed.skipped_summary(skipped),
                    "rows": seed.status(p, entering={a.benchmark_id for a in added}),
                    "notices": {a.benchmark_id: seed.set_notices(p, a) for a in added if seed.set_notices(p, a)}}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/benchmark/resolve")
    def post_seed_resolve(all: bool = False):
        from ..construct import seed

        p = P()
        try:
            rows = seed.resolve(p, only_unresolved=not all)
            return {"rows": rows, **seed.resolve_summary(rows)}
        finally:
            p.close()

    @app.post("/api/benchmark/split")
    def post_seed_split(body: SplitBody):
        from ..construct import seed

        p = P()
        try:
            split = seed.suggest_split(p, dev_share=body.dev_share, seed=body.seed)
            if body.apply:
                try:
                    seed.apply_split(p, split, confirm=body.confirm)
                except seed.MoveNeedsConfirm as e:   # nothing written: the page asks first (D3 of validation-reuse)
                    return {**split, "confirm_needed": True, "notice": e.notice}
                split["applied"] = True
            return split
        except ValueError as e:
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.post("/api/benchmark/assign")
    def post_seed_assign(body: AssignBody):
        from ..construct import seed

        p = P()
        try:
            bp = seed.set_assignment(p, body.benchmark_id, body.set, confirm=body.confirm)
            return {**bp.model_dump(mode="json"), "notices": seed.set_notices(p, bp)}
        except seed.MoveNeedsConfirm as e:   # nothing written: the page asks first (D3 of validation-reuse)
            return {"confirm_needed": True, "notice": e.notice}
        except KeyError:
            raise HTTPException(404, "unknown seed paper")
        finally:
            p.close()

    @app.post("/api/benchmark/move-used")
    def post_seed_move_used(body: ConfirmBody):
        """Twin of `wider benchmark move-used` (D4 of validation-reuse)."""
        from ..construct import seed

        p = P()
        try:
            ids = seed.move_used(p, confirm=body.confirm)
            return {"moved": ids, "invite": seed.ADD_INVITE}
        except seed.MoveNeedsConfirm as e:
            return {"confirm_needed": True, "notice": e.notice}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/benchmark/keywords")
    def post_seed_keywords(body: KeywordsBody):
        from ..construct import seed

        p = P()
        try:
            return seed.set_keywords(p, body.benchmark_id, body.keywords).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "unknown seed paper")
        finally:
            p.close()

    @app.post("/api/benchmark/source")
    def post_seed_source(body: SourceBody):
        from ..construct import seed

        p = P()
        try:
            return seed.set_source(p, body.benchmark_id, body.source).model_dump(mode="json")
        except KeyError:
            raise HTTPException(404, "unknown seed paper")
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.delete("/api/benchmark/{benchmark_id}")
    def delete_seed(benchmark_id: str):
        from ..construct import seed

        p = P()
        try:
            seed.remove(p, benchmark_id)
            return {"ok": True}
        except KeyError:
            raise HTTPException(404, "unknown seed paper")
        finally:
            p.close()

    # ----------------------------------------------------------------- candidates from the seed papers (2c, D16)
    @app.post("/api/benchmark/chain")
    def post_seed_chain(body: ChainBody):
        from ..seeds import chain

        if body.direction not in chain.DIRECTIONS:
            raise HTTPException(400, "direction must be references, cited_by or both")
        p = P()
        try:
            # ConsentRequired -> 403 (routes_lookups)
            return chain.chain_seeds(p, body.direction, body.limit, from_candidates=body.from_candidates)
        finally:
            p.close()

    @app.post("/api/benchmark/references")
    def post_seed_references(body: ReferencesBody):
        """``wider benchmark references [ID] [--route] [--tier]``: OpenAlex first, the PDF as the fallback."""
        from ..seeds import references

        p = P()
        try:
            # ConsentRequired and BackendConsentRequired -> 403 before anything is sent
            r = references.seed_references(p, body.benchmark_id or None, body.route, tier=body.tier or None)
            return {**r, "lines": references.lines(r)}
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    def _candidate_list(p, round_no: int | None = None) -> dict:
        from ..seeds import chain

        return {"candidates": chain.candidates(p, round_no), "rounds": chain.rounds(p),
                "stop_notice": chain.stop_notice(p), "rank_note": chain.RANK_NOTE, "imports": chain.imports(p)}

    @app.get("/api/benchmark/candidates")
    def get_seed_candidates(round: int | None = None):
        p = P()
        try:
            return _candidate_list(p, round)
        finally:
            p.close()

    @app.post("/api/benchmark/candidates/import")
    async def post_candidates_import(file: UploadFile = File(...), origin: str = Form(...)):
        """Twin of `wider benchmark candidates --import FILE --origin LABEL`: nothing is sent."""
        from ..prior.reviews import safe_filename
        from ..seeds import chain

        name = safe_filename(file.filename or "candidates")
        data = await file.read()
        p = P()
        tmp = p.path("inputs", ".candidates")
        try:
            tmp.mkdir(parents=True, exist_ok=True)
            src = tmp / name
            src.write_bytes(data)
            try:
                r = chain.import_candidates(p, src, origin, file_name=name)
            finally:
                src.unlink(missing_ok=True)
            return {**r, **_candidate_list(p)}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/benchmark/candidates/imports/{import_no}/dismiss")
    def post_candidates_import_dismiss(import_no: int):
        """Twin of `wider benchmark candidates --dismiss-import N`."""
        from ..core.journal import last_change_id
        from ..seeds import chain

        p = P()
        try:
            r = chain.dismiss_import(p, import_no)
            return {**r, **_candidate_list(p), "change_id": last_change_id() if r["dismissed"] else None}
        except KeyError:
            raise HTTPException(404, "unknown candidate import")
        finally:
            p.close()

    @app.post("/api/benchmark/candidates/{candidate_id}/dismiss")
    def post_candidate_dismiss(candidate_id: int):
        from ..seeds import chain

        p = P()
        try:
            chain.dismiss(p, candidate_id)
            return {"candidates": chain.candidates(p)}
        except KeyError:
            raise HTTPException(404, "unknown candidate")
        except ValueError as e:
            raise HTTPException(409, str(e))
        finally:
            p.close()

    @app.post("/api/benchmark/candidates/{candidate_id}/add")
    def post_candidate_add(candidate_id: int, body: CandidateAdd):
        from ..construct import seed
        from ..seeds import chain

        p = P()
        try:
            a, notices = chain.add(p, candidate_id, body.set)
            return {"added": a.model_dump(mode="json"), "notices": {a.benchmark_id: notices} if notices else {},
                    "rows": seed.status(p, entering={a.benchmark_id}), "candidates": chain.candidates(p)}
        except KeyError:
            raise HTTPException(404, "unknown candidate")
        except ValueError as e:
            raise HTTPException(409, str(e))
        finally:
            p.close()

    # ----------------------------------------------------------------- databases
    @app.get("/api/platforms")
    def get_platforms_setup():
        from ..search import catalogue

        p = P()
        return {"catalogue": catalogue.catalogue(), "chosen": catalogue.project_platforms(p),
                "families": catalogue.syntax_families()}

    @app.post("/api/platforms/add")
    def post_platform_add(body: PlatformAdd):
        from ..search import catalogue

        p = P()
        try:
            catalogue.add_platform(p, body.key, label=body.label, syntax=body.syntax, role=body.role)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "chosen": catalogue.project_platforms(p)}

    @app.delete("/api/platforms/{key}")
    def delete_platform(key: str):
        from ..search import catalogue

        p = P()
        try:
            catalogue.remove_platform(p, key)
        except KeyError:
            raise HTTPException(404, "not a chosen platform")
        return {"ok": True, "chosen": catalogue.project_platforms(p)}

    @app.post("/api/platforms/order")
    def post_platform_order(body: OrderBody):
        from ..search import catalogue

        p = P()
        try:
            catalogue.order_platforms(p, body.keys)
        except KeyError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "chosen": catalogue.project_platforms(p)}

    @app.post("/api/platforms/role")
    def post_platform_role(body: RoleBody):
        from ..search import catalogue

        p = P()
        try:
            catalogue.set_role(p, body.key, body.role)
        except KeyError:
            raise HTTPException(404, "not a chosen platform")
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"ok": True, "chosen": catalogue.project_platforms(p)}

    # ----------------------------------------------------------------- protocol
    def _protocol_payload(p, recorded_seen: int = 0):
        from ..search.coverage import HIDING_ON_SUMMARY

        rows = p.config.protocol.explain()
        sections, seen = [], set()
        for r in rows:
            if r["section"] not in seen:
                seen.add(r["section"])
                sections.append({"key": r["section"], "doc": r["section_doc"],
                                 "changed": sum(1 for x in rows if x["section"] == r["section"] and x["changed"])})
        from ..rank.versions import thin_words_effect
        from ..screen.devgate import reviewers_effect

        other = 3 - p.config.protocol.sampling.development_reviewers   # said before saving: the value a Set would store
        return {"rows": rows, "sections": sections, "hash": p.config.protocol.hash(),
                "effects": {"corpus.thin_words": thin_words_effect(p),
                            # said as a change, so it is not read as the stored value (ui-tester finding, 29 Oct)
                            "sampling.development_reviewers": f"If you change it to {other}: {reviewers_effect(p, other)}"},
                "changed": sum(1 for r in rows if r["changed"]),
                "notice": HIDING_ON_SUMMARY.format(n=recorded_seen) + "; logged in the history" if recorded_seen else ""}

    @app.get("/api/protocol")
    def get_protocol():
        return _protocol_payload(P())

    @app.post("/api/protocol/set")
    def post_protocol_set(body: ProtocolSet):
        from ..recall.validation import TargetError, save_protocol
        from ..search.coverage import hiding_turned_on

        p = P()
        old = p.config.protocol
        was_on = old.identification.validation_after_freeze
        try:
            p.config.protocol = p.config.protocol.set(body.path, body.value)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e)[:600])
        n = hiding_turned_on(p, was_on)
        try:
            timed = save_protocol(p, old)
        except TargetError as e:
            raise HTTPException(400, str(e)) from e
        return _protocol_payload(p, n) | {"timed": timed[1:]}

    @app.post("/api/protocol/reset")
    def post_protocol_reset(body: ProtocolReset):
        from ..core.protocol import ReviewProtocol
        from ..recall.validation import TargetError, save_protocol
        from ..search.coverage import hiding_turned_on

        p = P()
        old = p.config.protocol
        was_on = old.identification.validation_after_freeze
        try:
            if body.path:
                p.config.protocol = p.config.protocol.set(body.path, ReviewProtocol().get(body.path))
            else:
                p.config.protocol = ReviewProtocol()
        except KeyError as e:
            raise HTTPException(400, str(e.args[0])[:400])
        except (ValueError, AttributeError) as e:
            raise HTTPException(400, str(e)[:400])
        n = hiding_turned_on(p, was_on)
        try:
            timed = save_protocol(p, old)
        except TargetError as e:
            raise HTTPException(400, str(e)) from e
        return _protocol_payload(p, n) | {"timed": timed[1:]}
