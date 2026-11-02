"""Ask WIDER on the page (guide-assistant D8, D13, D17), under Node on the small DOM of ``a11y_dom``: the consent
dialog's "Remember my choice for Ask WIDER", and (tasks 8.2 and 12) the Ask WIDER page: its ways in, "Asking about:",
"Your name", the conversation and its lines. Synthetic answers only; no project, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.a11y_dom import run_dom

ROOT = Path(__file__).resolve().parents[2]

REFUSAL = {"message": "a needs your consent.", "backend_consent_required": {
    "backend": "a", "kind": "llm", "operation": "assistant", "label": "answer questions in Ask WIDER",
    "destination": "llm.example.invalid", "locality": "cloud",
    "sent": "your question, this page's guide, matching sections of WIDER's documents, your project's settings for "
            "those topics, the project's status (no record) and your earlier questions",
    "warning": None, "changed": [], "fingerprint": "f00d"}}

# fetch answers from a queue of [status, body] and records [url, method, the X-Wider-Consent-Once header]
FETCH = f"""
const answers = [], fetched = [];
global.fetch = async (url, opts) => {{
  fetched.push([url, (opts && opts.method) || "GET", ((opts && opts.headers) || {{}})["X-Wider-Consent-Once"] || null]);
  const [status, body] = answers.shift(); return {{ ok: status < 400, status, statusText: "x", json: async () => body }}; }};
const refusal = {json.dumps({"detail": REFUSAL})};
const dialog = async () => {{ for (let i = 0; i < 20 && !$("dialog.consent-dialog"); i++) await flush(); return $("dialog.consent-dialog"); }};
"""


def test_consent_dialog_remember_ticked():
    """The dialog for `assistant` says what is sent, "For: answer questions in Ask WIDER", and has "Remember my choice
    for Ask WIDER" ticked: Allow then stores the consent and sends the question again. Unticked, Allow stores nothing
    and the one retry carries X-Wider-Consent-Once with the fingerprint the dialog showed. Another operation's dialog
    has no such box."""
    r = run_dom(FETCH + """
      const res = {};
      answers.push([403, refusal], [200, {}], [200, { ok: 1 }]);
      let p = api("/api/assistant/ask", { method: "POST", body: "{}" });
      let d = await dialog();
      const box = d.querySelector("[data-remember]");
      res.ticked = !!box && box.checked; res.text = d.textContent.replace(/\\s+/g, " ");
      d.querySelector("[data-act=allow]").click(); await p;
      res.stored = fetched.slice();
      fetched.length = 0;
      answers.push([403, refusal], [200, { ok: 1 }]);
      p = api("/api/assistant/ask", { method: "POST", body: "{}" });
      d = await dialog(); d.querySelector("[data-remember]").checked = false;
      d.querySelector("[data-act=allow]").click(); await p;
      res.once = fetched.slice();
      fetched.length = 0;
      const other = JSON.parse(JSON.stringify(refusal)); other.detail.backend_consent_required.operation = "research";
      other.detail.backend_consent_required.label = "send research data";
      answers.push([403, other]);
      p = api("/api/jobs/screen", { method: "POST", body: "{}" }).catch(() => null);
      d = await dialog(); res.otherBox = !!d.querySelector("[data-remember]");
      d.querySelector("[data-act=cancel]").click(); await p;
      out(res);
    """, scripts=("common.js",))
    assert r["ticked"] is True
    assert "For: answer questions in Ask WIDER" in r["text"] and "Remember my choice for Ask WIDER" in r["text"]
    assert REFUSAL["backend_consent_required"]["sent"] in r["text"]
    assert r["stored"] == [["/api/assistant/ask", "POST", None], ["/api/providers/a/consent", "POST", None],
                           ["/api/assistant/ask", "POST", None]]
    assert r["once"] == [["/api/assistant/ask", "POST", None], ["/api/assistant/ask", "POST", "llm:a:assistant:f00d"]]
    assert r["otherBox"] is False



def test_consent_dialog_closed_otherwise_sends_nothing():
    """M124: a consent dialog closed some other way than its buttons or Escape (another script, the browser) still
    settles: nothing is stored, the question is not sent again, and the caller gets the refusal instead of waiting
    for ever."""
    r = run_dom(FETCH + """
      answers.push([403, refusal]);
      const p = api("/api/assistant/ask", { method: "POST", body: "{}" }).then(() => "sent", (e) => e.status);
      const d = await dialog(); d.close();
      out({ result: await p, fetched, left: !!$("dialog.consent-dialog") });
    """, scripts=("common.js",))
    assert r == {"result": 403, "fetched": [["/api/assistant/ask", "POST", None]], "left": False}

# ---------------------------------------------------------------------------------------------- the box (task 8.2)
from tests.unit.test_a11y_node import BODY, SHELL_PAGE_PRE  # noqa: E402

SCRIPTS = ("common.js", "jobs.js", "backends.js", "shell.js")
GUIDE = {"page": "rank", "title": "Ranking", "can_do": "Rank the records.", "notices": [],
         "parts": [{"heading": "What to do", "items": ["Press Rank records."]}]}
TURN = {"n": 1, "question": "Why rank?", "answer": "Ranking puts likely papers first.", "sources": ["methods/ranking/short"],
        "unreviewed": [], "show": [], "drafts": [], "notes": [], "read": "Read: Ranking (guide)", "retrieval": "keywords",
        "backend": "fake", "model": "fake-model", "depth": None, "page": "rank", "unhelpful": False,
        "docs_changed": False, "answered_by": "Answered by fake (fake-model)",
        "based_on": [{"id": "methods/ranking/short", "title": "Ranking: in short", "anchor": "#methods-ranking",
                      "command": "wider guide methods ranking", "unreviewed": False}]}


def ask_pre(turns=(), queue=False, backend="fake", author="ana", extra=None, project="Synthetic"):
    """A project, a model and a reviewer name; ``replies`` answer the guide, the history, the send notices and the job
    bar's polls. ``location.hash`` routes as app.js does: the page shows, ``currentPage`` follows and the shell marks
    it (a ``history.replaceState`` does not route); ``location.reload`` is counted in ``reloads``."""
    replies = {"/api/guide/rank": GUIDE, "/api/assistant/history": {"author": author, "turns": list(turns)},
               "/api/send-notice/assistant": {"sentence": "WIDER sends your question to fake.", "locality": "cloud"},
               "/api/send-notice/assistant_embed": {"sentence": ""}, "/api/jobs": [], "/api/stages": [],
               **(extra or {})}
    name = f"localStorage.setItem('wider.reviewer', {json.dumps(author)});" if author else ""
    return (f"PROJECT_TITLE = {json.dumps(project)}; CONSTRUCT_BACKEND = {json.dumps(backend)}; global.innerWidth = 1440;"
            f"window.askPollMs = 5; {name} Object.assign(replies, {json.dumps(replies)});"
            + ROUTE + ("window.queueOpen = () => true;" if queue else ""))


ROUTE = """
  let hashNow = ""; window.reloads = 0; location.reload = () => { window.reloads++; };
  const route = () => { let k = hashNow.replace(/^#/, "").split("?")[0] || "home";
    if (k === "ask" && window.askRoute) k = askRoute(hashNow);   // the old Ask page's address opens the tab (app.js)
    document.querySelectorAll("section.page").forEach(s => { s.hidden = s.id !== PAGES[k]; });
    currentPage = k; markCurrentPage(k); };
  Object.defineProperty(location, "hash", { configurable: true, get: () => hashNow,
    set: (v) => { const n = String(v).startsWith("#") ? String(v) : "#" + v; if (n === hashNow) return; hashNow = n; route(); } });
  history.replaceState = (a, b, h) => { hashNow = h; };
  global.navigateHash = (h) => { location.hash = h; };
"""


HELPERS = """
  const settle = async (n = 12) => { for (let i = 0; i < n; i++) await flush(); };
  const wait = async (ms) => new Promise(r => setTimeout(r, ms));
  const box = () => $("#ask-box");
  const txt = (e) => String((e || {}).textContent || "").replace(/\\s+/g, " ").trim();
  const shown = (s) => !!$(s) && !$(s).hidden;
"""
# Ranking first (the page the researcher came from), then the Ask WIDER page by the top bar's address
OPEN = HELPERS + """
  navigateHash("#rank"); await settle();
  navigateHash("#ask?about=rank"); await settle();
"""


def _run(body: str, start=OPEN, **kw) -> dict:
    return run_dom(start + body, html=BODY, scripts=SCRIPTS, pre=SHELL_PAGE_PRE + ask_pre(**kw))


def test_ask_box_renders_answer_as_text():
    """The box has its visible heading, the two fixed lines, a labelled box and Ask; a turn shows the question, the
    answer, Based on with an Open link to the section's anchor, Read and Answered by."""
    r = _run("""
      const b = box(), lab = b.querySelector("label");
      out({ h: txt(b.querySelector("#ask-h")), text: txt(b), label: txt(lab), for: lab.getAttribute("for"),
            ta: !!b.querySelector("textarea#ask-q"), go: txt(b.querySelector("#ask-go")),
            open: [...b.querySelectorAll(".ask-sources a")].map(a => a.getAttribute("href")),
            sends: [...b.querySelectorAll("[data-send]")].map(e => e.dataset.send) });
    """, turns=[TURN])
    assert r["h"] == "Your conversation" and r["for"] == "ask-q" and r["ta"] and r["go"] == "Ask"
    assert "It explains from WIDER's documents and can be wrong: check the sources." in r["text"]
    assert "What you type is sent as written: do not paste a record's text." in r["text"]
    for part in ("You asked: Why rank?", "Ranking puts likely papers first.", "Based on:", "Ranking: in short",
                 "Read: Ranking (guide)", "Answered by fake (fake-model)", "WIDER sends your question to fake."):
        assert part in r["text"], part
    assert r["open"] == ["#methods-ranking"] and r["sends"] == ["assistant", "assistant_embed"]


def test_no_image_or_link_from_model_text():
    """An answer, question or draft holding markup, an image or a link is shown as text: no img, no link but Open."""
    evil = '<img src=x onerror=alert(1)> [link](http://evil.invalid) <a href="http://evil.invalid">x</a>'
    t = dict(TURN, question=evil, answer=evil, drafts=[{"for": "methods", "text": evil}], notes=[evil],
             based_on=[dict(TURN["based_on"][0], title=evil)])
    r = _run("""
      const b = box();
      out({ imgs: b.querySelectorAll("img").length, links: [...b.querySelectorAll("a")].map(a => a.getAttribute("href")),
            answer: b.querySelector(".ask-answer").textContent });
    """, turns=[t])
    assert r["imgs"] == 0 and r["links"] == ["#methods-ranking"]
    assert r["answer"] == evil


def test_ask_box_absent_while_queue_open():
    """D14: while a labelling queue shows records there is no way to Ask WIDER: the top-bar button is hidden and the
    Ask WIDER tab, opened, holds only the sentence that it is not offered while you label (no box, no history asked
    for); when the queue empties the button returns and the tab holds the box again."""
    r = _run("""
      const res = {};
      window.queueOpen = () => true; askBoxRedraw(); await settle();
      res.tb = shown("#tb-ask");
      Guide.open("ask"); await settle();
      res.open = { off: txt($("#ask-off")), offShown: shown("#ask-off"), box: shown("#ask-box"), top: shown("#ask-top"),
                   tab: $("#ptab-ask").getAttribute("aria-selected") };
      res.callsOpen = calls.map(c => c[0]);
      window.queueOpen = () => false; askBoxRedraw(); await settle();
      res.closed = { tb: shown("#tb-ask"), expanded: $("#tb-ask").getAttribute("aria-expanded"), offShown: shown("#ask-off"),
                     box: shown("#ask-box"), ta: !!$("#ask-q") };
      out(res);
    """, start=HELPERS + 'navigateHash("#rank"); await settle();')
    assert r["tb"] is False
    assert r["open"] == {"off": "Ask WIDER is not offered while you label, so your labels stay your own.",
                         "offShown": True, "box": False, "top": False, "tab": "true"}
    assert "/api/assistant/history" not in r["callsOpen"]
    assert r["closed"] == {"tb": True, "expanded": "true", "offShown": False, "box": True, "ta": True}


def test_no_model_sentence():
    """Without a query-creation model the box says so and links to the Model page; without a name it asks for one."""
    r = _run("out({ text: txt(box()), link: box().querySelector('a').getAttribute('href'), ta: !!box().querySelector('textarea') });",
             backend=None)
    assert "Ask WIDER needs a model. Set one on Model for query creation." in r["text"]
    assert r["link"] == "#model" and not r["ta"]
    r = _run("out({ text: txt(box()), ta: !!box().querySelector('textarea') });", author="")
    assert "Type your name in Your name above: your conversation is kept under your name." in r["text"] and not r["ta"]


def test_ask_disabled_when_empty():
    """Ask cannot be pressed while the box is empty or blank; typing enables it; Ctrl+Enter asks, Enter does not."""
    r = _run("""
      const res = {}, ta = () => $("#ask-q");
      res.empty = $("#ask-go").disabled;
      ta().value = "   "; ta().dispatchEvent(new Event("input")); res.blank = $("#ask-go").disabled;
      ta().value = "Why rank?"; ta().dispatchEvent(new Event("input")); res.typed = $("#ask-go").disabled;
      ta().focus(); press("Enter"); await settle(); res.enter = posts().length;
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting for fake (fake-model)…" };
      replies["/api/jobs/j1"] = { status: "running", message: "" };
      ta().focus(); press("Enter", { ctrlKey: true }); await settle(); res.ctrl = posts().map(c => [c[0], c[2]]);
      out(res);
    """)
    assert r["empty"] is True and r["blank"] is True and r["typed"] is False and r["enter"] == 0
    assert r["ctrl"] == [["/api/assistant/ask", {"question": "Why rank?", "page": "rank", "depth": None}]]


def test_ask_box_kept_through_leaving_page():
    """A typed question survives leaving the Ask page and coming back, and a stage refresh keeps the question, the focus
    and the "Asking about:" choice; after an answer the box is empty, the new turn shows, and the focus is in the
    box."""
    r = _run("""
      const res = {};
      $("#ask-q").value = "Why rank?"; $("#ask-q").dispatchEvent(new Event("input"));
      navigateHash("#rank"); await settle(); navigateHash("#ask"); await settle();
      res.back = { value: $("#ask-q").value, about: $("#ask-about").value };
      $("#ask-about").value = "general"; $("#ask-about").dispatchEvent(new Event("change")); $("#ask-q").focus();
      markCurrentPage("ask"); await settle();   // what a stage refresh does
      res.refresh = { value: $("#ask-q").value, focus: focused(), about: $("#ask-about").value };
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting for fake (fake-model)…" };
      replies["/api/jobs/j1"] = { status: "done", result: { turn: 1, consent: null } };
      replies["/api/assistant/history"] = { author: "ana", turns: [ """ + json.dumps(TURN) + """ ] };
      $("#ask-go").click(); await wait(60); await settle();
      res.after = { value: $("#ask-q").value, focus: focused(), turns: box().querySelectorAll(".ask-turn").length };
      res.posted = posts().map(c => c[2].page);
      out(res);
    """)
    assert r["back"] == {"value": "Why rank?", "about": "rank"}
    assert r["refresh"] == {"value": "Why rank?", "focus": "#ask-q", "about": "general"}
    assert r["after"] == {"value": "", "focus": "#ask-q", "turns": 1}
    assert r["posted"] == ["general"]


def test_ask_stop_keeps_question():
    """While a question runs the box says "Waiting for fake (fake-model)…" with Stop and Ask cannot be pressed; Stop
    cancels the job, keeps the question in the box and adds no turn."""
    r = _run("""
      const res = {};
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting for fake (fake-model)…" };
      replies["/api/jobs/j1"] = { status: "running", message: "" };
      replies["/api/jobs/j1/cancel"] = { ok: true };
      $("#ask-q").value = "Why rank?"; $("#ask-q").dispatchEvent(new Event("input"));
      $("#ask-go").click(); await settle();
      res.wait = txt(box().querySelector(".ask-wait")); res.disabled = $("#ask-go").disabled;
      box().querySelector(".ask-stop").click(); await wait(30); await settle();
      res.after = { value: $("#ask-q").value, wait: !!box().querySelector(".ask-wait"), turns: box().querySelectorAll(".ask-turn").length,
                    go: $("#ask-go").disabled };
      res.posts = posts().map(c => c[0]);
      out(res);
    """)
    assert r["wait"] == "Waiting for fake (fake-model)… Stop" and r["disabled"] is True
    assert r["after"] == {"value": "Why rank?", "wait": False, "turns": 0, "go": False}
    assert r["posts"] == ["/api/assistant/ask", "/api/jobs/j1/cancel"]


def test_copy_records_nothing():
    """A draft reads "Draft by fake: check it before you use it"; Copy writes its text to the clipboard and sends
    nothing to the server."""
    t = dict(TURN, drafts=[{"for": "methods", "text": "Records were ranked by similarity."}])
    r = _run("""
      const got = []; global.navigator = { clipboard: { writeText: async (s) => { got.push(s); } } };
      const before = calls.length;
      const d = box().querySelector(".ask-draft");
      const head = txt(d.querySelector(".ask-draft-h"));
      [...d.querySelectorAll("button")].find(b => b.textContent === "Copy").click(); await settle();
      out({ head, got, sent: calls.slice(before).map(c => c[0]) });
    """, turns=[t])
    assert r["head"] == "Draft by fake: check it before you use it"
    assert r["got"] == ["Records were ranked by similarity."]
    assert [c for c in r["sent"] if not c.startswith(("/api/jobs", "/api/stages"))] == []


def test_more_detail_resends_with_depth():
    """More detail and Simpler send the turn's question again with depth detail or simpler."""
    r = _run("""
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting…" };
      replies["/api/jobs/j1"] = { status: "cancelled" };
      const btn = (name) => [...box().querySelectorAll(".ask-acts button")].find(b => b.textContent === name);
      btn("More detail").click(); await wait(30); await settle();
      btn("Simpler").click(); await wait(30); await settle();
      out(posts().map(c => c[2]));
    """, turns=[TURN])
    assert r == [{"question": "Why rank?", "page": "rank", "depth": "detail"},
                 {"question": "Why rank?", "page": "rank", "depth": "simpler"}]


def test_didnt_help_marks_turn():
    """Didn't help posts the mark for the turn and shows as pressed; pressed again it takes the mark off."""
    r = _run("""
      replies["/api/assistant/unhelpful"] = { ok: true };
      const btn = () => [...box().querySelectorAll(".ask-acts button")].find(b => b.textContent === "Didn't help");
      const res = { before: btn().getAttribute("aria-pressed") };
      btn().click(); await settle(); res.on = btn().getAttribute("aria-pressed");
      btn().click(); await settle(); res.off = btn().getAttribute("aria-pressed");
      res.posts = posts().map(c => [c[0], c[2]]);
      out(res);
    """, turns=[TURN])
    assert r["before"] == "false" and r["on"] == "true" and r["off"] == "false"
    assert r["posts"] == [["/api/assistant/unhelpful", {"turn": 1, "value": True}],
                          ["/api/assistant/unhelpful", {"turn": 1, "value": False}]]


def test_forget_dialog():
    """Forget this conversation asks first with the count; Cancel deletes nothing; OK deletes and empties the list."""
    r = _run("""
      replies["/api/assistant/history"] = { deleted: 2 };
      const forget = () => box().querySelector(".ask-forget");
      const res = {};
      forget().click(); await settle();
      let d = $("dialog.choice-dialog"); res.text = txt(d);
      d.querySelector("[data-act=cancel]") ? d.querySelector("[data-act=cancel]").click() : d.close(); await settle();
      res.cancel = posts().length;
      forget().click(); await settle();
      $("dialog.choice-dialog [data-act=ok]").click(); await settle();
      res.posts = posts().map(c => [c[0], c[1]]); res.turns = box().querySelectorAll(".ask-turn").length; res.left = !!forget();
      out(res);
    """, turns=[TURN, dict(TURN, n=2)])
    assert "Deletes your 2 questions and answers on this computer. This cannot be undone." in r["text"]
    assert r["cancel"] == 0
    assert r["posts"] == [["/api/assistant/history", "DELETE"]] and r["turns"] == 0 and r["left"] is False


def test_keyword_note_allow_opens_embedding_consent():
    """A keyword-only answer shows its note and "Allow fe for Ask WIDER…"; Allow opens the embedding consent dialog,
    and Allow there stores the consent for the assistant operation."""
    info = dict(REFUSAL["backend_consent_required"], backend="fe", kind="embedding", fingerprint="beef")
    note = "Searched by keywords only: the embedding service needs your consent."
    t = dict(TURN, notes=[note], retrieval="keywords")
    r = _run("""
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting…" };
      replies["/api/jobs/j1"] = { status: "done", result: { turn: 1, consent: { backend_consent_required: """ + json.dumps(info) + """ } } };
      replies["/api/embeddings/fe/consent"] = { ok: true };
      replies["/api/providers/fe/consent"] = { ok: true };
      $("#ask-q").value = "Why rank?"; $("#ask-q").dispatchEvent(new Event("input"));
      replies["/api/assistant/history"] = { author: "ana", turns: [ """ + json.dumps(t) + """ ] };
      $("#ask-go").click(); await wait(60); await settle();
      const res = { text: txt(box()) };
      const allow = box().querySelector(".ask-allow"); res.allow = txt(allow);
      allow.click(); await settle();
      const d = $("dialog.consent-dialog"); res.dialog = !!d;
      d.querySelector("[data-act=allow]").click(); await settle();
      res.posts = posts().map(c => c[0]); res.after = txt(box());
      out(res);
    """, turns=[])
    assert note in r["text"] and r["allow"] == "Allow fe for Ask WIDER…" and r["dialog"]
    assert any(p.endswith("/fe/consent") for p in r["posts"])
    assert "Allowed: the next question searches by meaning too." in r["after"]


# ---------------------------------------------------------------------------------- edits in the panel (task 8.4)
def test_unreviewed_source_marked():
    """A source in ``unreviewed`` reads "(not yet reviewed by the author)" after its title in "Based on:"; a reviewed
    one has no mark. While a new meaning index is built the wait line says "Updating the index of WIDER's
    documents…"."""
    t = dict(TURN, unreviewed=["methods/ranking/why"], based_on=[
        TURN["based_on"][0], {"id": "methods/ranking/why", "title": "Ranking: why", "anchor": "#methods-ranking-why",
                              "command": "wider guide methods ranking --why", "unreviewed": True}])
    r = _run("""
      const items = [...box().querySelectorAll(".ask-sources li")].map(txt);
      replies["/api/assistant/ask"] = { job_id: "j2", waiting: "Waiting for fake (fake-model)…" };
      replies["/api/jobs/j2"] = { status: "running", message: "Updating the index of WIDER's documents…" };
      replies["/api/jobs/j2/cancel"] = { ok: true };
      $("#ask-q").value = "Why rank?"; $("#ask-q").dispatchEvent(new Event("input"));
      $("#ask-go").click(); await wait(30); await settle();
      out({ items, wait: txt(box()) });
    """, turns=[t])
    assert r["items"][0].startswith("Ranking: in short") and "not yet reviewed" not in r["items"][0]
    assert r["items"][1].startswith("Ranking: why (not yet reviewed by the author)")
    assert "Updating the index of WIDER's documents…" in r["wait"]


def test_turn_marked_docs_changed():
    """A turn with ``docs_changed`` reads "Answered before WIDER's documents changed."; one without does not."""
    r = _run("""
      out([...box().querySelectorAll(".ask-turn")].map(txt));
    """, turns=[dict(TURN, docs_changed=True), dict(TURN, n=2, question="And dedup?")])
    assert len(r) == 2
    assert "Answered before WIDER's documents changed." in r[0]
    assert "Answered before WIDER's documents changed." not in r[1]


# ---------------------------------------------------------------------------------------- Settings (task 8.3)
def test_settings_budget_card():
    """Settings' "Ask WIDER" card under Language models: the budget now and whether it is the default, the default for
    the current model's place, a labelled number field, "Save" (POST of the number), "Use the default" (POST of null,
    disabled while the default is used), a refusal shown in the card, and the CLI twin named."""
    r = _run("""
      let state = { value: null, budget: 3000, default: 3000, min: 500, max: 200000 };
      replies["/api/assistant/budget"] = (url, opts) => {
        if (opts && opts.method === "POST") {
          const v = JSON.parse(opts.body).value;
          if (v !== null && v < 500) { const e = new Error("the budget must be a whole number from 500 to 200,000 tokens"); e.status = 400; return e; }
          state = { ...state, value: v, budget: v === null ? state.default : v };
        }
        return state;
      };
      const card = () => $("#settings-assistant");
      const res = { part: card().dataset.settingsPart };
      await renderAskBudget("#settings-assistant"); await settle();
      const c = card(), lab = c.querySelector("label");
      res.first = txt(c); res.for = lab.getAttribute("for"); res.val = c.querySelector("#ask-budget").value;
      res.defDisabled = c.querySelector("[data-budget-default]").disabled;
      c.querySelector("#ask-budget").value = "8000"; c.querySelector("[data-budget-save]").click(); await settle();
      res.saved = txt(card()); res.defAfter = card().querySelector("[data-budget-default]").disabled;
      card().querySelector("#ask-budget").value = "100"; card().querySelector("[data-budget-save]").click(); await settle();
      res.refused = txt(card().querySelector("[data-budget-msg]"));
      card().querySelector("#ask-budget").value = "abc"; card().querySelector("[data-budget-save]").click(); await settle();
      res.notNumber = txt(card().querySelector("[data-budget-msg]"));
      card().querySelector("[data-budget-default]").click(); await settle();
      res.back = txt(card());
      res.posts = posts().filter(p => p[0] === "/api/assistant/budget").map(p => p[2]);
      out(res);
    """)
    assert r["part"] == "models" and r["for"] == "ask-budget" and r["val"] == "3000" and r["defDisabled"] is True
    assert "Now: 3,000 tokens (the default for the model's place)." in r["first"]
    assert "The default for the current model's place is 3,000 tokens." in r["first"]
    assert "wider assistant budget --default" in r["first"]
    assert "Now: 8,000 tokens." in r["saved"] and "Saved: 8,000 tokens." in r["saved"] and r["defAfter"] is False
    assert "500 to 200,000" in r["refused"] and "Type a whole number from 500 to 200,000." == r["notNumber"]
    assert "Now: 3,000 tokens (the default for the model's place)." in r["back"]
    assert r["posts"] == [{"value": 8000}, {"value": 100}, {"value": None}]


NAME_CHANGE = """
  const convs = { ana: ANA, ben: BEN };
  const who = () => localStorage.getItem("wider.reviewer");
  const sent = [];
  replies["/api/assistant/history"] = (url, opts) => { const m = (opts && opts.method) || "GET"; sent.push([m, who()]);
     if (m === "DELETE") { const n = convs[who()].length; convs[who()] = []; return { deleted: n }; }
     return { author: who(), turns: convs[who()] }; };
  replies["/api/assistant/unhelpful"] = (url, opts) => { sent.push(["UNHELPFUL", who()]); return { ok: 1 }; };
  await settle(20);
  const res = { before: txt(box()).includes("ANA-Q1") };
  localStorage.setItem("wider.reviewer", "ben");     // another tab saved another name
"""


def _name_change(body: str) -> dict:
    ana = [dict(TURN, n=1, question="ANA-Q1"), dict(TURN, n=2, question="ANA-Q2")]
    ben = [dict(TURN, n=i, question=f"BEN-Q{i}") for i in (1, 2, 3)]
    # on the stand-in: D33's WiderName reloads the page when another tab switches the name, so only the stand-in can
    # see a name change under a drawn conversation
    pre = f"delete window.WiderName; const ANA = {json.dumps(ana)}, BEN = {json.dumps(ben)};"
    return _run(pre + NAME_CHANGE + body, turns=ana)


def test_name_change_redraws_before_forget():
    """Risk R1: after the stored name changes, Forget does not delete with the old reviewer's count: the panel is
    redrawn for the new name first (nothing deleted), and a Forget pressed then counts and deletes the new name's."""
    r = _name_change("""
      box().querySelector(".ask-forget").click(); await settle(20);
      res.dialogAfterChange = !!$("dialog.choice-dialog");
      res.text = txt(box());
      box().querySelector(".ask-forget").click(); await settle();
      res.dialog = txt($("dialog.choice-dialog"));
      $("dialog.choice-dialog [data-act=ok]").click(); await settle(20);
      res.sent = sent; res.convs = { ana: convs.ana.length, ben: convs.ben.length };
      out(res);
    """)
    assert r["before"] and r["dialogAfterChange"] is False
    assert "The name on this tab is now ben: the conversation shown is ben's." in r["text"]
    assert "BEN-Q1" in r["text"] and "ANA-Q1" not in r["text"]
    assert "Deletes your 3 questions and answers" in r["dialog"]
    assert r["convs"] == {"ana": 2, "ben": 0}
    assert ["DELETE", "ben"] in r["sent"] and ["DELETE", "ana"] not in r["sent"]


def test_forget_rechecks_name_after_confirm():
    """Risk-checker R1 (group 12): a name saved in another tab while Forget's question is open never deletes the new
    name's conversation with the count confirmed for the old one; nothing is deleted."""
    r = _name_change("""
      localStorage.setItem("wider.reviewer", "ana");   // start as ana again
      box().querySelector(".ask-forget").click(); await settle();
      res.dialog = txt($("dialog.choice-dialog"));
      localStorage.setItem("wider.reviewer", "ben");
      const ev = new Event("storage"); ev.key = "wider.reviewer"; window.dispatchEvent(ev); await settle(20);
      $("dialog.choice-dialog [data-act=ok]").click(); await settle(20);
      res.sent = sent; res.convs = { ana: convs.ana.length, ben: convs.ben.length };
      out(res);
    """)
    assert "Deletes your 2 questions and answers" in r["dialog"]
    assert r["convs"] == {"ana": 2, "ben": 3} and not any(m == "DELETE" for m, _ in r["sent"])


def test_long_name_finds_its_conversation():
    """Risk-checker R2 (group 12): a name the server cuts to 80 characters still shows its own conversation."""
    r = _run("""await settle(20); out({ text: txt(box()) });""", turns=[TURN], author="a" * 81 + "\t",
             extra={"/api/assistant/history": {"author": "a" * 80, "turns": [TURN]}})
    assert "Why rank?" in r["text"]


def test_name_change_shows_new_name_and_guards_buttons():
    """Risk R2: a name change saved in another tab ("storage") redraws the page for the new name; Didn't help or
    Simpler on a turn drawn for the old name sends nothing and redraws instead."""
    r = _name_change("""
      const ev = new Event("storage"); ev.key = "wider.reviewer"; window.dispatchEvent(ev); await wait(5); await settle(20);
      res.afterChange = txt(box());
      // back to ana without an event, then press Didn't help and Simpler on ben's (stale) turns
      localStorage.setItem("wider.reviewer", "ana");
      const before = sent.length;
      [...box().querySelectorAll(".ask-acts button")].find(b => b.textContent === "Didn't help").click(); await settle(20);
      res.unhelpfulSent = sent.slice(before).filter(s => s[0] === "UNHELPFUL");
      res.afterGuard = txt(box());
      out(res);
    """)
    assert "BEN-Q1" in r["afterChange"] and "ANA-Q1" not in r["afterChange"]
    assert r["unhelpfulSent"] == []
    assert "ANA-Q1" in r["afterGuard"] and "BEN-Q1" not in r["afterGuard"]


def test_forget_disabled_while_question_runs():
    """Risk R4: while a question runs, Forget this conversation is disabled (the running question would be stored
    after the forget)."""
    r = _run("""
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting for fake (fake-model)…" };
      replies["/api/jobs/j1"] = { status: "running", message: "" };
      await settle(20);
      const ta = box().querySelector("#ask-q"); ta.value = "Why rank again?"; ta.dispatchEvent(new Event("input"));
      box().querySelector("#ask-go").click(); await settle(20);
      out({ disabled: box().querySelector(".ask-forget").disabled, wait: txt(box()).includes("Waiting for fake") });
    """, turns=[TURN])
    assert r["wait"] and r["disabled"] is True


@pytest.mark.parametrize("how", ["failed job", "refused request"])
def test_failed_question_red_error_keeps_question(how):
    """A malformed answer, an unreachable backend or a backend that cannot write text ends the job "failed" (or the
    request is refused): the sentence shows as an alert under the box in the danger colour, the question stays in
    the box so it can be sent again, and no turn is added."""
    msg = "WIDER could not reach b (m-b). Check that it is running."
    if how == "failed job":
        setup = (f'replies["/api/assistant/ask"] = {{ job_id: "j1", waiting: "Waiting for fake (fake-model)…" }};'
                 f'replies["/api/jobs/j1"] = {{ status: "failed", error: {json.dumps(msg)} }};')
    else:
        setup = f'replies["/api/assistant/ask"] = () => {{ throw new Error({json.dumps(msg)}); }};'
    r = _run(setup + """
      $("#ask-q").value = "Why rank?"; $("#ask-q").dispatchEvent(new Event("input"));
      $("#ask-go").click(); await wait(30); await settle(20);
      const e = box().querySelector(".ask-error");
      out({ err: txt(e), role: e && e.getAttribute("role"), value: $("#ask-q").value,
            turns: box().querySelectorAll(".ask-turn").length, go: $("#ask-go").disabled });
    """)
    assert r["err"] == msg and r["role"] == "alert"
    assert r["value"] == "Why rank?" and r["turns"] == 0 and r["go"] is False
    css = (ROOT / "wider" / "ui" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".ask-box .ask-error { color:var(--danger); }" in css


def test_show_link_in_answer_focuses_never_clicks():
    """A turn naming a known button draws "Show: <button>"; pressing it opens that page and moves the focus to its
    button without pressing it; a name not on the page leaves the page and the panel as they are, and the Ask WIDER
    tab says so, with the same "Asking about:" choice; nothing is pressed."""
    turn = dict(TURN, show=[{"page": "rank", "button": "Rank records"}, {"page": "rank", "button": "Merge all"}])
    r = _run("""
      const btn = document.createElement("button"); btn.textContent = "Rank records"; let pressed = 0;
      btn.addEventListener("click", () => pressed++); $("#page-rank").appendChild(btn);
      const links = [...box().querySelectorAll(".ask-turn .guide-show button")];
      const res = { links: links.map(b => [txt(b), b.getAttribute("aria-label")]) };
      links[0].click(); await settle();
      res.first = { page: currentPage, focused: document.activeElement === btn, pressed };
      links[1].click(); await wait(2600); await settle();
      res.second = { page: currentPage, hash: location.hash, about: $("#ask-about").value, pressed,
                     tab: shown("#ppane-ask"), text: txt(box()) };
      out(res);
    """, turns=[turn])
    assert r["links"] == [["Rank records", "Show Rank records"], ["Merge all", "Show Merge all"]]
    assert r["first"] == {"page": "rank", "focused": True, "pressed": 0}
    assert r["second"]["page"] == "rank" and r["second"]["hash"] == "#rank" and r["second"]["about"] == "rank"
    assert r["second"]["tab"] is True
    assert "Merge all is not on the page now." in r["second"]["text"] and r["second"]["pressed"] == 0


def test_show_link_not_found_never_undoes_a_later_page():
    """A Show link whose button never appears waits for its page; when the reviewer has meanwhile moved to another
    page, or pressed a later Show link, the slow one changes nothing (walkthrough finding, task 12.5)."""
    turn = dict(TURN, show=[{"page": "rank", "button": "Merge all"}])
    r = _run("""
      const link = box().querySelector(".ask-turn .guide-show button");
      link.click(); await settle();
      navigateHash("#home"); await wait(2600);
      out({ page: currentPage, hash: location.hash });
    """, turns=[turn])
    assert r == {"page": "home", "hash": "#home"}

# ------------------------------------------------------------------------- the tab of the side panel (tasks 12, 13)
def test_ask_tab_follows_page():
    """The top-bar button turns the side panel (open on Guide by default) to its Ask WIDER tab, "Asking about: Ranking" for the page shown,
    changeable to WIDER in general or another page with a guide; the question is sent with that page. From Home the
    top-bar button is not shown and the panel is not either; #ask from there asks about WIDER in general."""
    r = _run("""
      const res = { tb: shown("#tb-ask"), expanded: $("#tb-ask").getAttribute("aria-expanded"), panel: shown("#guide-panel"),
                    guide: shown("#ppane-guide"), href: $("#tb-ask").getAttribute("href") };
      $("#tb-ask").click(); await settle();
      const sel = $("#ask-about"), lab = $("#ask-top label");
      res.tab = { panel: shown("#guide-panel"), pane: shown("#ppane-ask"), guidePane: shown("#ppane-guide"),
                  selected: $("#ptab-ask").getAttribute("aria-selected"), label: txt(lab), for: lab.getAttribute("for"),
                  about: sel.value, options: [...sel.querySelectorAll("option")].map(o => [o.value, txt(o)]),
                  expanded: $("#tb-ask").getAttribute("aria-expanded"), guideBtn: $("#tb-guide-btn").getAttribute("aria-expanded"),
                  focus: focused(), stored: localStorage.getItem("wider.panelTab") };
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting…" };
      replies["/api/jobs/j1"] = { status: "cancelled" };
      $("#ask-q").value = "Why?"; $("#ask-q").dispatchEvent(new Event("input"));
      $("#ask-go").click(); await wait(30); await settle();
      res.posted = posts().map(c => c[2].page);
      navigateHash("#home"); await settle();
      res.home = { tb: shown("#tb-ask"), panel: shown("#guide-panel") };
      navigateHash("#ask"); await settle();
      res.fromHome = { page: currentPage, hash: location.hash, about: $("#ask-about").value, pane: shown("#ppane-ask") };
      out(res);
    """, start=HELPERS + 'navigateHash("#rank"); await settle();')
    assert r["tb"] and r["expanded"] == "false" and r["panel"] and r["guide"] and r["href"] is None   # Guide open first
    t = r["tab"]
    assert t["panel"] and t["pane"] and not t["guidePane"] and t["selected"] == "true" and t["stored"] == "ask"
    assert t["label"] == "Asking about:" and t["for"] == "ask-about" and t["about"] == "rank"
    assert t["expanded"] == "true" and t["guideBtn"] == "false" and t["focus"] == "#ask-q"
    assert t["options"][0] == ["general", "WIDER in general"] and ["rank", "Ranking"] in t["options"]
    assert not any(v in ("home", "methods", "ask") for v, _ in t["options"])
    assert r["posted"] == ["rank"]
    assert r["home"] == {"tb": False, "panel": False}
    assert r["fromHome"] == {"page": "overview", "hash": "#overview", "about": "general", "pane": True}


def test_panel_tabs_keys():
    """D23: the panel's two tabs are a tablist: one tab in the Tab order (roving tab index), the arrow keys move and
    choose round the two, Home and End go to the first and last, a click chooses; the tab chosen is remembered in
    wider.panelTab and each tab controls its labelled pane. The other tab starts at the panel's top."""
    r = _run("""
      const st = () => ({ focus: focused(), stored: localStorage.getItem("wider.panelTab"),
        sel: ["guide", "ask"].map(t => $("#ptab-" + t).getAttribute("aria-selected")),
        idx: ["guide", "ask"].map(t => $("#ptab-" + t).tabIndex), panes: ["guide", "ask"].map(t => shown("#ppane-" + t)) });
      const list = $("#guide-panel [role=tablist]"), res = {};
      res.roles = { list: list.getAttribute("aria-label"), tabs: [...list.querySelectorAll("[role=tab]")].map(b =>
        [b.id, txt(b), b.getAttribute("aria-controls")]), panes: [...$("#guide-panel").querySelectorAll("[role=tabpanel]")].map(p =>
        [p.id, p.getAttribute("aria-labelledby")]) };
      $("#ptab-ask").focus(); press("ArrowRight"); res.right = st();
      press("ArrowRight"); res.wrap = st();
      press("Home"); res.home = st();
      press("End"); res.end = st();
      press("ArrowLeft"); res.left = st();
      $("#ptab-ask").click(); res.click = st(); delete res.click.focus;   // the fake DOM's click does not focus
      $("#guide-panel").scrollTop = 400; $("#ptab-ask").click(); res.same = $("#guide-panel").scrollTop;
      $("#ptab-guide").click(); res.other = $("#guide-panel").scrollTop;
      out(res);
    """)
    assert r["roles"] == {"list": "Side panel", "tabs": [["ptab-guide", "Guide", "ppane-guide"],
                                                       ["ptab-ask", "Ask WIDER", "ppane-ask"]],
                          "panes": [["ppane-guide", "ptab-guide"], ["ppane-ask", "ptab-ask"]]}
    g = {"focus": "#ptab-guide", "stored": "guide", "sel": ["true", "false"], "idx": [0, -1], "panes": [True, False]}
    a = {"focus": "#ptab-ask", "stored": "ask", "sel": ["false", "true"], "idx": [-1, 0], "panes": [False, True]}
    assert r["right"] == g and r["wrap"] == a and r["home"] == g and r["end"] == a and r["left"] == g
    assert r["click"] == {k: v for k, v in a.items() if k != "focus"}
    assert (r["same"], r["other"]) == (400, 0)   # the other tab starts at its top


def test_ask_page_general():
    """Asking about WIDER in general sends the page "general"; a turn about WIDER in general has no Show line."""
    r = _run("""
      $("#ask-about").value = "general"; $("#ask-about").dispatchEvent(new Event("change"));
      replies["/api/assistant/ask"] = { job_id: "j1", waiting: "Waiting…" };
      replies["/api/jobs/j1"] = { status: "cancelled" };
      $("#ask-q").value = "What is WIDER?"; $("#ask-q").dispatchEvent(new Event("input"));
      $("#ask-go").click(); await wait(30); await settle();
      out({ posted: posts().map(c => c[2]), show: box().querySelectorAll(".guide-show").length });
    """, turns=[dict(TURN, page="general", show=[])])
    assert r["posted"] == [{"question": "What is WIDER?", "page": "general", "depth": None}]
    assert r["show"] == 0


def test_ask_page_cold_open_and_no_project():
    """#ask opened first in a page load (a reload of an old link) opens Overview with the panel on Ask WIDER, about
    WIDER in general; without a project there is no top-bar button and no panel, and Home says to open a project
    first."""
    r = _run("""
      navigateHash("#ask"); await settle();
      out({ about: $("#ask-about").value, page: currentPage, pane: shown("#ppane-ask") });
    """, start=HELPERS + 'currentPage = "home";')   # where app.js starts
    assert r == {"about": "general", "page": "overview", "pane": True}
    r = _run("""
      navigateHash("#ask"); await settle();
      out({ home: txt($("#home-status")), page: currentPage, tb: shown("#tb-ask"), panel: shown("#guide-panel"),
            about: !!$("#ask-about"), name: !!$("#ask-name"), ta: !!$("#ask-q") });
    """, start=HELPERS, project="")
    assert "Open a project first: Ask WIDER uses the project's model." in r["home"]
    assert r == dict(r, page="home", tb=False, panel=False, about=False, name=False, ta=False)


@pytest.mark.parametrize("with_name_api", [True, False], ids=["WiderName", "stand-in"])
def test_ask_page_name_field(with_name_api):
    """D23: the Ask page reads the one name (wider.reviewer) the other pages sign with. Without a name nothing can be
    asked, and a small "Your name" field writes that name, through WiderName or, without it, the stand-in: a first
    name is stored and the conversation shows without a reload, and the field gives way to "Asking as ana", which
    points at Change beside Working as in the top bar (ui-redesign D33) when the top bar has it."""
    r = _run(("delete window.WiderName;" if not with_name_api else "") + """
      const res = {}, inp = () => $("#ask-name"), lab = $("label[for=ask-name]");
      const had = $("#tb-name"); if (had) had.remove();   // first without the top bar's Working as, then with it
      res.none = { label: txt(lab), text: txt(box()), ta: !!$("#ask-q"), value: inp().value };
      replies["/api/assistant/history"] = { author: "ana", turns: [ """ + json.dumps(TURN) + """ ] };
      inp().value = "ana"; inp().dispatchEvent(new Event("change")); await settle(20);
      res.first = { stored: localStorage.getItem("wider.reviewer"), ta: !!$("#ask-q"), reloads: window.reloads,
                    history: calls.filter(c => c[0] === "/api/assistant/history").length, field: !!inp(),
                    line: txt($("#ask-name-row")), turns: [...box().querySelectorAll(".ask-turn")].map(txt).join(" ") };
      const tb = document.createElement("div"); tb.id = "tb-name"; document.body.appendChild(tb);
      window.askBoxRedraw(); await settle();
      res.withTopBar = txt($("#ask-name-row"));
      out(res);
    """, author="")
    assert r["none"]["label"] == "Your name" and r["none"]["value"] == "" and not r["none"]["ta"]
    assert "Type your name in Your name above: your conversation is kept under your name." in r["none"]["text"]
    assert "Why rank?" in r["first"].pop("turns")   # ana's conversation shows
    assert r["first"] == {"stored": "ana", "ta": True, "reloads": 0, "history": 1, "field": False,
                          "line": "Asking as ana: your conversation is kept under this name."}
    assert r["withTopBar"] == ("Asking as ana: your conversation is kept under this name. "
                               "To change it, use Change beside Working as in the top bar.")
