// Shell (WP12, design-pass-a1-dedup): sidebar of steps with marks, collapse and footer, top bar, Explain buttons,
// home / help / settings pages.
// Loaded last; app.js calls renderShell() from loadStages() and markCurrentPage()/loadHome()/... from showPage().
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const qa = (s, root) => Array.from((root || document).querySelectorAll(s));
  const h = esc;
  const call = api;
  const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* private window */ } } };
  const go = (key) => { if (location.hash !== "#" + key) location.hash = key; else showPage(key); };
  // Status words (screening-page-tasks D8): completed, in progress, needs attention, not started; one function for the
  // sidebar, the page chip and the help list, so they never differ. The CLI keeps its codes (done, ready, blocked).
  const STATE_WORD = { done: "completed", ready: "not started", blocked: "not started", extension: "later" };
  const QSTAGES = new Set(["setup", "construct", "query"]);
  const running = (s) => !!(window.Jobs && Jobs.runningStages && Jobs.runningStages().has(s.key));
  // the word class: "running" while a job of the stage runs, "needs" when done with work left for you, else the state
  // ("review" when done but its inputs changed since, ui-redesign D5: Ranking only)
  // (open questions mark the Literature search stages in any state, review-plan-and-question-window D13)
  // ("test" when its results come from the test model only, D32 F3: amber, never green, before any other word)
  const stateClass = (s) => running(s) ? "running" : s.state === "done" && s.test_model ? "test"
    : s.needs_you && (s.state === "done" || QSTAGES.has(s.key)) ? "needs"
    : s.state === "done" && s.needs_review ? "review" : s.state;
  // only the next stage (the one `wider next` names) says where to work; other open stages read "not started", or
  // "in progress" when the stage already holds a run result or an artifact (`started`, /api/stages)
  const stateWord = (s) => ({ running: "in progress", needs: "needs attention", review: "needs review", test: "Test model" })[stateClass(s)]
    || (s.started ? (s.state === "ready" && s.key === NEXT_KEY ? "in progress, work here next" : "in progress")
      : s.state === "ready" && s.key === NEXT_KEY ? "not started, work here next" : (STATE_WORD[s.state] || s.state));
  // the word with its reason: "needs attention: 3 pairs to decide", "not started: needs Ranking first"
  const stateText = (s) => {
    const c = stateClass(s), w = stateWord(s);
    if (c === "test") return w + (s.needs_you ? `; needs attention: ${s.needs_you}` : "");
    if (c === "needs") return `${w}: ${s.needs_you}`;
    if (c === "review") return `${w}: ${s.needs_review}`;
    const waits = s.waits ? `${c === "blocked" && s.missing.length ? ";" : ":"} ${s.waits}` : "";
    if (c === "blocked" && s.missing.length) return `${w}: needs ${s.missing.map(stageTitle).join(", ")} first${waits}`;
    return w + waits;
  };
  // the word with what the stage waits for, no count ("not started: development labels open", ui-redesign 4.4)
  const wordWaits = (s) => stateWord(s) + (s.waits && stateClass(s) !== "running" ? `: ${s.waits}` : "");
  window.stageStatus = (s) => ({ cls: stateClass(s), word: stateWord(s), text: stateText(s) });
  // the stages with a page and a section; the later stages (full text, extraction, synthesis) are hidden
  const coreStages = () => STAGES.filter(s => s.state !== "extension" && s.section && PAGES[s.key]);

  // ------------------------------------------------------------------ status words and icons (design-pass-a1-dedup D12)
  // One map for every page: a status is never shown by colour alone, always a word and an aria-hidden icon.
  const ICON = (d) => `<svg class="st-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="${d}"/></svg>`;
  const STATUS = {
    open:    { word: "open", icon: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11z" },
    wait:    { word: "waiting for you", icon: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM8 5v3.2l2.2 1.4" },
    dup:     { word: "duplicate", icon: "M2.5 2.5h7v7h-7zM6.5 6.5h7v7h-7z" },
    notdup:  { word: "not duplicate", icon: "M1.5 4.5h5v7h-5zM9.5 4.5h5v7h-5z" },
    unsure:  { word: "unsure", icon: "M5.8 6a2.3 2.3 0 1 1 3.2 2.1c-.7.3-1 .8-1 1.5v.4M8 12.2v.3" },
    wider:   { word: "by WIDER", icon: "M8 1.8l1.6 4.6 4.6 1.6-4.6 1.6L8 14.2l-1.6-4.6L1.8 8l4.6-1.6z" },
    done:    { word: "done", icon: "M3 8.5l3.2 3L13 4.5" },
    needs:   { word: "needs you", icon: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM8 5v3.6M8 10.8v.3" },
    review:  { word: "needs review", icon: "M8 1.8L14.2 8 8 14.2 1.8 8z" },
    blocked: { word: "blocked", icon: "M8 2.5a5.5 5.5 0 1 0 0 11 5.5 5.5 0 0 0 0-11zM4.1 11.9l7.8-7.8" },
    running: { word: "running", icon: "M13.5 8A5.5 5.5 0 1 1 8 2.5M8 5v3l2 1.2" },
    test:    { word: "Test model", icon: "M6 2.5h4M7 2.5v4L3.2 12.6a.9.9 0 0 0 .8 1.4h8a.9.9 0 0 0 .8-1.4L9 6.5v-4" },
  };
  window.Status = {
    keys: Object.keys(STATUS),
    word: (key) => (STATUS[key] || { word: key }).word,
    icon: (key) => STATUS[key] ? ICON(STATUS[key].icon) : "",
    // a small tinted chip; `text` replaces the word (e.g. "Duplicate (ana)") and keeps the icon
    chip: (key, text) => `<span class="st-chip st-${h(key)}">${STATUS[key] ? ICON(STATUS[key].icon) : ""}<span class="st-word">${h(text == null ? (STATUS[key] || { word: key }).word : text)}</span></span>`,
  };

  // ------------------------------------------------------------------ sidebar
  function setCollapsed(collapsed, remember) {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    const b = q("#sidebar-toggle");
    b.setAttribute("aria-expanded", String(!collapsed));
    const label = collapsed ? "Expand the sidebar" : "Collapse the sidebar";
    b.setAttribute("aria-label", label); b.title = label;
    if (remember) store.set("wider.sidebar", collapsed ? "collapsed" : "open");
  }
  const pref = store.get("wider.sidebar");
  setCollapsed(pref ? pref === "collapsed" : window.innerWidth < 900, false);
  q("#sidebar-toggle").onclick = () => setCollapsed(!document.body.classList.contains("sidebar-collapsed"), true);
  // without a remembered choice, follow the window: icon rail below 900 px, full sidebar above
  const narrow = window.matchMedia("(max-width: 899px)");
  const follow = () => { if (!store.get("wider.sidebar")) setCollapsed(narrow.matches, false); };
  if (narrow.addEventListener) narrow.addEventListener("change", follow);
  // below 640 px the sidebar hides behind the Menu button (accessibility-and-naming D7): Enter opens it with the
  // full labels, Esc or choosing a page closes it, and the focus goes back to Menu after Esc
  const phone = window.matchMedia("(max-width: 639px)");
  const menuBtn = q("#menu-btn");
  function setMenu(open, focusBack) {
    if (!menuBtn) return;
    document.body.classList.toggle("menu-open", open);
    menuBtn.setAttribute("aria-expanded", String(open));
    // the page behind the open menu cannot be reached (inspection 1 Nov, item 16): inert, so Tab and a screen reader
    // stay in the menu and its button
    ["#main", "#guide-panel"].forEach(sel => { const el = q(sel); if (el && el.removeAttribute) { if (open) el.setAttribute("inert", ""); else el.removeAttribute("inert"); } });
    if (open) { setCollapsed(false, false); const first = q("#sidebar .nav-link"); if (first) first.focus(); }
    else { follow(); if (focusBack) menuBtn.focus(); }
  }
  if (menuBtn) {
    menuBtn.onclick = () => setMenu(!document.body.classList.contains("menu-open"), false);
    q("#sidebar").addEventListener("keydown", (e) => {
      if (e.key === "Escape" && document.body.classList.contains("menu-open")) { e.preventDefault(); e.stopPropagation(); setMenu(false, true); }
    });
    // Escape closes the menu wherever the focus is (also on the Menu button); Tab cycles through the menu and its button
    document.addEventListener("keydown", (e) => {
      if (!document.body.classList.contains("menu-open")) return;
      if (e.key === "Escape") { e.preventDefault(); setMenu(false, true); return; }
      if (e.key !== "Tab") return;
      const items = [menuBtn, ...q("#sidebar").querySelectorAll("a[href], button:not([disabled]), input, select, [tabindex]:not([tabindex='-1'])")]
        .filter(el => el === menuBtn || el.offsetParent !== null);
      if (items.length < 2) return;
      const i = items.indexOf(document.activeElement), last = items.length - 1;
      if (e.shiftKey && i <= 0) { e.preventDefault(); items[last].focus(); }
      else if (!e.shiftKey && (i === last || i < 0)) { e.preventDefault(); items[0].focus(); }
    });
    q("#sidebar").addEventListener("click", (e) => { if (phone.matches && e.target.closest && e.target.closest("a")) setMenu(false, false); });
    if (phone.addEventListener) phone.addEventListener("change", () => { if (!phone.matches) setMenu(false, false); });
  }

  // The pages that are not stages, with their commands (accessibility-and-naming 6.2, 6.5): the one list the sidebar's
  // names, Help's "Page names and commands" card and tests/unit/test_names.py (docs/names.md) read.
  const FIXED_PAGES = [
    { key: "home", title: "Home", commands: ["status", "init"] },
    { key: "overview", title: "Overview", commands: ["status", "next"] },
    { key: "model", title: "Model for query creation", commands: ["providers"] },
    { key: "review", title: "Screening review", commands: ["review"] },
    { key: "results", title: "Results dashboard", commands: ["report", "export"] },
    { key: "reporting", title: "PRISMA reporting", commands: ["report prisma"] },
    { key: "questions", title: "Questions", commands: ["questions"] },
    { key: "ask", title: "Ask WIDER", commands: ["assistant ask", "assistant history"] },
    { key: "notes", title: "Notes and tags", commands: ["notes"] },
    { key: "history", title: "History", commands: ["history"] },
    { key: "help", title: "Help", commands: ["guide", "next"] },
    { key: "settings", title: "Settings and data", commands: ["protocol", "providers", "check-backends"] },
    { key: "methods", title: "Methods", commands: ["guide methods"] },   // not in the sidebar: reached from Help and the guide
  ];
  window.FIXED_PAGES = FIXED_PAGES;
  const fixedTitle = (key) => FIXED_PAGES.find(p => p.key === key).title;
  // the fixed links written in index.html take their names from the list too
  qa("#sidebar > a.nav-link[data-key]").forEach(a => {
    const page = FIXED_PAGES.find(p => p.key === a.dataset.key); if (!page) return;
    a.title = page.title; const l = a.querySelector(".label"); if (l) l.textContent = page.title;
  });
  const scriptsLine = (commands) => commands.map(c => `<code>wider ${h(c)}</code>`).join(", ");

  // The model page is not a stage: a fixed link first in section 1, with no state dot.
  const MODEL_LINK = `<div role="listitem"><a class="nav-link" href="#model" data-key="model" title="${h(fixedTitle("model"))}">
    <svg class="icon" aria-hidden="true"><use href="#i-model"/></svg><span class="label">${h(fixedTitle("model"))}</span></a></div>`;
  // The results dashboard is not a stage either: a fixed link last in the section of Recall and report, with no
  // state word and no step number.
  const RESULTS_LINK = `<div role="listitem"><a class="nav-link" data-key="results" href="#results" title="${h(fixedTitle("results"))}">
    <svg class="icon" aria-hidden="true"><use href="#i-results"/></svg><span class="label">${h(fixedTitle("results"))}</span></a></div>`;
  // PRISMA reporting (prisma-reporting-assistant 7.2): a fixed link right after the results dashboard, likewise
  const REPORTING_LINK = `<div role="listitem"><a class="nav-link" data-key="reporting" href="#reporting" title="${h(fixedTitle("reporting"))}">
    <svg class="icon" aria-hidden="true"><use href="#i-reporting"/></svg><span class="label">${h(fixedTitle("reporting"))}</span></a></div>`;
  // The Screening review page (manual-review D1) is not a stage either: a fixed link right after "Screening", with no
  // state mark; its tooltip names how many records need a person, from GET /api/review/summary (read on each draw
  // and after a decision on the page); its aria-label keeps the name "Screening review" when the label is hidden.
  let REVIEW_TIP = "Screening review";
  const reviewTip = (pr) => !pr ? "no screening decisions yet"
    : `${pr.count} record${pr.count === 1 ? " needs" : "s need"} a person`;
  const reviewLink = () => `<div role="listitem"><a class="nav-link" data-key="review" href="#review" aria-label="${h(fixedTitle("review"))}" title="${h(REVIEW_TIP)}">
    <svg class="icon" aria-hidden="true"><use href="#i-review"/></svg><span class="label">${h(fixedTitle("review"))}</span></a></div>`;
  function refreshReviewTip() {
    return call("/api/review/summary").then(s => {
      REVIEW_TIP = reviewTip(s && s.pending_review);
      const a = q('#stage-links a[data-key="review"]'); if (a) a.title = REVIEW_TIP;
    }).catch(() => {});
  }
  if (window.Review) window.Review.onDecided = refreshReviewTip;
  // each link shows its task's own icon and no status mark (owner, 29 Oct: the journey line shows what is done,
  // current or needs attention); the status stays in the tooltip and in words for screen readers
  function stageLink(s) {
    const c = stateClass(s), word = c === "needs" || c === "review" || c === "test" ? stateText(s) : wordWaits(s);
    const needs = s.state === "blocked" && s.missing.length ? " (needs " + s.missing.map(stageTitle).join(", ") + " first)" : "";
    return `<div role="listitem"><a class="nav-link state-${h(s.state)}${s.needs_you ? " needs-you" : ""}${c === "review" ? " needs-review" : ""}${running(s) ? " is-running" : ""}" href="#${h(s.key)}" data-key="${h(s.key)}" title="${h(`${s.title}: ${word}${needs}`)}">
      <svg class="icon" aria-hidden="true"><use href="#i-${h(s.key)}"/></svg><span class="label">${h(s.short || s.title)}</span><span class="sr-only">, ${h(word)}</span></a></div>`;
  }
  // a section is done when all its stages are, current when it holds the current page (or, off the stage pages, the
  // next stage), partly done when some of its stages are done, upcoming otherwise (never "upcoming" beside a completed
  // stage, inspection 1 Nov 2026); the word is for screen readers (no mark, as on the links)
  const SEC_WORD = { done: "completed", current: "current", partial: "partly done", upcoming: "upcoming" };
  // the model step while no model is chosen: the project default still writes the proposals (inspection 1 Nov)
  // (app.js sets CONSTRUCT_DEFAULT; a page without it reads "not set yet")
  const constructDefault = () => typeof CONSTRUCT_DEFAULT === "undefined" ? null : CONSTRUCT_DEFAULT;
  const modelUnset = () => constructDefault() ? `not chosen yet (the default, ${constructDefault()}, is used)` : "not set yet";
  function sectionState(mine, key) {
    if (mine.length && mine.every(s => s.state === "done")) return "done";
    if (key === "results" || key === "reporting") key = "recall";   // these links sit in the section of Recall and report
    if (key === "review") key = "screen";    // the review link sits beside Screening
    const here = coreStages().some(s => s.key === key) ? key : NEXT_KEY;
    return mine.some(s => s.key === here) ? "current" : mine.some(s => s.state === "done") ? "partial" : "upcoming";
  }
  function markSections(key) {
    const stages = coreStages();
    SECTIONS.forEach(sec => {
      const el = q(`#nav-sec-${sec.number}`); if (!el) return;
      const st = sectionState(stages.filter(s => s.section === sec.number), key);
      el.className = `nav-section-title sec-${st}`;
      const w = q(`#nav-sec-${sec.number} .sr-only`); if (w) w.textContent = `, ${SEC_WORD[st]}`;
    });
  }
  // the footer (ui-redesign D11): what needs you and the recommended step, no "steps done" count and no bar; the
  // count links to the Overview, which lists the items (so the Overview itself shows only "Next:")
  // the D4 item of the project summary (an incomplete development sample with model decisions on it) counts as one
  // thing that needs you, as the Overview lists it; set when the summary is read
  let devGate = false;
  function renderFooter(key) {
    const el = q("#nav-foot"); if (!el) return;
    el.hidden = !PROJECT_TITLE;
    const n = coreStages().filter(s => stateClass(s) === "needs" || (s.state === "done" && (s.needs_review || s.needs_you))).length + (devGate ? 1 : 0);
    const rec = recommended(), rs = coreStages().find(s => s.key === rec);
    // what the next step waits on, in words only ("development labels open"), never a count of records
    const why = rs && (rs.needs_you && rs.state === "done" ? rs.needs_you : stateClass(rs) === "needs" ? rs.needs_you : rs.waits);
    const needs = key === "overview" ? ""
      : n ? `<a class="nav-foot-needs" href="#overview" title="The list is on the Overview">${Status.icon("needs")}<span class="label">${n} ${n === 1 ? "thing needs" : "things need"} you<small>See the list on the Overview</small></span></a>`
        : '<p class="label">Nothing waits for you.</p>';
    el.innerHTML = `${needs}<p class="label nav-foot-next">Next: <a href="#${h(rec)}">${h(pageTitle(rec))}</a>${why ? `: ${h(why)}` : ""}</p>`;

    fitSidebar();
  }
  // the Questions count in the footer, rose while above zero; questions.js also sets it on its own poll
  window.setQuestionCount = function (n) {
    const b = q("#q-count"); if (!b) return;
    b.hidden = !n; b.textContent = n || "";
    b.setAttribute("aria-label", `${n} open question${n === 1 ? "" : "s"}`);
  };
  window.renderShell = function () {
    const stages = coreStages();
    q("#stage-links").innerHTML = SECTIONS.map(sec => {
      const mine = stages.filter(s => s.section === sec.number), st = sectionState(mine, currentPage);
      return `<div class="nav-section"><p class="nav-section-title sec-${st}" id="nav-sec-${h(sec.number)}"><span class="sec-t">${h(sec.number)} ${h(sec.title)}</span><span class="sr-only">, ${SEC_WORD[st]}</span></p>
        <div role="list" aria-labelledby="nav-sec-${h(sec.number)}">${sec.number === 1 ? MODEL_LINK : ""}${mine.map(s => stageLink(s) + (s.key === "screen" ? reviewLink() : "")).join("")}${mine.some(s => s.key === "recall") ? RESULTS_LINK + REPORTING_LINK : ""}</div></div>`;
    }).join("");
    // each stage page's heading is its registry title, as in the sidebar and the top bar
    stages.forEach(s => { const t = q(`#${PAGES[s.key]} .page-head h1`); if (t) t.textContent = s.title; });
    if (PROJECT_TITLE) { const el = q("#nav-project"); el.textContent = PROJECT_TITLE; }
    q("#nav-overview-link") && (q("#nav-overview-link").hidden = !PROJECT_TITLE);
    call("/api/questions/summary").then(s => { if (s) setQuestionCount(s.open); }).catch(() => {});
    refreshReviewTip();
    markCurrentPage(currentPage);
  };

  // the last page used, per project, in this browser only: the key is a hash of the folder (no path); Home and the
  // Overview are never kept. A project opens on its Overview, which offers the last page (ui-redesign D7).
  const lastPageKey = () => PROJECT_KEY ? "wider.lastPage." + PROJECT_KEY : null;
  const NOT_KEPT = new Set(["home", "overview"]);
  const NO_GUIDE = new Set(["home", "methods"]);   // pages with no side panel and no Guide or Ask WIDER button
  window.startPage = function () { return PROJECT_TITLE ? "overview" : "home"; };
  // the recommended step: what `wider next` names (the server sends "model" while the model page is due; a project
  // past query creation is not sent back to it, audit F8); Recall and report once every step is done
  const recommended = () => NEXT_KEY || "recall";
  const lastPage = () => { const k = lastPageKey(), v = k ? store.get(k) : null; return v && PAGES[v] && !NOT_KEPT.has(v) ? v : null; };
  window.markCurrentPage = function (key) {
    // a stage refresh held while the labelling queue was open runs once the queue is left
    if (window.stageRefreshHeld && key !== "sample") { window.stageRefreshHeld = false; if (window.refreshStages) refreshStages(); }
    if (!NOT_KEPT.has(key) && PAGES[key] && lastPageKey()) store.set(lastPageKey(), key);
    qa(".sidebar .nav-link").forEach(a => {
      const on = a.dataset.key === key;
      a.classList.toggle("current", on);
      if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    markSections(key);
    askFollow(key);
    loadGuide(key);
    askWays(key);
    renderPrevNext(key);
    renderTopbar(key);
    renderJourney(key);
    renderFooter(key);
  };

  // ------------------------------------------------------------------ journey line (ui-redesign D12, task 4.3)
  // The transit line of concept A (owner, 29 Oct) under the top bar on every project page, the Overview included: each
  // of the four stages is a segment with its steps as stations (the model page first in stage 1). A solid segment is
  // done, a dashed one still open; a station is ticked when done, ringed blue when it is the step `wider next` names,
  // ringed rose when it needs you, and the page shown is underlined. Every station links to its page and says its
  // state in words to screen readers and in its tooltip. "Hide" folds it, kept in this browser (`wider.journey`);
  // "Show the journey line" brings it back. Home has none.
  const JOURNEY_KEY = "wider.journey";
  let journeyPage = null;
  function station(k, label, cls, word, key) {
    const mark = cls.includes("needs") ? Status.icon("needs") : cls.includes("test") ? Status.icon("test")
      : cls.includes("done") ? Status.icon("done") : "";
    return `<li class="jst ${cls}"><a href="#${h(k)}"${k === key ? ' aria-current="page"' : ""} title="${h(`${label}: ${word}`)}"><span class="d" aria-hidden="true">${mark}</span><span class="nm">${h(label)}</span><span class="sr-only">, ${h(word)}</span></a></li>`;
  }
  function renderJourney(key) {
    journeyPage = key;
    const r = q("#route"), sh = q("#route-show"); if (!r || !sh) return;
    const skip = !PROJECT_TITLE || key === "home", off = store.get(JOURNEY_KEY) === "0";
    r.hidden = skip || off; sh.hidden = skip || !off; sh.setAttribute("aria-expanded", "false");
    sh.onclick = () => { store.set(JOURNEY_KEY, "1"); renderJourney(journeyPage); const b = q("#route-hide"); if (b) b.focus(); };
    if (r.hidden) { r.innerHTML = ""; return; }
    const stages = coreStages(), rec = recommended();
    const cols = [];   // one grid column per stage, its share of the width by its number of stations
    const segs = SECTIONS.map(sec => {
      const mine = stages.filter(s => s.section === sec.number); if (!mine.length) return "";
      const noModel = sec.number === 1 && !CONSTRUCT_BACKEND;   // the model is a step of section 1 too
      let st = sectionState(mine, key);
      if (noModel && st === "done") st = "current";
      const sts = mine.map(s => {
        const c = stateClass(s), needs = c === "needs" || c === "review";
        const cls = [s.key === rec ? "now" : "", c === "test" ? "test" : needs ? "needs" : s.state === "done" ? "done" : ""].filter(Boolean).join(" ") || "open";
        const word = (s.key === rec ? "current step, " : "") + (needs || c === "test" ? stateText(s) : wordWaits(s));
        const out = [station(s.key, s.short || s.title, cls, word, key)];
        // the sidebar's fixed links sit at the same places (owner, 30 Oct, D32 F16): one list of steps in both
        if (s.key === "screen") out.push(station("review", fixedTitle("review"), "open", REVIEW_TIP, key));
        if (s.key === "recall") out.push(station("results", fixedTitle("results"), "open", "open any time", key),
                                        station("reporting", fixedTitle("reporting"), "open", "open any time", key));
        return out;
      }).flat();
      if (sec.number === 1) sts.unshift(station("model", "Model", CONSTRUCT_BACKEND ? "done" : rec === "model" ? "now" : "open",
        CONSTRUCT_BACKEND ? "set" : (rec === "model" ? "current step, " : "") + modelUnset(), key));
      const done = !noModel && mine.every(s => s.state === "done");
      cols.push(`minmax(min-content, ${sts.length}fr)`);
      return `<li class="jseg sec-${st}${done ? " all-done" : ""}" style="--n:${sts.length}"><span class="ln">${h(sec.number)} ${h(sec.title)}<span class="sr-only">, ${SEC_WORD[st]}</span></span><ol class="sts">${sts.join("")}</ol></li>`;
    }).join("");
    r.innerHTML = `<ol class="jl" style="--cols:${cols.join(" ")}">${segs}</ol>`
      + '<span class="rt"><button type="button" class="btn quiet small" id="route-hide" aria-controls="route" aria-expanded="true" aria-label="Hide the journey line">Hide<span class="tl"> journey line</span></button></span>';
    q("#route-hide").onclick = () => { store.set(JOURNEY_KEY, "0"); renderJourney(journeyPage); q("#route-show").focus(); };
  }

  // ------------------------------------------------------------------ top bar (design-pass-a1-dedup D4)
  // The top bar (ui-redesign D3, D6), one dark bar over every page: WIDER, "All projects" (Home), and inside a project
  // the breadcrumb with the "Switch project" menu on the project's title and the page name (no step number:
  // owner, 30 Oct, D32 F16), Questions, the labelled Guide
  // button with its state, and Help. A page fills two slots through Topbar.set(key, {notebook, main}) (a `guide` slot
  // still replaces the shared Guide button for a page with its own panel). The config is kept per page key, so a
  // page's buttons survive every redraw (showPage, refreshStages) and never show on a page that set none. A slot is
  // {label, onclick, count?, expanded?, title?}; label, count and expanded may be functions, read at each draw.
  const OTHER_PAGE = { home: "Home", overview: "Overview", model: "Model for query creation", questions: "Questions",
                       notes: "Notes and tags", history: "History", help: "Help", settings: "Settings and data",
                       results: "Results dashboard", reporting: "PRISMA reporting", review: "Screening review", methods: "Methods" };
  const topbarConf = {};
  const val = (v) => typeof v === "function" ? v() : v;
  const GUIDE_ICON = '<svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 3h4a2 2 0 0 1 1.5.7A2 2 0 0 1 9.5 3h4v9.5h-4a1.5 1.5 0 0 0-1.5 1.5 1.5 1.5 0 0 0-1.5-1.5h-4z" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>';
  const CARET = '<svg class="caret" width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M3 4.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>';
  function slotHtml(slot, cfg) {
    if (!cfg) return "";
    const count = val(cfg.count), exp = val(cfg.expanded);
    const cls = slot === "main" ? "tb-main-btn" : slot === "guide" ? "tb-btn gtop" : "tb-btn";
    // the main button's label may end in an ellipsis on a narrow bar (G0); its title then keeps the whole label
    const title = cfg.title || (slot === "main" ? val(cfg.label) : "");
    return `<button type="button" class="${cls}" id="tb-${slot}-btn"${title ? ` title="${h(title)}"` : ""}${exp == null ? "" : ` aria-expanded="${!!exp}"`}>`
      + `${slot === "guide" ? GUIDE_ICON : ""}${h(val(cfg.label))}${count == null ? "" : ` <span class="tb-count">${h(count)}</span>`}</button>`;
  }
  // the shared side panel (below) answers shows/toggle: Guide is expanded only while the panel shows its tab
  const sharedGuide = () => ({ label: "Guide", title: "Open or close the guide for this page",
    expanded: () => !!(window.Guide && Guide.shows("guide")), onclick: () => { if (window.Guide) Guide.toggle(); } });
  // the switcher's list: the recent projects from /api/home with how many items need the researcher and the next step
  let switchProjects = null;
  function switchItems() {
    if (!switchProjects) return '<li class="sw-wait">Loading the projects…</li>';
    return switchProjects.map((p, i) => {
      const need = (p.needs_you || []).length, nxt = p.next ? `Next: ${h(p.next.title)}` : "Every step is done";
      return `<li><a href="#overview" data-sw="${i}"${p.current ? ' aria-current="true"' : ""}>${h(p.title)}`
        + `<small>${need ? `${need} need${need === 1 ? "s" : ""} you · ` : ""}${nxt}</small></a></li>`;
    }).join("") + '<li class="sep" role="separator"></li><li><a href="#home" data-sw-home>All projects and new review</a></li>';
  }
  function setSwitch(open, focusBack) {
    const b = q("#sw-btn"), list = q("#sw-list"); if (!b || !list) return;
    list.hidden = !open; b.setAttribute("aria-expanded", String(open));
    if (open) {
      list.innerHTML = switchItems(); wireSwitchList();
      call("/api/home").then(r => {
        switchProjects = (r && r.projects) || [];
        if (!list.hidden) { list.innerHTML = switchItems(); wireSwitchList(); const a = list.querySelector("a"); if (a && a.focus) a.focus(); }
      }).catch(() => { switchProjects = []; if (!list.hidden) { list.innerHTML = switchItems(); wireSwitchList(); } });
    } else if (focusBack) b.focus();
  }
  function wireSwitchList() {
    const list = q("#sw-list");
    qa("[data-sw]", list).forEach(a => a.onclick = (e) => {
      const p = switchProjects && switchProjects[Number(a.dataset.sw)]; if (!p) return;
      if (e && e.preventDefault) e.preventDefault();
      setSwitch(false, false);
      if (p.current) go("overview"); else openProject(p.root, null);   // one open project at a time (D3)
    });
    qa("[data-sw-home]", list).forEach(a => a.onclick = () => setSwitch(false, false));
  }
  function renderTopbar(key) {
    const bar = q("#topbar"); if (!bar) return;
    const inProject = !!PROJECT_TITLE && key !== "home";
    const avail = STAGES.filter(s => s.state !== "extension");
    const idx = inProject ? avail.findIndex(s => s.key === key) : -1;
    const page = idx >= 0 ? avail[idx].title : (OTHER_PAGE[key] || "");
    bar.hidden = false;
    const home = q("#tb-home");
    if (key === "home") { home.setAttribute("aria-current", "page"); home.classList.add("primary"); }
    else { home.removeAttribute("aria-current"); home.classList.remove("primary"); }
    q("#tb-plbl").hidden = !inProject;
    const crumb = q("#tb-crumb");
    crumb.hidden = !inProject;
    crumb.innerHTML = !inProject ? "" : `<div class="switch" id="tb-switch"><button type="button" id="sw-btn" aria-expanded="false"`
      + ` aria-controls="sw-list" title="${h(PROJECT_TITLE)}" aria-label="Open project: ${h(PROJECT_TITLE)}. Switch project">`
      + `<span class="nm">${h(PROJECT_TITLE)}</span><span class="swl">Switch project</span>${CARET}</button>`
      + `<ul id="sw-list" hidden></ul></div> <span class="sep" aria-hidden="true">/</span> <span class="pg" aria-current="page">${h(page)}</span>`;
    if (inProject) {
      q("#sw-list").hidden = true;
      q("#sw-btn").onclick = () => setSwitch(q("#sw-list").hidden, false);
      q("#tb-switch").onkeydown = (e) => { if (e.key === "Escape" && !q("#sw-list").hidden) { e.preventDefault(); e.stopPropagation(); setSwitch(false, true); } };
    }
    q("#tb-questions").hidden = !inProject;
    const conf = topbarConf[key] || {};
    const slots = { notebook: conf.notebook, main: conf.main, guide: inProject && !NO_GUIDE.has(key) ? (conf.guide || sharedGuide()) : null };
    ["notebook", "main", "guide"].forEach(slot => {
      q(`#tb-${slot}`).innerHTML = slotHtml(slot, slots[slot]);
      if (slots[slot] && slots[slot].onclick) q(`#tb-${slot}-btn`).onclick = slots[slot].onclick;
    });
    renderMore(slots, key, inProject);
    // one name per browser (D33): shown on every page, asked once when a project opens without one
    if (window.WiderName) { WiderName.render(); if (inProject) WiderName.askOnce(); }
    syncTopH();
  }
  // On a phone (700 px or less, ui-redesign D27) Notebook and Guide fold into "More", which shows the Notebook count;
  // so do Ask WIDER and Questions (owner inspection G0: the bar keeps two rows at 375 px), whose own buttons the CSS
  // hides there. The main button sits in a bar at the bottom of the screen (CSS). Wider windows hide "More".
  const PHONE_BAR = "(max-width: 700px)";
  const phoneBar = () => !!(window.matchMedia && window.matchMedia(PHONE_BAR).matches);
  function renderMore(slots, key, inProject) {
    const box = q("#tb-more"); if (!box) return;
    slots = Object.assign({}, slots);
    let askHere = false;   // the guide helpers are declared further down; a draw before they exist leaves Ask out
    try { askHere = !!key && guideHere(key) && !askHidden(); } catch (e) { askHere = false; }
    if (askHere)
      slots.ask = { label: "Ask WIDER", expanded: () => panelShows("ask"), onclick: () => { const b = q("#tb-ask"); if (b && b.onclick) b.onclick(); } };
    if (inProject) {
      const qc = q("#q-count");
      slots.questions = { label: "Questions", count: qc && !qc.hidden && qc.textContent ? qc.textContent : null, onclick: () => { location.hash = "#questions"; } };
    }
    const items = ["notebook", "guide", "ask", "questions"].filter(k => slots[k]);
    if (!items.length) { box.innerHTML = ""; return; }
    const nb = slots.notebook ? val(slots.notebook.count) : null;
    box.innerHTML = `<div class="switch tb-more-wrap"><button type="button" class="tb-btn" id="tb-more-btn"`
      + ` aria-expanded="false" aria-controls="tb-more-list">More${nb == null ? "" : ` <span class="tb-count">${h(nb)}</span>`}${CARET}</button>`
      + `<ul id="tb-more-list" hidden>${items.map(k => {
        const c = slots[k], n = val(c.count), exp = val(c.expanded);
        return `<li><button type="button" data-more="${k}"${exp == null ? "" : ` aria-pressed="${!!exp}"`}>${k === "guide" ? GUIDE_ICON : ""}`
          + `${h(val(c.label))}${n == null ? "" : ` <span class="tb-count">${h(n)}</span>`}</button></li>`;
      }).join("")}</ul></div>`;
    const btn = q("#tb-more-btn"), list = q("#tb-more-list");
    const setMore = (open, focusBack) => {
      list.hidden = !open; btn.setAttribute("aria-expanded", String(open));
      if (open) { const f = list.querySelector("button"); if (f && f.focus) f.focus(); } else if (focusBack) btn.focus();
    };
    btn.onclick = () => setMore(list.hidden, false);
    q(".tb-more-wrap", box).onkeydown = (e) => { if (e.key === "Escape" && !list.hidden) { e.preventDefault(); e.stopPropagation(); setMore(false, true); } };
    qa("[data-more]", list).forEach(b => b.onclick = () => {
      const c = slots[b.dataset.more]; setMore(false, false);
      if (c && c.onclick) c.onclick();
    });
  }
  // a click elsewhere closes "More"
  document.addEventListener && document.addEventListener("click", (e) => {
    const list = q("#tb-more-list"), t = e.target;
    if (list && !list.hidden && !(t && t.closest && t.closest(".tb-more-wrap"))) { list.hidden = true; q("#tb-more-btn").setAttribute("aria-expanded", "false"); }
  });
  // Scrolling down on a phone slims the bar to its first row; scrolling up brings the second row back. The bar keeps
  // its place in the page (a bottom margin as tall as the hidden row): otherwise the page moves up by that height,
  // the browser shifts the scroll position to match, that reads as scrolling up and the row flickers back and forth.
  let lastY = 0;
  const setSlim = (on) => {
    const bar = q("#topbar"), h0 = bar ? Number(bar.offsetHeight) || 0 : 0;
    document.body.classList.toggle("tb-slim", on);
    if (bar && bar.style) bar.style.marginBottom = on ? Math.max(0, h0 - (Number(bar.offsetHeight) || 0)) + "px" : "";
  };
  window.addEventListener && window.addEventListener("scroll", () => {
    const y = window.scrollY || 0, slim = document.body.classList.contains("tb-slim");
    if (!phoneBar() || y < 60) { if (slim) setSlim(false); }
    else if (y > lastY + 8 && !slim) setSlim(true);
    else if (y < lastY - 8 && slim) setSlim(false);
    if (Math.abs(y - lastY) > 8 || y < 60) lastY = y;
  }, { passive: true });
  // The sidebar starts under the journey line until the page scrolls; a window-tall sidebar then hides its foot ("Next:
  // ...") below the window (owner inspection), so its height is the room left between its top and the window's bottom.
  function fitSidebar() {
    const sb = q("#sidebar");
    if (!sb || !sb.getBoundingClientRect || typeof getComputedStyle !== "function" || getComputedStyle(sb).position !== "sticky") return;
    const h = Math.round((window.innerHeight || 0) - Math.max(0, sb.getBoundingClientRect().top));
    if (h > 0 && document.body.style) document.body.style.setProperty("--sb-h", h + "px");
  }
  window.addEventListener && window.addEventListener("scroll", fitSidebar, { passive: true });
  window.addEventListener && window.addEventListener("resize", fitSidebar);
  if (typeof ResizeObserver === "function" && q("#route")) new ResizeObserver(fitSidebar).observe(q("#route"));
  // The bar wraps on a narrow window or when a page puts long buttons in it; the sidebar, the guide panel and the
  // record drawer sit under it through --top-h, so that is the bar's measured height (audit 5.6), not a guess per width.
  function syncTopH() {
    const bar = q("#topbar"); if (!bar || bar.hidden) return;
    const px = Math.round(Number(bar.offsetHeight) || 0);
    if (px > 0) document.body.style.setProperty("--top-h", px + "px");
  }
  if (typeof ResizeObserver === "function" && q("#topbar")) new ResizeObserver(syncTopH).observe(q("#topbar"));
  window.Topbar = {
    set(key, conf) { topbarConf[key] = conf || {}; if (key === currentPage) renderTopbar(key); },
    redraw() { renderTopbar(currentPage); },
  };

  // ------------------------------------------------------------------ side panel (ui-redesign D6, task 4.5)
  // Every page inside a project has a panel on the right with two tabs (guide-assistant D23, owner 31 Oct): Guide, the
  // page's guide (GET /api/guide/<page>, the text of `wider guide <page>`), and Ask WIDER. It is open on a window
  // 1180 px wide or more and closed below, until the researcher shows or hides it: that choice is kept in this browser
  // (`wider.guide`) and holds on every page, Deduplication included (D28); the tab chosen last is `wider.panelTab`.
  // One side panel at a time: a record drawer over the page hides it, and it comes back when the drawer closes.
  // A page's notices go into its "What you can do here now" box, not into the panel (D30).
  const GUIDE_KEY = "wider.guide", TAB_KEY = "wider.panelTab";
  const guideState = { page: null, data: null, titles: null };
  // Ask WIDER's state lives here, not in the page: the typed question (draft) is kept while the researcher is on
  // other pages; about is the page asked about ("general": WIDER in general), turns the reviewer's conversation
  // (GET /api/assistant/history), job the question running
  const askState = { about: "general", draft: "", author: null, turns: null, loading: null, job: null, waiting: "", progress: "",
                     error: "", note: "", consent: null, consentTurn: null };
  let askPage = null, askAboutNext = null, askNoProject = false;   // the page "Asking about:" last followed; what #ask set
  const ASK_LABELLING = "Ask WIDER is not offered while you label, so your labels stay your own.";
  // Ask WIDER is offered on every project page except while a labelling queue shows records (D14), which only happens
  // on Samples and labelling
  function askHidden() { return !!(window.queueOpen && window.queueOpen()); }
  // the pages "Asking about:" offers: those with a guide, in sidebar order
  function askPages() {
    const order = ["overview", "model", ...STAGES.filter(s => s.state !== "extension").map(s => s.key),
                   "review", "results", "reporting", "questions", "notes", "history", "settings", "help"];
    return order.filter((k, i) => order.indexOf(k) === i && PAGES[k] && !NO_GUIDE.has(k));
  }
  const askable = (key) => !!key && askPages().includes(key);
  const askTitle = (key) => { const st = STAGES.find(s => s.key === key); return st ? st.title : (OTHER_PAGE[key] || key); };
  // "Asking about:" follows the page shown: each new page sets it, or what an #ask address asked for
  function askFollow(key) {
    if (key === askPage) return;
    askPage = key;
    askState.about = askAboutNext || (askable(key) ? key : "general"); askAboutNext = null;
    askState.note = "";
    if (askNoProject) { askNoProject = false; const st = q("#home-status"); if (st) st.textContent = ASK_NO_PROJECT; }
  }
  // the top-bar button: where the panel shows, and not while labelling; expanded while the panel shows its tab
  function askWays(key) {
    const tb = q("#tb-ask"); if (!tb) return;
    tb.hidden = !guideHere(key) || askHidden();
    tb.setAttribute("aria-expanded", String(panelShows("ask")));
  }
  const guideWanted = () => { const v = store.get(GUIDE_KEY); return v ? v === "open" : (window.innerWidth || 0) >= 1180; };
  const panelTab = () => store.get(TAB_KEY) === "ask" ? "ask" : "guide";
  // a record drawer over the page (not the pair view docked in Deduplication) hides the panel while it is open
  const drawerOver = () => !!(window.Drawer && Drawer.isOpen() && !Drawer.isDocked());
  const panelOpen = () => guideHere(guideState.page) && guideWanted();
  const panelShows = (tab) => panelOpen() && !drawerOver() && panelTab() === tab;
  // Home and Methods have no guide of their own (methods-explainer D8: Methods is what the guide links to)
  const guideHere = (key) => !!PROJECT_TITLE && !!key && !NO_GUIDE.has(key) && !(topbarConf[key] || {}).guide;
  const GUIDE_LEAD = new Set(["What to do", "What to do now"]);
  function guidePanelHtml(g) {
    const head = `<div class="guide-head">${GUIDE_ICON}<h2 id="guide-h">Guide for this page</h2></div>`;   // Guide in the top bar closes it (owner, 29 Oct)
    if (!g) return head + '<p class="guide-wait">Loading the guide…</p>';
    return head + '<dl>' + (g.parts || []).filter(p => p.items && p.items.length).map(p => {
      const cls = GUIDE_LEAD.has(p.heading) ? "gp gbox glead" : p.heading === "Watch out" ? "gp gbox gwatch" : "gp";
      const items = GUIDE_LEAD.has(p.heading) && p.items.length > 1 ? `<ol>${p.items.map(t => `<li>${h(t)}</li>`).join("")}</ol>` : p.items.map(t => h(t)).join(" ");
      return `<div class="${cls}"><dt>${h(p.heading)}</dt><dd>${items}${showLinks(p.buttons)}</dd></div>`;
    }).join("") + '</dl>' + methodsLine(g.methods);
  }
  // "Show" beside an answer that names controls (guide-assistant D13): one link per name, which finds it on the page
  const showLinks = (names) => names && names.length
    ? `<p class="guide-show">Show: ${names.map(n => `<button type="button" class="link-btn" data-show="${h(n)}" aria-label="Show ${h(n)}">${h(n)}</button>`).join(", ")}</p><p class="guide-show-note" role="status"></p>`
    : "";
  // showButton(page, name): opens the page if needed, then scrolls to the first visible control whose accessible name
  // is the name (else a heading, legend or label with that text), outlines it and focuses it if it is a control. It
  // never presses anything. Returns "control", "text" or null (not on the page now). The side panel is not searched,
  // so its own links never count.
  const visible = (el) => { for (let n = el; n && n.nodeType === 1; n = n.parentNode) if (n.hidden) return false; return true; };
  const accName = (el) => String(el.getAttribute("aria-label") || el.textContent || "").replace(/\s+/g, " ").trim();
  function findShown(name) {
    const outside = (el) => !el.closest("#guide-panel");
    const pick = (sel) => qa(sel).find(el => outside(el) && visible(el) && accName(el) === name) || null;
    // a card link named by its title line ("Seed papers", then its state lines) counts when no control is named exactly
    const titled = (sel) => qa(sel).find(el => outside(el) && visible(el) && el.firstElementChild
                                               && accName(el.firstElementChild) === name) || null;
    const CONTROLS = 'button, a, [role="button"], input[type="submit"]';
    const c = pick(CONTROLS) || titled("a");
    if (c) return { el: c, control: true };
    const t = pick("h1, h2, h3, h4, h5, h6, legend, label");
    return t ? { el: t, control: false } : null;
  }
  async function showButton(page, name) {
    if (page && page !== currentPage) {
      go(page);
      for (let i = 0; i < 20 && (currentPage !== page || !findShown(name)); i++) await new Promise(r => setTimeout(r, 100));
    }
    const f = findShown(name);
    if (!f) return null;
    if (f.el.scrollIntoView) f.el.scrollIntoView({ block: "center" });
    f.el.classList.add("guide-shown");
    const off = () => { f.el.classList.remove("guide-shown"); f.el.removeEventListener("blur", off); };
    setTimeout(off, 3000);
    if (f.control) { f.el.addEventListener("blur", off); f.el.focus(); }
    return f.control ? "control" : "text";
  }
  window.showButton = showButton;
  // "Why WIDER works this way:" and a link to each methods topic of the page (methods-explainer D8), titled from
  // GET /api/methods (asked for once, by methods.js); the id stands in until the titles arrive
  function methodsLine(ids) {
    if (!ids || !ids.length) return "";
    const t = guideState.titles || {};
    return `<p class="guide-methods">Why WIDER works this way: ${ids.map(id => `<a href="#methods/${h(id)}">${h(t[id] || id)}</a>`).join(", ")}</p>`;
  }
  // the panel: a tablist (Guide, Ask WIDER) as the page tabs of app.js, the guide in #ppane-guide and Ask WIDER in
  // #ppane-ask; built when it opens, emptied when it closes, hidden (and kept) while a record drawer lies over the page
  const panelHtml = () => '<div class="ptabs" role="tablist" aria-label="Side panel">'
    + `<button type="button" role="tab" class="ptab" id="ptab-guide" aria-controls="ppane-guide">${GUIDE_ICON}Guide</button>`
    + '<button type="button" role="tab" class="ptab" id="ptab-ask" aria-controls="ppane-ask">Ask WIDER</button></div>'
    + '<div role="tabpanel" class="ppane" id="ppane-guide" aria-labelledby="ptab-guide" tabindex="0"><div id="guide-body"></div></div>'
    + '<div role="tabpanel" class="ppane" id="ppane-ask" aria-labelledby="ptab-ask" tabindex="0" hidden>'
    + '<p class="ask-intro">Ask how WIDER works, what a page or button does, or why it works this way. The answer comes from WIDER\'s own documents; it never reads your records.</p>'
    + '<p class="ask-line" id="ask-off" hidden></p><div id="ask-top"></div><section id="ask-box" class="ask-box" aria-labelledby="ask-h"></section>'
    + cmdFold(`<code>${esc('wider assistant ask "<question>" [--page <page>]')}</code>, <code>wider assistant history</code>`) + '</div>';
  const PTABS = ["guide", "ask"];
  function drawGuide() {
    const pn = q("#guide-panel"); if (!pn) return;
    const on = panelOpen(), shown = on && !drawerOver();
    pn.hidden = !shown;
    if (document.body && document.body.classList) document.body.classList.toggle("guide-open", shown);   // a wide window makes room
    if (!on) { pn.innerHTML = ""; return; }
    if (!q("#ppane-guide", pn)) { pn.innerHTML = panelHtml(); wireTabs(pn); }
    q("#guide-body", pn).innerHTML = guidePanelHtml(guideState.data);
    drawTabs();
  }
  // the tab chosen shows its panel: aria-selected and a roving tab index (only the chosen tab is in the Tab order)
  function drawTabs() {
    const tab = panelTab();
    PTABS.forEach(t => {
      const b = q(`#ptab-${t}`), pane = q(`#ppane-${t}`); if (!b || !pane) return;
      b.setAttribute("aria-selected", String(t === tab)); b.tabIndex = t === tab ? 0 : -1;
      pane.hidden = t !== tab;
    });
    if (tab === "ask") drawAsk();
  }
  // the arrow keys, Home and End choose the next, previous, first or last tab (round), show it and focus it
  function wireTabs(pn) {
    PTABS.forEach(t => {
      const b = q(`#ptab-${t}`, pn);
      b.addEventListener("click", () => chooseTab(t, false));
      b.addEventListener("keydown", (e) => {
        const i = PTABS.indexOf(t), n = PTABS.length;
        const to = e.key === "ArrowRight" ? PTABS[(i + 1) % n] : e.key === "ArrowLeft" ? PTABS[(i - 1 + n) % n]
          : e.key === "Home" ? PTABS[0] : e.key === "End" ? PTABS[n - 1] : null;
        if (!to) return;
        e.preventDefault(); chooseTab(to, true);
      });
    });
  }
  function chooseTab(t, focusTab) {
    const pn = q("#guide-panel");
    if (pn && panelTab() !== t) pn.scrollTop = 0;   // the other tab starts at its top, not where a long conversation was
    store.set(TAB_KEY, t);
    drawTabs(); panelButtons();
    const b = q(`#ptab-${t}`); if (focusTab && b && b.focus) b.focus();
  }
  // the top bar's Guide and Ask WIDER say whether the panel shows their tab
  function panelButtons() {
    const g = q("#tb-guide-btn"); if (g) g.setAttribute("aria-expanded", String(panelShows("guide")));
    const m = q('#tb-more-list [data-more="guide"]'); if (m) m.setAttribute("aria-pressed", String(panelShows("guide")));
    askWays(guideState.page);
  }
  function loadGuide(key) {
    if (guideState.page !== key) { guideState.page = key; guideState.data = null; }
    drawGuide();
    if (!PROJECT_TITLE || !key || NO_GUIDE.has(key)) return;
    // asked for on every visit: its notices count what still waits, its can_do follows the stage's state
    call(`/api/guide/${encodeURIComponent(key)}`).then(g => {
      if (!g || guideState.page !== key) return;
      guideState.data = g; drawGuide(); drawCanDo(key, g);
      if (g.methods && g.methods.length && !guideState.titles && window.Methods)
        Methods.titles().then(t => { guideState.titles = t; drawGuide(); }).catch(() => {});
    }).catch(() => {});
  }
  // "What you can do here now" (ui-redesign D14, task 4.6): one box under a stage page's title with the guide's can_do
  // sentence (the line `wider guide <page>` prints first), marked by the stage's state: work here, completed and still
  // changeable, locked (with "Unlock to change…", which opens Concepts and synonyms where Unlock is), or not yet. The
  // box never disables anything on the page.
  const hasCanDo = (key) => key === "model" || STAGES.some(s => s.key === key && s.state !== "extension");
  // a blocked step the guide still opens (Search results on a records start: "You can import ... now") is work
  function canDoKind(key, text) {
    // the model page is done once a model is chosen (D32 F15: its main button becomes Continue)
    if (key === "model") return typeof CONSTRUCT_BACKEND !== "undefined" && CONSTRUCT_BACKEND ? "done" : "work";
    const s = STAGES.find(x => x.key === key); if (!s) return "work";
    if (key === "query" && s.state === "done") return "locked";
    if (s.state === "blocked") return /^Not yet/.test(text || "") ? "notyet" : "work";
    if (s.state === "done" && s.test_model) return "test";     // D32 F3: amber
    if (s.state === "done" && s.needs_you) return "needs";     // D32 F2, F10: "Not finished", rose
    return s.state === "done" ? "done" : "work";
  }
  // the test-model sentence of the guide's can_do (D32 F3) gets its own red line
  const TEST_LINE = "These results come from the test model, not from a real one: do not use them for your review.";
  function drawCanDo(key, g) {
    if (!hasCanDo(key) || !g.can_do) return;
    const box = q(`#can-do-${key}`); if (!box) return;
    const kind = canDoKind(key, g.can_do);
    box.className = `can-do can-do-${kind}`;
    box.hidden = false;
    // a done step's one filled button is "Continue to <next step>" at the end of this box; the page's own main button
    // turns into an outline (style.css .page.step-done button.main-act), so the page keeps one filled button (D32 F15)
    // the last step has no Continue, so its own main button stays filled
    const avail = STAGES.filter(s => s.state !== "extension"), at = avail.findIndex(s => s.key === key);
    const next = key === "model" ? avail[0] : at >= 0 ? avail[at + 1] : null;
    const settled = (kind === "done" || kind === "locked") && key !== "dedup" && !!next;
    const page = q(`#page-${key}`); if (page && page.classList) page.classList.toggle("step-done", settled);
    const cont = kind === "done" && key !== "dedup" && next
      ? `<p class="can-do-main"><a class="btn primary" href="#${h(next.key)}">Continue to ${h(next.title)}</a></p>` : "";
    // the page's notices (Deduplication: pairs waiting, files added since the run) sit in this box, not in the guide,
    // which shows the same general text on every page (ui-redesign D30, owner 29 Oct)
    const notes = (g.notices || []).length ? `<ul class="can-do-notes">${g.notices.map(n => `<li>${h(n)}</li>`).join("")}</ul>` : "";
    const test = g.can_do.includes(TEST_LINE), text = test ? g.can_do.replace(" " + TEST_LINE, "") : g.can_do;
    box.innerHTML = (test ? `<p class="can-do-test">${h(TEST_LINE)}</p>` : "")
      + `<p><b>What you can do here now:</b> ${h(text)}</p>` + notes + cont
      + (kind === "locked" ? '<p class="can-do-main"><a class="btn primary" href="#import">Continue to Search results</a></p><details class="can-do-unlock"><summary>Change a locked string</summary><p><button type="button" class="btn quiet small" data-unlock>Unlock to change…</button></p></details>' : "");
    box.onclick = (e) => {
      if (!e.target || !e.target.closest || !e.target.closest("[data-unlock]")) return;
      go("construct");
      const b = q("#art-unlock"); if (b && !b.hidden && b.focus) b.focus();
    };
  }
  // the end of every stage page: "Previous <page>" and "Continue to <page>", the neighbouring stage pages of the
  // registry (ui-redesign D20: the page's one Continue). Deduplication has its own quiet "Continue to" link.
  function renderPrevNext(key) {
    const avail = STAGES.filter(s => s.state !== "extension"), i = avail.findIndex(s => s.key === key);
    if (i < 0 || key === "dedup") return;
    const nav = q(`[data-pn="${key}"]`); if (!nav) return;
    const prev = avail[i - 1], next = avail[i + 1];
    nav.hidden = false;
    nav.innerHTML = (prev ? `<a class="pn-prev" href="#${h(prev.key)}"><small>Previous</small> ${h(prev.title)}</a>` : "<span></span>")
      + (next ? `<a class="pn-next" href="#${h(next.key)}"><small>Continue to</small> ${h(next.title)}</a>` : "");
  }
  // opened: the focus goes into the panel (on Ask WIDER, into the question box); closed: back to the top-bar button of
  // the tab shown, or to "More" where Guide is folded into it (phone)
  function focusPanel(tab) {
    const t = tab === "ask" ? (q("#ask-q") || q("#ask-name") || q("#ptab-ask")) : q("#guide-panel");
    if (t && t.focus) t.focus();
  }
  function setGuide(open, tab) {
    tab = tab || panelTab();
    if (open) store.set(TAB_KEY, tab);
    store.set(GUIDE_KEY, open ? "open" : "closed");
    drawGuide(); renderTopbar(guideState.page); askWays(guideState.page);
    if (open) { focusPanel(tab); return; }
    const t = phoneBar() && q("#tb-more-btn") ? q("#tb-more-btn")
      : tab === "ask" && q("#tb-ask") && !q("#tb-ask").hidden ? q("#tb-ask") : q("#tb-guide-btn");
    if (t && t.focus) t.focus();
  }
  // a top-bar button opens the panel on its tab, switches to its tab, or closes the panel when it shows its tab; a
  // record drawer over the page is closed first (one side panel at a time, D24)
  function togglePanel(tab) {
    if (drawerOver()) {
      Drawer.close(true); drawGuide();   // drawer.js syncs too; this keeps the panel right without it
      if (panelOpen()) { if (panelTab() !== tab) chooseTab(tab, false); else panelButtons(); focusPanel(tab); return; }
    }
    if (panelOpen() && panelTab() === tab) setGuide(false, tab);
    else if (panelOpen()) { chooseTab(tab, false); focusPanel(tab); }
    else setGuide(true, tab);
  }
  const guidePanel = q("#guide-panel");
  if (guidePanel) {
    guidePanel.onkeydown = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setGuide(false); } };
    guidePanel.addEventListener("click", async (e) => {
      const b = e.target && e.target.closest ? e.target.closest("[data-show]") : null; if (!b) return;
      const note = b.closest("dd") ? b.closest("dd").querySelector(".guide-show-note") : null;
      if (note) note.textContent = "";
      const got = await showButton(currentPage, b.dataset.show);
      if (!got && note) note.textContent = `${b.dataset.show} is not on the page now.`;
    });
  }
  const tbAsk = q("#tb-ask");
  if (tbAsk) tbAsk.onclick = () => togglePanel("ask");
  // an old #ask address (the Ask WIDER page of 4d9fca7): app.js asks for the page to show instead. ?about=<page> of a
  // guide page shows that page; else the page shown stays, or Overview shows asking about WIDER in general; with no
  // project, Home says why. The address becomes the page's own and the panel opens on the Ask WIDER tab.
  window.askRoute = function (hash) {
    let about = null;
    try { about = new URLSearchParams(String(hash || "").split("?")[1] || "").get("about"); } catch (e) { about = null; }
    let key;
    if (!PROJECT_TITLE) { key = "home"; askNoProject = true; }
    else if (askable(about)) key = about;
    else if (askable(currentPage)) key = currentPage;
    else { key = "overview"; askAboutNext = "general"; }
    askPage = null;   // "Asking about:" is set again for the page shown
    if (PROJECT_TITLE) { store.set(GUIDE_KEY, "open"); store.set(TAB_KEY, "ask"); }
    history.replaceState(null, "", "#" + key);
    return key;
  };
  window.Guide = {
    isOpen: panelOpen,
    shows: panelShows,
    toggle: () => togglePanel("guide"),
    open: (tab) => setGuide(true, tab || "guide"),
    // drawer.js calls this when a drawer opens or closes: the panel hides under it and comes back on its tab
    sync: () => { drawGuide(); panelButtons(); },
    wanted: guideWanted,
    remember: (open) => store.set(GUIDE_KEY, open ? "open" : "closed"),
    // a page whose actions change what waits (Deduplication's decisions) asks for the guide again, so its notices follow
    reload: () => { if (guideState.page) loadGuide(guideState.page); },
  };

  // ------------------------------------------------------------------ Ask WIDER (guide-assistant D13, D17, D18, D23)
  // The second tab of the side panel: a question about WIDER, answered from WIDER's own documents by the project's query-creation
  // model (POST /api/assistant/ask, a job; the answer is read back from GET /api/assistant/history). Never offered
  // while a labelling queue is open. Everything the model or the researcher wrote is set with textContent: no link, image or
  // markup is ever made from it; only "Open" (a section's own address) and "Show" (a name the guide knows) are links.
  const ASK_FIXED = ["It explains from WIDER's documents and can be wrong: check the sources.",
                     "What you type is sent as written: do not paste a record's text."];
  const ASK_NO_NAME = "Type your name in Your name above: your conversation is kept under your name.";
  const ASK_NO_PROJECT = "Open a project first: Ask WIDER uses the project's model.";
  const ASK_NO_DOCS = "WIDER's documents do not explain this.";
  const ASK_DOCS_CHANGED = "Answered before WIDER's documents changed.";
  const ASK_UNREVIEWED = "(not yet reviewed by the author)";
  // the one name WIDER works as (ui-redesign D33, window.WiderName); until that lands, a stand-in with its rules: a
  // first name is stored, another name is stored and the page reloads, an empty one forgets
  const NAME_STANDIN = {
    key: "wider.reviewer",
    get: () => String(store.get(NAME_STANDIN.key) || "").trim(),
    set(name) {
      const n = String(name || "").trim(), before = NAME_STANDIN.get();
      if (n === before) return false;
      if (n) store.set(NAME_STANDIN.key, n); else { try { localStorage.removeItem(NAME_STANDIN.key); } catch (e) { /* private window */ } }
      if (before) location.reload();
      return true;
    },
  };
  const nameApi = () => window.WiderName || NAME_STANDIN;
  // the name as the server stores it (journal._clean: printable characters, trimmed, at most 80), so a long name or one
  // with a tab still finds its own conversation
  const askAuthor = () => [...String(nameApi().get() || "").replace(/[\p{C}\p{Z}]/gu, (c) => (c === " " ? c : ""))
    .trim()].slice(0, 80).join("").trim();
  // a request of the Ask WIDER tab names its signer, so a name saved in another tab meanwhile (or during the consent
  // question) never signs it
  const askSigned = (opts) => ({ ...opts, headers: { ...(opts.headers || {}), "X-Wider-Author": encodeURIComponent(askState.author) } });
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const askBusy = () => !!askState.job;
  function loadAskHistory(author) {
    if (author !== askState.author) askState.unkept = [];
    askState.author = author; askState.turns = null;
    const p = askState.loading = call("/api/assistant/history").then(d => {
      if (askState.loading !== p) return;
      askState.turns = (d && d.author === author && d.turns) || [];
    }).catch(() => { if (askState.loading === p) askState.turns = []; })
      .finally(() => { if (askState.loading === p) { askState.loading = null; drawAsk(); } });
    return p;
  }
  // "Asking as ana": with a name the tab names it and points at the top bar's Change (ui-redesign D33), the one place
  // the name is changed; the field is only there while no name is set
  const askAsLine = (n) => `Asking as ${n}: your conversation is kept under this name.`
    + (q("#tb-name") ? " To change it, use Change beside Working as in the top bar." : "");
  function drawAskName(row) {
    const n = askAuthor(), inp = q("#ask-name", row);
    if (n) {
      if (inp || !row.firstChild || row.textContent !== askAsLine(n)) { row.textContent = askAsLine(n); row.className = "ask-line"; }
      return;
    }
    if (inp) return;   // kept, so a redraw never takes a name being typed
    row.textContent = ""; row.className = "ask-field";
    const l2 = el("label", null, "Your name"); l2.setAttribute("for", "ask-name");
    const field = el("input"); field.id = "ask-name"; field.autocomplete = "name"; field.maxLength = 80;
    // a first name is only stored (no reload); the question box then takes the focus the field had
    const save = () => {
      if (!String(field.value || "").trim()) return;
      const had = document.activeElement === field;
      nameApi().set(field.value); drawAsk();
      const ta = q("#ask-q"); if (had && ta && ta.focus) ta.focus();
    };
    field.addEventListener("change", save);
    field.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
    row.append(l2, " ", field);
  }
  // "Asking about:" and the name row, drawn once and kept
  function drawAskTop() {
    const top = q("#ask-top"); if (!top) return;
    if (!q("#ask-about", top)) {
      top.textContent = "";
      const r1 = el("p", "ask-field"), l1 = el("label", null, "Asking about:"); l1.setAttribute("for", "ask-about");
      const sel = el("select"); sel.id = "ask-about";
      sel.addEventListener("change", () => { askState.about = sel.value; drawAsk(); });   // the typed question stays
      r1.append(l1, " ", sel);
      const r2 = el("p", "ask-field"); r2.id = "ask-name-row";
      top.append(r1, r2);
    }
    const sel = q("#ask-about", top);
    const opts = [["general", "WIDER in general"], ...askPages().map(k => [k, askTitle(k)])];
    if (sel.options.length !== opts.length) {
      sel.textContent = "";
      opts.forEach(([v, t]) => { const o = el("option", null, t); o.setAttribute("value", v); sel.appendChild(o); });
    }
    sel.value = askState.about;
    drawAskName(q("#ask-name-row", top));
  }
  // drawn while the Ask WIDER tab is chosen; while a labelling queue is open the tab says only why it offers nothing
  const askShown = () => { const pane = q("#ppane-ask"); return !!pane && !pane.hidden; };
  function drawAsk() {
    const box = q("#ask-box"); if (!box || !askShown()) return;
    const off = askHidden(), offLine = q("#ask-off");
    [".ask-intro", "#ask-top", "#ask-box", ".cmdline"].forEach(sel => { const n = q(`#ppane-ask ${sel}`); if (n) n.hidden = off; });
    if (offLine) { offLine.hidden = !off; offLine.textContent = off ? ASK_LABELLING : ""; }
    if (off) return;
    const ta0 = q("#ask-q", box), hadFocus = !!ta0 && document.activeElement === ta0;
    box.textContent = "";
    const top = q("#ask-top");
    if (!PROJECT_TITLE) { if (top) top.textContent = ""; box.appendChild(el("p", "ask-line", ASK_NO_PROJECT)); return; }
    drawAskTop();
    box.appendChild(el("h2", "ask-h", "Your conversation")).id = "ask-h";
    if (typeof CONSTRUCT_BACKEND === "undefined" || !CONSTRUCT_BACKEND) {
      const p = el("p", "ask-line", "Ask WIDER needs a model. Set one on ");
      const a = el("a", null, "Model for query creation"); a.href = "#model";
      p.append(a, "."); box.appendChild(p); return;
    }
    const author = askAuthor();
    if (!author) { askState.author = ""; askState.turns = null; box.appendChild(el("p", "ask-line", ASK_NO_NAME)); return; }
    if (author !== askState.author) loadAskHistory(author);
    ASK_FIXED.forEach(t => box.appendChild(el("p", "ask-line", t)));
    const conv = el("div", "ask-turns"); conv.setAttribute("aria-live", "polite");
    // turns allowed once are not stored (the consent dialog's promise): they stay on screen until the page reloads
    const shown = (askState.turns || []).concat(askState.turns ? askState.unkept || [] : []);
    shown.forEach((t, i, all) => conv.appendChild(askTurn(t, i === all.length - 1)));
    box.appendChild(conv);
    if (askState.turns === null) box.appendChild(el("p", "ask-line", "Loading your conversation…"));
    const lab = el("label", "ask-label", "Your question about WIDER"); lab.setAttribute("for", "ask-q");
    const ta = el("textarea", "ask-q"); ta.id = "ask-q"; ta.rows = 3; ta.value = askState.draft;
    const go = el("button", "ask-go", "Ask"); go.type = "button"; go.id = "ask-go";
    go.disabled = !askState.draft.trim() || askBusy();
    ta.addEventListener("input", () => { askState.draft = ta.value; go.disabled = !ta.value.trim() || askBusy(); });
    // Enter adds a line; Ctrl/Cmd+Enter asks (D18)
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!go.disabled) askNow(ta.value, null, "box"); } });
    go.addEventListener("click", () => askNow(ta.value, null, "box"));
    const row = el("div", "ask-row"); row.appendChild(go);
    box.append(lab, ta, row);
    // what a question sends, beside "Ask": the CLI's sentences (send notices assistant and assistant_embed); the
    // section search's local line says what it is about, so it never reads as "nothing leaves" beside an answer that does
    box.insertAdjacentHTML("beforeend", '<p class="send-note" data-send="assistant" hidden></p><p class="send-note" data-send="assistant_embed" data-send-local-line="Finding the matching sections runs on this computer." hidden></p>');
    if (askState.job) {
      const w = el("p", "ask-wait", askState.waiting); w.setAttribute("role", "status");
      const stop = el("button", "secondary small-btn ask-stop", "Stop"); stop.type = "button";
      stop.addEventListener("click", () => stopAsk(stop));
      w.append(" ", stop); box.appendChild(w);
      if (askState.progress) box.appendChild(el("p", "ask-line", askState.progress));
    }
    if (askState.error) { const e = el("p", "ask-error", askState.error); e.setAttribute("role", "alert"); box.appendChild(e); }
    if (askState.note) { const n = el("p", "ask-line", askState.note); n.setAttribute("role", "status"); box.appendChild(n); }
    if (askState.turns && askState.turns.length) {
      const f = el("button", "link-btn ask-forget", "Forget this conversation"); f.type = "button";
      f.disabled = askBusy();   // a running question would be stored after the forget
      f.addEventListener("click", forgetAsk);
      const p = el("p", "ask-forget-row"); p.appendChild(f); box.appendChild(p);
    }
    if (typeof SendNotes !== "undefined") SendNotes.fill(box);
    if (hadFocus) ta.focus();
  }
  // one turn: the question, the answer, its marks, Show links, drafts, Based on, notes, Read, Answered by, actions
  let askShowSeq = 0;   // counts Show clicks, so a slow one that found nothing never undoes a later one
  function askTurn(t, last) {
    const art = el("article", "ask-turn"); art.dataset.turn = String(t.n);
    const qq = el("p", "ask-you"); qq.append(el("b", null, "You asked: "), String(t.question || "")); art.appendChild(qq);
    const a = el("p", "ask-answer", String(t.answer || "")); art.appendChild(a);
    if (t.answer === ASK_NO_DOCS) {
      const p = el("p", "ask-line", "See "); const h2 = el("a", null, "Help"); h2.href = "#help"; p.append(h2, " for where to read more."); art.appendChild(p);
    }
    if (t.docs_changed) art.appendChild(el("p", "ask-mark", ASK_DOCS_CHANGED));
    if (t.show && t.show.length) {
      const p = el("p", "guide-show", "Show: ");
      t.show.forEach((s, i) => {
        const b = el("button", "link-btn", String(s.button || "")); b.type = "button";
        b.setAttribute("aria-label", `Show ${s.button}`);
        // the page opens and the button is pointed at, never pressed; the panel stays on this tab, which says so when
        // the button is not there
        b.addEventListener("click", async () => {
          const mine = ++askShowSeq;
          const got = await showButton(s.page, s.button);
          // only the latest Show click, and only while its page is still in view, writes the note
          if (!got && mine === askShowSeq && (!s.page || currentPage === s.page)) {
            askState.note = `${s.button} is not on the page now.`; drawAsk();
          }
        });
        if (i) p.append(", ");
        p.appendChild(b);
      });
      art.appendChild(p);
    }
    (t.drafts || []).forEach(d => {
      const box = el("div", "ask-draft");
      box.appendChild(el("p", "ask-draft-h", `Draft by ${t.backend || "the model"}: check it before you use it`));
      box.appendChild(el("p", "ask-draft-text", String(d.text || "")));
      const c = el("button", "secondary small-btn", "Copy"); c.type = "button";
      // the clipboard only: no route, no count, nothing recorded (D17)
      c.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(String(d.text || "")); c.textContent = "Copied"; }
        catch (e) { c.textContent = "Select and copy"; }
        setTimeout(() => { c.textContent = "Copy"; }, 1500);
      });
      box.appendChild(c); art.appendChild(box);
    });
    if (t.based_on && t.based_on.length) {
      art.appendChild(el("p", "ask-line", "Based on:"));
      const ul = el("ul", "ask-sources");
      t.based_on.forEach(b => {
        const li = el("li", null, String(b.title || b.id || ""));
        if (b.unreviewed) li.append(" ", el("span", "ask-unreviewed", ASK_UNREVIEWED));
        if (b.anchor) { const o = el("a", null, "Open"); o.href = b.anchor; o.setAttribute("aria-label", `Open ${b.title}`); li.append(" ", o); }
        ul.appendChild(li);
      });
      art.appendChild(ul);
    }
    (t.notes || []).forEach(n => art.appendChild(el("p", "ask-line", String(n))));
    // the meaning search needs the embedding service's consent: Allow opens its dialog (this question's last turn only)
    const c = last && askState.consent && askState.consentTurn === t.n ? askState.consent.backend_consent_required : null;
    if (c) {
      const b = el("button", "secondary small-btn ask-allow", `Allow ${c.backend} for Ask WIDER…`); b.type = "button";
      b.addEventListener("click", () => allowEmbedding(c, t));
      const p = el("p", "ask-row"); p.appendChild(b); art.appendChild(p);
    }
    if (t.read) art.appendChild(el("p", "ask-line", String(t.read)));
    if (t.answered_by) art.appendChild(el("p", "ask-line", String(t.answered_by)));
    const acts = el("div", "ask-acts");
    const mk = (text, fn) => { const b = el("button", "secondary small-btn", text); b.type = "button"; b.disabled = askBusy(); b.addEventListener("click", fn); acts.appendChild(b); return b; };
    mk("Simpler", () => askNow(t.question, "simpler", "again"));
    mk("More detail", () => askNow(t.question, "detail", "again"));
    if (t.n) {   // a turn that was not stored has nothing to mark
      const u = mk("Didn't help", () => markUnhelpful(t));
      u.disabled = false; u.setAttribute("aria-pressed", String(!!t.unhelpful));
    }
    art.appendChild(acts);
    return art;
  }
  // the tab holds one reviewer's turns, but every request is signed with the name stored now: when the name changed
  // (another tab, the top bar), redraw for the new name before anything is sent
  function askNameChanged() {
    const now = askAuthor();
    if (askState.author == null || now === askState.author || !askShown()) return false;
    askState.error = ""; askState.consent = null;
    askState.note = now ? `The name on this tab is now ${now}: the conversation shown is ${now}'s.` : "";
    drawAsk();
    return true;
  }
  async function askNow(question, depth, from, once) {
    question = String(question || "");
    if (askNameChanged() || askBusy() || !question.trim()) return;
    askState.error = ""; askState.note = "";
    const opts = { method: "POST", body: JSON.stringify({ question, page: askState.about || "general", depth: depth || null }) };
    if (once) opts.headers = { "X-Wider-Consent-Once": once };
    let r;
    try { r = await call("/api/assistant/ask", askSigned(opts)); }
    catch (e) { askState.error = e.message; drawAsk(); return; }   // the question stays in the box
    askState.job = r.job_id; askState.waiting = r.waiting || "Waiting for the model…"; askState.progress = "";
    drawAsk();
    pollAsk(r.job_id, from);
  }
  async function pollAsk(id, from) {
    for (let misses = 0; askState.job === id;) {
      await new Promise(r => setTimeout(r, (window.askPollMs || 600) * (misses ? 2.5 : 1)));
      if (askState.job !== id) return;
      let j;
      try { j = await call(`/api/jobs/${encodeURIComponent(id)}`); misses = 0; }
      catch (e) { if (++misses < 4) continue; askState.job = null; askState.error = e.message; drawAsk(); return; }
      if (j.status === "running") {
        if ((j.message || "") !== askState.progress) { askState.progress = j.message || ""; drawAsk(); }
        continue;
      }
      askState.job = null; askState.progress = "";
      if (j.status === "done") {
        const res = j.result || {};
        askState.consent = res.consent || null; askState.consentTurn = res.turn || null;
        if (res.kept === false && res.turn === 0) (askState.unkept = askState.unkept || []).push({ ...res, n: 0 });
        if (from === "box") askState.draft = "";
        await loadAskHistory(askState.author || askAuthor());
        const ta = q("#ask-q"); if (ta && ta.focus) ta.focus();
        return;
      }
      if (j.status === "failed") askState.error = j.error || "Ask WIDER could not answer. Ask again.";
      drawAsk();   // cancelled or interrupted: no turn, the question stays in the box
      return;
    }
  }
  async function stopAsk(btn) {
    const id = askState.job; if (!id) return;
    btn.disabled = true;
    try { await call(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); }
    catch (e) { askState.error = e.message; btn.disabled = false; drawAsk(); return; }
    askState.job = null; askState.progress = "";
    drawAsk();
    const ta = q("#ask-q"); if (ta && ta.focus) ta.focus();
  }
  async function markUnhelpful(t) {
    if (askNameChanged()) return;
    const value = !t.unhelpful;
    try { await call("/api/assistant/unhelpful", askSigned({ method: "POST", body: JSON.stringify({ turn: t.n, value }) })); }
    catch (e) { askState.error = e.message; drawAsk(); return; }
    t.unhelpful = value;
    askState.note = value ? `Turn ${t.n} marked Didn't help: it is kept with the questions WIDER's documents could not answer.` : `Turn ${t.n}: the mark is off.`;
    drawAsk();
  }
  async function allowEmbedding(info, t) {
    const got = await askBackendConsent(info);
    if (!got) return;
    askState.consent = null;
    if (got === "once") { askNow(t.question, t.depth || null, "again", `${info.kind}:${info.backend}:${info.operation}:${info.fingerprint}`); return; }
    askState.note = "Allowed: the next question searches by meaning too.";
    drawAsk();
  }
  async function forgetAsk() {
    if (askNameChanged() || askBusy()) return;
    const n = (askState.turns || []).length, who = askState.author;
    if (!(await askConfirm(`Deletes your ${n === 1 ? "1 question and answer" : `${n} questions and answers`} on this computer. This cannot be undone.`, "Forget this conversation"))) return;
    // the name changed while the question was open (the page may already show the new one): the count was another's
    if (askNameChanged() || askState.author !== who) return;
    try { await call("/api/assistant/history", askSigned({ method: "DELETE" })); }
    catch (e) { askState.error = e.message; drawAsk(); return; }
    askState.turns = []; askState.consent = null; askState.note = "Your conversation was deleted.";
    drawAsk();
    const ta = q("#ask-q"); if (ta && ta.focus) ta.focus();
  }
  // records.js calls this when the labelling queue fills or empties: the top-bar button and the tab follow
  window.askBoxRedraw = () => { askWays(currentPage); drawAsk(); };
  // a first name set anywhere (D33's "widername" event) or in another tab ("storage") redraws the tab
  document.addEventListener && document.addEventListener("widername", () => drawAsk());
  window.addEventListener && window.addEventListener("storage", (e) => { if (!e || e.key === "wider.reviewer" || e.key === null) askNameChanged(); });

  // Settings, "Ask WIDER": how much text one question may send (D10), as `wider assistant budget [N|--default]`;
  // saving goes through set_budget, so History records it with its author
  async function renderAskBudget(sel) {
    const card = q(sel); if (!card) return;
    let b;
    try { b = await call("/api/assistant/budget"); }
    catch (e) { card.innerHTML = `<h2>Ask WIDER</h2><p class="bad">${h(e.message)}</p>`; return; }
    const n = (x) => Number(x).toLocaleString("en-US");
    card.innerHTML = `<h2>Ask WIDER</h2>
      <p>How much text one question may send to the model: the page's guide, matching sections of WIDER's documents, your project's settings and status, and your earlier questions. More gives fuller answers; a model on this computer is slower with more.</p>
      <p data-budget-now>Now: ${n(b.budget)} tokens${b.value == null ? " (the default for the model's place)" : ""}.</p>
      <p>The default for the current model's place is ${n(b.default)} tokens.</p>
      <div class="row"><label for="ask-budget">Budget in tokens</label>
        <input id="ask-budget" type="number" min="${h(b.min)}" max="${h(b.max)}" step="100" value="${h(b.budget)}">
        <button type="button" class="secondary" data-budget-save>Save</button>
        <button type="button" class="secondary" data-budget-default${b.value == null ? " disabled" : ""}>Use the default</button>
        <span data-budget-msg role="status"></span></div>
      ${cmdFold("<code>wider assistant budget</code>, <code>wider assistant budget 8000</code>, <code>wider assistant budget --default</code>")}`;
    const msg = card.querySelector("[data-budget-msg]");
    const save = async (value, said) => {
      try { await call("/api/assistant/budget", { method: "POST", body: JSON.stringify({ value }) }); }
      catch (e) { msg.innerHTML = `<span class="bad">${h(e.message)}</span>`; return; }
      await renderAskBudget(sel);
      const m = card.querySelector("[data-budget-msg]"); if (m) m.textContent = said;
    };
    card.querySelector("[data-budget-save]").onclick = () => {
      const raw = card.querySelector("#ask-budget").value.trim();
      const v = Number(raw);
      if (!raw || !Number.isInteger(v)) { msg.innerHTML = `<span class="bad">Type a whole number from ${n(b.min)} to ${n(b.max)}.</span>`; return; }
      save(v, `Saved: ${n(v)} tokens.`);
    };
    card.querySelector("[data-budget-default]").onclick = () => save(null, "Saved: the default for the model's place.");
  }
  window.renderAskBudget = renderAskBudget;

  // ------------------------------------------------------------------ Explain this step
  qa(".explain-btn").forEach(b => b.onclick = () => {
    const box = q(`.stage-why[data-why="${b.dataset.explain}"]`); if (!box) return;
    box.hidden = !box.hidden;
    b.setAttribute("aria-expanded", String(!box.hidden));
    b.textContent = box.hidden ? "Explain this step" : "Hide the explanation";
  });

  // ------------------------------------------------------------------ home page
  const LOC_WORD = { local: "this computer", cloud: "remote", network: "Local network", unknown: "unknown", test: "test model (not for real reviews)" };
  const locTip = (b) => b.locality === "local" ? "nothing leaves this computer" : b.locality === "test" ? "built-in test model, not for real reviews"
    : b.destination ? `text is sent to ${b.destination}` : "text is sent off this computer";
  const locChip = (b) => b ? `<span class="chip loc-${h(b.locality)}" title="${h(locTip(b))}">${h(LOC_WORD[b.locality] || b.locality)}</span>` : "";
  const backendTxt = (b) => b ? `${h(b.name)} <span class="muted">(${h(b.model)})</span> ${locChip(b)}` : `<span class="muted">none chosen</span>`;
  const fmtInt = (n) => (n || 0).toLocaleString();

  function costHtml(c) {
    if (!c || !c.calls) return `<span class="muted">no model calls yet</span>`;
    const legacy = c.legacy_calls
      ? ` <span class="muted">(logged $${(c.logged_usd || 0).toFixed(2)}; $${(c.legacy_usd || 0).toFixed(2)} for ${fmtInt(c.legacy_calls)} older screening calls, ${h(c.legacy_label)})</span>`
      : "";
    const usd = `$${(c.usd || 0).toFixed(2)}${legacy}`;
    if (c.known) return `${usd}${c.locality === "local" && !c.usd ? ' <span class="muted">(local models only)</span>' : ""}`;
    return `${usd} <span class="muted">so far, plus ${c.unpriced_calls.toLocaleString()} calls without prices (set the model's prices on Settings)</span>`;
  }

  function infoRows(s) {
    const seed = s.seed;
    const tok = s.tokens || {};
    const chk = s.backend_check;
    const rows = [
      ["folder", `<code>${h(s.root)}</code>`],
      ["records", `${fmtInt(s.records)} unique of ${fmtInt(s.raw_records)} imported`
        + (s.records_provisional ? `<p class="warn-line">${h(s.records_provisional)}</p>` : "")],
      ["seed papers", seed.found == null ? `${seed.n} seed papers, coverage not checked yet` : `${seed.found}/${seed.n} found <span class="muted">(${h(seed.scope === "corpus" ? "in the deduplicated corpus" : "in at least one export")})</span>${seed.development ? `; development ${h(seed.development.found)}/${h(seed.development.n)}` : ""}`],
      ["AI model", (s.backends.llm_uses || []).length
        ? s.backends.llm_uses.map(b => `${backendTxt(b)} <span class="muted">for ${h(b.uses.join(", "))}</span>`).join("; ")
        : backendTxt(s.backends.llm)],
      ["embedding model", backendTxt(s.backends.embedding)],
      ["tokens", tok.calls ? `${fmtInt(tok.prompt + tok.completion)} <span class="muted">(${fmtInt(tok.prompt)} in, ${fmtInt(tok.completion)} out, ${fmtInt(tok.calls)} screening calls)</span>` : `<span class="muted">no model calls stored yet</span>`],
      ["cost", costHtml(s.cost)],
      ["last run", s.last_run ? `${h(stageTitle(s.last_run.stage))}: ${h(s.last_run.headline)} <span class="muted">(${h(fmtWhen(s.last_run.ran_at))})</span>` : `<span class="muted">nothing run yet</span>`],
      ["backend check", chk ? `${chk.results.map(r => `${r.role === "llm" ? "LLM" : "embeddings"} ${h(r.name)}${r.uses && r.uses.length ? ` (${h(r.uses.join(", "))})` : ""} ${r.ok ? '<span class="ok">ok</span>' : '<span class="bad">failed</span>'}`).join(", ")} <span class="muted">(${h(fmtWhen(chk.at))})</span>` : `<span class="muted">not run yet</span>`],
    ];
    return `<table class="summary-table">${rows.map(([k, v]) => `<tr><th scope="row" class="kv">${h(k)}</th><td>${v}</td></tr>`).join("")}</table>`;
  }

  // a card per project (ui-redesign D11): what needs the researcher, the next step as plain text, a running job, the
  // folder (its name; the full path in the tooltip and under Info) and records; no stage count and no segmented bar
  function projectCard(s, i) {
    if (s.error) return `<article class="project-card"><div class="pc-head"><h2>${h(s.title)}</h2></div><p class="bad small">${h(s.error)}</p><p class="small"><code>${h(s.root)}</code></p></article>`;
    const n = (s.needs_you || []).length;
    const needs = n ? `<span class="pc-needs">${Status.icon("needs")}${h(s.need_you_text || `${n} ${n === 1 ? "needs" : "need"} you`)}</span>`
      : '<span class="pc-calm">Nothing waits for you</span>';
    const running = (s.running || []).map(j => `<span class="pc-running">${Status.icon("running")}Running: ${h(j.label)}</span>`).join("");
    const next = s.next ? `Next: ${h(s.next.title)}` : "Every step is done";
    const forget = s.current
      ? ""   // the open project is not offered for removal (D32 F19)
      : `<button class="secondary small-btn" data-forget="${h(s.root)}" title="Remove from this list; the folder is not touched">Remove from list</button>`;
    const foot = `<button class="secondary small-btn" data-open="${h(s.root)}">Open<span class="sr-only"> ${h(s.title)}</span></button> ${forget}`;
    return `<article class="project-card${n ? " s-needs" : running ? " s-run" : ""}${s.current ? " current" : ""}" aria-label="${h(s.title)}">
      <div class="pc-state">${needs}${running}</div>
      <div class="pc-head"><h2>${h(s.title)}</h2>${s.current ? '<span class="chip">open</span>' : ""}<span class="spacer"></span>
        <button class="secondary small-btn" data-info="${i}" aria-expanded="false" aria-controls="pc-info-${i}" title="Project info"><svg class="icon" aria-hidden="true"><use href="#i-info"/></svg> Info</button></div>
      <p class="small"><span class="pc-folder" title="${h(s.root)}"><svg class="icon" aria-hidden="true"><use href="#i-folder"/></svg>${h(String(s.root).split(/[\\/]/).filter(Boolean).pop() || s.root)}</span> · ${s.records ? fmtInt(s.records) + " records" : s.raw_records ? fmtInt(s.raw_records) + " imported records" : "no records yet"}</p>
      <p class="pc-next">${next}</p>
      ${s.current && s.pending_review && s.pending_review.line ? `<p class="pc-pending"><a href="${h(s.pending_review.href)}">${h(s.pending_review.line)}</a></p>` : ""}
      <div class="pc-foot">${foot}</div>
      <div class="pc-info" id="pc-info-${i}" hidden>${infoRows(s)}</div>
    </article>`;
  }

  async function runCheck(btn) {
    const msg = btn.parentElement.querySelector("[data-check-msg]");
    btn.disabled = true; if (msg) msg.textContent = "probing… (a local model can take a minute)";
    try { await call("/api/backends/check", { method: "POST" }); }
    catch (e) { if (msg) msg.textContent = e.message; }
    btn.disabled = false;
    if (currentPage === "home") await loadHome(); else if (currentPage === "settings") await loadSettings();
  }
  const bindChecks = (root) => qa("[data-check]", root).forEach(b => b.onclick = () => runCheck(b));

  window.loadHome = async function () {
    let d;
    if (window.refreshStages) refreshStages();   // the sidebar and the cards below agree after CLI work
    try { d = await call("/api/home"); } catch (e) { q("#home-projects").innerHTML = `<p class="bad">${h(e.message)}</p>`; return; }
    q("#home-projects").innerHTML = d.projects.map(projectCard).join("");
    // an empty Home: the starts are open, and the list says where projects will appear
    q("#home-empty").hidden = d.projects.length > 0;
    if (!d.projects.length && q("#home-new").hidden && q("#home-open").hidden) showHomePanel("new", false);
    q("#home-note").innerHTML = (d.missing.length ? `<details class="home-missing"><summary>Show ${d.missing.length} hidden folder${d.missing.length === 1 ? "" : "s"}</summary>${d.missing.length === 1 ? "This folder no longer holds" : "These folders no longer hold"} a project: ${d.missing.map(m => `<code>${h(String(m.root || m).split(/[\\/]/).filter(Boolean).pop() || "")}</code>`).join(", ")}.</details> ` : "") +
      (d.recent_persisted ? "The list of recent projects is kept on this computer only." : "The list of recent projects is kept for this session only.");
    const cards = q("#home-projects");
    qa("[data-info]", cards).forEach(b => b.onclick = () => {
      const box = q("#pc-info-" + b.dataset.info); box.hidden = !box.hidden; b.setAttribute("aria-expanded", String(!box.hidden));
    });
    qa("[data-open]", cards).forEach(b => b.onclick = () => openProject(b.dataset.open, null));
    qa("[data-forget]", cards).forEach(b => b.onclick = async () => { try { await call("/api/projects/forget", { method: "POST", body: JSON.stringify({ path: b.dataset.forget }) }); await loadHome(); } catch (e) { errorToast(e.message); } });
  };
  // ------------------------------------------------------------------ Overview (ui-redesign D7, task 4.2)
  // In order: Continue (the recommended step, why, its button, and "Back to <page>, where you stopped" when the last
  // page differs), Needs attention (each needs-you and needs-review item with one action, open questions, review
  // packages still out), Research journey (every stage's state; last-result headlines only for the stages that
  // cannot hold a judgment, so a blind reviewer reads no model decision, miss count or seed paper result) and Project
  // summary (no percentages). `wider status` prints the same items and next step.
  const SAFE_HEADLINE = new Set(["setup", "construct", "import", "dedup", "enrich"]);
  const pageTitle = (key) => { const s = STAGES.find(x => x.key === key); return s ? s.title : (OTHER_PAGE[key] || key); };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  function overviewContinue() {
    const rec = recommended(), back = lastPage(), s = STAGES.find(x => x.key === rec);
    const why = rec === "model" ? (constructDefault()
        ? `No model for query creation is chosen yet; until you choose, the project default (${constructDefault()}) writes the proposals.`
        : "No model for query creation is set yet; every later step that asks a model needs one.")
      : s ? oneLine(s.why) || `It is the first step that is not done (${stateWord(s)}).` : "Every step is done; the results and the report wait on Recall and report.";
    return `<p class="ov-rec">Recommended: <b>${h(pageTitle(rec))}</b></p><p>${h(why)}</p>
      <div class="row"><a class="btn primary" href="#${h(rec)}" id="ov-go">Open ${h(pageTitle(rec))}</a>
      ${back && back !== rec ? `<a class="btn" href="#${h(back)}" id="ov-back">Back to ${h(pageTitle(back))}, where you stopped</a>` : ""}</div>`;
  }
  // each item carries a coloured left edge by its kind (owner inspection): rose needs you, amber needs review, blue to know
  function overviewNeeds(sum) {
    const items = (sum.needs_you || []).map(it => {
      if (it.kind !== "needs_review") return `<li class="ov-item ov-needs"><span>${Status.icon("needs")}<b>${h(it.title)}</b>: ${h(it.text)}</span>
        <a class="btn" href="#${h(it.stage)}">Open ${h(it.title)}</a></li>`;
      const st = STAGES.find(x => x.key === it.stage) || {}, rev = st.review || [];
      const drawn = rev.flatMap(r => r.samples || []);
      const keeps = rev.length > 1 ? rev.map(r => `<button type="button" class="btn secondary" data-ov-keep="${h(r.version)}">Keep version ${h(r.version)}</button>`).join(" ")
        : '<button type="button" class="btn secondary" data-ov-keep="">Keep this ranking</button>';
      return `<li class="ov-item ov-review"><span>${Status.icon("review")}<b>${h(it.title)}</b> needs review: ${h(it.text)}</span>
        <span class="row"><button type="button" class="btn secondary" data-ov-update>Update ranking</button> ${keeps}</span>
        ${drawn.length ? `<p class="small">Updating makes a new ranking version; the samples drawn from this one (${h(drawn.join(", "))}) keep their version.</p>` : ""}</li>`;
    });
    // a project upgraded to D4: why runs on development records wait, first, as it decides what can run
    if (sum.dev_gate) items.unshift(`<li class="ov-item ov-needs"><span>${Status.icon("needs")}<b>${h(sum.dev_gate.title)}</b>: ${h(sum.dev_gate.text)}</span>
        <a class="btn" href="#${h(sum.dev_gate.stage)}" data-goto-queue>Continue labelling</a></li>`);
    if (sum.open_questions) items.push(`<li class="ov-item ov-info"><span>${plural(sum.open_questions, "open question", "open questions")}</span> <a class="btn" href="#questions">Open Questions</a></li>`);
    if (sum.packages_out) items.push(`<li class="ov-item ov-info"><span>${plural(sum.packages_out, "review package", "review packages")} still out</span></li>`);
    return items.length ? `<ul class="ov-items">${items.join("")}</ul>` : "<p>Nothing waits for you.</p>";
  }
  // The board lists the same places as the journey line and the sidebar, open (M120 walk, 31 Oct: folded, it was not
  // found): each stage with its state in words as the sidebar has it, its steps with their state, and the model, review,
  // results and reporting pages at their sidebar places, marked "open any time" as apart from the numbered steps.
  function overviewBoard() {
    const stages = coreStages(), rec = recommended();
    const any = (k, title, word) => `<li class="ov-stage ov-any"><a href="#${h(k)}">${h(title)}</a> <span class="ov-state">${h(word)}</span></li>`;
    const lanes = SECTIONS.map(sec => {
      const mine = stages.filter(s => s.section === sec.number); if (!mine.length) return "";
      const items = mine.map(s => {
        const st = stageStatus(s), r = LAST_RESULTS[s.key];
        const last = !r ? "" : SAFE_HEADLINE.has(s.key) && r.headline ? r.headline : r.ran_at ? `last run ${fmtWhen(r.ran_at)}` : "";
        return [`<li class="ov-stage state-${h(st.cls)}"><a href="#${h(s.key)}">${h(s.title)}</a> <span class="ov-state">${h(st.text)}</span>${last ? `<span class="ov-last small">${h(last)}</span>` : ""}</li>`]
          .concat(s.key === "screen" ? [any("review", fixedTitle("review"), "open any time")] : [])
          .concat(s.key === "recall" ? [any("results", fixedTitle("results"), "open any time"), any("reporting", fixedTitle("reporting"), "open any time")] : []);
      }).flat();
      if (sec.number === 1) items.unshift(`<li class="ov-stage"><a href="#model">${h(fixedTitle("model"))}</a> <span class="ov-state">${CONSTRUCT_BACKEND ? "set" : (rec === "model" ? "current step, " : "") + h(modelUnset())}</span></li>`);
      return `<div class="ov-lane"><h3>${h(sec.number)} ${h(sec.title)} <span class="ov-state">${SEC_WORD[sectionState(mine, "overview")]}</span></h3><ul>${items.join("")}</ul></div>`;
    }).join("");
    return `<div class="ov-board"><p>The steps run in order; a page marked "open any time" can be used whenever you need it.</p>${lanes}</div>`;
  }
  function overviewSummary(sum) {
    const where = (b) => b ? `${b.name}${b.model ? ` (${b.model})` : ""}: ${b.locality_label || (b.locality && b.locality !== "unknown" ? b.locality : "unknown")}` : "none set";
    const llms = (sum.backends && sum.backends.llm_uses && sum.backends.llm_uses.length) ? sum.backends.llm_uses.map(b => `${where(b)} (${(b.uses || []).join(", ")})`)
      : [where(sum.backends && sum.backends.llm)];
    const rows = [
      ["Question", (sum.questions || []).join(" ") || "not written yet"],
      // no "Route" row: the route sentence stands once, under the title (#ov-route; inspection 1 Nov, item 15)
      ["Searches", (sum.platforms || []).length ? sum.platforms.join(", ") : "no platform chosen yet"],
      ["Records", `${sum.records || 0} after deduplication, ${sum.raw_records || 0} imported`
        + (sum.records_provisional ? `. ${sum.records_provisional}` : "")],
      ["Seed papers", sum.seed && sum.seed.n ? plural(sum.seed.n, "seed paper", "seed papers") : "none yet"],
      ["Reviewers", sum.reviewers ? plural(sum.reviewers, "reviewer", "reviewers") : "none yet"],
      ["Models and where they run", llms.join("; ")],
      ["Embedding model", where(sum.backends && sum.backends.embedding)],
      ["Online lookups", sum.lookups_allowed ? "allowed" : "off: nothing is sent; a lookup asks first"],
    ];
    return rows.filter(r => r[1]).map(([k, v]) => `<dt>${h(k)}</dt><dd>${h(v)}</dd>`).join("");
  }
  window.loadOverview = async function () {
    let sum;
    try { sum = await call("/api/project/summary"); }
    catch (e) { q("#ov-continue").innerHTML = `<p class="bad">${h(e.message)}</p>`; return; }
    devGate = !!sum.dev_gate;
    q("#ov-title").textContent = PROJECT_TITLE || sum.title || "Overview";
    q("#ov-route").textContent = sum.route || "";
    q("#ov-continue").innerHTML = overviewContinue();
    q("#ov-needs").innerHTML = overviewNeeds(sum);
    q("#ov-board").innerHTML = overviewBoard();
    q("#ov-summary").innerHTML = overviewSummary(sum);
    // the Ranking page's "Update ranking" flow (app.js rkUpdate: the check, its notice, consent by the usual 403)
    qa("[data-ov-update]", q("#ov-needs")).forEach(b => b.onclick = async () => {
      const msg = q("#ov-msg");
      try {
        msg.textContent = await rkUpdate({ start: (u, done) => Jobs.start(u, {}, async () => { done(); await loadOverview(); }),
                                           askChoice, clear: () => { q("#ov-msg").textContent = ""; } });
      }
      catch (e) { msg.innerHTML = `<span class="bad">${h(e.message)}</span>`; }
    });
    qa("[data-ov-keep]", q("#ov-needs")).forEach(b => b.onclick = async () => {
      const v = b.dataset.ovKeep, msg = q("#ov-msg");
      try { await call("/api/rank/keep", { method: "POST", body: JSON.stringify(v ? { version: Number(v) } : {}) }); }
      catch (e) { msg.innerHTML = `<span class="bad">${h(e.message)}</span>`; return; }
      msg.textContent = "Kept this ranking (in History; undo there).";
      if (window.loadStages) await loadStages();
      await loadOverview();
      q("#ov-msg").textContent = "Kept this ranking (in History; undo there).";
    });
  };
  // an error of a step that opens no dialog (Forget, opening a project from a card): a toast that stays until dismissed
  const errorToast = (text) => window.Jobs && Jobs.toast(text, "danger");

  async function openProject(path, msgEl, field) {
    try { await call("/api/projects/open", { method: "POST", body: JSON.stringify({ path }) }); }
    catch (e) { if (msgEl) fieldError(field, msgEl, e.message); else errorToast(e.message); return; }
    history.replaceState(null, "", location.pathname); location.reload();  // every page caches the old project's data; the bare address opens its last page
  }
  // Home's two panels: New project (the starting points, ui-redesign D8) and Open project; one open at a time
  const HOME_PANELS = { new: ["#home-create-toggle", "#home-new"], open: ["#home-open-toggle", "#home-open"] };
  function showHomePanel(which, focus = true) {
    for (const [k, [btn, box]] of Object.entries(HOME_PANELS)) {
      const on = k === which; q(box).hidden = !on; q(btn).setAttribute("aria-expanded", String(on));
    }
    if (!focus || !which) return;
    const f = which === "open" ? q("#ho-path") : q("#hn-h"); if (f) f.focus();
  }
  q("#home-create-toggle").onclick = () => showHomePanel(q("#home-new").hidden ? "new" : null);
  q("#home-open-toggle").onclick = () => showHomePanel(q("#home-open").hidden ? "open" : null);
  // each start opens its own first page; nothing about the start is stored (the next step comes from the project);
  // its command line is the one in the start card's own fold
  const START = { question: { page: "model", title: "A research question" },
                  strategy: { page: "query", title: "A search strategy you already have" },
                  records: { page: "import", title: "Records you already retrieved" } };
  let chosenStart = "question";
  q("#home-new").onclick = (e) => {
    const b = e.target && e.target.closest && e.target.closest("[data-start]"); if (!b) return;
    if (e.preventDefault) e.preventDefault();
    if (b.dataset.start === "existing") { showHomePanel("open"); return; }
    chosenStart = b.dataset.start;
    q("#hc-h").textContent = `Create a project: ${START[chosenStart].title}`;
    q("#hc-cmd").textContent = q(`#sc-cmd-${chosenStart}`).textContent;
    q("#home-create").hidden = false; q("#hc-path").focus();
  };
  q("#ho-open").onclick = () => openProject(q("#ho-path").value, q("#ho-msg"), q("#ho-path"));
  q("#ho-path").onkeydown = (e) => { if (e.key === "Enter") q("#ho-open").click(); };
  q("#hc-create").onclick = async () => {
    const body = { path: q("#hc-path").value, title: q("#hc-title").value,
                   questions: q("#hc-questions").value.split("\n"), platforms: q("#hc-platforms").value.split(",") };
    q("#hc-msg").textContent = "creating…";
    try { await call("/api/projects/create", { method: "POST", body: JSON.stringify(body) }); }
    catch (e) { fieldError(q("#hc-path"), q("#hc-msg"), e.message); return; }
    // replaceState fires no hashchange, so the page shown now (the old project's) does not record the start's page as its last page
    history.replaceState(null, "", "#" + START[chosenStart].page); location.reload();
  };
  ["#hc-path", "#hc-title"].forEach(sel => q(sel).onkeydown = (e) => { if (e.key === "Enter") q("#hc-create").click(); });

  // ------------------------------------------------------------------ help page
  const KEYS = [
    ["j / k", "next / previous record in the record drawer"],
    ["1 / 2 / 3", "the drawer's decisions when labelling: include / exclude / maybe"],
    ["1 / 2 / 3 / 4", "duplicate pairs: duplicates, keep A / duplicates, keep B / not duplicates / unsure"],
    ["n", "write a note on the open record"],
    ["t", "add a tag to the open record"],
    ["Enter", "open the record of the focused table row in the drawer"],
    ["Esc", "close the record drawer (or leave the note or tag field); back to the list on the Deduplication page, where the pair stays docked; cancel a dialog"],
    ["Ctrl+Z / Cmd+Z", "undo your last change (outside text fields)"],
    ["Ctrl+Shift+Z / Cmd+Shift+Z", "redo: put back your last undo (outside text fields)"],
    ["Left / Right", "move between the tabs of a tab list"],
    ["Esc", "close the Menu on a narrow window"],
    ["Tab, Shift+Tab", "move between controls; Enter or Space activates a button"],
    ["Skip to content", "first Tab stop on every page; jumps past the sidebar"],
  ];
  window.loadHelp = function () {
    q("#help-keys").innerHTML = '<thead class="sr-only"><tr><th>key</th><th>what it does</th></tr></thead>' + KEYS.map(([k, v]) => `<tr><th scope="row" class="kv"><kbd>${h(k)}</kbd></th><td>${h(v)}</td></tr>`).join("");
    // a redraw (the stage marks change while a run goes on) keeps the open summaries and the focused one
    const box = q("#help-stages"), was = document.activeElement;
    const opened = new Set(qa("details[open] > summary", box).map(x => x.dataset.focusKey));
    // "Page names and commands": every stage page with its description, why and commands, then the other pages
    box.innerHTML = STAGES.map((s, i) => `<details class="help-stage"${opened.has("help-" + s.key) ? " open" : ""}><summary data-focus-key="help-${h(s.key)}"><span class="dot-demo state-${h(s.state)}"></span> ${i + 1}. ${h(s.title)} <span class="muted small">${h(stateWord(s))}</span></summary>
      <p>${h(oneLine(s.description))}</p><p class="muted">${h(oneLine(s.why))}</p>
      <p class="small">${PAGES[s.key] ? `<a href="#${h(s.key)}">Open the page</a> · ` : ""}For scripts: ${scriptsLine(s.commands || [s.command])}</p></details>`).join("")
      + `<h3>Other pages</h3><table class="summary-table" id="help-pages" aria-label="Other pages"><thead class="sr-only"><tr><th>page</th><th>command</th></tr></thead>${FIXED_PAGES.map(p => `<tr><th scope="row" class="kv"><a href="#${h(p.key)}">${h(p.title)}</a></th><td>For scripts: ${scriptsLine(p.commands)}</td></tr>`).join("")}</table>`;
    if (was && !was.isConnected && was.dataset && was.dataset.focusKey) focusAfter(was);
  };

  // ------------------------------------------------------------------ settings page
  function gotoSetupStep(step) {
    // the task page of the Review plan (review-plan-and-question-window D17)
    location.hash = `setup?task=${step === "seed" ? "seeds" : step}`;
  }
  // Settings shows one section at a time, picked in the list on its left (owner, 29 Oct: option A); the choice is kept
  // while the page is open, and every section is filled on each load, so a hidden one is never stale
  function showSettingsPart(sec) {
    qa("[data-settings-sec]").forEach(b => { if (b.dataset.settingsSec === sec) b.setAttribute("aria-current", "true"); else b.removeAttribute("aria-current"); });
    qa("[data-settings-part]").forEach(c => { c.hidden = c.dataset.settingsPart !== sec; });
  }
  qa("[data-settings-sec]").forEach(b => b.onclick = () => showSettingsPart(b.dataset.settingsSec));
  window.loadSettings = async function () {
    let s, proj;
    try { [s, proj] = await Promise.all([call("/api/project/summary"), call("/api/project")]); }
    catch (e) { q("#settings-backends").innerHTML = `<p class="bad">${h(e.message)}</p>`; return; }
    const chk = s.backend_check;
    // every backend with Edit and Remove, the model per stage, and the consent cards with "Revoke"
    const [provs, embs, pcons, econs] = await Promise.all([call("/api/providers").catch(() => null), call("/api/embeddings").catch(() => null),
      call("/api/providers/consent").catch(() => []), call("/api/embeddings/consent").catch(() => [])]);
    const leaves = !Backends.whatLeavesHtml ? "" : Backends.whatLeavesHtml(provs, embs, (proj.prior_reviews && proj.prior_reviews.policy) || {}, proj.online_lookups || {});
    if (q("#settings-leaves-slot")) q("#settings-leaves-slot").innerHTML = leaves;
    q("#settings-backends").innerHTML = `<h2>Language models</h2><p class="section-intro">Where each model runs decides what leaves this computer.</p>
      <table class="summary-table" aria-label="Models in use"><tr><th scope="row" class="kv">AI model</th><td>${backendTxt(s.backends.llm)}</td></tr><tr><th scope="row" class="kv">embedding model</th><td>${backendTxt(s.backends.embedding)}</td></tr></table>
      ${Backends.settingsBackendsHtml(provs, embs, pcons, econs)}
      ${chk ? `<table class="check-table"><tr><th>role</th><th>model</th><th>result</th><th>time</th></tr>${chk.results.map(r => `<tr><td>${h(r.role)}${r.uses && r.uses.length ? ` (${h(r.uses.join(", "))})` : ""}</td><td>${h(r.name)} ${locChip(r)}</td><td>${r.ok ? '<span class="ok">ok</span>' : `<span class="bad">${h(r.error || "failed")}</span>`}</td><td>${r.elapsed_s != null ? h(r.elapsed_s) + " s" : ""}</td></tr>`).join("")}</table><p class="muted small">Checked ${h(fmtWhen(chk.at))}.</p>` : `<p class="muted small">Not checked yet.</p>`}
      <div class="row"><button class="secondary" data-check>Check models</button><span class="muted small" data-check-msg role="status"></span></div>
      <p class="send-note" data-send="backend_check" hidden></p>
      <p>${esc(Backends.checkBannerText())} Local embedding weights are never downloaded here.</p>
      ${cmdFold("<code>wider check-backends</code>")}`;
    const pol = (proj.prior_reviews && proj.prior_reviews.policy) || {};
    q("#settings-policy").innerHTML = `<h2>Prior-review data policy</h2>
      <table class="summary-table" aria-label="Prior-review data policy"><tr><th scope="row" class="kv">OpenAlex lookup</th><td>${pol.allow_openalex_lookup ? '<span class="chip loc-cloud">on</span> DOIs and titles of prior-review studies may be sent to OpenAlex' : '<span class="chip loc-local">off</span> nothing is sent'}</td></tr>
      <tr><th scope="row" class="kv">excerpts in LLM prompts</th><td>${pol.allow_llm_excerpts ? '<span class="chip loc-cloud">on</span> titles of prior-review studies may enter proposal prompts' : '<span class="chip loc-local">off</span> no prior-review content reaches a model'}</td></tr></table>
      <div class="row"><button class="secondary" data-step="prior">Change in the Prior reviews task of the Review plan</button></div>`;
    renderLookupsCard("#settings-lookups");
    renderAskBudget("#settings-assistant");   // guide-assistant 8.3
    if (window.SeedNames) SeedNames.renderPolicy("#settings-seeds");   // seeds.js: `wider seeds policy`
    // the Advanced protocol card (setup.js): every setting with Set and Reset, as `wider protocol show|set|reset`
    if (window.loadProtocol) await loadProtocol().catch(e => { q("#protocol-editor").innerHTML = `<p class="bad">${h(e.message)}</p>`; });
    const qsum = await call("/api/questions/summary").catch(() => null);
    q("#settings-doubts").innerHTML = `<h2>When the tool is in doubt</h2>
      <p>${qsum ? (qsum.mode === "ask" ? "Questions open in a dialog as they arrive." : "Questions wait on the Questions page.") + ` ${qsum.open} open.` : "not readable"}</p>
      <div class="row"><a href="#questions">Open the Questions page to change this or answer them</a></div>`;
    const home = await call("/api/home").catch(() => null);
    q("#settings-computer").innerHTML = `<h2>This computer</h2>
      <p class="small">Project folder <code>${h(s.root)}</code>. ${home && home.recent_persisted ? "The recent-projects list (folder paths only) is kept in WIDER's settings folder on this computer." : "The recent-projects list is kept for this session only."} The interface is served on 127.0.0.1 only.</p>`;
    qa("[data-step]", q("#page-settings")).forEach(b => b.onclick = () => gotoSetupStep(b.dataset.step));
    // opens the model page and runs the detection there (only on this press, never on opening the page)
    qa("[data-find-backends]", q("#page-settings")).forEach(b => b.onclick = () => {
      go("model");
      setTimeout(() => { const f = q("#backend-find"); if (f) { f.click(); f.focus(); } }, 400);
    });
    const bmsg = () => q("#settings-backends [data-check-msg]");
    const fail = (e) => { const m = bmsg(); if (m) m.innerHTML = `<span class="bad">${h(e.message)}</span>`; };
    // an LLM backend's form (model.js) opens here; an embedding model is edited on its Ranking card
    let last = null;   // the backend whose form is open: Cancel gives the keyboard back to its Edit button
    const openForm = provs && typeof ModelPage !== "undefined" ? ModelPage.mountForm(q("#settings-backend-form"), provs, {
      listId: "settings-backend-model-list", idPrefix: "st-", onSaved: async (name) => { await loadSettings(); const m = bmsg(); if (m) m.textContent = `saved ${name}`; },
      onCancel: () => { const b = q(`[data-settings-edit="${CSS.escape(last || "")}"]`); if (b) b.focus(); } }) : null;
    qa("[data-settings-edit]", q("#page-settings")).forEach(b => b.onclick = () => {
      const name = b.dataset.settingsEdit; last = name;
      if (b.dataset.settingsRole !== "embedding") { const p = provs.providers.find(x => x.name === name); if (p && openForm) openForm(Backends.formValuesFromBackend(p)); return; }
      go("rank");
      const sel = `[data-pact="edit"][data-name="${CSS.escape(name)}"]`;
      let tries = 0;
      const open = () => { const e = q(sel); if (e) e.click(); else if (++tries < 20) setTimeout(open, 150); };
      setTimeout(open, 300);
    });
    // Remove asks first, as `wider providers remove NAME` / `wider embed remove NAME`
    qa("[data-settings-remove]", q("#page-settings")).forEach(b => b.onclick = async () => {
      const name = b.dataset.settingsRemove, emb = b.dataset.settingsRole === "embedding";
      if (!(await askConfirm(`Remove ${emb ? "embedding" : "AI model"} ${name}?`, "Remove"))) return;
      try { await call(`${emb ? "/api/embeddings/" : "/api/providers/"}${encodeURIComponent(name)}`, { method: "DELETE" }); }
      catch (e) { fail(e); return; }
      window.dispatchEvent(new CustomEvent("wider:changed"));
      await loadSettings();
    });
    // the model per stage: an empty choice removes the override (the project default is used)
    qa("[data-settings-stage]", q("#page-settings")).forEach(sel => sel.onchange = async () => {
      try { await call("/api/providers/stage", { method: "POST", body: JSON.stringify({ stage: sel.dataset.settingsStage, name: sel.value || null }) }); }
      catch (e) { await loadSettings(); fail(e); return; }   // a refused choice (e.g. an embedding-only backend) shows why
      window.dispatchEvent(new CustomEvent("wider:changed"));
      await loadSettings();
    });
    qa("[data-revoke]", q("#settings-consent")).forEach(b => b.onclick = async () => {
      const base = b.dataset.revokeKind === "embedding" ? "/api/embeddings/" : "/api/providers/";
      b.disabled = true;
      try { await call(`${base}${encodeURIComponent(b.dataset.revoke)}/consent`, { method: "DELETE" }); }
      catch (e) { b.disabled = false; b.insertAdjacentHTML("afterend", ` <span class="bad small">${h(e.message)}</span>`); return; }
      const row = `#settings-consent [data-focus-key="${CSS.escape(b.closest("tr").dataset.focusKey)}"]`;
      await loadSettings();
      focusAfter(null, row);   // the Revoke button is gone; the backend's row keeps the focus
    });
    bindChecks(q("#settings-backends"));
  };

  // the open project's name under Home in the sidebar
  call("/api/project/summary").then(s => {
    const el = q("#nav-project"); el.textContent = s.title; el.title = "Open project: " + s.root;
    if (!!s.dev_gate !== devGate) { devGate = !!s.dev_gate; renderFooter(currentPage); }
  }).catch(() => {});
})();
