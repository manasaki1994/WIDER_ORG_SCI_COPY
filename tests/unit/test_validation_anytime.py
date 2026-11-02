"""Exact anytime-valid bounds on recall and the model-based supplement (validation-sample-planner D15, D17).
All populations are synthetic and seeded; no project data."""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from wider.recall.anytime import _log_betabinom, cell_bounds, recall_bounds
from wider.recall.uncertainty import model_interval, model_interval_line, stratified_uncertainty
from wider.recall.validation import judge


def _cells(spec):
    """spec: [(band, N_kept, t_kept, k_kept, N_not, t_not, k_not)]."""
    out = []
    for b, nk, tk, kk, nn, tn, kn in spec:
        out += [{"band": b, "decision": "kept", "N": nk, "t": tk, "k": kk},
                {"band": b, "decision": "not kept", "N": nn, "t": tn, "k": kn}]
    return out


def test_cell_bounds_properties():
    # zero relevant in 200 of 10,000 at alpha 0.005: the design's figure
    assert cell_bounds(10000, 200, 0, 0.005) == (0, 412)
    # a census gives the count exactly; an empty cell gives nothing
    assert cell_bounds(300, 300, 3, 0.005) == (3, 3)
    assert cell_bounds(0, 0, 0, 0.05) == (0, 0)
    # the observed relevant are always inside, and the bound never passes N minus the observed non-relevant
    for N, t, k in [(50, 10, 2), (500, 50, 0), (500, 100, 40), (2000, 400, 7)]:
        lo, hi = cell_bounds(N, t, k, 0.01)
        assert k <= lo <= hi <= N - (t - k)
    # more labels narrow the set; a smaller alpha widens it
    assert cell_bounds(5000, 400, 0, 0.01)[1] < cell_bounds(5000, 100, 0, 0.01)[1]
    assert cell_bounds(5000, 100, 3, 0.001)[1] > cell_bounds(5000, 100, 3, 0.05)[1]
    with pytest.raises(ValueError):
        cell_bounds(10, 5, 6, 0.05)


def test_bounds_match_brute_force():
    cells = _cells([("S1", 12, 6, 4, 8, 4, 1), ("S2", 10, 3, 0, 15, 5, 0)])
    b = recall_bounds(cells, 0.9)
    sets = [range(c["low"], c["high"] + 1) for c in b["cells"]]
    ratios = []
    for rs in itertools.product(*sets):
        kept = sum(r for r, c in zip(rs, b["cells"]) if c["decision"] == "kept")
        tot = sum(rs)
        if tot:
            ratios.append(kept / tot)
    assert b["low"] == pytest.approx(min(ratios))
    assert b["high"] == pytest.approx(max(ratios))
    assert b["method"] == "anytime-hypergeometric" and b["alpha_cell"] == pytest.approx(0.1 / 4)


def _in_set(N, R, t, k, alpha):
    """Whether R is in the confidence sequence after (t, k), vectorised over arrays t and k."""
    j = R - k
    ok = (j >= 0) & (j <= N - t)
    lp0 = _log_betabinom(R, N, 0.5, 0.5)
    lpt = np.where(ok, _log_betabinom(np.clip(j, 0, None), np.maximum(N - t, 0), 0.5 + k, 0.5 + t - k), -np.inf)
    return (lpt - lp0 > math.log(alpha)) | (t == N)


def test_cell_bounds_monitored_coverage():
    alpha, reps = 0.05, 300
    rng = np.random.default_rng(7)
    for N in (50, 500, 5000):
        for R in sorted({0, 1, 5, N // 100, N // 2}):
            looks = np.arange(10, N + 1, 10)
            pops = np.zeros((reps, N), int)
            pops[:, :R] = 1
            pops = rng.permuted(pops, axis=1)
            ks = np.cumsum(pops, axis=1)[:, looks - 1]
            inside = _in_set(N, R, np.broadcast_to(looks, ks.shape), ks, alpha)
            miss = float((~inside.all(axis=1)).mean())
            assert miss <= alpha + 3 * math.sqrt(alpha * (1 - alpha) / reps), (N, R, miss)
    # the vectorised membership agrees with cell_bounds
    for t, k in [(10, 0), (100, 3), (250, 120)]:
        lo, hi = cell_bounds(500, t, k, alpha)
        for r in (lo - 1, lo, hi, hi + 1):
            if 0 <= r <= 500:
                assert bool(_in_set(500, r, np.array(t), np.array(k), alpha)) == (lo <= r <= hi)


def test_zero_band_bounds():
    # a deep band with no relevant paper still gives bounds; its unseen papers are named as the dominant cell
    cells = _cells([("S1", 120, 60, 30, 380, 60, 2), ("S2", 0, 0, 0, 3000, 120, 0)])
    b = recall_bounds(cells, 0.95)
    assert 0 < b["low"] < b["high"] <= 1
    s2 = [c for c in b["cells"] if c["band"] == "S2" and c["decision"] == "not kept"][0]
    assert s2["low"] == 0 and s2["high"] > 0
    assert b["dominant"]["band"] == "S2" and 0 < b["dominant"]["share"] <= 1


def _rows():
    """A labelled sample whose band S2 shows no relevant paper: no design-based interval."""
    rows = []
    for i in range(40):
        rows.append({"band": "S1", "w": 3.0, "human": "include" if i < 20 else "exclude",
                     "screen": "include" if i < 18 or i >= 30 else "exclude"})
    for _ in range(60):
        rows.append({"band": "S2", "w": 50.0, "human": "exclude", "screen": "exclude"})
    return rows


SPEC = [("S1", 50, 28, 18, 70, 12, 2), ("S2", 0, 0, 0, 3000, 60, 0)]


def test_exact_bounds_where_no_design_interval():
    u = stratified_uncertainty(_rows(), ("include",), ("include",), 0.95)
    assert u["interval"] is None and "no relevant paper" in u["reason"]
    b = recall_bounds(_cells(SPEC), 0.95)
    assert b["low"] is not None and b["high"] is not None and b["low"] <= b["high"]


def test_model_interval_supplement():
    cells = _cells(SPEC)
    m = model_interval(cells, 0.95, draws=2000, seed=3)
    assert 0 <= m["low"] <= m["high"] <= 1 and m["level"] == 0.95
    assert m == model_interval(cells, 0.95, draws=2000, seed=3)
    line = model_interval_line(m)
    assert line.startswith("Supplementary, model-based (an assumption: a Jeffreys prior")
    assert "it does not decide the target" in line


def test_model_interval_is_supplementary():
    cells = _cells(SPEC)
    b = recall_bounds(cells, 0.95)
    m = model_interval(cells, 0.95, seed=1)
    for target in (0.05, 0.5, 0.95):
        verdict = judge(b, target)
        assert verdict == judge(dict(b), target)   # the model interval is never read
        assert judge({**b, "model_interval": m}, target) == verdict
    assert judge(b, None) is None
    assert judge({"low": 0.9, "high": 1.0}, 0.8) == "met"
    assert judge({"low": 0.1, "high": 0.7}, 0.8) == "not met"
    assert judge({"low": 0.7, "high": 0.9}, 0.8) == "cannot tell"


def _flip(p, n=1):
    """Change the label of the first ``n`` included sample records: a new label state. A look sealed them, so each
    change is a correction with a reason (label-completion-and-sealing D2)."""
    from wider.sample.labels import label

    for r in p.store.query("SELECT l.record_id, l.reviewer_id, s.name FROM labels l JOIN samples s ON "
                           "s.sample_id=l.sample_id WHERE l.decision='include' ORDER BY l.record_id LIMIT ?", (n,)):
        label(p, r["name"], r["record_id"], r["reviewer_id"], "exclude", correction_reason="misread on first pass")


def test_looks_counted_by_label_state(tmp_path):
    from tests.unit.test_validation_suggest import _drawn
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path)
    e = estimate(p, "validation", "main", B=50)
    assert e["look"]["index"] == 1 and e["bounds"]["method"] == "anytime-hypergeometric"
    assert {"low", "high", "level", "cells"} <= set(e["bounds"]) and e["outcome"]["basis"] == "exact"
    # the same labels again are the same look
    assert estimate(p, "validation", "main", B=50)["look"]["index"] == 1
    _flip(p)
    assert estimate(p, "validation", "main", B=50)["look"]["index"] == 2


def test_no_look_limit(tmp_path):
    from tests.unit.test_validation_suggest import _drawn
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path)
    for i in range(1, 7):
        e = estimate(p, "validation", "main", B=20)
        assert e["look"]["index"] == i and e["outcome"]["outcome"] in ("met", "not_met", "cannot_tell")
        assert not any("look limit" in x or "last look" in x for x in e["lines"])
        _flip(p)


def test_method_sentences(tmp_path):
    from tests.unit.test_validation_suggest import _drawn
    from wider.recall import validation as va
    from wider.recall.estimate import estimate

    p = _drawn(tmp_path)
    e = estimate(p, "validation", "main", B=20)
    lines = e["validation_lines"]
    assert any(x.startswith("Exact bounds valid at every look (") and "Waudby-Smith and Ramdas 2020" in x
               for x in lines)
    assert va.FIRST_DRAW in lines
    d = e["bounds"]["dominant"]
    assert any(x.startswith(f"Band {d['band']} (") and "its share of the bound is" in x for x in lines)


def test_fresh_sample_starts_at_look_1(tmp_path):
    from tests.unit.test_validation_suggest import _drawn, _rel
    from wider.recall.estimate import estimate
    from wider.sample.design import draw_probability
    from wider.sample.labels import label

    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=20)
    _flip(p)
    assert estimate(p, "validation", "main", B=20)["look"]["index"] == 2
    draw_probability(p, name="v2", fresh_from="validation")
    for rid in [r["record_id"] for r in p.store.query(
            "SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='v2'")]:
        label(p, "v2", rid, "r1", "include" if _rel(rid) else "exclude")
    e = estimate(p, "v2", "main", B=20)
    assert e["look"]["index"] == 1
    assert estimate(p, "validation", "main", B=20)["look"]["index"] == 2


# The adaptive path on the real functions (D15): look, `suggest`, `extend_probability(..., "suggested")`, look again,
# up to 8 looks, stopping on met, not met, or a suggestion that settles nothing. The truth is fixed just below the
# target, so any "met" is a false one. Two scenarios: low prevalence across the ranking, and relevant papers
# concentrated at the top with a large deep band of rare ones.
FALSE_MET_SCENARIOS = {
    "low_prevalence": {"N": 3000, "top": 600, "p_top": 0.03, "p_deep": 0.01, "keep_deep": 0.3, "target": 0.8,
                       "bands": [("S1", 1, 600, 60), ("S2", 601, None, 80)]},
    "imbalanced": {"N": 3000, "top": 200, "p_top": 0.2, "p_deep": 0.004, "keep_deep": 0.0, "target": 0.85,
                   "bands": [("S1", 1, 200, 50), ("S2", 201, 1000, 40), ("S3", 1001, None, 40)]},
}


def _truth(sc):
    import random

    rng = random.Random(11)
    rel = {r for r in range(1, sc["N"] + 1) if rng.random() < (sc["p_top"] if r <= sc["top"] else sc["p_deep"])}
    kept = {r for r in rel if r <= sc["top"] or rng.random() < sc["keep_deep"]}
    return rel, kept


def _false_met_rep(args):
    """One repetition: True when some look says "met" (the truth is below the target)."""
    import tempfile
    from pathlib import Path

    from tests.unit.test_validation_plan import Band, _ranked
    from wider.recall.estimate import estimate
    from wider.sample.design import draw_probability, extend_probability
    from wider.sample.labels import label
    from wider.screen.decisions import set_decision

    key, rep = args
    sc = FALSE_MET_SCENARIOS[key]
    rel, kept = _truth(sc)
    with tempfile.TemporaryDirectory() as d:
        p = _ranked(Path(d), n=sc["N"], bands=[Band(name=b, start=s, end=e, n=n) for b, s, e, n in sc["bands"]])
        p.config.protocol = p.config.protocol.set("recall.target", sc["target"]).set("recall.suggest_seconds", 1)
        p.save()
        for r in range(1, sc["N"] + 1):
            set_decision(p, "main", r, "include" if r in kept else "exclude")
        draw_probability(p, seed=1000 + rep)
        for _ in range(8):
            for x in p.store.query("SELECT sr.record_id FROM sample_rows sr LEFT JOIN labels l ON l.sample_id="
                                   "sr.sample_id AND l.record_id=sr.record_id WHERE l.record_id IS NULL"):
                label(p, "validation", x["record_id"], "r1", "include" if x["record_id"] in rel else "exclude")
            o = estimate(p, "validation", "main", B=20, seed=rep)["outcome"]["outcome"]
            if o != "cannot_tell":
                return o == "met"
            try:
                extend_probability(p, "validation", "suggested")
            except ValueError:   # the suggestion settles nothing, or the band has no records left: the researcher stops
                return False
    return False


@pytest.mark.slow
@pytest.mark.parametrize("key", sorted(FALSE_MET_SCENARIOS))
def test_anytime_false_met_rate(key):
    import os
    from concurrent.futures import ProcessPoolExecutor

    sc = FALSE_MET_SCENARIOS[key]
    rel, kept = _truth(sc)
    assert len(kept) / len(rel) < sc["target"]
    reps = int(os.environ.get("WIDER_FALSE_MET_REPS", "1000"))
    with ProcessPoolExecutor() as ex:
        false_met = sum(ex.map(_false_met_rep, [(key, i) for i in range(reps)], chunksize=10))
    assert false_met / reps <= 1 - 0.95 + 0.015, (key, false_met, reps)
