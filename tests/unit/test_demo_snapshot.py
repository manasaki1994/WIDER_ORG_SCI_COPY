"""The interactive demo's recorded answers (openspec change interactive-demo, design D9 and D10).

The snapshot under showcase/demo/snapshot/ is recorded from the example project at fixed checkpoints. These tests read
it as the demo page does: a checkpoint maps ``"METHOD path?query"`` to a body id, and bodies.json holds each body.
"""
import json
import re
from pathlib import Path

import pytest

SNAPSHOT = Path(__file__).resolve().parents[2] / "showcase" / "demo" / "snapshot"
FREE = ["imported", "ranked", "sampled"]
PAID = ["screened", "screened2"]


def _bodies():
    return json.loads((SNAPSHOT / "bodies.json").read_text(encoding="utf-8"))


def _answers(name):
    f = SNAPSHOT / f"checkpoint-{name}.json"
    if not f.exists():
        pytest.skip(f"checkpoint {name} is not recorded yet")
    return json.loads(f.read_text(encoding="utf-8"))["answers"]


def _get(name, key, bodies=None):
    bodies = bodies or _bodies()
    entry = bodies[_answers(name)[key]]
    return entry["json"] if entry["type"] == "json" else entry["text"]


@pytest.mark.parametrize("name", FREE + PAID)
def test_home_lists_only_demo_project(name):
    home = _get(name, "GET /api/home")
    assert [p["name"] for p in home["projects"]] == ["employee-voice"]
    assert home["projects"][0]["title"] == "Employee voice and silence"
    assert _get(name, "GET /api/stages")["project_key"] == "demo0employee0voice"


@pytest.mark.parametrize("name", FREE + PAID)
def test_history_authors_are_reviewer_a(name):
    bodies = _bodies()
    answers = _answers(name)
    authors = set()
    for key, body_id in answers.items():
        if key.startswith("GET /api/history?"):
            authors |= {row.get("author") for row in bodies[body_id]["json"]}
    assert authors == {"Reviewer A"}
    # every recorded change has the history page's own list
    assert len(_get(name, "GET /api/history?limit=200", bodies)) > 0


def _counts(name):
    bodies = _bodies()
    answers = _answers(name)
    dedup = _get(name, "GET /api/dedup", bodies)["summary"]
    prisma = _get(name, "GET /api/outputs/file?path=exports%2Fprisma_2020.json", bodies)
    rank_keys = [k for k in answers if k.startswith("GET /api/rank?")]
    corpus = {bodies[answers[k]]["json"]["corpus"]["records"] for k in rank_keys}
    records = _get(name, "GET /api/dedup/records?limit=50", bodies)["total"]
    return dedup, prisma, corpus, records


@pytest.mark.parametrize("name", FREE + PAID)
def test_counts_agree_across_pages(name):
    dedup, prisma, corpus, records = _counts(name)
    unique = dedup["unique_records"]
    assert corpus == {unique}
    assert records == unique
    assert prisma["unique"] == unique
    if name in FREE:
        assert prisma["not_screened"] == unique
    else:
        # the whole corpus was screened, and the PRISMA split adds up to it
        assert prisma["not_screened"] == 0 and prisma["screened"] == unique
        assert prisma["included_ta"] + prisma["maybe_ta"] + prisma["excluded"] == unique
    assert prisma["identified"] == dedup["total_records_imported"]
    assert prisma["duplicates_removed"] == dedup["duplicate_records_removed"]
    assert sum(d["n"] for d in prisma["databases"]) == prisma["identified"]


def test_merge_changes_the_count_once():
    """Between `imported` and `ranked` the open pairs are merged: the unique count drops by the pairs merged."""
    before, after = _counts("imported")[0], _counts("ranked")[0]
    merged = after.get("duplicate_records_removed_by_exact_title_manual_confirmed", 0)
    assert merged > 0
    assert after["unique_records"] == before["unique_records"] - merged
    assert after["total_records_imported"] == before["total_records_imported"]


def test_sampled_matches_screened():
    sampled = _answers("sampled")
    screened = _answers("screened")
    bodies = _bodies()
    # the same records drawn; each checkpoint is recorded on its own copy of the project (other draw times), and by
    # screening more labels are in and the validation sample is locked (completeness, provenance and the unlock
    # warning follow the labels and the lock)
    def drawn(cp):
        moving = ("drawn_at", "progress", "locked", "completeness", "provenance", "unlock_warning")
        return {k: {f: v for f, v in s.items() if f not in moving}
                for k, s in _get(cp, "GET /api/samples", bodies)["samples"].items()}
    assert drawn("sampled") == drawn("screened")
    assert _get("sampled", "GET /api/dedup", bodies)["summary"] == _get("screened", "GET /api/dedup", bodies)["summary"]
    assert set(sampled) & set(screened)


def test_screened_offers_second_setting():
    first = _get("screened", "GET /api/screen")
    second = _get("screened2", "GET /api/screen")
    assert len(second["runs"]) > len(first["runs"])
    models = {k for k in _answers("screened2") if k.startswith("GET /api/screen/models")}
    assert models


@pytest.mark.parametrize("name", FREE + PAID)
def test_backends_recorded_without_key(name):
    bodies = _bodies()
    answers = _answers(name)
    config = _get(name, "GET /api/project", bodies)["config"]
    assert config["providers"], "the example project names a model setting"
    assert not any(b["key_set"] for b in config["providers"] + config["embeddings"])
    for body_id in set(answers.values()):
        text = json.dumps(bodies[body_id])
        assert not re.search(r'"key_set":\s*true', text)
        assert not re.search(r"sk-[A-Za-z0-9_-]{20,}", text)


COVERAGE = SNAPSHOT.parent / "coverage.json"


def test_coverage_lists_guide_and_assistant():
    """D11: the two Ask WIDER routes the examples need are answered; the assistant's other routes are listed as not in
    the demo with a reason; ask_examples holds 3-5 questions, each with a page."""
    cov = json.loads(COVERAGE.read_text(encoding="utf-8"))
    handled = set(cov["handlers"])
    assert {"POST /api/assistant/ask", "GET /api/assistant/history"} <= handled
    others = [k for k in cov["not_in_demo"] if "/api/assistant/" in k]
    assert others and all("recorded beforehand" in cov["not_in_demo"][k] for k in others)
    assert not handled & set(others)
    ex = cov["ask_examples"]
    assert 3 <= len(ex) <= 5 and all(e["question"].strip() and e["page"] for e in ex)


def test_ask_examples_recorded():
    """D12: the recorded history holds one turn per example question that has an answer, signed by Reviewer A, made by
    the local model, each with an answer and at least one source, and only the shown fields."""
    cov = json.loads(COVERAGE.read_text(encoding="utf-8"))
    questions = [e["question"] for e in cov["ask_examples"]]
    answers = _answers("screened2")
    if "GET /api/assistant/history" not in answers:
        pytest.skip("the Ask examples are not recorded yet")
    h = _get("screened2", "GET /api/assistant/history")
    assert h["author"] == "Reviewer A"
    turns = h["turns"]
    assert turns and [t["n"] for t in turns] == list(range(1, len(turns) + 1))
    assert len(turns) >= 3 and len({t["question"] for t in turns}) == len(turns)
    for t in turns:
        assert t["question"] in questions
        assert t["answer"].strip() and t["based_on"]
        assert t["model"] == "qwen3:8b"
        assert set(t) <= {"n", "at", "page", "question", "answer", "based_on", "model", "answered_by"}
