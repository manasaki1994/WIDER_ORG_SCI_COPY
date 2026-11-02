# seed-paper-mining Specification

## Purpose
Mines the names a literature uses for its constructs from the seed papers (their PDFs, keywords and references) on the researcher's computer, verifies every mined name against its source text, lets a name into the query only when it earns its place, and shows which venues use which names so the researcher can label the streams of the literature.

## Requirements

### Requirement: Seed PDFs stay in the project folder
The system SHALL store an optional PDF per seed paper under `seeds/<benchmark_id>.pdf` in the project folder with its sha256 in the store, list and remove it, and MUST exclude `seeds/`, the extracted text and the passages from the share bundle, the replication package and every export. A file that is not a PDF is refused.
Surfaces: CLI `wider benchmark pdf add <id> <path>`, `wider benchmark pdf list|remove <id>`; UI Setup page, seed-papers step, "Full text" column.

#### Scenario: Add, list, remove and never export
- **WHEN** a synthetic PDF is added for B001, a text file is offered for B002, and the share bundle and package are then built
- **THEN** B001's file is under `seeds/` with its hash listed, B002's is refused as not a PDF
- **AND** neither the bundle nor the package nor `exports/` contains any file from `seeds/` or its extracted text
- **AND** a second PDF added for B001 replaces the first (one file, one hash), `seeds/.gitignore` starts with `*`, and the project's own `.gitignore` is left unchanged
Pinned by: `tests/unit/test_seed_mining.py::test_seed_pdf_add_list_remove_and_never_exported`

### Requirement: Text extraction and post-processing
The system SHALL extract text from a seed PDF with pypdf (`dev` extra; `wider[pdf]` for users), join hyphenated line breaks, drop repeated headers and footers, split the text into sections, and hand the reference section to the existing reference parser. Without pypdf it MUST name the extra and extract nothing. The text is written under `runs/seeds/` only.
Surfaces: CLI `wider seeds extract [--benchmark B]`; UI Construct workbench, "Names from seed papers" card, "Extract".

#### Scenario: Extraction on a synthetic PDF
- **WHEN** the synthetic two-page PDF with a hyphenated word across lines, a running header and a reference list is extracted
- **THEN** the text holds the joined word once, the header once, and the references are parsed into entries with their years
Pinned by: `tests/unit/test_seed_mining.py::test_extract_and_postprocess`

#### Scenario: Extra missing
- **WHEN** the import of pypdf is made to fail in the test (pypdf itself is installed everywhere; no test is skipped)
- **THEN** the command exits 1 naming `wider[pdf]` and `runs/seeds/` gets no file
Pinned by: `tests/unit/test_seed_mining.py::test_missing_extra_is_named`

### Requirement: Deterministic name mining
The system SHALL mine candidate names from the seed papers without a model: the Keywords line, phrases introduced by "also called", "also known as", "known as", "termed", "referred to as", "so-called", quoted phrases, capitalised multi-word phrases that recur, and word pairs recurring in retrieved titles that hold a mined name. Each candidate keeps its source (seed id, section, quote) and a count.
Surfaces: CLI `wider seeds mine --no-model`; UI "Names from seed papers" card.

#### Scenario: Patterns on synthetic text
- **WHEN** a seed text holds `Keywords: alpha coupling; beta drift`, the sentence "alpha coupling, also called phase binding, ..." and "phase binding" quoted twice more
- **THEN** the candidates include `alpha coupling`, `beta drift` and `phase binding`, the last with the pattern `also called` and a count of 3, each with its quote
Pinned by: `tests/unit/test_seed_mining.py::test_deterministic_patterns`

#### Scenario: Other introducing patterns
- **WHEN** a seed text introduces names with "also known as", "known as", "termed", "referred to as" and "so-called", and one sentence says "also called the better than average effect"
- **THEN** each introduced name is a candidate with its pattern and without the words that follow it, a "same as" partner only where the sentence names one, and "better than average effect" stays whole
Pinned by: `tests/unit/test_seed_mining.py::test_other_introducing_patterns`

### Requirement: Validation seeds stay out of mining
The system SHALL mine names only from development seed papers: a validation paper's text, keywords and reference list are not mined, sent or counted in the streams table, and the run names the papers it left out with how to mine one (move it to Development). Mining one validation paper by id is refused with the same words.
Surfaces: CLI `wider seeds mine [--benchmark ID]`; UI "Names from seed papers" card.

#### Scenario: A validation PDF is kept but not mined
- **WHEN** B001 (development) and V001 (validation) both have an extracted text and the researcher mines all seeds
- **THEN** only B001 is mined, no model call holds V001's text, the streams table counts B001's references only, and the CLI and the page say V001 was left out; `--benchmark V001` exits 1
- **AND** V001's keywords alone (no text) are not mined either
Pinned by: `tests/unit/test_seed_mining.py::test_validation_seeds_are_not_mined`

#### Scenario: A seed moved to Validation after mining
- **WHEN** B001 was mined and is then moved to Validation
- **THEN** the candidate list and the names table show no name or source from B001 (the run folder keeps them), and mining again with only validation seeds refuses with "nothing to mine", naming the papers and `wider benchmark set ID Development`
- **AND** the open or waiting questions resting only on B001 (its mined names, its miss) close with the reason, no ledger row, and an answer to one is refused; moving B001 back lets them be asked again
Pinned by: `tests/unit/test_seed_mining.py::test_validation_seeds_are_not_mined`, `tests/unit/test_interview.py::test_questions_from_a_seed_moved_to_validation_close`

### Requirement: Model pass with verbatim-quote verification
The system SHALL send passages of at most the configured size (default 1,500 tokens) to the stage `construct` backend with a flat schema (name, kind, quote, reasoning) and keep a candidate only if its quote is a substring of the passage after normalisation; others are dropped and counted. Model candidates are marked `model` and never enter a block on their own.
Surfaces: CLI `wider seeds mine`; UI "Names from seed papers" card, "Mine with the model".

#### Scenario: Fake provider with one bad quote
- **WHEN** the fake provider returns two candidates, one quoting the passage and one quoting words not in it
- **THEN** the first is kept marked `model` with its passage reference and the second is dropped, the result saying "1 dropped: quote not found"
- **AND** the run folder holds the prompt and answer hashes and no copy of the PDF
Pinned by: `tests/unit/test_seed_mining.py::test_model_candidates_need_a_verbatim_quote`

### Requirement: Passages to a cloud backend under a per-project switch
The system SHALL send seed-paper passages to a `network`, `cloud` or `unknown` backend only when `seed_pdfs.passages_to_cloud` is on (default on; ignored for `local` or `test`) and the backend has `passages` consent, asked once and remembered. Switch off: the model pass is skipped with a message, the deterministic pass still runs. Whole papers are never sent.
Surfaces: CLI `wider seeds mine`, `wider seeds policy [passages-to-cloud on|off] [--passage-tokens N]`; UI Settings, "Seed papers" card.

#### Scenario: Switch off skips the model pass
- **WHEN** the chosen backend is cloud, the switch is off and mining runs
- **THEN** no model call is made, the deterministic candidates are listed, and the message names the switch
- **AND** on a cloud backend without research consent, `wider seeds mine` without a terminal exits 2 and the mine route answers 403 `backend_consent_required`; no passage is sent
- **AND** with the switch on, each call carries at most one passage and never more than the configured size
- **AND** an `unknown`-locality backend with the switch off is skipped the same way
Pinned by: `tests/unit/test_seed_mining.py::test_passages_switch_gates_cloud_calls`

#### Scenario: Passages consent once per backend
- **WHEN** a cloud backend has research consent but not `passages` consent and mining runs without a terminal
- **THEN** the CLI exits 2 and the route answers 403 naming `passages`; after the researcher allows it the run sends, the Settings card lists the backend with "Withdraw", and after withdrawing it asks again
- **AND** a passage size outside 100-8,000 tokens is refused by the card and `wider seeds policy --passage-tokens`
Pinned by: `tests/unit/test_seed_mining.py::test_passages_switch_gates_cloud_calls`, `tests/unit/test_seed_mining.py::test_seed_policy_card`

### Requirement: Admission rule for a name
The system SHALL add a candidate name to a block only when the dry run shows it turns at least one development seed from `miss` to `hit` on a platform with a profile, or when the researcher accepts it with a note; an accepted name records who and why. Other candidates stay listed as "not admitted" with the reason.
Surfaces: CLI `wider seeds names [--admit <name> --block K --note ...]`; UI "Names from seed papers" card, "Add to block" with a note field.

#### Scenario: Earned and accepted names
- **WHEN** candidate X turns B002 into a hit and candidate Y turns no seed, and Y is accepted with the note "used in the German literature"
- **THEN** X can be admitted without a note, into a new draft version noting "retrieves B002"; Y is admitted only with the note, recording the researcher's name and note; a third candidate Z is refused and listed "not admitted: retrieves no development seed"
- **AND** mining never writes a version: every admission follows the researcher's Admit, `--admit` or a `refine_name_add` answer
Pinned by: `tests/unit/test_seed_mining.py::test_admission_rule`

#### Scenario: Admission while the query is frozen
- **WHEN** the query is frozen and a name is admitted
- **THEN** the page shows the same confirm step as any save after a freeze ("Cancel" writes nothing), the CLI prints the unlock line, and undoing the admission is refused naming the unlock
Pinned by: `tests/unit/test_seed_mining.py::test_admission_while_frozen`, `tests/unit/test_seed_mining.py::test_names_card_add_to_block_confirm_step`, `tests/unit/test_seed_mining.py::test_names_card_asks_for_the_note_before_the_confirm_step`

#### Scenario: Admitting a name closes its open question
- **WHEN** a `refine_name_add` question about a name is open and the name is admitted on the card or with `--admit`
- **THEN** the question is closed, naming the admission, the card and the CLI say which question was closed, the ledger holds the admission and no dismissal, and undoing the admission opens the question again
Pinned by: `tests/unit/test_interview.py::test_admitting_a_name_closes_its_open_question`

#### Scenario: Admission frees the question budget
- **WHEN** the open-question budget is full, another question waits, and an admission closes an open name-add question
- **THEN** the oldest waiting question opens, as after an answer or a dismissal
Pinned by: `tests/unit/test_interview.py::test_admission_releases_a_waiting_question`

### Requirement: Name map
The system SHALL keep a name map: names grouped as the same construct (from "also called" evidence and `SAME_CONSTRUCT` answers) and names flagged as look-alikes that differ (from the researcher's answers), written as a table and an SVG in the run folder; a group's names are proposed for one block, look-alikes for the adjacent list.
Surfaces: CLI `wider seeds names --map`; UI "Names from seed papers" card, name map.

#### Scenario: Grouping from evidence and answers
- **WHEN** "phase binding" was mined with `also called` from "alpha coupling", and a `SAME_CONSTRUCT` question about "alpha linkage" was answered "different construct"
- **THEN** the map groups the first two and flags the third as a look-alike, and `name_map.svg` names all three
Pinned by: `tests/unit/test_seed_mining.py::test_name_map_from_evidence_and_answers`

#### Scenario: "Different" splits an also-called pair
- **WHEN** a name was mined with `also called` from the name before it, and the `SAME_CONSTRUCT` question about the pair is answered "different"
- **THEN** the map no longer groups them and flags the mined name (not the one it was introduced with) as a look-alike of the other
Pinned by: `tests/unit/test_interview.py::test_different_answer_splits_an_also_called_pair`

### Requirement: Name-by-venue table for streams
The system SHALL build, without a model, a table of candidate names by venue from the seed papers' parsed reference sections (venue normalised with the dedup `norm_text`; a count per name and venue from the reference titles holding the name), show it, and let the researcher label a venue as a stream; a label is stored, journalled `stream_label` and in the package as `streams.json`.
Surfaces: CLI `wider seeds streams [--label <venue> <stream>]`; UI "Names from seed papers" card, "Streams" table.

#### Scenario: Two venues, one labelled
- **WHEN** the references of two seed texts hold "alpha coupling" in four titles from "Journal of Synthetic Signals" and one from "Proceedings on Phase Studies", and "phase binding" in one title from the proceedings only
- **THEN** the table lists both names against both venues with counts 4/1 and 0/1, and `wider seeds streams --label "proceedings on phase studies" engineering` stores the label and the history gets a `stream_label` change that undo reverses
- **AND** the "Streams" table shows the label through the same engine function, and the next package holds `streams.json` with the table and the label
Pinned by: `tests/unit/test_seed_mining.py::test_name_by_venue_table`
