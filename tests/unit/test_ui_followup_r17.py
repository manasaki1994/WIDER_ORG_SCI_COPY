"""Tests asked for by the seventeenth round of C1 checks: scenarios that were pinned only by a helper are now run on the
surface they name (an unbalanced EBSCO string on the command line and the page, the explorer's rows on the page and on
the command line, a protocol refusal leaving the history alone, two saves on two threads, Ctrl/Cmd+Z as a key, the
consent dialog closed by Esc or Allow, a note without a name)."""
import re
import threading

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_drawer_keys_node import STATIC, run_drawer, run_node
from tests.unit.test_query_roles import APP_JS, js_function, run_js
from tests.unit.test_ui_followup_r16 import _two_cells
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import journal
from wider.core.project import Project
from wider.ui.app import create_app

runner = CliRunner()


def test_an_unbalanced_string_is_refused_on_the_command_line_and_the_page(tmp_path):
    p = Project.init(tmp_path / "r")
    bad = tmp_path / "bad.txt"
    bad.write_text("(TI (voice OR silence)", encoding="utf-8")
    r = runner.invoke(cli_app, ["query", "import", str(bad), "--dir", str(p.root)])
    assert r.exit_code == 1 and "could not parse:" in r.output and "Traceback" not in r.output
    bad.write_text("(voice OR silence) AND", encoding="utf-8")   # a dangling AND, the page's second case (21st round)
    r = runner.invoke(cli_app, ["query", "import", str(bad), "--dir", str(p.root)])
    assert r.exit_code == 1 and "could not parse: unexpected end of query" in r.output and "Traceback" not in r.output
    c = TestClient(create_app(p))
    for text in ["(TI (voice OR silence)", "TI (voice) AND"]:
        d = c.post("/api/query/import", json={"text": text})
        assert d.status_code == 400 and d.json()["detail"].startswith("could not parse: "), d.text
    assert artifacts.list_versions(p, "synonyms") == []
    assert not list(p.artifact_dir("synonyms").glob("v*.yaml"))


def _page_render(reply: dict, offset: int) -> dict:
    """``loadExplorer`` of app.js on a stand-in page, answering its request with REPLY (the API's own answer)."""
    return run_js("let rkOffset = DATA.offset;\n" + js_function(APP_JS, "function cellScores(")
                  + "".join(js_function(APP_JS, f"function {n}(") for n in ("rkBaseOptions", "rkVersionsHtml", "rkReviewHtml"))
                  + js_function(APP_JS, "async function loadExplorer(") + """
const els = {};
const $ = (s) => (els[s] ||= { value: "", textContent: "", innerHTML: "", disabled: false, options: { length: 1 } });
$("#rk-cell").value = "ocean_cell";
const asked = [];
const api = async (u) => { asked.push(u); return DATA.reply; };
await loadExplorer();
const ranks = [...els["#rk-table"].innerHTML.matchAll(/<tr><td>(\\d+)<\\/td>/g)].map((m) => +m[1]);
console.log(JSON.stringify({ asked, ranks, page: els["#rk-page"].textContent, next: els["#rk-next"].disabled,
  prev: els["#rk-prev"].disabled }));""", {"reply": reply, "offset": offset})


def test_the_page_and_the_command_line_show_the_same_cell_page(tmp_path):
    p = _two_cells(tmp_path)
    c = TestClient(create_app(p))
    first = c.get("/api/rank", params={"cell": "ocean_cell", "offset": 0, "limit": 2}).json()
    out = _page_render(first, 0)
    assert "cell=ocean_cell" in out["asked"][0] and "offset=0" in out["asked"][0]
    assert out["page"] == "1–2 of 3" and not out["next"] and out["prev"]
    last = _page_render(c.get("/api/rank", params={"cell": "ocean_cell", "offset": 2, "limit": 2}).json(), 2)
    assert last["page"] == "3–3 of 3" and last["next"] and not last["prev"]   # the next page is the last
    r = runner.invoke(cli_app, ["rank", "show", "--cell", "ocean_cell", "--limit", "2", "--dir", str(p.root)],
                      terminal_width=200)
    assert r.exit_code == 0, r.output
    cli_ranks = [int(m) for m in re.findall(r"^\W*(\d+)\s+\W\s+\d\.\d{3}", r.output, re.M)]
    assert cli_ranks == out["ranks"] == [row["rank"] for row in first["rows"]] and len(cli_ranks) == 2
    assert "1–2 of 3 records best matching ocean_cell" in r.output


def test_a_refused_protocol_value_leaves_the_history_alone(tmp_path):
    p = Project.init(tmp_path / "r")
    yaml_before, n_before = (p.root / "project.yaml").read_text(encoding="utf-8"), len(journal.changes(p))
    r = runner.invoke(cli_app, ["protocol", "set", "ranking.top_n_compare", "[0, 2]", "--dir", str(p.root)])
    assert r.exit_code == 1
    assert len(journal.changes(p)) == n_before
    c = TestClient(create_app(p))
    d = c.post("/api/protocol/set", json={"path": "corpus.thin_words", "value": "x"})
    assert d.status_code == 400 and "was not changed" in d.json()["detail"]
    assert (p.root / "project.yaml").read_text(encoding="utf-8") == yaml_before and len(journal.changes(p)) == n_before
    # a value that fits is saved and journalled, so the counts above would have moved
    assert c.post("/api/protocol/set", json={"path": "corpus.thin_words", "value": 25}).status_code == 200
    assert len(journal.changes(p)) == n_before + 1


def test_the_cli_signs_with_wider_reviewer_then_user(tmp_path, monkeypatch):
    p = Project.init(tmp_path / "r")
    monkeypatch.setenv("USER", "os-login")
    monkeypatch.setenv("WIDER_REVIEWER", "env-reviewer")
    assert runner.invoke(cli_app, ["protocol", "set", "corpus.thin_words", "25", "--dir", str(p.root)]).exit_code == 0
    monkeypatch.delenv("WIDER_REVIEWER")
    assert runner.invoke(cli_app, ["protocol", "set", "corpus.thin_words", "30", "--dir", str(p.root)]).exit_code == 0
    assert [ch["author"] for ch in journal.changes(p)[:2]] == ["os-login", "env-reviewer"]


def test_two_saves_on_two_threads_get_their_own_versions(tmp_path, monkeypatch):
    from tests.unit.test_construct import SYN

    root = Project.init(tmp_path / "r").root
    gate, orig = threading.Barrier(2), artifacts.next_version

    def together(d, suffix):   # both saves read the next version before either writes its file
        v = orig(d, suffix)
        gate.wait(timeout=10)
        return v

    monkeypatch.setattr(artifacts, "next_version", together)
    rows, errors = [], []

    def save():
        try:
            rows.append(artifacts.save_new_version(Project.load(root), "synonyms", SYN, origin="import"))
        except Exception as e:   # noqa: BLE001 - the test reports any failure
            errors.append(repr(e))

    ts = [threading.Thread(target=save) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert errors == [] and sorted(r["version"] for r in rows) == [1, 2]
    p = Project.load(root)
    assert sorted(x.name for x in p.artifact_dir("synonyms").glob("v*.yaml")) == ["v1.yaml", "v2.yaml"]
    assert sorted(r["version"] for r in artifacts.list_versions(p, "synonyms")) == [1, 2]


def test_ctrl_or_cmd_z_undoes_unless_a_field_has_the_focus():
    hist = (STATIC / "history.js").read_text(encoding="utf-8")
    listener = js_function(hist, 'document.addEventListener("keydown", (e) => {') + ");"
    out = run_node(r"""
let handler = null;
const document = { activeElement: { tagName: "BODY" }, addEventListener(t, fn) { if (t === "keydown") handler = fn; } };
const undone = [];
const undo = (id) => { undone.push(id); };
let redone = 0; const redo = () => { redone += 1; };   // Ctrl/Cmd+Shift+Z (accessibility-and-naming 3.4)
""" + listener + r"""
const press = (mods, target) => {
  document.activeElement = target || { tagName: "BODY" };
  const e = { key: mods.key || "z", metaKey: !!mods.meta, ctrlKey: !!mods.ctrl, shiftKey: !!mods.shift, altKey: !!mods.alt,
              prevented: false, preventDefault() { this.prevented = true; } };
  const before = undone.length; handler(e); return [undone.length - before, e.prevented];
};
console.log(JSON.stringify({
  ctrl: press({ ctrl: true }), cmd: press({ meta: true }), caps: press({ ctrl: true, key: "Z" }),
  plain: press({}), redo: press({ meta: true, shift: true }), alt: press({ ctrl: true, alt: true }),
  input: press({ ctrl: true }, { tagName: "INPUT" }), area: press({ meta: true }, { tagName: "TEXTAREA" }),
  select: press({ ctrl: true }, { tagName: "SELECT" }), editable: press({ ctrl: true }, { tagName: "DIV", isContentEditable: true }),
  ids: undone, redone }));""")
    assert out["ctrl"] == out["cmd"] == out["caps"] == [1, True]   # the reviewer's own last change, by name
    assert out["ids"] == [None, None, None]
    assert out["redo"] == [0, True] and out["redone"] == 1   # Shift redoes instead of undoing
    for k in ("plain", "alt", "input", "area", "select", "editable"):   # a field keeps its own undo
        assert out[k] == [0, False], k


def _consent(close: str) -> dict:
    """Construct "Fetch missing abstracts" on a stand-in page: the 403 opens the consent dialog, which is closed by
    CLOSE (``esc``, ``allow`` or ``moved``: Cancel after the researcher moved the focus elsewhere)."""
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    handler = js[js.index('$("#bm-fetch").onclick'):js.index('$("#art-propose").onclick')]
    return run_node(r"""
const els = {}, observers = [];
let focusin = null, dialog = null, blocked = 0;
function mk(sel) {
  return { sel, isConnected: true, textContent: "", _dis: false,
    get disabled() { return this._dis; },
    set disabled(v) { this._dis = v; if (v && document.activeElement === this) document.activeElement = document.body;
                      observers.filter((o) => o.el === this).forEach((o) => o.fn()); },
    focus() { if (this._dis) return; document.activeElement = this; if (focusin && !this.inDialog) focusin({ target: this }); } };
}
global.MutationObserver = class { constructor(fn) { this.fn = fn; } observe(el) { this.el = el; observers.push(this); }
  disconnect() { const i = observers.indexOf(this); if (i >= 0) observers.splice(i, 1); } };
global.window = global; global.setTimeout = () => 0; global.clearTimeout = () => {};
global.document = { body: { sel: "body", appendChild() {} }, addEventListener(t, fn) { if (t === "focusin") focusin = fn; },
  createElement() {
    const handlers = {}, cancel = mk("cancel"), allow = mk("allow"); cancel.inDialog = allow.inDialog = true;
    dialog = { handlers, setAttribute() {}, addEventListener(t, fn) { handlers[t] = fn; }, showModal() {}, close() {},
      remove() { document.activeElement = document.body; }, querySelector: (s) => (s.includes("cancel") ? cancel : allow) };
    return dialog; } };
document.activeElement = document.body;
const sent = [];
global.fetch = async (path) => { sent.push(path);
  if (path === "/api/benchmark/fetch" && !blocked++) return { ok: false, status: 403, statusText: "Forbidden",
    json: async () => ({ detail: { consent_required: { services: ["OpenAlex"], fields: ["DOI"] }, message: "allow?" } }) };
  return { ok: true, status: 200, json: async () => ({ fetched: 0 }) }; };
""" + (STATIC / "common.js").read_text(encoding="utf-8") + r"""
const $ = (s) => (els[s] ||= mk(s));
const loadConstruct = async () => {};
""" + handler + r"""
const flush = () => new Promise((r) => setImmediate(r));
const btn = $("#bm-fetch"); btn.focus();
const run = btn.onclick(); await flush(); await flush();
const open = document.activeElement.sel;
const CLOSE = """ + f'"{close}"' + r""";
if (CLOSE === "esc") dialog.handlers.cancel({ preventDefault() {} });
else dialog.handlers.click({ target: { closest: () => ({ dataset: { act: CLOSE === "allow" ? "allow" : "cancel" } }) } });
if (CLOSE === "moved") $("#art-kind").focus();   // the researcher tabs on while the button is still disabled
await run; await flush(); await flush();
console.log(JSON.stringify({ open, focus: document.activeElement.sel, disabled: btn.disabled, sent,
  msg: $("#bm-msg").textContent }));""")


def test_the_consent_dialog_hands_the_focus_back_after_esc_or_allow():
    esc = _consent("esc")
    assert esc["open"] == "cancel" and esc["focus"] == "#bm-fetch" and not esc["disabled"]
    assert esc["sent"] == ["/api/benchmark/fetch"] and esc["msg"].startswith("Nothing was sent")   # Esc is Cancel
    allow = _consent("allow")
    assert allow["focus"] == "#bm-fetch" and not allow["disabled"]
    assert allow["sent"] == ["/api/benchmark/fetch", "/api/lookups/allow", "/api/benchmark/fetch"]
    moved = _consent("moved")   # a focus the researcher moved meanwhile is left where it is
    assert moved["focus"] == "#art-kind" and not moved["disabled"]


def test_a_note_without_a_name_points_at_change():
    # ui-redesign D33: no name, nothing posted; the drawer says WiderName.NEED with a Set your name button; once a first
    # name is set (no reload), the same typed note saves with Enter
    out = run_drawer(r"""
global.apiReply = () => ({ notes: [], tags: [] });
Drawer.open({ label: "Records", items: [{ id: 9 }], index: 0, recordId: (r) => r.id,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>` });
await flush();
const note = document.querySelector("#dw-note"); note.focus();
const enter = () => note.onkeydown({ key: "Enter", shiftKey: false, preventDefault() {}, target: { value: "check the sample" } });
await enter(); await flush();
const before = { posts: calls.filter(([, body]) => body).length, msg: document.querySelector("#dw-notes-msg").innerHTML };
global.apiReply = (url) => (url.includes("/notes") ? { change_id: 3 } : { notes: [], tags: [] });
WiderName.set("ana");
await enter(); await flush();
console.log(JSON.stringify({ before, need: WiderName.NEED, posts: calls.filter(([, body]) => body) }));""", opening="")
    assert out["before"]["posts"] == 0
    assert out["need"] in out["before"]["msg"] and "data-set-name>Set your name</button>" in out["before"]["msg"]
    assert out["posts"] == [["/api/records/9/notes", {"author": "ana", "text": "check the sample"}]]


def test_an_id_too_large_for_the_store_is_not_found_on_the_command_line(tmp_path):
    root = str(Project.init(tmp_path / "r").root)
    huge = "99999999999999999999999"
    for args in (["notes", "add", huge, "x", "--author", "ana"], ["notes", "untag", huge, "x", "--author", "ana"],
                 ["history", "undo", huge], ["questions", "dismiss", huge]):
        r = runner.invoke(cli_app, [*args, "--dir", root])
        assert r.exit_code == 1 and "no such id: the number is too large to be one" in r.output, (args, r.output)
        assert r.exception is None or isinstance(r.exception, SystemExit)
    c = TestClient(create_app(Project.load(tmp_path / "r")))
    assert c.get(f"/api/records/{huge}").json()["detail"] == "no such id: the number is too large to be one"


def test_the_focus_comes_back_to_a_control_the_page_re_rendered():
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    code = js_function(common, "function focusKey(") + "\n" + js_function(common, "function restoreFocus(")
    out = run_node("global.CSS = { escape: (s) => s };\n" + code + r"""
const doc = { body: { tag: "body" }, activeElement: null, els: {} };
doc.querySelector = (k) => doc.els[k] || null;
const el = (tag, attrs, o = {}) => ({ tagName: tag.toUpperCase(), id: attrs.id || "", isConnected: true, disabled: false,
  hidden: false, attributes: Object.entries(attrs).filter(([k]) => k !== "id").map(([name, value]) => ({ name, value })),
  getAttribute(n) { return attrs[n]; }, getClientRects() { return o.shown === false ? [] : [1]; }, closest() { return null; },
  focus() { doc.activeElement = this; }, ...o });
const res = {};
let save = el("button", { id: "cw-save" });
doc.activeElement = doc.body; res.same = restoreFocus(doc, save) === save;              // disabled, then enabled again
const twin = el("button", { id: "cw-save" }); save.isConnected = false; doc.els["#cw-save"] = twin;
doc.activeElement = doc.body; res.twin = restoreFocus(doc, save) === twin;              // the card re-rendered
const set = el("button", { "data-proto-set": "corpus.thin_words" }, { isConnected: false });
const setTwin = el("button", { "data-proto-set": "corpus.thin_words" }); doc.els['button[data-proto-set="corpus.thin_words"]'] = setTwin;
doc.activeElement = doc.body; res.data = restoreFocus(doc, set) === setTwin;            // a button with no id
doc.activeElement = doc.body; twin.disabled = true; res.disabled = restoreFocus(doc, save);
twin.disabled = false; twin.getClientRects = () => []; res.hidden = restoreFocus(doc, save);   // on a page no longer shown
const other = el("input", { id: "x" }); doc.activeElement = other; res.elsewhere = restoreFocus(doc, setTwin);
console.log(JSON.stringify(res));
""")
    assert out == {"same": True, "twin": True, "data": True, "disabled": None, "hidden": None, "elsewhere": None}
    assert 'if (typeof MutationObserver === "function" && document.documentElement) watchFocus();' in common
    assert 'window.addEventListener("hashchange", () => { lastControl = null; });' in common


def test_protocol_settings_and_the_ebsco_toggle_are_named():
    setup = (STATIC / "setup.js").read_text(encoding="utf-8")
    vi = js_function(setup, "function valueInput(")
    assert vi.count('aria-label="${esc(r.path)}"') == 4
    assert 'aria-label="Set ${esc(r.path)}">Set</button>' in setup
    assert 'aria-label="Reset ${esc(r.path)} to the default">Reset</button>' in setup
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="q-import-toggle" aria-expanded="false" aria-controls="q-import"' in html
    assert '$("#q-import-toggle").setAttribute("aria-expanded", String(!box.hidden))' in APP_JS


def test_ablation_leaves_the_chosen_ranking_alone(tmp_path):
    # an ablation once made its last ranking the default and the rank stage result, so sampling, the explorer and the
    # report moved to a ranking the samples were not drawn from (seventeenth round, bad-input tester)
    from wider.core.stage_result import last_result
    from wider.rank.ablate import ablate
    from wider.sample.design import draw_probability
    p = _two_cells(tmp_path)
    chosen = p.store.get_meta("default_ranking")
    before = last_result(p, "rank")
    draw_probability(p)
    p.config.protocol.ranking.top_n_compare = [1, 2]
    p.save()
    rows = ablate(p)
    assert {r["top_n"] for r in rows} == {1, 2}
    assert p.store.get_meta("default_ranking") == chosen
    assert last_result(p, "rank") == before
    r = runner.invoke(cli_app, ["rank", "show", "--dir", str(p.root)])
    assert r.exit_code == 0 and "1–12 of 12 records" in r.output
    assert TestClient(create_app(p)).get("/api/rank").json()["ranking"]["name"] == chosen


def test_every_comparison_above_the_cells_is_said_on_the_cli(tmp_path):
    p = _two_cells(tmp_path)
    p.config.protocol.ranking.top_n_compare = [1, 7, 99]
    p.save()
    r = runner.invoke(cli_app, ["rank", "validate", "--dir", str(p.root)])
    out = " ".join(r.output.split())
    assert r.exit_code == 0
    assert "top-7 is more than the 2 cells of these anchors, so it is compared as top-2" in out
    assert "top-99 is more than the 2 cells of these anchors, so it is compared as top-2" in out


def test_an_empty_cell_filter_says_so(tmp_path):
    p = _two_cells(tmp_path)
    p.store.execute("UPDATE ranking_rows SET best_cell='voice_cell'")
    r = runner.invoke(cli_app, ["rank", "show", "--cell", "ocean_cell", "--dir", str(p.root)])
    assert r.exit_code == 0 and "no record has ocean_cell as its best cell" in r.output and "none of 0" not in r.output
    assert "!d.n_rows ? (cell ? `no record has ${cell} as its best cell` : \"the ranking holds no records\")" in APP_JS


def test_a_long_name_is_cut_in_label_summaries(tmp_path):
    from wider.sample.design import draw_probability
    from wider.sample.labels import adjudicate, label
    p = _two_cells(tmp_path)
    s = draw_probability(p)
    rid = p.store.scalar("SELECT record_id FROM sample_rows WHERE sample_id=? LIMIT 1", (s["sample_id"],))
    long_name = "reviewer" + "y" * 97
    label(p, s["name"], rid, "other", "exclude")   # an adjudication needs a disagreement
    label(p, s["name"], rid, long_name, "include")
    adjudicate(p, s["name"], rid, "include", by=long_name)
    for ch in journal.changes(p)[:2]:
        assert long_name[:80] in ch["summary"] and long_name not in ch["summary"]


def test_long_names_wrap_in_toasts_and_the_history_table():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".toast span { flex:1; min-width:0; overflow-wrap:anywhere; }" in css
    assert "#hist-table td { overflow-wrap:anywhere; }" in css


def test_a_protocol_name_must_be_a_plain_word(tmp_path):
    import pytest

    from wider.screen.engine import check_protocol_name
    for ok in ("main", "main+human", "B2_second", "x" * 80):
        check_protocol_name(ok)
    for bad in ("<b>x</b>", "x/../../esc", "two words", "", "-lead", "x" * 81):
        with pytest.raises(ValueError, match="a protocol name is 1-80 letters"):
            check_protocol_name(bad)
    p = _two_cells(tmp_path)
    c = TestClient(create_app(p))
    for bad in ("<b>x</b>", "x/../../esc"):
        r = c.post("/api/jobs/screen", json={"scope": "corpus", "mode": "protocol", "protocol": bad})
        assert r.status_code == 400 and "a protocol name is 1-80 letters A-Z, digits, '-', '_' or '+', starting with a letter or digit, e.g. main or main+human" in r.json()["detail"]
    r = runner.invoke(cli_app, ["screen", "protocol", "corpus", "--name", "<b>x</b>", "--dir", str(p.root)])
    assert r.exit_code == 1 and "a protocol name is 1-80 letters A-Z, digits, '-', '_' or '+', starting with a letter or digit, e.g. main or main+human" in " ".join(r.output.split())
    assert not p.store.scalar("SELECT COUNT(*) FROM screen_decisions")


def test_nothing_to_ablate_is_said(tmp_path):
    root = str(Project.init(tmp_path / "blank").root)
    r = runner.invoke(cli_app, ["rank", "ablate", "--dir", root])
    assert r.exit_code == 1 and "nothing to ablate" in r.output and "Traceback" not in r.output
