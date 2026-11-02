# prior-reviews Specification

## Purpose
Prior reviews let the researcher register earlier reviews of the same construct (an export, a pasted reference list or a review PDF) during setup, so WIDER can check how many of their included studies each platform export and the deduplicated corpus contain. Parsing and coverage run locally; resolving papers on OpenAlex and citation chaining are opt-in and feed a `citation_chain` import file into the import stage.

## Requirements

### Requirement: Add a prior review from a file or pasted text
The system SHALL add a prior review from a file (RIS, CSV, TSV, XLSX, XLS, BibTeX, TXT or PDF) or pasted text, copy any file into `prior_reviews/`, parse it locally and store its papers in `prior_review_papers`, deduplicated within the review by DOI and then by normalised title. The label defaults to the file stem or "pasted list".
Surfaces: CLI `wider prior-reviews add [FILE] [--text] [--label]`; UI Setup step 3 (paste box, label, file picker).

#### Scenario: Pasted lines of different kinds
- **WHEN** the pasted text holds a bare DOI, a bare title and a full reference with a year
- **THEN** the first becomes a DOI-only paper, the second a title-only paper and the third a paper with title and year 2019
- **AND** pasted RIS text is read as RIS records
Pinned by: `tests/unit/test_prior_reviews.py::test_rows_from_text_modes`

#### Scenario: Pasted list with repeated DOI and title
- **WHEN** the researcher pastes `10.1000/aaa`, `10.1000/AAA`, "Employee voice in teams", "Employee Voice in Teams." and "New title only"
- **THEN** the review is stored with 3 papers
- **AND** `wider prior-reviews list` shows it with its label and paper counts
Pinned by: `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

#### Scenario: Same file added twice
- **WHEN** a file with the same SHA-256 as an existing review is added
- **THEN** the add is refused with "this file was already added as review #N (label)"
- **AND** the CLI exits with code 1 and the UI upload returns HTTP 400
Pinned by: `tests/unit/test_prior_reviews.py::test_prior_review_routes`

#### Scenario: Unsupported file or nothing parsed
- **WHEN** a file with another suffix (for example `.docx`) is uploaded, or pasted text contains no DOI, title or reference
- **THEN** the add fails with a message and nothing is stored
- **AND** the UI returns HTTP 400

### Requirement: Prior-review files stay private to the project
The system SHALL create `prior_reviews/.gitignore` containing `*` and `!.gitignore` when the folder is first used, and SHALL NOT send prior-review files or their contents to any model or remote service except through the opt-in switches defined below. The Setup step shows the note that these files are never uploaded or sent to any model or service.
Surfaces: CLI `wider prior-reviews add` (prints the policy note); UI Setup step 3 policy box.

#### Scenario: First file added
- **WHEN** the first prior review is added to a project
- **THEN** `prior_reviews/.gitignore` exists and starts with `*`
Pinned by: `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

### Requirement: Reference parsing of review PDFs
The system SHALL extract the reference section of a review PDF locally with the optional `pypdf` dependency, using the last "References", "Reference list", "Bibliography", "Literature cited" or "Works cited" heading, and split it into numbered, author-year or paragraph entries. Each paper gets a confidence of `doi`, `parsed` or `heuristic`.
Surfaces: CLI `wider prior-reviews add FILE.pdf`; UI Setup step 3 file picker (papers table shows confidence badges).

#### Scenario: No reference heading
- **WHEN** the text has no reference heading
- **THEN** the last 30 % of the document is parsed
- **AND** the warning "no reference heading found; parsed the last 30 % of the document" is returned
Pinned by: `tests/unit/test_prior_reviews.py::test_reference_section_without_heading_warns`

#### Scenario: Numbered and author-year lists
- **WHEN** the reference section is a numbered list or an author-year list
- **THEN** one paper per entry is produced with its title, year and DOI where present
Pinned by: `tests/unit/test_prior_reviews.py::test_reference_text_numbered_and_author_year`

#### Scenario: PDF support not installed
- **WHEN** a PDF is added and `pypdf` is not installed
- **THEN** the add fails with a message naming `wider[pdf]`
Pinned by: `tests/unit/test_prior_reviews.py::test_pdf_needs_optional_extra`

#### Scenario: Scanned or mostly unparsed PDF
- **WHEN** the PDF has no extractable text, or more than 50 % of entries are parsed heuristically
- **THEN** a warning asks the researcher to paste the reference list instead or to check titles

### Requirement: Data-policy switches default to off
The system SHALL keep a `prior_reviews` policy in `project.yaml` with `allow_openalex_lookup`, `allow_llm_excerpts` and `none_known` (all default false) and `max_citing_per_paper` (default 2000, at least 1), rejecting unknown fields and invalid values without saving.
Surfaces: CLI `wider prior-reviews policy` (`--allow-lookup`, `--allow-excerpts`, `--none-known`, `--max-citing N` and their negations); UI Setup step 3 checkboxes, Settings page (read-only).

#### Scenario: Round trip
- **WHEN** the lookup switch is turned on with `max_citing_per_paper` 10
- **THEN** reloading the project shows lookup on, excerpts still off and the cap at 10
- **AND** setting `max_citing_per_paper` to 0 fails (UI HTTP 400) and nothing changes
Pinned by: `tests/unit/test_prior_reviews.py::test_policy_round_trip`

### Requirement: Identifier lookups are gated
The system MUST refuse `resolve`, `chain` and chain-and-export with a policy error before any network client is created while `allow_openalex_lookup` is off.
Surfaces: CLI `wider prior-reviews resolve` and `wider prior-reviews chain` (exit code 2); UI Setup step 3 and Import page buttons (disabled while off; API returns HTTP 403).

#### Scenario: Lookup switch off
- **WHEN** resolve or chain is called with the switch off
- **THEN** a policy error is raised and no request is made
- **AND** `POST /api/prior-reviews/resolve` and `POST /api/prior-reviews/chain` return HTTP 403
Pinned by: `tests/unit/test_prior_reviews.py::test_gate_blocks_before_any_network`

### Requirement: Local coverage check
The system SHALL match prior-review papers locally (DOI, then exact normalised title, then Jaro-Winkler at or above `jw_benchmark`, flagged for confirmation) against each imported platform (`platform:<key>`) or the deduplicated corpus (`corpus`), store matches in `prior_review_matches` and report found, share and missed papers per review.
Surfaces: CLI `wider prior-reviews coverage [--scope platforms|corpus]`; UI Import page "Prior-review coverage" card (Per platform / Deduplicated corpus).

#### Scenario: Nothing imported yet
- **WHEN** coverage is checked before any import
- **THEN** it fails with "nothing imported yet" (CLI exit 1, API HTTP 400)
Pinned by: `tests/unit/test_prior_reviews.py::test_coverage_found_and_missed`

#### Scenario: Corpus scope before deduplication
- **WHEN** the `corpus` scope is requested and `records` is empty
- **THEN** it fails with "no deduplicated corpus yet (run wider dedup run)"

#### Scenario: Found and missed papers
- **WHEN** platforms have been imported
- **THEN** each review lists found/n per platform scope and its missed papers
- **AND** the CLI prints at most 10 missed papers per review
Pinned by: `tests/unit/test_prior_reviews.py::test_coverage_found_and_missed`

### Requirement: Resolve papers on OpenAlex
The system SHALL, with the lookup switch on, resolve prior-review papers without an `openalex_id` on OpenAlex by DOI in batches of 50 and otherwise by title search, set `openalex_id` and `resolved_at`, and fill an empty year or title. Papers that stay unresolved are attempted again on the next run.
Surfaces: CLI `wider prior-reviews resolve`; UI Setup step 3 "Resolve on OpenAlex" button.

#### Scenario: Resolve twice
- **WHEN** three papers are resolved and one is not found
- **THEN** the result is attempted 3, resolved 2, unresolved 1
- **AND** a second resolve attempts only the 1 unresolved paper
Pinned by: `tests/unit/test_prior_reviews.py::test_resolve_chain_export_import_dedup`

### Requirement: Citation chaining with a forward cap
The system SHALL, with the lookup switch on, fetch references (`backward`) and citing works (`forward`) of resolved papers from OpenAlex into `citation_chain`, chaining each paper at most once per direction and capping citing works per paper at `max_citing_per_paper`. Other directions are rejected.
Surfaces: CLI `wider prior-reviews chain [--direction both|backward|forward] [--limit N]`; UI Setup step 3 and Import page "Citation chaining" card.

#### Scenario: Cap reached
- **WHEN** a paper has more citing works than `max_citing_per_paper`
- **THEN** forward chaining stops at the cap with the warning "forward chaining of '...' stopped at the cap of N citing works"
Pinned by: `tests/unit/test_prior_reviews.py::test_forward_cap_warns`

#### Scenario: Unresolved papers and repeat runs
- **WHEN** chaining runs while some papers are unresolved, and then runs again
- **THEN** the first run warns that N papers are not resolved and were skipped
- **AND** the second run chains 0 papers
Pinned by: `tests/unit/test_prior_reviews.py::test_resolve_chain_export_import_dedup`

### Requirement: Chained works become an import file
The system SHALL write chained works not yet marked exported, one row per OpenAlex work, to `inputs/citation_chain/chain_<stamp>.csv` with the headers Title, Abstract, DOI, Year, Authors, Journal, ID, Chain Source, Chain Direction, mark them exported, and record a `setup` stage result with next stage `import`. The import stage reads the file as platform `citation_chain`.
Surfaces: CLI `wider prior-reviews chain` (prints "next: wider import run"); UI Setup step 3 and Import page chaining buttons.

#### Scenario: Export then import
- **WHEN** chaining finds 3 distinct works
- **THEN** a `chain_<stamp>.csv` is written with Chain Source like "Prior A#1; Prior A#2" and Chain Direction "backward+forward"
- **AND** an immediate second export writes no file and `wider import run` imports 3 records for `citation_chain`
Pinned by: `tests/unit/test_prior_reviews.py::test_resolve_chain_export_import_dedup`

### Requirement: Prior-review titles in prompts only with the excerpt switch
The system SHALL add up to 200 prior-review titles to construct-proposal prompts under "Titles of studies included in prior reviews" only while `allow_llm_excerpts` is on, and record the count as `prior_review_titles_n` in the run context.
Surfaces: CLI `wider prior-reviews policy --allow-excerpts`; UI Setup step 3 excerpt checkbox.

#### Scenario: Switch off then on
- **WHEN** a prompt is built with the switch off, and again with it on
- **THEN** the first prompt has no prior-review titles and `prior_review_titles_n` is 0
- **AND** the second includes the titles and `prior_review_titles_n` is 2
Pinned by: `tests/unit/test_prior_reviews.py::test_prompt_titles_only_with_excerpt_switch`

### Requirement: Remove a prior review
The system SHALL remove a prior review with its papers, matches and chained works, and delete the copied file when no other review uses it. An unknown review id fails.
Surfaces: CLI `wider prior-reviews remove ID` (exit 1 when unknown); UI Setup step 3 review table "Remove" button (`DELETE /api/prior-reviews/{id}`, HTTP 404 when unknown).

#### Scenario: Remove one of two reviews
- **WHEN** one of two reviews is removed
- **THEN** its copied file is gone and one review remains with its own papers
- **AND** removing review 999 fails
Pinned by: `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

### Requirement: Setup step completion
The system SHALL mark the prior-reviews setup step done when at least one review is added or `none_known` is true.
Surfaces: UI Setup step 3 ("I know of none" / "I do know of some" toggle); CLI `wider prior-reviews policy --none-known/--some-known`.

#### Scenario: Researcher knows of none
- **WHEN** no review is added and the researcher chooses "I know of none"
- **THEN** `none_known` is saved as true and setup step 3 shows as done
Pinned by: `tests/unit/test_prior_reviews.py::test_prior_review_routes`

### Requirement: Command-line coverage shows matches to confirm
The system SHALL print, for each prior review in `wider prior-reviews coverage`, the number of papers found only by a fuzzy title match that still need confirmation, the same count the Import page shows as "N fuzzy match(es) to confirm".
Surfaces: CLI `wider prior-reviews coverage [--scope platforms|corpus]`; UI Import page "Prior-review coverage" card.

#### Scenario: One fuzzy match
- **WHEN** a review has 3 papers found by DOI or exact title and 1 found only by fuzzy title
- **THEN** the CLI line for that review reports 1 fuzzy match to confirm
- **AND** the Import page shows "1 fuzzy match(es) to confirm" for the same review

#### Scenario: Nothing to confirm
- **WHEN** no paper of a review is matched only by fuzzy title
- **THEN** no to-confirm count is printed for that review

### Requirement: Resolving a limited number of papers
The system SHALL let `wider prior-reviews resolve --limit N` attempt only the first N unresolved papers, in paper order, and attempt all unresolved papers without the option. The lookup switch gate applies unchanged.
Surfaces: CLI `wider prior-reviews resolve [--limit N]`; UI Setup step 3 "Resolve on OpenAlex" (all unresolved papers).

#### Scenario: Resolve in two steps
- **WHEN** five papers are unresolved and the researcher runs `wider prior-reviews resolve --limit 2`
- **THEN** 2 papers are attempted
- **AND** a following `wider prior-reviews resolve` attempts the remaining 3

### Requirement: Pasted text added twice is reported as duplicate text
The system SHALL refuse pasted text identical to the text of an existing review with "this text was already added as review #N (label)", storing nothing, and keep the file wording for duplicate files.
Surfaces: CLI `wider prior-reviews add --text` (exit code 1); UI Setup step 3 "Add pasted list" (HTTP 400).

#### Scenario: Same list pasted twice
- **WHEN** the researcher pastes the same reference list a second time
- **THEN** the add is refused with "this text was already added as review #1 (pasted list)"
- **AND** the message does not mention a file and only one review exists

### Requirement: A chained work is exported only once
The system MUST NOT write an OpenAlex work to a new `chain_<stamp>.csv` when it was already written to an earlier chain file of the project, even when a later chaining run reaches it from another paper or in another direction.
Surfaces: CLI `wider prior-reviews chain`; UI Setup step 3 and Import page chaining buttons.

#### Scenario: Work reached again in a later run
- **WHEN** a first chaining run exports work W through backward chaining and a later forward run of another paper also finds W along with one new work
- **THEN** the later chain file holds only the new work
- **AND** `wider import run` imports W once for `citation_chain`

### Requirement: Setup step offers the chaining direction
The system SHALL let the researcher choose the chaining direction (references and citing works, references only, or citing works only) next to the "Chain citations" button of Setup step 3, as the Import page does, and send that direction with the request.
Surfaces: UI Setup step 3 "Chain citations" with direction select; CLI `wider prior-reviews chain --direction both|backward|forward`.

#### Scenario: References only from setup
- **WHEN** the researcher chooses "references only" in Setup step 3 and clicks "Chain citations"
- **THEN** only backward chaining runs and no citing works are fetched
- **AND** the default choice is references and citing works
