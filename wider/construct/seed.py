"""Seed (benchmark) papers added in the app: by DOI, by title, or from a CSV / RIS file.

Papers live in ``benchmark.csv`` (``Project.read_benchmark`` / ``write_benchmark``) and are mirrored into the
store's ``benchmark_papers`` table by ``construct.benchmark.sync``. ``resolve`` fills missing metadata and
abstracts from OpenAlex and Crossref, flags papers that could not be found, and marks duplicates by DOI or
normalised title. ``suggest_split`` proposes a random development / validation split: queries, prompts and
anchors are developed against the development set; the validation set is tested once, after the freeze.
"""
from __future__ import annotations

import csv
import io
import json
import random
import re
from pathlib import Path
from typing import Any

import httpx

from ..core import journal
from ..core.lookups import lookup_client, require_consent
from ..core.matching import jaro_winkler
from ..core.models import BenchmarkPaper
from ..core.project import Project
from ..core.provenance import utcnow
from ..core.text import clean_doi, clean_title, norm_title, word_count, year_of
from ..enrich.sources import _clean, _mailto
from ..ingest.ris import parse_ris
from ..ingest.standardize import standardize
from .benchmark import sync

META_KEY = "seed_resolve"
SETS = ("Development", "Validation")
SPLIT_RULE = ("Develop the construct artifacts, the query and the screening prompt against the development set only; "
              "the validation set is checked once, after the query is frozen, so that its recall is an honest test.")


SOURCES = ("prior_review", "researcher", "colleague", "other_database", "query_results")  # plus seed_chain:<id>
SOURCE_LABELS = {"query_results": "from the query's results: kept, not counted in validation"}


def source_name(value: str | None) -> str:
    """The source of a seed paper (default ``researcher``); refuses anything outside ``SOURCES`` and ``seed_chain:<id>``."""
    s = (value or "researcher").strip()
    if s in SOURCES or re.fullmatch(r"seed_chain:\S+", s):
        return s
    raise ValueError(f"unknown source {s!r}: use one of {', '.join(SOURCES)} or seed_chain:<id>")


def _set_name(value: str | None) -> str:
    s = (value or "Development").strip().lower()
    return "Validation" if s.startswith("val") else "Development"


def _next_id(papers: list[BenchmarkPaper]) -> str:
    used = {p.benchmark_id for p in papers}
    n = 0
    for p in papers:
        m = re.fullmatch(r"B0*(\d+)", p.benchmark_id)
        if m:
            n = max(n, int(m.group(1)))
    while True:
        n += 1
        cand = f"B{n:03d}"
        if cand not in used:
            return cand


# ----------------------------------------------------------------- placing a paper in a set (design D4)
FROM_QUERY_NOTICE = ("{id} came from the query results: it is kept in Validation but not counted, because a paper "
                     "the query found cannot test the query.")
USED_NOTICE = ("{id} has been shown as a validation paper: it is a used validation paper (Val (used)) and will not "
               "count in the validation set")
WAS_DEV_NOTICE = ("{id} was in Development: it is counted with Development and will not count in the "
                  "validation set")


def retrieved_by_query(project: Project, doi: str | None, title: str | None) -> dict | None:
    """The first imported record with this DOI, else this normalised title: ``{file, raw_id, platform, title}``."""
    sql = ("SELECT r.raw_id, r.platform, r.title, s.file_name FROM raw_records r JOIN sources s USING(source_id) "
           "WHERE {} ORDER BY r.raw_id LIMIT 1")
    rows = []
    d = clean_doi(doi)
    if d:
        rows = project.store.query(sql.format("r.doi_norm=?"), (d,))
    t = norm_title(title)
    if not rows and t:
        rows = project.store.query(sql.format("r.title_norm=?"), (t,))
    if not rows:
        return None
    r = rows[0]
    return {"file": r["file_name"], "raw_id": r["raw_id"], "platform": r["platform"], "title": r["title"] or ""}


def _match_warning(paper: BenchmarkPaper, rec: dict) -> str:
    return (f"{paper.benchmark_id} matches an imported record ({rec['file']}, record {rec['raw_id']}: "
            f"\"{rec['title'][:80]}\"); it is kept in Validation, but the query already finds it.")


def set_notices(project: Project, paper: BenchmarkPaper, on_entry: bool = True) -> list[str]:
    """What the seed step and `wider benchmark add|set` say beside a Validation paper (nothing is ever refused):
    from the query's results (kept, not counted), matching an imported record, a used validation paper, or counted
    with Development. The match warning is given when the paper enters Validation; on a later listing
    (``on_entry=False``) only while the set is shown, because repeating it after each import would tell, paper by
    paper, what the query finds in the hidden set."""
    if paper.set != "Validation":
        return []
    from ..search import coverage

    if paper.source == "query_results":
        return [FROM_QUERY_NOTICE.format(id=paper.benchmark_id)]
    out = []
    show_match = (on_entry or coverage.validation_unlocked(project)
                  or not project.config.protocol.identification.validation_after_freeze)
    rec = retrieved_by_query(project, paper.doi, paper.title) if show_match else None
    if rec:
        out.append(_match_warning(paper, rec))
    # the group the paper is in now (validation-reuse D1), with this paper's set as given (it may not be saved yet)
    papers = [b for b in project.read_benchmark() if b.benchmark_id != paper.benchmark_id] + [paper]
    if paper.benchmark_id in {b.benchmark_id for b in coverage.used_papers(project, papers)}:
        out.append(USED_NOTICE.format(id=paper.benchmark_id))
    elif paper.benchmark_id not in {b.benchmark_id for b in coverage.validation_papers(project, papers)}:
        out.append(WAS_DEV_NOTICE.format(id=paper.benchmark_id))
    return out


def _place_in_set(project: Project, paper: BenchmarkPaper, set_name: str, now: str,
                  entering: bool = False) -> list[str]:
    """The only code that assigns ``paper.set``. Entering Validation stamps ``validation_since`` and the current seen
    count; entering Development marks ``was_development``. Returns the notices for the paper (D4)."""
    from ..search import coverage

    target = _set_name(set_name)
    if entering or paper.set != target:
        if target == "Validation":
            paper.validation_since = now
            paper.validation_since_unlock = coverage.seen_count(project)
        else:
            paper.was_development = True
    paper.set = target
    return set_notices(project, paper)


def _append(project: Project, new: list[BenchmarkPaper], source: str = "researcher") -> list[BenchmarkPaper]:
    source = source_name(source)
    papers = project.read_benchmark()
    added = []
    now = utcnow()
    for p in new:
        p.benchmark_id = _next_id(papers)
        p.source = source
        p.added_at = now
        _place_in_set(project, p, p.set, now, entering=True)
        papers.append(p)
        added.append(p)
    project.write_benchmark(papers)
    sync(project)
    return added


# ----------------------------------------------------------------- adding
def _skip(skipped: list[dict] | None, paper: BenchmarkPaper | None, wanted_set: str, doi: str | None = None,
          title: str = "") -> None:
    """Record a row that was not added because it is already a seed (``paper``) or repeats an earlier row of the same
    input (``paper`` None). A seed already in the other set is a set conflict: it stays where it is (M8)."""
    if skipped is None:
        return
    label = doi or (title[:70] if title else "")
    if paper is None:
        reason = "repeated in the input"
    elif paper.set != _set_name(wanted_set):
        reason = (f"already seed {paper.benchmark_id} in {paper.set}; not moved to {_set_name(wanted_set)} "
                  f"(move it on the seed table)")
    else:
        reason = f"already seed {paper.benchmark_id} in {paper.set}"
    skipped.append({"doi": doi, "title": title, "label": label, "reason": reason,
                    "existing": paper.benchmark_id if paper else None,
                    "conflict": bool(paper and paper.set != _set_name(wanted_set))})


def skipped_summary(skipped: list[dict]) -> str:
    """One line for the CLI and the page: how many rows were skipped as duplicates and how many of them sit in the
    other set."""
    if not skipped:
        return ""
    conflicts = sum(1 for s in skipped if s["conflict"])
    tail = f", {conflicts} of them already in the other set (not moved)" if conflicts else ""
    return f"{len(skipped)} skipped as already a seed or repeated{tail}"


def add_by_dois(project: Project, dois: list[str], set_name: str = "Development", source: str = "researcher",
                skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    """Add one paper per DOI (URL prefixes and ``doi:`` labels are stripped); the title is filled by ``resolve``.
    Rows not added because they are already seeds go to ``skipped`` when given."""
    by_doi = {p.doi: p for p in project.read_benchmark() if p.doi}
    new, seen = [], set()
    for raw in dois:
        doi = clean_doi(raw)
        if not doi:
            continue
        if doi in by_doi or doi in seen:
            _skip(skipped, by_doi.get(doi), set_name, doi=doi)
            continue
        seen.add(doi)
        new.append(BenchmarkPaper(benchmark_id="", title="", doi=doi, set=_set_name(set_name), note="added by DOI"))
    return _append(project, new, source)


def add_by_titles(project: Project, titles: list[str], set_name: str = "Development", source: str = "researcher",
                  skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    """Add one paper per title; ``resolve`` looks the DOI up by title."""
    by_title = {norm_title(p.title): p for p in project.read_benchmark() if p.title}
    known = set(by_title)
    new = []
    for raw in titles:
        t = clean_title(raw)
        if not t:
            continue
        if norm_title(t) in known:
            _skip(skipped, by_title.get(norm_title(t)), set_name, title=t)
            continue
        known.add(norm_title(t))
        new.append(BenchmarkPaper(benchmark_id="", title=t, set=_set_name(set_name), note="added by title"))
    return _append(project, new, source)


def add_lines(project: Project, text: str, set_name: str = "Development", source: str = "researcher",
              skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    """Each non-empty line is a DOI when one can be parsed from it, otherwise a title."""
    dois, titles = [], []
    for line in text.splitlines():
        line = line.strip().strip(",;")
        if not line:
            continue
        (dois if clean_doi(line) else titles).append(line)
    source_name(source)
    return (add_by_dois(project, dois, set_name, source, skipped)
            + add_by_titles(project, titles, set_name, source, skipped))


def parse_seed_text(text: str, filename: str = "") -> list[dict[str, Any]]:
    """Rows ``{title, doi, year, first_author, set}`` from CSV text (title/doi columns) or RIS text."""
    name = (filename or "").lower()
    looks_ris = name.endswith(".ris") or re.search(r"^TY  - ", text, flags=re.M) is not None
    rows: list[dict[str, Any]] = []
    if looks_ris:
        for rec in parse_ris(text):
            s = standardize(rec, "ris")
            rows.append({"title": s.get("title") or "", "doi": s.get("doi"), "year": s.get("year"),
                         "first_author": s.get("first_author") or None, "set": None})
        return rows
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        title = row.get("title") or row.get("ti") or row.get("article title") or ""
        doi = clean_doi(row.get("doi") or row.get("di"))
        if not title and not doi:
            continue
        rows.append({"title": clean_title(title), "doi": doi, "year": year_of(row.get("year") or row.get("py")),
                     "first_author": row.get("first_author") or row.get("author") or None,
                     "set": row.get("set") or None})
    return rows


def add_rows(project: Project, rows: list[dict[str, Any]], set_name: str = "Development",
             note: str = "added from file", source: str = "researcher",
             skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    papers = project.read_benchmark()
    by_doi = {p.doi: p for p in papers if p.doi}
    by_title = {norm_title(p.title): p for p in papers if p.title}
    known_doi, known_title = set(by_doi), set(by_title)
    new = []
    for r in rows:
        doi, title = clean_doi(r.get("doi")), clean_title(r.get("title") or "")
        if not doi and not title:
            continue
        if (doi and doi in known_doi) or (not doi and norm_title(title) in known_title):
            existing = by_doi.get(doi) if doi else by_title.get(norm_title(title))
            _skip(skipped, existing, r.get("set") or set_name, doi=doi, title=title)
            continue
        if doi:
            known_doi.add(doi)
        if title:
            known_title.add(norm_title(title))
        fa = r.get("first_author")
        if isinstance(fa, list):
            fa = fa[0] if fa else None
        new.append(BenchmarkPaper(benchmark_id="", title=title, doi=doi, set=_set_name(r.get("set") or set_name),
                                  year=r.get("year"), first_author=(str(fa).split(",")[0].strip() if fa else None),
                                  note=note))
    return _append(project, new, source)


def add_from_text(project: Project, text: str, filename: str = "", set_name: str = "Development",
                  source: str = "researcher", skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    return add_rows(project, parse_seed_text(text, filename), set_name, note=f"added from {filename or 'file'}",
                    source=source, skipped=skipped)


def add_from_file(project: Project, path: str | Path, set_name: str = "Development",
                  source: str = "researcher", skipped: list[dict] | None = None) -> list[BenchmarkPaper]:
    """CSV (title/doi columns, optional set/year) or RIS file."""
    path = Path(path)
    return add_from_text(project, path.read_text(encoding="utf-8-sig", errors="replace"), path.name, set_name, source,
                         skipped)


# ----------------------------------------------------------------- lookups (mocked in tests)
def openalex_work(doi: str, client: httpx.Client) -> dict | None:
    """Metadata of one work by DOI: ``{title, year, doi, abstract}`` (abstract may be empty)."""
    params = {"mailto": _mailto()} if _mailto() else {}
    r = client.get(f"https://api.openalex.org/works/https://doi.org/{doi}", params=params)
    if r.status_code != 200:
        return None
    return _openalex_row(r.json())


def _openalex_row(d: dict) -> dict:
    inv = d.get("abstract_inverted_index") or {}
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    text = " ".join(pos[i] for i in sorted(pos))
    auth = ((d.get("authorships") or [{}])[0].get("author") or {}).get("display_name") or ""
    return {"source": "openalex", "openalex_id": d.get("id"), "title": clean_title(d.get("title") or ""), "year": d.get("publication_year"),
            "doi": clean_doi(d.get("doi")), "abstract": _clean(text), "first_author": auth.split()[-1] if auth else None}


def crossref_work(doi: str, client: httpx.Client) -> dict | None:
    params = {"mailto": _mailto()} if _mailto() else {}
    r = client.get(f"https://api.crossref.org/works/{doi}", params=params)
    if r.status_code != 200:
        return None
    m = r.json().get("message", {})
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = (m.get(k) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            year = parts[0][0]
            break
    auth = (m.get("author") or [{}])[0].get("family")
    return {"source": "crossref", "title": clean_title((m.get("title") or [""])[0]), "year": year,
            "doi": clean_doi(m.get("DOI")) or doi, "abstract": _clean(m.get("abstract")), "first_author": auth}


def openalex_search(title: str, client: httpx.Client) -> list[dict]:
    """Candidate works for a title (OpenAlex search, five best hits)."""
    params: dict[str, Any] = {"search": clean_title(title)[:300], "per-page": 5}
    if _mailto():
        params["mailto"] = _mailto()
    r = client.get("https://api.openalex.org/works", params=params)
    if r.status_code != 200:
        return []
    return [_openalex_row(d) for d in r.json().get("results", [])]


# ----------------------------------------------------------------- resolve
def _resolve_log(project: Project) -> dict:
    return project.store.unj(project.store.get_meta(META_KEY)) or {}


def _save_log(project: Project, log: dict) -> None:
    project.store.set_meta(META_KEY, project.store.j(log))


def resolve(project: Project, only_unresolved: bool = True, client: httpx.Client | None = None,
            progress=None) -> list[dict]:
    """Fill title, year, DOI and abstract from OpenAlex, then Crossref. Returns the status rows.
    Needs online-lookup consent, checked before anything is sent or changed."""
    require_consent(project, "seed_resolve")
    papers = project.read_benchmark()
    rows = {r["benchmark_id"]: r for r in sync(project)}
    log = _resolve_log(project)
    min_jw = project.config.protocol.identification.jw_benchmark
    min_words = project.config.protocol.corpus.thin_words
    changed = False
    looked: set[str] = set()   # the papers looked up in this run (the summary counts only these)
    filled: set[str] = set()   # the papers given a DOI in this run: checked as on entry into Validation
    with lookup_client(project, "seed_resolve", client) as client:
        for i, p in enumerate(papers, 1):
            row = rows.get(p.benchmark_id, {})
            complete = bool(p.title and p.doi and p.year and (row.get("abstract_words") or 0) >= min_words)
            if only_unresolved and complete:
                log[p.benchmark_id] = {**log.get(p.benchmark_id, {}), "status": "resolved"}
                continue
            found, note = None, ""
            looked.add(p.benchmark_id)
            try:
                if p.doi:
                    found = openalex_work(p.doi, client)
                    if found is None or (not found["abstract"] and not found["title"]):
                        found = crossref_work(p.doi, client) or found
                elif p.title:
                    best, best_jw = None, 0.0
                    for cand in openalex_search(p.title, client):
                        jw = jaro_winkler(cand.get("title") or "", p.title)
                        if jw > best_jw:
                            best, best_jw = cand, jw
                    if best is not None and best_jw >= min_jw:
                        found = {**best, "similarity": round(best_jw, 3)}
                    elif best is not None:
                        note = f"closest title only {best_jw:.2f} similar: {best.get('title', '')[:80]}"
            except (httpx.HTTPError, ValueError) as e:
                note = f"lookup error: {str(e)[:120]}"
            if found:
                if not p.title and found.get("title"):
                    p.title, changed = found["title"], True
                if not p.doi and found.get("doi"):
                    p.doi, changed = found["doi"], True
                    filled.add(p.benchmark_id)
                if not p.year and found.get("year"):
                    p.year, changed = int(found["year"]), True
                if not p.first_author and found.get("first_author"):
                    p.first_author, changed = found["first_author"], True
                words = word_count(found.get("abstract"))
                if words > (row.get("abstract_words") or 0):
                    project.store.execute(
                        "UPDATE benchmark_papers SET abstract=?, abstract_source=?, abstract_words=?, fetched_at=?, "
                        "fetch_log=? WHERE benchmark_id=?",
                        (found["abstract"], found["source"], words, utcnow(),
                         json.dumps([{"source": found["source"], "status": "hit", "words": words}]), p.benchmark_id))
                log[p.benchmark_id] = {"status": "resolved", "source": found["source"], "at": utcnow(),
                                       "similarity": found.get("similarity"), "note": ""}
            else:
                log[p.benchmark_id] = {"status": "unresolved", "source": None, "at": utcnow(),
                                       "note": note or ("no DOI and no title" if not p.doi and not p.title else "not found")}
            if progress:
                progress(i, len(papers), {"benchmark_id": p.benchmark_id, **log[p.benchmark_id]})
    if changed:
        project.write_benchmark(papers)
        sync(project)
    _save_log(project, log)
    return [{**r, "looked_up": r["benchmark_id"] in looked} for r in status(project, entering=filled)]


def resolve_summary(rows: list[dict]) -> dict:
    """Counts of a resolve run: k resolved and u unresolved of the n papers looked up, m already resolved (skipped)."""
    looked = [r for r in rows if r.get("looked_up")]
    k = sum(r["resolved"] == "resolved" for r in looked)
    u = sum(r["resolved"] != "resolved" for r in looked)
    m = sum(not r.get("looked_up") and r["resolved"] == "resolved" for r in rows)
    return {"resolved": k, "unresolved": u, "looked_up": len(looked), "already": m,
            "summary": f"{k} resolved, {u} unresolved of {len(looked)} looked up; {m} already resolved"}


def status(project: Project, entering: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """One row per seed paper with ``resolved`` (resolved | unresolved | pending), ``duplicate_of`` and ``notices``;
    the papers in ``entering`` (just added, or given a DOI by a resolve) get their notices as on entry."""
    rows = sync(project)
    papers = {p.benchmark_id: p for p in project.read_benchmark()}
    log = _resolve_log(project)
    min_words = project.config.protocol.corpus.thin_words
    by_doi: dict[str, str] = {}
    by_title: dict[str, str] = {}
    out = []
    for r in rows:
        d = dict(r)
        entry = log.get(r["benchmark_id"], {})
        complete = bool(r["title"] and r["doi"] and (r["abstract_words"] or 0) >= min_words)
        d["resolved"] = "resolved" if complete or entry.get("status") == "resolved" else entry.get("status", "pending")
        d["resolve_note"] = entry.get("note") or ""
        d["resolve_source"] = entry.get("source")
        dup = None
        if r["doi"]:
            dup = by_doi.get(r["doi"])
            by_doi.setdefault(r["doi"], r["benchmark_id"])
        nt = norm_title(r["title"]) if r["title"] else ""
        if nt:
            dup = dup or by_title.get(nt)
            by_title.setdefault(nt, r["benchmark_id"])
        d["duplicate_of"] = dup if dup != r["benchmark_id"] else None
        p = papers.get(r["benchmark_id"])
        d["notices"] = set_notices(project, p, on_entry=r["benchmark_id"] in entering) if p else []
        d["source_label"] = SOURCE_LABELS.get(r.get("source") or "", "")
        out.append(d)
    return out


# ----------------------------------------------------------------- assignment and split
class MoveNeedsConfirm(ValueError):
    """Moving a shown validation paper to Development needs the researcher's confirmation (D3 of validation-reuse);
    nothing was written. ``notice`` is the warning, ``confirm`` the CLI's line."""

    def __init__(self, notice: str, n: int = 1):
        super().__init__(notice)
        self.notice = notice
        self.confirm = f"Add --confirm to move {'it' if n == 1 else 'them'}. Nothing was done."


ADD_INVITE = ("Add new validation papers from sources independent of the query: prior reviews, citation chaining from "
              "seed papers, colleagues or other databases (wider benchmark add --set Validation --source <source>).")


def _names(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else f"{', '.join(ids[:-1])} and {ids[-1]}"


def move_notice(ids: list[str]) -> str:
    """The warning before shown validation papers move to Development (D3)."""
    if len(ids) == 1:
        return (f"{ids[0]} has been shown as a validation paper. Moving it to Development makes it a former validation "
                "paper: the report, the methods draft and the package list it, and the move cannot be undone.")
    return (f"{_names(ids)} have been shown as validation papers. Moving them to Development makes them former "
            "validation papers: the report, the methods draft and the package list them, and the move cannot be undone.")


def _shown_to_development(project: Project, papers: list[BenchmarkPaper], moves: dict[str, str]) -> list[str]:
    """The ids of the papers ``moves`` takes from Validation to Development that have been shown as validation papers."""
    from ..search import coverage

    going = [p for p in papers if p.benchmark_id in moves and p.set == "Validation" and p.source != "query_results"
             and _set_name(moves[p.benchmark_id]) == "Development"]
    if not going:
        return []
    shown = coverage._shown_ids(project, papers)
    return [p.benchmark_id for p in going if p.benchmark_id in shown]


def _move(project: Project, papers: list[BenchmarkPaper], moves: dict[str, str], confirm: bool, by: str | None,
          notice: str | None = None) -> list[BenchmarkPaper]:
    """Write ``moves`` ({benchmark_id: set}) as one change (D3): with a shown validation paper going to Development it
    needs ``confirm`` (else :class:`MoveNeedsConfirm`, nothing written) and is one final ``validation_moved`` change
    that lists the papers in the meta key ``validation_moved``; any other change of set is one undoable
    ``benchmark_set`` change."""
    from ..search import coverage

    shown = _shown_to_development(project, papers, moves)
    if shown and not confirm:
        raise MoveNeedsConfirm(notice or move_notice(shown), len(shown))
    changed = [p for p in papers if p.benchmark_id in moves and p.set != _set_name(moves[p.benchmark_id])]
    if not changed:
        return papers
    ids = [p.benchmark_id for p in changed]
    if shown:
        kind = "validation_moved"
        summary = (f"validation paper{'' if len(shown) == 1 else 's'} moved to Development: {', '.join(shown)}"
                   + (f"; seed paper sets changed: {', '.join(i for i in ids if i not in shown)}"
                      if len(ids) > len(shown) else ""))
    else:
        kind = "benchmark_set"
        summary = ("seed paper set changed: " if len(changed) == 1 else "seed paper sets changed: ") + ", ".join(
            f"{p.benchmark_id} {p.set} → {_set_name(moves[p.benchmark_id])}" for p in changed)
    now = utcnow()
    with journal.recording(project, kind, summary, author=by) as rec:
        rec.file("benchmark.csv").rows("benchmark_papers", f"benchmark_id IN ({','.join('?' * len(ids))})", ids)
        if shown:
            rec.rows("meta", "key=?", [coverage.MOVED_KEY])
            author = journal.current_author(by)
            entries = coverage.moved(project) + [
                {"benchmark_id": p.benchmark_id, "doi": clean_doi(p.doi) or "", "title_norm": norm_title(p.title) or "",
                 "at": now, "by": author} for p in changed if p.benchmark_id in shown]
            project.store.set_meta(coverage.MOVED_KEY, json.dumps(entries))
        for p in changed:
            _place_in_set(project, p, moves[p.benchmark_id], now)
        project.write_benchmark(papers)
        sync(project)
    _close_held_questions(project)
    return papers


def set_assignment(project: Project, benchmark_id: str, set_name: str, confirm: bool = False,
                   by: str | None = None) -> BenchmarkPaper:
    """Move one seed paper to a set (D3): a shown validation paper going to Development needs ``confirm``."""
    papers = project.read_benchmark()
    for p in papers:
        if p.benchmark_id == benchmark_id:
            _move(project, papers, {benchmark_id: set_name}, confirm, by)
            return p
    raise KeyError(f"no benchmark paper {benchmark_id!r}")


def move_used(project: Project, confirm: bool = False, by: str | None = None) -> list[str]:
    """Move every used validation paper to Development as former validation papers, in one ``validation_moved``
    change (D4); without ``confirm`` :class:`MoveNeedsConfirm`. ``ValueError`` when there is none."""
    from ..search import coverage

    papers = project.read_benchmark()
    ids = [b.benchmark_id for b in coverage.used_papers(project, papers)]
    if not ids:
        raise ValueError("no used validation paper")
    n = len(ids)
    notice = (f"Move the {n} used validation paper{'' if n == 1 else 's'} ({', '.join(ids)}) to Development? "
              f"{'It becomes a former validation paper' if n == 1 else 'They become former validation papers'}: the "
              f"report, the methods draft and the package list {'it' if n == 1 else 'them'}, and the move cannot be "
              "undone.")
    _move(project, papers, dict.fromkeys(ids, "Development"), confirm, by, notice=notice)
    return ids


def set_source(project: Project, benchmark_id: str, source: str, by: str | None = None) -> BenchmarkPaper:
    """Change where a seed paper came from; journalled ``benchmark_source`` (undo restores benchmark.csv)."""
    source = source_name(source)
    papers = project.read_benchmark()
    for p in papers:
        if p.benchmark_id == benchmark_id:
            if p.source == source:
                return p
            with journal.recording(project, "benchmark_source", f"{benchmark_id} source {p.source} → {source}",
                                   author=by) as rec:
                rec.file("benchmark.csv").rows("benchmark_papers", "benchmark_id=?", [benchmark_id])
                p.source = source
                project.write_benchmark(papers)
                sync(project)
            return p
    raise KeyError(f"no benchmark paper {benchmark_id!r}")


def keyword_text(value: str | None) -> str:
    """Keywords as WIDER stores them: separated by ";" (a new line also separates), trimmed, empty ones dropped."""
    parts = re.split(r"[;\n]", value or "")
    return "; ".join(k.strip() for k in parts if k.strip())


def set_keywords(project: Project, benchmark_id: str, keywords: str, by: str | None = None) -> BenchmarkPaper:
    """Type a seed paper's keywords (``keywords_from = researcher``; empty clears both); journalled
    ``benchmark_keywords`` (undo restores benchmark.csv). The dry run reads them for a block that searches keywords."""
    text = keyword_text(keywords)
    papers = project.read_benchmark()
    for p in papers:
        if p.benchmark_id == benchmark_id:
            src = "researcher" if text else ""
            if (p.keywords or "") == text and (p.keywords_from or "") == src:
                return p
            summary = f"{benchmark_id} keywords: {text}" if text else f"{benchmark_id} keywords cleared"
            with journal.recording(project, "benchmark_keywords", summary, author=by) as rec:
                rec.file("benchmark.csv").rows("benchmark_papers", "benchmark_id=?", [benchmark_id])
                p.keywords, p.keywords_from = text, src
                project.write_benchmark(papers)
                sync(project)
            return p
    raise KeyError(f"no benchmark paper {benchmark_id!r}")


def copy_import_keywords(project: Project, source_id: int, by: str | None = None) -> list[str]:
    """Fill the empty keywords of seed papers whose DOI matches a record of imported file ``source_id`` that carries
    keywords (``keywords_from = import``); typed keywords are never overwritten. Validation papers are left alone while
    the set is hidden: naming them would tell which hidden papers the export retrieves. Returns the seed ids filled, in
    order; journalled ``benchmark_keywords_import`` when any is filled (undo restores benchmark.csv)."""
    from ..search import coverage

    found: dict[str, str] = {}
    for r in project.store.query("SELECT doi_norm, keywords FROM raw_records WHERE source_id=? AND doi_norm IS NOT NULL "
                                 "AND doi_norm<>'' AND keywords IS NOT NULL AND keywords<>'' ORDER BY position",
                                 (source_id,)):
        found.setdefault(r["doi_norm"], keyword_text(r["keywords"]))
    papers = project.read_benchmark()
    shown = coverage.validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
    fill = [p for p in papers if not (p.keywords or "").strip() and clean_doi(p.doi) and found.get(clean_doi(p.doi))
            and (shown or p.set != "Validation")]
    if not fill:
        return []
    ids = [p.benchmark_id for p in fill]
    with journal.recording(project, "benchmark_keywords_import",
                           f"keywords copied from the import to {', '.join(ids)}", author=by) as rec:
        rec.file("benchmark.csv").rows("benchmark_papers", f"benchmark_id IN ({','.join('?' * len(ids))})", ids)
        for p in fill:
            p.keywords, p.keywords_from = found[clean_doi(p.doi)], "import"
        project.write_benchmark(papers)
        sync(project)
    return ids


def remove(project: Project, benchmark_id: str) -> None:
    papers = project.read_benchmark()
    keep = [p for p in papers if p.benchmark_id != benchmark_id]
    if len(keep) == len(papers):
        raise KeyError(f"no benchmark paper {benchmark_id!r}")
    project.write_benchmark(keep)
    sync(project)
    # a chaining candidate added as this paper is a candidate again, so it never points at a paper that reuses the id
    project.store.execute("UPDATE seed_chain_candidates SET status='new', benchmark_id=NULL, by=NULL, at=NULL "
                          "WHERE status='added' AND benchmark_id=?", (benchmark_id,))
    log = _resolve_log(project)
    if log.pop(benchmark_id, None) is not None:
        _save_log(project, log)


def suggest_split(project: Project, dev_share: float | None = None, seed: int | None = None) -> dict:
    """A seeded random development / validation split; nothing is written until ``apply_split``."""
    papers = project.read_benchmark()
    try:   # a share as typed (CLI text or the page's number)
        share = project.config.protocol.identification.development_share if dev_share is None else float(dev_share)
    except (TypeError, ValueError):
        raise ValueError(f"the development share must be a number from 0 to 1, e.g. 0.6 (got {str(dev_share)[:20]!r})") from None
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"the development share must be a number from 0 to 1, e.g. 0.6 (got {share:g})")
    seed = project.config.seed if seed is None else int(seed)
    ids = [p.benchmark_id for p in papers]
    rng = random.Random(seed)
    order = list(ids)
    rng.shuffle(order)
    n = len(order)
    n_dev = int(round(share * n))
    if n >= 2:
        n_dev = min(max(n_dev, 1), n - 1)
    dev = sorted(order[:n_dev], key=ids.index)
    val = sorted(order[n_dev:], key=ids.index)
    return {"development": dev, "validation": val, "n": n, "dev_share": share, "seed": seed, "rule": SPLIT_RULE,
            "current": {p.benchmark_id: p.set for p in papers}}


def apply_split(project: Project, split: dict, confirm: bool = False, by: str | None = None) -> list[BenchmarkPaper]:
    """Write a split as one change (D3): moving a shown validation paper to Development needs ``confirm``."""
    moves = dict.fromkeys(split.get("validation") or [], "Validation") | dict.fromkeys(split.get("development") or [],
                                                                                      "Development")
    return _move(project, project.read_benchmark(), moves, confirm, by)


def _close_held_questions(project: Project) -> None:
    """A seed moved to Validation takes its refinement questions with it (blinding; risk-checker R1)."""
    from ..interview import bank
    bank.close_stale(project)
