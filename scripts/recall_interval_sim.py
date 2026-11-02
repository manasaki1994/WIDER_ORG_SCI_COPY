#!/usr/bin/env python3
"""Coverage study of WIDER's recall interval (change recall-interval-honest, owner answer a-20261024-094201-8c08).

Each scenario is a finite population split into score bands as WIDER samples them: a census band (every record
labelled, weight 1) and sampled bands drawn without replacement at falling rates (weights N_h/n_h). The relevant
records of a band are either kept or missed by the screening; true recall is the population share kept. Every
replicate draws one stratified sample, labels it (the human sees who is relevant) and asks
``wider.recall.uncertainty.stratified_uncertainty`` for the interval, exactly as ``estimate()`` does.

Reported per scenario and rule: how often an interval is shown, how often the shown intervals hold the true recall
(coverage; the target is about 0.95), the median width, and the share of replicates whose observed recall was 0 or 1.
Rules:
  none       no band rule: an interval whenever the variance is estimable (Korn–Graubard inside (0, 1),
             Kish's effective size at 0 or 1), no interval for a lonely sampled band or df < 1.
  band       the shipped rule (design D10): as none, and no interval when a sampled band (weight > 1) shows no
             relevant paper, because what that band hides is then invisible to the variance.
  ncap       the NCHS rule for comparison: the effective number capped at the raw count n, and n itself at 0 or 1
             (the unweighted Clopper–Pearson interval there).

Usage: python3 scripts/recall_interval_sim.py [--reps 2000] [--seed 20261024] [--scenario NAME ...] [--markdown]
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wider.recall.uncertainty import beta_interval, ratio_variance, stratified_uncertainty  # noqa: E402

INC = ("include",)
# name: (why it is here, [(band, N, relevant, kept_of_relevant, labelled)])
SCENARIOS = {
    "default": ("three bands, weights 1, 10, 75; true recall 0.85",
                [("S1", 20, 10, 10, 20), ("S2", 300, 30, 27, 30), ("S3", 3000, 60, 48, 40)]),
    "rare_thin": ("rare relevant papers; the thin bottom band holds a third of them",
                  [("S1", 30, 6, 6, 30), ("S2", 400, 8, 7, 40), ("S3", 4000, 12, 8, 40)]),
    "hidden_misses": ("every miss sits in a thinly sampled band that rarely shows a relevant paper",
                      [("S1", 50, 40, 40, 50), ("S3", 5000, 20, 0, 50)]),
    "small_strata": ("four bands with 5 labelled records each in the sampled bands; weights up to 1000",
                     [("S1", 10, 5, 5, 10), ("S2", 100, 10, 8, 5), ("S3", 1000, 20, 14, 5), ("S4", 5000, 20, 10, 5)]),
    "near_perfect": ("true recall 0.94; observed recall is often exactly 1",
                     [("S1", 20, 10, 10, 20), ("S2", 300, 30, 30, 30), ("S3", 3000, 60, 54, 40)]),
    "perfect": ("true recall 1 (boundary)",
                [("S1", 20, 10, 10, 20), ("S2", 300, 30, 30, 30), ("S3", 3000, 60, 60, 40)]),
    "zero": ("true recall 0 (boundary)",
             [("S1", 20, 10, 0, 20), ("S2", 300, 30, 0, 30), ("S3", 3000, 60, 0, 40)]),
    "near_zero": ("true recall 0.06; observed recall is often exactly 0",
                  [("S1", 20, 10, 1, 20), ("S2", 300, 30, 2, 30), ("S3", 3000, 60, 3, 40)]),
    "equal_weights": ("one sampled band, so the interval is the plain Clopper–Pearson one (sanity check)",
                      [("S2", 1000, 100, 85, 100)]),
    "many_relevant": ("large sample, hundreds of relevant papers, well inside (0, 1)",
                      [("S1", 100, 60, 58, 100), ("S2", 1000, 200, 170, 200), ("S3", 5000, 300, 210, 250)]),
    "unequal_extreme": ("weights 1, 50 and 500 with 10 and 20 labelled records in the sampled bands",
                        [("S1", 20, 10, 10, 20), ("S2", 500, 20, 18, 10), ("S3", 10000, 40, 28, 20)]),
    "thin_band_seen": ("as hidden_misses but the bottom band is sampled enough to show its relevant papers",
                       [("S1", 50, 40, 40, 50), ("S3", 5000, 20, 0, 500)]),
}


def draw(rng, pop):
    rows = []
    for band, N, r, k, take in pop:
        units = np.zeros(N, dtype=np.int8)        # 0 other, 1 kept relevant, 2 missed relevant
        units[:k] = 1
        units[k:r] = 2
        for u in units[rng.choice(N, take, replace=False)]:
            rows.append({"band": band, "w": N / take, "human": "include" if u else "exclude",
                         "screen": "include" if u == 1 else "exclude"})
    return rows


def ncap_interval(rows, level=0.95):
    """The NCHS variant: Korn–Graubard capped at n, n itself at 0 or 1 (for comparison only)."""
    from scipy.stats import t

    rel = [r for r in rows if r["human"] in INC]
    n = len(rel)
    if not n:
        return None
    R, v, lonely = ratio_variance(rows, INC, INC)
    df = n - len({r["band"] for r in rel})
    if 0 < R < 1:
        if lonely or df < 1:
            return None
        if v > 0:
            a = (1 - level) / 2
            factor = (t.ppf(a, n - 1) / t.ppf(a, df)) ** 2 if n > 1 else 1.0
            ne = min(R * (1 - R) / v * factor, n)
            return beta_interval(R * ne, ne, level)
    return beta_interval(R * n, n, level)


def run(name, reps, seed, level=0.95):
    _, pop = SCENARIOS[name]
    true = sum(p[3] for p in pop) / sum(p[2] for p in pop)
    rng = np.random.default_rng(seed)
    stats = {k: {"shown": 0, "covered": 0, "widths": [], "points": 0} for k in ("none", "band", "ncap")}
    boundary = no_relevant = 0
    for _ in range(reps):
        rows = draw(rng, pop)
        u = stratified_uncertainty(rows, INC, INC, level, band_rule=False)
        if u["recall"] is None:
            no_relevant += 1
            continue
        if u["recall"] in (0, 1):
            boundary += 1
        shipped = stratified_uncertainty(rows, INC, INC, level)   # with the band rule (design D10)
        cands = {"none": u["interval"], "band": shipped["interval"], "ncap": ncap_interval(rows, level)}
        for k, iv in cands.items():
            if iv is None:
                continue
            s = stats[k]
            s["shown"] += 1
            s["covered"] += iv[0] - 1e-12 <= true <= iv[1] + 1e-12
            s["widths"].append(iv[1] - iv[0])
            s["points"] += iv[1] - iv[0] < 1e-9
    out = {"scenario": name, "true": true, "reps": reps, "boundary": boundary / reps, "no_relevant": no_relevant / reps}
    for k, s in stats.items():
        out[k] = {"shown": s["shown"] / reps, "coverage": s["covered"] / s["shown"] if s["shown"] else None,
                  "width": statistics.median(s["widths"]) if s["widths"] else None, "points": s["points"]}
    return out


def _by_band(rows):
    bands: dict = {}
    for r in rows:
        bands.setdefault(r["band"], []).append(r)
    return bands


def fmt(x, nd=3):
    return "–" if x is None else f"{x:.{nd}f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    ap.add_argument("--markdown", action="store_true", help="print the table for design.md")
    a = ap.parse_args()
    names = a.scenario or list(SCENARIOS)
    results = [run(n, a.reps, a.seed) for n in names]
    if a.markdown:
        print("| scenario | true recall | observed 0 or 1 | without band rule: shown / coverage / width | band rule (shipped): shown / "
              "coverage / width | n cap: coverage |")
        print("|---|---|---|---|---|---|")
        for r in results:
            c, b, n = r["none"], r["band"], r["ncap"]
            print(f"| {r['scenario']} | {r['true']:.3f} | {r['boundary']:.2f} | {c['shown']:.2f} / {fmt(c['coverage'])} / "
                  f"{fmt(c['width'], 2)} | {b['shown']:.2f} / {fmt(b['coverage'])} / {fmt(b['width'], 2)} | "
                  f"{fmt(n['coverage'])} |")
        return
    for r in results:
        print(f"{r['scenario']:16s} true {r['true']:.3f}  boundary {r['boundary']:.2f}  no-relevant {r['no_relevant']:.3f}")
        for k in ("none", "band", "ncap"):
            s = r[k]
            print(f"    {k:8s} shown {s['shown']:.3f}  coverage {fmt(s['coverage'])}  median width {fmt(s['width'])}  "
                  f"points {s['points']}")


if __name__ == "__main__":
    main()
