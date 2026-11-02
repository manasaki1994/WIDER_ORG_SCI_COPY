"""The validation sample planner (change validation-sample-planner, design D1).

:func:`plan` compares ways to spread a labelling budget over the bands of the probability sample by seeded
simulation of the outcome the researcher will see: each simulated sample is judged like ``wider recall`` judges a
look, on the exact bounds valid at every look (D15), or, before any screening result, on the band-level model-based
interval (D17). Without a budget it searches for the smallest total that gives a clear answer on the target in
``plan_power`` of the samples, or a median margin at most a precision. :func:`apply_plan` writes the chosen row into
``protocol.sampling.bands``. Nothing here reads or names a record's text; outputs are counts and band names.
"""
from __future__ import annotations

import math
import time

import numpy as np

from ..core.project import Project
from ..recall.anytime import cell_bounds
from ..recall.validation import judge
from .design import probability_frame, resolve_bands

DEFAULT_GUESS = (0.30, 0.05, 0.01, 0.005, 0.002, 0.001)
RULES = ("current", "proportional", "recall-optimal", "top band in full")
MAX_SIMS = 2000
STEP = 10
SENSITIVITY_LINE = "Rates uncertain: each allocation is shown at half and double your rates as well"
NO_TARGET_LINE = ("No recall target or margin is set: the recommendation only seeks an interval; set a target with "
                  "wider validation target.")
MODEL_DRAWS = 200


class PlanError(ValueError):
    """A plan that cannot be made; the CLI exits 1 and the route answers 400 with the message."""


# ------------------------------------------------------------------------------------------------ allocations
def _round(shares: dict[str, float], total: int, order: list[str]) -> dict[str, int]:
    """Largest-remainder rounding of ``shares`` (summing to ``total``); a tie goes to the higher band (earlier in
    the ranking)."""
    base = {b: int(math.floor(v)) for b, v in shares.items()}
    left = total - sum(base.values())
    rem = sorted(shares, key=lambda b: (-(shares[b] - base[b]), order.index(b)))
    for b in rem[:max(left, 0)]:
        base[b] += 1
    return base


def _spread(weights: dict[str, float], total: int, lo: dict[str, int], hi: dict[str, int],
            order: list[str]) -> dict[str, int] | None:
    """``total`` over the bands in proportion to ``weights``, each between lo and hi (water-filling, then
    largest-remainder rounding). None when the bounds cannot hold the total."""
    if total < sum(lo.values()) or total > sum(hi.values()):
        return None
    fixed: dict[str, float] = {}
    free = list(weights)
    while True:
        rest = total - sum(fixed.values())
        wsum = sum(max(weights[b], 0.0) for b in free)
        share = {b: (rest * weights[b] / wsum if wsum > 0 else rest / len(free)) for b in free} if free else {}
        over = [b for b in free if share[b] > hi[b]]
        under = [b for b in free if share[b] < lo[b]]
        if not over and not under:
            break
        for b in over or under:
            fixed[b] = hi[b] if b in over else lo[b]
            free.remove(b)
    out = _round({**fixed, **share}, total, order)
    for b in out:   # rounding can push a band one past a bound; move that unit to a band with room
        while out[b] > hi[b]:
            out[b] -= 1
            out[next(c for c in order if c != b and out[c] < hi[c])] += 1
        while out[b] < lo[b]:
            out[b] += 1
            out[next(c for c in reversed(order) if c != b and out[c] > lo[c])] -= 1
    return out


def allocate(rule: str, total: int, bands: list[dict], rates: dict[str, float], min_per_band: int) -> dict | None:
    """{band: n_h} for one rule, census bands at N_h; None when the rule does not apply (top band in full that does
    not fit). Raises PlanError when the total is below the census bands."""
    census = [b for b in bands if b["census"]]
    sampled = [b for b in bands if not b["census"]]
    c_total = sum(b["N"] for b in census)
    if total < c_total:
        names = ", ".join(b["name"] for b in census)
        raise PlanError(f"the budget of {total} is below the {c_total} records of the census bands ({names}); raise "
                        f"the budget or make {census[0]['name']} a sampled band")
    out = {b["name"]: b["N"] for b in census}
    if not sampled:
        return out
    order = [b["name"] for b in sampled]
    lo = {b["name"]: min(min_per_band, b["N"]) for b in sampled}
    hi = {b["name"]: b["N"] for b in sampled}
    rest = total - c_total
    if rule == "current":
        w = {b["name"]: float(b["n"] or 0) for b in sampled}
    elif rule == "proportional":
        w = {b["name"]: float(b["N"]) for b in sampled}
    elif rule == "recall-optimal":
        w = {b["name"]: b["N"] * math.sqrt(max(rates[b["name"]], 0.0)) for b in sampled}
    elif rule == "top band in full":
        top = sampled[0]
        others = sampled[1:]
        if top["N"] > rest - sum(lo[b["name"]] for b in others):
            return None
        out[top["name"]] = top["N"]
        if not others:
            return out if rest == top["N"] else None
        w = {b["name"]: b["N"] * math.sqrt(max(rates[b["name"]], 0.0)) for b in others}
        got = _spread(w, rest - top["N"], {k: lo[k] for k in w}, {k: hi[k] for k in w}, order[1:])
        return None if got is None else {**out, **got}
    else:
        raise PlanError(f"unknown allocation {rule}")
    got = _spread(w, rest, lo, hi, order)
    if got is None:
        return None
    return {**out, **got}


def parse_pairs(text: str | dict | None, what: str) -> dict[str, float] | None:
    """"S1=0.3,S2=0.1" (or a dict) as {band: value}."""
    if text is None or text == "" or text == {}:
        return None
    if isinstance(text, dict):
        return {str(k): float(v) for k, v in text.items()}
    out = {}
    for part in str(text).split(","):
        if not part.strip():
            continue
        if "=" not in part:
            raise PlanError(f"{what}: give band=value pairs, e.g. S1=0.3,S2=0.1")
        k, v = part.split("=", 1)
        try:
            out[k.strip()] = float(v)
        except ValueError:
            raise PlanError(f"{what}: {v.strip()} is not a number") from None
    return out


def check_own(own: dict[str, float], budget: int | None, bands: list[dict], min_per_band: int) -> dict[str, int]:
    names = {b["name"]: b for b in bands}
    for k in own:
        if k not in names:
            raise PlanError(f"no band {k}")
    out = {}
    for b in bands:
        v = int(own.get(b["name"], b["N"] if b["census"] else 0))
        if b["census"] and v != b["N"]:
            raise PlanError(f"{b['name']} is a census band and keeps all {b['N']} records")
        if not b["census"] and v < min(min_per_band, b["N"]):
            raise PlanError(f"{b['name']} needs at least {min(min_per_band, b['N'])}")
        if v > b["N"]:
            raise PlanError(f"{b['name']} holds only {b['N']} records")
        out[b["name"]] = v
    if budget is not None and sum(out.values()) != budget:
        raise PlanError(f"your allocation sums to {sum(out.values())}, not the budget of {budget}")
    return out


# ------------------------------------------------------------------------------------------------ inputs
def _bands(project: Project, rk: dict, percentiles=None) -> tuple[list[dict], dict, list[str]]:
    """The planned bands with their frame counts: [{name, start, end, N, n, census, N_kept}], the frame and notes."""
    from ..core.protocol import SamplingBand

    pf = probability_frame(project, rk)
    cfg_bands = project.config.bands
    if percentiles:
        cuts = [0.0] + [float(x) for x in percentiles] + [100.0]
        cuts = sorted(set(cuts))
        cfg_bands = [SamplingBand(name=f"S{i + 1}", start_pct=a, end_pct=b, n=None if i == 0 else 10)
                     for i, (a, b) in enumerate(zip(cuts, cuts[1:]))]
    resolved, notes = resolve_bands(cfg_bands, pf["n_ranked"])
    from .design import in_band

    out = []
    for b in resolved:
        ids = [x["record_id"] for x in pf["frame"] if in_band(x["rank"], b)]
        out.append({**b, "N": len(ids), "census": b["n"] is None, "ids": ids})
    return out, pf, notes


def _labels(project: Project, primary: str) -> list[dict]:
    """Final labels of the probability sample with rank, weight and whether relevant under the primary variant."""
    from ..recall.estimate import VARIANTS
    from .design import sampling_log
    from .labels import final_labels

    name = project.config.protocol.sampling.probability_sample
    if name not in sampling_log(project):
        return []
    relevant = VARIANTS[primary][0]
    return [{"rank": r["rank"], "w": float(r["weight"] or 1.0), "rel": r["decision"] in relevant}
            for r in final_labels(project, name) if r["decision"] is not None and r["rank"] is not None]


def _rates(project: Project, bands: list[dict], given: dict | None, primary: str) -> tuple[dict, dict, dict, str]:
    """(middle, low, high, sentence): the expected inclusion rate per planned band and the source, said once."""
    from scipy.stats import beta

    names = [b["name"] for b in bands]
    guess = {n: DEFAULT_GUESS[min(i, len(DEFAULT_GUESS) - 1)] for i, n in enumerate(names)}
    if given:
        for k in given:
            if k not in names:
                raise PlanError(f"no band {k}")
        mid = {n: float(given.get(n, guess[n])) for n in names}
        return mid, {n: v / 2 for n, v in mid.items()}, {n: min(1.0, v * 2) for n, v in mid.items()}, \
            "Expected inclusion rates: your own (--rates)."
    labs = _labels(project, primary)
    if not labs:
        txt = ", ".join(f"{n} {guess[n]:.2f}" for n in names[:3]) + (", ..." if len(names) > 3 else "")
        return guess, {n: v / 2 for n, v in guess.items()}, {n: min(1.0, v * 2) for n, v in guess.items()}, \
            f"Expected inclusion rates: a default guess ({txt}), not from your data; give your own with --rates"
    mid, low, high, missing = {}, {}, {}, []
    from .design import in_band

    for b in bands:
        inb = [x for x in labs if in_band(x["rank"], b)]
        if not inb:
            mid[b["name"]] = guess[b["name"]]
            low[b["name"]], high[b["name"]] = guess[b["name"]] / 2, min(1.0, guess[b["name"]] * 2)
            missing.append(b["name"])
            continue
        w = sum(x["w"] for x in inb)
        wr = sum(x["w"] for x in inb if x["rel"])
        k = sum(1 for x in inb if x["rel"])
        n = len(inb)
        mid[b["name"]] = wr / w if k else 0.5 / n
        low[b["name"]] = float(beta.ppf(0.10, k + 0.5, n - k + 0.5))
        high[b["name"]] = float(beta.ppf(0.90, k + 0.5, n - k + 0.5))
    line = f"Expected inclusion rates: from the {len(labs)} validation labels so far"
    if missing:
        line += f" (no labelled record falls in {', '.join(missing)}: a default guess there)"
    return mid, low, high, line + "."


def _decisions(project: Project, protocol: str | None, result_set: int | None, primary: str):
    """(kept record ids, protocol, set) of the result set, or (None, None, None) before any screening result."""
    from ..recall.estimate import VARIANTS, check_names
    from ..screen.decisions import decisions as screen_decisions
    from ..screen.decisions import protocols

    if protocol is None:
        protos = [p["protocol"] for p in protocols(project)]
        if not protos:
            if result_set is not None:
                raise PlanError("--set needs a protocol")
            return None, None, None
        protocol = protos[-1]
    try:
        info = check_names(project, None, protocol, result_set)
    except (KeyError, ValueError) as e:
        raise PlanError(str(e.args[0] if isinstance(e, KeyError) else e)) from None
    kept = VARIANTS[primary][1]
    dec = screen_decisions(project, info["key"])
    if not dec:
        return None, None, None
    return {rid for rid, d in dec.items() if d in kept}, protocol, info["result_set"]


def _stored_recall(project: Project) -> tuple[float, str] | None:
    """The primary recall stored with the latest look on the probability sample (``last_validation_lines``), while
    that look's labels checksum is the current one; never a new estimate, so a plan records no look (D4, Q11)."""
    import json

    from ..recall.validation import labels_checksum
    from .labels import final_labels

    name = project.config.protocol.sampling.probability_sample
    last = json.loads(project.store.get_meta(f"last_validation_lines:{name}") or "{}")
    idx = (last.get("look") or {}).get("index")
    if last.get("recall") is None or idx is None:   # no look, or one stored before the recall was
        return None
    cs = project.store.scalar("SELECT s.labels_checksum FROM validation_look_states s JOIN samples p ON "
                              "p.sample_id=s.sample_id WHERE p.name=? AND s.look_index=?", (name, idx))
    try:
        rows = [{"record_id": r["record_id"], "human": r["decision"]} for r in final_labels(project, name)
                if r["band"] is not None and r["weight"] is not None and r["decision"] is not None]
    except KeyError:
        return None
    if cs is None or labels_checksum(rows) != cs:
        return None
    return float(last["recall"]), f"the primary recall stored with look {idx}"


def _expected_recall(project: Project, given: float | None, target: float | None) -> tuple[float, str]:
    if given is not None:
        return float(given), "your expected recall"
    stored = _stored_recall(project)
    if stored is not None:
        return stored
    if target is not None:
        return min(0.99, target + 0.05), "the target plus 0.05"
    return 0.90, "a default guess of 0.90"


# ------------------------------------------------------------------------------------------------ simulation
class _Bounds:
    """cell_bounds with a cache: the simulated samples repeat (t, k) pairs."""

    def __init__(self):
        self.memo: dict = {}

    def __call__(self, N, t, k, alpha):
        key = (N, t, k, alpha)
        if key not in self.memo:
            self.memo[key] = cell_bounds(N, t, k, alpha)
        return self.memo[key]


def _cells(bands: list[dict], rates: dict, R: float, kept_ids: set | None) -> list[dict]:
    """Per band the four frame cells: [rel_kept, nonrel_kept, rel_not, nonrel_not]."""
    out = []
    for b in bands:
        N = b["N"]
        K = min(N, int(round(N * rates[b["name"]])))
        if kept_ids is None:
            out.append({"N": N, "K": K})
            continue
        nk = sum(1 for i in b["ids"] if i in kept_ids)
        nn = N - nk
        kk = min(nk, int(round(K * R)))
        kn = min(nn, K - kk)
        kk = min(nk, K - kn)
        out.append({"N": N, "K": kk + kn, "four": [kk, nk - kk, kn, nn - kn], "nk": nk, "nn": nn})
    return out


def simulate(bands: list[dict], alloc: dict, cells: list[dict], R: float, target: float | None, level: float,
             sims: int, seed: int, bounds: _Bounds) -> dict:
    """``sims`` seeded samples of ``alloc``; each judged on the exact bounds (with a result set) or the band-level
    model interval (before one). Returns the per-allocation summary."""
    rng = np.random.default_rng(seed)
    nb = len(bands)
    has_set = "four" in cells[0] if cells else False
    # draws[s, h, c]: the sample's count in cell c of band h
    if has_set:
        draws = np.stack([rng.multivariate_hypergeometric(c["four"], alloc[b["name"]], size=sims)
                          if alloc[b["name"]] < b["N"] else np.tile(c["four"], (sims, 1))
                          for b, c in zip(bands, cells)], axis=1)
    else:
        k = np.stack([rng.hypergeometric(c["K"], c["N"] - c["K"], alloc[b["name"]], size=sims)
                      if 0 < alloc[b["name"]] < b["N"] else np.full(sims, c["K"] if alloc[b["name"]] else 0)
                      for b, c in zip(bands, cells)], axis=1)
        m = rng.binomial(k, R)
        draws = np.stack([m, np.zeros_like(m), k - m, np.zeros_like(m)], axis=2)   # non-relevant split not needed
    n = np.array([alloc[b["name"]] for b in bands])
    N = np.array([b["N"] for b in bands])
    w = np.where(n > 0, N / np.maximum(n, 1), 0.0)
    rel_kept, rel_not = draws[:, :, 0], draws[:, :, 2]
    rel = rel_kept + rel_not
    sampled = np.array([not b["census"] for b in bands])
    interval = ~((rel == 0) & sampled[None, :] & (n[None, :] > 0)).any(axis=1) & (rel.sum(axis=1) > 0)
    num = (rel_kept * w).sum(axis=1)
    den = (rel * w).sum(axis=1)
    est = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    lows, highs = np.empty(sims), np.empty(sims)
    if has_set:
        alpha = (1 - level) / (2 * nb)
        for s in range(sims):
            lk = uk = ln = un = 0
            for h, c in enumerate(cells):
                t_k = int(draws[s, h, 0] + draws[s, h, 1])
                t_n = int(draws[s, h, 2] + draws[s, h, 3])
                a, bb = bounds(c["nk"], t_k, int(draws[s, h, 0]), alpha)
                lk, uk = lk + a, uk + bb
                a, bb = bounds(c["nn"], t_n, int(draws[s, h, 2]), alpha)
                ln, un = ln + a, un + bb
            lows[s] = lk / (lk + un) if lk + un else 0.0
            highs[s] = uk / (uk + ln) if uk + ln else 1.0
    else:
        lows[:], highs[:] = _band_model(rng, N, n, rel, rel_kept, level)
    verdicts = [judge({"low": lo, "high": hi}, target) for lo, hi in zip(lows, highs)] if target is not None else []
    margin = (est - lows)[interval & ~np.isnan(est)]
    q = (lambda p: float(np.quantile(margin, p))) if margin.size else (lambda p: None)
    out = {"allocation": {k: int(v) for k, v in alloc.items()}, "total": int(sum(alloc.values())),
           "expected_relevant": float(sum(alloc[b["name"]] * c["K"] / b["N"] for b, c in zip(bands, cells) if b["N"])),
           "p_interval": float(interval.mean()),
           "margin": {"median": q(0.5), "p10": q(0.1), "p90": q(0.9)}}
    if target is not None:
        met = sum(v == "met" for v in verdicts) / sims
        notm = sum(v == "not met" for v in verdicts) / sims
        out |= {"p_clear": met + notm, "p_met": met, "p_not_met": notm}
    return out


def _band_model(rng, N, n, rel, rel_kept, level, draws: int = MODEL_DRAWS):
    """Band-level model-based interval per simulated sample (D17, used before any screening result): Jeffreys
    θ_h for the band's relevant share, Binomial over its records left, a pooled Jeffreys share kept."""
    sims, nb = rel.shape
    th = rng.beta(rel[:, None, :] + 0.5, (n - rel)[:, None, :] + 0.5, size=(sims, draws, nb))
    left = np.broadcast_to(N - n, (sims, draws, nb))
    extra = rng.binomial(left, th)
    tot_rel = rel[:, None, :] + extra
    kk, kr = rel_kept.sum(axis=1), rel.sum(axis=1)
    phi = rng.beta(kk + 0.5, kr - kk + 0.5, size=(draws, sims)).T
    extra_kept = rng.binomial(extra, phi[:, :, None])
    kept = rel_kept[:, None, :] + extra_kept
    ratio = kept.sum(axis=2) / np.maximum(tot_rel.sum(axis=2), 1)
    a = (1 - level) / 2
    return np.quantile(ratio, a, axis=1), np.quantile(ratio, 1 - a, axis=1)


# ------------------------------------------------------------------------------------------------ plan
def _rows_for(rule_allocs, bands, rate_sets, R, kept_ids, target, level, sims, seed, bounds, deadline):
    rows, approx = [], False
    for i, (rule, alloc) in enumerate(rule_allocs):
        if alloc is None:
            continue
        for j, (rname, rates) in enumerate(rate_sets):
            if time.monotonic() > deadline:
                approx = True
                break
            cells = _cells(bands, rates, R, kept_ids)
            r = simulate(bands, alloc, cells, R, target, level, sims, seed + 1000 * i + j, bounds)
            rows.append({"rule": rule, "rates": rname, **r})
    return rows, approx


def _criterion(row, target, precision, power):
    if target is not None:
        return row["p_clear"] >= power
    m = row["margin"]["median"]
    return m is not None and m <= precision


def _search(rule, bands, rates_sets, R, kept_ids, target, precision, power, level, sims, seed, bounds, mpb, deadline):
    """Smallest total (steps of 10) meeting the criterion per rate row; None when even the frame does not."""
    frame_total = sum(b["N"] for b in bands)
    start = sum(b["N"] for b in bands if b["census"]) + sum(min(mpb, b["N"]) for b in bands if not b["census"])
    results = {}
    for j, (rname, rates) in enumerate(rates_sets):
        cells = _cells(bands, rates, R, kept_ids)

        def ok(total):
            a = allocate(rule, total, bands, rates_sets[1][1], mpb)
            if a is None:
                return None
            r = simulate(bands, a, cells, R, target, level, max(1, sims // 4), seed + 7 + j, bounds)
            return _criterion(r, target, precision, power)

        hi = max(start, STEP)
        while hi < frame_total and not ok(hi):
            if time.monotonic() > deadline:
                break
            hi = min(frame_total, hi * 2)
        if not ok(hi):
            results[rname] = None
            continue
        lo = max(start, hi // 2)
        while hi - lo > STEP:
            mid = lo + ((hi - lo) // 2 // STEP) * STEP or lo + STEP
            if ok(mid):
                hi = mid
            else:
                lo = mid
        b = int(math.ceil(hi / STEP) * STEP) if hi < frame_total else frame_total
        while b - STEP >= start and ok(b - STEP):
            b -= STEP
        results[rname] = b
    return results


def plan(project: Project, budget: int | None = None, precision: float | None = None, target: float | None = None,
         rates=None, expected_recall: float | None = None, allocation=None, percentiles=None,
         sims: int | None = None, protocol: str | None = None, result_set: int | None = None) -> dict:
    """The planner of D1; returns the table, the recommendation and the sentences. Stores nothing."""
    from .design import _ranking

    cfg = project.config.protocol
    sp, rp = cfg.sampling, cfg.recall
    try:
        rk = _ranking(project, None)
    except (KeyError, ValueError, LookupError):
        raise PlanError("no ranking yet: run wider rank run first") from None
    target = rp.target if target is None else target
    stored_precision = precision is None
    precision = rp.precision if precision is None else precision
    if budget is None and target is None and precision is None:
        raise PlanError("give a budget, or set a target or a margin to search for one")
    sims = min(int(sims or sp.plan_sims), MAX_SIMS)
    level, power, mpb = rp.ci_level, sp.plan_power, sp.min_per_band
    bands, pf, band_notes = _bands(project, rk, percentiles)
    primary = rp.primary_variant
    mid, low, high, rates_line = _rates(project, bands, parse_pairs(rates, "--rates"), primary)
    R, R_source = _expected_recall(project, expected_recall, target)
    kept_ids, proto_used, set_used = _decisions(project, protocol, result_set, primary)
    own = parse_pairs(allocation, "--allocation")
    own_alloc = check_own(own, budget, bands, mpb) if own else None
    rate_sets = [("low", low), ("middle", mid), ("high", high)]
    seed = int(project.config.seed)
    bounds = _Bounds()
    deadline = time.monotonic() + sp.plan_seconds
    lines = [rates_line, SENSITIVITY_LINE, f"Expected recall {R:.2f}: {R_source}."]
    if kept_ids is None:
        lines.append(f"No screening result yet: screening decisions are simulated from the expected recall {R:g}.")
    else:
        lines.append(f"Screening decisions from {proto_used}, set {set_used}.")
    lines += band_notes
    out = {"budget": budget, "target": target, "precision": precision, "precision_stored": stored_precision,
           "plan_power": power, "level": level, "sims": sims, "expected_recall": R, "expected_recall_source": R_source,
           "protocol": proto_used, "result_set": set_used, "rates": {"middle": mid, "low": low, "high": high},
           "rates_line": rates_line, "screening_result": kept_ids is not None,
           "bands": [{k: b[k] for k in ("name", "start", "end", "start_pct", "end_pct", "N", "census")} for b in bands],
           "frame": len(pf["frame"]), "rows": [], "recommended": None, "search": None, "approximate": False}
    if budget is not None:
        rule_allocs = [(r, allocate(r, budget, bands, mid, mpb)) for r in RULES]
        if bands and bands[0]["census"]:
            rule_allocs = [(r, a) for r, a in rule_allocs if r != "top band in full"]
            out["top_band_note"] = "same as recall-optimal: the top band is already a census"
        if own_alloc:
            rule_allocs.append(("yours", own_alloc))
        rows, approx = _rows_for(rule_allocs, bands, rate_sets, R, kept_ids, target, level, sims, seed, bounds,
                                 deadline)
        if bands and bands[0]["census"]:
            rows += [{**r, "rule": "top band in full", "note": out["top_band_note"]}
                     for r in rows if r["rule"] == "recall-optimal"]
        out["rows"], out["approximate"] = rows, approx
        for r in rows:
            if r["rates"] == "middle":
                lowcount = [b["name"] for b in bands if not b["census"] and r["allocation"][b["name"]]
                            and r["allocation"][b["name"]] * mid[b["name"]] < 1]
                if lowcount:
                    r["low_count_note"] = (f"{', '.join(lowcount)}: fewer than one relevant paper expected; a band "
                                           "with rate p needs about 1/p labels to show one")
        middle = [r for r in rows if r["rates"] == "middle"]
        pref = {"recall-optimal": 0}
        if target is not None:
            key = lambda r: (-r["p_clear"], pref.get(r["rule"], 1))   # noqa: E731
        elif precision is not None:
            key = lambda r: (r["margin"]["median"] if r["margin"]["median"] is not None else 9, pref.get(r["rule"], 1))  # noqa: E731
        else:
            key = lambda r: (-r["p_interval"], r["margin"]["median"] if r["margin"]["median"] is not None else 9,  # noqa: E731
                             pref.get(r["rule"], 1))
        cands = [r for r in middle if r["rule"] != "top band in full" or "note" not in r]
        if cands:
            out["recommended"] = min(cands, key=key)["rule"]
        if target is not None:
            for r in middle:
                r["line"] = (f"if recall is {R:g}: a clear answer in {r['p_clear']:.0%} of simulated samples (met "
                             f"{r['p_met']:.0%}, not met {r['p_not_met']:.0%})")
    else:
        found = {}
        for rule in ("recall-optimal", "proportional", "current", "top band in full"):
            if rule == "top band in full" and bands and bands[0]["census"]:
                continue
            found[rule] = _search(rule, bands, rate_sets, R, kept_ids, target, precision, power, level, sims, seed,
                                  bounds, mpb, deadline)
        frame_total = sum(b["N"] for b in bands)
        # a total that only a census of the frame reaches is out of reach: the exact bounds of a census always
        # decide, so "label everything" is no plan (D1 item 7)
        reach = {r: v for r, v in found.items() if all(v.get(k) is not None and v[k] < frame_total
                                                       for k in ("low", "middle", "high"))}
        if not reach:
            a = allocate("recall-optimal", frame_total, bands, mid, mpb) or {b["name"]: b["N"] for b in bands}
            r = simulate(bands, a, _cells(bands, mid, R, kept_ids), R, target, level, sims, seed, bounds)
            if target is not None and r["p_clear"] < power:
                lines.append(f"Even labelling all {frame_total} frame records gives a clear answer in only "
                             f"{r['p_clear']:.0%} of simulated samples: recall is expected near the target. A clear "
                             "answer needs higher screening recall (your rule, or a revised prompt) rather than more "
                             "labels.")
            elif target is not None:
                lines.append(f"Only labelling all {frame_total} frame records gives a clear answer in {power:.0%} of "
                             "simulated samples: recall is expected near the target. A clear answer needs higher "
                             "screening recall (your rule, or a revised prompt) rather than more labels.")
            else:
                m = r["margin"]["median"]
                if m is not None and m <= precision:
                    lines.append(f"a margin of {precision:g} is out of reach: only labelling all {frame_total} frame "
                                 "records gives it")
                else:
                    lines.append(f"a margin of {precision:g} is out of reach even with every frame record labelled "
                                 f"(predicted margin with every frame record labelled: "
                                 f"{'none' if m is None else f'{m:.3f}'})")
            out["search"] = {"reached": False, "by_rule": found}
        else:
            best = min(reach, key=lambda r: (max(reach[r].values()), 0 if r == "recall-optimal" else 1))
            B = max(reach[best].values())
            L, U = min(reach[best].values()), max(reach[best].values())
            alloc = allocate(best, B, bands, mid, mpb)
            parts = ", ".join(f"{k} {v}" for k, v in alloc.items())
            if target is not None:
                lines.append(f"About {B} labels give a clear answer in {power:.0%} of the simulated samples "
                             f"(sampling.plan_power; {best}: {parts}); likely between {L} and {U}, given the "
                             "uncertain rates.")
            else:
                lines.append(f"About {B} labels give a margin of at most {precision:g} ({best} allocation: {parts}); "
                             f"likely between {L} and {U}, given the uncertain rates.")
            if target is not None and B > frame_total / 2:
                lines.append(f"That is more than half of the {frame_total} frame records: recall is expected near the "
                             "target. A clear answer needs higher screening recall (your rule, or a revised prompt) "
                             "rather than more labels.")
            out["search"] = {"reached": True, "budget": B, "low": L, "high": U, "rule": best, "allocation": alloc,
                             "by_rule": found}
            out["recommended"] = best
    if target is None and precision is None:
        lines.append(NO_TARGET_LINE)
    if target is not None and R > target and stored_precision and rp.precision is None:
        lines.append(f"Suggested margin: about {R - target:.2f} (the expected recall {R:g} minus the target "
                     f"{target:g}); a wider margin cannot tell met from not met.")
        out["suggested_margin"] = round(R - target, 4)
    if out["approximate"]:
        lines.append("approximate: time limit reached")
    out["lines"] = lines
    return out


def plan_lines(p: dict) -> list[str]:
    """The table and sentences every surface prints."""
    out = list(p["lines"][:2])
    for r in p["rows"]:
        if r["rates"] != "middle":
            continue
        mark = "  recommended" if r["rule"] == p["recommended"] else ""
        alloc = ", ".join(f"{k} {v}" for k, v in r["allocation"].items())
        m = r["margin"]
        marg = "no interval" if m["median"] is None else f"margin {m['median']:.3f} ({m['p10']:.3f} to {m['p90']:.3f})"
        out.append(f"{r['rule']}: {alloc}; {r['expected_relevant']:.1f} relevant expected; interval in "
                   f"{r['p_interval']:.0%}; {marg}{mark}")
        for extra in ("note", "line", "low_count_note"):
            if r.get(extra):
                out.append(f"  {r[extra]}")
    out += p["lines"][2:]
    return out


def apply_plan(project: Project, p: dict, rule: str, author: str | None = None) -> dict:
    """Write the row ``rule`` of plan ``p`` into protocol.sampling.bands, as one History settings entry that undo
    restores; records the plan's inputs and the row used against the row recommended."""
    import json

    from ..core.journal import recording
    from ..core.protocol import SamplingBand
    from .design import sampling_log

    row = next((r for r in p["rows"] if r["rule"] == rule and r["rates"] == "middle"), None)
    if row is None and p.get("search") and p["search"].get("reached") and p["search"]["rule"] == rule:
        alloc = p["search"]["allocation"]
    elif row is None:
        raise PlanError(f"no allocation {rule} in this plan")
    else:
        alloc = row["allocation"]
    new = []
    for b in p["bands"]:
        kw = {"name": b["name"], "n": None if b["census"] else int(alloc[b["name"]])}
        if b.get("start_pct") is not None:
            kw |= {"start_pct": b["start_pct"], "end_pct": b["end_pct"]}
        else:
            kw |= {"start": b["start"], "end": b["end"]}
        new.append(SamplingBand(**kw))
    choice = {"used": rule, "recommended": p["recommended"],
              "inputs": {k: p[k] for k in ("budget", "target", "precision", "plan_power", "level", "sims",
                                           "expected_recall", "protocol", "result_set")} | {"rates": p["rates"]["middle"]}}
    summary = f"bands from plan {rule}" + (f" (used: {rule}; recommended: {p['recommended']})"
                                            if p["recommended"] and p["recommended"] != rule else "")
    with recording(project, "config", summary, author):
        project.config.protocol = project.config.protocol.set("sampling.bands", [b.model_dump() for b in new])
        project.save()
        project.store.set_meta("validation_plan", json.dumps(choice, default=str))
    lines = [f"protocol.sampling.bands = {', '.join(f'{k} {v}' for k, v in alloc.items())} (plan {rule})"]
    if p["recommended"] and p["recommended"] != rule:
        lines.append(f"used: {rule}; recommended: {p['recommended']}")
    name = project.config.protocol.sampling.probability_sample
    if name in sampling_log(project):
        lines.append(f"The sample {name} keeps its own bands; draw a new sample to use these")
    return {"bands": [b.model_dump() for b in new], "choice": choice, "lines": lines}


def run(project: Project, apply: str | None = None, author: str | None = None, **kw) -> dict:
    """What ``wider validation plan`` and ``POST /api/validation/plan`` both do: the plan, its printed lines and, with
    ``apply``, the row written into the bands."""
    percentiles = kw.pop("percentiles", None)
    if isinstance(percentiles, str):
        try:
            percentiles = [float(x) for x in percentiles.split(",") if x.strip()]
        except ValueError:
            raise PlanError("--percentiles takes numbers, e.g. 1,5,10,25,50") from None
    p = plan(project, percentiles=percentiles, **kw)
    out = {**p, "output": plan_lines(p)}
    if apply:
        a = apply_plan(project, p, apply, author)
        out["applied"] = a
        out["output"] += a["lines"]
    return out


# ------------------------------------------------------------------------------------------------ the suggestion (D5)
SUGGEST_DRAWS = 50
SUGGEST_INNER = 20
SUGGEST_INNER_DEEP = 10   # inner samples per checkpoint past DEEP labels (recall-certification-workload, D4 spike)
DEEP = 1000
SUGGEST_VERSION = 3       # a stored suggestion of an older version (capped at 300 labels; v2: cells as read now) is
                          # computed afresh
NO_LABELS = "no validation labels yet: plan the sample with wider validation plan"
NO_FRAME = "{name} was imported without a ranking: no records are left to suggest"
NEVER_A_PROMISE = "More labels make the answer clearer; they do not make the target more likely to be met."
STALE = "the labels changed since the last recall estimate; estimate recall again to update the suggestion"
PAST_300 = ("Recall is estimated again once the added records are labelled; adding them in smaller batches "
            "(wider validation extend --band) gives more looks. Once the result is settled, the rest need not be labelled.")
DEEP_NOTE = (f"Totals past {DEEP:,} labels are judged on {SUGGEST_INNER_DEEP} simulated samples instead of "
             f"{SUGGEST_INNER}, so their chances are rougher.")
KEPT, NOT_KEPT = "kept", "not kept"
IN_OTHER_OPEN = ("{n} records of these bands are in another sample and cannot be labelled here, so the bounds may not "
                 "close; a fresh sample (wider sample draw probability --name NEW --fresh-from {sample}) can settle it.")


class SuggestionStale(ValueError):
    """The labels or the result set changed since the stored suggestion (HTTP 409)."""


def sample_cells(project: Project, sample: str, key: str, primary: str | None = None) -> dict | None:
    """The cells of a drawn probability sample by band and screening decision (kept or not kept by result set
    ``key``): ``{"bands": [{name, in_other, cells: {kept: {N, n, a, left}, not kept: {...}}}], "n_labels"}``. N
    counts the band's frame records minus those the draw or last extension left out (``design.frame_excluded``), so
    it equals the design's N_h (recall-final-check-fixes D1); n and a the labelled and relevant ones (final labels,
    primary variant); ``left`` the unlabelled ids that can still be labelled here (not in another sample now, D4);
    ``in_other`` the band's records in N that another sample took since. None for a sample without a ranking."""
    from ..recall.estimate import VARIANTS
    from ..screen.decisions import decisions as screen_decisions
    from .design import frame_excluded, in_band, other_samples, sampling_log
    from .labels import final_labels

    primary = primary or project.config.protocol.recall.primary_variant
    log = sampling_log(project).get(sample)
    st = project.store
    from ..rank.versions import sample_ranking

    rk = sample_ranking(project, sample, log.get("ranking")) if log else None   # the version it was drawn from
    if not log or rk is None or not all("start" in b for b in log.get("bands", [])):
        return None
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    X, E = frame_excluded(project, sample), other_samples(project, sid)
    own = {int(r["record_id"]) for r in st.query("SELECT record_id FROM sample_rows WHERE sample_id=?", (sid,))}
    relevant, kept = VARIANTS[primary]
    dec = screen_decisions(project, key)
    labs = {r["record_id"]: r["decision"] for r in final_labels(project, sample) if r["decision"] is not None}
    frame = probability_frame(project, dict(rk))["frame"]
    out = []
    for b in log["bands"]:
        ids = [x["record_id"] for x in frame if in_band(x["rank"], b) and x["record_id"] not in X]
        cells = {}
        for c in (KEPT, NOT_KEPT):
            mine = [i for i in ids if (dec.get(i) in kept) == (c == KEPT)]
            lab = [i for i in mine if i in labs]
            cells[c] = {"N": len(mine), "n": len(lab), "a": sum(1 for i in lab if labs[i] in relevant),
                        "left": [i for i in mine if i not in labs and (i in own or i not in E)]}
        out.append({"name": b["band"], "in_other": sum(1 for i in ids if i in E and i not in own), "cells": cells})
    return {"bands": out, "n_labels": sum(c["n"] for b in out for c in b["cells"].values())}


def schedule(cap: int) -> list[int]:
    """The label totals the suggestion tries (D4): by 10 up to 300, by 50 up to 1,000, by 250 up to 5,000, by 1,000
    beyond, and always ``cap`` itself (every record left) last."""
    out, t = [], 0
    while True:
        t += STEP if t < 300 else 50 if t < 1000 else 250 if t < 5000 else 1000
        if t >= cap:
            break
        out.append(t)
    return out + ([cap] if cap > 0 else [])


def _path(H, N, n, p, room, cap):
    """The greedy path at the posterior-mean rates ``p`` [h, c], in steps of 10 (each to the band where it most lowers
    Σ N_h² s_h² (1/n_h − 1/N_h)), read at the totals of :func:`schedule` up to ``cap``. Returns [per-band additions],
    one per total."""
    Nh = N.sum(axis=1)
    share = np.where(Nh > 0, N[:, 0] / np.maximum(Nh, 1), 0.0)
    rk, rn = share * p[:, 0], (1 - share) * p[:, 1]
    R0 = (Nh * rk).sum() / max((Nh * (rk + rn)).sum(), 1e-12)
    s2 = rk * (1 - R0) ** 2 + rn * R0 ** 2 - (rk * (1 - R0) - rn * R0) ** 2
    nh = n.sum(axis=1).astype(float)

    def var(add):
        m = nh + add
        return np.where((m > 0) & (Nh > 0), Nh ** 2 * s2 * (1 / np.maximum(m, 1) - 1 / np.maximum(Nh, 1)), 0.0)

    marks = schedule(int(cap))
    add, path, total, i = np.zeros(H, int), [], 0, 0
    while total < cap and i < len(marks):
        now = var(add)
        best, gain = None, -1.0
        for h in range(H):
            step = min(STEP, room[h] - add[h], cap - total)
            if step <= 0:
                continue
            trial = add.copy()
            trial[h] += step
            g = float(now[h] - var(trial)[h])
            if g > gain:
                best, gain = (h, step), g
        if best is None:
            break
        add[best[0]] += best[1]
        total += best[1]
        while i < len(marks) and total >= marks[i]:
            path.append(add.copy())
            i += 1
    return path


def _inner(rng, theta, N, n, a, L, add, S, level, bounds):
    """S simulated samples adding ``add`` per band to the labels (without replacement from the records left, each
    relevant with its cell's rate ``theta``), judged on the exact bounds. Returns (low, high, estimate) arrays."""
    H = len(add)
    t = np.repeat(n[None], S, axis=0).astype(int)
    k = np.repeat(a[None], S, axis=0).astype(int)
    for h in range(H):
        if add[h] <= 0:
            continue
        kk = rng.hypergeometric(L[h, 0], L[h, 1], add[h], size=S) if L[h, 0] and L[h, 1] else \
            np.full(S, add[h] if L[h, 0] else 0)
        t[:, h, 0] += kk
        t[:, h, 1] += add[h] - kk
        k[:, h, 0] += rng.binomial(kk, theta[h, 0])
        k[:, h, 1] += rng.binomial(add[h] - kk, theta[h, 1])
    alpha = (1 - level) / (2 * H)
    low, high = np.empty(S), np.empty(S)
    for s in range(S):
        lk = uk = ln = un = 0
        for h in range(H):
            x, y = bounds(int(N[h, 0]), int(t[s, h, 0]), int(k[s, h, 0]), alpha)
            lk, uk = lk + x, uk + y
            x, y = bounds(int(N[h, 1]), int(t[s, h, 1]), int(k[s, h, 1]), alpha)
            ln, un = ln + x, un + y
        low[s] = lk / (lk + un) if lk + un else 0.0
        high[s] = uk / (uk + ln) if uk + ln else 1.0
    nb = t.sum(axis=2)
    w = np.where(nb > 0, N.sum(axis=1)[None] / np.maximum(nb, 1), 0.0)
    num = (w * k[:, :, 0]).sum(axis=1)
    den = (w * k.sum(axis=2)).sum(axis=1)
    est = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    return low, high, est


def _by_band(names, add) -> dict[str, int]:
    return {nm: int(v) for nm, v in zip(names, add) if v > 0}


def _band_text(d: dict) -> str:
    return ", ".join(f"{k} {v:,}" for k, v in d.items())


def _proportional(names, room, total) -> np.ndarray:
    """``total`` spread in proportion to the records left, in steps of 10 where the room allows."""
    room = np.asarray(room)
    if room.sum() <= 0:
        return np.zeros(len(names), int)
    units = _round({nm: total / STEP * r / room.sum() for nm, r in zip(names, room)}, total // STEP, list(names))
    add = np.minimum(np.array([units[nm] * STEP for nm in names]), room)
    short = total - add.sum()
    for h in np.argsort(-(room - add)):
        give = min(short, room[h] - add[h])
        add[h] += give
        short -= give
    return add


def _coarse(totals: list[int]) -> list[int]:
    """The checkpoints of the first pass, thinned by doubling (10, 20, 40, ...), and the last (every record left)."""
    out, target = [], STEP
    for j, t in enumerate(totals):
        if t >= target:
            out.append(j)
            while target <= t:
                target *= 2
    if totals and (not out or out[-1] != len(totals) - 1):
        out.append(len(totals) - 1)
    return out


def impractical_lines(m: int | None, rng: list | None, limit: int, left: int | None, target: float | None,
                      suffix: str = "") -> tuple[list[str], dict | None]:
    """The warning that settling the target may be impractical (D5), worked out at each estimate and status from the
    stored target part and the current ``recall.practical_labels`` (never cached): the warning when ``m`` is above
    ``limit``, the share line when ``m`` is half or more of the records left. Returns (lines, impractical)."""
    if m is None or target is None:
        return [], None
    lines, share = [], (m / left if left else None)
    if m > limit:
        lo, hi = (rng or [None, None])[0], (rng or [None, None])[1]
        within = (f" (likely between {lo:,} and {hi:,})" if lo is not None and hi is not None
                  else f" (likely more than {lo:,})" if lo is not None else "")
        lines.append(f"Settling whether the target {target:.2f} is met may be impractical: about {m:,} more labels"
                     f"{within} are expected before the bounds say met or not met, more than the {limit:,} you set as "
                     f"practical (recall.practical_labels). You may stop here and report the result as inconclusive; "
                     f"nothing requires more labels.{suffix}")
    if share is not None and share >= 0.5:
        lines.append(f"That is {share:.0%} of the {left:,} records left.")
    if not lines:
        return [], None
    return lines, {"m": m, "range": list(rng) if rng else None, "limit": limit,
                   "share_left": round(share, 2) if share is not None else None}


def _with_warning(project: Project, out: dict) -> dict:
    """The suggestion as shown: the stored one with the warning of :func:`impractical_lines` after its target part."""
    tg = out.get("target") or {}
    lines, info = impractical_lines(tg.get("m"), tg.get("range"), project.config.protocol.recall.practical_labels,
                                    out.get("left"), project.config.protocol.recall.target if tg else None,
                                    out.get("suffix", ""))
    res = {**out, "impractical": info}
    if lines:
        at = out.get("warn_at", len(out.get("lines") or []))
        res["lines"] = list(out["lines"][:at]) + lines + list(out["lines"][at:])
    return res


def suggest(project: Project, sample: str, key: str, look: dict | None = None, seed: int | None = None,
            seconds: float | None = None) -> dict | None:
    """How many more labels per band may be needed (D5), from the labels only: cells by band and screening decision,
    Jeffreys draws of their rates (a planning model, never the verdict), the greedy path read at the totals of
    :func:`schedule` (no cap but the records left), and inner samples drawn without replacement from the records left,
    each judged on the exact bounds: a coarse pass on totals thinned by doubling, then a refine pass where the answer
    lies, both on one grid for every draw. Stored on the look's state row (``look`` from ``record_look``), which a
    second call with the same labels reads back; the impracticality warning is added at each call, never stored.
    None when neither a target nor a precision is set."""
    import json

    rp = project.config.protocol.recall
    target, precision, level = rp.target, rp.precision, rp.ci_level
    if target is None and precision is None:
        return None
    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    if look and sid is not None:
        row = st.one("SELECT suggestion, set_key FROM validation_look_states WHERE sample_id=? AND labels_checksum=?",
                     (sid, look["checksum"]))
        if row and row["suggestion"] and row["set_key"] == key:
            old = json.loads(row["suggestion"])
            if old.get("v") == SUGGEST_VERSION:
                return _with_warning(project, old)
    cells = sample_cells(project, sample, key)
    if cells is None:
        return {"given": False, "lines": [NO_FRAME.format(name=sample)], "target": None, "precision": None}
    if cells["n_labels"] == 0:
        return {"given": False, "lines": [NO_LABELS], "target": None, "precision": None}
    names = [b["name"] for b in cells["bands"]]
    H = len(names)
    arr = lambda f: np.array([[f(b["cells"][c]) for c in (KEPT, NOT_KEPT)] for b in cells["bands"]])  # noqa: E731
    N, n, a = arr(lambda c: c["N"]), arr(lambda c: c["n"]), arr(lambda c: c["a"])
    L = arr(lambda c: len(c["left"]))
    room = L.sum(axis=1)
    cap = int(room.sum())
    in_other = sum(b["in_other"] for b in cells["bands"])   # counted in N, never labelled here (D4)
    nh = n.sum(axis=1)
    w = np.where(nh > 0, N.sum(axis=1) / np.maximum(nh, 1), 0.0)
    den = float((w * a.sum(axis=1)).sum())
    R_hat = float((w * a[:, 0]).sum()) / den if den else None
    path = _path(H, N, n, (a + 0.5) / (n + 1.0), room, cap)
    totals = [int(x.sum()) for x in path]
    rng = np.random.default_rng(project.config.seed if seed is None else seed)
    power = project.config.protocol.sampling.plan_power
    deadline = time.monotonic() + (rp.suggest_seconds if seconds is None else seconds)
    bounds = _Bounds()
    J = len(path)
    met = np.full((SUGGEST_DRAWS, J), np.nan)
    notm = np.full((SUGGEST_DRAWS, J), np.nan)
    marg = np.full((SUGGEST_DRAWS, J), np.nan)

    def evaluate(b, theta, js, first_draw):
        """Judge draw ``b`` at checkpoints ``js``; False when the time limit passed inside it (never in the first)."""
        for j in js:
            if not first_draw and time.monotonic() > deadline:
                return False
            S = SUGGEST_INNER_DEEP if totals[j] > DEEP else SUGGEST_INNER
            lo, hi, est = _inner(rng, theta, N, n, a, L, path[j], S, level, bounds)
            if target is not None:
                v = [judge({"low": x, "high": y}, target) for x, y in zip(lo, hi)]
                met[b, j] = sum(x == "met" for x in v) / S
                notm[b, j] = sum(x == "not met" for x in v) / S
            mm = (est - lo)[~np.isnan(est)]
            marg[b, j] = float(np.median(mm)) if mm.size else np.inf
        return True

    coarse = _coarse(totals)
    thetas, done = [], 0
    for b in range(SUGGEST_DRAWS):   # first pass: the coarse grid, for every draw that fits the time limit
        if b and time.monotonic() > deadline:
            break
        theta = rng.beta(a + 0.5, n - a + 0.5)
        if not evaluate(b, theta, coarse, b == 0):
            met[b], notm[b], marg[b] = np.nan, np.nan, np.nan   # a draw cut by the time limit is left out
            break
        thetas.append(theta)
        done += 1
    approx = done < SUGGEST_DRAWS

    def crossing(ok_cols):
        """Per draw, the first evaluated checkpoint that passes; the median draw's coarse interval to refine."""
        firsts = []
        for r in ok_cols[:done]:
            hit = [k for k, j in enumerate(coarse) if r[j]]
            firsts.append(hit[0] if hit else len(coarse))
        c = int(np.median(firsts)) if firsts else len(coarse)
        if c <= 0 or c >= len(coarse):
            return []
        return list(range(coarse[c - 1] + 1, coarse[c]))

    refine = []
    if J and done:
        if target is not None:
            refine += crossing(np.nan_to_num(met + notm) >= power)
        if precision is not None:
            refine += crossing(np.nan_to_num(marg, nan=np.inf) <= precision)
    refine = sorted(set(refine))
    if refine:   # second pass: the same refined checkpoints for every draw kept, or none of them
        for b in range(done):
            if not evaluate(b, thetas[b], refine, False):
                met[:, refine], notm[:, refine], marg[:, refine] = np.nan, np.nan, np.nan
                approx = True
                break
    met, notm, marg = met[:done], notm[:done], marg[:done]
    cols = [j for j in range(J) if done and not np.isnan(marg[0, j])]   # the checkpoints judged for every draw

    def first(ok):
        return np.array([next((totals[j] for j in cols if r[j]), np.inf) for r in ok])

    def summary(per_draw):
        if not cols or not len(per_draw):
            return None
        med = float(np.median(per_draw))
        if not np.isfinite(med):
            return None
        j = next((c for c in cols if totals[c] >= med), cols[-1])
        p10, p90 = (float(np.quantile(per_draw, q)) for q in (0.1, 0.9))
        return {"m": totals[j], "j": j, "by_band": _by_band(names, path[j]),
                "range": [int(p10), int(p90) if np.isfinite(p90) else None]}

    out = {"v": SUGGEST_VERSION, "given": True, "target": None, "precision": None, "approximate": approx,
           "draws": done, "cap": totals[-1] if totals else 0, "left": cap, "estimate": R_hat, "settles": True,
           "set_key": key, "checksum": look["checksum"] if look else None}
    lines = []
    suffix = f" (approximate: from {done} of {SUGGEST_DRAWS} draws; time limit reached)" if approx else ""
    out["suffix"] = suffix
    top = totals[-1] if totals else 0
    hi_text = lambda r: f"{r[1]:,}" if r[1] is not None else f"more than {top:,}"  # noqa: E731
    if target is not None and J and done:
        tg = summary(first(np.nan_to_num(met + notm) >= power))
        p_cap = float(met[:, -1].mean())
        if tg is None:   # close to the target, or records in another sample keep the bounds open (D4)
            out["settles"] = False
            chance = float((met[:, -1] + notm[:, -1]).mean())
            lines.append(f"Recall is estimated at {_f2(R_hat)}, close to the target {target:.2f}: even {top:,} more "
                         f"labels are not expected to settle it (chance of a decision about {chance:.0%}); consider "
                         f"your rule or a larger plan.{suffix}")
            if in_other:
                lines.append(IN_OTHER_OPEN.format(n=f"{in_other:,}", sample=sample))
        else:
            j = tg.pop("j")
            tg |= {"p_met": float(met[:, j].mean()), "p_not_met": float(notm[:, j].mean())}
            alt = _proportional(names, room, tg["m"])
            rng_alt = np.random.default_rng((project.config.seed if seed is None else seed) + 1)
            am = an = 0.0
            S = SUGGEST_INNER_DEEP if tg["m"] > DEEP else SUGGEST_INNER
            for theta in thetas:
                lo, hi, _ = _inner(rng_alt, theta, N, n, a, L, alt, S, level, bounds)
                v = [judge({"low": x, "high": y}, target) for x, y in zip(lo, hi)]
                am += sum(x == "met" for x in v) / S
                an += sum(x == "not met" for x in v) / S
            tg["alternative"] = {"by_band": _by_band(names, alt), "p_met": am / max(len(thetas), 1),
                                 "p_not_met": an / max(len(thetas), 1)}
            census = tg["m"] == cap and j == J - 1 and J > 1 and not in_other
            tg["census"] = census
            out["target"] = tg
            chances = (f"With {tg['m']:,} more, the chance is about {tg['p_met']:.0%} that the target is then shown "
                       f"met and {tg['p_not_met']:.0%} that it is shown not met.")
            if census:
                lines.append(f"Only labelling all {cap:,} records left in the sampled bands is expected to settle "
                             f"whether the target {target:.2f} is met. {chances}{suffix}")
            else:
                lines.append(f"More labels could settle it. To tell whether the target {target:.2f} is met at the next "
                             f"look: label about {tg['m']:,} more records ({_band_text(tg['by_band'])}), likely between "
                             f"{tg['range'][0]:,} and {hi_text(tg['range'])} given what the labels so far leave "
                             f"uncertain. {chances}{suffix}")
            ta = tg["alternative"]
            lines.append(f"Alternative: the same {tg['m']:,} spread in proportion to the records left "
                         f"({_band_text(ta['by_band'])}): chance about {ta['p_met']:.0%} met, "
                         f"{ta['p_not_met']:.0%} not met.")
            if tg["m"] > 300:
                lines.append(PAST_300)
            if tg["m"] > DEEP:
                lines.append(DEEP_NOTE)
        out["warn_at"] = len(lines)
        out["p_met_at_cap"] = p_cap
        if R_hat is not None and R_hat < target and p_cap < 0.10:
            lines.append(f"Recall is estimated at {_f2(R_hat)}, below the target {target:.2f}: more labels are "
                         f"unlikely to show the target met (chance about {p_cap:.0%} even with {top:,} more); they "
                         "would more likely show it not met.")
    if precision is not None and J and done:
        pr = summary(first(np.nan_to_num(marg, nan=np.inf) <= precision))
        if pr is None:
            lines.append(f"For a margin of {precision:.2f}: even {top:,} more labels are not expected to reach it."
                         + ("" if target is not None else suffix))
        else:
            pr.pop("j")
            out["precision"] = pr
            lines.append(f"For a margin of {precision:.2f}: about {pr['m']:,} more records ({_band_text(pr['by_band'])}), "
                         f"likely between {pr['range'][0]:,} and {hi_text(pr['range'])}."
                         + ("" if target is not None else suffix))
    if not J:
        lines.append(f"No records are left outside the other samples in the bands of {sample}: no more can be added.")
        out["settles"] = False
    lines.append(NEVER_A_PROMISE)
    out["lines"] = lines
    if look and sid is not None:
        st.execute("UPDATE validation_look_states SET suggestion=?, set_key=? WHERE sample_id=? AND labels_checksum=?",
                   (json.dumps(out), key, sid, look["checksum"]))
    return _with_warning(project, out)


WARNING_HEADS = ("Settling whether the target ",)


def _is_warning(line: str) -> bool:
    return line.startswith(WARNING_HEADS) or (line.startswith("That is ") and line.endswith(" records left."))


def refresh_warning(project: Project, sample: str, lines: list[str]) -> list[str]:
    """Stored look lines with the impracticality warning worked out again from the stored suggestion and the current
    ``recall.practical_labels`` (D5: a changed limit shows at the next status, with no new label)."""
    import json

    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    row = st.one("SELECT suggestion FROM validation_look_states WHERE sample_id=? AND suggestion IS NOT NULL "
                 "ORDER BY look_index DESC LIMIT 1", (sid,)) if sid is not None else None
    kept = [x for x in lines if not _is_warning(x)]
    if row is None:
        return kept
    sg = json.loads(row["suggestion"])
    if sg.get("v") != SUGGEST_VERSION or not sg.get("warn_at"):
        return kept
    shown = _with_warning(project, sg)["lines"]
    new = [x for x in shown if _is_warning(x)]
    anchor = sg["lines"][sg["warn_at"] - 1]
    if not new or anchor not in kept:
        return kept
    at = kept.index(anchor) + 1
    return kept[:at] + new + kept[at:]


def stored_suggestion(project: Project, sample: str, key: str, primary: str | None = None) -> dict | None:
    """The suggestion stored with the sample's last look; SuggestionStale when the labels or the result set changed
    since (D5, ``extend --suggested``)."""
    import json

    from ..recall.estimate import _rows
    from ..recall.validation import labels_checksum

    st = project.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name=?", (sample,))
    row = st.one("SELECT * FROM validation_look_states WHERE sample_id=? ORDER BY look_index DESC LIMIT 1", (sid,))
    if row is None or not row["suggestion"]:
        return None
    rows = _rows(project, sample, key, primary or project.config.protocol.recall.primary_variant)
    if row["labels_checksum"] != labels_checksum(rows) or row["set_key"] != key:
        raise SuggestionStale(STALE)
    return _with_warning(project, json.loads(row["suggestion"]))


def _f2(x) -> str:
    return "?" if x is None else f"{x:.2f}"
