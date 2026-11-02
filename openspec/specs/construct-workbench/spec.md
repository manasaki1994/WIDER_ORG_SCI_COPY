# construct-workbench Specification

## Purpose
The construct workbench is stage `construct` of the WIDER procedure: it follows `setup` and must be done before `query`. It turns the research question and the seed (benchmark) papers into versioned, reviewable artifacts (cells, codebook, synonyms, anchors, near_miss, adjacent) that later stages read. The researcher enters, resolves and splits seed papers, asks an LLM for grounded proposals, edits the YAML, then approves and freezes the versions later stages should use; the stage is done when cells and synonyms each have an approved or frozen version.

## Requirements

### Requirement: Versioned, schema-validated artifacts
The system SHALL save each artifact edit as a new immutable version `artifacts/<kind>/v<N>.yaml` with a row in the `artifacts` table (version, path, sha256, status, origin, parent_version, note), after validating it against `wider/construct/schemas/<kind>.json`. Synonyms MUST also parse as a Query IR. Two saves at once each get their own version.
Surfaces: CLI `wider construct save|show|list`; UI Construct workbench page, artifact tabs, "Save as new version".

#### Scenario: Saving a valid edit creates a new draft version
- **WHEN** the researcher saves an edited cells artifact while version 1 exists
- **THEN** version 2 is written to `artifacts/cells/v2.yaml` with status `draft`, origin `manual` and `parent_version` 1
- **AND** the version list on the page and `wider construct list` show each version's first 12 sha256 characters and its origin, and version 2 "from v1"
Pinned by: `tests/unit/test_construct.py::test_versioning_status_and_diff`, `tests/unit/test_static_followup.py::test_construct_version_list_names_sha_parent_and_announces_a_refusal`

#### Scenario: Invalid artifacts are refused
- **WHEN** a cells artifact lacks required fields, a synonyms logic tree references an unknown block key, or an anchors map holds an anchor too short for the schema
- **THEN** the save fails with a validation error and no version is created
- **AND** the UI save (`POST /api/construct/{kind}/save`) returns HTTP 400 with the error text, which the page shows as an error "not saved: …" in a status area screen readers announce
Pinned by: `tests/unit/test_construct.py::test_validation_rejects_bad_artifacts`, `tests/unit/test_static_followup.py::test_construct_version_list_names_sha_parent_and_announces_a_refusal`

#### Scenario: Importing a YAML or JSON file
- **WHEN** the researcher runs `wider construct save cells cells.yaml`
- **THEN** the file is validated and saved as a new draft version with origin `import`
- **AND** a `.json` file is read the same way
Pinned by: `tests/unit/test_construct.py::test_import_yaml_file`

#### Scenario: Two saves at once
- **WHEN** two saves of one kind start together (the page and the CLI, or two pages)
- **THEN** they are saved as two versions, each in its own file, and neither fails
- **AND** on the page, "Save as new version" and "Parse and save as new synonyms version" are disabled while their request runs, so a double click saves one version
Pinned by: `tests/unit/test_construct.py::test_two_saves_at_once_get_their_own_versions`, `tests/unit/test_ui_followup_r17.py::test_two_saves_on_two_threads_get_their_own_versions`, `tests/unit/test_static_followup.py::test_saving_a_pasted_search_string_sends_one_request`, `tests/unit/test_static_followup.py::test_save_as_new_version_sends_one_request`, `tests/unit/test_ui_followup_r30.py::test_a_double_click_on_either_save_button_saves_one_version`, `tests/unit/test_ui_followup_r30.py::test_a_save_from_the_page_and_one_from_the_cli_at_once_get_their_own_versions`

### Requirement: Draft, approved and frozen statuses
The system SHALL move a version from `draft` to `approved` to `frozen`, with later stages reading the newest approved or frozen version. A frozen version MUST NOT change status, and a version whose file was edited on disk after saving (sha256 mismatch) MUST NOT change status.
Surfaces: CLI `wider construct approve|freeze <kind> <version>`; UI Construct workbench page, "Approve" (drafts only) and "Freeze" (with confirmation).

#### Scenario: Approve then freeze
- **WHEN** the researcher approves cells v2 and then freezes it
- **THEN** the newest approved version of cells is v2
- **AND** an attempt to set v2 back to draft fails with "frozen; create a new version instead"
Pinned by: `tests/unit/test_construct.py::test_versioning_status_and_diff`

#### Scenario: A file edited on disk cannot change status
- **WHEN** `artifacts/cells/v1.yaml` is modified by hand after saving and the researcher approves v1
- **THEN** the approval is refused because the file no longer matches its stored sha256
- **AND** the researcher is told to save it as a new version first
Pinned by: `tests/unit/test_construct.py::test_versioning_status_and_diff`

#### Scenario: Status changes are journalled; a freeze is final
- **WHEN** an approval is undone from the change journal and a later freeze is listed there as an `artifact_status` change
- **THEN** the approved version is back to `draft`
- **AND** the freeze is marked not undoable because a freeze is final
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

### Requirement: Diff and restore of versions
The system SHALL show a unified diff between two versions of one kind, and SHALL restore an older version by saving its content as a new draft with origin `restore` and the note `restored from vN`. Restoring the newest version MUST fail.
Surfaces: CLI `wider construct diff <kind> <a> <b>`, `wider construct restore <kind> <version>`; UI Construct workbench page, "Diff with previous version" and "Restore as new version".

#### Scenario: Restore an older version
- **WHEN** anchors v1 and v2 exist and the researcher restores v1
- **THEN** v3 is created as a draft whose content equals v1
- **AND** restoring v3 fails with "already the newest" (HTTP 409 from `POST /api/construct/{kind}/{version}/restore`)
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`, `tests/unit/test_history.py::test_history_routes_blind_and_undo_last`

#### Scenario: Restore from the command line
- **WHEN** the researcher runs `wider construct restore anchors 1`
- **THEN** the command exits 0 and reports the new version, for example "restored as v3"
- **AND** the new version is a draft
Pinned by: `tests/unit/test_history.py::test_history_cli`

#### Scenario: Diff two versions
- **WHEN** cells v1 and v2 differ in one cell question
- **THEN** the diff shows the old line prefixed with `-` and the new line prefixed with `+`
- **AND** the diff headers name `cells v1` and `cells v2`
Pinned by: `tests/unit/test_construct.py::test_versioning_status_and_diff`

### Requirement: Proposal prompts grounded in the development benchmark
The system SHALL build each proposal prompt from the research questions, the clarifications for stage `construct` and the Development-set benchmark papers (abstracts cut to 350 words). Validation-set papers MUST NOT appear in any prompt. Prior-review titles (at most 200) appear only when `prior_reviews.allow_llm_excerpts` is on.
Surfaces: CLI `wider construct propose <kind> --show-prompt`; UI Construct workbench page, "Show prompt".

#### Scenario: Validation abstracts stay out of the prompt
- **WHEN** the benchmark holds one Development and one Validation paper and the cells prompt is built
- **THEN** the prompt contains the Development paper's title
- **AND** it contains neither the Validation paper's title nor its abstract, and the prompt info reports one benchmark paper
Pinned by: `tests/unit/test_construct.py::test_benchmark_sync_and_prompt_hides_validation`

#### Scenario: No development papers
- **WHEN** `benchmark.csv` holds no Development paper and a proposal is requested
- **THEN** the request fails with "benchmark.csv has no Development papers; add them first (wider benchmark fetch)"
- **AND** no version is saved

### Requirement: Proposal order between artifact kinds
The system SHALL build codebook, synonyms and anchors proposals on the newest approved cells, and synonyms and near_miss proposals on the newest approved codebook. An anchors proposal MUST fail without approved cells, and a near_miss proposal MUST fail without an approved codebook.
Surfaces: CLI `wider construct propose <kind>`; UI Construct workbench page, "Propose with LLM" and "Show prompt" (HTTP 400 on failure).

#### Scenario: Anchors need approved cells
- **WHEN** the researcher asks for an anchors prompt before any cells version is approved
- **THEN** the request fails with "anchors need an approved cells artifact first"
- **AND** the UI prompt route returns HTTP 400
Pinned by: `tests/unit/test_construct.py::test_benchmark_sync_and_prompt_hides_validation`

### Requirement: LLM proposals saved as drafts with a run record
The system SHALL send the prompt to the provider set for stage `construct` (or `--provider`), validate the returned JSON against the kind's schema, save it as a draft with origin `llm`, and write prompts, raw responses, run spec and call log to `runs/construct/<stamp>_<kind>/`. A failed proposal MUST raise a `proposal_failed` question instead of saving.
Surfaces: CLI `wider construct propose <kind> [--provider] [--note]`; UI Construct workbench page, "Propose with LLM" with a provider select.

#### Scenario: Proposing cells and then anchors
- **WHEN** the researcher proposes cells, approves the result, and proposes anchors for a construct with two cells
- **THEN** cells v1 is a draft with origin `llm` and the anchors version holds one anchor per cell
- **AND** the anchors were made in two calls (the first cell sets the style, the rest run in parallel) and `runs/construct/` exists
Pinned by: `tests/unit/test_construct.py::test_propose_with_fake_provider`

### Requirement: Benchmark paper table and abstract fetch
The system SHALL mirror `benchmark.csv` into the `benchmark_papers` table on every read, keeping fetched abstracts and dropping papers no longer in the CSV, and SHALL fetch abstracts by DOI from OpenAlex, Crossref, Europe PMC and Semantic Scholar. A fetch MUST NOT replace an abstract with a shorter one.
Surfaces: CLI `wider benchmark list`, `wider benchmark fetch [--all]`; UI Concepts and synonyms page, "Seed papers" card, "Fetch missing abstracts".

#### Scenario: Sync follows the CSV
- **WHEN** `benchmark.csv` lists B1 (Development) and B2 (Validation)
- **THEN** the benchmark table returns rows for B1 and B2
- **AND** a paper removed from the CSV disappears from the table on the next read
- **AND** the Concepts and synonyms page shows them in a card headed "Seed papers"
Pinned by: `tests/unit/test_construct.py::test_benchmark_sync_and_prompt_hides_validation`, `tests/unit/test_construct_js_node.py::test_seed_papers_card_lists_b001_and_b002`

#### Scenario: Default fetch skips complete abstracts
- **WHEN** the researcher fetches without `--all` and a paper already has an abstract of at least `protocol.corpus.thin_words` words
- **THEN** that paper gets status `kept` and is not looked up
- **AND** the other papers get `fetched`, `not_found` or `too_short`

#### Scenario: A paper without an abstract source
- **WHEN** no source returns an abstract for a paper, or the paper has no DOI
- **THEN** that paper gets status `not_found`
- **AND** a `seed_unresolved` question asks for the correct DOI

### Requirement: Seed paper entry
The system SHALL add seed papers to `benchmark.csv` from DOIs, titles, pasted lines, or a CSV or RIS file, giving each new paper the next identifier of the form `B001`, the set Development unless another is given, and a source (`researcher` unless another is given). A paper whose DOI or normalised title is already present MUST be skipped.
Surfaces: CLI `wider benchmark add [--doi] [--title] [--file] [--set] [--source] [--resolve]`, `wider benchmark set|remove`; UI Setup page, seed-papers step.

#### Scenario: DOIs and titles get sequential identifiers
- **WHEN** the researcher adds `https://doi.org/10.1000/synth.001` and `doi:10.1000/synth.002`, then one title for Validation
- **THEN** the papers get B001 and B002 with bare DOIs and set Development
- **AND** the title-only paper gets B003, set Validation and no DOI
Pinned by: `tests/unit/test_seed.py::test_add_by_doi_and_title_then_resolve`

#### Scenario: Duplicates are skipped on input
- **WHEN** pasted lines (a line that parses as a DOI is a DOI, otherwise a title) repeat a DOI and repeat a title in another case
- **THEN** only two papers are added
- **AND** a later title that normalises to an existing one adds nothing
Pinned by: `tests/unit/test_seed.py::test_add_lines_and_duplicates`

#### Scenario: Seed papers from a CSV or RIS file
- **WHEN** the researcher adds a CSV with title, doi, year and set columns, and an RIS file for Validation
- **THEN** each record becomes a paper with its title, DOI, year and set, a blank set meaning Development
- **AND** the RIS paper keeps its first author
Pinned by: `tests/unit/test_seed.py::test_add_from_csv_and_ris`

#### Scenario: Reassign and remove
- **WHEN** the researcher sets B002 to `validation`, then to `dev`, and later removes B001
- **THEN** a set name starting with `val` means Validation and any other means Development, and an unknown identifier fails
- **AND** after removing B001 the next new paper still gets the next free number
Pinned by: `tests/unit/test_seed.py::test_assignment_remove_and_split`

#### Scenario: Default source
- **WHEN** a paper is added without `--source`
- **THEN** `benchmark.csv` holds `researcher` in its `source` column
Pinned by: `tests/unit/test_seed.py::test_source_column`

### Requirement: Seed paper lookup
The system SHALL resolve seed papers by DOI through OpenAlex then Crossref, and by title through OpenAlex search accepting a Jaro-Winkler similarity of at least `identification.jw_benchmark` (0.88), filling only empty fields. Unresolved papers MUST be retried on the next run; resolved, complete papers MUST NOT be looked up again.
Surfaces: CLI `wider benchmark resolve [--all]`, `wider benchmark add --resolve`; UI Setup page, seed-papers step.

#### Scenario: Lookup fills fields and finds duplicates
- **WHEN** B001's DOI is known to OpenAlex, B002's only to Crossref, and B003 is a title of the same paper as B001
- **THEN** B001 is resolved from `openalex` and B002 from `crossref`, with title, year and first author filled
- **AND** B003 receives B001's DOI and is reported as `duplicate_of` B001
Pinned by: `tests/unit/test_seed.py::test_add_by_doi_and_title_then_resolve`

#### Scenario: Unresolved papers are flagged and retried
- **WHEN** neither a DOI nor a title finds a match
- **THEN** both papers are `unresolved`, with the note "not found" or a note on how similar the closest title was
- **AND** a second run looks them up again but skips papers already resolved and complete
Pinned by: `tests/unit/test_seed.py::test_unresolved_is_flagged_and_resolve_is_idempotent`

### Requirement: Development and validation split
The system SHALL suggest a seeded random split of the seed papers with development share `identification.development_share` (0.57) and the project seed, changing `benchmark.csv` only when applied. With two or more papers each set MUST get at least one paper, and a share outside 0 to 1 MUST be refused.
Surfaces: CLI `wider benchmark split [--dev-share] [--seed] [--apply]`; UI Setup page, seed-papers step.

#### Scenario: Seven papers at the default share
- **WHEN** seven seed papers exist and a split is suggested
- **THEN** four papers go to Development and three to Validation, and a repeated suggestion is identical
- **AND** applying a split with share 0.3 makes exactly the two suggested papers Development, while share 1.5 is refused
Pinned by: `tests/unit/test_seed.py::test_assignment_remove_and_split`

### Requirement: Adjacent literatures register
The system SHALL keep the adjacent literatures as the versioned `adjacent` artifact, a `literatures` list of entries with key, name, description, differs_from_construct, discriminating_cue, example_phrases, origin and status (`proposed`, `approved`, `rejected`). Every change MUST save a new version, and only approved entries are used by later stages.
Surfaces: CLI `wider construct adjacent list|add|seed|propose|approve|reject`; UI Construct workbench page, "Adjacent literatures" card.

#### Scenario: Seed, add and approve
- **WHEN** a codebook with one adjacent literature exists and the researcher seeds from it, then adds a second literature
- **THEN** the seeded entry is `proposed` with origin `researcher`, seeding again adds nothing, and the added entry is `approved`
- **AND** after approving the seeded entry both keys are approved, and rejecting one removes it from the approved list
Pinned by: `tests/unit/test_adjacent.py::test_seed_add_approve_round_trip`

#### Scenario: Entries are validated
- **WHEN** an adjacent entry has a key with spaces or an unknown origin
- **THEN** schema validation reports an error
- **AND** a valid list is saved as draft version 1
Pinned by: `tests/unit/test_adjacent.py::test_artifact_validation`

#### Scenario: UI routes edit the register
- **WHEN** the UI adds a literature, rejects it, edits its name and asks the model to propose from the question
- **THEN** each call saves a new version, a status change for an unknown key returns HTTP 400, and the model's proposal adds its new key
- **AND** `GET /api/adjacent` returns the newest version, the entries and the approved subset
Pinned by: `tests/unit/test_adjacent.py::test_ui_routes`

### Requirement: Finding adjacent literatures in the ranking
The system SHALL cluster the top of an anchor ranking, skip clusters holding a benchmark paper, and ask the LLM to name an adjacent literature per remaining cluster of at least `--min-size` records (default 3). A candidate MUST be accepted only when its quotes are verified against the cluster's records.
Surfaces: CLI `wider construct adjacent find [--min-size] [--no-merge] ...`; UI Construct workbench page, "Find in ranking (LLM)".

#### Scenario: Unverified quotes are refused
- **WHEN** three clusters exist, one holds a benchmark paper, and the model returns one candidate with verified quotes and one without
- **THEN** two clusters are considered and only the verified candidate is added, as `proposed` with origin `tool` and its cluster, terms and quotes as evidence
- **AND** the skipped candidate's reason says its quotes were not verified, and `candidates.json` is written under `runs/construct/<stamp>_adjacent_find/`
Pinned by: `tests/unit/test_adjacent.py::test_find_candidates_with_fake_provider`

### Requirement: Seed-paper lookups need online-lookup consent
The system MUST NOT resolve seed papers or fetch their abstracts without the project's online-lookup consent. Without it, the command asks (y/N) in an interactive terminal and otherwise exits with code 2 naming `wider lookups allow`; the UI shows the consent dialog. Adding, listing, splitting and removing seed papers stay local and need no consent.
Surfaces: CLI `wider benchmark resolve|fetch`, `wider benchmark add --resolve`; UI Setup seed-papers step, Construct "Fetch missing abstracts".

#### Scenario: Fetch without consent in a script
- **WHEN** `wider benchmark fetch` runs non-interactively with consent off
- **THEN** it exits with code 2, no seed paper's abstract or fetch time changes and no `seed_unresolved` question is raised

#### Scenario: Local seed work without consent
- **WHEN** consent is off and the researcher adds two DOIs without `--resolve`, then suggests a split
- **THEN** both commands exit 0 and nothing is sent to any lookup service

#### Scenario: Resolve after consent
- **WHEN** the researcher runs `wider lookups allow` and then `wider benchmark resolve`
- **THEN** the papers are resolved as before and a `seed_resolve` entry per service contacted is logged

### Requirement: Adjacent proposal from the question reports what it added
The system SHALL, after the model proposes adjacent literatures from the question, report how many it proposed, the new `adjacent` version (or that nothing new was added) and the key, name and discriminating cue of each added entry, and exit 0. A failed model call MUST print the error, save no version and exit with code 1.
Surfaces: CLI `wider construct adjacent propose [--provider]`; UI Construct workbench page, "Adjacent literatures" card, "Propose from question (LLM)".

#### Scenario: Two new literatures proposed
- **WHEN** the model proposes two literatures and one has the same name as an existing entry
- **THEN** the command exits 0, reports 2 proposed and 1 added with the new version number
- **AND** it lists the added entry's key, name and discriminating cue

#### Scenario: Nothing new
- **WHEN** every proposed literature is already in the register
- **THEN** the command exits 0, reports that nothing new was added and no new version is saved

#### Scenario: Model call fails
- **WHEN** the backend returns an error for the proposal
- **THEN** the command prints the error without a traceback, exits with code 1 and the register is unchanged

### Requirement: Only proposable kinds can be proposed
The system SHALL accept `wider construct propose` only for cells, codebook, synonyms, anchors and near_miss. `adjacent` MUST be refused with a message naming `wider construct adjacent propose`, `prompts` with a message that prompts are edited and saved, not proposed, and any other kind with the list of valid kinds, all before any model call.
Surfaces: CLI `wider construct propose <kind> [--show-prompt]`; UI Construct workbench page, "Propose with LLM" and "Show prompt" (HTTP 400).

#### Scenario: Adjacent through the general command
- **WHEN** the researcher runs `wider construct propose adjacent`
- **THEN** the command exits with code 2 and names `wider construct adjacent propose`
- **AND** no model is called, no version is saved and no run folder is created

#### Scenario: Prompts kind in the UI
- **WHEN** `POST /api/construct/prompts/propose` or `GET /api/construct/prompts/prompt` is called
- **THEN** each answers HTTP 400 with the message that prompts are not proposed by the model

#### Scenario: Unknown kind
- **WHEN** the researcher runs `wider construct propose nope --show-prompt`
- **THEN** the command exits with code 2 and lists cells, codebook, synonyms, anchors and near_miss

### Requirement: Failed adjacent proposal on the page is a message
The system SHALL answer a failed adjacent-literature proposal (a model error, or a reply that does not fit the expected shape) from the page with HTTP 400 and a message starting "adjacent proposal failed", never HTTP 500, and from the CLI with that message and exit code 1, never a traceback. The register stays unchanged.
Surfaces: UI Construct workbench page, "Adjacent literatures" card, "Propose from question (LLM)" (`POST /api/adjacent/propose`); CLI `wider construct adjacent propose`.

#### Scenario: Reply without literatures
- **WHEN** the model replies `{"literatures": []}` to a proposal started from the page
- **THEN** the route answers HTTP 400 with "adjacent proposal failed"
- **AND** no new `adjacent` version is saved
Pinned by: `tests/unit/test_adjacent.py::test_ui_propose_failure_is_a_message_not_a_500`

#### Scenario: Reply without literatures on the CLI
- **WHEN** the model replies `{"literatures": []}` while `wider construct adjacent propose` runs
- **THEN** the command prints "adjacent proposal failed: invalid structured output: ... should be non-empty" and exits with code 1
- **AND** no traceback is printed and no new `adjacent` version is saved
Pinned by: `tests/unit/test_adjacent.py::test_cli_propose_without_literatures_is_a_message`

#### Scenario: A model error on the CLI
- **WHEN** the backend fails with an error while `wider construct adjacent propose` runs
- **THEN** the command prints the error message and exits with code 1
- **AND** no traceback is printed and no new `adjacent` version is saved
Pinned by: `tests/unit/test_adjacent.py::test_cli_propose_lists_added_and_fails_cleanly`, `tests/unit/test_ui_followup_r12.py::test_adjacent_cli_reports_a_model_error_without_a_traceback`

#### Scenario: A model error on the page
- **WHEN** the backend fails with an error for a proposal started from the page
- **THEN** the route answers HTTP 400 with a message starting "adjacent proposal failed", never HTTP 500
- **AND** no new `adjacent` version is saved
Pinned by: `tests/unit/test_adjacent.py::test_ui_propose_failure_is_a_message_not_a_500`

### Requirement: Validation papers from the query's results are kept but not counted
The system SHALL place a paper whose source is `query_results` in Validation when asked, with a notice that it is kept there but never counted, because a paper the query found cannot test the query. A paper with another source whose DOI or normalised title matches an imported record is added with a warning naming that record. Both checks run on every path into Validation and when resolving fills in a DOI.
Surfaces: CLI `wider benchmark add|set`; UI Setup page, seed-papers step.

#### Scenario: A paper from the query results is kept but not counted
- **WHEN** a DOI is added with `--source query_results --set validation`, then another with `--source query_results` and no set
- **THEN** the first becomes B001 in Validation, the CLI prints "B001 came from the query results: it is kept in Validation but not counted, because a paper the query found cannot test the query." and exits 0, and the seed-papers step shows the same notice beside it; the second goes to Development
- **AND** `wider coverage` lists B001 under "from the query's results: kept, not counted in validation", in neither set, its source stays in `benchmark.csv`, and the validity note in the report and `manifest.json` counts it as not counted
- **AND** `wider rank validate` and `wider status` leave B001 out of the validation set
Pinned by: `tests/unit/test_seed.py::test_query_results_paper_kept_but_not_counted`, `tests/unit/test_dry_run.py::test_ranking_validation_leaves_out_query_results_papers`

#### Scenario: Ranking validation leaves out papers from the query results
- **WHEN** B001 (source `query_results`) and B004 (source `prior_review`) are in Validation and synonyms v2 is frozen
- **THEN** coverage, the dry run, acceptance, the validity note, `wider rank validate`, `wider rank ablate --validation` and the Ranking page each count only B004 as a validation paper
- **AND** each listing (CLI, page and card) shows B001 under "from the query's results: kept, not counted in validation", and `wider status` counts it in neither set
Pinned by: `tests/unit/test_dry_run.py::test_ranking_validation_leaves_out_query_results_papers`

#### Scenario: A matching paper from another source is added with a warning
- **WHEN** a DOI present in the imported records is added with `--source prior_review --set validation`
- **THEN** it is added to Validation, and the CLI prints a warning naming the matching imported record and exits 0
- **AND** the seed-papers step shows the same warning beside the paper
Pinned by: `tests/unit/test_seed.py::test_validation_match_from_another_source_warns`

#### Scenario: Every path into Validation is checked
- **WHEN** a paper with source `query_results` is put into Validation by DOI, by pasted lines, from a CSV file, by moving it there, and by applying a split that assigns it to Validation, and a title-only `researcher` paper in Validation matches an imported record's normalised title
- **THEN** each `query_results` path places the paper in Validation with the not-counted notice, and coverage counts none of them in the validation set
- **AND** the title-only paper stays in Validation with a warning naming the imported record, and resolving that later fills in a matching DOI warns the same way and refuses nothing
Pinned by: `tests/unit/test_seed.py::test_validation_check_on_every_path`

### Requirement: Seed paper source
The system SHALL keep each seed paper's source in a `source` column of `benchmark.csv`: `researcher` (default), `prior_review`, `seed_chain:<id>`, `colleague`, `other_database` or `query_results`; other values are refused. A file without the column loads with `researcher`. The select offers `prior_review` first; a change is journalled.
Surfaces: CLI `wider benchmark add --source S`, `wider benchmark source <id> S`, `wider benchmark list`; UI Setup page, seed-papers step, source select.

#### Scenario: Source recorded and shown
- **WHEN** a paper is added with `--source prior_review`, another without a source, and an old `benchmark.csv` without the column is opened
- **THEN** the first has source `prior_review`, the second `researcher`, the old file's papers load with `researcher`, and `wider benchmark list` and the seed table show the source
- **AND** `wider benchmark source B001 colleague` changes it and is journalled, and `--source friend` is refused
Pinned by: `tests/unit/test_seed.py::test_source_column`

### Requirement: Seed paper keywords
The system SHALL keep each seed paper's keywords in a `keywords` column of `benchmark.csv`, set from the file, seed step or CLI, and SHALL copy an imported record's keywords onto a seed with the same DOI when the seed has none, marking them `keywords_from` `import` and naming those papers in the import's result. Typed keywords are never overwritten.
Surfaces: CLI `wider benchmark keywords <id> "a; b"`, `wider benchmark list`; UI Setup page, seed-papers step, keywords field.

#### Scenario: Keywords typed and copied from an import
- **WHEN** B001's keywords are typed as "a; b" and an export is imported holding B001's and B002's DOIs with keywords
- **THEN** B001 keeps "a; b" with `keywords_from` `researcher`, B002 gets the export's keywords with `keywords_from` `import`, and the import's result names B002
- **AND** the history has one `benchmark_keywords` change for B001 that undo reverses
Pinned by: `tests/unit/test_seed.py::test_seed_keywords_typed_and_from_import`

### Requirement: Hidden Validation papers are not named by an import
While the validation set is hidden, the system SHALL NOT copy an import's keywords onto a Validation paper or name it in the import's result, and the seed list SHALL give the match warning beside a Validation paper only when it enters Validation or once the set is shown, so that no import tells, paper by paper, what the query finds in the hidden set.
Surfaces: CLI `wider import run`, `wider benchmark list`; UI Search results page, import; Setup page, seed-papers step.

#### Scenario: A hidden Validation paper is not named by an import
- **WHEN** B002 is in Validation, the set is hidden, and an export holding B001's and B002's DOIs with keywords is imported
- **THEN** B001 gets the keywords and B002 gets none, the import's result and history name only B001, and neither the seed list nor `wider benchmark list` says the query already finds B002
- **AND** once the set is shown, the seed list gives the match warning beside B002
Pinned by: `tests/unit/test_seed.py::test_hidden_validation_papers_not_named_by_import_or_listing`

### Requirement: Seed-paper chaining yields candidates, not benchmark papers
The system SHALL, under the online-lookup consent, fetch from OpenAlex the references and citing works of the Development seed papers (`seed_chain`) as candidates with source `seed_chain:<benchmark_id>`, and MUST NOT add any to `benchmark.csv` without the researcher. Validation seeds are not chained; the run says so. Candidates the query retrieved are marked.
Surfaces: CLI `wider benchmark chain|candidates`, `wider benchmark add --from-candidate N`; UI Setup, "Candidates from seed papers".

#### Scenario: Chaining with a mock client
- **WHEN** consent is on, the benchmark holds two Development seeds and one Validation seed, and the mock OpenAlex resolves the two DOIs and returns three works, one of them already in the imported records
- **THEN** three candidates are listed with their linking seeds, the imported one marked "retrieved by the query", the run says "1 Validation seed not chained", the Validation seed's DOI is in no request, and `benchmark.csv` is unchanged
- **AND** adding the marked one to Validation writes it with source `seed_chain:B001` and a warning naming the imported record, and adding another writes it with no warning
Pinned by: `tests/unit/test_seed_mining.py::test_seed_chain_candidates`

#### Scenario: Dismissing a candidate
- **WHEN** `wider benchmark candidates --dismiss 2` is run
- **THEN** candidate 2 is kept with status `dismissed`, who dismissed it and when, it is listed last and marked "dismissed", and the same action on the Setup list gives the same row
Pinned by: `tests/unit/test_seed_mining.py::test_seed_chain_candidates`

### Requirement: Chaining candidates are ranked and can snowball
The system SHALL rank candidates by linking seeds, both directions, then year, and SHALL chain again from Development seeds taken from candidates, recording each row's round; candidates in Validation MUST NOT be chained and the run counts them. After a round that added no candidate it SHALL say snowballing usually stops there, and MUST allow another round.
Surfaces: CLI `wider benchmark candidates [--round K]`, `wider benchmark chain --from-candidates`; UI Setup, "Chain again from added papers".

#### Scenario: Ranked candidates and a snowball round
- **WHEN** with consent the mock OpenAlex links candidate C1 to three seeds, C2 to one seed in both directions and C3 to one seed, the researcher adds C1 to Development and C3 to Validation, and `wider benchmark chain --from-candidates` runs
- **THEN** the list ranks C1, C2, C3 in that order, the new works carry round 2 and link to C1's benchmark id, and C3's DOI is in no request while the run says one candidate in Validation was not chained
- **AND** once every round 2 work is dismissed and none added, the list and the command say round 2 added no paper, and a further `--from-candidates` run says "round 3: nothing to chain" in its output
Pinned by: `tests/unit/test_seed_chain.py::test_candidates_ranked_and_snowball`

### Requirement: Candidates from a file or another tool
The system SHALL import candidates from a RIS, CSV or BibTeX export of any tool with a named origin and no request, journal the import, allow dismissing its new rows, and mark candidates the query missed as good validation candidates. An added row SHALL get source `seed_chain:<seed>` if it names a linking seed, else `other_database`.
Surfaces: CLI `wider benchmark candidates --import <file> --origin <label>`, `--dismiss-import N`; UI Setup, "Import candidates", "Dismiss this import".

#### Scenario: Candidates from a file, nothing sent
- **WHEN** a synthetic RIS file with three works, one already a seed paper, is imported with `--origin elicit` while online lookups are off
- **THEN** two candidates are listed with origin `file:elicit` and the "good validation candidate" mark, the seed paper is skipped as already present, the import is journalled, and no request is made
- **AND** importing the same file again adds nothing and says so, adding one candidate to Validation writes source `other_database` with the origin `file:elicit` in its note, and `--dismiss-import 1` dismisses the import's rows still new while Undo puts them back
Pinned by: `tests/unit/test_seed_chain.py::test_candidate_import_from_file`

#### Scenario: A Validation seed links no candidate
- **WHEN** an imported row's `cites` column names only Validation seeds' DOIs and another row names a Development seed's DOI
- **THEN** the first row gets no linking seed, ranks below the second, is not counted for the names block, and added to Development gets source `other_database`
- **AND** a candidate linked to a seed moved to Validation since loses that link in the list and in the added source
Pinned by: `tests/unit/test_seed_chain.py::test_validation_seeds_never_link_a_candidate`

### Requirement: Reference lists of seed papers
The system SHALL fill a Development seed's reference list from OpenAlex first (under consent) and its PDF as fallback, with counts per route and the entries one route lacks; Validation seeds MUST NOT be processed or sent. Optional tiers: GROBID on loopback only, a Crossref match by title only, a model structuring an entry; a field MUST be kept only when the raw entry holds it.
Surfaces: CLI `wider benchmark references [<id>] [--route R] [--pdf P] [--tier T]`; UI Setup, seed step, "References".

#### Scenario: OpenAlex list first, the PDF fills the gap
- **WHEN** with consent the mock OpenAlex lists four references for B001 and B001's synthetic PDF holds five entries, one of them not in OpenAlex
- **THEN** the panel shows "OpenAlex 4 references, PDF 5 entries" and lists the one entry OpenAlex lacks, and that entry's DOI becomes a `references` candidate with origin `pdf`
- **AND** without consent only the PDF route runs and nothing is sent; when the mock OpenAlex fails, the PDF route still runs and the panel names the failure; `wider benchmark references V001` exits 1 with the words of the validation-seed rule, and no Validation DOI is in any request
Pinned by: `tests/unit/test_seed_chain.py::test_reference_lists_openalex_first`

#### Scenario: Parsing tiers keep only what the entry says
- **WHEN** the model tier returns a title found in the raw entry and a DOI that is not in it, and the Crossref tier is asked for an entry without a DOI
- **THEN** the title is kept, the DOI is dropped and counted as dropped, and the Crossref request carried the entry's title and nothing else
Pinned by: `tests/unit/test_seed_chain.py::test_reference_tiers`

#### Scenario: GROBID only on this computer
- **WHEN** `seed_pdfs.grobid_url` is a `http://127.0.0.1` URL, then `https://grobid.example.org`, then a loopback URL where nothing answers
- **THEN** the first posts the PDF to the mock GROBID handler with consolidation off, the second is refused naming the privacy rule with no request made, and the third says "GROBID not reachable" and keeps the plain PDF entries
Pinned by: `tests/unit/test_seed_chain.py::test_grobid_url_must_be_loopback`

### Requirement: Optional model passes run on any backend kind
The system SHALL run the optional model passes (names, reference structuring) on the stage `construct` backend of any kind (local, API, CLI agent with tools off) under its consents, keep only output verbatim in its input, log usage per pass and record the backend kind. Each pass MUST be optional: without a backend the deterministic result is given and says so.
Surfaces: CLI `--model` on `wider query missed-names`, `--tier model` on `wider benchmark references`; UI "Use the model" checkbox.

#### Scenario: Same pass on a local, an API and a CLI backend
- **WHEN** the names pass and the reference pass run with an `ollama`, an `anthropic` and a `cli_agent` backend configured in turn, each answering the same text
- **THEN** all three give the same verified output, the run records name the backend kind, and `wider costs` lists the calls under the passes' stages
- **AND** with no backend configured both commands give the deterministic result and say the model pass did not run
Pinned by: `tests/unit/test_seed_chain.py::test_model_passes_on_any_backend`
