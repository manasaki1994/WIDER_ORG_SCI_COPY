"""Parse an EBSCO-style Boolean string (TI (...) OR AB (...), N5/W5, * truncation) into the query IR.

The purpose is to import a researcher's existing search string so WIDER can render it for the other
platforms and version it. Blocks are recovered from field-scoped groups: ``(TI (X) OR AB (X))`` with the
same X becomes one block searched in title and abstract. Lines starting with ``#`` are ignored.

Since change 2c the parser is :mod:`.parse_boolean`, set up per platform; this module is its EBSCOhost wrapper,
kept so its callers and tests work unchanged.
"""
from __future__ import annotations

import re

from .ir import Query
from .parse_boolean import Builder, Parser, Tok, parse, tokenize  # noqa: F401  (re-exported)

FIELD_NAMES = {"TI": "title", "AB": "abstract", "KW": "keywords", "SU": "subject", "TX": "text"}


def parse_ebsco(text: str, notes: str = "") -> Query:
    return parse(text, "ebsco", notes=notes)


def normalise(s: str) -> str:
    """Whitespace- and case-insensitive canonical form for comparing two Boolean strings."""
    s = "\n".join(ln for ln in s.splitlines() if not ln.lstrip().startswith("#"))
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    return s
