---
id: development-sample
title: The screening development sample
context: After ranking, you label the screening development sample first; it is where the screening prompt is tuned on hard cases before the model screens the corpus.
settings: [sampling.pool_size, sampling.per_cell, sampling.per_near_miss, sampling.near_miss_pool, sampling.near_miss_max_rank, sampling.per_adjacent, sampling.per_cluster, sampling.per_boundary, sampling.n_thin, sampling.n_tail]
artifacts: [anchors, near_miss, adjacent]
pages: [sample]
---

## In short

The screening development sample is a set of records picked on purpose for you to label first, to tune the screening prompt on hard cases. It is not the development set of seed papers from the search step, and it is not used to estimate recall. WIDER picks it from the ranking in pools: likely includes for each cell, records close to each near miss, records close to each approved adjacent literature, a few from each topic cluster, records with very short abstracts, clearly irrelevant records from far down, and boundary cases. You label them in a shuffled queue, and you can add boundary cases later.

## Why

A random sample of the ranking would be mostly irrelevant records and would rarely show the hard cases. Tuning a prompt needs the records where a wrong decision is likely: near misses, neighbouring literatures, records that sit between two cells, short abstracts. A few clearly irrelevant records check that the prompt does not include everything.

Because the records are chosen on purpose, this sample says nothing about recall across the corpus. That is the job of the probability sample, the screening validation sample. To keep both checks independent, the pools never take seed papers or any record of a probability sample. On a small corpus that can leave no record at all: WIDER then says the development sample is empty, screening goes on without it, and fewer validation records per band or drawing the development sample first would give one.

The draw uses the project seed, so the same inputs give the same sample. The queue is shuffled, so the order does not tell you which pool a record came from.

Boundary cases can be added to a sample that is not locked. If screening has already run, WIDER asks you to confirm, and the model's output on the development sample stays hidden until the new records are labelled, so you do not see the model's answer before your own.

WIDER does not ask a model to pick the hard cases and does not pick records from your earlier labels; the pools follow fixed rules.

## For auditors

Purposive (judgement) sample, seeded from the project seed. Pools are drawn in this order; no pool takes a seed paper, a record of any probability sample, a record already drawn or, except the thin pool, a thin record:

- likely include: per cell, from the top {default:sampling.pool_size} by that cell's score, records whose best cell it is first; {default:sampling.per_cell} each.
- near miss: per entry, the {default:sampling.near_miss_pool} most similar records within rank {default:sampling.near_miss_max_rank}; {default:sampling.per_near_miss} at random.
- adjacent: the {default:sampling.per_adjacent} records nearest to each approved literature within the same rank.
- topical coverage: per cluster, its highest-ranked member and random others, {default:sampling.per_cluster} in all.
- thin {default:sampling.n_thin} and clearly irrelevant {default:sampling.n_tail}, at random, the latter beyond the clustered top.
- boundary, last: per near-miss entry and approved literature, the smaller of two rank percentiles (best cell score, similarity to the entry); {default:sampling.per_boundary} at random from the top {default:sampling.near_miss_pool}.

Assumption: the anchors and near-miss texts describe the hard cases.

- `wider/sample/design.py:draw_development`, `wider/sample/design.py:draw_boundary`, `wider/sample/design.py:extend_development`
