"""Text normalisation shared by matching, deduplication, ranking and screening.

Ported from the R helpers of the original review (clean_doi, norm_title, word counts).
"""
from __future__ import annotations

import re
import unicodedata

from anyascii import anyascii

_DOI_PREFIX = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_LABEL = re.compile(r"^doi:\s*", re.IGNORECASE)
_DOI_PATTERN = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_TAGS = re.compile(r"<[^>]+>")


def clean_doi(value: str | None) -> str | None:
    """Return a lower-case bare DOI (``10.xxxx/...``) or ``None``.

    Strips URL prefixes, ``doi:`` labels, surrounding whitespace and trailing punctuation.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = _DOI_PREFIX.sub("", s)
    s = _DOI_LABEL.sub("", s)
    m = _DOI_PATTERN.search(s)
    if not m:
        return None
    doi = m.group(0).rstrip(".,;:)]}>\"'")
    return doi.lower()


def strip_tags(value: str) -> str:
    return _TAGS.sub(" ", value)


def norm_text(value: str | None) -> str:
    """Lower-case ASCII, alphanumerics and single spaces only. Used for exact-title matching."""
    if value is None:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = anyascii(s).lower()
    s = strip_tags(s)
    s = _NON_ALNUM.sub(" ", s)
    return _WS.sub(" ", s).strip()


def clean_title(value: str | None) -> str:
    """Title with HTML tags removed, whitespace collapsed, trailing period dropped. Keeps case."""
    if value is None:
        return ""
    s = strip_tags(str(value))
    s = _WS.sub(" ", s).strip()
    return s.removesuffix(".")


def norm_title(value: str | None) -> str:
    return norm_text(clean_title(value))


def word_count(value: str | None) -> int:
    if not value:
        return 0
    return len(_WS.split(str(value).strip())) if str(value).strip() else 0


def truncate_words(value: str, max_words: int) -> str:
    words = str(value).split()
    if len(words) <= max_words:
        return str(value)
    return " ".join(words[:max_words])


def is_thin(abstract: str | None, min_words: int = 20) -> bool:
    """A record is 'thin' when its abstract has fewer than ``min_words`` words (the review used 20)."""
    return word_count(abstract) < min_words


# Generic UK/US spelling pairs that matter in synonym blocks (topic-neutral). Pairs specific to a construct's
# vocabulary belong in the project's synonyms artifact, not here.
UK_US_PAIRS: tuple[tuple[str, str], ...] = (
    ("behaviour", "behavior"),
    ("behavioural", "behavioral"),
    ("organisation", "organization"),
    ("organisational", "organizational"),
    ("judgement", "judgment"),
    ("labour", "labor"),
    ("favour", "favor"),
    ("favourable", "favorable"),
    ("recognise", "recognize"),
    ("analyse", "analyze"),
    ("centre", "center"),
    ("programme", "program"),
    ("honour", "honor"),
    ("rumour", "rumor"),
    ("defence", "defense"),
    ("licence", "license"),
    ("criticise", "criticize"),
    ("emphasise", "emphasize"),
    ("mobilise", "mobilize"),
    ("socialisation", "socialization"),
    ("utilisation", "utilization"),
)


def uk_us_variants(term: str) -> list[str]:
    """Return the term plus its UK/US spelling variants (deduplicated, order preserved)."""
    out = [term]
    low = term.lower()
    for uk, us in UK_US_PAIRS:
        if uk == us:
            continue
        for a, b in ((uk, us), (us, uk)):
            if a in low:
                def _swap(m, b=b):
                    src = m.group(0)
                    return b.capitalize() if src[:1].isupper() else b
                v = re.sub(re.escape(a), _swap, term, flags=re.IGNORECASE)
                if v not in out:
                    out.append(v)
    return out


def first_author_surname(authors: list[str] | str | None) -> str:
    """Lower-case ASCII surname of the first author from RIS-style ``Surname, Given`` strings."""
    if not authors:
        return ""
    first = authors[0] if isinstance(authors, list) else str(authors).split(";")[0]
    surname = first.split(",")[0].strip()
    return norm_text(surname)


def year_of(value: str | int | None) -> int | None:
    if value is None:
        return None
    m = re.search(r"(19|20)\d{2}", str(value))
    return int(m.group(0)) if m else None
