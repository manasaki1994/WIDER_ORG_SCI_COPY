"""Tests asked for by the twentieth round of C1 checks: "Retry failed lookups" on the Enrich page opens the consent
dialog like Fetch does, sends nothing when it is cancelled, and starts one retry per press."""
from tests.unit.test_drawer_keys_node import STATIC, run_node

APP = (STATIC / "app.js").read_text(encoding="utf-8")


def test_retry_failed_lookups_goes_through_the_consent_dialog():
    assert '$("#en-retry").onclick = () => startEnrich(true);' in APP
    body = APP[APP.index("let enStarting = false;"):]
    body = body[:body.index("\n}\n") + 3]
    out = run_node(r"""
const els = { "#en-msg": { textContent: "" } };
const $ = (s) => els[s];
const enLimit = () => 100, enBatch = 100, enRecords = () => {}, renderEnrich = () => {}, showStageError = (k, m) => { els.err = m; };
const paths = []; let answer = "cancel", release;
// Jobs.start posts through api(): a 403 consent_required opens the dialog; Cancel throws the "Nothing was sent" error
const Jobs = { start: (path) => { paths.push(path);
  if (answer === "cancel") { const e = new Error("Nothing was sent: online lookups are not allowed for this project."); e.cancelled = true; return Promise.reject(e); }
  return new Promise((r) => { release = r; }); } };
""" + body + r"""
(async () => {
  const res = {};
  await startEnrich(true); res.cancelled = [paths.slice(), els["#en-msg"].textContent, els.err || null];
  answer = "allow"; paths.length = 0;
  const first = startEnrich(true); startEnrich(true); startEnrich(false);
  res.during = paths.slice(); release({}); await first;
  res.allowed = els["#en-msg"].textContent;
  console.log(JSON.stringify(res));
})();
""")
    assert out["cancelled"] == [["/api/jobs/enrich?limit=100&retry=true"],
                                "Nothing was sent: online lookups are not allowed for this project.", None]
    assert out["during"] == ["/api/jobs/enrich?limit=100&retry=true"]   # one retry per press, however many presses
    assert out["allowed"].startswith("Retrying failed and exhausted lookups in the background")


def test_a_second_decision_key_during_the_pause_decides_nothing():
    # keyboard-only tester: "1" pressed twice within 200 ms relabelled the record and skipped the next one
    from tests.unit.test_drawer_keys_node import run_drawer
    out = run_drawer(r"""
const res = {};
// j during the pause moves once: the pending advance is cancelled, not added to the move (3 -> 4, never 5)
listeners.keydown(key("1")); await flush();
listeners.keydown(key("j")); await flush();
timers.splice(0).forEach(([fn]) => fn()); await flush();
res.jDuring = [pos(), saved.slice()];
// a second decision key before the drawer moves on decides nothing; the drawer then moves on once
listeners.keydown(key("3")); await flush();
const second = key("2"); listeners.keydown(second); await flush();
res.double = [saved.slice(), second.prevented, document.querySelector("#dw-dec-msg").textContent];
timers.splice(0).forEach(([fn]) => fn()); await flush();
res.afterPause = pos();
// once the drawer has moved on, the next key decides again
listeners.keydown(key("1")); await flush();
res.next = saved.slice();
console.log(JSON.stringify(res));""")
    assert out["jDuring"] == ["4 / 5", [[3, "include"]]]
    assert out["double"] == [[[3, "include"], [4, "maybe"]], True,
                             "already saved, moving to the next record: nothing more was decided (1 key press ignored)"]
    assert out["afterPause"] == "5 / 5"
    assert out["next"] == [[3, "include"], [4, "maybe"], [5, "include"]]


def test_j_while_the_decision_is_saving_moves_once():
    # bad-input tester (21st round): "1" then "j" before the save had answered moved two records, skipping one unseen
    from tests.unit.test_drawer_keys_node import run_drawer
    out = run_drawer(r"""
let release = null; const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3, 4, 5].map((id) => ({ id })), index: 2, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: [{ value: "include", label: "Include",
    fn: async (r) => { await new Promise((ok) => { release = ok; }); saved.push(r.id); } }] });
await flush();
listeners.keydown(key("1")); await flush();
listeners.keydown(key("j")); await flush();
const during = pos();
release(); for (let i = 0; i < 4; i++) await flush();
timers.splice(0).forEach(([fn]) => fn()); for (let i = 0; i < 4; i++) await flush();
console.log(JSON.stringify({ during, after: pos(), saved, msg: document.querySelector("#dw-dec-msg").textContent }));""",
                     opening="")
    assert out["during"] == "4 / 5" and out["after"] == "4 / 5" and out["saved"] == [3]
    assert not out["msg"].startswith("saved")   # the new record is not marked as decided
