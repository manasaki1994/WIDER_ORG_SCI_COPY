# interactive-demo Specification

## Purpose
Lets a visitor try the real interface on an invented project in a browser tab, with no install, no model and nothing sent anywhere: every page answers from a recorded snapshot, actions are simulated and marked as such, a guided tour explains each step, and the demo is labelled on every page so it is never mistaken for a real project.

## Requirements

### Requirement: Demo Mode is labelled on every page
The demo SHALL show above the app's header, on every page, a Demo Mode bar that cannot be closed, reading "Demo mode" and "Invented data, simulated results. Nothing you do here leaves this browser tab.", with Guided/Free, Reset and Exit. The tab title SHALL keep the prefix "Demo — " even when the app's job bar sets the title. Every result a simulated run produces SHALL carry the mark "(simulated)".
Surfaces: the demo page (`showcase/site/demo/index.html`); no CLI (a static showcase page).

#### Scenario: Bar on every page
- **WHEN** the demo opens on any page of the app (each `section[id^="page-"]` in `index.html`: Home, the stage pages, Questions, Notes and tags, History, Help, Settings)
- **THEN** the Demo Mode bar is visible at the top with the text above and its controls
- **AND** the browser tab title starts with "Demo — "
Pinned by: `tests/unit/test_demo_layer_node.py::test_bar_on_every_page`

#### Scenario: Title keeps the prefix during a job
- **WHEN** a simulated job runs and the app's job poll sets the title to "(1 running) WIDER"
- **THEN** the tab title reads "Demo — (1 running) WIDER"
Pinned by: `tests/unit/test_demo_layer_node.py::test_title_keeps_demo_prefix_after_job_poll`

#### Scenario: Simulated results are marked
- **WHEN** the visitor presses Rank in the demo
- **THEN** the stage result's headline ends with the mark "(simulated)"
Pinned by: `tests/unit/test_demo_actions_node.py::test_rank_run_result_is_marked_simulated`

### Requirement: The demo sends nothing out
The demo SHALL answer every request to `/api/` inside the browser and SHALL refuse every other request, including through `XMLHttpRequest`, `WebSocket`, `EventSource` and `navigator.sendBeacon`. The page SHALL carry a Content-Security-Policy with `default-src 'self'` and `connect-src 'none'` and load nothing from an outside address. A click on a link to an outside site SHALL not navigate and SHALL say links are off in the demo.
Surfaces: the demo page; `showcase/demo/demo.js` (request catcher).

#### Scenario: Outside address refused
- **WHEN** page code calls `fetch("https://example.org/x")` in the demo
- **THEN** the call fails with the error "Blocked in the demo: nothing leaves this page"
- **AND** the browser's own `fetch` is not called
Pinned by: `tests/unit/test_demo_catcher_node.py::test_outside_fetch_is_refused`

#### Scenario: Other channels refused
- **WHEN** page code opens an `XMLHttpRequest`, a `WebSocket` or an `EventSource`, or calls `navigator.sendBeacon`
- **THEN** each throws with the same message, or returns false for `sendBeacon`
Pinned by: `tests/unit/test_demo_catcher_node.py::test_other_channels_are_refused`

#### Scenario: Outside link
- **WHEN** the visitor clicks, middle-clicks or Cmd/Ctrl-clicks the DOI link in a record drawer
- **THEN** the browser stays on the demo page, no tab opens, and the demo shows "Links to outside sites are off in the demo (the papers and their DOIs are invented)."
- **AND** the link's element holds no outside address in its `href`
Pinned by: `tests/unit/test_demo_catcher_node.py::test_outside_links_are_caught`

#### Scenario: Policy in the built page
- **WHEN** the demo is built
- **THEN** `showcase/site/demo/index.html` has a `<meta http-equiv="Content-Security-Policy">` with `default-src 'self'` and `connect-src 'none'`
- **AND** no file of the bundle loads an outside address by itself (a `src`, a `<link href>`, a CSS `url()` or `@import`, a `fetch` or `import` string); an `<a href>` such as the drawer's DOI link and an XML namespace value such as `xmlns="http://www.w3.org/2000/svg"` are allowed
Pinned by: `tests/unit/test_demo_build.py::test_csp_and_no_outside_address`

#### Scenario: No request leaves the page during a walkthrough
- **WHEN** the demo is opened from a static server and the full guided tour is played through
- **THEN** the browser's network log shows requests only to the demo's own static files
Pinned by: NOT_TESTED in the gate (browser walkthrough, tasks 5.2 and 7.2; hand check M71)

### Requirement: Download links stay in the page
A click on a link to `/api/` SHALL be caught: a recorded file SHALL be saved from the page, any other SHALL show the not-in-demo notice. PRISMA SHALL save both recorded files; the methods text SHALL be saved in the page as the app does, with no link; the references export and the replication package SHALL show the not-in-demo notice. The demo SHALL keep the browser from asking for notification permission.
Surfaces: the demo's Recall page (exports) and job bar.

#### Scenario: Export link
- **WHEN** the visitor makes the PRISMA export on the Recall page and clicks its file link
- **THEN** the recorded file is saved from the page without a request
Pinned by: `tests/unit/test_demo_catcher_node.py::test_api_links_are_caught`

#### Scenario: Replication package
- **WHEN** the visitor asks for the replication package on the Recall page
- **THEN** the page shows its error box with the reason "This export is not part of the demo. In WIDER it writes a file into your project folder." and nothing is saved
Pinned by: `tests/unit/test_demo_catcher_node.py::test_unsimulated_write_shows_reason`, `tests/unit/test_demo_coverage.py::test_exports_name_why_they_are_not_in_the_demo`

#### Scenario: No notification prompt
- **WHEN** a simulated job runs
- **THEN** no browser notification is shown and `Notification.requestPermission` is never called
Pinned by: `tests/unit/test_demo_catcher_node.py::test_notifications_are_off`

### Requirement: The demo answers from a recorded snapshot of the real app
The demo SHALL answer `GET /api/...` from a snapshot recorded by running the real WIDER UI on the invented project at each checkpoint, with a fresh WIDER home and the reviewer "Reviewer A", matching the exact path and query, then the path with sorted query parameters, then a pattern in `showcase/demo/coverage.json`. A request it cannot match SHALL get HTTP 404 with `{"detail": "Not part of the demo"}`.
Surfaces: the demo page; `showcase/demo/record_snapshot.py`; `showcase/demo/coverage.json`.

#### Scenario: Recorded answer
- **WHEN** the demo is at checkpoint "ranked" and the Ranking page asks for a request recorded at that checkpoint
- **THEN** the answer is the recorded body for that request at that checkpoint
Pinned by: `tests/unit/test_demo_catcher_node.py::test_get_answers_from_snapshot`

#### Scenario: Unknown request
- **WHEN** page code asks for `/api/not-a-route`
- **THEN** the answer has status 404 and the detail "Not part of the demo"
Pinned by: `tests/unit/test_demo_catcher_node.py::test_unknown_route_is_not_part_of_the_demo`

#### Scenario: Home lists only the demo project
- **WHEN** the recorded Home page answers are checked
- **THEN** they list one project, the demo's, at `~/reviews/employee-voice`
Pinned by: `tests/unit/test_demo_snapshot.py::test_home_lists_only_demo_project`

### Requirement: Every interface request is covered before a build
`showcase/demo/coverage.py` SHALL list each method and `/api/` path that `wider/ui/static/*.js` can call (parameters normalised) with no recorded answer, simulated action or not-in-demo entry in `coverage.json`; a not-in-demo entry SHALL give the reason the visitor sees. The deploy build SHALL refuse while the list is not empty; the gate's other build tests SHALL not depend on it.
Surfaces: `showcase/demo/coverage.py`; `showcase/demo/build_demo.py`.

#### Scenario: A new route in the app
- **WHEN** a file in `wider/ui/static/` gains a call to `/api/new/thing` and the demo is not updated
- **THEN** the coverage list names `GET /api/new/thing`
Pinned by: `tests/unit/test_demo_coverage.py::test_coverage_lists_uncovered_paths`

#### Scenario: A write to a recorded path
- **WHEN** the coverage list is computed for the app's current files
- **THEN** `POST /api/outputs/methods` is covered by its own entry, not by the recorded `GET /api/outputs/methods`
Pinned by: `tests/unit/test_demo_coverage.py::test_coverage_is_keyed_by_method`

#### Scenario: The build refuses an uncovered path
- **WHEN** the demo is built while `/api/new/thing` is uncovered
- **THEN** the build stops with an error naming `/api/new/thing` and writes no bundle
Pinned by: `tests/unit/test_demo_build.py::test_build_refuses_uncovered_path`

#### Scenario: Write actions without a simulation
- **WHEN** the visitor uses a write action the demo does not simulate (for example uploading a file or deleting a project)
- **THEN** the page shows the app's own error box with the reason from `coverage.json`, for example "Uploading files is not part of the demo: nothing leaves your browser."
- **AND** nothing in the demo's state changes
Pinned by: `tests/unit/test_demo_catcher_node.py::test_unsimulated_write_shows_reason`

### Requirement: The invented project is coherent and readable
The demo project SHALL be built only from `showcase/demo/papers.yaml` (invented papers with abstracts, a reference decision each, a reason for each borderline one), the hand-written `synonyms.yaml` and the example made from them, run through the real pipeline with every change signed "Reviewer A", so that the counts on every page agree within a checkpoint.
Surfaces: `showcase/demo/make_demo_example.py`, `showcase/build_project.py --example` (developer scripts).

#### Scenario: Counts agree across pages
- **WHEN** the snapshot is checked at each recorded checkpoint from "ranked" on
- **THEN** the records remaining after deduplication equal the corpus size on the Ranking page
- **AND** at "screened" they also equal the screened scope on the Screening page
Pinned by: `tests/unit/test_demo_snapshot.py::test_counts_agree_across_pages`

#### Scenario: Checkpoints agree with each other
- **WHEN** the snapshot is checked at checkpoints "sampled" and "screened"
- **THEN** the rank order and the samples are the same at both
Pinned by: `tests/unit/test_demo_snapshot.py::test_sampled_matches_screened`

#### Scenario: Labels follow the reference decisions
- **WHEN** `make_demo_example.py` writes the example into a temporary folder
- **THEN** `reference_labels.csv` has one row per paper of `papers.yaml` with its decision, and the example holds `synonyms.yaml` and `benchmark.csv`
Pinned by: `tests/unit/test_demo_example.py::test_reference_labels_match_papers`

#### Scenario: Every change is signed Reviewer A
- **WHEN** the recorded History answers are checked at every checkpoint
- **THEN** every author is "Reviewer A" and no entry names the build computer's user
Pinned by: `tests/unit/test_demo_snapshot.py::test_history_authors_are_reviewer_a`

#### Scenario: The second setting is offered at "screened"
- **WHEN** the recorded model list at checkpoint "screened" is checked
- **THEN** it lists `openai-t07` with its consent given, and set 2 does not exist yet
Pinned by: `tests/unit/test_demo_snapshot.py::test_screened_offers_second_setting`

### Requirement: The demo's state survives a reload and can be reset
The demo SHALL keep its state (checkpoint, labels, pair decisions, term edits, mode, tour step, page, running job) in `sessionStorage` under one key, restore it on reload, and run in memory when storage is unavailable. The app's own `localStorage` keys SHALL be kept for the tab only. Reset SHALL ask for confirmation, then clear that state and the app's keys and reopen the start card.
Surfaces: the Reset button in the Demo Mode bar.

#### Scenario: Reload keeps the place
- **WHEN** the visitor has labelled two records and reloads the page
- **THEN** the same page opens with the two labels and the counters as before
Pinned by: `tests/unit/test_demo_state_node.py::test_reload_restores_state`

#### Scenario: Reload during a run
- **WHEN** a simulated screening job is halfway and the visitor reloads the page
- **THEN** the job bar shows the job again at about the same progress, and it finishes as it would have
Pinned by: `tests/unit/test_demo_screening_node.py::test_reload_during_job_resumes_it`

#### Scenario: Reset
- **WHEN** the visitor presses Reset and confirms "Start the demo again? Everything you did in the demo is cleared."
- **THEN** the labels, pair decisions and search-term edits are gone, the checkpoint is the first one, and the start card is shown
Pinned by: `tests/unit/test_demo_state_node.py::test_reset_clears_state`

#### Scenario: Storage blocked
- **WHEN** `sessionStorage` throws on access
- **THEN** the demo opens at the start card and every action still works
Pinned by: `tests/unit/test_demo_state_node.py::test_runs_without_storage`

#### Scenario: App keys do not persist
- **WHEN** the app's code writes `wider.lastPage.<key>` during the demo
- **THEN** the browser's real `localStorage` holds no `wider.` key
Pinned by: `tests/unit/test_demo_state_node.py::test_app_keys_do_not_persist`

### Requirement: Labelling updates the counters at once
In the demo, the visitor SHALL work as "Reviewer A", whatever name is typed. An Include, Exclude or Maybe label given on the Samples page or in the record drawer SHALL be kept in the demo's state, and every counter the app shows for that sample SHALL reflect it on the next request. The app's Undo SHALL remove a label or pair decision made in the demo. The recorded reference labels SHALL never change.
Surfaces: the demo's Samples page and record drawer (the app's own controls).

#### Scenario: Label a record
- **WHEN** at checkpoint "sampled" the development sample shows 6 records left to label and the visitor labels one Include
- **THEN** the counters show 5 left to label and one more include
Pinned by: `tests/unit/test_demo_actions_node.py::test_label_updates_counters`

#### Scenario: Change a label
- **WHEN** the visitor changes that record from Include to Maybe
- **THEN** include goes down by one, maybe up by one, and 5 are still left to label
Pinned by: `tests/unit/test_demo_actions_node.py::test_relabel_moves_count`

#### Scenario: The visitor types another name
- **WHEN** the demo opens the Samples page, and later the visitor replaces the reviewer name "Reviewer A" with their own name
- **THEN** the page first loads without asking for a name, and after the change it still shows the 6 records left to label
Pinned by: `tests/unit/test_demo_state_node.py::test_reviewer_preset_and_any_name_answers`

#### Scenario: Undo a label in the demo
- **WHEN** the visitor labels a record Include and presses Undo in the app's "Label saved" message
- **THEN** the record is unlabelled again and the counters are as before the label
Pinned by: `tests/unit/test_demo_actions_node.py::test_undo_removes_overlay_change`

#### Scenario: Redo after Undo
- **WHEN** the visitor presses Redo in the app's "Undone" message
- **THEN** the label is back, the counters are as after the label, and the app's message reads "Redone" with an Undo button
Pinned by: `tests/unit/test_demo_actions_node.py::test_redo_restores_change`

### Requirement: Deciding duplicate pairs updates the count
In the demo, deciding a pair on the Deduplication page SHALL be kept in the demo's state and shown as the app shows it: the pair stays listed as decided and the count is unchanged. "Apply decisions and check again" SHALL then update the unique-record count and the PRISMA rows: one fewer unique record for each pair decided as the same paper.
Surfaces: the demo's Deduplication page (the app's own controls).

#### Scenario: Merge one pair
- **WHEN** at checkpoint "imported" the page shows 50 unique records and 11 pairs to decide and the visitor decides one pair as the same paper
- **THEN** the pair list reads "10 open, 1 decided" and the page still shows 50 unique records
- **AND** after the visitor presses "Apply decisions and check again" the page shows 49 unique records
Pinned by: `tests/unit/test_demo_actions_node.py::test_dedup_merge_updates_count`

#### Scenario: Last pair decided
- **WHEN** every pair is decided as the same paper and the visitor presses "Apply decisions and check again"
- **THEN** the PRISMA rows end with 39 records remaining for screening, as the app shows them at checkpoint "ranked"
Pinned by: `tests/unit/test_demo_actions_node.py::test_last_pair_makes_prisma_final`

#### Scenario: A pair decided as different papers
- **WHEN** the visitor decides one pair as not the same paper and presses "Apply decisions and check again"
- **THEN** the page still shows 50 unique records and 10 pairs open
Pinned by: `tests/unit/test_demo_actions_node.py::test_not_same_paper_changes_no_count`

### Requirement: Changing search terms shows the effect on retrieval
On the Search strings page the demo SHALL add a panel "Demo: try other search terms", saying that in WIDER terms are changed on the Concepts page. Adding or removing a term SHALL show at once how many of the 39 demo records and of the 19 include papers the terms find, counted in the browser, with a note that these are invented records and WIDER never runs the database search. The real page SHALL stay as recorded.
Surfaces: the demo's Search strings page (terms panel).

#### Scenario: Remove a term
- **WHEN** the visitor removes the term "suggestion*"
- **THEN** records found fall from 20 to 15, include papers found from 14 to 11, and the newly missed include papers P09, P16 and P23 are listed first, then the other newly missed papers P17 and P18
Pinned by: `tests/unit/test_demo_actions_node.py::test_removing_a_term_lowers_retrieval`

#### Scenario: Add a broad term
- **WHEN** the visitor adds the term "team*" to the block "Speaking up or staying silent"
- **THEN** records found rise from 20 to 22 and both newly found records are papers the reference labels exclude
- **AND** the note reads "Counted on the demo's invented records only. A real database search finds different numbers; WIDER never runs your database search for you."
Pinned by: `tests/unit/test_demo_actions_node.py::test_broad_term_adds_noise`

#### Scenario: A term that finds missed papers
- **WHEN** the visitor adds the term "employees" to the block "Speaking up or staying silent"
- **THEN** records found rise from 20 to 23 and P03, P05 and P27 are listed as newly found include papers
Pinned by: `tests/unit/test_demo_actions_node.py::test_term_finds_missed_includes`

#### Scenario: Empty block
- **WHEN** the visitor removes the last term of a block
- **THEN** the panel shows 0 records found and "A block with no terms finds nothing: every block needs at least one term."
Pinned by: `tests/unit/test_demo_actions_node.py::test_empty_block_finds_nothing`

### Requirement: Simulated runs advance the project
In the demo, Rank and Draw samples SHALL answer after a few seconds, and Run screening (also as a new result set) SHALL start a job shown in the job bar with progress and no remote mark. A finished run moves the demo to the next checkpoint; Cancel leaves it. A run before its earlier stage names that stage, and screening with development records unlabelled shows the app's sentence; both change nothing.
Surfaces: the demo's Ranking and Screening pages and job bar.

#### Scenario: Rank
- **WHEN** at checkpoint "imported" the visitor presses Rank
- **THEN** after about 3 seconds the Ranking page shows the records with the ranks and scores recorded at checkpoint "ranked"
- **AND** the tour's next step names one record's rank as a share of all records, rounded up (for example "rank 4 of 39: in the top 11% by score"), marked as the demo's explanation
Pinned by: `tests/unit/test_demo_actions_node.py::test_rank_run_moves_to_ranked`, `tests/unit/test_demo_tour.py::test_percentile_step_uses_snapshot_rank`

#### Scenario: Draw samples
- **WHEN** at checkpoint "ranked" the visitor presses "Draw samples from the ranking"
- **THEN** the demo moves to checkpoint "sampled", the stage result ends with "(simulated)", the Samples page lists the development and validation samples, and a second draw is refused as already drawn
Pinned by: `tests/unit/test_demo_actions_node.py::test_draw_samples_moves_to_sampled`

#### Scenario: Cancel a run
- **WHEN** after labelling the open development records, the visitor cancels a simulated screening job halfway
- **THEN** the demo stays at checkpoint "sampled" and the Screening page shows no new set
Pinned by: `tests/unit/test_demo_screening_node.py::test_cancel_keeps_checkpoint_and_a_restart_resumes`

#### Scenario: Run before its step
- **WHEN** at checkpoint "imported" the visitor presses Run screening
- **THEN** the page shows "Rank the records first (Ranking page)." and the demo state does not change
Pinned by: `tests/unit/test_demo_actions_node.py::test_run_out_of_order_names_earlier_stage`, `tests/unit/test_demo_screening_node.py::test_refusals_and_already_done`

#### Scenario: The visitor decided differently
- **WHEN** the visitor decided two duplicate pairs differently from Reviewer A, left 9 open, and then presses Rank
- **THEN** the result says "You decided 2 pairs differently from Reviewer A.", "The 9 pairs you left open were decided as Reviewer A decided them." and "The demo continues with Reviewer A's decisions so the numbers on every page agree; in WIDER your own decisions would be used."
Pinned by: `tests/unit/test_demo_actions_node.py::test_differing_choices_are_named`

#### Scenario: Screening waits for the development labels
- **WHEN** at checkpoint "sampled" with 6 development records left to label the visitor presses Run screening
- **THEN** the page shows "the development sample has 6 records without a final label; label them all first" with "Continue labelling", and the demo state does not change
- **AND** after the visitor labels the 6, Run screening starts the simulated job
Pinned by: `tests/unit/test_demo_screening_node.py::test_screen_run_is_progressive_and_ends_at_set1`

### Requirement: Screening fills in step by step with disagreements
During a simulated screening job a ticker below the Screening page's Run row SHALL list the decisions as they arrive, one record at a time in the recorded order, each with read A, read B and, where they disagree, the third read; the app's pages SHALL show the results when the job ends.
Surfaces: the demo's job bar and ticker; the Screening page.

#### Scenario: Screening fills in
- **WHEN** at checkpoint "sampled" the visitor has labelled the open development records and runs screening
- **THEN** the ticker adds the decisions one record at a time in the recorded order, the job bar says "Simulated: the recorded decisions are replayed; no model is called and nothing costs anything." without a remote mark, and at the end the Screening page's counts equal the recorded set 1 counts
Pinned by: `tests/unit/test_demo_screening_node.py::test_screen_run_is_progressive_and_ends_at_set1`, `tests/unit/test_demo_screening_node.py::test_ticker_shows_the_newest_decisions_while_running`

#### Scenario: Second setting shows disagreements
- **WHEN** at checkpoint "screened" the visitor chooses the second setting, confirms the app's new-result-set dialog and the run ends
- **THEN** the ticker has marked each record where reads A and B disagreed with its third read, and comparing set 1 with set 2 lists the records whose final decision changed
Pinned by: `tests/unit/test_demo_screening_node.py::test_second_set_needs_a_new_set_then_shows_disagreements`

### Requirement: Recall is shown with its uncertainty and its limits
The demo's Recall page SHALL show the recorded recall estimate with its interval for each screened set. The tour SHALL explain, with these numbers, what the interval means and why recall is estimated on the validation sample, and SHALL say the numbers are illustrative: invented papers, no evidence about WIDER or any model on a real review.
Surfaces: the demo's Recall page; the tour's recall steps and the concept card "Recall and its uncertainty".

#### Scenario: Recall card
- **WHEN** at checkpoint "screened" the visitor presses Estimate recall
- **THEN** the page shows the recorded estimate and interval for set 1
- **AND** the concept card names the same numbers and the sentence "Illustrative: invented papers, not evidence about any method."
Pinned by: `tests/unit/test_demo_screening_node.py::test_recall_shows_recorded_estimate_and_what_a_point_interval_means`, `tests/unit/test_demo_tour.py::test_recall_card_uses_snapshot_numbers`

#### Scenario: The validation sample is locked after screening
- **WHEN** at checkpoint "screened" the visitor changes a label in the validation sample
- **THEN** the page asks for a correction reason in the app's own dialog, and on "Save correction" says "The demo keeps its recorded validation labels; in WIDER this saves a documented correction with your reason." and no counter changes
- **AND** a label in the development sample can still be changed
Pinned by: `tests/unit/test_demo_screening_node.py::test_label_on_locked_validation_asks_for_a_correction`

### Requirement: Guided and Free modes
The demo SHALL open with a start card offering "Guided tour" (recommended) and "Explore freely", one sentence each; below 900 pixels wide it SHALL add that the demo is made for a laptop or desktop screen. Guided SHALL start at the first checkpoint on Home with step 1. Free SHALL start at the last checkpoint, so every page has results, with a note that Reset returns to the beginning.
Surfaces: the start card.

#### Scenario: Start card
- **WHEN** the demo opens for the first time in a tab
- **THEN** the start card shows "Guided tour" first and "Explore freely" second, each with its sentence
Pinned by: `tests/unit/test_demo_layer_node.py::test_start_card`

#### Scenario: Narrow window
- **WHEN** the demo opens for the first time in a window 800 pixels wide
- **THEN** the start card also says "This demo is made for a laptop or desktop screen."
Pinned by: `tests/unit/test_demo_layer_node.py::test_start_card_narrow_note`

#### Scenario: Free starts with results
- **WHEN** the visitor chooses Explore freely
- **THEN** the demo is at the last checkpoint and the Recall page shows both result sets
Pinned by: `tests/unit/test_demo_state_node.py::test_free_starts_at_last_checkpoint`

### Requirement: Switching modes keeps the place
Switching between Guided and Free SHALL keep the current page and the demo's state. Switching to Guided SHALL resume at the last step shown when it belongs to the current page, otherwise at the first tour step for the current page whose checkpoint is not later than the demo's; a step whose checkpoint has already passed SHALL say so and offer Reset.
Surfaces: the Guided/Free switch in the Demo Mode bar.

#### Scenario: Switch keeps the place
- **WHEN** in Guided mode on the step "Decide a duplicate pair, then apply" with one pair decided, the visitor switches to Free and back to Guided
- **THEN** the Deduplication page is still open, the pair is still decided, and the tour shows the same step
Pinned by: `tests/unit/test_demo_tour_node.py::test_mode_switch_keeps_page_and_state`, `tests/unit/test_demo_state_node.py::test_switching_mode_keeps_place`

#### Scenario: Guided from a finished project
- **WHEN** in Free mode (last checkpoint) on the Ranking page the visitor switches to Guided
- **THEN** the tour shows the first Ranking step with the note "This step already happened in this demo. Reset to replay it from the start, or read on."
Pinned by: `tests/unit/test_demo_tour_node.py::test_guided_after_free_explains_passed_steps`

### Requirement: The guided tour explains and points
Each tour step SHALL open its page, highlight one element of the real page or the demo layer and show a card beside it, never over it, with a title, a few plain sentences following `docs/plain-words.md`, and the tag "You decide" or "WIDER does". A step on a choice between methods SHALL add a "Trade-off" line; a step MAY link a concept card. Each stage SHALL open with a step saying what the stage is for.
Surfaces: the demo's tour card; `showcase/demo/tour.yaml` (steps).

#### Scenario: Every step has a target that exists
- **WHEN** the tour's steps are checked
- **THEN** each step names a page that exists in `index.html` and a target that is an element id in `index.html`, an element of the demo layer, or declared as drawn by the page code that draws it
Pinned by: `tests/unit/test_demo_tour.py::test_every_step_target_exists`

#### Scenario: Who acts is always stated
- **WHEN** the tour's steps are checked
- **THEN** every step has the tag "You decide" or "WIDER does", every step marked as a method choice has a "Trade-off" line, and each stage's first step says what the stage is for
Pinned by: `tests/unit/test_demo_tour.py::test_steps_state_who_acts_and_tradeoffs`

#### Scenario: Plain words
- **WHEN** the tour's texts are checked against the word list of `docs/plain-words.md`
- **THEN** no avoided word appears outside a concept card that defines it
Pinned by: `tests/unit/test_demo_tour.py::test_plain_words`

### Requirement: Action steps wait and show what changed
A tour step that asks the visitor to act SHALL say it is their turn until the action is done, then SHALL show what changed, in numbers. Next SHALL never be blocked: on an undone step it moves on, doing the action first only when the next step needs its result. Skip SHALL do the action, or move on without it, and say "Done for you" with what was done.
Surfaces: the demo's tour card.

#### Scenario: Action step waits
- **WHEN** the step "Decide a duplicate pair, then apply" is shown
- **THEN** the card says it is the visitor's turn; after a pair is decided as the same paper it says the count changes only once the decisions are applied
- **AND** after the visitor presses "Apply decisions and check again" the card says "50 → 49 unique records" (for a pair decided as different papers it says "50 → 50 unique records: a pair decided as different papers changes no count")
Pinned by: `tests/unit/test_demo_tour_node.py::test_action_step_waits_then_reports`

#### Scenario: Next on an undone step
- **WHEN** the visitor presses Next on the step "Press Rank" without ranking
- **THEN** the records are ranked for them, the notice says "Done for you: the records are ranked." and the scores step is shown
- **AND** Next on the search-terms step without a change moves on and keeps the terms as they were
Pinned by: `tests/unit/test_demo_tour_node.py::test_next_past_an_undone_step_does_it_first`

#### Scenario: Skip does it
- **WHEN** the visitor presses Skip on the step "Decide a duplicate pair, then apply"
- **THEN** one pair is decided as Reviewer A decided it and applied, and the card says "Done for you: one pair decided as the same paper and applied; 49 unique records."
Pinned by: `tests/unit/test_demo_tour_node.py::test_skip_does_the_action`

### Requirement: The visitor controls the pace
The tour SHALL offer Back, Next, Skip and Repeat step, and Play, which advances through steps that need no action at a slow or normal pace and stops at each action step; while playing, Pause, Resume and Stop SHALL be shown. Exit SHALL ask first, keep the demo's state and close the demo tab to go back to the WIDER app, never to the showcase page; if the tab stays open, it SHALL say to close it.
Surfaces: the tour card; the Demo Mode bar (Exit).

#### Scenario: Back and Repeat
- **WHEN** on a step the visitor presses Back and then Repeat step
- **THEN** the previous step is shown again with its highlight, and an action already done shows as done
Pinned by: `tests/unit/test_demo_tour_node.py::test_back_repeat_and_finish`

#### Scenario: Autoplay stops for actions
- **WHEN** the visitor presses Play at step 1
- **THEN** the tour advances by itself until the first step that asks for an action, then stops there and shows Play again
Pinned by: `tests/unit/test_demo_tour_node.py::test_play_stops_at_action_steps`

#### Scenario: Pause, Resume and Stop
- **WHEN** during Play the visitor presses Pause, then Resume, then Stop
- **THEN** the tour halts on the current step, continues from it, and halts again with Play shown
- **AND** a simulated job running at the time pauses and resumes with the tour
Pinned by: `tests/unit/test_demo_tour_node.py::test_pause_resume_stop`

#### Scenario: Exit
- **WHEN** the visitor presses Exit and confirms "Leave the demo? This closes the demo tab, or opens the WIDER showcase page when the tab stays open."
- **THEN** the demo asks the browser to close the tab; if it stays open, the tab opens the showcase page (the site root next to /demo/), and the demo's state is still in the tab's storage
Pinned by: `tests/unit/test_demo_layer_node.py::test_exit_confirms_and_keeps_state`, `tests/unit/test_demo_layer_node.py::test_exit_that_closes_the_tab_goes_nowhere`

### Requirement: The tour works from the keyboard
Right arrow SHALL go to the next step, Left arrow back, Space SHALL start or pause Play, and Escape SHALL hide the tour card, which a "Show tour" button brings back. These keys SHALL act for the tour only when focus is on the page body and no record drawer, dedup panel or dialog of the app is open; otherwise they SHALL go to the app.
Surfaces: the tour card.

#### Scenario: Keyboard
- **WHEN** focus is on the page body and the visitor presses Right arrow, Left arrow, Space and Escape
- **THEN** the tour goes forward, back, starts play, and hides the card
Pinned by: `tests/unit/test_demo_tour_node.py::test_keys_only_outside_fields`

#### Scenario: Keys in a field or on a button
- **WHEN** focus is in the terms panel's "Add a term" field or on a button, and the visitor presses Right arrow or Space
- **THEN** the tour does not move; the field gets the key, and Space presses the button
Pinned by: `tests/unit/test_demo_tour_node.py::test_keys_only_outside_fields`

#### Scenario: Escape with the drawer open
- **WHEN** the Ranking page's record drawer is open and the visitor presses Escape
- **THEN** the drawer closes as in the app and the tour card stays shown
- **AND** on the Deduplication page, where the drawer is docked, Escape moves focus to the pair list as in the app and the tour card stays shown
Pinned by: `tests/unit/test_demo_tour_node.py::test_escape_goes_to_app_first`

### Requirement: Concept cards explain without claiming
The demo SHALL offer five concept cards: "Development and validation sets", "Scores and percentiles", "Disagreement and the third read", "Recall and its uncertainty", "Illustrative, not validated". Each SHALL open from the tour and from the Concepts menu, be at most about 120 words, and use the demo's recorded numbers, saying they are illustrative. The percentiles card SHALL say the app shows scores and the demo works out the percentile.
Surfaces: the Concepts menu; the tour's concept links.

#### Scenario: Cards reachable in Free mode
- **WHEN** in Free mode the visitor opens Concepts
- **THEN** the five cards are listed and each opens and closes with the keyboard
Pinned by: `tests/unit/test_demo_tour_node.py::test_concepts_menu`

### Requirement: The demo ends with how to start a real project
The last tour step and the Concepts menu SHALL offer an end card that says WIDER runs on the researcher's own computer, names `wider init <folder>` and then `wider ui`, sums up per stage what the researcher decides, what WIDER does and the trade-off, and offers "Explore freely" and "Start again" (and "See all chapters" after the Quick tour). It SHALL hold no link to the showcase page or an outside site.
Surfaces: the end card.

#### Scenario: End card
- **WHEN** the visitor finishes the last tour step
- **THEN** the end card shows the commands, the summary of who decides what, and its choices, none of them a way to the showcase page
Pinned by: `tests/unit/test_demo_tour.py::test_end_card_content`

### Requirement: The demo fits a laptop and works with the keyboard
The demo SHALL be usable at 1280×720 and larger with the tour card beside its target and the app's header visible below the Demo Mode bar. Every demo control SHALL be reachable with Tab, show a focus ring and have an accessible name.
Surfaces: the demo page.

#### Scenario: Laptop size
- **WHEN** the demo is opened at 1280×720 and every tour step is shown
- **THEN** no step's card overlaps its highlighted element
Pinned by: NOT_TESTED in the gate (browser walkthrough task 5.2 and the `ui-tester` keyboard persona, task 6.2; hand check M70)

#### Scenario: Controls have names
- **WHEN** the demo layer's controls are checked
- **THEN** each button in the bar, the start card, the tour card and the concept cards has text or an `aria-label`
Pinned by: `tests/unit/test_demo_layer_node.py::test_controls_have_names`

### Requirement: The demo bundle holds no private data
At recording and at build, the demo SHALL rewrite computer paths and the user name, mark a word the privacy guard flags in a model's text as "[word hidden in the demo]", and refuse, naming the file and the kind of string, any `/Users/<name>` but `/Users/you`, the user name, a key-like string or anything else the guard refuses. Nothing SHALL be dropped silently.
Surfaces: `showcase/demo/scrub.py`, `build_demo.py`.

#### Scenario: Paths and the user name in a recorded answer
- **WHEN** the scrub is given an invented answer holding a home-folder path to the project, a temporary path and the computer's user name
- **THEN** the paths read `~/reviews/employee-voice` or `~/<last part>`, the name reads "Reviewer A", and nothing else changes
Pinned by: `tests/unit/test_demo_scrub.py::test_scrub_rewrites_paths_and_name`

#### Scenario: No key state is recorded
- **WHEN** the snapshot's model list (`/api/backends`) is checked at every recorded checkpoint
- **THEN** every entry has `key_set` false, whatever keys the recording computer had
Pinned by: `tests/unit/test_demo_snapshot.py::test_backends_recorded_without_key`

#### Scenario: Paths and names scrubbed
- **WHEN** the demo is built from answers recorded under the build computer's home folder
- **THEN** the bundle has no `/Users/` path other than the app's placeholder `/Users/you/...` and no occurrence of the user name, and the project folder reads `~/reviews/employee-voice`
Pinned by: `tests/unit/test_demo_build.py::test_bundle_has_no_paths_names_or_keys`

#### Scenario: Guard refuses a leak
- **WHEN** a recorded answer contains a key-like string
- **THEN** the build stops with an error naming the file and the kind of string, and writes no bundle
Pinned by: `tests/unit/test_demo_build.py::test_build_refuses_key_like_string`

#### Scenario: A flagged word in a model's reason
- **WHEN** the scrub is given an invented recorded answer whose reason text holds a word taken at run time from the privacy guard's topic list, beside counts and decisions
- **THEN** the word reads "[word hidden in the demo]", the scrub reports it, and the answer's counts, decisions and structure are unchanged
Pinned by: `tests/unit/test_demo_scrub.py::test_hidden_words_keep_counts`

#### Scenario: The app's own files pass
- **WHEN** the unchanged files of `wider/ui/static/` are scanned with the same rules
- **THEN** none is refused (for example the id `ask-dialog` is not key-like)
Pinned by: `tests/unit/test_demo_build.py::test_app_files_pass_the_scan`

### Requirement: The app's files are reused unchanged
The build SHALL copy every file of `wider/ui/static/` into the demo byte for byte, except `index.html`, where it SHALL only change `/static/` paths to `/demo/`, add the CSP, add the demo layer's scripts and style, and prefix the title with "Demo — ". The same inputs SHALL give the same bundle.
Surfaces: `showcase/demo/build_demo.py`.

#### Scenario: Unchanged copies
- **WHEN** the demo is built
- **THEN** every file in `showcase/site/demo/` that also exists in `wider/ui/static/` (except `index.html`) has the same bytes
- **AND** `index.html` differs from the app's only in the listed edits
Pinned by: `tests/unit/test_demo_build.py::test_static_files_copied_unchanged`

#### Scenario: Same inputs, same bundle
- **WHEN** the demo is built twice from the same snapshot
- **THEN** the two bundles are byte for byte the same
Pinned by: `tests/unit/test_demo_build.py::test_build_is_deterministic`
