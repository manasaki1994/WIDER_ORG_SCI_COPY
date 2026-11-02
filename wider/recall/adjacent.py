"""Report section for the adjacent-literature loop: exclusions per adjacent literature, counted from the stored screening
decisions (the ``adjacent_literature`` label every screened record carries once literatures are approved)."""
from __future__ import annotations

import json
from collections import Counter

from ..construct.adjacent import load_list
from ..core.project import Project

NONE_VALUES = (None, "", "none", "not_applicable")


def exclusions_by_literature(project: Project) -> dict[str, dict[str, dict[str, int]]]:
    """``{protocol: {literature_key: {decision: n}}}`` from ``screen_decisions.labels`` of each protocol's active result
    set (other sets are not counted); records without the label are skipped."""
    from ..screen.result_sets import active_key, split_key

    out: dict[str, dict[str, Counter]] = {}
    active: dict[str, str] = {}
    for r in project.store.query("SELECT protocol, decision, labels FROM screen_decisions WHERE labels IS NOT NULL"):
        base = split_key(r["protocol"])[0]
        if base not in active:
            active[base] = active_key(project, base)
        if r["protocol"] != active[base]:
            continue
        try:
            labels = json.loads(r["labels"] or "{}")
        except (TypeError, ValueError):
            continue
        key = labels.get("adjacent_literature")
        if key in NONE_VALUES:
            continue
        out.setdefault(base, {}).setdefault(str(key), Counter())[r["decision"]] += 1
    return {p: {k: dict(v) for k, v in sorted(d.items())} for p, d in out.items()}


def adjacent_section(project: Project) -> dict:
    """Literatures (with status) and the per-protocol decision counts; ``prisma_rows`` lists exclusions per literature
    for the last protocol, ready to append to the PRISMA table."""
    row, lits = load_list(project)
    by_protocol = exclusions_by_literature(project)
    last = list(by_protocol)[-1] if by_protocol else None
    prisma_rows = [[f"excluded as adjacent literature: {k}", v.get("exclude", 0)] for k, v in by_protocol.get(last, {}).items()] if last else []
    return {"version": row["version"] if row else None,
            "literatures": [{"key": e["key"], "name": e["name"], "origin": e.get("origin"), "status": e.get("status")} for e in lits],
            "by_protocol": by_protocol, "last_protocol": last, "prisma_rows": prisma_rows}


def adjacent_markdown(sec: dict | None) -> list[str]:
    if not sec or not sec.get("literatures"):
        return []
    names = {e["key"]: e["name"] for e in sec["literatures"]}
    L = ["## Adjacent literatures", "", "| key | name | origin | status |", "|---|---|---|---|"]
    L += [f"| {e['key']} | {e['name']} | {e.get('origin') or ''} | {e.get('status') or ''} |" for e in sec["literatures"]] + [""]
    for proto, d in sec.get("by_protocol", {}).items():
        L += [f"Decisions by adjacent literature, protocol `{proto}`:", "", "| literature | include | maybe | exclude |", "|---|---|---|---|"]
        L += [f"| {names.get(k, k)} | {v.get('include', 0)} | {v.get('maybe', 0)} | {v.get('exclude', 0)} |" for k, v in d.items()] + [""]
    return L
