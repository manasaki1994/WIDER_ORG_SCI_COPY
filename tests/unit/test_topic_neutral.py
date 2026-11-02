"""The prompt templates carry no vocabulary of the original review: rendered with a neutral fixture project, none of
the topic words the privacy guard lists appear in any construct prompt or in the screening prompt."""
import importlib.util
from pathlib import Path

import pytest

from wider.construct import artifacts
from wider.construct.benchmark import sync
from wider.construct.propose import build_prompt
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.screen.codebook import build

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("guard", ROOT / "scripts" / "check_no_private_data.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

TEMPLATE_KINDS = [k for k in artifacts.KINDS if (ROOT / "wider" / "construct" / "templates" / f"{k}.md.j2").exists()]

ABSTRACT = ("We study when employees speak up to their supervisors with suggestions and concerns. In a field survey of "
            "teams in three service firms, psychological safety and supervisor openness predicted voice, while fear of "
            "career consequences predicted silence. Voice was followed by more process improvements.")
CELLS = {"construct": "Employee voice and silence",
         "dimensions": [{"name": "form", "values": [{"key": "voice", "label": "Voice", "definition": "speaking up with suggestions or concerns"},
                                                    {"key": "silence", "label": "Silence", "definition": "withholding relevant input"}]},
                        {"name": "focus", "values": [{"key": "antecedent", "label": "Antecedent", "definition": "what leads to it"},
                                                     {"key": "consequence", "label": "Consequence", "definition": "what follows from it"}]}],
         "cells": [{"key": "voice_antecedent", "labels": {"form": "voice", "focus": "antecedent"}, "question": "What leads employees to speak up?"},
                   {"key": "silence_consequence", "labels": {"form": "silence", "focus": "consequence"}, "question": "What follows employee silence?"}],
         "not_a_criterion": ["the industry of the sample"], "benchmark_ids": ["B1"]}
CODEBOOK = {"question": "What leads employees to speak up or stay silent at work, and what follows?",
            "target_description": "Empirical studies of employee voice or silence directed at a party who could act on it.",
            "elements": [{"id": "e1", "name": "Voice or silence measured", "rule": "Speaking up or withholding input is measured or manipulated."},
                         {"id": "e2", "name": "Directed at someone who could act", "rule": "The message goes to a supervisor, management, peers or an outside body."}],
            "exclusion_reasons": [{"code": "a", "text": "no voice or silence measured", "element": "e1"}, {"code": "e", "text": "not empirical"}],
            "adjacent_literatures": [{"name": "works councils", "strains_element": "e2", "decision": "maybe", "reason_code": "a",
                                      "note": "include only when individual speaking up is measured"}],
            "checks": [{"key": "voice_subscale_only", "question": "Is voice only one subscale of a broader scale?", "if_yes": "maybe"}],
            "edge_cases": [{"key": "non_empirical", "case": "Reviews and essays on the topic", "default": "maybe"}],
            "labels": [{"group": "target", "values": ["supervisor", "senior_management", "peers"]}]}
ANCHORS = {"style": "aims only", "anchors": {k: " ".join([ABSTRACT] * 2) for k in ("voice_antecedent", "silence_consequence")}}


@pytest.fixture
def project(tmp_path):
    p = Project.init(tmp_path / "r", questions=["What leads employees to speak up or stay silent at work, and what follows?"])
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Speaking up in service teams", doi="10.1000/b1", set="Development", abstract=ABSTRACT),
                       BenchmarkPaper(benchmark_id="B2", title="Validation paper", set="Validation", abstract=ABSTRACT)])
    sync(p)
    for kind, data in (("cells", CELLS), ("codebook", CODEBOOK), ("anchors", ANCHORS)):
        row = artifacts.save_new_version(p, kind, data, origin="manual")
        artifacts.set_status(p, kind, row["version"], "approved")
    return p


def _hits(text: str) -> list[tuple[int, str]]:
    return guard.topic_hits(text)


@pytest.mark.parametrize("kind", TEMPLATE_KINDS)
def test_construct_templates_are_topic_neutral(project, kind):
    from wider.construct import adjacent

    # adjacent literatures have their own proposal path (`wider construct adjacent propose`)
    prompt, info = adjacent.build_prompt(project) if kind == "adjacent" else build_prompt(project, kind)
    assert len(prompt) > 500 and info["benchmark_n"] == 1
    assert _hits(prompt) == [], f"{kind}: {_hits(prompt)[:5]}"


def test_construct_templates_render_examples_from_the_project(project):
    prompt, _ = build_prompt(project, "synonyms")
    assert "Voice or silence measured" in prompt and 'dimension "form"' in prompt and "voice, silence" in prompt
    prompt, _ = build_prompt(project, "anchors")
    assert "voice_antecedent" in prompt and "silence_consequence" in prompt


def test_screening_template_is_topic_neutral(project):
    b = build(project)
    assert "two elements" in b["prompt"] and "voice_subscale_only" in b["prompt"]
    assert _hits(b["prompt"]) == [], _hits(b["prompt"])[:5]
    assert set(b["schema"]["required"]) >= {"summary", "e1", "e1_quote", "target", "cell", "decision"}


def test_template_sources_are_topic_neutral():
    for path in list((ROOT / "wider" / "construct" / "templates").glob("*.j2")) + [ROOT / "wider" / "screen" / "templates" / "screen.md.j2"]:
        assert _hits(path.read_text(encoding="utf-8")) == [], path.name
