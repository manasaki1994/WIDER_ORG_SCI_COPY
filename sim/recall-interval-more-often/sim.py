"""How often each validation design and interval gives the researcher an interval, and whether it holds.

Synthetic corpora only (numpy, fixed seeds): no project, no review data, no network. The corpus, ranker and LLM screen
are those of the stratified-vs-random study (28 Oct 2026): N records at prevalence p, a binormal ranker with AUC a, an
LLM that keeps a relevant record with probability r, its misses random or rank-linked (correlation 0.5).

Designs (all at a total of B labels):
  shipped   S1 1-100 census, S2 101-300, S3 301-1000, S4 1001-3000, S5 3001+; the other B-100 labels split
            70:60:46:40 (WIDER's default bands), systematic within band (wider.sample.design.sys_sample).
  sqrt      the same bands, the B-100 labels in proportion to sqrt(N_h), at least 20 per band.
  prop      the same bands, the B-100 labels in proportion to N_h, at least 20 per band.
  ropt      the planner's recall-optimal rule (wider.sample.plan.allocate) with its default rate guesses.
  srs       one band over the whole ranking: a simple random sample of B records (systematic over a random order).
  fixed     the same bands, B-100 split 20:26:43:127 whatever the corpus size (the sqrt split at 20,000 records).
  top+rest  S1 1-100 census and one band 101 to the end with the other B-100 labels.
  3band     S1 1-100 census, S2 101-1000, S3 1001 to the end; B-100 in proportion to sqrt(N_h), at least 20.
Intervals (95%):
  KG        wider.recall.uncertainty.stratified_uncertainty arithmetic (Korn-Graubard, band rule), as shipped.
  AT        wider.recall.anytime.recall_bounds, the exact bounds valid at every look (shipped; they judge the target).
  KG|AT     KG where it is given, else AT (a data-dependent choice; its coverage is measured, not assumed).

Usage: python sim.py OUT.jsonl [--reps 300] [--procs 2] [--N 20000] [--auc 0.90] [--budgets 316,632] [--designs a,b]
       [--check]
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wider.recall import anytime  # noqa: E402
from wider.recall.uncertainty import stratified_uncertainty  # noqa: E402
from wider.sample.plan import DEFAULT_GUESS, allocate  # noqa: E402

anytime.cell_bounds = functools.lru_cache(maxsize=200_000)(anytime.cell_bounds)   # same answers, many repeats

EDGES = [0, 100, 300, 1000, 3000]
SHIPPED = np.array([70, 60, 46, 40])
LEVEL = 0.95
DESIGNS = ("shipped", "sqrt", "prop", "ropt", "srs", "fixed", "top+rest", "3band")
FIXED = np.array([20, 26, 43, 127])   # the square-root split at 20,000 records, kept as fixed numbers
FLOOR = 20


def _largest_remainder(x: np.ndarray, total: int) -> np.ndarray:
    n = np.floor(x).astype(int)
    n[np.argsort(-(x - n))[: total - n.sum()]] += 1
    return n


def _floored(w: np.ndarray, total: int, Nh: np.ndarray, floor: int) -> np.ndarray:
    lo = np.minimum(floor, Nh)
    fixed = np.zeros(len(w), bool)
    while True:
        rest = total - lo[fixed].sum()
        x = np.where(fixed, lo, rest * w / w[~fixed].sum())
        x = np.minimum(x, Nh)
        under = (~fixed) & (x < lo)
        if not under.any():
            break
        fixed |= under
    return np.minimum(_largest_remainder(x.astype(float), total), Nh)


def alloc(design: str, B: int, Nh: np.ndarray) -> np.ndarray:
    """Labels for the sampled bands S2..S5 (Nh their sizes) at a total of B with S1 in full."""
    rest = B - 100
    if design in ("shipped", "fixed"):
        w = SHIPPED if design == "shipped" else FIXED
        return np.minimum(_largest_remainder(w * rest / w.sum(), rest), Nh)
    if design == "sqrt":
        return _floored(np.sqrt(Nh.astype(float)), rest, Nh, FLOOR)
    if design == "prop":
        return _floored(Nh.astype(float), rest, Nh, FLOOR)
    if design == "ropt":
        bands = [{"name": "S1", "N": 100, "census": True}] + [
            {"name": f"S{i + 2}", "N": int(n), "census": False, "n": None} for i, n in enumerate(Nh)]
        rates = {f"S{i + 2}": DEFAULT_GUESS[i + 1] for i in range(len(Nh))}
        a = allocate("recall-optimal", B, bands, rates, 10)
        return np.array([a[f"S{i + 2}"] for i in range(len(Nh))])
    raise ValueError(design)


def sys_idx(Nh: int, n: int, rng) -> np.ndarray:
    if n >= Nh:
        return np.arange(Nh)
    k = Nh / n
    return (rng.uniform(0, k) + np.arange(n) * k).astype(int)


def corpus(N, p, auc, rng):
    R = max(1, round(N * p))
    d = math.sqrt(2) * norm.ppf(auc)
    rel = np.zeros(N, bool)
    rel[:R] = True
    score = rng.normal(0, 1, N) + d * rel
    order = np.argsort(-score)
    rel, score = rel[order], score[order]
    u = rng.random(N)
    zr = np.zeros(N)
    s = score[rel]
    s = (s - s.mean()) / (s.std() + 1e-12)
    zr[rel] = 0.5 * s + math.sqrt(0.75) * rng.normal(0, 1, rel.sum())
    return rel, u, zr


def kept_of(rel, u, zr, r, errors):
    if errors == "random":
        return rel & (u < r)
    return rel & (zr > norm.ppf(1 - r))


def draw(design: str, rel, kept, B, rng):
    """[(name, N_h, kept_h, rel[idx], kept[idx])] per band; kept_h is the band's number of records the screen kept
    (WIDER knows it: every frame record is screened)."""
    N = len(rel)
    if design == "srs":
        idx = rng.permutation(N)[sys_idx(N, B, rng)]
        return [("S", N, int(kept.sum()), rel[idx], kept[idx])]
    if design in ("top+rest", "3band"):
        edges = [0, 100, N] if design == "top+rest" else [0, 100, 1000, N]
        Nh = np.diff(edges)[1:]
        nh = _floored(np.sqrt(Nh.astype(float)), B - 100, Nh, FLOOR)
        out = [("S1", 100, int(kept[:100].sum()), rel[:100], kept[:100])]
        for b in range(1, len(edges) - 1):
            lo, hi = edges[b], edges[b + 1]
            idx = sys_idx(int(Nh[b - 1]), int(nh[b - 1]), rng)
            out.append((f"S{b + 1}", int(Nh[b - 1]), int(kept[lo:hi].sum()), rel[lo + idx], kept[lo + idx]))
        return out
    edges = EDGES + [N]
    Nh = np.array([edges[b + 1] - edges[b] for b in range(1, 5)])
    nh = alloc(design, B, Nh)
    out = [("S1", 100, int(kept[:100].sum()), rel[:100], kept[:100])]
    for b in range(1, 5):
        lo, hi = edges[b], edges[b + 1]
        idx = sys_idx(int(Nh[b - 1]), int(nh[b - 1]), rng)
        out.append((f"S{b + 1}", int(Nh[b - 1]), int(kept[lo:hi].sum()), rel[lo + idx], kept[lo + idx]))
    return out


def kg(bands):
    rows = []
    for name, Nh, _, r, k in bands:
        w = Nh / len(r)
        rows += [{"band": name, "w": w, "human": "inc" if rr else "exc", "screen": "inc" if kk else "exc"}
                 for rr, kk in zip(r, k)]
    u = stratified_uncertainty(rows, {"inc"}, {"inc"}, LEVEL)
    return (u["recall"], *(u["interval"] or (None, None)))


def at(bands):
    cells = []
    for name, Nh, kh, r, k in bands:
        cells += [{"band": name, "decision": "kept", "N": kh, "t": int(k.sum()), "k": int((r & k).sum())},
                  {"band": name, "decision": "not kept", "N": Nh - kh, "t": int((~k).sum()), "k": int((r & ~k).sum())}]
    b = anytime.recall_bounds(cells, LEVEL)
    return b["low"], b["high"]


def run(cell):
    N, p, auc, budgets, reps, seed, designs = cell
    rng = np.random.default_rng([N, int(round(p * 1e4)), int(round(auc * 100)), seed])
    out = []
    for _ in range(reps):
        rel, u, zr = corpus(N, p, auc, rng)
        # the screen also keeps 5% of the irrelevant records, so the not-kept cells hold what a real screen leaves
        fp = rng.random(N) < 0.05
        for errors in ("random", "ranked"):
            for r in (0.90, 0.95):
                kept = kept_of(rel, u, zr, r, errors) | (fp & ~rel)
                true = (kept & rel).sum() / rel.sum()
                for B in budgets:
                    for d in designs:
                        bands = draw(d, rel, kept, B, rng)
                        R, klo, khi = kg(bands)
                        alo, ahi = at(bands)
                        nrel = int(sum(b[3].sum() for b in bands))
                        out.append([errors, r, B, d, float(true), R, klo, khi, alo, ahi, nrel])
    return {"N": N, "p": p, "auc": auc, "rows": out}


def check():
    """Every design draws exactly B labels."""
    rng = np.random.default_rng(1)
    rel, u, zr = corpus(20000, 0.02, 0.9, rng)
    kept = kept_of(rel, u, zr, 0.9, "random")
    for d in DESIGNS:
        bands = draw(d, rel, kept, 316, rng)
        assert sum(len(b[3]) for b in bands) == 316, d
    print("check: every design draws 316 labels")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--reps", type=int, default=300)
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--N", type=int, default=20000)
    ap.add_argument("--auc", type=float, default=0.90)
    ap.add_argument("--prev", default="0.005,0.02,0.05")
    ap.add_argument("--budgets", default="316,632")
    ap.add_argument("--chunks", type=int, default=4, help="seeded chunks per prevalence (parallel work units)")
    ap.add_argument("--designs", default=",".join(DESIGNS))
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        check()
        sys.exit()
    budgets = tuple(int(x) for x in a.budgets.split(","))
    per = math.ceil(a.reps / a.chunks)
    designs = tuple(a.designs.split(","))
    cells = [(a.N, float(p), a.auc, budgets, per, s, designs) for p in a.prev.split(",") for s in range(a.chunks)]
    with Pool(a.procs) as pool, open(a.out, "w") as f:
        for o in pool.imap_unordered(run, cells):
            f.write(json.dumps(o) + "\n")
            f.flush()
            print(o["N"], o["p"], len(o["rows"]), flush=True)
