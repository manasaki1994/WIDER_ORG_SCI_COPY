"""The indexing check (vision change 2a, D10, task 2.10): for each seed paper a platform did not retrieve, the
known-item string to paste into that database, and the researcher's verdict whether the database holds the paper.

A seed counts as retrieved by a platform when it has a DOI or normalised-title match in an export imported against
that platform (``raw_records``). The seeds are the development papers (seen papers included) and the validation set
only while it is shown (D9, F2): a hidden Validation paper is never listed, and no verdict can be set for it, so
nothing here says whether the database returned it. Papers from the query's results are in neither set.

The strings are only shown for the researcher to paste; nothing is searched or sent anywhere. Verdicts are stored in
``seed_indexing`` and journalled as ``seed_indexing`` (undoable). The CLI (``wider query indexing``) and the routes
(``/api/query/indexing``) call the functions here.
"""
from __future__ import annotations

import re

VERDICTS = ("indexed", "not_indexed", "unknown")
DIAGNOSIS = {"not_indexed": "not in the database", "indexed": "indexed but not retrieved",
             "unknown": "indexing unknown", None: "not checked yet"}
GROUPS = ("not_indexed", "indexed_missed", "unknown", "unchecked")
NO_TEMPLATE = "no known-item template for this platform: search the title by hand"
NO_EXPORT = "no export imported against this platform yet: nothing to check"
SAMPLED = ("an export of this platform is a random sample: a seed not in it may still be retrieved, so none is listed "
           "as missed")
_PHRASE_BREAKERS = re.compile(r'["*?$#]+')   # quotes and wildcards would break or widen the phrase


def _catalogue() -> dict:
    from .render import platforms

    return platforms()


def _platform_keys(project, platform: str | None) -> list[str]:
    cat = _catalogue()
    if platform is not None:
        if platform not in cat:
            raise ValueError(f"unknown platform {platform!r}; one of: {', '.join(cat)}")
        return [platform]
    chosen = [p.key for p in project.config.platforms if p.key in cat]
    imported = [r["platform"] for r in project.store.query("SELECT DISTINCT platform FROM sources ORDER BY platform")]
    return chosen + [k for k in imported if k not in chosen and k in cat]


def shown_seeds(project) -> tuple[bool, list[tuple[str, object]]]:
    """``(validation shown, [(group, paper)])``: the development papers, the validation set only while shown, and the
    used validation papers (listed apart, validation-reuse D1)."""
    from . import coverage

    shown = coverage.validation_unlocked(project)
    members = [(g, b) for g, b in coverage.set_members(project, shown) if g != coverage.NOT_COUNTED]
    return shown, members


def _sources(project, platform: str, version: int | None) -> list[int]:
    sql, params = "SELECT source_id FROM sources WHERE platform=?", [platform]
    if version is not None:
        sql += " AND query_version=?"
        params.append(version)
    return [r["source_id"] for r in project.store.query(sql, params)]


def retrieved(project, platform: str, papers, version: int | None = None) -> set[str]:
    """The benchmark ids among ``papers`` with a DOI or normalised-title match in the platform's imported exports
    (every imported file, whether in deduplication or not; with ``version``, the files imported for that version)."""
    from ..core.text import clean_doi, norm_title

    ids = _sources(project, platform, version)
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    dois, titles = set(), set()
    for r in project.store.query(f"SELECT doi_norm, title_norm FROM raw_records WHERE source_id IN ({marks})", ids):
        if r["doi_norm"]:
            dois.add(clean_doi(r["doi_norm"]) or r["doi_norm"])
        if r["title_norm"]:
            titles.add(r["title_norm"])
    return {b.benchmark_id for b in papers
            if (clean_doi(b.doi) or "\0") in dois or (norm_title(b.title) or "\0") in titles}


def title_phrase(title: str | None) -> str:
    """The title as a plain phrase: no quotes or wildcard characters, single spaces, no trailing period."""
    from ..core.text import clean_title

    return " ".join(_PHRASE_BREAKERS.sub(" ", clean_title(title)).split())


def known_item(platform: str, doi: str | None, title: str | None) -> dict:
    """``{string, by, alternative}`` for one paper: the DOI string where the platform's template has a DOI field and
    the paper a DOI, else the title phrase (``by`` "doi" or "title"); ``alternative`` is the title string when the
    DOI string is used. ``string`` is None when the platform has no template."""
    from ..core.text import clean_doi

    tpl = _catalogue().get(platform, {}).get("known_item") or {}
    d = clean_doi(doi)
    t = title_phrase(title)
    by_title = tpl["title"].replace("{title}", t) if tpl.get("title") and t else None
    if tpl.get("doi") and d:
        return {"string": tpl["doi"].replace("{doi}", d), "by": "doi", "alternative": by_title}
    return {"string": by_title, "by": "title" if by_title else None, "alternative": None}


def verdicts(project, platform: str | None = None) -> dict[tuple[str, str], dict]:
    sql, params = "SELECT * FROM seed_indexing", []
    if platform is not None:
        sql += " WHERE platform=?"
        params.append(platform)
    return {(r["benchmark_id"], r["platform"]): dict(r) for r in project.store.query(sql, params)}


def group_of(verdict: str | None) -> str:
    return {"not_indexed": "not_indexed", "indexed": "indexed_missed", "unknown": "unknown"}.get(verdict, "unchecked")


def known_item_strings(project, version: int | None = None, platform: str | None = None) -> dict:
    """Per platform (the project's chosen ones, then any with an imported export; or ``platform``): the seed papers it
    did not retrieve, each with its known-item string and the verdict so far, and the groups by verdict.

    Returns ``{validation_shown, note, platforms: {key: {label, files, retrieved, missed, verified, template_note,
    note, seeds: [...], groups: {...}}}}``; a seed row holds ``benchmark_id, set, used | former | was_development,
    title, doi, string, by,
    alternative, verdict, verdict_by, verdict_at, diagnosis``."""
    from . import coverage

    keys = _platform_keys(project, platform)
    shown, members = shown_seeds(project)
    papers = [b for _, b in members]
    cat = _catalogue()
    out: dict = {"validation_shown": shown, "note": coverage.BOTH_NOTE if shown else coverage.HIDDEN_NOTE,
                 "platforms": {}}
    held = verdicts(project)
    former = coverage.former_ids(project)
    for key in keys:
        entry = cat.get(key, {})
        tpl = entry.get("known_item") or {}
        files = _sources(project, key, version)
        got = retrieved(project, key, papers, version) if files else set()
        seeds = []
        marks = ",".join("?" * len(files))
        sampled = bool(files) and bool(project.store.scalar(
            f"SELECT COUNT(*) FROM sources WHERE sample_of IS NOT NULL AND source_id IN ({marks})", files))
        if files and not sampled:
            for group, b in members:
                if b.benchmark_id in got:
                    continue
                v = held.get((b.benchmark_id, key)) or {}
                flags = coverage.row_flags(group, b, former)
                seeds.append({"benchmark_id": b.benchmark_id, "set": coverage.set_label(group, flags.get("former")), **flags,
                              "title": b.title, "doi": b.doi, **known_item(key, b.doi, b.title),
                              "verdict": v.get("verdict"), "verdict_by": v.get("by"), "verdict_at": v.get("at"),
                              "diagnosis": DIAGNOSIS[v.get("verdict")]})
        groups: dict[str, list[str]] = {g: [] for g in GROUPS}
        for s in seeds:
            groups[group_of(s["verdict"])].append(s["benchmark_id"])
        note = NO_EXPORT if not files else SAMPLED if sampled else (NO_TEMPLATE if not tpl else None)
        out["platforms"][key] = {"label": entry.get("label", key), "files": len(files), "retrieved": len(got),
                                 "missed": len(seeds), "verified": bool(tpl.get("verified")),
                                 "template_note": tpl.get("note"), "note": note, "seeds": seeds, "groups": groups}
    return out


def verdict_groups(project, platform: str, version: int | None = None) -> dict[str, list[str]]:
    """The missed seeds of ``platform`` grouped by verdict: ``not_indexed`` (not in the database), ``indexed_missed``
    (indexed but not retrieved), ``unknown`` and ``unchecked`` (no verdict yet)."""
    return known_item_strings(project, version, platform)["platforms"][platform]["groups"]


def set_verdict(project, benchmark_id: str, platform: str, verdict: str, by: str | None = None) -> dict:
    """Store the researcher's verdict for one seed paper on one platform (journalled ``seed_indexing``, undoable).

    Refused, with nothing changed: an unknown verdict or platform, a seed that is not listed (a hidden Validation
    paper, a paper from the query's results, or no such seed), and ``not_indexed`` for a seed the platform retrieved
    (it contradicts the imported export). ``indexed`` and ``unknown`` are stored for any listed seed.
    Returns ``{benchmark_id, platform, verdict, diagnosis, retrieved, already, change_id}``."""
    from ..core import journal

    bid, out = check_verdict(project, benchmark_id, platform, verdict)
    old = project.store.one("SELECT verdict FROM seed_indexing WHERE benchmark_id=? AND platform=?", (bid, platform))
    if old is not None and old["verdict"] == verdict:
        return {**out, "already": True}
    who = journal.current_author(by)
    with journal.recording(project, "seed_indexing", f"{bid} on {platform}: {verdict}", by) as rec:
        rec.rows("seed_indexing", "benchmark_id=? AND platform=?", [bid, platform])
        _apply_verdict(project, bid, platform, verdict, who)
    return {**out, "change_id": rec.change_id}


def check_verdict(project, benchmark_id: str, platform: str, verdict: str) -> tuple[str, dict]:
    """The refusals of :func:`set_verdict` (``ValueError``, nothing written); returns the seed id and the result."""
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}; one of: {', '.join(VERDICTS)}")
    _platform_keys(project, platform)   # an unknown platform raises
    bid = (benchmark_id or "").strip()
    shown, members = shown_seeds(project)
    paper = next((b for _, b in members if b.benchmark_id == bid), None)
    if paper is None:
        if any(b.benchmark_id == bid for b in project.read_benchmark()):
            why = ("it is not a development seed and the validation set is hidden" if not shown
                   else "it is not in the development or validation set")
            raise ValueError(f"{bid} has no indexing check: {why}. Nothing was changed.")
        raise ValueError(f"no seed paper {bid!r}. Nothing was changed.")
    got = bid in retrieved(project, platform, [paper])
    if verdict == "not_indexed" and got:
        raise ValueError(f"{bid} was retrieved by {platform} (a DOI or title match in an imported export), so it is in "
                         f"the database: not_indexed is refused. Nothing was changed.")
    out = {"benchmark_id": bid, "platform": platform, "verdict": verdict, "diagnosis": DIAGNOSIS[verdict],
           "retrieved": got, "already": False, "change_id": None}
    return bid, out


def _apply_verdict(project, benchmark_id: str, platform: str, verdict: str, who: str) -> None:
    """Store a checked verdict (inside the caller's journalled change: a ``refine_seed_not_retrieved`` answer is one
    ``answer`` change, F3)."""
    from ..core.provenance import utcnow

    project.store.execute(
        "INSERT INTO seed_indexing(benchmark_id, platform, verdict, by, at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(benchmark_id, platform) DO UPDATE SET verdict=excluded.verdict, by=excluded.by, at=excluded.at",
        (benchmark_id, platform, verdict, who, utcnow()))
