"""The PRISMA 2020 flow diagram of the reporting exports (prisma-reporting-assistant D19): one layout model that the
SVG (``prisma_2020_flow.svg``), its machine-readable twin (``prisma_2020_flow.json``) and the Word drawing
(``docx_shapes``) are all built from. The SVG keeps to the ``editable-figures`` subset: rect, straight paths with
``marker-end``, text without tspan, start or middle anchor, #rrggbb colours, no transforms."""
from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

WIDTH = 680
FONT = 13
STEP = 17                 # line step, 1.3 em
PAD = 8
GAP = 34                  # between rows, room for the arrows
COLS = {0: (10, 320), 1: (360, 310)}   # x, width
HEAD_H = 20
INPUT_NEEDED = "researcher input needed"
NO_SCREENING = "no screening yet"
DAGGER = "†"
MISSING_FILL = "#fff4cc"
NOTE_MODELS = ("WIDER's model screening is not an automation tool removing records before screening: its "
               "decisions are counted under Records screened and Records excluded.")
NOTE_ENTERED = f"{DAGGER} entered by the researcher"
NOTE_DERIVED = f"{DAGGER} entered by the researcher or computed from an entered count"
OTHER_NOTE = "deduplicated and screened with the database records in WIDER"
MULTI_NOTE = "databases searched on this platform: researcher input needed"


def _n(v: int | None, source: str, screened: bool = True) -> str:
    if v is None:
        return INPUT_NEEDED if screened else NO_SCREENING
    return f"{v:,}{DAGGER}" if source in ("researcher", "derived") else f"{v:,}"


def _wrap(text: str, width: int) -> list[str]:
    """Lines of at most ``width`` characters; 13 px Arial averages under 7 px a character."""
    out, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = "  " + word
        else:
            cur = f"{cur} {word}" if cur else word
    return out + ([cur] if cur else [])


def flow_boxes(counts: dict[str, Any], entered: dict[str, int], excluded: list[dict[str, Any]] = (),
               split: dict[str, int] | None = None) -> dict[str, Any]:
    """The boxes of the template "new systematic reviews which included searches of databases, registers and other
    sources" with their lines, counts and sources, laid out on a 680 px canvas. ``counts`` are ``prisma_counts``,
    ``entered`` and ``excluded`` the researcher's counts (D21), ``split`` the excluded records by the models and by a
    researcher (the template footnote on automation tools)."""
    from ..search.render import platforms
    from .reporting import _short

    screened = counts.get("protocol") is not None
    boxes: list[dict[str, Any]] = []

    def cnt(name, label, n, source):
        return {"name": name, "label": label, "n": n, "source": source}

    def add(bid, col, row, lines, cs, source=None, phase=None):
        src = source or (cs[0]["source"] if cs else "wider")
        dashed = any(c["source"] == "missing" for c in cs)
        boxes.append({"id": bid, "col": col, "row": row, "lines": lines, "counts": cs, "source": src,
                      "dashed": dashed, "phase": phase})

    def ent(name):
        return entered.get(name), "researcher" if name in entered else "missing"

    row = 0
    if counts.get("other"):
        others = [cnt(f"other:{d['key']}", d["label"], d["n"], "wider") for d in counts["other"]]
        add("other", 1, row, ["Identification of studies via other methods", "Records identified from:"]
            + [f"{d['label']} (n = {d['n']:,})" for d in counts["other"]] + [OTHER_NOTE],
            [cnt("identified_other", "Records identified via other methods", counts["identified_other"], "wider")]
            + others)
        row += 1
    reg_n, reg_src = ent("registers")
    db_lines = []
    dbs = [cnt("identified_databases", "Records identified from databases", counts["identified_databases"], "wider")]
    for d in counts["databases"]:
        name = _short(d["key"])
        multi = (platforms().get(d["key"]) or {}).get("multi_database")
        db_lines.append(f"{name} (n = {d['n']:,})" + (f"; {MULTI_NOTE}" if multi else ""))
        dbs.append(cnt(f"database:{d['key']}", name, d["n"], "wider"))
    add("identified", 0, row, ["Records identified from:", f"Databases (n = {counts['identified_databases']:,})"]
        + [f"  {ln}" for ln in db_lines] + [f"Registers (n = {_n(reg_n, reg_src)})"],
        dbs + [cnt("registers", "Records identified from registers", reg_n, reg_src)], source="wider",
        phase="Identification")
    auto_n, auto_src = ent("automation_removed")
    add("removed", 1, row, ["Records removed before screening:",
                            f"Duplicate records removed (n = {counts['duplicates_removed']:,})",
                            f"Records marked as ineligible by automation tools (n = {_n(auto_n, auto_src)})",
                            f"Records removed for other reasons (n = {counts['excluded_file_records']:,})"]
                           + ([f"Not yet checked for duplicates (n = {counts['not_checked']:,})"]
                              if counts.get("not_checked") else []),
        [cnt("duplicates_removed", "Duplicate records removed", counts["duplicates_removed"], "wider"),
         cnt("automation_removed", "Records removed by automation tools", auto_n, auto_src),
         cnt("excluded_file_records", "Records of files kept out of deduplication", counts["excluded_file_records"],
             "wider")], source="wider")
    row += 1
    sc = counts["screened"] if screened else None
    lines = [f"Records screened (n = {_n(sc, 'wider', screened)})"]
    if screened and counts["not_screened"]:
        lines.append(f"not screened yet: {counts['not_screened']:,}")
    add("screened", 0, row, lines, [cnt("screened", "Records screened", sc, "wider"),
                                    cnt("not_screened", "Records not yet screened",
                                        counts["not_screened"] if screened else None, "wider")], phase="Screening")
    ex = counts["excluded"] if screened else None
    lines = [f"Records excluded (n = {_n(ex, 'wider', screened)})"]
    cs = [cnt("excluded", "Records excluded", ex, "wider")]
    if screened and split:
        parts = [f"by the models (n = {split['models']:,})", f"by a researcher (n = {split['researcher']:,})"]
        if split.get("other"):
            parts.append(f"by another route (n = {split['other']:,})")
        lines.append("; ".join(parts))
        cs += [cnt(f"excluded_{k}", f"Records excluded {w}", split[k], "wider")
               for k, w in (("models", "by the models"), ("researcher", "by a researcher"), ("other", "by another route"))
               if k in split]
    add("excluded", 1, row, lines, cs)
    row += 1
    so = counts["sought"] if screened else None
    lines = [f"Reports sought for retrieval (n = {_n(so, 'wider', screened)})"]
    if screened and counts["maybe_ta"]:
        lines.append(f"includes {counts['maybe_ta']:,} unresolved maybe")
    add("sought", 0, row, lines, [cnt("sought", "Reports sought for retrieval", so, "wider"),
                                  cnt("maybe_ta", "Records marked maybe", counts["maybe_ta"] if screened else None,
                                      "wider")])
    nr_n, nr_src = ent("not_retrieved")
    add("not_retrieved", 1, row, [f"Reports not retrieved (n = {_n(nr_n, nr_src)})"],
        [cnt("not_retrieved", "Reports not retrieved", nr_n, nr_src)])
    row += 1
    assessed = so - nr_n if so is not None and nr_n is not None else None
    a_src = "derived" if assessed is not None else "missing"
    add("assessed", 0, row, [f"Reports assessed for eligibility (n = {_n(assessed, a_src)})"],
        [cnt("assessed", "Reports assessed for eligibility", assessed, a_src)])
    rows = list(excluded or [])
    gone = sum(x["n"] for x in rows) if rows else None
    g_src = "researcher" if rows else "missing"
    add("reports_excluded", 1, row, [f"Reports excluded (n = {_n(gone, g_src)})" + (":" if rows else "")]
        + [f"{x['reason']} (n = {x['n']:,}{DAGGER})" for x in rows],
        [cnt("reports_excluded", "Reports excluded", gone, g_src)]
        + [cnt(f"reports_excluded:{i}", x["reason"], x["n"], "researcher") for i, x in enumerate(rows, 1)])
    row += 1
    st_n, st_src = ent("studies")
    rp_n, rp_src = ent("reports")
    add("included", 0, row, [f"Studies included in review (n = {_n(st_n, st_src)})",
                             f"Reports of included studies (n = {_n(rp_n, rp_src)})"],
        [cnt("studies", "Studies included in review", st_n, st_src),
         cnt("reports", "Reports of included studies", rp_n, rp_src)],
        source="missing" if st_src == rp_src == "missing" else ("researcher" if "missing" not in (st_src, rp_src)
                                                                else "partial"), phase="Included")
    notes = ([counts["provisional_line"]] if counts.get("provisional_line") else []) + [NOTE_MODELS]
    if any(c["source"] in ("researcher", "derived") for b in boxes for c in b["counts"]):
        notes.append(NOTE_DERIVED if assessed is not None else NOTE_ENTERED)
    return _place(boxes, notes)


def _place(boxes: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """Wrap each box's lines to its column and give it x, y, width and height; rows take their tallest box."""
    for b in boxes:
        x, w = COLS[b["col"]]
        b["x"], b["w"] = x, w
        b["text"] = [ln for line in b["lines"] for ln in _wrap(line, (w - 2 * PAD) // 7)]
        b["h"] = 2 * PAD + STEP * len(b["text"]) - (STEP - FONT) + 4
    y = 10
    heads = []
    for r in range(max(b["row"] for b in boxes) + 1):
        in_row = [b for b in boxes if b["row"] == r]
        phase = next((b["phase"] for b in in_row if b.get("phase")), None)
        if phase:
            heads.append({"text": phase, "x": 10, "y": y + FONT})
            y += HEAD_H
        h = max(b["h"] for b in in_row)
        for b in in_row:
            b["y"] = y
            b["h"] = h
        y += h + GAP
    by = {b["id"]: b for b in boxes}
    arrows = []
    left = [b for b in boxes if b["col"] == 0]
    for a, b in zip(left, left[1:]):
        cx = a["x"] + a["w"] // 2
        arrows.append((cx, a["y"] + a["h"], cx, b["y"] - 2))
    for b in boxes:
        if b["col"] == 1 and b["id"] != "other":
            src = next((x for x in left if x["row"] == b["row"]), None)
            if src:
                cy = b["y"] + b["h"] // 2
                arrows.append((src["x"] + src["w"], cy, b["x"] - 2, cy))
    if "other" in by:
        o, rm = by["other"], by["removed"]
        cx = o["x"] + o["w"] // 2
        arrows.append((cx, o["y"] + o["h"], cx, rm["y"] - 2))
    note_y = y
    note_lines = [ln for n in notes for ln in _wrap(n, (WIDTH - 20) // 7)]
    height = note_y + len(note_lines) * STEP + 10
    return {"width": WIDTH, "height": height, "boxes": boxes, "arrows": arrows, "heads": heads,
            "notes": notes, "note_lines": note_lines, "note_y": note_y}


def flow_svg(layout: dict[str, Any]) -> str:
    """``prisma_2020_flow.svg`` in the editable-figures subset."""
    W, H = layout["width"], layout["height"]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           f'font-family="Arial" font-size="{FONT}">',
           '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">'
           '<path d="M 0 0 L 8 4 L 0 8 z" fill="#000000"/></marker></defs>']
    for h in layout["heads"]:
        out.append(f'<text x="{h["x"]}" y="{h["y"]}" font-weight="700" fill="#000000">{escape(h["text"])}</text>')
    for b in layout["boxes"]:
        fill = MISSING_FILL if b["source"] == "missing" else "#ffffff"
        dash = ' stroke-dasharray="6 4"' if b["dashed"] else ""
        out.append(f'<rect x="{b["x"]}" y="{b["y"]}" width="{b["w"]}" height="{b["h"]}" fill="{fill}" '
                   f'stroke="#000000" stroke-width="1"{dash}/>')
        for i, ln in enumerate(b["text"]):
            out.append(f'<text x="{b["x"] + PAD}" y="{b["y"] + PAD + FONT + i * STEP}" fill="#000000">'
                       f'{escape(ln)}</text>')
    for x1, y1, x2, y2 in layout["arrows"]:
        d = f"M {x1} {y1} V {y2}" if x1 == x2 else f"M {x1} {y1} H {x2}"
        out.append(f'<path d="{d}" stroke="#000000" stroke-width="1" fill="none" marker-end="url(#arrow)"/>')
    for i, ln in enumerate(layout["note_lines"]):
        out.append(f'<text x="10" y="{layout["note_y"] + FONT + i * STEP}" fill="#000000">{escape(ln)}</text>')
    return "\n".join(out + ["</svg>"]) + "\n"


def flow_json(layout: dict[str, Any], checks: list[str] = (), protocol: str | None = None,
              result_set: int | None = None) -> dict[str, Any]:
    """``prisma_2020_flow.json``: every box with its counts and their sources and the check lines (D21); no name."""
    return {"protocol": protocol, "result_set": result_set,
            "boxes": [{"id": b["id"], "lines": b["lines"], "source": b["source"], "dashed": b["dashed"],
                       "counts": b["counts"]} for b in layout["boxes"]],
            "checks": list(checks), "notes": layout["notes"],
            "provisional": next((n for n in layout["notes"] if n.startswith("Provisional:")), None)}
