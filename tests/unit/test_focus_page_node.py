"""label-focus-pages: Label records and Adjudication open the record drawer over the whole window (focus.js). The real
drawer.js and focus.js run on the small DOM of a11y_dom with index.html's drawer; records.js's settings run on the
stand-in page of test_ui_followup_r31 with Focus recorded. Synthetic names (ana, lena, tomas, mara) only."""
import json
import re

from tests.unit.a11y_dom import STATIC, run_dom, static
from tests.unit.test_drawer_keys_node import run_node
from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name

RECORDS = static("records.js")


def _drawer_html() -> str:
    html = static("index.html")
    a = html.index('<aside id="drawer"')
    return html[a:html.index("</aside>", a) + 8]


PRE = """
  const key = (k, o = {}) => { const ev = new KeyboardEvent("keydown", { key: k, ...o }); (document.activeElement || document.body).dispatchEvent(ev); return ev.defaultPrevented; };
  const items = [1, 2, 3, 4, 5].map((id) => ({ id, title: "Synthetic tillage record " + id, abstract: "Synthetic abstract.", decision: id <= 2 ? "include" : "" }));
  const saved = [];
  const labelled = () => items.filter((x) => x.decision).length;
  const FULL = { title: "Label records", leave: "Leave labelling", barLabel: "Labelled", signing: "signing as <b>ana</b>",
    progress: (r) => ({ text: `Record ${items.indexOf(r) + 1} of 5 · you have labelled ${labelled()} of 5`, done: labelled(), total: 5 }),
    note: "You see only the record and your criteria.", aside: () => "<h2>Your criteria</h2><p class=\\"fx-inc\\">Include when</p>" };
  const CFG = { label: "Labelling queue (blind)", table: "#queue", items, index: 2, notes: false, blind: true,
    render: async (r, u) => u.recordBlock(r) + '<div class="dw-controls dw-after" id="dw-controls"><input id="dw-labnote"></div>',
    current: (r) => r.decision,
    decisions: ["include", "exclude", "maybe"].map((d) => ({ value: d, label: d[0].toUpperCase() + d.slice(1), tone: d,
      fn: async (r) => { saved.push([r.id, d]); r.decision = d; } })) };
  const order = () => $("#drawer").children.map((c) => c.id || c.className);
"""
HTML = '<table><tbody id="queue"><tr data-idx="0" tabindex="0"><td>x</td></tr></tbody></table>'


def test_label_focus_page_layout_and_save():
    """The labelling queue fills the window: the dark bar names the page and who signs, with Leave; the heading, the
    queue's own count with its bar and the blinding note; the decisions under the abstract in the record card with the
    fields under them and the criteria beside it. Key 1 saves, marks Include as chosen and moves the count on; Esc
    leaves and the drawer's parts are back where index.html has them."""
    r = run_dom("""
      const before = order();
      Focus.open({ ...CFG, full: FULL }); for (let i = 0; i < 4; i++) await flush();
      const d = $("#drawer"), card = $("#fx-card");
      const res = { full: d.classList.contains("full"), bodyFull: document.body.classList.contains("drawer-full"),
        bar: [$("#fx-brand").textContent, $("#fx-where").textContent, $("#fx-sign").innerHTML, $("#fx-leave").textContent],
        h1: $("#fx-top h1").textContent, progress: $("#fx-progress").textContent, now: $("#fx-bar").getAttribute("aria-valuenow"),
        note: $("#fx-note").textContent, crit: $("#fx-crit").textContent, critHidden: $("#fx-crit").hidden,
        inCard: [!!card.querySelector("#dw-body"), !!card.querySelector("#dw-decide"), !!$("#fx-after #dw-controls")],
        tones: $$("#dw-decide [data-dec]").map((b) => [b.dataset.tone, b.getAttribute("aria-pressed"), b.classList.contains("fx-dec")]) };
      $("#dw-labnote").blur && $("#dw-labnote").blur(); document.activeElement = document.body;
      key("1"); for (let i = 0; i < 4; i++) await flush();
      res.saved = saved; res.pressed = $$("#dw-decide [data-dec]").map((b) => b.getAttribute("aria-pressed"));
      await new Promise((ok) => setTimeout(ok, 260)); for (let i = 0; i < 4; i++) await flush();   // the drawer moves on after 200 ms
      res.afterSave = [$("#fx-progress").textContent, $("#fx-bar").getAttribute("aria-valuenow"),
        $$("#dw-decide [data-dec]").map((b) => b.getAttribute("aria-pressed"))];
      key("Escape"); await flush();
      res.closed = [d.hidden, d.classList.contains("full"), document.body.classList.contains("drawer-full"), !!$("#fx-top"), !!$("#fx-brand")];
      res.order = [before, order()];
      out(res);
    """, html=HTML + _drawer_html(), scripts=("common.js", "drawer.js", "focus.js"), pre=PRE)
    assert r["full"] and r["bodyFull"]
    assert r["bar"] == ["WIDER", "Label records", "signing as <b>ana</b>", "Leave labelling Esc"]
    assert r["h1"] == "Label records"
    assert r["progress"] == "Record 3 of 5 · you have labelled 2 of 5" and r["now"] == "2"
    assert r["note"].strip() == "You see only the record and your criteria."
    assert "Include when" in r["crit"] and not r["critHidden"]
    assert r["inCard"] == [True, True, True]
    assert r["tones"] == [["include", "false", True], ["exclude", "false", True], ["maybe", "false", True]]
    assert r["saved"] == [[3, "include"]] and r["pressed"] == ["true", "false", "false"]
    # the queue moved on to record 4 after the save; the count says 3 of 5 now
    assert r["afterSave"][0] == "Record 4 of 5 · you have labelled 3 of 5" and r["afterSave"][1] == "3" and r["afterSave"][2] == ["false"] * 3
    assert r["closed"] == [True, False, False, False, False]
    assert r["order"][0] == r["order"][1]


def test_other_drawers_not_full():
    """Only the labelling queue and adjudication use Focus; a plain drawer opened after a focus page was left is the
    side panel again."""
    r = run_dom("""
      Focus.open({ ...CFG, full: FULL }); for (let i = 0; i < 4; i++) await flush();
      key("Escape"); await flush();
      Drawer.open({ ...CFG }); for (let i = 0; i < 4; i++) await flush();
      out({ open: !$("#drawer").hidden, full: $("#drawer").classList.contains("full"), top: !!$("#fx-top"), focus: Focus.isOpen() });
    """, html=HTML + _drawer_html(), scripts=("common.js", "drawer.js", "focus.js"), pre=PRE)
    assert r == {"open": True, "full": False, "top": False, "focus": False}
    users = sorted(f.name for f in STATIC.glob("*.js") if f.name != "focus.js" and "Focus." in f.read_text(encoding="utf-8"))
    assert users == ["records.js"]
    assert RECORDS.count("(window.Focus || Drawer).open(") == 2


CODEBOOK = {"elements": [{"id": "e1", "name": "field study", "rule": "on cropland"}],
            "exclusion_reasons": [{"code": "E3", "text": "No organic carbon outcome below 30 cm"}],
            "labels": [{"group": "Study design", "values": ["Field experiment", "Survey"]}]}

LB = r"""
global.lbCodebook = CB; global.lbCodebookVersion = 2;
global.Focus = { open: (cfg) => opened.push(cfg), isOpen: () => false };
const rows = [
  { record_id: 1, sample_id: "s", title: "a decided record", decision: "include", locked: false },
  { record_id: 2, sample_id: "s", title: "an open record", decision: "", locked: false },
  { record_id: 3, sample_id: "s", title: "an excluded record", decision: "exclude", reason: "E3", locked: false } ];
apiReply = (p) => p.startsWith("/api/labels/queue") ? rows.map((r) => ({ ...r })) : p.startsWith("/api/labels") ? { change_id: 7 } : {};
$("#lb-filter").value = "all";
$("#lb-load").onclick(); await flush(); await flush();
const cfg = opened[0];
const u = { recordBlock: (r) => "<h2>" + r.title + "</h2>" };
"""


def test_reason_field_only_with_exclude():
    """The exclusion reason is hidden until Exclude is chosen: a first Exclude shows it and asks once in red (the
    drawer shows a thrown message in red), nothing is saved; Exclude again saves without a reason. A record already
    excluded shows its reason."""
    out = run_node(QUEUE_PAGE + queue_name("ana") + f"const CB = {json.dumps(CODEBOOK)};\n" + RECORDS + LB + r"""
const res = {};
res.open = await cfg.render(cfg.items[1], u);
res.excluded = await cfg.render(cfg.items[2], u);
els["#dw-reason-row"] = { ...$("#dw-reason-row"), hidden: true };
const body = { querySelectorAll: () => [], querySelector: () => ({ value: "" }) };
try { await cfg.decisions[1].fn(cfg.items[1], body); res.first = "saved"; } catch (e) { res.first = e.message; }
res.shown = !$("#dw-reason-row").hidden;
res.postsAfterFirst = calls.filter(([p, b]) => p === "/api/labels" && b).length;
await cfg.decisions[1].fn(cfg.items[1], body);
res.posted = calls.filter(([p, b]) => p === "/api/labels" && b).map(([, b]) => [b.record_id, b.decision, b.reason]);
console.log(JSON.stringify(res));
""")
    row = lambda html: re.search(r'<label[^>]*id="dw-reason-row"[^>]*>', html).group(0)  # noqa: E731
    assert "hidden" in row(out["open"]) and "hidden" not in row(out["excluded"])
    assert out["first"] == ("Choose an exclusion reason: picking one (a click, or Enter on the list) saves the exclude and moves on. "
                            "Or press Exclude again to save it without a reason.")
    # the instruction (the drawer's decision line) is read with the field (owner fixes m3)
    assert 'id="dw-reason" aria-describedby="dw-dec-msg"' in out["open"]
    assert out["shown"] and out["postsAfterFirst"] == 0
    assert out["posted"] == [[2, "exclude", ""]]
    assert "Study design" in out["open"] and "Note saved with your label (optional)" in out["open"]


def test_label_save_reads_fields_under_the_buttons():
    """On the focus page the fields saved with a label sit in #fx-after, under the decision buttons (moved there by
    focus.js): Exclude with reason E3, a label group and a note posts the same label body as the side drawer did, read
    from that box and not from the record body."""
    out = run_node(QUEUE_PAGE + queue_name("ana") + f"const CB = {json.dumps(CODEBOOK)};\n" + RECORDS + LB + r"""
Focus.isOpen = () => true; window.Focus = Focus;   // in a browser window.Focus is this global
const sel = (v) => ({ value: v });
els["#fx-after"] = { querySelectorAll: () => [{ value: "Field experiment", dataset: { label: "Study design" } }, { value: "", dataset: { label: "Unused" } }],
  querySelector: (s) => s === "#dw-reason" ? sel("E3") : s === "#dw-labnote" ? sel("deep cores only") : null };
els["#dw-reason-row"] = { ...$("#dw-reason-row"), hidden: false };
const body = { querySelectorAll: () => [], querySelector: () => sel("") };   // the record body: empty, so it must not be read
await cfg.decisions[1].fn(cfg.items[1], body);
console.log(JSON.stringify(calls.filter(([p, b]) => p === "/api/labels" && b).map(([, b]) => b)));
""")
    assert out == [{"sample_id": "s", "record_id": 2, "reviewer": "ana", "decision": "exclude", "reason": "E3",
                    "labels": {"Study design": "Field experiment"}, "note": "deep cores only"}]


def test_reason_by_keyboard_saves_only_on_enter():
    """After the first Exclude, arrow keys over the reasons fire "change" at every step: they only move the choice;
    Enter saves the reason the list shows, a moment later (a list opened on a Mac takes its value after the key), and
    only once, with one click even when the list's change comes too. A reason picked with the mouse saves at once (risk-checker R1; owner fixes 1 Nov 2026)."""
    out = run_node(QUEUE_PAGE + queue_name("ana") + f"const CB = {json.dumps(CODEBOOK)};\n" + RECORDS + LB + r"""
const posts = () => calls.filter(([p, b]) => p === "/api/labels" && b).map(([, b]) => [b.record_id, b.reason]);
let clicks = 0, slow = 0;
const sel = $("#dw-reason"), excl = { click: () => { clicks++; return new Promise((ok) => setTimeout(ok, slow)).then(() => cfg.decisions[1].fn(cur, body)).catch(() => {}); } };   // a slow save
els['#dw-decide [data-dec="1"]'] = excl;
els["#dw-reason-row"] = { ...$("#dw-reason-row"), hidden: true };
const body = { querySelectorAll: () => [], querySelector: (s) => s === "#dw-reason" ? sel : { value: "" } };
let cur = cfg.items[1];
const res = {};
cfg.afterRender();
try { await cfg.decisions[1].fn(cur, body); } catch (e) {}
sel.value = "E1"; sel.onkeydown({ key: "ArrowDown", preventDefault() {} }); sel.onchange(); await flush();
sel.value = "E3"; sel.onkeydown({ key: "ArrowDown", preventDefault() {} }); sel.onchange(); await flush();
res.afterArrows = posts();
sel.onkeydown({ key: "Enter", preventDefault() {} });
res.beforeTimer = posts();   // the value an open list commits after the key is read, not the one before it
sel.value = "E2"; sel.onchange();   // a Mac list commits its value with a change: saved once, with that reason
await new Promise((ok) => setTimeout(ok, 5)); await flush(); await flush();
res.afterEnter = posts();
cur = cfg.items[0]; cfg.afterRender(); els["#dw-reason-row"].hidden = true;
try { await cfg.decisions[1].fn(cur, body); } catch (e) {}
sel.value = "E3"; sel.onchange(); await flush(); await flush();
await new Promise((ok) => setTimeout(ok, 5)); await flush();
res.afterPointer = posts();
cur = cfg.items[2]; cfg.afterRender(); els["#dw-reason-row"].hidden = true;
try { await cfg.decisions[1].fn(cur, body); } catch (e) {}
clicks = 0; slow = 15;   // Enter on an open list, then its change: one save, never a second click refused as a key press
sel.value = "E1"; sel.onkeydown({ key: "Enter", preventDefault() {} }); sel.onchange();
await new Promise((ok) => setTimeout(ok, 60)); await flush(); await flush();
res.enterThenChange = [clicks, posts().slice(2)];
console.log(JSON.stringify(res));
""")
    assert out["afterArrows"] == []
    assert out["beforeTimer"] == []
    assert out["afterEnter"] == [[2, "E2"]]
    assert out["afterPointer"] == [[2, "E2"], [1, "E3"]]
    assert out["enterThenChange"] == [1, [[3, "E1"]]]


def test_label_focus_settings():
    """records.js hands the focus page its bar, the queue's own count and the criteria with the codebook version."""
    out = run_node(QUEUE_PAGE + queue_name("ana") + f"const CB = {json.dumps(CODEBOOK)};\n" + RECORDS + LB + r"""
const f = cfg.full;
console.log(JSON.stringify({ title: f.title, leave: f.leave, signing: f.signing, p: f.progress(cfg.items[1]), note: f.note,
  aside: f.aside(cfg.items[1]), tones: cfg.decisions.map((d) => d.tone) }));
""")
    assert out["title"] == "Label records" and out["leave"] == "Leave labelling"
    assert out["signing"] == "signing as <b>ana</b>"
    assert out["p"] == {"text": "Record 2 of 3 · you have labelled 2 of 3", "done": 2, "total": 3}
    assert "The AI model's decision" in out["note"]
    a = out["aside"]
    assert "Your criteria" in a and "Include when" in a and "field study: on cropland" in a
    assert "Exclude when" in a and "<b>E3</b> No organic carbon outcome below 30 cm" in a and "Codebook version 2" in a
    assert out["tones"] == ["include", "exclude", "maybe"]


SIDE = {"decision": "include", "reason": "", "ticks": {"e1": "yes"}, "tags": [], "note": "", "made_in": "wider",
        "blinded": "yes", "left_out": False, "abstract_version": 2, "made_at": "2026-10-14T10:12:00+00:00"}
COMPARE = {"sample": "development", "a": "lena", "b": "tomas", "locked": False, "rows": [
    {"record_id": 41, "title": "Synthetic tillage record 41", "agree": False, "adjudicated": None,
     "a": SIDE, "b": {**SIDE, "decision": "exclude", "reason": "E3"}, "different_abstracts": ""}]}
AGREE = {"n": 120, "agreement": 0.925, "kappa": 0.81, "disagreements": [{"record_id": 41}]}


def _adjudication(author: str, tail: str) -> dict:
    """The adjudication page of COMPARE opened by `author`; `tail` runs with cfg, f, u and body in scope."""
    return run_node(QUEUE_PAGE + queue_name(author) + f"const CB = {json.dumps(CODEBOOK)};\n" + RECORDS + f"""
global.lbCodebook = CB;
global.Focus = {{ open: (cfg) => opened.push(cfg), isOpen: () => false }};
global.call = global.api;
apiReply = (p) => p.startsWith("/api/labels/agreement") ? {json.dumps(AGREE)} : p.startsWith("/api/labels/compare") ? {json.dumps(COMPARE)}
  : p.startsWith("/api/records/") ? {{ record: {{ title: "Synthetic tillage record 41", abstract: "x" }} }} : {{ ok: true, change_id: 3 }};
global.Drawer.bindRows = (t, fn) => {{ global.openRow = fn; }};
global.Drawer.author = () => {json.dumps(author)};
$("#ag-sample").value = "development"; $("#ag-a").value = "lena"; $("#ag-b").value = "tomas";
await $("#ag-run").onclick(); await flush(); await flush();
openRow(0);
const cfg = opened[opened.length - 1], f = cfg.full;
const u = {{ call: (p) => api(p), recordBlock: (r) => "<h2>" + r.title + "</h2>", h: esc }};
const body = await cfg.render(cfg.items[0], u);
""" + tail)


def test_adjudication_focus_page():
    """Adjudication opens as a focus page: the bar says who decides, the count names the sample, both reviewers and
    how many are resolved, the agreement line under it; both labels above the record, each card coloured by its
    decision and signed; the adjudicator's line and the optional reason under the decisions; the model never shown."""
    out = _adjudication("mara", """
console.log(JSON.stringify({ title: f.title, leave: f.leave, signing: f.signing, p: f.progress(cfg.items[0]), note: f.note,
  aside: f.aside(cfg.items[0]), body, tones: cfg.decisions.map((d) => d.tone) }));""")
    assert out["title"] == "Adjudicate disagreements" and out["leave"] == "Leave adjudication"
    assert out["signing"] == "deciding as <b>mara</b>"
    p = out["p"]
    assert p["text"].startswith("Sample development · lena and tomas · disagreement 1 of 1") and p["total"] == 1
    assert p["more"] == "Both labelled 120 records · they agree on 111 (92.5%) · agreement beyond chance 0.81 (Cohen's κ)"   # plain words, the symbol as an aside
    assert "The AI model's decisions, the rank and the score are not shown." in out["note"]
    b = out["body"]
    assert b.index('class="fx-above"') < b.index("<h2>Synthetic tillage record 41</h2>") < b.index("dw-after fx-adj")
    assert 'data-tone="include"' in b and 'data-tone="exclude"' in b
    assert "Signed <b>lena</b>" in b and "Signed <b>tomas</b>" in b
    assert "You decide as <b>mara</b>, the adjudicator." in b and "Reason for the resolution (optional)" in b
    assert "The criteria" in out["aside"]
    assert out["tones"] == ["include", "exclude", "maybe"]
    for word in ("confidence", "rule_decision", "provider", "score"):
        assert word not in b

    assert "labelled this sample" not in b   # mara is neither reviewer


SAVE = """
els["#ag-adj-reason"] = { value: "  tomas read the depth wrong " };
await cfg.decisions[0].fn(cfg.items[0]);
console.log(JSON.stringify({ body, posted: calls.filter(([p, b]) => p === "/api/labels/adjudicate" && b).map(([, b]) => b) }));"""


def test_adjudication_saves_with_adjudicator_and_reason():
    """Saving Include posts the adjudication signed by the adjudicator, with the optional reason trimmed."""
    out = _adjudication("mara", SAVE)
    assert out["posted"] == [{"sample": "development", "record_id": 41, "decision": "include", "by": "mara",
                              "reason": "tomas read the depth wrong"}]


def test_adjudicator_who_labelled_the_sample():
    """When lena, one of the two reviewers, adjudicates, the page says once that she labelled this sample too, and the
    decision still saves, signed by lena."""
    out = _adjudication("lena", SAVE)
    assert out["body"].count("You labelled this sample as <b>lena</b> too; your decision is saved as the adjudication.") == 1
    assert [(x["by"], x["decision"]) for x in out["posted"]] == [("lena", "include")]


def test_focus_css_edges_and_narrow():
    """The look of the sketches: a 6px left edge on the record card and on each decision button, three buttons in a
    row, one column at 900px or less, the bar's light name colour, dark-mode variants for both themes."""
    css = static("focus.css")
    assert "border-left:6px solid var(--edge)" in css and "border-left:6px solid var(--dc)" in css
    assert "grid-template-columns:repeat(3, minmax(0, 1fr))" in css
    assert re.search(r"@media \(max-width: 900px\) \{ \.drawer\.full \.fx-grid \{ grid-template-columns:minmax\(0, 1fr\); \}", css)
    assert ':root:not([data-theme="light"]) .drawer.full' in css and ':root[data-theme="dark"] .drawer.full' in css
    html = static("index.html")
    assert html.index("/static/style.css") < html.index("/static/focus.css")
    assert html.index("/static/drawer.js") < html.index("/static/focus.js") < html.index("/static/records.js")


def test_adjudication_signed_by_the_name_the_bar_shows():
    """Opened with no name, the bar reads "no name yet"; a first name set afterwards (no reload) does not sign this
    page's decision: it is refused and nothing is posted until the disagreement is opened again (risk-checker R2)."""
    out = _adjudication("", """
global.Drawer.author = () => "mara";   // a first name set in another tab after the page was opened
let msg = "";
try { await cfg.decisions[0].fn(cfg.items[0]); } catch (e) { msg = e.message; }
console.log(JSON.stringify({ signing: f.signing, msg, posted: calls.filter(([p, b]) => p === "/api/labels/adjudicate" && b).length }));""")
    assert out == {"signing": "no name yet", "msg": "enter your name first, then open the disagreement again", "posted": 0}
