"""Term yield (change 2c, design D16): how many works one search term finds on OpenAlex, asked for one term at a time
under the term switch (``online_lookups.terms_allowed``, purpose ``term_yield``). Only the term leaves the computer
(plus the contact address when ``WIDER_MAILTO`` is set). The count informs and changes nothing: it is not stored
outside the response cache, which answers the same term again without a request.

The endpoint and the field were checked by hand by the author on 23 October 2026 (task 5.2a):
``GET https://api.openalex.org/works?search=<term>&per-page=1`` answers with the total in ``meta.count``, no key and
no charge. Only that field is read.
"""
from __future__ import annotations

import re
from typing import Any

from ..core.lookups import lookup_client
from ..core.project import Project
from ..core.provenance import utcnow
from .ir import term_words

WORKS = "https://api.openalex.org/works"
SOURCE = "OpenAlex"
NOT_AVAILABLE = "not available"


def search_text(term: str) -> str:
    """What is sent for a term: the term without its truncation stars (OpenAlex search has no wildcard; it stems)."""
    return " ".join(str(term or "").replace("*", " ").split())


def block_terms(q: Any) -> list[dict[str, Any]]:
    """Each block's terms for the Yield column: a word or phrase can be counted, a proximity group cannot (OpenAlex
    search has no proximity operator), so it is listed as not counted."""
    return [{"key": b.key, "name": b.name or b.key,
             "terms": [{"term": t, "countable": bool(search_text(t))} if isinstance(t, str)
                       else {"term": " ".join(term_words(t)), "countable": False} for t in b.terms]}
            for b in q.blocks]


def _count(r: Any) -> int | None:
    if r is None or not (200 <= getattr(r, "status_code", 0) < 300):
        return None
    try:
        n = r.json()["meta"]["count"]
    except (ValueError, KeyError, TypeError):
        return None
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def line(res: dict[str, Any]) -> str:
    """The sentence the CLI prints and the Yield column shows."""
    if res["count"] is None:
        return NOT_AVAILABLE + (f": {res['reason']}" if res.get("reason") else "")
    return f"{res['count']} works on {SOURCE}, {res['date']}" + (", cached" if res["cached"] else "")


def term_yield(project: Project, term: str, client: Any = None) -> dict[str, Any]:
    """Count the works matching ``term`` on OpenAlex. Raises ``TermsRequired`` before anything is sent while the term
    switch is off, ``ValueError`` for an empty term. A failed request gives ``count`` None and is logged as failed."""
    from ..prior.chain import _mailto

    text = search_text(term)
    if not text:
        raise ValueError("an empty term cannot be counted")
    params: dict[str, Any] = {"search": text, "per-page": 1, "select": "id"}   # the count only: no title cached
    if _mailto():
        params["mailto"] = _mailto()
    with lookup_client(project, "term_yield", client) as c:   # the term switch first: nothing is sent without it
        try:
            r = c.get(WORKS, params=params)
        except Exception:   # noqa: BLE001 - any failed request reads "not available"
            r = None   # counted as failed by the logging client
        cached = c.cache_hits > 0
        n = _count(r)
        if n is None and r is not None and not cached and getattr(r, "status_code", 0) < 500 \
                and getattr(r, "status_code", 0) != 429:
            c.failed["openalex"] = c.failed.get("openalex", 0) + 1   # a 4xx or an answer without the count
    at = getattr(r, "fetched_at", None) if cached else None
    res = {"term": term, "sent": text, "count": n, "cached": cached, "source": SOURCE,
           "date": str(at or utcnow())[:10], "reason": _reason(r) if n is None and r is not None else ""}
    res["line"] = line(res)
    return res


# ----------------------------------------------------------------- the whole query on OpenAlex (D27)
NOT_COMPARABLE = ("not comparable with a per-term count: a term is counted with OpenAlex's general search, the query "
                  "with its title-and-abstract search")
OA_MISS = "dry run hit, OpenAlex miss"
OA_ONLY = "dry run miss, OpenAlex hit"
NO_DOI = "not checked: no DOI"
SHARED_DOI = "not checked: a Validation paper has the same DOI"
NO_ANSWER = "not checked: OpenAlex did not answer in full"
NOT_ONE_DOI = "not checked: the DOI cell holds more than one DOI"
ONE_DOI = re.compile(r"10\.\d{4,9}/[^\s|,;]+")
BATCH = 50   # DOIs per request; PAGE results per page, so every work a batch matches fits on one page
PAGE = 200


def query_line(res: dict[str, Any]) -> str:
    """The count sentence for the whole query: the count, the field it was asked in, the date. A query sent in parts
    gives a range; a count that is not available says why when the reason is known."""
    rng = res.get("range")
    if rng:
        return (f"between {rng['low']} and {rng['high']} works on {SOURCE} ({res['field']}), {res['date']}"
                + (", cached" if res["cached"] else "") + f": {SPLIT_NOTE.format(n=rng['parts'])}")
    if res["count"] is None:
        return NOT_AVAILABLE + (f": {res['reason']}" if res.get("reason") else "")
    return (f"{res['count']} works on {SOURCE} ({res['field']}), {res['date']}"
            + (", cached" if res["cached"] else ""))


# OpenAlex refuses a request URL over 8,190 bytes ("Request URL too long", HTTP 400; seen 31 Oct 2026). A long query
# is sent in parts: one block's OR list is cut, and each part is the whole query with that block's share.
URL_LIMIT = 8190
URL_BUDGET = 8000   # what WIDER builds, leaving room for the client's own changes
SPLIT_NOTE = ("the query is longer than one OpenAlex request allows, so it was sent in {n} parts that each keep one "
              "share of a block's terms; the largest part's count is the lower bound and the sum of the parts' counts "
              "the upper bound (a work found by two parts is counted twice)")
TOO_LONG = ("the query is too long for one OpenAlex request ({n:,} bytes; the limit is 8,190) and cannot be sent in "
            "parts (no block can be cut)")


def url_bytes(params: dict[str, Any]) -> int:
    """The length of the request URL ``params`` make on the works endpoint, as encoded when sent."""
    import httpx

    return len(str(httpx.Request("GET", WORKS, params=params).url).encode())


def _splittable(logic: Any) -> set[str]:
    """Block keys the query's result is a union over when the block's OR list is cut: each used once, never under a
    NOT (then the query is (block AND P) OR Q for some P and Q, and the union of the parts is the whole result)."""
    seen: dict[str, int] = {}
    negated: set[str] = set()

    def walk(node: Any, neg: bool) -> None:
        if isinstance(node, str):
            seen[node] = seen.get(node, 0) + 1
            if neg:
                negated.add(node)
            return
        (op, val), = node.items()
        for k in ([val] if op == "not" else val):
            walk(k, neg or op == "not")

    walk(logic, False)
    return {k for k, n in seen.items() if n == 1 and k not in negated}


def query_parts(q: Any, fits: Any) -> list[Any] | None:
    """The query as parts that each ``fits`` (a test on a Query), whose results' union is the query's result: the
    longest cuttable block of a part that does not fit is cut in two, until every part fits. None when a part that
    does not fit has no block left to cut. A query that fits is one part."""
    from .render.openalex import _block

    if fits(q):
        return [q]
    cut = [b for b in q.blocks if b.key in _splittable(q.logic) and len(b.terms) > 1]
    if not cut:
        return None
    b = max(cut, key=lambda x: len(_block(x)))
    half = len(b.terms) // 2
    out: list[Any] = []
    for terms in (b.terms[:half], b.terms[half:]):
        part = q.model_copy(update={"blocks": [x.model_copy(update={"terms": terms}) if x.key == b.key else x
                                               for x in q.blocks]})
        sub = query_parts(part, fits)
        if sub is None:
            return None
        out += sub
    return out


def _reason(r: Any) -> str:
    """Why OpenAlex refused a request: plain words for a spent budget or a missing key, else its own words (its JSON
    ``message`` or ``error``), when it gives them."""
    from ..core.lookups import openalex_refusal
    plain = openalex_refusal(getattr(r, "status_code", None))
    if plain:
        return plain
    try:
        body = r.json()
    except Exception:   # noqa: BLE001 - no JSON body: no reason
        return ""
    if not isinstance(body, dict):
        return ""
    return " ".join(str(body.get("message") or body.get("error") or "").split())[:200]


def _dev_seeds(project: Project) -> list[tuple[Any, str | None, str]]:
    """(paper, DOI to send or None, why not) for each Development seed. A DOI that is also a Validation paper's is
    never sent."""
    from ..core.text import clean_doi

    papers = project.read_benchmark()
    held = {t for b in papers if b.set != "Development" for t in _doi_parts(b.doi)}
    out = []
    for b in papers:
        if b.set == "Development":
            d, parts = clean_doi(b.doi), _doi_parts(b.doi)
            out.append((b, None, NO_DOI) if d is None else (b, None, SHARED_DOI) if parts & held else
                       (b, None, NOT_ONE_DOI) if not ONE_DOI.fullmatch(d) else (b, d, ""))
    return out


def _doi_parts(cell: str | None) -> set[str]:
    """Every DOI-like piece of a DOI cell, so a cell like ``a|b`` is matched piece by piece."""
    from ..core.text import clean_doi

    return {d for x in re.split(r"[\s|,;]+", cell or "") if (d := clean_doi(x))}


def _dry_states(project: Project, query: Any, seeds: list) -> tuple[str | None, dict[str, str]]:
    """The local dry run of each seed on the first profiled platform (the dry run's own default)."""
    from .evaluate import NoProfile, _abstracts, default_platforms, evaluate, load_profile, seed_doc

    ab = _abstracts(project)
    for k in default_platforms(project):
        try:
            prof = load_profile(k)
        except NoProfile:
            continue
        return k, {b.benchmark_id: evaluate(query, seed_doc(b, "Development", ab.get(b.benchmark_id)), prof).state
                   for b, _, _ in seeds}
    return None, {}


def query_yield(project: Project, version: int | None = None, seeds: bool = False, client: Any = None) -> dict[str, Any]:
    """Count the works the whole query finds on OpenAlex and, with ``seeds``, which Development seeds it retrieves,
    compared per seed with the local dry run. Both consents are checked before anything is sent: the term switch
    (``TermsRequired``) and, for the seed check, the DOI consent (``ConsentRequired``). Raises ``ValueError`` without
    a synonyms version or for a query with no term. Validation seeds are never sent."""
    from ..construct import artifacts
    from ..core.lookups import require_consent
    from ..prior.chain import _mailto
    from .ir import Query
    from .render.openalex import render_openalex, rendering
    from .rounds import default_version
    from .translation import ledger_lines

    # the seed check's purpose checks the term switch first, then the DOI consent; its refusal names the seed DOIs too
    require_consent(project, "query_seed_check" if seeds else "query_yield")
    row, data = artifacts.load(project, "synonyms", version if version is not None else default_version(project))
    if row is None:
        raise ValueError(f"no synonyms version {version}" if version is not None else
                         "no synonyms version yet: propose or save one on the Construct workbench first")
    q = Query.model_validate(data)
    ren = rendering(q)
    if not ren["query"].strip("() "):
        raise ValueError("the query has no term to send")
    mail = {"mailto": _mailto()} if _mailto() else {}
    field = ren["field"]   # every part is asked in the whole query's field, so the parts add up to the query

    def params(part: Any) -> dict[str, Any]:
        return {"filter": f"{field}:{render_openalex(part)}", "per-page": 1, "select": "id", **mail}   # no title cached

    parts = query_parts(q, lambda part: url_bytes(params(part)) <= URL_BUDGET)
    res: dict[str, Any] = {"version": row["version"], "field": field, "sent": ren["query"],
                           "ledger": ren["ledger"], "ledger_lines": ledger_lines(ren["ledger"]), "count": None,
                           "cached": False, "source": SOURCE, "date": str(utcnow())[:10], "status": None,
                           "note": NOT_COMPARABLE, "seeds": None, "range": None, "parts": None, "reason": ""}
    if parts is None:   # nothing is sent: the request would be refused
        res["reason"] = TOO_LONG.format(n=url_bytes(params(q)))
        res["line"] = query_line(res)
        return res
    counts: list[int] = []
    at = None
    with lookup_client(project, "query_yield", client) as c:
        start = c.cache_hits
        for part in parts:
            before = c.cache_hits
            try:
                r = c.get(WORKS, params=params(part))
            except Exception:   # noqa: BLE001 - any failed request reads "not available"
                r = None   # counted as failed by the logging client
            n, code = _count(r), getattr(r, "status_code", 0)
            if n is None:
                if r is not None and c.cache_hits == before and code < 500 and code != 429:
                    c.failed["openalex"] = c.failed.get("openalex", 0) + 1   # a 4xx or an answer without the count
                res["status"] = code or None
                res["reason"] = _reason(r) if r is not None else ""
                break
            counts.append(n)
            at = getattr(r, "fetched_at", None) if c.cache_hits > before else None
        res["cached"] = c.cache_hits - start == len(parts)
    if res["cached"] and at:
        res["date"] = str(at)[:10]
    if len(counts) == len(parts):
        if len(parts) == 1:
            res["count"] = counts[0]
        else:
            res["range"] = {"low": max(counts), "high": sum(counts), "parts": len(parts)}
            res["parts"] = [{"sent": render_openalex(x), "count": k} for x, k in zip(parts, counts)]
    res["line"] = query_line(res)
    if seeds:
        res["seeds"] = _seed_check(project, q, field, mail, client)
    return res


def _seed_check(project: Project, q: Any, field: str, mail: dict, client: Any) -> dict[str, Any]:
    """Which Development seed DOIs the query finds on OpenAlex: DOI batches of at most :data:`BATCH`, each request
    under the URL limit; a long query is sent in parts (a DOI is found when any part finds it, and a miss needs every
    part's answer)."""
    from ..core.text import clean_doi
    from .render.openalex import render_openalex

    dev = _dev_seeds(project)
    platform, dry = _dry_states(project, q, dev)
    dois = list(dict.fromkeys(d for _, d, _ in dev if d))
    found: set[str] = set()
    unanswered: set[str] = set()

    def params(part: Any, batch: list[str]) -> dict[str, Any]:
        return {"filter": f"doi:{'|'.join(batch)},{field}:{render_openalex(part)}", "select": "id,doi",
                "per-page": PAGE, **mail}

    room = max([0] + [len(d) for d in dois]) * 3 + 2   # one DOI percent-encoded at worst, with its separator
    parts = query_parts(q, lambda part: url_bytes(params(part, [])) + room <= URL_BUDGET) if dois else []
    if parts is None:   # nothing is sent: every request would be refused
        unanswered.update(dois)
        parts = []
    if dois:
        with lookup_client(project, "query_seed_check", client) as c:
            for part in parts:
                for batch in _batches(dois, lambda bt: url_bytes(params(part, bt)) <= URL_BUDGET):
                    try:
                        r = c.get(WORKS, params=params(part, batch))
                        body = r.json() if 200 <= r.status_code < 300 else {}
                        works = body.get("results")
                        # an answer without its list, or with more matches than it lists, cannot tell a miss
                        ok = isinstance(works, list) and (body.get("meta") or {}).get("count", len(works)) <= len(works)
                    except Exception:   # noqa: BLE001 - an unanswered batch is listed as not checked
                        ok, works = False, []
                    if not ok:
                        unanswered.update(batch)
                        works = []
                    found.update(d for w in works if (d := clean_doi(w.get("doi"))))
    unanswered -= found
    rows = []
    for b, d, why in dev:
        oa = "not checked" if d is None or d in unanswered else ("hit" if d in found else "miss")
        st = dry.get(b.benchmark_id)
        note = why or (NO_ANSWER if d in unanswered else
                       OA_MISS if st == "hit" and oa == "miss" else OA_ONLY if st == "miss" and oa == "hit" else "")
        rows.append({"benchmark_id": b.benchmark_id, "title": b.title or "", "doi": d, "dry_run": st,
                     "openalex": oa, "note": note})
    return {"platform": platform, "n_sent": len(dois), "rows": rows,
            "n_hit": sum(r["openalex"] == "hit" for r in rows), "n_differ": sum(r["note"] in (OA_MISS, OA_ONLY) for r in rows)}


def _batches(dois: list[str], fits: Any) -> list[list[str]]:
    """``dois`` in order, cut into batches of at most :data:`BATCH` that each ``fits``; a DOI that fits in no batch
    alone is still sent alone (its request is then refused and the DOI listed as not checked)."""
    out: list[list[str]] = []
    cur: list[str] = []
    for d in dois:
        if cur and (len(cur) >= BATCH or not fits(cur + [d])):
            out.append(cur)
            cur = []
        cur.append(d)
    if cur:
        out.append(cur)
    return out


def query_check_lines(res: dict[str, Any]) -> list[str]:
    """What `wider query yield --query [--seeds]` prints: what was sent, the ledger, the count with its note, and
    with the seed check one line per Development seed."""
    out = [f"OpenAlex check, synonyms version {res['version']}",
           f"sent to OpenAlex ({res['field']}): {res['sent']}",
           "ledger:", *[f"  {ln}" for ln in res["ledger_lines"]],
           f"count: {res['line']}" + (f" (HTTP {res['status']})" if res["count"] is None and res["status"] else ""),
           *([("counts of the parts: " + "; ".join(f"part {i}: {x['count']}" for i, x in enumerate(res["parts"], 1)))]
             if res.get("parts") else []),
           res["note"]]
    s = res.get("seeds")
    if s is not None:
        on = f"dry run on {s['platform']}" if s["platform"] else "no dry run (no profiled platform)"
        out.append(f"Development seeds: {s['n_hit']} of {len(s['rows'])} found on OpenAlex ({on}; "
                   f"{s['n_sent']} DOIs sent, no Validation seed)")
        for r in s["rows"]:
            differs = r["note"] in (OA_MISS, OA_ONLY)
            out.append(f"  {r['benchmark_id']} {r['title'][:60]}: dry run {r['dry_run'] or '-'}, OpenAlex "
                       f"{r['openalex']}" + (" (differs: an indexing or field difference)" if differs else
                                             f" ({r['note']})" if r["note"] else ""))
    return out
