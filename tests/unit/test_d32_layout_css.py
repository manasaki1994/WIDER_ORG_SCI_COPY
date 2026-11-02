"""D32 look-only rules that can be read from style.css: one page-title size, the one section-label style, the
Deduplication decide bar, the export tick boxes and the phone rules (WCAG 2.2, 2.5.8 Target size)."""
from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "wider" / "ui" / "static" / "style.css").read_text(encoding="utf-8")


def media_bodies(query: str) -> str:
    """The joined bodies of every @media block whose condition contains `query`."""
    out, i = [], 0
    while (m := re.compile(r"@media([^{]*)\{").search(CSS, i)):
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(CSS[j], 0)
            j += 1
        if query in m.group(1):
            out.append(CSS[m.end():j - 1])
        i = j
    return "\n".join(out)


def rules(selector_pattern: str, css: str = CSS) -> list[str]:
    return [m.group(2) for m in re.finditer(r"(^|[}\n])\s*([^{}]*\{[^{}]*\})", css)
            if re.search(selector_pattern, m.group(2).split("{")[0])]


def test_every_page_title_is_22px():
    # h1 is 22 px (DESIGN.md headline); no page sets its own h1 size, so Deduplication matches every other page
    assert re.search(r"(^|\n)h1\s*\{[^}]*font-size:\s*22px", CSS)
    sizes = [r for r in rules(r"\bh1\b") if "font-size" in r and not r.lstrip().startswith("h1 {")]
    assert sizes == [], sizes


def test_section_labels_take_the_one_h2_style():
    h2 = re.search(r"(^|\n)h2\s*\{([^}]*)\}", CSS).group(2)
    # T1 (owner walkthrough): sentence case, 17 px, black
    for part in ("font-size:17px", "color:var(--fg)"):
        assert part in h2.replace(" ", ""), part
    assert "uppercase" not in CSS
    # the interview step titles and the dedup list heading no longer set their own size or colour
    for sel in (r"\.interview \.step-head h2", r"\.dd-listcard > h2"):
        for r in rules(sel):
            assert "font-size" not in r and "color:" not in r, r


def test_dedup_decide_bar_is_solid_and_the_pair_keeps_room_for_it():
    bar = " ".join(rules(r"^\s*\.drawer\.docked \.dw-decide\s*$"))
    assert "position:sticky" in bar and "bottom:0" in bar and "background:var(--card)" in bar
    assert re.search(r"\.dd-pair[^{]*\{[^}]*scroll-padding-bottom:\s*77px", CSS)
    # the docked drawer does not inherit the overlay drawer's top offset, which pushed it over the history
    assert re.search(r"\.drawer\.docked\s*\{[^}]*\btop:0", CSS)


def test_export_tick_boxes_sit_4px_from_their_label_and_16px_apart():
    assert re.search(r"\[data-export-card\] label\.inline\s*\{[^}]*gap:4px", CSS)
    assert re.search(r"\[data-export-card\] fieldset\s*\{[^}]*gap:8px 16px", CSS)


def test_phone_rules_hide_the_journey_and_keep_24px_targets():
    phone = media_bodies("max-width: 639px")
    assert re.search(r"\.route[^{]*#route-show[^{]*\{[^}]*display:none", phone)
    assert re.search(r"button, select, summary[^{]*\{[^}]*min-height:24px", phone)
    assert re.search(r"button[^{]*\.chip-x[^{]*\{[^}]*min-width:24px", phone)
    # scrolling down slims the bar to its first row
    assert "body.tb-slim" in media_bodies("max-width: 700px")


def test_deduplication_top_bar_scrolls_away_on_phones():
    """Owner, 31 Oct (f-20261031-131750-1737): below 768 px the top bar on Deduplication scrolls with the page, the
    drawer and the pair panel then open over the whole height; on wider screens and other pages it stays fixed."""
    narrow = media_bodies("max-width: 767.98px")
    assert "body:has(#page-dedup:not([hidden])) .top { position:relative; }" in narrow
    # the docked pair (not an overlay drawer) keeps its own height: a 100vh pair overlapped its history (owner, 31 Oct)
    assert "body:has(#page-dedup:not([hidden])) .drawer:not(.docked) { top:0;" in narrow
    assert "body:has(#page-dedup:not([hidden])) .dd-panel { top:6px;" in narrow
    top = re.search(r"\n\.top \{[^}]*\}", CSS).group(0)
    assert "position:sticky; top:0;" in top
