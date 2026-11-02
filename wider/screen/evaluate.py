"""Development evaluation: compare a run's rule decisions with the human labels of a purposive sample.
Refuses the probability (validation) sample: that one is only for the recall estimate, never for tuning."""
from __future__ import annotations

import json
from collections import Counter

from ..core.project import Project
from ..recall.estimate import disagreement_line, open_disagreements
from ..sample.labels import changed_after_line, final_labels, sample_by_name
from .engine import run_calls

NOT_LOCKED = "this sample is not locked: its labels can still change, and so can this evaluation"


def evaluate(project: Project, run_id: str, sample: str = "development", primary: str | None = None) -> dict:
    s = sample_by_name(project, sample)
    if s["kind"] == "probability":
        raise ValueError(f"{sample!r} is the probability sample; evaluating on it would tune the prompt on the recall set")
    from . import devgate

    calls = calls_by_record(project, run_id)
    devgate.check(project, set(calls) | devgate._records(project, [sample]))   # ui-redesign D4
    final = final_labels(project, sample, primary)
    labs = {r["record_id"]: r for r in final if r["decision"]}
    rows = score(calls, labs)
    lines = []   # said once, in normal text (D9: inform, not refuse)
    n_open, used = open_disagreements(final)
    if n_open:
        lines.append(disagreement_line(n_open, used, "evaluation"))
    if not s["locked"]:
        lines.append(NOT_LOCKED)
    run = project.store.one("SELECT started_at, finished_at FROM runs WHERE run_id=?", (run_id,))
    changed = changed_after_line(project, sample, run and (run["finished_at"] or run["started_at"]), f"run {run_id}",
                                 tail=bool(s["locked"]))   # beside NOT_LOCKED the tail would say it twice (D6)
    if changed:
        lines.append(changed)
    cats = Counter(r["category"] for r in rows)
    by_pool = {}
    for r in rows:
        pool = (r["pool"] or "").split(":")[0]
        d = by_pool.setdefault(pool, Counter())
        d["n"] += 1
        d[r["category"]] += 1
    reasons = [r for r in rows if r["reason_agree"] is not None]
    return {"run_id": run_id, "sample": sample, "n": len(rows),
            "must_not_miss": sum(1 for r in rows if r["human"] in ("include", "maybe")),
            "misses": cats["MISS"], "false_includes": cats["FALSE INCLUDE"], "false_maybes": cats["FALSE MAYBE"], "agree": cats["agree"],
            "reason_agreement": f"{sum(1 for r in reasons if r['reason_agree'])}/{len(reasons)}",
            "by_pool": {k: dict(v) for k, v in by_pool.items()},
            "by_adjacent": by_adjacent(rows), "adjacent_cases": adjacent_cases(rows),
            "open_disagreements": n_open, "labels_used": used, "lines": lines,
            "disagreements": sorted([r for r in rows if r["category"] != "agree"], key=lambda r: ({"MISS": 0, "FALSE INCLUDE": 1, "FALSE MAYBE": 2}[r["category"]], r["record_id"]))}


def calls_by_record(project: Project, run_id: str) -> dict[int, dict]:
    """The ok evaluations of a run per record, its own and the reused ones (``engine.run_calls``); a record with several
    own ok calls keeps the latest, as before."""
    every = [c for c in run_calls(project, run_id) if c["ok"]]
    calls = {c["record_id"]: c for c in every if c["evaluation"] == "reused"}
    calls.update({c["record_id"]: c for c in sorted((c for c in every if c["evaluation"] == "new"), key=lambda c: c["call_id"])})
    return calls


def score(calls: dict[int, dict], labels: dict[int, dict]) -> list[dict]:
    """One row per labelled record that has a call: MISS (human include/maybe, model exclude), FALSE INCLUDE, FALSE MAYBE
    or agree. Shared by ``evaluate`` and the configuration comparison (design D2)."""
    rows = []
    for rid, h in labels.items():
        c = calls.get(rid)
        if c is None:
            continue
        p = json.loads(c["parsed"] or "{}")
        human_pos = h["decision"] in ("include", "maybe")
        model = c["rule_decision"]
        cat = "agree"
        if human_pos and model == "exclude":
            cat = "MISS"
        elif h["decision"] == "exclude" and model == "include":
            cat = "FALSE INCLUDE"
        elif h["decision"] == "exclude" and model == "maybe":
            cat = "FALSE MAYBE"
        reason_agree = None
        if h["decision"] == "exclude" and model == "exclude" and h.get("reason"):
            reason_agree = (p.get("exclusion_reason") or "") != "" and h["reason"].strip().lower().startswith(p.get("exclusion_reason", "").lower())
        pool = h["pool"] or ""
        rows.append({"record_id": rid, "title": h["title"], "pool": pool, "human": h["decision"], "human_reason": h.get("reason"),
                     "model_raw": c["raw_decision"], "model": model, "category": cat, "reason_agree": reason_agree,
                     "missing_for_maybe": p.get("missing_for_maybe"), "summary": p.get("summary"),
                     "drawn_literature": pool.split(":", 1)[1] if pool.startswith("adjacent:") else None,
                     "model_literature": model_literature(p), "quotes": quotes_of(p)})
    return rows


def model_literature(parsed: dict) -> str | None:
    """The adjacent literature the model named in its ``adjacent_literature`` label, or None for none/not_applicable."""
    v = parsed.get("adjacent_literature")
    return v if isinstance(v, str) and v.strip() and v not in ("none", "not_applicable") else None


def quotes_of(parsed: dict) -> dict[str, str]:
    """Every ``*_quote`` field the model returned, so a case can be read against the abstract."""
    return {k[:-6]: v for k, v in parsed.items() if k.endswith("_quote") and isinstance(v, str) and v.strip()}


def by_adjacent(rows: list[dict]) -> dict:
    """Misses and false includes per adjacent literature: a record counts for the literature it was drawn for and for the
    one the model labelled it with (both, when they differ)."""
    out: dict[str, Counter] = {}
    for r in rows:
        for key, src in ((r.get("drawn_literature"), "n_drawn"), (r.get("model_literature"), "n_model")):
            if not key:
                continue
            d = out.setdefault(key, Counter())
            d[src] += 1
            if src == "n_drawn" or r.get("drawn_literature") != key:
                d["n"] += 1
                d[r["category"]] += 1
    return {k: dict(v) for k, v in sorted(out.items())}


def adjacent_cases(rows: list[dict]) -> list[dict]:
    """Rows tied to an adjacent literature by the sample design or by the model's label, misses and false includes first."""
    order = {"MISS": 0, "FALSE INCLUDE": 1, "FALSE MAYBE": 2, "agree": 3}
    cases = [r for r in rows if r.get("drawn_literature") or r.get("model_literature")]
    return sorted(cases, key=lambda r: (order.get(r["category"], 9), r.get("drawn_literature") or r.get("model_literature") or "", r["record_id"]))


def write_report(project: Project, ev: dict) -> str:
    run = project.store.one("SELECT dir FROM runs WHERE run_id=?", (ev["run_id"],))
    lines = [f"# Development evaluation: {ev['run_id']}", "",
             f"{ev['n']} labelled records; must not miss {ev['must_not_miss']}; misses {ev['misses']}; "
             f"false includes {ev['false_includes']}; false maybes {ev['false_maybes']}; agree {ev['agree']}; "
             f"exclusion reason agreement {ev['reason_agreement']}", "", "## By pool", ""]
    for k, v in ev["by_pool"].items():
        lines.append(f"- {k}: " + ", ".join(f"{a} {b}" for a, b in v.items()))
    if ev.get("by_adjacent"):
        lines += ["", "## By adjacent literature", "", "| literature | n | drawn | model-labelled | MISS | FALSE INCLUDE | FALSE MAYBE | agree |",
                  "|---|---|---|---|---|---|---|---|"]
        for k, v in ev["by_adjacent"].items():
            lines.append(f"| {k} | {v.get('n', 0)} | {v.get('n_drawn', 0)} | {v.get('n_model', 0)} | {v.get('MISS', 0)} | "
                         f"{v.get('FALSE INCLUDE', 0)} | {v.get('FALSE MAYBE', 0)} | {v.get('agree', 0)} |")
    lines += ["", f"## Disagreements ({len(ev['disagreements'])})", ""]
    for r in ev["disagreements"]:
        lines += [f"### {r['category']}: {r['title']}", f"- human: {r['human']} {r['human_reason'] or ''}",
                  f"- model: {r['model']} (raw {r['model_raw']})", f"- summary: {r['summary']}"]
        if r["missing_for_maybe"]:
            lines.append(f"- missing for maybe: {r['missing_for_maybe']}")
        if r.get("drawn_literature") or r.get("model_literature"):
            lines.append(f"- adjacent literature: drawn for {r.get('drawn_literature') or '-'}, model said {r.get('model_literature') or '-'}")
        lines.append("")
    text = "\n".join(lines)
    if run:
        (project.root / run["dir"] / "eval_report.md").write_text(text, encoding="utf-8")
    return text
