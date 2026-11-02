---
id: query-construction
title: Building and freezing the search
context: How your question and seed papers become the search strings you run in the databases, how the strings are checked against the seed papers, and why the search is frozen before it is tested.
settings: [identification.development_share, identification.validation_after_freeze, identification.record_budget]
artifacts: [cells, codebook, synonyms, anchors, near_miss, adjacent]
pages: [construct, query]
---

## In short

Building the search takes five steps.

- Seed papers: you add papers you know belong in the review and split them into a development and a validation set.
- Synonyms: a language model proposes the concepts and each field's words for them from your question and the development papers; you edit and approve each.
- Search strings: WIDER writes one string per database, in its syntax.
- Dry run: on this computer, WIDER checks which development papers each string finds. The optional Count the query counts on OpenAlex; your databases are never queried.
- Freeze: you freeze the search; WIDER then shows how it does on the validation papers. You run the strings in each database and upload the exports.

## Why

- WIDER is made for topics that several fields study under different names; seed papers from each field, and names mined from their texts, show each field's words.
- Recall comes first, but not at any cost: the record budget ({default:identification.record_budget}) is how many records your team can screen. Above it, WIDER lists first the edits that save records without losing a development paper.
- The validation papers are an exam the search has not seen. The model never reads them and WIDER reports them only after the freeze. Freezing is not a final lock: you can unlock, edit and freeze again. Whenever you look early or reopen the search after validation papers were shown, WIDER asks whether they now count as seen or stay as validation. Your answer is final, and the report states it and whether the search was tested on unseen papers. A seed paper taken from the search's own results is kept but never counted: it would test the search with itself.
- WIDER never runs a search; you run it yourself. When the dry run cannot copy a database's behaviour, the answer is "uncertain", never "found".
- A random sample of N results counts each of its k records as N/k: numbers read "about", name the sample, and recall is not reported.
- Only the synonyms become the search. The other building blocks serve later steps: anchor sentences for ranking, the codebook for screening, near misses and adjacent literatures for the development sample.

## For auditors

Versions are draft, approved or frozen; the frozen search is the latest frozen synonyms version unless an unlock followed it.

- Split: `wider/construct/seed.py:suggest_split`, seeded random, {default:identification.development_share} to development. The vision's split that spreads research streams over both sets is not built.
- Proposals see development papers only: `wider/construct/propose.py:build_prompt`, `wider/construct/propose.py:benchmark_context`.
- A term of several words the model left unquoted is quoted, and a proximity group it wrote as text is read as one, before you see the proposal; each fix is listed with it: `wider/construct/propose.py:propose_async`.
- Names mined from the development papers' texts and keywords by rules, then by the model; passages go to a cloud model only if `seed_pdfs.passages_to_cloud` is on and you consent: `wider/seeds/mine.py:mine`. A mined name is added through `wider/seeds/names.py:admit`, which needs a missed development paper it finds or your note.
- Evidence-triggered questions: `wider/interview/bank.py:trigger`.
- Strings and what a database cannot express: `wider/search/render/__init__.py:render_all`, `wider/search/translation.py:ledger`.
- Dry run (hit, miss, uncertain): `wider/search/evaluate.py:evaluate`; OpenAlex: `wider/search/evaluate.py:load_profile`; edits: `wider/search/diagnostics.py:suggested_edits`, `wider/search/diagnostics.py:budget`.
- Sampled exports: `wider/ingest/importer.py:set_sample`, `wider/search/diagnostics.py:sample_weights`.
- A database lacking a paper versus a search missing it: `wider/search/indexing.py:known_item_strings`.
- Freeze, unlock, validation shown: `wider/construct/artifacts.py:set_status`, `wider/construct/artifacts.py:unlock`, `wider/search/coverage.py:validation_unlocked`, `wider/search/coverage.py:look`.

Assumption: the dry run sees only title, abstract and keywords, as databases index them.
