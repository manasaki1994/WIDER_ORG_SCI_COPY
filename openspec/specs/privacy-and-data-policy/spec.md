# privacy-and-data-policy Specification

## Purpose
WIDER handles review data the researcher may not share: database exports with abstracts, prior-review files, model responses and labels. This capability fixes what stays on the researcher's computer, what may leave it and only through which explicit commands or switches, what a shareable bundle may contain, and how the repository is kept free of review data and of any one review's topic. It applies across every stage of the procedure, from `setup` to `recall`.

## Requirements

### Requirement: The project folder is the only data store
The system SHALL keep all review data inside the project folder: `project.yaml`, `benchmark.csv`, `inputs/`, `prior_reviews/`, `store.sqlite`, `artifacts/`, `embeddings/`, `runs/` and `exports/`. Added files are copied there and parsed locally. The only file written outside the project, the recent-projects list, MUST hold only folder paths and opening times.
Surfaces: no command of its own (every command); UI Settings page, "This computer" card.

#### Scenario: Recent list holds paths and times only
- **WHEN** two projects are opened and one is opened again
- **THEN** each entry of `$WIDER_HOME/recent_projects.json` (default `~/.wider`) has exactly the keys `path` and `opened_at`
- **AND** without a file path (tests) the list is kept in memory and nothing is written
Pinned by: `tests/unit/test_shell.py::test_recent_projects_file_and_memory`

### Requirement: Project .gitignore keeps raw data out of version control
The system SHALL write a `.gitignore` into every new project that excludes `inputs/`, `prior_reviews/`, `store.sqlite*`, `embeddings/`, `runs/`, `exports/` and `.env`, and leave an existing one unchanged. `prior_reviews/` gets its own `.gitignore` (`*`, `!.gitignore`).
Surfaces: CLI `wider init`, `wider prior-reviews add`; UI Home page "Create project", Setup page step 3.

#### Scenario: New project and first prior review
- **WHEN** a project is created and a prior review is added
- **THEN** the project has a `.gitignore`, and `prior_reviews/.gitignore` starts with `*`
- **AND** a project under version control carries only configuration, seed papers and artifacts
Pinned by: `tests/unit/test_project.py::test_init_creates_layout_and_config`, `tests/unit/test_prior_reviews.py::test_add_list_remove_and_gitignore`

### Requirement: Keys only by environment variable name
The system MUST NOT write an API key value to `project.yaml`, a run spec, a listing, an API response or a backend identity. A backend names its key's environment variable (`api_key_env`), which MUST be a variable name, not key-like (a known key prefix, or 20+ characters mixing upper case, lower case and digits); the key is read only at call time, and listings and detection say only whether it is set.
Surfaces: CLI `wider providers list|add|edit`; UI backend cards and form.

#### Scenario: Key set in the environment
- **WHEN** a backend's key variable holds a secret value
- **THEN** neither `project.yaml`, nor the backend row, nor the backend identity contains that value
Pinned by: `tests/unit/test_backends.py::test_provider_rows_never_expose_key_values`, `tests/unit/test_providers.py::test_identity_has_no_secrets`

#### Scenario: Detection names keys only
- **WHEN** a key variable named by a preset and a CLI sign-in file both hold secret values, and `wider providers detect --json` runs
- **THEN** the output names the variable as set and the sign-in file as found
- **AND** neither secret value appears in the output or in the `GET /api/backends/detect` response
Pinned by: `tests/unit/test_detect.py::test_detect_reports_keys_and_sign_in_by_name_only`, `tests/unit/test_setup_routes.py::test_detect_route_matches_cli`

#### Scenario: A key given where its variable name belongs
- **WHEN** a backend or an embedding is added or edited with `--api-key-env sk-proj-SYNTHETIC…` or with a 32-character mixed-case value such as `Xq7Lm2Pz9Rt4Vw8Yb3Nc6Hd1Kf5Gj0Ts`
- **THEN** it is refused with "api_key_env looks like a key value, not the name of an environment variable", the message does not repeat the value, and `project.yaml` is unchanged
- **AND** `OPENAI_API_KEY`, `my_lab_key` and `AZURE_OPENAI_API_KEY_FOR_RESEARCH_LAB_2` are accepted
Pinned by: `tests/unit/test_backends.py::test_key_value_as_api_key_env_is_refused`

### Requirement: Key values kept out of outputs
The system SHALL require a sensitive `extra.headers` value or a key-like `extra.env` value to be an `{env:NAME}` reference, resolved only when the call is made. Key and header values never appear in run specs, share bundles, CSV exports, `llm_usage`, run histories or job logs.
Surfaces: CLI `wider providers add|edit`, `wider report --bundle`, `wider export package`; UI backend form.

#### Scenario: Secret values cannot be stored through extra
- **WHEN** a backend is added or edited with `extra.env` `{"SOME_API_KEY": "abc"}` or `{"MY_TOKEN": "abc"}`
- **THEN** it is refused with a message saying to store the value in an environment variable and write `{env:NAME}`, and `project.yaml` is unchanged
- **AND** `extra.env` `{"SOME_API_KEY": "{env:SOME_KEY_VAR}"}` and `{"SOME_PROMPT_FILE": "{system_file}"}` are accepted
Pinned by: `tests/unit/test_backends.py::test_extra_env_key_refused`

#### Scenario: Key values kept out of records and exports
- **WHEN** a backend's key variable `SOME_KEY_VAR` and a header variable `SOME_HEADER_VAR` hold synthetic secret values and a screening run, a share bundle, a package, the decisions CSV and a job log are produced
- **THEN** neither secret value appears in any of them, nor in `llm_usage`, and the run spec names `SOME_KEY_VAR` and `{env:SOME_HEADER_VAR}` only
Pinned by: `tests/unit/test_provenance.py::test_key_value_never_recorded`

### Requirement: The UI is served on 127.0.0.1 only
The system SHALL serve the web UI only on `127.0.0.1` (default port 8765), without API documentation pages and without granting cross-origin access. Project paths must be absolute folders on the same computer. The file download route MUST serve only files under `<project>/exports/`.
Surfaces: CLI `wider ui [--dir] [--port] [--open/--no-open]`; UI Settings page, "This computer" card.

#### Scenario: Download outside the exports folder
- **WHEN** the UI is asked for `project.yaml`, `../project.yaml`, `exports/../project.yaml`, `/etc/hosts` or `store.sqlite`
- **THEN** each request is answered with 404
Pinned by: `tests/unit/test_outputs.py::test_output_routes_and_download_guard`

### Requirement: Prior-review lookups and excerpts behind off-by-default switches
The system SHALL keep two per-project switches under `prior_reviews` in `project.yaml`, both off by default. `allow_openalex_lookup` lets prior-review DOIs, titles and ids go to OpenAlex. `allow_llm_excerpts` lets prior-review titles enter construct prompts. With the lookup switch off, resolve and chain MUST stop before any network request.
Surfaces: CLI `wider prior-reviews policy|resolve|chain`; UI Setup page step 3, data-policy switches.

#### Scenario: Lookup switch off
- **WHEN** a prior review is added and resolve or chain is called while the lookup switch is off
- **THEN** each call raises a policy error and no request leaves the machine
Pinned by: `tests/unit/test_prior_reviews.py::test_gate_blocks_before_any_network`

#### Scenario: Excerpt switch
- **WHEN** a construct proposal prompt is built with the excerpt switch off, then on
- **THEN** the first prompt contains no prior-review titles and the second contains them
- **AND** `wider prior-reviews policy --allow-lookup` turns on only the lookup switch
Pinned by: `tests/unit/test_prior_reviews.py::test_prompt_titles_only_with_excerpt_switch`, `tests/unit/test_prior_reviews.py::test_policy_round_trip`

### Requirement: Only explicit commands send data off the machine
The system SHALL send data off the machine only by: calls to the chosen LLM backend or embedding API; DOI and title lookups (lookup consent); a term or query for a count (`online_lookups.terms_allowed`); prior-review lookups (own switch); on request only: weight downloads, a backend probe, a cloud model list.

A `network`, `cloud` or `unknown` backend is contacted only with its consent. It MUST NOT send telemetry.
Surfaces: CLI `wider lookups`, `wider providers probe|consent`; UI backend cards.

#### Scenario: What an external call carries
- **WHEN** screening runs on a cloud backend or a lookup service is called
- **THEN** each screening call carries the prompt with one record's title and abstract, and lookups to OpenAlex, Crossref, Europe PMC and Semantic Scholar carry DOIs or titles, or search terms or a query under the term switch, with development seed DOIs only for the OpenAlex seed check
- **AND** lookups identify the tool with a fixed User-Agent and add a contact address only when `WIDER_MAILTO` is set, and the backend check uses fixed test sentences and no project data
Pinned by: `tests/unit/test_lookups.py::test_screening_call_carries_one_records_title_and_abstract`, `tests/unit/test_lookups.py::test_lookups_carry_dois_or_titles_and_a_fixed_user_agent`, `tests/unit/test_lookups.py::test_backend_check_sends_fixed_sentences_and_no_project_data`

#### Scenario: Identifier lookups wait for consent
- **WHEN** a new project runs `wider benchmark resolve`, `wider benchmark fetch` or `wider enrich` without an interactive terminal
- **THEN** each command exits with code 2 and no DOI or title leaves the computer
- **AND** after `wider lookups allow` the same commands send their lookups and each run is logged locally
Pinned by: `tests/unit/test_lookups.py::test_seed_resolve_and_fetch_in_a_script_exit_2`, `tests/unit/test_lookups.py::test_enrichment_without_consent_sends_and_changes_nothing`, `tests/unit/test_lookups.py::test_lookups_commands_allow_status_revoke`

#### Scenario: Terms wait for their own switch
- **WHEN** `wider query yield "alpha coupling"` runs with online lookups allowed but the term switch off
- **THEN** it exits with code 2 naming `wider lookups terms allow`, and the mock client saw no request
- **AND** after `wider lookups terms allow` the request carries the term and nothing else from the project, and is logged as `term_yield`
Pinned by: `tests/unit/test_search_provenance.py::test_term_lookups_need_their_own_switch`

### Requirement: Locality shown and detection kept local
The system SHALL find backends on this computer by contacting only 127.0.0.1, reporting sign-in files as found or not, never reading them. Each backend is shown with its locality (`cloud` as "remote", `unknown` as "unknown", never as local) and the destination its data goes to. Opening a page, `wider info` and `wider providers|embed list` make no request.
Surfaces: CLI `wider info`, `wider check-backends`, `wider providers list|detect`; UI locality chips, model page, pickers, Settings cards.

#### Scenario: Locality shown per backend
- **WHEN** the locality of an Ollama backend on 127.0.0.1 whose model the local `/api/tags` lists without remote fields, an Ollama backend on a remote host, an OpenAI endpoint, an Anthropic backend, a fake backend and an `openai_compat` backend at `http://192.168.1.20:8000/v1` is computed
- **THEN** they are `local`, `cloud`, `cloud`, `cloud`, `test` and `network`
- **AND** the UI shows `network` as "Local network" with the host `192.168.1.20`
- **AND** the UI shows `cloud` as "remote" with the destination: the remote Ollama host, `api.openai.com`, `api.anthropic.com`
Pinned by: `tests/unit/test_shell.py::test_locality_rules`, `tests/unit/test_shell.py::test_destinations`

#### Scenario: Detection stays on this computer
- **WHEN** `wider providers detect` runs, the model page opens, or the backend form opens for a cloud preset
- **THEN** no request goes to any host other than 127.0.0.1
- **AND** a cloud model list is requested only by `wider providers models` or the "Load models from <provider>" button
Pinned by: `tests/unit/test_detect.py::test_detect_contacts_only_loopback`, `tests/unit/test_backends_js_node.py::test_cloud_models_not_loaded_on_open`

#### Scenario: Consent of an unchecked Ollama backend on Settings
- **WHEN** an Ollama LLM backend holds `probe` and `research` consent recorded while its model was routed to the cloud, before last checks were kept, so it has no last check, and Settings opens
- **THEN** its consent line lists `probe` and `research` with "not verified, press Check" and no stale warning, and no request is made
Pinned by: `tests/unit/test_backend_consent.py::test_status_without_probe_keeps_consent_unverified`

#### Scenario: Unchecked Ollama backend without consent on Settings
- **WHEN** an Ollama LLM backend on 127.0.0.1 was never checked, holds no consent, and Settings opens
- **THEN** its consent line reads "unknown, press Check" and does not ask for consent, and no request is made
Pinned by: `tests/unit/test_backend_consent.py::test_status_without_probe_asks_for_check`

#### Scenario: Opening a page contacts nothing
- **WHEN** Home or the model, Concepts, Ranking, Screening or Settings page opens (its cost estimate included), or `wider providers list` or `wider info` runs, for a project with an Ollama backend that was never checked
- **THEN** no request goes to any host, 127.0.0.1 included, and the backend shows "unknown, press Check"
Pinned by: `tests/unit/test_backends.py::test_rows_make_no_request`, `tests/unit/test_shell.py::test_summary_makes_no_request`, `tests/unit/test_stage_pages.py::test_page_open_routes_make_no_request`, `tests/unit/test_setup_routes.py::test_page_estimate_makes_no_request`, `tests/unit/test_model_page_node.py::test_guided_steps_on_first_visit`

### Requirement: Model usage is logged without text
The system SHALL log tokens, cost and locality per model call in the store table `llm_usage` and MUST NOT store prompt or answer text there. Raw model responses are kept only in the project's `runs/` folder (`responses.jsonl`), which the project `.gitignore` excludes.
Surfaces: CLI `wider costs`; UI Home page project card and stage results.

#### Scenario: Prompt text not logged
- **WHEN** a call with the user text "one two three" is logged
- **THEN** no row of `llm_usage` contains that text
Pinned by: `tests/unit/test_runs.py::test_usage_logged_only_inside_a_scope`

### Requirement: Share bundle and package without abstracts
The system SHALL write a bundle of configuration, seed papers, artifacts, run specs, prompts, schemas, decisions with their models, records without abstracts, labels and the report, and MUST NOT hold abstracts, RIS exports, raw model responses, secrets, the mapping or an undeclared login or `$WIDER_REVIEWER` as a name. `project.yaml` keeps variable names, no values or home path. The package adds a SHA-256 manifest.
Surfaces: CLI `wider report --bundle`, `wider export package`; UI Recall page.

#### Scenario: Package checked for abstracts
- **WHEN** the replication package is written after reference exports exist
- **THEN** it holds `MANIFEST.txt` and `share_bundle/records.csv`, whose header ends in `abstract_words` and has no abstract column
- **AND** no reference export, no `.ris` file and no abstract from the store appears in any packaged file
Pinned by: `tests/unit/test_outputs.py::test_package_has_no_abstracts_and_a_manifest`

#### Scenario: Bundle contents
- **WHEN** the share bundle is written
- **THEN** `records.csv` has the columns record_id, title, year, journal, doi, platforms and abstract_words
- **AND** the bundle also holds `screen_decisions.csv`, `labels.csv`, `report.md`, `report.json` and the dedup, query and search-string exports

#### Scenario: No login name or mapping in the package
- **WHEN** `USER` and `WIDER_REVIEWER` hold synthetic values that signed changes but were never confirmed, and the package is written in each attribution mode
- **THEN** neither value and no list of researchers occurs in any packaged file outside bibliographic text (titles, journals, authors, search terms)
Pinned by: `tests/unit/test_attribution_export.py::test_login_name_is_never_written`, `tests/unit/test_attribution_export.py::test_no_mapping_in_shared_files`

### Requirement: Secret scan before a bundle or package is finished
The system SHALL scan every file of the share bundle and of the package for secrets, then for names the attribution setting hides, before it is finished; a finding refuses the output.
Surfaces: CLI `wider report --bundle`, `wider export package`; UI Recall page.

#### Scenario: Package refused on a secret
- **WHEN** a synthetic key value set in `SOME_KEY_VAR` has been typed into a record title and the package is written
- **THEN** `wider export package` exits with code 1 and `POST /api/outputs/package` answers 400, naming the file, the line and the kind of finding but not the value
- **AND** no partial package remains
Pinned by: `tests/unit/test_outputs.py::test_package_refused_on_secret`

#### Scenario: Package refused on a hidden name
- **WHEN** a hidden researcher name is left in `methods.md` and the package is written
- **THEN** `wider export package` exits with code 1 and `POST /api/outputs/package` answers 400, naming the file, the line and the pseudonym
Pinned by: `tests/unit/test_attribution_export.py::test_name_left_in_a_file_refuses`

### Requirement: Pre-commit guard refuses review data
The repository SHALL hold no review data. The guard `scripts/check_no_private_data.py` checks staged files (or given paths) and MUST refuse the commit with exit code 1 when a file is a bibliographic export, a store or array file, a CSV/TSV with an abstract column outside `tests/` and `examples/`, a JSONL with abstracts, or contains a private string.
Surfaces: CLI `python3 scripts/check_no_private_data.py [paths] [--install]` (developer tool); no UI.

#### Scenario: Data files and private strings
- **WHEN** the guard checks a RIS file, a CSV with an `abstract` column, a Markdown file with an absolute path under the author's home directory, and a plain Python file
- **THEN** the first three are flagged and the Python file passes
Pinned by: `tests/unit/test_privacy_guard.py::test_guard_flags_exports_and_strings`

#### Scenario: Forbidden file kinds
- **WHEN** a staged file has one of the suffixes `.ris`, `.bib`, `.nbib`, `.enw`, `.sqlite`, `.sqlite-wal`, `.sqlite-shm`, `.npy`, `.npz` or `.pdf`, or is named `responses.jsonl` or `store.sqlite`
- **THEN** the guard refuses it, and `--install` writes the guard as the git pre-commit hook

### Requirement: Topic vocabulary guard
The guard SHALL flag any text file in the repository that uses the topic vocabulary of the original case-study review or names that review. A line that legitimately needs such a word carries the marker `topic-ok`. Only the replication harness, the case-study proposal documents and the changelog are exempt.
Surfaces: CLI `python3 scripts/check_no_private_data.py` (developer tool); no UI.

#### Scenario: Topic vocabulary and the marker
- **WHEN** a file uses a topic word without the marker, another uses it with `topic-ok`, and a third uses only methodological words such as strict and lenient recall
- **THEN** only the first is flagged, naming the line, and the exempt case-study documents pass
Pinned by: `tests/unit/test_privacy_guard.py::test_guard_flags_topic_vocabulary`

### Requirement: Topic-neutral prompts and templates
The system SHALL ship construct and screening prompt templates with no topic vocabulary of any one review, rendering their examples from the project's own seed papers and artifacts. Rendered for a neutral fixture project (employee voice and silence), no template MUST produce a word that the topic guard flags.
Surfaces: no command of its own; applies to `wider construct propose` and `wider screen` prompts.

#### Scenario: Rendered prompts are neutral
- **WHEN** each construct template and the screening template are rendered for the employee voice and silence fixture
- **THEN** the guard finds no topic word in the rendered prompts or in the template sources
- **AND** the rendered prompts contain the fixture's own dimension names and cell keys
Pinned by: `tests/unit/test_topic_neutral.py::test_construct_templates_are_topic_neutral`, `tests/unit/test_topic_neutral.py::test_screening_template_is_topic_neutral`, `tests/unit/test_topic_neutral.py::test_template_sources_are_topic_neutral`, `tests/unit/test_topic_neutral.py::test_construct_templates_render_examples_from_the_project`

### Requirement: Consent before contacting a cloud backend
The system MUST NOT contact a backend whose locality is `cloud`, `network` or `unknown` for a model list, a probe or research data until that backend has consent for that operation. Consent is kept per backend and operation apart from lookup consent, renewed when the destination or what is sent changes, and revocable. Declining leaves everything else working.
Surfaces: CLI `wider providers consent|allow|revoke`, any command calling the backend; UI consent dialog, backend cards.

#### Scenario: First model list asks first
- **WHEN** `wider providers models a` runs without a terminal for cloud backend `a` that has no consent
- **THEN** it exits with code 2, names the destination and what would be sent, and no request leaves the computer
- **AND** `GET /api/providers/a/models` answers 403 with `backend_consent_required`, and the page shows the consent dialog
Pinned by: `tests/unit/test_backend_consent.py::test_models_need_consent`, `tests/unit/test_setup_routes.py::test_models_route_consent_403`

#### Scenario: Consent is remembered per operation
- **WHEN** `wider providers allow a --for models` has run and the model list is requested twice
- **THEN** both requests are sent without asking again, and `wider providers probe a` without a terminal still exits with code 2
Pinned by: `tests/unit/test_backend_consent.py::test_consent_per_operation`

#### Scenario: Research data waits for consent
- **WHEN** a screening run, a construct proposal or an adjacent propose or find is started without a terminal, or through `POST /api/screen/run`, `POST /api/jobs/screen`, `POST /api/jobs/propose/{kind}`, `POST /api/adjacent/propose` or `POST /api/adjacent/find`, on cloud backend `a` with `models` and `probe` consent only
- **THEN** each CLI command exits with code 2, each route answers 403, no job is created and no record text is sent
- **AND** after `wider providers allow a --for research` the same run starts
Pinned by: `tests/unit/test_backend_consent.py::test_research_needs_consent`, `tests/unit/test_backend_consent.py::test_research_route_consent_403`

#### Scenario: A changed destination asks again
- **WHEN** backend `a` has consent and its base URL is edited to another host
- **THEN** its next call is refused as needing consent, and the message names the old and the new host
Pinned by: `tests/unit/test_backend_consent.py::test_fingerprint_change_renews`

#### Scenario: Revoking and declining
- **WHEN** consent for `a` is revoked with `wider providers revoke a` or the card's "Revoke", or the dialog is cancelled
- **THEN** no call to `a` is sent, and lookups consent, local backends, labelling and exports are unchanged
- **AND** `GET /api/providers/consent` and `wider providers consent --json` list the same state
Pinned by: `tests/unit/test_backend_consent.py::test_revoke_and_decline`, `tests/unit/test_setup_routes.py::test_consent_route_matches_cli`

#### Scenario: Local backends need no consent
- **WHEN** an Ollama backend on 127.0.0.1 with a model listed locally, or the fake backend, is probed and screens
- **THEN** no consent is asked and `backend_consent` in `project.yaml` is unchanged
- **AND** `wider providers models` on a CLI backend lists its catalogue models without asking and without running the CLI
Pinned by: `tests/unit/test_backend_consent.py::test_local_needs_none`

#### Scenario: A backend on the local network asks first
- **WHEN** backend `n` at `http://192.168.1.20:8000/v1` is used for research without consent, then allowed, then edited to `http://192.168.1.21:8000/v1`
- **THEN** the first call is refused as needing consent; the CLI message and the dialog say "Local network", name `192.168.1.20`, say the data leaves this computer and warn that it would travel unencrypted (http)
- **AND** after the edit the next call asks again, naming both hosts
Pinned by: `tests/unit/test_backend_consent.py::test_network_needs_consent`

#### Scenario: Ollama not running
- **WHEN** an Ollama backend on 127.0.0.1 is probed while nothing answers on its port
- **THEN** the probe reports that 127.0.0.1:11434 cannot be reached, and no consent is asked
Pinned by: `tests/unit/test_backend_consent.py::test_unreachable_loopback_no_consent`

### Requirement: Consent before sending text to a cloud embedding service
The system MUST NOT send the synthetic probe or research text to a `cloud`, `network` or `unknown` embedding backend until it has consent for that operation. This consent is kept apart from LLM-backend consent, renewed when provider, endpoint, model or fields sent change, and revocable. Local models need none.
Surfaces: CLI `wider embed consent|allow|revoke`, `embed probe|corpus`, `rank run|validate`, `sample draw`; UI consent dialog, embedding cards.

#### Scenario: Corpus waits for consent
- **WHEN** `wider embed corpus --embedding e` runs without a terminal, or `POST /api/jobs/embed` is called, for cloud embedding backend `e` that has only `probe` consent
- **THEN** the command exits with code 2 and the route answers 403 with `backend_consent_required` and `kind` `embedding`, no job is created and no record text is sent
- **AND** after `wider embed allow e --for research` the same command embeds the corpus
Pinned by: `tests/unit/test_backend_consent.py::test_embed_research_needs_consent`

#### Scenario: Probe and research are separate
- **WHEN** `wider embed allow e --for probe` has run and the dialog names what each operation sends
- **THEN** `wider embed probe e` sends only the fixed synthetic sentences, and `wider rank run` with `e` still exits with code 2 without a terminal
- **AND** an LLM backend named `e` has no consent from this
Pinned by: `tests/unit/test_backend_consent.py::test_embed_probe_vs_research`

#### Scenario: Changes ask again, local needs none
- **WHEN** `e`'s base URL or model is changed after consent, and a sentence-transformers backend `st` embeds the corpus
- **THEN** `e`'s next call is refused as needing consent, naming what changed
- **AND** `st` asks nothing and `embedding_consent` has no entry for it
Pinned by: `tests/unit/test_backend_consent.py::test_embed_renew_and_local`

### Requirement: Sensitive headers only by environment reference
A header whose name contains key, token, auth, secret or cookie, or that is listed in `extra.sensitive_headers`, MUST take only an `{env:NAME}` value, resolved when the request is sent; any other key-like `extra` value is refused. A missing variable fails the call before anything is sent. Redirects are not followed, so credentials never reach another host.
Surfaces: CLI `wider providers add|edit --extra`; UI Advanced section of the backend form.

#### Scenario: Plain sensitive value refused
- **WHEN** a backend is added with header `X-Gateway-Key: abc`, or with `X-Org: abc` and `extra.sensitive_headers: [X-Org]`
- **THEN** it is refused (exit 1, HTTP 400) naming the header and the `{env:NAME}` form, and `project.yaml` is unchanged
- **AND** `X-Gateway-Key: {env:SOME_HEADER_VAR}` and `X-Title: Review A` are accepted
Pinned by: `tests/unit/test_backends.py::test_sensitive_header_needs_env`

#### Scenario: Key-like text in other extra values refused
- **WHEN** `wider providers add` or `edit` is given `--extra body.user=sk-test0000000000000000`, or a 40-letter unbroken value in `--extra deployment=…`
- **THEN** it is refused (exit 1, HTTP 400) with a message naming `extra.body.user` or `extra.deployment` and the `{env:NAME}` form, without echoing the value, and `project.yaml` is unchanged
- **AND** `--extra body.user={env:SOME_VAR}` and `--extra deployment=review-a` are accepted
Pinned by: `tests/unit/test_backends.py::test_extra_secret_values_refused`

#### Scenario: Resolved at call time, missing variable stops the call
- **WHEN** a call is made with `X-Gateway-Key: {env:SOME_HEADER_VAR}`, first with the variable set and then with it unset
- **THEN** the first request carries the variable's value, and the second fails with a message naming the header and `SOME_HEADER_VAR`, sending no request
Pinned by: `tests/unit/test_http_adapters.py::test_env_header_resolved_and_missing`

#### Scenario: Redirect not followed
- **WHEN** the endpoint answers a call with a redirect to another host
- **THEN** the call fails with a message naming that host, and no request is sent to it
Pinned by: `tests/unit/test_http_adapters.py::test_redirect_not_followed`

### Requirement: Seed PDFs never leave the computer
The system SHALL keep seed-paper PDFs and their extracted text in the project folder, exclude them from every bundle, package and export, and send at most one passage of the configured size per model call, only to a `local` or `test` backend or, under `seed_pdfs.passages_to_cloud`, to any other consented backend. The privacy guard MUST refuse a committed PDF.
Surfaces: CLI `wider seeds mine`, `wider report --bundle`, `wider export package`; UI Settings, "Seed papers" card.

#### Scenario: Nothing of the PDF in outputs or calls
- **WHEN** a seed PDF is mined on the fake backend and the share bundle and package are built
- **THEN** every recorded call input is at most the configured passage size, and the bundle and package hold no `text.txt`, `sections.json`, `references.json`, passage or quote from `runs/seeds/` (no 40-character run of `text.txt`); venue and candidate names in `streams.json` are allowed
- **AND** `scripts/check_no_private_data.py` fails on a staged `seeds/x.pdf`
Pinned by: `tests/unit/test_seed_mining.py::test_pdf_never_leaves`

#### Scenario: Passages go only where the switch allows
- **WHEN** mining runs on a cloud backend with research and `passages` consent and `seed_pdfs.passages_to_cloud` on, then off
- **THEN** the first run's calls each carry one passage of at most the configured size and the usage log records them without text, and the second run makes no model call and names the switch
- **AND** `wider lookups status` and the Settings "Seed papers" card show the switch and what it lets out
Pinned by: `tests/unit/test_seed_mining.py::test_passages_switch_gates_cloud_calls`

### Requirement: A review package leaves the computer only as the researcher sends it
The system SHALL never send a review package or a decisions file anywhere itself: it writes the file only to the path the researcher chose and a copy in the project folder, and says before writing that the file leaves the computer when sent and what it holds. The page makes no network request, and neither file holds a key, a path of this computer or a person's address.
Surfaces: CLI `wider blinding package`, `wider blinding import`; UI card "Review packages".

#### Scenario: Nothing sent and nothing local inside
- **WHEN** on a synthetic project `wider blinding package R3 --sample validation --mode second --out r3.html` runs with a mock HTTP client that records every request, the page is run in Node and its decisions exported, and `wider blinding import` reads them back
- **THEN** the mock client saw no request, the page script made no `fetch`, `XMLHttpRequest`, image or link load, and neither file contains the project folder path, the user's home path, `$USER`, an environment variable value or an e-mail address
- **AND** `scripts/check_no_private_data.py` refuses a staged file under `review-packages/` and a `wider-decisions-*.json` or package `.html` with item tokens outside `tests/`, and a new project's `.gitignore` lists `review-packages/`
Pinned by: `tests/unit/test_package_privacy.py::test_nothing_sent_nothing_local_inside`, `tests/unit/test_package_privacy.py::test_guard_refuses_review_packages`

### Requirement: What a run sends, beside its button
The system SHALL show beside every button that can send record or project text off the computer one sentence from one shared builder: what is sent, where, why, and the consent state ("WIDER asks before sending" or "you allowed this on <date>; change it in Settings"). Locality comes from the backend's configuration ("unknown until checked"), never assumed. The CLI prints the same sentence first.
Surfaces: UI every sending button; CLI the same commands; API `GET /api/send-notice/<action>`.

#### Scenario: Abstract lookup
- **WHEN** Missing abstracts opens with online lookups allowed on 21 Oct and the protocol's sources OpenAlex, Crossref and Europe PMC
- **THEN** beside the lookup button the page says that each record's DOI goes to OpenAlex, Crossref and Europe PMC in that order, that for a record without a DOI its title goes to OpenAlex, that a contact address goes along when one is set, why, and "you allowed this on 21 Oct; change it in Settings"
- **AND** `wider enrich` prints the same sentence before it starts, and with the sources changed to Crossref only both name Crossref only
Pinned by: `tests/unit/test_send_notice.py::test_lookup_sentence_page_and_cli`

#### Scenario: No consent yet
- **WHEN** online lookups are not allowed
- **THEN** the sentence ends "WIDER asks before sending"
Pinned by: `tests/unit/test_send_notice.py::test_consent_states`

#### Scenario: Where a model runs
- **WHEN** Screening opens once with a loopback Ollama backend whose routing is unresolved, and once with a backend at a private network address
- **THEN** the first sentence says where it runs is unknown until checked, the second says titles and abstracts go to another computer on your network, both use the same locality as the model cards and Info, and neither says "this computer"
Pinned by: `tests/unit/test_send_notice.py::test_unknown_locality_never_local`

#### Scenario: Nothing leaves the computer
- **WHEN** a page has several sending buttons whose model runs on this computer, and one lookup that goes online
- **THEN** the page says "Runs on this computer; nothing leaves it." once, beside the first such button, and a local model card adds no consent line
- **AND** the lookup and every card that sends off the computer keep their full sentence and consent state
Pinned by: `tests/unit/test_send_notice.py::test_nothing_leaves_line_once_per_page`

#### Scenario: Every sending action has its sentence
- **WHEN** the list of sending actions in the builder is compared with the pages
- **THEN** every action in the builder's registry (built from the lookup services and every model and embedding call, seed-PDF passages, the missed-names model pass and the embedding step of "Update ranking" included) has the sentence beside its button and before its CLI run
- **AND** a scan of `wider/` for model calls, consent checks and lookup clients finds no call site missing from the registry, and the prior-review lookup's sentence names its own switch
Pinned by: `tests/unit/test_send_notice.py::test_every_sending_action_has_its_sentence`
