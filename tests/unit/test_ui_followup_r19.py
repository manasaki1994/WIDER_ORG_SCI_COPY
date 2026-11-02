"""Tests asked for by the nineteenth round of C1 checks: a protocol number field sends what was typed (so "x" or an
empty field is refused, never saved as 0), a moved platform keeps the keyboard on its row, and History's
"Undo my last change" signs with the one name WIDER works as (D33)."""
from tests.unit.test_drawer_keys_node import STATIC, run_node
from tests.unit.test_query_roles import js_function
from tests.unit.test_ui_followup_r11 import _undo_harness

SETUP = (STATIC / "setup.js").read_text(encoding="utf-8")
HISTORY = (STATIC / "history.js").read_text(encoding="utf-8")


def test_a_protocol_number_field_sends_what_was_typed():
    vi = js_function(SETUP, "function valueInput(")
    assert 'data-type="number" type="text" inputmode="decimal"' in vi
    assert ' type="number"' not in vi   # a number input turns "x" into "" and the old code sent "" as 0
    out = run_node(js_function(SETUP, "function parseValue(") + r"""
const f = (v) => parseValue({ value: v, dataset: { type: "number" } });
console.log(JSON.stringify({ x: f("x"), empty: f(""), blank: f("  "), twelve: f("12"), half: f("0.5"), neg: f("-3") }));
""")
    assert out == {"x": "x", "empty": "", "blank": "", "twelve": 12, "half": 0.5, "neg": -3}


def test_a_moved_platform_keeps_the_keyboard_on_its_row():
    assert 'focusMoved(q("#setup-databases"), b.dataset.move, b.dataset.dir);' in SETUP
    out = run_node(js_function(SETUP, "function focusMoved(") + r"""
let focused = null;
const btn = (move, dir, disabled) => ({ dataset: { move, dir }, disabled, focus() { focused = `${move}${dir}`; } });
const rows = (top) => [btn(top, "-1", true), btn(top, "1", false), btn("wos", "-1", false), btn("wos", "1", true)];
const box = (list) => ({ querySelectorAll: () => list });
const res = {};
focusMoved(box(rows("scopus")), "wos", "-1"); res.up = focused;          // moved up, Up still enabled
focusMoved(box(rows("scopus")), "scopus", "-1"); res.top = focused;      // reached the top: its Down button
focusMoved(box(rows("scopus")), "wos", "1"); res.bottom = focused;       // reached the bottom: its Up button
focused = null; focusMoved(null, "wos", "1"); res.none = focused;
console.log(JSON.stringify(res));
""")
    assert out == {"up": "wos-1", "top": "scopus1", "bottom": "wos-1", "none": None}


def test_history_undoes_as_the_name_wider_works_as():
    # one name (D33): History has no name field; "Undo my last change" signs with the name stored by the top bar
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="hist-name"' not in html and "#hist-name" not in HISTORY
    assert "const me = () => WiderName.get();" in HISTORY
    out = _undo_harness({"stored": "ana", "id": None, "reply": {"change_id": 2, "summary": "undo: x (change 1)"}})
    assert out["calls"] == [["/api/history/undo-last?author=ana", "POST"]] and out["asked"] == 0
