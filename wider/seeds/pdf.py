"""A full-text PDF per seed paper, kept in ``seeds/<benchmark_id>.pdf`` (gitignored, never in a bundle, package or
export), and its text: pypdf's page text, de-hyphenated, without the running headers and footers, split into
sections, the reference section parsed by :mod:`wider.prior.refs_pdf`. Output goes under ``runs/seeds/<id>/``.
**No network in this module.**
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.provenance import sha256_file, utcnow
from ..prior import refs_pdf

SEEDS_DIR = "seeds"
MAGIC = b"%PDF-"
# a line that is a section heading on its own (numbered or not)
SECTION_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?(abstract|introduction|background|literature review|theory|"
                        r"methods?|methodology|materials and methods|data|results|findings|discussion|conclusions?|"
                        r"limitations|acknowledg(?:e)?ments|references|reference list|bibliography|appendix)\s*:?\s*$",
                        re.IGNORECASE)


def _dir(project: Project) -> Path:
    d = project.path(SEEDS_DIR)
    d.mkdir(parents=True, exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n", encoding="utf-8")
    return d


def _seed_ids(project: Project) -> list[str]:
    return [b.benchmark_id for b in project.read_benchmark()]


def _check_id(project: Project, benchmark_id: str) -> str:
    if benchmark_id not in _seed_ids(project):
        raise KeyError(f"no seed paper {benchmark_id}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", benchmark_id) or benchmark_id.startswith("."):
        raise ValueError(f"seed id {benchmark_id!r} cannot name a file; rename the seed paper first")
    return benchmark_id


def file_path(project: Project, benchmark_id: str) -> Path:
    return project.path(SEEDS_DIR, f"{benchmark_id}.pdf")


def add(project: Project, benchmark_id: str, path: str | Path) -> dict[str, Any]:
    """Copy a PDF into ``seeds/<id>.pdf`` (replacing an earlier one) and record its sha256. A file that does not
    start with ``%PDF-`` is refused (``ValueError``); the original file is left where it is."""
    _check_id(project, benchmark_id)
    src = Path(path)
    if not src.is_file():
        raise ValueError(f"no file at {src.name}")
    with src.open("rb") as fh:
        if fh.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{src.name} is not a PDF (a PDF file starts with %PDF-)")
    dest = file_path(project, benchmark_id)
    _dir(project)
    tmp = dest.with_suffix(".part")
    shutil.copyfile(src, tmp)
    tmp.replace(dest)
    sha = sha256_file(dest)
    with project.store.tx() as c:
        c.execute("DELETE FROM seed_files WHERE benchmark_id=?", (benchmark_id,))
        c.execute("INSERT INTO seed_files(benchmark_id, sha256, added_at) VALUES(?,?,?)", (benchmark_id, sha, utcnow()))
    return {"benchmark_id": benchmark_id, "file": f"{SEEDS_DIR}/{dest.name}", "sha256": sha,
            "size_bytes": dest.stat().st_size}


def remove(project: Project, benchmark_id: str) -> dict[str, Any]:
    """Delete the project's copy of a seed's PDF, its hash and its extracted text (the researcher's original file
    elsewhere is untouched). ``KeyError`` when the seed has no PDF."""
    dest = file_path(project, benchmark_id)
    rows = project.store.query("SELECT 1 FROM seed_files WHERE benchmark_id=?", (benchmark_id,))
    if not rows and not dest.exists():
        raise KeyError(f"seed paper {benchmark_id} has no PDF")
    dest.unlink(missing_ok=True)
    project.store.execute("DELETE FROM seed_files WHERE benchmark_id=?", (benchmark_id,))
    shutil.rmtree(text_dir(project, benchmark_id), ignore_errors=True)
    return {"benchmark_id": benchmark_id, "removed": True}


def list_files(project: Project) -> list[dict[str, Any]]:
    """One row per seed paper: whether it has a PDF here, its hash and size, and whether its text was extracted."""
    rows = {r["benchmark_id"]: r for r in project.store.query("SELECT benchmark_id, sha256, added_at FROM seed_files")}
    out = []
    for bid in _seed_ids(project):
        f = file_path(project, bid)
        r = rows.get(bid)
        has = bool(r) and f.exists()
        out.append({"benchmark_id": bid, "has_pdf": has, "file": f"{SEEDS_DIR}/{f.name}" if has else None,
                    "sha256": r["sha256"] if has else None, "added_at": r["added_at"] if has else None,
                    "size_bytes": f.stat().st_size if has else None,
                    "extracted": (text_dir(project, bid) / "text.txt").exists()})
    return out


def with_pdf(project: Project) -> list[str]:
    return [r["benchmark_id"] for r in list_files(project) if r["has_pdf"]]


# ------------------------------------------------------------------------------------------------ text extraction
def text_dir(project: Project, benchmark_id: str) -> Path:
    return project.path("runs", "seeds", benchmark_id)


def _line_key(line: str) -> str:
    """A header or footer line compared without its page number."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line).strip().lower())


def postprocess(pages: list[str]) -> dict[str, Any]:
    """``{text, sections: [{name, text}], references: [entries], warnings}`` from the raw page texts.

    A line among the first or last two of a page that recurs (page numbers aside) on at least two pages is a
    running header or footer: it is kept where it first appears and dropped elsewhere. Words hyphenated across a
    line break are joined. Sections start at a line that is a common heading on its own; the text after the last
    reference heading is parsed into entries by :func:`wider.prior.refs_pdf.parse_reference_text`."""
    split = [[ln.rstrip() for ln in (p or "").splitlines()] for p in pages]
    edge: dict[str, int] = {}
    for lines in split:
        keys = {_line_key(ln) for ln in lines[:2] + lines[-2:] if ln.strip()}
        for k in keys:
            edge[k] = edge.get(k, 0) + 1
    repeated = {k for k, n in edge.items() if n >= 2 and len(k) > 2}
    seen: set[str] = set()
    kept: list[str] = []
    for lines in split:
        n = len(lines)
        for i, ln in enumerate(lines):
            k = _line_key(ln)
            if k in repeated and (i < 2 or i >= n - 2):
                if k in seen:
                    continue
                seen.add(k)
            kept.append(ln)
    text = "\n".join(kept)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    sections: list[dict[str, str]] = []
    name, buf = "front", []
    for ln in text.splitlines():
        m = SECTION_RE.match(ln)
        if m:
            if any(x.strip() for x in buf):
                sections.append({"name": name, "text": "\n".join(buf).strip()})
            name, buf = m.group(1).lower(), []
        else:
            buf.append(ln)
    if any(x.strip() for x in buf):
        sections.append({"name": name, "text": "\n".join(buf).strip()})
    warnings: list[str] = []
    refs: list[dict[str, Any]] = []
    if refs_pdf.HEADING_RE.search(text):
        refs, warnings = refs_pdf.parse_reference_text(text)
    else:
        warnings.append("no reference heading found; no references parsed")
    return {"text": text, "sections": sections, "references": refs, "warnings": warnings}


def extract(project: Project, benchmark_id: str) -> dict[str, Any]:
    """Extract one seed's PDF into ``runs/seeds/<id>/``: ``text.txt``, ``sections.json`` and ``references.json``.
    ``ValueError`` naming ``wider[pdf]`` when pypdf cannot be imported (nothing is written then)."""
    f = file_path(project, benchmark_id)
    if benchmark_id not in with_pdf(project):
        raise KeyError(f"seed paper {benchmark_id} has no PDF; add one with wider benchmark pdf add")
    pages = refs_pdf.extract_pages(f)
    out = postprocess(pages)
    d = text_dir(project, benchmark_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "text.txt").write_text(out["text"], encoding="utf-8")
    (d / "sections.json").write_text(json.dumps(out["sections"], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (d / "references.json").write_text(json.dumps(out["references"], indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    warnings = list(out["warnings"])
    if not out["text"].strip():
        warnings.insert(0, "the PDF has no extractable text (scanned image?)")
    return {"benchmark_id": benchmark_id, "pages": len(pages), "words": len(out["text"].split()),
            "sections": [s["name"] for s in out["sections"]], "references": len(out["references"]),
            "folder": str(d.relative_to(project.root)), "warnings": warnings}


def extract_all(project: Project, benchmark_id: str | None = None) -> list[dict[str, Any]]:
    """Extract every seed with a PDF (or the one named). The import check runs first, so a missing pypdf writes
    nothing for any seed."""
    ids = [benchmark_id] if benchmark_id else with_pdf(project)
    if not ids:
        raise ValueError("no seed paper has a PDF yet; add one with wider benchmark pdf add <id> <file>")
    try:
        import pypdf  # noqa: F401
    except ImportError as e:
        raise ValueError(refs_pdf.PDF_EXTRA) from e
    return [extract(project, b) for b in ids]


def load_text(project: Project, benchmark_id: str) -> dict[str, Any] | None:
    """The extracted sections and references of a seed, or ``None`` when it was not extracted."""
    d = text_dir(project, benchmark_id)
    if not (d / "sections.json").exists():
        return None
    return {"sections": json.loads((d / "sections.json").read_text(encoding="utf-8")),
            "references": json.loads((d / "references.json").read_text(encoding="utf-8"))
            if (d / "references.json").exists() else []}
