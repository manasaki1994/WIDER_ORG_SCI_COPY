"""Renderers: query IR → platform-specific string."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..ir import Query
from .boolean import (
    _term,
    field_code_notes,
    render_ebsco,
    render_ovid,
    render_proquest,
    render_pubmed,
    render_scopus,
    render_wos,
)
from .reduced import render_gscholar

_PLATFORMS: dict | None = None


def platforms() -> dict:
    global _PLATFORMS
    if _PLATFORMS is None:
        _PLATFORMS = yaml.safe_load((Path(__file__).parent.parent / "platforms.yaml").read_text(encoding="utf-8"))["platforms"]
    return _PLATFORMS


# sources the importer accepts that are not searched with a string (no renderer, not in platforms.yaml)
OTHER_SOURCES = {"citation_chain": "Citation chain (prior reviews via OpenAlex)"}


def platform_keys() -> list[str]:
    """Every key a project may list as a platform: the catalogue in catalogue order, then the other sources."""
    return [*platforms(), *OTHER_SOURCES]


RENDERERS = {
    "ebsco": render_ebsco,
    "scopus": render_scopus,
    "wos": render_wos,
    "proquest": render_proquest,
    "ovid": render_ovid,
    "pubmed": render_pubmed,
    "gscholar": render_gscholar,
}


BOOLEAN_STYLES = ("ebsco", "scopus", "wos", "proquest", "ovid", "pubmed")


def notes_for(query: Query, style: str | None) -> list[str]:
    """The field-code notes printed with a boolean string (task 2.3); none for Google Scholar or an unknown style."""
    return field_code_notes(query, style) if style in BOOLEAN_STYLES else []


def block_list(query: Query) -> list[dict]:
    """Each block's name, fields and terms, as `wider query render --blocks` prints them and the Search strings page
    lists them: ``text`` is "<name>: <fields> · <terms>"."""
    out = []
    for b in query.blocks:
        name, terms = b.name or b.key, [_term(t, "ebsco") for t in b.terms]
        line = f"{name}: {', '.join(b.fields) or 'title, abstract'}"
        out.append({"key": b.key, "name": name, "fields": list(b.fields), "terms": terms, "line": line,
                    "text": f"{line} · {'; '.join(terms)}"})
    return out


def render(query: Query, platform: str, pretty: bool = True) -> str:
    if platform not in RENDERERS:
        raise ValueError(f"no renderer for platform {platform!r}; known: {', '.join(RENDERERS)}")
    return RENDERERS[platform](query, pretty=pretty)


def render_all(query: Query, keys: list[str] | None = None, pretty: bool = True) -> dict[str, str]:
    return {k: render(query, k, pretty=pretty) for k in (keys or list(RENDERERS))}
