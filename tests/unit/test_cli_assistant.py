"""guide-assistant 6.1 and 2.4: `wider assistant ...`, the CLI twin of the panel's box (D19): the answer with its
Based on, Show, Read and Answered by lines, the exit codes, `--allow-once`, history, forget, unhelpful and budget.
Synthetic projects and the fake backend only."""
import json

import pytest
from typer.testing import CliRunner

from tests.unit.test_assistant_engine import DEDUP_Q, _answer, _proj
from wider.assistant import ask as A
from wider.assistant import store
from wider.cli import app as cli_app
from wider.core import backend_consent as bc
from wider.core.project import Project

runner = CliRunner()
ENV = {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}


def cli(p, *args, input=None, env=None):
    return runner.invoke(cli_app, ["assistant", *args, "--dir", str(p.root)], input=input, env={**ENV, **(env or {})})


def _needs_consent(monkeypatch, name="b"):
    """Backend ``name`` (a fake) is treated as a network backend that needs consent."""
    orig = bc.assess

    def assess(cfg, embedding=False):
        info = orig(cfg, embedding)
        if cfg.name == name and not embedding:
            info = {**info, "needs": True, "fingerprint": "fp-" + name}
        return info
    monkeypatch.setattr(bc, "assess", assess)


def test_cli_assistant_ask_json(tmp_path):
    """--json prints the engine's dict: the answer, the sources with their commands, the turn number."""
    p = _proj(tmp_path)
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup", "--json")
    assert r.exit_code == 0, r.output
    d = json.loads(r.output)
    assert d["turn"] == 1 and d["answer"] and d["backend"] == "b" and d["answered_by"] == "Answered by b (m-b)"
    assert all(b["command"].startswith("wider guide") for b in d["based_on"])
    assert [t["question"] for t in store.turns(Project.load(p.root), "ana")] == [DEDUP_Q]


def test_cli_assistant_exit_codes(tmp_path):
    """0 answered (also when the documents do not explain it), 1 refused with the spec's sentence."""
    p = _proj(tmp_path)
    r = cli(p, "ask", "What is the capital of France?", "--page", "dedup")
    assert r.exit_code == 0 and A.NO_DOCS in r.output
    assert cli(p, "ask", "  ", "--page", "dedup").exit_code == 1
    r = cli(p, "ask", DEDUP_Q, "--page", "nosuchpage")
    assert r.exit_code == 1 and "there is no such page: nosuchpage" in r.output
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup", env={"WIDER_REVIEWER": "", "USER": ""})
    assert r.exit_code == 1 and "Enter your name first" in r.output
    assert cli(p, "ask", DEDUP_Q, "--page", "dedup", "--depth", "deeper").exit_code == 1
    q = Project.load(p.root)
    q.config.stage_providers.pop("construct")
    q.save()
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup")
    assert r.exit_code == 1 and A.NO_MODEL in " ".join(r.output.split())


def test_cli_allow_once(tmp_path, monkeypatch):
    """Without consent and without a terminal: exit 2, nothing sent or stored, the --allow-once hint. With
    --allow-once the question is answered and neither the consent nor the question and answer is stored."""
    p = _proj(tmp_path)
    _needs_consent(monkeypatch)
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup")
    assert r.exit_code == 2 and "--allow-once" in r.output
    q = Project.load(p.root)
    assert store.turns(q, "ana") == [] and not q.store.execute("SELECT * FROM llm_usage").fetchall()
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup", "--allow-once")
    assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    assert "neither the consent nor this question" in " ".join(r.output.split())
    assert store.turns(q, "ana") == [] and "b" not in q.config.backend_consent
    assert cli(p, "ask", DEDUP_Q, "--page", "dedup").exit_code == 2      # nothing was remembered


def test_cli_send_notice_first(tmp_path):
    """The send-notice sentences come before the answer; --json prints only the JSON."""
    p = _proj(tmp_path)
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup")
    assert r.exit_code == 0
    assert r.output.index("WIDER sends your question") < r.output.index("Based on:")
    assert cli(p, "ask", DEDUP_Q, "--page", "dedup", "--json").output.lstrip().startswith("{")


def test_cli_show_and_source_lines(tmp_path, monkeypatch):
    """Show lines name the button and the page's title; Based on lines carry the command; the answer's brackets are
    printed literally; Read and Answered by close the answer."""
    p = _proj(tmp_path)

    def answer(req):
        first = req.user.split("[", 2)[1].split("]")[0]
        return {"answer": "Press [bold]it[/bold] [methods/x].", "sources": [first],
                "show": [{"page": "dedup", "button": "Run deduplication"}], "drafts": [{"for": "methods", "text": "D."}]}
    _answer(monkeypatch, answer)
    r = cli(p, "ask", DEDUP_Q, "--page", "dedup")
    assert r.exit_code == 0, r.output
    out = r.output
    assert "Press [bold]it[/bold] [methods/x]." in out
    assert "Show: Run deduplication (Deduplication)" in out
    assert "Based on:" in out and ": wider guide" in out
    assert "Draft (methods):" in out and "Read:" in out and "Answered by b (m-b)" in out
    assert "wider assistant unhelpful 1" in out


def test_cli_unreviewed_mark(tmp_path, monkeypatch):
    """A source not reviewed by the author carries the mark on its Based on line."""
    p = _proj(tmp_path)
    monkeypatch.setattr(A, "reviewed", lambda s: False)
    r = cli(p, "ask", "Why is the validation set hidden until the freeze?", "--page", "results")
    assert r.exit_code == 0 and A.UNREVIEWED_MARK in r.output


def test_cli_history_unanswered(tmp_path):
    """history lists the turns; --unanswered only the unmatched and the marked ones; unhelpful marks and --undo
    unmarks; an unknown turn exits 1."""
    p = _proj(tmp_path)
    cli(p, "ask", DEDUP_Q, "--page", "dedup")
    cli(p, "ask", "What is the capital of France?", "--page", "dedup")
    r = cli(p, "history")
    assert r.exit_code == 0 and "1. " in r.output and "2. " in r.output and DEDUP_Q in r.output
    r = cli(p, "history", "--unanswered")
    assert "2. " in r.output and DEDUP_Q not in r.output and "(not answered)" in r.output
    assert cli(p, "unhelpful", "1").exit_code == 0
    r = cli(p, "history", "--unanswered", "--json")
    assert [t["n"] for t in json.loads(r.output)] == [1, 2]
    assert cli(p, "unhelpful", "1", "--undo").exit_code == 0
    assert [t["n"] for t in json.loads(cli(p, "history", "--unanswered", "--json").output)] == [2]
    assert cli(p, "unhelpful", "9").exit_code == 1
    assert cli(p, "history", "--by", "bo").output.strip() == "No conversation yet."


def test_cli_history_docs_changed(tmp_path, monkeypatch):
    """A turn answered before WIDER's documents changed says so in history."""
    p = _proj(tmp_path)
    cli(p, "ask", DEDUP_Q, "--page", "dedup")
    assert A.DOCS_CHANGED not in cli(p, "history").output
    monkeypatch.setattr(store, "docs_changed", lambda t, by_id: True)
    assert A.DOCS_CHANGED in cli(p, "history").output


def test_cli_forget_needs_yes(tmp_path):
    """Without --yes and without a terminal forget exits 2 and keeps the file; --yes deletes only this author's."""
    p = _proj(tmp_path)
    cli(p, "ask", DEDUP_Q, "--page", "dedup")
    cli(p, "ask", DEDUP_Q, "--page", "dedup", "--by", "bo")
    r = cli(p, "forget")
    assert r.exit_code == 2 and "--yes" in r.output
    assert len(store.turns(p, "ana")) == 1
    r = cli(p, "forget", "--yes")
    assert r.exit_code == 0 and "(1 question and answer)" in r.output
    assert store.turns(p, "ana") == [] and len(store.turns(p, "bo")) == 1


def test_cli_budget_set_and_default(tmp_path):
    """budget shows the default by place, sets a number (journalled), refuses one out of range, and --default clears
    it."""
    p = _proj(tmp_path)
    r = cli(p, "budget")
    assert r.exit_code == 0 and "3,000 tokens (the default for the model's place)" in r.output
    r = cli(p, "budget", "8000")
    assert r.exit_code == 0 and "8,000 tokens" in r.output and "default for the model's place is 3,000" in r.output
    assert Project.load(p.root).config.assistant.context_tokens == 8000
    r = cli(p, "budget", "100")
    assert r.exit_code == 1 and "500" in r.output
    assert cli(p, "budget", "9000", "--default").exit_code == 1
    r = cli(p, "budget", "--default")
    assert r.exit_code == 0 and Project.load(p.root).config.assistant.context_tokens is None


@pytest.mark.parametrize("embedding", [True, False])
def test_cli_index(tmp_path, embedding):
    """index builds the meaning index with the fake embedder; without an embedding it exits 1 and says why."""
    p = _proj(tmp_path, embedding=embedding)
    r = cli(p, "index")
    if embedding:
        assert r.exit_code == 0 and "Index built with fe" in r.output
        assert any(p.path("assistant", "index").iterdir())
    else:
        assert r.exit_code == 1 and "No embedding model is set" in r.output


def test_cli_check_retrieval(monkeypatch):
    """check-retrieval prints the share per group and exits 0 on the shipped set; a failing check exits 1; --json
    prints the check's dict. It takes no project."""
    from wider.assistant import evaluate as E

    r = runner.invoke(cli_app, ["assistant", "check-retrieval"], env=ENV)
    assert r.exit_code == 0, r.output
    assert "first-time:" in r.output and "auditor:" in r.output and "0 of 10 matched" in r.output and "OK" in r.output
    d = json.loads(runner.invoke(cli_app, ["assistant", "check-retrieval", "--json"], env=ENV).output)
    assert d["ok"] and set(E.GROUPS) <= set(d["groups"])
    orig = E.check
    monkeypatch.setattr(E, "check", lambda: {**orig(), "ok": False})
    r = runner.invoke(cli_app, ["assistant", "check-retrieval"], env=ENV)
    assert r.exit_code == 1 and "Not OK" in r.output


def test_cli_ask_without_page_is_general(tmp_path):
    """D23: `wider assistant ask` without --page asks about WIDER in general, and history says so."""
    p = _proj(tmp_path)
    r = cli(p, "ask", DEDUP_Q)
    assert r.exit_code == 0, r.output
    assert store.turns(Project.load(p.root), "ana")[-1]["page"] == "general"
    h = cli(p, "history")
    assert h.exit_code == 0 and "about WIDER in general" in h.output
