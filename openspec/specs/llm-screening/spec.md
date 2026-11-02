# llm-screening Specification

## Purpose
LLM screening reads every title and abstract with a language model, guided by a prompt and a strict JSON schema built from the approved codebook. A recall-first rule turns each structured answer into an include, exclude or maybe decision that can be checked against verbatim quotes. Repeated blind passes, a majority vote and a second screen of maybes produce the protocol decisions used for the recall estimate. In the WIDER procedure this is stage key `screen` (requires `sample`); it is done once `screen_decisions` has at least one row, and prompts are tuned only on the purposive development sample.

## Requirements

### Requirement: Prompt built from the codebook
The system SHALL build the screening prompt and its JSON schema from the codebook artifact (the approved version, else the newest, unless a version is named) and save both as a new version of the `prompts` artifact, recording SHA-256 hashes of the prompt and the schema.
Surfaces: CLI `wider screen build-prompt [--codebook VERSION] [--show]`; UI LLM screening page, "Build prompt from codebook".

#### Scenario: First prompt version
- **WHEN** the prompt is built from a codebook with two elements
- **THEN** the prompt names both elements and their quote fields
- **AND** saving it creates version 1 of the prompts artifact
Pinned by: `tests/unit/test_screen.py::test_build_prompt_and_schema`

#### Scenario: No codebook
- **WHEN** the prompt is built before any codebook artifact exists
- **THEN** it fails with "no codebook artifact; propose or write one first"

### Requirement: Strict answer schema
The system SHALL require every answer to contain a summary, a grade (`present`, `unclear` or `absent`) and an `<element>_quote` for each codebook element, each check as a boolean, `target_fit` (`core`, `adjacent` or `unrelated`), `edge_case`, `decision`, `exclusion_reason`, `missing_for_maybe` and one field per label group, with no other properties allowed.
Surfaces: CLI `wider screen build-prompt --show`; UI LLM screening page, "Build prompt from codebook".

#### Scenario: Required fields
- **WHEN** the schema is built from a codebook with elements e1 and e2, an include check and a maybe check
- **THEN** its required fields start with summary, e1 and e1_quote
- **AND** the include and maybe checks are listed separately for the decision rule
Pinned by: `tests/unit/test_screen.py::test_build_prompt_and_schema`

#### Scenario: Label groups
- **WHEN** the project has construct cells, project labels and approved adjacent literatures
- **THEN** the schema has a field for the cell, one per project label and `adjacent_literature` (the approved literatures plus `none`), each also allowing `not_applicable`

### Requirement: Quote verification
The system SHALL check every quote verbatim against the record's title and abstract (quotes of at least `protocol.screening.quote_min_chars` characters, default 8), and SHALL downgrade an `absent` grade without a verified quote to `unclear` with the flag `absent_unverified:<element>`.
Surfaces: inside `wider screen run`, `wider screen protocol` and `wider screen second`; UI LLM screening page, "Inspect a run" drawer highlights the quotes.

#### Scenario: Verified absent element
- **WHEN** the quote for an absent element is found verbatim in the abstract
- **THEN** the element counts as verifiably absent
Pinned by: `tests/unit/test_screen.py::test_rule_overrides`

### Requirement: Recall-first decision rule
The system SHALL apply the recall-first rule (`rule-v3`) to every answer unless `recall_first` is off: an exclude becomes maybe when it lacks grounds (neither `target_fit` unrelated nor a verified absent element), lacks a reason or has an include check true; an include becomes maybe when a maybe or absent check is true, on a maybe edge case, or on a flag in `protocol.screening.maybe_flags`.
Surfaces: inside `wider screen run`, `protocol` and `second`; UI LLM screening page, "Inspect a run".

#### Scenario: Exclusion without grounds
- **WHEN** the model excludes a record whose only absent element has a quote not found in the text
- **THEN** the final decision is maybe, with the flags `absent_unverified:e1` and `override:no_grounds`
Pinned by: `tests/unit/test_screen.py::test_rule_overrides`

#### Scenario: Grounded exclusion stands unless an include check fires
- **WHEN** the absent element's quote is found verbatim in the abstract
- **THEN** the exclusion stands
- **AND** if an include check is also true, the final decision is maybe with the flag `override:ruling`
Pinned by: `tests/unit/test_screen.py::test_rule_overrides`

#### Scenario: Thin abstracts are flagged only
- **WHEN** a record's abstract has fewer than `protocol.corpus.thin_words` words
- **THEN** the answer is flagged `thin` and the rule otherwise decides as usual
Pinned by: `tests/unit/test_screen.py::test_rule_overrides`

### Requirement: Resumable screening run
The system SHALL screen one pass over a scope (`sample:<name>`, `corpus`, `maybe:<protocol>` or `ids:1,2,3`) as a run, store every call in `screen_calls`, and on a rerun screen only records without a successful call, after confirmation when the backend changed. The `screen` stage provider is the default. Comparison runs write no CSV files.
Surfaces: CLI `wider screen run SCOPE [--pass A] [--provider] [--prompt] [--limit] [--backend-change]`, `wider screen runs`; UI LLM screening page, "Run".

#### Scenario: Rerun after completion
- **WHEN** a pass over the development sample completes and the same pass is started again
- **THEN** every record is reported as resumed and no new model call is made
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: Run folder and status
- **WHEN** a run is started
- **THEN** its folder holds `run_spec.json`, `prompt.md`, `schema.json`, `responses.jsonl`, `decisions.csv` and `errors.csv`
- **AND** the `runs` table shows its status as `running`, `interrupted`, `done` or `done_with_errors`
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: Comparison run folder
- **WHEN** a run of comparison `cmp1` finishes, one call having failed
- **THEN** its folder holds `run_spec.json`, `prompt.md`, `schema.json` and `responses.jsonl`, and no `decisions.csv` or `errors.csv`
- **AND** `wider screen replay RUN_ID` writes none and says "comparison run: no decisions.csv"
Pinned by: `tests/unit/test_screen_compare.py::test_comparison_run_folder`

#### Scenario: Screening in the background from the UI
- **WHEN** the researcher starts a screening run on the LLM screening page
- **THEN** it runs as a background job whose progress the page can poll
Pinned by: `tests/unit/test_runs.py::test_job_routes_screen_in_background`

#### Scenario: Unknown scope
- **WHEN** a run is started with a scope of another form
- **THEN** it fails with "unknown scope"

Pinned by: `tests/unit/test_screen.py::test_unknown_scope_fails`

### Requirement: Screening run ids
The system SHALL name a screening run `<kind>__<scope>__p<prompt version>__<provider>__<pass>`, a scope over 40 characters cut and followed by a short hash of the whole, and SHALL append `__<protocol>.s<n>` for result set n of a protocol and `__cmp.<name>.c<i>.r<k>` for repeat k of configuration i of a comparison, so no run is shared between protocols, sets or comparisons.
Surfaces: CLI `wider screen runs`; UI LLM screening page, runs table.

#### Scenario: Two protocols never share a run
- **WHEN** protocols `main` and `alt` screen `sample:dev` on the same backend, prompt and pass, one after the other
- **THEN** each has its own run ids (`…__main.s1`, `…__alt.s1`), and `alt` makes its own model calls rather than resuming `main`'s
- **AND** two `ids:` scopes longer than 40 characters that share their first 40 get different run ids
Pinned by: `tests/unit/test_result_sets.py::test_two_protocols_own_run_ids`, `tests/unit/test_result_sets.py::test_long_scopes_get_distinct_run_ids`

#### Scenario: Comparison runs get their own ids
- **WHEN** comparison `cmp1` runs configurations `a` and `b` twice each
- **THEN** its 4 run ids end in `__cmp.cmp1.c1.r1`, `__cmp.cmp1.c1.r2`, `__cmp.cmp1.c2.r1` and `__cmp.cmp1.c2.r2`, and none is shared with a protocol's run on the same backend, prompt and pass
Pinned by: `tests/unit/test_screen_compare.py::test_comparison_run_ids`

### Requirement: Failed calls become questions
The system SHALL turn every model call that fails or returns unparseable output into one open `screen_failed` question with the options include, maybe, exclude and retry. An answer other than retry is stored as the researcher's read of the record, and later runs do not send that record to the model again. Comparison runs raise none; failures are counted and retried.
Surfaces: inside `wider screen run`, `wider screen protocol` and `wider screen second`; answered through the project's questions.

#### Scenario: Unparseable output
- **WHEN** the model returns text that is not JSON for two records
- **THEN** the run reports 2 errors and two `screen_failed` questions are open, and a retry that fails again adds no duplicate
- **AND** after one is answered include and the other retry, the next run calls the model only for the retry record
Pinned by: `tests/unit/test_questions.py::test_failed_parse_becomes_a_question_and_the_answer_is_the_read`

#### Scenario: No question for a comparison run
- **WHEN** a comparison run's call for record 5 fails
- **THEN** no `screen_failed` question is opened, the comparison counts 1 failed call, and its next run calls the model for record 5 only
Pinned by: `tests/unit/test_screen_compare.py::test_failed_call_counted_not_asked`

### Requirement: Screening protocol with blind third read and majority
The system SHALL run a named protocol (default `main`) as passes A and B (only A when `passes` is 1), a blind pass C on their disagreements when `screening.blind_third_read` is on (default), and store in `screen_decisions` of the result set being screened the decision with at least `screening.majority` votes (default 2), with votes and reason.
Surfaces: CLI `wider screen protocol [SCOPE] [--name] [--new-set] [--set-label]`; UI LLM screening page, mode "protocol A/B + third read".

#### Scenario: Majority of three votes
- **WHEN** the votes are include, include and exclude
- **THEN** the decision is include
- **AND** votes of include, maybe and exclude give maybe, and two disagreeing votes without a third read give maybe
Pinned by: `tests/unit/test_screen.py::test_majority`

#### Scenario: Researcher settles a three-way split
- **WHEN** a record gets three different votes
- **THEN** its decision is maybe and a `screen_split` question is raised
- **AND** once the researcher answers exclude, the decision is exclude with the vote `researcher:exclude` and survives reruns of the protocol within the same result set
Pinned by: `tests/unit/test_questions.py::test_three_way_split_asks_and_the_answer_survives_reruns`

### Requirement: Second screen of maybes
The system SHALL re-read the maybes of a protocol's result set (default the active one) in two blind passes with a third read on disagreement, writing a result set of protocol `<protocol>_second` that records its source set and carries over its non-maybe decisions unchanged. A finished second set is never overwritten; a new one is screened only on request.
Surfaces: CLI `wider screen second [PROTOCOL] [--from-set N] [--new-set]`; UI LLM screening page, "Maybes from".

#### Scenario: Maybes are re-read
- **WHEN** protocol `main` has maybes and the second screen runs
- **THEN** protocol `main_second` holds a decision for every record of `main`
- **AND** the number of records re-read equals the number of maybes in `main`
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: A second rerun with another model needs a request
- **WHEN** `main_second` set 1 finished on `m1`, the model is edited to `m2`, and `wider screen second main` runs, then runs again with `--new-set`
- **THEN** the first makes no call and names `--new-set`; the second creates `main_second` set 2 with the same source set, and set 1 is unchanged
Pinned by: `tests/unit/test_result_sets.py::test_second_rerun_needs_request`

### Requirement: Development evaluation only
The system SHALL compare a run's decisions, reused evaluations included, with a purposive sample's final human labels, classing disagreements as MISS (human include or maybe, model exclude), FALSE INCLUDE or FALSE MAYBE, by pool and adjacent literature, and MUST refuse a probability sample or one with unlabelled records. Report: `eval_report.md` in the run folder.
Surfaces: CLI `wider screen evaluate RUN_ID [--sample development]`; UI Screening, "Development evaluation".

#### Scenario: Evaluating on the development sample
- **WHEN** the model agrees with the human labels on every development record
- **THEN** the evaluation reports 0 misses and 0 false includes over all records of the run
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: Validation sample refused
- **WHEN** evaluation is requested against the probability sample `validation`
- **THEN** it fails because evaluating there would tune the prompt on the recall set
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: A run with reused evaluations
- **WHEN** run 2 reused 6 of its 10 development records' evaluations from run 1 and called the backend for 4
- **THEN** `wider screen evaluate` of run 2 scores all 10 records, as it does run 1
Pinned by: `tests/unit/test_screen_compare.py::test_evaluate_counts_reused_evaluations`

#### Scenario: Labels still open
- **WHEN** 41 of the development sample's 120 records have a final label and `wider screen evaluate RUN_ID` runs
- **THEN** it exits 1 with "the development sample has 79 records without a final label; label them all first" and writes no report
Pinned by: `tests/unit/test_screen_dev_gate.py::test_refused_while_labels_open`

### Requirement: Offline replay of a run
The system SHALL rebuild a run's decisions offline from its stored `responses.jsonl` without calling the model, honouring the researcher's answers and retractions and keeping each call's recorded backend, model and snapshot hash, and MUST refuse a stored response whose input hash no longer matches the record text ("stored response was produced from different text").
Surfaces: CLI `wider screen replay RUN_ID`; no UI.

#### Scenario: Unchanged replay
- **WHEN** a finished run is replayed with the same prompt and rule
- **THEN** every record is replayed and 0 decisions change
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: Replay keeps per-call provenance
- **WHEN** a run whose first 2 calls used model `m1` and last 2 used `m2` after a confirmed change is replayed into a store whose `screen_calls` and `backend_snapshots` rows for it were deleted
- **THEN** the 4 rows come back with the same models and `backend_hash` values, and both snapshots are restored from the run folder
Pinned by: `tests/unit/test_screen_provenance.py::test_replay_keeps_call_provenance`

#### Scenario: A repaired call replays
- **WHEN** a call's first answer failed the schema, its repair round succeeded, and the run is replayed
- **THEN** the stored `input_sha256` is the hash of the record's original input, the record is replayed, and 0 decisions change
Pinned by: `tests/unit/test_providers.py::test_repaired_call_keeps_input_hash`, `tests/unit/test_screen.py::test_repaired_call_replays`

### Requirement: Importing decisions from an earlier screening
The system SHALL import include/maybe/exclude decisions from a CSV column into a protocol, matched by DOI, then title, extra non-empty columns kept as labels, unlisted records exclude by default; of rows matching one record the first counts, the rest are reported. Importing into a protocol with decisions MUST add a result set naming the file, sha256, time and author, and MUST NOT change an earlier decision.
Surfaces: CLI `wider screen import-decisions PROTOCOL FILE`; UI "Protocol decisions".

#### Scenario: Results file listing only includes and maybes
- **WHEN** a file with one include, one maybe and one unknown row is imported into a 10-record corpus with the default `--others-exclude`
- **THEN** the import reports 1 include, 1 maybe, 1 unmatched and 8 exclude_by_absence
- **AND** the protocol listing shows 10 decisions, 8 of them exclude
Pinned by: `tests/unit/test_recall.py::test_import_decisions_csv_with_absent_as_exclude`

#### Scenario: Import into a protocol with results
- **WHEN** protocol `main` holds decisions from a screening run and a file is imported into `main`, and the same file is then imported into `old` twice
- **THEN** `main` gains result set 2 with source import, the file name and its sha256, set 1 stays active and every decision of set 1 is unchanged
- **AND** `old` has sets 1 and 2 with the same sha256, and set 1 is unchanged
Pinned by: `tests/unit/test_screen_export.py::test_import_never_replaces_screening_results`

#### Scenario: Two rows for one record
- **WHEN** a file whose row 2 says include and row 3 says exclude for the same record, and whose rows 4 and 5 both say maybe for another record, is imported into a 10-record corpus
- **THEN** the import reports 1 include, 1 maybe, 2 duplicate and 8 exclude_by_absence, and lists row 3 (exclude) as a conflict with row 2 (include), which is kept
- **AND** the protocol listing shows 10 decisions: 1 include, 1 maybe and 8 exclude
- **AND** `wider screen import-decisions` says that 2 later rows matched a record already listed and, in red, that row 3 says exclude and row 2 says include and is kept
Pinned by: `tests/unit/test_recall.py::test_import_decisions_duplicate_rows_first_counts`

### Requirement: Single-pass screening protocol
With `protocol.screening.passes` set to 1 the system SHALL screen a protocol's records in pass A only and store each record's pass A rule decision as the protocol decision, with that one vote, running no pass B and no third read. With the default of 2 passes the protocol MUST run exactly as before.
Surfaces: CLI `wider screen protocol`, `wider protocol set screening.passes 1`; UI LLM screening page, mode "protocol A/B + third read".

#### Scenario: One pass
- **WHEN** `screening.passes` is 1 and protocol `main` runs over a 10-record corpus
- **THEN** only one run (pass A) is made and `main` holds 10 decisions, each equal to that run's rule decision
- **AND** no `screen_split` question is raised

#### Scenario: Default unchanged
- **WHEN** `screening.passes` is left at 2
- **THEN** passes A and B run and disagreements get a blind third read when `blind_third_read` is on

### Requirement: Recall-first rule can be switched off
With `protocol.screening.recall_first` off the system SHALL keep the model's own decision for every answer, with no `override:*` change and no downgrade of unverified `absent` grades; unverified quotes MUST still be flagged, a true `maybe_flags` field still makes an include maybe, and the run spec records that the rule was off.
Surfaces: CLI `wider protocol set screening.recall_first false`, then `wider screen run|protocol|second|replay`; UI Settings protocol card.

#### Scenario: Exclusion without grounds stands
- **WHEN** `recall_first` is false and the model excludes a record whose only absent element has a quote not found in the text
- **THEN** the final decision is exclude
- **AND** the flags include `absent_unverified:e1` but no `override:no_grounds`

#### Scenario: Default keeps the rule
- **WHEN** `recall_first` is left at true
- **THEN** the same answer becomes maybe with the flag `override:no_grounds`

### Requirement: Second screen follows the protocol switch
The system MUST refuse a second screen of maybes, calling no model and writing no protocol, while `protocol.screening.second_screen` is off, with a message naming that setting. With the default (on) the second screen runs as before.
Surfaces: CLI `wider screen second` (exit code 1); UI LLM screening page, mode "second screen of maybes" (the job fails with the same message).

#### Scenario: Second screen switched off
- **WHEN** `screening.second_screen` is false and `wider screen second main` is run
- **THEN** the command says the second screen is switched off in `protocol.screening.second_screen` and exits with code 1
- **AND** no `main_second` decisions and no new runs exist

### Requirement: Replay uses the run's recorded rule settings
The system SHALL record in every run spec the rule settings the run used (maybe edge cases, `maybe_flags`, `recall_first`, label groups, `quote_min_chars`, thin-abstract threshold) and SHALL replay a run with exactly those settings, so replaying an unchanged run changes no decision; a run recorded before this change replays with the current protocol's settings and says so.
Surfaces: CLI `wider screen replay RUN_ID`; no UI.

#### Scenario: Run with a maybe flag
- **WHEN** `screening.maybe_flags` is `["contamination"]` and a live run turned an include into maybe because `contamination` was true
- **THEN** replaying that run keeps the decision maybe with the flag `flag:contamination`
- **AND** the replay reports 0 decisions changed

#### Scenario: Later protocol edits do not change a replay
- **WHEN** a field is added to `screening.maybe_flags` after a run and the run is replayed
- **THEN** the replay uses the run's recorded flags and reports 0 decisions changed

#### Scenario: Old run without recorded settings
- **WHEN** a run whose spec has no recorded rule settings is replayed
- **THEN** the replay uses the current protocol's settings and prints that it did so

### Requirement: Screening refuses a bad record limit or pass name
The system SHALL refuse a screening run whose record limit is not a whole number from 1 to 1,000,000,000, whose pass name is not 1-20 letters, digits, '-' or '_', or whose protocol name is not 1-80 letters, digits, '-', '_' or '+', naming the problem, before any model call, run folder or job is made. An empty limit means every record.
Surfaces: CLI `wider screen run|protocol|second` (`--limit`, `--pass`, `--name`); UI LLM screening page, "Run" (`POST /api/jobs/screen`).

#### Scenario: Negative limit
- **WHEN** a screening run is started with limit -5 from the CLI, the API or the engine, or a protocol run with limit -2 from the CLI or the API, or a second screen with limit -1 from the CLI or the API
- **THEN** the CLI exits with code 1 and the route answers HTTP 400 with "the record limit must be at least 1"
- **AND** no model call is made, no run is stored and no job is started
Pinned by: `tests/unit/test_screen.py::test_screening_refuses_a_bad_limit_or_an_empty_pass_name`

#### Scenario: Record limit field on the page
- **WHEN** the record limit field holds 0, or is empty, or holds 2.5
- **THEN** the digits are sent as typed text, so 0 is refused by the server and a 23-digit number is quoted as typed; an empty field sends no limit (every record), and 2.5 shows a message with no request
Pinned by: `tests/unit/test_static_followup.py::test_screen_page_sends_the_record_limit_as_typed`, `tests/unit/test_ui_followup_r30.py::test_the_record_limit_field_is_run_on_the_page`

#### Scenario: A limit that is not a whole number through the API
- **WHEN** `POST /api/jobs/screen` or `POST /api/screen/run` is called with limit 2.5, "abc", "2.5", "3 records" or JSON true or false
- **THEN** it answers HTTP 400 with "the record limit must be a whole number" and "leave it empty to screen every record", and no job or model call is made
- **AND** a limit of "0" is refused as below 1, one above 1,000,000,000 as too large with its digits as sent, and a blank one screens every record
Pinned by: `tests/unit/test_ui_followup_r13.py::test_a_screening_limit_that_is_not_a_whole_number_is_refused_in_plain_words`

#### Scenario: Empty pass name
- **WHEN** a pass-mode run is started with an empty pass name
- **THEN** it is refused with "the pass needs a name, e.g. A" and no run is stored
Pinned by: `tests/unit/test_screen.py::test_screening_refuses_a_bad_limit_or_an_empty_pass_name`

#### Scenario: A pass name with a path in it
- **WHEN** a pass name such as `x/../../escaped`, `..` or `a b` is given from the CLI, the API or the engine
- **THEN** it is refused with "a pass name is 1-20 letters A-Z, digits" and no run folder is made outside `runs/screen/`
Pinned by: `tests/unit/test_screen.py::test_a_pass_name_never_leads_the_run_folder_out_of_the_project`

#### Scenario: A protocol name with markup or a path in it
- **WHEN** a protocol run is started with the name `<b>x</b>` or `x/../../esc` from the CLI or the page
- **THEN** the CLI exits with code 1 and the route answers HTTP 400 with "a protocol name is 1-80 letters", and no decision is stored
- **AND** names such as `main`, `main+human` and `B2_second` are accepted
Pinned by: `tests/unit/test_ui_followup_r17.py::test_a_protocol_name_must_be_a_plain_word`, `tests/unit/test_screen.py::test_a_plain_protocol_name_is_accepted_on_the_command_line_and_the_page`

#### Scenario: A limit on a finished run
- **WHEN** a pass has screened every record and is run again on the same scope with a record limit of 2
- **THEN** no model call is made and the result reads "Screened 2 of 2 records" with the warning "2 records were already done and skipped."
- **AND** the runs table still counts the whole run (every record done of every record), and the result shows no tokens, since none were used
- **AND** a protocol run over passes that are already finished makes no call and shows no tokens either
- **AND** a protocol run with a limit of 1 over finished passes decides every record those passes hold, since the limit caps new model calls only
Pinned by: `tests/unit/test_screen.py::test_a_limit_on_a_finished_run_counts_only_its_own_records`

#### Scenario: A limit too large and a long pass name on the command line
- **WHEN** `wider screen run --limit 99999999999` or `--pass` with a name of 21 or more letters is run
- **THEN** it exits 1 with "the record limit must be at most 1,000,000,000 (got 99999999999)" or "a pass name is 1-20 letters"
- **AND** no backend is asked anything
Pinned by: `tests/unit/test_ui_followup_r30.py::test_the_screen_command_refuses_a_limit_too_large_and_a_long_pass_name`

#### Scenario: A second screen of a protocol with a bad or long name
- **WHEN** a second screen is started for the protocol `<b>x</b>`, or for a protocol name of 74 letters, from the engine, `wider screen second` or the page (`POST /api/jobs/screen`, `POST /api/screen/run`)
- **THEN** it is refused (CLI exit 1, HTTP 400) quoting the name as typed, "(got '<b>x</b>')", not as `<b>x</b>_second`, or with "at most 73 characters here (got 74)", since the second screen is written as `<protocol>_second` within 80 letters
- **AND** no model call is made and no run is stored
- **AND** a protocol name of 73 letters is accepted and writes `<name>_second`
Pinned by: `tests/unit/test_ui_followup_r32.py::test_a_second_screen_quotes_a_bad_protocol_name_as_typed_and_says_why_a_long_one_is_refused`, `tests/unit/test_ui_followup_r32.py::test_a_protocol_of_73_letters_is_second_screened_from_the_engine_the_cli_and_both_page_routes`

### Requirement: Output token budget checked before screening
The system SHALL refuse a screening run whose `screening.max_tokens` is above 32,000 or above the backend's `extra.max_output_tokens` when one is set, naming both numbers and the command to lower it, and SHALL refuse an `extra.max_output_tokens` that is not a whole number of at least 1 when a backend is added or edited.
Surfaces: CLI `wider protocol set screening.max_tokens N`, `wider providers add|edit`; UI LLM screening page, backend form.

#### Scenario: Output token budget above the limits
- **WHEN** `wider protocol set screening.max_tokens 50000` runs, and a run starts on backend `b` with `extra.max_output_tokens` 4096 while `screening.max_tokens` is 8000, from the CLI, the API and the engine
- **THEN** the first exits with code 1 saying "screening.max_tokens must be at most 32000" and `project.yaml` is unchanged
- **AND** the run is refused (exit 1, HTTP 400) naming 8000, 4096 and backend `b`, with no model call, run folder or job
- **AND** a `project.yaml` written earlier with `screening.max_tokens: 50000` still opens, and its run is refused with "screening.max_tokens must be at most 32000"
Pinned by: `tests/unit/test_screen.py::test_max_tokens_ceiling`, `tests/unit/test_screen.py::test_max_tokens_above_backend_limit`

#### Scenario: A junk backend output limit is refused
- **WHEN** a backend is added with `extra.max_output_tokens` set to `abc`, `-5`, `0` or `2.5`
- **THEN** it is refused with "extra.max_output_tokens must be a whole number of at least 1" and nothing is saved
- **AND** the value 4096 is accepted
Pinned by: `tests/unit/test_backends.py::test_max_output_tokens_must_be_whole_number`

### Requirement: Protocol headline counts passes in words
The system SHALL write the protocol result headline with the number of records and passes in correct English ("1 record", "2 records", "1 pass", "2 passes"), and SHALL name the blind third read as such, never as a third pass.
Surfaces: CLI `wider screen protocol`; UI LLM screening page, stage result card.

#### Scenario: One pass
- **WHEN** a protocol runs with `screening.passes` set to 1
- **THEN** its result headline says "decided with 1 pass"
Pinned by: `tests/unit/test_screen.py::test_single_pass_protocol`

#### Scenario: One record
- **WHEN** a protocol decides one record, for example over the scope of one record id
- **THEN** its headline says "1 record decided with 2 passes", never "1 records"
Pinned by: `tests/unit/test_stage_result.py::test_a_one_record_protocol_headline_says_record`, `tests/unit/test_screen.py::test_a_protocol_over_one_record_says_record`

#### Scenario: A third read where the passes disagree
- **WHEN** a two-pass protocol sends the records its passes disagree on to the blind third read
- **THEN** its headline says "decided with 2 passes and a blind third read", never "3 passes"
Pinned by: `tests/unit/test_stage_result.py::test_a_one_record_protocol_headline_says_record`, `tests/unit/test_screen.py::test_a_protocol_whose_passes_disagree_names_the_blind_third_read`

### Requirement: Backend change during a run needs confirmation
The system SHALL check, before resuming an unfinished screening pass, whether its backend changed since its earlier calls, under the same name or another. Nothing is screened until the researcher chooses to continue the run or, for another name, start a separate run; continuing records the change. An operational-only change (price, timeout, CLI version) is logged without asking.
Surfaces: CLI `wider screen run|protocol|second --backend-change continue|new`; UI LLM screening page, change dialog.

#### Scenario: Model edited between two sessions
- **WHEN** a pass over 4 records on fake backend `a` with model `m1` is interrupted after 2 records, `a` is edited to model `m2`, and `wider screen run` is started again without `--backend-change`
- **THEN** it exits with code 1, names `a`, `m1` → `m2`, the changed field `model` and "2 of 4 records done", makes no model call and adds no `llm_usage` row
- **AND** `POST /api/jobs/screen` with the same body answers 409 with a `backend_change` entry and starts no job
Pinned by: `tests/unit/test_screen_provenance.py::test_resume_after_model_edit_needs_confirmation`, `tests/unit/test_runs.py::test_screen_job_backend_change_409`

#### Scenario: Continue records the change
- **WHEN** the same run is started with `--backend-change continue`
- **THEN** the last 2 records are screened with `m2` under the same run id, `run_backend_changes` holds one row from `m1` to `m2` with `records_done` 2 and `confirmed_via` `cli`
- **AND** the run folder holds `backend_changes.jsonl` and a `backend_<hash>.json` snapshot for each backend, and `wider screen runs` marks the run "mixed models"
Pinned by: `tests/unit/test_screen_provenance.py::test_continue_records_change_and_marks_mixed`

#### Scenario: Unfinished run under another backend name
- **WHEN** a pass on backend `a` is interrupted after 2 of 4 records and the same pass is started with `--provider b`
- **THEN** without a choice it exits with code 1 offering `continue` and `new`
- **AND** `new` starts a separate run under `b` that screens all 4 records and leaves the run under `a` unchanged, while `continue` screens the last 2 records under the run id of `a` and records the change
- **AND** `--backend-change new` after a same-name edit exits with code 1 saying to rename the backend or set it back
Pinned by: `tests/unit/test_screen_provenance.py::test_unfinished_run_under_other_backend_offers_choice`

#### Scenario: Finished pass and CLI version bump
- **WHEN** a finished pass is started again after its backend's model was edited, and an unfinished pass on a fake CLI backend is resumed after only the CLI's `--version` output changed
- **THEN** the first makes no call, asks nothing and prints "already done under model m1; run it in a protocol, or under another backend name, for a new run", exiting 0
- **AND** the second resumes without asking and records the change with `confirmed_via` `auto`
Pinned by: `tests/unit/test_screen_provenance.py::test_finished_run_never_asks`, `tests/unit/test_screen_provenance.py::test_cli_version_change_recorded_without_asking`

#### Scenario: Change dialog in the UI
- **WHEN** the researcher starts that interrupted pass from the LLM screening page after the edit
- **THEN** a dialog "The backend changed since this run started" lists `m1` → `m2`, "2 of 4 records done", "Continue this run on the new backend" and Cancel
- **AND** Cancel starts no job
- **AND** when the pass is started with backend `b` after a run on `a` was interrupted, the dialog also offers "Start a separate run", which starts a job with `backend_change` `new`
Pinned by: `tests/unit/test_screen_models.py::test_backend_change_dialog_text`

#### Scenario: Operational edit recorded without asking
- **WHEN** the pass over 4 records on fake backend `a` is interrupted after 2 records, `a`'s timeout and input price are edited, and `wider screen run` is started again
- **THEN** the remaining 2 records are screened without asking and the run is not marked mixed
- **AND** `run_backend_changes` holds one row naming `timeout_s` and `price_input_per_mtok` with `confirmed_via` `auto`
Pinned by: `tests/unit/test_screen_provenance.py::test_operational_edit_recorded_without_asking`

### Requirement: Per-call screening provenance
The system SHALL record with every screening call its backend name, configured model, the model the backend reported, and the hash of the backend snapshot, stored once per distinct snapshot. A successful evaluation MUST never be replaced; a failed call may be retried, and every attempt stays in the response log and the usage log. Calls from before this change show the run's model, marked "recorded per run".
Surfaces: engine; CLI `wider screen runs`; UI runs table.

#### Scenario: Each call carries its backend
- **WHEN** a pass over 2 records runs on fake backend `a` with model `m1` that reports the model `m1-2026`
- **THEN** both `screen_calls` rows hold provider `a`, model `m1`, `model_reported` `m1-2026` and the same `backend_hash`, and `backend_snapshots` holds that snapshot once, without key values
- **AND** both `responses.jsonl` lines carry the same four fields
Pinned by: `tests/unit/test_screen_provenance.py::test_calls_record_backend_per_call`

#### Scenario: A success is never replaced
- **WHEN** one record failed and one succeeded, and the pass is resumed
- **THEN** only the failed record is called again, the successful row keeps its `call_id`, model and time, and `responses.jsonl` holds 3 lines
Pinned by: `tests/unit/test_screen_provenance.py::test_successful_evaluation_never_replaced`

#### Scenario: Rerunning a protocol keeps its decisions
- **WHEN** protocol `main` finished on model `m1` and `wider screen protocol main` runs again unchanged, then the backend is edited to `m2` and it runs again
- **THEN** the first rerun makes no model call and leaves every final decision and its `vote_calls` unchanged
- **AND** the second makes no call and creates no set; with `--new-set` it creates result set 2 on `m2`, and set 1's decisions, `vote_calls` and runs are unchanged
Pinned by: `tests/unit/test_screen_provenance.py::test_rerun_keeps_final_decisions`, `tests/unit/test_result_sets.py::test_rerun_other_model_new_set`

#### Scenario: Older store
- **WHEN** a version-3 store with one screening call and one protocol decision is opened
- **THEN** the call's provenance columns are empty and its model is shown from the run, marked "recorded per run"
Pinned by: `tests/unit/test_screen_provenance.py::test_migrate_v4_screen_columns`, `tests/unit/test_screen_provenance.py::test_old_store_calls_fall_back_to_run_model`

### Requirement: Screening decisions export with models
The system SHALL export a protocol's result set as a decisions file (final decision, its passes and models, mixed if a pass used a model not set for it) and an evaluations file (per evaluation: pass, run, backend, model, reported model, snapshot hash, decision, time, tokens, cost, new or reused, source run, used for final). Neither holds abstract, prompt, answer or error text.
Surfaces: CLI `wider screen export PROTOCOL [--set N]`; UI LLM screening page, "Result set" select, "Export CSV".

#### Scenario: Three reads with two models
- **WHEN** protocol `main` reads record 1 with A = include and B = exclude on model `m1`, and C = include on model `m2`
- **THEN** `exports/screening/main_decisions.csv` gives record 1 final include, `decided_by` `A,C`, models `m1;m2` and `mixed_models` true
- **AND** `main_evaluations.csv` holds three rows for record 1, with `used_for_final` true for A and C and false for B, each naming its model
Pinned by: `tests/unit/test_screen_export.py::test_protocol_export_marks_final_evaluations`

#### Scenario: Researcher and split decisions
- **WHEN** three reads disagree completely, and for another record the researcher answered the split question with exclude
- **THEN** the first has `decided_by` `split` and no evaluation marked `used_for_final`, and the second has `decided_by` `researcher`
Pinned by: `tests/unit/test_screen_export.py::test_protocol_export_marks_final_evaluations`

#### Scenario: Run decisions and second screen
- **WHEN** a run writes `decisions.csv`, and a second screen carries a non-maybe decision over from `main`
- **THEN** `decisions.csv` has the columns `provider`, `model`, `model_reported` and `backend_hash`
- **AND** the carried decision keeps the evaluations it rested on, with `decided_by` `carried from main`
Pinned by: `tests/unit/test_screen_export.py::test_run_decisions_csv_has_model_columns`, `tests/unit/test_screen_export.py::test_second_screen_carries_vote_calls`

#### Scenario: Export from the UI
- **WHEN** the researcher presses "Export CSV" on the `main` row of Protocol decisions
- **THEN** links to `main_decisions.csv` and `main_evaluations.csv` appear, served from the exports folder, with the same content as `wider screen export main`
Pinned by: `tests/unit/test_screen_export.py::test_export_route_matches_cli`

#### Scenario: Per-pass backends in the export
- **WHEN** protocol `main` ran A on `m1`, B on `m2` set with `--provider-b`, and read record 1 with A = include and B = exclude, then C on `m1` = include
- **THEN** `main_decisions.csv` gives record 1 `decided_by_models` `m1;m2` and an empty `mixed_models`
Pinned by: `tests/unit/test_pass_backends.py::test_export_pass_backend_not_mixed`

### Requirement: Models used summary
The system SHALL show for a protocol or run, per model: passes it ran, records, evaluations, calls (failures and retries too), include, maybe ("uncertain") and exclude counts, prompt, cached and completion tokens, cost or "not priced", records whose final used it; unique record and call totals; "mixed" if a pass used a model not set for it.
Surfaces: CLI `wider screen models [--protocol P [--set N] | --run RUN_ID] [--json]`; UI LLM screening page, "Models used" card with the "Result set" select.

#### Scenario: Counts for a mixed protocol
- **WHEN** protocol `main` over 3 records ran A and B on `m1`, where one call failed and succeeded when the pass was resumed, and C on `m2` for 1 record
- **THEN** the summary gives `m1` 3 records, 6 evaluations and 7 calls, `m2` 1 record, 1 evaluation and 1 call, and totals of 3 unique records and 8 calls, marked mixed
- **AND** each model's include, maybe and exclude counts add up to its evaluations
Pinned by: `tests/unit/test_screen_models.py::test_models_summary_counts`

#### Scenario: Experimental backend marked
- **WHEN** one of the models ran on an experimental backend
- **THEN** its row in the summary, the JSON and the card says "experimental backend: not validated"
Pinned by: `tests/unit/test_provenance.py::test_experimental_marked`

#### Scenario: CLI and UI agree
- **WHEN** `wider screen models --protocol main --json` runs and `GET /api/screen/models?protocol=main` is requested
- **THEN** both return the same rows and totals
- **AND** the "Models used" card shows "3 unique records, 8 model calls" and the "mixed models" badge
Pinned by: `tests/unit/test_screen_models.py::test_models_summary_cli_matches_api`, `tests/unit/test_screen_models.py::test_models_card_text`

#### Scenario: Looking at another set without switching
- **WHEN** `main` has set 1 (active) and set 2, and set 2 is chosen in the "Result set" select of the "Models used" card and then of "Export CSV"
- **THEN** the select shows set 2 with protocol `main`, its label, role, model and run ids, the card shows set 2's rows as `wider screen models --protocol main --set 2 --json` does, and the exported files carry `result_set` 2
- **AND** the "Result sets" card still marks set 1 active, and no model call is made
Pinned by: `tests/unit/test_result_sets.py::test_set_select_keeps_active`, `tests/unit/test_result_set_select_node.py::test_select_defaults_to_active`

#### Scenario: A pass-B backend chosen on purpose is not mixed
- **WHEN** protocol `main` ran A and the judge on `m1` and B on `m2` chosen with `--provider-b`
- **THEN** `wider screen models --protocol main --json` gives `m1` passes `A`, `C` and `m2` pass `B`, and the summary and card are not marked mixed
- **AND** when a later pass-B run of the same set continued on `m3` after a confirmed backend change, it is marked mixed
Pinned by: `tests/unit/test_pass_backends.py::test_pass_backend_not_mixed`

### Requirement: Result sets of a protocol
The system SHALL keep each screening of a protocol and each import as its own numbered result set, never overwritten, with label, source, method settings, runs, author. A new set MUST be screened only when the researcher asks for one; a changed configuration alone MUST NOT start a paid run. Price, timeout, concurrency or key edits never start or invalidate a set. One set is active until the researcher picks one.
Surfaces: CLI `wider screen sets`, `--new-set`, `--set N`; UI "Result sets" card.

#### Scenario: A changed model alone runs nothing
- **WHEN** protocol `main` finished on fake backend `a` with model `m1`, the model is edited to `m2`, and `wider screen protocol main` runs, and `POST /api/jobs/screen` is called for `main`
- **THEN** no model call is made and no set is created; the CLI names the changed field, the cost range and `--new-set`, and exits 0
- **AND** the route answers 409 `new_set_needed` before a job is created, and the page shows "Screen again as a new result set" and Cancel
Pinned by: `tests/unit/test_result_sets.py::test_changed_settings_need_request`

#### Scenario: Operational edits keep the set
- **WHEN** `main` finished on `a`, then `a`'s prices, timeout and concurrency are edited and `main` runs again
- **THEN** no call is made, no set is created, and the costs logged for set 1 are unchanged
Pinned by: `tests/unit/test_result_sets.py::test_price_edit_keeps_set`, `tests/unit/test_runs.py::test_logged_cost_survives_price_edit`

#### Scenario: A new model makes a new set
- **WHEN** protocol `main` finished on fake backend `a` (model `m1`) and is run again on `b` (model `m2`) with `--new-set --set-label "model m2"`
- **THEN** `wider screen sets list main --json` lists set 1 (`m1`, active) and set 2 (`m2`, label "model m2", source screening), each with its include, maybe and exclude counts, runs, author and time
- **AND** set 1's decisions are unchanged, and `wider recall` and `wider screen export main` still use set 1
Pinned by: `tests/unit/test_result_sets.py::test_rerun_other_model_new_set`

#### Scenario: Choosing and comparing sets
- **WHEN** `wider screen sets use main 2` runs, and then `wider screen sets compare main 1 2 --json`
- **THEN** set 2 is active, the change is in the history with its author and can be undone, no model call is made, and the export uses set 2 and names it with its models, run ids and date
- **AND** the comparison gives the 3×3 table of decisions, the agreement share and the differing records by id and title
- **AND** the "Result sets" card shows the same sets, active mark and comparison through `GET /api/screen/sets` and `GET /api/screen/sets/main/compare`
Pinned by: `tests/unit/test_result_sets.py::test_use_and_compare`, `tests/unit/test_result_sets.py::test_sets_routes_match_cli`

#### Scenario: Exports name the set
- **WHEN** `main` has two sets and `wider screen export main --set 2` runs, and the share bundle is built
- **THEN** every exported row carries `result_set` 2, its label, the run id and the model
- **AND** the bundle's `screen_decisions.csv` holds both sets with `result_set` and `active`, and `result_sets.csv` lists their settings
Pinned by: `tests/unit/test_result_sets.py::test_exports_carry_set`

#### Scenario: A second set screens in its own runs, and the protocol list is unchanged
- **WHEN** protocol `main` has set 1 and gains set 2 on another model
- **THEN** set 1's run ids end in `__main.s1` and set 2's in `__main.s2`, and set 1's runs are not resumed
- **AND** `wider screen protocols`, `/api/screen` and the report's default protocol still show `main` once, never `main#2`
Pinned by: `tests/unit/test_result_sets.py::test_protocol_run_ids_carry_set`, `tests/unit/test_result_sets.py::test_new_set_keeps_protocol_list`

#### Scenario: A set identical to an earlier one
- **WHEN** `main` has set 1 on `m1` and set 2 on `m2`, the backend is set back to `m1`, and the protocol runs again without `--new-set`
- **THEN** no call is made and no set is created; the message says the settings equal set 1 and names `wider screen sets use main 1` and `--new-set`
Pinned by: `tests/unit/test_result_sets.py::test_identical_settings_points_to_set`

#### Scenario: Older store
- **WHEN** a version-3 store with protocol `main`, its decisions and one protocol run on `sample:dev` is opened
- **THEN** `main` has one active result set 1 with the same decisions, scope `sample:dev` and its role, and no model call is made
Pinned by: `tests/unit/test_result_sets.py::test_migrate_v4_result_sets`, `tests/unit/test_result_sets.py::test_migrate_single_scope`

#### Scenario: Second-screen sets keep their source
- **WHEN** `main` has sets 1 and 2, `wider screen second main --from-set 1` runs, and then `main` set 2 is made active
- **THEN** `main_second` set 1 records source `main` set 1, every exported decision of it carries that source, and the link is unchanged after the switch
- **AND** recall on `main_second` then refuses as "out of step", naming `main` set 1 and `wider screen sets use main_second 1 --with-source`, which makes `main` set 1 active again
Pinned by: `tests/unit/test_result_sets.py::test_second_set_links_source`, `tests/unit/test_result_sets.py::test_incompatible_combination_refused`

#### Scenario: An earlier split answer is offered, never applied
- **WHEN** the researcher answered a split on record 3 in `main` set 1 with exclude, and set 2 (same codebook and rules, other model) splits on record 3
- **THEN** set 2's record 3 stays maybe with its own question, which shows the earlier answer and offers "Reuse this answer"
- **AND** after `wider questions answer ID reuse`, set 2's vote is `researcher:reused:set1:<question id>`, the history names the author, and set 1 is unchanged
Pinned by: `tests/unit/test_result_sets.py::test_split_answer_stays_in_its_set`, `tests/unit/test_result_sets.py::test_reuse_earlier_answer`

#### Scenario: A continued set after a model change is marked mixed
- **WHEN** an unfinished set 1 of `main` is continued with `continue` after its model changed
- **THEN** set 1 keeps its first settings and `wider screen sets list main --json` marks it `mixed`
- **AND** a later run with set 1's first settings is not told "same settings as set 1"
Pinned by: `tests/unit/test_result_sets.py::test_continue_marks_set_mixed`

#### Scenario: A plain run already done
- **WHEN** a plain `wider screen run sample:dev` finished under model `m1`, the model is edited, and the same run is started from the CLI and through `POST /api/jobs/screen`
- **THEN** the CLI prints "already done under model m1" and exits 0, and the route answers 200 with `already_done` naming the run id and `m1`, with no job and no call
Pinned by: `tests/unit/test_result_sets.py::test_plain_run_already_done_parity`

### Requirement: Derived result sets and scopes
The system SHALL screen each set on one scope, with a role (development, validation, corpus or other). A run on a scope without a set, or after an import, MUST start nothing until the researcher asks for a new set. A new set MAY reuse another set's final decisions (from an import, or a set with equal method settings) and screen only the rest, after showing counts and cost; reused decisions keep their origin, source set unchanged.
Surfaces: CLI `--reuse-from N`, `--no-reuse`; UI new-set dialog.

#### Scenario: An incomplete import is never continued
- **WHEN** `main` set 2 is an import made with `--others-unscreened` that left 4 of 10 records without a decision, and `wider screen protocol main` runs
- **THEN** no call is made, set 2 is unchanged, and the CLI says set 2 is an incomplete import, names 6 reusable and 4 to screen with the cost range, and names `--new-set --reuse-from 2` and `--new-set --no-reuse`, exiting 0
- **AND** `POST /api/jobs/screen` answers 409 `new_set_needed` with the same counts
- **AND** an import made with the default `--others-exclude` stores every other record as exclude and counts as finished
Pinned by: `tests/unit/test_result_sets.py::test_import_set_not_continued`

#### Scenario: Screening the gaps of an import
- **WHEN** the same run is given `--new-set --reuse-from 2`
- **THEN** set 3 holds the 6 imported decisions with origin `import` and set 2, and model decisions for the 4 others with origin NULL and their run ids
- **AND** exactly 4 records are sent to the backend, set 2 is unchanged, and the export carries `origin` and `origin_set` per row
Pinned by: `tests/unit/test_result_sets.py::test_import_gaps_into_derived_set`

#### Scenario: From the development sample to the corpus
- **WHEN** `main` set 1 screened `sample:dev` (a purposive sample) and `wider screen protocol corpus --name main` runs with unchanged settings
- **THEN** no call is made; the CLI says `main` has no set for the corpus and names how many of set 1's decisions could be reused
- **AND** with `--new-set --reuse-from 1` set 2 (role corpus) reuses set 1's decisions with origin `reused` and screens only the other records; set 1 (role development) is unchanged
Pinned by: `tests/unit/test_result_sets.py::test_new_scope_needs_request`, `tests/unit/test_result_sets.py::test_derived_set_reuses_and_screens_rest`

#### Scenario: Reuse refused after a method change
- **WHEN** the model was changed after set 1 and `--new-set --reuse-from 1` is given
- **THEN** nothing runs; the message names the changed field and says earlier model decisions are not reused when the method changed
Pinned by: `tests/unit/test_result_sets.py::test_reuse_refused_on_method_change`

#### Scenario: A development set is labelled
- **WHEN** the active set of `main` has role development, and `wider recall main` and `wider screen models --protocol main` run
- **THEN** both outputs label it "development set: not an independent validation"
Pinned by: `tests/unit/test_result_sets.py::test_development_set_labelled`

#### Scenario: A protocol name with # is refused on import
- **WHEN** `wider screen import-decisions 'main#2' file.csv` runs on a synthetic file
- **THEN** it is refused with the protocol-name message, writes nothing and exits 1
- **AND** an older store that already holds key `main#2` is migrated to `main-h2`, with the rename in the history, while its run ids, run folders and `run_spec.json` stay byte-identical
Pinned by: `tests/unit/test_result_sets.py::test_import_refuses_hash_in_name`, `tests/unit/test_result_sets.py::test_migrate_v4_renames_hash_key`

### Requirement: Reuse of identical model evaluations
The system SHALL reuse an earlier model evaluation only when the researcher asks and every effective input is identical (input text with prompt and codebook, model, endpoint, temperature, seed, schema options, pass); protocol names never decide it. A reused evaluation MUST stay unchanged with its run id, model and time, and be marked reused in exports, usage and costs.
Surfaces: CLI `--reuse-evaluations`; UI run dialog checkbox.

#### Scenario: Identical inputs are reused on request
- **WHEN** protocol `main` set 1 screened `sample:dev` on fake model `m1`, and protocol `alt` (same prompt, codebook and settings) is run on `sample:dev` with `--reuse-evaluations`
- **THEN** before any call the CLI prints the records in scope, the evaluations it can reuse, the new calls needed and their cost range
- **AND** no backend call is made for the reused evaluations, `main`'s calls are unchanged, and `alt`'s evaluations export marks them `reused` with `source_run_id` set to `main`'s run
- **AND** `wider costs` counts each reused evaluation once, under `main`'s run
Pinned by: `tests/unit/test_result_sets.py::test_reused_eval_immutable_and_marked`, `tests/unit/test_result_sets.py::test_reused_eval_cost_counted_once`

#### Scenario: Any changed input prevents reuse
- **WHEN** `alt` is run with `--reuse-evaluations` after one of model, temperature (a backend edit or a run `--temperature` override), prompt version, codebook, record text, endpoint or a schema option changed, or for pass B against pass A calls, or for a second screen against first-screen calls, or against calls stored before this version
- **THEN** no evaluation is reused and every record is screened anew
Pinned by: `tests/unit/test_result_sets.py::test_eval_reuse_needs_identical_inputs`, `tests/unit/test_result_sets.py::test_passes_never_share_evaluation`, `tests/unit/test_result_sets.py::test_legacy_calls_never_reused`

### Requirement: Migration of result sets by scope
The system SHALL migrate each existing protocol without model calls or changed decisions: one set per identifiable scope from its stored runs, the original kept unchanged and active. A scope it cannot establish MUST be marked unknown, and the next run of that protocol MUST start nothing until the researcher names the scope.
Surfaces: CLI `wider screen sets scope PROTOCOL N SCOPE`; UI "Result sets" card, "Scope" select.

#### Scenario: A protocol run on two scopes
- **WHEN** a version-3 store holds protocol `main` whose runs screened `sample:dev` and then `corpus`
- **THEN** set 1 keeps every decision row unchanged, stays active and has scope `mixed`; sets 2 (`sample:dev`) and 3 (`corpus`) are added with decisions re-derived from those runs' stored calls and origin `migrated`
- **AND** no model call is made, the migration is in the history and `wider info`, and `wider costs` counts each run once
Pinned by: `tests/unit/test_result_sets.py::test_migrate_multi_scope_splits_without_calls`

#### Scenario: Unknown scope is asked for
- **WHEN** a version-3 store holds protocol `main` whose runs cannot be found, and `wider screen protocol corpus --name main` runs
- **THEN** set 1 has scope `unknown`, no call is made, and the CLI names `wider screen sets scope main 1 SCOPE`; the route answers 409 `scope_needed`
- **AND** `wider screen sets scope main 1 corpus` is refused when set 1 holds records outside the corpus, and otherwise sets it and is journalled with its author
Pinned by: `tests/unit/test_result_sets.py::test_migrate_unknown_scope_needs_answer`, `tests/unit/test_result_sets.py::test_set_scope_checks_records`

### Requirement: Model picker on the Screening page
The system SHALL show the LLM backends at the top of the Screening page as picker cards: pass A (the `screen` stage backend, else the project default) and, for protocol and second screen, B and judge (`screen_b`, `screen_judge`, else A's). A choice is for the next run only and changes no setting, as the `--provider` flags. A line by Run names locality and cost basis.
Surfaces: UI Screening page, model pickers; CLI `wider screen run|protocol|second --provider`, `protocol|second --provider-b|-c`.

#### Scenario: Preselected and chosen per run
- **WHEN** `a` is the default and `b` is set for `screen`, and the researcher chooses `c` and starts a run
- **THEN** the picker first marks `b`, the run uses `c` as `wider screen run --provider c` would, and `project.yaml` is unchanged
Pinned by: `tests/unit/test_pickers_js_node.py::test_screen_picker_preselects_stage_backend`, `tests/unit/test_stage_pages.py::test_screen_run_uses_the_picked_backend`

#### Scenario: Locality and cost next to Run
- **WHEN** the researcher picks a cloud backend with prices, then a local backend, then a cloud backend without prices
- **THEN** the line next to Run names "remote" with the destination and the prices, then "local, no charge", then "remote" and "not priced", from the same row `wider providers list --json` prints
Pinned by: `tests/unit/test_pickers_js_node.py::test_screen_run_line_names_locality_and_cost`

#### Scenario: Pickers for pass B and the judge
- **WHEN** `b` is set for `screen_b` and nothing for `screen_judge`, the mode is protocol (or second screen), and the researcher picks `c` for the judge and starts the run
- **THEN** the pass B picker first marks `b` and the judge picker marks pass A's backend; the run is the one `wider screen protocol --provider-b b --provider-c c` starts, and `project.yaml` is unchanged
- **AND** in the plain-run mode only the pass A picker is shown
Pinned by: `tests/unit/test_pickers_js_node.py::test_screen_pass_pickers`, `tests/unit/test_stage_pages.py::test_screen_protocol_uses_picked_pass_backends`

### Requirement: Record limit explained where it is set
The system SHALL explain, beside the Screening page's record limit and in the `--limit` help of `wider screen run` and `wider screen protocol`, that the limit caps the new model calls of a run, and that a protocol run whose passes are already finished still decides every record without new calls.
Surfaces: UI Screening page, record limit; CLI `wider screen run --help`, `wider screen protocol --help`.

#### Scenario: Same words on the page and the command line
- **WHEN** the Screening page opens and `wider screen protocol --help` runs
- **THEN** both say the limit caps new model calls, and that finished passes still decide every record
Pinned by: `tests/unit/test_stage_pages.py::test_limit_wording_page_and_help`

### Requirement: Configuration comparison on a purposive sample
The system SHALL run a named comparison of configurations (backend, prompt version, temperature), R times (default 3), on a purposive sample, giving per configuration misses, false includes, false maybes, agreement with final labels (range over repeats), tokens, cost and failed calls. It MUST refuse a probability, unlocked or incomplete sample and MUST NOT name a best one; failed calls raise no question.
Surfaces: CLI `wider screen compare run|show|list`; UI Screening, "Compare configurations".

#### Scenario: Two configurations on the development sample
- **WHEN** a synthetic development sample of 10 records has final labels on all 10, fake backend `a` agrees with every label and fake backend `b` excludes record 3 (labelled include), and `wider screen compare run cmp1 --sample development --config a --config b --repeats 2 --json` runs
- **THEN** 4 runs are made (2 per configuration) and the rows give `a` 0 misses and `b` 1 miss (1–1 over the repeats), each with false includes, false maybes, agreement, tokens and cost
- **AND** `wider screen compare show cmp1 --json` and `GET /api/screen/compare/cmp1` return the same rows, and neither the output nor the card marks a configuration as best
Pinned by: `tests/unit/test_screen_compare.py::test_two_configurations_rows`, `tests/unit/test_screen_compare.py::test_compare_cli_matches_api`

#### Scenario: Prompt version and temperature as configurations
- **WHEN** the comparison is given `--config a:p1 --config a:p2@0.7`
- **THEN** the first configuration screens with prompt version 1 at `a`'s temperature and the second with prompt version 2 at 0.7, and each row names its backend, model, prompt version and temperature
Pinned by: `tests/unit/test_screen_compare.py::test_prompt_and_temperature_configurations`

#### Scenario: A backend that does not send a temperature
- **WHEN** backend `c` is a `cli_agent` or `typesafe` backend, or a backend listing `temperature` in `omit_params` or setting it in `extra.body` or `extra.options`, and the comparison is given `--config c@0.7`
- **THEN** it exits 1 and `POST /api/screen/compare` answers 400, each with "backend c does not send a temperature; compare it without @0.7", and nothing is stored
- **AND** `--config c` runs, and its row says "temperature not sent by this backend" instead of a temperature, with no temperature-0 note
Pinned by: `tests/unit/test_screen_compare.py::test_temperature_not_sent`

#### Scenario: Probability sample refused
- **WHEN** `wider screen compare run cmp2 --sample validation --config a` runs on the probability sample
- **THEN** it exits 1 saying a comparison there would select the model on the recall set, no comparison is stored and no model call is made
- **AND** `POST /api/screen/compare` answers 400 with the same message
Pinned by: `tests/unit/test_screen_compare.py::test_probability_sample_refused`

#### Scenario: Unlocked development sample refused
- **WHEN** `development` is not locked and `wider screen compare run cmp1 --sample development --config a` runs
- **THEN** it exits 1 with "sample development is not locked; lock it before comparing: wider sample lock development", no comparison is stored and no model call is made
- **AND** `POST /api/screen/compare` and `POST /api/jobs/compare` answer 409 with the same sentence, and the card shows it, word for word, in normal black text beside a disabled Run as soon as an unlocked sample is selected
- **AND** `--estimate-only` and `POST /api/screen/compare/estimate` still give the estimate
- **AND** after `wider sample lock development` (or the Sampling page's "Lock sample") the same command runs; an unlock between two runs of `cmp1` refuses the second run the same way until the sample is locked again
Pinned by: `tests/unit/test_screen_compare.py::test_unlocked_sample_refused`, `tests/unit/test_compare_card_node.py::test_card_refuses_unlocked_sample`

#### Scenario: A comparison name with the run-id separator
- **WHEN** `wider screen compare run a__b --config a` runs
- **THEN** it exits 1 with "a comparison name may not contain '__'", `POST /api/screen/compare` answers 400 with the same message, and nothing is stored
Pinned by: `tests/unit/test_screen_compare.py::test_comparison_name_without_double_underscore`

#### Scenario: No development sample or no configuration
- **WHEN** `wider screen compare run cmp1 --config a` runs before any development sample is drawn, and `wider screen compare run cmp1` runs with no `--config`
- **THEN** the first exits 1 with "sample development does not exist; draw it with wider sample draw development", the second exits 1 with "at least one --config is needed"
- **AND** `POST /api/screen/compare` answers 400 with the same messages, and nothing is stored
Pinned by: `tests/unit/test_screen_compare.py::test_compare_needs_sample_and_config`

#### Scenario: Records without a final label
- **WHEN** the locked development sample has final labels on 7 of its 10 records and `wider screen compare run cmp1 --sample development --config a` runs
- **THEN** it exits 1 with "the development sample has 3 records without a final label; label them all first", no comparison is stored and no model call is made, and the API answers 409 with the same sentence
- **AND** `--estimate-only` still gives the estimate
Pinned by: `tests/unit/test_screen_compare.py::test_unlabelled_records_refused`

#### Scenario: Running a comparison again
- **WHEN** `cmp1` finished and the same command runs again, and then `cmp1` is run with another configuration list
- **THEN** the first makes no model call and shows the stored rows; the second is refused naming the stored configurations and asking for a new name, and exits 1
- **AND** an interrupted `cmp1` resumes its unfinished runs only
Pinned by: `tests/unit/test_screen_compare.py::test_rerun_resumes_and_list_is_fixed`

#### Scenario: Resuming after a backend changed or was removed
- **WHEN** `cmp1` was interrupted, then backend `a`'s model is changed from `m1` to `m2` and backend `b` is removed, and `cmp1` runs again
- **THEN** it makes no call, exits 1 and says "backend a changed since cmp1 first ran (model m1, now m2); backend b is no longer configured; start a new comparison", and the API answers 409 with the same message
- **AND** `wider screen compare show cmp1` still shows the runs that finished
Pinned by: `tests/unit/test_screen_compare.py::test_resume_refused_after_backend_change`

#### Scenario: Records added to the sample after a comparison
- **WHEN** `cmp1` finished on the 10 records of the locked `development`, which is then unlocked, and `wider sample extend development --pool boundary` adds 4
- **THEN** while the 4 new records have no final label, `wider screen compare show cmp1` and the card hide `cmp1`'s figures and say "Model decisions on development records are hidden until every record has a final label"
- **AND** once the 4 are labelled, they say "4 records were added to development after this comparison; run a new comparison to include them", and `cmp1`'s figures still cover its 10 records
- **AND** neither `show --json` nor the API names an added record (the blind queue hides which records were added)
Pinned by: `tests/unit/test_screen_compare.py::test_records_added_after_comparison`

#### Scenario: Labels changed after an unlock
- **WHEN** `cmp1` finished on the locked `development`, which is then unlocked, and record 3's final label changes from include to exclude
- **THEN** `wider screen compare show cmp1`, `GET /api/screen/compare/cmp1` and the card score every configuration against the current final labels and say once, in normal black text, "sample development is unlocked: these figures use its current labels"
- **AND** after `wider sample lock development` the line is absent and the figures are unchanged
Pinned by: `tests/unit/test_screen_compare.py::test_show_after_unlock`

#### Scenario: A sample redrawn after a comparison
- **WHEN** an upgraded project holds `cmp1`, stored before this rule on 10 records of an unlabelled `development`, which was then redrawn (allowed while unlabelled) keeping 6 of them, and is now fully labelled
- **THEN** `wider screen compare show cmp1` and the card count only those 6 (records screened, consistency) and say "4 records of this comparison are no longer in the sample"; before the labels were complete its figures were hidden
Pinned by: `tests/unit/test_screen_compare.py::test_records_left_after_redraw`

#### Scenario: A failed call in a comparison
- **WHEN** fake backend `b` fails on record 5 in repeat 1 of `cmp1`
- **THEN** `b`'s row says "1 failed call" and repeat 1's figures leave record 5 out, no screening question is raised, and running `cmp1` again retries record 5 only
- **AND** the Screening stage's last result is replaced once, at the end, by a summary naming `cmp1`, not once per run
Pinned by: `tests/unit/test_screen_compare.py::test_failed_call_counted_not_asked`

#### Scenario: A new comparison reuses identical evaluations on request
- **WHEN** `cmp3` is run with `--config a --config c --repeats 2 --reuse-evaluations` after `cmp1`
- **THEN** no backend call is made for configuration `a` (its evaluations equal `cmp1`'s and are marked reused), and only `c` is screened
Pinned by: `tests/unit/test_screen_compare.py::test_comparison_reuses_identical_evaluations`

#### Scenario: Comparison evaluations reused by a protocol run
- **WHEN** `cmp1` screened the development records with configuration `a` (no `@T`, current prompt), and `wider screen protocol main --reuse-evaluations --provider a` then screens a scope holding those records
- **THEN** pass A makes no call for them, and the run's reuse provenance names the comparison run each evaluation came from
- **AND** so does `wider screen run sample:development --reuse-evaluations`; without `--reuse-evaluations` they are screened afresh
Pinned by: `tests/unit/test_screen_compare.py::test_protocol_reuses_comparison_evaluations`

### Requirement: Consistency across repeated runs
The system SHALL give per configuration the share of screened records with the same decision in every repeat and each changed record with every repeat's decision. With one repeat it MUST say consistency was not measured; at temperature 0 it MUST say the repeats test only whether the backend gives the same answer twice.
Surfaces: CLI `wider screen compare show NAME`; UI "Compare configurations" card, "Changed between repeats".

#### Scenario: One record changes between repeats
- **WHEN** fake backend `a` answers record 3 include in repeat 1 and exclude in repeat 2 (by the seed it is sent), and every other record the same in both
- **THEN** `a`'s consistency is "9 of 10 records (90%)" and record 3 is listed with "include, exclude" in the CLI, the API and the card
Pinned by: `tests/unit/test_screen_compare.py::test_consistency_lists_changed_records`

#### Scenario: One repeat, and temperature 0
- **WHEN** a configuration runs with `--repeats 1`, and another at temperature 0 with 3 repeats
- **THEN** the first row says "consistency not measured (one repeat)"; the second's consistency carries "temperature 0: tests only whether the backend gives the same answer twice"
Pinned by: `tests/unit/test_screen_compare.py::test_consistency_notes`

### Requirement: Comparison per pool and per research area
The system SHALL break each configuration's misses, false includes and labelled n down by development pool, and by research area: the cluster of the ranking the sample was drawn from, records beyond the clustered depth as "not clustered". A sample whose ranking has no clusters MUST say so instead of a per-area table.
Surfaces: CLI `wider screen compare show NAME [--by pool|area]`; UI "Compare configurations" card, "By pool" and "By research area".

#### Scenario: A miss in one research area
- **WHEN** the sample's records fall in clusters 0 and 1 of its ranking plus one record ranked beyond `cluster_top`, and backend `b` misses one included record of cluster 1
- **THEN** the per-area table gives `b` 1 miss in cluster 1, 0 in cluster 0, and a "not clustered" row with n 1; the per-pool table gives the miss under the record's pool
Pinned by: `tests/unit/test_screen_compare.py::test_breakdown_by_pool_and_area`

#### Scenario: No clusters
- **WHEN** the sample's ranking was never clustered
- **THEN** the per-area view says "no research areas: the ranking has no clusters (wider rank cluster)", and the per-pool table is shown
- **AND** for an imported sample with no ranking it says "no research areas: the sample has no ranking"
Pinned by: `tests/unit/test_screen_compare.py::test_breakdown_without_clusters`

### Requirement: Comparison cost shown before any call
The system SHALL show, before any model call of a comparison, the records, runs and new calls (configurations × repeats × records, less reusable evaluations) and each backend's locality and cost range. `--estimate-only` MUST stop there without storing the comparison; the page shows the same figures in a confirm step before it starts the job.
Surfaces: CLI `wider screen compare run ... --estimate-only`; UI "Compare configurations" card, confirm step.

#### Scenario: Estimate only
- **WHEN** 2 configurations with 3 repeats on 10 records are estimated with `--estimate-only --json`, and `POST /api/screen/compare/estimate` is called with the same body
- **THEN** both give 10 records, 6 runs and 60 new calls with each backend's locality and cost range, no model call is made, and `wider screen compare list` shows no comparison
Pinned by: `tests/unit/test_screen_compare.py::test_estimate_only_parity`

#### Scenario: A remote backend without consent
- **WHEN** a comparison names remote backend `r`, which has no consent for screening
- **THEN** `wider screen compare run`, without a terminal, exits 2 with the consent message and `POST /api/jobs/compare` answers 403 `backend_consent_required`, before any call, and no comparison is stored
Pinned by: `tests/unit/test_screen_compare.py::test_compare_remote_needs_consent`

### Requirement: A backend per pass
The system SHALL run pass B and judge C of `wider screen protocol`, and reads S2 and S3 of `wider screen second`, on their own backend: `--provider-b`, `--provider-c`, else stage keys `screen_b`, `screen_judge`, else pass A's (S1). Each pass uses its backend's temperature, all one prompt version. Cost and consent cover each pass's backend before any call.
Surfaces: CLI `wider screen protocol|second --provider-b NAME --provider-c NAME`; UI LLM screening page, pickers "Pass B" and "Judge".

#### Scenario: Three backends
- **WHEN** protocol `main` runs with `--provider a --provider-b b --provider-c c` on fake backends whose passes disagree on 2 records
- **THEN** pass A's calls carry `a`'s model, pass B's `b`'s and the 2 third reads `c`'s, and each run id names its pass's backend
- **AND** `wider screen models --protocol main --json` gives each model row the passes it ran (`A`, `B` or `C`), and the card shows them
Pinned by: `tests/unit/test_pass_backends.py::test_three_backends`

#### Scenario: Stage keys as defaults
- **WHEN** `wider providers stage screen_b b` and `wider providers stage screen_judge c` are set and `main` runs without per-pass flags
- **THEN** pass B uses `b` and the judge `c`; a run's `--provider-b a` uses `a` for that run only and `project.yaml` is unchanged
Pinned by: `tests/unit/test_pass_backends.py::test_stage_keys_default_per_pass`

#### Scenario: Second screen with per-pass backends
- **WHEN** `wider screen second main --provider a --provider-b b --provider-c c` runs
- **THEN** read S1 uses `a`, S2 `b` and the third read S3 `c`, `main_second`'s settings record `b` and `c`, and the model rows name `S1`, `S2`, `S3`
Pinned by: `tests/unit/test_pass_backends.py::test_second_screen_per_pass`

#### Scenario: A per-pass backend for a pass that does not run
- **WHEN** protocol `main` has `screening.passes: 1`, or `blind_third_read: false`, and runs with `--provider-b b` or `--provider-c c`
- **THEN** the run goes ahead and prints "pass B does not run in this protocol (screening.passes: 1); --provider-b is not used" (or the judge's line), the job result carries the same note, and the Screening page disables that picker with the same text
- **AND** `wider screen second` with the same protocol and flags uses both (S2 always runs, S3 when S1 and S2 disagree) and prints no such note
Pinned by: `tests/unit/test_pass_backends.py::test_unused_pass_backend_noted`

#### Scenario: Cost and consent for every pass's backend
- **WHEN** `main` runs with pass A on local `a` and `--provider-b r`, a priced remote backend without consent, first with `--estimate-only` and then without
- **THEN** the estimate prices pass B at `r`'s rates and pass A at "local, no charge"; the run stops before any call with the consent message naming `r`, and `POST /api/jobs/screen` answers 403 `backend_consent_required` before a job starts
Pinned by: `tests/unit/test_pass_backends.py::test_cost_and_consent_per_pass`

### Requirement: Per-pass backends in the result set's settings
The system SHALL record pass B's and the judge's backend and temperature in a result set's settings only when they differ from pass A's, so a set made before this change, or with every pass on A's backend, keeps its settings. A changed per-pass backend MUST start nothing without `--new-set`, as a changed model does.
Surfaces: CLI `wider screen protocol|second --new-set`; UI new-set dialog.

#### Scenario: An older set needs no new set
- **WHEN** a store made before this change holds `main` set 1 on `a`, and `wider screen protocol main` runs with no per-pass backend
- **THEN** no model call is made and no set is created, and set 1's settings are unchanged
Pinned by: `tests/unit/test_pass_backends.py::test_old_set_keeps_settings`

#### Scenario: A new pass-B backend needs a new set
- **WHEN** `main` set 1 finished with every pass on `a`, and `main` runs with `--provider-b b`, then with `--provider-b b --new-set`
- **THEN** the first makes no call, names the changed field `backends.B.model` and `--new-set` and exits 0, and the route answers 409 `new_set_needed`; the second creates set 2
Pinned by: `tests/unit/test_pass_backends.py::test_pass_backend_change_needs_new_set`

### Requirement: Passes that share a model are named
When two passes (A, B, judge; or S1, S2, S3) use the same model and temperature, the system SHALL say, before the run, in its result and in the methods draft, which passes share it and that their readings are not independent. It MUST NOT refuse the run.
Surfaces: CLI `wider screen protocol|second`; UI LLM screening page, line under the pickers; methods draft.

#### Scenario: A and B on one model
- **WHEN** `main` runs with passes A and B on `a` and the judge on `c`
- **THEN** the CLI, the page line and the methods draft say "Passes A and B use the same model (a's model, temperature 0): their readings are not independent."
- **AND** the run goes ahead
Pinned by: `tests/unit/test_pass_backends.py::test_shared_model_named`, `tests/unit/test_pickers_js_node.py::test_screen_pickers_name_shared_model`

### Requirement: Where screening stands
The system SHALL list the screening steps, each completed, in progress, needs attention or not started: prompt; development sample fully labelled (if a purposive sample exists); development sample tried (optional); validation labels; whole corpus screened by a protocol with a real model; maybes (optional). The next step is the first open required one; Recall only when all are done. An open required step gives `needs_you`.
Surfaces: UI Screening summary card; CLI `wider screen status`.

#### Scenario: A test-model corpus round is not finished screening
- **WHEN** the whole corpus was screened by a protocol with the test model only and every validation record has a label
- **THEN** the corpus step needs attention with "test model", the next step is not Recall and report, and `wider status` shows the screen stage as "done, needs you: ..."
Pinned by: `tests/unit/test_screen_progress.py::test_test_model_corpus_needs_attention`

#### Scenario: Recall comes next only when the required steps are done
- **WHEN** a protocol with a real model holds a decision for every record and every validation record has a final label
- **THEN** every required step is completed, the next step is "Estimate recall" on the Recall and report page, and the stage has no `needs_you`
Pinned by: `tests/unit/test_screen_progress.py::test_recall_next_when_required_steps_done`

#### Scenario: Validation labels missing
- **WHEN** 12 of 21 validation records have a final label
- **THEN** the labels step is in progress with "12 of 21", links to Samples and labelling, and names no label or decision of a record
Pinned by: `tests/unit/test_screen_progress.py::test_labels_step_counts_only`

#### Scenario: The command line prints the same steps
- **WHEN** `wider screen status` runs on a project
- **THEN** it prints each step with its status word and text and a closing "next:" line, and `--json` returns the same `steps` and `next` as `GET /api/screen` `progress`
Pinned by: `tests/unit/test_screen_progress.py::test_cli_screen_status`

#### Scenario: Development labels open
- **WHEN** the development sample has 41 of 120 final labels
- **THEN** the step "development sample fully labelled" is in progress with "79 left", the next step is "Continue labelling" on Samples and labelling, and no step offers a model run that the gate would refuse
- **AND** the "tried" step says its results are hidden until labelling is complete when earlier runs exist
Pinned by: `tests/unit/test_screen_progress.py::test_dev_labels_open_step`

### Requirement: Screening round names and kinds
The system SHALL give every screening round a readable name from its records, protocol, set, pass and prompt ("Whole corpus · protocol main · pass A · prompt v1") and a kind: development sample, validation sample, other sample, whole corpus, maybes, chosen records or other, plus "test model" when its backend is the test backend. The run id stays in technical details, the CLI's JSON and the files.
Surfaces: UI Screening summary card and history; CLI `wider screen status`, the screening headline.

#### Scenario: Names of every scope
- **WHEN** rounds exist on the development sample, the validation sample, the corpus under protocol main set 2, the maybes of main and three chosen records
- **THEN** their names start "Development sample", "Validation sample", "Whole corpus · protocol main · set 2", "Maybes of main" and "Chosen records", each with its pass and prompt
Pinned by: `tests/unit/test_screen_progress.py::test_run_names_and_kinds`

#### Scenario: The headline names the round
- **WHEN** a pass on the validation sample with the test backend finishes
- **THEN** the stage headline reads "Screened 21 of 21 records of the validation sample, pass A (test model)" and has no run id; the run id is in the result's rows
Pinned by: `tests/unit/test_screen_progress.py::test_screen_headline_names_the_round`

### Requirement: Unusual screening results explained
The system SHALL add a plain note to a round or protocol set with at least 5 decisions when every record is excluded, every record is included or more than half are maybe, saying what it may mean and what to check. Decisions that came only from the test model are said to be no screening result at all.
Surfaces: UI Screening summary card and history; CLI `wider screen status`.

#### Scenario: Every record excluded
- **WHEN** a round of 21 records excluded all 21
- **THEN** its note says every record was excluded and names the prompt, the model's answers and the backend as things to check
- **AND** with the test backend the note says the decisions come from the test model and are not a screening result
Pinned by: `tests/unit/test_screen_progress.py::test_unusual_notes`

#### Scenario: Small rounds have no note
- **WHEN** a round of 4 records excluded all 4
- **THEN** it has no unusual note
Pinned by: `tests/unit/test_screen_progress.py::test_unusual_notes`

### Requirement: Screening page in three task tabs
The system SHALL open the Screening page with the card "Where screening stands" (steps, a next-step button, a quiet "Go to Recall and report anyway" link while required steps are open, the round shown: name, kind, records, failures, decisions, note, cost, and closed "Technical details" with run id, folder, tokens and backend), then three tabs: Set up and run (with history), Decisions and result sets, Check quality. Every control stays.
Surfaces: UI Screening page; CLI `wider screen status`.

#### Scenario: First visit after a test round
- **WHEN** the page is drawn with one validation-sample round of the test backend where all 21 records were excluded
- **THEN** the card shows the round's name, the "test model" badge, "21 of 21" records, 0 failed, the counts and the red note, and the run id only inside the closed "Technical details"
- **AND** the next-step button does not lead to Recall and report while the "Go to Recall and report anyway" link is shown
Pinned by: `tests/unit/test_screen_page_node.py::test_summary_after_a_test_round`

#### Scenario: Showing an earlier round
- **WHEN** the researcher clicks "Show" on a round in Screening history
- **THEN** the summary card shows that round and the round select names it
Pinned by: `tests/unit/test_screen_page_node.py::test_history_show_selects_the_round`

#### Scenario: Three tabs keep every control
- **WHEN** the page is loaded
- **THEN** the tabs read "1 Set up and run", "2 Decisions and result sets" and "3 Check quality", and the model cards, the start form, the history, protocol decisions, records of one round, result sets, models used, development evaluation and adjacent cases are each in one of them
Pinned by: `tests/unit/test_static_followup.py::test_screen_page_is_split_into_three_task_tabs`

### Requirement: Evaluation and comparison name unadjudicated labels
The system SHALL say in the development evaluation and the configuration comparison how many labelled records where reviewers disagree are not adjudicated and whose labels were used, as the recall estimate does. Evaluating on a sample that is not locked SHALL say in normal black text "this sample is not locked: its labels can still change, and so can this evaluation".
Surfaces: CLI `wider screen evaluate RUN`, `wider screen compare run NAME`; UI Screening page, evaluation and comparison cards.

#### Scenario: Two disagreements on an open sample
- **WHEN** every record of the unlocked development sample has a final label, "ana" and "bo" disagree on 2 of them, none adjudicated, and a run is evaluated, then the sample is locked and the run compared
- **THEN** both outputs say "2 labelled records where the reviewers disagree are not adjudicated; the evaluation uses ana's labels there" (the comparison: "the comparison uses")
- **AND** the evaluation also says "this sample is not locked: its labels can still change, and so can this evaluation", once, in black
- **AND** after adjudicating both and locking the sample neither sentence appears
Pinned by: `tests/unit/test_screen.py::test_evaluation_names_unadjudicated_labels_and_an_open_sample`, `tests/unit/test_screen_compare.py::test_comparison_names_unadjudicated_labels`

### Requirement: Number options on the command line refuse in plain words
The system SHALL refuse a number option or setting that is not a whole number (a share: not a number) with the page's own sentence and range and exit code 1, not the command-line parser's wording. A negative value after `wider protocol set PATH` SHALL reach the setting's check, not be read as an option.
Surfaces: CLI `wider rank run --top-n`, `wider screen run|protocol|second --limit`, `wider benchmark split --dev-share`, `wider protocol set`; UI the same fields (unchanged).

#### Scenario: Fractions, words and negatives
- **WHEN** `wider rank run --top-n 2.5`, `--top-n abc`, `wider screen run --limit 2.5`, `wider benchmark split --dev-share abc` and `wider protocol set screening.max_tokens -5` run on a synthetic project
- **THEN** each exits 1 with a sentence naming the option and its range as the page and the API say it ("top-N must be a whole number from 1 to the number of cells", "the record limit must be a whole number", "the development share must be a number from 0 to 1", and `screening.max_tokens`'s own range check)
- **AND** none prints "Invalid value", "is not a valid" or "No such option", and nothing is run or saved
Pinned by: `tests/unit/test_cli.py::test_number_options_refuse_in_plain_words`

### Requirement: An import of decisions is listed in History
Every import of decisions SHALL add one History entry `decisions_import` naming the protocol, the new set, the file and the start of its sha256, with author and time, in the same transaction as the set. It MUST NOT be undoable, as a result set is never deleted; an earlier set switch of that protocol is then refused too, naming the import.
Surfaces: CLI `wider screen import-decisions PROTOCOL FILE [--author NAME]`, `wider history list`, `wider history undo`; UI History page.

#### Scenario: Import listed on both surfaces
- **WHEN** reviewer `Ana` runs `wider screen import-decisions main revised.csv` on a synthetic project whose `main` holds set 1, adding set 2
- **THEN** `wider history list --kind decisions_import` and the History page (`GET /api/history?kind=decisions_import`) show one entry "decisions imported into main set 2 from revised.csv (sha256 <first 12 characters>)" by Ana with its time, and the Show list offers "decisions imported"
- **AND** `wider history undo <id>` and the page's Undo are refused with "imported decisions stay on record as their own result set; make another set active instead", and the page's "Undo my last change" (`POST /api/history/undo-last`) for Ana undoes her change before the import instead
Pinned by: `tests/unit/test_screen_export.py::test_import_listed_in_history`

#### Scenario: A set switch before an import
- **WHEN** Ana makes set 2 of `main` active, Bea imports a file as set 3 (`--author Bea`), then Ana undoes her last change (`wider history undo` without an id, or the page's "Undo my last change")
- **THEN** nothing is undone and both say "set 3 was imported into main since, and imported decisions stay on record as their own result set; choose the set to use with wider screen sets use main <set> instead" and "blocked by change <id> by Bea: decisions imported into main set 3 ..."; the History list gives the switch the same reason
Pinned by: `tests/unit/test_screen_export.py::test_import_listed_in_history`

#### Scenario: A failed import leaves no entry
- **WHEN** an import fails inside its transaction after the set row was written
- **THEN** neither the result set nor a `decisions_import` entry is stored
Pinned by: `tests/unit/test_screen_export.py::test_import_listed_in_history`

### Requirement: Evaluations and comparisons flag later label changes
The system SHALL say once, in normal black text, in the development evaluation of a run and in a configuration comparison on a purposive sample, how many of the sample's records had their labels changed (a label, adjudication or correction) after the run or comparison finished, and that the figures use the current labels. Nothing stored is deleted; an undone change no longer counts.
Surfaces: CLI `wider screen evaluate RUN`, `wider screen compare show NAME`; UI Screening page, comparison card.
#### Scenario: A label corrected after the run and the comparison
- **WHEN** run R and comparison `cmp1` finished on the locked `development`, and R1 then corrects record 3 with a reason and R2 adds a second label on record 9
- **THEN** `wider screen evaluate R` and its route say once "2 records of development had their labels changed after run R ran (<date the run finished>); this evaluation uses the current labels.", and `wider screen compare show cmp1` and its route say once "2 records of development had their labels changed after this comparison ran (<date>); its figures use the current labels."
- **AND** the run, its calls and the comparison are still stored, and after both changes are undone neither line appears
Pinned by: `tests/unit/test_label_completion.py::test_evaluation_flags_later_label_changes`, `tests/unit/test_label_completion.py::test_comparison_flags_later_label_changes`

#### Scenario: An unlocked sample
- **WHEN** `development` is unlocked after the comparison and record 3's label changes
- **THEN** the comparison says "sample development is unlocked: these figures use its current labels" and "1 record of development had its labels changed after this comparison ran (<date>).", the second without a tail, so nothing is said twice; the evaluation likewise drops its tail beside "this sample is not locked: ..."
Pinned by: `tests/unit/test_label_completion.py::test_comparison_flags_later_label_changes`

#### Scenario: No change since
- **WHEN** no label of `development` changed after the run and the comparison
- **THEN** neither line appears
Pinned by: `tests/unit/test_label_completion.py::test_evaluation_flags_later_label_changes`

### Requirement: Importing an adjudicator's decisions for some records
The system SHALL import an adjudicator's decisions from one CSV column into a new active result set built from a finished set: records without a value keep their decision; each adjudicated record is decided by `researcher`, naming the adjudicator. Unused rows are listed with a reason; the base set is unchanged.
Surfaces: CLI `wider screen import-adjudication PROTOCOL FILE --adjudicator NAME`; UI Results page, "Import adjudicator's decisions".

#### Scenario: Some records adjudicated, the rest kept
- **WHEN** protocol `main` holds set 1 with 7 decisions, the Results export of its decisions is written as CSV, an `adjudication` column is filled with include for 2 rows and exclude for 1 row and left blank for the others, and `wider screen import-adjudication main adj.csv --adjudicator Ana` runs
- **THEN** `main` has set 2 (source adjudication, derived from set 1, active) whose 3 adjudicated records are decided by `researcher` with votes ending in `researcher:<decision>`, reason "researcher (Ana) decided as adjudicator", origin kind `adjudication` holding the set 1 decision, and pathway "decided by the researcher", also for a record whose set 1 decision was carried from another set
- **AND** the other 4 records of set 2 equal set 1's rows in decision, votes, cell, labels, reason, `decided_by` and origin, and every row of set 1 is unchanged with set 1 no longer active
- **AND** the result counts 3 imported (changed or confirmed) and the blank rows, and says "Set 2 of main is now active (adjudicated from set 1: ...). Set 1 is unchanged; wider screen sets use main 1 makes it active again."
Pinned by: `tests/unit/test_adjudication_import.py::test_partial_import_keeps_the_rest`

#### Scenario: Rows that cannot be used are listed
- **WHEN** the file also holds a row with value `perhaps`, a row whose DOI and title match no record, a row whose `record_id` exists but whose title differs, a second row for an adjudicated record with another decision, a row matched only by a similar title, a row for a record of a development sample not fully labelled, a row for a record whose failed read is open, and a row for a record whose failed read was answered but not written yet
- **THEN** none of those records changes, and the result lists each row by number with `unreadable`, `unmatched`, `title_differs`, `similar_title` (with the score and both titles), `duplicate` (the conflict shown in red, the first row kept), `hidden` (with a review decision's wording), `failed_read` (naming `wider questions answer <id>`) and `failed_answered`
- **AND** the CLI prints the same lines and the route returns the same lists
Pinned by: `tests/unit/test_adjudication_import.py::test_rows_left_out_are_listed`, `tests/unit/test_adjudication_import.py::test_hidden_and_failed_reads_are_left_out`

#### Scenario: Count first
- **WHEN** the same import runs with `--dry-run`, and the Results card posts the file with `dry_run=true`
- **THEN** both give the same counts and lists as the real import would, and no set, decision, researcher entry or History entry is written
Pinned by: `tests/unit/test_adjudication_import.py::test_dry_run_writes_nothing`, `tests/unit/test_adjudication_import.py::test_route_matches_cli`

#### Scenario: Refused imports change nothing
- **WHEN** the file is not a UTF-8 CSV, or has no `adjudication` column, or no row has a usable decision, or the base set is not finished, or no adjudicator name is given while a used row has a blank or missing `adjudicator` cell, or the name reads "Researcher 1"
- **THEN** the import is refused with its sentence (exit 1 for the file, the column, no usable row and the unfinished set; exit 2 for a missing or refused name; route 400 for each), and no set, researcher entry or History entry exists
Pinned by: `tests/unit/test_adjudication_import.py::test_refusals_change_nothing`

#### Scenario: A file exported from another set
- **WHEN** the file was exported from set 1 of `main` and set 2 is active when it is imported
- **THEN** the import builds set 3 from set 2 and says once, in normal black text, "This file was exported from set 1 of main; it was imported into set 2 (the active set). ..."
Pinned by: `tests/unit/test_adjudication_import.py::test_file_from_another_set_is_said`

#### Scenario: The adjudicator in shared files
- **WHEN** the file `adj_Jo.csv` is imported with `--adjudicator Ana` and a row whose `adjudicator` cell reads `Jo`, in attribution mode `pseudonyms`, and `wider screen export main`, the audit CSV and the share bundle are written
- **THEN** `exports/screening/main.s2_decisions.csv` reads "researcher (Researcher N) decided as adjudicator" with N each name's number in the researcher list, and no exported file holds "Ana", "Jo" or "adj_Jo"
- **AND** the audit CSV's `decided_by` reads `researcher`, Ana is listed as declared, and Jo as inferred "from an adjudication file"
Pinned by: `tests/unit/test_adjudication_import.py::test_adjudicator_in_shared_files`

#### Scenario: Not reused as model decisions
- **WHEN** set 2 (adjudication) is active and `main` is screened again with the same settings, as a new set or with `--reuse-from 2`
- **THEN** set 2 is not offered as a reuse source, `--reuse-from 2` is refused saying set 2 holds an adjudicator's decisions, not model calls, and a run that re-derives set 2 keeps every adjudicated decision
Pinned by: `tests/unit/test_adjudication_import.py::test_adjudication_set_not_reused`

#### Scenario: Second screens and validation samples are told
- **WHEN** a second screen was built on set 1, and 2 adjudicated records belong to a validation sample
- **THEN** the result names the second screen as out of step, and the sample's line "N records of this sample were decided by a person who saw the AI model's reads" counts the 2 adjudicated records of the sample (one changed, one confirmed)
Pinned by: `tests/unit/test_adjudication_import.py::test_second_screen_and_sample_are_told`

#### Scenario: The page card
- **WHEN** the Results page shows set 1 of `main` and a file is chosen in "Import adjudicator's decisions" with "Ana" typed beside "Adjudicator"
- **THEN** the card shows the counts and the rows left out in normal black text (conflicts in red) before "Import", and "Import" posts the same file with set 1 and then shows the active-set line
Pinned by: `tests/unit/test_results_js_node.py::test_adjudication_card_counts_then_imports`

### Requirement: An adjudicator import is listed in History and can be undone
Every adjudicator import SHALL add one History entry `adjudication_import` naming protocol, new set, sha256 start and counts (no names), by the importing person, in the set's transaction. Undo SHALL remove the new set and reactivate the earlier set; redo restores it; set numbers are never reused. Undo MUST be refused, naming why, once something uses the set.
Surfaces: CLI `wider history list --kind adjudication_import`, `wider history undo [ID]`, `wider history redo`; UI History page.

#### Scenario: Listed, undone and redone
- **WHEN** Bea imports Ana's adjudication into `main` as set 2 (`--author Bea`), then runs `wider history undo`, then `wider history redo`
- **THEN** History lists one entry "adjudicator decisions imported into main set 2 (file sha256 <12>): ..." by Bea, and the History page's Show list offers "adjudicator decisions imported"
- **AND** after undo `main` has only set 1, active, with its rows as before the import, and after redo set 2 is back, active, with the same rows
- **AND** when the import is undone again and the protocol is screened as a new set, that set is set 3, not set 2
Pinned by: `tests/unit/test_adjudication_import.py::test_history_undo_and_redo`

#### Scenario: Undo refused once the set is used
- **WHEN** after the import (or after its undo and redo) a review decision is made in set 2, or recall of set 2 is seen on a validation sample, or a quality-control sample is drawn from set 2, or a second screen is built on set 2
- **THEN** undoing the import is refused, naming the later change ("screen_decisions changed since; undo the later change first", blocked by the review decision) or the use ("recall of set 2 was seen on a validation sample; that look stays on record", "a quality-control sample was drawn from set 2", "a second screen was built on set 2 (<key>)"), and nothing changes
Pinned by: `tests/unit/test_adjudication_import.py::test_undo_refused_once_used`

#### Scenario: An earlier set switch is not called final
- **WHEN** Ana makes set 2 of `main` active, then Bea imports an adjudication as set 3, then Ana undoes her set switch
- **THEN** the undo is refused with "screen_result_sets changed since; undo the later change first", blocked by Bea's adjudication, and the reason does not say imported decisions stay on record
Pinned by: `tests/unit/test_adjudication_import.py::test_earlier_switch_names_the_adjudication`

#### Scenario: A failed import leaves no entry
- **WHEN** the import fails inside its transaction after the set row was written
- **THEN** neither the set, its decisions, nor an `adjudication_import` History row is stored, and the base set is still active
Pinned by: `tests/unit/test_adjudication_import.py::test_failed_import_leaves_nothing`

### Requirement: Development sample fully labelled before the model sees it
The system MUST refuse, while a purposive sample is not complete under the review protocol, every screening run that includes its records (whole corpus too), naming the sample and the labels left, exit 1, API 409, storing and sending nothing. It is a methodological refusal like the probability-sample one, not stage gating.
Surfaces: CLI `wider screen run|protocol|second`; UI Screening page; API screen and job routes.

#### Scenario: Labels still open
- **WHEN** the synthetic development sample has 120 records and 41 final labels, and a run on `sample:development` or on the corpus is requested from the CLI or the page
- **THEN** each is refused with "the development sample has 79 records without a final label; label them all first", no backend is called and nothing is stored
- **AND** the Screening page shows that sentence with "Continue labelling" as its main button
Pinned by: `tests/unit/test_screen_dev_gate.py::test_refused_while_labels_open`, `tests/unit/test_screen_page_node.py::test_dev_gate_message`, `tests/unit/test_screen_page_node.py::test_dev_gate_refusal_runs_through_start_screen`

#### Scenario: Two reviewers required
- **WHEN** the review protocol for the development sample is "two", reviewer ana labelled all 120 records and bo labelled 100, agreeing on 95 with 2 of the 5 disagreements adjudicated
- **THEN** the sample is not complete (20 records lack bo's label, 3 disagreements wait for adjudication) and runs are refused naming both counts; ana's labels alone are never taken as final
Pinned by: `tests/unit/test_screen_dev_gate.py::test_two_reviewer_protocol`

#### Scenario: A review package still out
- **WHEN** the protocol is "one" but a review package holding development records is out and not imported or withdrawn
- **THEN** runs that include those records are refused with "a review package with development records is still out", until every item is decided by an import or the package is withdrawn; a partly imported package keeps the gate closed
Pinned by: `tests/unit/test_screen_dev_gate.py::test_open_package_closes_the_gate`

#### Scenario: A locked sample that is not complete
- **WHEN** the development sample was locked with 7 of 10 records labelled (allowed before this change)
- **THEN** runs are refused as above, and "Continue labelling" opens a queue where the 3 missing labels can be added on the locked sample (change `label-completion-and-sealing`, D22)
Pinned by: `tests/unit/test_screen_dev_gate.py::test_locked_incomplete_sample_can_be_completed`

#### Scenario: What stays open
- **WHEN** labels are still open
- **THEN** choosing a model, `wider check-backends`, a cost estimate and a run on a validation sample that holds no development record proceed; one that holds some is refused like any other scope
Pinned by: `tests/unit/test_screen_dev_gate.py::test_preparation_not_refused`

#### Scenario: Every record labelled, or no purposive sample
- **WHEN** the sample is complete under its protocol, or the project has no purposive sample
- **THEN** runs and evaluations proceed as before, and a comparison once the sample is locked
Pinned by: `tests/unit/test_screen_dev_gate.py::test_allowed_when_all_labelled`

#### Scenario: Extending the sample after screening
- **WHEN** a corpus round exists and `wider sample extend development --pool boundary` would add 4 records
- **THEN** the Sampling page and the command first say that until the 4 are labelled, development model output is hidden and screening runs that include those records are refused, and ask to confirm; nothing changes on Cancel, `--yes` confirms on the command line, and `POST /api/samples/{name}/extend` without `confirm: true` answers 409 with the same sentence
Pinned by: `tests/unit/test_screen_dev_gate.py::test_extend_after_screening_warns`

### Requirement: Earlier model output on an open development sample is hidden
The system SHALL hide, while a purposive sample is not complete under its review protocol, each of its records' model decisions and every evaluation and comparison figure on it, wherever shown or exported per record, saying how many and why. Totals, PRISMA counts and recall are unchanged. Nothing is deleted.
Surfaces: UI Screening, drawer, Screening review, dashboard lists; CLI evaluate, compare show, review list, export; API reads.

#### Scenario: A project that ran the model early
- **WHEN** a synthetic project has a development run on all 120 records from before this rule and 41 final labels
- **THEN** `wider screen compare show`, the evaluation card and the drawer of a development record show no model decision and say "Model decisions on 120 development records are hidden until every record has a final label"
- **AND** the stored decisions are unchanged, and after the last label they show as before
Pinned by: `tests/unit/test_screen_dev_gate.py::test_earlier_output_hidden_not_deleted`

#### Scenario: Exports and totals
- **WHEN** in that project a decision set is exported (RIS, BibTeX, CSV), `wider export selection` and `wider screen export` run, the replication package and the share bundle are written, an earlier `exports/screening/main_decisions.csv` is requested through `GET /api/outputs/file`, `GET /api/screen/calls/{run_id}` is read for a development run, and the plain corpus export runs
- **THEN** every export with model decisions is refused on the page, the CLI and the API with "Exports with model decisions wait until the development sample is complete: 79 records need a final label", nothing is written or served (the earlier file is refused and marked as waiting in the output list; a report is served only when its `report.json` says comparisons are hidden or the sample is complete now), and the calls route returns no development record's reads
- **AND** the plain corpus export writes every unique record, and no export ever leaves records out silently
Pinned by: `tests/unit/test_screen_dev_gate.py::test_exports_and_totals`

#### Scenario: Imported decisions while labels are open
- **WHEN** `wider screen import-decisions` runs on that project with decisions for 30 development records
- **THEN** the import proceeds and is recorded, its decisions on those 30 records are hidden as for model output, and exporting the imported set is refused with the same sentence until the sample is complete
Pinned by: `tests/unit/test_screen_dev_gate.py::test_imported_decisions_hidden`

#### Scenario: The report stays available
- **WHEN** `wider report` runs in that project
- **THEN** `report.md` and `report.json` are written with the PRISMA counts and the recall estimate unchanged, and their configuration-comparison section says "Comparison figures are hidden until the development sample is complete, so they cannot sway the labels still open" instead of the figures
Pinned by: `tests/unit/test_screen_dev_gate.py::test_report_hides_comparisons`

#### Scenario: The blind drawer
- **WHEN** the labelling queue opens the drawer of a development record and of a validation record in that project
- **THEN** neither shows a model decision, a quote highlight or a sentence about hidden output, so the two drawers differ only in the record's own text
Pinned by: `tests/unit/test_drawer_keys_node.py::test_blind_drawer_same_for_dev_and_validation`

#### Scenario: Replay
- **WHEN** `wider screen replay` runs on an early development run while the sample is incomplete
- **THEN** it is refused like a run, naming the labels left, and writes nothing
Pinned by: `tests/unit/test_screen_dev_gate.py::test_replay_refused`

#### Scenario: Questions about development records
- **WHEN** an earlier run left a `screen_split` question on a development record and the sample is incomplete
- **THEN** the Questions page and `wider questions list` show that a question is waiting without the model's reads, saying they show once the sample is complete
Pinned by: `tests/unit/test_screen_dev_gate.py::test_questions_hide_model_reads`

#### Scenario: An upgraded project is told
- **WHEN** such a project is opened after this change
- **THEN** its Overview's Needs attention says that screening runs including development records wait until the sample is complete, and why
Pinned by: `tests/unit/test_shell_js_node.py::test_overview_dev_gate_item`
