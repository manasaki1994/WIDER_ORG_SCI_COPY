# doubt-handling Specification

## Purpose
When the tool would otherwise guess or silently skip, it raises a question for the researcher and applies the answer
at once. Questions come from screening (`screen`), enrichment (`enrich`), seed-paper fetching (`setup`) and construct
proposals (`construct`); the researcher chooses per project whether they wait in a queue or open in a dialog while the
run continues.

## Requirements

### Requirement: Five kinds of doubt
The system SHALL raise `screen_failed` when a screening answer is unusable after retries, `screen_split` when three protocol reads are include, maybe and exclude, `enrich_unresolved` when no source has a record's abstract, `seed_unresolved` when no abstract is found for a seed paper, and `proposal_failed` when a construct proposal fails. Each question stores its options, context, record or seed id, run and job.
Surfaces: CLI `wider questions list`; UI Questions page.

#### Scenario: An unusable screening answer becomes a question
- **WHEN** a screening pass gets an answer that is not JSON for two records
- **THEN** two open `screen_failed` questions exist for the pass's run, each offering include, maybe, exclude and retry
Pinned by: `tests/unit/test_questions.py::test_failed_parse_becomes_a_question_and_the_answer_is_the_read`

#### Scenario: Enrichment and seed questions
- **WHEN** no source returns an abstract for two corpus records or for a seed paper
- **THEN** enrichment reports two questions offering `doi`, `abstract` or `leave`, and the seed paper gets a question offering `doi` or `leave`
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

### Requirement: No duplicate open questions
The system SHALL NOT store a new question while one with the same kind and key is open (run, record and pass for `screen_failed`; protocol and record for `screen_split`; the record, seed paper or artifact kind for the others). Enrichment raises at most 50 questions per run.
Surfaces: engine only.

#### Scenario: Re-running a failing pass does not ask twice
- **WHEN** a pass whose answers failed for two records runs again and fails again for them
- **THEN** there are still exactly two open questions
Pinned by: `tests/unit/test_questions.py::test_failed_parse_becomes_a_question_and_the_answer_is_the_read`

### Requirement: Queue or ask, per project
The system SHALL keep the project's choice in `project.yaml` as `doubts: queue` (default; questions wait on the Questions page) or `doubts: ask` (each new question opens in a dialog while the run continues), and SHALL reject any other value.
Surfaces: CLI `wider questions mode [queue|ask]`; UI Questions page ("When a question comes up"), settings summary.

#### Scenario: Switching mode through the API and the CLI
- **WHEN** the mode is set to `sometimes`, then to `ask` through `POST /api/questions/mode`, then to `queue` with `wider questions mode queue`
- **THEN** `sometimes` is refused with HTTP 400, `ask` is saved in `project.yaml`, and the CLI prints "doubts: queue"
Pinned by: `tests/unit/test_questions.py::test_question_routes_mode_and_cli`

### Requirement: Ask dialog and open-question count
The system SHALL check for new questions every 10 seconds (every 3 seconds while a background job runs), show the open count in the sidebar, and in ask mode open a dialog for open questions newer than those already shown. "Later" SHALL leave every question open at that moment in the queue, so only newer ones open the dialog.
Surfaces: UI ask dialog, sidebar count (`GET /api/questions/summary`); no CLI.

#### Scenario: The Questions page and the ask dialog are present
- **WHEN** the UI shell is loaded
- **THEN** it contains the Questions page, the ask dialog, the sidebar count and the mode radio buttons
- **AND** the questions script opens a modal dialog, reads the summary route and posts answers
Pinned by: `tests/unit/test_questions.py::test_static_questions`

### Requirement: Valid answers only
The system SHALL accept an answer only for an open question, only with one of its options, and only with a value when the option needs one; a DOI SHALL start with `10.` after removing a `https://doi.org/` prefix and lower-casing. An unknown question returns HTTP 404; an invalid answer HTTP 400; both exit with status 1 on the CLI.
Surfaces: CLI `wider questions answer <id> <choice> [--value] [--by]`; UI Questions page, ask dialog.

#### Scenario: Missing and malformed values are refused
- **WHEN** a `doi` answer is given without a value, then with "not a doi"
- **THEN** the first is refused as needing a value and the second as not a DOI
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

#### Scenario: A closed question cannot be answered again
- **WHEN** an answered question is answered again
- **THEN** the answer is refused with "already answered"
Pinned by: `tests/unit/test_questions.py::test_failed_parse_becomes_a_question_and_the_answer_is_the_read`

### Requirement: Unusable screening answer resolved by the researcher
The system SHALL store an include, maybe or exclude answer to `screen_failed` as that pass's successful read of the record, marked as the researcher's, append it to the run's `responses.jsonl` and update the run's count, so later protocol votes and replays use it. A `retry` answer SHALL change nothing and leave the record for the next run of the pass.
Surfaces: CLI `wider questions answer`; UI Questions page, ask dialog.

#### Scenario: The answer becomes the pass's read
- **WHEN** one failed record is answered `include` and the other `retry`, and the pass runs again
- **THEN** the first record's read in that pass is successful, include and marked as the researcher's
- **AND** the rerun calls the model only for the retry record
Pinned by: `tests/unit/test_questions.py::test_failed_parse_becomes_a_question_and_the_answer_is_the_read`

### Requirement: Three-way split decided by the researcher
The system SHALL record `maybe` for a record whose three protocol reads all differ and ask a `screen_split` question in the result set being screened; the answer SHALL become that set's decision with the vote `researcher:<choice>` and the reason "researcher (<name>) decided after three disagreeing reads", and a rerun in that set raises no new question.
Surfaces: CLI `wider screen protocol`, `wider questions answer`; UI LLM screening page, Questions page.

#### Scenario: The researcher's decision survives a rerun
- **WHEN** every record gets three different reads, one split question is answered `exclude` by "ana", and the protocol runs again
- **THEN** the decision for that record is exclude, its votes include `researcher:exclude` and its reason starts with "researcher (ana)"
- **AND** no open question remains for that record
Pinned by: `tests/unit/test_questions.py::test_three_way_split_asks_and_the_answer_survives_reruns`

#### Scenario: A split answer stays in its set
- **WHEN** `main` has set 1 and set 2, and a split question raised while set 2 was screened is answered `include`
- **THEN** set 2's decision for that record is include with the vote `researcher:include`, and set 1's decision for it is unchanged
Pinned by: `tests/unit/test_result_sets.py::test_split_answer_stays_in_its_set`

### Requirement: Reusing a split answer from another set
The system SHALL show, for a split in another set of the same protocol with the same codebook hash and screening rules on a record the researcher already answered, the earlier answer and offer to reuse it. It is never applied without that choice, and the earlier answer is never changed.
Surfaces: CLI `wider questions answer <id> reuse`; UI Questions page, button "Reuse this answer".

#### Scenario: Reusing an earlier answer
- **WHEN** "ana" answered a split on record 3 in `main` set 1 with exclude, set 2 (same codebook and rules) splits on record 3, and the question is answered with `reuse` through the CLI, and the same is done through the Questions page button "Reuse this answer" on a second synthetic project
- **THEN** the question shows "Your earlier answer in set 1: exclude" before the answer, and set 2's vote becomes `researcher:reused:set1:<question id>` with decision exclude
- **AND** the history names the author of the reuse, the decision's reason credits the earlier answer to the person who gave it ("researcher (bob) reused the answer ana gave in set 1" when bob reuses ana's answer), set 1 is unchanged, and `reuse` on a question without an earlier answer is refused as an invalid answer
Pinned by: `tests/unit/test_result_sets.py::test_reuse_earlier_answer`

### Requirement: Missing abstracts corrected by the researcher
The system SHALL apply a corrected DOI to the record and clear its `exhausted` enrichment entries so the next enrichment run tries again, and SHALL store a pasted abstract with source `researcher`, its word count and the thin flag recomputed from `protocol.corpus.thin_words`, logged as `filled`. `leave` changes nothing.
Surfaces: CLI `wider questions answer <id> doi|abstract --value ...`; UI Questions page, ask dialog.

#### Scenario: DOI and abstract answers
- **WHEN** one record's question is answered with "https://doi.org/10.1000/Corrected" and another's with a 40-word abstract
- **THEN** the first record's DOI is `10.1000/corrected`
- **AND** the second has 40 words from source `researcher` and is not thin
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

### Requirement: Seed DOIs corrected by the researcher
The system SHALL write a corrected DOI for a `seed_unresolved` question into `benchmark.csv` and synchronise the store, and SHALL fail when the seed paper is no longer in `benchmark.csv`.
Surfaces: CLI `wider questions answer <id> doi --value ...`; UI Questions page, ask dialog.

#### Scenario: A seed DOI answer reaches benchmark.csv
- **WHEN** a seed paper's question is answered with "10.1000/right"
- **THEN** `benchmark.csv` holds the DOI "10.1000/right"
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

### Requirement: Clarifications for construct proposals
The system SHALL store a `clarify` answer to `proposal_failed` as an active clarification with scope `construct`, and every later construct proposal prompt SHALL list the active clarifications under "Clarifications from the researcher". Answers SHALL NOT change screening prompts.
Surfaces: CLI `wider questions answer <id> clarify --value <text>`, `wider construct propose`; UI Questions page, Construct workbench.

#### Scenario: A clarification reaches the next proposal prompt
- **WHEN** a cells proposal fails, its question is answered with a clarification, and the cells prompt is built again
- **THEN** the prompt contains "Clarifications from the researcher" and the clarification text, and counts one clarification
Pinned by: `tests/unit/test_questions.py::test_failed_proposal_asks_for_a_clarification_that_later_prompts_show`

### Requirement: Listing and dismissing questions
The system SHALL list open questions oldest first, or all questions newest first, with counts and the mode, and SHALL let the researcher dismiss an open question without changing any record or setting; the dismissal itself is recorded in the history. An answer SHALL keep who gave it, when, and what it changed.
Surfaces: CLI `wider questions list [--all]`, `wider questions dismiss <id>`; UI Questions page ("Answered and dismissed").

#### Scenario: Routes and CLI over the same questions
- **WHEN** a question is answered `leave` through the API and another is dismissed
- **THEN** the first is answered with "left as it is" and the second is dismissed
- **AND** `wider questions list` prints the mode and "clarify <value>", and a CLI `clarify` answer without a value exits with status 1
Pinned by: `tests/unit/test_questions.py::test_question_routes_mode_and_cli`

#### Scenario: Dismissal is recorded but changes nothing else
- **WHEN** an open question is dismissed
- **THEN** no record, label or setting changes, and the history gains one `dismiss` change

#### Scenario: Answers keep who gave them
- **WHEN** a question is answered on the CLI without `--by`
- **THEN** the answer records `$WIDER_REVIEWER`, else `$USER`, as its author, with the time and what it changed
- **AND** an answer given in the UI records the reviewer name remembered in the browser

### Requirement: Answers are journalled
The system SHALL record each answer, with everything it changed, as one `answer` change in the history, and the UI SHALL offer Undo in the message that confirms the answer. Undoing an answer reopens the question.
Surfaces: CLI `wider history list --kind answer`, `wider history undo`; UI Questions page message, History page.

#### Scenario: Undoing an answer reopens the question
- **WHEN** a three-way split question is answered `exclude` and the last change is undone
- **THEN** the protocol decision is maybe again and the question is open
Pinned by: `tests/unit/test_history.py::test_answers_undo_including_screening_log_and_replay`

### Requirement: Refinement questions use the questions store and page
The system SHALL store the ten refinement kinds (`refine_*`, stage `query`) in the same questions store as the five kinds of doubt and apply to them the same listing, dedupe, queue-or-ask mode, valid-answer and journalling rules, so the Questions page, the ask dialog and `wider questions` work on them unchanged.
Surfaces: CLI `wider questions list|answer|dismiss`; UI Questions page, ask dialog.

#### Scenario: Refinement questions on the same page
- **WHEN** a `refine_noise_term` question is open
- **THEN** `wider questions list` shows it with stage `query`, the Questions page lists it with its evidence and default, and in `ask` mode it opens in the same dialog
- **AND** an answer with an option it does not offer is refused with HTTP 400
Pinned by: `tests/unit/test_interview.py::test_refinement_questions_use_the_questions_page`
