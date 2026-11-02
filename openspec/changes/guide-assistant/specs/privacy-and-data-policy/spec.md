## ADDED Requirements

### Requirement: Consent before the assistant sends
The system MUST NOT send an assistant question to a chat or embedding backend whose place is cloud, network or unknown until the researcher allows that backend for the operation `assistant`, apart from other operations. "Remember my choice", ticked by default, keeps the consent until revoked; unticked, it allows this one question only.
Surfaces: UI consent dialog, model cards; CLI `wider providers|embed allow|revoke NAME --for assistant`, `wider assistant ask --allow-once`.

#### Scenario: A cloud backend asks first, once
- **WHEN** construct backend `a` is a remote service with `research` consent only, and a question is asked without a terminal and through `POST /api/assistant/ask`
- **THEN** the CLI exits with code 2, the route answers 403 `backend_consent_required`, the Ask WIDER tab shows the consent dialog naming what is sent with "Remember my choice for Ask WIDER" ticked, and nothing is sent
- **AND** after "Allow" with the box ticked, or `wider providers allow a --for assistant`, the question is answered and later questions to `a` ask nothing
Pinned by: `tests/unit/test_backend_consent.py::test_assistant_needs_own_consent`, `tests/unit/test_assistant_js_node.py::test_consent_dialog_remember_ticked`

#### Scenario: Allowed for one question only
- **WHEN** the researcher unticks "Remember my choice for Ask WIDER" and presses "Allow", or runs `wider assistant ask "..." --allow-once` without a terminal
- **THEN** that one question is answered, no consent is stored, and the next question asks again
- **AND** in a terminal, `wider assistant ask` asks whether to allow and then "Remember this choice? [Y/n]"
- **AND** a one-question allowance is refused when the backend's address or place changed after the dialog was shown
- **AND** after consent with Remember was revoked on Settings, a cloud CLI backend's 403 goes through the page's own dialog (Remember unticked, Allow) and the header it builds: the turn is stored and no consent is
- **AND** a dialog closed any other way stores nothing and sends nothing, and the question gets the refusal instead of waiting
Pinned by: `tests/unit/test_assistant_routes.py::test_consent_once_not_stored`, `tests/unit/test_cli_assistant.py::test_cli_allow_once`, `tests/unit/test_assistant_routes.py::test_consent_once_refused_after_backend_change`, `tests/unit/test_assistant_routes.py::test_consent_once_through_the_dialog_after_revoke`, `tests/unit/test_assistant_js_node.py::test_consent_dialog_closed_otherwise_sends_nothing`

#### Scenario: Revoking leaves the rest working
- **WHEN** `wider providers revoke a --for assistant` runs
- **THEN** the next question is refused as needing consent, and screening and query creation on `a` keep their own consent
Pinned by: `tests/unit/test_backend_consent.py::test_revoke_assistant_keeps_other_operations`

### Requirement: What the assistant sends, beside its button
The system SHALL show beside "Ask" and print before `wider assistant ask` the sentences of the shared send-notice builder for the actions `assistant` (chat) and, when the project has an embedding backend, `assistant_embed`: what is sent, where it goes in the words of the model cards, and whether consent is needed or was given.
Surfaces: UI sentences beside "Ask"; CLI `wider assistant ask`; `GET /api/send-notice/assistant`, `GET /api/send-notice/assistant_embed`.

#### Scenario: The chat sentence for each place
- **WHEN** the notice is built for a checked loopback Ollama backend, a private network address, a remote service, an unknown place and the fake backend
- **THEN** each says WIDER sends "your question, this page's guide, matching sections of WIDER's documents, your project's settings for those topics, the project's status (no record) and your earlier questions"
- **AND** each names its place as the model cards do, with "no consent is needed for this place" for the first and last
Pinned by: `tests/unit/test_send_notice.py::test_send_notice_assistant_per_place`

#### Scenario: The embedding sentence
- **WHEN** the project's default embedding backend is a remote service
- **THEN** a second sentence says WIDER sends "your question, and WIDER's own documents to build the index, again after they change; no project data" to that service, and that consent is needed
Pinned by: `tests/unit/test_send_notice.py::test_send_notice_assistant_embed`

### Requirement: The assistant's conversation stays in the project folder
The system SHALL store each reviewer's conversation only in one file per reviewer in `assistant/` and the search index only in `assistant/index/`, readable and writable by the owner only, and leave `assistant/` out of share bundles, review packages and replication packages.
Surfaces: project folder; CLI `wider export share`, `wider export package`; UI the same exports.

#### Scenario: Not in any package
- **WHEN** a question was answered, the index was built, and a share bundle and a replication package are built
- **THEN** neither holds an `assistant/` file, the question or the answer
- **AND** the conversation file has mode 0600 on macOS and Linux
Pinned by: `tests/unit/test_assistant_store.py::test_assistant_not_in_packages`, `tests/unit/test_assistant_store.py::test_conversation_file_mode`

### Requirement: No text of the assistant in logs
The system SHALL keep the text of questions, answers, context and drafts only in the conversation file: the usage log, the background-run table, the server log, error messages and History hold none of it, and no raw response is written elsewhere.
Surfaces: usage log; CLI `wider costs`; server log; `store.sqlite` runs.

#### Scenario: A failed call
- **WHEN** the fake backend fails on a question containing the word "synthetic-secret-question"
- **THEN** the usage log, the background-run row, the server's log output and the error shown hold no "synthetic-secret-question"
Pinned by: `tests/unit/test_assistant_engine.py::test_failed_call_leaks_no_text`

#### Scenario: An answered question in the usage log
- **WHEN** a question is answered
- **THEN** the usage log gains one row with stage `assistant`, the backend and the tokens, and `wider costs` counts it under that stage
Pinned by: `tests/unit/test_assistant_engine.py::test_usage_row_without_text`

## MODIFIED Requirements

### Requirement: The project folder is the only data store
The system SHALL keep all review data inside the project folder: `project.yaml`, `benchmark.csv`, `inputs/`, `prior_reviews/`, `store.sqlite`, `artifacts/`, `embeddings/`, `runs/`, `exports/` and `assistant/`. Added files are copied there and parsed locally. The only file written outside the project, the recent-projects list, MUST hold only folder paths and opening times.
Surfaces: no command of its own (every command); UI Settings page, "This computer" card.

#### Scenario: Recent list holds paths and times only
- **WHEN** two projects are opened and one is opened again
- **THEN** each entry of `$WIDER_HOME/recent_projects.json` (default `~/.wider`) has exactly the keys `path` and `opened_at`
- **AND** without a file path (tests) the list is kept in memory and nothing is written
Pinned by: `tests/unit/test_shell.py::test_recent_projects_file_and_memory`

### Requirement: Project .gitignore keeps raw data out of version control
The system SHALL write a `.gitignore` into every new project that excludes `inputs/`, `prior_reviews/`, `store.sqlite*`, `embeddings/`, `runs/`, `exports/`, `assistant/` and `.env`, and leave an existing one unchanged except for adding a missing `assistant/` line at the first question. `prior_reviews/` gets its own `.gitignore` (`*`, `!.gitignore`).
Surfaces: CLI `wider init`, `wider prior-reviews add`, `wider assistant ask`; UI Home page "Create project", Setup page step 3, "Ask".

#### Scenario: New project and first prior review
- **WHEN** a project is created and a prior review is added
- **THEN** the project has a `.gitignore`, and `prior_reviews/.gitignore` starts with `*`
- **AND** a project under version control carries only configuration, seed papers and artifacts
Pinned by: `tests/unit/test_project.py::test_init_creates_layout_and_config`, `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

#### Scenario: A project made before the assistant
- **WHEN** a project whose `.gitignore` lacks `assistant/` gets its first question, and another project has no `.gitignore`
- **THEN** the first `.gitignore` gains the line `assistant/` with its other lines unchanged, and no `.gitignore` is created in the second
Pinned by: `tests/unit/test_assistant_store.py::test_gitignore_has_assistant_new_and_old_project`
