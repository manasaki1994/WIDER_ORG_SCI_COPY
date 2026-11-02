# Manual Review Specification

## Purpose
The human review of screening results that need a person: the records the LLM passes left unresolved or uncertain,
and quality-control samples of records the LLM settled, shown with each pass's read, why the record is listed and the
criteria, decided and signed by a researcher, undoable, and reported as researcher interventions.

## Requirements

### Requirement: Records that need a person
The system SHALL list, for one protocol and set (default: the Screening card's protocol, else "main"; its active set), one row per record that needs a person: an open split question, a failed read (linked to Questions, winning over its maybe), a final maybe or a QC record. Show adds third-read majorities, QC records only, or all; it counts the undecided.
Surfaces: CLI `wider review list [PROTOCOL] [--set N] [--show flagged|majority|qc|all] [--json]`; UI Screening review page, its selects.

#### Scenario: Flagged records of a synthetic set
- **WHEN** a synthetic set of 10 records has one open split question, one failed read in one of its runs, two other final maybes and two records settled by the third read
- **THEN** `wider review list` lists 4 records (the split, the failed read, the two maybes), each with its route, and says "4 records need a person, 4 undecided"
- **AND** `--show majority` lists 6 and `--show all` lists 10, and `--json` holds the same rows as the page's list
Pinned by: `tests/unit/test_review.py::test_review_list_flagged_majority_all`, `tests/unit/test_review_js_node.py::test_review_list_and_filter`

#### Scenario: Nothing needs a person
- **WHEN** every record of the set was settled by the passes and "ana" opens the review page
- **THEN** the page asks for the flagged records first, then lists every record of the set (Show "all") and says "No record needs a person, so every record of this set is listed: " before the count, rather than an empty list
- **AND** `wider review list` still says "0 records need a person, 0 undecided"; `--show all` lists the same rows as the page
Pinned by: `tests/unit/test_review_js_node.py::test_review_lists_all_when_none_flagged`

#### Scenario: A failed read is answered on the Questions page
- **WHEN** record 5 of the set has a failed read and "ana" opens it on the review page, then runs `wider review decide 5 include --by ana`
- **THEN** the page shows "failed read: answer it on the Questions page" with a link to `#questions` and no decision buttons
- **AND** the CLI exits 1 with "record 5 has a failed read; answer its question on the Questions page (wider questions answer ID)", the route answers 409 with the same text, and nothing is written
Pinned by: `tests/unit/test_review.py::test_failed_read_is_linked_not_decided`, `tests/unit/test_review_js_node.py::test_failed_read_links_to_questions`

#### Scenario: A failed third read and its maybe are one row
- **WHEN** record 6's third read failed, so it has a final maybe row and an open failed-read question, and the list runs with `--show flagged` and with `--show all`
- **THEN** each lists record 6 once, as the failed read with the link to the Questions page and no decision buttons
- **AND** after "ana" answers its question on the Questions page, the row reads "failed read answered by ana; the next screening run of this set writes its decision" and still counts as undecided, and the methods sentence does not count record 6
- **AND** the page shows no decision buttons for it, and `wider review decide 6 include --by ana` exits 1 with "record 6: failed read answered by ana; the next screening run of this set writes its decision" (409 from the route), writing nothing
Pinned by: `tests/unit/test_review.py::test_failed_read_and_its_maybe_are_one_row`, `tests/unit/test_review.py::test_answered_failed_read_waits_for_the_next_run`

#### Scenario: Choosing the protocol and set
- **WHEN** the synthetic project has protocols "main" (sets 1 and 2, set 2 active) and "probe", and "ana" opens the review page, then chooses "main" and set 1
- **THEN** the page first shows "main" set 2, then the rows of set 1, the same rows as `wider review list main --set 1 --json`
- **AND** when the Screening page's "Where screening stands" card shows a round of "probe", the page, `wider review list` without PROTOCOL and the pending count all start on "probe" and its active set
Pinned by: `tests/unit/test_review_js_node.py::test_review_protocol_and_set_selects`, `tests/unit/test_review.py::test_review_list_default_protocol_and_set`

#### Scenario: Nothing screened
- **WHEN** the project has no screening decisions
- **THEN** the CLI prints "no screening decisions yet" and exits 0, and the page says the same in one sentence naming the Screening step
Pinned by: `tests/unit/test_review.py::test_review_list_without_decisions`, `tests/unit/test_review_js_node.py::test_review_empty`

### Requirement: The review page says it shows the LLM's reads
The system SHALL say once above the review list, in normal black text, "This page shows the AI model's reads of each record. If you still label a validation sample, label it first: these reads could sway your labels." The page is an owner view; the blind labelling queue is unchanged.
Surfaces: UI Screening review page; CLI `wider review list` (the same sentence as its first line; not in `--json`).

#### Scenario: The sentence on the page and the command line
- **WHEN** a synthetic set has records that need a person and "ana" opens the review page, then runs `wider review list`
- **THEN** the page shows the sentence once, in black and at normal size, above the list
- **AND** the CLI prints the same sentence as its first line, and `wider review list --json` holds only the rows
Pinned by: `tests/unit/test_review_js_node.py::test_review_page_says_it_shows_llm_reads`, `tests/unit/test_review.py::test_review_list_first_line_says_it_shows_llm_reads`

### Requirement: Record view with each pass's read
The system SHALL show a listed record's title, abstract, each pass's read (decision, reason, model or "researcher"), the third read when there is one, the LLM outcome (an imported row's earlier decision), and the final decision with who reached it. It SHALL show no sample label, band, rank, score or sample membership.
Surfaces: CLI `wider review show RECORD [PROTOCOL] [--set N] [--json]`; UI Screening review page, docked drawer.

#### Scenario: A split record
- **WHEN** record 7 of the synthetic set had reads include (pass A), exclude (pass B) and maybe (third read)
- **THEN** `wider review show 7` and the drawer list the three reads with their reasons and models and the final decision "no majority (left at maybe)"
- **AND** neither output has a band, pool, rank, weight, sample id or sample name field, nor a label given in a labelling sample, even when record 7 is also in the development sample (the synthetic reasons do not contain those words)
Pinned by: `tests/unit/test_review.py::test_review_show_reads_and_no_sample_information`, `tests/unit/test_review_js_node.py::test_review_drawer_shows_reads_only`

#### Scenario: A record without stored pass reads
- **WHEN** record 8 of the set was imported or carried from an older project and has no stored pass reads
- **THEN** `wider review show 8` and the drawer show its final decision and "no pass reads were stored for this record (imported or older project)"
Pinned by: `tests/unit/test_review.py::test_review_show_without_pass_reads`, `tests/unit/test_review_js_node.py::test_review_drawer_without_pass_reads`

#### Scenario: A decided record shows the LLM outcome and the final decision
- **WHEN** record 3's passes left it at maybe and "ana" decided it exclude
- **THEN** `wider review show 3` and the drawer show "AI model outcome: maybe" and "Final: exclude (researcher ana)", and both pass reads unchanged
- **AND** `--json` holds `llm_outcome` "maybe" and `final` "exclude"
Pinned by: `tests/unit/test_review.py::test_review_show_llm_outcome_and_final`, `tests/unit/test_review_js_node.py::test_review_drawer_llm_outcome_and_final`

#### Scenario: An imported record decided on the review page
- **WHEN** record 8 was imported with the decision exclude and no pass reads, and "ana" decides it include, then maybe
- **THEN** `wider review show 8` and the drawer show "Earlier decision (imported): exclude" in place of "AI model outcome" and "Final: maybe (researcher ana)"
- **AND** the methods sentence counts record 8 as changed, and undoing both decisions makes it exclude again
Pinned by: `tests/unit/test_review.py::test_imported_row_keeps_its_earlier_decision`

#### Scenario: A pass read given by a researcher
- **WHEN** record 4's pass B failed, "bo" answered that failed read exclude on the Questions page, and the next run left record 4 at maybe
- **THEN** `wider review show 4` and the drawer label pass B "researcher (answered failed read)" and say "Outcome of the passes' reads (one given by a researcher): maybe"
Pinned by: `tests/unit/test_review.py::test_answered_failed_read_is_labelled`, `tests/unit/test_review_js_node.py::test_review_drawer_answered_failed_read`

#### Scenario: Keys on the page
- **WHEN** the list has focus on its first record and the reviewer presses j, then 1
- **THEN** the second record opens, it is decided include, and the next undecided record opens
- **AND** 2 decides exclude and 3 maybe, the same keys as the Labelling queue
- **AND** pressing 1, 2 or 3 while typing in the reason field types the digit and decides nothing
Pinned by: `tests/unit/test_review_js_node.py::test_review_keys_move_and_decide`

### Requirement: Review decisions are signed and written in place
The system SHALL write a review decision into the set in place, decided by the researcher, keeping the passes' reads in its votes (never overwritten), the row's origin, and the reviewer's name and note in its reason. A record with an open split question is decided by answering it. A decision needs a name and one of include, exclude, maybe.
Surfaces: CLI `wider review decide RECORD include|exclude|maybe --by NAME [--reason TEXT] [PROTOCOL] [--set N]`; UI drawer buttons and keys 1/2/3.

#### Scenario: Deciding a maybe and a split
- **WHEN** "ana" decides record 3 (final maybe, no open question) exclude and record 7 (open split question) include
- **THEN** both decisions read "decided by the researcher" with "researcher (ana)" in the reason and the passes' reads still in the votes, and the split question of record 7 is answered by "ana"
- **AND** History holds one `review_decision` for record 3, summarised "review decision on record 3: exclude (was maybe)", and one `answer` for record 7, both by "ana"
Pinned by: `tests/unit/test_review.py::test_review_decide_in_place_and_through_the_question`

#### Scenario: A carried decision keeps its origin
- **WHEN** record 9's maybe was carried into the set from another run and "ana" decides it include
- **THEN** the row keeps its origin, and after undoing her change the row is maybe again with the same origin
Pinned by: `tests/unit/test_review.py::test_review_decision_keeps_and_restores_the_origin`

#### Scenario: Refused decisions
- **WHEN** `wider review decide` runs without `--by`, with decision "keep", or for record 999 that is not in the set
- **THEN** it prints "a reviewer name is needed", "decision must be include, exclude or maybe" or "record 999 is not in set <set>" and exits 2, 2 or 1, and the route returns 400, 400 or 404
- **AND** nothing is written and History is unchanged
Pinned by: `tests/unit/test_review.py::test_review_decide_refusals`

#### Scenario: The page says who signs
- **WHEN** "ana" opens the review page with her reviewer name set, and later with no name set
- **THEN** the page shows "Signing as ana" once above the decision buttons
- **AND** without a name it shows "no name yet: decisions need your name" in black at normal size, and the decision buttons, keys 1/2/3 and the quality-control dialog decide and draw nothing
Pinned by: `tests/unit/test_review_js_node.py::test_review_page_says_who_signs`

#### Scenario: A decision made while a screening run is going
- **WHEN** a screening run of the set has read the researcher rows and "ana" decides record 3 exclude before the run writes record 3
- **THEN** the run keeps ana's decision, votes and reason when it writes the set
Pinned by: `tests/unit/test_screen.py::test_run_keeps_a_review_decision_made_during_the_run`

### Requirement: Review decisions can be undone
The system SHALL undo a review decision from History or from the page's Undo offer, restoring the decision, votes, reason, route and origin it replaced. When a later change decided the record again, the undo SHALL be refused with the journal's "changed since; undo the later change first" and change nothing.
Surfaces: CLI `wider history undo ID`; UI History page, Undo; the toast's Undo after a decision on the Screening review page.

#### Scenario: Undo restores the AI model outcome
- **WHEN** "ana" decided record 3 exclude and then undoes that change, once from History and once from the toast's Undo
- **THEN** record 3 is maybe again with its earlier votes, reason and route, and the review list counts it undecided
Pinned by: `tests/unit/test_review.py::test_review_decision_undo_restores_the_llm_outcome`, `tests/unit/test_review_js_node.py::test_review_toast_undo`

#### Scenario: A later decision on the same record
- **WHEN** "ana" decided record 3 exclude, then "bo" decided it include, and ana undoes her change
- **THEN** the undo is refused with a message ending "changed since; undo the later change first", and record 3 keeps bo's include
- **AND** record 3's votes hold the passes' reads and one researcher vote, bo's
- **AND** a later screening run of the set keeps bo's decision, since a run leaves a researcher's decision as it is
Pinned by: `tests/unit/test_review.py::test_review_decision_undo_refused_after_a_later_decision`

### Requirement: Review decisions in the report and methods
The system SHALL list each review decision under the report's "Researcher interventions" like an answered question, and the methods draft SHALL add one sentence: how many records of the set a researcher decided after screening and how many differ from the LLM passes' outcome, and one per quality-control sample: drawn, pool, seed, checked, changed. With none, those sentences are left out.
Surfaces: CLI `wider report`, `wider export methods`; UI Recall page, "Build report" and the methods card.

#### Scenario: Two review decisions
- **WHEN** "ana" decided records 3 and 7 and the report and methods draft are built
- **THEN** "Researcher interventions" lists both `review_decision` and answer entries with date and "ana"
- **AND** the methods draft says "A researcher decided 2 records after screening; 2 of them changed the LLM outcome."
- **AND** a project without review decisions or answers has no such sentence

#### Scenario: A quality-control sample in the methods
- **WHEN** "ana" drew a quality-control sample of 5 records with seed 7, decided 3 of them and changed 1
- **THEN** "Researcher interventions" lists the draw with date and "ana"
- **AND** the methods draft says "A quality-control sample of 5 of 32 records the LLM settled was drawn at random (seed 7); a researcher checked 3 and changed 1."
Pinned by: `tests/unit/test_review.py::test_review_decisions_in_report_and_methods`, `tests/unit/test_review_qc.py::test_qc_sample_in_report_and_methods`

### Requirement: Each listed record says why it needs a person
The system SHALL give each listed row one plain sentence saying why it is listed: "the three reads disagree", "a pass failed to read it", "the final decision is maybe", "settled by the third read", "quality-control sample: the AI model settled it as include" (or exclude), or else the route's words used on the dashboard. `--json` holds it as `why`.
Surfaces: CLI `wider review list`, `wider review show`; UI Screening review page, list rows and drawer.

#### Scenario: Why each record of a synthetic set is listed
- **WHEN** the synthetic set of 10 records (one open split, one failed read, two other maybes, two third-read majorities) is listed with `--show majority`
- **THEN** each row carries one sentence: "the three reads disagree" for the split, "a pass failed to read it" for the failed read, "the final decision is maybe" for the two maybes and "settled by the third read" for the majorities
- **AND** the page's rows and drawer show the same sentences as `wider review list --show majority --json` holds in `why`
- **AND** with `--show all` a record both passes agreed on says "both passes agreed", the dashboard's words for that route
Pinned by: `tests/unit/test_review.py::test_review_rows_say_why`, `tests/unit/test_review_js_node.py::test_review_rows_and_drawer_say_why`

### Requirement: The criteria beside the records
The system SHALL show on the review page, once, in a panel the reviewer can fold, the criteria the set was screened with: every section the codebook puts in the screening prompt, of the version behind the set's prompt. When that version is not known (an imported or older set), it shows the current codebook and says so in black, at normal size.
Surfaces: CLI `wider review criteria [PROTOCOL] [--set N] [--json]`; UI Screening review page, Criteria panel.

#### Scenario: The criteria of the set's codebook version
- **WHEN** the synthetic set was screened with a prompt built from codebook version 2 and the project's current codebook is version 3
- **THEN** `wider review criteria` and the page's Criteria panel show version 2's question, target description, elements with their cells, checks, edge cases, exclusion reasons, what is not a criterion, adjacent literatures and the thin-abstract rule, leaving out empty sections, and say "codebook version 2, used by this set"
- **AND** the panel is shown once above the list and stays folded or open as the reviewer left it while records change
Pinned by: `tests/unit/test_review.py::test_review_criteria_of_the_set_s_codebook`, `tests/unit/test_review_js_node.py::test_review_criteria_panel`

#### Scenario: A set whose codebook version is not known
- **WHEN** the set's runs record no prompt version
- **THEN** both show the current codebook with "this set's codebook version is not recorded; showing the current codebook (version 3)" in black
Pinned by: `tests/unit/test_review.py::test_review_criteria_version_unknown`

### Requirement: Quality-control samples of settled records
The system SHALL draw a seeded random sample of N records the LLM settled, leaving out records a person decided, in a labelling sample or already listed. A draw stores its seed and pool, is signed, in History, undoable while none of its records has a review decision, and listed; the page says it is not a recall estimate.
Surfaces: CLI `wider review qc draw [PROTOCOL] [--set N] --n N [--seed S] --by NAME`; UI Screening review page, "Draw quality-control sample" dialog.

#### Scenario: Drawing a quality-control sample
- **WHEN** a synthetic set has 40 records the LLM settled, 6 of them in the development sample and 2 decided by a researcher, and "ana" runs `wider review qc draw --n 5 --seed 7 --by ana`
- **THEN** 5 records are drawn from the 32 others, the same 5 for the same seed and set, and `wider review list --show qc` lists them, each "quality-control sample: the AI model settled it as include" (or exclude), undecided
- **AND** History holds one `review_qc_draw` by "ana", summarised "quality-control sample of 5 records (seed 7)", and the draw stores the pool's size 32 and a sha256 of its sorted record ids
- **AND** the page's dialog (size and seed; it signs with the page's reviewer name) draws the same 5 and the page then says once, in black, "A quality-control sample checks the AI model's settled decisions; it is not a recall estimate."
Pinned by: `tests/unit/test_review_qc.py::test_qc_draw_seeded_and_leaves_out_sampled_and_decided`, `tests/unit/test_review_js_node.py::test_review_qc_dialog_and_sentence`

#### Scenario: Undoing a draw
- **WHEN** "ana" undoes the draw before any of its records has a review decision, and draws again and decides one of its records before undoing
- **THEN** the first undo removes the 5 records from the list
- **AND** the second is refused with "a record of this quality-control sample has a review decision; undo that decision first", changing nothing, from History and from `wider undo` alike (neither steps over it)
- **AND** after that decision is undone, the draw's undo succeeds
Pinned by: `tests/unit/test_review_qc.py::test_qc_draw_undo_and_refusal`

#### Scenario: Refused draws
- **WHEN** `wider review qc draw` runs with `--n 0`, without `--by`, or with `--n 50` when 32 records can be drawn
- **THEN** it prints "the sample size must be at least 1" (exit 1), "a reviewer name is needed" (exit 2), or "only 32 settled records can be drawn" (exit 1), the route answers 400 with the same text, and nothing is written
Pinned by: `tests/unit/test_review_qc.py::test_qc_draw_refusals`

### Requirement: Pending reviews shown on the Screening page and Home
The system SHALL show "N records wait for a person" linking to the Screening review page when N > 0: on the Screening page's summary card and maybes step and the Home project card. N is the review list's undecided count for its default protocol and set, which the link opens. `wider screen status` and `wider info` print the same line.
Surfaces: CLI `wider screen status`, `wider info`; UI Screening page summary card and maybes step, Home page project card.

#### Scenario: Four records wait
- **WHEN** the synthetic set has 4 undecided records that need a person
- **THEN** the Screening page's summary card and the Home card say "4 records wait for a person" with a link to the review page for that protocol and set, and the maybes step links to the same place
- **AND** `wider screen status` and `wider info` print "4 records wait for a person (Screening review, wider review list)"
- **AND** with no record waiting, none of them shows the line
Pinned by: `tests/unit/test_review.py::test_pending_reviews_in_screen_status_and_info`, `tests/unit/test_review_js_node.py::test_pending_reviews_link_on_screening_and_home`

### Requirement: A sample says how many of its records were reviewed with the LLM's reads
The system SHALL say on the owner's sample row, as a count only, how many records of a labelling sample were decided by a person who saw the AI model's reads (undone decisions not counted), so a validation label given after that can be weighed. The blind labelling queue shows nothing of it, and nothing is refused.
Surfaces: CLI `wider sample status [--json]`; UI Samples and labelling page, sample rows.

#### Scenario: Two sampled records were reviewed
- **WHEN** records 3 and 7 are in the validation sample and "ana" decided both on the review page
- **THEN** the validation sample's row and `wider sample status` say "2 records of this sample were decided by a person who saw the AI model's reads"
- **AND** after "ana" undoes her decision on record 3, the count is 1, and `GET /api/samples` and `wider sample status --json` hold `reviewed_with_llm_reads` 1
- **AND** the blind labelling queue's rows and `GET /api/labels/queue` carry no such field
Pinned by: `tests/unit/test_review.py::test_sample_counts_records_reviewed_with_llm_reads`, `tests/unit/test_samples_page_node.py::test_sample_row_counts_reviewed_records`
