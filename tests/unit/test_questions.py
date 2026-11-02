"""WP14 part 3: questions when the tool is in doubt; answers applied at once; queue or ask; routes, CLI, static page."""
import json
from collections import defaultdict

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.helpers import consent, mock_client
from tests.unit.test_enrich import RIS as THIN_RIS
from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core import journal
from wider.core import questions as qs
from wider.core.models import BenchmarkPaper, ProviderConfig
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.screen import engine as engmod
from wider.screen.decisions import decisions
from wider.screen.engine import screen
from wider.screen.protocol import run_protocol
from wider.ui.app import create_app

runner = CliRunner()


_ORIG_INIT = fakemod.FakeProvider.__init__


def _fake(monkeypatch, respond):
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: _ORIG_INIT(self, cfg, respond))


def test_failed_parse_becomes_a_question_and_the_answer_is_the_read(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    good = _responder({"calls": 0})
    bad_titles = set()

    def respond(req):
        if "Paper 3 " in req.user or "Paper 6 " in req.user:
            bad_titles.add(req.user[:40])
            return "this is not JSON at all"
        return good(req)
    _fake(monkeypatch, respond)
    res = screen(p, "corpus", pass_name="A")
    assert res["n_err"] == 2
    open_q = qs.list_questions(p)
    assert [q["kind"] for q in open_q] == ["screen_failed", "screen_failed"] and open_q[0]["run_ref"] == res["run_id"]
    assert {o["value"] for o in open_q[0]["options"]} == {"include", "maybe", "exclude", "retry"}
    screen(p, "corpus", pass_name="A")      # the retry fails again: no duplicate question while one is open
    assert len(qs.list_questions(p)) == 2
    q1, q2 = open_q
    a = qs.answer(p, q1["question_id"], "include", text="clearly about voice", by="ana")
    assert a["status"] == "answered" and "marked as the researcher's" in a["applied"]
    row = p.store.one("SELECT ok, rule_decision, parsed FROM screen_calls WHERE run_id=? AND record_id=? AND pass='A'",
                      (res["run_id"], q1["record_id"]))
    assert row["ok"] == 1 and row["rule_decision"] == "include" and json.loads(row["parsed"])["human"] is True
    run_dir = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    assert '"human": true' in (run_dir / "responses.jsonl").read_text()
    r2 = qs.answer(p, q2["question_id"], "retry", by="ana")
    assert "next run" in r2["applied"]
    with pytest.raises(ValueError, match="already answered"):
        qs.answer(p, q1["question_id"], "exclude")
    # the human read counts as done: the next run of the pass does not ask the model about it again
    calls = {"n": 0}

    def counting(req):
        calls["n"] += 1
        return good(req)
    _fake(monkeypatch, counting)
    screen(p, "corpus", pass_name="A")
    assert calls["n"] == 1      # only the "retry" record


def test_three_way_split_asks_and_the_answer_survives_reruns(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    seen = defaultdict(int)
    order = ["include", "maybe", "exclude"]

    base = _responder({"calls": 0})

    def respond(req):
        k = req.user
        d = order[seen[k] % 3]
        seen[k] += 1
        return {**base(req), "decision": d}
    _fake(monkeypatch, respond)
    monkeypatch.setattr(engmod, "apply_rule", lambda parsed, text, spec, thin=False, quote_min_chars=8:
                        {"raw": parsed["decision"], "final": parsed["decision"], "flags": []})
    out = run_protocol(p, "sample:development", "main")
    splits = [q for q in qs.list_questions(p) if q["kind"] == "screen_split"]
    assert len(splits) == out["n"] > 0 and out["maybe"] == out["n"]
    q = splits[0]
    assert q["context"]["votes"] == ["include", "maybe", "exclude"]
    qs.answer(p, q["question_id"], "exclude", text="not about voice", by="ana")
    d = decisions(p, "main")
    assert d[q["record_id"]] == "exclude"
    row = p.store.one("SELECT votes, reason FROM screen_decisions WHERE protocol='main' AND record_id=?", (q["record_id"],))
    assert "researcher:exclude" in row["votes"] and row["reason"].startswith("researcher (ana)")
    run_protocol(p, "sample:development", "main")   # re-run: the researcher's decision stands, no new question for it
    assert decisions(p, "main")[q["record_id"]] == "exclude"
    assert not [x for x in qs.list_questions(p) if x["record_id"] == q["record_id"]]


def test_enrichment_and_seed_questions(tmp_path, monkeypatch):
    from wider.construct import benchmark as bmmod
    from wider.dedup.engine import run as dedup_run
    from wider.enrich.fetch import candidates, enrich
    from wider.ingest.importer import add_file, import_all

    p = consent(Project.init(tmp_path / "r", platforms=["scopus"]))
    (tmp_path / "a.ris").write_text(THIN_RIS)
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    stats = enrich(p, pause_s=0, client=mock_client({}))   # every source answers 404: no abstract anywhere
    assert stats["exhausted"] == 2 and stats["failed"] == 0 and stats["questions"] == 2
    eq = qs.list_questions(p)
    with_doi = next(q for q in eq if q["context"]["doi"])
    no_doi = next(q for q in eq if not q["context"]["doi"])
    with pytest.raises(ValueError, match="needs a value"):
        qs.answer(p, with_doi["question_id"], "doi")
    with pytest.raises(ValueError, match="DOI"):
        qs.answer(p, with_doi["question_id"], "doi", text="not a doi")
    qs.answer(p, with_doi["question_id"], "doi", text="https://doi.org/10.1000/Corrected")
    assert p.store.scalar("SELECT doi FROM records WHERE record_id=?", (with_doi["record_id"],)) == "10.1000/corrected"
    assert with_doi["record_id"] in {c["record_id"] for c in candidates(p)}   # looked up again next run
    # the log is append-only: the exhausted attempt stays, a reset row follows it
    log = [r["status"] for r in p.store.query("SELECT status FROM enrichment_log WHERE record_id=? ORDER BY id", (with_doi["record_id"],))]
    assert log == ["exhausted", "reset"]
    text = " ".join(["word"] * 40)
    qs.answer(p, no_doi["question_id"], "abstract", text=text, by="ana")
    r = p.store.one("SELECT abstract_words, abstract_source, thin FROM records WHERE record_id=?", (no_doi["record_id"],))
    assert r["abstract_words"] == 40 and r["abstract_source"] == "researcher" and r["thin"] == 0
    # seed papers
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", doi="10.1000/wrong", set="Development")])
    monkeypatch.setattr(bmmod, "fetch_abstract", lambda doi, min_words=20, **kw: (None, []))
    bmmod.fetch(p, client=mock_client({}))
    sq = [q for q in qs.list_questions(p) if q["kind"] == "seed_unresolved"]
    assert len(sq) == 1 and sq[0]["ref"] == "B1"
    qs.answer(p, sq[0]["question_id"], "doi", text="10.1000/right")
    assert p.read_benchmark()[0].doi == "10.1000/right"


def test_failed_lookups_raise_no_question(tmp_path):
    from wider.dedup.engine import run as dedup_run
    from wider.enrich.fetch import enrich, summary
    from wider.ingest.importer import add_file, import_all

    p = consent(Project.init(tmp_path / "r", platforms=["scopus"]))
    (tmp_path / "a.ris").write_text(THIN_RIS)
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    stats = enrich(p, pause_s=0, client=mock_client(lambda r: httpx.Response(503)))
    assert stats["failed"] == 2 and stats["exhausted"] == 0 and not stats.get("questions")
    assert not [q for q in qs.list_questions(p) if q["kind"] == "enrich_unresolved"]
    assert summary(p)["failed"] == 2


def test_failed_proposal_asks_for_a_clarification_that_later_prompts_show(tmp_path, monkeypatch):
    from wider.construct.propose import ProposalError, build_prompt, propose

    p = Project.init(tmp_path / "r", questions=["What leads employees to speak up?"])
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.config.default_provider = "fake"
    p.save()
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    from wider.construct.benchmark import sync
    sync(p)
    _fake(monkeypatch, lambda req: "no json here")
    with pytest.raises(ProposalError):
        propose(p, "cells", provider_name="fake")
    q = qs.list_questions(p)[0]
    assert q["kind"] == "proposal_failed" and q["context"]["kind"] == "cells"
    assert "Clarifications from the researcher" not in build_prompt(p, "cells")[0]
    qs.answer(p, q["question_id"], "clarify", text="Grievance filing counts as voice.", by="ana")
    prompt, info = build_prompt(p, "cells")
    assert "Clarifications from the researcher" in prompt and "Grievance filing counts as voice." in prompt
    assert info["clarifications_n"] == 1


def test_question_routes_mode_and_cli(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    qid = qs.raise_question(p, "enrich_unresolved", "x1", "A record", "No abstract.", record_id=1, context={"doi": None})
    assert qs.raise_question(p, "enrich_unresolved", "x1", "A record", "No abstract.") is None
    c = TestClient(create_app(p))
    d = c.get("/api/questions").json()
    assert d["mode"] == "queue" and d["counts"] == {"open": 1} and d["questions"][0]["question_id"] == qid
    assert c.get("/api/questions/summary").json() == {"open": 1, "mode": "queue", "latest_open": qid}
    assert c.post(f"/api/questions/{qid}/answer", json={"choice": "nope"}).status_code == 400
    assert c.post("/api/questions/999/answer", json={"choice": "leave"}).status_code == 404
    assert c.post("/api/questions/mode", json={"mode": "sometimes"}).status_code == 400
    assert c.post("/api/questions/mode", json={"mode": "ask"}).json() == {"mode": "ask"}
    assert Project.load(p.root).config.doubts == "ask"
    r = c.post(f"/api/questions/{qid}/answer", json={"choice": "leave", "by": "ana"}).json()
    assert r["status"] == "answered" and r["applied"] == "left as it is"
    q2 = qs.raise_question(p, "seed_unresolved", "B9", "Seed", "No abstract.", ref="B9")
    assert c.post(f"/api/questions/{q2}/dismiss", json={"by": "ana"}).json()["status"] == "dismissed"
    assert [(h["kind"], h["author"]) for h in journal.changes(p, kind="dismiss")] == [("dismiss", "ana")]
    assert c.post(f"/api/questions/{q2}/dismiss", json={"by": "ana"}).status_code >= 400   # refused: nothing recorded
    assert len(journal.changes(p, kind="dismiss")) == 1
    assert len(c.get("/api/questions?status=all").json()["questions"]) == 2
    root, env = str(p.root), {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}
    q3 = qs.raise_question(p, "proposal_failed", "cells", "cells proposal", "Could not parse.", context={"kind": "cells"})
    r = runner.invoke(cli_app, ["questions", "list", "--dir", root], env=env)
    assert r.exit_code == 0 and "mode: ask" in r.output and "clarify <value>" in r.output
    assert runner.invoke(cli_app, ["questions", "answer", str(q3), "clarify", "--dir", root], env=env).exit_code == 1
    r = runner.invoke(cli_app, ["questions", "answer", str(q3), "clarify", "--value", "Use the term speaking up.", "--dir", root], env=env)
    assert r.exit_code == 0 and "clarification" in r.output
    assert "answered" in runner.invoke(cli_app, ["questions", "list", "--all", "--dir", root], env=env).output
    assert runner.invoke(cli_app, ["questions", "mode", "queue", "--dir", root], env=env).output.strip() == "doubts: queue"
    assert runner.invoke(cli_app, ["questions", "dismiss", "999", "--dir", root], env=env).exit_code == 1


def test_static_questions(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    html = c.get("/").text
    for needle in ['id="page-questions"', 'id="ask-dialog"', 'id="q-count"', 'name="q-mode"', "/static/questions.js", 'id="settings-doubts"']:
        assert needle in html, needle
    js = c.get("/static/questions.js").text
    assert "showModal" in js and "/api/questions/summary" in js and "/answer" in js
