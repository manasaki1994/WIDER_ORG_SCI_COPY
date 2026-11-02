# UI Accessibility Specification

## Purpose
How the web UI works by keyboard, with a screen reader and at phone width: dialogs, focus after navigation and
actions, the toast's buttons, tab strips, control and chart names, one time format, narrow screens and Enter in
fields. Each requirement is checked by a Node page-script test, a static check of the page files, or the ui-tester
keyboard-only persona (for real Tab order and layout, which a Node stand-in cannot show).

## Requirements

### Requirement: In-page dialogs instead of browser boxes
The system SHALL ask every confirmation, name or text it needs on a page in the app's in-page dialog, never with the browser's `confirm()`, `prompt()` or `alert()`. Cancel has the focus, Esc cancels, Cancel changes nothing, and focus returns to the control that opened it.
Surfaces: UI every page (Cancel a run, import, outputs, model cards, setup, settings, adjacent literatures, Screening set label); CLI each action's own command, as `wider screen sets label`, `wider construct adjacent edit KEY`.

#### Scenario: Cancelling a run from the job bar
- **WHEN** a run is in progress and the researcher presses its Cancel button, then Esc
- **THEN** an in-page dialog asked "Cancel this run? Finished items are kept; starting the run again resumes it." with its Cancel button focused, no request was sent, and the focus is back on the run's Cancel button
- **AND** pressing the dialog's "Cancel the run" instead sends the cancel request once
Pinned by: `tests/unit/test_a11y_node.py::test_cancel_run_asks_in_page`

#### Scenario: Relabelling a result set in the dialog
- **WHEN** the researcher relabels result set 2 on the Screening page, types "pilot" in the dialog's field and presses its "Save label" button, then relabels it again with an empty field, then once more and presses Esc
- **THEN** the first sends one label request with "pilot", the second one with an empty label (the label is removed), and the third sends nothing
- **AND** `wider screen sets label` sets and removes the same label on the command line, as today
Pinned by: `tests/unit/test_a11y_node.py::test_set_label_is_typed_in_the_dialog`

#### Scenario: Editing an adjacent literature in the dialog
- **WHEN** the researcher edits the synthetic adjacent literature "adj-1" on the Concepts and synonyms page, changes its Name to "Synthetic neighbour" in the dialog's four fields and presses "Save", then edits it again and presses Esc
- **THEN** the first sends one edit request with the four fields, and the second sends nothing
- **AND** `wider construct adjacent edit adj-1 --name "Synthetic neighbour"` makes the same new version through the same engine function
Pinned by: `tests/unit/test_a11y_node.py::test_adjacent_edit_is_typed_in_the_dialog`, `tests/unit/test_adjacent.py::test_adjacent_edit_on_the_command_line`

#### Scenario: No browser box is left
- **WHEN** the page scripts under `wider/ui/static/` are searched
- **THEN** none calls `confirm(`, `prompt(` or `alert(` (the four adjacent-literature fields and the Screening set label are typed in the dialog's own fields)
Pinned by: `tests/unit/test_a11y_static.py::test_no_browser_confirm_prompt_or_alert`

### Requirement: The question dialog works by keyboard
The system SHALL move the focus into the question dialog when it opens (its first answer control), treat Esc as "Later" (the questions stay open), and return the focus to the control that had it before the dialog opened.
Surfaces: UI question dialog (ask mode); CLI none (a page dialog): its twin is `wider questions answer`.

#### Scenario: A question arrives during a run
- **WHEN** the focus is on the Screening page's run button and the question dialog opens, and the researcher presses Esc
- **THEN** the focus was on the dialog's first answer control while it was open, the question is still open on the Questions page, and the focus is back on the run button
Pinned by: `tests/unit/test_a11y_node.py::test_question_dialog_focus_and_escape`

### Requirement: A failed consent request says so
The system SHALL show in the consent dialog, in the error style, why an "Allow" request failed and keep the dialog open, so nothing starts and the failure is not taken for a Cancel.
Surfaces: UI consent dialog (online lookups, cloud models); CLI `wider lookups allow` (prints the error and exits 1).

#### Scenario: The server refuses Allow
- **WHEN** the researcher presses Allow in the online-lookup consent dialog and the request answers 500 "could not save project.yaml"
- **THEN** the dialog stays open and shows "could not save project.yaml" as an alert, and no lookup request is sent
Pinned by: `tests/unit/test_a11y_node.py::test_failed_allow_stays_in_the_dialog`

### Requirement: The record drawer is named for screen readers
The system SHALL name the record drawer by its record's title. Opened over a list it SHALL be `role="dialog"`, keep Tab and Shift+Tab inside, and on Esc close and return the focus to the row. Docked beside a list, as on the Deduplication page, it SHALL be `role="region"` with no Tab trap, and Esc moves the focus to the list while it stays open.
Surfaces: UI record drawer on every list page; CLI none (a page layout aid): its twin is `wider notes list`.

#### Scenario: Tab at the end of the drawer
- **WHEN** the drawer of the record "A synthetic title" is open over the Labelling queue and the focus is on its last control, and the researcher presses Tab, then Shift+Tab, then Esc
- **THEN** the drawer has `role="dialog"` and the name "A synthetic title", Tab moved the focus to its first control, Shift+Tab back to the last, and after Esc the drawer is closed and the focus is on that record's row
Pinned by: `tests/unit/test_a11y_node.py::test_drawer_is_a_dialog_that_keeps_tab`

#### Scenario: The docked drawer on the Deduplication page
- **WHEN** a synthetic pair is open in the drawer docked on the Deduplication page, the focus is on its last control, and the researcher presses Tab, then Esc
- **THEN** the drawer has `role="region"` and the pair's first title as its name, the Tab keydown is not prevented and the drawer moves no focus (the browser moves it on to the next control of the page), and after Esc the drawer is still open and the focus is on the pair list
Pinned by: `tests/unit/test_a11y_node.py::test_docked_drawer_is_a_named_region`; the real Tab move by the ui-tester keyboard-only persona (task 10.2)

### Requirement: Opening a page moves the focus to its heading
The system SHALL, when a page is opened after the first one drawn (sidebar link, address, Back or Forward), move the focus to its heading, show its top, and set the window title to "<page title> – WIDER".
Surfaces: UI every page; CLI none (navigation): `wider stages` lists the page titles.

#### Scenario: From the sidebar to Deduplication
- **WHEN** Home is open, scrolled down, and the researcher opens "Deduplication" from the sidebar
- **THEN** the focus is on the Deduplication heading, the page shows its top, and the window title is "Deduplication – WIDER" ("(1 running) Deduplication – WIDER" while one run goes on)
- **AND** on the first page drawn the focus stays where the browser puts it, so "Skip to content" is still the first Tab stop
Pinned by: `tests/unit/test_a11y_node.py::test_page_open_moves_focus_title_and_scroll`

### Requirement: The focus stays on a control after an action
The system SHALL keep the focus on a named control after an action redraws the page: the pressed button or, when it is gone, the result it made. The focus never falls back to the page body.
Surfaces: UI Screening run, a toast's Redo, Ctrl/Cmd+Z, History "Undo" by id, Settings "Revoke", Help stage summaries; CLI none (focus only): each action's twin is unchanged.

#### Scenario: Six actions that redraw
- **WHEN** each of these runs on the stand-in page: a Screening run finishes, a toast's Redo, Ctrl+Z on the Labelling queue, History "Undo" by id, Settings "Revoke" for a backend, and Space on a Help stage summary
- **THEN** the focus is afterwards on, in order: the run button, the queue row, the queue row, the Undo button of the new "undo:" entry in History (its redo), the backend's row (the Revoke button is gone), and the same summary
- **AND** after none of them is the focus on the page body
Pinned by: `tests/unit/test_a11y_node.py::test_focus_kept_after_six_redrawing_actions`

### Requirement: A toast's button and the focused row
The system SHALL return the focus, after a toast's own button is pressed, to the control that had it before, and keep the focused table row clear of the toasts: a row that takes the focus scrolls into view with a bottom margin as high as the open toasts.
Surfaces: UI toasts and tables on every page; CLI none (focus and layout): the toast's actions have their twins (`wider history undo ID`, `wider history redo`).

#### Scenario: Undo from the toast by keyboard
- **WHEN** "ana" saves a label from row 3 of the Labelling queue, moves to the toast's Undo and presses Enter
- **THEN** the label is undone and the focus is back on row 3
Pinned by: `tests/unit/test_a11y_node.py::test_toast_button_returns_the_focus`

#### Scenario: Three toasts and the focused row
- **WHEN** three toasts are open and the focus moves down the Labelling queue to a row near the bottom of the window
- **THEN** the page holds the toast box's height in `--toast-h`, the row is scrolled with `block: "nearest"`, and the rows' rule reads `scroll-margin-bottom: var(--toast-h)`
- **AND** the keyboard-only persona at 1280×800 sees no focused row under a toast
Pinned by: `tests/unit/test_a11y_node.py::test_focused_row_keeps_clear_of_toasts`, `tests/unit/test_a11y_static.py::test_rows_keep_a_margin_for_toasts`; ui-tester keyboard-only persona (task 10.2)

### Requirement: Tab strips are tab lists
The system SHALL draw the Concepts and synonyms artifact tabs as a tab list: each tab has `role="tab"` and `aria-selected`, the panel has `role="tabpanel"`, and Left/Right move to the next tab and open it.
Surfaces: UI Concepts and synonyms page, artifact tabs; CLI `wider construct show KIND`.

#### Scenario: Arrow keys on the artifact tabs
- **WHEN** the focus is on the "cells" tab and the researcher presses Right
- **THEN** the next tab is focused and open, has `aria-selected="true"`, and every other tab `aria-selected="false"`
Pinned by: `tests/unit/test_a11y_node.py::test_artifact_tabs_are_a_tablist`

### Requirement: Every control has a name
The system SHALL give every input, select, textarea and button on a page a name for screen readers (visible label, `aria-label` or text), and a button repeated per row SHALL name its row ("Remove seed paper B001"; a model card's button names its model). Every select on the Recall page has a visible caption.
Surfaces: UI every page, static and script-drawn controls; CLI none (names only).

#### Scenario: Static and drawn controls
- **WHEN** `index.html` is read with Python's HTML parser and the Setup page and model cards are drawn by their scripts on the stand-in page with two seed papers and two synthetic model cards
- **THEN** no control lacks a name, the Remove buttons read "Remove seed paper B001" and "Remove seed paper B002", each model card button names its card's model, and each Recall page select has a visible `<label>`
Pinned by: `tests/unit/test_a11y_static.py::test_every_static_control_has_a_name`, `tests/unit/test_a11y_node.py::test_drawn_controls_name_their_row`

### Requirement: Charts are named pictures
The system SHALL name every chart by what it shows: the coverage plot and PRISMA diagram as `role="img"`, the dashboard chart as a named group (its band marks are focusable). Axis charts SHALL title both axes, and two charts on one page SHALL share no id.
Surfaces: UI Results dashboard (drawn on the page), Search strings dry-run card, Recall page; CLI `wider query dry-run` (coverage SVG), `wider export prisma` (PRISMA SVG), `wider results` (the chart's points as a table).

#### Scenario: Dashboard chart, coverage plot and PRISMA diagram
- **WHEN** the dashboard chart is drawn for a synthetic result set, `wider query dry-run` writes the coverage plot and `wider export prisma` the PRISMA diagram for a synthetic project
- **THEN** the coverage plot ("Dry run coverage per database, synonyms v1") and the PRISMA diagram (a name starting "PRISMA 2020 flow diagram") have `role="img"` and their name, and the dashboard chart, under the heading "Relevant found along the ranking" that `wider results` prints above the same points, is a named group with an x-axis title and a y-axis title holding "estimated relevant records"
- **AND** two coverage plots drawn into one page share no id, and `wider results` prints the chart's points as a table
Pinned by: `tests/unit/test_results_js_node.py::test_chart_is_a_named_picture_with_two_axis_titles`, `tests/unit/test_a11y_static.py::test_prisma_and_coverage_svgs_are_named_with_unique_ids`

### Requirement: One time format on the pages
The system SHALL show every date and time on a page through one formatter (local date and time without seconds, as History shows it today), never a raw ISO text or one cut to 16 characters. The command line keeps ISO times.
Surfaces: UI every page that shows a time; CLI unchanged (ISO 8601 in text and `--json`).

#### Scenario: The same time everywhere
- **WHEN** the Results dashboard, Rounds, model cards, backends, Setup, Search results and the Recall outputs archive are drawn on the stand-in page with the time "2026-10-25T07:05:00+00:00"
- **THEN** each shows the formatter's text for that time and none shows "2026-10-25T07:05" or "+00:00"
- **AND** no message `scripts/list_messages.py` collects from `wider/ui/static` carries its `timestamp` flag, which also flags a time value named `at` and a time cut with `slice(0, 10)` or `slice(0, 16)`
Pinned by: `tests/unit/test_a11y_node.py::test_one_time_format_on_every_page`, `tests/unit/test_list_messages.py::test_ui_shows_no_raw_timestamp`

### Requirement: The pages work at phone width
The system SHALL put the sidebar behind a "Menu" button (with `aria-expanded`) below 640 px, give the content the full width, and let no page scroll sideways at 375 px; a wide table scrolls inside its card and a long unbroken title wraps inside its box.
Surfaces: UI every page; CLI none (layout).

#### Scenario: A walk at 375 px
- **WHEN** the keyboard-only persona opens every page of the demo project at 375×812 and opens the menu with Enter
- **THEN** no page is wider than the window, the menu button says whether it is open, and a 300-character unbroken title stays inside its Home card and its agreement-table row
- **AND** `style.css` gives the Home card heading and the agreement table `overflow-wrap: anywhere` inside its 640 px rule
Pinned by: ui-tester keyboard-only persona (task 10.2); `tests/unit/test_a11y_node.py::test_menu_button_below_640_px`, `tests/unit/test_a11y_static.py::test_long_titles_wrap_at_phone_width`

### Requirement: Enter in a field does what its button does
The system SHALL make Enter in a field act as its own button: in the Screening record limit it updates the estimate and moves the focus to the run button, so a second Enter starts the run; in a protocol setting field it equals Set. On Concepts and synonyms, Save comes after the editor in Tab order.
Surfaces: UI Screening page, Setup protocol fields, Concepts and synonyms editor; CLI `wider screen run --limit N`, `wider protocol set PATH VALUE`, `wider construct save`.

#### Scenario: Enter in the limit and a protocol field
- **WHEN** the researcher types 20 in the Screening record limit and presses Enter, presses Enter again, then types 0.4 in a protocol share field and presses Enter
- **THEN** the first Enter updated the estimate for 20 records, sent no run and moved the focus to the run button; the second asked for one run with limit 20
- **AND** the setting was set once and its Set button holds the focus, and the Construct editor comes before Save in the page's order
Pinned by: `tests/unit/test_a11y_node.py::test_enter_in_limit_and_protocol_fields`, `tests/unit/test_a11y_static.py::test_construct_editor_before_save`

### Requirement: Script-drawn message areas and refusals are read once
The system SHALL give every message area a script draws (comparison areas, lookups, backend check) `role="status"`, and show every refusal of saving or approving an artifact in the error style once, not in a muted status line.
Surfaces: UI Screening comparison, Settings lookups and backend check, Concepts and synonyms; CLI each command prints its message (unchanged).

#### Scenario: Drawn areas and an artifact refusal
- **WHEN** the comparison card, the lookups card and the backend check are drawn, and approving an artifact is refused
- **THEN** each drawn message area has `role="status"`, and the refusal is one `role="alert"` text in the error style
Pinned by: `tests/unit/test_a11y_node.py::test_drawn_message_areas_and_artifact_refusal`
