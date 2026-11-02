"""Assessing a criteria change (validation-sample-planner D11, the owner's answer to Q6): the difference between the
early labels' codebook version and the frozen one, the affected set, the recommendation, the assessment, the lines,
the revisions, and CLI and route parity. Synthetic projects only."""
from __future__ import annotations

import copy
import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import CODEBOOK
from tests.unit.test_validation_plan import _ranked
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import journal
from wider.sample import labels as lb
from wider.sample.design import draw_probability


def _freeze(p, data):
    v = artifacts.save_new_version(p, "codebook", data, origin="manual")["version"]
    artifacts.set_status(p, "codebook", v, "frozen")
    return v


def _project(tmp_path, early, change):
    """A drawn project, a frozen codebook v_a, ``early`` = [(decision, reason)] labelled under it by R1, then a frozen
    v_b made by ``change`` (a function on a copy of the codebook)."""
    p = _ranked(tmp_path)
    draw_probability(p)
    va = _freeze(p, CODEBOOK)
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")]
    for rid, (d, reason) in zip(ids, early):
        lb.label(p, "validation", rid, "R1", d, reason)
    new = copy.deepcopy(CODEBOOK)
    change(new)
    vb = _freeze(p, new)
    return p, va, vb, ids


def _reword(cb):
    cb["target_description"] += " Reworded example."


def _population_and_e(cb):
    cb["elements"][0]["rule"] = "A changed population rule."
    cb["exclusion_reasons"] = [r for r in cb["exclusion_reasons"] if r["code"] != "e"]


AFFECTED = [("include", "")] * 6 + [("maybe", "")] * 2 + [("exclude", "e")] * 3 + [("exclude", "")] * 2 \
    + [("exclude", "b")] * 7


def test_assess_not_substantive(tmp_path):
    p, va, vb, _ = _project(tmp_path, [("exclude", "a")] * 20, _reword)
    ch = lb.criteria_change(p, "validation")
    assert "other changes: 1; elements changed: 0; reasons changed: 0" in ch["lines"]
    assert "Recommended: keep the labels; the eligibility criteria did not change" in ch["lines"]
    lb.assess_criteria(p, "validation", "no", note="wording only", author="R1")
    assert lb.due_labels(p, "validation") == []
    assert lb.criteria_lines(p, "validation") == [
        f"Criteria changed after 20 labels (v{va} to v{vb}); assessed as not substantive by R1: wording only"]
    assert any(h["summary"] == "R1 assessed a criteria change" for h in journal.changes(p, kind="criteria_assess"))


def test_criteria_cli_matches_api(tmp_path):
    p, *_ = _project(tmp_path, [("exclude", "a")] * 20, _reword)
    root = p.root
    r = CliRunner().invoke(cli_app, ["label", "criteria", "validation", "--json", "--dir", str(root)])
    assert r.exit_code == 0, r.output
    cli = json.loads(r.output)
    from wider.ui.app import create_app
    c = TestClient(create_app(p))
    assert c.get("/api/labels/criteria", params={"sample": "validation"}).json()["lines"] == cli["lines"]
    r = c.post("/api/labels/criteria", json={"sample": "validation", "substantive": "no", "note": "wording only",
                                             "author": "R1"})
    assert r.status_code == 200 and r.json()["assessment"]["substantive"] == "no"
    assert c.post("/api/labels/criteria", json={"sample": "validation", "substantive": "maybe"}).status_code == 400
    r = CliRunner().invoke(cli_app, ["label", "criteria", "validation", "--assess", "maybe", "--dir", str(root)])
    assert r.exit_code == 1


def test_affected_set(tmp_path):
    p, *_ = _project(tmp_path, AFFECTED, _population_and_e)
    ch = lb.criteria_change(p, "validation")
    assert len(ch["affected"]) == 11 and len(ch["not_identifiable"]) == 2
    assert ch["recommendation"] == "re-check the 11 affected labels (2 cannot be identified; re-check all 20 to cover them)"
    lb.assess_criteria(p, "validation", "yes", "affected", author="R1")
    assert len(lb.due_labels(p, "validation")) == 11


def test_large_change_recommends_all(tmp_path):
    def both(cb):
        for e in cb["elements"]:
            e["rule"] += " Changed."
    p, *_ = _project(tmp_path, AFFECTED, both)
    ch = lb.criteria_change(p, "validation")
    assert ch["recommended"] == "all" and ch["recommendation"] == "re-check all 20 early labels"
    lb.assess_criteria(p, "validation", "yes", "all", author="R1")
    assert len(lb.due_labels(p, "validation")) == 20


def test_large_change_when_no_version(tmp_path):
    p = _ranked(tmp_path)
    draw_probability(p)
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos LIMIT 5")]
    for rid in ids:
        lb.label(p, "validation", rid, "R1", "exclude", "a")   # no codebook yet: no version recorded
    _freeze(p, CODEBOOK)
    assert lb.criteria_change(p, "validation")["recommendation"] == "re-check all 5 early labels"


def test_partial_recheck_disclosed(tmp_path):
    p, va, vb, ids = _project(tmp_path, AFFECTED, _population_and_e)
    lb.assess_criteria(p, "validation", "yes", "affected", author="R1")
    due = set(lb.due_labels(p, "validation"))
    rows = p.store.query("SELECT label_id, record_id, decision, reason FROM labels WHERE sample_id=1")
    for r in rows:
        if r["label_id"] in due:
            lb.label(p, "validation", r["record_id"], "R1", r["decision"], r["reason"], recheck=True)
    assert lb.due_labels(p, "validation") == []
    assert (f"9 of the 20 early labels were not re-checked under v{vb}; they are not independently verified against "
            "the frozen criteria.") in lb.criteria_lines(p, "validation")
    assert "nothing to re-check: no label is due after the assessment" in lb.criteria_report(p, "validation")["lines"]


def test_unassessed_change_line(tmp_path):
    p, va, vb, _ = _project(tmp_path, AFFECTED, _population_and_e)
    assert lb.due_labels(p, "validation") == []
    assert lb.criteria_lines(p, "validation") == [
        f"The criteria changed after 20 labels (v{va} to v{vb}) and the change has not been assessed: "
        "wider label criteria validation"]


def test_revisions_kept(tmp_path):
    p, va, vb, ids = _project(tmp_path, AFFECTED, _population_and_e)
    lb.assess_criteria(p, "validation", "yes", "all", note="population rule changed", author="R1")
    lb.label(p, "validation", ids[0], "R1", "exclude", "a", recheck=True)
    revs = lb.revisions(p, "validation")
    assert len(revs) == 1
    r = revs[0]
    assert (r["old_decision"], r["new_decision"]) == ("include", "exclude")
    assert (r["old_codebook_version"], r["new_codebook_version"]) == (va, vb)
    assert r["assessment_note"] == "population rule changed"
    row = p.store.one("SELECT decision, recheck_from, codebook_version, criteria_frozen FROM labels WHERE record_id=?", (ids[0],))
    assert dict(row) == {"decision": "exclude", "recheck_from": "include", "codebook_version": vb, "criteria_frozen": 1}
    # still counted as early, never as after the freeze; and judged against the version it was first made under
    assert lb.criteria_counts(p, "validation")["before"] == 20
    lid = p.store.scalar("SELECT label_id FROM labels WHERE record_id=?", (ids[0],))
    assert lid in lb.criteria_change(p, "validation")["affected"]
    # undo removes the revision with the label change
    last = journal.changes(p, kind="label")[0]
    journal.undo(p, last["change_id"], author="R1")
    assert lb.revisions(p, "validation") == []
    assert p.store.scalar("SELECT decision FROM labels WHERE record_id=?", (ids[0],)) == "include"
    r = CliRunner().invoke(cli_app, ["label", "revisions", "validation", "--dir", str(p.root)])
    assert r.exit_code == 0 and "no re-checked labels" in r.output


def test_second_reviewer_recheck(tmp_path):
    # the independent second reviewer of the due labels; the queue itself is tested with the others in
    # test_validation_checks.py
    from tests.unit.test_validation_checks import test_second_reviewer_recheck as run
    run(tmp_path)


def test_recheck_keeps_note_and_needs_name(tmp_path):
    p, va, vb, ids = _project(tmp_path, AFFECTED, _population_and_e)
    p.store.execute("UPDATE labels SET note='full text says adults only' WHERE record_id=?", (ids[0],))
    lb.assess_criteria(p, "validation", "yes", "all", author="R1")
    q = lb.recheck_queue(p, "R1", "validation")
    import pytest
    with pytest.raises(ValueError):
        lb.label_item(p, q["items"][0]["item"], "", "exclude", "a")
    for it in q["items"]:
        lb.label_item(p, it["item"], "R1", "include", "")
    assert p.store.scalar("SELECT note FROM labels WHERE record_id=?", (ids[0],)) == "full text says adults only"
