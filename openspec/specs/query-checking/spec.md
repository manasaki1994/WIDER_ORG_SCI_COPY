# Query Checking Specification

## Purpose
Checks every rendered search string before the researcher pastes it: lists what each platform's syntax lost in translation, parses the string back to prove it says what the query says, and has a second model read it against the PRESS checklist.

## Requirements

### Requirement: Translation ledger per platform
The system SHALL list, next to every rendered string, what the platform cannot express or does differently and each unverified field code, each row with a kind (`lost`, `wider`, `unverified`) and a reason code. Without `lost` or `wider` rows it says "nothing lost". It goes into the strings archive and the package.
Surfaces: CLI `wider query ledger [--platform P]`, `wider query render --ledger`; UI Search strings page, under each string.

#### Scenario: PubMed proximity and a keywords field
- **WHEN** a query with an unordered proximity block and a keywords-only block, with no truncation, is rendered for PubMed, Scopus and EBSCO
- **THEN** PubMed's ledger lists "proximity rendered as AND" (`lost`) and "[tiab] includes author keywords" (`unverified`), and Scopus's lists "keywords searched more widely: the code also covers title and abstract" (`wider`)
- **AND** EBSCO's ledger says "nothing lost" and lists "KW: keyword field code not yet verified against the platform's help page" (`unverified`)
Pinned by: `tests/unit/test_query_checking.py::test_translation_ledger`

#### Scenario: Ledger next to the archived string
- **WHEN** a synonyms version's EBSCO string is archived, the platform's syntax changes and the version is archived again the same day (the second file gets the `_<h8>` suffix), and an older string was archived before this change
- **THEN** each new string has `<its stem>_ledger.json` next to it, holding that string's ledger rows
- **AND** the strings page shows "no ledger (archived before ledgers)" for the older string
Pinned by: `tests/unit/test_query_checking.py::test_ledger_next_to_archived_string`

#### Scenario: Silent changes are listed
- **WHEN** a query with an ordered proximity of 5, a title-only block, the truncated term `organi*` and the phrase "job demand" is rendered for every platform
- **THEN** Web of Science and Ovid list the ordered proximity searched in any order (`lost`, `ordered_as_unordered`), Ovid lists "ADJ5 allows 4 words between; the query asked for 5" (`lost`, `proximity_counting`) and PubMed the title-only block searched in title and abstract (`wider`, `field_widened`)
- **AND** ProQuest lists that `*` covers at most five characters (`lost`, `truncation_cap`), Scopus that quotes also find variants of the phrase (`wider`, `loose_phrase`), and Web of Science that truncation switches lemmatisation off (`lost`, `variants_off`)
Pinned by: `tests/unit/test_query_checking.py::test_ledger_lists_silent_changes`

### Requirement: Round-trip parse for the Boolean family
The system SHALL parse a string in EBSCOhost, Scopus, Web of Science, ProQuest, Ovid or PubMed syntax back into the query; render then parse MUST give the query's canonical form (the ledger's changes and the parser's block names applied). Google Scholar strings are not parsed. A string that does not parse names the position and the token.
Surfaces: CLI `wider query parse --platform P [FILE|-]` (saves nothing), `wider query import --platform P FILE`; UI Search strings page, "Import a string".

#### Scenario: Round trip on synthetic queries
- **WHEN** fifty synthetic queries (random blocks, phrases, truncation, proximity and fields from a fixed seed) are rendered and parsed for each platform of the family (on PubMed without proximity, which its ledger lists as lost)
- **THEN** every parsed query equals the original's canonical form for that platform
- **AND** `wider query parse --platform pubmed -` reading `(a AND (b OR c` exits 1 naming the missing parenthesis at its position
Pinned by: `tests/unit/test_query_checking.py::test_round_trip_property`

### Requirement: PRESS second reader
The system SHALL ask one or two readers (default: construct backend twice) PRESS items about each string, with the query, ledger, lint findings and decisions. Findings quoting text not in the string are dropped; those both runs report are agreed, others "unsure". CLI agents with tools are refused. Findings change nothing.
Surfaces: CLI `wider query review [--platform P] [--reader NAME]`, `wider providers stage review NAME [NAME2]`; UI Search strings page, "Second reader"; Settings, "review" row.

#### Scenario: Two readers, agreed and unsure findings
- **WHEN** two fake backends with the models `llama3.1:8b` and `qwen2.5:7b` are set as the `review` stage's readers, both return finding A quoting the string, the first also returns B quoting the string and the second C quoting words not in it
- **THEN** the review lists A as agreed and B as "unsure", each with its backend and PRESS item, says "1 dropped: quote not found", names the two readers and their families (`llama`, `qwen`), and the rendered strings and synonyms versions are unchanged
- **AND** with one reader, or two with the same model, the review runs twice on it and says "same model twice"; two different models of one family say "same family twice"
Pinned by: `tests/unit/test_query_checking.py::test_press_second_reader`

#### Scenario: What the reader is given
- **WHEN** a query with one ledger row, one lint warning and two query decisions (one of them dismissed) is reviewed on a fake backend
- **THEN** the prompt holds the query tree, the rendered string, the ledger row, the lint warning, the PRESS items (subject headings and text words among them) and the decision that was not dismissed, and no seed-paper passage
- **AND** the run's `calls.json` holds each call's input and output hashes and attempts, and `llm_usage` holds `press_review` rows with no text
Pinned by: `tests/unit/test_query_checking.py::test_second_reader_prompt`

#### Scenario: A CLI agent with tools is not used as a reader
- **WHEN** the chosen reader is a `cli_agent` backend whose command lacks its tool's tool-off flags
- **THEN** `wider query review` exits 2 naming the missing flags, and nothing is sent to that backend
Pinned by: `tests/unit/test_query_checking.py::test_second_reader_refuses_agent_with_tools`

### Requirement: Property tests over synthetic queries
The system SHALL check, on synthetic queries from a fixed seed, that rendering never emits an unquoted multi-word term (except on Ovid, whose phrase syntax is unquoted), unbalanced parentheses, a lower-case operator or a proximity distance outside the platform's range, and that a lint-clean query (no error-level `lint` finding) renders strings with no error-level string check.
Surfaces: engine only (test suite).

#### Scenario: Rendered strings are lint-clean
- **WHEN** fifty synthetic lint-clean queries are rendered for every platform
- **THEN** no string has an error-level finding from the string check and all parentheses balance
Pinned by: `tests/unit/test_query_checking.py::test_rendering_properties`
