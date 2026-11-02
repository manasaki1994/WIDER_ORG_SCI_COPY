"""The recall target and rule, timed on every write path (validation-sample-planner D4). Synthetic projects only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_validation_plan import _ranked
from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.recall import validation as va
from wider.ui.app import create_app

LOOKS = ("CREATE TABLE IF NOT EXISTS validation_looks(look_id INTEGER PRIMARY KEY, sample_id INTEGER, result_set_key TEXT, "
         "settings_hash TEXT, prompt_version INTEGER, prompt_sha256 TEXT, codebook_version INTEGER, first_at TEXT, "
         "last_at TEXT, n INTEGER, first_by TEXT, choice TEXT, choice_by TEXT, choice_at TEXT)")


def _look(proj, at="2026-10-21T09:00:00+00:00"):
    """A recall result seen on the probability sample `validation` at ``at``."""
    st = proj.store
    st.execute("INSERT INTO samples(sample_id,name,kind,ranking_id,design,seed,n,created_at) "
               "VALUES(1,'validation','probability',1,'{}',1,10,'2026-10-20')")
    st.execute(LOOKS)
    st.execute("INSERT INTO validation_looks(sample_id,result_set_key,first_at,last_at,n) VALUES(1,'main:1',?,?,1)", (at, at))


def _cli(proj, *args):
    return CliRunner().invoke(cli_app, [*args, "--dir", str(proj.root)])


def _entries(proj):
    return journal.changes(proj, kind="recall_target")


def test_target_before_results(tmp_path):
    proj = _ranked(tmp_path, n=200)
    r = _cli(proj, "validation", "target", "0.9", "--rule", "maybes-as-includes", "--author", "ana")
    assert r.exit_code == 0, r.output
    p = Project.load(proj.root)
    assert p.config.protocol.recall.target == 0.9 and p.config.protocol.recall.low_recall_rule == "maybes-as-includes"
    assert p.config.protocol.recall.precision is None
    e = _entries(p)
    assert len(e) == 1 and e[0]["author"] == "ana" and e[0]["kind_label"] == "recall target"
    day = e[0]["at"][:10]
    want = f"Recall target 0.90, rule: treat maybes as includes; set {day} before any recall result was seen"
    s = _cli(proj, "validation", "status")
    assert s.exit_code == 0 and s.output.splitlines()[0] == want
    r = _cli(proj, "validation", "target", "0.9", "--rule", "maybes-as-includes", "--precision", "0.05")
    assert r.exit_code == 0 and Project.load(proj.root).config.protocol.recall.precision == 0.05


def test_target_cli_matches_api(tmp_path):
    proj = _ranked(tmp_path, n=200)
    c = TestClient(create_app(proj))
    r = c.post("/api/validation/target", json={"target": 0.9, "rule": "maybes-as-includes", "precision": 0.05})
    assert r.status_code == 200, r.text
    api = c.get("/api/validation/status").json()
    cli = json.loads(_cli(proj, "validation", "status", "--json").output)
    assert api == cli and api["target"] == 0.9 and api["precision"] == 0.05 and api["rule"] == "maybes-as-includes"
    assert api["lines"][0].startswith("Recall target 0.90, rule: treat maybes as includes; set ")
    assert r.json()["status"] == api["lines"][0]


def test_top_strata_needs_depth(tmp_path):
    proj = _ranked(tmp_path, n=200)
    c = TestClient(create_app(proj))
    for extra, words in ([], va.DEPTH_NEEDED), (["--depth", "0"], "depth must be between 1 and 200"), \
                        (["--depth", "201"], "depth must be between 1 and 200"):
        r = _cli(proj, "validation", "target", "0.9", "--rule", "top-strata", *extra)
        assert r.exit_code == 1 and words in r.output, r.output
        body = {"target": 0.9, "rule": "top-strata"} | ({"depth": int(extra[1])} if extra else {})
        a = c.post("/api/validation/target", json=body)
        assert a.status_code == 400 and a.json()["detail"] == words
    assert Project.load(proj.root).config.protocol.recall.target is None and not _entries(proj)
    ok = _cli(proj, "validation", "target", "0.9", "--rule", "top-strata", "--depth", "50")
    assert ok.exit_code == 0 and "humans screen every record ranked 1 to 50" in ok.output


def test_target_after_look_is_said(tmp_path):
    proj = _ranked(tmp_path, n=200)
    assert _cli(proj, "validation", "target", "0.9").exit_code == 0
    _look(proj, "2000-01-01T00:00:00+00:00")   # long before any entry made now
    proj.store.execute("UPDATE validation_looks SET first_at='2999-01-01'")   # the target came first
    assert va.timing_lines(Project.load(proj.root)) == []
    proj.store.execute("UPDATE validation_looks SET first_at='2000-01-01T00:00:00+00:00'")
    r = _cli(proj, "validation", "target", "0.8")
    assert "a recall result on validation was seen on 2000-01-01, before this target was set; every output will say so" \
        in r.output, r.output
    p = Project.load(proj.root)
    day = _entries(p)[0]["at"][:10]
    assert f"Recall target set on {day}, after a recall result was seen (2000-01-01)" in va.timing_lines(p)
    assert "target 0.90 → 0.80" in _entries(p)[0]["summary"]
    journal.undo(p, _entries(p)[0]["change_id"], author="ana", other=True)
    assert Project.load(proj.root).config.protocol.recall.target == 0.9


def test_target_set_outside_wider(tmp_path):
    proj = _ranked(tmp_path, n=200)
    p = Project.load(proj.root)
    p.config.protocol = p.config.protocol.set("recall.target", 0.85)
    (proj.root / "project.yaml").write_text(p.config_yaml() if hasattr(p, "config_yaml") else _yaml(p), encoding="utf-8")
    assert va.OUTSIDE in va.timing_lines(Project.load(proj.root))
    assert _cli(proj, "validation", "target", "0.85").exit_code == 0
    assert va.OUTSIDE not in va.timing_lines(Project.load(proj.root))


def _yaml(p):
    import yaml

    return yaml.safe_dump(p.config.model_dump(mode="json"), sort_keys=False)


def test_undone_target_not_outside(tmp_path):
    proj = _ranked(tmp_path, n=200)
    assert _cli(proj, "validation", "target", "0.9").exit_code == 0
    p = Project.load(proj.root)
    journal.undo(p, _entries(p)[0]["change_id"], author="ana", other=True)
    p = Project.load(proj.root)
    assert p.config.protocol.recall.target is None and va.timing_lines(p) == []
    assert _cli(proj, "validation", "target", "0.85").exit_code == 0
    p = Project.load(proj.root)
    assert va.OUTSIDE not in va.timing_lines(p) and va.status_line(p).startswith("Recall target 0.85, rule: none; set ")


def test_target_via_protocol_route_is_timed(tmp_path):
    proj = _ranked(tmp_path, n=200)
    _look(proj, "2000-01-01T00:00:00+00:00")
    c = TestClient(create_app(proj))
    assert _cli(proj, "protocol", "set", "recall.target", "0.8").exit_code == 0
    r = c.post("/api/protocol/set", json={"path": "recall.low_recall_rule", "value": "revise-prompt"})
    assert r.status_code == 200 and r.json()["timed"]
    assert _cli(proj, "protocol", "reset", "recall.target").exit_code == 0
    assert _cli(proj, "protocol", "set", "recall.primary_variant", "strict").exit_code == 0
    assert c.post("/api/protocol/set", json={"path": "recall.ci_level", "value": 0.9}).status_code == 200
    p = Project.load(proj.root)
    assert len(_entries(p)) == 5
    lines = va.timing_lines(p)
    day = _entries(p)[0]["at"][:10]
    for words in ("Recall target set", "Primary variant changed", "Interval level changed"):
        assert f"{words} on {day}, after a recall result was seen (2000-01-01)" in lines
    # a setting outside recall is saved as before, with no recall_target entry
    assert _cli(proj, "protocol", "set", "sampling.plan_sims", "300").exit_code == 0
    assert len(_entries(Project.load(proj.root))) == 5


def test_whole_protocol_reset_is_timed(tmp_path):
    proj = _ranked(tmp_path, n=200)
    c = TestClient(create_app(proj))
    assert c.post("/api/protocol/reset", json={}).status_code == 200      # nothing in recall changes
    assert not _entries(proj)
    assert _cli(proj, "validation", "target", "0.9").exit_code == 0
    assert _cli(proj, "protocol", "reset").exit_code == 0
    p = Project.load(proj.root)
    assert p.config.protocol.recall.target is None and len(_entries(p)) == 2
    assert _cli(proj, "validation", "target", "0.9").exit_code == 0
    assert c.post("/api/protocol/reset", json={}).status_code == 200
    assert len(_entries(Project.load(proj.root))) == 4


@pytest.mark.parametrize("bad", [["0.9", "--rule", "nonsense"]])
def test_target_refuses_unknown_rule(tmp_path, bad):
    proj = _ranked(tmp_path, n=200)
    r = _cli(proj, "validation", "target", *bad)
    assert r.exit_code == 1 and "unknown rule nonsense" in r.output


def _proto(**kw):
    from wider.core.protocol import ReviewProtocol as Protocol

    p = Protocol()
    for k, v in kw.items():
        p = p.set(f"recall.{k}", v)
    return p


def test_outcome_met():
    o = va.outcome({"estimate": 0.9, "low": 0.84, "high": 0.98}, _proto(target=0.8, low_recall_rule="maybes-as-includes"), 3)
    assert o["lines"] == ["Target 0.80 met: at look 3, the exact bounds valid at every look (95%), 0.84 to 0.98, "
                          "lie at or above it."]
    assert o["outcome"] == "met" and o["basis"] == "exact" and not o["more_validation"]


def test_outcome_not_met_rule_lines():
    pr = {"estimate": 0.8, "low": 0.70, "high": 0.88}
    o = va.outcome(pr, _proto(target=0.95, low_recall_rule="maybes-as-includes", primary_variant="strict"), 1,
                   {"estimate": 0.97, "low": 0.95, "high": 0.99})
    assert o["outcome"] == "not_met" and o["lines"] == [
        "Target 0.95 not met: at look 1, the exact bounds valid at every look (95%), 0.70 to 0.88, lie below it.",
        "Your rule: treat maybes as includes. With it, recall is 0.97 (exact bounds 0.95 to 0.99): target met under "
        "your rule."]
    o = va.outcome(pr, _proto(target=0.95, low_recall_rule="top-strata", top_strata_depth=500), 1,
                   {"estimate": 0.9, "low": 0.8, "high": 0.94, "missed": 12})
    assert o["lines"][1] == ("Your rule: humans screen every record ranked 1 to 500 (12 records the screening excluded "
                             "there). With it, recall is 0.90 (exact bounds 0.80 to 0.94): target not met under your rule.")
    o = va.outcome(pr, _proto(target=0.95, low_recall_rule="revise-prompt"), 1, sample="validation")
    assert o["lines"][1] == ("Your rule: revise the prompt. This uses up validation: draw a fresh sample before "
                             "estimating again (wider sample draw probability --name NAME --fresh-from validation).")


def test_rule_already_applied():
    for variant in ("lenient", "cautious"):
        o = va.outcome({"estimate": 0.8, "low": 0.7, "high": 0.88},
                       _proto(target=0.95, low_recall_rule="maybes-as-includes", primary_variant=variant), 1,
                       {"estimate": 0.97, "low": 0.95, "high": 0.99})
        assert o["lines"][1] == ("Your rule: treat maybes as includes. Your primary estimate already counts maybes as "
                                 "kept, so the rule changes nothing.")


def test_outcome_cannot_tell():
    o = va.outcome({"estimate": 0.93, "low": 0.84, "high": 0.99},
                   _proto(target=0.9, low_recall_rule="maybes-as-includes", primary_variant="strict"), 1,
                   {"estimate": 0.95, "low": 0.87, "high": 0.99})
    assert o["outcome"] == "cannot_tell" and o["more_validation"]
    assert o["lines"] == ["Inconclusive for the target 0.90: at look 1, the exact bounds valid at every look (95%), "
                          "0.84 to 0.99, contain it. This is not evidence that the target was missed; the labels so far "
                          "cannot tell met from not met.",
                          "Your rule: treat maybes as includes. With it, recall is 0.95 (exact bounds 0.87 to 0.99): "
                          "inconclusive."]


def test_outcome_without_target():
    o = va.outcome({"estimate": 0.9, "low": 0.76, "high": 0.97}, _proto(precision=0.1), 1)
    assert o["lines"] == [va.NO_TARGET, "The margin is wider than planned: the lower bound is 0.14 below the estimate, "
                                        "more than the 0.10 planned."] and o["more_validation"] and o["outcome"] is None
    assert va.outcome({"estimate": 0.9, "low": 0.84, "high": 0.97}, _proto(precision=0.1), 1)["lines"] == [va.NO_TARGET]
    o = va.outcome({"estimate": 0.9, "low": 0.5, "high": 0.97}, _proto(), 1)
    assert o["lines"] == [va.NO_TARGET] and not o["more_validation"]


def _label_all(p, _label):
    """``_label`` of test_validation_scope (20 labelled, 10 of them include), then the rest of the validation sample
    excluded, so recall can be revealed (label-completion-and-sealing D1)."""
    from wider.sample.labels import label

    for rid in _label(p, "validation")[20:]:
        label(p, "validation", rid, "R1", "exclude", "a")


def test_outcome_same_words(tmp_path):
    """D10: the outcome block's words are one list, the same in `wider recall --json`, GET /api/recall and report.json."""
    from tests.unit.test_validation_scope import _drawn, _label
    from wider.recall.report import build_report

    p = _drawn(tmp_path)
    _label_all(p, _label)
    va.set_target(p, 0.5)
    r = _cli(p, "recall", "main", "--sample", "validation", "-B", "20", "--json")
    assert r.exit_code == 0, r.output
    cli = json.loads(r.output)
    api = TestClient(create_app(Project.load(p.root))).get(
        "/api/recall", params={"protocol": "main", "sample": "validation", "B": 20}).json()
    rep = build_report(Project.load(p.root), "main", "validation", B=20)["recall"]
    assert cli["validation_lines"] and cli["validation_lines"] == api["validation_lines"] == rep["validation_lines"]
    assert any(x.startswith(("Target 0.50", "Inconclusive for the target 0.50")) for x in cli["validation_lines"])
    res = _cli(p, "results", "main")   # the Results dashboard repeats them
    assert res.exit_code == 0 and all(res.output.count(x) == 1 for x in cli["validation_lines"]
                                      if x.startswith(("Target 0.50", "Inconclusive for the target 0.50")))
    assert cli["benchmark_apart"] == api["benchmark_apart"] == rep["benchmark_apart"]
    assert cli["scope_line"] == api["scope_line"] == rep["scope_line"] and cli["lines"] == api["lines"] == rep["lines"]


def test_inconclusive_key_kept(tmp_path):
    """The words say inconclusive; --json, the API and the stored look keep the key cannot_tell and more_validation."""
    from tests.unit.test_validation_scope import _drawn, _label
    p = _drawn(tmp_path)
    _label_all(p, _label)
    va.set_target(p, 0.5)
    r = _cli(p, "recall", "main", "--sample", "validation", "-B", "20", "--json")
    cli = json.loads(r.output)
    api = TestClient(create_app(Project.load(p.root))).get(
        "/api/recall", params={"protocol": "main", "sample": "validation", "B": 20}).json()
    stored = json.loads(Project.load(p.root).store.get_meta("last_validation_lines:validation"))["outcome"]
    for o in (cli["outcome"], api["outcome"], stored):
        if o["outcome"] == "cannot_tell":
            assert o["more_validation"] is True and any(x.startswith("Inconclusive for the target") for x in o["lines"])
        else:
            assert o["outcome"] in ("met", "not_met")
    assert {cli["outcome"]["outcome"], api["outcome"]["outcome"], stored["outcome"]} <= {"met", "not_met", "cannot_tell"}
    assert va.OUTCOME_WORDS == {"met": "met", "not_met": "not met", "cannot_tell": "inconclusive"}


def _verdicts(lines):
    return [x for x in lines if x.startswith(("Target 0.", "Inconclusive for the target", "Your rule:"))]


def test_target_set_after_a_look_shows_current_state_only(tmp_path):
    """Audit C2: a look without a target, then `wider validation target 0.80`: status says the target was set after a
    result was seen, never also that no target was set; the stored verdictless result asks for a new estimate."""
    from tests.unit.test_validation_scope import _drawn, _label
    from wider.recall.dashboard import dashboard

    p = _drawn(tmp_path)
    _label_all(p, _label)
    assert _cli(p, "recall", "main", "--sample", "validation", "-B", "20").exit_code == 0
    assert va.NO_TARGET in va.status(Project.load(p.root))["lines"]
    assert _cli(p, "validation", "target", "0.80").exit_code == 0
    cli = json.loads(_cli(p, "validation", "status", "--json").output)
    api = TestClient(create_app(Project.load(p.root))).get("/api/validation/status").json()
    assert api["lines"] == cli["lines"]
    text = " ".join(_cli(p, "validation", "status").output.split())
    for lines in (cli["lines"], [text]):
        assert not any(va.NO_TARGET in x for x in lines)
        assert any("Recall target 0.80" in x and "after a recall result was seen" in x for x in lines)
    day = json.loads(Project.load(p.root).store.get_meta("last_validation_lines:validation"))["at"][:10]
    assert (f"The last recall result on validation ({day}) was judged without a target; estimate recall again for a "
            "verdict on the target 0.80.") in cli["lines"]
    assert cli["outcome"] is None and cli["target_stale"] is True and not _verdicts(cli["validation_lines"])
    assert dashboard(Project.load(p.root), "main")["recall"]["outcome"] is None   # the Results dashboard too
    # a new estimate judges against 0.80 and the line goes
    assert _cli(p, "recall", "main", "--sample", "validation", "-B", "20").exit_code == 0
    st = va.status(Project.load(p.root))
    assert st["target_stale"] is False and _verdicts(st["lines"]) and not any("was judged" in x for x in st["lines"])


def test_target_undone_after_a_look_shows_current_state_only(tmp_path):
    """Audit C2: a look judged against 0.80 (with a rule), then `wider history undo` of the target: status says no
    target is set and gives no stored verdict or rule line for 0.80."""
    from tests.unit.test_validation_scope import _drawn, _label

    p = _drawn(tmp_path)
    _label_all(p, _label)
    assert _cli(p, "validation", "target", "0.80", "--rule", "revise-prompt", "--author", "ana").exit_code == 0
    assert _cli(p, "recall", "main", "--sample", "validation", "-B", "20").exit_code == 0
    assert _verdicts(va.status(Project.load(p.root))["lines"])
    cid = _entries(Project.load(p.root))[0]["change_id"]
    r = _cli(p, "history", "undo", str(cid), "--author", "ana")
    assert r.exit_code == 0, r.output
    assert Project.load(p.root).config.protocol.recall.target is None
    cli = json.loads(_cli(p, "validation", "status", "--json").output)
    api = TestClient(create_app(Project.load(p.root))).get("/api/validation/status").json()
    assert api["lines"] == cli["lines"] and cli["lines"][0] == va.NO_TARGET
    assert not _verdicts(cli["lines"]) and not _verdicts(cli["validation_lines"]) and cli["outcome"] is None
    assert any(x.startswith("The last recall result on validation (") and x.endswith(
        "was judged against the target 0.80; no target is set now, so it gives no verdict.") for x in cli["lines"])
    text = " ".join(_cli(p, "validation", "status").output.split())
    assert "Inconclusive for the target" not in text and "Target 0.80" not in text and "Your rule" not in text
