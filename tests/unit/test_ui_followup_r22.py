"""Tests asked for by the twenty-second round of C1 checks: a second decision key pressed while the first one is
still saving keeps its "nothing more was decided" answer after the save."""
from tests.unit.test_drawer_keys_node import run_drawer


def test_a_second_key_during_the_save_keeps_its_answer():
    # keyboard-only tester: "already saved ..." was replaced about 20 ms later by "saved: include"
    out = run_drawer(r"""
let release = null; const saved = [];
Drawer.open({ label: "Labelling", items: [1, 2, 3, 4, 5].map((id) => ({ id })), index: 2, notes: false,
  render: async (r) => `<h2 id="dw-title">record ${r.id}</h2>`,
  decisions: ["include", "exclude"].map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1),
    fn: async (r) => { await new Promise((ok) => { release = ok; }); saved.push([r.id, value]); } })) });
await flush();
const m = () => document.querySelector("#dw-dec-msg").textContent;
listeners.keydown(key("1")); await flush();
listeners.keydown(key("2")); await flush();
const during = m();
release(); for (let i = 0; i < 4; i++) await flush();
const afterSave = m();
timers.splice(0).forEach(([fn]) => fn()); for (let i = 0; i < 4; i++) await flush();
listeners.keydown(key("1")); await flush();
const next = m(); release(); for (let i = 0; i < 4; i++) await flush();
console.log(JSON.stringify({ during, afterSave, pos: pos(), saved, next, last: m() }));""", opening="")
    already = "already saved, moving to the next record: nothing more was decided (1 key press ignored)"
    assert out["during"] == already and out["afterSave"] == already
    assert out["pos"] == "4 / 5" and out["saved"] == [[3, "include"], [4, "include"]]
    assert out["last"] == "saved: include"   # the next record's own decision says saved again


def test_removing_a_platform_keeps_the_keyboard_in_the_table():
    from tests.unit.test_drawer_keys_node import STATIC, run_node
    from tests.unit.test_query_roles import js_function
    setup = (STATIC / "setup.js").read_text(encoding="utf-8")
    assert "focusAfterRemove(q(\"#setup-databases\"), keys.filter(k => k !== b.dataset.platRemove), i);" in setup
    for name in ('aria-label="Move ${esc(c.label)} up"', 'aria-label="Move ${esc(c.label)} down"',
                 'aria-label="Remove ${esc(c.label)}"'):
        assert name in setup   # each row's buttons say which platform they act on
    out = run_node(js_function(setup, "function focusAfterRemove(") + r"""
let focused = null;
const el = (id, attrs) => ({ ...attrs, focus() { focused = id; } });
const box = (keys) => ({ querySelectorAll: () => keys.map((k) => el(k, { dataset: { platRemove: k } })),
                         querySelector: () => el("#plat-pick", {}) });
const res = {};
focusAfterRemove(box(["a", "c"]), ["a", "c"], 1); res.middle = focused;   // b removed: c took its place
focusAfterRemove(box(["a", "b"]), ["a", "b"], 2); res.last = focused;     // c removed: b is the new last row
focusAfterRemove(box([]), [], 0); res.none = focused;                      // nothing left: the catalogue picker
console.log(JSON.stringify(res));
""")
    assert out == {"middle": "c", "last": "b", "none": "#plat-pick"}


def test_history_says_why_a_change_cannot_be_undone_and_refusals_are_alerts():
    from tests.unit.test_drawer_keys_node import STATIC
    history = (STATIC / "history.js").read_text(encoding="utf-8")
    assert 'cannot undo${c.why_not ? `: ${h(c.why_not)}` : ""}' in history and 'title="${h(c.why_not' not in history
    assert '"warning"' not in history   # a refused undo or restore is an error: its toast is read out as an alert
    jobs = (STATIC / "jobs.js").read_text(encoding="utf-8")
    # toasts are read out through the page's lasting live regions (fixes of 1 Nov 2026): an error through the alert one
    assert 'q(tone === "danger" ? "#toast-alert" : "#toast-live")' in jobs
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ('id="ag-sample" aria-label=', 'id="ag-a" placeholder="reviewer A" aria-label=',
                 'id="ag-b" placeholder="reviewer B" aria-label='):
        assert name in html
