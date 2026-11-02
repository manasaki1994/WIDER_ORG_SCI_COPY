"""BibTeX exports (Scopus, Web of Science) as field -> values records.

``bibtexparser`` is used when it is installed (either the 1.x or the 2.x API); otherwise a small built-in
parser handles the subset database exports use: ``@type{key, field = {value}, field = "value", field = 123}``
with nested braces and ``{{double braces}}``. Authors are split on ``and``; the entry type is kept under
``ENTRYTYPE`` and the citation key under ``ID``. Every field name is lower-cased.
"""
from __future__ import annotations

import re
from pathlib import Path

_AND = re.compile(r"\s+and\s+", re.I)
_WS = re.compile(r"\s+")
SKIP_TYPES = {"comment", "string", "preamble"}


def _clean(value: str) -> str:
    v = value.strip()
    while len(v) >= 2 and v[0] == "{" and v[-1] == "}":
        v = v[1:-1].strip()
    v = v.replace("{", "").replace("}", "")
    return _WS.sub(" ", v).strip()


def _entry_to_record(entry_type: str, key: str, fields: dict[str, str]) -> dict[str, list[str]]:
    rec: dict[str, list[str]] = {"ENTRYTYPE": [entry_type.lower()]}
    if key:
        rec["ID"] = [key]
    for name, raw in fields.items():
        val = _clean(raw)
        if not val:
            continue
        name = name.lower().strip()
        if name in ("author", "editor"):
            rec[name] = [a.strip() for a in _AND.split(val) if a.strip()]
        else:
            rec[name] = [val]
    return rec


def parse_bibtex_builtin(text: str) -> list[dict[str, list[str]]]:
    """Dependency-free parser for the BibTeX subset that database exports produce."""
    records: list[dict[str, list[str]]] = []
    macros: dict[str, str] = {}  # @string{name = "value"} definitions, substituted into later fields
    i, n = 0, len(text)
    while True:
        at = text.find("@", i)
        if at < 0:
            break
        j = at + 1
        while j < n and (text[j].isalnum() or text[j] in "_-"):
            j += 1
        entry_type = text[at + 1:j].strip()
        k = j
        while k < n and text[k].isspace():
            k += 1
        if k >= n or text[k] not in "{(":
            i = j
            continue
        open_ch = text[k]
        close_ch = "}" if open_ch == "{" else ")"
        depth, m = 1, k + 1
        while m < n and depth:
            c = text[m]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == close_ch and open_ch == "(" and depth == 1:
                depth = 0
                break
            m += 1
        body = text[k + 1:m - 1] if open_ch == "{" else text[k + 1:m]
        i = m + 1
        if entry_type.lower() == "string":
            _, defs = _parse_body(body, macros)
            macros.update({k.lower(): v for k, v in defs.items()})
            continue
        if entry_type.lower() in SKIP_TYPES or not entry_type:
            continue
        key, fields = _parse_body(body, macros)
        records.append(_entry_to_record(entry_type, key, fields))
    return records


def _parse_body(body: str, macros: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    parts = _split_top_level(body, ",")
    key = ""
    fields: dict[str, str] = {}
    if parts and "=" not in parts[0]:
        key = parts[0].strip()
        parts = parts[1:]
    for part in parts:
        if "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if not name:
            continue
        chunks = [_chunk_value(c.strip(), macros or {}) for c in _split_top_level(value, "#")]
        fields[name] = " ".join(c for c in chunks if c)
    return key, fields


def _chunk_value(chunk: str, macros: dict[str, str]) -> str:
    """One ``#``-separated piece: quoted string, braced string, number, or a macro name."""
    if chunk[:1] in ('"', "{") or not chunk:
        return _strip_quotes(chunk)
    return macros.get(chunk.lower(), chunk)


def _strip_quotes(v: str) -> str:
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        return v[1:-1]
    return v


def _split_top_level(s: str, sep: str) -> list[str]:
    out, depth, cur, in_quote = [], 0, [], False
    for c in s:
        if c == '"' and depth == 0:
            in_quote = not in_quote
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        if c == sep and depth == 0 and not in_quote:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    tail = "".join(cur)
    if tail.strip():
        out.append(tail)
    return out


def _parse_with_library(text: str) -> list[dict[str, list[str]]] | None:
    """Use bibtexparser when available; None when it is not installed or fails on this input."""
    try:
        import bibtexparser
    except ImportError:
        return None
    try:
        if hasattr(bibtexparser, "parse_string"):  # 2.x
            lib = bibtexparser.parse_string(text)
            out = []
            for e in lib.entries:
                fields = {f.key: str(f.value) for f in e.fields}
                out.append(_entry_to_record(e.entry_type, e.key, fields))
            return out
        db = bibtexparser.loads(text)  # 1.x
        out = []
        for e in db.entries:
            fields = {k: v for k, v in e.items() if k not in ("ENTRYTYPE", "ID")}
            out.append(_entry_to_record(e.get("ENTRYTYPE", ""), e.get("ID", ""), fields))
        return out
    except Exception:
        return None


def parse_bibtex(text: str, prefer_builtin: bool = False) -> list[dict[str, list[str]]]:
    if not prefer_builtin:
        recs = _parse_with_library(text)
        if recs is not None:
            return recs
    return parse_bibtex_builtin(text)


def read_bibtex(path: str | Path, prefer_builtin: bool = False) -> list[dict[str, list[str]]]:
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    return parse_bibtex(text, prefer_builtin=prefer_builtin)
