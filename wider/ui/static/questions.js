// Questions (WP14 part 3): the tool's doubts, answered on the Questions page (queue mode) or in a dialog as they
// arrive (ask mode). Answers are applied by the server at once; the sidebar shows how many are open.
(function () {
  const q = (s, root) => (root || document).querySelector(s);
  const h = esc;
  const call = api;
  const me = () => (window.Drawer && Drawer.author()) || "";
  // an answer or a dismissal is signed: without the name WIDER works as nothing is sent and the card points at Change (D33)
  const NEED_LEAD = "Answers are signed with your name:";
  const needName = () => (window.WiderName ? WiderName.needHtml(NEED_LEAD) : h(NEED_LEAD + " enter your name first"));
  const STAGE = (k) => (typeof stageTitle === "function" ? stageTitle(k) : k);
  let mode = "queue", askedThrough = 0, timer = null, lastSeen = "";
  const inWindow = new Set();   // questions shown in the question window: the dialog never asks them again (D12)
  // a question is about the page in view when it is that page's (the Review plan holds every setup task's, but not a
  // seed question about a validation seed paper: the plan names none; D6, questions.on_page)
  const here = (x) => {
    const page = window.QWindow ? QWindow.pageValue() : null;
    const validationSeed = x.kind === "seed_unresolved" && (x.context || {}).set === "Validation";
    return !!page && (x.page === page || (page === "setup" && String(x.page || "").startsWith("setup?") && !validationSeed));
  };

  // A refinement question (2b) shows why it was asked, each answer's consequence and the default.
  function refine(x) {
    if (!x.kind.startsWith("refine_")) return "";
    const ev = (x.context || {}).evidence || {};
    const rows = x.options.map(o => `<li><b>${h(o.label)}</b>${o.default ? ' <span class="muted small">(the default)</span>' : ""}: ${h(o.consequence || "")}</li>`).join("");
    // the note goes into the decision ledger with the answer, like `wider questions answer ... --value`
    return `${ev.why ? `<p class="small">Why: ${h(ev.why)}</p>` : ""}<ul class="small q-consequences">${rows}</ul>
      <label class="small">Note (optional, kept with the decision) <input type="text" data-note></label>`;
  }

  function card(x) {
    const secondary = (o) => o.input || (x.kind.startsWith("refine_") && !o.default);   // a refinement default stands out
    const opts = x.options.map(o => `<button class="${secondary(o) ? "secondary" : ""} small-btn" data-choice="${h(o.value)}" ${o.input ? `data-input="${h(o.input)}"` : ""}${o.writes ? ' data-writes="1"' : ""}>${h(o.label)}${o.default ? " (default)" : ""}</button>`).join(" ");
    return `<article class="question" data-qid="${x.question_id}" data-kind="${h(x.kind)}">
      <p class="small muted">${h(STAGE(x.stage))} · ${h(x.kind.replace(/_/g, " "))} · ${h(fmtWhen(x.created_at))}${x.record_id ? ` · record ${x.record_id}` : ""}</p>
      <h3 id="ask-title-${x.question_id}">${h(x.title)}</h3>
      <p>${h(x.prompt)}</p>${refine(x)}
      <div class="row q-actions">${opts} <button class="secondary small-btn" data-dismiss>Dismiss</button></div>
      <div class="q-input" hidden><label class="small"><span class="q-input-label"></span><textarea rows="3"></textarea></label>
        <div class="row"><button class="small-btn" data-save>Save answer</button><button class="secondary small-btn" data-cancel>Cancel</button></div></div>
      <p class="small q-msg" role="status"></p></article>`;
  }

  function bind(root, after) {
    root.querySelectorAll(".question").forEach(el => {
      const id = el.dataset.qid, msg = q(".q-msg", el), box = q(".q-input", el);
      let pending = null, busy = false;
      // one answer per click: the card's buttons stay disabled while a request is out (a double click sent two; ui-tester F1)
      const lock = (on) => { busy = on; el.querySelectorAll("button").forEach(b => b.disabled = on); };
      const send = async (choice, text, seen) => {
        if (busy) return;
        if (!me()) { msg.innerHTML = needName(); return; }
        lock(true);
        const note = el.dataset.kind.startsWith("refine_") ? q("[data-note]", el) : null;
        // "freeze this round" freezes nothing itself: it closes the round and opens the Construct workbench's Freeze
        const freeze = el.dataset.kind === "refine_round_stop" && choice === "freeze";
        try {
          const r = await call(`/api/questions/${id}/answer`, { method: "POST", body: JSON.stringify(Object.assign({ choice, text: text || (note && note.value) || "", by: me() }, seen ? { seen_choice: seen } : {})) });
          msg.innerHTML = `<span class="ok">${h(r.applied || "answered")}</span>${freeze ? " Opening the Construct workbench: the freeze is its own step there." : ""}`;
          if (window.offerUndo) offerUndo(r.change_id, r.applied ? `Answer applied: ${r.applied}` : "Answer applied.");
          if (window.afterAction) afterAction();
          setTimeout(() => { after(); if (freeze) { if (dlg.open) dlg.close(); location.hash = "construct"; } }, 700);
        } catch (e) { lock(false); msg.innerHTML = `<span class="bad">${h(e.message)}</span>`; }
      };
      el.querySelectorAll("[data-choice]").forEach(b => b.onclick = async () => {
        if (busy) return;
        if (b.dataset.input) {
          pending = b.dataset.choice; box.hidden = false;
          q(".q-input-label", el).textContent = b.dataset.input === "doi" ? "DOI (10.xxxx/...)" : b.textContent;
          const ta = q("textarea", box); ta.rows = b.dataset.input === "doi" ? 1 : 4; ta.focus();
        } else if (b.dataset.writes && window.Refine) {   // the same confirm step as any save after a freeze
          lock(true);
          const ok = await Refine.confirmSave("synonyms", "Save the answer as a new synonyms version?", "Save");
          lock(false);
          if (ok) await send(b.dataset.choice, undefined, typeof ok === "string" ? ok : null); else msg.textContent = "not saved: nothing changed";
        } else send(b.dataset.choice);
      });
      q("[data-save]", el).onclick = () => send(pending, q("textarea", box).value);
      q("[data-cancel]", el).onclick = () => { box.hidden = true; pending = null; };
      q("[data-dismiss]", el).onclick = async () => {
        if (busy) return;
        if (!me()) { msg.innerHTML = needName(); return; }
        lock(true);
        try { await call(`/api/questions/${id}/dismiss`, { method: "POST", body: JSON.stringify({ by: me() }) }); after(); }
        catch (e) { lock(false); msg.textContent = e.message; }
      };
    });
  }

  // ---------------------------------------------------------------- Questions page
  window.loadQuestions = async function () {
    let d, done;
    try { [d, done] = await Promise.all([call("/api/questions?status=open"), call("/api/questions?status=all&limit=100")]); }
    catch (e) { q("#q-open").innerHTML = `<p class="bad">${h(e.message)}</p>`; return; }
    mode = d.mode;
    document.querySelectorAll('input[name="q-mode"]').forEach(r => r.checked = r.value === mode);
    const waiting = (d.counts || {}).waiting || 0;
    q("#q-open").innerHTML = (d.questions.length ? d.questions.map(card).join("") : `<p class="muted card">No open questions.</p>`)
      + (d.hidden ? `<p class="card" id="q-hidden" role="note">${h(d.hidden)}.</p>` : "")   // development records (ui-redesign D4)
      + (waiting ? `<p class="muted small" id="q-waiting">${waiting} waiting for the budget: they open as open questions are answered or dismissed.</p>` : "");
    bind(q("#q-open"), () => { loadQuestions(); refreshCount(); });
    loadDecisions();
    const closed = done.questions.filter(x => x.status === "answered" || x.status === "dismissed");
    q("#q-done").innerHTML = closed.length ? `<table><tr><th>when</th><th>about</th><th>answer</th><th>what changed</th></tr>${closed.map(x =>
      `<tr><td class="small">${h(fmtWhen(x.answered_at || x.created_at))}</td><td>${h(x.title)}<div class="muted small">${h(x.kind.replace(/_/g, " "))}</div></td>
       <td>${x.status === "dismissed" ? '<span class="muted">dismissed</span>' : h((x.answer || {}).choice || "")}${x.answered_by ? ` <span class="muted small">${h(x.answered_by)}</span>` : ""}</td>
       <td class="small">${h(x.applied || "")}</td></tr>`).join("")}</table>` : `<p class="muted small">nothing yet</p>`;
  };
  // The decision ledger of the refinement interview (`wider decisions list`, the same lines).
  async function loadDecisions() {
    const box = q("#q-decisions-list");
    if (!box) return;
    try { const d = await call("/api/decisions"); box.innerHTML = d.decisions.length ? `<ul class="small">${d.lines.map(l => `<li>${h(l)}</li>`).join("")}</ul>` : `<p class="muted small">no decisions yet</p>`; }
    catch (e) { box.innerHTML = `<p class="bad small">${h(e.message)}</p>`; }
  }
  const exp = q("#q-decisions-export");
  if (exp) exp.onclick = async () => {
    try { const r = await call("/api/decisions/export", { method: "POST" }); q("#q-decisions-msg").textContent = `${r.decisions} decisions written to ${r.files.join(", ")}`; }
    catch (e) { q("#q-decisions-msg").textContent = e.message; }
  };
  document.querySelectorAll('input[name="q-mode"]').forEach(r => r.onchange = async () => {
    try { const res = await call("/api/questions/mode", { method: "POST", body: JSON.stringify({ mode: r.value }) }); mode = res.mode;
      q("#q-mode-msg").textContent = mode === "ask" ? "questions will open in a dialog as they arrive" : "questions will wait on this page"; }
    catch (e) { q("#q-mode-msg").textContent = e.message; }
  });

  // ---------------------------------------------------------------- ask mode dialog
  const dlg = q("#ask-dialog");
  async function showNext() {
    let d;
    try { d = await call("/api/questions?status=open"); } catch (e) { return; }
    // ask mode: a question about this page opens the window, one about another page the dialog (D12)
    const fresh = d.questions.filter(x => x.question_id > askedThrough && !inWindow.has(x.question_id));
    const mine = fresh.filter(here);
    if (mine.length && window.QWindow) { mine.forEach(x => inWindow.add(x.question_id)); QWindow.open(); }
    const next = fresh.filter(x => !inWindow.has(x.question_id));
    if (!next.length) {   // everything new is in the window: those are asked, the check does not ask them again
      askedThrough = Math.max(askedThrough, ...fresh.map(x => x.question_id));
      if (dlg.open) dlg.close();
      return;
    }
    const x = next[0];
    q("#ask-count").textContent = `The tool has a question${next.length > 1 ? ` (${next.length} waiting)` : ""}. Runs continue while you answer; Later keeps it on the Questions page.`;
    q("#ask-body").innerHTML = card(x);
    dlg.setAttribute("aria-labelledby", `ask-title-${x.question_id}`);
    bind(q("#ask-body"), () => { askedThrough = Math.max(askedThrough, x.question_id - 1); refreshCount(); showNext(); });
    if (!dlg.open) { opener = document.activeElement; dlg.showModal(); }
    const first = q("#ask-body [data-choice]") || q("#ask-body button");   // the first answer, not Later
    if (first) first.focus();
  }
  let opener = null;   // the control that had the focus before the dialog opened; it gets the focus back on close
  const later = async () => {   // everything open now stays in the queue; only newer questions open the dialog
    try { const s = await call("/api/questions/summary"); askedThrough = Math.max(askedThrough, s.latest_open || 0); } catch (e) { /* keep */ }
    dlg.close(); refreshCount();
  };
  q("#ask-later").onclick = later;
  dlg.addEventListener("cancel", (ev) => { ev.preventDefault(); later(); });   // Esc takes the Later path
  dlg.addEventListener("close", () => { if (opener && opener.isConnected !== false && opener.focus) opener.focus(); opener = null; });

  async function refreshCount() {
    let s;
    try { s = await call("/api/questions/summary"); } catch (e) { return; }
    mode = s.mode;
    if (window.setQuestionCount) setQuestionCount(s.open);
    const seen = `${s.open}:${s.latest_open}`;   // the window reloads its list only when the open questions changed
    if (seen !== lastSeen) { lastSeen = seen; if (window.QWindow) QWindow.refresh(); }
    if (mode === "ask" && s.latest_open && s.latest_open > askedThrough && !dlg.open) showNext();
    if (typeof currentPage !== "undefined" && currentPage === "questions" && !document.activeElement.closest(".question")) loadQuestions();
  }
  function loop() {
    clearTimeout(timer);
    refreshCount().finally(() => { timer = setTimeout(loop, window.Jobs && Jobs.activeCount() ? 3000 : 10000); });
  }
  // a question shown in the window (answered or kept for later there) is not asked again in the dialog
  const markAsked = (id) => { inWindow.add(id); };
  window.Questions = { refresh: refreshCount, markAsked };
  loop();
})();
