import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "guard", Path(__file__).resolve().parents[2] / "scripts" / "check_no_private_data.py")
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def test_guard_flags_exports_and_strings(tmp_path):
    ris = tmp_path / "x.ris"
    ris.write_text("TY  - JOUR\n")
    assert guard.check(ris)
    csv = tmp_path / "data.csv"
    csv.write_text("title,abstract,doi\nA,B,C\n")
    assert any("abstract" in p for p in guard.check(csv))
    py = tmp_path / "ok.py"
    py.write_text("x = 1\n")
    assert guard.check(py) == []
    bad = tmp_path / "bad.md"
    bad.write_text("see " + str(Path.home()) + "/stuff\n")
    assert guard.check(bad)


def test_guard_flags_topic_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "TOPIC_WORDS", guard.topic_pattern([r"zebra\w*", r"stripe\w*"]))
    py = tmp_path / "topic.py"
    py.write_text("# whether the animal is a zebra\nx = 1\n")
    probs = guard.check(py)
    assert len(probs) == 1 and "topic vocabulary" in probs[0] and "line 1" in probs[0]
    marked = tmp_path / "marked.py"
    marked.write_text("STOP = {'zebra'}  # topic-ok: guard fixture\n")
    assert guard.check(marked) == []
    neutral = tmp_path / "neutral.md"
    neutral.write_text("lenient and strict recall; near-miss texts; cells and anchors; strict and lenient are methodological words\n")
    assert guard.check(neutral) == [], guard.check(neutral)
    exempt = tmp_path / "docs" / "CHANGELOG.md"
    exempt.parent.mkdir()
    exempt.write_text("the case study on zebras\n")
    assert guard.check(exempt) == []
    assert guard.topic_exempt(Path("CHANGELOG.md")) and not guard.topic_exempt(Path("scripts/other.py"))
    assert not guard.topic_exempt(Path("docs/proposal_draft.md"))
    assert not guard.topic_exempt(Path("docs/technical_appendix.md"))
