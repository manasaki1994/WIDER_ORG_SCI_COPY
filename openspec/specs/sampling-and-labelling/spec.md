# sampling-and-labelling Specification

## Purpose
Sampling and labelling turns the anchor ranking into human-labelled reference sets. A band-stratified probability sample with design weights supports the recall estimate. Purposive development pools are used to tune the screening prompt. Reviewers label both in one blinded queue, a second reviewer and adjudication settle disagreements, and locking freezes the labels. In the WIDER procedure this is stage key `sample` (requires `rank`); it is done once at least one label exists, and LLM screening is evaluated against its labels.

## Requirements

### Requirement: Band-stratified probability sample
The system SHALL draw the probability sample (default name `validation`) from the ranked records minus thin records and benchmark papers, taking within each rank band of `protocol.sampling.bands` a systematic sample with a random start and giving each record the design weight N_h/n_h. The draw uses the project seed unless one is given.
Surfaces: CLI `wider sample draw probability [--ranking NAME] [--seed N]`; UI Sampling and labelling page, "Draw samples from the ranking".

#### Scenario: Census band and sampled bands
- **WHEN** the probability sample is drawn with a census band S1 and sampled bands S2 (n=8) and S3 (n=6)
- **THEN** every eligible S1 record is drawn, 8 records are drawn from S2 and 6 from S3
- **AND** each S2 record carries the weight N_S2/8, every weight is at least 1, and no thin record or benchmark paper is drawn
Pinned by: `tests/unit/test_sample.py::test_probability_sample_design`

#### Scenario: Default bands
- **WHEN** the project has not changed `protocol.sampling.bands`
- **THEN** the bands are S1 ranks 1-100 (census), S2 101-300 (n=70), S3 301-1000 (n=60), S4 1001-3000 (n=46) and S5 3001 to the end (n=40)

#### Scenario: Implicit stratification by cluster
- **WHEN** a band ends within `protocol.ranking.cluster_top` and clusters of the ranking exist
- **THEN** the band's frame is sorted by cluster, then rank, before the systematic draw

#### Scenario: No ranking yet
- **WHEN** a draw is requested before any ranking exists
- **THEN** it fails with "no ranking; run wider rank run first" and the CLI exits with status 1

### Requirement: Purposive development pools
The system SHALL draw the development sample (default name `development`) from purposive pools, excluding benchmark papers and probability-sample members. Pools: `likely_include:<cell>`, `near_miss:<key>`, `adjacent:<key>` (approved literatures only), `topical_coverage:<cluster>`, `no_abstract:thin`, `clearly_irrelevant:tail` and, last, `boundary:near_miss|adjacent:<key>`.
Surfaces: CLI `wider sample draw development` (or `both`); UI Sampling and labelling page, "Draw samples from the ranking".

#### Scenario: Pools are disjoint from the probability sample
- **WHEN** the development sample is drawn after the probability sample
- **THEN** it contains likely_include, near_miss, topical_coverage, no_abstract and clearly_irrelevant pools
- **AND** no record is in both samples, and the sampling log lists both samples
Pinned by: `tests/unit/test_sample.py::test_development_sample_and_labels`

#### Scenario: Default pool quotas
- **WHEN** `protocol.sampling` is left at its defaults
- **THEN** each cell gets 4 likely includes from its top 100 records by cell score, each near-miss entry 4 records within rank 3000, and each approved adjacent literature 4 records
- **AND** each cluster gets 2 records starting with its best-ranked member, plus 5 thin records and 3 records ranked beyond `cluster_top`
- **AND** each near-miss entry and approved adjacent literature gets 4 boundary records
Pinned by: `tests/unit/test_sample_boundary.py::test_default_boundary_quota`

#### Scenario: Only approved adjacent literatures get a pool
- **WHEN** one adjacent literature is approved and another is only proposed, and `per_adjacent` is 3
- **THEN** the draw contains 3 records in the pool `adjacent:<approved key>` and none for the proposed literature
- **AND** with `per_adjacent` 0 or nothing approved, no `adjacent:` pool is drawn (a `boundary:adjacent:` pool follows `per_boundary`)
Pinned by: `tests/unit/test_adjacent.py::test_draw_development_adjacent_quota`

### Requirement: Stored sample with design log
The system SHALL store each sample in `samples` with its kind (`probability` or `purposive`), ranking, ranking checksum, seed and design log, and its records in `sample_rows` with band, pool, rank, weight and a shuffled queue position. Each draw records a stage result under stage `sample`.
Surfaces: CLI `wider sample draw`, `wider sample status`; UI Sampling and labelling page, sample cards with band tables and progress.

#### Scenario: Redrawing a labelled or locked sample
- **WHEN** a sample already has labels and the researcher draws it again under the same name
- **THEN** the draw is refused with "already has labels; use a new name" and the CLI exits with status 1
- **AND** a locked sample is refused with "sample <name> is locked"

### Requirement: Blinded labelling queue
The system SHALL present the probability and development samples to each reviewer as one queue ordered by queue position, hiding band, pool, rank and sample name and showing only that reviewer's own label. Locked samples stay in the queue, marked as locked and still blinded.
Surfaces: UI Sampling and labelling page, "Labelling queue" card (the reviewer name is remembered in the browser); CLI `wider label export` gives the same blinded view as a file.

#### Scenario: Two reviewers are blind to each other
- **WHEN** reviewer A labels a record include and reviewer B labels it exclude
- **THEN** A's queue shows include and B's queue shows exclude for that record
- **AND** neither queue row contains band, pool or rank
Pinned by: `tests/unit/test_sample.py::test_development_sample_and_labels`

#### Scenario: Locked samples are marked
- **WHEN** a sample is locked
- **THEN** its queue rows are marked locked and still contain no band, pool, rank or sample name
Pinned by: `tests/unit/test_records.py::test_queue_marks_locked_samples`

### Requirement: Labels per reviewer
The system SHALL store one label per sample, record and reviewer in `labels`, with a decision of `include`, `exclude` or `maybe`, an optional reason, codebook labels and a note, adding unknown reviewers to `reviewers`. A first label is accepted on a locked sample too; a change to a stored label on a locked sample is a correction (see "Corrections to labels and adjudications").
Surfaces: UI Sampling and labelling page, "Labelling queue" record drawer; CLI only through `wider label import`.

#### Scenario: Label changes are journalled and can be undone
- **WHEN** a reviewer changes a label and then undoes the change
- **THEN** the earlier label is restored
- **AND** the history summary of the change does not reveal the decision
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

### Requirement: Inter-reviewer agreement
The system SHALL compute, for two reviewers on one sample, the number of records both labelled, the share of identical decisions and Cohen's kappa over the three decisions, with a 3x3 table and the list of disagreements.
Surfaces: CLI `wider label agreement SAMPLE A B`; UI Sampling and labelling page, "Second reviewer and adjudication" card, "Agreement".

#### Scenario: One shared record in disagreement
- **WHEN** two reviewers have labelled one common record differently
- **THEN** agreement reports n = 1 and lists one disagreement
Pinned by: `tests/unit/test_sample.py::test_development_sample_and_labels`

### Requirement: Adjudication and final labels
The system SHALL let a named adjudicator record a decision and reason for any sample's record in `adjudications` (on a locked sample changing a stored one is a correction), refusing one without a name, and SHALL take as a record's final label the adjudication, else the named primary reviewer's label, else the first stored label.
Surfaces: CLI `wider label adjudicate SAMPLE RECORD_ID DECISION [--by NAME] [--reason TEXT]` (default `$WIDER_REVIEWER`, then `$USER`); UI disagreements in the drawer.

#### Scenario: Adjudication overrides both reviewers
- **WHEN** reviewers disagree on a record and the adjudicator decides maybe
- **THEN** the record's final label is maybe
Pinned by: `tests/unit/test_sample.py::test_development_sample_and_labels`

#### Scenario: Adjudication on a locked sample
- **WHEN** `dev` is locked, R1 labelled record 3 include and R2 exclude, and "dana" adjudicates it include with `wider label adjudicate dev 3 include --by dana`
- **THEN** the adjudication is stored and the record's final label is include
- **AND** a second `wider label adjudicate dev 3 exclude --by dana` exits 1 with "sample 'dev' is locked: a change to a saved label needs a reason for the correction" and changes nothing, while the same command with `--correction-reason "misread the population"` stores exclude and one correction
Pinned by: `tests/unit/test_label_completion.py::test_adjudication_on_a_locked_sample`

#### Scenario: Name taken from the environment
- **WHEN** `wider label adjudicate` runs without `--by` and with `WIDER_REVIEWER=dana`
- **THEN** the adjudication is stored as decided by "dana"
Pinned by: `tests/unit/test_records.py::test_remove_cli_without_any_name_and_adjudication_errors`

#### Scenario: No name, no adjudication
- **WHEN** an adjudication is posted through the API with an empty name, and `wider label adjudicate` runs without `--by` and with neither `$WIDER_REVIEWER` nor `$USER` set
- **THEN** the API returns 400 with a message that the adjudicator's name is needed, and the CLI prints that message and exits with code 1
- **AND** no adjudication is stored and none is decided by "adjudicator"
Pinned by: `tests/unit/test_records.py::test_remove_cli_without_any_name_and_adjudication_errors`

#### Scenario: Bad decision or unknown sample
- **WHEN** `wider label adjudicate` runs with the decision `perhaps`, or with a sample name that does not exist
- **THEN** it prints which value is wrong and exits with code 1, without a traceback, and stores nothing
- **AND** the API answers 400 for the bad decision and 404 for the unknown sample
Pinned by: `tests/unit/test_records.py::test_remove_cli_without_any_name_and_adjudication_errors`

#### Scenario: Name needed in the adjudication drawer
- **WHEN** no reviewer name is remembered in the browser and the reviewer presses `1` in the adjudication drawer
- **THEN** the drawer shows "enter your name first" and saves nothing

### Requirement: Locking a sample
The system SHALL lock a sample, freezing its membership and provenance (no redraw, extension or its undo); missing labels and adjudications are still accepted, a changed one is a correction. Lock and unlock are signed History changes; a lock is undoable until its recall is seen, an unlock is final. An unknown sample is refused ("no sample '<name>'"). Final labels show band, pool, weight and rank once locked.
Surfaces: CLI `wider sample lock|unlock NAME` (`--by`, `--yes`); UI Samples page.

#### Scenario: Labelling after the lock
- **WHEN** `dev` is locked, R1 has labelled record 1 include and not record 2, and R1 labels record 2 exclude and then changes record 1 to exclude without a reason
- **THEN** record 2's label is saved as an ordinary label, and the change of record 1 is refused with "sample 'dev' is locked: a change to a saved label needs a reason for the correction", leaving include in place
- **AND** the same change with the correction reason "misread the population" is saved and recorded as a correction (see "Corrections to labels and adjudications")
- **AND** a redraw of `dev`, `wider sample extend dev --pool boundary` and the undo of an earlier extension are refused as before
Pinned by: `tests/unit/test_sample.py::test_development_sample_and_labels`, `tests/unit/test_label_completion.py::test_redraw_and_extension_still_refused_when_locked`

#### Scenario: Unlocking on the command line
- **WHEN** a locked sample on which no recall result was seen is unlocked with `wider sample unlock <name> --by ana`
- **THEN** it prints "unlocked <name>" and exits with code 0
- **AND** a reviewer can change a label on that sample again without a correction reason
- **AND** History holds a `sample_unlock` change by "ana", and undoing it is refused with "an unlock is final"
Pinned by: `tests/unit/test_sample_lock.py::test_unlock_on_the_command_line_is_journalled_and_final`

#### Scenario: Unknown sample
- **WHEN** `wider sample lock nosuch` or `wider sample unlock nosuch` runs, or the UI route is called for `nosuch` with or without `unlock=true`
- **THEN** the CLI prints "no sample 'nosuch'" and exits with code 1, and the route returns 404
- **AND** no sample is created or changed and History is unchanged
Pinned by: `tests/unit/test_sample_lock.py::test_lock_unknown_sample`

#### Scenario: Lock journalled and undone
- **WHEN** "ana" locks sample "dev" on the page and then undoes that change before any recall result on "dev" is seen
- **THEN** History holds a `sample_lock` change by "ana" whose summary names "dev", and after the undo a change to a label of "dev" needs no correction reason
- **AND** after a recall result on a locked "validation" sample is recorded, undoing its lock is refused with "recall on validation was seen after the lock; unlock it instead, which is recorded"
Pinned by: `tests/unit/test_sample_lock.py::test_lock_is_journalled_and_undoable_until_recall_is_seen`

#### Scenario: A lock made before this change
- **WHEN** a synthetic project has a sample locked by an older version, so History holds no `sample_lock` for it
- **THEN** History offers no undo for that lock, and `wider sample unlock <name> --by ana` unlocks it and records a `sample_unlock` by "ana"
Pinned by: `tests/unit/test_sample_lock.py::test_a_lock_from_an_older_version_is_unlocked_not_undone`

#### Scenario: Unlock after recall was seen
- **WHEN** a recall result on the locked sample "validation" was recorded, or the project's recall looks were taken before looks were recorded, and "ana" asks to unlock it
- **THEN** the page's dialog and the CLI say in normal black text "The recall estimate on validation is no longer blind: its result was seen before this unlock." (for the older project: "may have been seen")
- **AND** the CLI without `--yes` changes nothing and exits 2; the dialog's Cancel changes nothing; `--yes` or the dialog's "Unlock" unlocks it
Pinned by: `tests/unit/test_sample_lock.py::test_unlock_after_recall_warns_and_needs_yes`, `tests/unit/test_samples_page_node.py::test_unlock_dialog_warns_when_recall_was_seen`

### Requirement: Blinded label CSV export
The system SHALL export a sample's queue as a CSV with the columns queue_pos, record_id, title, authors, year, journal, doi, abstract, decision, reason, labels and note, without band, pool, rank or weight, limited to one reviewer's labels when one is named.
Surfaces: CLI `wider label export SAMPLE OUT [--reviewer]`; no UI.

#### Scenario: Export hides the design
- **WHEN** the probability sample is exported
- **THEN** the file has one row per sampled record and no band column
Pinned by: `tests/unit/test_sample.py::test_csv_round_trip_and_rayyan`

### Requirement: Rayyan-compatible label import
The system SHALL import labels from a WIDER export or a Rayyan export, matching records by record_id, else by DOI or title, and reading Rayyan notes (`RAYYAN-INCLUSION`, `RAYYAN-LABELS`, `RAYYAN-EXCLUSION-REASONS`). Existing labels of the same reviewer are kept unless overwrite is requested.
Surfaces: CLI `wider label import SAMPLE FILE [--reviewer] [--overwrite]`; no UI.

#### Scenario: Round trip through a spreadsheet
- **WHEN** an exported file gets the decision include on three rows and is imported for a reviewer
- **THEN** 3 labels are imported and the other rows count as no_decision
- **AND** the result also reports unmatched, bad_decision and kept_existing counts
Pinned by: `tests/unit/test_sample.py::test_csv_round_trip_and_rayyan`

#### Scenario: Rayyan notes
- **WHEN** a Rayyan export row carries `RAYYAN-INCLUSION: {"Dana"=>"Excluded"}`, a `RAYYAN-LABELS` cell label and an exclusion reason
- **THEN** a label for reviewer Dana is stored with decision exclude, the cell label and the reason
Pinned by: `tests/unit/test_sample.py::test_csv_round_trip_and_rayyan`

### Requirement: Importing an externally drawn sample
The system SHALL import a CSV sample labelled elsewhere as a new locked sample, with each row's band and weight (else N_h/n_h), its decisions and `rayyan_labels` signed `original` or `--reviewer`. It replaces a same-named sample unless WIDER has since added labels, adjudications, corrections, provenance, a review package or a recall look (refused). `--independent BASIS` marks the labels independent.
Surfaces: CLI `wider sample import NAME FILE` (`--kind`, `--reviewer`, `--independent`); no UI.

#### Scenario: Importing a labelled sample
- **WHEN** a file with two known records (bands S1 and S2, decisions Included and Excluded) and one unknown title is imported
- **THEN** 2 records are matched and labelled, 1 is unmatched, and the weights 1.0 and 2.8 are kept
- **AND** the new sample is locked and its final labels are include and exclude
Pinned by: `tests/unit/test_sample.py::test_import_sample_csv`

#### Scenario: A sample with work added in WIDER is not replaced
- **WHEN** the imported `validation` then took a label by R2 in WIDER (or an adjudication, a correction, a provenance declaration, a review package or a recall estimate), and `wider sample import validation other.csv` runs
- **THEN** it exits 1 with "validation has labels or results added in WIDER since it was imported; import the file under another name", and the sample, its labels, adjudications and corrections are unchanged
- **AND** a re-import of a sample nothing was added to replaces it as before, and leaves no stated independence, correction or seal of the old sample: a sample imported with `--independent BASIS` and re-imported without it has no independent label
Pinned by: `tests/unit/test_label_completion.py::test_sample_import_keeps_work_added_in_wider`

#### Scenario: A review package made for an imported sample blocks a replacing import
- **WHEN** a second-reviewer package was made for the imported, locked `ext`, and `wider sample import ext other.csv` runs
- **THEN** it exits 1 with "ext has labels or results added in WIDER since it was imported; import the file under another name", and the labels are unchanged
Pinned by: `tests/unit/test_label_completion.py::test_sample_import_refused_once_a_package_was_made`

### Requirement: Redrawing an unlabelled sample keeps its id
The system SHALL keep a sample's id when a sample without labels is drawn again under the same name, replacing its records, design log, seed and size, so that there is still one sample of that name and everything that refers to it by id still finds it.
Surfaces: CLI `wider sample draw`, `wider sample status`; UI Sampling and labelling page, "Draw samples from the ranking".

#### Scenario: Redraw with another seed before labelling
- **WHEN** the development sample is drawn, and drawn again under the same name with another seed before any label exists
- **THEN** the second draw returns the same sample id as the first
- **AND** the sample holds only the records of the second draw, its design log names the new seed, and exactly one sample has that name

### Requirement: Clearing the reviewer name forgets it
The system SHALL forget the remembered reviewer name in the browser when an empty name is saved with Change in the top bar (D33), so later undo, notes and decisions are not made under that name. The Labelling page has no reviewer field; it signs with the name WIDER works as.
Surfaces: UI top bar Change; CLI `--by` (nothing is remembered on the CLI).

#### Scenario: Reviewer field cleared
- **WHEN** WIDER works as "ana" and an empty name is saved with Change
- **THEN** the remembered name `wider.reviewer` is removed from the browser and the top bar reads "No name yet"
- **AND** the Labelling page has no reviewer field
Pinned by: `tests/unit/test_name_once_node.py::test_change_with_an_empty_name_forgets_it`, `tests/unit/test_static_followup.py::test_clearing_the_name_forgets_it`

#### Scenario: The History page after the name was cleared
- **WHEN** the History page was opened as "ana", and an empty name is then saved with Change
- **THEN** "Undo my last change" sends nothing and says "enter your name first: use Change beside “Working as” in the top bar"
Pinned by: `tests/unit/test_ui_followup_r13.py::test_history_shows_the_remembered_name_after_it_was_cleared_elsewhere`, `tests/unit/test_ui_followup_r11.py::test_undo_last_without_a_name_sends_nothing`, `tests/unit/test_ui_followup_r13.py::test_undo_last_without_a_name_off_the_history_page_only_says_so`

#### Scenario: No name, no request
- **WHEN** no name is set and the labelling queue would ask for the reader's own notes and tags
- **THEN** no request is sent and nothing is shown, since blind mode needs a name
- **AND** "Open queue" opens the name dialog in place of a request
- **AND** the record drawer opened from the queue is headed "Labelling queue (blind)"
Pinned by: `tests/unit/test_ui_followup_r23.py::test_the_labelling_queue_asks_nothing_without_a_name`, `tests/unit/test_ui_followup_r23.py::test_the_name_request_turns_into_the_next_step_once_a_name_is_typed`, `tests/unit/test_ui_followup_r31.py::test_the_labelling_queue_runs_whole_rows_filter_and_signing`

#### Scenario: Another name while a queue is shown
- **WHEN** the queue loaded for "ana" is shown and "bo" is saved with Change, in this tab or another
- **THEN** the page reloads, so ana's queue and its decisions, notes and tags are no longer in view, and a queue record open in the drawer is gone
- **AND** decisions, notes and tags made in the drawer are signed with the name the queue was loaded for
- **AND** after the reload Blind is ticked, whatever the browser restored
Pinned by: `tests/unit/test_name_once_node.py::test_change_switches_the_name_and_reloads`, `tests/unit/test_ui_followup_r29.py::test_switching_to_another_name_reloads_so_no_queue_stays`, `tests/unit/test_name_once_node.py::test_another_tab_reloads_when_the_name_changes`, `tests/unit/test_name_once_node.py::test_blind_is_ticked_again_after_a_reload`, `tests/unit/test_ui_followup_r30.py::test_notes_and_tags_in_the_queue_are_signed_with_the_name_the_queue_was_loaded_for`

### Requirement: A blind queue over locked and open samples leaves the locked records out
The system SHALL leave a locked sample's records out of a blind queue merged with an open sample, and sealed validation records out of one mixed with unsealed ones: changing them needs a correction reason, telling how they were drawn. A one-sample or unblinded queue lists them, none read-only. A blind row SHALL carry an opaque item, never a sample id, and over several samples no locked flag; an unblinded row keeps both.
Surfaces: UI Labelling queue; CLI `wider label export SAMPLE` (one sample).

#### Scenario: One locked and one open sample
- **WHEN** samples "dev" (locked) and "probe" (open) exist and a reviewer opens the blind queue
- **THEN** it holds only the records of "probe", none read-only, from the engine and the route alike
- **AND** the unblinded queue holds both, none read-only
Pinned by: `tests/unit/test_records.py::test_blind_queue_mixing_locked_and_open_samples_leaves_the_locked_records_out`

#### Scenario: No locked flag in a blind queue over several samples
- **WHEN** "dev" is locked, every record of "validation" is sealed, and a reviewer opens the blind queue over both
- **THEN** it holds the records of both (none is left out, as all are guarded) and no row carries a locked flag, from the engine and the route alike
- **AND** a queue of "dev" alone and the unblinded queue keep the flag
Pinned by: `tests/unit/test_label_completion.py::test_blind_queue_over_several_samples_carries_no_locked_flag`

#### Scenario: Sealed records and an open sample
- **WHEN** 10 records of the unlocked `validation` are sealed by a recall estimate, 5 were added to it afterwards, `dev` is open, and a reviewer opens the blind queue over all samples
- **THEN** it holds the records of `dev` and the 5 added records, and none of the 10 sealed ones
- **AND** the blind queue of `validation` alone holds all 15
- **AND** when records the reader has not labelled were left out, the route (whose body stays a list of rows) sends the header `X-Left-Out: N` and the page says once "N records not labelled yet are in a queue of their own: choose a sample in the sample select", which names no sample
Pinned by: `tests/unit/test_label_completion.py::test_blind_queue_leaves_sealed_records_out`, `tests/unit/test_samples_page_node.py::test_left_out_records_said_once`

#### Scenario: Blind rows carry no sample id
- **WHEN** "ana" opens the blind queue over the open samples "dev" and "probe" and saves a label from a row
- **THEN** no row holds `sample_id`, band, pool, rank or sample name, each holds an `item`, and the page posts the label with that item
- **AND** the route still returns a list of rows, so the drawer, the synthetic demo and the existing queue tests read it as before
- **AND** the label is saved on the right sample, and its History summary names neither sample
Pinned by: `tests/unit/test_records.py::test_blind_queue_rows_carry_items_not_sample_ids`, `tests/unit/test_drawer_keys_node.py::test_blind_queue_label_posts_the_item`

#### Scenario: A blind label stays in the drawer's history
- **WHEN** "ana" labels record 7 from the blind queue and opens "History of this record (your changes)" in the drawer
- **THEN** it lists her label, summarised "ana labelled record 7", and no line of it names a sample
Pinned by: `tests/unit/test_records.py::test_blind_label_stays_in_the_drawer_history`

### Requirement: Agreement controls have names
The system SHALL give the sample picker and the two reviewer fields of the "Second reviewer and adjudication" card accessible names ("Sample to compare", "Reviewer A", "Reviewer B"), so a screen reader names them when they are reached by the keyboard.
Surfaces: UI Sampling and labelling page, "Second reviewer and adjudication" card; CLI `wider label agreement SAMPLE A B` takes them as arguments.

#### Scenario: Tabbing to the agreement controls
- **WHEN** a keyboard user tabs to the sample picker and the reviewer fields of the card
- **THEN** each has its own accessible name
Pinned by: `tests/unit/test_ui_followup_r23.py::test_the_agreement_fields_are_named`

### Requirement: Boundary-case pool
The system SHALL draw per near-miss entry and approved adjacent literature `per_boundary` records (default 4) into `boundary:near_miss|adjacent:<key>`, at random from the `near_miss_pool` untaken records with an abstract within `near_miss_max_rank` that score highest on both the best cell and that entry (the lower rank percentile). No model call; it is drawn after every other pool.
Surfaces: CLI `wider sample draw development`; UI Sampling and labelling page, "Draw samples from the ranking".

#### Scenario: Boundary records sit close to the criteria and to a near miss
- **WHEN** a synthetic corpus has records scoring high on a cell only, high on near-miss entry `nm1` only, and high on both, and the development sample is drawn with `per_boundary` 2 and `near_miss_pool` 4
- **THEN** the pool `boundary:near_miss:nm1` holds 2 records, both from the high-on-both group, none of them in another pool
Pinned by: `tests/unit/test_sample_boundary.py::test_boundary_records_score_high_on_both`

#### Scenario: Other pools unchanged for the same seed
- **WHEN** the same ranking is drawn with seed 7 once with `per_boundary` 0 and once with the default
- **THEN** every record of the first draw is in the second under the same pool, and the second adds only `boundary:` records
Pinned by: `tests/unit/test_sample_boundary.py::test_other_pools_unchanged`

#### Scenario: No near-miss or adjacent entry
- **WHEN** the project has no near-miss artifact and no approved adjacent literature
- **THEN** no boundary pool is drawn and the sampling log says "boundary: no near-miss or adjacent entries"
Pinned by: `tests/unit/test_sample_boundary.py::test_no_entries_no_pool`

### Requirement: Extending an unlocked purposive sample with a pool
The system SHALL add the boundary pool to an existing purposive sample: records in it or the probability sample, and benchmarks, are skipped; new ones are shuffled in among its unlabelled records, labels stay; the design log and journal record it with time and author; undo removes the added records unless one is labelled. A locked or probability sample MUST be refused.
Surfaces: CLI `wider sample extend NAME --pool boundary [--author]`; UI Sampling page, sample card, "Add boundary cases".

#### Scenario: Extending a labelled development sample
- **WHEN** `development` holds 20 records, 12 of them labelled, and `wider sample extend development --pool boundary --json` runs with 2 near-miss entries
- **THEN** up to 8 new records are added, the 12 labelled records keep their labels and queue positions, the new records are shuffled with the 8 unlabelled ones over those 8 positions and positions 21 and later, and the output gives the number added per pool and names no record (the CLI, `--json` and the route alike)
- **AND** `POST /api/samples/development/extend` with `{"pool": "boundary"}` on a copy of the project adds the same records
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_keeps_labels_and_queue`, `tests/unit/test_sample_boundary.py::test_extend_cli_matches_api`

#### Scenario: Every earlier record already labelled
- **WHEN** all 20 records of `development` are labelled and `wider sample extend development --pool boundary` adds 8
- **THEN** the output says "all 20 earlier records are labelled: the added records are recognisable as the boundary pool", and the CLI, `--json` and the route say the same
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_all_labelled_says_so`

#### Scenario: Extending again, or a sample that already has the pool
- **WHEN** `development` was drawn with the boundary pool, or was extended once, and the extension runs again
- **THEN** it adds no record and says "development already has the boundary pool for every entry", exiting 0
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_twice_adds_nothing`

#### Scenario: An entry approved after the boundary pool was drawn
- **WHEN** `development` has `boundary:near_miss:nm1`, then a second adjacent literature `al2` is approved and the extension runs
- **THEN** it adds records only to `boundary:adjacent:al2`, and `boundary:near_miss:nm1` is unchanged
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_new_entry_gets_its_pool`

#### Scenario: No entry to draw from, or no embedding consent
- **WHEN** the project has no near-miss entry and no approved adjacent literature, and separately its embedding backend is remote without consent
- **THEN** the first adds nothing and says "no near-miss or adjacent entry: no boundary records to add", exiting 0 (with `per_boundary: 0` it says "per_boundary is 0: no boundary records to add"); the second, without a terminal, exits 2 with the consent message and the route answers 403 `backend_consent_required`, before any embedding call, storing nothing
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_without_entries_or_consent`

#### Scenario: An imported sample
- **WHEN** `dev2` was made by `wider sample import` (no seed), then unlocked, once with a ranking and once without
- **THEN** with a ranking the extension draws with the project seed; without one it exits 1 with "sample dev2 was imported without a ranking: no boundary records can be drawn", the route answers 400, and nothing is stored
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_imported_sample`

#### Scenario: Locked or probability sample
- **WHEN** `wider sample extend development --pool boundary` runs on a locked `development`, and on the probability sample `validation`
- **THEN** the first exits 1 with "sample development is locked; wider sample unlock development first", the second exits 1 saying only a purposive sample can be extended, and nothing is stored
- **AND** the route answers 409 and 400 with the same messages
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_refused_locked_or_probability`

#### Scenario: Undoing an extension
- **WHEN** `development` was extended with 6 boundary records and `wider history undo ID` runs on its change (or History's Undo button)
- **THEN** the 6 records leave the sample, and the first 20 records keep their labels and get back the queue positions they had before the extension, and the sample's size is 20 again
- **AND** when one added record already has a label by either reviewer, or an adjudication, the undo is refused with "a record added by this extension is labelled" and nothing changes
Pinned by: `tests/unit/test_sample_boundary.py::test_extend_undo`

### Requirement: Validation sample plan from a labelling budget
The system SHALL compare ways to spread a labelling budget over the bands (current, proportional, recall-optimal n_h ∝ N_h·√p_h, top band in full, the researcher's own), each sampled band ≥ `min_per_band`, by seeded simulation at low, middle and high rates: expected relevant papers, median margin and, with a target, the chances of a clear answer, "met" and "not met".
Surfaces: CLI `wider validation plan [--budget N] [--rates ..] [--allocation ..] [--protocol P] [--set N]`; UI plan card.

#### Scenario: Allocations of 300 labels
- **WHEN** a synthetic ranking of 2000 records has sampled bands S1 1-100, S2 101-500 and S3 501-2000, no thin record or benchmark paper, and `wider validation plan --budget 300 --rates S1=0.5,S2=0.1,S3=0.01 --expected-recall 0.9 --json` runs
- **THEN** it lists the allocations "current", "proportional", "recall-optimal" and "top band in full", each summing to 300 with every sampled band at least 10, "top band in full" taking all 100 of S1 and spreading 200 over S2 and S3 recall-optimally, and each giving the expected number of relevant papers, the chance of getting an interval at all (no interval when a sampled band shows no relevant paper), and the median, 10th and 90th percentile of the margin over the simulated samples that give one
- **AND** shares are rounded by largest remainder, a tie going to the higher band; running it again gives the same numbers (the simulation uses the project seed), and `POST /api/validation/plan` with the same body returns the same table
- **AND** each allocation has three rows, at the given rates, at half of them and at double them (at most 1), and the output says "Rates uncertain: each allocation is shown at half and double your rates as well"
Pinned by: `tests/unit/test_validation_plan.py::test_four_allocations_sum_to_budget`, `tests/unit/test_validation_plan.py::test_plan_cli_matches_api`, `tests/unit/test_validation_plan.py::test_plan_sensitivity_rows`

#### Scenario: Your own allocation
- **WHEN** the plan of "Allocations of 300 labels" runs with `--allocation S1=100,S2=100,S3=100` (or the card's "Your own allocation" fields)
- **THEN** a fifth row "yours" is simulated and shown with the same columns, not rescaled, and is never refused for differing from the recommendation
- **AND** an allocation that does not sum to the budget, gives a band fewer than 10 or more than N_h, or names an unknown band, is refused with "your allocation sums to 290, not the budget of 300", "S3 needs at least 10" or "no band S9", the route answers 400, and nothing is stored
Pinned by: `tests/unit/test_validation_plan.py::test_plan_own_allocation`

#### Scenario: Top band already a census
- **WHEN** S1 is a census band (all 100 records drawn) and the plan runs with `--budget 300`
- **THEN** every allocation keeps S1 at 100 and spreads 200 over S2 and S3, and the row "top band in full" says "same as recall-optimal: the top band is already a census"
Pinned by: `tests/unit/test_validation_plan.py::test_top_band_already_census`

#### Scenario: A clear answer on the target
- **WHEN** a recall target of 0.85 is set and the plan of "Allocations of 300 labels" runs
- **THEN** each allocation also gives "if recall is 0.9: a clear answer in P% of simulated samples (met M%, not met N%)", each simulated sample judged as `wider recall` would judge it at look 1 (the headline interval, or the model-based interval where the band rule gives none)
- **AND** the allocation with the highest chance of a clear answer at the middle rates is marked "recommended", not the one with the highest chance of "met"; ties go to recall-optimal; the CLI, `--json` and the card name the same one
- **AND** with neither target nor precision, the recommendation is the highest chance of an interval, then the smallest median margin, and the output adds "No recall target or margin is set: the recommendation only seeks an interval; set a target with wider validation target."
Pinned by: `tests/unit/test_validation_plan.py::test_chance_of_clear_answer`, `tests/unit/test_validation_plan.py::test_plan_without_target_said`

#### Scenario: Plan with and before a screening result
- **WHEN** the plan of "A clear answer on the target" runs once with `--protocol main`, whose active result set keeps 300 of S2's 400 records, and once in a copy with no result set
- **THEN** the first simulates each band from four cells (kept or not by the set, relevant or not) and judges a sample the band rule gives no interval with the model-based interval by band and screening decision
- **AND** `--set 1` reads set 1 of `main` instead; with no `--protocol`, the most recently screened protocol is read, as the report does, and the output names it
- **AND** the second simulates the screening decisions from the expected recall, judges such a sample with the band-level model-based interval, and says "No screening result yet: screening decisions are simulated from the expected recall 0.9."
Pinned by: `tests/unit/test_validation_plan.py::test_plan_before_screening_result`

#### Scenario: Where the inclusion rates come from
- **WHEN** the plan runs without `--rates`, once with no validation label, and once after 120 validation records are labelled
- **THEN** the first says "Expected inclusion rates: a default guess (S1 0.30, S2 0.05, S3 0.01), not from your data; give your own with --rates", and the second says "Expected inclusion rates: from the 120 validation labels so far" and uses each band's share of relevant labels under the primary recall variant (include, or include and maybe), with one half of one relevant paper added to a band with none
- **AND** the sentence is said once, in normal text, on the card and in the CLI; when the planned bands differ from the sample's, each planned band's rate comes from the labelled records whose rank falls in it, and a planned band with none takes the default guess, named in the sentence
- **AND** with rates from labels, the low and high rows use each band's 10th and 90th percentile of its Jeffreys posterior; no screening score, rank or model probability is used as a rate
Pinned by: `tests/unit/test_validation_plan.py::test_rates_source_is_said`, `tests/unit/test_validation_plan.py::test_rates_mapped_by_rank`

#### Scenario: Budget smaller than the census bands
- **WHEN** `wider validation plan --budget 80` runs and the census band S1 holds 100 eligible records
- **THEN** it exits 1 with "the budget of 80 is below the 100 records of the census bands (S1); raise the budget or make S1 a sampled band", and the route answers 400 with the same message
Pinned by: `tests/unit/test_validation_plan.py::test_budget_below_census`

#### Scenario: No ranking yet
- **WHEN** `wider validation plan --budget 300` runs in a project with no ranking
- **THEN** it exits 1 with "no ranking yet: run wider rank run first", the route answers 400, and nothing is stored
- **AND** with a ranking but no probability sample, `--apply recall-optimal` writes the bands and its output has no "keeps its own bands" line
Pinned by: `tests/unit/test_validation_plan.py::test_plan_refusals_no_ranking`

### Requirement: Smallest budget for a clear answer or a precision
The system SHALL, given no budget, search for the smallest total that gives a clear answer on the target in `plan_power` (default 0.8) of simulated samples, or a median margin at most a set precision, per rule, with a range over the low, middle and high rates; if even the whole frame does not, it SHALL say a clear answer needs higher screening recall. There is no default precision.
Surfaces: CLI `wider validation plan [--precision M]`; UI plan card, "Margin wanted (optional)".

#### Scenario: Budget for a clear answer
- **WHEN** the target is 0.85 and the ranking of "Allocations of 300 labels" is planned with `--rates S1=0.5,S2=0.1,S3=0.01 --expected-recall 0.95` and no budget
- **THEN** the output says "About B labels give a clear answer in 80% of the simulated samples (sampling.plan_power; recall-optimal: S1 x, S2 y, S3 z); likely between L and U, given the uncertain rates.", where B is in steps of 10, comes from the most demanding of the three rate rows, and L ≤ B ≤ U; the recommended rule is the one with the smallest such B
- **AND** after `wider protocol set sampling.plan_power 0.9` the sentence says 90% and B does not fall; the methods draft names the chance when a plan was used
Pinned by: `tests/unit/test_validation_plan.py::test_smallest_budget_for_decision`, `tests/unit/test_validation_plan.py::test_plan_power_used`

#### Scenario: Suggested margin
- **WHEN** the target is 0.85, `--expected-recall 0.95` is given and no precision is stored
- **THEN** the plan adds "Suggested margin: about 0.10 (the expected recall 0.95 minus the target 0.85); a wider margin cannot tell met from not met.", and `protocol.recall.precision` stays empty
- **AND** with `--expected-recall 0.84`, or with no target, no margin is suggested
Pinned by: `tests/unit/test_validation_plan.py::test_plan_suggested_margin`

#### Scenario: A margin tried in the plan
- **WHEN** no precision is stored and `wider validation plan --precision 0.08` runs (or the card's "Margin wanted (optional)" holds 0.08)
- **THEN** the plan searches for a margin of 0.08, `protocol.recall.precision` stays empty and History gets no entry; only `wider validation target --precision`, the "Recall target and rule" section's "Planned margin (optional)" or `wider protocol set` store a margin
Pinned by: `tests/unit/test_validation_plan.py::test_plan_precision_is_what_if`

#### Scenario: A clear answer out of reach
- **WHEN** the target is 0.90 and `--expected-recall 0.90` equals it, so that a clear answer in 80% (`plan_power`) of the simulated samples needs more than half of the frame, or only the whole frame
- **THEN** the output adds "That is more than half of the N frame records: recall is expected near the target. A clear answer needs higher screening recall (your rule, or a revised prompt) rather than more labels." after the total, or, when only labelling every frame record gives it, says "Only labelling all N frame records gives a clear answer in 80% of simulated samples: ..." with the same advice and no total, and exits 0
Pinned by: `tests/unit/test_validation_plan.py::test_decision_out_of_reach`

#### Scenario: Nothing to plan for
- **WHEN** `wider validation plan` runs with no budget, no target and no precision
- **THEN** it exits 1 with "give a budget, or set a target or a margin to search for one", the route answers 400, and `protocol.recall.precision` stays empty: no default margin is assumed
Pinned by: `tests/unit/test_validation_plan.py::test_plan_needs_budget_target_or_precision`

#### Scenario: Budget for a margin of 0.08
- **WHEN** a synthetic ranking of 2000 records has the census band S1 1-100 (100 records) and the sampled bands S2 101-500 and S3 501-2000, and is planned with `--precision 0.08 --rates S1=0.5,S2=0.1,S3=0.01 --expected-recall 0.9` and no budget
- **THEN** the output says "About B labels give a margin of at most 0.08 (recall-optimal allocation: S1 100, S2 x, S3 y); likely between L and U, given the uncertain rates.", where B is the smallest budget in steps of 10 that meets it at the most demanding rate row, and B − 10 does not
Pinned by: `tests/unit/test_validation_plan.py::test_smallest_budget_for_precision`

#### Scenario: Precision out of reach
- **WHEN** `--precision 0.0001` runs and only labelling every frame record gives a predicted margin that small (the exact bounds of a census have no width)
- **THEN** the output says "a margin of 0.0001 is out of reach: only labelling all N frame records gives it" (or, when even that misses it, "... out of reach even with every frame record labelled (predicted margin with every frame record labelled: M)"), gives no budget, and exits 0
Pinned by: `tests/unit/test_validation_plan.py::test_precision_out_of_reach`

### Requirement: Bands from ranking percentiles
The system SHALL accept probability-sample bands as percentiles of the ranking, resolve them to ranks from the ranking's size at draw time, and keep both in the design log. Using a plan SHALL write its allocation into `protocol.sampling.bands`, recorded in history, and say that an existing sample keeps its own bands.
Surfaces: CLI `wider validation plan --percentiles 1,5,10,25,50 [--apply NAME]`; UI Samples and labelling page, plan card, "Bands by percentile", "Use this allocation".

#### Scenario: Percentile bands resolved to ranks
- **WHEN** a ranking has 2000 records and the bands are top 1%, 1-5%, 5-10%, 10-25%, 25-50% and 50-100%
- **THEN** the draw uses ranks 1-20, 21-100, 101-200, 201-500, 501-1000 and 1001-2000, and the design log gives each band's percentiles and ranks
Pinned by: `tests/unit/test_validation_plan.py::test_percentile_bands_resolved`

#### Scenario: Mixed or gapped percentile bands
- **WHEN** `protocol.sampling.bands` mixes a band given by ranks with one given by percentiles, or its percentile bands leave a gap (0-1, then 5-10) or do not end at 100
- **THEN** the protocol is refused with "bands are given either all by rank or all by percentile", "percentile bands must follow on from each other: 1 then 5" or "the last percentile band must end at 100", from `wider protocol set` and the plan card alike, and nothing is stored
Pinned by: `tests/unit/test_validation_plan.py::test_percentile_bands_validated`

#### Scenario: Using a plan
- **WHEN** a probability sample `validation` exists and `wider validation plan --budget 300 --apply recall-optimal` runs (or "Use this allocation" on that row)
- **THEN** `protocol.sampling.bands` holds that allocation, History gets a settings entry ("bands from plan recall-optimal") that undo restores, and the output says "The sample validation keeps its own bands; draw a new sample to use these"
- **AND** with `--apply yours` while recall-optimal was recommended, the entry and `validation_checks.plan` in the report record the plan's inputs and "used: yours; recommended: recall-optimal"
Pinned by: `tests/unit/test_validation_plan.py::test_apply_writes_bands_and_history`, `tests/unit/test_validation_plan.py::test_apply_records_choice_against_advice`

### Requirement: Extending the probability sample within its bands
The system SHALL add to chosen bands of an unlocked probability sample a random sample of frame records in no other sample, keeping the weight N_h/n_h, both counted over band records outside every other sample; records also in another sample get no weight. It SHALL journal it with undo and record whether a result was seen. A locked sample or changed ranking MUST be refused.
Surfaces: CLI `wider validation extend NAME (--band S2=20 ...|--suggested)`; UI sample card, "Add records to bands".

#### Scenario: Twenty more records in S2
- **WHEN** `validation` has S2 with N=400 and n=70 (weight 400/70), no S2 record in the development sample, 50 records labelled, and `wider validation extend validation --band S2=20 --json` runs
- **THEN** 20 S2 frame records not in any sample are added, S2's weight becomes 400/90 for all 90 records, the design log's S2 entry and `samples.n` are updated, the labelled records keep their labels and queue positions, the new ones are shuffled with the unlabelled ones, and the output names no record
- **AND** `POST /api/validation/extend` with `{"sample": "validation", "bands": {"S2": 20}}` on a copy gives the same records and weights, and the sample card's "Add records to bands" with S2 20 sends that body
Pinned by: `tests/unit/test_validation_extend.py::test_extend_band_weights_and_queue`, `tests/unit/test_validation_extend.py::test_extend_cli_matches_api`, `tests/unit/test_samples_page_node.py::test_add_records_to_bands`

#### Scenario: The suggested extension
- **WHEN** the outcome is "inconclusive" with the suggestion "S2 30, S3 30" stored with the last look, every sampled record is labelled, and `wider validation extend validation --suggested` runs
- **THEN** 30 records are added to S2 and 30 to S3, the same as `--band S2=30 --band S3=30`; with 10 S3 records of the sample still unlabelled, S3 gets 20
- **AND** when a label changed since that look, it exits 1 with "the labels changed since the last recall estimate; estimate recall again to update the suggestion" (the route answers 409) and adds nothing
- **AND** when no suggestion is stored, it exits 1 with the suggestion's first line and adds nothing; when the suggestion is above the practical limit (`recall.practical_labels`), or is every record left, it adds the records as usual
Pinned by: `tests/unit/test_validation_extend.py::test_extend_suggested`, `tests/unit/test_validation_extend.py::test_extend_suggested_above_limit`

#### Scenario: Development records left out
- **WHEN** 30 of S2's 400 frame records are in the development sample, none of them in `validation` (n=70), and `--band S2=20` runs
- **THEN** the 20 are drawn from the 300 records in neither sample, S2's weight becomes 370/90, and the estimate's scope line adds "30 records of the development sample are not covered"
Pinned by: `tests/unit/test_validation_extend.py::test_extend_excludes_development`

#### Scenario: Development records already in the sample
- **WHEN** 30 of S2's 400 frame records are in the development sample, 5 of them also in `validation` (n=70), and `--band S2=20` runs
- **THEN** the 20 come from records in neither sample, S2's 85 records outside the development sample get the weight 370/85, the 5 keep their labels but get no weight, and the scope line adds "5 records of validation are in the development sample and are left out of the estimate"
Pinned by: `tests/unit/test_validation_extend.py::test_extend_with_development_records_in_sample`

#### Scenario: First draw leaves out development records
- **WHEN** 30 of S2's 400 frame records are in the development sample and `wider sample draw validation` draws a new probability sample, while an older sample drawn before this change holds 5 development records
- **THEN** none of the 30 is drawn, `--json` gives `development_excluded: 30`, and the scope line adds "30 records of the development sample are not covered"; the older sample keeps its records unchanged and its 5 development records get no weight
Pinned by: `tests/unit/test_validation_extend.py::test_first_draw_leaves_out_development`

#### Scenario: Added records recognisable
- **WHEN** every one of S2's 70 records is labelled and `--band S2=20` runs
- **THEN** the output and the history entry say "every earlier record of S2 is labelled, so the 20 added records are recognisable as added", and the queue still shows no band, weight or screening decision
Pinned by: `tests/unit/test_validation_extend.py::test_extend_recognisable_said`

#### Scenario: Extending after a result was seen
- **WHEN** a recall estimate was made on `validation` before it was extended
- **THEN** the history entry and every later recall output say "validation was extended after a recall result was seen (date); the interval does not allow for that decision"
Pinned by: `tests/unit/test_validation_extend.py::test_extend_after_look_is_said`

#### Scenario: Refused extensions
- **WHEN** the extension runs on a locked `validation`, on a sample drawn from a ranking that has since been re-run with a different checksum, on the purposive `development`, with more records than the band's frame has left, and on `validation` after `validation2` was drawn fresh from it
- **THEN** the first exits 1 with "sample validation is locked; wider sample unlock validation first", the second with "the ranking changed since validation was drawn; draw a fresh sample instead", the third with "only a probability sample can be extended here; use wider sample extend for a purposive sample", the fourth with "S2 has only 12 frame records left", the fifth with "validation was replaced by validation2 (a fresh sample); extend validation2 instead", the route answers 409, 409, 400, 400 and 409, and nothing is stored
- **AND** the route refuses a body without `"sample"` (422), as the CLI needs NAME
Pinned by: `tests/unit/test_validation_extend.py::test_extend_refusals`

#### Scenario: Undoing an extension
- **WHEN** `wider history undo ID` runs on the extension's entry and no added record has a label or adjudication
- **THEN** the added records leave the sample, the earlier weights and queue positions come back, and with a labelled added record the undo is refused with "a record added by this extension is labelled"
Pinned by: `tests/unit/test_validation_extend.py::test_extend_undo`

### Requirement: Fresh probability sample after the sample was used
The system SHALL draw a new probability sample under a new name from the frame minus the records of a named used probability sample, its predecessors and the development sample, with the protocol's bands; record the predecessors in the design log; and make it the probability sample used for recall. The used sample is kept.
Surfaces: CLI `wider sample draw probability --name NAME --fresh-from SAMPLE`; UI card "Draw samples from the ranking", "Fresh sample instead of the used one".

#### Scenario: A fresh sample beside the used one
- **WHEN** `validation` holds 216 records and `wider sample draw probability --name validation2 --fresh-from validation` runs
- **THEN** `validation2` holds no record of `validation` or of the development sample, each band's N_h counts frame records outside both, its design log has `fresh_from: validation`, the 216 excluded and the development records excluded, and `validation` keeps its labels
- **AND** `POST /api/samples/draw?which=probability&name=validation2&fresh_from=validation` gives the same records, `protocol.sampling.probability_sample` becomes `validation2` in the same recording, the output says "validation2 is now the probability sample used for recall (protocol.sampling.probability_sample)", and undo removes `validation2` and restores the earlier name
- **AND** the card's "Fresh sample instead of the used one" with `validation` selected and the name `validation2` sends that request
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_sample_excludes_used`, `tests/unit/test_validation_fresh.py::test_fresh_cli_matches_api`, `tests/unit/test_validation_fresh.py::test_fresh_excludes_development`, `tests/unit/test_validation_fresh.py::test_fresh_sets_probability_sample`, `tests/unit/test_samples_page_node.py::test_fresh_sample_button`

#### Scenario: The fresh sample is read by default
- **WHEN** `validation2` was drawn fresh from `validation` and is labelled, and `wider recall main`, `GET /api/recall?protocol=main`, `wider report`, `wider export package`, `wider validation status`, `wider validation extend --band S2=10` and `wider validation stop` run without naming a sample, and the Recall page opens
- **THEN** each reads `validation2` (`protocol.sampling.probability_sample`), the Recall page's sample select shows it, `wider sample status` marks it "in use for recall", and `validation` is read only when named
- **AND** a later `wider sample draw development` takes no record of `validation` or `validation2`
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_sample_is_default_for_recall`

#### Scenario: Fresh from a fresh sample
- **WHEN** `validation2` was drawn fresh from `validation` and `wider sample draw probability --name validation3 --fresh-from validation2` runs
- **THEN** `validation3` holds no record of `validation`, `validation2` or the development sample, and its design log names both predecessors
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_from_fresh_excludes_chain`

#### Scenario: Fresh from an unknown or purposive sample
- **WHEN** `--fresh-from nosuch` or `--fresh-from development` runs
- **THEN** it exits 1 with "no sample 'nosuch'" or "development is not a probability sample", the route answers 404 or 400, and nothing is drawn
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_from_refusals`

#### Scenario: Undo of a fresh draw
- **WHEN** `validation2` was drawn fresh from `validation` and the researcher undoes the draw in History (or `wider history undo ID`)
- **THEN** `validation2` is removed and `protocol.sampling.probability_sample` is `validation` again
- **AND** once `validation2` holds a label, the undo is refused with "validation2 already has labels; its draw cannot be undone" and nothing changes
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_undo`

#### Scenario: Redrawing a fresh sample
- **WHEN** `validation2`, drawn fresh from `validation` and still unlabelled, is redrawn with a plain `wider sample draw probability` (or the Draw card's "Draw samples")
- **THEN** the redraw again holds no record of `validation` or the development sample, its design log keeps `fresh_from` and the chain, and the output says "validation2 was drawn fresh from validation; the redraw leaves out the same records"
- **AND** `wider sample draw probability --name validation3` without `--fresh-from` leaves `protocol.sampling.probability_sample` at `validation2` and names it in the output
Pinned by: `tests/unit/test_validation_fresh.py::test_fresh_redraw_keeps_chain`

### Requirement: Criteria state on each validation label
The system SHALL store with each label and adjudication on a probability sample the codebook version that stood when it was saved, whether that version was frozen, and whether a recall result on that sample had been seen. Outputs SHALL count labels made before and after the freeze of the latest frozen version, and after a result was seen.
Surfaces: CLI `wider sample status`, `wider recall`, `wider report`; UI Samples and labelling page, sample card; Recall page, "Validation checks".

#### Scenario: Labels before and after the freeze
- **WHEN** reviewer R1 labels 10 `validation` records while codebook v2 is approved, then v2 is frozen, then R1 labels 30 more
- **THEN** the first 10 labels have `codebook_version` 2 and `criteria_frozen` false, the 30 have true, and `wider sample status --json`, the sample card and the report say "40 labels: 10 before the criteria were frozen, 30 after"
- **AND** the count of 10 stays after those labels are re-checked; a draft codebook version that is neither approved nor frozen is never recorded
Pinned by: `tests/unit/test_validation_checks.py::test_criteria_state_on_labels`, `tests/unit/test_samples_page_node.py::test_criteria_counts_on_card`

#### Scenario: Criteria frozen again
- **WHEN** R1 labels 20 `validation` records under frozen v2, then v3 is frozen, then R1 labels 10 more
- **THEN** the 20 count as before the freeze, the outputs say "30 labels: 20 before the criteria were frozen, 10 after; the criteria changed after 20 labels (v2 to v3)", and the criteria change awaits an assessment (see "Assessing a criteria change")
Pinned by: `tests/unit/test_validation_checks.py::test_refreeze_counts_as_before`

#### Scenario: Labels saved after a result was seen
- **WHEN** a recall estimate was made on `validation` and R1 then saves 5 more labels
- **THEN** those labels have `after_look` true, and the outputs add "5 labels were saved after a recall result on this sample was seen"
Pinned by: `tests/unit/test_validation_checks.py::test_labels_after_look_counted`

#### Scenario: Changing a label keeps its criteria state
- **WHEN** R1 labelled a record include before v2 was frozen and, after the freeze, changes it to exclude in the ordinary queue
- **THEN** the label keeps `criteria_frozen` false and its codebook version, still counts as before the freeze, and stays due if the assessment made it due; only a save from the re-check queue records the new state
Pinned by: `tests/unit/test_validation_checks.py::test_relabel_keeps_before_state`

#### Scenario: Labels from before this version
- **WHEN** a project upgraded from before this change holds 12 `validation` labels with no criteria state and v2 is frozen
- **THEN** the outputs say "12 labels: criteria state not recorded (saved before this version)" beside the before and after counts, and they count as "not identifiable" in the assessment
Pinned by: `tests/unit/test_validation_checks.py::test_null_state_labels_counted`

### Requirement: Assessing a criteria change
The system SHALL show what changed between each early label's codebook version and the latest frozen one, list the labels it may affect, recommend keeping them, re-checking the affected or all, and record the researcher's assessment, which alone makes labels due.
Surfaces: CLI `wider label criteria [SAMPLE] [--assess yes|no --scope affected|all --note T]`; UI sample card.

#### Scenario: No substantive change
- **WHEN** 20 `validation` labels were made under v2, v3 is frozen with only a reworded example, and the researcher runs `wider label criteria validation --assess no --note "wording only"`
- **THEN** `wider label criteria validation` first lists the change ("other changes: 1; elements changed: 0; reasons changed: 0") with "Recommended: keep the labels", and after the assessment no label is due, the outputs say "Criteria changed after 20 labels (v2 to v3); assessed as not substantive by R1: wording only", and History holds "R1 assessed a criteria change"
Pinned by: `tests/unit/test_validation_criteria.py::test_assess_not_substantive`, `tests/unit/test_validation_criteria.py::test_criteria_cli_matches_api`

#### Scenario: Affected labels
- **WHEN** 6 includes, 2 maybes and 12 excludes were labelled under v2, v3 changes element "population" and removes reason code E3, 3 excludes carry E3 and 2 carry no reason code
- **THEN** the affected set holds the 8 includes and maybes and the 3 E3 excludes, the 2 excludes without a reason count as "not identifiable", the recommendation is "re-check the 11 affected labels (2 cannot be identified; re-check all 20 to cover them)", and `--assess yes --scope affected` makes the 11 due
Pinned by: `tests/unit/test_validation_criteria.py::test_affected_set`

#### Scenario: Large change
- **WHEN** v3 changes more than half of v2's elements, or no label of the set has a codebook version
- **THEN** the recommendation is "re-check all 20 early labels", and `--scope all` makes all 20 due
Pinned by: `tests/unit/test_validation_criteria.py::test_large_change_recommends_all`

#### Scenario: Partial re-check disclosed
- **WHEN** the assessment scope is `affected` with 11 of 20 early labels due and all 11 re-checked
- **THEN** the outputs, report and methods draft say "9 of the 20 early labels were not re-checked under v3; they are not independently verified against the frozen criteria."
Pinned by: `tests/unit/test_validation_criteria.py::test_partial_recheck_disclosed`

#### Scenario: Unassessed change
- **WHEN** a criteria change has no assessment
- **THEN** no label is due, and the sample card, `wider recall` and the report say "The criteria changed after 20 labels (v2 to v3) and the change has not been assessed: wider label criteria validation"
Pinned by: `tests/unit/test_validation_criteria.py::test_unassessed_change_line`

### Requirement: Blind re-check of labels due after an assessment
The system SHALL offer each reviewer a blind, shuffled queue of their own validation labels made due by an assessment, saying they are validation records only; a save keeps the original and revised labels, both codebook versions and a reason.
Surfaces: CLI `wider label recheck [SAMPLE] --reviewer R [--export OUT]`, `wider label import --queue FILE`, `wider label revisions SAMPLE`; UI drawer mode "Re-check", sample card "Revised labels".

#### Scenario: Re-checking the due labels
- **WHEN** the 11 labels of "Affected labels" are due for R1 and R1 opens the re-check queue
- **THEN** the queue holds those 11 in an order shuffled with the project seed, with title and abstract only (no earlier label, no screening decision, no band or weight), `GET /api/labels/queue?mode=recheck&reviewer=R1` returns the same 11, each with an opaque `item` token and neither `sample`, `sample_id` nor `record_id`, `wider label recheck validation --reviewer R1 --export out.csv` holds the same 11 with empty decisions and an `item` column, no sample column (without SAMPLE: every probability sample), and each says "This queue holds validation records only, so a reviewer can tell they are validation records."
- **AND** after R1 confirms 10 and changes one include to exclude with reason "population now adults only", the outputs say "11 labels re-checked under v3; 1 changed", History holds "R1 saved a re-check label" (no sample name, no record id), `wider label revisions validation` lists the original and revised label with v2, v3 and the reason, and undo restores the earlier label
- **AND** in this mode the drawer shows the title, abstract and label form only, with no notes or History panel, and the drawer and `wider label recheck` say "Blind to the queue only: the History page still lists earlier labels."
Pinned by: `tests/unit/test_validation_checks.py::test_recheck_queue_blind`, `tests/unit/test_validation_checks.py::test_recheck_counts_and_undo`, `tests/unit/test_validation_checks.py::test_recheck_cli_matches_api`, `tests/unit/test_validation_checks.py::test_queue_save_history_blind`, `tests/unit/test_validation_criteria.py::test_revisions_kept`, `tests/unit/test_drawer_keys_node.py::test_drawer_recheck_and_second_modes`

#### Scenario: Re-checked labels leave the queue
- **WHEN** R1 has saved all 11 from the re-check queue under v3, and later v4 is frozen and assessed with scope `all`
- **THEN** after the saves the queue says "nothing to re-check: no label is due after the assessment", and after the v4 assessment the labels are due again
Pinned by: `tests/unit/test_validation_checks.py::test_rechecked_leave_queue`

#### Scenario: Re-check from the CLI
- **WHEN** R1 fills the exported re-check file and runs `wider label import --queue out.csv`
- **THEN** only the records of the re-check queue are overwritten, each with `recheck_from` set to the earlier decision, and the labels, counts, revisions and history equal those of the same decisions saved in the drawer; a row with an unknown token is skipped and counted
Pinned by: `tests/unit/test_validation_checks.py::test_recheck_import_matches_drawer`

#### Scenario: Saving by token
- **WHEN** the drawer saves a re-check item with `POST /api/labels` `{"item": TOKEN, "decision": "exclude"}`, a second save sends an unknown token, and a third sends `"recheck": true` with a record id and no token
- **THEN** the first is saved to the token's sample and record as a re-check (from the token's mode), the second is refused with 400 "unknown queue item", the third with 400 "a re-check is saved from the re-check queue", and nothing is stored for those two
- **AND** a body with both `item` and `sample_id`, or with neither `item` nor `sample_id` and `record_id`, is refused with 400 "give either a queue item or a sample and record"; `wider label import` with both SAMPLE FILE and `--queue FILE`, or with neither, exits 1 with "give either SAMPLE FILE or --queue FILE"
Pinned by: `tests/unit/test_validation_checks.py::test_label_by_token`, `tests/unit/test_validation_checks.py::test_label_import_forms`

#### Scenario: One file across samples
- **WHEN** `validation` and `validation2` both have records due for R1 and `wider label recheck --reviewer R1 --export out.csv` runs without SAMPLE
- **THEN** the file holds rows of both samples with tokens and no sample column, and `wider label import --queue out.csv` saves each row to its own sample, as the drawer would
Pinned by: `tests/unit/test_validation_checks.py::test_queue_round_trip_across_samples`

#### Scenario: Nothing to re-check
- **WHEN** no codebook version is frozen, or no label of the reviewer is due
- **THEN** the queue is empty and says "no frozen codebook: freeze the criteria first (wider construct freeze codebook VERSION)" or "nothing to re-check: no label is due after the assessment"
Pinned by: `tests/unit/test_validation_checks.py::test_recheck_nothing_to_do`

#### Scenario: A second reviewer for the re-check
- **WHEN** the 11 labels are due and `wider label second-reviewer validation --reviewer R2 --recheck` runs (route `mode=second&recheck=true`)
- **THEN** R2's queue holds the 11 not first labelled by R2, shuffled, blind, with no development records, and the outputs count "11 of 11 re-checked labels have a second reviewer" once R2 has labelled them
Pinned by: `tests/unit/test_validation_criteria.py::test_second_reviewer_recheck`

### Requirement: Second reviewer on validation includes and maybes
The system SHALL recommend a second reviewer on the validation includes and maybes and count how many have one. A second-reviewer queue SHALL hold those first labelled by someone else, shuffled with up to as many random first-label excludes and no development records, showing no first label; outputs say the queue was enriched.
Surfaces: CLI `wider label second-reviewer [SAMPLE] --reviewer R [--recheck] [--export OUT]`, `wider label import --queue FILE`; UI drawer mode "Second reviewer"; Recall.

#### Scenario: Enriched second-reviewer queue
- **WHEN** R1 labelled `validation` with 14 includes and maybes and 186 excludes, the development sample has no label by anyone but R2, and R2 opens the second-reviewer queue
- **THEN** it holds the 14 and 14 of the 186 chosen with the project seed, shuffled, showing no label of R1, and `wider label second-reviewer validation --reviewer R2 --export out.csv` and `GET /api/labels/queue?mode=second&samples=validation&reviewer=R2` hold the same 28, each with an `item` token and neither `sample`, `sample_id` nor `record_id`
- **AND** the Recall page, `wider recall` and the report say "A second reviewer is recommended on validation includes and maybes: recall is only as good as the labels. 0 of 14 have one."; once R2 has labelled them, "14 of 14 have a second reviewer (queue enriched with 14 excludes)"
Pinned by: `tests/unit/test_validation_checks.py::test_second_reviewer_queue_enriched`, `tests/unit/test_validation_checks.py::test_second_reviewer_recommendation_line`, `tests/unit/test_validation_checks.py::test_second_reviewer_cli_matches_api`

#### Scenario: A reviewer cannot be their own second reviewer
- **WHEN** R1 made every first label of the 14 includes and maybes and asks for the second-reviewer queue, and in a second project R2 asks for it after making the first labels of 4 of the 14
- **THEN** R1's request exits 1 with "R1 made the first labels; choose another reviewer" (the route answers 400), and R2's queue holds the other 10 with 10 excludes and says "4 records you labelled first are left out"
- **AND** with fewer excludes than includes and maybes, the queue says "this queue has fewer excludes than includes and maybes, so it is not evenly mixed"
Pinned by: `tests/unit/test_validation_checks.py::test_second_reviewer_not_self`

### Requirement: Sample unlocks in the methods text and the package
The system SHALL record every sample unlock in the methods draft (one sentence per unlock: sample, date, author, and whether a recall result on it had been seen) and in the replication package as `sample_unlocks.json` with the keys sample, at, by and recall_seen. Without a sample unlock both stay as they are.
Surfaces: CLI `wider export methods`, `wider export package`; UI Recall page, methods card and replication package card.

#### Scenario: One unlock after recall was seen
- **WHEN** "ana" unlocked "validation" with `--yes` after a recall result on it was seen, and the methods draft and package are built
- **THEN** the methods draft says "The validation sample was unlocked on <date> by ana after its recall result had been seen."
- **AND** the package holds `sample_unlocks.json` with one row of exactly the four keys and no record id, label or abstract
- **AND** a project without an unlock has neither the sentence nor the file
Pinned by: `tests/unit/test_sample_lock.py::test_sample_unlock_in_methods_and_package`

### Requirement: The labelling page says who signs
The system SHALL show on the Labelling queue, in normal black text, the name that signs labels, notes and undo, and SHALL say once when the server's `WIDER_REVIEWER` differs from it ("the command line signs as bo"). The name is the one WIDER works as (D33).
Surfaces: UI Labelling queue and record drawer; CLI `--by` or `WIDER_REVIEWER` (no page name is used on the command line).

#### Scenario: Name and environment differ
- **WHEN** WIDER works as "ana" and the server runs with `WIDER_REVIEWER=bo`
- **THEN** the page reads "Signing as ana" and "the command line signs as bo", each once, in normal black text
- **AND** with `WIDER_REVIEWER` unset or "ana" the second sentence is absent, and with no name set the page reads "no name yet: labels need your name" with a "Set your name" button
Pinned by: `tests/unit/test_samples_page_node.py::test_labelling_page_says_who_signs`

### Requirement: A re-check queue is only for the page's own name
The system SHALL refuse on the page a re-check or second-reviewer queue for a `reviewer` other than the name the page signs with: "this queue is for the name you sign with". The page asks with the name WIDER works as (D33). On the command line `--reviewer` names the queue as today.
Surfaces: UI Labelling queue (`GET /api/labels/queue?mode=recheck|second`); CLI `wider label recheck`, `wider label second-reviewer` (unchanged).

#### Scenario: Another reviewer's re-check queue
- **WHEN** the page signs as "ana" and asks for `mode=recheck&reviewer=bo`, then for `mode=second&reviewer=ana&blind=false`
- **THEN** the first is refused with 400 "this queue is for the name you sign with" and shows none of bo's labels
- **AND** the second holds only items, titles and abstracts, and `wider label recheck --reviewer bo --json` still lists bo's queue
- **AND** both queues stay blind
Pinned by: `tests/unit/test_records.py::test_recheck_queue_only_for_the_signing_name`

#### Scenario: A first name typed and Enter pressed
- **WHEN** WIDER works as "ana", the re-check queue is chosen and "Open queue" pressed
- **THEN** the request carries "ana" as reviewer and as author, and her re-check queue opens
Pinned by: `tests/unit/test_drawer_keys_node.py::test_open_queue_opens_the_own_recheck_queue`

### Requirement: Corrections to labels and adjudications
The system SHALL treat a change to a stored label or adjudication on a locked sample, or on a sealed validation record, as a correction: it needs a reason, is saved and signed as usual, and is kept in `label_corrections` with the old and new values. Without a reason nothing is saved; an equal save writes nothing. First labels, re-checks and package imports are not corrections.
Surfaces: UI drawers; CLI `--correction-reason` of `wider label import`, `adjudicate`.

#### Scenario: A correction from the drawer and from the CLI
- **WHEN** `dev` is locked, R1's label on record 1 is include, and R1 changes it to exclude in the drawer, types "misread the population" in the dialog and presses "Save correction"; and in a copy of the project the same change is imported with `wider label import dev changed.csv --reviewer R1 --overwrite --correction-reason "misread the population"`
- **THEN** both store exclude for R1 on record 1 and one row in `label_corrections` with old decision include, new decision exclude, the reason, author R1 and the time, and History holds one `label_correction` change "R1 corrected record 1 in sample dev" that names neither decision nor reason
- **AND** `wider label revisions dev --json` lists it with `cause` "correction", the old and new decision and `why`
- **AND** the import without `--correction-reason` exits 1 with "1 row changes a saved label on the locked sample dev; give --correction-reason" and imports nothing
Pinned by: `tests/unit/test_label_completion.py::test_change_on_a_locked_sample_needs_a_correction_reason`, `tests/unit/test_label_completion.py::test_correction_cli_and_route_same`, `tests/unit/test_label_completion.py::test_revisions_list_corrections`, `tests/unit/test_drawer_keys_node.py::test_correction_reason_dialog_posts_again`

#### Scenario: Corrections on the sample card
- **WHEN** R1's corrections on records 1 and 2 of the purposive `dev` are saved
- **THEN** `GET /api/samples` and `wider sample status --json` give `corrections` 2 for `dev`, and the sample card says once "2 corrected labels", with `wider label revisions dev` in its closed "Command line" fold and no decision, record or reason on the page
Pinned by: `tests/unit/test_label_completion.py::test_corrections_on_the_sample_card`, `tests/unit/test_samples_page_node.py::test_corrected_labels_list`

#### Scenario: Cancel, or nothing changed
- **WHEN** the dialog of the scenario above is cancelled, and in another case R1 saves include again on record 1 with the same reason, ticks and note
- **THEN** nothing is saved and the drawer says "nothing was saved"; the equal save writes nothing, asks for no reason and adds no History change
Pinned by: `tests/unit/test_label_completion.py::test_identical_save_writes_nothing`, `tests/unit/test_drawer_keys_node.py::test_correction_dialog_cancel_saves_nothing`

#### Scenario: The command-line and route twins
- **WHEN** a correction is made with `wider label import --queue FILE --correction-reason TEXT`, `wider label adjudicate SAMPLE RECORD_ID DECISION --correction-reason TEXT`, and `POST /api/labels` or `POST /api/labels/adjudicate` with `correction_reason`
- **THEN** each stores the same correction as the drawer would, and a reason longer than 500 characters is refused with "a correction reason is at most 500 characters" (exit 1, HTTP 400); a file import refused so saves none of its rows
Pinned by: `tests/unit/test_label_completion.py::test_correction_cli_and_route_same`, `tests/unit/test_label_completion.py::test_too_long_correction_reason_imports_nothing`

#### Scenario: A blind correction names no sample
- **WHEN** R1 changes a label from the blind queue of the locked `dev` without a reason, through `POST /api/labels` with the row's item
- **THEN** the route answers 409 with `correction_needed` true (the CLI's twin exits 1) and "a change to a saved label here needs a reason for the correction", which contains neither "dev" nor "sample", and the History summary of the correction saved afterwards reads "R1 corrected a label" with no record id
Pinned by: `tests/unit/test_label_completion.py::test_blind_correction_refusal_names_no_sample`

#### Scenario: Adjudication drawer
- **WHEN** an adjudication on the locked `dev` is changed in the adjudication drawer
- **THEN** the same dialog asks the reason, and the saved correction has target `adjudication`, the old and new adjudicated decision and the adjudicator as author
Pinned by: `tests/unit/test_drawer_keys_node.py::test_adjudication_correction_dialog`, `tests/unit/test_label_completion.py::test_adjudication_on_a_locked_sample`

### Requirement: Completeness of a sample's labels
The system SHALL report for each sample whether every record has a final label (labels that all agree, or an adjudication); on a probability sample a final label also needs one independent label, and an open review package keeps it incomplete. `completeness(project, sample_name, min_reviewers=1)` returns at least `complete`, `total`, `final`, `missing`, `open`, `sealed`, `packages_out`.
Surfaces: CLI `wider sample status --json`; API `GET /api/samples` (key `completeness`).

#### Scenario: What is left
- **WHEN** `validation` has 20 records, none sealed, 14 labelled by R1 in the blind queue, record 15 labelled only in the unblinded queue, record 16 labelled include by R1 and exclude by R2 in the blind queue, record 17 the same but adjudicated, and 3 unlabelled
- **THEN** `completeness` gives `sample`, `kind`, `rule`, `complete` false, `total` 20, `final` 15, `missing` the ids of records 15, 16 and the 3 unlabelled ones in queue order, `no_label` the 3, `not_independent` record 15, `open` record 16, `sealed` [], `packages_out` [], and `lines` ["3 of 20 records have no label", "1 record has no independent label (made in an unblinded queue, imported, or saved before provenance was recorded)", "1 record has labels that disagree and is not adjudicated"]
- **AND** after the 3 are labelled, record 15 is labelled by R2 in the blind queue with the same decision and record 16 is adjudicated, `complete` is true and `lines` is empty; `wider sample status --json` and `GET /api/samples` give the same dict without the id lists, with `n_missing`, `n_no_label`, `n_not_independent`, `n_open` and `n_sealed` in their place
- **AND** on a purposive sample with the same labels, record 15 counts as final (any label counts there)
- **AND** a sample with no record is incomplete with the line "the sample has no records"
- **AND** a sealed record counts as final through its reference, whatever a later supplementary label says, and is listed in `sealed`
- **AND** with `min_reviewers=2` a record with one label is not final, and a record with two agreeing labels of distinct reviewers, or an adjudication, is
Pinned by: `tests/unit/test_label_completion.py::test_completeness_contract`, `tests/unit/test_label_completion.py::test_completeness_min_reviewers`, `tests/unit/test_label_completion.py::test_completeness_in_sample_status_and_api`

#### Scenario: A validation package open
- **WHEN** every record of `validation` has a final label and R3's second-reviewer package on it is out, and the same holds for an independent package on `dev`
- **THEN** `validation` is incomplete with `packages_out` ["R3"] and the line "R3's review package on validation is not finished", and `dev` is complete
- **AND** after an import that decided 10 of the package's 20 items it is still incomplete with the same line; after a later import decides the rest, or after the package is withdrawn, it is complete unless the labels bring a disagreement
- **AND** the withdraw keeps the 10 imported labels and their History, and no record that was final becomes incomplete
Pinned by: `tests/unit/test_label_completion.py::test_completeness_validation_package_out`

### Requirement: Provenance of each label
The system SHALL record in each label's `made_in` how it was made: a package id or `blind queue` (independent); `unblinded queue`, `file import`, `sample import`, or `wider` for older labels (independent only with a stated basis, which a later save drops). Independence is never assumed.
Surfaces: CLI `wider label provenance`, `--independent BASIS`; UI sample card.

#### Scenario: How labels were made
- **WHEN** R1 labels record 1 from the blind queue, record 2 with the blind switch off, record 3 through a second-reviewer item, record 4 arrives in R3's package, and `wider label import validation f.csv --reviewer R4` imports record 5
- **THEN** their `made_in` values are `blind queue`, `unblinded queue`, `blind queue`, the package id and `file import`, `wider sample status --json` gives the counts under `provenance`, and records 1, 3 and 4 count as independent, records 2 and 5 do not
- **AND** the same import with `--independent "labelled blind in Rayyan"` counts record 5 as independent and keeps the basis; when R4 later changes that label in the unblinded queue, the basis is dropped and the record is not independent; an undo of that change brings label and basis back
- **AND** when R1 then switches the blind switch on and saves record 2 again from the blind queue, with the same decision or another one, its `made_in` stays `unblinded queue` and record 2 is still not independent
- **AND** a re-check save by R1 on record 2 keeps `unblinded queue`, and a package of R1 with `--earlier re-review` that brings record 2 back as a repeat item leaves record 2 not independent after its import (the replaced label is read from the import's History), and a later blind save by R1 over that repeat label writes `unblinded queue`
- **AND** a re-check save by R1 over her label on record 4 made in a package (not a repeat) writes `blind queue`, so a later export of that package does not overwrite it
Pinned by: `tests/unit/test_label_completion.py::test_provenance_of_each_label`, `tests/unit/test_label_completion.py::test_label_callers_name_made_in`, `tests/unit/test_label_completion.py::test_a_later_blind_save_never_raises_independence`, `tests/unit/test_label_completion.py::test_repeat_package_label_keeps_independence`

#### Scenario: Labels from before this change
- **WHEN** a project from before this change holds on `validation` R1's 20 labels made in WIDER (`wider`), R2's 3 from the second-reviewer queue (`blind queue`) and R3's 4 from a package
- **THEN** R2's and R3's labels count as independent at once and R1's do not, and the refusal of recall names R1's 20 with "wider label provenance validation --reviewer R1 --independent BASIS"
Pinned by: `tests/unit/test_label_completion.py::test_older_labels_keep_their_provenance`

#### Scenario: Declaring earlier labels independent
- **WHEN** R1's 20 `wider` labels and 1 `unblinded queue` label are on `validation`, and `wider label provenance validation --reviewer R1 --independent "labelled in the blind queue, the default" --by ana` runs; in a copy, the sample card's "Declare labels independent" dialog is filled with R1 and the same basis
- **THEN** both declare the 20 with that basis, History holds one `label_provenance` change by ana that can be undone, and both say "1 label made in the unblinded queue stays not independent"
- **AND** the card shows the button only on a probability sample with labels whose independence is not established, with the line "R1: 20 labels without an established independence"; without a basis, `wider label provenance` exits 1 and the route answers 400 with "give --independent and the basis of independence", changing nothing
Pinned by: `tests/unit/test_label_completion.py::test_declaring_earlier_labels_independent`, `tests/unit/test_samples_page_node.py::test_declare_independent_dialog`

### Requirement: A locked sample's queue takes missing labels and corrections
The system SHALL let the Labelling queue open one locked sample's queue from its sample select with no row read-only: a missing label saves at once, a change to the reader's stored label asks for a correction reason first, and while blind no row shows how it was drawn. `wider label export` keeps its `read_only` column, now always "no".
Surfaces: UI Labelling queue; CLI `wider label export NAME OUT`, `wider label import NAME FILE`.

#### Scenario: Labelling the rest of a locked sample
- **WHEN** `dev` is locked with 10 of its 20 records labelled by "ana", and she chooses "dev" in the queue's sample select, opens the queue and presses 1 on an unlabelled record and 2 on a labelled one
- **THEN** no row shows a "read-only" chip, the "not labelled yet" filter lists the 10 unlabelled records, the first key saves include at once, and the second opens the correction dialog
- **AND** no row holds band, pool, rank, weight or a sample field, and with the blind switch off the rows show band and rank as an unblinded queue does
- **AND** `wider label export dev` writes the 20 records with `read_only` "no" on each, and its import saves the same decisions as the same file without that column
Pinned by: `tests/unit/test_samples_page_node.py::test_locked_sample_queue_takes_labels`, `tests/unit/test_records.py::test_label_export_marks_a_locked_sample_read_only`

### Requirement: A label change after a lock in another tab asks for a reason
The system SHALL accept a missing label posted from a queue shown before its sample was locked in another tab, and SHALL answer a change to a stored label there with the correction request, whose blind text names no sample, so the page asks for the reason.
Surfaces: UI Labelling queue and drawer (keys 1/2/3, decision buttons); CLI `wider label import NAME FILE --overwrite --correction-reason TEXT`.

#### Scenario: Lock in another tab
- **WHEN** "ana" has the blind queue open, `dev` is locked from another tab, and she presses 1 on a record of `dev` she has not labelled and then 2 on one she has
- **THEN** the first is saved; the second opens the correction dialog and saves nothing until a reason is given
- **AND** the route's error text contains neither "dev" nor "sample"
Pinned by: `tests/unit/test_records.py::test_label_after_lock_in_another_tab_names_no_sample`, `tests/unit/test_drawer_keys_node.py::test_key_after_lock_elsewhere_asks_for_a_reason`

### Requirement: Locked samples in the re-check and second-reviewer queues
The system SHALL include locked probability samples in the re-check queue and the second-reviewer queue: a second reviewer's labels are missing labels, and a re-check save is documented by its criteria assessment and `label_revisions` row, so neither needs a correction reason.
Surfaces: CLI `wider label recheck`, `wider label second-reviewer`, `wider label import --queue FILE`; UI drawer modes "Re-check" and "Second reviewer".

#### Scenario: A locked validation sample
- **WHEN** `validation` is locked, R1 labelled 14 includes and maybes and 186 excludes, 11 labels are due after an assessment, and R2 and R1 open the second-reviewer and re-check queues
- **THEN** R2's queue holds the 28 records of "Enriched second-reviewer queue" and R1's the 11 due records, with no line saying the sample is locked, and saves from both are stored without a correction reason
Pinned by: `tests/unit/test_label_completion.py::test_recheck_and_second_queues_serve_locked_samples`

### Requirement: Reviewers for the development sample
The system SHALL keep a visible setting on Samples and labelling, "Reviewers for the development sample: one / two (agreement or adjudication needed)", default one, applying to every purposive sample, with its CLI twin. A change is journalled and named in the methods text. With two, a record is complete when two or more distinct reviewers labelled it and all agree, or it is adjudicated.
Surfaces: UI Samples and labelling; CLI `wider protocol set sampling.development_reviewers 1|2`.

#### Scenario: Setting two reviewers
- **WHEN** the researcher sets two reviewers on the page, and `wider protocol set sampling.development_reviewers 2` runs on another synthetic project
- **THEN** both projects store 2, History lists the change signed by its author, the methods draft says the development sample was labelled by two reviewers with disagreements adjudicated, and the page shows the setting with its explanation in normal text
Pinned by: `tests/unit/test_dev_reviewers.py::test_setting_page_and_cli`

#### Scenario: Three reviewers
- **WHEN** with two required, ana, bo and cy labelled a record and cy disagrees
- **THEN** the record is not complete until it is adjudicated
Pinned by: `tests/unit/test_dev_reviewers.py::test_three_reviewers`

#### Scenario: Default one
- **WHEN** a project never set it
- **THEN** it reads one, and a record is complete with one final label as today
Pinned by: `tests/unit/test_dev_reviewers.py::test_default_one`
