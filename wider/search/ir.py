"""Platform-independent query representation.

A query is concept *blocks* joined by a boolean *logic* tree. Each block is a list of terms:

- ``"behavio*"``           a word, ``*`` = right truncation
- ``'"decision making"'``  a phrase (quoted); may end in ``*`` inside the quotes for phrase truncation
- ``{"near": ["employee*", "turnover"], "distance": 5}``       proximity: all items within N words, any order
- ``{"near": ["effect*", ["performance", "output*"]], "distance": 5}``  an inner list is an OR group

The logic tree uses block keys and ``{"and": [...]}`` / ``{"or": [...]}`` / ``{"not": x}`` nodes.
Fields default to title and abstract on every platform that supports field scoping.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Role = Literal["required", "optional"]


class Near(BaseModel):
    near: list[str | list[str]]
    distance: int = 5
    ordered: bool = False

    @field_validator("near")
    @classmethod
    def _two_or_more(cls, v):
        if len(v) < 2:
            raise ValueError("a proximity group needs at least two items")
        return v


Term = str | Near


def near_from_text(term: Any) -> dict | None:
    """The proximity group a string term holds as JSON text (``'{"near": [...], "distance": 3}'``, as some models
    write it), else None. A term starting with ``{`` is never a word or a phrase."""
    if not isinstance(term, str) or not term.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(term)
    except ValueError:
        return None
    return data if isinstance(data, dict) and "near" in data else None


class Block(BaseModel):
    key: str
    name: str = ""
    description: str = ""
    role: Role = "required"
    terms: list[Term] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=lambda: ["title", "abstract"])

    @field_validator("terms", mode="before")
    @classmethod
    def _near_text(cls, v):
        # a proximity group saved as JSON text is read as the group (versions saved before tidy_terms existed)
        return [near_from_text(t) or t for t in v] if isinstance(v, list) else v

    @field_validator("key")
    @classmethod
    def _key(cls, v):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", v):
            raise ValueError("block keys are lower-case identifiers")
        return v


class Query(BaseModel):
    blocks: list[Block]
    logic: Any  # str block key | {"and": [...]} | {"or": [...]} | {"not": ...}
    notes: str = ""

    @model_validator(mode="after")
    def _check_logic(self):
        keys = {b.key for b in self.blocks}
        if len(keys) != len(self.blocks):
            raise ValueError("duplicate block keys")
        for ref in logic_refs(self.logic):
            if ref not in keys:
                raise ValueError(f"logic refers to unknown block {ref!r}")
        return self

    def block(self, key: str) -> Block:
        for b in self.blocks:
            if b.key == key:
                return b
        raise KeyError(key)


def logic_refs(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        out: list[str] = []
        for k, v in node.items():
            if k in ("and", "or"):
                for child in v:
                    out.extend(logic_refs(child))
            elif k == "not":
                out.extend(logic_refs(v))
            else:
                raise ValueError(f"unknown logic operator {k!r}")
        return out
    raise ValueError(f"bad logic node {node!r}")


# ----- term helpers shared by renderers -----
PHRASE = re.compile(r'^"(.+)"$')


def is_phrase(term: str) -> bool:
    return bool(PHRASE.match(term.strip()))


def needs_quotes(term) -> bool:
    """A multi-word term typed without quotes. Every renderer quotes it as a phrase, since unquoted words inside an
    OR list would each be searched on their own (or read as an operator) and change the query's meaning."""
    return isinstance(term, str) and not is_phrase(term) and " " in term.strip()


def as_phrase(term: str) -> bool:
    """True when the renderers write ``term`` as a phrase: quoted, or multi-word (:func:`needs_quotes`)."""
    return is_phrase(term) or needs_quotes(term)


def phrase_text(term: str) -> str:
    m = PHRASE.match(term.strip())
    return m.group(1) if m else term.strip()


def has_truncation(term: str) -> bool:
    return phrase_text(term).endswith("*")


def term_words(term: Term) -> list[str]:
    """Flat list of word tokens in a term, for linting and spelling expansion."""
    if isinstance(term, Near):
        out: list[str] = []
        for it in term.near:
            for s in (it if isinstance(it, list) else [it]):
                out.extend(term_words(s))
        return out
    return phrase_text(term).replace("*", "").split()


def tidy_terms(data: dict) -> tuple[dict, list[str]]:
    """A proposed query (IR as plain data) with the slips a model makes fixed, and one line per fix: a proximity
    group written as JSON text becomes the group, an unquoted multi-word term is quoted (``third part*`` ->
    ``"third part*"``). Nothing else changes; ``data`` is not modified."""
    out = json.loads(json.dumps(data))
    fixes: list[str] = []
    for b in out.get("blocks") or []:
        terms = b.get("terms")
        if not isinstance(terms, list):
            continue
        for i, t in enumerate(terms):
            near = near_from_text(t)
            if near is not None:
                try:
                    Near.model_validate(near)
                except ValueError as e:   # a malformed group is refused, never saved as text
                    raise ValueError(f"block {b.get('key')}: the term {t[:80]} is a proximity group written as "
                                     f"JSON text, and not a valid one: {str(e).splitlines()[-1][:120]}") from e
                terms[i] = near
                fixes.append(f"{b.get('key')}: proximity group written as JSON text read as a proximity group")
            elif isinstance(t, str) and not is_phrase(t) and " " in t.strip() and '"' not in t:
                terms[i] = '"' + " ".join(t.split()) + '"'
                fixes.append(f"{b.get('key')}: multi-word term {t.strip()} quoted as {terms[i]}")
    return out, fixes


def count_terms(q: Query) -> dict[str, int]:
    return {b.key: len(b.terms) for b in q.blocks}
