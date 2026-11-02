"""Fill missing or thin abstracts of unique records from open sources. Resumable; never overwrites a
usable abstract; every attempt is logged in enrichment_log, which is append-only.

Each attempt ends as ``filled``, ``exhausted`` (every source answered and none had an abstract) or ``failed`` (at least
one lookup could not be completed: no connection, timeout, rate limit, another HTTP error). A record's state is its
latest log row; a ``reset`` row (a corrected DOI) makes it a candidate again. Failed records wait for a retry."""
from __future__ import annotations

import json
import os
import time

import httpx

from ..core.lookups import lookup_client
from ..core.matching import jaro_winkler
from ..core.project import Project
from ..core.protocol import EnrichmentProtocol
from ..core.provenance import utcnow
from ..core.text import clean_title, word_count
from .abstracts import add_version
from .sources import Found, LookupFailed, _clean, _json, _mailto, describe_error, errored, fetch_abstract

TITLE_SEARCH_MIN_JW = EnrichmentProtocol().title_search_min_jw


def openalex_by_title(title: str, year: int | None, client: httpx.Client, min_jw: float = TITLE_SEARCH_MIN_JW) -> Found | None:
    """Last resort for records without a DOI: OpenAlex title search, accepted only on a near-exact title.
    Raises :class:`~wider.enrich.sources.LookupFailed` (or ``httpx.HTTPError``) when the search could not be done."""
    if not title or len(title) < 15:
        return None
    params = {"search": clean_title(title)[:300], "per-page": 5}
    if _mailto():
        params["mailto"] = _mailto()
    res = _json(client.get("https://api.openalex.org/works", params=params))
    if res is None:
        return None
    for d in res.get("results") or []:
        if not d.get("abstract_inverted_index"):
            continue
        if jaro_winkler(d.get("title") or "", title) < min_jw:
            continue
        if year and d.get("publication_year") and abs(int(d["publication_year"]) - year) > 1:
            continue
        inv = d["abstract_inverted_index"]
        pos = {i: w for w, idxs in inv.items() for i in idxs}
        text = " ".join(pos[i] for i in sorted(pos))
        doi = (d.get("doi") or "").replace("https://doi.org/", "") or None
        return Found("openalex_title", _clean(text), d.get("title"), d.get("publication_year"), doi)
    return None


MAX_QUESTIONS_PER_RUN = 50   # more unresolved records than this stay in the summary rather than flooding the questions
SETTLED = ("filled", "exhausted", "failed")   # a record whose latest attempt is one of these is not tried again
# each record's latest enrichment_log row (the log is append-only; the latest row is the record's state)
LATEST = "SELECT l.record_id, l.status, l.source FROM enrichment_log l WHERE l.id IN (SELECT MAX(id) FROM enrichment_log GROUP BY record_id)"


def candidates(project: Project, min_words: int | None = None, retry: bool = False) -> list[dict]:
    """Thin records not tried yet (or reset by a corrected DOI), in record order. With ``retry`` also the records whose
    latest attempt is ``failed`` or ``exhausted``."""
    mw = min_words or project.config.thresholds.thin_words
    skip = ("filled",) if retry else SETTLED
    return [dict(r) for r in project.store.query(
        "SELECT record_id, title, doi, year, abstract_words FROM records WHERE COALESCE(abstract_words,0) < ? "
        f"AND record_id NOT IN (SELECT record_id FROM ({LATEST}) WHERE status IN ({','.join('?' * len(skip))})) "
        "ORDER BY record_id", (mw, *skip))]


def enrich(project: Project, limit: int | None = None, pause_s: float = 0.15, progress=None,
           title_search: bool = True, retry: bool = False, client: httpx.Client | None = None) -> dict:
    """Look up abstracts for at most ``limit`` candidates (``None``: all). Needs online-lookup consent, checked before
    anything is sent or changed (:class:`~wider.core.lookups.ConsentRequired`). ``retry`` also tries failed and
    exhausted records again; their earlier log rows are kept."""
    if limit is not None and limit < 1:
        raise ValueError("the batch size (limit) must be a whole number of at least 1")
    pr = project.config.protocol
    mw = pr.corpus.thin_words
    st = project.store
    stats = {"tried": 0, "filled": 0, "exhausted": 0, "failed": 0, "by_source": {}}
    with lookup_client(project, "enrich", client) as client:
        todo = candidates(project, retry=retry)
        if limit:
            todo = todo[:limit]
        for i, r in enumerate(todo, 1):
            found, log = fetch_abstract(r["doi"], order=list(pr.enrichment.sources), min_words=mw, client=client)
            if (found is None or found.words < mw) and title_search and not r["doi"]:
                try:
                    f2 = openalex_by_title(r["title"], r["year"], client, min_jw=pr.enrichment.title_search_min_jw)
                except (httpx.HTTPError, LookupFailed, ValueError, TypeError, AttributeError) as e:
                    log.append({"source": "openalex_title", "status": "error", "note": describe_error(e)})
                else:
                    if f2 is not None:
                        log.append({"source": "openalex_title", "status": "hit", "words": f2.words})
                        if found is None or f2.words > found.words:
                            found = f2
                    else:
                        log.append({"source": "openalex_title", "status": "miss"})
            stats["tried"] += 1
            have = r["abstract_words"] or 0
            if found and found.words >= mw and found.words > have:
                st.execute("UPDATE records SET abstract=?, abstract_words=?, thin=0, abstract_source=?, "
                           "doi=COALESCE(doi,?) WHERE record_id=?",
                           (found.abstract, found.words, found.source, found.doi, r["record_id"]))
                add_version(st, r["record_id"], found.abstract, found.source)
                status = "filled"
                stats["filled"] += 1
                stats["by_source"][found.source] = stats["by_source"].get(found.source, 0) + 1
            elif errored(log):   # a service could not answer: retry later, no question
                status = "failed"
                stats["failed"] += 1
            else:
                status = "exhausted"
                stats["exhausted"] += 1
                if stats.get("questions", 0) < MAX_QUESTIONS_PER_RUN:
                    from ..core.questions import raise_question
                    how = f"its DOI {r['doi']}" if r["doi"] else "its title (it has no DOI)"
                    if raise_question(project, "enrich_unresolved", str(r["record_id"]), r["title"] or f"record {r['record_id']}",
                                      f"No source had an abstract for this record by {how}. Correct the DOI, paste the abstract, "
                                      "or leave it; ranking and screening read the abstract.",
                                      context={"doi": r["doi"], "year": r["year"]}, record_id=r["record_id"]):
                        stats["questions"] = stats.get("questions", 0) + 1
            st.execute("INSERT INTO enrichment_log(record_id,source,status,words,fetched_at,note) VALUES(?,?,?,?,?,?)",
                       (r["record_id"], found.source if found else "none", status, found.words if found else None,
                        utcnow(), json.dumps(log)))
            if progress:
                progress(i, len(todo), status)
            time.sleep(pause_s)
    stats["remaining_thin"] = st.scalar("SELECT COUNT(*) FROM records WHERE COALESCE(abstract_words,0) < ?", (mw,))
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_enrich
    record(project, summarize_enrich(stats, summary(project)))
    return stats


def s2_key_note() -> tuple[str, str]:
    """Whether ``S2_API_KEY`` is set ("set" or "not set") and the line the page and ``wider enrich --status`` show.
    Only the presence is read; the value is never shown, logged or written."""
    if os.environ.get("S2_API_KEY"):
        return "set", "S2_API_KEY set"
    return "not set", ("S2_API_KEY not set; lookups work without it, but Semantic Scholar may limit the rate, and records "
                       "it turned away wait for `wider enrich --retry`")


def summary(project: Project) -> dict:
    """Counts for the Enrich page and ``wider enrich --status``; filled, exhausted and failed count each record once,
    by its latest attempt."""
    st = project.store
    mw = project.config.thresholds.thin_words
    latest = {r["status"]: r["n"] for r in st.query(f"SELECT status, COUNT(*) AS n FROM ({LATEST}) GROUP BY status")}
    key, note = s2_key_note()
    return {
        "records": st.scalar("SELECT COUNT(*) FROM records"),
        "thin": st.scalar("SELECT COUNT(*) FROM records WHERE COALESCE(abstract_words,0) < ?", (mw,)),
        "filled": latest.get("filled", 0),
        "exhausted": latest.get("exhausted", 0),
        "failed": latest.get("failed", 0),
        "pending": len(candidates(project)),
        "by_source": {r["source"]: r["n"] for r in st.query(
            f"SELECT source, COUNT(*) AS n FROM ({LATEST}) WHERE status='filled' GROUP BY source")},
        "by_source_all": {r["abstract_source"]: r["n"] for r in st.query(
            "SELECT abstract_source, COUNT(*) AS n FROM records GROUP BY abstract_source")},
        "s2_key": key,
        "s2_key_note": note,
        # the page's words (D32 F10): no command, normal size while it changes what the researcher may do
        "s2_key_page": "Semantic Scholar API key (S2_API_KEY) is set." if key == "set" else (
            "No Semantic Scholar API key (S2_API_KEY) is set. Lookups work without it, but Semantic Scholar may limit "
            "the rate; the records it turned away can be tried again with Retry failed lookups."),
    }


# the records list of the Missing abstracts page and `wider enrich --records` (owner walkthrough M6/M7): three groups
# still without an abstract, then one group per source that found one
MISSING_GROUPS = {"waiting": "Not looked up yet", "failed": "Failed, can retry", "nowhere": "In no source"}
SOURCE_NAMES = {"openalex": "OpenAlex", "crossref": "Crossref", "europepmc": "Europe PMC",
                "semantic_scholar": "Semantic Scholar", "openalex_title": "OpenAlex by exact title"}
GROUP_NAMES = {**MISSING_GROUPS, **SOURCE_NAMES}


def _what_happened(status: str, log: list[dict], doi: str | None) -> str:
    """One plain sentence for a looked-up record: which source failed and why, or that none had an abstract."""
    def names(e: dict) -> str:
        return SOURCE_NAMES.get(e.get("source"), e.get("source") or "a source")

    errors = [f"{names(e)}: {e.get('note') or 'error'}" for e in log if e.get("status") == "error"]
    asked = [names(e) for e in log if e.get("status") in ("miss", "hit")]
    if status == "failed":
        out = "Could not be completed (" + "; ".join(errors) + ")."
        return out + (f" {', '.join(asked)} had none." if asked else "")
    if not doi:
        return "No DOI; OpenAlex has no record with this exact title."
    return f"Asked by DOI: {', '.join(asked) or 'every source'}; none has an abstract."


def records_by_state(project: Project, group: str = "waiting", limit: int = 50) -> dict:
    """The counts of every group and up to ``limit`` records of ``group`` (sorted by first author), each with its DOI
    and what happened: not looked up yet, failed and why, in no source, or found by a source (words and date)."""
    if group not in GROUP_NAMES:
        raise ValueError(f"unknown group {group!r}; groups: {', '.join(GROUP_NAMES)}")
    st = project.store
    mw = project.config.thresholds.thin_words
    latest = {r["record_id"]: dict(r) for r in st.query(
        "SELECT l.record_id, l.status, l.source, l.words, l.fetched_at, l.note FROM enrichment_log l "
        "WHERE l.id IN (SELECT MAX(id) FROM enrichment_log GROUP BY record_id)")}
    waiting = {r["record_id"] for r in candidates(project)}
    rows = {r["record_id"]: dict(r) for r in st.query(
        "SELECT record_id, title, authors, year, doi, abstract_words, abstract_source FROM records")}

    def group_of(rid: int) -> str | None:
        if rid in waiting:
            return "waiting"
        lt = latest.get(rid)
        if lt is None:
            return None
        if lt["status"] == "filled":
            return lt["source"] if lt["source"] in SOURCE_NAMES else None
        if (rows[rid]["abstract_words"] or 0) >= mw:   # filled by hand or another way since
            return None
        return "failed" if lt["status"] == "failed" else "nowhere"

    groups: dict[str, list[int]] = {k: [] for k in GROUP_NAMES}
    for rid in rows:
        g = group_of(rid)
        if g:
            groups[g].append(rid)
    def first(a: str | None) -> str:
        return (a or "").split(";")[0].split(",")[0].strip()

    picked = sorted(groups[group], key=lambda rid: (first(rows[rid]["authors"]).lower(), rid))[:max(limit, 0)]
    out = []
    for rid in picked:
        r, lt = rows[rid], latest.get(rid)
        if group == "waiting":
            what = "Not looked up yet." + ("" if r["doi"] else " No DOI, so it goes to OpenAlex by exact title.")
        elif group in MISSING_GROUPS:
            try:
                log = json.loads(lt["note"] or "[]")
            except ValueError:
                log = []
            what = _what_happened(lt["status"], log if isinstance(log, list) else [], r["doi"])
            what += f" Last tried {(lt['fetched_at'] or '')[:10]}."
        else:
            what = f"{lt['words'] or r['abstract_words'] or 0} words, {(lt['fetched_at'] or '')[:10]}"
        out.append({"record_id": rid, "first_author": first(r["authors"]), "year": r["year"], "title": r["title"],
                    "doi": r["doi"], "what": what})
    return {"group": group, "label": GROUP_NAMES[group], "labels": GROUP_NAMES,
            "counts": {k: len(v) for k, v in groups.items()},
            "missing": sum(len(groups[k]) for k in MISSING_GROUPS),
            "found": sum(len(groups[k]) for k in SOURCE_NAMES), "rows": out}


def word_count_check(text: str | None) -> int:
    return word_count(text)
