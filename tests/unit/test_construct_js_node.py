"""The Construct page's query-refinement confirm steps (refine.js, D2) run under Node on the stand-in page of
test_backends_js_node: the freeze, save and unlock notices come from GET /api/construct/synonyms, the choice buttons,
and that Cancel posts nothing."""
import json

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

SYN = {"versions": [], "frozen_query": 3, "reopened": False, "save_notice": None,
       "freeze_notice": {"2": "Count line.\nv2 is now the frozen query; v3 stays frozen and is not the searched query.",
                         "4": "v4 is now the frozen query; draft v5 is kept and is not the searched query."},
       "freeze_choice": {"2": True, "4": False}, "unlock_notice": {"3": "Unlocking v3 reopens the query."}}


def run_refine(synonyms: dict, act: str | None, body: str) -> dict:
    """Load refine.js with the synonyms reply, let askChoice answer `act`, run `body`; return the dialog's HTML, the
    POSTs sent and #art-status / #art-freeze state."""
    script = (ESC + PAGE + f"""
replies["/api/construct/synonyms"] = {json.dumps(synonyms)};
replies["/api/construct/unlock"] = {{ version: 9, notice: "copied" }};
replies["/api/construct/synonyms/2/status/frozen"] = {{ notice: "frozen" }};
replies["/api/construct/synonyms/4/status/frozen"] = {{ notice: "frozen" }};
let shown = "", focus = null;
global.askChoice = async (html) => {{ shown = html; focus = "cancel"; return {json.dumps(act)} ? {{ act: {json.dumps(act)} }} : null; }};
global.artKind = "synonyms"; global.artCurrent = null; global.loadConstruct = async () => {{}};
{(STATIC / "refine.js").read_text(encoding="utf-8")}
{body}
await flush();
console.log(JSON.stringify({{ html: shown, focus, posts: calls.filter(c => c[1] === "POST").map(c => [c[0], c[2]]).map(c => c[1] === null ? [c[0]] : c),
  status: $("#art-status").textContent, freezeDisabled: $("#art-freeze").disabled, unlockHidden: $("#art-unlock").hidden,
  unlockDisabled: $("#art-unlock").disabled }}));
""")
    return run_node(script)


def test_freeze_on_a_frozen_version_shows_the_swap_notice():
    # another version than the frozen query: two choices, neither pre-selected, the notice lines, Cancel posts nothing
    out = run_refine(SYN, None, "await Refine.freeze('synonyms', 2);")
    h = out["html"]
    assert "Count them as seen" in h and "Keep them as validation" in h and 'data-act="freeze"' not in h
    assert "<p>Count line.</p>" in h and "v3 stays frozen and is not the searched query." in h
    assert "autofocus" not in h and "checked" not in h and 'data-act="cancel"' in h
    assert out["posts"] == []
    out = run_refine(SYN, "keep", "await Refine.freeze('synonyms', 2);")
    assert out["posts"] == [["/api/construct/synonyms/2/status/frozen?seen_choice=keep"]]
    out = run_refine(SYN, "count", "await Refine.freeze('synonyms', 2);")
    assert out["posts"] == [["/api/construct/synonyms/2/status/frozen?seen_choice=count"]]
    # the frozen query itself: Freeze is disabled; Unlock shows and is enabled on a frozen version
    out = run_refine(SYN, None, "await Refine.afterLoad('synonyms', { version: 3, status: 'frozen' });")
    assert out["freezeDisabled"] is True and out["unlockHidden"] is False and out["unlockDisabled"] is False
    out = run_refine(SYN, None, "await Refine.afterLoad('synonyms', { version: 5, status: 'draft' });")
    assert out["freezeDisabled"] is False and out["unlockDisabled"] is True
    out = run_refine(SYN, None, "await Refine.afterLoad('cells', { version: 1, status: 'approved' });")
    assert out["unlockHidden"] is True and out["freezeDisabled"] is False


def test_freeze_confirm_shows_freeze_notice():
    out = run_refine(SYN, "freeze", "await Refine.freeze('synonyms', 4);")
    assert "<p>v4 is now the frozen query; draft v5 is kept and is not the searched query.</p>" in out["html"]
    assert 'data-act="freeze"' in out["html"] and "Count them as seen" not in out["html"]
    assert out["posts"] == [["/api/construct/synonyms/4/status/frozen"]]
    # the save confirm: shown only when a save reopens something; Cancel stops the save
    d = {**SYN, "save_notice": "v3 is frozen. Saving v6 reopens the query; no validation paper has been seen yet."}
    out = run_refine(d, None, "global.res = await Refine.confirmSave('synonyms', 'Save?', 'Save'); $('#art-status').textContent = String(res);")
    assert d["save_notice"] in out["html"] and out["status"] == "false"
    out = run_refine(d, "go", "global.res = await Refine.confirmSave('synonyms', 'Save?', 'Save'); $('#art-status').textContent = String(res);")
    assert out["status"] == "true"
    out = run_refine(SYN, None, "global.res = await Refine.confirmSave('synonyms', 'Save?', 'Save'); $('#art-status').textContent = String(res);")
    assert out["html"] == "" and out["status"] == "true"
    # `always` (Add to block): asks even when the save reopens nothing; Cancel stops it
    out = run_refine(SYN, None, "global.res = await Refine.confirmSave('synonyms', 'Add?', 'Add', true); $('#art-status').textContent = String(res);")
    assert 'data-act="go"' in out["html"] and "Add?" in out["html"] and out["status"] == "false"


def _app_fn(name: str) -> str:
    """The text of app.js's top-level ``async function <name>(`` up to its closing brace at column 0."""
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    i = app.index(f"async function {name}(") if f"async function {name}(" in app else app.index(f"function {name}(")
    return app[i:app.index("\n}\n", i) + 2]


def test_freeze_result_stays_after_the_page_reloads():
    """Scenario "Freezing another version asks whether the papers count as seen", the page half of the result lines
    (ui-tester F1): after Freeze with a choice the real loadConstruct redraws the workbench, and the status line then
    reads the notice the freeze returned; the version line of the slower version reply does not write over it."""
    notice = ("Counted as seen: the 14 validation papers shown so far no longer validate the search.\n"
              "v2 is now the frozen query; v3 stays frozen and is not the searched query.")
    row = {"version": 2, "status": "frozen", "origin": "manual", "sha256": "ab", "created_at": "t", "note": ""}
    script = (ESC + PAGE + f"""
replies["/api/benchmark"] = [];
replies["/api/project"] = {{ config: {{}} }};
replies["/api/construct"] = {{ synonyms: [{json.dumps(row)}] }};
replies["/api/construct/synonyms"] = {json.dumps(SYN)};
replies["/api/construct/synonyms/2"] = () => new Promise((r) => setTimeout(() => r({{ row: {json.dumps(row)}, text: "" }}), 30));
replies["/api/construct/synonyms/2/status/frozen"] = {{ notice: {json.dumps(notice)} }};
global.askChoice = async () => ({{ act: "count" }});
document.querySelectorAll = () => new NodeListLike([]);
const KINDS = ["synonyms"], providerSelect = () => {{}}, constructEstimate = () => {{}}, drawArtTabs = () => {{}}, artRefusal = () => {{}};
let artKind = "synonyms", artVersions = {{}}, artCurrent = {{ version: 2, status: "draft" }};
{_app_fn("loadConstruct")}
{_app_fn("artItemsHtml")}
{_app_fn("loadArtifact")}
{(STATIC / "refine.js").read_text(encoding="utf-8")}
await $("#art-freeze").onclick();
await new Promise((r) => setTimeout(r, 60)); await flush();
console.log(JSON.stringify({{ status: $("#art-status").textContent,
  posts: calls.filter(c => c[1] === "POST").map(c => c[0]) }}));
""")
    out = run_node(script)
    assert out["posts"] == ["/api/construct/synonyms/2/status/frozen?seen_choice=count"]
    assert out["status"] == notice


def test_seed_papers_card_lists_b001_and_b002():
    """accessibility-and-naming 7.2: the Concepts and synonyms page's card of known papers is headed "Seed papers"
    (not "Benchmark papers"), and the real loadConstruct lists synthetic B001 and B002 in it with their set."""
    ix = (STATIC / "index.html").read_text(encoding="utf-8")
    card = ix[ix.rindex('<div class="card', 0, ix.index('id="bm-table"')):ix.index('id="bm-table"')]
    assert "<h2>Seed papers <button" in card and "Benchmark papers" not in ix
    rows = [{"benchmark_id": "B001", "set_name": "Development", "year": 2020, "abstract_words": 120,
             "abstract_source": "csv", "title": "A synthetic first paper"},
            {"benchmark_id": "B002", "set_name": "Validation", "year": 2021, "abstract_words": 0, "title": "A second"}]
    script = (ESC + PAGE + f"""
replies["/api/benchmark"] = {json.dumps(rows)};
replies["/api/project"] = {{ config: {{}} }};
replies["/api/construct"] = {{ synonyms: [] }};
document.querySelectorAll = () => new NodeListLike([]);
const KINDS = ["synonyms"], providerSelect = () => {{}}, constructEstimate = () => {{}}, drawArtTabs = () => {{}}, artRefusal = () => {{}};
let artKind = "synonyms", artVersions = {{}}, artCurrent = null;
{_app_fn("loadConstruct")}
await loadConstruct(); await flush();
console.log(JSON.stringify({{ table: $("#bm-table").innerHTML }}));
""")
    table = run_node(script)["table"]
    assert "<td>B001</td><td>Dev</td><td>2020</td>" in table and "A synthetic first paper" in table
    assert "<td>B002</td><td>Val</td><td>2021</td>" in table and '<span class="bad">missing</span>' in table


def test_artifact_items_list_view():
    """ui-redesign D32 F7: a small read-only list of the version's contents sits above "Show as text"; the editor
    comes first on the page, the extra term sources fold into "More ways to find terms"."""
    syn = {"blocks": [{"key": "a", "name": "Burnout", "terms": [f"t{i}" for i in range(14)]},
                      {"key": "b", "terms": [{"text": "<nurse>"}]}]}
    other = {"title": "x" * 210, "seeds": [{"id": "B001"}, {"id": "B002"}], "empty": [], "n": 3}
    r = run_node(f"{ESC}\n{_app_fn('artItemsHtml')}\nconsole.log(JSON.stringify({{ syn: artItemsHtml('synonyms', {json.dumps(syn)}),"
                 f" other: artItemsHtml('seeds', {json.dumps(other)}), none: artItemsHtml('synonyms', null) }}));")
    assert r["syn"].startswith('<h2 class="h-sub">In this version</h2><ul class="art-items">')
    assert "<li><b>Burnout</b>: t0, t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, and 2 more</li>" in r["syn"]
    assert "<li><b>b</b>: &lt;nurse&gt;</li>" in r["syn"]
    assert "<li><b>seeds</b> (2): B001, B002</li>" in r["other"] and "empty" not in r["other"] and "<b>n</b>" not in r["other"]
    assert "x" * 200 + "…" in r["other"] and r["none"] == ""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = html[html.index('<section id="page-construct"'):]
    sec = sec[:sec.index("</section>\n")]
    assert sec.index('id="art-tabs"') < sec.index('id="construct-more"')
    assert sec.index('id="art-items"') < sec.index('id="art-text-wrap"') < sec.index('id="art-text"')
    assert "More ways to find terms" in sec and "Propose with LLM" in sec
    assert "Propose from question" not in sec[:sec.index('id="construct-more"')]   # that name is the adjacent-literature button's
    more = sec[sec.index('id="construct-more"'):]
    assert 'id="adj-card"' in more and 'id="sn-card"' in more
