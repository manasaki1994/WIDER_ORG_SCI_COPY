"""The suggestion of how many more labels may be needed (validation-sample-planner D5). Synthetic projects only; the
tests check structure (steps of 10, band sums, L <= M <= U, probabilities in [0, 1], same seed same numbers), not
exact shares."""
from __future__ import annotations

import pytest

from tests.unit.test_validation_plan import Band, _ranked
from wider.recall.estimate import estimate
from wider.sample import plan as pl
from wider.sample.design import draw_probability
from wider.sample.labels import label
from wider.screen.decisions import set_decision

N = 600


def _rel(r):
    return (r <= 100 and r % 3 == 0) or (100 < r <= 300 and r % 8 == 0) or (r > 300 and r % 25 == 0)


def _drawn(tmp_path, keep=lambda r: r % 7 != 0, target=0.5, precision=None, unlabelled=0):
    """A 600-record ranking, three bands of 40, every record screened and the sample labelled from ``_rel``."""
    p = _ranked(tmp_path, n=N, bands=[Band(name="S1", start=1, end=100, n=40), Band(name="S2", start=101, end=300, n=40),
                                      Band(name="S3", start=301, end=None, n=40)])
    pr = p.config.protocol.set("recall.target", target)
    p.config.protocol = pr.set("recall.precision", precision) if precision else pr
    p.save()
    draw_probability(p)
    for r in range(1, N + 1):
        set_decision(p, "main", r, "include" if (_rel(r) and keep(r)) or r <= 60 else "exclude")
    ids = [x["record_id"] for x in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")]
    for rid in ids[unlabelled:]:
        label(p, "validation", rid, "r1", "include" if _rel(rid) else "exclude")
    return p


def _check(part, cap=300):
    assert part["m"] % 10 == 0 and sum(part["by_band"].values()) == part["m"]
    assert all(v % 10 == 0 for v in part["by_band"].values())
    lo, hi = part["range"]
    assert lo <= part["m"] and (hi is None or part["m"] <= hi) and part["m"] <= cap


def test_suggest_target_and_precision(tmp_path):
    p = _drawn(tmp_path, precision=0.3)
    s = pl.suggest(p, "validation", "main")
    t, pr = s["target"], s["precision"]
    _check(t)
    _check(pr)
    assert 0 <= t["p_met"] <= 1 and 0 <= t["p_not_met"] <= 1 and t["p_met"] + t["p_not_met"] <= 1
    assert s["lines"][0].startswith(f"More labels could settle it. To tell whether the target 0.50 is met at the next look: "
                                    f"label about {t['m']} more records (")
    assert "given what the labels so far leave uncertain. With" in s["lines"][0]
    assert any(x.startswith(f"For a margin of 0.30: about {pr['m']} more records (") for x in s["lines"])
    assert s["lines"][-1] == pl.NEVER_A_PROMISE
    assert pl.suggest(p, "validation", "main") == s                      # same seed, same numbers
    p.config.protocol = p.config.protocol.set("recall.precision", None)
    q = pl.suggest(p, "validation", "main")
    assert q["precision"] is None and not any(x.startswith("For a margin") for x in q["lines"])
    p.config.protocol = p.config.protocol.set("recall.target", None)
    assert pl.suggest(p, "validation", "main") is None                   # neither target nor precision


def test_suggest_uses_labels_only(tmp_path):
    p = _drawn(tmp_path, precision=0.3)
    s = pl.suggest(p, "validation", "main")
    # every score changes, and the model's votes on every record: the numbers stay
    p.store.execute("UPDATE ranking_rows SET score = 1 - score")
    p.store.execute("UPDATE screen_decisions SET votes='[0.99]', cell='x'")
    assert pl.suggest(p, "validation", "main") == s
    # the cells hold the frame outside every other sample
    cells = pl.sample_cells(p, "validation", "main")
    assert [b["name"] for b in cells["bands"]] == ["S1", "S2", "S3"]
    assert sum(c["N"] for b in cells["bands"] for c in b["cells"].values()) == N
    assert all(c["n"] + len(c["left"]) == c["N"] for b in cells["bands"] for c in b["cells"].values())
    p.store.execute("DELETE FROM labels")
    assert pl.suggest(p, "validation", "main")["lines"] == [pl.NO_LABELS]


def test_suggest_excludes_other_samples(tmp_path):
    p = _drawn(tmp_path, precision=0.3)
    s3 = [x["record_id"] for x in p.store.query("SELECT record_id FROM ranking_rows WHERE rank > 300 ORDER BY rank")]
    sid = p.store.scalar("SELECT sample_id FROM sample_rows LIMIT 1")
    taken = {x["record_id"] for x in p.store.query("SELECT record_id FROM sample_rows WHERE sample_id=?", (sid,))}
    dev = [r for r in s3 if r not in taken][:5]
    p.store.execute("INSERT INTO samples(name,kind,ranking_id,design,seed,n,created_at,locked) "
                    "VALUES('dev','purposive',1,'{}',1,5,'2026-01-01',0)")
    did = p.store.scalar("SELECT sample_id FROM samples WHERE name='dev'")
    p.store.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) "
                        "VALUES(?,?,?,NULL,NULL,'x',NULL)", [(did, r, i) for i, r in enumerate(dev)])
    cells = pl.sample_cells(p, "validation", "main")
    s3c = cells["bands"][2]["cells"]
    left = set(s3c["kept"]["left"]) | set(s3c["not kept"]["left"])
    # dev was drawn after the validation sample: its records stay in the band's N (the design's N_h) but cannot be
    # labelled here (recall-final-check-fixes)
    assert not left & set(dev) and sum(c["N"] for c in s3c.values()) == 300 and cells["bands"][2]["in_other"] == 5


def test_suggest_below_target_said(tmp_path):
    p = _drawn(tmp_path, keep=lambda r: r <= 120, target=0.9)
    s = pl.suggest(p, "validation", "main")
    assert s["estimate"] < 0.9 and s["p_met_at_cap"] < 0.10
    assert any(x.startswith(f"Recall is estimated at {s['estimate']:.2f}, below the target 0.90: more labels are "
                            "unlikely to show the target met (chance about ") and
               x.endswith(f"even with {s['cap']:,} more); they would more likely show it not met.") for x in s["lines"])
    assert s["cap"] == s["left"]   # the largest total tried is every record left (no cap of 300)


def test_suggest_cap_said(tmp_path):
    """A census decides: on this frame only labelling every record left is expected to settle the target 0.88, and
    the target part says so (no cap of 300, no monkeypatch of the steps)."""
    p = _drawn(tmp_path, target=0.88)
    s = pl.suggest(p, "validation", "main")
    t = s["target"]
    assert s["settles"] and t["census"] and t["m"] == s["left"] == s["cap"] == 480
    assert s["lines"][0].startswith("Only labelling all 480 records left in the sampled bands is expected to settle "
                                    "whether the target 0.88 is met. With 480 more, the chance is about ")
    assert pl.PAST_300 in s["lines"]


def test_suggest_alternative_split(tmp_path):
    p = _drawn(tmp_path)
    t = pl.suggest(p, "validation", "main")["target"]
    alt = t["alternative"]
    assert sum(alt["by_band"].values()) == t["m"] and 0 <= alt["p_met"] <= 1 and 0 <= alt["p_not_met"] <= 1
    cells = pl.sample_cells(p, "validation", "main")
    room = {b["name"]: sum(len(c["left"]) for c in b["cells"].values()) for b in cells["bands"]}
    # in proportion to the records left: the band with most left gets most
    assert max(alt["by_band"], key=alt["by_band"].get) == max(room, key=room.get)
    line = next(x for x in pl.suggest(p, "validation", "main")["lines"] if x.startswith("Alternative:"))
    assert line.startswith(f"Alternative: the same {t['m']} spread in proportion to the records left (")


def test_suggest_stored_with_look(tmp_path, monkeypatch):
    p = _drawn(tmp_path)
    look = estimate(p, "validation", "main", B=50)["look"]
    s = pl.suggest(p, "validation", "main", look=look)
    assert s["checksum"] == look["checksum"] and s["set_key"] == "main"
    monkeypatch.setattr(pl, "_inner", lambda *a, **k: pytest.fail("simulated again"))
    assert pl.suggest(p, "validation", "main", look=look) == s           # a reload reads it back
    assert pl.stored_suggestion(p, "validation", "main") == s


def test_suggested_refused_after_label_change(tmp_path):
    p = _drawn(tmp_path)
    pl.suggest(p, "validation", "main", look=estimate(p, "validation", "main", B=50)["look"])
    rid = p.store.scalar("SELECT record_id FROM labels ORDER BY record_id LIMIT 1")
    was = p.store.scalar("SELECT decision FROM labels WHERE record_id=?", (rid,))
    # the record is sealed by the look: the change is a correction with a reason (label-completion-and-sealing D5)
    label(p, "validation", rid, "r1", "maybe", correction_reason="synthetic correction")
    stale = "the labels changed since the last recall estimate; estimate recall again to update the suggestion"
    with pytest.raises(pl.SuggestionStale, match=f"^{stale}$"):
        pl.stored_suggestion(p, "validation", "main")
    # the CLI and the API twin refuse the stale suggestion and add nothing
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    rows = p.store.scalar("SELECT COUNT(*) FROM sample_rows")
    r = CliRunner().invoke(cli_app, ["validation", "extend", "validation", "--suggested", "--dir", str(p.root)])
    assert r.exit_code == 1 and stale in " ".join(r.output.split())
    a = TestClient(create_app(p)).post("/api/validation/extend", json={"sample": "validation", "suggested": True})
    assert a.status_code == 409 and a.json()["detail"] == stale
    assert p.store.scalar("SELECT COUNT(*) FROM sample_rows") == rows
    label(p, "validation", rid, "r1", was, correction_reason="synthetic correction undone")
    assert pl.stored_suggestion(p, "validation", "main") is not None    # the labels of the look again
    with pytest.raises(pl.SuggestionStale):
        pl.stored_suggestion(p, "validation", "main#2")                  # another result set


def _unlabelled(p):
    return [x["record_id"] for x in p.store.query(
        "SELECT sr.record_id FROM sample_rows sr LEFT JOIN labels l ON l.record_id=sr.record_id "
        "AND l.sample_id=sr.sample_id WHERE l.label_id IS NULL ORDER BY sr.record_id")]


def _look1_and_suggested(tmp_path):
    """Look 1 on the fully labelled sample (inconclusive for 0.50), then the suggested records added."""
    from wider.sample.design import extend_probability

    p = _drawn(tmp_path)
    first = estimate(p, "validation", "main", B=50)
    assert first["outcome"]["outcome"] == "cannot_tell" and first["more_validation"]["target"]["m"] > 10
    added = extend_probability(p, "validation", "suggested")["added"]
    assert added > 10 and len(_unlabelled(p)) == added
    return p, first, added


def test_suggest_updates_with_labels(tmp_path):
    """Scenario "The suggestion follows the labels": the suggested records added and labelled, the next estimate is
    look 2 and its suggestion is computed afresh from all labels (recall waits for every final label, so the first
    look is on the fully labelled sample, label-completion-and-sealing D1)."""
    p, first, added = _look1_and_suggested(tmp_path)
    s1 = pl.suggest(p, "validation", "main", look=first["look"])
    for rid in _unlabelled(p):
        label(p, "validation", rid, "r1", "include" if _rel(rid) else "exclude")
    second = estimate(p, "validation", "main", B=50)
    assert second["look"]["index"] == first["look"]["index"] + 1 == 2
    assert second["bounds"]["method"] == first["bounds"]["method"]
    s2 = pl.suggest(p, "validation", "main", look=second["look"])
    assert s2["checksum"] == second["look"]["checksum"] != s1["checksum"]
    n1 = sum(c["n"] for b in pl.sample_cells(p, "validation", "main")["bands"] for c in b["cells"].values())
    assert n1 == 120 + added                           # every label, the added ones too


def test_suggest_time_limit(tmp_path, monkeypatch):
    p = _drawn(tmp_path)
    p.config.protocol = p.config.protocol.set("recall.suggest_seconds", 1)
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(pl.time, "monotonic", lambda: next(clock))
    s = pl.suggest(p, "validation", "main")
    assert s["approximate"] and s["draws"] == 1
    assert "(approximate: from 1 of 50 draws; time limit reached)" in s["lines"][0]


# ------------------------------------------------------------------------------- recall-certification-workload (D4, D5)
def _deep(tmp_path, target=0.9):
    """A 6,400-record low-prevalence frame: three bands of 40 sampled, the deep band (6,000 records) with one relevant
    record in 500 and none among its labels, every sampled record labelled."""
    n = 6400
    p = _ranked(tmp_path, n=n, bands=[Band(name="S1", start=1, end=100, n=40), Band(name="S2", start=101, end=400, n=40),
                                      Band(name="S3", start=401, end=None, n=40)])
    p.config.protocol = p.config.protocol.set("recall.target", target)
    p.save()
    draw_probability(p)
    rel = lambda r: (r <= 100 and r % 3 == 0) or (100 < r <= 400 and r % 10 == 0) or (r > 400 and r % 500 == 0)  # noqa: E731
    for r in range(1, n + 1):
        set_decision(p, "main", r, "include" if rel(r) or r <= 100 else "exclude")
    for rid in [x["record_id"] for x in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")]:
        label(p, "validation", rid, "r1", "include" if rel(rid) else "exclude")
    return p


def test_suggest_beyond_300(tmp_path):
    p = _deep(tmp_path)
    s = pl.suggest(p, "validation", "main", seconds=120)
    t = s["target"]
    assert t["m"] > 300 and t["m"] in pl.schedule(s["left"]) and sum(t["by_band"].values()) == t["m"]
    room = {b["name"]: sum(len(c["left"]) for c in b["cells"].values()) for b in pl.sample_cells(p, "validation", "main")["bands"]}
    assert all(v <= room[k] and v % 10 == 0 for k, v in t["by_band"].items())
    assert pl.schedule(1100) == [*range(10, 301, 10), *range(350, 1001, 50), 1100]
    assert pl.schedule(6280)[-4:] == [4750, 5000, 6000, 6280]
    assert s["lines"].count(pl.PAST_300) == 1 and s["cap"] == s["left"] == 6280
    assert f"label about {t['m']:,} more records (" in s["lines"][0]


def test_old_stored_suggestion_recomputed(tmp_path):
    import json
    p = _drawn(tmp_path)
    look = estimate(p, "validation", "main", B=50)["look"]
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    old = {"given": True, "target": {"m": 300, "by_band": {"S3": 300}, "range": [200, None]}, "precision": None,
           "lines": ["More validation needed. To tell whether the target 0.50 is met: even 300 more ..."],
           "settles": True, "set_key": "main", "checksum": look["checksum"]}   # stored before this change: no "v"
    p.store.execute("UPDATE validation_look_states SET suggestion=? WHERE sample_id=? AND labels_checksum=?",
                    (json.dumps(old), sid, look["checksum"]))
    s = pl.suggest(p, "validation", "main", look=look)
    assert s["v"] == pl.SUGGEST_VERSION and s["lines"][0].startswith("More labels could settle it.")
    stored = json.loads(p.store.scalar("SELECT suggestion FROM validation_look_states WHERE sample_id=? AND "
                                       "labels_checksum=?", (sid, look["checksum"])))
    assert stored["v"] == pl.SUGGEST_VERSION


def test_suggest_deadline_inside_draw(tmp_path):
    p = _deep(tmp_path)
    s = pl.suggest(p, "validation", "main", seconds=0.001)
    assert s["approximate"] and 1 <= s["draws"] < pl.SUGGEST_DRAWS
    assert s["target"] is not None and s["target"]["m"] > 0
    assert "(approximate: from " in s["lines"][0]


WARN = ("Settling whether the target 0.90 is met may be impractical: about 2,400 more labels (likely between 1,500 and "
        "4,000) are expected before the bounds say met or not met, more than the 500 you set as practical "
        "(recall.practical_labels). You may stop here and report the result as inconclusive; nothing requires more "
        "labels.")


def test_impractical_warning():
    lines, info = pl.impractical_lines(2400, [1500, 4000], 500, 6000, 0.9)
    assert lines == [WARN]
    assert info == {"m": 2400, "range": [1500, 4000], "limit": 500, "share_left": 0.4}
    assert pl.impractical_lines(400, [300, 600], 500, 6000, 0.9) == ([], None)   # under the limit: nothing
    assert pl.impractical_lines(2400, [1500, 4000], 500, 6000, None) == ([], None)


def test_impractical_share_line():
    lines, info = pl.impractical_lines(2400, [1500, 4000], 500, 4000, 0.9)
    assert lines == [WARN, "That is 60% of the 4,000 records left."] and info["share_left"] == 0.6
    lines, _ = pl.impractical_lines(300, [200, 400], 500, 480, 0.9)
    assert lines == ["That is 62% of the 480 records left."]


def test_impractical_approximate_note():
    note = " (approximate: from 3 of 50 draws; time limit reached)"
    lines, _ = pl.impractical_lines(2400, [1500, 4000], 500, 6000, 0.9, note)
    assert lines == [WARN + note]


def test_impractical_limit_set(tmp_path):
    import json

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.project import Project
    from wider.ui.app import create_app
    p = _deep(tmp_path)
    estimate(p, "validation", "main", B=20)
    run = lambda *a: CliRunner().invoke(cli_app, [*a, "--dir", str(p.root)])  # noqa: E731
    first = run("validation", "status", "--json")
    assert any(x.startswith("Settling whether the target 0.90") and "more than the 500 you set" in x
               for x in json.loads(first.output)["lines"])
    n = p.store.scalar("SELECT COUNT(*) FROM labels")
    r = run("protocol", "set", "recall.practical_labels", "0")
    assert r.exit_code != 0                                               # 0 is refused
    assert run("protocol", "set", "recall.practical_labels", "100000").exit_code == 0
    later = json.loads(run("validation", "status", "--json").output)["lines"]
    assert not any(x.startswith("Settling whether") for x in later)       # the new limit, with no new label
    assert p.store.scalar("SELECT COUNT(*) FROM labels") == n
    c = TestClient(create_app(Project.load(p.root)))
    r = c.post("/api/protocol/set", json={"path": "recall.practical_labels", "value": 0})   # the Settings table's route
    assert r.status_code == 400
    r = c.post("/api/protocol/set", json={"path": "recall.practical_labels", "value": 1000})
    assert r.status_code == 200, r.text
    assert Project.load(p.root).config.protocol.recall.practical_labels == 1000


def test_impractical_uses_labels_only(tmp_path):
    p = _deep(tmp_path)
    s = pl.suggest(p, "validation", "main", seconds=120)
    assert s["impractical"] is not None
    p.store.execute("UPDATE ranking_rows SET score = 1 - score")
    p.store.execute("UPDATE screen_decisions SET votes='[0.99]', cell='x'")
    assert pl.suggest(p, "validation", "main", seconds=120) == s


def test_impractical_never_blocks(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app
    p = _deep(tmp_path)
    run = lambda *a: CliRunner().invoke(cli_app, [*a, "--dir", str(p.root)])  # noqa: E731
    r = run("recall", "main", "--sample", "validation", "-B", "20")
    assert r.exit_code == 0 and "may be impractical" in r.output
    assert run("validation", "status").exit_code == 0
    assert TestClient(create_app(p)).get("/api/recall", params={"protocol": "main", "sample": "validation",
                                                                 "B": 20}).status_code == 200


def test_suggest_with_unlabelled_sample_records(tmp_path):
    """Scenario "Before every sampled record is labelled" (label-completion-and-sealing): the suggested records added,
    10 of them unlabelled: `wider recall main` gives no estimate and records no look, the refusal names the 10;
    `wider validation status` repeats look 1's suggestion; once the 10 are labelled the estimate is look 2."""
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p, first, added = _look1_and_suggested(tmp_path)
    left = _unlabelled(p)
    for rid in left[10:]:
        label(p, "validation", rid, "r1", "include" if _rel(rid) else "exclude")
    run = lambda *a: CliRunner().invoke(cli_app, [*a, "--dir", str(p.root)])  # noqa: E731
    looks = p.store.scalar("SELECT SUM(n) FROM validation_looks")
    stored = p.store.get_meta("last_validation_lines:validation")
    r = run("recall", "main", "-B", "20")
    assert r.exit_code == 1
    assert f"10 of {120 + added} records have no label" in " ".join(r.output.split())
    assert p.store.scalar("SELECT SUM(n) FROM validation_looks") == looks
    assert p.store.get_meta("last_validation_lines:validation") == stored
    sg = [x for x in first["validation_lines"] if x.startswith(("More labels could settle it.", "Only labelling all"))]
    st = run("validation", "status")
    assert st.exit_code == 0 and sg and all(x in st.output.splitlines() for x in sg)
    for rid in left[:10]:
        label(p, "validation", rid, "r1", "include" if _rel(rid) else "exclude")
    assert estimate(p, "validation", "main", B=20)["look"]["index"] == 2
