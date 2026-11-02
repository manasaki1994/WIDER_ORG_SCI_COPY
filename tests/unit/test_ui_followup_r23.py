"""Tests asked for by the twenty-third round of C1 checks: one report build per press, a refused protocol value does
not stay in its field and the keyboard stays on the row, a number too large to read is refused in plain words, and
the labelling queue asks nothing without a name and names itself as the labelling queue."""
import pytest

from tests.unit.test_drawer_keys_node import STATIC, run_node
from tests.unit.test_query_roles import js_function
from wider.core.protocol import ReviewProtocol

APP = (STATIC / "app.js").read_text(encoding="utf-8")
SETUP = (STATIC / "setup.js").read_text(encoding="utf-8")
RECORDS = (STATIC / "records.js").read_text(encoding="utf-8")


def test_a_number_too_large_to_read_is_refused_in_plain_words():
    with pytest.raises(ValueError) as e:
        ReviewProtocol().set("screening.max_tokens", 1e23)
    assert str(e.value) == "screening.max_tokens was not changed: the number is too large (got 1e+23)"


def test_build_report_twice_builds_once_and_keeps_the_keyboard():
    line = next(x for x in APP.splitlines() if x.startswith('$("#rp-build").onclick'))
    out = run_node(r"""
const doc = { activeElement: null }; global.document = doc;
const mk = (id) => ({ id, disabled: false, textContent: "", focus() { doc.activeElement = this; } });
const els = { "#rp-build": mk("build"), "#rp-msg": mk("msg"), "#rp-md": mk("md") };
const $ = (s) => els[s];
const calls = []; let release;
const api = (path) => { calls.push(path); return new Promise((r) => { release = r; }); };
const reportParams = () => "?protocol=main", showStageResult = () => {}, refreshStages = () => {}, showStageError = () => {};
doc.body = { id: "body" };
""" + line + r"""
(async () => {
  const first = els["#rp-build"].onclick(); els["#rp-build"].onclick();
  const during = [calls.length, els["#rp-build"].disabled];
  doc.activeElement = doc.body;   // a disabled button drops the keyboard
  release({ markdown: "# report", stage_result: {} }); await first;
  console.log(JSON.stringify({ during, after: els["#rp-build"].disabled, focus: doc.activeElement.id }));
})();
""")
    assert out == {"during": [1, True], "after": False, "focus": "build"}
    assert '$("#rp-bundle").onclick = async (ev, choice) => { const b = $("#rp-bundle"); if (b.disabled) return;' in APP


def test_a_refused_protocol_value_goes_back_and_the_keyboard_stays_on_the_row():
    assert "catch (e) { showStored(el); throw e; }" in SETUP
    assert 'await post("/api/protocol/reset", { path }); await loadProtocol(); focusSet(path);' in SETUP
    out = run_node(js_function(SETUP, "function showStored(") + js_function(SETUP, "function focusSet(") + r"""
let focused = null, asked = null;
const q = (s) => { asked = s; return { focus() { focused = s; } }; };
const field = { tagName: "INPUT", value: "-5", defaultValue: "2000" };
const pick = { tagName: "SELECT", value: "false", options: [{ value: "true", defaultSelected: true }, { value: "false", defaultSelected: false }] };
showStored(field); showStored(pick); focusSet("screening.max_tokens");
console.log(JSON.stringify({ field: field.value, pick: pick.value, focused }));
""")
    assert out == {"field": "2000", "pick": "true",
                   "focused": '#protocol-editor [data-proto-set="screening.max_tokens"]'}


def test_the_labelling_queue_asks_nothing_without_a_name():
    line = next(x for x in RECORDS.splitlines() if x.strip().startswith("const summary = "))
    out = run_node(r"""
const calls = []; const call = (p) => { calls.push(p); return Promise.resolve({ 1: {} }); };
""" + line + r"""
(async () => {
  const blindNoName = await summary("", true), open = await summary("", false), named = await summary("ana", true);
  console.log(JSON.stringify({ blindNoName, calls, named }));
})();
""")
    assert out["blindNoName"] == {}
    assert out["calls"] == ["/api/annotations/summary?author=&blind=false", "/api/annotations/summary?author=ana&blind=true"]
    assert 'label: "Labelling queue (blind)"' in RECORDS and "Screening queue (blind)" not in RECORDS
    # Open queue without a name (ui-redesign D33): nothing is requested and the one name dialog opens
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
    page = run_node(QUEUE_PAGE + queue_name() + RECORDS + r"""
await $("#lb-load").onclick(); await flush();
console.log(JSON.stringify({ calls, opened: opened.length, dialogs }));
""")
    assert page["calls"] == [] and page["opened"] == 0
    assert len(page["dialogs"]) == 1 and "Your name" in page["dialogs"][0] and "Cancel" in page["dialogs"][0]


# ---- spec-checker, 23rd round: the tests above left these parts of the scenarios unchecked

JOBS = (STATIC / "jobs.js").read_text(encoding="utf-8")
HISTORY = (STATIC / "history.js").read_text(encoding="utf-8")


def test_set_and_reset_everything_put_the_keyboard_back():
    set_handler = "\n".join(SETUP[SETUP.index('await post("/api/protocol/set"'):].split("\n")[:3])
    assert "await loadProtocol(); focusSet(path); flash(" in set_handler   # after a saved value, the row's Set button
    assert 'await post("/api/protocol/reset", {}); await loadProtocol(); q("#proto-reset-all").focus();' in SETUP


def test_a_danger_toast_is_an_alert_that_stays():
    out = run_node(r"""
const timers = []; global.setTimeout = (fn, ms) => { timers.push(ms); };
const made = [];
const el = () => { const e = { attrs: {}, removed: false, setAttribute(k, v) { this.attrs[k] = v; },
  querySelector: () => ({}), remove() { this.removed = true; } }; made.push(e); return e; };
global.document = { createElement: el };
const box = { appendChild() {} }; const q = () => box; const h = (s) => String(s);
let plain = [];   // the plain notices open now (jobs.js, beside toast)
const toastHeight = () => {};   // the scroll margin (accessibility-and-naming 3.5)
const said = []; const say = (t, tone) => said.push(tone === "danger" ? "alert" : "status");   // the live regions (fix 1 Nov 2026)
""" + js_function(JOBS, "function toast(") + r"""
const bad = toast("refused", "danger"); const badTimers = timers.length;
const ok = toast("saved", "success");
console.log(JSON.stringify({ bad: [said[0], bad.className, badTimers], ok: [said[1], timers] }));
""")
    assert out == {"bad": ["alert", "toast tone-danger", 0], "ok": ["status", [20000]]}
    # History's refusals use that tone: the missing name, a refused undo, a refused restore
    assert ('toast(`${what} needs your name: set it with Change in the top bar. Nothing was ${what === "Undo" ? "undone" '
            ': "redone"}.`, "danger", setName);') in HISTORY
    assert HISTORY.count('toast(e.message, "danger");') == 3   # undo, restore and redo (accessibility-and-naming 3.4)


def test_the_agreement_fields_are_named():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<select id="ag-sample" aria-label="Sample to compare">' in html
    assert 'id="ag-a" placeholder="reviewer A" aria-label="Reviewer A"' in html
    assert 'id="ag-b" placeholder="reviewer B" aria-label="Reviewer B"' in html


def test_the_name_request_turns_into_the_next_step_once_a_name_is_typed():
    # ui-redesign D33: a first name set from the name dialog fires "widername"; the signing line follows without a
    # reload, and Open queue then asks for that name's queue
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
    out = run_node(QUEUE_PAGE + queue_name() + RECORDS + r"""
apiReply = () => [];
const before = $("#lb-signing").innerHTML;
WiderName.set("ana"); const after = $("#lb-signing").innerHTML;
await $("#lb-load").onclick(); await flush();
console.log(JSON.stringify({ before, after, reloads: location.reloads, asked: calls.map((c) => c[0]) }));
""")
    assert out["before"].startswith("no name yet: labels need your name.") and "data-set-name" in out["before"]
    assert out["after"] == "Signing as <b>ana</b>." and out["reloads"] == 0
    assert out["asked"][0] == "/api/labels/queue?reviewer=ana"


def test_a_decision_in_the_same_second_as_the_run_is_counted(tmp_path):
    from tests.unit.test_records import _deduped
    from wider.dedup.engine import decide, decided_since_run, queue_rows
    from wider.dedup.engine import run as dedup_run
    p, _ = _deduped(tmp_path)
    dedup_run(p)
    q = queue_rows(p)[0]
    decide(p, q["raw_a"], q["raw_b"], "not_duplicate", by="ana")
    at = p.store.get_meta("dedup_run_at")   # the clock fixed: the decision carries the run's own second
    p.store.execute("UPDATE dedup_pairs SET decided_at=? WHERE raw_a=? AND raw_b=?", (at, q["raw_a"], q["raw_b"]))
    assert decided_since_run(p) == 1


def test_a_run_made_by_an_earlier_version_counts_by_time(tmp_path):
    from tests.unit.test_records import _deduped
    from wider.dedup.engine import decide, decided_since_run, queue_rows
    from wider.dedup.engine import run as dedup_run
    p, _ = _deduped(tmp_path)
    dedup_run(p)
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_manual'")   # earlier versions kept only the run's time
    p.store.set_meta("dedup_run_at", "2026-01-01T00:00:00+00:00")
    q = queue_rows(p)[0]
    decide(p, q["raw_a"], q["raw_b"], "not_duplicate", by="ana")
    pair = (q["raw_a"], q["raw_b"])
    p.store.execute("UPDATE dedup_pairs SET decided_at='2026-01-01T00:00:01+00:00' WHERE raw_a=? AND raw_b=?", pair)
    assert decided_since_run(p) == 1                     # a later time counts
    p.store.execute("UPDATE dedup_pairs SET decided_at='2026-01-01T00:00:00+00:00' WHERE raw_a=? AND raw_b=?", pair)
    assert decided_since_run(p) == 0                     # the same second cannot be told apart from the run
    dedup_run(p)                                         # the next run keeps the list, and the count is exact again
    assert p.store.get_meta("dedup_run_manual") is not None and decided_since_run(p) == 0
