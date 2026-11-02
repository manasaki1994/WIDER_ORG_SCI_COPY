"""Native Word shapes for the flow diagram (prisma-reporting-assistant D20), ported from the ``editable-figures``
skill's ``word_drawing.js`` (``xfrm``, ``shape``, ``lineXml`` and the text-box body) and built from the D19 layout,
not by parsing SVG. Differences from the skill, on purpose: each box's text sits in the box's own ``wps:txbx`` (the
skill lays separate text boxes over the rectangles), so text moves with its box and wraps in Word; the boxes keep
the layout's inner padding as text insets; and the ``wp``, ``wpg``, ``wps`` and ``a`` namespaces are declared on the
drawing element, as python-docx's document root does not declare all of them."""
from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from .flow_layout import FONT, PAD, STEP

EMU = 12700                                   # EMU per point
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
      "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"}
BLACK = "000000"


def _emu(pt: float) -> int:
    return round(pt * EMU)


def xfrm(x: float, y: float, w: float, h: float) -> str:
    """Offset and extent in points, as EMU."""
    return f'<a:xfrm><a:off x="{_emu(x)}" y="{_emu(y)}"/><a:ext cx="{_emu(w)}" cy="{_emu(h)}"/></a:xfrm>'


def fill_xml(color: str | None) -> str:
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>' if color else "<a:noFill/>"


def line_xml(color: str | None, width_pt: float, dashed: bool = False, arrow: bool = False) -> str:
    """The outline; Word sizes arrowheads by the line weight, so a thin line gets the medium head (as the skill)."""
    if not color:
        return "<a:ln><a:noFill/></a:ln>"
    dash = '<a:prstDash val="dash"/>' if dashed else ""
    tail = '<a:tailEnd type="triangle" w="med" len="med"/>' if arrow else ""
    return f'<a:ln w="{_emu(width_pt)}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>{dash}{tail}</a:ln>'


def body_xml(inset_pt: float = 0) -> str:
    """The text-box body: no autofit, so Word keeps the layout's box size; insets in EMU."""
    i = _emu(inset_pt)
    return (f'<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="{i}" tIns="{i}" rIns="{i}" bIns="{i}" anchor="t" '
            f'anchorCtr="0"><a:noAutofit/></wps:bodyPr>')


def paragraphs(lines: list[str], size_pt: float, step_pt: float, bold: bool = False) -> str:
    """One paragraph per line, Arial, exact line spacing (as the skill), black."""
    hp = round(size_pt * 2)
    b = "<w:b/>" if bold else ""
    return "".join(
        f'<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{round(step_pt * 20)}" w:lineRule="exact"/>'
        f'</w:pPr><w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>{b}<w:color w:val="{BLACK}"/>'
        f'<w:sz w:val="{hp}"/><w:szCs w:val="{hp}"/></w:rPr><w:t xml:space="preserve">{escape(ln)}</w:t></w:r></w:p>'
        for ln in lines)


class _Ids:
    def __init__(self, start: int = 1000):
        self.n = start

    def __call__(self) -> int:
        self.n += 1
        return self.n


def shape(sid: int, name: str, cnv: str, sp_pr: str, txbx: str = "", body: str | None = None) -> str:
    return (f'<wps:wsp><wps:cNvPr id="{sid}" name="{escape(name, {chr(34): "&quot;"})}"/>{cnv}<wps:spPr>{sp_pr}'
            f'</wps:spPr>{txbx}{body if body is not None else body_xml()}</wps:wsp>')


def drawing_xml(layout: dict[str, Any], width_in: float, doc_pr_id: int = 1) -> str:
    """One inline ``w:drawing`` holding one ``wpg:wgp`` group: a ``wps:wsp`` rectangle per box with its text in its
    own ``wps:txbx``, a text box per phase heading, a connector line per arrow. Fonts scale with the figure, since
    Word never scales text when a group is resized; the notes are not in the drawing (paragraphs below it)."""
    s = width_in * 72 / layout["width"]                   # pt per layout px
    height = layout["note_y"] - 10
    cx, cy = _emu(layout["width"] * s), _emu(height * s)
    ids = _Ids()
    size, step, pad = FONT * s, STEP * s, PAD * s
    geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    sh = []
    for h in layout["heads"]:
        sh.append(shape(ids(), f"Heading: {h['text']}", '<wps:cNvSpPr txBox="1"/>',
                        xfrm(h["x"] * s, (h["y"] - FONT) * s, 300 * s, (FONT + 4) * s) + geom + fill_xml(None)
                        + line_xml(None, 0), f"<wps:txbx><w:txbxContent>{paragraphs([h['text']], size, step, True)}"
                        "</w:txbxContent></wps:txbx>"))
    for b in layout["boxes"]:
        fill = "FFF4CC" if b["source"] == "missing" else "FFFFFF"
        sh.append(shape(ids(), f"Box {b['id']}", "<wps:cNvSpPr/>",
                        xfrm(b["x"] * s, b["y"] * s, b["w"] * s, b["h"] * s) + geom + fill_xml(fill)
                        + line_xml(BLACK, s, dashed=b["dashed"]),
                        f"<wps:txbx><w:txbxContent>{paragraphs(b['lines'], size, step)}</w:txbxContent></wps:txbx>",
                        body_xml(pad)))
    for x1, y1, x2, y2 in layout["arrows"]:
        sh.append(shape(ids(), "Arrow", "<wps:cNvCnPr/>",
                        xfrm(x1 * s, y1 * s, (x2 - x1) * s, (y2 - y1) * s)
                        + '<a:prstGeom prst="line"><a:avLst/></a:prstGeom><a:noFill/>'
                        + line_xml(BLACK, s, arrow=True), "", "<wps:bodyPr/>"))
    decl = " ".join(f'xmlns:{k}="{v}"' for k, v in NS.items())
    return (f'<w:drawing {decl}><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{doc_pr_id}" name="PRISMA 2020 flow diagram"/>'
            f'<wp:cNvGraphicFramePr/><a:graphic><a:graphicData uri="{NS["wpg"]}"><wpg:wgp><wpg:cNvGrpSpPr/>'
            f'<wpg:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/><a:chOff x="0" y="0"/>'
            f'<a:chExt cx="{cx}" cy="{cy}"/></a:xfrm></wpg:grpSpPr>{"".join(sh)}</wpg:wgp></a:graphicData>'
            "</a:graphic></wp:inline></w:drawing>")
