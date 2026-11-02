---
id: stratified-sampling
title: Stratified sampling for validation
context: After ranking, the screening validation sample is drawn band by band from the whole ranking; its labels are what the recall estimate of screening is computed from.
settings: [sampling.min_per_band, sampling.plan_sims, sampling.plan_power]
artifacts: []
pages: [sample]
---

## In short

To measure how many relevant records screening misses, you label a probability sample: records picked at random under rules fixed in advance, so each one stands for a known number of others. This is the screening validation sample, not the validation set of seed papers from the search step. WIDER cuts the ranking into bands, takes every record of the top band and a set number at random from each lower band, fewer where relevant records are rare. A planner suggests how many records each band needs for a clear answer on your recall target; you can apply its plan or keep your own.

## Why

Relevant records are dense at the top of the ranking and rare further down. Sampling the whole ranking at one rate would spend most labels where little is found; sampling only the top would say nothing about what the ranking missed. Bands let you label the top heavily and still reach the tail, and each record's weight, the band's size divided by the number drawn from it, makes the estimate cover the whole ranking.

Seed papers, the screening development sample and records with a very short abstract are not in the frame, so the estimate covers only the other ranked records; a relevant thin record missed by screening is not counted in it.

Within a band near the top, records are ordered by topic cluster before the draw, so the sample spreads across topics.

You can add records to a band later. The weights are then recomputed, and if you add after seeing a result, WIDER records that.

The planner compares allocation rules by simulation, at your expected rates and at half and double them, because these rates are guesses. You can choose any rule; WIDER records the rule used beside the one it recommended.

## For auditors

Stratified sampling with systematic selection within strata. A percentile band starts at the floor of the start percentile times the frame size, plus one; an empty band is dropped with a note. Within a band that ends inside the clustered top, records are sorted by cluster, then rank (implicit stratification); else by rank. Selection uses interval k equal to N_h/n_h with a uniform random start in [0, k); the weight is N_h/n_h. The analysis treats the draw as simple random within each band, which can fail if the order has a period. An extension draws a seeded simple random sample within bands from records outside every sample; weights become N'_h/n'_h. Records left out then are stored (`wider/sample/design.py:frame_excluded`); later samples' records stay in their band, unlabelled.

The planner allocates by the current, proportional, recall-optimal (n_h proportional to N_h times the square root of p_h), top-band-in-full or your rule, at least {default:sampling.min_per_band} per sampled band, rounded by largest remainder. It runs {default:sampling.plan_sims} simulated samples per candidate and picks the smallest total that settles the target in a share {default:sampling.plan_power} of them.

- `wider/sample/design.py:resolve_bands`, `wider/sample/design.py:sys_sample`, `wider/sample/design.py:draw_probability`, `wider/sample/design.py:extend_probability`
- `wider/sample/plan.py:allocate`, `wider/sample/plan.py:plan`, `wider/sample/plan.py:apply_plan`
