"""Papers beyond the query (design D22-D28): ranked candidates and snowball rounds, candidates from a file."""
from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from tests.unit.test_dry_run import _cli
from tests.unit.test_seed_mining import _chained, _openalex, _proj
from wider.core.project import Project
from wider.ui.app import create_app


def _snowball_openalex(request):
    """`_openalex` plus the paper Y as a seed: Y cites X (already a candidate) and N (new); nothing cites Y."""
    flt = request.url.params.get("filter", "")
    n = {"id": "https://openalex.org/N", "doi": "https://doi.org/10.1000/n1", "title": "Pair drift measured",
         "publication_year": 2021}
    x = {"id": "https://openalex.org/X", "doi": "https://doi.org/10.1000/abc", "title": "Team meetings after errors",
         "publication_year": 2020}
    extra = {"doi:10.1000/y1": [{"id": "https://openalex.org/WY", "doi": "https://doi.org/10.1000/y1"}],
             "cited_by:WY": [x, n], "cites:WY": []}
    if flt in extra:
        return httpx.Response(200, json={"results": extra[flt], "meta": {"next_cursor": None}})
    return _openalex(request)


def test_candidates_ranked_and_snowball(tmp_path, monkeypatch):
    """Scenario "Ranked candidates and a snowball round" (construct-workbench, D23)."""
    from tests.unit.helpers import mock_client
    from wider.seeds import chain

    calls: list = []
    p = _chained(tmp_path, monkeypatch, "a", calls)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(_snowball_openalex, calls))
    r = _cli(p, "benchmark", "chain")
    assert r.exit_code == 0 and "round 1:" in r.output, r.output
    assert chain.RANK_NOTE in " ".join(r.output.split())
    rows = chain.candidates(p)
    # most linking seeds first (direct links only), then the newest of the one-link papers
    assert [(c["title"], c["rank"], c["n_linking"]) for c in rows] == [
        ("Why pairs drift", 1, 2), ("Coupling revisited", 2, 1), ("Team meetings after errors", 3, 1)]
    assert {c["chain_round"] for c in rows} == {1} and all(c["origins"] == ["openalex"] for c in rows)
    # one link each: found in both directions ranks first, then the newest
    keyed = sorted([{"n_linking": 1, "direction": "references", "year": 2024, "candidate_id": 1},
                    {"n_linking": 1, "direction": "both", "year": 2001, "candidate_id": 2},
                    {"n_linking": 3, "direction": "cited_by", "year": None, "candidate_id": 3}], key=chain._rank_key)
    assert [k["candidate_id"] for k in keyed] == [3, 2, 1]
    ids = {c["title"]: c["candidate_id"] for c in rows}

    # the researcher takes Y into Development and Z into Validation, then chains again from the added papers
    assert _cli(p, "benchmark", "add", "--from-candidate", str(ids["Why pairs drift"]), "--set",
                "Development").exit_code == 0
    assert _cli(p, "benchmark", "add", "--from-candidate", str(ids["Coupling revisited"]), "--set",
                "Validation").exit_code == 0
    sent_before = len(calls)
    r = _cli(p, "benchmark", "chain", "--from-candidates")
    assert r.exit_code == 0, r.output
    assert "round 2: chained 1 Development seed(s)" in r.output and "1 new candidate(s)" in r.output
    assert "1 candidate in Validation not chained" in r.output
    sent = " ".join(str(c.url) for c in calls[sent_before:])
    assert "10.1000%2Fy1" in sent or "10.1000/y1" in sent
    for doi in ("10.1000/a1", "10.1000/a2", "10.1000/z1", "10.1000/v1"):   # chained seeds and Validation: not sent
        assert doi not in sent and doi.replace("/", "%2F") not in sent
    rows = {c["title"]: c for c in chain.candidates(p)}
    assert rows["Pair drift measured"]["chain_round"] == 2   # 1 + the generation of its only linking seed (Y: 1)
    y_id = rows["Why pairs drift"]["benchmark_id"]
    assert rows["Pair drift measured"]["linking_seeds"] == [y_id]   # the new work links to the added paper's id
    assert rows["Team meetings after errors"]["linking_seeds"] == ["B001", y_id]
    assert rows["Team meetings after errors"]["chain_round"] == 1   # a known row keeps its round
    assert rows["Team meetings after errors"]["rank"] == 1   # two links now
    assert [c["title"] for c in chain.candidates(p, 2)] == ["Pair drift measured"]

    # every seed chained in both directions: nothing is sent and the round says so
    sent_before = len(calls)
    r = _cli(p, "benchmark", "chain", "--from-candidates")
    assert r.exit_code == 0 and "round 3: nothing to chain" in r.output, r.output
    assert len(calls) == sent_before

    # round 2 looked at and nothing added: the stop notice, as information; another round is still allowed
    assert chain.stop_notice(p) is None
    assert _cli(p, "benchmark", "candidates", "--dismiss", str(rows["Pair drift measured"]["candidate_id"])).exit_code == 0
    assert chain.stop_notice(p) == "round 2 added no paper; snowballing usually stops here"
    r = _cli(p, "benchmark", "candidates", "--round", "2")
    assert r.exit_code == 0 and "snowballing usually stops here" in r.output
    r3 = _cli(p, "benchmark", "chain", "--from-candidates")   # the command says it too, beside round 3
    flat = " ".join(r3.output.split())
    assert r3.exit_code == 0 and "round 3: nothing to chain" in flat and "round 2 added no paper" in flat, flat
    assert "Pair drift measured" in r.output and "Why pairs drift" not in r.output
    assert [x["round"] for x in chain.rounds(p)] == [1, 2]

    # the route gives the same list, rank, round and notice; the chain route takes from_candidates
    client = TestClient(create_app(Project.load(p.root)))
    got = client.get("/api/benchmark/candidates", params={"round": 2}).json()
    assert [c["title"] for c in got["candidates"]] == ["Pair drift measured"]
    assert got["stop_notice"] == chain.stop_notice(p) and got["rank_note"] == chain.RANK_NOTE
    assert [x["round"] for x in got["rounds"]] == [1, 2]
    res = client.post("/api/benchmark/chain", json={"from_candidates": True})
    assert res.status_code == 200 and "round 3: nothing to chain" in res.json()["messages"]
    j = json.loads(_cli(p, "benchmark", "candidates", "--json").output)
    assert [c["rank"] for c in j["candidates"]] == [c["rank"] for c in client.get("/api/benchmark/candidates").json()[
        "candidates"]]


RIS = """TY  - JOUR
TI  - Pairs in sync
PY  - 2022
DO  - 10.1000/p1
AB  - Two people coordinate.
KW  - dyads
KW  - synchrony
ER  -
TY  - JOUR
TI  - Alpha coupling in pairs
DO  - 10.1000/a1
ER  -
TY  - JOUR
TI  - Quiet coupling
PY  - 2021
DO  - 10.1000/q1
ER  -
"""
CSV = """title,doi,year,cites
Pairs in sync,10.1000/p1,2022,10.1000/a1
Team meetings after errors,10.1000/abc,2020,
,,2020,
"""


def test_candidate_import_from_file(tmp_path, monkeypatch):
    """Scenario "Candidates from a file, nothing sent" (construct-workbench, D22, D23)."""
    from tests.unit.helpers import no_requests
    from tests.unit.test_seed import _imported_proj
    from wider.core import journal
    from wider.seeds import chain

    (tmp_path / "r").mkdir()
    p = _imported_proj(_proj(tmp_path / "r"), tmp_path / "r")   # no online-lookup consent at all
    tried = no_requests(monkeypatch)
    f = tmp_path / "elicit.ris"
    f.write_text(RIS, encoding="utf-8")
    r = _cli(p, "benchmark", "candidates", "--import", str(f), "--origin", "elicit")
    assert r.exit_code == 0, r.output
    assert "2 new candidate(s)" in r.output and "nothing was sent" in r.output
    assert "1 record already seed papers, skipped" in r.output
    rows = {c["title"]: c for c in chain.candidates(p)}
    assert set(rows) == {"Pairs in sync", "Quiet coupling"}
    assert all(c["origins"] == ["file:elicit"] and c["chain_round"] == 0 and not c["retrieved"] for c in rows.values())
    assert all(c["good_validation"] for c in rows.values())   # the query missed both: the mark the list shows
    assert " ".join(_cli(p, "benchmark", "candidates").output.split()).count("good validation candidate") == 2
    assert rows["Pairs in sync"]["keywords"] == ["dyads", "synchrony"]
    assert rows["Pairs in sync"]["abstract"] == "Two people coordinate."
    [ch] = journal.changes(p, kind="candidate_import")
    assert ch["author"] and "import 1: 2 new candidate(s)" in ch["summary"] and "file:elicit" in ch["summary"]
    [imp] = chain.imports(p)
    assert (imp["import_no"], imp["origin"], imp["file_name"], imp["n_new"], imp["still_new"]) == (
        1, "file:elicit", "elicit.ris", 2, 2)
    assert len(imp["sha256"]) == 64

    # the same file again adds nothing, says so and records no import
    r = _cli(p, "benchmark", "candidates", "--import", str(f), "--origin", "elicit")
    assert r.exit_code == 0 and "nothing new" in r.output and len(chain.imports(p)) == 1
    # another tool's CSV through the route: a known row gains the origin and its linking seed (a `cites` column),
    # a new one the query retrieved carries the mark, a record with no DOI or title is skipped
    (tmp_path / "chaser.csv").write_text(CSV, encoding="utf-8")
    client = TestClient(create_app(Project.load(p.root)))
    res = client.post("/api/benchmark/candidates/import", files={"file": ("chaser.csv", CSV.encode())},
                      data={"origin": "citationchaser"})
    assert res.status_code == 200, res.text
    assert (res.json()["merged"], res.json()["new"], res.json()["import_no"]) == (1, 1, 2)
    assert "1 record without a DOI or title, skipped" in res.json()["messages"]
    rows = {c["title"]: c for c in res.json()["candidates"]}
    sync = rows["Pairs in sync"]
    assert sync["origins"] == ["file:elicit", "file:citationchaser"]
    assert (sync["linking_seeds"], sync["direction"]) == (["B001"], "cited_by")   # it cites the seed B001
    assert rows["Team meetings after errors"]["retrieved"]   # the query retrieved it: not a good validation candidate
    assert not list((p.root / "inputs" / ".candidates").glob("*"))   # the upload is not kept
    assert client.post("/api/benchmark/candidates/import", files={"file": ("x.csv", CSV.encode())},
                       data={"origin": "bad label!"}).status_code == 400
    assert _cli(p, "benchmark", "candidates", "--import", str(f), "--origin", "bad label!").exit_code == 1
    assert _cli(p, "benchmark", "candidates", "--import", str(f)).exit_code == 1   # --origin is required

    # adding: none linked -> other_database with the origin in its note; linked to a seed -> seed_chain:B001
    quiet, _ = chain.add(p, rows["Quiet coupling"]["candidate_id"], "Validation")
    assert (quiet.set, quiet.source) == ("Validation", "other_database") and "file:elicit" in quiet.note
    assert chain.add(p, sync["candidate_id"], "Development")[0].source == "seed_chain:B001"
    # --dismiss-import 1: its rows still new are dismissed (none left here), the added ones stay
    r = _cli(p, "benchmark", "candidates", "--dismiss-import", "1")
    assert r.exit_code == 0 and "0 candidate(s) dismissed" in r.output, r.output

    # an import is journalled: undone, its rows and its import row go
    (tmp_path / "q").mkdir()
    q = _imported_proj(_proj(tmp_path / "q"), tmp_path / "q")
    assert _cli(q, "benchmark", "candidates", "--import", str(f), "--origin", "elicit").exit_code == 0
    [ch] = journal.changes(q, kind="candidate_import")
    journal.undo(q, ch["change_id"])
    assert chain.candidates(q) == [] and chain.imports(q) == []
    # dismissing an import removes only its rows still new, and Undo puts them back
    assert _cli(q, "benchmark", "candidates", "--import", str(f), "--origin", "elicit").exit_code == 0
    first = next(c for c in chain.candidates(q) if c["title"] == "Pairs in sync")
    chain.add(q, first["candidate_id"], "Development")
    r = _cli(q, "benchmark", "candidates", "--dismiss-import", "1")
    assert r.exit_code == 0 and "1 candidate(s) dismissed" in r.output, r.output
    status = {c["title"]: c["status"] for c in chain.candidates(q)}
    assert status == {"Pairs in sync": "added", "Quiet coupling": "dismissed"}
    assert chain.imports(q)[0]["still_new"] == 0
    [d] = journal.changes(q, kind="candidate_import_dismiss")
    journal.undo(q, d["change_id"])
    assert {c["title"]: c["status"] for c in chain.candidates(q)}["Quiet coupling"] == "new"
    assert _cli(q, "benchmark", "candidates", "--dismiss-import", "9").exit_code == 1
    assert TestClient(create_app(Project.load(q.root))).post(
        "/api/benchmark/candidates/imports/9/dismiss").status_code == 404
    assert tried == []


CSV_V = """title,doi,year,abstract,cites
Beta lattice in dyads,10.1000/c1,2020,A synthetic abstract.,10.1000/v1;10.1000/v2
Beta lattice and team timing,10.1000/c2,2021,A synthetic abstract.,10.1000/v1;10.1000/a1
"""


def test_validation_seeds_never_link_a_candidate(tmp_path):
    """D24, risk-checker R1: a Validation seed DOI in an import's cites column links nothing, so it never ranks a
    candidate, lifts it over --min-links for the names block or becomes the added paper's source; a stored link to a
    seed moved to Validation since is dropped on read."""
    from tests.unit.test_seed_mining import SYN
    from wider.construct import artifacts
    from wider.construct.benchmark import sync
    from wider.core.models import BenchmarkPaper
    from wider.seeds import chain
    from wider.seeds import missed_names as mn

    p = _proj(tmp_path)
    p.write_benchmark(p.read_benchmark() + [BenchmarkPaper(benchmark_id="V002", title="Second validation",
                                                           doi="10.1000/v2", set="Validation")])
    sync(p)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    f = tmp_path / "tool.csv"
    f.write_text(CSV_V, encoding="utf-8")
    chain.import_candidates(p, f, "tool")
    rows = {c["title"]: c for c in chain.candidates(p)}
    assert rows["Beta lattice in dyads"]["linking_seeds"] == [] and rows["Beta lattice in dyads"]["n_linking"] == 0
    assert rows["Beta lattice and team timing"]["linking_seeds"] == ["B001"]
    assert [r["rank"] for r in chain.candidates(p)] == [1, 2]
    assert rows["Beta lattice and team timing"]["rank"] == 1
    assert "V00" not in json.dumps([r[0] for r in p.store.query("SELECT linking_seeds_json FROM seed_chain_candidates")])
    assert mn.missed_names(p)["names"] == []   # one Development link each at most: below --min-links 2
    a, _ = chain.add(p, rows["Beta lattice in dyads"]["candidate_id"], "Development")
    assert a.source == "other_database"
    # a seed moved to Validation after it linked a candidate
    chain._store(p, {"doi:10.1000/zz": {"doi": "10.1000/zz", "title": "Later work", "title_norm": "later work",
                                         "year": 2022, "dirs": {"references"}, "seeds": ["B002", "B001"], "round": 1}})
    papers = [b.model_copy(update={"set": "Validation"}) if b.benchmark_id == "B002" else b for b in p.read_benchmark()]
    p.write_benchmark(papers)
    sync(p)
    later = next(c for c in chain.candidates(p) if c["title"] == "Later work")
    assert later["linking_seeds"] == ["B001"] and later["n_linking"] == 1
    b, _ = chain.add(p, later["candidate_id"], "Development")
    assert b.source == "seed_chain:B001"


def test_import_undo_survives_a_later_chaining_run(tmp_path):
    """D23, risk-checker R4: undo removes an import whose rows are unchanged, even after a chaining run added new
    candidates of its own; those stay."""
    from wider.core import journal
    from wider.seeds import chain

    p = _proj(tmp_path)
    f = tmp_path / "e.ris"
    f.write_text(RIS, encoding="utf-8")
    chain.import_candidates(p, f, "elicit", by="Ana")
    chain._store(p, {"doi:10.1000/zz": {"doi": "10.1000/zz", "title": "Later work", "title_norm": "later work",
                                         "year": 2022, "dirs": {"references"}, "seeds": ["B001"], "round": 1}})
    [ch] = journal.changes(p, kind="candidate_import")
    assert ch["undoable"], ch["why_not"]
    journal.undo(p, ch["change_id"], author="Ana")
    assert [c["title"] for c in chain.candidates(p)] == ["Later work"] and chain.imports(p) == []


def test_empty_import_file_says_so(tmp_path):
    """ui-tester F3: a file with no records says so, not "every record is already a candidate"; nothing is journalled."""
    from wider.core import journal
    from wider.seeds import chain

    p = _proj(tmp_path)
    for name, text in (("empty.ris", ""), ("empty.csv", "title,doi\n")):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        assert chain.import_candidates(p, f, "x")["messages"] == [f"nothing imported: {name} holds no records"]
    assert journal.changes(p, kind="candidate_import") == [] and chain.imports(p) == []


def test_ranked_list_import_and_snowball_on_the_setup_page():
    """The Setup list (D23): rank, linking seeds, direction, round and origin per row, the rank note and the stop
    notice; "Chain again from added papers" posts from_candidates, "Import candidates" posts the file with its origin,
    "Dismiss this import" posts to its route, the round filter asks for that round."""
    from tests.unit.test_seed import _seed_page

    cands = [{"candidate_id": 4, "rank": 1, "title": "Pairs in sync", "doi": "10.1000/p1", "year": 2022,
              "direction": "cited_by", "linking_seeds": ["B001", "B002"], "status": "new", "retrieved": False,
              "good_validation": True, "chain_round": 0, "origins": ["openalex", "file:citationchaser"]},
             {"candidate_id": 2, "rank": 2, "title": "Pair drift measured", "doi": "10.1000/n1", "year": 2021,
              "direction": "references", "linking_seeds": ["B004"], "status": "new", "retrieved": False,
              "chain_round": 2, "origins": ["openalex"]}]
    listing = {"candidates": cands, "rank_note": "ranked by links to your seeds; a paper with one link may still be "
               "relevant", "stop_notice": "round 2 added no paper; snowballing usually stops here",
               "rounds": [{"round": 0, "n": 1, "added": 0, "new": 1, "dismissed": 0},
                          {"round": 2, "n": 1, "added": 0, "new": 1, "dismissed": 0}],
               "imports": [{"import_no": 1, "origin": "file:citationchaser", "file_name": "chaser.ris", "n_new": 1,
                            "still_new": 1, "by": "Reviewer A", "at": "2026-10-23T10:00:00Z"}]}
    body = """
      Object.assign(replies, {"/api/benchmark/candidates": LISTING,
        "/api/benchmark/chain": {round: 3, seeds_chained: 0, found: 0, new: 0, messages: ["round 3: nothing to chain"]},
        "/api/benchmark/candidates/import": {import_no: 2, n_records: 3, file: "rabbit.ris", new: 1, merged: 1,
                                             messages: []},
        "/api/benchmark/candidates/imports/1/dismiss": {import_no: 1, dismissed: 1}});
      global.FormData = class { constructor() { this.e = []; }
        append(k, v) { this.e.push([k, typeof v === "string" ? v : v.name]); }
        toString() { return JSON.stringify({form: this.e}); } };
      await window.loadMore(); await flush();
      const html = $("#seed-chain").innerHTML;
      $("#chain-dir").value = "both"; await $("#chain-again").onclick(); await flush();
      const againMsg = $("#chain-msg").innerHTML;
      $("#cand-file").files = [{name: "rabbit.ris"}]; $("#cand-origin").value = " researchrabbit ";
      await $("#cand-import").onclick(); await flush();
      const importMsg = $("#chain-msg").innerHTML;
      await click("#seed-chain", "[data-import-dismiss]");
      const dismissMsg = $("#chain-msg").innerHTML;
      $("#cand-round").value = "2"; await $("#cand-round").onchange(); await flush();
      console.log(JSON.stringify({html, calls, againMsg, importMsg, dismissMsg}));
    """.replace("LISTING", json.dumps(listing))
    out = _seed_page([], body)
    html = out["html"]
    row = html[html.index("Pairs in sync"):html.index("</tr>", html.index("Pairs in sync"))]
    assert "B001, B002" in row and "it cites a seed" in row and "openalex, file:citationchaser" in row
    assert "good validation candidate" in row and "good validation candidate" not in html[html.index("Pair drift"):]
    assert "<td>1</td><td>4</td>" in html.replace("\n", "").replace("  ", "")   # rank, then the candidate number
    assert listing["rank_note"] in html
    assert "<b>round 2 added no paper; snowballing usually stops here</b>" in html   # bold, not grey
    assert "Nothing is sent" in html and "Dismiss this import" in html
    posts = [c for c in out["calls"] if c[1] == "POST"]
    assert posts == [["/api/benchmark/chain", "POST", {"direction": "both", "from_candidates": True}],
                     ["/api/benchmark/candidates/import", "POST",
                      {"form": [["file", "rabbit.ris"], ["origin", "researchrabbit"]]}],
                     ["/api/benchmark/candidates/imports/1/dismiss", "POST", {}]]
    assert out["againMsg"] == "round 3: 0 development seed(s) chained: 0 works, 0 new; round 3: nothing to chain"
    assert out["importMsg"] == ("import 2: 3 record(s) from rabbit.ris: 1 new, 1 known candidate(s) gained the origin; "
                                "nothing was sent")
    assert out["dismissMsg"].startswith("import 1: 1 candidate(s) dismissed")
    assert out["calls"][-1][0] == "/api/benchmark/candidates?round=2"


# ----------------------------------------------------------------- names from connected papers the query misses (D24)
def _connected(tmp_path, name="m"):
    """B001 is a hit of block a ("alpha coupling"), B002 a miss; four chaining candidates the query misses: two titles
    hold "beta lattice", one does not, and one holding it is the Validation seed V001; a dismissed one holds it too."""
    from tests.unit.test_seed_mining import SYN
    from wider.construct import artifacts
    from wider.construct.benchmark import sync

    p = _proj(tmp_path, name)
    sync(p)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    rows = [("Beta lattice in dyads", "new", None, None, "10.1000/c1"),
            ("Beta lattice and team timing", "new", None, None, "10.1000/c2"),
            ("Gamma rhythm in groups", "new", None, None, "10.1000/c3"),
            ("Beta lattice in the validation sample", "added", "V001", None, "10.1000/v1"),
            ("Beta lattice dismissed here", "dismissed", None, None, "10.1000/c5")]
    for i, (title, status, bid, _x, doi) in enumerate(rows, 1):
        p.store.execute("INSERT INTO seed_chain_candidates(candidate_id, doi, title, title_norm, year, direction, "
                        "linking_seeds_json, n_linking, status, benchmark_id, origins_json, chain_round, abstract) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (i, doi, title, title.lower(), 2020, "both", '["B001", "B002"]', 2, status, bid,
                         '["openalex"]', 1, "A synthetic abstract."))
    return p


def test_names_from_missed_candidates(tmp_path):
    """Scenario "Names from missed connected papers" (query-generation, D24)."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from wider.construct import artifacts
    from wider.core import journal
    from wider.seeds import missed_names as mn

    p = _connected(tmp_path)
    bench = (p.root / "benchmark.csv").read_text(encoding="utf-8")
    d = mn.missed_names(p)
    assert d["platform"] == "scopus" and d["evaluated"] == 3 and d["missed"] == 3
    assert d["left_out"] == {"validation": 1, "dismissed": 1, "few_links": 0} and d["uncertain"] == 0
    assert [n["name"] for n in d["names"]] == ["beta lattice"]   # "beta", "lattice": inside it, same candidates
    n = d["names"][0]
    assert n["block"] == "a" and n["turns"] == ["C1", "C2"] and n["gains"] == []
    assert n["line"] == "turns 2 connected candidates into hits, +0 development seeds; connected, not judged"
    assert [q["title"] for q in n["candidates"]] == ["Beta lattice in dyads", "Beta lattice and team timing"]
    assert n["note"] == "from connected candidates C1, C2"
    out = "\n".join(mn.lines(d))
    assert "validation sample" not in out and "C4" not in out and "dismissed here" not in out
    # CLI and route give the same list
    r = _cli(p, "query", "missed-names")
    assert r.exit_code == 0, r.output
    flat = " ".join(r.output.split())
    assert "beta lattice → block a: turns 2 connected candidates into hits, +0 development seeds; connected, not " \
           "judged" in flat and "“Beta lattice in dyads”" in flat and "validation sample" not in flat
    assert "left out: 1 Validation, 1 dismissed" in flat
    c = TestClient(create_app(p))
    got = c.get("/api/query/missed-names").json()
    assert [x["name"] for x in got["names"]] == ["beta lattice"] and got["names"][0]["line"] == n["line"]
    assert c.get("/api/query/missed-names?min_links=0").status_code == 400
    assert _cli(p, "query", "missed-names", "--min-links", "3").output.count("beta lattice") == 0   # links 2 < 3
    # the block on the Search strings page shows the same, the Validation title nowhere
    html = run_node(ESC + PAGE + (STATIC / "diagnose.js").read_text(encoding="utf-8") +
                    f"\nconsole.log(JSON.stringify({{ html: Diagnosis.missedHtml({json.dumps(got)}) }}));")["html"]
    assert "Names from connected papers" in html and "turns 2 connected candidates into hits" in html
    assert "connected, not judged" in html and "Beta lattice in dyads" in html and "validation sample" not in html
    assert 'data-missed-add="0"' in html and "from connected candidates C1, C2; " in html

    # nothing changed: no new version, benchmark.csv as it was
    assert len(artifacts.list_versions(p, "synonyms")) == 1
    assert (p.root / "benchmark.csv").read_text(encoding="utf-8") == bench
    # the researcher admits it with a note: who and why are recorded
    r = _cli(p, "seeds", "names", "--admit", "beta lattice", "--block", "a", "--note",
             n["note"] + "; the lattice papers name our construct", "--by", "Ana")
    assert r.exit_code == 0, r.output
    ch = journal.changes(p, kind="name_admit")[0]
    assert ch["author"] == "Ana" and "beta lattice" in ch["summary"]
    dec = p.store.query("SELECT author, consequence FROM decisions WHERE kind='name_admit'")[0]
    assert dec["author"] == "Ana" and "from connected candidates C1, C2; the lattice papers" in dec["consequence"]
    assert len(artifacts.list_versions(p, "synonyms")) == 2
    assert (p.root / "benchmark.csv").read_text(encoding="utf-8") == bench
    assert mn.missed_names(p)["names"] == []   # the query now holds it
    # a candidate without an abstract is uncertain in the dry run: counted and said, not used
    p.store.execute("UPDATE seed_chain_candidates SET abstract=NULL WHERE candidate_id=3")
    d = mn.missed_names(p)
    assert d["uncertain"] == 1 and "1 candidate(s) uncertain in the dry run" in "\n".join(mn.lines(d))


def test_names_block_actions_on_the_page(tmp_path):
    """The names block's actions (D24): "Use the model" posts model true, "Add to block" asks first (a cancel saves
    nothing) and then posts the name, its block and the researcher's edited note to the admission route."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    got = TestClient(create_app(_connected(tmp_path, "page"))).get("/api/query/missed-names").json()
    admit = {"version": 2, "note": "from connected candidates C1, C2; the lattice papers name our construct",
             "notice": ""}
    body = """
      Object.assign(replies, {"/api/query/missed-names": GOT, "/api/seeds/names/admit": ADMIT});
      let answer = false; const asked = [];
      window.Refine = { confirmSave: async (...a) => { asked.push(a); return answer; } };
      $("#missed-min").value = "2"; $("#missed-model").checked = true;
      await Diagnosis.runMissed(); await flush();
      $('[data-missed-note="0"]').value = "from connected candidates C1, C2; the lattice papers name our construct; ";
      await click("#missed-out", "[data-missed-add]");
      const cancelled = $("#missed-msg").textContent;
      answer = true;
      await click("#missed-out", "[data-missed-add]");
      console.log(JSON.stringify({calls, cancelled, asked, msg: $("#missed-msg").textContent}));
    """.replace("GOT", json.dumps(got)).replace("ADMIT", json.dumps(admit))
    out = run_node(ESC + PAGE + (STATIC / "diagnose.js").read_text(encoding="utf-8") + body)
    posts = [c for c in out["calls"] if c[1] == "POST"]
    assert posts[0] == ["/api/query/missed-names", "POST",
                        {"version": None, "platform": None, "min_links": 2, "model": True}]
    assert out["cancelled"] == "Nothing saved."
    assert out["asked"][0] == ["synonyms", 'Add "beta lattice" to block a?', "Add", True]   # asks even without a notice
    admits = [c for c in posts if c[0] == "/api/seeds/names/admit"]
    assert admits == [["/api/seeds/names/admit", "POST", {"name": "beta lattice", "block": "a", "note": admit["note"]}]]
    assert out["msg"] == "synonyms v2 saved: " + admit["note"]


def test_missed_names_model_pass_is_verbatim(tmp_path):
    """Scenario "A model name not in the text is dropped" (query-generation, D24, D22)."""
    from tests.unit.test_seed_mining import _fake
    from wider.seeds import missed_names as mn

    p = _connected(tmp_path, "mm")
    p.store.execute("UPDATE seed_chain_candidates SET title='Moods of pairs', abstract='We study the beta lattice "
                    "of pairs.' WHERE candidate_id=1")
    p.store.execute("UPDATE seed_chain_candidates SET title='Timing in teams' WHERE candidate_id=2")
    seen = []

    def answer(req):
        seen.append(req.user)
        if "Candidate C1" in req.user:
            return {"names": [{"block": "a", "phrase": "beta lattice", "candidate_id": "C1"},
                              {"block": "a", "phrase": "gamma field", "candidate_id": "C1"}]}
        return {"names": []}

    fake = _fake(answer)
    d = mn.missed_names(p, model=True, provider=fake)
    m = d["model"]
    assert m["ran"] and m["dropped"] == 1 and m["kept"] == 1 and "1 dropped: not in the candidate's text" in m["message"]
    assert [x["name"] for x in d["names"]] == ["beta lattice"] and d["names"][0]["origin"] == ["llm"]
    assert d["names"][0]["candidates"][0]["candidate_id"] == "C1" and d["names"][0]["block"] == "a"
    assert len(seen) == 3 and not [u for u in seen if "validation sample" in u or "dismissed here" in u]
    assert all("text" not in k for c in m["calls"] for k in c)   # hashes and counts, no text
    stages = {r["stage"] for r in p.store.query("SELECT stage FROM llm_usage")}
    assert "missed_names" in stages
    # without the model the text pass alone: no two titles share a name
    assert mn.missed_names(p)["names"] == [] and mn.missed_names(p)["model"] is None


# ------------------------------------------------------------------------------ seed reference lists (D26)
REFS = ["Synthetic Signals Review 2026", "References",
        "Abel, A. (2019). Alpha coupling in paired signals. Journal of Synthetic Signals, 12, 1-10. doi:10.1000/r1",
        "Berg, B. (2020). Alpha coupling and beta drift. Journal of Synthetic Signals, 13, 11-20. doi:10.1000/r2",
        "Chen, C. (2021). Phase binding at scale. Proceedings on Phase Studies, 4, 21-30.",
        "Diaz, D. (2018). Signals in pairs revisited. Journal of Synthetic Signals, 9, 5-9. doi:10.1000/r4",
        "Evans, E. (2022). Drift without coupling. Proceedings on Phase Studies, 5, 1-4. doi:10.1000/r5"]


def _refs_openalex(request):
    """B001 -> W1 cites four works (Evans 2022 is not among them); B002 -> W2 cites Y; nothing else is known."""
    flt = request.url.params.get("filter", "")

    def w(k, doi, title, year):
        return {"id": f"https://openalex.org/{k}", "doi": f"https://doi.org/{doi}", "title": title,
                "publication_year": year}
    body = {"doi:10.1000/a1": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/a1"}],
            "doi:10.1000/a1|10.1000/a2": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/a1"},
                                          {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1000/a2"}],
            "cited_by:W1": [w("R1", "10.1000/r1", "Alpha coupling in paired signals", 2019),
                            w("R2", "10.1000/r2", "Alpha coupling and beta drift", 2020),
                            w("R3", "10.1000/r3", "Phase binding at scale", 2021),
                            w("R4", "10.1000/r4", "Signals in pairs revisited", 2018)],
            "cited_by:W2": [w("Y", "10.1000/y1", "Why pairs drift", 2019)], "cites:W1": [], "cites:W2": []}
    if flt not in body:
        return httpx.Response(404)
    return httpx.Response(200, json={"results": body[flt], "meta": {"next_cursor": None}})


def _with_refs(tmp_path, monkeypatch, name, calls, handler=_refs_openalex):
    from tests.fixtures.seeds.make_synthetic_pdf import PAGE1, make_pdf
    from tests.unit.helpers import mock_client
    from wider.seeds import pdf

    p = _chained(tmp_path, monkeypatch, name, calls)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(handler, calls))
    pdf.add(p, "B001", make_pdf(tmp_path / f"{name}.pdf", (PAGE1, REFS)))
    return p


def test_reference_lists_openalex_first(tmp_path, monkeypatch):
    """Scenario "OpenAlex list first, the PDF fills the gap" (construct-workbench, D26)."""
    from wider.core import lookups
    from wider.seeds import chain, references

    calls: list = []
    p = _with_refs(tmp_path, monkeypatch, "a", calls)
    r = _cli(p, "benchmark", "references", "B001")
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "B001: OpenAlex 4 references, PDF 5 entries" in out
    assert "1 entry the PDF has and OpenAlex lacks:" in out and "Drift without coupling" in out and "10.1000/r5" in out
    cand = [c for c in chain.candidates(p) if c["doi"] == "10.1000/r5"]
    assert len(cand) == 1 and cand[0]["origins"] == ["pdf"] and cand[0]["direction"] == "references"
    assert cand[0]["linking_seeds"] == ["B001"] and cand[0]["chain_round"] == 1
    # the matched entries are no candidates; Chen 2021 (no DOI in the PDF) matched OpenAlex by its title
    assert not [c for c in chain.candidates(p) if c["doi"] in ("10.1000/r1", "10.1000/r2", "10.1000/r4")]
    by = {x["ref_index"]: x["matched_by"] for x in p.store.query(
        "SELECT ref_index, matched_by FROM seed_references WHERE benchmark_id='B001' AND route='pdf'")}
    assert by == {0: "doi", 1: "doi", 2: "title", 3: "doi", 4: None}
    sent = " ".join(str(c.url) for c in calls)
    assert calls and "10.1000%2Fv1" not in sent and "10.1000/v1" not in sent
    # a second run reuses the stored OpenAlex list: nothing is sent
    n = len(calls)
    assert references.seed_references(p, "B001")["seeds"][0]["openalex"]["source"] == "stored" and len(calls) == n
    # a validation seed is refused with the rule; its DOI is in no request
    r = _cli(p, "benchmark", "references", "V001")
    assert r.exit_code == 1 and "not mined, sent or counted" in " ".join(r.output.split())
    assert len(calls) == n
    # the panel's twin answers the same line
    client = TestClient(create_app(Project.load(p.root)))
    j = client.post("/api/benchmark/references", json={"benchmark_id": "B001"}).json()
    assert "B001: OpenAlex 4 references, PDF 5 entries" in j["lines"]
    assert client.post("/api/benchmark/references", json={"benchmark_id": "V001"}).status_code == 400

    # without the consent only the PDF route runs and nothing is sent; route openalex asks first (exit 2)
    calls2: list = []
    q = _with_refs(tmp_path, monkeypatch, "b", calls2)
    lookups.revoke(q)
    r = _cli(q, "benchmark", "references", "B001")
    assert r.exit_code == 0 and references.OFFLINE in " ".join(r.output.split()) and "B001: PDF 5 entries" in r.output
    assert _cli(q, "benchmark", "references", "B001", "--route", "openalex").exit_code == 2
    assert TestClient(create_app(Project.load(q.root))).post(
        "/api/benchmark/references", json={"route": "openalex"}).status_code == 403
    assert calls2 == []

    # OpenAlex failing: the failure is named and the PDF route still runs
    calls3: list = []
    f = _with_refs(tmp_path, monkeypatch, "c", calls3, handler=lambda req: httpx.Response(503))
    d = references.seed_references(f, "B001")
    assert d["seeds"][0]["line"] == "OpenAlex request failed, PDF 5 entries" and d["seeds"][0]["openalex"]["failed"]
    assert calls3

    # a backward chaining run keeps each seed's OpenAlex list, so the reference run sends nothing for it
    calls4: list = []
    g = _with_refs(tmp_path, monkeypatch, "d", calls4)
    assert _cli(g, "benchmark", "chain", "--direction", "references").exit_code == 0
    n = len(calls4)
    d = references.seed_references(g, "B001", route="openalex")
    assert d["seeds"][0]["line"] == "OpenAlex 4 references" and len(calls4) == n


def test_references_panel_on_the_setup_page(tmp_path, monkeypatch):
    """The References panel of the seed step (D26) draws the route's real answer: it offers only development seeds,
    posts the chosen seed, route and tier, lists each line (the OpenAlex failure named too) and reloads the candidate
    list when a seed gave new candidates."""
    from tests.unit.test_seed import _seed_page

    calls: list = []
    p = _with_refs(tmp_path, monkeypatch, "ui", calls)
    client = TestClient(create_app(Project.load(p.root)))
    rows = client.get("/api/benchmark/seed").json()["rows"]
    answer = client.post("/api/benchmark/references", json={"benchmark_id": "B001"}).json()
    assert answer["seeds"][0]["new_candidates"] == 1
    failed = {"lines": ["B001: OpenAlex request failed, PDF 5 entries"], "seeds": [{"new_candidates": 0}]}
    body = """
      Object.assign(replies, {"/api/benchmark/references": ANSWER, "/api/benchmark/candidates": {candidates: []}});
      const box = $("#seed-refs").innerHTML, sel = box.slice(box.indexOf('id="refs-seed"'), box.indexOf("</select>"));
      const seeds = [...sel.matchAll(/value="([^"]*)"/g)].map(m => m[1]);
      $("#refs-seed").value = "B001"; $("#refs-route").value = "pdf"; $("#refs-tier").value = "crossref";
      await $("#refs-run").onclick(); await flush();
      const first = {out: $("#refs-out").innerHTML, n: calls.length};
      replies["/api/benchmark/references"] = FAILED;
      $("#refs-seed").value = ""; $("#refs-route").value = "auto"; $("#refs-tier").value = "";
      await $("#refs-run").onclick(); await flush();
      console.log(JSON.stringify({seeds, first, calls, out: $("#refs-out").innerHTML}));
    """.replace("ANSWER", json.dumps(answer)).replace("FAILED", json.dumps(failed))
    out = _seed_page(rows, body, more=True)
    assert out["seeds"] == ["", "B001", "B002"]   # every development seed, then each; V001 is not offered
    posts = [c for c in out["calls"] if c[1] == "POST"]
    assert posts == [["/api/benchmark/references", "POST", {"benchmark_id": "B001", "route": "pdf", "tier": "crossref"}],
                     ["/api/benchmark/references", "POST", {"benchmark_id": None, "route": "auto", "tier": None}]]
    assert "B001: OpenAlex 4 references, PDF 5 entries" in out["first"]["out"]
    assert "Drift without coupling" in out["first"]["out"]
    # a seed with new candidates reloads the list; the failed run (no new candidate) does not
    after_first = [c[0] for c in out["calls"][:out["first"]["n"]]]
    assert after_first[-1].startswith("/api/benchmark/candidates")
    assert out["calls"][-1][0] == "/api/benchmark/references"
    assert "B001: OpenAlex request failed, PDF 5 entries" in out["out"]


def test_reference_tiers(tmp_path, monkeypatch):
    """Scenario "Parsing tiers keep only what the entry says" (construct-workbench, D26)."""
    from tests.unit.test_seed_mining import _fake
    from wider.seeds import chain, references

    calls: list = []

    def crossref(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": [
                {"DOI": "10.1000/r3x", "title": ["Phase binding at scale"], "issued": {"date-parts": [[2021]]}},
                {"DOI": "10.1000/old", "title": ["Phase binding at scale"], "issued": {"date-parts": [[2001]]}}]}})
        return httpx.Response(404)
    p = _with_refs(tmp_path, monkeypatch, "t", calls, handler=crossref)
    r = _cli(p, "benchmark", "references", "B001", "--route", "pdf", "--tier", "crossref")
    assert r.exit_code == 0, r.output
    assert "Crossref: 1 of 1 entries without a DOI matched by title, each fuzzy (confirm)" in " ".join(r.output.split())
    assert [c.url.host for c in calls] == ["api.crossref.org"]   # the PDF route sends nothing; one title went out
    assert set(calls[0].url.params) <= {"query.bibliographic", "rows", "mailto"}
    assert calls[0].url.params["query.bibliographic"] == "Phase binding at scale"
    row = p.store.query("SELECT doi, tier, matched_by FROM seed_references WHERE route='pdf' AND ref_index=2")[0]
    assert (row["doi"], row["tier"], row["matched_by"]) == ("10.1000/r3x", "crossref", "fuzzy (confirm)")
    assert not [c for c in chain.candidates(p) if c["doi"] == "10.1000/r3x"]   # fuzzy until confirmed

    # the model tier: a title found in the entry is kept, a DOI that is not in it is dropped and counted
    def answer(req):
        if "Drift without coupling" in req.user:
            return {"authors": "Evans, E.", "title": "Drift without coupling", "year": "2022",
                    "venue": "Proceedings on Phase Studies", "doi": "10.9999/invented"}
        return {}
    d = references.seed_references(p, "B001", route="pdf", tier="model", provider=_fake(answer))
    m = d["model"]
    assert m["ran"] and m["dropped"] == 1 and "1 dropped: not in the entry" in m["message"]
    evans = [c for c in m["calls"] if c["dropped"]]
    assert len(evans) == 1 and evans[0]["dropped"] == ["doi"] and "title" in evans[0]["kept"]
    assert all("Drift" not in json.dumps(c) for c in m["calls"])   # hashes only, never the entry
    row = p.store.query("SELECT doi, title, tier FROM seed_references WHERE route='pdf' AND ref_index=4")[0]
    assert (row["doi"], row["title"], row["tier"]) == ("10.1000/r5", "Drift without coupling", "model")


def test_model_passes_wait_for_their_consents(tmp_path, monkeypatch):
    """The optional model passes run "under its consents" (D22): an API backend without the research consent gets
    nothing (exit 2 on the CLI, 403 on the routes, no adapter built); with research and passages allowed but
    seed_pdfs.passages_to_cloud off, the reference entries stay on the computer and the output says why."""
    from wider.core import backend_consent as bc
    from wider.providers import registry
    from wider.providers.fake import FakeProvider

    made: list = []
    monkeypatch.setattr(registry, "_make", lambda cfg: made.append(cfg.kind) or FakeProvider(cfg, lambda req: {}))
    names_p = _connected(tmp_path, "gate")
    refs_p = _with_refs(tmp_path, monkeypatch, "gate-r", [])
    for p in (names_p, refs_p):
        p.config.stage_providers["construct"] = "anthropic"
        p.save()
    r = _cli(names_p, "query", "missed-names", "--model")
    assert r.exit_code == 2 and "nothing was sent" in " ".join(r.output.split()), r.output
    p = refs_p
    p.config.seed_pdfs.passages_to_cloud = True
    p.save()
    r = _cli(p, "benchmark", "references", "B001", "--route", "pdf", "--tier", "model")
    assert r.exit_code == 2 and "nothing was sent" in " ".join(r.output.split()), r.output
    assert TestClient(create_app(Project.load(names_p.root))).post(
        "/api/query/missed-names", json={"model": True}).status_code == 403
    assert TestClient(create_app(Project.load(p.root))).post(
        "/api/benchmark/references", json={"benchmark_id": "B001", "route": "pdf", "tier": "model"}).status_code == 403
    assert made == []

    # both consents, but the switch off: the model tier is skipped with the rule named; the PDF entries are kept
    p = Project.load(refs_p.root)
    p.config.seed_pdfs.passages_to_cloud = False
    p.save()
    bc.allow_backend(p, "anthropic", ["research", "passages"], author="test")
    r = _cli(p, "benchmark", "references", "B001", "--route", "pdf", "--tier", "model")
    flat = " ".join(r.output.split())
    assert r.exit_code == 0 and "seed_pdfs.passages_to_cloud is off" in flat and "B001: PDF 5 entries" in flat, flat
    assert "reference entry" in flat and made == []


def test_grobid_url_must_be_loopback(tmp_path, monkeypatch):
    """Scenario "GROBID only on this computer" (construct-workbench, D26)."""
    from wider.seeds import policy, references

    calls: list = []
    p = _with_refs(tmp_path, monkeypatch, "g", calls)
    r = _cli(p, "seeds", "policy", "grobid-url", "https://grobid.example.org")
    assert r.exit_code == 1 and "must run on this computer" in " ".join(r.output.split())
    assert Project.load(p.root).config.seed_pdfs.grobid_url == ""
    assert TestClient(create_app(Project.load(p.root))).post(
        "/api/seeds/policy", json={"grobid_url": "http://10.0.0.5:8070"}).status_code == 400

    posted: list = []
    tei = ('<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><back><listBibl>'
           '<biblStruct><analytic><title level="a">Alpha coupling in paired signals</title></analytic>'
           '<monogr><title level="j">Journal of Synthetic Signals</title><imprint><date when="2019"/></imprint>'
           '</monogr><idno type="DOI">10.1000/r1</idno><note type="raw_reference">Abel, A. (2019). Alpha coupling '
           'in paired signals.</note></biblStruct></listBibl></back></text></TEI>')

    class Http:
        def __init__(self, fail=False):
            self.fail = fail

        def post(self, url, files=None, data=None, timeout=None):
            posted.append((url, data))
            if self.fail:
                raise httpx.ConnectError("refused")
            return httpx.Response(200, text=tei, request=httpx.Request("POST", url))

    assert _cli(p, "seeds", "policy", "grobid-url", "http://127.0.0.1:8070").exit_code == 0
    p = Project.load(p.root)
    assert policy.status(p)["grobid_url"] == "http://127.0.0.1:8070"
    d = references.seed_references(p, "B001", route="pdf", tier="grobid", http=Http())
    assert posted == [("http://127.0.0.1:8070" + references.GROBID_PATH, references.GROBID_PARAMS)]
    assert references.GROBID_PARAMS["consolidateCitations"] == "0"
    assert d["seeds"][0]["line"] == "PDF 1 entry" and "GROBID parsed 1 entries" in d["seeds"][0]["messages"]
    # a server that is not running: named, and the plain PDF entries stay
    d = references.seed_references(p, "B001", route="pdf", tier="grobid", http=Http(fail=True))
    assert "GROBID not reachable at http://127.0.0.1:8070; the plain PDF entries are kept" in d["seeds"][0]["messages"]
    assert d["seeds"][0]["line"] == "PDF 5 entries"
    # a URL put in project.yaml by hand is refused before anything is read or sent
    p.config.seed_pdfs.grobid_url = "https://grobid.example.org"
    n = len(posted)
    try:
        references.seed_references(p, "B001", route="pdf", tier="grobid", http=Http())
        raise AssertionError("a remote GROBID URL was accepted")
    except ValueError as e:
        assert "must run on this computer" in str(e)
    assert len(posted) == n and calls == []


def test_grobid_never_goes_through_a_proxy(tmp_path, monkeypatch):
    """Risk-checker R3: the seed PDF is posted to the loopback GROBID only, never to an HTTP proxy from the
    environment."""
    import http.server
    import socket
    import threading

    from wider.seeds import references

    got = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):   # noqa: N802
            got.append(self.path)
            self.send_response(502)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    p = _proj(tmp_path)
    p.config.seed_pdfs.grobid_url = f"http://127.0.0.1:{free}"
    pdf = tmp_path / "seed.pdf"
    pdf.write_bytes(b"%PDF-1.4 synthetic")
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    try:
        entries, msg = references._grobid(p, pdf)
    finally:
        srv.shutdown()
    assert got == [] and entries is None and "not reachable" in msg


def test_crossref_sends_no_heuristic_title(tmp_path, monkeypatch):
    """Risk-checker R2, scenario "Parsing tiers keep only what the entry says": an entry parsed only heuristically
    (no year, so its "title" may hold the authors and the journal) is not sent to Crossref, and that is counted."""
    from tests.unit.helpers import consent, mock_client
    from wider.prior.refs_pdf import parse_entry
    from wider.seeds import references

    calls = []
    monkeypatch.setattr("wider.enrich.sources._client",
                        lambda *a, **k: mock_client({"crossref": {"message": {"items": []}}}, calls))
    p = consent(_proj(tmp_path))
    vague = parse_entry("Smith J, Doe K, Roe L, Alpha coupling in lattices of pairs, Journal of Synthetic Things 12, 3-4")
    clear = parse_entry("Roe, L. (2019). Quiet coupling in synthetic pairs. Journal of Things, 3, 1-9.")
    assert vague["confidence"] == "heuristic" and clear["confidence"] == "parsed"
    msg = references._crossref(p, [{**vague, "tier": "plain"}, {**clear, "tier": "plain"}], None)
    assert [dict(c.url.params)["query.bibliographic"] for c in calls] == [clear["title"]]
    assert "0 of 1 entries" in msg and "1 entry whose title could not be told apart from the rest" in msg
    calls.clear()
    msg = references._crossref(p, [{**vague, "tier": "plain"}], None)
    assert calls == [] and "nothing was sent; 1 entry whose title" in msg


def test_grobid_field_on_the_seed_policy_card():
    """The GROBID field of Settings, Seed papers (D26): Save posts the trimmed URL, an empty field posts "off", and a
    refusal (a server not on this computer) is shown beside the field."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    pol = {"passages_to_cloud": False, "passage_tokens": 1500, "lets_out": "extracted passages", "grobid_url": None,
           "backend": None, "passages_allowed": []}
    js = (STATIC / "seeds.js").read_text(encoding="utf-8")
    out = run_node(ESC + PAGE + js + """
      const POLICY = POL;
      replies["/api/seeds/policy"] = POLICY;
      const card = $("#settings-seeds");
      await SeedNames.renderPolicy(card); await flush();
      const html = card.innerHTML;
      card.querySelector("[data-grobid-url]").value = "  http://127.0.0.1:8070 ";
      await card.querySelector("[data-grobid-save]").onclick(); await flush();
      card.querySelector("[data-grobid-url]").value = "";
      await card.querySelector("[data-grobid-save]").onclick(); await flush();
      const cleared = card.querySelector("[data-seed-policy-msg]").textContent;
      replies["/api/seeds/policy"] = (url, opts) => { if (opts && opts.method === "POST") throw new Error(
        "the GROBID server must run on this computer"); return POLICY; };
      card.querySelector("[data-grobid-url]").value = "https://grobid.example.org";
      await card.querySelector("[data-grobid-save]").onclick(); await flush();
      console.log(JSON.stringify({html, calls, cleared, bad: card.querySelector("[data-grobid-msg]").innerHTML}));
    """.replace("POL;", json.dumps(pol) + ";"))
    assert "GROBID server for seed reference lists (on this computer only" in out["html"]
    sent = [c for c in out["calls"] if c[1] == "POST"]
    assert sent == [["/api/seeds/policy", "POST", {"grobid_url": "http://127.0.0.1:8070"}],
                    ["/api/seeds/policy", "POST", {"grobid_url": "off"}],
                    ["/api/seeds/policy", "POST", {"grobid_url": "https://grobid.example.org"}]]
    assert out["cleared"] == "GROBID server cleared: the grobid tier is off."
    assert out["bad"] == '<span class="bad">the GROBID server must run on this computer</span>'


# ------------------------------------------------------------------------- model passes on every backend kind (D22)
def test_model_passes_on_any_backend(tmp_path, monkeypatch):
    """Scenario "Same pass on a local, an API and a CLI backend" (construct-workbench, D22)."""
    from wider.core import backend_consent as bc
    from wider.core import detect
    from wider.providers import registry
    from wider.providers.fake import FakeProvider

    def answer(req):
        if "Candidate C3" in req.user:   # one phrase of the candidate's title, one invented
            return {"names": [{"block": "a", "phrase": "gamma rhythm", "candidate_id": "C3"},
                              {"block": "a", "phrase": "delta field", "candidate_id": "C3"}]}
        if "Drift without coupling" in req.user:
            return {"authors": "Evans, E.", "title": "Drift without coupling", "year": "2022",
                    "venue": "Proceedings on Phase Studies", "doi": "10.9999/invented"}
        return {"names": []} if "Candidate" in req.user else {}
    made: list = []

    def _make(cfg):   # every kind answers the same text; the adapter keeps the configured kind
        made.append(cfg.kind)
        return FakeProvider(cfg, answer)
    monkeypatch.setattr(registry, "_make", _make)
    # the loopback Ollama answers and lists its model: local, so it needs no consent and nothing leaves
    monkeypatch.setattr(detect, "_ollama_tags", lambda base, get=None, fresh=False: {"models": [{"name": "qwen3:8b"}]})
    monkeypatch.setattr(detect, "ollama_answers", lambda cfg, get=None: True)

    names_p = _connected(tmp_path, "kinds")
    refs_p = _with_refs(tmp_path, monkeypatch, "kinds-r", [])
    for p in (names_p, refs_p):
        p.config.seed_pdfs.passages_to_cloud = True   # the reference entries may go to the API and CLI backends
        p.save()
        bc.allow_backend(p, "anthropic", ["research", "passages"], author="test")
        bc.allow_backend(p, "claude-code", ["research", "passages"], author="test")
    seen: dict = {}
    for name, kind in (("ollama", "ollama"), ("anthropic", "anthropic"), ("claude-code", "cli_agent")):
        for p in (names_p, refs_p):
            p = Project.load(p.root)
            p.config.stage_providers["construct"] = name
            p.save()
        r = _cli(names_p, "query", "missed-names", "--model", "--json")
        assert r.exit_code == 0, r.output
        d = json.loads(r.output)
        m = d["model"]
        assert m["ran"] and m["backend_kind"] == kind and m["provider"]["kind"] == kind, (kind, m)
        assert (m["kept"], m["dropped"]) == (1, 1) and "1 dropped: not in the candidate's text" in m["message"]
        names = sorted((n["name"], n["block"], tuple(n["origin"])) for n in d["names"])
        r = _cli(refs_p, "benchmark", "references", "B001", "--route", "pdf", "--tier", "model", "--json")
        assert r.exit_code == 0, r.output
        e = json.loads(r.output)["model"]
        assert e["ran"] and e["backend_kind"] == kind and e["provider"]["kind"] == kind, (kind, e)
        assert e["dropped"] == 1 and "1 dropped: not in the entry" in e["message"]
        rows = [tuple(x) for x in Project.load(refs_p.root).store.query(
            "SELECT ref_index, doi, title, tier FROM seed_references WHERE route='pdf' ORDER BY ref_index")]
        seen[kind] = (names, m["kept"], e["kept"], rows)
    assert seen["ollama"] == seen["anthropic"] == seen["cli_agent"], seen
    names, *_x, rows = seen["ollama"]
    assert ("gamma rhythm", "a", ("llm",)) in names and not [n for n in names if n[0] == "delta field"]
    assert (4, "10.1000/r5", "Drift without coupling", "model") in rows
    assert set(made) == {"ollama", "anthropic", "cli_agent"}
    # the usage log names each kind under the passes' stages, and `wider costs` lists them
    for p, stage, n in ((names_p, "missed_names", 3), (refs_p, "ref_structure", 5)):
        got = Project.load(p.root).store.query("SELECT kind, COUNT(*) n FROM llm_usage WHERE stage=? GROUP BY kind",
                                               (stage,))
        assert {g["kind"]: g["n"] for g in got} == {"ollama": n, "anthropic": n, "cli_agent": n}, (stage, got)
        out = _cli(p, "costs").output
        assert stage in out, out

    # no backend configured: both commands give the deterministic result and say the model pass did not run
    for p in (names_p, refs_p):
        p = Project.load(p.root)
        p.config.providers, p.config.default_provider, p.config.stage_providers = [], None, {}
        p.save()
    r = _cli(names_p, "query", "missed-names", "--model")
    flat = " ".join(r.output.split())
    assert r.exit_code == 0 and "the model pass did not run" in flat, r.output
    assert "beta lattice → block a" in flat and "gamma rhythm" not in flat
    r = _cli(refs_p, "benchmark", "references", "B001", "--route", "pdf", "--tier", "model")
    flat = " ".join(r.output.split())
    assert r.exit_code == 0 and "the model pass did not run" in flat and "B001: PDF 5 entries" in flat, r.output
    assert len(made) == 6   # no adapter was built without a backend


def _independent_proj(tmp_path, *, shown=True, n_val=10, retrieved=8, includes=40, in_both=8, name="ind"):
    """Independent validation papers V01.. (researcher, seed_chain, colleague), one Validation paper from the query's
    results (never counted), Development B001 (retrieved) and B002; the query's import holds the first ``retrieved``
    validation papers; protocol P includes ``includes`` records, ``in_both`` of them validation papers."""
    from wider.core.models import BenchmarkPaper
    from wider.screen.decisions import set_decision

    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.config.protocol.identification.validation_after_freeze = not shown
    p.save()
    src = ["researcher"] * 4 + ["seed_chain:B001"] * 5 + ["colleague"] * 1
    val = [BenchmarkPaper(benchmark_id=f"V{i:02d}", title=f"Synthetic validation paper {i}", doi=f"10.1000/v{i}",
                          set="Validation", source=src[(i - 1) % len(src)]) for i in range(1, n_val + 1)]
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="Alpha coupling in pairs", doi="10.1000/a1"),
                       BenchmarkPaper(benchmark_id="B002", title="Beta drift over time", doi="10.1000/a2"),
                       *val, BenchmarkPaper(benchmark_id="Q01", title="Taken from the query results",
                                            doi="10.1000/q1", set="Validation", source="query_results")])
    st = p.store
    st.execute("INSERT INTO sources(source_id, platform, file_name, sha256, n_records, imported_at) "
               "VALUES(1, 'scopus', 'synthetic.ris', 'x', 0, '2026-10-24')")
    for i, doi in enumerate(["10.1000/a1", "10.1000/q1"] + [f"10.1000/v{i}" for i in range(1, retrieved + 1)], 1):
        st.execute("INSERT INTO raw_records(raw_id, source_id, platform, position, title, doi, doi_norm) "
                   "VALUES(?, 1, 'scopus', ?, ?, ?, ?)", (i, i, f"record {i}", doi, doi))
    for i in range(1, includes + 1):
        doi = f"10.1000/v{i}" if i <= in_both else f"10.1000/other{i}"
        st.execute("INSERT INTO records(record_id, cluster_id, key, title, doi) VALUES(?,?,?,?,?)",
                   (i, i, f"k{i}", f"included record {i}", doi))
        set_decision(p, "P", i, "include")
    st.execute("INSERT INTO records(record_id, cluster_id, key, title, doi) VALUES(999, 999, 'k999', 'other', "
               "'10.1000/zz')")
    set_decision(p, "Q", 999, "include")
    return p


def test_independent_estimate(tmp_path):
    """Scenario "Recall on independent papers, then a total estimate" (recall-and-outputs, D25)."""
    import zipfile

    from wider.recall import independent
    from wider.recall.outputs import write_package
    from wider.recall.report import build_report, to_markdown

    p = _independent_proj(tmp_path)
    d = independent.check(p, "P")
    lines = d["lines"]
    assert lines[0] == ("Recall on independent validation papers: 8 of 10 (0 of 1 from a colleague, 4 of 4 added by "
                        "the researcher, 4 of 5 found through the seeds' citations), 95% interval 0.44 to 0.97, for these 10 papers only; they are not a "
                        "random sample of all relevant papers.")
    est = d["estimate"]
    assert (est["n1"], est["n2"], est["m"]) == (40, 10, 8) and abs(est["N"] - (41 * 11 / 9 - 1)) < 1e-9
    assert ("Estimate after screening (P: 40 included by screening, 10 independent validation papers, 8 in both): Chapman's "
            "estimate of all relevant papers is about 49 (SE 6.0); about 7 relevant papers may be missing from both."
            ) in lines
    assert lines[-1] == independent.CAVEAT and "not independent" in independent.CAVEAT
    assert "Independent Development papers: 1 of 2, not a validation figure (seen during development)." in lines
    assert "Q01" not in json.dumps(d)   # a paper from the query's results is never counted
    # no overlap: no estimate
    q = independent.check(p, "Q")
    assert q["estimate"]["reason"] == "not defined (no overlap)" and any("not defined (no overlap)" in x for x in q["lines"])
    # the same lines on every surface: the CLI, the API, the report
    out = _cli(p, "coverage", "--independent", "--protocol", "P")
    assert out.exit_code == 0, out.output
    assert "about 49 (SE 6.0)" in out.output and "for these 10 papers only" in out.output
    assert TestClient(create_app(Project.load(p.root))).get("/api/coverage/independent?protocol=P").json()["lines"] == lines
    md = to_markdown(build_report(p, protocol="P"))
    assert "## Independent check" in md and "about 7 relevant papers may be missing from both" in md
    assert _cli(p, "coverage", "--independent", "--protocol", "nope").exit_code == 1
    # the package: counts, ids, formulas and the caveat, no title or abstract
    z = zipfile.ZipFile(p.root / write_package(p, "P")["path"])
    js = z.read("independent_estimate.json").decode("utf-8")
    e = json.loads(js)
    assert e["validation"]["found"] == 8 and e["validation"]["missed_ids"] == ["V09", "V10"]
    assert e["estimate"]["in_both_ids"] == [f"V{i:02d}" for i in range(1, 9)] and e["caveat"] == independent.CAVEAT
    assert "chapman" in e["formulas"]
    assert "Synthetic validation paper" not in js and "Alpha coupling" not in js and "abstract" not in js


def test_interval_and_chapman(tmp_path):
    """Scenario "All independent papers found" (recall-and-outputs, D25), and the estimate's table values."""
    from wider.recall import independent

    p = _independent_proj(tmp_path, n_val=4, retrieved=4, includes=6, in_both=4)
    lines = independent.check(p)["lines"]   # the protocol defaults to the most recently screened (P, then Q)
    assert lines[0].startswith("Recall on independent validation papers: 4 of 4 (4 of 4 added by the researcher), "
                               "95% interval 0.40 to 1.00, for these 4 papers only")
    assert "That is few: one paper more or fewer missed would change the estimate a lot." in lines
    assert "All found here does not mean none are missing." in lines
    assert not any(x.startswith("No independent validation paper was missed") for x in lines)
    c = independent.chapman_numbers
    assert round(c(40, 10, 8)["N"], 2) == 49.11 and round(c(40, 10, 8)["missing"], 2) == 7.11
    assert round(c(40, 10, 8)["se"], 2) == 5.97
    # m = n2 would give SE 0 and "0 missing"; fewer than ten papers are too few: no estimate, said why
    assert c(10, 10, 10)["N"] is None and "SE 0" in c(10, 10, 10)["reason"]
    assert c(40, 9, 5)["N"] is None and "too few" in c(40, 9, 5)["reason"]
    assert "Chapman's estimate" not in "".join(lines) and "too few" in "".join(lines)
    assert c(40, 10, 0)["N"] is None and c(40, 10, 0)["reason"] == "not defined (no overlap)"
    assert c(40, 0, 0)["reason"] == "no independent validation papers"


def test_independent_check_hidden_set(tmp_path):
    """Scenario "Independent check while the validation set is hidden" (recall-and-outputs, D25)."""
    import zipfile

    from wider.core import journal
    from wider.recall import independent
    from wider.recall.outputs import write_package

    p = _independent_proj(tmp_path, shown=False)
    out = _cli(p, "coverage", "--independent")
    assert out.exit_code == 0, out.output
    assert "Validation set hidden: independent check not shown." in out.output
    assert "Independent Development papers: 1 of 2, not a validation figure (seen during development)." in out.output
    assert "8 of 10" not in out.output and "Chapman" not in out.output
    api = TestClient(create_app(Project.load(p.root))).get("/api/coverage/independent").json()
    assert api["validation"] is None and api["estimate"] is None and api["shown"] is False
    e = json.loads(zipfile.ZipFile(p.root / write_package(p, "P")["path"]).read("independent_estimate.json"))
    assert e["validation"] is None and e["estimate"] is None and e["hidden_line"] == independent.HIDDEN_LINE
    # no look recorded
    assert p.store.get_meta("validation_peeks") is None and not journal.changes(p, "validation_peek")


def test_independent_check_card_on_the_recall_page(tmp_path):
    """The Recall page's "Independent check" card asks for the chosen protocol and shows the lines, the first bold."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node

    reply = {"lines": ["Recall on independent validation papers: 8 of 10.", "Caveat <b>line</b>."]}
    r = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\n"
                 f"$('#rc-protocol').value = 'P';\n"
                 f"replies['/api/coverage/independent?protocol=P'] = {json.dumps(reply)};\n"
                 f"{(STATIC / 'outputs.js').read_text(encoding='utf-8')}\nawait window.loadOutputs.loadIndependent();\n"
                 + out("{calls, html: $('#out-independent').innerHTML}"))
    assert any(c[0] == "/api/coverage/independent?protocol=P" for c in r["calls"]), r["calls"]
    assert r["html"].startswith("<p><strong>Recall on independent validation papers: 8 of 10.</strong></p>"), r["html"]
    assert "Caveat &lt;b&gt;line&lt;/b&gt;." in r["html"]
