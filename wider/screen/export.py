"""Exports of a protocol's screening (design Decision 16, result sets Decision 23).

:func:`export_protocol` writes, under ``exports/screening/``, one row per final decision of a result set
(``<key>_decisions.csv``) and one row per evaluation behind it (``<key>_evaluations.csv``): which run, call, backend
and model evaluated each record, whether the evaluation was new or reused, and whether it was used for the final
decision. No abstract, prompt, answer or error text.

:func:`decision_rows` is shared with the share bundle and the models summary, so the three read decisions the same way.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.usage import call_cost, tokens_of

DECISIONS_COLUMNS = ["record_id", "title", "final_decision", "decided_by", "decided_by_models", "run_ids", "mixed_models",
                     "cell", "reason", "protocol", "result_set", "result_set_label", "result_set_role", "source_set", "origin",
                     "origin_set", "experimental"]
EVALUATIONS_COLUMNS = ["record_id", "pass", "run_id", "call_id", "provider", "model", "model_reported", "backend_hash",
                       "cli_version", "prompt_version", "decision", "raw_decision", "at", "prompt_tokens",
                       "cached_tokens", "completion_tokens", "cost_usd", "final_decision", "used_for_final",
                       "evaluation", "source_run_id", "protocol", "result_set", "result_set_label", "result_set_role",
                       "source_set", "experimental"]
NO_FINAL = ("researcher", "split")


def snapshots(project: Project) -> dict[str, dict]:
    return {r["backend_hash"]: json.loads(r["snapshot"]) for r in project.store.query(
        "SELECT backend_hash, snapshot FROM backend_snapshots")}


def set_calls(project: Project, row: dict[str, Any]) -> list[dict]:
    """Every evaluation behind a result set: the calls (own and reused) of its runs."""
    from .engine import run_calls
    from .result_sets import run_ids

    out = []
    for rid in run_ids(row):
        out.extend(run_calls(project, rid))
    return out


def cost_of(call: dict, snap: dict) -> float | None:
    """The cost of one call from the backend snapshot it ran under (None: not priced or no snapshot)."""
    from ..core.models import ProviderConfig

    if not snap:
        return None
    try:
        cfg = ProviderConfig(**{k: v for k, v in snap.items() if k in ProviderConfig.model_fields})
        return call_cost(cfg, json.loads(call["usage"]) if call.get("usage") else {}, snap.get("locality"))
    except (ValueError, TypeError):
        return None


def _by_call(calls: list[dict]) -> dict[int, dict]:
    return {c["call_id"]: c for c in calls}


def settings_by_run(project: Project, first: dict | None = None) -> dict[str, dict | None]:
    """Run id -> the recorded settings of the set holding the run; the runs of ``first`` (a set row) take its
    settings, so carried votes of a second screen are judged by their source set's settings (design D12)."""
    from .result_sets import _row, run_ids

    out: dict[str, dict | None] = {}
    rows = [first] if first else []
    rows += [_row(r) for r in project.store.query("SELECT * FROM screen_result_sets ORDER BY protocol, result_set")]
    for row in rows:
        s = row.get("settings") if isinstance(row.get("settings"), dict) else None
        for rid in run_ids(row):
            out.setdefault(rid, s)
    return out


def off_model(call: dict, settings: dict | None) -> bool | None:
    """True when the evaluation used a model other than the one ``settings`` give its pass (design D12); None when
    no settings were recorded (then the rule is "more than one model")."""
    from .result_sets import pass_settings

    ps = pass_settings(settings, call.get("pass") or "A")
    if not ps or not ps.get("model"):
        return None
    return (call.get("model") or "") != ps["model"]


def decision_rows(project: Project, key: str, calls: list[dict] | None = None) -> list[dict[str, Any]]:
    """The decisions of set ``key`` with ``decided_by``, the models of the votes behind them and ``mixed_models``."""
    from .result_sets import row_by_key

    st = project.store
    row = row_by_key(project, key) or {"runs": [], "key": key}
    calls = set_calls(project, row) if calls is None else calls
    by_id = _by_call(calls)
    by_run = settings_by_run(project, row if row.get("runs") else None)
    # a vote may name a call of a run outside the set (an older decision carried in); read those directly
    snaps = snapshots(project)
    out = []
    for r in st.query("SELECT d.*, m.title FROM screen_decisions d JOIN records m ON m.record_id=d.record_id "
                      "WHERE d.protocol=? ORDER BY d.record_id", (key,)):
        d = dict(r)
        vc = json.loads(d["vote_calls"]) if d.get("vote_calls") else []
        models, run_ids, exp, offs = [], [], False, []
        for cid in vc:
            c = by_id.get(cid)
            if c is None:
                c = st.one("SELECT c.run_id, c.pass, c.provider, c.model, c.backend_hash, r.model run_model FROM screen_calls c "
                           "JOIN runs r ON r.run_id=c.run_id WHERE c.call_id=?", (cid,))
                c = dict(c) if c else None
                if c:
                    c["model"] = c["model"] or c["run_model"]
            if not c or c.get("provider") == "researcher":
                continue
            if c.get("model") and c["model"] not in models:
                models.append(c["model"])
            if c.get("run_id") and c["run_id"] not in run_ids:
                run_ids.append(c["run_id"])
            exp = exp or bool((snaps.get(c.get("backend_hash")) or {}).get("experimental"))
            offs.append(off_model(c, by_run.get(c.get("run_id"))))
        # mixed: a vote's model is not the one its set's settings give its pass; without settings, more than one model
        mixed = any(offs) if offs and None not in offs else len(models) > 1
        origin = json.loads(d["origin"]) if d.get("origin") else None
        d.update(vote_call_ids=vc, models=models, run_ids=run_ids, mixed_models=mixed, experimental=exp, origin=origin)
        out.append(d)
    return out


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name.replace("#", ".s"))


def _yes(b: bool) -> str:
    return "yes" if b else ""


def export_protocol(project: Project, protocol: str, result_set: int | None = None) -> dict[str, Any]:
    """Write the decisions and evaluations CSVs of ``protocol``'s active set (or set ``result_set``). Returns
    ``{decisions, evaluations, key, result_set, n_decisions, n_evaluations}`` with paths relative to the project."""
    from .devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    from .engine import EXPERIMENTAL_NOTE
    from .result_sets import reader, row_by_key

    info = reader(project, protocol, result_set)
    key = info["key"]
    row = row_by_key(project, key)
    if row is None or not project.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (key,)):
        raise ValueError(f"no screening decisions under protocol {protocol!r}"
                         + (f" set {result_set}" if result_set else ""))
    calls = set_calls(project, row)
    decs = decision_rows(project, key, calls)
    final = {d["record_id"]: d for d in decs}
    source_set = (f"{row['source_protocol']}#{row['source_set']}" if row.get("source_protocol") else "")
    set_cols = [protocol, info["result_set"], info["label"] or "", info["role"] or "", source_set]
    out_dir = project.path("exports", "screening")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe(key)
    dpath, epath = out_dir / f"{stem}_decisions.csv", out_dir / f"{stem}_evaluations.csv"
    from ..core.researchers import Attributor
    from .review import _reviewer

    att = Attributor(project)   # reasons name the researcher who decided ("researcher (Ana) decided ..."); a shared file
    with open(dpath, "w", newline="", encoding="utf-8") as f:   # writes them as exports.attribution says
        w = csv.writer(f)
        w.writerow(DECISIONS_COLUMNS)
        for d in decs:
            o = d["origin"] or {}
            w.writerow([d["record_id"], d["title"], d["decision"], d.get("decided_by") or "", ";".join(d["models"]),
                        ";".join(d["run_ids"]), _yes(d["mixed_models"]), d.get("cell") or "",
                        att.text(d.get("reason") or "", own=(_reviewer(d.get("reason")),)), *set_cols,
                        o.get("kind", ""), o.get("set", "") or "", EXPERIMENTAL_NOTE if d["experimental"] else ""])
    snaps = snapshots(project)
    runs = {r["run_id"]: dict(r) for r in project.store.query("SELECT run_id, prompt_version FROM runs")}
    n_eval = 0
    with open(epath, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(EVALUATIONS_COLUMNS)
        for c in sorted(calls, key=lambda c: (c["record_id"], c["pass"], c["call_id"])):
            snap = snaps.get(c.get("backend_hash")) or {}
            t = tokens_of(json.loads(c["usage"]) if c.get("usage") else {})
            cost = cost_of(c, snap)
            fd = final.get(c["record_id"])
            used = bool(fd and c["call_id"] in fd["vote_call_ids"] and c.get("rule_decision") == fd["decision"]
                        and (fd.get("decided_by") or "") not in NO_FINAL and c.get("ok"))
            w.writerow([c["record_id"], c["pass"], c["run_id"], c["call_id"], c.get("provider") or "",
                        c.get("model") or "", c.get("model_reported") or "", c.get("backend_hash") or "",
                        snap.get("version") or "", (runs.get(c.get("source_run_id") or c["run_id"]) or {}).get("prompt_version") or "",
                        c.get("rule_decision") or "", c.get("raw_decision") or "", c.get("at") or "", t["prompt"],
                        "" if t["cached"] is None else t["cached"], t["completion"],
                        "" if cost is None else round(cost, 8), fd["decision"] if fd else "", _yes(used),
                        c["evaluation"], c.get("source_run_id") or "", *set_cols,
                        EXPERIMENTAL_NOTE if snap.get("experimental") else ""])
            n_eval += 1
    rel = lambda p: str(Path(p).relative_to(project.root))  # noqa: E731
    return {"protocol": protocol, "key": key, "result_set": info["result_set"], "decisions": rel(dpath),
            "evaluations": rel(epath), "n_decisions": len(decs), "n_evaluations": n_eval,
            "role_note": info["role_note"]}
