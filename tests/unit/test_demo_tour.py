"""The guided tour's data (showcase/demo/tour.yaml through build_demo.tour_data; openspec change interactive-demo,
design D7, D8): every step points at an element that exists, says who acts and what a choice trades off, uses plain
words, and quotes numbers that match the recording."""
import importlib.util
import json
import math
import re
import shutil
import sys

import pytest

from tests.unit.demo_node import DEMO_DIR, REPO

STATIC = REPO / "wider" / "ui" / "static"
PLACE = re.compile(r"\{[a-z0-9]+\.[a-z0-9_]+\}")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, mod)   # a dataclass looks its module up
    spec.loader.exec_module(mod)
    return mod


build = _load("build_demo", DEMO_DIR / "build_demo.py")


@pytest.fixture(scope="module")
def tour():
    return build.tour_data()


def recorded():
    return [c for c in build.CHECKPOINTS if (build.SNAPSHOT / f"checkpoint-{c}.json").exists()]


def answer(cp, key):
    bodies = json.loads((build.SNAPSHOT / "bodies.json").read_text(encoding="utf-8"))
    ref = json.loads((build.SNAPSHOT / f"checkpoint-{cp}.json").read_text(encoding="utf-8"))["answers"][key]
    return bodies[ref]["json"]


def text_of(step):
    return " ".join([step["title"], *step["body"], step.get("tradeoff", "")])


def test_every_step_target_exists(tour):
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    pages = re.search(r"const PAGES = \{([^}]*)\}", (STATIC / "app.js").read_text(encoding="utf-8")).group(1)
    layer = " ".join((DEMO_DIR / f).read_text(encoding="utf-8") for f in build.LAYER)
    app_js = " ".join(f.read_text(encoding="utf-8") for f in STATIC.glob("*.js"))
    assert tour["steps"]
    for s in tour["steps"]:
        assert re.search(rf"\b{s['page']}:", pages), f"{s['id']}: no page {s['page']} in app.js"
        t = s["target"]
        if s.get("drawn"):
            assert t in app_js, f"{s['id']}: no {t} in the app's code"
        else:
            assert f'id="{t}"' in html or f'"{t}"' in layer, f"{s['id']}: no element {t}"


def test_steps_state_who_acts_and_tradeoffs(tour):
    for s in tour["steps"]:
        assert s["who"] in ("You decide", "WIDER does"), s["id"]
        if s.get("choice"):
            assert s.get("tradeoff"), f"{s['id']} is a choice without a trade-off"
    assert any(s["who"] == "You decide" for s in tour["steps"])
    assert any(s.get("tradeoff") for s in tour["steps"])


def test_every_chapter_has_an_intro(tour):
    """Spec: each chapter opens with a line on what it is for (change demo-chapters-and-video, D2)."""
    stages = list(dict.fromkeys(s["stage"] for s in tour["steps"]))
    assert [c["stage"] for c in tour["chapters"]] == stages
    for c in tour["chapters"]:
        assert c["title"] and c["intro"].startswith("This chapter is for"), c["stage"]
    assert not any(s["id"].endswith("-why") and not s.get("action") for s in tour["steps"]), "a step that only explains"


def test_core_path_content_and_length(tour):
    """Spec: the Quick tour has 8 to 12 steps in the order of the review, with a decision by the researcher, both
    reads, the comparison and the recall interval (D3)."""
    core = [s for s in tour["steps"] if s.get("core")]
    assert 8 <= len(core) <= 12
    order = [c["stage"] for c in tour["chapters"]]
    assert [order.index(s["stage"]) for s in core] == sorted(order.index(s["stage"]) for s in core)
    ids = {s["id"] for s in core}
    assert {"screen-act", "screen-compare", "recall-interval"} <= ids
    assert any(s["who"] == "You decide" for s in core)
    assert {"Question", "Deduplication", "Samples", "Screening", "Recall"} <= {s["stage"] for s in core}


def test_plain_words(tour):
    lm = _load("list_messages", REPO / "scripts" / "list_messages.py")
    terms, plain = lm.read_word_list(REPO / "docs" / "guide-text.md")
    avoid = [t for t in terms if t not in plain]
    assert avoid

    def check(where, text, allowed=()):
        t = re.sub(r"“[^”]*”", " ", text).casefold()   # an app label, quoted as it reads
        for w in avoid:
            if w in allowed:
                continue
            assert not re.search(lm.whole(w), t), f"{where} uses {w!r}"

    for s in tour["steps"]:
        check(f"step {s['id']}", text_of(s))
    for c in tour["chapters"]:
        check(f"chapter {c['stage']}", c["title"] + " " + c["intro"])
    for c in tour["concepts"]:
        check(f"concept {c['id']}", c["title"] + " " + " ".join(c["body"]), {d.casefold() for d in c["defines"]})
    end = tour["end"]
    check("end card", " ".join([end["title"], end["intro"], *(c["says"] for c in end["commands"]),
                                *(" ".join([r["you"], r["wider"], r["tradeoff"]]) for r in end["rows"])]))


def test_percentile_step_uses_snapshot_rank(tour):
    if "ranked" not in recorded():
        pytest.skip("the ranking is not recorded")
    n = answer("ranked", "GET /api/rank?limit=50&offset=0")["n_rows"]
    rows = [r for r in answer("ranked", "GET /api/rank/validate")["rows"] if r.get("found") and r["set"] == "Validation"]
    ex = min(rows, key=lambda r: r["rank"])
    want = f"rank {ex['rank']} of {n}: in the top {math.ceil(100 * ex['rank'] / n)}% of records by score"
    step = next(s for s in tour["steps"] if s["id"] == "rank-pct")
    assert want in " ".join(step["body"])
    assert ex["title"] in " ".join(step["body"])
    card = next(c for c in tour["concepts"] if c["id"] == "scores")
    assert f"rank {ex['rank']} of {n}" in " ".join(card["body"])


def test_recall_card_uses_snapshot_numbers(tour):
    if "screened" not in recorded():
        pytest.skip("the screening round is not recorded yet")
    card = " ".join(next(c for c in tour["concepts"] if c["id"] == "recall")["body"])
    assert not PLACE.search(card) and "95% interval" in card
    assert "Illustrative" in card


def test_disagreement_card_matches_snapshot(tour):
    if "screened2" not in recorded():
        pytest.skip("the second screening round is not recorded yet")
    card = " ".join(next(c for c in tour["concepts"] if c["id"] == "disagree")["body"])
    assert not PLACE.search(card) and "third read" in card


def test_cards_without_a_recording_say_so(tour):
    for cid, cp in (("disagree", "screened2"), ("recall", "screened")):
        if cp in recorded():
            continue
        card = " ".join(next(c for c in tour["concepts"] if c["id"] == cid)["body"])
        assert "not recorded yet" in card and not PLACE.search(card)


def test_end_card_content(tour):
    end = tour["end"]
    assert end["title"] == "Start a review of your own"
    assert [c["command"] for c in end["commands"]] == ["wider init <folder>", "wider ui"]
    stages = [r["stage"] for r in end["rows"]]
    assert stages == ["Question", "Search strings", "Deduplication", "Ranking", "Samples", "Screening", "Recall"]
    for r in end["rows"]:
        assert r["you"] and r["wider"] and r["tradeoff"], r["stage"]


def test_unrecorded_steps_are_dropped(tour, tmp_path):
    ids = [s["id"] for s in tour["steps"]]
    done = recorded()
    src = build.yaml.safe_load((DEMO_DIR / "tour.yaml").read_text(encoding="utf-8"))["steps"]
    for s in src:
        cp = done[-1] if s["checkpoint"] == "last" else s["checkpoint"]
        keep = cp in done and all(n in done for n in s.get("needs", []))
        assert (s["id"] in ids) == keep, s["id"]
    # a copy of the recording with only the first checkpoint: nothing from a later one is left
    snap = tmp_path / "snapshot"
    snap.mkdir()
    shutil.copy(build.SNAPSHOT / "bodies.json", snap / "bodies.json")
    shutil.copy(build.SNAPSHOT / "checkpoint-imported.json", snap / "checkpoint-imported.json")
    small = build.tour_data(snap)
    assert {s["checkpoint"] for s in small["steps"]} == {"imported"}
    assert small["steps"][-1]["id"] == "finish"
    assert not PLACE.search(json.dumps(small))


def test_unknown_placeholder_raises(tmp_path):
    src = (DEMO_DIR / "tour.yaml").read_text(encoding="utf-8")
    bad = src.replace("Welcome to the guided tour", "Welcome to {rk.nothing_here}", 1)
    assert bad != src
    (tmp_path / "tour.yaml").write_text(bad, encoding="utf-8")
    with pytest.raises(build.BuildError, match="nothing_here"):
        build.tour_data(here=tmp_path)


def test_a_choice_needs_a_tradeoff(tmp_path):
    data = build.yaml.safe_load((DEMO_DIR / "tour.yaml").read_text(encoding="utf-8"))
    step = next(s for s in data["steps"] if s.get("choice"))
    del step["tradeoff"]
    (tmp_path / "tour.yaml").write_text(build.yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(build.BuildError, match="tradeoff"):
        build.tour_data(here=tmp_path)
