"""A fresh probability sample instead of the used one (validation-sample-planner D9): what it leaves out, the chain,
the sample recall reads, undo, refusals, CLI and route parity. Synthetic projects only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_validation_extend import _dev, _rows
from tests.unit.test_validation_plan import _ranked
from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.sample import design as ds
from wider.sample.design import draw_probability
from wider.sample.labels import label

runner = CliRunner()


def _ids(p, name):
    return {r["record_id"] for r in _rows(p, name)}


def _design(p, name):
    return json.loads(p.store.scalar("SELECT design FROM samples WHERE name=?", (name,)))


def test_fresh_sample_excludes_used(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    out = draw_probability(p, name="validation2", fresh_from="validation", author="ana")
    assert not _ids(p, "validation") & _ids(p, "validation2")
    d = _design(p, "validation2")
    assert d["fresh_from"] == "validation" and d["chain"] == ["validation"]
    assert d["excluded_used"] == len(_ids(p, "validation"))
    # S1 has 100 records, 50 used: the fresh sample takes the other 50, each standing for itself
    s1 = [r for r in _rows(p, "validation2") if r["band"] == "S1"]
    assert len(s1) == 50 and all(r["weight"] == pytest.approx(1.0) for r in s1)
    assert ds.IN_USE.format(name="validation2") in out["messages"]
    ch = journal.changes(p, kind="sample_draw")
    assert ch[0]["author"] == "ana" and "fresh from validation" in ch[0]["summary"]


def test_fresh_from_refusals(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    with pytest.raises(KeyError):
        draw_probability(p, name="v2", fresh_from="nope")
    with pytest.raises(ValueError, match="needs a new name"):
        draw_probability(p, name="validation", fresh_from="validation")
    _dev(p, [1999])
    with pytest.raises(ValueError, match="not a probability sample"):
        draw_probability(p, name="v2", fresh_from="development")
    draw_probability(p, name="v2", fresh_from="validation")
    with pytest.raises(ValueError, match="in the chain"):
        draw_probability(p, name="validation", fresh_from="v2")
    assert p.store.scalar("SELECT COUNT(*) FROM samples WHERE name='v2'") == 1


def test_fresh_cli_matches_api(tmp_path):
    from wider.ui.app import create_app

    p, q = _ranked(tmp_path), _ranked(tmp_path, name="q")
    draw_probability(p)
    draw_probability(q)
    r = runner.invoke(cli_app, ["sample", "draw", "probability", "--name", "v2", "--fresh-from", "validation",
                                "--author", "ana", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "v2 is now the probability sample used for recall" in " ".join(r.output.split())
    c = TestClient(create_app(q))
    a = c.post("/api/samples/draw?which=probability&name=v2&fresh_from=validation&author=ana")
    assert a.status_code == 200, a.text
    assert ds.IN_USE.format(name="v2") in a.json()["samples"]["v2"]["messages"]
    assert _ids(p, "v2") == _ids(q, "v2")
    for proj in (p, q):
        assert Project.find(proj.root).config.protocol.sampling.probability_sample == "v2"
    bad = runner.invoke(cli_app, ["sample", "draw", "both", "--fresh-from", "validation", "--dir", str(p.root)])
    assert bad.exit_code == 1 and "probability sample only" in bad.output
    assert c.post("/api/samples/draw?which=both&fresh_from=validation").status_code == 400
    assert c.post("/api/samples/draw?which=probability&name=v3&fresh_from=nope").status_code == 404
    assert c.post("/api/samples/draw?which=probability&name=v2&fresh_from=v2").status_code == 400


def test_fresh_excludes_development(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    dev = sorted(set(range(1, 101)) - _ids(p, "validation"))[:20]
    _dev(p, dev)
    draw_probability(p, name="v2", fresh_from="validation")
    got = _ids(p, "v2")
    assert not got & set(dev) and not got & _ids(p, "validation")
    s1 = [r for r in _rows(p, "v2") if r["band"] == "S1"]
    assert len(s1) == 30     # 100 in S1, 50 used, 20 development
    assert _design(p, "v2")["development_excluded"] == 20


def test_fresh_sets_probability_sample(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    draw_probability(p, name="v2", fresh_from="validation")
    assert Project.find(p.root).config.protocol.sampling.probability_sample == "v2"
    assert ds.in_use(p) == "v2"
    assert ds.sampling_log(p)["v2"].get("in_use") is True
    assert not ds.sampling_log(p)["validation"].get("in_use")


def test_fresh_from_fresh_excludes_chain(tmp_path):
    p = _ranked(tmp_path, n=3000)
    draw_probability(p)
    draw_probability(p, name="v2", fresh_from="validation")
    draw_probability(p, name="v3", fresh_from="v2")
    assert _design(p, "v3")["chain"] == ["v2", "validation"]
    assert not _ids(p, "v3") & (_ids(p, "validation") | _ids(p, "v2"))
    assert ds.in_use(p) == "v3"


def test_fresh_sample_is_default_for_recall(tmp_path):
    from wider.recall import validation as va
    from wider.ui.app import create_app

    p = _ranked(tmp_path)
    draw_probability(p)
    draw_probability(p, name="v2", fresh_from="validation")
    p = Project.find(p.root)
    p.store.set_meta("last_validation_lines:v2", json.dumps({"lines": ["v2 look"]}))
    assert "v2 look" in json.dumps(va.status(p))
    assert "v2 look" in TestClient(create_app(p)).get("/api/validation/status").text
    r = runner.invoke(cli_app, ["validation", "status", "--dir", str(p.root)])
    assert r.exit_code == 0 and "v2 look" in r.output


def test_fresh_undo(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    yaml_before = (p.root / "project.yaml").read_text()
    out = draw_probability(p, name="v2", fresh_from="validation")
    journal.undo(p, out["change_id"], author="ana", other=True)
    assert p.store.scalar("SELECT COUNT(*) FROM samples WHERE name='v2'") == 0
    assert not _rows(p, "v2")
    assert (p.root / "project.yaml").read_text() == yaml_before
    # refused once the fresh sample has a label
    out = draw_probability(p, name="v2", fresh_from="validation")
    label(p, "v2", sorted(_ids(p, "v2"))[0], "r1", "exclude")
    with pytest.raises(Exception, match="already has labels"):
        journal.undo(p, out["change_id"], author="ana")


def test_fresh_redraw_keeps_chain(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    draw_probability(p, name="v2", fresh_from="validation")
    out = draw_probability(p, name="v2", seed=99)
    assert ds.REDRAW_KEEPS.format(name="v2", used="validation") in out["messages"]
    assert _design(p, "v2")["chain"] == ["validation"]
    assert not _ids(p, "v2") & _ids(p, "validation")
    # --name alone draws another sample and leaves the one recall reads unchanged
    out = draw_probability(p, name="spare")
    assert ds.in_use(p) == "v2" and ds.READS.format(name="v2") in out["messages"]
    assert "fresh_from" not in _design(p, "spare")
