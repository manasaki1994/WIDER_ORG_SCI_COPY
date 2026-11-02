## MODIFIED Requirements

### Requirement: The project folder is the only data store
The system SHALL keep all review data inside the project folder: `project.yaml`, `benchmark.csv`, `inputs/`, `prior_reviews/`, `store.sqlite`, `artifacts/`, `embeddings/`, `runs/`, `exports/` and `reporting/`. Added files are copied there and parsed locally. The only file written outside the project, the recent-projects list, MUST hold only folder paths and opening times.
Surfaces: no command of its own (every command); UI Settings page, "This computer" card.

#### Scenario: Recent list holds paths and times only
- **WHEN** two projects are opened and one is opened again
- **THEN** each entry of `$WIDER_HOME/recent_projects.json` (default `~/.wider`) has exactly the keys `path` and `opened_at`
- **AND** without a file path (tests) the list is kept in memory and nothing is written
Pinned by: `tests/unit/test_shell.py::test_recent_projects_file_and_memory`

### Requirement: Project .gitignore keeps raw data out of version control
The system SHALL write a `.gitignore` into every new project that excludes `inputs/`, `prior_reviews/`, `store.sqlite*`, `embeddings/`, `runs/`, `exports/`, `reporting/` and `.env`, and leave an existing one unchanged except for adding a missing `reporting/` line at the first reporting save. `prior_reviews/` gets its own `.gitignore` (`*`, `!.gitignore`).
Surfaces: CLI `wider init`, `wider prior-reviews add`, `wider report prisma`; UI Home "Create project", Setup step 3, PRISMA reporting.

#### Scenario: New project and first prior review
- **WHEN** a project is created and a prior review is added
- **THEN** the project has a `.gitignore`, and `prior_reviews/.gitignore` starts with `*`
- **AND** a project under version control carries only configuration, seed papers and artifacts
Pinned by: `tests/unit/test_project.py::test_init_creates_layout_and_config`, `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

#### Scenario: Existing project at the first reporting save
- **WHEN** a project whose `.gitignore` lacks `reporting/` gets its first reporting save, and another project has no `.gitignore`
- **THEN** the first `.gitignore` gains the line `reporting/` with its other lines unchanged, a new project's `.gitignore` already lists it, and no `.gitignore` is created in the second
Pinned by: `tests/unit/test_reporting.py::test_reporting_folder_gitignored`
