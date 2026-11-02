# online-lookups Specification

## Purpose
Online lookups send identifiers of papers (DOIs and titles) to outside metadata services to resolve seed papers and fetch abstracts. This capability makes every such lookup wait for the researcher's per-project consent, tells the researcher which services receive which fields before anything is sent, and keeps a local log of every outside request.

## Requirements

### Requirement: Per-project consent for online lookups, off by default
The system SHALL keep one consent per project, `online_lookups.allowed` in `project.yaml`, off by default, with the time it was given. It covers seed-paper resolution (OpenAlex, Crossref), seed abstract fetch and abstract enrichment (OpenAlex, Crossref, Europe PMC, Semantic Scholar). A `project.yaml` without the setting MUST load with consent off.
Surfaces: CLI `wider lookups status`; UI Settings and Setup, "Online lookups" card.

#### Scenario: New project
- **WHEN** a project is created
- **THEN** `wider lookups status` reports online lookups as off
- **AND** the "Online lookups" card on the Settings page shows the switch off

#### Scenario: Project created before consent existed
- **WHEN** a project whose `project.yaml` has no `online_lookups` setting is opened
- **THEN** it loads without error and online lookups are off
- **AND** no other setting of the project changes

### Requirement: No outside lookup request without consent
The system MUST check consent before a seed-paper resolve, a seed abstract fetch or an enrichment run sends anything. Without consent, no request leaves the computer, no record, seed paper or log entry changes, and no lookup job starts.
Surfaces: CLI `wider benchmark resolve|fetch`, `wider benchmark add --resolve`, `wider enrich`; UI Setup seed-papers step, Construct "Fetch missing abstracts", Enrich page.

#### Scenario: Enrichment without consent
- **WHEN** a project with consent off and two thin records runs enrichment in a non-interactive run
- **THEN** no request is made to any lookup service
- **AND** both records keep their abstracts, `enrichment_log` stays empty and no outside request is logged

#### Scenario: Local commands are not affected
- **WHEN** consent is off and the researcher runs `wider enrich --status` or `wider benchmark list`
- **THEN** each command runs as before and exits 0, because it sends nothing

### Requirement: Consent prompt in an interactive terminal
The system SHALL, when a lookup command starts without consent in an interactive terminal, ask once with a (y/N) prompt naming the services, the fields sent (the DOI and title of each paper, and the `WIDER_MAILTO` address when set) and the number of papers. Yes MUST store the consent in the project and continue; any other answer MUST send nothing and exit with code 2.
Surfaces: CLI `wider benchmark resolve|fetch`, `wider benchmark add --resolve`, `wider enrich`; no UI.

#### Scenario: Researcher accepts
- **WHEN** `wider enrich` starts in a terminal without consent and the researcher answers `y`
- **THEN** the prompt named OpenAlex, Crossref, Europe PMC and Semantic Scholar and the fields DOI and title
- **AND** `project.yaml` now records the consent and the enrichment run continues

#### Scenario: Researcher declines
- **WHEN** the researcher answers `n` or just presses Enter
- **THEN** the command prints that nothing was sent and exits with code 2
- **AND** consent stays off, so the next lookup asks again

### Requirement: Non-interactive runs without consent exit with code 2
The system SHALL, when a lookup command runs without consent and without an interactive terminal (a script or a pipe), print a message naming the services, the fields sent and the command `wider lookups allow`, and exit with code 2 before changing anything. `wider benchmark add --resolve` MUST add no paper in that case.
Surfaces: CLI `wider benchmark resolve|fetch`, `wider benchmark add --resolve`, `wider enrich`; no UI.

#### Scenario: Seed resolve in a script
- **WHEN** `wider benchmark resolve` runs with its input not a terminal and consent off
- **THEN** it exits with code 2 and the message names OpenAlex, Crossref, the DOI and title fields and `wider lookups allow`
- **AND** `benchmark.csv` is unchanged

#### Scenario: Add with resolve in a script
- **WHEN** `wider benchmark add --doi 10.1000/synth.001 --resolve` runs non-interactively with consent off
- **THEN** it exits with code 2 and `benchmark.csv` holds no new paper
- **AND** the message says the paper can be added without `--resolve` and resolved after consent is given

### Requirement: Consent dialog in the UI
The system SHALL, when the researcher starts a lookup in the UI without consent, show a dialog naming the services and the fields sent, with "Allow and continue" and "Cancel". Allowing MUST store the consent and start the lookup; cancelling MUST send nothing. A lookup route called without consent MUST answer HTTP 403 naming the missing consent and start no job.
Surfaces: UI Setup seed-papers step, Construct "Fetch missing abstracts", Enrich page fetch and retry; CLI `wider lookups allow`.

#### Scenario: Cancel the dialog
- **WHEN** consent is off and the researcher clicks the Enrich page fetch button, then "Cancel"
- **THEN** no lookup job runs and consent stays off

#### Scenario: Allow from the dialog
- **WHEN** the researcher clicks "Allow and continue" in the same dialog
- **THEN** consent is stored in `project.yaml` and the fetch job starts
- **AND** the Settings card shows online lookups as on with the time consent was given

#### Scenario: Route called without consent
- **WHEN** `POST /api/benchmark/resolve`, `POST /api/benchmark/fetch`, `POST /api/enrich/run` or `POST /api/jobs/enrich` is called with consent off
- **THEN** each answers HTTP 403 with a message naming the missing online-lookup consent
- **AND** no job is listed and no request leaves the computer

### Requirement: Lookups commands
The system SHALL provide `wider lookups status` (on or off, since when, the services and fields, and the separate prior-review switch), `wider lookups allow` (store consent), `wider lookups revoke` (remove it) and `wider lookups log [--limit N]` (the newest outside requests, 50 by default). Each exits 0.
Surfaces: CLI `wider lookups status|allow|revoke|log`; UI Settings and Setup, "Online lookups" card.

#### Scenario: Allow, then revoke
- **WHEN** the researcher runs `wider lookups allow` and then `wider lookups status`
- **THEN** status reports online lookups as on with the time of consent
- **AND** after `wider lookups revoke` status reports them off and the next `wider enrich` asks for consent again

#### Scenario: Empty log
- **WHEN** `wider lookups log` runs in a project that never sent a lookup
- **THEN** it prints that no outside request has been logged and exits 0

### Requirement: Online lookups card in Settings and Setup
The system SHALL show an "Online lookups" card on the Settings page and on the Setup page seed-papers step with the consent state, the services, the fields sent, a switch to allow or revoke consent and the newest log entries. Changing the switch MUST store the same setting as `wider lookups allow|revoke`.
Surfaces: UI Settings page and Setup page step 2, "Online lookups" card; CLI `wider lookups status|allow|revoke|log`.

#### Scenario: Switch turned on in Settings
- **WHEN** the researcher turns the switch on in the Settings card
- **THEN** `wider lookups status` reports online lookups as on
- **AND** the Setup card shows the same state

### Requirement: Local log of every outside lookup request
The system SHALL log in `external_requests`, per lookup run and service contacted, service, purpose (`seed_resolve`, `seed_abstracts`, `enrich`, `prior_resolve`, `prior_chain`, `seed_chain`, `term_yield`, `query_yield`, `query_seed_check`, `seed_refs` or `ref_match`), the number of items sent and the time. Failures are logged too. The log MUST NOT hold response bodies. Cached answers are not outside requests.
Surfaces: CLI `wider lookups log`; UI Settings and Setup, "Online lookups" card.

#### Scenario: Enrichment run logged
- **WHEN** an enrichment run with consent sends three DOIs to OpenAlex and one of them on to Crossref
- **THEN** `external_requests` gains an `enrich` entry for OpenAlex with 3 items and one for Crossref with 1 item
- **AND** no entry holds an abstract or any other text returned by a service
Pinned by: `tests/unit/test_lookups.py::test_enrichment_run_is_logged_per_service`

#### Scenario: Prior-review lookups are logged too
- **WHEN** prior-review DOIs are resolved on OpenAlex with the prior-review lookup switch on
- **THEN** a `prior_resolve` entry for OpenAlex with the number of DOIs sent is logged
Pinned by: `tests/unit/test_lookups.py::test_prior_review_lookups_logged_and_keep_their_own_switch`

#### Scenario: Seed chaining and term yield are logged
- **WHEN** seed chaining with direction `references` resolves two seed DOIs in one request and fetches each seed's references, and a term yield sends one term
- **THEN** the log holds a `seed_chain` entry for OpenAlex with 4 items (two DOIs resolved, two reference lists) and a `term_yield` entry with 1 item, and no entry holds the term's results
- **AND** a reference-list run and a Crossref match are logged as `seed_refs` (OpenAlex) and `ref_match` (Crossref) with their items counted the same way (one seed: its DOI resolved and its reference list fetched, 2 items; one title matched, 1 item)
Pinned by: `tests/unit/test_search_provenance.py::test_new_purposes_are_logged`

### Requirement: Prior-review lookups keep their own switch
The system SHALL keep the prior-review lookup switch (`prior_reviews.allow_openalex_lookup`) separate from the online-lookup consent: giving or revoking one MUST NOT change the other, and prior-review resolve and chaining depend only on their own switch.
Surfaces: CLI `wider lookups status`, `wider prior-reviews policy`; UI Setup step 3 data-policy switches and the "Online lookups" card.

#### Scenario: Consent does not open prior-review lookups
- **WHEN** online lookups are allowed while the prior-review lookup switch is off
- **THEN** prior-review resolve is still refused before any request
- **AND** `wider lookups status` reports the prior-review switch as off and names `wider prior-reviews policy` to change it

#### Scenario: Prior-review switch does not give consent
- **WHEN** the prior-review lookup switch is on and online lookups are off
- **THEN** `wider enrich` in a non-interactive run exits with code 2

### Requirement: Consent dialog focus
The system SHALL open the online-lookup consent dialog with the keyboard focus on "Cancel", so consent is never one Enter away. Esc MUST act as Cancel. When the dialog closes, the focus SHALL return to the control that opened it, once that control is enabled again.
Surfaces: UI consent dialog (Setup seed-papers step, Construct "Fetch missing abstracts", Enrich page fetch and retry); CLI `wider lookups allow` (no dialog).

#### Scenario: Dialog opens on Cancel
- **WHEN** consent is off and the researcher starts the Enrich fetch with the keyboard
- **THEN** the dialog opens with the focus on "Cancel", not on "Allow and continue"
- **AND** the focus never rests on "Allow and continue" on the way (Cancel is the dialog's autofocus)
- **AND** Esc closes it as Cancel and nothing is sent
Pinned by: `tests/unit/test_static_followup.py::test_consent_dialog_focuses_cancel_and_returns_focus`, `tests/unit/test_drawer_keys_node.py::test_cancelling_the_consent_dialog_sends_nothing`, `tests/unit/test_ui_followup_r13.py::test_page_controls_named_and_bounded`

#### Scenario: Focus returns after the dialog
- **WHEN** the dialog closes by Cancel, Esc or Allow
- **THEN** the focus is back on the button that opened it
- **AND** Esc sends nothing and says "Nothing was sent"; Allow stores the consent and sends the request again once
Pinned by: `tests/unit/test_static_followup.py::test_consent_dialog_focuses_cancel_and_returns_focus`, `tests/unit/test_ui_followup_r17.py::test_the_consent_dialog_hands_the_focus_back_after_esc_or_allow`

#### Scenario: The opener is disabled while its request runs
- **WHEN** the researcher starts Construct "Fetch missing abstracts" with the keyboard (the button disables itself until the request ends) and cancels the dialog
- **THEN** "Nothing was sent" is shown and the focus is back on "Fetch missing abstracts" once it is enabled again, unless the researcher moved the focus meanwhile
Pinned by: `tests/unit/test_drawer_keys_node.py::test_focus_returns_to_a_fetch_button_disabled_while_the_dialog_was_open`, `tests/unit/test_ui_followup_r17.py::test_the_consent_dialog_hands_the_focus_back_after_esc_or_allow`

#### Scenario: Retry failed lookups with consent off
- **WHEN** consent is off, some lookups failed, and the researcher presses "Retry failed lookups"
- **THEN** the same consent dialog opens; Cancel or Esc sends nothing and says "Nothing was sent"
- **AND** after Allow one retry starts, however often the button was pressed while the dialog was open
Pinned by: `tests/unit/test_ui_followup_r20.py::test_retry_failed_lookups_goes_through_the_consent_dialog`, `tests/unit/test_static_followup.py::test_consent_dialog_focuses_cancel_and_returns_focus`, `tests/unit/test_ui_followup_r17.py::test_the_consent_dialog_hands_the_focus_back_after_esc_or_allow`, `tests/unit/test_lookups.py::test_routes_without_consent_answer_403_and_start_no_job`, `tests/unit/test_ui_followup_r30.py::test_retry_with_lookups_off_opens_the_consent_dialog_through_the_real_request`

### Requirement: Term lookups keep their own switch, off by default
The system SHALL keep `online_lookups.terms_allowed` off by default and apart from the DOI consent: giving one MUST NOT give the other. A term lookup needs the term switch only; while it is off the researcher is asked, and without a yes nothing is sent (exit 2 or 403 naming `wider lookups terms allow`).
Surfaces: CLI `wider lookups terms allow|revoke`, `wider lookups status`; UI "Online lookups" card (Settings, Setup), second switch.

#### Scenario: Two switches
- **WHEN** `wider lookups allow` is run and then `wider lookups status`
- **THEN** status reports online lookups on and term lookups off, and a term lookup sends nothing and answers 403 with "sends one search term to OpenAlex; turn on with `wider lookups terms allow`"
- **AND** after `wider lookups terms allow` the Settings card shows both on; `wider lookups revoke` turns off only the DOI consent and a term lookup still passes, and `wider lookups terms revoke` leaves the DOI consent on
Pinned by: `tests/unit/test_lookups.py::test_term_switch_is_separate`

### Requirement: Term yield count
The system SHALL, when the researcher asks for one term, request from OpenAlex the number of works matching that term (purpose `term_yield`) and show it with the date and the source, marked "cached" when it comes from the cache. A failed request shows "not available" and is logged as failed. The count informs and changes nothing.
Surfaces: CLI `wider query yield "<term>"`; UI Search strings page, "Yield" column ("Count" per term).

#### Scenario: A count, a cached count and a failure
- **WHEN** the term switch is on and the mock OpenAlex answers a term with 1234 in the field recorded in design D16
- **THEN** `wider query yield` and the Yield column show "1234 works on OpenAlex" with the date, and asking again shows the same count marked "cached" with no new request
- **AND** a term whose request answers 500 shows "not available", and `external_requests` holds a failed `term_yield` entry
Pinned by: `tests/unit/test_search_provenance.py::test_term_yield_count`

#### Scenario: Nothing is counted while the switch is off
- **WHEN** the term switch is off and the Search strings page is opened
- **THEN** the Yield column says "term lookups are off (Settings)", a Count button reaches only WIDER's own route and the consent dialog, and the mock client saw no request
Pinned by: `tests/unit/test_search_provenance.py::test_term_yield_count`

### Requirement: Query count and seed check on OpenAlex
The system SHALL, under the term switch, ask OpenAlex how many works a rendered query retrieves, and, with the DOI consent too, which development seeds its search retrieves, compared per seed with the local dry run. Validation seeds MUST NOT be sent. The rendering comes with its ledger. `wider query yield` SHALL take a term or `--query`, not both.
Surfaces: CLI `wider query yield [TERM] [--query] [--version V] [--seeds]`; UI Search strings page, "OpenAlex check".

#### Scenario: Query count and seed check on OpenAlex
- **WHEN** both switches are on and the mock OpenAlex returns a count and two of three development seeds, while the local dry run hit all three
- **THEN** the count is shown with the ledger and a note that it is not comparable with a per-term count, and the third seed is listed as "dry run hit, OpenAlex miss"
- **AND** the requests carried the query string and development seed DOIs only, no validation seed, and are logged as `query_yield` and `query_seed_check`; with the term switch off nothing is sent, and with the DOI consent off the seed check sends nothing
Pinned by: `tests/unit/test_search_provenance.py::test_query_yield_and_seed_check`
