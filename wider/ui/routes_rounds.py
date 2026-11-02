"""Rounds of query refinement on the page (D8): the "Rounds" card's list and plot, the count typed by hand, the
acceptance with its confirm step, and the upload card's round selector; also one term's yield (D16) and the whole
query's OpenAlex check (D27). The CLI twin is ``wider/cli_rounds.py``; both call ``wider/search/rounds.py``."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class YieldBody(BaseModel):
    term: str | None = None
    query: bool = False   # the whole query instead of one term (D27)
    seeds: bool = False   # with query: which Development seeds OpenAlex retrieves
    version: int | None = None


class CountBody(BaseModel):
    platform: str
    n: int | str


def register(app: FastAPI, proj) -> None:
    @app.post("/api/query/yield")
    def post_yield(body: YieldBody):
        """One term's count on OpenAlex (D16), the twin of `wider query yield`. The term switch off answers 403 with
        ``consent_required`` (switch "terms") before anything is sent; the page's consent dialog offers the switch.
        With ``query`` the whole query is counted (D27), with ``seeds`` also checked against the Development seeds,
        which needs the DOI consent too (403 without it, before anything is sent). Exactly one of term or query."""
        from ..search.term_yield import query_yield, term_yield

        if (body.term is not None) == body.query:
            raise HTTPException(400, "give exactly one of term or query")
        if not body.query and (body.seeds or body.version is not None):
            raise HTTPException(400, "seeds and version go with query")
        p = proj()
        try:
            if body.query:
                return query_yield(p, version=body.version, seeds=body.seeds)
            return term_yield(p, body.term)
        except ValueError as e:   # an empty term, no synonyms version
            raise HTTPException(400, str(e))
        finally:
            p.close()

    @app.get("/api/query/rounds")
    def get_rounds():
        """Every round with its recall and count, the plot (SVG) and its text table, and the upload's round selector.
        Reads only (the CLI writes the SVG file)."""
        from ..search import round_plots, rounds

        p = proj()
        try:
            data = rounds.rounds(p)
            return {**data, "lines": rounds.table_lines(data), "svg": round_plots.recall_vs_yield(data["rounds"]),
                    "table": round_plots.recall_vs_yield_table(data["rounds"])}
        finally:
            p.close()

    @app.post("/api/query/rounds/refresh")
    def post_rounds_refresh():
        """Dry-run every round on today's seeds (``wider query rounds --refresh``)."""
        from ..search import rounds

        p = proj()
        try:
            return {"refreshed": rounds.refresh(p)}
        except ValueError as e:   # no seed papers, no profile
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/rounds/{round_no}/count")
    def post_round_count(round_no: int, body: CountBody):
        """The record count the database showed (pasted), as ``wider query count``; undoable."""
        from ..core.journal import last_change_id
        from ..search import rounds

        p = proj()
        try:
            return {**rounds.set_count(p, round_no, body.platform, body.n), "change_id": last_change_id()}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/rounds/{round_no}/replay")
    def get_round_replay(round_no: int):
        """Recompute the round offline and compare with its record (``wider query replay``). Writes nothing."""
        from ..search import replay

        p = proj()
        try:
            return replay.replay(p, round_no)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/rounds/{round_no}/provenance")
    def get_round_provenance(round_no: int):
        """The round's decisions, model calls and outside requests (``wider query provenance``, which also writes
        the file). Writes nothing."""
        from ..search import provenance

        p = proj()
        try:
            return provenance.view(p, round_no)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/query/rounds/{round_no}/accept")
    def get_round_accept(round_no: int):
        """What accepting the round would state (the confirm step; ``wider query accept --check``). Reads only."""
        from ..search import rounds

        p = proj()
        try:
            return rounds.accept_plan(p, round_no)
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.post("/api/query/rounds/{round_no}/accept")
    def post_round_accept(round_no: int):
        """Accept the round, as ``wider query accept``; the loss is stated and recorded, never refused. Undoable."""
        from ..core.journal import last_change_id
        from ..search import rounds

        p = proj()
        try:
            return {**rounds.accept(p, round_no), "change_id": last_change_id()}
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()
