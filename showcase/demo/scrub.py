"""Privacy scrub for the interactive demo (design D10), used when recording the snapshot and again when building.

``scrub(text)`` rewrites computer paths to the demo's project folder and the build computer's login name to
"Reviewer A". ``problems(text)`` lists what must stop a recording or a build: a key-like string, a ``/Users/<name>``
path other than the app's own placeholder ``/Users/you``, the login name, a forbidden name (``forbidden_names``, change
demo-chapters-and-video D13) and anything the repository's privacy guard (scripts/check_no_private_data.py) refuses. Nothing is dropped silently: a problem stops the caller, which names the
file and the kind of string.
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import check_no_private_data as guard  # noqa: E402

PROJECT = "~/reviews/employee-voice"
AUTHOR = "Reviewer A"
MARKER = "[word hidden in the demo]"
KEY_LIKE = re.compile(r"sk-[A-Za-z0-9_-]{20,}|Bearer [A-Za-z0-9._-]{16,}")
USERS_PATH = re.compile(r"/Users/(?!you(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+")
# a path under a home folder or a temporary folder, up to and including the project folder's name when it is there
_PATH = re.compile(r"(?:/private)?(?:/Users/(?!you(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+|/tmp|/var/folders)"
                   r"(?:/[^\s\"'<>/]+)*?/reviews/employee-voice")
_ANY_PATH = re.compile(r"(?:/private)?(?:/Users/(?!you(?![A-Za-z0-9._-]))[A-Za-z0-9._-]+|/tmp|/var/folders)"
                       r"(?:/[^\s\"'<>]*)?")


def login_name() -> str:
    return getpass.getuser()


def forbidden_names() -> list[str]:
    """Names the demo must not hold (D13), read now and never written down: ``$USER``, ``git config user.name``, the local
    part of ``git config user.email``, their parts split on ``-``, ``_``, ``.``, ``+``, digits and spaces, and ``WIDER_DEMO_FORBIDDEN_NAMES`` (comma-separated). An unset source is
    skipped; a part shorter than three letters is too common a word to refuse."""
    found = [os.environ.get("USER", "")]
    for key in ("user.name", "user.email"):
        try:
            value = subprocess.run(["git", "config", key], capture_output=True, text=True, timeout=10,
                                   cwd=REPO).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        # an e-mail address gives its local part: a run-together git name often hides the parts it spells out
        found.append(value.split("@", 1)[0] if key == "user.email" else value)
    found += os.environ.get("WIDER_DEMO_FORBIDDEN_NAMES", "").split(",")
    names = set()
    for n in filter(None, (f.strip() for f in found)):
        names.add(n)
        names.update(re.split(r"[-_.+\d\s]+", n))
    return sorted(n for n in names if len(n) >= 3 and n.lower() != AUTHOR.lower())


def name_hits(text: str, names: list[str] | None = None) -> list[str]:
    """The forbidden names that appear in ``text`` as a whole word, in any case."""
    names = forbidden_names() if names is None else names
    return [n for n in names if re.search(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", text, re.IGNORECASE)]


def scrub(text: str, user: str | None = None) -> str:
    """The text with computer paths and the login name rewritten. The project folder becomes ``PROJECT``; any other
    home or temporary path becomes ``~/...`` with only its last part kept."""
    user = user or login_name()
    text = _PATH.sub(PROJECT, text)
    text = _ANY_PATH.sub(lambda m: "~/" + m.group(0).rstrip("/").rsplit("/", 1)[-1], text)
    if user:
        text = re.sub(rf"(?<![A-Za-z0-9._-]){re.escape(user)}(?![A-Za-z0-9._-])", AUTHOR, text)
    return text


def hide_flagged(text: str) -> str:
    """A model's text with every word the privacy guard flags replaced by ``MARKER`` (nothing else changes)."""
    return guard.TOPIC_WORDS.sub(MARKER, text) if guard.TOPIC_WORDS else text


def problems(text: str, user: str | None = None) -> list[str]:
    """The kinds of string in ``text`` that must stop a recording or a build (empty when the text is safe)."""
    user = user or login_name()
    out = []
    if KEY_LIKE.search(text):
        out.append("a key-like string")
    if USERS_PATH.search(text):
        out.append("a /Users/ path")
    if user and re.search(rf"(?<![A-Za-z0-9._-]){re.escape(user)}(?![A-Za-z0-9._-])", text):
        out.append("the login name")
    if name_hits(text):
        out.append("a forbidden name (D13: the demo holds no person's name but Reviewer A)")
    for rx in guard.FORBIDDEN_STRINGS:
        if rx.search(text):
            out.append(f"a string the privacy guard forbids ({rx.pattern})")
    hits = guard.topic_hits(text)
    if hits:
        out.append(f"words the privacy guard flags ({', '.join(sorted({w.lower() for _, w in hits})[:5])})")
    return out
