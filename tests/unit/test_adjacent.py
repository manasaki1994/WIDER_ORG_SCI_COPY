"""Adjacent-literature loop: artifact, researcher entries, LLM find with a fake provider, marginal cases in the development
set, per-literature evaluation, screening label. Synthetic project only (topic: urban trees vs. indoor plants)."""
import json

import pytest
from fastapi.testclient import TestClient

from wider.construct import adjacent as adj
from wider.construct import artifacts
from wider.core.models import BenchmarkPaper, EmbeddingConfig, ProviderConfig, SamplingBand
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.sample.design import draw_development, draw_probability
from wider.sample.labels import label
from wider.screen.codebook import build, save_prompt_artifact
from wider.screen.engine import screen
from wider.screen.evaluate import evaluate, write_report

TREES = "street tree canopy cooling shade city pavement"
PLANTS = "indoor potted plant office air humidity desk"
ROOFS = "green roof membrane runoff building insulation"
CODEBOOK = {
    "question": "Does street tree canopy cool city pavements?",
    "target_description": "Studies measuring pavement or air temperature under street tree canopy in cities.",
    "elements": [{"id": "e1", "name": "Street trees", "rule": "Street trees or urban canopy are studied."},
                 {"id": "e2", "name": "Temperature outcome", "rule": "A cooling or temperature outcome is measured."}],
    "exclusion_reasons": [{"code": "a", "text": "no street trees", "element": "e1"}, {"code": "b", "text": "no temperature outcome", "element": "e2"}],
    "checks": [], "edge_cases": [],
    "adjacent_literatures": [{"name": "Indoor plants", "strains_element": "e1", "decision": "exclude", "reason_code": "a",
                              "note": "potted plants in offices, no canopy"}],
}


def test_artifact_validation(tmp_path):
    p = Project.init(tmp_path / "r")
    good = {"literatures": [{"key": "indoor_plants", "name": "Indoor plants", "description": "potted plants indoors", "origin": "researcher",
                             "status": "approved", "example_phrases": ["potted plant", "office air"]}]}
    assert artifacts.validate("adjacent", good) is None
    assert artifacts.validate("adjacent", {"literatures": [{"key": "Bad Key", "name": "x", "description": "", "origin": "researcher", "status": "approved"}]})
    assert artifacts.validate("adjacent", {"literatures": [{"key": "ok", "name": "x", "description": "", "origin": "nobody", "status": "approved"}]})
    row = artifacts.save_new_version(p, "adjacent", good, origin="manual")
    assert row["version"] == 1 and row["status"] == "draft"
    assert "adjacent" in artifacts.KINDS
    p.close()


def test_seed_add_approve_round_trip(tmp_path):
    p = Project.init(tmp_path / "r")
    assert adj.seed_from_codebook(p)["added"] == []  # no codebook yet
    artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")
    s = adj.seed_from_codebook(p)
    assert s["added"] == ["indoor_plants"] and s["codebook_version"] == 1
    row, lits = adj.load_list(p)
    assert lits[0]["status"] == "proposed" and lits[0]["origin"] == "researcher"
    assert lits[0]["description"] == "potted plants in offices, no canopy" and lits[0]["differs_from_construct"] == "e1: exclude (reason a)"
    assert adj.approved_literatures(p) == []
    assert adj.seed_from_codebook(p)["added"] == []  # idempotent
    r = adj.add(p, "Green roofs", description="roof membranes and runoff", example_phrases=["green roof", "runoff"])
    assert r["version"] == 2
    keys = [e["key"] for e in adj.load_list(p)[1]]
    assert keys == ["indoor_plants", "green_roofs"]
    adj.set_status(p, "indoor_plants", "approved")
    assert [e["key"] for e in adj.approved_literatures(p)] == ["indoor_plants", "green_roofs"]
    adj.set_status(p, "green_roofs", "rejected")
    assert [e["key"] for e in adj.approved_literatures(p)] == ["indoor_plants"]
    with pytest.raises(ValueError):
        adj.set_status(p, "nope", "approved")
    with pytest.raises(ValueError):
        adj.set_status(p, "indoor_plants", "frozen")
    e = adj.update_entry(p, "indoor_plants", discriminating_cue="plants on desks")
    assert e["entry"]["discriminating_cue"] == "plants on desks"
    assert "green roof" in adj.embedding_text(adj.load_list(p)[1][1])
    sm = adj.summary(p)
    assert sm["row"]["version"] == len(sm["versions"]) and len(sm["approved"]) == 1
    p.close()


@pytest.fixture
def project(tmp_path):
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.cluster import cluster
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.config.default_provider = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=6, n=None), SamplingBand(name="S2", start=7, end=None, n=6)]
    p.config.questions = ["Does street tree canopy cool city pavements?"]
    p.save()
    ris = []
    for i in range(45):
        topic = [TREES, PLANTS, ROOFS][i % 3]
        ris.append(f"TY  - JOUR\nTI  - Paper {i} about {topic}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {topic} {'extra words ' * 10}\nER  -\n")
    (tmp_path / "a.ris").write_text("\n".join(ris))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    embed_corpus(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title=f"Paper 0 about {TREES}", doi="10.1000/p0", set="Development",
                                      abstract=f"{TREES} " + "extra words " * 10)])
    artifacts.save_new_version(p, "anchors", {"anchors": {"trees": " ".join([TREES] * 6)}}, origin="manual")
    rank(p, top_n=1)
    cluster(p, k=3, top=45)
    artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")
    return p


_ORIG_INIT = fakemod.FakeProvider.__init__


def _fake(monkeypatch, responder):
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: _ORIG_INIT(self, cfg, responder))


def test_find_candidates_with_fake_provider(project, monkeypatch):
    from wider.rank.adjacent import candidate_clusters, find_candidates

    cc = candidate_clusters(project, top=45)
    assert cc["n_clusters"] == 3 and cc["k"] == 3 and cc["with_benchmark"] == 1 and len(cc["candidates"]) == 2  # the benchmark cluster is skipped
    assert all(c["benchmark_hits"] == 0 and len(c["records"]) == 5 and c["terms"] for c in cc["candidates"])
    assert not any("canopy" in r["title"] for c in cc["candidates"] for r in c["records"])
    seen = []

    def respond(req):
        seen.append(req.user)
        plants = "potted" in req.user
        quote = "indoor potted plant office air" if plants else "not a sentence from the abstracts"
        rid = int(req.user.split("[record ")[1].split("]")[0])
        return {"is_adjacent": True, "already_listed": False, "key": "indoor_plants" if plants else "other", "name": "Indoor plants" if plants else "Other",
                "description": "potted plants in offices", "differs_from_construct": "indoors, no canopy", "discriminating_cue": "office desks",
                "example_phrases": ["potted plant"], "quotes": [{"record_id": rid, "quote": quote}]}
    _fake(monkeypatch, respond)
    r = find_candidates(project, top=45)
    assert all(c["ok"] and c["attempts"] == 1 for c in r["calls"]), r["calls"]
    assert len(seen) == 2 and r["clusters_considered"] == 2 and r["with_benchmark"] == 1
    assert [e["key"] for e in r["candidates"]] == ["indoor_plants"] and len(r["skipped"]) == 1  # unverified quotes are refused
    assert "verified" in r["skipped"][0]["why"]
    ev = r["candidates"][0]["evidence"]
    assert ev["quotes"] and ev["cluster"] is not None and ev["terms"]
    assert r["added"] == ["indoor_plants"] and r["version"] == 1
    row, lits = adj.load_list(project)
    assert lits[0]["origin"] == "tool" and lits[0]["status"] == "proposed" and row["origin"] == "llm"
    assert any(True for _ in project.root.rglob("candidates.json")), r["run_dir"]
    # second run: same literature again is merged, not duplicated
    r2 = find_candidates(project, top=45, max_clusters=1)
    assert r2["clusters_considered"] == 1 and r2["added"] == [] and len(adj.load_list(project)[1]) == 1


def test_draw_development_adjacent_quota(project):
    from wider.sample.design import sampling_log

    draw_probability(project)
    base = draw_development(project, cluster_max_rank=20, near_miss_max_rank=45, name="dev0")
    assert base["design"]["per_adjacent"] == 4 and base["design"]["adjacent"] is None  # nothing approved → pools unchanged
    assert not any(r["pool"].startswith("adjacent:") for r in project.store.query(
        "SELECT sr.pool FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='dev0'"))
    adj.add(project, "Indoor plants", description=PLANTS, example_phrases=["potted plant", "office air"])
    adj.add(project, "Green roofs", description=ROOFS, example_phrases=["green roof"], status="proposed")
    d = draw_development(project, cluster_max_rank=20, near_miss_max_rank=45, name="dev1", per_adjacent=3)
    assert d["design"]["adjacent"]["keys"] == ["indoor_plants"] and d["design"]["per_adjacent"] == 3
    rows = [dict(r) for r in project.store.query(
        "SELECT sr.record_id, sr.pool, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id JOIN records m ON m.record_id=sr.record_id WHERE s.name='dev1'")]
    adj_rows = [r for r in rows if r["pool"] == "adjacent:indoor_plants"]
    assert len(adj_rows) == 3 and all("potted" in r["title"] for r in adj_rows)
    assert not any(r["pool"].startswith("adjacent:green_roofs") for r in rows)
    assert sampling_log(project)["dev1"]["adjacent"]["keys"] == ["indoor_plants"]
    d2 = draw_development(project, cluster_max_rank=20, near_miss_max_rank=45, name="dev2", per_adjacent=0)
    assert d2["design"]["adjacent"] is None


def test_codebook_label_and_evaluate_grouping(project, monkeypatch):
    b0 = build(project)
    assert "adjacent_literature" not in b0["schema"]["properties"] and "adjacent_literature" not in b0["label_groups"]
    adj.add(project, "Indoor plants", description=PLANTS, discriminating_cue="potted plants in offices", example_phrases=["potted plant"])
    b = build(project)
    assert b["schema"]["properties"]["adjacent_literature"]["enum"] == ["indoor_plants", "none", "not_applicable"]
    assert "adjacent_literature" in b["label_groups"] and "adjacent_literature" in b["schema"]["required"]
    assert "Indoor plants" in b["prompt"] and "potted plants in offices" in b["prompt"] and "whatever the decision" in b["prompt"]
    save_prompt_artifact(project, b)
    draw_probability(project)
    draw_development(project, cluster_max_rank=20, near_miss_max_rank=45, per_adjacent=3)

    def respond(req):  # the model mistakes potted-plant papers for the construct (verified quotes, so the rule keeps its include)
        trees, plants = "canopy" in req.user, "potted" in req.user
        quote = "street tree canopy cooling" if trees else ("indoor potted plant office" if plants else "green roof membrane runoff")
        return {"summary": "s", "e1": "present" if (trees or plants) else "absent", "e1_quote": quote,
                "e2": "present", "e2_quote": "extra words extra words", "target_fit": "core" if trees else "adjacent",
                "decision": "include" if (trees or plants) else "exclude", "exclusion_reason": "none" if (trees or plants) else "a",
                "missing_for_maybe": "", "adjacent_literature": "indoor_plants" if plants else "none"}
    _fake(monkeypatch, respond)
    for r in project.store.query("SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                                 "JOIN records m ON m.record_id=sr.record_id WHERE s.name='development'"):
        label(project, "development", r["record_id"], "alice", "include" if "canopy" in r["title"] else "exclude")
    res = screen(project, "sample:development", pass_name="A")   # labelled first (D4)
    assert res["n_err"] == 0
    ev = evaluate(project, res["run_id"], "development")
    ba = ev["by_adjacent"]["indoor_plants"]
    assert ba["n_drawn"] == 3 and ba["n_model"] >= 3 and ba["FALSE INCLUDE"] == ba["n"] >= 3, ba  # the model includes every potted-plant paper
    assert all(c["drawn_literature"] == "indoor_plants" or c["model_literature"] == "indoor_plants" for c in ev["adjacent_cases"])
    assert ev["adjacent_cases"][0]["category"] == "FALSE INCLUDE" and ev["adjacent_cases"][0]["quotes"]["e1"]
    text = write_report(project, ev)
    assert "## By adjacent literature" in text and "| indoor_plants |" in text
    # the label is stored with the corpus decisions and counted in the report
    from wider.recall.adjacent import exclusions_by_literature
    from wider.recall.report import build_report, to_markdown
    from wider.screen.protocol import run_protocol
    run_protocol(project, "corpus", "main")
    ex = exclusions_by_literature(project)
    assert ex["main"]["indoor_plants"]["include"] == 15
    rep = build_report(project)
    assert rep["adjacent"]["literatures"][0]["key"] == "indoor_plants" and rep["adjacent"]["by_protocol"]["main"]["indoor_plants"]
    md = to_markdown(rep)
    assert "## Adjacent literatures" in md and "Indoor plants" in md
    assert ["excluded as adjacent literature: indoor_plants", 0] in rep["prisma"]


def test_ui_routes(project, monkeypatch):
    from wider.ui.app import create_app

    c = TestClient(create_app(project))
    assert c.get("/api/adjacent").json()["literatures"] == []
    r = c.post("/api/adjacent/add", json={"name": "Indoor plants", "description": PLANTS, "discriminating_cue": "potted plants"})
    assert r.status_code == 200 and r.json()["version"] == 1
    assert c.post("/api/adjacent/seed").json()["added"] == []  # codebook entry has the same key
    assert c.post("/api/adjacent/indoor_plants/status/rejected").status_code == 200
    assert c.post("/api/adjacent/nope/status/approved").status_code == 400
    assert c.post("/api/adjacent/indoor_plants/edit", json={"name": "Indoor plants and office air"}).json()["entry"]["name"] == "Indoor plants and office air"
    d = c.get("/api/adjacent").json()
    assert d["literatures"][0]["status"] == "rejected" and d["approved"] == []
    _fake(monkeypatch, lambda req: {"literatures": [{"key": "green_roofs", "name": "Green roofs", "description": ROOFS, "differs_from_construct": "roofs",
                                                     "discriminating_cue": "membrane", "example_phrases": ["green roof"]}]})
    r = c.post("/api/adjacent/propose")
    assert r.status_code == 200 and r.json()["added"] == ["green_roofs"]
    _fake(monkeypatch, lambda req: {"is_adjacent": False, "already_listed": False, "key": "", "name": "", "description": "", "differs_from_construct": "",
                                    "discriminating_cue": "", "example_phrases": [], "quotes": []})
    r = c.post("/api/adjacent/find", json={"top": 45})
    assert r.status_code == 200 and r.json()["added"] == [] and r.json()["clusters_considered"] == 2
    assert all("not an adjacent" in s["why"] for s in r.json()["skipped"])
    assert c.get("/api/adjacent/cases/nope").status_code in (200, 400)
    html = c.get("/").text
    assert 'id="adj-card"' in html and "adjacent.js" in html and 'id="adj-cases-card"' in html
    assert c.get("/static/adjacent.js").status_code == 200
    assert json.loads(json.dumps(d))  # response is plain JSON


def test_cli_propose_lists_added_and_fails_cleanly(project, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.providers.base import ProviderError

    runner, d = CliRunner(), ["--dir", str(project.root)]
    adj.add(project, "Indoor plants", description=PLANTS)
    _fake(monkeypatch, lambda req: {"literatures": [
        {"key": "indoor_plants", "name": "Indoor plants", "description": PLANTS, "differs_from_construct": "indoor",
         "discriminating_cue": "potted", "example_phrases": []},
        {"key": "green_roofs", "name": "Green roofs", "description": ROOFS, "differs_from_construct": "roofs",
         "discriminating_cue": "membrane", "example_phrases": ["green roof"]}]})
    r = runner.invoke(app, ["construct", "adjacent", "propose", *d])
    assert r.exit_code == 0, r.output
    assert "2 proposed, 1 added: adjacent v2 saved" in r.output
    assert "green_roofs: Green roofs" in r.output and "membrane" in r.output and "indoor_plants:" not in r.output
    r = runner.invoke(app, ["construct", "adjacent", "propose", *d])  # same answer again: nothing new
    assert r.exit_code == 0 and "nothing new added" in r.output and "no new version saved" in r.output
    assert len(artifacts.list_versions(project, "adjacent")) == 2

    def boom(req):
        raise ProviderError("backend unavailable", retryable=False)

    _fake(monkeypatch, boom)
    r = runner.invoke(app, ["construct", "adjacent", "propose", *d])
    assert r.exit_code == 1 and "adjacent proposal failed" in r.output and "Traceback" not in r.output
    assert len(artifacts.list_versions(project, "adjacent")) == 2


def test_cli_propose_without_literatures_is_a_message(project, monkeypatch):
    """The CLI twin of the page's 400: an empty reply is named, exit 1, no traceback, no new version."""
    from typer.testing import CliRunner

    from wider.cli import app

    n_versions = len(artifacts.list_versions(project, "adjacent"))
    _fake(monkeypatch, lambda req: {"literatures": []})
    r = CliRunner().invoke(app, ["construct", "adjacent", "propose", "--dir", str(project.root)])
    out = " ".join(r.output.split())
    assert r.exit_code == 1 and "Traceback" not in r.output, r.output
    assert "adjacent proposal failed: invalid structured output" in out and "should be non-empty" in out
    assert len(artifacts.list_versions(project, "adjacent")) == n_versions


def test_ui_propose_failure_is_a_message_not_a_500(project, monkeypatch):
    """A model answer that fails validation (here an empty list) is reported with its message; nothing is added."""
    from wider.ui.app import create_app

    c = TestClient(create_app(project))
    before = c.get("/api/adjacent").json()["literatures"]
    n_versions = len(artifacts.list_versions(project, "adjacent"))
    _fake(monkeypatch, lambda req: {"literatures": []})
    r = c.post("/api/adjacent/propose")
    assert r.status_code == 400 and r.json()["detail"].startswith("adjacent proposal failed") and "Traceback" not in r.text
    assert c.get("/api/adjacent").json()["literatures"] == before
    assert len(artifacts.list_versions(project, "adjacent")) == n_versions
    # a provider that is not in project.yaml is a message too (the CLI says the same), never a 500
    r = c.post("/api/adjacent/propose?provider=nope")
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    # a model error is a message too
    from wider.providers.base import ProviderError

    def boom(req):
        raise ProviderError("backend unavailable", retryable=False)

    _fake(monkeypatch, boom)
    r = c.post("/api/adjacent/propose")
    assert r.status_code == 400 and r.json()["detail"].startswith("adjacent proposal failed") and "Traceback" not in r.text
    assert len(artifacts.list_versions(project, "adjacent")) == n_versions


def test_adjacent_uses_construct_override(project, monkeypatch):
    from wider.construct.adjacent import propose_from_question
    from wider.rank.adjacent import find_candidates

    project.config.providers.append(ProviderConfig(name="fake-c", kind="fake", model="fake-c"))
    project.config.stage_providers["construct"] = "fake-c"
    project.save()
    used = []

    def init(self, cfg, responder_=None):
        used.append(cfg.name)
        _ORIG_INIT(self, cfg, lambda req: {"literatures": [
            {"key": "green_roofs", "name": "Green roofs", "description": ROOFS, "differs_from_construct": "roofs",
             "discriminating_cue": "membrane", "example_phrases": []}]} if "[record " not in req.user else {
            "is_adjacent": False, "already_listed": False, "key": "x", "name": "x", "description": "", "differs_from_construct": "",
            "discriminating_cue": "", "example_phrases": [], "quotes": []})
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", init)
    propose_from_question(project)
    find_candidates(project, top=45, max_clusters=1)
    assert used == ["fake-c", "fake-c"]          # the construct override, not the default "fake"
    used.clear()
    propose_from_question(project, provider_name="fake")   # an explicit --provider still wins
    assert used == ["fake"]


def test_adjacent_provider_help_names_override():
    import re

    from typer.testing import CliRunner

    from wider.cli import app

    for cmd in (["construct", "propose"], ["construct", "adjacent", "propose"], ["construct", "adjacent", "find"]):
        r = CliRunner().invoke(app, [*cmd, "--help"], env={"COLUMNS": "200"})
        text = re.sub(r"[\s│]+", " ", r.output)
        assert r.exit_code == 0 and "default: the construct stage provider, else the project default" in text, cmd


def test_adjacent_edit_on_the_command_line(project):
    """The CLI twin of the page's Edit dialog (accessibility-and-naming D1): given options change, the rest stay;
    an unknown key exits 1 with a plain message and saves nothing."""
    from typer.testing import CliRunner

    from wider.cli import app

    runner, d = CliRunner(), ["--dir", str(project.root)]
    key = adj.add(project, "Indoor plants", description=PLANTS, discriminating_cue="potted")["entry"]["key"]
    r = runner.invoke(app, ["construct", "adjacent", "edit", key, "--name", "House plants", "--differs", "indoor only", *d])
    assert r.exit_code == 0, r.output
    assert "adjacent v2 saved" in r.output
    e = next(e for e in adj.load_list(project)[1] if e["key"] == key)
    assert e["name"] == "House plants" and e["differs_from_construct"] == "indoor only"
    assert e["description"] == PLANTS and e["discriminating_cue"] == "potted"
    r = runner.invoke(app, ["construct", "adjacent", "edit", "no_such", "--name", "X", *d])
    assert r.exit_code == 1 and "no adjacent literature 'no_such'" in r.output and "Traceback" not in r.output
    assert len(artifacts.list_versions(project, "adjacent")) == 2
