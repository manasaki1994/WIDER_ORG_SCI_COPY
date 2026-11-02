"""The screening dialogs, the "Models used" card and the result-set selects run under Node (no browser): the
backend-change dialog names both backends and offers "Start a separate run" only in case (b); the models card lists
each model with a total row; a result-set select defaults to the active set, labels each set, keeps a choice made for
the same protocol, and for a second screen shows only the sets built from the chosen source."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

# a select stand-in for fillSelect: options are kept as {value, label}
PAGE = r"""
global.esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const els = {};
function sel() { return { value: "", dataset: {}, disabled: false, options: [] }; }
global.document = { querySelector: (s) => (els[s] ||= sel()) };
global.fillSelect = (el, opts, placeholder) => {
  el = typeof el === "string" ? document.querySelector(el) : el;
  el.options = opts.map(o => (typeof o === "string" ? { value: o, label: o } : o));
  el.value = el.options.length ? el.options[0].value : ""; el.disabled = !el.options.length; return el.options.length > 0;
};
"""


def run_node(script: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    start = common.index("function resultSetOptions(")
    stop = common.index("\n}\n", common.index("function resultSetSelect(")) + 3
    code = f"{PAGE}\n{common[start:stop]}\nconst Backends = require({json.dumps(str(STATIC / 'backends.js'))});\n"
    code += "(async () => {\n" + script + "\n})().catch(e => { console.error(e && e.stack || e); process.exit(1); });"
    # backends.js reads `esc` as a global when its functions run
    r = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


SETS = r"""
const sets = [
  { result_set: 1, active: false, label: "pilot", role: null, models: ["fake"], run_ids: ["r1"] },
  { result_set: 2, active: true, label: "", role: "second screen", models: ["fake", "fake-2"], run_ids: ["r2", "r3"],
    source_protocol: "sp", source_set: 1 },
  { result_set: 3, active: false, label: "<b>x</b>", models: [], run_ids: [], source_protocol: "sp", source_set: 2 },
];
"""


def test_select_defaults_to_active():
    out = run_node(SETS + r"""
const n = resultSetSelect("#s", "main", sets);
const el = document.querySelector("#s");
const first = el.value;
const follows = resultSetSelect("#s", "main", sets.map(s => ({ ...s, active: s.result_set === 1 })));
resultSetSelect("#s", "main", sets);              // not chosen: a reload follows the active set
el.value = "3"; el.dataset.chosen = "3";         // the researcher picks set 3 (the change listener records it)
const kept = resultSetSelect("#s", "main", sets); // a reload keeps it
const other = resultSetSelect("#s", "other", sets); // another protocol starts at its active set
const none = resultSetSelect("#t", "main", []);
console.log(JSON.stringify({ n, first, follows, kept, other, labels: el.options.map(o => o.label), none,
  activeUnchanged: sets.filter(s => s.active).map(s => s.result_set) }));
""")
    assert out["n"] == 2 and out["first"] == "2" and out["follows"] == 1 and out["kept"] == 3 and out["other"] == 2
    assert out["none"] is None and out["activeUnchanged"] == [2]
    assert out["labels"][0] == "other set 1 · pilot · fake · r1"
    assert out["labels"][1] == "other set 2 (active) · second screen · fake, fake-2 · r2, r3"


def test_select_second_screen_filters_by_source():
    out = run_node(SETS + r"""
const a = resultSetOptions("main", sets, { protocol: "sp", set: 1 }).map(o => o.value);
const b = resultSetOptions("main", sets, { protocol: "sp", set: 2 }).map(o => o.value);
const c = resultSetOptions("main", sets, { protocol: "other" }).map(o => o.value);
console.log(JSON.stringify({ a, b, c }));
""")
    # sets without a source always show; sets built from another source never do
    assert out == {"a": ["1", "2"], "b": ["1", "3"], "c": ["1"]}


def test_backend_change_dialog_text():
    out = run_node(r"""
const c = { run_id: "r<1>", case: "a", done: 3, total: 10, changed: ["model"],
  from: { provider: "fake", model: "m1" }, to: { provider: "fake", model: "m2" } };
const a = Backends.backendChangeHtml(c), b = Backends.backendChangeHtml({ ...c, case: "b" });
console.log(JSON.stringify({ a, b }));
""")
    a, b = out["a"], out["b"]
    assert "The model changed since this run started" in a and "3 of 10 records done" in a
    assert "fake m1" in a and "fake m2" in a and "model" in a and "r&lt;1&gt;" in a and "r<1>" not in a
    assert 'data-act="continue"' in a and 'data-act="cancel"' in a and 'data-act="new"' not in a
    assert 'data-act="new"' in b and "Start a separate run" in b


def test_new_set_dialog_text():
    out = run_node(r"""
const r = { protocol: "main", set: 3, in_scope: 40, to_screen: 15, changed: ["prompt"], same_as: null,
  reusable: { set: 1, count: 25 },
  estimate: { all: { priced: true, min_usd: 0.1, max_usd: 0.2, calls: 40 }, reuse: { priced: false, calls: 15 } } };
const cli = "main set 2 is finished under fake; your settings differ in: model. To screen again as a new result set, add --new-set (or --new-set --reuse-from 1 to reuse 25 decisions)";
console.log(JSON.stringify({ h: Backends.newSetHtml(r, "main set 2 is finished"), c: Backends.newSetHtml(r, cli),
  none: Backends.newSetHtml({ ...r, reusable: null }, "") }));
""")
    h = out["h"]
    assert "main set 3" in h and "Reuse 25 decisions from set 1" in h and "Screen all 40 records" in h
    assert "15 records to screen (15 calls, not priced)" in h and "$0.1000–$0.2000 for 40 calls" in h
    assert 'data-field="set_label"' in h and "main set 2 is finished" in h
    assert 'data-act="reuse"' not in out["none"] and 'data-act="all"' in out["none"]
    # the CLI flags of the refusal text are not shown in the dialog, whose buttons replace them
    assert "--new-set" not in out["c"] and "--reuse-from" not in out["c"]
    assert "main set 2 is finished under fake; your settings differ in: model." in out["c"]


def test_models_card_text():
    out = run_node(r"""
const s = { headline: "12 unique records, 14 model calls", mixed: true, note: "experimental backend",
  scope: { protocol: "main", result_set: 2, label: "<i>v2</i>", role_note: "used for analysis" },
  models: [{ model: "fake", records: 10, evaluations: 11, reused: 2, calls: 9, include: 3, maybe: 2, exclude: 6,
             prompt_tokens: 1000, completion_tokens: 50, cost: "$0.0100", final_records: 8 },
           { model: "fake-2", records: 3, evaluations: 3, reused: 0, calls: 5, include: 1, maybe: 0, exclude: 2,
             prompt_tokens: 300, completion_tokens: 20, cost: "not priced", final_records: 2 }],
  totals: { records: 12, evaluations: 14, calls: 14, include: 4, maybe: 2, exclude: 8, prompt_tokens: 1300,
            completion_tokens: 70, cost: "not priced" },
  origins: { screened: 10, reused: 2 } };
console.log(JSON.stringify({ h: Backends.modelsCardHtml(s) }));
""")
    h = out["h"]
    assert "12 unique records, 14 model calls" in h and "main set 2" in h and "&lt;i&gt;v2&lt;/i&gt;" in h
    assert 'class="chip mixed"' in h and "experimental backend" in h and "used for analysis" in h
    assert "<td>fake</td>" in h and "<td>fake-2</td>" in h and "11 (2 reused)" in h
    assert "<b>total</b>" in h and h.count("not priced") == 2 and "screened 10, reused 2" in h
