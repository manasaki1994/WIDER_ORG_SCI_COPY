---
id: development-validation-split
title: Development and validation seed papers
context: When you add seed papers before building the search, WIDER splits them into a development set that shapes the search and a validation set that tests the frozen search.
settings: [identification.development_share, identification.validation_after_freeze, identification.jw_benchmark]
artifacts: []
pages: [construct, query]
---

## In short

Seed papers are relevant papers you already know, for example from an earlier review or a colleague. WIDER splits them into two sets for the search. The development set of seed papers is used to build and tune the search strings. The validation set of seed papers is kept hidden until you freeze the search or mark your uploads final, and then shows how many of them the search found. These two sets are not the screening development and validation samples, which are drawn later from the records. You can accept the suggested split, move papers, or add more papers later.

## Why

A search tuned on a paper will find that paper, so finding it says little. Papers kept out of tuning give a fairer test. This is why the model and the suggested edits use only the development set.

The suggested split is random, from the project seed, with {default:identification.development_share} of the papers in development, at least one in each set. It does not try to balance topics; you can move papers by hand.

By default the validation set is hidden before the freeze ({default:identification.validation_after_freeze}). You may look earlier, or after reopening the search; WIDER asks whether the papers shown count as seen or stay as validation, and records your answer. A paper counted as seen is marked used and no longer counts; one kept as validation still counts, and every output says it was shown. Moving a seen validation paper to development is allowed after you confirm, and is recorded as final.

A paper that you found through the query's own results stays in the validation set but is not counted, since the query found it by construction. WIDER warns and records; it does not refuse.

Known papers are easier to find than unknown ones, so recall on seed papers overstates recall on the wider literature.

## For auditors

Split: `wider/construct/seed.py:suggest_split` shuffles with the project seed and puts the rounded share in development, clamped so each set keeps a paper when there are two or more; `wider/construct/seed.py:apply_split` writes it as one change. Moves and notices: `wider/construct/seed.py:set_notices`, `wider/construct/seed.py:move_used`, `wider/construct/seed.py:set_assignment`.

Hiding and looks: `wider/search/coverage.py:validation_unlocked`, `wider/search/coverage.py:look`, `wider/search/coverage.py:used_papers`, `wider/search/coverage.py:validity_note`.

Matching seeds to records: `wider/core/matching.py:locate_benchmark` tries DOI, then exact normalised title, then the best Jaro–Winkler title match at {default:identification.jw_benchmark} or above, which needs your confirmation. The same threshold accepts a title search when resolving a seed paper (`wider/construct/seed.py:resolve`). Coverage per set: `wider/search/coverage.py:check`, with an exact Clopper–Pearson interval from `wider/search/diagnostics.py:clopper_pearson`.

Assumptions: the validation papers are like the relevant papers you do not know yet, and were chosen without the query. Both fail for papers from the same authors or the same stream; few seed papers give a wide interval.
