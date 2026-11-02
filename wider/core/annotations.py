"""Notes and tags a reviewer attaches to a unique record.

Used by the record drawer in the UI and by `wider notes`. Every note and tag carries its author. In blind labelling
the drawer shows only the reader's own notes and tags (``author`` + ``blind``), so another reviewer's opinion cannot
leak through a note. When deduplication merges two records, their notes and tags move to the surviving record.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .provenance import utcnow

if TYPE_CHECKING:
    from .project import Project

MAX_NOTE = 2000
MAX_TAG = 40
_TAG_OK = re.compile(r"^[^\W_][\w.-]*$")  # letters and digits of any script, then also "_", "-", "."


def norm_tag(tag: str) -> str:
    """Lower case, spaces to hyphens; letters, digits, '-', '_' and '.' only."""
    t = re.sub(r"\s+", "-", (tag or "").strip().lower()).strip("-")
    if not t or len(t) > MAX_TAG or not _TAG_OK.match(t):
        raise ValueError(f"a tag is 1-{MAX_TAG} characters of letters, digits, '-', '_' or '.', starting with a letter or digit (got {tag!r})")
    return t


MAX_AUTHOR = 80


def _author(author: str) -> str:
    a = (author or "").strip()
    if not a:
        raise ValueError("give your name (author) so the note or tag can be attributed")
    return a[:MAX_AUTHOR]


def _require_record(project: Project, record_id: int) -> dict:
    r = project.store.one("SELECT record_id, title FROM records WHERE record_id=?", (record_id,))
    if r is None:
        raise KeyError(f"no unique record {record_id}")
    return dict(r)


def add_note(project: Project, record_id: int, author: str, text: str) -> int:
    _require_record(project, record_id)
    text = (text or "").strip()
    if not text:
        raise ValueError("the note is empty")
    if len(text) > MAX_NOTE:
        raise ValueError(f"a note holds at most {MAX_NOTE} characters")
    from .journal import recording
    with recording(project, "note", f"{_author(author)} added a note to record {record_id}", author=author, record_id=record_id) as rec:
        rec.rows("record_notes", "record_id=? AND author=?", (record_id, _author(author)))
        cur = project.store.execute("INSERT INTO record_notes(record_id,author,text,created_at) VALUES(?,?,?,?)",
                                    (record_id, _author(author), text, utcnow()))
    return int(cur.lastrowid)


def remove_note(project: Project, note_id: int, author: str) -> bool:
    """Delete one of ``author``'s own notes; False when ``author`` has no note ``note_id``."""
    a = _author(author)
    from .journal import recording
    row = project.store.one("SELECT record_id FROM record_notes WHERE note_id=? AND author=?", (note_id, a))
    with recording(project, "note", f"{a} deleted note {note_id}", author=a, record_id=row["record_id"] if row else None) as rec:
        rec.rows("record_notes", "note_id=?", (note_id,))
        cur = project.store.execute("DELETE FROM record_notes WHERE note_id=? AND author=?", (note_id, a))
    return cur.rowcount > 0


def add_tag(project: Project, record_id: int, author: str, tag: str) -> str:
    _require_record(project, record_id)
    t = norm_tag(tag)
    from .journal import recording
    with recording(project, "tag", f"{_author(author)} tagged record {record_id} {t}", author=author, record_id=record_id) as rec:
        rec.rows("record_tags", "record_id=? AND tag=? AND author=?", (record_id, t, _author(author)))
        project.store.execute("INSERT OR IGNORE INTO record_tags(record_id,tag,author,created_at) VALUES(?,?,?,?)",
                              (record_id, t, _author(author), utcnow()))
    return t


def remove_tag(project: Project, record_id: int, tag: str, author: str) -> int:
    """Remove ``author``'s own tag from a record (other reviewers' identical tags stay); returns the number removed."""
    a = _author(author)
    t = norm_tag(tag)
    from .journal import recording
    with recording(project, "tag", f"{a} removed tag {t} from record {record_id}", author=a, record_id=record_id) as rec:
        rec.rows("record_tags", "record_id=? AND tag=? AND author=?", (record_id, t, a))
        cur = project.store.execute("DELETE FROM record_tags WHERE record_id=? AND tag=? AND author=?", (record_id, t, a))
    return cur.rowcount


def _visible(author: str | None, blind: bool) -> tuple[str, tuple]:
    if blind:
        # the same cut as when saving, so a long name still finds its own notes and tags in blind mode
        return " AND author=?", ((author or "").strip()[:MAX_AUTHOR],)
    return "", ()


def for_record(project: Project, record_id: int, author: str | None = None, blind: bool = False) -> dict[str, Any]:
    cond, extra = _visible(author, blind)
    st = project.store
    notes = [dict(r) for r in st.query(f"SELECT note_id, author, text, created_at FROM record_notes WHERE record_id=?{cond} "
                                       "ORDER BY created_at, note_id", (record_id, *extra))]
    tags = [dict(r) for r in st.query(f"SELECT tag, author, created_at FROM record_tags WHERE record_id=?{cond} ORDER BY tag, author",
                                      (record_id, *extra))]
    return {"notes": notes, "tags": tags}


def summary(project: Project, author: str | None = None, blind: bool = False) -> dict[int, dict[str, Any]]:
    """Per record: distinct tags, number of notes, and the notes' text (for searching tables in the UI)."""
    cond, extra = _visible(author, blind)
    out: dict[int, dict[str, Any]] = {}
    for r in project.store.query(f"SELECT record_id, tag FROM record_tags WHERE 1=1{cond} ORDER BY tag", extra):
        d = out.setdefault(r["record_id"], {"tags": [], "n_notes": 0, "text": ""})
        if r["tag"] not in d["tags"]:
            d["tags"].append(r["tag"])
    for r in project.store.query(f"SELECT record_id, text FROM record_notes WHERE 1=1{cond} ORDER BY note_id", extra):
        d = out.setdefault(r["record_id"], {"tags": [], "n_notes": 0, "text": ""})
        d["n_notes"] += 1
        d["text"] = (d["text"] + " " + r["text"]).strip()
    return out


def search(project: Project, q: str | None = None, tag: str | None = None, author: str | None = None,
           limit: int = 200, blind: bool = False) -> list[dict[str, Any]]:
    """Records with notes or tags, filtered by words in a note or the title (``q``), a tag and an author. ``blind``
    matches and returns only ``author``'s own notes and tags."""
    if blind:
        author = _author(author)
    st = project.store
    want_tag = norm_tag(tag) if tag else None
    terms = [w for w in (q or "").lower().split() if w]
    summ = summary(project, author, blind)
    ids = sorted(summ)
    if not ids:
        return []
    rows = []
    titles = {r["record_id"]: dict(r) for r in st.query(
        f"SELECT record_id, title, year, journal FROM records WHERE record_id IN ({','.join('?' * len(ids))})", ids)}
    for rid in ids:
        ann = for_record(project, rid, author, blind)
        if author:
            a = author.strip()
            ann = {"notes": [n for n in ann["notes"] if n["author"] == a], "tags": [t for t in ann["tags"] if t["author"] == a]}
            if not ann["notes"] and not ann["tags"]:
                continue
        if want_tag and want_tag not in {t["tag"] for t in ann["tags"]}:
            continue
        rec = titles.get(rid, {"record_id": rid, "title": None, "year": None, "journal": None})
        hay = " ".join([rec.get("title") or "", *(n["text"] for n in ann["notes"]), *(t["tag"] for t in ann["tags"])]).lower()
        if terms and not all(w in hay for w in terms):
            continue
        rows.append({**rec, **ann})
        if len(rows) >= limit:
            break
    return rows


def all_tags(project: Project, author: str | None = None, blind: bool = False) -> list[dict[str, Any]]:
    """Tags in use with their record counts; in blind mode only ``author``'s own tags."""
    cond, extra = _visible(author, blind)
    return [dict(r) for r in project.store.query(f"SELECT tag, COUNT(DISTINCT record_id) n FROM record_tags WHERE 1=1{cond} "
                                                 "GROUP BY tag ORDER BY n DESC, tag", extra)]
