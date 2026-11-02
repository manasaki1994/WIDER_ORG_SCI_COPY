"""Recall of the screening against human labels on the band-stratified probability sample.

Estimator: stratified Horvitz–Thompson ratio. For band h with N_h records and n_h sampled, weight w_h = N_h/n_h.
Recall = Σ_h w_h Σ_i y_i d_i / Σ_h w_h Σ_i y_i, where y_i = human says relevant, d_i = screening kept the record.
Strict: relevant = human include, kept = screening include. Lenient: relevant = include, kept = include or maybe.
Also reported: human include+maybe as relevant (what a cautious reader would want kept).

Primary interval: Korn–Graubard on the stratified linearised variance, Kish's effective size at recall 0 or 1, or
the reason there is none (:mod:`wider.recall.uncertainty`), with the plain sentences every surface prints.
Cross-checks (report method section and JSON): a seeded stratified percentile bootstrap (census strata fixed, sampled
strata resampled with replacement) and the linearised (Taylor) variance with a logit Wald interval; both collapse to
a point at recall 0 or 1. Bands with zero positives get a rule-of-three note (upper bound of a proportion with 0/n
observed ≈ 3/n).
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

from ..core.project import Project
from ..sample.labels import final_labels
from ..screen.decisions import decisions as screen_decisions
from .uncertainty import METHOD_SENTENCE, stratified_uncertainty, uncertainty_lines

MAX_BOOTSTRAP_B = 50_000   # about 0.05 ms per replicate and row: 50,000 keep a 300-row sample under 10 s for the three variants


def disagreement_line(n: int, used: list[str], what: str = "estimate") -> str:
    """"3 labelled records where the reviewers disagree are not adjudicated; the estimate uses alice's labels there.
    Adjudicating them gives a firmer estimate." (ui-tester finding: the estimate must say whose labels it uses);
    ``what`` is the evaluation or the comparison for those outputs (manual-review D9)."""
    who = " and ".join(f"{u}'s" for u in used) if used else "the first reviewer's"
    return (f"{n} labelled record{' where the reviewers disagree is' if n == 1 else 's where the reviewers disagree are'} "
            f"not adjudicated; the {what} uses {who} labels there. Adjudicating them gives a firmer {what}.")


def open_disagreements(labels: list[dict]) -> tuple[int, list[str]]:
    """How many of these final labels are open disagreements, and whose labels stand there (``final_labels`` rows)."""
    rows = [r for r in labels if r.get("open_disagreement")]
    return len(rows), sorted({str(r["source"]) for r in rows})


def _rows(project: Project, sample: str, protocol: str, primary: str | None):
    """``protocol`` is the decisions key (the active set's, or the chosen set's)."""
    labs = final_labels(project, sample, primary)
    dec = screen_decisions(project, protocol)
    rows = []
    sampled: dict = {}
    for r in labs:
        if r["band"] is None or r["weight"] is None:
            continue
        sampled[r["band"]] = sampled.get(r["band"], 0) + 1
        if r["decision"] is None:
            continue
        rows.append({"record_id": r["record_id"], "band": r["band"], "w": float(r["weight"]), "human": r["decision"],
                     "screen": dec.get(r["record_id"], "unscreened"), "source": r.get("source"),
                     "open_disagreement": bool(r.get("open_disagreement"))})
    # The design weight is N_h/n_h over the sampled records; only the labelled ones are kept, so the weight becomes
    # N_h/t_h over those (design D13): an unlabelled sampled record counts neither as relevant nor as not relevant.
    labelled: dict = {}
    for r in rows:
        labelled[r["band"]] = labelled.get(r["band"], 0) + 1
    for r in rows:
        r["w"] *= sampled[r["band"]] / labelled[r["band"]]
    return rows


VARIANTS = {
    "strict": (("include",), ("include",)),
    "lenient": (("include",), ("include", "maybe")),
    "cautious": (("include", "maybe"), ("include", "maybe")),
}


def _ratio(rows, relevant, kept) -> tuple[float | None, float, float]:
    num = sum(r["w"] for r in rows if r["human"] in relevant and r["screen"] in kept)
    den = sum(r["w"] for r in rows if r["human"] in relevant)
    return (num / den if den else None), num, den


def _bootstrap(rows, relevant, kept, B: int, seed: int, level: float = 0.95) -> tuple[float, float] | None:
    rng = np.random.default_rng(seed)
    bands = {}
    for r in rows:
        bands.setdefault(r["band"], []).append(r)
    census = [b for b, lst in bands.items() if abs(lst[0]["w"] - 1.0) < 1e-9]
    est = []
    for _ in range(B):
        num = den = 0.0
        for b, lst in bands.items():
            if b in census:
                sel = lst
            else:
                idx = rng.integers(0, len(lst), len(lst))
                sel = [lst[i] for i in idx]
            for r in sel:
                if r["human"] in relevant:
                    den += r["w"]
                    if r["screen"] in kept:
                        num += r["w"]
        if den > 0:
            est.append(num / den)
    if not est:
        return None
    a = 100 * (1 - level) / 2
    lo, hi = np.percentile(est, [a, 100 - a])
    return float(lo), float(hi)


def _linearised(rows, relevant, kept, level: float = 0.95) -> dict | None:
    """Taylor variance of R = Y/X with stratified weights; logit Wald interval at ``level`` (0.95 gives z = 1.96)."""
    R, num, den = _ratio(rows, relevant, kept)
    if R is None or den == 0:
        return None
    var = 0.0
    bands = {}
    for r in rows:
        bands.setdefault(r["band"], []).append(r)
    for lst in bands.values():
        n = len(lst)
        w = lst[0]["w"]
        if n < 2 or abs(w - 1.0) < 1e-9:
            continue  # census stratum contributes no sampling variance
        N = w * n
        z = [((r["human"] in relevant and r["screen"] in kept) - R * (r["human"] in relevant)) for r in lst]
        s2 = float(np.var(z, ddof=1))
        var += (N * N) * (1 - n / N) * s2 / n
    se = math.sqrt(var) / den
    z = NormalDist().inv_cdf(0.5 + level / 2)
    if R <= 0 or R >= 1:
        return {"se": se, "ci": (max(0.0, R - z * se), min(1.0, R + z * se)), "method": "wald"}
    lg = math.log(R / (1 - R))
    se_lg = se / (R * (1 - R))
    lo, hi = lg - z * se_lg, lg + z * se_lg
    f = lambda x: 1 / (1 + math.exp(-x))  # noqa: E731
    return {"se": se, "ci": (f(lo), f(hi)), "method": "logit_wald"}


def check_names(project: Project, sample: str | None = None, protocol: str | None = None,
                result_set: int | None = None) -> dict | None:
    """ValueError naming an unknown protocol (no screening decisions in the set read) or an unknown sample; KeyError
    "no result set" for an unknown set; ValueError for an out-of-step second-screen set. Returns the set read."""
    from ..screen.result_sets import reader

    st = project.store
    info = None
    if protocol is not None:
        info = reader(project, protocol, result_set)
        if not st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (info["key"],)):
            raise ValueError(f"no screening decisions under protocol {protocol!r}"
                             + (f" set {result_set}" if result_set else ""))
    if sample is not None and not st.scalar("SELECT COUNT(*) FROM samples WHERE name=?", (sample,)):
        raise ValueError(f"no sample {sample!r}")
    return info


def _band_note(lst, rel) -> str:
    """The note for a band with no relevant paper in the sample: the rule-of-three bound on what a sampled band could
    hide; a census band (weight 1, every record labelled) hides nothing."""
    if rel or not lst:
        return ""
    if abs(lst[0]["w"] - 1.0) < 1e-9:
        return "no positives; every record in the band was labelled"
    return f"no positives in sample; rule of three: missed rate ≤ {3 / len(lst):.2f}"


def estimate(project: Project, sample: str, protocol: str, primary: str | None = None, B: int | None = None,
             seed: int | None = None, record_result: bool = True, result_set: int | None = None,
             compare: int | None = None, record_look: bool = True, choice: str | None = None) -> dict:
    """Recall of ``protocol`` on ``sample``, read from the protocol's active result set or set ``result_set``.
    Unknown names raise ValueError before anything is recorded; the report and read-only callers pass
    ``record_result=False`` so only an explicit estimate is recorded as a stage result. ``compare`` adds the same
    estimate for another set of the protocol (``compare``), never changing the active set. More than
    ``MAX_BOOTSTRAP_B`` bootstrap replicates are capped (``B_requested`` keeps the asked number), so a huge
    ``protocol.recall.bootstrap_b`` or ``-B`` never makes the estimate run for ever. On a probability sample the
    estimate is a look (D7), also for ``compare``; only the report's internal re-reads pass ``record_look=False``.
    A set made after a look with other settings needs ``choice`` (``used`` | ``not_informed``); without it
    :class:`~wider.recall.validation.SampleChoiceNeeded` is raised before anything is recorded (D8). A probability
    sample whose labels are not complete raises :class:`~wider.recall.validation.LabelsIncomplete` before that, so
    nothing is estimated or recorded (label-completion-and-sealing D4)."""
    from ..sample.labels import completeness, incomplete_notice, sample_by_name
    from .validation import LabelsIncomplete, SampleChoiceNeeded, choice_lines, needs_choice, record_choice

    info = check_names(project, sample, protocol, result_set)
    if sample_by_name(project, sample)["kind"] == "probability":
        c = completeness(project, sample)
        if not c["complete"]:
            raise LabelsIncomplete(incomplete_notice(project, c), sample, c)
    if choice:
        record_choice(project, sample, info["key"], choice)
    notice = needs_choice(project, sample, info["key"])
    if notice:
        raise SampleChoiceNeeded(notice, sample, info["key"])
    rp = project.config.protocol.recall
    B_requested = B or rp.bootstrap_b
    B = min(B_requested, MAX_BOOTSTRAP_B)
    rows = _rows(project, sample, info["key"], primary)
    seed = project.config.seed if seed is None else seed
    from ..screen.result_sets import _models, row_by_key, run_ids
    srow = row_by_key(project, info["key"]) or {}
    out = {"sample": sample, "protocol": protocol, "result_set": info["result_set"], "result_set_key": info["key"],
           "result_set_label": info["label"], "result_set_role": info["role"], "role_note": info["role_note"],
           "models": _models(project, srow) if srow else [], "run_ids": run_ids(srow) if srow else [],
           "date": srow.get("created_at"),
           "n_labelled": len(rows), "B": B, "B_requested": B_requested, "seed": seed,
           "open_disagreements": sum(1 for r in rows if r["open_disagreement"]),
           "labels_used": sorted({str(r["source"]) for r in rows if r["open_disagreement"]}),
           "method_note": METHOD_SENTENCE,
           "unscreened_in_sample": sum(1 for r in rows if r["screen"] == "unscreened"), "variants": {}, "bands": [],
           "primary_variant": rp.primary_variant, "ci_level": rp.ci_level}
    for name, (relevant, kept) in VARIANTS.items():
        R, num, den = _ratio(rows, relevant, kept)
        boot = _bootstrap(rows, relevant, kept, B, seed, rp.ci_level) if R is not None else None
        lin = _linearised(rows, relevant, kept, rp.ci_level)
        u = stratified_uncertainty(rows, relevant, kept, rp.ci_level)
        out["variants"][name] = {
            "relevant": list(relevant), "kept": list(kept), "recall": R,
            "weighted_relevant": den, "weighted_kept": num,
            "n_relevant": sum(1 for r in rows if r["human"] in relevant),
            "n_missed": sum(1 for r in rows if r["human"] in relevant and r["screen"] not in kept),
            "interval": list(u["interval"]) if u["interval"] else None, "interval_method": u["method"],
            "n_effective": u["n_effective"], "df": u["df"], "interval_reason": u["reason"],
            "lines": uncertainty_lines(u),
            "ci_bootstrap": boot, "ci_linearised": lin["ci"] if lin else None, "se_linearised": lin["se"] if lin else None,
        }
    out["lines"] = list((out["variants"].get(rp.primary_variant) or {}).get("lines", []))
    out["lines"] += choice_lines(project, sample, info["key"])
    if out["open_disagreements"]:
        out["lines"].append(disagreement_line(out["open_disagreements"], out["labels_used"]))
    from ..sample.package import validation_out
    out["lines"] += validation_out(project, sample)   # a review package out and not imported (blinding D11)
    from ..sample.labels import sealed_change_line, sealed_disagreement_line, sealed_disagreements
    changed = sealed_change_line(project, sample)   # sealed references corrected since (label-completion D6)
    if changed:
        out["lines"].append(changed)
    out["sealed_disagreements"] = sealed_disagreements(project, sample)   # recall-final-check-fixes D6
    if out["sealed_disagreements"]:
        out["lines"].append(sealed_disagreement_line(project, sample))
    bands = sorted({r["band"] for r in rows})
    for b in bands:
        lst = [r for r in rows if r["band"] == b]
        rel = [r for r in lst if r["human"] == "include"]
        kept_s = sum(1 for r in rel if r["screen"] == "include")
        kept_l = sum(1 for r in rel if r["screen"] in ("include", "maybe"))
        out["bands"].append({"band": b, "n": len(lst), "weight": lst[0]["w"], "human_include": len(rel),
                             "human_maybe": sum(1 for r in lst if r["human"] == "maybe"),
                             "kept_strict": kept_s, "kept_lenient": kept_l,
                             "note": _band_note(lst, rel)})
    missed = [r for r in rows if r["human"] == "include" and r["screen"] not in ("include", "maybe")]
    out["missed_records"] = [{"record_id": r["record_id"], "band": r["band"], "weight": r["w"], "screen": r["screen"]} for r in missed]
    if compare is not None:
        try:
            other = estimate(project, sample, protocol, primary, B, seed, record_result=False, result_set=compare,
                             record_look=record_look)
        except SampleChoiceNeeded as e:   # the compared set waits for its own choice (D7, D8)
            other = None
            out["compare"] = {"choice_needed": True, "notice": e.notice}
    if compare is not None and other is not None:
        out["compare"] = {"result_set": other["result_set"], "result_set_key": other["result_set_key"],
                          "variants": {k: {"recall": v["recall"], "n_missed": v["n_missed"], "interval": v["interval"],
                                           "interval_reason": v["interval_reason"]}
                                       for k, v in other["variants"].items()}}
    if record_look:
        from .validation import record_look as _look

        out["look"] = _look(project, sample, info["key"], rows, rp.ci_level, primary)
        if out["look"] is not None:
            _exact(project, sample, info["key"], out["look"], out, primary)
    from .validation import benchmark_apart, model_selection_line, scope_line, used_sample_lines

    chosen = model_selection_line(project, sample)   # after this look, so the set just seen counts (D7)
    if chosen:
        out["lines"].append(chosen)

    out["benchmark_apart"] = benchmark_apart(project, info["key"], rp.ci_level)
    out["used_lines"] = used_sample_lines(project, sample, info["key"], primary)
    out["scope_line"] = scope_line(project, sample)
    out["lines"] += out["benchmark_apart"]["lines"] + out["used_lines"] + ([out["scope_line"]] if out["scope_line"] else [])
    if record_result:
        from ..core.stage_result import record
        from ..core.stage_summaries import summarize_recall
        record(project, summarize_recall(out))
    return out


def _exact(project: Project, sample: str, key: str, look: dict, out: dict, primary: str | None = None) -> None:
    """At a look on a probability sample (``look`` from ``record_look``: index, first, checksum): ``bounds`` and ``outcome`` (D15, D5) and their lines, also
    stored as ``last_validation_lines:<sample>`` for ``wider validation status`` (which records no look), with the time
    ``at`` and, under ``last_recall_curve:<sample>``, the relevant-found series of the Results dashboard (D4, D5)."""
    import json

    from ..core.provenance import utcnow
    from . import curve
    from .validation import look_block

    blk = look_block(project, sample, key, look, out)
    if blk is None:
        return
    from ..sample.labels import criteria_lines
    blk["lines"] = blk["lines"] + criteria_lines(project, sample)   # the criteria-change lines (D11)
    out["bounds"], out["outcome"], out["validation_lines"] = blk["bounds"], blk["outcome"], blk["lines"]
    out["validation_stopped"], out["validation_unlabelled"] = blk["stopped"], blk["unlabelled"]
    if blk["suggestion"] is not None:
        out["more_validation"] = blk["suggestion"]
    out["lines"] += blk["lines"]
    at = utcnow()
    project.store.set_meta(f"last_validation_lines:{sample}", json.dumps(
        {"result_set_key": key, "at": at, "look": blk["look"], "outcome": blk["outcome"], "lines": blk["lines"],
         "target": project.config.protocol.recall.target,   # what the verdict was judged against (audit C2)
         "recall": (out["variants"].get(out["primary_variant"]) or {}).get("recall"),
         "variant": out["primary_variant"],   # the headline names it (the Results dashboard's Recall section)
         "bounds": {k: blk["bounds"][k] for k in ("low", "high", "level", "method")},
         "population": curve.POPULATION}))
    curve.store(project, sample, key, at, primary, blk["bounds"])


def screening_counts(project: Project, protocol: str, result_set: int | None = None) -> dict:
    from ..screen.decisions import summary
    from ..screen.result_sets import active_key
    return {**summary(project, active_key(project, protocol, result_set)), "protocol": protocol}
