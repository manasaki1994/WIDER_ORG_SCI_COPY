# import-and-coverage Specification

## Purpose
Import and coverage is stage `import` of the WIDER procedure: it follows `query` and must be done before deduplication. The researcher runs each platform's search string and saves the exports in the project; WIDER reads them (RIS, CSV/TSV, XLS/XLSX, BibTeX) into one table of raw records, checks every file for signs of a wrong, truncated or duplicate export, and reports which benchmark (seed) papers each platform and the deduplicated corpus retrieved. The stage is done when the `raw_records` table holds at least one record.

## Requirements

### Requirement: Export files per platform
The system SHALL read every export under `inputs/<platform>/` and the legacy `inputs/ris/<platform>/`, the folder name being the platform key, with suffixes `.ris`, `.csv`, `.tsv`, `.tab`, `.xlsx`, `.xls`, `.bib`, `.bibtex` or `.txt`. A `.txt` file MUST be sniffed as RIS, BibTeX, TSV or CSV from its content.
Surfaces: CLI `wider import add <platform> <file...>`, `wider import run`; UI Import and benchmark coverage page, "Exports" card.

#### Scenario: Format detection
- **WHEN** the inputs hold `proquest.csv`, a tab-delimited `wos.txt` and a BibTeX file named `scopus.bib.txt`
- **THEN** their formats are detected as `csv`, `tsv` and `bib`
- **AND** a text starting with `TY  - ` is sniffed as `ris`
Pinned by: `tests/unit/test_import_formats.py::test_format_detection`

#### Scenario: Delimiters and byte-order marks
- **WHEN** CSV exports use comma, semicolon or tab delimiters, one of them with a byte-order mark
- **THEN** the delimiter is sniffed for each file and the mark is stripped from the first header
- **AND** empty cells are dropped from the record
Pinned by: `tests/unit/test_import_formats.py::test_csv_sniffing_and_bom`

### Requirement: Each file imported once by content hash
The system SHALL import each file at most once by the sha256 of its content, adding a `sources` row (platform, file_name, sha256, n_records, imported_at, format, profile) and one `raw_records` row per record. Each file is reported `imported`, `already` or `error`, and an unreadable file MUST NOT stop the other files.
Surfaces: CLI `wider import run` (plain `wider import` does the same); UI Import and benchmark coverage page, "Import the uploaded files".

#### Scenario: Re-running the import
- **WHEN** a Scopus file with two records and a ProQuest file with one record are imported and the import is run again
- **THEN** the first run reports both files as `imported` and the second reports `already`
- **AND** the per-platform summary shows 2 records for scopus and 1 for proquest
Pinned by: `tests/unit/test_ingest.py::test_import_once_and_coverage`

#### Scenario: Mixed formats and one broken file
- **WHEN** CSV, TSV, BibTeX, a legacy `inputs/ris/scopus/old.ris` and a broken `.xlsx` are imported together
- **THEN** the `sources` rows record format and profile, such as `csv`/`proquest_csv`, `tsv`/`wos_tsv`, `bib`/`bibtex` and `ris`/`ris:scopus`
- **AND** the broken file is reported with status `error` while the others are imported
Pinned by: `tests/unit/test_import_formats.py::test_import_all_mixed_formats_records_format`

### Requirement: Column profiles and platform mismatch
The system SHALL map the columns of tabular and BibTeX exports to standard record fields with a column profile from `wider/ingest/profiles.yaml` chosen by headers and format, falling back to a generic profile. A file whose headers match another known platform than its folder MUST be flagged "columns look like a <detected> export, not <platform>".
Surfaces: CLI `wider import check`, `wider import add`; UI Import and benchmark coverage page, "Export sanity check" card.

#### Scenario: Profiles detected from headers
- **WHEN** headers `Title, Abstract, StoreId, year` (csv), `Authors, Title, Source title, EID` (csv) and `PT, AU, TI, AB, UT` (tsv) are checked
- **THEN** the detected platforms are ProQuest, Scopus and WoS
- **AND** unknown headers give no profile, and `Title, Year` uses the generic profile
Pinned by: `tests/unit/test_import_formats.py::test_detect_profile_per_platform`

#### Scenario: A Scopus export filed under another platform
- **WHEN** a Scopus CSV export is read as an `ebsco` export
- **THEN** the metadata reports `detected_platform` `scopus` and `mismatch` true, and the records are still read
- **AND** a BibTeX file reports profile `bibtex` and no mismatch
Pinned by: `tests/unit/test_import_formats.py::test_read_standard_bibtex_and_mismatch`

### Requirement: Spreadsheet exports need the optional extras
The system SHALL read `.xlsx` and `.xls` exports through optional libraries and, when they are missing, MUST fail that file with a message naming the install command `pip install "wider[import]"`.
Surfaces: CLI `wider import run`; UI Import and benchmark coverage page, "Exports" card.

#### Scenario: Missing spreadsheet library
- **WHEN** an `.xlsx` file is read and the spreadsheet library cannot be imported
- **THEN** a missing-dependency error is raised whose message contains `wider[import]`
- **AND** with the library installed a WoS `.xlsx` export is read with profile `wos_xlsx`
Pinned by: `tests/unit/test_import_formats.py::test_xlsx_missing_dependency_message`, `tests/unit/test_import_formats.py::test_xlsx_round_trip`

### Requirement: Export sanity verdicts
The system SHALL give each export one verdict, checked in order: `empty`, `duplicate` (same sha256 as an earlier file), `off_topic` (all available topic signals below threshold), `capped` (count equals the platform's export cap, if `imports.warn_on_export_cap`), else `ok`. Each verdict MUST have a plain-language message.
Surfaces: CLI `wider import check` (also printed by `wider import run` and `wider import add`); UI Import and benchmark coverage page, "Export sanity check" card, "Re-check".

#### Scenario: All verdicts in one project
- **WHEN** the inputs hold a good ProQuest CSV, a ProQuest CSV of exactly 100 records, an off-topic Scopus file, an empty file, an EBSCO file and an identical copy of it
- **THEN** the verdicts are `ok`, `capped`, `off_topic`, `empty`, `ok` and `duplicate` (of `ebsco/ebsco.csv`), counted as ok 2, capped 1, off_topic 1, empty 1, duplicate 1
- **AND** the messages mention the export cap and "probably is not the result of your query", and a file is marked imported only after the import has run
Pinned by: `tests/unit/test_import_formats.py::test_check_all_verdicts`

#### Scenario: Cap warning switched off
- **WHEN** `imports.warn_on_export_cap` is false and a ProQuest CSV has exactly 100 records
- **THEN** its verdict is `ok`
- **AND** no cap message is shown
Pinned by: `tests/unit/test_import_formats.py::test_check_file_without_benchmark_or_synonyms_is_graceful`

#### Scenario: Duplicate message
- **WHEN** a file has the same content as an earlier file
- **THEN** its message reads "identical to <platform>/<file>; it will be imported once"
- **AND** an import run reports it as `already`

### Requirement: Topic signals for the sanity check
The system SHALL compute two signals per file: seed paper hits (Development seed papers, plus Validation once unlocked (synonyms frozen or uploads marked final) or when `validation_after_freeze` is false), with threshold `imports.min_benchmark_hits` (1), and the share of records containing a synonyms term, with threshold `imports.min_vocabulary_overlap` (0.5).
Surfaces: CLI `wider import check`; UI Search results page, "Export sanity check" card (seed papers and vocabulary columns).

#### Scenario: Vocabulary overlap per block
- **WHEN** three records are checked against two blocks, matching normalised title, abstract and keywords with `*` as truncation, and two records match each block
- **THEN** the overlap is 0.667 overall and 0.667 for each block
- **AND** with no vocabulary the overlap is not computed
Pinned by: `tests/unit/test_import_formats.py::test_term_pattern_and_vocabulary_overlap`

#### Scenario: Nothing to compare against yet
- **WHEN** a project has no seed papers and no synonyms and an off-topic file is checked
- **THEN** the verdict is `ok` with no seed paper hits and no overlap computed
- **AND** the message says "no seed papers and no synonyms yet: topic checks skipped"
Pinned by: `tests/unit/test_import_formats.py::test_check_file_without_benchmark_or_synonyms_is_graceful`

#### Scenario: Only a draft synonyms version
- **WHEN** no synonyms version is approved or frozen but a draft exists
- **THEN** the vocabulary comes from the latest draft
- **AND** the file gets the warning "no approved synonyms artifact; using the latest draft"
Pinned by: `tests/unit/test_import_formats.py::test_check_uses_the_latest_draft_synonyms`

### Requirement: Export caps from the platform catalogue
The system SHALL take each platform's export cap per format from `export_cap` in `wider/search/platforms.yaml` (for example ProQuest 100 for CSV, Scopus 2000 for CSV, EBSCO 200 for RIS) and treat a platform or format without an entry as having no cap.
Surfaces: CLI `wider import check`; UI Import and benchmark coverage page, "Export sanity check" card.

#### Scenario: Cap lookup
- **WHEN** the caps for ProQuest CSV, Scopus CSV, EBSCO CSV and an unknown platform are looked up
- **THEN** they are 100, 2000, none and none
- **AND** only a file whose count equals a defined cap can get the `capped` verdict
Pinned by: `tests/unit/test_import_formats.py::test_export_cap_from_platforms_yaml`

### Requirement: Adding and checking exports from the command line
The system SHALL copy export files into `inputs/<platform>/` and print their sanity verdicts without importing, check all files without importing, and import and then print the per-platform summary and the sanity table. A missing file argument MUST stop `wider import add` with exit status 2.
Surfaces: CLI `wider import add <platform> <file...>`, `wider import check`, `wider import run`, `wider import`; UI see the upload requirement.

#### Scenario: Add, check, run
- **WHEN** the researcher adds a good ProQuest CSV and an off-topic Scopus CSV, runs `wider import check`, then `wider import run`, then plain `wider import`
- **THEN** `add` prints `ok` and `off_topic` for the two files and `check` lists both
- **AND** `run` reports "4 records (csv)" and the sanity table, and the bare command reports the files as `already`
Pinned by: `tests/unit/test_import_formats.py::test_cli_import_add_check_run`

### Requirement: Upload and check from the Import page
The system SHALL accept an export uploaded for a platform key, save it under a sanitised name in `inputs/<platform>/` and return its sanity verdict without importing it. A platform key not matching `^[a-z][a-z0-9_]*$` or a file with an unaccepted suffix MUST be refused with HTTP 400.
Surfaces: UI Import and benchmark coverage page, "Exports" card, "Upload and check"; CLI equivalent `wider import add`.

#### Scenario: Upload a ProQuest CSV
- **WHEN** the researcher uploads `proquest.csv` for platform `proquest`
- **THEN** the response has verdict `ok` and the file exists at `inputs/proquest/proquest.csv`
- **AND** the sanity table (`GET /api/import/check`) lists one file, while platform key `Bad Key` or a `.docx` file is refused with HTTP 400
Pinned by: `tests/unit/test_import_formats.py::test_ui_import_check_and_upload`

### Requirement: Benchmark coverage per platform and for the corpus
The system SHALL report which benchmark papers each platform's raw records (`platform:<key>`) or the deduplicated `records` (`corpus`) retrieved, matching by DOI, then exact normalised title, then Jaro-Winkler title similarity ≥ `identification.jw_benchmark` (0.88) as a `fuzzy` match to confirm. Results MUST be stored in `benchmark_matches`.
Surfaces: CLI `wider coverage [--corpus]`; UI Import and benchmark coverage page, "Benchmark coverage" card ("Per platform", "Deduplicated corpus").

#### Scenario: DOI and title matches per platform
- **WHEN** a Scopus export holds one paper by DOI and another by its exact title
- **THEN** coverage for `platform:scopus` finds 2 Development papers, with methods `doi` and `title`
- **AND** the ProQuest scope reports its own count of 1
Pinned by: `tests/unit/test_ingest.py::test_import_once_and_coverage`

#### Scenario: Match order
- **WHEN** benchmark papers are located in a corpus by DOI, exact title, a near title and no match
- **THEN** the methods are `doi`, `title`, `fuzzy` (needs confirmation) and `none`
- **AND** the unmatched paper has no record reference, and the DOI match ignores the `https://doi.org/` prefix and letter case
Pinned by: `tests/unit/test_matching.py::test_locate_benchmark_doi_title_fuzzy_none`

#### Scenario: Fuzzy matches shown for confirmation
- **WHEN** a benchmark paper is matched only by fuzzy title
- **THEN** the CLI shows its method as "fuzzy (confirm)" and the UI marks it "(confirm)"
- **AND** each set is summarised as found of n

### Requirement: Validation set hidden until the query or the uploads are final
The system SHALL report only the Development set in coverage until the Validation set is unlocked (a frozen query or final uploads; after an unlock, a new freeze or mark), and both sets then or with `identification.validation_after_freeze` false. An early request MUST first ask if the papers shown count as seen, then show them and record the time (`validation_peeked_at`), papers and choice.
Surfaces: CLI `wider coverage [--validation]`; API `validation=true`; UI Search results, coverage card.

#### Scenario: Before the freeze
- **WHEN** coverage is checked before any synonyms version is frozen and before the uploads are marked final
- **THEN** `validation_included` is false and only Development rows are reported
- **AND** asking explicitly for the Validation set, with the researcher's choice on whether the papers shown count as seen, reports it (0 found in the test project)
Pinned by: `tests/unit/test_ingest.py::test_import_once_and_coverage`

#### Scenario: Notes on the hidden set
- **WHEN** coverage is shown before the freeze without `--validation`
- **THEN** the CLI notes "validation set hidden until the synonyms artifact is frozen or the uploads are marked final (wider construct freeze synonyms N, or wider import final)"
- **AND** the Search results page notes "Validation set hidden until the query is frozen or the uploads are marked final.", which becomes "Development and validation sets." once it is unlocked
Pinned by: `tests/unit/test_stage_pages.py::test_hidden_set_notes`, `tests/unit/test_pickers_js_node.py::test_hidden_set_note_rendered`

#### Scenario: Validation gating switched off
- **WHEN** `identification.validation_after_freeze` is false and no synonyms version is frozen
- **THEN** `wider coverage` and `GET /api/coverage` both report the Development and the Validation set
- **AND** no hidden-set note is shown and `validation_peeked_at` is not set
Pinned by: `tests/unit/test_ingest.py::test_validation_gating_follows_the_protocol_and_a_peek_is_logged`

#### Scenario: Early peek from the UI
- **WHEN** `GET /api/coverage?validation=true` is called before the query is frozen or the uploads are final, while gating is on and the Validation set holds B002 and B003
- **THEN** without `seen_choice` it answers 200 with `choice_needed` true, no Validation rows and the notice "Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final.", records nothing, and the coverage card shows that notice with the buttons "Count them as seen" and "Keep them as validation" (focus on "Cancel"); `wider coverage --validation` without a flag prints it and exits 2
- **AND** with `seen_choice=count` (or `--count-seen`) the set is reported with "validation set shown before the query was frozen; this look is logged in the history. Counted as seen: the 2 validation papers shown no longer validate the search.", `validation_peeked_at` is set, the papers shown are recorded with `counted` true, and after the freeze they are listed under "Validation, used"
- **AND** History gains a `validation_peek` change "validation set looked at before the freeze: counted as seen" that cannot be undone, and the validity note in the report and `manifest.json` adds "Validation set looked at on <date> before the freeze (2 papers shown); the researcher chose to count them as seen."
Pinned by: `tests/unit/test_dry_run.py::test_early_look_asks_whether_to_count_as_seen`, `tests/unit/test_dry_run.py::test_seen_choice_missing_exits_2`

#### Scenario: Early peek kept as validation
- **WHEN** the Validation set holds B002 and B003 before the query is frozen or the uploads are final, and it is asked for with `wider coverage --validation --keep-validation` or with "Keep them as validation" on the coverage card
- **THEN** the set is reported with "validation set shown before the query was frozen; this look is logged in the history. Kept as validation: the 2 validation papers shown still count; every output states this choice.", and `validation_peeked_at` is set with the papers recorded as shown, `counted` false
- **AND** after v3 is frozen coverage reports B002 and B003 in the validation set (none under "Validation, used"), and the note in the report, the methods draft and `manifest.json` adds "Validation set looked at on <date> before the freeze (2 papers shown); the researcher chose to keep them as validation."
- **AND** History lists the `validation_peek` change as "validation set looked at before the freeze: kept as validation", which cannot be undone
Pinned by: `tests/unit/test_dry_run.py::test_early_look_kept_as_validation`

#### Scenario: Hidden again after an unlock
- **WHEN** synonyms version 3 is frozen and the query is unlocked into draft version 4
- **THEN** `validation_included` is false although version 3 is still frozen, and the hidden-set note is shown
- **AND** freezing version 4 makes `validation_included` true again
Pinned by: `tests/unit/test_dry_run.py::test_new_validation_hidden_until_refreeze`

### Requirement: Backfilling benchmark abstracts from matched records
The system SHALL copy, on request, the longest abstract of a confirmed match (never an unconfirmed fuzzy match) into a benchmark paper whose abstract is shorter than `protocol.corpus.thin_words`, when the copied abstract reaches that length, setting `abstract_source` to `corpus`.
Surfaces: CLI `wider coverage --backfill-abstracts`; UI none.

#### Scenario: Abstract taken from the platform export
- **WHEN** a benchmark paper without an abstract was matched by DOI to a Scopus record with a longer abstract and the backfill runs
- **THEN** one benchmark abstract is backfilled
- **AND** its `abstract_source` is `corpus` and its word count exceeds 10
Pinned by: `tests/unit/test_ingest.py::test_import_once_and_coverage`

### Requirement: Text exports are checked against the text-export cap
The system SHALL check a `.txt` export against the platform's `txt` entry in `export_cap` when one is defined, whatever format its content is sniffed as, and otherwise against the cap of the sniffed format.
Surfaces: CLI `wider import check`, `wider import add`, `wider import run`; UI Import and benchmark coverage page, "Export sanity check" card.

#### Scenario: Tab-delimited WoS text export at the cap
- **WHEN** a tab-delimited `wos/savedrecs.txt` with exactly 500 records is checked and the WoS catalogue entry has `txt: 500`
- **THEN** its cap is 500 and its verdict is `capped`
- **AND** the same file with 499 records gets the verdict `ok`

#### Scenario: No text cap defined
- **WHEN** a `.txt` file sniffed as RIS is checked for a platform whose catalogue has a `ris` cap but no `txt` cap
- **THEN** the `ris` cap is used

### Requirement: Imported state on the Import page follows the content hash
The system SHALL mark a file on the Import page as imported only when a `sources` row has the same sha256 as the file's content, never by file name alone.
Surfaces: UI Import and benchmark coverage page, "Exports" card (`GET /api/import`); CLI equivalent `wider import run` (reports `already` by content hash).

#### Scenario: Same name, new content
- **WHEN** `scopus/export.ris` was imported and is then replaced by a different file of the same name
- **THEN** `GET /api/import` lists `export.ris` as new, not imported
- **AND** a renamed copy of an imported file is listed as imported

### Requirement: Adding an export never overwrites a file
The system SHALL leave every existing file under `inputs/` unchanged when an export is added. A file identical in content to an existing export MUST NOT be saved again and gets the verdict `duplicate` naming that export; a different file whose name is taken MUST be saved under a new free name, which the result reports.
Surfaces: UI Import and benchmark coverage page, "Upload and check" (`POST /api/import/upload`); CLI `wider import add`.

#### Scenario: Uploading the same export twice
- **WHEN** the researcher uploads `proquest.csv` for `proquest` and then uploads the same bytes again
- **THEN** the second response has verdict `duplicate` with the message "identical to proquest/proquest.csv; it will be imported once"
- **AND** `inputs/proquest/` still holds one file

#### Scenario: Different export with a taken name
- **WHEN** a different file also named `proquest.csv` is uploaded for `proquest`
- **THEN** the first `proquest.csv` keeps its content and the new file is saved under another name in `inputs/proquest/`
- **AND** the response names the saved file and gives its own sanity verdict

### Requirement: Adding files from the command line is all or nothing
The system SHALL check that every file named to `wider import add` exists before copying any of them; when one is missing it MUST name the missing file, copy nothing and exit with status 2.
Surfaces: CLI `wider import add <platform> <file...>`; UI not applicable (one file per upload).

#### Scenario: Second file missing
- **WHEN** the researcher runs `wider import add scopus a.csv missing.csv` and only `a.csv` exists
- **THEN** the command reports that `missing.csv` does not exist and exits with status 2
- **AND** `inputs/scopus/` does not contain `a.csv`

### Requirement: Sanity footer shows the vocabulary actually used
The system SHALL end the sanity table printed by `wider import add` with the number of benchmark papers checked and the synonyms version and status the verdicts used, or "none yet" only when no synonyms version exists, as `wider import check` does.
Surfaces: CLI `wider import add`, `wider import check`, `wider import run`; UI Import and benchmark coverage page, "Export sanity check" card footer.

#### Scenario: Approved synonyms present
- **WHEN** synonyms version 2 is approved and the researcher adds an export with `wider import add`
- **THEN** the footer reads "synonyms: v2 (approved)"
- **AND** with no synonyms version at all it reads "synonyms: none yet"

### Requirement: Stored match confirmation follows the current match
The system SHALL store each benchmark match's confirmation from the current check: a DOI or exact-title match is confirmed and a fuzzy match is unconfirmed. A match whose record or method changed since the last check MUST NOT keep the earlier confirmation.
Surfaces: CLI `wider coverage [--corpus]`, `wider coverage --backfill-abstracts`; UI Import and benchmark coverage page, "Benchmark coverage" card.

#### Scenario: A DOI match becomes a fuzzy match
- **WHEN** a benchmark paper matched by DOI in the corpus is matched only by fuzzy title after the corpus changed and coverage runs again
- **THEN** its stored match is unconfirmed and shown as "fuzzy (confirm)"
- **AND** `--backfill-abstracts` does not copy an abstract from that record

#### Scenario: A fuzzy match becomes a DOI match
- **WHEN** a paper first matched by fuzzy title is matched by DOI on a later check
- **THEN** its stored match is confirmed

### Requirement: Query version recorded per imported file
The system SHALL record, for each imported file, the search-strings (synonyms) version chosen at import (`--round N` or the round selector; default the frozen query, else the newest version), or none when no version exists. Files imported before this change or with no version are listed as "not recorded". The version is stored in the project's own store only.
Surfaces: CLI `wider import run [--round N]`, `wider import list`; UI Search results page (upload, round selector), Deduplication page.

#### Scenario: Import after a freeze
- **WHEN** synonyms version 2 is frozen and a synthetic RIS file is imported, then version 2 is unlocked into version 3, version 3 is frozen and a second file is imported
- **THEN** the first file is listed with query version 2 and the second with query version 3
- **AND** `wider import run` and the upload card print "recorded under v2 (the frozen query)" for the first and "recorded under v3 (the frozen query)" for the second
Pinned by: `tests/unit/test_stage_pages.py::test_import_records_the_frozen_query_version`

#### Scenario: Import defaults to the frozen query
- **WHEN** v2 is frozen, draft v3 exists, and a file is imported without `--round` with `wider import run` and, in a second project, on the page
- **THEN** the file is recorded under v2, the round selector preselects v2, and both print "recorded under v2 (the frozen query)"
- **AND** once v2 is unlocked into v4 (no frozen query stands) the next file is recorded under v4, the newest version, and both print "recorded under v4 (draft)"
Pinned by: `tests/unit/test_dry_run.py::test_import_defaults_to_the_frozen_query`

#### Scenario: Import with no frozen version
- **WHEN** a file is imported while the project has no synonyms version, and a second file while version 1 is a draft and none is frozen
- **THEN** the first file's query version is empty in the store and the listing says "not recorded", and the second is listed with query version 1
Pinned by: `tests/unit/test_stage_pages.py::test_import_records_the_frozen_query_version`

#### Scenario: An export of an earlier draft attaches to its round
- **WHEN** synonyms versions 2 and 3 are drafts, a Scopus file is imported with `wider import run --round 2`, and a second without a round
- **THEN** the first is listed with query version 2 and its record count appears in round 2 of `wider query rounds`, and the second is listed with version 3
- **AND** the upload's round selector lists every version with the newest preselected (no version is frozen) and sends the same choice, and `--round 9` (no such version) exits 1 naming the versions that exist
Pinned by: `tests/unit/test_dry_run.py::test_import_attaches_to_chosen_round`

### Requirement: Listing imported files
The system SHALL list every imported file with its platform, file name, number of records, import time and query version, oldest first, and the time the uploads were marked final, if they were. The listing reads the store only and changes nothing.
Surfaces: CLI `wider import list [--json]`; UI Deduplication page, box "Records imported" and its list (`GET /api/import/sources`).

#### Scenario: Same rows on the page and the command line
- **WHEN** two synthetic files were imported on two platforms
- **THEN** `wider import list --json` and `GET /api/import/sources` give the same two rows with platform, file, records, imported at and query version, and the same final mark (none yet)
- **AND** the store, `project.yaml` and the history are unchanged after both
Pinned by: `tests/unit/test_stage_pages.py::test_import_list_matches_the_page`

#### Scenario: Nothing imported yet
- **WHEN** no file was imported
- **THEN** `wider import list` prints "no files imported yet" and exits 0, and the route returns an empty list
Pinned by: `tests/unit/test_stage_pages.py::test_import_list_matches_the_page`

### Requirement: Upload verdict shown as text
The system SHALL show the file name, verdict and message of an uploaded export as text on the Search results page, never as markup, as the sanity table does.
Surfaces: UI Search results page, "Upload and check"; CLI `wider import add` (prints the verdict as plain text).

#### Scenario: Markup in a verdict
- **WHEN** the upload route answers with the verdict `<b>x</b>` and the message `<i>y</i>`
- **THEN** the upload message shows the characters `<b>x</b>` and `<i>y</i>` and contains no `b` or `i` element
Pinned by: `tests/unit/test_import_js_node.py::test_upload_verdict_is_text`

### Requirement: Validation checkbox outside the coverage heading
The system SHALL place the "include validation set" checkbox and its label in the toolbar under the "Seed paper coverage" heading of the Search results page, so the heading holds only its name.
Surfaces: UI Search results page, "Seed paper coverage" card; CLI `wider coverage --validation`.

#### Scenario: Heading holds only its name
- **WHEN** the UI page is loaded
- **THEN** the "Seed paper coverage" heading contains no input, and the `cov-val` checkbox has a label in the toolbar under it
Pinned by: `tests/unit/test_shell.py::test_coverage_heading_has_no_controls`

### Requirement: Choosing which uploaded files enter deduplication
The system SHALL keep an `included` flag per imported file, on by default, off for a file the import's check finds off topic or empty (a notice names it), switched by the file's id (a name only when unique); only included files are deduplicated. A switch is refused while later stages need records only that file holds, and is journalled and undoable. Coverage reads every file, marking excluded ones.
Surfaces: CLI `wider import include|exclude|list`; UI Search results, "Enters deduplication".

#### Scenario: Exclude a file before deduplication
- **WHEN** one of two imported files is excluded by its id and deduplication runs
- **THEN** only the other file's records are in the corpus, the listing shows "no" under "enters dedup" for the excluded file, and the history has one `source_included` change that undo reverses
- **AND** after screening decisions exist on records only that file holds, excluding it is refused naming the dependent tables
Pinned by: `tests/unit/test_ingest.py::test_files_can_be_kept_out_of_dedup`

#### Scenario: A file name that is not unique
- **WHEN** two imported files are both called `scopus.csv` and `wider import exclude scopus.csv` is run
- **THEN** it is refused with exit 1, listing both files with their ids, and nothing changes
- **AND** the page shows the checkbox only on imported files, not on files that are on disk but not imported
Pinned by: `tests/unit/test_ingest.py::test_include_by_name_only_when_unique`

#### Scenario: An excluded file in the PRISMA counts and the package
- **WHEN** two Scopus files of 10 and 4 records are imported, the second is excluded, and deduplication runs
- **THEN** the PRISMA rows (`prisma_rows`, `wider dedup prisma`) count 14 records for Scopus, then "Records not used (file excluded): 4", so that 14 minus 4 minus the duplicates removed gives the records screened, and the PRISMA 2020 diagram shows "Records removed before screening: file excluded (n = 4)" and does not count the 4 as duplicates
- **AND** the replication package's `manifest.json` lists both files, the second with `"included": false`, and the Deduplication page's "Records imported" box counts the 10 records of the included file with "file excluded: 4" under it
- **AND** with no file excluded neither the PRISMA row, the diagram line nor the "file excluded" line appears, so the rows are those of today
Pinned by: `tests/unit/test_ingest.py::test_excluded_file_shown_as_not_used`, `tests/unit/test_dedup_page_node.py::test_flow_shows_file_excluded_only_above_zero`

#### Scenario: An existing project opens unchanged
- **WHEN** a project store at schema 4 with two imported files and two seed papers (one per set) is opened by the new code
- **THEN** the store is at schema 5, both files have `included` = 1, both papers have `source` = `researcher` and an `added_at` equal to the migration time, the Validation paper has that time as `validation_since`, the Development paper is marked as having been in Development, and every new table exists and is empty
- **AND** `wider import list`, `wider benchmark list` and `wider coverage` give the same answers as before the migration
Pinned by: `tests/unit/test_store.py::test_migrate_v5_keeps_rows`

#### Scenario: A file that looks wrong starts out of deduplication
- **WHEN** an on-topic file and a file whose check says off topic are imported (`wider import run` or the page's Import)
- **THEN** the off-topic file is imported with `included` off and the notice "<file> is kept out of deduplication until you include it: <the check's sentence>.", and the on-topic file enters deduplication with no notice
- **AND** the files table shows the off-topic file unticked under "Enters deduplication" with "it stays out of deduplication until you tick it", and `wider import include <id>` or the tick puts it back
Pinned by: `tests/unit/test_import_formats.py::test_flagged_files_start_out_of_deduplication`, `tests/unit/test_import_formats.py::test_cli_import_run_names_a_kept_out_file`, `tests/unit/test_import_js_node.py::test_files_table_shows_the_verdict`

### Requirement: Validation set after an unlock
After counting unlocks (Count chosen, or none shown), a Validation paper SHALL count only if it entered since the latest, was not in Development, seen at a counting unlock or a counted or used look, nor from `query_results`; others are "Validation, used". Note: "Validation set seen on <date> (N unlocks); M papers added since." or "Search not validated: …; no paper added since.", plus look and choice lines.
Surfaces: CLI `wider coverage`; UI Search results, coverage card.

#### Scenario: After an unlock
- **WHEN** the query was unlocked with two Validation papers shown and "Count them as seen", one paper is then added to Validation with source `colleague`, and the new newest synonyms version is frozen
- **THEN** coverage lists the two under "Validation, used" (label "Val (used)") with their own recall, not with Development, the new paper is the validation set, and the note reads "Validation set seen on <date> (1 unlock); 1 paper added since."
- **AND** with no paper added since, the note reads "Search not validated: validation set seen on <date> (1 unlock); no paper added since."
Pinned by: `tests/unit/test_dry_run.py::test_coverage_after_unlock`

#### Scenario: After an unlock kept as validation
- **WHEN** the query was unlocked with V001 and V002 shown and "Keep them as validation", one paper is then added to Validation with source `colleague`, and v4 is frozen
- **THEN** coverage reports V001, V002 and the new paper as the validation set, none under "Validation, used", and the note reads "Development and validation sets." then "v3 was unlocked on <date> into v4 (2 validation papers shown); the researcher chose to keep them as validation."
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_kept_unlock_keeps_the_set`

#### Scenario: After an unlock, new validation papers stay hidden until the re-freeze
- **WHEN** the query is unlocked with "Count them as seen", a paper is added to Validation with source `colleague`, and coverage and a dry run are run before the newest synonyms version is frozen
- **THEN** coverage reports `validation_included` false with the hidden-set note, lists the papers seen at the unlock under "Validation, used", and the dry run leaves out the new paper and the used papers
- **AND** after the newest version is frozen, or the uploads are marked final after the unlock, coverage reports the new paper as the validation set
- **AND** after an unlock with "Keep them as validation" the set is hidden the same way until the re-freeze or the mark, and the kept papers are then reported in it
Pinned by: `tests/unit/test_dry_run.py::test_new_validation_hidden_until_refreeze`, `tests/unit/test_unlock_seen_choice.py::test_kept_unlock_keeps_the_set`

#### Scenario: Two unlocks
- **WHEN** the query is unlocked with "Count them as seen", paper B005 is added to Validation, and the query is frozen and unlocked again with "Count them as seen"
- **THEN** B005 is listed under "Validation, used", because it entered Validation before the latest unlock
- **AND** the note gives the first unlock's date and "2 unlocks"
Pinned by: `tests/unit/test_dry_run.py::test_two_unlocks_use_the_latest`

#### Scenario: Freeze, unlock, add and mark within one second
- **WHEN** with the clock held at one second, v3 is frozen and unlocked into v4 with "Count them as seen", paper B008 is added to Validation and the uploads are marked final
- **THEN** coverage reports `validation_included` true and counts B008 in the validation set, because the order comes from the unlock numbers, not from the times
- **AND** after a second unlock in the same second (v4 frozen, then unlocked into v5 with "Count them as seen"), B008 is listed under "Validation, used" and the set is hidden again
Pinned by: `tests/unit/test_dry_run.py::test_unlock_order_by_sequence_in_the_same_second`

#### Scenario: Ranking validation after an unlock excludes seen papers
- **WHEN** the query was unlocked with Validation papers B002 and B003 shown and "Count them as seen", B007 is then added to Validation with source `colleague`, and the newest version is frozen
- **THEN** `wider rank validate`, the Ranking page's benchmark counts, the `wider status` line, the screening sanity list and the methods draft treat B007 as the only validation paper and B002 and B003 as used validation papers ("Val (used)"), not development papers
- **AND** the Overview's seed coverage counts B002 and B003 as used validation papers, not with Development
Pinned by: `tests/unit/test_dry_run.py::test_ranking_validation_after_unlock_excludes_seen_papers`

#### Scenario: A development paper moved to Validation, and a paper added again
- **WHEN** after an unlock with "Count them as seen" B006 is added to Development and later moved to Validation, and a Validation paper that was in the benchmark at the unlock is removed and added again
- **THEN** the re-added paper is listed under "Validation, used", B006 is counted with Development (label "Dev", title "in Validation, but was in Development: counted with Development"), and neither counts in the validation set
- **AND** `wider benchmark set` and `wider benchmark add` print (exit 0), and the seed-papers step shows beside the paper, "B006 was in Development: it is counted with Development and will not count in the validation set", and beside the re-added paper that it has been shown as a validation paper and is a used validation paper
Pinned by: `tests/unit/test_dry_run.py::test_moved_and_re_added_papers_are_seen`

#### Scenario: Peek after an unlock
- **WHEN** v3 was unlocked into draft v4 with "Count them as seen", B007 is added to Validation with source `colleague`, and before any freeze the Validation set is asked for with `wider coverage --validation` and on the Search results page
- **THEN** the command without a flag prints "Validation set requested while the query is reopened; it holds 1 paper." with the choice text and "Choose one: --count-seen or --keep-validation. Nothing was done.", exits 2 and records nothing; the page answers `choice_needed` with that notice and shows "Count them as seen", "Keep them as validation" and "Cancel", focus on "Cancel"
- **AND** with `--count-seen` (or "Count them as seen") both show the set with "validation set shown while the query is reopened; this look is logged in the history. Counted as seen: the 1 validation paper shown no longer validates the search.", and History gains a `validation_peek` change that cannot be undone
- **AND** after v4 is frozen the note reads "Search not validated: validation set seen on <date> (1 unlock); no paper added since." then "Looked at on <date> while the query was reopened (1 validation paper shown); the researcher chose to count them as seen.", the same in the report, the methods draft and `manifest.json`
- **AND** with `--keep-validation` instead, after v4 is frozen B007 is counted in the validation set and the look line ends "the researcher chose to keep them as validation."
- **AND** after that kept look, saving a new synonyms version before the next freeze asks the revision confirm (as after any kept look) and, when confirmed, records B007 as used
- **AND** in a project whose only counting event is that look (v3 was unlocked with "Keep them as validation", the look with "Count them as seen"), the note after v4 is frozen reads "Search not validated: validation set seen on <date> (looked at while the query was reopened); no paper added since."
Pinned by: `tests/unit/test_dry_run.py::test_peek_after_unlock_is_not_validated`, `tests/unit/test_unlock_seen_choice.py::test_kept_look_while_reopened_then_revised`

### Requirement: Uploads marked final, reopened by a later file
The system SHALL let the researcher mark the uploads final after confirming, once files are imported, none waiting; the mark is listed in History, undoable until an unlock, and opens the Validation set. A later file reopens the uploads (an unlock clears the mark, earlier work stays, a notice names the file); with validation papers shown the researcher first chooses if they count as seen.
Surfaces: CLI `wider import final|add|run`, `wider history undo`; UI Search results.

#### Scenario: Marking the uploads final
- **WHEN** no synonyms version is frozen, the researcher presses "These uploads are final" on Search results and confirms
- **THEN** History lists the change "uploads_final", the page shows the time of the mark instead of the button, and `wider import list` shows the same time, as after `wider import final`
- **AND** `wider coverage` and `GET /api/coverage` now report the Development and the Validation set, and `validation_peeked_at` is not set
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_mark_matches_cli`, `tests/unit/test_pickers_js_node.py::test_uploads_final_button`

#### Scenario: Uploads while final reopen the search
- **WHEN** no synonyms version is frozen, the uploads were marked final on <date>, 3 validation papers have been shown, and a synthetic file `new.ris` is uploaded on the page, added with `wider import add`, or imported with `wider import run`
- **THEN** before the file is taken the page shows a confirm step with "The uploads were marked final on <date>; adding new.ris reopens them; 3 validation papers have been shown." and the choice text (requirement "Every unlock asks whether shown papers count as seen"), the buttons "Count them as seen" and "Keep them as validation" and the focus on "Cancel"; `GET /api/import/sources` returns `upload_choice` true with that text as `upload_notice`
- **AND** "Cancel" takes nothing; `wider import add` and `wider import run` without a flag print the same notice and "Choose one: --count-seen or --keep-validation. Nothing was done.", take nothing and exit 2
- **AND** with "Count them as seen" or `--count-seen` (exit 0) the file is taken and the page and the commands show "Uploads reopened: new.ris was added after they were marked final on <date>. Earlier imports and decisions are kept. Counted as seen: the 3 validation papers shown so far no longer validate the search."
- **AND** the list of unlocks gains one entry with cause `uploads`, `counts_as_seen` 1 and `seen_choice` `count`, History lists "uploads reopened: new.ris added (counted as seen)" (not undoable) with the earlier mark still listed, `unlock.json` lists it, and `wider import list` shows "uploads not marked final"
- **AND** with "Keep them as validation" or `--keep-validation` the notice ends "Kept as validation: the 3 validation papers shown so far still count; every output states this choice.", the entry has `counts_as_seen` 0 and `seen_choice` `keep`, and once a version is frozen the 3 papers are reported in the validation set
- **AND** the earlier imports, their records and the deduplication decisions are unchanged
- **AND** an identical file while final records nothing and asks nothing: the same bytes under any name, uploaded on the page or added with `wider import add`, nor does `wider import run` with no new file waiting
Pinned by: `tests/unit/test_stage_pages.py::test_upload_while_final_reopens_the_search`

#### Scenario: Uploads while final with a frozen query ask whether the papers count
- **WHEN** synonyms v3 is the frozen query, the uploads were marked final on <date>, Validation papers B002 and B003 have been shown, and `new.ris` is uploaded on the page or added with `wider import add`
- **THEN** before the file is taken the page shows a confirm step with "The uploads were marked final on <date> and the frozen query v3 stands; adding new.ris reopens them; 2 validation papers have been shown. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." and the buttons "Count them as seen" and "Keep them as validation", neither pre-selected, with focus on "Cancel"
- **AND** "Cancel" takes nothing and records nothing; `wider import add new.ris` without a flag prints the same notice and "Choose one: --count-seen or --keep-validation. Nothing was done.", takes nothing and exits 2; `GET /api/import/sources` returns `upload_choice` true with that text as `upload_notice`
- **AND** with "Count them as seen" or `--count-seen` (exit 0) the file is taken and the notice reads "Uploads reopened: new.ris was added after they were marked final on <date>. Earlier imports and decisions are kept; the frozen query v3 still stands. Counted as seen: the 2 validation papers shown so far no longer validate the search."
- **AND** the list of unlocks gains one entry with cause `uploads`, which `unlock.json` lists with `counts_as_seen` 1 and `seen_choice` `count`, History lists "uploads reopened: new.ris added (counted as seen)" (not undoable), B002 and B003 are listed under "Validation, used", and the note reads "Search not validated: validation set seen on <date> (1 unlock); no paper added since." then "Uploads reopened on <date> while v3 was the frozen query (2 validation papers shown); the researcher chose to count them as seen."
Pinned by: `tests/unit/test_stage_pages.py::test_upload_while_final_with_frozen_query_asks`

#### Scenario: Uploads while final with a frozen query kept as validation
- **WHEN** synonyms v3 is the frozen query, the uploads were marked final on <date>, B002 and B003 have been shown, and `new.ris` is uploaded on the page with "Keep them as validation" or added with `wider import add new.ris --keep-validation`
- **THEN** the file is taken (exit 0) and the page and the command show "Uploads reopened: new.ris was added after they were marked final on <date>. Earlier imports and decisions are kept; the frozen query v3 still stands. Kept as validation: the 2 validation papers shown so far still count; every output states this choice."
- **AND** the list of unlocks gains one entry with cause `uploads`, which `unlock.json` lists with `counts_as_seen` 0 and `seen_choice` `keep`, History lists "uploads reopened: new.ris added (kept as validation)" (not undoable), and `wider import list` shows "uploads not marked final"
- **AND** coverage still reports B002 and B003 as the validation set (2 counted, none under "Validation, used"), and the note in every output adds "Uploads reopened on <date> while v3 was the frozen query (2 validation papers shown); the researcher chose to keep them as validation."
- **AND** a later save of v4 asks the choice again; with `--count-seen` it records an unlock with cause `save`, after which B002 and B003 are listed under "Validation, used" and the note reads "(1 unlock)"
Pinned by: `tests/unit/test_stage_pages.py::test_upload_while_final_with_frozen_query_asks`

#### Scenario: Uploads while final with a frozen query and no validation paper
- **WHEN** synonyms v3 is the frozen query, the uploads were marked final on <date>, the Validation set holds no paper (or only papers from the query's results), and `new.ris` is uploaded on the page or added with `wider import add`
- **THEN** the file is taken with no confirm step and no choice asked (exit 0), and the notice reads "Uploads reopened: new.ris was added after they were marked final on <date>. Earlier imports and decisions are kept; the frozen query v3 still stands."
- **AND** the list of unlocks gains one entry with cause `uploads`, an empty `seen`, `counts_as_seen` 0 and `seen_choice` empty, `wider import list` shows "uploads not marked final", and the validity note gains no line for it
- **AND** with no frozen query and no validation paper the file is taken with no confirm step and the notice reads "Uploads reopened: new.ris was added after they were marked final on <date>. Earlier imports and decisions are kept; no validation paper has been shown yet."
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_with_no_paper_shown_count_nothing`

#### Scenario: Citation chaining while final reopens the search
- **WHEN** the uploads are marked final and citation chaining is run on the page (`POST /api/prior-reviews/chain`) or with `wider prior-reviews chain` and saves a file to `inputs/citation_chain/`
- **THEN** it records the same unlock and shows the same notice naming the saved file, exit 0
- **AND** a chaining run that saves no file records nothing
- **AND** with validation papers shown, with or without a frozen query, the page and `wider prior-reviews chain` ask the same choice as an upload before the run starts (`GET /api/prior-reviews` returns `chain_choice` and `chain_notice`; without `--count-seen` or `--keep-validation` the command exits 2 and nothing runs); with none shown it runs with no extra confirm step
Pinned by: `tests/unit/test_stage_pages.py::test_upload_while_final_reopens_the_search`, `tests/unit/test_dry_run.py::test_seen_choice_missing_exits_2`

#### Scenario: Files waiting to be imported
- **WHEN** a synthetic RIS file was uploaded on the page or added with `wider import add` but not imported, and the researcher marks the uploads final on the page or with `wider import final`
- **THEN** the route answers 409 and the command exits 1, both saying "1 file is saved but not imported yet", naming the platform and file, and "Import the uploaded files first" (`wider import run`)
- **AND** nothing is journalled and coverage still hides the Validation set
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_refusals`

#### Scenario: A file that cannot be read
- **WHEN** a saved file in `inputs/<platform>/` is not imported because it cannot be parsed, and the uploads are marked final on the page or with `wider import final`
- **THEN** the route answers 409 and the command exits 1, both naming the file and its parse error and saying "Fix or delete it in inputs/<platform>/, then try again"
- **AND** nothing is journalled
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_refusals`

#### Scenario: Nothing imported yet
- **WHEN** no file was imported and the uploads are marked final on the page or with `wider import final`
- **THEN** the route answers 409 and the command exits 1, both saying "no files imported yet", and nothing is journalled
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_refusals`

#### Scenario: Redo of an undone mark
- **WHEN** the mark was undone, a synthetic RIS file was saved but not imported, and the undo is undone on the History page or with `wider history undo ID`
- **THEN** the route answers 409 and the command exits 1, both saying the uploads cannot be marked final again and "1 file is saved but not imported yet", and coverage still hides the Validation set
- **AND** once the file is imported the redo goes through, and a later upload records an unlock with the notice naming the redo's time
- **AND** when the undo had recorded an unlock (the set was shown, no version frozen), the redo stores the current latest unlock number with the mark, so the redone mark opens the Validation set again, and the papers seen at that undo stay under "Validation, used" when that undo counted them as seen
Pinned by: `tests/unit/test_stage_pages.py::test_redo_of_final_mark_rechecks_and_names_the_redo`

#### Scenario: Marked twice
- **WHEN** the uploads are already marked final and `wider import final` runs again, or `POST /api/import/final` is called again
- **THEN** the command exits 0 and the route answers 200, both naming the time of the existing mark, and History gains no new entry
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_mark_matches_cli`

#### Scenario: Undoing the mark
- **WHEN** the mark, made while the Validation set was empty, is undone from History or with `wider history undo ID`
- **THEN** a later upload shows no notice and records no unlock and, while no synonyms version is frozen, coverage hides the Validation set again
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_mark_matches_cli`

#### Scenario: An unlock clears the mark
- **WHEN** the uploads were marked final, synonyms v3 is frozen and unlocked into v4, and the mark's change is then undone from History or with `wider history undo ID`
- **THEN** `wider import list` shows "uploads not marked final" and a new upload shows no notice and records no further unlock
- **AND** the undo of the earlier mark is refused because the mark changed since (the route answers 409, the command exits 1) and nothing changes
Pinned by: `tests/unit/test_dry_run.py::test_unlock_reopens_final_uploads`

### Requirement: Used validation papers stay in Validation, reported apart
Validation papers that no longer count (in Validation at a counting unlock, a counted or used look, or moved back after a move) SHALL stay in Validation as the group "Validation, used" ("Val (used)"), listed even while the set is hidden, with their own recall and interval, and MUST NOT be dry-run development seeds or counted with Development until the researcher moves them.
Surfaces: CLI `wider coverage`, `wider status`, `wider query dry-run`; UI Search results, coverage card; Overview.

#### Scenario: Used papers in coverage and status
- **WHEN** the query was unlocked with Validation papers B002 and B003, B007 is added to Validation with source `colleague`, and v4 is frozen
- **THEN** `wider coverage` and `GET /api/coverage` report B007 as the validation set and B002 and B003 in the group "Validation, used" with their found count and exact interval, rows marked `used` true, and the CLI table and the coverage card label them "Val (used)" (the card's title: "used validation paper: seen before the query was revised or the uploads reopened; recall on it is not independent")
- **AND** `wider status` reads "benchmark: 4 papers (1 development, 1 validation, 2 used validation)" and `GET /api/project` gives the benchmark count `used` 2
Pinned by: `tests/unit/test_validation_reuse.py::test_used_papers_are_reported_apart`

#### Scenario: Used papers are not development seeds
- **WHEN** B002 and B003 are used validation papers and B001 is the only Development paper
- **THEN** `wider query dry-run` and the Search results page's dry run evaluate B001 as the only development seed, and the Overview's seed coverage counts 1 development paper and 2 used validation papers apart
- **AND** while the validation set is hidden, coverage still lists B002 and B003 under "Validation, used" (they were already shown) and records no look
Pinned by: `tests/unit/test_validation_reuse.py::test_used_papers_are_not_development_seeds`

### Requirement: A revision after a kept look records the papers as used
Saving a new synonyms version with no frozen query and the set hidden, after a look that kept papers as validation, SHALL warn first (page confirm step; CLI exit 2 without `--confirm`) and record those papers as used in one final `validation_used` change; the note then says the search is not validated on them.
Surfaces: CLI `wider construct save|propose|restore`, `wider query import|edit apply`, `wider seeds names --admit`, `wider questions answer` (`--confirm`); UI each save's confirm step.

#### Scenario: Saving after a kept look
- **WHEN** B002 and B003 were shown before the freeze and kept as validation, and v4 is then saved with `wider construct save synonyms` or on the page
- **THEN** `GET /api/construct/synonyms` gives `save_notice` "You looked at the validation set on <date> and kept 2 papers as validation. Saving v4 revises the query after that look: the 2 papers stay in Validation, recorded as used; recall on them will not be independent.", the page's confirm step shows it ("Cancel" saves nothing), and `wider construct save synonyms` without `--confirm` prints it with "Add --confirm to save it. Nothing was done." and exits 2, saving nothing; with `--confirm` it saves v4 (exit 0); `wider query edit apply E1` behaves the same, and `wider construct propose synonyms` without `--confirm` exits 2 before any model call
- **AND** History gains a `validation_used` change "validation papers recorded as used: v4 saved after a look" that cannot be undone (undo answers 409, exit 1); when `wider seeds names --admit` saved v4, its change cannot be undone either
- **AND** after v4 is frozen coverage lists B002 and B003 under "Validation, used", and the report, the methods draft, `manifest.json` and the Recall page read "Search not validated: validation set seen on <date> (looked at before the freeze); no paper added since." with the look's line and "The query was revised on <date> (v4 saved) after that look: the 2 papers shown are recorded as used."
Pinned by: `tests/unit/test_validation_reuse.py::test_save_after_kept_look_records_used`

#### Scenario: Nothing more to record
- **WHEN** the papers of a kept look were recorded as used by the save of v4, and v5 is saved
- **THEN** no save notice is shown and no second `validation_used` change is recorded
- **AND** a save after a look with no hidden Validation paper, or after a counted look, gives no notice
Pinned by: `tests/unit/test_validation_reuse.py::test_save_after_kept_look_records_used`

### Requirement: Moving a shown validation paper to Development
Moving to Development a Validation paper shown (by a look, in Validation at an unlock, or in the set while shown) SHALL warn first and need a confirmation, then be one `validation_moved` change that cannot be undone; the paper is then a former validation paper ("Dev (former val)"). A move of a paper never shown is an undoable `benchmark_set` change. An applied split follows the same rule.
Surfaces: CLI `wider benchmark set|split [--confirm]`; UI Setup page, seed-papers step.

#### Scenario: The move warns first
- **WHEN** v3 is frozen, B002 is in the validation set, and B002 is moved to Development with `wider benchmark set B002 Development` or with the seed-papers step's set select
- **THEN** the command prints "B002 has been shown as a validation paper. Moving it to Development makes it a former validation paper: the report, the methods draft and the package list it, and the move cannot be undone." and "Add --confirm to move it. Nothing was done." and exits 2; `POST /api/benchmark/assign` without `confirm` answers 200 with `confirm_needed` true and that `notice`
- **AND** the page shows the notice in a dialog with "Move to Development" and "Cancel" (focus on "Cancel", which puts the select back), and nothing changes until the move is confirmed
Pinned by: `tests/unit/test_validation_reuse.py::test_move_of_shown_paper_warns`, `tests/unit/test_setup_js_node.py::test_move_shown_paper_dialog`

#### Scenario: A confirmed move is final and reported
- **WHEN** the move of B002 is confirmed with `--confirm` (exit 0) or "Move to Development"
- **THEN** History gains a `validation_moved` change "validation paper moved to Development: B002" whose undo is refused ("a move of a shown validation paper is final"; 409, exit 1)
- **AND** coverage lists B002 under Development labelled "Dev (former val)", and the validity note adds "1 validation paper that had been shown was moved to Development on <date> (former validation paper: B002)."
Pinned by: `tests/unit/test_validation_reuse.py::test_confirmed_move_is_final_and_reported`

#### Scenario: A paper never shown moves freely
- **WHEN** no query is frozen, the uploads are not final, no look was made, and B004 is moved from Validation to Development
- **THEN** it moves with no warning (exit 0), History lists a `benchmark_set` change "seed paper set changed: B004 Validation → Development" that can be undone, and the validity note gains no line
- **AND** after an unlock (set hidden), a Development paper B006 moved to Validation and back moves with no warning as a `benchmark_set` change, though B006 was in the unlock's list of benchmark papers
Pinned by: `tests/unit/test_validation_reuse.py::test_unshown_move_is_undoable`

#### Scenario: A former validation paper moved back
- **WHEN** B002, a former validation paper, is moved back to Validation
- **THEN** it moves with no warning, is listed under "Validation, used", and the command and the page show beside it the notice that it is a used validation paper and will not count in the validation set
Pinned by: `tests/unit/test_validation_reuse.py::test_former_paper_moved_back_is_used`

#### Scenario: An applied split moves shown papers
- **WHEN** v3 is frozen and an applied split (`wider benchmark split --apply`, or "Apply this split" on the page) would move B002 and B003 from Validation to Development
- **THEN** the command prints a notice naming B002 and B003 and "Add --confirm to move them. Nothing was done." and exits 2, `POST /api/benchmark/split` with `apply` and without `confirm` answers 200 with `confirm_needed` true, and the page shows the same dialog
- **AND** with `--confirm` the split is applied in one `validation_moved` change; a split that moves no shown paper is one undoable `benchmark_set` change
Pinned by: `tests/unit/test_validation_reuse.py::test_split_moving_shown_papers_needs_confirm`

### Requirement: Moving the used validation papers to Development
One action SHALL move every used validation paper to Development after the same warning, as one `validation_moved` change that cannot be undone, and then invite the researcher to add new validation papers from sources independent of the query.
Surfaces: CLI `wider benchmark move-used [--confirm]`; UI Setup page, seed-papers step, "Move used validation papers to Development".

#### Scenario: Moving the used papers
- **WHEN** B002 and B003 are used validation papers and `wider benchmark move-used` runs, or the seed-papers step's button "Move used validation papers to Development" is pressed
- **THEN** without `--confirm` the command prints "Move the 2 used validation papers (B002, B003) to Development? They become former validation papers: the report, the methods draft and the package list them, and the move cannot be undone." and "Add --confirm to move them. Nothing was done." and exits 2; `POST /api/benchmark/move-used` without `confirm` answers 200 with `confirm_needed` true; the page shows the dialog (focus on "Cancel")
- **AND** confirmed, both move to Development as former validation papers in one `validation_moved` change, and the command and the page show "Add new validation papers from sources independent of the query: prior reviews, citation chaining from seed papers, colleagues or other databases (wider benchmark add --set Validation --source <source>)."; the seed-papers step keeps showing it until a Validation paper is added
Pinned by: `tests/unit/test_validation_reuse.py::test_move_used_moves_all_and_invites`, `tests/unit/test_setup_js_node.py::test_move_used_button`

#### Scenario: No used paper
- **WHEN** no validation paper is used
- **THEN** `wider benchmark move-used` prints "no used validation paper" and exits 1, `POST /api/benchmark/move-used` answers 400, and the page shows no button
Pinned by: `tests/unit/test_validation_reuse.py::test_move_used_moves_all_and_invites`

### Requirement: Turning validation hiding back on records the papers shown
Turning `identification.validation_after_freeze` from off to on (set or reset, page or CLI) SHALL record the papers then in the validation set as shown, kept as validation, in one `validation_peek` change that cannot be undone: while hiding was off they were shown. A later revision then records them as used, and moving one warns. A hand edit of `project.yaml` bypasses this.
Surfaces: CLI `wider protocol set|reset`; UI Settings, protocol table (Set, Reset, Reset everything).

#### Scenario: Hiding turned back on
- **WHEN** B002 and B003 are in the validation set, `identification.validation_after_freeze` was set to false, and it is set to true with `wider protocol set` or `wider protocol reset`, or with Set or Reset on the page
- **THEN** History gains a `validation_peek` change "validation hiding turned back on: 2 validation papers shown while it was off recorded as seen (kept as validation)" that cannot be undone, the command prints that line with "; logged in the history", and `POST /api/protocol/set|reset` returns it as `notice`, shown under the protocol table
- **AND** saving v4 then shows the revision notice for the 2 papers, and moving B002 to Development warns first (exit 2 without `--confirm`); setting it to true while it is already on records nothing
Pinned by: `tests/unit/test_validation_reuse.py::test_hiding_turned_back_on_records_papers_as_seen`
