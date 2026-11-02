#!/usr/bin/env python3
"""Narrow-versus-broad test: what share of a review's included studies would a typical narrow Boolean query have
retrieved, and what the narrow query misses, by decision cell, score band, embedding cluster and labelled stream.

The query is read against the records' title, abstract and keywords with WIDER's local Boolean evaluator
(``wider.search.evaluate``), under the field and truncation profile of one platform, so the answer is the evaluator's
reading of the query, not a database run: ``uncertain`` records are those the profile cannot decide offline.

Real review data never enters the repository: the project directory, the query and any list of included studies are
arguments, and everything printed is a count or a query block name. ``--detail`` writes one row per included record
(ids, states, cells) to a path of the caller's choice, which must lie outside this repository.

Examples
  python3 scripts/narrow_vs_broad.py ~/my-review --query-file ~/narrow.txt --protocol original
  python3 scripts/narrow_vs_broad.py ~/my-review --query-file ~/narrow.txt --list "~/.../included.csv" \
      --title-col Title --all-records --detail ~/narrow_detail.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from wider.core.matching import CorpusRecord, locate_benchmark  # noqa: E402
from wider.core.project import Project  # noqa: E402
from wider.core.text import norm_text  # noqa: E402
from wider.search.evaluate import SeedDoc, _logic, _positive_refs, evaluate, load_profile  # noqa: E402
from wider.search.parse_boolean import parse  # noqa: E402

STATES = ("hit", "uncertain", "miss")


def args():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("project", help="WIDER project directory (built outside any synced review folder)")
    q = ap.add_mutually_exclusive_group(required=True)
    q.add_argument("--query", help="the narrow Boolean query, as a string")
    q.add_argument("--query-file", help="file holding the narrow Boolean query")
    ap.add_argument("--platform", default="ebsco", help="syntax and profile the query is read under (default ebsco)")
    ap.add_argument("--protocol", help="screening protocol whose decisions define the included set")
    ap.add_argument("--decisions", default="include", help="decisions counted as included, comma-separated (default include)")
    ap.add_argument("--list", dest="list_csv", help="CSV of included studies, matched to the corpus by DOI then title")
    ap.add_argument("--title-col", default="title")
    ap.add_argument("--doi-col", default="doi")
    ap.add_argument("--sample", help="sample whose human 'include' labels define the included set")
    ap.add_argument("--ranking", help="ranking name for cells, bands and clusters (default: the newest)")
    ap.add_argument("--all-records", action="store_true", help="also read the query against every record of the frame")
    ap.add_argument("--minutes", type=float, default=1.0, help="screening minutes per record per screener (default 1)")
    ap.add_argument("--screeners", type=int, default=2, help="screeners per record (default 2)")
    ap.add_argument("--detail", help="write per-record states (ids, cells; no text) to this CSV, outside the repository")
    return ap.parse_args()


def included_ids(p, a) -> tuple[set[int], list[str]]:
    st = p.store
    ids: set[int] = set()
    notes: list[str] = []
    if a.protocol:
        decs = [d.strip() for d in a.decisions.split(",") if d.strip()]
        rows = st.query(f"SELECT record_id FROM screen_decisions WHERE protocol=? AND decision IN ({','.join('?' * len(decs))})",
                        (a.protocol, *decs))
        got = {int(r["record_id"]) for r in rows}
        notes.append(f"protocol {a.protocol!r}, decisions {decs}: {len(got)} records")
        ids |= got
    if a.sample:
        s = st.one("SELECT sample_id FROM samples WHERE name=?", (a.sample,))
        if s is None:
            sys.exit(f"no sample named {a.sample!r}")
        rows = st.query("SELECT DISTINCT record_id FROM labels WHERE sample_id=? AND decision='include'", (s["sample_id"],))
        got = {int(r["record_id"]) for r in rows}
        notes.append(f"sample {a.sample!r} human includes: {len(got)} records")
        ids |= got
    if a.list_csv:
        with open(Path(a.list_csv).expanduser(), newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        bench = [(str(i), (r.get(a.title_col) or "").strip(), (r.get(a.doi_col) or "").strip() or None) for i, r in enumerate(rows)]
        corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in st.query("SELECT record_id, title, doi FROM records")]
        matches = locate_benchmark(bench, corpus)
        how = Counter(m.method for m in matches)
        got = {int(m.record_id) for m in matches if m.record_id is not None and not m.needs_confirmation}
        fuzzy = sum(1 for m in matches if m.record_id is not None and m.needs_confirmation)
        notes.append(f"list of {len(rows)} studies: matched {len(got)} (by {dict(how)}); {fuzzy} fuzzy matches left out; "
                     f"{how.get('none', 0)} not in the corpus")
        ids |= got
    if not ids:
        sys.exit("no included records: give --protocol, --sample or --list")
    return ids, notes


def band_of(rank, bands):
    if rank is None:
        return "unranked"
    for b in bands:
        start, end = b.get("start") or 1, b.get("end")
        if rank >= start and (end is None or rank <= end):
            return b["name"]
    return "beyond bands"


def context(p, a):
    """Per-record cell, rank, band, cluster and stream from the newest (or named) ranking."""
    st = p.store
    r = st.one("SELECT ranking_id, name FROM rankings WHERE name=?", (a.ranking,)) if a.ranking else \
        st.one("SELECT ranking_id, name FROM rankings ORDER BY ranking_id DESC LIMIT 1")
    cell, rank = {}, {}
    rname = None
    if r is not None:
        rname = r["name"]
        for x in st.query("SELECT record_id, rank, best_cell FROM ranking_rows WHERE ranking_id=?", (r["ranking_id"],)):
            cell[int(x["record_id"])] = x["best_cell"] or "no cell"
            rank[int(x["record_id"])] = x["rank"]
    bands = [dict(b) if isinstance(b, dict) else b.model_dump() for b in p.config.protocol.sampling.bands]
    clusters, ckey = {}, None
    keys = [m["key"] for m in st.query("SELECT key FROM meta WHERE key LIKE 'clusters:%'")]
    if rname:
        keys = [k for k in keys if k.split(":")[1] == rname] or keys
    if keys:
        ckey = sorted(keys, key=lambda k: int(k.rsplit(":", 1)[1]))[-1]
        clusters = {int(k): v for k, v in json.loads(st.one("SELECT value FROM meta WHERE key=?", (ckey,))["value"])["labels"].items()}
    streams = {x["venue_norm"]: x["label"] for x in st.query("SELECT venue_norm, label FROM stream_labels")}
    journal = {int(x["record_id"]): x["journal"] for x in st.query("SELECT record_id, journal FROM records")}
    thin = {int(x["record_id"]): bool(x["thin"]) for x in st.query("SELECT record_id, thin FROM records")}

    def row(rid):
        rk = rank.get(rid)
        return {"cell": cell.get(rid, "unranked"), "rank": rk, "band": band_of(rk, bands),
                "cluster": clusters.get(rid, "not clustered") if clusters else None,
                "stream": streams.get(norm_text(journal.get(rid)), "no stream label") if streams else None,
                "thin": thin.get(rid, False)}
    return row, rname, ckey, bool(streams)


def concept_groups(query, misses, names):
    """When the query is an AND of concept groups: each group's label, the count of misses per failing-group
    combination, and the count of misses that fail each group alone. None when the query has no top-level AND."""
    logic = query.logic
    if not isinstance(logic, dict) or "and" not in logic:
        return None
    children = logic["and"]
    labels = []
    for ch in children:
        refs = _positive_refs(ch)
        shown = [names.get(k) or k for k in refs[:4]]
        labels.append("/".join(shown) + (f" (+{len(refs) - 4} more)" if len(refs) > 4 else "") if refs else "NOT clause")
    combos, singles = Counter(), Counter()
    for r in misses:
        bad = tuple(i for i, ch in enumerate(children) if _logic(ch, r.per_block) == "miss")
        combos[bad] += 1
        if len(bad) == 1:
            singles[bad[0]] += 1
    return labels, combos, singles


def table(title, counts: dict[str, Counter], order=None):
    keys = order or sorted(counts, key=lambda k: -sum(counts[k].values()))
    w = max([len(title)] + [len(str(k)) for k in keys]) + 2
    print(f"\n{title:<{w}} {'n':>5} {'hit':>5} {'unc.':>5} {'miss':>5} {'missed %':>9}")
    for k in keys:
        c = counts[k]
        n = sum(c.values())
        print(f"{str(k):<{w}} {n:>5} {c['hit']:>5} {c['uncertain']:>5} {c['miss']:>5} {100 * c['miss'] / n:>8.0f}%")


def main():
    a = args()
    p = Project.find(Path(a.project).expanduser())
    text = a.query if a.query is not None else Path(a.query_file).expanduser().read_text(encoding="utf-8")
    try:
        query = parse(text, platform=a.platform)
    except ValueError as e:
        sys.exit(f"cannot read the query as {a.platform} syntax: {e}\n  field tags are written TI(...), AB(...), KW(...); proximity as (a N3 b)")
    profile = load_profile(a.platform)
    ids, notes = included_ids(p, a)
    ctx, rname, ckey, have_streams = context(p, a)
    st = p.store
    recs = {int(x["record_id"]): x for x in st.query("SELECT record_id, title, abstract, keywords FROM records")}
    n_frame = len(recs)

    def read(rid):
        x = recs[rid]
        return evaluate(query, SeedDoc(str(rid), "Included", x["title"] or "", x["abstract"] or "", x["keywords"] or ""), profile)

    results = {rid: read(rid) for rid in sorted(ids) if rid in recs}
    print(f"project {p.root}  platform {a.platform}  query blocks: {[b.key for b in query.blocks]}")
    for n in notes:
        print("  " + n)
    print(f"  included records read: {len(results)} of {len(ids)} (the rest are not in the corpus table)")
    print(f"  ranking {rname!r}; clusters {ckey!r}; stream labels: {'yes' if have_streams else 'none'}")

    by_state = Counter(r.state for r in results.values())
    n = len(results)
    print(f"\nNarrow query against the {n} included studies")
    print(f"  retrieved for certain (hit):   {by_state['hit']:>5}  ({100 * by_state['hit'] / n:.0f}%)")
    print(f"  undecidable offline (unc.):    {by_state['uncertain']:>5}  ({100 * by_state['uncertain'] / n:.0f}%)")
    print(f"  missed:                        {by_state['miss']:>5}  ({100 * by_state['miss'] / n:.0f}%)")
    print(f"  retrieved share, certain .. at most: {100 * by_state['hit'] / n:.0f}% .. {100 * (by_state['hit'] + by_state['uncertain']) / n:.0f}%")

    groups = {"cell": defaultdict(Counter), "band": defaultdict(Counter), "cluster": defaultdict(Counter), "stream": defaultdict(Counter)}
    thin_miss = Counter()
    failed = Counter()
    for rid, r in results.items():
        c = ctx(rid)
        for g in groups:
            if c[g] is not None:
                groups[g][c[g]][r.state] += 1
        if r.state == "miss":
            thin_miss["thin" if c["thin"] else "full abstract"] += 1
            failed[tuple(sorted(r.failed_blocks))] += 1
    table("by decision cell", groups["cell"])
    bands = [b["name"] if isinstance(b, dict) else b.name for b in p.config.protocol.sampling.bands]
    table("by score band", groups["band"], order=[b for b in bands if b in groups["band"]] + [k for k in groups["band"] if k not in bands])
    if groups["cluster"]:
        table("by embedding cluster", groups["cluster"])
    if groups["stream"]:
        table("by labelled stream", groups["stream"])
    names = {b.key: b.name for b in query.blocks}
    misses = [r for r in results.values() if r.state == "miss"]
    if misses:
        groups_failed = concept_groups(query, misses, names)
        if groups_failed is not None:
            labels, combos, singles = groups_failed
            print(f"\nWhy the {len(misses)} misses miss: the top-level AND of the query has {len(labels)} concept groups")
            for i, lab in enumerate(labels):
                print(f"  group {i + 1}: {lab}")
            print("  misses by the groups the record fails (every listed group fails; the others match)")
            for ks, cnt in combos.most_common(12):
                print(f"  {cnt:>5}  {' + '.join(f'group {i + 1}' for i in ks) or 'the NOT clause'}")
            print("  misses that fail one group only: " + ", ".join(f"group {i + 1}: {singles[i]}" for i in range(len(labels))))
        else:
            print("\nWhy the misses miss (query blocks that failed; a record fails all listed blocks)")
            for ks, cnt in failed.most_common(12):
                print(f"  {cnt:>5}  {', '.join(f'{k} ({names.get(k) or k})' for k in ks) or 'logic only'}")
        print(f"  of the misses, {thin_miss['thin']} have a thin abstract and {thin_miss['full abstract']} a full one")

    if a.all_records:
        allr = Counter()
        hit_ids = set()
        for rid in recs:
            s = read(rid).state
            allr[s] += 1
            if s == "hit":
                hit_ids.add(rid)
        inc_hit = len(hit_ids & set(results))
        print(f"\nNarrow query against the whole frame of {n_frame} records")
        print(f"  would retrieve {allr['hit']} for certain, {allr['uncertain']} undecidable, {allr['miss']} not")
        print(f"  of the {allr['hit']} certain retrievals, {inc_hit} are included studies "
              f"({100 * inc_hit / allr['hit']:.1f}% precision)" if allr['hit'] else "")
        print(f"  the broad frame holds {n} included studies in {n_frame} records ({100 * n / n_frame:.1f}%)")

    hours = lambda k: k * a.minutes * a.screeners / 60  # noqa: E731
    print(f"\nScreening burden at {a.minutes:g} min per record per screener, {a.screeners} screeners")
    print(f"  whole broad frame of {n_frame} records: {hours(n_frame):,.0f} hours")
    lab = st.one("SELECT COUNT(*) AS n FROM labels")
    print(f"  human labels in this project: {lab['n'] if lab else 0} ({hours(lab['n'] if lab else 0):,.0f} hours at the same rate)")

    if a.detail:
        out = Path(a.detail).expanduser().resolve()
        if REPO in out.parents or out == REPO:
            sys.exit(f"--detail must lie outside the repository {REPO}")
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["record_id", "state", "failed_blocks", "cell", "rank", "band", "cluster", "stream", "thin"])
            for rid, r in results.items():
                c = ctx(rid)
                w.writerow([rid, r.state, " ".join(r.failed_blocks), c["cell"], c["rank"], c["band"], c["cluster"], c["stream"], int(c["thin"])])
        print(f"\nper-record detail written to {out}")
    p.close()


if __name__ == "__main__":
    main()
