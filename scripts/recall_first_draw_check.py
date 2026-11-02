#!/usr/bin/env python3
"""Exact check of the shipped recall bounds under WIDER's systematic first draw (change
stratified-sequential-recall-test, design D0, task 1.3). Synthetic bands only; no project, no network.

The shipped per-cell set (``wider.recall.anytime.cell_bounds``) drops the true R of a cell once the prior-posterior
ratio e_t = pi_0(R) / pi_t(R) reaches 1/alpha_c. Its proof assumes records drawn uniformly at random. WIDER's first
draw in a band is systematic (``wider.sample.design.sys_sample``: step N/n, one uniform start), so the first set S0 is
one of finitely many sets, each with the weight of its start interval; the labelling queue then shuffles S0, and
extensions are uniform among the records left in the band (``extend_probability``).

For one cell (band x screening decision) with N_c records and R_c relevant, this script computes exactly

    Q = E_sys[ P(cross inside) + (1 - P(cross inside)) * alpha_c * e_end ]

where "inside" is every look (one after each record) up to the point where S0 and any extension are fully labelled,
and e_end is the e-value there. From that point on the draws are uniform, e_t / e_end is a test martingale, and by
Ville's inequality a later crossing has chance at most alpha_c * e_end; so Q bounds the chance that the cell ever
drops its true R. Under a uniform first draw Q <= alpha_c by optional stopping (reported as the "uniform" column), so
Q <= alpha_c is the condition that the shipped guarantee holds under WIDER's draw. (Design D0 wrote
min(1, P_inside + alpha_c * e); that union bound exceeds alpha_c even for a uniform draw, so the check uses the
sharper bound above, which is equally valid; decided in design D10.)

The mixed queue: an extension of m records is made after j records of the band's S0 are labelled; the other S0
records and the extension are then shuffled together (as ``extend_probability`` reshuffles the unlabelled records).
P(cross inside) averages over where the extension lands and over its draws; Q is reported as the largest over j.
Probability mass the dynamic programme prunes (below 1e-13) is counted as a crossing, so Q is never understated.

Usage: python3 scripts/recall_first_draw_check.py [--frames 2000,20000] [--markdown] [--level 0.95]
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.special import betaln, gammaln
from scipy.stats import hypergeom

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wider.core.protocol import DEFAULT_BANDS  # noqa: E402
from wider.recall.anytime import PRIOR  # noqa: E402
from wider.sample.design import sys_sample  # noqa: E402

PRUNE = 1e-13
EXTENSIONS = (0, 10, 50, 300)
ARRANGEMENTS = ("ranked", "cluster", "random", "aligned")
REALISTIC = ("ranked", "cluster", "random")
# relevant share, share of the relevant kept by the screening, share of the others kept; per default band S2..S5
PROFILE = {"S2": (0.20, 0.97, 0.30), "S3": (0.06, 0.92, 0.10), "S4": (0.015, 0.85, 0.03), "S5": (0.003, 0.70, 0.005)}


class _Fixed:
    """A stand-in for ``random.Random`` whose ``uniform`` returns one chosen start (to enumerate sys_sample)."""

    def __init__(self, start: float):
        self.start = start

    def uniform(self, a, b):
        return self.start


def start_sets(N: int, n: int) -> list[tuple[tuple[int, ...], float]]:
    """Every set ``sys_sample`` can draw from positions 0..N-1, with the probability of its start interval. The set
    changes only where start + i*N/n crosses an integer, so the starts between two such points give one set."""
    if n >= N:
        return [(tuple(range(N)), 1.0)]
    k = N / n
    cuts = {0.0, k}
    for i in range(n):
        lo = i * k
        for j in range(math.ceil(lo), math.ceil(lo + k)):
            b = j - lo
            if 0.0 < b < k:
                cuts.add(b)
    cuts = sorted(cuts)
    out: dict[tuple, float] = defaultdict(float)
    ids = list(range(N))
    for a, b in zip(cuts, cuts[1:]):
        if b - a <= 1e-12:
            continue
        out[tuple(sys_sample(ids, n, _Fixed((a + b) / 2)))] += (b - a) / k
    return list(out.items())


def _log_bb(j, n, a, b):
    return gammaln(n + 1) - gammaln(j + 1) - gammaln(n - j + 1) + betaln(j + a, n - j + b) - betaln(a, b)


def log_e_grid(N: int, R: int, tmax: int) -> np.ndarray:
    """log e_t at the true R for every (t, k), t <= tmax, k <= t (impossible states -inf); as cell_bounds computes
    its set (prior BetaBinomial(N, 1/2, 1/2); posterior of R - k given t, k)."""
    a, b = PRIOR
    t = np.arange(tmax + 1)[:, None]
    k = np.arange(tmax + 1)[None, :]
    ok = (k <= t) & (k <= R) & (t - k <= N - R)
    tt, kk = np.broadcast_arrays(t, k)
    out = np.full(tt.shape, -np.inf)
    lp = _log_bb(R, N, a, b)
    post = _log_bb(R - kk[ok], N - tt[ok], a + kk[ok], b + tt[ok] - kk[ok])
    out[ok] = lp - post
    return out


def _forward(t0, k0, cross):
    """Segment 1: a uniform order of the t0 S0 records (k0 relevant). F[s, o] = P(reach (s, o) without crossing),
    X[s] = P(crossed by depth s)."""
    F = np.zeros((t0 + 1, k0 + 1))
    F[0, 0] = 1.0
    X = np.zeros(t0 + 1)
    for s in range(t0):
        o = np.arange(k0 + 1)
        p1 = np.where(t0 - s > 0, (k0 - o) / max(t0 - s, 1), 0.0).clip(0, 1)
        nxt = np.zeros(k0 + 1)
        nxt += F[s] * (1 - p1)
        nxt[1:] += (F[s] * p1)[:-1]
        hit = cross[s + 1, : k0 + 1] & (nxt > 0)
        X[s + 1] = X[s] + nxt[hit].sum()
        nxt[hit] = 0.0
        F[s + 1] = nxt
    return F, X


def _backward(t_from, t_end, k_ends, e_end_alpha, cross, kmax):
    """Segment 2 for a batch of endpoints (t_end, k_ends[b]): q[b, t, k] = P(hit after (t, k)) + P(no hit) *
    alpha * e_end, for t_from <= t <= t_end. Returns q[:, t_from: t_end + 1, :kmax + 1]."""
    B = len(k_ends)
    K = kmax + 1
    V = np.zeros((B, K))   # value at the current t: 1 if the state crosses, else q
    kk = np.arange(K)
    endok = kk[None, :] == k_ends[:, None]
    V[endok] = np.where(cross[t_end, k_ends], 1.0, e_end_alpha)
    out = np.zeros((B, t_end - t_from + 1, K))
    out[:, t_end - t_from] = np.where(endok, np.where(cross[t_end, k_ends], 1.0, e_end_alpha)[:, None], 0.0)
    for t in range(t_end - 1, t_from - 1, -1):
        left = t_end - t
        need = k_ends[:, None] - kk[None, :]
        p1 = np.where((need >= 0) & (need <= left), need / left, np.nan)
        up = np.zeros((B, K))
        up[:, :-1] = V[:, 1:]
        q = np.where(np.isnan(p1), 0.0, np.nan_to_num(p1) * up + (1 - np.nan_to_num(p1)) * V)
        out[:, t - t_from] = q
        V = np.where(cross[t, :K][None, :], 1.0, q)
    return out


def cell_q(Nc, Rc, n_band, N_band, comps, alpha, m, level_cut=PRUNE):
    """Q for one cell: ``comps`` maps (t0, k0) of the cell in S0 to the set probability; an extension of m records
    in the band after j of its n_band S0 records. Returns the list of Q over j (one value for m = 0)."""
    m = min(m, N_band - n_band)
    tmax = max(t0 for t0, _ in comps) + m
    le = log_e_grid(Nc, Rc, tmax)
    cross = le >= math.log(1 / alpha) - 1e-12
    js = [0] if m == 0 else list(range(n_band + 1))
    Q = np.zeros(len(js))
    for (t0, k0), w in comps.items():
        F, X = _forward(t0, k0, cross)
        # extension draws into this cell: x of the m, y of those relevant
        G = np.zeros((t0 + 1, k0 + 1))
        lost = 0.0
        if m == 0:
            pairs = {0: ([0], np.array([1.0]))}
        else:
            pairs = {}
            px = hypergeom(N_band - n_band, Nc - t0, m).pmf(np.arange(m + 1))
            for x in range(m + 1):
                if px[x] < level_cut:
                    lost += px[x]
                    continue
                if x == 0:
                    pairs[0] = ([0], np.array([px[0]]))
                    continue
                py = hypergeom(Nc - t0, Rc - k0, x).pmf(np.arange(x + 1)) * px[x]
                ys = [y for y in range(x + 1) if py[y] >= level_cut]
                lost += px[x] - py[ys].sum()
                if ys:
                    pairs[x] = (ys, py[ys])
        for x, (ys, pr) in pairs.items():
            t_end = t0 + x
            k_ends = np.array([k0 + y for y in ys], dtype=int)
            ea = np.exp(np.minimum(le[t_end, k_ends], 50.0)) * alpha
            q = _backward(0, t_end, k_ends, ea, cross, k0 + max(ys))   # q[b, t, k]
            G += np.tensordot(pr, q[:, : t0 + 1, : k0 + 1], axes=1)
        G += lost
        for i, j in enumerate(js):
            if m == 0:
                Q[i] += w * min(1.0, G[0, 0])
                continue
            pj = hypergeom(n_band, t0, j).pmf(np.arange(t0 + 1))   # cell records among the first j of S0
            val = float((pj * (X + (F * G).sum(axis=1))).sum())
            Q[i] += w * min(1.0, val)
    return Q


def arrange(name, N, R, Rk, F, n, seed=0):
    """Relevant and kept flags in band order for an arrangement: ranked (relevant on top), cluster (clusters of about
    25 records, relevant in a few, sorted by cluster), random, aligned (relevant on the sys_sample step)."""
    rng = np.random.default_rng(seed)
    rel = np.zeros(N, bool)
    kept = np.zeros(N, bool)
    if name == "ranked":
        order = np.arange(N)
    elif name == "random":
        order = rng.permutation(N)
    elif name == "cluster":
        n_cl = max(1, N // 25)
        weights = 1.0 / np.arange(1, n_cl + 1) ** 1.2
        cl_rel = rng.choice(n_cl, size=N, p=weights / weights.sum())   # cluster of each would-be relevant record
        cl_other = rng.integers(0, n_cl, size=N)
        cl = np.concatenate([cl_rel[:R], cl_other[R:]])
        perm_cl = rng.permutation(n_cl)
        order = np.lexsort((np.arange(N), perm_cl[cl]))
        pos = np.empty(N, int)
        pos[order] = np.arange(N)
        order = pos
    elif name == "aligned":
        k = N / n
        grid = []
        off = 0.5
        while len(grid) < N and off < k:
            grid += [int(off + i * k) for i in range(n)]
            off += 1.0
        seen = set(grid)
        grid = list(dict.fromkeys(grid)) + [p for p in range(N) if p not in seen]
        order = np.array(grid[:N])
    else:
        raise ValueError(name)
    # record i of the logical list (relevant kept, relevant missed, others kept, others) sits at position order[i]
    rel[order[:R]] = True
    kept[order[:Rk]] = True
    kept[order[R:R + F]] = True
    return rel, kept


def default_frame(total: int) -> list[dict]:
    """The sampled default bands (S2..S5) on a frame of ``total`` records: {name, N, n, R, Rk, F}."""
    out = []
    for b in DEFAULT_BANDS:
        if b.n is None:
            continue
        end = total if b.end is None else min(b.end, total)
        N = end - b.start + 1
        if N <= 0:
            continue
        p, s, f = PROFILE[b.name]
        R = max(1, round(N * p))
        Rk = round(R * s)
        out.append({"name": b.name, "N": N, "n": b.n, "R": R, "Rk": Rk, "F": round((N - R) * f)})
    return out


def check_band(band, arrangement, alpha, extensions=EXTENSIONS, seed=0):
    """Per cell and extension size: (Q max over j, uniform Q) for one band and arrangement."""
    N, n = band["N"], band["n"]
    rel, kept = arrange(arrangement, N, band["R"], band["Rk"], band["F"], n, seed)
    sets = start_sets(N, n)
    rows = []
    for flag, cname in ((True, "kept"), (False, "not kept")):
        cell = kept == flag
        Nc, Rc = int(cell.sum()), int((rel & cell).sum())
        comps: dict = defaultdict(float)
        for idx, w in sets:
            ix = np.array(idx)
            c = cell[ix]
            comps[(int(c.sum()), int((rel[ix] & c).sum()))] += w
        uni = uniform_comps(N, n, Nc, Rc)
        for m in extensions:
            q = cell_q(Nc, Rc, n, N, comps, alpha, m)
            qu = cell_q(Nc, Rc, n, N, uni, alpha, m) if m == 0 else None
            rows.append({"band": band["name"], "cell": cname, "N": Nc, "R": Rc, "m": m, "sets": len(comps),
                         "q": float(np.max(q)), "j": int(np.argmax(q)) if m else None,
                         "uniform": None if qu is None else float(qu[0]), "alpha": alpha})
    return rows


def uniform_comps(N, n, Nc, Rc):
    """(t0, k0) of a cell under a simple random sample of n of the band's N records."""
    out: dict = {}
    pt = hypergeom(N, Nc, n).pmf(np.arange(n + 1))
    for t0 in range(n + 1):
        if pt[t0] < PRUNE:
            continue
        pk = hypergeom(Nc, Rc, t0).pmf(np.arange(t0 + 1))
        for k0 in range(t0 + 1):
            if pt[t0] * pk[k0] >= PRUNE:
                out[(t0, k0)] = pt[t0] * pk[k0]
    s = sum(out.values())
    return {key: v / s for key, v in out.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--frames", default="2000,20000")
    ap.add_argument("--level", type=float, default=0.95)
    ap.add_argument("--arrangements", default=",".join(ARRANGEMENTS))
    ap.add_argument("--extensions", default=",".join(map(str, EXTENSIONS)))
    ap.add_argument("--seeds", default="0", help="arrangement seeds (random and cluster), comma-separated")
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args(argv)
    seeds = [int(x) for x in a.seeds.split(",")]
    exts = tuple(int(x) for x in a.extensions.split(","))
    rows = []
    for total in (int(x) for x in a.frames.split(",")):
        bands = default_frame(total)
        H = len(bands) + 1   # the census band S1 keeps its two cells in the split
        alpha = (1 - a.level) / (2 * H)
        for arr in a.arrangements.split(","):
            for seed in (seeds if arr in ("random", "cluster") else [0]):
                for b in bands:
                    for r in check_band(b, arr, alpha, exts, seed):
                        rows.append({"frame": total, "arrangement": arr, "seed": seed, **r})
                        print_row(rows[-1], a.markdown)
    worst = [r for r in rows if r["arrangement"] in REALISTIC and r["q"] > r["alpha"] * (1 + 1e-9)]
    print(f"\nrealistic arrangements over alpha_c: {len(worst)} of "
          f"{sum(r['arrangement'] in REALISTIC for r in rows)} rows")
    for r in worst:
        print(f"  EXCESS frame {r['frame']} {r['arrangement']} s{r['seed']} {r['band']} {r['cell']} m={r['m']}: "
              f"Q={r['q']:.5f} vs alpha_c={r['alpha']:.5f} (x{r['q'] / r['alpha']:.3f})")
    return rows


def print_row(r, md):
    ratio = r["q"] / r["alpha"]
    uni = "" if r["uniform"] is None else f"{r['uniform'] / r['alpha']:.3f}"
    if md:
        print(f"| {r['frame']} | {r['arrangement']} | {r['seed']} | {r['band']} | {r['cell']} | {r['N']} | {r['R']} | {r['m']} | "
              f"{r['sets']} | {ratio:.3f} | {uni} |", flush=True)
    else:
        print(f"{r['frame']:6d} {r['arrangement']:8s} s{r['seed']} {r['band']} {r['cell']:9s} N={r['N']:6d} R={r['R']:4d} "
              f"m={r['m']:3d} sets={r['sets']:4d} Q/alpha={ratio:.3f} uniform={uni}", flush=True)


if __name__ == "__main__":
    main()
