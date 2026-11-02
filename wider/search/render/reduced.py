"""Google Scholar: ~256 characters, no truncation, no proximity, no field scoping.
Keep the first terms of each required block until the budget is spent; a truncated term is kept without its `*`."""
from __future__ import annotations

from ..ir import Near, Query, is_phrase, logic_refs, phrase_text

BUDGET = 256


def _plain(term) -> str | None:
    if isinstance(term, Near):
        return None
    t = phrase_text(term).replace("*", "")
    if is_phrase(term):
        return f'"{t}"'
    return t if " " not in t else f'"{t}"'


def render_gscholar(query: Query, pretty: bool = True, budget: int = BUDGET) -> str:
    by_key = {b.key: b for b in query.blocks}
    order = {b.key: i for i, b in enumerate(query.blocks)}
    paths = [[k for k in g if k in by_key and by_key[k].role == "required"] for g in _groups(query.logic)]
    paths = sorted((g for g in paths if g), key=lambda g: min(order[k] for k in g))
    if not paths:  # no required block on the first path: the first three required blocks that are never excluded
        excluded = set(_excluded(query.logic))
        paths = [[b.key] for b in query.blocks if b.role == "required" and b.key not in excluded][:3]
    groups = []
    for g in paths:
        # every term in the blocks' order, truncated ones without `*` (silence* -> silence), each once; the loop
        # below trims the longest group, so the string is filled up to the budget
        terms = list(dict.fromkeys(p for k in g for p in (_plain(t) for t in by_key[k].terms) if p))
        if terms:
            groups.append("(" + " OR ".join(terms) + ")")
    s = " ".join(groups)
    while len(s) > budget and any(g.count(" OR ") > 0 for g in groups):
        # drop the last term of the longest group
        i = max(range(len(groups)), key=lambda k: len(groups[k]))
        inner = groups[i][1:-1].split(" OR ")
        groups[i] = "(" + " OR ".join(inner[:-1]) + ")"
        s = " ".join(groups)
    while len(s) > budget and len(groups) > 1:  # one term per block is still too long: keep the first blocks
        groups.pop()
        s = " ".join(groups)
    if len(s) > budget:  # a single term longer than the limit: keep its leading words
        quote = '"' if s.startswith('("') else ""
        words = s[1 + len(quote):-1 - len(quote)].split()
        while len(words) > 1 and len(s) > budget:
            words.pop()
            s = f"({quote}{' '.join(words)}{quote})"
        if len(s) > budget:  # one word longer than the limit: cut it inside its brackets and quotes
            s = f"({quote}{words[0][:budget - 2 - 2 * len(quote)]}{quote})"
    return s


def _groups(node) -> list[list[str]]:
    """Groups of blocks on the first conjunctive path: every AND child; of an OR, all its alternatives when they are
    plain blocks (a string without field codes imports one block per term), else the first alternative; NOT parts are
    left out."""
    if isinstance(node, str):
        return [[node]]
    if "not" in node:  # an excluded block never lends its terms to the string
        return []
    if "and" in node:
        return [g for k in node["and"] for g in _groups(k)]
    if "or" in node and node["or"]:
        if all(isinstance(k, str) for k in node["or"]):
            return [list(node["or"])]
        return _groups(node["or"][0])
    return [[k] for k in logic_refs(node)]


def _excluded(node) -> list[str]:
    """Blocks that appear under a NOT anywhere in the logic."""
    if isinstance(node, str):
        return []
    if "not" in node:
        return logic_refs(node["not"])
    return [k for op in ("and", "or") for c in node.get(op, []) for k in _excluded(c)]


def gscholar_notes(query: Query) -> dict:
    """What the reduced string leaves out: blocks under a NOT ("excluded") and blocks whose every term is a proximity
    term ("proximity only"), and, when no string can be built, why. ``text`` is the line the Search strings card and
    ``wider query render gscholar`` show under the paste note ("" when nothing is left out)."""
    excluded = set(_excluded(query.logic))
    left_out = [{"block": b.key, "reason": "excluded" if b.key in excluded else "proximity only"} for b in query.blocks
                if b.key in excluded or (b.terms and all(_plain(t) is None for t in b.terms))]
    empty = None
    if not render_gscholar(query, pretty=False):
        if all(b.key in excluded for b in query.blocks):
            empty = "only excluded blocks"
        elif not any(b.role == "required" and b.key not in excluded for b in query.blocks):
            empty = "no required block"
        else:
            empty = "the required blocks hold only proximity terms"
    parts = []
    if empty:
        parts.append(f"No Google Scholar string can be built from this query: {empty}.")
    if left_out:
        parts.append("Left out: " + ", ".join(f"{x['block']} ({x['reason']})" for x in left_out) + ".")
    return {"left_out": left_out, "empty": empty, "text": " ".join(parts)}
