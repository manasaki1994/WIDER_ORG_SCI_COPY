"""How sure a recall figure is, said once for every surface (change recall-interval-honest, design D2–D6).

A recall of ``found`` of ``total`` relevant papers gets an approximate interval, Clopper–Pearson bounds on an *effective* number
of papers. On the band-stratified validation sample (unequal weights) that number comes from the design:
inside (0, 1) it is the Korn–Graubard effective number R(1−R)/v from the stratified linearised variance v, times a
degrees-of-freedom factor and never more than Kish's effective size (Σw)²/Σw² of the relevant papers' weights; at
recall 0 or 1 (or zero variance) v says nothing, so it is that Kish size itself. The common cap keeps one more missed
paper from narrowing the interval (with the raw count as cap it could). Equal weights give back the plain
Clopper–Pearson interval (10 of 10 found: 0.69 to 1.00). When no interval can be defended the result carries a
reason instead, and :func:`uncertainty_lines` turns any result into the plain sentences the Recall page, the CLI, the
stage result and the report all print.

:func:`recall_uncertainty` without design information is the plain exact interval on found of total, for known papers
on the search side (equal weights, not a probability sample).
"""
from __future__ import annotations

import numpy as np

NO_RELEVANT = "the labelled sample holds no relevant validation papers"
FEW_DF = "too few relevant papers across the bands to estimate the variance"
METHOD_NAMES = {"korn_graubard": "Korn–Graubard", "kish": "Kish effective size", "clopper_pearson": "Clopper–Pearson",
                "census": "census (every record of every band labelled)"}
CENSUS = ("Every record of every band was labelled, so this recall is exact for these records: there is no sampling "
          "uncertainty, and no interval is needed. It is only as good as the labels.")


def beta_interval(x: float, n: float, level: float = 0.95) -> tuple[float, float]:
    """Clopper–Pearson bounds for ``x`` successes of ``n`` trials, both possibly fractional (an effective number)."""
    from scipy.stats import beta

    a = (1 - level) / 2
    lo = 0.0 if x <= 0 else float(beta.ppf(a, x, n - x + 1))
    hi = 1.0 if x >= n else float(beta.ppf(1 - a, x + 1, n - x))
    return lo, hi


def recall_uncertainty(found: int, total: int, *, level: float = 0.95, recall: float | None = None,
                       n_effective: float | None = None, df: int | None = None, method: str | None = None,
                       reason: str | None = None) -> dict:
    """``found`` of ``total`` relevant papers kept, with an interval or the reason there is none.

    Without ``recall`` and ``n_effective`` this is the exact interval on found/total (method "clopper_pearson").
    Returns {found, n_relevant, recall, level, interval, method, n_effective, df, reason}; ``interval`` is (lo, hi)
    or None, and with ``total`` 0 the recall is None as well."""
    out = {"found": found, "n_relevant": total, "recall": None, "level": level, "interval": None, "method": None,
           "n_effective": None, "df": df, "reason": None}
    if total <= 0:
        return {**out, "reason": NO_RELEVANT}
    if not 0 <= found <= total:
        raise ValueError("found must be between 0 and total")
    r = found / total if recall is None else recall
    out["recall"] = r
    if reason:
        return {**out, "reason": reason}
    n = float(total if n_effective is None else n_effective)
    x = 0.0 if r <= 0 else n if r >= 1 else r * n
    return {**out, "interval": beta_interval(x, n, level), "method": method or "clopper_pearson", "n_effective": n}


def ratio_variance(rows, relevant, kept) -> tuple[float | None, float, list[str]]:
    """Recall R = Σw·y·d / Σw·y over the rows ({band, w, human, screen}), its stratified linearised (Taylor) variance
    with the finite-population factor, and the sampled bands (weight ≠ 1) that hold a relevant paper but only one
    labelled record, whose variance cannot be computed. Census bands (weight 1) add no variance."""
    den = sum(r["w"] for r in rows if r["human"] in relevant)
    if not den:
        return None, 0.0, []
    R = sum(r["w"] for r in rows if r["human"] in relevant and r["screen"] in kept) / den
    bands: dict = {}
    for r in rows:
        bands.setdefault(r["band"], []).append(r)
    var, lonely = 0.0, []
    for b, lst in bands.items():
        n, w = len(lst), lst[0]["w"]
        if abs(w - 1.0) < 1e-9:
            continue
        if n < 2:
            if any(r["human"] in relevant for r in lst):
                lonely.append(b)
            continue
        z = [(r["human"] in relevant and r["screen"] in kept) - R * (r["human"] in relevant) for r in lst]
        mean = sum(z) / n
        s2 = sum((v - mean) ** 2 for v in z) / (n - 1)
        N = w * n
        var += N * N * (1 - n / N) * s2 / n
    return R, var / (den * den), sorted(lonely)


def empty_sampled_bands(rows, relevant) -> list[tuple]:
    """The sampled bands (weight not 1) whose labelled records hold no relevant paper, as (band, n, weight), in band
    order. Such a band may hide relevant papers the screening missed, and nothing in the sample says how many; the
    simulation study of design D10 found the interval unreliable exactly then, so none is given (D3)."""
    bands: dict = {}
    for r in rows:
        bands.setdefault(r["band"], []).append(r)
    return sorted((b, len(lst), lst[0]["w"]) for b, lst in bands.items()
                  if abs(lst[0]["w"] - 1.0) > 1e-9 and not any(r["human"] in relevant for r in lst))


def empty_bands_reason(empty: list[tuple]) -> str:
    """"band S3 shows no relevant paper in its 40 labelled records (1 in 75 sampled), so what it hides cannot be
    estimated"; several bands are joined with semicolons."""
    parts = [f"band {b} shows no relevant paper in its {n} labelled record{'' if n == 1 else 's'} (1 in {round(w, 1):g} "
             "sampled)" for b, n, w in empty]
    return "; ".join(parts) + f", so what {'it hides' if len(empty) == 1 else 'they hide'} cannot be estimated"


METHOD_SENTENCE = ("The interval is approximate: Clopper–Pearson bounds on an effective number of relevant papers "
                   "(Korn–Graubard from the stratified variance; Kish's effective size at recall 0 or 1), never more "
                   "than that Kish size. In simulated samples like WIDER's it held the true recall at least as often "
                   "as its level promises wherever it is shown, usually more often, so it errs on the wide side.")


def stratified_uncertainty(rows, relevant, kept, level: float = 0.95, band_rule: bool = True) -> dict:
    """The primary recall interval on the band-stratified sample (design D2, D3, D10), as :func:`recall_uncertainty`.
    No interval when a sampled band shows no relevant paper (what it hides is unknown), when a sampled band holds a
    relevant paper but a single labelled record, or when fewer than one degree of freedom is left."""
    from scipy.stats import t

    rel = [r for r in rows if r["human"] in relevant]
    n = len(rel)
    found = sum(1 for r in rel if r["screen"] in kept)
    if not n:
        return recall_uncertainty(0, 0, level=level)
    R, v, lonely = ratio_variance(rows, relevant, kept)
    df = n - len({r["band"] for r in rel})
    sw = sum(r["w"] for r in rel)
    kish = min(sw * sw / sum(r["w"] ** 2 for r in rel), n)
    if all(abs(r["w"] - 1.0) < 1e-9 for r in rows):   # a census: the frame was labelled in full, recall is exact
        return {**recall_uncertainty(found, n, level=level, recall=R, df=df), "interval": (R, R), "method": "census"}
    empty = empty_sampled_bands(rows, relevant) if band_rule else []   # band_rule=False only for the simulation study
    if empty:
        return recall_uncertainty(found, n, level=level, recall=R, df=df, reason=empty_bands_reason(empty))
    if 0 < R < 1:
        if lonely:
            return recall_uncertainty(found, n, level=level, recall=R, df=df,
                                      reason=f"band {', '.join(map(str, lonely))} holds a relevant paper but only one "
                                             "labelled record")
        if df < 1:
            return recall_uncertainty(found, n, level=level, recall=R, df=df, reason=FEW_DF)
        if v > 0:
            a = (1 - level) / 2
            factor = (t.ppf(a, n - 1) / t.ppf(a, df)) ** 2 if n > 1 else 1.0
            n_eff = min(R * (1 - R) / v * factor, kish)
            return recall_uncertainty(found, n, level=level, recall=R, n_effective=n_eff, df=df,
                                      method="korn_graubard")
    return recall_uncertainty(found, n, level=level, recall=R, n_effective=kish, df=df, method="kish")


def _census(u: dict) -> bool:
    """A census result: the method of :func:`recall_uncertainty` or, on a stored estimate, ``interval_method``."""
    return (u.get("method") or u.get("interval_method")) == "census"


def _num(x: float) -> str:
    return f"{x:.2f}"


def level_label(level: float | None) -> str:
    """0.95 -> "95%", 0.9 -> "90%", 0.975 -> "97.5%"."""
    return f"{round(float(level if level is not None else 0.95) * 100, 1):g}%"


def interval_text(u: dict) -> str:
    """The interval cell of a table: "0.43–1.00", or the words when there is none."""
    if _census(u):
        return "exact (census)"
    if u.get("interval"):
        return f"{u['interval'][0]:.2f}–{u['interval'][1]:.2f}"
    return "–" if u.get("recall") is None else "could not be reliably estimated"


def interval_words(u: dict) -> str:
    """The interval inside a sentence (headlines, the methods draft): "95% interval 0.43 to 1.00", or the words when
    there is none."""
    iv = u.get("interval")
    if _census(u):
        return "exact, every record labelled"
    if iv:
        return f"{level_label(u.get('level'))} interval {_num(iv[0])} to {_num(iv[1])}"
    return "–" if u.get("recall") is None else "uncertainty could not be reliably estimated"


def papers_text(n: int, noun: str = "relevant validation paper") -> str:
    """ "7 relevant validation papers", "1 relevant validation paper"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


LABEL_MORE = "Labelling more of the sample gives a firmer estimate."


def uncertainty_lines(u: dict, *, noun: str = "relevant validation paper", few_below: int = 20,
                      subject: str = "the screening", keeps: str = "keeps", kept: str = "kept",
                      remedy: str | None = LABEL_MORE) -> list[str]:
    """The plain sentences for one result of :func:`recall_uncertainty` (design D4): the count, the interval or why
    there is none, a note when the papers are few, and what perfect or zero observed recall does and does not show.
    The order is fixed (count, interval, notes), so ``lines[2:]`` are the notes; without a recall there is one line.
    Another caller (the search side's known papers) passes its own ``noun``, ``subject`` ("the query"), verbs
    ("finds", "found") and ``remedy`` (None leaves it out)."""
    n = u.get("n_relevant") or 0
    if u.get("recall") is None:
        return [f"Recall could not be estimated: {u.get('reason') or NO_RELEVANT}."]
    lines = [f"Based on {papers_text(n, noun)}."]
    iv = u.get("interval")
    if _census(u):
        lines.append(CENSUS)
        if u["recall"] >= 1:
            lines.append(f"No {noun} was missed among these records.")
        elif u["recall"] <= 0:
            lines.append(f"{subject[:1].upper()}{subject[1:]} {kept} none of the {noun}s.")
        return lines
    if iv:
        lines.append(f"{level_label(u.get('level'))} interval: {_num(iv[0])} to {_num(iv[1])}.")
    else:
        lines.append(f"Uncertainty could not be reliably estimated: {u.get('reason')}.")
    if n < few_below:
        lines.append("That is few: one paper more or fewer missed would change the estimate a lot."
                     + (f" {remedy}" if remedy else ""))
    if u["recall"] >= 1:
        tail = f": a recall as low as {_num(iv[0])} fits what was seen." if iv else "."
        lines.append(f"No {noun} was missed. This does not mean {subject} {keeps} every relevant paper{tail}")
    elif u["recall"] <= 0:
        lines.append(f"{subject[:1].upper()}{subject[1:]} {kept} none of the {noun}s.")
    return lines


def method_text(u: dict) -> str:
    """The method cell of the technical report: name, effective number and degrees of freedom."""
    if not u.get("method"):
        return "none"
    parts = [METHOD_NAMES.get(u["method"], u["method"])]
    if u.get("n_effective") is not None:
        parts.append(f"effective n {u['n_effective']:.2f}")
    if u["method"] == "korn_graubard" and u.get("df") is not None:
        parts.append(f"df {u['df']}")
    return ", ".join(parts)



MODEL_ASSUMPTION = "a Jeffreys prior for each band and screening decision"


def model_interval(cells: list[dict], level: float = 0.95, draws: int = 2000, seed: int = 0) -> dict:
    """The model-based supplement of design D17: for each cell ({band, decision, N, t, k}) a relevance rate from the
    Jeffreys posterior Beta(k + 1/2, t - k + 1/2), and Binomial(N - t, rate) relevant records among those not
    labelled; recall is kept / all over the cells, and the interval its quantiles at ``level``. An assumption, shown
    only where the Korn–Graubard interval is missing and used by the planner as a planning model; it never decides
    the target. Returns {low, high, level, assumption}."""
    rng = np.random.default_rng(seed)
    kept = np.zeros(draws)
    total = np.zeros(draws)
    for c in cells:
        N, t, k = int(c["N"]), int(c["t"]), int(c["k"])
        left = max(N - t, 0)
        rel = k + (rng.binomial(left, rng.beta(k + 0.5, t - k + 0.5, draws)) if left else np.zeros(draws))
        total += rel
        if c["decision"] == "kept":
            kept += rel
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(total > 0, kept / np.maximum(total, 1), np.nan)
    r = r[~np.isnan(r)]
    a = (1 - level) / 2
    lo, hi = (float(np.quantile(r, a)), float(np.quantile(r, 1 - a))) if len(r) else (0.0, 1.0)
    return {"low": lo, "high": hi, "level": level, "assumption": MODEL_ASSUMPTION}


def model_interval_line(m: dict) -> str:
    """"Supplementary, model-based (an assumption: ...; it does not decide the target): 0.71 to 0.97." """
    return (f"Supplementary, model-based (an assumption: {m['assumption']}; it does not decide the target): "
            f"{_num(m['low'])} to {_num(m['high'])}.")
