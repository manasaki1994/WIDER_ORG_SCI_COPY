"""review-plan-and-question-window, group 1: question pages and consequences, the Review plan's task cards
(`wider/core/plan.py`) and the needs-you mark of setup, construct and query."""
import json

import pytest

from wider.core import plan, stages
from wider.core import questions as qs
from wider.core.models import BenchmarkPaper
from wider.core.project import Project


def _project(tmp_path, platforms=("scopus",), questions=("Q?",)) -> Project:
    return Project.init(tmp_path / "r", questions=list(questions), platforms=list(platforms))


def _seeds(p: Project, dev: int, val: int) -> None:
    p.write_benchmark([BenchmarkPaper(benchmark_id=f"B{i:03d}", title=f"Seed title {i}",
                                      set="Development" if i <= dev else "Validation")
                       for i in range(1, dev + val + 1)])


def _seed_question(p: Project, bid: str = "B001") -> int:
    return qs.raise_question(p, "seed_unresolved", bid, f"Seed title {bid}", f"No source found {bid}.",
                             context={"doi": None}, ref=bid)


def _state(p: Project, key: str) -> dict:
    return next(s for s in stages.stage_states(p) if s["key"] == key)


# ------------------------------------------------------------------------------------------- 1.1 and 1.3: pages
def test_every_kind_names_a_page():
    pages = {"screen", "enrich", "construct", "query", "setup?task=seeds"}
    assert all(spec.get("page") in pages for spec in qs.KINDS.values()), {k: v.get("page") for k, v in qs.KINDS.items()}
    assert qs.KINDS["seed_unresolved"]["page"] == "setup?task=seeds"


def test_list_filters_by_page(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    _seed_question(p)
    qs.raise_question(p, "proposal_failed", "x", "Proposal", "The proposal failed.")
    assert [q["kind"] for q in qs.list_questions(p, page="setup")] == ["seed_unresolved"]
    assert [q["kind"] for q in qs.list_questions(p, page="setup?task=seeds")] == ["seed_unresolved"]
    assert [q["kind"] for q in qs.list_questions(p, page="construct")] == ["proposal_failed"]
    assert qs.list_questions(p, page="screen") == []
    assert len(qs.list_questions(p)) == 2


def test_anchor_is_seed_id(tmp_path):
    p = _project(tmp_path)
    _seed_question(p, "B007")
    q = qs.list_questions(p)[0]
    assert q["anchor"] == "B007" and q["page"] == "setup?task=seeds"


def test_hidden_screening_questions_stay_hidden_with_page(tmp_path, monkeypatch):
    p = _project(tmp_path)
    for rid in (5, 6):
        qs.raise_question(p, "screen_failed", f"r{rid}", f"Record {rid}", "No read.", record_id=rid)
    monkeypatch.setattr(qs, "_hidden_records", lambda project: {5})
    shown = qs.list_questions(p, page="screen")
    assert [q["record_id"] for q in shown] == [6] and shown[0]["anchor"] == 6
    assert [q["record_id"] for q in qs.list_questions(p, page="screen", limit=1)] == [6]


def test_original_kinds_have_consequences():
    for kind in qs.ORIGINAL_KINDS:
        opts = qs.KINDS[kind]["options"]
        assert opts and all(o.get("consequence", "").strip() for o in opts), kind
    assert [o["value"] for o in qs.KINDS["enrich_unresolved"]["options"] if o.get("default")] == ["doi"]
    assert [o["value"] for o in qs.KINDS["seed_unresolved"]["options"] if o.get("default")] == ["doi"]
    assert [o["value"] for o in qs.KINDS["proposal_failed"]["options"] if o.get("default")] == ["clarify"]


def test_screening_kinds_mark_no_default():
    for kind in ("screen_failed", "screen_split"):
        assert not any(o.get("default") for o in qs.KINDS[kind]["options"]), kind


def test_old_question_gets_consequences(tmp_path):
    """A question stored before the consequences existed (options without them) gets them by value on read."""
    p = _project(tmp_path)
    qid = _seed_question(p)
    bare = [{"value": "doi", "label": "Give the DOI", "needs_text": True}, {"value": "leave", "label": "Leave it"}]
    p.store.execute("UPDATE questions SET options=? WHERE question_id=?", (json.dumps(bare), qid))
    for q in (qs.get(p, qid), qs.list_questions(p)[0]):
        by = {o["value"]: o for o in q["options"]}
        assert by["doi"]["default"] is True and "benchmark.csv" in by["doi"]["consequence"]
        assert by["leave"]["consequence"] == "Seed coverage is counted without this paper."
        assert by["doi"]["label"] == "Give the DOI"   # the stored text is kept


# ------------------------------------------------------------------------------------------- 1.2: plan.tasks
def test_plan_cards_of_a_new_project(tmp_path):
    p = _project(tmp_path, platforms=("wos", "scopus"))
    for pl in p.config.platforms:
        pl.role = "development" if pl.key == "scopus" else "validation"
    p.save()
    _seeds(p, 4, 2)
    cards = plan.tasks(p)
    assert [plan.card_line(c) for c in cards] == [
        "Done · Research question · 1 question saved",
        "Done · Seed papers · 6 seed papers: 4 development, 2 validation",
        "To do · Prior reviews · add a prior review or say you know of none",
        "Done · Databases · 2 platforms, development on scopus",
        "Optional · Find more seed papers · nothing waiting"]
    assert [c["key"] for c in cards] == ["question", "seeds", "prior", "databases", "more"]
    assert plan.summary_line(cards) == "complete the tasks above"
    from wider.prior import reviews
    reviews.set_policy(p, none_known=True)
    cards = plan.tasks(p)
    assert cards[2]["state"] == "done" and cards[2]["line"] == "no prior review known"
    assert plan.summary_line(cards) == "ready for the construct stage"


def test_plan_without_anything(tmp_path):
    p = _project(tmp_path, platforms=(), questions=())
    assert [(c["state"], c["line"]) for c in plan.tasks(p)][:4] == [
        ("todo", "no research question yet"), ("todo", "no seed papers yet"),
        ("todo", "add a prior review or say you know of none"), ("todo", "no databases chosen")]


def test_plan_only_validation_seeds(tmp_path):
    """Scenario "No development seed paper": every seed paper is in the validation set, so the Seed papers card is not
    done, asks to assign some to the development set, and the plan is not ready."""
    p = _project(tmp_path)
    _seeds(p, 0, 3)
    cards = plan.tasks(p)
    assert cards[1]["state"] == "todo" and cards[1]["done"] is False
    assert cards[1]["line"] == "3 seed papers: 0 development, 3 validation (assign some to the development set)"
    assert plan.summary_line(cards) == "complete the tasks above"


def test_plan_seed_question_waits(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    qid = _seed_question(p)
    seeds = plan.tasks(p)[1]
    assert seeds["state"] == "waiting" and seeds["questions"] == 1 and seeds["line"].endswith("; 1 not found")
    assert plan.card_line(seeds).startswith("Waiting for you · Seed papers · ")
    qs.answer(p, qid, "leave")
    seeds = plan.tasks(p)[1]
    assert seeds["state"] == "done" and seeds["questions"] == 0 and "not found" not in seeds["line"]


def test_plan_names_no_validation_seed(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 2)
    text = json.dumps(plan.tasks(p))
    assert "Seed title 2" not in text and "Seed title 3" not in text and "B002" not in text
    assert "2 validation" in text


def test_plan_more_counts_candidates(tmp_path):
    p = _project(tmp_path)
    for i, status in enumerate(("new", "new", "new", "dismissed")):
        p.store.execute("INSERT INTO seed_chain_candidates(title, title_norm, status, chain_round, linking_seeds_json) "
                        "VALUES(?,?,?,?,?)", (f"Cand {i}", f"cand {i}", status, 1, "[]"))
    more = plan.tasks(p)[4]
    assert more["state"] == "optional" and more["line"] == "3 candidates to look at" and more["optional"]


# ------------------------------------------------------------------------------------------- 1.4: needs you
def test_seed_question_marks_review_plan(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    _seed_question(p)
    s = _state(p, "setup")
    assert s["needs_you"] == "1 question for you"
    assert "needs you: 1 question for you" in stages.state_text(s)


def test_needs_you_on_a_stage_not_done(tmp_path):
    p = _project(tmp_path)
    qs.raise_question(p, "proposal_failed", "x", "Proposal", "The proposal failed.")
    qs.raise_question(p, "proposal_failed", "y", "Proposal", "The proposal failed.")
    s = _state(p, "construct")
    assert s["state"] != "done" and s["needs_you"] == "2 questions for you"


def test_answered_question_clears_the_mark(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    qid = _seed_question(p)
    assert _state(p, "setup")["needs_you"]
    qs.answer(p, qid, "leave")
    assert not _state(p, "setup")["needs_you"]


def test_needs_you_kept_for_dedup_sample_screen(monkeypatch, tmp_path):
    """dedup, sample and screen keep their own needs-you, still only when done."""
    p = _project(tmp_path)
    for key in ("dedup", "sample", "screen"):
        monkeypatch.setitem(stages.NEEDS_YOU, key, lambda project, k=key: f"{k} work")
    by = {s["key"]: s for s in stages.stage_states(p)}
    for key in ("dedup", "sample", "screen"):
        assert by[key]["state"] != "done" and not by[key].get("needs_you"), key
    assert set(stages.NEEDS_YOU) >= {"dedup", "sample", "screen", "setup", "construct", "query"}
    assert stages.NEEDS_YOU_ANY_STATE == ("setup", "construct", "query")


def test_guide_state_words_needs_you_not_done():
    from wider.guide import _state_words

    s = {"key": "construct", "state": "ready", "needs_you": "1 question for you"}
    assert _state_words(s, "construct") == "needs attention: 1 question for you"
    assert _state_words({"key": "x", "state": "done", "needs_you": None}, None) == "completed"
    assert _state_words({"key": "x", "state": "ready"}, "x") == "not started, work here next"


@pytest.mark.parametrize("key", ["setup"])
def test_review_plan_title(key):
    s = next(x for x in stages.load_stages() if x["key"] == key)
    assert s["title"] == "Review plan" and s["short"] == "Review plan"


# ------------------------------------------------------------------------------------------- 2 and 3: CLI, routes
def _cli(*args, root):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    return CliRunner().invoke(cli_app, [*args, "--dir", str(root)], env={"COLUMNS": "200", "WIDER_REVIEWER": "ana"})


def _client(p: Project):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    return TestClient(create_app(p))


def test_cli_plan_prints_cards(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 1)
    _seed_question(p)
    r = _cli("plan", root=p.root)
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert lines[0] == "Done · Research question · 1 question saved"
    assert lines[1] == "Waiting for you · Seed papers · 2 seed papers: 1 development, 1 validation; 1 not found"
    assert lines[-1] == "Plan summary: complete the tasks above" and len(lines) == 6


def test_cli_plan_json_matches_route(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 2, 1)
    r = _cli("plan", "--json", root=p.root)
    assert r.exit_code == 0, r.output
    route = _client(p).get("/api/plan").json()
    assert json.loads(r.output)["tasks"] == route["tasks"]
    assert route["summary"] == "complete the tasks above" and route["ready"] is False


def test_api_plan(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    d = _client(p).get("/api/plan").json()
    assert [t["key"] for t in d["tasks"]] == ["question", "seeds", "prior", "databases", "more"]
    assert set(d["tasks"][0]) >= {"key", "title", "optional", "state", "line", "questions"}


def test_api_questions_page_filter(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    _seed_question(p)
    qs.raise_question(p, "proposal_failed", "x", "Proposal", "The proposal failed.")
    c = _client(p)
    kinds = lambda url: [q["kind"] for q in c.get(url).json()["questions"]]  # noqa: E731
    assert kinds("/api/questions?page=setup%3Ftask%3Dseeds") == ["seed_unresolved"]
    assert kinds("/api/questions?page=setup") == ["seed_unresolved"]
    assert kinds("/api/questions?page=construct") == ["proposal_failed"]
    assert kinds("/api/questions?page=query") == []
    assert len(kinds("/api/questions")) == 2
    q = c.get("/api/questions?page=setup").json()["questions"][0]
    assert q["page"] == "setup?task=seeds" and q["anchor"] == "B001"


def test_cli_questions_list_page(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    sid = _seed_question(p)
    pid = qs.raise_question(p, "proposal_failed", "x", "Proposal", "The proposal failed.")
    out = _cli("questions", "list", "--page", "setup", root=p.root).output
    assert f"{sid} seed_unresolved" in out and f"{pid} proposal_failed" not in out
    out = _cli("questions", "list", "--page", "setup?task=seeds", root=p.root).output
    assert f"{sid} seed_unresolved" in out
    out = _cli("questions", "list", "--page", "construct", root=p.root).output
    assert f"{pid} proposal_failed" in out and f"{sid} seed_unresolved" not in out


def test_cli_questions_list_shows_consequences(tmp_path):
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    _seed_question(p)
    out = _cli("questions", "list", root=p.root).output
    assert "doi <value>*" in out or "doi*" in out
    assert "doi (default): The DOI is saved in benchmark.csv; fetch the seed abstracts again to use it." in out
    assert "leave: Seed coverage is counted without this paper." in out


def test_change_after_seed_page_reload(tmp_path):
    """D15: answer a seed question with a DOI, reload the seed page (which syncs benchmark_papers), then undo:
    200 and the question is open again."""
    p = _project(tmp_path)
    _seeds(p, 1, 0)
    qid = _seed_question(p)
    c = _client(p)
    r = c.post(f"/api/questions/{qid}/answer", json={"choice": "doi", "text": "10.1000/right", "by": "ana"}, headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200, r.text
    change = r.json()["change_id"]
    assert c.get("/api/benchmark/seed").status_code == 200
    u = c.post(f"/api/history/{change}/undo", headers={"X-Wider-Author": "ana"})
    assert u.status_code == 200, u.text
    assert [q["question_id"] for q in c.get("/api/questions?page=setup").json()["questions"]] == [qid]


def test_demo_has_open_questions(tmp_path):
    """D15: the synthetic demo (the e2e project the walkthrough and the ui-tester start) leaves two seed questions on
    the Seed papers task and one record question on Abstracts open, raised without an online lookup."""
    from tests.e2e.test_synthetic_pipeline import raise_demo_questions
    from tests.unit.test_dedup import _project_with_imports
    from wider.dedup.engine import run

    p = _project_with_imports(tmp_path)
    run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id=f"B{i}", title=f"Seed title {i}", doi=f"10.9999/syn.{i:03d}",
                                      set="Development" if i <= 4 else "Validation") for i in range(1, 7)])
    assert all(raise_demo_questions(p))
    open_qs = qs.list_questions(p, status="open")
    assert sorted((q["kind"], q["page"]) for q in open_qs) == [("enrich_unresolved", "enrich"),
                                                               ("seed_unresolved", "setup?task=seeds"),
                                                               ("seed_unresolved", "setup?task=seeds")]
    assert next(t for t in plan.tasks(p) if t["key"] == "seeds")["state"] == "waiting"
    assert raise_demo_questions(p) == [None, None, None]   # a rebuilt demo does not ask twice


def test_validation_seed_question_only_on_seed_task(tmp_path):
    """Risk-check R2, scenario "Validation papers are not named on the plan": a seed question about a validation seed
    paper is listed for the Seed papers task, not for the Review plan itself; the setup stage still counts it."""
    p = _project(tmp_path)
    _seeds(p, 1, 1)
    qs.raise_question(p, "seed_unresolved", "B002", "Seed title 2", "No source found an abstract for B002.",
                      context={"doi": "10.9/val", "set": "Validation"}, ref="B002")
    dev = _seed_question(p)
    assert [q["question_id"] for q in qs.list_questions(p, "open", page="setup")] == [dev]
    assert len(qs.list_questions(p, "open", page="setup?task=seeds")) == 2
    assert _state(p, "setup")["needs_you"] == "2 questions for you"
