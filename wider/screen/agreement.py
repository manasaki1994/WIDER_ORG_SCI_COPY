"""Agreement between a result set's passes and the route each decision took (design D2): the 3 x 3 table of pass A
against pass B (S1 against S2 in a second screen) over the decisions the set's own passes made, percent agreement,
Cohen's kappa, and every record counted once by how its decision was reached. One function, :func:`route_of`, serves
the dashboard, the report and the reference sets."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..core.project import Project

DECISIONS = ("include", "maybe", "exclude")
ROUTES = ("agreed", "third_read", "split", "researcher", "import", "carried", "not_recorded", "one_pass", "other")
ROUTE_LABELS = {"agreed": "both passes agreed", "third_read": "settled by the third read",
                "split": "no majority (left at maybe)", "researcher": "decided by the researcher", "import": "imported",
                "carried": "carried from another set", "not_recorded": "not recorded (older decision)",
                "one_pass": "one pass", "other": "other"}
ONE_PASS = "This set was screened with one pass; there is no agreement to report."
NO_PAIRS = "No record was read by both passes."
KAPPA_ONE = "kappa not defined: both passes gave only one decision"


def reads(votes: list | None) -> list[str]:
    """The set's own reads in pass order (the researcher's votes left out)."""
    return [str(v) for v in votes or [] if v is not None and not str(v).startswith("researcher:")]


def route_of(decided_by: str | None, origin: dict | None, votes: list | None, final: str, majority: int) -> str:
    """How one decision was reached, checked in design D2's order. ``majority`` is the rule the set was screened with."""
    db = decided_by or ""
    kind = (origin or {}).get("kind")
    if db.startswith(("imported from", "absent from")) or kind == "import":
        return "import"
    if db.startswith("carried from") or kind == "reused":
        return "carried"
    if db == "researcher":
        return "researcher"
    if decided_by is None:
        return "not_recorded"
    rd = reads(votes)
    if len(rd) == 1:
        return "one_pass"
    if len(rd) >= 2 and rd[0] == rd[1] == final:
        return "agreed"
    if len(rd) == 3 and sum(v == final for v in rd) >= majority:
        return "third_read"
    if final == "maybe":
        return "split"
    return "other"


def _origin(raw) -> dict | None:
    if not raw:
        return None
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _votes(raw) -> list:
    if not raw:
        return []
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def set_rules(project: Project, key: str) -> dict[str, Any]:
    """The rules set ``key`` was screened with (its ``settings.rules``); the protocol's current ones for a set without
    settings (a migrated or imported set), marked ``recorded: False``."""
    from .result_sets import row_by_key

    row = row_by_key(project, key) or {}
    settings = row.get("settings") if isinstance(row.get("settings"), dict) else None
    sc = project.config.protocol.screening
    rules = (settings or {}).get("rules") or {}
    return {"passes": int(rules.get("passes", sc.passes)), "majority": int(rules.get("majority", sc.majority)),
            "blind_third_read": bool(rules.get("blind_third_read", sc.blind_third_read)),
            "recorded": bool(rules), "second_screen": (settings or {}).get("kind") == "second_screen"
            or row.get("source") == "second", "settings": settings}


def decision_routes(project: Project, key: str, majority: int | None = None) -> dict[int, str]:
    """``{record_id: route}`` for every decision of set ``key``."""
    if majority is None:
        majority = set_rules(project, key)["majority"]
    return {r["record_id"]: route_of(r["decided_by"], _origin(r["origin"]), _votes(r["votes"]), r["decision"], majority)
            for r in project.store.query("SELECT record_id, decision, votes, decided_by, origin FROM screen_decisions "
                                         "WHERE protocol=?", (key,))}


def kappa(table: dict[str, dict[str, int]]) -> tuple[float | None, str | None]:
    """Cohen's kappa of a 3 x 3 table (two decimals), or None with the reason in words."""
    n = sum(sum(row.values()) for row in table.values())
    if n == 0:
        return None, NO_PAIRS
    po = sum(table[d][d] for d in DECISIONS) / n
    pe = sum(sum(table[d].values()) * sum(table[a][d] for a in DECISIONS) for d in DECISIONS) / (n * n)
    if pe >= 1:
        return None, KAPPA_ONE
    return round((po - pe) / (1 - pe), 2), None


def pass_agreement(project: Project, key: str) -> dict[str, Any]:
    """The agreement section of set ``key``: ``table`` (first read -> second read -> count), ``n``, ``agreed``,
    ``agreement`` ("6 of 10 (60%)"), ``kappa`` / ``kappa_text`` or ``kappa_reason``, ``routes`` (every route of D2 with
    its label and count; ``other`` with the stored values), ``sentence`` for a one-pass set or a set without pairs,
    ``columns`` ("pass A"/"pass B", "read S1"/"read S2") and the models per pass."""
    from .devgate import LEFT_OUT, hidden_ids
    from .protocol import pass_models
    from .result_sets import carried_ids

    hid = hidden_ids(project)    # left out of the table, not of the routes (PRISMA's counts) (D4)
    left = False
    rules = set_rules(project, key)
    second = rules["second_screen"]
    carried = carried_ids(project, key)
    table = {a: dict.fromkeys(DECISIONS, 0) for a in DECISIONS}
    routes: Counter = Counter()
    other: Counter = Counter()
    own_reads: list[int] = []
    for r in project.store.query("SELECT record_id, decision, votes, decided_by, origin FROM screen_decisions "
                                 "WHERE protocol=? ORDER BY record_id", (key,)):
        votes = _votes(r["votes"])
        route = route_of(r["decided_by"], _origin(r["origin"]), votes, r["decision"], rules["majority"])
        routes[route] += 1
        if route == "other":
            other[r["decided_by"] or ""] += 1
        if route in ("import", "carried", "not_recorded") or r["record_id"] in carried:
            continue
        if r["record_id"] in hid:
            left = True
            continue
        rd = reads(votes)
        own_reads.append(len(rd))
        if len(rd) >= 2 and rd[0] in DECISIONS and rd[1] in DECISIONS:
            table[rd[0]][rd[1]] += 1
    n = sum(sum(row.values()) for row in table.values())
    agreed = sum(table[d][d] for d in DECISIONS)
    one_pass = rules["passes"] == 1 if rules["recorded"] else (bool(own_reads) and max(own_reads) == 1)
    out: dict[str, Any] = {
        "key": key, "second_screen": second,
        "columns": ["read S1", "read S2"] if second else ["pass A", "pass B"],
        "rules": {k: rules[k] for k in ("passes", "majority", "blind_third_read", "recorded")},
        "one_pass": one_pass, "table": table, "n": n, "agreed": agreed, "agreement": None, "percent": None,
        "kappa": None, "kappa_text": None, "kappa_reason": None, "sentence": None,
        "routes": [{"route": k, "label": ROUTE_LABELS[k], "count": routes.get(k, 0),
                    **({"values": dict(other)} if k == "other" else {})} for k in ROUTES],
        "models": pass_models(project, key), "hidden": LEFT_OUT if left else None}
    if one_pass:
        out["sentence"] = ONE_PASS
    elif n == 0:
        out["sentence"] = NO_PAIRS
    else:
        pct = round(100 * agreed / n)
        out.update(percent=pct, agreement=f"{agreed} of {n} ({pct}%)")
        k, reason = kappa(table)
        out.update(kappa=k, kappa_text=f"{k:.2f}" if k is not None else None, kappa_reason=reason)
    return out


def route_lines(ag: dict[str, Any]) -> list[str]:
    """The routes as lines ("both passes agreed: 6"); ``other`` names its stored values."""
    out = []
    for r in ag["routes"]:
        if r["route"] == "other" and not r["count"]:
            continue
        line = f"{r['label']}: {r['count']}"
        if r["route"] == "other":
            line += " (" + ", ".join(f"{v or 'empty'} {c}" for v, c in sorted(r["values"].items())) + ")"
        out.append(line)
    return out


def markdown(ag: dict[str, Any]) -> list[str]:
    """The agreement as Markdown lines (report and dashboard text)."""
    lines: list[str] = []
    if ag["sentence"]:
        lines.append(ag["sentence"])
    else:
        a, b = ag["columns"]
        lines += [f"Agreement: {ag['agreement']}; " + (f"Cohen's kappa: {ag['kappa_text']}" if ag["kappa_text"] else ag["kappa_reason"]), "",
                  f"| {a} \\ {b} | " + " | ".join(DECISIONS) + " |", "|---|" + "---:|" * len(DECISIONS)]
        for d in DECISIONS:
            lines.append(f"| {d} | " + " | ".join(str(ag["table"][d][e]) for e in DECISIONS) + " |")
    if ag.get("hidden"):
        lines += ["", f"{ag['hidden']}."]
    lines += ["", "How each decision was reached:"] + [f"- {x}" for x in route_lines(ag)]
    return lines
