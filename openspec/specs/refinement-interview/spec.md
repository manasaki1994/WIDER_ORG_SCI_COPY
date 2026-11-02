# refinement-interview Specification

## Purpose
Asks the researcher only the questions the evidence raises while the query is refined, with a default and its computed consequence for each, within a budget, and keeps every answer, accepted default and dismissal in a decision ledger that later prompts and the replication package read.

## Requirements

### Requirement: Ten evidence-triggered question types
The system SHALL raise only the kinds `refine_name_add`, `refine_same_construct`, `refine_noise_term`, `refine_variant`, `refine_context_and`, `refine_seed_not_retrieved`, `refine_boundary_record`, `refine_limit`, `refine_screening_budget` and `refine_round_stop`, each only when its evidence exists after a dry run, mining or a diagnosis, with 3 to 5 options, a default and its consequence in numbers.
Surfaces: CLI `wider questions list`; UI Questions page.

#### Scenario: Triggers on the synthetic project
- **WHEN** a dry run leaves B002 a miss, mining finds a name that would retrieve it, and the diagnosis finds a noise term
- **THEN** exactly one `refine_seed_not_retrieved`, one `refine_name_add` and one `refine_noise_term` question are open, each with its evidence in the context, 3 to 5 options and a default whose consequence names seeds and records
- **AND** no `refine_variant` or `refine_limit` question exists because nothing triggered them
Pinned by: `tests/unit/test_interview.py::test_questions_follow_the_evidence`, `tests/unit/test_interview.py::test_more_finders_follow_the_evidence`

### Requirement: Dedupe key and budgets
The system SHALL use one dedupe key per kind (the name, term, seed id, record id, block or round it is about) so the same question is never open twice, and SHALL hold back new questions when 5 are open for the current round or 10 have been asked for one construct block, listing the held-back ones as "waiting for the budget".
Surfaces: CLI `wider questions list` (shows waiting count); UI Questions page.

#### Scenario: Budget reached
- **WHEN** seven name-add triggers exist for block A in one round
- **THEN** five questions are open, two are waiting, and answering one opens the next
- **AND** a second mining run with the same names opens nothing new, also for questions already answered or dismissed (their dedupe keys are in the ledger)
Pinned by: `tests/unit/test_interview.py::test_budget_and_dedupe`

### Requirement: Answers apply at once and are journalled
The system SHALL apply each answer through existing paths: name-add, noise-term, variant, context-AND and seed-not-retrieved answers write a new draft synonyms version, same-construct updates the name map, boundary-record marks the cluster, limit sets the platform's `limits`, budget sets `record_budget`, `leave` changes nothing. Each answer is one journalled change; undoing it reopens the question.
Surfaces: CLI `wider questions answer <id> <choice>`; UI Questions page, ask dialog.

#### Scenario: Name-add and undo
- **WHEN** a `refine_name_add` question is answered with its default and the change is undone
- **THEN** the version written by the answer is no longer listed by `wider construct list` (its file stays), the question is open again, and its decision row is gone from the ledger and from the next `decisions.jsonl`
Pinned by: `tests/unit/test_interview.py::test_answers_apply_and_undo`

#### Scenario: An answer that unlocked a frozen query
- **WHEN** the query is frozen and a `refine_name_add` answer is given
- **THEN** the Questions page and the ask dialog show the same confirm step as any save after a freeze ("Cancel" writes nothing), `wider questions answer` writes the version and prints the unlock line, and undoing the answer is refused naming the unlock
Pinned by: `tests/unit/test_interview.py::test_answer_that_unlocked_cannot_be_undone`

#### Scenario: A question about an older version
- **WHEN** a question asked about v1 proposes adding a term that v2, saved and frozen on the workbench, already holds
- **THEN** answering it says the term is already in the block of v2 and saves and unlocks nothing, and the other open or waiting questions proposing only edits v2 already holds close with the reason and no ledger row
- **AND** undoing the answer reopens the questions it closed
Pinned by: `tests/unit/test_interview.py::test_stale_questions_write_nothing_and_close`

### Requirement: Decision ledger with answer mode
The system SHALL write every refinement decision to a ledger with the kind, dedupe key, evidence, options, default, chosen option, answer mode (`chosen`, `default` when the researcher accepted the default, `dismissed`), consequence, author, time and round, and SHALL export it as `decisions.jsonl` and a table.
Surfaces: CLI `wider decisions list [--round N]`, `wider decisions export`; UI Questions page, "Decisions" list.

#### Scenario: Three modes in the ledger
- **WHEN** one question is answered with a non-default option, one with "accept the default" and one is dismissed
- **THEN** the ledger has three rows with modes `chosen`, `default` and `dismissed`, each with evidence and author, and `decisions.jsonl` has the same three
- **AND** evidence from a seed PDF is held as seed id, section, offset and quote hash, never the quote's text, and the decision texts in prompts carry the name, the choice and the researcher's note, never a quote
Pinned by: `tests/unit/test_interview.py::test_ledger_modes_and_export`

### Requirement: Decisions reach the prompts
The system SHALL turn each decision into an active clarification of scope `query` and list those under "Decisions from the researcher" in every later synonyms proposal and mining prompt (and the second-reader prompt of `query-checking-provenance`): the newest 30, listed oldest first, without changing screening prompts.
Surfaces: CLI `wider construct propose synonyms`, `wider seeds mine`; UI the same actions.

#### Scenario: A noise-term decision in the next prompt
- **WHEN** a noise-term question is answered "drop it" and the synonyms prompt is built again
- **THEN** the prompt lists "Decisions from the researcher" with the dropped term and the reason, and the screening prompt does not
Pinned by: `tests/unit/test_interview.py::test_decisions_enter_prompts`

### Requirement: Stop rule per round
The system SHALL raise `refine_round_stop` when the last three answers in a round moved development recall and the accepted round's record count by less than 2% each, offering "freeze this round" (it opens the Freeze step; the freeze is its own action), "one more round" and "stop asking for now"; and SHALL not raise other refinement questions after "stop asking for now" until a new dry run or import.
Surfaces: CLI `wider questions list`; UI Questions page.

#### Scenario: Little movement stops the round
- **WHEN** three answers in a row change recall by 0 and records by 1% each
- **THEN** one `refine_round_stop` question is open and no other refinement question is raised until the next dry run
Pinned by: `tests/unit/test_interview.py::test_stop_rule`

#### Scenario: Freeze this round or one more round
- **WHEN** the `refine_round_stop` question is answered "freeze this round" with a note, and a later one "one more round"
- **THEN** the freeze answer freezes nothing, starts the next round and says to freeze on the Construct workbench or with `wider construct freeze synonyms`; the Questions page then opens the Construct workbench, and the note (the card's Note field, `--value` in the CLI) is kept with the decision
- **AND** "one more round" starts the round after it, and nothing is frozen
Pinned by: `tests/unit/test_interview.py::test_freeze_and_more_answers`
