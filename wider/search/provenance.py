"""The three ledgers of one round in one view (change 2c, D17): the refinement decisions of the round, the model
calls that shaped the query (synonym proposals, seed-name mining, the second reader) with their input and output
hashes, and the outside requests, with the switch that allowed each.

The window of round N is [its ``created_at``, the next round's ``created_at``), open-ended for the newest round.
Decisions are taken by their ``round_no``. A synonym proposal run is listed in the round it was made from: its
``run_spec.json`` names that round (``settings.from_version``); an older run without it falls back to the window.
A second-reader run is listed in the round it read (``review.json`` ``synonyms_version``), else by the window. Seed
mining and outside requests go by the window. Prompt and response texts are never shown, only their hashes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from ..core.project import Project
from ..core.provenance import utcnow

STAGES = ("construct", "seed_mining", "press_review")
NO_OUTPUT_HASH = "output hash not recorded"
NO_DECISIONS = "no decisions recorded for this round"


def _windows(project: Project) -> dict[int, tuple[str, str | None]]:
    rs = [dict(r) for r in project.store.query(
        "SELECT round_no, created_at FROM query_rounds ORDER BY round_no")]
    out = {}
    for i, r in enumerate(rs):
        nxt = rs[i + 1]["created_at"] if i + 1 < len(rs) else None
        out[r["round_no"]] = (r["created_at"] or "", nxt)
    return out


def _in(at: str | None, win: tuple[str, str | None]) -> bool:
    start, end = win
    return bool(at) and at >= start and (end is None or at < end)


def _read(path: Path) -> Any:
    if not path.is_file():
        return None
    try:   # the YAML loader also reads JSON (proposal runs write calls.json as YAML)
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_dir(project: Project, stage: str, run_ref: str) -> Path:
    if stage == "construct":
        return project.root / "runs" / "construct" / Path(run_ref).name
    return project.root / "runs" / run_ref


def switch_for(purpose: str) -> str:
    """The setting that allowed an outside request of this purpose."""
    from ..core.lookups import PRIOR_PURPOSES, TERM_AND_DOI_PURPOSES, TERM_PURPOSES

    if purpose in PRIOR_PURPOSES:
        return "prior-review lookup switch (prior_reviews.allow_openalex_lookup)"
    if purpose in TERM_AND_DOI_PURPOSES:
        return "search-term switch (online_lookups.terms_allowed) and DOI consent (online_lookups.allowed)"
    if purpose in TERM_PURPOSES:
        return "search-term switch (online_lookups.terms_allowed)"
    return "DOI consent (online_lookups.allowed)"


def _round_of(project: Project, stage: str, d: Path) -> int | None:
    """The round a run belongs to when its own files say so; None falls back to the window."""
    if stage == "construct":
        v = ((_read(d / "run_spec.json") or {}).get("settings") or {}).get("from_version")
    elif stage == "press_review":
        v = (_read(d / "review.json") or {}).get("synonyms_version")
    else:
        return None
    return int(v) if isinstance(v, int) or (isinstance(v, str) and v.isdigit()) else None


def _calls(project: Project, round_no: int, win: tuple[str, str | None]) -> list[dict[str, Any]]:
    rows = project.store.query(
        f"SELECT at, stage, run_ref, provider, model FROM llm_usage WHERE stage IN ({','.join('?' * len(STAGES))}) "
        "AND run_ref IS NOT NULL ORDER BY usage_id", STAGES)
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        runs.setdefault((r["stage"], r["run_ref"]), {"first_at": r["at"], "n": 0, "provider": r["provider"],
                                                      "model": r["model"]})["n"] += 1
    out: list[dict[str, Any]] = []
    for (stage, ref), info in runs.items():
        d = _run_dir(project, stage, ref)
        if stage == "construct" and ((_read(d / "run_spec.json") or {}).get("settings") or {}).get("artifact") != "synonyms":
            continue   # cells, codebook, anchors and the adjacent-paper calls do not shape the query
        owner = _round_of(project, stage, d)
        if (owner != round_no) if owner is not None else not _in(info["first_at"], win):
            continue
        calls = _read(d / "calls.json")
        calls = calls if isinstance(calls, list) else []
        base = {"stage": stage, "run_ref": ref, "provider": info["provider"], "model": info["model"]}
        if not calls:   # no calls.json: one line per logged call, without hashes
            out += [{**base, "input_sha256": None, "output_sha256": None, "note": "calls.json not found"}
                    for _ in range(info["n"])]
            continue
        for c in calls:
            c = c if isinstance(c, dict) else {}
            who = _reader(c.get("reader"))   # a second-reader call names its own backend; llm_usage has one per run
            row = {**base, "provider": c.get("backend") or who[0] or base["provider"],
                   "model": c.get("model") or who[1] or base["model"], "failed": c.get("ok") is False,
                   "input_sha256": c.get("input_sha256"), "output_sha256": c.get("output_sha256")}
            row["note"] = (f"failed: {c.get('error') or 'no answer'}" if row["failed"]
                           else None if c.get("output_sha256") else NO_OUTPUT_HASH)
            out.append(row)
    return out


def _reader(label: Any) -> tuple[str | None, str | None]:
    """``name/model#run`` (a second-reader call, ``review.reader_label``) as (name, model)."""
    if not isinstance(label, str) or "/" not in label:
        return None, None
    name, rest = label.split("/", 1)
    return name or None, rest.rsplit("#", 1)[0] or None


def view(project: Project, round_no: int) -> dict[str, Any]:
    """Round ``round_no``'s decisions, model calls and outside requests. A ``ValueError`` for an unknown round."""
    from ..interview import ledger

    wins = _windows(project)
    if round_no not in wins:
        raise ValueError(f"no round {round_no}")
    win = wins[round_no]
    decisions = [{"decision_id": d["decision_id"], "kind": d["kind"], "mode": d["mode"],
                  "dismissed": d["mode"] == "dismissed", "author": d["author"], "at": d["at"], "text": d["text"]}
                 for d in ledger.rows(project, round_no)]
    web = [{"at": r["at"], "service": r["service"], "purpose": r["purpose"], "items": r["n_items"],
            "failed": r["n_failed"], "switch": switch_for(r["purpose"])}
           for r in project.store.query("SELECT at, service, purpose, n_items, n_failed FROM external_requests "
                                        "ORDER BY id") if _in(r["at"], win)]
    return {"round": round_no, "window": {"from": win[0], "to": win[1]}, "decisions": decisions,
            "decisions_message": None if decisions else NO_DECISIONS,
            "model_calls": _calls(project, round_no, win), "web_calls": web}


def write(project: Project, round_no: int) -> tuple[dict[str, Any], Path]:
    """The view, written as ``exports/provenance_<round>.json`` with names as ``exports.attribution`` says (the
    returned view keeps them as recorded)."""
    from ..core.researchers import attribute_obj

    v = view(project, round_no)
    d = project.path("exports")
    d.mkdir(exist_ok=True)
    path = d / f"provenance_{round_no}.json"
    path.write_text(json.dumps({**attribute_obj(project, v), "written_at": utcnow()}, indent=2, ensure_ascii=False)
                    + "\n", encoding="utf-8")
    return v, path


def lines(v: dict[str, Any]) -> list[str]:
    out = [f"round {v['round']}: from {v['window']['from']} to {v['window']['to'] or 'now'}", "decisions:"]
    out += [f"  #{d['decision_id']} {d['text']}{' [dismissed]' if d['dismissed'] else ''}" for d in v["decisions"]] or [
        f"  {NO_DECISIONS}"]
    out.append(f"model calls: {len(v['model_calls'])}")
    for c in v["model_calls"]:
        outh = c["output_sha256"][:12] if c["output_sha256"] and not c.get("failed") else (c["note"] or NO_OUTPUT_HASH)
        inh = (c["input_sha256"] or "not recorded")[:12]
        who = f"{c['provider']}, {c['model']}" if c.get("model") else c["provider"]
        out.append(f"  {c['stage']} {c['run_ref']} ({who}): input {inh}, output {outh}")
    out.append(f"web calls: {len(v['web_calls'])}")
    out += [f"  {w['at']} {w['service']} {w['purpose']}: {w['items']} item(s), allowed by the {w['switch']}"
            for w in v["web_calls"]]
    return out
