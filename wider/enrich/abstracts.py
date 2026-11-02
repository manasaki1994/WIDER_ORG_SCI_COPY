"""Every abstract a record has had, numbered (reviewer-roles-and-blinding D8).

Version 1 is the imported abstract; a fill, an abstract pasted as an answer and a dedup re-run that takes a longer
export abstract each add one. ``records.abstract`` stays the current abstract and always equals the latest version.
Model calls and labels store the version they read (``abstract_version``), so a comparison can say when two judgments
read different text. ``db`` is the store or the connection of a running transaction (both have ``execute``).
"""
from __future__ import annotations

from typing import Any

from ..core.provenance import utcnow
from ..core.text import word_count

# the plain word a reader sees for each source (the review page's mark, the drawer, the CLI)
SOURCE_WORDS = {"imported": "imported", "export": "export", "researcher": "pasted by the researcher",
                "openalex": "OpenAlex", "crossref": "Crossref", "europepmc": "Europe PMC",
                "semantic_scholar": "Semantic Scholar", "openalex_title": "OpenAlex"}
NOT_RECORDED = "abstract version not recorded"


def source_word(source: str | None) -> str:
    return SOURCE_WORDS.get(source or "", source or "unknown")


def _latest(db: Any, record_id: int):
    return db.execute("SELECT * FROM record_abstracts WHERE record_id=? ORDER BY version DESC LIMIT 1",
                      (record_id,)).fetchone()


def current_version(db: Any, record_id: int) -> int | None:
    """The record's latest version number, or None when it has none (a record written outside deduplication)."""
    r = _latest(db, record_id)
    return int(r["version"]) if r else None


def add_version(db: Any, record_id: int, text: str | None, source: str, at: str | None = None) -> int | None:
    """Add ``text`` as the record's next version; nothing when it equals the latest. Returns the version now
    current."""
    text = text or ""
    last = _latest(db, record_id)
    if last is not None and (last["abstract"] or "") == text:
        return int(last["version"])
    v = (int(last["version"]) if last else 0) + 1
    db.execute("INSERT INTO record_abstracts(record_id,version,abstract,source,words,at) VALUES(?,?,?,?,?,?)",
               (record_id, v, text, source, word_count(text), at or utcnow()))
    return v


def move_on_merge(db: Any, old_id: int, new_id: int) -> dict[int, int]:
    """A dedup merge (D8): the merged-away record's versions follow the survivor's, numbered after its last one.
    Returns the old-to-new version map, which the caller applies to the rows it moved with them."""
    base = current_version(db, new_id) or 0
    olds = [int(r[0]) for r in db.execute("SELECT version FROM record_abstracts WHERE record_id=? ORDER BY version",
                                          (old_id,)).fetchall()]
    mapping = {v: v + base for v in olds}
    for v in reversed(olds):   # the primary key (record_id, version): the new numbers never clash with the survivor's
        db.execute("UPDATE record_abstracts SET record_id=?, version=? WHERE record_id=? AND version=?",
                   (new_id, mapping[v], old_id, v))
    return mapping


def versions(db: Any, record_id: int) -> list[dict]:
    """The record's versions, oldest first: version, source (as stored), source word, words, at, abstract."""
    return [{**dict(r), "source_word": source_word(r["source"])} for r in db.execute(
        "SELECT version, source, words, at, abstract FROM record_abstracts WHERE record_id=? ORDER BY version",
        (record_id,)).fetchall()]


def describe(db: Any, record_id: int, version: int | None) -> str:
    """One version as a short phrase ("imported, 80 words"), or "abstract version not recorded"."""
    if version is None:
        return NOT_RECORDED
    r = db.execute("SELECT source, words FROM record_abstracts WHERE record_id=? AND version=?",
                   (record_id, version)).fetchone()
    if r is None:
        return NOT_RECORDED
    return f"{source_word(r['source'])}, {r['words'] or 0} words"


def record_versions(project, record_id: int) -> dict:
    """What `wider enrich --versions` and GET /api/records/{id}/abstracts show: the versions and the current one.
    KeyError "no record N" for an id that is neither a record nor has versions (a dropped record keeps them)."""
    st = project.store
    vs = versions(st, record_id)
    if not vs and st.one("SELECT 1 FROM records WHERE record_id=?", (record_id,)) is None:
        raise KeyError(f"no record {record_id}")
    return {"record_id": record_id, "versions": vs, "current": vs[-1]["version"] if vs else None}
