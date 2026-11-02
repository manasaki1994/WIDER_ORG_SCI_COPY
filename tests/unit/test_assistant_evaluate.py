"""guide-assistant 7.1 and 7.2: the shipped question set and the retrieval check (D11), by keywords alone over
WIDER's own documents. No project and no model."""
from __future__ import annotations

from wider.assistant import evaluate as E
from wider.assistant.sections import all_sections


def test_question_set_counts():
    """At least 15 questions per reader group, 10 uncovered; every covered question names sections that exist, an
    uncovered one names none; no question twice."""
    qs = E.questions()
    by_group = {g: [q for q in qs if q["group"] == g] for g in (*E.GROUPS, "uncovered")}
    assert {q["group"] for q in qs} == set(by_group)
    assert all(len(by_group[g]) >= 15 for g in E.GROUPS)
    assert len(by_group["uncovered"]) == 10
    ids = {s.id for s in all_sections()}
    for q in qs:
        if q["group"] == "uncovered":
            assert q["needs"] == [], q["question"]
        else:
            assert q["needs"] and set(q["needs"]) <= ids, (q["question"], set(q["needs"]) - ids)
    assert len({q["question"] for q in qs}) == len(qs)


def test_question_set_retrieval():
    """Keywords alone: in every group at least 90% of the questions find a section that answers them among the first
    three, and no uncovered question finds any section."""
    r = E.check()
    for g in E.GROUPS:
        assert r["groups"][g]["share"] >= E.MIN_SHARE, (g, r["groups"][g]["missed"])
    assert r["uncovered"]["matched"] == []
    assert r["ok"]


def test_check_reports_a_miss():
    """A covered question whose named section is not among the first three is listed as missed, and an uncovered
    question that matches is listed, so the check fails."""
    qs = [{"group": g, "question": "How does deduplication find duplicate records?", "needs": ["glossary/recall"]}
          for g in E.GROUPS]
    qs.append({"group": "uncovered", "question": "How does deduplication find duplicate records?", "needs": []})
    r = E.check(qs=qs)
    assert not r["ok"] and r["groups"]["auditor"]["missed"] and r["uncovered"]["matched"]
