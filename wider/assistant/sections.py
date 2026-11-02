"""The sections Ask WIDER searches (guide-assistant D3): one per written part of each shipped guide page, one per layer
of each methods topic and one per glossary term, all read from the files shipped under ``wider/`` as they are now
(D22). Nothing here reads a project, a record or a setting."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..guide import guide_text, methods, pages
from ..guide.methods import Section, slug


def guide_sections() -> list[Section]:
    """One section per written part of each guide page: id ``guide/<page>/<part>``, the context line "WIDER guide,
    page <title>: <part>", the page's own address as anchor (the panel opens that page's guide)."""
    out = []
    for page in pages():
        g = guide_text(page)
        for part in g["parts"]:
            heading = part["heading"]
            out.append(Section(id=f"guide/{page}/{slug(heading)[0]}", kind="guide", topic=None, layer=None,
                               title=f"{g['title']}: {heading}", context=f"WIDER guide, page {g['title']}: {heading}",
                               text="\n".join(f"- {i}" for i in part["items"]), settings=(), artifacts=(),
                               pages=(page,), anchor=f"#{page}"))
    return out


def all_sections(methods_root: Path | None = None) -> list[Section]:
    """The guide sections, then the methods layers and glossary terms of ``methods_root`` (the shipped folder unless a
    test names another). A methods file that cannot be read is left out (``methods.problems``)."""
    return guide_sections() + methods.sections(methods_root)


def docs_key(sections: list[Section]) -> str:
    """sha256 over the ids, titles, contexts and texts in id order (D22): any edit of a sentence, context line or
    glossary synonym gives a new key, so the keyword index and the meaning index are built anew."""
    h = hashlib.sha256()
    for s in sorted(sections, key=lambda s: s.id):
        for part in (s.id, s.title, s.context, s.text):
            h.update(part.encode("utf-8") + b"\0")
    return h.hexdigest()


def section_hash(s: Section) -> str:
    """The hash of one section's text, stored with each turn so a later edit of that section is noticed (D22)."""
    return hashlib.sha256("\0".join((s.id, s.title, s.context, s.text)).encode("utf-8")).hexdigest()[:16]
