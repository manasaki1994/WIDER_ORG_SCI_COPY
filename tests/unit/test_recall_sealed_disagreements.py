"""Disagreements with sealed reference labels stated (recall-final-check-fixes D6, D7): sealing keeps recall on the
reference labels, and every place recall is read says how many sealed records have another label that disagrees.
Synthetic, topic-neutral records only."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_package import _codebook, _export, _write
from tests.unit.test_validation_plan import Band, _ranked
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.recall.estimate import estimate
from wider.recall.outputs import recall_sentences
from wider.recall.validation import status, validation_checks
from wider.sample import labels as lb
from wider.sample import package as pk
from wider.sample.design import draw_probability, extend_probability
from wider.screen.decisions import set_decision
from wider.ui.app import create_app

runner = CliRunner()
LINE = ("10 records of validation have another label that disagrees with their reference label; recall uses the "
        "reference labels. List them with wider label agreement validation REVIEWER OTHER_REVIEWER; change a reference "
        "with wider label adjudicate validation RECORD DECISION --by NAME --correction-reason WHY.")
FIRST = "10 records of validation have another label that disagrees with their reference label; recall uses the reference labels."
UI_LINE = (f"{FIRST} List them in the agreement table (Samples and labelling, Second reviewer and adjudication); change "
           "a reference there, with a correction reason.")   # the UI pages' copy (audit C1)


def _cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)])


def _ids(p):
    return [r["record_id"] for r in p.store.query(
        "SELECT record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='validation' "
        "ORDER BY queue_pos")]


def _sealed(tmp_path, n=5, name="p", look=True):
    """``validation`` of 2*``n`` records (bands of 20 and 40 ranks), every record screened include and labelled include
    by the oracle, target 0.75; with ``look`` a recall look seals them."""
    p = _ranked(tmp_path, n=60, bands=[Band(name="S1", start=1, end=20, n=n), Band(name="S2", start=21, end=None, n=n)],
                name=name)
    p.config.protocol = p.config.protocol.set("recall.target", 0.75)
    p.save()
    draw_probability(p)
    _codebook(p, "frozen")
    for r in range(1, 61):
        set_decision(p, "main", r, "include")
    for rid in _ids(p):
        lb.label(p, "validation", rid, "oracle", "include")
    if look:
        estimate(p, "validation", "main", B=20)
    lb.add_reviewer(p, "rev2")
    return p


def _second(p, tmp_path, decision="exclude", earlier=None, file="dec.json"):
    P = pk.make(p, "rev2", ["validation"], "second", earlier=earlier, out=tmp_path / f"{file}.html")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], {i: decision for i in range(P["n_items"])}), file))
    return P


def _disagreeing(tmp_path):
    p = _sealed(tmp_path)
    before = estimate(p, "validation", "main", B=20)
    P = _second(p, tmp_path)
    assert P["n_items"] == 10
    return p, before


def test_blind_second_review_disagreements_stated(tmp_path):
    p, before = _disagreeing(tmp_path)
    out = estimate(p, "validation", "main", B=20)
    v = p.config.protocol.recall.primary_variant
    assert out["variants"][v]["recall"] == before["variants"][v]["recall"] == 1.0
    assert out["open_disagreements"] == 0 and out["sealed_disagreements"] == 10
    assert LINE in out["lines"]
    j = json.loads(_cli(p, "recall", "main", "--sample", "validation", "--json").output)
    assert j["open_disagreements"] == 0 and j["sealed_disagreements"] == 10 and LINE in j["lines"]
    assert LINE in " ".join(_cli(p, "recall", "main", "--sample", "validation").output.split())
    # the Recall page's estimate, the status, the report's validation checks; the methods draft its first sentence
    api = TestClient(create_app(Project.load(p.root))).get("/api/recall", params={"protocol": "main",
                                                                                  "sample": "validation"}).json()
    assert api["sealed_disagreements"] == 10 and UI_LINE in api["lines"] and LINE not in api["lines"]
    assert LINE in status(p, "validation")["lines"]
    assert LINE in " ".join(_cli(p, "validation", "status").output.split())
    rec = json.loads(p.store.get_meta("last_validation_lines:validation"))
    assert LINE in validation_checks(p, "validation", rec)["lines"]
    draft = " ".join(recall_sentences(p, out))                      # the recall part of the methods draft
    assert FIRST in draft and "--correction-reason" not in draft
    # counts only: no record, decision or reviewer
    shown = [x for x in out["lines"] + api["lines"] + j["lines"] + status(p, "validation")["lines"]
             + recall_sentences(p, out) if "disagrees with" in x]
    assert shown and all(x in (LINE, FIRST, UI_LINE) for x in shown)
    assert not any(n in x for x in shown for n in ("rev2", "oracle", "exclude", "include"))
    # the package's report line
    line = next(x for x in pk.report_section(p)["lines"] if x.startswith("rev2: second reviewer"))
    assert line.endswith("labels used for agreement only; all its records had reference labels sealed by an earlier "
                         "recall look")


def test_correction_settles_a_disagreement(tmp_path):
    p, _ = _disagreeing(tmp_path)
    rid = _ids(p)[0]
    r = _cli(p, "label", "adjudicate", "validation", str(rid), "exclude", "--by", "dana",
             "--correction-reason", "second review")
    assert r.exit_code == 0, r.output
    out = estimate(p, "validation", "main", B=20)
    assert out["sealed_disagreements"] == 9
    assert LINE.replace("10 records", "9 records") in out["lines"]
    assert any(x.startswith("1 record of validation had its labels changed after its recall was first revealed")
               for x in out["lines"])


def test_left_out_and_agreeing_labels_not_counted(tmp_path):
    p = _sealed(tmp_path)
    first = _ids(p)[0]
    lb.label(p, "validation", first, "rev2", "exclude", made_in="unblinded queue")
    assert lb.sealed_disagreements(p, "validation") == 1
    # a package with --earlier exclude leaves the earlier label out; the package's labels agree with the reference
    _second(p, tmp_path, decision="include", earlier="exclude")
    assert pk.left_out(p)
    lb.add_reviewer(p, "rev3")
    lb.label(p, "validation", first, "rev3", "include")
    out = estimate(p, "validation", "main", B=20)
    assert out["sealed_disagreements"] == 0
    assert not any("disagrees with" in x for x in out["lines"] + status(p, "validation")["lines"])


def test_one_later_disagreement_counted(tmp_path):
    p = _sealed(tmp_path)
    lb.label(p, "validation", _ids(p)[0], "rev2", "exclude")          # blind queue, after the look
    out = estimate(p, "validation", "main", B=20)
    assert out["open_disagreements"] == 0 and out["sealed_disagreements"] == 1
    assert ("1 record of validation has another label that disagrees with its reference label; recall uses the "
            "reference labels. List them with wider label agreement validation REVIEWER OTHER_REVIEWER; change a "
            "reference with wider label adjudicate validation RECORD DECISION --by NAME --correction-reason WHY."
            ) in out["lines"]


def test_package_line_by_sealed_records(tmp_path):
    # 4 records sealed by a look, then 6 added by an extension and labelled: a package of 10 imported after
    p = _sealed(tmp_path, n=2)
    extend_probability(p, "validation", {"S1": 3, "S2": 3}, author="ana")
    for rid in _ids(p):
        if not p.store.scalar("SELECT 1 FROM labels WHERE record_id=? AND reviewer_id='oracle'", (rid,)):
            lb.label(p, "validation", rid, "oracle", "include")
    P = _second(p, tmp_path, decision="include")
    assert P["n_items"] == 10
    line = next(x for x in pk.report_section(p)["lines"] if x.startswith("rev2: second reviewer"))
    assert line.endswith("labels used for agreement, and for final labels except on 4 records whose reference labels "
                         "were sealed by an earlier recall look")
    # a package imported before any look keeps the earlier words
    q = _sealed(tmp_path, name="q", look=False)
    _second(q, tmp_path, decision="include", file="q.json")
    line = next(x for x in pk.report_section(q)["lines"] if x.startswith("rev2: second reviewer"))
    assert line.endswith("labels used for agreement and final labels")


def test_hint_commands_list_the_records(tmp_path):
    """Audit C1: the line's `wider label agreement` lists the disagreeing records; the UI pages (Recall, validation
    status, Results) point to the agreement table instead of the CLI commands."""
    p, _ = _disagreeing(tmp_path)
    r = _cli(p, "label", "agreement", "validation", "oracle", "rev2")
    assert r.exit_code == 0, r.output
    assert "10 disagreements" in r.output
    for rid in _ids(p):
        assert f"record {rid}: oracle include, rev2 exclude" in r.output
    c = TestClient(create_app(Project.load(p.root)))
    for url, params in (("/api/recall", {"protocol": "main", "sample": "validation"}),
                        ("/api/validation/status", {"sample": "validation"}), ("/api/results", {"protocol": "main"})):
        text = json.dumps(c.get(url, params=params).json())
        assert UI_LINE in text and "wider label agreement" not in text and "wider label adjudicate" not in text, url
    t = c.post("/api/validation/target", json={"target": 0.75}).json()
    assert UI_LINE in t["lines"] and LINE not in t["lines"]
    assert LINE in " ".join(_cli(p, "validation", "status").output.split())   # the CLI keeps the commands
