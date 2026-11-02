#!/usr/bin/env python3
"""Pre-commit guard: refuse to commit anything that looks like private review data.

Fails when a staged file
  - is a bibliographic export (*.ris, *.bib, *.nbib, *.enw) or a SQLite store / numpy array,
  - is a CSV/TSV/JSONL whose header contains an abstract column,
  - contains an absolute path under the author's home directory,
  - is a review package or a reviewer's decisions file (a file under ``review-packages/``, a ``wider-decisions-*.json``
    or a package page with item tokens outside ``tests/``), or
  - (text files) uses a word of the private topic list (the file named by ``WIDER_PRIVATE_WORDS``); the package, its templates, tests and docs are
    topic-neutral. A line that legitimately needs such a word carries the marker ``topic-ok``. The replication
    harness for the original review and the proposal documents that describe it as the case study are exempt.
Run manually:  python3 scripts/check_no_private_data.py [paths...]   (defaults to staged files)
Install hook:  python3 scripts/check_no_private_data.py --install
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_SUFFIXES = {".ris", ".bib", ".nbib", ".enw", ".sqlite", ".sqlite-wal", ".sqlite-shm", ".npy", ".npz", ".pdf"}
FORBIDDEN_NAMES = {"responses.jsonl", "store.sqlite"}
_HOME = str(Path.home())
FORBIDDEN_STRINGS = [re.compile(re.escape(_HOME) + r"(/|$)")] if _HOME not in ("", "/") else []


# vocabulary of the case-study topic, kept outside the repository: one word pattern per line in the file named
# by WIDER_PRIVATE_WORDS (word-bounded, case-insensitive); without that file there is no topic check
def topic_pattern(words: list[str]) -> re.Pattern | None:
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.IGNORECASE) if words else None


def _private_words() -> list[str]:
    src = os.environ.get("WIDER_PRIVATE_WORDS")
    if not src or not Path(src).is_file():
        return []
    lines = Path(src).read_text(encoding="utf-8").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.lstrip().startswith("#")]


TOPIC_WORDS = topic_pattern(_private_words())
TOPIC_OK = "topic-ok"
# files allowed to use the topic vocabulary (the case-study replication harness, proposal draft and appendix
# numbers moved out of the repository on 28 Oct 2026)
TOPIC_EXEMPT = (
    re.compile(r"(^|/)CHANGELOG[^/]*$", re.IGNORECASE),
)
ABSTRACT_COLUMN = re.compile(r"(^|[,;\t\"])\s*abstract\s*(\"|[,;\t]|$)", re.IGNORECASE)
TEXT_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".jsonl", ".csv", ".tsv", ".toml", ".html",
                 ".js", ".css", ".j2", ".cfg", ".ini", ".rst"}
SELF = Path(__file__).resolve()


def staged_files() -> list[Path]:
    out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True, text=True, check=True).stdout
    return [Path(line) for line in out.splitlines() if line.strip()]


def topic_exempt(path: Path) -> bool:
    posix = path.as_posix()
    return any(rx.search(posix) for rx in TOPIC_EXEMPT)


def topic_hits(text: str) -> list[tuple[int, str]]:
    """(line number, word) for every topic word on a line that does not carry the ``topic-ok`` marker."""
    hits = []
    if TOPIC_WORDS is None:
        return hits
    for n, line in enumerate(text.splitlines(), 1):
        if TOPIC_OK in line:
            continue
        hits.extend((n, m.group(0)) for m in TOPIC_WORDS.finditer(line))
    return hits


PACKAGE_PAGE = re.compile(r'<script type="application/json" id="data">\s*\{"package_id": "p-')


def package_problem(path: Path, text: str | None = None) -> str | None:
    """A review package or decisions file (reviewer-roles-and-blinding): titles and abstracts of review records."""
    posix = path.as_posix()
    if "review-packages" in path.parts:
        return f"{path}: a file of a project's review-packages/ folder (review records) must not be committed"
    if "tests/" in posix or posix.startswith("tests"):
        return None
    if re.fullmatch(r"wider-decisions-.*\.json", path.name):
        return f"{path}: a reviewer's decisions file must not be committed"
    if text is not None and path.suffix.lower() == ".html" and PACKAGE_PAGE.search(text):
        return f"{path}: a review package page (review records) must not be committed"
    return None


def check(path: Path) -> list[str]:
    problems = []
    if path.suffix.lower() in FORBIDDEN_SUFFIXES or path.name in FORBIDDEN_NAMES:
        return [f"{path}: bibliographic export / store / embedding file must not be committed"]
    pp = package_problem(path)
    if pp:
        return [pp]
    if not path.exists() or path.resolve() == SELF:
        return problems
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return problems
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return problems
    pp = package_problem(path, text)
    if pp:
        return [pp]
    for rx in FORBIDDEN_STRINGS:
        if rx.search(text):
            problems.append(f"{path}: contains forbidden string {rx.pattern!r}")
    if not topic_exempt(path):
        hits = topic_hits(text)
        if hits:
            shown = ", ".join(f"line {n}: {w!r}" for n, w in hits[:5])
            more = f" (+{len(hits) - 5} more)" if len(hits) > 5 else ""
            problems.append(f"{path}: topic vocabulary of the original review ({shown}{more}); "
                            f"the tool is topic-neutral, rephrase or mark the line with '{TOPIC_OK}'")
    if path.suffix.lower() in {".csv", ".tsv"}:
        head = text.splitlines()[0] if text else ""
        if ABSTRACT_COLUMN.search(head) and "examples/" not in path.as_posix() and "tests/" not in path.as_posix():
            problems.append(f"{path}: CSV with an abstract column (review data?)")
    if path.suffix.lower() == ".jsonl" and '"abstract"' in text[:5000]:
        problems.append(f"{path}: JSONL with abstracts (model responses?)")
    return problems


def install() -> None:
    hook = Path(".git/hooks/pre-commit")
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexec python3 scripts/check_no_private_data.py\n", encoding="utf-8")
    os.chmod(hook, 0o755)
    print(f"installed {hook}")


def main(argv: list[str]) -> int:
    if "--install" in argv:
        install()
        return 0
    paths = [Path(a) for a in argv if not a.startswith("--")] or staged_files()
    problems = [p for path in paths for p in check(path)]
    for p in problems:
        print("PRIVATE DATA GUARD:", p, file=sys.stderr)
    if problems:
        print("commit refused; remove the files or fix the strings above", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
