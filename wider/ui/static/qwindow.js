// The question window (review-plan-and-question-window D7-D12): WIDER's open questions about the page in view,
// asked one at a time in a small chat window docked at the bottom right of the main column. Every message is fixed
// text (the question's title, prompt and each answer's consequence; the engine's `applied` reply); no model is called.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const call = api;
  const me = () => (window.Drawer && Drawer.author()) || "";
  // the five original kinds can be changed here (undo, then ask again); a refinement answer's effects run outside the
  // journalled change, so it is changed through History (D10)
  const CHANGEABLE = ["screen_failed", "screen_split", "enrich_unresolved", "seed_unresolved", "proposal_failed"];
  const KEPT = "Kept for later. Nothing has changed yet.";
  const ALL_DONE = "That is everything on this page. Your answers are saved in History, with your name.";
  const KEY = "wider.qwindow";
  const store = {   // the shell's store is private to its IIFE: a small wrapper of its own (D11)
    get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } },
    set(v) { try { localStorage.setItem(KEY, v); } catch (e) { /* the view state lasts for this page only */ } },
  };

  let mode = "queue";       // questions mode of the project (ask or queue), from the list request
  let items = [];           // the questions in the window: open ones on this page and the ones answered here
  let cur = 0;               // index of the question shown
  let replies = {};         // question_id -> {choice, label, text, applied, change_id, when, error}
  let later = new Set();    // question ids whose Later was used: the button is hidden until the page is drawn again
  let pending = null;       // the question id whose answer request is out (typing dots)
  let closing = false;      // the closing line is shown under the last answer
  let shownPage = null, seq = 0, lastIds = "";

  // the page value of GET /api/questions?page=: a task page of the Review plan has its own (D6)
  function pageValue() {
    const page = typeof currentPage !== "undefined" ? currentPage : "home";
    if (page !== "setup") return page;
    const task = window.currentSetupTask ? currentSetupTask() : "";
    return task ? `setup?task=${task}` : "setup";
  }
  const view = () => store.get() || (mode === "ask" ? "open" : "mini");
  const isOpen = (x) => { const r = replies[x.question_id]; return !r || !!r.error || !!r.kept; };
  const openCount = () => items.filter(isOpen).length;
  const clock = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

  function bubbleOf(x) {
    const cons = (x.options || []).filter(o => o.consequence)
      .map(o => `<li><b>${h(o.label)}</b>: ${h(o.consequence)}</li>`).join("");
    return `<div class="qw-bubble qw-wider" id="qw-q-${x.question_id}"><p class="qw-title">${h(x.title)}</p>
      <p>${h(x.prompt)}</p>${cons ? `<ul class="qw-cons">${cons}</ul>` : ""}</div>`;
  }
  function repliesOf(x) {
    const opts = x.options || [];
    const btns = opts.map(o => `<button type="button" class="qw-reply${o.default ? " qw-default" : ""}" data-qw-choice="${h(o.value)}"${o.input ? ` data-qw-input="${h(o.input)}"` : ""}>${h(o.label)}</button>`);
    if (!later.has(x.question_id)) btns.push(`<button type="button" class="qw-reply" data-qw-later>Later</button>`);
    return `<div class="qw-replies">${btns.join("")}</div>
      <div class="qw-value" hidden><input type="text" data-qw-value aria-label="Your answer"><button type="button" class="qw-send" data-qw-send>Send</button></div>`;
  }
  function threadOf(x) {
    const r = replies[x.question_id];
    let html = bubbleOf(x);
    if (r && !r.error && !r.kept) {
      html += `<div class="qw-bubble qw-you"><p>${h(r.text ? `${r.label}: ${r.text}` : r.label)}</p></div><p class="qw-meta">You · ${h(r.when)}</p>`;
      html += `<div class="qw-bubble qw-wider"><p>${h(r.applied || "Answered.")}</p></div>`;
      if (r.change_id && CHANGEABLE.includes(x.kind)) html += `<p class="qw-change-row"><button type="button" class="link-btn" data-qw-change>Change</button></p>`;
      if (r.refused) html += `<p class="qw-refusal" role="alert">${h(r.refused)}</p>`;
    } else {
      if (r && r.kept) html += `<div class="qw-bubble qw-wider"><p>${KEPT}</p></div>`;
      html += pending === x.question_id ? `<div class="qw-typing" aria-label="WIDER is answering"><span></span><span></span><span></span></div>` : repliesOf(x);
      if (r && r.error) html += `<p class="qw-refusal" role="alert">${h(r.error)}</p>`;   // under the value box
    }
    if (closing) html += `<div class="qw-bubble qw-wider qw-closing"><p>${ALL_DONE}</p></div>`;
    return html;
  }

  function box() { return q("#qwindow"); }
  // the control that had the focus, as a selector to find it again once the window is drawn anew
  function focusKey(el) {
    const a = document.activeElement;
    if (!a || !el.contains(a)) return null;
    for (const k of ["data-qw-choice", "data-qw-value", "data-qw-send", "data-qw-later", "data-qw-change", "data-qw-prev", "data-qw-next", "data-qw-mini", "data-qw-open", "data-qw-close"])
      if (a.hasAttribute(k)) return a.getAttribute(k) ? `[${k}="${a.getAttribute(k).replace(/["\\]/g, "\\$&")}"]` : `[${k}]`;
    return "";
  }
  // focus kept in the window: the same control, else the first answer, Change, or the header button (never taken
  // unasked: only when the focus was in the window before it was drawn)
  let parked = false;       // the focus waits on the header while an answer is out: it goes to the next answer after
  function keepFocus(el, key) {
    if (key == null) return;
    if (parked && key === "[data-qw-mini]") key = "";
    parked = false;
    if (el.contains(document.activeElement)) return;
    for (const s of [key, ".qw-reply", "[data-qw-change]", "[data-qw-mini]", "[data-qw-open]"]) {
      const b = s && q(`#qwindow ${s}`);
      if (b && !b.disabled && !b.closest("[hidden]")) { b.focus(); parked = s === "[data-qw-mini]" && pending != null; return; }
    }
  }
  function draw() {
    const el = box();
    if (!el) return;
    const key = focusKey(el);
    markRow(null);
    if (!items.length) { el.hidden = true; el.innerHTML = ""; return; }
    el.hidden = false;
    const v = view(), n = openCount(), x = items[cur];
    el.className = `qwindow qw-${v}`;
    el.setAttribute("role", "region");
    el.setAttribute("aria-label", "Questions from WIDER");
    if (v === "closed") {
      el.innerHTML = n ? `<button type="button" class="qw-pill" data-qw-open>! Waiting for you · ${n}</button>` : "";
      el.hidden = !n;
    } else if (v === "mini") {
      el.innerHTML = `<div class="qw-head"><span class="qw-avatar" aria-hidden="true">W</span>
        <button type="button" class="qw-expand" data-qw-open aria-expanded="false"><b>WIDER</b> · ${n} question${n === 1 ? "" : "s"} open</button>
        <button type="button" class="qw-icon" data-qw-close aria-label="Close">×</button></div>`;
    } else {
      el.innerHTML = `<div class="qw-head"><span class="qw-avatar" aria-hidden="true">W</span><b class="qw-name">WIDER</b>
        <span class="qw-nav"><button type="button" class="qw-icon" data-qw-prev aria-label="Previous question"${cur ? "" : " disabled"}>‹</button>
        <span class="qw-pos">${cur + 1} of ${items.length}</span>
        <button type="button" class="qw-icon" data-qw-next aria-label="Next question"${cur < items.length - 1 ? "" : " disabled"}>›</button></span>
        <span class="qw-divider" aria-hidden="true"></span>
        <button type="button" class="qw-icon qw-expand" data-qw-mini aria-label="Minimize" aria-expanded="true">–</button>
        <button type="button" class="qw-icon" data-qw-close aria-label="Close">×</button></div>
        <div class="qw-thread" role="log" aria-live="polite">${threadOf(x)}</div>`;
      markRow(x);
      if (window.Questions && Questions.markAsked) Questions.markAsked(x.question_id);   // shown: not asked again (D12)
    }
    bind(el);
    keepFocus(el, key);
  }

  // Seed papers: the row of the question shown gets the rose bar and points to the question's bubble (D7)
  function markRow(x) {
    document.querySelectorAll("tr.qw-row-mark").forEach(tr => { tr.classList.remove("qw-row-mark"); tr.removeAttribute("aria-describedby"); });
    if (!x || x.kind !== "seed_unresolved" || x.anchor == null || !isOpen(x)) return;   // an answered thread: no bar
    const tr = q(`tr[data-seed-row="${String(x.anchor).replace(/["\\]/g, "\\$&")}"]`);
    if (tr) { tr.classList.add("qw-row-mark"); tr.setAttribute("aria-describedby", `qw-q-${x.question_id}`); }
  }

  const firstReply = () => { const b = q("#qwindow .qw-reply"); if (b) b.focus(); };
  function go(i, focus) { cur = Math.max(0, Math.min(items.length - 1, i)); closing = false; draw(); if (focus) firstReply(); }
  // after an answer or Later: the next question not yet answered or kept, or the closing line when every one is answered
  function moveOn() {
    const waiting = (i) => { const r = replies[items[i].question_id]; return !r || !!r.error; };
    for (let k = 1; k <= items.length; k++) {
      const i = (cur + k) % items.length;
      if (waiting(i)) { go(i); return; }
    }
    closing = !items.some(isOpen);
    draw();
  }

  async function answer(x, opt, text) {
    if (pending) return;
    if (!me()) {   // an answer is signed: without the name WIDER works as nothing is sent (D33)
      const need = window.WiderName ? WiderName.NEED : "enter your name first";
      replies[x.question_id] = { error: `Answers are signed with your name: ${need}.`, choice: opt.value, text: text || "" }; draw(); return;
    }
    const label = opt.label;
    pending = x.question_id; replies[x.question_id] = null; draw();
    try {
      const r = await call(`/api/questions/${x.question_id}/answer`, { method: "POST", body: JSON.stringify({ choice: opt.value, text: text || "", by: me() }) });
      replies[x.question_id] = { label, text, applied: r.applied, change_id: r.change_id, when: clock(new Date()) };
      if (window.Questions && Questions.markAsked) Questions.markAsked(x.question_id);
      if (window.afterAction) afterAction();
      pending = null; draw();
      setTimeout(moveOn, 700);
    } catch (e) {
      // the engine's message, nothing more; the value box stays open with what was typed
      pending = null; replies[x.question_id] = { error: e.message, choice: opt.value, text: text || "" }; draw();
    }
  }
  async function change(x) {
    const r = replies[x.question_id];
    try {
      const who = me();   // signed with the name the answer was given with, not only the remembered one (rule 3)
      await call(`/api/history/${r.change_id}/undo`, { method: "POST", headers: who ? { "X-Wider-Author": encodeURIComponent(who) } : {} });
      replies[x.question_id] = null; closing = false; draw(); firstReply();   // the question is open again: asked anew
      if (window.afterAction) afterAction();
    } catch (e) {
      const other = e.status === 409 && e.body && e.body.author;
      r.refused = other ? `${e.message} Change it on the History page.` : e.message;   // the answer stays
      draw();
    }
  }

  function bind(el) {
    const x = items[cur];
    el.querySelectorAll("[data-qw-open]").forEach(b => b.onclick = () => { store.set("open"); draw(); firstReply(); });
    el.querySelectorAll("[data-qw-close]").forEach(b => b.onclick = () => { store.set("closed"); draw(); const p = q("#qwindow .qw-pill"); if (p) p.focus(); });
    const mini = q("[data-qw-mini]", el);
    if (mini) mini.onclick = () => { store.set("mini"); draw(); const b = q("#qwindow [data-qw-open]"); if (b) b.focus(); };
    const prev = q("[data-qw-prev]", el), next = q("[data-qw-next]", el);
    if (prev) prev.onclick = () => go(cur - 1, true);
    if (next) next.onclick = () => go(cur + 1, true);
    if (!x) return;
    const valueBox = q(".qw-value", el);
    let chosen = null;
    el.querySelectorAll("[data-qw-choice]").forEach(b => b.onclick = () => {
      const opt = (x.options || []).find(o => o.value === b.dataset.qwChoice);
      if (!opt) return;
      if (opt.input) {
        chosen = opt; valueBox.hidden = false;
        const inp = q("[data-qw-value]", valueBox);
        inp.setAttribute("aria-label", opt.input === "doi" ? "DOI (10.xxxx/...)" : opt.label);
        inp.placeholder = opt.input === "doi" ? "10.xxxx/..." : "";
        q("[data-qw-send]", valueBox).textContent = opt.input === "doi" ? "Save DOI" : "Send";
        inp.focus();
      } else answer(x, opt);
    });
    const r = replies[x.question_id];
    if (r && r.error && r.choice) {   // a refused value: the box again, with the typed text, and the focus in it
      const b = q(`[data-qw-choice="${r.choice.replace(/["\\]/g, "\\$&")}"]`, el);
      const had = document.activeElement, inside = had && el.contains(had);
      if (b && b.getAttribute("data-qw-input")) {
        b.onclick(); q("[data-qw-value]", el).value = r.text;
        if (!inside && had && had.focus) had.focus();   // never taken unasked
      }
    }
    const send = q("[data-qw-send]", el);
    if (send) send.onclick = () => { if (chosen) answer(x, chosen, q("[data-qw-value]", el).value); };
    const val = q("[data-qw-value]", el);
    if (val) val.onkeydown = (ev) => { if (ev.key === "Enter" && chosen) { ev.preventDefault(); answer(x, chosen, val.value); } };
    const lat = q("[data-qw-later]", el);
    if (lat) lat.onclick = () => {   // a view action: nothing is written, the question stays open everywhere (D9)
      later.add(x.question_id); replies[x.question_id] = { kept: true }; draw();
      if (window.Questions && Questions.markAsked) Questions.markAsked(x.question_id);
      setTimeout(moveOn, 700);
    };
    const ch = q("[data-qw-change]", el);
    if (ch) ch.onclick = () => change(x);
    el.onkeydown = (ev) => {   // Esc minimizes and gives the focus to the header button
      if (ev.key !== "Escape" || view() !== "open") return;
      ev.preventDefault(); store.set("mini"); draw();
      const b = q("#qwindow [data-qw-open]"); if (b) b.focus();
    };
  }

  // fresh: the page was drawn again (Later buttons come back, the answered threads are left behind)
  async function refresh(fresh) {
    const page = pageValue();
    if (fresh || page !== shownPage) {
      const moved = page !== shownPage;
      items = []; replies = {}; later = new Set(); cur = 0; closing = false; lastIds = ""; shownPage = page;
      if (moved) draw();   // another page: the window of the last one goes at once, before the list loads
    }
    const n = ++seq;
    let d;
    try { d = await call(`/api/questions?status=open&page=${encodeURIComponent(page)}`); } catch (e) { return; }
    if (n !== seq || page !== pageValue()) return;   // the page changed while the list loaded
    mode = d.mode || mode;
    const open = d.questions || [];
    const ids = open.map(x => x.question_id).join(",");
    if (ids === lastIds) return;   // nothing new: an answer being typed is not redrawn away
    const known = new Set(items.map(x => x.question_id));
    const arrived = open.some(x => !known.has(x.question_id));
    lastIds = ids;
    const answeredHere = items.filter(x => replies[x.question_id] && !replies[x.question_id].error && !replies[x.question_id].kept);
    const current = items[cur] && items[cur].question_id;
    items = [...answeredHere.filter(x => !open.some(o => o.question_id === x.question_id)), ...open]
      .sort((a, b) => a.question_id - b.question_id);
    cur = Math.max(0, items.findIndex(x => x.question_id === current));
    if (current == null || !items[cur] || items[cur].question_id !== current) {
      const firstOpen = items.findIndex(x => !replies[x.question_id]);
      cur = firstOpen < 0 ? 0 : firstOpen;
    }
    if (arrived && known.size && store.get() === "closed") store.set("mini");   // a new question is never silently hidden (D11)
    if (pending == null) draw();
  }
  // ask mode, a new question about this page (questions.js, D12): the window opens, expanded, without taking the focus
  function open() { store.set("open"); draw(); return refresh(); }
  // the seed table was drawn again: the row of the question shown gets its bar back
  function remark() { if (items.length && view() === "open") markRow(items[cur]); }
  window.QWindow = { refresh, open, remark, pageValue };
})();
