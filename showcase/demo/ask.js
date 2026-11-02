/* WIDER interactive demo: Ask WIDER as recorded example questions (openspec change demo-chapters-and-video, design D5b).
 *
 * Loaded after actions.js and screening.js, before the app's scripts. The side panel's Ask WIDER tab keeps the app's
 * own drawing (shell.js): this file puts the example questions above the conversation as buttons, and pressing one
 * goes the app's own way, through the hidden question field and the hidden Ask button. The catcher answers
 * POST /api/assistant/ask as a short simulated job and GET /api/assistant/history with the recorded turns of the
 * examples asked so far in this tab (state.asked). The answers were recorded beforehand from the real assistant on
 * the invented project with a local model; no model is called and nothing leaves the page.
 */
(function () {
  "use strict";
  const W = window, D = W.Demo, A = W.DemoActions;
  if (!D || !A) return;
  const { ok, refuse } = A;
  const SNAP = W.DEMO_SNAPSHOT || {};
  const doc = W.document;
  const HISTORY = "/api/assistant/history";
  const LEAD = "Example questions. These answers were recorded beforehand; in the demo you cannot type your own question.";
  const ONLY = "In the demo only the example questions are answered: their answers were recorded beforehand.";
  const SENT = "What you type is sent as written: do not paste a record's text.";

  // the recorded turns: GET /api/assistant/history on the latest checkpoint that has it (record_snapshot.py's ask phase)
  function recordedTurns() {
    for (const cp of D.order().slice().reverse()) {
      const h = D.recorded(HISTORY, cp);
      if (h && Array.isArray(h.turns) && h.turns.length) return h.turns;
    }
    return [];
  }
  // an example is offered only with a recorded answer and its sources
  function examples() {
    const turns = recordedTurns();
    return (SNAP.ask_examples || []).map((e) => ({ ...e, turn: turns.find((t) => t.question === e.question) }))
      .filter((e) => e.turn && String(e.turn.answer || "").trim() && (e.turn.based_on || []).length);
  }
  const asked = () => (Array.isArray(D.state.asked) ? D.state.asked : (D.state.asked = []));
  const byQuestion = (q) => examples().find((e) => e.question === String(q || "").trim());

  // ---- the catcher ------------------------------------------------------------------------------------------------
  D.handle("POST /api/assistant/ask", ({ body }) => {
    const ex = byQuestion((body || {}).question);
    if (!ex) return refuse(409, ONLY);
    if (!asked().includes(ex.question)) asked().push(ex.question);
    // the job is done at once; the app reads it on its first poll (screening.js answers GET /api/jobs/{id} from here)
    const id = `demo-ask-${ex.turn.n}`;
    const jobs = (D.state.jobsDone = D.state.jobsDone || {});
    jobs[id] = { job_id: id, status: "done", label: "Ask WIDER", stage: "assistant", result: { turn: ex.turn.n }, error: null };
    return ok({ job_id: id, waiting: "Showing the recorded answer…" });
  });
  D.handle("GET /api/assistant/history", () => {
    const all = examples();
    const turns = asked().map((q) => all.find((e) => e.question === q)).filter(Boolean).map((e) => e.turn);
    return ok({ author: "Reviewer A", turns });
  });

  // ---- the examples above the conversation ------------------------------------------------------------------------
  const byId = (id) => doc.getElementById(id);
  function el(tag, cls, text) {
    const e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function day(at) {
    const d = new Date(at);
    if (isNaN(d)) return "";
    return d.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  }
  const recordedLine = (t) => ["Recorded answer", day(t.at), t.model || t.backend || ""].filter(Boolean).join(", ");
  const turnFor = (art) => {
    const n = Number(art.dataset && art.dataset.turn);
    return examples().map((e) => e.turn).find((t) => t.n === n);
  };

  function press(ex) {
    if (asked().includes(ex.question)) {
      const art = [...((byId("ask-box") || { querySelectorAll: () => [] }).querySelectorAll(".ask-turn"))].find((a) => turnFor(a) === ex.turn);
      if (art && art.scrollIntoView) art.scrollIntoView({ block: "nearest" });
      return;
    }
    const sel = byId("ask-about");
    if (sel && ex.page && sel.value !== ex.page) {
      sel.value = ex.page;
      sel.dispatchEvent(new Event("change", { bubbles: true }));   // the app redraws the box
    }
    const ta = byId("ask-q"), go = byId("ask-go");
    if (!ta || !go) return;
    ta.value = ex.question;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    go.click();
  }

  function decorate() {
    const box = byId("ask-box");
    if (!box) return;
    const conv = box.querySelector(".ask-turns");
    if (!conv) return;   // no project, no model or no name: the app says why
    if (!box.querySelector(".demo-ask-examples")) {
      const wrap = el("div", "demo-ask-examples");
      wrap.appendChild(el("p", "demo-ask-lead", LEAD));
      const list = el("div", "demo-ask-list");
      examples().forEach((ex, i) => {
        const b = el("button", "secondary demo-ask-ex", ex.question);
        b.type = "button";
        b.id = `demo-ask-${i + 1}`;
        b.addEventListener("click", () => press(ex));
        list.appendChild(b);
      });
      wrap.appendChild(list);
      box.insertBefore(wrap, box.firstChild);
    }
    // the fixed line about typing has nothing to refer to without a field
    box.querySelectorAll("p.ask-line").forEach((p) => { if (p.textContent === SENT) p.classList.add("demo-hide"); });
    conv.querySelectorAll(".ask-turn").forEach((art) => {
      if (art.querySelector(".demo-ask-recorded")) return;
      const t = turnFor(art);
      if (!t) return;
      const line = el("p", "demo-ask-recorded", recordedLine(t));
      art.insertBefore(line, art.firstChild);
    });
  }

  function watch() {
    decorate();
    if (typeof W.MutationObserver !== "function" || !doc.body) return;
    new W.MutationObserver(() => decorate()).observe(doc.body, { childList: true, subtree: true });
  }
  if (doc.readyState === "loading" && doc.addEventListener) doc.addEventListener("DOMContentLoaded", watch);
  else watch();

  // the app's name functions answer the fixed name and open nothing (D6); common.js defines them after this file
  function pinName() {
    const n = W.WiderName;
    if (!n) return;
    n.ask = async () => "Reviewer A";
    n.askOnce = () => {};
  }
  if (doc.readyState === "loading" && doc.addEventListener) doc.addEventListener("DOMContentLoaded", pinName);
  else pinName();

  W.DemoAsk = { examples, decorate, press, recordedLine };
})();
