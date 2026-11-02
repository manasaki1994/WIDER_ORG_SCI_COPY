"""guide-assistant 5.3: Ask WIDER's conversations, one file per author in the project's ``assistant/`` folder (D14,
D16), never in a share bundle or replication package. Synthetic projects and the fake backend only."""
import os
import stat
import sys
import zipfile

import pytest

from tests.unit.test_assistant_engine import DEDUP_Q, _proj
from wider.assistant import ask as A
from wider.assistant import store
from wider.core.project import PROJECT_GITIGNORE, Project
from wider.providers import fake

Q = "How does deduplication treat synthetic-secret-question records?"


def _turn(q="q", **kw):
    return {"question": q, "answer": "a", "sources": [], "docs": {"x": "h"}, "unhelpful": False, **kw}


def test_store_per_author(tmp_path):
    """Each author has a file of their own, numbered from 1; case and spacing do not make another author (M124:
    "Reviewer" and "reviewer" went to two files)."""
    p = _proj(tmp_path)
    assert store.append(p, "Ana", _turn("one")) == 1
    assert store.append(p, "Ana", _turn("two")) == 2
    assert store.append(p, " ana ", _turn("three")) == 3
    assert store.conversation_path(p, "Ana") == store.conversation_path(p, "ana") != store.conversation_path(p, "Bo")
    assert store.conversation_path(p, "Ana").parent == p.path("assistant")
    assert [(t["n"], t["question"]) for t in store.turns(p, "ANA")] == [(1, "one"), (2, "two"), (3, "three")]
    assert store.turns(p, "Bo") == [] and store.turns(p, "") == []
    r = A.ask(p, "dedup", DEDUP_Q, "Cy")
    assert r["turn"] == 1 and [t["question"] for t in A.history(p, "Cy")] == [DEDUP_Q]
    assert A.history(p, "Ana")[0]["question"] == "one"


def test_store_folds_files_kept_by_exact_name(tmp_path):
    """M124: files written before the key ignored case ("Reviewer" and "reviewer" apart, the hand-check copy's two
    files) are folded into one conversation in the order the questions were asked, the next time either
    name is used; a file whose turns were asked by another name with the same file-name start stays apart."""
    import hashlib
    import json

    p = _proj(tmp_path)
    d = p.path("assistant")
    d.mkdir()

    def old(name, rows, slug="reviewer"):         # the file name the code before M124 gave the exact name
        f = d / f"{slug}-{hashlib.sha256(name.encode()).hexdigest()[:6]}.jsonl"
        f.write_text("".join(json.dumps({**_turn(q), "author": name, "at": at}) + "\n" for q, at in rows))
        return f
    a = old("Reviewer", [("first", "2026-10-31T13:00:00Z"), ("third", "2026-10-31T13:20:00Z")])
    b = old("reviewer ", [("second", "2026-10-31T13:10:00Z")])
    other = old("Reviewer!", [("not hers", "2026-10-31T13:05:00Z")])     # slug "reviewer" too, another name
    assert [t["question"] for t in store.turns(p, "reviewer")] == ["first", "second", "third"]
    assert not a.exists() and not b.exists() and other.exists()
    assert store.append(p, "REVIEWER", _turn("fourth")) == 4
    assert store.mark_unhelpful(p, "Reviewer", 2)["question"] == "second"
    assert [t["question"] for t in store.turns(p, "Reviewer!")] == ["not hers"]
    assert store.forget(p, "reviewer") == 4 and store.conversation_path(p, "Reviewer!").exists()   # moved to its own key


def test_forget_only_own(tmp_path):
    """Forget deletes this author's conversation only, and says how many turns it held."""
    p = _proj(tmp_path)
    store.append(p, "Ana", _turn("one"))
    store.append(p, "Ana", _turn("two"))
    store.append(p, "Bo", _turn("three"))
    assert store.forget(p, "Ana") == 2
    assert store.turns(p, "Ana") == [] and not store.conversation_path(p, "Ana").exists()
    assert [t["question"] for t in store.turns(p, "Bo")] == ["three"]
    assert store.forget(p, "Ana") == 0


def test_gitignore_has_assistant_new_and_old_project(tmp_path):
    """A new project's .gitignore lists assistant/; an older one gains the line at the first question with its other
    lines kept; a project without a .gitignore gets none."""
    assert "assistant/" in PROJECT_GITIGNORE.splitlines()
    new = _proj(tmp_path / "new")
    assert "assistant/" in new.path(".gitignore").read_text(encoding="utf-8").splitlines()
    old = _proj(tmp_path / "old")
    old.path(".gitignore").write_text("inputs/\nmy-own-line", encoding="utf-8")
    A.ask(old, "dedup", DEDUP_Q, "ana")
    assert old.path(".gitignore").read_text(encoding="utf-8") == "inputs/\nmy-own-line\nassistant/\n"
    A.ask(old, "dedup", DEDUP_Q, "ana")
    assert old.path(".gitignore").read_text(encoding="utf-8").count("assistant/") == 1
    none = _proj(tmp_path / "none")
    none.path(".gitignore").unlink()
    A.ask(none, "dedup", DEDUP_Q, "ana")
    assert not none.path(".gitignore").exists()


@pytest.mark.parametrize("failure", ["malformed", "unreachable", "no_model"])
def test_no_turn_on_failure(tmp_path, monkeypatch, failure):
    """A refused question stores nothing: no file, and an earlier conversation is unchanged."""
    p = _proj(tmp_path)
    A.ask(p, "dedup", DEDUP_Q, "ana")
    before = store.conversation_path(p, "ana").read_bytes()
    if failure == "malformed":
        monkeypatch.setattr(fake, "_assistant_answer", lambda req: "not json")
    elif failure == "unreachable":
        async def down(self, req):
            raise RuntimeError("down")
        monkeypatch.setattr(fake.FakeProvider, "_call", down)
    else:
        p.config.stage_providers.pop("construct")
        p.save()
    for author in ("ana", "bo"):
        with pytest.raises(A.Refusal):
            A.ask(p, "dedup", DEDUP_Q, author)
    assert store.conversation_path(p, "ana").read_bytes() == before
    assert not store.conversation_path(p, "bo").exists()


def test_unanswered_lists_unmatched_and_marked(tmp_path):
    """Unanswered lists a question no section matched and one marked "Didn't help"; unmarking takes it off."""
    p = _proj(tmp_path)
    A.ask(p, "dedup", DEDUP_Q, "ana")
    A.ask(p, "dedup", "What is the capital of France?", "ana")
    A.ask(p, "results", "Why is the validation set hidden until the freeze?", "ana")
    assert [t["n"] for t in store.unanswered(p, "ana")] == [2]
    store.mark_unhelpful(p, "ana", 3)
    assert [(t["n"], t["question"][:4]) for t in store.unanswered(p, "ana")] == [(2, "What"), (3, "Why ")]
    store.mark_unhelpful(p, "ana", 3, False)
    assert [t["n"] for t in store.unanswered(p, "ana")] == [2]
    with pytest.raises(KeyError):
        store.mark_unhelpful(p, "ana", 4)
    assert store.unanswered(p, "bo") == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_conversation_file_mode(tmp_path):
    """The conversation file is readable and writable by the owner only, after an append and after a rewrite."""
    p = _proj(tmp_path)
    A.ask(p, "dedup", DEDUP_Q, "ana")
    f = store.conversation_path(p, "ana")
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    store.mark_unhelpful(p, "ana", 1)
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_assistant_not_in_packages(tmp_path):
    """After a question (and the meaning index, with the fake embedder), neither the share bundle nor the replication
    package holds an assistant/ file, the question or the answer."""
    from tests.unit.test_package import _project
    from wider.core.models import EmbeddingConfig, ProviderConfig
    from wider.recall.outputs import write_package
    from wider.recall.report import share_bundle
    from wider.screen.devgate import DevSampleIncomplete

    p = _project(tmp_path)
    p.config.providers.append(ProviderConfig(name="b", kind="fake", model="m-b"))
    p.config.stage_providers["construct"] = "b"
    p.config.embeddings.append(EmbeddingConfig(name="fe", kind="fake", model="fake", dim=32))
    p.config.default_embedding = "fe"
    p.save()
    p = Project.load(p.root)
    r = A.ask(p, "dedup", Q, "ana")
    assert r["retrieval"] == "keywords and meaning"
    assert store.conversation_path(p, "ana").exists() and any(p.path("assistant", "index").iterdir())
    answer = r["answer"]

    bundle = share_bundle(p, tmp_path / "bundle")
    for f in bundle.rglob("*"):
        assert "assistant" not in f.relative_to(bundle).parts
        if f.is_file():
            text = f.read_bytes().decode("utf-8", "replace")
            assert "synthetic-secret-question" not in text and answer not in text, f
    try:
        z = zipfile.ZipFile(p.root / write_package(p)["path"])
    except DevSampleIncomplete:
        pytest.fail("the synthetic project should allow the replication package")
    for n in z.namelist():
        assert not n.startswith("assistant/") and "/assistant/" not in n
        text = z.read(n).decode("utf-8", "replace")
        assert "synthetic-secret-question" not in text and answer not in text, n


def test_unhelpful_and_append_lose_no_turn(tmp_path):
    """Risk R3: "Didn't help" rewriting the file while turns are appended loses none of them (one lock per file)."""
    import threading

    p = _proj(tmp_path)
    for i in range(3):
        store.append(p, "ana", _turn(f"q{i}"))
    stop = threading.Event()

    def marker():
        while not stop.is_set():
            store.mark_unhelpful(p, "ana", 1)
    t = threading.Thread(target=marker)
    t.start()
    try:
        for i in range(200):
            store.append(p, "ana", _turn(f"a{i}"))
    finally:
        stop.set()
        t.join()
    assert len(store.turns(p, "ana")) == 203


def test_forget_during_question_is_kept(tmp_path, monkeypatch):
    """Risk R4: a conversation forgotten while a question is being answered stays forgotten: the answer is not stored
    and says why; a first question (no conversation yet) is stored as usual."""
    p = _proj(tmp_path)
    A.ask(p, "dedup", DEDUP_Q, "ana")
    orig = store.append

    def forget_then_append(project, author, turn, since=None):
        store.forget(project, author)
        return orig(project, author, turn, since=since)
    monkeypatch.setattr(store, "append", forget_then_append)
    r = A.ask(p, "dedup", DEDUP_Q, "ana")
    assert r["turn"] == 0 and A.FORGOTTEN in r["notes"]
    assert store.turns(p, "ana") == [] and not store.conversation_path(p, "ana").exists()
    monkeypatch.setattr(store, "append", orig)
    assert A.ask(p, "dedup", DEDUP_Q, "ana")["turn"] == 1
