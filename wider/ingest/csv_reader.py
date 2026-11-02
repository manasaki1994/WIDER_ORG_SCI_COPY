"""Delimited exports (CSV from ProQuest, EBSCO, Scopus; tab-delimited Web of Science) as tag -> values records.

Every row becomes ``{column: [value]}`` with empty cells dropped, the same shape as :func:`wider.ingest.ris.read_ris`
produces for RIS tags, so :mod:`wider.ingest.standardize` can treat both alike through a column profile.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

DELIMITERS = ",;\t|"
ENCODINGS = ("utf-8-sig", "utf-16", "cp1252", "latin-1")


def decode(path: str | Path) -> str:
    """Read a text export, tolerating a UTF-8 BOM, UTF-16 (EBSCO) and Windows code pages."""
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sniff_delimiter(text: str) -> str:
    """Comma, semicolon, tab or pipe, decided on the header and first rows (csv.Sniffer with a counting fallback)."""
    sample = text[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=DELIMITERS).delimiter
    except csv.Error:
        pass
    head = sample.splitlines()[0] if sample else ""
    counts = {d: head.count(d) for d in DELIMITERS}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def read_table(path: str | Path, delimiter: str | None = None) -> tuple[list[str], list[dict[str, list[str]]]]:
    """Return ``(headers, rows)``; rows are ``{header: [cell]}`` without empty cells. Blank lines are skipped."""
    text = decode(path)
    # skip leading blank lines or a title line before the header (some exports put the query above the table)
    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    text = "\n".join(lines[start:])
    if not text.strip():
        return [], []
    delim = delimiter or sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    headers = [h.strip().lstrip("﻿") for h in next(reader, [])]
    rows: list[dict[str, list[str]]] = []
    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        rec: dict[str, list[str]] = {}
        for h, v in zip(headers, cells):
            v = v.strip()
            if h and v:
                rec.setdefault(h, []).append(v)
        if rec:
            rows.append(rec)
    return headers, rows


def read_csv(path: str | Path, delimiter: str | None = None) -> list[dict[str, list[str]]]:
    return read_table(path, delimiter)[1]
