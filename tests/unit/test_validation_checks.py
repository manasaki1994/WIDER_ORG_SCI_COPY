"""The criteria state on labels (validation-sample-planner D11): the codebook version and frozen flag a label on a
probability sample records, labels saved after a look, the counts and their lines. Synthetic projects only."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tests.unit.test_screen import CODEBOOK
from tests.unit.test_validation_plan import _ranked
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.sample.design import draw_probability
from wider.sample.labels import criteria_counts, label


def _project(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    return p


def _ids(p, n):
    return [r["record_id"] for r in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos LIMIT ?", (n,))]


def _codebook(p, status="frozen", note=""):
    v = artifacts.save_new_version(p, "codebook", {**CODEBOOK, "question": CODEBOOK["question"] + note}, origin="manual")
    if status != "draft":
        artifacts.set_status(p, "codebook", v["version"], status)
    return v["version"]


def _state(p, rid):
    return dict(p.store.one("SELECT codebook_version, criteria_frozen, after_look FROM labels WHERE record_id=?", (rid,)))


def test_criteria_state_on_labels(tmp_path):
    p = _project(tmp_path)
    a, b, c = _ids(p, 3)
    label(p, "validation", a, "r1", "exclude")                      # no codebook at all
    assert _state(p, a) == {"codebook_version": None, "criteria_frozen": 0, "after_look": 0}
    v1 = _codebook(p, "draft")
    label(p, "validation", b, "r1", "exclude")                      # a draft is never recorded
    assert _state(p, b)["codebook_version"] is None
    artifacts.set_status(p, "codebook", v1, "approved")
    v2 = _codebook(p, "frozen", " (v2)")
    label(p, "validation", c, "r1", "include")
    assert _state(p, c) == {"codebook_version": v2, "criteria_frozen": 1, "after_look": 0}
    c_ = criteria_counts(p, "validation")
    assert (c_["before"], c_["after"]) == (2, 1)
    assert c_["lines"][0] == "3 labels: 2 before the criteria were frozen, 1 after"


def test_labels_after_look_counted(tmp_path):
    p = _project(tmp_path)
    a, b = _ids(p, 2)
    _codebook(p)
    label(p, "validation", a, "r1", "exclude")
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    p.store.execute("INSERT INTO validation_looks(sample_id,result_set_key,first_at,last_at) VALUES(?,?,?,?)",
                    (sid, "main", "t", "t"))
    label(p, "validation", b, "r1", "exclude")
    assert _state(p, b)["after_look"] == 1 and _state(p, a)["after_look"] == 0
    c = criteria_counts(p, "validation")
    assert c["after_look"] == 1
    assert "1 labels were saved after a recall result on this sample was seen" in c["lines"]


def test_refreeze_counts_as_before(tmp_path):
    p = _project(tmp_path)
    ids = _ids(p, 5)
    v1 = _codebook(p)
    for r in ids[:3]:
        label(p, "validation", r, "r1", "exclude")
    v2 = _codebook(p, "frozen", " (changed)")
    for r in ids[3:]:
        label(p, "validation", r, "r1", "exclude")
    c = criteria_counts(p, "validation")
    assert (c["before"], c["after"]) == (3, 2)
    assert c["changed"] == {"n": 3, "from": v1, "to": v2}
    assert c["lines"][0] == f"5 labels: 3 before the criteria were frozen, 2 after; the criteria changed after 3 labels (v{v1} to v{v2})"


def test_relabel_keeps_before_state(tmp_path):
    p = _project(tmp_path)
    (a,) = _ids(p, 1)
    label(p, "validation", a, "r1", "exclude")
    v = _codebook(p)
    label(p, "validation", a, "r1", "include")                     # an ordinary save keeps the first state
    assert _state(p, a)["codebook_version"] is None and _state(p, a)["criteria_frozen"] == 0
    assert v and criteria_counts(p, "validation")["before"] == 1


def test_null_state_labels_counted(tmp_path):
    p = _project(tmp_path)
    a, b = _ids(p, 2)
    _codebook(p)
    label(p, "validation", a, "r1", "exclude")
    label(p, "validation", b, "r1", "exclude")
    p.store.execute("UPDATE labels SET codebook_version=NULL, criteria_frozen=NULL, after_look=NULL WHERE record_id=?", (b,))
    c = criteria_counts(p, "validation")
    assert (c["before"], c["after"], c["not_recorded"]) == (0, 1, 1)
    assert "1 labels: criteria state not recorded (saved before this version)" in c["lines"]
    # the CLI twin shows the same counts
    p.close()
    r = CliRunner().invoke(cli_app, ["sample", "status", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["samples"]["validation"]["criteria"]["not_recorded"] == 1
    r = CliRunner().invoke(cli_app, ["sample", "status", "--dir", str(p.root)])
    assert "criteria state not recorded" in r.output


# ----------------------------------------------------------------- the re-check and second-reviewer queues (D12)
from fastapi.testclient import TestClient  # noqa: E402

from tests.unit.test_validation_criteria import AFFECTED, _population_and_e  # noqa: E402
from tests.unit.test_validation_criteria import _project as _changed  # noqa: E402
from wider.core import journal  # noqa: E402
from wider.sample import labels as lb  # noqa: E402

FORBIDDEN = ("decision", "band", "rank", "set", "kind", "sample", "sample_id", "record_id", "queue_pos", "updated_at",
             "locked", "reason", "labels", "note", "pool")


def _assessed(tmp_path, scope="all"):
    p, va, vb, ids = _changed(tmp_path, AFFECTED, _population_and_e)
    lb.assess_criteria(p, "validation", "yes", scope, author="R1")
    return p, ids


def _client(p):
    from wider.ui.app import create_app
    return TestClient(create_app(p))


def _blind(items):
    for it in items:
        assert set(it) == {"item", "title", "abstract"}
        assert not any(k in it for k in FORBIDDEN)


def test_recheck_queue_blind(tmp_path):
    p, ids = _assessed(tmp_path)
    q = lb.recheck_queue(p, "R1")
    assert q["n"] == 20
    _blind(q["items"])
    assert lb.VALIDATION_ONLY in q["lines"] and lb.BLIND_QUEUE_ONLY in q["lines"]
    titles = [p.store.scalar("SELECT title FROM records WHERE record_id=?", (r,)) for r in ids[:20]]
    assert [it["title"] for it in q["items"]] != titles          # shuffled
    assert lb.recheck_queue(p, "R1")["items"] == q["items"]      # seeded: the same order and tokens again
    # the CSV export is blind too
    out = tmp_path / "q.csv"
    lb.export_queue_csv(q, out)
    import csv
    with open(out) as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == ["item", "title", "abstract", "decision", "reason", "labels", "note"]
    assert all(not r["decision"] and not r["reason"] for r in rows)


def test_recheck_counts_and_undo(tmp_path):
    p, ids = _assessed(tmp_path, "affected")
    q = lb.recheck_queue(p, "R1")
    assert q["n"] == 11
    lb.label_item(p, q["items"][0]["item"], "R1", "exclude", "a")
    assert lb.recheck_queue(p, "R1")["n"] == 10 and len(lb.revisions(p, "validation")) == 1
    journal.undo(p, journal.changes(p, kind="label")[0]["change_id"], author="R1")
    assert lb.recheck_queue(p, "R1")["n"] == 11 and lb.revisions(p, "validation") == []


def test_recheck_nothing_to_do(tmp_path):
    p, *_ = _changed(tmp_path, AFFECTED, _population_and_e)
    q = lb.recheck_queue(p, "R1")                                  # no assessment: nothing is due
    assert q["items"] == [] and q["lines"][0] == "nothing to re-check: no label of R1 is due after the assessment"
    p2, _ = _assessed(tmp_path / "b")
    assert lb.recheck_queue(p2, "R2")["n"] == 0                    # another reviewer has no labels to re-check


def test_rechecked_leave_queue(tmp_path):
    p, ids = _assessed(tmp_path)
    for it in lb.recheck_queue(p, "R1")["items"]:
        lb.label_item(p, it["item"], "R1", "exclude", "b")
    assert lb.recheck_queue(p, "R1")["n"] == 0


def test_second_reviewer_queue_enriched(tmp_path):
    p, ids = _assessed(tmp_path)
    q = lb.second_reviewer_queue(p, "R2")
    _blind(q["items"])
    assert q["n"] == 16                                            # 8 includes and maybes plus 8 of the 12 excludes
    assert "this queue has fewer excludes than includes and maybes, so it is not evenly mixed" not in q["lines"]
    assert lb.VALIDATION_ONLY in q["lines"]
    rq = lb.second_reviewer_queue(p, "R2", recheck=True)
    assert rq["n"] == 20


def test_second_reviewer_not_self(tmp_path):
    p, ids = _assessed(tmp_path)
    try:
        lb.second_reviewer_queue(p, "R1")
        raise AssertionError("expected a refusal")
    except ValueError as e:
        assert str(e) == "R1 made the first labels; choose another reviewer"
    # a record R2 already labelled is left out
    lb.label(p, "validation", ids[0], "R2", "include")
    q = lb.second_reviewer_queue(p, "R2")
    assert q["n"] == 14


def test_second_reviewer_recommendation_line(tmp_path):
    p, ids = _assessed(tmp_path)
    q = lb.recheck_queue(p, "R1")
    assert ("An independent second reviewer can re-check the same 20 records: wider label second-reviewer validation "
            "--reviewer R2 --recheck") in q["lines"]


def test_second_reviewer_recheck(tmp_path):
    p, ids = _assessed(tmp_path, "affected")
    q = lb.second_reviewer_queue(p, "R2", recheck=True)
    assert q["n"] == 11
    lb.label_item(p, q["items"][0]["item"], "R2", "include")
    assert p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='R2' AND recheck_from IS NULL") == 1
    assert lb.second_reviewer_queue(p, "R2", recheck=True)["n"] == 10
    assert lb.revisions(p, "validation") == []                     # an ordinary second label, not a revision


def test_label_by_token(tmp_path):
    p, ids = _assessed(tmp_path)
    c = _client(p)
    it = c.get("/api/labels/queue", params={"mode": "recheck", "reviewer": "R1"}, headers={"X-Wider-Author": "R1"}).json()["items"][0]["item"]
    post = lambda **b: c.post("/api/labels", json={"reviewer": "R1", "decision": "exclude", **b})  # noqa: E731
    assert post(item="nope").status_code == 400
    r = post(item=it, reviewer="R2")
    assert r.status_code == 400 and r.json()["detail"] == "this queue item belongs to another reviewer"
    r = post(item=it, sample_id=1, record_id=ids[0])
    assert r.status_code == 400 and r.json()["detail"] == "give either a queue item or a sample and record"
    assert post().status_code == 400
    assert post(sample_id=1, record_id=ids[0], recheck=True).status_code == 400
    sec = c.get("/api/labels/queue", params={"mode": "second", "reviewer": "R2"}, headers={"X-Wider-Author": "R2"}).json()["items"][0]["item"]
    assert c.post("/api/labels", json={"reviewer": "R2", "decision": "include", "item": sec, "recheck": True}).status_code == 400
    assert post(item=it, reason="a").status_code == 200
    assert len(lb.revisions(p, "validation")) == 1


def test_label_import_forms(tmp_path):
    p, ids = _assessed(tmp_path)
    root = str(p.root)
    run = lambda *a: CliRunner().invoke(cli_app, ["label", "import", *a, "--dir", root])  # noqa: E731
    for args in ((), ("validation",), ("validation", "x.csv", "--queue", "y.csv")):
        r = run(*args)
        assert r.exit_code == 1 and "give either SAMPLE FILE or --queue FILE" in r.output


def _fill(path, decision="exclude", reason="b"):
    import csv
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["decision"], r["reason"] = decision, reason
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return rows


def test_recheck_import_matches_drawer(tmp_path):
    p, ids = _assessed(tmp_path, "affected")
    root = str(p.root)
    out = tmp_path / "re.csv"
    r = CliRunner().invoke(cli_app, ["label", "recheck", "--reviewer", "R1", "--export", str(out), "--dir", root])
    assert r.exit_code == 0, r.output
    rows = _fill(out)
    r = CliRunner().invoke(cli_app, ["label", "import", "--queue", str(out), "--reviewer", "R2", "--dir", root])
    assert r.exit_code == 1 and "this queue item belongs to another reviewer" in r.output
    r = CliRunner().invoke(cli_app, ["label", "import", "--queue", str(out), "--dir", root])
    assert r.exit_code == 0 and "'imported': 11" in r.output
    revs = lb.revisions(p, "validation")
    assert len(revs) == len(rows) == 11 and all(v["new_decision"] == "exclude" for v in revs)
    assert lb.recheck_queue(p, "R1")["n"] == 0


def test_recheck_cli_matches_api(tmp_path):
    p, ids = _assessed(tmp_path)
    r = CliRunner().invoke(cli_app, ["label", "recheck", "validation", "--reviewer", "R1", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    api = _client(p).get("/api/labels/queue", params={"mode": "recheck", "reviewer": "R1", "samples": "validation"}, headers={"X-Wider-Author": "R1"}).json()
    assert json.loads(r.output) == api


def test_second_reviewer_cli_matches_api(tmp_path):
    p, ids = _assessed(tmp_path)
    r = CliRunner().invoke(cli_app, ["label", "second-reviewer", "--reviewer", "R2", "--recheck", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    api = _client(p).get("/api/labels/queue", params={"mode": "second", "reviewer": "R2", "recheck": "true"}, headers={"X-Wider-Author": "R2"}).json()
    assert json.loads(r.output) == api
    r = CliRunner().invoke(cli_app, ["label", "second-reviewer", "--reviewer", "R1", "--dir", str(p.root)])
    assert r.exit_code == 1 and "R1 made the first labels; choose another reviewer" in r.output
    assert _client(p).get("/api/labels/queue", params={"mode": "second", "reviewer": "R1"}, headers={"X-Wider-Author": "R1"}).status_code == 400


def test_queue_round_trip_across_samples(tmp_path):
    from wider.sample.design import draw_probability
    p, ids = _assessed(tmp_path)
    draw_probability(p, name="second")
    sid2 = p.store.scalar("SELECT sample_id FROM samples WHERE name='second'")
    rid = p.store.scalar("SELECT record_id FROM sample_rows WHERE sample_id=? ORDER BY queue_pos LIMIT 1", (sid2,))
    lb.label(p, "second", rid, "R1", "include")
    q = lb.second_reviewer_queue(p, "R2")                          # one queue over both samples
    assert q["n"] == 17
    assert "this queue has fewer excludes than includes and maybes, so it is not evenly mixed" in q["lines"]
    out = tmp_path / "q.csv"
    lb.export_queue_csv(q, out)
    _fill(out, "maybe", "")
    assert lb.import_queue_csv(p, out)["imported"] == 17
    assert p.store.scalar("SELECT decision FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id='R2'",
                          (sid2, rid)) == "maybe"
    bad = tmp_path / "bad.csv"
    bad.write_text("item,decision\nnope,include\n")
    assert lb.import_queue_csv(p, bad) == {"unknown_item": 1}


def test_queue_save_history_blind(tmp_path):
    p, ids = _assessed(tmp_path)
    c = _client(p)
    for mode, rev in (("recheck", "R1"), ("second", "R2")):
        items = c.get("/api/labels/queue", params={"mode": mode, "reviewer": rev}, headers={"X-Wider-Author": rev}).json()["items"]
        _blind(items)
        assert c.post("/api/labels", json={"item": items[0]["item"], "reviewer": rev, "decision": "exclude"}).status_code == 200
    hist = c.get("/api/history").json()
    top = hist[:2]
    assert {h["summary"] for h in top} == {"R1 saved a re-check label", "R2 saved a second-reviewer label"}
    for h in top:
        assert "validation" not in json.dumps(h) and not h.get("record_id")


def test_recheck_locked_samples(tmp_path):
    # label-completion-and-sealing D8: a lock keeps the re-check and second-reviewer queues open; a re-check save is
    # documented by its revision row and needs no correction reason
    p, ids = _assessed(tmp_path)
    before = (lb.recheck_queue(p, "R1")["n"], lb.second_reviewer_queue(p, "R2")["n"])
    lb.lock(p, "validation")
    q = lb.recheck_queue(p, "R1")
    assert (q["n"], lb.second_reviewer_queue(p, "R2")["n"]) == before and q["n"] > 0
    assert not any("is locked" in x for x in q["lines"])
    lb.label_item(p, q["items"][0]["item"], "R1", "exclude", "a")
    assert len(lb.revisions(p, "validation")) == 1 and not lb.corrections(p, "validation")


def test_second_reviewer_and_recheck_lines(tmp_path):
    # the spec's recommendation and re-check count lines (spec-checker round 2 F2, F3)
    from wider.recall import validation as va
    p, ids = _assessed(tmp_path)
    ls = va.review_lines(p, "validation")
    assert "A second reviewer is recommended on validation includes and maybes: recall is only as good as the " \
        "labels. 0 of 8 have one." in ls
    q = lb.second_reviewer_queue(p, "R2")
    for it in q["items"]:
        lb.label_item(p, it["item"], "R2", "exclude", "a")
    assert "8 of 8 have a second reviewer (queue enriched with 8 excludes)" in va.review_lines(p, "validation")
    rq = lb.recheck_queue(p, "R1")
    lb.label_item(p, rq["items"][0]["item"], "R1", "exclude", "a")
    assert any(x.startswith("1 labels re-checked under v") for x in va.review_lines(p, "validation"))
