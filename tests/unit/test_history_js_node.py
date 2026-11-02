"""The Restore button (history.js) under Node on the stand-in page: a restore that reopens the frozen query shows the
save notice first; Cancel restores nothing, Restore posts the route and shows the notice in the message."""
import json

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

# the one name of common.js (ui-redesign D33), read from the stubbed localStorage each time, as a switch reloads the page
NAME = r"""
global.WiderName = { NEED: "enter your name first: use Change beside “Working as” in the top bar",
  get: () => String(localStorage.getItem("wider.reviewer") || "").trim(), ask() {}, askOnce() {}, render() {}, set() {},
  needHtml: (lead) => esc(lead ? lead + " " : "") + esc(WiderName.NEED) + '. <button type="button" class="secondary small" data-set-name>Set your name</button>' };
"""

NOTICE = "v2 is frozen. Saving v3 reopens the query: the 2 validation papers seen so far no longer count as fresh validation."


def _restore(act):
    script = ESC + PAGE + NAME + f"""
replies["/api/construct/synonyms"] = {{ save_notice: {json.dumps(NOTICE)} }};
replies["/api/construct/synonyms/1/restore"] = {{ version: 3, notice: {json.dumps(NOTICE)} }};
let shown = "";
const toasts = [];
global.Jobs = {{ toast: (t, tone) => toasts.push([t, tone]) }};
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
global.artKind = "synonyms"; global.loadConstruct = () => {{}};
global.localStorage = {{ getItem: () => "" }};
{(STATIC / "refine.js").read_text(encoding="utf-8")}
{(STATIC / "history.js").read_text(encoding="utf-8")}
$("#art-version").value = "1";
await $("#art-restore").onclick();
await flush();
console.log(JSON.stringify({{ html: shown, toasts, posts: calls.filter(c => c[1] === "POST").map(c => c[0]) }}));
"""
    return run_node(script)


def test_restore_confirms_the_save_notice():
    out = _restore(None)
    assert NOTICE in out["html"] and "Restore synonyms v1 as a new version?" in out["html"]
    assert out["posts"] == [] and out["toasts"] == []
    out = _restore("go")
    assert out["posts"] == ["/api/construct/synonyms/1/restore"]
    assert out["toasts"] == [[f"synonyms v1 restored as v3 (draft). {NOTICE}", "success"]]


def test_history_filter_draws_the_newest_list():
    """Changing the Show filter twice quickly: the first list arrives last and is not drawn over the second
    (validation-reuse, keyboard ui-tester F2)."""
    row = lambda cid, s: {"change_id": cid, "at": "2026-10-20T09:00:00", "author": "ana", "kind_label": "k", "summary": s}  # noqa: E731
    script = ESC + PAGE + NAME + f"""
global.localStorage = {{ getItem: () => "" }}; global.fmtWhen = (t) => t;
replies["/api/history"] = (url) => url.includes("kind=old")
  ? new Promise(r => setTimeout(() => r([{json.dumps(row("c1", "older list"))}]), 50))
  : [{json.dumps(row("c2", "newer list"))}];
{(STATIC / "history.js").read_text(encoding="utf-8")}
$("#hist-kind").value = "old"; const first = window.loadHistory();
$("#hist-kind").value = "validation_moved"; await window.loadHistory(); await first; await flush();
console.log(JSON.stringify({{ table: $("#hist-table").innerHTML, msg: $("#hist-msg").textContent }}));
"""
    out = run_node(script)
    assert "newer list" in out["table"] and "older list" not in out["table"] and out["msg"] == "1 change"


def test_undo_button_works_again_after_cancel():
    """Undo of the uploads-final mark under a frozen query asks first; Cancel undoes nothing and leaves the row's Undo
    button enabled, so it can be pressed again (validation-reuse, new-researcher ui-tester F2)."""
    row = {"change_id": 7, "at": "2026-10-20T09:00:00", "author": "ana", "kind_label": "k", "undoable": True,
           "summary": "uploads marked final", "undo_choice": True, "undo_notice": "v3 is the frozen query."}
    script = ESC + PAGE + NAME + f"""
global.localStorage = {{ getItem: () => "" }}; global.fmtWhen = (t) => t;
let asked = "";
global.Refine = {{ askSeen: async (title, notice) => {{ asked = notice; return null; }} }};
replies["/api/history"] = [{json.dumps(row)}];
{(STATIC / "history.js").read_text(encoding="utf-8")}
await window.loadHistory();
const b = $("#hist-table").querySelectorAll("[data-undo]")[0];
await b.onclick(); await flush();
console.log(JSON.stringify({{ asked, disabled: b.disabled, msg: $("#hist-msg").textContent,
  posts: calls.filter(c => c[1] === "POST").map(c => c[0]) }}));
"""
    out = run_node(script)
    assert out["asked"] == "v3 is the frozen query." and out["posts"] == []
    assert out["msg"] == "Nothing was undone." and out["disabled"] is False


def _other(act):
    row = {"change_id": 9, "at": "2026-10-20T09:00:00", "author": "bo", "kind_label": "setting changed", "undoable": True,
           "summary": "protocol.screening.majority: 2 → 3"}
    script = ESC + PAGE + NAME + f"""
global.localStorage = {{ getItem: () => "ana" }}; global.fmtWhen = (t) => t;
const toasts = []; global.Jobs = {{ toast: (t, tone) => toasts.push([t, tone]) }};
let shown = "";
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
replies["/api/history"] = [{json.dumps(row)}];
replies["/api/history/9/undo"] = (url) => {{
  if (url.includes("other=true")) return {{ change_id: 10, summary: "undo: protocol.screening.majority: 2 → 3 (change 9)" }};
  const e = new Error("change 9 was made by bo; add --other to undo it as ana");
  e.status = 409; e.body = {{ detail: e.message, author: "bo" }}; throw e;
}};
{(STATIC / "history.js").read_text(encoding="utf-8")}
await window.loadHistory();
const b = $("#hist-table").querySelectorAll("[data-undo]")[0];
await b.onclick(); await flush();
console.log(JSON.stringify({{ shown, toasts, msg: $("#hist-msg").textContent,
  posts: calls.filter(c => c[1] === "POST").map(c => c[0]) }}));
"""
    return run_node(script)


def test_undo_dialog_names_the_author():
    """manual-review D7: undoing bo's change from the History page as "ana": the server refuses (409 with the author),
    the dialog names bo and says the undo is signed ana; "Undo bo's change" sends other=true; Cancel undoes nothing."""
    out = _other("other")
    assert "Undo bo's change?" in out["shown"] and ">Undo bo's change</button>" in out["shown"]
    assert "made by <b>bo</b>" in out["shown"] and "signed <b>ana</b>" in out["shown"]
    assert out["posts"] == ["/api/history/9/undo", "/api/history/9/undo?other=true"]
    assert out["toasts"][0] == ["Undone: protocol.screening.majority: 2 → 3", "success"]
    out = _other(None)
    assert out["posts"] == ["/api/history/9/undo"] and out["toasts"] == [] and out["msg"] == "Nothing was undone."


def test_near_match_sentence_is_shown():
    """manual-review D7: Undo my last change as "Ana" when only 'ana' has changes: the engine's sentence is shown once,
    in the History page's black line (#hist-near, not the grey message or a red alert); a later undo clears it."""
    msg = "nothing to undo by Ana; there are changes by 'ana'"
    script = ESC + PAGE + NAME + f"""
global.localStorage = {{ getItem: () => "Ana" }}; global.fmtWhen = (t) => t;
const toasts = []; global.Jobs = {{ toast: (t, tone) => toasts.push([t, tone]) }};
replies["/api/history"] = [];
replies["/api/history/undo-last?author=Ana"] = () => {{ const e = new Error({json.dumps(msg)}); e.status = 409; throw e; }};
{(STATIC / "history.js").read_text(encoding="utf-8")}
$("#hist-near").offsetParent = {{}};
await $("#hist-undo-mine").onclick(); await flush();
const first = $("#hist-near").textContent;
replies["/api/history/undo-last?author=Ana"] = {{ change_id: 4, summary: "undo: Ana labelled record 1 (change 3)" }};
await $("#hist-undo-mine").onclick(); await flush();
console.log(JSON.stringify({{ first, after: $("#hist-near").textContent, toasts }}));
"""
    out = run_node(script)
    assert out["first"] == msg and out["after"] == ""
    assert out["toasts"] == [["Undone: Ana labelled record 1", "success"]]


def test_show_list_holds_every_kind(tmp_path):
    """manual-review 6.3: the Show list is built from the real GET /api/history/kinds, so every kind the engine records
    (review decisions and sample locks among them) can be chosen; choosing one asks the list for that kind."""
    from fastapi.testclient import TestClient

    from tests.unit.test_records import _deduped
    from wider.core import journal
    from wider.ui.app import create_app

    p, _ = _deduped(tmp_path)
    kinds = TestClient(create_app(p)).get("/api/history/kinds").json()
    script = ESC + PAGE + NAME + f"""
global.localStorage = {{ getItem: () => "" }}; global.fmtWhen = (t) => t;
replies["/api/history/kinds"] = {json.dumps(kinds)};
replies["/api/history?limit=200"] = [];
replies["/api/history?limit=200&kind=review_decision"] = [];
{(STATIC / "history.js").read_text(encoding="utf-8")}
await window.loadHistory();
const html = $("#hist-kind").innerHTML;
$("#hist-kind").value = "review_decision"; await $("#hist-kind").onchange(); await flush();
console.log(JSON.stringify({{ html, gets: calls.filter(c => c[1] !== "POST").map(c => c[0]) }}));
"""
    out = run_node(script)
    for k in journal.kinds():
        assert f'<option value="{k["kind"]}">{k["label"]}</option>' in out["html"]
    assert out["html"].startswith('<option value="">all changes</option>')
    assert out["gets"].count("/api/history/kinds") == 1 and out["gets"][-1] == "/api/history?limit=200&kind=review_decision"


def test_history_blind_box_hides_sample_names():
    """Owner answer 37e6 (b): the History page's Blind box follows the Sampling page's one and asks the server to
    leave sample names out while it is ticked; unticking it unticks the Sampling page's box too."""
    script = ESC + PAGE + NAME + """
global.localStorage = { getItem: () => "" }; global.fmtWhen = (t) => t;
replies["/api/history"] = [];
""" + (STATIC / "history.js").read_text(encoding="utf-8") + """
$("#lb-blind").checked = true;
await window.loadHistory(); await flush();
const on = calls.filter(c => c[0].startsWith("/api/history?")).pop()[0], boxOn = $("#hist-blind").checked;
$("#hist-blind").checked = false; await $("#hist-blind").onchange(); await flush();
const off = calls.filter(c => c[0].startsWith("/api/history?")).pop()[0], lbOff = $("#lb-blind").checked;
$("#hist-blind").checked = true; await $("#hist-blind").onchange(); await flush();
global.localStorage = { getItem: () => "ana" };
replies["/api/history/undo-last"] = { change_id: 3, summary: "undo: ana labelled record 1 in a sample (change 2)" };
replies["/api/history/redo-last"] = { change_id: 4, summary: "redo: ana labelled record 1 in a sample (change 3)" };
await window.Undo.undo(null); await window.Undo.redo(); await flush();
const posts = calls.filter(c => c[1] === "POST").map(c => c[0]);
console.log(JSON.stringify({ on, off, boxOn, lb: lbOff, posts }));
"""
    out = run_node(script)
    assert "hide_samples=true" in out["on"] and out["boxOn"] is True
    assert "hide_samples" not in out["off"] and out["lb"] is False
    assert len(out["posts"]) == 2 and all("hide_samples=true" in u for u in out["posts"])   # Undo and Redo too


def test_history_blind_is_on_by_default():
    """Without the Sampling page's box, the History page's own Blind box (ticked in the page) decides: on."""
    script = ESC + PAGE + NAME + """
global.localStorage = { getItem: () => "" }; global.fmtWhen = (t) => t;
replies["/api/history"] = [];
document.querySelector = ((orig) => (s) => s === "#lb-blind" ? null : orig(s))(document.querySelector);
""" + (STATIC / "history.js").read_text(encoding="utf-8") + """
$("#hist-blind").checked = true;
await window.loadHistory(); await flush();
console.log(JSON.stringify({ url: calls.filter(c => c[0].startsWith("/api/history?")).pop()[0] }));
"""
    assert "hide_samples=true" in run_node(script)["url"]
