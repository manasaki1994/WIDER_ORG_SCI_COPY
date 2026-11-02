"""String check before parsing (change 2c, design D0b): what a pasted or imported string says that the parser has to
read one way or another, each finding with its position (a character offset into the string).

Errors stop the import (nothing is saved): unbalanced parentheses. Warnings say how the string was read: AND and OR
mixed without parentheses (read AND first, the reading shown), curly quotes, a lower-case operator, needless
parentheses, NEAR over a grouped AND, words with no operator between them and an untagged group after a tagged one
(Web of Science) and a field tag nested in a field group (PubMed).
"""
from __future__ import annotations

from .parse_boolean import CURLY, Tok, clean, dialect, tokenize


def _f(level: str, pos: int, message: str) -> dict:
    return {"level": level, "pos": pos, "message": message}


def _pairs(toks: list[Tok]) -> tuple[dict[int, int], list[dict]]:
    """Index of each "(" -> index of its ")", and the unbalanced ones as errors."""
    stack, pairs, out = [], {}, []
    for i, t in enumerate(toks):
        if t.kind == "lp":
            stack.append(i)
        elif t.kind == "rp":
            if stack:
                pairs[stack.pop()] = i
            else:
                out.append(_f("error", t.pos, f"unbalanced parentheses: the ) at position {t.pos} has no ( before it"))
    for i in stack:
        out.append(_f("error", toks[i].pos, f"missing ): the ( at position {toks[i].pos} is never closed"))
    return pairs, out


def _levels(toks: list[Tok], pairs: dict[int, int]):
    """Each level of the string (the top and every parenthesised group): (first, last) token index, exclusive."""
    yield 0, len(toks)
    for a, b in pairs.items():
        yield a + 1, b


def _top(toks: list[Tok], pairs: dict[int, int], a: int, b: int) -> list[int]:
    """Indices of the tokens of one level, groups skipped."""
    out, i = [], a
    while i < b:
        out.append(i)
        i = pairs[i] + 1 if toks[i].kind == "lp" and i in pairs else i + 1
    return out


def check_string(text: str, platform: str = "ebsco") -> list[dict]:
    """Findings ``{level, pos, message}`` on ``text`` read as ``platform``'s syntax, in position order."""
    d = dialect(platform)
    out: list[dict] = []
    for i, c in enumerate(text):
        if c in CURLY:
            out.append(_f("warning", i, f"curly quote {c} read as a straight one ({CURLY[c]})"))
    toks = tokenize(clean(text), d)
    pairs, errs = _pairs(toks)
    out += errs
    for t in toks:
        if t.kind == "word" and d.is_operator(t.text) and t.text != t.text.upper():
            out.append(_f("warning", t.pos, f"lower-case operator {t.text!r} read as {t.text.upper()}"))
    for a, b in ([] if errs else _levels(toks, pairs)):   # levels are only known when the parentheses balance
        idx = _top(toks, pairs, a, b)
        ops = []   # binary operators of this level: AND, OR, NOT after an operand
        for n, i in enumerate(idx):
            w = toks[i].text.upper() if toks[i].kind == "word" else ""
            if w in ("AND", "OR") or (w == "NOT" and n and not _is_op(toks[idx[n - 1]], d)):
                ops.append((n, w))
        kinds = {"OR" if w == "OR" else "AND" for _, w in ops}
        if kinds == {"AND", "OR"}:
            first_or = idx[next(n for n, w in ops if w == "OR")]
            out.append(_f("warning", toks[first_or].pos, "AND and OR mixed without parentheses: read as "
                          + _reading(text, toks, pairs, idx, ops)))
        # needless parentheses: a group whose only content is another group
        if a > 0 and len(idx) == 1 and toks[idx[0]].kind == "lp" and (b + 1 >= len(toks) or toks[b + 1].kind != "tag"):
            out.append(_f("warning", toks[a - 1].pos, f"needless parentheses at position {toks[a - 1].pos}"))
        if d.implied_and:
            out += _implied_and(text, toks, pairs, idx, d, mixed=kinds == {"AND", "OR"})
        for n, i in enumerate(idx):
            if toks[i].kind != "word" or not d.prox_match(toks[i].text) or d.platform != "wos":
                continue
            for j in (idx[n - 1] if n else None, idx[n + 1] if n + 1 < len(idx) else None):
                g = _group_at(toks, pairs, j)
                if g and any(toks[k].text.upper() == "AND" for k in _top(toks, pairs, g[0] + 1, g[1])):
                    out.append(_f("warning", toks[i].pos, f"{toks[i].text} over a grouped AND: Web of Science "
                                                          "does not allow AND inside a proximity group"))
    if d.tags:
        for a, b in pairs.items():
            if b + 1 < len(toks) and toks[b + 1].kind == "tag":
                for k in range(a + 1, b):
                    if toks[k].kind == "tag":
                        out.append(_f("warning", toks[k].pos, f"field tag {toks[k].text} nested in a field group "
                                                              f"({toks[b + 1].text}): read with the group's tag"))
    seen, uniq = set(), []
    for f in sorted(out, key=lambda f: (f["pos"], f["level"] != "error")):
        key = (f["pos"], f["message"])
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    return uniq


def _is_tag(toks, i, d) -> bool:
    nxt = toks[i + 1] if i + 1 < len(toks) else None
    return toks[i].kind == "word" and nxt is not None and nxt.kind == "lp" \
        and all(c in d.prefix for c in toks[i].text.upper().split(","))


def _implied_and(text, toks, pairs, idx, d, mixed: bool) -> list[dict]:
    """Web of Science readings of one level: two operands with no operator between them are ANDed
    (``abusive supervis*``), and an untagged group after a tagged one takes that tag (``AB=(a) AND (b)``). The tag is
    only reported on a level that does not mix AND and OR, where the reading is plain (the mix has its own warning)."""
    out, last, prev = [], None, None   # prev: the top token that ended the previous operand
    for n, i in enumerate(idx):
        t = toks[i]
        if _is_op(t, d):
            prev = None
            continue
        if n and _is_tag(toks, idx[n - 1], d):   # the group a field code opens
            prev = i
            continue
        if prev is not None:
            out.append(_f("warning", t.pos, f"no operator between {_span(text, toks, pairs, [prev])} and "
                          f"{_span(text, toks, pairs, [i]) if not _is_tag(toks, i, d) else t.text}: "
                          "read as AND, as Web of Science does"))
        if _is_tag(toks, i, d):
            last, prev = t.text.upper(), None
            continue
        inner = range(i + 1, pairs[i]) if t.kind == "lp" and i in pairs else range(0)
        if last is not None and not mixed and not any(_is_tag(toks, k, d) for k in inner):
            out.append(_f("warning", t.pos, f"{_span(text, toks, pairs, [i])} has no field tag: read with the "
                          f"tag before it ({last}), as Web of Science does"))
        prev = i
    return out


def _is_op(t: Tok, d) -> bool:
    return t.kind == "word" and d.is_operator(t.text)


def _group_at(toks, pairs, j):
    """(open, close) of the group at token j: a bare group, or the group a field code opens."""
    if j is None:
        return None
    if toks[j].kind == "lp" and j in pairs:
        return j, pairs[j]
    if j + 1 < len(toks) and toks[j + 1].kind == "lp" and j + 1 in pairs:
        return j + 1, pairs[j + 1]
    return None


def _span(text, toks, pairs, idx_list) -> str:
    first, last = idx_list[0], idx_list[-1]
    end_tok = toks[pairs[last]] if toks[last].kind == "lp" and last in pairs else toks[last]
    return " ".join(text[toks[first].pos:end_tok.pos + len(end_tok.text)].split())


def _reading(text, toks, pairs, idx, ops) -> str:
    """The level as the parser reads it: each run of AND (and binary NOT) between ORs in parentheses."""
    cuts = [n for n, w in ops if w == "OR"]
    segs, start = [], 0
    for c in cuts + [len(idx)]:
        seg = idx[start:c]
        if seg:
            s = _span(text, toks, pairs, seg)
            segs.append(f"({s})" if any(start <= n < c for n, _ in ops) else s)
        start = c + 1
    return " OR ".join(segs)


def checked_parse(text: str, platform: str = "ebsco", notes: str = ""):
    """The string check, then the parse: (query, findings). Raises ValueError with every error-level finding (the
    import saves nothing) or the parser's own message (position and token)."""
    from .parse_boolean import parse

    findings = check_string(text, platform)
    errors = [f["message"] for f in findings if f["level"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    try:
        return parse(text, platform, notes=notes), findings
    except ValueError as e:   # the warnings say what the parser met first (a NEAR over a grouped AND, say)
        raise ValueError("; ".join([str(e)] + [f["message"] for f in findings])) from e


def save_refusal(q) -> str | None:
    """What a save of the parsed query would refuse (the synonyms schema: a one-letter term, a lone *, a proximity
    distance outside 1-15), so the preview says it before Save does; None when it would save."""
    from ..construct.artifacts import validate

    err = validate("synonyms", q.model_dump(mode="json", exclude_defaults=True))
    return f"Save would refuse it: synonyms does not validate: {err}" if err else None


def finding_lines(findings: list[dict]) -> list[str]:
    return [f"{f['level']} at position {f['pos']}: {f['message']}" for f in findings]
