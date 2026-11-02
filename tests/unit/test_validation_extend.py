"""Adding records to bands of the probability sample (validation-sample-planner D6): weights, refusals, the
lines that say what the extension means, undo, CLI and route parity. Synthetic projects only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_validation_plan import _ranked
from tests.unit.test_validation_suggest import _drawn, _rel
from wider.cli import app as cli_app
from wider.core import journal
from wider.sample import design as ds
from wider.sample.design import SampleConflict, draw_probability, extend_probability
from wider.sample.labels import label

runner = CliRunner()


def _rows(p, name="validation"):
    return [dict(r) for r in p.store.query(
        "SELECT sr.record_id, sr.band, sr.weight, sr.queue_pos FROM sample_rows sr JOIN samples s "
        "ON s.sample_id=sr.sample_id WHERE s.name=? ORDER BY sr.queue_pos", (name,))]


def _dev(p, ids):
    """A purposive development sample holding ``ids``."""
    with p.store.tx() as c:
        cur = c.execute("INSERT INTO samples(name,kind,ranking_id,design,seed,n,created_at,locked) "
                        "VALUES('development','purposive',1,'{}',1,?,'2026-01-01',0)", (len(ids),))
        c.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) "
                      "VALUES(?,?,?,NULL,NULL,'likely_include',?)", [(cur.lastrowid, r, i, r) for i, r in enumerate(ids)])


def test_extend_band_weights_and_queue(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    before = _rows(p)
    first = [r["record_id"] for r in before[:5]]
    for rid in first:
        label(p, "validation", rid, "r1", "exclude")
    out = extend_probability(p, "validation", {"S2": 20}, author="ana")
    assert out["added"] == 20 and out["n"] == len(before) + 20
    after = _rows(p)
    assert [r["record_id"] for r in after[:5]] == first          # labelled records keep their positions
    s2 = [r for r in after if r["band"] == "S2"]
    assert len(s2) == 90 and all(r["weight"] == pytest.approx(400 / 90) for r in s2)
    assert out["weights"]["S2"] == {"N_h": 400, "n_h": 90, "weight": pytest.approx(400 / 90)}
    assert {r["record_id"] for r in before} <= {r["record_id"] for r in after}
    assert len({r["queue_pos"] for r in after}) == len(after)
    assert set(out) == {"name", "added", "bands", "n", "weights", "change_id", "messages", "lines"}
    ch = journal.changes(p, kind="sample_extend")
    assert ch and ch[0]["author"] == "ana"
    # seeded: the same extension on a copy gives the same records
    q = _ranked(tmp_path, name="q")
    draw_probability(q)
    for rid in first:
        label(q, "validation", rid, "r1", "exclude")
    extend_probability(q, "validation", {"S2": 20}, author="ana")
    assert sorted(r["record_id"] for r in _rows(q)) == sorted(r["record_id"] for r in after)


def test_extend_refusals(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    with pytest.raises(ValueError, match="no band S9"):
        extend_probability(p, "validation", {"S9": 5})
    with pytest.raises(ValueError, match="S1 has only 50 frame records left"):
        extend_probability(p, "validation", {"S1": 80})
    with pytest.raises(KeyError):
        extend_probability(p, "nope", {"S1": 1})
    _dev(p, [1999])
    with pytest.raises(ValueError, match="only a probability sample"):
        extend_probability(p, "development", {"S1": 1})
    p.store.execute("UPDATE samples SET locked=1 WHERE name='validation'")
    with pytest.raises(SampleConflict):
        extend_probability(p, "validation", {"S1": 1})
    p.store.execute("UPDATE samples SET locked=0 WHERE name='validation'")
    p.store.execute("UPDATE rankings SET md5='y'")
    with pytest.raises(SampleConflict, match="ranking"):
        extend_probability(p, "validation", {"S1": 1})
    p.store.execute("UPDATE rankings SET md5='x'")
    p.store.execute("UPDATE samples SET design=? WHERE name='development'", (json.dumps({"fresh_from": "validation"}),))
    with pytest.raises(SampleConflict):
        extend_probability(p, "validation", {"S1": 1})


def _label_new(p, name="validation"):
    """Label every sampled record still without a label (from ``_rel``, as ``_drawn`` does), so recall can be
    revealed after an extension (label-completion-and-sealing D1)."""
    for r in p.store.query("SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                           "WHERE s.name=? AND sr.record_id NOT IN (SELECT record_id FROM labels "
                           "WHERE sample_id=s.sample_id)", (name,)):
        label(p, name, r["record_id"], "r1", "include" if _rel(r["record_id"]) else "exclude")


def test_extend_after_look_is_said(tmp_path):
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path)
    out0 = extend_probability(p, "validation", {"S3": 10})
    assert not any("after a recall result was seen" in m for m in out0["messages"])   # no look yet
    _label_new(p)                                      # recall waits for every final label
    estimate(p, "validation", "main", B=20)
    out = extend_probability(p, "validation", {"S3": 10})
    assert any("after a recall result was seen" in m and "does not allow for that decision" in m
               for m in out["messages"])
    _label_new(p)
    e = estimate(p, "validation", "main", B=20)
    assert any("after a recall result was seen" in x for x in e["validation_lines"])


def test_extend_suggested(tmp_path):
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path, target=0.95)
    with pytest.raises(ValueError):
        extend_probability(p, "validation", "suggested")          # no look, no suggestion
    e = estimate(p, "validation", "main", B=20)
    sg = e.get("more_validation")
    if not sg or not sg.get("given") or not (sg.get("target") or sg.get("precision")) or not sg.get("settles", True):
        with pytest.raises(ValueError):
            extend_probability(p, "validation", "suggested")
        return
    part = sg.get("target") or sg.get("precision")
    out = extend_probability(p, "validation", "suggested")
    assert out["bands"] == {b: m for b, m in part["by_band"].items() if m > 0}


def test_extend_undo(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    before = _rows(p)
    out = extend_probability(p, "validation", {"S1": 10, "S3": 5})
    journal.undo(p, out["change_id"], author="ana", other=True)
    assert _rows(p) == before
    assert p.store.scalar("SELECT n FROM samples WHERE name='validation'") == len(before)
    # refused once a record added by the extension is labelled
    out = extend_probability(p, "validation", {"S1": 10})
    new = {r["record_id"] for r in _rows(p)} - {r["record_id"] for r in before}
    label(p, "validation", sorted(new)[0], "r1", "exclude")
    with pytest.raises(Exception, match="added by this extension is labelled"):
        journal.undo(p, out["change_id"], author="ana")


def test_extend_cli_matches_api(tmp_path):
    from wider.ui.app import create_app

    p = _ranked(tmp_path)
    draw_probability(p)
    q = _ranked(tmp_path, name="q")
    draw_probability(q)
    r = runner.invoke(cli_app, ["validation", "extend", "validation", "--band", "S2=15", "--author", "ana",
                                "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    cli = json.loads(r.output)
    api = TestClient(create_app(q)).post("/api/validation/extend",
                                         json={"sample": "validation", "bands": {"S2": 15}, "author": "ana"})
    assert api.status_code == 200, api.text
    a = api.json()
    for k in ("added", "bands", "n", "weights", "lines"):
        assert cli[k] == a[k]
    assert sorted(x["record_id"] for x in _rows(p)) == sorted(x["record_id"] for x in _rows(q))
    bad = runner.invoke(cli_app, ["validation", "extend", "validation", "--dir", str(p.root)])
    assert bad.exit_code != 0
    c = TestClient(create_app(q))
    assert c.post("/api/validation/extend", json={"sample": "validation"}).status_code == 400
    assert c.post("/api/validation/extend", json={"sample": "nope", "bands": {"S1": 1}}).status_code == 404
    q.store.execute("UPDATE samples SET locked=1 WHERE name='validation'")
    assert c.post("/api/validation/extend", json={"sample": "validation", "bands": {"S1": 1}}).status_code == 409


def test_extend_excludes_development(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    mine = {r["record_id"] for r in _rows(p)}
    dev = [r for r in range(1, 101) if r not in mine][:30]
    _dev(p, dev)
    extend_probability(p, "validation", {"S1": 20})
    got = {r["record_id"] for r in _rows(p)}
    assert len(got - mine) == 20 and not (got - mine) & set(dev)
    # S1 has 100 records, 30 in development: N'_h = 70 over n'_h = 70
    s1 = [r for r in _rows(p) if r["band"] == "S1"]
    assert all(r["weight"] == pytest.approx(1.0) for r in s1)
    assert any("not covered" in x for x in ds.scope_lines(p, "validation"))


def test_extend_with_development_records_in_sample(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    s1 = [r["record_id"] for r in _rows(p) if r["band"] == "S1"]
    _dev(p, s1[:5])
    extend_probability(p, "validation", {"S1": 5})
    rows = {r["record_id"]: r for r in _rows(p) if r["band"] == "S1"}
    assert all(rows[r]["weight"] is None for r in s1[:5])
    others = [r for r in rows.values() if r["record_id"] not in s1[:5]]
    assert len(others) == 50 and all(r["weight"] == pytest.approx(95 / 50) for r in others)
    assert any("left out of the estimate" in x for x in ds.scope_lines(p, "validation"))


def test_extend_recognisable_said(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    for r in _rows(p):
        if r["band"] == "S1":
            label(p, "validation", r["record_id"], "r1", "exclude")
    out = extend_probability(p, "validation", {"S1": 5, "S2": 5})
    assert any("S1" in m for m in out["messages"]) and not any(m.startswith("S2") for m in out["messages"])


def test_extend_weights_exact_or_approximate(tmp_path):
    from wider.recall.outputs import methods_draft

    p = _ranked(tmp_path)
    draw_probability(p)
    assert ds.weights_basis(p, "validation") is None                  # not extended
    extend_probability(p, "validation", {"S1": 5})
    assert ds.weights_basis(p, "validation") == ds.WEIGHTS_EXACT
    _dev(p, [1999])                                                    # drawn after, around validation
    assert ds.weights_basis(p, "validation") == ds.WEIGHTS_APPROX.format(name="validation")
    assert ds.WEIGHTS_APPROX.format(name="validation") in json.dumps(methods_draft(p))


def test_extend_suggested_stale(tmp_path):
    from tests.unit.test_validation_anytime import _flip
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path, target=0.95)
    estimate(p, "validation", "main", B=20)
    _flip(p)
    with pytest.raises(ValueError, match="labels changed since the last recall estimate"):
        extend_probability(p, "validation", "suggested")


def test_first_draw_leaves_out_development(tmp_path):
    from wider.sample.design import draw_development

    # development first: the probability draw leaves its records out
    p = _ranked(tmp_path)
    _dev(p, list(range(1, 31)))
    out = draw_probability(p)
    assert not {r["record_id"] for r in _rows(p)} & set(range(1, 31))
    assert out["development_excluded"] == 30
    s1 = [r for r in _rows(p) if r["band"] == "S1"]
    assert all(r["weight"] == pytest.approx(70 / 50) for r in s1)
    # probability first: the development draw leaves the probability sample out
    from tests.unit.test_sample_boundary import _build

    q, _ = _build(tmp_path / "q")
    draw_probability(q)
    d = draw_development(q, per_boundary=0, n_thin=2, n_tail=0)
    dev = {r["record_id"] for r in q.store.query("SELECT record_id FROM sample_rows WHERE sample_id=?",
                                                 (d["sample_id"],))}
    assert dev and not dev & {r["record_id"] for r in _rows(q)}


def test_extend_suggested_above_limit(tmp_path):
    """recall-certification-workload D5: a suggestion above recall.practical_labels still adds its records; the warning
    informs and never refuses."""
    from tests.unit.test_validation_suggest import _deep
    from wider.recall.estimate import estimate

    p = _deep(tmp_path)
    e = estimate(p, "validation", "main", B=20)
    sg = e["more_validation"]
    assert sg["impractical"] is not None and sg["settles"]
    out = extend_probability(p, "validation", "suggested")
    assert sum(out["bands"].values()) == sg["target"]["m"]


def test_extend_suggested_census(tmp_path):
    """When only labelling every record left settles it, --suggested adds every record left."""
    from tests.unit.test_validation_suggest import _drawn
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path, target=0.88)
    sg = estimate(p, "validation", "main", B=20)["more_validation"]
    assert sg["target"]["census"] and sg["settles"]
    out = extend_probability(p, "validation", "suggested")
    assert sum(out["bands"].values()) == sg["left"] == 480
