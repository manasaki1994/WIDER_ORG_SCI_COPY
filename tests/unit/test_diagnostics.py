"""Search diagnostics (vision change 2a): the exact binomial interval for a recall of known papers, and what each
term retrieved in an imported export, its clusters and noise terms (D9). Synthetic exports only."""
import csv
import json

import pytest
import rich
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import journal
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.search import diagnostics
from wider.search.diagnostics import clopper_pearson
from wider.ui.app import create_app

FIXTURES = __import__("pathlib").Path(__file__).parent.parent / "fixtures" / "imports"
HEAD = ["Authors", "Title", "Year", "Source title", "DOI", "Abstract", "Author Keywords", "EID", "Document Type"]


def test_clopper_pearson_interval():
    """Scenario "Ten of ten": 10 of 10 gives 0.69 to 1.00, three of five 0.15 to 0.95 (95%, exact)."""
    lo, hi = clopper_pearson(10, 10)
    assert (f"{lo:.2f}", f"{hi:.2f}") == ("0.69", "1.00")
    lo, hi = clopper_pearson(3, 5)
    assert (f"{lo:.2f}", f"{hi:.2f}") == ("0.15", "0.95")
    assert clopper_pearson(0, 0) is None and clopper_pearson(0, 4)[0] == 0.0
    with pytest.raises(ValueError):
        clopper_pearson(5, 4)


def _export(path, rows):
    """A Scopus CSV export of invented records: ``(title, abstract, doi)``."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        for i, (title, abstract, doi) in enumerate(rows):
            w.writerow(["Doe, A.", title, 2021, "Journal of Invented Work", doi or "", abstract, "", f"2-s2.0-{i}",
                        "Article"])
    return path


def _imported(tmp_path, name, syn, papers, rows=None, src=None):
    """A project with ``syn`` saved as v1 and frozen, then one Scopus export imported against it."""
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.write_benchmark(papers)
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    add_file(p, "scopus", src or _export(tmp_path / f"{name}.csv", rows))
    import_all(p)
    return Project.load(p.root)


def _cli(p, *args):
    con = rich.get_console()
    old, con.width = con._width, 1000
    try:
        return CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "1000"})
    finally:
        con._width = old


def _card(d):
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    return run_node(ESC + PAGE + (STATIC / "diagnose.js").read_text(encoding="utf-8") + f"""
console.log(JSON.stringify({{ html: Diagnosis.resultHtml({json.dumps(d)}) }}));
""")["html"]


TWO = {"blocks": [{"key": "a", "name": "Remote", "terms": ['"remote work"', "telework", "hybrid"]},
                  {"key": "b", "name": "Teams", "terms": ["team*", "collaboration"]}], "logic": {"and": ["a", "b"]}}
ROWS = [("Remote work and team productivity", "", "10.1000/d1"),
        ("Hybrid schedules and collaboration quality", "Remote work in teams.", "10.1000/d2"),
        ("Telework adoption in teams", "", "10.1000/d3"),
        ("Office redesign and wellbeing", "Nothing about the topic.", "10.1000/d4")]


def test_what_did_what_counts(tmp_path):
    """Scenario "Counts per term on the synthetic import": every term has a record count, the records a term alone
    retrieved in its block are marked, and a record no term explains is counted with the note; CLI, route and card
    show the same numbers."""
    p = _imported(tmp_path, "w", TWO, [BenchmarkPaper(benchmark_id="B001", title="Telework adoption in teams",
                                                      doi="10.1000/d3", set="Development")], ROWS)
    d = diagnostics.what_did_what(p)
    assert d["version"] == 1 and d["validation_shown"] is True
    pl = d["platforms"]["scopus"]
    assert pl["records"] == 4 and pl["blocks"] == [{"block": "a", "records": 3}, {"block": "b", "records": 3}]
    assert {(t["block"], t["term"]): (t["records"], t["only"]) for t in pl["terms"]} == {
        ("a", '"remote work"'): (2, 1), ("a", "telework"): (1, 1), ("a", "hybrid"): (1, 0),
        ("b", "team*"): (3, 2), ("b", "collaboration"): (1, 0)}
    assert (pl["no_term"], pl["no_term_note"]) == (1, "retrieved by the database for a reason the profile does not see")
    assert pl["recall"]["Development"] == {"found": 1, "total": 1, "interval": list(clopper_pearson(1, 1)),
                                           "missed": []}
    # noise is read within a term's block: "remote work" and hybrid share no block-a term with B001 (telework), while
    # collaboration's record also holds team*, which B001 matches
    assert [(x["term"], x["records"], x["share"]) for x in pl["noise"]] == [('"remote work"', 2, 0.5), ("hybrid", 1, 0.25)]
    # four clusters of one record each; the one no term explains comes last
    assert [(c["id"], c["label"], c["seeds"]) for c in pl["clusters"]] == [
        ("C1", 'a: "remote work"; a: hybrid; b: collaboration; b: team*', []),
        ("C2", 'a: "remote work"; b: team*', []), ("C3", "a: telework; b: team*", ["B001"]),
        ("C4", "(no term)", [])]
    # CLI: the engine's lines, printed as they are
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0, r.output
    lines = diagnostics.diagnose_lines(d)
    assert r.output.splitlines() == lines
    for want in ("scopus: 4 records", "  block a: 3 records", '    a: "remote work": 2 records, 1 by this term alone',
                 "    a: hybrid: 1 records", "  no term explains: 1 (retrieved by the database for a reason the profile "
                 "does not see)", "  development seeds found: 1 of 1, 95% interval 0.03 to 1.00"):
        assert want in lines, (want, lines)
    # the route gives the same numbers and the same lines
    c = TestClient(create_app(p))
    got = c.get("/api/query/diagnose").json()
    assert got["lines"] == lines and got["platforms"]["scopus"]["terms"] == pl["terms"]
    # the card: the counts table, the note and two buttons per cluster
    html = _card(got)
    assert 'data-diag="scopus"' in html and "retrieved by the database for a reason the profile does not see" in html
    assert html.count('data-mark="relevant"') == 4 and html.count('data-mark="noise"') == 4
    assert "development seeds found: 1 of 1, 95% interval 0.03 to 1.00" in html
    # nothing imported against a version: exit 1 / 400 with the reason
    q = Project.init(tmp_path / "none", platforms=["scopus"])
    r = _cli(q, "query", "diagnose")
    assert r.exit_code == 1 and "no export imported against a synonyms version yet" in r.output, r.output
    # the page: nothing imported yet is an expected state, 200 with state "nothing_yet" and the same reason
    r = TestClient(create_app(q)).get("/api/query/diagnose")
    assert r.status_code == 200 and r.json()["state"] == "nothing_yet", r.text
    assert "no export imported against a synonyms version yet" in r.json()["message"] and r.json()["platforms"] == {}
    assert got["state"] == "ready"
    assert TestClient(create_app(p)).get("/api/query/diagnose?platform=pubmed").status_code == 400
    r = _cli(p, "query", "diagnose", "--platform", "pubmed")
    assert r.exit_code == 1 and "no pubmed export imported against synonyms v1" in r.output, r.output


ONE = {"blocks": [{"key": "a", "name": "Remote", "terms": ['"remote work"', "telework", "hybrid"]}],
       "logic": {"and": ["a"]}}


def test_clusters_are_marked_by_the_researcher(tmp_path):
    """Scenario "Marking a cluster": the cluster matched only by one term is marked noise on the CLI, another relevant
    through the page; both marks carry their author on the next listing, each is one undoable change, and no
    screening decision exists for any record."""
    p = _imported(tmp_path, "m", ONE, [BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/zz",
                                                      set="Development")], ROWS)
    r = _cli(p, "query", "clusters")
    assert r.exit_code == 0, r.output
    assert "  C1 (1 records): a: \"remote work\"\n      Remote work and team productivity\n" in r.output, r.output
    assert "  C2 (1 records): a: \"remote work\"; a: hybrid" in r.output
    r = _cli(p, "query", "cluster", "C1", "noise", "--by", "ana")
    assert r.exit_code == 0 and 'scopus v1 C1 (a: "remote work") marked noise by ana' in r.output, r.output
    c = TestClient(create_app(Project.load(p.root)))
    r = c.post("/api/query/cluster", json={"key": "a: telework", "mark": "relevant"}, headers={"x-wider-author": "bo"})
    assert r.status_code == 200 and (r.json()["id"], r.json()["by"]) == ("C3", "bo"), r.text
    r = _cli(p, "query", "clusters")
    assert '  C1 (1 records) [noise by ana]: a: "remote work"' in r.output, r.output
    assert "  C3 (1 records) [relevant by bo]: a: telework" in r.output, r.output
    got = c.get("/api/query/clusters").json()["platforms"]["scopus"]["clusters"]
    assert {x["id"]: (x["mark"], x["by"]) for x in got if x["mark"]} == {"C1": ("noise", "ana"), "C3": ("relevant", "bo")}
    ch = journal.changes(p, "cluster_mark")
    assert sorted((h["summary"], h["kind_label"], h["author"], h["undoable"]) for h in ch) == [
        ('scopus v1 cluster "a: \"remote work\"" marked noise', "cluster marked", "ana", True),
        ('scopus v1 cluster "a: telework" marked relevant', "cluster marked", "bo", True)]
    # no screening decision is made for any record of either cluster
    assert p.store.query("SELECT COUNT(*) AS n FROM labels")[0]["n"] == 0
    # marking it again the same way changes nothing; a bad mark and an unknown cluster are refused
    _cli(p, "query", "cluster", "C1", "noise", "--by", "ana")
    assert len(journal.changes(p, "cluster_mark")) == 2
    r = _cli(p, "query", "cluster", "C1", "maybe")
    assert r.exit_code == 1 and "mark must be relevant or noise" in r.output
    assert c.post("/api/query/cluster", json={"key": "C9", "mark": "noise"}).status_code == 400
    # undo the CLI mark from History: the listing shows it unmarked
    cid = next(h["change_id"] for h in ch if h["author"] == "ana")
    assert c.post(f"/api/history/{cid}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200
    got = diagnostics.what_did_what(Project.load(p.root))["platforms"]["scopus"]["clusters"]
    assert {x["id"]: x["mark"] for x in got} == {"C1": None, "C2": None, "C3": "relevant", "C4": None}


def test_noise_terms_respect_relevant_clusters(tmp_path):
    """Scenario "A noise term and a rescued one": huddle* retrieves 40 records holding no other term and no seed and
    is noise with 40 records; standup*'s only cluster is marked relevant and is not listed."""
    rows = [("Team meetings after errors", "", "10.1000/seed")]
    rows += [(f"Huddle number {i}", "", f"10.1000/h{i}") for i in range(40)]
    rows += [(f"Standup notes {i}", "", f"10.1000/s{i}") for i in range(5)]
    syn = {"blocks": [{"key": "a", "name": "Meetings", "terms": ["meeting*", "huddle*", "standup*"]}],
           "logic": {"and": ["a"]}}
    p = _imported(tmp_path, "n", syn, [BenchmarkPaper(benchmark_id="B001", title="Team meetings after errors",
                                                      doi="10.1000/seed", set="Development")], rows)
    noise = diagnostics.what_did_what(p)["platforms"]["scopus"]["noise"]
    assert noise == [{"block": "a", "term": "huddle*", "records": 40, "share": round(40 / 46, 4)},
                     {"block": "a", "term": "standup*", "records": 5, "share": round(5 / 46, 4)}]
    diagnostics.mark_cluster(p, "a: standup*", "relevant", by="ana")
    d = diagnostics.what_did_what(Project.load(p.root))
    assert d["platforms"]["scopus"]["noise"] == [{"block": "a", "term": "huddle*", "records": 40,
                                                  "share": round(40 / 46, 4)}]
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0, r.output
    noise_part = r.output.split("  Noise (no seed and no cluster marked relevant shares a term of the same block with these records):\n")[1]
    assert noise_part == f"    a: huddle*: 40 records, 87.0% of scopus\n{diagnostics.KNOWN_PAPERS_NOTE}\n", r.output


def test_diagnostics_hide_validation_seeds(tmp_path):
    """Scenario "Diagnostics with the validation set hidden" (what did what, clusters, noise, the indexing check and
    the known-item strings): after the unlock B002 (seen) is a used validation paper, apart and not a development seed
    (validation-reuse D1), B004 (entered since) is named and counted nowhere, B001 (from the query's results) is in
    neither set; after the re-freeze validation recall counts B004 alone. X001 (used, never retrieved) is listed as
    "Val (used)" in the known-item list, and after `benchmark move-used --confirm` as "Dev (former val)", counted with
    development by every diagnostic."""
    from wider.construct import seed

    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ['"remote work"', "telework", "hybrid", "meeting*"]}],
           "logic": {"and": ["a"]}}
    papers = [BenchmarkPaper(benchmark_id="B001", title="Remote work and team productivity in distributed software "
                             "teams", doi="10.1000/rw.001", set="Validation", source="query_results"),
              BenchmarkPaper(benchmark_id="B002", title="Hybrid schedules and collaboration quality",
                             doi="10.1000/rw.002", set="Validation"),
              BenchmarkPaper(benchmark_id="B003", title="Telework adoption after the pandemic: a review",
                             doi="10.1000/rw.003", set="Development"),
              BenchmarkPaper(benchmark_id="X001", title="Grazing pressure on alpine meadow plant cover",
                             doi="10.1000/rw.005", set="Validation")]   # never retrieved: in the known-item list (an id the B-numbering skips)
    p = _imported(tmp_path, "h", syn, papers, src=FIXTURES / "scopus.csv")
    artifacts.unlock(p, "synonyms", 1, by="ana", seen_choice="count")
    assert seed.add_by_dois(p, ["10.1000/rw.004"], set_name="Validation", source="colleague")[0].benchmark_id == "B004"
    p = Project.load(p.root)
    d = diagnostics.what_did_what(p)
    assert d["validation_shown"] is False and list(d["platforms"]["scopus"]["recall"]) == ["Development", "Validation, used"]
    rec = d["platforms"]["scopus"]["recall"]
    assert (rec["Development"]["found"], rec["Development"]["total"]) == (1, 1)
    assert (rec["Validation, used"]["found"], rec["Validation, used"]["total"]) == (1, 2)
    c = TestClient(create_app(p))
    outs = [_cli(p, "query", "diagnose"), _cli(p, "query", "clusters")]
    assert all(r.exit_code == 0 for r in outs), [r.output for r in outs]
    pages = [c.get("/api/query/diagnose").json(), c.get("/api/query/clusters").json()]
    texts = [r.output for r in outs] + [json.dumps(x) for x in pages] + [_card(pages[0])]
    for t in texts:
        assert "B004" not in t and "B001" not in t, t
    assert "development seeds found: 1 of 1" in outs[0].output and "(validation set hidden" in outs[0].output
    assert "used validation (not independent) seeds found: 1 of 2" in outs[0].output, outs[0].output
    assert '"Validation, used"' in json.dumps(pages[0]) and "used validation (not independent) seeds found" in _card(pages[0])
    # clusters' seed lists read no used validation paper
    assert "B002" not in outs[1].output and "seeds B003" in outs[1].output, outs[1].output
    # the indexing check: the known-item list (engine, CLI, route) never names B004 or B001, B002 counts as
    # development, and a verdict for the hidden B004 is refused without saying whether it was retrieved
    from wider.search import indexing

    ki = indexing.known_item_strings(p)
    idx = [ki, json.loads(json.dumps(c.get("/api/query/indexing").json())), _cli(p, "query", "indexing", "list").output]
    for t in idx:
        assert "B004" not in json.dumps(t) and "B001" not in json.dumps(t), t
    assert all(s["set"] != "Val" for s in ki["platforms"]["scopus"]["seeds"])
    # X001 (used, never retrieved) is listed as a used validation paper, not as a development seed, on CLI and page
    assert [(s["benchmark_id"], s["set"], s.get("used")) for s in ki["platforms"]["scopus"]["seeds"]
            if s["benchmark_id"] == "X001"] == [("X001", "Val (used)", True)]
    assert any(s["benchmark_id"] == "X001" and s["set"] == "Val (used)" for s in idx[1]["platforms"]["scopus"]["seeds"])
    assert "X001" in idx[2] and "Val (used)" in idx[2], idx[2]
    hid = c.post("/api/query/indexing", json={"benchmark_id": "B004", "platform": "scopus", "verdict": "indexed"})
    assert hid.status_code == 400 and "retrieved" not in hid.json()["detail"]
    # the re-freeze shows the set again: B004 alone validates, B002 stays with development, B001 in neither
    artifacts.set_status(p, "synonyms", 2, "frozen")
    d = diagnostics.what_did_what(Project.load(p.root))
    rec = d["platforms"]["scopus"]["recall"]
    assert (rec["Development"]["found"], rec["Development"]["total"]) == (1, 1)
    assert (rec["Validation"]["found"], rec["Validation"]["total"], rec["Validation"]["missed"]) == (1, 1, [])
    assert (rec["Validation, used"]["found"], rec["Validation, used"]["total"]) == (1, 2)
    assert "B001" not in json.dumps(d)
    # after move-used B002 and X001 are former validation papers: each diagnostic counts them as development seeds
    assert _cli(p, "benchmark", "move-used", "--confirm").exit_code == 0
    p = Project.load(p.root)
    rec = diagnostics.what_did_what(p)["platforms"]["scopus"]["recall"]
    assert (rec["Development"]["found"], rec["Development"]["total"]) == (2, 3) and "Validation, used" not in rec
    r = _cli(p, "query", "diagnose")
    assert "development seeds found: 2 of 3" in r.output and "used validation" not in r.output, r.output
    assert "B002" in _cli(p, "query", "clusters").output
    ki = indexing.known_item_strings(p)
    assert [(s["benchmark_id"], s["set"], s.get("former")) for s in ki["platforms"]["scopus"]["seeds"]
            if s["benchmark_id"] == "X001"] == [("X001", "Dev (former val)", True)]
    page = TestClient(create_app(p)).get("/api/query/indexing").json()
    assert any(s["benchmark_id"] == "X001" and s["set"] == "Dev (former val)" for s in page["platforms"]["scopus"]["seeds"])
    assert "Dev (former val)" in _cli(p, "query", "indexing", "list").output


def _limits_box(all_limits, key):
    """The Search strings page's limits line under a platform's string, as app.js builds it."""
    from tests.unit.test_backends_js_node import ESC, run_node

    js = (__import__("pathlib").Path(__file__).parents[2] / "wider/ui/static/app.js").read_text(encoding="utf-8")
    body = js[js.index("async function loadLimits()"):js.index('$("#q-import-toggle").onclick')]
    return run_node(ESC + f"""
const box = {{ dataset: {{ limits: {json.dumps(key)} }}, innerHTML: "", querySelector: () => ({{}}) }};
const document = {{ querySelectorAll: () => [box] }};
const api = async () => ({json.dumps(all_limits)});
{body}
loadLimits().then(() => console.log(JSON.stringify({{ html: box.innerHTML }})));
""")["html"]


def test_platform_limits_set_and_shown(tmp_path):
    """Scenario "Limits set on the CLI and on the page": the CLI in one project and the route in a second store the
    same limits; the page shows the CLI's line under the Scopus string and the render header carries it; History
    lists one platform_limits change that undo reverses; 2024..2010 is refused on both and nothing changes; the
    package's limits.json holds the same limits."""
    from wider.search import limits

    a = Project.init(tmp_path / "a", platforms=["scopus", "pubmed"])
    b = Project.init(tmp_path / "b", platforms=["scopus", "pubmed"])
    r = _cli(a, "query", "limits", "scopus", "--dates", "2010..2024", "--languages", "en,de",
             "--reason", "protocol limits", "--by", "ana")
    line = "limits: dates 2010..2024; languages en, de; reason: protocol limits"
    assert r.exit_code == 0 and r.output.strip() == f"scopus {line}", r.output
    c = TestClient(create_app(b))
    got = c.post("/api/query/limits", json={"platform": "scopus", "dates": "2010..2024", "languages": "en, de",
                                            "reason": "protocol limits"}, headers={"x-wider-author": "bo"})
    assert got.status_code == 200 and got.json()["line"] == line, got.text
    want = {"dates": "2010..2024", "languages": ["en", "de"], "reason": "protocol limits"}
    assert limits.limits(Project.load(a.root))["scopus"] == limits.limits(Project.load(b.root))["scopus"] == want
    assert limits.limits(Project.load(a.root))["pubmed"] == {}
    from tests.unit.test_outputs import package_json
    assert package_json(Project.load(a.root))["limits.json"] == {"scopus": want, "pubmed": {}}
    # the page: the line under the Scopus string is the CLI's; the fields hold the stored values
    page = c.get("/api/query/limits").json()
    assert page["scopus"] == {"limits": want, "line": line} and page["pubmed"]["line"] == "limits: none"
    html = _limits_box(page, "scopus")
    assert f'data-limits-line="scopus">{line}</p>' in html and 'value="en, de"' in html, html
    # the render header carries the line for scopus only
    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]}], "logic": {"and": ["a"]}}
    artifacts.save_new_version(a, "synonyms", syn, origin="manual")
    r = _cli(a, "query", "render")
    assert r.exit_code == 0 and f"# {line}" in r.output and r.output.count("# limits:") == 1, r.output
    # one platform_limits change, undone from History
    ch = journal.changes(a, "platform_limits")
    assert [(h["summary"], h["kind_label"], h["author"], h["undoable"]) for h in ch] == [
        (f"scopus {line}", "platform limits", "ana", True)]
    assert TestClient(create_app(a)).post(f"/api/history/{ch[0]['change_id']}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200
    assert limits.limits(Project.load(a.root))["scopus"] == {}
    # a range that ends before it starts: exit 1 and 400, both naming it; nothing changes
    r = _cli(b, "query", "limits", "scopus", "--dates", "2024..2010")
    assert r.exit_code == 1 and "date range '2024..2010' ends before it starts" in r.output, r.output
    bad = c.post("/api/query/limits", json={"platform": "scopus", "dates": "2024..2010"})
    assert bad.status_code == 400 and "2024..2010" in bad.text, bad.text
    assert limits.limits(Project.load(b.root))["scopus"] == want and len(journal.changes(b, "platform_limits")) == 1
    # dates that do not exist do not parse (ui-tester bad input): month 13, day 45, February 30, year 0000
    for text in ("2010-13-45..2024-02-30", "0000..9999", "2023-02-29.."):
        r = _cli(b, "query", "limits", "scopus", "--dates", text)
        assert r.exit_code == 1 and f"date range {text!r} does not parse" in r.output, r.output
        assert c.post("/api/query/limits", json={"platform": "scopus", "dates": text}).status_code == 400
    assert limits.parse_dates("2024-02-29..") == ("2024-02-29", None)
    assert limits.limits(Project.load(b.root))["scopus"] == want and len(journal.changes(b, "platform_limits")) == 1
    # an empty field clears that limit; an unknown platform is refused
    assert limits.set_limits(Project.load(b.root), "scopus", reason="")["limits"] == {k: want[k] for k in ("dates", "languages")}
    r = _cli(b, "query", "limits", "embase", "--dates", "2010..")
    assert r.exit_code == 1 and "'embase' is not one of the project's platforms" in r.output, r.output


def test_apply_edit_writes_a_draft(tmp_path):
    """Scenario "Apply an add-name edit": a term from B002's keywords that no block holds turns B002 into a hit on
    ebsco; it is listed "+1 seed on ebsco, +0 records (no import for this version yet)", and applying it writes a new
    draft holding the term in the right block, with a note naming the edit; v1 is unchanged."""
    from tests.unit.test_dry_run import _bench

    p = _bench(tmp_path, [("B001", "Development", "Telework and team learning", "Telework teams that learn.", ""),
                          ("B002", "Development", "Distributed teams learning from errors",
                           "Virtual work groups learning from errors.", "virtual work; learning")],
               name="e", platforms=("ebsco",))
    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]},
                      {"key": "b", "name": "Learning", "terms": ["learning"]}], "logic": {"and": ["a", "b"]}}
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    res = diagnostics.suggested_edits(p)
    e = next(x for x in res["edits"] if x["label"] == 'add "virtual work" to block a')
    assert e["consequence"] == "+1 seed on ebsco, +0 records (no import for this version yet)", e
    assert e["source"] == "B002 keywords" and e["kind"] == "add_name"
    # CLI and route list the same edits
    r = _cli(p, "query", "edits")
    assert r.exit_code == 0 and r.output.splitlines() == diagnostics.edit_lines(res), r.output
    assert f'{e["id"]} add "virtual work" to block a (B002 keywords): {e["consequence"]}' in r.output
    c = TestClient(create_app(p))
    assert c.get("/api/query/edits").json()["lines"] == diagnostics.edit_lines(res)
    r = _cli(p, "query", "edit", "apply", e["id"].lower(), "--by", "ana")
    assert r.exit_code == 0 and f"saved draft synonyms v2 from v1: edit {e['id']} on v1" in r.output, r.output
    rows = artifacts.list_versions(p, "synonyms")
    v2 = next(x for x in rows if x["version"] == 2)
    assert v2["status"] == "draft" and v2["note"].startswith(f'edit {e["id"]} on v1: add "virtual work" to block a')
    assert artifacts.load(p, "synonyms", 1)[1] == syn
    blocks = {b["key"]: b["terms"] for b in artifacts.load(p, "synonyms", 2)[1]["blocks"]}
    assert blocks == {"a": ["telework", '"virtual work"'], "b": ["learning"]}
    # v2 now holds the term: the same edit is no longer listed; an unknown id is refused on CLI and route
    assert all(x["label"] != 'add "virtual work" to block a' for x in diagnostics.suggested_edits(p)["edits"])
    r = _cli(p, "query", "edit", "apply", "E99")
    assert r.exit_code == 1 and "no edit E99 for synonyms v2" in r.output, r.output
    assert c.post("/api/query/edit", json={"id": "E99"}).status_code == 400


def test_record_budget_orders_edits(tmp_path):
    """Scenario "Over budget": with a budget of 100 and 150 imported records the diagnosis says "150 records, 50 over
    the budget of 100", and the drop-noise edit comes first among the edits that lose no seed."""
    rows = [("Team meetings after errors", "", "10.1000/seed")]
    rows += [(f"Huddle number {i}", "A short note.", f"10.1000/h{i}") for i in range(149)]
    syn = {"blocks": [{"key": "a", "name": "Meetings", "terms": ["meeting*", "huddle*"]}], "logic": {"and": ["a"]}}
    p = _imported(tmp_path, "bud", syn, [BenchmarkPaper(benchmark_id="B001", title="Team meetings after errors",
                                                        doi="10.1000/seed", set="Development")], rows)
    r = _cli(p, "protocol", "set", "identification.record_budget", "100")
    assert r.exit_code == 0, r.output
    p = Project.load(p.root)
    assert diagnostics.budget(p, 1)["text"] == "150 records, 50 over the budget of 100"
    # the Settings protocol editor lists the budget with its default (the page's twin of `protocol set`)
    row = next(x for x in TestClient(create_app(p)).get("/api/protocol").json()["rows"]
               if x["path"] == "identification.record_budget")
    assert (row["value"], row["default"], row["changed"]) == (100, 5000, True), row
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0 and r.output.splitlines()[1] == "150 records, 50 over the budget of 100", r.output
    edits = diagnostics.suggested_edits(p)["edits"]
    safe = [e for e in edits if not any(s["lost"] for s in e["seeds"].values())]
    assert safe and safe[0]["kind"] == "drop_term" and safe[0]["label"] == "drop huddle* from block a", edits
    assert safe[0]["consequence"] == "no seed gained or lost, -149 records on scopus" and safe[0]["id"] == "E1"
    # within the budget the same edit is ranked by kind, after the others that lose no seed
    _cli(p, "protocol", "set", "identification.record_budget", "5000")
    p = Project.load(p.root)
    assert diagnostics.budget(p, 1)["text"] == "150 records, within the budget of 5000"
    safe = [e for e in diagnostics.suggested_edits(p)["edits"] if not any(s["lost"] for s in e["seeds"].values())]
    assert safe[-1]["kind"] == "drop_term"


def _coverage_card(reply):
    """app.js `showCoverage` given ``reply`` for GET /api/coverage: the card's heading HTML and its note."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    from tests.unit.test_pickers_js_node import set_cell_js
    from tests.unit.test_query_roles import js_function

    fn = set_cell_js() + js_function((STATIC / "app.js").read_text(encoding="utf-8"), "async function showCoverage(")
    body = f"""{fn}
replies["/api/coverage"] = {json.dumps(reply)}; $("#cov-val").checked = true;
await showCoverage("platforms");
""" + out("{tables: $('#cov-tables').innerHTML, note: $('#cov-note').innerHTML || $('#cov-note').textContent}")
    return run_node(f"{ESC}\n{PAGE}\n{body}")


def test_interval_in_coverage_diagnosis_and_report(tmp_path):
    """Scenario "Ten of ten" on every surface: 10 of 10 development seeds found reads
    "10 of 10, 95% interval 0.69 to 1.00" in `wider coverage`, the coverage card, the diagnosis (CLI, route, card) and
    the report, each with the sentence that recall on known papers overstates recall on the scattered literature."""
    from wider.recall.report import build_report, to_markdown
    from wider.search import coverage

    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]}], "logic": {"and": ["a"]}}
    rows = [(f"Telework study {i} in invented teams", "", f"10.1000/s{i}") for i in range(10)]
    papers = [BenchmarkPaper(benchmark_id=f"B{i:03d}", title=t, doi=doi, set="Development")
              for i, (t, _, doi) in enumerate(rows)]
    papers.append(BenchmarkPaper(benchmark_id="V001", title="Unrelated validation paper on office plants",
                                 doi="10.1000/v1", set="Validation"))
    p = _imported(tmp_path, "ci", syn, papers, rows)
    want = "10 of 10, 95% interval 0.69 to 1.00"
    note = diagnostics.KNOWN_PAPERS_NOTE
    assert "overstates" in note
    # coverage: the engine, the CLI and the card
    res = coverage.check(p)
    dev = res["scopes"]["platform:scopus"]["Development"]
    assert (dev["found"], dev["n"], dev["text"]) == (10, 10, want) and dev["interval"] == list(clopper_pearson(10, 10))
    assert res["scopes"]["platform:scopus"]["Validation"]["text"].startswith("0 of 1, 95% interval 0.00 to ")
    assert res["known_papers_note"] == note
    r = _cli(p, "coverage")
    assert r.exit_code == 0, r.output
    assert f"  Development: {want}" in r.output and note in r.output.replace("\n", " ")
    card = _coverage_card(TestClient(create_app(Project.load(p.root))).get("/api/coverage").json())
    assert f"Development {want}" in card["tables"] and note in card["note"]
    # the diagnosis: lines (CLI = route) and the card
    d = diagnostics.what_did_what(p)
    lines = diagnostics.diagnose_lines(d)
    assert f"  development seeds found: {want}" in lines and lines[-1] == note
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0 and r.output.splitlines()[:len(lines)] == lines
    got = TestClient(create_app(Project.load(p.root))).get("/api/query/diagnose").json()
    assert got["lines"] == lines and got["known_papers_note"] == note
    html = _card(got)
    assert f"development seeds found: {want}" in html and note in html
    # the report: a row per scope and set, then the sentence
    rep = build_report(Project.load(p.root))
    assert rep["coverage"]["platform:scopus"]["Development"]["text"] == want
    md = to_markdown(rep)
    assert f"| platform:scopus | Development | {want} |" in md and note in md
    assert "## Seed paper coverage\n" in md and "Benchmark coverage" not in md   # accessibility-and-naming 7.4


# --- stream C: indexing check (2.10) ---

INDEX_RIS = ("TY  - JOUR\nTI  - Huddles after errors\nPY  - 2020\nDO  - https://doi.org/10.1000/B001\nER  -\n"
             "TY  - JOUR\nTI  - Hidden validation paper on wards\nPY  - 2021\nER  -\n")


def _indexing_project(tmp_path):
    """B001-B003 development, B004 and B005 validation (hidden until the freeze); the Scopus export holds B001 (by DOI)
    and B004 (by title); nothing is imported for Web of Science."""
    from wider.core.models import BenchmarkPaper
    from wider.core.project import Project
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "ix", platforms=["scopus", "wos"])
    p.write_benchmark([
        BenchmarkPaper(benchmark_id="B001", title="Huddles after errors", doi="10.1000/b001", set="Development"),
        BenchmarkPaper(benchmark_id="B002", title='Team "learning" in wards*', doi="10.1000/b002", set="Development"),
        BenchmarkPaper(benchmark_id="B003", title="Voice and silence at night", set="Development"),
        BenchmarkPaper(benchmark_id="B004", title="Hidden validation paper on wards", doi="10.1000/b004", set="Validation"),
        BenchmarkPaper(benchmark_id="B005", title="Another hidden paper", doi="10.1000/b005", set="Validation")])
    (tmp_path / "scopus.ris").write_text(INDEX_RIS, encoding="utf-8")
    add_file(p, "scopus", tmp_path / "scopus.ris")
    import_all(p)
    return p


def test_indexing_verdicts(tmp_path):
    # scenario "Verdicts change the diagnosis" (the diagnosis half: test_indexing_verdicts_in_the_diagnosis): B002 and B003
    # are missed on Scopus and their known-item strings are listed; not_indexed for B002 on the CLI, indexed for B003
    # through the page; both journalled and undoable; not_indexed for a retrieved seed is refused, indexed and unknown
    # are stored; a hidden Validation seed is never listed and takes no verdict (D9, F2)
    import json

    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from tests.unit.test_dry_run import _cli, _client
    from wider.construct import artifacts
    from wider.core import journal
    from wider.search import indexing

    p = _indexing_project(tmp_path)
    res = indexing.known_item_strings(p)
    sc = res["platforms"]["scopus"]
    assert res["validation_shown"] is False and list(res["platforms"]) == ["scopus", "wos"]
    assert (sc["files"], sc["retrieved"], sc["missed"]) == (1, 1, 2)
    b2, b3 = sc["seeds"]
    assert (b2["benchmark_id"], b3["benchmark_id"]) == ("B002", "B003")
    assert b2["string"] == 'DOI("10.1000/b002")' and b2["by"] == "doi" and b2["alternative"] == 'TITLE("Team learning in wards")'
    assert b3["string"] == 'TITLE("Voice and silence at night")' and b3["by"] == "title" and b3["alternative"] is None
    assert b2["verdict"] is None and b2["diagnosis"] == "not checked yet"
    assert res["platforms"]["wos"]["note"] == indexing.NO_EXPORT and res["platforms"]["wos"]["seeds"] == []
    assert "B004" not in json.dumps(res) and "B005" not in json.dumps(res)
    # the title phrase where the platform has no DOI field; no template, no string
    assert indexing.known_item("proquest", "10.1000/x", "A title")["string"] == 'TI("A title")'
    assert indexing.known_item("gscholar", "10.1000/x", "A title")["string"] is None
    # the CLI list shows the same strings, and not the hidden seeds; the route returns the engine's result (parity)
    r = _cli(p, "query", "indexing", "list")
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert 'DOI("10.1000/b002")' in out and 'TITLE("Voice and silence at night")' in out and "not checked yet" in out
    assert "B004" not in out and "B005" not in out and indexing.NO_EXPORT in out
    c = _client(p)
    g = c.get("/api/query/indexing")
    assert g.status_code == 200 and g.json() == res
    # not_indexed for B002 on the CLI; indexed for B003 through the page (the page's name is the author)
    r = _cli(p, "query", "indexing", "set", "B002", "scopus", "not_indexed", "--by", "Ana")
    assert r.exit_code == 0, r.output
    assert "B002 on scopus: not_indexed (not in the database)" in " ".join(r.output.split())
    rr = c.post("/api/query/indexing", json={"benchmark_id": "B003", "platform": "scopus", "verdict": "indexed"},
                headers={"x-wider-author": "Ben"})
    assert rr.status_code == 200, rr.text
    assert rr.json()["diagnosis"] == "indexed but not retrieved" and rr.json()["change_id"]
    stored = {(row["benchmark_id"], row["verdict"], row["by"]) for row in p.store.query("SELECT * FROM seed_indexing")}
    assert stored == {("B002", "not_indexed", "Ana"), ("B003", "indexed", "Ben")}
    assert indexing.verdict_groups(p, "scopus") == {"not_indexed": ["B002"], "indexed_missed": ["B003"],
                                                    "unknown": [], "unchecked": []}
    ch = journal.changes(p, "seed_indexing")
    assert [(x["summary"], x["author"], x["kind_label"]) for x in ch] == [
        ("B003 on scopus: indexed", "Ben", "indexing verdict"), ("B002 on scopus: not_indexed", "Ana", "indexing verdict")]
    assert all(x["undoable"] for x in ch)
    # refused, nothing changed: not_indexed for the retrieved B001 (CLI exit 1, route 400, the same reason), a hidden
    # Validation seed (without saying whether Scopus retrieved it), an unknown verdict or platform
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    r = _cli(p, "query", "indexing", "set", "B001", "scopus", "not_indexed")
    assert r.exit_code == 1 and "was retrieved by scopus" in " ".join(r.output.split())
    bad = c.post("/api/query/indexing", json={"benchmark_id": "B001", "platform": "scopus", "verdict": "not_indexed"})
    assert bad.status_code == 400 and "was retrieved by scopus" in bad.json()["detail"]
    for bid in ("B004", "B005"):
        hid = c.post("/api/query/indexing", json={"benchmark_id": bid, "platform": "scopus", "verdict": "indexed"})
        assert hid.status_code == 400 and "validation set is hidden" in hid.json()["detail"]
        assert "retrieved" not in hid.json()["detail"]
        r = _cli(p, "query", "indexing", "set", bid, "scopus", "not_indexed")
        assert r.exit_code == 1 and "validation set is hidden" in " ".join(r.output.split())
    assert _cli(p, "query", "indexing", "set", "B002", "scopus", "maybe").exit_code == 1
    assert c.post("/api/query/indexing", json={"benchmark_id": "B002", "platform": "nowhere", "verdict": "unknown"}).status_code == 400
    assert c.post("/api/query/indexing", json={"benchmark_id": "B999", "platform": "scopus", "verdict": "unknown"}).status_code == 400
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n and p.store.scalar("SELECT COUNT(*) FROM seed_indexing") == 2
    # indexed and unknown are stored for the retrieved seed (they do not contradict the export)
    assert indexing.set_verdict(p, "B001", "scopus", "indexed", "Ana")["retrieved"] is True
    assert indexing.set_verdict(p, "B001", "scopus", "unknown", "Ana")["change_id"]
    assert p.store.one("SELECT verdict FROM seed_indexing WHERE benchmark_id='B001'")["verdict"] == "unknown"
    assert indexing.set_verdict(p, "B001", "scopus", "unknown")["already"] is True
    # undo B003's verdict: it is unchecked again; undo B002's: no verdict left for it
    journal.undo(p, rr.json()["change_id"], other=True)
    assert indexing.verdict_groups(p, "scopus") == {"not_indexed": ["B002"], "indexed_missed": [], "unknown": [],
                                                    "unchecked": ["B003"]}
    journal.undo(p, ch[1]["change_id"], other=True)
    assert p.store.one("SELECT * FROM seed_indexing WHERE benchmark_id='B002'") is None
    # the card on the Search results page: the missed seeds with their strings and verdict buttons; a button posts
    # the verdict to the same route, and the groups line follows the verdicts
    indexing.set_verdict(p, "B002", "scopus", "not_indexed", "Ana")
    data = c.get("/api/query/indexing").json()
    page = run_node(ESC + PAGE + (STATIC / "indexing.js").read_text(encoding="utf-8") + f"""
replies["/api/query/indexing"] = (url, opts) => (opts && opts.method === "POST")
  ? {{ benchmark_id: "B003", platform: "scopus", verdict: "indexed", diagnosis: "indexed but not retrieved", already: false, change_id: 9 }}
  : {json.dumps(data)};
await IndexingCheck.load();
const html = $("#idx-out").innerHTML;
await click("#idx-out", "[data-idx-verdict]", (b) => b.dataset.seed === "B003" && b.dataset.idxVerdict === "indexed");
console.log(JSON.stringify({{ html, calls, msg: $("#idx-msg").textContent }}));
""")
    h = page["html"]
    assert '<tr data-seed="B002">' in h and '<tr data-seed="B003">' in h and "B004" not in h and "B005" not in h
    assert "DOI(&quot;10.1000/b002&quot;)" in h and "TITLE(&quot;Voice and silence at night&quot;)" in h
    assert 'data-group="not_indexed">not in the database: B002' in h and "not checked yet: B003" in h
    assert 'data-idx-verdict="not_indexed" data-seed="B002"' in h.replace("\n      ", " ")
    assert "Validation set hidden" in h and indexing.NO_EXPORT in h
    assert ["/api/query/indexing", "POST", {"benchmark_id": "B003", "platform": "scopus", "verdict": "indexed"}] in page["calls"]
    assert "B003 on scopus: indexed (indexed but not retrieved). Undo it in History." == page["msg"]
    # once the set is shown (the query frozen), the missed Validation seed B005 is listed; B004 was retrieved
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["huddle*"]}],
                                               "logic": {"and": ["a"]}}, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    shown = indexing.known_item_strings(p, platform="scopus")
    assert shown["validation_shown"] is True
    assert [(s["benchmark_id"], s["set"]) for s in shown["platforms"]["scopus"]["seeds"]] == [
        ("B002", "Dev"), ("B003", "Dev"), ("B005", "Val")]
    assert shown["platforms"]["scopus"]["retrieved"] == 2
    bad = c.post("/api/query/indexing", json={"benchmark_id": "B004", "platform": "scopus", "verdict": "not_indexed"})
    assert bad.status_code == 400 and "was retrieved by scopus" in bad.json()["detail"]


def test_indexing_verdicts_in_the_diagnosis(tmp_path):
    """Scenario "Verdicts change the diagnosis", the diagnosis half: B002 and B003 are missed on Scopus; after
    not_indexed for B002 on the CLI and indexed for B003 through the page, the diagnosis (engine, `wider query
    diagnose`, the route and the Diagnostic report card) lists B002 under "not in the database" and B003 under
    "indexed but not retrieved"; undoing a verdict puts the seed back under "indexing not checked yet"."""
    syn = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]}], "logic": {"and": ["a"]}}
    papers = [BenchmarkPaper(benchmark_id="B001", title="Telework adoption in teams", doi="10.1000/d3", set="Development"),
              BenchmarkPaper(benchmark_id="B002", title="Distributed crews at sea", doi="10.1000/x2", set="Development"),
              BenchmarkPaper(benchmark_id="B003", title="Home offices and sleep", set="Development")]
    p = _imported(tmp_path, "iv", syn, papers, ROWS)
    pl = diagnostics.what_did_what(p)["platforms"]["scopus"]
    assert pl["recall"]["Development"]["missed"] == ["B002", "B003"]
    assert pl["indexing"] == {"unchecked": ["B002", "B003"]}
    r = _cli(p, "query", "indexing", "set", "B002", "scopus", "not_indexed", "--by", "Ana")
    assert r.exit_code == 0, r.output
    c = TestClient(create_app(Project.load(p.root)))
    rr = c.post("/api/query/indexing", json={"benchmark_id": "B003", "platform": "scopus", "verdict": "indexed"},
                headers={"x-wider-author": "Ben"})
    assert rr.status_code == 200, rr.text
    d = diagnostics.what_did_what(Project.load(p.root))
    assert d["platforms"]["scopus"]["indexing"] == {"not_indexed": ["B002"], "indexed_missed": ["B003"]}
    lines = diagnostics.diagnose_lines(d)
    assert "    not in the database: B002" in lines and "    indexed but not retrieved: B003" in lines
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0 and r.output.splitlines()[:len(lines)] == lines
    got = c.get("/api/query/diagnose").json()
    assert got["lines"] == lines
    html = _card(got)
    assert 'data-indexing-group="not_indexed">not in the database: B002' in html
    assert 'data-indexing-group="indexed_missed">indexed but not retrieved: B003' in html
    assert [x["kind"] for x in journal.changes(p, "seed_indexing")] == ["seed_indexing", "seed_indexing"]
    journal.undo(p, rr.json()["change_id"], other=True)
    assert diagnostics.what_did_what(Project.load(p.root))["platforms"]["scopus"]["indexing"] == {
        "not_indexed": ["B002"], "unchecked": ["B003"]}


LIGHT = {"blocks": [{"key": "a", "name": "Lights", "terms": ["lighthouse*", "beacon", '"signal lamp"', "chart*"]},
                    {"key": "b", "name": "Weather", "terms": ["fog", "storm*"]}], "logic": {"and": ["a", "b"]}}
LIGHT_ROWS = ([("Lighthouse keepers in fog", "A beacon in the storm.", "10.1000/l1"),
               ("Beacon maintenance after storms", "", "10.1000/l2"),
               ("Signal lamp use in fog", "Lighthouses compared.", "10.1000/l3"),
               ("Harbour charts and tides", "Nothing about lights.", "10.1000/l4")]
              + [(f"Storm log number {i}", "A beacon note.", f"10.1000/s{i}") for i in range(6)]
              + [(f"Charts of the storm coast {i}", "Coastal survey notes.", f"10.1000/c{i}") for i in range(5)])
LIGHT_SEED = BenchmarkPaper(benchmark_id="B001", title="Lighthouse keepers in fog", doi="10.1000/l1",
                            set="Development")


def test_unsampled_output_unchanged(tmp_path):
    """Scenario "Unmarked exports read as before": with no file marked as a sample, the diagnosis, the budget line and
    the edits are word for word what they were before the sampled-export change (pinned against that code)."""
    p = _imported(tmp_path, "light", LIGHT, [LIGHT_SEED], LIGHT_ROWS)
    _cli(p, "protocol", "set", "identification.record_budget", "5")
    p = Project.load(p.root)
    assert diagnostics.diagnose_lines(diagnostics.what_did_what(p)) == [
        "synonyms v1",
        "15 records, 10 over the budget of 5",
        "scopus: 15 records",
        "  block a: 15 records",
        "  block b: 14 records",
        "    a: lighthouse*: 2 records",
        "    a: beacon: 8 records, 7 by this term alone",
        '    a: "signal lamp": 1 records',
        "    a: chart*: 6 records, 6 by this term alone",
        "    b: fog: 2 records, 1 by this term alone",
        "    b: storm*: 13 records, 12 by this term alone",
        "  no term explains: 1 (retrieved by the database for a reason the profile does not see)",
        "  development seeds found: 1 of 1, 95% interval 0.03 to 1.00",
        "  Noise (no seed and no cluster marked relevant shares a term of the same block with these records):",
        "    a: chart*: 6 records, 40.0% of scopus",
        "Recall on known papers overstates recall on the scattered literature: the papers a researcher already knows "
        "are the ones a search finds most easily."]
    bud = diagnostics.budget(p, 1)
    assert (bud["records"], bud["over"], bud["per_platform"], bud["text"]) == (
        15, 10, {"scopus": 15}, "15 records, 10 over the budget of 5")
    res = diagnostics.suggested_edits(p)
    assert diagnostics.edit_lines(res) == [
        "synonyms v1 (frozen): 15 records, 10 over the budget of 5",
        "E1 drop chart* from block a (noise on scopus: 6 records): no seed gained or lost, -5 records on scopus"]
    assert res["edits"][0]["records_removed"] == {"scopus": 5}
    r = _cli(p, "query", "edits")
    assert r.exit_code == 0 and "-5 records on scopus" in r.output, r.output
    # an export of a catalogue platform the project did not choose leaves the edits as before (risk-check R1)
    from wider.ingest.importer import add_file, import_all

    add_file(p, "wos", _export(tmp_path / "wos.csv", LIGHT_ROWS[:9]))
    import_all(p)
    p = Project.load(p.root)
    assert p.store.scalar("SELECT included FROM sources WHERE platform='wos'") == 1
    assert diagnostics.edit_lines(diagnostics.suggested_edits(p))[1] == (
        "E1 drop chart* from block a (noise on scopus: 6 records): no seed gained or lost, -5 records on scopus")


def test_openalex_import_is_diagnosed(tmp_path):
    """Scenario "An OpenAlex import is diagnosed and feeds the edits": an export imported as openalex in a project whose
    chosen databases are Scopus and Web of Science is diagnosed (no "no profile" line) and its records removed appear
    in the edits; the CLI and the routes give the same counts."""
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "oa", platforms=["scopus", "wos"])
    p.write_benchmark([LIGHT_SEED])
    artifacts.save_new_version(p, "synonyms", LIGHT, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    add_file(p, "openalex", _export(tmp_path / "oa.csv", LIGHT_ROWS))
    import_all(p)
    _cli(p, "protocol", "set", "identification.record_budget", "5")
    p = Project.load(p.root)
    d = diagnostics.what_did_what(p)
    lines = diagnostics.diagnose_lines(d)
    assert "openalex: no profile for openalex: not diagnosed" not in lines
    assert "openalex: 15 records" in lines and "    a: chart*: 6 records, 6 by this term alone" in lines, lines
    res = diagnostics.suggested_edits(p)
    drop = next(e for e in res["edits"] if e["source"].startswith("noise on openalex"))
    assert drop["records_removed"] == {"openalex": 5}
    assert list(drop["seeds"]) == ["scopus", "wos", "openalex"], drop
    text = diagnostics.edit_lines(res)
    assert any("-5 records on openalex" in x for x in text), text
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0 and r.output.splitlines()[:len(lines)] == lines
    c = TestClient(create_app(p))
    assert c.get("/api/query/diagnose").json()["lines"] == lines
    assert c.get("/api/query/edits").json()["lines"] == text
    assert _cli(p, "query", "edits").output.splitlines()[-len(text):] == text


def _oa(tmp_path, name, files, seeds=(LIGHT_SEED,), budget=None):
    """A project (Scopus chosen) with LIGHT frozen as v1 and each ``(platform, rows)`` of ``files`` imported against
    it; returns the project and the source ids in the order of ``files``."""
    from wider.ingest.importer import add_file, import_all, list_sources

    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.write_benchmark(list(seeds))
    artifacts.save_new_version(p, "synonyms", LIGHT, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    for i, (plat, rows) in enumerate(files):
        add_file(p, plat, _export(tmp_path / f"{name}{i}.csv", rows))
    import_all(p)
    if budget:
        _cli(p, "protocol", "set", "identification.record_budget", str(budget))
    p = Project.load(p.root)
    return p, [s["source_id"] for s in sorted(list_sources(p)["sources"], key=lambda s: s["source_id"])]


def _mark(p, sid, of):
    r = _cli(p, "import", "sample", str(sid), "--of", str(of))
    assert r.exit_code == 0, r.output
    return Project.load(p.root)


# 1,249 records: the seed, 300 that chart* retrieves and 948 more (beacon and storm*)
SAMPLE_ROWS = ([LIGHT_ROWS[0]] + [(f"Charts of the storm coast {i}", "Coastal survey notes.", f"10.1000/c{i}")
                                  for i in range(300)]
               + [(f"Storm log number {i}", "A beacon note.", f"10.1000/s{i}") for i in range(948)])


def test_sampled_export_scales_the_diagnosis(tmp_path):
    """Scenarios "A sample of 1,249 out of 650,000" and "A sample over the budget": the budget line, the heading and
    the term lines are estimates naming the sample; before the mark the file reads "within the budget"; the CLI and the
    route give the same lines."""
    p, (sid,) = _oa(tmp_path, "s", [("openalex", SAMPLE_ROWS)], budget=5000)
    assert diagnostics.budget(p, 1)["text"] == "1249 records, within the budget of 5000"
    p = _mark(p, sid, 650000)
    bud = diagnostics.budget(p, 1)
    assert bud["text"] == ("about 650,000 records, 645,000 over the budget of 5000 (estimate: openalex is a random "
                           "sample of 1,249 of 650,000 results)"), bud["text"]
    assert (bud["records"], bud["per_platform"], bud["estimated"]) == (650000, {"openalex": 1249}, True)
    lines = diagnostics.diagnose_lines(diagnostics.what_did_what(p))
    assert lines[1] == bud["text"]
    assert ("openalex: 1,249 records imported, a random sample of 650,000 results; the counts below are estimates: "
            "records in the sample × 650,000 / 1,249") in lines, lines
    chart = next(x for x in lines if x.startswith("    a: chart*: "))
    assert chart.startswith("    a: chart*: about 156,000 records (300 in the sample)"), chart
    assert not any("within the budget" in x for x in lines)
    r = _cli(p, "query", "diagnose")
    assert r.exit_code == 0 and r.output.splitlines()[:len(lines)] == lines, r.output
    assert TestClient(create_app(p)).get("/api/query/diagnose").json()["lines"] == lines


def test_diagnosis_shows_sample_estimates(tmp_path):
    """Scenario "A sample of 1,249 out of 650,000", last AND: the card shows the CLI's sentences, the heading and the
    budget line at normal size (no small grey class)."""
    p, (sid,) = _oa(tmp_path, "c", [("openalex", SAMPLE_ROWS)], budget=5000)
    p = _mark(p, sid, 650000)
    d = TestClient(create_app(p)).get("/api/query/diagnose").json()
    html = _card(d)
    assert ('<p data-sample-heading="openalex">openalex: 1,249 records imported, a random sample of 650,000 results; '
            "the counts below are estimates: records in the sample × 650,000 / 1,249</p>") in html, html
    assert ("<p data-budget>about 650,000 records, 645,000 over the budget of 5000 (estimate: openalex is a random "
            "sample of 1,249 of 650,000 results)</p>") in html, html
    assert "about 156,000 records (300 in the sample)" in html


def test_same_sample_files_are_pooled(tmp_path):
    """Scenario "Two draws from one result are one sample": 600 + 400 records marked 50,000 count as about 50,000;
    a third, complete file makes the platform mixed: its records plus 50,000, and no recall."""
    rows = SAMPLE_ROWS[1:1001]
    p, (a, b) = _oa(tmp_path, "p", [("openalex", rows[:600]), ("openalex", rows[600:])], budget=5000)
    p = _mark(_mark(p, a, 50000), b, 50000)
    bud = diagnostics.budget(p, 1)
    assert bud["records"] == 50000 and bud["text"].startswith("about 50,000 records, 45,000 over"), bud["text"]
    lines = diagnostics.diagnose_lines(diagnostics.what_did_what(p))
    assert any(x.startswith("openalex: 1,000 records imported in 2 files, a random sample of 50,000 results")
               for x in lines), lines
    p, (a, b, c) = _oa(tmp_path, "m", [("openalex", rows[:600]), ("openalex", rows[600:]),
                                       ("openalex", SAMPLE_ROWS[1001:1031])], budget=5000)
    p = _mark(_mark(p, a, 50000), b, 50000)
    sw = diagnostics.sample_weights(p, 1)["openalex"]
    assert sw["mixed"] and sw["estimated"] == 50030 and sw["sample"] is None
    d = diagnostics.what_did_what(p)
    pl = d["platforms"]["openalex"]
    assert pl["mixed"] and all(r.get("sampled") for r in pl["recall"].values()), pl["recall"]
    assert not any("development seeds found" in x for x in diagnostics.diagnose_lines(d))
    edits = [e for e in diagnostics.suggested_edits(p)["edits"] if (e.get("records_removed") or {}).get("openalex")]
    assert edits and not any("openalex" in (e.get("removed_interval") or {}) for e in edits)  # mixed: no interval


def test_sampled_platform_reports_no_recall(tmp_path):
    """Scenario "Seeds outside a sample are not missed": 3 of 10 development seeds in a sample; the seeds line has no
    interval and no other seed is offered for the indexing check."""
    from wider.search import indexing

    words = ["amber", "basalt", "cobalt", "dune", "ember", "flint", "garnet", "heron", "indigo", "juniper"]
    seeds = [BenchmarkPaper(benchmark_id=f"B{i:03d}", title=f"{w.title()} {w} {w} beacon fog", doi=f"10.1000/k{i}",
                            set="Development") for i, w in enumerate(words, 1)]
    rows = [(s.title, "A beacon in fog.", s.doi) for s in seeds[:3]] + SAMPLE_ROWS[1:200]
    p, (sid,) = _oa(tmp_path, "r", [("openalex", rows)], seeds=seeds)
    p = _mark(p, sid, 650000)
    d = diagnostics.what_did_what(p)
    lines = diagnostics.diagnose_lines(d)
    assert ("  development seeds in the sample: 3 of 10 (a seed not in the sample may still be retrieved; recall not "
            "measured)") in lines, lines
    assert not any("interval" in x and "seeds" in x for x in lines)
    assert d["platforms"]["openalex"]["indexing"] == {}
    assert "openalex" not in indexing.known_item_strings(p, 1)["platforms"]   # outside the catalogue
    p, (sid,) = _oa(tmp_path, "rs", [("scopus", rows)], seeds=seeds)   # a sampled catalogue platform
    k = indexing.known_item_strings(_mark(p, sid, 650000), 1, "scopus")["platforms"]["scopus"]
    assert k["seeds"] == [] and k["note"] == indexing.SAMPLED


def test_edits_rank_by_estimated_records_saved(tmp_path):
    """Scenario "Edits ranked by estimated records saved": X drops 30 records of a complete Scopus export, Y 20 of an
    OpenAlex sample of 1,000 out of 100,000; Y is first once marked, X before; Y carries N x the exact interval."""
    syn = {"blocks": [{"key": "a", "name": "Lights", "terms": ["lighthouse*", "beacon", "chart*", "harbour*"]},
                      {"key": "b", "name": "Weather", "terms": ["fog", "storm*"]}], "logic": {"and": ["a", "b"]}}
    scopus = [LIGHT_ROWS[0]] + [(f"Charts of the storm coast {i}", "Survey.", f"10.1000/x{i}") for i in range(30)]
    oa = [LIGHT_ROWS[0]] + [(f"Harbour storm notes {i}", "Survey.", f"10.1000/y{i}") for i in range(20)] + [
        (f"Beacon storm log {i}", "A beacon note.", f"10.1000/z{i}") for i in range(979)]
    from wider.ingest.importer import add_file, import_all, list_sources

    p = Project.init(tmp_path / "e", platforms=["scopus"])
    p.write_benchmark([LIGHT_SEED])
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    add_file(p, "scopus", _export(tmp_path / "e0.csv", scopus))
    add_file(p, "openalex", _export(tmp_path / "e1.csv", oa))
    import_all(p)
    _cli(p, "protocol", "set", "identification.record_budget", "100")
    p = Project.load(p.root)
    sid = next(s["source_id"] for s in list_sources(p)["sources"] if s["platform"] == "openalex")

    def drops(p):
        res = diagnostics.suggested_edits(p)
        return [e for e in res["edits"] if e["kind"] == "drop_term" and e["change"]["term"] in ("chart*", "harbour*")]

    assert [e["change"]["term"] for e in drops(p)] == ["chart*", "harbour*"]
    p = _mark(p, sid, 100000)
    y, x = drops(p)
    assert (y["change"]["term"], x["change"]["term"]) == ("harbour*", "chart*")
    lo, hi = clopper_pearson(20, 1000)
    assert y["records_removed"]["openalex"] == 2000 and not y["records_removed"].get("scopus")
    assert y["records_removed_sample"]["openalex"] == 20
    assert y["removed_interval"] == {"openalex": [round(100000 * lo), round(100000 * hi)]}
    text = diagnostics.edit_lines(diagnostics.suggested_edits(p))
    ly = next(t for t in text if "harbour*" in t)
    assert ("-about 2,000 records on openalex (20 of 1,000 in a random sample of 100,000; 95% interval "
            f"{diagnostics.about(100000 * lo)} to {diagnostics.about(100000 * hi)})") in ly, ly
    assert "-30 records on scopus" in next(t for t in text if "chart*" in t)
