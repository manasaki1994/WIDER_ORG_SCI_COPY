---
id: provenance-and-reporting
title: The record of the review
context: Throughout the review WIDER keeps a signed history of every manual change, and at the end it builds the report, the PRISMA documents and a replication package from that record and the stored results.
settings: []
artifacts: []
pages: [results, history]
---

## In short

Every change you make by hand, such as a label, a setting, a duplicate decision or a freeze, is saved in the History with your name, the time and what it changed. You can undo a change as long as nothing later depends on it. Automated runs keep their own record of inputs, settings and the model used. At the end, the Results page builds a report with every number, a PRISMA 2020 flow diagram and checklist, a PRISMA-S search report that names any sampled export's N, a draft methods paragraph for you to rewrite, reference files, and a replication package. All numbers come from the stored record; none are typed by hand.

## Why

A review is as trustworthy as its trail. Readers need to see who decided what and when, and which run produced each number. So WIDER records the before and after of each manual change and signs it with the researcher's name.

Undo restores the before only when the touched rows or files still look exactly like the after; otherwise it refuses and names what changed. Some steps are final, as undoing them would hide that validation data was seen: a freeze, a look at the hidden validation set, a recall seen on a validation sample, an unlock. Screening rows a merge set aside also stay; to split the pair, decide Not duplicates and deduplicate again. Reopening the search after validation papers were shown asks whether they still count; your answer is final and stated in every output. Undo never deletes a set in use or reuses its number.

Automated runs such as deduplication and screening are not journalled; each writes a run specification (input hashes, settings, software version, model) so it can be reproduced.

The report fills only what WIDER knows. The flow diagram stops at reports sought for retrieval, as full-text screening happens outside WIDER; checklist items WIDER does not record say so. No model writes report text; the methods paragraph is assembled from stored facts. The share bundle and the replication package leave out abstracts, database exports and raw model responses, and are scanned for key values and hidden names before they are written.

## For auditors

The journal is a before/after log. `wider/core/journal.py:recording` watches the rows and project files a change touches through `wider/core/journal.py:Recorder` and writes one `history` row with author, kind, summary and items; a change that touches nothing is not stored. `wider/core/journal.py:undo` restores in one transaction after a check that the current state equals the stored after; final kinds are refused; `wider/core/journal.py:_check` also refuses a set in use (`wider/core/journal.py:_set_in_use`) or a later-built change (`wider/core/journal.py:_blocker`). The author comes from `wider/core/journal.py:current_author`: the page's name, an explicit option, or the `WIDER_REVIEWER` or `USER` variable, recorded as inferred. A name is a claim, not an authentication.

Runs write `wider/core/provenance.py:RunSpec`. `wider/recall/report.py:build_report` assembles the report from the store, including the protocol hash, changed settings and `wider/recall/report.py:interventions`, which counts per-record kinds and lists the rest. `wider/recall/outputs.py:prisma_counts` feeds the flow diagram; records imported since the last deduplication run are counted as not yet checked for duplicates, and the counts are marked provisional until deduplication runs again; `wider/recall/prisma_2020.py:build` fills seven items, following Page et al. (2021), The PRISMA 2020 statement: an updated guideline for reporting systematic reviews; `wider/recall/prisma_s.py:build` follows Rethlefsen et al. (2021), PRISMA-S: an extension to the PRISMA Statement for Reporting Literature Searches in Systematic Reviews. `wider/recall/outputs.py:write_package` adds a SHA-256 manifest.
