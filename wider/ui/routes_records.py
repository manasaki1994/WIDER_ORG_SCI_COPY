"""Record drawer routes: one record with its notes and tags, note and tag edits, annotation search, and the
two-reviewer comparison the adjudication drawer reads. Registered once from ``app.py``."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..core import annotations as ann
from ..core.journal import last_change_id

RECORD_FIELDS = "record_id, title, authors, year, journal, doi, url, abstract, abstract_words, abstract_source, platforms"


class NoteBody(BaseModel):
    author: str
    text: str


class TagBody(BaseModel):
    author: str
    tag: str


def register(app: FastAPI, P) -> None:
    def guarded(fn):
        try:
            return fn()
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'\"")) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    def need_name(author: str | None, blind: bool) -> None:
        if blind and not (author or "").strip():
            raise HTTPException(400, "blind mode needs the reviewer's name")

    @app.get("/api/records/{record_id}")
    def get_record(record_id: int, author: str | None = None, blind: bool = False):
        """Bibliographic fields and abstract (never rank, score, cell or sample) plus notes and tags; in blind mode
        only ``author``'s own notes and tags."""
        need_name(author, blind)
        p = P()
        try:
            r = p.store.one(f"SELECT {RECORD_FIELDS} FROM records WHERE record_id=?", (record_id,))
            if r is None:
                raise HTTPException(404, f"no unique record {record_id}")
            from ..enrich.abstracts import versions
            vs = [{k: v[k] for k in ("version", "source_word", "words", "at")} for v in versions(p.store, record_id)]
            return {"record": {**dict(r), "abstract_versions": vs}, **ann.for_record(p, record_id, author, blind)}
        finally:
            p.close()

    @app.get("/api/records/{record_id}/abstracts")
    def get_record_abstracts(record_id: int):
        """Every abstract the record has had (`wider enrich --versions`); 404 for an unknown record."""
        from ..enrich.abstracts import record_versions

        p = P()
        try:
            return guarded(lambda: record_versions(p, record_id))
        finally:
            p.close()

    @app.post("/api/records/{record_id}/notes")
    def post_note(record_id: int, body: NoteBody, blind: bool = False):
        """Save a note; the answer lists the record's notes and tags (in blind mode only the author's own)."""
        need_name(body.author, blind)
        p = P()
        try:
            note_id = guarded(lambda: ann.add_note(p, record_id, body.author, body.text))
            return {"note_id": note_id, "change_id": last_change_id(), **ann.for_record(p, record_id, body.author.strip(), blind)}
        finally:
            p.close()

    @app.delete("/api/records/{record_id}/notes/{note_id}")
    def delete_note(record_id: int, note_id: int, author: str | None = None):
        """Delete one of ``author``'s own notes (400 without an author, 404 for anyone else's)."""
        p = P()
        try:
            if not guarded(lambda: ann.remove_note(p, note_id, author or "")):
                raise HTTPException(404, "no such note by this author")
            return {"ok": True, "change_id": last_change_id()}
        finally:
            p.close()

    @app.post("/api/records/{record_id}/tags")
    def post_tag(record_id: int, body: TagBody):
        p = P()
        try:
            tag = guarded(lambda: ann.add_tag(p, record_id, body.author, body.tag))
            return {"tag": tag, "change_id": last_change_id()}
        finally:
            p.close()

    @app.delete("/api/records/{record_id}/tags/{tag}")
    def delete_tag(record_id: int, tag: str, author: str | None = None):
        """Remove ``author``'s own tag (400 without an author); other reviewers' identical tags stay."""
        p = P()
        try:
            n = guarded(lambda: ann.remove_tag(p, record_id, tag, author or ""))
            if not n:
                raise HTTPException(404, "no such tag by this author")
            return {"ok": True, "change_id": last_change_id()}
        finally:
            p.close()

    @app.get("/api/annotations")
    def get_annotations(q: str | None = None, tag: str | None = None, author: str | None = None, blind: bool = False,
                        limit: int = 200):
        need_name(author, blind)
        p = P()
        try:
            return {"rows": guarded(lambda: ann.search(p, q, tag, author, limit, blind)), "tags": ann.all_tags(p, author, blind)}
        finally:
            p.close()

    @app.get("/api/annotations/summary")
    def get_annotation_summary(author: str | None = None, blind: bool = False):
        need_name(author, blind)
        p = P()
        try:
            return {str(k): v for k, v in ann.summary(p, author, blind).items()}
        finally:
            p.close()

    @app.get("/api/labels/compare")
    def get_label_compare(sample: str, a: str, b: str, all: bool = False):  # noqa: A002 - query parameter name
        """Both reviewers' labels side by side and any adjudication; disagreements only unless ``all``. Each side has
        decision, reason, ticks, tags, note, how it was made, blinded, left out and its abstract version, with no
        model output (`wider blinding disagreements`, reviewer-roles-and-blinding D9). Opened after both reviewers
        finished, for adjudication, so it is not blind."""
        from ..sample.package import disagreements

        p = P()
        try:
            return guarded(lambda: disagreements(p, sample, a, b, all))
        finally:
            p.close()
