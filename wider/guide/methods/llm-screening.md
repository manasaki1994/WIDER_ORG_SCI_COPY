---
id: llm-screening
title: Screening with language models
context: After the screening development sample is labelled, a language model screens the title and abstract of every record, and you decide the records it leaves open on the Screening review page.
settings: [screening.passes, screening.majority, screening.blind_third_read, screening.second_screen, screening.recall_first, screening.quote_min_chars, corpus.thin_words]
artifacts: [codebook, prompts, cells]
pages: [screen, review]
---

## In short

A language model reads the title and abstract of each record and suggests include, maybe or exclude against your codebook. It reads every record on its own, {default:screening.passes} times by default, and a third blind read settles records where those reads disagree. Anything the model cannot settle becomes maybe and comes to you on the Screening review page, where your decision always stands. You improve the prompt only on the screening development sample, records picked on purpose. The screening validation sample, which estimates recall, is never used to tune the prompt or to choose a model. Every answer is stored, so you can replay a run without new calls.

## Why

A model can take the first read of thousands of abstracts, but it makes errors, and a missed relevant paper costs more than an extra paper a person reads. WIDER therefore leans towards recall. The model quotes the words behind every grade, and an absent grade rests on its quote: a quote that cannot be found word for word turns it into unclear. An exclusion needs grounds and a reason, and any sign that the record might fit turns it into maybe. Very short records are flagged, not excluded.

Two reads, a third only where they disagree, and a majority vote keep the cost of extra calls low while catching unstable answers. By default all passes use the same backend with different seeds; you can give the second and third reads other models, and WIDER names a shared model in the record rather than refusing it.

The configuration comparison runs backends, prompt versions and temperatures on the screening development sample and shows ranges over repeats. It names no winner, because those figures describe a purposive sample, not the corpus. The development gate refuses any run, evaluation or comparison that includes a development record until that sample is fully labelled, and hides model reads on it until then. WIDER does not freeze the prompt before the corpus run; a rerun becomes a new result set and the earlier one is kept. An adjudicator's imported decisions on some records make a new set where only those change, signed with the adjudicator's name.

## For auditors

Method: one isolated zero-shot call per record with a strict JSON answer, a recall-first override (rule v3), and a majority of {default:screening.majority} out of three reads.

- Prompt and schema from the codebook: `wider/screen/codebook.py:build`; calls, stored raw answers and reuse: `wider/screen/engine.py:screen_async`, `wider/screen/engine.py:replay`, `wider/screen/engine.py:eval_key`.
- Override: `wider/screen/rule.py:apply_rule`; quote check, fragments of at least {default:screening.quote_min_chars} characters: `wider/core/matching.py:quote_verified`.
- Passes, third read and vote: `wider/screen/protocol.py:run_protocol_async`, `wider/screen/protocol.py:majority`; second screen of maybes: `wider/screen/protocol.py:second_screen_async`.
- Pass agreement and routes: `wider/screen/agreement.py:pass_agreement`, `wider/screen/agreement.py:route_of`.
- Scoring and comparison on the development sample: `wider/screen/evaluate.py:score`, `wider/screen/compare.py:check_sample`; gate: `wider/screen/devgate.py:check`.
- Review and quality-control draw: `wider/screen/review.py:decide`, `wider/screen/review.py:qc_draw`.
- Adjudicator's decisions for some records: `wider/screen/adjudication.py:import_adjudication`.

Assumptions: reads by one backend do not err independently, so agreement between passes overstates reliability. A verified quote shows the words exist, not that the judgment is right. At temperature zero, repeats test only whether the backend answers the same twice.
