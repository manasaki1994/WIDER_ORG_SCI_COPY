"""The query rendered for OpenAlex's title-and-abstract search (change papers-beyond-the-query, D27), with its
translation ledger (2c's D13). Used only by the OpenAlex check (`wider query yield --query`); it is not in the
platform catalogue, so the linter and the database renderings do not change.

Syntax checked on 24 Oct 2026 (design, "Outside facts"): ``title_and_abstract.search`` takes quoted phrases,
``AND``/``OR``/``NOT`` and parentheses, and stems every word; it refuses ``*``, which needs the unstemmed
``title_and_abstract.search.exact``. A query with truncation is therefore sent to the exact field. OpenAlex has no
proximity operator (a Near group becomes AND) and no keywords field in this search; a comma separates filters, so a
comma inside a term is sent as a space.
"""
from __future__ import annotations

from ..ir import Near, Query, as_phrase, has_truncation, logic_refs, phrase_text

FIELD = "title_and_abstract.search"
EXACT = "title_and_abstract.search.exact"
TA = ["title", "abstract"]

# The dry-run profile (change openalex-profile-and-sampled-exports, D1), in the shape of a catalogue ``profile:``; kept
# here, not in platforms.yaml, so the catalogue, the linter and the renderings do not change. Each value is checked by
# hand in manual check M131 (design, "Outside facts").
PROFILE = {
    # every block is sent to title_and_abstract.search (``_block`` ignores the fields; ledger rows field_widened and
    # keywords_not_searched): no keywords are read
    "fields": {"title": TA, "abstract": TA, "keywords": TA},
    "min_stem": 3,                 # help page: three characters before a wildcard, no leading wildcard
    "phrase_truncation": False,    # ``_term`` drops * inside a phrase
    "near_as_and": True,           # ``_term`` sends a Near group as AND; no proximity operator
    # help page: "removes stop words" (kept only inside a short quoted phrase); the list is not published, so these
    # are WIDER's guess and a stop word in a phrase is uncertain, never a miss
    "stop_words": ["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or",
                   "the", "to", "with"],
    # help page: "OpenAlex uses stemming" in the stemmed field; dropped for a query sent to EXACT (``load_profile``)
    "uncertain_when": ["lemmatisation", "stop_words"],
}


def _row(kind: str, reason: str, text: str) -> dict:
    return {"kind": kind, "reason": reason, "text": text}


def _clean(s: str) -> str:
    return " ".join(s.replace(",", " ").split())


def _term(t) -> str:
    if isinstance(t, Near):
        return "(" + " AND ".join("(" + " OR ".join(_term(x) for x in it) + ")" if isinstance(it, list) else _term(it)
                                  for it in t.near) + ")"
    t = t.strip()
    if as_phrase(t):   # a multi-word term typed without quotes is still one phrase
        return '"' + _clean(phrase_text(t).replace("*", " ")) + '"'   # no truncation inside a phrase
    return _clean(t)


def _block(b) -> str:
    return "(" + " OR ".join(_term(t) for t in b.terms) + ")"


def _tree(node, query: Query) -> str:
    if isinstance(node, str):
        return _block(query.block(node))
    (op, val), = node.items()
    if op == "not":
        return f"NOT {_tree(val, query)}"
    if op == "and":   # positives joined by AND, each NOT after them, as in the checked example
        pos = [_tree(k, query) for k in val if not (isinstance(k, dict) and "not" in k)]
        neg = [_tree(k, query) for k in val if isinstance(k, dict) and "not" in k]
        return "(" + " ".join([" AND ".join(pos)] + neg if pos else neg) + ")"
    return "(" + " OR ".join(_tree(k, query) for k in val) + ")"


def _used(query: Query):
    refs = set(logic_refs(query.logic))
    return [b for b in query.blocks if b.key in refs]


def _plain(query: Query) -> list[str]:
    return [s for b in _used(query) for t in b.terms
            for s in ([x for it in t.near for x in (it if isinstance(it, list) else [it])] if isinstance(t, Near) else [t])]


def field_for(query: Query) -> str:
    """The exact field when any used term is truncated outside a phrase, else the stemmed one."""
    return EXACT if any(has_truncation(w) and not as_phrase(w) for w in _plain(query)) else FIELD


def render_openalex(query: Query) -> str:
    """The query string sent as ``filter=<field>:<string>`` (one line, outer parentheses dropped)."""
    s = _tree(query.logic, query)
    return s[1:-1] if s.startswith("(") and s.endswith(")") and isinstance(query.logic, dict) else s


def ledger(query: Query) -> list[dict]:
    """What the OpenAlex string cannot say or says more widely than the query; each row once, in a fixed order."""
    from .boolean import _near

    rows: list[dict] = []
    for b in _used(query):
        for t in b.terms:
            if isinstance(t, Near):
                rows.append(_row("lost", "near_as_and", f"proximity rendered as AND: {_near(t, 'ebsco')} is searched "
                                                        "as all words anywhere in title and abstract"))
    for b in _used(query):
        name = b.name or b.key
        if "keywords" in b.fields:
            rows.append(_row("lost", "keywords_not_searched", f"block {name}: keywords are not searched (OpenAlex "
                                                              "searches title and abstract only)"))
        elif b.fields and b.fields != TA and sorted(b.fields) != sorted(TA):
            rows.append(_row("wider", "field_widened", f"block {name} ({', '.join(b.fields)}) searched in title and "
                                                       "abstract"))
    for w in _plain(query):
        if as_phrase(w) and "*" in phrase_text(w):
            rows.append(_row("lost", "phrase_truncation", f"truncation inside a phrase is dropped: {w}"))
        if "," in w:
            rows.append(_row("lost", "comma", f"a comma separates OpenAlex filters, so it is sent as a space: {w}"))
    if field_for(query) == EXACT:
        rows.append(_row("lost", "no_stemming", f"the query has truncation, so it is sent to {EXACT}: no word is "
                                                "stemmed, and a word without * finds only that exact form"))
    else:
        rows.append(_row("wider", "stemmed", f"{FIELD} stems every word, so other forms of a word are found too"))
    out: list[dict] = []
    for r in rows:
        if r not in out:
            out.append(r)
    return out


def rendering(query: Query) -> dict:
    """``{field, query, ledger}``: what the OpenAlex check sends and what it leaves out."""
    return {"field": field_for(query), "query": render_openalex(query), "ledger": ledger(query)}
