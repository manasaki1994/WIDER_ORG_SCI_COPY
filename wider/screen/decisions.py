"""Corpus-level screening decisions by protocol: written by the screening engine, or imported from a CSV
(for example an earlier pipeline's results). ``screen_decisions`` holds one decision per record per protocol."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import utcnow

DECISIONS = ("include", "exclude", "maybe")
ALIASES = {"included": "include", "excluded": "exclude", "uncertain": "maybe", "unclear": "maybe"}


def set_decision(project: Project, protocol: str, record_id: int, decision: str, votes=None, cell=None, labels: dict | None = None,
                 reason=None, vote_calls: list | None = None, decided_by: str | None = None, origin: dict | None = None) -> None:
    """Write one final decision into the result set ``protocol`` (a set key: ``P`` or ``P#n``). ``vote_calls`` are the
    call ids behind the votes, ``decided_by`` the passes that agreed (or ``carried from …``, ``researcher``), ``origin``
    where a decision copied from another set came from."""
    from .result_sets import ensure_set

    ensure_set(project, protocol)
    project.store.execute(
        "INSERT INTO screen_decisions(protocol,record_id,decision,votes,cell,labels,reason,decided_at,vote_calls,decided_by,origin) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(protocol,record_id) DO UPDATE SET decision=excluded.decision, votes=excluded.votes, cell=excluded.cell, "
        "labels=excluded.labels, reason=excluded.reason, decided_at=excluded.decided_at, "
        "vote_calls=COALESCE(excluded.vote_calls, screen_decisions.vote_calls), "
        "decided_by=COALESCE(excluded.decided_by, screen_decisions.decided_by), origin=excluded.origin",
        (protocol, record_id, decision, json.dumps(votes) if votes is not None else None, cell,
         json.dumps(labels) if labels else None, reason, utcnow(), json.dumps(vote_calls) if vote_calls is not None else None,
         decided_by, json.dumps(origin) if origin else None))


def labels_from_parsed(parsed: dict, label_groups: list[str]) -> dict:
    """The label fields of one screening response (cell excluded), dropping not_applicable."""
    return {g: parsed[g] for g in label_groups if g != "cell" and parsed.get(g) not in (None, "", "not_applicable")}


def decisions(project: Project, protocol: str) -> dict[int, str]:
    return {r["record_id"]: r["decision"] for r in project.store.query(
        "SELECT record_id, decision FROM screen_decisions WHERE protocol=?", (protocol,))}


def summary(project: Project, protocol: str) -> dict:
    c = Counter(decisions(project, protocol).values())
    return {"protocol": protocol, "n": sum(c.values()), **dict(c)}


def protocols(project: Project) -> list[dict]:
    """Decision counts of each protocol's active result set, the most recently screened protocol last (over all its
    sets; ties: the later written). Protocol names only: a set ``P#n`` is never listed as a protocol."""
    st = project.store
    order = {r["p"]: i for i, r in enumerate(st.query(
        "SELECT s.protocol p FROM screen_result_sets s LEFT JOIN screen_decisions d ON d.protocol=s.key "
        "GROUP BY s.protocol ORDER BY MAX(d.decided_at), MAX(d.rowid), MIN(s.created_at)"))}
    out = []
    for r in st.query("SELECT s.protocol, s.key, s.result_set, s.mixed, COUNT(d.record_id) n, SUM(d.decision='include') include, "
                      "SUM(d.decision='maybe') maybe, SUM(d.decision='exclude') exclude FROM screen_result_sets s "
                      "LEFT JOIN screen_decisions d ON d.protocol=s.key WHERE s.active=1 GROUP BY s.key"):
        d = dict(r)
        if not d["n"]:
            continue
        d["mixed"] = bool(d["mixed"])
        d["sets"] = st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol=?", (d["protocol"],))
        out.append(d)
    out.sort(key=lambda d: order.get(d["protocol"], -1))
    return out


def import_csv(project: Project, protocol: str, path: str | Path, decision_col: str = "final_decision",
               others_are_exclude: bool = True, author: str | None = None) -> dict:
    """Import decisions for records matched by DOI then title. If ``others_are_exclude``, every unique record not in the
    file gets 'exclude' (the file lists only includes and maybes, as the original review's results file did). An import
    never replaces decisions: into a protocol that has some it adds a new result set (source import, the file's name and
    sha256, the time and the author); the active set stays as it is. Every import is a final ``decisions_import``
    History entry. When several rows match one record, the first row counts; each later one is counted as
    ``duplicate``, and one with another decision is listed under ``conflicts`` (its row in the file, header = row 1,
    and the kept row)."""
    from ..core.journal import _store_change, current_author
    from ..core.provenance import sha256_bytes
    from . import result_sets as rs
    from .engine import check_protocol_name

    check_protocol_name(protocol)
    st = project.store
    path = Path(path)
    digest = sha256_bytes(path.read_bytes())
    corpus = [CorpusRecord(str(x["record_id"]), x["title"] or "", x["doi"]) for x in st.query("SELECT record_id,title,doi FROM records")]
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(f)]
    dc = decision_col.lower()
    bench = [(str(i), r.get("title", ""), r.get("doi") or None) for i, r in enumerate(rows)]
    matches = locate_benchmark(bench, corpus, project.config.protocol.identification.jw_confirm)
    n = Counter()
    seen = set()
    with st.tx() as c:
        sets = st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol=?", (protocol,))
        legacy = st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (protocol,))
        if legacy and not sets:   # decisions written before result sets: they are set 1
            rs.ensure_set(project, protocol)
            sets = 1
        row = rs.new_set(project, protocol, "import", scope="corpus", import_file=path.name, import_sha256=digest,
                         author=author)
        key = row["key"]
        first, conflicts = {}, []   # record id -> (file row, decision) of the row that counts
        for row_no, (r, m) in enumerate(zip(rows, matches), start=2):   # row 1 is the header
            dec = ALIASES.get(r.get(dc, "").lower(), r.get(dc, "").lower())
            if m.record_id is None:
                n["unmatched"] += 1
                continue
            if dec not in DECISIONS:
                n["bad_decision"] += 1
                continue
            rid = int(m.record_id)
            # a second row for the same record: the first row counts, the later one is reported, never stored
            if rid in first:
                n["duplicate"] += 1
                if dec != first[rid][1]:
                    conflicts.append({"row": row_no, "record_id": rid, "decision": dec,
                                      "kept_row": first[rid][0], "kept": first[rid][1]})
                continue
            first[rid] = (row_no, dec)
            seen.add(rid)
            extra = {k: v for k, v in r.items() if k not in ("record_id", "title", "doi", "decision", "cell", "exclusion_reason", "reason", "votes") and v}
            c.execute("INSERT INTO screen_decisions(protocol,record_id,decision,votes,cell,labels,reason,decided_at,decided_by) "
                      "VALUES(?,?,?,?,?,?,?,?,?)",
                      (key, rid, dec, None, r.get("cell") or None, json.dumps(extra) if extra else None,
                       r.get("exclusion_reason") or r.get("reason") or None, utcnow(), f"imported from {path.name}"))
            n[dec] += 1
        if others_are_exclude:
            ids = [x["record_id"] for x in st.query("SELECT record_id FROM records")]
            rest = [rid for rid in ids if rid not in seen]
            c.executemany("INSERT OR IGNORE INTO screen_decisions(protocol,record_id,decision,decided_at,decided_by) "
                          "VALUES(?,?,'exclude',?,?)", [(key, rid, utcnow(), f"absent from {path.name}") for rid in rest])
            n["exclude_by_absence"] = len(rest)
        # listed in History in the same transaction, final: a result set is never deleted (D1, D2)
        _store_change(project, "decisions_import",
                      f"decisions imported into {protocol} set {row['result_set']} from {path.name} (sha256 {digest[:12]})",
                      [{"t": "decisions_import", "protocol": protocol, "result_set_key": key, "result_set": row["result_set"],
                        "file": path.name, "sha256": digest}], current_author(author), None)
    out = dict(n)
    if conflicts:
        out["conflicts"] = conflicts
    out.update(result_set=row["result_set"], key=key, active=bool(row["active"]))
    return out
