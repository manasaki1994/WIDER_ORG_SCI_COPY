"""Screening review routes (manual-review): the list, one record, a decision after screening, the set's criteria, a
quality-control draw and the pending count. Twins of `wider review ...`; the POSTs sign with the page's author header."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class DecideBody(BaseModel):
    decision: str
    reason: str = ""


class QcBody(BaseModel):
    n: int | None = None
    seed: int | None = None


def register(app: FastAPI, P) -> None:
    from ..core.journal import AUTHOR
    from ..screen import review as rv

    def guarded(p, fn):
        try:
            return fn()
        except rv.Refusal as e:
            raise HTTPException(e.status, str(e)) from e
        except KeyError as e:   # an unknown protocol or set
            raise HTTPException(404, str(e.args[0]) if e.args else str(e)) from e
        finally:
            p.close()

    @app.get("/api/review")
    def get_review(protocol: str | None = None, set: int | None = None, show: str = "flagged"):  # noqa: A002 - --set
        """The review list (`wider review list --json`), the protocols to choose from and the page's sentences."""
        p = P()

        def go():
            res = rv.flagged(p, protocol, set, show)
            from ..screen import result_sets as rs
            from ..screen.decisions import protocols

            sets = rs.sets(p, res["protocol"]) if res["protocol"] else []
            return {**res, "count_line": rv.count_line(res), "sentence": rv.SENTENCE, "nothing_line": rv.NOTHING,
                    "qc_sentence": rv.QC_SENTENCE, "protocols": [x["protocol"] for x in protocols(p)],
                    "sets": [{"set": int(s["result_set"]), "label": s.get("label") or f"set {s['result_set']}",
                              "active": bool(s.get("active"))} for s in sets]}
        return guarded(p, go)

    @app.get("/api/review/summary")
    def get_review_summary():
        """``pending_review`` of the default list (D12): the sidebar tooltip, the Screening card and Home."""
        p = P()
        return guarded(p, lambda: {"pending_review": rv.pending(p)})

    @app.get("/api/review/criteria")
    def get_review_criteria(protocol: str | None = None, set: int | None = None):  # noqa: A002
        p = P()
        return guarded(p, lambda: rv.criteria(p, protocol, set))

    @app.post("/api/review/qc")
    def post_review_qc(body: QcBody, protocol: str | None = None, set: int | None = None):  # noqa: A002
        p = P()
        return guarded(p, lambda: rv.qc_draw(p, protocol, set, body.n, body.seed, AUTHOR.get() or ""))

    @app.get("/api/review/{record_id}")
    def get_review_record(record_id: int, protocol: str | None = None, set: int | None = None):  # noqa: A002
        p = P()
        return guarded(p, lambda: rv.record_view(p, protocol, set, record_id))

    @app.post("/api/review/{record_id}")
    def post_review_record(record_id: int, body: DecideBody, protocol: str | None = None,
                           set: int | None = None):  # noqa: A002
        p = P()
        return guarded(p, lambda: rv.decide(p, protocol, set, record_id, body.decision, AUTHOR.get() or "",
                                            body.reason or None))
