"""Import-page routes: export sanity table and file upload. Registered once from ``app.py``."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..ingest.readers import INPUT_SUFFIXES

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_PLATFORM = re.compile(r"^[a-z][a-z0-9_]*$")


class IncludedBody(BaseModel):
    included: bool


class SampleBody(BaseModel):
    of: int | None = None


def safe_filename(name: str) -> str:
    base = Path(name or "export").name
    base = _SAFE_NAME.sub("_", base).strip(" .") or "export"
    return base


def register(app: FastAPI, P) -> None:
    """``P()`` returns a freshly loaded Project (the app's accessor)."""

    @app.get("/api/import/check")
    def get_import_check():
        from ..ingest.sanity import check_all

        p = P()
        try:
            return check_all(p)
        finally:
            p.close()

    @app.post("/api/import/upload")
    async def post_import_upload(platform: str = Form(...), file: UploadFile = File(...),
                                 seen_choice: str | None = Form(None)):
        from ..construct.artifacts import SeenChoiceNeeded
        from ..core.provenance import sha256_file
        from ..ingest.importer import add_file, same_export
        from ..ingest.sanity import check_file

        key = platform.strip().lower()
        if not _PLATFORM.match(key):
            raise HTTPException(400, "platform must be a lower-case key such as scopus or wos")
        name = safe_filename(file.filename or "")
        if Path(name).suffix.lower() not in INPUT_SUFFIXES:
            raise HTTPException(400, f"unsupported file type; use one of {', '.join(sorted(INPUT_SUFFIXES))}")
        p = P()
        src = None
        try:
            tmp = p.path("inputs", ".upload")
            tmp.mkdir(parents=True, exist_ok=True)
            src = tmp / name
            src.write_bytes(await file.read())
            same = same_export(p, src)
            notices: list[str] = []
            if same:  # identical bytes are already under inputs/: report the duplicate, save nothing, reopen nothing
                check = check_file(p, key, src, seen={sha256_file(src): f"{same[0]}/{same[1].name}"})
                check["saved"], check["path"] = False, str(same[1])
            else:  # never overwrites: a taken name gets a free one (name-2.csv); while final this reopens the uploads
                try:
                    dst = add_file(p, key, src, seen_choice=seen_choice, notices=notices)
                except SeenChoiceNeeded as e:   # nothing taken: the page asks the researcher first (D21, Q2)
                    raise HTTPException(400, e.notice) from e
                except ValueError as e:
                    raise HTTPException(400, str(e)) from e
                check = check_file(p, key, dst)
                check["saved"], check["renamed"] = True, dst.name != name
            check["notice"] = notices[0] if notices else None
            from ..core.stage_result import record
            from ..core.stage_summaries import summarize_upload
            res = record(p, summarize_upload(check))
            return {**check, "stage_result": res.as_dict()}
        finally:
            if src is not None:
                src.unlink(missing_ok=True)
            p.close()

    @app.get("/api/import/sources")
    def get_import_sources(file: str = "a new file"):
        """The imported files and the final mark; ``upload_choice``/``upload_notice``: whether adding ``file`` now
        needs the researcher's choice (D21, Q2) and the asking text the page shows before sending it."""
        from ..ingest.importer import list_sources, upload_plan

        p = P()
        try:
            plan = upload_plan(p, safe_filename(file) if file != "a new file" else file)
            return {**list_sources(p), "upload_choice": plan["choice"], "upload_notice": plan["asking"]}
        finally:
            p.close()

    @app.post("/api/import/final")
    def post_import_final():
        """Mark the uploads final (twin of `wider import final`); 409 when refused."""
        from ..ingest.importer import mark_final

        p = P()
        try:
            return mark_final(p)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        finally:
            p.close()

    @app.post("/api/import/sources/{source_id}/included")
    def post_import_included(source_id: int, body: IncludedBody):
        """Keep an imported file in deduplication or out of it (twin of `wider import include|exclude`); 404 for an
        unknown id, 409 when refused."""
        from ..ingest.importer import resolve_source, set_included

        p = P()
        try:
            try:
                resolve_source(p, source_id)
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
            try:
                return set_included(p, source_id, body.included)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
        finally:
            p.close()

    @app.post("/api/import/sources/{source_id}/sample")
    def post_import_sample(source_id: int, body: SampleBody):
        """Mark an imported file as a random sample of ``of`` results, or a complete export with ``of`` null (twin of
        `wider import sample --of N|--clear`); 404 for an unknown id, 400 when refused."""
        from ..ingest.importer import resolve_source, set_sample

        p = P()
        try:
            try:
                resolve_source(p, source_id)
            except ValueError as e:
                raise HTTPException(404, str(e)) from e
            try:
                return set_sample(p, source_id, body.of)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
        finally:
            p.close()
