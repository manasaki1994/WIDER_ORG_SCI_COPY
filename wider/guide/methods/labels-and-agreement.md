---
id: labels-and-agreement
title: Human labels and agreement
context: After the samples are drawn, people label each sample record include, maybe or exclude; these labels are the reference the model is scored against and recall is estimated from.
settings: [sampling.development_reviewers, identification.jw_confirm]
artifacts: [codebook]
pages: [sample, review]
---

## In short

You, and a second reviewer if you have one, label each sample record include, maybe or exclude from its title and abstract. Where two reviewers disagree, an adjudicator decides and is named. A record's final label is the adjudicated one, else the primary reviewer's label. The screening development sample counts as complete when each record has agreeing labels from {default:sampling.development_reviewers} reviewer(s), or an adjudication. You lock a sample when it is final; a change after that is a correction and needs a reason. WIDER shows how far two reviewers agree beyond chance.

## Why

The labels are the yardstick for everything after them: the model's misses on the screening development sample and the recall estimate on the screening validation sample both assume the labels are right. Two independent readers catch slips one reader makes, so WIDER shows agreement, flags open disagreements and asks for a named adjudicator rather than picking a label silently. One reviewer is allowed; WIDER records it and does not refuse it.

Locking fixes a sample's labels before results are seen. You can undo a lock until a recall result has been shown; an unlock after that needs your confirmation and is written into the methods draft. Corrections keep the old value and your reason, so the history shows what changed.

When the criteria change, WIDER marks labels made under the old criteria as due for a re-check rather than discarding them, and each re-check is stored as a revision.

WIDER does not compute weighted kappa or statistics for more than two reviewers at once. Include, maybe and exclude are ordered, so weighted kappa would be defensible, but its weights are a choice: how much nearer maybe is to include than to exclude. Unweighted kappa needs no such choice and counts every disagreement in full. Agreement is reported for pairs.

## For auditors

Method: Cohen's kappa, unweighted, on the three label categories, between two named reviewers on the records both labelled; observed agreement against chance agreement from each reviewer's marginal shares.

- Between reviewers: `wider/sample/labels.py:agreement`, rounded to three decimals; labels left out by a review package's exclude choice are counted, not used.
- Between model passes: `wider/screen/agreement.py:kappa`.
- Adjudication, final labels and completeness: `wider/sample/labels.py:adjudicate`, `wider/sample/labels.py:final_labels`, `wider/sample/labels.py:completeness`.
- Lock, unlock and references sealed at a recall look: `wider/sample/labels.py:lock`, `wider/sample/labels.py:unlock`, `wider/sample/labels.py:seal_look`. Later labels that disagree with a sealed reference are counted on every recall result (`wider/sample/labels.py:sealed_disagreements`).
- Criteria changes: `wider/sample/labels.py:criteria_change`, `wider/sample/labels.py:due_labels`.
- Labels imported from a file: `wider/sample/labels.py:import_csv` and `wider/sample/labels.py:import_sample_csv` join rows to the corpus by record id or DOI, else by a title match at {default:identification.jw_confirm} or above; rows that match nothing are counted as unmatched.

Assumptions and limits: kappa depends on prevalence, so with few relevant records it can be low although raw agreement is high. When chance agreement is complete, the reviewer function returns one while the pass function returns no value with a reason. Kappa assumes both reviewers labelled the same records independently.
