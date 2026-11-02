"""Node tests of accessibility-and-naming (C6): in-page dialogs, focus, names and keys, on the small DOM of
``a11y_dom.py`` (no browser, no package)."""
import json
import re

import pytest

from tests.unit.a11y_dom import run_dom, section, static

TOASTS = '<div id="toasts"></div><div id="job-bar" hidden></div>'
LIVE = '<p id="toast-live" role="status"></p><p id="toast-alert" role="alert"></p>'


def test_cancel_run_asks_in_page():
    """The job bar's Cancel opens the in-page dialog: Cancel has the focus, Esc sends nothing and returns the focus,
    "Cancel the run" sends the cancel."""
    r = run_dom("""
      replies["/api/jobs"] = [{ job_id: "j1", label: "Screening", n_done: 1, n_total: 4 }];
      replies["/api/jobs/j1/cancel"] = { ok: true };
      await Jobs.poll(); await flush();
      const b = $("[data-cancel]"); b.focus(); b.click(); await flush();
      const dlg = $("dialog.choice-dialog");
      const first = { open: !!(dlg && dlg.open), text: dlg.querySelector("#choice-title").textContent,
                      buttons: dlg.querySelectorAll("button").map(x => x.textContent), focus: document.activeElement.dataset.act,
                      labelled: dlg.getAttribute("aria-labelledby") };
      dlg.dispatchEvent(new Event("cancel")); await flush(); await flush();
      const afterEsc = { posts: posts().length, gone: !$("dialog.choice-dialog"), back: document.activeElement === b };
      b.click(); await flush();
      $("dialog.choice-dialog [data-act=ok]").click(); await flush(); await flush();
      out({ first, afterEsc, sent: posts().map(c => c[0]) });
    """, html=TOASTS, scripts=("common.js", "jobs.js"))
    assert r["first"] == {"open": True, "text": "Cancel this run? Finished items are kept; starting the run again resumes it.",
                          "buttons": ["Cancel the run", "Cancel"], "focus": "cancel", "labelled": "choice-title"}
    assert r["afterEsc"] == {"posts": 0, "gone": True, "back": True}
    assert r["sent"] == ["/api/jobs/j1/cancel"]


def test_failed_allow_stays_in_the_dialog():
    """A failed Allow shows its error as role="alert" in the consent dialog and keeps it open; the request is not
    retried; Cancel then closes it and the call fails with "Nothing was sent"."""
    r = run_dom("""
      let n = 0;
      replies["/api/lookups/allow"] = () => Object.assign(new Error("the consent was not stored: project.yaml could not be written (Permission denied)"), { status: 500 });
      replies["/api/lookups"] = { allowed: false };
      const orig = global.fetch;   // the lookup route refuses with the consent 403 every time
      global.fetch = async (url, opts) => {
        if (url !== "/api/lookup-thing") return orig(url, opts);
        n++;
        return { ok: false, status: 403, json: async () => ({ detail: { message: "consent", consent_required: { purpose: "doi", services: ["Crossref"], fields: ["DOI"] } } }) };
      };
      let err = null;
      const p = api("/api/lookup-thing").catch(e => { err = e.message; });
      await flush();
      $("dialog.consent-dialog [data-act=allow]").click(); await flush(); await flush(); await flush();
      const dlg = $("dialog.consent-dialog");
      const alertEl = dlg && dlg.querySelector("[role=alert]");
      const mid = { open: !!(dlg && dlg.open), alert: alertEl && alertEl.textContent, tries: n };
      dlg.querySelector("[data-act=cancel]").click(); await p;
      out({ mid, err, gone: !$("dialog.consent-dialog"), tries: n });
    """, scripts=("common.js",))
    assert r["mid"] == {"open": True, "alert": "the consent was not stored: project.yaml could not be written (Permission denied)", "tries": 1}
    assert r["err"] == "Nothing was sent: online lookups are not allowed for this project."
    assert r["gone"] and r["tries"] == 1


@pytest.mark.parametrize("stored, expected", [
    ("true", "The consent was stored, but the server failed (HTTP 500). Allow and continue goes on; to withdraw it, "
             "revoke online lookups in Settings."),
    ("false", "The consent was not stored: the server failed (HTTP 500)."),
    (None, "Whether the consent was stored is unknown: the server failed (HTTP 500). Open Settings to see it before "
           "you continue."),
])
def test_failed_allow_says_what_is_stored(stored, expected):
    """risk-checker R1: a 500 with a plain-text body (the History write failed after project.yaml was saved) does not
    claim "not stored"; the dialog reads /api/lookups back and says whether the consent is stored, or that it cannot
    tell. The dialog stays open in each case."""
    lookups = f'replies["/api/lookups"] = {{ allowed: {stored} }};' if stored else ""
    r = run_dom(lookups + """
      const orig = global.fetch;
      global.fetch = async (url, opts) => {
        if (url === "/api/lookups/allow") return { ok: false, status: 500, json: async () => { throw new SyntaxError("not JSON"); } };
        if (url !== "/api/lookup-thing") return orig(url, opts);
        return { ok: false, status: 403, json: async () => ({ detail: { message: "consent", consent_required: { purpose: "doi", services: ["Crossref"], fields: ["DOI"] } } }) };
      };
      api("/api/lookup-thing").catch(() => {});
      await flush();
      $("dialog.consent-dialog [data-act=allow]").click(); for (let i = 0; i < 6; i++) await flush();
      const dlg = $("dialog.consent-dialog");
      out({ open: !!(dlg && dlg.open), alert: dlg && dlg.querySelector("[role=alert]").textContent });
    """, scripts=("common.js",))
    assert r == {"open": True, "alert": expected}


ASK = ('<button id="elsewhere">Elsewhere</button><dialog id="ask-dialog" aria-labelledby="ask-title"><p id="ask-count"></p>'
       '<div id="ask-body"></div><div class="row"><button class="secondary" id="ask-later">Later</button></div></dialog>')


def test_question_dialog_focus_and_escape():
    """A question arriving in ask mode opens the dialog with the focus on its first answer (not Later); Esc takes the
    Later path (the question stays open in the queue, nothing is answered) and the focus returns to the earlier control."""
    pre = """
      global.fmtWhen = (t) => String(t || "");
      replies["/api/questions/summary"] = { mode: "ask", open: 1, latest_open: 7 };
      replies["/api/questions"] = { questions: [{ question_id: 7, stage: "screen", kind: "missing_doi", title: "Which DOI?",
        prompt: "Two records share a title.", created_at: "", options: [{ value: "a", label: "Keep the first" }, { value: "b", label: "Keep the second" }] }] };
      $("#elsewhere").focus();
    """
    r = run_dom("""
      for (let i = 0; i < 4; i++) await flush();
      const dlg = $("#ask-dialog");
      const opened = { open: dlg.open, focus: document.activeElement.textContent, labelled: dlg.getAttribute("aria-labelledby") };
      dlg.dispatchEvent(new Event("cancel")); for (let i = 0; i < 4; i++) await flush();
      out({ opened, open: dlg.open, focus: focused(), answers: posts().length });
    """, html=ASK, scripts=("common.js", "questions.js"), pre=pre)
    assert r["opened"] == {"open": True, "focus": "Keep the first", "labelled": "ask-title-7"}
    assert (r["open"], r["focus"], r["answers"]) == (False, "#elsewhere", 0)


ADJ = ('<input id="adj-name"><input id="adj-desc"><input id="adj-cue"><button id="adj-add">Add</button>'
       '<button id="adj-seed"></button><button id="adj-propose"></button><button id="adj-find"></button>'
       '<span id="adj-msg" role="status"></span><div id="adj-list"></div><button id="adj-cases-btn"></button><button id="adj-rerun-btn"></button>')


def test_adjacent_edit_is_typed_in_the_dialog():
    """Edit opens one dialog with the four fields, labelled and filled with the current text; Save sends them in one
    POST (the same body as `wider construct adjacent edit`); Esc sends nothing."""
    r = run_dom("""
      replies["/api/adjacent"] = { row: { version: 1 }, approved: ["plants"], literatures: [{ key: "plants", name: "Indoor plants",
        description: "Potted plants indoors", discriminating_cue: "potted", differs_from_construct: "indoor", status: "approved", origin: "manual" }] };
      replies["/api/adjacent/plants/edit"] = { ok: true };
      await loadAdjacent();
      const edit = () => $$("#adj-list button").find(b => b.textContent === "edit");
      edit().focus(); edit().click(); await flush();
      let dlg = $("dialog.choice-dialog");
      const fields = dlg.querySelectorAll("label.dlg-field").map(l => [l.childNodes[0].textContent.trim(), l.querySelector("input").value]);
      const shown = { title: dlg.querySelector("#choice-title").textContent, fields, focus: document.activeElement.dataset.act,
                      buttons: dlg.querySelectorAll("button").map(b => b.textContent) };
      dlg.dispatchEvent(new Event("cancel")); await flush();
      const afterEsc = posts().length;
      edit().click(); await flush();
      dlg = $("dialog.choice-dialog");
      dlg.querySelector("[data-field=name]").value = "House plants ";
      dlg.querySelector("[data-field=differs]").value = "indoor only";
      dlg.querySelector("[data-act=ok]").click(); await flush(); await flush();
      out({ shown, afterEsc, sent: posts() });
    """, html=ADJ, scripts=("common.js", "adjacent.js"), pre="global.loadConstruct = async () => {}; global.loadScreen = async () => {};")
    assert r["shown"] == {"title": "Edit Indoor plants", "focus": "cancel", "buttons": ["Save", "Cancel"], "fields": [
        ["Name", "Indoor plants"], ["Description (what these studies examine)", "Potted plants indoors"],
        ["Discriminating cue", "potted"], ["How it differs from the construct", "indoor"]]}
    assert r["afterEsc"] == 0
    assert r["sent"] == [["/api/adjacent/plants/edit", "POST", {"name": "House plants", "description": "Potted plants indoors",
                                                                 "discriminating_cue": "potted", "differs_from_construct": "indoor only"}]]


def _sets_handler() -> str:
    """The Screening sets card's click handler, as app.js has it."""
    src = static("app.js")
    a = src.index('$("#ss-table").onclick = async (ev) => {')
    return src[a:src.index("\n};\n", a) + 3]


SETS = r"""
const els = {};
const $ = (s) => els[s] || (els[s] = { value: "", textContent: "" });
$("#ss-protocol").value = "main";
const setsShown = [{ result_set: 2, label: "first try" }];
const calls = [], asked = [];
let failUse = true;
global.api = async (url, opts) => {
  calls.push([url, (opts && opts.method) || "GET", opts && opts.body ? JSON.parse(opts.body) : null]);
  if (url.endsWith("/use") && failUse) { const e = new Error("main set 1 uses another source; switch it first, or add --with-source to switch both"); e.status = 400; throw e; }
  return {};
};
const loadScreen = async () => {};
const Backends = { useRefusalHtml: (m) => `<p>${m}</p>` };
let fieldsAnswer = null, choiceAnswer = null;
global.askFields = async (title, fields, action) => { asked.push({ title, fields, action }); return fieldsAnswer; };
global.askChoice = async (html) => { asked.push({ html }); return choiceAnswer; };
const click = (act) => $("#ss-table").onclick({ target: { closest: () => ({ dataset: { setAct: act, n: "2" } }) } });
"""


def test_set_label_is_typed_in_the_dialog():
    """Label opens the in-page field dialog filled with the current label; Cancel sends nothing; Save label sends the
    typed text in the same PATCH as `wider screen sets label`."""
    from tests.unit.test_backends_js_node import run_node

    r = run_node(SETS + _sets_handler() + """
      await click("label"); const cancelled = calls.length;
      fieldsAnswer = { label: "second try" }; await click("label");
      process.stdout.write(JSON.stringify({ asked, cancelled, calls, msg: $("#ss-msg").textContent }));
    """)
    assert r["asked"][0] == {"title": "Label of main set 2", "action": "Save label",
                             "fields": [{"key": "label", "label": "Label (empty removes it)", "value": "first try"}]}
    assert r["cancelled"] == 0
    assert r["calls"] == [["/api/screen/sets/main/2", "PATCH", {"label": "second try"}]]
    assert r["msg"] == "main set 2 relabelled"


def test_use_both_switches_set_and_source():
    """The refusal to use a set with another source asks in the page; "Use both" sends the switch with_source (it never
    did while the dialog's answer was compared as a string), Cancel sends nothing more."""
    from tests.unit.test_backends_js_node import run_node

    r = run_node(SETS + _sets_handler() + """
      choiceAnswer = null; await click("use"); const cancelled = calls.map(c => c[0]);
      calls.length = 0; choiceAnswer = { act: "both", fields: {} }; await click("use");
      process.stdout.write(JSON.stringify({ cancelled, calls: calls.map(c => c[0]), html: asked[0].html, msg: $("#ss-msg").textContent }));
    """)
    assert r["cancelled"] == ["/api/screen/sets/main/2/use"]
    assert r["calls"] == ["/api/screen/sets/main/2/use", "/api/screen/sets/main/2/use?with_source=true"]
    assert "; or use both" in r["html"] and "--with-source" not in r["html"]
    assert r["msg"] == "main uses set 2 for analysis (undo in History)"


def test_shell_errors_are_toasts():
    """Forget and opening a project from a card report a failure as an error toast (stays until dismissed), not a
    browser alert. A toast's line is read out through the page's persistent live regions (#toast-alert for an error,
    #toast-live for a notice), so the toast box itself has no role (owner fixes M1, 1 Nov 2026)."""
    src = static("shell.js")
    assert "alert(" not in src
    assert src.count("errorToast(e.message)") == 2 and 'Jobs.toast(text, "danger")' in src
    html = static("index.html")
    assert '<p id="toast-live" class="sr-only" role="status"></p>' in html and 'id="toast-alert" class="sr-only" role="alert"' in html
    r = run_dom("""
      const bad = Jobs.toast("the project folder could not be read", "danger");
      const good = Jobs.toast("saved");
      await new Promise((ok) => setTimeout(ok, 80));
      out({ bad: bad.getAttribute("role"), good: good.getAttribute("role"), alert: $("#toast-alert").textContent,
            live: $("#toast-live").textContent,
            kept: $("#toasts").querySelectorAll(".toast").length, x: bad.querySelector("[data-x]").getAttribute("aria-label") });
    """, html=TOASTS + LIVE, scripts=("common.js", "jobs.js"))
    assert r == {"bad": None, "good": None, "alert": "the project folder could not be read", "live": "saved",
                 "kept": 2, "x": "Dismiss"}


def test_a_toast_from_a_drawer_decision_is_not_read_twice():
    """While the drawer saves a decision its own line says what was saved; the Undo toast that comes with it is
    shown but not read out a second time (owner fixes M1)."""
    r = run_dom("""
      window.Drawer = { isDeciding: () => true };
      Jobs.toast("Label saved: exclude.", "success", null, { label: "Undo", fn() {} });
      await new Promise((ok) => setTimeout(ok, 80));
      out({ live: $("#toast-live").textContent, shown: $("#toasts").querySelectorAll(".toast").length });
    """, html=TOASTS + LIVE, scripts=("common.js", "jobs.js"))
    assert r == {"live": "", "shown": 1}


def _drawer_html() -> str:
    html = static("index.html")
    a = html.index('<aside id="drawer"')
    return html[a:html.index("</aside>", a) + 8]


DRAWER_PRE = """
  const key = (k, o = {}) => { const ev = new KeyboardEvent("keydown", { key: k, ...o }); (document.activeElement || document.body).dispatchEvent(ev); return ev.defaultPrevented; };
  const named = () => { const d = $("#drawer"), id = d.getAttribute("aria-labelledby"); return { role: d.getAttribute("role"), name: id && $("#" + id) ? $("#" + id).textContent : d.getAttribute("aria-label") }; };
  const ITEM = { title: "A synthetic title", abstract: "Synthetic abstract." };
  const base = { items: [ITEM], notes: false, render: async (r, x) => x.recordBlock(r),
                 decisions: [{ value: "include", label: "Include" }, { value: "exclude", label: "Exclude" }, { value: "maybe", label: "Maybe" }] };
"""


def test_drawer_is_a_dialog_that_keeps_tab():
    """Over the Labelling queue the drawer is role="dialog" named by the record's title; Tab on its last control goes
    to its first, Shift+Tab back to the last, and Esc closes it with the focus on the record's row."""
    r = run_dom("""
      $("#queue").innerHTML = '<tr data-idx="0" tabindex="0"><td>A synthetic title</td></tr>';
      $("#queue tr").focus();
      Drawer.open({ ...base, label: "Labelling queue", table: "#queue" }); for (let i = 0; i < 4; i++) await flush();
      const f = $("#drawer").querySelectorAll("button, [tabindex]").filter(x => !x.disabled && x.tabIndex >= 0 && x.getClientRects().length);
      const last = f[f.length - 1]; last.focus();
      const tab = key("Tab"), afterTab = document.activeElement === f[0];
      const shift = key("Tab", { shiftKey: true }), afterShift = document.activeElement === last;
      key("Escape");
      out({ ...named(), tab, afterTab, shift, afterShift, closed: $("#drawer").hidden, row: document.activeElement === $("#queue tr"),
            first: f[0].id, last: last.textContent });
    """, html='<table><tbody id="queue"></tbody></table>' + _drawer_html(), scripts=("common.js", "drawer.js"), pre=DRAWER_PRE)
    assert r["role"] == "dialog" and r["name"] == "A synthetic title"
    assert r["tab"] and r["afterTab"] and r["shift"] and r["afterShift"]
    assert r["closed"] and r["row"]
    assert r["first"] == "dw-close" and r["last"].startswith("Maybe")


def test_docked_drawer_is_a_named_region():
    """Docked beside the Deduplication list the drawer is role="region" named by the pair's first title; Tab is left to
    the browser (not prevented, no focus moved) and Esc keeps it open with the focus on the list."""
    r = run_dom("""
      location.hash = "#dedup";
      Drawer.open({ ...base, label: "Pair to decide", dock: "#pair-box", onEsc: () => $("#pair-list").focus(),
                    render: async (p, x) => x.recordBlock(p) + x.recordBlock({ title: "Another synthetic title" }) });
      for (let i = 0; i < 4; i++) await flush();
      const f = $("#drawer").querySelectorAll("button").filter(x => !x.disabled && x.getClientRects().length);
      const last = f[f.length - 1]; last.focus();
      const tab = key("Tab"), stayed = document.activeElement === last;
      key("Escape");
      out({ ...named(), tab, stayed, open: !$("#drawer").hidden, list: document.activeElement === $("#pair-list"),
            docked: $("#drawer").parentNode === $("#pair-box") });
    """, html='<section class="page" id="page-dedup"><div id="pair-list" tabindex="-1"></div><div id="pair-box"></div></section>'
             + _drawer_html(), scripts=("common.js", "drawer.js"), pre=DRAWER_PRE)
    assert r == {"role": "region", "name": "A synthetic title", "tab": False, "stayed": True, "open": True, "list": True,
                 "docked": True}


def _show_page() -> str:
    """app.js's PAGES, showPage and the hash listener, run with the page loaders stubbed."""
    src = static("app.js")
    a = src.index("const PAGES = {")
    b = src.index("// Tabs inside a page (.page-tabs)")
    c = src.index('window.addEventListener("hashchange"')
    return src[a:b] + src[c:src.index("\n", c)] + "\n"


SHELL_HTML = ('<a class="skip-link" href="#main">Skip to content</a><div id="toasts"></div><div id="job-bar" hidden></div>'
              + section("page-home") + section("page-rank") + section("page-dedup"))
SHELL_PRE = """
  global.marked = []; global.markCurrentPage = (k) => marked.push(k);
  global.showLoadError = () => {}; global.renderStageResults = () => {}; global.pageLoader = () => null;
  global.scrolls = 0; window.scrollTo = () => { scrolls++; };
  const $ = (s) => document.querySelector(s);
"""


def test_page_open_moves_focus_title_and_scroll():
    """The first page drawn leaves the focus alone (the skip link stays first); opening Deduplication afterwards puts
    the focus on its heading, scrolls to the top and titles the window "Deduplication – WIDER", with "(1 running) "
    in front while a run goes on."""
    r = run_dom("""
      replies["/api/jobs?active=true"] = [];
      showPage("home"); const first = { focus: document.activeElement === document.body, title: document.title, scrolls };
      showPage("dedup");
      const h1 = $("#page-dedup h1");
      const second = { focus: document.activeElement === h1, tabindex: h1.getAttribute("tabindex"), title: document.title, scrolls };
      replies["/api/jobs?active=true"] = [{ job_id: "j1", label: "Ranking", n_done: 1, n_total: 4 }];
      await Jobs.poll(); await flush();
      out({ first, second, running: document.title, hidden: $("#page-home").hidden });
    """, html=SHELL_HTML, scripts=("common.js", "jobs.js"), pre=SHELL_PRE + _show_page())
    assert r["first"] == {"focus": True, "title": "Your projects – WIDER", "scrolls": 0}
    assert r["second"] == {"focus": True, "tabindex": "-1", "title": "Deduplication – WIDER", "scrolls": 1}
    assert r["running"] == "(1 running) Deduplication – WIDER" and r["hidden"]


def test_unknown_address_says_page_not_found():
    """An unknown address opens Home, marked current, the address reads #home and Home's status line says
    "page not found: X" once, as text (markup in the hash is not drawn); the next page clears it."""
    r = run_dom("""
      showPage("rank");
      location.hash = '#<img src=x onerror=alert(1)>'; showPage(location.hash.slice(1));
      const st = $("#home-status");
      const got = { hash: location.hash, home: !$("#page-home").hidden, rank: $("#page-rank").hidden, marked: marked.slice(-1)[0],
                    text: st.textContent, imgs: st.querySelectorAll("img").length, role: st.getAttribute("role") };
      showPage("rank");
      out({ ...got, after: st.textContent });
    """, html=SHELL_HTML, scripts=("common.js", "jobs.js"), pre=SHELL_PRE + _show_page())
    assert r == {"hash": "#home", "home": True, "rank": True, "marked": "home", "text": "page not found: <img src=x onerror=alert(1)>",
                 "imgs": 0, "role": "status", "after": ""}


# ------------------------------------------------------------------ focus after an action that redraws (D2)

_IX = static("index.html")
BODY = _IX[_IX.index(">", _IX.index("<body")) + 1:_IX.index("<script")]   # the whole page, for shell.js
SHELL_PAGE_PRE = next(line for line in static("app.js").splitlines() if line.startswith("const PAGES = ")) + """
let STAGES = [{ key: "setup", title: "Setup", state: "done", missing: [], description: "d", why: "w", command: "init", section: "s" },
              { key: "rank", title: "Ranking", state: "ready", missing: [], description: "d", why: "w", command: "rank", section: "s" }];
let NEXT_KEY = "rank", LAST_RESULTS = {}, SECTIONS = [], PROJECT_TITLE = "", PROJECT_KEY = null, CONSTRUCT_BACKEND = null, currentPage = "settings";
const stageTitle = (k) => k; const oneLine = (s) => String(s || ""); global.fmtWhen = (t) => t;
global.showPage = () => {}; global.renderLookupsCard = () => {};
"""
FOCUS_PRE = """
  localStorage.setItem("wider.reviewer", "ana"); global.fmtWhen = (t) => t;
  let undone = [];   // the History list: change 6 by ana, then one "undo:"/"redo:" entry per undo
  const gone = new Set();   // changes already undone: History shows "undone" instead of their Undo button
  const entry = (id, summary) => ({ change_id: id, at: "t", author: "ana", kind_label: "label", summary, undoable: !gone.has(id), undone_by: gone.has(id) ? id + 1 : null });
  global.drawQueue = () => { $("#queue").innerHTML = '<tr data-idx="0" tabindex="0"><td>A synthetic title</td></tr><tr data-idx="1" tabindex="0"><td>Another</td></tr>'; };
  global.afterAction = async () => { drawQueue(); await loadHistory(); };
"""


def test_focus_kept_after_six_redrawing_actions():
    """After each action that redraws the part of the page holding the focus, the focus is on the control the spec
    names, never on the body: Ctrl+Z on a queue row and the toast's Redo keep that row; History's Undo goes to the
    new entry's Undo; Revoke on Settings to the backend's row; a Help redraw keeps the same summary (open); a
    finished screening run keeps the run button."""
    r = run_dom("""
      let next = 7; const QROW = '#queue tr[data-idx="1"]';
      const rows = () => [entry(6, "label record 3"), ...undone.map(u => entry(u.id, u.summary))].reverse();
      replies["/api/history/kinds"] = [];
      replies["/api/history"] = () => rows();
      const done = (summary) => { gone.add(next - 1); const u = { id: next++, summary }; undone.push(u); return { change_id: u.id, summary: `${summary} (change ${u.id - 1})` }; };
      replies["/api/history/undo-last"] = () => done("undo: label record 3");
      for (const id of [7, 8, 9, 10]) replies[`/api/history/${id}/undo`] = () => done(id % 2 ? "redo: label record 3" : "undo: label record 3");
      $("#page-history").hidden = false;
      drawQueue(); await loadHistory();
      const settle = async () => { for (let i = 0; i < 8; i++) await flush(); };
      const res = {};
      // 1. Ctrl+Z on a queue row
      $(QROW).focus();
      press("z", { ctrlKey: true }); await settle();
      res.ctrlz = document.activeElement === $(QROW);
      // 2. the toast's Redo (reached with Tab): the focus comes back to the row
      $("#toasts [data-act]").focus(); $("#toasts [data-act]").click(); await settle();
      res.redo = document.activeElement === $(QROW);
      // 3. History's Undo on the newest entry: the focus goes to the new "undo:" entry's Undo
      const top = $("#hist-table [data-undo]"), was = +top.dataset.undo;
      top.focus(); top.click(); await settle();
      res.history = document.activeElement.dataset && +document.activeElement.dataset.undo === next - 1 && next - 1 !== was;
      out(res);
    """, html=section("page-history") + TOASTS + '<button id="art-restore"></button><table><tbody id="queue"></tbody></table>',
        scripts=("common.js", "jobs.js", "history.js"), pre=FOCUS_PRE)
    assert r == {"ctrlz": True, "redo": True, "history": True}

    # 4. Revoke on Settings, 5. Help redrawn while a summary has the focus (shell.js on the whole page)
    r = run_dom("""
      let allowed = ["screen"];
      Object.assign(replies, { "/api/project/summary": { backend_check: null, backends: { llm: null, embedding: null }, root: "/r" },
        "/api/project": {}, "/api/embeddings": { embeddings: [], default: null }, "/api/embeddings/consent": [],
        "/api/providers": { providers: [{ name: "gpt", kind: "openai", model: "m", default: true, stages: [] }], stage_providers: {} },
        "/api/providers/consent": () => [{ name: "gpt", destination: "api.example.org", needs_consent: true, operations: allowed }],
        "/api/providers/gpt/consent": () => { allowed = []; return { ok: true }; } });
      const res = {};
      $("#page-settings").hidden = false;
      await loadSettings();
      const b = $("#settings-consent [data-revoke]"); b.focus(); b.click();
      for (let i = 0; i < 8; i++) await flush();
      const a = document.activeElement;
      res.revoke = { row: a.tagName === "TR" && a.dataset.focusKey === "consent-llm-gpt", button: !$("#settings-consent [data-revoke]") };
      $("#page-help").hidden = false;
      loadHelp();
      const sum = () => $('#help-stages summary[data-focus-key="help-rank"]');
      const old = sum(); old.parentNode.open = true; old.focus();
      loadHelp();
      res.help = { same: document.activeElement === sum() && sum() !== old, open: sum().parentNode.open };
      out(res);
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"), pre=SHELL_PAGE_PRE)
    assert r == {"revoke": {"row": True, "button": True}, "help": {"same": True, "open": True}}

    # 6. a screening run that finishes while its button has the focus: the page redraws, the button keeps it
    src = static("app.js")
    start = src[src.index("async function startScreen"):src.index('$("#sc-run").onclick')]
    r = run_dom("""
      replies["/api/jobs/screen"] = { job_id: "j1" };
      replies["/api/jobs?active=true"] = [{ job_id: "j1", label: "Screening", n_done: 0, n_total: 2 }];
      replies["/api/jobs/j1"] = { job_id: "j1", status: "done", label: "Screening", stage: "screen" };
      const btn = $("#sc-run"); btn.focus();
      await startScreen({ scope: "all" });
      replies["/api/jobs?active=true"] = [];
      await Jobs.poll(); for (let i = 0; i < 8; i++) await flush();
      out({ redrawn: redraws, button: document.activeElement === btn });
    """, html=TOASTS + '<button id="sc-run">Run screening</button><p id="sc-msg"></p>', scripts=("common.js", "jobs.js"),
        pre="""global.redraws = 0; global.scPicked = "gpt"; global.showStageError = () => {};
               global.loadScreen = async () => { redraws++; const b = $("#sc-run"); b.blur(); b.disabled = true; await flush(); b.disabled = false; };
            """ + start)
    assert r == {"redrawn": 1, "button": True}


# 20-second timers held back so a test can let them pass at once; every other timer runs as usual
TIMERS_PRE = """
  const realTimeout = setTimeout; global.due = [];
  global.setTimeout = (fn, ms) => ms === 20000 ? (due.push(fn), 0) : realTimeout(fn, ms);
"""


def test_toast_waits_while_hovered_or_focused():
    """Two Undo toasts and a plain notice when their 20 seconds pass: the one holding the focus and the one under the
    mouse stay open, the notice closes; each then closes once it holds neither the focus nor the mouse."""
    r = run_dom("""
      const texts = () => $$("#toasts .toast span").map(s => s.textContent);
      const a = Jobs.toast("Saved A.", "success", null, { label: "Undo", fn: async () => {} });
      const b = Jobs.toast("Saved B.", "success", null, { label: "Undo", fn: async () => {} });
      Jobs.toast("A notice.", "success");
      a.querySelector("[data-act]").focus();
      b.dispatchEvent(new Event("mouseenter"));
      due.forEach(fn => fn()); await flush();
      const passed = texts();
      $("#elsewhere").focus(); await flush(); await flush();
      const unfocused = texts();
      b.dispatchEvent(new Event("mouseleave")); await flush();
      out({ passed, unfocused, left: texts() });
    """, html=TOASTS + '<button id="elsewhere"></button>', scripts=("common.js", "jobs.js"), pre=TIMERS_PRE)
    assert r == {"passed": ["Saved A.", "Saved B."], "unfocused": ["Saved B."], "left": []}


def test_toast_button_returns_the_focus():
    """ana saves a label from row 3 of the queue, moves to the toast's Undo and activates it (Enter on a button is a
    click): the label is undone and the focus is back on row 3, redrawn by the undo."""
    r = run_dom("""
      replies["/api/history/kinds"] = []; replies["/api/history"] = [];
      replies["/api/history/12/undo"] = { change_id: 13, summary: "undo: ana labelled record 3 (change 12)" };
      const row = '#queue tr[data-idx="2"]';
      global.drawQueue = () => { $("#queue").innerHTML = [0, 1, 2, 3].map(i => `<tr data-idx="${i}" tabindex="0"><td>Synthetic ${i}</td></tr>`).join(""); };
      drawQueue();
      const old = $(row); old.focus();
      offerUndo(12, "Saved: include.");
      const btn = $("#toasts [data-act]"); btn.focus(); btn.click();
      for (let i = 0; i < 8; i++) await flush();
      out({ undo: posts().map(c => c[0]), back: document.activeElement === $(row) && $(row) !== old,
            toast: $$("#toasts .toast span").map(s => s.textContent) });
    """, html=section("page-history") + TOASTS + '<button id="art-restore"></button><table><tbody id="queue"></tbody></table>',
        scripts=("common.js", "jobs.js", "history.js"), pre=FOCUS_PRE)
    assert r["undo"] == ["/api/history/12/undo?hide_samples=true"] and r["back"] is True   # Blind is on by default (owner answer 37e6): the answer names no sample
    assert r["toast"] == ["Undone: ana labelled record 3"]


OBSERVED = """
  const observers = [];
  global.MutationObserver = class { constructor(fn) { observers.push(fn); } observe() {} disconnect() {} };
"""


def test_toast_undo_then_redo_keep_the_row_and_one_toast():
    """keyboard-only persona F1/F4: the label was saved from the drawer, so the toast has no control of its own to
    return to; Enter on the toast's Undo, then on the new toast's Redo, each puts the focus back on row 3 (redrawn),
    the control focused last outside the toasts, not on the next toast's button; the older toast is gone, so no button acts on a change already
    taken back."""
    r = run_dom("""
      replies["/api/history/kinds"] = []; replies["/api/history"] = [];
      replies["/api/history/12/undo"] = { change_id: 13, summary: "undo: ana labelled record 3 (change 12)" };
      replies["/api/history/13/undo"] = { change_id: 14, summary: "redo: ana labelled record 3 (change 13)" };
      const row = '#queue tr[data-idx="2"]';
      // as a browser does: a redraw that replaces the focused row drops the focus to the body and the page's
      // MutationObserver sees it
      global.drawQueue = () => {
        const a = document.activeElement, inQueue = a && a !== document.body && $("#queue").contains(a);
        $("#queue").innerHTML = [0, 1, 2, 3].map(i => `<tr data-idx="${i}" tabindex="0"><td>Synthetic ${i}</td></tr>`).join("");
        if (inQueue) document.activeElement = document.body;
        observers.forEach(fn => fn([]));
      };
      drawQueue();
      // the queue reloads on "wider:undone" without being awaited, as records.js does: the redraw comes after the undo
      global.afterAction = async () => {};
      window.addEventListener("wider:undone", () => setTimeout(drawQueue, 0));
      // the label is saved with the drawer's Include button, which goes when the drawer closes; the focus is back
      // on row 3, and Tab reaches the toast
      $("#drawer").innerHTML = '<button id="lbl-include">Include</button>'; $("#lbl-include").focus();
      offerUndo(12, "Saved: include.");
      $("#drawer").innerHTML = ""; $(row).focus();
      const steps = [];
      for (const label of ["Undo", "Redo"]) {
        const btn = $("#toasts [data-act]"); steps.push(btn.textContent); btn.focus(); btn.click();
        for (let i = 0; i < 8; i++) { await flush(); observers.forEach(fn => fn([])); }
        steps.push(document.activeElement === $(row) || focused());
      }
      out({ steps, posts: posts().map(c => c[0]), toast: $$("#toasts .toast span").map(s => s.textContent) });
    """, html=section("page-history") + TOASTS + '<button id="art-restore"></button><div id="drawer"></div><table><tbody id="queue"></tbody></table>',
        scripts=("common.js", "jobs.js", "history.js"), pre=OBSERVED + FOCUS_PRE)
    assert r["steps"] == ["Undo", True, "Redo", True]
    assert r["posts"] == ["/api/history/12/undo?hide_samples=true", "/api/history/13/undo?hide_samples=true"]   # Blind is on by default (owner answer 37e6): the answer names no sample
    assert r["toast"] == ["Redone: ana labelled record 3"]


def test_redo_by_key():
    """Ctrl+Z twice, then Ctrl+Shift+Z twice on a queue row: two undo-last calls and two redo-last calls signed with
    the page's name, each redo toast says "Redone"; a third Ctrl+Shift+Z shows the refusal as an error toast. In a text
    field the key is the field's own. With no name the page says so and sends nothing."""
    r = run_dom("""
      replies["/api/history/kinds"] = []; replies["/api/history"] = [];
      const undos = [{ change_id: 8, summary: "undo: ana labelled record 5 (change 7)" }, { change_id: 9, summary: "undo: ana labelled record 4 (change 6)" }];
      const redos = [{ change_id: 10, summary: "redo: ana labelled record 4 (change 9)" }, { change_id: 11, summary: "redo: ana labelled record 5 (change 8)" }];
      replies["/api/history/undo-last"] = () => undos.shift();
      replies["/api/history/redo-last"] = () => { const r = redos.shift(); if (r) return r; const e = new Error("nothing to redo by ana"); e.status = 409; return e; };
      const settle = async () => { for (let i = 0; i < 6; i++) await flush(); };
      const QROW = '#queue tr[data-idx="1"]';
      drawQueue(); $(QROW).focus();
      const toasts = [];
      for (const shiftKey of [false, false, true, true, true]) {
        press("z", { ctrlKey: true, shiftKey }); await settle();
        const t = $$("#toasts .toast"); const last = t[t.length - 1];
        toasts.push([last.querySelector("span").textContent, (/danger/.test(last.className) ? "alert" : "status")]);
      }
      const sent = posts().map(c => c[0]);
      $("#text-field").focus(); press("Z", { metaKey: true, shiftKey: true }); await settle();
      const inField = posts().length - sent.length;
      window.WiderName.get = () => "";   // the page as it reloads after Change cleared the name (D33)
      $(QROW).focus();
      press("z", { metaKey: true, shiftKey: true }); await settle();
      const t = $$("#toasts .toast");
      out({ sent, toasts, inField, noName: [posts().length - sent.length, t[t.length - 1].querySelector("span").textContent, (/danger/.test(t[t.length - 1].className) ? "alert" : "status")] });
    """, html=section("page-history") + TOASTS + '<input id="text-field" type="text"><button id="art-restore"></button>'
        '<table><tbody id="queue"></tbody></table>', scripts=("common.js", "jobs.js", "history.js"), pre=FOCUS_PRE)
    assert r["sent"] == (["/api/history/undo-last?author=ana&hide_samples=true"] * 2   # Blind is on by default (owner answer 37e6): the answer names no sample
                         + ["/api/history/redo-last?author=ana&hide_samples=true"] * 3)
    assert r["toasts"] == [["Undone: ana labelled record 5", "status"], ["Undone: ana labelled record 4", "status"],
                           ["Redone: ana labelled record 4", "status"], ["Redone: ana labelled record 5", "status"],
                           ["nothing to redo by ana", "alert"]]
    assert r["inField"] == 0
    assert r["noName"] == [0, "Redo needs your name: set it with Change in the top bar. Nothing was redone.", "alert"]


def test_focused_row_keeps_clear_of_toasts():
    """Three toasts open: the page holds the stack's height in --toast-h; a queue row that takes the focus is scrolled
    with block "nearest" (so its scroll margin keeps it above the toasts); with the toasts closed the height is 0."""
    r = run_dom("""
      $("#toasts")._height = 180;
      const ts = [1, 2, 3].map(i => Jobs.toast(`Saved ${i}.`, "success", null, { label: "Undo", fn: async () => {} }));
      const h = document.documentElement.style["--toast-h"];
      $('#queue tr[data-idx="3"]').focus();
      const scrolled = $('#queue tr[data-idx="3"]').scrolled;
      ts.forEach(t => t.querySelector("[data-x]").click());
      out({ h, scrolled, closed: document.documentElement.style["--toast-h"] });
    """, html=TOASTS + '<table><tbody id="queue">' + "".join(f'<tr data-idx="{i}" tabindex="0"><td>Synthetic {i}</td></tr>' for i in range(4)) + "</tbody></table>",
        scripts=("common.js", "jobs.js"), pre=TIMERS_PRE)
    assert r == {"h": "196px", "scrolled": [{"block": "nearest"}], "closed": "0px"}


def test_artifact_tabs_are_a_tablist():
    """The Concepts and synonyms artifact tabs: a tab list with one tab stop; Right on "cells" focuses and opens the
    next tab (aria-selected true, every other false, the panel named by it); Left on the first wraps to the last."""
    src = static("app.js")
    code = src[src.index("const KINDS = "):src.index("\n", src.index("const KINDS = "))] + "\n" + \
        src[src.index("// the artifact tabs are a tab list"):src.index("async function loadConstruct()")]
    r = run_dom("const esc = (x) => String(x);\n" + code + """
      let artKind = "cells", artVersions = { cells: [{ version: 2, status: "draft" }] };
      const opened = []; global.loadConstruct = async () => { opened.push(artKind); drawArtTabs(); };
      drawArtTabs();
      const tabs = () => $$("#art-tabs [role=tab]").map(b => [b.dataset.kind, b.getAttribute("aria-selected"), b.getAttribute("tabindex")]);
      const before = tabs();
      $("#art-tab-cells").focus(); press("ArrowRight"); await flush();
      const after = { tabs: tabs(), focus: document.activeElement.id, panel: $("#art-panel").getAttribute("aria-labelledby") };
      press("ArrowLeft"); await flush(); press("ArrowLeft"); await flush();
      out({ before, after, wrapped: document.activeElement.id, opened,
            roles: [$("#art-tabs").getAttribute("role"), $("#art-panel").getAttribute("role")] });
    """, html=section("page-construct"))
    assert r["roles"] == ["tablist", "tabpanel"]
    assert r["before"][0] == ["cells", "true", "0"] and all(t[1:] == ["false", "-1"] for t in r["before"][1:])
    assert r["after"]["focus"] == "art-tab-codebook" and r["after"]["panel"] == "art-tab-codebook"
    assert [t[1] for t in r["after"]["tabs"]] == ["false", "true", "false", "false", "false"]
    assert r["wrapped"] == "art-tab-near_miss" and r["opened"] == ["codebook", "cells", "near_miss"]


def test_drawn_controls_name_their_row(tmp_path):
    """Task 4.2: the buttons and selects the Setup page draws per row say whose row they act on, and the model cards'
    buttons name their model. Drawn by setup.js and pickers.js from the real routes of a synthetic project."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from tests.unit.test_dry_run import _client
    from tests.unit.test_validation_reuse import _frozen

    c = _client(_frozen(tmp_path, "names"))
    c.post("/api/prior-reviews/text", json={"text": "Doe J. 2020. A synthetic study. J Test 1:1-2.", "label": "Synthetic prior"})
    c.post("/api/platforms/add", json={"key": "ebsco"})
    replies = {u: c.get(u).json() for u in ("/api/benchmark/seed", "/api/prior-reviews", "/api/platforms")}
    rows = [{"name": "alpha", "kind": "ollama", "model": "m1"}, {"name": "beta", "kind": "openai", "model": "m2"}]
    out = run_node(ESC + PAGE + f"""
Object.assign(replies, {json.dumps(replies)}); replies["/api/seeds/files"] = {{ files: [] }};
const renderLookupsCard = () => {{}};
replies["/api/plan"] = {{ tasks: [], ready: false, summary: "" }};
globalThis.location = {{ hash: "" }};
{(STATIC / "setup.js").read_text(encoding="utf-8")}
{(STATIC / "pickers.js").read_text(encoding="utf-8")}
const drawn = {{}};   // each task page loads its own card (review-plan-and-question-window)
for (const [task, sel] of [["seeds", "#setup-seed"], ["prior", "#setup-prior"], ["databases", "#setup-databases"]]) {{
  location.hash = `#setup?task=${{task}}`; await window.renderSetupCards(); await flush(); drawn[task] = $(sel).innerHTML;
}}
console.log(JSON.stringify({{ seed: drawn.seeds, prior: drawn.prior,
  db: drawn.databases,
  cards: Pickers.cardsHtml({json.dumps(rows)}, {{ kind: "llm", chosen: "alpha", actions: ["check", "edit", "remove"] }}) }}));
""")
    for bid in ("B001", "B002"):
        assert f'data-seed-remove="{bid}" aria-label="Remove seed paper {bid}">Remove</button>' in out["seed"]
    assert 'aria-label="Remove prior review Synthetic prior">Remove</button>' in out["prior"]
    assert 'aria-label="Papers of prior review Synthetic prior">Papers</button>' in out["prior"]
    assert re.search(r'<select data-role="ebsco" aria-label="Role of EBSCOhost[^"]*">', out["db"])
    for act in ("Check", "Edit", "Remove"):
        for model in ("alpha", "beta"):
            assert f'aria-label="{act} {model}">{act}</button>' in out["cards"]


def test_one_time_format_on_every_page():
    """accessibility-and-naming 4.4 (D7): common.js's fmtWhen is the one time format (local date and time, minutes,
    the zone named, "" when empty, the text itself when it is no time), and no other page script formats a time its own way."""
    r = run_dom("""
      const iso = "2026-10-15T08:30:00+00:00";
      out({ shown: fmtWhen(iso), want: new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric",
            hour: "2-digit", minute: "2-digit", timeZoneName: "short" }),
            empty: [fmtWhen(""), fmtWhen(null), fmtWhen(undefined)], other: fmtWhen("before the split") });
    """, scripts=("common.js",))
    assert r["shown"] == r["want"] and "T08:30" not in r["shown"]
    assert r["empty"] == ["", "", ""] and r["other"] == "before the split"
    from tests.unit.a11y_dom import STATIC
    own = []
    for f in sorted(STATIC.glob("*.js")):
        if f.name == "common.js":
            continue
        src = f.read_text(encoding="utf-8")
        own += [f"{f.name}: {m.group(0)}" for m in re.finditer(
            r"(?:const|let|function)\s+fmtWhen\b|toLocale(?:Date|Time)String\(|new Date\([^)]*\)\.toLocaleString\(|"
            r"\.toISOString\(\)\.slice\(", src)]
    assert not own, own


def test_drawn_message_areas_and_artifact_refusal():
    """accessibility-and-naming 4.5: the message areas the scripts draw (Settings' backend check and lookups, the
    comparison's research-area note) are role="status"; a refused approval or save of an artifact is one role="alert"
    in the error style, the muted status line keeps the version, and loading a version clears the alert."""
    from tests.unit.test_query_roles import js_function
    r = run_dom("""
      Object.assign(replies, { "/api/project/summary": { backend_check: null, backends: { llm: null, embedding: null }, root: "/r" },
        "/api/project": {}, "/api/embeddings": { embeddings: [], default: null }, "/api/embeddings/consent": [],
        "/api/providers": { providers: [], stage_providers: {} }, "/api/providers/consent": [],
        "/api/lookups": { allowed: false, services: ["OpenAlex"], fields: ["doi"], purposes: {}, log: [] } });
      $("#page-settings").hidden = false;
      await loadSettings();
      await realLookupsCard("#settings-lookups");   // the shell harness stubs it; common.js's own card here
      const role = (sel) => { const e = $(sel); return e ? e.getAttribute("role") : "missing"; };
      out({ check: role("#settings-backends [data-check-msg]"), lookups: role("#settings-lookups [data-lookups-msg]") });
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"),
        pre=SHELL_PAGE_PRE + f"\nconst realLookupsCard = {js_function(static('common.js'), 'async function renderLookupsCard(')};")
    assert r == {"check": "status", "lookups": "status"}

    r = run_dom("""
      const res = { name: "c1", sample: "s1", configs: [], repeats: 1, labelled_text: "4 labelled", notes: [],
                    areas_message: "no research areas: the ranking has no clusters (wider rank cluster)" };
      document.body.innerHTML = Compare.resultsHtml(res, "area");
      const p = $("[data-cmp-areas-msg]");
      out({ role: p.getAttribute("role"), text: p.textContent });
    """, scripts=("common.js", "compare.js"))
    assert r == {"role": "status", "text": "no research areas: the ranking has no clusters (wider rank cluster)"}

    src = static("app.js")
    approve = next(line for line in src.splitlines() if line.startswith('$("#art-approve").onclick'))
    refusal = next(line for line in src.splitlines() if line.startswith("function artRefusal("))
    r = run_dom(f"""
      artCurrent = {{ version: 3, status: "draft" }};
      replies["/api/construct/synonyms/3/status/approved"] = () => Object.assign(new Error("v3 is not the newest version"), {{ status: 409 }});
      $("#art-status").textContent = "synonyms v3 · draft";
      {approve}
      await $("#art-approve").onclick(); await flush();
      const a = $("#art-refusal");
      const first = {{ alerts: document.querySelectorAll('[role="alert"]').filter(e => !e.hidden).length, bad: a.className,
                      text: a.textContent, hidden: a.hidden, status: $("#art-status").textContent }};
      await $("#art-approve").onclick(); await flush();
      const again = {{ alerts: document.querySelectorAll('[role="alert"]').filter(e => !e.hidden).length, text: a.textContent }};
      artRefusal("");
      out({{ first, again, cleared: a.hidden }});
    """, html=section("page-construct") + TOASTS, scripts=("common.js",),
        pre=f'var artKind = "synonyms", artCurrent = null; const loadConstruct = async () => {{}};\n{refusal}')
    assert r["first"]["alerts"] == 1 and r["first"]["bad"] == "bad" and r["first"]["hidden"] is False
    assert r["first"]["text"].startswith("Not approved: ") and "v3 is not the newest version" in r["first"]["text"]
    assert r["first"]["status"] == "synonyms v3 · draft"
    assert r["again"] == {"alerts": 1, "text": r["first"]["text"]} and r["cleared"] is True


def test_menu_button_below_640_px():
    """accessibility-and-naming 5.1 (D7): below 640 px the Menu button opens the sidebar (aria-expanded true, the
    focus on its first link, the labels shown), Esc closes it and gives the focus back to Menu, and choosing a page
    closes it; wider than 640 px the Menu button does nothing it has to undo."""
    r = run_dom("""
      const mq = window.mqs.find(m => m.media === "(max-width: 639px)");
      window.media["(max-width: 639px)"] = true; window.media["(max-width: 899px)"] = true;
      const btn = $("#menu-btn"), body = document.body;
      const before = { expanded: btn.getAttribute("aria-expanded"), controls: btn.getAttribute("aria-controls"), open: body.classList.contains("menu-open") };
      btn.focus(); btn.click();
      const opened = { expanded: btn.getAttribute("aria-expanded"), open: body.classList.contains("menu-open"),
                       labels: !body.classList.contains("sidebar-collapsed"), focusInMenu: !!document.activeElement.closest("#sidebar") };
      const ev = new Event("keydown", { bubbles: true }); ev.key = "Escape";
      document.activeElement.dispatchEvent(ev);
      const closed = { expanded: btn.getAttribute("aria-expanded"), open: body.classList.contains("menu-open"), back: document.activeElement === btn };
      btn.click(); $('#sidebar a[data-key="history"]').click();
      const chosen = { expanded: btn.getAttribute("aria-expanded"), open: body.classList.contains("menu-open") };
      btn.click(); window.media["(max-width: 639px)"] = false; mq.ls.forEach(fn => fn({ matches: false }));
      out({ before, opened, closed, chosen, widened: body.classList.contains("menu-open") });
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"), pre=SHELL_PAGE_PRE)
    assert r["before"] == {"expanded": "false", "controls": "sidebar", "open": False}
    assert r["opened"] == {"expanded": "true", "open": True, "labels": True, "focusInMenu": True}
    assert r["closed"] == {"expanded": "false", "open": False, "back": True}
    assert r["chosen"] == {"expanded": "false", "open": False} and r["widened"] is False


def test_phone_bar_slims_without_flickering():
    """ui-redesign 5.9.12 (owner, 30 Oct: "the top bar keeps glitching"): on a phone, hiding the bar's second row
    must not move the page. The browser's scroll anchoring is modelled: when the bar's place in the page (height plus
    bottom margin) changes, the scroll position moves by the same amount and another scroll event fires. Before the
    fix every slim moved the page 41 px up, which read as scrolling up and brought the row back, over and over."""
    r = run_dom("""
      window.media["(max-width: 700px)"] = true;
      const bar = $("#topbar"), body = document.body;
      Object.defineProperty(bar, "offsetHeight", { get: () => body.classList.contains("tb-slim") ? 48 : 89 });
      const place = () => bar.offsetHeight + (parseInt(bar.style.marginBottom || "0", 10) || 0);
      let flips = 0, was = false;
      const scrollTo = (y) => {
        window.scrollY = y;
        for (let i = 0; i < 6; i++) {   // the browser keeps the content still: a smaller bar moves the scroll up
          const before = place(); window.dispatchEvent(new Event("scroll"));
          const s = body.classList.contains("tb-slim"); if (s !== was) { flips++; was = s; }
          const d = place() - before; if (!d) break; window.scrollY += d;
        }
      };
      for (const y of [100, 200, 300, 400]) scrollTo(y);
      const down = { slim: body.classList.contains("tb-slim"), flips, y: window.scrollY, margin: bar.style.marginBottom };
      flips = 0; scrollTo(380);
      const up = { slim: body.classList.contains("tb-slim"), flips, y: window.scrollY, margin: bar.style.marginBottom };
      window.media["(max-width: 700px)"] = false; scrollTo(500);
      out({ down, up, wide: { slim: body.classList.contains("tb-slim"), margin: bar.style.marginBottom } });
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"), pre=SHELL_PAGE_PRE)
    assert r["down"] == {"slim": True, "flips": 1, "y": 400, "margin": "41px"}
    assert r["up"] == {"slim": False, "flips": 1, "y": 380, "margin": ""}
    assert r["wide"] == {"slim": False, "margin": ""}


def test_enter_in_limit_and_protocol_fields():
    """accessibility-and-naming 5.2: Enter in the Screening record limit updates the estimate at once and moves
    the focus to Run, sending no run (Enter on Run then starts it); Enter in a protocol setting field equals its
    Set button, once, and the focus stays on Set."""
    app, setup = static("app.js"), static("setup.js")
    limit = app[app.index('// Enter in the limit updates the estimate'):]
    limit = limit[:limit.index("\n});\n") + 5]
    proto = setup[setup.index("    // Enter in a setting's text field equals its Set button"):]
    proto = proto[:proto.index("\n    });\n") + 8]
    html = ('<input id="sc-limit"><button id="sc-run">Run</button>'
            '<div id="protocol-editor"><table><tr><td class="proto-value"><input id="proto-a-b"></td>'
            '<td><button data-proto-set="a.b">Set</button></td></tr></table></div>')
    r = run_dom("""
      const R = { est: 0, runs: 0, sets: 0 };
      let scEstimateTimer = 7; const screenEstimate = () => { R.est++; };
      $("#sc-run").onclick = () => { R.runs++; };
      """ + limit + """
      const key = (el, k, extra) => { const ev = new Event("keydown", { bubbles: true }); ev.key = k; Object.assign(ev, extra || {});
        el.dispatchEvent(ev); return ev.defaultPrevented; };
      $("#sc-limit").focus();
      R.limitPrevented = key($("#sc-limit"), "Enter"); R.afterLimit = document.activeElement.id; R.runsAfterLimit = R.runs;
      R.otherKey = key($("#sc-limit"), "a");
      const box = $("#protocol-editor");
      box.querySelector("[data-proto-set]").onclick = () => { R.sets++; };
      """ + proto + """
      $("#proto-a-b").focus();
      R.protoPrevented = key($("#proto-a-b"), "Enter");
      R.afterProto = document.activeElement.getAttribute("data-proto-set");
      R.shiftEnter = key($("#proto-a-b"), "Enter", { shiftKey: true });
      out(R);
    """, html=html)
    assert r["limitPrevented"] is True and r["afterLimit"] == "sc-run"
    assert r["est"] == 1 and r["runsAfterLimit"] == 0 and r["runs"] == 0 and r["otherKey"] is False
    assert r["protoPrevented"] is True and r["sets"] == 1 and r["afterProto"] == "a.b" and r["shiftEnter"] is False


def test_tab_in_the_drawer_reaches_the_toast_undo_and_wraps_back():
    """Over the page the drawer keeps Tab inside itself and its toasts: Tab on the drawer's last button is left to
    the browser (the toast's Undo comes next), Tab on Undo goes on to the toast's Dismiss, Tab there wraps to the drawer's
    first control and Shift+Tab on the first comes back, and a decision key pressed on Undo decides nothing (owner fixes M2, 1 Nov 2026)."""
    r = run_dom("""
      $("#queue").innerHTML = '<tr data-idx="0" tabindex="0"><td>A synthetic title</td></tr>';
      let decided = 0;
      Drawer.open({ ...base, label: "Labelling queue", table: "#queue",
                    decisions: base.decisions.map(d => ({ ...d, fn: async () => { decided++; } })) });
      for (let i = 0; i < 4; i++) await flush();
      Jobs.toast("Label saved: include.", "success", null, { label: "Undo", fn() {} });
      const undo = $("#toasts [data-act]");
      const f = $("#drawer").querySelectorAll("button, [tabindex]").filter(x => !x.disabled && x.tabIndex >= 0 && x.getClientRects().length);
      f[f.length - 1].focus();
      const leftToBrowser = !key("Tab");
      const xs = $("#toasts").querySelectorAll("button"), x = xs[xs.length - 1];   // Undo, then the toast's Dismiss
      undo.focus(); const undoOn = !key("Tab");
      x.focus(); const wrapped = key("Tab") && document.activeElement === f[0];
      const back = key("Tab", { shiftKey: true }) && document.activeElement === x;
      undo.focus(); key("2"); for (let i = 0; i < 4; i++) await flush();
      out({ leftToBrowser, undoOn, wrapped, back, decided, open: !$("#drawer").hidden });
    """, html='<table><tbody id="queue"></tbody></table>' + _drawer_html() + TOASTS + LIVE,
        scripts=("common.js", "jobs.js", "drawer.js"), pre=DRAWER_PRE)
    assert r == {"leftToBrowser": True, "undoOn": True, "wrapped": True, "back": True, "decided": 0, "open": True}


def test_a_field_error_marks_the_field_and_clears_on_input():
    """A refused folder path is shown in red beside the field, the field gets aria-invalid and is described by the
    message, and typing clears aria-invalid (owner fixes m5)."""
    r = run_dom("""
      const f = $("#path"), m = $("#msg");
      fieldError(f, m, "no such folder");
      const a = [f.getAttribute("aria-invalid"), f.getAttribute("aria-describedby"), m.querySelector(".bad").textContent, m.classList.contains("small")];
      fieldError(f, m, "still no such folder");
      const ids = f.getAttribute("aria-describedby");
      f.dispatchEvent(new Event("input"));
      out({ a, ids, after: f.getAttribute("aria-invalid") });
    """, html='<input id="path" aria-describedby="hint"><p id="hint"></p><p id="msg" class="small" role="status"></p>',
        scripts=("common.js",))
    assert r == {"a": ["true", "hint msg", "no such folder", False], "ids": "hint msg", "after": None}
    assert 'fieldError(q("#hc-path"), q("#hc-msg"), e.message)' in static("shell.js")
    assert "fieldError(field, msgEl, e.message)" in static("shell.js")


def test_a_choice_dialog_with_no_opener_left_puts_the_focus_on_main():
    """The first-time name dialog can open with no button holding the focus (a key press); closing it puts the focus
    on the page's main area, not on the body (owner fixes m4)."""
    r = run_dom("""
      document.activeElement && document.activeElement.blur && document.activeElement.blur();
      const p = askChoice('<h3 id="choice-title">Your name</h3><button data-act="ok">Save</button><button data-act="cancel">Not now</button>');
      await flush();
      $("dialog.choice-dialog [data-act=ok]").click(); await p;
      await new Promise((ok) => setTimeout(ok, 20));
      out(document.activeElement && document.activeElement.id);
    """, html='<main id="main" tabindex="-1"></main>', scripts=("common.js",))
    assert r == "main"


def test_data_tables_have_header_cells_and_names():
    """Two-column fact tables use a row header (th scope=row) for the fact's name; the tables are named; list tables
    with no visible heading have a screen-reader-only header row; menu buttons that open plain lists do not claim a
    popup menu (owner fixes M4, m6)."""
    shell, app, results, dedup = static("shell.js"), static("app.js"), static("results.js"), static("dedup.js")
    html = static("index.html")
    for label in ("Models in use", "Prior-review data policy", "Other pages"):
        assert f'aria-label="{label}"' in shell, label
    assert 'aria-label="Project summary"' in app and 'aria-label="Record counts"' in results
    for label in ("All counts", "Deduplication run details", "Deduplication list", "Syntax check"):
        assert f'aria-label="{label}"' in html, label
    # named by their visible heading: the guide keeps headings and control names apart (test_guide)
    assert 'aria-labelledby="help-keys-h"' in html and 'aria-labelledby="help-marks-h"' in html
    for src in (shell, app, results, dedup):
        assert '<th scope="row" class="kv">' in src
    assert '<thead class="sr-only"><tr><th>key</th><th>what it does</th></tr></thead>' in shell
    assert "aria-haspopup" not in shell
