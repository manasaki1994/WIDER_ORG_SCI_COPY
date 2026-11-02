"""Abstract lookup by DOI (and title) from open sources, in the review's order:
OpenAlex → Crossref → Europe PMC → Semantic Scholar. Never overwrites an existing abstract; callers decide.

Each source returns ``None`` only when the paper is not found (HTTP 404) or has no abstract there. A lookup that could
not be completed (rate limit, another HTTP error, an unreadable answer) raises :class:`LookupFailed`; connection errors
and timeouts surface as ``httpx.HTTPError``. Callers log both as errors, so the record can be retried later."""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass

import httpx

from ..core.text import clean_doi, strip_tags, word_count

USER_AGENT = "wider/0.0.1 (literature review tool)"


def _mailto() -> str | None:
    return os.environ.get("WIDER_MAILTO") or None


@dataclass
class Found:
    source: str
    abstract: str
    title: str | None = None
    year: int | None = None
    doi: str | None = None

    @property
    def words(self) -> int:
        return word_count(self.abstract)


class LookupFailed(Exception):
    """A source could not answer (HTTP 429, another non-404 error, or an unreadable answer); retry later."""


def _json(r: httpx.Response) -> dict | None:
    """The answer's JSON; ``None`` for 404 (not found); :class:`LookupFailed` for any other failure."""
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403, 429) and _host(r) == "api.openalex.org":
        from ..core.lookups import openalex_refusal
        raise LookupFailed(f"{openalex_refusal(r.status_code)} (HTTP {r.status_code})")
    if r.status_code == 429:
        raise LookupFailed("HTTP 429 (rate limited)")
    if not 200 <= r.status_code < 300:
        raise LookupFailed(f"HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError as e:
        raise LookupFailed("unreadable answer") from e
    if not isinstance(d, dict):
        raise LookupFailed("unreadable answer")
    return d


def _host(r: httpx.Response) -> str:
    try:
        return r.request.url.host
    except (AttributeError, RuntimeError):   # a test fake without its request
        return ""


def describe_error(e: BaseException) -> str:
    """A short description of a failed lookup for the log (never a response body or a URL)."""
    if isinstance(e, LookupFailed):
        return str(e)
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, httpx.ConnectError):
        return "connection error"
    if isinstance(e, httpx.HTTPError):
        return f"network error ({type(e).__name__})"
    return f"unreadable answer ({type(e).__name__})"


def errored(log: list[dict]) -> bool:
    """True when at least one lookup in a ``fetch_abstract`` log could not be completed."""
    return any(e.get("status") == "error" for e in log)


def _client() -> httpx.Client:
    return httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT + (f" mailto:{_mailto()}" if _mailto() else "")},
                        follow_redirects=True)


def _clean(text: str | None) -> str:
    if not text:
        return ""
    t = strip_tags(str(text))
    t = re.sub(r"^\s*(abstract|summary)[:.]?\s*", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def inverted_abstract(work: dict) -> str:
    """Rebuild the abstract text from an OpenAlex ``abstract_inverted_index`` (empty string when absent)."""
    inv = work.get("abstract_inverted_index")
    if not inv:
        return ""
    positions: dict[int, str] = {}
    for word, idxs in inv.items():
        for i in idxs:
            positions[i] = word
    return _clean(" ".join(positions[i] for i in sorted(positions)))


def openalex(doi: str, client: httpx.Client) -> Found | None:
    params = {"mailto": _mailto()} if _mailto() else {}
    d = _json(client.get(f"https://api.openalex.org/works/https://doi.org/{doi}", params=params))
    if d is None:
        return None
    text = inverted_abstract(d)
    if not text:
        return None
    return Found("openalex", text, d.get("title"), d.get("publication_year"), doi)


def crossref(doi: str, client: httpx.Client) -> Found | None:
    params = {"mailto": _mailto()} if _mailto() else {}
    d = _json(client.get(f"https://api.crossref.org/works/{doi}", params=params))
    if d is None:
        return None
    m = d.get("message") or {}
    ab = m.get("abstract")
    if not ab:
        return None
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (m.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    return Found("crossref", _clean(ab), (m.get("title") or [None])[0], year, doi)


def europepmc(doi: str, client: httpx.Client) -> Found | None:
    d = _json(client.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                         params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 1}))
    if d is None:
        return None
    hits = (d.get("resultList") or {}).get("result") or []
    if not hits or not hits[0].get("abstractText"):
        return None
    h = hits[0]
    y = h.get("pubYear")
    return Found("europepmc", _clean(h["abstractText"]), h.get("title"), int(y) if y and str(y).isdigit() else None, doi)


def semantic_scholar(doi: str, client: httpx.Client) -> Found | None:
    headers = {}
    key = os.environ.get("S2_API_KEY")
    if key:
        headers["x-api-key"] = key
    r = client.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                   params={"fields": "title,abstract,year"}, headers=headers)
    if r.status_code == 429:
        time.sleep(3)
        r = client.get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                       params={"fields": "title,abstract,year"}, headers=headers)
    d = _json(r)
    if d is None or not d.get("abstract"):
        return None
    return Found("semantic_scholar", _clean(d["abstract"]), d.get("title"), d.get("year"), doi)


SOURCES = {"openalex": openalex, "crossref": crossref, "europepmc": europepmc, "semantic_scholar": semantic_scholar}
DEFAULT_ORDER = ["openalex", "crossref", "europepmc", "semantic_scholar"]


def fetch_abstract(doi: str | None, order: list[str] | None = None, min_words: int = 20,
                   client: httpx.Client | None = None) -> tuple[Found | None, list[dict]]:
    """Try each source in order; return the first abstract with at least ``min_words`` words and a log with each
    source's ``hit``, ``miss`` or ``error`` (a short description; :func:`errored` tells whether a lookup failed)."""
    doi = clean_doi(doi)
    log: list[dict] = []
    if not doi:
        return None, [{"source": "none", "status": "no_doi"}]
    own = client is None
    client = client or _client()
    try:
        best: Found | None = None
        for name in order or DEFAULT_ORDER:
            fn = SOURCES[name]
            try:
                f = fn(doi, client)
            except (httpx.HTTPError, LookupFailed, ValueError, TypeError, AttributeError) as e:
                log.append({"source": name, "status": "error", "note": describe_error(e)})
                continue
            if f is None:
                log.append({"source": name, "status": "miss"})
                continue
            log.append({"source": name, "status": "hit", "words": f.words})
            if f.words >= min_words:
                return f, log
            if best is None or f.words > best.words:
                best = f
        return best, log
    finally:
        if own:
            client.close()
