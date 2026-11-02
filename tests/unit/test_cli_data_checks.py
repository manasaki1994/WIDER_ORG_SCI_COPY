"""CLI and data checks from the whole-app inspection of 1 Nov 2026: a folder without a project, versions written v1,
reviewer names that need no login name, local times with the zone, plain lines instead of Python dicts, the
agreement after adjudication, reviewer-name hints and files that are not database exports."""
from __future__ import annotations

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_recall_sealed_disagreements import _disagreeing, _ids
from wider.cli import _w2
from wider.cli import app as cli_app
from wider.cli_refine import version_number
from wider.core.journal import NAME_NEEDED
from wider.core.project import Project
from wider.core.provenance import local_time
from wider.core.stage_summaries import summarize_samples
from wider.ingest.importer import add_file
from wider.recall.report import _ticks
from wider.ui.app import create_app

runner = CliRunner()


def _cli(p, *args, env=None):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env=env)


def test_a_folder_without_a_project_says_so_without_a_traceback(tmp_path):
    for args in (["status"], ["ui"], ["history", "list"]):
        r = runner.invoke(cli_app, [*args, "--dir", str(tmp_path)])
        assert r.exit_code == 1, r.output
        assert "no WIDER project here" in r.output and "wider init" in r.output
        assert not isinstance(r.exception, FileNotFoundError)


def test_a_version_may_be_written_as_the_pages_print_it(tmp_path):
    assert version_number("v1") == 1 and version_number("V12") == 12 and version_number("3") == 3
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    r = _cli(p, "construct", "approve", "anchors", "v1")
    assert "not a valid int" not in r.output
    bad = _cli(p, "construct", "approve", "anchors", "vx")
    assert bad.exit_code == 2 and "write 1 or v1" in bad.output


def test_declarations_need_a_name_never_the_login_name(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    r = _cli(p, "label", "provenance", "validation", "--reviewer", "ann", "--independent", "blind",
             env={"USER": "login-name", "WIDER_REVIEWER": ""})
    assert r.exit_code == 2 and " ".join(NAME_NEEDED.split()) in " ".join(r.output.split())


def test_times_are_local_with_the_zone_named():
    shown = local_time("2026-10-27T21:59:00")            # stored without a zone: UTC
    assert shown.startswith("2026-10-2") and len(shown.split()) == 3 and shown.split()[2]
    assert local_time("not a time") == "not a time" and local_time(None) == ""
    assert local_time("2026-10-27T21:59:30+00:00", seconds=True).count(":") == 2


def test_weights_round_as_the_pages_do():
    assert _w2(17 / 8) == "2.13" and _w2(1.0) == "1.00" and _w2(2.004) == "2.00"


def test_labels_csv_ticks_and_the_sample_headline_are_plain():
    assert _ticks("{}") == "" and _ticks(None) == ""
    assert _ticks('{"e1": "yes", "e2": "no"}') == "e1=yes; e2=no"
    head = summarize_samples([{"name": "validation", "kind": "probability", "n": 40, "locked": 1},
                              {"name": "development", "kind": "purposive", "n": 20, "locked": 0}]).headline
    assert head.startswith("1 sample ready for labelling: development") and "Locked: validation" in head


def test_agreement_says_which_disagreements_are_adjudicated(tmp_path):
    p, _ = _disagreeing(tmp_path)
    rid = _ids(p)[0]
    r = _cli(p, "label", "adjudicate", "validation", str(rid), "exclude", "--by", "dana",
             "--correction-reason", "second review")
    assert r.exit_code == 0, r.output
    out = _cli(p, "label", "agreement", "validation", "oracle", "rev2").output
    assert "{" not in out and "10 disagreements, 1 adjudicated, 9 still open" in " ".join(out.split())
    assert f"record {rid}: oracle include, rev2 exclude; adjudicated exclude by dana" in out
    assert f"record {_ids(p)[1]}: oracle include, rev2 exclude; not adjudicated" in out
    api = TestClient(create_app(Project.load(p.root))).get(
        "/api/labels/agreement", params={"sample": "validation", "a": "oracle", "b": "rev2"}).json()
    adj = {d["record_id"]: d["adjudicated"] for d in api["disagreements"]}
    assert adj[rid]["decision"] == "exclude" and adj[rid]["decided_by"] == "dana" and adj[_ids(p)[1]] is None


def test_a_mistyped_reviewer_name_gets_a_hint(tmp_path):
    p, _ = _disagreeing(tmp_path)
    out = " ".join(_cli(p, "label", "agreement", "validation", "Oracle", "rev 2").output.split())
    assert "'Oracle' has no labels in sample validation; did you mean 'oracle'?" in out
    assert "'rev 2' has no labels in sample validation; did you mean 'rev2'?" in out
    api = TestClient(create_app(Project.load(p.root))).get(
        "/api/labels/agreement", params={"sample": "validation", "a": "oracle", "b": "zed"}).json()
    assert api["hints"] == ["'zed' has no labels in sample validation; reviewers with labels: oracle, rev2"]


def test_a_file_that_is_not_an_export_is_refused(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    junk = tmp_path / "random.txt"
    junk.write_text("Some notes about the review\nnothing here is a record\nor a column\n", encoding="utf-8")
    try:
        add_file(p, "scopus", junk)
        raise AssertionError("not refused")
    except ValueError as e:
        assert "does not look like a database export" in str(e) and "nothing was saved" in str(e)
    assert not p.path("inputs", "scopus").exists() or not any(p.path("inputs", "scopus").iterdir())
    r = _cli(p, "import", "add", "scopus", str(junk))
    assert r.exit_code == 1 and "does not look like a database export" in " ".join(r.output.split())
    c = TestClient(create_app(Project.load(p.root)))
    with junk.open("rb") as fh:
        res = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("random.txt", fh)})
    assert res.status_code == 400 and "does not look like a database export" in res.json()["detail"]
