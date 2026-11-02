# Query Dry Run Specification

## Purpose
Runs the query on the researcher's computer against the seed papers' titles, abstracts and keywords under a profile of each database's search behaviour, so a query is checked before anything is pasted into a database, and keeps the rounds of refinement with their recall and yield.

## Requirements

### Requirement: Local evaluation of a query against seed papers
The system SHALL evaluate a synonyms version against each seed paper's title, abstract and keywords under the profile of a platform and return, per seed paper and platform, one of `hit`, `miss` or `uncertain`, with the terms that matched. A term matches the way the platform would read it (truncation, phrase, proximity, fields of the block). The app MUST NOT run a query in any database.
Surfaces: CLI `wider query dry-run [--version N] [--platform P]`; UI Search strings page, "Dry run" card.

#### Scenario: Hits and misses on synthetic seeds
- **WHEN** the synthetic project's synonyms v1 is dry-run on the development platform and seed B001's abstract holds a phrase of block A and a stem of block B while B002 holds only block A
- **THEN** B001 is `hit` with both terms listed and B002 is `miss` with block B named as the block that did not match
- **AND** the result names the version, the platform and the profile it used
Pinned by: `tests/unit/test_dry_run.py::test_hits_and_misses_follow_the_profile`

#### Scenario: Proximity and field restriction
- **WHEN** a block uses a proximity of 3 words and another block searches `title` only
- **THEN** a seed whose matching words are five words apart in the abstract is `miss` for the first block, and a seed with the second block's term only in its abstract is `miss` for it
Pinned by: `tests/unit/test_dry_run.py::test_proximity_and_fields_are_evaluated`

#### Scenario: Dry run without seed papers
- **WHEN** `wider query dry-run` runs on a project with no seed papers
- **THEN** it exits 1 with "no seed papers: add them in the Setup step (Question and seed papers)" and writes no run folder
- **AND** the "Dry run" card shows the same sentence with a link to the Setup step
Pinned by: `tests/unit/test_dry_run.py::test_dry_run_without_seeds_names_setup`

### Requirement: Uncertain when the profile cannot decide
The system SHALL return `uncertain` with a reason when the outcome depends on a platform behaviour the profile cannot emulate (automatic term mapping, lemmatisation, stop-word handling, truncation the platform applies differently inside phrases) or when the seed paper has no abstract or no keywords and the block searches that field. `uncertain` is never counted as a hit in recall.
Surfaces: CLI `wider query dry-run`; UI "Dry run" card, state column with the reason on focus.

#### Scenario: PubMed term mapping and a missing abstract
- **WHEN** the dry run is done for PubMed on a seed that matches only through a term the profile marks as subject to automatic term mapping, and for a seed without an abstract
- **THEN** both seeds are `uncertain`, the first with the reason "automatic term mapping" and the second with "no abstract"
- **AND** the recall figure counts neither as retrieved and reports the number of uncertain seeds separately
Pinned by: `tests/unit/test_dry_run.py::test_uncertain_states_and_reasons`

### Requirement: Per-database profiles in the platform catalogue
The system SHALL read, per platform, a profile with the fields each block field maps to, the minimum truncation stem, whether truncation applies inside phrases, the proximity range and whether it is ordered, and the behaviours that make a result uncertain. A platform without a profile MUST be reported as "no profile" and skipped, not guessed.
Surfaces: CLI `wider query dry-run --platform P`, `wider query platforms` (shows "profile: yes/no"); UI "Dry run" card, platform list.

#### Scenario: Every catalogue platform has a profile except Google Scholar
- **WHEN** the profiles are listed
- **THEN** EBSCOhost, Scopus, Web of Science, ProQuest, Ovid and PubMed each have a profile with a minimum stem and a proximity range
- **AND** Google Scholar has none and `wider query dry-run --platform gscholar` says "no profile for gscholar" and exits 1
Pinned by: `tests/unit/test_dry_run.py::test_profiles_cover_the_boolean_family`

### Requirement: Truncation diagnostics
The system SHALL report, per truncated term, the words of the seed papers it matched and the words that a longer or shorter stem would gain or lose, so the researcher sees what `*` does on the seeds.
Surfaces: CLI `wider query dry-run --terms`; UI "Dry run" card, term row expanded.

#### Scenario: A stem matches more than intended
- **WHEN** block B holds `organ*` and the seeds contain "organization", "organizational" and "organic"
- **THEN** the diagnostics list all three words under `organ*` and show that `organi?ation*` would keep the first two and drop "organic"
Pinned by: `tests/unit/test_dry_run.py::test_truncation_diagnostics_list_matched_words`

### Requirement: Term-by-seed grid and minimal cover
The system SHALL build a grid of terms against development seed papers (which term matches which seed, per platform), compute a minimal cover (a smallest set of terms that keeps every retrieved seed retrieved, by greedy set cover with a fixed tie order) and list the terms that match no seed. The grid is also written as CSV and as an SVG heatmap in the run folder.
Surfaces: CLI `wider query dry-run --grid`; UI "Dry run" card, grid and heatmap.

#### Scenario: Cover and terms without a seed
- **WHEN** four terms match seeds {1,2}, {2,3}, {3} and {} respectively
- **THEN** the cover is the first two terms, the fourth is listed under "matches no seed paper"
- **AND** `grid.csv` and `grid.svg` exist in the run folder and the SVG names every term and seed id
Pinned by: `tests/unit/test_dry_run.py::test_grid_cover_and_heatmap`

### Requirement: Rounds of refinement
The system SHALL treat every synonyms version as a round of the same number (older versions become rounds on upgrade) and keep per round and platform development recall, uncertain seeds, the record count (exported, or typed and marked `pasted`) and the time. `wider query rounds` lists them oldest first with the recall-versus-yield plot, pasted counts marked.
Surfaces: CLI `wider query rounds`, `wider query count <round> <platform> <n>`; UI "Rounds" card (plot, count fields).

#### Scenario: Two rounds, one imported
- **WHEN** synonyms v1 and v2 are dry-run and an export recorded against v1 is imported
- **THEN** the rounds list shows v1 with its recall and record count and v2 with recall and "no export yet"
- **AND** `recall_vs_yield.svg` plots v1 as a point and v2 on the recall axis only
Pinned by: `tests/unit/test_dry_run.py::test_rounds_log_and_plot`

#### Scenario: A count typed by hand
- **WHEN** the researcher runs `wider query count 2 scopus 1840` for v2, which has no import, and later imports a Scopus export of 1,790 records against v2
- **THEN** the rounds list first shows 1840 marked "pasted" and the plot draws v2 as a hollow point with "pasted" in its description and in the text table, and the history has a `round_count` change that undo reverses
- **AND** after the import the count is 1,790 with source `export`, and the same field on the Rounds card calls the same engine function as the CLI
Pinned by: `tests/unit/test_dry_run.py::test_pasted_count_marks_the_plot`

#### Scenario: An existing project's versions appear as rounds
- **WHEN** a schema 4 project with synonyms v1 (draft) and v2 (frozen) is opened by the new code
- **THEN** `wider query rounds` lists rounds 1 and 2, round 2 marked "accepted (by freeze)" and round 1 not accepted, each with "no dry run yet" until one runs
Pinned by: `tests/unit/test_store.py::test_migrate_v5_backfills_rounds`

### Requirement: Acceptance of a round states the seeds it loses
The system SHALL accept any round picked after re-evaluating it and the last accepted round on today's seeds on every profiled platform, naming by platform each development seed the last accepted round retrieves and this one misses; a seed `uncertain` under either counts neither way and is listed. Validation seeds show only for the frozen query's own version. A loss is confirmed on the page, printed by the CLI after, and recorded.
Surfaces: CLI `wider query accept <round>`; UI "Rounds" card.

#### Scenario: A round that loses a seed is accepted with the loss stated
- **WHEN** v1 is accepted with recall 3/3 and v2 retrieves 2/3 on Scopus, missing B003
- **THEN** "Accept this round" first shows a confirm step with "v2 loses B003 on scopus, which v1 retrieves."; `wider query accept 2` accepts v2, prints the same line and exits 0
- **AND** the rounds list marks v2 "accepted, loses B003 on scopus", the history lists a `round_accept` change that undo reverses, the package's `rounds.json` carries `lost` for v2, and a v3 with 3/3 is accepted with no confirm step and no loss line
Pinned by: `tests/unit/test_dry_run.py::test_acceptance_states_lost_seeds`

#### Scenario: The seed set grew since the last acceptance
- **WHEN** v1 was accepted with three seeds, seed B004 is added later, v1 retrieves B004 today and v2 does not, while v2 retrieves all three original seeds
- **THEN** `wider query accept 2` accepts v2 and prints "v2 loses B004 on scopus, which v1 retrieves.", although v2's recall on the original three equals v1's
Pinned by: `tests/unit/test_dry_run.py::test_acceptance_reevaluates_on_todays_seeds`

#### Scenario: An uncertain seed
- **WHEN** v1 is accepted and retrieves B003 on PubMed, and v2 (which changed B003's matching term) leaves B003 `uncertain` there ("automatic term mapping") while every other seed v1 retrieves is retrieved
- **THEN** v2 is accepted with no loss line and the result lists "uncertain: B003" without counting it as lost or found
Pinned by: `tests/unit/test_dry_run.py::test_uncertain_seed_neither_gains_nor_loses`

#### Scenario: Validation seeds only for the frozen query
- **WHEN** synonyms v3 is the frozen query with B004 in the validation set, draft v4 is kept from before the freeze, and v4 is dry-run with `wider query dry-run --version 4` and on the "Dry run" card, then accepted with `wider query accept 4` and on the "Rounds" card
- **THEN** the dry run and the acceptance of v4 evaluate development seeds only and read "validation seeds are shown only for the frozen query v3", on CLI and page alike
- **AND** the dry run of v3 evaluates B004 too; with no frozen query (for example the uploads final and no version frozen) every dry run reads "validation seeds are shown only for a frozen query; none stands"
Pinned by: `tests/unit/test_dry_run.py::test_validation_seeds_only_for_the_frozen_query`

#### Scenario: Freezing a round that was not accepted
- **WHEN** v2 is the accepted round and the researcher freezes v3, which was never accepted
- **THEN** the freeze succeeds, v3 becomes the accepted round, and the stage result adds "v3 was accepted implicitly by the freeze"
- **AND** the rounds list marks v3 "accepted (by freeze)"
Pinned by: `tests/unit/test_dry_run.py::test_freeze_accepts_implicitly`

### Requirement: Per-database coverage plot
The system SHALL write, per dry run, a per-database coverage plot (platforms on one axis, hits, uncertain and misses stacked) to the run folder and show it on the page with a text table carrying the same numbers.
Surfaces: CLI `wider query dry-run` (path printed); UI "Dry run" card.

#### Scenario: Plot and table agree
- **WHEN** a dry run covers two platforms
- **THEN** `coverage_by_platform.svg` exists and the table on the page lists the same hit, uncertain and miss counts per platform
Pinned by: `tests/unit/test_dry_run.py::test_coverage_plot_matches_table`
