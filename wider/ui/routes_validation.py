"""Recall validation routes (change validation-sample-planner): the sample planner, the target and its status. Each calls the engine function the
CLI calls (``wider/sample/plan.py::run``, ``wider/recall/validation.py``); a plan that cannot be made answers 400 with the engine's sentence."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class PlanBody(BaseModel):   # module level: FastAPI must resolve the body model by name
    budget: int | None = None
    precision: float | None = None
    target: float | None = None
    rates: str | dict | None = None
    expected_recall: float | None = None
    allocation: str | dict | None = None
    percentiles: str | list[float] | None = None
    sims: int | None = None
    protocol: str | None = None
    result_set: int | None = None
    apply: str | None = None
    author: str | None = None


class TargetBody(BaseModel):
    target: float
    rule: str | None = None
    depth: int | None = None
    precision: float | None = None
    author: str | None = None


class ExtendBody(BaseModel):
    sample: str
    bands: dict[str, int] | None = None
    suggested: bool = False
    author: str | None = None


class StopBody(BaseModel):
    sample: str | None = None
    note: str | None = None
    author: str | None = None


class CriteriaBody(BaseModel):
    sample: str | None = None
    substantive: str
    scope: str | None = None
    note: str | None = None
    author: str | None = None


def register(app: FastAPI, P) -> None:
    from ..sample import plan as pl

    @app.post("/api/validation/plan")
    def validation_plan(body: PlanBody):
        p = P()
        try:
            from ..recall.validation import as_fraction
            return pl.run(p, **{**body.model_dump(), "target": as_fraction(body.target)})
        except pl.PlanError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.post("/api/validation/target")
    def validation_target(body: TargetBody):
        from ..recall import validation as va
        from ..sample.labels import ui_wording

        p = P()
        try:
            lines = va.set_target(p, body.target, body.rule, body.depth, body.precision, body.author)
            return ui_wording({**va.status(p), "output": lines, "status": va.status_line(p)})
        except va.TargetError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/validation/status")
    def validation_status(sample: str | None = None):
        from ..recall import validation as va
        from ..sample.labels import ui_wording

        p = P()
        try:   # planned: a validation plan was applied (the Samples page's step 2, D32 F6)
            return ui_wording({**va.status(p, sample), "planned": bool(p.store.get_meta("validation_plan"))})
        finally:
            p.close()

    @app.post("/api/validation/extend")
    def validation_extend(body: ExtendBody):
        """``wider validation extend``: 409 for a locked, re-ranked or replaced sample and a changed suggestion, 400 for
        the rest; ``sample`` is required (422 without it)."""
        from ..sample.design import SampleConflict, extend_probability
        from ..sample.plan import SuggestionStale

        if bool(body.bands) == body.suggested:
            raise HTTPException(400, "give bands or suggested")
        p = P()
        try:
            r = extend_probability(p, body.sample, "suggested" if body.suggested else body.bands, author=body.author)
            return {k: v for k, v in r.items() if k != "change_id"}
        except (SampleConflict, SuggestionStale) as e:
            raise HTTPException(409, str(e)) from e
        except KeyError as e:
            raise HTTPException(404, str(e.args[0])) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.post("/api/validation/stop")
    def validation_stop(body: StopBody):
        """``wider validation stop``: refused (400) before any look, with the engine's sentence."""
        from ..recall import validation as va

        p = P()
        try:
            return va.stop(p, body.sample, body.note, body.author)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.get("/api/labels/criteria")
    def labels_criteria(sample: str | None = None):
        """``wider label criteria [SAMPLE] --json``: the change, the recommendation, the latest assessment (D11)."""
        from ..sample.labels import criteria_report

        p = P()
        try:
            return criteria_report(p, sample)
        except KeyError as e:
            raise HTTPException(404, str(e).strip('"')) from e
        finally:
            p.close()

    @app.post("/api/labels/criteria")
    def labels_criteria_assess(body: CriteriaBody):
        """``wider label criteria [SAMPLE] --assess yes|no [--scope] [--note]``; refusals answer 400."""
        from ..core.journal import current_author
        from ..sample.labels import assess_criteria, criteria_report

        p = P()
        try:
            assess_criteria(p, body.sample, body.substantive, body.scope, body.note or "", author=current_author(body.author))
            return criteria_report(p, body.sample)
        except KeyError as e:
            raise HTTPException(404, str(e).strip('"')) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()
