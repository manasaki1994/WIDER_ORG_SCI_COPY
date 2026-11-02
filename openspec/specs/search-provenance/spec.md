# Search Provenance Specification

## Purpose
Makes the refined search reproducible: every decision, every model call and every outside request is in a ledger, outside responses are cached by request hash, and a round can be replayed offline with no model on the path.

## Requirements

### Requirement: Three ledgers in one view
The system SHALL show, per round, its decisions, the model calls that shape the query in the round's window (synonym proposals, mining, second reader: backend, model, tokens, cost, the hashes in the run's `calls.json`; never text) and web calls in the window (service, purpose, items, switch) as one listing, written as `provenance_<round>.json`. A window runs from the round's creation to the next round's.
Surfaces: CLI `wider query provenance <round>`; UI Search strings page, "Provenance" card.

#### Scenario: One round, three ledgers
- **WHEN** round 1 had two decisions, one synonym proposal and one second-reader run with one reader on one platform on the fake backend, and one chaining request through the mock client, and round 2 then had one more decision
- **THEN** round 1's listing has 2 decisions, 3 model calls (1 `construct`, 2 `press_review` since one reader runs twice; each with input and output hashes from its run's `calls.json` and no prompt text) and 1 web call naming `seed_chain` and the DOI consent
- **AND** `provenance_1.json` holds the same, and round 2's decision is not in it
Pinned by: `tests/unit/test_search_provenance.py::test_three_ledgers_one_view`

#### Scenario: An older proposal run without an output hash
- **WHEN** the round's synonym proposal run has a `calls.json` written before this change, with an input hash and no output hash, and the round also had a cells proposal
- **THEN** the listing shows the proposal call with its input hash and "output hash not recorded", and the cells proposal is not listed
Pinned by: `tests/unit/test_search_provenance.py::test_three_ledgers_one_view`

### Requirement: Outside responses cached by request hash
The system SHALL cache the successful (2xx) responses of `seed_chain` and `term_yield` lookups under the sha256 of the service, method, URL path, sorted query parameters and purpose, serve a repeated request from the cache without a network call, and let the researcher clear the cache. Error responses are not cached. The cache lives in the project store and is not exported.
Surfaces: CLI `wider lookups cache [--clear]`; UI Settings, "Online lookups" card ("cached responses: N").

#### Scenario: Second chaining run sends nothing
- **WHEN** chaining runs twice with the mock client counting requests
- **THEN** the second run makes no request and reports "from cache", and `wider lookups log` shows only the first run's requests
- **AND** after `wider lookups cache --clear` the next run requests again
Pinned by: `tests/unit/test_search_provenance.py::test_lookup_cache`

#### Scenario: Different requests never share an answer
- **WHEN** two requests differ only in their URL path, and a third answers 429
- **THEN** each of the first two is cached with its own body, and the third is not cached and is requested again on the next run
Pinned by: `tests/unit/test_search_provenance.py::test_lookup_cache`

### Requirement: Offline replay of a round
The system SHALL recompute a round's outputs (dry run, grid, cover, diagnosis, edits, strings) from the stored inputs with no model and no network, and compare with the round's record: first the input hashes (seeds, imports, profiles, synonyms version), naming any that changed, then the outputs, reporting "replay matches" or the first difference. A replay writes nothing; the round's first dry run writes the record.
Surfaces: CLI `wider query replay <round>`; UI "Rounds" card, "Replay".

#### Scenario: Replay matches, then a profile change shows
- **WHEN** round 1 is replayed unchanged, then replayed after a profile's minimum stem is edited
- **THEN** the first replay says "replay matches" with no model call and no request, and the second names "profiles" as the changed input and the dry run as the first difference
- **AND** the rounds log and the round's record are unchanged by both, and freezing the round's synonyms version before a replay changes nothing in it
Pinned by: `tests/unit/test_search_provenance.py::test_replay_offline`

#### Scenario: A round without a record
- **WHEN** a round created before this change, with no record, is replayed
- **THEN** the replay says "no record to compare" and writes nothing
- **AND** the round's next dry run writes the record with its date, which a later dry run does not change
Pinned by: `tests/unit/test_search_provenance.py::test_replay_offline`
