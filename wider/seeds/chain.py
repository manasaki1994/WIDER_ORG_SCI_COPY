"""Candidates from the seed papers (change 2c, design D16): the works the Development seeds cite and the works citing
them, fetched from OpenAlex under the DOI consent (purpose ``seed_chain``). Candidates are listed for the researcher
and never become seed papers on their own: Add puts one in a set (source ``seed_chain:<first linking seed>``), Dismiss
keeps the row with who and when. Validation seeds are never chained: their DOIs are in no request.

Papers beyond the query (D23): the list is ranked by direct links to the seeds; a snowball round chains the
Development seeds taken from candidates; an export of any other tool becomes candidates with its origin, sending
nothing, and each such import is journalled and can be dismissed as a whole.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..core.journal import current_author, recording
from ..core.lookups import lookup_client
from ..core.project import Project
from ..core.provenance import utcnow
from ..core.text import clean_doi, norm_title

DIRECTIONS = {"references": ("backward",), "cited_by": ("forward",), "both": ("backward", "forward")}
_NAME = {"backward": "references", "forward": "cited_by"}   # how a candidate is linked, in the CLI's words
RETRIEVED = "retrieved by the query"
RANK_NOTE = "ranked by links to your seeds; a paper with one link may still be relevant"
STOP_NOTICE = "round {k} added no paper; snowballing usually stops here"
ORIGIN_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
CHAINED_META = "seed_chain_chained"   # benchmark id -> the directions already chained (backward, forward)


def _resolve(client, dois: list[str]) -> dict[str, str]:
    """DOI -> OpenAlex short id, in ``filter=doi:`` requests of at most 50 DOIs (as ``prior/chain.resolve`` does)."""
    from ..prior.chain import WORKS, _get_json, _short_id

    out: dict[str, str] = {}
    for i in range(0, len(dois), 50):
        batch = dois[i:i + 50]
        d = _get_json(client, WORKS, {"filter": "doi:" + "|".join(batch), "select": "id,doi", "per-page": 50})
        for w in (d or {}).get("results", []):
            if (doi := clean_doi(w.get("doi"))) and w.get("id"):
                out[doi] = _short_id(w["id"])
    return out


def _generations(project: Project, papers) -> dict[str, int]:
    """Each seed's generation: 0 for a seed not taken from a candidate, else that candidate's round."""
    gen = {b.benchmark_id: 0 for b in papers}
    for r in project.store.query("SELECT benchmark_id, chain_round FROM seed_chain_candidates "
                                 "WHERE status='added' AND benchmark_id IS NOT NULL"):
        if r["benchmark_id"] in gen:
            gen[r["benchmark_id"]] = int(r["chain_round"] or 0)
    return gen


def _chained(project: Project) -> dict[str, list[str]]:
    return json.loads(project.store.get_meta(CHAINED_META) or "{}")


def chain_seeds(project: Project, direction: str = "both", limit: int | None = None,
                client: Any = None, from_candidates: bool = False) -> dict[str, Any]:
    """Chain the Development seeds in ``direction`` (``references``, ``cited_by`` or ``both``); at most ``limit``
    seeds. Stores the candidates (new ones as ``new``, in round 1 + the lowest generation of their linking seeds; a
    known one keeps its status and gains linking seeds) and returns a summary with the messages to show.
    ``from_candidates`` is a snowball round: only the Development seeds taken from candidates, in the directions not
    chained yet; nothing is sent when there is nothing to chain. ``benchmark.csv`` is not touched."""
    from ..prior.chain import _fetch_related

    if direction not in DIRECTIONS:
        raise ValueError("direction must be references, cited_by or both")
    papers = project.read_benchmark()
    dev = [p for p in papers if p.set == "Development"]
    n_val = sum(p.set == "Validation" for p in papers)
    gen = _generations(project, papers)
    chained = _chained(project)
    todo = {p.benchmark_id: list(DIRECTIONS[direction]) for p in dev}
    val_from_cand = 0
    if from_candidates:
        added = {r["benchmark_id"] for r in project.store.query(
            "SELECT benchmark_id FROM seed_chain_candidates WHERE status='added' AND benchmark_id IS NOT NULL")}
        val_from_cand = sum(p.set == "Validation" and p.benchmark_id in added for p in papers)
        todo = {b: [d for d in ds if d not in chained.get(b, [])] for b, ds in todo.items() if b in added}
        todo = {b: ds for b, ds in todo.items() if ds}
    with_doi = [p for p in dev if p.benchmark_id in todo and clean_doi(p.doi)]
    if limit:
        with_doi = with_doi[:limit]
    # the round these seeds open; with nothing to chain, the round after the newest one
    k = 1 + (min(gen[p.benchmark_id] for p in with_doi) if with_doi else
             int(project.store.scalar("SELECT COALESCE(MAX(chain_round), 0) FROM seed_chain_candidates")))
    known_dois = {clean_doi(p.doi) for p in papers if clean_doi(p.doi)}
    known_titles = {norm_title(p.title) for p in papers if norm_title(p.title)}
    cap = project.config.prior_reviews.max_citing_per_paper
    found: dict[str, dict] = {}   # key (doi:… or title:…) -> candidate
    capped: list[str] = []
    unresolved: list[str] = []
    hits = sent = 0
    if with_doi:
        with lookup_client(project, "seed_chain", client) as c:   # consent first: nothing is sent without it
            ids = _resolve(c, [clean_doi(p.doi) for p in with_doi])
            for p in with_doi:
                oid = ids.get(clean_doi(p.doi))
                if not oid:
                    unresolved.append(p.benchmark_id)
                    continue
                for d in todo[p.benchmark_id]:
                    rows, was_capped = _fetch_related(c, oid, d, cap)
                    if was_capped:
                        capped.append(f"{p.benchmark_id} {_NAME[d]}")
                    elif d == "backward" and rows:   # the seed's OpenAlex reference list, reused by D26 unsent
                        from .references import store_openalex
                        store_openalex(project, p.benchmark_id, rows)
                    for w in rows:
                        doi, tnorm = clean_doi(w.get("doi")), norm_title(w.get("title"))
                        if (doi and doi in known_dois) or (tnorm and tnorm in known_titles) or not (doi or tnorm):
                            continue   # already a seed paper (or nothing to name it by)
                        key = f"doi:{doi}" if doi else f"title:{tnorm}"
                        cand = found.setdefault(key, {"doi": doi, "title": w.get("title") or "", "title_norm": tnorm,
                                                      "year": w.get("year"), "dirs": set(), "seeds": [],
                                                      "openalex_id": w.get("openalex_id"),
                                                      "abstract": w.get("abstract") or None})
                        cand["dirs"].add(_NAME[d])
                        if p.benchmark_id not in cand["seeds"]:
                            cand["seeds"].append(p.benchmark_id)
                chained[p.benchmark_id] = sorted(set(chained.get(p.benchmark_id, [])) | set(todo[p.benchmark_id]))
            hits, sent = c.cache_hits, c.requests
        project.store.set_meta(CHAINED_META, json.dumps(chained, sort_keys=True))
    for cand in found.values():
        cand["round"] = 1 + min(gen.get(b, 0) for b in cand["seeds"])
    n_new = _store(project, found)
    msgs = []
    if from_candidates:
        if val_from_cand:
            msgs.append(f"{val_from_cand} candidate{'s' if val_from_cand != 1 else ''} in Validation not chained")
        if not with_doi:
            msgs.append(f"round {k}: nothing to chain")
        elif not n_new:
            msgs.append(f"round {k} found no new candidate; snowballing usually stops here")
    elif n_val:
        msgs.append(f"{n_val} Validation seed{'s' if n_val != 1 else ''} not chained")
    no_doi = [p.benchmark_id for p in dev if p.benchmark_id in todo and not clean_doi(p.doi)]
    if no_doi:
        msgs.append(f"{len(no_doi)} Development seed{'s' if len(no_doi) != 1 else ''} without a DOI not chained "
                    f"({', '.join(no_doi)}); resolve them first")
    if unresolved:
        msgs.append(f"not found on OpenAlex: {', '.join(unresolved)}")
    if capped:
        msgs.append(f"stopped at {cap} works for: {', '.join(capped)}")
    if hits:
        msgs.append(("all answers from the lookup cache, nothing was sent" if not sent else
                     f"{hits} answer{'s' if hits != 1 else ''} from the lookup cache, {sent} request"
                     f"{'s' if sent != 1 else ''} sent") + "; clear it with `wider lookups cache --clear`")
    stop = stop_notice(project)
    if stop and not any("snowballing usually stops" in m for m in msgs):
        msgs.append(stop)
    return {"direction": direction, "round": k, "from_candidates": from_candidates,
            "seeds_chained": len(with_doi) - len(unresolved), "validation_not_chained": n_val,
            "validation_candidates_not_chained": val_from_cand, "found": len(found), "new": n_new,
            "from_cache": hits, "requests_sent": sent, "messages": msgs, "candidates": candidates(project),
            "rounds": rounds(project), "stop_notice": stop}


def _origins(row) -> list[str]:
    return json.loads(row["origins_json"] or "[]")


def _store(project: Project, found: dict[str, dict]) -> int:
    st = project.store
    now = utcnow()
    n_new = 0
    with st.tx() as c:
        for cand in found.values():
            row = None
            if cand["doi"]:
                row = c.execute("SELECT * FROM seed_chain_candidates WHERE doi=?", (cand["doi"],)).fetchone()
            if row is None and cand["title_norm"]:
                row = c.execute("SELECT * FROM seed_chain_candidates WHERE title_norm=?", (cand["title_norm"],)).fetchone()
            if row is None:
                n_new += 1
                direction = "both" if len(cand["dirs"]) > 1 else next(iter(cand["dirs"]))
                c.execute("INSERT INTO seed_chain_candidates(doi, title, title_norm, year, direction, linking_seeds_json, "
                          "n_linking, status, fetched_at, openalex_id, abstract, origins_json, chain_round) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (cand["doi"], cand["title"], cand["title_norm"], cand["year"], direction,
                           json.dumps(cand["seeds"]), len(cand["seeds"]), "new", now, cand.get("openalex_id"),
                           cand.get("abstract"), json.dumps(["openalex"]), cand.get("round", 1)))
                continue
            seeds = json.loads(row["linking_seeds_json"] or "[]")
            seeds += [s for s in cand["seeds"] if s not in seeds]
            dirs = set(cand["dirs"]) | _dirs(row["direction"])
            origins = _origins(row) + ([] if "openalex" in _origins(row) else ["openalex"])
            c.execute("UPDATE seed_chain_candidates SET linking_seeds_json=?, n_linking=?, direction=?, fetched_at=?, "
                      "origins_json=?, openalex_id=COALESCE(openalex_id, ?), abstract=COALESCE(abstract, ?) "
                      "WHERE candidate_id=?", (json.dumps(seeds), len(seeds), _direction(dirs), now,
                                               json.dumps(origins), cand.get("openalex_id"), cand.get("abstract"),
                                               row["candidate_id"]))
    return n_new


def _dirs(direction: str | None) -> set[str]:
    return {"references", "cited_by"} if direction == "both" else ({direction} if direction else set())


def _direction(dirs: set[str]) -> str | None:
    return "both" if len(dirs) > 1 else next(iter(dirs), None)


def _show_match(project: Project, benchmark_id: str | None) -> bool:
    """Whether the "retrieved by the query" mark may be shown: always for a candidate not yet added; for an added one
    in Validation only while the set is shown (as ``seed.set_notices`` does on a later listing)."""
    if not benchmark_id:
        return True
    from ..search import coverage

    bp = next((b for b in project.read_benchmark() if b.benchmark_id == benchmark_id), None)
    if bp is None or bp.set != "Validation":
        return True
    return coverage.validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze


def _rank_key(row: dict) -> tuple:
    """D23: most linking seeds (direct links only), then found in both directions, then newest, then the oldest row."""
    return (-(row["n_linking"] or 0), row["direction"] != "both", -(row["year"] or 0), row["candidate_id"])


def candidates(project: Project, round_no: int | None = None) -> list[dict[str, Any]]:
    """The candidates, ranked (``rank``, computed on read): new and added first, dismissed last, by rank within each
    group; ``round_no`` keeps one round. Each is checked again against the imported records, so a later import marks
    it "retrieved by the query". A new candidate the imported records lack gets ``good_validation`` (D23), only once
    the query's records are imported and never for a candidate that is a hidden Validation seed."""
    from ..construct.seed import retrieved_by_query

    st = project.store
    has_records = bool(st.query("SELECT 1 FROM raw_records LIMIT 1"))
    papers = project.read_benchmark()
    by_doi = {clean_doi(b.doi): b.benchmark_id for b in papers if b.doi}
    by_title = {norm_title(b.title): b.benchmark_id for b in papers if b.title}
    val = {b.benchmark_id for b in papers if b.set == "Validation"}

    def seed_ids(row: dict) -> set[str]:
        """The seed papers the candidate now is, by DOI or title, whichever route added it."""
        return {i for i in (by_doi.get(row["doi"]) if row["doi"] else None,
                            by_title.get(row["title_norm"]) if row["title_norm"] else None) if i}

    out = []
    rows = [dict(r) for r in st.query("SELECT * FROM seed_chain_candidates")]
    for row in rows:   # a seed moved to Validation since no longer links, ranks or names anything (D24)
        row["linking_seeds"] = [b for b in json.loads(row.pop("linking_seeds_json") or "[]") if b not in val]
        row["n_linking"] = len(row["linking_seeds"])
    ranked = sorted(rows, key=_rank_key)
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    for row in sorted(ranked, key=lambda x: (x["status"] == "dismissed", x["rank"])):
        if round_no is not None and row["chain_round"] != round_no:
            continue
        if row["status"] == "new":
            rec = retrieved_by_query(project, row["doi"], row["title"])
            rid = rec["raw_id"] if rec else None
            if rid != row["retrieved_raw_id"]:
                st.execute("UPDATE seed_chain_candidates SET retrieved_raw_id=? WHERE candidate_id=?",
                           (rid, row["candidate_id"]))
                row["retrieved_raw_id"] = rid
        shown = all(_show_match(project, b) for b in {row["benchmark_id"], *seed_ids(row)} if b)
        if not shown:
            row["retrieved_raw_id"] = None   # a candidate that is now a hidden Validation seed tells nothing (R1)
        row["origins"] = json.loads(row.pop("origins_json") or "[]")
        row["keywords"] = json.loads(row.pop("keywords_json") or "[]")
        row["retrieved"] = row["retrieved_raw_id"] is not None
        row["good_validation"] = shown and has_records and row["status"] == "new" and not row["retrieved"]
        out.append(row)
    return out


def rounds(project: Project) -> list[dict[str, Any]]:
    """Per round (0 = from a file, 1 = from the seeds, 2+ = snowball rounds): candidates, added, new, dismissed."""
    return [dict(r) for r in project.store.query(
        "SELECT chain_round AS round, COUNT(*) AS n, SUM(status='added') AS added, SUM(status='new') AS new, "
        "SUM(status='dismissed') AS dismissed FROM seed_chain_candidates GROUP BY chain_round ORDER BY chain_round")]


def stop_notice(project: Project) -> str | None:
    """Wohlin's stop rule, as information: the newest chaining round is fully looked at and none of it was added."""
    rs = [r for r in rounds(project) if (r["round"] or 0) >= 1]
    if rs and not rs[-1]["new"] and not rs[-1]["added"]:
        return STOP_NOTICE.format(k=rs[-1]["round"])
    return None


# ----------------------------------------------------------------- candidates from a file (D22, D23)
def _cell_dois(values: list[str]) -> list[str]:
    from ..ingest.standardize import DOI_IN_TEXT

    out = []
    for v in values:
        for part in re.split(r"[;,\s]+", v or ""):
            m = DOI_IN_TEXT.search(part)
            if m and (d := clean_doi(m.group(0))) and d not in out:
                out.append(d)
    return out


def _read_candidates(path: Path) -> list[dict]:
    """The records of an export (RIS, CSV/TSV, XLS/XLSX, BibTeX) with title, DOI, year, abstract, keywords, and the
    DOIs of its ``cites`` / ``cited_by`` columns when it has them."""
    from ..ingest.readers import read_file
    from ..ingest.standardize import _index, norm_key, profile_for, standardize

    fmt, headers, recs = read_file(path)
    prof = None if fmt == "ris" else profile_for(headers, fmt)
    out = []
    for r in recs:
        s = standardize(r, "other", prof)
        idx = _index(r)
        s["cites"] = _cell_dois(idx.get(norm_key("cites"), []))
        s["cited_by"] = _cell_dois(idx.get(norm_key("cited_by"), []) + idx.get(norm_key("cited by"), []))
        out.append(s)
    return out


def import_candidates(project: Project, path: str | Path, origin: str, by: str | None = None,
                      file_name: str | None = None) -> dict[str, Any]:
    """Turn an export of any tool into candidates with origin ``file:<origin>`` and round 0; nothing is sent. A record
    that is a seed paper is skipped and counted; one already a candidate keeps its row and gains the origin, so a
    second import of the same file adds nothing. A Development seed DOI in a ``cites`` column links the record as citing that
    seed (``cited_by``), in a ``cited_by`` column as cited by it (``references``); a Validation seed DOI links nothing.
    Journalled (kind
    ``candidate_import``) with a row in ``candidate_imports``; returns the counts and messages."""
    from ..construct.seed import retrieved_by_query

    label = (origin or "").strip()
    if not ORIGIN_LABEL.fullmatch(label):
        raise ValueError("the origin must be a short label of letters, digits, '.', '_' or '-' (e.g. citationchaser)")
    org = f"file:{label}"
    path = Path(path)
    name = file_name or path.name
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    recs = _read_candidates(path)
    papers = project.read_benchmark()
    seed_doi = {clean_doi(b.doi): b.benchmark_id for b in papers if clean_doi(b.doi)}
    dev_doi = {d: i for d, i in seed_doi.items() if any(b.benchmark_id == i and b.set == "Development" for b in papers)}
    seed_title = {norm_title(b.title) for b in papers if norm_title(b.title)}
    st = project.store
    plan: dict[str, dict] = {}
    n_seed = n_empty = 0
    for s in recs:
        doi, tnorm = s["doi"], s["title_norm"]
        if not (doi or tnorm):
            n_empty += 1
            continue
        if (doi and doi in seed_doi) or (tnorm and tnorm in seed_title):
            n_seed += 1
            continue
        key = f"doi:{doi}" if doi else f"title:{tnorm}"
        cand = plan.setdefault(key, {"doi": doi, "title": s["title"], "title_norm": tnorm, "year": s["year"],
                                     "abstract": s["abstract"], "keywords": [k.strip() for k in
                                                                             (s["keywords"] or "").split(";") if k.strip()],
                                     "seeds": [], "dirs": set()})
        for col, link in (("cites", "cited_by"), ("cited_by", "references")):
            for d in s[col]:
                if d in dev_doi:   # a Validation seed never links a candidate (D24)
                    cand["dirs"].add(link)
                    if dev_doi[d] not in cand["seeds"]:
                        cand["seeds"].append(dev_doi[d])
    existing: dict[str, dict] = {}
    for key, cand in plan.items():
        row = st.one("SELECT * FROM seed_chain_candidates WHERE doi=?", (cand["doi"],)) if cand["doi"] else None
        if row is None and cand["title_norm"]:
            row = st.one("SELECT * FROM seed_chain_candidates WHERE title_norm=?", (cand["title_norm"],))
        if row is not None:
            existing[key] = dict(row)
    merged = [k for k, row in existing.items() if org not in _origins(row)]
    new = [k for k in plan if k not in existing]
    n_known = len(existing) - len(merged)
    msgs = []
    if n_seed:
        msgs.append(f"{n_seed} record{'s' if n_seed != 1 else ''} already seed papers, skipped")
    if n_empty:
        msgs.append(f"{n_empty} record{'s' if n_empty != 1 else ''} without a DOI or title, skipped")
    out = {"import_no": None, "origin": org, "file": name, "n_records": len(recs), "new": len(new),
           "merged": len(merged), "known": n_known, "skipped_seed": n_seed, "skipped_empty": n_empty}
    if not recs:
        msgs.append(f"nothing imported: {name} holds no records")
        return {**out, "messages": msgs, "candidates": candidates(project)}
    if not new and not merged:
        msgs.append(f"nothing new: every record is already a candidate from {org} or a seed paper")
        return {**out, "messages": msgs, "candidates": candidates(project)}
    max_id = st.scalar("SELECT COALESCE(MAX(candidate_id), 0) FROM seed_chain_candidates")
    import_no = 1 + st.scalar("SELECT COALESCE(MAX(import_no), 0) FROM candidate_imports")
    ids = [existing[k]["candidate_id"] for k in merged]
    where = "candidate_id > ?" + (f" OR candidate_id IN ({','.join('?' * len(ids))})" if ids else "")
    author = current_author(by) or None
    now = utcnow()
    summary = (f"import {import_no}: {len(new)} new candidate(s), {len(merged)} known one(s) gained {org} "
               f"({name})")
    with recording(project, "candidate_import", summary, author=by) as rec:
        rec.rows("seed_chain_candidates", where, [max_id, *ids])
        watch = rec.items[-1]
        rec.rows("candidate_imports", "import_no=?", [import_no])
        new_ids = []
        with st.tx() as c:
            for k in new:
                cand = plan[k]
                hit = retrieved_by_query(project, cand["doi"], cand["title"])
                cur = c.execute(
                    "INSERT INTO seed_chain_candidates(doi, title, title_norm, year, direction, linking_seeds_json, "
                    "n_linking, retrieved_raw_id, status, fetched_at, abstract, keywords_json, origins_json, "
                    "chain_round) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                    (cand["doi"], cand["title"], cand["title_norm"], cand["year"], _direction(cand["dirs"]),
                     json.dumps(cand["seeds"]), len(cand["seeds"]), hit["raw_id"] if hit else None, "new", now,
                     cand["abstract"], json.dumps(cand["keywords"]) if cand["keywords"] else None, json.dumps([org])))
                new_ids.append(int(cur.lastrowid))
            for k in merged:
                row, cand = existing[k], plan[k]
                seeds = json.loads(row["linking_seeds_json"] or "[]")
                seeds += [b for b in cand["seeds"] if b not in seeds]
                c.execute("UPDATE seed_chain_candidates SET origins_json=?, linking_seeds_json=?, n_linking=?, "
                          "direction=?, abstract=COALESCE(abstract, ?) WHERE candidate_id=?",
                          (json.dumps(_origins(row) + [org]), json.dumps(seeds), len(seeds),
                           _direction(_dirs(row["direction"]) | cand["dirs"]), cand["abstract"], row["candidate_id"]))
            c.execute("INSERT INTO candidate_imports(import_no, origin, file_name, sha256, n_records, n_new, n_merged, "
                      "n_skipped, new_ids_json, by, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (import_no, org, name, sha, len(recs), len(new), len(merged), n_seed + n_empty,
                       json.dumps(new_ids), author, now))
        # watch only this import's rows from now on, so a later chaining run's new rows do not block its undo (D23)
        own = new_ids + ids
        watch["where"], watch["params"] = f"candidate_id IN ({','.join('?' * len(own))})", own
    if n_known:
        msgs.append(f"{n_known} record{'s' if n_known != 1 else ''} already candidates from {org}")
    return {**out, "import_no": import_no, "messages": msgs, "candidates": candidates(project)}


def imports(project: Project) -> list[dict[str, Any]]:
    """The candidate imports, newest first, with how many of their new rows are still new."""
    out = []
    for r in project.store.query("SELECT * FROM candidate_imports ORDER BY import_no DESC"):
        row = dict(r)
        ids = json.loads(row.pop("new_ids_json") or "[]")
        row["still_new"] = project.store.scalar(
            f"SELECT COUNT(*) FROM seed_chain_candidates WHERE status='new' AND candidate_id IN "
            f"({','.join('?' * len(ids)) or 'NULL'})", ids) if ids else 0
        out.append(row)
    return out


def dismiss_import(project: Project, import_no: int, by: str | None = None) -> dict[str, Any]:
    """Dismiss the rows import ``import_no`` created that are still new (added ones stay; journalled, so Undo puts
    them back)."""
    r = project.store.one("SELECT * FROM candidate_imports WHERE import_no=?", (int(import_no),))
    if r is None:
        raise KeyError(f"no candidate import {import_no}")
    ids = json.loads(r["new_ids_json"] or "[]")
    marks = ",".join("?" * len(ids)) or "NULL"
    n = project.store.scalar(f"SELECT COUNT(*) FROM seed_chain_candidates WHERE status='new' AND candidate_id IN "
                             f"({marks})", ids) if ids else 0
    if n:
        with recording(project, "candidate_import_dismiss",
                       f"import {import_no} ({r['origin']}): {n} candidate(s) dismissed", author=by) as rec:
            rec.rows("seed_chain_candidates", f"candidate_id IN ({marks})", ids)
            project.store.execute(f"UPDATE seed_chain_candidates SET status='dismissed', by=?, at=? "
                                  f"WHERE status='new' AND candidate_id IN ({marks})",
                                  [current_author(by) or None, utcnow(), *ids])
    return {"import_no": int(import_no), "origin": r["origin"], "dismissed": n, "candidates": candidates(project)}


def _row(project: Project, candidate_id: int) -> dict:
    r = project.store.query("SELECT * FROM seed_chain_candidates WHERE candidate_id=?", (int(candidate_id),))
    if not r:
        raise KeyError(f"no candidate {candidate_id}")
    return dict(r[0])


def dismiss(project: Project, candidate_id: int, by: str | None = None) -> dict[str, Any]:
    """Keep the candidate with status ``dismissed``, who dismissed it and when. An added candidate cannot be
    dismissed (remove the seed paper instead)."""
    row = _row(project, candidate_id)
    if row["status"] == "added":
        raise ValueError(f"candidate {candidate_id} was added as {row['benchmark_id']}; remove that seed paper instead")
    if row["status"] != "dismissed":
        project.store.execute("UPDATE seed_chain_candidates SET status='dismissed', by=?, at=? WHERE candidate_id=?",
                              (current_author(by) or None, utcnow(), int(candidate_id)))
    return next(c for c in candidates(project) if c["candidate_id"] == int(candidate_id))


def add(project: Project, candidate_id: int, set_name: str, by: str | None = None):
    """Add the candidate as a seed paper in ``set_name`` with source ``seed_chain:<first linking seed>``, through the
    ordinary placing rule (2a's D4: a candidate the query retrieved may enter Validation, with a warning naming the
    imported record). A candidate with no linking seed (from a file that names none) gets source ``other_database``.
    Returns ``(paper, notices)``. A dismissed candidate may still be added."""
    from ..construct import seed
    from ..core.models import BenchmarkPaper

    row = _row(project, candidate_id)
    if row["status"] == "added":
        raise ValueError(f"candidate {candidate_id} was already added as {row['benchmark_id']}")
    for b in project.read_benchmark():
        if (row["doi"] and clean_doi(b.doi) == row["doi"]) or (row["title_norm"] and norm_title(b.title) == row["title_norm"]):
            raise ValueError(f"candidate {candidate_id} is already the seed paper {b.benchmark_id}")
    val = {b.benchmark_id for b in project.read_benchmark() if b.set == "Validation"}
    seeds = [b for b in json.loads(row["linking_seeds_json"] or "[]") if b not in val]
    origins = _origins(row) or ["openalex"]
    how = "seed chaining" if origins == ["openalex"] else ", ".join(origins)
    paper = BenchmarkPaper(benchmark_id="", title=row["title"] or "", doi=row["doi"], year=row["year"],
                           set=seed._set_name(set_name), note=f"from {how} (candidate {candidate_id})")
    # linked to a seed: seed chaining by whichever tool; an imported row with no named seed: another database
    [added] = seed._append(project, [paper], f"seed_chain:{seeds[0]}" if seeds else "other_database")
    project.store.execute("UPDATE seed_chain_candidates SET status='added', benchmark_id=?, by=?, at=? "
                          "WHERE candidate_id=?", (added.benchmark_id, current_author(by) or None, utcnow(),
                                                   int(candidate_id)))
    return added, seed.set_notices(project, added)
