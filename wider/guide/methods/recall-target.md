---
id: recall-target
title: The recall target and when more labels are needed
context: Before any recall result is seen you set a recall target; at each look at the validation sample WIDER judges it as met, not met or inconclusive, and suggests how many more labels may settle it.
settings: [recall.target, recall.low_recall_rule, recall.top_strata_depth, recall.precision, recall.ci_level, recall.practical_labels, sampling.plan_power]
artifacts: []
pages: [recall, sample]
---

## In short

Before you see any recall result, you set a recall target (0.95, or 95 as a percentage) and say what you will do if it is not met. Each time WIDER estimates recall on the validation sample, it records a look and compares the target with exact bounds. The answer is met, not met or inconclusive; inconclusive is not evidence that the target was missed. You may label more and look again. WIDER suggests how many more labels per band may settle it, and warns above {default:recall.practical_labels}. More labels make the answer clearer, not the target more likely to be met, and you may always stop and report the result as inconclusive.

## Why

A target chosen after seeing the result can be fitted to it. WIDER does not refuse a late target; History times it, and outputs say when it was set after a look.

Repeating an ordinary interval after each added batch raises the chance of a wrong verdict. The bounds used here are confidence sequences: they are built to keep that chance at most one minus the level over all looks, whenever you stop and however many records you add, provided the records were drawn at random within each band. WIDER's first draw is systematic along the score order; a check found this can break the guarantee for some band orders, an open question.

The price is caution. The error is split over every band and screening decision, and each part is bounded on its own, so these bounds need more labels than a sharper method. When relevant papers are rare, settling the target may need most remaining records; WIDER says so, never requires it.

The verdict reads only these bounds, never the approximate interval or a model-based range. The suggestion's planning model predicts, never decides. Changing the screening configuration after a look asks you to choose: the sample is used (the estimate is marked not independent) or the change was not informed by the result. Revising the prompt as your rule uses up the sample; draw a fresh one.

## For auditors

- Per band and decision (kept, not kept), the relevant count among N records gets a hypergeometric confidence sequence: the prior-posterior ratio with a BetaBinomial(1/2, 1/2) prior, valid at every look by Ville's inequality (`wider/recall/anytime.py:cell_bounds`). Waudby-Smith and Ramdas (2020), Confidence sequences for sampling without replacement.
- 1 − level is split equally over all cells; recall bounds are the box over the ratio (`wider/recall/anytime.py:recall_bounds`). Met when the lower bound ≥ target, not met when the upper bound < target (`wider/recall/validation.py:judge`, `wider/recall/validation.py:look_block`). The guarantee is per result set and variant, each judged at the full level without correction; looks at several sets are reported as model selection, not corrected.
- Assumes random order within each cell and correct labels. Cell N is the stored N_h, as the weights (`wider/sample/plan.py:sample_cells`); the estimate can still fall outside the bounds.
- Suggestion: Jeffreys draws of cell rates, a greedy band path, simulated additions judged on the same bounds at chance `sampling.plan_power` (`wider/sample/plan.py:suggest`); the planner uses `wider/sample/plan.py:simulate`.
- OPEN: q-20261028-002105-a871, the systematic first draw breaks the any-look guarantee for some band orders; q-20261029-000215-c519, keep the shipped recall test or replace it.
