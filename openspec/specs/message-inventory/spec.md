# message-inventory Specification

## Purpose
A developer tool that lists every message WIDER shows to a researcher, where it is shown and which plain-language
rules of the word list it breaks, so the plain versions can be written without missing a message.

## Requirements

### Requirement: UI messages are collected
The tool `scripts/list_messages.py` SHALL list, with text, `file:line` and kind, the visible text and the
`title`, `placeholder`, `aria-label`, `alt` values of `wider/ui/static/index.html` and the text that
`wider/ui/static/*.js` puts on the page ("UI text", "toast", "UI dialog" for `confirm()`, `alert()`, `prompt()`).
Comments and selectors are not messages.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py [--out F] [--summary] [--root D]`; no UI.

#### Scenario: UI messages of each kind
- **WHEN** the tool runs with `--root` on a small synthetic tree holding one HTML button label, one JS toast, one JS
  `confirm()` and a one-word button label "Remove" inside an HTML template literal, plus a comment and a CSS selector
  and a value of a `STATE_WORD` map
- **THEN** each of the five appears once with its kind and its correct `file:line`, and the comment and the
  selector do not appear

Pinned by: `tests/unit/test_list_messages.py::test_ui_messages_of_each_kind`

### Requirement: API and result messages are collected
The tool SHALL list `raise` and `HTTPException` messages under `wider/` outside the CLI ("API error"), and as
"result text": prose literals in `wider/ui/*.py`, `wider/core/*.py` and the five engine files of design decision 1,
the metric and row keys of `stage_summaries.py`, and the `title`, `prompt` of `raise_question(...)` and text of
`progress(...)` under `wider/`. Docstrings, other dict keys and logging are not messages.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py`; no UI.

#### Scenario: API and result messages of each kind
- **WHEN** the synthetic tree holds one `HTTPException` detail, one engine `raise ValueError`, one result headline
  and one metric key given inline and one column header given through a variable in `stage_summaries.py`, one
  `raise_question` title given as `title or f"..."`, one prompt joined with `+`, one `progress` text and one
  returned text in `wider/search/lint.py`, plus a docstring, a prose dict key and a logging call in a `wider/core` file
- **THEN** each of the nine appears once with its kind and its correct `file:line`, and the docstring, the dict key
  and the logging call do not appear

Pinned by: `tests/unit/test_list_messages.py::test_api_and_result_messages`

### Requirement: CLI and stage messages are collected
The tool SHALL list text printed, asked (`typer.confirm`, `typer.prompt`) or raised by `wider/cli*.py` and its
table titles ("CLI output"); option `help=`,
`typer.Typer(help=...)` and command docstrings ("CLI help"); and per stage in `wider/stages.yaml` the `title`,
`short`, `description`, `why`, `command` (as "wider <command>"), `produces` (one joined text) and section
titles ("stage text").
Surfaces: CLI `.venv/bin/python scripts/list_messages.py`; no UI.

#### Scenario: CLI and stage messages of each kind
- **WHEN** the synthetic tree holds one CLI `rprint`, one `typer.BadParameter` message, one `typer.confirm`
  prompt, one option help, one decorated command's docstring and one stage with a description, a command and a
  produces list of two items
- **THEN** the first three are "CLI output", the next two "CLI help", and the stage gives its description, "wider
  <command>" and one text of its two produces items as "stage text", each with its correct `file:line`

Pinned by: `tests/unit/test_list_messages.py::test_cli_and_stage_messages`

#### Scenario: Every listed line exists in the real repository
- **WHEN** the tool runs on the WIDER repository
- **THEN** every kind has at least one message; for every message the named line of the named file holds the
  start of its source form (design decision 9); and the known messages named there are listed

Pinned by: `tests/unit/test_list_messages.py::test_real_repository_lines`

### Requirement: Catalogue texts are collected
The tool SHALL list, as "catalogue text", the `label`, `vendor`, `billing`, `defaults.extra.auth_hint` and `telemetry.note`
of each entry in `wider/providers/presets.yaml`, under Model for query creation, Setup and Settings, and the
`label`, `description`, `coverage` and `cap_note` of each entry in `wider/search/platforms.yaml` under Setup and
its `paste` under Search strings.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py`; no UI.

#### Scenario: Catalogue texts and their pages
- **WHEN** the synthetic tree has a `presets.yaml` entry with an `auth_hint` holding a back-quoted command and a
  `platforms.yaml` entry with a `coverage` and a `paste`
- **THEN** the hint is listed under Model for query creation, Setup and Settings with the `command` flag, the
  coverage under Setup and the paste under Search strings, each with its correct `file:line`

Pinned by: `tests/unit/test_list_messages.py::test_catalogue_texts`

### Requirement: The tool reads source files only
The tool SHALL read only files under `wider/` and `docs/guide-text.md` of the given root (default: the folder
above `scripts/`). It SHALL open no project,
contact no service, and write nothing except the file named by `--out`. Paths in its output are relative to the
root.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: No absolute paths in the output
- **WHEN** the tool runs on the synthetic tree in a temporary folder
  with every network socket made to fail
- **THEN** the run succeeds, the output does not contain that folder's absolute path, and the folder holds no new
  file

Pinned by: `tests/unit/test_list_messages.py::test_no_absolute_paths_and_no_new_file`

### Requirement: Where the researcher sees each message
Each message SHALL name where it is seen: its page (a stage title from `wider/stages.yaml`, or Your projects,
Model for query creation, Questions, Notes and tags, History, Help, Settings) from its HTML section, script part
or named function (JS or `stage_summaries.py`), each page a shared part serves; "every page" for shared
scripts; the stage's page and Help for stage text; "CLI only".
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Page from the HTML section, the script part and a named function
- **WHEN** the synthetic tree has a label inside `<section id="page-dedup">`, a toast under the "dedup page" part of
  `app.js`, a toast in `loadProject` inside the "construct page" part, a headline in `summarize_dedup`, a text in
  the `pickers.js` part before its first marker, and a toast in a shared script
- **THEN** the first two and the headline are listed under Deduplication, the third under the setup page, the
  `pickers.js` text under Model for query creation, Ranking and Screening, and the last under "every page"

Pinned by: `tests/unit/test_list_messages.py::test_page_from_section_part_and_function`

### Requirement: Pages of API messages, and messages without a page
An API error or result text in a route SHALL be listed under each page whose script names that route, whatever
helper sends it. An engine error outside `wider/ui/` SHALL be listed under "any page that calls this". A message
whose page cannot be told, including one in an unknown script or script part, SHALL be listed under "page
unknown", never dropped.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Route, engine and unknown pages
- **WHEN** the synthetic tree has an `HTTPException` in a route that only the ranking part of `app.js` names,
  through `Jobs.start(` (while a shared script holds a call built wholly at run time, `/api/${x}/y`), a `raise
  ValueError` in an engine file, and a toast in a new script `extra.js`
- **THEN** the first is listed under Ranking and not under "every page", the second under "any page that calls this" and the third under
  "page unknown"

Pinned by: `tests/unit/test_list_messages.py::test_route_engine_and_unknown_pages`

### Requirement: Word-list flags
A message SHALL be flagged `word list: <term>` for each term of the "In WIDER today" column of the word-list table
in `docs/guide-text.md` (split on commas) that it contains as a whole word, ignoring case, unless the match lies
inside a plain form of the table (parenthesised parts removed, then split on commas). The table is read each run, so a new row needs no code change.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Word-list terms
- **WHEN** the synthetic word list has the rows "dedup, deduplication | deduplication (author, 17 Oct: keep, it
  only removes duplicates)", "freeze, frozen | lock, locked",
  "development set | query development set (explained in the guide)" and "run | screening round", and the
  messages are "Open dedup again", "Freeze the query development set", "The list is frozen" and "Deduplication is
  done"
- **THEN** the first is flagged `word list: dedup`, the second `word list: freeze` only, the third `word list:
  frozen`, and the fourth has no flag

Pinned by: `tests/unit/test_list_messages.py::test_word_list_terms`

#### Scenario: A new word-list row is used without a code change
- **WHEN** a synthetic `docs/guide-text.md` adds the row "| widget | gadget |" and a message says "Open the widget"
- **THEN** that message is flagged `word list: widget`

Pinned by: `tests/unit/test_list_messages.py::test_new_word_list_row`

### Requirement: Name, command and timestamp flags
A message SHALL be flagged `snake_case` when its shown text holds a name such as `exact_normalized_title`, `command`
when it shows `wider` followed by a sub-command or a template part, or a back-quoted command, and `timestamp` when
it shows an ISO date-time or puts a time value (a name ending in `_at`) into a template without a date formatter.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Name, command and timestamp
- **WHEN** the messages are "stage exact_normalized_title", "use `wider enrich --retry`",
  "checked 2026-10-15T07:51:38+00:00", a JS template "last checked {checked_at}" built from `${s.checked_at}`, and
  one built from `${fmtWhen(s.checked_at)}`, none holding a word-list term
- **THEN** they are flagged `snake_case`, `command`, `timestamp` and `timestamp`, and the last has no flag

Pinned by: `tests/unit/test_list_messages.py::test_name_command_and_timestamp`

### Requirement: Markdown output with counts first
The tool SHALL write Markdown to standard output, or to the `--out` file: a count table (per kind, per page, per
flag, and UI messages with a flag), then the messages grouped by page in sidebar order, each line with text,
`file:line`, kind and flags. `--summary` writes the table only. The same sources give the same output.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Counts, order and a stable output
- **WHEN** the tool runs on the same synthetic tree once with `--summary` and twice with `--out`
- **THEN** the summary is the header line and the count table alone, the file starts with the same two followed by the pages in
  sidebar order, the page table sums to the number of list lines and the total equals the number of distinct
  messages, and the two files are identical

Pinned by: `tests/unit/test_list_messages.py::test_counts_order_and_stable_output`

### Requirement: A folder that is not a WIDER checkout is refused
The tool SHALL exit 2 with one line on standard error, and write no file, when the root lacks `wider/stages.yaml`
or `wider/ui/static/index.html`, when `docs/guide-text.md` has no word-list table, or when PyYAML cannot be
imported. Otherwise it exits 0,
whatever the flags.
Surfaces: CLI `.venv/bin/python scripts/list_messages.py` (developer tool); no UI.

#### Scenario: Not a WIDER checkout
- **WHEN** `--root` names an empty folder and `--out` names a file in it
- **THEN** the tool exits 2 with one line saying the folder is not a WIDER checkout, and the file does not exist

Pinned by: `tests/unit/test_list_messages.py::test_not_a_wider_checkout`
