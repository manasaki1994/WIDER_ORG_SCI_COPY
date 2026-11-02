"""Codebook artifact → screening prompt (Jinja) + JSON schema. Both are versioned as a prompts artifact."""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from ..construct import artifacts
from ..construct.adjacent import approved_literatures
from ..core.project import Project
from ..core.provenance import sha256_text

TEMPLATES = Path(__file__).parent / "templates"
ADJACENT_LABEL = "adjacent_literature"  # label group added when adjacent literatures are approved; values = keys + "none"
WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}


def build(project: Project, codebook_version: int | None = None, cells_version: int | None = None) -> dict:
    row, cb = artifacts.load(project, "codebook", codebook_version)
    if row is None:
        raise ValueError("There is no codebook yet, and the screening prompt is built from it. Write one by hand or ask "
                         "the model for one on the Concepts and synonyms page (codebook tab), or with wider construct save codebook.")
    crow, cells = artifacts.load(project, "cells", cells_version)
    elements = cb["elements"]
    checks = cb.get("checks", [])
    edge_cases = cb.get("edge_cases", [])
    label_groups = cb.get("labels", [])
    if cells and not any(g["group"] == "cell" for g in label_groups):
        label_groups = [{"group": "cell", "values": [c["key"] for c in cells["cells"]]}] + label_groups
    for spec in project.config.protocol.labelling.labels:  # project-level labels (e.g. adjacent_literature)
        if spec.values and not any(g["group"] == spec.key for g in label_groups):
            label_groups.append({"group": spec.key, "values": list(spec.values), "description": spec.description, "label": spec.label})
    adjacent_lits = approved_literatures(project)  # approved adjacent artifact entries become the adjacent_literature label
    if adjacent_lits and not any(g["group"] == ADJACENT_LABEL for g in label_groups):
        label_groups.append({"group": ADJACENT_LABEL, "values": [e["key"] for e in adjacent_lits] + ["none"], "label": "Adjacent literature",
                             "any_decision": True, "description": "The named adjacent literature the record belongs to, whatever the decision."})
    thin_words = project.config.protocol.corpus.thin_words
    ctx = {
        "question": cb["question"], "target_description": cb["target_description"], "elements": elements,
        "n_elements_word": WORDS.get(len(elements), str(len(elements))), "checks": checks,
        "n_checks_word": WORDS.get(len(checks), str(len(checks))), "adjacent": cb.get("adjacent_literatures", []),
        "edge_cases": edge_cases, "cells": cells["cells"] if cells else [], "exclusion_reasons": cb["exclusion_reasons"],
        "label_groups": label_groups, "not_a_criterion": (cells or {}).get("not_a_criterion", []),
        "adjacent_literatures": adjacent_lits,
        "thin_abstract_rule": cb.get("thin_abstract_rule") or
        f"A record with no abstract or an abstract of fewer than about {thin_words} words is \"maybe\", unless the title alone makes an include obvious, or the title alone shows the record is plainly not a study or plainly off topic (then exclude).",
        "include_checks": [c["key"] for c in checks if "include" in c.get("if_yes", "").lower()],
    }
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True)
    prompt = env.get_template("screen.md.j2").render(**ctx)
    props: dict = {"summary": {"type": "string"}}
    for e in elements:
        props[e["id"]] = {"type": "string", "enum": ["present", "unclear", "absent"]}
        props[f"{e['id']}_quote"] = {"type": "string"}
    for c in checks:
        props[c["key"]] = {"type": "boolean"}
    props["target_fit"] = {"type": "string", "enum": ["core", "adjacent", "unrelated"]}
    if edge_cases:
        props["edge_case"] = {"type": "string", "enum": [e["key"] for e in edge_cases] + ["none"]}
    props["decision"] = {"type": "string", "enum": ["include", "maybe", "exclude"]}
    props["exclusion_reason"] = {"type": "string", "enum": [r["code"] for r in cb["exclusion_reasons"]] + ["none"]}
    props["missing_for_maybe"] = {"type": "string"}
    for g in label_groups:
        props[g["group"]] = {"type": "string", "enum": list(g["values"]) + ["not_applicable"]}
    schema = {"type": "object", "properties": props, "required": list(props.keys()), "additionalProperties": False}
    return {"prompt": prompt, "schema": schema, "codebook_version": row["version"], "cells_version": crow["version"] if crow else None,
            "elements": [e["id"] for e in elements], "checks": [c["key"] for c in checks],
            "include_checks": ctx["include_checks"], "label_groups": [g["group"] for g in label_groups],
            "maybe_checks": [c["key"] for c in checks if "maybe" in c.get("if_yes", "").lower()],
            "absent_checks": [c["key"] for c in checks if "absent" in c.get("if_yes", "").lower()]}


def save_prompt_artifact(project: Project, built: dict, note: str = "") -> dict:
    data = {"prompt": built["prompt"], "schema": built["schema"], "codebook_version": built["codebook_version"],
            "cells_version": built["cells_version"], "elements": built["elements"], "checks": built["checks"],
            "include_checks": built["include_checks"], "maybe_checks": built["maybe_checks"],
            "absent_checks": built["absent_checks"], "label_groups": built["label_groups"],
            "prompt_sha256": sha256_text(built["prompt"]), "schema_sha256": sha256_text(json.dumps(built["schema"], sort_keys=True))}
    return artifacts.save_new_version(project, "prompts", data, origin="manual",
                                      note=note or f"built from codebook v{built['codebook_version']}")


def load_prompt(project: Project, version: int | None = None) -> tuple[dict, dict]:
    row, data = artifacts.load(project, "prompts", version)
    if row is None:
        raise ValueError("no prompts artifact; run wider screen build-prompt")
    return row, data
