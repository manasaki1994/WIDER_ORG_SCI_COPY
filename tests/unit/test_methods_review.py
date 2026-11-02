"""The methods topics' fingerprint and ``scripts/methods_review.py`` (methods-explainer D6), on modules written to
``tmp_path``."""
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest
import yaml

from wider.guide import methods as m

TOPIC = """---
id: one
title: One
context: A synthetic topic.
settings: [recall.ci_level]
artifacts: []
pages: [sample]
---

## In short

Plain words.

## Why

More words.

## For auditors

The code is `src/mod.py:weight`.
"""
CODE = '''"""Module docstring."""


def weight(n, k):
    """How heavy a record is."""
    # a comment
    return n / k


def other():
    return 1
'''
GLOSSARY = "# Glossary\n\n## recall\n\nsynonyms: sensitivity\n\nThe share of relevant papers found.\n"
DAY = date(2026, 10, 30)


def _load_script():
    path = Path(__file__).parent.parent.parent / "scripts" / "methods_review.py"
    spec = importlib.util.spec_from_file_location("methods_review", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """A methods folder with one topic and a glossary, and a base folder holding the topic's source."""
    root, base = tmp_path / "methods", tmp_path / "base"
    root.mkdir()
    (base / "src").mkdir(parents=True)
    (root / "one.md").write_text(TOPIC, encoding="utf-8")
    (root / "glossary.md").write_text(GLOSSARY, encoding="utf-8")
    (root / "review.yaml").write_text(yaml.safe_dump({"topics": {"one": {"sources": ["src/mod.py:weight"]}}}),
                                      encoding="utf-8")
    (base / "src" / "mod.py").write_text(CODE, encoding="utf-8")
    m._load.cache_clear()
    m._glossary.cache_clear()
    yield root, base
    m._load.cache_clear()
    m._glossary.cache_clear()


def _review(root: Path) -> dict:
    return yaml.safe_load((root / "review.yaml").read_text(encoding="utf-8"))


def test_fingerprint_ignores_comments_docstrings_layout(tree):
    root, base = tree
    before = m.fingerprint("one", root, base)
    (base / "src" / "mod.py").write_text(
        "def weight(n,   k):\n    return (n / k)   # same code\n\n\ndef other():\n    return 2\n", encoding="utf-8")
    assert m.fingerprint("one", root, base) == before   # docstrings, comments, layout and other functions ignored


def test_fingerprint_changes_with_code(tree):
    root, base = tree
    before = m.fingerprint("one", root, base)
    (base / "src" / "mod.py").write_text(CODE.replace("return n / k", "return n / (k + 1)"), encoding="utf-8")
    assert m.fingerprint("one", root, base) != before


def test_fingerprint_changes_with_default(tree, monkeypatch):
    root, base = tree
    before = m.fingerprint("one", root, base)
    defaults = dict(m._defaults())
    defaults["recall.ci_level"] = 0.9
    monkeypatch.setattr(m, "_defaults", lambda: defaults)
    assert m.fingerprint("one", root, base) != before


def test_stamp_sets_drafted_today(tree):
    root, base = tree
    assert m.stale(root, base) == ["one"]
    m.stamp("one", root, base, today=DAY)
    entry = _review(root)["topics"]["one"]
    assert entry["status"] == "drafted" and entry["date"] == "2026-10-30"
    assert entry["fingerprint"] == m.fingerprint("one", root, base)
    assert m.stale(root, base) == []
    assert m.topic("one", root)["review"]["status"] == "drafted"


def test_stamp_records_python_minor(tree):
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    assert _review(root)["python"] == m.python_minor()


def test_check_skips_on_other_minor(tree):
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    assert m.other_minor(root) is None
    data = _review(root)
    data["python"] = "3.9"
    (root / "review.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    note = m.other_minor(root)
    assert note and "3.9" in note and m.python_minor() in note


def test_stamp_on_new_minor_keeps_review_when_text_unchanged(tree):
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    m.approve("one", "q-1", root, base, today=DAY)
    data = _review(root)
    data["python"] = "3.9"
    (root / "review.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    m.stamp("one", root, base, today=date(2026, 11, 2))
    assert _review(root)["topics"]["one"]["status"] == "reviewed"


def test_stamp_all_on_new_minor_keeps_every_review(tree, capsys):
    """Risk-checker R1: the new minor is decided once, so every unchanged reviewed text keeps its review."""
    root, base = tree
    (root / "two.md").write_text(TOPIC.replace("id: one", "id: two"), encoding="utf-8")
    data = _review(root)
    data["topics"]["two"] = {"sources": ["src/mod.py:weight"]}
    (root / "review.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    m._load.cache_clear()
    m.stamp_all(root, base, today=DAY)
    for t in ("one", "two", m.GLOSSARY):
        m.approve(t, f"q-{t}", root, base, today=DAY)
    data = _review(root)
    data["python"] = "3.9"
    (root / "review.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    assert _load_script().main(["stamp", "--all"], root, base) == 0
    assert capsys.readouterr().out.split("\n")[:3] == ["stamped one: reviewed", "stamped two: reviewed",
                                                       "stamped glossary: reviewed"]
    after = _review(root)
    assert [after["topics"][t]["answer"] for t in ("one", "two")] + [after["glossary"]["answer"]] == [
        "q-one", "q-two", "q-glossary"]
    assert after["python"] == m.python_minor()


def test_stamp_after_text_edit_drafts_and_drops_answer(tree):
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    m.approve("one", "q-1", root, base, today=DAY)
    (root / "one.md").write_text(TOPIC + "\nAn edit.\n", encoding="utf-8")
    m._load.cache_clear()
    assert m.topic("one", root)["review"]["status"] == "drafted"     # the line never vouches for an edited text
    assert m.text_changed(root) == ["one"]
    m.stamp("one", root, base, today=DAY)
    entry = _review(root)["topics"]["one"]
    assert entry["status"] == "drafted" and "answer" not in entry


def test_approve_refuses_text_edited_after_stamp(tree):
    """Risk-checker R2: an edit after the stamp is not what the owner reviewed."""
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    m.stamp(m.GLOSSARY, root, base, today=DAY)
    (root / "one.md").write_text(TOPIC + "\nAn edit.\n", encoding="utf-8")
    (root / "glossary.md").write_text(GLOSSARY + "\n", encoding="utf-8")
    for t in ("one", m.GLOSSARY):
        with pytest.raises(ValueError, match=f"stamp {t}"):
            m.approve(t, "q-1", root, base, today=DAY)
    assert _review(root)["topics"]["one"]["status"] == "drafted"


def test_reapprove_after_edit(tree):
    """guide-assistant D22: a reviewed topic edited reads drafted with edited; an approval before the stamp is refused
    with the stamp command; stamp then approve gives reviewed with the new date and no edited."""
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    m.approve("one", "q-1", root, base, today=DAY)
    (root / "one.md").write_text(TOPIC.replace("Plain words.", "Plainer words."), encoding="utf-8")
    assert m.topic("one", root)["review"] == {"status": "drafted", "date": "2026-10-30", "edited": True}
    with pytest.raises(ValueError, match="stamp one"):
        m.approve("one", "q-2", root, base, today=date(2026, 11, 2))
    m.stamp("one", root, base, today=date(2026, 11, 2))
    m.approve("one", "q-2", root, base, today=date(2026, 11, 2))
    assert m.topic("one", root)["review"] == {"status": "reviewed", "date": "2026-11-02"}


def test_approve_refuses_stale(tree):
    root, base = tree
    m.stamp("one", root, base, today=DAY)
    (base / "src" / "mod.py").write_text(CODE.replace("n / k", "n * k"), encoding="utf-8")
    with pytest.raises(ValueError, match="stamp one"):
        m.approve("one", "q-1", root, base, today=DAY)
    m.stamp("one", root, base, today=DAY)
    m.approve("one", "q-1", root, base, today=DAY)
    entry = _review(root)["topics"]["one"]
    assert (entry["status"], entry["answer"]) == ("reviewed", "q-1")


def test_approve_glossary(tree):
    root, base = tree
    m.stamp(m.GLOSSARY, root, base, today=DAY)
    assert _review(root)["glossary"]["status"] == "drafted"
    m.approve(m.GLOSSARY, "q-2", root, base, today=DAY)
    assert _review(root)["glossary"] == {"status": "reviewed", "date": "2026-10-30", "answer": "q-2",
                                         "text": m.text_hash(m.GLOSSARY, root)}
    assert m.glossary_review(root)["status"] == "reviewed"


def test_script_stale_stamp_approve(tree, capsys):
    root, base = tree
    script = _load_script()
    assert script.main(["stale"], root, base) == 0
    assert "one" in capsys.readouterr().out.split()
    assert script.main(["stamp", "--all"], root, base) == 0
    assert "stamped glossary" in capsys.readouterr().out
    assert script.main(["stale"], root, base) == 0
    assert "no methods topic is stale" in capsys.readouterr().out
    assert script.main(["approve", "one", "--answer", "q-3"], root, base) == 0
    assert script.main(["approve", "nope", "--answer", "q-3"], root, base) == 1
    assert "there is no such methods topic: nope" in capsys.readouterr().err
