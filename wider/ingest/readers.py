"""One entry point for every export format: detect the format, read the records, standardise them.

Formats: ``ris`` (also ``.txt`` RIS), ``csv``, ``tsv`` (tab-delimited, e.g. Web of Science "savedrecs.txt"),
``xlsx``, ``xls`` and ``bib``. A ``.txt`` file is sniffed: RIS tags, a BibTeX ``@`` entry, tabs or commas.
"""
from __future__ import annotations

import re
from pathlib import Path

from .bibtex_reader import read_bibtex
from .csv_reader import decode, read_table, sniff_delimiter
from .ris import TAG, read_ris
from .standardize import profile_for, standardize
from .xlsx_reader import read_excel_table

SUFFIX_FORMATS = {".ris": "ris", ".csv": "csv", ".tsv": "tsv", ".xlsx": "xlsx", ".xls": "xls", ".bib": "bib",
                  ".bibtex": "bib", ".txt": None, ".tab": "tsv"}
INPUT_SUFFIXES = set(SUFFIX_FORMATS)
FORMATS = ("ris", "csv", "tsv", "xlsx", "xls", "bib")
_BIB_ENTRY = re.compile(r"^\s*@[A-Za-z]+\s*[{(]", re.M)


class NotAnExport(ValueError):
    """A file read as a table whose rows have no title and no DOI: not a database export."""


def sniff_text_format(text: str) -> str:
    """ris | bib | tsv | csv for an unlabelled text export."""
    head = text[:20000]
    lines = [ln for ln in head.splitlines() if ln.strip()]
    if any(TAG.match(ln.rstrip("\r")) and ln.startswith("TY  -") for ln in lines[:50]):
        return "ris"
    if _BIB_ENTRY.search(head):
        return "bib"
    return "tsv" if sniff_delimiter(head) == "\t" else "csv"


def detect_format(path: str | Path) -> str:
    """Format from the suffix, or sniffed from the content for ``.txt`` and unknown suffixes."""
    path = Path(path)
    fmt = SUFFIX_FORMATS.get(path.suffix.lower())
    if fmt:
        return fmt
    return sniff_text_format(decode(path))


def read_file(path: str | Path, fmt: str | None = None) -> tuple[str, list[str], list[dict[str, list[str]]]]:
    """``(format, headers, records)``. Headers are the column names of a table, the field names of BibTeX,
    and empty for RIS."""
    path = Path(path)
    fmt = fmt or detect_format(path)
    if fmt == "ris":
        return fmt, [], read_ris(path)
    if fmt == "bib":
        recs = read_bibtex(path)
        headers = sorted({k for r in recs for k in r})
        return fmt, headers, recs
    if fmt in ("xlsx", "xls"):
        headers, recs = read_excel_table(path)
        return fmt, headers, recs
    if fmt in ("csv", "tsv"):
        headers, recs = read_table(path, delimiter="\t" if fmt == "tsv" else None)
        return fmt, headers, recs
    raise ValueError(f"unsupported export format {fmt!r} for {path.name}")


def read_standard(path: str | Path, platform: str, fmt: str | None = None) -> tuple[dict, list[dict]]:
    """Read and standardise one export: ``(meta, rows)``.

    ``meta`` carries ``format``, ``profile`` (tag profile for RIS, column profile otherwise), ``headers``,
    ``detected_platform`` (the platform whose column layout the header matched, ``any`` for BibTeX, ``None``
    when no profile matched) and ``mismatch`` (True when the detected platform is another known platform).
    """
    fmt, headers, recs = read_file(path, fmt)
    if fmt == "ris":
        rows = [standardize(r, platform) for r in recs]
        if not rows:   # an empty file, or one with no "TY  -" record, imports nothing (final check, CLI m2)
            empty = not Path(path).read_bytes().strip()
            raise NotAnExport(f"{Path(path).name} " + ("is empty" if empty else "holds no RIS record (no line starts "
                              "with \"TY  -\")") + ": nothing was imported. Export the records from the database "
                              "again as RIS, CSV, TSV, Excel or BibTeX and add that file")
        return {"format": fmt, "profile": f"ris:{platform}", "headers": [], "detected_platform": None,
                "mismatch": False}, rows
    prof = profile_for(headers, fmt)
    rows = [standardize(r, platform, prof) for r in recs]
    if rows and not any(r["title"] or r["doi"] for r in rows):   # e.g. a text file read as a one-column table
        cols = ", ".join(h for h in headers if h)[:200] or "none"
        raise NotAnExport(f"{Path(path).name} does not look like a database export: none of its {len(rows)} "
                          f"row{'' if len(rows) == 1 else 's'} has a title or DOI (columns: {cols}). Export the "
                          "records from the database as RIS, CSV, TSV, Excel or BibTeX and add that file")
    det = prof.get("platform") if prof["name"] not in ("generic", "bibtex") else ("any" if fmt == "bib" else None)
    return {"format": fmt, "profile": prof["name"], "headers": headers, "detected_platform": det,
            "mismatch": det not in (None, "any", platform)}, rows
