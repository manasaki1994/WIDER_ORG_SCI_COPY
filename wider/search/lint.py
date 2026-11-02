"""Query linter: catches the mistakes that cost recall or flood a platform."""
from __future__ import annotations

import re
from collections import Counter

from ..core.text import UK_US_PAIRS
from .ir import Near, Query, has_truncation, is_phrase, phrase_text, term_words

SHORT_STEM = 4
STRICT_RANGE = (1, 99)   # the strictest proximity range of all profiles (Ovid's ADJn), used without a platform
OPERATOR_WORDS = {"and", "or", "not"}


def limits_for(platform: str | None) -> dict:
    """The linter's limits for a platform key or syntax style (change 2c, D0b): the profile's ``min_stem`` and
    ``proximity_range``; without a platform the strictest (stem 4, range 1-99). A profile without a minimum stem or an
    upper distance gives None there; PubMed (proximity rendered as AND) gets no range, its ledger lists it instead."""
    if platform is None:
        return {"label": None, "style": None, "min_stem": SHORT_STEM, "range": STRICT_RANGE}
    from .render import platforms

    cat = platforms()
    if platform not in cat:
        raise ValueError(f"unknown platform {platform!r}; known: {', '.join(cat)}")
    style = cat[platform].get("style", platform)
    prof = cat.get(style, {}).get("profile") or {}
    rng = None if prof.get("near_as_and") or not prof.get("proximity_range") else tuple(prof["proximity_range"])
    return {"label": cat[platform].get("label", platform).split(" (")[0], "style": style,
            "min_stem": prof.get("min_stem"), "range": rng}


def lint_style(project, platform: str | None) -> str | None:
    """A platform as the user names it (a project key, custom ones through their syntax family, or a catalogue key)
    as the key :func:`limits_for` takes; None stays None. Raises ValueError for an unknown name."""
    if platform is None:
        return None
    from .catalogue import project_platforms
    from .render import platforms

    chosen = {c["key"]: c["style"] for c in project_platforms(project)} if project.config.platforms else {}
    if platform in chosen:
        return chosen[platform]
    if platform in platforms():
        return platform
    raise ValueError(f"unknown platform {platform!r}; known: {', '.join(list(chosen) or platforms())}")


def lint(q: Query, platform: str | None = None) -> list[dict]:
    """Findings on the query: errors (a stem of two letters or fewer, a standalone wildcard among them; an empty block)
    and warnings (among them an unquoted multi-word term, which every renderer quotes as a phrase). ``platform`` applies that platform's limits (:func:`limits_for`)."""
    lim = limits_for(platform)
    out: list[dict] = []
    for b in q.blocks:
        seen = Counter()
        stems = [phrase_text(t).replace("*", "").lower() for t in b.terms
                 if isinstance(t, str) and has_truncation(t) and not is_phrase(t) and " " not in phrase_text(t)]
        for t in b.terms:
            if isinstance(t, Near):
                if t.distance > 10:
                    out.append(_w(b.key, f"proximity distance {t.distance} is loose", str(t.model_dump())))
                if lim["range"] is not None:
                    lo, hi = lim["range"]
                    if t.distance < lo or (hi is not None and t.distance > hi):
                        where = f"{lim['label']}'s" if lim["label"] else "the strictest platform's"
                        span = f"{lo}-{hi}" if hi is not None else f"{lo} or more"
                        out.append(_w(b.key, f"proximity distance {t.distance} is outside {where} range ({span})",
                                      str(t.model_dump())))
                for w in term_words(t):
                    _check_word(b.key, w, out)
                continue
            key = phrase_text(t).lower()
            seen[key] += 1
            if seen[key] == 2:
                out.append(_w(b.key, "duplicate term", t))
            stem = phrase_text(t).replace("*", "")
            last = stem.split()[-1] if stem.split() else ""
            if has_truncation(t) and not is_phrase(t):
                if not last:
                    out.append(_e(b.key, "truncation stem of two letters or fewer floods the search: a standalone "
                                         "wildcard has no stem at all", t))
                elif len(last) <= 2:
                    out.append(_e(b.key, "truncation stem of two letters or fewer floods the search", t))
                elif lim["min_stem"] is not None and len(last) < lim["min_stem"]:
                    if lim["label"]:
                        out.append(_w(b.key, f"short truncation stem: under {lim['label']}'s minimum of "
                                             f"{_number(lim['min_stem'])} letters", t))
                    else:
                        out.append(_w(b.key, "short truncation stem: check what it expands to", t))
                if last.endswith("-"):
                    out.append(_w(b.key, "wildcard after a hyphen: most platforms drop the hyphen or the wildcard", t))
            if isinstance(t, str) and not is_phrase(t) and " " not in phrase_text(t) and stem:
                low = stem.lower()
                cover = next((c for c in stems if c and c != low and low.startswith(c)), None)
                if cover is None and not has_truncation(t):
                    cover = next((c for c in stems if c and low.startswith(c)), None)
                if cover is not None:
                    out.append(_w(b.key, f"term covered by {cover}*: it adds nothing", t))
            if lim["style"] == "ovid" and is_phrase(t) and OPERATOR_WORDS & {x.lower() for x in stem.split()}:
                out.append(_w(b.key, "Ovid phrases are unquoted: the operator word in this phrase is read as an operator", t))
            if not is_phrase(t) and " " in phrase_text(t):
                out.append(_w(b.key, "multi-word term is not quoted: every search string quotes it as one phrase; "
                                     "add the quotes here to make that visible", t))
            if is_phrase(t) and len(stem.split()) > 5:
                out.append(_w(b.key, "phrase longer than five words rarely matches", t))
            for w in stem.split():
                _check_word(b.key, w, out)
        # UK/US spelling coverage
        words = {w.lower() for t in b.terms for w in term_words(t)}
        for uk, us in UK_US_PAIRS:
            if uk == us:
                continue
            for a, c in ((uk, us), (us, uk)):
                hits = [w for w in words if a in w]
                if hits and not any(c in w for w in words):
                    # only warn if the truncated stem does not already cover both spellings
                    covered = any(has_truncation(t) and phrase_text(t).replace("*", "").lower() in a
                                  and phrase_text(t).replace("*", "").lower() in c
                                  for t in b.terms if isinstance(t, str))
                    if not covered:
                        out.append(_w(b.key, f"spelling variant missing: {a} present, {c} absent", ", ".join(sorted(hits)[:3])))
                    break
        if not b.terms:
            out.append(_e(b.key, "block has no terms", ""))
    # logic sanity
    refs = _refs(q.logic)
    for b in q.blocks:
        if b.key not in refs:
            out.append(_w(b.key, "block is not used in the logic", ""))
    return out


def _check_word(block: str, w: str, out: list) -> None:
    if re.search(r"[^\w*$'\-]", w):
        out.append(_w(block, "unusual character in term", w))


def _refs(node) -> set[str]:
    if isinstance(node, str):
        return {node}
    out = set()
    for k, v in node.items():
        for c in (v if isinstance(v, list) else [v]):
            out |= _refs(c)
    return out


def _w(block, msg, term):
    return {"level": "warning", "block": block, "message": msg, "term": str(term)}


def _e(block, msg, term):
    return {"level": "error", "block": block, "message": msg, "term": str(term)}


def _number(n: int) -> str:
    return {3: "three", 4: "four", 5: "five"}.get(n, str(n))
