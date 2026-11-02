"""Online lookups: the per-project consent gate and the local log of outside requests.

Seed-paper resolution, seed abstract fetch and abstract enrichment send the DOI and title of each paper to outside
metadata services. :func:`require_consent` refuses before anything is sent unless the project allows online lookups
(``project.yaml`` ``online_lookups.allowed``). :func:`lookup_client` wraps the HTTP client of one lookup run, counts
the requests per service and writes one ``external_requests`` row per service on exit: service, purpose, number of
items sent, number of failed requests and time. It never stores a URL, a query or a response body.

Prior-review lookups keep their own switch (``prior_reviews.allow_openalex_lookup``, checked by
``wider.prior.reviews.require_lookup``); their requests are logged here too but do not need this consent.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

import httpx

from .project import Project
from .provenance import utcnow
from .researchers import sign

SERVICE_LABELS = {"openalex": "OpenAlex", "crossref": "Crossref", "europepmc": "Europe PMC",
                  "semantic_scholar": "Semantic Scholar"}
HOSTS = {"api.openalex.org": "openalex", "api.crossref.org": "crossref", "www.ebi.ac.uk": "europepmc",
         "api.semanticscholar.org": "semantic_scholar"}
SERVICES = {
    "seed_resolve": ["openalex", "crossref"],
    "seed_abstracts": ["openalex", "crossref", "europepmc", "semantic_scholar"],
    "enrich": ["openalex", "crossref", "europepmc", "semantic_scholar"],
    "prior_resolve": ["openalex"],
    "prior_chain": ["openalex"],
    "seed_chain": ["openalex"],
    "term_yield": ["openalex"],
    "seed_refs": ["openalex"],
    "ref_match": ["crossref"],
    "query_yield": ["openalex"],
    "query_seed_check": ["openalex"],
}
PURPOSE_LABELS = {"seed_resolve": "resolve seed papers", "seed_abstracts": "fetch seed-paper abstracts",
                  "enrich": "fetch missing abstracts", "prior_resolve": "resolve prior-review papers",
                  "prior_chain": "chain citations of prior-review papers",
                  "seed_chain": "list the works the Development seed papers cite and the works citing them",
                  "term_yield": "count the works one search term finds",
                  "seed_refs": "list the references of Development seed papers",
                  "ref_match": "match a parsed reference by its title",
                  "query_yield": "count the works the whole query finds",
                  "query_seed_check": "check which Development seed papers the whole query finds"}
PRIOR_PURPOSES = {"prior_resolve", "prior_chain"}
# purposes that send a search term, not a paper: they need the term switch (``online_lookups.terms_allowed``) only
TERM_PURPOSES = {"term_yield", "query_yield"}
# purposes that send a query and Development seed DOIs: they need the term switch and the DOI consent
TERM_AND_DOI_PURPOSES = {"query_seed_check"}
# purposes whose successful answers are kept in ``lookup_cache`` (2c D16); the older purposes keep their behaviour
CACHE_PURPOSES = {"seed_chain", "term_yield", "seed_refs", "ref_match", "query_yield", "query_seed_check"}
# request parameters that page or shape an answer; they do not name a paper
_PAGING = {"cursor", "page", "per-page", "per_page", "rows", "select", "mailto", "api_key", "fields", "format", "resultType",
           "limit", "offset"}


def _mailto() -> str | None:
    from ..enrich.sources import _mailto as m
    return m()


def fields_sent() -> list[str]:
    """What is sent for each paper: DOI and title, plus the contact address when ``WIDER_MAILTO`` is set."""
    return ["DOI", "title"] + (["contact address (WIDER_MAILTO)"] if _mailto() else [])


def services_for(purpose: str) -> list[str]:
    if purpose not in SERVICES:
        raise ValueError(f"unknown lookup purpose {purpose!r}; expected one of {', '.join(SERVICES)}")
    return list(SERVICES[purpose])


def service_labels(purpose: str | None = None) -> list[str]:
    keys = services_for(purpose) if purpose else list(SERVICE_LABELS)
    return [SERVICE_LABELS[k] for k in keys]


# The DOI consent is one switch for the project: given for the seed check, it covers the other DOI lookups too.
SEED_CHECK_SCOPE = ("Allowing turns on online lookups for this whole project: enrichment and seed lookups then send "
                    "the DOI and title of each paper without asking again.")
# The term switch asked for the seed check: what the whole run sends, though the DOIs also wait for the DOI consent.
SEED_CHECK_TERMS = ("The seed DOIs are sent only with the online-lookup consent as well; it is asked next if it is "
                    "off.")


class ConsentRequired(PermissionError):
    """Online lookups are not allowed for this project; nothing was sent."""

    def __init__(self, purpose: str):
        self.purpose = purpose
        self.services = service_labels(purpose)
        self.fields = fields_sent()
        self.note = ""
        what = f"the {' and '.join(self.fields[:2])} of each paper"
        if purpose == "query_seed_check":   # D27: the seeds' DOIs go with the query string, no title
            self.fields = ["DOI of each Development seed paper", "query"] + self.fields[2:]
            what = "the DOI of each Development seed paper (never a Validation paper) with the query"
            self.note = SEED_CHECK_SCOPE
        super().__init__(
            f"Online lookups are not allowed for this project, so nothing was sent. To {PURPOSE_LABELS[purpose]}, "
            f"WIDER sends {what}"
            + (" and your contact address (WIDER_MAILTO)" if len(self.fields) > 2 else "")
            + f" to {', '.join(self.services)}. Allow online lookups with `wider lookups allow` or on the Settings page."
            + (f" {self.note}" if self.note else ""))

    def payload(self) -> dict[str, Any]:
        out = {"purpose": self.purpose, "services": self.services, "fields": self.fields}
        return {"message": str(self), "consent_required": out | ({"note": self.note} if self.note else {})}


class TermsRequired(ConsentRequired):
    """The term switch is off; nothing was sent. Asked for on its own: the DOI consent neither gives nor needs it."""

    def __init__(self, purpose: str):
        self.purpose = purpose
        self.services = service_labels(purpose)
        self.fields = ["search terms or a query"]
        self.note = ""
        what = "search terms or a query"
        if purpose == "query_seed_check":   # name everything the run sends, not only what this switch covers
            self.fields = ["query", "DOI of each Development seed paper (never a Validation paper)"]
            what, self.note = "the query and the DOI of each Development seed paper (never a Validation paper)", \
                SEED_CHECK_TERMS
        PermissionError.__init__(
            self, f"Search-term lookups are off for this project, so nothing was sent. To {PURPOSE_LABELS[purpose]}, "
            f"WIDER sends {what} to OpenAlex; turn on with `wider lookups terms allow` or the term "
            f"switch on the Settings page." + (f" {self.note}" if self.note else ""))

    def payload(self) -> dict[str, Any]:
        out = super().payload()
        out["consent_required"]["switch"] = "terms"
        return out


def allowed(project: Project) -> bool:
    return bool(project.config.online_lookups.allowed)


def require_consent(project: Project, purpose: str) -> None:
    """Raise :class:`ConsentRequired` unless the project allows online lookups (prior-review purposes are exempt:
    they depend on their own switch only). A term purpose needs the term switch only and raises
    :class:`TermsRequired` without it (owner's answer to D0c Q1, 20 Oct 2026: the researcher decides)."""
    services_for(purpose)
    if purpose in PRIOR_PURPOSES:
        return
    if purpose in TERM_PURPOSES or purpose in TERM_AND_DOI_PURPOSES:
        if not project.config.online_lookups.terms_allowed:
            raise TermsRequired(purpose)
        if purpose in TERM_PURPOSES:
            return
    if not allowed(project):
        raise ConsentRequired(purpose)


def allow(project: Project, author: str | None = None) -> None:
    """Store consent in ``project.yaml`` (journalled like any settings change)."""
    ol = project.config.online_lookups
    if ol.allowed:
        return
    ol.allowed, ol.allowed_at, ol.allowed_by = True, utcnow(), sign(project, author)   # a listed researcher (D2)
    project.save()


def not_stored_message(e: OSError) -> str:
    """Plain words for a consent that could not be written (page and CLI say the same)."""
    return f"the consent was not stored: project.yaml could not be written ({e.strerror or e})"


def revoke(project: Project) -> None:
    ol = project.config.online_lookups
    if not ol.allowed and ol.allowed_at is None:
        return
    ol.allowed, ol.allowed_at, ol.allowed_by = False, None, None
    project.save()


def allow_terms(project: Project, author: str | None = None) -> None:
    """Turn the term switch on (stored in ``project.yaml``, journalled); the DOI consent is left as it is."""
    ol = project.config.online_lookups
    if ol.terms_allowed:
        return
    ol.terms_allowed, ol.terms_allowed_at, ol.terms_allowed_by = True, utcnow(), sign(project, author)
    project.save()


def revoke_terms(project: Project) -> None:
    ol = project.config.online_lookups
    if not ol.terms_allowed and ol.terms_allowed_at is None:
        return
    ol.terms_allowed, ol.terms_allowed_at, ol.terms_allowed_by = False, None, None
    project.save()


def status(project: Project) -> dict[str, Any]:
    ol = project.config.online_lookups
    return {"allowed": ol.allowed, "allowed_at": ol.allowed_at, "allowed_by": ol.allowed_by,
            "terms_allowed": ol.terms_allowed, "terms_allowed_at": ol.terms_allowed_at,
            "terms_allowed_by": ol.terms_allowed_by,
            "services": service_labels(), "fields": fields_sent(),
            "purposes": {k: {"label": PURPOSE_LABELS[k], "services": service_labels(k)} for k in SERVICES},
            "prior_review_lookup": bool(project.config.prior_reviews.allow_openalex_lookup),
            "cached_responses": cache_count(project),
            "seed_passages": _seed_passages(project)}


def _seed_passages(project: Project) -> dict[str, Any]:
    """The seed-PDF passages switch (2b), shown with the lookups because it is the other thing that may leave."""
    from ..seeds import policy

    s = policy.status(project)
    return {k: s[k] for k in ("passages_to_cloud", "passage_tokens", "lets_out", "passages_allowed")}


def cache_count(project: Project) -> int:
    return project.store.conn.execute("SELECT COUNT(*) FROM lookup_cache").fetchone()[0]


def clear_cache(project: Project) -> int:
    """Empty ``lookup_cache``; returns how many answers were removed. The next run requests again."""
    n = cache_count(project)
    project.store.conn.execute("DELETE FROM lookup_cache")
    return n


def log(project: Project, limit: int = 50) -> list[dict[str, Any]]:
    """Newest outside requests first."""
    rows = project.store.conn.execute(
        "SELECT id, at, service, purpose, n_items, n_failed FROM external_requests ORDER BY id DESC LIMIT ?",
        (max(int(limit), 0),)).fetchall()
    return [{**dict(r), "service_label": SERVICE_LABELS.get(r["service"], r["service"])} for r in rows]


def _service(url: Any) -> str:
    host = (urlsplit(str(url)).hostname or "").lower()
    return HOSTS.get(host, host or "unknown")


def _items(url: Any, params: Any) -> list[str]:
    """The papers named by one request: each ``|``-joined value of an OpenAlex ``filter``, else the request itself."""
    p = dict(params or {})
    flt = p.get("filter")
    if isinstance(flt, str) and ":" in flt and "," not in flt:
        key, _, val = flt.partition(":")
        return [f"{key}:{v}" for v in val.split("|") if v]
    path = urlsplit(str(url)).path
    rest = tuple(sorted((k, str(v)) for k, v in p.items() if k not in _PAGING))
    return [repr((path, rest))]


def cache_key(service: str, method: str, url: Any, params: Any, purpose: str) -> str:
    """sha256 of (service, method, URL path, sorted query parameters, purpose); the contact address is left out (it
    does not change the answer)."""
    p = sorted((str(k), str(v)) for k, v in dict(params or {}).items() if k not in ("mailto", "api_key"))
    raw = json.dumps([service, method.upper(), urlsplit(str(url)).path, p, purpose])
    return hashlib.sha256(raw.encode()).hexdigest()


class CachedResponse:
    """A stored 2xx answer, served instead of a request: what ``_get_json`` and its peers read of a response."""

    status_code = 200
    is_success = True

    def __init__(self, body: str, fetched_at: str | None = None):
        self.text = body
        self.content = body.encode()
        self.fetched_at = fetched_at

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        return self


# an OpenAlex API key, read from this environment variable when a request is sent; never stored, logged, cached or
# printed (the request kept with an answer has it removed)
OPENALEX_KEY_ENV = "OPENALEX_API_KEY"


def _openalex_key() -> str | None:
    return os.environ.get(OPENALEX_KEY_ENV) or None


def openalex_refusal(code: int | None, now: dt.datetime | None = None) -> str:
    """Plain words for an OpenAlex refusal that is about its budget or a key (HTTP 429, 401, 403); "" otherwise.
    The status is not part of the words: the callers add it."""
    key = _openalex_key() is not None
    if code == 429:
        now = now or dt.datetime.now(dt.UTC)
        left = int((dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), dt.UTC) - now)
                   .total_seconds() // 60)
        reset = f"it resets at midnight UTC (in {left // 60} h {left % 60} min)"
        if key:
            return (f"OpenAlex's daily budget for the API key in {OPENALEX_KEY_ENV} is spent, or the requests came too "
                    f"fast; {reset}")
        return (f"OpenAlex's free daily budget is spent: without an API key, requests count against a budget shared by "
                f"everyone on this network, and {reset}. An API key of your own from openalex.org, set in the "
                f"environment variable {OPENALEX_KEY_ENV}, has a budget of its own")
    if code in (401, 403):
        if key:
            return f"OpenAlex refused the API key in {OPENALEX_KEY_ENV}"
        return (f"OpenAlex asks for an API key for this request (paging past the first page needs one): set one in the "
                f"environment variable {OPENALEX_KEY_ENV}")
    return ""


def _without_key(r: Any) -> None:
    """Remove the API key from the request kept with an answer, so no error message or log can show it."""
    try:
        req = r.request
    except (AttributeError, RuntimeError):   # a test fake, or an answer with no request
        return
    if isinstance(req, httpx.Request) and "api_key" in req.url.params:
        r.request = httpx.Request(req.method, req.url.copy_remove_param("api_key"))


class LoggingClient:
    """Wraps any object with ``.get`` (an ``httpx.Client`` or a test fake) and counts requests per service. Given a
    project and a purpose in :data:`CACHE_PURPOSES`, it answers a request seen before from ``lookup_cache`` (no
    request, nothing logged) and stores each new 2xx answer."""

    def __init__(self, client: Any, project: Project | None = None, purpose: str | None = None):
        self._client = client
        self._store = project.store if project is not None and purpose in CACHE_PURPOSES else None
        self._purpose = purpose
        self.items: dict[str, set[str]] = {}
        self.failed: dict[str, int] = {}
        self.cache_hits = 0
        self.requests = 0

    def get(self, url, *args, **kwargs):
        params = kwargs.get("params", args[0] if args else None)
        svc = _service(url)
        key = cache_key(svc, "GET", url, params, self._purpose or "") if self._store is not None else None
        if key:
            hit = self._store.conn.execute("SELECT body, fetched_at FROM lookup_cache WHERE key=?",
                                           (key,)).fetchone()
            if hit is not None:
                self.cache_hits += 1
                return CachedResponse(hit["body"], hit["fetched_at"])
        self.items.setdefault(svc, set()).update(_items(url, params))
        self.failed.setdefault(svc, 0)
        self.requests += 1
        if svc == "openalex" and _openalex_key():   # added here, after the cache key and the log items are taken
            kwargs["params"] = {**dict(params or {}), "api_key": _openalex_key()}
            args = ()
        try:
            r = self._client.get(url, *args, **kwargs)
        except Exception:
            self.failed[svc] += 1
            raise
        _without_key(r)
        code = getattr(r, "status_code", 200)
        if isinstance(code, int) and (code == 429 or code >= 500):
            self.failed[svc] += 1
        if key and isinstance(code, int) and 200 <= code < 300:
            self._store.conn.execute("INSERT OR REPLACE INTO lookup_cache(key, body, fetched_at) VALUES (?,?,?)",
                                     (key, r.text, utcnow()))
        return r

    def __getattr__(self, name):
        return getattr(self._client, name)

    def rows(self) -> list[tuple[str, int, int]]:
        return [(svc, len(items), self.failed.get(svc, 0)) for svc, items in self.items.items()]


@contextlib.contextmanager
def lookup_client(project: Project, purpose: str, client: Any = None) -> Iterator[LoggingClient]:
    """Open the client for one lookup run: consent is checked first (nothing is sent without it), the client is
    created when none is given (and closed on exit), and one ``external_requests`` row per service contacted is
    written on exit, also when the run fails."""
    require_consent(project, purpose)
    own = client is None
    if own:
        from ..enrich.sources import _client
        client = _client()
    wrapped = client if isinstance(client, LoggingClient) else LoggingClient(client, project, purpose)
    try:
        yield wrapped
    finally:
        if wrapped is not client:  # a nested call reuses the outer wrapper, which logs once
            at = utcnow()
            for svc, n_items, n_failed in wrapped.rows():
                project.store.conn.execute(
                    "INSERT INTO external_requests(at, service, purpose, n_items, n_failed) VALUES (?,?,?,?,?)",
                    (at, svc, purpose, n_items, n_failed))
        if own:
            client.close()
