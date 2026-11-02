"""The interactive demo's privacy scrub (openspec change interactive-demo, design D10).

No flagged word is written in this file: the one the tests need is read at run time from the privacy guard's list.
"""
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "showcase" / "demo"))
spec = importlib.util.spec_from_file_location("demo_scrub", REPO / "showcase" / "demo" / "scrub.py")
scrub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scrub)


def _flagged_word() -> str:
    """The first word of the guard's list, as a plain word (its ``\\w*`` and ``?`` endings dropped)."""
    first = scrub.guard.TOPIC_WORDS.pattern.split("(", 1)[1].split("|", 1)[0]
    word = re.sub(r"\\w\*|\?|\[.*?\]", "", first)
    assert scrub.guard.TOPIC_WORDS.fullmatch(word), word
    return word


def test_hidden_words_keep_counts(monkeypatch):
    if scrub.guard.TOPIC_WORDS is None:   # no private word list here: test with an invented one
        monkeypatch.setattr(scrub.guard, "TOPIC_WORDS", scrub.guard.topic_pattern(["zebra\\w*", "okapi"]))
    word = _flagged_word()
    answer = {
        "decision": "include",
        "reason": f"The study measures {word} differences in speaking up.",
        "counts": {"include": 3, "exclude": 1, "maybe": 2},
        "quotes": [f"{word.title()} gaps", "no other words"],
    }
    text = json.dumps(answer)
    hidden = scrub.hide_flagged(text)
    assert not scrub.guard.topic_hits(hidden)
    back = json.loads(hidden)
    assert back["counts"] == answer["counts"] and back["decision"] == "include"
    assert back["reason"] == f"The study measures {scrub.MARKER} differences in speaking up."
    assert back["quotes"] == [f"{scrub.MARKER} gaps", "no other words"]
    assert scrub.hide_flagged("a neutral sentence") == "a neutral sentence"


def test_scrub_rewrites_paths_and_name():
    user = "someone"
    home = "/" + "Users/" + user
    text = (f"{home}/Desktop/x/reviews/employee-voice/exports/a.svg and /private/var/folders/ab/T/other/file.json "
            f"by {user}; app placeholder /Users/you stays")
    out = scrub.scrub(text, user)
    assert f"{scrub.PROJECT}/exports/a.svg" in out
    assert "~/file.json" in out
    assert f"by {scrub.AUTHOR};" in out
    assert "/Users/you stays" in out
    assert scrub.problems(out, user) == []
    assert "a /Users/ path" in scrub.problems(text, user)
    assert "the login name" in scrub.problems(text, user)
    assert scrub.problems("key sk-" + "a" * 24, user) == ["a key-like string"]


def test_build_refuses_forbidden_name(tmp_path, monkeypatch):
    """D13: a name set through WIDER_DEMO_FORBIDDEN_NAMES is refused as a whole word in any case, in the bundle and in
    the site's own files next to it (here a caption track)."""
    monkeypatch.setenv("WIDER_DEMO_FORBIDDEN_NAMES", "Quillon Vetch")
    names = scrub.forbidden_names()
    assert {"Quillon Vetch", "Quillon", "Vetch"} <= set(names)
    assert "a forbidden name (D13: the demo holds no person's name but Reviewer A)" in scrub.problems(
        '{"author": "QUILLON"}', "nobody-here")
    spec_b = importlib.util.spec_from_file_location("demo_build_d13", REPO / "showcase" / "demo" / "build_demo.py")
    build_demo = importlib.util.module_from_spec(spec_b)
    spec_b.loader.exec_module(build_demo)
    site = tmp_path / "site"
    site.mkdir()
    (site / "wider-showcase.captions.vtt").write_text("WEBVTT\n\n00:00.000 --> 00:02.000\nFilmed by vetch.\n")
    import pytest
    with pytest.raises(build_demo.BuildError, match=r"wider-showcase\.captions\.vtt: a forbidden name"):
        build_demo.build(site / "demo")
    assert not (site / "demo").exists()


def test_ordinary_word_not_refused(monkeypatch):
    """A forbidden name inside a longer word, a short part, Reviewer A and an unset source refuse nothing."""
    monkeypatch.setenv("WIDER_DEMO_FORBIDDEN_NAMES", "Quillon-Vetch, Al")
    names = scrub.forbidden_names()
    assert "Al" not in names and "Reviewer A" not in names
    assert scrub.name_hits("Quillons and vetchling grow; Al went home. Reviewer A decided.", names) == []
    monkeypatch.setenv("WIDER_DEMO_FORBIDDEN_NAMES", "")
    monkeypatch.delenv("USER", raising=False)
    assert all(n.strip() for n in scrub.forbidden_names())


def test_email_local_part_splits_a_joined_name(monkeypatch):
    """A git name written as one word ("quillonvetch") hides its surname; the e-mail's local part, split on dots and
    digits, gives the parts (risk check R1). The domain and short digit runs are not names."""
    import subprocess
    answers = {"user.name": "quillonvetch\n", "user.email": "quillon.vetch@example.org\n"}
    monkeypatch.setattr(scrub.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 0, answers.get(cmd[-1], ""), ""))
    monkeypatch.setenv("USER", "")
    monkeypatch.setenv("WIDER_DEMO_FORBIDDEN_NAMES", "")
    names = scrub.forbidden_names()
    assert {"quillonvetch", "quillon", "vetch"} <= set(names)
    assert not {"example", "org", "94"} & set(names)
    assert scrub.name_hits("signed M. Vetch", names) == ["vetch"]
