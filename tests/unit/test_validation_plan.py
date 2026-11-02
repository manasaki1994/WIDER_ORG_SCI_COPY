"""The sample planner and percentile bands (validation-sample-planner D1, D3). Synthetic values only."""
from __future__ import annotations

import pytest

from wider.core.protocol import ReviewProtocol, SamplingBand
from wider.sample.design import resolve_bands

PCT = [{"name": "S1", "start_pct": 0, "end_pct": 5, "n": None}, {"name": "S2", "start_pct": 5, "end_pct": 25, "n": 40},
       {"name": "S3", "start_pct": 25, "end_pct": 100, "n": 60}]


def test_percentile_bands_resolved():
    bands = [SamplingBand(**b) for b in PCT]
    out, notes = resolve_bands(bands, 1000)
    assert [(b["start"], b["end"]) for b in out] == [(1, 50), (51, 250), (251, 1000)]
    assert out[0]["start_pct"] == 0 and out[2]["end_pct"] == 100 and not notes
    # a tiny ranking: the 0-5% band holds no rank and is left out with a note; the last band still ends at n
    out, notes = resolve_bands(bands, 10)
    assert [b["name"] for b in out] == ["S2", "S3"] and out[-1]["end"] == 10
    assert len(notes) == 1 and "S1" in notes[0]
    # rank bands pass through unchanged
    rk = [SamplingBand(name="A", start=1, end=10, n=None), SamplingBand(name="B", start=11, end=None, n=5)]
    assert [(b["start"], b["end"]) for b in resolve_bands(rk, 50)[0]] == [(1, 10), (11, None)]


def test_percentile_bands_validated():
    p = ReviewProtocol()
    assert p.set("sampling.bands", PCT).sampling.bands[1].is_pct
    bad = [
        [{"name": "S1", "start_pct": 5, "end_pct": 100, "n": 5}],                       # does not start at 0
        [{"name": "S1", "start_pct": 0, "end_pct": 50, "n": 5}],                        # does not end at 100
        [{"name": "S1", "start_pct": 0, "end_pct": 40, "n": 5},
         {"name": "S2", "start_pct": 50, "end_pct": 100, "n": 5}],                      # gap
        [{"name": "S1", "start_pct": 0, "end_pct": 0, "n": 5},
         {"name": "S2", "start_pct": 0, "end_pct": 100, "n": 5}],                       # empty band
        [{"name": "S1", "start": 1, "end": 10, "n": None},
         {"name": "S2", "start_pct": 10, "end_pct": 100, "n": 5}],                      # mixed
    ]
    for b in bad:
        with pytest.raises(ValueError):
            p.set("sampling.bands", b)


def test_plan_fields_bounded(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.core.project import Project

    p = ReviewProtocol()
    assert p.set("sampling.plan_sims", "1000").sampling.plan_sims == 1000
    for path, val in [("sampling.plan_sims", "3000"), ("sampling.plan_power", "1"), ("sampling.plan_seconds", "0"),
                      ("sampling.min_per_band", "-1")]:
        with pytest.raises(ValueError):
            p.set(path, val)
    proj = Project.init(tmp_path / "p", platforms=["scopus"])
    for path, val in [("sampling.plan_sims", "3000"), ("sampling.plan_power", "1")]:
        r = CliRunner().invoke(app, ["protocol", "set", path, val, "--dir", str(proj.root)])
        assert r.exit_code != 0, r.output
    assert Project.load(proj.root).config.protocol.sampling.plan_sims == 400


# ------------------------------------------------------------------------------------------------ the planner
from wider.core import journal  # noqa: E402
from wider.core.models import SamplingBand as Band  # noqa: E402
from wider.core.project import Project  # noqa: E402
from wider.sample import plan as pl  # noqa: E402

RATES = "S1=0.5,S2=0.1,S3=0.01"


def _ranked(tmp_path, n=2000, bands=None, name="p"):
    """A synthetic project with ``n`` ranked records (no text, no thin record, no benchmark paper)."""
    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.config.bands = bands or [Band(name="S1", start=1, end=100, n=50), Band(name="S2", start=101, end=500, n=70),
                               Band(name="S3", start=501, end=None, n=60)]
    p.save()
    st = p.store
    st.executemany("INSERT INTO records(record_id, cluster_id, key, title, abstract_words, thin) VALUES(?,?,?,?,?,0)",
                   [(i, i, f"k{i}", f"Record {i}", 200) for i in range(1, n + 1)])
    st.execute("INSERT INTO rankings(ranking_id,name,set_name,anchors_version,top_n,n,md5,created_at) "
               "VALUES(1,'r1','fake',1,100,?,'x','2026-01-01')", (n,))
    st.executemany("INSERT INTO ranking_rows(ranking_id,record_id,rank,score) VALUES(1,?,?,?)",
                   [(i, i, 1 - i / n) for i in range(1, n + 1)])
    st.set_meta("default_ranking", "r1")
    st.commit() if hasattr(st, "commit") else None
    return p


def _mid(p, rule):
    return next(r for r in p["rows"] if r["rule"] == rule and r["rates"] == "middle")


def test_four_allocations_sum_to_budget(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, sims=100)
    rules = {r["rule"] for r in p["rows"]}
    assert rules == {"current", "proportional", "recall-optimal", "top band in full"}
    for r in p["rows"]:
        assert r["total"] == 300 and min(r["allocation"].values()) >= 10
        assert r["expected_relevant"] > 0 and 0 <= r["p_interval"] <= 1
    top = _mid(p, "top band in full")["allocation"]
    assert top["S1"] == 100 and top["S2"] + top["S3"] == 200
    # proportional: 100/400/1500 of 2000 -> 15, 60, 225
    assert _mid(p, "proportional")["allocation"] == {"S1": 15, "S2": 60, "S3": 225}
    assert pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, sims=100)["rows"] == p["rows"]


def test_largest_remainder_tie_goes_to_higher_band():
    assert pl._round({"A": 1.5, "B": 1.5}, 3, ["A", "B"]) == {"A": 2, "B": 1}


def test_plan_sensitivity_rows(tmp_path):
    p = pl.plan(_ranked(tmp_path), budget=300, rates=RATES, expected_recall=0.9, sims=50)
    for rule in ("current", "recall-optimal"):
        assert sorted(r["rates"] for r in p["rows"] if r["rule"] == rule) == ["high", "low", "middle"]
    assert p["rates"]["low"]["S1"] == 0.25 and p["rates"]["high"]["S1"] == 1.0 and p["rates"]["high"]["S3"] == 0.02
    assert pl.SENSITIVITY_LINE in pl.plan_lines(p)


def test_plan_own_allocation(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, allocation="S1=100,S2=100,S3=100", sims=50)
    assert _mid(p, "yours")["allocation"] == {"S1": 100, "S2": 100, "S3": 100}
    for alloc, msg in [("S1=100,S2=100,S3=90", "your allocation sums to 290, not the budget of 300"),
                       ("S1=100,S2=195,S3=5", "S3 needs at least 10"), ("S1=100,S2=100,S9=100", "no band S9")]:
        with pytest.raises(pl.PlanError, match=msg):
            pl.plan(proj, budget=300, rates=RATES, allocation=alloc, sims=10)


def test_top_band_already_census(tmp_path):
    proj = _ranked(tmp_path, bands=[Band(name="S1", start=1, end=100, n=None), Band(name="S2", start=101, end=500, n=70),
                                    Band(name="S3", start=501, end=None, n=60)])
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, sims=50)
    for r in p["rows"]:
        assert r["allocation"]["S1"] == 100 and r["allocation"]["S2"] + r["allocation"]["S3"] == 200
    assert _mid(p, "top band in full")["note"] == "same as recall-optimal: the top band is already a census"


def test_budget_below_census(tmp_path):
    proj = _ranked(tmp_path, bands=[Band(name="S1", start=1, end=100, n=None), Band(name="S2", start=101, end=None, n=70)])
    with pytest.raises(pl.PlanError, match=r"the budget of 80 is below the 100 records of the census bands \(S1\); "
                                           "raise the budget or make S1 a sampled band"):
        pl.plan(proj, budget=80, rates="S1=0.5,S2=0.1")


def test_chance_of_clear_answer(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, target=0.85, sims=100)
    for r in p["rows"]:
        assert r["p_clear"] == pytest.approx(r["p_met"] + r["p_not_met"])
    best = max((r for r in p["rows"] if r["rates"] == "middle"), key=lambda r: r["p_clear"])
    assert _mid(p, p["recommended"])["p_clear"] == best["p_clear"]
    assert _mid(p, "current")["line"].startswith("if recall is 0.9: a clear answer in ")


def test_plan_without_target_said(tmp_path):
    p = pl.plan(_ranked(tmp_path), budget=300, rates=RATES, expected_recall=0.9, sims=50)
    assert pl.NO_TARGET_LINE in p["lines"] and p["recommended"]


def test_plan_needs_budget_target_or_precision(tmp_path):
    proj = _ranked(tmp_path)
    with pytest.raises(pl.PlanError, match="give a budget, or set a target or a margin to search for one"):
        pl.plan(proj)
    assert proj.config.protocol.recall.precision is None


def test_plan_refusals_no_ranking(tmp_path):
    proj = Project.init(tmp_path / "e", platforms=["scopus"])
    with pytest.raises(pl.PlanError, match="no ranking yet: run wider rank run first"):
        pl.plan(proj, budget=300)
    ranked = _ranked(tmp_path, name="q")
    p = pl.plan(ranked, budget=300, rates=RATES, sims=20)
    a = pl.apply_plan(ranked, p, "recall-optimal")
    assert not any("keeps its own bands" in x for x in a["lines"])
    assert [b.n for b in Project.load(ranked.root).config.bands] == list(_mid(p, "recall-optimal")["allocation"].values())


def test_plan_suggested_margin(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.95, target=0.85, sims=20)
    assert ("Suggested margin: about 0.10 (the expected recall 0.95 minus the target 0.85); a wider margin cannot tell "
            "met from not met.") in p["lines"]
    assert Project.load(proj.root).config.protocol.recall.precision is None
    for kw in ({"expected_recall": 0.84, "target": 0.85}, {"expected_recall": 0.95}):
        assert not any(x.startswith("Suggested margin") for x in pl.plan(proj, budget=300, rates=RATES, sims=20, **kw)["lines"])


def _fake_labels(monkeypatch, labs):
    monkeypatch.setattr(pl, "_labels", lambda project, primary: labs)


def test_rates_source_is_said(tmp_path, monkeypatch):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, sims=20, expected_recall=0.9)
    assert p["lines"][0].startswith("Expected inclusion rates: a default guess (S1 0.30, S2 0.05, S3 0.01), not from "
                                    "your data; give your own with --rates")
    assert pl.plan(proj, budget=300, rates=RATES, sims=20)["lines"][0] == "Expected inclusion rates: your own (--rates)."
    _fake_labels(monkeypatch, [{"rank": 5, "w": 2.0, "rel": True}, {"rank": 50, "w": 2.0, "rel": False},
                               {"rank": 200, "w": 6.0, "rel": False}])
    line = pl.plan(proj, budget=300, sims=20, expected_recall=0.9)["lines"][0]
    assert line == ("Expected inclusion rates: from the 3 validation labels so far (no labelled record falls in S3: a "
                    "default guess there).")


def test_rates_mapped_by_rank(tmp_path, monkeypatch):
    # the labels sit under the planned bands by rank, whatever bands they were drawn under
    proj = _ranked(tmp_path, bands=[Band(name="A", start=1, end=40, n=20), Band(name="B", start=41, end=None, n=50)])
    _fake_labels(monkeypatch, [{"rank": r, "w": 1.0, "rel": r <= 10} for r in range(1, 81)])
    p = pl.plan(proj, budget=200, sims=20, expected_recall=0.9)
    assert p["rates"]["middle"]["A"] == pytest.approx(10 / 40)
    assert p["rates"]["middle"]["B"] == pytest.approx(0.5 / 40)    # no relevant label: half a paper over n
    assert p["rates"]["low"]["A"] < 0.25 < p["rates"]["high"]["A"]
    assert p["rates"]["low"]["B"] < p["rates"]["high"]["B"]


def test_smallest_budget_for_decision(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, target=0.6, rates=RATES, expected_recall=0.95, sims=80)
    s = p["search"]
    assert s["reached"] and s["budget"] % 10 == 0 and s["low"] <= s["budget"] <= s["high"]
    assert sum(s["allocation"].values()) == s["budget"] and p["recommended"] == s["rule"]
    assert any(x.startswith(f"About {s['budget']} labels give a clear answer in 80% of the simulated samples "
                            "(sampling.plan_power;") for x in p["lines"])
    # the found budget does give a clear answer that often
    row = _mid(pl.plan(proj, budget=s["budget"], target=0.6, rates=RATES, expected_recall=0.95, sims=80), s["rule"])
    assert row["p_clear"] >= 0.6


def test_decision_out_of_reach(tmp_path):
    # exact bounds always decide on a census, so a total always exists; near the target it costs most of the frame,
    # and the output says that more labels are not the answer
    proj = _ranked(tmp_path, n=600)
    p = pl.plan(proj, target=0.9, rates=RATES, expected_recall=0.9, sims=40)
    assert p["search"]["budget"] > 300
    assert ("That is more than half of the 600 frame records: recall is expected near the target. A clear answer "
            "needs higher screening recall (your rule, or a revised prompt) rather than more labels.") in p["lines"]
    far = pl.plan(proj, target=0.6, rates=RATES, expected_recall=0.95, sims=40)
    assert not any(x.startswith("That is more than half") for x in far["lines"])


def test_smallest_budget_for_precision(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, precision=0.2, rates=RATES, expected_recall=0.9, sims=60)
    s = p["search"]
    assert s["reached"] and s["budget"] % 10 == 0
    assert any(x.startswith(f"About {s['budget']} labels give a margin of at most 0.2") for x in p["lines"])


def test_precision_out_of_reach(tmp_path):
    proj = _ranked(tmp_path, n=600)
    p = pl.plan(proj, precision=0.0001, rates=RATES, expected_recall=0.9, sims=20)
    assert p["search"]["reached"] is False
    assert any(x.startswith("a margin of 0.0001 is out of reach") for x in p["lines"])


def test_plan_precision_is_what_if(tmp_path):
    proj = _ranked(tmp_path)
    before = len(journal.changes(proj))
    pl.plan(proj, budget=300, precision=0.1, rates=RATES, sims=20)
    again = Project.load(proj.root)
    assert again.config.protocol.recall.precision is None and len(journal.changes(again)) == before


def test_apply_writes_bands_and_history(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, sims=20)
    alloc = _mid(p, "proportional")["allocation"]
    a = pl.apply_plan(proj, p, "proportional", author="ann")
    assert a["lines"][0] == "protocol.sampling.bands = S1 15, S2 60, S3 225 (plan proportional)"
    again = Project.load(proj.root)
    assert [b.n for b in again.config.bands] == list(alloc.values())
    ch = journal.changes(again)[0]
    assert ch["kind"] == "config" and ch["summary"].startswith("bands from plan proportional") and ch["author"] == "ann"
    journal.undo(again, ch["change_id"], author="ann")
    assert [b.n for b in Project.load(proj.root).config.bands] == [50, 70, 60]
    with pytest.raises(pl.PlanError, match="no allocation nonsense in this plan"):
        pl.apply_plan(proj, p, "nonsense")


def test_apply_records_choice_against_advice(tmp_path):
    proj = _ranked(tmp_path)
    p = pl.plan(proj, budget=300, rates=RATES, expected_recall=0.9, target=0.8, sims=20)
    other = next(r for r in pl.RULES if r != p["recommended"])
    a = pl.apply_plan(proj, p, other)
    assert f"used: {other}; recommended: {p['recommended']}" in a["lines"]
    import json
    stored = json.loads(proj.store.get_meta("validation_plan"))
    assert stored["used"] == other and stored["recommended"] == p["recommended"]
    assert stored["inputs"]["budget"] == 300 and stored["inputs"]["target"] == 0.8


def test_plan_before_screening_result(tmp_path):
    p = pl.plan(_ranked(tmp_path), budget=300, rates=RATES, expected_recall=0.9, sims=20)
    assert not p["screening_result"] and p["protocol"] is None
    assert "No screening result yet: screening decisions are simulated from the expected recall 0.9." in p["lines"]


def test_plan_with_result_set(tmp_path):
    from wider.screen.decisions import set_decision

    proj = _ranked(tmp_path, n=600)
    for i in range(1, 601):
        set_decision(proj, "P", i, "include" if i <= 150 else "exclude")
    p = pl.plan(proj, budget=200, rates=RATES, expected_recall=0.9, sims=20)
    assert p["screening_result"] and p["protocol"] == "P"
    assert any(x.startswith("Screening decisions from P, set ") for x in p["lines"])
    with pytest.raises(pl.PlanError):
        pl.plan(proj, budget=200, rates=RATES, sims=20, protocol="nope")


def test_plan_power_used(tmp_path):
    proj = _ranked(tmp_path)
    proj.config.protocol = proj.config.protocol.set("sampling.plan_power", 0.5)
    proj.save()
    lo = pl.plan(proj, target=0.6, rates=RATES, expected_recall=0.95, sims=60)
    assert lo["plan_power"] == 0.5 and any("in 50% of the simulated samples" in x for x in lo["lines"])
    proj.config.protocol = proj.config.protocol.set("sampling.plan_power", 0.9)
    proj.save()
    hi = pl.plan(proj, target=0.6, rates=RATES, expected_recall=0.95, sims=60)
    assert hi["search"]["budget"] >= lo["search"]["budget"]


@pytest.mark.slow
def test_plan_speed_20000(tmp_path):
    import time
    proj = _ranked(tmp_path, n=20000, bands=[Band(name="S1", start=1, end=500, n=100),
                                             Band(name="S2", start=501, end=5000, n=150),
                                             Band(name="S3", start=5001, end=None, n=150)])
    t = time.monotonic()
    pl.plan(proj, budget=400, rates="S1=0.3,S2=0.03,S3=0.002", target=0.8, expected_recall=0.9)
    t1 = time.monotonic() - t
    t = time.monotonic()
    pl.plan(proj, target=0.8, rates="S1=0.3,S2=0.03,S3=0.002", expected_recall=0.9)
    print(f"plan {t1:.1f}s, search {time.monotonic() - t:.1f}s")


def test_plan_cli_matches_api(tmp_path):
    import json

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    proj = _ranked(tmp_path)
    args = ["validation", "plan", "--budget", "300", "--rates", RATES, "--expected-recall", "0.9", "--sims", "30",
            "--dir", str(proj.root)]
    cli = CliRunner().invoke(cli_app, args + ["--json"])
    assert cli.exit_code == 0, cli.output
    c = TestClient(create_app(proj))
    api = c.post("/api/validation/plan", json={"budget": 300, "rates": RATES, "expected_recall": 0.9, "sims": 30}).json()
    assert json.loads(cli.output)["rows"] == api["rows"] and json.loads(cli.output)["output"] == api["output"]
    text = CliRunner().invoke(cli_app, args)
    assert text.exit_code == 0 and text.output.splitlines() == api["output"]
    # refusals: exit 1 and 400 with the same sentence, nothing written
    bad = CliRunner().invoke(cli_app, ["validation", "plan", "--dir", str(proj.root)])
    assert bad.exit_code == 1 and "give a budget, or set a target or a margin to search for one" in bad.output
    r = c.post("/api/validation/plan", json={})
    assert r.status_code == 400 and r.json()["detail"] == "give a budget, or set a target or a margin to search for one"
    # --apply and the route's apply write the same bands
    a = CliRunner().invoke(cli_app, args + ["--apply", "proportional"])
    assert a.exit_code == 0 and "protocol.sampling.bands = S1 15, S2 60, S3 225 (plan proportional)" in a.output
    assert [b.n for b in Project.load(proj.root).config.bands] == [15, 60, 225]
    r = c.post("/api/validation/plan", json={"budget": 300, "rates": RATES, "expected_recall": 0.9, "sims": 30,
                                             "apply": "proportional", "percentiles": "5,25"})
    assert r.status_code == 200 and r.json()["applied"]["bands"][0]["start_pct"] == 0
