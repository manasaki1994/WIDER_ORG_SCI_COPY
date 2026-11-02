"""A topic-neutral two-page seed PDF, written into the test's ``tmp_path`` (no binary enters the repository).

Built with pypdf's ``PdfWriter``: two blank pages, each with a hand-written content stream of ``BT … Tj … ET`` text
operators in the standard Type 1 font Helvetica, so ``PdfReader.extract_text`` reads the lines back. Page 1 holds
a running header, the Keywords line, an "also called" sentence and a word hyphenated across two lines; page 2 the
same header and a reference list of three author-year entries in two venues.
"""
from __future__ import annotations

from pathlib import Path

HEADER = "Synthetic Signals Review 2026"
PAGE1 = [
    HEADER,
    "Abstract",
    "We study alpha coupling in synthetic signal pairs and their synchro-",
    "nisation across trials.",
    "Keywords: alpha coupling; beta drift",
    "Introduction",
    'Alpha coupling, also called phase binding, links two signals. The "phase binding" view',
    'treats coupling as shared timing; "phase binding" recurs in later work.',
]
PAGE2 = [
    HEADER,
    "Discussion",
    "Beta drift remains open.",
    "References",
    "Abel, A. (2019). Alpha coupling in paired signals. Journal of Synthetic Signals, 12(3), 1-10.",
    "Berg, B. (2020). Alpha coupling and beta drift. Journal of Synthetic Signals, 13(1), 11-20.",
    "Chen, C. (2021). Phase binding at scale. Proceedings on Phase Studies, 4, 21-30.",
]


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _content(lines: list[str]) -> bytes:
    ops = ["BT", "/F1 10 Tf", "14 TL", "56 760 Td"]
    for i, line in enumerate(lines):
        ops.append(("" if i == 0 else "T* ") + f"({_escape(line)}) Tj")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def make_pdf(path: Path, pages: tuple[list[str], ...] = (PAGE1, PAGE2)) -> Path:
    """Write the PDF to ``path`` and return it."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    font = w._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                           NameObject("/Subtype"): NameObject("/Type1"),
                                           NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for lines in pages:
        page = w.add_blank_page(612, 792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(_content(lines))
        page[NameObject("/Contents")] = w._add_object(stream)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        w.write(fh)
    return path
