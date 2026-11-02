"""Which models screened what (design Decision 16, result sets Decision 23).

:func:`by_model` summarises a run or a protocol's result set per model: unique records, evaluations (new and reused),
calls, include / maybe / exclude, tokens, cost (or "not priced"), the records whose final decision used the model,
and whether the run or set is mixed. A reused evaluation counts as an evaluation of the set but its tokens and cost
stay with the run that made the call (counted once). Researcher answers are not a model and are left out.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.project import Project
from ..core.usage import tokens_of

NOT_PRICED = "not priced"


def _set_of_run(project: Project, run_id: str) -> dict[str, Any] | None:
    from .result_sets import _row, run_ids

    for r in project.store.query("SELECT * FROM screen_result_sets ORDER BY protocol, result_set"):
        d = _row(r)
        if run_id in run_ids(d):
            return d
    return None


def by_model(project: Project, protocol: str | None = None, run_id: str | None = None,
             result_set: int | None = None) -> dict[str, Any]:
    """``{scope, models: [...], totals, mixed, experimental, origins}`` for run ``run_id`` or for ``protocol``'s
    active set (or set ``result_set``). KeyError for an unknown run or set; ValueError without either."""
    from .engine import EXPERIMENTAL_NOTE, run_calls, run_mixed
    from .export import NO_FINAL, cost_of, decision_rows, off_model, set_calls, snapshots
    from .result_sets import reader, row_by_key

    st = project.store
    if run_id:
        if not st.scalar("SELECT COUNT(*) FROM runs WHERE run_id=?", (run_id,)):
            raise KeyError(f"no run {run_id!r}")
        calls = run_calls(project, run_id)
        row = _set_of_run(project, run_id)
        decs = decision_rows(project, row["key"], set_calls(project, row)) if row else []
        scope: dict[str, Any] = {"run_id": run_id}
        if row:
            scope.update(protocol=row["protocol"], key=row["key"], result_set=row["result_set"])
        mixed = run_mixed(project, run_id)
        by_set = None   # one run: mixed is the run's own rule
    elif protocol:
        info = reader(project, protocol, result_set)
        row = row_by_key(project, info["key"])
        if row is None or not st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (info["key"],)):
            raise KeyError(f"no screening decisions under protocol {protocol!r}"
                           + (f" set {result_set}" if result_set else ""))
        calls = set_calls(project, row)
        decs = decision_rows(project, info["key"], calls)
        scope = {k: info[k] for k in ("protocol", "key", "result_set", "label", "role", "role_note")}
        mixed = bool(row.get("mixed")) or any(d["mixed_models"] for d in decs)
        by_set = row.get("settings") if isinstance(row.get("settings"), dict) else None
    else:
        raise ValueError("give a protocol or a run id")
    from .devgate import LEFT_OUT, hidden_ids

    hid = hidden_ids(project)    # their decisions are left out of include / maybe / exclude (D4)
    left = False
    snaps = snapshots(project)
    final_by_record = {d["record_id"]: d for d in decs}
    models: dict[str, dict[str, Any]] = {}
    records_all: set[int] = set()
    for c in calls:
        if c.get("provider") == "researcher":
            continue
        name = c.get("model") or "?"
        m = models.setdefault(name, {"model": name, "providers": set(), "records": set(), "evaluations": 0,
                                     "reused": 0, "calls": 0, "failed": 0, "include": 0, "maybe": 0, "exclude": 0,
                                     "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0, "unpriced_calls": 0,
                                     "final_records": set(), "experimental": False, "passes": set()})
        if c.get("provider"):
            m["providers"].add(c["provider"])
        if c.get("pass"):
            m["passes"].add(c["pass"])
        if by_set is not None and c.get("ok") and off_model(c, by_set):
            mixed = True
        snap = snaps.get(c.get("backend_hash")) or {}
        m["experimental"] = m["experimental"] or bool(snap.get("experimental"))
        if c["evaluation"] == "new":
            m["calls"] += 1
            t = tokens_of(json.loads(c["usage"]) if c.get("usage") else {})
            m["prompt_tokens"] += t["prompt"]
            m["completion_tokens"] += t["completion"]
            cost = cost_of(c, snap)
            if cost is None:
                m["unpriced_calls"] += 1
            else:
                m["cost"] += cost
        if not c.get("ok"):
            m["failed"] += 1
            continue
        m["evaluations"] += 1
        if c["evaluation"] == "reused":
            m["reused"] += 1
        m["records"].add(c["record_id"])
        records_all.add(c["record_id"])
        if c["record_id"] in hid:
            left = True
        elif c.get("rule_decision") in ("include", "maybe", "exclude"):
            m[c["rule_decision"]] += 1
        fd = final_by_record.get(c["record_id"])
        if fd and c["call_id"] in fd["vote_call_ids"] and (fd.get("decided_by") or "") not in NO_FINAL:
            m["final_records"].add(c["record_id"])
    rows = []
    for m in sorted(models.values(), key=lambda x: (-x["evaluations"], x["model"])):
        priced = m["unpriced_calls"] == 0 or m["calls"] == 0
        rows.append({"model": m["model"], "providers": sorted(m["providers"]), "passes": sorted(m["passes"]),
                     "records": len(m["records"]),
                     "evaluations": m["evaluations"], "reused": m["reused"], "calls": m["calls"],
                     "failed": m["failed"], "include": m["include"], "maybe": m["maybe"], "exclude": m["exclude"],
                     "prompt_tokens": m["prompt_tokens"], "completion_tokens": m["completion_tokens"],
                     "cost_usd": round(m["cost"], 6) if priced else None,
                     "cost": f"${m['cost']:.4f}" if priced else NOT_PRICED,
                     "final_records": len(m["final_records"]), "experimental": m["experimental"],
                     "note": EXPERIMENTAL_NOTE if m["experimental"] else None})
    all_priced = all(r["cost_usd"] is not None for r in rows)
    total_cost = sum(r["cost_usd"] or 0 for r in rows)
    totals = {"records": len(records_all), "evaluations": sum(r["evaluations"] for r in rows),
              "reused": sum(r["reused"] for r in rows), "calls": sum(r["calls"] for r in rows),
              "failed": sum(r["failed"] for r in rows),
              "include": sum(r["include"] for r in rows), "maybe": sum(r["maybe"] for r in rows),
              "exclude": sum(r["exclude"] for r in rows),
              "prompt_tokens": sum(r["prompt_tokens"] for r in rows),
              "completion_tokens": sum(r["completion_tokens"] for r in rows),
              "cost_usd": round(total_cost, 6) if all_priced else None,
              "cost": f"${total_cost:.4f}" if all_priced else NOT_PRICED}
    origins: dict[str, int] = {}
    for d in decs:
        k = (d.get("origin") or {}).get("kind") or "screened"
        origins[k] = origins.get(k, 0) + 1
    experimental = any(r["experimental"] for r in rows)
    return {"scope": scope, "models": rows, "totals": totals, "mixed": mixed or (by_set is None and len(rows) > 1),
            "experimental": experimental, "note": EXPERIMENTAL_NOTE if experimental else None, "origins": origins,
            "hidden": LEFT_OUT if left else None,
            "headline": f"{totals['records']} unique records, {totals['calls']} model calls"}
