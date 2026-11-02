"""Reference-list extraction from a PDF of a review article. **Local only: no network in this module.**

Best effort: the reference section is located by heading, entries are split on numbering or author-year
starts, and each entry yields a DOI (when printed), a year and a heuristic title. Entries that cannot be
parsed keep their raw text so the researcher can see and correct them (or paste a clean list instead).
Needs the optional extra ``pip install "wider[pdf]"`` (pypdf).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.text import clean_doi, clean_title

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>\]]+)", re.IGNORECASE)
YEAR_RE = re.compile(r"\((\d{4}[a-z]?)\)|\b((?:19|20)\d{2})\b")
HEADING_RE = re.compile(r"^\s*(?:\d+\.?\s*)?(references|reference list|bibliography|literature cited|works cited)\s*:?\s*$",
                        re.IGNORECASE | re.MULTILINE)
NUMBERED_RE = re.compile(r"^\s*\[?(\d{1,3})[\].)]\s+", re.MULTILINE)
# author-year entry start: "Surname, A. B., & Other, C. (2019)." or "Surname A, Other B (2019)"
AUTHOR_YEAR_RE = re.compile(r"^(?=[A-Z][^\n]{2,200}?\((?:19|20)\d{2}[a-z]?\))", re.MULTILINE)
_STOP_RE = re.compile(r"^\s*(appendix|supplementary|supplemental|tables?|figures?)\b", re.IGNORECASE | re.MULTILINE)


PDF_EXTRA = 'PDF support needs the optional extra: pip install "wider[pdf]"'


def extract_pages(path: str | Path) -> list[str]:
    """Raw text of each page (a damaged page gives ""); ``ValueError`` naming ``wider[pdf]`` without pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ValueError(PDF_EXTRA) from e
    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - a damaged page should not lose the others
            pages.append("")
    return pages


def extract_text(path: str | Path) -> str:
    """Text of all pages joined; fixes end-of-line hyphenation and collapses runs of blank lines."""
    text = "\n".join(extract_pages(path))
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # hyphenation across lines
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def reference_section(text: str) -> tuple[str, list[str]]:
    """Text after the last reference heading; falls back to the last 30 % of the document with a warning."""
    warnings: list[str] = []
    hits = list(HEADING_RE.finditer(text))
    if hits:
        body = text[hits[-1].end():]
        stop = _STOP_RE.search(body)
        if stop and stop.start() > 200:
            body = body[:stop.start()]
        return body.strip(), warnings
    warnings.append("no reference heading found; parsed the last 30 % of the document")
    return text[int(len(text) * 0.7):].strip(), warnings


def split_entries(section: str) -> list[str]:
    """Reference entries from the section text (numbered lists first, then author-year starts)."""
    section = section.strip()
    if not section:
        return []
    numbered = list(NUMBERED_RE.finditer(section))
    if len(numbered) >= 3:
        parts = []
        for i, m in enumerate(numbered):
            end = numbered[i + 1].start() if i + 1 < len(numbered) else len(section)
            parts.append(section[m.end():end])
        return [_one_line(p) for p in parts if p.strip()]
    starts = [m.start() for m in AUTHOR_YEAR_RE.finditer(section)]
    if len(starts) >= 2:
        starts = sorted(set(starts))
        parts = [section[a:b] for a, b in zip(starts, starts[1:] + [len(section)])]
        return [_one_line(p) for p in parts if p.strip()]
    # last resort: blank-line separated paragraphs
    return [_one_line(p) for p in re.split(r"\n\s*\n", section) if p.strip()]


def _one_line(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_entry(entry: str) -> dict[str, Any]:
    """``{title, doi, year, first_author, raw_ref, confidence}`` from one reference string.

    ``confidence`` is ``doi`` when a DOI was printed, ``parsed`` when a year was found and the title is the
    segment after it, ``heuristic`` otherwise (the whole entry is kept as the title candidate).
    """
    raw = _one_line(entry)
    doi = None
    m = DOI_RE.search(raw)
    if m:
        doi = clean_doi(m.group(1).rstrip(".,;)"))
    year = None
    ym = YEAR_RE.search(raw)
    if ym:
        year = int((ym.group(1) or ym.group(2))[:4])
    title = ""
    confidence = "heuristic"
    if ym:
        after = raw[ym.end():].lstrip(" .:;,")
        # title = first sentence-like segment after the year
        seg = re.split(r"(?<=[a-z0-9\)\]])[.?!]\s+(?=[A-Z])|\?\s+|\.\s+(?=[A-Z][a-z]+(?: of| and| &|,)?\s)", after, maxsplit=1)
        cand = seg[0].strip(" .") if seg else ""
        if 3 <= len(cand.split()) <= 60:
            title = cand
            confidence = "parsed"
    if not title:
        # heuristic: longest segment between periods that is not the author list
        segs = [s.strip() for s in re.split(r"\.\s+", raw) if len(s.split()) >= 4]
        title = max(segs, key=len) if segs else raw
    if doi:
        confidence = "doi"
    first_author = raw.split(",")[0].strip() if "," in raw[:80] else (raw.split()[0] if raw else "")
    first_author = re.sub(r"[^A-Za-z' \-]", "", first_author).strip() or None
    return {"title": clean_title(title)[:500], "doi": doi, "year": year, "first_author": first_author,
            "raw_ref": raw[:1000], "confidence": confidence}


def parse_reference_text(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Entries → rows from reference-list text (used for PDFs and for pasted reference lists)."""
    section, warnings = text, []
    if HEADING_RE.search(text):  # a pasted list may carry the heading and what follows the list
        section, warnings = reference_section(text)
    rows = [parse_entry(e) for e in split_entries(section)]
    rows = [r for r in rows if r["title"] or r["doi"]]
    n_heur = sum(r["confidence"] == "heuristic" for r in rows)
    if rows and n_heur / len(rows) > 0.5:
        warnings.append(f"{n_heur} of {len(rows)} entries parsed heuristically; check titles or paste a clean list")
    return rows, warnings


def parse_pdf(path: str | Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows and warnings from a review article's PDF (reference section only)."""
    text = extract_text(path)
    if not text.strip():
        return [], ["the PDF has no extractable text (scanned image?); paste the reference list instead"]
    section, warnings = reference_section(text)
    rows, w2 = parse_reference_text(section)
    if not rows:
        warnings.append("no reference entries recognised; paste the reference list instead")
    return rows, warnings + w2
