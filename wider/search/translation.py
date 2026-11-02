"""Translation ledger (change 2c, D13): what a platform's string cannot say, or says more widely, than the query.

The ledger is computed from the same facts the renderer uses (the query, ``platforms.yaml``'s syntax and its 2a
profile), not from the string, so the string and its ledger cannot disagree. Each row is ``{kind, reason, text}``:
``lost`` (the platform cannot say it), ``wider`` (the platform searches more than asked) or ``unverified`` (a field
code not yet checked against the platform's help page, or a code's render note). 2a's field-code notes
(``render.boolean.field_code_notes``) become rows here word for word.
"""
from __future__ import annotations

from .ir import Near, Query, has_truncation, is_phrase, logic_refs, phrase_text
from .render import BOOLEAN_STYLES, platforms
from .render.boolean import _near, _term, field_code_notes

KINDS = ("lost", "wider", "unverified")
NOTHING_LOST = "nothing lost"
TA = ["title", "abstract"]
OPERATOR_WORDS = {"and", "or", "not"}


def _row(kind: str, reason: str, text: str) -> dict:
    return {"kind": kind, "reason": reason, "text": text}


def _used_terms(query: Query):
    """(block, term) for every term of every block the logic uses, in block order."""
    refs = set(logic_refs(query.logic))
    for b in query.blocks:
        if b.key in refs:
            for t in b.terms:
                yield b, t


def _words(term) -> list[str]:
    """The plain string items of a term (a Near's items flattened), as written in the query."""
    if isinstance(term, Near):
        return [s for it in term.near for s in (it if isinstance(it, list) else [it])]
    return [term]


def _field_rows(query: Query, style: str) -> list[dict]:
    """2a's field-code notes as rows: an unverified keyword code, a keyword code wider than the block, a render note."""
    rows = []
    for n in field_code_notes(query, style):
        if n.endswith("keyword field code not yet verified against the platform's help page"):
            rows.append(_row("unverified", "field_code_unverified", n))
        elif n.startswith("keywords searched more widely"):
            rows.append(_row("wider", "keywords_wider", n))
        else:
            rows.append(_row("unverified", "render_note", n))
    return rows


def _gscholar(query: Query) -> list[dict]:
    from .render.reduced import gscholar_notes

    rows = [_row("lost", "block_left_out", f"block {x['block']} left out ({x['reason']})")
            for x in gscholar_notes(query)["left_out"]]
    rows.append(_row("lost", "block_left_out", "reduced string: no truncation, proximity or field codes, and terms "
                                               "trimmed to 256 characters"))
    return rows


def ledger(query: Query, platform: str) -> list[dict]:
    """The ledger of ``query`` rendered for ``platform`` (a catalogue key or a syntax style). Rows in a fixed order,
    each once."""
    style = platforms().get(platform, {}).get("style", platform)
    if style == "gscholar":
        return _gscholar(query)
    if style not in BOOLEAN_STYLES:
        raise ValueError(f"no ledger for platform {platform!r}")
    cat = platforms()[style]
    prof = cat.get("profile") or {}
    rows: list[dict] = []
    nears = [(b, t) for b, t in _used_terms(query) if isinstance(t, Near)]
    plain = [(b, w) for b, t in _used_terms(query) for w in _words(t)]
    # proximity
    for _, n in nears:
        shown = _near(n, "ebsco")
        if prof.get("near_as_and"):
            rows.append(_row("lost", "near_as_and", f"proximity rendered as AND: {shown} is searched as all words "
                                                    "anywhere in title and abstract"))
            continue
        if n.ordered and not cat.get("proximity_ordered"):
            rows.append(_row("lost", "ordered_as_unordered", f"ordered proximity searched in any order: {shown}"))
        if prof.get("proximity_counts") == "within":
            op = cat["proximity"].format(n=n.distance)
            rows.append(_row("lost", "proximity_counting", f"{op} allows {n.distance - 1} words between; the query "
                                                           f"asked for {n.distance}"))
    # fields
    if style == "pubmed":
        for b in query.blocks:
            if b.key in set(logic_refs(query.logic)) and b.fields != TA:
                rows.append(_row("wider", "field_widened", f"block {b.name or b.key} ({', '.join(b.fields) or 'no fields'}) "
                                                           "searched in title and abstract ([tiab])"))
    # phrases and truncation
    phrases = [w for _, w in plain if is_phrase(w)]
    truncated = [w for _, w in plain if has_truncation(w)]
    if style == "pubmed" or prof.get("phrase_truncation") is False:
        for w in phrases:
            if has_truncation(w):
                rows.append(_row("lost", "phrase_truncation", f"truncation inside a phrase is dropped: {w}"))
    if style == "ovid":
        for w in phrases:
            if OPERATOR_WORDS & {x.lower() for x in phrase_text(w).replace("*", "").split()}:
                rows.append(_row("lost", "phrase_operator_word", f"Ovid phrases are unquoted, so the operator word in "
                                                                 f"{w} is read as an operator"))
    if (cap := prof.get("max_truncation")) and truncated:
        rows.append(_row("lost", "truncation_cap", f"* covers at most {_number(cap)} characters: "
                                                   + ", ".join(dict.fromkeys(_term(w, style) for w in truncated))))
    if style == "scopus" and phrases:
        rows.append(_row("wider", "loose_phrase", "quotes also find variants of the phrase: " + ", ".join(dict.fromkeys(phrases))))
    if "lemmatisation" in (prof.get("uncertain_when") or []) and truncated:
        rows.append(_row("lost", "variants_off", "truncation switches lemmatisation off: "
                                                 + ", ".join(dict.fromkeys(w for w in truncated))))
    rows += _field_rows(query, style)
    out: list[dict] = []
    for r in rows:
        if r not in out:
            out.append(r)
    return out


def _number(n: int) -> str:
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}
    return words.get(n, str(n))


def nothing_lost(rows: list[dict]) -> bool:
    """True when no row is ``lost`` or ``wider``; ``unverified`` rows are still listed under "nothing lost"."""
    return not any(r["kind"] in ("lost", "wider") for r in rows)


def ledger_lines(rows: list[dict]) -> list[str]:
    """The ledger as printed under a string: "nothing lost" first when it applies, then "<kind>: <text>"."""
    return ([NOTHING_LOST] if nothing_lost(rows) else []) + [f"{r['kind']}: {r['text']}" for r in rows]


def project_ledgers(project, query: Query, keys: list[str] | None = None) -> dict[str, list[dict]]:
    """The ledger of every chosen platform (custom ones through their syntax family), keyed by platform key; a
    project without a list gets the catalogue keys it renders today."""
    from .catalogue import style_for

    if project.config.platforms:
        styles = {p.key: style_for(p) for p in project.config.platforms}
    else:
        styles = {k: k for k in ("ebsco", "scopus", "wos", "proquest", "ovid")}
    return {k: ledger(query, s) for k, s in styles.items() if keys is None or k in keys}

