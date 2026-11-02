"""The "Add boundary cases" button on the Sampling page (wider/ui/static/sample_extend.js) under Node, no browser
(change screening-config-comparison, D9): only on an unlocked purposive sample, it posts the sample name, and shows the
server's answer in the CLI's words, in normal text, red when refused. Replies come from the real route on the
synthetic project of test_sample_boundary.py."""
import json
import re

from tests.unit.test_backends_js_node import ESC, STATIC, run_node
from tests.unit.test_sample_boundary import _build, _cli, _client, _copy, _dev20, _label_first
from wider.core.project import Project
from wider.sample.design import boundary_line, draw_probability, sampling_log

GREY = re.compile(r'class="[^"]*\b(muted|small)\b')


def page(body: str) -> dict:
    """sample_extend.js with the real escaping and a fake api that records requests and answers from `replies`."""
    return run_node(f"""{ESC}
{(STATIC / 'sample_extend.js').read_text(encoding='utf-8')}
const sent = []; let replies = {{}}; let reloads = 0;
const fakeApi = async (url, opts = {{}}) => {{
  sent.push({{ url, method: opts.method || "GET", body: opts.body ? JSON.parse(opts.body) : null }});
  const r = replies[url];
  if (r && r.error) {{ const e = new Error(r.error); e.status = r.status; throw e; }}
  if (r) return r;
  throw new Error("no reply for " + url);
}};
const deps = {{ api: fakeApi, reload: async () => {{ reloads++; }} }};
{body}
""")


def test_button_only_on_unlocked_purposive():
    out = page("""
const html = (s) => SampleExtend.cardHtml("development", s);
process.stdout.write(JSON.stringify({
  open: html({ kind: "purposive", locked: false, n: 20, boundary: "no near-miss or adjacent entries" }),
  locked: html({ kind: "purposive", locked: true, n: 20 }),
  prob: html({ kind: "probability", locked: false, n: 12 }),
}));""")
    assert out["open"].count("Add boundary cases") == 1 and 'data-extend="development"' in out["open"]
    assert "boundary: no near-miss or adjacent entries" in out["open"] and not GREY.search(out["open"])
    assert "Add boundary cases" not in out["locked"] and out["prob"] == ""


def test_button_posts_name_and_shows_server_message(tmp_path):
    p, _ = _build(tmp_path)
    draw_probability(p)
    _dev20(p)
    _label_first(p, 12)
    log = sampling_log(p)
    q = _copy(p, tmp_path, "copy")
    reply = _client(q).post("/api/samples/development/extend", json={"pool": "boundary"}).json()
    cli_lines = _cli(Project.load(p.root), "sample", "extend", "development").output.strip().splitlines()
    assert cli_lines[0].startswith("WIDER sends the anchor sentences")   # what the embedding step sends (D17)
    cli_text = "\n".join(cli_lines[1:])
    after = sampling_log(Project.load(p.root))
    out = page(f"""
replies["/api/samples/development/extend"] = {json.dumps(reply)};
const r = await SampleExtend.click("development", deps);
process.stdout.write(JSON.stringify({{ sent, reloads, added: r.added, before: SampleExtend.cardHtml("development", {json.dumps(log["development"])}),
  card: SampleExtend.cardHtml("development", {json.dumps(after["development"])}), lines: SampleExtend.message("development").lines }}));""")
    assert out["sent"] == [{"url": "/api/samples/development/extend", "method": "POST", "body": {"pool": "boundary"}}]
    assert out["reloads"] == 1 and out["added"] == 8
    assert "\n".join(out["lines"]) == cli_text == ("development: 8 record(s) added, now 28: boundary:near_miss:nm1 4, "
                                                    "boundary:near_miss:nm2 4")
    assert "boundary: per_boundary is 0" in out["before"]
    status = _cli(Project.load(p.root), "sample", "status", env={"COLUMNS": "200"}).output
    assert "boundary: boundary:near_miss:nm1 4, boundary:near_miss:nm2 4" in status, status
    assert "boundary: boundary:near_miss:nm1 4, boundary:near_miss:nm2 4" in out["card"]
    msg = re.search(r"<p data-extend-msg[^>]*>.*?</p>", out["card"]).group(0)
    assert "8 record(s) added" in msg and 'class="bad"' not in msg and not GREY.search(msg)


def test_refusal_shown_red_as_is():
    locked = "sample development is locked; wider sample unlock development first"
    out = page(f"""
replies["/api/samples/development/extend"] = {{ error: {json.dumps(locked)}, status: 409 }};
await SampleExtend.click("development", deps);
process.stdout.write(JSON.stringify({{ reloads, card: SampleExtend.cardHtml("development", {{ kind: "purposive", locked: false, n: 20 }}) }}));""")
    msg = re.search(r"<p data-extend-msg[^>]*>.*?</p>", out["card"]).group(0)
    assert 'class="bad"' in msg and locked in msg and not GREY.search(msg) and out["reloads"] == 1


def test_boundary_line_is_the_engines():
    """The card's boundary line is `boundary_line` (the line of `wider sample status`) for every shape of design."""
    designs = [{"kind": "purposive", "boundary": "no near-miss or adjacent entries"},
               {"kind": "purposive", "boundary": "per_boundary is 0",
                "extensions": [{"pools": {"boundary:near_miss:a": 3}}, {"pools": {"boundary:adjacent:b": 2}}]},
               {"kind": "purposive", "boundary": {"pools": {"boundary:near_miss:a": 4}},
                "extensions": [{"pools": {"boundary:near_miss:a": 1, "boundary:adjacent:c": 4}}]},
               {"kind": "purposive", "boundary": {"pools": {}}}, {"kind": "purposive"}, {"kind": "probability"}]
    out = page(f"process.stdout.write(JSON.stringify({json.dumps(designs)}.map(SampleExtend.boundaryText)));")
    assert out == [f"boundary: {boundary_line(d)}" if boundary_line(d) is not None else "" for d in designs]


def test_confirm_after_corpus_round():
    """ui-redesign 3.4: a 409 with confirm_needed is asked with the server's sentence; Cancel posts nothing more and
    says nothing changed; "Add them" posts again with confirm: true. Another 409 is shown red as before."""
    said = ("Until the 8 added records are labelled, development model output is hidden and screening runs on those "
            "records are refused.")
    out = page(f"""
const asked = [];
let answer = false;
const url = "/api/samples/development/extend";
const api2 = async (u, opts = {{}}) => {{
  const body = JSON.parse(opts.body);
  sent.push(body);
  if (!body.confirm) {{ const e = new Error({json.dumps(said)}); e.status = 409; e.body = {{ detail: {json.dumps(said)}, confirm_needed: true, n: 8 }}; throw e; }}
  return {{ name: "development", added: 8, n: 28, pools: {{ "boundary:near_miss:nm1": 8 }}, messages: [] }};
}};
const d = {{ api: api2, reload: async () => {{ reloads++; }}, askConfirm: async (t, b) => {{ asked.push([t, b]); return answer; }} }};
await SampleExtend.click("development", d);
const cancel = {{ sent: sent.slice(), msg: SampleExtend.message("development") }};
answer = true; sent.length = 0;
const r = await SampleExtend.click("development", d);
process.stdout.write(JSON.stringify({{ asked, cancel, sent, added: r.added, msg: SampleExtend.message("development") }}));""")
    assert out["asked"] == [[said, "Add them"], [said, "Add them"]]
    assert out["cancel"]["sent"] == [{"pool": "boundary"}]
    assert out["cancel"]["msg"] == {"lines": ["Nothing was changed."], "bad": False}
    assert out["sent"] == [{"pool": "boundary"}, {"pool": "boundary", "confirm": True}] and out["added"] == 8
    assert out["msg"]["lines"][0].startswith("development: 8 record(s) added") and not out["msg"]["bad"]
