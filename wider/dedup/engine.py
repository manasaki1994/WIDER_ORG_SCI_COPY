"""Deduplication engine: stages A (DOI), B (exact title), C (fuzzy, blocked), conflict-aware union,
manual queue, final clustering, masters and PRISMA counts. Port of the review's R pipeline."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from ..core.project import Project
from ..core.protocol import DedupProtocol
from ..core.provenance import utcnow
from ..core.text import norm_text, word_count
from ..enrich import abstracts
from .diagnostics import (
    METHOD_PRIORITY,
    RULE_TO_METHOD,
    Rec,
    diagnose,
    normalize_author,
    normalize_locator,
    page_start,
    publication_family,
)

MANUAL_METHODS = {"exact_doi": "exact_doi_conflict_manual_confirmed", "exact_normalized_title": "exact_title_manual_confirmed",
                  "fuzzy_title": "fuzzy_title_manual_confirmed"}

# tables whose rows point at records.record_id (foreign keys, plus the two match tables that store a record_id
# for the corpus scope). A re-run keeps record ids stable per cluster and re-maps these rows when two masters merge.
DOWNSTREAM_TABLES = ("enrichment_log", "ranking_rows", "sample_rows", "labels", "adjudications", "screen_calls",
                     "screen_decisions", "screen_call_reuse")   # every result set key: no protocol filter
DOWNSTREAM_REF_TABLES = ("benchmark_matches", "prior_review_matches")   # record_ref, scope='corpus'
# reviewer notes and tags merge instead of clashing: both records' notes and tags move to the surviving record
ANNOTATION_TABLES = ("record_notes", "record_tags")


class DedupRerunError(RuntimeError):
    """A deduplication re-run would orphan or collide rows that later stages wrote (screening decisions, labels,
    samples, rankings). Nothing is written; the message says which records and tables are involved."""


# ----------------------------------------------------------------------------- load
# the raw records deduplication reads (files the researcher keeps in, D5); raw_count() uses the same rows, so "+N
# imported since the last run" agrees
RAW_FROM = "FROM raw_records r JOIN sources s ON s.source_id=r.source_id AND s.included = 1"


def raw_count(project: Project) -> int:
    r = project.store.one(f"SELECT COUNT(*) n {RAW_FROM}")
    return int(r["n"]) if r else 0


def excluded_file_records(project: Project, summary: dict | None = None) -> int:
    """Records of files kept out of deduplication (D5): as the last run counted them, live before any run."""
    if summary is None:
        summary = json.loads(project.store.get_meta("dedup_summary") or "{}")
    if summary:
        return int(summary.get("excluded_file_records") or 0)
    r = project.store.one("SELECT COUNT(*) n FROM raw_records r JOIN sources s ON s.source_id=r.source_id AND s.included = 0")
    return int(r["n"]) if r else 0


def load_records(project: Project) -> dict[int, Rec]:
    rows = project.store.query(
        "SELECT r.raw_id, r.platform, r.title_norm, r.doi_norm, r.authors, r.year, r.journal, r.volume, r.issue, "
        f"r.pages, r.type, r.extra, s.file_name {RAW_FROM} ORDER BY r.raw_id")
    out = {}
    for r in rows:
        extra = json.loads(r["extra"] or "{}")
        first = (r["authors"] or "").split(";")[0]
        first = first.split(",")[0].strip() if "," in first else (first.split()[0] if first.split() else "")
        out[r["raw_id"]] = Rec(
            rid=r["raw_id"], title_clean=r["title_norm"] or "", doi=r["doi_norm"],
            first_author=normalize_author(first), year=r["year"],
            journal_clean=norm_text(r["journal"]) or None,
            volume=normalize_locator(r["volume"]), issue=normalize_locator(r["issue"]),
            page_start=page_start(r["pages"]), article_number=None,
            family=publication_family(r["type"]), source_file=r["file_name"],
            database_id=extra.get("native_id"), platform=r["platform"])
    return out


# ----------------------------------------------------------------------------- union-find with conflict sets
class SafeUnion:
    def __init__(self, recs: dict[int, Rec]):
        self.parent = {k: k for k in recs}
        self.dois = {k: ({recs[k].doi} if recs[k].doi else set()) for k in recs}
        self.fams = {k: ({recs[k].family} if recs[k].family != "other_or_unknown" else set()) for k in recs}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def try_union(self, a: int, b: int, method: str, allow_doi_alias: bool, check: bool = True) -> tuple[bool, str | None]:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True, None
        if check:
            da, db = self.dois[ra], self.dois[rb]
            if da and db and len(da | db) > max(len(da), len(db)) and not allow_doi_alias:
                return False, "doi_conflict"
            fa, fb = self.fams[ra], self.fams[rb]
            if method != "exact_doi" and fa and fb and not (fa & fb):
                return False, "family_conflict"
        lo, hi = min(ra, rb), max(ra, rb)
        self.parent[hi] = lo
        self.dois[lo] = self.dois[lo] | self.dois[hi]
        self.fams[lo] = self.fams[lo] | self.fams[hi]
        return True, None

    def components(self) -> dict[int, int]:
        return {k: self.find(k) for k in self.parent}


# ----------------------------------------------------------------------------- candidate generation
def stage_a(recs: dict[int, Rec], dp: DedupProtocol | None = None) -> list[dict]:
    dp = dp or DedupProtocol()
    by_doi = defaultdict(list)
    for r in recs.values():
        if r.doi:
            by_doi[r.doi].append(r.rid)
    pairs = []
    for ids in by_doi.values():
        if len(ids) < 2:
            continue
        for a, b in combinations(sorted(ids), 2):
            d = diagnose(recs[a], recs[b], "exact_doi", dp)
            override = (d["same_year"] and d["first_author_match"] and d["journal_match"] and d["volume_match"]
                        and (d["issue_match"] or d["page_start_match"]) and not d["publication_family_conflict"])
            ya, yb = recs[a].year, recs[b].year
            suspicious = d["title_similarity"] < dp.doi_conflict_title or (ya is not None and yb is not None and abs(ya - yb) > 2)
            conflict = suspicious and not override
            d["auto_duplicate_rule"] = None if conflict else ("exact_doi_full_citation_despite_title_variant" if suspicious else "exact_doi")
            d["auto_not_duplicate_rule"] = None
            d["match_method"] = "exact_doi_full_citation_title_variant" if (suspicious and override) else "exact_doi"
            d["auto_merge"] = not conflict
            d["allow_doi_alias"] = False
            pairs.append({"a": a, "b": b, **d})
    return pairs


def stage_b(recs: dict[int, Rec], seen: set[tuple[int, int]], dp: DedupProtocol | None = None) -> list[dict]:
    dp = dp or DedupProtocol()
    by_title = defaultdict(list)
    for r in recs.values():
        if r.title_clean:
            by_title[r.title_clean].append(r.rid)
    pairs = []
    for ids in by_title.values():
        if len(ids) < 2:
            continue
        for a, b in combinations(sorted(ids), 2):
            if (a, b) in seen:
                continue
            d = diagnose(recs[a], recs[b], "exact_normalized_title", dp)
            rule = d["auto_duplicate_rule"]
            d["match_method"] = RULE_TO_METHOD.get(rule) if rule else None
            d["auto_merge"] = rule is not None
            d["allow_doi_alias"] = bool(d["auto_merge"] and d["doi_conflict"])
            pairs.append({"a": a, "b": b, **d})
    return pairs


def stage_c(recs: dict[int, Rec], seen: set[tuple[int, int]], comp: dict[int, int], dp: DedupProtocol | None = None) -> list[dict]:
    dp = dp or DedupProtocol()
    base = [r for r in recs.values() if len(r.title_clean) >= 15]
    df = Counter()
    rare: dict[int, str | None] = {}
    toks = {}
    for r in base:
        ts = {t for t in r.title_clean.split() if len(t) >= 5}
        toks[r.rid] = ts
        df.update(ts)
    for r in base:
        ts = toks[r.rid]
        rare[r.rid] = min(ts, key=lambda t: (df[t], -len(t), t)) if ts else None
    cand: dict[tuple[int, int], str] = {}

    def add(a, b, reason):
        if a < b and (a, b) not in cand:
            cand[(a, b)] = reason

    by_author = defaultdict(list)
    by_prefix = defaultdict(list)
    by_rare = defaultdict(list)
    for r in base:
        if r.first_author and r.year is not None:
            by_author[r.first_author].append(r)
        pre = r.title_clean[:24]
        if len(pre) >= 15:
            by_prefix[pre].append(r)
        if rare[r.rid] and df[rare[r.rid]] <= 50 and r.year is not None:
            by_rare[rare[r.rid]].append(r)
    for group in by_author.values():
        for x, y in combinations(group, 2):
            if abs(x.year - y.year) <= 1:
                add(min(x.rid, y.rid), max(x.rid, y.rid), "first_author_and_year_within_one")
    for group in by_prefix.values():
        for x, y in combinations(group, 2):
            a, b = (x, y) if x.rid < y.rid else (y, x)
            if x.year is not None and y.year is not None and abs(x.year - y.year) <= 2:
                add(a.rid, b.rid, "title_prefix")
            elif a.year is None and len(a.title_clean[:24]) >= 20 and len(b.title_clean[:24]) >= 20:
                add(a.rid, b.rid, "title_prefix")
    for group in by_rare.values():
        for x, y in combinations(group, 2):
            if abs(x.year - y.year) <= 1:
                add(min(x.rid, y.rid), max(x.rid, y.rid), "rare_title_token_and_year")
    pairs = []
    for (a, b), reason in cand.items():
        if (a, b) in seen or comp[a] == comp[b]:
            continue
        d = diagnose(recs[a], recs[b], "fuzzy_title", dp)
        if d["title_similarity"] < dp.jw_candidate:
            continue
        rule = d["auto_duplicate_rule"]
        d["blocking_reason"] = reason
        d["match_method"] = RULE_TO_METHOD.get(rule) if rule else None
        d["auto_merge"] = rule is not None
        d["allow_doi_alias"] = bool(d["auto_merge"] and d["doi_conflict"])
        pairs.append({"a": a, "b": b, **d})
    return pairs


def _prio(m):
    return METHOD_PRIORITY.index(m) if m in METHOD_PRIORITY else len(METHOD_PRIORITY)


def safe_union(recs, pairs: list[dict]) -> SafeUnion:
    su = SafeUnion(recs)
    for p in sorted((p for p in pairs if p["auto_merge"]), key=lambda p: (_prio(p["match_method"]), p["a"], p["b"])):
        ok, why = su.try_union(p["a"], p["b"], p["match_method"], p["allow_doi_alias"], check=True)
        p["cluster_level_conflict"] = (not ok) and p["candidate_reason"] != "exact_doi"
        if not ok and p["candidate_reason"] != "exact_doi":
            p["auto_merge"] = False
            p["conflict_type"] = why
    return su


# ----------------------------------------------------------------------------- run
def run(project: Project, write_exports: bool = True) -> dict:
    st = project.store
    recs = load_records(project)
    dp = project.config.protocol.dedup
    pa = stage_a(recs, dp)
    seen = {(p["a"], p["b"]) for p in pa}
    pb = stage_b(recs, seen, dp)
    seen |= {(p["a"], p["b"]) for p in pb}
    for p in pa + pb:
        p["cluster_level_conflict"] = False
    su1 = safe_union(recs, pa + pb)
    comp1 = su1.components()
    pc = stage_c(recs, seen, comp1, dp)
    for p in pc:
        p["cluster_level_conflict"] = False
    all_pairs = pa + pb + pc
    su2 = safe_union(recs, all_pairs)
    comp = su2.components()

    # manual decisions already stored (keyed on raw pair)
    manual = {(r["raw_a"], r["raw_b"]): dict(r) for r in st.query(
        "SELECT raw_a, raw_b, decision, decided_by, decided_at FROM dedup_pairs WHERE decision IN ('manual_same','manual_different','manual_uncertain')")}

    # non-duplicate component pairs
    nondup_comp = set()
    for p in all_pairs:
        if p["auto_not_duplicate_rule"] and p["candidate_reason"] != "exact_doi":
            nondup_comp.add((min(comp[p["a"]], comp[p["b"]]), max(comp[p["a"]], comp[p["b"]])))

    # manual queue: representative per component pair
    queue_pairs = [p for p in all_pairs if not p["auto_merge"] and not p["auto_not_duplicate_rule"]
                   and comp[p["a"]] != comp[p["b"]]
                   and (min(comp[p["a"]], comp[p["b"]]), max(comp[p["a"]], comp[p["b"]])) not in nondup_comp]
    reason_rank = {"exact_doi": 1, "exact_normalized_title": 2, "fuzzy_title": 3}

    def prio_rank(p):
        if p["candidate_reason"] == "exact_doi" or p.get("cluster_level_conflict"):
            return 1
        if (p["first_author_match"] and p["same_year"] and p["title_similarity"] >= dp.jw_strong_title) or p["doi_conflict"]:
            return 2
        return 3

    by_comp_pair: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for p in queue_pairs:
        by_comp_pair[(min(comp[p["a"]], comp[p["b"]]), max(comp[p["a"]], comp[p["b"]]))].append(p)
    queue = []
    for key, lst in by_comp_pair.items():
        lst.sort(key=lambda p: (reason_rank[p["candidate_reason"]], prio_rank(p), -p["title_similarity"], p["a"], p["b"]))
        lst[0]["queue"] = True
        rep = dict(lst[0])
        rep["review_priority_rank"] = prio_rank(lst[0])
        rep["raw_candidate_comparisons_collapsed"] = len(lst)
        rep["candidate_reasons_all"] = "; ".join(sorted({p["candidate_reason"] for p in lst}))
        queue.append(rep)

    # manual decisions apply at component level: a decision on any raw pair bridging the same two
    # automatic components counts for the representative pair (as candidate_pair_id did in the R pipeline)
    manual_by_comp: dict[tuple[int, int], dict] = {}
    rows_by_comp: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for (x, y), m in manual.items():
        if x in comp and y in comp and comp[x] != comp[y]:
            manual_by_comp.setdefault((min(comp[x], comp[y]), max(comp[x], comp[y])), m)
            rows_by_comp[(min(comp[x], comp[y]), max(comp[x], comp[y]))].append(m)
    copied_from: dict[tuple[int, int], tuple[int, int]] = {}
    for p in queue:
        key = (min(comp[p["a"]], comp[p["b"]]), max(comp[p["a"]], comp[p["b"]]))
        if (p["a"], p["b"]) not in manual and key in manual_by_comp:
            manual[(p["a"], p["b"])] = manual_by_comp[key]
            copied_from[(p["a"], p["b"])] = (manual_by_comp[key]["raw_a"], manual_by_comp[key]["raw_b"])

    # final clustering: automatic merges + manual duplicates (plain union)
    edges = [p for p in all_pairs if p["auto_merge"]]
    manual_edges = []
    for p in queue:
        m = manual.get((p["a"], p["b"]))
        if m and m["decision"] == "manual_same":
            e = dict(p)
            e["match_method"] = MANUAL_METHODS[p["candidate_reason"]]
            e["auto_merge"] = True
            manual_edges.append(e)
    fu = SafeUnion(recs)
    removed_by = Counter()
    merged_edges = []
    for e in sorted(edges + manual_edges, key=lambda p: (_prio(p["match_method"]), p["a"], p["b"])):
        ra, rb = fu.find(e["a"]), fu.find(e["b"])
        fu.try_union(e["a"], e["b"], e["match_method"], True, check=False)
        e["merged_new_component"] = ra != rb
        if ra != rb:
            removed_by[e["match_method"]] += 1
            merged_edges.append(e)
    final = fu.components()
    run_at = utcnow()
    run_queue = _run_queue_value(st, queue, manual, copied_from, rows_by_comp, comp, merged_edges, run_at)

    # persist pairs and clusters
    with st.tx() as c:
        c.execute("DELETE FROM dedup_pairs WHERE decision NOT IN ('manual_same','manual_different','manual_uncertain')")
        for p in all_pairs:
            dec = manual.get((p["a"], p["b"]), {}).get("decision") or ("queued" if p.get("queue") else "auto")
            diag = {k: v for k, v in p.items() if k not in ("a", "b")}
            c.execute(
                "INSERT INTO dedup_pairs(raw_a,raw_b,stage,rule,score,diagnostics,decision) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(raw_a,raw_b) DO UPDATE SET stage=excluded.stage, rule=excluded.rule, score=excluded.score, "
                "diagnostics=CASE WHEN json_extract(dedup_pairs.diagnostics,'$.keep') IS NULL THEN excluded.diagnostics "
                "ELSE json_set(excluded.diagnostics,'$.keep',json_extract(dedup_pairs.diagnostics,'$.keep')) END",   # the kept record stays
                (p["a"], p["b"], p["candidate_reason"], p["auto_duplicate_rule"] or p["auto_not_duplicate_rule"] or "",
                 p["title_similarity"], json.dumps(diag, default=str), dec))
        c.execute("DELETE FROM dedup_clusters")
        c.executemany("INSERT INTO dedup_clusters(raw_id,cluster_id,is_master) VALUES(?,?,0)",
                      [(rid, root) for rid, root in final.items()])
        # masters are rebuilt in the same transaction: a refused re-run leaves the store as it was
        m = build_masters(project, final, c)
    masters = m["unique_records"]
    aside = _record_set_aside(project, m["set_aside"], m["set_aside_at"])
    pending = sum(1 for p in queue if (p["a"], p["b"]) not in manual)
    uncertain = sum(1 for p in queue if manual.get((p["a"], p["b"]), {}).get("decision") == "manual_uncertain")
    summary = {
        "total_records_imported": len(recs),
        "excluded_file_records": excluded_file_records(project, {}),
        "duplicate_clusters": sum(1 for n in Counter(final.values()).values() if n > 1),
        "duplicate_records_removed": len(recs) - masters,
        "unique_records": masters,
        "unique_records_kept_from_previous_run": m["kept"],
        "unique_records_new": m["new"],
        "unique_records_merged_into_existing": m["merged"],
        "unique_records_dropped": m["dropped"],
        "raw_candidate_comparisons_before_component_collapse": len(queue_pairs),
        "automatic_duplicate_candidate_pairs_accepted": len(edges),
        "manual_component_pairs_exported": len(queue),
        "unresolved_manual_component_pairs_retained": pending,
        "uncertain_manual_component_pairs": uncertain,
        "automatically_resolved_nonduplicate_raw_pairs": sum(1 for p in all_pairs if p["auto_not_duplicate_rule"]),
        "automatically_resolved_nonduplicate_component_pairs": len(nondup_comp),
        "confirmed_not_duplicate_pairs": sum(1 for m in manual.values() if m["decision"] == "manual_different"),
        "manually_confirmed_duplicate_pairs": len(manual_edges),
        **{f"duplicate_records_removed_by_{k}": v for k, v in sorted(removed_by.items())},
    }
    rule_summary = Counter()
    for p in all_pairs:
        if p["auto_duplicate_rule"]:
            rule_summary[("duplicate", p["auto_duplicate_rule"])] += 1
        if p["auto_not_duplicate_rule"]:
            rule_summary[("not_duplicate", p["auto_not_duplicate_rule"])] += 1
    st.set_meta("dedup_summary", json.dumps(summary))
    st.set_meta("dedup_run_at", run_at)
    st.set_meta("dedup_run_manual", json.dumps(_manual_decisions(st)))
    st.set_meta("dedup_run_queue", json.dumps(run_queue))
    if write_exports:
        export_audit(project, recs, all_pairs, queue, manual, summary, rule_summary, edges + manual_edges)
    out = {"summary": summary, "rule_summary": {f"{d}:{r}": n for (d, r), n in rule_summary.most_common()},
           "queue": len(queue), "pending": pending, "set_aside": aside}
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_dedup
    record(project, summarize_dedup(out))
    return out


def _run_queue_value(st, queue, manual, copied_from, rows_by_comp, comp, merged_edges, run_at) -> dict:
    """The meta value ``dedup_run_queue`` (design-pass-a1-dedup D6): the pairs this run kept for the researcher, with
    the decision it applied and who signed it, and every edge that removed a record. A decision the run copied onto a
    representative is stored there without a signer, so the signer is carried forward from the previous value, or
    looked up among the cluster pair's rows with the same decision (signer only; the decision applied is unchanged)."""
    prev = json.loads(st.get_meta("dedup_run_queue") or "{}")
    prev_by_pair: dict[tuple[int, int], dict] = {}
    for e in prev.get("merged", []):
        if e.get("by") != "WIDER":
            prev_by_pair[(e["a"], e["b"])] = {**e, "decision": "manual_same"}
    for e in prev.get("queue", []):
        prev_by_pair[(e["a"], e["b"])] = e

    def carried(pair, decision):
        e = prev_by_pair.get(pair)
        if e and e.get("decision") == decision and (e.get("by") or e.get("source")):
            return e.get("source"), e.get("by"), e.get("at")
        return None

    out_queue = []
    for p in queue:
        a, b = p["a"], p["b"]
        m = manual.get((a, b))
        entry = {"a": a, "b": b, "reason": p["candidate_reason"], "source": None, "decision": None, "by": None, "at": None}
        if m:
            entry["decision"] = m["decision"]
            entry["by"], entry["at"] = m["decided_by"], m["decided_at"]
            src = copied_from.get((a, b))
            if src:
                entry["source"] = list(src)
            if not entry["by"]:
                if src is None:
                    got = carried((a, b), m["decision"])   # a copy stored by an earlier run on this representative
                    if got:
                        entry["source"], entry["by"], entry["at"] = got[0], got[1], got[2] or entry["at"]
                else:
                    got = carried(src, m["decision"])      # the source row is itself an earlier copy
                    if got:
                        entry["source"] = got[0] or entry["source"]
                        entry["by"], entry["at"] = got[1], got[2] or entry["at"]
                    if not entry["by"]:
                        key = (min(comp[a], comp[b]), max(comp[a], comp[b]))
                        signed = [r for r in rows_by_comp.get(key, []) if r["decision"] == m["decision"] and r["decided_by"]]
                        if signed:
                            entry["by"], entry["at"] = signed[0]["decided_by"], signed[0]["decided_at"]
        out_queue.append(entry)
    by_pair = {(e["a"], e["b"]): e for e in out_queue}
    merged = []
    for e in merged_edges:
        if e["match_method"] in MANUAL_METHODS.values():
            q = by_pair.get((e["a"], e["b"]), {})
            merged.append({"a": e["a"], "b": e["b"], "match_method": e["match_method"], "source": q.get("source"),
                           "by": q.get("by"), "at": q.get("at")})
        else:
            merged.append({"a": e["a"], "b": e["b"], "match_method": e["match_method"], "source": None,
                           "by": "WIDER", "at": run_at})
    return {"queue": out_queue, "merged": merged}


def run_queue(project: Project) -> dict | None:
    """The stored value of the last run's queue and merges, or None for a run made before WIDER kept it."""
    v = project.store.get_meta("dedup_run_queue")
    return json.loads(v) if v else None


def last_queue(project: Project) -> list[dict]:
    """The pairs the last run kept for the researcher ({a, b, reason, source, decision, by, at}). For a run stored
    before WIDER kept that list, the rows whose diagnostics carry the "queue" flag (which a decided pair can keep
    after a later import links its two records)."""
    v = run_queue(project)
    if v is not None:
        return v["queue"]
    out = []
    for r in project.store.query("SELECT raw_a, raw_b, stage, decision, decided_by, decided_at, diagnostics FROM dedup_pairs "
                                 "ORDER BY raw_a, raw_b"):
        if json.loads(r["diagnostics"] or "{}").get("queue"):
            dec = r["decision"] if r["decision"].startswith("manual_") else None
            out.append({"a": r["raw_a"], "b": r["raw_b"], "reason": r["stage"], "source": None, "decision": dec,
                        "by": r["decided_by"] if dec else None, "at": r["decided_at"] if dec else None})
    return out


# ----------------------------------------------------------------------------- read functions for the page (D5-D7)
# a candidate reason or a merge method in words; the manual methods read "Removed by your decision" on the list
REASON_WORDS = {
    "exact_doi": "Same DOI",
    "exact_normalized_title": "Same title",
    "fuzzy_title": "Similar title",
    "exact_doi_full_citation_title_variant": "Same DOI and citation, title spelt differently",
    "exact_title_full_citation": "Same title and citation",
    "verified_doi_alias_full_citation": "Same title and citation, DOI of another version",
    "exact_title_two_locator_corroboration": "Same title and year, two of volume, issue and pages agree",
    "exact_title_one_year_metadata_discrepancy": "Same title, journal, volume and first page, year one apart",
    "high_similarity_full_citation": "Nearly the same title, same citation",
    "high_similarity_verified_doi_alias": "Nearly the same title, same citation, DOI of another version",
    "fuzzy_title_two_locator_corroboration": "Similar title, same year, two of volume, issue and pages agree",
    "exact_doi_conflict_manual_confirmed": "Same DOI, confirmed by your decision",
    "exact_title_manual_confirmed": "Same title, confirmed by your decision",
    "fuzzy_title_manual_confirmed": "Similar title, confirmed by your decision",
}
DECISION_WORDS = {"manual_same": "Duplicate", "manual_different": "Not duplicate", "manual_uncertain": "Unsure"}


def reason_words(key: str | None) -> str:
    return REASON_WORDS.get(key or "", (key or "").replace("_", " "))


def _match_norm(s: str) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]|_", " ", s).split())


def match_word(a, b) -> str:
    """The match word of one field of a pair (D9, the same list as ``DedupView.matchWord`` in dedup.js): "same",
    "spelling" (equal once case, accents, punctuation and spacing are ignored), "nearly" (word-set Jaccard >= 0.8),
    "only_a", "only_b", "missing" or "differs"."""
    a = "" if a is None else str(a).strip()
    b = "" if b is None else str(b).strip()
    if not a and not b:
        return "missing"
    if not b:
        return "only_a"
    if not a:
        return "only_b"
    if a == b:
        return "same"
    na, nb = _match_norm(a), _match_norm(b)
    if na == nb:
        return "spelling"
    wa, wb = set(na.split()), set(nb.split())
    if wa and wb and len(wa & wb) / len(wa | wb) >= 0.8:
        return "nearly"
    return "differs"


def _signed(e: dict, date: bool = True) -> str:
    """"copied from raw records x and y, ana, <date>" / "ana, <date>" / "signer not recorded"."""
    parts = [f"copied from raw records {e['source'][0]} and {e['source'][1]}"] if e.get("source") else []
    parts.append(e.get("by") or "signer not recorded")
    if date and e.get("at"):
        parts.append(str(e["at"])[:10])
    return ", ".join(parts)


def _raw_info(project: Project, ids) -> dict[int, dict]:
    ids = sorted(set(ids))
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    return {r["raw_id"]: dict(r) for r in project.store.query(
        f"SELECT raw_id, title, authors, year, journal, platform, doi FROM raw_records WHERE raw_id IN ({q})", ids)}


def _replayed_merges(project: Project) -> list[dict]:
    """For a run stored before ``dedup_run_queue``: replay the final union from the current rows (automatic edges,
    then the queued pairs decided duplicate), as run() orders them."""
    edges = []
    for r in project.store.query("SELECT raw_a, raw_b, stage, decision, decided_by, decided_at, diagnostics FROM dedup_pairs"):
        d = json.loads(r["diagnostics"] or "{}")
        if d.get("auto_merge") and d.get("match_method"):
            edges.append({"a": r["raw_a"], "b": r["raw_b"], "match_method": d["match_method"], "source": None, "by": "WIDER",
                          "at": project.store.get_meta("dedup_run_at")})
        elif r["decision"] == "manual_same" and d.get("queue") and r["stage"] in MANUAL_METHODS:
            edges.append({"a": r["raw_a"], "b": r["raw_b"], "match_method": MANUAL_METHODS[r["stage"]], "source": None,
                          "by": r["decided_by"], "at": r["decided_at"]})
    parent: dict[int, int] = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    out = []
    for e in sorted(edges, key=lambda e: (_prio(e["match_method"]), e["a"], e["b"])):
        ra, rb = find(e["a"]), find(e["b"])
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
            out.append(e)
    return out


def merged_pairs(project: Project, limit: int = 200) -> dict:
    """The pairs whose merge removed a record at the last run, one per removed record: ``{rows, total, approximate}``.
    Read from the list the run stored, so a decision changed after the run does not change it until the next run;
    for a run stored before that list, replayed from the current rows (``approximate: true``)."""
    v = run_queue(project)
    edges = v["merged"] if v is not None else _replayed_merges(project)
    info = _raw_info(project, [x for e in edges for x in (e["a"], e["b"])])
    rows = []
    for e in edges[:limit]:
        manual = e["match_method"] in MANUAL_METHODS.values()
        text = (f"Removed by your decision ({_signed(e)})" if manual else
                f"Removed by WIDER on {str(e.get('at') or '')[:10]}: {reason_words(e['match_method'])}")
        a, b = info.get(e["a"], {}), info.get(e["b"], {})
        rows.append({**e, "manual": manual, "reason": "Removed by your decision" if manual else reason_words(e["match_method"]),
                     "text": text, **{f"{k}_a": a.get(k) for k in ("title", "authors", "year", "journal", "platform", "doi")},
                     **{f"{k}_b": b.get(k) for k in ("title", "authors", "year", "journal", "platform", "doi")}})
    return {"rows": rows, "total": len(edges), "approximate": v is None}


def short_authors(authors: str | None) -> str:
    names = [a.strip() for a in (authors or "").split(";") if a.strip()]
    return "; ".join(names[:3]) + ("; et al." if len(names) > 3 else "")


def unique_records(project: Project, limit: int = 200) -> list[dict]:
    """The records left after the last run, with the number of imported records each was made from."""
    rows = project.store.query(
        "SELECT m.record_id, m.title, m.authors, m.year, m.journal, COUNT(c.raw_id) AS n_raw FROM records m "
        "LEFT JOIN dedup_clusters c ON c.cluster_id=m.cluster_id GROUP BY m.record_id ORDER BY m.record_id LIMIT ?", (limit,))
    return [{**dict(r), "authors_short": short_authors(r["authors"])} for r in rows]


def flow_counts(project: Project, summary: dict | None = None) -> dict:
    """The flow chart's numbers (D6): the stored run's summary for what PRISMA reports, live counts for the pairs."""
    st = project.store
    if summary is None:
        summary = json.loads(st.get_meta("dedup_summary") or "{}")
    has_run = bool(summary)
    live = raw_count(project)
    imported = summary.get("total_records_imported", live) if has_run else live
    per = {k[len("duplicate_records_removed_by_"):]: v for k, v in summary.items() if k.startswith("duplicate_records_removed_by_")}
    removed = summary.get("duplicate_records_removed") if has_run else None
    split = bool(per) or removed == 0  # a run that removed nothing has nothing to split
    removed_manual = sum(v for k, v in per.items() if k in MANUAL_METHODS.values()) if split else None
    current = {(r["raw_a"], r["raw_b"]): r["decision"] for r in st.query("SELECT raw_a, raw_b, decision FROM dedup_pairs")}
    lq = last_queue(project) if has_run else []
    decisions = [current.get((e["a"], e["b"])) for e in lq]
    to_decide = sum(1 for d in decisions if not (d or "").startswith("manual_"))
    r = st.one("SELECT COUNT(*) n FROM sources WHERE included = 1")
    flow = {
        "has_run": has_run, "imported": imported, "imported_since_run": max(0, live - imported) if has_run else 0,
        "files": int(r["n"]) if r else 0, "excluded_file_records": excluded_file_records(project, summary),
        "removed": removed, "removed_manual": removed_manual,
        "removed_auto": (removed - removed_manual) if split and removed is not None else None, "split_recorded": split,
        "left": summary.get("unique_records") if has_run else None, "provisional": is_provisional(summary) if has_run else False,
        "to_decide": to_decide, "decided": len(lq) - to_decide, "unsure": decisions.count("manual_uncertain"),
        "decided_since": decided_since_run(project) if has_run else 0,
    }
    flow["message"] = flow_message(flow)
    return flow


def flow_message(flow: dict) -> str:
    """The sentence beside the flow chart, written from the current counts (D5)."""
    if not flow["has_run"]:
        return "No deduplication run yet. Press Run deduplication to find the duplicates."
    n = flow["to_decide"]
    if n:
        return (f"{n} pairs need your decision." if n != 1 else "1 pair needs your decision.") + \
            " The record count stays provisional until every pair is decided."
    if flow["decided_since"]:
        return "Every pair is decided. Press Apply decisions and check again to update the counts."
    n = flow["imported_since_run"]
    if n:
        return (f"{n} records were" if n != 1 else "1 record was") + \
            " imported since the last run. Press Apply decisions and check again to include them."
    if flow["provisional"]:
        u = flow["unsure"]
        return f"{flow['left']} records left, provisional while " + (f"{u} pairs are" if u != 1 else "1 pair is") + " Unsure."
    return f"Deduplication is done: {flow['left']} records left."


def removed_line(flow: dict) -> str:
    """"6 duplicates removed (5 by WIDER, 1 by your decisions)", as the box and ``wider dedup prisma`` read."""
    if flow["removed"] is None:
        return ""
    if not flow["split_recorded"]:
        return f"{flow['removed']} duplicates removed (split not recorded)"
    return f"{flow['removed']} duplicates removed ({flow['removed_auto']} by WIDER, {flow['removed_manual']} by your decisions)"


def queue_all(project: Project) -> list[dict]:
    """The last run's queue as the page lists it: every pair, open or decided now, with its status word and signer."""
    lq = last_queue(project)
    rows = {(r["raw_a"], r["raw_b"]): r for r in queue_rows(project, pending_only=False)}
    out = []
    for e in lq:
        r = rows.get((e["a"], e["b"]))
        if r is None:
            continue
        d = dict(r)
        dec = d["decision"] if (d["decision"] or "").startswith("manual_") else None
        by, at, source = d.get("decided_by"), d.get("decided_at"), None
        if dec and e.get("source") and not by and dec == e.get("decision"):
            by, at, source = e.get("by"), e.get("at") or at, e.get("source")
        d.update(status=dec or "open", status_words=DECISION_WORDS.get(dec, "Open"), signed_by=by if dec else None,
                 signed_at=at if dec else None, source=source, reason_words=reason_words(e.get("reason")))
        d["text"] = d["status_words"] + (f" ({_signed({'source': source, 'by': by}, date=False)})" if dec else "")
        out.append(d)
    return out


# ----------------------------------------------------------------------------- masters
def _completeness(r) -> int:
    return (2 * bool(r["doi"]) + 2 * bool(r["abstract"]) + sum(bool(r[k]) for k in
            ("title", "authors", "year", "journal", "volume", "issue", "pages", "keywords")))


def _mode(vals):
    vals = [v for v in vals if v]
    if not vals:
        return None
    c = Counter(vals)
    best = max(c.values())
    for v in vals:
        if c[v] == best:
            return v


def _downstream_counts(c: sqlite3.Connection, record_id: int) -> dict[str, int]:
    out = {}
    for t in DOWNSTREAM_TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE record_id=?", (record_id,)).fetchone()[0]
        if n:
            out[t] = n
    for t in DOWNSTREAM_REF_TABLES:
        n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE scope='corpus' AND record_ref=?", (record_id,)).fetchone()[0]
        if n:
            out[t] = n
    for t in ANNOTATION_TABLES:   # a note must never vanish silently with its record
        n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE record_id=?", (record_id,)).fetchone()[0]
        if n:
            out[t] = n
    return out


def downstream_for_source(c: sqlite3.Connection, source_id: int) -> dict[str, int]:
    """What later stages hold on records only this file brought (D5): summed over the masters whose cluster's raw
    records all come from it. Empty before the first deduplication run."""
    rows = c.execute(
        "SELECT rec.record_id FROM records rec JOIN dedup_clusters dc ON dc.cluster_id = rec.cluster_id "
        "JOIN raw_records r ON r.raw_id = dc.raw_id GROUP BY rec.record_id "
        "HAVING SUM(r.source_id != ?) = 0", (source_id,)).fetchall()
    out: dict[str, int] = {}
    for (record_id,) in rows:
        for t, n in _downstream_counts(c, record_id).items():
            out[t] = out.get(t, 0) + n
    return out


# what a researcher calls the rows of each table, for messages (no table names on the pages)
ROW_WORDS = {"enrichment_log": "abstract lookups", "ranking_rows": "ranking places", "sample_rows": "sample places",
             "labels": "labels", "adjudications": "adjudications", "screen_calls": "screening calls",
             "screen_decisions": "screening decisions", "screen_call_reuse": "reused screening calls",
             "benchmark_matches": "seed paper matches", "prior_review_matches": "prior review matches",
             "record_notes": "notes", "record_tags": "tags"}
# screening rows of a merged-away record that clash with the survivor's are set aside, not refused: the survivor's
# rows stay, the other's are kept in merge_set_aside and named in History (owner f-20261101, bug 1)
SCREEN_TABLES = ("screen_calls", "screen_call_reuse", "screen_decisions")


def _fmt_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{n} {ROW_WORDS.get(t, t)}" for t, n in counts.items())


def _set_aside(c: sqlite3.Connection, t: str, rowid: int, old_id: int, new_id: int, at: str) -> dict:
    row = dict(c.execute(f"SELECT * FROM {t} WHERE rowid=?", (rowid,)).fetchone())
    c.execute("INSERT INTO merge_set_aside(at,from_table,record_id,kept_record_id,row) VALUES(?,?,?,?,?)",
              (at, t, old_id, new_id, json.dumps(row, default=str)))
    c.execute(f"DELETE FROM {t} WHERE rowid=?", (rowid,))
    return row


def _move_screening(c: sqlite3.Connection, old_id: int, new_id: int, at: str) -> dict:
    """Move a merged-away record's screening rows to the survivor; where the survivor already has the same row (the
    same run and pass, or a decision in the same result set), the survivor's stays and the other is set aside."""
    out = {"rows": 0, "differ": []}
    aside_calls: set[int] = set()
    for t in SCREEN_TABLES:
        for (rowid,) in c.execute(f"SELECT rowid FROM {t} WHERE record_id=?", (old_id,)).fetchall():
            if t == "screen_call_reuse":
                src = c.execute("SELECT source_call_id FROM screen_call_reuse WHERE rowid=?", (rowid,)).fetchone()[0]
                if src in aside_calls:
                    _set_aside(c, t, rowid, old_id, new_id, at)
                    out["rows"] += 1
                    continue
            try:
                c.execute(f"UPDATE {t} SET record_id=? WHERE rowid=?", (new_id, rowid))
            except sqlite3.IntegrityError:
                row = _set_aside(c, t, rowid, old_id, new_id, at)
                out["rows"] += 1
                if t == "screen_calls":
                    aside_calls.add(row["call_id"])
                elif t == "screen_decisions":
                    kept = c.execute("SELECT decision FROM screen_decisions WHERE protocol=? AND record_id=?",
                                     (row["protocol"], new_id)).fetchone()
                    if kept and kept[0] != row["decision"]:
                        out["differ"].append((row["protocol"], kept[0], row["decision"]))
    return out


def _remap_downstream(c: sqlite3.Connection, old_id: int, new_id: int, at: str = "") -> dict:
    """Point every downstream row of a merged-away master at the surviving master. A uniqueness clash (both masters
    already screened under the same protocol, labelled in the same sample, ...) refuses the whole run."""
    # rankings are derived and re-run from the corpus: where both masters are ranked, the survivor's row stays and the
    # merged-away one is dropped, so a merge after ranking is never refused
    c.execute("DELETE FROM ranking_rows WHERE record_id=? AND ranking_id IN "
              "(SELECT ranking_id FROM ranking_rows WHERE record_id=?)", (old_id, new_id))
    # the merged-away abstract versions follow the survivor's, renumbered; the rows moved with them name the new
    # numbers (D8). Highest first, so a renumbered row is never renumbered again.
    vmap = abstracts.move_on_merge(c, old_id, new_id)
    for old_v, new_v in sorted(vmap.items(), reverse=True):
        for t in ("labels", "screen_calls", "review_package_items"):
            c.execute(f"UPDATE {t} SET abstract_version=? WHERE record_id=? AND abstract_version=?", (new_v, old_id, old_v))
    moved = _move_screening(c, old_id, new_id, at or utcnow())
    for t in DOWNSTREAM_TABLES:
        if t in SCREEN_TABLES:
            continue
        try:
            c.execute(f"UPDATE {t} SET record_id=? WHERE record_id=?", (new_id, old_id))
        except sqlite3.IntegrityError as e:
            raise DedupRerunError(
                f"Deduplication re-run refused: records {old_id} and {new_id} now merge into one, but both already have "
                f"{ROW_WORDS.get(t, t)} in the same sample. Decide the pair Not duplicates or Unsure on Deduplication "
                f"(or undo the decision in History), or remove one record from the sample, then run again; nothing was "
                f"changed.") from e
    for t in DOWNSTREAM_REF_TABLES:
        c.execute(f"UPDATE {t} SET record_ref=? WHERE scope='corpus' AND record_ref=?", (new_id, old_id))
    c.execute("UPDATE record_notes SET record_id=? WHERE record_id=?", (new_id, old_id))
    # review package items follow their record but are never downstream work: a package is permanent, and counting
    # its items would refuse every later re-run (reviewer-roles-and-blinding D12)
    c.execute("UPDATE OR IGNORE review_package_items SET record_id=? WHERE record_id=?", (new_id, old_id))
    # corrections, seals and stated independence follow their record the same way (label-completion-and-sealing D2)
    for t in ("label_corrections", "label_seals", "label_independence"):
        c.execute(f"UPDATE OR IGNORE {t} SET record_id=? WHERE record_id=?", (new_id, old_id))
    c.execute("UPDATE OR IGNORE record_tags SET record_id=? WHERE record_id=?", (new_id, old_id))
    c.execute("DELETE FROM record_tags WHERE record_id=?", (old_id,))   # the same tag by the same author was already there
    return moved


def build_masters(project: Project, final: dict[int, int], c: sqlite3.Connection) -> dict:
    """Write the unique records for the final clustering inside the caller's transaction.

    Record ids are stable across re-runs (living reviews): a cluster keeps its ``record_id`` as long as its root raw
    record stays the root; new clusters get new ids; a master whose cluster merged into another has its downstream
    rows re-mapped to the surviving master; a master whose raw records vanished is dropped, or the run is refused with
    ``DedupRerunError`` when later stages still refer to it. Enriched abstracts survive unless the exports now carry
    a longer one. Returns counts: unique_records, kept, new, merged, dropped."""
    st = project.store
    rows = {r["raw_id"]: dict(r) for r in st.query("SELECT * FROM raw_records")}
    clusters = defaultdict(list)
    for rid, root in final.items():
        clusters[root].append(rows[rid])
    thin_words = project.config.thresholds.thin_words
    existing = {r["cluster_id"]: dict(r) for r in c.execute(
        "SELECT record_id, cluster_id, doi, abstract, abstract_words, thin, abstract_source FROM records")}
    out = []
    masters = []
    versioned = []   # (root, abstract, source, import time) for record_abstracts (D8)
    imported_at = {r["source_id"]: r["imported_at"] for r in st.query("SELECT source_id, imported_at FROM sources")}
    # "Duplicates, keep A/B": the kept raw record's title, authors, year, journal and DOI stand for its cluster (the
    # latest such decision wins); the abstract is still the longest and the record id still follows the anchor
    kept = {r["k"]: i for i, r in enumerate(c.execute(
        "SELECT json_extract(diagnostics,'$.keep') AS k FROM dedup_pairs WHERE decision='manual_same' "
        "AND json_extract(diagnostics,'$.keep') IS NOT NULL ORDER BY decided_at, raw_a, raw_b"))}
    for root, members in sorted(clusters.items()):
        members.sort(key=lambda r: (-_completeness(r), r["raw_id"]))
        anchor = members[0]
        pref = max((m for m in members if m["raw_id"] in kept), key=lambda m: kept[m["raw_id"]], default=None)
        longest = lambda k: (pref[k] if pref and pref[k] else  # noqa: E731
                             max((m[k] for m in members if m[k]), key=len, default=None))
        authors = pref["authors"] if pref and pref["authors"] else max(members, key=lambda m: (len((m["authors"] or "").split(";")) if m["authors"] else -1, len(m["authors"] or "")))["authors"]
        abstract = max((m["abstract"] for m in members if m["abstract"]), key=len, default=None)
        doi = pref["doi"] if pref and pref["doi"] else _mode([m["doi"] for m in members])
        kws = sorted({k.strip() for m in members if m["keywords"] for k in re.split(r";\s*", m["keywords"]) if k.strip()})
        field_sources = {"title": next(m["raw_id"] for m in members if m["title"] == longest("title")) if longest("title") else None,
                         "abstract": next((m["raw_id"] for m in members if m["abstract"] == abstract), None),
                         "doi": next((m["raw_id"] for m in members if m["doi"] == doi), None)}
        platforms = sorted({m["platform"] for m in members})
        key = f"M{anchor['raw_id']:07d}"
        words, source = word_count(abstract), "export"
        prev = existing.get(root)
        if prev and prev["abstract_source"] != "export" and (prev["abstract_words"] or 0) >= words:
            abstract, words, source = prev["abstract"], prev["abstract_words"], prev["abstract_source"]   # keep enrichment
            field_sources["abstract"] = None
        if prev and not doi:
            doi = prev["doi"]   # enrichment may have filled a DOI the exports lack
        out.append((root, key, longest("title"), authors, pref["year"] if pref and pref["year"] else _mode([m["year"] for m in members]), longest("journal"), doi,
                    abstract, "; ".join(kws) or None, next((m["url"] for m in members if m["url"]), None),
                    len(members), ";".join(platforms), json.dumps(field_sources),
                    words, int(words < thin_words), source))
        masters.append((anchor["raw_id"], root))
        versioned.append((root, abstract, source, imported_at.get(next((m["source_id"] for m in members
                                                                        if m["abstract"] == abstract), None))))

    roots = set(clusters)
    vanished = {root: r["record_id"] for root, r in existing.items() if root not in roots}
    # a vanished master's successor is the cluster its old root raw record now belongs to (None if it was removed)
    successor = {root: final.get(root) for root in vanished}
    for root, old_id in vanished.items():
        if successor[root] is None:
            counts = _downstream_counts(c, old_id)
            if counts:
                raise DedupRerunError(
                    f"Deduplication re-run refused: unique record {old_id} (cluster of raw record {root}) no longer exists, "
                    f"but later stages still refer to it ({_fmt_counts(counts)}). Remove those rows or restore the "
                    f"source file, then re-run; nothing was changed.")

    cols = ("cluster_id,key,title,authors,year,journal,doi,abstract,keywords,url,n_sources,platforms,field_sources,"
            "abstract_words,thin,abstract_source")
    sets = ", ".join(f"{k}=?" for k in cols.split(",")[1:])
    if existing:
        c.execute("UPDATE records SET key='~' || record_id")   # keys are recomputed below; avoid transient clashes
    ids = {root: r["record_id"] for root, r in existing.items() if root in roots}
    # record ids are never reused: a retired id stays retired so exported CSVs and cached embeddings cannot
    # silently point at a different paper after a re-run (SQLite would otherwise hand out max(rowid)+1 again)
    seq = max(int(st.get_meta("record_id_seq") or 0), max((r["record_id"] for r in existing.values()), default=0))
    n_new = 0
    for row in out:
        if row[0] in ids:
            c.execute(f"UPDATE records SET {sets} WHERE record_id=?", (*row[1:], ids[row[0]]))
        else:
            seq += 1
            c.execute(f"INSERT INTO records(record_id,{cols}) VALUES({','.join('?' * (len(row) + 1))})", (seq, *row))
            ids[row[0]] = seq
            n_new += 1
    st.set_meta("record_id_seq", str(seq))
    existing_ids = {r["record_id"] for r in existing.values()}
    for root, abstract, source, at in versioned:   # a new record's first version is the imported abstract
        rid = ids[root]
        abstracts.add_version(c, rid, abstract, "export" if rid in existing_ids and source == "export" else
                              source if rid in existing_ids else "imported", at=None if rid in existing_ids else at)
    n_merged = n_dropped = 0
    set_aside = []   # (merged-away record, survivor, rows set aside, decisions that differed)
    at = utcnow()
    for root, old_id in sorted(vanished.items()):
        succ = successor[root]
        if succ is not None:
            moved = _remap_downstream(c, old_id, ids[succ], at)
            if moved["rows"]:
                set_aside.append((old_id, ids[succ], moved["rows"], moved["differ"]))
            n_merged += 1
        else:
            n_dropped += 1   # its abstract versions stay: labels and calls that name them may still be shown (D8)
        c.execute("DELETE FROM records WHERE record_id=?", (old_id,))
    for root in sorted({successor[r] for r in vanished if successor[r] is not None}):   # latest = current abstract
        cur = c.execute("SELECT abstract, abstract_source FROM records WHERE record_id=?", (ids[root],)).fetchone()
        abstracts.add_version(c, ids[root], cur["abstract"], "export" if cur["abstract_source"] == "export"
                              else cur["abstract_source"])
    c.executemany("UPDATE dedup_clusters SET is_master=1 WHERE raw_id=?", [(a,) for a, _ in masters])
    return {"unique_records": len(out), "kept": len(out) - n_new, "new": n_new, "merged": n_merged, "dropped": n_dropped,
            "set_aside": set_aside, "set_aside_at": at}


def set_aside_lines(set_aside: list) -> list[str]:
    """One sentence per merged record whose screening rows were set aside, as History, the page and the CLI say it."""
    out = []
    for old_id, new_id, n, differ in set_aside:
        s = (f"Records {old_id} and {new_id} were merged as duplicates; both had been screened, so record {new_id}'s "
             f"screening is kept and record {old_id}'s {n} screening row{'s' if n != 1 else ''} "
             f"{'are' if n != 1 else 'is'} set aside (kept on record, not counted).")
        # the decisions themselves are not named: one of them may be a model decision the development sample hides
        for prot in sorted({d[0] for d in differ}):
            s += f" In {prot} the two had different screening decisions; record {new_id}'s is kept."
        out.append(s)
    return out


def _record_set_aside(project: Project, set_aside: list, at: str) -> list[str]:
    """Name the set-aside screening rows in History (a final entry: the rows stay in merge_set_aside)."""
    lines = set_aside_lines(set_aside)
    if lines:
        from ..core import journal
        items = [{"t": "rows", "table": "merge_set_aside", "where": "at=?", "params": [at], "before": [],
                  "after": [dict(r) for r in project.store.query("SELECT * FROM merge_set_aside WHERE at=?", (at,))]}]
        journal._store_change(project, "dedup_set_aside", " ".join(lines), items, journal.current_author(), None)
    return lines


# ----------------------------------------------------------------------------- manual decisions
def queue_rows(project: Project, pending_only: bool = True) -> list[dict]:
    st = project.store
    sql = ("SELECT p.*, a.title AS title_a, b.title AS title_b, a.platform AS platform_a, b.platform AS platform_b, "
           "a.year AS year_a, b.year AS year_b, a.doi AS doi_a, b.doi AS doi_b, a.authors AS authors_a, b.authors AS authors_b, "
           "a.journal AS journal_a, b.journal AS journal_b, a.volume AS volume_a, b.volume AS volume_b, a.pages AS pages_a, b.pages AS pages_b, "
           "a.abstract AS abstract_a, b.abstract AS abstract_b "
           "FROM dedup_pairs p JOIN raw_records a ON a.raw_id=p.raw_a JOIN raw_records b ON b.raw_id=p.raw_b WHERE ")
    sql += "p.decision='queued'" if pending_only else "p.decision IN ('queued','manual_same','manual_different','manual_uncertain')"
    rows = []
    for r in st.query(sql):
        d = dict(r)
        d["diagnostics"] = json.loads(d["diagnostics"] or "{}")
        rows.append(d)
    rows.sort(key=lambda d: (d["diagnostics"].get("review_priority_rank", 3), -(d["score"] or 0), d["raw_a"]))
    return rows


def _manual_decisions(st) -> list[list]:
    """[raw_a, raw_b, decision], with the kept raw id appended when one was chosen (so a changed keep counts)."""
    return [[r["raw_a"], r["raw_b"], r["decision"]] + ([r["k"]] if r["k"] is not None else []) for r in st.query(
        "SELECT raw_a, raw_b, decision, json_extract(diagnostics,'$.keep') AS k FROM dedup_pairs "
        "WHERE decision LIKE 'manual_%' ORDER BY raw_a, raw_b")]


def decided_since_run(project: Project) -> int:
    """Pairs decided by hand after the last dedup run: they count once the run is repeated."""
    applied = project.store.get_meta("dedup_run_manual")
    if applied is not None:   # compare with the decisions the run applied: times have whole seconds only
        seen = {tuple(x) for x in json.loads(applied)}
        return sum(tuple(x) not in seen for x in _manual_decisions(project.store))
    since = project.store.get_meta("dedup_run_at") or ""   # a run made before WIDER kept that list
    r = project.store.one("SELECT COUNT(*) n FROM dedup_pairs WHERE decision LIKE 'manual_%' AND decided_at > ?", (since,))
    return int(r["n"]) if r else 0


def decide(project: Project, raw_a: int, raw_b: int, decision: str, by: str = "", note: str = "",
           keep: int | None = None) -> None:
    """Record a person's decision on a pair. ``keep`` (duplicate only) names the raw record whose title, authors,
    year, journal and DOI the merged record shows after the next run."""
    m = {"duplicate": "manual_same", "not_duplicate": "manual_different", "unsure": "manual_uncertain",
         "uncertain": "manual_uncertain"}   # "unsure" is the page's word; "uncertain" is kept for older scripts
    if decision not in m:
        raise ValueError("decision must be duplicate, not_duplicate or unsure")
    if raw_a == raw_b:
        raise ValueError(f"raw record {raw_a} cannot be a duplicate of itself; give two different raw record ids")
    for rid in (raw_a, raw_b):
        if project.store.one("SELECT 1 FROM raw_records WHERE raw_id=?", (rid,)) is None:
            raise ValueError(f"raw record {rid} does not exist")
    if keep is not None and m[decision] != "manual_same":
        raise ValueError("a kept record can only be named for a duplicate decision")
    if keep is not None and keep not in (raw_a, raw_b):
        raise ValueError(f"the kept record must be raw record {raw_a} or {raw_b}, not {keep}")
    a, b = min(raw_a, raw_b), max(raw_a, raw_b)
    from ..core.journal import current_author, recording
    by = current_author((by or "").strip() or None)  # the pair row and the history keep the same name
    what = decision.replace("_", " ") + (f", keep raw record {keep}" if keep is not None else "")
    keep_sql = ("json_set(COALESCE(dedup_pairs.diagnostics,'{}'),'$.keep',?)" if keep is not None
                else "CASE WHEN json_extract(dedup_pairs.diagnostics,'$.keep') IS NULL THEN dedup_pairs.diagnostics "
                     "ELSE json_remove(dedup_pairs.diagnostics,'$.keep') END")   # untouched when no record was kept
    with recording(project, "dedup_decision", f"raw records {a} and {b}: {what}", author=by) as rec:
        rec.rows("dedup_pairs", "raw_a=? AND raw_b=?", (a, b))
        project.store.execute(
            "INSERT INTO dedup_pairs(raw_a,raw_b,stage,rule,score,diagnostics,decision,decided_by,decided_at) VALUES(?,?,'manual','',NULL,?,?,?,?) "
            "ON CONFLICT(raw_a,raw_b) DO UPDATE SET decision=excluded.decision, decided_by=excluded.decided_by, "
            f"decided_at=excluded.decided_at, diagnostics={keep_sql}",
            (a, b, json.dumps({"note": note} | ({"keep": keep} if keep is not None else {})), m[decision], by, utcnow(),
             *([keep] if keep is not None else [])))


def import_decisions_csv(project: Project, path: str | Path, by: str = "import") -> dict:
    """Re-apply decisions from a 05_manual_duplicate_review.csv: this tool's own (``raw_a``/``raw_b``, ``title_a``,
    ``platform_a``; matched on the raw id when that record has the row's title, else on title+platform+year) or the
    original pipeline's (``title_raw_a``, ``database_a``; matched on title+platform+year)."""
    st = project.store
    db_map = {"psycinfo": "ovid", "business source premier": "ebsco", "web of science": "wos", "scopus": "scopus", "proquest": "proquest"}

    def _int(v):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

    def find(title, database, year):
        if not norm_text(title or ""):
            return None
        plat = db_map.get((database or "").lower(), (database or "").lower())
        rows = st.query("SELECT raw_id FROM raw_records WHERE title_norm=? AND platform=?", (norm_text(title), plat))
        if len(rows) > 1 and _int(year) is not None:
            rows2 = st.query("SELECT raw_id FROM raw_records WHERE title_norm=? AND platform=? AND year=?", (norm_text(title), plat, _int(year)))
            rows = rows2 or rows
        return rows[0]["raw_id"] if rows else None

    def by_id(raw_id, title):
        rid = _int(raw_id)
        r = st.one("SELECT title_norm FROM raw_records WHERE raw_id=?", (rid,)) if rid is not None else None
        return rid if r is not None and (not (title or "").strip() or r["title_norm"] == norm_text(title)) else None

    n = Counter()
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = set(reader.fieldnames or [])
        own = {"raw_a", "raw_b", "manual_decision"} <= cols
        if not own and not {"title_raw_a", "title_raw_b", "database_a", "database_b", "manual_decision"} <= cols:
            raise ValueError("unknown decisions file: expected the columns raw_a, raw_b and manual_decision (WIDER's "
                             "05_manual_duplicate_review.csv) or title_raw_a, title_raw_b, database_a, database_b and "
                             "manual_decision (the original pipeline's file)")
        for row in reader:
            dec = (row.get("manual_decision") or "").strip().lower()
            if dec not in ("duplicate", "not_duplicate", "uncertain"):
                n["blank"] += 1
                continue
            if own:
                a = by_id(row["raw_a"], row.get("title_a")) or find(row.get("title_a"), row.get("platform_a"), row.get("year_a"))
                b = by_id(row["raw_b"], row.get("title_b")) or find(row.get("title_b"), row.get("platform_b"), row.get("year_b"))
            else:
                a = find(row["title_raw_a"], row["database_a"], row.get("year_a"))
                b = find(row["title_raw_b"], row["database_b"], row.get("year_b"))
            if a is None or b is None or a == b:
                n["unmatched"] += 1
                continue
            decide(project, a, b, dec, by=by, note=row.get("review_notes", ""))
            n[dec] += 1
    return dict(n)


# ----------------------------------------------------------------------------- exports
def export_audit(project, recs, pairs, queue, manual, summary, rule_summary, edges) -> Path:
    out = project.path("exports", "dedup")
    out.mkdir(parents=True, exist_ok=True)
    st = project.store
    titles = {r["raw_id"]: (r["title"], r["platform"], r["doi"], r["year"], r["authors"], r["journal"], r["volume"], r["issue"], r["pages"])
              for r in st.query("SELECT raw_id,title,platform,doi,year,authors,journal,volume,issue,pages FROM raw_records")}

    def w(name, header, rows):
        with open(out / name, "w", newline="", encoding="utf-8") as f:
            cw = csv.writer(f)
            cw.writerow(header)
            cw.writerows(rows)

    flagcols = ["title_similarity", "jaro_winkler", "token_cosine", "first_author_match", "same_year", "journal_match",
                "volume_match", "issue_match", "page_start_match", "doi_match", "doi_conflict", "publication_family_conflict",
                "cluster_level_conflict", "auto_duplicate_rule", "auto_not_duplicate_rule", "match_method", "auto_merge"]
    w("03_candidate_pairs.csv", ["raw_a", "raw_b", "candidate_reason", "blocking_reason", *flagcols],
      [[p["a"], p["b"], p["candidate_reason"], p.get("blocking_reason", "")] + [p.get(k) for k in flagcols] for p in pairs])
    w("04e_automatic_decision_rule_summary.csv", ["decision", "rule", "candidate_pairs"],
      [[d, r, n] for (d, r), n in rule_summary.most_common()])
    w("05_manual_duplicate_review.csv",
      ["raw_a", "raw_b", "candidate_reason", "candidate_reasons_all", "raw_candidate_comparisons_collapsed", "review_priority_rank",
       "title_a", "title_b", "platform_a", "platform_b", "doi_a", "doi_b", "year_a", "year_b", "authors_a", "authors_b",
       "journal_a", "journal_b", "volume_a", "volume_b", "issue_a", "issue_b", "pages_a", "pages_b", "title_similarity",
       "manual_decision"],
      [[p["a"], p["b"], p["candidate_reason"], p["candidate_reasons_all"], p["raw_candidate_comparisons_collapsed"],
        p["review_priority_rank"], titles[p["a"]][0], titles[p["b"]][0], titles[p["a"]][1], titles[p["b"]][1],
        titles[p["a"]][2], titles[p["b"]][2], titles[p["a"]][3], titles[p["b"]][3], titles[p["a"]][4], titles[p["b"]][4],
        titles[p["a"]][5], titles[p["b"]][5], titles[p["a"]][6], titles[p["b"]][6], titles[p["a"]][7], titles[p["b"]][7],
        titles[p["a"]][8], titles[p["b"]][8], round(p["title_similarity"], 4),
        {"manual_same": "duplicate", "manual_different": "not_duplicate", "manual_uncertain": "uncertain"}.get(
            manual.get((p["a"], p["b"]), {}).get("decision"), "")]
       for p in sorted(queue, key=lambda p: (p["review_priority_rank"], -p["title_similarity"]))])
    w("07_record_provenance_crosswalk.csv", ["raw_id", "platform", "cluster_id", "is_master", "record_key"],
      [[r["raw_id"], r["platform"], r["cluster_id"], r["is_master"], r["key"]] for r in st.query(
          "SELECT c.raw_id, r.platform, c.cluster_id, c.is_master, m.key FROM dedup_clusters c JOIN raw_records r ON r.raw_id=c.raw_id "
          "JOIN records m ON m.cluster_id=c.cluster_id ORDER BY c.cluster_id, c.raw_id")])
    w("08_deduplication_summary.csv", ["metric", "value"], list(summary.items()))
    w("09_prisma_counts.csv", ["stage_record", "n"], prisma_rows(project, summary))
    w("12_deduplication_match_edges.csv", ["raw_a", "raw_b", "match_method", "merged_new_component"],
      [[e["a"], e["b"], e["match_method"], e.get("merged_new_component")] for e in edges])
    return out


def prisma_rows(project: Project, summary: dict | None = None) -> list[list]:
    st = project.store
    if summary is None:
        summary = json.loads(st.get_meta("dedup_summary") or "{}")
    labels = {p.key: p.label for p in project.config.platforms}
    rows = [[f"{labels.get(r['platform'], r['platform'])} records retrieved", r["n"]]
            for r in st.query("SELECT platform, SUM(n_records) AS n FROM sources GROUP BY platform ORDER BY platform")]
    excluded = excluded_file_records(project, summary)
    if excluded:   # shown only when a file is kept out (D5, F7): the platform rows minus it give the total below
        rows.append(["Records not used (file excluded)", excluded])
    rows.append(["Total records before deduplication", summary.get("total_records_imported")])
    rows.append(["Duplicate records removed before screening", summary.get("duplicate_records_removed")])
    rows.append(["Records remaining for title and abstract screening" if not is_provisional(summary) else
                 "Provisional records remaining after automated deduplication", summary.get("unique_records")])
    return rows


def is_provisional(summary: dict) -> bool:
    """Unique-record counts are provisional while any queued pair is undecided or decided uncertain."""
    return bool(summary.get("unresolved_manual_component_pairs_retained") or summary.get("uncertain_manual_component_pairs"))
