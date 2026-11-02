"""Stopping and continuing validation (validation-sample-planner D16). Synthetic projects only."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_validation_suggest import _drawn
from wider.cli import app as cli_app
from wider.core import journal
from wider.recall import validation as va
from wider.recall.estimate import estimate
from wider.ui.app import create_app


def _stops(p):
    return [dict(r) for r in p.store.query("SELECT * FROM validation_stops ORDER BY rowid")]


def test_stop_before_look_refused(tmp_path):
    p = _drawn(tmp_path)
    with pytest.raises(ValueError) as e:
        va.stop(p, "validation")
    assert str(e.value) == va.NOTHING_TO_STOP.format(sample="validation")
    r = CliRunner().invoke(cli_app, ["validation", "stop", "--dir", str(p.root)])
    assert r.exit_code == 1 and "nothing to stop" in r.output
    j = TestClient(create_app(p)).post("/api/validation/stop", json={})
    assert j.status_code == 400 and "nothing to stop" in j.json()["detail"]
    assert not _stops(p) and not journal.changes(p, kind="validation_stop")


def test_stop_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDER_REVIEWER", "R1")
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    r = va.stop(p, "validation", note="enough")
    assert r["look"] == 1 and r["author"] == "R1" and r["note"] == "enough"
    assert r["outcome"] == "cannot_tell" and r["lines"] == ["Validation of validation stopped at look 1, outcome "
                                                            "inconclusive, by R1."]
    assert len(_stops(p)) == 1
    h = journal.changes(p, kind="validation_stop")
    assert len(h) == 1 and not h[0]["undoable"]
    assert h[0]["summary"] == "validation on validation stopped at look 1: inconclusive (enough)"
    st = va.status(p, "validation")
    assert st["look"] == {"index": 1} and st["stops"] and any("stopped" in line for line in st["lines"])


def test_stop_cli_matches_api(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDER_REVIEWER", "R1")
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    r = CliRunner().invoke(cli_app, ["validation", "stop", "--note", "n", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    j = TestClient(create_app(p), headers={"X-Wider-Author": "R1"}).post("/api/validation/stop", json={"note": "n"})
    assert j.status_code == 200
    import json
    a, b = json.loads(r.output), j.json()
    assert {k: a[k] for k in ("sample", "look", "outcome", "note")} == {k: b[k] for k in ("sample", "look", "outcome", "note")}
    assert a["lines"] == b["lines"] == ["Validation of validation stopped at look 1, outcome inconclusive, by R1."]
    assert len(_stops(p)) == 2


def test_old_stop_entry_said_inconclusive(tmp_path, monkeypatch):
    """A stop recorded before this change keeps its History words "cannot tell"; the report says "inconclusive"."""
    from wider.rank import score
    from wider.recall.report import build_report, to_markdown

    def no_ranking_check(project):   # the synthetic ranking has no embedding set behind it
        raise ValueError("no ranking check")
    monkeypatch.setattr(score, "validate", no_ranking_check)
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    p.store.execute("INSERT INTO validation_stops(sample_id,at,author,look,outcome,note) VALUES(?,?,?,?,?,?)",
                    (sid, "2026-10-23T10:00:00+00:00", "R1", 1, "cannot_tell", "budget used"))
    p.store.execute("INSERT INTO history(at,author,kind,summary,items) VALUES(?,?,?,?,?)",
                    ("2026-10-23T10:00:00+00:00", "R1", "validation_stop",
                     "validation on validation stopped at look 1: cannot tell (budget used)", "[]"))
    assert journal.changes(p, kind="validation_stop")[0]["summary"].endswith("cannot tell (budget used)")
    md = to_markdown(build_report(p, "main", "validation", B=20))
    assert "Validation stopped on 2026-10-23 at look 1; outcome: inconclusive. Note: budget used." in md
    assert "outcome: cannot tell" not in md


def test_continue_after_stop_said(tmp_path):
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    va.stop(p, "validation")
    from wider.sample.labels import label
    rid = p.store.scalar("SELECT record_id FROM labels WHERE decision='include' ORDER BY record_id LIMIT 1")
    # the look sealed the record: the changed label is a correction with a reason (label-completion-and-sealing D2)
    label(p, "validation", rid, "r1", "exclude", correction_reason="misread on first pass")
    e = estimate(p, "validation", "main", B=50)
    assert e["look"]["index"] == 2
    assert any("was continued on" in line and "after it was stopped on" in line for line in e["lines"])


def test_continue_by_extension_said(tmp_path):
    # an extension after a stop is a continuation, said before any new look (spec-checker round 2 F4)
    import time

    from wider.sample.design import extend_probability as extend
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    va.stop(p, "validation", note="budget used")
    time.sleep(1.1)
    band = p.store.scalar("SELECT band FROM sample_rows WHERE sample_id=1 ORDER BY band DESC LIMIT 1")
    extend(p, "validation", {band: 2})
    assert any("was continued on" in x for x in va.continue_lines(p, "validation"))
    assert "outcome: " in va.stop_line(va._stops(p, "validation")[0]) and "_" not in \
        va.stop_line(va._stops(p, "validation")[0]).split("outcome: ")[1].split(".")[0]
