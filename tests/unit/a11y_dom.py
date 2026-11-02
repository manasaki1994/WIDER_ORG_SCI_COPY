"""A small DOM for Node page-script tests that need a real tree: HTML written to ``innerHTML`` is parsed into
elements, selectors find them, focus, events and dialogs work, and attributes are kept. No browser and no package:
only what the accessibility tests need (focus moves, roles, names, keys, Tab order inside an element). Layout is not
modelled: ``getClientRects`` is non-empty for every connected element that is not hidden."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

DOM = r"""
const VOID = new Set(["area","base","br","col","embed","hr","img","input","link","meta","source","track","wbr"]);
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", apos: "'", nbsp: " ", ndash: "–", mdash: "—", hellip: "…", times: "×", rarr: "→", larr: "←", middot: "·" };
const unent = (s) => s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) => e[0] === "#" ? (e[1] === "x" || e[1] === "X" ? String.fromCodePoint(parseInt(e.slice(2), 16)) : (ENT[e] ?? String.fromCodePoint(+e.slice(1)))) : (ENT[e] ?? m));
const escT = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const escA = (s) => String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
const camel = (s) => s.replace(/-([a-z])/g, (m, c) => c.toUpperCase());
const kebab = (s) => s.replace(/[A-Z]/g, (c) => "-" + c.toLowerCase());
const BOOL = ["hidden", "disabled", "checked", "readOnly", "open", "required", "multiple", "selected", "autofocus"];
const FOCUSABLE = "a[href], button, input, select, textarea, summary, [tabindex]";

class Text {
  constructor(t) { this.nodeType = 3; this.data = t; this.parentNode = null; }
  get textContent() { return this.data; }
  get isConnected() { return !!this.parentNode && this.parentNode.isConnected; }
  remove() { if (this.parentNode) { this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1); this.parentNode = null; } }
}
class Element {
  constructor(tag) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.childNodes = []; this.parentNode = null;
    this.listeners = {}; this._value = null; this.style = { setProperty: (k, v) => { this.style[k] = v; }, removeProperty: (k) => { delete this.style[k]; }, getPropertyValue: (k) => this.style[k] || "" };
    this.scrolled = [];
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() { const c = this.children; return c[c.length - 1] || null; }
  get nextElementSibling() { if (!this.parentNode) return null; const s = this.parentNode.children; return s[s.indexOf(this) + 1] || null; }
  get previousElementSibling() { if (!this.parentNode) return null; const s = this.parentNode.children; return s[s.indexOf(this) - 1] || null; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  removeAttribute(n) { this.attrs.delete(n); }
  hasAttribute(n) { return this.attrs.has(n); }
  toggleAttribute(n, on) { const v = on === undefined ? !this.attrs.has(n) : on; if (v) this.attrs.set(n, ""); else this.attrs.delete(n); return v; }
  get attributes() { return [...this.attrs].map(([name, value]) => ({ name, value })); }
  get id() { return this.getAttribute("id") || ""; } set id(v) { this.setAttribute("id", v); }
  get className() { return this.getAttribute("class") || ""; } set className(v) { this.setAttribute("class", v); }
  get type() { return this.getAttribute("type") || (this.tagName === "BUTTON" ? "submit" : this.tagName === "INPUT" ? "text" : ""); } set type(v) { this.setAttribute("type", v); }
  get href() { return this.getAttribute("href") || ""; } set href(v) { this.setAttribute("href", v); }
  get name() { return this.getAttribute("name") || ""; } set name(v) { this.setAttribute("name", v); }
  get title() { return this.getAttribute("title") || ""; } set title(v) { this.setAttribute("title", v); }
  get placeholder() { return this.getAttribute("placeholder") || ""; } set placeholder(v) { this.setAttribute("placeholder", v); }
  get tabIndex() { const t = this.getAttribute("tabindex"); return t === null ? (this.matches("a[href], button, input, select, textarea, summary") ? 0 : -1) : +t; }
  set tabIndex(v) { this.setAttribute("tabindex", v); }
  get classList() {
    const self = this, list = () => self.className.split(/\s+/).filter(Boolean);
    return { contains: (c) => list().includes(c), add: (...cs) => { self.className = [...new Set([...list(), ...cs])].join(" "); },
      remove: (...cs) => { self.className = list().filter((x) => !cs.includes(x)).join(" "); },
      toggle: (c, on) => { const has = list().includes(c); const v = on === undefined ? !has : !!on; if (v && !has) self.classList.add(c); if (!v && has) self.classList.remove(c); return v; } };
  }
  get dataset() {
    const self = this;
    return new Proxy({}, { get: (t, p) => typeof p === "string" ? (self.getAttribute("data-" + kebab(p)) ?? undefined) : undefined,
      set: (t, p, v) => { self.setAttribute("data-" + kebab(p), v); return true; },
      deleteProperty: (t, p) => { self.removeAttribute("data-" + kebab(p)); return true; },
      has: (t, p) => self.hasAttribute("data-" + kebab(p)),
      ownKeys: () => self.attributes.filter((a) => a.name.startsWith("data-")).map((a) => camel(a.name.slice(5))),
      getOwnPropertyDescriptor: (t, p) => ({ enumerable: true, configurable: true, value: self.getAttribute("data-" + kebab(p)) }) });
  }
  get value() {
    if (this.tagName === "SELECT") { const o = this.querySelectorAll("option"); const s = o.find((x) => x.selected) || o[0]; return this._value ?? (s ? s.value : ""); }
    if (this.tagName === "TEXTAREA") return this._value ?? this.textContent;
    if (this.tagName === "OPTION") return this.getAttribute("value") ?? this.textContent;
    return this._value ?? (this.getAttribute("value") || "");
  }
  set value(v) { this._value = String(v); }
  get textContent() { return this.childNodes.map((n) => n.textContent).join(""); }
  set textContent(t) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = []; if (t !== "" && t != null) this.appendChild(new Text(String(t))); }
  get innerText() { return this.textContent; } set innerText(t) { this.textContent = t; }
  get innerHTML() { return this.childNodes.map(serialize).join(""); }
  set innerHTML(h) { this.childNodes.forEach((n) => { n.parentNode = null; }); this.childNodes = []; parseInto(this, String(h ?? "")); }
  get outerHTML() { return serialize(this); }
  insertAdjacentHTML(pos, h) { const tmp = new Element("div"); tmp.innerHTML = h; const kids = [...tmp.childNodes];
    if (pos === "beforeend") kids.forEach((k) => this.appendChild(k)); else if (pos === "afterbegin") kids.reverse().forEach((k) => this.prepend(k));
    else if (pos === "afterend") kids.reverse().forEach((k) => this.after(k)); else if (pos === "beforebegin") kids.forEach((k) => this.before(k)); }
  appendChild(n) { if (n.parentNode) n.remove(); n.parentNode = this; this.childNodes.push(n); return n; }
  append(...ns) { ns.forEach((n) => this.appendChild(typeof n === "string" ? new Text(n) : n)); }
  prepend(n) { if (n.parentNode) n.remove(); n.parentNode = this; this.childNodes.unshift(n); return n; }
  insertBefore(n, ref) { if (!ref) return this.appendChild(n); if (n.parentNode) n.remove(); n.parentNode = this; this.childNodes.splice(this.childNodes.indexOf(ref), 0, n); return n; }
  after(n) { const p = this.parentNode; if (!p) return; const nx = p.childNodes[p.childNodes.indexOf(this) + 1]; nx ? p.insertBefore(n, nx) : p.appendChild(n); }
  before(n) { if (this.parentNode) this.parentNode.insertBefore(n, this); }
  replaceWith(n) { this.before(n); this.remove(); }
  removeChild(n) { n.remove(); return n; }
  remove() { const p = this.parentNode; if (!p) return; p.childNodes.splice(p.childNodes.indexOf(this), 1); this.parentNode = null;
    if (document.activeElement && (document.activeElement === this || this.contains(document.activeElement))) document.activeElement = document.body; }
  cloneNode(deep) { const c = new Element(this.tagName); this.attrs.forEach((v, k) => c.attrs.set(k, v)); if (deep) c.innerHTML = this.innerHTML; return c; }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === document; }
  contains(x) { for (let n = x; n; n = n.parentNode) if (n === this) return true; return false; }
  closest(sel) { for (let n = this; n && n.nodeType === 1; n = n.parentNode) if (n.matches(sel)) return n; return null; }
  matches(sel) { return splitGroups(sel).some((g) => matchChain(this, parseChain(g))); }
  querySelectorAll(sel) { const out = [], groups = splitGroups(sel).map(parseChain); walk(this, (e) => { if (groups.some((c) => matchChain(e, c, this))) out.push(e); }); return out; }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  getElementsByTagName(t) { return this.querySelectorAll(t.toLowerCase()); }
  getClientRects() { if (!this.isConnected) return []; for (let n = this; n && n.nodeType === 1; n = n.parentNode) { if (n.hidden || (n.tagName === "DIALOG" && !n.open) || n.style.display === "none") return []; } return [{}]; }
  getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: this._height || 0 }; }
  get offsetHeight() { return this._height || 0; }
  focus() { if (!this.isConnected || this.disabled) return; const prev = document.activeElement; if (prev === this) return;
    document.activeElement = this; if (prev && prev !== document.body) prev.dispatchEvent(new Event("focusout", { bubbles: true, relatedTarget: this }));
    this.dispatchEvent(new Event("focus")); this.dispatchEvent(new Event("focusin", { bubbles: true })); }
  blur() { if (document.activeElement === this) { document.activeElement = document.body; this.dispatchEvent(new Event("focusout", { bubbles: true })); } }
  click() { if (this.disabled) return; const ev = new Event("click", { bubbles: true }); this.dispatchEvent(ev);
    if (!ev.defaultPrevented && this.tagName === "SUMMARY" && this.parentNode && this.parentNode.tagName === "DETAILS") this.parentNode.open = !this.parentNode.open;
    if (!ev.defaultPrevented && this.tagName === "A" && this.getAttribute("href") && this.getAttribute("href").startsWith("#")) navigateHash(this.getAttribute("href")); }
  scrollIntoView(opts) { this.scrolled.push(opts === undefined ? true : opts); }
  scrollTo() {} select() {} setSelectionRange() {}
  showModal() { this.open = true; } show() { this.open = true; } close() { if (!this.open) return; this.open = false; this.dispatchEvent(new Event("close")); }
  addEventListener(t, fn, opts) { (this.listeners[t] ||= []).push(fn); }
  removeEventListener(t, fn) { this.listeners[t] = (this.listeners[t] || []).filter((f) => f !== fn); }
  dispatchEvent(ev) { ev.target ??= this; const path = []; for (let n = this; n; n = n.parentNode) path.push(n); if (path[path.length - 1] === document) path.push(window);
    for (const n of path) { ev.currentTarget = n; const on = n["on" + ev.type]; if (typeof on === "function" && n !== window) on.call(n, ev);
      for (const fn of [...((n.listeners || {})[ev.type] || [])]) fn.call(n, ev); if (!ev.bubbles || ev.stopped) break; }
    return !ev.defaultPrevented; }
  get options() { return this.querySelectorAll("option"); }
  get selectedIndex() { return Math.max(0, this.options.findIndex((o) => o.value === this.value)); }
  get form() { return this.closest("form"); }
  get labels() { return this.id ? document.querySelectorAll(`label[for="${this.id}"]`) : []; }
}
for (const b of BOOL) {
  const attr = b === "readOnly" ? "readonly" : b;
  Object.defineProperty(Element.prototype, b, { get() { return this.attrs.has(attr); }, set(v) { if (v) this.attrs.set(attr, ""); else this.attrs.delete(attr); } });
}
class Event {
  constructor(type, o = {}) { this.type = type; this.bubbles = !!o.bubbles; Object.assign(this, o); this.defaultPrevented = false; }
  preventDefault() { this.defaultPrevented = true; } stopPropagation() { this.stopped = true; } stopImmediatePropagation() { this.stopped = true; }
}
class KeyboardEvent extends Event { constructor(type, o = {}) { super(type, { bubbles: true, ...o }); this.ctrlKey ??= false; this.metaKey ??= false; this.shiftKey ??= false; this.altKey ??= false; } }
class CustomEvent extends Event { constructor(type, o = {}) { super(type, o); this.detail = o.detail; } }
function walk(root, fn) { for (const c of root.childNodes) if (c.nodeType === 1) { fn(c); walk(c, fn); } }
function serialize(n) {
  if (n.nodeType === 3) return escT(n.data);
  const t = n.tagName.toLowerCase(), a = [...n.attrs].map(([k, v]) => v === "" ? ` ${k}` : ` ${k}="${escA(v)}"`).join("");
  return VOID.has(t) ? `<${t}${a}>` : `<${t}${a}>${n.childNodes.map(serialize).join("")}</${t}>`;
}
function parseInto(root, html) {
  const re = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|<!doctype[^>]*>/gi;
  let cur = root, last = 0, m;
  const text = (s) => { if (s) cur.appendChild(new Text(unent(s))); };
  while ((m = re.exec(html))) {
    text(html.slice(last, m.index)); last = re.lastIndex;
    if (m[1]) { const t = m[1].toUpperCase(); for (let n = cur; n && n !== root.parentNode; n = n.parentNode) { if (n.tagName === t) { cur = n.parentNode; break; } if (n === root) break; } continue; }
    if (!m[2]) continue;
    const e = new Element(m[2]);
    for (const a of (m[3] || "").matchAll(/([^\s=>\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) e.attrs.set(a[1].toLowerCase(), unent(a[2] ?? a[3] ?? a[4] ?? ""));
    cur.appendChild(e);
    const tag = m[2].toLowerCase();
    if (tag === "script" || tag === "style" || tag === "textarea") {
      const end = html.toLowerCase().indexOf(`</${tag}`, last); const inner = html.slice(last, end < 0 ? html.length : end);
      if (inner) e.appendChild(new Text(tag === "textarea" ? unent(inner) : inner));
      last = end < 0 ? html.length : html.indexOf(">", end) + 1; re.lastIndex = last; continue;
    }
    if (!VOID.has(tag) && !m[4]) cur = e;
  }
  text(html.slice(last));
}
// selectors: groups by comma; a chain of compounds joined by descendant (space) or child (>) combinators
function splitGroups(sel) { const out = []; let depth = 0, cur = ""; for (const ch of sel) { if (ch === "(" || ch === "[") depth++; if (ch === ")" || ch === "]") depth--; if (ch === "," && !depth) { out.push(cur.trim()); cur = ""; } else cur += ch; } if (cur.trim()) out.push(cur.trim()); return out; }
function parseChain(sel) {
  const parts = [], re = /\s*(>)?\s*((?:[^\s>\[\(]|\[[^\]]*\]|\([^)]*\))+)/g; let m;
  while ((m = re.exec(sel))) { if (!m[2]) break; parts.push({ comb: m[1] ? ">" : " ", c: m[2] }); }
  return parts;
}
function matchCompound(e, c) {
  if (!e || e.nodeType !== 1) return false;
  const re = /^([a-zA-Z*][\w-]*)|#((?:\\.|[\w-])+)|\.([\w-]+)|\[([\w-]+)(?:([\^$*~|]?=)"?([^"\]]*)"?)?\]|:not\(([^)]*)\)|:(\w[\w-]*)/g; let m, any = false;
  while ((m = re.exec(c))) {
    if (m.index !== re.lastIndex - m[0].length) break; any = true;
    if (m[1] && m[1] !== "*" && e.tagName !== m[1].toUpperCase()) return false;
    if (m[2] && e.id !== m[2].replace(/\\(.)/g, "$1")) return false;
    if (m[3] && !e.classList.contains(m[3])) return false;
    if (m[4]) { const v = e.getAttribute(m[4]); if (v === null) return false;
      if (m[5]) { const w = m[6]; if (m[5] === "=" && v !== w) return false; if (m[5] === "^=" && !v.startsWith(w)) return false; if (m[5] === "$=" && !v.endsWith(w)) return false; if (m[5] === "*=" && !v.includes(w)) return false; if (m[5] === "~=" && !v.split(/\s+/).includes(w)) return false; } }
    if (m[7] !== undefined && e.matches(m[7])) return false;
    if (m[8]) { const p = m[8]; if (p === "checked" && !e.checked) return false; if (p === "disabled" && !e.disabled) return false; if (p === "focus" && document.activeElement !== e) return false; if (p === "first-child" && e.parentNode && e.parentNode.children[0] !== e) return false; }
  }
  return any;
}
function matchChain(e, chain, scope) {
  let i = chain.length - 1; if (!matchCompound(e, chain[i].c)) return false;
  let n = e;
  for (; i > 0; i--) {
    const comb = chain[i].comb, want = chain[i - 1].c;
    if (comb === ">") { n = n.parentNode; if (!n || n === scope || !matchCompound(n, want)) return false; }
    else { do { n = n.parentNode; } while (n && n !== scope && n.nodeType === 1 && !matchCompound(n, want)); if (!n || n === scope || n.nodeType !== 1) return false; }
  }
  return true;
}
// the document: html > head + body; activeElement starts on the body
const document = new Element("#document"); document.nodeType = 9; global.document = document;
Object.defineProperty(document, "isConnected", { get: () => true });
const htmlEl = new Element("html"); document.appendChild(htmlEl); document.documentElement = htmlEl;
document.head = new Element("head"); htmlEl.appendChild(document.head); document.body = new Element("body"); htmlEl.appendChild(document.body);
document.activeElement = document.body; document.title = "";
document.createElement = (t) => new Element(t); document.createTextNode = (t) => new Text(t);
document.getElementById = (id) => document.querySelector("#" + id);
document.createDocumentFragment = () => new Element("#fragment");
global.Element = Element; global.HTMLElement = Element; global.Event = Event; global.KeyboardEvent = KeyboardEvent; global.CustomEvent = CustomEvent;
global.window = global; global.listeners = {};
window.addEventListener = (t, fn) => { (window.listeners[t] ||= []).push(fn); };
window.removeEventListener = (t, fn) => { window.listeners[t] = (window.listeners[t] || []).filter((f) => f !== fn); };
window.dispatchEvent = (ev) => { for (const fn of [...(window.listeners[ev.type] || [])]) fn(ev); return !ev.defaultPrevented; };
window.scrolls = []; window.scrollTo = (...a) => { window.scrolls.push(a); }; window.scrollY = 0;
window.innerWidth = 1280; window.innerHeight = 800;
window.media = {};   // a test sets media["(max-width: 640px)"] = true
window.matchMedia = (q) => { const mq = { media: q, get matches() { return !!window.media[q]; }, ls: [], addEventListener(t, fn) { this.ls.push(fn); }, addListener(fn) { this.ls.push(fn); }, removeEventListener() {} }; (window.mqs ||= []).push(mq); return mq; };
global.location = { hash: "", pathname: "/", search: "", href: "http://127.0.0.1/" };
function navigateHash(h) { location.hash = h; window.dispatchEvent(new Event("hashchange")); }
global.navigateHash = navigateHash;
global.history = { replaceState(a, b, h) { location.hash = h; }, pushState(a, b, h) { location.hash = h; }, back() {} };
const LS = {}; global.localStorage = { getItem: (k) => (k in LS ? LS[k] : null), setItem: (k, v) => { LS[k] = String(v); }, removeItem: (k) => { delete LS[k]; } };
global.CSS = { escape: (s) => String(s).replace(/([^\w-])/g, "\\$1") };
global.MutationObserver = class { constructor(fn) { this.fn = fn; } observe() {} disconnect() {} };
global.getComputedStyle = (e) => e.style;
global.requestAnimationFrame = (fn) => setTimeout(fn, 0);
global.flush = () => new Promise((r) => setTimeout(r, 0));
global.$ = (s) => document.querySelector(s);
global.$$ = (s) => document.querySelectorAll(s);
// keys: dispatched at the focused element (or the body), as the browser does
global.press = (k, o = {}) => { const t = document.activeElement || document.body; const ev = new KeyboardEvent("keydown", { key: k, ...o }); t.dispatchEvent(ev); return ev; };
global.focused = () => { const a = document.activeElement; if (!a || a === document.body) return "BODY"; return a.id ? "#" + a.id : a.outerHTML.slice(0, 120); };
// requests: recorded, answered from `replies` (a value, or a function of (url, opts)); a reply that is an Error is thrown
global.calls = []; global.replies = {};
global.answer = async (url, opts) => {
  calls.push([url, (opts && opts.method) || "GET", opts && opts.body ? (() => { try { return JSON.parse(opts.body); } catch (e) { return opts.body; } })() : null]);
  for (const [k, v] of Object.entries(replies)) if (url === k || url.startsWith(k + "?")) { const r = typeof v === "function" ? await v(url, opts) : JSON.parse(JSON.stringify(v)); if (r instanceof Error) throw r; return r; }
  throw new Error("no reply for " + url);
};
global.fetch = async (url, opts) => { try { const body = await answer(url, opts); return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) }; }
  catch (e) { const status = e.status || 500; return { ok: false, status, json: async () => ({ detail: e.message }), text: async () => JSON.stringify({ detail: e.message }) }; } };
global.posts = () => calls.filter((c) => c[1] !== "GET");
"""


def static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def run_dom(body: str, *, html: str = "", scripts: tuple[str, ...] = (), pre: str = "") -> dict:
    """Run ``body`` (an async function body that prints a JSON result with ``out(...)``) on the small DOM, with
    ``html`` as the body's content and the named static scripts loaded in order after ``pre``."""
    if not NODE:
        pytest.skip("node is not installed")
    code = "\n".join(static(s) for s in scripts)
    script = (f"{DOM}\ndocument.body.innerHTML = {json.dumps(html)};\n"
              # out() ends the run: the pages' polling timers (jobs, questions) would keep Node alive
              "global.out = (x) => { process.stdout.write(JSON.stringify(x), () => process.exit(0)); };\n"
              f"{pre}\n{code}\n;(async () => {{\n{body}\n}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});")
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout or "null")


def section(page_id: str) -> str:
    """The ``<section id=page_id>`` of index.html, as the page has it."""
    html = static("index.html")
    a = html.index(f'<section id="{page_id}"')
    depth, i = 0, a
    import re
    for m in re.finditer(r"<(/?)section\b", html[a:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            i = a + m.end()
            break
    return html[a:html.index(">", i) + 1]
