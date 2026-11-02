"""The methods topics stay in step with the code (methods-explainer D6, checks 1–5): each check runs on the shipped
topics (``shipped_corpus``) and has a fixture test that makes it fail and name the topic, module or key."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.unit.test_methods import _numeric_choice_flag
from wider.guide import methods as m

FIXTURE = Path(__file__).parent.parent / "fixtures" / "methods"
SHIPPED_READY = True   # task 2.8 switches the shipped-corpus checks on
shipped_corpus = pytest.mark.skipif(not SHIPPED_READY, reason="shipped methods topics are switched on by task 2.8")
PACKAGES = ("wider/recall", "wider/sample", "wider/rank", "wider/dedup", "wider/screen", "wider/embeddings")
EXTRA_MODULES = ("wider/core/matching.py", "wider/construct/benchmark.py", "wider/construct/seed.py",
                 "wider/construct/propose.py", "wider/construct/artifacts.py", "wider/core/backend_consent.py",
                 "wider/core/send_notice.py", "wider/core/stages.py", "wider/search/evaluate.py",
                 "wider/search/diagnostics.py", "wider/search/coverage.py")
SECTIONS = ("identification", "dedup", "corpus", "ranking", "sampling", "screening", "recall")
CODE_REF = re.compile(r"`([\w./-]+\.py)(?::([A-Za-z_]\w*))?`")


# ----------------------------------------------------------------------------------------------- the checks
def _review(root: Path) -> dict:
    return yaml.safe_load((root / m.REVIEW).read_text(encoding="utf-8")) or {}


def _sources(root: Path) -> dict[str, list[str]]:
    return {t: list((e or {}).get("sources") or []) for t, e in (_review(root).get("topics") or {}).items()}


def _tracked(base: Path) -> set[str] | None:
    """The files git tracks under ``base`` (``None`` outside a git checkout, where every file counts)."""
    try:
        out = subprocess.run(["git", "ls-files"], cwd=base, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(out.splitlines()) or None


def fingerprint_problems(root: Path, base: Path | None = None) -> list[str]:
    """Check 1: every topic's stored fingerprint matches its code and defaults."""
    return [f"methods topic {t} no longer matches its code or defaults: update its text, then run "
            f"python3 scripts/methods_review.py stamp {t}" for t in m.stale(root, base)]


def text_problems(root: Path) -> list[str]:
    """Check 1b (risk-checker R2): a topic or the glossary marked reviewed still has the text the owner reviewed."""
    return [f"methods {t} is marked reviewed but its file changed after the review: run "
            f"python3 scripts/methods_review.py stamp {t} and ask the owner again" for t in m.text_changed(root)]


def module_problems(root: Path, base: Path, packages=PACKAGES, extra=EXTRA_MODULES) -> list[str]:
    """Check 2: every module of the method packages (and ``extra``) is a source of some topic or in
    ``outside.modules``."""
    modules = {p.relative_to(base).as_posix() for pkg in packages for p in (base / pkg).glob("*.py")
               if p.name != "__init__.py"} | {x for x in extra if (base / x).is_file()}
    tracked = _tracked(base)
    if tracked is not None:      # another chat's uncommitted module is placed by that chat's change (D11)
        modules &= tracked
    named = {ref.partition(":")[0] for refs in _sources(root).values() for ref in refs}
    outside = set((_review(root).get("outside") or {}).get("modules") or {})
    return [f"{mod} is described by no methods topic: add it to a topic's sources in review.yaml, or to "
            "outside.modules with a reason" for mod in sorted(modules - named - outside)]


def setting_problems(root: Path, sections=SECTIONS) -> list[str]:
    """Check 3: every numeric, choice or flag key of the method sections is in some topic's settings or in
    ``outside.settings``."""
    keys = {k for k in _numeric_choice_flag() if k.split(".")[0] in sections}
    listed = {k for t in m.topics(root) for k in t["settings"]}
    outside = set((_review(root).get("outside") or {}).get("settings") or {})
    return [f"the setting {k} is listed by no methods topic: add it to a topic's settings, or to outside.settings "
            "with a reason" for k in sorted(keys - listed - outside)]


def auditor_ref_problems(root: Path, base: Path | None = None) -> list[str]:
    """Check 4: every code reference in a "For auditors" layer resolves."""
    out = []
    for t in m.topics(root):
        text = next(x["text"] for x in m.topic(t["id"], root)["layers"] if x["layer"] == "auditors")
        for path, name in CODE_REF.findall(text):
            why = m.resolve(f"{path}:{name}" if name else path, base)
            if why:
                out.append(f"methods topic {t['id']}, For auditors: {why}")
    return out


def source_problems(root: Path, base: Path | None = None) -> list[str]:
    """Check 5: every source in review.yaml resolves."""
    return [f"methods topic {t}, sources: {why}" for t, refs in _sources(root).items() for ref in refs
            if (why := m.resolve(ref, base))]


# --------------------------------------------------------------------------------------------- fixture tests
def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "methods"
    shutil.copytree(FIXTURE, root)
    return root


def _set_review(root: Path, data: dict) -> None:
    (root / m.REVIEW).write_text(yaml.safe_dump(data), encoding="utf-8")
    m._load.cache_clear()


def test_fixture_passes_every_check(tmp_path):
    root = _copy(tmp_path)
    for t in ("alpha", "beta"):
        m.stamp(t, root)
    assert fingerprint_problems(root) == []
    assert auditor_ref_problems(root) == []
    assert source_problems(root) == []


def test_fingerprint_check_names_topic(tmp_path):
    root = _copy(tmp_path)
    m.stamp("alpha", root)
    problems = fingerprint_problems(root)       # beta was never stamped
    assert len(problems) == 1 and "methods topic beta" in problems[0] and "stamp beta" in problems[0]


def test_text_check_names_topic(tmp_path):
    root = _copy(tmp_path)
    assert text_problems(root) == []                 # alpha is reviewed with its text hash
    (root / "alpha.md").write_text((root / "alpha.md").read_text(encoding="utf-8") + "\nAn edit.\n", encoding="utf-8")
    m._load.cache_clear()
    problems = text_problems(root)
    assert len(problems) == 1 and "methods alpha" in problems[0] and "stamp alpha" in problems[0]
    assert m.topic("alpha", root)["review"]["status"] == "drafted"


def test_module_check_names_module(tmp_path):
    root, base = _copy(tmp_path), tmp_path / "base"
    (base / "pkg").mkdir(parents=True)
    for name in ("__init__.py", "a.py", "b.py", "c.py"):
        (base / "pkg" / name).write_text("x = 1\n", encoding="utf-8")
    (base / "extra.py").write_text("y = 1\n", encoding="utf-8")
    data = _review(root)
    data["topics"]["alpha"]["sources"] = ["pkg/a.py:x"]
    data["outside"] = {"modules": {"pkg/b.py": "layout, no method"}, "settings": {}}
    _set_review(root, data)
    assert module_problems(root, base, ("pkg",), ("extra.py", "gone.py")) == [
        f"{mod} is described by no methods topic: add it to a topic's sources in review.yaml, or to outside.modules "
        "with a reason" for mod in ("extra.py", "pkg/c.py")]


def test_setting_check_names_key(tmp_path):
    root = _copy(tmp_path)
    problems = setting_problems(root, ("recall",))
    assert any("recall.target" in p for p in problems)
    assert not any("recall.ci_level " in p for p in problems)          # listed by alpha
    data = _review(root)
    data["outside"]["settings"] = {p.split()[2]: "test" for p in problems if "recall.target" not in p}
    _set_review(root, data)
    assert setting_problems(root, ("recall",)) == [
        "the setting recall.target is listed by no methods topic: add it to a topic's settings, or to "
        "outside.settings with a reason"]


def test_auditor_ref_check_names_topic(tmp_path):
    root = _copy(tmp_path)
    md = root / "beta.md"
    md.write_text(md.read_text(encoding="utf-8").replace("method_b.py:weight", "method_b.py:gone"), encoding="utf-8")
    m._load.cache_clear()
    assert auditor_ref_problems(root) == [
        "methods topic beta, For auditors: tests/fixtures/methods/src/method_b.py has no top-level gone"]


def test_source_check_names_topic(tmp_path):
    root = _copy(tmp_path)
    data = _review(root)
    data["topics"]["alpha"]["sources"] = ["wider/recall/no_such_module.py", "wider/recall/estimate.py:gone"]
    _set_review(root, data)
    assert source_problems(root) == ["methods topic alpha, sources: wider/recall/no_such_module.py does not exist",
                                     "methods topic alpha, sources: wider/recall/estimate.py has no top-level gone"]


# --------------------------------------------------------------------------------------------- shipped corpus
@shipped_corpus
def test_shipped_fingerprints_current():
    note = m.other_minor()
    if note:
        pytest.skip(note)
    assert fingerprint_problems(m.HERE) == []


@shipped_corpus
def test_shipped_reviewed_texts_unchanged():
    assert text_problems(m.HERE) == []


@shipped_corpus
def test_shipped_modules_covered():
    assert module_problems(m.HERE, m.BASE) == []


@shipped_corpus
def test_shipped_settings_covered():
    assert setting_problems(m.HERE) == []


@shipped_corpus
def test_shipped_auditor_refs_resolve():
    assert auditor_ref_problems(m.HERE) == []


@shipped_corpus
def test_shipped_sources_resolve():
    assert source_problems(m.HERE) == []
