"""Tests asked for by the thirtieth round of C1 checks: the record-limit field, the Rank button and the two construct
save buttons are run in Node instead of matched as text; two saves from the page and the CLI at once; the explorer's
zero-count cell in anchor order with its page message."""
import sys
import threading

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_drawer_keys_node import STATIC, run_node
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core.project import Project
from wider.ui.app import create_app

APP = (STATIC / "app.js").read_text(encoding="utf-8")
RECORDS = (STATIC / "records.js").read_text(encoding="utf-8")
runner = CliRunner()

# a stand-in element; like a browser, a disabled button does not fire its click
ELEMENTS = r"""
const doc = { activeElement: null, body: { id: "body" } }; global.document = doc;
const els = {};
const $ = (s) => (els[s] ||= { id: s, value: "", textContent: "", innerHTML: "", disabled: false,
  focus() { doc.activeElement = this; }, click() { if (!this.disabled) return this.onclick(); } });
"""


def _handler(head: str) -> str:
    body = APP[APP.index(head):]
    return body[:body.index("\n};\n") + 3]


def test_the_record_limit_field_is_run_on_the_page():
    out = run_node(ELEMENTS + r"""
const started = [], errors = [];
const Jobs = { start: (path, opts) => { started.push(JSON.parse(opts.body).limit); return new Promise(() => {}); } };
const showStageError = (k, m) => errors.push(m), loadScreen = () => {};
let scPicked = "fake"; $("#sc-scope").value = "all"; $("#sc-pass").value = "A";
""" + APP[APP.index("function screenBody()"):APP.index("// ---------------- recall + report page")] + r"""
const res = {};
for (const typed of ["", "  ", "20", " 20 ", "0", "-5", "99999999999999999999999", "2.5", "x"]) {
  $("#sc-run").disabled = false; started.length = 0; errors.length = 0;
  $("#sc-limit").value = typed; $("#sc-run").onclick();
  res[typed] = [started.slice(), errors.slice()];
}
console.log(JSON.stringify(res));""")
    assert out[""] == [[None], []] and out["  "] == [[None], []]          # an empty field: every record
    assert out["20"] == [["20"], []] and out[" 20 "] == [["20"], []]      # digits go as typed text
    assert out["0"] == [["0"], []] and out["-5"] == [["-5"], []]          # the server refuses them in words
    assert out["99999999999999999999999"] == [["99999999999999999999999"], []]   # never 1e+23
    assert out["2.5"] == [[], ['the record limit must be a whole number, e.g. 20 (got "2.5")']]   # no request
    assert out["x"] == [[], ['the record limit must be a whole number, e.g. 20 (got "x")']]


def test_the_rank_button_sends_one_run_and_gives_the_keyboard_back():
    out = run_node(ELEMENTS + r"""
let rkOffset = 7; const asked = [], errors = []; let release;
const api = (u) => { asked.push(u); return new Promise((r) => { release = r; }); };
const showStageError = (k, m) => errors.push(m), showStageResult = () => {}, loadExplorer = async () => {};
let rkEmbedding = "fake"; $("#rk-anchors").value = "1";
""" + _handler('$("#rk-run").onclick') + r"""
const res = {};
$("#rk-topn").value = "2.5"; $("#rk-run").focus(); await $("#rk-run").click();
res.fraction = [asked.length, errors.slice()];
$("#rk-topn").value = ""; $("#rk-run").focus();
const first = $("#rk-run").click(); doc.activeElement = doc.body;   // the disabled button dropped the focus
$("#rk-run").click(); $("#rk-run").click();
res.during = [asked.slice(), $("#rk-run").disabled];
release({ stage_result: {} }); await first;
res.after = [$("#rk-run").disabled, doc.activeElement.id, rkOffset];
$("#rk-topn").value = "3"; const second = $("#rk-run").click(); release({ stage_result: {} }); await second;
res.withN = asked[1];
console.log(JSON.stringify(res));""")
    assert out["fraction"] == [0, ['top-N must be a whole number of cells, e.g. 2 (got "2.5")']]
    assert out["during"] == [["/api/rank/run?embedding=fake&anchors_version=1"], True]   # no top_n: the default
    assert out["after"] == [False, "#rk-run", 0]
    assert out["withN"] == "/api/rank/run?embedding=fake&anchors_version=1&top_n=3"


def test_a_double_click_on_either_save_button_saves_one_version():
    out = run_node(ELEMENTS + r"""
const asked = []; let release;
const api = (u) => { asked.push(u); return new Promise((r) => { release = r; }); };
let artKind = "cells"; const loadConstruct = async () => {}, loadQuery = async () => {}, esc = (s) => s;
const confirmSave = async () => true;   // refine.js's confirm step: no frozen query here, so nothing to confirm
""" + _handler('$("#art-save").onclick') + _handler('$("#q-import-save").onclick') + r"""
const res = {};
$("#art-text").value = "cells: []";
let first = $("#art-save").click(); $("#art-save").click(); $("#art-save").onclick();   // even a direct second call
await new Promise((r) => setImmediate(r));   // the request goes after the (empty) confirm step
res.art = [asked.slice(), $("#art-save").disabled];
release({ version: 2 }); await first; res.artAfter = $("#art-save").disabled;
asked.length = 0; $("#q-import-text").value = "TI voice";
first = $("#q-import-save").click(); $("#q-import-save").click();
await new Promise((r) => setImmediate(r));
res.query = [asked.slice(), $("#q-import-save").disabled];
release({ version: 3 }); await first; res.queryAfter = [$("#q-import-save").disabled, $("#q-import-msg").textContent];
console.log(JSON.stringify(res));""")
    assert out["art"] == [["/api/construct/cells/save"], True] and out["artAfter"] is False
    assert out["query"] == [["/api/query/import"], True] and out["queryAfter"] == [False, "saved synonyms v3"]


def test_a_save_from_the_page_and_one_from_the_cli_at_once_get_their_own_versions(tmp_path, monkeypatch):
    import yaml

    from tests.unit.test_construct import SYN
    root = Project.init(tmp_path / "r").root
    artifacts.save_new_version(Project.load(root), "synonyms", SYN, origin="import")   # v1, so both have a parent
    edited = tmp_path / "syn.yaml"
    edited.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    gate, orig = threading.Barrier(2), artifacts.next_version

    def together(d, suffix):   # both saves read the next version before either writes its file
        v = orig(d, suffix)
        if sys._getframe(1).f_code.co_name == "save_new_version":   # not the seen-choice check before it
            gate.wait(timeout=10)
        return v

    monkeypatch.setattr(artifacts, "next_version", together)
    got, errors = {}, []

    def page():
        try:
            r = TestClient(create_app(Project.load(root))).post("/api/construct/synonyms/save",
                                                                 json={"text": yaml.safe_dump(SYN)})
            got["page"] = (r.status_code, r.json())
        except Exception as e:   # noqa: BLE001 - the test reports any failure
            errors.append(repr(e))

    def cli():
        try:
            r = runner.invoke(cli_app, ["construct", "save", "synonyms", str(edited), "--dir", str(root)])
            got["cli"] = (r.exit_code, r.output)
        except Exception as e:   # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=page), threading.Thread(target=cli)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert errors == [] and got["page"][0] == 200 and got["cli"][0] == 0, got
    p = Project.load(root)
    assert sorted(x.name for x in p.artifact_dir("synonyms").glob("v*.yaml")) == ["v1.yaml", "v2.yaml", "v3.yaml"]
    assert sorted(r["version"] for r in artifacts.list_versions(p, "synonyms")) == [1, 2, 3]
    assert {got["page"][1]["version"], int(got["cli"][1].split(" v")[1].split()[0])} == {2, 3}


def test_a_cell_best_for_no_record_is_listed_in_anchor_order_and_said_on_the_page(tmp_path):
    from tests.unit.test_rank import _ranked
    from tests.unit.test_ui_followup_r17 import _page_render
    from wider.rank.score import rank
    p = _ranked(tmp_path, 12, 2, 50)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    p.store.execute("UPDATE ranking_rows SET best_cell='voice_cell'")   # every record closest to the first cell
    c = TestClient(create_app(p))
    d = c.get("/api/rank").json()
    assert list(d["best_cell_top3000"].items()) == [("voice_cell", 12), ("ocean_cell", 0)]   # anchor order, zero kept
    only = c.get("/api/rank", params={"cell": "ocean_cell"}).json()
    assert only["rows"] == [] and only["n_rows"] == 0
    out = _page_render(only, 0)   # the page's own loadExplorer on that reply
    assert out["page"] == "no record has ocean_cell as its best cell" and out["prev"] and out["next"]


def test_an_unknown_cell_is_refused_on_the_page_as_on_the_command_line(tmp_path):
    from tests.unit.test_ui_followup_r16 import _two_cells
    p = _two_cells(tmp_path)
    r = TestClient(create_app(p)).get("/api/rank", params={"cell": "nosuchcell"})
    assert r.status_code == 404 and r.json()["detail"] == "no cell nosuchcell; cells: voice_cell, ocean_cell"
    out = runner.invoke(cli_app, ["rank", "show", "--cell", "nosuchcell", "--dir", str(p.root)], terminal_width=200)
    assert out.exit_code == 1 and "no cell nosuchcell; cells: voice_cell, ocean_cell" in out.output


# ---- spec-checker, 30th round: the drawer on a page where an element exists only when the page or the drawer draws it

import json  # noqa: E402
import re  # noqa: E402

from tests.unit.test_drawer_keys_node import PAGE  # noqa: E402

INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
PAGE_IDS = sorted(set(re.findall(r'\bid="([^"]+)"', INDEX)))

# Replaces the stand-in's "any selector exists": an id is found only in index.html or in the HTML the drawer last
# wrote into its body, notes or decision box; an element written there is replaced by a new one on each re-render
STRICT = r"""
const PAGE_IDS = new Set(__IDS__), BOXES = ["#dw-body", "#dw-notes", "#dw-decide"], gen = {}, made = {};
const madeIn = (box, key, sel, tag) => { const k = `${box}|${key}|${gen[box]}`;
  if (!made[k]) { const e = mk(sel, tag); const g = gen[box]; e.within = box;
    Object.defineProperty(e, "isConnected", { get: () => gen[box] === g }); made[k] = e; }
  return made[k]; };
for (const b of BOXES) {
  let html = ""; gen[b] = 0;
  Object.defineProperty(els[b], "innerHTML", { get: () => html, set: (v) => { html = String(v); gen[b]++; } });
  els[b].querySelectorAll = (s) => { const m = /^\[data-([a-z]+)\]$/.exec(s); if (!m) return [];
    return [...html.matchAll(new RegExp(`<button[^>]*data-${m[1]}="([^"]*)"`, "g"))].map((x) => {
      const e = madeIn(b, `${m[1]}=${x[1]}`, s, "BUTTON"); e.dataset = { [m[1]]: x[1] }; return e; }); };
}
document.querySelector = (s) => {
  if (!s.startsWith("#")) return (els[s] ||= mk(s));
  if (s.includes(" ")) return null;   // rows inside page tables are not drawn here
  const id = s.slice(1);
  if (PAGE_IDS.has(id)) return (els[s] ||= mk(s, "DIV"));
  for (const b of BOXES) {
    const m = new RegExp(`<(\\w+)[^>]*\\bid="${id}"`).exec(els[b].innerHTML);
    if (m) return madeIn(b, s, s, m[1].toUpperCase());
  }
  return null;
};
global.fmtWhen = (s) => String(s);
""".replace("__IDS__", json.dumps(PAGE_IDS))


def _strict(body: str, files=("drawer.js",)) -> dict:
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in files)
    return run_node(f"{PAGE}\n{STRICT}\n{code}\n{body}")


def test_the_strict_page_has_no_drawer_field_until_the_drawer_draws_it():
    out = _strict(r"""
const r = { before: [document.querySelector("#dw-note"), document.querySelector("#dw-title")] };
Drawer.open({ label: "x", items: [{ id: 1 }], render: async () => "<p>no title here</p>" }); await flush(); await flush();
r.after = [document.querySelector("#dw-note"), document.querySelector("#dw-title"), document.activeElement.sel];
console.log(JSON.stringify(r));""")
    assert out == {"before": [None, None], "after": [None, None, "#drawer"]}   # nothing drawn: the drawer holds the focus


NOTES_REPLY = r"""
global.apiReply = (u) => u.startsWith("/api/records/") ? { notes: [], tags: [] } : u.startsWith("/api/history") ? [] : {};
"""


def test_n_or_t_pressed_while_the_next_record_loads_focuses_the_field_the_drawer_draws():
    out = _strict(NOTES_REPLY + r"""
const res = {};
for (const k of ["n", "t"]) {
  let release = null;
  Drawer.open({ label: "Labelling", items: [1, 2, 3].map((id) => ({ id })), index: 0, recordId: (r) => r.id,
    render: async (r) => { if (r.id > 1) await new Promise((ok) => { release = ok; }); return `<h2 id="dw-title">record ${r.id}</h2>`; } });
  for (let i = 0; i < 4; i++) await flush();
  listeners.keydown(key("j")); await flush();
  const during = document.activeElement.sel;
  listeners.keydown(key(k)); await flush();
  release(); for (let i = 0; i < 6; i++) await flush();
  const a = document.activeElement;
  res[k] = [pos(), during, a.sel, a.tagName, a.within, a.isConnected];
  Drawer.close(true);
}
console.log(JSON.stringify(res));""")
    assert out["n"] == ["2 / 3", "#drawer", "#dw-note", "TEXTAREA", "#dw-notes", True]
    assert out["t"] == ["2 / 3", "#drawer", "#dw-tag", "INPUT", "#dw-notes", True]


def test_a_decision_button_the_next_record_replaces_hands_the_focus_to_the_new_title():
    out = _strict(r"""
const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3, 4, 5].map((id) => ({ id })), index: 2, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: ["include", "exclude", "maybe"].map((d) => ({ value: d, label: d, fn: async (r) => { saved.push([r.id, d]); } })) });
await flush(); await flush();
const btn = document.querySelector("#dw-decide").querySelectorAll("[data-dec]")[1];
btn.focus(); btn.onclick(); await flush();
const r = { during: [document.activeElement === btn, saved.slice()] };
timers.splice(0).forEach(([fn]) => fn()); for (let i = 0; i < 4; i++) await flush();
const a = document.activeElement;
r.after = [pos(), a.sel, a.within, a.isConnected, btn.isConnected];
console.log(JSON.stringify(r));""")
    assert out["during"] == [True, [[3, "exclude"]]]
    assert out["after"] == ["4 / 5", "#dw-title", "#dw-body", True, False]   # the pressed button is gone, not the body


def test_next_on_the_last_but_one_record_and_k_or_next_on_a_kept_control():
    out = _strict(r"""
Drawer.open({ label: "Reading", items: [1, 2, 3, 4, 5].map((id) => ({ id })), index: 3, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush(); await flush();
const next = document.querySelector("#dw-next"), close = document.querySelector("#dw-close");
const r = {};
next.focus(); next.onclick(); for (let i = 0; i < 4; i++) await flush();
r.next = [pos(), next.disabled, document.activeElement.sel];
close.focus(); listeners.keydown(key("k")); for (let i = 0; i < 4; i++) await flush();
r.k = [pos(), document.activeElement.sel];
next.focus(); next.onclick(); for (let i = 0; i < 4; i++) await flush();
r.nextKept = [pos(), next.disabled, document.activeElement.sel];
console.log(JSON.stringify(r));""")
    assert out["next"] == ["5 / 5", True, "#dw-title"]   # Next turned disabled: the title, never the body
    assert out["k"] == ["4 / 5", "#dw-close"]
    assert out["nextKept"] == ["5 / 5", True, "#dw-title"]


LABELLING = r"""
const rows = [1, 2, 3, 4, 5].map((id) => ({ record_id: id, sample_id: "s1", title: `record ${id}`, decision: null }));
global.apiReply = (u) => { if (u.startsWith("/api/labels/queue")) return rows; if (u.startsWith("/api/annotations/summary")) return {};
  if (u.startsWith("/api/records/")) return { notes: [], tags: [] }; if (u === "/api/labels") return { change_id: 9 }; return []; };
"""


def test_the_labelling_queue_saves_the_key_s_decision_through_its_own_request():
    out = _strict(LABELLING + (STATIC / "records.js").read_text(encoding="utf-8") + r"""
localStorage.setItem("wider.reviewer", "ana"); document.querySelector("#lb-filter").value = "all";
document.querySelector("#lb-load").onclick(); for (let i = 0; i < 6; i++) await flush();
listeners.keydown(key("j")); for (let i = 0; i < 4; i++) await flush();
listeners.keydown(key("j")); for (let i = 0; i < 4; i++) await flush();
const before = pos(); calls.length = 0;
listeners.keydown(key("2")); for (let i = 0; i < 4; i++) await flush();
const r = { before, posts: calls.filter(([u]) => u === "/api/labels"), msg: document.querySelector("#dw-dec-msg").textContent,
  progress: document.querySelector("#lb-progress").textContent, row: rows[2].decision };
timers.splice(0).forEach(([fn]) => fn()); for (let i = 0; i < 4; i++) await flush();
r.after = pos();
console.log(JSON.stringify(r));""", files=("drawer.js",))
    assert out["before"] == "3 / 5"
    assert out["posts"] == [["/api/labels", {"sample_id": "s1", "record_id": 3, "reviewer": "ana", "decision": "exclude",
                                             "reason": "", "labels": {}, "note": ""}]]
    assert out["msg"] == "saved: exclude" and out["row"] == "exclude"
    assert out["progress"] == "1 of 5 labelled · showing 5" and out["after"] == "4 / 5"


def test_the_pair_drawer_hides_the_note_keys_and_the_labelling_drawer_shows_them():
    out = _strict(LABELLING + (STATIC / "records.js").read_text(encoding="utf-8") + r"""
const hint = () => [document.querySelector(".dw-keys-notes").hidden, document.querySelector(".dw-keys-decide").hidden];
localStorage.setItem("wider.reviewer", "ana"); document.querySelector("#lb-filter").value = "all";
document.querySelector("#lb-load").onclick(); for (let i = 0; i < 6; i++) await flush();
console.log(JSON.stringify({ labelling: hint() }));""")
    # the labelling drawer shows the note and tag keys; the docked pair view of the Deduplication page hides them
    # (test_dedup_page_node.py::test_the_pair_view_header_buttons_and_signing_name)
    assert out["labelling"] == [False, False]
    assert ">n<" in INDEX.split('class="dw-keys-notes"')[1].split("</span>")[0] or "note" in INDEX.split('class="dw-keys-notes"')[1].split("</span>")[0]


def test_a_long_name_s_own_note_and_tag_carry_the_remove_button_and_another_s_does_not():
    long = "a" * 105
    out = _strict(r"""
const me = "LONG", mine = me.slice(0, 80);
global.apiReply = (u) => u.startsWith("/api/records/") ? { notes: [{ note_id: 1, author: mine, text: "mine", created_at: "t" },
  { note_id: 2, author: "bo", text: "theirs", created_at: "t" }], tags: [{ tag: "keep", author: mine }, { tag: "other", author: "bo" }] } : [];
localStorage.setItem("wider.reviewer", me);
Drawer.open({ label: "Labelling queue (blind)", items: [{ id: 3 }], blind: true, recordId: (r) => r.id,
  render: async () => `<h2 id="dw-title">record</h2>` });
for (let i = 0; i < 6; i++) await flush();
const html = document.querySelector("#dw-notes").innerHTML;
console.log(JSON.stringify({ asked: calls.map(([u]) => u), del: [...html.matchAll(/data-delnote="(\d+)"/g)].map((m) => m[1]),
  untag: [...html.matchAll(/data-untag="([^"]+)"/g)].map((m) => m[1]), blindMark: html.includes("(yours only, blind mode)") }));
""".replace("LONG", long))
    assert out["asked"] == [f"/api/records/3?author={long}&blind=true"]
    assert out["del"] == ["1"] and out["untag"] == ["keep"] and out["blindMark"] is True


def test_the_drawer_signs_with_the_queue_name_then_the_name_wider_works_as():
    # ui-redesign D33: a queue's own name (cfg.author) signs first, otherwise the name WIDER works as; no name field
    out = _strict(NOTES_REPLY + r"""
global.apiReply = (u, o) => u.endsWith("/tags") ? { change_id: 1, tag: "t1" } : u.startsWith("/api/records/") ? { notes: [], tags: [] } : [];
localStorage.setItem("wider.reviewer", "cy");
const r = {};
for (const [k, author] of [["queue", "ana"], ["reading", undefined]]) {
  calls.length = 0;
  Drawer.open({ label: "Reading", items: [{ id: 4 }], author, recordId: (r) => r.id, render: async () => `<h2 id="dw-title">r</h2>` });
  for (let i = 0; i < 6; i++) await flush();
  const tag = document.querySelector("#dw-tag"); tag.value = "t1";
  await tag.onkeydown({ key: "Enter", preventDefault() {}, target: tag });
  r[k] = { field: !!document.querySelector("#dw-author"), me: Drawer.author(),
           tagPost: calls.filter(([u]) => u.endsWith("/tags")).map(([, b]) => b.author) };
  Drawer.close(true);
}
r.remembered = localStorage.getItem("wider.reviewer");
console.log(JSON.stringify(r));""")
    assert out["queue"] == {"field": False, "me": "ana", "tagPost": ["ana"]}
    assert out["reading"] == {"field": False, "me": "cy", "tagPost": ["cy"]}
    assert out["remembered"] == "cy"   # signing never rewrites the stored name


# ---- spec-checker, 30th round: the skip link, the message areas, the toasts of History and the collapsed sidebar

def test_the_skip_link_moves_the_keyboard_to_the_content_and_keeps_the_page():
    line = next(x for x in APP.splitlines() if x.startswith('document.querySelector(".skip-link")'))
    out = run_node(r"""
const focused = [], shown = []; let handler = null;
const main = { focus() { focused.push("#main"); } };
global.location = { hash: "#rank" };
const showPage = (k) => shown.push(k), $ = (s) => (s === "#main" ? main : null);
global.document = { querySelector: (s) => (s === ".skip-link" ? { addEventListener: (t, fn) => { handler = [t, fn]; } } : null) };
""" + line + r"""
const ev = { prevented: false, preventDefault() { this.prevented = true; } };
handler[1](ev);
console.log(JSON.stringify({ type: handler[0], prevented: ev.prevented, focused, hash: location.hash, shown }));""")
    assert out == {"type": "click", "prevented": True, "focused": ["#main"], "hash": "#rank", "shown": []}
    assert '<a class="skip-link" href="#main">Skip to content</a>' in INDEX and 'id="main"' in INDEX


MSG_AREA = re.compile(r'<(span|p|div|section)\b[^>]*\bid="[A-Za-z0-9_-]*-msg"[^>]*>')


def test_every_message_area_is_read_out_when_it_changes():
    found = []
    for f in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.js")):
        for m in MSG_AREA.finditer(f.read_text(encoding="utf-8")):
            found.append((f.name, m.group(0)))
    assert len(found) >= 20   # the scan sees the page's and the scripts' message areas
    silent = [(f, tag) for f, tag in found if not re.search(r'\brole="(status|alert)"', tag)]
    assert silent == []


def test_a_refused_undo_and_a_refused_restore_are_danger_toasts():
    out = run_node(PAGE + r"""
const toasts = []; global.Jobs = { toast: (t, tone) => { toasts.push([t, tone]); return { remove() {} }; } };
global.CustomEvent = class { constructor(t) { this.type = t; } }; global.dispatchEvent = () => {};
global.fmtWhen = (s) => s; global.artKind = "codebook";
global.api = async (url) => { calls.push(url); const e = new Error(url.includes("restore") ? "version 2 of codebook does not exist" : "change 5 cannot be undone: a later change depends on it"); e.status = 409; throw e; };
""" + (STATIC / "history.js").read_text(encoding="utf-8") + r"""
const r = { undo: await Undo.undo(5) };
document.querySelector("#art-version").value = "2"; await document.querySelector("#art-restore").onclick();
localStorage.setItem("wider.reviewer", "ana"); await document.querySelector("#hist-undo-mine").onclick();
r.calls = calls; r.toasts = toasts;
console.log(JSON.stringify(r));""")
    assert out["undo"] is None
    assert out["calls"] == ["/api/history/5/undo", "/api/construct/codebook/2/restore", "/api/history/undo-last?author=ana"]
    assert out["toasts"] == [["change 5 cannot be undone: a later change depends on it", "danger"],
                             ["version 2 of codebook does not exist", "danger"],
                             ["change 5 cannot be undone: a later change depends on it", "danger"]]


def test_the_history_table_says_why_a_change_cannot_be_undone():
    out = run_node(PAGE + r"""
global.Jobs = { toast: () => ({ remove() {} }) }; global.fmtWhen = (s) => s;
global.apiReply = () => [{ change_id: 7, at: "t", author: "ana", kind_label: "label", summary: "label record 3",
  undoable: false, why_not: "a later change depends on it" }, { change_id: 8, at: "t", author: "ana", kind_label: "tag",
  summary: "tag record 4", undoable: true }];
""" + (STATIC / "history.js").read_text(encoding="utf-8") + r"""
await loadHistory();
console.log(JSON.stringify({ html: document.querySelector("#hist-table").innerHTML, msg: document.querySelector("#hist-msg").textContent }));""")
    assert "cannot undo: a later change depends on it" in out["html"]
    assert 'data-undo="8"' in out["html"] and 'data-undo="7"' not in out["html"] and out["msg"] == "2 changes"


def test_the_collapsed_sidebar_keeps_each_link_s_name_for_a_screen_reader():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    rules = [r for r in re.findall(r"[^{}]+\{[^}]*\}", css) if "sidebar-collapsed" in r and ".label" in r]
    assert rules
    for r in rules:
        assert "display:none" not in r.replace(" ", "") and "visibility:hidden" not in r.replace(" ", "")
    label = " ".join(rules).replace(" ", "")
    assert "position:absolute" in label and "clip:" in label and "width:1px" in label
    assert re.search(r'class="nav-link[^"]*"[^>]*>.*?<span class="label">', INDEX, re.S) or 'class="label"' in (STATIC / "app.js").read_text(encoding="utf-8")


# ---- spec-checker, 30th round: the engines and commands behind the scenarios, run instead of read


def test_the_dedup_queue_command_counts_decisions_made_since_the_last_run(tmp_path):
    from tests.unit.test_records import _deduped
    from wider.dedup.engine import decide, decided_since_run, queue_rows
    from wider.dedup.engine import run as dedup_run
    p, _ = _deduped(tmp_path)
    q = queue_rows(p)[0]
    pair = (q["raw_a"], q["raw_b"])
    out = runner.invoke(cli_app, ["dedup", "queue", "--dir", str(p.root)])
    assert out.exit_code == 0 and "decided since the last run" not in out.output
    decide(p, *pair, "duplicate", by="ana")
    out = runner.invoke(cli_app, ["dedup", "queue", "--dir", str(p.root)])
    line = " ".join(out.output.split())
    assert out.exit_code == 0 and "· 1 decided since the last run (run `wider dedup run` to apply)" in line
    # the run applies it; a change of mind counts again, and going back to what the run applied counts as nothing
    dedup_run(p)
    assert decided_since_run(p) == 0
    out = runner.invoke(cli_app, ["dedup", "queue", "--dir", str(p.root)])   # after the run the note is gone
    assert out.exit_code == 0 and "decided since the last run" not in out.output
    decide(p, *pair, "not_duplicate", by="ana")
    assert decided_since_run(p) == 1
    decide(p, *pair, "duplicate", by="ana")
    assert decided_since_run(p) == 0


def test_undo_last_skips_a_freeze_and_undoes_the_reviewer_s_older_change(tmp_path, monkeypatch):
    from tests.unit.test_records import _deduped
    from wider.core import annotations as ann
    from wider.core import journal
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "ana", "an older change")
    monkeypatch.setenv("WIDER_REVIEWER", "ben")
    row = artifacts.save_new_version(p, "anchors", {"anchors": {"a": "Studies in which employees speak up to managers about problems at work and the reasons they do. " * 6}}, origin="manual")
    monkeypatch.setenv("WIDER_REVIEWER", "ana")
    artifacts.set_status(p, "anchors", row["version"], "frozen")
    assert journal.changes(p)[0]["kind"] == "artifact_status" and journal.changes(p)[0]["author"] == "ana"
    u = journal.undo_last(p, "ana")
    assert p.store.scalar("SELECT COUNT(*) FROM record_notes WHERE record_id=?", (ids[0],)) == 0   # the note is gone
    assert artifacts.latest(p, "anchors")["status"] == "frozen"                               # the freeze stays
    assert u["kind"] == "undo"


def test_a_settings_summary_names_six_keys_and_counts_the_rest(tmp_path):
    from wider.core import journal
    before = "\n".join(f"k{i}: {i}" for i in range(8)) + "\n"
    after = "\n".join(f"k{i}: {i + 10}" for i in range(8)) + "\n"
    s = journal.config_diff(before, after)
    assert s.count(" → ") == 6 and s.endswith("; and 2 more")
    assert all(f"k{i}: {i} → {i + 10}" in s for i in range(6))   # each as "key: old → new"
    assert journal.config_diff(before, before) == "project.yaml rewritten"


def test_a_settings_save_inside_another_change_is_part_of_that_change(tmp_path):
    from wider.core import journal
    p = Project.init(tmp_path / "r")
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    with journal.recording(p, "construct", "set the cluster count", author="ana"):
        p.config.protocol.ranking.cluster_k = p.config.protocol.ranking.cluster_k + 1
        p.save()
    rows = journal.changes(p)
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n + 1          # one change, no separate settings change
    assert rows[0]["summary"] == "set the cluster count" and rows[0]["touches"] == ["project.yaml"]
    # outside a change, the same save is a settings change of its own, summarised by key
    p.config.protocol.ranking.cluster_k = p.config.protocol.ranking.cluster_k + 1
    p.save()
    top = journal.changes(p)[0]
    assert top["kind"] == "config" and top["summary"].startswith("protocol.ranking.cluster_k: ")


def test_google_scholar_with_no_required_block_gives_no_string():
    from wider.search.ir import Block, Query
    from wider.search.render import platforms
    from wider.search.render.reduced import render_gscholar
    optional = Query(blocks=[Block(key="a", terms=["alpha"], role="optional"), Block(key="b", terms=["beta"], role="optional")],
                     logic={"and": ["a", "b"]})
    excluded = Query(blocks=[Block(key="a", terms=["alpha"]), Block(key="b", terms=["beta"])],
                     logic={"not": {"or": ["a", "b"]}})
    assert render_gscholar(optional) == "" and render_gscholar(excluded) == ""
    assert "Excluded (NOT) blocks are left out too." in " ".join(platforms()["gscholar"]["paste"].split())


def test_the_screen_command_refuses_a_limit_too_large_and_a_long_pass_name(tmp_path, monkeypatch):
    from wider.screen import engine
    p = Project.init(tmp_path / "r")
    def no_call(*a, **k):
        raise AssertionError("no prompt or model is loaded for a refused run")
    monkeypatch.setattr(engine, "load_prompt", no_call)
    monkeypatch.setattr(engine, "research_provider", no_call)
    out = runner.invoke(cli_app, ["screen", "run", "corpus", "--limit", "99999999999", "--dir", str(p.root)])
    assert out.exit_code == 1 and "the record limit must be at most 1,000,000,000 (got 99999999999)" in " ".join(out.output.split())
    out = runner.invoke(cli_app, ["screen", "run", "corpus", "--pass", "A" * 21, "--dir", str(p.root)])
    assert out.exit_code == 1 and "a pass name is 1-20 letters" in " ".join(out.output.split())
    assert "Traceback" not in out.output


def test_ablation_with_the_validation_set_fills_every_column(tmp_path):
    from tests.unit.test_rank import _ris
    from wider.core.models import BenchmarkPaper, EmbeddingConfig
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.ablate import ablate
    from wider.rank.corpus import embed_corpus
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / "a.ris").write_text(_ris(30))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="x", doi="10.1000/p3", set="Validation"),
                       BenchmarkPaper(benchmark_id="B3", title="x", doi="10.1000/none", set="Validation")])
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence speaking up " * 10}}, origin="manual")
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["voice"]}],
                                               "logic": {"and": ["a"]}}, origin="manual", status="frozen")
    rows = ablate(p, top_ns=(1,), include_validation=True)
    assert len(rows) == 1
    r = rows[0]
    for pre in ("dev", "val"):
        assert isinstance(r[f"{pre}_median_rank"], (int, float)) and r[f"{pre}_median_rank"] >= 1
        assert isinstance(r[f"{pre}_max_rank"], (int, float)) and r[f"{pre}_max_rank"] >= r[f"{pre}_median_rank"]
        assert isinstance(r[f"{pre}_recall_at_250"], (int, float))
    assert r["dev_found"] == "1/1" and r["val_found"] == "1/2"   # the paper not in the corpus is counted, not found
    out = runner.invoke(cli_app, ["rank", "ablate", "--validation", "--dir", str(p.root)], env={"COLUMNS": "400"})
    assert out.exit_code == 0
    from wider.rank.ablate import column_label
    lines = [x.split("┃")[1:-1] for x in out.output.splitlines() if x.startswith("┃")]   # a long header wraps
    head = [" ".join(c[i].strip() for c in lines if c[i].strip()) for i in range(len(lines[0]))]
    row = next(x for x in out.output.splitlines() if "1/2" in x)
    cells = dict(zip(head, [c.strip() for c in row.split("│")[1:-1]]))
    for col in ("dev_median_rank", "dev_max_rank", "dev_recall_at_250", "dev_found",
                "val_median_rank", "val_max_rank", "val_recall_at_250", "val_found"):
        # the readable header (as on the page) and the same value as the table on the page
        assert cells[column_label(col)] == str(r[col]), (col, cells, r[col])


def _report_button(name: str) -> str:
    return next(x for x in APP.splitlines() if x.startswith(f'$("{name}").onclick'))


def test_the_share_bundle_pressed_twice_sends_one_request_and_a_failed_build_gives_the_button_back():
    out = run_node(ELEMENTS + r"""
const calls = [], errors = []; let release, fail = false;
const api = (path) => { calls.push(path); if (fail) return Promise.reject(new Error("no screening decisions yet"));
  return new Promise((r) => { release = r; }); };
const reportParams = () => "", showStageResult = () => {}, afterAction = () => {};
const showStageError = (k, m) => errors.push([k, m]);
""" + _report_button("#rp-bundle") + "\n" + _report_button("#rp-build") + r"""
(async () => {
  const res = {};
  const first = $("#rp-bundle").click(); $("#rp-bundle").click();
  res.bundle = [calls.slice(), $("#rp-bundle").disabled];
  doc.activeElement = doc.body; release({ stage_result: {} }); await first;
  res.bundleAfter = [$("#rp-bundle").disabled, doc.activeElement.id];
  fail = true; calls.length = 0; doc.activeElement = doc.body;
  await $("#rp-build").click();
  res.build = [calls.slice(), $("#rp-build").disabled, doc.activeElement.id, $("#rp-msg").textContent, errors];
  console.log(JSON.stringify(res));
})();
""")
    assert out["bundle"] == [["/api/report/bundle"], True]
    assert out["bundleAfter"] == [False, "#rp-bundle"]
    assert out["build"] == [["/api/report"], False, "#rp-build", "", [["recall", "no screening decisions yet"]]]


def test_fetch_after_a_cancelled_dialog_or_a_failed_start_asks_again():
    body = APP[APP.index("let enStarting = false;"):]
    body = body[:body.index("\n}\n") + 3]
    out = run_node(ELEMENTS + r"""
const enLimit = () => 100, enBatch = 100, enRecords = () => {}, renderEnrich = () => {}, errors = [];
const showStageError = (k, m) => errors.push(m);
const paths = []; let answer = "cancel";
const Jobs = { start: (path) => { paths.push(path);
  if (answer === "cancel") { const e = new Error("Nothing was sent: online lookups are not allowed for this project."); e.cancelled = true; return Promise.reject(e); }
  if (answer === "fail") return Promise.reject(new Error("the server stopped"));
  return Promise.resolve({}); } };
""" + body + r"""
$("#en-run").onclick = () => startEnrich(false);
(async () => {
  await $("#en-run").click(); const cancelled = [paths.length, $("#en-msg").textContent];
  answer = "fail"; await $("#en-run").click(); const failed = [paths.length, errors.slice()];
  answer = "allow"; await $("#en-run").click();
  console.log(JSON.stringify({ cancelled, failed, allowed: [paths.length, $("#en-msg").textContent] }));
})();
""")
    assert out["cancelled"] == [1, "Nothing was sent: online lookups are not allowed for this project."]
    assert out["failed"] == [2, ["the server stopped"]]
    assert out["allowed"] == [3, "Fetching in the background; see the bar at the top."]
    assert '$("#en-run").onclick = () => startEnrich(false);' in APP


def test_the_split_follows_the_project_s_development_share(tmp_path):
    from wider.construct import seed
    p = Project.init(tmp_path / "r")
    seed.add_by_dois(p, [f"10.1000/synth.{i:03d}" for i in range(1, 11)])
    got = {}
    for share in (0.3, 0.8):
        p.config.protocol.identification.development_share = share
        p.save()
        s = seed.suggest_split(Project.find(p.root))
        got[share] = (len(s["development"]), len(s["validation"]), s["dev_share"])
    assert got == {0.3: (3, 7, 0.3), 0.8: (8, 2, 0.8)}
    for share, words in ((0.3, ("3 development / 7 validation", "dev share 0.30")),
                         (0.8, ("8 development / 2 validation", "dev share 0.80"))):
        p.config.protocol.identification.development_share = share
        p.save()
        out = runner.invoke(cli_app, ["benchmark", "split", "--dir", str(p.root)])   # the command line reads the same share
        assert out.exit_code == 0 and all(w in out.output for w in words), (share, out.output)


SETUP = (STATIC / "setup.js").read_text(encoding="utf-8")


def test_down_twice_takes_the_first_platform_to_the_bottom_and_keeps_the_keyboard_on_its_row():
    from tests.unit.test_query_roles import js_function
    handler = SETUP[SETUP.index('box.querySelectorAll("[data-move]")'):]
    handler = handler[:handler.index("}, msg));") + 9]
    out = run_node(r"""
const d = { chosen: [{ key: "scopus" }, { key: "wos" }, { key: "ebsco" }] };
const posts = []; let focused = null;
// the table as drawn: Up and Down per row, Up disabled on the first row and Down on the last
let buttons = [];
const draw = () => { buttons = d.chosen.flatMap((c, i) => [["-1", i === 0], ["1", i === d.chosen.length - 1]].map(([dir, disabled]) =>
  ({ dataset: { move: c.key, dir }, disabled, focus() { focused = `${c.key} ${dir}`; } }))); };
const box = { querySelectorAll: () => buttons };
const q = () => box, wrap = (fn) => fn, msg = null;
const post = async (path, body) => { posts.push(body.keys.join(",")); d.chosen = body.keys.map((key) => ({ key })); };
const loadDatabases = async () => { draw(); bind(); };
const bind = () => {
""" + handler + r"""
};
""" + js_function(SETUP, "function focusMoved(") + r"""
(async () => {
  draw(); bind();
  await buttons.find((b) => b.dataset.move === "scopus" && b.dataset.dir === "1").onclick();
  const once = [d.chosen.map((c) => c.key).join(","), focused];
  await buttons.find((b) => b.dataset.move === "scopus" && b.dataset.dir === "1").onclick();
  console.log(JSON.stringify({ posts, once, twice: [d.chosen.map((c) => c.key).join(","), focused] }));
})();
""")
    assert out["posts"] == ["wos,scopus,ebsco", "wos,ebsco,scopus"]
    assert out["once"] == ["wos,scopus,ebsco", "scopus 1"]      # still movable down: the keyboard stays on Down
    assert out["twice"] == ["wos,ebsco,scopus", "scopus -1"]    # at the bottom: Down is disabled, so its Up button


def test_retry_with_lookups_off_opens_the_consent_dialog_through_the_real_request():
    jobs = (STATIC / "jobs.js").read_text(encoding="utf-8")
    start = jobs[jobs.index("    async start(path"):]
    start = start[:start.index("\n    },\n") + 7]
    body = APP[APP.index("let enStarting = false;"):]
    body = body[:body.index("\n}\n") + 3]
    out = run_node(r"""
global.window = global;
global.document = { activeElement: null, addEventListener() {} };
global.setTimeout = () => 0; global.clearTimeout = () => {};
const sent = [];
global.fetch = async (path) => { sent.push(path);
  return { ok: false, status: 403, statusText: "Forbidden",
           json: async () => ({ detail: { consent_required: { service: "OpenAlex" }, message: "allow?" } }) }; };
""" + (STATIC / "common.js").read_text(encoding="utf-8") + r"""
const asked = []; askLookupConsent = async (info) => { asked.push(info.service || JSON.stringify(info)); return false; };
const call = api, callbacks = {}, active = {}, poll = async () => {};
const Jobs = {
""" + start + r"""
};
const els = {}; const $ = (s) => (els[s] ||= { textContent: "" });
const enLimit = () => 100, enBatch = 100, enRecords = () => {}, renderEnrich = () => {}, errors = [];
const showStageError = (k, m) => errors.push(m);
""" + body + r"""
(async () => {
  await startEnrich(true);
  const first = [sent.splice(0), asked.slice(), els["#en-msg"].textContent, errors.slice()];
  await startEnrich(true);
  console.log(JSON.stringify({ first, again: [sent.splice(0), asked.length] }));
})();
""")
    assert out["first"][0] == ["/api/jobs/enrich?limit=100&retry=true"]   # one blocked request, nothing sent after Cancel
    assert len(out["first"][1]) == 1                                       # the consent dialog was opened
    assert out["first"][2].startswith("Nothing was sent") and out["first"][3] == []
    assert out["again"] == [["/api/jobs/enrich?limit=100&retry=true"], 2]  # pressed again, it asks again


def test_notes_and_tags_in_the_queue_are_signed_with_the_name_the_queue_was_loaded_for():
    from tests.unit.test_drawer_keys_node import run_drawer
    lb = RECORDS[RECORDS.index("function lbOpen"):]
    assert "blind: true, author: owner," in lb[:lb.index("\n  }\n")]
    out = run_drawer(r"""
global.apiReply = (url) => (url.includes("/notes") || url.includes("/tags") ? { change_id: 3, tag: "later" } : { notes: [], tags: [] });
localStorage.setItem("wider.reviewer", "ana");
Drawer.open({ label: "Labelling queue (blind)", items: [{ id: 9 }], index: 0, blind: true, author: "ana",
  recordId: (r) => r.id, render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush();
localStorage.setItem("wider.reviewer", "bo");   // another name stored (another tab) while ana's queue is shown
await document.querySelector("#dw-note").onkeydown({ key: "Enter", shiftKey: false, preventDefault() {}, target: { value: "check" } });
await flush();
await document.querySelector("#dw-tag").onkeydown({ key: "Enter", preventDefault() {}, target: { value: "later" } });
await flush();
console.log(JSON.stringify({ signed: calls.filter(([, b]) => b).map(([u, b]) => [u, b.author]),
  read: calls.filter(([u, b]) => !b && u.startsWith("/api/records/9?")).map(([u]) => u)[0], me: Drawer.author() }));""",
                     opening="")
    assert out["signed"] == [["/api/records/9/notes?blind=true", "ana"], ["/api/records/9/tags", "ana"]]
    assert out["read"] == "/api/records/9?author=ana&blind=true" and out["me"] == "ana"


def test_history_writes_why_a_change_cannot_be_undone_in_its_own_row():
    history = (STATIC / "history.js").read_text(encoding="utf-8")
    load = history[history.index("  window.loadHistory = async function () {"):]
    load = load[:load.index("\n  };\n") + 5]
    out = run_node(r"""
global.window = global; global.document = { activeElement: null };
const els = {}; const q = (s) => (els[s] ||= { value: "", checked: false, textContent: "", innerHTML: "", querySelectorAll: () => [] });
const h = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"), fmtWhen = (s) => s, me = () => "ana";
const blindOn = () => false;
const call = async () => [
  { change_id: 2, at: "t2", author: "ana", kind_label: "note", summary: "note added", undoable: false,
    why_not: "a later change by ben depends on it" },
  { change_id: 1, at: "t1", author: "ana", kind_label: "tag", summary: "tagged", undoable: true }];
""" + load + r"""
(async () => { await loadHistory(); console.log(JSON.stringify(els["#hist-table"].innerHTML.split("<tr").slice(2))); })();
""")
    assert "note added" in out[0] and "cannot undo: a later change by ben depends on it" in out[0]   # read with its row
    assert "cannot undo" not in out[1] and 'aria-label="Undo change 1: tagged"' in out[1]
