# abstract-enrichment Specification

## Purpose
Abstract enrichment (stage key `enrich`) runs after deduplication and fills missing or thin abstracts of unique records from public bibliographic sources, so ranking and screening can read an abstract for as many records as possible. It tries OpenAlex, Crossref, Europe PMC and Semantic Scholar in order, never replaces a longer abstract, logs every attempt in `enrichment_log` and can be resumed at any point.

## Requirements

### Requirement: Candidates are thin records not yet settled
The system SHALL treat as candidates the records in `records` whose `abstract_words` (missing counts as 0) is below `thin_words` (default 20) and that have not been tried yet (a corrected DOI counts as not tried), in `record_id` order, and SHALL process at most `--limit` of them per run when a limit is given. A record whose last attempt is `filled`, `exhausted` or `failed` MUST NOT be a candidate.
Surfaces: CLI `wider enrich [--limit N]`; UI Enrich page "Fetch next N" (batch size, default 100).

#### Scenario: Two thin records after deduplication
- **WHEN** a deduplicated corpus has two records with no abstract
- **THEN** both are candidates
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

#### Scenario: Failed records wait for a retry
- **WHEN** a record's last attempt is `failed`
- **THEN** it is not a candidate of the next run without retry, and the summary counts it as failed

### Requirement: Sources are tried by DOI in protocol order
The system SHALL look up a record with a DOI in the order of `protocol.enrichment.sources` (default `openalex`, `crossref`, `europepmc`, `semantic_scholar`) and use the first source returning an abstract of at least `thin_words` words. Markup and a leading "Abstract:" or "Summary:" are removed; `WIDER_MAILTO` and `S2_API_KEY` are sent when set.
Surfaces: CLI `wider enrich`; UI Enrich page.

#### Scenario: First source too short
- **WHEN** OpenAlex returns a 1-word abstract and Crossref a 25-word abstract for a DOI
- **THEN** the record is filled from `crossref`
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

### Requirement: Title search fallback for records without a DOI
The system SHALL, for a record without a DOI and a title of at least 15 characters, search OpenAlex by title and accept a hit only when it has an abstract, its normalised title has Jaro-Winkler similarity of at least `title_search_min_jw` (default 0.95) and, when both years are known, the years differ by at most 1. Such a fill has source `openalex_title`.
Surfaces: CLI `wider enrich`; UI Enrich page.

#### Scenario: Record without DOI
- **WHEN** a record without a DOI matches an OpenAlex work by title with a 30-word abstract
- **THEN** the record is filled with `abstract_source` `openalex_title`
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

### Requirement: Never overwrite a longer abstract
The system MUST update a record only when the found abstract has at least `thin_words` words and more words than the current abstract, and then SHALL set `abstract`, `abstract_words`, `thin` = 0 and `abstract_source` to the source name, and fill `doi` from the hit only when the record had none.
Surfaces: CLI `wider enrich`; UI Enrich page.

#### Scenario: DOI learned from a title hit
- **WHEN** a record without a DOI is filled through the title search
- **THEN** its `doi` is set to the DOI of the OpenAlex work (`10.1000/found`)
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

#### Scenario: Short hit
- **WHEN** every source returns fewer than `thin_words` words
- **THEN** the record's abstract is unchanged and it is marked `exhausted`

### Requirement: Every attempt is logged
The system SHALL write one `enrichment_log` row per record tried, with the source used (or `none`), status `filled`, `exhausted` or `failed`, word count, `fetched_at` and a JSON note listing each source's hit, miss or error (`no_doi` when the record had no DOI). Rows are never deleted by a later run or a retry.
Surfaces: CLI `wider enrich`; UI Enrich page (filled by source).

#### Scenario: Nothing found
- **WHEN** no source returns an abstract for two records
- **THEN** both get an `exhausted` row and the run reports `exhausted` 2
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

#### Scenario: Errors are logged per source
- **WHEN** a lookup fails with a timeout at one source
- **THEN** the record's note lists that source with status `error` and a short error description, and no response body is stored

### Requirement: Runs are resumable and exhausted records can be retried
The system SHALL skip records whose last attempt is `filled`, `exhausted` or `failed` on later runs, so an interrupted or limited run continues where it stopped, and SHALL, when the researcher asks for a retry, try again in that run the records whose last attempt is `failed` or `exhausted`. A retry MUST keep every earlier `enrichment_log` row and add a row per record tried.
Surfaces: CLI `wider enrich --retry` (prints "retrying N records"); UI Enrich page "Retry failed lookups".

#### Scenario: Second run
- **WHEN** enrichment runs again after both candidates were filled
- **THEN** it tries 0 records
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

#### Scenario: Retry keeps the audit log
- **WHEN** one record is `failed` and one `exhausted`, and the researcher runs `wider enrich --retry` and the first is now filled
- **THEN** the first record has two `enrichment_log` rows, `failed` then `filled`, and the second has two `exhausted` rows
- **AND** the summary counts the first as filled and the second once as exhausted

#### Scenario: Retry from the Enrich page
- **WHEN** the summary shows failed records and the researcher clicks "Retry failed lookups"
- **THEN** a background job tries the failed and exhausted records again with the same batch size, as `wider enrich --retry --limit N` does

### Requirement: Unresolved records become questions
The system SHALL raise an `enrich_unresolved` question for each exhausted record, at most 50 per run, offering to correct the DOI, paste the abstract or leave it. A corrected DOI makes the record a candidate again; a pasted abstract is stored with `abstract_source` `researcher` and a `filled` log row.
Surfaces: CLI `wider enrich` (answers through `wider questions`); UI Enrich page and Questions page.

#### Scenario: Answer with a DOI and with an abstract
- **WHEN** one exhausted record's question is answered with `https://doi.org/10.1000/Corrected` and another's with a 40-word abstract
- **THEN** the first record's DOI is `10.1000/corrected` and it is a candidate again
- **AND** the second has 40 abstract words, `abstract_source` `researcher` and `thin` 0
Pinned by: `tests/unit/test_questions.py::test_enrichment_and_seed_questions`

### Requirement: Enrichment summary
The system SHALL report the number of records, thin records, filled, exhausted, failed and pending lookups, fills per source and abstract origin per `abstract_source`. Filled, exhausted and failed MUST count each record once, by its last attempt.
Surfaces: CLI `wider enrich --status`; UI Enrich page summary table (`GET /api/enrich`).

#### Scenario: All filled
- **WHEN** both thin records have been filled
- **THEN** the summary shows 0 thin and 0 pending
Pinned by: `tests/unit/test_enrich.py::test_enrich_fills_and_logs`

#### Scenario: Failed records shown with the retry button
- **WHEN** three records are `failed`
- **THEN** the summary shows failed 3 and the Enrich page shows "Retry failed lookups"

### Requirement: Background run with live progress
The system SHALL run enrichment from the UI as a background job "Fetching abstracts (up to N)" with progress per record, where N is the batch size set on the Enrich page (default 100).
Surfaces: UI Enrich page "Fetch next N" (`POST /api/jobs/enrich?limit=N`); CLI `wider enrich` runs in the foreground.

#### Scenario: Start from the Enrich page
- **WHEN** the researcher clicks "Fetch next 100" with the default batch size
- **THEN** a background job looks up at most 100 candidates and the summary refreshes when it ends

### Requirement: Stage result
The system SHALL record an `enrich` stage result (action `fetch`) at the end of every run with the headline "Looked up N records and filled M abstracts; K thin or missing remain.", or "Nothing to fetch: every record already has an abstract or was tried." when there were no candidates, metrics tried, filled, exhausted and still thin, and warnings when nothing was filled or records are still pending.
Surfaces: CLI `wider enrich`; UI Enrich page stage result banner.

#### Scenario: Run that fills nothing
- **WHEN** a run tries records and no source returns an abstract
- **THEN** the stored `enrich` stage result warns "No abstract was found: check the network connection and the enrichment sources."

#### Scenario: Nothing left
- **WHEN** a run starts with no candidates
- **THEN** the headline starts with "Nothing to fetch"

### Requirement: Enrichment needs online-lookup consent
The system MUST NOT start an enrichment run without the project's online-lookup consent. Without it, `wider enrich` asks (y/N) in an interactive terminal and otherwise exits with code 2 naming `wider lookups allow`; the Enrich page shows the consent dialog. `wider enrich --status` sends nothing and needs no consent.
Surfaces: CLI `wider enrich [--limit N] [--retry]`; UI Enrich page fetch and "Retry failed lookups" buttons.

#### Scenario: Script without consent
- **WHEN** `wider enrich --limit 10` runs non-interactively in a project with consent off
- **THEN** it exits with code 2 before any lookup
- **AND** `enrichment_log` and every record are unchanged and no `enrich` stage result is recorded

#### Scenario: Status without consent
- **WHEN** `wider enrich --status` runs with consent off
- **THEN** it prints the summary and exits 0

### Requirement: Network and service errors are retryable failures
The system SHALL log a record as `failed`, not `exhausted`, when no usable abstract was found and at least one lookup could not be completed: a connection error, a timeout, an unreadable answer, a rate limit (HTTP 429) or another HTTP error except 404, which means not found. A `failed` record MUST NOT raise a question and is tried again only on retry.
Surfaces: CLI `wider enrich`, `wider enrich --retry`; UI Enrich page, summary and "Retry failed lookups".

#### Scenario: Connection lost during a run
- **WHEN** every lookup of two thin records fails with a connection error
- **THEN** both records get a `failed` row, the run reports failed 2 and exhausted 0
- **AND** no `enrich_unresolved` question is raised and the next run without retry does not try them

#### Scenario: One source down, the others answer
- **WHEN** OpenAlex answers HTTP 503 for a DOI and Crossref, Europe PMC and Semantic Scholar report it not found
- **THEN** the record is `failed`, and its log note lists the error for `openalex` and a miss for each other source

#### Scenario: Not found everywhere
- **WHEN** every source answers HTTP 404 or returns no abstract for a DOI
- **THEN** the record is `exhausted` and an `enrich_unresolved` question is raised as before

#### Scenario: Stage result names the failures
- **WHEN** a run ends with failed records
- **THEN** the stored `enrich` stage result warns that N lookups failed because a service could not be reached and that they can be retried with `wider enrich --retry` or "Retry failed lookups"

### Requirement: Batch size on the Enrich page
The system SHALL let the researcher set the number of records per run on the Enrich page (a whole number of at least 1, default 100) and label the button "Fetch next N". A batch size below 1 or not a whole number MUST be refused (button disabled, HTTP 400 from the run routes) and MUST NOT mean "no limit".
Surfaces: UI Enrich page batch size field and "Fetch next N" (`POST /api/jobs/enrich?limit=N`); CLI `wider enrich --limit N`.

#### Scenario: Smaller batch
- **WHEN** the researcher sets the batch size to 25 and clicks "Fetch next 25"
- **THEN** a background job looks up at most 25 candidates

#### Scenario: Batch size zero
- **WHEN** the run route is called with `limit=0` or `limit=-5`
- **THEN** it answers HTTP 400 and no job starts
- **AND** `wider enrich --limit 0` is refused as a usage error (exit code 2) without looking anything up

### Requirement: Readable source counts on the Enrich page
The system SHALL show the "filled by source" and "abstract origin" counts on the Enrich page as words, each source followed by its count and separated by commas, or "none yet" when there is none. Raw JSON MUST NOT be shown.
Surfaces: UI Enrich page, summary table; CLI `wider enrich --status` prints the same counts (as a dictionary).

#### Scenario: Counts after a run
- **WHEN** 3 abstracts came from OpenAlex and 62 records had one in the export
- **THEN** the abstract origin row reads "openalex 3, export 62"
- **AND** with no counts it reads "none yet"
Pinned by: `tests/unit/test_static_followup.py::test_enrich_counts_formatter_under_node`, `tests/unit/test_static_followup.py::test_enrich_source_counts_are_readable_not_json`

### Requirement: One lookup per press of Fetch
The system SHALL start at most one lookup from the Enrich page at a time: pressing "Fetch next N" or "Retry" again while the online-lookup consent dialog or the start request is still open MUST NOT open a second dialog or start a second job.
Surfaces: UI Enrich page, "Fetch next N" and "Retry failed lookups" buttons; CLI `wider enrich` runs one lookup per command.

#### Scenario: Fetch pressed three times
- **WHEN** the researcher presses "Fetch next 100" three times before the first start has answered
- **THEN** one start request is sent
- **AND** once it has answered, the next press starts a lookup again
Pinned by: `tests/unit/test_static_followup.py::test_fetch_pressed_again_while_starting_starts_one_lookup`

#### Scenario: Fetch after a cancelled dialog or a failed start
- **WHEN** Fetch is pressed, the consent dialog is cancelled, Fetch is pressed again and the start request fails, then Fetch is pressed a third time and allowed
- **THEN** each press asks again and starts at most one lookup
- **AND** the page says what happened after each press
Pinned by: `tests/unit/test_ui_followup_r30.py::test_fetch_after_a_cancelled_dialog_or_a_failed_start_asks_again`, `tests/unit/test_ui_followup_r31.py::test_fetch_after_a_cancelled_dialog_or_a_failed_start_asks_again_through_the_real_request`

### Requirement: Semantic Scholar key named on the Missing abstracts page
The system SHALL say, on the Missing abstracts page and in `wider enrich --status`, that a Semantic Scholar API key is read from the environment variable `S2_API_KEY` and whether it is set ("S2_API_KEY set" or "S2_API_KEY not set; lookups work without it, but Semantic Scholar may limit the rate, and records it turned away wait for `wider enrich --retry`"). The value MUST never be shown, logged or written.
Surfaces: CLI `wider enrich --status`, `wider enrich --help`; UI Missing abstracts page.

#### Scenario: Key set and not set
- **WHEN** `S2_API_KEY` holds a synthetic secret value, and then is unset
- **THEN** `GET /api/enrich` and `wider enrich --status` say "S2_API_KEY set", then "S2_API_KEY not set"
- **AND** the secret value appears in neither output, and `wider enrich --help` names `S2_API_KEY`
Pinned by: `tests/unit/test_stage_pages.py::test_s2_key_named_never_shown`

### Requirement: Every abstract version is kept, and each judgment names the one it used
The system SHALL keep every abstract a record has had as a numbered version with its source, words and date (version 1 the imported one), add a version whenever a fill, a pasted abstract or a dedup re-run changes it, and store with each model call and each label the version it read; the current abstract is the latest. The drawer and the CLI show a record's versions.
Surfaces: CLI `wider enrich --versions RECORD_ID [--json]`; UI record drawer, "Abstract versions".

#### Scenario: Versions kept on fill
- **WHEN** a synthetic record imported with a 12-word abstract is screened, then filled from OpenAlex with 240 words, then labelled by R1
- **THEN** `wider enrich --versions <id> --json` lists version 1 (imported, 12 words, import date) and version 2 (OpenAlex, 240 words, fill date), the model call names version 1 and R1's label version 2
- **AND** the drawer shows "Abstract versions: 2" and, when opened, the same two lines; the current abstract is unchanged from today's behaviour
Pinned by: `tests/unit/test_abstract_versions.py::test_versions_kept_on_fill`, `tests/unit/test_abstract_versions.py::test_drawer_lists_versions_node`

#### Scenario: An existing project gains its versions
- **WHEN** a project made before this change, with one record filled from Crossref and one never enriched, is opened
- **THEN** the filled record has version 1 from the imported abstract (empty if it had none) and version 2 from Crossref with the fill date from `enrichment_log`, and the other only version 1
- **AND** an earlier model call or label is given the version in force at its time (a call made before the Crossref fill date gets version 1), and one with no time to match is left without a version and shown as "abstract version not recorded"
Pinned by: `tests/unit/test_abstract_versions.py::test_upgrade_builds_versions`

#### Scenario: Pasted and deduplicated abstracts are versions too
- **WHEN** the researcher pastes an abstract for a record as an answer to a question, and a later dedup re-run brings a longer export abstract for another record and merges a third into a fourth
- **THEN** the first record gains a version with source "pasted by the researcher", the second a version with source "export", and the merged-away record's versions follow the survivor's, numbered after them; the labels and model calls moved with them name the renumbered versions, and the survivor's latest version is its current abstract
Pinned by: `tests/unit/test_abstract_versions.py::test_versions_kept_on_paste`, `tests/unit/test_abstract_versions.py::test_versions_kept_on_dedup_rerun`

#### Scenario: Bad record id
- **WHEN** `wider enrich --versions 99999` runs for a record that does not exist
- **THEN** it prints "no record 99999" and exits with code 1, and `/api/records/99999/abstracts` answers 404
- **AND** `wider enrich --versions` for any record asks no consent, builds no HTTP client and looks nothing up
Pinned by: `tests/unit/test_abstract_versions.py::test_unknown_record`
