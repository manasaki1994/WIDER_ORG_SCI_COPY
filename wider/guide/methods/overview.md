---
id: overview
title: The WIDER procedure
context: The whole review in WIDER, from the question and seed papers to the search, cleaning, ranking and labelling, screening and the recall report, and how the app picks the next step.
settings: []
artifacts: []
pages: [overview, setup]
---

## In short

WIDER walks you through a systematic review in four parts. First the literature search: you state the question, add seed papers you already know, build the concepts and synonyms, and freeze the search strings. You run the searches in the databases yourself and upload the exports. Then data cleaning: WIDER finds duplicate records and fills missing abstracts. Then model tuning and validation: WIDER ranks the records by how close each is to your concepts, and you label samples drawn from that ranking, a development sample to tune the screening and a validation sample to check it. Last, a language model screens the records and WIDER estimates how many relevant records were found. The overview names the next step and why.

## Why

A review that spans several literatures fails in two places: the search misses papers that use other words, and screening thousands of records by hand is slow. WIDER splits the work so that each step leaves a record another person can check: the frozen search strings, the merge decisions, the samples, the labels and the screening decisions.

The steps have an order because each one uses what an earlier one made. A step is done when its result exists, for example the synonyms are frozen or every record has been ranked. A done step can still hold work for you, such as duplicate pairs to decide; the overview names it. Results from the built-in test model are marked as such, never as finished. The app reads these conditions from the project and does not keep a separate checklist, so the overview cannot drift from the data.

WIDER suggests a next step but does not lock the others. You can go back, unlock the search or change a sample; the change is recorded. This follows the rule that the app informs and records rather than refuses a method choice.

WIDER does not run database searches, does not decide inclusion without your labels, and does not send records to an outside model unless you have set one up and agreed to it.

## For auditors

The stages and their order live in `wider/stages.yaml`, in four sections: literature search (setup, construct, query, import), data cleaning (dedup, enrich), model tuning and validation (rank, sample: the samples that tune and later validate the screening) and screening and outputs (screen, recall). Each stage lists what it requires and a done condition read from the project (a table with rows, an artifact status, a stored value, a setting).

`wider/core/stages.py:stage_states` evaluates every condition and gives each stage a state. `wider/core/stages.py:next_stage` returns the first stage that is ready and not done, with three exceptions: the model page comes first while no model is set and nothing from the search results on is done; search strings are skipped when records were imported without a frozen search; and the sample step comes first while the development sample is not fully labelled. A done deduplication, screening or recall stage with open work stays next; test-model results are flagged. `wider/core/stages.py:route_sentence` turns this into the sentence the overview shows.

Assumption: a done condition means the step is finished. It checks that a result exists, not that it is good; the quality checks are in the later topics.

- `wider/stages.yaml`
- `wider/core/stages.py:stage_states`, `wider/core/stages.py:next_stage`, `wider/core/stages.py:route_sentence`
