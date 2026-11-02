# guided-workflow-and-shell Specification

## Purpose
The guided workflow walks the researcher through the WIDER procedure one stage at a time. Ten stages are available: `setup`, `construct`, `query`, `import`, `dedup`, `enrich`, `rank`, `sample`, `screen` and `recall`. For each stage the system shows what is done, what comes next and why, and what the last run produced, both on the command line and in the local web UI. The shell around the stage pages (sidebar, home page with project cards, stepper, help, settings and the active-run bar) keeps this orientation visible on every page. Gating is advisory: the system shows when a stage is blocked but never stops the researcher from opening it.

## Requirements

### Requirement: Stage registry
The system SHALL read its stages from `wider/stages.yaml`, each with key, title, short label, command, commands, status (`available` or `extension`), description, why, requires, used_by, produces and done_when. A done_when check is `table` (minimum rows), `artifact` (artifact kind in a status), `meta` (meta key exists) or `config` (truthy project field); any other kind is an error.
Surfaces: CLI `wider stages`; UI Help, "Page names and commands".

#### Scenario: Listing the stages
- **WHEN** the researcher runs `wider stages`
- **THEN** a table lists every stage with its key, title, command and status, and the exit code is 0
- **AND** the command column holds the stage's `commands`, "sample, label" for `sample`
Pinned by: `tests/unit/test_cli.py::test_stages`, `tests/unit/test_cli.py::test_stages_lists_each_stage_s_commands`

#### Scenario: Order and commands
- **WHEN** the registry is loaded
- **THEN** the available stages, in order, are `setup` (command `wider ui`), `construct`, `query`, `import`, `dedup`, `enrich`, `rank`, `sample`, `screen` and `recall`, each run by the command of the same name
- **AND** the extension stages are `fulltext`, `extract` and `synthesis` (command `wider synth`)
- **AND** setup's `commands` are `init, benchmark, protocol, providers`, sample's are `sample, label`, and a stage without `commands` gets `[command]`
Pinned by: `tests/unit/test_cli.py::test_stages_lists_each_stage_s_commands`

#### Scenario: The used_by lists
- **WHEN** the registry is loaded
- **THEN** `enrich` lists `rank` and `screen` in `used_by`, every name in a `used_by` list is a known stage key, and an unknown key there is refused with "stage enrich: unknown stage nosuch in used_by"
Pinned by: `tests/unit/test_guided.py::test_used_by_lists`

### Requirement: Stage states
The system SHALL give every stage one state: `extension` for extension stages; else `done` when its non-empty done_when checks all pass, `ready` when every required stage is done, else `blocked` with undone required stages as `missing`. A done stage with open work carries `needs_you`; `rank` carries `needs_review` when the ranking a sample uses (else the current one) has an unkept input hash unlike the current inputs.
Surfaces: CLI `wider status`; UI sidebar, state chips, journey line, Overview.

#### Scenario: Fresh project
- **WHEN** a project has just been created
- **THEN** `setup` is ready, every other available stage is blocked, `construct` lists `setup` as missing, and the recommended step is the Model for query creation page because no construct model is set
- **AND** `fulltext`, `extract` and `synthesis` are in state `extension`
Pinned by: `tests/unit/test_guided.py::test_fresh_project_setup_ready_rest_blocked`

#### Scenario: Setup completed
- **WHEN** the project has a research question and one seed paper synced into `benchmark_papers`
- **THEN** `setup` is done, `construct` is ready and next, and `query` is still blocked
Pinned by: `tests/unit/test_guided.py::test_setup_done_after_question_and_seed_paper`

#### Scenario: Recall done after the report
- **WHEN** the report has been written, so the meta key `last_report` exists
- **THEN** the `recall` stage is done
Pinned by: `tests/unit/test_stage_result.py::test_write_report_sets_last_report_so_recall_is_done`

#### Scenario: Done with pairs waiting
- **WHEN** a synthetic project is deduplicated and the run leaves 3 pairs open
- **THEN** `dedup` is `done` with `needs_you` "3 pairs to decide", and `enrich` is ready
- **AND** `wider status` shows the dedup state as "done, needs you: 3 pairs to decide"
- **AND** once every pair is decided, `needs_you` is empty
Pinned by: `tests/unit/test_guided.py::test_dedup_done_but_needs_you_while_pairs_wait`

#### Scenario: Ranking made before new abstracts
- **WHEN** a synthetic project's samples were drawn from a ranking and then 3 records got an abstract from Missing abstracts
- **THEN** `rank` stays `done` with `needs_review` "the records or their abstracts changed since this ranking", and `wider status` shows "done, needs review: the records or their abstracts changed since this ranking"
- **AND** nothing is rerun and no other stage changes state; `wider status` prints the same line
Pinned by: `tests/unit/test_guided.py::test_rank_needs_review_after_new_abstracts`

#### Scenario: Ranked from outdated embeddings
- **WHEN** abstracts change and the ranking is redone from the stored embeddings without re-embedding
- **THEN** the ranking still needs review, because its input hash comes from the texts its embeddings were made from (an identical hash reuses the existing version)
Pinned by: `tests/unit/test_guided.py::test_stale_embeddings_keep_review`

#### Scenario: Not marked without a real change
- **WHEN** after ranking only the embeddings are computed again from the same texts and model, a concept draft is saved, an abstract lookup fails, or Deduplication runs again and leaves the same records and texts
- **THEN** `needs_review` stays empty; and a ranking made before input hashes were stored is never marked
- **AND** approving new anchors, choosing another embedding model, or changing `corpus.thin_words` or `corpus.max_words` marks it
Pinned by: `tests/unit/test_guided.py::test_rank_not_marked_without_a_change`

#### Scenario: Two samples on two ranking versions
- **WHEN** the development sample was drawn from ranking version 1 and the validation sample from version 2, and the records then change
- **THEN** both versions need review, each named with its samples, and keeping one leaves the other marked
Pinned by: `tests/unit/test_guided.py::test_two_samples_two_versions`

### Requirement: Advisory gating
The system MUST NOT disable or hide a stage page because the stage is blocked. A blocked stage page shows the state chip "not started: needs <titles of the missing stages> first", and every command still runs.
Surfaces: UI every stage page (title row state chip) and sidebar; CLI every stage command (not gated).

#### Scenario: Opening a blocked stage
- **WHEN** the researcher opens the Rank page before deduplication is done
- **THEN** the page opens with its controls enabled and its state chip reads "not started: needs" followed by the missing stage titles and "first"

### Requirement: Next step on the command line
The system SHALL print with `wider next` the command of the first stage with open work, except: no construct model and nothing from Search results on done gives `wider providers`; an incomplete development sample before Screening gives Samples and labelling; `query` is passed over while records are imported and no search is locked. With none ready: "all stages done"; `--why` adds why now. `wider status` ends with the same "next:".
Surfaces: CLI `wider next [--why]`, `wider status`; UI sidebar.

#### Scenario: Next after setup
- **WHEN** a project was just created with `wider init`
- **THEN** `wider next` prints "wider providers"
- **AND** after a construct model is set it prints "wider ui", and after a seed paper is added `wider next --why` prints "wider construct" and `wider status` shows "next:" with `construct`
Pinned by: `tests/unit/test_guided.py::test_next_and_status_commands`

#### Scenario: No construct model, and open development labels
- **WHEN** a synthetic project was just created with no construct model, and another has every stage before Screening done with 79 development labels open
- **THEN** `wider next` prints `wider providers` for the first and `wider sample status` for the second, and each Overview's Continue names the same page
Pinned by: `tests/unit/test_guided.py::test_next_model_and_open_dev_labels`

#### Scenario: No construct model in a project past query creation
- **WHEN** a synthetic project has Ranking done and no construct model set
- **THEN** `wider next` does not print `wider providers`, the Overview's Continue names the same step as `wider next`, and every stage keeps its own state
Pinned by: `tests/unit/test_guided.py::test_next_skips_model_once_past_query_creation`

#### Scenario: Records imported before a search is locked
- **WHEN** a synthetic project has its question, a seed paper and approved concepts, records imported and no locked search
- **THEN** `wider next` names Deduplication, not Search strings, Search strings stays not completed, and the Overview's Continue names the same step
Pinned by: `tests/unit/test_guided.py::test_next_passes_over_query_with_records`

### Requirement: Stage results
The system SHALL record a stage result after every engine action, with stage, action, time, ok flag, headline, metrics, rows, warnings, outputs, usage and `next_stage` (the next non-extension stage; none after `recall`), in the meta key `last_result:<stage>` and the table `stage_results`. Tone: `danger` if not ok, `warning` with warnings, else `success`.
Surfaces: CLI every stage command and `wider status`; UI result card on each stage page (`GET /api/stages`).

#### Scenario: Round trip of a result
- **WHEN** a `dedup` result with metric a = 1 and one warning is recorded
- **THEN** its next stage is `enrich`, its tone is `warning`, and the last `dedup` result returns the same headline and metrics
- **AND** a second result replaces it as the last result
Pinned by: `tests/unit/test_stage_result.py::test_record_and_last_result_roundtrip`

#### Scenario: Import from the command line
- **WHEN** `wider import` imports one file of 12 records
- **THEN** the output contains "Imported 1 file with 12 records" and "next: wider dedup"
- **AND** `wider status` shows "last result" with that headline
Pinned by: `tests/unit/test_stage_result.py::test_cli_status_shows_last_result`

#### Scenario: Results through the API
- **WHEN** the UI runs import and then deduplication
- **THEN** each response carries a `stage_result` for its stage, and `GET /api/stages` lists `import` and `dedup` under `last_results`
Pinned by: `tests/unit/test_stage_result.py::test_engines_record_and_api_wraps`

### Requirement: Result card, Continue button and earlier results
The system SHALL show a step's result from this visit as a card (tone, headline, action, time, usage; tiles, warnings, details and files under "Details"), ending with the page's one Continue link. An earlier visit's result is one line, "Last run <when>. Show the result"; the next stage's page shows only this visit's strip. Deduplication shows the chart's message; Screening "Where screening stands".
Surfaces: UI stage pages; CLI `wider status`.

#### Scenario: Continue after a run
- **WHEN** deduplication finishes on the Deduplication page
- **THEN** the message beside the flow chart is the one written from the current counts, and the page ends with "Continue to Missing abstracts"; the page does not change until it is clicked
- **AND** "Details" opens the run's metric tiles, warnings, details table and files written
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_main_button_runs_and_shows_errors_beside_the_chart`, `tests/unit/test_shell_js_node.py::test_pages_redraw_after_actions`

#### Scenario: Earlier results of a stage
- **WHEN** a stage has 2 or more recorded results
- **THEN** a selector offers "Latest result" and up to 30 earlier results, and an earlier result shows a "Back to the latest result" button
- **AND** with fewer than 2 results the selector is hidden
- **AND** the Deduplication page has no selector: each run is a notebook entry whose "Show this result" opens that run's card in the entry
- **AND** the Deduplication page has no line with the nearest earlier stage's result
Pinned by: `tests/unit/test_dedup_page_node.py::test_no_run_selector_and_no_earlier_stage_line_on_dedup`

#### Scenario: A result from an earlier visit
- **WHEN** a page opens on a stage that has a recorded result from an earlier visit and nothing has run in this visit
- **THEN** the page shows one line "Last run <when>. Show the result" instead of the card, and the next stage's page shows no strip for it
- **AND** after the step runs in this visit its card shows with the metric tiles under "Details", and the next stage's page shows its strip
Pinned by: `tests/unit/test_plain_words_ui.py::test_a_result_card_shows_only_for_this_visits_action`

#### Scenario: Screening shows its summary instead of the result card
- **WHEN** a screening round has finished and the Screening page is opened
- **THEN** the page shows the card "Where screening stands" and no stage-result card or result selector of its own
Pinned by: `tests/unit/test_screen_page_node.py::test_screen_page_has_no_stage_result_card`

### Requirement: Sidebar and stepper
The system SHALL show a sidebar with the project box and the `stages.yaml` sections, numbered, marked completed, current or upcoming; per stage a link with its task's icon and no status mark, the status in words ("completed", "needs attention", "needs review", "in progress", "not started", the next stage "not started, work here next"), tooltip "Title: status", "(needs ... first)" if blocked; `aria-current="page"` on the current page.
Surfaces: UI shell on every page; CLI `wider status`.

#### Scenario: Shell elements present
- **WHEN** the UI page is loaded
- **THEN** it contains the sidebar, its toggle, the home, model, notes, help and settings pages, the top bar and a skip link
- **AND** each of the ten stage pages has a state chip and an icon, under the section headings "1 Literature search", "2 Data cleaning", "3 Model tuning and validation" and "4 Screening and outputs", with the model page first in section 1
Pinned by: `tests/unit/test_shell.py::test_static_shell`, `tests/unit/test_shell_js_node.py::test_sidebar_sections`

#### Scenario: Only the next stage is marked
- **WHEN** two stages are ready, one stage is done although a stage it requires is not, and one is blocked
- **THEN** only the recommended stage (as `wider next` names it) is marked "work here next" ("not started, work here next", or for a done stage "completed" plus "needs attention: ... work here next"), and the other reads "not started"
- **AND** no link holds a status mark, the done stage reads "completed" and no "(needs ...)", only the blocked stage's tooltip adds "(needs <titles> first)", and every tooltip starts "n. Title: "
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_state_words`

#### Scenario: Icon rail
- **WHEN** the window is narrower than 900 px and the researcher has not chosen otherwise
- **THEN** the sidebar is collapsed to an icon rail
- **AND** a collapse or expand by the researcher is remembered in the browser under `wider.sidebar`
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_icon_rail_below_900_px_and_the_choice_is_remembered`

#### Scenario: Section marks
- **WHEN** every stage of section 1 is done and the current page is Deduplication
- **THEN** section 1 has the word "completed" for screen readers and no mark, section 2's number is marked current, and sections 3 and 4 are marked upcoming
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_section_marks`

#### Scenario: Done but pairs wait
- **WHEN** `dedup` is done with `needs_you` "3 pairs to decide"
- **THEN** its sidebar link keeps its task icon with no mark, its words read "needs attention: 3 pairs to decide"
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_needs_you_mark`

#### Scenario: A running stage
- **WHEN** a screening job is running
- **THEN** the Screening link and the Screening page chip read "in progress"
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_in_progress_while_a_job_runs`

#### Scenario: Ranking needs review
- **WHEN** `rank` is done with `needs_review`
- **THEN** its link keeps its task icon with no mark, and its words read "needs review: the records or their abstracts changed since this ranking"
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_needs_review_mark`

#### Scenario: The recommended stage with records imported first
- **WHEN** records are imported, no search is locked, and question, seed papers and concepts are done
- **THEN** the sidebar marks Deduplication "not started, work here next" and Search strings "not started", as `wider next` and the Overview's Continue name Deduplication
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_recommended_stage`

### Requirement: Explain this stage
The system SHALL give every stage page an "Explain this step" button that reveals what happens, why now, the command line and what the stage produces. The explanation is hidden until asked for, and the button then reads "Hide the explanation" with `aria-expanded` set to true. The button is a quiet button, never the page's main button.
Surfaces: UI every stage page; CLI `wider next --why` (what the stage does and why now).

#### Scenario: Explanation hidden by default
- **WHEN** a stage page is loaded
- **THEN** each of the ten stage pages has an "Explain this step" button and its explanation is hidden
- **AND** no page, including Help, still says "Explain this stage"
Pinned by: `tests/unit/test_shell.py::test_static_shell`, `tests/unit/test_shell.py::test_no_page_says_explain_this_stage`

### Requirement: Home page and project summary
The system SHALL show a home page with a card per known project: title, folder, records, the next step, "N need you" (needs-you and needs-review items) or "Nothing waits for you", a running job, and Open, Info and Remove from list; no stage count or bar. Info shows folder, records, seed papers found, backends with uses and locality, tokens, cost, last run and backend check. No backend banner, next-stage banner or notes search.
Surfaces: CLI `wider info`; UI Home page (cards, Info).

#### Scenario: Summary of a fresh project
- **WHEN** a project has just been created
- **THEN** the summary reports the next step Model for query creation, 0 records, the default Ollama LLM as "unknown" until a backend check, a local embedding backend, 0 calls and no last run
- **AND** building the summary makes no request
Pinned by: `tests/unit/test_shell.py::test_summary_fresh_project`

#### Scenario: Info on the command line
- **WHEN** `wider info` runs on a project that has not run a backend check
- **THEN** the output shows the next stage, the backend name and "not run yet"
Pinned by: `tests/unit/test_shell.py::test_info_and_check_backends_cli`

#### Scenario: Progress line on the card
- **WHEN** a project has 7 of its 10 stages done, Missing abstracts is next and Deduplication has 3 pairs to decide
- **THEN** its card reads "1 needs you" and "Next: Missing abstracts" (plain text, not a link), and nowhere "7 of 10 stages done", a segmented bar or "Stage 2 of 10"
- **AND** a project whose duplicate check is running shows the running job and what needs the researcher, both
- **AND** the home page shows no backend banner and no Notes and tags search, and on the open project's card Remove from list is disabled and says the open project cannot be removed from the list
Pinned by: `tests/unit/test_shell_js_node.py::test_home_card_next_step`

#### Scenario: Info panel
- **WHEN** the researcher presses Info on a project card
- **THEN** the panel shows the folder, records, seed papers found, each backend with its uses and locality, tokens, cost, last run and last backend check, as `wider info` prints them
Pinned by: `tests/unit/test_shell_js_node.py::test_home_info_panel`

### Requirement: Recent projects, open, create and forget
The system SHALL remember up to 20 recent projects in `recent_projects.json` under `$WIDER_HOME` (default `~/.wider`), storing only paths and opening times (in memory when there is no file). Projects are opened, created and forgotten from the home page; forgetting never touches the folder, and folders that no longer exist are hidden and listed as missing.
Surfaces: UI Home page (Create and Open forms, switch and remove buttons); CLI `wider init` creates a project (no recent list).

#### Scenario: Recent list stores paths only
- **WHEN** projects a, b and a again are opened
- **THEN** the list holds a then b, and each entry has only `path` and `opened_at`
Pinned by: `tests/unit/test_shell.py::test_recent_projects_file_and_memory`

#### Scenario: Switch, create and forget
- **WHEN** the researcher opens another project, creates a new one and forgets the first
- **THEN** every route reads the newly opened folder, the new project's `project.yaml` exists, and the forgotten project's folder is untouched
- **AND** opening a relative path returns 400, a folder without `project.yaml` 404, creating over a project 409, creating in a missing parent or a non-empty folder 400, and forgetting the open project 400
Pinned by: `tests/unit/test_shell.py::test_home_routes_switch_create_forget`

### Requirement: Active-run bar
The system SHALL show on every page a bar for each running background job with its label, progress "x of n", time left, backend locality, tokens and cost, a Cancel button that asks for confirmation, and the note that the run continues when the researcher leaves the page. While jobs run, the tab title starts with "(N running)".
Surfaces: UI job bar on every page (`GET /api/jobs?active=true`); CLI not applicable (CLI runs in the foreground).

#### Scenario: Job bar wiring
- **WHEN** the UI page is loaded
- **THEN** it contains the job bar and the notice area, and the script polls active jobs and offers cancel and browser notifications
Pinned by: `tests/unit/test_runs.py::test_static_job_bar`

#### Scenario: Finished job notice
- **WHEN** a background job finishes
- **THEN** a notice appears; a failure notice stays until closed and other notices close after 20 seconds

### Requirement: Local UI server and settings
The system SHALL serve the web UI only on 127.0.0.1 (default port 8765) and open the browser unless told not to. Settings lists the backends (Edit with prices, Remove, stage selects, consents (Revoke), "Check backends"), the prior-review policy, the protocol (changed fields, hash), the doubt settings and recent projects.
Surfaces: CLI `wider ui`, `wider check-backends`, `wider providers|embed edit|remove|consent|revoke`, `wider providers stage`, `wider protocol show|set|reset`; UI Settings.

#### Scenario: Starting the UI
- **WHEN** the researcher runs `wider ui --no-open`
- **THEN** the server listens on 127.0.0.1:8765 and no browser window is opened

#### Scenario: First backend check
- **WHEN** the backends were never checked and the researcher presses "Check backends" on Settings
- **THEN** fixed test sentences go to each checked backend and no project data, and the note beside the button says the check may cost a little on a paid backend
- **AND** each result names its uses, such as "LLM b (screen)", as `wider check-backends` does
Pinned by: `tests/unit/test_backends_js_node.py::test_check_banner_text`, `tests/unit/test_shell_js_node.py::test_backend_check_shows_uses`, `tests/unit/test_shell_js_node.py::test_settings_check_result_shows_uses`

#### Scenario: Finding backends from Settings
- **WHEN** the researcher presses "Add or find AI models" on Settings
- **THEN** the model page opens, where "Find AI models on this computer" shows the grouped preset cards, and "Add" on a card opens the backend form filled from that preset; Cancel saves nothing
- **AND** Settings itself shows no preset cards and the home page no backend banner
Pinned by: `tests/unit/test_backends_js_node.py::test_cards_grouped_by_family`, `tests/unit/test_backends_js_node.py::test_form_values_from_preset`, `tests/unit/test_backends_js_node.py::test_settings_lists_backends_with_edit_and_revoke`

#### Scenario: Managing a backend on Settings
- **WHEN** the researcher edits backend `a` on Settings with an input price of 0.5, sets the `screen` select to `a`, and removes backend `b`
- **THEN** `project.yaml` holds the price for `a`, `stage_providers.screen: a` and no `b`, as after `wider providers edit a --price-in 0.5`, `wider providers stage screen a` and `wider providers remove b`
- **AND** no text on Settings or in the cost card names a "setup step"
Pinned by: `tests/unit/test_shell_js_node.py::test_settings_manage_backends`, `tests/unit/test_setup_routes.py::test_settings_backend_actions_match_cli`

#### Scenario: Revoking backend consent from Settings
- **WHEN** cloud backend `a` has `models` and `research` consent and the researcher presses "Revoke" on its Settings card
- **THEN** the card shows no consented operation, and `wider providers consent a --json` lists none
- **AND** "Revoke" on a cloud embedding card does the same for that embedding backend, matching `wider embed consent e --json`, and leaves backend `a` unchanged
Pinned by: `tests/unit/test_backends_js_node.py::test_consent_revoke_card`, `tests/unit/test_backends_js_node.py::test_settings_lists_backends_with_edit_and_revoke`, `tests/unit/test_setup_routes.py::test_consent_route_matches_cli`

### Requirement: Unknown page address opens Home
The system SHALL open the Home page whenever the page address names no known page, such as an unknown, misspelt or stale `#` hash from an earlier version or bookmark, and the address SHALL then read `#home`, with "page not found: <address>" said once in Home's status line. It SHALL never leave the window blank or keep showing the previous page.
Surfaces: UI shell (address hash, sidebar, browser back and forward); CLI none.

#### Scenario: Opening an unknown address
- **WHEN** the UI is opened at `#no-such-page`
- **THEN** the Home page is shown, marked as the current page in the sidebar, and the address reads `#home`
- **AND** Home's status line says "page not found: no-such-page", once
Pinned by: `tests/unit/test_a11y_node.py::test_unknown_address_says_page_not_found`

#### Scenario: Unknown address while working
- **WHEN** the Rank page is open and the address is changed to `#no-such-page`
- **THEN** the Home page is shown instead of the Rank page

### Requirement: Failed page load shows a banner
The system SHALL show a visible banner with the error message when the stage list or a page's data cannot be loaded, instead of a blank or half-filled page. The sidebar and the other pages stay usable, and the banner disappears once a later load of that page succeeds.
Surfaces: UI shell and every stage page; CLI none (the CLI prints errors directly).

#### Scenario: Stage list cannot be loaded
- **WHEN** the UI starts and the request for the stage list fails
- **THEN** a banner shows the error message and the Home page and sidebar can still be used

#### Scenario: A page's data cannot be loaded
- **WHEN** the Recall page is opened and the request for its data fails
- **THEN** the page shows a banner with the error message
- **AND** after the cause is fixed and the page is opened again, the banner is gone

### Requirement: Help lists the real keys
The system SHALL list on the Help page the keys the UI actually handles: `j`/`k` next and previous record, `1`/`2`/`3` the decisions, `n` note, `t` tag and `Esc` close in the record drawer (back to the list where it is docked), Ctrl/Cmd+Z to undo the reviewer's last change and Ctrl/Cmd+Shift+Z redo, Left/Right on tab lists, `Esc` closing the Menu, and Tab, Shift+Tab and the skip link. It SHALL NOT list a key the UI does not handle.
Surfaces: UI Help page, Keyboard card; CLI none.

#### Scenario: Keyboard card
- **WHEN** the Help page is opened
- **THEN** its Keyboard card lists `j`/`k`, `1`/`2`/`3`, `n`, `t`, `Esc` and Ctrl/Cmd+Z with what each does, `Esc` with both "close" and "back to the list on the Deduplication page"
- **AND** it does not list `p` or "n / p" as next and previous
- **AND** it lists Ctrl/Cmd+Shift+Z as redo, Left/Right as moving between tabs, and `Esc` as closing the Menu at phone width
Pinned by: `tests/unit/test_shell.py::test_static_shell`, `tests/unit/test_shell_js_node.py::test_help_lists_redo_tab_and_menu_keys`

### Requirement: Store and file values are escaped
The system SHALL escape every value that comes from the store, `project.yaml`, a run folder or an imported or uploaded file (titles, authors, names of protocols, samples and reviewers, notes, tags, messages) before inserting it into a page, so that it shows as text and never adds elements, attributes or scripts.
Surfaces: UI every page, the record drawer, select options and result cards; CLI not applicable (terminal output is escaped for markup).

#### Scenario: Markup in names and titles
- **WHEN** a protocol is named `<img src=x onerror=alert(1)>` and a record title contains `"><b>bold</b>`, and the Recall and Sampling pages are opened
- **THEN** the select options, tables and drawer show both values as literal text
- **AND** no `img` or `b` element is created from them and no script runs

### Requirement: Empty selects show a placeholder
The system SHALL show a placeholder option (for example "no samples yet") in a select that has no options, keep a button that needs that value disabled while the placeholder is shown, and SHALL NOT send a request with an empty parameter: an optional parameter left at its placeholder is left out of the request.
Surfaces: UI every page with selects (protocol, sample, run, ranking, backend, prompt and version selects); CLI not applicable.

#### Scenario: No probability sample yet
- **WHEN** the Recall page is opened on a project without a probability sample
- **THEN** the sample select shows a placeholder and "Estimate recall" is disabled
- **AND** no recall request with an empty sample is sent

#### Scenario: Options appear later
- **WHEN** a probability sample is drawn and the Recall page is opened again on a project with screening decisions
- **THEN** the select lists the sample and "Estimate recall" is enabled

### Requirement: Skip link keeps the current page
The system SHALL move the keyboard focus to the page content when the "Skip to content" link is used, and SHALL keep the current page and its address. It MUST NOT treat the link's target as a page address.
Surfaces: UI every page, "Skip to content" link (first Tab stop); no CLI twin (a keyboard aid of the page only).

#### Scenario: Skip from a stage page
- **WHEN** the researcher is on the Enrich page and presses Tab, then Enter on "Skip to content"
- **THEN** the focus is on the main content and the Enrich page stays open with the address `#enrich`
Pinned by: `tests/unit/test_static_followup.py::test_skip_link_keeps_the_page`, `tests/unit/test_ui_followup_r30.py::test_the_skip_link_moves_the_keyboard_to_the_content_and_keeps_the_page`

#### Scenario: Skip from Home
- **WHEN** the researcher is on Home (address `#home`) and uses "Skip to content"
- **THEN** the focus is on the main content, Home stays open and the address gains no `#main`
Pinned by: `tests/unit/test_ui_followup_r13.py::test_skip_link_on_home_keeps_home`

### Requirement: Page messages are announced once
The system SHALL announce each result message of a page (`*-msg`) to screen readers as a status, once: every message area has `role="status"`, and each toast is its own status or alert inside a toast box that is not a second live region.
Surfaces: UI every page's result messages and the toasts; no CLI twin (the CLI prints its messages).

#### Scenario: A result message and a toast
- **WHEN** a page shows a result message and a toast
- **THEN** every `*-msg` area in the page has `role="status"`, also the Setup page's areas drawn by its script, and the `#toasts` box has no `aria-live` of its own, so each is read once
- **AND** each toast is its own `role="alert"` (an error) or `role="status"` (anything else)
Pinned by: `tests/unit/test_ui_followup_r16.py::test_messages_are_announced_once_and_named_links_survive_collapse`, `tests/unit/test_ui_followup_r30.py::test_every_message_area_is_read_out_when_it_changes`, `tests/unit/test_ui_followup_r32.py::test_every_message_area_is_a_status_area`

#### Scenario: A refused undo is an error
- **WHEN** an undo is refused (nothing to undo by this reviewer, no name given, or a 409 from the server) or an artifact restore fails
- **THEN** its toast is `role="alert"` and stays until dismissed
Pinned by: `tests/unit/test_ui_followup_r22.py::test_history_says_why_a_change_cannot_be_undone_and_refusals_are_alerts`, `tests/unit/test_ui_followup_r23.py::test_a_danger_toast_is_an_alert_that_stays`, `tests/unit/test_ui_followup_r30.py::test_a_refused_undo_and_a_refused_restore_are_danger_toasts`

### Requirement: Collapsed sidebar links keep their names
The system SHALL keep each sidebar link's name for screen readers when the sidebar is collapsed to the icon rail: the label is hidden visually only, never with `display:none`.
Surfaces: UI sidebar, collapse toggle; no CLI twin (a page layout aid).

#### Scenario: Icon rail
- **WHEN** the researcher collapses the sidebar and moves through its links with Tab
- **THEN** each link is still announced by its stage name ("Deduplication", ...), while only its icon shows
- **AND** each link's label is the stage's short name from `stages.yaml` (Deduplication for `dedup`)
Pinned by: `tests/unit/test_ui_followup_r16.py::test_messages_are_announced_once_and_named_links_survive_collapse`, `tests/unit/test_ui_followup_r30.py::test_the_collapsed_sidebar_keeps_each_link_s_name_for_a_screen_reader`, `tests/unit/test_ui_followup_r32.py::test_the_icon_rail_keeps_each_stage_s_name_in_its_label`

### Requirement: Page names and sections
The system SHALL take each stage page's name from its `title` in `stages.yaml` and use it in the sidebar (its `short` label is the same), the breadcrumb, the page heading, the state chips and `wider stages`. Unbuilt stages (full text, extraction, synthesis) are hidden in the sidebar, and `wider stages` lists them as "later". A page named by an earlier title is the page of the same stage.
Surfaces: CLI `wider stages`, `wider status`; UI sidebar, breadcrumb, headings.

#### Scenario: New page names
- **WHEN** `wider stages` runs
- **THEN** the titles of the ten available stages, in order, are "Question and seed papers", "Concepts and synonyms", "Search strings", "Search results", "Deduplication", "Missing abstracts", "Ranking", "Samples and labelling", "Screening" and "Recall and report", with the keys and commands unchanged
- **AND** each stage page's sidebar link, breadcrumb and heading show the same title
Pinned by: `tests/unit/test_cli.py::test_stages`, `tests/unit/test_shell.py::test_page_names_follow_the_registry`

#### Scenario: Page headings follow the registry
- **WHEN** the `title` of the stage `rank` in the registry given to the page is "Ranked records"
- **THEN** the heading of the Ranking page reads "Ranked records", as do its sidebar link and breadcrumb
Pinned by: `tests/unit/test_shell_js_node.py::test_page_headings_from_registry`

#### Scenario: Later stages hidden
- **WHEN** the sidebar is drawn
- **THEN** it has no link for `fulltext`, `extract` or `synthesis`
- **AND** `wider stages` lists these three with the section "later"
Pinned by: `tests/unit/test_shell_js_node.py::test_later_stages_hidden`, `tests/unit/test_cli.py::test_stages`

### Requirement: Sections in the stage registry
The system SHALL give each available stage in `wider/stages.yaml` a `section` from the top-level `sections:` list (number and title); extension stages have none. An available stage with a missing or unknown section is an error when the registry is loaded.
Surfaces: CLI `wider stages` (section column); UI sidebar section headings.

#### Scenario: Stage without a known section
- **WHEN** a registry given to `load_stages` has an available stage with no section, or with a section not in `sections:`
- **THEN** loading it is refused with an error naming the stage, and an extension stage without a section loads
Pinned by: `tests/unit/test_guided.py::test_registry_refuses_unknown_section`

### Requirement: Model for query creation page
The system SHALL offer a page "Model for query creation" (`#model`, not a stage) to add an AI model, find AI models on this computer, give consent and check it. While no `construct` backend is set it shows three steps (where it runs, which model, check it), else a card per backend with the chosen one marked. Choosing a card makes it the `construct` backend and the default.
Surfaces: CLI `wider providers stage|set-default|add|detect|models|allow|probe`; UI Model for query creation page.

#### Scenario: First visit
- **WHEN** a new project opens the model page
- **THEN** it shows step 1 "Where should the model run?" with the choices CLI, Cloud and Local, and steps 2 "Which model?" and 3 "Check it"
- **AND** opening the page calls only `GET /api/stages`, `GET /api/providers` and `GET /api/embeddings`: no detection, model list, probe or backend check runs until the researcher presses "Find AI models on this computer" or "Check"
Pinned by: `tests/unit/test_model_page_node.py::test_guided_steps_on_first_visit`

#### Scenario: Choosing a model
- **WHEN** the researcher chooses the card of backend `a`
- **THEN** `project.yaml` holds `default_provider: a` and `stage_providers.construct: a`, as after `wider providers set-default a` and `wider providers stage construct a`
- **AND** the page then shows the card grid with `a` marked as chosen, and a later visit shows the grid, not the steps
Pinned by: `tests/unit/test_model_page_node.py::test_choosing_a_card_sets_construct_and_default`, `tests/unit/test_setup_routes.py::test_model_choice_matches_cli`

#### Scenario: Undoing a choice and removing the chosen model
- **WHEN** the researcher chooses backend `a` on the model page
- **THEN** History lists two changes, the construct backend and the default, and undoing both restores `project.yaml` as before, as `wider history undo` does
- **AND** when `a` is then chosen again and removed on Settings or with `wider providers remove a`, `stage_providers.construct` is unset and the model page shows the three steps again
Pinned by: `tests/unit/test_setup_routes.py::test_model_choice_undo_and_remove`, `tests/unit/test_model_page_node.py::test_guided_steps_return_after_remove`

#### Scenario: Checking the chosen model
- **WHEN** the researcher presses "Check" in step 3 for a cloud backend without `probe` consent
- **THEN** the consent dialog opens first and nothing is sent until the researcher allows it
- **AND** once allowed, only the fixed test question is sent, and the step shows the answer as ok or names the error
Pinned by: `tests/unit/test_model_page_node.py::test_check_asks_consent_first`

#### Scenario: Concepts and synonyms page uses the chosen model
- **WHEN** `b` is the `construct` backend and `a` the default
- **THEN** the backend select of the Concepts and synonyms page preselects `b`, as `wider construct propose` does without `--provider`
Pinned by: `tests/unit/test_model_page_node.py::test_construct_select_prefers_the_stage_backend`

### Requirement: Model picker cards
The system SHALL show the backend choice on the model, Ranking (embedding) and Screening (LLM) pages as cards from one builder: name, model, locality chip, destination, key variable "NAME set" or "NAME MISSING" (never a value), last check, and a mark on the chosen card; choosing is a button the keyboard reaches. Test backends stay behind "Show test models" unless chosen.
Surfaces: UI model, Ranking and Screening pages; CLI `wider providers list`, `wider embed list`.

#### Scenario: Card contents
- **WHEN** a cloud backend `a` naming `OPENAI_API_KEY` (not set) and a local backend `b` are shown, with `b` chosen
- **THEN** `a`'s card reads "remote", destination `api.openai.com` and "OPENAI_API_KEY MISSING"; `b`'s card reads "local"
- **AND** only `b`'s choose button has `aria-pressed="true"`, and no key value appears in any card
Pinned by: `tests/unit/test_pickers_js_node.py::test_card_fields_and_chosen_mark`

#### Scenario: Same fields on the command line
- **WHEN** `wider providers list --json` and `wider embed list --json` run on the same project
- **THEN** they print the rows the cards are drawn from: the same names, models, localities, destinations, key states and last checks
Pinned by: `tests/unit/test_pickers_js_node.py::test_cards_match_providers_list`, `tests/unit/test_cli_setup.py::test_list_json_matches_rows`

#### Scenario: Test models hidden until asked
- **WHEN** a test backend is listed and not chosen
- **THEN** its card is hidden and a "Show test models (1)" button shows it; a chosen test backend stays visible
Pinned by: `tests/unit/test_pickers_js_node.py::test_test_models_hidden_until_shown`

### Requirement: Pages redraw after an action
The system SHALL redraw a page's cards, counts, selects and "Latest result" when an action's answer arrives, without a reload: sidebar states, project title, run, ranking, sample and protocol selects, and the Deduplication chart, list heading and notebook count, also after a change on another page. While the labelling queue is open, a label save leaves the sidebar, footer, journey line and guide until the queue is left.
Surfaces: UI every stage page and the sidebar.

#### Scenario: New ranking in the select
- **WHEN** an ablation on the Ranking page stores new rankings
- **THEN** the ranking select and the run selector list them without a reload
Pinned by: `tests/unit/test_shell_js_node.py::test_pages_redraw_after_actions`

#### Scenario: Queue heading and sidebar follow a decision
- **WHEN** a duplicate pair is decided in the pair view, or a stage becomes done
- **THEN** the "Pairs to decide" list heading and the Notebook count show the new values at once, and the sidebar shows the new state
- **AND** a project renamed on the Question and seed papers page shows its new title in the sidebar without a reload
Pinned by: `tests/unit/test_shell_js_node.py::test_pages_redraw_after_actions`

#### Scenario: A protocol screened elsewhere
- **WHEN** a screening run finishes and the researcher then opens the Recall and report page
- **THEN** its protocol select lists the new protocol without a reload
Pinned by: `tests/unit/test_shell_js_node.py::test_pages_redraw_after_actions`

#### Scenario: Label saves in the queue
- **WHEN** the labelling queue is open and two labels are saved
- **THEN** the sidebar, footer, journey line and guide are not redrawn until the queue is left, then redrawn once
Pinned by: `tests/unit/test_shell_js_node.py::test_no_dev_count_beside_queue`

### Requirement: A newer notice closes the earlier ones
The system SHALL close the earlier toasts that are neither errors nor carry an action (such as Undo) when a new toast appears, so at most one plain notice shows; an action toast closes after 20 seconds, waiting while it holds the focus or the mouse, or when dismissed; an error toast when dismissed. Result cards and metric tiles write a count of one in the singular ("1 protocol").
Surfaces: UI toasts and result cards on every page; CLI not applicable (each command prints its own lines).

#### Scenario: Two notices in a row
- **WHEN** a success toast is shown and then another success toast, and earlier an error toast
- **THEN** only the newer success toast and the error toast remain
- **AND** an Undo toast shown before them is still there, and closes 20 seconds after it opened
Pinned by: `tests/unit/test_shell_js_node.py::test_newer_toast_closes_earlier_ones`

#### Scenario: An Undo toast survives a newer notice
- **WHEN** a toast with an Undo button is shown and then a plain success toast
- **THEN** both remain, and the Undo button still works
Pinned by: `tests/unit/test_shell_js_node.py::test_newer_toast_closes_earlier_ones`

#### Scenario: A toast that holds the focus waits
- **WHEN** a toast with an Undo button holds the keyboard focus when its 20 seconds pass, and the mouse rests on another Undo toast
- **THEN** both stay open, and each closes once it holds neither the focus nor the mouse
Pinned by: `tests/unit/test_a11y_node.py::test_toast_waits_while_hovered_or_focused`

#### Scenario: One protocol
- **WHEN** a result card has the metric `protocols` with the value 1
- **THEN** its tile reads "1 protocol", and with 2 it reads "2 protocols"
Pinned by: `tests/unit/test_shell_js_node.py::test_metric_tiles_agree_in_number`

### Requirement: Sidebar footer
The system SHALL end the sidebar with Notes and tags, History and Settings, then "N things need you" (needs-you and needs-review items, linking to the Overview, which lists them; not on the Overview) or "Nothing waits for you.", and "Next: <recommended step>". Questions (open count, needs-you colour above zero) and Help are in the top bar. No "N of M steps done" line or bar.
Surfaces: UI sidebar and top bar; CLI `wider status`, `wider questions list`.

#### Scenario: No development count beside the queue
- **WHEN** the labelling queue is open on a project whose development sample has open labels, and a development record and then a validation record are labelled
- **THEN** the sidebar words for Screening, the footer, the journey line and the guide say "development labels open" with no number, and none of them changes between the two labels; they refresh when the queue is left
Pinned by: `tests/unit/test_shell_js_node.py::test_no_dev_count_beside_queue`, `tests/unit/test_guide.py::test_no_dev_count_in_guide`

#### Scenario: Steps done
- **WHEN** the first four of the ten available stages are done, Deduplication has 3 pairs to decide and Missing abstracts is next
- **THEN** the sidebar footer reads "1 thing needs you" and "Next: Missing abstracts", and no "steps done" text or bar is drawn
- **AND** with two open questions, the Questions button of the top bar shows 2
Pinned by: `tests/unit/test_shell_js_node.py::test_sidebar_footer_needs_and_next`

### Requirement: Top bar
The system SHALL show on every page a top bar with WIDER, an "All projects" button to Home and, inside a project, the breadcrumb (the project's short title / the page name) with a "Switch project" menu beside the title (no step number), the labelled "Guide" button with its state, the Notebook button where the page has one, and the page's main button where it has one. The stepper above the page title is gone.
Surfaces: UI top bar on every page; CLI `wider status` (the stages in order).

#### Scenario: Deduplication top bar
- **WHEN** the Deduplication page of the project "Practice review" opens after a run
- **THEN** the top bar shows "All projects", "Practice review / Deduplication", "Switch project", "Guide", the Notebook button and the main button "Apply decisions and check again"
Pinned by: `tests/unit/test_shell_js_node.py::test_topbar_dedup`

#### Scenario: A page not yet through the pass
- **WHEN** the Ranking page opens
- **THEN** its top bar shows "All projects", the breadcrumb, "Switch project" and "Guide", and no Notebook button and no main button
- **AND** the page's own buttons stay where they are
Pinned by: `tests/unit/test_shell_js_node.py::test_topbar_page_not_through_pass`

#### Scenario: Home
- **WHEN** Home opens
- **THEN** the top bar shows "All projects" marked as the current page, and no breadcrumb, switcher or Guide button
Pinned by: `tests/unit/test_shell_js_node.py::test_topbar_home`

### Requirement: Font shipped with WIDER
The system SHALL draw the UI in Public Sans from font files shipped inside the WIDER package, with the licence text beside them, and fall back to the system font. No page, style sheet or script refers to a font, style sheet or script on another host.
Surfaces: UI every page; CLI not applicable (the CLI prints plain text).

#### Scenario: No online font service
- **WHEN** the UI's HTML, CSS and scripts are read
- **THEN** every `@font-face` source is a path under the UI's own static folder, and no `<link>`, `@import`, `<script src>` or `@font-face` names an `http://` or `https://` address
- **AND** the installed package contains the font files and the SIL Open Font License text

Pinned by: `tests/unit/test_shell.py::test_no_online_font_or_script`

#### Scenario: Font served locally
- **WHEN** the local UI server is asked for a font file named in the style sheet
- **THEN** it answers 200 with the media type `font/woff2`, on every supported Python version and system

Pinned by: `tests/unit/test_shell.py::test_font_served_locally`

### Requirement: Colour tokens pass contrast
The system SHALL define the UI colours as named tokens for blue (you and the main action), violet (by WIDER), stone (open), rose (needs you), cyan (unsure), amber (needs review) and green (done), each a text colour and a wash, in a light and a dark theme. In both themes each tone's text on its wash, blue text on the page, body text and the main button's text on its fill pass 4.5:1, and field borders and the focus ring pass 3:1 against the page.
Surfaces: UI every page; CLI not applicable.

#### Scenario: Contrast of every pair
- **WHEN** the tokens of the style sheet are read for the light and the dark theme
- **THEN** every text-on-wash pair, body text on the page, and the main button's text on its fill reach at least 4.5:1
- **AND** field borders and the focus ring reach at least 3:1 against the page background
Pinned by: `tests/unit/test_style_tokens.py::test_contrast_of_every_pair`

### Requirement: Status shown by word and icon
The system SHALL show every status on the pass's pages with a word and an icon, the colour only adding to them: open, waiting for you, duplicate, not duplicate, unsure, by WIDER, done, needs review. The page-level "Waiting for you" is a small tinted chip next to the page title. Icons are hidden from screen readers, so the word is read once.
Surfaces: UI Deduplication page (chips, flow-chart boxes, list rows, pair view); CLI prints the same words.

#### Scenario: Waiting chip
- **WHEN** the Deduplication page has pairs waiting
- **THEN** next to the title is a chip reading "Waiting for you" with an icon marked `aria-hidden`, in the rose wash, at the size of other chips
Pinned by: `tests/unit/test_dedup_page_node.py::test_counts_after_a_run_with_open_pairs`

#### Scenario: Every status has a word and an icon
- **WHEN** the Deduplication page is drawn with one list row of each status
- **THEN** every status chip and list-row mark holds a text word and an icon element with `aria-hidden="true"`
- **AND** no two statuses share the same word and icon
Pinned by: `tests/unit/test_dedup_page_node.py::test_every_status_has_a_word_and_an_icon`

### Requirement: Results dashboard in the sidebar
The system SHALL show a fixed link "Results dashboard" (address `#results`) as the last link of sidebar section 4, after "Recall and report", with an icon and no state mark, like the model page in section 1. It is a page, not a stage: it is not numbered, not in `stages.yaml` and has no state of its own. The breadcrumb and page heading read "Results dashboard".
Surfaces: UI sidebar on every page inside a project; CLI `wider results` (the page's content), `wider stages` (unchanged).

#### Scenario: Link after Recall and report
- **WHEN** the sidebar is drawn with the ten available stages, the first four done
- **THEN** section 4 lists "Screening", "Screening review", "Recall and report" and then "Results dashboard", whose link goes to `#results`, has an icon, no state words and no "n. " in its tooltip
- **AND** the sidebar footer is unchanged by the link, `wider stages` lists the same ten stages as before, and opening `#results` shows the heading and breadcrumb "Results dashboard" with the link marked `aria-current="page"`
Pinned by: `tests/unit/test_shell_js_node.py::test_results_link_after_recall`, `tests/unit/test_results_js_node.py::test_results_hash_opens_page`, `tests/unit/test_cli.py::test_stages`

#### Scenario: Collapsed sidebar
- **WHEN** the sidebar is collapsed to the icon rail
- **THEN** the Results dashboard link keeps its accessible name "Results dashboard"
Pinned by: `tests/unit/test_shell_js_node.py::test_results_link_after_recall`

### Requirement: Screening review in the sidebar
The system SHALL show a fixed link "Screening review" (address `#review`) in sidebar section 4 right after "Screening", with an icon and no state mark. It is a page, not a stage: not numbered, not in `stages.yaml`, with no state of its own. Its breadcrumb and heading read "Screening review"; it names how many records need a person in its tooltip only.
Surfaces: UI sidebar on every page inside a project; CLI `wider review list` (the page's content), `wider stages` (unchanged).

#### Scenario: Link after Screening
- **WHEN** the sidebar is drawn with the ten stages and a result set with 4 records that need a person
- **THEN** the "Screening review" link follows "Screening", goes to `#review`, has an icon, no state words, no "n. " and the tooltip "4 records need a person"
- **AND** the footer and `wider stages` are unchanged, and opening `#review` shows the heading "Screening review" with the link marked `aria-current="page"`
Pinned by: `tests/unit/test_shell_js_node.py::test_review_link_after_screening`, `tests/unit/test_cli.py::test_stages`

#### Scenario: Collapsed sidebar and no screening
- **WHEN** the sidebar is collapsed to the icon rail, or the project has no screening decisions
- **THEN** the link keeps its accessible name "Screening review", and with no decisions its tooltip reads "no screening decisions yet"
Pinned by: `tests/unit/test_shell_js_node.py::test_review_link_after_screening`

### Requirement: Commands kept out of the page text
The system SHALL name no command in text a page writes itself (running text, buttons, its own messages): a card's command, or one the server sends in its own field, sits in a closed "Command line" fold at the card's end, and a stage's commands (`stages.yaml` `commands`) in its Explain panel and in Help's "Page names and commands" card. A server sentence the CLI prints too keeps its words. `wider stages` lists the same commands.
Surfaces: UI every page, Explain panels, Help; CLI `wider stages`.

#### Scenario: A card's command is folded away
- **WHEN** the Search strings page is opened, the Explain panel of Samples and labelling is opened, and `wider stages` runs
- **THEN** the Yield card's text holds no `wider`, and its closed "Command line" fold holds `wider query yield "<term>"`
- **AND** the Explain panel lists "wider sample, wider label" under "For scripts", Help's card shows the same for Samples and labelling, and `wider stages` shows "sample, label" in the command column of `sample`
Pinned by: `tests/unit/test_a11y_static.py::test_no_command_in_page_text`, `tests/unit/test_shell_js_node.py::test_stage_commands_in_explain_and_help`, `tests/unit/test_cli.py::test_stages_lists_each_stage_s_commands`

#### Scenario: No command in the page text
- **WHEN** the page files under `wider/ui/static/` are read, `scripts/list_messages.py` collects the UI messages, and the Results dashboard is drawn for a synthetic project with no recall target
- **THEN** no text outside a "Command line" fold, an Explain panel or Help's card names `wider` followed by a command
- **AND** on the Results dashboard the commands sent as `again_command`, `all_command` and a document's `command` are drawn inside closed "Command line" folds, while the server sentence "No recall target was set before the results; set one with wider validation target." keeps its words
Pinned by: `tests/unit/test_a11y_static.py::test_no_command_in_page_text`, `tests/unit/test_results_js_node.py::test_dashboard_commands_in_folds`

### Requirement: The guide is headed by page names
The system SHALL head each numbered stage section of `docs/guide.md` with the page title from `stages.yaml` alone ("2. Concepts and synonyms") and its subsections in plain words, and give a section's commands in one "For scripts:" line under its heading, so the guide and the sidebar use one name per page and the guide reads without the command line. Sections after the stages keep their names.
Surfaces: docs `docs/guide.md`; UI sidebar and page headings (the same titles); CLI `wider stages`.

#### Scenario: Guide headings follow the registry
- **WHEN** the `##` headings numbered 1 to 10 in `docs/guide.md` are compared with the available stages of `stages.yaml`
- **THEN** each is its stage's title in order ("1. Question and seed papers", ... "10. Recall and report"), followed by a "For scripts:" line naming the stage's commands, and "11. Extension points" keeps its name
- **AND** no heading of the guide holds a back-quoted command or an old name ("Construct workbench", "Query generation", "Anchor ranking", "Abstract enrichment", "LLM screening", "Sampling and labelling"), and no link in `docs/` points at an old anchor
Pinned by: `tests/unit/test_docs.py::test_guide_headings_follow_the_page_names`

### Requirement: One map of page names and commands
The system SHALL keep `docs/names.md`: one row per page (page name, address, stage key or internal id, commands, guide section, spec capability) and the one glossary of the words the pages use. A test SHALL check it against `stages.yaml`, the sidebar's fixed pages and the guide headings, so a renamed page fails until the map follows. Help's "Page names and commands" card shows the same page and command pairs.
Surfaces: docs `docs/names.md`; UI Help page card; CLI `wider stages`.

#### Scenario: The map matches the pages
- **WHEN** `docs/names.md` is compared with `stages.yaml`, the list of fixed sidebar pages in `shell.js` and the guide headings
- **THEN** every available stage and every sidebar page (Home, Overview, Questions, Notes and tags, History, Help, Settings, Model for query creation, Screening review, Results dashboard) has one row with the sidebar's name, its `#` address and its commands, and each guide section it names exists
- **AND** with "Ranking" renamed "Rank order" in a copy of `stages.yaml`, the check reports "docs/names.md: no row for Rank order"
Pinned by: `tests/unit/test_names.py::test_names_map_matches_pages_commands_and_guide`, `tests/unit/test_names.py::test_names_map_reports_a_renamed_page`

### Requirement: One word for one thing on the pages
The system SHALL use one word per meaning on pages and in command help: "Unsure" for the third deduplication decision (the CLI takes `unsure`, and `uncertain` as before), "Seed papers" for the Concepts and synonyms card and the Recall section, "Labelling queue" for the queue heading, `wider history undo` for undo. A page text with "development" or "validation" SHALL say which set it means.
Surfaces: UI every page; CLI `--help` texts, `wider dedup decide`.

#### Scenario: The words checked
- **WHEN** `wider dedup decide 3 7 unsure --by ana` runs on a synthetic project, and the page files under `wider/ui/static/` and the command help are searched
- **THEN** the pair is stored as `manual_uncertain` by "ana", as with `uncertain`, and the help of `wider dedup decide` lists "duplicate | not_duplicate | unsure"
- **AND** no page shows "Screening queue" or "`wider undo`", no help says "`wider undo`", no page shows "Benchmark papers", and the Help keys say "unsure for duplicate pairs"
Pinned by: `tests/unit/test_a11y_static.py::test_one_word_for_one_thing`, `tests/unit/test_dedup.py::test_decide_takes_unsure`

#### Scenario: Development and validation say which set
- **WHEN** `scripts/list_messages.py` collects the UI messages
- **THEN** each that holds the whole word "development" or "validation" also holds, as whole words, "seed paper(s)", "query", "sample", "screening", or is a "Dev"/"Val" chip, or "platform", or the line of code that draws it says which set; otherwise it is on the test's list of exceptions, each with its reason (platform roles, section names such as "Validation checks")
Pinned by: `tests/unit/test_list_messages.py::test_development_and_validation_are_qualified`

### Requirement: Opening a project shows its Overview
The system SHALL open an existing project on its Overview (`#overview`); a new one opens on its start's first page. The last page shown is still kept in the browser under a hash of the folder, never sent, and offered there as "Back to <page>, where you stopped" when it is not the recommended step. An explicit `#page` in the address wins; Home and the Overview are never stored.
Surfaces: UI Home page, Open and Create, the project switcher; CLI `wider next` (the recommended step).

#### Scenario: Back to where the researcher left off
- **WHEN** the researcher works on the Missing abstracts page of project A, switches to project B and back
- **THEN** A opens on its Overview, whose Continue box offers the recommended step and "Back to Missing abstracts, where you stopped"
- **AND** the stored key holds no folder path, and the value is a page name such as `enrich`
Pinned by: `tests/unit/test_shell_js_node.py::test_open_lands_on_overview`

#### Scenario: Same page twice is not offered
- **WHEN** the page last shown is the recommended step, or nothing is stored, or local storage cannot be used
- **THEN** the Continue box offers only the recommended step, without an error
Pinned by: `tests/unit/test_shell_js_node.py::test_open_lands_on_overview`

#### Scenario: No model for query creation yet
- **WHEN** an existing project without a construct model is opened
- **THEN** its Overview's Continue recommends the Model for query creation page
Pinned by: `tests/unit/test_shell_js_node.py::test_open_lands_on_overview`

#### Scenario: An explicit page
- **WHEN** an address ending in `#dedup` is opened
- **THEN** the Deduplication page opens whatever is stored
Pinned by: `tests/unit/test_shell_js_node.py::test_open_lands_on_overview`

### Requirement: Project Overview page
The system SHALL give every project an Overview (`#overview`, first sidebar link) with, in order: Continue (the recommended step, why, its button), Needs attention (each `needs_you` and `needs_review` item, its reason and one action), Research journey (every stage's state and last result) and Project summary (question, route, searches, records, reviewers, models and where they run, lookups). No percentages.
Surfaces: UI Overview page; CLI `wider status` (the same states, items and "next:").

#### Scenario: Overview of a project with work waiting
- **WHEN** the synthetic project has 3 pairs to decide and Ranking needs review after a newer Missing abstracts result
- **THEN** the four section headings appear in the order Continue, Needs attention, Research journey, Project summary
- **AND** Needs attention lists "3 pairs to decide" with a button to Deduplication and the Ranking item with "Update ranking" and "Keep this ranking", each with its reason; "Update ranking" on a ranking that samples were drawn from first says briefly that those samples keep their version
- **AND** it lists open questions (their count and a link to Questions) and review packages still out (their count only), and `wider status` prints the same items
- **AND** `wider status` names the same two items and the same next step
Pinned by: `tests/unit/test_shell_js_node.py::test_overview_sections_in_order`, `tests/unit/test_guided.py::test_status_lists_needs_and_review`

#### Scenario: Nothing for a blind reviewer to read
- **WHEN** the Overview is drawn while the query is not locked and the development sample has open labels
- **THEN** no item shows a model decision, a miss or false-include count, a validation seed paper result, or another reviewer's labels
- **AND** each model in Project summary shows where it runs from the backend's configuration, "unknown" until checked, as on the Info panel
Pinned by: `tests/unit/test_shell_js_node.py::test_overview_hides_judgments`

### Requirement: Project switcher
The system SHALL offer, in the top bar of every project page, a "Switch project" menu listing the recent projects with their next step and how many items need the researcher, and "All projects and new review". Choosing a project opens it (one open project at a time) on its Overview. Esc closes the menu and returns the focus to its button.
Surfaces: UI top bar; CLI `wider ui --dir` (open another project).

#### Scenario: Switching and back
- **WHEN** the researcher switches from project A to project B and back with the menu
- **THEN** each opens on its Overview, A's Overview offers "Back to <A's last page>", and B's folder is the one every route reads while B is open
Pinned by: `tests/unit/test_shell_js_node.py::test_switch_project_menu`

### Requirement: One name, asked once
The system SHALL keep one name per browser, with no login, that signs what the pages save (D33). Home, and a project opened without one, ask for it ("Your name"); the top bar shows "Working as: <name> · Change". Change switches the name and reloads the page, so nothing of the earlier name's own work stays in view; an empty name is forgotten.
Surfaces: UI Home, top bar; CLI `--by`, `--author`, `WIDER_REVIEWER`.

#### Scenario: Home asks once
- **WHEN** no name is set and Home opens
- **THEN** it shows a "Your name" field with "Decisions, labels and notes are signed with this name." in normal black text
- **AND** after "ana" is saved the field is gone, the top bar reads "Working as: ana · Change" and `wider.reviewer` is "ana"
Pinned by: `tests/unit/test_name_once_node.py::test_home_asks_for_the_name_once`

#### Scenario: A project opens without a name
- **WHEN** no name is set and a project opens
- **THEN** the name dialog opens once, with the same line; "Not now" closes it and the top bar reads "No name yet · Set name"
- **AND** nothing is blocked: pages that sign ask again only when something is saved
Pinned by: `tests/unit/test_name_once_node.py::test_opening_a_project_without_a_name_asks_once`

#### Scenario: Switching names
- **WHEN** WIDER works as "ana" and "bo" is saved with Change
- **THEN** `wider.reviewer` is "bo" and the page reloads, and another open tab of WIDER reloads too, as the dialog says
- **AND** saving the same name again reloads nothing, and Cancel changes nothing
- **AND** a WIDER tab brought back with Back or Forward after the switch reloads too
Pinned by: `tests/unit/test_name_once_node.py::test_change_switches_the_name_and_reloads`, `tests/unit/test_name_once_node.py::test_another_tab_reloads_when_the_name_changes`, `tests/unit/test_name_once_node.py::test_a_page_brought_back_by_back_follows_the_name`

#### Scenario: No name field on the pages
- **WHEN** the Labelling, History, Deduplication, Screening review, Lock/Unlock, Validation and record drawer pages render
- **THEN** none has a field for the researcher's own name, and a save without a name says "enter your name first: use Change beside “Working as” in the top bar" with a "Set your name" button
- **AND** the fields that name other people stay: Reviewer A and B, the review package reviewer and the notes author filter
Pinned by: `tests/unit/test_name_once_node.py::test_no_page_has_its_own_name_field`, `tests/unit/test_name_once_node.py::test_a_refusal_points_at_change_and_opens_the_dialog`

### Requirement: Journey line
The system SHALL show under the top bar of every project page a transit line: the four stages as segments, each step a station linking to its page, the same pages in the same order as the sidebar (Model first), marked completed, current (`wider next`) or needs attention by icon and words; the open page underlined. "Hide" folds it (kept as `wider.journey`), "Show the journey line" brings it back. It scrolls below 860 px.
Surfaces: UI every project page; CLI `wider status` (the same states).

#### Scenario: Hide and show
- **WHEN** the researcher hides the journey line on the Ranking page and opens the Overview
- **THEN** the line stays hidden, "Show the journey line" is offered, and showing it marks Ranking as the current step with its state icon and words, among all four stages and their steps
Pinned by: `tests/unit/test_shell_js_node.py::test_journey_line_hide_show`

#### Scenario: Journey and sidebar list the same pages
- **WHEN** a project page is drawn with the model set
- **THEN** the journey's stations and the sidebar's links name the same pages in the same order, and the top bar shows no step number
Pinned by: `tests/unit/test_shell_js_node.py::test_journey_and_sidebar_list_the_same_steps`

### Requirement: What you can do here now
The system SHALL open every stage page, under its title, with one box from the stage's state saying what the researcher can do there now: work here (and what to do), completed but still changeable (naming the later steps that read its result, from the registry's `used_by` list), locked and read only (with Unlock and its consequence), or not yet (with what it needs, the page still open). The box never disables the page.
Surfaces: UI every stage page; CLI `wider guide <page>`, `wider status`.

#### Scenario: A locked search
- **WHEN** Search strings opens on a project whose query is locked
- **THEN** the box says the strings are locked and can be read and copied, offers "Unlock to change…", and says that unlocking makes the search no longer validated on the validation seed papers and is recorded in History, the methods text and the package
Pinned by: `tests/unit/test_shell_js_node.py::test_can_do_box_states`

#### Scenario: A completed step names what uses it
- **WHEN** Missing abstracts opens after a finished lookup
- **THEN** the box says it is completed, that changing it reruns nothing, and that Ranking and Screening read its abstracts
- **AND** `wider guide enrich` prints the same sentence first
Pinned by: `tests/unit/test_shell_js_node.py::test_can_do_box_states`

#### Scenario: A blocked stage stays open
- **WHEN** Ranking opens before deduplication is done
- **THEN** the box says it needs Deduplication first and the page's controls stay enabled
Pinned by: `tests/unit/test_shell_js_node.py::test_can_do_box_states`

### Requirement: Previous and Continue on stage pages
The system SHALL end every stage page with "Previous" and "Continue to <next page>" links named after the neighbouring stage pages (fixed pages such as Screening review are not neighbours); the first and last pages show only the one that exists.
Surfaces: UI every stage page; CLI `wider next`.

#### Scenario: Missing abstracts
- **WHEN** the Missing abstracts page opens
- **THEN** it ends with "Previous Deduplication" and "Continue to Ranking"
Pinned by: `tests/unit/test_shell_js_node.py::test_prev_next_links`

### Requirement: Starting points on Home
The system SHALL offer on Home four ways to start a review, each saying what the researcher needs and what the start means: a research question, a search strategy you already have, records you already retrieved, an existing WIDER project. A new project opens on its start's first page (Model for query creation, Search strings, Search results); nothing about the start is stored.
Surfaces: UI Home; CLI `wider init` then the matching command (`wider providers`, `wider query`, `wider import`).

#### Scenario: Starting from records
- **WHEN** a project is created from "Records you already retrieved" and one export is imported
- **THEN** it opened on Search results; the Overview's route sentence, derived from the project's state, says writing search strings is optional, that Question and seed papers and Concepts and synonyms are still needed for ranking and screening, and that seed paper coverage is checked once the uploads are marked final
- **AND** the sentence never calls Concepts and synonyms optional, Continue names what `wider next` names, and `wider status` prints the same route sentence
Pinned by: `tests/unit/test_shell_js_node.py::test_start_from_records`, `tests/unit/test_guided.py::test_route_sentence_from_state`

#### Scenario: Starting from a search strategy
- **WHEN** a project is created from "A search strategy you already have"
- **THEN** it opens on Search strings, which offers importing the string as a draft (`wider query import`) and says Question and seed papers and Concepts and synonyms are still needed
Pinned by: `tests/unit/test_shell_js_node.py::test_start_from_strategy`

#### Scenario: An existing project and an empty Home
- **WHEN** "An existing WIDER project" is chosen and a folder holding `project.yaml` is opened, and another time Home has no recent projects
- **THEN** the opened project shows its Overview; the empty Home shows the four starts and the demo link, and says projects will be listed here once created or opened
Pinned by: `tests/unit/test_shell_js_node.py::test_start_existing_and_empty_home`

#### Scenario: A records start before any import
- **WHEN** a project created from "Records you already retrieved" is opened before any file is imported
- **THEN** it opens on Search results, whose box says importing the records you already have is fine before writing search strings, and Search strings' box says the same
Pinned by: `tests/unit/test_shell_js_node.py::test_records_start_before_import`

#### Scenario: Documenting an outside search
- **WHEN** Search strings opens on a project with imported records and no locked search
- **THEN** its box says the step is optional here, suggests recording the search used outside WIDER for reproducibility (for example by importing the string as a draft), and the step is not marked completed
Pinned by: `tests/unit/test_shell_js_node.py::test_can_do_box_states`

### Requirement: Interactive demo from Home
The system SHALL show on Home a clearly labelled "Explore the demo" link that opens the existing demo in a separate tab, saying that the demo uses invented data, is separate from your projects, and opens a page on the internet without sending anything from your projects. It is a link only; WIDER makes no request for it.
Surfaces: UI Home; CLI none (a link to a website).

#### Scenario: The demo link
- **WHEN** Home is drawn
- **THEN** it holds one link "Explore the demo" with `target="_blank"` and `rel="noopener noreferrer"` to the demo address in `docs/guide.md`, next to the sentence that the demo is separate from your projects, and loading Home makes no request to that address
Pinned by: `tests/unit/test_shell_js_node.py::test_home_demo_link`

### Requirement: Settings on the Data cleaning pages
The system SHALL show on Deduplication and Missing abstracts every setting those steps use, in named sections with a plain explanation and the default, none behind an "Advanced" fold: thresholds, title rules, thin-abstract words, sources and their order. A change equals `wider protocol set` and is journalled; batch size is a run option.
Surfaces: UI Deduplication and Missing abstracts pages; CLI `wider protocol set dedup.*|corpus.thin_words|enrichment.*`.

#### Scenario: Every setting visible
- **WHEN** Deduplication and Missing abstracts open
- **THEN** every field of the protocol's `dedup`, `corpus.thin_words` and `enrichment` sections appears on its page in a named section with its explanation and default, and no element on either page is a fold named "Advanced"
- **AND** the batch size sits beside the lookup button as the run option `wider enrich --limit`, and `corpus.max_words` stays with Ranking, which it shapes
- **AND** beside the thin-abstract words the page says changing it reruns nothing and marks Ranking "needs review" while a ranking made with input tracking exists (a ranking from before this change is never marked, and the line then omits that clause), and `wider protocol set corpus.thin_words` prints the same line
- **AND** changing the thin-abstract words on the page and `wider protocol set corpus.thin_words 30` give the same stored value and the same History entry
Pinned by: `tests/unit/test_dedup_page_node.py::test_every_setting_visible`, `tests/unit/test_enrich_page_node.py::test_every_setting_visible`

### Requirement: Open work sets the recommended step
The system SHALL treat a stage as having open work when it is ready, or done with work left for you on Deduplication, Screening or Recall and report; the recommended step is the first such stage in journey order. Deduplication has work left while pairs wait or records were imported since its last run. Open questions and Review plan tasks do not move it.
Surfaces: CLI `wider next`, `wider status`; UI Overview Continue, sidebar footer, Home card.

#### Scenario: Pairs to decide come before an empty later step
- **WHEN** a synthetic project has Deduplication done with 7 pairs to decide and Missing abstracts ready
- **THEN** `wider next` prints `wider dedup` and the Overview's Continue names Deduplication with "7 pairs to decide"
Pinned by: `tests/unit/test_guided.py::test_next_open_work_dedup`

#### Scenario: Records imported after the last run
- **WHEN** records are imported after the last deduplication run and no pair waits
- **THEN** Deduplication needs you with "N new records not checked for duplicates" and is the recommended step
Pinned by: `tests/unit/test_guided.py::test_next_unchecked_imports`

### Requirement: Stage chip and box agree
The system SHALL derive a stage's chip, journey mark and "What you can do here now" box from one state. A done stage with work left reads "Not finished: it needs you: ..." in its box. The Review plan needs you while a required task card is not done; Recall and report while its last report has no recall estimate. A stage whose results all come from the test model shows a "Test model" chip and a red line in its box.
Surfaces: UI chips, journey line, the box; CLI `wider status` (state column).

#### Scenario: Done with work left
- **WHEN** Deduplication is done with 7 pairs to decide
- **THEN** its chip reads needs you and its box starts "Not finished: it needs you: 7 pairs to decide", without "Completed"
Pinned by: `tests/unit/test_guided.py::test_done_with_work_left_box`

#### Scenario: Prior reviews not done
- **WHEN** a project has a question and a seed paper but no prior review and no "none known"
- **THEN** the Review plan needs you with "Prior reviews to do" and `wider next` prints `wider construct`
Pinned by: `tests/unit/test_guided.py::test_setup_needs_prior_review`

#### Scenario: Results from the test model
- **WHEN** every ranking uses a fake embedding set and every screening run used the test backend
- **THEN** Ranking, Screening and Recall and report carry `test_model`, their chips read "Test model", and `wider status` adds "test model" to their state
Pinned by: `tests/unit/test_guided.py::test_test_model_flag`
