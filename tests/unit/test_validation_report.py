"""Validation checks in the technical report and the methods draft (validation-sample-planner D14). Synthetic
projects only."""
from __future__ import annotations

import json
import time

from tests.unit.test_validation_suggest import _drawn as _drawn0
from wider.recall import validation as va
from wider.recall.outputs import methods_draft
from wider.recall.report import build_report, to_markdown


def _drawn(tmp_path, **kw):
    p = _drawn0(tmp_path, **kw)
    p.store.execute("UPDATE ranking_rows SET cell_scores='{\"c\": 0.5}'")   # the report's ranking section reads them
    return p


def _method(p):
    md = to_markdown(build_report(p, B=20))
    return md[md.index("### Recall: method"):]


def test_validation_checks_in_report(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDER_REVIEWER", "R1")
    p = _drawn(tmp_path)
    p.store.set_meta("validation_plan", json.dumps({"used": "proportional", "recommended": "recall-optimal"}))
    va.set_target(p, 0.6)
    time.sleep(1.1)                             # times are kept to the second; a tie reads as "after"
    build_report(p, B=20)                       # look 1
    va.stop(p, "validation", note="budget used")
    rep = build_report(p, B=20)
    vc = rep["recall"]["validation_checks"]
    assert vc["plan"]["used"] == "proportional" and vc["target"]["target"] == 0.6
    assert vc["outcome_basis"] == "exact" and vc["looks"][0]["look_index"] == 1
    assert vc["stops"][0]["note"] == "budget used" and vc["criteria"]["n"] == 120
    head = [x for x in vc["lines"]]
    assert head[0] == "Validation plan: used: proportional; recommended: recall-optimal."
    assert head[1].startswith("Recall target 0.60") and "before any recall result was seen" in head[1]
    order = [next(i for i, x in enumerate(head) if x.startswith(w))
             for w in ("Validation plan", "Recall target", "Exact bounds", "Validation stopped on", "Look 1 on", "Labels: 120")]
    assert order == sorted(order)
    assert any(x.endswith("at look 1; outcome: " + va.OUTCOME_WORDS[vc["stops"][0]["outcome"]] + ". Note: budget used.")
               for x in head)
    md = _method(p)
    assert "- Validation plan: used: proportional" in md and "- Labels: 120;" in md
    m = methods_draft(p)
    assert head[1] in m and va.FIRST_DRAW in m and va.CONSERVATIVE in m
    assert any(x in m for x in rep["recall"]["outcome"]["lines"])


def test_validation_checks_absent(tmp_path):
    p = _drawn(tmp_path, target=None)
    vc = build_report(p, B=20)["recall"]["validation_checks"]
    assert vc["plan"] is None and vc["stops"] == [] and vc["second_reviewer"] is None and vc["revisions"] == []
    assert vc["lines"][:2] == [va.NO_PLAN, va.NO_TARGET]
    assert [x for x in vc["lines"] if x.startswith("Labels:")] == [
        "Labels: 120; before the criteria were frozen: 120; after: 0; not recorded: 0; saved after a look: 0."]


def test_outcome_actions_after_stop_and_extend():
    # a stopped validation offers no buttons; unlabelled records hide "Add the suggested records" (ui-tester F1/F2)
    import re
    from pathlib import Path
    js = Path("wider/ui/static/app.js").read_text()
    body = re.search(r"function outcomeActionsHtml\(r\) \{(.*?)\n\}", js, re.S).group(1)
    assert "r.validation_stopped" in body and "r.validation_unlabelled" in body


# ------------------------------------------------------------------------------- recall-certification-workload (D3, D6)
def test_conservative_cost_said(tmp_path):
    """The cost sentence: once in the whole report.md, once in the methods draft and in `wider recall`, no number in
    it; a report from a record stored with the old sentence keeps a conservative line."""
    import re

    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.recall.report import write_report
    p = _drawn(tmp_path)
    md, _ = write_report(p, record_result=False, B=20)
    text = md.read_text()
    assert text.count(va.CONSERVATIVE) == 1 and not re.search(r"\d", va.CONSERVATIVE)
    assert "may need substantially more labels than a sharper method" in va.CONSERVATIVE
    assert methods_draft(p).count(va.CONSERVATIVE) == 1
    r = CliRunner().invoke(cli_app, ["recall", "main", "--sample", "validation", "-B", "20", "--dir", str(p.root)])
    assert " ".join(r.output.split()).count(va.CONSERVATIVE) == 1
    rec = build_report(p, B=20)["recall"]
    old = {**rec, "validation_lines": [va.CONSERVATIVE_OLD if x == va.CONSERVATIVE else x for x in rec["validation_lines"]]}
    assert va.CONSERVATIVE_OLD in va.validation_checks(p, "validation", old)["method"]


def test_open_result_sentence(tmp_path):
    p = _drawn(tmp_path)
    rec = build_report(p, B=20)["recall"]
    assert rec["outcome"]["outcome"] == "cannot_tell"
    lo, hi = rec["bounds"]["low"], rec["bounds"]["high"]
    look = p.store.scalar("SELECT MAX(look_index) FROM validation_look_states")
    open_ = (f"At look {look} the result is inconclusive: recall lies between {lo:.2f} and {hi:.2f} (exact bounds valid "
             f"at every look, 95%); the target 0.50 has been neither shown met nor shown missed.")
    assert va.open_result_line(p, "validation", rec) == open_
    assert methods_draft(p).count(open_) == 1 and _method(p).count(open_) == 1
    va.stop(p, "validation", author="R1")
    ended = (f"Validation ended at look {look} with an inconclusive result: recall lies between {lo:.2f} and {hi:.2f} "
             f"(exact bounds valid at every look, 95%); the target 0.50 was neither shown met nor shown missed.")
    assert va.open_result_line(p, "validation", rec) == ended and methods_draft(p).count(ended) == 1
    for key in ("met", "not_met"):
        assert va.open_result_line(p, "validation", {**rec, "outcome": {**rec["outcome"], "outcome": key}}) is None


def test_open_result_refuses_nothing(tmp_path):
    """An inconclusive or a not-met outcome refuses no report, export or package, on the CLI or a route."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.project import Project
    from wider.ui.app import create_app
    for target in (0.5, 0.99):   # inconclusive, then (with every relevant record kept far from the target) not met
        p = _drawn(tmp_path / str(target), target=target, keep=(lambda r: r % 7 != 0) if target == 0.5 else (lambda r: r <= 60))
        build_report(p, B=20)
        run = lambda *a: CliRunner().invoke(cli_app, [*a, "--dir", str(p.root)])  # noqa: E731
        # (`wider export prisma` needs imported database exports, which this synthetic frame has none of)
        for args in (("report",), ("export", "methods"), ("export", "references"), ("export", "package")):
            r = run(*args)
            assert r.exit_code == 0, (args, r.output)
        c = TestClient(create_app(Project.load(p.root)), headers={"X-Wider-Author": "Owner"})
        assert c.get("/api/outputs/methods/draft").status_code == 200
        assert c.get("/api/recall", params={"protocol": "main", "sample": "validation", "B": 20}).status_code == 200


def test_open_result_after_continued_stop(tmp_path):
    """spec-checker F1: a stop continued by an extension no longer reads as the end of validation."""
    from wider.sample.design import extend_probability
    p = _drawn(tmp_path)
    rec = build_report(p, B=20)["recall"]
    va.stop(p, "validation", author="R1")
    assert va.open_result_line(p, "validation", rec).startswith("Validation ended at look ")
    time.sleep(1.1)                             # times are kept to the second
    extend_probability(p, "validation", {"S2": 10}, author="R1")
    assert any(x.startswith("Validation was continued") for x in va.continue_lines(p, "validation"))
    assert va.open_result_line(p, "validation", rec).startswith("At look ")
