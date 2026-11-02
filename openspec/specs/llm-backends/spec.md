# llm-backends Specification

## Purpose
LLM backends and embedding models are the researcher's choice of which models do the work in the WIDER procedure. Construct proposals and screening use an LLM backend, and ranking uses an embedding model. The capability keeps these choices in `project.yaml` (keys only by environment variable name). It runs every model call through one loop with retries, structured output and repair, and logs tokens and costs. It also probes backends with fixed test inputs so the researcher can confirm that a backend works before using it on project data.

## Requirements

### Requirement: Backends in project.yaml with keys by variable name only
The system SHALL keep LLM backends under `providers:` and embedding models under `embeddings:` in `project.yaml`, with `default_provider` and `default_embedding` naming the defaults. An API key MUST be referenced only by environment variable name (`api_key_env`) and read at call time. Listings show "NAME set" or "NAME MISSING".
Surfaces: CLI `wider providers list`, `wider embed list`; UI Settings backend cards; model page and Ranking page cards.

#### Scenario: Key variable set in the environment
- **WHEN** a provider references `SOME_KEY_VAR` and that variable holds a secret value
- **THEN** the provider row reports the key as set and names `SOME_KEY_VAR`
- **AND** the secret value appears neither in the row, nor in `project.yaml`, nor in the backend identity
Pinned by: `tests/unit/test_backends.py::test_provider_rows_never_expose_key_values`, `tests/unit/test_providers.py::test_identity_has_no_secrets`

#### Scenario: Backends of a new project
- **WHEN** a project is created
- **THEN** it has the providers `ollama` (default; `qwen3:8b` at `http://127.0.0.1:11434`, timeout 1800 s, thinking off), `openai` (`gpt-4o-mini`, `OPENAI_API_KEY`), `anthropic` (`ANTHROPIC_API_KEY`) and `claude-code` (`cli_agent`)
- **AND** it has the embedding models `bge-m3` (default, local, `BAAI/bge-m3`), `openai-large-1024` (`text-embedding-3-large`, 1024 dimensions), `bge-base`, `minilm` and `ollama-nomic`, and looking up an unknown backend raises a KeyError
Pinned by: `tests/unit/test_project.py::test_provider_and_embedding_lookup`

### Requirement: Add, update, remove and choose LLM backends
The system SHALL add or replace an LLM backend by name, of kind `openai_compat` (default), `ollama`, `anthropic`, `cli_agent`, `litellm` or `typesafe`; replacing needs `replace: true` in the API (else 409) and the CLI names the reset fields. An unknown kind, a missing or unsafe name and a `cli_agent` without a command MUST be refused. Removing the default makes the first remaining backend the default.
Surfaces: CLI `wider providers add|remove|set-default`; UI model page, Settings backend cards.

#### Scenario: Add, make default, remove
- **WHEN** a backend `mine` is added with `--set-default`, then a `cli_agent` backend is added, made the default and removed
- **THEN** `mine` was the default after the first step, and after the removal the default is no longer the removed backend
- **AND** stage overrides that named the removed backend are gone, and removing it again exits with code 1 (HTTP 404)
Pinned by: `tests/unit/test_cli_setup.py::test_providers_and_embed_commands`, `tests/unit/test_setup_routes.py::test_provider_routes`, `tests/unit/test_backends.py::test_provider_add_remove_default_roundtrip`

#### Scenario: Agentic CLI without a command
- **WHEN** a backend of kind `cli_agent` is added without `--command`
- **THEN** it is refused with "a cli_agent provider needs a command (argv template, JSON list)"

#### Scenario: A backend name that would break its own buttons
- **WHEN** a backend or an embedding is added with the name `a/b` (or `<b>x</b>`, `a\b`, `..`)
- **THEN** it is refused with "backend name … may not contain …", nothing is saved, and the backend form on the model page shows that message
- **AND** a name of letters, digits, `-`, `_` and `.` such as `lab-model_2.1` is accepted
Pinned by: `tests/unit/test_backends.py::test_backend_name_with_slash_is_refused`

#### Scenario: Shared settings and defaults
- **WHEN** a backend is added without further settings
- **THEN** it has `temperature` 0.0, `seed` 1, `max_concurrency` 4 and `timeout_s` 300, and it becomes the default only when asked or when no default exists
- **AND** the optional prices `--price-in` / `--price-out` (USD per million tokens) must be at least 0

#### Scenario: Adding over an existing name
- **WHEN** backend `a` has timeout 900 and input price 1, and `wider providers add --name a --model m2` runs
- **THEN** `a` is replaced, and the command prints a warning naming `timeout_s` and `price_input_per_mtok` as reset and pointing to `wider providers edit a`
- **AND** `POST /api/providers` for `a` without `replace` answers 409 listing the same fields and saves nothing, and with `replace: true` it replaces `a`
Pinned by: `tests/unit/test_cli_setup.py::test_providers_add_replace_warns`, `tests/unit/test_setup_routes.py::test_post_existing_provider_needs_replace`

### Requirement: Per-stage backend overrides
The system SHALL let each stage (`construct`, `screen`, `screen_b`, `screen_judge`; `stage_providers`) name its own LLM backend; a stage uses it, else the default (also when gone), but `screen_b`, `screen_judge` then follow pass A. An unknown stage or backend MUST be refused. `construct` also covers adjacent-literature proposals and finds; `--provider` wins.
Surfaces: CLI `wider providers stage STAGE [NAME]`, `--provider`; UI LLM backends card, per-stage selects.

#### Scenario: Screening on a second backend
- **WHEN** `a` is the default and `b` is set for `screen`
- **THEN** screening resolves to `b` and construct proposals resolve to `a`
- **AND** after `b` is removed, screening resolves to `a` again, and an unknown stage or backend name raises an error
Pinned by: `tests/unit/test_backends.py::test_stage_override_and_provider_for`

#### Scenario: Adjacent literatures on the construct backend
- **WHEN** `a` is the default and `b` is set for `construct`, and adjacent literatures are proposed and found without `--provider`
- **THEN** both calls go to `b`
- **AND** with `--provider a` they go to `a`, and the `--provider` help of both commands says the `construct` override applies when it is omitted
Pinned by: `tests/unit/test_adjacent.py::test_adjacent_uses_construct_override`

#### Scenario: Pass B and judge keys
- **WHEN** `a` is the default, `b` is set for `screen_b`, and `wider providers stage screen_judge nosuch` runs
- **THEN** pass B resolves to `b`, the judge follows pass A, and the unknown backend is refused with exit 1 and nothing saved
- **AND** `wider providers stage screen_b` without a name removes the override, and a `screen_b` naming a removed backend also follows pass A
Pinned by: `tests/unit/test_pass_backends.py::test_stage_keys_for_passes`

#### Scenario: Settings screen row with a select per pass
- **WHEN** the LLM backends card lists the per-stage selects
- **THEN** the Screening row shows three selects, "Pass A", "Pass B" and "Judge", the last two offering "Same as pass A" first
- **AND** choosing `b` for Pass B sends the same request as `wider providers stage screen_b b`
Pinned by: `tests/unit/test_backends_js_node.py::test_settings_screen_row_has_pass_selects`

### Requirement: One request loop with retries and schema repair
The system SHALL send every model call through one loop that limits concurrency per backend to `max_concurrency` and tries up to 3 times, backing off min(30, 2^i + random) seconds after a retryable error. With a schema, the answer's JSON is extracted and validated; invalid output gets one repair round, also after a retried error, then the call fails.
Surfaces: none of its own; used by `wider construct`, `wider screen`, `wider providers probe`.

#### Scenario: Repaired output
- **WHEN** the first answer does not match the schema and the second does
- **THEN** the call succeeds after 2 attempts, and the second request carries the validation error and the previous reply (cut to 1500 characters)
Pinned by: `tests/unit/test_providers.py::test_repair_retry_on_invalid_output`

#### Scenario: Output still invalid after repair
- **WHEN** both answers fail the schema
- **THEN** the call fails after 2 attempts with "invalid structured output: ..."
Pinned by: `tests/unit/test_providers.py::test_gives_up_after_repair`

#### Scenario: JSON found in prose
- **WHEN** an answer wraps the JSON in a code fence or surrounding text
- **THEN** the JSON is extracted, and a schema violation is reported with its field path
Pinned by: `tests/unit/test_providers.py::test_extract_json_variants`, `tests/unit/test_providers.py::test_validate_json_reports_path`

#### Scenario: Repair after a retried error
- **WHEN** the first attempt times out, the second answer fails the schema and the third matches it
- **THEN** the call succeeds after 3 attempts, and the third request carries the repair note
Pinned by: `tests/unit/test_providers.py::test_repair_after_transport_retry`

#### Scenario: Dropped connection and timeout are retried
- **WHEN** an HTTP backend's connection is refused once and then times out once before it answers
- **THEN** the call succeeds after 3 attempts
- **AND** when every attempt fails, the call fails with a message that begins "cannot reach" and names the base URL, instead of an unhandled error
Pinned by: `tests/unit/test_http_adapters.py::test_transport_errors_are_retryable`

### Requirement: HTTP backend kinds
The system SHALL call `openai_compat` at `{base_url}/chat/completions`, `ollama` at `{base_url}/api/chat`, `anthropic` at `/v1/messages` (forced tool `answer`) and `typesafe` at `{base_url}/v1/systemone`. HTTP 429 and 5xx retry. A missing key MUST be reported ("environment variable NAME is not set") before any request for Anthropic, Jev, and a remote `openai_compat` naming a key variable without `extra.auth: none`.
Surfaces: CLI `wider providers add --kind`.

#### Scenario: Fake backend fills the schema
- **WHEN** the test-only `fake` backend answers a request with a schema
- **THEN** the parsed answer is an example built from the schema and the response carries the input hash
Pinned by: `tests/unit/test_providers.py::test_fake_provider_fills_schema_and_hashes_input`

#### Scenario: Unknown kind
- **WHEN** a backend of an unknown kind is constructed
- **THEN** a ValueError is raised
Pinned by: `tests/unit/test_providers.py::test_unknown_kind`

#### Scenario: Ollama options
- **WHEN** an Ollama backend is called
- **THEN** it asks for format `json` (or the schema when `extra.json_mode` is `schema`) and sends the `think` flag
- **AND** an unreachable server is reported as "cannot reach Ollama at ..."
Pinned by: `tests/unit/test_http_adapters.py::test_ollama_request_shape`

#### Scenario: Anthropic request
- **WHEN** an Anthropic backend with `ANTHROPIC_API_KEY` set to a synthetic value is called with a schema
- **THEN** the request goes to `/v1/messages` with the `x-api-key` header, `anthropic-version` `2023-06-01` and a forced tool `answer` whose input schema is the request's schema
Pinned by: `tests/unit/test_http_adapters.py::test_anthropic_request_shape`

#### Scenario: Missing key stops a cloud call
- **WHEN** a backend added from the `groq` preset is called while `GROQ_API_KEY` is not set
- **THEN** the call fails with "environment variable GROQ_API_KEY is not set"
- **AND** no request is sent
Pinned by: `tests/unit/test_http_adapters.py::test_missing_key_on_cloud_endpoint_sends_nothing`

### Requirement: Agentic CLI backends run isolated
The system SHALL run a `cli_agent` backend as one process per call in a fresh, empty temporary directory holding only `system_prompt.md`, `schema.json` and the preset's own settings file (Gemini CLI's usage-statistics switch), never other research data. The answer is parsed per `extra.output`: `claude_json`, `codex_jsonl`, `gemini_json`, `agy_json`, `json`, `last_line`, or `text` by default.
Surfaces: CLI `wider providers add --kind cli_agent --command`; UI LLM backends card.

#### Scenario: A process answers with JSON
- **WHEN** a `cli_agent` backend runs a command that prints a JSON object, with `output: text`
- **THEN** the call succeeds with that object parsed and the identity names the executable
Pinned by: `tests/unit/test_providers.py::test_cli_agent_runs_a_process`

#### Scenario: Input, failures and executable lookup
- **WHEN** an agentic CLI backend is called
- **THEN** the user message goes on stdin unless `extra.stdin` is `none`, and a timeout, a non-zero exit with empty output or a `claude_json` error is retryable unless it shows the CLI is not signed in
- **AND** the executable is found as an absolute path, else on PATH, else at the first existing one of the preset's app-bundle paths in their fixed order (the Claude desktop app; for `codex`, `Codex.app`, then `ChatGPT.app`, then the VS Code extension), taking the highest version within one path pattern
- **AND** with both `Codex.app` and the VS Code extension present, the `Codex.app` copy is used even when the extension's version is higher

#### Scenario: Codex answer
- **WHEN** a fake `codex` executable prints synthetic JSONL events that end with an `agent_message` item holding a JSON object and a `turn.completed` event with usage
- **THEN** the call succeeds with that object, and the usage gives its input, cached and output tokens
- **AND** a `turn.failed` or `error` event makes the call fail with that event's message
Pinned by: `tests/unit/test_cli_agent.py::test_codex_jsonl_parser`

#### Scenario: Codex developer instructions
- **WHEN** a backend from the `codex` preset calls a fake `codex` with a system text holding quotes, a backslash, a newline and a control character
- **THEN** the argv holds `-c` `developer_instructions=…` whose value parses as TOML back to exactly that system text
- **AND** the system text is not on stdin, which holds only the user message
Pinned by: `tests/unit/test_cli_agent.py::test_codex_developer_instructions_round_trip`

#### Scenario: Strict schema for Codex
- **WHEN** a backend with `extra.strict_schema` true is called with a schema that has an optional property `model_note`, and the fake CLI answers with `model_note` null
- **THEN** the written `schema.json` lists every property as required, gives `model_note` a nullable type and sets `additionalProperties` false on every object
- **AND** the parsed answer has no `model_note` key and passes the original schema
- **AND** a call without a schema has neither `--output-schema` nor a schema path in its argv
Pinned by: `tests/unit/test_schema_strict.py::test_strict_transform_and_null_drop`, `tests/unit/test_cli_agent.py::test_no_schema_drops_schema_flag`

#### Scenario: Gemini CLI answer
- **WHEN** a fake `gemini` executable prints a JSON object with `response` and `stats`
- **THEN** the call succeeds with the JSON found in `response`, and the tokens are summed over the models in `stats`
- **AND** an `error` field makes the call fail with its message
Pinned by: `tests/unit/test_cli_agent.py::test_gemini_json_parser`

#### Scenario: Antigravity CLI answer
- **WHEN** a fake `agy` executable prints a JSON object with `status` `SUCCESS`, `structured_output` and `usage`
- **THEN** the call succeeds with `structured_output`, and the usage gives input, output plus thinking, and cache-read tokens
- **AND** a `status` other than `SUCCESS` or a non-empty `error` makes the call fail with its message
Pinned by: `tests/unit/test_cli_agent.py::test_agy_json_parser`

### Requirement: Agentic CLI command placeholders
The system SHALL substitute `{model}`, `{system_file}`, `{system_toml}`, `{schema_file}`, `{system}` and `{user}` in a `cli_agent` argv template and in its `extra.env` values; without a schema, `{schema_file}` and the flag before it are left out.
Surfaces: CLI `wider providers add --kind cli_agent --command`; UI LLM backends card, command field.

#### Scenario: Schema file and environment placeholders
- **WHEN** a call with a schema runs a command containing `{schema_file}`, with `extra.env` `{"SOME_PROMPT_FILE": "{system_file}"}`
- **THEN** the process finds the request's schema as JSON at the `{schema_file}` path, inside its empty working directory
- **AND** the variable `SOME_PROMPT_FILE` holds the path of `system_prompt.md`
Pinned by: `tests/unit/test_cli_agent.py::test_schema_file_and_env_placeholders`

### Requirement: Provider probe with a fixed test question
The system SHALL probe an LLM backend with one structured request that contains no project data. The request asks for `status: ok` and the sum of 17 and 25, with at most 2 attempts. The probe MUST be reported as ok only when the sum is 42, and the CLI exits with code 1 on any failure. A Jev backend is probed with its two fixed sums instead.
Surfaces: CLI `wider providers probe [NAMES...]`; UI LLM backends card, "Probe" (404 for an unknown backend).

#### Scenario: Probe of the fake backend
- **WHEN** the fake backend is probed
- **THEN** the probe is not ok, because the fake answer's sum is 0
- **AND** the error begins with "wrong answer"
Pinned by: `tests/unit/test_providers.py::test_probe_with_fake_provider`

#### Scenario: Unreachable backend
- **WHEN** a backend whose server does not answer is probed
- **THEN** the probe is not ok after 2 attempts, its error begins with "cannot reach", and `wider providers probe` exits with code 1
- **AND** the UI probe answers with that message, not with a server error
Pinned by: `tests/unit/test_providers.py::test_probe_unreachable_backend`, `tests/unit/test_setup_routes.py::test_probe_route_unreachable`

### Requirement: Embedding models and the download gate
The system SHALL manage embedding models of kind `sentence_transformers` (default), `openai`, `openai_compat` or `ollama`. A name and a model are required. A local model MUST NOT download weights unless asked. `wider embed download` is the only download command, and `--allow-download` permits a download for one run.
Surfaces: CLI `wider embed add|remove|set-default|download`; UI Embeddings card ("Download now (~N MB)", behind a confirmation).

#### Scenario: Cached local model stays offline
- **WHEN** a local model's weights are in the cache and no download was requested
- **THEN** the model is loaded with local files only
Pinned by: `tests/unit/test_embeddings.py::test_cached_local_model_loads_offline_unless_download_requested`

#### Scenario: Weights missing
- **WHEN** a local model without downloaded weights is probed or used for the corpus without `--allow-download`
- **THEN** the probe is skipped and `wider embed corpus` exits with code 1
- **AND** downloading a remote model answers "remote model, nothing to download"

#### Scenario: Add, choose and remove an embedding model
- **WHEN** an `openai_compat` model `e1` with `--dim 64` is added as default, then `bge-m3` is made default and `e1` removed
- **THEN** `e1` is gone, and `wider embed download nope` exits with code 1
Pinned by: `tests/unit/test_cli_setup.py::test_providers_and_embed_commands`, `tests/unit/test_setup_routes.py::test_embedding_routes`, `tests/unit/test_backends.py::test_embedding_add_remove_default`

### Requirement: Embedding computation and cache
The system SHALL cut each text to `max_words` (default 3000) before embedding it. When `dim` is set, vectors are truncated to `dim`, and every vector is then L2-normalised. Vectors are cached in `embeddings/cache.sqlite` under `kind:model:dim`, so cached texts are not sent again. A vector count that differs from the text count MUST raise an error.
Surfaces: CLI `wider embed corpus [-e NAME] [--allow-download]`; UI Rank page, embed step.

#### Scenario: Cached and truncated vectors
- **WHEN** texts are embedded with `dim` 16 and `max_words` 2, then one of them is embedded again
- **THEN** the second call is served from the cache, and a text equal to the first one cut to 2 words gets the same vector
- **AND** every vector has `dim` entries and unit length
Pinned by: `tests/unit/test_embeddings.py::test_cache_roundtrip_and_truncation`, `tests/unit/test_embeddings.py::test_fake_embedder_normalised_and_deterministic`, `tests/unit/test_embeddings.py::test_cache_count_by_model`

#### Scenario: Out of memory on a local model
- **WHEN** a local model fails a batch for lack of memory
- **THEN** the batch is split in half and the embedding completes
Pinned by: `tests/unit/test_embeddings.py::test_local_model_halves_batch_on_out_of_memory`

### Requirement: Embedding probe with fixed sentences
The system SHALL probe an embedding model with three fixed sentences: two related sentences about team meetings and one unrelated sentence about plankton. The probe reports the dimension, elapsed seconds, both similarities and a sanity flag that is true when the related pair scores higher. The probe is ok only when the model answers and the sanity flag is true. Errors MUST be reported as a failed probe.
Surfaces: CLI `wider embed probe [NAMES...] [--allow-download]`; UI Embeddings card, "Probe".

#### Scenario: Fake model probe
- **WHEN** the fake embedding model with `dim` 64 is probed
- **THEN** the result is ok, reports dimension 64 and passes the sanity check
Pinned by: `tests/unit/test_embeddings.py::test_probe_fake`

#### Scenario: Model fails the sanity check
- **WHEN** a model scores the unrelated pair at least as high as the related pair
- **THEN** the probe is not ok, and its error begins with "sanity check failed"
- **AND** `wider embed probe` exits with code 1
Pinned by: `tests/unit/test_embeddings.py::test_probe_fails_sanity`

### Requirement: Backend check
The system SHALL check the default LLM backend, each stage-override backend and the default embedding model with the fixed probe inputs only, no project data. Each result keeps its role (`llm`, `embedding`) and uses (`default`, `construct`, `screen`). It is stored as JSON `{at, ok, results}` under the meta key `backend_check` and shown by `wider info` and the Home Info panel; the CLI MUST exit 1 when any result is not ok.
Surfaces: CLI `wider check-backends`; UI Settings, Home Info panel.

#### Scenario: Failed, then passing check
- **WHEN** the default LLM backend answers the probe wrongly
- **THEN** `wider check-backends` exits with code 1 showing "wrong answer", and `wider info` shows the LLM check as failed
- **AND** after a correct probe the check exits with code 0 and `wider info` shows it as ok
Pinned by: `tests/unit/test_shell.py::test_info_and_check_backends_cli`, `tests/unit/test_shell.py::test_backend_check_route_and_summary`

#### Scenario: Local weights missing
- **WHEN** the default embedding model is a local model that is not downloaded
- **THEN** the check reports "model weights are not downloaded (wider embed download)" and exits with code 1
Pinned by: `tests/unit/test_shell.py::test_check_backends_skips_missing_weights`

#### Scenario: No default chosen
- **WHEN** no default LLM backend or no default embedding model is set
- **THEN** the check reports "no default LLM backend chosen" or "no default embedding model chosen"
Pinned by: `tests/unit/test_cli_setup.py::test_no_default_chosen_is_reported`

#### Scenario: Stage override checked too
- **WHEN** `a` is the default, `b` is set for `screen`, and `b` answers the probe wrongly
- **THEN** the check probes `a` and `b` once each, gives both the role `llm`, names `b`'s use as `screen`, and exits with code 1
- **AND** a backend that is both the default and an override is probed once and lists both uses
- **AND** `wider check-backends` prints `b`'s use, and the Home Info panel shows "LLM b (screen)"
Pinned by: `tests/unit/test_shell.py::test_check_backends_includes_stage_overrides`, `tests/unit/test_shell_js_node.py::test_backend_check_shows_uses`

#### Scenario: Cloud backend without consent is not called
- **WHEN** the default backend is cloud backend `a` without `probe` consent
- **THEN** the check sends nothing to `a`, reports it as "not checked: needs your consent to contact <destination>" naming `wider providers allow a --for probe`, and exits with code 1
Pinned by: `tests/unit/test_shell.py::test_check_backends_needs_consent`

### Requirement: Usage log, locality and cost of every call
The system SHALL log every model call inside a stage's usage scope into `llm_usage`: time, stage, run, job, backend, kind, model, locality, ok, prompt (cached included), cached, cache-write and completion tokens, and cost. It MUST NOT store prompt or answer text; a logging failure never breaks a run. Each backend is classed `local`, `network`, `cloud`, `unknown` or `test` from its endpoint and routing.
Surfaces: CLI `wider costs --by backend`, `wider info`; UI Home project card.

#### Scenario: Calls outside and inside a scope
- **WHEN** the fake backend is called once outside a scope and twice inside a `construct` scope
- **THEN** `llm_usage` holds exactly 2 rows, with locality `test` and cost 0
- **AND** the prompt text does not appear in the rows
Pinned by: `tests/unit/test_runs.py::test_usage_logged_only_inside_a_scope`

#### Scenario: Cost rules
- **WHEN** costs are computed for a local Ollama backend whose model the local `/api/tags` lists (routing result `local`), an unpriced cloud backend, a priced backend (3 / 15 USD per million tokens) and an agentic CLI that reports its cost
- **THEN** they are 0, unknown, 4.5 USD for 1,000,000 input and 100,000 output tokens, and the reported cost
Pinned by: `tests/unit/test_runs.py::test_call_cost_rules`

#### Scenario: Locality rules
- **WHEN** locality is computed
- **THEN** `ollama`, `openai_compat`, `litellm` and `typesafe` take the host class defined in runs-provenance-and-costs "Local, cloud or test backend" (for example `local` for 127.0.0.1, `network` for 192.168.1.20, `unknown` for 0.0.0.0, `cloud` for a public name), `anthropic` and `cli_agent` are `cloud`, and `fake` is `test`
- **AND** an Ollama backend on a local host takes the locality its model's routing gives (see "Locality of Ollama models")
Pinned by: `tests/unit/test_shell.py::test_locality_rules`

#### Scenario: Cached input tokens counted
- **WHEN** a call's usage reports 100 input tokens, 2,000 cache-read and 300 cache-creation input tokens, and 50 output tokens
- **THEN** its row has 2,400 prompt tokens, 2,000 cached tokens, 300 cache-write tokens and 50 completion tokens
- **AND** a call whose usage has no cache fields has empty cached and cache-write tokens
Pinned by: `tests/unit/test_runs.py::test_cached_tokens_counted`

### Requirement: Backend preset catalogue
The system SHALL ship a validated catalogue of backend presets. Each preset has an id, a family (`cli`, `cloud` or `local`), a backend kind, a vendor, default settings, suggested models, detection hints, a billing note and how it was checked (vendor docs only, or a real call). The 18 presets are named in the first scenario.
Surfaces: CLI `wider providers presets [--json]`; UI model page, preset cards.

#### Scenario: Listing the presets
- **WHEN** `wider providers presets` runs
- **THEN** it lists 18 presets with their kind, default model and billing note: CLI `claude-code`, `codex`, `gemini-cli`, `antigravity`, `custom-cli`; cloud `openai`, `anthropic`, `openrouter`, `gemini`, `azure-openai`, `mistral`, `groq`, `together`, `jev`; local `ollama`, `lm-studio`, `vllm`, `llama-cpp`
- **AND** the `gemini-cli` and `antigravity` rows say "checked against the docs, not yet run on this computer"
Pinned by: `tests/unit/test_presets.py::test_catalogue_lists_every_preset`, `tests/unit/test_cli_setup.py::test_providers_presets_command`

#### Scenario: CLI presets disclose the tool's own usage data
- **WHEN** the `codex`, `gemini-cli` and `antigravity` cards are listed
- **THEN** each says the tool may send its own usage data to its vendor and whether WIDER turned that off
- **AND** a preset's command holds a usage-statistics switch only when that switch is marked checked
Pinned by: `tests/unit/test_presets.py::test_cli_presets_disclose_telemetry`, `tests/unit/test_presets.py::test_detect_cards_carry_telemetry_note`, `tests/unit/test_backends_js_node.py::test_cards_grouped_by_family`

#### Scenario: Presets name keys only and agree with computed locality
- **WHEN** each preset's default settings are turned into a backend, with every value a preset `needs` filled with a synthetic one (for `azure-openai` the base URL `https://res.example.test/openai/v1/`)
- **THEN** every local preset's backend is classed `local` (for `ollama` given an injected routing result of local), and every cloud and CLI preset's backend is classed `cloud`
- **AND** the `ollama` preset's backend is `unknown` when no routing result is available
- **AND** every preset that needs a key names only an environment variable, such as `OPENAI_API_KEY`, `GEMINI_API_KEY` or `TYPESAFE_API_KEY`, and holds no key value
Pinned by: `tests/unit/test_presets.py::test_preset_locality_matches_kind_and_url`, `tests/unit/test_presets.py::test_presets_name_keys_only`

#### Scenario: A broken catalogue entry
- **WHEN** a catalogue entry has no kind or names an unknown family
- **THEN** loading the catalogue fails with a message that names the preset id
Pinned by: `tests/unit/test_presets.py::test_invalid_preset_is_refused`

### Requirement: Find backends on this computer
The system SHALL report, for each preset:
- whether its CLI is installed (on PATH or in a known app bundle), and its version;
- whether a local model server answers on 127.0.0.1, and its models;
- whether its API-key environment variables are set;
- whether its sign-in file exists.

Detection MUST stay on this computer: no request to another host, and no credential file is read.
Surfaces: CLI `wider providers detect [--json]`; UI model page, "Find AI models on this computer".

#### Scenario: Installed CLI and running local server
- **WHEN** a fake `claude` executable that prints a version is on PATH, and a fake server on 127.0.0.1:11434 answers `/api/tags` with the model `m1`
- **THEN** the `claude-code` row is found with that version, and the `ollama` row is found with its server running and the model `m1`
- **AND** the rows of CLIs that are not installed and of servers that do not answer say "not found"
Pinned by: `tests/unit/test_detect.py::test_detect_cli_and_local_server`

#### Scenario: CLI found in an app bundle
- **WHEN** no `codex` is on PATH, and a fake `codex` that prints `codex-cli 9.9.9` sits at the `Codex.app` bundle path under a temporary root
- **THEN** the `codex` row is found with version `codex-cli 9.9.9` and that path
- **AND** when the same fake sits on PATH too, the PATH copy is used
Pinned by: `tests/unit/test_detect.py::test_detect_codex_in_app_bundle`

#### Scenario: Keys and sign-in reported by name only
- **WHEN** `SOME_KEY_VAR` holds a secret value, a preset names `SOME_KEY_VAR`, and the `codex` sign-in file exists with secret content
- **THEN** that preset's row reports `SOME_KEY_VAR` as set, and the `codex` row says a sign-in file was found
- **AND** neither the secret value nor the file's content appears in the output, and no request goes to a host other than 127.0.0.1
Pinned by: `tests/unit/test_detect.py::test_detect_reports_keys_and_sign_in_by_name_only`

#### Scenario: Home folder hidden and slow servers skipped
- **WHEN** a CLI is found inside the home folder and a local port accepts connections but never answers
- **THEN** the CLI's location is shown with the home folder written as `~`
- **AND** detection still finishes, reporting that server as not answering
Pinned by: `tests/unit/test_detect.py::test_detect_hides_home_and_times_out`

#### Scenario: Detection only on request
- **WHEN** the model page, the Ranking page, the Screening page or Settings opens
- **THEN** no detection request is made and no CLI is run
- **AND** detection runs only after "Find AI models on this computer" is pressed or `wider providers detect` runs
Pinned by: `tests/unit/test_backends_js_node.py::test_detect_not_run_on_open`, `tests/unit/test_model_page_node.py::test_guided_steps_on_first_visit`, `tests/unit/test_pickers_js_node.py::test_embedding_picker`, `tests/unit/test_pickers_js_node.py::test_screen_picker_preselects_stage_backend`, `tests/unit/test_shell_js_node.py::test_settings_manage_backends`

### Requirement: Add a backend from a preset
The system SHALL add a backend from a preset id, taking its kind, base URL, key variable, command and `extra` from the preset; any setting may be overridden. Without a name it takes the preset id, or the first free `<id>-N`. An unknown preset id, or a preset missing a value only the researcher knows (e.g. the Azure URL), MUST be refused.
Surfaces: CLI `wider providers add --preset ID [--name] [--model] [--base-url] [--extra k=v]...`; UI preset card "Add".

#### Scenario: Codex from its preset
- **WHEN** `wider providers add --preset codex --model m1` runs
- **THEN** a `cli_agent` backend `codex` is saved with `preset: codex`, `extra.output` `codex_jsonl`, `extra.strict_schema` true and the preset's command, which contains `{schema_file}` and `developer_instructions={system_toml}`
- **AND** `wider providers list` shows it with destination "OpenAI (via Codex)"
Pinned by: `tests/unit/test_cli_setup.py::test_providers_add_from_preset`, `tests/unit/test_backends.py::test_add_from_preset`

#### Scenario: Form filled from a preset
- **WHEN** the researcher presses "Add" on the `lm-studio` card
- **THEN** the backend form, titled "Add backend", opens with the name `lm-studio`, kind `openai_compat`, base URL `http://127.0.0.1:1234/v1` and no key variable
- **AND** pressing Cancel saves nothing
Pinned by: `tests/unit/test_backends_js_node.py::test_form_values_from_preset`

#### Scenario: Missing value or unknown preset
- **WHEN** `wider providers add --preset azure-openai --model m1` runs without `--base-url`, `--preset nope` is given, or `--preset openai` runs without `--model`
- **THEN** the first exits with code 1 saying the preset needs a base URL, the second exits with code 1 listing the preset ids, and the third exits with code 1 saying "choose a model" and naming `wider providers models --preset openai`
- **AND** `POST /api/providers` refuses all three with status 400, and nothing is saved
Pinned by: `tests/unit/test_cli_setup.py::test_preset_refusals`, `tests/unit/test_cli_setup.py::test_cloud_preset_needs_model`, `tests/unit/test_setup_routes.py::test_provider_from_preset_refusals`

#### Scenario: Preset id already used by a default backend
- **WHEN** in a new project, which already holds the default backend `ollama`, `wider providers add --preset ollama --model m1` runs without `--name`
- **THEN** a backend `ollama-2` is saved, the command prints "added as ollama-2" and points to `wider providers edit ollama`
- **AND** the existing `ollama` backend is unchanged
Pinned by: `tests/unit/test_cli_setup.py::test_preset_default_name_takes_free_suffix`

### Requirement: Edit a backend without losing its settings
The system SHALL change only the given settings when a backend is edited and keep all others, including every `extra` key not named. `--extra key=value` is read as JSON when it parses and as text otherwise, and `key=` removes the key. The merged result is validated before saving. The name and kind cannot be edited, and editing an unknown backend MUST be refused.
Surfaces: CLI `wider providers edit NAME` with the `providers add` options; UI backend card "Edit".

#### Scenario: Edit keeps the rest
- **WHEN** backend `a`, with timeout 900, concurrency 2, input price 1 and `extra.think` false, is edited with `--model m2 --extra json_mode=object`
- **THEN** `a` has model `m2` and `extra.json_mode` `object`, and still has timeout 900, concurrency 2, input price 1 and `extra.think` false
- **AND** `PATCH /api/providers/a` with only a model gives the same result
Pinned by: `tests/unit/test_backends.py::test_update_provider_keeps_fields`, `tests/unit/test_cli_setup.py::test_providers_edit`, `tests/unit/test_setup_routes.py::test_patch_provider_keeps_fields`

#### Scenario: Edit form shows the current values
- **WHEN** the researcher presses "Edit" on a backend `a`
- **THEN** the form, titled "Edit a", shows its current model, base URL, key variable, timeout, concurrency, temperature, prices and `extra` settings
- **AND** pressing Cancel saves nothing
Pinned by: `tests/unit/test_backends_js_node.py::test_edit_form_shows_current_values`

#### Scenario: Remove an extra key, unknown backend, invalid edit
- **WHEN** `wider providers edit a --extra think=` runs, then `wider providers edit nope --model m2`, then `wider providers edit a --timeout 0`
- **THEN** `a` no longer has `extra.think`
- **AND** the second command exits with code 1 and `PATCH /api/providers/nope` answers 404
- **AND** the third exits with code 1 naming the timeout, `PATCH /api/providers/a` with a negative price answers 400, and `a` is unchanged
Pinned by: `tests/unit/test_cli_setup.py::test_providers_edit`, `tests/unit/test_setup_routes.py::test_patch_provider_keeps_fields`, `tests/unit/test_backends.py::test_update_provider_validates`

### Requirement: OpenAI-compatible endpoint options
The system SHALL let an `openai_compat` backend use these `extra` settings:
- send its key as `Authorization: Bearer` (the default), as an `api-key` header, or not at all (`none`);
- add an `api-version` query;
- call an Azure deployment path;
- leave out named request parameters;
- name the max-tokens parameter.

`extra.headers` MUST NOT set the key headers unless `extra.auth` is `none`.
Surfaces: CLI `wider providers add|edit --extra k=v`; UI backend form, Advanced section.

#### Scenario: Azure deployment
- **WHEN** a backend with base URL `https://res.example.test/openai`, `extra.auth` `api-key`, `extra.deployment` `d1`, `extra.api_version` `2024-10-21` and key variable `SOME_KEY_VAR` (set to a synthetic value) is called
- **THEN** the request goes to `https://res.example.test/openai/deployments/d1/chat/completions?api-version=2024-10-21` with the key in the `api-key` header
- **AND** the request has no `Authorization` header
Pinned by: `tests/unit/test_http_adapters.py::test_azure_deployment_request`

#### Scenario: Reasoning model parameters
- **WHEN** a backend with `extra.max_tokens_param` `max_completion_tokens` and `extra.omit_params` `["temperature", "seed"]` is called
- **THEN** the request body holds `max_completion_tokens`
- **AND** it holds no `max_tokens`, `temperature` or `seed`
Pinned by: `tests/unit/test_http_adapters.py::test_reasoning_model_params`

#### Scenario: Gemini through its OpenAI-compatible endpoint
- **WHEN** a backend added from the `gemini` preset is called with `GEMINI_API_KEY` set to a synthetic value
- **THEN** the request goes to `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` with that value as a Bearer token
Pinned by: `tests/unit/test_http_adapters.py::test_gemini_preset_request`

#### Scenario: No auth header
- **WHEN** a backend at `https://models.example.test/v1` with `extra.auth` `none` and key variable `SOME_KEY_VAR` (set to a synthetic value) is called
- **THEN** the request has no `Authorization`, `api-key` or `x-api-key` header
Pinned by: `tests/unit/test_http_adapters.py::test_auth_none_sends_no_key`

#### Scenario: Auth header in extra refused
- **WHEN** a backend with the default `extra.auth` is added or edited with `extra.headers` containing `Authorization`, `api-key` or `x-api-key`
- **THEN** it is refused with a message saying the key is set by its variable name (`--key-env`), and nothing is saved
- **AND** with `extra.auth` `none`, `Authorization: {env:SOME_HEADER_VAR}` is accepted and a plain value is refused
Pinned by: `tests/unit/test_backends.py::test_extra_auth_headers_refused`

### Requirement: Agentic CLI sign-in failures and stopped calls
The system SHALL fail a CLI call at once, without retrying, when its exit code or output shows that the CLI is not signed in. The message names the CLI and the sign-in command to run in a terminal. A CLI call that times out, or whose job is cancelled, MUST stop its process before the call ends.
Surfaces: CLI `wider providers probe`, `wider screen ...`, `wider construct propose`; UI LLM backends card "Probe", job bar "Cancel".

#### Scenario: Not signed in
- **WHEN** a fake `gemini` executable used by a `gemini-cli` backend exits with code 41, and a fake `agy` used by an `antigravity` backend prints "authentication required"
- **THEN** each call fails after 1 attempt, the first with a message that begins "Gemini CLI is not signed in" and the second with one that begins "Antigravity CLI is not signed in", each naming the sign-in step
Pinned by: `tests/unit/test_cli_agent.py::test_not_signed_in_is_not_retried`

#### Scenario: Timeout and cancel stop the process
- **WHEN** a fake CLI that never answers outlives the backend's timeout, and in a second run its job is cancelled
- **THEN** both calls fail, the first as timed out
- **AND** after each call no process of the fake CLI is running
Pinned by: `tests/unit/test_cli_agent.py::test_timeout_kills_child`, `tests/unit/test_cli_agent.py::test_cancel_kills_child`

### Requirement: List a backend's models
The system SHALL list a backend's models: a server on this computer's loopback address on its own, any other server's list only when the researcher asks for it, and a CLI or Jev preset's list from the catalogue. A failed listing MUST be reported as a message, and a model name can always be typed instead.
Surfaces: CLI `wider providers models NAME | --preset ID | --kind KIND [--base-url URL]`; UI backend form, model dropdown with "Load models from <provider>" and free text.

#### Scenario: Models of a local server
- **WHEN** a fake OpenAI-compatible server on 127.0.0.1:1234 answers `/v1/models` with `m1` and `m2`
- **THEN** `wider providers models --preset lm-studio` prints `m1` and `m2`
Pinned by: `tests/unit/test_detect.py::test_list_models_local_openai_compat`, `tests/unit/test_cli_setup.py::test_providers_models_local_preset`

#### Scenario: Cloud list only on request
- **WHEN** the backend form opens for the `openai` preset
- **THEN** no request is sent, and the dropdown shows the catalogue's suggested models, "Load models from OpenAI" and a free-text entry
- **AND** `wider providers models --preset openai` with `OPENAI_API_KEY` not set, and the Anthropic listing with `ANTHROPIC_API_KEY` not set, each fail saying "environment variable … is not set", and send nothing
Pinned by: `tests/unit/test_backends_js_node.py::test_cloud_models_not_loaded_on_open`, `tests/unit/test_cli_setup.py::test_models_cloud_needs_key`, `tests/unit/test_detect.py::test_list_models_anthropic_needs_key`

#### Scenario: Local preset on another host waits for a click
- **WHEN** the Edit form opens for a backend added from the `lm-studio` preset with the base URL `https://models.example.test/v1`
- **THEN** no request is sent, and the dropdown offers "Load models from models.example.test" and a free-text entry
- **AND** for the same preset at `http://127.0.0.1:1234/v1` the list loads when the form opens
Pinned by: `tests/unit/test_backends_js_node.py::test_models_auto_load_only_on_loopback`

#### Scenario: Models for a backend filled in by hand
- **WHEN** the Add form is filled in by hand with kind `openai_compat` and base URL `http://127.0.0.1:8001/v1`, where a fake server answers `m1` and `m2`, and "Load models" is pressed
- **THEN** the request names the kind (not a preset), the dropdown lists `m1` and `m2`, and `wider providers models --kind openai_compat --base-url http://127.0.0.1:8001/v1` prints the same
- **AND** the same request for `https://gw.example.org/v1` is refused with "save the backend first" and sends nothing, and kind `cli_agent` is refused with "no server to ask"
Pinned by: `tests/unit/test_detect.py::test_list_models_by_kind_for_a_hand_filled_form`, `tests/unit/test_backends_js_node.py::test_load_models_on_hand_filled_form_sends_kind`

#### Scenario: Unreachable server
- **WHEN** the models of a backend whose server does not answer are listed
- **THEN** the listing fails with "cannot reach" and the base URL, the CLI exits with code 1, and the UI shows that message and keeps the free-text entry
Pinned by: `tests/unit/test_detect.py::test_list_models_unreachable`, `tests/unit/test_setup_routes.py::test_models_route_unreachable`

### Requirement: Embedding presets and edit
The system SHALL add an embedding model from a preset (`openai-3-small`, `openai-3-large`, `gemini-embedding`, `ollama-embed`, `lm-studio-embed` or a local sentence-transformers model) and SHALL edit an embedding model keeping the settings not given. Adding a local model MUST NOT download weights.
Surfaces: CLI `wider embed add --preset ID`, `wider embed edit NAME [--model] [--base-url] [--api-key-env] [--dim] [--batch-size] [--price-in]`; UI Embeddings card, Add and Edit.

#### Scenario: Gemini embeddings from a preset
- **WHEN** `wider embed add --preset gemini-embedding` runs
- **THEN** an `openai_compat` model with model `gemini-embedding-001`, base URL `https://generativelanguage.googleapis.com/v1beta/openai` and key variable `GEMINI_API_KEY` is saved and listed as remote
Pinned by: `tests/unit/test_cli_setup.py::test_embed_add_from_preset`

#### Scenario: Edit keeps the other settings
- **WHEN** an embedding model `e1` with dimension 64, batch size 16 and an input price is edited to batch size 32
- **THEN** `e1` has batch size 32 and still has dimension 64 and its input price
- **AND** `PATCH /api/embeddings/e1` with only a batch size gives the same result
Pinned by: `tests/unit/test_backends.py::test_update_embedding_keeps_fields`, `tests/unit/test_setup_routes.py::test_patch_embedding_keeps_fields`

#### Scenario: Embedding edit form
- **WHEN** the researcher presses "Edit" on the embedding model `e1`
- **THEN** the form shows its model, dimension, batch size, base URL, key variable and input price
- **AND** pressing Cancel saves nothing
Pinned by: `tests/unit/test_backends_js_node.py::test_embedding_edit_form_shows_current_values`

### Requirement: Embedding API calls are retried and logged
The system SHALL retry an embedding API call up to 3 times after a timeout, a dropped connection, HTTP 429 or 5xx. It SHALL log each embedding API call (`openai`, `openai_compat`, `ollama`) made inside a usage scope in `llm_usage`, with its input tokens and cost and without any text. In-process local models are not logged. Computing corpus embeddings runs inside the `rank` usage scope.
Surfaces: CLI `wider embed corpus`, `wider costs`; UI Rank page embed step, cost cards.

#### Scenario: Retried, then logged
- **WHEN**:
  - an embedding model priced at 0.02 USD per million input tokens calls a fake endpoint inside a `rank` scope;
  - the endpoint answers 503 once, then the vectors with `usage.prompt_tokens` 120.
- **THEN** the call succeeds on the second try
- **AND** one `llm_usage` row has stage `rank`, prompt tokens 120, completion tokens 0 and cost 0.0000024, and holds no text
Pinned by: `tests/unit/test_embeddings.py::test_embedding_retry_and_usage`

#### Scenario: Fatal error not retried
- **WHEN** the fake endpoint answers 401
- **THEN** the call fails after 1 attempt with "HTTP 401"
Pinned by: `tests/unit/test_embeddings.py::test_embedding_auth_error_not_retried`

### Requirement: Jev as a screening-only backend
The system SHALL call Jev (kind `typesafe`) for screening by turning the schema's booleans, string enums and small integer ranges into Jev questions; text fields are left empty and listed as unanswered. A schema with nothing Jev can answer fails without retrying. Construct and adjacent proposals MUST refuse a Jev backend.
Surfaces: CLI `wider providers add --preset jev`, `wider screen ...`, `wider providers probe`; UI preset card "Jev", screening run form.

#### Scenario: Screening schema answered
- **WHEN** a `jev` backend calls a fake endpoint with a schema holding an enum `decision`, a boolean `c1` and a string `summary`, and the endpoint answers `decision` `include` and `c1` with probability 0.8
- **THEN** the request goes to `https://api.typesafe.ai/v1/systemone` with a Bearer key from `TYPESAFE_API_KEY` and two questions, `choice` and `noul`
- **AND** the answer has `decision` `include`, `c1` true, `summary` empty, `meta.unanswered` `["summary"]` and the probabilities in `meta`
Pinned by: `tests/unit/test_typesafe.py::test_questions_from_schema_and_answer`

#### Scenario: Nothing to answer, and text stages
- **WHEN** a `jev` backend gets a schema with only string fields, and a `jev` backend is used for `construct propose` or set as the `construct` override
- **THEN** the first fails after 1 attempt with "Jev cannot answer this schema"
- **AND** the others are refused with "Jev cannot write text; choose a generative backend"
- **AND** `wider construct adjacent propose --provider jev` exits with status 1 and the same message, and sends nothing
Pinned by: `tests/unit/test_typesafe.py::test_unanswerable_schema`, `tests/unit/test_typesafe.py::test_text_stages_refuse_jev`

#### Scenario: Jev probe
- **WHEN** a `jev` backend is probed against a fake endpoint that answers 0.9 for "17 + 25 = 42" and 0.1 for "17 + 25 = 41"
- **THEN** the probe is ok
- **AND** when both answer 0.9, the probe fails with "wrong answer"
Pinned by: `tests/unit/test_typesafe.py::test_jev_probe`

### Requirement: Locality of Ollama models
The system SHALL class an Ollama model on a local host from the local `/api/tags` only, read at 127.0.0.1: a remote field on its entry makes it `cloud`; else a `-cloud` name suffix makes it `cloud`; else a listed model is `local`; otherwise it is `unknown`. A known cloud model is never `local`, and no cloud request is made to find out. The locality and how it was found are recorded with each run.
Surfaces: CLI `wider providers detect`, `wider costs --by backend`; UI backend cards, run rows.

#### Scenario: Ollama routing
- **WHEN** a fake `/api/tags` on 127.0.0.1 lists `m1:7b` plainly, `m2` with a remote host, and `m3:7b-cloud` plainly, and an Ollama backend uses each of them and then the unlisted `m4`
- **THEN** their localities are `local`, `cloud`, `cloud` and `unknown`, with sources `ollama_tags`, `ollama_tags`, `name_suffix` and `not_established`
- **AND** `unknown` is shown as "unknown", never as local, and only 127.0.0.1 is contacted
Pinned by: `tests/unit/test_detect.py::test_ollama_routing`

#### Scenario: Ollama not answering
- **WHEN** the local Ollama on 127.0.0.1 does not answer and the model name has no `-cloud` suffix
- **THEN** the locality is `unknown` with source `not_established`, no consent is asked, the run is refused with "cannot reach 127.0.0.1:11434", and no chat request is sent
- **AND** when Ollama answers but does not list the model, the locality is `unknown` and screening with it asks for cloud consent first
Pinned by: `tests/unit/test_detect.py::test_ollama_unreachable_unknown`, `tests/unit/test_backend_consent.py::test_unreachable_loopback_no_consent`

#### Scenario: Ollama addressed as localhost
- **WHEN** an Ollama backend at `http://localhost:18431` uses a model that the fake `/api/tags` on 127.0.0.1:18431 marks with a remote host
- **THEN** it is `cloud` and needs consent before a probe or any other call, exactly as at `http://127.0.0.1:18431`, and only 127.0.0.1 is contacted
- **AND** the same backend at `http://[::1]:18431` is not read and needs consent (it is not treated as unreachable)
Pinned by: `tests/unit/test_detect.py::test_ollama_localhost_routing_is_read_and_needs_consent`

#### Scenario: The usage log takes the routing
- **WHEN** a screening call on `m3:7b-cloud` goes through an Ollama backend on 127.0.0.1 whose fake `/api/tags` lists it, with consent given and no price
- **THEN** its `llm_usage` row has locality `cloud` and cost not priced, never `local` or 0
Pinned by: `tests/unit/test_runs.py::test_usage_row_takes_ollama_routing`

### Requirement: Experimental backends
The system SHALL mark a preset as experimental with its limitations. An experimental preset is never a default or preselected, its card names the limitations, and adding it needs an explicit acknowledgement. Results from it are marked "experimental backend: not validated" wherever shown or exported. `antigravity` and `gemini-cli` (isolation unverified) are experimental.
Surfaces: CLI `wider providers presets|add --accept-experimental`; UI backend cards and form, result cards.

#### Scenario: Adding needs an acknowledgement
- **WHEN** `wider providers add --preset antigravity --name ag` runs without `--accept-experimental`, or the form is saved with the box unticked
- **THEN** it is refused (exit 1, HTTP 400) naming the limitations, and `project.yaml` is unchanged
- **AND** with the acknowledgement it is added and the journal records it with its author
Pinned by: `tests/unit/test_cli_setup.py::test_experimental_needs_ack`, `tests/unit/test_setup_routes.py::test_experimental_route_needs_ack`

#### Scenario: Card and order
- **WHEN** the preset cards are listed
- **THEN** the `antigravity` card is last in the CLI group, has the badge "Experimental" and says prompts and record text are visible in process arguments and that instruction following is unverified
- **AND** `wider providers presets --json` gives it `experimental: true` with the same limitations
- **AND** the `gemini-cli` card has the same badge, says that personal Gemini settings, GEMINI.md, MCP servers or extensions may take part in each call, and shows whether it has run on this computer apart from that warning
Pinned by: `tests/unit/test_backends_js_node.py::test_experimental_card`, `tests/unit/test_presets.py::test_experimental_presets`

#### Scenario: Results are marked
- **WHEN** a screening run uses a fake experimental CLI backend
- **THEN** the run snapshot has `experimental: true` and the limitations, and the models summary, `decisions.csv`, the evaluations export and the share bundle say "experimental backend: not validated"
Pinned by: `tests/unit/test_provenance.py::test_experimental_marked`

### Requirement: Backend rows carry locality and the last check
The system SHALL give each LLM and embedding backend row its locality, destination and last check from `project.yaml` and the store only, with no request. An Ollama LLM backend takes the locality of its last check while its model and address are unchanged, else "unknown, press Check". A card Check and `wider providers|embed probe` record the last check.
Surfaces: CLI `wider providers list [--json]`, `wider embed list [--json]`, `wider providers|embed probe`; UI picker cards, Settings.

#### Scenario: Same rows on the page and the command line
- **WHEN** a project has a cloud backend `a`, a local Ollama backend `o` and a cloud embedding `e`
- **THEN** `wider providers list --json` equals the rows of `GET /api/providers`, and `wider embed list --json` those of `GET /api/embeddings`, each with `locality`, `destination` and `last_check`
- **AND** `a` reads `cloud` with destination `api.openai.com`, and no key value appears in either output
Pinned by: `tests/unit/test_cli_setup.py::test_list_json_matches_rows`

#### Scenario: Ollama never checked
- **WHEN** `o` was never checked and `wider providers list` runs or the model page opens
- **THEN** `o` reads "unknown, press Check" and its last check is none
- **AND** no request is made, not even to 127.0.0.1
Pinned by: `tests/unit/test_backends.py::test_rows_make_no_request`, `tests/unit/test_model_page_node.py::test_guided_steps_on_first_visit`

#### Scenario: After a check
- **WHEN** `o` is checked with `wider providers probe o` against a fake local server that lists its model without remote fields
- **THEN** the row of `o` reads `local` with the time and result of that check, the same after a card Check through `POST /api/providers/o/probe`
- **AND** after `o`'s model is changed with `wider providers edit`, it reads "unknown, press Check" again
Pinned by: `tests/unit/test_backends.py::test_probe_records_last_check`

#### Scenario: Ollama on another host
- **WHEN** Ollama backends use model `m` at `http://ollama-host.example.invalid:11434` and at `http://192.168.1.20:11434`, model `m-cloud` at the first address and at 127.0.0.1, and an Ollama embedding uses the first address
- **THEN** the rows of `GET /api/providers`, `GET /api/embeddings` and `wider providers list --json` name the destinations "ollama-host.example.invalid (via Ollama)", "192.168.1.20 (another computer on your network: data leaves this computer)", "ollama.com (via Ollama at ollama-host.example.invalid)", "ollama.com (via Ollama)" and, for the embedding, "ollama-host.example.invalid (via Ollama)"
- **AND** only a cloud-tagged model names ollama.com, in the destination and in the consent endpoint, and no request is made
Pinned by: `tests/unit/test_shell.py::test_ollama_destination_names_its_host`

### Requirement: Extraction and review calls are deterministic and bounded
The system SHALL make mining calls (and typed calls that opt in, such as 2c's second reader) with temperature 0 and the project seed (a `cli_agent` backend cannot; its run record says so), allow one schema-repair round (two when the backend's `repairs` is 2), make at most three calls per item, and log each call's hashes, attempts and repairs without text. An item that still fails is reported, not retried later.
Surfaces: CLI `wider seeds mine`; UI the same action (call counts in the result).

#### Scenario: Bounded calls on a failing item
- **WHEN** the fake provider returns unusable JSON for one passage every time
- **THEN** that passage is called three times at most, reported as failed with its hashes, and the other passages succeed
- **AND** every request carried temperature 0 and the project seed, and a fake backend configured with `repairs: 2` gets two repair rounds where the default gets one
Pinned by: `tests/unit/test_seed_mining.py::test_call_rules`

#### Scenario: A CLI backend's flags are on record
- **WHEN** mining runs on a `cli_agent` backend added from the `claude-code`, `codex` or `gemini-cli` preset
- **THEN** the run record's provider snapshot (`Provider.identity()`, written to `run.json`) carries the preset's command line with the flags that disable tools (`--tools ""` and `--strict-mcp-config`; `features.shell_tool=false`, `web_search="disabled"` and `--sandbox read-only`; `-e none --approval-mode plan`)
- **AND** the usage log holds counts and the model, never the passage (hashes are in the run's `calls.json`)
- **AND** a CLI backend whose command lacks those flags for its tool (an edited command, `antigravity`, `custom-cli`, an unknown tool) gets no passage: the model pass is skipped with a message naming the missing flags or the tool
Pinned by: `tests/unit/test_seed_mining.py::test_cli_backend_flags_in_run_record`
