# step-guide Specification

## Purpose
The guide panel tells the researcher, on the page they are on, what the step is for, what to do now, what "done"
means and what to watch out for, and says something specific when the project is in a known state. The text is
written for WIDER and shipped with it; no model and no online service is used. `wider guide <page>` prints the same
text.

## Requirements

### Requirement: Guide panel on a page
The system SHALL show on every project page a guide panel on the right with the page's guide, open at 1180 px or wider and closed below until the researcher toggles it (kept as `wider.guide`) with the top bar's "Guide" button, which shows its state; Esc in the panel closes it, focus back on "Guide". A record drawer lies over the panel; "Guide" closes it. Privacy and blinding sentences stay on the page.
Surfaces: UI project pages; CLI `wider guide <page>`; `GET /api/guide/<page>`.

#### Scenario: Closed by default
- **WHEN** the Deduplication page opens in a window 900 px wide with no stored choice
- **THEN** the "Guide" button has `aria-expanded` false and the guide panel is hidden
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_guide_panel`

#### Scenario: Open and close
- **WHEN** the researcher presses "Guide" and then the close control of the panel
- **THEN** the panel first shows the Deduplication guide with `aria-expanded` true and the focus in the panel, then hides with `aria-expanded` false and the focus on the "Guide" button
- **AND** pressing Esc inside the open panel closes it the same way
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_guide_panel`

#### Scenario: The drawer lies over the guide
- **WHEN** at 1440 px with the guide open the researcher opens a record's drawer over the page, then presses "Guide"
- **THEN** the drawer first covers the guide panel and the main area keeps its width, then the drawer closes and the guide shows with the focus in it
Pinned by: `tests/unit/test_shell_js_node.py::test_drawer_lies_over_the_guide`

#### Scenario: A page without guide text
- **WHEN** the Ranking page opens
- **THEN** it has a guide like every other page: no page inside a project is left without one
Pinned by: `tests/unit/test_guide.py::test_every_page_has_a_guide`

#### Scenario: Open on a wide window
- **WHEN** the Deduplication page opens in a window 1440 px wide with no stored choice
- **THEN** the guide panel is open on the right with the Deduplication guide, and the "Guide" button reads `aria-expanded` true
Pinned by: `tests/unit/test_shell_js_node.py::test_guide_panel_default_and_choice`

#### Scenario: Closed on a narrow window, choice kept
- **WHEN** the Ranking page opens 900 px wide, the researcher shows the panel, then opens another page
- **THEN** the panel is first closed, then open with the Ranking guide, and stays open on the next page
- **AND** pressing Esc in the open panel closes it and puts the focus on the "Guide" button
Pinned by: `tests/unit/test_shell_js_node.py::test_guide_panel_default_and_choice`

### Requirement: Guide notices for the project's state
The system SHALL list, under a page's "What you can do here now" sentence, the notice of each named state the project is in, with its own numbers; the guide panel keeps its general text. For Deduplication: pairs waiting for a decision, files imported since the last run, and a last run with no duplicates. With no named state, the box shows only its sentence.
Surfaces: UI "What you can do here now" box; CLI `wider guide dedup`; `GET /api/guide/dedup`.

#### Scenario: Pairs waiting
- **WHEN** the last run queued 12 pairs and none is decided yet
- **THEN** the page's "What you can do here now" box and `wider guide dedup` say "12 pairs wait for your decision. Decide them, then press Apply decisions and check again."
- **AND** the guide panel shows the six answers, with no notice and no switch between them
Pinned by: `tests/unit/test_guide.py::test_pairs_waiting`, `tests/unit/test_shell_js_node.py::test_guide_panel_default_and_choice`

#### Scenario: New files since the run
- **WHEN** two synthetic files were imported after the last deduplication run
- **THEN** the box and `wider guide dedup` say "You added 2 files since the last run. Press Apply decisions and check again."
Pinned by: `tests/unit/test_guide.py::test_new_files_since_the_run`

#### Scenario: No duplicates found
- **WHEN** the last run merged no records and queued no pair
- **THEN** the box and `wider guide dedup` say "No duplicates were found. Check that every database's export is uploaded."
Pinned by: `tests/unit/test_guide.py::test_no_duplicates_found`

#### Scenario: No state applies
- **WHEN** deduplication has run, no pair waits and no file came after the run
- **THEN** the box shows only its sentence and the guide its six answers
Pinned by: `tests/unit/test_guide.py::test_no_state_applies`

### Requirement: Guide text in six answers
The system SHALL give every page inside a project a guide answering, in order, "Where you are", "What to do", "Why it matters", "What you need", "Done when", "What comes next", and "Watch out" where written. The first and sixth come from the registry and project state, the rest from shipped Markdown in the page's button words. Panel, API and CLI agree; it shows only stage and page names and counts.
Surfaces: UI guide panel; CLI `wider guide <page> [--json]`; `GET /api/guide/<page>`.

#### Scenario: Same text on both surfaces
- **WHEN** `wider guide enrich` runs on the synthetic project and the Missing abstracts guide panel is opened
- **THEN** both show the six headings in this order and the same sentences under each, and "Where you are" names section 2 and the step
Pinned by: `tests/unit/test_guide.py::test_six_answers_same_on_both_surfaces`

#### Scenario: Every page has a guide
- **WHEN** `wider guide <page>` runs for each page of the first table of `docs/names.md` except Home
- **THEN** each prints the six headings and none says that no guide is written
Pinned by: `tests/unit/test_guide.py::test_every_page_has_a_guide`

#### Scenario: Button names and no record data
- **WHEN** `wider guide dedup` runs, and `wider guide sample --json` runs while the development sample has open labels
- **THEN** the dedup steps under "What to do" name "Run deduplication", "Duplicates, keep A", "Duplicates, keep B", "Not duplicates" and "Unsure" as the page shows them
- **AND** the sample guide holds no record title, label, model decision or sample membership of any record, only counts and step names, and no count of development labels ("development labels open" only)
Pinned by: `tests/unit/test_guide.py::test_button_names_and_no_record_data`

#### Scenario: A page outside the stages
- **WHEN** `wider guide history` runs on the synthetic project whose next stage is Missing abstracts
- **THEN** "Where you are" says History is a project page outside the stages, and "What comes next" says "Back to Missing abstracts"
Pinned by: `tests/unit/test_guide.py::test_page_outside_the_stages`

#### Scenario: Unknown page
- **WHEN** `wider guide nosuchpage` runs
- **THEN** it says there is no such page and exits with an error code, and `GET /api/guide/nosuchpage` answers 404
Pinned by: `tests/unit/test_guide.py::test_unknown_page`
