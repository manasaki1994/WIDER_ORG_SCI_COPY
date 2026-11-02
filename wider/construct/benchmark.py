"""Benchmark papers: sync benchmark.csv into the store and fetch abstracts by DOI."""
from __future__ import annotations

import json

from ..core.lookups import lookup_client
from ..core.project import Project
from ..core.provenance import utcnow
from ..core.text import word_count
from ..enrich.sources import fetch_abstract


def sync(project: Project) -> list[dict]:
    """Upsert benchmark.csv rows into benchmark_papers (keeps fetched abstracts). Returns the rows."""
    papers = project.read_benchmark()
    st = project.store
    with st.tx() as c:
        for b in papers:
            c.execute(
                "INSERT INTO benchmark_papers(benchmark_id,title,doi,set_name,year,abstract,abstract_words,source,added_at,"
                "validation_since,validation_since_unlock,was_development,keywords,keywords_from) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(benchmark_id) DO UPDATE SET title=excluded.title, "
                "doi=excluded.doi, set_name=excluded.set_name, year=COALESCE(excluded.year, benchmark_papers.year), "
                "source=excluded.source, added_at=excluded.added_at, validation_since=excluded.validation_since, "
                "validation_since_unlock=excluded.validation_since_unlock, was_development=excluded.was_development, "
                "keywords=excluded.keywords, keywords_from=excluded.keywords_from",
                (b.benchmark_id, b.title, b.doi, b.set, b.year, b.abstract, word_count(b.abstract) if b.abstract else None,
                 b.source, b.added_at or None, b.validation_since or None, b.validation_since_unlock,
                 int(b.was_development), b.keywords or None, b.keywords_from or None))
        ids = [b.benchmark_id for b in papers]
        if ids:
            c.execute(f"DELETE FROM benchmark_papers WHERE benchmark_id NOT IN ({','.join('?' * len(ids))})", ids)
        else:
            c.execute("DELETE FROM benchmark_papers")
    return [dict(r) for r in st.query("SELECT * FROM benchmark_papers ORDER BY set_name, benchmark_id")]


def fetch(project: Project, only_missing: bool = True, min_words: int | None = None, progress=None,
          client=None) -> list[dict]:
    """Fetch abstracts for benchmark papers; never overwrites an abstract of at least ``min_words`` words.
    Needs online-lookup consent, checked before anything is sent or changed."""
    with lookup_client(project, "seed_abstracts", client) as client:
        return _fetch(project, only_missing, min_words, progress, client)


def _fetch(project: Project, only_missing: bool, min_words: int | None, progress, client) -> list[dict]:
    min_words = min_words or project.config.protocol.corpus.thin_words
    rows = sync(project)
    st = project.store
    out = []
    for i, r in enumerate(rows, 1):
        have = r["abstract_words"] or 0
        if only_missing and have >= min_words:
            out.append({**r, "status": "kept"})
            continue
        found, log = fetch_abstract(r["doi"], min_words=min_words, client=client)
        if found and found.words > have:
            st.execute("UPDATE benchmark_papers SET abstract=?, abstract_source=?, abstract_words=?, year=COALESCE(year,?), "
                       "fetched_at=?, fetch_log=? WHERE benchmark_id=?",
                       (found.abstract, found.source, found.words, found.year, utcnow(), json.dumps(log), r["benchmark_id"]))
            out.append({**r, "abstract_words": found.words, "abstract_source": found.source, "status": "fetched"})
        else:
            st.execute("UPDATE benchmark_papers SET fetched_at=?, fetch_log=? WHERE benchmark_id=?",
                       (utcnow(), json.dumps(log), r["benchmark_id"]))
            out.append({**r, "status": "not_found" if not r["doi"] or not found else "too_short"})
            if not found:
                from ..core.questions import raise_question
                raise_question(project, "seed_unresolved", r["benchmark_id"], r["title"] or r["benchmark_id"],
                               (f"No source found an abstract for seed paper {r['benchmark_id']} by its DOI {r['doi']}. "
                                if r["doi"] else f"Seed paper {r['benchmark_id']} has no DOI, so no abstract could be fetched. ")
                               + "Give the correct DOI, or leave it; proposals are grounded in the seed papers' abstracts.",
                               context={"doi": r["doi"], "set": r.get("set_name")}, ref=r["benchmark_id"])
        if progress:
            progress(i, len(rows), out[-1])
    return out
