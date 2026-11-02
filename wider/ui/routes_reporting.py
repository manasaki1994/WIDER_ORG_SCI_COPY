"""PRISMA reporting routes (prisma-reporting-assistant D14, D21): the checklists, one item, the researcher's text, the
not-applicable mark, the flow counts and the export. Every route calls the engine in :mod:`wider.recall.reporting`
that `wider report prisma ...` calls, so the page and the CLI give the same answers. The flow routes are registered
before ``/api/reporting/{checklist}`` so that ``flow`` is never read as a checklist name."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class TextBody(BaseModel):
    text: str | None = None
    covers: list[str] | None = None
    where: str | None = None
    from_draft: bool = False
    from_item: str | None = None
    keep: bool = False
    protocol: str | None = None
    result_set: int | None = Field(None, alias="set")


class NaBody(BaseModel):
    reason: str | None = None
    clear: bool = False


class FlowBody(BaseModel):
    protocol: str | None = None
    result_set: int | None = Field(None, alias="set")
    counts: dict[str, Any] | None = None      # the engine refuses bad values with its own words (D21)
    excluded: list[Any] | None = None


class FlowTarget(BaseModel):
    protocol: str | None = None
    result_set: int | None = Field(None, alias="set")


class ExportBody(BaseModel):
    protocol: str | None = None
    result_set: int | None = Field(None, alias="set")


def register(app: FastAPI, P) -> None:
    from ..recall import reporting as r

    def flow_view(p, protocol, result_set):
        """flow_state plus ``svg``, the diagram as the export draws it (prisma_2020_flow.svg), for the tab's preview."""
        from ..recall import flow_layout

        ctx = r.context(p, protocol, result_set)
        st = r.flow_state(p, ctx=ctx)
        layout = flow_layout.flow_boxes(ctx["counts"], st["entered"], st["excluded"], r.excluded_split(p, ctx["counts"]))
        return {**st, "svg": flow_layout.flow_svg(layout)}

    def guarded(p, fn):
        try:
            return fn()
        except KeyError as e:
            raise HTTPException(400, str(e.args[0] if e.args else e)[:400]) from e
        except ValueError as e:
            raise HTTPException(400, str(e)[:400]) from e
        finally:
            p.close()

    # ----- the flow diagram (D21): before /{checklist} -----
    @app.get("/api/reporting/flow")
    def get_flow(protocol: str | None = None, set: int | None = None):   # noqa: A002 (the page's parameter name)
        """The flow boxes with their source, the checks and the diagram's SVG; writes nothing."""
        p = P()
        return guarded(p, lambda: flow_view(p, protocol, set))

    @app.post("/api/reporting/flow")
    def post_flow(body: FlowBody):
        """Save the counts WIDER does not record and the reports excluded with their reasons (journalled)."""
        p = P()

        def go():
            res = r.save_flow(p, body.protocol, body.result_set, counts=body.counts or {}, excluded=body.excluded)
            return {**res, "flow": flow_view(p, body.protocol, body.result_set)}
        return guarded(p, go)

    @app.post("/api/reporting/flow/clear")
    def post_flow_clear(body: FlowTarget):
        """Remove the researcher's flow counts for this protocol and set (journalled)."""
        p = P()

        def go():
            res = r.clear_flow(p, body.protocol, body.result_set)
            return {**res, "flow": flow_view(p, body.protocol, body.result_set)}
        return guarded(p, go)

    @app.post("/api/reporting/export")
    def post_export(body: ExportBody):
        """Write exports/prisma_reporting/ and redraw exports/prisma_2020.svg; the message and every file written."""
        p = P()
        return guarded(p, lambda: r.export(p, body.protocol, body.result_set))

    # ----- the checklists (D4, D14, D25) -----
    @app.get("/api/reporting/{checklist}")
    def get_checklist(checklist: str, protocol: str | None = None, set: int | None = None,   # noqa: A002
                      status: str | None = None, to_check: bool = False):
        """The items in PRISMA order with WIDER's status and "Your text" (the rows of `status --json`), the
        checklist's title, its sentence on how the two checklists relate, and the shown protocol and set with what
        the page's selects offer. Viewing records nothing."""
        from ..recall.dashboard import _choices

        p = P()

        def go():
            cl = r.checklist_key(checklist)
            ctx = r.context(p, protocol, set)
            rows = r.status_rows(p, cl, to_check=to_check, only=status, ctx=ctx)
            info = ctx["info"]
            return {"checklist": cl, "title": r.TITLES[cl], "relate": r.RELATE[cl], "rows": rows,
                    "protocol": info["protocol"] if info else None, "result_set": info["result_set"] if info else None,
                    "choices": _choices(p, info["protocol"]) if info else {"protocols": [], "sets": []}}
        return guarded(p, go)

    @app.get("/api/reporting/{checklist}/{no}")
    def get_item(checklist: str, no: str, protocol: str | None = None, set: int | None = None):   # noqa: A002
        """One item as `show --json` gives it: the D4 blocks, the related items, your text and the draft."""
        p = P()

        def go():
            ctx = r.context(p, protocol, set)
            return {**r.show(p, checklist, no, ctx), "draft": r.draft(p, checklist, no, ctx)}
        return guarded(p, go)

    @app.post("/api/reporting/{checklist}/{no}/text")
    def post_text(checklist: str, no: str, body: TextBody):
        """Save the researcher's text, covers, location, or keep it for the current evidence (`edit`)."""
        if sum(1 for x in (body.text is not None, body.from_draft, bool(body.from_item)) if x) > 1:
            raise HTTPException(400, "give one of text, from_draft and from_item")
        if body.text is None and not body.from_draft and not body.from_item and body.where is None \
                and body.covers is None and not body.keep:
            raise HTTPException(400, "give text, from_draft, from_item, covers, where or keep")
        p = P()
        return guarded(p, lambda: r.save_text(p, checklist, no, text=body.text, covers=body.covers, where=body.where,
                                              from_draft=body.from_draft, from_item=body.from_item, keep=body.keep,
                                              protocol=body.protocol, result_set=body.result_set))

    @app.post("/api/reporting/{checklist}/{no}/na")
    def post_na(checklist: str, no: str, body: NaBody):
        """Mark the item not applicable with a reason, or remove the mark with ``clear`` (`na`, `na --clear`)."""
        p = P()
        return guarded(p, lambda: r.mark_not_applicable(p, checklist, no, reason=body.reason, remove=body.clear))

    @app.post("/api/reporting/{checklist}/{no}/clear")
    def post_clear(checklist: str, no: str):
        """Clear the researcher's text, covers and location; earlier versions stay (`clear`)."""
        p = P()
        return guarded(p, lambda: r.clear(p, checklist, no))
