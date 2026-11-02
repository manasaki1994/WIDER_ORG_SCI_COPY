"""D32 F15 and F20 (UX review of 30 Oct): one filled (main) button per page, the button tiers (outline, text link,
"More"), "Chosen" as a chip and "Continue to <next step>" ending the can-do box of a done step. Static reads of
index.html, the page scripts and style.css, plus pickers.js and shell.js under Node on the stand-in page."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.unit.test_pickers_js_node import ROWS, cards, pickers
from tests.unit.test_shell_js_node import shell_page, stages

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

# classes whose buttons are not filled (style.css): outline, quiet, text link, icon and chrome buttons
NOT_FILLED = {"secondary", "btn-quiet", "quiet", "link-btn", "icon-btn", "chip-x", "tb-btn", "tb-link", "link", "dd-ghost",
              "explain-btn", "bk-add", "dd-box", "qw-reply", "qw-send", "qw-pill", "qw-expand", "qw-icon",
              "dbtn",   # a decision button: white with a coloured left edge, filled only as a pale tint when pressed
              "fx-leave",   # Leave on a focus page's dark bar: an outline in the bar's light colour (focus.css)
              "en-g"}   # Missing abstracts: a group row of the records side list (plain row, pressed shows a tint)
# filled buttons in index.html that sit in a dialog over the page, not on the page itself
DIALOG_IDS = {"rv-qc-go"}


def page_sections() -> dict[str, str]:
    """The markup of each `<section id="page-...">`, up to the next page section (pages hold nested sections)."""
    starts = [(m.start(), m.group(1)) for m in re.finditer(r'<section id="page-([\w-]+)"', INDEX)]
    ends = [s for s, _ in starts[1:]] + [INDEX.index("</main>") if "</main>" in INDEX else len(INDEX)]
    return {key: INDEX[s:e] for (s, key), e in zip(starts, ends)}


def filled(markup: str) -> list[str]:
    """The id (or label) of every filled button and a.btn.primary link in the markup."""
    markup = re.sub(r'<nav class="settings-nav".*?</nav>', "", markup, flags=re.S)
    out = []
    for b in re.finditer(r"<button\b([^>]*)>(.*?)</button>", markup, re.S):
        attrs = b.group(1)
        cls = set((re.search(r'class="([^"]*)"', attrs) or [None, ""])[1].split())
        if cls & NOT_FILLED or 'role="tab"' in attrs:
            continue
        bid = (re.search(r'id="([^"]*)"', attrs) or [None, ""])[1]
        if bid in DIALOG_IDS:
            continue
        out.append(bid or re.sub(r"<[^>]+>", "", b.group(2)).strip())
    out += [f"a:{t}" for t in re.findall(r'<a class="btn primary"[^>]*>(.*?)</a>', markup)]
    return out


def test_at_most_one_filled_button_per_page_markup():
    """Every page's static markup has at most one filled button; the home page's "New project" turns into an outline
    while its form (with "Create and open") is open, so one shows at a time."""
    pages = page_sections()
    assert {"overview", "setup", "construct", "query", "import", "screen", "recall", "rank", "settings"} <= set(pages)
    over = {k: f for k, f in ((k, filled(v)) for k, v in pages.items()) if len(f) > 1}
    assert over == {"home": ["home-create-toggle", "hc-create"]}, over
    assert re.search(r'#home-create-toggle\[aria-expanded="true"\]\s*\{[^}]*background:transparent', CSS)
    assert filled(pages["settings"]) == []
    # the step's main action is marked, so a done step turns it into an outline (the box's Continue is then the one)
    for bid in ("save", "art-save", "q-import-save", "en-run", "rk-run", "rc-run"):
        assert re.search(rf'<button id="{bid}"[^>]*class="main-act"|<button[^>]*class="main-act"[^>]*id="{bid}"', INDEX), bid
    assert re.search(r"\.page\.step-done button\.main-act\s*\{[^}]*background:transparent", CSS)


# Filled buttons drawn by the page scripts, each with where it shows. A dialog's buttons (data-act=..., askChoice)
# are not on a page. A new filled template must be added here with the reason it is the page's only one.
SCRIPT_FILLED = {
    ("app.js", 'class="${cls}" data-sc-go='): "Screening: the Next step button (sc-next-btn main-act); the rest secondary",
    ("model.js", 'id="model-use"'): "Model: Use <name> for query creation, once a card is picked (Find is then an outline)",
    ("questions.js", "data-save"): "Questions: Save answer on each question card (left as is, see the report)",
    ("results.js", "data-export-go"): "Results: Export (main-act)",
    ("app.js", 'id="en-apply-btn"'): "Missing abstracts: Apply changes, shown only while Fetch is off waiting for it",
    ("common.js", 'id="home-name-save"'): "Home: Save of the name asked once (D33)",
    ("shell.js", 'id="tb-${slot}-btn"'): "top bar: the main slot of Deduplication and PRISMA reporting; Notebook and Guide are tb-btn",
    ("shell.js", "data-more="): "top bar More menu items (.tb-more-wrap li button, not filled)",
    ("shell.js", 'id="sw-btn"'): "top bar project switcher (.switch > button, dark chrome, not filled)",
}


def test_filled_buttons_in_page_scripts_are_listed():
    found = set()
    for f in sorted(STATIC.glob("*.js")):
        for tag in re.findall(r"<button\b[^>]*>", f.read_text(encoding="utf-8")):
            cls = (re.search(r'class="((?:[^"$]|\$\{[^}]*\})*)"', tag) or [None, ""])[1]
            if set(re.sub(r"\$\{[^}]*\}", " ", cls).split()) & NOT_FILLED:
                continue
            if re.search(r'\$\{[^}]*"secondary', cls):
                continue   # an outline filled only as a state (the drawer's current decision)
            if "data-act=" in tag or 'role="tab"' in tag:
                continue   # a dialog's buttons, or a tab
            key = next((k for k in SCRIPT_FILLED if k[0] == f.name and k[1] in tag), None)
            assert key, f"{f.name}: a new filled button {tag[:120]}"
            found.add(key)
    assert found == set(SCRIPT_FILLED)


def test_chosen_is_a_chip_not_a_button():
    """The chosen card shows "Chosen" as a state chip (a span the keyboard can be put on), the others a Choose
    outline button; nothing is aria-pressed any more."""
    r = pickers(f"console.log(JSON.stringify(Pickers.cardsHtml({json.dumps(ROWS)}, {{kind: 'llm', chosen: 'b'}})));")
    c = cards(r)
    assert re.search(r'<span class="chip chip-chosen" data-chosen="b" tabindex="-1" aria-label="Chosen model b">Chosen</span>', c["b"])
    assert "<button" not in c["b"].split('class="row"')[1].split("</div>")[0]
    for n in ("a", "c"):
        assert f'<button type="button" class="secondary small-btn" data-pick="{n}" aria-label="Choose model {n}">Choose</button>' in c[n]
    assert "aria-pressed" not in r
    assert re.search(r"\.chip\.chip-chosen\s*\{", CSS)


def more(details_id: str) -> str:
    m = re.search(rf'<details class="more-acts" id="{details_id}"><summary>More</summary>(.*?)</details>', INDEX, re.S)
    assert m, details_id
    return m.group(1)


def test_rare_actions_under_more():
    """Concepts and Search results put their rare actions, as text links, under "More"; the Recall headers' rare
    action is a text link; the step's own actions are outline buttons."""
    adj, sn, cov = more("adj-more"), more("sn-more"), more("prior-cov-more")
    for box, ids in ((adj, ("adj-seed", "adj-propose", "adj-find")), (sn, ("sn-mine-plain", "sn-mine")),
                     (cov, ("prior-cov-plat", "prior-cov-corpus"))):
        for bid in ids:
            assert re.search(rf'<button[^>]*class="link-btn"[^>]*id="{bid}"|<button[^>]*id="{bid}"[^>]*class="link-btn"', box), bid
    for bid in ("adj-add", "sn-extract"):
        assert re.search(rf'<button[^>]*id="{bid}"[^>]*class="secondary|<button[^>]*class="secondary[^"]*"[^>]*id="{bid}"', INDEX), bid
    for bid in ("out-prisma", "out-prisma-s", "out-prisma-2020", "imp-check"):
        assert re.search(rf'<button[^>]*(id="{bid}"[^>]*class="link-btn"|class="link-btn"[^>]*id="{bid}")', INDEX), bid
    assert re.search(r"button\.link-btn\s*\{[^}]*background:none[^}]*text-decoration:underline", CSS)
    assert re.search(r"button\.secondary:not\(\.small-btn\)\s*\{\s*font-size:14px", CSS)


def test_done_step_ends_its_box_with_continue():
    """A done step's can-do box ends with "Continue to <next step>" (the page's one filled button) and the page is
    marked step-done, which turns its own main button into an outline; a step to work on gets neither. A chosen model
    continues to the first step. The last step has no Continue, so its own main button stays filled."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "enrich": "done", "dedup": "ready"})
    last = [r["key"] for r in rows if r["state"] != "extension"][-1]
    for r in rows:
        if r["key"] == last:
            r["state"] = "done"
    g = lambda page, c: {"page": page, "title": page, "can_do": c, "notices": [], "parts": []}  # noqa: E731
    body = r"""
const mk = (k) => { const s = new Set(); $("#page-" + k).classList = { toggle(c, on) { on ? s.add(c) : s.delete(c); }, s }; };
["enrich", "dedup", "model", "LAST"].forEach(mk);
const r = {}, box = (k) => ({ cls: $("#can-do-" + k).className, html: $("#can-do-" + k).innerHTML, done: $("#page-" + k).classList.s.has("step-done") });
markCurrentPage("enrich"); await flush(); r.enrich = box("enrich");
markCurrentPage("dedup"); await flush(); r.dedup = box("dedup");
markCurrentPage("model"); await flush(); r.modelOpen = box("model");
CONSTRUCT_BACKEND = "fake";
markCurrentPage("model"); await flush(); r.modelSet = box("model");
markCurrentPage("LAST"); await flush(); r.last = box("LAST");
console.log(JSON.stringify(r));""".replace("LAST", last)
    out = shell_page(body, rows, "dedup", {"/api/guide/enrich": g("enrich", "Completed, and you can still change it."),
                                           "/api/guide/dedup": g("dedup", "Work here."),
                                           "/api/guide/model": g("model", "Choose the model."),
                                           f"/api/guide/{last}": g(last, "Completed, and you can still change it.")})
    titles = {r["key"]: r["title"] for r in rows}
    avail = [r["key"] for r in rows if r["state"] != "extension"]
    nxt = avail[avail.index("enrich") + 1]
    assert out["enrich"]["cls"] == "can-do can-do-done" and out["enrich"]["done"] is True
    html = out["enrich"]["html"]
    assert html.endswith(f'<p class="can-do-main"><a class="btn primary" href="#{nxt}">Continue to {titles[nxt]}</a></p>')
    assert html.count('class="btn primary"') == 1
    assert "btn primary" not in out["dedup"]["html"] and out["dedup"]["done"] is False
    assert "btn primary" not in out["modelOpen"]["html"] and out["modelOpen"]["done"] is False
    assert out["modelSet"]["cls"] == "can-do can-do-done" and out["modelSet"]["done"] is True
    assert f'<a class="btn primary" href="#{avail[0]}">Continue to {titles[avail[0]]}</a>' in out["modelSet"]["html"]
    assert out["last"]["cls"] == "can-do can-do-done" and out["last"]["done"] is False
    assert "btn primary" not in out["last"]["html"]
