"""The documentation stays in step with the code: every stage has a guide section, every command the guide
names exists, and the generated protocol reference matches the ReviewProtocol field descriptions."""
from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

from wider.cli import app
from wider.core.protocol import ReviewProtocol
from wider.core.stages import load_stages

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "guide.md"
REFERENCE = ROOT / "docs" / "protocol_reference.md"
FEATURES = ROOT / "docs" / "features.md"
BUILD_SCRIPT = ROOT / "scripts" / "build_docs.py"
SPECS = ROOT / "openspec" / "specs"


def _headings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("#")]


def _cli_names() -> tuple[dict[str, set[str]], set[str]]:
    """Sub-app name -> its command names, and the top-level command names, from typer's registry."""
    groups = {g.name: {c.name or c.callback.__name__ for c in g.typer_instance.registered_commands}
              | {sub.name for sub in g.typer_instance.registered_groups}
              for g in app.registered_groups}
    commands = {c.name or c.callback.__name__ for c in app.registered_commands}
    return groups, commands


OLD_NAMES = ("Construct workbench", "Query generation", "Anchor ranking", "Abstract enrichment", "LLM screening",
             "Sampling and labelling")


def _anchor(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.lstrip("#").strip().lower()).replace(" ", "-")


def test_guide_headings_follow_the_page_names():
    text = GUIDE.read_text(encoding="utf-8")
    lines = text.splitlines()
    numbered = [(i, line) for i, line in enumerate(lines) if re.match(r"^## \d+\. ", line)]
    stages = [st for st in load_stages() if st.get("status") != "extension"]
    assert [line for _, line in numbered] == [f"## {n}. {st['title']}" for n, st in enumerate(stages, 1)] + [
        "## 11. Extension points"]
    for (i, _), st in zip(numbered, stages):  # the "For scripts:" line names the stage's commands
        follow = next(line for line in lines[i + 1:] if line.strip())
        cmds = st.get("commands") or [st["command"]]
        assert follow == "For scripts: " + ", ".join(f"`wider {c}`" for c in cmds) + ".", follow
    ext = next(line for line in lines[numbered[-1][0] + 1:] if line.strip())
    assert ext.startswith("For scripts:") and all(f"`{k}`" in ext for k in ("fulltext", "extract", "synth"))
    for line in _headings(text):  # no heading carries a command or an old page name
        assert "`" not in line and not any(old in line for old in OLD_NAMES), line
    anchors = {_anchor(line) for line in lines if line.startswith("#")}  # an old anchor has no heading any more
    for doc in [*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md"]:
        body = doc.read_text(encoding="utf-8")
        for target in re.findall(r"\]\((?:docs/)?guide\.md#([^)]+)\)", body) + (
                re.findall(r"\]\(#([^)]+)\)", body) if doc == GUIDE else []):
            assert target in anchors, f"{doc.name}: no guide heading for #{target}"


def test_guide_names_only_real_commands():
    groups, commands = _cli_names()
    text = GUIDE.read_text(encoding="utf-8")
    mentions = re.findall(r"\bwider ([a-z][a-z-]*)(?:[ \t]+([a-z][a-z-]*))?", text)
    assert mentions, "the guide names no commands"
    for first, second in mentions:
        assert first in groups or first in commands, f"`wider {first}` is not a CLI command or sub-app"
        if first in groups and second:
            assert second in groups[first], f"`wider {first} {second}` is not a command of the {first} sub-app"
    for name in groups:  # every sub-app is documented at least once
        assert f"wider {name}" in text, f"sub-app `wider {name}` is not mentioned in the guide"


def test_guide_covers_every_protocol_section():
    text = GUIDE.read_text(encoding="utf-8")
    for section in ReviewProtocol.model_fields:
        assert f"`{section}" in text, f"protocol section {section} is not referenced in the guide"


def test_protocol_reference_is_current():
    text = REFERENCE.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.startswith("<!--"))
    assert body.strip() == ReviewProtocol().markdown().strip(), "run python scripts/build_docs.py"


def test_build_docs_script_regenerates_reference(tmp_path):
    spec = importlib.util.spec_from_file_location("build_docs", BUILD_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.build_protocol_reference(tmp_path / "protocol_reference.md")
    assert out.read_text(encoding="utf-8") == REFERENCE.read_text(encoding="utf-8")


def test_feature_file_names_every_stage():
    text = FEATURES.read_text(encoding="utf-8")
    for st in load_stages():
        assert f"`{st['key']}`" in text, f"docs/features.md does not name stage `{st['key']}`"


def _spec_texts() -> dict[str, str]:
    return {p.parent.name: p.read_text(encoding="utf-8") for p in sorted(SPECS.glob("*/spec.md"))}


def test_feature_file_links_every_spec():
    specs = _spec_texts()
    assert specs, "openspec/specs/ holds no capability specs"
    text = FEATURES.read_text(encoding="utf-8")
    for name in specs:
        assert f"../openspec/specs/{name}/spec.md" in text, f"docs/features.md does not link spec `{name}`"
    for name in re.findall(r"\.\./openspec/specs/([a-z0-9-]+)/spec\.md", text):
        assert name in specs, f"docs/features.md links missing spec `{name}`"


def test_specs_name_every_stage_and_sub_app():
    # an active change's delta specifies a new sub-app until the change is archived into the main specs
    deltas = [p.read_text(encoding="utf-8") for p in sorted((ROOT / "openspec" / "changes").glob("*/specs/*/spec.md"))]
    joined = "\n".join([*_spec_texts().values(), *deltas])
    for st in load_stages():
        if st.get("status") != "available":  # extension points are not specified yet
            continue
        assert f"`{st['key']}`" in joined, f"no spec names stage `{st['key']}`"
    groups, _ = _cli_names()
    for name in groups:
        assert f"wider {name}" in joined, f"no spec names `wider {name}`"


def test_specs_validate_strictly():
    exe = shutil.which("openspec")
    if exe is None:
        import pytest
        pytest.skip("openspec CLI not installed")
    out = subprocess.run([exe, "validate", "--specs", "--strict", "--no-interactive"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
