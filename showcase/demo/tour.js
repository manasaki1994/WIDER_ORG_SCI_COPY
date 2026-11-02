/* WIDER interactive demo: the guided tour, the concept cards and the end card (openspec change interactive-demo,
 * design D7, D8; the Quick tour, the chapters and narrow screens: change demo-chapters-and-video, D1-D4, D7).
 *
 * The steps come from tour.yaml through snapshot.js (DEMO_SNAPSHOT.tour), with every number already filled in from
 * the recording. The card sits in a panel docked on the right, so it covers nothing on the page; the step's element
 * gets an outline. A step that asks the visitor to do something waits until the demo's state shows it done (Demo
 * calls back after every change), and Skip does it the way Reviewer A did. The tour only reads and calls the demo's
 * own handlers: nothing here sends anything anywhere.
 */
(function () {
  "use strict";
  const W = window, D = W.Demo, TOUR = (W.DEMO_SNAPSHOT || {}).tour;
  if (!D || !TOUR || !Array.isArray(TOUR.steps) || !TOUR.steps.length) return;
  const doc = W.document, STEPS = TOUR.steps, CONCEPTS = TOUR.concepts || [], END = TOUR.end || null;
  const CHAPTERS = (TOUR.chapters || []).filter((c) => STEPS.some((s) => s.stage === c.stage));
  const timing = { poll: 100, tries: 30, perWord: 300, min: 5000, slow: 1.6 };
  const PASSED = "This step already happened in this demo. Reset to replay it from the start, or read on.";
  const KEYS = "Keys: → next, ← back, Space play or pause, Esc hide the tour.";

  let at = 0, panel = null, marked = null, timer = null, busy = false, card = null;
  let play = "idle";   // idle, playing, paused
  let slow = false;

  // ---- small helpers ----------------------------------------------------------------------------------------------
  function el(tag, attrs, text) {
    const e = doc.createElement(tag);
    Object.keys(attrs || {}).forEach((k) => e.setAttribute(k, attrs[k]));
    if (text != null) e.textContent = text;
    return e;
  }
  function button(id, text, onclick, cls) {
    const b = el("button", { type: "button", id, class: cls || "demo-quiet" }, text);
    b.addEventListener("click", onclick);
    return b;
  }
  const byId = (id) => doc.getElementById(id);
  const order = () => D.order();
  const cpIndex = (name) => order().indexOf(name);
  const now = () => cpIndex(D.state.checkpoint);
  const passed = (s) => cpIndex(s.checkpoint) < now();
  const ahead = (s) => cpIndex(s.checkpoint) > now();
  const page = () => String(W.location.hash || "").replace(/^#/, "") || "home";
  // the two paths through the same steps (D1): the Quick tour (the core steps) and all chapters; `at` stays an index
  // of STEPS, and a step off the chosen path (opened by a resume) moves on to the path's next step
  const PATHS = { core: STEPS.map((s, i) => (s.core ? i : -1)).filter((i) => i >= 0), all: STEPS.map((s, i) => i) };
  const quick = () => D.state.path === "core" && PATHS.core.length > 0;
  const path = () => (quick() ? PATHS.core : PATHS.all);
  const after = (n) => path().find((i) => i > n);
  const before = (n) => path().filter((i) => i < n).pop();
  const seen = () => (Array.isArray(D.state.seen) ? D.state.seen : (D.state.seen = []));
  function shown(e) {
    for (let n = e; n && n !== doc.documentElement; n = n.parentElement || n.parent) if (n.hidden) return false;
    return true;
  }
  const filled = (e) => !!((e.children && e.children.length) || String(e.textContent || "").trim());
  function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }
  const overlay = () => D.state.overlay || {};
  const get = (path) => { try { const a = D.answer("GET", path); return a && a.status === 200 ? a.json : null; } catch (e) { return null; } };
  async function refresh(key) {
    try { if (typeof W.loadStages === "function") await W.loadStages(); } catch (e) { /* the page shows its own error */ }
    try { if (typeof W.showPage === "function") W.showPage(key); } catch (e) { /* idem */ }
  }

  // ---- what each kind of action step waits for (D7) ---------------------------------------------------------------
  function termsChanged() {
    const first = ((W.DEMO_SNAPSHOT || {}).terms || { blocks: [] }).blocks.map((b) => b.terms);
    const t = overlay().terms;
    return Array.isArray(t) && JSON.stringify(t) !== JSON.stringify(first);
  }
  function termsLine() {
    const r = W.DemoTerms && W.DemoTerms.result();
    return r ? `Now: ${r.records} of ${r.of} records found, ${r.includes} of the ${r.ofIncludes} include papers.` : "";
  }
  const dedupBase = () => ((D.recorded("/api/dedup", order()[0]) || {}).flow || {}).left;
  function dedupLine() {
    const applied = overlay().applied || {}, vals = Object.keys(applied).map((k) => applied[k]);
    const left = ((get("/api/dedup") || {}).flow || {}).left, base = dedupBase();
    if (vals.some((v) => v === "manual_same")) return `${base} → ${left} unique records.`;
    if (vals.some((v) => v === "manual_uncertain") && !vals.some((v) => v === "manual_different"))
      return `${base} → ${left} unique records: a pair marked Unsure changes no count.`;
    return `${base} → ${left} unique records: a pair decided as different papers changes no count.`;
  }
  function devProgress() {
    const dev = (((get("/api/samples") || {}).samples) || {}).development;
    if (!dev || !dev.progress) return "";
    const done = Object.values(dev.progress.reviewers || {}).reduce((n, r) => Math.max(n, r.done || 0), 0);
    return `Development sample: ${done} of ${dev.n} records labelled.`;
  }
  const reached = (name) => cpIndex(name) >= 0 && now() >= cpIndex(name);
  function screenLine(kind) {
    const J = W.DemoJobs;
    return J ? J.summary(kind) : "Done: the records are screened.";
  }
  const KINDS = {
    terms: {
      done: () => termsChanged(),
      wait: "Your turn: remove or add a term in the panel.",
      report: () => termsLine(),
      async skip() {
        const T = W.DemoTerms, blocks = overlay().terms || [];
        if (!T || !blocks.length) return "";
        const term = blocks[0].includes("suggestion*") ? "suggestion*" : blocks[0][0];
        if (term) T.remove(0, term);
        return `Done for you: ${term} removed. ${termsLine()}`;
      },
    },
    dedup: {
      done: () => Object.keys(overlay().applied || {}).length > 0,
      wait: () => (Object.keys(overlay().pairs || {}).length
        ? "You decided a pair. The count changes only after “Apply decisions and check again”."
        : "Your turn: open a pair, decide it, then press “Apply decisions and check again”."),
      report: () => dedupLine(),
      async skip() {
        const words = { manual_same: "duplicate", manual_different: "not_duplicate", manual_uncertain: "uncertain" };
        const mine = overlay().pairs || {}, open = ((D.recorded("/api/dedup", order()[0]) || {}).queue || [])
          .filter((r) => !mine[`${Math.min(r.raw_a, r.raw_b)}-${Math.max(r.raw_a, r.raw_b)}`]);
        const later = (D.recorded("/api/dedup", order()[1]) || {}).queue || [];
        const pick = open.map((r) => ({ r, as: (later.find((x) => x.raw_a === r.raw_a && x.raw_b === r.raw_b) || {}).decision }))
          .find((p) => words[p.as]);
        if (pick) await D.answer("POST", "/api/dedup/decide", { raw_a: pick.r.raw_a, raw_b: pick.r.raw_b, decision: words[pick.as], by: "Reviewer A" });
        await D.answer("POST", "/api/dedup/run");
        await refresh("dedup");
        const left = ((get("/api/dedup") || {}).flow || {}).left;
        const how = pick && pick.as === "manual_same" ? "decided as the same paper" : "decided as Reviewer A decided it";
        return `Done for you: one pair ${how} and applied; ${left} unique records.`;
      },
    },
    rank: {
      done: () => reached(order()[1]),
      wait: "Your turn: press “Rank”.",
      report: () => "Done: every record now has a score and a rank.",
      async skip() { await D.answer("POST", "/api/rank/run"); await refresh("rank"); return "Done for you: the records are ranked."; },
    },
    draw: {
      done: () => reached(order()[2]),
      wait: "Your turn: press “Draw samples from the ranking”.",
      report: () => "Done: two samples drawn.",
      async skip() { await D.answer("POST", "/api/samples/draw"); await refresh("sample"); return "Done for you: two samples drawn."; },
    },
    label: {
      done: () => Object.keys(overlay().labels || {}).length > 0,
      wait: "Your turn: open a record that is not labelled yet and choose Include, Exclude or Maybe.",
      report: () => devProgress(),
      moveOn: true,   // Skip goes on: Reviewer A's labels stay as they are
    },
    screen: {
      done: () => reached("screened"),
      // ui-redesign D4: the run waits for the open development labels
      wait: () => (D.state.job ? "Running: the decisions come in below “Run”."
        : W.DemoActions && W.DemoActions.devOpen() ? "Your turn: label the development records still open " +
          "(“Continue labelling”), then press “Run”; or press Skip and the demo labels them as Reviewer A did."
          : "Your turn: press “Run”."),
      report: () => screenLine("screen"),
      async skip() {
        if (!W.DemoJobs || !W.DemoJobs.runNow("screen")) return "";
        await refresh("screen");
        return `Done for you. ${screenLine("screen")}`;
      },
    },
    screen2: {
      done: () => reached("screened2"),
      wait: () => (D.state.job ? "Running: the decisions come in below “Run”." : "Your turn: choose the second setting and press “Run”."),
      report: () => screenLine("screen2"),
      async skip() {
        if (!W.DemoJobs || !W.DemoJobs.runNow("screen2")) return "";
        await refresh("screen");
        return `Done for you. ${screenLine("screen2")}`;
      },
    },
    recall: {
      done: () => !!(overlay().results || {}).recall,
      wait: "Your turn: press “Estimate recall”.",
      report: () => "Done: recall is estimated. Read the interval next to the number, not the number alone.",
      async skip() {
        await D.answer("GET", "/api/recall?protocol=main&result_set=1&sample=validation");
        await refresh("recall");
        const r = (overlay().results || {}).recall;
        return r ? `Done for you: ${r.result.headline}` : "";
      },
    },
  };
  // a step is waiting when it asks for an action that is not done (a passed step counts as done)
  const kind = (s) => (s && s.action ? KINDS[s.action] || null : null);
  const isDone = (s) => !kind(s) || passed(s) || !!kind(s).done();
  // the pages a later checkpoint changes (the duplicate pairs are decided, the records ranked, ...): a passed step
  // there says the page now shows a later moment
  const MOVES = ["dedup", "rank", "draw", "screen", "screen2", "recall"];
  const CHANGING = new Set(STEPS.filter((s) => MOVES.includes(s.action)).map((s) => s.page));
  const say = (v) => (typeof v === "function" ? v() : v || "");

  // ---- the panel ---------------------------------------------------------------------------------------------------
  function buildPanel() {
    if (panel) return panel;
    panel = el("aside", { id: "demo-tour", class: "demo-tour", role: "complementary", "aria-label": "Guided tour" });
    const head = el("div", { class: "demo-tour-head" });
    head.appendChild(el("span", { id: "demo-tour-where", class: "demo-tour-where" }));
    head.appendChild(button("demo-tour-hide", "Hide", () => hide(), "demo-quiet demo-tour-small"));
    panel.appendChild(head);
    panel.appendChild(el("p", { id: "demo-tour-intro", class: "demo-tour-intro" }));
    panel.appendChild(el("span", { id: "demo-tour-who", class: "demo-who" }));
    panel.appendChild(el("h2", { id: "demo-tour-title", tabindex: "-1" }));
    panel.appendChild(el("div", { id: "demo-tour-body", class: "demo-tour-body" }));
    panel.appendChild(el("p", { id: "demo-tour-tradeoff", class: "demo-tour-tradeoff" }));
    const pass = el("div", { id: "demo-tour-passed", class: "demo-tour-passed" });
    pass.appendChild(el("p", {}, PASSED));
    pass.appendChild(button("demo-tour-reset", "Reset the demo", () => D.reset(true)));
    panel.appendChild(pass);
    panel.appendChild(el("p", { id: "demo-tour-report", class: "demo-tour-report", role: "status", "aria-live": "polite" }));
    panel.appendChild(button("demo-tour-concept", "", () => { const s = STEPS[at]; if (s && s.concept) openConcept(s.concept); }, "demo-quiet demo-tour-link"));
    const nav = el("div", { class: "demo-tour-nav" });
    nav.appendChild(button("demo-tour-back", "Back", () => back()));
    nav.appendChild(button("demo-tour-skip", "Skip", () => skip()));
    nav.appendChild(button("demo-tour-next", "Next", () => next(), "demo-tour-primary"));
    panel.appendChild(nav);
    const more = el("div", { class: "demo-tour-more" });
    more.appendChild(button("demo-tour-chapters", "Chapters", () => openChapters()));
    more.appendChild(button("demo-tour-play", "Play", () => togglePlay()));
    more.appendChild(button("demo-tour-stop", "Stop", () => stop()));
    more.appendChild(button("demo-tour-repeat", "Repeat step", () => go(at)));
    more.appendChild(button("demo-tour-speed", "Speed: normal", () => { slow = !slow; drawControls(); }));
    panel.appendChild(more);
    panel.appendChild(el("p", { class: "demo-tour-keys" }, KEYS));
    doc.body.appendChild(panel);
    return panel;
  }
  function drawControls() {
    const s = STEPS[at], k = kind(s), last = after(at) === undefined;
    byId("demo-tour-back").disabled = before(at) === undefined;
    byId("demo-tour-next").textContent = last ? "Finish" : "Next";
    const sk = byId("demo-tour-skip");
    sk.hidden = !k || isDone(s) || !(k.skip || k.moveOn);
    sk.setAttribute("title", "The demo does this step for you, as Reviewer A did it");
    byId("demo-tour-play").textContent = play === "playing" ? "Pause" : play === "paused" ? "Resume" : "Play";
    byId("demo-tour-play").setAttribute("aria-pressed", String(play === "playing"));
    byId("demo-tour-stop").disabled = play === "idle";
    byId("demo-tour-speed").textContent = slow ? "Speed: slow" : "Speed: normal";
  }
  function drawReport() {
    const s = STEPS[at], k = kind(s), r = byId("demo-tour-report");
    if (!r) return;
    if (!k) { r.textContent = ""; r.hidden = true; return; }
    r.hidden = false;
    if (passed(s)) r.textContent = "Done in this demo already.";
    else if (k.done()) r.textContent = say(k.report);
    else r.textContent = say(k.wait);
    r.classList.toggle("demo-tour-waiting", !isDone(s));
  }
  function draw() {
    const s = STEPS[at];
    byId("demo-tour-where").textContent = where(s);
    // the chapter's purpose stands above its first step on the chosen path (D2)
    const intro = byId("demo-tour-intro"), ch = CHAPTERS.find((c) => c.stage === s.stage);
    const first = path().find((i) => STEPS[i].stage === s.stage);
    intro.textContent = ch && (first === at || (first === undefined && STEPS.findIndex((x) => x.stage === s.stage) === at)) ? ch.intro : "";
    intro.hidden = !intro.textContent;
    const who = byId("demo-tour-who");
    who.textContent = s.who;
    who.classList.toggle("demo-who-you", s.who === "You decide");
    who.classList.toggle("demo-who-wider", s.who !== "You decide");
    byId("demo-tour-title").textContent = s.title;
    const body = byId("demo-tour-body");
    body.textContent = "";
    (s.body || []).forEach((p) => body.appendChild(el("p", {}, p)));
    const t = byId("demo-tour-tradeoff");
    t.textContent = "";
    t.hidden = !s.tradeoff;
    if (s.tradeoff) { t.appendChild(el("b", {}, "Trade-off: ")); t.appendChild(doc.createTextNode(s.tradeoff)); }
    byId("demo-tour-passed").hidden = !(passed(s) && CHANGING.has(s.page));
    const c = byId("demo-tour-concept"), concept = CONCEPTS.find((x) => x.id === s.concept);
    c.hidden = !concept;
    c.textContent = concept ? `More: ${concept.title}` : "";
    drawReport();
    drawControls();
    dock();
  }
  // "Quick tour · step S of N", or "Chapter C of K · <stage> · step S of T" (D1)
  function where(s) {
    if (quick()) {
      const i = PATHS.core.indexOf(at);
      return i >= 0 ? `Quick tour · step ${i + 1} of ${PATHS.core.length}` : `Quick tour · ${s.stage} (an extra step)`;
    }
    const c = CHAPTERS.findIndex((x) => x.stage === s.stage), mine = PATHS.all.filter((i) => STEPS[i].stage === s.stage);
    return `Chapter ${c + 1} of ${CHAPTERS.length} · ${s.stage} · step ${mine.indexOf(at) + 1} of ${mine.length}`;
  }

  // ---- narrow screens (D7): the card docks at the bottom, above the app's fixed main button -----------------------
  const narrow = () => { try { return !!W.matchMedia && W.matchMedia("(max-width: 700px)").matches; } catch (e) { return false; } };
  function dock() {
    if (!panel || !panel.style) return;
    const main = byId("tb-main");
    const full = main && shown(main) && ((main.children && main.children.length) || String(main.textContent || "").trim());
    panel.style.bottom = narrow() ? `${full ? main.offsetHeight || 0 : 0}px` : "";
  }
  if (W.addEventListener) W.addEventListener("resize", () => dock());

  // ---- pointing at the step's element ------------------------------------------------------------------------------
  const calm = () => { try { return W.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) { return false; } };
  function mark(target, jump) {
    if (marked && marked.classList) marked.classList.remove("demo-target");
    marked = target;
    if (!target) return;
    target.classList.add("demo-target");
    const behavior = jump || calm() ? "auto" : "smooth";
    // a tall element starts at the top of the screen, below the Demo Mode bar, so its first lines are not cut off
    const tall = target.getBoundingClientRect && target.getBoundingClientRect().height > (W.innerHeight || 0) * 0.6;
    // a narrow screen has the card at the bottom: the element goes to the top, just below the Demo Mode bar (D7)
    const small = narrow();
    if (small && target.style) { const bar = byId("demo-bar"); target.style.scrollMarginTop = `${((bar && bar.offsetHeight) || 0) + 8}px`; }
    if (target.scrollIntoView) try { target.scrollIntoView({ block: tall || small ? "start" : "center", behavior }); } catch (e) { /* old browser */ }
  }
  // the page's answers come in after the first scroll and push the target down: scroll once more if it is off screen
  function settle(seq) {
    setTimeout(() => {
      if (seq !== shownSeq || !marked || !marked.getBoundingClientRect) return;
      const r = marked.getBoundingClientRect(), h = W.innerHeight || 0;
      const off = r.height > h * 0.6 ? r.top < 0 || r.top > h * 0.4 : r.top < 0 || r.bottom > h;
      if (off) mark(marked, true);
    }, 700);
  }
  // a target inside a page tab that is not chosen (app.js .page-tabs, e.g. Screening's "2 Decisions and result sets"):
  // the tour chooses that tab, as the visitor would, instead of pointing at the page's heading
  function reveal(t) {
    for (let n = t; n && n !== doc.documentElement; n = n.parentElement || n.parent) {
      if (!n.hidden || !n.id) continue;
      const tab = doc.querySelector(`[aria-controls="${n.id}"]`);
      const bar = tab && tab.getAttribute("role") === "tab" && tab.closest(".page-tabs");
      if (bar && typeof bar.pick === "function") bar.pick(tab.dataset.tab);
    }
  }
  function point(s) {
    const t = byId(s.target);
    if (t && !shown(t)) reveal(t);
    // a step whose target a button fills (the Recall tables come from "Estimate recall"): an empty target is filled
    // first, as the visitor would press the button, so the step never points at nothing (tester 1 Nov 2026)
    if (t && shown(t) && s.press && !filled(t)) {
      const b = byId(s.press);
      if (!b || b.disabled) return false;   // the page is still loading: look again
      b.click();
    }
    if (t && shown(t)) { mark(t); return true; }
    const head = byId(`page-${s.page}`);
    mark(head && shown(head) ? head : null);
    return false;
  }
  function travel(s, seq) {
    if (page() !== s.page) W.location.hash = s.page;
    let tries = 0;
    const look = () => {
      if (seq !== shownSeq) return;
      if (point(s)) { settle(seq); return; }
      if (++tries >= timing.tries) return;
      setTimeout(look, timing.poll);
    };
    look();
  }

  // ---- moving through the steps -----------------------------------------------------------------------------------
  let shownSeq = 0;
  function go(n) {
    if (n < 0 || n >= STEPS.length) return;
    at = n;
    buildPanel();
    open();
    busy = true;
    try {
      D.state.step = STEPS[at].id;
      if (!seen().includes(STEPS[at].id)) seen().push(STEPS[at].id);
      D.save();
    } finally { busy = false; }
    draw();
    travel(STEPS[at], ++shownSeq);
    const title = byId("demo-tour-title");
    if (title && title.focus && play === "idle") try { title.focus({ preventScroll: true }); } catch (e) { title.focus(); }
    if (play === "playing") schedule();
  }
  // a step's action done as Reviewer A did it; a screening run first labels the open development records as Reviewer A
  // labelled them (screening.js), and says so (D3)
  async function doFor(s) {
    const pre = s.action === "screen" ? await labelDev() : "";
    return together([pre, await kind(s).skip()]);
  }
  // the open development records labelled as Reviewer A labelled them (actions.js, the labelling page's own POST),
  // so the page's Run is not refused for them
  const devLeft = () => (W.DemoActions ? W.DemoActions.devOpen() : 0);
  async function labelDev() {
    const n = devLeft() ? await W.DemoActions.fillDev() : 0;
    return n ? `Done for you: the ${plural(n, "development record")} still open got Reviewer A's labels.` : "";
  }
  const arriving = (to) => quick() && STEPS[to].action === "screen" && !isDone(STEPS[to]) && devLeft() > 0;
  // one notice for several actions: "Done for you: a. B. C."
  function together(texts) {
    const parts = texts.filter(Boolean);
    if (parts.length < 2) return parts[0] || "";
    return "Done for you: " + parts.map((t, i) => {
      const x = t.replace(/^Done for you[:.]\s*/, "");
      return i ? x.charAt(0).toUpperCase() + x.slice(1) : x;
    }).join(" ");
  }
  const later = (s) => kind(s) && kind(s).skip && MOVES.includes(s.action) && !isDone(s);
  // from step `from` to step `to` of the path: every action of a step in between that is not done is done first, in
  // order; the step's own action only when a later step needs its result (D3)
  const own = (from, to, between) => { const s = STEPS[from], k = kind(s); return !!(k && k.skip && !isDone(s) && (between.length || ahead(STEPS[to]))); };
  async function gap(from, to) {
    const between = STEPS.slice(from + 1, to).filter(later), s = STEPS[from], texts = [];
    try {
      if (own(from, to, between)) texts.push(await doFor(s));
      for (const x of between) if (!isDone(x)) texts.push(await doFor(x));
      if (arriving(to)) texts.push(await labelDev());
    } catch (e) { /* the page shows what it has */ }
    return together(texts);
  }
  // Next past a step the visitor has not done, into a step that needs it (the ranking, the samples), does it first
  async function next() {
    const to = after(at);
    if (to === undefined) { stop(); openEnd(); return; }
    // nothing to do first: the step changes at once (the keys and the tests rely on it)
    const between = STEPS.slice(at + 1, to).filter(later);
    if (!between.length && !own(at, to, between) && !arriving(to)) { go(to); return; }
    const text = await gap(at, to);
    if (text) D.notice(text);
    go(to);
  }
  function back() { const to = before(at); if (to !== undefined) go(to); }
  async function skip() {
    const s = STEPS[at], k = kind(s);
    if (!k || isDone(s)) return;
    if (k.moveOn) { await next(); return; }
    if (!k.skip) return;
    const r = byId("demo-tour-report");
    let text = "";
    try { text = await doFor(s); } catch (e) { text = "The demo could not do this step. Try it on the page, or press Next."; }
    draw();
    if (r && text) r.textContent = text;
  }

  // ---- play, pause, stop ------------------------------------------------------------------------------------------
  function readingTime(s) {
    const words = [s.title].concat(s.body || [], s.tradeoff || []).join(" ").split(/\s+/).length;
    return Math.max(timing.min, words * timing.perWord) * (slow ? timing.slow : 1);
  }
  function clear() { if (timer) { clearTimeout(timer); timer = null; } }
  function schedule() {
    clear();
    const s = STEPS[at];
    if (!isDone(s)) { play = "idle"; drawControls(); return; }   // play stops where the visitor has something to do
    if (after(at) === undefined) { play = "idle"; drawControls(); return; }
    timer = setTimeout(() => { timer = null; if (play === "playing") next(); }, readingTime(s));
  }
  const jobs = (what) => { const J = W.DemoJobs; if (J && typeof J[what] === "function") J[what](); };
  function togglePlay() {
    if (play === "playing") { play = "paused"; clear(); jobs("pause"); }
    else if (play === "paused") { play = "playing"; jobs("resume"); schedule(); }
    else { play = "playing"; schedule(); }
    drawControls();
  }
  function stop() {
    const was = play;
    play = "idle";
    clear();
    if (was !== "idle") jobs("stop");
    if (panel) drawControls();
  }

  // ---- showing and hiding -----------------------------------------------------------------------------------------
  function open() {
    buildPanel();
    panel.hidden = false;
    doc.documentElement.classList.add("demo-tour-on");
    const b = byId("demo-show-tour");
    if (b) b.hidden = true;
  }
  function hide() {
    stop();
    if (panel) panel.hidden = true;
    doc.documentElement.classList.remove("demo-tour-on");
    mark(null);
    const b = byId("demo-show-tour");
    if (b) b.hidden = D.state.mode !== "guided";
  }
  // where to pick up: the last step if it is on this page or the address names no page (the demo opened again), else
  // this page's first step that is not ahead of the demo
  function resumeAt() {
    const id = D.state.step, last = STEPS.findIndex((s) => s.id === id), here = page();
    if (last >= 0 && (STEPS[last].page === here || !String(W.location.hash || "").replace(/^#/, ""))) return last;
    const onPage = STEPS.findIndex((s) => s.page === here && cpIndex(s.checkpoint) <= now());
    if (onPage >= 0) return onPage;
    return last >= 0 ? last : 0;
  }
  function start() { stop(); go(path()[0]); }
  function resume() {
    go(resumeAt());
    // a jump ahead reloads the page: its notice is kept until here (D4)
    if (D.state.notice) { const n = D.state.notice; delete D.state.notice; D.save(); D.notice(n); }
  }

  // the visitor did something: a waiting step may be done now
  D.onSave(() => {
    if (busy || !panel || panel.hidden) return;
    drawReport();
    drawControls();
  });

  // ---- the concept cards, the menu and the end card ----------------------------------------------------------------
  function dialog(id, labelId) {
    closeCard();
    const me = el("dialog", { id, class: "demo-card", "aria-labelledby": labelId });
    // a browser fires close after the call returns: by then another card may be open, so this one removes only itself
    me.addEventListener("close", () => { me.remove(); if (card === me) card = null; });
    card = me;
    return me;
  }
  function show(d) {
    doc.body.appendChild(d);
    try { d.showModal(); } catch (e) { d.setAttribute("open", ""); d.open = true; }
    const f = byId("demo-card-close");
    if (f && f.focus) f.focus();
  }
  function closeCard() {
    if (!card) return;
    const c = card;
    card = null;
    if (c.open && c.close) c.close();
    c.remove();
  }
  function closeButton() { return button("demo-card-close", "Close", () => closeCard()); }
  function openConcept(id) {
    const c = CONCEPTS.find((x) => x.id === id);
    if (!c) return;
    const d = dialog(`demo-concept-${c.id}`, "demo-card-title");
    d.appendChild(el("p", { class: "demo-card-kind" }, "Concept"));
    d.appendChild(el("h2", { id: "demo-card-title" }, c.title));
    c.body.forEach((p) => d.appendChild(el("p", {}, p)));
    const row = el("div", { class: "demo-card-actions" });
    row.appendChild(button("demo-card-menu", "All concepts", () => openMenu()));
    row.appendChild(closeButton());
    d.appendChild(row);
    show(d);
  }
  function openMenu() {
    const d = dialog("demo-concepts-menu", "demo-card-title");
    d.appendChild(el("h2", { id: "demo-card-title" }, "Concepts"));
    d.appendChild(el("p", { class: "muted small" }, "Short explanations of the ideas the tour uses. Open one; Close returns you here."));
    const ul = el("ul", { class: "demo-card-list" });
    CONCEPTS.forEach((c) => {
      const li = el("li");
      li.appendChild(button(`demo-open-${c.id}`, c.title, () => openConcept(c.id), "demo-quiet demo-tour-link"));
      ul.appendChild(li);
    });
    if (END) {
      const li = el("li");
      li.appendChild(button("demo-open-end", END.title, () => openEnd(), "demo-quiet demo-tour-link"));
      ul.appendChild(li);
    }
    d.appendChild(ul);
    const row = el("div", { class: "demo-card-actions" });
    row.appendChild(closeButton());
    d.appendChild(row);
    show(d);
  }
  function openEnd() {
    if (!END) return;
    const d = dialog("demo-end", "demo-card-title");
    d.classList.add("demo-end");
    d.appendChild(el("h2", { id: "demo-card-title" }, END.title));
    d.appendChild(el("p", {}, END.intro));
    const cmds = el("ol", { class: "demo-end-commands" });
    (END.commands || []).forEach((c) => {
      const li = el("li");
      li.appendChild(el("code", {}, c.command));
      li.appendChild(doc.createTextNode(` ${c.says}`));
      cmds.appendChild(li);
    });
    d.appendChild(cmds);
    const table = el("table", { class: "demo-end-table" });
    const head = el("tr");
    ["Stage", "You decide", "WIDER does", "Trade-off"].forEach((x) => head.appendChild(el("th", { scope: "col" }, x)));
    const thead = el("thead");
    thead.appendChild(head);
    table.appendChild(thead);
    const tbody = el("tbody");
    (END.rows || []).forEach((r) => {
      const tr = el("tr");
      tr.appendChild(el("th", { scope: "row" }, r.stage));
      [r.you, r.wider, r.tradeoff].forEach((x) => tr.appendChild(el("td", {}, x)));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    d.appendChild(table);
    const row = el("div", { class: "demo-card-actions" });
    if (quick()) row.appendChild(button("demo-end-chapters", "See all chapters", () => openChapters(), "demo-tour-primary"));
    row.appendChild(button("demo-end-free", "Explore freely", () => {
      closeCard(); D.setMode("free");
      // keyboard users land on the bar, where Guided brings the tour back
      const g = byId("demo-guided"); if (g) g.focus();
    }, quick() ? "demo-quiet" : "demo-tour-primary"));
    row.appendChild(button("demo-end-again", "Start again", () => { closeCard(); D.reset(true); }));
    d.appendChild(row);
    show(d);
  }
  // the chapter list (D4): every stage with its intro, its step count and a tick once all its steps were shown
  function openChapters() {
    const d = dialog("demo-chapters", "demo-card-title");
    let picked = false;
    // Escape or Close: the focus goes back to the tour card's Chapters button (not after a chapter was opened)
    d.addEventListener("close", () => { const b = byId("demo-tour-chapters"); if (!picked && b && b.focus) b.focus(); });
    d.appendChild(el("h2", { id: "demo-card-title" }, "Chapters"));
    d.appendChild(el("p", {}, "One chapter per stage of the review. Opening a chapter shows all its steps, one by one."));
    const ul = el("ul", { class: "demo-card-list demo-chapter-list" });
    CHAPTERS.forEach((c, i) => {
      const mine = STEPS.filter((s) => s.stage === c.stage), done = mine.every((s) => seen().includes(s.id));
      const b = el("button", { type: "button", id: `demo-chapter-${i + 1}`, class: "demo-quiet demo-chapter" });
      b.appendChild(el("b", {}, c.title));
      b.appendChild(el("span", {}, c.intro));
      b.appendChild(el("span", { class: "demo-chapter-count" }, plural(mine.length, "step") + (done ? " · ✓ seen" : "")));
      b.addEventListener("click", () => { picked = true; openChapter(c.stage); });
      const li = el("li");
      li.appendChild(b);
      ul.appendChild(li);
    });
    d.appendChild(ul);
    const row = el("div", { class: "demo-card-actions" });
    row.appendChild(closeButton());
    d.appendChild(row);
    show(d);
    const one = byId("demo-chapter-1");
    if (one && one.focus) one.focus();
  }
  // a chapter opens on its first step, on the path of all chapters; a later moment of the review is reached by doing
  // the earlier actions as Reviewer A did, and if that is not enough by moving the demo there and reloading (D4)
  async function openChapter(stage) {
    const i = STEPS.findIndex((s) => s.stage === stage);
    if (i < 0) return;
    closeCard();
    stop();
    D.state.path = "all";
    D.save();
    const s = STEPS[i];
    if (ahead(s)) {
      for (const x of STEPS.slice(0, i).filter(later)) { try { if (!isDone(x)) await doFor(x); } catch (e) { /* next one */ } }
      const msg = `The demo moved ahead to ${s.stage}: the earlier steps were done as Reviewer A did them.`;
      if (ahead(s)) {
        D.state.step = s.id;
        D.state.notice = msg;
        D.setCheckpoint(s.checkpoint);
        W.location.hash = s.page;
        W.location.reload();
        return;
      }
      D.notice(msg);
    }
    go(i);
  }
  (function conceptsButton() {
    const reset = byId("demo-reset");
    if (!reset || byId("demo-concepts")) return;
    const b = button("demo-concepts", "Concepts", () => openMenu());
    b.setAttribute("title", "Short explanations of the ideas the demo uses");
    (reset.parentElement || reset.parent).insertBefore(b, reset);
  })();

  // ---- keys -------------------------------------------------------------------------------------------------------
  function typing() {
    const a = doc.activeElement;
    if (!a) return false;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName) || !!a.isContentEditable;
  }
  function appHasIt(key) {
    if (card) return true;
    if (doc.querySelector && (doc.querySelector("dialog[open]") || doc.querySelector(".dd-panel:not([hidden])"))) return true;
    const Dr = W.Drawer;
    if (Dr && Dr.isOpen && Dr.isOpen()) return key === "Escape" || !(Dr.isDocked && Dr.isDocked());
    return false;
  }
  doc.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    if (D.state.mode !== "guided" || !panel || panel.hidden || typing() || appHasIt(e.key)) return;
    const a = doc.activeElement, onPage = !a || a === doc.body || a === doc.documentElement || a === byId("demo-tour-title");
    // on a button or a table row the keys belong to that control
    if (e.key === "ArrowRight" && onPage) { e.preventDefault(); next(); }
    else if (e.key === "ArrowLeft" && onPage) { e.preventDefault(); back(); }
    else if (e.key === " " && onPage) { e.preventDefault(); togglePlay(); }
    else if (e.key === "Escape") { e.preventDefault(); hide(); }
  });

  // with the tour beside it the pair stands under the list (demo.css): a click on a pair brings the pair into view
  doc.addEventListener("click", (e) => {
    const row = e.target && e.target.closest && e.target.closest("#dd-list tr[data-idx]");
    if (!row || !panel || panel.hidden) return;
    W.setTimeout(() => {
      const pair = byId("dd-pair"), top = pair && pair.getBoundingClientRect ? pair.getBoundingClientRect().top : 0;
      // just below the demo bar and the page's own header
      if (top > W.innerHeight / 2 && W.scrollBy) W.scrollBy(0, top - 110);
    }, 150);
  });

  W.DemoTour = {
    timing, start, resume, hide, next, back, go, skip, togglePlay, stop, openConcept, openMenu, openEnd, openChapters,
    openChapter, get at() { return at; }, get playing() { return play; }, get path() { return path().slice(); },
    steps: STEPS, chapters: CHAPTERS,
  };
})();
