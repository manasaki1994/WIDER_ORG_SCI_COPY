"""One name, asked once (ui-redesign D33): the ``WiderName`` block of common.js runs under Node on a small stand-in page
(the top bar slot, the Home card, a stub name dialog), and the pages are checked for name fields of their own."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")
COMMON = (STATIC / "common.js").read_text(encoding="utf-8")
BLOCK = COMMON[COMMON.index("// --- WiderName ---"):COMMON.index("// --- end WiderName ---")]

# A stand-in page: elements by id, document and window events, a localStorage, a counted reload, and a name dialog
# that records its html and answers with the next queued reply (null = Cancel / Not now).
PAGE = r"""
const esc = (s) => String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, hidden: false, innerHTML: "", value: "", onclick: null, onkeydown: null,
    querySelector(sel) { if (!sel.startsWith("#") || !this.innerHTML.includes(`id="${sel.slice(1)}"`)) return null; return el(sel.slice(1)); } };
  return els[id];
}
const docEvents = {}, winEvents = {}, fired = [];
global.CustomEvent = class { constructor(type, o) { this.type = type; this.detail = o && o.detail; } };
global.document = {
  getElementById: (id) => (SLOTS.includes(id) || els[id] ? el(id) : null),
  addEventListener: (t, f) => { (docEvents[t] = docEvents[t] || []).push(f); },
  dispatchEvent: (e) => { fired.push(e.type); (docEvents[e.type] || []).forEach(f => f(e)); },
};
global.window = { addEventListener: (t, f) => { (winEvents[t] = winEvents[t] || []).push(f); } };
const storage = {};
global.localStorage = { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); },
  removeItem: (k) => { delete storage[k]; } };
global.location = { reloads: 0, reload() { this.reloads++; } };
const dialogs = [], replies = [];
global.askChoice = (html, opts) => { dialogs.push({ html, opts }); const r = replies.shift(); return Promise.resolve(r === undefined ? null : r); };
const click = (target) => (docEvents.click || []).forEach(f => f({ target, preventDefault() {} }));
const button = (attrs) => ({ closest: (sel) => (sel === "[data-set-name]" && attrs.includes("data-set-name") ? {} : null) });
const tick = () => new Promise(r => setImmediate(r));
"""


def run(body: str, stored: str | None = None, slots=("tb-name", "home-name")) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    pre = f"const SLOTS = {json.dumps(list(slots))};\n{PAGE}\n"
    if stored is not None:
        pre += f"storage['wider.reviewer'] = {json.dumps(stored)};\n"
    script = pre + BLOCK + "\n(async () => {\n" + body + "\n})().catch(e => { console.error(e); process.exit(1); });"
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_home_asks_for_the_name_once():
    out = run("""
      WiderName.render();
      const before = { hidden: el("home-name").hidden, html: el("home-name").innerHTML, top: el("tb-name").innerHTML };
      el("home-name-input").value = "  ana ";
      el("home-name-save").onclick();
      console.log(JSON.stringify({ before, after: { hidden: el("home-name").hidden, top: el("tb-name").innerHTML },
        stored: storage["wider.reviewer"], reloads: location.reloads, fired }));
    """)
    b = out["before"]
    assert b["hidden"] is False and "Your name" in b["html"]
    assert "Decisions, labels and notes are signed with this name." in b["html"]
    assert "No name yet" in b["top"] and "Set name" in b["top"]
    # a first name is stored without a reload (unsaved input elsewhere survives) and the Home card goes away
    assert out["stored"] == "ana" and out["reloads"] == 0 and out["fired"] == ["widername"]
    assert out["after"]["hidden"] is True
    assert "Working as:" in out["after"]["top"] and "<b class=\"tb-name-nm\">ana</b>" in out["after"]["top"]
    assert "data-set-name>Change" in out["after"]["top"]


def test_opening_a_project_without_a_name_asks_once():
    out = run("""
      WiderName.askOnce(); await tick(); WiderName.askOnce(); await tick();
      console.log(JSON.stringify({ n: dialogs.length, html: dialogs[0].html, top: el("tb-name").innerHTML,
        stored: storage["wider.reviewer"] || null }));
    """)
    assert out["n"] == 1   # "Not now" (null) and a second project opening: no second dialog in this page load
    assert "Not now" in out["html"] and "Decisions, labels and notes are signed with this name." in out["html"]
    assert "WIDER has no login" in out["html"]
    assert out["stored"] is None
    named = run("WiderName.askOnce(); await tick(); console.log(JSON.stringify({ n: dialogs.length }));", stored="ana")
    assert named["n"] == 0


def test_change_switches_the_name_and_reloads():
    out = run("""
      WiderName.render();
      replies.push({ act: "ok", fields: { name: "bo" } });
      click(button("data-set-name")); await tick(); await tick();
      const first = { stored: storage["wider.reviewer"], reloads: location.reloads, html: dialogs[0].html };
      replies.push({ act: "ok", fields: { name: "bo" } }); await WiderName.ask();
      replies.push(null); await WiderName.ask();
      console.log(JSON.stringify({ first, reloads: location.reloads, stored: storage["wider.reviewer"] }));
    """, stored="ana")
    f = out["first"]
    assert f["stored"] == "bo" and f["reloads"] == 1
    assert 'value="ana"' in f["html"] and "Cancel" in f["html"]
    assert "Saving another name reloads the page and any other WIDER tab; anything not saved is dropped." in f["html"]
    # the same name again and Cancel change nothing
    assert out["reloads"] == 1 and out["stored"] == "bo"


def test_another_tab_reloads_when_the_name_changes():
    out = run("""
      storage["wider.reviewer"] = "bo";
      winEvents.storage.forEach(f => f({ key: "wider.reviewer" }));
      const switched = location.reloads;
      winEvents.storage.forEach(f => f({ key: "wider.journey" }));
      console.log(JSON.stringify({ switched, after: location.reloads }));
    """, stored="ana")
    assert out == {"switched": 1, "after": 1}
    # a tab with no name yet takes the new one without a reload
    first = run("""
      storage["wider.reviewer"] = "bo";
      winEvents.storage.forEach(f => f({ key: "wider.reviewer" }));
      console.log(JSON.stringify({ reloads: location.reloads, name: WiderName.get(), fired }));
    """)
    assert first == {"reloads": 0, "name": "bo", "fired": ["widername"]}


def test_change_with_an_empty_name_forgets_it():
    out = run("""
      replies.push({ act: "ok", fields: { name: "" } }); await WiderName.ask();
      console.log(JSON.stringify({ has: "wider.reviewer" in storage, reloads: location.reloads, name: WiderName.get() }));
    """, stored="ana")
    assert out == {"has": False, "reloads": 1, "name": ""}


def test_no_page_has_its_own_name_field():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("*.js")}
    for gone in ("lb-reviewer", "hist-name", "dw-author", "data-change-name"):
        assert gone not in html, gone
        assert not [n for n, s in js.items() if gone in s], gone
    # only common.js reads or writes the key; app.js's request wrapper only copies it into the author header
    for name, src in js.items():
        if name in ("common.js",):
            continue
        for m in re.finditer(r"localStorage\.(get|set|remove)Item\(\"wider\.reviewer\"", src):
            assert name == "app.js" and m.group(1) == "get", name
    for name in ("records.js", "history.js", "drawer.js", "review.js"):
        assert "WiderName.get()" in js[name], name
    assert "Drawer.author()" in js["dedup.js"]   # the pair view signs through the drawer, which reads the same name
    assert "WiderName.get() || null" in js["app.js"]   # Validation steps
    assert 'id="tb-name"' in html and 'id="home-name"' in html
    assert 'aria-label="Reviewer A"' in html and 'aria-label="Reviewer B"' in html   # they name other people: kept


def test_a_refusal_points_at_change_and_opens_the_dialog():
    out = run("""
      const line = WiderName.needHtml("no name yet: labels need your name.");
      click(button("data-set-name")); await tick();
      console.log(JSON.stringify({ line, need: WiderName.NEED, n: dialogs.length }));
    """)
    assert out["need"] == "enter your name first: use Change beside “Working as” in the top bar"
    assert out["line"].startswith("no name yet: labels need your name. enter your name first: use Change beside")
    assert "data-set-name>Set your name</button>" in out["line"]
    assert out["n"] == 1


def test_blind_is_ticked_again_after_a_reload():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for tag in ('<select id="lb-mode" autocomplete="off">', '<select id="lb-sample" autocomplete="off">',
                '<input type="checkbox" id="lb-blind" checked autocomplete="off">'):
        assert tag in html, tag
    if not NODE:
        pytest.skip("node is not installed")
    # records.js ticks Blind on load, whatever the browser restored after the name switch's reload
    src = (STATIC / "records.js").read_text(encoding="utf-8")
    stmt = next(ln.strip() for ln in src.splitlines() if '#lb-blind").checked = true' in ln)
    script = ("const box = { checked: false }; const q = (s) => (s === '#lb-blind' ? box : null);\n"
              f"{stmt}\nconsole.log(JSON.stringify(box));")
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"checked": True}


def test_a_page_brought_back_by_back_follows_the_name():
    # risk-checker R1: a page kept in the back-forward cache gets no storage event; on pageshow it catches up
    out = run("""
      storage["wider.reviewer"] = "bo";
      winEvents.pageshow.forEach(f => f({ persisted: false }));
      const fresh = location.reloads;
      winEvents.pageshow.forEach(f => f({ persisted: true }));
      console.log(JSON.stringify({ fresh, restored: location.reloads }));
    """, stored="ana")
    assert out == {"fresh": 0, "restored": 1}
    # the author header is the name the page works as, not a newer stored one; the drawer leaves a dialog's keys alone
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'const who = window.WiderName ? WiderName.get() : "";' in app
    drawer = (STATIC / "drawer.js").read_text(encoding="utf-8")
    keys = drawer[drawer.index('document.addEventListener("keydown"'):]
    assert keys.index('dialog[open]') < keys.index('e.key === "Tab"')
