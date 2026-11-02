"""Query refinement routes: the synonyms versions with the notices a save, freeze or unlock would give (shown in
the page's confirm steps), the unlock (D2), the dry run on the seed papers (D6), the diagnosis of imported exports
(D9), the suggested edits and the platform limits (D10). The CLI twin is
``wider/cli_refine.py``."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .routes_indexing import register as register_indexing


class UnlockBody(BaseModel):
    kind: str = "synonyms"
    version: int
    seen_choice: str | None = None   # "count" | "keep" when validation papers were shown (unlock-seen-choice D6)


class DryRunBody(BaseModel):
    version: int | None = None
    platform: str | None = None


class ClusterBody(BaseModel):
    key: str
    mark: str
    platform: str | None = None
    version: int | None = None


class EditBody(BaseModel):
    id: str
    version: int | None = None
    seen_choice: str | None = None


class MissedNamesBody(BaseModel):
    version: int | None = None
    platform: str | None = None
    min_links: int = 2
    model: bool = False


class LimitsBody(BaseModel):
    platform: str
    dates: str | None = None
    languages: str | None = None
    reason: str | None = None


def register(app: FastAPI, proj) -> None:
    from .routes_rounds import register as _rounds; _rounds(app, proj)   # noqa: E702, I001 rounds and acceptance (D8)
    @app.get("/api/construct/synonyms")
    def get_synonyms_notices():
        """Every synonyms version, the frozen query, and per version what freezing or unlocking it would say."""
        from ..construct import artifacts

        p = proj()
        try:
            rows = artifacts.list_versions(p, "synonyms")
            fq = artifacts.frozen_query(p)
            freeze_notice, freeze_choice, unlock_notice, unlock_choice = {}, {}, {}, {}
            for r in rows:
                v = r["version"]
                if fq is not None and fq["version"] == v:
                    continue
                plan = artifacts.freeze_plan(p, "synonyms", v)
                freeze_notice[v] = artifacts.freeze_notice(p, "synonyms", v)
                freeze_choice[v] = bool(plan["choice"])
            for r in rows:
                if r["status"] == "frozen":
                    unlock_notice[r["version"]] = artifacts.unlock_notice(p, "synonyms", r["version"])
                    unlock_choice[r["version"]] = artifacts.unlock_choice(p, r["version"])
            return {"versions": rows, "frozen_query": fq["version"] if fq else None,
                    "reopened": artifacts.query_reopened(p), "save_notice": artifacts.save_notice(p),
                    "save_choice": artifacts.save_choice(p), "freeze_notice": freeze_notice,
                    "freeze_choice": freeze_choice, "unlock_notice": unlock_notice, "unlock_choice": unlock_choice}
        finally:
            p.close()

    @app.post("/api/construct/unlock")
    def post_unlock(body: UnlockBody):
        """Copy a frozen query into a new draft and record the unlock (final, never undone)."""
        from ..construct import artifacts
        from ..core.journal import last_change_id

        p = proj()
        try:
            row = artifacts.unlock(p, body.kind, body.version, seen_choice=body.seen_choice)
            return {**row, "change_id": last_change_id()}
        except artifacts.SeenChoiceNeeded as e:   # nothing written: the page asks the researcher first (D6)
            raise HTTPException(400, e.notice)
        except KeyError as e:
            raise HTTPException(400, str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/profiles")
    def get_profiles():
        """The catalogue platforms with whether the dry run has a profile for them (the "Dry run" card's list), then
        OpenAlex, whose profile lives outside the catalogue and which is never run by default."""
        from ..search.evaluate import default_platforms, profiled_platforms
        from ..search.render import platforms

        p = proj()
        try:
            prof = set(profiled_platforms())
            return {"platforms": [{"key": k, "label": v.get("label", k), "profile": k in prof}
                                  for k, v in platforms().items()]
                    + [{"key": "openalex", "label": "OpenAlex", "profile": True}],
                    "default": default_platforms(p)}
        finally:
            p.close()

    @app.post("/api/query/dry-run")
    def post_dry_run(body: DryRunBody):
        """Evaluate a synonyms version on the seed papers under each platform's profile; writes the run folder."""
        from ..search.evaluate import dry_run

        p = proj()
        try:
            return dry_run(p, body.version, body.platform)
        except ValueError as e:   # no seed papers, no such version, no profile: nothing written
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/diagnose")
    def get_diagnose(version: int | None = None, platform: str | None = None):
        """What did what, clusters, noise and recall per set for the imported exports (the "Diagnostic report" card);
        ``lines`` and ``cluster_lines`` are the CLI's text."""
        from ..search import diagnostics

        p = proj()
        try:
            from ..interview import bank

            # nothing imported against a synonyms version yet is an expected state, not an error: 200 with
            # state "nothing_yet" and the reason (the CLI still exits 1 with the same sentence)
            if not p.store.scalar("SELECT COUNT(*) FROM sources WHERE included=1 AND query_version IS NOT NULL"):
                n = diagnostics.unrecorded_imports(p)
                why = (diagnostics.unrecorded_text(n) if n else
                       "no export imported against a synonyms version yet; import one on the Search results page")
                return {"state": "nothing_yet", "message": why, "version": None, "platforms": {}, "lines": [],
                        "cluster_lines": [], "interview": None, "interview_notice": None}
            d = diagnostics.what_did_what(p, version, platform)
            iv = bank.safe_trigger(p, "diagnose")   # the same questions as `wider query diagnose` (F14)
            return {**d, "state": "ready", "lines": diagnostics.diagnose_lines(d),
                    "cluster_lines": diagnostics.cluster_lines(d), "interview": iv, "interview_notice": bank.notice(iv)}
        except ValueError as e:   # no export of the asked version or platform
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/clusters")
    def get_clusters(version: int | None = None, platform: str | None = None):
        """The clusters per platform with their marks (`wider query clusters`)."""
        from ..search import diagnostics

        p = proj()
        try:
            d = diagnostics.what_did_what(p, version, platform)
            return {"version": d["version"], "platforms": {k: {"clusters": v.get("clusters", []), "note": v.get("note")}
                                                           for k, v in d["platforms"].items()},
                    "lines": diagnostics.cluster_lines(d)}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/cluster")
    def post_cluster(body: ClusterBody):
        """Mark a cluster relevant or noise (journalled ``cluster_mark``, undoable); no screening decision is made."""
        from ..search import diagnostics

        p = proj()
        try:
            return diagnostics.mark_cluster(p, body.key, body.mark, platform=body.platform, version=body.version)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/edits")
    def get_edits(version: int | None = None):
        """The suggested edits with their consequences (`wider query edits`); ``lines`` is the CLI's text."""
        from ..search import diagnostics

        p = proj()
        try:
            d = diagnostics.suggested_edits(p, version)
            return {**d, "lines": diagnostics.edit_lines(d)}
        except ValueError as e:   # no synonyms version
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/missed-names")
    def get_missed_names(version: int | None = None, platform: str | None = None, min_links: int = 2):
        """Names from connected papers the query misses (`wider query missed-names`), text pass only."""
        return _missed_names(version, platform, min_links, False)

    @app.post("/api/query/missed-names")
    def post_missed_names(body: MissedNamesBody):
        """The same with the model pass (`--model`): research consent missing answers 403 before anything is sent."""
        return _missed_names(body.version, body.platform, body.min_links, body.model)

    def _missed_names(version, platform, min_links, model):
        from ..seeds import missed_names as mn

        p = proj()
        try:
            d = mn.missed_names(p, version, platform, min_links, model=model)
            return {**d, "lines": mn.lines(d)}
        except ValueError as e:   # no synonyms version, no profile, min_links below 1
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/edit")
    def post_edit(body: EditBody):
        """Apply a suggested edit: a new draft synonyms version (`wider query edit apply <id>`)."""
        from ..construct.artifacts import SeenChoiceNeeded, save_seen_choice
        from ..search import diagnostics

        p = proj()
        try:
            with save_seen_choice(body.seen_choice):
                return diagnostics.apply_edit(p, body.id, body.version, confirm_revision=True)
        except SeenChoiceNeeded as e:   # nothing saved: the page asks the researcher first (D6)
            raise HTTPException(400, e.notice)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/limits")
    def get_limits():
        """Each platform's limits and the line shown under its rendered string (`wider query limits <platform>`)."""
        from ..search import limits

        p = proj()
        try:
            return {k: {"limits": v, "line": limits.limits_line(v)} for k, v in limits.limits(p).items()}
        finally:
            p.close()

    @app.post("/api/query/limits")
    def post_limits(body: LimitsBody):
        """Set a platform's limits (journalled ``platform_limits``, undoable); an empty field clears that limit."""
        from ..search import limits

        p = proj()
        try:
            return limits.set_limits(p, body.platform, body.dates, body.languages, body.reason)
        except ValueError as e:   # a range that does not parse or ends before it starts, an unknown platform
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    register_indexing(app, proj)   # /api/query/indexing (routes_indexing.py, 2.10)
