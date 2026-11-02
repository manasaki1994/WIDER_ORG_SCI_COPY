"""Missing abstracts page (ui-redesign 4.10): every setting of the step in named sections, the batch size beside the
lookup button with its command line, the thin-abstract words' effect line, and the page and `wider protocol set`
giving the same stored value and History entry. Page script under Node with a small fake DOM; synthetic data only."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core.project import Project
from wider.core.protocol import ReviewProtocol
from wider.rank.versions import thin_words_effect
from wider.ui.app import create_app

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "wider" / "ui" / "static"
NODE = shutil.which("node")
NO_RERUN = "Changing the thin-abstract words reruns nothing."
MARKS = 'Changing the thin-abstract words reruns nothing; it marks Ranking "needs review", because the ranking was made with the old value.'

PAGE = r"""
const els = {}, calls = [];
function mk(sel) {
  return { sel, textContent: "", innerHTML: "", hidden: false, disabled: false, value: "", dataset: {}, className: "",
    querySelector(s) { return document.querySelector(s); }, querySelectorAll() { return []; }, focus() { document.activeElement = this; } };
}
global.document = { querySelector(s) { return (els[s] ||= mk(s)); } };
global.window = global;
global.$ = (s) => document.querySelector(s);
global.api = async (url, opts) => {
  const method = (opts && opts.method) || "GET", body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push([method, url, body]);
  const h = R[url.split("?")[0]];
  if (h === undefined) throw new Error("no canned answer for " + url);
  return JSON.parse(JSON.stringify(typeof h === "function" ? h(body) : h));
};
global.Jobs = { start: async () => ({}) }; global.showStageError = () => {};
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
"""


def protocol_payload(effect: str = NO_RERUN) -> dict:
    rows = ReviewProtocol().explain()
    return {"rows": rows, "sections": [], "hash": "abc", "changed": 0, "effects": {"corpus.thin_words": effect}}


def run_page(body: str, r: dict) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    common = common[:common.index("let lastControl")]          # esc and cmdFold
    card = (STATIC / "settings_card.js").read_text(encoding="utf-8")
    appjs = (STATIC / "app.js").read_text(encoding="utf-8")
    enrich = appjs[appjs.index("// ---------------- enrichment page"):appjs.index("// ---------------- ranking page")]
    script = f"{PAGE}\nconst R = {json.dumps(r)};\n{common}\n{card}\n{enrich}\n(async () => {{\nawait loadEnrich(); await settle();\n{body}\n}})()" \
             ".catch(e => { console.error(e && e.stack || e); process.exit(1); });"
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def plain(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s).split())


def page_section(page: str) -> str:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    start = html.index(f'<section id="page-{page}"')
    return html[start:html.index("\n  </section>", start)]


ENRICH = {"records": 10, "thin": 3, "pending": 3, "filled": 0, "exhausted": 0, "failed": 0, "by_source": {}, "by_source_all": {}}


def test_every_setting_visible():
    out = run_page("console.log(JSON.stringify({ s: document.querySelector('#en-settings').innerHTML }));",
                   {"/api/enrich": ENRICH, "/api/protocol": protocol_payload(), "/api/enrich/records": RECS})
    s, words = out["s"], plain(out["s"])
    for name in ("Thin abstracts", "Sources and their order", "Title lookup"):
        assert f"<h3>{name}</h3>" in s
    rows = [r for r in ReviewProtocol().explain() if r["path"] == "corpus.thin_words" or r["section"] == "enrichment"]
    for r in rows:   # every field: its input, its explanation and its default
        assert f'data-ps-set="{r["path"]}"' in s and r["description"] in plain(s.replace("&#39;", "'")), r["path"]
        shown = ", ".join(r["default"]) if isinstance(r["default"], list) else str(r["default"])
        assert f"default: {shown}" in words, r["path"]
    assert "corpus.max_words" not in s                       # it stays with Ranking, which it shapes
    assert NO_RERUN in words and "needs review" not in words  # no ranking made with input tracking: the clause is left out
    for page in ("enrich", "dedup"):
        sec = page_section(page)
        assert not re.search(r"<summary>[^<]*Advanced", sec), page
    sec = page_section("enrich")                             # layout A: where this stands with the run button, then before you run
    assert sec.index('id="en-facts"') < sec.index('id="en-run"') < sec.index('id="en-limit"') < sec.index('id="en-recs"')
    assert "Where WIDER looks:" in sec and sec.index("Where WIDER looks:") < sec.index('id="en-run"')
    assert "wider enrich --limit 100" in sec and "100 is WIDER's default" in sec
    assert sec.index('id="en-settings"') > sec.index('id="en-recs"')
    assert "max_words" not in sec                            # Settings keeps it in the review protocol, beside Ranking's


def test_effect_line_names_needs_review_while_a_tracked_ranking_exists():
    out = run_page("console.log(JSON.stringify({ s: document.querySelector('#en-settings').innerHTML }));",
                   {"/api/enrich": ENRICH, "/api/protocol": protocol_payload(MARKS), "/api/enrich/records": RECS})
    assert MARKS.replace('"', "&quot;") in out["s"]


def test_set_on_the_page_posts_the_change_and_redraws():
    out = run_page("""
      Object.assign(document.querySelector('#ps-corpus-thin_words'), { value: ' 30 ', dataset: { type: 'number' } });   // as the drawn input has it
      document.querySelector('#en-settings').onclick({ target: { closest: (s) => s === '[data-ps-set]' ? { dataset: { psSet: 'corpus.thin_words' } } : null } });
      await settle();
      console.log(JSON.stringify({ calls, msg: document.querySelector('[data-ps-msg]').textContent }));""",
                   {"/api/enrich": ENRICH, "/api/protocol": protocol_payload(), "/api/protocol/set": protocol_payload() | {"timed": []},
                    "/api/enrich/records": RECS})
    assert ["POST", "/api/protocol/set", {"path": "corpus.thin_words", "value": 30}] in out["calls"]
    assert out["msg"].startswith("corpus.thin_words set")


RECS = {"group": "waiting", "label": "Not looked up yet", "missing": 3, "found": 0,
        "labels": {"waiting": "Not looked up yet", "failed": "Failed, can retry", "nowhere": "In no source",
                   "openalex": "OpenAlex", "crossref": "Crossref"},
        "counts": {"waiting": 3, "failed": 0, "nowhere": 0, "openalex": 0, "crossref": 0},
        "rows": [{"record_id": 1, "first_author": "Smith", "year": 2019, "title": "Synthetic title", "doi": None,
                  "what": "Not looked up yet. No DOI, so it goes to OpenAlex by exact title."}]}
BIG = dict(ENRICH, thin=300, pending=300)


def test_batch_size_waits_for_apply_then_the_command_follows():
    """owner walkthrough M3: a changed batch size says "Not applied yet" with Apply changes and Keep; Fetch waits."""
    out = run_page("""
      const st = () => ({ cmd: $('#en-cmd').textContent, run: $('#en-run').textContent, off: $('#en-run').disabled, box: $('#en-apply').innerHTML });
      $('#en-cmd').textContent = 'wider enrich --limit 100';   // as index.html has it
      $('#en-limit').value = '100'; $('#en-limit').oninput(); const before = st();
      $('#en-limit').value = '40'; $('#en-limit').oninput(); const typed = st();
      $('#en-apply-btn').onclick(); const applied = st();
      $('#en-limit').value = '70'; $('#en-limit').oninput(); $('#en-keep-btn').onclick(); const kept = st();
      console.log(JSON.stringify({ before, typed, applied, kept, limit: $('#en-limit').value }));""",
                   {"/api/enrich": BIG, "/api/protocol": protocol_payload(), "/api/enrich/records": RECS})
    assert out["before"]["run"] == "Fetch next 100" and not out["before"]["off"] and "WIDER's default" in out["before"]["box"]
    assert out["typed"]["off"] and "Not applied yet." in out["typed"]["box"] and "Apply changes" in out["typed"]["box"]
    assert "Keep 100 records" in out["typed"]["box"] and out["typed"]["cmd"] == "wider enrich --limit 100"
    assert out["applied"] == {"cmd": "wider enrich --limit 40", "run": "Fetch next 40", "off": False,
                              "box": out["applied"]["box"]} and "Not applied" not in out["applied"]["box"]
    assert out["kept"]["run"] == "Fetch next 40" and out["limit"] == "40"


def test_lead_figure_records_list_and_nothing_left():
    """owner walkthrough M2, M6, M7: the lead figure, the records by state, and Fetch off with nothing pending."""
    out = run_page("""
      console.log(JSON.stringify({ facts: $('#en-facts').innerHTML, recs: $('#en-recs').innerHTML,
        run: $('#en-run').textContent, off: $('#en-run').disabled }));""",
                   {"/api/enrich": dict(ENRICH, pending=0, thin=2, exhausted=2), "/api/protocol": protocol_payload(),
                    "/api/enrich/records": RECS})
    assert '<div class="lead"><dt>records still have no usable abstract</dt><dd>2</dd></div>' in out["facts"]
    assert out["off"] and out["run"] == "Nothing left to look up"
    recs = plain(out["recs"])
    assert "Still without an abstract · 3" in recs and "Not looked up yet 3" in recs and "Abstract found · 0" in recs
    assert "Smith 2019 · Synthetic title no DOI" in recs and "Showing 1 of 3 · Not looked up yet" in recs


def _add_ranking(p: Project, input_hash: str | None) -> None:
    with p.store.tx() as c:
        c.execute("INSERT INTO rankings(name,set_name,anchors_version,top_n,n,md5,created_at,input_hash) "
                  "VALUES ('r1','s',1,10,10,'m','2026-10-01',?)", (input_hash,))


def test_thin_words_page_and_cli_agree(tmp_path):
    runner = CliRunner()
    a, b = Project.init(tmp_path / "a", questions=["q"]), Project.init(tmp_path / "b", questions=["q"])
    r = runner.invoke(cli_app, ["protocol", "set", "corpus.thin_words", "30", "--dir", str(a.root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0 and NO_RERUN in " ".join(r.output.split())
    c = TestClient(create_app(b))
    d = c.post("/api/protocol/set", json={"path": "corpus.thin_words", "value": 30}).json()
    assert d["effects"]["corpus.thin_words"] == NO_RERUN
    pa, pb = Project.load(a.root), Project.load(b.root)
    assert pa.config.protocol.corpus.thin_words == pb.config.protocol.corpus.thin_words == 30
    ha = TestClient(create_app(pa)).get("/api/history").json()
    hb = c.get("/api/history").json()
    assert [h["summary"] for h in ha] == [h["summary"] for h in hb] and "corpus.thin_words" in hb[0]["summary"]


def test_effect_clause_only_for_a_ranking_with_input_tracking(tmp_path):
    p = Project.init(tmp_path / "r", questions=["q"])
    assert thin_words_effect(p) == NO_RERUN
    _add_ranking(p, None)                     # a ranking from before input hashes were stored is never marked
    assert thin_words_effect(p) == NO_RERUN
    with p.store.tx() as c:
        c.execute("UPDATE rankings SET input_hash='h'")
    assert thin_words_effect(p) == MARKS
    r = CliRunner().invoke(cli_app, ["protocol", "set", "corpus.thin_words", "25", "--dir", str(p.root)], env={"COLUMNS": "300"})
    assert r.exit_code == 0 and MARKS in " ".join(r.output.split())
