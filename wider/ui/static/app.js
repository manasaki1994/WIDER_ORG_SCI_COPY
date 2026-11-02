const $ = (s) => document.querySelector(s);
// Changes are journaled with an author: every API request carries the reviewer name this browser remembers,
// percent-encoded (a header holds Latin-1 only; the server decodes it).
(function () {
  const orig = window.fetch.bind(window);
  window.fetch = (input, init = {}) => {
    const url = typeof input === "string" ? input : input.url;
    if (url.startsWith("/api/")) {
      const who = window.WiderName ? WiderName.get() : "";   // the name this page works as (D33), never a newer one it has not reloaded for
      const headers = new Headers(init.headers || {});
      // a request that names its signer (the question window's Change) keeps it
      if (who && !headers.has("X-Wider-Author")) { headers.set("X-Wider-Author", encodeURIComponent(who)); init = { ...init, headers }; }
    }
    return orig(input, init);
  };
})();
// api(), esc() and fillSelect() come from common.js

const PAGES = { home: "page-home", overview: "page-overview", model: "page-model", help: "page-help", settings: "page-settings", questions: "page-questions", history: "page-history", notes: "page-notes", setup: "page-setup", construct: "page-construct", query: "page-query", import: "page-import", dedup: "page-dedup", enrich: "page-enrich", rank: "page-rank", sample: "page-sample", screen: "page-screen", recall: "page-recall", results: "page-results", reporting: "page-reporting", review: "page-review", methods: "page-methods" };
let methodsSub = "";   // "#methods/<topic>/<layer>": the part after "methods/", read by the Methods page (methods.js)
let currentPage = "home";
let lastHash = null;   // the address last shown: "#setup?task=seeds" back to "#setup" shows the plan again (D2)
let pagesShown = 0;   // the first page drawn leaves the focus where the browser puts it (the skip link stays first)

function showPage(key) {
  key = String(key || "").split("?")[0];   // "#review?protocol=main&set=1": the page reads its own parameters
  if (key === "ask" && window.askRoute) key = askRoute(location.hash);   // the old Ask WIDER page: its tab (shell.js)
  if (key.startsWith("methods/")) { methodsSub = key.slice(8); key = "methods"; } else if (key === "methods") methodsSub = "";
  let unknown = null;
  if (!PAGES[key]) { try { unknown = decodeURIComponent(key); } catch (e) { unknown = key; } key = "home"; history.replaceState(null, "", "#home"); }  // unknown or stale address: Home
  currentPage = key;
  lastHash = location.hash;
  if (window.Drawer && Drawer.isOpen()) Drawer.close(true);  // the drawer belongs to the page it was opened on
  document.querySelectorAll(".page").forEach(p => p.hidden = p.id !== PAGES[key]);
  const status = $("#home-status");
  if (status) status.textContent = unknown ? `page not found: ${unknown}` : "";   // textContent: the hash is never markup
  // the window title names the page (jobs.js puts "(n running) " in front); after the first page the focus moves to
  // the page heading and the page shows its top, so a screen reader hears where it is (D2)
  const h1 = document.querySelector(`#${PAGES[key]} h1`);
  window.pageTitle = h1 ? `${h1.textContent.trim()} – WIDER` : "WIDER";
  if (window.Jobs && Jobs.retitle) Jobs.retitle(); else document.title = window.pageTitle;
  if (pagesShown++ && h1) { h1.tabIndex = -1; h1.focus({ preventScroll: true }); window.scrollTo(0, 0); }
  if (window.markCurrentPage) markCurrentPage(key);  // shell.js: sidebar highlight, top bar
  showLoadError("page", null);
  const load = pageLoader(key);
  if (load) Promise.resolve().then(load).catch(e => pageLoadFailed(key, e));
  renderStageResults();
  if (window.loadRunSelector) loadRunSelector(key);
  if (window.QWindow) QWindow.refresh(true);   // the questions about the page now in view (D7)
}
// Tabs inside a page (.page-tabs): only the chosen part of the page is shown; the choice is kept for this browser tab.
function initPageTabs(bar) {
  const tabs = [...bar.querySelectorAll('[role="tab"]')], store = "wider.tab." + bar.closest(".page").id;
  const pick = (key, focus) => {
    const t = tabs.find(b => b.dataset.tab === key) || tabs[0];
    for (const b of tabs) {
      const on = b === t; b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1;
      $("#" + b.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) t.focus();
    try { sessionStorage.setItem(store, t.dataset.tab); } catch (e) {}
  };
  bar.addEventListener("click", e => { const b = e.target.closest('[role="tab"]'); if (b) pick(b.dataset.tab); });
  bar.addEventListener("keydown", e => {
    const i = tabs.indexOf(document.activeElement); if (i < 0) return;
    const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key]; if (j == null) return;
    e.preventDefault(); pick(tabs[(j + tabs.length) % tabs.length].dataset.tab, true);
  });
  let kept = null; try { kept = sessionStorage.getItem(store); } catch (e) {}
  pick(kept);
  bar.pick = pick;
}
document.querySelectorAll(".page-tabs").forEach(initPageTabs);
function pageLoader(key) {
  return { home: window.loadHome, overview: window.loadOverview, model: window.loadModel, help: window.loadHelp, settings: window.loadSettings, questions: window.loadQuestions,
    history: window.loadHistory, notes: window.loadNotesSearch, construct: loadConstruct, query: loadQuery, import: loadImport, dedup: (...a) => window.DedupView && DedupView.load(...a),
    enrich: loadEnrich, rank: loadRank, sample: loadSamples, screen: loadScreen, recall: loadRecall, results: window.loadResults, reporting: window.loadReporting, review: window.loadScreeningReview, setup: loadProject,
    methods: () => window.Methods && Methods.load(methodsSub) }[key];
}
// A page whose data cannot be loaded says so: in its stage-result box, or in the banner above the pages when it
// has none. The next successful open clears it (renderStageResults redraws the box; showPage hides the banner).
function pageLoadFailed(key, e) {
  if (currentPage !== key) return;
  if (document.querySelector(`.stage-result[data-result="${key}"]`)) showStageError(key, `Could not load this page: ${e.message}`);
  else showLoadError("page", `Could not load this page: ${e.message}`);
}
// The banner above the pages (#load-error); scope "boot" is the stage list at start-up, "page" a page without a stage.
function showLoadError(scope, msg) {
  const b = $("#load-error"); if (!b) return;
  if (msg == null) { if (b.dataset.scope === scope) { b.hidden = true; b.textContent = ""; } return; }
  b.dataset.scope = scope; b.textContent = msg; b.hidden = false;
}

let STAGES = [], NEXT_KEY = null, LAST_RESULTS = {}, SECTIONS = [], PROJECT_TITLE = "", PROJECT_KEY = null, CONSTRUCT_BACKEND = null,
  CONSTRUCT_DEFAULT = null;

async function loadStages() {
  const d = await api("/api/stages");
  STAGES = d.stages; NEXT_KEY = d.next; LAST_RESULTS = d.last_results || {};
  SECTIONS = d.sections || []; PROJECT_TITLE = d.title || ""; PROJECT_KEY = d.project_key || null;
  CONSTRUCT_BACKEND = d.construct_backend || null; CONSTRUCT_DEFAULT = d.construct_default || null;
  showLoadError("boot", null);
  if (window.renderShell) renderShell();  // shell.js: sidebar links with their marks, top bar
  renderStageWhy(); renderStageResults();
}

// Title row: a status chip (word + colour, never colour alone); the explanation opens on demand (Explain this step).
function renderStageWhy() {
  document.querySelectorAll(".state-chip[data-chip]").forEach(el => {
    const s = STAGES.find(x => x.key === el.dataset.chip); if (!s) return;
    // the same words as the sidebar (shell.js stageStatus, screening-page-tasks D8)
    const st = window.stageStatus ? stageStatus(s) : { cls: s.state, text: s.state };
    el.className = `state-chip state-${st.cls}`;
    el.textContent = st.text;
  });
  document.querySelectorAll(".stage-why[data-why]").forEach(el => {
    const s = STAGES.find(x => x.key === el.dataset.why); if (!s) return;
    el.innerHTML = `<p><b>What happens.</b> ${esc(oneLine(s.description))}</p><p><b>Why now.</b> ${esc(oneLine(s.why))}</p>
      ${cmdFold(`For scripts: ${(s.commands || [s.command]).map(c => `<code>wider ${esc(c)}</code>`).join(", ")}${(s.produces || []).length ? ` · produces ${s.produces.map(x => `<code>${esc(x)}</code>`).join(", ")}` : ""}`)}`;
  });
}
const oneLine = (s) => String(s || "").split(/\s+/).join(" ").trim();

// ---------------- stage results (shared with the CLI via meta last_result:<stage>) ----------------
const stageTitle = (key) => { const s = STAGES.find(x => x.key === key); return s ? s.title : key; };
const fmtNum = (v) => typeof v === "number" ? (Number.isInteger(v) ? v.toLocaleString() : (Math.abs(v) < 10 ? v.toFixed(3) : v.toFixed(1))) : String(v ?? "–");
// a count of one names its thing in the singular: "1 protocol", "1 file imported", "1 unique record"
const NOT_PLURAL = new Set(["status", "analysis", "bias", "is", "was", "has", "its", "yes", "less", "across"]);
const metricLabel = (k, v) => {
  const words = k.replace(/_/g, " ").split(" ");
  return (v === 1 ? words.map(w => w.length > 3 && w.endsWith("s") && !w.endsWith("ss") && !NOT_PLURAL.has(w.toLowerCase())
    ? (w.endsWith("ies") ? w.slice(0, -3) + "y" : w.slice(0, -1)) : w) : words).join(" ");
};
function metricTiles(metrics) {
  const entries = Object.entries(metrics || {}).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object");
  if (!entries.length) return "";
  return `<div class="metrics">${entries.map(([k, v]) => `<div class="metric"><div class="v">${esc(fmtNum(v))}</div><div class="k">${esc(metricLabel(k, v))}</div></div>`).join("")}</div>`;
}
// "Details" holds the run's counts (metric tiles: no decision rests on them, UX review F13) and its rows
function rowsTable(rows, tiles) {
  if (!rows || !rows.length) return tiles ? `<details><summary>Details</summary>${tiles}</details>` : "";
  const cols = Object.keys(rows[0]);
  return `<details><summary>Details (${rows.length} row${rows.length === 1 ? "" : "s"})</summary>${tiles || ""}<table><thead><tr>${cols.map(c => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(typeof r[c] === "object" ? JSON.stringify(r[c]) : fmtNum(r[c]))}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
}
// the card of one stage result; opts.chosen marks an earlier result (with "Back to the latest result"). The page's one
// Continue is the link at its end (ui-redesign D20). The Deduplication page shows it under "Details" and in notebook entries.
function resultCardHtml(res, opts) {
  const o = opts || {}, chosen = o.chosen;
  return `<div class="result-card tone-${esc(res.tone || "success")}">
    <p class="headline">${esc(res.headline)}</p>
    <p class="when">${chosen ? "<b>Earlier result</b> · " : ""}${res.action ? `${esc(res.action)} · ` : ""}${esc(fmtWhen(res.ran_at))}${res.usage ? ` · ${usageChip(res.usage)} ${esc(usageText(res.usage))}${res.usage.backends ? ` · ${esc(res.usage.backends.join(", "))}` : ""}` : ""}</p>
    ${(res.warnings || []).length ? `<ul class="warnings">${res.warnings.map(w => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    ${rowsTable(res.rows, metricTiles(res.metrics))}
    ${(res.outputs || []).length ? `<div class="outputs">Wrote: ${res.outputs.map(o => `<code>${esc(o)}</code>`).join(", ")}</div>` : ""}
    ${chosen ? `<div class="actions"><span class="spacer"></span><button class="secondary" data-latest>Back to the latest result</button></div>` : ""}
  </div>`;
}
// A result card shows only for an action taken in this visit (or one the researcher asks to see): an older result is
// one quiet line with "Show", and the run selector lists the earlier ones (ui-redesign D32, UX review F13).
const SHOWN_THIS_VISIT = new Set();
function renderStageResult(key, chosen) {
  const box = document.querySelector(`.stage-result[data-result="${key}"]`); if (!box) return;
  const res = chosen || LAST_RESULTS[key];
  if (!res) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  if (!chosen && !SHOWN_THIS_VISIT.has(key)) {
    box.innerHTML = `<p class="small last-run">Last run ${esc(fmtWhen(res.ran_at))}. <button type="button" class="link" data-show-last>Show the result</button></p>`;
    box.querySelector("[data-show-last]").onclick = () => { SHOWN_THIS_VISIT.add(key); renderStageResult(key); };
    return;
  }
  box.innerHTML = resultCardHtml(res, { chosen });
  const l = box.querySelector("[data-latest]");
  if (l) l.onclick = () => { const sel = document.querySelector(`.run-select[data-runs="${key}"]`); if (sel) sel.value = ""; renderStageResult(key); };
}
// a headline short enough for a menu option, cut at a word with "…" so a reason after a colon is never cut to nothing
function shortHeadline(s, n = 110) {
  s = String(s || "");
  if (s.length <= n) return s;
  const cut = s.lastIndexOf(" ", n);
  return s.slice(0, cut > n / 2 ? cut : n).replace(/[\s:,;]+$/, "") + "…";
}
// run selector in the title row: every earlier result of the stage (stage_results table), newest first
async function loadRunSelector(key) {
  const sel = document.querySelector(`.run-select[data-runs="${key}"]`); if (!sel) return;
  let rows = [];
  try { rows = await api(`/api/stages/${encodeURIComponent(key)}/results?limit=30`); } catch (e) { rows = []; }
  if (rows.length < 2) { sel.hidden = true; sel.innerHTML = ""; return; }
  sel.hidden = false;
  sel.innerHTML = `<option value="">Latest result</option>` + rows.slice(1).map(r =>
    `<option value="${r.result_id}" title="${esc(r.headline || "")}">${esc(fmtWhen(r.ran_at))} · ${esc(r.action)} · ${esc(shortHeadline(r.headline))}</option>`).join("");
  sel.onchange = () => { const r = rows.find(x => String(x.result_id) === sel.value); renderStageResult(key, r || null); };
}
function renderPrevResult(key) {
  const box = document.querySelector(`.stage-result.prev[data-prev="${key}"]`); if (!box) return;
  const idx = STAGES.findIndex(s => s.key === key);
  // the nearest earlier stage's result, only when it was produced in this visit (F13)
  const prev = STAGES.slice(0, Math.max(idx, 0)).reverse().find(s => LAST_RESULTS[s.key]);
  if (!prev || !SHOWN_THIS_VISIT.has(prev.key)) { box.hidden = true; box.innerHTML = ""; return; }
  const r = LAST_RESULTS[prev.key];
  box.hidden = false;
  box.innerHTML = `<div class="prev-line tone-${esc(r.tone || "success")}"><span class="dot"></span><b>${esc(prev.title)}</b> ${esc(r.headline)} <span class="muted">(${esc(fmtWhen(r.ran_at))})</span> <a href="#${esc(prev.key)}" data-goto="${esc(prev.key)}">open</a></div>`;
  box.querySelector("[data-goto]").onclick = (e) => { e.preventDefault(); location.hash = prev.key; showPage(prev.key); };
}
function renderStageResults() {
  document.querySelectorAll(".stage-result[data-result]").forEach(el => renderStageResult(el.dataset.result));
  document.querySelectorAll(".stage-result.prev[data-prev]").forEach(el => renderPrevResult(el.dataset.prev));
}
// update immediately from a run response (the 400 ms refreshStages in api() would otherwise race the render)
function showStageResult(res) {
  if (!res || !res.stage) return;
  LAST_RESULTS[res.stage] = res; SHOWN_THIS_VISIT.add(res.stage);
  renderStageResult(res.stage);
  document.querySelectorAll(".stage-result.prev[data-prev]").forEach(el => renderPrevResult(el.dataset.prev));
}
function showStageError(key, msg) {
  const box = document.querySelector(`.stage-result[data-result="${key}"]`); if (!box) return;
  box.hidden = false;
  box.innerHTML = `<div class="stage-error" role="alert">${esc(msg)}</div>`;   // read out by screen readers
}
// the sidebar and stage marks only (also the 400 ms refresh after any write in api(), and while the setup is typed);
// a function declaration, so window.refreshStages exists for the other scripts (a top-level const would not be on window)
function refreshStages() { return loadStages().catch(() => {}); }
// call after an action: the sidebar (states, project title), the result cards and the current page's cards, counts and
// selects redraw from the server, and the run selector refills; no reload needed
async function afterAction() {
  await refreshStages();
  const key = currentPage, load = pageLoader(key);
  if (load) await Promise.resolve().then(load).catch(e => pageLoadFailed(key, e));
  await loadRunSelector(key);
}
// LLM backend select: every provider; unless the researcher already picked one, the backend set for `stage` (the model
// page sets the construct one) is chosen, else the project's default.
function providerSelect(sel, cfg, button, stage) {
  const el = $(sel), had = el.value, provs = cfg.providers || [];
  const any = fillSelect(el, provs.map(p => ({ value: p.name, label: `${p.name} (${p.model})` })), "no AI model yet", button);
  const pref = [stage && (cfg.stage_providers || {})[stage], cfg.default_provider].find(n => n && provs.some(p => p.name === n));
  if (any && !had && pref) el.value = pref;
}

// ---------------- construct page ----------------
const KINDS = ["cells", "codebook", "synonyms", "anchors", "near_miss"];
let artKind = "cells", artVersions = {}, artCurrent = null;
let rkEmbeddings = [];   // the project's embedding models (Explorer), for the cost check before "Embed corpus"
let rkEmbedding = null;  // the chosen card of the embedding picker (the project's default embedding): Embed corpus and Rank use it
// The cost range, its assumptions and the destination before Start (POST /api/estimate computes it; nothing is sent).
async function showEstimate(sel, body) {
  const box = $(sel); if (!box) return;
  try { box.innerHTML = Backends.estimateText(await api("/api/estimate", { method: "POST", body: JSON.stringify(body) })); }
  catch (e) { box.innerHTML = `<span class="muted">${esc("No cost estimate: " + e.message)}</span>`; }
}
function constructEstimate() {
  const prov = $("#art-provider").value;
  if (!prov) { $("#art-estimate").textContent = ""; return; }
  showEstimate("#art-estimate", { stage: "construct", kind: artKind, provider: prov });
}

// the artifact tabs are a tab list (C6 D6): one tab stop, Left/Right move to the next tab and open it
function drawArtTabs() {
  const box = $("#art-tabs"), had = box.contains(document.activeElement);
  box.innerHTML = KINDS.map(k => {
    const vs = artVersions[k] || [], last = vs[vs.length - 1], on = k === artKind;
    return `<button type="button" role="tab" id="art-tab-${k}" data-kind="${k}" aria-selected="${on}" aria-controls="art-panel" tabindex="${on ? 0 : -1}" class="${on ? "active" : ""}">${k}${last ? ` <span class="muted">v${esc(last.version)} ${esc(last.status)}</span>` : ""}</button>`;
  }).join("");
  $("#art-panel").setAttribute("aria-labelledby", `art-tab-${artKind}`);
  const open = async (k) => { artKind = k; await loadConstruct(); };
  box.querySelectorAll("button").forEach(b => b.onclick = () => open(b.dataset.kind));
  box.onkeydown = (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const k = KINDS[(KINDS.indexOf(artKind) + (e.key === "ArrowRight" ? 1 : KINDS.length - 1)) % KINDS.length];
    artKind = k; drawArtTabs(); $(`#art-tab-${k}`).focus();   // at once, so the key is felt; the panel follows
    open(k);
  };
  if (had) { const t = $(`#art-tab-${artKind}`); if (t) t.focus(); }   // a redraw keeps the focus on the open tab
}

async function loadConstruct() {
  const bm = await api("/api/benchmark");
  $("#bm-table").innerHTML = `<tr><th>id</th><th>set</th><th>year</th><th>abstract</th><th>title</th></tr>` +
    bm.map(r => `<tr><td>${esc(r.benchmark_id)}</td><td>${esc(r.set_name.slice(0, 3))}</td><td>${esc(r.year || "")}</td>
      <td>${r.abstract_words ? `${esc(r.abstract_words)} w <span class="muted">(${esc(r.abstract_source || "csv")})</span>` : '<span class="bad">missing</span>'}</td>
      <td>${esc(r.title)}</td></tr>`).join("") || "<tr><td class='muted'>benchmark.csv is empty</td></tr>";
  const proj = await api("/api/project");
  providerSelect("#art-provider", proj.config, "#art-propose", "construct");
  artVersions = await api("/api/construct");
  drawArtTabs();
  $("#art-provider").onchange = constructEstimate; constructEstimate();
  const vs = artVersions[artKind] || [];
  $("#art-version").innerHTML = vs.map(v => `<option value="${esc(v.version)}">v${esc(v.version)} · ${esc(v.status)} · ${esc(v.origin)}${v.parent_version ? ` · from v${esc(v.parent_version)}` : ""} · sha256 ${esc(String(v.sha256 || "").slice(0, 12))}</option>`).reverse().join("")
    || `<option value="">no versions yet</option>`;
  $("#art-version").onchange = () => loadArtifact(+$("#art-version").value);
  if (window.SeedNames) SeedNames.load();   // seeds.js: the "Names from seed papers" card
  // awaited, so a message the caller writes after loadConstruct (a freeze or save notice) is not overwritten
  if (vs.length) await loadArtifact(vs[vs.length - 1].version);
  else {   // no version yet: Show as text starts from the outline of the fields the format needs
    artCurrent = null; $("#art-items").innerHTML = ""; $("#art-text-wrap").open = true; $("#art-diff").textContent = "";
    let outline = "";
    try { outline = (await api(`/api/construct/${artKind}/outline`)).text; } catch (e) { /* an empty box still works */ }
    $("#art-text").value = outline;
    $("#art-status").textContent = "No version yet. Ask the model for one, or fill in the fields under Show as text and save.";
  }
}

// the current version's items as a short read-only list above "Show as text" (ui-redesign D32 F7): the synonyms
// block by block with their terms; any other artifact its lists by name, at most 12 items each
function artItemsHtml(kind, data) {
  if (!data || typeof data !== "object") return "";
  const label = (x) => x && typeof x === "object" ? (x.name || x.label || x.key || x.id || x.text || "") : String(x ?? "");
  const few = (xs) => xs.slice(0, 12).map(label).filter(Boolean).join(", ") + (xs.length > 12 ? `, and ${xs.length - 12} more` : "");
  let rows;
  if (kind === "synonyms" && Array.isArray(data.blocks)) rows = data.blocks.map(b => `<li><b>${esc(b.name || b.key)}</b>: ${esc(few(b.terms || []))}</li>`);
  else rows = Object.entries(data).filter(([, v]) => (Array.isArray(v) && v.length) || typeof v === "string")
    .map(([k, v]) => Array.isArray(v) ? `<li><b>${esc(k)}</b> (${v.length}): ${esc(few(v))}</li>`
      : `<li><b>${esc(k)}</b>: ${esc(v.length > 200 ? v.slice(0, 200) + "…" : v)}</li>`);
  return rows.length ? `<h2 class="h-sub">In this version</h2><ul class="art-items">${rows.join("")}</ul>` : "";
}

async function loadArtifact(version) {
  const d = await api(`/api/construct/${artKind}/${version}`);
  artCurrent = d.row;
  artRefusal("");
  $("#art-text").value = d.text;
  $("#art-items").innerHTML = artItemsHtml(artKind, d.data);
  $("#art-status").className = "muted small";   // a freeze warning (refine.js) turned it red; a new load is plain again
  $("#art-status").textContent = `${artKind} v${d.row.version} · ${d.row.status} · ${d.row.origin}${d.row.parent_version ? ` · from v${d.row.parent_version}` : ""} · sha256 ${String(d.row.sha256 || "").slice(0, 12)} · ${fmtWhen(d.row.created_at)} · ${d.row.note || ""}`;
  $("#art-approve").disabled = d.row.status !== "draft";
  $("#art-freeze").disabled = d.row.status === "frozen";
  if (window.Refine) await Refine.afterLoad(artKind, d.row);   // refine.js: Freeze on a frozen synonyms version, Unlock
  const newest = Math.max(...(artVersions[artKind] || []).map(v => v.version));
  $("#art-restore").disabled = d.row.version === newest;   // only an older version can come back as a new draft
  if (d.row.parent_version) {
    const df = await api(`/api/construct/${artKind}/diff/${d.row.parent_version}/${d.row.version}`);
    $("#art-diff").textContent = df.diff || "(identical)";
  } else $("#art-diff").textContent = "(first version)";
}

$("#bm-fetch").onclick = async () => {
  $("#bm-msg").textContent = "fetching…"; $("#bm-fetch").disabled = true;
  try { const r = await api("/api/benchmark/fetch", { method: "POST" }); $("#bm-msg").textContent = `${r.with_abstract}/${r.rows.length} with abstract`; await loadConstruct(); }
  catch (e) { $("#bm-msg").textContent = e.message; }
  $("#bm-fetch").disabled = false;
};
$("#art-propose").onclick = async () => {
  const prov = $("#art-provider").value, kind = artKind;
  if (!prov) return;
  try {
    const go = await confirmSave(kind, `Propose a new ${kind} version?`, "Propose");
    if (!go) return;
    const seen = typeof go === "string" ? `&seen_choice=${go}` : "";
    await Jobs.start(`/api/jobs/propose/${kind}?provider=${encodeURIComponent(prov)}${seen}`, {}, async (j) => {
      if (j.status !== "done") { await proposalFailed(kind, j.error || j.status); return; }
      artKind = kind; await loadConstruct(); $("#art-status").textContent += ` · proposed v${j.result.version}`;
    });
    $("#art-status").textContent = `asking ${prov} in the background (minutes with a local model); see the bar at the top.`;
  } catch (e) { $("#art-status").textContent = e.message; }
};
$("#art-prompt").onclick = async () => {
  try { const r = await api(`/api/construct/${artKind}/prompt`); $("#art-prompt-text").textContent = r.prompt; $("#art-prompt-wrap").open = true; }
  catch (e) { $("#art-prompt-text").textContent = e.message; $("#art-prompt-wrap").open = true; }
};
$("#art-save").onclick = async () => {
  const btn = $("#art-save"); if (btn.disabled) return;
  btn.disabled = true;  // a double click saves one version
  try {
    const go = await confirmSave(artKind, `Save a new ${artKind} version?`, "Save");
    if (!go) return;
    const body = { text: $("#art-text").value };
    if (typeof go === "string") body.seen_choice = go;
    const r = await api(`/api/construct/${artKind}/save`, { method: "POST", body: JSON.stringify(body) }); await loadConstruct(); $("#art-status").textContent += ` · saved v${r.version}`;
    if (r.notice) $("#art-status").textContent += ` · ${r.notice}`;
  }
  catch (e) { artRefusal("Not saved: " + e.message); }
  finally { btn.disabled = false; }
};
$("#art-approve").onclick = async () => { if (!artCurrent) return; try { await api(`/api/construct/${artKind}/${artCurrent.version}/status/approved`, { method: "POST" }); await loadConstruct(); } catch (e) { artRefusal("Not approved: " + e.message); } };
// a failed proposal: a plain alert, and a route to write the artifact by hand. An empty Show as text gets the
// outline of the fields the format needs (GET /api/construct/{kind}/outline, `wider construct outline`) and opens.
async function proposalFailed(kind, error) {
  artRefusal(`No ${kind} was proposed: ${error}. You can write it yourself: Show as text below holds the fields it needs; fill them in and press Save.`);
  if (artKind !== kind || $("#art-text").value.trim()) { $("#art-text-wrap").open = true; return; }
  try { $("#art-text").value = (await api(`/api/construct/${kind}/outline`)).text; } catch (e) { /* the alert above still stands */ }
  $("#art-text-wrap").open = true;
}
// a refused save or approval: one alert in the error style, not a muted status line (accessibility-and-naming 4.5)
function artRefusal(text) { const r = $("#art-refusal"); r.textContent = text; r.hidden = !text; }
// #art-freeze and #art-unlock: refine.js (the confirm step with the frozen-query notice and the researcher's choice)
const confirmSave = (kind, title, label) => (window.Refine ? Refine.confirmSave(kind, title, label) : Promise.resolve(true));

async function loadProject() {
  const d = await api("/api/project");
  const c = d.config;
  $("#project-name").textContent = c.name + (c.title ? " — " + c.title : "");
  $("#project-root").textContent = d.root;
  $("#title").value = c.title || "";
  $("#questions").value = (c.questions || []).join("\n");
  $("#include_notes").value = c.include_notes || "";
  $("#exclude_notes").value = c.exclude_notes || "";
  if (window.renderSetupCards) await renderSetupCards(d);  // setup.js: the task cards and the shown task page
  let proto = null;
  try { proto = await api("/api/protocol"); } catch (e) { proto = null; }
  renderSummary(d, proto);
}

// setup.js dispatches wider:changed after every write; refresh the task cards, the folded summary and the nav without
// re-rendering the task page.
let planTimer = null, planSeq = 0;
async function refreshSetupPlan() {
  const n = ++planSeq;
  if (window.refreshPlan) refreshPlan().catch(() => {});
  const d = await api("/api/project");
  let proto = null;
  try { proto = await api("/api/protocol"); } catch (e) { proto = null; }
  if (n === planSeq) renderSummary(d, proto);   // an older reply never draws over a newer one
}
window.addEventListener("wider:changed", () => {
  clearTimeout(planTimer);
  planTimer = setTimeout(() => { refreshSetupPlan().catch(() => {}); if (window.refreshStages) refreshStages(); }, 300);
});

// The platform whose role is development; the first in search order only when no platform has that role
// (as search/catalogue.py:project_platforms decides).
const devPlatform = (plats) => plats.find(p => p.role === "development") || plats[0];

function renderSummary(d, proto) {
  const box = $("#setup-summary"); if (!box) return;
  const c = d.config, bm = d.benchmark;
  const row = (k, v) => `<tr><th scope="row" class="kv">${esc(k)}</th><td>${v}</td></tr>`;
  const dev = devPlatform(c.platforms || []);
  const platforms = (c.platforms || []).map(p => `${esc(p.key)}${p === dev ? " (development)" : ""}`).join(", ") || "—";
  const stageProv = Object.entries(c.stage_providers || {}).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(", ");
  const store = Object.keys(d.store || {}).length ? Object.entries(d.store).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ") : "empty";
  box.innerHTML = `<table class="summary-table" aria-label="Project summary">
    ${row("title", esc(c.title || c.name))}
    ${row("questions", (c.questions || []).length ? c.questions.map(esc).join("<br>") : "—")}
    ${row("include / exclude notes", `${c.include_notes ? "yes" : "none"} / ${c.exclude_notes ? "yes" : "none"}`)}
    ${row("seed papers", `${bm.n} (${bm.development} development, ${bm.validation} validation${bm.used ? `, ${bm.used} used validation` : ""}${bm.not_counted ? `; ${bm.not_counted} ${NOT_COUNTED}` : ""})`)}
    ${row("prior reviews", d.prior_reviews ? `${d.prior_reviews.n_reviews} (${d.prior_reviews.n_papers} papers)${d.prior_reviews.policy.none_known ? ", none known" : ""} · OpenAlex lookup ${d.prior_reviews.policy.allow_openalex_lookup ? "on" : "off"} · LLM excerpts ${d.prior_reviews.policy.allow_llm_excerpts ? "on" : "off"}` : "—")}
    ${row("databases", platforms)}
    ${row("AI model", esc(c.default_provider || "—") + (stageProv ? ` <span class="muted small">per stage: ${stageProv}</span>` : ""))}
    ${row("embedding model", esc(c.default_embedding || "—"))}
    ${row("review protocol", proto ? `${proto.changed ? `${proto.changed} changed` : "defaults"} · hash <code>${esc(proto.hash)}</code>` : "—")}
    ${row("random seed", esc(String(c.seed)))}
    ${row("store", store)}
  </table>
  <p class="muted small">Stored in the project folder.</p>
  ${cmdFold("<code>wider status</code> prints the same picture.")}`;
}

$("#save").onclick = async () => {
  const body = {
    title: $("#title").value,
    questions: $("#questions").value.split("\n").map(s => s.trim()).filter(Boolean),
    include_notes: $("#include_notes").value,
    exclude_notes: $("#exclude_notes").value,
  };  // platforms are edited in the Databases card (setup.js)
  try { await api("/api/project", { method: "PATCH", body: JSON.stringify(body) }); $("#save-msg").textContent = "saved"; await loadProject(); }
  catch (e) { fieldError(null, $("#save-msg"), e.message); }
};

// the skip link moves focus to the content and keeps the page (its #main is not a page address)
document.querySelector(".skip-link")?.addEventListener("click", (ev) => { ev.preventDefault(); $("#main").focus(); });
window.addEventListener("hashchange", () => { const k = (location.hash.replace("#", "") || "home").split("?")[0]; if (k !== currentPage || location.hash !== lastHash || /^methods(\/|$)/.test(k)) showPage(k); });   // a new address on the same page (#setup?task=seeds back to #setup, #methods/<topic> to another topic) shows it again
// "Continue labelling" opens Samples and labelling at its labelling queue, with the focus on Open queue (not the page
// top, ui-tester finding 29 Oct); the router shows the page on hashchange first, as its listener (above) was added first
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("[data-goto-queue]"); if (!a) return;
  e.preventDefault();
  const go = () => { const c = $("#lb-card"); if (c) c.scrollIntoView({ block: "start" }); const b = $("#lb-load"); if (b) b.focus({ preventScroll: true }); };
  if (currentPage === "sample") go(); else { window.addEventListener("hashchange", go, { once: true }); location.hash = "sample"; }
});
// a link to one card of the Samples and labelling page: open the page, then scroll the card into view and focus its
// first button (a refused recall estimate links its fixes this way)
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("[data-goto-sample]"); if (!a) return;
  e.preventDefault();
  const sel = a.dataset.gotoSample;
  const go = () => setTimeout(() => {   // the cards render after the page's data arrives
    const c = document.querySelector(sel) || $("#sm-samples"); if (!c) return;
    c.scrollIntoView({ block: "start" }); const b = c.matches("button, a") ? c : c.querySelector("button, select, input"); if (b) b.focus({ preventScroll: true });
  }, 300);
  if (currentPage === "sample") go(); else { window.addEventListener("hashchange", go, { once: true }); location.hash = "sample"; }
});
// a failed stage list leaves a banner; Home and the sidebar still open
// an explicit #page wins; a bare address (after Open, or `wider ui`) opens the project's Overview (ui-redesign D7)
// it starts once every script has run: on a cold load the stage list could arrive before jobs.js (usageChip) did
function boot() {
loadStages().catch(e => showLoadError("boot", `Could not load the stage list: ${e.message}`)).then(() => {
  let key = location.hash.replace("#", "");
  if (!key) { key = window.startPage ? startPage() : "home"; history.replaceState(null, "", "#" + key); }
  showPage(key);
});
}
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();


// ---------------- query page ----------------
async function loadQuery() {
  if (globalThis.DryRun) globalThis.DryRun.load();
  if (globalThis.Rounds) globalThis.Rounds.load();
  const plats = await api("/api/query/platforms");
  const dev = devPlatform((await api("/api/platforms")).chosen || []);  // role resolved as in project_platforms
  $("#q-dev").innerHTML = dev ? `Development platform: <b>${esc(dev.label || dev.key)}</b>. The query is built and checked there; the other platforms run the frozen query.` : "No database chosen yet (Setup, step 4).";
  try {
    // the cards show the indented rendering; the count and Copy both use the compact string that is pasted
    const d = await api("/api/query/render");
    if (!d.row) { $("#q-status").textContent = d.message; $("#q-strings").innerHTML = ""; $("#q-blocks").innerHTML = ""; return; }
    const compact = await api("/api/query/render?compact=true");
    $("#q-status").textContent = `synonyms v${d.row.version} · ${d.row.status} · terms per block: ` +
      Object.entries(d.counts).map(([k, v]) => `${k} ${v}`).join(", ");
    const showLint = (lint) => {
      $("#q-lint-wrap").querySelector("summary").textContent = `Syntax check: ${lint.length} issue${lint.length === 1 ? "" : "s"}`;
      $("#q-lint").innerHTML = (lint.length ? "<thead><tr><th>level</th><th>block</th><th>message</th><th>term</th></tr></thead>" : "") + lint.map(i => `<tr><td class="${i.level === "error" ? "bad" : ""}">${esc(i.level)}</td><td>${esc(i.block)}</td><td>${esc(i.message)}</td><td class="small">${esc(i.term)}</td></tr>`).join("");
    };
    // the linter with one platform's limits (D0b), chosen on the panel; the twin of `wider query lint --platform P`
    const sel = $("#q-lint-platform"), was = sel ? sel.value : "";
    if (sel) {
      sel.innerHTML = `<option value="">strictest of all platforms</option>` +
        Object.keys(d.strings).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join("");
      sel.value = Object.keys(d.strings).includes(was) ? was : "";
      sel.onchange = async () => showLint((await api(`/api/query/render?platform=${encodeURIComponent(sel.value)}`)).lint);
    }
    showLint(sel && sel.value ? (await api(`/api/query/render?platform=${encodeURIComponent(sel.value)}`)).lint : d.lint);
    const gsNotes = d.notes || {};   // Google Scholar: the blocks the reduced string leaves out
    // each block's name, fields and terms: the same lines as `wider query render --blocks`
    $("#q-blocks").innerHTML = (d.blocks || []).map(b => `<li data-block="${esc(b.key)}">${esc(b.text)}</li>`).join("");
    if (globalThis.TermYield) globalThis.TermYield.render(d);   // D16: each term's count on OpenAlex
    // the translation ledger (D13): what the platform cannot say or searches more widely; 2a's field-code notes are rows
    const ledgers = d.ledgers || {};
    const ledgerHtml = (k) => {
      const rows = ledgers[k] || [], clean = !rows.some(r => r.kind === "lost" || r.kind === "wider");
      return `<ul data-ledger="${esc(k)}" aria-label="Translation notes">${clean ? "<li>nothing lost</li>" : ""}${
        rows.map(r => `<li data-kind="${esc(r.kind)}" data-reason="${esc(r.reason)}"><b>${esc(r.kind)}</b>: ${esc(r.text)}</li>`).join("")}</ul>`;
    };
    $("#q-strings").innerHTML = Object.entries(d.strings).map(([k, s]) => `
      <div class="card">
        <h2>${esc((plats[k] || {}).label || k)}${dev && dev.key === k ? ' <span class="badge">development</span>' : ""} <button class="secondary small-btn" data-copy="${esc(k)}">Copy</button> <span class="muted small">${(compact.strings[k] ?? s).length} characters when pasted</span></h2>
        <p class="muted small">${esc((plats[k] || {}).paste)}</p>
        <p class="small" data-gs-note="${esc(k)}"${gsNotes[k] ? "" : " hidden"}>${esc(gsNotes[k] || "")}</p>
        <pre id="qs-${esc(k)}" class="small">${esc(s)}</pre>
        ${ledgerHtml(k)}
        <div data-limits="${esc(k)}"></div>
      </div>`).join("");
    if (typeof loadLimits === "function") loadLimits();   // absent when a test runs loadQuery alone
    if (typeof loadReview === "function") loadReview(Object.keys(d.strings));
    document.querySelectorAll("[data-copy]").forEach(b => b.onclick = async () => {
      await navigator.clipboard.writeText(compact.strings[b.dataset.copy] ?? d.strings[b.dataset.copy]); b.textContent = "Copied"; setTimeout(() => b.textContent = "Copy", 1500);
    });
  } catch (e) {
    $("#q-status").textContent = e.message; $("#q-strings").innerHTML = "";
  }
}
// What a platform without findings says (twin of review.empty_text): a failed call is not a reader that found nothing.
function reviewEmpty(p) {
  const failed = p.failed || 0, n = p.n_calls || 0;
  if (failed && failed >= n) return "no answer: every call failed, so this is not a review";
  if (failed) return `no findings from the reader that answered; ${failed} call(s) failed`;
  return "no findings";
}
// The second reader (2c D15): the readers and where each runs before a run, then the findings, agreed and unsure.
// Twin of `wider query review [--platform P] [--reader NAME]...`; the empty choices are the review stage's readers.
function reviewFindingsHtml(r) {
  if (!r) return `<p class="muted small">No review yet.</p>`;
  const readers = (r.readers || []).map(x => `${esc(x.name)} (${esc(x.model || "no model")}, family ${esc(x.family)}, ${esc(x.locality || "unknown")})`).join("; ");
  const plat = Object.entries(r.platforms || {}).map(([k, p]) => `<h3>${esc(k)}</h3>
    <ul class="small" data-review-platform="${esc(k)}">${(p.findings || []).length ? "" : `<li data-review-empty>${esc(reviewEmpty(p))}</li>`}${(p.findings || []).map(f => `<li data-agreement="${esc(f.agreement)}"><span class="chip${f.agreement === "agreed" ? " ok" : ""}">${f.agreement === "agreed" ? "agreed" : "unsure"}</span> <b>${esc(f.item)}</b> (${esc(f.severity)}, ${esc((f.readers || []).join(", "))}): <q>${esc(f.quote)}</q> ${esc(f.note)}</li>`).join("")}${p.dropped ? `<li class="muted" data-dropped>${p.dropped} dropped: quote not found</li>` : ""}${p.failed && (p.findings || []).length ? `<li class="muted" data-failed>${p.failed} call(s) failed: the findings come from the reader(s) that answered</li>` : ""}</ul>`).join("");
  return `<p class="small" data-review-message>${esc(r.message)} · ${esc(r.at || "")}</p><p class="small muted">Readers: ${readers}. ${esc(r.note || "")}</p>
    ${(r.notices || []).map(n => `<p class="small warn" data-review-notice>${esc(n)}</p>`).join("")}${plat}`;
}
async function loadReview(keys) {
  const r1 = $("#q-review-r1"), r2 = $("#q-review-r2"), ps = $("#q-review-platform");
  if (!r1) return;
  let provs = { providers: [] };
  try { provs = await api("/api/providers"); } catch (e) { /* no backends: the readers line says so */ }
  const opts = (empty, cur) => `<option value="">${esc(empty)}</option>` + (provs.providers || []).map(p => `<option value="${esc(p.name)}"${p.name === cur ? " selected" : ""}>${esc(p.name)}</option>`).join("");
  const keep1 = r1.value, keep2 = r2.value, keepP = ps.value;
  r1.innerHTML = opts("from Settings (review row)", keep1);
  r2.innerHTML = opts("reader 1 again", keep2);
  ps.innerHTML = `<option value="">every chosen platform</option>` + (keys || []).map(k => `<option value="${esc(k)}">${esc(k)}</option>`).join("");
  ps.value = (keys || []).includes(keepP) ? keepP : "";
  const chosen = () => [r1.value, r2.value].filter(Boolean);
  const show = async () => {
    const q = chosen().map(n => `readers=${encodeURIComponent(n)}`).join("&");
    let d;
    try { d = await api(`/api/query/review${q ? `?${q}` : ""}`); } catch (e) { $("#q-review-readers").innerHTML = `<li class="bad">${esc(e.message)}</li>`; return; }
    $("#q-review-readers").innerHTML = d.error ? `<li class="bad">${esc(d.error)}</li>` :
      (d.readers || []).map(x => `<li data-reader="${x.run}">reader ${x.run}: <b>${esc(x.name)}</b> (${esc(x.model || "no model")}, family ${esc(x.family)}) <span class="chip loc-${esc(x.locality || "unknown")}">${esc(x.locality_label || x.locality || "unknown")}</span>${x.refused ? ` <span class="bad">${esc(x.refused)}</span>` : ""}</li>`).join("") +
      (d.notices || []).map(n => `<li class="warn" data-review-notice>${esc(n)}</li>`).join("") +
      (d.source ? `<li class="muted">readers from: ${esc(d.source)}</li>` : "");
    $("#q-review-run").disabled = !!d.error || (d.readers || []).some(x => x.refused);
    $("#q-review-out").innerHTML = reviewFindingsHtml(d.last);
  };
  r1.onchange = show; r2.onchange = show;
  $("#q-review-run").onclick = async (ev) => {
    const btn = ev.currentTarget, msg = $("#q-review-msg");
    btn.disabled = true; msg.textContent = "reading…";
    try {
      const body = JSON.stringify({ platform: ps.value || null, readers: chosen().length ? chosen() : null });
      let r;
      for (let i = 0; ; i++) {   // two readers may each need their consent: the retry's 403 opens the second dialog
        try { r = await api("/api/query/review", { method: "POST", body }); break; } catch (e) {
          if (i < 2 && e.status === 403 && e.detail && e.detail.backend_consent_required && !e.cancelled) continue;
          throw e;
        }
      }
      msg.textContent = r.message;
      $("#q-review-out").innerHTML = reviewFindingsHtml(r);
    } catch (e) { msg.textContent = e.message; } finally { btn.disabled = false; }
  };
  await show();
}
// Limits applied in the database (D10): the line under each string and the fields that change it; same engine as
// `wider query limits <platform> --dates --languages --reason`. An empty field clears that limit.
async function loadLimits() {
  let all;
  try { all = await api("/api/query/limits"); } catch (e) { return; }
  document.querySelectorAll("[data-limits]").forEach(box => {
    const k = box.dataset.limits, r = all[k]; if (!r) { box.innerHTML = ""; return; }
    const lim = r.limits || {};
    box.innerHTML = `<p class="small" data-limits-line="${esc(k)}">${esc(r.line)}</p>
      <details class="small"><summary>Limits applied in this database</summary>
        <div class="row">
          <label class="inline">Dates <input data-lim="dates" value="${esc(lim.dates || "")}" placeholder="2010..2024" size="22"></label>
          <label class="inline">Languages <input data-lim="languages" value="${esc((lim.languages || []).join(", "))}" placeholder="en, de" size="14"></label>
          <label class="inline">Reason <input data-lim="reason" value="${esc(lim.reason || "")}" size="30"></label>
          <button class="secondary small-btn" data-lim-save>Save limits</button>
        </div>
        <p class="muted small">Write dates as 2010..2024 or 2010-01-01..2024-12-31; leave one side open for no bound. An empty field clears it.</p>
        <p class="small" role="status" data-lim-msg></p>
      </details>`;
    box.querySelector("[data-lim-save]").onclick = async (ev) => {
      const btn = ev.currentTarget, msg = box.querySelector("[data-lim-msg]");
      const val = f => box.querySelector(`[data-lim="${f}"]`).value;
      btn.disabled = true;
      try {
        const res = await api("/api/query/limits", { method: "POST",
          body: JSON.stringify({ platform: k, dates: val("dates"), languages: val("languages"), reason: val("reason") }) });
        box.querySelector("[data-limits-line]").textContent = res.line;
        msg.textContent = res.change_id ? "saved" : "nothing changed";
      } catch (e) { msg.textContent = e.message; } finally { btn.disabled = false; }
    };
  });
}
$("#q-import-toggle").onclick = () => { const box = $("#q-import"); box.hidden = !box.hidden; $("#q-import-toggle").setAttribute("aria-expanded", String(!box.hidden)); };
$("#q-import-preview").onclick = async () => {
  const btn = $("#q-import-preview"), text = $("#q-import-text").value, list = $("#q-import-findings");
  if (!text.trim()) { list.innerHTML = ""; $("#q-import-msg").textContent = "paste a search string first"; return; }
  btn.disabled = true;
  try {
    const r = await api("/api/query/parse", { method: "POST", body: JSON.stringify({ text, platform: $("#q-import-platform").value || "ebsco" }) });
    const lines = r.findings.map((f) => `<li>${esc(f.level)} at position ${f.pos}: ${esc(f.message)}</li>`)
      .concat(r.blocks.map((b) => `<li>${esc(b.text)}</li>`));
    list.innerHTML = lines.join("");
    $("#q-import-msg").textContent = `read as ${r.blocks.length} block${r.blocks.length === 1 ? "" : "s"}, nothing saved`
      + (r.findings.length ? ` · ${r.findings.length} warning${r.findings.length === 1 ? "" : "s"}` : "")
      + (r.save_refusal ? ` · ${r.save_refusal}` : "");
  }
  catch (e) { list.innerHTML = ""; $("#q-import-msg").textContent = e.message; }
  finally { btn.disabled = false; }
};
$("#q-import-save").onclick = async () => {
  const btn = $("#q-import-save"), text = $("#q-import-text").value;
  if (!text.trim()) { $("#q-import-findings").innerHTML = ""; $("#q-import-msg").textContent = "paste a search string first"; return; }
  btn.disabled = true;  // a double click saves one version
  try {
    const go = await confirmSave("synonyms", "Import as a new synonyms version?", "Import");
    if (!go) return;
    const body = { text, platform: $("#q-import-platform").value || "ebsco" };
    if (typeof go === "string") body.seen_choice = go;
    const r = await api("/api/query/import", { method: "POST", body: JSON.stringify(body) });
    $("#q-import-findings").innerHTML = (r.findings || []).map((f) => `<li>${esc(f.level)} at position ${f.pos}: ${esc(f.message)}</li>`).join("");
    $("#q-import-msg").textContent = `saved synonyms v${r.version}` + (r.notice ? ` · ${r.notice}` : ""); await loadQuery();
  }
  catch (e) { $("#q-import-findings").innerHTML = ""; $("#q-import-msg").textContent = e.message; }   // no stale preview under a refusal
  finally { btn.disabled = false; }
};


// ---------------- import + coverage page ----------------
async function loadImport() {
  const d = await api("/api/import");
  if (globalThis.Rounds) globalThis.Rounds.fillSelector();
  $("#imp-root").textContent = d.root;
  // "Enters deduplication" (D5): a checkbox on imported files only; the file check's verdict beside it (import.js, D32 F8)
  let chk = null;
  if (window.filesTable) { $("#imp-check-msg").textContent = "checking…"; try { chk = await api("/api/import/check"); } catch (e) { $("#imp-check-msg").textContent = e.message; } }
  $("#imp-files").innerHTML = window.filesTable ? filesTable(d, chk) : "";
  if (window.loadUploadPlatforms) loadUploadPlatforms(d);
  if (window.wireSampleFields) wireSampleFields($("#imp-files"));
  $("#imp-files").querySelectorAll("[data-source]").forEach((box) => {
    box.onchange = async () => {
      const on = box.checked;
      box.disabled = true;
      try {
        const r = await api(`/api/import/sources/${box.dataset.source}/included`, { method: "POST", body: JSON.stringify({ included: on }) });
        await loadImport();
        $("#imp-msg").textContent = `File ${r.source_id} (${r.file}) ${on ? "enters deduplication" : "is kept out of deduplication"}; the next deduplication run reads it. Undo it in History.`;
      } catch (e) {   // refused (later stages hold rows on records only this file brought): nothing changed
        box.checked = !on; box.disabled = false;
        $("#imp-msg").textContent = e.message;
      }
    };
  });
  $("#cov-tables").innerHTML = ""; $("#cov-note").textContent = "";
  if (window.showUploadsFinal) showUploadsFinal(d.final_at, d.files.length, d.files.filter(f => !f.imported).length);
  if (window.loadImportCheck && chk) loadImportCheck(chk);
  if (window.Diagnosis) Diagnosis.load();
  if (globalThis.IndexingCheck) globalThis.IndexingCheck.load();
}
// a run that reopens final uploads under a frozen query with validation papers shown asks the choice first (D21, Q2)
$("#imp-run").onclick = async () => {
  $("#imp-msg").textContent = "importing…";
  try {
    const rq = globalThis.Rounds ? Rounds.roundParam() : "";   // the round selector (D8); "" = the server's default
    const r = await Refine.withSeenChoice("Import the new files?", (c) => api(`/api/import/run?${[c ? "seen_choice=" + c : "", rq].filter(Boolean).join("&")}`, { method: "POST" }));
    if (!r) { $("#imp-msg").textContent = "Nothing was imported."; return; }
    $("#imp-msg").textContent = [r.notice, r.recorded].filter(Boolean).join(" ");
    showStageResult(r.stage_result); await loadImport();
  } catch (e) { $("#imp-msg").textContent = ""; showStageError("import", e.message); }
};
// The set cell of a seed-paper listing (D3, validation-reuse D1): "Dev" / "Val", "Val (used)" for a used validation
// paper, "Dev (former val)" for a shown validation paper moved to Development, "Dev" with a title for a Validation
// paper counted with Development, and the papers from the query's results under their label in full (coverage card
// and "Check the seed papers").
const NOT_COUNTED = "from the query's results: kept, not counted in validation";
const USED = "Validation, used";
const USED_TITLE = "used validation paper: seen before the query was revised or the uploads reopened; recall on it is not independent";
const WAS_DEV_TITLE = "in Validation, but was in Development: counted with Development";
const setCell = (s, r = {}) => { const t = s === USED ? USED_TITLE : (s === "Development" && r.was_development ? WAS_DEV_TITLE : ""); const label = s === NOT_COUNTED ? s : (s === USED ? "Val (used)" : (s === "Development" && r.former ? "Dev (former val)" : s.slice(0, 3))); return `<td${t ? ` title="${esc(t)}"` : ""}>${esc(label)}</td>`; };
async function showCoverage(scope) {
  const sentence = (t) => t.charAt(0).toUpperCase() + t.slice(1) + (t.endsWith(".") ? "" : ".");
  $("#cov-note").textContent = "checking…";
  try {
    const q = `/api/coverage?scope=${encodeURIComponent(scope)}${$("#cov-val").checked ? "&validation=true" : ""}`;
    const d = await (window.Refine ? Refine.lookWithSeenChoice : (t, send) => send(null))("Show the validation seed papers?", (c) => api(q + (c ? "&seen_choice=" + c : "")));
    if (!d) { $("#cov-note").textContent = ""; return; }   // Cancel: nothing shown, nothing recorded
    const note = d.note || {};
    // the look's notice, else the validity note (D3): its sentence, then any line for an unlock, a look or a choice
    const main = d.validation_peeked ? sentence(d.notice || "validation set shown before the query was frozen; this look is logged in the history")
      : (note.sentence || (d.validation_included ? "Development and validation sets." : "Validation set hidden until the query is frozen or the uploads are marked final."));
    $("#cov-note").textContent = main;
    // the validity lines, then (when any set is counted) that recall on known papers overstates recall (D7)
    const lines = [...(note.lines || []), ...(d.known_papers_note && Object.keys(d.scopes).length ? [d.known_papers_note] : [])];
    if (lines.length) $("#cov-note").innerHTML = esc(main) + lines.map(l => `<br>${esc(l)}`).join("");
    $("#cov-tables").innerHTML = Object.entries(d.scopes).map(([sc, sets]) => `<h2>${esc(sc)}: ${Object.entries(sets).map(([s, v]) => `${esc(s)} ${esc(v.text || v.found + "/" + v.n)}`).join(" · ")}</h2>` +
      `<table><tr><th>id</th><th>set</th><th>method</th><th>score</th><th>title</th></tr>` +
      Object.entries(sets).flatMap(([s, v]) => v.rows.map(r => `<tr><td>${esc(r.benchmark_id)}</td>${setCell(s, r)}<td class="${r.method === "none" ? "bad" : (r.method === "fuzzy" ? "" : "ok")}">${r.method === "none" ? "missing" : esc(r.method)}${r.needs_confirmation ? " (confirm)" : ""}</td><td>${r.score.toFixed(2)}</td><td>${esc(r.title)}</td></tr>`)).join("") + `</table>`).join("");
  } catch (e) { $("#cov-note").textContent = e.message; }
}
$("#cov-plat").onclick = () => showCoverage("platforms");
$("#cov-corpus").onclick = () => showCoverage("corpus");

// ---------------- enrichment page ----------------
// {"openalex": 3, "export": 62} → "openalex 3, export 62"; none → "none yet"
const counts = (o) => Object.entries(o || {}).map(([k, v]) => `${k} ${v}`).join(", ") || "none yet";
// layout A (owner walkthrough M2-M7): the lead figure is the records still without a usable abstract; Fetch is off while
// nothing is left to look up or a changed batch size waits for Apply changes or Keep
const EN_DEFAULT_BATCH = 100;
let enBatch = EN_DEFAULT_BATCH, enLast = null, enGroup = null;
function renderEnrich(d) {
  enLast = d;
  const looked = (d.filled || 0) + (d.exhausted || 0) + (d.failed || 0);
  $("#en-facts").innerHTML = `<div class="lead"><dt>records still have no usable abstract</dt><dd>${esc(d.thin)}</dd></div>`
    + [[d.pending, "left to look up"], [looked, "looked up"], [d.filled, "abstract found"], [d.exhausted, "in no source"], [d.failed || 0, "failed, can retry"]]
      .map(([v, k]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("");
  $("#en-summary").innerHTML = [["unique records", d.records], ["thin or missing abstracts", d.thin], ["pending lookups", d.pending],
    ["filled so far", d.filled], ["exhausted (no source had an abstract)", d.exhausted], ["failed (service unreachable or error; retry later)", d.failed || 0],
    ["filled by source", counts(d.by_source)], ["abstract origin", counts(d.by_source_all)]]
    .map(([k, v]) => `<tr><th scope="row" class="kv">${esc(k)}</th><td><b>${esc(v)}</b></td></tr>`).join("");
  $("#en-retry").hidden = !(d.failed > 0);
  const s2 = $("#en-s2");
  s2.textContent = d.s2_key_page || d.s2_key_note || "";
  s2.className = d.s2_key === "set" ? "" : "needs-line";   // D32 F10: a missing key is not grey
  enButtons();
}
// the run button: its label is the applied batch size; off with nothing pending or a batch size not applied yet
function enButtons() {
  const d = enLast || {}, typed = enLimit(), waiting = typed !== null && typed !== enBatch;
  const none = d.pending === 0;
  $("#en-run").textContent = none ? "Nothing left to look up" : `Fetch next ${Math.min(enBatch, d.pending || enBatch)}`;
  $("#en-run").disabled = none || waiting || typed === null;
  $("#en-retry").disabled = waiting || typed === null;
}
// the Questions page asks only about records no source had, at most 50 per run, and only open ones: say so, so its count
// is not read as this page's "in no source" or "still no usable abstract" figure (inspection 1 Nov, item 12)
async function enQuestionNote() {
  const el = $("#en-qnote"); if (!el) return;
  let n = 0;
  try { n = ((await api("/api/questions?limit=1000")).questions || []).filter(x => x.kind === "enrich_unresolved").length; }
  catch (e) { el.hidden = true; return; }
  el.hidden = n === 0;
  el.textContent = n ? `${n} of the records in no source wait as questions on the Questions page (open questions only; one look-up run asks about at most 50 records), so the Questions count can be smaller than the figures above.` : "";
}
async function loadEnrich() {
  renderEnrich(await api("/api/enrich"));
  await enQuestionNote();
  await SettingsCard.draw("#en-settings", "enrich");
  await enRecords();
}
// the records by state and by source: groups with their counts on the left, the chosen group's records on the right
async function enRecords(group) {
  const box = $("#en-recs"); if (!box) return;
  let r;
  try { r = await api(`/api/enrich/records?group=${encodeURIComponent(group || enGroup || "waiting")}&limit=50`); }
  catch (e) { box.textContent = e.message; return; }
  if (!group && !enGroup) {   // open on the first missing group that has records
    const first = ["waiting", "failed", "nowhere"].find(k => r.counts[k] > 0);
    if (first && first !== r.group) return enRecords(first);
  }
  enGroup = r.group;
  const missing = ["waiting", "failed", "nowhere"], found = Object.keys(r.labels).filter(k => !missing.includes(k));
  const row = (k) => `<button type="button" class="en-g" data-en-group="${esc(k)}" aria-pressed="${k === r.group ? "true" : "false"}"><span>${esc(r.labels[k])}</span><b>${esc(r.counts[k] || 0)}</b></button>`;
  const isFound = !missing.includes(r.group);
  box.innerHTML = `<div class="en-side"><nav aria-label="Show records"><p class="en-glab">Still without an abstract · ${esc(r.missing)}</p>${missing.map(row).join("")}`
    + `<p class="en-glab">Abstract found · ${esc(r.found)}</p>${found.map(row).join("")}</nav>`
    + `<div>${r.rows.length ? `<div class="table-wrap"><table class="en-rec"><thead><tr><th>Record</th><th>${isFound ? "Abstract" : "What happened"}</th></tr></thead><tbody>`
      + r.rows.map(x => `<tr><td><b>${esc(x.first_author || "–")} ${esc(x.year || "")}</b> · ${esc(x.title || "")}<br><span class="en-doi">${x.doi ? "DOI " + esc(x.doi) : "no DOI"}</span></td><td>${esc(x.what)}</td></tr>`).join("")
      + `</tbody></table></div>` : ""}<p>${r.rows.length ? `Showing ${esc(r.rows.length)} of ${esc(r.counts[r.group] || 0)} · ${esc(r.label)}, sorted by first author` : `No records here: ${esc(r.label)}.`}</p></div></div>`;
  box.onclick = (e) => { const b = e.target.closest && e.target.closest("[data-en-group]"); if (b) enRecords(b.dataset.enGroup); };
}
// the batch size: a whole number of at least 1, else null (the buttons are disabled)
const enLimit = () => { const v = $("#en-limit").value.trim(); return /^\d+$/.test(v) && Number(v) >= 1 ? Number(v) : null; };
function enApplyBox() {
  const n = enLimit(), box = $("#en-apply");
  if (n === null) box.innerHTML = '<p class="needs-line" role="alert">The batch size must be a whole number of at least 1, written in digits, e.g. 100.</p>';
  else if (n !== enBatch) box.innerHTML = `<div class="en-pending"><p><b>Not applied yet.</b> The next batch looks up ${esc(n)} records instead of ${esc(enBatch)}. Fetch waits until you apply or keep.</p>`
    + `<div class="row"><button type="button" class="main-act" id="en-apply-btn">Apply changes</button><button type="button" class="secondary" id="en-keep-btn">Keep ${esc(enBatch)} records</button></div></div>`;
  else box.innerHTML = `<p>Batch size: ${esc(enBatch)} records${enBatch === EN_DEFAULT_BATCH ? ", WIDER's default" : ""}. Change the number and an Apply button appears here.</p>`;
  const a = $("#en-apply-btn"), k = $("#en-keep-btn");
  if (a) a.onclick = () => enSetBatch(n);
  if (k) k.onclick = () => { $("#en-limit").value = String(enBatch); enApplyBox(); };
  enButtons();
}
function enSetBatch(n) {
  enBatch = n;
  $("#en-cmd").textContent = $("#en-cmd").textContent.replace(/--limit \d+/, `--limit ${n}`);   // the fold's command follows
  enApplyBox();
}
$("#en-limit").oninput = enApplyBox;
$("#en-limit").onchange = $("#en-limit").oninput;
let enStarting = false;  // pressing Fetch again while its consent dialog or start request is open starts nothing more
async function startEnrich(retry) {
  if (enStarting) return;
  const n = enBatch; if (enLimit() !== n) return;
  enStarting = true;
  $("#en-msg").textContent = "";
  try {
    await Jobs.start(`/api/jobs/enrich?limit=${n}${retry ? "&retry=true" : ""}`, {}, (j) => { if (j.result && j.result.summary) { renderEnrich(j.result.summary); enRecords(); } });
    $("#en-msg").textContent = retry ? "Retrying failed and exhausted lookups in the background; see the bar at the top." : "Fetching in the background; see the bar at the top.";
  } catch (e) { if (e.cancelled) $("#en-msg").textContent = e.message; else showStageError("enrich", e.message); }
  finally { enStarting = false; }
}
$("#en-run").onclick = () => startEnrich(false);
$("#en-retry").onclick = () => startEnrich(true);


// ---------------- ranking page ----------------
let rkOffset = 0;
async function loadRank() {
  const proj = await api("/api/project");
  const cfg = proj.config;
  rkEmbeddings = cfg.embeddings || [];
  // the picker cards at the top (pickers.js): opening the page sends GET /api/embeddings only
  const pick = await Pickers.embeddingPicker($("#rk-embeddings"), { onChanged: () => (window.afterAction ? afterAction() : loadRank()) });
  rkEmbedding = pick.chosen;
  $("#rk-embed").disabled = $("#rk-run").disabled = !rkEmbedding;
  const arts = await api("/api/construct");
  // optional: without anchors the ranking uses the cells alone, and anchors_version is left out of the request
  const anchors = (arts.anchors || []).map(a => ({ value: a.version, label: `anchors v${a.version} (${a.status})` })).reverse();
  fillSelect("#rk-anchors", anchors, "no anchors (cells only)");
  // the field stays empty so the server applies the default capped at the anchors' cell count; the placeholder names it
  $("#rk-topn").placeholder = `default ${(cfg.protocol && cfg.protocol.ranking && cfg.protocol.ranking.top_n_cells) || 2}`;
  // the box stays enabled (inform, not control: D21); while the validation set is hidden it says that ticking it is a
  // logged look, and the researcher chooses on the confirm step whether the papers shown count as seen (D3)
  const gated = !(cfg.protocol && cfg.protocol.identification && cfg.protocol.identification.validation_after_freeze === false);
  const valOpen = !!proj.validation_unlocked || !gated, why = $("#rk-ablate-val-why");
  $("#rk-ablate-val").disabled = false;
  why.hidden = valOpen;
  why.textContent = valOpen ? "" : "Include the validation set: it is hidden until the query is frozen or the uploads are final; including it now is logged, and you choose whether the papers shown count as seen";
  await loadExplorer();
}
// every cell's cosine for one row, best first; the best cell in bold
// the two best cells by name (underscores read as spaces); the others in the hover text of "+N more" (UX review F24)
function cellScores(cs) {
  const e = Object.entries(cs || {}).sort((a, b) => b[1] - a[1]);
  const one = ([c, v]) => `${String(c).replace(/_/g, " ")} ${Number(v).toFixed(3)}`;
  const top = e.slice(0, 2).map((x, i) => i ? esc(one(x)) : `<b>${esc(one(x))}</b>`).join("<br>");
  const rest = e.slice(2);
  return top + (rest.length ? `<br><span class="cell-more" title="${esc(rest.map(one).join("; "))}">+${rest.length} more<span class="sr-only">: ${esc(rest.map(one).join("; "))}</span></span>` : "");
}
// ui-redesign D5: the select lists base names, each its newest version; earlier versions only in the history fold
function rkBaseOptions(d) {
  const cur = d.ranking ? d.ranking.base_name || d.ranking.name : "";
  return (d.bases || []).map(b => `<option value="${esc(b)}"${b === cur ? " selected" : ""}>${esc(b)}</option>`).join("") || "<option value=''>no ranking yet</option>";
}
// the version history fold, shown only when the ranking has more than one version (twin: `wider rank show`)
function rkVersionsHtml(vs) {
  if (!vs || vs.length < 2) return "";
  const li = vs.map((v, i) => `<li>version ${esc(v.version)}${v.samples && v.samples.length ? ` (${esc(v.samples.join(", "))})` : ""}${i === vs.length - 1 ? " (newest)" : ""}</li>`).join("");
  return `<details data-rk-versions><summary>${vs.length} versions</summary><ul>${li}</ul></details>`;
}
// the versions that need review, one box per sample drawn from them (twin: `wider rank show`, `wider rank keep`):
// Keep (per version when several need review); a labelled development sample cannot be drawn afresh, so it offers
// Keep only; the validation sample, or a sample with no labels yet, can also be drawn again on Samples and labelling
function rkReviewHtml(review) {
  if (!review || !review.length) return "";
  const many = review.length > 1;
  return review.map(it => {
    const keep = `<button type="button" class="secondary" data-rk-keep="${esc(it.version)}">${many ? `Keep version ${esc(it.version)}` : "Keep this ranking"}</button>`;
    const fresh = (label) => `<button type="button" class="secondary" data-rk-fresh="#sample">${label}</button>`;
    const boxes = (it.samples || []).map(s => {
      const dev = s.kind === "purposive";
      const text = dev && s.labelled ? `${esc(s.name)} is labelled: a fresh development sample draw does not exist, so keep this ranking for it.`
        : dev ? `${esc(s.name)} has no labels yet: you can keep this ranking or draw it again.`
        : `${esc(s.name)} was drawn from version ${esc(it.version)}: keep this ranking for it, or draw a fresh sample from the newest version.`;
      return `<div class="rk-box" data-rk-sample="${esc(s.name)}"><p>${text}</p><div class="row">${keep}${dev && s.labelled ? "" : fresh(dev ? "Draw it again" : "Draw a fresh sample")}</div></div>`;
    }).join("");
    return `<div data-rk-review="${esc(it.version)}"><p><b>Ranking needs review</b> (version ${esc(it.version)}): ${esc(it.reason)}</p>`
      + (boxes || `<div class="row">${keep}</div>`) + `</div>`;
  }).join("");
}
// "Update ranking" (D5; twin: `wider rank update`): the first request checks; when the embeddings were made from older
// texts the answer is the check (confirm_needed), asked with its notice: embed the changed texts again and rank (a
// model to download is allowed by the same answer, as the notice says), rank with the older embeddings (keeps "needs
// review"), or Cancel, which sends nothing. Consent is asked by the usual 403. Returns the sentence for #rk-msg;
// `deps.start(url, done)` calls `done` when the job ends, and `deps.clear` then takes the "in the background" line away.
async function rkUpdate(deps) {
  const url = "/api/jobs/rank-update";
  let ended = false;
  const done = () => { ended = true; if (deps.clear) deps.clear(); };
  let r = await deps.start(url, done);
  if (r && r.confirm_needed) {
    const c = await deps.askChoice(`<p id="choice-title">${esc(r.notice)}</p>${r.keep_note ? `<p>${esc(r.keep_note)}</p>` : ""}`
      + `<div class="row"><button type="button" data-act="embed">Embed the changed texts and rank</button>`
      + `<button type="button" class="secondary" data-act="older">Rank with the older embeddings</button>`
      + `<button type="button" class="secondary" data-act="cancel">Cancel</button></div>`);
    if (!c) return "Nothing was sent and no version was added.";
    r = await deps.start(c.act === "older" ? `${url}?use_older=true` : `${url}?confirm=true${r.needs_download ? "&allow_download=true" : ""}`, done);
  }
  if (r && r.skipped) return r.error;
  return ended ? "" : "Updating the ranking in the background; see the bar at the top.";
}
async function loadExplorer() {
  const sel = $("#rk-ranking").value, cell = $("#rk-cell").value;
  const d = await api(`/api/rank?offset=${rkOffset}&limit=50${sel ? "&ranking=" + encodeURIComponent(sel) : ""}${cell ? "&cell=" + encodeURIComponent(cell) : ""}`);
  $("#rk-info").textContent = `${d.corpus.records} unique records (${d.corpus.thin} thin) · embedding sets: ${d.corpus.sets.map(s => s.set_name + " " + s.dim + "d").join(", ") || "none"}`;
  $("#rk-ranking").innerHTML = rkBaseOptions(d);
  $("#rk-versions").innerHTML = rkVersionsHtml(d.versions);
  $("#rk-review").innerHTML = rkReviewHtml(d.review);
  $("#rk-review").hidden = !(d.review && d.review.length);
  $("#rk-keep-note").textContent = d.keep_before || "";
  $("#rk-keep-note").hidden = !d.keep_before;
  if (d.ranking) {
    const cells = Object.keys(d.best_cell_top3000);
    if ($("#rk-cell").options.length <= 1) $("#rk-cell").innerHTML = `<option value="">all cells</option>` + cells.map(c => `<option value="${esc(c)}" ${c === cell ? "selected" : ""}>${esc(c)} (${esc(d.best_cell_top3000[c])} in top ${esc(d.top_pool)})</option>`).join("");
  }
  $("#rk-page").textContent = !d.ranking ? "" : d.rows.length ? `${rkOffset + 1}–${rkOffset + d.rows.length} of ${d.n_rows}`
    : !d.n_rows ? (cell ? `no record has ${cell} as its best cell` : "the ranking holds no records") : `none of ${d.n_rows}`;
  $("#rk-prev").disabled = rkOffset === 0;
  $("#rk-next").disabled = !d.ranking || rkOffset + d.rows.length >= d.n_rows;
  $("#rk-table").innerHTML = `<tr><th>rank</th><th>score</th><th>cell scores (best first)</th><th>title</th><th>year</th><th>abstract</th></tr>` +
    d.rows.map(r => `<tr><td>${esc(r.rank)}</td><td>${r.score.toFixed(3)}</td><td class="small">${cellScores(r.cell_scores)}</td><td>${esc(r.title)}<div class="muted small">${esc(r.journal || "")} · ${esc(r.platforms)}</div></td><td>${esc(r.year || "")}</td><td>${r.thin ? '<span class="bad">thin</span>' : esc(r.abstract_words) + " w"}</td></tr>`).join("");
}
$("#rk-ranking").onchange = () => { rkOffset = 0; $("#rk-cell").innerHTML = `<option value="">all cells</option>`; loadExplorer(); };
$("#rk-cell").onchange = () => { rkOffset = 0; loadExplorer(); };
let rkPaging = false;   // one page request at a time: a second click while it loads is ignored
async function rkPage(step) {
  const btn = $(step > 0 ? "#rk-next" : "#rk-prev"), other = $(step > 0 ? "#rk-prev" : "#rk-next");
  if (rkPaging || btn.disabled) return;
  rkPaging = true;
  const from = rkOffset;
  rkOffset = Math.max(0, rkOffset + step);
  try { await loadExplorer(); } catch (e) { rkOffset = from; throw e; } finally { rkPaging = false; }
  if (btn.disabled && !other.disabled && (document.activeElement === btn || document.activeElement === document.body)) other.focus();
}
$("#rk-prev").onclick = () => rkPage(-50);
$("#rk-next").onclick = () => rkPage(50);
$("#rk-embed").onclick = async () => {
  const e = rkEmbedding; $("#rk-msg").textContent = "";
  if (!e) return;
  try {
    // a priced model shows its cost range first; Cancel starts no job
    if (!(await Backends.confirmEmbedCost(rkEmbeddings.find(x => x.name === e), api, askChoice))) { $("#rk-msg").textContent = "Nothing started."; return; }
    let r = await Jobs.start(`/api/jobs/embed?embedding=${encodeURIComponent(e)}`, {}, () => loadExplorer());
    if (r.skipped && await askConfirm(`${r.error}. Download the weights now?`, "Download the weights")) r = await Jobs.start(`/api/jobs/embed?embedding=${encodeURIComponent(e)}&allow_download=true`, {}, () => loadExplorer());
    $("#rk-msg").textContent = r.skipped ? r.error : `Embedding with ${e} in the background (minutes for a local model); see the bar at the top.`;
  } catch (err) { showStageError("rank", err.message); }
};
$("#rk-run").onclick = async () => {
  const emb = rkEmbedding, anc = $("#rk-anchors").value; if (!emb) return;
  // empty top-N = the protocol default; a fraction or text is refused here, a whole number (even 0 or -3) goes to the
  // server, which names the allowed range
  const topn = $("#rk-topn").value.trim();
  if (topn && !/^-?\d+$/.test(topn)) { $("#rk-msg").textContent = ""; showStageError("rank", `top-N must be a whole number of cells, e.g. 2 (got "${topn}")`); return; }
  $("#rk-msg").textContent = "ranking…";
  const btn = $("#rk-run"); if (btn.disabled) return;
  const hadFocus = document.activeElement === btn; btn.disabled = true;   // a double click sends one request
  try { const r = await api(`/api/rank/run?embedding=${encodeURIComponent(emb)}${anc ? "&anchors_version=" + encodeURIComponent(anc) : ""}${topn ? "&top_n=" + encodeURIComponent(topn) : ""}`, { method: "POST" }); $("#rk-msg").textContent = ""; showStageResult(r.stage_result); rkOffset = 0; $("#rk-cell").innerHTML = `<option value="">all cells</option>`; await loadExplorer(); }
  catch (e) { $("#rk-msg").textContent = ""; showStageError("rank", e.message); }
  // disabling the button dropped the focus to the page; a keyboard user gets it back on the button
  finally { btn.disabled = false; if (hadFocus && (!document.activeElement || document.activeElement === document.body)) btn.focus(); }
};
$("#rk-update").onclick = async () => {
  $("#rk-msg").textContent = "";
  try {
    $("#rk-msg").textContent = await rkUpdate({ start: (u, done) => Jobs.start(u, {}, async () => { done(); await loadExplorer(); }),
                                                askChoice, clear: () => { $("#rk-msg").textContent = ""; } });
  }
  catch (e) { showStageError("rank", e.message); }
};
$("#rk-review").onclick = async (ev) => {
  const k = ev.target.closest("[data-rk-keep]"), f = ev.target.closest("[data-rk-fresh]");
  if (f) { location.hash = f.dataset.rkFresh; return; }
  if (!k || k.disabled) return;
  k.disabled = true;
  try { await api("/api/rank/keep", { method: "POST", body: JSON.stringify({ version: Number(k.dataset.rkKeep) }) }); $("#rk-msg").textContent = `Kept version ${k.dataset.rkKeep}; undo it in History.`; window.afterAction ? await afterAction() : await loadExplorer(); }
  catch (e) { k.disabled = false; showStageError("rank", e.message); }
};
$("#rk-topn").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("#rk-run").click(); } };
$("#rk-validate").onclick = async () => {
  try {
    // the "Include the validation set" box next to the button: asked for while hidden, a logged look (D3)
    const qs = new URLSearchParams();
    if ($("#rk-ranking").value) qs.set("ranking", $("#rk-ranking").value);
    if ($("#rk-ablate-val").checked) qs.set("validation", "true");
    const v = await (window.Refine ? Refine.lookWithSeenChoice : (t, send) => send(null))("Also check the search on the validation seed papers?", (c) => {
      if (c) qs.set("seen_choice", c);
      return api(`/api/rank/validate${qs.toString() ? "?" + qs : ""}`);
    });
    if (!v) return;   // Cancel: nothing shown, nothing recorded
    const ns = Object.keys(v.top_n_compare || {});  // one column per compared top-N (protocol: ranking.top_n_compare)
    $("#rk-validation").innerHTML = rkSeedCardHtml(v) + `<h2>Seed paper recovery · ${Object.entries(v.summary).map(([s, m]) => `${esc(s)}: ${esc(m.found)}/${esc(m.n)} found, median rank ${esc(m.median_rank)}, recall@250 ${esc(m.recall_at_250)}`).join(" · ")}${v.validation_included ? "" : " · validation set hidden until the query is frozen or the uploads are marked final"}</h2>${v.notice ? `<p class="small">${esc(v.notice)}</p>` : ""}${(v.notes || []).map(n => `<p class="small">${esc(n)}</p>`).join("")}
      ${pctTable(v.summary)}
      <table><tr><th>id</th><th>set</th><th>rank</th>${ns.map(n => `<th>top-${esc(n)}</th>`).join("")}<th>best cell</th><th>title</th></tr>` +
      v.rows.sort((a, b) => (a.rank || 1e9) - (b.rank || 1e9)).map(r => r.found ? `<tr><td>${esc(r.benchmark_id)}</td>${setCell(r.set, r)}<td><b>${esc(r.rank)}</b></td>${ns.map(n => `<td>${esc(r["rank_top" + n])}</td>`).join("")}<td class="small">${esc(r.best_cell)}</td><td>${esc(r.title)}</td></tr>`
        : `<tr><td>${esc(r.benchmark_id)}</td>${setCell(r.set, r)}<td class="bad" colspan="${ns.length + 2}">not in corpus</td><td>${esc(r.title)}</td></tr>`).join("") + "</table>";
  } catch (e) { $("#rk-validation").textContent = e.message; }
};
// benchmark papers within the top 1/5/10/25/50% per set, "k of n" (protocol.ranking.recall_at_pct; D3)
// the seed paper check as one card above the tables (owner walkthrough R2, mock redesign-c): the big figure is how many
// development seed papers rank in the top 10% (or the first share the protocol names), a green edge when all do and a
// rose edge when some fall below; it informs and blocks nothing
function rkSeedCardHtml(v) {
  const sets = Object.entries((v && v.summary) || {});
  if (!sets.length) return "";
  const pick = (m) => { const a = m.recall_at_pct || []; return a.find(h => Number(h.pct) === 10) || a[0]; };
  const [key, m] = sets.find(([s]) => String(s).toLowerCase() === "development") || sets[0];
  const name = String(key).toLowerCase();
  const h = pick(m);
  if (!h || !h.n) return "";
  const all = Number(h.k) === Number(h.n);
  const rest = sets.filter(([s]) => s !== key).map(([s, o]) => { const x = pick(o); return x && x.n ? `<div><dt>${esc(String(s).toLowerCase())} seed papers in the top ${esc(x.pct)}%</dt><dd>${esc(x.k)} of ${esc(x.n)}</dd></div>` : ""; }).join("");
  return `<div class="card rk-seed ${all ? "ok" : "warn"}" data-rk-seed="${all ? "ok" : "warn"}">
    <p class="rk-seed-state">${all ? "Seed paper check: all in the top " + esc(h.pct) + "%" : "Seed paper check: some fall below the top " + esc(h.pct) + "%"}</p>
    <p class="rk-seed-fig"><b>${esc(h.k)} of ${esc(h.n)}</b> ${esc(name)} seed papers rank in the top ${esc(h.pct)}% (rank ${esc(h.rank)} or better)</p>
    <p>Your seed papers are records you know belong in the review, so where they land shows whether the ranking puts relevant records near the top.${all ? "" : ` If some fall below the top ${esc(h.pct)}%, check your anchor sentences before you draw samples.`}</p>
    ${rest ? `<dl class="facts">${rest}<div><dt>records ranked</dt><dd>${esc(v.n)}</dd></div></dl>` : ""}</div>`;
}
function pctTable(summary) {
  const sets = Object.entries(summary || {});
  const head = (sets[0] && sets[0][1].recall_at_pct) || [];
  if (!head.length) return "";
  return `<table class="rk-pct"><tr><th>set</th>${head.map(h => `<th>top ${esc(h.pct)}% (rank ${esc(h.rank)})</th>`).join("")}</tr>` +
    sets.map(([s, m]) => `<tr><td>${esc(s)}</td>${(m.recall_at_pct || []).map(h => `<td>${esc(h.k)} of ${esc(h.n)}</td>`).join("")}</tr>`).join("") + "</table>";
}
window.pctTable = pctTable;
$("#rk-ablate").onclick = async () => {
  const val = $("#rk-ablate-val").checked;
  // the rows come keyed by the readable headers of `wider rank ablate` (rank/ablate.py::column_label); a look at the
  // hidden validation set asks the researcher first (Cancel runs nothing; "running" is cleared while the dialog asks)
  try {
    const d = await (window.Refine ? Refine.lookWithSeenChoice : (t, send) => send(null))("Run the test without one part on the validation seed papers too?",
      (c) => { $("#rk-ablation").textContent = "running every combination…";
        return api(`/api/rank/ablate${val ? "?validation=true" : ""}${c ? "&seen_choice=" + c : ""}`)   // a choice comes only with the box ticked
          .then((r) => { if (r && r.choice_needed) $("#rk-ablation").textContent = ""; return r; }); });
    if (!d) { $("#rk-ablation").textContent = ""; return; }
    afterAction();
    const rows = d.rows;
    if (!rows.length) { $("#rk-ablation").textContent = "nothing to ablate"; return; }
    const cols = Object.keys(rows[0]); $("#rk-ablation").innerHTML = `<h2>Ablation</h2>${d.notice ? `<p class="small">${esc(d.notice)}</p>` : ""}<table><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}</tr>` + rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c])}</td>`).join("")}</tr>`).join("") + "</table>"; }
  catch (e) { $("#rk-ablation").textContent = e.message; }
};
$("#rk-cluster").onclick = async () => {
  $("#rk-clusters").textContent = "clustering…";
  try { const c = await api("/api/rank/cluster"); $("#rk-clusters").innerHTML = `<h2>Clusters of the top ${esc(c.n_clustered ?? c.top)} (k=${esc(c.k)}, split-half ARI ${esc(c.split_half_ari)})</h2><table><tr><th>#</th><th>n</th><th>median rank</th><th>terms</th><th>medoid</th></tr>` + c.clusters.map(x => `<tr><td>${esc(x.cluster)}</td><td>${esc(x.n)}</td><td>${esc(x.median_rank)}</td><td>${esc(x.terms.join(", "))}</td><td>${esc(x.medoid ? x.medoid.title : "")}</td></tr>`).join("") + "</table>"; }
  catch (e) { $("#rk-clusters").textContent = e.message; }
};


// ---------------- sampling + labelling page ----------------
let lbCodebook = null;  // the labelling drawer (records.js) reads the exclusion reasons and label groups
let lbCodebookVersion = null;  // shown under the criteria of the focus pages (records.js)
// the sample's kind in plain words (owner inspection: no bare "purposive" or "probability")
function sampleKindWords(kind) {
  return kind === "probability" ? "validation sample, drawn at random within each band"
    : kind === "purposive" ? "development sample, records picked by hand" : String(kind || "");
}
// while Blind is ticked, another reviewer's include/exclude/maybe split stays hidden: only how far they are shows
// (inspection 1 Nov 2026: Reviewer B saw Reviewer A's 21 includes on this page)
function labelledSoFar(s) {
  const blind = !($("#lb-blind") && $("#lb-blind").checked === false);
  const me = (typeof WiderName !== "undefined" && WiderName.get()) || "";
  return Object.entries(s.progress.reviewers).map(([r, c]) => `${esc(r)} ${esc(c.done)} of ${esc(s.n)}` +
    (blind && r !== me ? " (decisions hidden while Blind is ticked)"
      : ` (include ${esc(c.include || 0)}, exclude ${esc(c.exclude || 0)}, maybe ${esc(c.maybe || 0)})`)).join(" · ") || "no labels yet";
}

// duplicate pairs still open: deciding one Duplicates later merges two records, and a merge of two records that both
// hold places in one sample is refused, so say it before a draw (inspection 1 Nov, item 2); a warning, never a block
async function smDedupWarn() {
  const el = $("#sm-dedup-warn"); if (!el) return;
  let n = 0;
  try { n = (await api("/api/dedup")).pending || 0; } catch (e) { n = 0; }
  el.hidden = !n;
  el.textContent = n ? `${n} duplicate pair${n === 1 ? " is" : "s are"} still open on Deduplication. Decide them before drawing: if both records of a pair are drawn and you later decide the pair Duplicates, the merge is refused until one record leaves the sample.` : "";
}
async function loadSamples() {
  const d = await api("/api/samples");
  smDedupWarn();
  $("#sm-samples").innerHTML = (Object.entries(d.samples).map(([name, s]) => `
    <h2>${esc(name)}</h2>
    <p class="sm-what">${esc(sampleKindWords(s.kind))} · ${esc(s.n)} records · ${s.locked ? "locked: its records are fixed" : "open: records can still be added"}${s.ranking ? ` · from the ranking by ${esc(String(s.ranking).split("__")[0])} <span class="muted">(${esc(s.ranking)})</span>` : ""}</p>
    ${s.bands ? `<table><tr><th>band</th><th>ranks</th><th>records in the band <span class="muted">(N<sub>h</sub>)</span></th><th>drawn for labelling <span class="muted">(n<sub>h</sub>)</span></th><th>weight</th></tr>${s.bands.map(b => `<tr><td>${esc(b.band)}</td><td>${esc(b.start)}–${esc(b.end ?? "end")}${b.start_pct != null ? ` (${esc(b.start_pct)}–${esc(b.end_pct)}%)` : ""}</td><td>${esc(b.N_h)}</td><td>${esc(b.n_h ?? b.n_rows)}</td><td>${b.weight ? b.weight.toFixed(2) : ""}</td></tr>`).join("")}</table>` : ""}
    <p>Labelled so far: ${labelledSoFar(s)}${s.progress.adjudicated ? ` · ${esc(s.progress.adjudicated)} adjudicated` : ""}</p>
    ${s.reviewed_line ? `<p class="sm-reviewed">${esc(s.reviewed_line)}</p>` : ""}
    ${typeof SampleExtend !== "undefined" ? SampleExtend.correctionsHtml(name, s.corrections) : ""}
    ${typeof SampleExtend !== "undefined" ? SampleExtend.independenceHtml(name, s) : ""}
    ${typeof SampleExtend !== "undefined" ? SampleExtend.criteriaHtml(s.criteria) : ""}
    ${typeof SampleExtend !== "undefined" ? SampleExtend.criteriaChangeHtml(name, s.criteria_change, s.revisions) : ""}
    ${typeof SampleExtend !== "undefined" ? SampleExtend.cardHtml(name, s) : ""}${typeof SampleExtend !== "undefined" ? SampleExtend.bandsHtml(name, s) : ""}`).join("") || "<p class='muted'>no samples yet</p>") +
    // the band note, said once under all samples (it holds for every banded sample)
    (Object.values(d.samples).some(s => s.bands) ? `<p class="sm-band-note">Ranks count every ranked record. N<sub>h</sub> leaves out seed papers, records with a missing or thin abstract and records already in another sample (such as the development sample), which are never drawn, so a band can hold fewer records than its ranks.</p>` : "");
  if ($("#sm-fresh") && typeof SampleExtend !== "undefined") $("#sm-fresh").innerHTML = SampleExtend.freshHtml(d.samples);
  smShown = d.samples;
  $("#ag-lock").disabled = !fillSelect("#ag-sample", Object.keys(d.samples), "no samples yet", "#ag-run");
  agNames();
  lockButtonText();
  if (typeof window !== "undefined" && window.lbSampleOptions) window.lbSampleOptions(d.samples);   // records.js
  if (typeof window !== "undefined" && window.Blinding) Blinding.load(d.samples);   // card "Review packages"
  try { const c = await api("/api/codebook"); lbCodebook = c.codebook; lbCodebookVersion = c.row ? c.row.version : null; } catch (e) { lbCodebook = null; lbCodebookVersion = null; }
  if (typeof window !== "undefined" && window.lbSigning) window.lbSigning(d.env_reviewer);
  if (typeof window !== "undefined" && window.SettingsCard) await SettingsCard.draw("#sm-settings", "samples");   // D4
  await markSampleSteps(d.samples);
}
// the one filled button of the page: the first of its four numbered steps not done yet (validation.js, D32 F6)
async function markSampleSteps(samples) {
  if (typeof Validation === "undefined") return;
  if (!samples) { try { samples = (await api("/api/samples")).samples; } catch (e) { return; } }
  let st = {};
  try { st = await api("/api/validation/status"); } catch (e) { st = {}; }
  const list = Object.values(samples || {});
  const step = Validation.nextStep({ target: st.target, planned: st.planned, drawn: list.length > 0,
                                     probability: list.some(s => s.kind === "probability") });
  Validation.markSteps(document.querySelectorAll("#page-sample [data-sm-step]"), step);
}
// the reviewers with labels in the chosen sample, offered as the agreement's names (typed names still go)
function agNames() {
  const s = smShown[$("#ag-sample").value], dl = $("#ag-names");
  if (!dl) return;
  const me = (typeof WiderName !== "undefined" && WiderName.get()) || "";
  const names = [...new Set([...Object.keys((s && s.progress && s.progress.reviewers) || {}), me].filter(Boolean))];
  dl.innerHTML = names.map(n => `<option value="${esc(n)}">`).join("");
}
// a new name (Change in the top bar) is offered at once, without reloading the page
if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("widername", () => { if (smShown) agNames(); });
// "Add boundary cases" on an unlocked purposive sample card (sample_extend.js; CLI twin: wider sample extend)
$("#sm-samples").addEventListener("click", async (ev) => {
  const add = ev.target.closest && ev.target.closest("[data-add-bands]");
  if (add && typeof SampleExtend !== "undefined") {
    const name = add.dataset.addBands, values = {};
    for (const i of document.querySelectorAll("[data-add-band]")) if (i.dataset.addSample === name) values[i.dataset.addBand] = i.value;
    add.disabled = true;
    await SampleExtend.addBands(name, values, { api, reload: async () => { await loadSamples(); } });
    return;
  }
  const b = ev.target.closest && ev.target.closest("[data-extend]");
  if (!b || typeof SampleExtend === "undefined") return;
  b.disabled = true;
  await SampleExtend.click(b.dataset.extend, { api, askConfirm, reload: async (r) => { if (r && r.stage_result) showStageResult(r.stage_result); await loadSamples(); } });
});
if ($("#sm-fresh")) $("#sm-fresh").addEventListener("click", async (ev) => {
  if (!ev.target.closest("[data-fresh]")) return;
  const used = $("#sm-fresh [data-fresh-from]").value, name = $("#sm-fresh [data-fresh-name]").value;
  $("#sm-msg").textContent = "drawing…";
  try {
    const r = await api(SampleExtend.freshUrl(used, name), { method: "POST" });
    const s = Object.values(r.samples)[0] || {};
    $("#sm-msg").textContent = (s.messages || []).join(" "); showStageResult(r.stage_result); await loadSamples();
  } catch (e) { $("#sm-msg").textContent = ""; showStageError("sample", e.message); }
});
// "Record the assessment" on the "Criteria changed after labelling" section (D11), POST /api/labels/criteria
if ($("#sm-samples")) $("#sm-samples").addEventListener("click", async (ev) => {
  const dec = ev.target.closest("[data-declare-independent]");
  if (dec) {
    const name = dec.dataset.declareIndependent;
    await SampleExtend.declareIndependent(name, smShown[name], { api, askFields, say: (t) => { $("#sm-msg").textContent = t; }, reload: loadSamples });
    return;
  }
  const btn = ev.target.closest("[data-crit-save]");
  if (!btn) return;
  const box = btn.closest("[data-criteria-change]"), picked = box.querySelector("[data-crit-scope]:checked");
  const body = SampleExtend.criteriaBody(btn.dataset.critSave, picked && picked.value, box.querySelector("[data-crit-note]").value);
  if (!body) { $("#sm-msg").textContent = "Choose whether to keep the labels or which labels to re-check."; return; }
  try {
    const r = await api("/api/labels/criteria", { method: "POST", body: JSON.stringify(body) });
    $("#sm-msg").textContent = (r.lines || []).join(" "); await loadSamples();
  } catch (e) { $("#sm-msg").textContent = String(e.message || e); }
});
$("#sm-draw").onclick = async () => { $("#sm-msg").textContent = "drawing…"; try { const r = await api("/api/samples/draw", { method: "POST" }); $("#sm-msg").textContent = Object.values(r.samples || {}).flatMap(x => x.messages || []).join(" "); /* e.g. an empty development sample */ showStageResult(r.stage_result); await loadSamples(); } catch (e) { $("#sm-msg").textContent = ""; showStageError("sample", e.message); } };
// Lock and unlock (D5; CLI twin: wider sample lock|unlock NAME [--yes]): both in History; an unlock is final
let smShown = {};
function lockButtonText() { const s = smShown[$("#ag-sample").value]; $("#ag-lock").textContent = s && s.locked ? "Unlock sample" : "Lock sample"; }
function lockDialogHtml(name) {
  return `<h3 id="choice-title">Lock ${esc(name)}</h3><p>The sample's records are fixed: it can no longer be redrawn or extended. ` +
    `Missing labels and adjudications are still accepted; changing a saved one becomes a correction that needs a reason. ` +
    `The sampling key (band, pool, rank) becomes visible. The lock is recorded in History; you can undo it there until a recall result on ${esc(name)} is seen.</p>` +
    `<div class="row"><button data-act="lock">Lock sample</button><button class="secondary" data-act="cancel">Cancel</button></div>`;
}
function unlockDialogHtml(name, warning) {
  return `<h3 id="choice-title">Unlock ${esc(name)}</h3>` + (warning ? `<p class="dlg-warning">${esc(warning)}</p>` : "") +
    `<p>The sample can be redrawn or extended again, and saved labels and adjudications change without a correction reason. The unlock is final: History, the methods draft and the replication package record it.</p>` +
    `<div class="row"><button data-act="unlock">Unlock</button><button class="secondary" data-act="cancel">Cancel</button></div>`;
}
$("#ag-sample").addEventListener("change", lockButtonText);
$("#ag-sample").addEventListener("change", () => agNames());
$("#ag-lock").onclick = async () => {
  const n = $("#ag-sample").value, s = smShown[n];
  if (!n || !s) return;
  const unlock = !!s.locked;
  if (!WiderName.get()) { $("#ag-summary").innerHTML = WiderName.needHtml("no name yet: a lock or unlock is signed with your name. "); return; }
  const c = await askChoice(unlock ? unlockDialogHtml(n, s.unlock_warning) : lockDialogHtml(n));
  if (!c) return;
  try {
    await api(`/api/samples/${encodeURIComponent(n)}/lock${unlock ? `?unlock=true${s.unlock_warning ? "&yes=true" : ""}` : ""}`, { method: "POST" });
    $("#ag-summary").textContent = `${unlock ? "unlocked" : "locked"} ${n} (in History)`;
  } catch (e) { $("#ag-summary").textContent = e.message; }
  await loadSamples();
};


// ---------------- screening page ----------------
let scPicked = null;   // the backend card picked for the next run (pickers.js llmPicker); nothing is saved
let scRows = [];       // the backend rows of the cards
let scPassPicks = null;   // {B, C}: pass B's and the judge's backend for the next run (null: same as pass A)
let scPassView = null;    // GET /api/screen/passes for the current mode and picks
// Protocol and second-screen mode: the pickers of pass B and the judge, preselected from the stage backends
// screen_b and screen_judge (design D14); the plain pass mode shows only the cards above.
async function loadPassPickers() {
  const box = $("#sc-passes"), mode = $("#sc-mode").value;
  if (mode !== "protocol" && mode !== "second") { box.hidden = true; box.innerHTML = ""; scPassView = null; return; }
  const has = (n) => n && scRows.some(r => r.name === n);
  const q = new URLSearchParams({ mode });
  if (scPicked) q.set("provider", scPicked);
  try {
    if (scPassPicks === null) {
      const d = await api(`/api/screen/passes?${q}`);
      scPassPicks = { B: d.defaults.B, C: d.defaults.C };
    }
    for (const p of ["B", "C"]) if (!has(scPassPicks[p])) scPassPicks[p] = null;
    const f = Pickers.passFields({ pass_off: {} }, scPassPicks, scPicked);
    for (const [k, v] of Object.entries(f)) if (v) q.set(k, v);
    scPassView = await api(`/api/screen/passes?${q}`);
  } catch (e) { box.hidden = false; box.innerHTML = `<p class="bad">${esc(e.message)}</p>`; scPassView = null; return; }
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.passPick : null;
  box.innerHTML = Pickers.passPickersHtml(scRows, scPassView, scPassPicks);
  box.hidden = false;
  box.querySelectorAll("[data-pass-pick]").forEach(s => s.onchange = async () => {
    scPassPicks[s.dataset.passPick] = s.value || null;
    await loadPassPickers();
    screenEstimate();
  });
  if (focused) { const s = box.querySelector(`[data-pass-pick="${focused}"]`); if (s && s.focus) s.focus(); }
}
const scPassFields = () => (scPassView ? Pickers.passFields(scPassView, scPassPicks, scPicked) : {});
function screenRunLine(row) {
  $("#sc-run-line").textContent = Pickers.runLine(row);
  $("#sc-run").disabled = !row;
}
// ---------------------------------------------------------------- Screening: where it stands (screening-page-tasks)
// The summary card replaces the stage-result panel on this page: the steps, one next step, then the round shown.
let scData = null, scShown = "";
const scInt = (n) => (n || 0).toLocaleString();
const SC_RUN_STATUS = { running: "in progress", done: "completed", done_with_errors: "completed, some records failed",
  interrupted: "stopped before the end" };
const SC_STEP_ICON = { completed: "M3.5 8.5l3 3 6-7", in_progress: "M8 2.5a5.5 5.5 0 1 0 5.5 5.5M8 5v3.2l2.2 1.4",
  needs_attention: "M8 2.5v7M8 12.2v.3", not_started: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11z" };
const screenRounds = (d) => d.runs.filter(r => r.kind).slice().reverse();   // screening rounds, newest first
// the name, then the date and the record count, so two rounds of the same name differ in a select
const roundLabel = (r) => [`${r.name}${r.test ? " (test model)" : ""}`, r.started_at ? fmtWhen(r.started_at) : "",
  r.n_items != null ? `${scInt(r.n_items)} record${r.n_items === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
function scKindBadges(r) {
  return `<span class="chip sc-kind sc-kind-${esc(r.kind)}">${esc(r.kind_label)}</span>` +
    (r.test ? ' <span class="chip sc-kind sc-kind-test">test model</span>' : "");
}
// a run refused while the development sample is not complete (ui-redesign D4, 409 dev_incomplete): the server's
// sentence in normal text, and "Continue labelling" as the main button (Samples and labelling); nothing started
function scDevGateHtml(detail) {
  return `<div class="stage-error sc-devgate" role="alert"><p>${esc(detail)}</p>
    <div class="row"><a class="btn primary" href="#sample" data-sc-devgate data-goto-queue>Continue labelling</a></div></div>`;
}
function scGoButton(page, tab, label, cls = "secondary small-btn") {
  return `<button type="button" class="${cls}" data-sc-go="${esc(page)}" data-sc-tab="${esc(tab || "")}">${esc(label)}</button>`;
}
function scRoundHtml(r) {
  if (!r) return '<p>No screening round yet. Start one on the <b>Set up and run</b> tab.</p>';
  const u = r.usage || (r.locality ? { locality: r.locality } : null);
  const cost = r.usage && r.usage.calls ? (r.usage.cost_known ? `$${(r.usage.cost_usd || 0).toFixed(r.usage.cost_usd >= 1 ? 2 : 4)}` : "not priced") : "";
  const warn = [r.mixed ? "More than one model answered in this round." : "", r.experimental || ""].filter(Boolean);
  const failed = (r.n || 0) - (r.ok || 0);
  return `<p class="sc-round-name"><b>${esc(r.name)}</b> ${scKindBadges(r)}</p>
    <dl class="sc-facts">
      <div><dt>Status</dt><dd>${esc(SC_RUN_STATUS[r.status] || r.status)}</dd></div>
      <div><dt>Records screened</dt><dd>${scInt(r.ok || 0)} of ${scInt(r.n_items || 0)}</dd></div>
      <div><dt>Failed</dt><dd${failed ? ' class="warn-text"' : ""}>${scInt(failed)}</dd></div>
      ${r.hidden ? "" : `<div><dt>Include · maybe · exclude</dt><dd>${scInt(r.inc || 0)} · ${scInt(r.may || 0)} · ${scInt(r.exc || 0)}</dd></div>`}
      ${cost ? `<div><dt>Cost</dt><dd>${esc(cost)}</dd></div>` : ""}
    </dl>
    ${r.hidden ? `<p role="note">${esc(r.hidden)}</p>` : ""}
    ${warn.map(w => `<p class="warn-text">${esc(w)}</p>`).join("")}
    ${r.unusual ? `<p class="warn-text sc-unusual" role="note">${esc(r.unusual)}</p>` : ""}
    <details class="sc-tech"><summary>Technical details</summary>
      <dl class="sc-facts sc-tech-facts">
        <div><dt>Run id</dt><dd><code>${esc(r.run_id)}</code></dd></div>
        <div><dt>Folder</dt><dd><code>${esc(r.dir || "")}</code></dd></div>
        <div><dt>Model</dt><dd>${esc(r.provider)} · ${esc(r.model)} ${usageChip(u)}</dd></div>
        <div><dt>Tokens and cost</dt><dd>${r.usage ? esc(usageText(r.usage)) : "not logged"}</dd></div>
        <div><dt>Prompt</dt><dd>v${esc(r.prompt_version)}</dd></div>
        <div><dt>Started · finished</dt><dd>${esc(fmtWhen(r.started_at))} · ${esc(fmtWhen(r.finished_at) || "not finished")}</dd></div>
      </dl></details>`;
}
// the maybes step names the Screening review page: a link to the review list when records wait there (manual-review D12)
function scStepText(s, pend) {
  const t = esc(s.text), page = "Screening review page";
  return s.key === "maybes" && pend && t.includes(page) ? t.replace(page, `<a href="${esc(pend.href)}">${page}</a>`) : t;
}
function renderScreenOverview(d) {
  const box = $("#sc-overview"); if (!box) return;
  const prog = d.progress || { steps: [], next: null, also: [] }, rounds = screenRounds(d);
  if (!rounds.some(r => r.run_id === scShown)) scShown = "";
  const shown = rounds.find(r => r.run_id === scShown) || rounds[0];
  const pend = prog.pending_review && prog.pending_review.line ? prog.pending_review : null;   // manual-review D12
  const steps = prog.steps.map(s => `<li class="sc-step sc-st-${esc(s.state)}${s.required ? "" : " sc-optional"}">
      <svg class="st-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="${SC_STEP_ICON[s.state] || ""}"/></svg>
      <span class="sc-step-word">${esc(s.word)}</span>
      <span class="sc-step-body"><b>${esc(s.title)}</b> <span>${scStepText(s, pend)}</span></span></li>`).join("");
  const n = prog.next;
  const next = n ? `<div class="sc-next"><h3>Next step</h3><p>${esc(n.text)}</p>
      <div class="row">${scGoButton(n.page, n.tab, n.label, "sc-next-btn main-act")}
      ${prog.required_done ? "" : `<a href="#recall" class="quiet-link" data-sc-go="recall">Go to Recall and report anyway</a>`}</div>
      ${(prog.also || []).map(a => `<p class="sc-also">You may also: ${esc(a)}</p>`).join("")}</div>` : "";
  const opts = rounds.map((r, i) => `<option value="${esc(r.run_id)}"${r === shown ? " selected" : ""}>${i === 0 ? "Latest: " : ""}${esc(roundLabel(r))}</option>`).join("");
  box.innerHTML = `<h2 id="sc-overview-h">Where screening stands</h2>
    <ol class="sc-steps">${steps}</ol>${pend ? `<p class="sc-pending"><a href="${esc(pend.href)}">${esc(pend.line)}</a></p>` : ""}${next}
    <div class="sc-round"><h3>Round shown</h3>
      ${rounds.length ? `<label class="inline">Round <select id="sc-round-select" aria-label="Screening round shown">${opts}</select></label>` : ""}
      <div id="sc-round-body">${scRoundHtml(shown)}</div></div>`;
  const sel = $("#sc-round-select");
  if (sel) sel.onchange = () => { scShown = sel.value; $("#sc-round-body").innerHTML = scRoundHtml(rounds.find(r => r.run_id === sel.value)); };
}
function renderScreenHistory(d) {
  const rounds = screenRounds(d);
  $("#sc-history-count").textContent = rounds.length ? `(${rounds.length} round${rounds.length === 1 ? "" : "s"})` : "(none yet)";
  if (!scHistoryTouched) $("#sc-rounds-card").open = rounds.length > 0 && rounds.length <= 5;
  $("#sc-runs").innerHTML = `<tr><th>Round</th><th>Status</th><th>Records</th><th>Include · maybe · exclude</th><th>Cost</th><th><span class="sr-only">Show</span></th></tr>` +
    rounds.map(r => `<tr><td>${esc(r.name)} ${scKindBadges(r)}${Backends.mixedBadge(r.mixed)}${Backends.expBadge(r.experimental)}</td><td>${esc(SC_RUN_STATUS[r.status] || r.status)}</td>
      <td>${scInt(r.ok || 0)} of ${scInt(r.n_items || 0)}</td><td>${r.hidden ? "hidden until the development sample is complete" : `${scInt(r.inc || 0)} · ${scInt(r.may || 0)} · ${scInt(r.exc || 0)}`}</td>
      <td class="small">${r.usage && r.usage.calls ? esc(r.usage.cost_known ? "$" + (r.usage.cost_usd || 0).toFixed(r.usage.cost_usd >= 1 ? 2 : 4) : "not priced") : ""}</td>
      <td><button type="button" class="secondary small-btn" data-sc-show="${esc(r.run_id)}" aria-label="Show ${esc(r.name)} in the summary">Show</button></td></tr>`).join("") ||
    "<tr><td colspan='6'>No screening round yet.</td></tr>";
}
let scHistoryTouched = false;
$("#sc-rounds-card").querySelector("summary").addEventListener("click", () => { scHistoryTouched = true; });
$("#sc-runs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sc-show]"); if (!b || !scData) return;
  scShown = b.dataset.scShow; renderScreenOverview(scData);
  $("#sc-overview").scrollIntoView({ block: "start" }); const sel = $("#sc-round-select"); if (sel) sel.focus();
});
$("#sc-overview").addEventListener("click", (e) => {
  const b = e.target.closest("[data-sc-go]"); if (!b) return;
  e.preventDefault();
  const page = b.dataset.scGo, tab = b.dataset.scTab;
  if (page === "screen") { const bar = $("#page-screen .page-tabs"); if (bar && bar.pick) bar.pick(tab || "setup", true); return; }
  location.hash = page;
});

async function loadScreen() {
  const d = await api("/api/screen");
  // the page reloads after every run: the prompt and the records chosen stay chosen (a reset would screen other records)
  const had = { prompt: $("#sc-prompt").value, scope: $("#sc-scope").value };
  $("#sc-prompt").innerHTML = d.prompts.map(p => `<option value="${esc(p.version)}">prompt v${esc(p.version)} (${esc(p.status)})</option>`).reverse().join("") || "<option value=''>no prompt yet</option>";
  // the cards at the top: the screen stage backend, else the default, is picked first; a card picked before a reload stays
  const pick = await Pickers.llmPicker($("#sc-models"), { stage: "screen", keep: scPicked,
    onPick: async (row) => { scPicked = row && row.name; screenRunLine(row); await loadPassPickers(); screenEstimate(); } });
  scPicked = pick.chosen; scRows = pick.rows; screenRunLine(pick.row);
  await loadPassPickers();
  $("#sc-scope").innerHTML = d.samples.map(s => `<option value="sample:${esc(s.name)}">sample ${esc(s.name)} (${esc(s.n)})</option>`).join("") + `<option value="corpus">whole corpus</option>` + d.protocols.map(p => `<option value="maybe:${esc(p.protocol)}">maybes of ${esc(p.protocol)} (${esc(p.maybe)})</option>`).join("");
  for (const [sel, v] of [["#sc-prompt", had.prompt], ["#sc-scope", had.scope]]) if (v && [...$(sel).options].some(o => o.value === v)) $(sel).value = v;
  scData = d; renderScreenOverview(d); renderScreenHistory(d);
  // a development-gate refusal is about the moment of the Run press: opening the page again clears it (D10)
  const gate = $("#sc-refusal");
  if (gate && gate.querySelector(".sc-devgate")) { gate.hidden = true; gate.innerHTML = ""; if ($("#sc-msg").textContent === "Nothing started.") $("#sc-msg").textContent = ""; }
  $("#sc-protocols").innerHTML = `<tr><th>protocol</th><th>n</th><th>include</th><th>maybe</th><th>exclude</th></tr>` + d.protocols.map(p => `<tr><td>${esc(p.protocol)}${p.sets > 1 ? ` <span class="muted small">set ${esc(p.result_set)} of ${esc(p.sets)}</span>` : ""}${Backends.mixedBadge(p.mixed)}</td><td>${esc(p.n)}</td><td>${esc(p.include)}</td><td>${esc(p.maybe)}</td><td>${esc(p.exclude)}</td></tr>${p.unusual ? `<tr><td colspan="5"><p class="warn-text sc-unusual" role="note">${esc(p.unusual)}</p></td></tr>` : ""}`).join("");
  const runIds = screenRounds(d).map(r => ({ value: r.run_id, label: roundLabel(r) }));   // newest first, in words
  fillSelect("#ev-run", runIds, "no runs yet", "#ev-run-btn"); fillSelect("#in-run", runIds, "no runs yet", "#in-btn");
  // optional: without a purposive sample the evaluation uses the server's default, and sample is left out
  fillSelect("#ev-sample", d.samples.filter(s => s.kind === "purposive").map(s => s.name), "no development sample yet");
  const protos = d.protocols.map(p => p.protocol);
  fillSelect("#mu-protocol", protos, "no protocol decisions yet", "#sc-export");
  fillSelect("#ss-protocol", protos, "no protocol decisions yet");
  await Promise.all([loadModelsCard(), loadSetsCard(), loadFromSets(),
    cmpCard.load({ prompts: d.prompts }).catch(e => { $("#cmp-gate").innerHTML = `<p class="bad">${esc(e.message)}</p>`; })]);
  screenEstimate();
}
// The screening run's cost range before Run, refreshed when a choice of the run form changes.
let scEstimateTimer = null;
function screenEstimate() {
  const lim = $("#sc-limit").value.trim(), prov = scPicked, scope = $("#sc-scope").value;
  if (!prov || !scope || (lim && !/^\d+$/.test(lim))) { $("#sc-estimate").textContent = ""; return; }
  const body = { stage: "screen", mode: $("#sc-mode").value, scope, protocol: $("#sc-protocol").value.trim() || "main",
    provider: prov, prompt_version: +$("#sc-prompt").value || null, limit: lim === "" ? null : +lim, ...scPassFields() };
  showEstimate("#sc-estimate", body);
}
for (const id of ["#sc-scope", "#sc-mode", "#sc-prompt", "#sc-from-set"]) $(id).addEventListener("change", screenEstimate);
for (const id of ["#sc-limit", "#sc-protocol"]) $(id).addEventListener("input", () => { clearTimeout(scEstimateTimer); scEstimateTimer = setTimeout(screenEstimate, 400); });
// Enter in the limit updates the estimate and moves to Run; a run costs time and money, so only Enter on Run starts it
$("#sc-limit").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
  e.preventDefault(); clearTimeout(scEstimateTimer); screenEstimate(); $("#sc-run").focus();
});

// "Models used": the models summary of a protocol's set (default the active set; choosing one never changes it)
async function loadModelsCard() {
  const proto = $("#mu-protocol").value;
  if (!proto) { $("#mu-out").innerHTML = '<p class="muted small">No protocol decisions yet.</p>'; $("#mu-set").disabled = true; return; }
  try {
    const sets = (await api(`/api/screen/sets?protocol=${encodeURIComponent(proto)}`)).sets;
    const n = resultSetSelect("#mu-set", proto, sets);
    const s = await api(`/api/screen/models?protocol=${encodeURIComponent(proto)}${n ? "&result_set=" + n : ""}`);
    $("#mu-out").innerHTML = Backends.modelsCardHtml(s); $("#mu-msg").textContent = "";
  } catch (e) { $("#mu-out").innerHTML = ""; $("#mu-msg").textContent = e.message; }
}
$("#mu-protocol").onchange = loadModelsCard;
$("#mu-set").onchange = loadModelsCard;
$("#sc-export").onclick = async () => {
  const proto = $("#mu-protocol").value, n = $("#mu-set").value;
  if (!proto) return;
  const b = $("#sc-export"); b.disabled = true; $("#sc-export-msg").textContent = "writing…";
  try {
    const r = await api(`/api/screen/export?protocol=${encodeURIComponent(proto)}${n ? "&result_set=" + encodeURIComponent(n) : ""}`, { method: "POST" });
    const link = (p) => `<a href="/api/outputs/file?path=${encodeURIComponent(p)}" download>${esc(p.split("/").pop())}</a>`;
    $("#sc-export-msg").innerHTML = `${esc(r.n_decisions)} decisions, ${esc(r.n_evaluations)} evaluations: ${link(r.decisions)} · ${link(r.evaluations)}${r.role_note ? ` <span class="muted">(${esc(r.role_note)})</span>` : ""}`;
  } catch (e) { $("#sc-export-msg").textContent = e.message; }
  finally { b.disabled = false; }
};

// "Result sets": every set of a protocol, with "Use for analysis", "Rename", compare and "Screen again as a new set"
let setsShown = [];
async function loadSetsCard() {
  const proto = $("#ss-protocol").value;
  $("#ss-new").disabled = $("#ss-compare").disabled = !proto;
  if (!proto) { $("#ss-table").innerHTML = '<p class="muted small">No protocol decisions yet.</p>'; setsShown = []; return; }
  try { setsShown = (await api(`/api/screen/sets?protocol=${encodeURIComponent(proto)}`)).sets; }
  catch (e) { $("#ss-msg").textContent = e.message; return; }
  $("#ss-table").innerHTML = Backends.setsCardHtml(proto, setsShown);
  const opts = setsShown.map(s => ({ value: String(s.result_set), label: `set ${s.result_set}${s.label ? " · " + s.label : ""}` }));
  fillSelect("#ss-a", opts, "no set"); fillSelect("#ss-b", opts, "no set");
  if (opts.length > 1 && $("#ss-a").value === $("#ss-b").value) $("#ss-b").value = opts[opts.length - 1].value === $("#ss-a").value ? opts[0].value : opts[opts.length - 1].value;
  $("#ss-compare").disabled = opts.length < 2;
}
$("#ss-protocol").onchange = () => { $("#ss-compare-out").innerHTML = ""; loadSetsCard(); };
$("#ss-table").onclick = async (ev) => {
  const b = ev.target.closest("[data-set-act]"); if (!b) return;
  const proto = $("#ss-protocol").value, n = b.dataset.n;
  const url = `/api/screen/sets/${encodeURIComponent(proto)}/${encodeURIComponent(n)}`;
  try {
    if (b.dataset.setAct === "use") {
      try { await api(`${url}/use`, { method: "POST" }); }
      catch (e) {
        if (e.status !== 400 || !/--with-source/.test(e.message)) throw e;
        const pick = await askChoice(Backends.useRefusalHtml(e.message.replace(/, or add --with-source to switch both/, "; or use both")));
        if (!pick || pick.act !== "both") return;   // askChoice resolves { act, fields }, null on Cancel
        await api(`${url}/use?with_source=true`, { method: "POST" });
      }
      $("#ss-msg").textContent = `${proto} uses set ${n} for analysis (undo in History)`;
    } else {
      const cur = (setsShown.find(s => String(s.result_set) === String(n)) || {}).label || "";
      const f = await askFields(`Label of ${proto} set ${n}`, [{ key: "label", label: "Label (empty removes it)", value: cur }], "Save label");
      if (!f) return;
      await api(url, { method: "PATCH", body: JSON.stringify({ label: f.label }) });
      $("#ss-msg").textContent = `${proto} set ${n} relabelled`;
    }
    await loadScreen();
  } catch (e) { $("#ss-msg").textContent = e.message; }
};
$("#ss-compare").onclick = async () => {
  const proto = $("#ss-protocol").value, a = $("#ss-a").value, b = $("#ss-b").value;
  if (!proto || !a || !b) return;
  if (a === b) { $("#ss-compare-out").innerHTML = '<p class="muted small">Choose two different sets.</p>'; return; }
  try {
    const c = await api(`/api/screen/sets/${encodeURIComponent(proto)}/compare?a=${a}&b=${b}${$("#ss-models-only").checked ? "&models_only=true" : ""}`);
    $("#ss-compare-out").innerHTML = Backends.compareHtml(c);
  } catch (e) { $("#ss-compare-out").innerHTML = `<p class="bad">${esc(e.message)}</p>`; }
};
// screen the chosen protocol again with the form's prompt, backend and records; the server says what a new set would
// reuse and screen, and the new-set dialog asks before anything runs
$("#ss-new").onclick = () => {
  const proto = $("#ss-protocol").value; if (!proto) return;
  $("#sc-mode").value = "protocol"; $("#sc-protocol").value = proto; $("#sc-mode").onchange();
  const body = screenBody(); if (body) startScreen(body);
};

// "Plan the validation sample" and "Recall target and rule" (validation.js)
const valCard = typeof Validation !== "undefined" ? Validation.controller({ table: $("#val-table"), msg: $("#val-msg"),
  depth: $("#val-depth"), targetMsg: $("#val-target-msg") }, { api, author: () => WiderName.get() || null,
  afterStep: () => markSampleSteps() }) : null;
if (valCard) {
  const valField = (ev) => { const t = ev.target;
    if (t.dataset.valField) valCard.set(t.dataset.valField, t.value);
    else if (t.dataset.valTarget) valCard.setTarget(t.dataset.valTarget, t.value); };
  $("#val-card").addEventListener("input", valField);
  $("#val-card").addEventListener("change", valField);
  $("#val-card").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-val-act],[data-val-apply]"); if (!b) return;
    if (b.dataset.valApply) valCard.use(b.dataset.valApply);
    else if (b.dataset.valAct === "plan") valCard.plan();
    else if (b.dataset.valAct === "target") valCard.saveTarget();
  });
}

// "Compare configurations" (compare.js): the controller keeps the form across page reloads; every field reports to it,
// and the estimate it holds stops matching as soon as a field changes, which disables Run until the next estimate
const cmpCard = typeof Compare !== "undefined" ? Compare.controller({ form: $("#cmp-form"), gate: $("#cmp-gate"), results: $("#cmp-results") },
  { api, startJob: (path, opts, onDone) => Jobs.start(path, opts, onDone) })
  : { load: async () => {}, set() {}, setRow() {}, addRow() {}, removeRow() {}, estimate: async () => {}, run() {} };
function cmpField(ev) {
  const t = ev.target;
  if (t.dataset.cmpRowField) cmpCard.setRow(+t.dataset.i, t.dataset.cmpRowField, t.value);
  else if (t.dataset.cmpField) cmpCard.set(t.dataset.cmpField, t.type === "checkbox" ? t.checked : t.value);
}
$("#cmp-card").addEventListener("change", cmpField);
$("#cmp-card").addEventListener("input", (ev) => { if (ev.target.tagName === "INPUT" && ev.target.type !== "checkbox") cmpField(ev); });
$("#cmp-card").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-cmp-act]"); if (!b || b.disabled) return;
  const act = b.dataset.cmpAct;
  if (act === "add") { cmpCard.addRow(); const rows = document.querySelectorAll("#cmp-form [data-cmp-row-field=provider]"); if (rows.length) rows[rows.length - 1].focus(); }
  else if (act === "remove") { cmpCard.removeRow(+b.dataset.i); const f = document.querySelector("#cmp-form [data-cmp-act=add]"); if (f) f.focus(); }
  else if (act === "estimate") cmpCard.estimate().then(() => { const r = document.querySelector("#cmp-gate [data-cmp-act=run]"); if (r && !r.disabled) r.focus(); });
  else if (act === "run") cmpCard.run();
});

// "Maybes from": the set of the protocol whose maybes a second screen reads (default its active set)
async function loadFromSets() {
  const second = $("#sc-mode").value === "second";
  $("#sc-from-row").hidden = !second;
  if (!second) return;
  const proto = $("#sc-protocol").value.trim();
  try { resultSetSelect("#sc-from-set", proto, (await api(`/api/screen/sets?protocol=${encodeURIComponent(proto)}`)).sets); }
  catch (e) { resultSetSelect("#sc-from-set", proto, []); }
}
$("#sc-mode").onchange = () => Promise.all([loadFromSets(), loadPassPickers()]).then(screenEstimate);
$("#sc-protocol").onchange = loadFromSets;

$("#sc-build").onclick = async () => { try { const r = await api("/api/screen/build-prompt", { method: "POST" }); $("#sc-msg").textContent = `prompt v${r.version} built`; await loadScreen(); } catch (e) { $("#sc-msg").textContent = e.message; } };
// the form as a ScreenRun body (null, with the error shown, for a limit that is not a whole number)
function screenBody() {
  // empty limit = every record; anything else is sent as typed text, so 0 or -5 is refused by the server instead of becoming
  // "no limit", and a 23-digit number is quoted as typed instead of as 1e+23
  const lim = $("#sc-limit").value.trim();
  if (lim && !/^-?\d+$/.test(lim)) { $("#sc-msg").textContent = ""; showStageError("screen", `the record limit must be a whole number, e.g. 20 (got "${lim}")`); return null; }
  const body = { scope: $("#sc-scope").value, pass_name: $("#sc-pass").value, provider: scPicked, prompt_version: +$("#sc-prompt").value || null, limit: lim === "" ? null : lim, mode: $("#sc-mode").value, protocol: $("#sc-protocol").value };
  if (body.mode === "protocol" || body.mode === "second") Object.assign(body, scPassFields());
  if (body.mode === "second" && $("#sc-from-set").value) body.from_set = +$("#sc-from-set").value;
  return body;
}
// Start a screening job. A refusal (409) starts nothing: a changed backend or a finished set with other settings opens a
// dialog, and the choice made there is sent again; Cancel leaves everything as it was.
async function startScreen(body) {
  const btn = $("#sc-run"); btn.disabled = true; $("#sc-msg").textContent = "";
  const refusal = $("#sc-refusal");   // a refused run says why beside Run (the page has no screen result box)
  const refuse = (html) => { if (refusal) { refusal.hidden = !html; refusal.innerHTML = html; } };
  refuse("");
  try {
    for (;;) {
      try {
        // when the run finishes the page redraws; the focus stays on (or comes back to) the run button (D2)
        const r = await Jobs.start("/api/jobs/screen", { body: JSON.stringify(body) }, async () => { await loadScreen(); focusAfter(btn); });
        $("#sc-msg").textContent = r && r.already_done ? r.message
          : "Running in the background: progress, time left and cost are in the bar at the top. You can leave this page.";
        return;
      } catch (e) {
        const rb = e.status === 409 ? e.body || {} : {};
        let pick = null;
        if (rb.backend_change) {
          pick = await askChoice(Backends.backendChangeHtml(rb.backend_change));
          if (pick) { body = { ...body, backend_change: pick.act }; continue; }
        } else if (rb.new_set_needed) {
          const ns = rb.new_set_needed;
          pick = await askChoice(Backends.newSetHtml(ns, rb.detail));
          if (pick) {
            body = { ...body, new_set: true, set_label: pick.fields.set_label || null,
                     ...(pick.act === "reuse" ? { reuse_from: ns.reusable.set, no_reuse: false } : { reuse_from: null, no_reuse: true }) };
            continue;
          }
        } else if (rb.dev_incomplete) {
          refuse(scDevGateHtml(rb.detail));
          const go = refusal && refusal.querySelector("[data-goto-queue]"); if (go && go.focus) go.focus();   // not the page top
          $("#sc-msg").textContent = "Nothing started.";
          return;
        } else throw e;   // scope_needed and every other refusal: the server's message
        $("#sc-msg").textContent = "Nothing started.";
        return;
      }
    }
  } catch (e) { refuse(`<div class="stage-error" role="alert">${esc(e.message)}</div>`); }
  finally { btn.disabled = !scPicked; }
}
$("#sc-run").onclick = () => {
  if (!scPicked) return;
  const body = screenBody(); if (body) startScreen(body);
};

// ---------------- recall + report page ----------------
async function loadRecall() {
  const d = await api("/api/screen"), s = await api("/api/samples");
  const anyProto = fillSelect("#rc-protocol", d.protocols.map(p => p.protocol), "no protocol decisions yet");
  await loadRecallSets();
  // the sample in use for recall first (protocol.sampling.probability_sample, D9)
  const anySample = fillSelect("#rc-sample", Object.entries(s.samples).filter(([k, v]) => v.kind === "probability").sort(([, a], [, b]) => (b.in_use ? 1 : 0) - (a.in_use ? 1 : 0)).map(([k]) => k), "no probability sample yet");
  $("#rc-run").disabled = !(anyProto && anySample);  // the report and bundle leave an empty choice out (server defaults)
  if (window.Researchers) Researchers.load();   // card "Names in shared files" and the mode lines
}
// the protocol's result sets: "set" defaults to the active one (choosing another never changes it); "compare with" is optional
async function loadRecallSets() {
  const proto = $("#rc-protocol").value;
  let sets = [];
  if (proto) { try { sets = (await api(`/api/screen/sets?protocol=${encodeURIComponent(proto)}`)).sets; } catch (e) { sets = []; } }
  resultSetSelect("#rc-set", proto, sets);
  const keep = $("#rc-compare").value;
  $("#rc-compare").innerHTML = `<option value="">no comparison</option>` + resultSetOptions(proto, sets).map(o => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  if ([...$("#rc-compare").options].some(o => o.value === keep)) $("#rc-compare").value = keep;
  $("#rc-set").disabled = $("#rc-compare").disabled = sets.length < 1;
}
$("#rc-protocol").onchange = loadRecallSets;
// the recall of the same sample under another set of the protocol, beside the chosen set's
// the interval cell: "0.43–1.00", "could not be reliably estimated", or "–" without a recall or for an answer from
// before the interval was added (the demo's recorded snapshot), which never shows its bootstrap point as the interval
function recallIntervalCell(v) {
  if (!v || !("interval" in v) || v.recall == null) return "–";
  if (v.interval_method === "census") return "exact (census)";   // every record labelled: no sampling interval
  return v.interval ? `${v.interval[0].toFixed(2)}–${v.interval[1].toFixed(2)}${RC_METHOD[v.interval_method] ? ` (${RC_METHOD[v.interval_method]})` : ""}` : "could not be reliably estimated";
}
// the method of each interval named beside it (inspection 1 Nov, item 13): the table's design-based interval and the
// Validation checks' exact any-look bounds are two different intervals; the names are uncertainty.METHOD_NAMES'
const RC_METHOD = { korn_graubard: "Korn–Graubard", kish: "Kish effective size", clopper_pearson: "Clopper–Pearson" };
const rcLine = (l, v) => (/^\d+(\.\d+)?% interval: /.test(l) && v && RC_METHOD[v.interval_method]
  ? l.replace(/\.$/, ` (design-based, ${RC_METHOD[v.interval_method]} method).`) : l);
function recallCompareHtml(r) {
  const c = r.compare; if (!c) return "";
  const f = (x) => (x == null ? "-" : x.toFixed(3));
  const lv = `${+((r.ci_level ?? 0.95) * 100).toFixed(1)}%`;
  return `<h3>Set ${esc(r.result_set)} against set ${esc(c.result_set)}</h3><table><tr><th>variant</th><th>recall, set ${esc(r.result_set)}</th><th>recall, set ${esc(c.result_set)}</th><th>${lv} interval, set ${esc(r.result_set)}</th><th>${lv} interval, set ${esc(c.result_set)}</th><th>missed, set ${esc(r.result_set)}</th><th>missed, set ${esc(c.result_set)}</th></tr>` +
    Object.entries(r.variants).map(([k, v]) => { const o = c.variants[k] || {}; return `<tr${k === r.primary_variant ? ' class="rc-main"' : ""}><td>${esc(k)}</td><td>${f(v.recall)}</td><td>${f(o.recall)}</td><td>${recallIntervalCell(v)}</td><td>${recallIntervalCell(o)}</td><td>${esc(v.n_missed)}</td><td>${esc(o.n_missed ?? "-")}</td></tr>`; }).join("") + "</table>";
}
// the answer first, in a box: the primary variant's recall, then its plain sentences (count, interval or why there is
// none, notes, open reviewer disagreements; normal body text, not small); below, one row per way of counting, the
// primary one marked, then the one method sentence the CLI and the report's "Recall: method" also say (small); the
// per-variant method and the bootstrap and linearised cross-checks are in the report only
function recallTableHtml(r) {
  const lv = `${+((r.ci_level ?? 0.95) * 100).toFixed(1)}%`;
  const iv = recallIntervalCell;
  const apart = new Set([...(r.validation_lines || []), ...((r.benchmark_apart || {}).lines || [])]);
  const pv = r.primary_variant, p = pv && r.variants[pv];
  const figure = p && p.recall != null ? `<p class="rc-figure">Recall (${esc(pv)}): ${(p.recall * 100).toFixed(1)}%</p>` : "";
  return `<div class="rc-answer">${figure}${(r.lines || []).filter(l => !apart.has(l)).map(l => `<p>${esc(rcLine(l, p))}</p>`).join("")}</div>` +
    `<h3>Three ways of counting</h3><p class="section-intro"><b>Strict</b>: a relevant paper counts as kept only when screening included it. <b>Lenient</b>: a maybe also counts as kept. <b>Cautious</b>: as lenient, and the papers you marked maybe also count as relevant.${p ? ` The ${esc(pv)} row is the main estimate (set in the protocol).` : ""}</p>` +
    `<table><tr><th>variant</th><th>relevant</th><th>kept</th><th>recall</th><th>${lv} interval (design-based)</th><th>relevant papers</th><th>missed</th></tr>` +
    Object.entries(r.variants).map(([k, v]) => `<tr${k === pv ? ' class="rc-main"' : ""}><td>${esc(k)}${k === pv ? " (main)" : ""}</td><td>${esc(v.relevant.join("+"))}</td><td>${esc(v.kept.join("+"))}</td><td><b>${v.recall == null ? "-" : v.recall.toFixed(3)}</b></td><td>${iv(v)}</td><td>${esc(v.n_relevant)}</td><td>${esc(v.n_missed)}</td></tr>`).join("") + "</table>" +
    (r.method_note ? `<p class="small">${esc(r.method_note)}</p>` : "");
}
// the Recall page's sections below the tables (D10): "Validation checks" (the outcome block, its not-met and
// cannot-tell lines in the warning colour, never grey) and "Seed papers apart" (D13); empty without lines
function validationSectionHtml(r) {
  const ls = (r && r.validation_lines) || [];
  if (!ls.length) return "";
  const warn = (l) => /^Target \S+ not met:/.test(l);   // inconclusive is drawn in black: it is not evidence of a miss
  return `<h3>Validation checks</h3>` + ls.map(l => warn(l) ? `<p class="warn-line">${esc(l)}</p>` : `<p>${esc(l)}</p>`).join("") +
    outcomeActionsHtml(r);
}
function benchmarkSectionHtml(r) {
  const ls = ((r && r.benchmark_apart) || {}).lines || [];
  return ls.length ? `<h3>Seed papers apart</h3>` + ls.map(l => `<p>${esc(l)}</p>`).join("") : "";
}
// the outcome block at a look on a probability sample (D16): the two ordinary buttons, neither pre-selected, since
// stopping and continuing are both allowed; the requests they send (CLI twins: wider validation stop / extend --suggested)
function outcomeActionsHtml(r) {
  // a stopped validation offers neither; records still to label come before adding more
  if (!r || !r.outcome || r.validation_stopped) return "";
  return `<div class="row" id="rc-outcome-actions"><button class="secondary" data-rc-act="stop">Stop validation here</button>` +
    (r.validation_unlabelled ? "" : `<button class="secondary" data-rc-act="extend">Add the suggested records</button>`) + `</div>`;
}
function stopDialogHtml() {
  return `<h3 id="choice-title">Stop validation here</h3><p>The decision is recorded in History and cannot be undone. ` +
    `Adding records later is still allowed, and every output then says so.</p>` +
    `<label>Note (optional) <input data-field="note"></label>` +
    `<div class="row"><button data-act="stop">Stop validation here</button><button class="secondary" data-act="cancel">Cancel</button></div>`;
}
function outcomeRequest(act, sample, fields) {
  return act === "stop"
    ? ["/api/validation/stop", { method: "POST", body: JSON.stringify({ sample, note: (fields && fields.note) || null }) }]
    : ["/api/validation/extend", { method: "POST", body: JSON.stringify({ sample, suggested: true }) }];
}
// the choice of D8 after a configuration changed once recall was seen: the notice in normal text, the two choices and
// Cancel (focused, so a stray Enter does nothing); resolves "used", "not_informed" or null. Nothing is pre-selected.
function sampleChoiceHtml(notice) {
  return `<h3 id="choice-title">This set was made after recall on the sample was seen</h3><p>${esc(notice)}</p>` +
    `<div class="row"><button data-act="used">Sample used</button><button data-act="not_informed">Not informed</button>` +
    `<button class="secondary" data-act="cancel">Cancel</button></div>`;
}
async function askSampleChoice(notice) { const r = await askChoice(sampleChoiceHtml(notice)); return r ? r.act : null; }
// label-completion-and-sealing D4: recall is revealed only once every sampled record has a final label; the notice says
// what is left, in the message line as normal black text (not muted), and the tables are cleared. True when refused.
function recallRefused(r, msg, panes) {
  msg.className = r.labels_incomplete ? "" : "muted small";
  if (!r.labels_incomplete) return false;
  msg.innerHTML = esc(r.notice) + recallFixLinks(r.completeness || {}, r.sample); panes.forEach(x => { x.innerHTML = ""; });
  return true;
}
// where each reason of a refused estimate is cleared, on the Samples and labelling page (inspection 1 Nov 2026)
function recallFixLinks(c, sample) {
  const links = [];
  if (c.n_no_label) links.push(`<a href="#sample" data-goto-queue>Continue labelling</a>`);
  if (c.n_not_independent) links.push(`<a href="#sample" data-goto-sample="[data-declare-independent='${esc(sample || "")}']">Declare labels independent</a>`);
  if (c.n_open || c.n_few) links.push(`<a href="#sample" data-goto-sample="#ag-card">Adjudicate the disagreements</a>`);
  return links.length ? ` <span class="row rc-fix">${links.join(" ")}</span>` : "";
}
$("#rc-run").onclick = async (ev, choice) => {
  if (!$("#rc-protocol").value || !$("#rc-sample").value) return;
  $("#rc-msg").className = "muted small"; $("#rc-msg").textContent = "estimating…";
  try {
    const q = new URLSearchParams({ protocol: $("#rc-protocol").value, sample: $("#rc-sample").value });
    if ($("#rc-set").value) q.set("result_set", $("#rc-set").value);
    if ($("#rc-compare").value && $("#rc-compare").value !== $("#rc-set").value) q.set("compare", $("#rc-compare").value);
    if (choice) q.set("choice", choice);
    const r = await api(`/api/recall?${q}`);
    if (recallRefused(r, $("#rc-msg"), [$("#rc-tables"), $("#rc-validation"), $("#rc-benchmark")])) return;
    if (r.choice_needed) {
      $("#rc-msg").textContent = r.notice; $("#rc-tables").innerHTML = $("#rc-validation").innerHTML = $("#rc-benchmark").innerHTML = "";
      const c = await askSampleChoice(r.notice);
      if (c) return $("#rc-run").onclick(ev, c);
      $("#rc-msg").textContent = "Nothing was done."; return;
    }
    $("#rc-msg").textContent = ""; showStageResult(r.stage_result);
    $("#rc-tables").innerHTML = recallTableHtml(r) + recallCompareHtml(r) + `<details class="rc-bands"><summary>Per sampling band</summary><table><tr><th>band</th><th>n</th><th>weight</th><th>human include</th><th>human maybe</th><th>kept strict</th><th>kept lenient</th><th>note</th></tr>` +
      r.bands.map(b => `<tr><td>${esc(b.band)}</td><td>${esc(b.n)}</td><td>${b.weight.toFixed(2)}</td><td>${esc(b.human_include)}</td><td>${esc(b.human_maybe)}</td><td>${esc(b.kept_strict)}</td><td>${esc(b.kept_lenient)}</td><td class="small">${esc(b.note)}</td></tr>`).join("") + "</table></details>";
    $("#rc-validation").innerHTML = validationSectionHtml(r); $("#rc-benchmark").innerHTML = benchmarkSectionHtml(r);
  } catch (e) { $("#rc-msg").textContent = ""; showStageError("recall", e.message); }
};
$("#rc-validation").addEventListener("click", async (ev) => {
  const b = ev.target.closest("[data-rc-act]"); if (!b) return;
  const sample = $("#rc-sample").value;
  let fields = null;
  if (b.dataset.rcAct === "stop") { const c = await askChoice(stopDialogHtml()); if (!c) return; fields = c.fields; }
  try { const r = await api(...outcomeRequest(b.dataset.rcAct, sample, fields)); $("#rc-msg").textContent = (r.lines || []).join(" "); afterAction(); }
  catch (e) { $("#rc-msg").textContent = String(e.message || e); }
});
// protocol and sample are optional for the report and bundle: a select left at its placeholder is left out
const reportParams = (choice) => { const q = new URLSearchParams(); if (choice) q.set("choice", choice); if ($("#rc-protocol").value) q.set("protocol", $("#rc-protocol").value); if ($("#rc-sample").value) q.set("sample", $("#rc-sample").value); if ($("#rc-set").value) q.set("result_set", $("#rc-set").value); return q.toString() ? "?" + q : ""; };
// one build per press: a second press while the report is being built sends nothing (the button is disabled meanwhile, then takes the keyboard back)
// a report or bundle written with the pending section (D8) asks the choice, then builds again with it
async function reportChoice(r, again) { if (!r.choice_needed) return false; const c = await askSampleChoice(r.notice); if (c) await again(c); else $("#rp-msg").textContent = r.notice; return true; }
$("#rp-build").onclick = async (ev, choice) => { const b = $("#rp-build"); if (b.disabled) return; b.disabled = true; $("#rp-msg").textContent = "building…"; try { const r = await api(`/api/report${reportParams(choice)}`, { method: "POST" }); $("#rp-md").textContent = r.markdown; $("#rp-msg").textContent = ""; if (r.choice_needed && await reportChoice(r, (c) => { b.disabled = false; return $("#rp-build").onclick(ev, c); })) return; showStageResult(r.stage_result); afterAction(); } catch (e) { $("#rp-msg").textContent = ""; showStageError("recall", e.message); } finally { b.disabled = false; if (document.activeElement === document.body) b.focus(); } };
$("#rp-bundle").onclick = async (ev, choice) => { const b = $("#rp-bundle"); if (b.disabled) return; b.disabled = true; try { const r = await api(`/api/report/bundle${reportParams(choice)}`, { method: "POST" }); $("#rp-msg").textContent = ""; if (r.choice_needed && await reportChoice(r, (c) => { b.disabled = false; return $("#rp-bundle").onclick(ev, c); })) return; showStageResult(r.stage_result); afterAction(); } catch (e) { $("#rp-msg").textContent = ""; showStageError("recall", e.message); } finally { b.disabled = false; if (document.activeElement === document.body) b.focus(); } };
