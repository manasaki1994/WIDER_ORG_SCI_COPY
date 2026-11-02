"""Step notebook routes (design-pass-a1-dedup D10): a step's runs, decisions and undos, newest first; and the
shipped guide text of a page with the project's notices (D11)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException


def register(app: FastAPI, P) -> None:
    from ..core.notebook import STAGES, step_notebook

    @app.get("/api/notebook/{stage}")
    def get_notebook(stage: str, limit: int = 50, a: int | None = None, b: int | None = None):
        """The step's notebook; with ``a`` and ``b`` only that raw pair's history (either order)."""
        if stage not in STAGES:
            raise HTTPException(404, f"no notebook for the step {stage!r}")
        if (a is None) != (b is None):
            raise HTTPException(400, "give both raw record ids of the pair, a and b")
        p = P()
        try:
            return step_notebook(p, stage, (a, b) if a is not None else None, max(1, min(limit, 5000)))
        finally:
            p.close()

    @app.get("/api/methods")
    def get_methods():
        """The methods document: every topic and the glossary (``wider guide methods --json``); no project needed."""
        from ..guide import methods
        return {"topics": methods.topics(), "glossary": methods.glossary(), "glossary_review": methods.glossary_review(),
                "problems": methods.problem_lines()}

    @app.get("/api/methods/{topic}")
    def get_methods_topic(topic: str):
        """One methods topic with its three layers (``wider guide methods <topic> --json``)."""
        from ..guide import methods
        try:
            return methods.topic(topic)
        except KeyError:
            raise HTTPException(404, methods.bad_topic(topic) or methods.NO_TOPIC.format(topic=topic)) from None

    @app.get("/api/guide/{page}")
    def get_guide(page: str):
        """The page's guide: what you can do here now, the six answers and the notices of the states the project is
        in (``wider guide <page> --json`` gives the same)."""
        from ..guide import NO_PAGE, guide, pages
        if page not in pages():
            raise HTTPException(404, NO_PAGE.format(page=page))
        p = P()
        try:
            return guide(p, page)
        finally:
            p.close()
