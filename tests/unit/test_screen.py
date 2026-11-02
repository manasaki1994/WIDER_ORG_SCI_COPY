
import pytest

from wider.construct import artifacts
from wider.core.models import ProviderConfig, SamplingBand
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.sample.design import draw_development, draw_probability
from wider.sample.labels import label
from wider.screen.codebook import build, save_prompt_artifact
from wider.screen.decisions import decisions
from wider.screen.engine import check_run_args, replay, screen
from wider.screen.evaluate import evaluate
from wider.screen.protocol import majority, run_protocol, second_screen
from wider.screen.rule import apply_rule

CODEBOOK = {
    "question": "What leads employees to speak up to someone who could act on what they say?",
    "target_description": "Studies in which employees voice concerns or suggestions to a party who could act on them.",
    "elements": [
        {"id": "e1", "name": "Voice behaviour measured", "rule": "Speaking up, staying silent or reporting is measured or manipulated."},
        {"id": "e2", "name": "Directed at someone who could act", "rule": "The message goes to a party other than the speaker."},
    ],
    "exclusion_reasons": [{"code": "a", "text": "no voice measured", "element": "e1"}, {"code": "b", "text": "no addressee", "element": "e2"},
                          {"code": "e", "text": "not empirical"}],
    "checks": [{"key": "grievance", "question": "Is a formal grievance outcome analysed as a form of voice?", "if_yes": "include"},
               {"key": "prevalence_only", "question": "Is only how often employees speak up compared?", "if_yes": "maybe"}],
    "edge_cases": [{"key": "non_empirical", "case": "Reviews and essays on the topic", "default": "maybe"}],
    "labels": [{"group": "responder", "values": ["supervisor", "senior_management", "peers"]}],
}


def test_build_prompt_and_schema(tmp_path):
    p = Project.init(tmp_path / "r")
    artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")
    b = build(p)
    assert "two elements" in b["prompt"] and "grievance" in b["prompt"] and "e2_quote" in b["prompt"]
    assert b["schema"]["required"][:3] == ["summary", "e1", "e1_quote"] and "responder" in b["schema"]["properties"]
    assert b["include_checks"] == ["grievance"] and b["maybe_checks"] == ["prevalence_only"]
    row = save_prompt_artifact(p, b)
    assert row["version"] == 1


SPEC = {"elements": ["e1", "e2"], "include_checks": ["grievance"], "maybe_checks": ["prevalence_only"], "absent_checks": [],
        "maybe_edge_cases": ["non_empirical"]}
TEXT = "Title: Speaking up after errors. Abstract: Junior employees spoke up less often than senior employees about identical errors to supervisors."


def test_rule_overrides():
    base = {"decision": "exclude", "exclusion_reason": "a", "target_fit": "adjacent", "e1": "absent", "e1_quote": "not in text at all",
            "e2": "present", "e2_quote": "about identical errors to supervisors", "grievance": False, "prevalence_only": False, "edge_case": "none"}
    r = apply_rule(base, TEXT, SPEC)
    assert r["final"] == "maybe" and "absent_unverified:e1" in r["flags"] and "override:no_grounds" in r["flags"]
    ok = dict(base, e1_quote="Junior employees spoke up less")
    r = apply_rule(ok, TEXT, SPEC)
    assert r["final"] == "exclude" and r["verified"]["e1"]
    r = apply_rule(dict(ok, grievance=True), TEXT, SPEC)
    assert r["final"] == "maybe" and "override:ruling" in r["flags"]
    r = apply_rule(dict(base, decision="include", prevalence_only=True), TEXT, SPEC)
    assert r["final"] == "maybe"
    r = apply_rule(dict(base, decision="include", edge_case="non_empirical"), TEXT, SPEC, thin=True)
    assert r["final"] == "maybe" and "thin" in r["flags"]


def _responder(counter):
    def respond(req):
        counter["calls"] += 1
        hit = "voice" in req.user.lower()
        dec = "include" if hit else "exclude"
        if counter.get("flip") and counter["calls"] % 2 == 0 and hit:
            dec = "maybe"
        return {"summary": "s", "e1": "present" if hit else "absent",
                "e1_quote": "employee voice speaking up" if hit else "ocean plankton temperature bloom",
                "e2": "present", "e2_quote": "extra words extra words", "grievance": False, "prevalence_only": False,
                "target_fit": "core" if hit else "unrelated", "edge_case": "none", "decision": dec,
                "exclusion_reason": "none" if hit else "a", "missing_for_maybe": "", "responder": "supervisor" if hit else "not_applicable"}
    return respond


def label_development(p, reviewer="alice"):
    """The true labels on the development sample (voice papers are includes): runs that include development records
    wait until every one has a final label (ui-redesign D4)."""
    for r in p.store.query("SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                           "JOIN records m ON m.record_id=sr.record_id WHERE s.name='development'"):
        voice = "voice" in r["title"]
        label(p, "development", r["record_id"], reviewer, "include" if voice else "exclude",
              reason="" if voice else "a no voice measured")


@pytest.fixture
def project(tmp_path):
    """The synthetic project with its development sample fully labelled."""
    p = _make(tmp_path)
    label_development(p)
    return p


@pytest.fixture
def project_open(tmp_path):
    """The synthetic project, its development sample drawn and not labelled."""
    return _make(tmp_path)


def _make(tmp_path):
    from wider.core.models import EmbeddingConfig
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.config.default_provider = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=6, n=None), SamplingBand(name="S2", start=7, end=None, n=6)]
    p.save()
    ris = []
    for i in range(40):
        topic = "employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent" if i % 3 == 0 else "ocean plankton temperature bloom"
        ris.append(f"TY  - JOUR\nTI  - Paper {i} about {topic}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {topic} {'extra words ' * 10}\nER  -\n")
    (tmp_path / "a.ris").write_text("\n".join(ris))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    embed_corpus(p)
    anchor = " ".join(["employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent"] * 4)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": anchor}}, origin="manual")
    rank(p, top_n=1)
    artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")
    save_prompt_artifact(p, build(p))
    draw_probability(p)
    draw_development(p, cluster_max_rank=20, near_miss_max_rank=20)
    return p


def test_engine_protocol_second_evaluate_replay(project, monkeypatch):
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    res = screen(project, "sample:development", pass_name="A")
    assert res["n_err"] == 0 and res["n_ok"] == res["n_records"] > 0
    calls_after = counter["calls"]
    res2 = screen(project, "sample:development", pass_name="A")  # resumable: nothing to do
    assert res2["resumed"] == res["n_records"] and counter["calls"] == calls_after
    run_dir = project.root / project.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    assert (run_dir / "responses.jsonl").exists() and (run_dir / "decisions.csv").exists() and (run_dir / "run_spec.json").exists()
    # human labels on the development set: voice papers are includes
    for r in project.store.query("SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                                 "JOIN records m ON m.record_id=sr.record_id WHERE s.name='development'"):
        label(project, "development", r["record_id"], "alice", "include" if "voice" in r["title"] else "exclude",
              reason="" if "voice" in r["title"] else "a no voice measured")
    ev = evaluate(project, res["run_id"], "development")
    assert ev["misses"] == 0 and ev["false_includes"] == 0 and ev["n"] == res["n_records"]
    with pytest.raises(ValueError):
        evaluate(project, res["run_id"], "validation")
    rp = replay(project, res["run_id"])
    assert rp["replayed"] == res["n_records"] and rp["changed"] == 0
    # corpus protocol with disagreements between passes → third read → majority
    counter["flip"] = True
    pr = run_protocol(project, "corpus", "main")
    dec = decisions(project, "main")
    assert pr["n"] == 40 and len(dec) == 40 and set(dec.values()) <= {"include", "exclude", "maybe"}
    assert pr["disagreements"] >= 0
    counter["flip"] = False
    if "maybe" in dec.values():
        ss = second_screen(project, "main")
        dec2 = decisions(project, "main_second")
        assert len(dec2) == 40 and ss["maybes_reread"] == list(dec.values()).count("maybe")


def test_evaluation_names_unadjudicated_labels_and_an_open_sample(project, monkeypatch):
    """manual-review D9: ana and bo disagree on 2 records of the unlocked development sample, none adjudicated: the
    evaluation names them and whose labels it uses, and says once that the sample is not locked (CLI the same);
    after adjudicating both and locking the sample neither sentence appears."""
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.sample.labels import adjudicate, lock

    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    res = screen(project, "sample:development", pass_name="A")
    # the labels below come after the run; History times have one-second resolution, so on a slow machine they would
    # add the D6 changed-after line, which this test is not about: the run is dated after them
    with project.store.tx() as con:
        con.execute("UPDATE runs SET finished_at='9999-12-31T00:00:00+00:00' WHERE run_id=?", (res["run_id"],))
    rows = [dict(r) for r in project.store.query(
        "SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        "JOIN records m ON m.record_id=sr.record_id WHERE s.name='development' ORDER BY sr.record_id")]
    for r in rows:
        label(project, "development", r["record_id"], "ana", "include" if "voice" in r["title"] else "exclude",
              reason="" if "voice" in r["title"] else "a no voice measured")
    split = [r["record_id"] for r in rows[:2]]
    for rid in split:
        theirs = project.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='ana'", (rid,))["decision"]
        label(project, "development", rid, "bo", "exclude" if theirs == "include" else "include",
              reason="a no voice measured" if theirs == "include" else "")
    said = "2 labelled records where the reviewers disagree are not adjudicated; the evaluation uses ana's labels there"
    open_line = "this sample is not locked: its labels can still change, and so can this evaluation"
    # with one reviewer required, a disagreement leaves the record without a final label: the evaluation is refused
    # until it is adjudicated (ui-redesign D4), so the unadjudicated line is never shown for a development sample
    from wider.screen.devgate import DevSampleIncomplete
    with pytest.raises(DevSampleIncomplete, match="2 disagreements wait for adjudication"):
        evaluate(project, res["run_id"], "development")
    out = CliRunner().invoke(cli_app, ["screen", "evaluate", res["run_id"], "--dir", str(project.root)])
    assert out.exit_code == 1 and said not in out.output and "label them all first" in out.output
    for rid in split:
        adjudicate(project, "development", rid, "include", by="cara")
    ev = evaluate(project, res["run_id"], "development")
    assert ev["open_disagreements"] == 0 and ev["lines"] == [open_line]
    out = CliRunner().invoke(cli_app, ["screen", "evaluate", res["run_id"], "--dir", str(project.root)])
    assert out.exit_code == 0 and out.output.count(open_line) == 1 and said not in out.output
    lock(project, "development")
    assert evaluate(project, res["run_id"], "development")["lines"] == []
    out = CliRunner().invoke(cli_app, ["screen", "evaluate", res["run_id"], "--dir", str(project.root)])
    assert "not adjudicated" not in out.output and "not locked" not in out.output


def test_repaired_call_replays(project, monkeypatch):
    """A record whose first answer failed the schema and was repaired keeps its input hash, so replay accepts it."""
    import json as _json

    counter = {"calls": 0}
    good = _responder(counter)
    seen: set = set()

    def respond(req):
        if req.user not in seen and "did not match" not in req.user:
            seen.add(req.user)
            return '{"decision": "not json enough"'
        return good(req)

    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, respond))
    res = screen(project, "sample:development", pass_name="A")
    assert res["n_err"] == 0 and res["n_ok"] == res["n_records"] > 0
    run_dir = project.root / project.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    lines = [_json.loads(x) for x in (run_dir / "responses.jsonl").read_text().splitlines() if x.strip()]
    assert lines and all(x["repaired"] for x in lines if "record_id" in x and x.get("ok"))
    rp = replay(project, res["run_id"])
    assert rp["replayed"] == res["n_records"] and rp["changed"] == 0


def test_majority():
    assert majority(["include", "include", "exclude"]) == "include"
    assert majority(["include", "maybe", "exclude"]) == "maybe"
    assert majority(["exclude", "exclude"]) == "exclude"


# ----------------------------------------------------------------------------- protocol settings and replay parity
def _fake(monkeypatch, counter, responder=_responder):
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, responder(counter)))


def _schema_responder(counter):
    """``_responder`` limited to the fields of the prompt's schema; ``counter["grievance"]`` marks every include."""
    base = _responder(counter)

    def respond(req):
        out = base(req)
        if counter.get("grievance") and out["decision"] == "include":
            out["grievance"] = True
        return {k: v for k, v in out.items() if k in req.schema_["properties"]}
    return respond


def _no_grounds_responder(counter):
    def respond(req):   # every record excluded on an absent element whose quote is not in the text
        counter["calls"] += 1
        return {"summary": "s", "e1": "absent", "e1_quote": "words that are nowhere", "e2": "present",
                "e2_quote": "extra words extra words", "grievance": False, "prevalence_only": False, "target_fit": "adjacent",
                "edge_case": "none", "decision": "exclude", "exclusion_reason": "a", "missing_for_maybe": "",
                "responder": "not_applicable"}
    return respond


def _run_dir(project, run_id):
    return project.root / project.store.one("SELECT dir FROM runs WHERE run_id=?", (run_id,))["dir"]


def _rule_decisions(project, run_id) -> dict:
    return {r["record_id"]: r["rule_decision"] for r in project.store.query(
        "SELECT record_id, rule_decision FROM screen_calls WHERE run_id=?", (run_id,))}


def test_rule_recall_first_off_keeps_the_models_decision():
    base = {"decision": "exclude", "exclusion_reason": "a", "target_fit": "adjacent", "e1": "absent", "e1_quote": "not in text at all",
            "e2": "present", "e2_quote": "about identical errors to supervisors", "grievance": False, "prevalence_only": False, "edge_case": "none"}
    off = dict(SPEC, recall_first=False)
    r = apply_rule(base, TEXT, off)
    assert r["final"] == "exclude" and "absent_unverified:e1" in r["flags"] and "override" not in r["flags"]
    assert r["effective"]["e1"] == "absent"
    assert apply_rule(dict(base, exclusion_reason="none", grievance=True), TEXT, off)["final"] == "exclude"
    # the include -> maybe checks and the maybe flags still apply
    assert apply_rule(dict(base, decision="include", prevalence_only=True), TEXT, off)["final"] == "maybe"
    r = apply_rule(dict(base, decision="include", grievance=True), TEXT, dict(off, maybe_flags=["grievance"]))
    assert r["final"] == "maybe" and "flag:grievance" in r["flags"]
    # on (the default, or the explicit parameter) keeps the rule
    r = apply_rule(base, TEXT, dict(SPEC, recall_first=True))
    assert r["final"] == "maybe" and "override:no_grounds" in r["flags"]
    assert apply_rule(base, TEXT, off, recall_first=True)["final"] == "maybe"


def test_rule_spec_recorded_and_resume_refused_when_it_changed(project, monkeypatch):
    import json

    from wider.screen.codebook import load_prompt
    from wider.screen.engine import rule_spec
    _fake(monkeypatch, {"calls": 0})
    res = screen(project, "sample:development", pass_name="A")
    stored = json.loads((_run_dir(project, res["run_id"]) / "run_spec.json").read_text())["settings"]["rule_spec"]
    _, pdata = load_prompt(project, None)
    assert stored == rule_spec(project, pdata)
    assert stored["recall_first"] is True and stored["maybe_flags"] == [] and stored["maybe_edge_cases"] == ["non_empirical"]
    assert stored["quote_min_chars"] == 8 and stored["thin_words"] == 20
    project.config.protocol.screening.recall_first = False
    with pytest.raises(ValueError, match="protocol.screening.recall_first"):
        screen(project, "sample:development", pass_name="A")
    project.config.protocol.screening.recall_first = True
    assert screen(project, "sample:development", pass_name="A")["resumed"] == res["n_records"]


def test_recall_first_off_in_a_live_run(project, monkeypatch):
    import json
    _fake(monkeypatch, {"calls": 0}, _no_grounds_responder)
    on = screen(project, "sample:development", pass_name="A")
    project.config.protocol.screening.recall_first = False
    off = screen(project, "sample:development", pass_name="B")
    assert set(_rule_decisions(project, on["run_id"]).values()) == {"maybe"}
    assert set(_rule_decisions(project, off["run_id"]).values()) == {"exclude"}
    spec = json.loads((_run_dir(project, off["run_id"]) / "run_spec.json").read_text())["settings"]["rule_spec"]
    assert spec["recall_first"] is False
    assert replay(project, off["run_id"])["changed"] == 0


def test_single_pass_protocol(project, monkeypatch):
    import json
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    project.config.protocol.screening.passes = 1
    pr = run_protocol(project, "corpus", "main")
    assert len(pr["runs"]) == 1 and counter["calls"] == 40 and pr["n"] == 40 and pr["disagreements"] == 0
    a = _rule_decisions(project, pr["runs"][0])
    rows = project.store.query("SELECT record_id, decision, votes FROM screen_decisions WHERE protocol='main'")
    assert len(rows) == 40
    assert all(r["decision"] == a[r["record_id"]] and json.loads(r["votes"]) == [a[r["record_id"]]] for r in rows)
    assert "maybe" in a.values()   # the flipped answers stand as they are: no majority, no third read
    assert not project.store.scalar("SELECT COUNT(*) FROM questions WHERE kind='screen_split'")
    from wider.core.stage_result import last_result
    assert "decided with 1 pass;" in last_result(project, "screen")["headline"]
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    help_text = " ".join(CliRunner().invoke(cli_app, ["screen", "protocol", "--help"]).output.split())
    assert "with one, pass A alone decides" in help_text.replace("│ ", "")
    # majority is checked only for two passes
    from wider.core.protocol import ReviewProtocol
    ReviewProtocol.model_validate({"screening": {"passes": 1, "blind_third_read": False, "majority": 2}})
    with pytest.raises(ValueError):
        ReviewProtocol.model_validate({"screening": {"passes": 2, "blind_third_read": False, "majority": 3}})


def test_second_screen_switched_off(project, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    run_protocol(project, "corpus", "main")
    project.config.protocol.screening.second_screen = False
    project.save()
    n_runs, calls = project.store.scalar("SELECT COUNT(*) FROM runs"), counter["calls"]
    with pytest.raises(ValueError, match="protocol.screening.second_screen is off"):
        second_screen(project, "main")
    res = CliRunner().invoke(cli_app, ["screen", "second", "main", "--dir", str(project.root)])
    assert res.exit_code == 1 and "protocol.screening.second_screen" in res.output and "Traceback" not in res.output
    c = TestClient(create_app(project))
    r = c.post("/api/jobs/screen", json={"scope": "corpus", "mode": "second", "protocol": "main"})
    assert r.status_code == 400 and "second_screen" in r.json()["detail"]
    r = c.post("/api/screen/run", json={"scope": "corpus", "mode": "second", "protocol": "main"})
    assert r.status_code == 400 and "second_screen" in r.json()["detail"]
    assert counter["calls"] == calls and project.store.scalar("SELECT COUNT(*) FROM runs") == n_runs
    assert not project.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main_second'")


def test_replay_uses_the_runs_recorded_rule_settings(project, monkeypatch):
    import json

    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core import jsonl
    # a prompt whose schema has no non_empirical edge case; grievance serves as a maybe flag
    artifacts.save_new_version(project, "codebook", {k: v for k, v in CODEBOOK.items() if k != "edge_cases"}, origin="manual")
    save_prompt_artifact(project, build(project))
    _fake(monkeypatch, {"calls": 0, "grievance": True}, _schema_responder)
    project.config.protocol.screening.maybe_flags = ["grievance"]
    ra = screen(project, "sample:development", pass_name="A")
    assert any("flag:grievance" in ln["rule"]["flags"] for ln in jsonl.read(_run_dir(project, ra["run_id"]) / "responses.jsonl"))
    assert replay(project, ra["run_id"]) == {"run_id": ra["run_id"], "replayed": ra["n_records"], "changed": 0}
    # a flag added after the run does not change its replay
    project.config.protocol.screening.maybe_flags = []
    rb = screen(project, "sample:development", pass_name="B")
    project.config.protocol.screening.maybe_flags = ["grievance"]
    rp = replay(project, rb["run_id"])
    assert rp["changed"] == 0 and "note" not in rp
    # a run recorded without rule settings replays with the current protocol's, and says so
    spec_path = _run_dir(project, rb["run_id"]) / "run_spec.json"
    d = json.loads(spec_path.read_text())
    del d["settings"]["rule_spec"]
    spec_path.write_text(json.dumps(d))
    rp = replay(project, rb["run_id"])
    assert rp["changed"] > 0 and rp["note"] == "no rule settings recorded; replayed with the current protocol's settings"
    project.save()
    res = CliRunner().invoke(cli_app, ["screen", "replay", rb["run_id"], "--dir", str(project.root)])
    assert res.exit_code == 0 and "no rule settings recorded" in res.output


def test_screening_refuses_a_bad_limit_or_an_empty_pass_name(project, monkeypatch):
    """A negative limit used to drop the last records silently; an empty pass name wrote an unnamed run."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    c = TestClient(create_app(project))
    for body, needle in [({"limit": -5}, "the record limit must be at least 1"), ({"limit": 0}, "the record limit must be at least 1"),
                         ({"pass_name": ""}, "the pass needs a name, e.g. A"), ({"pass_name": " "}, "the pass needs a name, e.g. A")]:
        r = c.post("/api/jobs/screen", json={"scope": "corpus", **body})
        assert r.status_code == 400 and needle in r.json()["detail"], body
        with pytest.raises(ValueError, match=needle):
            screen(project, "corpus", **body)
    res = CliRunner().invoke(cli_app, ["screen", "run", "corpus", "--limit", "-5", "--dir", str(project.root)])
    assert res.exit_code == 1 and "the record limit must be at least 1" in res.output and "Traceback" not in res.output
    res = CliRunner().invoke(cli_app, ["screen", "run", "corpus", "--pass", "", "--dir", str(project.root)])
    assert res.exit_code == 1 and "the pass needs a name, e.g. A" in res.output
    # the protocol runs its passes with the same limit, so it is refused the same way (eighth round, spec-checker)
    res = CliRunner().invoke(cli_app, ["screen", "protocol", "corpus", "--limit", "-2", "--dir", str(project.root)])
    assert res.exit_code == 1 and "the record limit must be at least 1" in res.output and "Traceback" not in res.output
    res = CliRunner().invoke(cli_app, ["screen", "second", "main", "--limit", "-1", "--dir", str(project.root)])
    assert res.exit_code == 1 and "the record limit must be at least 1" in res.output and "Traceback" not in res.output
    r = c.post("/api/jobs/screen", json={"scope": "corpus", "mode": "protocol", "protocol": "main", "limit": -2})
    assert r.status_code == 400 and "the record limit must be at least 1" in r.json()["detail"]
    # the second screen from the page is refused the same way (spec-checker, 28th round)
    r = c.post("/api/jobs/screen", json={"scope": "corpus", "mode": "second", "protocol": "main", "limit": -1})
    assert r.status_code == 400 and "the record limit must be at least 1" in r.json()["detail"]
    assert c.get("/api/jobs").json() == []
    assert project.store.scalar("SELECT COUNT(*) FROM screen_decisions") == 0
    assert project.store.scalar("SELECT COUNT(*) FROM jobs") == 0 if project.store.query(
        "SELECT name FROM sqlite_master WHERE name='jobs'") else True
    assert counter["calls"] == 0 and project.store.scalar("SELECT COUNT(*) FROM runs") == 0


def test_a_plain_protocol_name_is_accepted_on_the_command_line_and_the_page(project, monkeypatch):
    # the refusals are pinned in test_ui_followup_r17; here "+" and "_" names run on both surfaces (21st round)
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from tests.unit.test_runs import _wait
    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    _fake(monkeypatch, {"calls": 0})
    project.save()
    res = CliRunner().invoke(cli_app, ["screen", "protocol", "corpus", "--name", "main+human", "--limit", "1",
                                       "--dir", str(project.root)])
    assert res.exit_code == 0 and "a protocol name is" not in res.output, res.output
    c = TestClient(create_app(project))
    r = c.post("/api/jobs/screen", json={"scope": "corpus", "mode": "protocol", "protocol": "B2_second", "limit": 1})
    assert r.status_code == 200, r.text
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    names = {row["protocol"] for row in project.store.query("SELECT DISTINCT protocol FROM screen_decisions")}
    assert names == {"main+human", "B2_second"}


def test_a_limit_on_a_finished_run_counts_only_its_own_records(project, monkeypatch):
    # the run of every record exists; a rerun with a limit of 2 says "2 of 2", never "all of 2"
    from wider.core.stage_result import last_result
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    full = screen(project, "corpus")
    calls = counter["calls"]
    out = screen(project, "corpus", limit=2)
    assert full["n_records"] > 2 and counter["calls"] == calls          # nothing new to screen
    assert (out["n_records"], out["n_ok"], out["resumed"]) == (2, 2, 2)
    res = last_result(project, "screen")
    assert res["headline"].startswith("Screened 2 of 2 records")
    assert "2 records were already done and skipped." in res["warnings"]   # the words, not only the count (14th round)
    # the runs table keeps the whole run ("done 62/62", never "62/2"), and a rerun without calls shows no tokens
    # (it once showed the whole run's total: seventh round)
    row = project.store.one("SELECT n_items, n_ok FROM runs WHERE run_id=?", (out["run_id"],))
    assert (row["n_items"], row["n_ok"]) == (full["n_records"], full["n_records"])
    assert full["usage"] and full["usage"]["calls"] == full["n_records"] and out["usage"] is None
    assert last_result(project, "screen").get("usage") is None
    more = screen(project, "corpus", limit=2, pass_name="B")
    assert more["usage"]["calls"] == 2 and counter["calls"] == calls + 2
    # the protocol twin: a one-pass protocol over the finished pass A makes no call and shows no tokens (21st round);
    # a protocol run has its own run id (result sets), so it gets there by reusing the identical evaluation
    from wider.screen.protocol import run_protocol
    project.config.protocol.screening.passes = 1
    prot = run_protocol(project, "corpus", "main", limit=1, reuse_evaluations=True)
    assert counter["calls"] == calls + 2 and prot["usage"] is None
    # the protocol's own run holds only the record its limit allowed
    assert prot["n"] == 1
    assert last_result(project, "screen").get("usage") is None


def test_a_pass_name_never_leads_the_run_folder_out_of_the_project(project, monkeypatch):
    """A pass name such as `x/../../escaped` once made the run folder land outside the project (seventh round)."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    c = TestClient(create_app(project))
    needle = "a pass name is 1-20 letters A-Z, digits"
    for bad in ["x/../../../../escaped-run", "..", "a b", ".hidden", "A" * 21, "ü"]:
        r = c.post("/api/jobs/screen", json={"scope": "corpus", "pass_name": bad})
        assert r.status_code == 400 and needle in r.json()["detail"], bad
        with pytest.raises(ValueError, match=needle):
            screen(project, "corpus", pass_name=bad)
    res = CliRunner().invoke(cli_app, ["screen", "run", "corpus", "--pass", "x/../y", "--dir", str(project.root)])
    assert res.exit_code == 1 and needle in res.output and "Traceback" not in res.output
    assert counter["calls"] == 0 and project.store.scalar("SELECT COUNT(*) FROM runs") == 0
    assert not (project.root.parent / "escaped-run").exists()
    # names a researcher would type still work
    for good in ["A", "B2", "pass_1", "run-b"]:
        check_run_args(good)
    # the run-folder helper keeps any folder name one level inside runs/<kind>/
    base = (project.root / "runs" / "screen").resolve()
    for stamp in ["x/../../../../escaped", "..", "../..", "a b/c"]:
        d = project.run_dir("screen", stamp).resolve()
        assert d.parent == base, (stamp, d)


def test_a_protocol_over_one_record_says_record(project, monkeypatch):
    # the headline of a real protocol run over a one-record scope (spec-checker, 25th round)
    from wider.core.stage_result import last_result
    from wider.screen.protocol import run_protocol
    _fake(monkeypatch, {"calls": 0, "flip": True})
    rid = project.store.scalar("SELECT MIN(record_id) FROM records")
    out = run_protocol(project, f"ids:{rid}", "one")
    assert out["n"] == 1
    assert last_result(project, "screen")["headline"].startswith("Protocol one: 1 record decided with 2 passes")


def _second_read_differs(counter):
    """``_responder``, except that the second read of a matching record grades it as unrelated (the rule decides from
    the grades, so the grades change, not only the decision)."""
    base, reads = _responder(counter), counter.setdefault("reads", {})   # one provider per pass: keep the count outside

    def respond(req):
        out = base(req)
        reads[req.user] = reads.get(req.user, 0) + 1
        if reads[req.user] == 2 and out["target_fit"] == "core":
            out.update(e1="absent", target_fit="unrelated", decision="exclude", exclusion_reason="a")
        return out
    return respond


def test_a_protocol_whose_passes_disagree_names_the_blind_third_read(project, monkeypatch):
    # a real run that sends records to the third read: "2 passes and a blind third read", never "3 passes"
    # (spec-checker, 28th round)
    from wider.core.stage_result import last_result
    from wider.screen.protocol import run_protocol
    _fake(monkeypatch, {"calls": 0}, _second_read_differs)
    out = run_protocol(project, "corpus", "split")
    assert 0 < out["disagreements"] < out["n"] and len(out["runs"]) == 3
    head = last_result(project, "screen")["headline"]
    assert head.startswith(f"Protocol split: {out['n']} records decided with 2 passes and a blind third read;")
    assert "3 passes" not in head


def _nothing_ran(project):
    st = project.store
    return (st.scalar("SELECT COUNT(*) FROM runs WHERE kind='screen'") == 0
            and st.scalar("SELECT COUNT(*) FROM screen_calls") == 0
            and st.scalar("SELECT COUNT(*) FROM jobs") == 0
            and not any((project.root / "runs").glob("*screen*")))


def test_max_tokens_ceiling(project, monkeypatch):
    """Decision 26: screening.max_tokens is at most 32000 in protocol set, the page's save and the run preflight;
    an older project.yaml with a larger value still loads but its run is refused before any call or run folder."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    counter = {"calls": 0}
    _fake(monkeypatch, counter)
    msg = "screening.max_tokens must be at most 32000"
    d = str(project.root)
    yaml_before = (project.root / "project.yaml").read_text()
    res = CliRunner().invoke(cli_app, ["protocol", "set", "screening.max_tokens", "50000", "--dir", d])
    assert res.exit_code == 1 and msg in res.output and "Traceback" not in res.output
    r = TestClient(create_app(project)).post("/api/protocol/set", json={"path": "screening.max_tokens", "value": "50000"})
    assert r.status_code == 400 and msg in r.json()["detail"]
    assert (project.root / "project.yaml").read_text() == yaml_before
    res = CliRunner().invoke(cli_app, ["protocol", "set", "screening.max_tokens", "32000", "--dir", d])
    assert res.exit_code == 0, res.output
    # a project.yaml saved earlier with a larger value still loads; its runs are refused
    project = Project.find(project.root)   # re-read: the CLI changed project.yaml since
    project.config.protocol.screening.max_tokens = 50000
    project.save()
    p2 = Project.find(project.root)
    assert p2.config.protocol.screening.max_tokens == 50000
    with pytest.raises(ValueError, match=msg):
        screen(p2, "corpus", limit=2)
    with pytest.raises(ValueError, match=msg):
        run_protocol(p2, "sample:development", "main")
    res = CliRunner().invoke(cli_app, ["screen", "run", "corpus", "--limit", "2", "--dir", d])
    assert res.exit_code == 1 and msg in res.output and "Traceback" not in res.output
    r = TestClient(create_app(p2)).post("/api/jobs/screen", json={"scope": "corpus", "limit": 2})
    assert r.status_code == 400 and msg in r.json()["detail"]
    assert counter["calls"] == 0 and _nothing_ran(p2)


def test_max_tokens_above_backend_limit(project, monkeypatch):
    """Decision 26: a backend's extra.max_output_tokens below screening.max_tokens refuses the run (CLI exit 1,
    HTTP 400) before any call, run folder or job; the cost range uses the smaller of the two."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.backends import update_provider
    from wider.core.estimate import estimate_screen
    from wider.ui.app import create_app
    counter = {"calls": 0}
    _fake(monkeypatch, counter)
    d = str(project.root)
    project.config.protocol.screening.max_tokens = 2000
    project.save()
    before = estimate_screen(project, "pass", scope="corpus", limit=4)
    edited = CliRunner().invoke(cli_app, ["providers", "edit", "fake", "--extra", "max_output_tokens=1000", "--dir", d])
    assert edited.exit_code == 0, edited.output
    p = Project.find(project.root)
    assert p.provider("fake").extra["max_output_tokens"] == 1000
    msg = ("screening.max_tokens 2000 is above the 1000 output tokens backend fake allows; "
           "lower it with wider protocol set screening.max_tokens 1000")
    with pytest.raises(ValueError, match="above the 1000 output tokens backend fake"):
        screen(p, "corpus", limit=2)
    res = CliRunner().invoke(cli_app, ["screen", "run", "corpus", "--limit", "2", "--dir", d])
    assert res.exit_code == 1 and msg in " ".join(res.output.split()) and "Traceback" not in res.output
    r = TestClient(create_app(p)).post("/api/jobs/screen", json={"scope": "corpus", "limit": 2})
    assert r.status_code == 400 and msg in r.json()["detail"]
    assert counter["calls"] == 0 and _nothing_ran(p)
    # the cost range assumes the smaller limit as the output per call
    capped = estimate_screen(p, "pass", scope="corpus", limit=4)
    assert capped["output_tokens"] == before["output_tokens"] // 2
    assert any("1,000 tokens" in a for a in capped["assumptions"])
    # lowering max_tokens to the limit lets the run start
    update_provider(p, "fake", extra={"max_output_tokens": "2000"})
    out = screen(Project.find(project.root), "corpus", limit=2)
    assert out and counter["calls"] > 0


def test_unknown_scope_fails(tmp_path):
    """Unknown scope: a run started with a scope of another form fails with "unknown scope"."""
    from wider.screen.engine import record_set

    p = Project.init(tmp_path / "r")
    try:
        with pytest.raises(ValueError, match="unknown scope 'everything'"):
            record_set(p, "everything")
    finally:
        p.close()


def test_run_keeps_a_review_decision_made_during_the_run(project, monkeypatch):
    """A record decided on the Screening review page while the run writes its decisions keeps that decision
    (manual-review D3): the run re-checks each record for a researcher vote just before writing it."""
    from wider.screen import protocol as protomod

    real, seen = protomod.set_decision, {}

    def write(p, key, rid, final, **kw):
        real(p, key, rid, final, **kw)
        if not seen:   # after the run's first write, a person decides another record on the review page
            other = p.store.scalar("SELECT MAX(record_id) FROM records WHERE record_id<>?", (rid,))
            seen["rid"] = other
            real(p, key, other, "maybe", votes=["exclude", "exclude", "researcher:maybe"], decided_by="researcher",
                 reason="researcher (ana) decided after screening")

    monkeypatch.setattr(protomod, "set_decision", write)
    _fake(monkeypatch, {"calls": 0})
    run_protocol(project, "corpus", "main")
    row = project.store.one("SELECT decision, decided_by, votes FROM screen_decisions WHERE protocol='main' AND "
                            "record_id=?", (seen["rid"],))
    assert row["decision"] == "maybe" and row["decided_by"] == "researcher" and "researcher:maybe" in row["votes"]
    assert project.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main'") == 40
