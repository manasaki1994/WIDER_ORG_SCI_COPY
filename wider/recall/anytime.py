"""Exact bounds on recall that stay valid at every look (change validation-sample-planner, design D15).

Each band h of the probability sample splits into two cells by the result set's decision (kept or not kept). A cell
has N frame records, t of them labelled and k of those relevant; R, its number of relevant records, is unknown.
:func:`cell_bounds` gives the prior-posterior-ratio confidence sequence for R (Waudby-Smith and Ramdas 2020,
"Confidence sequences for sampling without replacement"): with the prior R ~ BetaBinomial(N, 1/2, 1/2) the set
{r : pi_t(r) / pi_0(r) > alpha}. At the true R, pi_0(R) / pi_t(R) is a nonnegative martingale with mean 1 under
sampling without replacement, so by Ville's inequality R leaves the set with probability at most alpha, at any look,
whatever rule decides when to look or how many records to add. The bounds are exact, end at N, and work with k = 0.

:func:`recall_bounds` splits 1 - level equally over the cells (a union bound) and takes the box over the ratio:
recall rises with every kept cell's R and falls with every not-kept cell's R, so the lower bound is
sum(L_kept) / (sum(L_kept) + sum(U_notkept)) and the upper bound sum(U_kept) / (sum(U_kept) + sum(L_notkept)).
"""
from __future__ import annotations

import math

import numpy as np

METHOD = "anytime-hypergeometric"
PRIOR = (0.5, 0.5)
KEPT, NOT_KEPT = "kept", "not kept"
OUTCOMES = ("met", "not met", "cannot tell")


def _log_betabinom(k, n, a, b):
    from scipy.special import betaln, gammaln

    return gammaln(n + 1) - gammaln(k + 1) - gammaln(n - k + 1) + betaln(k + a, n - k + b) - betaln(a, b)


def cell_bounds(N: int, t: int, k: int, alpha: float) -> tuple[int, int]:
    """The smallest and largest number of relevant records among ``N`` that the confidence sequence keeps after
    ``t`` labelled records with ``k`` relevant, at error ``alpha``. A census (t = N) gives k exactly; an empty cell
    (N = 0) gives (0, 0)."""
    N, t, k = int(N), int(t), int(k)
    if not 0 <= k <= t <= N:
        raise ValueError(f"need 0 <= k <= t <= N, got N={N}, t={t}, k={k}")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")
    if t == N:
        return k, k
    a, b = PRIOR
    r = np.arange(N + 1)
    log_prior = _log_betabinom(r, N, a, b)
    j = r - k
    ok = (j >= 0) & (j <= N - t)
    log_post = np.full(N + 1, -np.inf)
    log_post[ok] = _log_betabinom(j[ok], N - t, a + k, b + t - k)
    keep = r[log_post - log_prior > math.log(alpha)]
    return int(keep.min()), int(keep.max())


def _ratio(num: float, other: float, empty: float) -> float:
    return num / (num + other) if num + other > 0 else empty


def recall_bounds(cells: list[dict], level: float) -> dict:
    """Exact bounds on recall valid at every look at ``level``, from ``cells`` ({band, decision, N, t, k}, decision
    "kept" or "not kept"; every band gives both cells, an empty one with N = 0, so the split is fixed by the design).
    Returns {low, high, level, method, alpha_cell, cells: [{band, decision, N, t, k, low, high}], dominant}, where
    ``dominant`` is the not-kept cell whose upper bound leaves the most relevant papers unseen ({band, N, t, k,
    unseen, share}), or None when no such cell can hide any."""
    if not cells:
        raise ValueError("no cells to bound")
    alpha = (1 - level) / len(cells)
    out_cells = []
    for c in cells:
        lo, hi = cell_bounds(c["N"], c["t"], c["k"], alpha)
        out_cells.append({"band": c["band"], "decision": c["decision"], "N": int(c["N"]), "t": int(c["t"]),
                          "k": int(c["k"]), "low": lo, "high": hi})
    lk = sum(c["low"] for c in out_cells if c["decision"] == KEPT)
    uk = sum(c["high"] for c in out_cells if c["decision"] == KEPT)
    ln = sum(c["low"] for c in out_cells if c["decision"] != KEPT)
    un = sum(c["high"] for c in out_cells if c["decision"] != KEPT)
    unseen = [(c["high"] - c["k"], c) for c in out_cells if c["decision"] != KEPT]
    total_unseen = sum(u for u, _ in unseen)
    dominant = None
    if total_unseen > 0:
        u, c = max(unseen, key=lambda x: x[0])
        dominant = {"band": c["band"], "N": c["N"], "t": c["t"], "k": c["k"], "unseen": u,
                    "share": u / total_unseen}
    return {"low": _ratio(lk, un, 0.0), "high": _ratio(uk, ln, 1.0), "level": level, "method": METHOD,
            "alpha_cell": alpha, "cells": out_cells, "dominant": dominant}

