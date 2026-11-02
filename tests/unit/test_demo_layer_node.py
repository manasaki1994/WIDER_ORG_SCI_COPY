"""The Demo Mode bar, the start card, Exit and the page title (openspec change interactive-demo, design D7)."""
import re

from tests.unit.demo_node import DEMO_DIR, run_demo

BAR_IDS = ["demo-guided", "demo-free", "demo-reset", "demo-exit"]


def test_bar_on_every_page():
    """The bar is the first thing in the page, on any page the visitor opens (the app swaps pages inside <body>)."""
    out = run_demo("""
      for (const hash of ["", "#home", "#rank", "#screen"]) {
        location.hash = hash;
        boot();
        const bar = document.body.children[0];
        out[hash || "none"] = [bar.id, bar.getAttribute("aria-label"), bar.textContent.includes("Demo mode"),
                               bar.textContent.includes("Invented data, simulated results. Nothing you do here leaves this browser tab."),
                               ["demo-guided", "demo-free", "demo-reset", "demo-exit"].every((id) => { const b = $(id); return !!b && bar.textContent.includes(b.textContent); }),
                               document.title.startsWith("Demo — "),
                               document.documentElement.classList.contains("demo-on")];
      }
    """)
    assert set(map(tuple, out.values())) == {("demo-bar", "Demo mode", True, True, True, True, True)}


def test_title_keeps_demo_prefix_after_job_poll():
    out = run_demo("""
      boot();
      out.start = document.title;
      document.title = "WIDER";               // jobs.js on every poll
      out.poll = document.title;
      document.title = "(40%) WIDER";
      out.progress = document.title;
      document.title = "Demo — WIDER";
      out.again = document.title;
    """)
    assert out == {"start": "Demo — WIDER", "poll": "Demo — WIDER", "progress": "Demo — (40%) WIDER",
                   "again": "Demo — WIDER"}


def test_controls_have_names():
    out = run_demo(f"""
      boot();
      for (const id of {BAR_IDS!r}) out[id] = [$(id).tagName, $(id).getAttribute("type"), $(id).textContent];
      out.pressed = [$("demo-guided").getAttribute("aria-pressed"), $("demo-free").getAttribute("aria-pressed")];
      out.notice = [$("demo-notice").getAttribute("role"), $("demo-notice").getAttribute("aria-live")];
      out.group = document.body.children[0].children.at(-1).children[0].getAttribute("aria-label");
    """)
    for i in BAR_IDS:
        tag, kind, text = out[i]
        assert (tag, kind) == ("BUTTON", "button") and text.strip(), i
    assert out["pressed"] == ["true", "false"]
    assert out["notice"] == ["status", "polite"]
    assert out["group"] == "How to explore the demo"


def test_start_card():
    out = run_demo("""
      boot();
      const card = $("demo-start");
      out.open = card.open;
      out.title = $("demo-start-title").textContent;
      out.focus = document.activeElement && document.activeElement.id;
      out.choices = ["demo-start-quick", "demo-start-all", "demo-start-free"].map((id) => $(id).textContent);
      out.narrow = !!$("demo-start").children.find((c) => c.getAttribute("class") === "demo-narrow");
      out.escape = card.fire("cancel").defaultPrevented;
      $("demo-start-quick").click();
      out.after = [Demo.state.mode, Demo.state.path, Demo.state.checkpoint, !!$("demo-start"), location.hash];
      boot();                                  // a reload: the card is not shown again
      out.again = !!$("demo-start");
    """)
    assert out["open"] and out["title"] == "Try WIDER with an invented review"
    assert out["focus"] == "demo-start-quick"
    assert out["choices"][0].startswith("Quick tour (recommended)") and "about 5 minutes" in out["choices"][0]
    assert out["choices"][1].startswith("All chapters") and out["choices"][2].startswith("Explore freely")
    assert out["narrow"] is True
    assert out["escape"] is True
    assert out["after"] == ["guided", "core", "one", False, "home"]
    assert out["again"] is False


def test_start_card_closed_without_choice_explores_freely():
    out = run_demo("""
      boot();
      $("demo-start").close();
      await tick();
      out.mode = Demo.state.mode;
      out.checkpoint = Demo.state.checkpoint;
      out.reload = location.reloaded;
      out.replaced = location.replaced;
    """)
    assert out == {"mode": "free", "checkpoint": "two", "reload": "/demo/#home", "replaced": None}


def test_start_card_narrow_note():
    """The note is always in the card; demo.css shows it only below 900px wide, so it follows a resized window."""
    out = run_demo("""
      boot();
      const note = document.body.children.at(-1).children.find((n) => n.classList.contains && n.getAttribute("class") === "demo-narrow");
      out.text = note && note.textContent;
    """)
    assert out["text"] == "This demo is made for a laptop or desktop screen."
    css = (DEMO_DIR / "demo.css").read_text(encoding="utf-8")
    assert re.search(r"^\.demo-narrow \{ display: none;", css, re.MULTILINE)
    narrow = css.split("@media (max-width: 900px)", 1)[1]
    assert ".demo-narrow { display: block; }" in narrow


def test_app_top_bar_stays_below_the_demo_bar():
    """Tester 1 Nov 2026: in Guided mode at 1280x800 the app's sticky top bar scrolled under the fixed demo bar, and
    its one row, 340px narrower beside the tour, ran the main button under Ask WIDER and cut the names to "Dedu...".
    demo.css keeps the bar and what sticks under it below the demo bar, gives the bar the app's two planned rows
    while the tour leaves the app at most about 1360px, and hides the long labels the app drops below 1600px."""
    css = (DEMO_DIR / "demo.css").read_text(encoding="utf-8")
    assert re.search(r"^\.demo-on \.top \{ top: var\(--demo-bar-h\); \}", css, re.MULTILINE)
    assert re.search(r"^\.demo-on \.sidebar \{ top: calc\(var\(--demo-bar-h\) \+ var\(--top-h\)\);", css, re.MULTILINE)
    rows = css.split("@media (min-width: 1101px) and (max-width: 1700px) {", 1)[1].split("\n}", 1)[0]
    for rule in (".demo-tour-on .top { flex-wrap: wrap;", ".demo-tour-on body { --top-h: 94px; }",
                 ".demo-tour-on .top .brk { display: block; order: 5; flex-basis: 100%;",
                 ".demo-tour-on .tb-actions { order: 6; flex-shrink: 0; }", ".demo-tour-on #tb-ask { order: 7; }"):
        assert rule in rows, rule
    labels = css.split("@media (min-width: 1101px) and (max-width: 1940px) {", 1)[1].split("\n}", 1)[0]
    assert ".demo-tour-on .plbl { display: none; }" in labels
    assert ".demo-tour-on #tb-home .tb-lbl, .demo-tour-on .switch .swl {" in labels
    # the tour panel is the 340px the ranges add to the app's own 1100px and 1600px steps
    assert ":root { --demo-tour-w: 340px; }" in css


ASK_EXIT = "Leave the demo? This closes the demo tab, or opens the WIDER showcase page when the tab stays open."


def test_exit_confirms_and_keeps_state():
    """Owner, 31 Oct 2026: Exit asks first, then closes the tab the WIDER app opened. Tester 1 Nov 2026: where the
    browser keeps the tab open (the showcase page opens the demo in the same tab), it goes to the showcase page, the
    site root next to /demo/, instead of leaving the visitor on the demo."""
    out = run_demo("""
      globalThis.close = () => { out.closed = (out.closed || 0) + 1; };
      location.assign = (u) => { out.went = (out.went || []).concat([u]); };
      boot();
      Demo.choose("guided");
      confirmAnswer = false;
      out.declined = [Demo.exit(), out.closed || 0];
      await tick(250);
      out.declinedWent = out.went || [];
      confirmAnswer = true;
      out.left = [Demo.exit(), out.closed];
      await tick(250);
      out.asked = out.confirmed;
      out.title = $("demo-exit").getAttribute("title");
      boot();
      out.kept = [Demo.state.mode, Demo.state.checkpoint];
    """)
    assert out["declined"] == [False, 0]
    assert out["declinedWent"] == []
    assert out["left"] == [True, 1]
    assert out["went"] == ["http://demo.test/"]
    assert out["asked"] == [ASK_EXIT] * 2
    assert out["title"] == "Back to WIDER"
    assert out["kept"] == ["guided", "one"]


def test_exit_that_closes_the_tab_goes_nowhere():
    """A tab the WIDER app opened closes: nothing is loaded into it afterwards."""
    out = run_demo("""
      globalThis.close = () => { globalThis.closed = true; };
      location.assign = (u) => { out.went = (out.went || []).concat([u]); };
      boot();
      Demo.choose("guided");
      out.left = Demo.exit();
      await tick(250);
      out.went = out.went || [];
    """)
    assert out == {"left": True, "went": [], "confirmed": [ASK_EXIT]}


def test_exit_served_at_the_root_goes_to_the_root():
    """Audit F14 (29 Oct 2026): served at the site root, Exit's fallback is the root itself, never above it."""
    out = run_demo("""
      location.href = "http://demo.test/"; location.pathname = "/";
      location.assign = (u) => { out.went = (out.went || []).concat([u]); };
      boot();
      Demo.choose("guided");
      out.left = Demo.exit();
      await tick(250);
    """)
    assert out["left"] is True
    assert out["went"] == ["http://demo.test/"]


def test_reset_forgets_the_tab_chosen_inside_a_page():
    """Tester 1 Nov 2026: a page's tab (app.js keeps it as wider.tab.<page>) survived Reset, so the tour's Screening
    steps opened on the wrong tab. Reset clears it; other keys of the visitor's tab store stay."""
    out = run_demo("""
      boot();
      Demo.choose("free");
      sessionStorage.setItem("wider.tab.page-screen", "review");
      sessionStorage.setItem("other.thing", "kept");
      Demo.reset();
      out.left = [sessionStorage.getItem("wider.tab.page-screen"), sessionStorage.getItem("other.thing")];
    """)
    assert out["left"] == [None, "kept"]


# ---- change demo-chapters-and-video: no Guide, a fixed name, Ask WIDER as recorded examples (D5, D5b, D6) -----------

def _ask_snapshot():
    """The invented snapshot plus two recorded Ask WIDER turns at its last checkpoint and three example questions;
    the third has no recorded answer, so it is not offered."""
    import copy

    from tests.unit.demo_node import SNAPSHOT
    snap = copy.deepcopy(SNAPSHOT)
    turns = [
        {"n": 1, "at": "2026-10-31T16:00:00Z", "question": "What do I do first?", "answer": "Import your search results.",
         "based_on": [{"title": "Getting started", "where": "docs/guide.md"}], "backend": "ollama", "model": "qwen3:8b",
         "answered_by": "Reviewer A"},
        {"n": 2, "at": "2026-10-31T16:01:00Z", "question": "How is the recall estimate computed?",
         "answer": "Sum of w·y·d over sum of w·y.", "based_on": [{"title": "Recall", "where": "docs/methods.md"}],
         "backend": "ollama", "model": "qwen3:8b", "answered_by": "Reviewer A"},
    ]
    snap["checkpoints"]["two"]["GET /api/assistant/history"] = "bh"
    snap["bodies"]["bh"] = {"status": 200, "type": "json", "json": {"author": "Reviewer A", "turns": turns}}
    snap["ask_examples"] = [{"question": "What do I do first?", "page": "general"},
                            {"question": "How is the recall estimate computed?", "page": "results"},
                            {"question": "What does the second reader do?", "page": "screen"}]
    return snap


# a stand-in for what shell.js draws in the Ask WIDER tab: the field, the Ask button, the fixed lines, the conversation
ASK_BOX = """
  function drawBox() {
    const old = $("ask-box"); if (old) old.remove();
    const box = document.createElement("section"); box.id = "ask-box";
    const sent = document.createElement("p"); sent.className = "ask-line";
    sent.textContent = "What you type is sent as written: do not paste a record's text."; box.appendChild(sent);
    const conv = document.createElement("div"); conv.className = "ask-turns"; box.appendChild(conv);
    (globalThis.turns || []).forEach((t) => {
      const art = document.createElement("article"); art.className = "ask-turn"; art.dataset = { turn: String(t.n) };
      art.textContent = "You asked: " + t.question + " " + t.answer + " " + t.based_on.map((b) => b.title).join(", ");
      conv.appendChild(art);
    });
    const about = document.createElement("select"); about.id = "ask-about"; about.value = "general";
    about.addEventListener("change", () => { out.about = about.value; });
    const ta = document.createElement("textarea"); ta.id = "ask-q"; ta.value = "";
    const go = document.createElement("button"); go.id = "ask-go";
    go.addEventListener("click", async () => {
      const r = await getJson("/api/assistant/ask", { method: "POST", body: JSON.stringify({ question: ta.value, about: about.value }) });
      out.asks = (out.asks || []).concat([r]);
      const job = await getJson("/api/jobs/" + r.json.job_id);
      out.job = job.json.status;
      globalThis.turns = (await getJson("/api/assistant/history")).json.turns;
      drawBox();
    });
    [about, ta, go].forEach((e) => box.appendChild(e));
    document.body.appendChild(box);
    DemoAsk.decorate();
  }
"""


def test_no_guide():
    """D5: the Guide button, tab, menu item and an empty More menu are hidden by demo.css; the stored panel tab reads
    Ask WIDER whatever the app writes, and the panel starts closed in a fresh tab."""
    css = (DEMO_DIR / "demo.css").read_text(encoding="utf-8")
    hidden = re.search(r"([^{}]*)\{\s*display:\s*none\s*!important;\s*\}", css[css.index("#tb-guide"):]).group(1)
    for sel in ("#tb-guide", "#ptab-guide", "#ppane-guide", '#tb-more-list [data-more="guide"]'):
        assert sel in hidden, sel
    assert re.search(r'\.tb-more-wrap:not\(:has\(\[data-more\]:not\(\[data-more="guide"\]\)\)\)\s*\{\s*display:\s*none',
                     css)
    out = run_demo("""
      boot();
      out.fresh = [localStorage.getItem("wider.guide"), localStorage.getItem("wider.panelTab")];
      localStorage.setItem("wider.panelTab", "guide");
      localStorage.setItem("wider.guide", "open");
      out.after = [localStorage.getItem("wider.guide"), localStorage.getItem("wider.panelTab")];
      boot();
      out.reload = [localStorage.getItem("wider.guide"), localStorage.getItem("wider.panelTab")];
    """, actions=True)
    assert out["fresh"] == ["closed", "ask"]
    assert out["after"] == ["open", "ask"] and out["reload"] == ["open", "ask"]


def test_fixed_name():
    """D6: the name reads Reviewer A whatever is stored, the app's name functions open nothing, a click on a Change
    control never reaches the app's own handler, and the Change link and Home name card are hidden by demo.css."""
    css = (DEMO_DIR / "demo.css").read_text(encoding="utf-8")
    assert re.search(r"\[data-set-name\],\s*#home-name\s*\{\s*display:\s*none\s*!important", css)
    out = run_demo("""
      globalThis.beforeBoot = () => {
        // common.js's name helpers as the app defines them (ask.js pins them once the page has loaded)
        globalThis.WiderName = { ask: async () => { out.dialog = true; return "Typed"; }, askOnce: () => { out.dialog = true; } };
      };
      boot();
      localStorage.setItem("wider.reviewer", "Someone else");
      out.name = localStorage.getItem("wider.reviewer");
      out.asked = await WiderName.ask();
      WiderName.askOnce();
      const change = document.createElement("button"); change.setAttribute("data-set-name", "");
      const inner = change.appendChild(document.createElement("span"));
      document.body.appendChild(change);
      document.addEventListener("click", () => { out.reached = true; });   // the app's own handler, registered later
      const e = inner.click();
      out.prevented = e.defaultPrevented;
      Demo.reset && Demo.reset();
      out.afterReset = localStorage.getItem("wider.reviewer");
    """, actions=True)
    assert out["name"] == "Reviewer A" and out["afterReset"] == "Reviewer A"
    assert out["asked"] == "Reviewer A"
    assert "dialog" not in out and "reached" not in out and out["prevented"] is True


def test_ask_examples_show_recorded_answers():
    """D5b: the recorded examples become buttons above the conversation; pressing one asks it the app's way and shows
    the recorded turn with its sources under "Recorded answer, <date>, <model>"; an example without a recorded answer
    is not offered; a typed question is refused; nothing leaves the page."""
    out = run_demo(ASK_BOX + """
      boot();
      Demo.setCheckpoint("two");
      drawBox();
      const box = () => $("ask-box");
      out.lead = box().querySelector(".demo-ask-lead").textContent;
      out.buttons = box().querySelectorAll(".demo-ask-ex").map((b) => [b.id, b.textContent]);
      out.sentHidden = box().querySelector("p.ask-line").classList.contains("demo-hide");
      $("demo-ask-2").click();
      await tick(); await tick(); await tick();
      const arts = box().querySelectorAll(".ask-turn");
      out.turns = arts.map((a) => a.textContent);
      out.recorded = arts.map((a) => a.querySelector(".demo-ask-recorded").textContent);
      $("demo-ask-2").click();   // asked already: no second turn
      await tick(); await tick();
      out.count = box().querySelectorAll(".ask-turn").length;
      out.typed = await getJson("/api/assistant/ask", { method: "POST", body: JSON.stringify({ question: "Anything else?" }) });
      out.fetch = spy.fetch; out.beacon = spy.beacon;
    """, snapshot=_ask_snapshot(), actions=True)
    assert out["lead"].startswith("Example questions. These answers were recorded beforehand")
    assert out["buttons"] == [["demo-ask-1", "What do I do first?"], ["demo-ask-2", "How is the recall estimate computed?"]]
    assert out["sentHidden"] is True
    assert out["about"] == "results" and out["job"] == "done"
    assert out["asks"][0]["status"] in (200, 202) and out["asks"][0]["json"]["job_id"] == "demo-ask-2"
    assert len(out["turns"]) == 1 and "Sum of w·y·d over sum of w·y." in out["turns"][0] and "Recall" in out["turns"][0]
    assert out["recorded"] == ["Recorded answer, 31 October 2026, qwen3:8b"]
    assert out["count"] == 1
    assert out["typed"]["status"] == 409
    assert "only the example questions" in json_text(out["typed"]["json"])
    assert out["fetch"] == 0 and out["beacon"] == 0


def test_ask_address_opens_examples():
    """D5b: an old #ask address opens the panel on Ask WIDER (shell.js writes the panel keys; the demo keeps the tab on
    Ask WIDER), and the tab shows the example questions."""
    out = run_demo(ASK_BOX + """
      location.hash = "#ask";
      boot();
      Demo.setCheckpoint("two");
      // shell.js's askRoute for an #ask address
      localStorage.setItem("wider.guide", "open"); localStorage.setItem("wider.panelTab", "ask");
      out.keys = [localStorage.getItem("wider.guide"), localStorage.getItem("wider.panelTab")];
      drawBox();
      out.buttons = $("ask-box").querySelectorAll(".demo-ask-ex").length;
      out.first = $("ask-box").children[0].className;
    """, snapshot=_ask_snapshot(), actions=True)
    assert out["keys"] == ["open", "ask"]
    assert out["buttons"] == 2 and out["first"] == "demo-ask-examples"


def json_text(v):
    import json
    return json.dumps(v)
