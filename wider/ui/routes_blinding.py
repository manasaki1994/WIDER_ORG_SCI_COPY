"""Review package routes (reviewer-roles-and-blinding): the make dialog's preview, making a package, its kept file,
importing a decisions file, the status and withdrawing. Twins of `wider blinding ...`; the POSTs sign with the
page's author header."""
from __future__ import annotations

import json

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel


class PackageBody(BaseModel):
    reviewer: str = ""
    samples: list[str] = []
    mode: str = ""
    earlier: str | None = None
    codebook_version: int | None = None


class WithdrawBody(BaseModel):
    reason: str = ""


def register(app: FastAPI, P) -> None:
    from ..sample import package as pk

    def signed() -> None:
        """A package made, imported or withdrawn is signed with the page's name (risk-checker R3, like a sample lock)."""
        from ..core.journal import AUTHOR
        if not (AUTHOR.get() or "").strip():
            raise HTTPException(400, "a reviewer name is needed: a review package change is signed with your name")

    def guarded(p, fn):
        try:
            return fn()
        except pk.PackageError as e:
            raise HTTPException(e.status, str(e)) from e
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]) if e.args else str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            p.close()

    @app.post("/api/blinding/package/preview")
    def post_package_preview(body: PackageBody):
        """What the make dialog shows above "Download": the leaving sentence, earlier labels, codebook versions."""
        p = P()
        return guarded(p, lambda: pk.preview(p, body.reviewer, body.samples, body.mode, body.earlier, body.codebook_version))

    @app.post("/api/blinding/package")
    def post_package(body: PackageBody):
        """Make the package (`wider blinding package`); the page then downloads it from .../file."""
        signed()
        p = P()

        def go():
            res = pk.make(p, body.reviewer, body.samples, body.mode, body.earlier, body.codebook_version)
            return {k: res[k] for k in ("package_id", "reviewer", "n_items", "leaving", "notes", "download_name",
                                        "change_id", "codebook_version")}
        return guarded(p, go)

    @app.get("/api/blinding/package/{package_id}/file")
    def get_package_file(package_id: str):
        """The kept copy of the page as an attachment, byte for byte (`wider blinding package-file`)."""
        p = P()

        def go():
            path = pk.package_file(p, package_id)
            name = pk.download_name(pk._row(p, package_id))
            return FileResponse(path, media_type="text/html", filename=name)
        return guarded(p, go)

    @app.post("/api/blinding/import")
    async def post_import(file: UploadFile = File(...)):
        """Import a reviewer's decisions file (`wider blinding import`)."""
        signed()
        raw = await file.read()
        p = P()

        def go():
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as e:
                raise pk.PackageError(pk.UNREADABLE) from e
            return pk.import_file(p, data=data)
        return guarded(p, go)

    @app.get("/api/blinding/status")
    def get_status():
        """Every package and the two texts (`wider blinding status --json`)."""
        p = P()
        return guarded(p, lambda: pk.status(p))

    @app.post("/api/blinding/package/{package_id}/withdraw")
    def post_withdraw(package_id: str, body: WithdrawBody):
        signed()
        p = P()

        def go():
            res = pk.withdraw(p, package_id, body.reason)
            return {"package_id": res["package_id"], "withdrawn_at": res["withdrawn_at"],
                    "withdraw_reason": res["withdraw_reason"], "change_id": res["change_id"]}
        return guarded(p, go)
