---
id: blinding
title: Blinding
context: Blinding governs what a reviewer sees while labelling sample records, so that human labels are not shaped by the model's reads, the sampling design or another reviewer's decisions.
settings: [sampling.development_reviewers]
artifacts: []
pages: [sample, review]
---

## In short

While you label, the queue shows only the title and abstract. It hides the model's decision, the score band, the rank, the pool and which sample a record came from, and shows records in a shuffled order. A second reviewer gets a queue that mixes the first reviewer's includes and maybes with an equal number of excludes, so the mix does not give away the first decisions. You can also send a review package, a standalone page, to a reviewer outside WIDER. WIDER records where each label was made, so it can tell independent labels from ones made with model output in view.

## Why

A label made after seeing the model's answer, or knowing that a record sits in a high-score band, leans towards that answer; recall measured against such labels looks better than it is. WIDER therefore hides model reads on the screening development sample until it is fully labelled, and the development gate refuses runs that would show them earlier.

Blinding here is practical, not perfect. The second-reviewer and re-check queues hold screening validation records only and say so on the page. The History page still lists earlier labels, though while Blind is ticked it says "in a sample" instead of naming the sample a labelled, corrected or adjudicated record is in. The Screening review page shows model reads on an unlocked validation sample. While Blind is ticked, the Samples page gives how far each other reviewer has labelled, not how they labelled. Rather than forbid these, WIDER records how each label was made. A label from the blind queue, or from a review package on a record that is not a repeat, counts as independent; one from an unblinded queue never does; any other counts only when you declare it independent and state why. The screening validation sample needs one independent label per record.

A review package carries no model output and states that it does not prove who labelled. WIDER does not check a reviewer's identity.

## For auditors

Method: single-blind labelling by interface design. The queue shows a whitelist of opaque item token, title and abstract, in an order shuffled with the project seed.

- Blind queue: `wider/sample/labels.py:queue`, `wider/sample/labels.py:_items`, `wider/sample/labels.py:queue_left_out` (locked or sealed records are left out of a mixed queue, since a correction reason would reveal how they were drawn).
- Second-reviewer queue: `wider/sample/labels.py:second_reviewer_queue`, `wider/sample/labels.py:_second_pairs`.
- Independence: `wider/sample/labels.py:_independent`, `wider/sample/labels.py:declare_independent`.
- Review package: `wider/sample/package.py:make`, `wider/sample/package.py:import_file`.
- Hidden model reads: `wider/screen/devgate.py:hidden_ids`, `wider/screen/devgate.py:check`, `wider/screen/review.py:_hidden`.
- History without row items, and without sample names while blind: `wider/core/journal.py:changes`.

Limits: the blind holds inside the queue only. History hides sample names only while Blind is ticked, and entries for a whole sample (drawn, locked, extended) still name it. A reviewer who labels both samples may recognise records.
