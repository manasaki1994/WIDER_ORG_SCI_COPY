"""Import database exports from ``inputs/<platform>/`` into raw_records, once per file (by content hash).

Accepted formats: RIS, CSV/TSV, XLS/XLSX and BibTeX (see :mod:`wider.ingest.readers`). The pre-Phase-2
layout ``inputs/ris/<platform>/`` keeps working alongside ``inputs/<platform>/``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core.project import Project
from ..core.provenance import local_time, sha256_file, utcnow
from .readers import INPUT_SUFFIXES, NotAnExport, read_standard

RIS_SUFFIXES = {".ris", ".txt"}  # kept for callers of the old name
LEGACY_DIR = "ris"
FINAL_KEY = "uploads_final_at"   # store meta: when the uploads were marked final (journalled, undoable)
UNLOCK_NO_KEY = "uploads_final_unlock_no"   # store meta: the latest unlock number when the mark was made (D3)


def final_at(project: Project) -> str | None:
    """When the uploads were marked final, or None."""
    return project.store.get_meta(FINAL_KEY) or None


def _final_change(project: Project) -> int | None:
    """The journalled change that set the mark now in force: the newest change touching it (a mark, or a Redo of one)."""
    import json

    for r in project.store.query("SELECT change_id, items FROM history WHERE items LIKE ? ORDER BY change_id DESC",
                                 (f"%{FINAL_KEY}%",)):
        if any(it.get("table") == "meta" and it.get("params") == [FINAL_KEY] for it in json.loads(r["items"])):
            return int(r["change_id"])
    return None


def upload_plan(project: Project, file_name: str = "a new file") -> dict:
    """What taking a new file now would do (D2, D21): ``final`` (the mark stands), ``choice`` (papers are in the
    validation set, so the researcher chooses whether they count as seen, Q2; with or without a frozen query,
    unlock-seen-choice D3) and ``asking`` (that notice, naming ``file_name``)."""
    from ..construct import artifacts

    at = final_at(project)
    if not at:
        return {"final": False, "choice": False, "asking": None}
    fq = artifacts.frozen_query(project)
    n = artifacts._n_validation(project)
    if not n:
        return {"final": True, "choice": False, "asking": None}
    stands = f" and the frozen query v{fq['version']} stands" if fq is not None else ""
    return {"final": True, "choice": True,
            "asking": (f"The uploads were marked final on {at[:10]}{stands}; adding {file_name} reopens them; "
                       f"{artifacts.papers_phrase(n, 'has been shown', 'have been shown')}. {artifacts.CHOICE_TAIL}")}


def reopen_if_final(project: Project, by: str | None, file_name: str, seen_choice: str | None = None) -> str | None:
    """A new file while the uploads are marked final reopens them (D2, D21): records an unlock with cause ``uploads``
    (which clears the mark; earlier imports and decisions are kept) and returns its notice, or None when no mark
    stands. With papers in the validation set it needs ``seen_choice`` (``count`` | ``keep``; else
    :class:`~wider.construct.artifacts.SeenChoiceNeeded`, nothing done). Call it only once a new file is taken."""
    from ..construct import artifacts

    at = final_at(project)
    if not at:
        return None
    choice = artifacts.seen_choice(seen_choice)
    plan = upload_plan(project, file_name)
    if plan["choice"] and choice is None:
        raise artifacts.SeenChoiceNeeded(plan["asking"])
    fq = artifacts.frozen_query(project)
    row = artifacts.record_unlock(project, "uploads", None, None, by, choice=choice if plan["choice"] else None,
                                  detail=f"{file_name} added")
    head = (f"Uploads reopened: {file_name} was added after they were marked final on {at[:10]}. Earlier imports and "
            "decisions are kept; ")
    if fq is not None:
        tail = f" {artifacts.outcome_line(choice, row['n'])}" if plan["choice"] else ""
        return f"{head}the frozen query v{fq['version']} still stands.{tail}"
    if plan["choice"]:
        return f"{head[:-2]}. {artifacts.outcome_line(choice, row['n'])}"
    return head + "no validation paper has been shown yet."


def waiting_files(project: Project) -> list[tuple[str, Path]]:
    """Saved exports whose bytes are not imported yet, as ``(platform, path)``."""
    done = {r["sha256"] for r in project.store.query("SELECT sha256 FROM sources")}
    return [(pl, f) for pl, f in input_files(project) if sha256_file(f) not in done]


def final_blocker(project: Project) -> str | None:
    """Why the uploads cannot be marked final now (nothing imported, or a saved file not imported, one that cannot be
    read named with its parse error), or None. Used by :func:`mark_final` and by a Redo of an undone mark."""
    n_files = project.store.scalar("SELECT COUNT(*) FROM sources") or 0
    if not n_files:
        return "no files imported yet: import the search results first (wider import run)"
    waiting = waiting_files(project)
    if waiting:
        bad, todo = [], []
        for platform, f in waiting:
            try:
                read_standard(f, platform)
                todo.append(f"{platform}/{f.name}")
            except Exception as e:  # noqa: BLE001 - any parse failure is reported with the file
                bad.append(f"{platform}/{f.name} cannot be read ({str(e)[:200]}). Fix or delete it in "
                           f"inputs/{platform}/, then try again")
        parts = bad[:]
        if todo:
            n = len(todo)
            parts.append(f"{n} file{' is' if n == 1 else 's are'} saved but not imported yet: {', '.join(todo)}. "
                         "Import the uploaded files first (wider import run)")
        return "; ".join(parts)
    return None


def mark_final(project: Project, author: str | None = None) -> dict:
    """Mark the uploads final (journalled kind ``uploads_final``, undoable until an unlock): the Validation set
    unlocks, and a later file reopens the uploads (:func:`reopen_if_final`). Refused, with nothing journalled, when no file is imported
    yet or a saved file is not imported (one that cannot be read is named with its parse error). Marking again
    reports the existing time. Returns ``{at, already, change_id}``."""
    from ..core import journal

    at = final_at(project)
    if at:
        return {"at": at, "already": True, "change_id": None}
    why = final_blocker(project)
    if why:
        raise ValueError(why)
    n_files = project.store.scalar("SELECT COUNT(*) FROM sources") or 0
    n_records = project.store.scalar("SELECT COALESCE(SUM(n_records), 0) FROM sources") or 0
    now = utcnow()
    with journal.recording(project, "uploads_final",
                           f"uploads marked final ({n_files} files, {n_records} records)", author) as rec:
        rec.rows("meta", "key=?", [FINAL_KEY])
        rec.rows("meta", "key=?", [UNLOCK_NO_KEY])
        project.store.set_meta(FINAL_KEY, now)
        from ..search.coverage import latest_unlock_no
        project.store.set_meta(UNLOCK_NO_KEY, str(latest_unlock_no(project)))
    return {"at": now, "already": False, "change_id": rec.change_id}


def input_dirs(project: Project) -> list[tuple[str, Path]]:
    """``(platform, directory)`` pairs: ``inputs/<platform>/`` and the legacy ``inputs/ris/<platform>/``."""
    base = project.path("inputs")
    out: list[tuple[str, Path]] = []
    if not base.exists():
        return out
    for pdir in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if pdir.name == LEGACY_DIR:
            out += [(q.name, q) for q in sorted(pdir.iterdir()) if q.is_dir() and not q.name.startswith(".")]
        else:
            out.append((pdir.name, pdir))
    return out


def input_files(project: Project) -> list[tuple[str, Path]]:
    """Every export file with a known suffix, as ``(platform, path)``, in a stable order."""
    out = []
    for platform, pdir in input_dirs(project):
        for f in sorted(pdir.iterdir()):
            if f.is_file() and f.suffix.lower() in INPUT_SUFFIXES and not f.name.startswith("."):
                out.append((platform, f))
    return out


ris_files = input_files  # backwards-compatible name


def same_export(project: Project, src: str | Path) -> tuple[str, Path] | None:
    """``(platform, path)`` of an export under inputs/ with the same bytes as ``src``, if there is one."""
    h = sha256_file(src)
    src = Path(src).resolve()
    for platform, f in input_files(project):
        if f.resolve() != src and sha256_file(f) == h:
            return platform, f
    return None


def free_name(dst_dir: Path, name: str) -> Path:
    """``dst_dir/name``, or ``stem-2.ext``, ``stem-3.ext``, ... when that name is taken."""
    dst, stem, suffix, i = dst_dir / name, Path(name).stem, Path(name).suffix, 2
    while dst.exists():
        dst, i = dst_dir / f"{stem}-{i}{suffix}", i + 1
    return dst


def add_file(project: Project, platform: str, src: str | Path, overwrite: bool = False, by: str | None = None,
             seen_choice: str | None = None, notices: list[str] | None = None) -> Path:
    """Copy an export into inputs/<platform>/ (the project folder is gitignored). An existing file is never
    overwritten unless ``overwrite``: the same bytes already under inputs/ (any name or platform) are kept as they
    are, and a different file whose name is taken is saved under a free name (``stem-2.ext``). Returns where the
    export is. A file that is read as a table without a title or DOI in any row is refused with a ValueError
    (:class:`~wider.ingest.readers.NotAnExport`) and nothing is saved. A file really taken while the uploads are marked final reopens them (:func:`reopen_if_final`, which
    may need ``seen_choice``); its notice is appended to ``notices``."""
    if not overwrite:
        same = same_export(project, src)
        if same:
            return same[1]
    try:   # a file that is not an export is refused before anything is saved (ValueError, the page's 400)
        read_standard(src, platform)
    except NotAnExport as e:
        raise ValueError(f"{e}; nothing was saved") from e
    except Exception:   # an unreadable export is saved as before; the file check and the import report it
        pass
    dst_dir = project.path("inputs", platform)
    dst = dst_dir / Path(src).name
    if dst.exists() and not overwrite:
        if sha256_file(dst) == sha256_file(src):
            return dst
        dst = free_name(dst_dir, dst.name)
    notice = reopen_if_final(project, by, dst.name, seen_choice)
    dst_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if notice and notices is not None:
        notices.append(notice)
    return dst


def import_all(project: Project, progress=None, by: str | None = None, seen_choice: str | None = None,
               notices: list[str] | None = None, round_no: int | None = None) -> list[dict]:
    """Import every saved export not imported yet (by content hash). A file waiting while the uploads are marked
    final reopens them first (:func:`reopen_if_final`, which may need ``seen_choice``; nothing is imported without
    it); its notice is appended to ``notices``. With no file waiting nothing is recorded. Each file is recorded under
    ``round_no`` (refused when it is not a synonyms version), else the frozen query, else the newest version (D8)."""
    from ..search import rounds
    qv, recorded = rounds.import_version(project, round_no)   # before anything changes: an unknown round does nothing
    st = project.store
    done = {r["sha256"]: dict(r) for r in st.query("SELECT * FROM sources")}
    if final_at(project):
        waiting = [f for _, f in input_files(project) if sha256_file(f) not in done]
        if waiting:
            names = ", ".join(f.name for f in waiting)
            notice = reopen_if_final(project, by, names, seen_choice)
            if notice and notices is not None:
                notices.append(notice)
    results = []
    check = None   # (bench, vocab) for the file check, read once when the first new file is imported
    for platform, f in input_files(project):
        h = sha256_file(f)
        if h in done:  # imported earlier, or an identical file seen earlier in this run
            results.append({"platform": platform, "file": f.name, "status": "already", "n": done[h]["n_records"],
                            "format": done[h].get("format"), "profile": done[h].get("profile"),
                            "same_as": done[h].get("file_name")})
            continue
        try:
            meta, rows = read_standard(f, platform)
        except Exception as e:  # unreadable file: report, do not abort the other files
            results.append({"platform": platform, "file": f.name, "status": "error", "n": 0, "error": str(e)[:300]})
            if progress:
                progress(results[-1])
            continue
        # a file the check flags (off topic, empty) starts out of deduplication (ui-redesign D32 F8, owner 30 Oct);
        # the researcher includes it with one tick or `wider import include`
        from .sanity import FLAGGED, benchmark_list, check_rows, vocabulary
        if check is None:
            try:
                check = (benchmark_list(project), vocabulary(project))
            except OSError:   # an unreadable synonyms file: the check has no signal and the file enters as before
                check = ([], {})
        chk = check_rows(project, platform, rows, meta, bench=check[0], vocab=check[1], sha256=h, suffix=f.suffix)
        kept_out = chk["verdict"] in FLAGGED
        with st.tx() as c:
            cur = c.execute("INSERT INTO sources(platform,file_name,sha256,n_records,imported_at,query_version,format,"
                            "profile,included) VALUES(?,?,?,?,?,?,?,?,?)",
                            (platform, f.name, h, len(rows), utcnow(), qv, meta["format"], meta["profile"],
                             0 if kept_out else 1))
            sid = cur.lastrowid
            done[h] = {"n_records": len(rows), "format": meta["format"], "profile": meta["profile"],
                       "file_name": f.name}
            c.executemany(
                "INSERT INTO raw_records(source_id,platform,position,type,title,authors,year,journal,volume,issue,pages,"
                "doi,abstract,keywords,url,language,extra,title_norm,doi_norm,first_author) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(sid, platform, i + 1, r["type"], r["title"], r["authors"], r["year"], r["journal"], r["volume"],
                  r["issue"], r["pages"], r["doi"], r["abstract"], r["keywords"], r["url"], r["language"],
                  json.dumps({"native_id": r["native_id"]}), r["title_norm"], r["doi_norm"], r["first_author"])
                 for i, r in enumerate(rows)])
        results.append({"platform": platform, "file": f.name, "status": "imported", "n": len(rows), "source_id": sid,
                        "format": meta["format"], "profile": meta["profile"],
                        "detected_platform": meta["detected_platform"], "mismatch": meta["mismatch"],
                        "no_title": sum(1 for r in rows if not r["title"]),
                        "no_abstract": sum(1 for r in rows if not r["abstract"]),
                        "no_doi": sum(1 for r in rows if not r["doi"]), "query_version": qv, "recorded": recorded,
                        "verdict": chk["verdict"], "kept_out": kept_out})
        if kept_out and notices is not None:
            notices.append(f"{f.name} is kept out of deduplication until you include it: {chk['message']}.")
        if progress:
            progress(results[-1])
    copied = _copy_keywords(project, results, by)
    if copied and notices is not None:
        notices.append(keywords_notice(copied))
    rounds.record_exports(project)
    if any(r.get("status") == "imported" for r in results):
        from ..interview import bank  # the diagnosis of the new records can raise questions (2b, F14)
        line = bank.notice(bank.safe_trigger(project, "import"))
        if line and notices is not None:
            notices.append(line)
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_import
    record(project, summarize_import(results, project.root))
    return results


def keywords_notice(ids: list[str]) -> str:
    return (f"Keywords copied from the import to seed {'paper' if len(ids) == 1 else 'papers'} {', '.join(ids)} "
            "(shown as \"from import\"); keywords typed by the researcher were left unchanged.")


def _copy_keywords(project: Project, results: list[dict], by: str | None) -> list[str]:
    """After an import: each new file fills the empty keywords of seed papers it matches by DOI (D6, 2.3); the file's
    result names them under ``keywords_copied``."""
    from ..construct import seed

    copied: list[str] = []
    for r in results:
        if r.get("status") == "imported" and r.get("source_id"):
            r["keywords_copied"] = seed.copy_import_keywords(project, r["source_id"], by)
            copied += r["keywords_copied"]
    return copied


def list_sources(project: Project) -> dict:
    """Every imported file, oldest first, with its query version (None: not recorded), and the final mark. Reads the
    store only (`wider import list`, ``GET /api/import/sources``)."""
    rows = [{**dict(r), "included": bool(r["included"])} for r in project.store.query(
        "SELECT source_id, platform, file_name AS file, n_records AS records, imported_at, query_version, included, "
        "sample_of FROM sources ORDER BY source_id")]
    return {"sources": rows, "final_at": final_at(project)}


def resolve_source(project: Project, ref: str | int) -> dict:
    """An imported file by its id, or by its name when exactly one imported file has it (D5); otherwise a ValueError
    that lists the candidates with their ids."""
    rows = [dict(r) for r in project.store.query(
        "SELECT source_id, platform, file_name, n_records, imported_at, included, query_version, sample_of FROM sources "
        "ORDER BY source_id")]
    text = str(ref).strip()
    if text.isdigit():
        hit = [r for r in rows if r["source_id"] == int(text)]
        if hit:
            return hit[0]
    hit = [r for r in rows if r["file_name"] == text]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        raise ValueError(f"No imported file has the id or name {text!r}. `wider import list` shows the ids.")
    listed = "; ".join(f"id {r['source_id']}: {r['platform']}, {r['n_records']} records, imported {local_time(r['imported_at'])}"
                       for r in hit)
    raise ValueError(f"{len(hit)} imported files are called {text}, so the name does not say which: {listed}. "
                     "Give the file's id instead.")


def set_included(project: Project, ref: str | int, included: bool, by: str | None = None) -> dict:
    """Keep an imported file in deduplication or out of it (D5; journalled kind ``source_included``, undoable). Refused,
    with nothing changed, while a later stage holds rows on records only that file brought; the next deduplication run
    reads the new choice. Returns ``{source_id, file, included, already, change_id}``."""
    from ..core import journal
    from ..dedup.engine import _fmt_counts, downstream_for_source

    src = resolve_source(project, ref)
    out = {"source_id": src["source_id"], "file": src["file_name"], "included": bool(included), "already": False,
           "change_id": None}
    if bool(src["included"]) == bool(included):
        return {**out, "already": True}
    dep = downstream_for_source(project.store.conn, src["source_id"])
    if dep:
        raise ValueError(f"File {src['source_id']} ({src['file_name']}) cannot be "
                         f"{'included' if included else 'excluded'}: later stages hold rows on records only this file "
                         f"brought ({_fmt_counts(dep)}). Nothing was changed.")
    word = "enters deduplication" if included else "kept out of deduplication"
    with journal.recording(project, "source_included", f"file {src['source_id']} ({src['file_name']}) {word}", by) as rec:
        rec.rows("sources", "source_id=?", [src["source_id"]])
        project.store.execute("UPDATE sources SET included=? WHERE source_id=?", (int(bool(included)), src["source_id"]))
    return {**out, "change_id": rec.change_id}


def set_sample(project: Project, ref: str | int, of: int | None, by: str | None = None) -> dict:
    """Mark an imported file as a random sample of ``of`` results, or as a complete export with ``of`` None
    (openalex-profile-and-sampled-exports D4; journalled kind ``source_sample``, undoable). No record changes, so later
    stages never refuse it. Refused, with nothing changed, when ``of`` is below 1 or not above the file's records, or
    when the included files of the same platform and version marked with the same ``of`` (one sample, D5) would hold
    ``of`` records or more. Returns ``{source_id, file, sample_of, already, change_id}``."""
    from ..core import journal

    src = resolve_source(project, ref)
    if of is not None:
        of = int(of)
        if of < 1:
            raise ValueError(f"A file cannot be a random sample of {of} results: give the number of results the "
                             "database reported, 1 or more. Nothing was changed.")
        if of > 10 ** 12:
            raise ValueError(f"{of:,} results is more than any database holds: give the number of results the "
                             "database reported. Nothing was changed.")
        if of <= src["n_records"]:
            raise ValueError(f"File {src['source_id']} ({src['file_name']}) holds {src['n_records']:,} records, so it "
                             f"cannot be a random sample of {of:,} results: a sample of {src['n_records']:,} records "
                             f"cannot come from {src['n_records']:,} results or fewer. Nothing was changed.")
        same = [dict(r) for r in project.store.query(
            "SELECT source_id, file_name, n_records FROM sources WHERE platform=? AND included=1 AND sample_of=? "
            "AND query_version IS ? AND source_id<>?",
            (src["platform"], of, src["query_version"], src["source_id"]))]
        held = src["n_records"] + sum(r["n_records"] for r in same)
        if same and held >= of:
            listed = ", ".join(f"file {r['source_id']} ({r['file_name']}, {r['n_records']:,} records)" for r in same)
            raise ValueError(f"{listed} {'is' if len(same) == 1 else 'are'} already marked a random sample of {of:,} "
                             f"results on {src['platform']}; with file {src['source_id']} ({src['n_records']:,} "
                             f"records) that sample would hold {held:,} records, not fewer than {of:,}. Nothing was "
                             "changed.")
    out = {"source_id": src["source_id"], "file": src["file_name"], "sample_of": of, "already": False,
           "change_id": None}
    if src["sample_of"] == of:
        return {**out, "already": True}
    what = f"marked a random sample of {of:,} results" if of is not None else "marked a complete export"
    with journal.recording(project, "source_sample", f"file {src['source_id']} ({src['file_name']}) {what}", by) as rec:
        rec.rows("sources", "source_id=?", [src["source_id"]])
        project.store.execute("UPDATE sources SET sample_of=? WHERE source_id=?", (of, src["source_id"]))
    return {**out, "change_id": rec.change_id}


def summary(project: Project) -> list[dict]:
    return [dict(r) for r in project.store.query(
        "SELECT platform, COUNT(*) AS files, SUM(n_records) AS records FROM sources GROUP BY platform ORDER BY platform")]
