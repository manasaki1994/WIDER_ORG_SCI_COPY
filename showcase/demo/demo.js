/* WIDER interactive demo layer (openspec change interactive-demo, design D2, D4, D7).
 *
 * Loaded before the app's own scripts. It answers every `/api/` request of the app from the recorded snapshot
 * (window.DEMO_SNAPSHOT, written by build_demo.py) plus the visitor's own changes, refuses every other request before
 * it leaves the page, keeps the demo's state and the app's own storage in this tab only, and draws the Demo Mode bar
 * and the start card. The guided tour (tour.js) hooks in through window.DemoTour.
 */
(function () {
  "use strict";
  const W = window;
  const SNAP = W.DEMO_SNAPSHOT || { checkpoints: {}, order: [], bodies: {}, patterns: {}, not_in_demo: {}, handlers: [] };
  const BLOCKED = "Blocked in the demo: nothing leaves this page";
  const NOT_PART = "Not part of the demo";
  const NOT_HERE = "This is not part of the demo.";
  const OUTSIDE_LINK = "Links to outside sites are off in the demo (the papers and their DOIs are invented).";
  const STATE_KEY = "wider-demo-v1";
  const APP_PREFIX = "wider-demo-app.";
  const TITLE_PREFIX = "Demo — ";
  const BASE = "http://demo.invalid";  // only to parse recorded keys; never requested

  // ---- storage: this tab only (sessionStorage), or memory when the browser refuses it ----------------------------
  const SS = (() => {
    try { const s = W.sessionStorage; s.setItem("wider-demo-probe", "1"); s.removeItem("wider-demo-probe"); return s; } catch (e) { return null; }
  })();
  const memory = new Map();
  const raw = {
    get(k) { if (SS) { try { return SS.getItem(k); } catch (e) { /* fall back */ } } return memory.has(k) ? memory.get(k) : null; },
    set(k, v) { if (SS) { try { SS.setItem(k, String(v)); return; } catch (e) { /* fall back */ } } memory.set(k, String(v)); },
    remove(k) { memory.delete(k); if (SS) { try { SS.removeItem(k); } catch (e) { /* nothing kept */ } } },
    keys() {
      const out = new Set(memory.keys());
      if (SS) { try { for (let i = 0; i < SS.length; i++) out.add(SS.key(i)); } catch (e) { /* memory only */ } }
      return [...out];
    },
  };

  // The app's localStorage, kept per tab under a prefix, so its wider.* keys never outlive the tab (D7).
  const appKeys = () => raw.keys().filter((k) => k.startsWith(APP_PREFIX)).map((k) => k.slice(APP_PREFIX.length)).sort();
  // change demo-chapters-and-video: the demo works as one fixed name (D6) and its side panel shows only Ask WIDER (D5)
  const PINNED = { "wider.reviewer": "Reviewer A", "wider.panelTab": "ask" };
  const appStore = {
    getItem: (k) => (k in PINNED ? PINNED[k] : raw.get(APP_PREFIX + k)),
    setItem: (k, v) => { if (!(k in PINNED)) raw.set(APP_PREFIX + k, v); },
    removeItem: (k) => raw.remove(APP_PREFIX + k),
    clear: () => appKeys().forEach((k) => raw.remove(APP_PREFIX + k)),
    key: (i) => appKeys()[i] ?? null,
    get length() { return appKeys().length; },
  };
  let appStoreOn = false;
  try {
    Object.defineProperty(W, "localStorage", { value: appStore, configurable: true });
    appStoreOn = W.localStorage === appStore;
  } catch (e) { appStoreOn = false; }
  function clearAppKeys() {
    appStore.clear();
    if (!appStoreOn) {  // the browser kept its own localStorage: remove the app's keys there instead
      try {
        const ls = W.localStorage, ks = [];
        for (let i = 0; i < ls.length; i++) if (String(ls.key(i)).startsWith("wider.")) ks.push(ls.key(i));
        ks.forEach((k) => ls.removeItem(k));
      } catch (e) { /* nothing stored */ }
    }
  }
  try {
    if (!appStoreOn) Object.entries(PINNED).forEach(([k, v]) => W.localStorage.setItem(k, v));
    // the panel starts closed in a fresh tab, so it does not compete with the tour's targets; Ask WIDER opens it (D5)
    if (!W.localStorage.getItem("wider.guide")) W.localStorage.setItem("wider.guide", "closed");
  } catch (e) { /* the app asks */ }
  // the name dialog never opens: common.js's click handler on [data-set-name] calls its own closure, so the click is
  // stopped before it gets there (D6); the elements are hidden too (demo.css)
  if (W.document && W.document.addEventListener) {
    W.document.addEventListener("click", (e) => {
      const b = e.target && e.target.closest && e.target.closest("[data-set-name]");
      if (b) { e.preventDefault(); e.stopImmediatePropagation(); }
    }, true);
  }

  // ---- state ------------------------------------------------------------------------------------------------------
  const first = () => SNAP.order[0] || "imported";
  const last = () => SNAP.order[SNAP.order.length - 1] || first();
  // version 2 (change demo-chapters-and-video): `path` is the tour's path (core: the Quick tour, all: every chapter),
  // `seen` the ids of the steps shown, `asked` the recorded Ask WIDER answers opened
  const fresh = () => ({ v: 2, checkpoint: first(), mode: null, step: 0, overlay: {}, job: null, seq: 0, path: "core",
    seen: [], asked: [] });
  function load() {
    try {
      const s = JSON.parse(raw.get(STATE_KEY) || "null");
      if (s && s.v === 2 && SNAP.checkpoints[s.checkpoint]) return Object.assign(fresh(), s);
    } catch (e) { /* a broken entry starts afresh */ }
    return fresh();
  }
  let state = load();
  // after every change: the tour re-checks a step that waits for the visitor (watchers never break a save)
  const watchers = [];
  function save() {
    try { raw.set(STATE_KEY, JSON.stringify(state)); } catch (e) { /* memory only */ }
    for (const fn of watchers) { try { fn(state); } catch (e) { /* a watcher's own problem */ } }
  }

  // ---- answers ----------------------------------------------------------------------------------------------------
  const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  // the key the recorder wrote: method, path, the query sorted and encoded as Python's quote(safe="!*'()") does
  function keyOf(method, url) {
    const pairs = [...url.searchParams.entries()].sort((a, b) => cmp(a[0], b[0]) || cmp(a[1], b[1]));
    const q = pairs.map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    return `${method} ${url.pathname}` + (q ? `?${q}` : "");
  }
  const rxCache = new Map();
  // "METHOD /api/a/{name}/b" as a pattern over "METHOD /path"
  function routeRx(entry) {
    if (!rxCache.has(entry)) {
      const body = entry.split(/(\{[^}]+\})/).map((p) => (/^\{[^}]+\}$/.test(p) ? "[^/]+" : p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))).join("");
      rxCache.set(entry, new RegExp(`^${body}$`));
    }
    return rxCache.get(entry);
  }
  const answers = () => SNAP.checkpoints[state.checkpoint] || {};
  const clone = (x) => (x === undefined ? undefined : JSON.parse(JSON.stringify(x)));

  function recorded(method, url) {
    const id = answers()[keyOf(method, url)];
    if (id) return SNAP.bodies[id];
    if (method !== "GET") return null;
    // a call that differs from a recorded one only in the named parameters (D4): Reviewer A's own recording first
    for (const [entry, ignore] of Object.entries(SNAP.patterns || {})) {
      if (!routeRx(entry).test(`${method} ${url.pathname}`)) continue;
      const reduce = (u) => { const c = new URL(u.href); ignore.forEach((p) => c.searchParams.delete(p)); return keyOf(method, c); };
      const want = reduce(url);
      let best = null;
      for (const k of Object.keys(answers()).sort()) {
        if (!k.startsWith(`${method} ${url.pathname}`)) continue;
        const u = new URL(k.slice(method.length + 1), BASE);
        if (u.pathname !== url.pathname || reduce(u) !== want) continue;
        const own = ignore.some((p) => u.searchParams.get(p) === "Reviewer A");
        if (!best || own) best = answers()[k];
        if (own) break;
      }
      if (best) return SNAP.bodies[best];
    }
    return null;
  }

  // the recording's model ran on this computer (Ollama at 127.0.0.1) but was never "checked", so the app would call
  // it "unknown" while Ask WIDER says it answered: the demo states where it ran
  function localModel(json) {
    const walk = (o) => {
      if (Array.isArray(o)) { o.forEach(walk); return; }
      if (!o || typeof o !== "object") return;
      if (o.locality === "unknown" && /^local-/.test(o.name || o.backend || "")) {
        o.locality = "local"; o.locality_label = "this computer"; if ("locality_source" in o) o.locality_source = "checked";
        if (/^unknown/.test(o.destination || "")) o.destination = "this computer (Ollama at 127.0.0.1)";
      }
      Object.values(o).forEach(walk);
    };
    walk(json);
    return json;
  }

  function reasonFor(method, path) {
    for (const [entry, why] of Object.entries(SNAP.not_in_demo || {})) if (routeRx(entry).test(`${method} ${path}`)) return why;
    return null;
  }

  // simulated actions (phase 2 adds them): handle("POST /api/labels", fn) answers a call; patch("GET /api/x", fn)
  // changes a recorded answer before it is returned. fn gets {method, url, body, state} (and the recorded json).
  // A handler may answer with a promise (a simulated run that takes a moment).
  const handlers = [], patches = [];
  const handle = (entry, fn) => handlers.push({ entry, fn });
  const patch = (entry, fn) => patches.push({ entry, fn });

  // {status, json} or {status, text, type}
  function answer(method, url, body) {
    const req = { method, url, body, state };
    for (const h of handlers) {
      if (routeRx(h.entry).test(`${method} ${url.pathname}`)) {
        const r = h.fn(req);
        if (r && typeof r.then === "function") return r.then((x) => { save(); return x; });
        if (r !== undefined) { save(); return r; }
      }
    }
    const rec = recorded(method, url);
    if (rec) {
      if (rec.type !== "json") return { status: rec.status || 200, text: rec.text, type: rec.type };
      let json = clone(rec.json);
      for (const p of patches) if (routeRx(p.entry).test(`${method} ${url.pathname}`)) json = p.fn(json, req);
      return { status: rec.status || 200, json: localModel(json) };
    }
    const why = reasonFor(method, url.pathname);
    if (method === "GET") return { status: 404, json: { detail: why || NOT_PART } };
    return { status: 409, json: { detail: why || NOT_HERE } };
  }

  function respond(a) {
    const empty = [204, 205, 304].includes(a.status);
    if (a.json !== undefined) {
      return new Response(empty ? null : JSON.stringify(a.json), { status: a.status, headers: { "Content-Type": "application/json" } });
    }
    return new Response(empty ? null : a.text, { status: a.status, headers: { "Content-Type": a.type || "text/plain" } });
  }

  // ---- nothing leaves the page (D2) -------------------------------------------------------------------------------
  function ownApi(target) {
    let url;
    try { url = new URL(target, W.location.href); } catch (e) { return null; }
    return url.origin === W.location.origin && url.pathname.startsWith("/api/") ? url : null;
  }
  W.fetch = async function demoFetch(input, init) {
    const target = typeof input === "string" ? input : (input && input.url) || String(input);
    const url = ownApi(target);
    if (!url) throw new TypeError(BLOCKED);
    const method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
    let body = init ? init.body : undefined;
    if (typeof body === "string") { try { body = JSON.parse(body); } catch (e) { /* kept as text */ } }
    await null;
    return respond(await answer(method, url, body));
  };
  function Refused() { throw new TypeError(BLOCKED); }
  for (const name of ["XMLHttpRequest", "WebSocket", "EventSource"]) {
    try { Object.defineProperty(W, name, { value: Refused, configurable: true, writable: true }); } catch (e) { /* CSP still blocks it */ }
  }
  try { if (W.navigator) Object.defineProperty(W.navigator, "sendBeacon", { value: () => false, configurable: true }); } catch (e) { /* CSP */ }
  function DemoNotification() { throw new TypeError("Notifications are off in the demo"); }
  DemoNotification.permission = "denied";
  DemoNotification.requestPermission = () => Promise.resolve("denied");
  try { Object.defineProperty(W, "Notification", { value: DemoNotification, configurable: true, writable: true }); } catch (e) { /* none */ }

  // the app sets the title to "WIDER" on every job poll (jobs.js); the prefix stays
  try {
    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Object.getPrototypeOf(document)), "title")
      || Object.getOwnPropertyDescriptor(Object.getPrototypeOf(document), "title");
    if (d && d.set) {
      Object.defineProperty(document, "title", {
        configurable: true,
        get() { return d.get.call(document); },
        set(v) { v = String(v); d.set.call(document, v.startsWith(TITLE_PREFIX) ? v : TITLE_PREFIX + v); },
      });
      document.title = document.title;
    }
  } catch (e) { /* the build's own title keeps the prefix */ }

  // ---- links: outside addresses never open; /api/ files are saved from the recording ------------------------------
  function isOutside(href) {
    if (!href) return false;
    if (/^\/\//.test(href)) return true;
    if (!/^[a-z][a-z0-9+.-]*:/i.test(href)) return false;
    try { return new URL(href, W.location.href).origin !== W.location.origin; } catch (e) { return true; }
  }
  function disarm(node) {
    if (!node || node.nodeType !== 1) return;
    const list = [node, ...(node.querySelectorAll ? node.querySelectorAll("a[href]") : [])];
    for (const a of list) {
      if (a.tagName !== "A") continue;
      const href = a.getAttribute("href");
      if (isOutside(href)) { a.setAttribute("data-demo-href", href); a.setAttribute("href", "#"); }
    }
  }
  function download(url, a) {
    const r = answer("GET", url, undefined);
    if (r.status !== 200) { notice((r.json && r.json.detail) || NOT_PART); return; }
    const text = r.json !== undefined ? JSON.stringify(r.json, null, 1) : r.text;
    const type = r.json !== undefined ? "application/json" : r.type || "text/plain";
    const name = a.getAttribute("download") || (url.searchParams.get("path") || url.pathname).split("/").pop() || "file";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type }));
    link.download = name;
    link.setAttribute("data-demo-own", "1");
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }
  function onLink(e) {
    const a = e.target && e.target.closest ? e.target.closest("a") : null;
    if (!a || a.getAttribute("data-demo-own")) return;
    const href = a.getAttribute("href") || "";
    if (a.getAttribute("data-demo-href") || isOutside(href)) {
      e.preventDefault(); e.stopPropagation(); notice(OUTSIDE_LINK); return;
    }
    const url = ownApi(href);
    if (url && !href.startsWith("#")) { e.preventDefault(); e.stopPropagation(); download(url, a); }
  }
  document.addEventListener("click", onLink, true);
  document.addEventListener("auxclick", onLink, true);
  if (W.MutationObserver) {
    new W.MutationObserver((list) => {
      for (const m of list) {
        if (m.type === "attributes") disarm(m.target);
        else m.addedNodes.forEach(disarm);
      }
    }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["href"] });
  }

  // ---- the Samples page in plain words: the app's short technical labels, said for a first visit ----------------------
  const PLAIN = [
    [/^N_h$/, "records in band"],
    [/^n_h$/, "drawn"],
    [/^weight$/, "each drawn record stands for"],
    [/^(.*?)(\d+) labels? without an established independence$/,
      "$1$2 labels not yet declared as made without seeing the AI model's answers (Reviewer A's labels are invented)"],
    [/^(.*?)(\d+) labels: criteria state not recorded \(saved before this version\)$/,
      "$1$2 labels were brought in with the demo, so WIDER cannot say whether the criteria were frozen when they were made"],
  ];
  function plainWords() {
    const page = document.getElementById("page-sample");
    if (!page || typeof document.createTreeWalker !== "function") return;
    const walk = document.createTreeWalker(page, NodeFilter.SHOW_TEXT);
    for (let n = walk.nextNode(); n; n = walk.nextNode()) {
      const s = n.nodeValue.trim();
      for (const [re, to] of PLAIN) if (re.test(s)) { n.nodeValue = s.replace(re, to); break; }
    }
    const alloc = page.querySelector('[data-val-field="allocation"]');
    if (alloc && alloc.placeholder === "S1=100,S2=200") alloc.placeholder = "records per band";
  }
  if (W.MutationObserver) {
    const watchSample = () => { const page = document.getElementById("page-sample"); if (page) { plainWords(); new W.MutationObserver(plainWords).observe(page, { childList: true, subtree: true }); } };
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchSample); else watchSample();
  }

  // ---- "not in the demo" notices: the app shows a refused call in its error colour; here it is no error ------------
  const OFF = [...new Set([NOT_PART, NOT_HERE, ...Object.values(SNAP.not_in_demo || {})])].map((m) => m.slice(0, 40));
  function markOff(root) {
    if (!root || root.nodeType !== 1) return;
    for (const e of [root, ...root.querySelectorAll("p, div, span, li, td")]) {
      if (e.children.length) continue;
      const t = (e.textContent || "").trim();
      if (t && OFF.some((m) => t.includes(m))) e.classList.add("demo-off");
      else e.classList.remove("demo-off");
    }
  }
  if (W.MutationObserver) {
    const watchOff = () => new W.MutationObserver((list) => {
      for (const m of list) markOff(m.type === "characterData" ? m.target.parentElement : m.target);
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watchOff); else watchOff();
  }

  // ---- the Demo Mode bar, the start card, Reset and Exit (D7) -------------------------------------------------------
  function h(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "text") e.textContent = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid) e.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    return e;
  }
  const byId = (id) => document.getElementById(id);
  let noticeTimer = null;
  function notice(text) {
    const n = byId("demo-notice");
    if (!n) return;
    n.textContent = text;
    n.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { n.hidden = true; n.textContent = ""; }, 8000);
  }
  const tour = () => W.DemoTour || null;

  function setMode(mode) {
    state.mode = mode;
    save();
    drawMode();
    const t = tour();
    if (t) { if (mode === "free") t.hide(); else t.resume(state); }
  }
  function drawMode() {
    const g = byId("demo-guided"), f = byId("demo-free");
    if (g) g.setAttribute("aria-pressed", String(state.mode !== "free"));
    if (f) f.setAttribute("aria-pressed", String(state.mode === "free"));
  }
  // the tab chosen inside a page (app.js keeps it in sessionStorage as wider.tab.<page>): a reset opens each page on
  // its first tab again
  function clearTabs() { raw.keys().filter((k) => k.startsWith("wider.tab.")).forEach((k) => raw.remove(k)); }
  // the page stays where it was (the app opens its start page when there is no hash); location.replace() to the
  // same address with a fragment would only scroll, not reload
  function reload() { W.location.reload(); }
  function reset(ask = true) {
    if (ask && !W.confirm("Start the demo again? Everything you did in the demo is cleared.")) return false;
    raw.remove(STATE_KEY);
    clearAppKeys();
    clearTabs();
    state = fresh();
    reload();
    return true;
  }
  // Exit goes back to where the demo was opened. The WIDER app's Home opens it in a new tab (owner, 31 Oct 2026): the
  // tab closes. The showcase page opens it in the same tab, where the browser refuses to close it (a tab with a
  // history of its own): it goes to the showcase page instead, the site's root next to /demo/
  const SHOWCASE = "../";
  function exit() {
    if (!W.confirm("Leave the demo? This closes the demo tab, or opens the WIDER showcase page when the tab stays open.")) return false;
    save();
    if (!appStoreOn) clearAppKeys();  // the browser kept its own localStorage: leave no typed name behind (D7)
    try { if (typeof W.close === "function") W.close(); } catch (e) { /* refused */ }
    // a refused close() leaves the tab open without an error: look again once the browser has had its say
    setTimeout(() => { if (!W.closed) W.location.assign(new URL(SHOWCASE, W.location.href).href); }, 200);
    return true;
  }
  function choose(mode, path) {
    const before = state.checkpoint;
    state.mode = mode;
    if (path) state.path = path;
    state.checkpoint = mode === "free" ? last() : first();
    state.step = 0;
    save();
    const card = byId("demo-start");
    if (card) { if (card.close) card.close(); card.remove(); }
    drawMode();
    if (state.checkpoint !== before) { W.location.hash = "home"; reload(); return; }
    W.location.hash = "home";
    const t = tour();
    if (t && mode === "guided") t.start(state);
  }

  function bar() {
    return h("div", { id: "demo-bar", class: "demo-bar", role: "region", "aria-label": "Demo mode" },
      h("span", { class: "demo-badge", text: "Demo mode" }),
      h("span", { class: "demo-bar-text", text: "Invented data, simulated results. Nothing you do here leaves this browser tab." }),
      h("span", { id: "demo-notice", class: "demo-notice", role: "status", "aria-live": "polite", hidden: true }),
      h("span", { class: "demo-bar-actions" },
        h("span", { class: "demo-switch", role: "group", "aria-label": "How to explore the demo" },
          h("button", { type: "button", id: "demo-guided", "aria-pressed": "true", text: "Guided", onclick: () => setMode("guided") }),
          h("button", { type: "button", id: "demo-free", "aria-pressed": "false", text: "Free", onclick: () => setMode("free") })),
        h("button", { type: "button", id: "demo-show-tour", class: "demo-quiet", text: "Show tour", hidden: true,
          onclick: () => { const t = tour(); if (t) t.resume(state); } }),
        h("button", { type: "button", id: "demo-reset", class: "demo-quiet", text: "Reset", title: "Start the demo again",
          onclick: () => reset(true) }),
        h("button", { type: "button", id: "demo-exit", class: "demo-quiet", text: "Exit", title: "Back to WIDER",
          onclick: () => exit() })));
  }

  const coreSteps = () => ((SNAP.tour && SNAP.tour.steps) || []).filter((s) => s.core).length;
  function startCard() {
    const d = h("dialog", { id: "demo-start", class: "demo-start", "aria-labelledby": "demo-start-title" },
      h("h2", { id: "demo-start-title", text: "Try WIDER with an invented review" }),
      h("p", { text: "This is WIDER's own interface with a small invented project: 74 records from three made-up " +
        "databases about employee voice and silence. Model runs are replayed from a recording, so nothing is sent " +
        "anywhere and nothing costs anything." }),
      h("div", { class: "demo-start-choices" },
        h("button", { type: "button", id: "demo-start-quick", class: "demo-choice", onclick: () => choose("guided", "core") },
          h("b", { text: "Quick tour (recommended)" }),
          h("span", { text: `${coreSteps()} steps, about 5 minutes: what you decide, the two model reads and where they ` +
            "disagree, and how sure the recall estimate is." })),
        h("button", { type: "button", id: "demo-start-all", class: "demo-choice demo-quiet", onclick: () => choose("guided", "all") },
          h("b", { text: "All chapters" }),
          h("span", { text: "Every stage of the review, one chapter each, with what you decide and the trade-offs." })),
        h("button", { type: "button", id: "demo-start-free", class: "demo-choice demo-quiet", onclick: () => choose("free") },
          h("b", { text: "Explore freely" }),
          h("span", { text: "The example project after screening and recall: open any page and try anything. Optional steps it skipped, such as missing abstracts, show as not started." }))),
      h("p", { class: "demo-narrow", text: "This demo is made for a laptop or desktop screen." }),  // shown by demo.css below 900px
      h("p", { class: "demo-start-foot", text: "You can switch between the tour and free exploring at any time with the switch in the Demo mode bar. " +
        "Reset in the same bar starts the demo again from the beginning." }));
    d.addEventListener("cancel", (e) => e.preventDefault());  // a choice is needed; Escape does not skip it
    d.addEventListener("close", () => { if (!state.mode) choose("free"); });  // a browser that closes it anyway
    return d;
  }

  function mount() {
    document.documentElement.classList.add("demo-on");
    document.body.insertBefore(bar(), document.body.firstChild);
    drawMode();
    disarm(document.body);
    if (!state.mode) {
      const card = startCard();
      document.body.appendChild(card);
      try { card.showModal(); } catch (e) { card.setAttribute("open", ""); }
      const g = byId("demo-start-quick");
      if (g && g.focus) g.focus();
    } else if (state.mode === "guided") {
      // tour.js loads after this file: look for the tour once the page has loaded
      W.addEventListener("load", () => { const t = tour(); if (t) t.resume(state); });
    }
  }
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);

  W.Demo = {
    get state() { return state; },
    save, reset, exit, choose, setMode, notice, handle, patch, keyOf,
    onSave(fn) { watchers.push(fn); },
    answer: (method, target, body) => answer(String(method).toUpperCase(), new URL(target, W.location.href), body),
    setCheckpoint(name) { if (SNAP.checkpoints[name]) { state.checkpoint = name; save(); } return state.checkpoint; },
    // the recorded json of a GET at this checkpoint or another, before any patch (null when not recorded)
    // (method: another recorded call, e.g. "POST", matched exactly)
    recorded(target, checkpoint, method) {
      const keep = state.checkpoint;
      if (checkpoint) { if (!SNAP.checkpoints[checkpoint]) return null; state.checkpoint = checkpoint; }
      try { const r = recorded(method || "GET", new URL(target, W.location.href)); return r && r.type === "json" ? clone(r.json) : null; }
      finally { state.checkpoint = keep; }
    },
    has: (name) => !!SNAP.checkpoints[name],
    appStoreOn: () => appStoreOn,
    order: () => SNAP.order.slice(),
  };
})();
