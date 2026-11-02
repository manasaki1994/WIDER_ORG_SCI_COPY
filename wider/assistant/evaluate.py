"""The retrieval check of Ask WIDER (guide-assistant D11): the shipped question set, asked of WIDER's own documents by
keywords alone. Per reader group, the share of questions with a needed section in the first three; for the uncovered
questions, the ones that found any section. Nothing of a project is read and no model is called. A test runs the same set
through the fused path with the fake embedder (M123, 31 Oct)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .retrieve import retrieve
from .sections import all_sections

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions.yaml"
GROUPS = ("first-time", "researcher", "auditor")
TOP = 3
MIN_SHARE = 0.9


def questions(path: Path | None = None) -> list[dict[str, Any]]:
    """The entries ``{group, question, needs}`` of the shipped set (``needs``: the section ids that answer it, any
    one of which counts; empty for an uncovered question)."""
    data = yaml.safe_load((path or QUESTIONS).read_text(encoding="utf-8")) or {}
    return [{"group": str(e["group"]), "question": str(e["question"]), "needs": [str(i) for i in e.get("needs") or []]}
            for e in data.get("questions") or []]


def check(sections=None, qs: list[dict[str, Any]] | None = None, project=None) -> dict[str, Any]:
    """``{groups: {group: {share, found, total, missed: [question]}}, uncovered: {total, matched: [question]}, ok}``
    by keywords alone, or with ``project``'s embedding backend as Ask WIDER ranks (asked in general); ``ok`` when
    every group reaches ``MIN_SHARE`` and no uncovered question matches."""
    sections = all_sections() if sections is None else sections
    qs = questions() if qs is None else qs
    groups: dict[str, dict[str, Any]] = {}
    matched: list[str] = []
    n_uncovered = 0
    for q in qs:
        res = retrieve(q["question"], project=project, sections=sections, meaning=project is not None)
        hit = res.matched
        if q["group"] == "uncovered":
            n_uncovered += 1
            if hit:
                matched.append(q["question"])
            continue
        g = groups.setdefault(q["group"], {"found": 0, "total": 0, "missed": []})
        g["total"] += 1
        top = res.ranked[:TOP] if hit else []
        if any(i in top for i in q["needs"]):
            g["found"] += 1
        else:
            g["missed"].append(q["question"])
    for g in groups.values():
        g["share"] = round(g["found"] / g["total"], 3) if g["total"] else 0.0
    ok = all(groups.get(name, {}).get("share", 0.0) >= MIN_SHARE for name in GROUPS) and not matched
    return {"groups": groups, "uncovered": {"total": n_uncovered, "matched": matched}, "top": TOP,
            "min_share": MIN_SHARE, "ok": ok}
