---
id: deduplication
title: Finding duplicate records
context: After you upload the database exports, WIDER finds records that describe the same paper, merges the sure ones and asks you about the unsure ones before ranking.
settings: [dedup.jw_candidate, dedup.jw_auto_merge, dedup.jw_corroborated, dedup.jw_strong_title, dedup.doi_conflict_title, dedup.generic_title_max_words]
artifacts: []
pages: [dedup]
---

## In short

The same paper often appears in several database exports, sometimes with small differences in the title, authors or page numbers. WIDER compares the records and merges those that clearly describe the same paper, for example the same DOI or the same title with the same year, volume and pages. Pairs that look alike but could be different papers go to a queue, where you decide duplicates (keeping record A or record B), not duplicates or unsure. Each merged group becomes one record with the most complete details, or the citation of the record you chose to keep. Every merge and every decision is listed with its reason, and you can export the list.

## Why

Duplicates inflate the record counts, waste screening time and can count one paper twice in recall. Merging two different papers is worse: a relevant paper can vanish. WIDER therefore merges only on strong evidence, keeps a list of conflicts that block a merge, and leaves the rest to you.

Similar titles alone are not enough. Short generic titles, such as an editorial heading, match many papers, and a conference paper and its journal version share a title but are different records. WIDER needs the title plus agreeing citation details, and refuses a merge when DOIs, publication types or details disagree.

Your decisions apply to the whole group a record belongs to and are applied again on every new run, so an import of new exports keeps them. If two records that were both screened turn out to be duplicates, the kept record's screening stays and the other's is set aside: kept on record, not counted, and named in History.

WIDER does not merge on abstracts and does not use a language model here; the rules are fixed and named, so each merge can be checked.

## For auditors

Rule-based record linkage in three passes, then union-find with conflict sets:

- A: exact DOI; a pair with title similarity under {default:dedup.doi_conflict_title} or years more than two apart goes to the queue instead.
- B: exact normalised title, merged only when an automatic rule fires.
- C: blocking on first author and year, title prefix, or a rare title word; pairs at title similarity {default:dedup.jw_candidate} or above are scored.

Title similarity is the larger of Jaro–Winkler and token cosine. Automatic merge rules include a full citation match at {default:dedup.jw_auto_merge} and two matching locators with the same year at {default:dedup.jw_corroborated}; titles of at most {default:dedup.generic_title_max_words} words count as generic. Not-duplicate rules include different DOIs and first authors despite similarity {default:dedup.jw_strong_title}. Unions never join groups holding a conflict. Rule names follow the original review's R script. A merged record takes its citation fields from the record a person chose to keep (the latest such decision in the group), otherwise from the most complete record.

Assumption: export metadata is accurate enough; errors in DOIs or years can block or force a merge.

- `wider/dedup/diagnostics.py`
- `wider/dedup/engine.py:stage_a`, `wider/dedup/engine.py:stage_b`, `wider/dedup/engine.py:stage_c`, `wider/dedup/engine.py:SafeUnion`, `wider/dedup/engine.py:run`, `wider/dedup/engine.py:build_masters`
