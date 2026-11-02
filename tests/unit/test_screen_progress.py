"""Where screening stands (screening-page-tasks): steps, next step, round names and kinds, unusual results, the stage's
needs-you, the headline, `GET /api/screen` and `wider screen status`. Synthetic projects only."""
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core.project import Project
from wider.core.stage_result import last_result
from wider.core.stages import stage_states, state_text
from wider.sample.labels import label
from wider.screen import progress
from wider.screen.engine import screen
from wider.screen.protocol import run_protocol
from wider.ui.app import create_app

from .test_screen import (
    _fake,
    _responder,
    project_open,  # noqa: F401 - pytest fixture
)
from .test_screen import project as screen_project  # noqa: F401 - pytest fixture


@pytest.fixture
def project(request):
    """The synthetic project of test_screen, its development sample fully labelled (ui-redesign D4: runs that include
    development records wait for it)."""
    return request.getfixturevalue("screen_project")   # its development sample is fully labelled


@pytest.fixture
def unlabelled(request):
    return request.getfixturevalue("project_open")   # its development sample has no label yet


def _excluder(counter):
    base = _responder(counter)

    def respond(req):
        out = base(req)
        out.update(decision="exclude", exclusion_reason="a", e1="absent", e1_quote="extra words extra words")
        return out
    return respond


def _label_all(p, sample, upto=None):
    ids = [r["record_id"] for r in p.store.query(
        "SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name=? "
        "ORDER BY sr.queue_pos", (sample,))]
    for rid in ids[:upto]:
        label(p, sample, rid, "alice", "exclude", reason="a no voice measured")
    return len(ids)


def _step(prog, key):
    return next(s for s in prog["steps"] if s["key"] == key)


def _real(monkeypatch):
    """Pretend every round came from a real model (the test backend is the only one a unit test can run)."""
    orig = progress._test_runs
    monkeypatch.setattr(progress, "_test_runs", lambda p: {k: False for k in orig(p)})


def test_steps_before_any_round(project):
    prog = progress.screen_progress(project)
    assert [s["key"] for s in prog["steps"]] == ["prompt", "dev_labels", "development", "labels", "corpus", "maybes"]
    assert _step(prog, "prompt")["state"] == "completed" and _step(prog, "dev_labels")["state"] == "completed"
    assert _step(prog, "labels")["state"] == "not_started" and _step(prog, "labels")["page"] == "sample"
    assert _step(prog, "corpus")["state"] == "not_started"
    # the first open required step is the labels: the next step goes to the Samples and labelling page
    assert prog["next"]["page"] == "sample" and prog["next"]["label"] == "Label the validation sample"
    assert not prog["required_done"] and prog["rounds"] == []


def test_labels_step_counts_only(project):
    n = _label_all(project, "validation", upto=3)
    s = _step(progress.screen_progress(project), "labels")
    assert s["state"] == "in_progress" and s["short"] == f"validation labels: 3 of {n}"
    assert "exclude" not in s["text"] and "include" not in s["text"]   # counts only, never a label (blinding)
    _label_all(project, "validation")
    assert _step(progress.screen_progress(project), "labels")["state"] == "completed"


def test_test_model_corpus_needs_attention(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    _label_all(project, "validation")
    run_protocol(project, "corpus", "main")
    prog = progress.screen_progress(project)
    corpus = _step(prog, "corpus")
    assert corpus["state"] == "needs_attention" and corpus["short"] == "whole corpus screened by the test model only"
    assert prog["next"]["page"] == "screen" and prog["next"]["label"] == "Screen the whole corpus with a real model"
    assert not prog["required_done"]                     # never Recall while a required step is open
    assert all(r["test"] for r in prog["rounds"]) and all(progress.TEST_NOTE in r["unusual"] for r in prog["rounds"])
    st = next(s for s in stage_states(project) if s["key"] == "screen")
    assert state_text(st) == "done, test model, needs you: whole corpus screened by the test model only"


def test_recall_next_when_required_steps_done(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    _real(monkeypatch)
    _label_all(project, "validation")
    run_protocol(project, "corpus", "main")
    prog = progress.screen_progress(project)
    assert _step(prog, "corpus")["state"] == "completed" and prog["protocol"] == "main"
    assert prog["required_done"] and prog["next"]["page"] == "recall" and prog["next"]["label"] == "Estimate recall"
    assert progress.needs_you(project) is None
    st = next(s for s in stage_states(project) if s["key"] == "screen")
    assert state_text(st) == "done"


def test_screen_needs_you_in_stage_states(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    run_protocol(project, "sample:development", "dev")   # the Screening stage is done after one decision
    st = next(s for s in stage_states(project) if s["key"] == "screen")
    assert st["state"] == "done" and st["needs_you"].startswith("validation labels: 0 of ")


def test_run_names_and_kinds(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    screen(project, "sample:development", pass_name="A")
    screen(project, "sample:validation", pass_name="A")
    run_protocol(project, "corpus", "main")
    ids = [r["record_id"] for r in project.store.query("SELECT record_id FROM records LIMIT 2")]
    screen(project, "ids:" + ",".join(map(str, ids)), pass_name="A")
    rs = progress.rounds(project)
    names = [r["name"] for r in rs]
    assert names[0].startswith("Development sample · pass A") and rs[0]["kind"] == "development"
    assert names[1].startswith("Validation sample · pass A") and rs[1]["kind"] == "validation"
    corpus = [r for r in rs if r["kind"] == "corpus"]
    assert corpus and corpus[0]["name"].startswith("Whole corpus · protocol main · pass A")
    assert rs[-1]["name"].startswith("Chosen records") and rs[-1]["kind"] == "records"
    assert all(r["name"].endswith("prompt v1") for r in rs)
    assert progress.scope_kind(project, "maybe:main") == "maybes"
    assert progress._records_words("maybes", "maybe:main") == ("Maybes of main", "the maybes of main")
    assert progress.scope_kind(project, "sample:other") == "sample" and progress.scope_kind(project, None) == "other"


def test_screen_headline_names_the_round(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    out = screen(project, "sample:validation", pass_name="A")
    res = last_result(project, "screen")
    n = out["n_records"]
    assert res["headline"] == f"Screened {n} of {n} records of the validation sample, pass A (test model)."
    assert res["rows"] == [{"run": out["run_id"]}]     # the run id stays in the result's rows


def test_unusual_notes():
    note = progress.unusual(21, 0, 0, 21)
    assert note.startswith("Every one of the 21 records was excluded")
    assert "prompt" in note and "model's answers" in note and "backend" in note
    assert progress.unusual(21, 21, 0, 0).startswith("Every one of the 21 records was included")
    assert progress.unusual(10, 2, 6, 2).startswith("More than half of the 10 records are maybe")
    assert progress.unusual(4, 0, 0, 4) is None                     # too few to be unusual
    assert progress.unusual(10, 3, 3, 4) is None
    assert progress.unusual(4, 0, 0, 4, test=True) == progress.TEST_NOTE
    assert "not a screening result" in progress.TEST_NOTE


def test_all_excluded_corpus_needs_attention(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0}, responder=_excluder)
    _real(monkeypatch)
    _label_all(project, "validation")
    run_protocol(project, "corpus", "main")
    prog = progress.screen_progress(project)
    corpus = _step(prog, "corpus")
    assert corpus["state"] == "needs_attention" and "excluded every record" in corpus["text"]
    assert prog["next"]["page"] == "screen" and not prog["required_done"]


def test_api_screen_carries_progress(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    out = screen(project, "sample:validation", pass_name="A")
    body = TestClient(create_app(Project.find(project.root))).get("/api/screen").json()
    assert [s["key"] for s in body["progress"]["steps"]][:4] == ["prompt", "dev_labels", "development", "labels"]
    assert body["progress"]["next"]["page"] == "sample" and "rounds" not in body["progress"]
    run = next(r for r in body["runs"] if r["run_id"] == out["run_id"])
    assert run["name"].startswith("Validation sample · pass A") and run["kind"] == "validation"
    assert run["test"] is True and progress.TEST_NOTE in run["unusual"]


def test_cli_screen_status(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0})
    screen(project, "sample:validation", pass_name="A")
    run = lambda *a: CliRunner().invoke(cli_app, ["screen", "status", *a, "--dir", str(project.root)],  # noqa: E731
                                        env={"COLUMNS": "300"})
    res = run()
    assert res.exit_code == 0, res.output
    assert "Where screening stands:" in res.output and "[not started] Your labels on the validation sample" in res.output
    assert "Latest round: Validation sample · pass A" in res.output and "(test model)" in res.output
    assert "next: Label the validation sample" in res.output
    data = json.loads(run("--json").output)
    assert data["next"]["page"] == "sample" and data["rounds"][0]["kind"] == "validation"
    # the same steps and next step as the page gets from GET /api/screen (the page has the rounds in its runs)
    api = TestClient(create_app(Project.find(project.root))).get("/api/screen").json()["progress"]
    assert {k: v for k, v in data.items() if k != "rounds"} == api
    for s in data["steps"]:
        assert f"[{s['word']}] {s['title']}" in res.output


def test_api_protocol_set_carries_its_unusual_note(project, monkeypatch):
    _fake(monkeypatch, {"calls": 0}, responder=_excluder)
    run_protocol(project, "corpus", "main")
    body = TestClient(create_app(Project.find(project.root))).get("/api/screen").json()
    main = next(d for d in body["protocols"] if d["protocol"] == "main")
    assert main["unusual"].startswith(f"Every one of the {main['n']:,} records was excluded")


def test_dev_labels_open_step(unlabelled, monkeypatch):
    """Development labels open: the step is in progress with the labels left (its short text has no number), the next
    step is "Continue labelling" on Samples and labelling, and no step offers a run the gate would refuse; the
    "tried" step says earlier results are hidden once a development round exists."""
    p = unlabelled
    _fake(monkeypatch, {"calls": 0})
    n = _label_all(p, "development", upto=0)
    prog = progress.screen_progress(p)
    s = _step(prog, "dev_labels")
    assert s["state"] == "not_started" and s["required"] and s["page"] == "sample" and s["text"].startswith(f"{n} left")
    _label_all(p, "development", upto=3)
    prog = progress.screen_progress(p)
    s = _step(prog, "dev_labels")
    assert s["state"] == "in_progress" and s["text"].startswith(f"{n - 3} left")
    assert s["short"] == "development labels open" and not any(ch.isdigit() for ch in s["short"])
    assert "exclude" not in s["text"] and "include" not in s["text"]     # counts only (blinding)
    assert prog["next"] == {**prog["next"], "page": "sample", "label": "Continue labelling", "step": "dev_labels"}
    assert _step(prog, "corpus")["text"].startswith("After the development sample is fully labelled")
    assert "hidden" not in _step(prog, "development")["text"]
    assert progress.needs_you(p) == "development labels open"
    # a round on the development sample, then a label withdrawn: its results are hidden until labelling is complete
    _label_all(p, "development")
    screen(p, "sample:development", pass_name="A")
    rid = p.store.scalar("SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                         "WHERE s.name='development' ORDER BY sr.queue_pos LIMIT 1")
    p.store.execute("DELETE FROM labels WHERE record_id=?", (rid,))
    prog = progress.screen_progress(p)
    assert _step(prog, "dev_labels")["text"].startswith("1 left")
    dev = _step(prog, "development")
    assert dev["text"].startswith("Earlier results on the development sample are hidden until labelling is complete")
    assert "Check quality" not in dev["text"]
    res = CliRunner().invoke(cli_app, ["screen", "status", "--dir", str(p.root)], env={"COLUMNS": "300"})
    assert "[in progress] Development sample fully labelled: 1 left" in res.output
    assert "next: Continue labelling (sample)" in res.output
