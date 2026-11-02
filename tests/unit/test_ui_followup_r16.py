"""Fixes from the sixteenth round of C1 checks: the ranking explorer on the command line and paging one cell's
records to its last page, NOT blocks in an imported EBSCO string, the CLI refusing another reviewer's tag, a protocol
value refused in plain words, a record id too large for the store, and keyboard-only findings (one announced status per
message, the note's name prompt, collapsed sidebar names)."""
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_query_roles import APP_JS, STATIC, js_function, run_js
from tests.unit.test_rank import _ranked
from tests.unit.test_records import _deduped
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import annotations as ann
from wider.core.project import Project
from wider.rank.score import explorer, rank
from wider.search.ir import Block, Query
from wider.search.parse_ebsco import parse_ebsco
from wider.search.render import render
from wider.ui.app import create_app

runner = CliRunner()


def _two_cells(tmp_path, n=12):
    p = _ranked(tmp_path, n, 2, 50)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    p.store.execute("UPDATE ranking_rows SET best_cell=CASE WHEN rank<=3 THEN 'ocean_cell' ELSE 'voice_cell' END")
    return p


def test_explorer_counts_the_rows_its_cell_filter_holds(tmp_path):
    p = _two_cells(tmp_path)
    assert explorer(p)["n_rows"] == 12 and explorer(p, cell="ocean_cell")["n_rows"] == 3
    last = explorer(p, cell="voice_cell", offset=5, limit=5)   # the cell's last page: rows 6–9 of 9
    assert last["n_rows"] == 9 and len(last["rows"]) == 4 and {r["best_cell"] for r in last["rows"]} == {"voice_cell"}
    assert explorer(Project.init(tmp_path / "empty"))["n_rows"] == 0


def test_rank_show_pages_the_ranking_on_the_command_line(tmp_path):
    p = _two_cells(tmp_path)
    root = str(p.root)
    r = runner.invoke(cli_app, ["rank", "show", "--cell", "ocean_cell", "--limit", "2", "--dir", root])
    assert r.exit_code == 0 and "1–2 of 3 records best matching ocean_cell" in r.output, r.output
    assert "ocean_cell 3" in r.output and "voice_cell 9" in r.output   # best cell of the top pool
    r = runner.invoke(cli_app, ["rank", "show", "--offset", "10", "--dir", root])
    assert r.exit_code == 0 and "11–12 of 12 records" in r.output
    r = runner.invoke(cli_app, ["rank", "show", "--cell", "no_such_cell", "--dir", root])
    assert r.exit_code == 1 and "no cell no_such_cell" in r.output
    r = runner.invoke(cli_app, ["rank", "show", "--dir", str(Project.init(tmp_path / "empty").root)])
    assert r.exit_code == 1 and "no ranking yet" in r.output


def test_a_not_block_parses_back():
    q = Query(blocks=[Block(key="voice", terms=["voice"]), Block(key="employee", terms=["employee*"]),
                      Block(key="plankton", terms=["plankton"])],
              logic={"and": ["voice", "employee", {"not": "plankton"}]})
    out = render(q, "ebsco", pretty=False)
    assert "AND NOT (TI (plankton)" in out
    back = parse_ebsco(out)
    assert back.logic == q.logic and render(back, "ebsco", pretty=False) == out
    assert parse_ebsco(render(q, "ebsco", pretty=True)).logic == q.logic
    bare = parse_ebsco("(TI (voice) OR AB (voice)) NOT (TI (plankton) OR AB (plankton))")   # NOT without AND
    assert bare.logic == {"and": ["voice", {"not": "plankton"}]}
    nested = parse_ebsco("TI (voice) AND (TI (employee*) NOT TI (plankton))")
    assert nested.logic == {"and": ["voice", {"and": ["employee", {"not": "plankton"}]}]}


def test_cli_untag_refuses_another_reviewers_tag(tmp_path):
    p, ids = _deduped(tmp_path)
    ann.add_tag(p, ids[0], "ana", "recheck")
    root = str(p.root)
    r = runner.invoke(cli_app, ["notes", "untag", str(ids[0]), "recheck", "--author", "ben", "--dir", root])
    assert r.exit_code == 1 and "no such tag by this author" in r.output
    assert [t["author"] for t in ann.for_record(p, ids[0])["tags"]] == ["ana"]
    r = runner.invoke(cli_app, ["notes", "untag", str(ids[0]), "recheck", "--author", "ana", "--dir", root])
    assert r.exit_code == 0 and "1 tag(s) removed" in r.output


def test_a_protocol_value_out_of_range_is_refused_in_plain_words(tmp_path):
    p = Project.init(tmp_path / "r")
    before = (p.root / "project.yaml").read_text(encoding="utf-8")
    r = runner.invoke(cli_app, ["protocol", "set", "ranking.top_n_compare", "[0, 2]", "--dir", str(p.root)])
    assert r.exit_code == 1 and "validation error" not in r.output
    assert "ranking.top_n_compare was not changed: item 1: input should be greater than or equal to 1" in " ".join(r.output.split())
    assert (p.root / "project.yaml").read_text(encoding="utf-8") == before
    d = TestClient(create_app(p)).post("/api/protocol/set", json={"path": "corpus.thin_words", "value": "x"})
    assert d.status_code == 400 and d.json()["detail"].startswith("corpus.thin_words was not changed: input should be a valid integer")


def test_a_record_id_too_large_for_the_store_is_not_found(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    r = c.get("/api/records/99999999999999999999999?author=ana")
    assert r.status_code == 404 and "too large" in r.json()["detail"]
    assert c.get("/api/records/999999").status_code == 404
    r = CliRunner().invoke(cli_app, ["notes", "add", "999999", "x", "--author", "ana", "--dir", str(tmp_path / "r")])
    assert r.exit_code == 1 and "no unique record 999999" in r.output


def test_a_double_click_on_next_asks_for_one_page():
    assert "${rkOffset + 1}–${rkOffset + d.rows.length} of ${d.n_rows}" in APP_JS   # the count of the filtered rows
    assert '$("#rk-next").disabled = !d.ranking || rkOffset + d.rows.length >= d.n_rows;' in APP_JS
    out = run_js("let rkPaging = false;\n" + js_function(APP_JS, "async function rkPage(") + """
let rkOffset = 0, fail = false, release = null;
const loads = [];
const document = { body: {}, activeElement: null };
const btn = (name) => ({ name, disabled: false, focus() { document.activeElement = this; } });
const els = { "#rk-next": btn("next"), "#rk-prev": btn("prev") };
const $ = (sel) => els[sel];
async function loadExplorer() {
  loads.push(rkOffset);
  if (fail) throw new Error("offline");
  await new Promise((r) => { release = r; });
}
const first = rkPage(50), second = rkPage(50);   // the second click lands while the first page loads
release(); await first; await second;
const afterDouble = { loads: [...loads], offset: rkOffset };
fail = true;
let refused = "";
try { await rkPage(50); } catch (e) { refused = e.message; }
const afterFail = { offset: rkOffset, paging: rkPaging };
fail = false;
const third = rkPage(-50); release(); await third;
console.log(JSON.stringify({ afterDouble, refused, afterFail, offset: rkOffset }));
""", {})
    assert out["afterDouble"] == {"loads": [50], "offset": 50}
    assert out["refused"] == "offline" and out["afterFail"] == {"offset": 50, "paging": False}   # a failed page keeps the old one
    assert out["offset"] == 0


def test_messages_are_announced_once_and_named_links_survive_collapse():
    assert 'id="rk-topn"' in (STATIC / "index.html").read_text(encoding="utf-8").split("aria-label=\"top-N: cell")[0][-120:]
    import re
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("index.html", "setup.js", "model.js"):   # the shell, and the areas rendered by setup.js and model.js
        msgs = re.findall(r'<(?:span|p|div) [^>]*id="[a-z-]*-msg"[^>]*>', (STATIC / name).read_text(encoding="utf-8"))
        assert msgs and all('role="status"' in m for m in msgs), (name, [m for m in msgs if 'role="status"' not in m])
    jobs = (STATIC / "jobs.js").read_text(encoding="utf-8")
    # a toast's line is read once through the page's persistent live regions; neither the box nor a toast has a role
    assert "say(text, tone);" in js_function(jobs, "function toast(") and "setAttribute(\"role\"" not in js_function(jobs, "function toast(")
    assert '<div id="toasts" class="toasts"></div>' in html
    drawer = (STATIC / "drawer.js").read_text(encoding="utf-8")
    assert 'msg.innerHTML = `<span class="bad">${WiderName.needHtml("")}</span>`; return null;' in drawer
    assert "dw-author" not in drawer
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".nav-link .label, " not in css and "body.sidebar-collapsed .nav-link .label { position:absolute;" in css
