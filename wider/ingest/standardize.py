"""Export record → standard fields used by matching, deduplication and screening.

Two kinds of input share one code path: RIS records (``{tag: [values]}``, mapped through the per-platform
tag profiles at the top of ``profiles.yaml``) and tabular or BibTeX records (``{column: [values]}``, mapped
through a *column profile* from the ``columns`` section). :func:`detect_profile` picks the column profile
from the header row, so a Scopus CSV dropped into the ProQuest folder is still read correctly (and flagged).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..core.text import clean_doi, clean_title, first_author_surname, norm_title, year_of

_PROFILES: dict | None = None
_WS = re.compile(r"\s+")
DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
RIS_DOI_FALLBACK_TAGS = ("UR", "L2", "L3", "N1", "M3")
STANDARD_FIELDS = ("title", "abstract", "doi", "year", "authors", "journal", "volume", "issue", "pages",
                   "start_page", "end_page", "issn", "keywords", "url", "language", "native_id", "type")


def profiles() -> dict:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = yaml.safe_load((Path(__file__).parent / "profiles.yaml").read_text(encoding="utf-8"))
    return _PROFILES


def column_profiles() -> dict[str, dict]:
    return profiles().get("columns", {}) or {}


def norm_key(key: str) -> str:
    """Header / tag comparison key: lower-case, whitespace collapsed, BOM stripped."""
    return _WS.sub(" ", str(key).replace("﻿", "").strip()).lower()


def ris_profile(platform: str) -> dict:
    """Field -> RIS tags for a platform (``default`` with the platform's overrides)."""
    prof = dict(profiles()["default"])
    prof.update({k: v for k, v in (profiles().get(platform, {}) or {}).items()})
    return {"name": f"ris:{platform}", "fields": prof, "multi_sep": None, "ris": True}


def _tabular(fmt: str | None) -> bool:
    return fmt in (None, "csv", "tsv", "xlsx", "xls", "txt")


def detect_profile_name(headers: list[str], fmt: str | None = None) -> str | None:
    """Name of the column profile whose signature columns are all present (the most specific wins)."""
    keys = {norm_key(h) for h in headers if h}
    best, best_score = None, 0
    for name, prof in column_profiles().items():
        if name == "generic":
            continue
        pfmt = prof.get("format")
        if fmt == "bib" and pfmt != "bib":
            continue
        if _tabular(fmt) and fmt is not None and pfmt == "bib":
            continue
        sig = [norm_key(s) for s in prof.get("signature", [])]
        if sig and all(s in keys for s in sig) and len(sig) > best_score:
            best, best_score = name, len(sig)
    return best


def detect_profile(headers: list[str], fmt: str | None = None) -> tuple[str | None, str | None]:
    """``(platform, format)`` of the column profile that matches these headers, or ``(None, None)``."""
    name = detect_profile_name(headers, fmt)
    if name is None:
        return None, None
    prof = column_profiles()[name]
    return prof.get("platform"), prof.get("format")


def column_profile(name: str) -> dict:
    """A column profile by name, with ``name`` and normalised candidate columns ready for :func:`standardize`."""
    raw = column_profiles().get(name)
    if raw is None:
        raise KeyError(f"unknown column profile {name!r}")
    fields = {k: list(v or []) for k, v in (raw.get("fields") or {}).items()}
    return {"name": name, "fields": fields, "multi_sep": raw.get("multi_sep"), "ris": False,
            "platform": raw.get("platform"), "format": raw.get("format")}


def profile_for(headers: list[str], fmt: str | None = None) -> dict:
    """The column profile to use for a tabular or BibTeX file: detected by header, else ``bibtex``/``generic``."""
    name = detect_profile_name(headers, fmt)
    if name is None:
        name = "bibtex" if fmt == "bib" else "generic"
    return column_profile(name)


# ----- field access -----
def _index(rec: dict[str, list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, vals in rec.items():
        out.setdefault(norm_key(k), []).extend(vals)
    return out


def _first(idx: dict[str, list[str]], keys: list[str]) -> str | None:
    for k in keys:
        vals = idx.get(norm_key(k))
        if vals:
            v = vals[0].strip()
            if v:
                return v
    return None


def _all(idx: dict[str, list[str]], keys: list[str], sep: str | None) -> list[str]:
    for k in keys:
        vals = [v.strip() for v in idx.get(norm_key(k), []) if v.strip()]
        if sep:
            vals = [part.strip() for v in vals for part in v.split(sep) if part.strip()]
        if vals:
            return vals
    return []


def standardize(rec: dict[str, list[str]], platform: str, profile: dict | None = None) -> dict:
    """Map one export record to the standard fields.

    ``profile`` is ``None`` for RIS records (the platform's tag profile is used) or a column profile from
    :func:`profile_for` / :func:`column_profile` for tabular and BibTeX records.
    """
    prof = profile or ris_profile(platform)
    fields: dict = prof["fields"]
    sep = prof.get("multi_sep")
    idx = _index(rec)
    get = lambda f: fields.get(f, [])  # noqa: E731
    title = clean_title(_first(idx, get("title")) or "")
    abstract = (_first(idx, get("abstract")) or "").strip()
    doi = clean_doi(_first(idx, get("doi")))
    if not doi:
        scan = list(RIS_DOI_FALLBACK_TAGS) if prof.get("ris") else list(get("url"))
        for t in scan:
            for v in idx.get(norm_key(t), []):
                m = DOI_IN_TEXT.search(v)
                if m:
                    doi = clean_doi(m.group(0))
                    break
            if doi:
                break
    authors = _all(idx, get("authors"), sep)
    pages = _first(idx, get("pages")) if get("pages") else None
    if not pages:
        sp, ep = _first(idx, get("start_page")), _first(idx, get("end_page"))
        pages = f"{sp}-{ep}" if sp and ep else (sp or ep or None)
    return {
        "type": _first(idx, get("type")),
        "title": title,
        "authors": "; ".join(authors) if authors else None,
        "year": year_of(_first(idx, get("year"))),
        "journal": _first(idx, get("journal")),
        "volume": _first(idx, get("volume")),
        "issue": _first(idx, get("issue")),
        "pages": pages,
        "doi": doi,
        "abstract": abstract or None,
        "keywords": "; ".join(_all(idx, get("keywords"), sep)) or None,
        "url": _first(idx, get("url")),
        "language": _first(idx, get("language")),
        "native_id": _first(idx, get("native_id")),
        "title_norm": norm_title(title),
        "doi_norm": doi,
        "first_author": first_author_surname(authors) or None,
    }
