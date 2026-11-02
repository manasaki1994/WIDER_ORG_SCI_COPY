## MODIFIED Requirements

### Requirement: Guide panel on a page
The system SHALL show on every project page a guide panel on the right with the page's guide, open at 1180 px or wider and closed below until the researcher toggles it (kept as `wider.guide`) with the top bar's "Guide" button, which shows its state; Esc in the panel closes it, focus back on "Guide". It has tabs Guide and Ask WIDER and hides while a drawer is open. Privacy and blinding sentences stay on the page.
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
- **THEN** the guide panel first hides while the drawer shows, then the drawer closes and the guide shows with the focus in it
Pinned by: `tests/unit/test_shell_js_node.py::test_drawer_lies_over_the_guide`, `tests/unit/test_shell_js_node.py::test_panel_and_drawer_one_at_a_time`

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

## ADDED Requirements

### Requirement: Show a button the guide names
The system SHALL put a "Show" link beside each guide answer that names a control in `**bold**`. It scrolls to the visible control with that name, else a heading or label with it, outlines it, focuses it if it can take focus, and never presses it; a name on another page opens that page first. The guide's JSON lists the names per answer.
Surfaces: UI guide panel "Show"; CLI `wider guide <page> --json` (`buttons`); `GET /api/guide/<page>`.

#### Scenario: Show a button on this page
- **WHEN** the Deduplication guide is open and the researcher presses "Show" beside the answer that names "Run deduplication"
- **THEN** the "Run deduplication" button is scrolled into view, outlined and focused, and no deduplication run starts
- **AND** `wider guide dedup --json` lists "Run deduplication" in that answer's `buttons`
Pinned by: `tests/unit/test_guide_show_js_node.py::test_guide_show_focuses_never_clicks`, `tests/unit/test_guide.py::test_guide_buttons_listed`

#### Scenario: A name that is a heading, not a button
- **WHEN** "Show" is pressed for a name that is a section heading or a field label on the page
- **THEN** that heading or label is scrolled into view and outlined, and the focus stays on the link
Pinned by: `tests/unit/test_guide_show_js_node.py::test_guide_show_heading_outlined_not_focused`

#### Scenario: A button that is not on the page now
- **WHEN** "Show" is pressed for "Run deduplication" while the page hides it in its current state
- **THEN** the panel says "Run deduplication is not on the page now." in normal black text, and the focus stays on the link
Pinned by: `tests/unit/test_guide_show_js_node.py::test_guide_show_missing_button`

#### Scenario: Every named control exists
- **WHEN** the bold names of every shipped guide page are read
- **THEN** each is the text or accessible name of a button, link, heading, legend or label in that page's HTML or script, or is listed in the guide's short list of names that are not shown, and only names on the page get a "Show" link
Pinned by: `tests/unit/test_guide.py::test_guide_button_names_exist`
