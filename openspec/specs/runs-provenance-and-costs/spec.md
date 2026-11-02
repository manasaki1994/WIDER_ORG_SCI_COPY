# runs-provenance-and-costs Specification

## Purpose
Records what every model run saw and with what (input hashes, settings, software, backend identity), keeps each run's
raw output in its own folder under `runs/`, logs the tokens and cost of every model call, and runs long stages in the
background with progress, time left and cost so far. It cuts across the stages that call a model (`construct`,
`screen`) or run long (`enrich`, `rank`), and supports reproducibility: decisions are stored and replayed offline
rather than recomputed.

## Requirements

### Requirement: Stable content hashes and run specs
The system SHALL hash inputs with sha256 (canonical JSON, sorted keys) and describe each model run in `run_spec.json` with a `spec_hash` over kind, WIDER and Python versions, platform, input hashes, settings and the backend snapshot at the run's start, not its start time. The snapshot never holds a key value. Later backends are recorded as backend changes; `run_spec.json` is never rewritten.
Surfaces: engine only (written by screening and construct runs).

#### Scenario: Two identical runs at different times share a spec hash
- **WHEN** two run specs are built with the same kind, inputs and settings at different moments
- **THEN** their `spec_hash` values are equal
- **AND** writing one creates `run_spec.json` containing a `spec_hash` field
Pinned by: `tests/unit/test_provenance.py::test_runspec_hash_ignores_time`

#### Scenario: JSON hashing ignores key order
- **WHEN** two dictionaries with the same keys and values in a different order are hashed
- **THEN** the hashes are equal
Pinned by: `tests/unit/test_provenance.py::test_hashes_are_stable`

#### Scenario: Run spec unchanged by a confirmed backend change
- **WHEN** a run started on model `m1` is continued on `m2` after confirmation
- **THEN** its `run_spec.json` and `spec_hash` are byte-for-byte unchanged and still name `m1`
- **AND** the change is in `backend_changes.jsonl` beside it
Pinned by: `tests/unit/test_screen_provenance.py::test_continue_records_change_and_marks_mixed`

### Requirement: Run folders inside the project
The system SHALL write each screening pass to `runs/<kind>/<stamp>_<run_id>/` (UTC stamp `YYYYMMDDTHHMMSSZ`) with `run_spec.json`, `prompt.md`, `schema.json`, `responses.jsonl`, `decisions.csv`, `errors.csv`, a `backend_<hash12>.json` per backend used and, when the backend changed, `backend_changes.jsonl`; each construct proposal goes to `runs/construct/<stamp>_<artifact kind>/`.
Surfaces: CLI `wider screen`, `wider construct propose`; UI screening page, Construct workbench.

#### Scenario: A screening pass leaves its files
- **WHEN** a screening pass finishes on the development sample
- **THEN** its run folder contains `responses.jsonl`, `decisions.csv` and `run_spec.json`
- **AND** the run spec settings hold the scope, pass, prompt version, number of records, temperature, rule `rule-v3` and protocol hash
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: A run folder holds its backend snapshots
- **WHEN** a screening pass with the fake backend finishes, and another pass continues after its model changed from `m1` to `m2`
- **THEN** the first folder holds one `backend_<hash12>.json` and no `backend_changes.jsonl`
- **AND** the second holds two snapshots and a `backend_changes.jsonl` with one line from `m1` to `m2`
Pinned by: `tests/unit/test_screen_provenance.py::test_continue_records_change_and_marks_mixed`, `tests/unit/test_runs.py::test_run_folder_has_snapshot`

### Requirement: Screening runs table and resumption
The system SHALL register each screening pass in the `runs` table with status `running`, then `done`, `done_with_errors`, or `interrupted` when stopped part-way, keeping the provider and model it started with. Starting the same pass again SHALL reuse the run and skip every record that already has a successful call; when the backend changed, only after the researcher confirms.
Surfaces: CLI `wider screen runs`, `--backend-change`; UI runs table and change dialog.

#### Scenario: Re-running a finished pass makes no model calls
- **WHEN** a pass that has finished on a sample is started again with the same prompt, provider and scope
- **THEN** no model call is made and every record is reported as resumed
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: The runs table carries usage per run
- **WHEN** the LLM screening page reads `GET /api/screen` after screening with the test backend
- **THEN** every listed run carries its usage totals and the locality `test`
Pinned by: `tests/unit/test_runs.py::test_screening_usage_in_results_and_history`

### Requirement: Run ids per result set and mixed runs
The system SHALL register a screening run under `<kind>__<scope>__p<prompt version>__<provider>__<pass>`, with `__<protocol>.s<n>` appended for result set n of a protocol so protocols and sets never share a run; plain runs and runs made before this version keep their ids. A run whose calls used more than one model is shown as "mixed models".
Surfaces: CLI `wider screen runs`; UI LLM screening page, runs table.

#### Scenario: Mixed run in the runs table
- **WHEN** a run was continued from model `m1` on `m2` after confirmation
- **THEN** `GET /api/screen` lists the run once with provider and model as started, `mixed_models` true and both models
- **AND** `wider screen runs` shows the same run with the marker "mixed"
Pinned by: `tests/unit/test_screen_provenance.py::test_continue_records_change_and_marks_mixed`, `tests/unit/test_screen_models.py::test_runs_table_mixed_marker`

#### Scenario: A run shared by two protocols before this version
- **WHEN** a version-3 store holds one run that protocols `main` and `alt` both used, and `main` is continued on more records
- **THEN** the old run is listed in both protocols' sets as shared and is not resumed, and the new records are screened in a run ending `__main.s1`
Pinned by: `tests/unit/test_result_sets.py::test_legacy_shared_run_frozen`

### Requirement: Offline replay of a screening run
The system SHALL rebuild a screening run's rule decisions from its stored `responses.jsonl` without network calls, after checking each stored input hash against the current prompt and record text, and SHALL refuse when they differ. Researcher reads in the log count unless a later line retracts them; `decisions.csv` is rewritten and the counts of replayed reads and changed decisions are reported.
Surfaces: CLI `wider screen replay <run_id>`; no UI control.

#### Scenario: Replay reproduces the stored decisions
- **WHEN** a finished pass is replayed with an unchanged rule
- **THEN** every record is replayed and no decision changes
Pinned by: `tests/unit/test_screen.py::test_engine_protocol_second_evaluate_replay`

#### Scenario: A retracted researcher read stays out after replay
- **WHEN** a researcher's answer added a read to a run, the answer was undone, and the run is replayed
- **THEN** the record has no successful read in that pass
Pinned by: `tests/unit/test_history.py::test_answers_undo_including_screening_log_and_replay`

### Requirement: Usage log of model calls
The system SHALL log every LLM and embedding API call made inside a usage scope in `llm_usage` with time, stage, run, job, backend, model, locality, success, prompt, completion, cached and cache-write tokens (empty when not reported) and cost, and SHALL NOT log calls outside a scope or any prompt or answer text. Scopes use stage `screen` with the run id, `construct`, or `rank` for corpus embeddings.
Surfaces: engine only; read by `wider costs`, `wider info` and the UI.

#### Scenario: Calls are logged only inside a scope and without text
- **WHEN** one call is made outside a scope and two calls are made inside a scope for run `run-x`
- **THEN** the log holds exactly two rows, both with run reference `run-x`, locality `test` and cost 0
- **AND** the user message text appears nowhere in the logged rows
Pinned by: `tests/unit/test_runs.py::test_usage_logged_only_inside_a_scope`

#### Scenario: Corpus embeddings are logged under rank
- **WHEN** corpus embeddings are computed with an embedding API model on a fake endpoint, then computed again for the same texts
- **THEN** the first run adds rows with stage `rank`, its prompt tokens and completion tokens 0
- **AND** the second run, served from the embedding cache, adds no row
Pinned by: `tests/unit/test_embeddings.py::test_corpus_embeddings_logged_under_rank`

#### Scenario: Older store gains the cache columns
- **WHEN** a version-3 store with one `llm_usage` row is opened
- **THEN** the store is at version 4, `llm_usage` has `cached_tokens` and `cache_write_tokens`, and the old row has both empty
Pinned by: `tests/unit/test_runs.py::test_migrate_v4_adds_cache_columns`

### Requirement: Cost of a call
The system SHALL take a call's cost from the backend's reported cost (`total_cost_usd`); else from its prices when it has any, at any locality; else 0 for a local or test backend; else unknown ("not priced", never "paid"). Prices are USD per million tokens: input, output, and optional cached-input and cache-write (default the input price). Embedding calls use the model's `price_input_per_mtok`.
Surfaces: CLI `--price-*` on `wider providers|embed add|edit`; UI backend and Embeddings forms.

#### Scenario: The four cost rules
- **WHEN** costs are computed for a local Ollama backend whose model the local `/api/tags` lists (routing result `local`), an unpriced cloud backend, a backend priced at 3 and 15 USD per million with 1,000,000 input and 100,000 output tokens, and an agentic CLI reporting 0.0123 USD
- **THEN** the costs are 0, unknown, 4.5 and 0.0123 USD
Pinned by: `tests/unit/test_runs.py::test_call_cost_rules`

#### Scenario: Prices set from the UI and the CLI
- **WHEN** a backend is added with prices through `POST /api/providers` and another with `wider providers add --price-in 0.5 --price-out 1.5`
- **THEN** both backends keep their prices in `project.yaml`
Pinned by: `tests/unit/test_runs.py::test_prices_via_api_and_cli`

#### Scenario: Embedding call priced on input
- **WHEN** an embedding model priced at 0.02 USD per million input tokens embeds texts for 1,000,000 tokens, and an unpriced cloud embedding model embeds the same texts
- **THEN** the costs are 0.02 USD and unknown
- **AND** an embedding model added through `POST /api/embeddings` with an input price and one through `wider embed add --price-in 0.02` both keep the price
Pinned by: `tests/unit/test_runs.py::test_embedding_cost_rules`, `tests/unit/test_runs.py::test_embedding_price_via_api_and_cli`

#### Scenario: Cached and cache-write prices
- **WHEN** a call with 1,000,000 input tokens, of which 600,000 cached and 100,000 cache-write, and no output, runs on a backend priced at 3 USD in, 0.3 USD cached in and 3.75 USD cache write
- **THEN** its cost is 0.9 + 0.18 + 0.375 = 1.455 USD
- **AND** without the cached prices the same call costs 3 USD
Pinned by: `tests/unit/test_runs.py::test_cached_price_rules`

#### Scenario: Priced remote self-hosted and unpriced remote
- **WHEN** a vLLM backend at `https://gpu.example.test/v1` has an input price of 1 USD, and another remote vLLM backend has none
- **THEN** the first call is priced from its price
- **AND** the second is "not priced", and no label calls it paid
Pinned by: `tests/unit/test_runs.py::test_remote_self_hosted_pricing`

### Requirement: Local, cloud or test backend
The system SHALL classify a backend from its actual endpoint and routing: `fake` is test; `ollama` without a base URL, and `ollama`, `openai_compat`, `litellm` or `typesafe` on a loopback host are local, except an Ollama model takes its routing's locality; 0.0.0.0, :: or an unreadable host are `unknown`; every other backend, `anthropic` and `cli_agent` included, is cloud ("remote"). No host name is resolved.
Surfaces: CLI `wider providers list`; UI locality marker and destination.

#### Scenario: A local Ollama call is free
- **WHEN** the cost of a call to an Ollama backend at 127.0.0.1 is computed, for a model the local `/api/tags` lists without a cloud marker (routing result `local`)
- **THEN** it is 0
Pinned by: `tests/unit/test_runs.py::test_call_cost_rules`

#### Scenario: A local preset pointed at another host is remote
- **WHEN** a backend is added from the `lm-studio` preset with the base URL `https://models.example.test/v1`
- **THEN** its locality is `cloud`, shown as "remote", and its destination is `models.example.test`
Pinned by: `tests/unit/test_presets.py::test_preset_never_sets_locality`

#### Scenario: Ollama cloud model on the local server
- **WHEN** an Ollama backend at 127.0.0.1 uses the model `m1:7b-cloud`, and then the model `m4` that the local server does not list
- **THEN** the first locality is `cloud` with destination "ollama.com (via Ollama)", and the second is `unknown` with destination "unknown (via Ollama)"
Pinned by: `tests/unit/test_shell.py::test_locality_rules`

#### Scenario: Destinations
- **WHEN** the destination is computed for an Ollama backend at 127.0.0.1 whose model routing is local, an OpenAI-compatible backend at 127.0.0.1, an `openai` preset backend, a `codex` preset backend and a custom CLI
- **THEN** they are "this computer", "this computer", `api.openai.com`, "OpenAI (via Codex)" and "unknown (custom CLI)"
- **AND** an Ollama backend whose routing is not established (Ollama not reachable) is "unknown (via Ollama)", never "this computer"
- **AND** `wider providers list` shows them in its destination column
Pinned by: `tests/unit/test_shell.py::test_destinations`, `tests/unit/test_cli_setup.py::test_providers_list_destination`

### Requirement: Local-network backends and mixed totals
The system SHALL class those local kinds on a private or link-local address or a `.local`, `.lan`, `.internal`, `.home.arpa` or single-label host as `network` ("Local network"), priced like any call, never assumed free. `unknown` is shown as "unknown" and counts as cloud. Loopback is localhost, `*.localhost`, 127.0.0.0/8 or ::1. A set of calls is cloud when any call is cloud, else network when any is network.
Surfaces: CLI `wider costs --by backend`; UI result cards, runs table, job bar.

#### Scenario: A backend on another computer of the network
- **WHEN** locality and cost are computed for an `openai_compat` backend at `http://192.168.1.20:8000/v1` without prices, one at `http://gpu.local:11434` priced at 1 USD per million input tokens, and one at `http://0.0.0.0:1234/v1`
- **THEN** the first two are `network`, shown as "Local network" with destinations `192.168.1.20` and `gpu.local`, and the third is `unknown`
- **AND** the first call's cost is not priced (never 0), the second's is 1 USD for 1,000,000 input tokens, and no host name was resolved
Pinned by: `tests/unit/test_shell.py::test_host_class`, `tests/unit/test_runs.py::test_network_cost_not_assumed`

#### Scenario: Any cloud call makes the total cloud
- **WHEN** the project totals combine calls to a cloud backend and calls without a known price
- **THEN** the locality of the total is `cloud`
Pinned by: `tests/unit/test_runs.py::test_project_cost_with_prices_and_legacy`

### Requirement: Project cost totals
The system SHALL report the project's calls, tokens and cost by stage (default), backend or run, then a total that also counts screening calls stored before the usage log existed and states how many calls have no price. These are priced at current prices and shown apart as "estimated at today's prices", never mixed into the logged cost. An unknown `--by` exits 1; the project summary shows the total in one line.
Surfaces: CLI `wider costs [--by]`, `wider info`; UI Home page info panel.

#### Scenario: Totals with priced, unpriced and legacy calls
- **WHEN** the log holds one priced cloud call costing 1.8 USD and one unpriced call, and one older screening call of 1,000,000 input tokens on a backend priced at 2 USD per million has no log row
- **THEN** the project total is 3 calls and 3.8 USD with 1 unpriced call
- **AND** `wider costs --by backend` lists "cloud (m, cloud)" with "1 unpriced" and prints "project total: 3 calls"
- **AND** `wider costs --by nope` exits with status 1
Pinned by: `tests/unit/test_runs.py::test_project_cost_with_prices_and_legacy`

#### Scenario: Older calls labelled as an estimate
- **WHEN** the same project's costs are shown by `wider costs` and on the Home page info panel
- **THEN** both show the 2 USD of the older call on its own line, "estimated at today's prices", next to the 1.8 USD logged cost
- **AND** after the backend's input price is edited to 4 USD the logged 1.8 USD is unchanged and the estimate becomes 4 USD
Pinned by: `tests/unit/test_runs.py::test_legacy_cost_labelled_estimate`

### Requirement: Usage on every stage result
The system SHALL attach the run's usage (calls, tokens, cost, whether the cost is fully known, backends and locality) to the stage result of each screening pass, protocol and construct proposal, and the result card SHALL show tokens, cost and the locality marker. A result recorded inside an open usage scope takes that scope's usage.
Surfaces: CLI summaries printed by `wider screen ...` and `wider construct propose`; UI result card on each stage page.

#### Scenario: A screening pass reports its own calls
- **WHEN** a screening pass runs on the development sample with the test backend
- **THEN** its usage counts exactly as many calls as the backend received and the stored last result shows locality `test` with a known cost
Pinned by: `tests/unit/test_runs.py::test_screening_usage_in_results_and_history`

#### Scenario: A result recorded inside a scope gets that run's usage
- **WHEN** a construct result is recorded inside a usage scope for run `prop-1` that has one logged call
- **THEN** the recorded result's usage shows that call and its tokens
Pinned by: `tests/unit/test_runs.py::test_stage_result_history_and_scope_usage`

### Requirement: Earlier stage results and the run selector
The system SHALL keep every stage result in `stage_results` and return a stage's results newest first (default 30). Each stage page SHALL show a run selector, hidden while the stage has fewer than two results; an earlier result is shown marked "Earlier result" until "Back to the latest result" is chosen.
Surfaces: UI run selector on each stage page (`GET /api/stages/{stage}/results`); CLI `wider status` shows only the latest result.

#### Scenario: Results are kept newest first
- **WHEN** two enrichment results "first" and "second" are recorded in that order
- **THEN** the stage history lists "second" before "first"
Pinned by: `tests/unit/test_runs.py::test_stage_result_history_and_scope_usage`

### Requirement: Background runs in the interface
The system SHALL run screening, embedding, abstract fetching and construct proposals started from the UI as background jobs in the `jobs` table, so the page stays usable and the run continues when the tab closes, and SHALL refuse a second job on a busy stage (HTTP 409). An embedding job whose local model is not downloaded is skipped.
Surfaces: UI stage pages (`POST /api/jobs/screen|embed|enrich|propose/{kind}`); CLI runs the same stages in the foreground only.

#### Scenario: A screening job runs in the background and reports its usage
- **WHEN** a screening pass is started through `POST /api/jobs/screen` and finishes
- **THEN** the job is `done`, its result carries the screening stage result, its usage counts every model call and its progress reached the total
- **AND** an unknown job id returns 404, an unknown proposal kind 400, and an embedding job for a model that is not downloaded returns `skipped`
Pinned by: `tests/unit/test_runs.py::test_job_routes_screen_in_background`

#### Scenario: A second job on a busy stage is refused
- **WHEN** a job is running on a stage and another job is started on the same stage
- **THEN** the start fails with "still running"
Pinned by: `tests/unit/test_runs.py::test_job_runner_progress_cancel_fail_and_interrupted`

### Requirement: Screening jobs that need an answer first
The system SHALL start no screening job that needs an answer first and say why: 409 `backend_change`, 409 `new_set_needed` or 409 `scope_needed`; a cloud backend without consent answers 403 `backend_consent_required`; a plain run whose records were all screened under a model edited since answers 200 `already_done` with the run id and model.
Surfaces: UI LLM screening page (`POST /api/jobs/screen`).

#### Scenario: A screening job that needs an answer starts nothing
- **WHEN** `POST /api/jobs/screen` is sent for a protocol whose settings changed, for a migrated set of unknown scope, for an unconsented cloud backend, and for a plain run finished under a model that was edited since
- **THEN** the answers are 409 `new_set_needed`, 409 `scope_needed`, 403 `backend_consent_required` and 200 `already_done`
- **AND** the `jobs` table holds no new row after any of them
- **AND** `POST /api/screen/run` with the same four bodies gives the same four answers, makes no model call and creates no run folder
Pinned by: `tests/unit/test_runs.py::test_screen_job_refusals_start_nothing`

### Requirement: Job progress, time left and cancellation
The system SHALL report each job's items done and total, time left from the pace of the current phase, elapsed time and tokens and cost so far. Cancel SHALL stop the job as `cancelled` (finished items kept); an error marks it `failed`; a job left running by a stopped server reads `interrupted`; cancelling a job that is not running returns HTTP 409.
Surfaces: UI job bar on every page (polls every 2 s while a job runs, else every 15 s; Cancel asks first); no CLI.

#### Scenario: Progress, ETA, cancel, failure and interruption
- **WHEN** a job reports progress on 1000 items, is cancelled, another job raises "bad input", and a `running` job row exists without a live thread
- **THEN** the first job shows items done and a time left while running and ends `cancelled`
- **AND** the failing job ends `failed` with "bad input" in its error, and the orphaned job reads `interrupted` and cannot be cancelled
Pinned by: `tests/unit/test_runs.py::test_job_runner_progress_cancel_fail_and_interrupted`

#### Scenario: The job bar is on every page
- **WHEN** the UI shell is loaded
- **THEN** it contains the job bar, the message area and the run selector, and the job script polls the active jobs, offers notifications and calls the cancel route
Pinned by: `tests/unit/test_runs.py::test_static_job_bar`

### Requirement: Backend snapshot of a run
The system SHALL record in each run spec the effective backend: every setting, preset, `extra`, destination, locality and its source, `experimental` and limitations, and for a `cli_agent` its argv, version and executable. Secrets appear only as variable names or `{env:NAME}`. Home-folder paths are written as `~`. `spec_hash` covers the snapshot.
Surfaces: engine (`wider screen ...`, `wider construct propose`, their UI runs); `wider report --bundle`, `wider export package`.

#### Scenario: CLI snapshot without the home folder
- **WHEN** a run uses a `cli_agent` backend added from a preset, whose fake executable lives inside the home folder, is named by an absolute argv[0] under that home folder and prints the version `9.9.9`
- **THEN** the run spec's backend snapshot holds the preset id, the command template with argv[0] starting with `~`, the version `9.9.9`, the destination and the executable path starting with `~`
- **AND** neither the run spec nor the share bundle's copy of it contains the home-folder path
Pinned by: `tests/unit/test_provenance.py::test_cli_identity_hides_home_and_records_version`

#### Scenario: Extra settings change the spec hash
- **WHEN** two run specs differ only in the backend's `extra.json_mode`
- **THEN** their `spec_hash` values differ
- **AND** an `extra.headers` entry appears as written (`{env:SOME_HEADER_VAR}` or a plain non-sensitive value), and an `extra.env` value that is neither placeholders nor `{env:NAME}` appears as "(value not recorded)"
Pinned by: `tests/unit/test_provenance.py::test_extra_in_identity_and_hash`

#### Scenario: A later edit leaves the run's record alone
- **WHEN** a run finishes on backend `a` with timeout 900, and `a` is then edited to timeout 60
- **THEN** the stored run spec still says timeout 900 and keeps its `spec_hash`
Pinned by: `tests/unit/test_provenance.py::test_snapshot_survives_later_edit`

#### Scenario: Key values never recorded
- **WHEN** a backend's key variable `SOME_KEY_VAR` holds a synthetic secret value and a screening run, a share bundle and a job log are produced
- **THEN** the secret value appears in none of `project.yaml`, the run spec, the share bundle, `llm_usage` or the job log
- **AND** the run spec names `SOME_KEY_VAR`
Pinned by: `tests/unit/test_provenance.py::test_key_value_never_recorded`

### Requirement: Cost range before a run
The system SHALL show a cost range for the chosen backend before a screening pass, a construct proposal or corpus embeddings: minimum with the most eligible caching and cache-write fees, maximum with none, both with output. It names its assumptions and destination. `--estimate-only` prints it and makes no call.
Surfaces: CLI `--estimate-only` on `wider screen run|protocol|second`, `wider construct propose`, `wider embed corpus`; UI run forms, embed action.

#### Scenario: Range for a priced backend
- **WHEN** `wider screen run sample:development --estimate-only` runs on a fake backend priced at 3 USD in, 0.3 USD cached in, 3.75 USD cache write and 15 USD out per million tokens
- **THEN** it prints a minimum lower than the maximum, the number of calls, the destination and the assumptions "input tokens estimated as characters ÷ 4" and "output tokens: max_tokens per call"
- **AND** no model call is made and no `llm_usage` row is added
Pinned by: `tests/unit/test_estimate.py::test_range_priced_backend`, `tests/unit/test_cli_setup.py::test_estimate_only_makes_no_call`

#### Scenario: Range from earlier calls
- **WHEN** the same backend already has logged screening calls
- **THEN** the estimate uses their tokens per character and their median output tokens, and says so in its assumptions
Pinned by: `tests/unit/test_estimate.py::test_range_uses_observed_usage`

#### Scenario: Local and unpriced backends
- **WHEN** the estimate is made for a local Ollama backend whose model the local `/api/tags` lists (routing result `local`) without prices, and for a remote backend without prices
- **THEN** the first range is 0 to 0 USD with destination "this computer"
- **AND** the second says "not priced" with its token counts and its host as destination
Pinned by: `tests/unit/test_estimate.py::test_range_local_and_unpriced`

#### Scenario: Range in the run form
- **WHEN** the researcher opens the screening run form
- **THEN** the cost range, its assumptions and the destination are shown before Start, from `POST /api/estimate`
- **AND** pressing "Embed corpus" on a priced cloud embedding model first shows its range with Continue and Cancel, and Cancel starts no job
Pinned by: `tests/unit/test_backends_js_node.py::test_estimate_text`, `tests/unit/test_backends_js_node.py::test_embed_estimate_confirm`, `tests/unit/test_setup_routes.py::test_estimate_route_matches_cli`

### Requirement: Estimate updated during a run
The system SHALL update a running job's cost while it runs: the cost so far from logged calls, and a projected range for the whole run from the observed mean cost per call times the calls left.
Surfaces: UI job bar; CLI progress lines of `wider screen ...`.

#### Scenario: Projection after some calls
- **WHEN** a screening job on a priced fake backend has finished 2 of 4 calls
- **THEN** the job's usage holds the cost so far and a projected minimum and maximum
- **AND** the job bar shows "cost so far" and "projected"
- **AND** the progress lines of `wider screen run` on the same backend show the same cost so far and projected range
Pinned by: `tests/unit/test_runs.py::test_job_projection`, `tests/unit/test_backends_js_node.py::test_usage_text_projection`, `tests/unit/test_cli_setup.py::test_screen_progress_shows_projection`

### Requirement: Cost and cache use after a run
The system SHALL report after each run the calculated cost and the cached input tokens, or "cache use not reported" when the backend gave none.
Surfaces: UI result card on each stage page; CLI `wider costs`, `wider info` and the summary printed by `wider screen ...`.

#### Scenario: Cached tokens shown, or not reported
- **WHEN** one run's calls report 2,000 cached tokens and another run's backend reports no cache use
- **THEN** the first result shows its cost and 2,000 cached tokens
- **AND** the second shows its cost and "cache use not reported"
Pinned by: `tests/unit/test_runs.py::test_cache_use_after_run`
