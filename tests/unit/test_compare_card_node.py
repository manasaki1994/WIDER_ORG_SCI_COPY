"""The "Compare configurations" card (wider/ui/static/compare.js) under Node, no browser (change
screening-config-comparison, D6, D7, D16): the estimate before Run is enabled, rows in the order given with no winner
mark, the not-the-corpus sentence once in normal text, the unlocked-sample refusal word for word as the engine beside a
disabled Run, and a server refusal shown as is. Results come from the real engine on the synthetic project of
test_screen_compare.py."""
import json
import re

from tests.unit.test_backends_js_node import ESC, STATIC, run_node
from tests.unit.test_screen_compare import (  # noqa: F401  (fixtures: the synthetic project, built once)
    BETA_DEV,
    _template,
    api,
    cproject,
    locked,
    per_backend,
    use_responder,
)
from wider.screen import compare

GREY = re.compile(r'class="[^"]*\b(muted|small)\b')


def card(body: str) -> dict:
    """compare.js with the real escaping, a controller on three plain parts and a fake api that records requests."""
    return run_node(f"""{ESC}
{(STATIC / 'compare.js').read_text(encoding='utf-8')}
const parts = {{ form: {{ innerHTML: "" }}, gate: {{ innerHTML: "" }}, results: {{ innerHTML: "" }} }};
const sent = [];
let samples = {{ development: {{ kind: "purposive", n: 7, locked: true }}, validation: {{ kind: "probability", n: 12, locked: false }} }};
let replies = {{}};
const fakeApi = async (url, opts = {{}}) => {{
  sent.push({{ url, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null }});
  if (replies[url] instanceof Error) throw replies[url];
  if (url === "/api/samples") return {{ samples }};
  if (url === "/api/providers") return {{ providers: [{{ name: "fake", model: "fake-m1", locality: "test" }}, {{ name: "fake_b", model: "fake-m2", locality: "test" }}], default: "fake", stage_providers: {{}} }};
  if (url === "/api/screen/compare") return replies[url] || {{ comparisons: [] }};
  if (url in replies) return replies[url];
  throw new Error("no reply for " + url);
}};
let jobError = null;
let jobDone = null;
const startJob = async (path, opts, done) => {{ jobDone = done; sent.push({{ url: path, method: "POST", body: JSON.parse(opts.body) }}); if (jobError) throw jobError; return {{ job_id: "j1" }}; }};
const ctl = Compare.controller(parts, {{ api: fakeApi, startJob }});
const runButton = () => (parts.gate.innerHTML.match(/<button[^>]*data-cmp-act="run"[^>]*>/) || [""])[0];
{body}
""")


ESTIMATE = {"name": "cmp1", "records": 7, "runs": 6, "new_calls": 42, "locked": True,
            "lines": ["Comparison cmp1: 7 records, 2 configurations × 3 repeats = 6 runs, 42 new calls",
                      "backend fake (test):", "  no charge"]}


def test_strings_are_the_engines():
    """The card's sentences are the engine's constants, so a drift between compare.py and compare.js fails here."""
    out = card("process.stdout.write(JSON.stringify({ nl: Compare.NOT_LOCKED, nc: Compare.NOT_CORPUS, f: Compare.notLocked('development') }));")
    assert out["nl"] == compare.NOT_LOCKED and out["nc"] == compare.NOT_CORPUS
    assert out["f"] == compare.NOT_LOCKED.format(name="development")


def test_estimate_shown_before_run_is_enabled():
    """Run is disabled until an estimate for exactly the form's choices is shown; changing a choice disables it
    again until the next estimate. The estimate posts the body the job would get."""
    out = card(f"""
replies["/api/screen/compare/estimate"] = {json.dumps(ESTIMATE)};
await ctl.load({{ prompts: [{{ version: 1, status: "active" }}] }});
const before = {{ run: runButton(), gate: parts.gate.innerHTML }};
ctl.addRow(); ctl.setRow(1, "provider", "fake_b"); ctl.setRow(1, "temperature", "0.7");
await ctl.estimate();
const after = {{ run: runButton(), gate: parts.gate.innerHTML }};
ctl.set("repeats", "2");
const changed = {{ run: runButton(), gate: parts.gate.innerHTML }};
await ctl.estimate();
ctl.run();
await new Promise(r => setTimeout(r, 0));
process.stdout.write(JSON.stringify({{ before, after, changed, sent, form: parts.form.innerHTML }}));
""")
    assert "disabled" in out["before"]["run"] and "Estimate first" in out["before"]["gate"]
    assert "42 new calls" not in out["before"]["gate"]
    assert "disabled" not in out["after"]["run"] and "Run comparison (42 new calls)</button>" in out["after"]["gate"]
    assert "<p>Comparison cmp1: 7 records, 2 configurations × 3 repeats = 6 runs, 42 new calls</p>" in out["after"]["gate"]
    assert "disabled" in out["changed"]["run"] and "estimate again" in out["changed"]["gate"]
    est = [s for s in out["sent"] if s["url"] == "/api/screen/compare/estimate"]
    assert est[0]["body"] == {"name": "cmp1", "sample": "development", "configs": ["fake", "fake_b@0.7"], "repeats": 3,
                              "reuse_evaluations": False}
    job = [s for s in out["sent"] if s["url"] == "/api/jobs/compare"]
    assert len(job) == 1 and job[0]["body"] == {**est[1]["body"], "repeats": 2} == est[1]["body"]
    # the reuse tick box sits beside its text, and only purposive samples are offered
    assert re.search(r'<label><input type="checkbox" data-cmp-field="reuse"> Reuse identical evaluations', out["form"])
    assert "validation" not in out["form"] and "development (7 records, locked)" in out["form"]


def test_card_refuses_unlocked_sample():
    """Scenario "Unlocked sample refused" (D7): an unlocked sample shows the engine's sentence word for word, not in a
    grey class, beside a disabled Run, as soon as it is selected; the estimate stays available and Run stays
    disabled after it; nothing is started."""
    out = card(f"""
samples = {{ development: {{ kind: "purposive", n: 7, locked: false }} }};
replies["/api/screen/compare/estimate"] = {json.dumps({**ESTIMATE, "locked": False})};
await ctl.load();
const first = {{ run: runButton(), gate: parts.gate.innerHTML }};
await ctl.estimate();
await ctl.run();
process.stdout.write(JSON.stringify({{ first, run: runButton(), gate: parts.gate.innerHTML, sent }}));
""")
    sentence = compare.NOT_LOCKED.format(name="development")
    span = re.search(r"<span data-cmp-refusal[^>]*>([^<]*)</span>", out["first"]["gate"])
    assert span and span.group(1) == sentence and not GREY.search(span.group(0))
    assert "disabled" in out["first"]["run"]
    assert "disabled" in out["run"] and sentence in out["gate"] and "42 new calls</p>" in out["gate"]
    assert [s["url"] for s in out["sent"] if s["method"] == "POST"] == ["/api/screen/compare/estimate"]


def test_locked_sample_enables_run_and_server_message_shown_as_is():
    """A locked sample enables Run after the estimate; a 409 from the job route is shown word for word."""
    msg = "comparison cmp1 exists with other configurations (fake); choose another name"
    out = card(f"""
replies["/api/screen/compare/estimate"] = {json.dumps(ESTIMATE)};
jobError = Object.assign(new Error({json.dumps(msg)}), {{ status: 409 }});
await ctl.load();
await ctl.estimate();
const run = runButton();
await ctl.run();
process.stdout.write(JSON.stringify({{ run, gate: parts.gate.innerHTML }}));
""")
    assert "disabled" not in out["run"] and "data-cmp-refusal" not in out["gate"]
    assert re.search(r'<p data-cmp-msg role="status" class="bad">' + re.escape(msg) + "</p>", out["gate"])


def test_empty_list_says_no_comparison_yet():
    """Task 3.4: with no comparison (the demo answers an empty list) the card says so and shows no error."""
    out = card("await ctl.load(); process.stdout.write(JSON.stringify({ r: parts.results.innerHTML, g: parts.gate.innerHTML }));")
    assert out["r"] == "<p data-cmp-none>No comparison yet.</p>" and 'class="bad"' not in out["g"]


def test_results_in_given_order_without_winner(locked, monkeypatch):  # noqa: F811
    """Scenarios "Two configurations compared" and "One record changes between repeats" on the card: real results
    from the engine, rows in the order given (fake_b before fake), no winner mark, the not-the-corpus sentence once in
    normal text, the changed record listed with its decisions, and the pool and area breakdowns."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(flips={"fake": {BETA_DEV: {0: "include", 1: "exclude"}}}))
    compare.run(p, "cmp1", None, ["fake_b", "fake"], repeats=2)
    res = api(p).get("/api/screen/compare/cmp1").json()
    out = card(f"""
const res = {json.dumps(res)};
process.stdout.write(JSON.stringify({{ none: Compare.resultsHtml(res, ""), pool: Compare.resultsHtml(res, "pool"), area: Compare.resultsHtml(res, "area") }}));
""")
    html = out["none"]
    rows = re.findall(r'<tr data-cmp-config="([^"]+)">', html)
    assert rows == ["fake_b", "fake"]
    assert html.count(compare.NOT_CORPUS) == 1
    assert re.search(r"<p>" + re.escape(compare.NOT_CORPUS) + "</p>", html)
    assert not re.search(r"best|winner|recommended|★|chosen", html, re.I)
    fake_row = html[html.index('<tr data-cmp-config="fake">'):]
    cons = res["configs"][1]["consistency"]["text"]
    assert cons in fake_row.split("</tr>")[0]
    assert re.search(r'<ul data-cmp-changed="fake"><li>record 5 Record 04 on topic beta[^:]*: include, exclude</li></ul>', html)
    assert "data-cmp-pool" in out["pool"] and "data-cmp-area" not in out["pool"]
    areas = res["areas_message"]
    if areas:
        assert f"<p data-cmp-areas-msg role=\"status\">{areas}</p>" in out["area"] and "data-cmp-pool" in out["area"]
    else:
        assert "data-cmp-area" in out["area"]
    for c in res["configs"]:
        for b in c["by_pool"]:
            assert f"<td>{c['label']}</td><td>{b['key']}</td><td>{b['n']}</td><td>{b['misses_text']}</td>" in out["pool"]


def test_running_line_cleared_when_the_job_ends():
    """The "Running in the background" line under Run goes once the comparison job has finished (ui-tester F3)."""
    out = card(f"""
replies["/api/screen/compare/estimate"] = {json.dumps(ESTIMATE)};
await ctl.load({{ prompts: [{{ version: 1, status: "active" }}] }});
await ctl.estimate();
await ctl.run();
const running = parts.gate.innerHTML;
await jobDone();
process.stdout.write(JSON.stringify({{ running, done: parts.gate.innerHTML }}));
""")
    assert "Running in the background" in out["running"]
    assert "Running in the background" not in out["done"]


def test_hidden_results_show_settings_without_figures():
    """ui-redesign D4 (3.3): while the development sample is incomplete the card shows the hidden sentence in normal
    text and each configuration's settings, and no figure table."""
    from wider.screen.devgate import HIDDEN_SOME

    res = {"name": "cmp1", "sample": "development", "repeats": 2, "labelled_text": "7 of 7 records labelled",
           "hidden": HIDDEN_SOME, "notes": [HIDDEN_SOME],
           "configs": [{"label": "fake", "provider": "fake", "model": "fake-m1", "prompt_version": 3,
                        "temperature_text": "0"}]}
    html = card(f"process.stdout.write(JSON.stringify({{ h: Compare.resultsHtml({json.dumps(res)}, '') }}));")["h"]
    assert html.count(HIDDEN_SOME) == 1 and f'<p data-cmp-hidden role="status">{HIDDEN_SOME}</p>' in html
    assert '<li data-cmp-config="fake">fake: fake · fake-m1, prompt v3, temperature 0</li>' in html
    assert "<table" not in html and "misses" not in html and not GREY.search(html)
