"""The Screening page's summary card and history run under Node on a small stand-in page (no browser): the steps with
their words, the next step's button, the "anyway" link to Recall, the round shown with its kind and test badges, the
unusual note in red, the run id kept in closed technical details, and the history's Show button selecting a round
(change screening-page-tasks, design D7). Synthetic data only."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")

PAGE = r"""
global.fmtWhen = (t) => String(t || "");
global.esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const els = {};
function mk(sel) {
  const e = { sel, innerHTML: "", textContent: "", open: false, value: "", listeners: {}, focused: false,
    addEventListener(t, fn) { this.listeners[t] = fn; }, querySelector(s) { return $(sel + " " + s); },
    scrollIntoView() {}, focus() { this.focused = true; } };
  return e;
}
const $ = (s) => els[s] || (els[s] = mk(s));
const usageChip = () => "", usageText = () => "12 calls";
const Backends = { mixedBadge: () => "", expBadge: () => "" };
global.location = { hash: "" };
"""

STEPS = [
    {"key": "development", "title": "Try the model on the development sample (optional)", "required": False,
     "state": "needs_attention", "word": "needs attention", "text": "Only the test model ran.", "page": "screen",
     "tab": "setup"},
    {"key": "prompt", "title": "Screening prompt", "required": True, "state": "completed", "word": "completed",
     "text": "A screening prompt is built.", "page": "screen", "tab": "setup"},
    {"key": "labels", "title": "Your labels on the validation sample", "required": True, "state": "not_started",
     "word": "not started", "text": "Label the 21 records.", "page": "sample", "tab": None},
    {"key": "corpus", "title": "Whole corpus screened by a protocol", "required": True, "state": "needs_attention",
     "word": "needs attention", "text": "Screened with the test model only.", "page": "screen", "tab": "setup"},
]
TEST_NOTE = "These decisions come from the test model: they are not a screening result."
RUNS = [
    {"run_id": "screen__sample-development__p1__fake__A", "name": "Development sample · pass A · prompt v1",
     "kind": "development", "kind_label": "development sample", "test": True, "status": "done", "n_items": 35,
     "ok": 35, "n": 35, "inc": 3, "may": 2, "exc": 30, "unusual": TEST_NOTE, "provider": "fake", "model": "fake",
     "prompt_version": 1, "usage": None},
    {"run_id": "screen__sample-validation__p1__fake__A", "name": "Validation sample · pass A · prompt v1",
     "kind": "validation", "kind_label": "validation sample", "test": True, "status": "done", "n_items": 21,
     "ok": 21, "n": 21, "inc": 0, "may": 0, "exc": 21, "unusual": TEST_NOTE + " Every one of the 21 records was excluded.",
     "provider": "fake", "model": "fake", "prompt_version": 1, "usage": None, "dir": "runs/x"},
    {"run_id": "old-build", "status": "done"},    # not a screening round (no kind): never listed
]
DATA = {"runs": RUNS, "progress": {"steps": STEPS, "required_done": False, "also": ["Try a real model first."],
                                   "next": {"page": "sample", "tab": None, "label": "Label the validation sample",
                                            "text": "Label the 21 records.", "step": "labels"}}}


def run_page(body: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    code = src[src.index("let scData = null"):src.index("async function loadScreen()")]
    script = f"{PAGE}\n{code}\nconst d = {json.dumps(DATA)};\n{body}"
    out = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_summary_after_a_test_round():
    out = run_page("renderScreenOverview(d); console.log(JSON.stringify({html: $('#sc-overview').innerHTML}));")
    html = out["html"]
    assert html.startswith('<h2 id="sc-overview-h">Where screening stands</h2>')
    words = [w for w in ("completed", "not started", "needs attention") if f'<span class="sc-step-word">{w}</span>' in html]
    assert words == ["completed", "not started", "needs attention"]
    assert 'class="sc-step sc-st-needs_attention sc-optional"' in html
    assert 'class="sc-step sc-st-needs_attention"' in html
    # the next step goes to the open required step's page, and Recall stays reachable (inform, not control)
    assert 'class="sc-next-btn main-act" data-sc-go="sample" data-sc-tab="">Label the validation sample</button>' in html
    assert '<a href="#recall" class="quiet-link" data-sc-go="recall">Go to Recall and report anyway</a>' in html
    assert "You may also: Try a real model first." in html
    # the newest round is shown first, by name, with its kind and the test-model badge; old builds are not rounds
    sel = html[html.index('<select id="sc-round-select"'):html.index("</select>")]
    assert sel.index("Latest: Validation sample") < sel.index("Development sample") and "old-build" not in sel
    body = html[html.index('id="sc-round-body"'):]
    assert "<b>Validation sample · pass A · prompt v1</b>" in body
    assert '<span class="chip sc-kind sc-kind-validation">validation sample</span>' in body
    assert '<span class="chip sc-kind sc-kind-test">test model</span>' in body
    assert '<p class="warn-text sc-unusual" role="note">These decisions come from the test model' in body
    assert "Every one of the 21 records was excluded." in body
    assert "<dt>Records screened</dt><dd>21 of 21</dd>" in body and "<dt>Failed</dt><dd>0</dd>" in body
    assert "<dt>Include · maybe · exclude</dt><dd>0 · 0 · 21</dd>" in body
    # only a required step that needs attention is a red box; the optional development step has the calm class
    assert 'class="sc-step sc-st-completed sc-optional"' not in html and 'class="sc-step sc-st-needs_attention"' in html
    # the run id and the folder only in the closed technical details
    tech = body[body.index('<details class="sc-tech">'):]
    assert "<details class=\"sc-tech\" open" not in body
    assert "<code>screen__sample-validation__p1__fake__A</code>" in tech
    assert body.index("screen__sample-validation") > body.index("<summary>Technical details</summary>")


def test_history_show_selects_the_round():
    body = r"""
renderScreenOverview(d); scData = d; renderScreenHistory(d);
const runs = $('#sc-runs').innerHTML, count = $('#sc-history-count').textContent, open = $('#sc-rounds-card').open;
const btn = { dataset: { scShow: "screen__sample-development__p1__fake__A" } };
$('#sc-runs').listeners.click({ target: { closest: () => btn } });
const html = $('#sc-overview').innerHTML;
console.log(JSON.stringify({ runs, count, open, html, focused: $('#sc-round-select').focused }));
"""
    out = run_page(body)
    assert out["count"] == "(2 rounds)" and out["open"] is True       # five rounds or fewer: open
    runs = out["runs"]
    assert runs.index("Validation sample") < runs.index("Development sample") and "old-build" not in runs
    assert 'data-sc-show="screen__sample-development__p1__fake__A" aria-label="Show Development sample' in runs
    assert "<td>completed</td>" in runs and "<td>21 of 21</td>" in runs
    html = out["html"]
    assert '<option value="screen__sample-development__p1__fake__A" selected>' in html
    assert "<b>Development sample · pass A · prompt v1</b>" in html[html.index('id="sc-round-body"'):]
    assert out["focused"] is True


def test_screen_page_has_no_stage_result_card():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    page = html[html.index('<section id="page-screen"'):]
    page = page[:page.index("</section>")]
    assert 'id="sc-overview"' in page and 'data-result="screen"' not in page and 'class="run-select' not in page


def test_next_step_button_opens_its_tab_and_the_anyway_link_goes_to_recall():
    body = r"""
renderScreenOverview(d);
const picked = []; $('#page-screen .page-tabs').pick = (tab, focus) => picked.push([tab, focus]);
const click = (go, tab) => { const b = { dataset: { scGo: go, scTab: tab } }; let prevented = false;
  $('#sc-overview').listeners.click({ target: { closest: () => b }, preventDefault() { prevented = true; } }); return prevented; };
const p1 = click("screen", "quality"), p2 = click("screen", ""), hashBefore = location.hash, p3 = click("recall", "");
console.log(JSON.stringify({ picked, hashBefore, hash: location.hash, prevented: [p1, p2, p3] }));
"""
    out = run_page(body)
    assert out["picked"] == [["quality", True], ["setup", True]]     # a screen step opens its tab (setup by default)
    assert out["hashBefore"] == "" and out["hash"] == "recall" and out["prevented"] == [True, True, True]


def scDevGate_src() -> str:
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    i = app.index("function scDevGateHtml(")
    return app[i:app.index("\n}\n", i)]


def test_dev_gate_message():
    """Scenario "Labels still open" on the page: a run refused with 409 dev_incomplete shows the server's sentence in
    normal text with "Continue labelling" as the main button to Samples and labelling, and starts nothing; the page's
    start code sends the refusal there (static check of startScreen)."""
    sentence = "the development sample has 79 records without a final label; label them all first"
    out = run_page(f"console.log(JSON.stringify({{ h: scDevGateHtml({json.dumps(sentence)}) }}));")
    h = out["h"]
    assert f"<p>{sentence}</p>" in h and 'role="alert"' in h
    assert '<a class="btn primary" href="#sample" data-sc-devgate data-goto-queue>Continue labelling</a>' in h
    assert "small" not in h and "muted" not in h
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    start = src[src.index("async function startScreen"):src.index('$("#sc-run").onclick')]
    assert "rb.dev_incomplete" in start and "refuse(scDevGateHtml(rb.detail))" in start
    # the box beside Run exists on the page (a missing box left the refusal unseen: ui-tester finding, 29 Oct)
    assert 'refusal = $("#sc-refusal")' in start and "showStageError" not in start
    # focus goes to Continue labelling, which opens the labelling queue with the focus on Open queue
    assert 'querySelector("[data-goto-queue]")' in start and "data-goto-queue" in scDevGate_src()
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    nav = app[app.index('closest("[data-goto-queue]")'):][:600]
    assert '$("#lb-card")' in nav and '$("#lb-load")' in nav and 'location.hash = "sample"' in nav
    assert 'id="lb-card"' in (STATIC / "index.html").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    run = html[html.index('id="sc-run"'):]
    assert run.index('id="sc-refusal"') < run.index("</div>\n      </div>") + 40


def test_dev_gate_refusal_runs_through_start_screen():
    """Scenario "Labels still open", the page half run under Node (spec-checker round 3, F1): startScreen gets a 409
    with dev_incomplete from the run request; the refusal box beside Run shows the server's sentence with Continue
    labelling, the focus moves to Continue labelling, "Nothing started." is said, no choice dialog opens and Run is
    enabled again."""
    if not NODE:
        pytest.skip("node is not installed")
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    start = src[src.index("async function startScreen"):src.index('$("#sc-run").onclick')]
    sentence = "the development sample has 6 records without a final label; label them all first"
    body = {"detail": sentence, "dev_incomplete": {"samples": [{"sample": "development", "missing": 6,
                                                                "packages_out": []}], "missing": 6}}
    script = f"""{PAGE}
let scPicked = true; const asked = [];
const askChoice = async (h) => {{ asked.push(h); return null; }};
const loadScreen = async () => {{}}, focusAfter = () => {{}};
const Jobs = {{ start: async () => {{ const e = new Error({json.dumps(sentence)}); e.status = 409;
  e.body = {json.dumps(body)}; throw e; }} }};
{scDevGate_src()}
}}
{start}
startScreen({{ mode: "pass", scope: "sample:development" }}).then(() => console.log(JSON.stringify({{
  box: $("#sc-refusal").innerHTML, hidden: $("#sc-refusal").hidden, msg: $("#sc-msg").textContent,
  focused: $("#sc-refusal [data-goto-queue]").focused, asked, disabled: $("#sc-run").disabled }})));
"""
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert f"<p>{sentence}</p>" in out["box"] and ">Continue labelling</a>" in out["box"]
    assert out["hidden"] is False and out["focused"] is True
    assert out["msg"] == "Nothing started." and out["asked"] == [] and out["disabled"] is False


def test_dev_gate_refusal_cleared_on_reopen():
    """Scenarios "Refusal cleared after labelling" and "Refusal shows the current count" (demo-chapters-and-video
    D10): after a run refused for 6 open development records, opening the Screening page again (loadScreen) clears
    the refusal box and its "Nothing started."; another refusal's text and an unrelated message stay; the next Run's
    refusal shows the server's current count (2), drawn fresh by startScreen."""
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    load = src[src.index("async function loadScreen()"):]
    i = load.index("scData = d; renderScreenOverview(d); renderScreenHistory(d);")
    clear = load[i:load.index("\n  $(\"#sc-protocols\")", i)].split("\n", 1)[1]
    def run(box_html, msg):
        script = f"""{PAGE}
$("#sc-refusal").innerHTML = {json.dumps(box_html)}; $("#sc-refusal").hidden = false;
$("#sc-refusal").querySelector = (s) => (s === ".sc-devgate" && $("#sc-refusal").innerHTML.includes("sc-devgate")) ? {{}} : null;
$("#sc-msg").textContent = {json.dumps(msg)};
{clear}
console.log(JSON.stringify({{ box: $("#sc-refusal").innerHTML, hidden: $("#sc-refusal").hidden, msg: $("#sc-msg").textContent }}));
"""
        res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)
    if not NODE:
        pytest.skip("node is not installed")
    gate = '<div class="stage-error sc-devgate" role="alert"><p>the development sample has 6 records ...</p></div>'
    out = run(gate, "Nothing started.")
    assert out == {"box": "", "hidden": True, "msg": ""}
    other = '<div class="stage-error" role="alert">the server said no</div>'
    out = run(other, "Running in the background")
    assert out["box"] == other and out["hidden"] is False and out["msg"] == "Running in the background"
    start = src[src.index("async function startScreen"):src.index('$("#sc-run").onclick')]
    sentence = "the development sample has 2 records without a final label; label them all first"
    body = {"detail": sentence, "dev_incomplete": {"missing": 2}}
    script = f"""{PAGE}
let scPicked = true; const askChoice = async () => null;
const loadScreen = async () => {{}}, focusAfter = () => {{}};
const Jobs = {{ start: async () => {{ const e = new Error("x"); e.status = 409; e.body = {json.dumps(body)}; throw e; }} }};
{scDevGate_src()}
}}
{start}
$("#sc-refusal").innerHTML = {json.dumps(gate)};
startScreen({{ mode: "pass", scope: "sample:development" }}).then(() => console.log(JSON.stringify({{ box: $("#sc-refusal").innerHTML }})));
"""
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    box = json.loads(res.stdout)["box"]
    assert "has 2 records" in box and "6 records" not in box
