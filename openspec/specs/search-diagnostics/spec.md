# Search Diagnostics Specification

## Purpose
After the researcher imports a database export, explains what each term and block retrieved, which streams are noise, which seed papers were missed and why, and proposes edits whose consequences are computed before the researcher accepts them.

## Requirements

### Requirement: What did what
The system SHALL report, per imported platform and query version, how many records each block and each term retrieved (a record counts for every term it matches in its title, abstract or keywords), how many records only one term retrieved, and how many records no term explains (records the platform returned that match no term under the profile).
Surfaces: CLI `wider query diagnose [--version N] [--platform P]`; UI Search results page, "Diagnostic report" card.

#### Scenario: Counts per term on the synthetic import
- **WHEN** the synthetic export is imported and diagnosed
- **THEN** every term of the query has a record count, the terms that alone retrieved a record are marked, and records matching no term are counted with the note "retrieved by the database for a reason the profile does not see"
Pinned by: `tests/unit/test_diagnostics.py::test_what_did_what_counts`

### Requirement: Clusters judged by the researcher, not by a model
The system SHALL group retrieved records by the set of terms that matched them, list each cluster with its size, three sample titles and the seed papers in it, and let the researcher mark a cluster `relevant` or `noise`. No model decides include or exclude at this stage.
Surfaces: CLI `wider query clusters`, `wider query cluster <signature> relevant|noise [--by]`; UI "Diagnostic report" card, cluster rows with two buttons.

#### Scenario: Marking a cluster
- **WHEN** the clusters are listed and the cluster matched only by one term is marked `noise` on the CLI, and another `relevant` through the page
- **THEN** both marks are stored with the author and shown on the next listing, and the history gains one change per mark that can be undone
- **AND** no screening decision exists for any record of either cluster
Pinned by: `tests/unit/test_diagnostics.py::test_clusters_are_marked_by_the_researcher`

### Requirement: Noise report
The system SHALL list the noise terms: terms whose retrieved records share no matched term with any seed paper and belong to no cluster marked relevant, with their record count and the share of the platform's total. Terms in a cluster marked relevant MUST NOT be listed as noise.
Surfaces: CLI `wider query diagnose` ("Noise" section); UI "Diagnostic report" card.

#### Scenario: A noise term and a rescued one
- **WHEN** term X retrieves 40 records none of which holds any other matched term or a seed paper, and term Y's only cluster was marked relevant
- **THEN** X is listed as noise with 40 records and Y is not
Pinned by: `tests/unit/test_diagnostics.py::test_noise_terms_respect_relevant_clusters`

### Requirement: Exact interval for benchmark recall
The system SHALL report benchmark recall per set and platform as found/total with the exact two-sided 95% Clopper-Pearson interval, and say in words that recall on known papers overstates recall on the scattered literature.
Surfaces: CLI `wider coverage`, `wider query diagnose`; UI "Seed paper coverage" card and the report (section "Seed paper coverage").

#### Scenario: Ten of ten
- **WHEN** ten development seed papers are all found
- **THEN** the coverage shows "10 of 10, 95% interval 0.69 to 1.00"
- **AND** three of five shows "0.15 to 0.95"
Pinned by: `tests/unit/test_diagnostics.py::test_clopper_pearson_interval`, `tests/unit/test_diagnostics.py::test_interval_in_coverage_diagnosis_and_report`

### Requirement: Indexing check per missed seed paper
The system SHALL write, for each seed paper a platform did not retrieve, a known-item string in that platform's syntax (DOI where searchable, else the title as a phrase) and store the researcher's verdict `indexed`, `not_indexed` or `unknown` per seed paper and platform; the diagnosis then says "not in the database" or "indexed but not retrieved" per missed seed.
Surfaces: CLI `wider query indexing [list|set <benchmark_id> <platform> <verdict>]`; UI Search results page, "Indexing check" card.

#### Scenario: Verdicts change the diagnosis
- **WHEN** B002 is missed on Scopus, the known-item string is listed, and the verdict `not_indexed` is set on the CLI while B003's is set `indexed` through the page
- **THEN** the diagnosis lists B002 under "not in the database" and B003 under "indexed but not retrieved", and both verdicts are journalled
- **AND** `not_indexed` for a seed the platform retrieved (a DOI or normalised-title match in an export imported against that platform) is refused; `indexed` and `unknown` are stored
Pinned by: `tests/unit/test_diagnostics.py::test_indexing_verdicts`, `tests/unit/test_diagnostics.py::test_indexing_verdicts_in_the_diagnosis`

#### Scenario: Pasting the known-item string is checked by hand
- **WHEN** the researcher pastes the known-item string into the real database
- **THEN** the string returns the paper when it is indexed (hand check M47; NOT_TESTED here)

### Requirement: Diagnostics read only the seeds that may be shown
The system SHALL base every diagnostic (what did what, clusters, noise, indexing and known-item checks, recall per set and platform, edits) on Development papers and the validation set while shown. Used validation papers ("Val (used)") show apart, only in recall per set and the indexing and known-item checks; neither they nor query-result papers count in validation recall.
Surfaces: CLI `wider query diagnose|clusters|indexing list`; UI Search results, Diagnostic report and Indexing check cards.

#### Scenario: Diagnostics with the validation set hidden
- **WHEN** the query is reopened after an unlock, B002 was seen at the unlock, B004 entered Validation since, B001 came from the query's results, and `wider query diagnose`, `wider query clusters` and `wider query indexing list` run and the Search results page's cards load
- **THEN** no noise, cluster, indexing or known-item output names B004 or counts it, and B002 is listed as a used validation paper ("Val (used)"), not as a development seed, on CLI and page alike
- **AND** after the re-freeze the validation recall per platform counts B004 alone, with B002 counted under "Validation, used" and B001 in neither set
- **AND** after B002 is moved to Development with `wider benchmark move-used --confirm`, each diagnostic lists it as a development seed ("Dev (former val)")
Pinned by: `tests/unit/test_diagnostics.py::test_diagnostics_hide_validation_seeds`

### Requirement: Platform limits per database
The system SHALL store, per platform, the limits the researcher applied in the database (a date range, languages and a reason), show them under its rendered string, journal each change as `platform_limits` (undoable) and write them to the package's `limits.json`. A date range that does not parse or ends before it starts is refused.
Surfaces: CLI `wider query limits <platform> [--dates A..B] [--languages L,...] [--reason R]`; UI Search strings page, "Limits" fields (`POST /api/query/limits`).

#### Scenario: Limits set on the CLI and on the page
- **WHEN** `wider query limits scopus --dates 2010..2024 --languages en,de --reason "protocol limits"` runs, and in a second project the same dates, languages and reason are entered in the Scopus "Limits" fields on the Search strings page
- **THEN** both projects store the same limits, and the rendered page shows the dates, the languages and the reason under the Scopus string, as `wider query limits scopus` prints them
- **AND** History lists one `platform_limits` change, which undo reverses, and the package's `limits.json` carries the dates, languages and reason for scopus
- **AND** `--dates 2024..2010` exits 1 and the route answers 400, both naming the range, and nothing changes
Pinned by: `tests/unit/test_diagnostics.py::test_platform_limits_set_and_shown`

### Requirement: Suggested edits with computed consequences
The system SHALL list edits (add a name that turns a `miss` into a `hit`, drop a noise term, change a stem, add a keyword field), each with its consequence from the dry run (seeds gained or lost per platform) and, after an import, records added or removed. Applying an edit writes a new draft synonyms version noting the edit; a frozen version is never changed.
Surfaces: CLI `wider query edits`, `wider query edit apply <edit_id>`; UI "Diagnostic report" card, "Apply".

#### Scenario: Apply an add-name edit
- **WHEN** a term from B002's keywords that is not in the query would turn B002 into a hit and the edit is applied
- **THEN** a new draft synonyms version exists holding the name in the right block, its note names the edit, and the previous version is unchanged
- **AND** the edit listed "+1 seed on ebsco, +0 records (no import for this version yet)" before it was applied
Pinned by: `tests/unit/test_diagnostics.py::test_apply_edit_writes_a_draft`

### Requirement: Record budget sets the precision weight
The system SHALL read `protocol.identification.record_budget` (default 5000) and rank suggested edits by development recall first and by records saved per seed kept second; the diagnosis says when the accepted round's record count exceeds the budget.
Surfaces: CLI `wider query diagnose`; UI "Diagnostic report" card header; Setup protocol settings.

#### Scenario: Over budget
- **WHEN** the budget is 100 and the imported export holds 150 records
- **THEN** the diagnosis says "150 records, 50 over the budget of 100" and lists the drop-noise edits first among edits that lose no seed
Pinned by: `tests/unit/test_diagnostics.py::test_record_budget_orders_edits`
