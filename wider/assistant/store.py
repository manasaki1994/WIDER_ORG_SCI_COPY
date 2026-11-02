"""Ask WIDER's conversations (guide-assistant D14, D16): one JSON line per turn in ``assistant/<slug>-<hash6>.jsonl``
of the project folder, one file per author, written with mode 0600. This is not History: a conversation is not a
project change, and forgetting it cannot be undone, so the surfaces ask first."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.project import Project
from .sections import section_hash

GITIGNORE_LINE = "assistant/"
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def name_key(author: str) -> str:
    """The name a conversation belongs to, ignoring case and spacing: "Reviewer", "reviewer" and " reviewer " are one person
    (M124; the name field is typed again on each computer and browser)."""
    return " ".join(author.split()).casefold()


def conversation_path(project: Project, author: str) -> Path:
    """``assistant/<slug>-<hash6>.jsonl``: a filesystem-safe form of the name and 6 hex characters of the SHA-256 of
    `name_key`, so "Ana" and "ana" share a file and "Ana" and "Bo" do not."""
    key = name_key(author)
    slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-")[:40] or "author"
    return project.path("assistant", f"{slug}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:6]}.jsonl")


def _rows(f: Path) -> list[dict[str, Any]]:
    out = []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if isinstance(t, dict):
            out.append(t)
    return out


def _adopt(project: Project, author: str) -> Path:
    """Before M124 the file was keyed by the exact name, so "Reviewer" and "reviewer" had a file each. Fold such files into
    this name's file, ordered by when each turn was asked. A file is taken only when its turns say they were asked
    by a name with the same `name_key`, never because its file name looks alike."""
    f = conversation_path(project, author)
    key = name_key(author)
    if not key or not f.parent.is_dir():
        return f
    slug = f.name.rsplit("-", 1)[0]
    others = [g for g in f.parent.glob(f"{glob.escape(slug)}-*.jsonl") if g != f
              and {name_key(str(r.get("author", ""))) for r in _rows(g)} == {key}]
    if not others:
        return f
    with _locked(f):
        rows = sorted(_rows(f) + [r for g in others for r in _rows(g)], key=lambda r: str(r.get("at", "")))
        _write(f, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), append=False)
        for g in others:
            g.unlink(missing_ok=True)
            Path(str(g) + ".lock").unlink(missing_ok=True)
    return f


def ensure_gitignore(project: Project) -> None:
    """Append ``assistant/`` to the project's existing ``.gitignore`` when it lacks the line, keeping its other lines;
    a project without a ``.gitignore`` gets none (D16)."""
    gi = project.path(".gitignore")
    if not gi.exists():
        return
    text = gi.read_text(encoding="utf-8")
    if GITIGNORE_LINE in (ln.strip() for ln in text.splitlines()):
        return
    gi.write_text(text + ("" if not text or text.endswith("\n") else "\n") + GITIGNORE_LINE + "\n", encoding="utf-8")


def turns(project: Project, author: str) -> list[dict[str, Any]]:
    """This author's turns in file order, each with ``n`` (from 1); a line that cannot be read is skipped."""
    if not name_key(author):
        return []
    return _numbered(_adopt(project, author))


def _numbered(f: Path) -> list[dict[str, Any]]:
    return [{**t, "n": i} for i, t in enumerate(_rows(f), 1)]


def _write(f: Path, text: str, append: bool) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(f, flags, 0o600)
    try:
        os.write(fd, text.encode("utf-8"))
    finally:
        os.close(fd)
    os.chmod(f, 0o600)


@contextmanager
def _locked(f: Path):
    """One writer at a time per conversation file: a lock per file in this process, and an advisory lock on
    ``<file>.lock`` against a CLI beside the UI (POSIX; elsewhere the in-process lock only). A rewrite ("Didn't help")
    and an append or a forget never interleave, so no turn is lost."""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(f), threading.Lock())
    with lock:
        try:
            import fcntl
        except ImportError:                    # Windows
            yield
            return
        f.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(f) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)                       # closing releases the lock


def _inode(f: Path) -> int | None:
    try:
        return f.stat().st_ino
    except (OSError, ValueError):
        return None


def started(project: Project, author: str) -> int | None:
    """What `append` compares with to see that the conversation was forgotten while a question ran: the file's
    inode, or None when there is no conversation yet."""
    return _inode(_adopt(project, author))


def append(project: Project, author: str, turn: dict[str, Any], since: int | None = None) -> int:
    """Store one finished turn; returns its number. With ``since`` (`started` when the question began): when the
    conversation was forgotten meanwhile, the turn is not stored and 0 is returned, so a forget is not undone by a
    question that was running."""
    ensure_gitignore(project)
    row = {k: v for k, v in turn.items() if k != "n"}
    f = _adopt(project, author)
    with _locked(f):
        if since is not None and _inode(f) != since:
            return 0
        _write(f, json.dumps(row, ensure_ascii=False) + "\n", append=True)
        return len(_rows(f))


def forget(project: Project, author: str) -> int:
    """Delete this author's conversation (asked first on both surfaces); returns how many turns it held."""
    f = _adopt(project, author)
    with _locked(f):
        n = len(_rows(f))
        if f.exists():
            f.unlink()
    return n


def mark_unhelpful(project: Project, author: str, n: int, value: bool = True) -> dict[str, Any]:
    """Mark turn ``n`` "Didn't help" (rewrites the file under the conversation's lock); ``KeyError`` for a turn that
    does not exist."""
    f = _adopt(project, author)
    with _locked(f):
        rows = _numbered(f)
        if not 1 <= n <= len(rows):
            raise KeyError(f"there is no turn {n} in this conversation")
        rows[n - 1]["unhelpful"] = bool(value)
        _write(f, "".join(json.dumps({k: v for k, v in r.items() if k != "n"}, ensure_ascii=False) + "\n"
                          for r in rows), append=False)
    return rows[n - 1]


def unanswered(project: Project, author: str) -> list[dict[str, Any]]:
    """Turns the documents could not answer: no section matched, or marked "Didn't help"."""
    return [t for t in turns(project, author) if not t.get("docs") or t.get("unhelpful")]


def docs_changed(turn: dict[str, Any], by_id: dict[str, Any]) -> bool:
    """A section this turn was sent has another text now or is gone (D22)."""
    return any(i not in by_id or section_hash(by_id[i]) != h for i, h in (turn.get("docs") or {}).items())
