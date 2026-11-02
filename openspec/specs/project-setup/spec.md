# project-setup Specification

## Purpose
Project setup is the first stage of the WIDER procedure (stage key `setup`). It creates the review project folder and fills `project.yaml` and `benchmark.csv` with the researcher's inputs: research questions, inclusion and exclusion notes, seed papers, prior reviews, databases, backends and the review protocol. Every later stage is checked against these inputs. The home page and `wider status` / `wider info` show where each project stands.

## Requirements

### Requirement: Create a project folder
The system SHALL create a review project with `wider init DIRECTORY`, creating the directory if it is missing. The project holds `project.yaml`, `benchmark.csv`, `store.sqlite`, a project `.gitignore` and the folders `inputs/`, `prior_reviews/`, `artifacts/<kind>/`, `embeddings/`, `runs/` and `exports/`. An existing `.gitignore` or `benchmark.csv` is kept.
Surfaces: CLI `wider init DIRECTORY [-n] [-t] [-q ...] [-p ...]`; UI Home page, "Create project" form.

#### Scenario: New project with three platforms
- **WHEN** the researcher runs `wider init myreview -q "Does X affect Y?" -p ebsco -p scopus -p wos`
- **THEN** `project.yaml`, `benchmark.csv`, `.gitignore`, `store.sqlite` and the folders `inputs/ris`, `artifacts/anchors`, `runs`, `exports` and `embeddings` exist
- **AND** `name` is `myreview`, ebsco is the development platform, scopus and wos are validation platforms, and no provider entry holds a key value
Pinned by: `tests/unit/test_project.py::test_init_creates_layout_and_config`

#### Scenario: Defaults of a new project
- **WHEN** a project is created without options
- **THEN** `default_provider` is `ollama` (model `qwen3:8b` at `http://127.0.0.1:11434`), `default_embedding` is `bge-m3` and `seed` is 20260915
- **AND** `created_at` and `wider_version` are recorded, and `benchmark.csv` holds only the header `benchmark_id,title,doi,set,year,first_author,note`

### Requirement: Never overwrite an existing project silently
The system MUST refuse to create a project where `project.yaml` already exists unless `--force` is given. Without `--force`, `wider init` prints the error with the hint "(use --force to overwrite project.yaml)" and exits with code 1.
Surfaces: CLI `wider init --force`; UI Home page "Create project" (409 for a folder that holds a project).

#### Scenario: Second init on the same folder
- **WHEN** `wider init` runs twice on the same directory without `--force`
- **THEN** the second run exits with code 1 and the first `project.yaml` is unchanged
Pinned by: `tests/unit/test_cli.py::test_init_and_status`

#### Scenario: Forced re-init
- **WHEN** the project is created again with `exist_ok` (`--force`)
- **THEN** `project.yaml` is rewritten and no error is raised
Pinned by: `tests/unit/test_project.py::test_init_refuses_existing_without_force`

### Requirement: Find the project from any child folder
The system SHALL locate the project for every command by walking up from `--dir` (default `.`) to the nearest folder that holds `project.yaml`. When none is found it SHALL fail with "no project.yaml in <folder>; run `wider init` first".
Surfaces: CLI every command with `--dir/-d`; no UI twin (`wider ui --dir` picks the starting project).

#### Scenario: Command run in a subfolder
- **WHEN** a command runs with `--dir` pointing at `inputs/ris` inside the project
- **THEN** it loads the enclosing project's `project.yaml`
Pinned by: `tests/unit/test_project.py::test_load_find_and_benchmark_roundtrip`

### Requirement: Setup stage completion rule
The system SHALL mark the stage `setup` as done when the store table `benchmark_papers` holds at least one row and `project.yaml` has at least one research question. The stage has no prerequisites, produces `project.yaml` and `benchmark.csv`, and its command is `wider ui`.
Surfaces: CLI `wider status`, `wider next [--why]`, `wider stages`; UI Setup page status chip.

#### Scenario: Fresh project, then question and seed paper
- **WHEN** a project has just been created
- **THEN** `setup` is `ready`, `construct` is `blocked`, the next stage is `setup` and `wider status` ends with "next: wider ui"
- **AND** after one question and one seed paper are added, `setup` is `done` and the next stage is `construct`
Pinned by: `tests/unit/test_protocol.py::test_stage_registry_and_states`

### Requirement: Setup interview
The system SHALL show the setup page as five steps: question, seed papers, prior reviews, databases and summary. Step completion is computed from the project each time and never stored, and the summary is done when the other four steps are done, with or without an embedding model. Backends are chosen on the model page and the Ranking page, and the protocol on Settings.
Surfaces: UI Question and seed papers page, stepper; CLI per step only (there is no `wider setup` command).

#### Scenario: Completion rules per step
- **WHEN** the setup page is shown
- **THEN** question is done when one question is non-empty, seed when there is a seed paper in the development set, and prior when a prior review was added or "none known" is recorded
- **AND** databases is done when a platform is chosen
Pinned by: `tests/unit/test_setup_routes.py::test_setup_steps_without_models_or_protocol`

#### Scenario: No development seed paper
- **WHEN** every seed paper is in the validation set
- **THEN** the seed step is not done and asks to assign some to the development set, and the summary step is not done

#### Scenario: Summary done without models
- **WHEN** a project has a question, a development seed paper, "none known" for prior reviews and one platform, and its default embedding model is not downloaded
- **THEN** the setup page shows no models or protocol step, and its summary step is done
Pinned by: `tests/unit/test_setup_routes.py::test_setup_steps_without_models_or_protocol`

### Requirement: Edit questions, notes, title and platforms
The system SHALL save title, questions, inclusion and exclusion notes, platforms and prior-review policy from the setup page, and the default backends from the model and Ranking pages, to `project.yaml` and journal them. The first submitted platform becomes development. An unknown default backend or embedding name, or no non-empty question, MUST be refused (HTTP 400) unsaved.
Surfaces: UI setup steps 1, 3 and 4, model and Ranking pages; CLI at creation (`wider init -t -q -p`) and per item.

#### Scenario: Questions and platforms changed on the setup page
- **WHEN** the researcher saves the question "Q?", the platforms wos and ebsco and the title "T"
- **THEN** `project.yaml` holds these values and wos is the first platform
- **AND** saving the default provider "nope" is refused with status 400
Pinned by: `tests/unit/test_ui.py::test_setup_page_and_patch`

#### Scenario: An unknown default backend or embedding is refused unsaved
- **WHEN** one `PATCH /api/project` request saves a new title and question together with the default embedding "nope", or the default provider "nope"
- **THEN** the reply is 400
- **AND** `project.yaml` is unchanged: neither the title nor the question of the same request is kept
Pinned by: `tests/unit/test_ui_followup_r24.py::test_an_unknown_default_embedding_is_refused_unsaved`

#### Scenario: An empty question list is refused
- **WHEN** the setup page saves the questions "  " and ""
- **THEN** the reply is 400 saying a project needs at least one research question, and `project.yaml` keeps its questions
- **AND** `wider init -q "" -q "  "` exits with code 1 with the same words and creates nothing; blank `-q` values beside a real question are dropped
- **AND** the setup page saving "  ", " Q? " and "" answers 200 and keeps the one question "Q?"
Pinned by: `tests/unit/test_ui_followup_r13.py::test_a_project_needs_at_least_one_question`, `tests/unit/test_ui_followup_r13.py::test_init_drops_blank_questions`, `tests/unit/test_ui_followup_r13.py::test_blank_questions_beside_a_real_one_are_dropped_on_the_page`

### Requirement: Seed papers and the development / validation split
The system SHALL keep the seed papers in `benchmark.csv` and the store, with ids `B001`, `B002`, ... in order. Papers are added by DOI, title or CSV/RIS file into `Development` (default) or `Validation`. `split` suggests a seeded random split and changes nothing without `--apply`; a share outside 0 to 1 is refused in plain words.
Surfaces: CLI `wider benchmark add|resolve|split|set|remove|list`; UI Setup page step 2, seed papers card.

#### Scenario: Add, move, split and remove
- **WHEN** two DOIs, one title and a one-row CSV are added to the Validation set
- **THEN** the papers get ids B001 to B004, all in Validation, and `benchmark add` without a paper exits with code 1
- **AND** `split --dev-share 0.5 --apply` leaves two papers in Development, and `split --dev-share 3` exits with code 1
Pinned by: `tests/unit/test_cli_setup.py::test_benchmark_commands`

#### Scenario: Split defaults
- **WHEN** `split` runs without options on two or more papers
- **THEN** it uses the share `protocol.identification.development_share` (default 0.57) and the project seed, and each set gets at least one paper
- **AND** a development share of 0.3 or 0.8 on ten seed papers gives 3 or 8 development papers, on the page and with `wider benchmark split`
Pinned by: `tests/unit/test_seed.py::test_split_defaults_come_from_the_project`, `tests/unit/test_seed.py::test_split_on_the_command_line_takes_the_project_defaults`, `tests/unit/test_ui_followup_r30.py::test_the_split_follows_the_project_s_development_share`, `tests/unit/test_ui_followup_r32.py::test_the_page_s_split_follows_the_project_s_development_share`

#### Scenario: Loose CSV headers
- **WHEN** `benchmark.csv` has the headers `Title,DOI,Set`, no ids, a DOI given as `https://doi.org/10.9999/X` and the set values `dev` and `validation`
- **THEN** the papers are read with ids from B001, the DOI `10.9999/x`, and the sets Development and Validation
Pinned by: `tests/unit/test_project.py::test_benchmark_tolerates_loose_headers`

#### Scenario: A development share the split cannot use
- **WHEN** the page asks for a split with the share -1, or with a share field that is empty or not a number
- **THEN** the reply (or, before any request, the page) says the development share must be a number from 0 to 1, e.g. 0.6
- **AND** no earlier suggestion stays on the page
Pinned by: `tests/unit/test_ui_followup_r13.py::test_a_development_share_out_of_range_is_said_in_plain_words`, `tests/unit/test_ui_followup_r13.py::test_seed_split_reads_the_share_before_sending`

### Requirement: Prior-reviews step records existence
The system SHALL treat the prior-reviews step as answered when at least one prior review has been added or the researcher records that none is known (`none_known: true`). The step also shows the two data-policy switches, both off by default.
Surfaces: CLI `wider prior-reviews policy --none-known/--some-known`, `wider prior-reviews add`; UI Setup page step 3.

#### Scenario: No prior review known
- **WHEN** the researcher records "none known"
- **THEN** the prior step is shown as done with the text "no prior review known"

### Requirement: Databases with exactly one development platform
The system SHALL keep an ordered list of databases in `project.yaml`, with exactly one development platform and the others for validation. A key outside the catalogue needs a syntax family. Unknown keys MUST be refused with exit code 1.
Surfaces: CLI `wider query platforms [add KEY | remove KEY | order KEYS... | role KEY ROLE]`; UI Setup page step 4, Databases card.

#### Scenario: Custom platform and role change
- **WHEN** a custom platform `mine` is added with `--syntax ebsco` and scopus is made the development platform
- **THEN** scopus is the only development platform and `mine` keeps its label and syntax
- **AND** adding `mine` without a syntax, or removing a platform that is not listed, exits with code 1
Pinned by: `tests/unit/test_cli_setup.py::test_query_platforms_commands`, `tests/unit/test_setup_routes.py::test_platform_routes_and_render`

#### Scenario: Catalogue, first platform, removal and order
- **WHEN** platforms are added to an empty list, the development platform is removed, or the list is reordered
- **THEN** the catalogue offers scopus, wos, ebsco, proquest, ovid, pubmed, gscholar and citation_chain, and the first platform added becomes development
- **AND** removing the development platform makes the first remaining one development, and reordering puts the listed keys first and keeps the order of the rest

### Requirement: Review protocol settings
The system SHALL keep every methodological setting in the `protocol` section of `project.yaml`, with the original review's values as defaults. The protocol hash is the sha256 of the protocol as sorted JSON. `set` parses the value as JSON and validates the whole protocol, and an invalid path or value MUST be refused (exit code 1, HTTP 400).
Surfaces: CLI `wider protocol show|explain|set|reset`; UI Settings, Advanced protocol card.

#### Scenario: Change and reset one setting
- **WHEN** `identification.development_share` is set from 0.57 to 0.5
- **THEN** one setting is marked changed and the protocol hash changes
- **AND** setting it to 7 or setting `nope.nope` is refused, and resetting it restores the original hash
Pinned by: `tests/unit/test_setup_routes.py::test_protocol_routes`, `tests/unit/test_protocol.py::test_set_get_hash_and_validation`

#### Scenario: Sections, validation and documentation
- **WHEN** the protocol of a new project is shown or explained
- **THEN** it has the sections identification, imports, dedup, corpus, enrichment, ranking, sampling, screening, recall and labelling, every field has a description, and none is marked changed
- **AND** `wider protocol show` prints the first 12 characters of the hash, and bands that are empty, not contiguous or not starting at rank 1, or a majority above the number of reads, fail validation
Pinned by: `tests/unit/test_protocol.py::test_every_field_is_explained_and_unchanged_by_default`

#### Scenario: The protocol card on Settings
- **WHEN** the researcher sets `identification.development_share` to 0.5 on the Settings Advanced protocol card and then resets it there
- **THEN** the card marks the setting changed and then unchanged, as `wider protocol show` does after each step
Pinned by: `tests/unit/test_setup_routes.py::test_protocol_card_on_settings`

### Requirement: Home page opens, creates and forgets projects
The system SHALL list on the home page the open project first, then recently opened projects, with missing folders listed apart. Opening and creating switch every later request to the chosen project. Forgetting removes a project from the list only, never touches its folder, and is refused for the open project.
Surfaces: UI Home page (project cards, Create, Open, Remove from list); no CLI twin beyond `wider ui --dir`.

#### Scenario: Switch, create and forget
- **WHEN** project A is open and the researcher opens project B by its absolute path
- **THEN** the home list shows B first and marked current, then A
- **AND** forgetting A leaves A's `project.yaml` in place, and a deleted folder is listed as missing
Pinned by: `tests/unit/test_shell.py::test_home_routes_switch_create_forget`

#### Scenario: Refused open and create requests
- **WHEN** the researcher opens a relative path, opens a folder without `project.yaml`, or creates a project in a folder that holds a project, has no parent or is not empty
- **THEN** the answers are 400, 404, 409, 400 and 400
- **AND** a successful create trims the questions, drops empty ones, lowercases the platform keys and opens the first page of the chosen start (Model for query creation for a research question)

#### Scenario: Recent list kept small
- **WHEN** projects are opened
- **THEN** `recent_projects.json` under `$WIDER_HOME` (default `~/.wider`) keeps at most 20 entries, most recent first
Pinned by: `tests/unit/test_shell.py::test_recent_projects_file_and_memory`

### Requirement: Project status and one-screen summary
The system SHALL report a project's state from the store and `project.yaml`. `wider status` shows the configuration, seed and store counts, the stage table and the next command. `wider info` and the home Info panel show the same summary: next step, what needs the researcher, folder, records, seed papers found, backends with uses and locality, tokens, cost, last run and last backend check.
Surfaces: CLI `wider status`, `wider info`, `wider next`; UI Home card and Info panel.

#### Scenario: Fresh project summary
- **WHEN** a project has just been created with the default backends
- **THEN** the summary shows the next step Model for query creation and no stage count, 0 records, the LLM backend "unknown" until a backend check, a local embedding backend, 0 calls at a known cost of 0 and no backend check
- **AND** `wider status` reports "benchmark: 0 papers"
Pinned by: `tests/unit/test_shell.py::test_summary_fresh_project`, `tests/unit/test_cli.py::test_init_and_status`

### Requirement: Resetting an unknown protocol setting is refused
The system SHALL refuse `wider protocol reset PATH` when PATH is not a known setting of the form `section.key`, printing a message that names the unknown setting and points to `wider protocol show`, and exiting with code 1 without a traceback. `project.yaml` MUST stay unchanged. The UI reset answers HTTP 400 in the same case.
Surfaces: CLI `wider protocol reset [PATH]`; UI Settings, Advanced protocol card.

#### Scenario: Unknown setting
- **WHEN** the researcher runs `wider protocol reset nope.nope`
- **THEN** the command exits with code 1 and the message names `nope.nope`
- **AND** `project.yaml` and the protocol hash are unchanged

#### Scenario: Section name instead of a setting
- **WHEN** the researcher runs `wider protocol reset dedup`
- **THEN** the command exits with code 1 and says a setting is given as `section.key`

#### Scenario: Known setting still resets
- **WHEN** `identification.development_share` was set to 0.5 and the researcher runs `wider protocol reset identification.development_share`
- **THEN** the value is 0.57 again and the command exits 0

### Requirement: Databases step names the development platform
The system SHALL summarise the databases step of the setup page as the number of platforms and the key of the platform whose role is development, whatever its position in the search order.
Surfaces: UI Setup page stepper, step 4; CLI `wider query platforms`.

#### Scenario: Development platform not listed first
- **WHEN** the platforms are wos then scopus and scopus was made the development platform
- **THEN** the databases step reads "2 platforms, development on scopus"

#### Scenario: No platform chosen
- **WHEN** no platform is chosen
- **THEN** the databases step reads "no databases chosen" and is not done

### Requirement: Init help lists the catalogue platforms
The system SHALL list, in the help of `wider init --platform`, every platform key of the database catalogue in catalogue order, taken from the catalogue itself so that a key added to the catalogue appears without other changes.
Surfaces: CLI `wider init --help`; UI Home page "Create project" form (free keys).

#### Scenario: Help lists every catalogue key
- **WHEN** the researcher runs `wider init --help`
- **THEN** the platform option lists scopus, wos, ebsco, proquest, ovid, pubmed, gscholar and citation_chain
- **AND** it still says the first platform is the development platform

### Requirement: CLI-agent command shows only supported placeholders
The system SHALL show, in the CLI-agent command field of the backend form on the model page and Settings and in the `--command` help, an example and placeholder list that use only the placeholders the CLI-agent adapter replaces (`{model}`, `{system_file}`, `{system_toml}`, `{schema_file}`, `{system}`, `{user}`), with the user message sent on standard input by default.
Surfaces: UI model page and Settings, backend form; CLI `wider providers add --kind cli_agent --command`.

#### Scenario: Example command in the form
- **WHEN** the researcher picks the CLI-agent kind in the backend form
- **THEN** the command field's label and placeholder name `{model}`, `{system_file}`, `{system_toml}`, `{schema_file}`, `{system}` and `{user}` and do not contain `{prompt}`
Pinned by: `tests/unit/test_setup_routes.py::test_cli_agent_form_names_only_supported_placeholders`

#### Scenario: Example command in the help
- **WHEN** `wider providers add --help` runs
- **THEN** the `--command` help shows an example without `{prompt}`
Pinned by: `tests/unit/test_cli_setup.py::test_providers_add_help_command_example_has_only_supported_placeholders`

### Requirement: A moved platform keeps the keyboard on its row
The system SHALL keep the keyboard focus on a platform's row after its Up or Down button reorders the databases on the setup page: on the same button, or on the row's other button when the row reached the top or the bottom.
Surfaces: UI Setup page step 4, Databases card (Up / Down); CLI `wider query platforms order` has no focus.

#### Scenario: Moving a platform up to the top
- **WHEN** a keyboard user presses Up on the second platform
- **THEN** the table is drawn again with that platform first and the focus on its Down button
Pinned by: `tests/unit/test_ui_followup_r19.py::test_a_moved_platform_keeps_the_keyboard_on_its_row`, `tests/unit/test_ui_followup_r24.py::test_up_on_the_second_platform_puts_it_first`

#### Scenario: Moving a platform down to the bottom
- **WHEN** a keyboard user presses Down on the second-to-last platform
- **THEN** that platform is drawn last and the focus is on its Up button, since its Down button is now disabled
Pinned by: `tests/unit/test_ui_followup_r19.py::test_a_moved_platform_keeps_the_keyboard_on_its_row`, `tests/unit/test_ui_followup_r30.py::test_down_twice_takes_the_first_platform_to_the_bottom_and_keeps_the_keyboard_on_its_row`

### Requirement: Platform buttons name their platform and removal keeps the keyboard in the table
The system SHALL name each platform row's Up, Down and Remove buttons after the platform ("Move Scopus up", "Remove Scopus"), and SHALL keep the keyboard focus in the Databases table after a removal: on the Remove button of the row that took the removed row's place, else of the new last row, and on the catalogue picker once no platform is left.
Surfaces: UI Setup page step 4, Databases card; CLI `wider query platforms remove` has no focus.

#### Scenario: Removing the middle platform
- **WHEN** a keyboard user presses Remove on the second of three platforms
- **THEN** the focus is on the Remove button of the platform that is now second
- **AND** after removing the last row the focus is on the new last row's Remove button, and with no platform left on the catalogue picker
Pinned by: `tests/unit/test_ui_followup_r22.py::test_removing_a_platform_keeps_the_keyboard_in_the_table`

### Requirement: Model page offers found backends and presets
The system SHALL offer, on the model page, "Find AI models on this computer": one card per preset, grouped as CLI, cloud and local. Each card shows whether it was found, whether a key is set or a sign-in file exists, whether its local server runs, a local or remote chip, the destination, the billing note, and "Add", which opens the form filled from the preset.
Surfaces: UI Model for query creation page; CLI `wider providers detect`, `wider providers presets`, `wider providers add --preset ID`.

#### Scenario: Cards on the models step
- **WHEN** the researcher opens the model page and presses "Find AI models on this computer"
- **THEN** the cards appear under the headings CLI, Cloud and Local, each with a local or remote chip, a destination line and a billing note
- **AND** the `gemini-cli` and `antigravity` cards say "checked against the docs, not yet run on this computer", and the found or not-found state of each card matches `wider providers detect`
Pinned by: `tests/unit/test_backends_js_node.py::test_cards_grouped_by_family`, `tests/unit/test_setup_routes.py::test_detect_route_matches_cli`

#### Scenario: Nothing found
- **WHEN** no CLI is installed, no local server answers and no key variable is set
- **THEN** every card says "not found" and still offers "Add"
- **AND** a note says that an AI model can be added by hand or installed first, and nothing is installed
Pinned by: `tests/unit/test_backends_js_node.py::test_nothing_found_note`

#### Scenario: Card of a preset that is already a backend
- **WHEN** the model page of a new project, which holds the default backend `ollama`, shows the `ollama` card
- **THEN** the card offers "Edit ollama" and "Add"
- **AND** "Add" opens the form titled "Add AI model" with the name `ollama-2`, and Cancel saves nothing
Pinned by: `tests/unit/test_backends_js_node.py::test_card_of_existing_backend`

### Requirement: Adding over an existing backend asks first
The system SHALL title the backend form "Add backend" when adding and "Edit <name>" when editing. Saving an Add whose name already exists MUST first show a confirmation that lists the settings the replace would reset and points to Edit; nothing is saved until the researcher confirms.
Surfaces: UI model page and Settings, backend form; CLI `wider providers add` (prints the same warning), `wider providers edit`.

#### Scenario: Add over an existing name
- **WHEN** backend `a` has timeout 900, and the researcher fills the Add form with the name `a` and presses Save
- **THEN** a confirmation lists `timeout_s` as reset and offers Replace, Edit instead and Cancel
- **AND** Cancel saves nothing and leaves timeout 900, and Replace sends `replace: true`
Pinned by: `tests/unit/test_backends_js_node.py::test_reset_fields_text`, `tests/unit/test_setup_routes.py::test_post_existing_provider_needs_replace`

### Requirement: Resolve summary counts the papers looked up
The system SHALL report after resolving seed papers "k resolved, u unresolved of n looked up; m already resolved", counting in k, u and n only the papers looked up in that run, and in m the papers that were already resolved and skipped.
Surfaces: CLI `wider benchmark resolve [--all]`; UI Question and seed papers page, "Resolve" (`POST /api/benchmark/resolve`).

#### Scenario: Two new papers beside three resolved ones
- **WHEN** three seed papers are resolved and two new ones are added, one of which a fake lookup finds
- **THEN** the page and `wider benchmark resolve` both report "1 resolved, 1 unresolved of 2 looked up; 3 already resolved"
- **AND** with `--all` (or `all=true`) the five papers are looked up and m is 0
Pinned by: `tests/unit/test_stage_pages.py::test_resolve_summary_counts_looked_up`
