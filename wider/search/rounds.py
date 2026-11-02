"""Rounds of query refinement (D8): every synonyms version is a round of the same number.

A round keeps, per platform, the development recall of its last dry run (``dry_runs``, a cache written by
:func:`record_dry_run`), the record count (``round_counts``: ``export`` summed from the included files imported
against the round, or ``pasted`` when the researcher typed what the database showed) and whether it was accepted.
Accepting a round re-evaluates it and the last accepted round on today's seeds and names each seed it loses; it
never refuses (D21). The accepted round is the default version of the Search strings page and ``wider query render``.
"""
from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING

from ..core.provenance import utcnow

if TYPE_CHECKING:
    from ..core.project import Project

ACCEPT_HOW = {"accept": "accepted", "freeze": "accepted (by freeze)"}
FREEZE_LINE = "v{v} was accepted implicitly by the freeze"


def records(n: int) -> str:
    """'1 record', '1,840 records'."""
    return f"{n:,} record" + ("" if n == 1 else "s")


# ----------------------------------------------------------------- rows
def _tx(project: Project):
    """One transaction, unless the caller already holds one (a freeze accepting its round)."""
    return contextlib.nullcontext() if project.store.conn.in_transaction else project.store.tx()


def register(project: Project, version: int, created_at: str | None = None) -> None:
    """A saved synonyms version becomes a round (called inside the save's transaction)."""
    project.store.execute("INSERT OR IGNORE INTO query_rounds(round_no, created_at) VALUES(?,?)",
                          (version, created_at or utcnow()))


def _versions(project: Project) -> list[dict]:
    from ..construct import artifacts
    return artifacts.list_versions(project, "synonyms")


def _named(versions: list[dict]) -> str:
    return ", ".join(f"v{r['version']}" for r in versions) if versions else "none yet"


def check_round(project: Project, round_no: int) -> dict:
    """The synonyms version of ``round_no``, else a ValueError naming the versions that exist."""
    vs = _versions(project)
    row = next((r for r in vs if r["version"] == round_no), None)
    if row is None:
        raise ValueError(f"no round {round_no}: the synonyms versions are {_named(vs)}")
    register(project, round_no, row["created_at"])
    return row


def accepted_round(project: Project) -> int | None:
    """The round accepted last (the highest acceptance number), or None."""
    r = project.store.one("SELECT round_no FROM query_rounds WHERE accepted > 0 ORDER BY accepted DESC LIMIT 1")
    return r["round_no"] if r else None


def default_version(project: Project) -> int | None:
    """The version the Search strings page and ``wider query render`` show by default: the accepted round, if any
    (None: the newest version, as before)."""
    n = accepted_round(project)
    return n if n is not None and any(r["version"] == n for r in _versions(project)) else None


def platforms(project: Project) -> list[str]:
    """The platforms a round is evaluated on: every profiled platform, the project's own first (as the dry run)."""
    from .evaluate import default_platforms
    return default_platforms(project)


# ----------------------------------------------------------------- the version an import is recorded under
def status_label(project: Project, version: int) -> str:
    from ..construct import artifacts
    fq = artifacts.frozen_query(project)
    if fq is not None and fq["version"] == version:
        return "the frozen query"
    row = next((r for r in _versions(project) if r["version"] == version), None)
    st = row["status"] if row else "unknown"
    return "frozen, not the frozen query" if st == "frozen" else st


def import_version(project: Project, round_no: int | None = None) -> tuple[int | None, str]:
    """The synonyms version a file imported now is recorded under: ``round_no`` when given (refused when it is not a
    version), else the frozen query, else the newest version of any status, else None. Also the line to print."""
    from ..construct import artifacts
    if round_no is not None:
        v = check_round(project, int(round_no))["version"]
    else:
        fq = artifacts.frozen_query(project)
        newest = artifacts.latest(project, "synonyms")
        v = fq["version"] if fq else (newest["version"] if newest else None)
    if v is None:
        return None, "no query version recorded (no synonyms version yet)"
    return v, f"recorded under v{v} ({status_label(project, v)})"


def selector(project: Project) -> dict:
    """The upload card's round selector: every version, the frozen query preselected, else the newest."""
    vs = _versions(project)
    pre, _ = import_version(project) if vs else (None, "")
    return {"preselect": pre, "options": [{"version": r["version"], "label": f"v{r['version']} ({status_label(project, r['version'])})"}
                                          for r in reversed(vs)]}


# ----------------------------------------------------------------- counts
def _exports(project: Project) -> dict[tuple[int, str], dict]:
    """Per round and platform, the records of the included files imported against the round (newest file's id)."""
    rows = project.store.query(
        "SELECT query_version AS r, platform, SUM(n_records) AS n, MAX(source_id) AS sid FROM sources "
        "WHERE included=1 AND query_version IS NOT NULL GROUP BY query_version, platform")
    return {(x["r"], x["platform"]): {"n": x["n"], "source_id": x["sid"]} for x in rows}


def record_exports(project: Project) -> None:
    """After an import: write each round's exported count (``export``), replacing a pasted one. Derived from the
    sources, so not journalled; the pasted count it replaces stays in the history."""
    now = utcnow()
    c = project.store
    with _tx(project):
        for (r, plat), e in _exports(project).items():
            c.execute("INSERT OR IGNORE INTO query_rounds(round_no, created_at) VALUES(?,?)", (r, now))
            c.execute("INSERT INTO round_counts(round_no, platform, n, count_source, source_id, by, at) "
                      "VALUES(?,?,?,'export',?,NULL,?) ON CONFLICT(round_no, platform) DO UPDATE SET n=excluded.n, "
                      "count_source='export', source_id=excluded.source_id, by=NULL, at=excluded.at",
                      (r, plat, e["n"], e["source_id"], now))


def _known_platforms(project: Project) -> list[str]:
    from .render import platforms as catalogue
    own = [p.key for p in project.config.platforms]
    return own + [k for k in catalogue() if k not in own]


def set_count(project: Project, round_no: int, platform: str, n, by: str | None = None) -> dict:
    """The record count the researcher read off the database's own result page (``pasted``, journalled as
    ``round_count``, undoable). An import of that round and platform later replaces it."""
    from ..core.journal import current_author, recording
    try:
        n = int(str(n).replace(",", "").strip())
    except ValueError:
        raise ValueError(f"the record count must be a whole number, not {n!r}") from None
    if n < 0:
        raise ValueError("the record count must be 0 or more")
    if n >= 2 ** 63:   # past what the store can hold; any real count is far below
        raise ValueError(f"the record count {n:,} is too large to store; check the number on the database's page")
    platform = (platform or "").strip()
    if platform not in _known_platforms(project):
        raise ValueError(f"unknown platform {platform!r}: use one of {', '.join(_known_platforms(project))}")
    check_round(project, round_no)
    e = _exports(project).get((round_no, platform))
    if e is not None:
        raise ValueError(f"round {round_no} has an imported {platform} export of {records(e['n'])}; that count stands")
    by = current_author(by)
    with _tx(project):
        with recording(project, "round_count", f"round v{round_no} {platform}: {records(n)} (pasted)", author=by) as rec:
            rec.rows("round_counts", "round_no=? AND platform=?", (round_no, platform))
            project.store.execute(
                "INSERT INTO round_counts(round_no, platform, n, count_source, source_id, by, at) VALUES(?,?,?,'pasted',NULL,?,?) "
                "ON CONFLICT(round_no, platform) DO UPDATE SET n=excluded.n, count_source='pasted', source_id=NULL, "
                "by=excluded.by, at=excluded.at", (round_no, platform, n, by, utcnow()))
    return {"round": round_no, "platform": platform, "n": n, "count_source": "pasted",
            "line": f"round v{round_no} on {platform}: {records(n)} (pasted)"}


def _counts(project: Project) -> dict[tuple[int, str], dict]:
    """Exports read live from the included files (excluding a file never leaves a stale count), else a pasted row."""
    out = {(r["round_no"], r["platform"]): {"n": r["n"], "source": "pasted", "source_id": None}
           for r in project.store.query("SELECT * FROM round_counts WHERE count_source='pasted'")}
    for k, e in _exports(project).items():
        out[k] = {"n": e["n"], "source": "export", "source_id": e["source_id"]}
    return out


# ----------------------------------------------------------------- dry runs
def record_dry_run(project: Project, out: dict) -> None:
    """Keep the development rows of a dry run per round and platform (a cache: replaced by the next run of that
    round on that platform, never journalled). Validation rows stay in the run folder only."""
    r = out["version"]
    c = project.store
    with _tx(project):
        c.execute("INSERT OR IGNORE INTO query_rounds(round_no, created_at) VALUES(?,?)", (r, out.get("at") or utcnow()))
        for plat, res in out["platforms"].items():
            c.execute("DELETE FROM dry_runs WHERE round_no=? AND platform=?", (r, plat))
            c.executemany("INSERT INTO dry_runs(round_no, platform, benchmark_id, state, blocks_json, reasons_json) "
                          "VALUES(?,?,?,?,?,?)",
                          [(r, plat, s["benchmark_id"], s["state"], json.dumps(s.get("failed_blocks") or []),
                            json.dumps(s.get("reasons") or [])) for s in res["seeds"] if s["set"] == "Development"])
    from . import replay
    try:   # the round's replay record, at its first dry run only (2c D0b); never blocks the dry run
        replay.record(project, r)
    except ValueError:
        pass


def refresh(project: Project, round_no: int | None = None) -> list[int]:
    """Dry-run ``round_no`` (default: every round) on each profiled platform and rewrite the exported counts."""
    from .evaluate import dry_run
    vs = [check_round(project, round_no)["version"]] if round_no is not None else [r["version"] for r in _versions(project)]
    for v in vs:
        record_dry_run(project, dry_run(project, v, write=False))
    record_exports(project)
    return vs


def _dev_ids(project: Project) -> list[str]:
    from . import coverage
    return [b.benchmark_id for b in coverage.development_papers(project, project.read_benchmark())]


# ----------------------------------------------------------------- the list
def _loss_text(lost: list[dict]) -> str:
    """'B003 on scopus' or 'B003, B004 on scopus, wos; B005 on pubmed': seeds lost on the same platforms together."""
    plats: dict[str, list[str]] = {}
    for x in lost:
        plats.setdefault(x["benchmark_id"], []).append(x["platform"])
    by: dict[tuple, list[str]] = {}
    for b, ps in plats.items():
        by.setdefault(tuple(ps), []).append(b)
    return "; ".join(f"{', '.join(ids)} on {', '.join(ps)}" for ps, ids in by.items())


def mark(row: dict) -> str:
    """'accepted', 'accepted, loses B003 on scopus', 'accepted (by freeze)', 'accepted earlier' or ''."""
    if not row.get("accepted"):
        return ""
    lost = json.loads(row["lost_json"]) if row.get("lost_json") else []
    word = ACCEPT_HOW.get(row.get("accepted_how") or "accept", "accepted")
    return word + (f", loses {_loss_text(lost)}" if lost else "")


def rounds(project: Project) -> dict:
    """Every round, oldest first, with its per-platform recall (from its last dry run) and record count."""
    from ..construct import artifacts
    vs = _versions(project)
    for r in vs:
        register(project, r["version"], r["created_at"])
    meta = {r["round_no"]: dict(r) for r in project.store.query("SELECT * FROM query_rounds")}
    counts = _counts(project)
    dev = set(_dev_ids(project))
    acc = accepted_round(project)
    fq = artifacts.frozen_query(project)
    plats = platforms(project)
    stored: dict[tuple[int, str], list] = {}
    for d in project.store.query("SELECT round_no, platform, benchmark_id, state FROM dry_runs"):
        stored.setdefault((d["round_no"], d["platform"]), []).append(dict(d))
    out = []
    from .diagnostics import unrecorded_imports
    unrec = unrecorded_imports(project)
    no_export = (f"no export recorded under this version ({unrec} file{'s' if unrec != 1 else ''} imported before "
                 f"any version existed)") if unrec else "no export yet"
    for v in vs:
        n = v["version"]
        m = meta.get(n, {})
        rows_p = []
        # the platforms this round has a dry run or a count on, in the dry run's order
        shown = [k for k in plats if (n, k) in stored or (n, k) in counts]
        shown += sorted(k for (r, k) in set(stored) | set(counts) if r == n and k not in shown)
        for plat in shown:
            seeds, c = stored.get((n, plat)), counts.get((n, plat))
            item = {"platform": plat, "count": c["n"] if c else None, "count_source": c["source"] if c else None,
                    "source_id": c["source_id"] if c else None, "uncertain": [], "stale": False,
                    "count_text": (records(c["n"]) + (" (pasted)" if c["source"] == "pasted" else "")) if c
                    else no_export}
            if seeds is None:
                item.update(recall=None, recall_text="no dry run yet")
            else:
                hits = sum(s["state"] == "hit" for s in seeds)
                unc = [s["benchmark_id"] for s in seeds if s["state"] == "uncertain"]
                item.update(recall={"hits": hits, "total": len(seeds), "uncertain": len(unc)}, uncertain=unc,
                            recall_text=f"recall {hits}/{len(seeds)}" + (f", {len(unc)} uncertain (not counted)" if unc else ""),
                            stale={s["benchmark_id"] for s in seeds} != dev)
            rows_p.append(item)
        out.append({"round": n, "status": v["status"], "created_at": m.get("created_at") or v["created_at"],
                    "frozen_query": bool(fq and fq["version"] == n), "accepted": n == acc,
                    "accepted_no": m.get("accepted") or 0, "accepted_how": m.get("accepted_how"),
                    "accepted_by": m.get("accepted_by"), "accepted_at": m.get("accepted_at"),
                    "lost": json.loads(m["lost_json"]) if m.get("lost_json") else [],
                    "mark": (mark(m) if n == acc else ("accepted earlier" if m.get("accepted") else "")),
                    "platforms": rows_p})
    return {"rounds": out, "accepted": acc, "platforms": plats, "selector": selector(project), "no_export": no_export,
            "count_platforms": [p.key for p in project.config.platforms] or plats}


def table_lines(data: dict) -> list[str]:
    """The text the CLI prints for the rounds list (one line per round and platform)."""
    lines = []
    for r in data["rounds"]:
        head = f"v{r['round']} ({r['status']}" + (", the frozen query" if r["frozen_query"] else "") + ")"
        if r["mark"]:
            head += f": {r['mark']}"
        lines.append(head)
        for p in r["platforms"]:
            stale = " (seeds changed since this dry run)" if p.get("stale") else ""
            lines.append(f"  {p['platform']}: {p['recall_text']}{stale}; {p['count_text']}")
        if not r["platforms"]:
            lines.append(f"  no dry run yet; {data.get('no_export', 'no export yet')}")
    return lines or ["no rounds yet: save a synonyms version first"]


# ----------------------------------------------------------------- acceptance
def _states(query, docs, prof) -> dict[str, str]:
    from .evaluate import evaluate
    return {d.benchmark_id: evaluate(query, d, prof).state for d in docs}


def accept_plan(project: Project, round_no: int) -> dict:
    """What accepting ``round_no`` would state, read only (the page's confirm step and ``--check``): the loss lines,
    the uncertain lines, the validation lines, and ``already`` when it is the accepted round."""
    from ..construct import artifacts
    from .evaluate import NO_SEEDS, load_profile, seeds_for
    from .ir import Query

    check_round(project, round_no)
    prev = accepted_round(project)
    plan = {"round": round_no, "previous": prev, "already": prev == round_no, "lost": [], "loss_lines": [],
            "loss_text": "", "uncertain_lines": [], "lines": []}
    if plan["already"]:
        plan["lines"] = [f"v{round_no} is already the accepted round"]
        return plan
    if not project.read_benchmark():
        plan["lines"] = [NO_SEEDS]
        return plan
    _, data = artifacts.load(project, "synonyms", round_no)
    query = Query.model_validate(data)
    docs, notice = seeds_for(project, round_no)
    dev = [d for d in docs if d.set == "Development"]
    val = [d for d in docs if d.set != "Development"]
    pq = None
    if prev is not None:
        _, pdata = artifacts.load(project, "synonyms", prev)
        pq = Query.model_validate(pdata) if pdata is not None else None
    val_lines = []
    for k in platforms(project):
        prof = load_profile(k)
        cand = _states(query, dev, prof)
        old = _states(pq, dev, prof) if pq is not None else {}
        lost = [b for b, s in cand.items() if s == "miss" and old.get(b) == "hit"]
        unc = [b for b, s in cand.items() if s == "uncertain" or old.get(b) == "uncertain"]
        if lost:
            plan["lost"] += [{"benchmark_id": b, "platform": k} for b in lost]
            plan["loss_lines"].append(f"v{round_no} loses {', '.join(lost)} on {k}, which v{prev} retrieves.")
        if unc:
            plan["uncertain_lines"].append(f"uncertain: {', '.join(unc)} on {k} (not counted as lost or found)")
        if val:   # the frozen query's own version: its validation results, never compared with another version's
            vs = _states(query, val, prof)
            hits = sum(s == "hit" for s in vs.values())
            val_lines.append(f"validation seeds on {k}: {hits}/{len(vs)} retrieved")
    plan["lines"] = plan["loss_lines"] + plan["uncertain_lines"] + ([notice] if notice else []) + val_lines
    plan["loss_text"] = _loss_text(plan["lost"])   # the page's one-line summary after accepting
    return plan


def accept(project: Project, round_no: int, by: str | None = None) -> dict:
    """Accept any round (D21): the losses against the last accepted round are stated and stored, never refused.
    Journalled as ``round_accept`` (watches the round's row); undo makes the earlier accepted round the accepted one."""
    from ..core.journal import current_author, recording
    plan = accept_plan(project, round_no)
    if plan["already"]:
        return {**plan, "accepted": False}
    by = current_author(by)
    with _tx(project):
        with recording(project, "round_accept", f"round v{round_no} accepted"
                       + (f", loses {_loss_text(plan['lost'])}" if plan["lost"] else ""), author=by) as rec:
            rec.rows("query_rounds", "round_no=?", (round_no,))
            nxt = int(project.store.scalar("SELECT COALESCE(MAX(accepted), 0) FROM query_rounds") or 0) + 1
            project.store.execute("UPDATE query_rounds SET accepted=?, accepted_by=?, accepted_at=?, accepted_how='accept', "
                                  "lost_json=?, note=NULL WHERE round_no=?",
                                  (nxt, by, utcnow(), json.dumps(plan["lost"]), round_no))
    return {**plan, "accepted": True}


def accept_by_freeze(project: Project, version: int, by: str | None = None) -> str | None:
    """A freeze of a round that is not the accepted one accepts it implicitly (D0: a freeze needs no accepted round).
    Called inside the freeze's transaction; returns the stage-result line, or None when it was the accepted round."""
    from ..core.journal import current_author, recording
    if accepted_round(project) == version:
        return None
    check_round(project, version)
    with recording(project, "round_accept", f"round v{version} accepted by the freeze", author=current_author(by)) as rec:
        rec.rows("query_rounds", "round_no=?", (version,))
        nxt = int(project.store.scalar("SELECT COALESCE(MAX(accepted), 0) FROM query_rounds") or 0) + 1
        project.store.execute("UPDATE query_rounds SET accepted=?, accepted_by=?, accepted_at=?, accepted_how='freeze', "
                              "lost_json=NULL, note='accepted implicitly by the freeze' WHERE round_no=?",
                              (nxt, current_author(by), utcnow(), version))
    return FREEZE_LINE.format(v=version)
