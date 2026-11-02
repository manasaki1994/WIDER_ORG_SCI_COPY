"""Colour tokens in style.css pass WCAG 2.2 AA contrast in the light and the dark theme (design-pass-a1-dedup D2)."""
from __future__ import annotations

import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static" / "style.css"

TONES = ["blue", "violet", "stone", "rose", "cyan", "amber", "green"]   # amber: needs review (ui-redesign D5)


def _blocks(css: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return (light, dark) token sets. Light: every `:root` block outside any media query; dark: the one inside
    `@media (prefers-color-scheme: dark)`. Other media queries (narrow screens) hold sizes and are skipped."""
    light: dict[str, str] = {}
    dark: dict[str, str] = {}
    i = 0
    while True:
        m = re.compile(r"@media[^{]*\{|:root\s*\{").search(css, i)
        if not m:
            break
        if m.group(0).startswith("@media"):
            depth, j = 1, m.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(css[j], 0)
                j += 1
            if "prefers-color-scheme: dark" in m.group(0):
                for inner in re.finditer(r":root\s*\{([^}]*)\}", css[m.end():j]):
                    dark.update(_tokens(inner.group(1)))
            i = j
        else:
            end = css.index("}", m.end())
            light.update(_tokens(css[m.end():end]))
            i = end
    return light, dark


def _tokens(body: str) -> dict[str, str]:
    return {k: v.lower() for k, v in re.findall(r"--([\w-]+)\s*:\s*(#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})\b", body)}


def _lum(hex_: str) -> float:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chans = []
    for k in range(3):
        c = int(h[2 * k:2 * k + 2], 16) / 255
        chans.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = chans
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _pairs():
    """(foreground token, background token, minimum ratio)."""
    out = [(t, f"{t}-bg", 4.5) for t in TONES]
    out += [("blue", "bg", 4.5), ("blue", "card", 4.5), ("fg", "bg", 4.5), ("fg", "card", 4.5),
            ("on-blue", "blue-fill", 4.5), ("amber", "bg", 4.5), ("amber", "card", 4.5)]
    out += [(t, s, 3.0) for t in ("field-line", "focus") for s in ("bg", "card")]
    return out


def test_ratio_matches_wcag_examples():
    assert round(ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(ratio("#777777", "#ffffff"), 2) == 4.48


def test_contrast_of_every_pair():
    light, dark = _blocks(CSS.read_text(encoding="utf-8"))
    failures = []
    for name, tokens in (("light", light), ("dark", dark)):
        for fg, bg, need in _pairs():
            if fg not in tokens or bg not in tokens:
                failures.append(f"{name}: --{fg} or --{bg} missing")
                continue
            r = ratio(tokens[fg], tokens[bg])
            if r < need:
                failures.append(f"{name}: --{fg} {tokens[fg]} on --{bg} {tokens[bg]} is {r:.2f}:1, needs {need}:1")
    assert not failures, "\n".join(failures)


def test_three_root_blocks():
    css = CSS.read_text(encoding="utf-8")
    assert len(re.findall(r":root\s*\{", css)) == 3


def test_design_md_tokens_match_the_css():
    """DESIGN.md's front matter lists the light tone tokens style.css ships, amber ("needs review") included."""
    light, _ = _blocks(CSS.read_text(encoding="utf-8"))
    design = (CSS.parents[3] / "DESIGN.md").read_text(encoding="utf-8")
    front = design.split("---")[1]
    for t in TONES:
        assert f'  {t}: "{light[t]}"' in front, t
    assert f'  amber-wash: "{light["amber-bg"]}"' in front
    assert '"needs review"' in design and "diamond" in design


def _rule(css: str, selector: str) -> str:
    """The body of the first rule whose selector list is exactly ``selector``."""
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return re.sub(r"\s+", "", m.group(1))


def test_secondary_text_contrast():
    """Audit 5.6: graphite (secondary text) reads at 4.5:1 on every surface it sits on, the tinted ones too (the
    stone wash of counts and keys, the blue wash, the line grey behind code), in light and dark."""
    light, dark = _blocks(CSS.read_text(encoding="utf-8"))
    for name, tokens in (("light", light), ("dark", dark)):
        for bg in ("bg", "card", "stone-bg", "blue-bg", "line"):
            assert ratio(tokens["muted"], tokens[bg]) >= 4.5, (name, bg, round(ratio(tokens["muted"], tokens[bg]), 2))


def test_audit_56_layout_rules():
    """Audit 5.6 (impeccable-findings.md): the record drawer starts under the top bar (whose height shell.js
    measures), the top bar wraps instead of drawing over itself, `btn quiet` buttons are quiet (not a second blue
    main button), the journey line scrolls inside its own box below 860 px so it never runs under Hide, warning
    badges and the "Showing" pill use theme tokens, a warning line under a page heading is normal size in the text
    colour, and an Overview item keeps its words on one text line."""
    css = CSS.read_text(encoding="utf-8")
    drawer = _rule(css, ".drawer")
    assert "top:var(--top-h)" in drawer and "height:calc(100vh-var(--top-h))" in drawer
    top = _rule(css, ".top")
    assert "flex-wrap:wrap" in top
    # --top-h is the bar's measured height, so the bar must not take its own height as a minimum (it could never
    # shrink back after a taller page), and an empty actions box must not take a wrapped line of its own
    assert "var(--top-h)" not in top and "min-height:50px" in top
    assert re.search(r"\.tb-actions:not\(:has\(> :not\(:empty\)\)\)\s*\{\s*display:none", css)
    assert re.search(r"\.btn\.quiet\s*[,{]", css), "no rule for the btn quiet class"
    quiet = re.search(r"[^}]*\.btn\.quiet[^{]*\{([^}]*)\}", css).group(1)
    assert "background:var(--card)" in quiet
    narrow = css[css.index("@media (max-width: 860px) { .route"):]
    narrow = narrow[:narrow.index("\n")]
    # the strip scrolls and holds the visually hidden .tl labels, which would otherwise widen the page on a phone
    assert ".route .jl { overflow-x:auto; position:relative; grid-template-columns:none; grid-auto-columns:max-content; }" in narrow
    assert ".route { overflow-x:auto; }" not in narrow
    assert "var(--amber)" in _rule(css, ".badge.warn") and "#9a6a00" not in css
    showing = _rule(css, ".dd-showing")
    assert "color:var(--on-blue)" in showing and "background:var(--blue-fill)" in showing
    warn = _rule(css, ".stage-result.prev .prev-line.tone-warning")
    assert "color:var(--fg)" in warn and "font-size:15px" in warn
    assert "inline-flex" not in _rule(css, ".ov-item > span:first-child")
    assert "top:calc(var(--top-h)+6px)" in _rule(css, ".dd-panel"), "Deduplication guide/notebook panel under the bar"
    # a small button beside a labelled field lines up with the field, not with the label's margin box
    assert "margin-bottom:0" in _rule(css, ".row > label")
    assert re.search(r"\.row:has\(> label > input[^{]*\)\s*\{\s*align-items:flex-end;", css)
    assert re.search(r"\.row:has\(> label[^{]*\) > \.small-btn\s*\{\s*min-height:30px;", css)


def test_send_note_is_a_small_blue_box():
    """Owner, 29 Oct (5.6 chat): the "what this sends" note under a button (D17) is smaller and sits in a light blue
    box, in the text colour (important text is not grey)."""
    css = CSS.read_text(encoding="utf-8")
    note = _rule(css, ".send-note:not([hidden])")
    assert "background:var(--blue-bg)" in note and "font-size:13px" in note and "color:var(--fg)" in note
    assert "display:block" in note


def test_guide_head_is_plain_with_a_blue_edge_and_the_lead_box_reads_like_the_rest():
    """Owner, 29 Oct 2026: the guide head is the panel's own colour with the book icon and a rule (no dark band, no
    Hide button: Guide in the top bar closes it); a blue edge marks the panel; the text was too big."""
    css = CSS.read_text(encoding="utf-8")
    head = _rule(css, ".guide-panel .guide-head")
    assert "background:var(--card)" in head and "position:sticky" in head and "border-bottom:1pxsolidvar(--line)" in head
    assert "--chrome" not in head
    assert "inset3px00var(--blue)" in _rule(css, ".guide-panel")
    # later the same day: smaller type (13.5 px) and space instead of rules between the parts
    for sel in (".guide-panel dd", ".guide-panel .glead dd"):
        assert "font-size:13.5px" in _rule(css, sel), sel
    assert "border" not in _rule(css, ".guide-panel .gp")
    assert "font-weight:400" in _rule(css, ".guide-panel .glead dd")


def test_side_shadow_on_important_boxes():
    """Owner, 29 Oct 2026: important boxes cast a shadow to their left side, in both themes; a card inside a card
    casts none, so nested boxes do not stack shadows."""
    css = CSS.read_text(encoding="utf-8")
    light = re.search(r"--shadow-side:(-\d+px [^;]+);", css).group(1)
    assert light.startswith("-") and css.count("--shadow-side:") == 2   # light and dark
    for sel in (".page > .card, .page > .grid > .card, .can-do", ".rk-steps > .card", ".settings-body > .card", ".bk-card"):
        assert "box-shadow:var(--shadow-side)" in _rule(css, sel), sel
    assert "box-shadow:none" in _rule(css, ".card .card")


def test_ui_redesign_leftovers_home_card_and_touch_targets():
    """5.6 leftovers: the Home card's "needs you" edge is a real 4 px left border, its buttons are 30 px, checkboxes
    are 20 px on a touch screen, and Explain on Deduplication is centred on the 32 px h1 line."""
    css = CSS.read_text(encoding="utf-8")
    assert "border-left:4pxsolidvar(--rose)" in _rule(css, ".project-card.s-needs")
    assert "min-height:30px" in _rule(css, ".project-card .small-btn")
    assert re.search(r"@media \(pointer: coarse\) \{ input\[type=checkbox\], input\[type=radio\] \{ width:20px; height:20px;", css)
    assert "margin-top:4px" in _rule(css, ".dd-head > .small-btn")
    # a scrolling table holds its absolutely placed .sr-only text, else that text widens the page at 375 px
    assert "#sc-runs { display:block; overflow-x:auto; position:relative; }" in css


def test_home_demo_play_icon_opens_the_demo():
    """Owner, 29 Oct: the play icon on Home's demo card did nothing. It now opens the same page as the button, in a new
    tab, and stays out of the tab order and the screen reader (the button carries the name)."""
    html = (Path(__file__).resolve().parents[2] / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    url = 'href="https://wider-showcase.vercel.app/demo/" target="_blank" rel="noopener noreferrer"'
    assert f'<a class="demo-play" {url} tabindex="-1" aria-hidden="true"><svg class="icon"><use href="#i-play"/></svg></a>' in html
    assert f'<a class="btn" id="home-demo-link" {url}' in html


def test_journey_line_never_runs_under_its_hide_button():
    """Owner, 30 Oct: with 14 steps the journey line needs about 1,500 px; at laptop widths its last section ran under
    "Hide journey line". The sections now share the width by their number of steps, the line scrolls inside itself if
    it still does not fit, and below 1,700 px the button reads "Hide" (its aria-label keeps the full name)."""
    css = CSS.read_text(encoding="utf-8")
    assert "grid-template-columns:var(--cols)" in _rule(css, ".route .jl") and "min-width:min-content" in _rule(css, ".route .jseg")
    assert "overflow-x:auto" in _rule(css, ".route .jl") and "min-width:min-content" in _rule(css, ".route .jst")
    assert "white-space:nowrap" not in _rule(css, ".route .jseg .ln")
    # owner, 31 Oct: a stage title that wraps to two lines pushed that stage's line lower than the others; the titles
    # now share one grid row (subgrid), so every stage's line starts at the same height
    assert "grid-template-rows:autoauto" in _rule(css, ".route .jl") and "grid-auto-flow:column" in _rule(css, ".route .jl")
    assert "grid-row:span2" in _rule(css, ".route .jseg") and "grid-template-rows:subgrid" in _rule(css, ".route .jseg")
    assert "@media (max-width: 1700px) { .route .rt { position:relative; } .route .tl { position:absolute;" in css
