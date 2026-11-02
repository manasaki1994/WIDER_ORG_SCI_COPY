# query-generation Specification

## Purpose
Query generation is stage `query` of the WIDER procedure: it follows `construct` and must be done before `import`. It renders the synonyms artifact, a platform-neutral query of term blocks combined by a logic tree, as a Boolean search string for each database the researcher chose, checks it with a linter, and archives every string for the methods section. The stage is done when a synonyms version is frozen, which also unlocks the validation set for benchmark coverage.

## Requirements

### Requirement: Platform-neutral query representation
The system SHALL represent the synonyms artifact as a Query IR of blocks (key matching `[a-z][a-z0-9_]*`, name, role `required` or `optional`, terms, fields defaulting to title and abstract) and a logic tree of `and`, `or` and `not` over block keys. Duplicate block keys and references to unknown blocks MUST be rejected.
Surfaces: CLI `wider construct save synonyms <file>`; UI Construct workbench page, synonyms tab.

#### Scenario: Words, phrases and proximity groups
- **WHEN** a block `actor` holds `employee*` and the proximity group `{"near": ["work*", ["team*", "unit*"]], "distance": 5}`, where the inner list is an OR group
- **THEN** the term count per block is reported (for example `voice 3, actor 3`)
- **AND** the words of that proximity term are `work`, `team` and `unit`
Pinned by: `tests/unit/test_construct.py::test_query_ir_helpers`

#### Scenario: Unknown block reference
- **WHEN** a synonyms logic refers to a block key that no block defines
- **THEN** saving the version fails with a validation error
- **AND** no synonyms version is created
Pinned by: `tests/unit/test_construct.py::test_validation_rejects_bad_artifacts`

#### Scenario: Proximity defaults
- **WHEN** a proximity group gives no distance and no order
- **THEN** it is read as distance 5, any order
- **AND** a proximity group with fewer than two items is rejected

### Requirement: Rendering per platform syntax
The system SHALL render the query for ebsco, scopus, wos, proquest, ovid, pubmed and gscholar in each platform's own field tags, truncation and proximity syntax, either multi-line (pretty) or as one line (compact, as pasted).
Surfaces: CLI `wider query render [platform...] [--version] [--compact] [--out]`; UI Query generation page, one card per platform.

#### Scenario: One query, several syntaxes
- **WHEN** a parsed EBSCO string with truncation, phrases, `N5` and `W3` proximity is rendered compactly for every platform
- **THEN** Scopus starts with `(TITLE-ABS(voice*` and has `W/5` and `PRE/3`, WoS has `TI=(`, `AB=(` and `NEAR/5`, and ProQuest has `TI,AB(`, `NEAR/5` and `PRE/3`
- **AND** Ovid has `.ti,ab.`, `voice$` and `ADJ5` and no double quote, and PubMed has `voice*[tiab]`, `"upward communication"[tiab]` and proximity expanded to `(issue[tiab] AND sell*[tiab])`
Pinned by: `tests/unit/test_search.py::test_renderers_per_platform`

#### Scenario: Pretty EBSCO output parses back to the same query
- **WHEN** the query is rendered for EBSCO in pretty form
- **THEN** the string spans several lines in `TI (...) OR AB (...)` groups
- **AND** parsing it again gives a query identical to the original
Pinned by: `tests/unit/test_search.py::test_renderers_per_platform`

#### Scenario: Keywords as a searched field
- **WHEN** a block searches title, abstract and keywords
- **THEN** Scopus uses `TITLE-ABS-KEY(...)` and Web of Science uses `TS=(...)`
- **AND** a block searching only title and abstract uses `TITLE-ABS(...)` and `TI=`/`AB=`

### Requirement: Google Scholar reduction
The system SHALL render a Google Scholar string of at most 256 characters without truncation, proximity or NOT parts (the paste note says so), from the required blocks of the first conjunctive path (else the first three required blocks), in block order, a truncated term without `*`; to fit, it drops the last term of the longest group, then the last groups, and cuts a single term inside its brackets.
Surfaces: CLI `wider query render gscholar`; UI Query generation page, Google Scholar card.

#### Scenario: Long query fits the Scholar limit
- **WHEN** a query with many truncated terms is rendered for `gscholar`
- **THEN** the string is at most 256 characters long
- **AND** it contains no `*` and still holds an OR group such as `(voice OR silence OR ...` (from `voice*`)
- **AND** a block whose first term is truncated starts with that term without `*`, and 9 such blocks with one short word each all fit
Pinned by: `tests/unit/test_search.py::test_renderers_per_platform`, `tests/unit/test_query_roles.py::test_gscholar_keeps_truncated_terms_without_the_star`

#### Scenario: Terms that fit are all kept
- **WHEN** 4 blocks of 5 short terms (under 256 characters together) are rendered, or one block of 80 terms beside a block of 3
- **THEN** every term of the first query is kept; in the second the long block is trimmed, the short block stays whole and the string ends within 12 characters of the limit
- **AND** the paste note says the reduced string keeps as many terms as fit, trimming the longest block first
Pinned by: `tests/unit/test_query_roles.py::test_gscholar_fills_the_string_up_to_the_limit`

#### Scenario: A search string without field codes
- **WHEN** `(voice* OR silence* OR "speak* up" OR whistleblow*) AND (employee* OR worker* OR staff) AND (manag* OR supervis* OR leader*)` is imported (one block per term) and rendered for `gscholar`
- **THEN** each OR is one group holding all its terms: `(voice OR silence OR "speak up" OR whistleblow) (employee OR worker OR staff) (manag OR supervis OR leader)`
Pinned by: `tests/unit/test_query_roles.py::test_gscholar_keeps_every_alternative_of_an_or_of_plain_blocks`

#### Scenario: Too many blocks for one term each
- **WHEN** a query of 9 or 12 required blocks of long terms, or one block with a 60-word term, is rendered
- **THEN** the string is at most 256 characters, with balanced brackets and quotes
- **AND** it starts with the first block's first term; with 9 or more blocks the last block is left out, and one block keeps its term's leading words
- **AND** the paste note says that the last blocks are left out
Pinned by: `tests/unit/test_query_roles.py::test_gscholar_string_never_exceeds_budget`

#### Scenario: A NOT block is left out
- **WHEN** a query `a AND b AND NOT c` (voice, employee, plankton) is rendered for `gscholar`, or an OR of two AND paths whose second holds a NOT
- **THEN** the string holds voice and employee and never plankton
- **AND** the OR of paths renders the first path only: `(voice) (employee)`, and the paste note says NOT blocks are left out
Pinned by: `tests/unit/test_ui_followup_r11.py::test_gscholar_leaves_out_a_not_block`

#### Scenario: No required block on the first path
- **WHEN** the first path holds only a NOT block and an optional block, and two other required blocks exist
- **THEN** the string is built from those two required blocks
- **AND** neither the excluded nor the optional block's terms appear
Pinned by: `tests/unit/test_ui_followup_r12.py::test_gscholar_fallback_leaves_out_not_and_optional_blocks`

#### Scenario: One word longer than the limit
- **WHEN** a block's only term is a single word of 300 characters, bare or in a quoted phrase
- **THEN** the string is at most 256 characters
- **AND** it still opens and closes its bracket, with its quotes paired
Pinned by: `tests/unit/test_ui_followup_r12.py::test_gscholar_cuts_one_long_word_inside_its_brackets`

#### Scenario: No required block at all
- **WHEN** a query has only optional blocks, or only NOT blocks
- **THEN** its Google Scholar string is empty
- **AND** the paste note says that excluded (NOT) blocks are left out too
Pinned by: `tests/unit/test_ui_followup_r30.py::test_google_scholar_with_no_required_block_gives_no_string`

### Requirement: Project platform list and development platform
The system SHALL render strings only for the platforms chosen in `project.yaml`, in search order, with exactly one `development` platform (the first unless set) and the rest `validation`; with none chosen it uses ebsco, scopus, wos, proquest and ovid. A custom platform MUST name a built-in syntax family.
Surfaces: CLI `wider query platforms [add|remove|order|role]`; UI Setup page, platforms step, and the Query generation page cards.

#### Scenario: Custom platform, order and role from the command line
- **WHEN** the researcher adds `wos`, adds a custom platform `mine` with `--syntax ebsco`, orders `wos mine`, and sets `scopus` as development
- **THEN** adding `mine` without `--syntax` exits 1, and the listing marks `mine` as `(custom)`
- **AND** the platforms become wos, mine, ebsco (validation) and scopus (development), and removing a platform twice exits 1
Pinned by: `tests/unit/test_cli_setup.py::test_query_platforms_commands`

#### Scenario: Rendering covers only the chosen platforms
- **WHEN** the chosen platforms are `mine` (Scopus syntax), `scopus` and `ebsco`, and the Query page asks for the strings
- **THEN** `GET /api/query/render` returns strings for exactly those three keys in that order, with `TITLE-ABS` in the custom one
- **AND** the role `primary` or an order naming an unknown key returns HTTP 400
Pinned by: `tests/unit/test_setup_routes.py::test_platform_routes_and_render`, `tests/unit/test_catalogue.py::test_project_platforms_order_role_custom`

#### Scenario: Custom platform without a syntax family
- **WHEN** the renderer style is asked for a custom platform that names no syntax family
- **THEN** an error is raised instead of guessing a syntax
- **AND** nothing is rendered for that platform
Pinned by: `tests/unit/test_catalogue.py::test_style_for_custom_without_syntax_raises`

### Requirement: Query page and render command output
The system SHALL show the newest (or requested) synonyms version as one card per chosen platform with label, Copy button, character count and paste instructions, under a status line `synonyms vN · <status> · terms per block: ...`. The CLI MUST exit 1 for an unknown platform key.
Surfaces: CLI `wider query render`; UI Query generation page, status line and platform cards.

#### Scenario: Copying a string
- **WHEN** the researcher clicks Copy on the Scopus card
- **THEN** the compact single-line Scopus string is put on the clipboard
- **AND** the button reads "Copied" briefly

#### Scenario: Writing strings to files
- **WHEN** the researcher runs `wider query render --compact --out strings/`
- **THEN** one `<platform>.txt` file per chosen platform is written into `strings/`
- **AND** each string is preceded by a header naming the project, the synonyms version and status, and the platform

#### Scenario: Unknown platform on the command line
- **WHEN** the researcher runs `wider query render nowhere`
- **THEN** the command says the platform is unknown and suggests `wider query platforms add nowhere --syntax <family>`
- **AND** exits with status 1

### Requirement: Search-string linter
The system SHALL report errors for a truncation stem of two letters or fewer, an unquoted multi-word term and an empty block, and warnings for a stem under the platform minimum (4 without one), duplicate terms, phrases over 5 words, proximity over 10 or out of the platform range (1-99 without), missing UK/US spellings, unusual characters and unused blocks. The CLI MUST exit 1 on any error.
Surfaces: CLI `wider query lint [--version] [--platform P]`; UI Search strings page, "Linter" panel.

#### Scenario: Problems found in one block
- **WHEN** a block contains `abc*`, the unquoted `poor performance`, `behaviour*` twice, and another block is not in the logic
- **THEN** the linter reports a short truncation stem, an unquoted multi-word term, a duplicate term and a missing spelling variant
- **AND** the unused block is reported, while a stem such as `behavio*` that covers both spellings gives no spelling warning
Pinned by: `tests/unit/test_search.py::test_lint_catches_problems`

#### Scenario: A clean query
- **WHEN** the linter finds nothing
- **THEN** `wider query lint` prints "no issues" and exits 0
- **AND** the UI panel summary reads "Linter: 0 issues"
Pinned by: `tests/unit/test_query_checking.py::test_clean_query_lint`

#### Scenario: The platform sets the limits
- **WHEN** a block holds `wor*` and a proximity of 120 and the linter runs with `--platform ebsco`, `ovid`, `pubmed`, then without a platform
- **THEN** EBSCOhost gives no stem or range warning (minimum three letters, up to 255), Ovid only the range (up to 99, no minimum stem), PubMed only the stem (minimum four; proximity becomes AND, listed in the ledger), and no platform both (4 letters, 1-99)
- **AND** every run keeps the warning "proximity distance 120 is loose"
Pinned by: `tests/unit/test_query_checking.py::test_platform_aware_lint_rules`

### Requirement: Importing an existing EBSCO string
The system SHALL parse a Boolean string in EBSCO syntax (TI/AB field groups, AND/OR/NOT, with `A NOT B` read as `A AND NOT B`, `Nn`/`Wn` proximity, `#` comment lines ignored) into the Query IR and save it as a new draft synonyms version with origin `import`; `--platform` takes another platform's syntax. An unparsable string MUST be refused without saving.
Surfaces: CLI `wider query import [--platform P] <file> [--note]`; UI Search strings page, "Import a string".

#### Scenario: Blocks and logic recovered
- **WHEN** an EBSCO string with a comment line and four concept groups, two of them joined by OR inside an AND, is parsed
- **THEN** the blocks are `voice`, `whistleblow`, `upward_communication` and `antecedent`, with logic `{"and": ["voice", {"or": ["whistleblow", "upward_communication"]}, "antecedent"]}`
- **AND** `N5` becomes an unordered proximity group of distance 5, `W3` an ordered one, and the compact EBSCO rendering parses back to an identical query
Pinned by: `tests/unit/test_search.py::test_parse_recovers_blocks_and_logic`, `tests/unit/test_search.py::test_ebsco_roundtrip_is_structurally_identical`

#### Scenario: Unbalanced string
- **WHEN** the string has an unclosed parenthesis or ends with a dangling `AND`
- **THEN** parsing fails; the CLI prints "could not parse: ..." (the position follows the words) and exits 1, and the UI returns HTTP 400 with the same "could not parse: ..." words
- **AND** no synonyms version is saved
Pinned by: `tests/unit/test_search.py::test_parser_errors`, `tests/unit/test_ui_followup_r17.py::test_an_unbalanced_string_is_refused_on_the_command_line_and_the_page`

#### Scenario: A NOT block parses back
- **WHEN** a query with logic `{"and": ["voice", "employee", {"not": "plankton"}]}` is rendered for EBSCO, compact or pretty, and parsed
- **THEN** the logic comes back identical and the compact rendering re-renders to the same string
- **AND** the bare `(TI (voice) OR AB (voice)) NOT (TI (plankton) OR AB (plankton))` gives `{"and": ["voice", {"not": "plankton"}]}`
Pinned by: `tests/unit/test_ui_followup_r16.py::test_a_not_block_parses_back`

### Requirement: Search-strings archive
The system SHALL archive the rendered string of every chosen platform as `exports/search_strings/<YYYY-MM-DD>_synonyms-v<N>_<platform>.txt` with a header holding its sha256, indexed in `archive.csv` there. A string already archived for the platform MUST NOT be written again, and archiving with no synonyms version MUST fail.
Surfaces: CLI `wider query archive [--version] [--list]`; UI Query generation page, "Search-strings archive" card, "Archive these strings".

#### Scenario: Archive on request, then on freeze
- **WHEN** a draft synonyms version is archived on request and then archived again unchanged
- **THEN** the first call writes one new file whose index row has status `draft`, and the second writes nothing
- **AND** freezing a changed synonyms version adds an index row with reason `synonyms frozen` and status `frozen`
Pinned by: `tests/unit/test_outputs.py::test_archive_on_request_and_on_freeze`

#### Scenario: Index columns and name clashes
- **WHEN** a platform's string changes on the same day for the same synonyms version
- **THEN** the new file name gets an `_<first 8 characters of the sha256>` suffix
- **AND** its `archive.csv` row holds archived_at, platform, label, synonyms_version, synonyms_status, sha256, file and reason

### Requirement: Freezing the query completes the stage
The system SHALL treat stage `query` as done while a frozen query stands (the latest freeze, not reopened), record the freeze (or an imported synonyms version) as its stage result, then include the validation set in coverage. A revised query MUST be a new version, whose save records an unlock while a frozen query stands or the uploads are final with gating on.
Surfaces: CLI `wider construct freeze synonyms <version>`; UI Construct workbench, synonyms tab "Freeze" (Query generation page: status).

#### Scenario: Freezing synonyms
- **WHEN** the researcher freezes synonyms v3
- **THEN** the query stage result says "synonyms v3 is now frozen. The validation seed papers are now reported in coverage."
- **AND** the strings are archived and benchmark coverage reports the Validation set as well as the Development set
Pinned by: `tests/unit/test_dry_run.py::test_freezing_synonyms_reports_validation`

#### Scenario: Freeze after an unlock
- **WHEN** v3 was unlocked into v4 and v4 is frozen
- **THEN** the stage is done again, the stage result names v4, and coverage lists the papers seen before the unlock under "Validation, used"
- **AND** freezing v3 again instead of v4, while the query is still reopened, gives v3 a new freeze number; the stage is done and the list of unlocks gains no entry
- **AND** once v4 is frozen, freezing v3 again gives it a new freeze number and, with papers in the validation set, first asks whether the papers shown count as seen ("v4 is the frozen query. Freezing v3 instead changes the searched query; …"), then records an unlock with cause `freeze` holding the choice (requirement "Freezing another version while a frozen query stands")
Pinned by: `tests/unit/test_dry_run.py::test_freeze_after_unlock`

#### Scenario: A project with a draft after a freeze
- **WHEN** a schema 4 project with synonyms v2 frozen and a draft v3 saved after it is opened by the new code
- **THEN** `wider status` still shows the query stage done and coverage still reports both sets, because v2 is still the frozen query
- **AND** saving v4 with "Count them as seen" then records an unlock naming v2, prints the save notice, and the query stage turns to in progress
Pinned by: `tests/unit/test_store.py::test_migrate_v5_keeps_done_stage_with_draft_after_freeze`

#### Scenario: Saving after a freeze reopens the query
- **WHEN** synonyms v2 is the frozen query, 14 validation papers have been shown, and draft v3 is written with "Count them as seen" by a save, a restore of v1 (the "Restore" button, `POST /api/construct/synonyms/1/restore`), a query-file import or an applied suggested edit, from the CLI (`--count-seen`) and from the Construct workbench
- **THEN** the page (the "Restore" button included) first shows a confirm step with "v2 is frozen. Saving v3 reopens the query; 14 validation papers have been shown." and the choice (requirement "Every unlock asks whether shown papers count as seen"), and "Cancel" writes nothing; the CLI writes v3, prints the notice and "Counted as seen: the 14 validation papers shown so far no longer validate the search." and exits 0
- **AND** the list of unlocks gains one entry with cause `save`, the history lists an `artifact_unlock` change marked not undoable, the package's `unlock.json` lists it, the query stage is in progress, and saving v4 next adds no second entry and no notice
- **AND** a save refused as invalid input (an unparseable query file) writes no version and no unlock entry
- **AND** with no validation paper shown the confirm step and the CLI read "v2 is frozen. Saving v3 reopens the query; no validation paper has been seen yet.", ask nothing and exit 0
Pinned by: `tests/unit/test_dry_run.py::test_save_after_freeze_reopens_the_query`, `tests/unit/test_dry_run.py::test_invalid_save_records_no_unlock`, `tests/unit/test_history_js_node.py::test_restore_confirms_the_save_notice`

#### Scenario: Saving while the uploads are final records what was seen
- **WHEN** no synonyms version is frozen, the uploads are marked final with `validation_after_freeze` on, 3 validation papers have been shown, and draft v2 is saved with "Count them as seen" on the workbench and, in a second project, with `wider construct save synonyms --count-seen`
- **THEN** the confirm step and the CLI read "The uploads are final. Saving v2 reopens them; 3 validation papers have been shown." with the choice, and the CLI (exit 0) adds "Counted as seen: the 3 validation papers shown so far no longer validate the search."
- **AND** the list of unlocks gains one entry with cause `save` whose `seen` lists the papers shown, the mark is cleared and the set hidden again; with the setting off the save records nothing
- **AND** History lists an `artifact_unlock` change marked not undoable, and `unlock.json` lists the entry with cause `save` and the mark's time under `final_before`
Pinned by: `tests/unit/test_dry_run.py::test_save_while_uploads_final_records_what_was_seen`

#### Scenario: Freezing an older version while a newer draft exists
- **WHEN** synonyms v2 and v3 are drafts and v2 is frozen on the synonyms tab and, in a second project, with `wider construct freeze synonyms 2`
- **THEN** the page's confirm step (its text from `freeze_notice` in `GET /api/construct/synonyms`) and the CLI line after the freeze read "v2 is now the frozen query; draft v3 is kept and is not the searched query.", the stage result names v2, the query stage is done and coverage reports both sets
- **AND** the history lists the freeze, the list of unlocks gains no entry (no frozen query stood before), the package's `rounds.json` marks v2 as the frozen query, and saving v4 later records an unlock naming v2
Pinned by: `tests/unit/test_dry_run.py::test_freeze_an_older_version_names_the_frozen_query`, `tests/unit/test_construct_js_node.py::test_freeze_confirm_shows_freeze_notice`

### Requirement: Character count matches the copied string
The system SHALL show on each platform card of the Query generation page the character count of the exact string the Copy button puts on the clipboard (the compact single-line rendering), so the count can be compared with a platform's length limit.
Surfaces: UI Query generation page, platform cards; CLI `wider query render --compact --out DIR` (reports the length of each string it writes).

#### Scenario: Multi-line string on a card
- **WHEN** a platform's string is shown over several indented lines and its compact single-line form has 412 characters
- **THEN** the card shows "412 characters"
- **AND** clicking Copy puts exactly those 412 characters on the clipboard

#### Scenario: Google Scholar within its limit
- **WHEN** the Google Scholar card is shown
- **THEN** its character count is at most 256, the same length as the copied string

### Requirement: Query page names the development platform by its role
The system SHALL name on the Query generation page, and in the Setup checklist and summary, the platform whose role is `development` as set in setup (the first platform only when no role was set), never simply the first platform in search order. The page MUST say that the Validation set of seed papers, not any platform, stays hidden until the query is frozen.
Surfaces: UI Query generation page intro and platform cards, Setup checklist and summary; CLI `wider query platforms` (lists each role).

#### Scenario: Development platform set out of order
- **WHEN** the platforms are `ebsco`, `scopus` and `wos` in that order and `scopus` is set as development
- **THEN** the Query generation page names Scopus as the development platform and marks its card as development
- **AND** the Setup checklist and summary name `scopus`, not `ebsco`, as the development platform

#### Scenario: No role set
- **WHEN** no platform has been given the development role
- **THEN** the first platform in search order is named as the development platform

### Requirement: Google Scholar notes name left-out blocks
The system SHALL add to the Google Scholar paste note the names of the blocks whose terms the reduced string leaves out because they are NOT blocks or hold only proximity terms, and, when the string is empty, say that no Google Scholar string can be built from this query and why.
Surfaces: CLI `wider query render gscholar`; UI Search strings page, Google Scholar card.

#### Scenario: Proximity-only and NOT blocks named
- **WHEN** a synthetic query has required blocks `a` and `b`, a block `c` whose terms are all proximity terms, and a NOT block `d`
- **THEN** the note on the card and the header of `wider query render gscholar` name `c` as left out (proximity only) and `d` as left out (excluded)
Pinned by: `tests/unit/test_stage_pages.py::test_gscholar_notes_name_left_out_blocks`

#### Scenario: No string can be built
- **WHEN** a query has only NOT blocks, or only optional blocks
- **THEN** the card and the command say that no Google Scholar string can be built and name the reason (only excluded blocks, or no required block)
Pinned by: `tests/unit/test_stage_pages.py::test_gscholar_notes_name_left_out_blocks`

### Requirement: Platform list in the add help
The system SHALL list, in the help of `wider query platforms add`, the platform keys of the shipped catalogue, built from the catalogue, so a platform added to the catalogue appears without a change to the help.
Surfaces: CLI `wider query platforms add --help`; UI Question and seed papers page, databases step (the same catalogue).

#### Scenario: Help follows the catalogue
- **WHEN** `wider query platforms add --help` runs
- **THEN** it names every catalogue key that `wider query platforms` lists, and the databases step offers the same keys
Pinned by: `tests/unit/test_stage_pages.py::test_platforms_add_help_lists_catalogue`

### Requirement: Keywords field per block
The system SHALL let a block search `title`, `abstract` or `keywords`, show each block's fields and have the dry run read seed keywords. Field codes come from the platform catalogue and render unchanged; a code not checked against the platform's help page is marked `verified: false` with a render note. Without a field set a block searches title and abstract.
Surfaces: CLI `wider query render --blocks`, `wider construct save synonyms`; UI Search strings page, block list; Construct workbench.

#### Scenario: A keywords block on every platform
- **WHEN** block C searches `keywords` only and the query is rendered for every platform
- **THEN** each string uses today's code (EBSCOhost `KW`, Scopus `TITLE-ABS-KEY`, Web of Science `AK=`, ProQuest `IF`, Ovid the suffix `.kw.`, PubMed `[tiab]`), each with the render note "keyword field code not yet verified against the platform's help page" while its code is `verified: false` (every code when this change lands); Scopus adds "keywords searched more widely: the code also covers title and abstract", PubMed adds "[tiab] includes author keywords", and the page lists "C: keywords"
- **AND** a Scopus block searching title and keywords renders `TITLE-ABS-KEY` with "keywords searched more widely: the code also covers the abstract", and no Web of Science string carries that note (keywords only renders `AK=`, `TS=` only for all three fields)
- **AND** a seed whose keywords hold C's term and whose abstract does not is a `hit` for C in the dry run
Pinned by: `tests/unit/test_dry_run.py::test_keywords_field_rendered_and_evaluated`

#### Scenario: Today's rendered strings do not change
- **WHEN** a block with each field combination (title, abstract, keywords, each pair, all three) is rendered in every style after the field codes move into the platform catalogue
- **THEN** every string equals the one the renderer gave before this change, held in the test as a fixed list, including Web of Science `TS=` when all three fields are chosen
Pinned by: `tests/unit/test_search.py::test_field_codes_unchanged_for_every_style`

### Requirement: Unlocking a frozen query
The system SHALL unlock any frozen synonyms version: it copies it to a new draft noting its source and, unless already reopened, lists an unlock (cause, time, versions, author, papers seen, the seen choice) that clears the uploads-final mark, cannot be undone and reopens stage `query`. The frozen version stays frozen; only synonyms unlock.
Surfaces: CLI `wider construct unlock synonyms <version> [--count-seen|--keep-validation]`; UI Construct workbench, synonyms tab.

#### Scenario: Unlocking a frozen query
- **WHEN** synonyms v3 is the frozen query, two Validation papers have been shown, and the researcher runs `wider construct unlock synonyms 3 --count-seen`
- **THEN** draft v4 is created with the note "unlocked from v3", v3 is still `frozen`, the list of unlocks has one entry with cause `unlock`, the history lists an `artifact_unlock` change marked not undoable, and `wider status` shows the query stage in progress
- **AND** the command prints "Unlocking v3 reopens the query; 2 validation papers have been shown." and "Counted as seen: the 2 validation papers shown so far no longer validate the search." and exits 0, and the replication package's `unlock.json` lists the unlock
Pinned by: `tests/unit/test_dry_run.py::test_unlock_copies_warns_and_records`

#### Scenario: Unlocking again, and unlocking a draft
- **WHEN** v3 was unlocked into v4, `wider construct unlock synonyms 3` runs again, and then `wider construct unlock synonyms 4` runs on draft v4
- **THEN** the second unlock of v3 asks nothing, creates draft v5 with the note "unlocked from v3", adds no unlock entry, prints "the query is already reopened (unlock 1 on <date>); v5 is a copy of v3" and exits 0
- **AND** unlocking draft v4 is refused with "v4 is a draft; edit and save it, no unlock needed" (exit 1, HTTP 400 from the route) and changes nothing
- **AND** History gains no second `artifact_unlock` change, and the package's `unlock.json` still lists one unlock
Pinned by: `tests/unit/test_dry_run.py::test_unlock_again_and_unlock_a_draft`

#### Scenario: Unlocking an older frozen version reopens the frozen query
- **WHEN** v2 and v3 are frozen, v3 most recently, draft v4 exists, 2 validation papers have been shown, and `wider construct unlock synonyms 2 --keep-validation` runs (or "Unlock" is pressed on v2 and "Keep them as validation" chosen)
- **THEN** draft v5 is a copy of v2 with the note "unlocked from v2", and the unlock entry names v2 as its source and v3's freeze as the one it reopened
- **AND** the confirm step and the CLI read "v3 is the frozen query; unlocking v2 reopens it and copies v2 into v5; 2 validation papers have been shown." with the choice, exit 0, and the query stage is in progress
- **AND** History lists an `artifact_unlock` change marked not undoable, and `unlock.json` lists the entry with `from_version` 2 and `seen_choice` `keep`
Pinned by: `tests/unit/test_dry_run.py::test_unlock_older_frozen_reopens_the_frozen_query`

#### Scenario: Unlock from the page, and only synonyms
- **WHEN** the researcher presses "Unlock" on the synonyms tab while v1 is the frozen query and its validation set is empty, and `wider construct unlock cells 1` is run
- **THEN** a dialog shows "Unlocking v1 reopens the query; no validation paper has been seen yet." with the focus on "Cancel"; "Cancel" closes it and changes nothing (no new version, no unlock entry, no history entry); "Unlock" calls the same engine function as the CLI and the page then shows v2 as a draft
- **AND** the cells command is refused with "only synonyms can be unlocked"
Pinned by: `tests/unit/test_dry_run.py::test_unlock_route_and_dialog`

#### Scenario: An unlock reopens final uploads
- **WHEN** the uploads were marked final, synonyms v3 is frozen, and the researcher runs `wider construct unlock synonyms 3 --count-seen`
- **THEN** `wider import list` shows "uploads not marked final", the unlock entry keeps the earlier final time, and `wider import run` takes the new round's export without a notice or a further unlock entry
- **AND** the Validation set stays hidden until a version is frozen again or the uploads are marked final again, and that later mark opens it
Pinned by: `tests/unit/test_dry_run.py::test_unlock_reopens_final_uploads`

#### Scenario: Other stages' checks are unchanged
- **WHEN** synonyms v3 is frozen and unlocked into draft v4, and cells v1 is `approved` with a later cells draft v2
- **THEN** stage `query` is in progress because its check asks for the frozen query (`frozen_query: true` in `wider/stages.yaml`), which the unlock reopened, while stage `construct` stays done because its `approved` checks count any version
Pinned by: `tests/unit/test_dry_run.py::test_stage_check_frozen_query_only_for_query`

### Requirement: Freezing another version while a frozen query stands
While a frozen query stands and its validation set holds papers, freezing another synonyms version SHALL first ask whether the papers shown count as seen, stating both outcomes (CLI: exit 2 without a choice), then record an unlock with cause `freeze` holding the choice, in the same step. With an empty set or a reopened query it asks and records nothing.
Surfaces: CLI `wider construct freeze synonyms <version> [--count-seen|--keep-validation]`; UI Construct, synonyms tab "Freeze".

#### Scenario: Freezing another version asks whether the papers count as seen
- **WHEN** v2 and v3 were drafts, v3 was frozen and is the frozen query, 14 validation papers have been shown, and draft v2 is frozen on the synonyms tab and, in a second project, with `wider construct freeze synonyms 2`
- **THEN** the page's confirm step (its text from `freeze_notice`, `freeze_choice` true for v2) reads "v3 is the frozen query. Freezing v2 instead changes the searched query; 14 validation papers have been shown. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." with the buttons "Count them as seen" and "Keep them as validation", neither pre-selected, with focus on "Cancel"
- **AND** "Cancel" freezes nothing and records nothing; the CLI without a flag prints the same notice and "Choose one: --count-seen or --keep-validation. Nothing was done.", freezes nothing and exits 2
- **AND** with "Count them as seen" or `--count-seen` (exit 0) the page and the CLI show "Counted as seen: the 14 validation papers shown so far no longer validate the search." followed by "v2 is now the frozen query; v3 stays frozen and is not the searched query."; the list of unlocks gains one entry with cause `freeze` naming v3 and v2 and `counts_as_seen` 1, History lists an `artifact_unlock` change "(counted as seen)" marked not undoable, `unlock.json` lists the entry, v2 is the frozen query with a new freeze number, and coverage lists the 14 papers under "Validation, used" with the note "Search not validated: validation set seen on <date> (1 unlock); no paper added since." then "v2 was frozen on <date> in place of the frozen query v3 (14 validation papers shown); the researcher chose to count them as seen."
- **AND** v3, frozen but no longer the frozen query, keeps its "Freeze" button enabled; freezing it again on the page and with the CLI makes v3 the frozen query with a new freeze number and asks nothing and records no entry, because the validation set is now empty
Pinned by: `tests/unit/test_dry_run.py::test_freeze_another_version_asks`, `tests/unit/test_dry_run.py::test_seen_choice_missing_exits_2`, `tests/unit/test_construct_js_node.py::test_freeze_on_a_frozen_version_shows_the_swap_notice`

#### Scenario: Freezing another version and keeping the papers as validation
- **WHEN** v3 is the frozen query, 14 validation papers have been shown, and draft v2 is frozen with "Keep them as validation" on the synonyms tab or with `wider construct freeze synonyms 2 --keep-validation`
- **THEN** the page and the CLI (exit 0) show "Kept as validation: the 14 validation papers shown so far still count; every output states this choice." followed by "v2 is now the frozen query; v3 stays frozen and is not the searched query."
- **AND** the list of unlocks gains one entry with cause `freeze` and `counts_as_seen` 0, History lists it "(kept as validation)" marked not undoable, `unlock.json` lists it, and v2 is the frozen query with a new freeze number
- **AND** coverage still reports the 14 papers as the validation set, and the note in every output adds "v2 was frozen on <date> in place of the frozen query v3 (14 validation papers shown); the researcher chose to keep them as validation."
- **AND** with no frozen query standing, `--count-seen` and `--keep-validation` are ignored and the freeze asks nothing
Pinned by: `tests/unit/test_dry_run.py::test_freeze_another_version_asks`

### Requirement: Platform-aware lint rules
The system SHALL add to the linter of "Search-string linter" warnings for a wildcard after a hyphen and a term covered by a truncation, name a lone wildcard in the short-stem error (one finding), and apply the platform's limits (the strictest without `--platform`).
Surfaces: CLI `wider query lint [--platform P]`; UI Linter panel, platform select.

#### Scenario: Platform rules
- **WHEN** a block holds `wor*`, `worker`, `*` alone and `self-*`
- **THEN** with `--platform pubmed` the linter warns that `wor*` is under PubMed's minimum of four letters, that `worker` is covered by `wor*` and about the wildcard after the hyphen, and reports `*` once, as the short-stem error naming the standalone wildcard
- **AND** `--platform ebsco` gives no minimum-stem warning for `wor*` (EBSCOhost's minimum is three)
Pinned by: `tests/unit/test_query_checking.py::test_platform_aware_lint_rules`

### Requirement: String check before parsing
The system SHALL check an imported or pasted string before parsing it, with each finding's position: an error for unbalanced parentheses; warnings for mixed AND/OR without parentheses (read AND first), curly quotes, a lower-case operator, needless parentheses, NEAR over a grouped AND (Web of Science) and a field tag nested in a field group (PubMed).
Surfaces: CLI `wider query parse --platform P [FILE|-]`, `wider query import --platform P FILE`; UI Search strings page, "Import a string".

#### Scenario: Checking an imported string
- **WHEN** an imported PubMed string has curly quotes and a lower-case `or` and nests `[tiab]` inside a field group
- **THEN** the check warns about the quotes, the lower-case operator (read as OR) and the nested field, each with its position, and the string is parsed
- **AND** `wider query import` of `(voice AND silence OR dissent)` warns that it was read as `(voice AND silence) OR dissent`, saves it and exits 0, while `(a AND (b OR c)` exits 1 naming the missing parenthesis and saves nothing
Pinned by: `tests/unit/test_query_checking.py::test_string_check`

### Requirement: Names from connected papers the query misses
The system SHALL list title and keyword words that no block holds, shared by two or more missed new or Development candidates linked to two or more seeds, in their own block with consequence and quotes; Validation and dismissed candidates MUST NOT count. A model pass MUST keep only verbatim phrases. Only a name the researcher admits changes the query.
Surfaces: CLI `wider query missed-names [--min-links N] [--model]`, `wider seeds names --admit`; UI Search results, Diagnostic report card.

#### Scenario: Names from missed connected papers
- **WHEN** three synthetic candidates each linked to two seeds are missed by the query, two of their titles hold "beta lattice", which no block holds, and a fourth missed candidate added to Validation holds it too
- **THEN** "beta lattice" is listed under the block those candidates fail, quoting the two titles, with "turns 2 connected candidates into hits, +0 development seeds" and "connected, not judged"; the Validation candidate is not quoted or counted
- **AND** the query and `benchmark.csv` are unchanged until the researcher admits the name with a note, which records who admitted it and why
Pinned by: `tests/unit/test_seed_chain.py::test_names_from_missed_candidates`

#### Scenario: A model name not in the text is dropped
- **WHEN** the model pass returns "beta lattice" and "gamma field", and only the first appears in a candidate's text
- **THEN** only "beta lattice" is listed and the run records one dropped phrase
Pinned by: `tests/unit/test_seed_chain.py::test_missed_names_model_pass_is_verbatim`

### Requirement: Every unlock asks whether shown papers count as seen
An unlock, save or freeze reopening the frozen query or final uploads while the validation set holds papers SHALL first ask "Count them as seen" or "Keep them as validation" (CLI exit 2 without a flag) and record the choice on the unlock, final. Keep leaves them in the set; with none it asks nothing.
Surfaces: CLI `--count-seen|--keep-validation` on every command that unlocks or saves synonyms; UI their confirm steps.

#### Scenario: An unlock asks first
- **WHEN** synonyms v3 is the frozen query, the validation set holds V001 and V002, and `wider construct unlock synonyms 3` runs without a flag
- **THEN** it prints "Unlocking v3 reopens the query; 2 validation papers have been shown. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." and "Choose one: --count-seen or --keep-validation. Nothing was done.", exits 2, and writes no v4, no unlock entry and no History change
- **AND** with `--keep-validation` (exit 0) draft v4 is created, the line "Kept as validation: the 2 validation papers shown so far still count; every output states this choice." is printed, the unlock entry has cause `unlock`, `counts_as_seen` 0 and `seen_choice` `keep`, History lists "query unlocked: v3 copied into v4 (kept as validation)" marked not undoable, and after v4 is frozen coverage reports V001 and V002 in the validation set, not under "Validation, used"
- **AND** in a second project with `--count-seen` the line reads "Counted as seen: the 2 validation papers shown so far no longer validate the search.", the entry has `counts_as_seen` 1 and `seen_choice` `count`, and after v4 is frozen V001 and V002 are listed under "Validation, used"
- **AND** passing both flags exits 2 with "Choose one: --count-seen or --keep-validation. Nothing was done." and does nothing
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_unlock_asks_first`

#### Scenario: Unlock from the page asks first
- **WHEN** v3 is the frozen query with 2 validation papers shown and the researcher presses "Unlock" on v3 on the synonyms tab
- **THEN** the dialog shows the same notice (from `unlock_notice` in `GET /api/construct/synonyms`, with `unlock_choice` true for v3) with "Count them as seen", "Keep them as validation" and "Cancel", neither choice pre-selected and the focus on "Cancel"
- **AND** "Cancel" posts nothing; "Keep them as validation" posts `{kind: "synonyms", version: 3, seen_choice: "keep"}` to `POST /api/construct/unlock`, which records the same entry as the CLI and answers with the "Kept as validation: …" line as `notice`
- **AND** `POST /api/construct/unlock` without `seen_choice` answers 400 with the notice and changes nothing
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_unlock_page_asks_first`

#### Scenario: A save after a freeze asks first
- **WHEN** v1 is the frozen query, 2 validation papers have been shown, and a new synonyms version is written by `wider construct save synonyms <file>`, `wider construct restore synonyms 1`, `wider query import <file>`, `wider construct propose synonyms`, `wider query edit apply`, `wider seeds names --admit` or `wider questions answer`, each without a flag
- **THEN** each prints "v1 is frozen. Saving v2 reopens the query; 2 validation papers have been shown." followed by the choice text and "Choose one: --count-seen or --keep-validation. Nothing was done.", exits 2 and writes no version, no unlock entry and no History change; `propose` stops before any model call
- **AND** with `--keep-validation` each writes the version, prints "Kept as validation: the 2 validation papers shown so far still count; every output states this choice.", and the unlock entry has cause `save`, `counts_as_seen` 0 and `seen_choice` `keep`; with `--count-seen` the entry has `counts_as_seen` 1 and `seen_choice` `count`
- **AND** the page's save confirm step (Construct workbench save, "Restore", query import, Propose, applying a suggested edit, admitting a seed name, answering a question) shows the notice (`save_notice`, `save_choice` true) with "Count them as seen", "Keep them as validation" and "Cancel", focus on "Cancel"; "Cancel" writes nothing, and a choice is sent as `seen_choice` to the same engine function
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_save_after_freeze_asks_first`, `tests/unit/test_unlock_seen_choice.py::test_save_page_asks_first`

#### Scenario: A save while the uploads are final asks first
- **WHEN** no synonyms version is frozen, the uploads are marked final with `validation_after_freeze` on, 3 validation papers have been shown, and draft v1 is saved without a flag, then with `--keep-validation`
- **THEN** the first prints "The uploads are final. Saving v1 reopens them; 3 validation papers have been shown." followed by the choice text and the choose line, exits 2 and saves nothing
- **AND** the second saves v1, records an unlock with cause `save`, `counts_as_seen` 0 and `seen_choice` `keep`, clears the mark, hides the set again, and the 3 papers are reported in the validation set once a version is frozen
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_save_while_final_asks_first`

#### Scenario: Nothing shown, nothing asked
- **WHEN** v1 is the frozen query and the validation set holds no paper, and `wider construct unlock synonyms 1` and then a save run without a flag
- **THEN** both exit 0 as before this change, the unlock prints "Unlocking v1 reopens the query; no validation paper has been seen yet.", and the entry has `seen_choice` empty and `counts_as_seen` 1
- **AND** a project created before this change opens with its unlock entries kept, each with `seen_choice` empty
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_nothing_shown_nothing_asked`, `tests/unit/test_store.py::test_migrate_v11_adds_seen_choice`
