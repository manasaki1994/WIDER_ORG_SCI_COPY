"""The Setup page's seed-papers step (setup.js, change validation-reuse D3 and D4) runs under Node on the stand-in page
of test_backends_js_node: the move dialog for a shown validation paper, its Cancel, and the bulk move of the used
papers with the invite to add new ones. The seed replies come from the real route on a synthetic project."""
import html
import json

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from tests.unit.test_dry_run import _cli, _client
from tests.unit.test_validation_reuse import _frozen, _looked
from wider.construct import seed

NOTICE = ("B002 has been shown as a validation paper. Moving it to Development makes it a former validation paper: "
          "the report, the methods draft and the package list it, and the move cannot be undone.")


def run_seed(before: dict, after: dict, path: str, done: dict, act: str | None, body: str) -> dict:
    """setup.js's seed step drawn from ``before``; POST ``path`` answers confirm_needed until ``confirm`` is sent, then
    ``done`` (and the seed step is drawn from ``after``). askChoice answers ``act`` (None: Cancel). Returns the dialog's
    HTML, the POSTs sent, the step's HTML, the message and the focused element's name."""
    script = (ESC + PAGE + f"""
let moved = false, shown = "";
replies["/api/benchmark/seed"] = () => JSON.parse(JSON.stringify(moved ? {json.dumps(after)} : {json.dumps(before)}));
replies["/api/seeds/files"] = {{ files: [] }};
replies[{json.dumps(path)}] = (url, opts) => {{
  const b = JSON.parse(opts.body);
  if (!b.confirm) return {{ confirm_needed: true, notice: {json.dumps(NOTICE if "assign" in path else "bulk notice")} }};
  moved = true; return {json.dumps(done)};
}};
global.askChoice = async (html) => {{ shown = html; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
const renderLookupsCard = () => {{}};
{(STATIC / "setup.js").read_text(encoding="utf-8")}
await window.loadSeed(); await flush();
const first = $("#setup-seed").innerHTML;
{body}
await flush();
console.log(JSON.stringify({{ html: shown, first, posts: calls.filter(c => c[1] === "POST").map(c => [c[0], c[2]]),
  step: $("#setup-seed").innerHTML, msg: $("#seed-msg").innerHTML, split: $("#seed-split-msg").innerHTML,
  focus: document.activeElement ? document.activeElement._name : null }}));
""")
    return run_node(script)


def _select_row(html: str, bid: str) -> str:
    i = html.index(f'data-assign="{bid}"')
    return html[i:html.index("</select>", i)]


CHANGE_B002 = """
const sel = [...$("#setup-seed").querySelectorAll("[data-assign]")].find(s => s.dataset.assign === "B002");
sel.value = "Development"; await sel.onchange(); await flush();
"""


def test_move_shown_paper_dialog(tmp_path):
    """Scenario "The move warns first", the page half: the notice in a dialog with "Move to Development" and "Cancel"
    (askChoice focuses Cancel); Cancel sends nothing more, redraws the select with Validation and focuses it; "Move to
    Development" sends the move again with confirm."""
    p = _frozen(tmp_path, "dialog")
    before = _client(p).get("/api/benchmark/seed").json()
    assert before["n_used"] == 0
    out = run_seed(before, before, "/api/benchmark/assign", {"benchmark_id": "B002", "set": "Development"}, None,
                   CHANGE_B002)
    assert "seed-move-used" not in out["first"]   # no used paper: no bulk-move button
    h = out["html"]
    assert '<h2 id="choice-title">Move to Development?</h2>' in h and f"<p>{NOTICE}</p>" in h
    assert 'data-act="move">Move to Development</button>' in h and 'data-act="cancel">Cancel</button>' in h
    assert "autofocus" not in h   # askChoice focuses [data-act=cancel], so a stray Enter starts nothing
    common = (STATIC / "common.js").read_text(encoding="utf-8")   # askChoice moved there (accessibility-and-naming D1)
    assert 'dlg.querySelector("[data-act=cancel]").focus();' in common
    assert out["posts"] == [["/api/benchmark/assign", {"benchmark_id": "B002", "set": "Development"}]]   # no confirm
    assert out["msg"] == "Nothing was moved: B002 stays in Validation."
    assert '<option value="Validation" selected>' in _select_row(out["step"], "B002")
    assert "data-assign" in out["focus"]   # the redrawn select of B002 has the focus back
    out = run_seed(before, before, "/api/benchmark/assign", {"benchmark_id": "B002", "set": "Development"}, "move",
                   CHANGE_B002)
    assert [b for _u, b in out["posts"]] == [{"benchmark_id": "B002", "set": "Development"},
                                             {"benchmark_id": "B002", "set": "Development", "confirm": True}]
    assert "Nothing was moved" not in out["msg"]


def test_move_used_button(tmp_path):
    """Scenario "Moving the used papers", the page half: the button and its note while papers are used, the dialog,
    Cancel moves nothing, the confirmed move names the papers and the step then shows the invite."""
    import yaml

    from tests.unit.test_validation_reuse import SYN

    p = _looked(tmp_path, "button")
    f = tmp_path / "v4.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    assert _cli(p, "construct", "save", "synonyms", str(f), "--confirm").exit_code == 0
    before = _client(p).get("/api/benchmark/seed").json()
    assert before["n_used"] == 2
    assert _cli(p, "benchmark", "move-used", "--confirm").exit_code == 0
    after = _client(p).get("/api/benchmark/seed").json()
    assert after["invite"] == seed.ADD_INVITE
    click = 'await $("#seed-move-used").onclick(); await flush();'
    done = {"moved": ["B002", "B003"], "invite": seed.ADD_INVITE}
    out = run_seed(before, after, "/api/benchmark/move-used", done, None, click)
    first = out["first"]
    assert 'id="seed-move-used">Move used validation papers to Development</button>' in first
    assert "2 validation papers were shown before the query was revised; recall on them is not independent." in first
    assert "(2 used)" in first and "seed-invite" not in first
    assert '<h2 id="choice-title">Move to Development?</h2>' in out["html"] and 'data-act="cancel"' in out["html"]
    assert out["posts"] == [["/api/benchmark/move-used", {}]] and out["msg"] == "Nothing was moved."
    assert out["focus"] == "#seed-move-used"   # Cancel leaves the focus on the button
    out = run_seed(before, after, "/api/benchmark/move-used", done, "move", click)
    assert out["posts"] == [["/api/benchmark/move-used", {}], ["/api/benchmark/move-used", {"confirm": True}]]
    assert out["msg"] == "Moved to Development: B002, B003."
    invite = html.escape(seed.ADD_INVITE, quote=False)
    assert f'<p class="warnings small" id="seed-invite" tabindex="-1">{invite}</p>' in out["step"]
    assert "seed-move-used" not in out["step"] and out["focus"] == "#seed-invite"   # focus moves to the invite


SPLIT = """
replies["/api/benchmark/split"] = (url, opts) => {
  const b = JSON.parse(opts.body);
  if (!b.apply) return { rule: "a seeded split", development: ["B001", "B002"], validation: ["B003"], seed: 7, dev_share: 0.6 };
  if (!b.confirm) return { confirm_needed: true, notice: "B002 has been shown as a validation paper." };
  return { applied: true };
};
$("#seed-share").value = "0.6";
await $("#seed-split").onclick(); await flush();
await $("#seed-apply").onclick(); await flush();
"""


def test_split_dialog(tmp_path):
    """Scenario "An applied split moves shown papers", the page half: "Apply this split" shows the same dialog; Cancel
    applies nothing, "Move to Development" sends the split again with confirm."""
    p = _frozen(tmp_path, "split")
    before = _client(p).get("/api/benchmark/seed").json()
    for act, posts, msg in ((None, 2, "Nothing was moved."), ("move", 3, "split applied")):
        out = run_seed(before, before, "/api/none", {}, act, SPLIT)
        assert '<h2 id="choice-title">Move to Development?</h2>' in out["html"]
        assert "<p>B002 has been shown as a validation paper.</p>" in out["html"]
        bodies = [b for u, b in out["posts"] if u == "/api/benchmark/split"]
        assert len(bodies) == posts and bodies[1] == {"dev_share": 0.6, "seed": 7, "apply": True}
        assert bodies[2:] in ([], [{"dev_share": 0.6, "seed": 7, "apply": True, "confirm": True}]) and out["split"] == msg
