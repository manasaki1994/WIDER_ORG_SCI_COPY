"""A small, tolerant RIS reader. Every tag keeps a list of values; continuation lines are joined."""
from __future__ import annotations

import re
from pathlib import Path

TAG = re.compile(r"^([A-Z][A-Z0-9])  - ?(.*)$")


def read_ris(path: str | Path) -> list[dict[str, list[str]]]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_ris(text)


def parse_ris(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    cur: dict[str, list[str]] | None = None
    last_tag: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        m = TAG.match(line)
        if m:
            tag, val = m.group(1), m.group(2).strip()
            if tag == "TY":
                if cur:
                    records.append(cur)
                cur = {"TY": [val]}
                last_tag = tag
                continue
            if tag == "ER":
                if cur:
                    records.append(cur)
                cur, last_tag = None, None
                continue
            if cur is None:
                cur = {}
            cur.setdefault(tag, []).append(val)
            last_tag = tag
        elif cur is not None and last_tag and line.strip():
            # continuation of the previous value (wrapped abstract)
            cur[last_tag][-1] = (cur[last_tag][-1] + " " + line.strip()).strip()
    if cur:
        records.append(cur)
    return records
