"""A stand-in page for the interactive demo's layer (showcase/demo/demo.js), run under Node (no browser).

``run_demo(body)`` runs ``body`` (JavaScript, may ``await``) after ``boot()`` is defined; ``boot()`` makes a fresh
document and runs demo.js on it, as a page load does. The tab's sessionStorage (``session``) outlives a ``boot()``, so
a second ``boot()`` is a reload. ``out`` is returned as JSON. The page's document holds only what demo.js needs:
elements with attributes, children, listeners, ``a[href]`` lookups and a dialog's ``showModal``/``close``.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DEMO_DIR = REPO / "showcase" / "demo"
NODE = shutil.which("node")

# a small invented snapshot: two checkpoints, a pattern, a reason, a file
SNAPSHOT = {
    "order": ["one", "two"],
    "checkpoints": {
        "one": {"GET /api/home": "b1", "GET /api/history?author=Reviewer%20A&limit=5": "b2",
                "GET /api/outputs/file?path=exports%2Fx.csv": "b3"},
        "two": {"GET /api/home": "b4", "GET /api/history?author=Reviewer%20A&limit=5": "b2"},
    },
    "bodies": {
        "b1": {"status": 200, "type": "json", "json": {"projects": [{"name": "one"}]}},
        "b2": {"status": 200, "type": "json", "json": [{"author": "Reviewer A", "what": "label"}]},
        "b3": {"status": 200, "type": "text/csv", "text": "id,title\n1,A paper\n"},
        "b4": {"status": 200, "type": "json", "json": {"projects": [{"name": "two"}]}},
    },
    "patterns": {"GET /api/history": ["author"]},
    "not_in_demo": {"POST /api/import/upload": "Uploading files is off in the demo."},
    "handlers": [],
}

PAGE = r"""
const out = {};
const docListeners = {};
function ev(type, target) {
  return { type, target, defaultPrevented: false, stopped: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; },
    stopImmediatePropagation() { this.stopped = true; } };
}
function walk(n, fn) { fn(n); (n.children || []).forEach((c) => walk(c, fn)); }
// a small selector matcher: compound parts tag.class#id[attr][attr="v"], joined by spaces (descendant)
function matchOne(n, s) {
  if (!n || n.nodeType !== 1) return false;
  const m = /^([a-z]*)((?:[.#][\w-]+)*)((?:\[[\w-]+(?:="[^"]*")?\])*)$/i.exec(s);
  if (!m) return false;
  if (m[1] && n.tagName !== m[1].toUpperCase()) return false;
  for (const [, k, v] of m[2].matchAll(/([.#])([\w-]+)/g)) {
    if (k === "#" ? n.id !== v : !n.classList.contains(v)) return false;
  }
  for (const [, a, v] of m[3].matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    const got = n.getAttribute(a);
    if (got == null || (v !== undefined && got !== v)) return false;
  }
  return true;
}
function matches(n, sel) {
  const parts = String(sel).trim().split(/\s+/);
  if (!matchOne(n, parts.pop())) return false;
  let up = n.parent;
  while (parts.length && up) { if (matchOne(up, parts[parts.length - 1])) parts.pop(); up = up.parent; }
  return !parts.length;
}
function makeEl(tag) {
  const cls = new Set();
  const e = {
    nodeType: 1, tagName: String(tag).toUpperCase(), attrs: {}, children: [], parent: null, _text: "", _ls: {},
    hidden: false, open: false, style: {}, offsetHeight: 0,
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); },
    set textContent(v) { this._text = String(v); this.children = []; },
    get id() { return this.attrs.id || ""; },
    set id(v) { this.attrs.id = String(v); },
    get className() { return [...cls].join(" "); },
    set className(v) { cls.clear(); String(v || "").split(/\s+/).filter(Boolean).forEach((c) => cls.add(c)); },
    dataset: {},
    get parentNode() { return this.parent; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { const k = this.parent ? this.parent.children : []; return k[k.indexOf(this) + 1] || null; },
    classList: { add: (c) => cls.add(c), remove: (c) => cls.delete(c), contains: (c) => cls.has(c),
                 toggle: (c, on) => (on ?? !cls.has(c)) ? cls.add(c) : cls.delete(c) },
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === "hidden") this.hidden = true; },
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; if (k === "hidden") this.hidden = false; },
    addEventListener(t, fn) { (this._ls[t] ||= []).push(fn); },
    fire(t) { const e2 = ev(t, this); (this._ls[t] || []).forEach((f) => f(e2)); return e2; },
    click() {
      const e2 = ev("click", this);
      for (const f of docListeners.click || []) { f(e2); if (e2.stopped) return e2; }
      (this._ls.click || []).forEach((f) => f(e2));
      return e2;
    },
    appendChild(c) { if (c.parent) c.remove(); c.parent = this; this.children.push(c); return c; },
    insertBefore(c, ref) {
      if (c.parent) c.remove();
      c.parent = this;
      const i = this.children.indexOf(ref);
      this.children.splice(i < 0 ? this.children.length : i, 0, c);
      return c;
    },
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); this.parent = null; },
    querySelectorAll(sel) {
      const all = [];
      walk(this, (n) => { if (n !== this && matches(n, sel || "a[href]")) all.push(n); });
      return all;
    },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    closest(sel) { let n = this; while (n) { if (matches(n, sel || "a")) return n; n = n.parent; } return null; },
    dispatchEvent(e2) { (this._ls[e2.type] || []).forEach((f) => f(e2)); return true; },
    showModal() { this.open = true; this.attrs.open = ""; },
    // as in a browser, the close event comes after close() returns
    close() { if (!this.open) return; this.open = false; delete this.attrs.open; setTimeout(() => this.fire("close"), 0); },
    focus() { document.activeElement = this; },
  };
  return e;
}
class Doc { get title() { return this._title; } set title(v) { this._title = String(v); } }
function newDocument() {
  const d = new Doc();
  d._title = "Demo — WIDER";
  d.documentElement = makeEl("html");
  d.body = d.documentElement.appendChild(makeEl("body"));
  d.createElement = makeEl;
  d.createTextNode = (v) => ({ nodeType: 3, textContent: String(v), children: [] });
  d.getElementById = (id) => { let f = null; walk(d.documentElement, (n) => { if (!f && n.id === id) f = n; }); return f; };
  d.addEventListener = (t, fn) => { (docListeners[t] ||= []).push(fn); };
  // only the attribute form the demo uses: [name="value"]
  d.querySelector = (sel) => {
    const m = /^\[([\w-]+)="([^"]*)"\]$/.exec(sel);
    if (!m) return null;
    let f = null;
    walk(d.documentElement, (n) => { if (!f && n.getAttribute && n.getAttribute(m[1]) === m[2]) f = n; });
    return f;
  };
  return d;
}
class FakeStorage {
  constructor() { this.m = new Map(); }
  getItem(k) { return this.m.has(k) ? this.m.get(k) : null; }
  setItem(k, v) { this.m.set(k, String(v)); }
  removeItem(k) { this.m.delete(k); }
  key(i) { return [...this.m.keys()][i] ?? null; }
  get length() { return this.m.size; }
}
const session = new FakeStorage();
const realLocal = new FakeStorage();
const spy = { fetch: 0, beacon: 0, blobs: [] };
let storageWorks = true;
let confirmAnswer = true;
globalThis.window = globalThis;
globalThis.location = { href: "http://demo.test/demo/", origin: "http://demo.test", pathname: "/demo/", search: "",
  hash: "", replaced: null, replace(u) { this.replaced = u; },
  // a real reload; replace() to the page's own address with a fragment only scrolls, so it is recorded apart
  reloaded: null, reload() { const h = String(this.hash).replace(/^#/, ""); this.reloaded = this.pathname + this.search + (h ? "#" + h : ""); } };
globalThis.confirm = (msg) => { out.confirmed = (out.confirmed || []).concat([msg]); return confirmAnswer; };
// a test sets narrowScreen = true for a phone-width window (max-width: 700px)
globalThis.narrowScreen = false;
globalThis.matchMedia = (q) => ({ media: q, matches: /max-width/.test(q) && globalThis.narrowScreen });
const winListeners = {};
globalThis.addEventListener = (t, fn) => { (winListeners[t] ||= []).push(fn); };
// the page has loaded: every script has run
function loaded() { (winListeners.load || []).forEach((f) => f()); }
const realCreate = URL.createObjectURL;
URL.createObjectURL = (b) => { spy.blobs.push(b); return realCreate(b); };
// a page's timers end with the page: boot() (a reload) clears the ones the last page started
const realSetInterval = setInterval, intervals = [];
globalThis.setInterval = (fn, ms) => { const t = realSetInterval(fn, ms); t.unref && t.unref(); intervals.push(t); return t; };
// the screening runs' clock (screening.js); a test moves it with clockNow += ms
globalThis.clockNow = 1000000;
function boot(snapshot) {
  intervals.splice(0).forEach((t) => clearInterval(t));
  for (const k of Object.keys(docListeners)) delete docListeners[k];
  for (const k of Object.keys(winListeners)) delete winListeners[k];
  globalThis.document = newDocument();
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true,
    get() { if (!storageWorks) throw new Error("storage is off"); return session; } });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, writable: true, value: realLocal });
  Object.defineProperty(globalThis, "navigator", { configurable: true, writable: true,
    value: { sendBeacon: () => { spy.beacon++; return true; } } });
  globalThis.fetch = async () => { spy.fetch++; return new Response("{}"); };
  globalThis.DEMO_SNAPSHOT = snapshot || SNAP_DATA;
  if (globalThis.beforeBoot) globalThis.beforeBoot(document);
  (0, eval)(DEMO);
  if (globalThis.ACTIONS) (0, eval)(ACTIONS);
  if (globalThis.DemoActions) DemoActions.timing.rank = DemoActions.timing.draw = 0;
  if (globalThis.SCREENING) (0, eval)(SCREENING);
  if (globalThis.ASK) (0, eval)(ASK);
  if (globalThis.DemoJobs) { DemoJobs.timing.screen = globalThis.SCREEN_MS || 0; DemoJobs.clock.now = () => clockNow; }
  if (globalThis.TERMS) (0, eval)(TERMS);
  if (globalThis.TOUR) (0, eval)(TOUR);
  if (globalThis.DemoTour) Object.assign(DemoTour.timing, { poll: 0, tries: 0, min: 0, perWord: 0 });
  return document;
}
// a key press on the page, as the browser sends it to the document's listeners
function key(k, target) {
  const e = ev("keydown", target || document.activeElement || document.body);
  Object.assign(e, { key: k, metaKey: false, ctrlKey: false, altKey: false });
  (docListeners.keydown || []).forEach((f) => f(e));
  return e;
}
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const $ = (id) => document.getElementById(id);
const getJson = async (path, init) => { const r = await fetch(path, init); return { status: r.status, json: await r.json() }; };
"""


def run_demo(body: str, snapshot: dict | None = None, snapshot_js: str | None = None, actions: bool = False,
             tour: bool = False) -> dict:
    """Run ``body`` on the stand-in page; ``snapshot_js`` (the build's snapshot.js) replaces the invented snapshot;
    ``actions`` also loads actions.js, screening.js and ask.js after demo.js (their simulated runs take no time here, unless
    the body sets ``SCREEN_MS`` before ``boot()``; ``clockNow`` is the screening clock) and then terms.js, which
    puts its panel before an element ``q-strings`` when ``beforeBoot(document)`` made one; ``tour`` loads those and
    then tour.js, with its waits and reading times set to nothing. ``key(k)`` presses a key, ``tick(ms)`` waits, ``loaded()`` fires the page's load event."""
    actions = actions or tour
    if not NODE:
        pytest.skip("node is not installed")
    data = f"const SNAP_DATA = {json.dumps(snapshot or SNAPSHOT)};"
    if snapshot_js:
        data = snapshot_js + "\nconst SNAP_DATA = window.DEMO_SNAPSHOT;"
    demo = (DEMO_DIR / "demo.js").read_text(encoding="utf-8")
    acts = (DEMO_DIR / "actions.js").read_text(encoding="utf-8") if actions else ""
    screening = (DEMO_DIR / "screening.js").read_text(encoding="utf-8") if actions else ""
    ask = (DEMO_DIR / "ask.js").read_text(encoding="utf-8") if actions else ""
    terms = (DEMO_DIR / "terms.js").read_text(encoding="utf-8") if actions else ""
    tour_js = (DEMO_DIR / "tour.js").read_text(encoding="utf-8") if tour else ""
    script = (f"globalThis.window = globalThis;\n{data}\nconst DEMO = {json.dumps(demo)};\nglobalThis.ACTIONS = {json.dumps(acts)};\nglobalThis.SCREENING = {json.dumps(screening)};\nglobalThis.ASK = {json.dumps(ask)};\nglobalThis.TERMS = {json.dumps(terms)};\nglobalThis.TOUR = {json.dumps(tour_js)};\n{PAGE}\n(async () => {{\n{body}\n}})()"
              ".then(() => { process.stdout.write(JSON.stringify(out)); process.exit(0); })"
              ".catch((e) => { console.error(e && e.stack || e); process.exit(1); });")
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)
