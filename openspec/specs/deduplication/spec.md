# deduplication Specification

## Purpose
Deduplication (stage key `dedup`) turns the imported platform records in `raw_records` into one master record per work in `records`, after import and before abstract enrichment. It finds duplicates by DOI, exact normalised title and corroborated fuzzy title, merges them conflict-aware, sends ambiguous pairs to a manual queue the researcher decides, and writes PRISMA identification counts and numbered audit CSVs.

## Requirements

### Requirement: Stage A matches records by DOI
The system SHALL pair raw records that share a normalised DOI (method `exact_doi`), and SHALL flag such a pair as a DOI conflict when the title similarity is below `doi_conflict_title` (default 0.70) or the years differ by more than 2, unless the pair has a full citation match (method `exact_doi_full_citation_title_variant`).
Surfaces: CLI `wider dedup run`; UI Dedup page "Run deduplication" button.

#### Scenario: Same DOI in two exports
- **WHEN** one export has DOI `10.1000/abc` and another has `https://doi.org/10.1000/ABC` for the same title and year
- **THEN** both raw records end up in one master record whose `doi` is `10.1000/abc`
Pinned by: `tests/unit/test_dedup.py::test_engine_end_to_end`

### Requirement: Stage B matches records by exact normalised title
The system SHALL pair raw records with the same normalised title (method `exact_normalized_title`) and decide them with the diagnostic rules: auto-merge on `exact_title_full_citation`, `exact_title_same_year_two_locators` (a short generic title also needs a first-author or journal match) or `exact_title_verified_one_year_metadata_discrepancy`, and auto-separate on rules such as `publication_years_over_one_apart_with_citation_conflicts`.
Surfaces: CLI `wider dedup run`; UI Dedup page.

#### Scenario: Exact title with full citation
- **WHEN** two records have the same title, year, author, journal, volume and pages
- **THEN** the rule `exact_title_full_citation` merges them automatically
Pinned by: `tests/unit/test_dedup.py::test_rules_exact_title_and_fuzzy`

#### Scenario: Exact title with conflicting citations
- **WHEN** two records share a title but have different DOIs, authors, journals and years more than one apart
- **THEN** no merge rule applies and `publication_years_over_one_apart_with_citation_conflicts` marks them not duplicate

### Requirement: Stage C finds corroborated fuzzy title matches
The system SHALL compare titles of at least 15 characters within blocks (same first author, same 24-character title prefix, or a shared rare token, with years within 1 for author and token blocks) and keep pairs whose similarity, the larger of Jaro-Winkler and token cosine, is at least `jw_candidate` (default 0.85). Fuzzy pairs merge automatically only when corroborated by citation fields.
Surfaces: CLI `wider dedup run`; UI Dedup page.

#### Scenario: Corroboration rules for fuzzy pairs
- **WHEN** a fuzzy pair is evaluated
- **THEN** it merges through `high_similarity_full_citation` (similarity at least `jw_auto_merge`, default 0.97, and a full citation match)
- **AND** otherwise through `fuzzy_title_same_year_two_locators` only with similarity at least `jw_corroborated` (default 0.90), the same year, a first-author or journal match, at least two matching locators and no DOI or publication-family conflict

#### Scenario: Near-identical title
- **WHEN** "team meetings after errors" is compared with "team meeting after error" with the same citation
- **THEN** the similarity is at least 0.97 and `high_similarity_full_citation` merges them
- **AND** with a differing page the same pair merges through `fuzzy_title_same_year_two_locators`
Pinned by: `tests/unit/test_dedup.py::test_rules_exact_title_and_fuzzy`

#### Scenario: Similarity measures
- **WHEN** similarity is computed
- **THEN** identical strings score 1.0 and token cosine of an empty string is 0.0
Pinned by: `tests/unit/test_dedup.py::test_similarities`

### Requirement: Conflict-aware merging
The system SHALL apply accepted pairs with a union-find in method-priority order and MUST NOT join two clusters through a non-DOI pair when that would put conflicting DOIs or conflicting publication families (correction, dissertation, conference, book_chapter, preprint, journal_article) in one cluster; such a pair loses its auto-merge and is marked `cluster_level_conflict`.
Surfaces: CLI `wider dedup run`; UI Dedup page.

#### Scenario: Conference paper and journal article
- **WHEN** a journal article and a conference paper share an exact title
- **THEN** a publication-family conflict is flagged and no automatic merge rule applies
Pinned by: `tests/unit/test_dedup.py::test_rules_exact_title_and_fuzzy`

### Requirement: Manual queue of component pairs
The system SHALL send every candidate pair that is neither auto-merged nor auto-separated, and whose records are in different clusters not already settled as different, to a manual queue with one representative pair per pair of clusters, ordered by review priority rank and then by similarity, and store all pairs in `dedup_pairs` with decision `auto`, `queued` or `manual_*`.
Surfaces: CLI `wider dedup queue [--limit 30] [--all]`; UI Deduplication page, "Pairs to decide".

#### Scenario: Ambiguous title pair
- **WHEN** two records share a title but have different authors and journals with the same year
- **THEN** `wider dedup queue` shows one pending pair with stage `exact_normalized_title`
- **AND** the run summary counts it in `unresolved_manual_component_pairs_retained`
Pinned by: `tests/unit/test_dedup.py::test_engine_end_to_end`

### Requirement: Manual decisions are stored, journalled and applied on the next run
The system SHALL store a manual decision `duplicate`, `not_duplicate` or `unsure` (or `uncertain`) for a raw-record pair as `manual_same`, `manual_different` or `manual_uncertain` in `dedup_pairs`, journal it as an undoable `dedup_decision` change, and apply it at cluster level on every later run.
Surfaces: CLI `wider dedup decide RAW_A RAW_B DECISION [--by]`; UI Deduplication page, "Pairs to decide" list and pair view (`POST /api/dedup/decide`, HTTP 400 for an unknown decision).

#### Scenario: Confirm a duplicate
- **WHEN** the queued pair is decided as `duplicate` and deduplication runs again
- **THEN** unique records drop from 4 to 3 and `manually_confirmed_duplicate_pairs` is 1
- **AND** no pair is left pending
Pinned by: `tests/unit/test_dedup.py::test_engine_end_to_end`

### Requirement: Master records with field provenance
The system SHALL build one master record per cluster from its most complete member, taking the longest title, abstract and journal, the authors of the member with the most names, the most common DOI and year, the union of keywords, and recording in `field_sources` which raw record gave the title, abstract and DOI, with `n_sources`, `platforms` (joined with ";"), `abstract_words` and `thin` (words below `thin_words`, default 20).
Surfaces: CLI `wider dedup run`; UI Dedup page and record drawer.

#### Scenario: Three sources of one work
- **WHEN** a work appears once in one export and twice in another
- **THEN** its master has `n_sources` 3, platforms "scopus;wos" and the longer of the abstracts
Pinned by: `tests/unit/test_dedup.py::test_engine_end_to_end`

#### Scenario: Enriched abstract survives a re-run
- **WHEN** a master's abstract was filled by enrichment and deduplication runs again with no longer export abstract
- **THEN** the enriched abstract, its word count and its `abstract_source` are kept
Pinned by: `tests/unit/test_dedup.py::test_rerun_keeps_enriched_abstract`

### Requirement: Stable record ids across re-runs
The system SHALL keep each master's `record_id` and key for an unchanged cluster across re-runs, never reuse a retired id, and move downstream rows (screening decisions, labels, enrichment log, notes, tags and others) from a master that merges away to the surviving master; where both are in one ranking, the survivor's ranking row is kept.
Surfaces: CLI `wider dedup run`; UI Dedup page "Run deduplication".

#### Scenario: Plain re-run and a later merge
- **WHEN** deduplication runs again unchanged, and then after a manual merge
- **THEN** the first re-run reports 4 kept and 0 new with identical ids and keys
- **AND** after the merge the losing master's screening decision and enrichment log rows belong to the survivor
- **AND** a later merge of two masters both screened under the same protocol succeeds: the survivor's decision is kept and the other's is set aside, kept on record and named in History in words
Pinned by: `tests/unit/test_dedup.py::test_rerun_keeps_record_ids_and_remaps_merged_masters`

#### Scenario: Two screened records decided duplicates
- **WHEN** two records screened in the same run are decided "Duplicates, keep A" and deduplication runs again (`wider dedup run`, or "Apply decisions and check again")
- **THEN** the run succeeds; the kept record's screening calls, reused calls and decision stay, the other record's clashing ones are set aside (kept on record, not counted)
- **AND** one History entry, which cannot be undone, says which record's screening was kept, how many rows were set aside and where the two decisions disagreed; the CLI and the page say the same sentence, and none of it names an internal table
Pinned by: `tests/unit/test_dedup.py::test_merge_of_two_screened_records_sets_the_others_calls_aside`, `tests/unit/test_result_sets.py::test_dedup_covers_all_sets`

#### Scenario: Notes and tags follow the merge
- **WHEN** a master carrying notes and tags merges into another
- **THEN** the notes and tags move to the survivor, with identical tags by the same author kept once
Pinned by: `tests/unit/test_records.py::test_dedup_merge_moves_notes_and_tags_and_refuses_orphans`

#### Scenario: A merge after ranking
- **WHEN** two masters that are both in a ranking merge after a Duplicate decision
- **THEN** the re-run succeeds instead of refusing on `ranking_rows`
- **AND** the ranking keeps the survivor's row and rank and drops the merged-away master's row
Pinned by: `tests/unit/test_dedup.py::test_a_merge_after_ranking_keeps_the_survivors_ranking_row`

### Requirement: Unsafe re-runs are refused
The system MUST refuse a re-run, changing nothing, when both merging masters are in one sample (sample places, labels or adjudications) or a master that disappears without a successor still has downstream rows, notes or tags. Screening rows never refuse a run. The message names the records and what to do, never an internal table.
Surfaces: CLI `wider dedup run` (exit code 1); UI Dedup page (`POST /api/dedup/run` returns HTTP 409).

#### Scenario: Both masters in one sample
- **WHEN** two masters that are both in one sample would merge
- **THEN** the run fails saying both have sample places in the same sample and how to proceed (decide the pair Not duplicates or Unsure, undo the decision, or remove one record from the sample), without a table name
- **AND** the stored queue of the last run is unchanged
Pinned by: `tests/unit/test_dedup.py::test_labels_clash_is_refused_in_words`, `tests/unit/test_dedup.py::test_refused_run_leaves_the_run_queue_unchanged`

#### Scenario: Orphaned note
- **WHEN** a master carrying a note would disappear without a successor
- **THEN** the run fails naming the record and "1 notes", and the note is kept
Pinned by: `tests/unit/test_records.py::test_dedup_merge_moves_notes_and_tags_and_refuses_orphans`

### Requirement: PRISMA counts and numbered audit CSVs
The system SHALL write on every run the numbered audit CSVs `03_candidate_pairs`, `04e_automatic_decision_rule_summary`, `05_manual_duplicate_review`, `07_record_provenance_crosswalk`, `08_deduplication_summary`, `09_prisma_counts` and `12_deduplication_match_edges` to `exports/dedup/`, and report PRISMA identification rows ending in a final or provisional remaining-records row.
Surfaces: CLI `wider dedup prisma`, `wider dedup run` (prints the audit path); UI Deduplication flow chart.

#### Scenario: Pairs still pending
- **WHEN** unresolved manual pairs remain after a run
- **THEN** the rows are records retrieved per platform, "Total records before deduplication" and "Duplicate records removed before screening"
- **AND** the last row reads "Provisional records remaining after automated deduplication"
Pinned by: `tests/unit/test_dedup.py::test_prisma_remaining_is_provisional_while_pairs_are_open`, `tests/unit/test_dedup_page_node.py::test_counts_after_a_run_with_open_pairs`

#### Scenario: Queue resolved
- **WHEN** the last manual pair is decided as `duplicate` and deduplication runs again
- **THEN** the last PRISMA row is "Records remaining for title and abstract screening" with 3
- **AND** `exports/dedup/05_manual_duplicate_review.csv` exists
Pinned by: `tests/unit/test_dedup.py::test_engine_end_to_end`

### Requirement: Stage result and summary
The system SHALL store the run summary in the meta keys `dedup_summary` and `dedup_run_at` and record a `dedup` stage result with the headline "N unique records from M imported; K duplicates merged.", metrics for unique records, duplicates merged, pending pairs and manual decisions applied, a warning tone while pairs are pending, and next stage `enrich`.
Surfaces: CLI `wider dedup run`; UI Deduplication page (flow chart, "Details", `GET /api/dedup`).

#### Scenario: Pending pairs
- **WHEN** a run leaves 3 manual pairs pending
- **THEN** the stage result for `dedup` has a warning tone and its metrics show the unique records
Pinned by: `tests/unit/test_stage_result.py::test_summarize_dedup_pending_is_a_warning`

#### Scenario: Run from the UI
- **WHEN** `POST /api/dedup/run` is called after importing 12 distinct records
- **THEN** the response has `unique_records` 12 and a stage result for stage `dedup`
Pinned by: `tests/unit/test_stage_result.py::test_engines_record_and_api_wraps`

### Requirement: Side-by-side pair review in the record drawer
The system SHALL show the pair selected in the Deduplication page's list large beside the list, in the record drawer docked in the page: title, authors, year, journal, volume, pages, DOI and abstract of records A and B field by field, each with a match word, and let the researcher decide it with keys 1 to 4 (Duplicates, keep A or B; Not duplicates; Unsure), undo offered.
Surfaces: UI Deduplication page, list and pair view (CLI equivalent `wider dedup decide`).

#### Scenario: Queue carries both abstracts
- **WHEN** the Deduplication page loads its queue
- **THEN** each queued pair includes `abstract_a` and `abstract_b`
- **AND** a decision in the pair view posts to `/api/dedup/decide`
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`, `tests/unit/test_records.py::test_static_drawer`

#### Scenario: Big buttons show their keys
- **WHEN** a pending pair is selected
- **THEN** the pair view asks "Are these the same paper?" and shows the buttons "Duplicates, keep A" (pink), "Duplicates, keep B" (purple), "Not duplicates" (green) and "Unsure" (yellow) with the keys 1 to 4 on them
- **AND** pressing 4 sends the decision `uncertain`, and the pair's row then reads "Unsure" with its icon
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_pair_view_header_buttons_and_signing_name`

#### Scenario: Keep A or keep B
- **WHEN** the researcher decides a pair "Duplicates, keep B" in the pair view, or runs `wider dedup decide RAW_A RAW_B duplicate --keep B`, and then runs deduplication again
- **THEN** the merged record shows record B's title, journal and year, keeps its record id, and a later run keeps the choice
- **AND** the pair view of that pair shows "Duplicates, keep B" pressed; a kept record named for a decision other than duplicate, or a record outside the pair, is refused
Pinned by: `tests/unit/test_dedup.py::test_decide_keep_names_the_record_the_merged_record_shows`, `tests/unit/test_dedup_page_node.py::test_a_decided_pair_presses_its_kept_record`

### Requirement: Manual decisions are validated before they are stored
The system MUST refuse a manual dedup decision, storing and journalling nothing, when the decision is not `duplicate`, `not_duplicate` or `unsure` (or `uncertain`), when either raw record id does not exist in `raw_records`, or when both ids are the same. The refusal names the problem in plain words.
Surfaces: CLI `wider dedup decide RAW_A RAW_B DECISION` (exit code 1, no traceback); UI Deduplication page, "Pairs to decide" list and pair view (`POST /api/dedup/decide`, HTTP 400).

#### Scenario: Misspelt decision on the command line
- **WHEN** the researcher runs `wider dedup decide 3 7 dupe`
- **THEN** the command prints "decision must be duplicate, not_duplicate or unsure" and exits with code 1
- **AND** `dedup_pairs` and the change journal are unchanged
Pinned by: `tests/unit/test_dedup.py::test_decide_validates_before_recording`

#### Scenario: Unknown raw record
- **WHEN** a decision is posted for raw records 3 and 9999 and record 9999 does not exist
- **THEN** the request fails with HTTP 400 naming raw record 9999
- **AND** no `dedup_pairs` row is stored for the pair
Pinned by: `tests/unit/test_dedup.py::test_decide_validates_before_recording`

#### Scenario: Same record twice
- **WHEN** a decision is given for raw records 5 and 5
- **THEN** it is refused because a record cannot be a duplicate of itself
Pinned by: `tests/unit/test_dedup.py::test_decide_validates_before_recording`

### Requirement: Importing manual decisions from a review CSV
The system SHALL import manual decisions from WIDER's own `05_manual_duplicate_review.csv` (`raw_a`, `raw_b`, titles, `manual_decision`) and from the original pipeline's file (`title_raw_a`, `database_a`, `year_a`, ...), matching rows by raw id when that record has the row's title, else by title, platform and year, and counting rows without a valid decision as blank.
Surfaces: CLI `wider dedup import-decisions FILE`; UI none (decisions are made in the Dedup queue).

#### Scenario: Round trip through WIDER's own audit file
- **WHEN** deduplication leaves two pairs queued, the researcher fills `manual_decision` with `duplicate` for one row of `exports/dedup/05_manual_duplicate_review.csv`, leaves the other blank and imports the file
- **THEN** the import reports 1 duplicate and 1 blank
- **AND** the next run merges that pair and leaves 1 pair pending

#### Scenario: File in neither format
- **WHEN** the CSV has neither the `raw_a`/`raw_b` columns nor the `title_raw_a`/`database_a` columns
- **THEN** the import fails naming the expected columns, with exit code 1, and stores nothing

#### Scenario: Row from another project
- **WHEN** a row's `raw_a` exists but has a different title than the row and no record matches its title, platform and year
- **THEN** the row is counted as unmatched and no decision is stored for it

### Requirement: Remaining-records row is provisional while pairs are unsettled
The system SHALL label the last PRISMA row "Provisional records remaining after automated deduplication" while any manual pair is pending or decided `uncertain`, and "Records remaining for title and abstract screening" only once every queued pair is decided `duplicate` or `not_duplicate`, in the CLI and `09_prisma_counts.csv`; the page's "Records left" box says "provisional" or "final".
Surfaces: CLI `wider dedup prisma`, `wider dedup run`; UI box "Records left"; `09_prisma_counts.csv`.

#### Scenario: Uncertain pair left
- **WHEN** the only queued pair is decided `uncertain` and deduplication runs again
- **THEN** the "Records left" box of the Deduplication page says "provisional", and the last row of `wider dedup prisma` and of `09_prisma_counts.csv` reads "Provisional records remaining after automated deduplication"
- **AND** after the pair is decided `not_duplicate` and deduplication runs again, the box says "final" and the last row reads "Records remaining for title and abstract screening"
Pinned by: `tests/unit/test_dedup.py::test_prisma_remaining_is_provisional_while_pairs_are_open`, `tests/unit/test_dedup_page_node.py::test_an_unsure_pair`, `tests/unit/test_dedup_page_node.py::test_the_main_button_runs_and_shows_errors_beside_the_chart`

### Requirement: Decisions since the last run are counted
The system SHALL report with the Dedup page data the number of pairs decided by hand after the last deduplication run, so the heading of the "Pairs to decide" list counts them as decided after a reload, and SHALL count none once a run has applied them.
Surfaces: CLI `wider dedup queue` (pending pairs and the decisions since the last run); UI Deduplication page, heading of the "Pairs to decide" list (`GET /api/dedup` `decided_since_run`).

#### Scenario: A decision survives a reload of the page
- **WHEN** one queued pair is decided and the Dedup page data is loaded again
- **THEN** `decided_since_run` is 1 and one pair fewer is pending
- **AND** after the next run `decided_since_run` is 0
- **AND** `wider dedup queue` prints "· 1 decided since the last run (run `wider dedup run` to apply)" after the decision and no such note after the run
Pinned by: `tests/unit/test_ui_followup_r10.py::test_dedup_heading_counts_every_decision_since_the_last_run`, `tests/unit/test_ui_followup_r30.py::test_the_dedup_queue_command_counts_decisions_made_since_the_last_run`

#### Scenario: A decision in the same second as the run
- **WHEN** a queued pair is decided within the same second as the deduplication run that came before it
- **THEN** `decided_since_run` is 1
Pinned by: `tests/unit/test_ui_followup_r23.py::test_a_decision_in_the_same_second_as_the_run_is_counted`

#### Scenario: A run made by an earlier WIDER version
- **WHEN** the last deduplication run was made by a WIDER version that kept only the run's time, not the list of decisions it applied
- **THEN** a decision with a later time than the run is counted
- **AND** a decision within the run's own second is not counted, since it cannot be told apart from the run, until the next run keeps the list
Pinned by: `tests/unit/test_ui_followup_r23.py::test_a_run_made_by_an_earlier_version_counts_by_time`

#### Scenario: An undo takes a decision off the count
- **WHEN** a reviewer decides one queued pair from the page and then undoes their last change
- **THEN** `decided_since_run` goes from 1 back to 0
- **AND** the pair is pending again
Pinned by: `tests/unit/test_ui_followup_r12.py::test_undo_takes_a_decision_off_the_dedup_count`

#### Scenario: A pair decided twice since the run
- **WHEN** a reviewer decides one queued pair as duplicate and then changes it to not duplicate, both after the last run
- **THEN** `decided_since_run` is 1, not 2
Pinned by: `tests/unit/test_ui_followup_r12.py::test_a_pair_decided_twice_counts_once`

#### Scenario: A pair decided back to what the run applied
- **WHEN** after a run, a pair the run applied as duplicate is decided not duplicate and then duplicate again
- **THEN** `decided_since_run` is 1 after the first change and 0 after the second, since the pair is back to the run's own decision
Pinned by: `tests/unit/test_ui_followup_r30.py::test_the_dedup_queue_command_counts_decisions_made_since_the_last_run`

#### Scenario: The list heading follows the count
- **WHEN** 12 pairs were queued by the last run and 2 of them have been decided since
- **THEN** the "Pairs to decide" list heading reads "10 open, 2 decided"
Pinned by: `tests/unit/test_dedup_page_node.py::test_message_after_deciding_every_pair_and_the_heading_follow_a_decision`, `tests/unit/test_dedup_page_node.py::test_decided_pairs_stay_in_the_list_with_their_signers`

### Requirement: Page duplicate decisions need a reviewer name
The system SHALL sign a duplicate decision sent from the page with the name WIDER works as (D33) and send none without it: the pair view then says "enter your name first: use Change beside “Working as” in the top bar" and offers "Set your name", which opens the name dialog; no browser prompt. Undo per reviewer finds the decision. The pair view shows the signing name and has no name field.
Surfaces: UI Deduplication page, pair view; CLI `wider dedup decide --by`.

#### Scenario: No name given
- **WHEN** no reviewer name is set and the researcher marks a pair "Duplicate" in the pair view
- **THEN** no decision is sent and the pair view says "enter your name first: use Change beside “Working as” in the top bar"
- **AND** its "Set your name" button opens the name dialog, and no browser prompt opens
Pinned by: `tests/unit/test_records.py::test_static_dedup_decision_asks_for_the_name`, `tests/unit/test_dedup_page_node.py::test_duplicate_pair_view_without_a_name_points_at_change`

#### Scenario: The signing name is shown
- **WHEN** the remembered name is "ana" and a pending pair is selected
- **THEN** the pair view shows "Deciding as ana" and no name field
- **AND** after Change in the top bar and saving "bob", the page reloads and the next decision is signed "bob"
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_pair_view_header_buttons_and_signing_name`, `tests/unit/test_dedup_page_node.py::test_duplicate_pair_view_signs_with_the_stored_name`

### Requirement: Uploads listed on the Deduplication page
The system SHALL list, when the flow chart's "Records imported" box is pressed, the imported files that deduplication reads (platform, file, records, imported at, query version), read only, and SHALL give the page a quiet button "Add or change uploads" that opens `#import`. The list offers no way to remove or choose files.
Surfaces: UI Deduplication page, box "Records imported" and its list; CLI `wider import list`.

#### Scenario: Uploads card
- **WHEN** the Deduplication page opens on a project with two imported synthetic files, one with query version 2 and one imported before versions were recorded, and the researcher presses "Records imported"
- **THEN** the list shows two rows, the second with "not recorded", and the box shows the number of records and "from 2 database exports"
- **AND** the list has no button or checkbox, and "Add or change uploads" opens `#import`
Pinned by: `tests/unit/test_dedup_page_node.py::test_dedup_uploads_card_is_the_records_imported_list`

### Requirement: Flow chart of the deduplication counts
The system SHALL draw the step's counts on the Deduplication page as a PRISMA-style flow chart of four boxes: records imported, duplicates removed (by WIDER and by your decisions), records left, pairs to decide. The numbers agree with `wider dedup prisma`. A message beside it is written from the current counts after each decision; the caption says the counts go into the report's PRISMA diagram.
Surfaces: UI Deduplication flow chart; CLI `wider dedup prisma`, `wider dedup queue` (message).

#### Scenario: Counts after a run with open pairs
- **WHEN** a synthetic project of 20 imported records is deduplicated, WIDER removes 5 records on its own and queues 3 pairs
- **THEN** the boxes read 20 records imported, 5 duplicates removed (5 by WIDER, 0 by your decisions), 15 records left "provisional" and 3 pairs to decide "waiting for you"
- **AND** the message beside the chart reads "3 pairs need your decision. The record count stays provisional until every pair is decided.", and `wider dedup queue` prints the same sentence
Pinned by: `tests/unit/test_dedup_page_node.py::test_counts_after_a_run_with_open_pairs`, `tests/unit/test_dedup.py::test_flow_after_a_run_with_open_pairs`

#### Scenario: Message after deciding every pair
- **WHEN** the researcher decides the last of the 3 pairs and has not run deduplication again
- **THEN** the message changes, without a reload, to "Every pair is decided. Press Apply decisions and check again to update the counts."
- **AND** the run's stored warnings stay as they were, under "Details"
Pinned by: `tests/unit/test_dedup_page_node.py::test_message_after_deciding_every_pair_and_the_heading_follow_a_decision`, `tests/unit/test_dedup.py::test_flow_messages`

#### Scenario: Removed by your decisions
- **WHEN** one of the 3 pairs is decided `duplicate` and deduplication runs again
- **THEN** the "Duplicates removed" box reads 6 (5 by WIDER, 1 by your decisions), and records left reads 14
- **AND** the two sub-counts add up to "Duplicate records removed before screening" in `wider dedup prisma`, which also prints "6 duplicates removed (5 by WIDER, 1 by your decisions)"
Pinned by: `tests/unit/test_dedup.py::test_cli_queue_all_prisma_merged_and_records`, `tests/unit/test_dedup.py::test_route_flow_matches_prisma_and_the_lists_match_the_functions`, `tests/unit/test_dedup_page_node.py::test_the_main_button_runs_and_shows_errors_beside_the_chart`

#### Scenario: A run from before the split was stored
- **WHEN** the last run's summary has `duplicate_records_removed` 5 but no per-method counts
- **THEN** the box reads "5 duplicates removed" with "split not recorded" and the page loads without an error
Pinned by: `tests/unit/test_dedup_page_node.py::test_a_run_from_before_the_split_was_stored`, `tests/unit/test_dedup.py::test_flow_of_a_summary_without_the_split`, `tests/unit/test_dedup.py::test_cli_merged_of_an_older_run_and_summary_without_split`

#### Scenario: Files imported after the run
- **WHEN** 20 records were imported and deduplicated, then 2 synthetic records are imported
- **THEN** the "Records imported" box still reads 20, with "+2 imported since the last run", and matches `wider dedup prisma`, which prints the same "+2" line
- **AND** with no pair open, the message reads "2 records were imported since the last run. Press Apply decisions and check again to include them."
Pinned by: `tests/unit/test_dedup_page_node.py::test_files_imported_after_the_run`, `tests/unit/test_dedup.py::test_flow_messages`

#### Scenario: Before the first run
- **WHEN** records are imported but deduplication has not run
- **THEN** only "Records imported" shows a number, the other boxes show no number, and the message says to run deduplication
Pinned by: `tests/unit/test_dedup_page_node.py::test_before_the_first_run_only_records_imported_has_a_number`

### Requirement: Flow-chart boxes filter the list
The system SHALL make each flow-chart box a toggle button that fills the list: records imported (uploads), duplicates removed (merged pairs), records left (unique records), pairs to decide (queue). One box is pressed (`aria-pressed`, "Showing"). It opens on pairs to decide while any is open, else on records left; before any run on records imported, the rest disabled.
Surfaces: UI Deduplication flow chart; CLI `wider import list`, `wider dedup merged|records|queue --all`.

#### Scenario: Pressing a box
- **WHEN** the page shows pairs to decide and the researcher presses "Duplicates removed"
- **THEN** that box has `aria-pressed` true and reads "Showing", the other boxes have `aria-pressed` false, and the list heading names the merged pairs
Pinned by: `tests/unit/test_dedup_page_node.py::test_pressing_a_box_and_opening_on_the_right_box`

#### Scenario: Opening on the right box
- **WHEN** the page opens on a project with no open pair after a run
- **THEN** "Records left" is the pressed box
Pinned by: `tests/unit/test_dedup_page_node.py::test_pressing_a_box_and_opening_on_the_right_box`

#### Scenario: Opening before the first run
- **WHEN** the page opens on a project with imported files and no deduplication run
- **THEN** "Records imported" is the pressed box, and the other three boxes show "–" and are disabled
Pinned by: `tests/unit/test_dedup_page_node.py::test_before_the_first_run_only_records_imported_has_a_number`

### Requirement: Merged pairs can be checked
The system SHALL list the pairs the last run merged, one per record it removed, each with its reason in words ("Same DOI") or "Removed by your decision", and show a selected one side by side, read only, with the line "Removed by WIDER on <date>: <reason>" or "Removed by your decision (<name>, <date>)" and no decision buttons. A1 offers no undo of an automatic merge.
Surfaces: CLI `wider dedup merged [--limit 30] [--json]`; UI Deduplication page, box "Duplicates removed"; `GET /api/dedup/merged`.

#### Scenario: One row per removed record
- **WHEN** WIDER removed 5 records on its own in the last run
- **THEN** `wider dedup merged` lists 5 pairs with their reasons, and the page list for the box has 5 rows
- **AND** a selected merged pair shows no decision button and the keys 1 to 4 decide nothing
Pinned by: `tests/unit/test_dedup.py::test_merge_by_your_decision_is_listed_with_the_run_time_signer`, `tests/unit/test_dedup.py::test_cli_queue_all_prisma_merged_and_records`, `tests/unit/test_dedup_page_node.py::test_a_merged_pair_is_read_only`

#### Scenario: A merge by your decision
- **WHEN** "ana" decided one queued pair `duplicate` and deduplication ran again
- **THEN** `wider dedup merged` lists 6 pairs, one of them "Removed by your decision (ana, <date>)", and the page list shows the same row marked "You" when the current name is "ana"
- **AND** that decision is changed through the notebook's undo or by deciding the pair again in "Pairs to decide", not from this list
Pinned by: `tests/unit/test_dedup.py::test_merge_by_your_decision_is_listed_with_the_run_time_signer`, `tests/unit/test_dedup_page_node.py::test_a_merged_pair_is_read_only`

#### Scenario: Merged pairs of a run made before this change
- **WHEN** the last run was stored before WIDER kept the run's list of merges
- **THEN** `wider dedup merged` prints the rows with the line "(approximate: this run was made before WIDER kept its list; run deduplication again for an exact list)", and `--json` has `approximate: true`
- **AND** the page shows the same note above the list
Pinned by: `tests/unit/test_dedup.py::test_merged_pairs_of_an_older_run_are_replayed`, `tests/unit/test_dedup.py::test_cli_merged_of_an_older_run_and_summary_without_split`, `tests/unit/test_dedup_page_node.py::test_a_merged_pair_is_read_only`

### Requirement: Records left after deduplication can be listed
The system SHALL list the records left after the last deduplication run, each with its title, authors, year and the number of imported records it was made from, and show a selected one read only in the pair view's place.
Surfaces: CLI `wider dedup records [--limit 30] [--json]`; UI Deduplication page, box "Records left" and its list; `GET /api/dedup/records`.

#### Scenario: A record made from two imports
- **WHEN** two imported records were merged into one and 13 other records were left alone
- **THEN** `wider dedup records` lists 14 records, and the merged one says "from 2 imported records"
- **AND** the page list for "Records left" has the same 14 rows
Pinned by: `tests/unit/test_dedup.py::test_cli_queue_all_prisma_merged_and_records`, `tests/unit/test_dedup_page_node.py::test_records_left_list`

### Requirement: Pairs to decide list
The system SHALL list in "Pairs to decide" every pair the last run queued, open or decided (before or since the run), each row with number, title, authors, year and a status word with an icon ("Open", "Duplicate", "Not duplicate", "Unsure") plus "(you)" or the signer's name. Unsure counts as decided. "Open first" puts open pairs on top; the search box filters by title words.
Surfaces: CLI `wider dedup queue --all`; UI Deduplication page, list of "Pairs to decide".

#### Scenario: Decided pairs stay in the list
- **WHEN** 3 pairs were queued, "ana" decided one as duplicate and "ben" one as unsure, and the current name is "ana"
- **THEN** the list has 3 rows reading "Open", "Duplicate (you)" and "Unsure (ben)", each with its icon
- **AND** `wider dedup queue --all` prints the same 3 pairs with the decisions and their names
Pinned by: `tests/unit/test_dedup_page_node.py::test_decided_pairs_stay_in_the_list_with_their_signers`, `tests/unit/test_dedup.py::test_run_stores_its_queue_and_merges`

#### Scenario: An Unsure pair
- **WHEN** the only open pair is decided Unsure and deduplication runs again
- **THEN** the list heading reads "0 open, 1 decided", the row reads "Unsure", the "Pairs to decide" box shows 0 with "1 unsure", and the "Records left" box still says "provisional"
- **AND** the message beside the chart reads "<N> records left, provisional while 1 pair is Unsure." with N the "Records left" number, and `wider dedup queue` prints the same sentence
Pinned by: `tests/unit/test_dedup_page_node.py::test_an_unsure_pair`

#### Scenario: A decision copied from another pair
- **WHEN** "ana" decided raw records 3 and 7 duplicate, a new import made raw records 3 and 9 the pair shown for the same two groups, and deduplication ran again
- **THEN** the row for 3 and 9 reads "Duplicate (copied from raw records 3 and 7, ana)", and `wider dedup queue --all` prints the same
- **AND** undoing ana's decision on 3 and 7 from the notebook is refused with the journal's words, as `wider history undo` with that change id is, and nothing changes; the copy is changed by deciding 3 and 9 again
Pinned by: `tests/unit/test_dedup_page_node.py::test_a_decision_copied_from_another_pair`, `tests/unit/test_dedup.py::test_copied_decision_keeps_its_signer`, `tests/unit/test_dedup.py::test_cli_queue_all_shows_a_copy_and_undo_of_its_source_is_refused`

#### Scenario: A pair decided before the last run
- **WHEN** a queued pair was decided `duplicate` by the current name and deduplication ran again
- **THEN** the list still has its row, reading "Duplicate (you)", and the heading counts it as decided
- **AND** `wider dedup queue --all` prints it with its decision
Pinned by: `tests/unit/test_dedup.py::test_run_stores_its_queue_and_merges`, `tests/unit/test_dedup.py::test_cli_queue_all_prisma_merged_and_records`, `tests/unit/test_dedup_page_node.py::test_decided_pairs_stay_in_the_list_with_their_signers`

### Requirement: Field match words and marked differences
The system SHALL give each field of a pair a match word: "Same" (equal text), "Spelling differs" (equal once case, accents, punctuation and spacing are ignored), "Nearly the same" (most words shared), "Only in A" or "Only in B" (one side empty), "Missing" (both empty), or "Differs". In titles and abstracts, words found in one record only are marked, by more than colour.
Surfaces: UI Deduplication pair view; CLI not applicable (a view; `wider dedup queue` prints both titles).

#### Scenario: Match words
- **WHEN** a pair has titles "Work stress in nurses" and "Work Stress in Nurses.", the DOI only in A, no volume in either, and abstracts that differ in two words
- **THEN** the title reads "Spelling differs", the DOI "Only in A", the volume "Missing" and the abstract "Nearly the same"
- **AND** the two differing words are marked in each abstract with a mark and an underline, and every other word is plain text
Pinned by: `tests/unit/test_dedup_page_node.py::test_scenario_match_words`

#### Scenario: Marking does not change the text
- **WHEN** an abstract contains `<b>` and `&amp;`
- **THEN** the pair view shows them as text, escaped, with only the differing words marked
Pinned by: `tests/unit/test_dedup_page_node.py::test_scenario_marking_does_not_change_the_text`

### Requirement: One main button on the Deduplication page
The system SHALL give the Deduplication page one main button, in the top bar: "Run deduplication" before the first run and "Apply decisions and check again" after it; both run deduplication. A refused re-run says why beside the flow chart. Side actions ("Explain this step", "Add or change uploads") are quiet buttons.
Surfaces: UI Deduplication page, top bar; CLI `wider dedup run`.

#### Scenario: Button name after a run
- **WHEN** the page opens on a project that has been deduplicated once
- **THEN** the top bar's main button reads "Apply decisions and check again", and it is the only button on the page with the main-action look
- **AND** pressing it runs deduplication and redraws the flow chart and the list with the new counts
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_main_button_runs_and_shows_errors_beside_the_chart`
