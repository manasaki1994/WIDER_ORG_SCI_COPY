// Shared helpers, loaded before every other script: one escape function, one fetch helper (with the online-lookup
// consent dialog) and one way to fill a select. Other files alias these instead of defining their own.

// Escape a value for HTML text and attribute positions. null and undefined become "".
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// A card's command for scripts: a closed "Command line" fold at the card's end, never in the running text (C6 D5)
const cmdFold = (html) => `<details class="cmdline"><summary>Command line</summary><p class="small">${html}</p></details>`;

// One time format for every page (accessibility-and-naming D7): a stored ISO time as the reader's local date and
// time ("25 Oct 2026, 14:05"); an empty value is "", a value that is not a time is shown as it is.
function fmtWhen(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  // local time with the zone named, the rule the CLI and the reports follow (provenance.local_time)
  return isNaN(d) ? String(iso) : d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
}

// Turn an error body's `detail` (a string, a 422 list or an object with a message) into one readable line.
function errorText(detail, fallback) {
  if (detail == null || detail === "") return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(d => (d && d.msg) ? (d.loc ? d.loc.slice(1).join(".") + ": " : "") + d.msg : JSON.stringify(d)).join("; ");
  if (detail.message) return detail.message;
  return JSON.stringify(detail);
}

// The control focused last outside a dialog: a button disabled while its request runs drops the focus to the page
// body, and the consent dialog still knows where to return it.
let lastControl = null;
document.addEventListener("focusin", (e) => {
  const t = e.target;
  // a toast's button is never the control to return to: its re-drawn twin would be the next toast's button
  if (t && t !== document.body && !(t.closest && t.closest("dialog, #toasts"))) lastControl = t;
});

// The page re-renders a card after an action, or disables its button while the request runs, and the browser then
// drops the focus to the page body. The control focused last, or its re-rendered twin (same id or same data-*
// attribute), gets the focus back when it is visible and enabled; a click on empty page space lets it go.
function focusKey(el) {
  if (!el || !el.getAttribute) return null;
  const fk = el.getAttribute("data-focus-key");
  if (fk) return `[data-focus-key="${CSS.escape(fk)}"]`;
  if (el.id) return `#${CSS.escape(el.id)}`;
  const a = [...(el.attributes || [])].find(x => x.name.startsWith("data-"));
  return a ? `${el.tagName.toLowerCase()}[${a.name}="${CSS.escape(a.value)}"]` : null;
}
function restoreFocus(doc, last) {
  if (!last || doc.activeElement !== doc.body) return null;
  let el = last.isConnected ? last : null;
  if (!el) { const k = focusKey(last); el = k ? doc.querySelector(k) : null; }
  if (!el || el.disabled || el.hidden || !el.getClientRects().length || el.closest("[hidden], dialog:not([open])")) return null;
  el.focus();
  return el;
}
// Focus rules in one place (D2): after an action that redraws, the focus goes to `el` while it is still usable, else to
// its re-drawn twin (same data-focus-key, id or data-* attribute), else to `fallback` (an element or a selector). It
// moves nothing when the researcher has put the focus on another control meanwhile.
function focusAfter(el, fallback) {
  const a = document.activeElement;
  if (a && a !== document.body && a !== el && a.isConnected && !a.disabled) return a;
  const usable = (x) => !!x && !!x.isConnected && !x.disabled && x.getClientRects().length > 0;   // not hidden, not in a closed dialog
  let t = usable(el) ? el : null;
  if (!t) { const k = focusKey(el), twin = k && document.querySelector(k); if (usable(twin)) t = twin; }
  if (!t) { const f = typeof fallback === "string" ? document.querySelector(fallback) : fallback; if (usable(f)) t = f; }
  if (t && t !== a) t.focus();
  return t;
}
function watchFocus() {
  let timer = null;
  new MutationObserver(() => {
    if (timer || document.activeElement !== document.body || !lastControl) return;
    timer = setTimeout(() => { timer = null; const el = restoreFocus(document, lastControl); if (el) lastControl = el; }, 0);
  }).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["disabled", "hidden"] });
  document.addEventListener("mousedown", (e) => {
    if (!(e.target.closest && e.target.closest("a, button, input, select, textarea, summary, label, [tabindex], [contenteditable]"))) lastControl = null;
  });
  window.addEventListener("hashchange", () => { lastControl = null; });   // a new page starts without the old control
}
if (typeof MutationObserver === "function" && document.documentElement) watchFocus();

// Focus a control again. One disabled while its request runs gets the focus once it is enabled again, unless the
// researcher has put the focus somewhere else meanwhile.
function focusWhenEnabled(el) {
  if (!el || !el.isConnected || !el.focus) return;
  if (!el.disabled) { el.focus(); return; }
  const obs = new MutationObserver(() => {
    if (el.disabled) return;
    obs.disconnect();
    const a = document.activeElement;
    if (el.isConnected && (!a || a === document.body)) el.focus();
  });
  obs.observe(el, { attributes: true, attributeFilter: ["disabled"] });
  setTimeout(() => obs.disconnect(), 60000);
}

// The in-page dialog for every confirmation, name or text (accessibility-and-naming D1; no browser confirm/prompt/alert):
// resolves { act, fields } for the pressed [data-act] button, with each [data-field] input's trimmed value, or null on
// Cancel or Esc. Cancel has the focus, so nothing starts on a stray Enter; the focus returns to the opener. With
// opts.enter, Enter in a [data-field] input presses that action (a typed label or edit, never an irreversible step).
// While one is open a second call resolves null (a cancel), so a double click cannot stack two dialogs and act twice.
// An error tied to its field (WCAG 3.3.1): the message in red at normal size, aria-invalid on the field and the
// message named in its aria-describedby; editing the field clears aria-invalid.
function fieldError(field, msg, text) {
  if (!msg) return;
  msg.innerHTML = `<span class="bad">${esc(text)}</span>`; msg.classList.remove("small");
  if (!field) return;
  field.setAttribute("aria-invalid", "true");
  const ids = (field.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
  if (msg.id && !ids.includes(msg.id)) field.setAttribute("aria-describedby", [...ids, msg.id].join(" "));
  field.addEventListener("input", () => field.removeAttribute("aria-invalid"), { once: true });
}
function askChoice(html, opts = {}) {
  if (document.querySelector("dialog.choice-dialog[open]")) return Promise.resolve(null);
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "consent-dialog choice-dialog"; dlg.setAttribute("aria-labelledby", "choice-title");
    if (opts.describedby) dlg.setAttribute("aria-describedby", opts.describedby);   // the dialog's own sentences, read on opening
    dlg.innerHTML = html;
    document.body.appendChild(dlg);
    const opener = document.activeElement && document.activeElement !== document.body ? document.activeElement : lastControl;
    const finish = (act) => {
      const fields = Object.fromEntries([...dlg.querySelectorAll("[data-field]")].map(i => [i.dataset.field, i.value.trim()]));
      dlg.close(); dlg.remove(); focusWhenEnabled(opener); resolve(act === "cancel" ? null : { act, fields });
      // a dialog that opened by itself (the first-time name question) has no opener: the focus goes to the main
      // area instead of falling to the page body (WCAG 2.4.3), once the caller has re-rendered what it changed
      if (!opener || !opener.isConnected) setTimeout(() => {
        const a = document.activeElement, m = document.querySelector && document.querySelector("#main");
        if ((!a || a === document.body) && m && m.focus) m.focus();
      }, 0);
    };
    dlg.addEventListener("click", (ev) => { const a = ev.target.closest("[data-act]"); if (a) finish(a.dataset.act); });
    dlg.addEventListener("cancel", (ev) => { ev.preventDefault(); finish("cancel"); });
    if (opts.enter) dlg.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && ev.target.matches && ev.target.matches("input[data-field]")) { ev.preventDefault(); finish(opts.enter); }
    });
    dlg.showModal();
    dlg.querySelector("[data-act=cancel]").focus();
  });
}
// A yes/no question whose button names its action ("Cancel the run", "Remove"): resolves true only on that button.
function askConfirm(text, action) {
  return askChoice(`<p id="choice-title">${esc(text)}</p><div class="row"><button type="button" data-act="ok">${esc(action)}</button>` +
    `<button type="button" class="secondary" data-act="cancel">Cancel</button></div>`).then(r => !!r);
}
// Text fields typed in the dialog: fields is [{ key, label, value }]; resolves { key: trimmed value } on the action
// button (or Enter in a field), null on Cancel or Esc. Cancel has the focus, as in every dialog.
function askFields(title, fields, action) {
  const inputs = fields.map(f => `<label class="dlg-field">${esc(f.label)} <input data-field="${esc(f.key)}" value="${esc(f.value || "")}"></label>`).join("");
  return askChoice(`<h3 id="choice-title">${esc(title)}</h3>${inputs}<div class="row"><button type="button" data-act="ok">${esc(action)}</button>` +
    `<button type="button" class="secondary" data-act="cancel">Cancel</button></div>`, { enter: "ok" }).then(r => r ? r.fields : null);
}

// --- WiderName ---
// One name per browser, with no login, that signs what the pages save (ui-redesign D33). Home and a project opened
// without one ask for it; the top bar shows "Working as: <name> · Change". Switching to another name stores it and
// reloads the page, so nothing the earlier name had in view stays on screen; another open tab reloads too. A first
// name (none before) is only stored: there is no earlier name's work to hide, and a reload would drop unsaved input.
const WiderName = (() => {
  const KEY = "wider.reviewer";
  const NEED = "enter your name first: use Change beside “Working as” in the top bar";
  const read = () => { try { return String(localStorage.getItem(KEY) || "").trim(); } catch (e) { return ""; } };
  let current = read(), askedOnce = false;
  const changed = () => {
    render();
    try { document.dispatchEvent(new CustomEvent("widername", { detail: current })); } catch (e) { /* no events in tests */ }
  };
  // Store a name ("" forgets it). Returns true when it changed; another name than a stored one reloads the page.
  function set(name) {
    const n = String(name || "").trim();
    if (n === current) return false;
    try { if (n) localStorage.setItem(KEY, n); else localStorage.removeItem(KEY); } catch (e) { /* blocked storage: this page only */ }
    const before = current; current = n;
    if (before) location.reload(); else changed();
    return true;
  }
  // The name dialog. first: the once-only question on opening a project ("Not now" instead of "Cancel").
  async function ask(opts = {}) {
    const r = await askChoice(`<h3 id="choice-title">Your name</h3>`
      + `<label class="dlg-field"><span class="sr-only">Your name</span><input data-field="name" autocomplete="name" value="${esc(current)}"></label>`
      + `<div id="name-dlg-desc"><p>Decisions, labels and notes are signed with this name.</p><p>WIDER has no login: anyone at this computer can change it.</p>`
      + (current ? `<p>Saving another name reloads the page and any other WIDER tab; anything not saved is dropped.</p>` : "") + `</div>`
      + `<div class="row"><button type="button" data-act="ok">Save</button>`
      + `<button type="button" class="secondary" data-act="cancel">${opts.first ? "Not now" : "Cancel"}</button></div>`, { enter: "ok", describedby: "name-dlg-desc" });
    if (r) set(r.fields.name);
    return current;
  }
  // Opening a project without a name asks once per page load; nothing is blocked after "Not now".
  function askOnce() { if (current || askedOnce) return; askedOnce = true; ask({ first: true }); }
  // A refusal for a save without a name: the line and a button that opens the dialog.
  const needHtml = (lead) => `${esc(lead ? lead + " " : "")}${esc(NEED)}. <button type="button" class="secondary small" data-set-name>Set your name</button>`;
  function render() {
    const slot = document.getElementById && document.getElementById("tb-name");
    if (slot) slot.innerHTML = current
      ? `<span class="tb-name-lbl">Working as:</span> <b class="tb-name-nm">${esc(current)}</b> <span aria-hidden="true">·</span> `
        + `<button type="button" class="tb-link" data-set-name>Change<span class="sr-only"> name</span></button>`
      : `No name yet <span aria-hidden="true">·</span> <button type="button" class="tb-link" data-set-name>Set name</button>`;
    const home = document.getElementById && document.getElementById("home-name");
    if (home) {
      home.hidden = !!current;
      if (!current && !home.querySelector("#home-name-input")) {
        home.innerHTML = `<h2 id="home-name-h">Your name</h2><p>Decisions, labels and notes are signed with this name. `
          + `WIDER has no login: anyone at this computer can change it.</p><div class="row"><label class="dlg-field">`
          + `<span class="sr-only">Your name</span><input id="home-name-input" autocomplete="name" aria-labelledby="home-name-h"></label>`
          + `<button type="button" id="home-name-save">Save</button></div>`;
        const inp = home.querySelector("#home-name-input");
        home.querySelector("#home-name-save").onclick = () => set(inp.value);
        inp.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); set(inp.value); } };
      }
    }
  }
  if (document.addEventListener) document.addEventListener("click", (e) => {
    const b = e.target && e.target.closest && e.target.closest("[data-set-name]");
    if (b) { e.preventDefault(); ask(); }
  });
  // Another tab switched the name: reload so this one never shows the earlier name's work under the new one.
  const follow = () => {
    const n = read();
    if (n === current) return;
    if (current) location.reload(); else { current = n; changed(); }
  };
  if (typeof window !== "undefined" && window.addEventListener) {
    window.addEventListener("storage", (e) => { if (e.key === KEY || e.key === null) follow(); });
    // a page brought back by Back or Forward missed the storage event while it was put away
    window.addEventListener("pageshow", (e) => { if (e.persisted) follow(); });
  }
  return { KEY, NEED, get: () => current, set, ask, askOnce, needHtml, render };
})();
window.WiderName = WiderName;
// --- end WiderName ---

// Ask for online-lookup consent: resolves true after "Allow and continue" stored the consent, false on "Cancel".
function askLookupConsent(info) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "consent-dialog";
    dlg.setAttribute("aria-labelledby", "consent-title");
    const services = (info.services || []).map(esc).join(", ");
    const fields = (info.fields || []).map(esc).join(", ");
    const terms = info.switch === "terms";
    // the refusal's long message is the CLI's text with its command; the dialog shows only its own lines
    dlg.innerHTML = `<h2 id="consent-title">${terms ? "Allow search-term lookups?" : "Allow online lookups?"}</h2>
      <p><b>Services:</b> ${services}<br><b>${terms ? "Sent per request" : "Sent for each paper"}:</b> ${fields}</p>
      ${info.note ? `<p>${esc(info.note)}</p>` : ""}
      <p class="muted small">Nothing has been sent yet, and nothing else leaves this computer. Stored in this project; revoke in Settings.</p>
      <p class="bad" role="alert" data-role="consent-error"></p>
      <div class="row"><button type="button" data-act="allow">Allow and continue</button>
      <button type="button" class="secondary" data-act="cancel" autofocus>Cancel</button></div>`;
    document.body.appendChild(dlg);
    const active = document.activeElement;
    const opener = active && active !== document.body ? active : lastControl;
    let done = false;
    const finish = async (allow) => {
      if (done) return; done = true;
      if (allow) {   // a failed Allow stays in the dialog with its error; nothing starts until it is stored or cancelled
        let err = "";
        try {
          const r = await fetch(terms ? "/api/lookups/terms/allow" : "/api/lookups/allow", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
          if (!r.ok) { let b = {}; try { b = await r.json(); } catch (e) { /* not JSON */ } err = errorText(b && b.detail, `the server failed (HTTP ${r.status})`); }
        } catch (e) { err = `the server did not answer: ${e.message}`; }
        if (err) {   // say what is stored now, read back from the project, never guessed from the failure
          let stored = null;
          try { const s = await fetch("/api/lookups"); if (s.ok) { const j = await s.json(); stored = !!(terms ? j.terms_allowed : j.allowed); } } catch (e) { /* unknown */ }
          const word = terms ? "search-term lookups" : "online lookups";
          const text = stored === true ? `The consent was stored, but ${err}. Allow and continue goes on; to withdraw it, revoke ${word} in Settings.`
            : stored === false ? (/not stored/.test(err) ? err : `The consent was not stored: ${err}.`)
            : `Whether the consent was stored is unknown: ${err}. Open Settings to see it before you continue.`;
          dlg.querySelector("[data-role=consent-error]").textContent = text; done = false; return;
        }
      }
      const ok = allow;
      dlg.close(); dlg.remove();
      focusWhenEnabled(opener);
      resolve(ok);
    };
    dlg.addEventListener("click", (ev) => { const a = ev.target.closest("[data-act]"); if (a) finish(a.dataset.act === "allow"); });
    dlg.addEventListener("cancel", (ev) => { ev.preventDefault(); finish(false); });
    dlg.showModal();
    dlg.querySelector("[data-act=cancel]").focus();  // consent is never one Enter away
  });
}

// The backend consent dialog (403 backend_consent_required): where it sends to first, a warning in its own box, then
// what is sent and for which use. The CLI's long message is not repeated: it says the same and names a CLI command.
// A pure builder, so tests check its escaping.
function backendConsentHtml(info) {
  const svc = info.kind === "embedding" ? "embedding service" : "AI model";
  const what = (info.changed || []).map(c => esc(c)).join(", ");
  const changed = what ? `<p class="small">It asks again because ${what} changed since it was allowed.</p>` : "";
  return `<h2 id="backend-consent-title">Send to the ${svc} ${esc(info.backend)}?</h2>
    <p class="consent-dest"><b>Sends to:</b> ${esc(info.destination || info.locality || "unknown")}</p>
    ${info.warning ? `<p class="consent-warn" role="note"><b>Warning:</b> ${esc(info.warning)}</p>` : ""}
    <p><b>What is sent:</b> ${esc(Array.isArray(info.sent) ? info.sent.join(", ") : (info.sent || ""))}<br>
    <b>For:</b> ${esc(info.label || info.operation || "")}</p>${changed}
    ${info.operation === "assistant"
      ? `<p class="row"><label><input type="checkbox" data-remember checked> Remember my choice for Ask WIDER</label></p>
    <p>Nothing has been sent yet. Remembered: stored for this ${svc} and Ask WIDER only, until you revoke it on Settings. Not remembered: this question only; neither your choice nor the question and its answer is stored.</p>`
      : `<p class="muted small">Nothing has been sent yet. Stored for this ${svc} and this use only; revoke on Settings.</p>`}
    <div class="row"><button type="button" data-act="allow">Allow and continue</button>
    <button type="button" class="secondary" data-act="cancel">Cancel</button></div>`;
}
// Ask for backend consent: resolves true after "Allow and continue" stored the consent for this operation, false on
// Cancel (nothing stored, nothing sent). For Ask WIDER with "Remember my choice" unticked, "Allow" stores nothing and
// resolves "once": the caller sends the one request again with X-Wider-Consent-Once (guide-assistant D8).
function askBackendConsent(info) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "consent-dialog";
    dlg.setAttribute("aria-labelledby", "backend-consent-title");
    dlg.innerHTML = backendConsentHtml(info);
    document.body.appendChild(dlg);
    const active = document.activeElement;
    const opener = active && active !== document.body ? active : lastControl;
    let done = false;
    const finish = async (allow) => {
      if (done) return; done = true;
      let ok = false;
      const remember = info.operation === "assistant" ? dlg.querySelector("[data-remember]") : null;
      if (allow && remember && !remember.checked) ok = "once";
      else if (allow) {
        const coll = info.kind === "embedding" ? "embeddings" : "providers";
        try {
          const r = await fetch(`/api/${coll}/${encodeURIComponent(info.backend)}/consent`, { method: "POST",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify({ operations: [info.operation] }) });
          ok = r.ok;
        } catch (e) { ok = false; }
      }
      try { dlg.close(); dlg.remove(); focusWhenEnabled(opener); }
      finally { resolve(ok); }                     // M124: the question is sent again (or not) whatever the tidy-up does
    };
    dlg.addEventListener("click", (ev) => { const a = ev.target.closest("[data-act]"); if (a) finish(a.dataset.act === "allow"); });
    dlg.addEventListener("cancel", (ev) => { ev.preventDefault(); finish(false); });
    dlg.addEventListener("close", () => finish(false));  // closed any other way: nothing stored, nothing sent
    dlg.showModal();
    dlg.querySelector("[data-act=cancel]").focus();  // consent is never one Enter away
  });
}

let stageRefreshTimer = null;
const UNREACHABLE = "WIDER is not answering. Start WIDER again the way you started it, then reload this page.";
// Fetch a JSON API route. Errors throw with a readable message (err.status, err.detail and the whole err.body kept,
// so a 409 refusal's own fields, e.g. backend_change, reach the page). A 403 asking for
// online-lookup or backend consent opens its consent dialog; after "Allow and continue" the request is sent once more.
// Each kind of consent is asked at most once per request, so a request needing two (the OpenAlex seed check: the term
// switch, then the DOI consent) shows two dialogs, and a refusal after "Allow" is never asked again.
// With `withHeaders: true` it returns `{body, headers}` (the labelling queue reads its X-Left-Out count so).
const api = async (path, opts = {}, asked = []) => {
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const headers = isForm ? (opts.headers || {}) : { "Content-Type": "application/json", ...(opts.headers || {}) };
  let r;
  // the browser's own "Failed to fetch" / "Load failed" means WIDER itself stopped answering: say so in words
  try { r = await fetch(path, { ...opts, headers }); }
  catch (e) { const err = new Error(UNREACHABLE); err.unreachable = true; throw err; }
  if (!r.ok) {
    let detail = null, body = null;
    try { body = await r.json(); detail = body ? body.detail : null; } catch (e) { detail = null; }
    const kind = detail && detail.consent_required ? (detail.consent_required.switch === "terms" ? "terms" : "lookups") : "backend";
    if (r.status === 403 && detail && detail.consent_required && !asked.includes(kind)) {
      if (await askLookupConsent(detail.consent_required)) return api(path, opts, [...asked, kind]);
      const err = new Error(detail.consent_required.switch === "terms" ? "Nothing was sent: search-term lookups are off for this project."
        : "Nothing was sent: online lookups are not allowed for this project.");
      err.status = 403; err.detail = detail; err.cancelled = true; throw err;
    }
    if (r.status === 403 && detail && detail.backend_consent_required && !asked.includes(kind)) {
      const info = detail.backend_consent_required;
      const got = await askBackendConsent(info);
      if (got === "once") {   // this request only: the server checks the fingerprint the dialog showed
        const once = `${info.kind}:${info.backend}:${info.operation}:${info.fingerprint}`;
        const prev = (opts.headers || {})["X-Wider-Consent-Once"];
        return api(path, { ...opts, headers: { ...(opts.headers || {}), "X-Wider-Consent-Once": prev ? `${prev},${once}` : once } }, [...asked, kind]);
      }
      if (got) return api(path, opts, [...asked, kind]);
      // in words where the server names the use ("answer questions in Ask WIDER"), never only its code word
      const err = new Error(info.label ? `Nothing was sent: you did not allow ${info.backend} to ${info.label}.`
        : `Nothing was sent: ${info.backend} is not allowed for ${info.operation}.`);
      err.status = 403; err.detail = detail; err.cancelled = true; throw err;
    }
    const err = new Error(errorText(detail, r.statusText || `HTTP ${r.status}`));
    err.status = r.status; err.detail = detail; err.body = body; throw err;
  }
  // any write may complete a stage: refresh the nav badges shortly after; not while the labelling queue is open
  // (records.js queueOpen), so the sidebar, footer, journey line and guide cannot change between two labels and hint
  // which record was a development record (ui-redesign D4); the held refresh runs when the queue is left (shell.js)
  if (opts.method && opts.method !== "GET" && !path.startsWith("/api/stages")) {
    if (window.queueOpen && window.queueOpen()) window.stageRefreshHeld = true;
    else { clearTimeout(stageRefreshTimer); stageRefreshTimer = setTimeout(() => { if (window.refreshStages) refreshStages(); }, 400); }
    if (SendNotes.CHANGES.test(path)) SendNotes.refresh();   // a consent, backend or source change rewrites the sentences
  }
  const body = await r.json();
  return opts.withHeaders ? { body, headers: r.headers } : body;
};

// What a run sends, beside its button (ui-redesign D17): an element `<p class="send-note" data-send="<action>">` (with
// `data-send-backend="<name>"` for one backend's card) gets the sentence of GET /api/send-notice/<action>, the one the
// CLI prints before the run; `data-send-part="general"` or `"consent_line"` takes only that half (model cards, D29);
// `data-send-local-line` names what runs on this computer where a note beside it may send something out (Ask WIDER's
// section search beside the answer's sentence). The server builds every word; this only fetches and places it, for the page on screen:
// on opening a page, when a page draws a new sending button, and after a consent, backend or source change.
const SendNotes = (() => {
  const CHANGES = /lookups|consent|protocol|provider|embedding|backend/;
  const url = (el) => `/api/send-notice/${encodeURIComponent(el.dataset.send)}`
    + (el.dataset.sendBackend ? `?backend=${encodeURIComponent(el.dataset.sendBackend)}` : "");
  const onScreen = (el) => { const pg = el.closest ? el.closest(".page") : null; return !pg || !pg.hidden; };
  const known = {};
  const LOCAL_LINE = "Runs on this computer; nothing leaves it.";
  // fill the notes under root that are on screen (all of them when `again`, else only those not filled yet)
  async function fill(root, again = false) {
    const els = [...(root || document).querySelectorAll("[data-send]")].filter(el => onScreen(el) && (again || !el.dataset.sendDone));
    const byUrl = {};
    for (const el of els) { el.dataset.sendDone = "1"; (byUrl[url(el)] ||= []).push(el); }
    if (again) for (const k in known) delete known[k];
    await Promise.all(Object.entries(byUrl).map(async ([u, list]) => {
      let n = known[u];   // a page drawing its card again reuses the sentence until a page change or a setting change
      if (n === undefined) {
        try { n = (await api(u)) || {}; } catch (e) { n = {}; }   // an unknown action shows nothing, never a guess
        known[u] = n;
      }
      for (const el of list) {
        const part = (el.dataset && el.dataset.sendPart) || "sentence";
        // nothing leaves the computer (a model on this computer or the test model, no consent needed): the page says
        // so once, near its first sending button, and a model card adds nothing (UX review F12); a note for anything
        // that leaves the computer keeps its full sentence
        const local = (n.locality === "local" || n.locality === "test") && n.allowed === true;
        if (local && part !== "general") el.dataset.sendLocal = part === "sentence" ? "line" : "none";
        else delete el.dataset.sendLocal;
        const line = (el.dataset && el.dataset.sendLocalLine) || LOCAL_LINE;
        const t = local ? (part === "sentence" ? line : part === "general" ? n.general || "" : "") : n[part] || "";
        el.textContent = t; el.hidden = !t;
      }
    }));
    oncePerPage(typeof document !== "undefined" ? document : root);
  }
  // of the "nothing leaves" lines, only the first of each page stays shown
  function oncePerPage(root) {
    const seen = new Set();
    for (const el of (root || document).querySelectorAll("[data-send]")) {
      const kind = el.dataset && el.dataset.sendLocal;
      if (!kind) continue;
      if (kind === "none") { el.hidden = true; continue; }
      const page = el.closest ? el.closest(".page") : null;
      if (page && page.hidden) continue;
      el.hidden = seen.has(page);
      seen.add(page);
    }
  }
  let timer = null, pendingAgain = false;   // a refill asked for is never dropped by a later plain fill
  const later = (again) => {
    pendingAgain = pendingAgain || again; clearTimeout(timer);
    timer = setTimeout(() => { const a = pendingAgain; pendingAgain = false; fill(document, a); }, pendingAgain ? 400 : 50);
  };
  if (typeof MutationObserver !== "undefined" && typeof addEventListener === "function" && typeof document !== "undefined" && document.body) {
    new MutationObserver((muts) => {
      if (muts.some(m => [...m.addedNodes].some(n => n.nodeType === 1 && (n.matches("[data-send]") || n.querySelector("[data-send]"))))) later(false);
    }).observe(document.body, { childList: true, subtree: true });
    addEventListener("hashchange", () => later(true));
    addEventListener("DOMContentLoaded", () => later(true));
  }
  return { fill, CHANGES, LOCAL_LINE, refresh: () => later(true) };
})();

// Fill a <select> (element or selector) with options: strings, or {value, label}. Keeps the current choice when it is
// still offered. With no options it shows a disabled placeholder and disables `button` (element or selector), so no
// request goes out with an empty parameter. Returns true when there is at least one option.
function fillSelect(sel, opts, placeholder = "nothing to choose yet", button = null) {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) return false;
  const btn = typeof button === "string" ? document.querySelector(button) : button;
  const items = (opts || []).map(o => (o !== null && typeof o === "object") ? { value: String(o.value), label: String(o.label ?? o.value) } : { value: String(o), label: String(o) });
  const prev = el.value;
  if (!items.length) {
    el.innerHTML = `<option value="" disabled selected>${esc(placeholder)}</option>`;
    el.disabled = true;
    if (btn) btn.disabled = true;
    return false;
  }
  el.innerHTML = items.map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  el.disabled = false;
  if (items.some(o => o.value === prev)) el.value = prev;
  if (btn) btn.disabled = false;
  return true;
}

// A protocol's result sets as {value, label} options: labelled with protocol, set, label, role, models and run ids.
// `source` ({protocol, set}) keeps only the second-screen sets built from that set.
function resultSetOptions(protocol, sets, source = null) {
  const keep = (sets || []).filter(s => !source || !s.source_protocol || (s.source_protocol === source.protocol && (source.set == null || s.source_set === source.set)));
  return keep.map(s => ({ value: String(s.result_set), label: [`${protocol} set ${s.result_set}${s.active ? " (active)" : ""}`,
    s.label, s.role, (s.models || []).join(", "), (s.run_ids || []).join(", ")].filter(Boolean).join(" · ") }));
}

// Fill a result-set select (Recall page, LLM screening page) and choose the active set by default; choosing another
// set here only changes what the page reads, never the active set. A set the researcher chose is kept on reload for
// the same protocol; otherwise the select follows the active set (also after "Use for analysis"). Returns the chosen
// set number or null.
function resultSetSelect(sel, protocol, sets, source = null) {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) return null;
  if (!el.dataset.listening && el.addEventListener) {
    el.addEventListener("change", () => { el.dataset.chosen = el.value; });
    el.dataset.listening = "1";
  }
  if (el.dataset.protocol !== protocol) delete el.dataset.chosen;
  const chosen = el.dataset.chosen, opts = resultSetOptions(protocol, sets, source);
  fillSelect(el, opts, "no result set yet");
  if (!opts.length) return null;
  const act = (sets || []).find(s => s.active && opts.some(o => o.value === String(s.result_set)));
  el.value = chosen && opts.some(o => o.value === chosen) ? chosen : (act ? String(act.result_set) : opts[opts.length - 1].value);
  el.dataset.protocol = protocol;
  return +el.value;
}

// The "Online lookups" card (Settings page and Setup seed-papers step): consent state, services, fields sent, a switch
// that stores the same setting as `wider lookups allow|revoke`, and the newest outside requests. `onChange` runs after
// the switch was changed.
async function renderLookupsCard(el, onChange = null) {
  const card = typeof el === "string" ? document.querySelector(el) : el;
  if (!card) return;
  let s;
  try { s = await api("/api/lookups"); }
  catch (e) { card.innerHTML = `<h2>Online lookups</h2><p class="bad">${esc(e.message)}</p>`; return; }
  const log = s.log || [];
  card.innerHTML = `<h2>Online lookups</h2>
    <p>${s.allowed ? `<span class="chip loc-cloud">on</span> allowed${s.allowed_at ? " since " + esc(fmtWhen(s.allowed_at)) : ""}${s.allowed_by ? " by " + esc(s.allowed_by) : ""}` : '<span class="chip loc-local">off</span> nothing is sent; a lookup asks first'}</p>
    <table class="summary-table"><tr><td>services</td><td>${(s.services || []).map(esc).join(", ")}</td></tr>
    <tr><td>sent for each paper</td><td>${(s.fields || []).map(esc).join(", ")}</td></tr>
    <tr><td>used to</td><td>${Object.entries(s.purposes || {}).filter(([k]) => !k.startsWith("prior_") && k !== "term_yield" && k !== "query_yield").map(([, v]) => esc(v.label)).join("; ")}</td></tr></table>
    <div class="row"><label><input type="checkbox" data-lookups-switch ${s.allowed ? "checked" : ""}> Allow online lookups for this project</label> <span class="muted small" data-lookups-msg role="status"></span></div>
    <p>${s.terms_allowed ? `<span class="chip loc-cloud">on</span> search terms allowed${s.terms_allowed_at ? " since " + esc(fmtWhen(s.terms_allowed_at)) : ""}${s.terms_allowed_by ? " by " + esc(s.terms_allowed_by) : ""}` : '<span class="chip loc-local">off</span> no search term or query is sent; a count asks first'}</p>
    <div class="row"><label><input type="checkbox" data-terms-switch ${s.terms_allowed ? "checked" : ""}> Allow search-term lookups (a count sends search terms or a query to OpenAlex; separate from the switch above)</label></div>
    <div class="row"><span>cached responses: <strong data-cache-count>${esc(s.cached_responses ?? 0)}</strong></span> <button type="button" class="secondary small-btn" data-cache-clear ${s.cached_responses ? "" : "disabled"}>Clear</button> <span class="muted small">Seed chaining and OpenAlex counts keep each answer; a repeated request is answered from here and sends nothing.</span></div>
    <p class="muted small">Prior-review lookups have their own switch (in the Prior reviews task of the Review plan), now ${s.prior_review_lookup ? "on" : "off"}; this consent does not change it.</p>
    ${log.length ? `<table class="check-table"><tr><th>time</th><th>service</th><th>purpose</th><th>papers</th><th>failed</th></tr>${log.map(r => `<tr><td>${esc(when(r.at))}</td><td>${esc(r.service_label)}</td><td>${esc(r.purpose)}</td><td>${esc(r.n_items)}</td><td>${esc(r.n_failed)}</td></tr>`).join("")}</table>` : '<p class="muted small">No outside request has been logged in this project.</p>'}
    ${cmdFold("<code>wider lookups status|allow|revoke|log</code>, <code>wider lookups terms allow|revoke</code>, <code>wider lookups cache [--clear]</code>")}`;
  const wire = (sw, allowUrl, revokeUrl) => {
    sw.onchange = async () => {
      sw.disabled = true;
      try {
        await api(sw.checked ? allowUrl : revokeUrl, { method: "POST", body: "{}" });
        await renderLookupsCard(card, onChange);
        if (onChange) onChange();
      } catch (e) {
        sw.checked = !sw.checked; sw.disabled = false;
        const m = card.querySelector("[data-lookups-msg]"); if (m) m.textContent = e.message;
      }
    };
  };
  const clr = card.querySelector("[data-cache-clear]");
  if (clr) clr.onclick = async () => {
    clr.disabled = true;
    try { await api("/api/lookups/cache", { method: "DELETE" }); await renderLookupsCard(card, onChange); }
    catch (e) { clr.disabled = false; const m = card.querySelector("[data-lookups-msg]"); if (m) m.textContent = e.message; }
  };
  wire(card.querySelector("[data-lookups-switch]"), "/api/lookups/allow", "/api/lookups/revoke");
  wire(card.querySelector("[data-terms-switch]"), "/api/lookups/terms/allow", "/api/lookups/terms/revoke");
}

// Tables drawn by the pages (inspection 1 Nov 2026, item 20): a header cell of a header row gets scope="col", a header
// cell that starts a body row scope="row", and a table with no name of its own a visually hidden caption from the
// nearest heading above it, so a screen reader names every table and its columns. Runs on every table the pages add.
const TableA11y = (() => {
  function nameFrom(t) {
    for (let el = t; el && el !== document.body; el = el.parentElement) {
      for (let p = el.previousElementSibling; p; p = p.previousElementSibling) {
        const hd = /^H[1-6]$/.test(p.tagName) ? p : (p.querySelector && p.querySelector("h1,h2,h3,h4,h5,h6"));
        if (hd && hd.textContent.trim()) return hd.textContent.trim();
      }
      const sum = el.tagName === "DETAILS" && el.querySelector("summary");
      if (sum && sum.textContent.trim()) return sum.textContent.trim();
    }
    return "";
  }
  function fix(t) {
    if (!t || !t.querySelectorAll) return;
    t.querySelectorAll("tr").forEach((tr) => {
      const inHead = tr.parentElement && tr.parentElement.tagName === "THEAD";
      const cells = [...tr.children], allTh = cells.length && cells.every(c => c.tagName === "TH");
      cells.forEach((c, i) => {
        if (c.tagName !== "TH" || c.hasAttribute("scope")) return;
        c.setAttribute("scope", inHead || allTh ? "col" : i === 0 ? "row" : "col");
      });
    });
    if (!t.querySelector("caption") && !t.hasAttribute("aria-label") && !t.hasAttribute("aria-labelledby")) {
      const name = nameFrom(t);
      if (name) { const c = document.createElement("caption"); c.className = "sr-only"; c.textContent = name; t.insertBefore(c, t.firstChild); }
    }
  }
  function scan(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.tagName === "TABLE") fix(root);
    else if (root.closest && root.closest("table")) fix(root.closest("table"));
    if (root.querySelectorAll) root.querySelectorAll("table").forEach(fix);
  }
  if (typeof MutationObserver === "function" && document.body) {
    new MutationObserver((ms) => ms.forEach(m => m.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
    scan(document.body);
  }
  return { fix, scan };
})();
