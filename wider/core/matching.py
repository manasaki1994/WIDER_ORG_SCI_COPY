"""Fuzzy matching used by benchmark coverage, deduplication and quote verification."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz.distance import JaroWinkler

# Thresholds used in the original review (see Queries/*.R and the dedup script).
from .protocol import DedupProtocol, IdentificationProtocol
from .text import clean_doi, norm_text, norm_title

JW_BENCHMARK = IdentificationProtocol().jw_benchmark  # benchmark coverage: candidate match that needs confirmation
JW_DEDUP = DedupProtocol().jw_candidate  # fuzzy duplicate when corroborated by year/author/journal


def jaro_winkler(a: str, b: str) -> float:
    """Jaro–Winkler similarity on normalised text, in [0, 1]."""
    return float(JaroWinkler.similarity(norm_text(a), norm_text(b)))


def token_jaccard(a: str, b: str) -> float:
    ta, tb = set(norm_text(a).split()), set(norm_text(b).split())
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


@dataclass(frozen=True)
class Match:
    """How a benchmark record was located in a corpus."""

    benchmark_id: str
    record_id: str | None
    method: str  # "doi" | "title" | "fuzzy" | "none"
    score: float  # 1.0 for doi/title, JW similarity for fuzzy, 0 for none
    needs_confirmation: bool


@dataclass(frozen=True)
class CorpusRecord:
    record_id: str
    title: str
    doi: str | None = None


def locate_benchmark(
    benchmark: Sequence[tuple[str, str, str | None]],
    corpus: Iterable[CorpusRecord],
    jw_threshold: float = JW_BENCHMARK,
) -> list[Match]:
    """Locate each benchmark paper (id, title, doi) in the corpus.

    Order of evidence, as in the review: DOI equality, then exact normalised title,
    then best Jaro–Winkler title similarity at or above ``jw_threshold`` (flagged for confirmation).
    """
    corpus = list(corpus)
    by_doi: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for r in corpus:
        d = clean_doi(r.doi)
        if d and d not in by_doi:
            by_doi[d] = r.record_id
        t = norm_title(r.title)
        if t and t not in by_title:
            by_title[t] = r.record_id

    out: list[Match] = []
    for bid, btitle, bdoi in benchmark:
        d = clean_doi(bdoi)
        if d and d in by_doi:
            out.append(Match(bid, by_doi[d], "doi", 1.0, False))
            continue
        t = norm_title(btitle)
        if t and t in by_title:
            out.append(Match(bid, by_title[t], "title", 1.0, False))
            continue
        best_id, best = None, 0.0
        for r in corpus:
            s = float(JaroWinkler.similarity(t, norm_title(r.title)))
            if s > best:
                best_id, best = r.record_id, s
        if best_id is not None and best >= jw_threshold:
            out.append(Match(bid, best_id, "fuzzy", best, True))
        else:
            out.append(Match(bid, None, "none", best, False))
    return out


def quote_verified(quote: str | None, text: str, min_fragment: int = 8) -> bool:
    """Is ``quote`` a verbatim copy of words in ``text``?

    The review's rule: the quote, after normalisation, must appear in the normalised title+abstract;
    quotes shorter than ``min_fragment`` characters never count. Ellipses split the quote into
    fragments that must each appear.
    """
    if not quote:
        return False
    hay = norm_text(text)
    fragments = [norm_text(f) for f in str(quote).replace("…", "...").split("...")]
    fragments = [f for f in fragments if f]
    if not fragments:
        return False
    for f in fragments:
        if len(f) < min_fragment or f not in hay:
            return False
    return True
