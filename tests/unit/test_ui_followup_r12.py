"""Fixes from the thirteenth round of C1 checks: the Google Scholar string when no required block is on the first path,
a single word longer than the limit, the Dedup count after an undo, and the adjacent CLI after a model error."""
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_records import _deduped
from wider.cli import app
from wider.construct import adjacent
from wider.core.project import Project
from wider.dedup.engine import queue_rows
from wider.search.ir import Block, Query
from wider.search.render.reduced import render_gscholar
from wider.ui.app import create_app


def test_gscholar_fallback_leaves_out_not_and_optional_blocks():
    q = Query(blocks=[Block(key="a", terms=["plankton"]), Block(key="b", terms=["voice"]),
                      Block(key="c", terms=["employee"], role="optional"), Block(key="d", terms=["silence"])],
              logic={"and": [{"not": "a"}, {"or": [{"and": ["c"]}, "d"]}]})
    s = render_gscholar(q)   # the first path holds no required block, so the fallback picks the blocks
    assert "plankton" not in s and "employee" not in s and s == "(voice) (silence)"


def test_gscholar_cuts_one_long_word_inside_its_brackets():
    for term in ["x" * 300, '"' + "y" * 300 + ' z"']:
        s = render_gscholar(Query(blocks=[Block(key="a", terms=[term])], logic={"and": ["a"]}))
        assert len(s) <= 256 and s.startswith("(") and s.endswith(")") and s.count('"') % 2 == 0, s[-8:]


def test_undo_takes_a_decision_off_the_dedup_count(tmp_path):
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    q = queue_rows(p)[0]
    pending = c.get("/api/dedup").json()["pending"]
    assert c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "duplicate",
                                            "by": "tester"}).status_code == 200
    assert c.get("/api/dedup").json()["decided_since_run"] == 1
    assert c.post("/api/history/undo-last?author=tester").status_code == 200
    d = c.get("/api/dedup").json()
    assert d["decided_since_run"] == 0 and d["pending"] == pending


def test_adjacent_cli_reports_a_model_error_without_a_traceback(tmp_path, monkeypatch):
    p = Project.init(tmp_path / "r", platforms=["scopus"])

    def boom(*a, **k):
        raise RuntimeError("cannot reach the backend")

    monkeypatch.setattr(adjacent, "propose_from_question", boom)
    r = CliRunner().invoke(app, ["construct", "adjacent", "propose", "--dir", str(p.root)])
    assert r.exit_code == 1 and "cannot reach the backend" in r.output and "Traceback" not in r.output


def test_a_pair_decided_twice_counts_once(tmp_path):
    # spec-checker, 28th round: changing a pair's decision since the run does not count it again
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    q = queue_rows(p)[0]
    for decision in ("duplicate", "not_duplicate"):
        assert c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": decision,
                                                "by": "tester"}).status_code == 200
    assert c.get("/api/dedup").json()["decided_since_run"] == 1
