"""Parse a Boolean string of any platform of the family into the query IR (change 2c, D14).

One recursive-descent parser, set up per platform by a :class:`Dialect` read from ``platforms.yaml``: field codes
written before a group (EBSCOhost ``TI (...)``, Scopus ``TITLE-ABS(...)``, Web of Science ``TI=(...)``, ProQuest
``TI,AB(...)``), after a group (Ovid ``(...).ti,ab.``) or after each term (PubMed ``term[tiab]``), the proximity
operators, the truncation character and Ovid's unquoted phrases. Blocks are recovered from field-scoped groups:
``(TI (X) OR AB (X))`` with the same X becomes one block searched in title and abstract; a PubMed OR list of terms
with the same tag becomes one block. Lines starting with ``#`` are ignored. Operators are read whatever their case;
:mod:`.string_check` warns about lower-case ones and about every other reading this parser has to choose.

:func:`canonical` is what a render and a parse change and nothing else (design D0b), so
``parse(render(q, P), P) == canonical(q, P)`` for every query the generator can make.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .ir import Block, Near, Query, logic_refs

CURLY = {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'"}
OPERATORS = {"AND", "OR", "NOT"}
# EBSCOhost's subject and all-text codes, read as before (not in the catalogue's field codes)
EXTRA_EBSCO = {"SU": ["subject"], "TX": ["text"]}
PUBMED_TAGS = {"[tiab]": ["title", "abstract"], "[ti]": ["title"], "[ab]": ["abstract"]}

_TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<lp>\()
  | (?P<rp>\))
  | (?P<phrase>"[^"]*")
  | (?P<word>[^\s()"]+)
''', re.X)
_OVID_TAG = re.compile(r"^(.*?)(\.[a-z]{2}(?:,[a-z]{2})*\.)$", re.I)


@dataclass
class Tok:
    kind: str
    text: str
    pos: int = 0


@dataclass(frozen=True)
class Dialect:
    platform: str
    prefix: dict = field(default_factory=dict)    # upper-cased code -> IR fields, written CODE(...)
    suffix: dict = field(default_factory=dict)    # Ovid: lower-case code -> IR fields, written (...).ti,ab.
    tags: dict = field(default_factory=dict)      # PubMed: tag -> IR fields, written term[tiab]
    prox: tuple = (("N", False), ("W", True))     # operator prefix (before the distance), ordered
    truncation: str = "*"
    unquoted_phrases: bool = False
    implied_and: bool = False   # Web of Science: words with no operator between them are ANDed

    def prox_match(self, text: str):
        """(distance, ordered) when ``text`` is a proximity operator of this dialect, else None."""
        for pre, ordered in self.prox:
            m = re.fullmatch(re.escape(pre) + r"(\d+)", text, re.I)
            if m:
                return int(m.group(1)), ordered
        return None

    def is_operator(self, text: str) -> bool:
        return text.upper() in OPERATORS or self.prox_match(text) is not None


def style_of(platform: str) -> str:
    from .render import platforms

    cat = platforms()
    if platform not in cat:
        raise ValueError(f"unknown platform {platform!r}; known: {', '.join(cat)}")
    return cat[platform].get("style", platform)


def dialect(platform: str) -> Dialect:
    """The dialect of a catalogue key or syntax style. Google Scholar's reduced string is one-way."""
    from .render import BOOLEAN_STYLES, platforms

    style = style_of(platform)
    if style not in BOOLEAN_STYLES:
        raise ValueError(f"{platform} strings are one-way: they cannot be parsed")
    cat = platforms()[style]
    codes = {e["code"]: e.get("covers") or k.split(",") for k, e in cat["field_codes"].items()}
    prox = []
    for key, ordered in (("proximity", False), ("proximity_ordered", True)):
        pat = cat.get(key)
        if pat and "{n}" in pat and "{phrase}" not in pat:
            prox.append((pat.split("{n}")[0], ordered))
    if style == "pubmed":
        return Dialect(style, tags=dict(PUBMED_TAGS), prox=())
    if style == "ovid":
        return Dialect(style, suffix={c.lower(): f for c, f in codes.items()}, prox=tuple(prox), truncation="$",
                       unquoted_phrases=True)
    prefix = {c.upper(): f for c, f in codes.items()}
    if style == "ebsco":
        prefix.update(EXTRA_EBSCO)
    return Dialect(style, prefix=prefix, prox=tuple(prox), implied_and=style == "wos")


EBSCO = Dialect("ebsco", prefix={"TI": ["title"], "AB": ["abstract"], "KW": ["keywords"], **EXTRA_EBSCO})


def clean(text: str) -> str:
    """Comment lines blanked (positions kept) and curly quotes made straight."""
    lines = [(" " * len(ln) if ln.lstrip().startswith("#") else ln) for ln in text.split("\n")]
    return "".join(CURLY.get(c, c) for c in "\n".join(lines))


def tokenize(s: str, d: Dialect = EBSCO) -> list[Tok]:
    out = []
    for m in _TOKEN.finditer(s):
        kind, text, pos = m.lastgroup, m.group(0), m.start()
        if kind == "ws":
            continue
        if kind == "word" and d.tags and "[" in text:
            i = text.index("[")
            if i:
                out.append(Tok("word", text[:i], pos))
            out.append(Tok("tag", text[i:], pos + i))
            continue
        if kind == "word" and d.suffix and (t := _OVID_TAG.match(text)):
            if t.group(1):
                out.append(Tok("word", t.group(1), pos))
            out.append(Tok("tag", t.group(2), pos + len(t.group(1))))
            continue
        out.append(Tok(kind, text, pos))
    return out


def tag_fields(tok: Tok, d: Dialect) -> list[str]:
    if d.tags:
        if tok.text.lower() not in d.tags:
            raise ValueError(f"unknown field tag {tok.text!r} at position {tok.pos}")
        return list(d.tags[tok.text.lower()])
    out = []
    for c in tok.text.strip(".").split(","):
        if c.lower() not in d.suffix:
            raise ValueError(f"unknown field code .{c}. at position {tok.pos}")
        out += [f for f in d.suffix[c.lower()] if f not in out]
    return out


# AST: ("field", [fields], node) | ("and"/"or", [nodes]) | ("not", node) | ("near", n, ordered, [nodes]) | ("term", str)
@dataclass
class Parser:
    toks: list[Tok]
    d: Dialect = EBSCO
    i: int = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            t = self.peek()
            raise ValueError(f"unexpected token {t.text!r} at position {t.pos}")
        return node

    # precedence: proximity > NOT > AND > OR; "A NOT B" and "A AND NOT B" both mean A AND (NOT B)
    def expr(self):
        return self.or_()

    def _op(self, *ops):
        t = self.peek()
        return t is not None and t.kind == "word" and t.text.upper() in ops

    def or_(self):
        items = [self.and_()]
        while self._op("OR"):
            self.take()
            items.append(self.and_())
        return items[0] if len(items) == 1 else ("or", items)

    def and_(self):
        items = [self.not_()]
        while self._op("AND", "NOT") or self._implied():
            if self._op("AND"):
                self.take()
            items.append(self.not_())   # a NOT here is taken by not_
        return items[0] if len(items) == 1 else ("and", items)

    def _implied(self):
        """An operand right after an operand, with no operator between them (``abusive supervis*``): an AND in the
        dialects that read it so (:mod:`.string_check` warns)."""
        t = self.peek()
        return self.d.implied_and and t is not None and (t.kind in ("lp", "phrase") or
                                                          (t.kind == "word" and not self.d.is_operator(t.text)))

    def not_(self):
        if self._op("NOT"):
            self.take()
            return ("not", self.not_())
        return self.prox_()

    def prox_(self):
        items, op = [self.atom()], None
        while self.peek() and self.peek().kind == "word" and self.d.prox_match(self.peek().text):
            op = self.d.prox_match(self.take().text)
            items.append(self.atom())
        return items[0] if op is None else ("near", op[0], op[1], items)

    def _group(self, opener: Tok, what: str = ""):
        node = self.expr()
        if not self.peek() or self.peek().kind != "rp":
            raise ValueError(f"missing ){what}: the ( at position {opener.pos} is never closed")
        self.take()
        return node

    def _tagged(self, node):
        """A field tag after a term or group (PubMed, Ovid): the group's tag wins over tags inside it."""
        if self.peek() and self.peek().kind == "tag":
            fields = tag_fields(self.take(), self.d)
            return ("field", fields, _untag(node))
        return node

    def atom(self):
        t = self.peek()
        if t is None:
            raise ValueError("unexpected end of query")
        if t.kind == "lp":
            node = self._group(self.take())
            # PubMed: the tagged terms directly inside one pair of parentheses are one block; a group is never merged
            return self._tagged(("group", _merge_tagged(node)) if self.d.tags else node)
        nxt = self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None
        if t.kind == "word" and self.d.prefix and nxt is not None and nxt.kind == "lp":
            codes = t.text.upper().split(",")
            if all(c in self.d.prefix for c in codes):
                self.take()
                node = self._group(self.take(), " after field group")
                fields = []
                for c in codes:
                    fields += [f for f in self.d.prefix[c] if f not in fields]
                return ("field", fields, node)
        tok = self.take()
        if tok.kind == "phrase":
            return self._tagged(("term", self._trunc(tok.text)))
        if tok.kind != "word" or self.d.is_operator(tok.text):
            raise ValueError(f"unexpected {tok.text!r} at position {tok.pos}")
        words = [tok.text]
        while self.d.unquoted_phrases and self.peek() and self.peek().kind == "word" \
                and not self.d.is_operator(self.peek().text):
            words.append(self.take().text)
        text = words[0] if len(words) == 1 else '"' + " ".join(words) + '"'
        return self._tagged(("term", self._trunc(text)))

    def _trunc(self, text: str) -> str:
        return text.replace(self.d.truncation, "*") if self.d.truncation != "*" else text


def _untag(node):
    if node[0] == "group":
        return _untag(node[1])
    if node[0] == "field":
        return _untag(node[2])
    if node[0] in ("and", "or"):
        return (node[0], [_untag(k) for k in node[1]])
    if node[0] == "not":
        return ("not", _untag(node[1]))
    if node[0] == "near":
        return ("near", node[1], node[2], [_untag(k) for k in node[3]])
    return node


def _merge_tagged(node):
    """PubMed: an OR list of terms carrying the same tag is one block (``a[tiab] OR b[tiab]``). Only the terms of
    one level are merged; a parenthesised group (a block of its own) is not."""
    if node[0] == "or" and all(k[0] == "field" and k[1] == node[1][0][1] and k[2][0] == "term" for k in node[1]):
        return ("field", node[1][0][1], ("or", [k[2] for k in node[1]]))
    return node


def _ungroup(node):
    if node[0] == "group":
        return _ungroup(node[1])
    if node[0] in ("and", "or"):
        return (node[0], [_ungroup(k) for k in node[1]])
    if node[0] in ("not",):
        return ("not", _ungroup(node[1]))
    if node[0] == "field":
        return ("field", node[1], _ungroup(node[2]))
    if node[0] == "near":
        return ("near", node[1], node[2], [_ungroup(k) for k in node[3]])
    return node


def _flatten_or_terms(node) -> list | None:
    """If node is a pure OR of terms/near groups, return the term list in IR form; else None."""
    if node[0] == "term":
        return [node[1]]
    if node[0] == "near":
        return [_near_ir(node)]
    if node[0] == "or":
        out = []
        for k in node[1]:
            sub = _flatten_or_terms(k)
            if sub is None:
                return None
            out.extend(sub)
        return out
    return None


def _near_ir(node) -> Near:
    _, n, ordered, items = node
    conv = []
    for it in items:
        if it[0] == "term":
            conv.append(it[1])
        elif it[0] == "or" and all(k[0] == "term" for k in it[1]):
            conv.append([k[1] for k in it[1]])
        else:
            raise ValueError("proximity groups may only contain terms or OR groups of terms")
    return Near(near=conv, distance=n, ordered=ordered)


@dataclass
class Builder:
    blocks: list[Block] = field(default_factory=list)
    names: dict[str, int] = field(default_factory=dict)

    def key_for(self, terms: list) -> str:
        first = terms[0] if isinstance(terms[0], str) else "near"
        base = re.sub(r"[^a-z0-9]+", "_", str(first).lower().strip('"').replace("*", "")).strip("_") or "block"
        if not base[0].isalpha():
            base = "b_" + base
        n = self.names.get(base, 0) + 1
        self.names[base] = n
        return base if n == 1 else f"{base}_{n}"

    def logic(self, node, fields: list[str] | None = None):
        """The logic tree of ``node``. ``fields`` is the tag of an enclosing field group, which holds for every group
        nested in it (``TS=((a OR b) AND (c OR d))``); without one a block is searched in title and abstract."""
        # a block = (field OR field) with identical term lists, or a single field group
        if node[0] == "field":
            terms = _flatten_or_terms(node[2])
            if terms is None:
                return self.logic(node[2], node[1])
            return self.add(terms, node[1])
        if node[0] == "or" and all(k[0] == "field" for k in node[1]):
            lists = [_flatten_or_terms(k[2]) for k in node[1]]
            if all(lst is not None for lst in lists) and all(_same(lists[0], lst) for lst in lists[1:]):
                fields_ = []
                for k in node[1]:
                    fields_ += [f for f in k[1] if f not in fields_]
                return self.add(lists[0], fields_)
        if node[0] == "or":   # the terms of an OR list are one block, its groups their own parts
            terms = [t for k in node[1] if k[0] in ("term", "near") for t in _flatten_or_terms(k)]
            out, placed = [], False
            for k in node[1]:   # the terms block stands where the first term stood
                if k[0] not in ("term", "near"):
                    out.append(self.logic(k, fields))
                elif not placed:
                    out.append(self.add(terms, fields or ["title", "abstract"]))
                    placed = True
            return out[0] if len(out) == 1 else {"or": out}
        if node[0] == "and":
            return {"and": [self.logic(k, fields) for k in node[1]]}
        if node[0] == "not":
            return {"not": self.logic(node[1], fields)}
        if node[0] in ("term", "near"):
            return self.add(_flatten_or_terms(node), fields or ["title", "abstract"])
        raise ValueError(f"cannot convert node {node[0]}")

    def add(self, terms: list, fields: list[str]) -> str:
        key = self.key_for(terms)
        self.blocks.append(Block(key=key, name=key.replace("_", " "), terms=terms, fields=list(fields)))
        return key


def _same(a: list, b: list) -> bool:
    return [_canon(x) for x in a] == [_canon(x) for x in b]


def _canon(x):
    return x.model_dump() if isinstance(x, Near) else x


def _has_field(node) -> bool:
    if node[0] == "field":
        return True
    if node[0] in ("and", "or"):
        return any(_has_field(k) for k in node[1])
    if node[0] in ("not", "group"):
        return _has_field(node[1])
    if node[0] == "near":
        return any(_has_field(k) for k in node[3])
    return False


def carry_tags(node):
    """Web of Science: an untagged group after a tagged one in the same AND or OR list takes that tag
    (``AB=(a OR b) AND (c OR d)`` searches c and d in the abstract too). :mod:`.string_check` warns."""
    if node[0] not in ("and", "or"):
        return node
    out, last = [], None
    for k in node[1]:
        if k[0] == "field":
            last = k[1]
            out.append(k)
        elif last is not None and not _has_field(k):
            out.append(("field", last, k))
        else:
            out.append(carry_tags(k))
    return (node[0], out)


def parse_ast(text: str, platform: str = "ebsco"):
    d = dialect(platform)
    ast = Parser(tokenize(clean(text), d), d).parse()
    if d.implied_and:
        ast = carry_tags(ast)
    return _ungroup(_merge_tagged(ast)) if d.tags else ast


def parse(text: str, platform: str = "ebsco", notes: str = "") -> Query:
    """The query a string of ``platform`` says. Raises ValueError naming the position and the token."""
    b = Builder()
    logic = b.logic(parse_ast(text, platform))
    return Query(blocks=b.blocks, logic=logic, notes=notes)


# ----------------------------------------------------------------- canonical form (D0b)
def _ws(t):
    if isinstance(t, Near):
        return Near(near=[[_ws(x) for x in i] if isinstance(i, list) else _ws(i) for i in t.near],
                    distance=t.distance, ordered=t.ordered)
    return " ".join(t.split())


def canonical(q: Query, platform: str) -> Query:
    """``q`` as a render for ``platform`` and a parse give it back: only the changes of design D0b (a)-(d)."""
    from .render import platforms
    from .render.boolean import codes_for

    style = style_of(platform)
    cat = platforms()[style]
    back = {}
    for k, e in cat["field_codes"].items():
        back[e["code"]] = PUBMED_TAGS["[tiab]"] if style == "pubmed" else (e.get("covers") or k.split(","))
    b = Builder()
    rename: dict[str, str] = {}
    for key in dict.fromkeys(logic_refs(q.logic)):
        blk = q.block(key)
        terms = []
        for t in blk.terms:
            t = _ws(t)
            if isinstance(t, Near) and t.ordered and not cat.get("proximity_ordered"):
                t = Near(near=t.near, distance=t.distance, ordered=False)
            if isinstance(t, str) and style == "pubmed" and t.startswith('"'):
                t = t.replace("*", "")
            terms.append(t)
        fields = []
        for _, e in codes_for(blk, style):
            fields += [f for f in back[e["code"]] if f not in fields]
        rename[key] = b.add(terms, fields)
    return Query(blocks=b.blocks, logic=_rename(q.logic, rename), notes="")


def _rename(node, m):
    if isinstance(node, str):
        return m[node]
    (op, val), = node.items()
    return {op: _rename(val, m)} if op == "not" else {op: [_rename(k, m) for k in val]}
