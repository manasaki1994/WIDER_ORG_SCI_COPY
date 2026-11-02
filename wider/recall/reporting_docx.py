"""The Word files of the reporting exports (prisma-reporting-assistant D17, D18, D20). python-docx and lxml are
imported lazily, so WIDER runs without them; ``available()`` says whether they can be imported."""
from __future__ import annotations

from pathlib import Path
from typing import Any

TEXT_WIDTH_IN = 6.3              # A4 with 2.5 cm margins (D17)
MARGIN_CM = 2.5
AUTHOR = "WIDER"
LICENCE = "Licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."
FLOW_TITLE = "PRISMA 2020 flow diagram"


def available() -> bool:
    try:
        import docx  # noqa: F401
        import lxml  # noqa: F401
    except ImportError:
        return False
    return True


def _document(title: str):
    """A4 portrait, 2.5 cm margins, built-in styles, core properties title and author "WIDER" (D17)."""
    from docx import Document
    from docx.shared import Cm

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Cm(MARGIN_CM)
    cp = doc.core_properties
    cp.title, cp.author, cp.last_modified_by = title, AUTHOR, AUTHOR
    cp.comments = ""
    return doc


def flow_docx(layout: dict[str, Any], path: Path) -> Path:
    """``prisma_2020_flow.docx`` (D20): the diagram as one editable group of Word shapes, then the notes and the
    PRISMA 2020 citation and licence line."""
    from lxml import etree

    from . import prisma_2020
    from .docx_shapes import drawing_xml

    doc = _document(FLOW_TITLE)
    doc.add_paragraph().add_run()._r.append(etree.fromstring(drawing_xml(layout, TEXT_WIDTH_IN)))
    for note in layout["notes"]:
        doc.add_paragraph(note)
    doc.add_paragraph(f"Flow diagram template: {prisma_2020.CITATION}. {LICENCE}")
    doc.save(str(path))
    return path


TABLE_STYLE = "PRISMA checklist"
STRING_STYLE = "Search string"
SECTION_FILL = "D9D9D9"
WHERE_NEEDED = "Researcher input needed: page or section"
COLUMNS = {"prisma_2020": ("Section and Topic", "Item #", "Checklist item", "Location where item is reported"),
           "prisma_s": ("Section/topic", "Item #", "Checklist item", "Location where item is reported")}
CHECKLIST_WIDTHS_CM = (3.6, 1.4, 7.0, 4.0)   # 16 cm text width; the item number narrow as in the template
CHECKLIST_TITLE = {"prisma_2020": "PRISMA 2020 checklist", "prisma_s": "PRISMA-S checklist"}
METHODS_TITLE = "Methods documentation"


def _styles(doc) -> None:
    """A copy of "Table Grid" named "PRISMA checklist" that sets Arial 9 pt, and a monospace paragraph style for the
    search strings, so no run carries direct font formatting (D17, D18)."""
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Pt

    st = doc.styles.add_style(TABLE_STYLE, WD_STYLE_TYPE.TABLE)
    st.base_style = doc.styles["Table Grid"]
    st.font.name, st.font.size = "Arial", Pt(9)
    ss = doc.styles.add_style(STRING_STYLE, WD_STYLE_TYPE.PARAGRAPH)
    ss.base_style = doc.styles["Normal"]
    ss.font.name, ss.font.size = "Courier New", Pt(9)


def _shade(cell, fill: str) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    cell._tc.get_or_add_tcPr().append(shd)


def _repeat_header(row) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    h = OxmlElement("w:tblHeader")
    h.set(qn("w:val"), "true")
    row._tr.get_or_add_trPr().append(h)


def _highlighted(paragraph, text: str) -> None:
    from docx.enum.text import WD_COLOR_INDEX

    paragraph.add_run(text).font.highlight_color = WD_COLOR_INDEX.YELLOW


def _widths(table, widths_cm: tuple[float, ...]) -> None:
    from docx.shared import Cm

    table.autofit = False
    for row in table.rows:
        for cell, w in zip(row.cells, widths_cm):
            cell.width = Cm(w)


def _table(doc, header: tuple[str, ...]):
    t = doc.add_table(rows=1, cols=len(header))
    t.style = doc.styles[TABLE_STYLE]
    for cell, h in zip(t.rows[0].cells, header):
        cell.text = h
    _repeat_header(t.rows[0])
    return t


def checklist_docx(rows: list[dict[str, Any]], checklist: str, citation: str, path: Path) -> Path:
    """One checklist in its published structure (D17): the published columns and wording, section rows shaded, the
    researcher's "where reported" note in the location cell (empty: a highlighted request, never WIDER's evidence),
    no status. ``rows`` are ``{"section"}`` rows and ``{"topic", "no", "official", "where", "not_applicable"}``
    rows in checklist order."""
    doc = _document(CHECKLIST_TITLE[checklist])
    _styles(doc)
    doc.add_heading(CHECKLIST_TITLE[checklist], level=1)
    t = _table(doc, COLUMNS[checklist])
    topic = None
    for r in rows:
        cells = t.add_row().cells
        if "section" in r:
            merged = cells[0].merge(cells[-1])
            merged.text = r["section"].upper()
            _shade(merged, SECTION_FILL)
            topic = None
            continue
        cells[0].text = r["topic"] if r["topic"] != topic else ""
        topic = r["topic"]
        cells[1].text, cells[2].text = r["no"], r["official"]
        if r["not_applicable"]:
            cells[3].text = f"Not applicable: {r['not_applicable']}"
        elif r["where"]:
            cells[3].text = r["where"]
        else:
            _highlighted(cells[3].paragraphs[0], WHERE_NEEDED)
    _widths(t, CHECKLIST_WIDTHS_CM)
    doc.add_paragraph(f"Checklist: {citation}. {LICENCE} Checklist wording copied unchanged.")
    doc.save(str(path))
    return path


def _gapped(paragraph, text: str, gap: Any) -> None:
    """``text`` as runs, every bracketed gap ("[search date: not recorded by WIDER]") highlighted (D18)."""
    pos = 0
    for m in gap.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        _highlighted(paragraph, m.group(0))
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def methods_docx(data: dict[str, Any], gap: Any, path: Path) -> Path:
    """``methods_documentation.docx`` (D18): "Methods text" item by item (each block a label line and its text,
    researcher text and WIDER drafts in separate paragraphs, gaps highlighted), "Search strategies" one block per
    platform with its archived string verbatim, and "Reporting status" (where the statuses go, so the checklists
    stay official); the footer says what it was prepared from."""
    doc = _document(METHODS_TITLE)
    _styles(doc)
    doc.sections[0].footer.paragraphs[0].text = data["footer"]
    doc.add_heading(METHODS_TITLE, level=1)
    doc.add_heading("Methods text", level=1)
    for it in data["methods"]:
        doc.add_heading(it["heading"], level=2)
        for b in it["blocks"]:
            if b["label"]:
                doc.add_paragraph(b["label"])
            for para in [p for p in b["text"].split("\n\n") if p.strip()] or [""]:
                _gapped(doc.add_paragraph(), para.strip(), gap)
    doc.add_heading("Search strategies", level=1)
    if not data["strategies"]:
        doc.add_paragraph("No database imported yet.")
    for s in data["strategies"]:
        doc.add_heading(s["platform"], level=2)
        if s["string"] is not None:
            for ln in s["string"].split("\n"):
                doc.add_paragraph(ln, style=STRING_STYLE)
        else:
            _gapped(doc.add_paragraph(), s["no_string"], gap)
        for ln in s["lines"]:
            _gapped(doc.add_paragraph(), ln, gap)
    doc.add_heading("Reporting status", level=1)
    for title, rows in data["status"]:
        doc.add_heading(title, level=2)
        t = _table(doc, ("Item", "WIDER status", "Your text", "Still missing"))
        for r in rows:
            for cell, v in zip(t.add_row().cells, (r["item"], r["status"], r["your_text"], r["missing"])):
                cell.text = v
    doc.save(str(path))
    return path
