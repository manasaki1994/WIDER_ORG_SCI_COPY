"""The Ranking page's benchmark recovery (wider/ui/static/app.js) under Node, no browser: the percentile table of
validation-sample-planner D3, from the engine's own summary."""
import re

from tests.unit.test_backends_js_node import ESC, STATIC, run_node
from wider.rank.score import pct_hits


def _fn(name: str) -> str:
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    m = re.search(rf"^(?:async )?function {name}\(.*?^}}\n", src, re.S | re.M)
    assert m, name
    return m.group(0)


def test_validate_percentile_columns():
    dev = pct_hits([5, 12, 30, 80, 150, 190, 400, 700, 1200, 1900], 10, 2000, [1, 5, 10, 25, 50])
    val = pct_hits([3, 50, 260, 990], 4, 2000, [1, 5, 10, 25, 50])
    summary = {"Development": {"recall_at_pct": dev}, "Validation": {"recall_at_pct": val}}
    import json
    out = run_node(f"""{ESC}
{_fn("pctTable")}
process.stdout.write(JSON.stringify({{ html: pctTable({json.dumps(summary)}), none: pctTable({{}}) }}));
""")
    html = out["html"]
    assert "<th>top 1% (rank 20)</th>" in html and "<th>top 50% (rank 1000)</th>" in html
    cells = re.findall(r"<td>(\d+ of \d+)</td>", html)
    assert cells == ["2 of 10", "4 of 10", "6 of 10", "7 of 10", "8 of 10", "1 of 4", "2 of 4", "2 of 4", "3 of 4", "4 of 4"]
    assert out["none"] == "" and 'class="small"' not in html and "muted" not in html


DEV = {"name": "development", "kind": "purposive", "labelled": True}
VAL = {"name": "validation", "kind": "probability", "labelled": False}
REASON = "the records or their abstracts changed since this ranking"


def _review(review):
    import json
    out = run_node(f"""{ESC}
{_fn("rkReviewHtml")}
process.stdout.write(JSON.stringify({{ html: rkReviewHtml({json.dumps(review)}) }}));
""")
    return out["html"]


def _boxes(html):
    return {m.group(1): m.group(2) for m in re.finditer(r'<div class="rk-box" data-rk-sample="([^"]+)">(.*?)</div>',
                                                         html, re.S)}


def test_review_actions_per_sample():
    """Scenario "A labelled development sample offers Keep only": the development sample's box offers "Keep this
    ranking" only and says a fresh development draw does not exist; the validation sample's box also offers "Draw a
    fresh sample" (the Draw card's fresh sample on Samples and labelling). In normal text, never grey."""
    html = _review([{"version": 1, "reason": REASON, "samples": [VAL, DEV]}])
    assert "Ranking needs review" in html and REASON in html and "muted" not in html and 'class="small"' not in html
    b = _boxes(html)
    assert set(b) == {"development", "validation"}
    assert 'data-rk-keep="1">Keep this ranking</button>' in b["development"]
    assert "a fresh development sample draw does not exist" in b["development"]
    assert "data-rk-fresh" not in b["development"] and "Draw a fresh sample" not in b["development"]
    assert 'data-rk-keep="1">Keep this ranking</button>' in b["validation"]
    assert ">Draw a fresh sample</button>" in b["validation"] and 'data-rk-fresh="#sample"' in b["validation"]
    # a development sample without labels may be drawn again
    b = _boxes(_review([{"version": 1, "reason": REASON, "samples": [{**DEV, "labelled": False}]}]))
    assert "data-rk-fresh" in b["development"] and "does not exist" not in b["development"]
    assert _review([]) == "" and _review(None) == ""


def test_review_keep_version_n_when_several():
    """Scenario "Keeping one of two versions": with two versions needing review each box keeps its own version."""
    html = _review([{"version": 1, "reason": REASON, "samples": [DEV]},
                    {"version": 2, "reason": REASON, "samples": [VAL]}])
    b = _boxes(html)
    assert 'data-rk-keep="1">Keep version 1</button>' in b["development"]
    assert 'data-rk-keep="2">Keep version 2</button>' in b["validation"]
    assert "Keep this ranking" not in html
    # a version no sample was drawn from still offers Keep
    html = _review([{"version": 3, "reason": REASON, "samples": []}])
    assert 'data-rk-keep="3">Keep this ranking</button>' in html


def test_versions_fold_and_base_names():
    """Scenario "A re-rank adds a version": one ranking in the select (its base name) and "2 versions" under a version
    history fold; with one version nothing names versions."""
    import json
    vs = [{"version": 1, "samples": ["validation", "development"]}, {"version": 2, "samples": []}]
    d = {"ranking": {"base_name": "fake__anchors_v1__top1"}, "bases": ["fake__anchors_v1__top1"], "versions": vs}
    out = run_node(f"""{ESC}
{_fn("rkVersionsHtml")}
{_fn("rkBaseOptions")}
process.stdout.write(JSON.stringify({{ two: rkVersionsHtml({json.dumps(vs)}), one: rkVersionsHtml({json.dumps(vs[:1])}),
  opts: rkBaseOptions({json.dumps(d)}), none: rkBaseOptions({{ ranking: null, bases: [] }}) }}));
""")
    assert "<summary>2 versions</summary>" in out["two"] and "<details" in out["two"]
    assert "version 1 (validation, development)" in out["two"] and "version 2 (newest)" in out["two"]
    assert out["one"] == ""
    assert out["opts"] == '<option value="fake__anchors_v1__top1" selected>fake__anchors_v1__top1</option>'
    assert "@v" not in out["opts"] and out["none"] == "<option value=''>no ranking yet</option>"


def _update(replies, choice):
    """rkUpdate with fake deps: `start` answers from ``replies`` in order; the dialog answers ``choice``."""
    import json
    out = run_node(f"""{ESC}
{_fn("rkUpdate")}
const replies = {json.dumps(replies)}, urls = [], asked = [];
const deps = {{ start: async (u) => {{ urls.push(u); return replies.shift(); }},
  askChoice: async (html) => {{ asked.push(html); return {json.dumps(choice)}; }} }};
const said = await rkUpdate(deps);
process.stdout.write(JSON.stringify({{ said, urls, asked }}));
""")
    return out


CHECK = {"confirm_needed": True, "embeddings_match": False, "needs_download": False,
         "notice": "The embeddings were made from older texts. Updating sends 3 changed texts to fake.",
         "keep_note": "Samples keep the version they were drawn from: validation and development keep version 1."}


def test_update_ranking_flow():
    """Scenario "Abstracts changed" (page side): "Update ranking" first gets the check and asks; embedding again sends
    confirm=true, "Rank with the older embeddings" sends use_older=true, Cancel sends nothing more; a model to download
    is allowed by the same answer, as the notice says; embeddings that match rank at once."""
    o = _update([CHECK, {"job_id": "j1"}], {"act": "embed"})
    assert o["urls"] == ["/api/jobs/rank-update", "/api/jobs/rank-update?confirm=true"]
    assert CHECK["notice"] in o["asked"][0] and CHECK["keep_note"] in o["asked"][0]
    assert "Rank with the older embeddings" in o["asked"][0] and 'data-act="cancel"' in o["asked"][0]
    assert "background" in o["said"]
    o = _update([CHECK, {"job_id": "j2"}], {"act": "older"})
    assert o["urls"][1] == "/api/jobs/rank-update?use_older=true"
    o = _update([CHECK], None)
    assert len(o["urls"]) == 1 and o["said"] == "Nothing was sent and no version was added."
    o = _update([{**CHECK, "needs_download": True}, {"job_id": "j3"}], {"act": "embed"})
    assert o["urls"][1] == "/api/jobs/rank-update?confirm=true&allow_download=true"
    o = _update([{"job_id": "j4"}], None)
    assert o["urls"] == ["/api/jobs/rank-update"] and not o["asked"]
    o = _update([{"skipped": True, "error": "fake is not downloaded"}], None)
    assert o["said"] == "fake is not downloaded"


def test_update_line_goes_when_the_job_ends():
    """ui-tester re-run F1 (29 Oct): "Updating the ranking in the background" is taken away when the job ends (the
    toast reports the version), and is never said for a job that ended before the answer came back."""
    out = run_node(f"""{ESC}
{_fn("rkUpdate")}
let msg = "", ends = [];
const deps = {{ start: async (u, done) => {{ ends.push(done); return {{ job_id: "j1" }}; }}, askChoice: async () => null,
  clear: () => {{ msg = ""; }} }};
msg = await rkUpdate(deps);
const during = msg;
ends[0]();
const late = {{ start: async (u, done) => {{ done(); return {{ job_id: "j2" }}; }}, askChoice: async () => null, clear: () => {{}} }};
process.stdout.write(JSON.stringify({{ during, after: msg, fast: await rkUpdate(late), n: ends.length }}));
""")
    assert out["during"].startswith("Updating the ranking in the background") and out["n"] == 1
    assert out["after"] == "" and out["fast"] == ""
