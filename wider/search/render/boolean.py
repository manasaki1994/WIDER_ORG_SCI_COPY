"""Boolean renderers for the field-scoped platforms.

Each renderer turns a block into a field-scoped OR list of terms and the logic tree into nested
parentheses. Output is pretty-printed (one block per line group) by default; ``pretty=False``
gives a single-line string, which is what gets pasted.
"""
from __future__ import annotations

from ..ir import Block, Near, Query, Term, as_phrase, has_truncation, logic_refs, phrase_text

INDENT = "    "


# ----------------------------------------------------------------- term rendering per style
def _term(term: Term, style: str) -> str:
    if isinstance(term, Near):
        return _near(term, style)
    t = term.strip()
    if as_phrase(t):   # a multi-word term typed without quotes is still one phrase (lint notes it)
        return _phrase(phrase_text(t), style)
    return _word(t, style)


def _word(w: str, style: str) -> str:
    if style == "ovid":
        return w.replace("*", "$")
    if style == "pubmed":
        return w
    return w


def _phrase(p: str, style: str) -> str:
    if style == "ovid":
        return p.replace("*", "$")  # unquoted phrase, $ truncation
    if style == "pubmed":
        return f'"{p.replace("*", "")}"'  # PubMed ignores wildcards in phrases
    if style == "scopus":
        return f'"{p}"'  # loose phrase; wildcards allowed
    return f'"{p}"'


def _near_item(item, style: str) -> str:
    tag = field_codes("pubmed")["title"]["code"] if style == "pubmed" else ""
    if isinstance(item, list):
        return "(" + " OR ".join(_term(x, style) + tag for x in item) + ")"
    return _term(item, style) + tag


def _near(n: Near, style: str) -> str:
    ops = {"ebsco": ("N{n}", "W{n}"), "scopus": ("W/{n}", "PRE/{n}"), "wos": ("NEAR/{n}", None),
           "proquest": ("NEAR/{n}", "PRE/{n}"), "ovid": ("ADJ{n}", None)}
    if style == "pubmed":
        # no nested proximity: expand to AND of the items (recall-preserving, less precise)
        return "(" + " AND ".join(_near_item(i, style) for i in n.near) + ")"
    un, ord_ = ops[style]
    op = (ord_ or un) if n.ordered else un
    op = op.format(n=n.distance)
    return "(" + f" {op} ".join(_near_item(i, style) for i in n.near) + ")"


# ----------------------------------------------------------------- block and tree
def _terms_joined(block: Block, style: str, pretty: bool, depth: int) -> str:
    parts = [_term(t, style) for t in block.terms]
    if not pretty:
        return " OR ".join(parts)
    # wrap at ~90 chars, continuation lines start with OR
    lines, cur = [], ""
    for i, p in enumerate(parts):
        piece = p if i == 0 else f"OR {p}"
        if cur and len(cur) + 1 + len(piece) > 90:
            lines.append(cur)
            cur = piece
        else:
            cur = (cur + " " + piece).strip()
    if cur:
        lines.append(cur)
    ind = INDENT * (depth + 1)
    return "\n".join(ind + ln for ln in lines)


def field_codes(style: str) -> dict:
    """The style's ``field_codes:`` from platforms.yaml: {field or "a,b" combination: {code, verified, note, ...}}."""
    from . import platforms  # the package imports this module, so read the catalogue at call time
    return platforms()[style]["field_codes"]


def verified(entry: dict) -> bool:
    """A code counts as verified only with the help page it was checked on: ``source: {url, accessed}``."""
    src = entry.get("source") or {}
    return bool(entry.get("verified") and src.get("url") and src.get("accessed"))


def codes_for(block: Block, style: str) -> list[tuple[str, dict]]:
    """The field codes a block is written with: (field or combination, catalogue entry), in the block's field order."""
    fc, f = field_codes(style), block.fields
    if style == "scopus":
        # one code per block: any keywords field takes TITLE-ABS-KEY; title or abstract alone their own code
        k = "keywords" if "keywords" in f else "title" if f == ["title"] else "abstract" if f == ["abstract"] else "title,abstract"
        return [(k, fc[k])]
    if style == "wos" and set(f) >= {"title", "abstract", "keywords"}:
        return [("title,abstract,keywords", fc["title,abstract,keywords"])]
    if style == "pubmed":
        return [(x, fc[x]) for x in f if x in fc] or [("title", fc["title"])]  # one tag for every field
    return [(x, fc[x]) for x in f if x in fc]


def _block(block: Block, style: str, pretty: bool, depth: int) -> str:
    inner = _terms_joined(block, style, pretty, depth)
    ind = INDENT * depth if pretty else ""
    nl = "\n" if pretty else ""
    used = codes_for(block, style)
    codes = [e["code"] for _, e in used]
    if style == "ebsco":
        return "(" + " OR ".join(f"{c} ({nl}{inner}{nl}{ind})" for c in codes) + ")"
    if style == "scopus":
        return f"{codes[0]}({nl}{inner}{nl}{ind})"
    if style == "wos":
        if used[0][0] == "title,abstract,keywords":
            return f"{codes[0]}({nl}{inner}{nl}{ind})"
        return "(" + " OR ".join(f"{c}({nl}{inner}{nl}{ind})" for c in codes) + ")"
    if style == "proquest":
        return ",".join(codes) + f"({nl}{inner}{nl}{ind})"
    if style == "ovid":
        return f"({nl}{inner}{nl}{ind}).{','.join(codes)}."
    if style == "pubmed":
        # the tag applies per term; wrap each term
        tag = codes[0]
        parts = [f"{_term(t, style)}{tag}" if not isinstance(t, Near) else _term(t, style) for t in block.terms]
        joined = " OR ".join(parts)
        return f"({joined})"
    raise ValueError(style)


def _wider(extra: list[str]) -> str:
    return "title and abstract" if len(extra) > 1 else f"the {extra[0]}"


def field_code_notes(query: Query, style: str) -> list[str]:
    """Notes printed with a string: every unverified keyword code it uses, a code wider than the block, a code's
    own render note (PubMed's [tiab]). Each note once per string, in block order."""
    out: list[str] = []
    refs = set(logic_refs(query.logic))
    for b in query.blocks:
        if b.key not in refs:
            continue
        for combo, e in codes_for(b, style):
            notes = []
            if "keywords" in combo.split(",") and not verified(e):
                notes.append(f"{e['code']}: keyword field code not yet verified against the platform's help page")
            extra = [f for f in e.get("covers") or [] if f not in b.fields]
            if extra and "keywords" in b.fields:
                notes.append(f"keywords searched more widely: the code also covers {_wider(extra)}")
            if e.get("render_note"):
                notes.append(e["render_note"])
            out += [n for n in notes if n not in out]
    return out


def _tree(node, query: Query, style: str, pretty: bool, depth: int) -> str:
    ind = INDENT * depth
    nl = "\n" if pretty else ""
    if isinstance(node, str):
        return _block(query.block(node), style, pretty, depth)
    (op, val), = node.items()
    if op == "not":
        return f"NOT {_tree(val, query, style, pretty, depth)}"
    kids = [_tree(k, query, style, pretty, depth + 1) for k in val]
    if pretty:
        sep = f"\n{ind}{INDENT}{op.upper()}\n{ind}{INDENT}"
        return f"({nl}{ind}{INDENT}" + sep.join(kids) + f"{nl}{ind})"
    return "(" + f" {op.upper()} ".join(kids) + ")"


def _render(query: Query, style: str, pretty: bool) -> str:
    return _tree(query.logic, query, style, pretty, 0)


def render_ebsco(query: Query, pretty: bool = True) -> str:
    return _render(query, "ebsco", pretty)


def render_scopus(query: Query, pretty: bool = True) -> str:
    return _render(query, "scopus", pretty)


def render_wos(query: Query, pretty: bool = True) -> str:
    return _render(query, "wos", pretty)


def render_proquest(query: Query, pretty: bool = True) -> str:
    return _render(query, "proquest", pretty)


def render_ovid(query: Query, pretty: bool = True) -> str:
    return _render(query, "ovid", pretty)


def render_pubmed(query: Query, pretty: bool = True) -> str:
    return _render(query, "pubmed", pretty)


def has_phrase_truncation(query: Query) -> bool:
    return any(isinstance(t, str) and as_phrase(t) and has_truncation(t) for b in query.blocks for t in b.terms)
