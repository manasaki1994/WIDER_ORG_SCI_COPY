"""Seed papers' full texts (vision change 2b): the PDF per seed (Setup page, "Full text" column), extraction, name
mining, the candidate names with their admission, the name map and the name-by-venue table for streams (Construct
workbench, "Names from seed papers" card). The CLI twin is ``wider/cli_seeds.py``; both call ``wider/seeds/*``."""
from __future__ import annotations

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel


class ExtractBody(BaseModel):
    benchmark_id: str | None = None


class MineBody(BaseModel):
    benchmark_id: str | None = None
    model: bool = True


class StreamLabelBody(BaseModel):
    venue: str
    stream: str = ""
    author: str | None = None


class PolicyBody(BaseModel):
    passages_to_cloud: bool | None = None
    passage_tokens: int | None = None
    grobid_url: str | None = None   # a server on this computer, or "off"


class AdmitBody(BaseModel):
    name: str
    block: str | None = None
    note: str | None = None
    author: str | None = None
    seen_choice: str | None = None   # "count" | "keep" when the save reopens the query (unlock-seen-choice D6)


def register(app: FastAPI, proj) -> None:
    def _run(fn):
        from ..construct.artifacts import SeenChoiceNeeded

        p = proj()
        try:
            return fn(p)
        except SeenChoiceNeeded as e:   # nothing saved: the page asks the researcher first (unlock-seen-choice D6)
            raise HTTPException(400, e.notice)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\""))
        except ValueError as e:
            raise HTTPException(400, str(e)[:400])
        finally:
            p.close()

    @app.get("/api/seeds/files")
    def get_seed_files():
        """Per seed paper: whether a PDF is kept in ``seeds/``, its hash, and whether its text was extracted."""
        from ..seeds import pdf
        return _run(lambda p: {"files": pdf.list_files(p)})

    @app.post("/api/seeds/files/{benchmark_id}")
    async def post_seed_file(benchmark_id: str, file: UploadFile = File(...)):
        """Keep a PDF for one seed paper in ``seeds/<id>.pdf``; a file that is not a PDF is refused."""
        from ..seeds import pdf
        data = await file.read()

        def go(p):
            pdf._check_id(p, benchmark_id)
            tmp = pdf._dir(p) / f".upload-{benchmark_id}"
            try:
                tmp.write_bytes(data)
                return pdf.add(p, benchmark_id, tmp)
            finally:
                tmp.unlink(missing_ok=True)
        return _run(go)

    @app.delete("/api/seeds/files/{benchmark_id}")
    def delete_seed_file(benchmark_id: str):
        from ..seeds import pdf
        return _run(lambda p: pdf.remove(p, benchmark_id))

    @app.post("/api/seeds/extract")
    def post_seed_extract(body: ExtractBody):
        """Extract the text of every seed PDF (or one) into ``runs/seeds/<id>/``."""
        from ..seeds import pdf
        return _run(lambda p: {"seeds": pdf.extract_all(p, body.benchmark_id)})

    @app.get("/api/seeds/policy")
    def get_seed_policy():
        """What the model pass may send (``wider seeds policy``): the switch, the passage size, the backends allowed
        passages. No request leaves the computer."""
        from ..seeds import policy
        return _run(policy.status)

    @app.post("/api/seeds/policy")
    def post_seed_policy(body: PolicyBody):
        """Change the passages switch, the passage size and/or the GROBID server (``wider seeds policy
        passages-to-cloud on|off``, ``wider seeds policy grobid-url URL|off``)."""
        from ..seeds import policy
        return _run(lambda p: policy.set_policy(p, body.passages_to_cloud, body.passage_tokens, body.grobid_url))

    @app.post("/api/seeds/mine")
    def post_seed_mine(body: MineBody):
        """Mine candidate names (``model: false`` is ``wider seeds mine --no-model``)."""
        from ..seeds import mine
        return _run(lambda p: mine.mine(p, body.benchmark_id, model=body.model))

    @app.get("/api/seeds/candidates")
    def get_seed_candidates():
        """The newest mining run's candidate names."""
        from ..seeds import mine
        return _run(lambda p: {"candidates": mine.candidates(p)})

    @app.get("/api/seeds/streams")
    def get_seed_streams():
        """The name-by-venue table of the seeds' reference lists, with the stream labels."""
        from ..seeds import streams
        return _run(streams.table)

    @app.post("/api/seeds/streams/label")
    def post_seed_stream_label(body: StreamLabelBody):
        """Label a venue as a stream (``wider seeds streams --label VENUE STREAM``); journalled, undoable."""
        from ..seeds import streams
        return _run(lambda p: streams.label(p, body.venue, body.stream, body.author))

    @app.get("/api/seeds/names")
    def get_seed_names():
        """The mined names with their admission state and gain (``wider seeds names``), and the name map."""
        from ..seeds import names
        return _run(lambda p: {**names.listing(p), "map": names.name_map(p)})

    @app.post("/api/seeds/names/admit")
    def post_seed_name_admit(body: AdmitBody):
        """Add a name to a block (``wider seeds names --admit NAME --block K --note TEXT``); journalled."""
        from ..construct.artifacts import save_seen_choice
        from ..seeds import names

        def go(p):
            with save_seen_choice(body.seen_choice):
                return names.admit(p, body.name, body.block, body.note, body.author, confirm_revision=True)
        return _run(go)

    @app.post("/api/seeds/names/map")
    def post_seed_name_map():
        """Write the name map files into the mining run folder (``wider seeds names --map``)."""
        from ..seeds import names
        return _run(lambda p: {k: v for k, v in names.write_map(p).items() if k != "svg"})
