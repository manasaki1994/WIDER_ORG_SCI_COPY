---
id: recall-estimate
title: The recall estimate and its interval
context: After the screening has run and every record of the validation sample (the probability sample) has a final human label, WIDER estimates what share of the relevant records the screening kept.
settings: [recall.primary_variant, recall.ci_level, recall.bootstrap_b]
artifacts: []
pages: [recall, results]
---

## In short

Recall is the share of relevant papers that the screening kept. WIDER compares the screening with your labels on the validation sample, drawn band by band from the ranking. Each labelled record stands for the undrawn records of its band, so records from thinly sampled bands count more. You see the estimate, an interval at {default:recall.ci_level} confidence and the number of relevant papers it rests on. Strict recall counts only the screening's includes as kept; lenient also its maybes; cautious also counts your maybe labels as relevant. The main one is {default:recall.primary_variant} unless you change it. When the interval cannot be trusted, WIDER shows none and says why.

## Why

Broad searches hold few relevant papers, so a simple random sample would find too few. Drawing more from high-score bands finds more, and the weights (records in the band divided by records drawn from it) correct for the unequal chances.

A bootstrap within bands, as the vision suggested, is only a report cross-check: when every sampled relevant paper was kept, every resample gives recall 1 and the interval shrinks to a point, as does the linearised one. The main interval instead turns the design's uncertainty into an effective number of relevant papers and puts exact binomial bounds on it. It never shrinks to a point; with equal weights it is the plain exact interval.

It is an approximation, not an exact or validated interval. In WIDER's simulation study it held the true recall at least as often as its level promised wherever it was shown, so it errs wide; that is evidence for the scenarios studied, not a proof for every design. It failed in one situation: a sampled band showing no relevant paper while hiding missed ones. There WIDER gives no interval and suggests labelling more of that band.

This interval describes one look. Judging a recall target, possibly after adding labels, uses other bounds (see the recall target topic). Known seed papers are reported apart and never pooled, because they are not a random sample.

## For auditors

- Point estimate: stratified Horvitz–Thompson ratio, the sum of w·y·d over the sum of w·y, w = N_h/n_h, y relevant, d kept, per variant (`wider/recall/estimate.py:estimate`, `wider/recall/estimate.py:VARIANTS`). Only approximately unbiased.
- Interval (`wider/recall/uncertainty.py:stratified_uncertainty`): linearised variance v with finite-population correction, census bands adding none (`wider/recall/uncertainty.py:ratio_variance`). Inside (0, 1), the Korn–Graubard effective number R(1−R)/v · (t_{n−1}/t_df)², n the labelled relevant papers, df = n minus the bands holding one, capped at their weights' Kish size; at 0 or 1, or v = 0, the Kish size. Clopper–Pearson bounds on it (`wider/recall/uncertainty.py:beta_interval`).
- No interval when a sampled band shows no relevant paper, or, inside (0, 1), has one but one labelled record, or df < 1.
- All bands a census (weights 1): recall is exact, no interval ("exact (census)").
- Assumes correct labels and treats each band's draw as simple random, though the first draw is systematic. Evidence: `scripts/recall_interval_sim.py`.
- Report cross-checks: seeded stratified bootstrap, logit Wald. Where the interval is missing, a Jeffreys model range is a labelled supplement (`wider/recall/uncertainty.py:model_interval`).
- Korn and Graubard (1998), Confidence intervals for proportions with small expected number of positive counts estimated from survey data; Kish (1965), Survey Sampling.
