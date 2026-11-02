"""Seed papers: adding by DOI / title / file, mocked resolution, duplicates, assignment, the suggested split and the
online-lookup consent of seed resolution."""
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.helpers import consent, mock_client
from wider.cli import app as cli_app
from wider.construct import seed
from wider.core import lookups
from wider.core.project import Project
from wider.ui.app import create_app

# synthetic lookups; no network
WORKS = {
    "10.1000/synth.001": {"source": "openalex", "title": "Alpha Study of Team Coordination", "year": 2015,
                          "doi": "10.1000/synth.001", "first_author": "Alpha",
                          "abstract": " ".join(["word"] * 80)},
    "10.1000/synth.002": {"source": "crossref", "title": "Beta Analysis of Learning Curves", "year": 2018,
                          "doi": "10.1000/synth.002", "first_author": "Beta", "abstract": " ".join(["term"] * 70)},
}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    p = Project.init(tmp_path / "r")
    calls = {"openalex": [], "crossref": [], "search": []}

    def openalex_work(doi, client):
        calls["openalex"].append(doi)
        w = WORKS.get(doi)
        return w if w and w["source"] == "openalex" else None

    def crossref_work(doi, client):
        calls["crossref"].append(doi)
        w = WORKS.get(doi)
        return w if w and w["source"] == "crossref" else None

    def openalex_search(title, client):
        calls["search"].append(title)
        return [w for w in WORKS.values()]

    monkeypatch.setattr(seed, "openalex_work", openalex_work)
    monkeypatch.setattr(seed, "crossref_work", crossref_work)
    monkeypatch.setattr(seed, "openalex_search", openalex_search)
    p.calls = calls
    yield p
    p.close()


def _resolve(p, **kw):
    return seed.resolve(consent(p), client=SimpleNamespace(close=lambda: None), **kw)


def test_add_by_doi_and_title_then_resolve(proj):
    added = seed.add_by_dois(proj, ["https://doi.org/10.1000/synth.001", "doi:10.1000/synth.002"])
    assert [a.benchmark_id for a in added] == ["B001", "B002"]
    assert added[0].doi == "10.1000/synth.001" and added[0].set == "Development"
    t = seed.add_by_titles(proj, ["Alpha study of team coordination"], set_name="Validation")
    assert t[0].benchmark_id == "B003" and t[0].set == "Validation" and t[0].doi is None
    rows = {r["benchmark_id"]: r for r in seed.status(proj)}
    assert rows["B001"]["resolved"] == "pending"
    rows = {r["benchmark_id"]: r for r in _resolve(proj)}
    assert rows["B001"]["resolved"] == "resolved" and rows["B001"]["resolve_source"] == "openalex"
    assert rows["B001"]["title"] == "Alpha Study of Team Coordination" and rows["B001"]["year"] == 2015
    assert rows["B001"]["abstract_words"] == 80
    # DOI 002 is unknown to OpenAlex, so Crossref was tried
    assert rows["B002"]["resolved"] == "resolved" and rows["B002"]["resolve_source"] == "crossref"
    assert proj.calls["crossref"] == ["10.1000/synth.002"]
    # the title-only paper got its DOI by title search and is now a duplicate of B001
    assert rows["B003"]["doi"] == "10.1000/synth.001" and rows["B003"]["duplicate_of"] == "B001"
    # persisted in benchmark.csv
    papers = {b.benchmark_id: b for b in Project.load(proj.root).read_benchmark()}
    assert papers["B002"].year == 2018 and papers["B002"].first_author == "Beta"


def test_unresolved_is_flagged_and_resolve_is_idempotent(proj):
    seed.add_by_dois(proj, ["10.1000/nothing.9"])
    seed.add_by_titles(proj, ["A title that matches nothing at all in the index"])
    rows = {r["benchmark_id"]: r for r in _resolve(proj)}
    assert rows["B001"]["resolved"] == "unresolved" and rows["B001"]["resolve_note"] == "not found"
    assert rows["B002"]["resolved"] == "unresolved" and "similar" in rows["B002"]["resolve_note"]
    n = len(proj.calls["openalex"])
    _resolve(proj)
    assert len(proj.calls["openalex"]) == 2 * n  # unresolved papers are retried
    seed.add_by_dois(proj, ["10.1000/synth.001"])
    _resolve(proj)
    rows = {r["benchmark_id"]: r for r in seed.status(proj)}
    assert rows["B003"]["resolved"] == "resolved"
    n = len(proj.calls["openalex"])
    _resolve(proj)  # resolved + complete papers are not looked up again
    assert proj.calls["openalex"][n:] == ["10.1000/nothing.9"]


def test_add_lines_and_duplicates(proj):
    added = seed.add_lines(proj, "10.1000/synth.001\n\nSome Other Title\nhttps://doi.org/10.1000/synth.001\nsome other title\n")
    assert len(added) == 2  # same DOI and same normalised title are skipped on input
    added = seed.add_by_titles(proj, ["Some other title!"])
    assert added == []
    seed.add_rows(proj, [{"title": "Explicit Dup", "doi": "10.1000/synth.001"}])  # DOI already known
    assert len(proj.read_benchmark()) == 2


def test_rows_already_seeds_are_reported_not_dropped_silently(proj, tmp_path):
    """M8 hand check: a CSV whose rows are already seeds names each skipped row on the API, the page message and
    the CLI; a row asking for the other set is a set conflict and the seed stays where it is."""
    seed.add_rows(proj, [{"title": "Gamma Paper", "doi": "10.1000/synth.003"},
                         {"title": "Delta Paper", "doi": None, "set": "Validation"}])
    text = "title,doi,set\nGamma Paper,10.1000/synth.003,Validation\nDelta Paper,,Validation\nNew One,10.1000/synth.009,\n"
    c = TestClient(create_app(proj))
    r = c.post("/api/benchmark/add", json={"text": text, "filename": "m8-seeds.csv"}).json()
    assert [a["title"] for a in r["added"]] == ["New One"]
    by = {s["label"]: s for s in r["skipped"]}
    assert set(by) == {"10.1000/synth.003", "Delta Paper"}
    assert by["10.1000/synth.003"]["conflict"] and "B001 in Development; not moved to Validation" in by["10.1000/synth.003"]["reason"]
    assert not by["Delta Paper"]["conflict"] and by["Delta Paper"]["reason"] == "already seed B002 in Validation"
    assert r["skipped_summary"] == "2 skipped as already a seed or repeated, 1 of them already in the other set (not moved)"
    js = (Path(__file__).parents[2] / "wider/ui/static/setup.js").read_text()
    assert js.count("${skippedText(r)}") == 2   # both add paths put the skipped rows in the page message
    again = c.post("/api/benchmark/add", json={"text": "10.1000/synth.009\n10.1000/synth.009\n"}).json()
    assert again["added"] == [] and [s["reason"] for s in again["skipped"]] == ["already seed B003 in Development"] * 2
    csv_path = tmp_path / "m8-seeds.csv"
    csv_path.write_text(text, encoding="utf-8")
    out = _cli(proj, "benchmark", "add", "--file", str(csv_path))
    assert out.exit_code == 1
    assert "skipped 10.1000/synth.003: already seed B001 in Development; not moved" in " ".join(out.output.split())
    assert "nothing added: 3 skipped as already a seed or repeated" in " ".join(out.output.split())
    assert len(proj.read_benchmark()) == 3


def test_add_from_csv_and_ris(proj, tmp_path):
    csv_path = tmp_path / "seeds.csv"
    csv_path.write_text("title,doi,year,set\nGamma Paper,10.1000/synth.003,2020,Validation\nDelta Paper,,2021,\n",
                        encoding="utf-8")
    added = seed.add_from_file(proj, csv_path)
    assert [(a.title, a.doi, a.set, a.year) for a in added] == [("Gamma Paper", "10.1000/synth.003", "Validation", 2020),
                                                                 ("Delta Paper", None, "Development", 2021)]
    ris = "TY  - JOUR\nTI  - Epsilon Record\nDO  - 10.1000/synth.004\nPY  - 2019\nAU  - Epsilon, E.\nER  - \n"
    added = seed.add_from_text(proj, ris, "export.ris", set_name="Validation")
    assert len(added) == 1 and added[0].doi == "10.1000/synth.004" and added[0].set == "Validation"
    assert added[0].year == 2019 and (added[0].first_author or "").lower().startswith("epsilon")
    assert len(proj.read_benchmark()) == 3


def test_assignment_remove_and_split(proj):
    seed.add_by_dois(proj, [f"10.1000/synth.{i:03d}" for i in range(1, 8)])
    bp = seed.set_assignment(proj, "B002", "validation")
    assert bp.set == "Validation"
    with pytest.raises(KeyError):
        seed.set_assignment(proj, "B099", "Development")
    assert seed.set_assignment(proj, "B002", "dev").set == "Development"  # lenient names: dev* / val*
    seed.set_assignment(proj, "B002", "Validation")
    s = seed.suggest_split(proj)
    assert s["n"] == 7 and s["dev_share"] == proj.config.protocol.identification.development_share == 0.57
    assert len(s["development"]) == 4 and len(s["validation"]) == 3
    assert sorted(s["development"] + s["validation"]) == [f"B{i:03d}" for i in range(1, 8)]
    assert seed.suggest_split(proj) == s  # seeded: stable
    other = seed.suggest_split(proj, seed=s["seed"] + 1)  # another seed: same sizes, a valid partition
    assert other["seed"] == s["seed"] + 1 and len(other["development"]) == 4 and len(other["validation"]) == 3
    assert sorted(other["development"] + other["validation"]) == [f"B{i:03d}" for i in range(1, 8)]
    s2 = seed.suggest_split(proj, dev_share=0.3)
    assert len(s2["development"]) == 2
    with pytest.raises(ValueError):
        seed.suggest_split(proj, dev_share=1.5)
    seed.apply_split(proj, s2)
    sets = {b.benchmark_id: b.set for b in proj.read_benchmark()}
    assert [k for k, v in sets.items() if v == "Development"] == s2["development"]
    seed.remove(proj, "B001")
    assert [b.benchmark_id for b in proj.read_benchmark()] == [f"B{i:03d}" for i in range(2, 8)]
    assert seed.add_by_dois(proj, ["10.1000/synth.099"])[0].benchmark_id == "B008"


def test_split_defaults_come_from_the_project(proj):
    """Scenario "Split defaults": the project's seed and development share, and one paper per set at either end."""
    seed.add_by_dois(proj, [f"10.1000/synth.{i:03d}" for i in range(1, 4)])
    assert proj.config.protocol.identification.development_share == 0.57   # the documented default
    for project_seed in (7, 8):
        proj.config.seed = project_seed
        s = seed.suggest_split(proj)
        assert s["seed"] == project_seed and s == seed.suggest_split(proj, seed=project_seed)
        assert s["dev_share"] == proj.config.protocol.identification.development_share
    assert [len(seed.suggest_split(proj, dev_share=x)["development"]) for x in (0.0, 1.0)] == [1, 2]
    for n, share in ((3, 0.0), (3, 1.0), (2, 0.0), (2, 1.0), (2, 0.57)):   # each set gets at least one paper
        proj.write_benchmark(proj.read_benchmark()[:n])
        s = seed.suggest_split(proj, dev_share=share)
        assert len(s["development"]) >= 1 and len(s["validation"]) >= 1 and s["n"] == n


# ----------------------------------------------------------------- online-lookup consent (spec: construct-workbench)
def _cli(p, *args):
    return CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)])


def test_local_seed_work_without_consent_sends_nothing(proj, monkeypatch):
    """Scenario "Local seed work without consent": add two DOIs without --resolve, then suggest a split."""
    def boom(*a, **k):
        raise AssertionError("a lookup client was created without consent")
    monkeypatch.setattr("wider.enrich.sources._client", boom)
    assert lookups.allowed(proj) is False
    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.001", "--doi", "10.1000/synth.002")
    assert r.exit_code == 0, r.output
    assert [(b.benchmark_id, b.doi) for b in Project.load(proj.root).read_benchmark()] == [
        ("B001", "10.1000/synth.001"), ("B002", "10.1000/synth.002")]
    r = _cli(proj, "benchmark", "split")
    assert r.exit_code == 0, r.output
    assert "1 development / 1 validation" in r.output
    # the Setup seed-papers step calls the same local functions through these routes
    c = TestClient(create_app(proj))
    assert c.post("/api/benchmark/add", json={"text": "10.1000/synth.003\nA synthetic title only"}).status_code == 200
    assert c.post("/api/benchmark/split", json={}).status_code == 200
    assert len(Project.load(proj.root).read_benchmark()) == 4
    # nothing was sent: no lookup function ran, no client was created, no outside request was logged
    assert proj.calls == {"openalex": [], "crossref": [], "search": []}
    assert lookups.log(proj) == [] and proj.store.scalar("SELECT COUNT(*) FROM external_requests") == 0
    assert Project.load(proj.root).config.online_lookups.allowed is False
    assert all(row["resolved"] == "pending" for row in seed.status(proj))


OA_WORK = {"id": "https://openalex.org/W1", "title": "Alpha Study of Team Coordination", "publication_year": 2015,
           "doi": "https://doi.org/10.1000/synth.001", "abstract_inverted_index": {f"w{i}": [i] for i in range(80)},
           "authorships": [{"author": {"display_name": "Ann Alpha"}}]}
CR_WORK = {"message": {"title": ["Beta Analysis of Learning Curves"], "DOI": "10.1000/synth.002",
                       "issued": {"date-parts": [[2018]]}, "abstract": "term " * 70, "author": [{"family": "Beta"}]}}


def test_resolve_after_consent_logs_seed_resolve_per_service(tmp_path, monkeypatch):
    """Scenario "Resolve after consent": `wider lookups allow`, then `wider benchmark resolve`."""
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    sent: list = []

    def service(request):   # OpenAlex knows DOI 001 only; Crossref knows DOI 002
        url = str(request.url)
        if request.url.host == "api.openalex.org" and url.endswith("10.1000/synth.001"):
            return httpx.Response(200, json=OA_WORK)
        if request.url.host == "api.crossref.org" and url.endswith("10.1000/synth.002"):
            return httpx.Response(200, json=CR_WORK)
        return httpx.Response(404)

    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client(service, sent))
    p = Project.init(tmp_path / "r")
    seed.add_by_dois(p, ["10.1000/synth.001", "10.1000/synth.002"])
    assert _cli(p, "lookups", "allow").exit_code == 0
    r = _cli(p, "benchmark", "resolve")
    assert r.exit_code == 0, r.output
    # resolved as before: OpenAlex first, Crossref for the DOI OpenAlex does not know
    rows = {row["benchmark_id"]: row for row in seed.status(p)}
    assert (rows["B001"]["resolved"], rows["B001"]["resolve_source"]) == ("resolved", "openalex")
    assert (rows["B002"]["resolved"], rows["B002"]["resolve_source"]) == ("resolved", "crossref")
    papers = {b.benchmark_id: b for b in Project.load(p.root).read_benchmark()}
    assert (papers["B001"].year, papers["B001"].first_author) == (2015, "Alpha")
    assert (papers["B002"].year, papers["B002"].first_author) == (2018, "Beta")
    assert [(q.url.host, q.url.path) for q in sent] == [
        ("api.openalex.org", "/works/https://doi.org/10.1000/synth.001"),
        ("api.openalex.org", "/works/https://doi.org/10.1000/synth.002"),
        ("api.crossref.org", "/works/10.1000/synth.002")]
    # one seed_resolve row per service contacted, none for a service that was not
    log = lookups.log(p)
    assert sorted((x["service"], x["purpose"], x["n_items"], x["n_failed"]) for x in log) == [
        ("crossref", "seed_resolve", 1, 0), ("openalex", "seed_resolve", 2, 0)]
    # a second run has nothing to look up: nothing is sent and nothing is logged
    assert _cli(p, "benchmark", "resolve").exit_code == 0
    assert len(sent) == 3 and len(lookups.log(p)) == 2
    # the Setup step's route resolves the same way and logs the same entries
    c = TestClient(create_app(p))
    d = c.post("/api/benchmark/resolve", params={"all": True}).json()
    assert (d["resolved"], d["unresolved"]) == (2, 0)
    new = lookups.log(p)[:-2]
    assert sorted((x["service"], x["purpose"], x["n_items"]) for x in new) == [
        ("crossref", "seed_resolve", 1), ("openalex", "seed_resolve", 2)]
    p.close()


def test_split_on_the_command_line_takes_the_project_defaults(proj):
    # `wider benchmark split` without options: the protocol's development share and the project seed
    seed.add_by_dois(proj, [f"10.1000/synth.{i:03d}" for i in range(1, 8)])
    s = seed.suggest_split(proj)
    res = CliRunner().invoke(cli_app, ["benchmark", "split", "--dir", str(proj.root)], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output
    assert f"4 development / 3 validation of 7 (dev share 0.57, seed {s['seed']})" in res.output
    assert "development: " + ", ".join(s["development"]) in res.output


# ----------------------------------------------------------------- source (spec: import-and-coverage, query-refinement-loop)
def test_source_column(proj):
    """Scenarios "Source recorded and shown" and "Default source": the source is kept in benchmark.csv, shown by
    `wider benchmark list` and the seed step, changed with `wider benchmark source` (journalled, undoable), an old file
    without the column loads as researcher, and an unknown source is refused before anything is written."""
    from wider.core import journal

    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.001", "--source", "prior_review")
    assert r.exit_code == 0, r.output
    assert _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.002").exit_code == 0
    papers = {p.benchmark_id: p for p in proj.read_benchmark()}
    assert papers["B001"].source == "prior_review" and papers["B002"].source == "researcher"
    assert papers["B001"].added_at
    header = (proj.root / "benchmark.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "source" in header
    listing = CliRunner().invoke(cli_app, ["benchmark", "list", "--dir", str(proj.root)], terminal_width=200).output
    assert "prior_review" in listing and "researcher" in listing

    # the page: the select offers prior_review first; rows carry the source; the add route takes it
    client = TestClient(create_app(proj))
    d = client.get("/api/benchmark/seed").json()
    assert d["sources"][0] == "prior_review" and "researcher" in d["sources"]
    assert {r["benchmark_id"]: r["source"] for r in d["rows"]} == {"B001": "prior_review", "B002": "researcher"}
    added = client.post("/api/benchmark/add", json={"text": "A Colleague's Paper on Teams", "source": "colleague"}).json()
    assert added["added"][0]["source"] == "colleague"
    assert client.post("/api/benchmark/add", json={"text": "Another Title", "source": "friend"}).status_code == 400

    # a change of source is journalled and undone
    before = (proj.root / "benchmark.csv").read_text(encoding="utf-8")
    r = _cli(proj, "benchmark", "source", "B001", "colleague")
    assert r.exit_code == 0, r.output
    assert {p.benchmark_id: p.source for p in proj.read_benchmark()}["B001"] == "colleague"
    ch = journal.changes(proj, kind="benchmark_source")
    assert len(ch) == 1 and "B001" in ch[0]["summary"]
    journal.undo(proj, ch[0]["change_id"], author="tester", other=True)
    assert (proj.root / "benchmark.csv").read_text(encoding="utf-8") == before
    assert client.post("/api/benchmark/source", json={"benchmark_id": "B002", "source": "other_database"}).json()[
        "source"] == "other_database"

    # unknown source: refused, nothing written
    before = (proj.root / "benchmark.csv").read_text(encoding="utf-8")
    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.009", "--source", "friend")
    assert r.exit_code == 1 and "unknown source" in r.output
    assert _cli(proj, "benchmark", "source", "B001", "friend").exit_code == 1
    assert (proj.root / "benchmark.csv").read_text(encoding="utf-8") == before
    assert seed.source_name("seed_chain:SC3") == "seed_chain:SC3"

    # an old benchmark.csv without the column loads as researcher
    old = ["benchmark_id,title,doi,set,year,first_author,note", "B001,Old Paper,10.1000/old,Development,2010,Old,"]
    (proj.root / "benchmark.csv").write_text("\n".join(old) + "\n", encoding="utf-8")
    assert [(p.benchmark_id, p.source) for p in proj.read_benchmark()] == [("B001", "researcher")]


# ----------------------------------------------------------------- every path into a set (design D4)
RIS_ONE = """TY  - JOUR
TI  - Team meetings after errors
PY  - 2020
DO  - 10.1000/abc
AB  - A synthetic abstract about meetings.
ER  -
"""
QR_NOTICE = ("came from the query results: it is kept in Validation but not counted, because a paper the query found "
             "cannot test the query.")


def _imported_proj(proj, tmp_path):
    """The seed fixture's project with one synthetic Scopus file imported (10.1000/abc, "Team meetings after errors")."""
    from wider.ingest.importer import add_file, import_all

    other = Project.init(tmp_path / "with_scopus", platforms=["scopus"])
    proj.config.platforms = other.config.platforms
    other.close()
    proj.save()
    (tmp_path / "s.ris").write_text(RIS_ONE, encoding="utf-8")
    add_file(proj, "scopus", tmp_path / "s.ris")
    import_all(proj)
    return proj


def _seed_page(rows: list, body: str, more: bool = False) -> dict:
    """setup.js's Seed papers task on the stand-in page, drawn from the given rows; ``more`` also draws the Find more
    seed papers task (reference lists and citation candidates, review-plan-and-question-window D4)."""
    import json as _json

    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    replies = {"/api/benchmark/seed": {"rows": rows, "rule": seed.SPLIT_RULE, "sources": list(seed.SOURCES),
                                       "dev_share": 0.6, "seed": 1}}
    setup = (STATIC / "setup.js").read_text(encoding="utf-8")
    return run_node(f"{ESC}\n{PAGE}\nconst renderLookupsCard = () => {{}};\n"
                    f"Object.assign(replies, {_json.dumps(replies)});\n{setup}\n"
                    f"await window.loadSeed(); await flush();\n"
                    + ("await window.loadMore(); await flush();\n" if more else "") + body)


def test_query_results_paper_kept_but_not_counted(proj):
    """Scenario "A paper from the query results is kept but not counted" (the seed half; coverage, status and the
    ranking are pinned in test_dry_run): kept in Validation with the notice, exit 0; without a set, Development."""
    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.001", "--source", "query_results", "--set", "validation")
    assert r.exit_code == 0, r.output
    assert f"B001 {QR_NOTICE}" in " ".join(r.output.split())
    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.002", "--source", "query_results")
    assert r.exit_code == 0 and "came from the query results" not in r.output
    sets = {p.benchmark_id: (p.set, p.source) for p in proj.read_benchmark()}
    assert sets == {"B001": ("Validation", "query_results"), "B002": ("Development", "query_results")}
    rows = TestClient(create_app(proj)).get("/api/benchmark/seed").json()["rows"]
    by_id = {x["benchmark_id"]: x for x in rows}
    assert by_id["B001"]["notices"] == [f"B001 {QR_NOTICE}"] and by_id["B002"]["notices"] == []
    page = _seed_page(rows, "console.log(JSON.stringify({html: $('#setup-seed').innerHTML}));")
    html = page["html"]
    row = html[html.index('data-assign="B001"'):html.index("</tr>", html.index('data-assign="B001"'))]
    assert f"B001 {QR_NOTICE}" in row and 'class="warnings small seed-notices"' in row


def test_validation_match_from_another_source_warns(proj, tmp_path):
    """Scenario "A matching paper from another source is added with a warning": added to Validation, the warning
    names the imported record (file, record id, title), exit 0; the add route returns it; the seed step shows it."""
    _imported_proj(proj, tmp_path)
    raw_id = proj.store.scalar("SELECT raw_id FROM raw_records WHERE doi_norm='10.1000/abc'")
    r = _cli(proj, "benchmark", "add", "--doi", "10.1000/abc", "--source", "prior_review", "--set", "validation")
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert f'B001 matches an imported record (s.ris, record {raw_id}: "Team meetings after errors")' in out
    assert [(p.benchmark_id, p.set) for p in proj.read_benchmark()] == [("B001", "Validation")]
    client = TestClient(create_app(proj))
    res = client.post("/api/benchmark/add", json={"text": "Team Meetings After Errors.", "set": "Validation",
                                                  "source": "colleague"})
    assert res.status_code == 200
    assert "B002 matches an imported record (s.ris" in res.json()["notices"]["B002"][0]
    page = _seed_page(res.json()["rows"], "console.log(JSON.stringify({html: $('#setup-seed').innerHTML}));")
    html = page["html"]
    # only the paper entering now: B001 was warned when it was added, and repeating it would be a look (risk R1)
    assert html.count("matches an imported record (s.ris") == 1
    start = html.index('data-assign="B002"')
    assert "B002 matches an imported record" in html[start:html.index("</tr>", start)]   # beside its own paper


PATHS = ["add_by_dois", "add_lines", "add_from_file", "set_assignment", "apply_split", "resolve"]


@pytest.mark.parametrize("path", PATHS)
def test_validation_check_on_every_path(proj, tmp_path, path):
    """Scenario "Every path into Validation is checked": every path places a query_results paper in Validation with
    the not-counted notice and stamps the set fields; a title-only researcher paper matching an import warns, and
    resolve, filling in the matching DOI, warns the same way and refuses nothing."""
    _imported_proj(proj, tmp_path)
    if path == "resolve":
        consent(proj)
        WORKS["10.1000/abc"] = {"source": "openalex", "title": "Team meetings after errors", "year": 2020,
                                "doi": "10.1000/abc", "first_author": "Gamma", "abstract": " ".join(["w"] * 80)}
        try:
            seed.add_by_titles(proj, ["Team Meetings after Errors"], "Validation")
            before = seed.set_notices(proj, proj.read_benchmark()[0])
            assert len(before) == 1 and "matches an imported record" in before[0]
            rows = seed.resolve(proj)
        finally:
            WORKS.pop("10.1000/abc")
        paper = proj.read_benchmark()[0]
        assert paper.doi == "10.1000/abc" and paper.set == "Validation"
        assert [n for r in rows for n in r["notices"]] == before
        return
    if path == "add_by_dois":
        seed.add_by_dois(proj, ["10.1000/synth.001"], "Validation", source="query_results")
    elif path == "add_lines":
        seed.add_lines(proj, "10.1000/synth.001", "Validation", source="query_results")
    elif path == "add_from_file":
        (tmp_path / "seed.csv").write_text("title,doi,set\nAlpha Study,10.1000/synth.001,Validation\n", encoding="utf-8")
        seed.add_from_file(proj, tmp_path / "seed.csv", source="query_results")
    else:
        seed.add_by_dois(proj, ["10.1000/synth.001"], "Development", source="query_results")
        assert proj.read_benchmark()[0].was_development is True
        if path == "set_assignment":
            seed.set_assignment(proj, "B001", "Validation")
        else:
            seed.apply_split(proj, {"development": [], "validation": ["B001"]})
    paper = proj.read_benchmark()[0]
    assert (paper.set, paper.source) == ("Validation", "query_results")
    assert paper.validation_since and paper.validation_since_unlock == 0
    assert seed.set_notices(proj, paper) == [f"B001 {QR_NOTICE}"]
    row = proj.store.query("SELECT set_name, validation_since FROM benchmark_papers WHERE benchmark_id='B001'")[0]
    assert row["set_name"] == "Validation" and row["validation_since"] == paper.validation_since


# ----------------------------------------------------------------- seed keywords (query-refinement-loop 2.3, D6)
RIS_KW = """TY  - JOUR
TI  - Alpha Study
DO  - 10.1000/synth.001
KW  - export one
KW  - export two
ER  -
TY  - JOUR
TI  - Beta Analysis
DO  - 10.1000/synth.002
KW  - voice
KW  - silence
ER  -
TY  - JOUR
TI  - Unrelated record
DO  - 10.1000/other
KW  - noise
ER  -
"""


def test_seed_keywords_typed_and_from_import(proj, tmp_path):
    """Scenario "Keywords typed and copied from an import": B001's typed "a; b" stays (researcher), B002 gets the
    export's keywords (import) and the import's result names B002 on the CLI and the import route; the Setup list
    shows "from import"; one `benchmark_keywords` change for B001 that undo reverses (after the later import copy,
    which is its own undoable change, is undone first); an empty value clears both fields; resolve adds none."""
    from wider.core import journal
    from wider.ingest.importer import add_file

    other = Project.init(tmp_path / "with_scopus", platforms=["scopus"])
    proj.config.platforms = other.config.platforms
    other.close()
    proj.save()
    for doi in ("10.1000/synth.001", "10.1000/synth.002", "10.1000/synth.009"):
        assert _cli(proj, "benchmark", "add", "--doi", doi).exit_code == 0
    before_typed = (proj.root / "benchmark.csv").read_text(encoding="utf-8")
    r = _cli(proj, "benchmark", "keywords", "B001", " a ;b; ")
    assert r.exit_code == 0, r.output
    assert "B001: a; b (typed)" in r.output
    assert _cli(proj, "benchmark", "keywords", "B404", "x").exit_code == 1
    ch = journal.changes(proj, kind="benchmark_keywords")
    assert len(ch) == 1 and "B001" in ch[0]["summary"]

    (tmp_path / "kw.ris").write_text(RIS_KW, encoding="utf-8")
    add_file(proj, "scopus", tmp_path / "kw.ris")
    r = CliRunner().invoke(cli_app, ["import", "run", "--dir", str(proj.root)], terminal_width=300)
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "keywords copied to B002" in out and "Keywords copied from the import to seed paper B002" in out
    assert "B001" not in out.split("Keywords copied")[1].split(".")[0]
    papers = {p.benchmark_id: p for p in proj.read_benchmark()}
    assert (papers["B001"].keywords, papers["B001"].keywords_from) == ("a; b", "researcher")
    assert (papers["B002"].keywords, papers["B002"].keywords_from) == ("voice; silence", "import")
    assert (papers["B003"].keywords, papers["B003"].keywords_from) == ("", "")
    assert len(journal.changes(proj, kind="benchmark_keywords")) == 1
    imp = journal.changes(proj, kind="benchmark_keywords_import")
    assert len(imp) == 1 and "B002" in imp[0]["summary"]

    # the page: rows carry keywords and their origin; the Setup list shows "from import" for B002 only
    client = TestClient(create_app(proj))
    rows = client.get("/api/benchmark/seed").json()["rows"]
    by_id = {x["benchmark_id"]: x for x in rows}
    assert by_id["B002"]["keywords_from"] == "import" and by_id["B001"]["keywords_from"] == "researcher"
    html = _seed_page(rows, "console.log(JSON.stringify({html: $('#setup-seed').innerHTML}));")["html"]

    def row(bid):
        i = html.index(f'data-assign="{bid}"')
        return html[i:html.index("</tr>", i)]
    assert 'data-keywords="B002"' in row("B002") and 'value="voice; silence"' in row("B002") and "from import" in row("B002")
    assert 'value="a; b"' in row("B001") and "from import" not in row("B001")
    assert "from import" not in row("B003")

    # the same engine on the route: typed on B003, then cleared (both fields empty)
    assert client.post("/api/benchmark/keywords", json={"benchmark_id": "B003", "keywords": "x; y"}).json()[
        "keywords_from"] == "researcher"
    got = client.post("/api/benchmark/keywords", json={"benchmark_id": "B003", "keywords": " "}).json()
    assert (got["keywords"], got["keywords_from"]) == ("", "")
    assert client.post("/api/benchmark/keywords", json={"benchmark_id": "B404", "keywords": "x"}).status_code == 404
    for c in journal.changes(proj, kind="benchmark_keywords")[:2]:   # newest first: B003 cleared, B003 typed
        journal.undo(proj, c["change_id"], author="tester", other=True)

    # a second import of a file with B002's DOI never overwrites (B002 now has keywords): nothing copied
    (tmp_path / "kw2.ris").write_text(RIS_KW.replace("Unrelated record", "Unrelated record two"), encoding="utf-8")
    add_file(proj, "scopus", tmp_path / "kw2.ris")
    r = client.post("/api/import/run").json()
    assert not any(f.get("keywords_copied") for f in r["files"]) and "Keywords copied" not in (r["notice"] or "")

    # undo: the B001 change waits for the later import copy; undone in order, benchmark.csv is as before typing
    with pytest.raises(ValueError, match="changed since"):
        journal.undo(proj, ch[0]["change_id"], author="tester", other=True)
    journal.undo(proj, imp[0]["change_id"], author="tester", other=True)
    journal.undo(proj, ch[0]["change_id"], author="tester", other=True)
    assert (proj.root / "benchmark.csv").read_text(encoding="utf-8") == before_typed

    # the resolve path adds no keywords
    assert "keywords" not in seed._openalex_row({"id": "W1", "title": "T", "keywords": [{"display_name": "k"}]})


def test_hidden_validation_papers_not_named_by_import_or_listing(proj, tmp_path):
    """While the validation set is hidden, an import neither copies keywords onto a Validation paper nor names it, and
    the seed list does not repeat "the query already finds it" beside one (that would tell, paper by paper, what the
    query retrieves in the hidden set); the match warning is still given when a paper enters Validation, and the list
    shows it once the set is shown (here: validation_after_freeze switched off)."""
    from wider.core import journal
    from wider.ingest.importer import add_file

    other = Project.init(tmp_path / "with_scopus", platforms=["scopus"])
    proj.config.platforms = other.config.platforms
    other.close()
    proj.save()
    assert _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.001").exit_code == 0
    assert _cli(proj, "benchmark", "add", "--doi", "10.1000/synth.002", "--set", "validation").exit_code == 0
    (tmp_path / "kw.ris").write_text(RIS_KW, encoding="utf-8")
    add_file(proj, "scopus", tmp_path / "kw.ris")
    r = CliRunner().invoke(cli_app, ["import", "run", "--dir", str(proj.root)], terminal_width=300)
    assert r.exit_code == 0, r.output
    assert "keywords copied to B001" in " ".join(r.output.split()) and "B002" not in r.output
    papers = {p.benchmark_id: p for p in proj.read_benchmark()}
    assert (papers["B002"].keywords, papers["B002"].keywords_from) == ("", "")
    imp = journal.changes(proj, kind="benchmark_keywords_import")
    assert len(imp) == 1 and "B002" not in imp[0]["summary"]

    client = TestClient(create_app(proj))
    rows = {x["benchmark_id"]: x for x in client.get("/api/benchmark/seed").json()["rows"]}
    assert not any("already finds" in n for n in rows["B002"]["notices"])
    assert "already finds" not in _cli(proj, "benchmark", "list").output
    # entering Validation still warns about that one paper
    r = _cli(proj, "benchmark", "set", "B001", "validation")
    assert r.exit_code == 0 and "query already finds it" in " ".join(r.output.split())

    proj.config.protocol.identification.validation_after_freeze = False
    proj.save()
    rows = {x["benchmark_id"]: x for x in client.get("/api/benchmark/seed").json()["rows"]}
    assert any("already finds" in n for n in rows["B002"]["notices"])


def test_setup_page_shows_entry_notices_after_an_add_and_a_resolve():
    """The plain seed list leaves out "the query already finds it" beside a hidden Validation paper, so the page shows
    the add and resolve replies' entry notices on the new rows (ui-tester new-researcher F24)."""
    import json as _json

    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    note = "B011 matches an imported record (a.ris, record 4); it is kept in Validation, but the query already finds it."
    row = {"benchmark_id": "B011", "set_name": "Validation", "source": "prior_review", "doi": "10.9999/syn.004",
           "resolved": "pending", "notices": []}
    replies = {"/api/benchmark/seed": {"rows": [row], "sources": ["prior_review", "researcher"], "dev_share": 0.6},
               "/api/benchmark/add": {"added": [{"benchmark_id": "B011"}], "rows": [{**row, "notices": [note]}],
                                      "notices": {"B011": [note]}},
               "/api/benchmark/resolve": {"rows": [{**row, "notices": [note]}], "summary": "1 resolved"}}
    js = (ESC + PAGE + f"Object.assign(replies, {_json.dumps(replies)});\nglobal.renderLookupsCard = () => {{}};\n"
          + (STATIC / "setup.js").read_text(encoding="utf-8") + """
await window.loadSeed(); await flush();
const plain = $("#setup-seed").innerHTML.includes("already finds it");
$("#seed-text").value = "10.9999/syn.004";
await $("#seed-add").onclick(); await flush();
const added = { list: $("#setup-seed").innerHTML.split("already finds it").length - 1, msg: $("#seed-msg").innerHTML };
await window.loadSeed(); await flush();
const reloaded = $("#setup-seed").innerHTML.includes("already finds it");
await $("#seed-resolve").onclick(); await flush();
const resolved = $("#setup-seed").innerHTML.split("already finds it").length - 1;
console.log(JSON.stringify({ plain, added, reloaded, resolved }));""")
    out = run_node(js)
    assert out["plain"] is False and out["reloaded"] is False   # the plain list never says it while the set is hidden
    assert out["added"] == {"list": 1, "msg": "1 added; see the note beside B011"}
    assert out["resolved"] == 1
