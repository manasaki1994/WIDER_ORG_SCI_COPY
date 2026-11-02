## MODIFIED Requirements

### Requirement: Results dashboard in the sidebar
The system SHALL show a fixed link "Results dashboard" (address `#results`) in sidebar section 4, right after "Recall and report", with an icon and no state mark, like the model page in section 1. It is a page, not a stage: it is not numbered, not in `stages.yaml` and has no state of its own. The breadcrumb and page heading read "Results dashboard".
Surfaces: UI sidebar on every page inside a project; CLI `wider results` (the page's content), `wider stages` (unchanged).

#### Scenario: Link after Recall and report
- **WHEN** the sidebar is drawn with the ten available stages, the first four done
- **THEN** section 4 lists "Screening", "Screening review", "Recall and report", "Results dashboard" and then "PRISMA reporting", and the Results dashboard link goes to `#results`, has an icon, no state words and no "n. " in its tooltip
- **AND** the sidebar footer is unchanged by the link, `wider stages` lists the same ten stages as before, and opening `#results` shows the heading and breadcrumb "Results dashboard" with the link marked `aria-current="page"`
Pinned by: `tests/unit/test_shell_js_node.py::test_results_link_after_recall`, `tests/unit/test_results_js_node.py::test_results_hash_opens_page`, `tests/unit/test_cli.py::test_stages`

#### Scenario: Collapsed sidebar
- **WHEN** the sidebar is collapsed to the icon rail
- **THEN** the Results dashboard link keeps its accessible name "Results dashboard"
Pinned by: `tests/unit/test_shell_js_node.py::test_results_link_after_recall`

### Requirement: One map of page names and commands
The system SHALL keep `docs/names.md`: one row per page (page name, address, stage key or internal id, commands, guide section, spec capability) and the one glossary of the words the pages use. A test SHALL check it against `stages.yaml`, the sidebar's fixed pages and the guide headings, so a renamed page fails until the map follows. Help's "Page names and commands" card shows the same page and command pairs.
Surfaces: docs `docs/names.md`; UI Help page card; CLI `wider stages`.

#### Scenario: The map matches the pages
- **WHEN** `docs/names.md` is compared with `stages.yaml`, the list of fixed sidebar pages in `shell.js` and the guide headings
- **THEN** every available stage and every sidebar page (Home, Overview, Questions, Notes and tags, History, Help, Settings, Model for query creation, Screening review, Results dashboard, PRISMA reporting) has one row with the sidebar's name, its `#` address and its commands, and each guide section it names exists
- **AND** with "Ranking" renamed "Rank order" in a copy of `stages.yaml`, the check reports "docs/names.md: no row for Rank order"
Pinned by: `tests/unit/test_names.py::test_names_map_matches_pages_commands_and_guide`, `tests/unit/test_names.py::test_names_map_reports_a_renamed_page`

## ADDED Requirements

### Requirement: PRISMA reporting in the sidebar
The system SHALL show a fixed link "PRISMA reporting" (`#reporting`) last in sidebar section 4, after "Results dashboard", with an icon and no state mark. It is a page, not a stage. Its top bar shows the breadcrumb, "Guide" and the main button "Export checklists".
Surfaces: UI sidebar and top bar; CLI `wider report prisma status` (the content), `wider report prisma export` (the main button).

#### Scenario: Link after the Results dashboard
- **WHEN** the sidebar is drawn with the ten available stages, the first four done
- **THEN** the last link of section 4 is "PRISMA reporting", going to `#reporting`, with an icon, no state words and no "n. " in its tooltip, and the sidebar footer is unchanged by the link
- **AND** opening `#reporting` shows the heading "PRISMA reporting", the top bar's breadcrumb "<project> / PRISMA reporting", "Guide" and the main button "Export checklists", no "Step N of M", and the link marked `aria-current="page"`; in the collapsed sidebar the link keeps its accessible name
Pinned by: `tests/unit/test_shell_js_node.py::test_reporting_link_after_results`, `tests/unit/test_reporting_js_node.py::test_reporting_hash_opens_page`
