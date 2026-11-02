"""Pair diagnostics and the automatic duplicate / non-duplicate rules, ported from the review's R script.

Rule names are kept verbatim so audit files stay comparable with the original pipeline.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from ..core.protocol import DedupProtocol
from ..core.text import norm_text

_DEFAULT = DedupProtocol()  # the original review's cut-offs; projects override them in project.yaml
FUZZY_CANDIDATE_MIN = _DEFAULT.jw_candidate
FUZZY_AUTO_MERGE_MIN = _DEFAULT.jw_auto_merge
DOI_CONFLICT_TITLE_THRESHOLD = _DEFAULT.doi_conflict_title
GENERIC_TITLE_MAX_WORDS = _DEFAULT.generic_title_max_words
FAMILY_RULES = [
    (re.compile(r"erratum|corrigendum|correction|retraction"), "correction"),
    (re.compile(r"dissertation|thesis"), "dissertation"),
    (re.compile(r"conference|proceeding|meeting abstract"), "conference"),
    (re.compile(r"book chapter|chapter"), "book_chapter"),
    (re.compile(r"preprint|working paper"), "preprint"),
    (re.compile(r"journal|article|jour"), "journal_article"),
]


def publication_family(pub_type: str | None) -> str:
    t = (pub_type or "").lower()
    for rx, fam in FAMILY_RULES:
        if rx.search(t):
            return fam
    return "other_or_unknown"


def jaro_winkler(a: str, b: str, p: float = 0.1) -> float:
    """Jaro–Winkler similarity with the prefix boost always applied (stringdist's default, bt=0)."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(la, lb) // 2 - 1
    ma = [False] * la
    mb = [False] * lb
    matches = 0
    for i in range(la):
        lo, hi = max(0, i - window), min(lb, i + window + 1)
        for j in range(lo, hi):
            if not mb[j] and a[i] == b[j]:
                ma[i] = mb[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    jaro = (matches / la + matches / lb + (matches - t / 2) / matches) / 3
    prefix = 0
    for i in range(min(4, la, lb)):
        if a[i] == b[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * p * (1 - jaro)


def token_cosine(a: str, b: str) -> float:
    ca, cb = Counter(a.split()), Counter(b.split())
    if not ca or not cb:
        return 0.0
    dot = sum(ca[k] * cb[k] for k in ca if k in cb)
    na = math.sqrt(sum(v * v for v in ca.values()))
    nb = math.sqrt(sum(v * v for v in cb.values()))
    return dot / (na * nb) if na and nb else 0.0


def normalize_locator(v: str | None) -> str | None:
    if v is None:
        return None
    s = re.sub(r"[^0-9a-z]", "", str(v).lower())
    return s or None


_PAGE_START = re.compile(r"[a-z]?\d+")


def page_start(pages: str | None) -> str | None:
    if not pages:
        return None
    m = _PAGE_START.search(str(pages).lower())
    return m.group(0) if m else None


def normalize_author(v: str | None) -> str | None:
    if not v:
        return None
    s = re.sub(r"[^a-z0-9]", "", norm_text(v))
    return s or None


@dataclass
class Rec:
    """Standardised record as the diagnostics see it."""

    rid: int
    title_clean: str
    doi: str | None
    first_author: str | None
    year: int | None
    journal_clean: str | None
    volume: str | None
    issue: str | None
    page_start: str | None
    article_number: str | None
    family: str
    source_file: str
    database_id: str | None
    platform: str


def diagnose(a: Rec, b: Rec, reason: str, dp: DedupProtocol | None = None) -> dict:
    dp = dp or _DEFAULT
    """All pair flags from the R script's add_pair_diagnostics, plus the two first-match rules."""
    jw = jaro_winkler(a.title_clean, b.title_clean)
    cos = token_cosine(a.title_clean, b.title_clean)
    sim = max(jw, cos)
    both = lambda x, y: x is not None and y is not None  # noqa: E731
    d = {
        "candidate_reason": reason,
        "jaro_winkler": jw, "token_cosine": cos, "title_similarity": sim,
        "first_author_match": both(a.first_author, b.first_author) and a.first_author == b.first_author,
        "both_first_authors_present": both(a.first_author, b.first_author),
        "same_year": both(a.year, b.year) and a.year == b.year,
        "year_within_one": both(a.year, b.year) and abs(a.year - b.year) <= 1,
        "years_differ_by_more_than_one": both(a.year, b.year) and abs(a.year - b.year) > 1,
        "both_years_present": both(a.year, b.year),
        "journal_match": both(a.journal_clean, b.journal_clean) and a.journal_clean == b.journal_clean,
        "volume_match": both(a.volume, b.volume) and a.volume == b.volume,
        "volume_conflict": both(a.volume, b.volume) and a.volume != b.volume,
        "issue_match": both(a.issue, b.issue) and a.issue == b.issue,
        "issue_conflict": both(a.issue, b.issue) and a.issue != b.issue,
        "page_start_match": both(a.page_start, b.page_start) and a.page_start == b.page_start,
        "page_start_conflict": both(a.page_start, b.page_start) and a.page_start != b.page_start,
        "article_number_match": both(a.article_number, b.article_number) and a.article_number == b.article_number,
        "same_source_file": a.source_file == b.source_file,
        "database_id_conflict": both(a.database_id, b.database_id) and a.database_id != b.database_id,
        "doi_match": both(a.doi, b.doi) and a.doi == b.doi,
        "doi_conflict": both(a.doi, b.doi) and a.doi != b.doi,
        "publication_family_conflict": a.family != b.family and "other_or_unknown" not in (a.family, b.family),
        "short_generic_title": len(a.title_clean.split()) <= dp.generic_title_max_words,
    }
    d["full_citation_match"] = (d["first_author_match"] and d["same_year"] and d["journal_match"]
                                and not d["publication_family_conflict"]
                                and (d["article_number_match"] or (d["volume_match"] and d["page_start_match"])))
    d["locator_match_count"] = sum(d[k] for k in ("volume_match", "issue_match", "page_start_match", "article_number_match"))
    d["citation_conflict_count"] = ((d["both_years_present"] and not d["same_year"]) + d["volume_conflict"]
                                    + d["issue_conflict"] + d["page_start_conflict"])
    d["auto_duplicate_rule"] = auto_duplicate_rule(d, dp)
    d["auto_not_duplicate_rule"] = auto_not_duplicate_rule(d, dp)
    return d


def auto_duplicate_rule(d: dict, dp: DedupProtocol | None = None) -> str | None:
    dp = dp or _DEFAULT
    exact = d["candidate_reason"] == "exact_normalized_title"
    fuzzy = d["candidate_reason"] == "fuzzy_title"
    sim = d["title_similarity"]
    if exact and d["full_citation_match"] and not d["doi_conflict"]:
        return "exact_title_full_citation"
    if exact and d["full_citation_match"] and d["doi_conflict"]:
        return "doi_alias_full_citation"
    if fuzzy and sim >= dp.jw_auto_merge and d["full_citation_match"] and not d["doi_conflict"]:
        return "high_similarity_full_citation"
    if fuzzy and sim >= dp.jw_auto_merge and d["full_citation_match"] and d["doi_conflict"]:
        return "high_similarity_doi_alias_full_citation"
    if (exact and d["same_year"] and not d["doi_conflict"] and not d["publication_family_conflict"]
            and d["locator_match_count"] >= 2
            and (not d["short_generic_title"] or d["first_author_match"] or d["journal_match"])):
        return "exact_title_same_year_two_locators"
    if (exact and not d["same_year"] and d["year_within_one"] and d["journal_match"] and d["volume_match"]
            and d["page_start_match"] and not d["doi_conflict"]):
        return "exact_title_verified_one_year_metadata_discrepancy"
    if (fuzzy and sim >= dp.jw_corroborated and d["same_year"] and (d["first_author_match"] or d["journal_match"])
            and d["locator_match_count"] >= 2 and not d["doi_conflict"] and not d["publication_family_conflict"]):
        return "fuzzy_title_same_year_two_locators"
    return None


def auto_not_duplicate_rule(d: dict, dp: DedupProtocol | None = None) -> str | None:
    dp = dp or _DEFAULT
    fuzzy = d["candidate_reason"] == "fuzzy_title"
    sim = d["title_similarity"]
    if d["years_differ_by_more_than_one"] and d["citation_conflict_count"] >= 2:
        return "publication_years_over_one_apart_with_citation_conflicts"
    if (fuzzy and d["doi_conflict"] and sim < dp.jw_corroborated and d["first_author_match"] and not d["journal_match"]
            and (d["volume_conflict"] or d["page_start_conflict"])):
        return "different_dois_and_incompatible_fuzzy_citations"
    if (fuzzy and sim < dp.jw_corroborated and d["same_year"] and d["both_first_authors_present"] and not d["first_author_match"]
            and d["journal_match"] and d["volume_match"] and d["issue_match"] and d["page_start_conflict"]):
        return "same_journal_but_different_authors_and_pages"
    if (fuzzy and sim < dp.jw_corroborated and d["same_year"] and d["first_author_match"] and not d["journal_match"]
            and d["volume_conflict"] and d["issue_conflict"] and d["page_start_conflict"]):
        return "same_author_but_different_journal_and_locators"
    if (d["same_source_file"] and d["database_id_conflict"] and d["short_generic_title"]
            and d["citation_conflict_count"] >= 2):
        return "generic_title_distinct_source_records_and_citations"
    if d["doi_conflict"] and not d["first_author_match"] and sim < dp.jw_strong_title:
        return "different_dois_and_first_authors"
    if d["doi_conflict"] and d["first_author_match"] and not d["same_year"] and (d["volume_conflict"] or d["page_start_conflict"]):
        return "different_dois_years_and_locators"
    if (fuzzy and sim < dp.jw_corroborated and not d["first_author_match"] and not d["same_year"]
            and d["both_first_authors_present"] and d["both_years_present"]):
        return "fuzzy_title_different_authors_and_years"
    return None


METHOD_PRIORITY = [
    "exact_doi", "exact_doi_full_citation_title_variant", "exact_title_full_citation",
    "verified_doi_alias_full_citation", "exact_title_two_locator_corroboration",
    "exact_title_one_year_metadata_discrepancy", "high_similarity_full_citation",
    "high_similarity_verified_doi_alias", "fuzzy_title_two_locator_corroboration",
    "exact_doi_conflict_manual_confirmed", "exact_title_manual_confirmed", "fuzzy_title_manual_confirmed",
]
RULE_TO_METHOD = {
    "exact_title_full_citation": "exact_title_full_citation",
    "doi_alias_full_citation": "verified_doi_alias_full_citation",
    "exact_title_same_year_two_locators": "exact_title_two_locator_corroboration",
    "exact_title_verified_one_year_metadata_discrepancy": "exact_title_one_year_metadata_discrepancy",
    "high_similarity_full_citation": "high_similarity_full_citation",
    "high_similarity_doi_alias_full_citation": "high_similarity_verified_doi_alias",
    "fuzzy_title_same_year_two_locators": "fuzzy_title_two_locator_corroboration",
}
