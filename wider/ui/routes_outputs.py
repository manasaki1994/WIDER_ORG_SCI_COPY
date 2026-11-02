"""Output routes: search-strings archive, PRISMA 2020 diagram, methods paragraph, reference exports, replication
package, and a download route restricted to the project's exports folder."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel


class MethodsBody(BaseModel):
    text: str


def register(app: FastAPI, P) -> None:
    def guarded(p, fn):
        from ..screen.devgate import DevSampleIncomplete

        try:
            return fn()
        except DevSampleIncomplete:
            raise                      # 409 with the counts (app.py's handler)
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e).strip("'\"")[:400]) from e
        finally:
            p.close()

    @app.get("/api/query/archive")
    def get_archive():
        from ..search.archive import entries

        p = P()
        return guarded(p, lambda: {"entries": entries(p)})

    @app.post("/api/query/archive")
    def post_archive(version: int | None = None):
        from ..search.archive import archive

        p = P()
        return guarded(p, lambda: archive(p, version, reason="requested"))

    @app.post("/api/outputs/prisma")
    def post_prisma(protocol: str | None = None, result_set: int | None = None):
        from ..recall.outputs import write_prisma

        p = P()
        return guarded(p, lambda: write_prisma(p, protocol, result_set))

    @app.post("/api/outputs/prisma-s")
    def post_prisma_s():
        from ..recall.prisma_s import write

        p = P()
        return guarded(p, lambda: write(p))

    @app.get("/api/outputs/validity")
    def get_validity():
        """The validity note (D3) as the report, the methods draft and the package give it; None without a benchmark."""
        from ..search.coverage import validity_lines, validity_note

        p = P()

        def note():
            if not p.read_benchmark():
                return {"note": None, "text": []}
            n = validity_note(p)
            return {"note": n, "text": validity_lines(n)}
        return guarded(p, note)

    @app.get("/api/outputs/methods")
    def get_methods(protocol: str | None = None):
        from ..recall.outputs import METHODS_FILE, read_methods

        p = P()
        return guarded(p, lambda: {"text": read_methods(p), "file": METHODS_FILE})

    @app.get("/api/outputs/methods/draft")
    def get_methods_draft(protocol: str | None = None, result_set: int | None = None, choice: str | None = None):
        """The draft; a recall set waiting for the D8 choice gives its pending line and ``choice_needed`` (200)."""
        from ..recall.outputs import methods_draft

        p = P()
        pending: dict = {}
        return guarded(p, lambda: {"draft": methods_draft(p, protocol, result_set, choice=choice, pending=pending),
                                   **pending})

    @app.post("/api/outputs/methods")
    def post_methods(body: MethodsBody):
        from ..recall.outputs import save_methods

        if not body.text.strip():
            raise HTTPException(400, "the methods text is empty")
        p = P()
        return guarded(p, lambda: {"file": save_methods(p, body.text)})

    @app.post("/api/outputs/references")
    def post_references(protocol: str | None = None, which: str | None = None, result_set: int | None = None,
                        all: bool = False):  # noqa: A002 - the query parameter is `all=true`
        from ..recall.outputs import export_all_references, export_references

        p = P()
        if all:
            if which is not None:
                p.close()
                raise HTTPException(400, "all=true writes every decision set; leave out which")
            return guarded(p, lambda: export_all_references(p, protocol, result_set))
        return guarded(p, lambda: export_references(p, protocol, which or "include", result_set))

    @app.post("/api/outputs/corpus")
    def post_corpus():
        """``wider export references --corpus``: every unique record, bibliographic data only (ui-redesign D4)."""
        from ..recall.outputs import export_corpus

        p = P()
        return guarded(p, lambda: export_corpus(p))

    def _selection(protocol, result_set, preset, pathway, pattern, final_only, records, format, abstracts,  # noqa: A002
                   dry_run, audit=False, package=False, add=None):
        from ..recall.selection import run_selection

        p = P()
        return guarded(p, lambda: run_selection(p, protocol or None, result_set, preset or None, pathway or None,
                                                pattern or None, final_only, records or None, format, abstracts,
                                                dry_run, audit, package, add or None))

    @app.get("/api/outputs/selection")
    def get_selection(protocol: str | None = None, result_set: int | None = None, preset: str | None = None,
                      pathway: str | None = None, pattern: str | None = None, final_only: bool = False,
                      records: str | None = None, format: str | None = None,  # noqa: A002 - the query parameter
                      abstracts: bool = True, package: bool = False, add: str | None = None):
        """The count, file name and records of a selection (design D5); writes nothing and records nothing. While the
        exports wait for the development sample the answer is that sentence (``ready`` false), not a 409: the Results
        page asks on load, and only writing is refused."""
        from ..screen.devgate import DevSampleIncomplete

        try:
            return _selection(protocol, result_set, preset, pathway, pattern, final_only, records, format, abstracts,
                              True, package=package, add=add)
        except DevSampleIncomplete as e:
            return {"ready": False, "n": 0, "message": str(e), "records": [], "files": [], "waiting": e.counts}

    @app.post("/api/outputs/selection")
    def post_selection(protocol: str | None = None, result_set: int | None = None, preset: str | None = None,
                       pathway: str | None = None, pattern: str | None = None, final_only: bool = False,
                       records: str | None = None, format: str | None = None,  # noqa: A002 - the query parameter
                       abstracts: bool = True, audit: bool = False, package: bool = False, add: str | None = None):
        return _selection(protocol, result_set, preset, pathway, pattern, final_only, records, format, abstracts,
                          False, audit, package, add)

    @app.post("/api/outputs/prisma-checklist")
    def post_prisma_checklist(protocol: str | None = None, result_set: int | None = None):
        from ..recall.prisma_2020 import write

        p = P()
        return guarded(p, lambda: write(p, protocol, result_set))

    @app.post("/api/outputs/package")
    def post_package(protocol: str | None = None, sample: str | None = None, result_set: int | None = None,
                     choice: str | None = None):
        from ..recall.outputs import write_package

        p = P()
        return guarded(p, lambda: write_package(p, protocol, sample, result_set, choice=choice))

    @app.get("/api/results")
    def get_results(protocol: str | None = None, result_set: int | None = None, sample: str | None = None):
        """The Results dashboard of one set (``wider results``); reads only: no look, no active set changed."""
        from ..recall.dashboard import dashboard
        from ..sample.labels import ui_wording

        p = P()
        return guarded(p, lambda: ui_wording(dashboard(p, protocol or None, result_set, sample or None)))

    @app.post("/api/screen/adjudication")
    async def post_screen_adjudication(file: UploadFile = File(...), protocol: str = Form(...),
                                       adjudicator: str = Form(""), column: str = Form("adjudication"),
                                       result_set: int | None = Form(None), dry_run: bool = Form(False)):
        """Import an adjudicator's decisions for some records (partial-adjudication-import D13): the file is read in
        memory and never saved; the History author is the page's name."""
        from ..screen.adjudication import import_adjudication

        data = await file.read()
        p = P()
        return guarded(p, lambda: import_adjudication(p, protocol, data, file.filename or "the file",
                                                      adjudicator=adjudicator.strip() or None, column=column,
                                                      result_set=result_set, dry_run=dry_run))

    @app.get("/api/outputs/file")
    def get_output_file(path: str):
        """Download a file the tool wrote, only from <project>/exports (never anything else on the computer)."""
        p = P()
        try:
            base = p.path("exports").resolve()
            f = (p.root / path).resolve()
            if not f.is_relative_to(base) or not f.is_file():
                raise HTTPException(404, "no such file in the exports folder")
            from ..screen import devgate

            if devgate.file_waits(p, f.relative_to(p.root.resolve()).as_posix()):
                devgate.check_export(p)        # 409: exports with model decisions wait (ui-redesign D4)
            return FileResponse(f, filename=Path(f).name)
        finally:
            p.close()
