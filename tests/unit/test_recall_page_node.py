"""The Recall page's table runs under Node (no browser): the plain lines above the table as normal body text, the
interval column at the protocol's level, "could not be reliably estimated" when there is no interval, and no bootstrap
or linearised column (change recall-interval-honest, design D5)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")
ESC = r"""global.esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");"""


def run_js(fn: str, r: dict) -> str:
    """Run one of app.js's table functions on ``r``, with recallIntervalCell and outcomeActionsHtml, which they use."""
    if not NODE:
        pytest.skip("node is not installed")
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    parts = []
    for name in dict.fromkeys(("recallIntervalCell", "outcomeActionsHtml", fn)):
        start = src.index(f"function {name}(")
        parts.append(src[start:src.index("\n}\n", start) + 3])
    start = src.index("const RC_METHOD = ")  # the method names and rcLine, which the tables use
    parts.append(src[start:src.index(" : l);\n", start) + 7])
    code = f"{ESC}\n{''.join(parts)}\nprocess.stdout.write(JSON.stringify({fn}({json.dumps(r)})));"
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def recall_table_html(r: dict) -> str:
    return run_js("recallTableHtml", r)


def variant(recall, interval, n_rel, n_missed):
    return {"recall": recall, "interval": interval, "n_relevant": n_rel, "n_missed": n_missed,
            "relevant": ["include"], "kept": ["include", "maybe"], "ci_bootstrap": [1.0, 1.0], "ci_linearised": [1.0, 1.0]}


def test_recall_table_lines_and_columns():
    r = {"ci_level": 0.95, "lines": ["Based on 7 relevant validation papers.", "95% interval: 0.16 to 1.00.", "<b>x</b>"],
         "variants": {"strict": variant(0.6, None, 5, 2), "lenient": variant(1.0, [0.16178, 1.0], 7, 0),
                      "cautious": variant(None, None, 0, 0)}}
    html = recall_table_html(r)
    assert html.startswith('<div class="rc-answer"><p>Based on 7 relevant validation papers.</p><p>95% interval: 0.16 to 1.00.</p>')
    assert "<p>&lt;b&gt;x&lt;/b&gt;</p>" in html and 'class="small"' not in html
    assert "<th>95% interval (design-based)</th><th>relevant papers</th><th>missed</th>" in html
    assert "<td>0.16–1.00</td>" in html and "<td>could not be reliably estimated</td>" in html and "<td>–</td>" in html
    assert "bootstrap" not in html and "linearised" not in html
    assert "<th>90% interval (design-based)</th>" in recall_table_html({**r, "ci_level": 0.9})


def test_method_sentence_under_the_table():
    """The one method sentence (shared with the CLI and the report) stands under the variant table, small, escaped."""
    r = {"ci_level": 0.95, "lines": ["Based on 7 relevant validation papers."], "method_note": "The interval is approximate: a < b.",
         "variants": {"lenient": variant(1.0, [0.16178, 1.0], 7, 0)}}
    html = recall_table_html(r)
    assert html.endswith('</table><p class="small">The interval is approximate: a &lt; b.</p>')
    assert html.index("</table>") > html.index("Based on 7")


def test_answer_without_interval_key_shows_a_dash():
    """An answer recorded before this change (the demo's snapshot) has no ``interval`` key: the cell is "–", never its
    collapsed bootstrap interval."""
    old = {k: v for k, v in variant(1.0, None, 7, 0).items() if k != "interval"}
    html = recall_table_html({"ci_level": 0.95, "variants": {"lenient": old}})
    assert "<td>–</td>" in html and "1.00–1.00" not in html and "could not be reliably estimated" not in html


def test_compare_table_has_interval_columns():
    r = {"ci_level": 0.95, "result_set": 2,
         "variants": {"lenient": variant(1.0, [0.16178, 1.0], 7, 0)},
         "compare": {"result_set": 1, "variants": {"lenient": {"recall": 0.5, "n_missed": 3, "interval": None,
                                                               "interval_reason": "x"}}}}
    html = run_js("recallCompareHtml", r)
    assert "<th>95% interval, set 2</th><th>95% interval, set 1</th>" in html
    assert "<td>0.16–1.00</td><td>could not be reliably estimated</td>" in html


def test_used_choice_dialog():
    """D8: the dialog states the notice in normal text, offers Sample used, Not informed and Cancel, none pre-selected."""
    html = run_js("sampleChoiceHtml", "Set 2 was made after <recall> was seen.")
    assert "<p>Set 2 was made after &lt;recall&gt; was seen.</p>" in html and 'class="small"' not in html
    assert 'data-act="used">Sample used<' in html and 'data-act="not_informed">Not informed<' in html
    assert 'data-act="cancel">Cancel<' in html and "autofocus" not in html and "checked" not in html
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert 'dlg.querySelector("[data-act=cancel]").focus()' in common   # askChoice puts the focus on Cancel


def test_stop_and_continue_buttons():
    """D16: two ordinary buttons in the outcome block, neither pre-selected; the stop dialog asks a note (optional);
    stop sends sample and note, the other sends the suggested extension."""
    html = run_js("outcomeActionsHtml", {"outcome": {"outcome": "cannot_tell"}})
    assert 'data-rc-act="stop">Stop validation here<' in html and 'data-rc-act="extend">Add the suggested records<' in html
    assert "autofocus" not in html and "checked" not in html
    assert run_js("outcomeActionsHtml", {"outcome": None}) == ""
    dlg = run_js("stopDialogHtml", {})
    assert "Note (optional)" in dlg and 'data-field="note"' in dlg and 'data-act="cancel"' in dlg and 'class="small"' not in dlg
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    start = src.index("function outcomeRequest(")
    fn = src[start:src.index("\n}\n", start) + 3]
    code = (fn + 'process.stdout.write(JSON.stringify([outcomeRequest("stop", "validation", {note: "enough"}),'
            ' outcomeRequest("stop", "validation", {note: ""}), outcomeRequest("extend", "validation", null)]));')
    out = json.loads(subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60).stdout)
    assert out[0][0] == "/api/validation/stop" and json.loads(out[0][1]["body"]) == {"sample": "validation", "note": "enough"}
    assert json.loads(out[1][1]["body"]) == {"sample": "validation", "note": None}
    assert out[2][0] == "/api/validation/extend" and json.loads(out[2][1]["body"]) == {"sample": "validation", "suggested": True}


def test_outcome_block_lines():
    """D10: the outcome block goes to "Validation checks" with only the not-met line in the warning colour (never grey;
    an inconclusive line, and an old stored "Cannot tell" one, in black: recall-certification-workload D2), the
    benchmark lines to "Seed papers apart", and neither is repeated above the table."""
    cannot = ("Inconclusive for the target 0.95: at look 1, the exact bounds ..., contain it. This is not evidence that "
              "the target was missed; the labels so far cannot tell met from not met.")
    old = "Cannot tell whether the target 0.95 is met: at look 1, the exact bounds ..., contain it."
    notmet = "Target 0.95 not met: at look 2, the exact bounds ..., lie below it."
    met = "Target 0.95 met: at look 3, the exact bounds ..., lie at or above it."
    bench = "Development seed papers: 11 of 12 kept (10 include, 1 maybe, 1 excluded), 95% interval 0.62 to 1.00."
    r = {"ci_level": 0.95, "outcome": {"outcome": "cannot_tell"}, "lines": ["Based on 7.", cannot, notmet, met, bench],
         "validation_lines": [cannot, old, notmet, met], "benchmark_apart": {"lines": [bench]},
         "variants": {"lenient": variant(1.0, [0.16178, 1.0], 7, 0)}}
    v = run_js("validationSectionHtml", r)
    assert v.startswith("<h3>Validation checks</h3>")
    assert f"<p>{cannot}</p>" in v and f"<p>{old}</p>" in v and f'<p class="warn-line">{notmet}</p>' in v
    assert v.count("warn-line") == 1
    assert f"<p>{met}</p>" in v and "muted" not in v and "small" not in v
    b = run_js("benchmarkSectionHtml", r)
    assert b == f"<h3>Seed papers apart</h3><p>{bench}</p>"
    t = recall_table_html(r)
    assert "Based on 7." in t and cannot not in t and bench not in t
    assert run_js("validationSectionHtml", {"lines": []}) == "" and run_js("benchmarkSectionHtml", {}) == ""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert html.index('id="rc-tables"') < html.index('id="rc-validation"') < html.index('id="rc-benchmark"')


def test_answer_box_comes_first_and_the_main_row_is_marked():
    """Hand check M85 (24 Oct 2026): the reader could not tell where to look. The primary variant's recall heads a
    boxed answer above the tables, the three ways of counting are explained in words, and the main row is marked."""
    r = {"ci_level": 0.95, "primary_variant": "lenient", "lines": ["Based on 6 relevant validation papers."],
         "variants": {"strict": variant(0.333, [0.01, 0.91], 6, 4), "lenient": variant(0.364, [0.01, 0.92], 6, 3)}}
    html = recall_table_html(r)
    assert html.startswith('<div class="rc-answer"><p class="rc-figure">Recall (lenient): 36.4%</p><p>Based on 6')
    assert html.index("</div>") < html.index("<table>") and "<h3>Three ways of counting</h3>" in html
    assert "The lenient row is the main estimate" in html
    assert '<tr class="rc-main"><td>lenient (main)</td>' in html and "<td>strict (main)</td>" not in html
    # without a recall there is no figure, only the sentences saying why
    none = recall_table_html({**r, "variants": {"lenient": variant(None, None, 0, 0)}})
    assert 'class="rc-figure"' not in none
    compare = run_js("recallCompareHtml", {**r, "result_set": 1, "compare": {"result_set": 2, "variants": {}}})
    assert compare.startswith("<h3>Set 1 against set 2</h3>") and '<tr class="rc-main"><td>lenient</td>' in compare


def test_package_out_line_once(tmp_path):
    """reviewer-roles-and-blinding D11 under label-completion-and-sealing D4: while R3's validation package is out,
    GET /api/recall refuses, and the Recall page shows its notice, saying "R3's review package on validation is not
    finished" once, in the message line as normal text (not muted), with the tables cleared."""
    if not NODE:
        pytest.skip("node is not installed")
    from fastapi.testclient import TestClient

    from tests.unit.test_package import _codebook
    from tests.unit.test_validation_suggest import _drawn
    from wider.sample import package as pk
    from wider.ui.app import create_app
    p = _drawn(tmp_path)
    _codebook(p, "frozen")
    pk.make(p, "R3", ["validation"], "second")
    r = TestClient(create_app(p)).get("/api/recall?protocol=main&sample=validation&B=50").json()
    assert r["labels_incomplete"] is True
    code = _refused_src() + f"""
const mk = () => ({{ className: "muted small", textContent: "estimating…", innerHTML: "old" }});
const msg = mk(), panes = [mk(), mk(), mk()];
const a = recallRefused({json.dumps(r)}, msg, panes);
process.stdout.write(JSON.stringify({{ a, msg, panes: panes.map(x => x.innerHTML) }}));"""
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    res = json.loads(out.stdout)
    sentence = "R3&#39;s review package on validation is not finished"   # escaped: the line is HTML now
    assert res["a"] is True and res["msg"]["className"] == "" and res["panes"] == ["", "", ""]
    assert res["msg"]["innerHTML"].count(sentence) == 1


def test_impractical_line_black():
    """recall-certification-workload D5: the impracticality warning and the share line are ordinary black lines in
    "Validation checks", with no new control; both outcome buttons are offered, neither pre-selected."""
    from tests.unit.test_validation_suggest import WARN
    share = "That is 60% of the 4,000 records left."
    inc = ("Inconclusive for the target 0.90: at look 1, the exact bounds ..., contain it. This is not evidence that "
           "the target was missed; the labels so far cannot tell met from not met.")
    r = {"ci_level": 0.95, "outcome": {"outcome": "cannot_tell"}, "validation_lines": [inc, WARN, share],
         "variants": {}}
    v = run_js("validationSectionHtml", r)
    assert f"<p>{WARN}</p>" in v and f"<p>{share}</p>" in v and "warn-line" not in v
    assert v.count("<button") == 2 and "<input" not in v   # only the two outcome buttons, no new control
    html = run_js("outcomeActionsHtml", r)
    assert 'data-rc-act="stop"' in html and 'data-rc-act="extend"' in html and "checked" not in html and "autofocus" not in html


def test_conservative_line_black():
    """recall-certification-workload D3: the cost sentence is an ordinary black paragraph in "Validation checks"."""
    from wider.recall.validation import CONSERVATIVE
    r = {"ci_level": 0.95, "outcome": {"outcome": "cannot_tell"}, "validation_lines": [CONSERVATIVE], "variants": {}}
    v = run_js("validationSectionHtml", r)
    assert f"<p>{CONSERVATIVE}</p>" in v and "warn-line" not in v


def _refused_src() -> str:
    """recallRefused with what it calls: esc (common.js) and recallFixLinks (inspection 1 Nov 2026)."""
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    esc = next(line for line in common.splitlines() if line.startswith("const esc ="))
    out = [esc]
    for name in ("function recallRefused(", "function recallFixLinks("):
        start = src.index(name)
        out.append(src[start:src.index("\n}\n", start) + 3])
    return "\n".join(out)


def test_labels_incomplete_notice():
    """label-completion-and-sealing D4: the refusal goes to the message line as normal black text (the muted class
    is dropped) and the tables are cleared; any other answer keeps the muted line and the tables."""
    if not NODE:
        pytest.skip("node is not installed")
    code = _refused_src() + """
const mk = () => ({ className: "muted small", textContent: "estimating…", innerHTML: "old" });
const msg = mk(), panes = [mk(), mk(), mk()];
const a = recallRefused({ labels_incomplete: true, notice: "Recall on validation is revealed once ..." }, msg, panes);
const msg2 = mk(), panes2 = [mk()];
const b = recallRefused({ variants: {} }, msg2, panes2);
process.stdout.write(JSON.stringify({ a, msg, panes: panes.map(x => x.innerHTML), b, msg2, panes2: panes2.map(x => x.innerHTML) }));"""
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["a"] is True and r["msg"]["className"] == "" and r["msg"]["innerHTML"].startswith("Recall on validation")
    assert r["panes"] == ["", "", ""]
    assert r["b"] is False and r["msg2"]["className"] == "muted small" and r["panes2"] == ["old"]


def test_refusal_links_its_fixes():
    """Inspection 1 Nov 2026: a refused estimate links each fix on the Samples and labelling page."""
    if not NODE:
        pytest.skip("node is not installed")
    code = _refused_src() + """
const msg = { className: "", innerHTML: "" };
recallRefused({ labels_incomplete: true, notice: "Not yet <final>", sample: "validation",
  completeness: { n_not_independent: 3, n_open: 7 } }, msg, []);
process.stdout.write(JSON.stringify(msg));"""
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    h = json.loads(out.stdout)["innerHTML"]
    assert h.startswith("Not yet &lt;final&gt;")
    assert "data-goto-sample=\"[data-declare-independent='validation']\"" in h and "Declare labels independent" in h
    assert 'data-goto-sample="#ag-card"' in h and "Continue labelling" not in h


def test_interval_names_its_method():
    """The answer line and the table cell say which design-based interval method produced the bounds."""
    v = {**variant(1.0, [0.16178, 1.0], 7, 0), "interval_method": "korn_graubard"}
    r = {"ci_level": 0.95, "lines": ["95% interval: 0.16 to 1.00."], "primary_variant": "lenient", "variants": {"lenient": v}}
    html = recall_table_html(r)
    assert "<p>95% interval: 0.16 to 1.00 (design-based, Korn–Graubard method).</p>" in html
    assert "<td>0.16–1.00 (Korn–Graubard)</td>" in html
