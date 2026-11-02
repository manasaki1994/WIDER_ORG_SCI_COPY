"""docs/names.md, the map of page names and commands, matches stages.yaml, the fixed sidebar pages of shell.js and
the guide headings, so a renamed page fails until the map follows (accessibility-and-naming, task 6.5)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

from wider.core.stages import load_stages

ROOT = Path(__file__).resolve().parents[2]
NAMES = ROOT / "docs" / "names.md"
GUIDE = ROOT / "docs" / "guide.md"
SHELL = ROOT / "wider" / "ui" / "static" / "shell.js"
STAGES = ROOT / "wider" / "stages.yaml"


def _anchor(heading: str) -> str:
    return re.sub(r"[^\w\- ]", "", heading.lstrip("#").strip().lower()).replace(" ", "-")


def _fixed_pages() -> list[dict]:
    """The FIXED_PAGES list of shell.js: key, title and commands of each sidebar page that is not a stage."""
    src = SHELL.read_text(encoding="utf-8")
    block = src[src.index("const FIXED_PAGES = ["):src.index("];", src.index("const FIXED_PAGES = ["))]
    return [{"key": k, "title": t, "commands": re.findall(r'"([^"]+)"', c)}
            for k, t, c in re.findall(r'\{ key: "([^"]+)", title: "([^"]+)", commands: \[([^\]]*)\] \}', block)]


def _rows(text: str) -> dict[str, list[str]]:
    """Page name -> its cells, from the tables above the glossary."""
    rows = {}
    for line in text[:text.index("## Glossary")].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 6 and cells[0] not in ("Page", "---"):
            rows[cells[0]] = cells
    return rows


def check_names(names: Path = NAMES, stages_file: Path | None = None) -> list[str]:
    """What docs/names.md is missing or gets wrong, one line each; empty when the map matches."""
    stages = load_stages(stages_file) if stages_file else load_stages()
    rows = _rows(names.read_text(encoding="utf-8"))
    anchors = {_anchor(line) for line in GUIDE.read_text(encoding="utf-8").splitlines() if line.startswith("#")}
    specs = {p.name for p in (ROOT / "openspec" / "specs").iterdir()} | {
        p.name for p in (ROOT / "openspec" / "changes").glob("*/specs/*")}
    pages = [(s["title"], s["key"], [] if s.get("status") == "extension" else s.get("commands") or [s["command"]],
              s.get("status") == "extension") for s in stages]  # an extension stage has no command yet
    pages += [(p["title"], p["key"], p["commands"], False) for p in _fixed_pages()]
    problems = []
    for title, key, commands, extension in pages:
        row = rows.get(title)
        if row is None:
            problems.append(f"docs/names.md: no row for {title}")
            continue
        _, address, ident, cmds, guide, spec = row
        if ident != f"`{key}`":
            problems.append(f"docs/names.md: {title} has id {ident}, not `{key}`")
        if address != ("none" if extension else f"`#{key}`"):
            problems.append(f"docs/names.md: {title} has address {address}")
        want = ", ".join(f"`wider {c}`" for c in commands) if commands else "none yet"
        if cmds != want:
            problems.append(f"docs/names.md: {title} lists {cmds}, the page has {want}")
        target = re.fullmatch(r"\[[^\]]+\]\(guide\.md#([^)]+)\)", guide)
        if not target or target.group(1) not in anchors:
            problems.append(f"docs/names.md: {title} names a guide section that does not exist: {guide}")
        if spec not in specs:
            problems.append(f"docs/names.md: {title} names an unknown spec capability {spec}")
    return problems


def test_names_map_matches_pages_commands_and_guide():
    assert len(_fixed_pages()) >= 8
    assert {p["title"] for p in _fixed_pages()} >= {"Home", "Questions", "Notes and tags", "History", "Help",
                                                     "Settings and data", "Model for query creation", "Results dashboard"}
    assert check_names() == []
    text = NAMES.read_text(encoding="utf-8")
    for word in ("Seed papers", "Labelling queue", "Unsure", "Screening round", "Result set", "2 Review decisions"):
        assert word in text[text.index("## Glossary"):], word
    assert "(names.md)" in GUIDE.read_text(encoding="utf-8")
    assert "(docs/names.md)" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_names_map_reports_a_renamed_page(tmp_path):
    data = yaml.safe_load(STAGES.read_text(encoding="utf-8"))
    for st in data["stages"]:
        if st["key"] == "rank":
            st["title"] = "Rank order"
    copy = tmp_path / "stages.yaml"
    copy.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    assert "docs/names.md: no row for Rank order" in check_names(stages_file=copy)
    names = tmp_path / "names.md"  # and a map row that lists the wrong commands is reported too
    shutil.copy(NAMES, names)
    names.write_text(names.read_text(encoding="utf-8").replace("`wider sample`, `wider label`", "`wider sample`"),
                     encoding="utf-8")
    assert any("Samples and labelling lists `wider sample`" in p for p in check_names(names))
