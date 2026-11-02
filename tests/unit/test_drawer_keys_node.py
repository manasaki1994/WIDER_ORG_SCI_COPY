"""Record drawer keys run under Node on a small stand-in for the page (no browser): deciding with a number key saves,
says so and moves on after 200 ms; a letter typed in the note field never moves the drawer; the duplicate-pair drawer
asks for the name its decisions are signed with; page requests carry a name in any script."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

# The one name WIDER works as (common.js ``WiderName``, ui-redesign D33), read live from the stored key so a test sets
# localStorage "wider.reviewer"; NEED and needHtml are common.js's own lines. ask() only counts (no dialog here).
_COMMON = (STATIC / "common.js").read_text(encoding="utf-8").splitlines()
WIDER_NAME = ("global.WiderName = (() => {\n"
              + next(ln for ln in _COMMON if "const NEED = " in ln) + "\n"
              + next(ln for ln in _COMMON if "const needHtml = " in ln) + "\n"
              + r"""  const get = () => String(localStorage.getItem("wider.reviewer") || "").trim();
  return { KEY: "wider.reviewer", NEED, needHtml, get, asked: 0, ask() { this.asked++; return Promise.resolve(get()); },
    askOnce() {}, render() {},
    set(n) { n = String(n || "").trim(); if (n) localStorage.setItem("wider.reviewer", n); else localStorage.removeItem("wider.reviewer"); } };
})();
""")

# just enough of a document for drawer.js: elements by selector, focus, one keydown listener, timers held back
PAGE = r"""
const els = {}, listeners = {}, timers = [];
function mk(sel, tag) {
  return { sel, tagName: tag || "DIV", textContent: "", innerHTML: "", hidden: false, disabled: false, className: "",
    dataset: {}, value: "", setAttribute() {}, appendChild(x) { x.parentNode = this; }, addEventListener() {}, scrollIntoView() {}, querySelectorAll() { return []; },
    querySelector(s) { return document.querySelector(s); }, contains(x) { return !!x && x !== document.body; },
    focus() { document.activeElement = this; },
    blur() { if (document.activeElement === this) document.activeElement = document.body; },
    classList: { add() {}, remove() {}, toggle() {} } };
}
global.document = { body: mk("body", "BODY"), querySelector(s) { return (els[s] ||= mk(s)); },
  addEventListener(type, fn) { listeners[type] = fn; }, contains() { return true; } };
// the parts of the drawer that are rebuilt for each record hold only the elements marked as inside them
for (const s of ["#dw-body", "#dw-notes", "#dw-decide", "#toasts"]) { const e = mk(s); e.contains = (x) => !!x && x.within === s; els[s] = e; }
document.activeElement = document.body;
global.window = global; global.addEventListener = () => {};
global.location = { hash: "#dedup" };
const ls = {}, calls = [];
global.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: (k) => { delete ls[k]; } };
global.setTimeout = (fn, ms) => { const t = [fn, ms]; timers.push(t); return t; };
global.clearTimeout = (t) => { if (t) t[0] = () => {}; };   // a cleared timer stays in the list but does nothing
global.esc = (s) => String(s);
// requests are recorded; a test that expects a reply sets global.apiReply
global.api = async (url, opts) => { calls.push([url, opts && opts.body ? JSON.parse(opts.body) : null]);
  if (global.apiReply) return global.apiReply(url); throw new Error("no network in this test"); };
const flush = () => new Promise((r) => setImmediate(r));
const key = (k) => ({ key: k, metaKey: false, ctrlKey: false, altKey: false, prevented: false, preventDefault() { this.prevented = true; } });
const pos = () => document.querySelector("#dw-pos").textContent;
""" + WIDER_NAME

# records.js on test_ui_followup_r31's QUEUE_PAGE: it listens for the document's "widername" event and reads WiderName
QUEUE_NAME = "document.addEventListener = () => {};\n" + WIDER_NAME

# the labelling queue's decisions, in the order records.js gives them (asserted below)
OPEN = r"""
const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3, 4, 5].map((id) => ({ id })), index: 2, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: ["include", "exclude", "maybe"].map((d) => ({ value: d, label: d[0].toUpperCase() + d.slice(1),
    fn: async (r) => { saved.push([r.id, d]); } })) });
await flush();
"""


def run_node(script: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    script = f"(async () => {{\n{script}\n}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});"
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def run_drawer(body: str, files: tuple[str, ...] = ("drawer.js",), opening: str = OPEN) -> dict:
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in files)
    return run_node(f"{PAGE}\n{code}\n{opening}\n{body}")


def test_labelling_queue_decisions_are_include_exclude_maybe():
    js = (STATIC / "records.js").read_text(encoding="utf-8")
    lb = js[js.index("function lbOpen"):]
    assert lb.index('decisions: ["include", "exclude", "maybe"]') < lb.index("\n  }\n")


def test_decide_with_a_key_saves_and_advances_after_200_ms():
    out = run_drawer(r"""
const before = pos();
const ev = key("2"); listeners.keydown(ev); await flush();
const msg = document.querySelector("#dw-dec-msg").textContent, waits = timers.map((t) => t[1]), still = pos();
timers.splice(0).forEach(([fn]) => fn()); await flush();
console.log(JSON.stringify({ before, saved, msg, waits, still, after: pos(), prevented: ev.prevented }));""")
    assert out["before"] == "3 / 5" and out["prevented"]
    assert out["saved"] == [[3, "exclude"]] and out["msg"] == "saved: exclude"
    # the move waits 200 ms (so the reviewer sees the confirmation), then shows the fourth record
    assert out["waits"] == [200] and out["still"] == "3 / 5" and out["after"] == "4 / 5"


def test_a_letter_typed_in_the_note_field_never_moves_the_drawer():
    out = run_drawer(r"""
const note = mk("#dw-note", "TEXTAREA"); note.focus();
const evs = ["j", "k", "2", "n", "t"].map(key);
evs.forEach((e) => listeners.keydown(e)); await flush();
const r = { pos: pos(), saved, timers: timers.length, prevented: evs.map((e) => e.prevented), focus: document.activeElement.sel };
// Esc leaves the field (the drawer stays open), a second Esc closes it
listeners.keydown(key("Escape")); r.afterEsc = document.activeElement.sel; r.openAfterEsc = Drawer.isOpen();
console.log(JSON.stringify(r));""")
    # no move, no decision, no default prevented: the letters go into the note as typed
    assert out["pos"] == "3 / 5" and out["saved"] == [] and out["timers"] == 0
    assert out["prevented"] == [False] * 5 and out["focus"] == "#dw-note"
    assert out["afterEsc"] == "#drawer" and out["openAfterEsc"] is True


# the duplicate pair's name rules on the Deduplication page: test_dedup_page_node.py::test_duplicate_pair_view_asks_for_the_name_and_remembers_it


def test_page_requests_send_a_name_in_any_script():
    # the fetch wrapper at the top of app.js sends the name WiderName works as (a header holds Latin-1 only, so the
    # name is percent-encoded)
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    start = js.index("(function () {\n  const orig = window.fetch")
    wrapper = js[start:js.index("})();", start) + len("})();")]
    names = ["Łucja", "مانا", "ana"]
    out = run_node(r"""
global.window = global;
const ls = {}, sent = [];
global.localStorage = { getItem: (k) => (k in ls ? ls[k] : null) };
global.WiderName = { get: () => ls["wider.reviewer"] || "" };   // the name the page works as (D33)
global.fetch = async (input, init) => { const hd = new Headers(init.headers || {});
  sent.push([input, hd.get("X-Wider-Author"), hd.get("Content-Type")]); return "ok"; };
""" + wrapper + f"""
for (const n of {json.dumps(names)}) {{ ls["wider.reviewer"] = n; await fetch("/api/project", {{ headers: {{ "Content-Type": "application/json" }} }}); }}
delete ls["wider.reviewer"]; await fetch("/api/project", {{}});
ls["wider.reviewer"] = "Łucja"; await fetch("/static/app.js", {{}});
console.log(JSON.stringify(sent));""")
    # every request went out (none threw), with an ASCII header that decodes to the name as typed
    assert [s[1] for s in out[:3]] and all(s[1].isascii() for s in out[:3])
    assert [unquote(s[1]) for s in out[:3]] == names and all(s[2] == "application/json" for s in out[:3])
    # no name: no header; a request outside /api/ never carries the name
    assert out[3] == ["/api/project", None, None] and out[4][1] is None


def test_a_note_is_saved_under_the_name_wider_works_as():
    # ui-redesign D33: the drawer has no name field of its own; a note is signed with the name WIDER works as, and no
    # prompt() ever opens
    out = run_drawer(r"""
global.prompt = () => { throw new Error("prompt() opened"); };
localStorage.setItem("wider.reviewer", "ana");
global.apiReply = (url) => (url.includes("/notes") ? { change_id: 3 } : { notes: [], tags: [] });
Drawer.open({ label: "Records", items: [{ id: 9 }], index: 0, recordId: (r) => r.id,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush();
const r = { field: /dw-author|Your name/.test(document.querySelector("#dw-notes").innerHTML) };
await document.querySelector("#dw-note").onkeydown({ key: "Enter", shiftKey: false, preventDefault() {}, target: { value: "check the sample" } });
await flush();
r.posts = calls.filter(([url, body]) => body); r.stored = localStorage.getItem("wider.reviewer");
console.log(JSON.stringify(r));""", opening="")
    assert not out["field"]
    assert out["posts"] == [["/api/records/9/notes", {"author": "ana", "text": "check the sample"}]]
    assert out["stored"] == "ana"


def test_cancelling_the_consent_dialog_sends_nothing():
    # api() in common.js: a 403 asking for consent opens the dialog; the request is sent again only after Allow
    out = run_node(r"""
global.window = global;
global.document = { activeElement: null, addEventListener() {} };
global.setTimeout = () => 0; global.clearTimeout = () => {};
const sent = [];
global.fetch = async (path) => { sent.push(path);
  if (path === "/api/enrich/run" && sent.length % 2 === 1)
    return { ok: false, status: 403, statusText: "Forbidden",
             json: async () => ({ detail: { consent_required: { service: "OpenAlex" }, message: "allow?" } }) };
  return { ok: true, status: 200, json: async () => ({ done: true }) }; };
""" + (STATIC / "common.js").read_text(encoding="utf-8") + r"""
const r = {};
askLookupConsent = async () => false;
try { await api("/api/enrich/run", { method: "POST" }); r.cancel = "no error"; } catch (e) { r.cancel = [e.cancelled, e.message]; }
r.afterCancel = sent.splice(0);
askLookupConsent = async () => true;
r.allow = await api("/api/enrich/run", { method: "POST" });
r.afterAllow = sent.splice(0);
console.log(JSON.stringify(r));""")
    # Cancel (and Esc, which resolves the dialog the same way) sends nothing more: one blocked request, an error saying so
    assert out["afterCancel"] == ["/api/enrich/run"]
    assert out["cancel"][0] is True and out["cancel"][1].startswith("Nothing was sent")
    # Allow repeats the request once
    assert out["afterAllow"] == ["/api/enrich/run", "/api/enrich/run"] and out["allow"] == {"done": True}


def test_focus_returns_to_a_fetch_button_disabled_while_the_dialog_was_open():
    # Construct "Fetch missing abstracts" disables itself before its request, which drops the focus to the page body;
    # after Cancel (or Allow) the focus goes back to it once it is enabled again (fourteenth round)
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    handler = js[js.index('$("#bm-fetch").onclick'):js.index('$("#art-propose").onclick')]
    out = run_node(r"""
const els = {}, observers = [];
let focusin = null, dialog = null;
function mk(sel) {
  return { sel, isConnected: true, textContent: "", _dis: false,
    get disabled() { return this._dis; },
    set disabled(v) { this._dis = v; if (v && document.activeElement === this) document.activeElement = document.body;
                      observers.filter((o) => o.el === this).forEach((o) => o.fn()); },
    focus() { if (this._dis) return; document.activeElement = this; if (focusin && !this.inDialog) focusin({ target: this }); } };
}
global.MutationObserver = class { constructor(fn) { this.fn = fn; } observe(el) { this.el = el; observers.push(this); }
  disconnect() { const i = observers.indexOf(this); if (i >= 0) observers.splice(i, 1); } };
global.window = global; global.setTimeout = () => 0; global.clearTimeout = () => {};
global.document = { body: { sel: "body", appendChild() {} }, addEventListener(t, fn) { if (t === "focusin") focusin = fn; },
  createElement() {
    const handlers = {}, cancel = mk("cancel"), allow = mk("allow"); cancel.inDialog = allow.inDialog = true;
    dialog = { handlers, setAttribute() {}, addEventListener(t, fn) { handlers[t] = fn; }, showModal() {}, close() {},
      remove() { document.activeElement = document.body; }, querySelector: (s) => (s.includes("cancel") ? cancel : allow) };
    return dialog; } };
document.activeElement = document.body;
const sent = [];
global.fetch = async (path) => { sent.push(path);
  if (path === "/api/benchmark/fetch") return { ok: false, status: 403, statusText: "Forbidden",
    json: async () => ({ detail: { consent_required: { services: ["OpenAlex"], fields: ["DOI"] }, message: "allow?" } }) };
  return { ok: true, status: 200, json: async () => ({}) }; };
""" + (STATIC / "common.js").read_text(encoding="utf-8") + r"""
const $ = (s) => (els[s] ||= mk(s));
const loadConstruct = async () => {};
""" + handler + r"""
const flush = () => new Promise((r) => setImmediate(r));
const r = {};
const btn = $("#bm-fetch"); btn.focus();
const run = btn.onclick(); await flush(); await flush();
r.open = [document.activeElement.sel, btn.disabled];
dialog.handlers.click({ target: { closest: () => ({ dataset: { act: "cancel" } }) } });
await run; await flush();
r.after = [document.activeElement.sel, btn.disabled, $("#bm-msg").textContent, sent];
console.log(JSON.stringify(r));""")
    assert out["open"] == ["cancel", True]
    assert out["after"] == ["#bm-fetch", False, "Nothing was sent: online lookups are not allowed for this project.",
                            ["/api/benchmark/fetch"]]


def test_the_labelling_queue_has_no_read_only_rows():
    # label-completion-and-sealing D9: no read-only chip, no closed sentence and no readOnly rule in the labelling
    # queue or the adjudication drawer; a locked sample's rows take labels
    js = (STATIC / "records.js").read_text(encoding="utf-8")
    assert "readOnly" not in js and "read-only" not in js and "CLOSED" not in js


def test_focus_stays_in_the_drawer_after_a_move():
    out = run_drawer(r"""
const r = { opened: document.activeElement.sel };
// a control that survives the move (Next) keeps the focus
const next = mk("#dw-next", "BUTTON"); els["#dw-next"] = next; next.focus();
listeners.keydown(key("j")); await flush(); await flush();
r.kept = [pos(), document.activeElement.sel];
// focus that left the drawer (the old item's controls were replaced) goes to the new title, never the page body
document.activeElement = document.body;
listeners.keydown(key("k")); await flush(); await flush();
r.body = [pos(), document.activeElement.sel];
// Next turns disabled on the last record, so it hands the focus to the title too
listeners.keydown(key("j")); await flush(); await flush();
next.focus();
listeners.keydown(key("j")); await flush(); await flush();
r.disabled = [pos(), document.activeElement.sel];
console.log(JSON.stringify(r));""")
    assert out["opened"] == "#dw-title"
    assert out["kept"] == ["4 / 5", "#dw-next"]
    assert out["body"] == ["3 / 5", "#dw-title"]
    assert out["disabled"] == ["5 / 5", "#dw-title"]


def test_keys_pressed_while_the_next_record_loads_are_kept_in_the_drawer():
    out = run_drawer(r"""
let release = null;
Drawer.open({ label: "Labelling", items: [1, 2, 3].map((id) => ({ id })), index: 0,
  render: async (r) => { if (r.id > 1) await new Promise((ok) => { release = ok; }); return `<h2 id="dw-title">record ${r.id}</h2>`; } });
await flush(); await flush();
const r = { opened: document.activeElement.sel };
document.querySelector("#dw-title").within = "#dw-body";  // the title is rebuilt with the record
// j: while record 2 loads the drawer itself holds the focus, never the page body
listeners.keydown(key("j")); await flush();
r.loading = [pos(), document.activeElement.sel];
// n pressed now waits for record 2 and then opens its note field
const n = key("n"); listeners.keydown(n); await flush();
r.n = [n.prevented, document.activeElement.sel];
release(); for (let i = 0; i < 4; i++) await flush();
r.after = [pos(), document.activeElement.sel];
console.log(JSON.stringify(r));""", opening="")
    assert out["opened"] == "#dw-title"
    assert out["loading"] == ["2 / 3", "#drawer"]
    assert out["n"] == [True, "#drawer"]
    assert out["after"] == ["2 / 3", "#dw-note"]


def test_t_and_decision_keys_pressed_while_the_next_record_loads():
    # t waits for the record on its way and opens its tag field; a decision key decides nothing the reviewer has not
    # seen yet (fourteenth round)
    out = run_drawer(r"""
let release = null; const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3].map((id) => ({ id })), index: 0,
  render: async (r) => { if (r.id > 1) await new Promise((ok) => { release = ok; }); return `<h2 id="dw-title">record ${r.id}</h2>`; },
  decisions: [{ value: "include", label: "Include", fn: async (r) => { saved.push(r.id); } }] });
await flush(); await flush();
document.querySelector("#dw-title").within = "#dw-body";
listeners.keydown(key("j")); await flush();
const one = key("1"); listeners.keydown(one); await flush();
const t = key("t"); listeners.keydown(t); await flush();
const r = { one: [one.prevented, saved.slice(), document.querySelector("#dw-dec-msg").textContent], t: [t.prevented, document.activeElement.sel] };
release(); for (let i = 0; i < 4; i++) await flush();
r.after = [pos(), document.activeElement.sel, saved.slice()];
console.log(JSON.stringify(r));""", opening="")
    assert out["one"][0] is True and out["one"][1] == [] and out["one"][2] == "the next record is still loading: nothing was decided (1 key press ignored)"
    assert out["t"] == [True, "#drawer"]
    assert out["after"] == ["2 / 3", "#dw-tag", []]


def test_the_drawer_stays_with_the_page_it_was_opened_on():
    # showPage closes an open drawer before showing another page (app.js)
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    sp = js[js.index("function showPage"):]
    sp = sp[:sp.index("\n}\n")]
    assert sp.index("Drawer.close(true)") < sp.index('document.querySelectorAll(".page")')
    out = run_drawer(r"""
const saved = [];
const opts = (table) => ({ label: "Labelling", table, items: [1, 2].map((id) => ({ id })), index: 0,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: [{ value: "include", label: "Include", fn: async (r) => { saved.push(r.id); } }] });
// a queue that finished loading after its page was left does not open over the new page
const hiddenTable = mk("#lb-table", "TABLE"); hiddenTable.closest = () => ({ hidden: true }); els["#lb-table"] = hiddenTable;
Drawer.open(opts("#lb-table")); await flush();
const r = { openedOnHiddenPage: Drawer.isOpen() };
// open on a shown page, then leave it: the drawer closes, keeps the new page's focus, and its keys label nothing
const shownTable = mk("#ev-table", "TABLE"); shownTable.closest = () => ({ hidden: false }); els["#ev-table"] = shownTable;
Drawer.open(opts("#ev-table")); await flush(); await flush();
r.opened = Drawer.isOpen();
const main = mk("#main", "MAIN"); main.focus();
Drawer.close(true);
r.after = [Drawer.isOpen(), document.activeElement.sel];
listeners.keydown(key("1")); listeners.keydown(key("j")); await flush();
r.saved = saved;
console.log(JSON.stringify(r));""", opening="")
    assert out["openedOnHiddenPage"] is False
    assert out["opened"] is True
    assert out["after"] == [False, "#main"]
    assert out["saved"] == []


def test_a_note_is_saved_once_and_the_field_empties():
    # two quick Enters while the first save is on its way save one note; a refused save gives the text back
    out = run_drawer(r"""
let refuse = false;
global.apiReply = (url) => {
  if (!url.includes("/notes")) return { notes: [], tags: [] };
  if (refuse) throw new Error("the note was not saved");
  return { change_id: 3 };
};
localStorage.setItem("wider.reviewer", "ana");
Drawer.open({ label: "Records", items: [{ id: 9 }], index: 0, recordId: (r) => r.id,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush();
const field = document.querySelector("#dw-note");
const enter = () => ({ key: "Enter", shiftKey: false, preventDefault() {}, target: field });
field.value = "check the sample";
const first = field.onkeydown(enter()), second = field.onkeydown(enter());
await first; await second; await flush();
const r = { posts: calls.filter(([url, body]) => body).length, after: field.value };
refuse = true; field.value = "keep me";
await field.onkeydown(enter()); await flush();
r.kept = document.querySelector("#dw-note").value;
console.log(JSON.stringify(r));""", opening="")
    assert out == {"posts": 1, "after": "", "kept": "keep me"}


def test_a_tag_refusal_survives_the_re_render_of_an_earlier_tag():
    # a good tag and a bad one sent back to back: the refusal arrives while the first save re-renders the notes box,
    # and both the message and the refused text are still there afterwards
    out = run_drawer(r"""
let gate = null, release = null;
global.apiReply = (url) => {
  if (url.includes("/tags")) {
    const body = calls[calls.length - 1][1];
    if (body.tag.includes("/")) throw new Error("a tag is 1-40 characters (got 'bad/tag')");
    return { change_id: 4, tag: "hard-case" };
  }
  return gate ? gate.then(() => ({ notes: [], tags: [] })) : { notes: [], tags: [] };
};
localStorage.setItem("wider.reviewer", "ana");
Drawer.open({ label: "Records", items: [{ id: 9 }], index: 0, recordId: (r) => r.id,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush();
const box = document.querySelector("#dw-notes"); let html = box.innerHTML;
Object.defineProperty(box, "innerHTML", { get: () => html, set: (v) => { html = v;   // a re-render empties the parts
  for (const s of ["#dw-tag", "#dw-note", "#dw-notes-msg"]) { const e = document.querySelector(s); e.value = ""; e.innerHTML = ""; } } });
gate = new Promise((r) => { release = r; });
const field = document.querySelector("#dw-tag");
const enter = () => ({ key: "Enter", preventDefault() {}, target: field });
field.value = "Hard Case"; const first = field.onkeydown(enter()); await flush();
field.value = "bad/tag"; await field.onkeydown(enter()); await flush();
release(); await first; await flush();
console.log(JSON.stringify({ field: document.querySelector("#dw-tag").value,
  msg: document.querySelector("#dw-notes-msg").innerHTML.includes("a tag is 1-40") }));""", opening="")
    assert out == {"field": "bad/tag", "msg": True}


# the Deduplication page's docked pair view: the drawer sits in the page's own box, Esc goes back to the list
DOCK = r"""
const saved = [], escs = [];
const box = mk("#dd-pair"); box.closest = () => ({ hidden: false }); els["#dd-pair"] = box;
const search = mk("#dd-search", "INPUT"), row = mk("#dd-row-0", "TR");
const decide = (d) => async (r) => { saved.push([r.id, d, localStorage.getItem("wider.reviewer")]); };
Drawer.open({ label: "Duplicate pairs", dock: "#dd-pair", items: [1, 2, 3].map((id) => ({ id })), index: 0, notes: false,
  onEsc: () => { escs.push(1); row.focus(); },
  render: async (r) => `<h2 id="dw-title">pair ${r.id}</h2>`,
  decisions: ["duplicate", "not duplicate", "uncertain"].map((d) => ({ value: d, label: d, fn: decide(d) })) });
await flush();
"""


def test_docked_pair_view_keys_decide_once_and_esc_goes_back_to_the_list():
    out = run_drawer(r"""
localStorage.setItem("wider.reviewer", "ana");
const r = { docked: Drawer.isDocked(), inBox: document.querySelector("#drawer").parentNode === box,
  closeHidden: document.querySelector("#dw-close").hidden, escWord: document.querySelector(".dw-keys-esc").textContent };
listeners.keydown(key("1")); await flush();
r.msg = document.querySelector("#dw-dec-msg").textContent; r.waits = timers.map((t) => t[1]);
listeners.keydown(key("1")); await flush();   // the pause: a second 1 sends nothing
r.second = document.querySelector("#dw-dec-msg").textContent; r.savedInPause = saved.length;
timers.splice(0).forEach(([fn]) => fn()); await flush();
r.pos = pos();
// typing in the page's search box decides and moves nothing
search.focus(); ["1", "j"].map(key).forEach((e) => listeners.keydown(e)); await flush();
r.afterTyping = [pos(), saved.length, timers.length];
// Esc: the list row takes the focus and the pair stays docked
document.querySelector("#drawer").focus(); listeners.keydown(key("Escape")); await flush();
r.esc = [escs.length, document.activeElement.sel, Drawer.isOpen(), Drawer.isDocked()];
r.saved = saved;
console.log(JSON.stringify(r));""", opening=DOCK)
    assert out["docked"] is True and out["inBox"] is True
    # no close button on the page's own view, and the key hint says where Esc goes
    assert out["closeHidden"] is True and out["escWord"] == "back to the list"
    assert out["msg"] == "saved: duplicate" and out["waits"] == [200]
    assert out["savedInPause"] == 1 and "nothing more was decided" in out["second"]
    assert out["pos"] == "2 / 3"
    assert out["afterTyping"] == ["2 / 3", 1, 0]
    assert out["esc"] == [1, "#dd-row-0", True, True]
    assert out["saved"] == [[1, "duplicate", "ana"]]


def test_docked_keys_do_nothing_after_leaving_the_page_and_close_puts_the_drawer_back():
    out = run_drawer(r"""
const home = document.querySelector("#drawer").parentNode;
location.hash = "#screen";   // another page is shown
listeners.keydown(key("1")); listeners.keydown(key("j")); listeners.keydown(key("Escape")); await flush();
const r = { saved: saved.length, pos: pos(), escs: escs.length, open: Drawer.isOpen() };
Drawer.close(true);
r.after = [Drawer.isOpen(), Drawer.isDocked(), document.querySelector("#dw-close").hidden];
// the overlay drawer opened next is not docked and shows its close button again
Drawer.open({ label: "Labelling", items: [{ id: 9 }], render: async (x) => `<h2 id="dw-title">record ${x.id}</h2>` });
await flush();
r.overlay = [Drawer.isDocked(), document.querySelector(".dw-keys-esc").textContent];
console.log(JSON.stringify(r));""", opening=DOCK)
    assert out["saved"] == 0 and out["pos"] == "1 / 3" and out["escs"] == 0 and out["open"] is True
    assert out["after"] == [False, False, False]
    assert out["overlay"] == [False, "close"]


def test_a_docked_view_does_not_open_on_a_hidden_page():
    out = run_drawer(r"""
const hidden = mk("#dd-pair"); hidden.closest = () => ({ hidden: true }); els["#dd-pair"] = hidden;
Drawer.open({ label: "Duplicate pairs", dock: "#dd-pair", items: [{ id: 1 }], render: async () => "" });
await flush();
console.log(JSON.stringify({ open: Drawer.isOpen() }));""", opening="")
    assert out["open"] is False


def test_drawer_recheck_and_second_modes():
    # records.js with a stand-in drawer: each mode requests its own mode=, a save posts the item and no sample
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    out = run_node(QUEUE_PAGE + QUEUE_NAME + rec + r"""
const items = [{ item: "tok1", title: "t1", abstract: "a1" }, { item: "tok2", title: "t2", abstract: "a2" }];
apiReply = (p) => p.startsWith("/api/labels/queue") ? { items: items.map((r) => ({ ...r })), lines: ["Blind to the queue only: the History page still lists earlier labels."] }
  : p.startsWith("/api/labels") ? { change_id: 9 } : {};
const res = { modes: [] };
localStorage.setItem("wider.reviewer", "R1"); $("#lb-filter").value = "all";
for (const m of ["recheck", "second"]) {
  $("#lb-mode").value = m; calls.length = 0;
  $("#lb-load").onclick(); await flush(); await flush();
  const cfg = opened[opened.length - 1];
  const body = { querySelectorAll: () => [], querySelector: () => ({ value: "" }) };
  await cfg.decisions[0].fn(cfg.items[0], body);
  res.modes.push({ asked: calls[0][0], label: cfg.label, recordId: cfg.recordId(cfg.items[0]), posted: calls[calls.length - 1],
                   lines: $("#lb-lines").innerHTML, history: calls.some((c) => c[0].includes("/api/history")) });
}
console.log(JSON.stringify(res));""")
    for m, mode in zip(out["modes"], ("recheck", "second")):
        assert m["asked"] == f"/api/labels/queue?mode={mode}&reviewer=R1"
        assert m["recordId"] is None and not m["history"]
        assert m["posted"][1]["item"] == "tok1" and "sample_id" not in m["posted"][1] and "record_id" not in m["posted"][1]
        assert "Blind to the queue only" in m["lines"]
    # the real drawer, given no record id, renders no notes, tags or History panel and asks for no History
    res = run_drawer(r"""
Drawer.open({ label: "Re-check queue (blind)", items: [{ item: "tok1", title: "t1", abstract: "a1" }], index: 0, blind: true,
  author: "R1", recordId: () => null, render: async (r) => `<h2 id="dw-title">${r.title}</h2>`,
  decisions: [{ value: "include", label: "Include", fn: async () => {} }] });
await flush(); await flush();
console.log(JSON.stringify({ notes: document.querySelector("#dw-notes").innerHTML, calls: calls.map((c) => c[0]) }));""", opening="")
    assert "dw-history" not in res["notes"] and not any("/api/history" in c for c in res["calls"])


def test_blind_queue_label_posts_the_item():
    # manual-review D6: records.js posts a blind row's opaque item, no sample or record id, and still gives the drawer
    # the record id for its notes and history; an unblinded row (no item) posts its sample and record as before
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    out = run_node(QUEUE_PAGE + QUEUE_NAME + rec + r"""
let rows = [{ item: "tokA", record_id: 7, title: "t7", locked: false }];
apiReply = (p) => p.startsWith("/api/labels/queue") ? rows.map((r) => ({ ...r })) : p.startsWith("/api/labels") ? { change_id: 3 } : {};
const res = {};
localStorage.setItem("wider.reviewer", "ana"); $("#lb-filter").value = "all"; $("#lb-mode").value = "queue";
const body = { querySelectorAll: () => [], querySelector: () => ({ value: "" }) };
for (const k of ["blind", "plain"]) {
  calls.length = 0;
  $("#lb-load").onclick(); await flush(); await flush();
  const cfg = opened[opened.length - 1];
  await cfg.decisions[1].fn(cfg.items[0], body);
  res[k] = { recordId: cfg.recordId(cfg.items[0]), posted: calls.filter((c) => c[0] === "/api/labels").pop() };
  rows = [{ sample_id: 2, record_id: 8, title: "t8", locked: false }];
}
console.log(JSON.stringify(res));""")
    b = out["blind"]
    assert b["recordId"] == 7
    assert b["posted"][1]["item"] == "tokA" and b["posted"][1]["decision"] == "exclude"
    assert "sample_id" not in b["posted"][1] and "record_id" not in b["posted"][1]
    pl = out["plain"]["posted"][1]
    assert (pl["sample_id"], pl["record_id"]) == (2, 8) and "item" not in pl



def test_open_queue_opens_the_own_recheck_queue():
    # manual-review D10 with one name (ui-redesign D33): Open queue asks for the re-check queue of the name WIDER works
    # as, which is also the request's author header (app.js sends the stored name) and the drawer's author; with no
    # name it opens the name dialog and requests nothing
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    out = run_node(QUEUE_PAGE + QUEUE_NAME + rec + r"""
const sent = [];
apiReply = (p) => { sent.push([p, localStorage.getItem("wider.reviewer")]);
  return p.startsWith("/api/labels/queue") ? { items: [{ item: "tok1", title: "t1", abstract: "a1" }], lines: [] } : {}; };
$("#lb-filter").value = "all"; $("#lb-mode").value = "recheck";
$("#lb-load").onclick(); await flush(); await flush();
const none = { sent: sent.length, asked: WiderName.asked, opened: opened.length };
localStorage.setItem("wider.reviewer", "ana");
$("#lb-load").onclick(); await flush(); await flush();
const cfg = opened[opened.length - 1];
console.log(JSON.stringify({ none, sent, label: cfg && cfg.label, author: cfg && cfg.author }));""")
    assert out["none"] == {"sent": 0, "asked": 1, "opened": 0}
    assert out["sent"][0] == ["/api/labels/queue?mode=recheck&reviewer=ana", "ana"]
    assert out["label"] == "Re-check queue (blind)" and out["author"] == "ana"



def test_drawer_clears_the_previous_record_while_loading():
    # manual-review D11: j moves to a record whose answer is delayed: the drawer shows "loading" and nothing of the
    # earlier record (title, note, decision buttons); a 1 pressed meanwhile saves nothing and says so
    out = run_drawer(r"""
let release = null; const saved = []; global.fmtWhen = (s) => s;
apiReply = (u) => u.startsWith("/api/records/3") ? { notes: [{ note_id: 1, author: "ana", created_at: "2026-01-01T00:00:00", text: "check the design" }], tags: [] }
  : { notes: [], tags: [] };
Drawer.open({ label: "Labelling", items: [3, 4].map((id) => ({ id })), index: 0, author: "ana", blind: true,
  recordId: (r) => r.id,
  render: async (r) => { if (r.id === 4) await new Promise((ok) => { release = ok; }); return `<h2 id="dw-title">record ${r.id}</h2>`; },
  decisions: [{ value: "include", label: "Include", fn: async (r) => { saved.push(r.id); } }] });
for (let i = 0; i < 4; i++) await flush();
const before = { notes: document.querySelector("#dw-notes").innerHTML, body: document.querySelector("#dw-body").innerHTML,
  buttons: document.querySelector("#dw-decide").innerHTML };
listeners.keydown(key("j")); await flush();
const during = { notes: document.querySelector("#dw-notes").innerHTML, body: document.querySelector("#dw-body").innerHTML,
  buttons: document.querySelector("#dw-decide").innerHTML };
listeners.keydown(key("1")); await flush();
during.msg = document.querySelector("#dw-dec-msg").textContent; during.saved = saved.slice();
release(); for (let i = 0; i < 4; i++) await flush();
console.log(JSON.stringify({ before, during, after: document.querySelector("#dw-body").innerHTML }));""", opening="")
    b, d = out["before"], out["during"]
    assert "check the design" in b["notes"] and "record 3" in b["body"] and "data-dec" in b["buttons"]
    assert "loading" in d["body"] and "record 3" not in d["body"]
    assert "check the design" not in d["notes"] and "data-dec" not in d["buttons"]
    assert d["saved"] == [] and d["msg"] == "the next record is still loading: nothing was decided (1 key press ignored)"
    assert "record 4" in out["after"]


def test_recheck_drawer_says_history_is_not_shown():
    # manual-review D11: a re-check item (no record id) says where its history would be and asks for no history
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    cfg = run_node(QUEUE_PAGE + QUEUE_NAME + rec + r"""
apiReply = (p) => p.startsWith("/api/labels/queue") ? { items: [{ item: "tok1", title: "t1", abstract: "a1" }], lines: [] } : {};
localStorage.setItem("wider.reviewer", "ana"); $("#lb-filter").value = "all";
const res = {};
for (const m of ["recheck", "second", "queue"]) {
  $("#lb-mode").value = m;
  if (m === "queue") apiReply = (p) => p.startsWith("/api/labels/queue") ? [{ item: "tokA", record_id: 7, title: "t7", locked: false }] : {};
  $("#lb-load").onclick(); await flush(); await flush();
  res[m] = opened[opened.length - 1].historyNote;
}
console.log(JSON.stringify(res));""")
    assert cfg == {"recheck": "earlier labels are not shown in this queue",
                   "second": "earlier labels are not shown in this queue", "queue": ""}
    out = run_drawer(r"""
Drawer.open({ label: "Re-check queue (blind)", items: [{ item: "tok1", title: "t1", abstract: "a1" }], index: 0, blind: true,
  author: "ana", recordId: () => null, historyNote: "earlier labels are not shown in this queue",
  render: async (r) => `<h2 id="dw-title">${r.title}</h2>`, decisions: [{ value: "include", label: "Include", fn: async () => {} }] });
for (let i = 0; i < 3; i++) await flush();
const box = document.querySelector("#dw-notes");
console.log(JSON.stringify({ notes: box.innerHTML, hidden: box.hidden, calls: calls.map((c) => c[0]) }));""", opening="")
    assert "earlier labels are not shown in this queue" in out["notes"] and out["hidden"] is False
    assert not any("/api/history" in c or "/api/records" in c for c in out["calls"])


BLIND_ASK = "a change to a saved label here needs a reason for the correction"
UNBLIND_ASK = "sample 'dev' is locked: a change to a saved label needs a reason for the correction"
# records.js's labelling queue on the real drawer: a reply per request, the queue loaded for ana and opened; a post
# without a correction reason is answered like the route's 409 while `ask` holds its sentence; the dialog answers
# with `answer` (null is Cancel)
LB = r"""
let rows = [], ask = null, answer = null;
const dialogs = [];
global.askFields = async (title, fields, action) => { dialogs.push({ title, fields, action }); return answer; };
const refusal = (text) => { const e = new Error(text); e.status = 409; e.body = { detail: text, correction_needed: true }; return e; };
global.apiReply = (url) => {
  if (url.startsWith("/api/labels/queue")) return rows.map((r) => ({ ...r }));
  if (url === "/api/labels" || url === "/api/labels/adjudicate") {
    if (ask && !calls[calls.length - 1][1].correction_reason) throw refusal(ask);
    return { change_id: 5 };
  }
  if (url.startsWith("/api/annotations")) return {};
  if (url.startsWith("/api/records/")) return { notes: [], tags: [], record: { record_id: 4, title: "a record" } };
  return [];
};
const lbOpenQueue = async () => { localStorage.setItem("wider.reviewer", "ana");
  document.querySelector("#lb-filter").value = "all"; document.querySelector("#lb-load").onclick();
  for (let i = 0; i < 6; i++) await flush(); };
const posts = () => calls.filter((c) => c[0] === "/api/labels").map((c) => c[1]);
"""


def test_key_after_lock_elsewhere_asks_for_a_reason():
    # sampling-and-labelling "A label change after a lock in another tab asks for a reason": the queue was shown
    # before the lock; a missing label is saved, a change opens the correction dialog whose text names no sample, and
    # nothing is saved until a reason is given
    out = run_drawer(LB + r"""
rows = [{ record_id: 4, item: "t4", title: "a record", decision: "", locked: false },
        { record_id: 5, item: "t5", title: "another", decision: "include", locked: false }];
await lbOpenQueue();
listeners.keydown(key("1")); for (let i = 0; i < 4; i++) await flush();
const first = posts().length;
timers.filter((t) => t[1] === 200).forEach((t) => t[0]()); for (let i = 0; i < 8; i++) await flush();
const at = pos();
ask = "BLIND"; answer = null;
listeners.keydown(key("2")); for (let i = 0; i < 6; i++) await flush();
console.log(JSON.stringify({ at, first, all: posts(), dialogs, msg: document.querySelector("#dw-dec-msg").innerHTML }));""".replace("BLIND", BLIND_ASK),
        files=("drawer.js", "records.js"), opening="")
    assert out["first"] == 1 and out["all"][0]["decision"] == "include"   # the missing label saved at once
    assert len(out["all"]) == 2 and out["all"][1]["item"] == "t5" and "correction_reason" not in out["all"][1]
    d = out["dialogs"][0]
    assert d["title"] == "Reason for this correction" and d["action"] == "Save correction"
    text = json.dumps(d)
    assert "dev" not in text and "sample" not in text
    assert "nothing was saved" in out["msg"]


def test_correction_reason_dialog_posts_again():
    # D9: the reason typed in the dialog goes with the same request a second time, and the save is said as usual
    out = run_drawer(LB + r"""
rows = [{ record_id: 5, item: "t5", title: "a record", decision: "include", locked: true }];
await lbOpenQueue();
ask = "BLIND"; answer = { why: "misread the population" };
listeners.keydown(key("2")); for (let i = 0; i < 12; i++) await flush();
console.log(JSON.stringify({ all: posts(), msg: document.querySelector("#dw-dec-msg").textContent,
  table: document.querySelector("#lb-table").innerHTML }));""".replace("BLIND", BLIND_ASK),
        files=("drawer.js", "records.js"), opening="")
    first, again = out["all"]
    assert "correction_reason" not in first and again["correction_reason"] == "misread the population"
    assert {k: v for k, v in again.items() if k != "correction_reason"} == first
    assert out["msg"] == "saved: exclude" and "read-only" not in out["table"]


def test_correction_dialog_cancel_saves_nothing():
    # D9: Cancel (or an empty reason) sends nothing more and says so; the drawer does not move on
    res = {}
    for name, ans in (("cancel", "null"), ("empty", '{ why: "  " }')):
        res[name] = run_drawer(LB + r"""
rows = [{ record_id: 5, item: "t5", title: "a record", decision: "include", locked: true }];
await lbOpenQueue();
ask = "BLIND"; answer = ANSWER;
listeners.keydown(key("2")); for (let i = 0; i < 12; i++) await flush();
console.log(JSON.stringify({ all: posts(), msg: document.querySelector("#dw-dec-msg").innerHTML,
  timers: timers.filter((t) => t[1] === 200).length }));""".replace("BLIND", BLIND_ASK).replace("ANSWER", ans),
            files=("drawer.js", "records.js"), opening="")
    for out in res.values():
        assert len(out["all"]) == 1 and out["timers"] == 0
        assert "nothing was saved" in out["msg"]


def test_locked_record_drawer_blind_and_unblinded():
    # record-drawer MODIFIED "Locked samples read-only in the drawer": neither drawer says the record is closed; a
    # change asks for a reason with the route's sentence, which names no sample when blind and names it unblinded
    res = {}
    for blind in (True, False):
        out = run_drawer(LB + r"""
document.querySelector("#lb-blind").checked = BLIND;
rows = [BLIND ? { record_id: 4, item: "t4", title: "a record", decision: "include", locked: true }
              : { record_id: 4, sample_id: 1, sample: "dev", band: "S1", rank: 2, title: "a record", decision: "include", locked: true }];
await lbOpenQueue();
const html = document.querySelector("#dw-decide").innerHTML;
ask = BLIND ? "BASK" : "UASK"; answer = null;
listeners.keydown(key("2")); for (let i = 0; i < 6; i++) await flush();
console.log(JSON.stringify({ html, dialogs, sent: posts().length,
  asked: calls.find((c) => c[0].startsWith("/api/labels/queue"))[0] }));""".replace("BLIND", "true" if blind else "false")
            .replace("BASK", BLIND_ASK).replace("UASK", UNBLIND_ASK),
            files=("drawer.js", "records.js"), opening="")
        res[blind] = out
    for out in res.values():
        assert "closed" not in out["html"] and " disabled>" not in out["html"] and out["sent"] == 1
    blind_text = json.dumps(res[True]["dialogs"])
    assert BLIND_ASK in blind_text and "dev" not in blind_text and "sample" not in blind_text
    assert UNBLIND_ASK in json.dumps(res[False]["dialogs"])
    assert "&blind=false" in res[False]["asked"] and "blind=false" not in res[True]["asked"]


def test_adjudication_correction_dialog():
    # sampling-and-labelling: on a locked sample a first adjudication saves at once; a changed one asks for the reason
    # in the same dialog and posts again with it
    out = run_drawer(LB + r"""
let openRow = null;
Drawer.bindRows = (t, fn) => { openRow = fn; };
document.querySelector("#ag-sample").value = "dev"; document.querySelector("#ag-a").value = "ana";
document.querySelector("#ag-b").value = "bo"; localStorage.setItem("wider.reviewer", "carla");
const base = global.apiReply;
global.apiReply = (url) => {
  if (url.startsWith("/api/labels/agreement")) return { n: 2, agreement: 0, kappa: 0, disagreements: [{}, {}] };
  if (url.startsWith("/api/labels/compare")) return { locked: true, rows: [
    { record_id: 4, title: "a record", a: { decision: "include" }, b: { decision: "exclude" }, adjudicated: null },
    { record_id: 6, title: "another", a: { decision: "include" }, b: { decision: "exclude" }, adjudicated: { decision: "include", decided_by: "carla" } }] };
  return base(url);
};
await document.querySelector("#ag-run").onclick(); for (let i = 0; i < 4; i++) await flush();
const adj = () => calls.filter((c) => c[0] === "/api/labels/adjudicate").map((c) => c[1]);
openRow(0); for (let i = 0; i < 6; i++) await flush();
const opened = document.querySelector("#dw-decide").innerHTML;
listeners.keydown(key("2")); for (let i = 0; i < 6; i++) await flush();
const first = adj();
Drawer.close(true); for (let i = 0; i < 2; i++) await flush();
openRow(1); for (let i = 0; i < 6; i++) await flush();
ask = "UASK"; answer = { why: "second look at the method" };
listeners.keydown(key("2")); for (let i = 0; i < 12; i++) await flush();
console.log(JSON.stringify({ opened, first, all: adj(), dialogs }));""".replace("UASK", UNBLIND_ASK),
        files=("drawer.js", "records.js"), opening="")
    assert " disabled>" not in out["opened"] and "locked" not in out["opened"]
    assert len(out["first"]) == 1 and out["first"][0]["record_id"] == 4 and "correction_reason" not in out["first"][0]
    changed = out["all"][1:]
    assert [c.get("correction_reason") for c in changed] == [None, "second look at the method"]
    assert changed[0]["record_id"] == changed[1]["record_id"] == 6 and changed[1]["decision"] == "exclude"
    d = out["dialogs"][0]
    assert d["title"] == "Reason for this correction" and d["action"] == "Save correction"


# WCAG 2.1.4: the single keys belong to the drawer; elsewhere on the page they do nothing (fixes of 1 Nov 2026)
SCOPED = r"""
for (const s of ["#drawer", "#dd-pair", "#dd-list"]) document.querySelector(s).contains = (x) => !!x && x.within === s;
const outside = mk("#lb-load", "BUTTON"), inDrawer = mk("#dw-prev", "BUTTON"); inDrawer.within = "#drawer";
const press = async (k) => { listeners.keydown(key(k)); await flush(); timers.splice(0).forEach(([fn]) => fn()); await flush(); };
"""


def test_drawer_keys_do_nothing_while_the_focus_is_elsewhere_on_the_page():
    out = run_drawer(SCOPED + r"""
outside.focus(); await press("1"); await press("j");
const r = { fromOutside: saved.slice(), posOutside: pos() };
inDrawer.focus(); await press("1"); r.fromDrawer = saved.slice();
document.activeElement = document.body; await press("3"); r.fromBody = saved.slice();
console.log(JSON.stringify(r));""")
    assert out["fromOutside"] == [] and out["posOutside"] == "3 / 5"
    assert out["fromDrawer"] == [[3, "include"]]
    # over the page the drawer is modal: a focus that fell to the page body still counts as in it
    assert out["fromBody"] == [[3, "include"], [4, "maybe"]]


def test_a_docked_drawer_takes_keys_only_from_its_pair_or_its_list():
    out = run_drawer(SCOPED + r"""
const row = mk("tr", "TR"); row.within = "#dd-list";
document.activeElement = document.body; await press("1");
const r = { fromBody: saved.slice() };
outside.focus(); await press("1"); r.fromOutside = saved.slice();
row.focus(); await press("1"); r.fromList = saved.slice();
console.log(JSON.stringify(r));""", opening=OPEN.replace('notes: false,', 'notes: false, dock: "#dd-pair", table: "#dd-list",'))
    assert out["fromBody"] == [] and out["fromOutside"] == []
    assert out["fromList"] == [[3, "include"]]


def test_a_key_pressed_while_a_decision_saves_is_refused_with_a_count():
    out = run_drawer(r"""
let release; const gate = new Promise((r) => { release = r; });
const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3].map((id) => ({ id })), index: 0, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: ["include", "exclude"].map((d) => ({ value: d, label: d, fn: async (r) => { saved.push([r.id, d]); await gate; } })) });
await flush();
const m = () => document.querySelector("#dw-dec-msg");
listeners.keydown(key("1")); await flush();
listeners.keydown(key("2")); listeners.keydown(key("2")); await flush();
const r = { during: m().textContent, cls: m().className, savedDuring: saved.slice() };
release(); await flush(); await flush();
r.after = m().textContent; r.saved = saved.slice();
console.log(JSON.stringify(r));""", opening="")
    assert out["savedDuring"] == [[1, "include"]] and out["saved"] == [[1, "include"]]
    assert out["during"].endswith("(2 key presses ignored)") and "nothing more was decided" in out["during"]
    assert "dw-warn" in out["cls"]
    # the refusal stays on screen: the finished save does not write "saved" over it
    assert out["after"] == out["during"]


def test_the_same_decision_again_on_the_last_record_is_not_sent_twice():
    out = run_drawer(r"""
const m = () => document.querySelector("#dw-dec-msg").textContent;
const press = async (k) => { listeners.keydown(key(k)); await flush(); timers.splice(0).forEach(([fn]) => fn()); await flush(); };
await press("1"); await press("1"); await press("1");
const r = { twice: saved.slice(), msg: m(), pos: pos() };
await press("3"); r.other = saved.slice();
console.log(JSON.stringify(r));""", opening=OPEN.replace("index: 2", "index: 4"))
    assert out["pos"] == "5 / 5"
    assert out["twice"] == [[5, "include"]] and out["msg"] == "already saved as include: nothing more was sent"
    # another decision is a real change and is sent
    assert out["other"] == [[5, "include"], [5, "maybe"]]
