"""The question window and its ask-mode hand-off under Node, on the small DOM of ``a11y_dom.py``
(review-plan-and-question-window D7-D12): the thread, the suggested answer, the value box, refusals in rose, Later,
Change through the journal undo, minimize / close / the pill, the keys, the seed row mark, and questions.js asking a
question about this page in the window and one about another page in the dialog."""
from __future__ import annotations

import json
import re

from tests.unit.a11y_dom import run_dom, static

SEED_Q = {"question_id": 3, "kind": "seed_unresolved", "stage": "setup", "page": "setup?task=seeds", "anchor": "S2",
          "record_id": None, "title": "Seed paper S2 was not found", "prompt": "No DOI or title match for S2.",
          "created_at": "", "options": [
              {"value": "doi", "label": "Correct the DOI", "input": "doi", "default": True,
               "consequence": "The DOI is saved in benchmark.csv; fetch the seed abstracts again to use it."},
              {"value": "leave", "label": "Leave it", "consequence": "Seed coverage is counted without this paper."}]}
SEED_Q2 = {**SEED_Q, "question_id": 4, "anchor": "S5", "title": "Seed paper S5 was not found",
           "prompt": "No DOI or title match for S5."}
SCREEN_Q = {"question_id": 5, "kind": "screen_failed", "stage": "screen", "page": "screen", "anchor": 12, "record_id": 12,
            "title": "Record 12 could not be screened", "prompt": "The model gave no answer.", "created_at": "",
            "options": [{"value": "include", "label": "Include", "consequence": "The record counts as included."},
                        {"value": "exclude", "label": "Exclude", "consequence": "The record counts as excluded."}]}
REFINE_Q = {"question_id": 7, "kind": "refine_noise_term", "stage": "query", "page": "query", "anchor": None,
            "record_id": None, "title": "Is 'burnout' noise?", "prompt": "It brings 40 records, none relevant.",
            "created_at": "", "options": [
                {"value": "keep", "label": "Keep it", "default": True, "consequence": "The term stays in the query."},
                {"value": "drop", "label": "Drop it", "consequence": "The term leaves the query."}]}

HTML = ('<button id="elsewhere">Elsewhere</button><table><tr data-seed-row="S2"><td>S2</td></tr>'
        '<tr data-seed-row="S5"><td>S5</td></tr></table>'
        '<div id="qwindow" class="qwindow" role="region" aria-label="Questions from WIDER" hidden></div>')
WAIT = "await new Promise(r => setTimeout(r, 760)); await flush();"   # the 700 ms move to the next question


def win(body: str, questions: list, *, page: str = "setup", task: str = "seeds", mode: str = "ask",
        stored: str | None = None, pre: str = "") -> dict:
    """qwindow.js on a page with ``questions`` open on it; ``refresh(true)`` has run."""
    setup = f"""
      var currentPage = {json.dumps(page)};
      window.currentSetupTask = () => {json.dumps(task)};
      replies["/api/questions"] = {{ questions: {json.dumps(questions)}, mode: {json.dumps(mode)} }};
      {f'localStorage.setItem("wider.qwindow", {json.dumps(stored)});' if stored else ""}
      window.Drawer = {{ author: () => "ana" }};   // the name WIDER works as (D33); a test may replace it
      {pre}"""
    return run_dom(f"""
      $("#elsewhere").focus();
      await QWindow.refresh(true); await flush();
      const W = () => $("#qwindow");
      const thread = () => W().querySelector(".qw-thread").innerHTML;
      const replyLabels = () => W().querySelectorAll(".qw-reply").map(b => b.textContent);
      {body}""", html=HTML, scripts=("common.js", "qwindow.js"), pre=setup)


def test_qwindow_answers_seed_question():
    """Scenario "A seed question is answered in the window": the window asks for this page (the page value encoded),
    shows title, prompt and each consequence; "Correct the DOI" opens the value box with "Save DOI"; the answer is
    posted as the route takes it; the reply shows "You · hh:mm" and the engine's applied text; after 700 ms the closing
    line follows. The focus stays where it was."""
    r = win(f"""
      replies["/api/questions/3/answer"] = {{ question_id: 3, applied: "DOI 10.1/x saved for S2", change_id: 41 }};
      const first = {{ thread: thread(), hidden: W().hidden, focus: focused(), labels: replyLabels() }};
      W().querySelector('[data-qw-choice="doi"]').click(); await flush();
      const box = W().querySelector(".qw-value");
      const send = W().querySelector("[data-qw-send]");
      const valueShown = {{ hidden: box.hidden, send: send.textContent, label: W().querySelector("[data-qw-value]").getAttribute("aria-label") }};
      W().querySelector("[data-qw-value]").value = "10.1/x";
      send.click(); await flush(); await flush();
      const after = thread();
      {WAIT}
      out({{ first, valueShown, after, closing: thread(), sent: posts(), urls: calls.map(c => c[0]) }});
    """, [SEED_Q])
    f = r["first"]
    assert f["hidden"] is False and f["focus"] == "#elsewhere"
    assert "Seed paper S2 was not found" in f["thread"] and "No DOI or title match for S2." in f["thread"]
    assert "<b>Leave it</b>: Seed coverage is counted without this paper." in f["thread"]
    assert f["labels"] == ["Correct the DOI", "Leave it", "Later"]
    assert r["valueShown"] == {"hidden": False, "send": "Save DOI", "label": "DOI (10.xxxx/...)"}
    assert r["sent"] == [["/api/questions/3/answer", "POST", {"choice": "doi", "text": "10.1/x", "by": "ana"}]]
    assert "/api/questions?status=open&page=setup%3Ftask%3Dseeds" in r["urls"]
    assert "Correct the DOI: 10.1/x" in r["after"] and re.search(r"You · \d\d:\d\d", r["after"])
    assert "DOI 10.1/x saved for S2" in r["after"]
    assert "That is everything on this page. Your answers are saved in History, with your name." in r["closing"]



def test_qwindow_without_a_name_sends_nothing():
    """risk-checker R1 (D33): an answer is signed; with no name WIDER works as, nothing is posted and the window says
    to enter the name first, pointing at Change in the top bar."""
    r = win("""
      W().querySelector('[data-qw-choice="doi"]').click(); await flush();
      W().querySelector("[data-qw-value]").value = "10.1/x";
      W().querySelector("[data-qw-send]").click(); await flush(); await flush();
      const el = W().querySelector(".qw-refusal");
      out({ text: el && el.textContent, sent: posts() });
    """, [SEED_Q], pre='window.Drawer = { author: () => "" };')
    assert r["sent"] == []
    assert r["text"].startswith("Answers are signed with your name: enter your name first")

def test_qwindow_refusal_in_rose():
    """A value the engine refuses shows its message in rose, bold, 13 px (role="alert"); the answers come back. The
    class is black-or-rose in style.css, never grey (D7, 4.7)."""
    r = win("""
      replies["/api/questions/3/answer"] = () => Object.assign(new Error("not a DOI: abc"), { status: 400 });
      W().querySelector('[data-qw-choice="doi"]').click(); await flush();
      W().querySelector("[data-qw-value]").value = "abc";
      W().querySelector("[data-qw-send]").click(); await flush(); await flush();
      const el = W().querySelector(".qw-refusal");
      out({ text: el && el.textContent, role: el && el.getAttribute("role"), labels: replyLabels() });
    """, [SEED_Q])
    assert r == {"text": "not a DOI: abc", "role": "alert", "labels": ["Correct the DOI", "Leave it", "Later"]}
    css = static("style.css")
    rule = re.search(r"\.qw-refusal \{([^}]*)\}", css).group(1)
    assert "color:var(--rose)" in rule and "font-weight:700" in rule and "font-size:13px" in rule
    for cls in ("qw-refusal", "qw-cons", "qw-meta", "qw-closing", "qw-pill"):
        for m in re.finditer(rf"\.{cls}\b[^{{]*\{{([^}}]*)\}}", css):
            assert "--muted" not in m.group(1), cls


def test_qwindow_keeps_the_focus():
    """Keyboard (ui-tester F2): a refused value leaves the value box open with the typed text, the refusal under it and
    the focus in it; after an answer the focus goes to the next question's first answer; the previous arrow keeps the
    focus in the window. The focus is never taken while it is outside the window."""
    r = win(f"""
      replies["/api/questions/3/answer"] = () => Object.assign(new Error("not a DOI: abc"), {{ status: 400 }});
      W().querySelector('[data-qw-choice="doi"]').click(); await flush();
      W().querySelector("[data-qw-value]").value = "abc";
      W().querySelector("[data-qw-send]").click(); await flush(); await flush();
      const box = W().querySelector(".qw-value");
      const refused = {{ hidden: box.hidden, value: W().querySelector("[data-qw-value]").value,
        focus: document.activeElement === W().querySelector("[data-qw-value]"),
        under: thread().indexOf("qw-value") < thread().indexOf("qw-refusal") }};
      replies["/api/questions/3/answer"] = {{ question_id: 3, applied: "saved", change_id: 41 }};
      W().querySelector("[data-qw-value]").value = "10.1/x";
      W().querySelector("[data-qw-send]").click(); await flush(); await flush();
      {WAIT}
      const next = {{ pos: W().querySelector(".qw-pos").textContent, focus: document.activeElement.textContent }};
      W().querySelector("[data-qw-prev]").click(); await flush();
      const back = {{ pos: W().querySelector(".qw-pos").textContent, inside: W().contains(document.activeElement) }};
      $("#elsewhere").focus(); await QWindow.refresh(); await flush();
      out({{ refused, next, back, outside: focused() }});
    """, [SEED_Q, SEED_Q2])
    assert r["refused"] == {"hidden": False, "value": "abc", "focus": True, "under": True}
    assert r["next"] == {"pos": "2 of 2", "focus": "Correct the DOI"}
    assert r["back"] == {"pos": "1 of 2", "inside": True}
    assert r["outside"] == "#elsewhere"


def test_qwindow_leaves_with_the_page():
    """ui-tester F1: on another page the window of the last one goes at once, before the new list loads, and the
    window asks for the new page."""
    r = win("""
      const before = W().hidden;
      replies["/api/questions"] = () => new Promise(() => {});   // the new list never comes
      currentPage = "construct";
      QWindow.refresh(); await flush();
      out({ before, after: W().hidden, url: calls[calls.length - 1][0] });
    """, [SEED_Q])
    assert r == {"before": False, "after": True, "url": "/api/questions?status=open&page=construct"}


def test_qwindow_only_default_bordered():
    """Scenario "The suggested answer": only the option marked default gets the violet border; Later is last; a
    screening question suggests nothing (D8)."""
    r = win("""
      const seed = W().querySelectorAll(".qw-reply").map(b => [b.textContent, b.classList.contains("qw-default")]);
      out({ seed });
    """, [SEED_Q])
    assert r["seed"] == [["Correct the DOI", True], ["Leave it", False], ["Later", False]]
    s = win('out({ n: W().querySelectorAll(".qw-default").length, labels: replyLabels() });', [SCREEN_Q], page="screen")
    assert s == {"n": 0, "labels": ["Include", "Exclude", "Later"]}
    css = static("style.css")
    assert re.search(r"button\.qw-reply\.qw-default \{[^}]*border:2px solid var\(--violet\)[^}]*font-weight:600", css)


def test_qwindow_later_on_the_only_question():
    """Scenario "Later on the only question": the window says "Kept for later. Nothing has changed yet." and not the
    closing line, writes nothing, and the question is still offered after a redraw."""
    r = win(f"""
      W().querySelector("[data-qw-later]").click(); await flush();
      {WAIT}
      const after = thread();
      await QWindow.refresh(true); await flush();
      out({{ after, redrawn: replyLabels(), posts: posts().length }});
    """, [SEED_Q])
    assert "Kept for later. Nothing has changed yet." in r["after"] and "qw-closing" not in r["after"]
    assert r["redrawn"] == ["Correct the DOI", "Leave it", "Later"] and r["posts"] == 0


def test_qwindow_later_writes_nothing():
    """Scenario "Later keeps a question": nothing is posted, "Kept for later. Nothing has changed yet." is said, the
    window moves to the next question; back on the kept one there is no Later; a redraw of the page brings it back."""
    r = win(f"""
      W().querySelector("[data-qw-later]").click(); await flush();
      const kept = thread();
      {WAIT}
      const moved = {{ pos: W().querySelector(".qw-pos").textContent, thread: thread() }};
      W().querySelector("[data-qw-prev]").click(); await flush();
      const back = replyLabels();
      await QWindow.refresh(true); await flush();
      out({{ kept, moved, back, redrawn: replyLabels(), posts: posts().length }});
    """, [SEED_Q, SEED_Q2])
    assert "Kept for later. Nothing has changed yet." in r["kept"]
    assert r["moved"]["pos"] == "2 of 2" and "Seed paper S5" in r["moved"]["thread"]
    assert r["back"] == ["Correct the DOI", "Leave it"]
    assert r["redrawn"] == ["Correct the DOI", "Leave it", "Later"] and r["posts"] == 0


def test_qwindow_minimize_close_pill():
    """Scenario "Minimize, close and the pill": minimized the header reads "WIDER · n questions open"; closed, the
    rose pill "! Waiting for you · n" reopens it; the state is kept in wider.qwindow; queue mode starts minimized; a new
    question for this page turns a closed window into a minimized one (D11)."""
    r = win("""
      W().querySelector("[data-qw-mini]").click(); await flush();
      const mini = { text: W().querySelector("[data-qw-open]").textContent.replace(/\\s+/g, " ").trim(), ls: localStorage.getItem("wider.qwindow"),
                     thread: !!W().querySelector(".qw-thread") };
      W().querySelector("[data-qw-close]").click(); await flush();
      const closed = { pill: W().querySelector(".qw-pill").textContent, ls: localStorage.getItem("wider.qwindow"), focus: focused() };
      W().querySelector(".qw-pill").click(); await flush();
      out({ mini, closed, reopened: { ls: localStorage.getItem("wider.qwindow"), thread: !!W().querySelector(".qw-thread") } });
    """, [SEED_Q, SEED_Q2])
    assert r["mini"] == {"text": "WIDER · 2 questions open", "ls": "mini", "thread": False}
    assert r["closed"]["pill"] == "! Waiting for you · 2" and r["closed"]["ls"] == "closed"
    assert "qw-pill" in r["closed"]["focus"]
    assert r["reopened"] == {"ls": "open", "thread": True}
    q = win('out({ cls: W().className, thread: !!W().querySelector(".qw-thread") });', [SEED_Q], mode="queue")
    assert q == {"cls": "qwindow qw-mini", "thread": False}
    n = win(f"""
      replies["/api/questions"] = {{ questions: {json.dumps([SEED_Q, SEED_Q2])}, mode: "queue" }};
      await QWindow.refresh(); await flush();
      out({{ ls: localStorage.getItem("wider.qwindow"), cls: W().className }});
    """, [SEED_Q], mode="queue", stored="closed")
    assert n == {"ls": "mini", "cls": "qwindow qw-mini"}


def test_qwindow_keyboard():
    """Esc in the window minimizes it and gives the focus to the header button; after ‹ › the focus is on the first
    quick reply; the four header buttons have names; the window is a named region with a polite log (D7)."""
    r = win("""
      const names = W().querySelectorAll(".qw-head button[aria-label]").map(b => b.getAttribute("aria-label"));
      const region = [W().getAttribute("role"), W().getAttribute("aria-label")];
      const log = [W().querySelector(".qw-thread").getAttribute("role"), W().querySelector(".qw-thread").getAttribute("aria-live")];
      W().querySelector("[data-qw-next]").click(); await flush();
      const afterNext = document.activeElement.textContent;
      press("Escape"); await flush();
      out({ names, region, log, afterNext, cls: W().className, focus: document.activeElement.getAttribute("data-qw-open") });
    """, [SEED_Q, SEED_Q2])
    assert r["names"] == ["Previous question", "Next question", "Minimize", "Close"]
    assert r["region"] == ["region", "Questions from WIDER"] and r["log"] == ["log", "polite"]
    assert r["afterNext"] == "Correct the DOI"
    assert r["cls"] == "qwindow qw-mini" and r["focus"] == ""


def test_qwindow_change_undoes():
    """Scenario "Change an answer": Change calls the journal undo of the answer's change; the question is asked again
    with its answers and the focus on the first one (D10)."""
    r = win("""
      replies["/api/questions/3/answer"] = { question_id: 3, applied: "S2 left without a DOI", change_id: 41 };
      replies["/api/history/41/undo"] = { change_id: 42, summary: "undo: answer (change 41)" };
      W().querySelector('[data-qw-choice="leave"]').click(); await flush(); await flush();
      const answered = thread();
      W().querySelector("[data-qw-change]").click(); await flush(); await flush();
      out({ answered, sent: posts().map(c => c[0]), labels: replyLabels(), focus: document.activeElement.textContent });
    """, [SEED_Q])
    assert "S2 left without a DOI" in r["answered"] and "data-qw-change" in r["answered"]
    assert r["sent"] == ["/api/questions/3/answer", "/api/history/41/undo"]
    assert r["labels"] == ["Correct the DOI", "Leave it", "Later"] and r["focus"] == "Correct the DOI"


def test_qwindow_answer_and_change_signed_alike():
    """Rule 3 (risk-check R1): the answer is posted with the page's name and its Change is signed with the same name in
    the X-Wider-Author header, so the researcher's own Change is never refused as another person's change."""
    r = win("""
      const heads = [];
      const realFetch = global.fetch;
      global.fetch = (u, o) => { heads.push([u, (o && o.headers) || {}]); return realFetch(u, o); };
      replies["/api/questions/3/answer"] = { question_id: 3, applied: "S2 left without a DOI", change_id: 41 };
      replies["/api/history/41/undo"] = { change_id: 42, summary: "undo: answer (change 41)" };
      W().querySelector('[data-qw-choice="leave"]').click(); await flush(); await flush();
      W().querySelector("[data-qw-change]").click(); await flush(); await flush();
      out({ by: posts()[0][2].by, undo: heads.find(h => h[0] === "/api/history/41/undo")[1] });
    """, [SEED_Q], pre='window.Drawer = { author: () => "Ann Ö" };')
    assert r["by"] == "Ann Ö" and r["undo"]["X-Wider-Author"] == "Ann%20%C3%96"


def test_qwindow_no_change_on_refinement():
    """A refinement answer gets no Change: its effects run outside the journalled change (D10)."""
    r = win("""
      replies["/api/questions/7/answer"] = { question_id: 7, applied: "kept 'burnout'", change_id: 50 };
      W().querySelector('[data-qw-choice="keep"]').click(); await flush(); await flush();
      out({ thread: thread() });
    """, [REFINE_Q], page="query")
    assert "kept &#39;burnout&#39;" in r["thread"] or "kept 'burnout'" in r["thread"]
    assert "data-qw-change" not in r["thread"]


def test_qwindow_refused_undo_keeps_answer():
    """An undo the engine refuses (another person's change) shows its message in rose with a pointer to History; the
    answer stays (D10)."""
    r = win("""
      replies["/api/questions/3/answer"] = { question_id: 3, applied: "S2 left without a DOI", change_id: 41 };
      const orig = global.fetch;
      global.fetch = async (url, opts) => url === "/api/history/41/undo"
        ? { ok: false, status: 409, json: async () => ({ detail: "change 41 was made by Ann", author: "Ann" }) } : orig(url, opts);
      W().querySelector('[data-qw-choice="leave"]').click(); await flush(); await flush();
      W().querySelector("[data-qw-change]").click(); await flush(); await flush();
      const el = W().querySelector(".qw-refusal");
      out({ refusal: el && el.textContent, thread: thread() });
    """, [SEED_Q])
    assert r["refusal"] == "change 41 was made by Ann Change it on the History page."
    assert "S2 left without a DOI" in r["thread"] and "data-qw-change" in r["thread"]


def test_qwindow_row_mark():
    """Scenario "The seed row is marked": the row of the question shown gets the rose bar and points to the question's
    bubble; moving on moves the mark; minimized, no row is marked (D7)."""
    r = win("""
      const mark = () => $$("tr").map(t => [t.getAttribute("data-seed-row"), t.classList.contains("qw-row-mark"), t.getAttribute("aria-describedby")]);
      const first = mark();
      const bubble = !!$("#qw-q-3");
      W().querySelector("[data-qw-next]").click(); await flush();
      const second = mark();
      W().querySelector("[data-qw-mini]").click(); await flush();
      out({ first, bubble, second, mini: mark() });
    """, [SEED_Q, SEED_Q2])
    assert r["first"] == [["S2", True, "qw-q-3"], ["S5", False, None]] and r["bubble"]
    assert r["second"] == [["S2", False, None], ["S5", True, "qw-q-4"]]
    assert r["mini"] == [["S2", False, None], ["S5", False, None]]
    assert re.search(r"tr\.qw-row-mark[^{]*\{[^}]*var\(--rose\)", static("style.css"))


def test_qwindow_old_question_consequences():
    """A question stored before the consequences existed shows the ones the list fills in (D5); an option without
    one is left out of the list, never shown empty."""
    old = {**SEED_Q, "options": [{"value": "doi", "label": "Correct the DOI", "input": "doi", "default": True,
                                  "consequence": "The DOI is saved in benchmark.csv; fetch the seed abstracts again to use it."},
                                 {"value": "skip", "label": "Skip"}]}
    r = win("out({ thread: thread() });", [old])
    assert "<b>Correct the DOI</b>: The DOI is saved in benchmark.csv" in r["thread"]
    assert "<b>Skip</b>" not in r["thread"]


def test_qwindow_on_review_plan():
    """Scenario "The Review plan shows every setup question": on #setup the window asks for page "setup", which the
    server matches to every setup task's questions; a page without questions shows no window."""
    r = win('out({ urls: calls.map(c => c[0]), hidden: W().hidden });', [SEED_Q], task="")
    assert "/api/questions?status=open&page=setup" in r["urls"] and r["hidden"] is False
    e = win('out({ hidden: W().hidden, html: W().innerHTML });', [], page="dedup")
    assert e == {"hidden": True, "html": ""}


# ---------------------------------------------------------------- questions.js ask mode (D12)

ASK = ('<button id="elsewhere">Elsewhere</button><dialog id="ask-dialog" aria-labelledby="ask-title"><p id="ask-count"></p>'
       '<div id="ask-body"></div><div class="row"><button class="secondary" id="ask-later">Later</button></div></dialog>'
       '<div id="qwindow" class="qwindow" hidden></div>')


def ask(body: str, questions: list, *, mode: str = "ask", page: str = "setup", task: str = "seeds",
        stored: str | None = None) -> dict:
    latest = max([x["question_id"] for x in questions] or [0])
    pre = f"""
      global.fmtWhen = (t) => String(t || "");
      var currentPage = {json.dumps(page)};
      window.currentSetupTask = () => {json.dumps(task)};
      {f'localStorage.setItem("wider.qwindow", {json.dumps(stored)});' if stored else ""}
      replies["/api/questions/summary"] = {{ mode: {json.dumps(mode)}, open: 0, latest_open: 0 }};
      replies["/api/questions"] = {{ questions: [], mode: {json.dumps(mode)} }};
      $("#elsewhere").focus();"""
    # the questions arrive after the page loaded: the ones open at load are never asked (questions.js)
    return run_dom(f"""
      for (let i = 0; i < 4; i++) await flush();
      replies["/api/questions/summary"] = {{ mode: {json.dumps(mode)}, open: {len(questions)}, latest_open: {latest} }};
      replies["/api/questions"] = {{ questions: {json.dumps(questions)}, mode: {json.dumps(mode)} }};
      await Questions.refresh(); for (let i = 0; i < 6; i++) await flush();
      const dlg = $("#ask-dialog");
      {body}""", html=ASK, scripts=("common.js", "qwindow.js", "questions.js"), pre=pre)


def test_question_for_this_page_opens_window():
    """Ask mode: a new question about the page in view opens the window, expanded, without taking the focus, and not
    the dialog; one about another page opens the dialog as before."""
    body = 'out({ dialog: dlg.open, cls: $("#qwindow").className, focus: focused() });'
    assert ask(body, [SEED_Q]) == {"dialog": False, "cls": "qwindow qw-open", "focus": "#elsewhere"}
    assert ask(body, [SEED_Q], stored="closed") == {"dialog": False, "cls": "qwindow qw-open", "focus": "#elsewhere"}
    assert ask(body, [SEED_Q], stored="mini") == {"dialog": False, "cls": "qwindow qw-open", "focus": "#elsewhere"}
    o = ask('out({ dialog: dlg.open, body: $("#ask-body").innerHTML });', [SEED_Q, SCREEN_Q])
    assert o["dialog"] is True and "Record 12 could not be screened" in o["body"] and "Seed paper S2" not in o["body"]


def test_window_question_not_asked_again():
    """A question shown in the window is not asked in the dialog later, also after the researcher moves to another
    page."""
    r = ask("""
      currentPage = "dedup";
      await QWindow.refresh(true); await Questions.refresh(); for (let i = 0; i < 4; i++) await flush();
      out({ dialog: dlg.open, gets: calls.filter(c => c[0] === "/api/questions").length });
    """, [SEED_Q])
    assert r["dialog"] is False


def test_queue_mode_opens_neither():
    """Queue mode: the window is drawn minimized and the dialog does not open."""
    r = ask('out({ dialog: dlg.open, cls: $("#qwindow").className, ls: localStorage.getItem("wider.qwindow") });',
            [SEED_Q, SCREEN_Q], mode="queue")
    assert r == {"dialog": False, "cls": "qwindow qw-mini", "ls": None}
