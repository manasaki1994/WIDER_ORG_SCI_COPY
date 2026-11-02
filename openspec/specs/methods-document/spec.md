# methods-document Specification

## Purpose
Explain why WIDER works the way it does: fourteen methods topics in three layers (In short, Why, For auditors)
and a glossary with synonyms, read on the Methods page and with `wider guide methods`, each marked as reviewed by
the author or drafted, and kept in step with the code they describe by fingerprints that a gate check compares.

## Requirements

### Requirement: Methods topics in three layers
The system SHALL ship one Markdown file per methods topic in `wider/guide/methods/`, with front matter `id`, `title`, `context`, `settings`, `artifacts` and `pages`, and a body with exactly the headings "In short", "Why" and "For auditors" in that order. "In short" holds no formula. Each setting is an existing numeric, choice or flag protocol key; each artifact is a kind in `KINDS`; each page is a guide page.
Surfaces: files under `wider/guide/methods/`; test `tests/unit/test_methods.py`.

#### Scenario: Every topic follows the format
- **WHEN** every shipped topic file is read
- **THEN** each has the six front matter keys, a one-sentence `context`, and the three headings in order with text under each
- **AND** each `settings` key names a protocol field whose type is a number, a fixed choice or a flag, each `artifacts` entry is in `KINDS`, and each `pages` entry is a page with shipped guide text

#### Scenario: Plain layer has no formula
- **WHEN** the "In short" layer of every topic is read
- **THEN** it contains none of `=`, `<`, `>`, `≤`, `≥`, `±`, `Σ`, `√`, `^`, `$` or a backslash, and has at most 120 words

#### Scenario: Layers fit the assistant's budget
- **WHEN** the "Why" and "For auditors" layers of every topic are read
- **THEN** "Why" has at most 250 words and "For auditors" at most 200 words

#### Scenario: The required topics exist
- **WHEN** the topic ids are listed
- **THEN** no topic is named `glossary`, and they include `overview`, `query-construction`, `development-validation-split`, `deduplication`, `ranking`, `development-sample`, `stratified-sampling`, `recall-estimate`, `recall-target`, `llm-screening`, `labels-and-agreement`, `blinding`, `consent-and-privacy` and `provenance-and-reporting`

### Requirement: Methods glossary
The system SHALL ship `wider/guide/methods/glossary.md` with one entry per term: a `## <term>` heading, a `synonyms:` line (it may be empty) and a plain definition of one to three sentences and at most 60 words. A term or synonym appears in one entry only.
Surfaces: `wider/guide/methods/glossary.md`; Methods page `#methods/glossary`; CLI `wider guide methods glossary [--json]`.

#### Scenario: Recall entry carries its synonyms
- **WHEN** the glossary is read
- **THEN** the entry "recall" lists "sensitivity" and "share of relevant papers found" among its synonyms, and its definition has one to three sentences

#### Scenario: Glossary on the page and the CLI
- **WHEN** `wider guide methods glossary` runs or `#methods/glossary/recall` is opened
- **THEN** the CLI prints every term with its synonyms and definition, and the page shows the glossary scrolled to "recall"

#### Scenario: No term is defined twice
- **WHEN** all terms and synonyms are lower-cased and compared
- **THEN** none appears in two entries

### Requirement: Methods sections loader
The system SHALL offer `wider.guide.methods.sections()`, returning one section per layer of each topic, with id `methods/<topic>/<short|why|auditors>`, and one per glossary term, with id `glossary/<slug>`. Each section carries kind, topic, layer, title, context, text, settings, artifacts, pages and anchor. Each `{default:section.key}` in a text is filled with the shipped protocol default.
Surfaces: Python `wider.guide.methods.sections()`, `topics()`, `topic(id)`, `glossary()`.

#### Scenario: Three sections per topic
- **WHEN** `sections()` is called
- **THEN** it returns three sections for every topic and one for every glossary term, with unique ids, and the anchor of `methods/recall-estimate/auditors` is `#methods/recall-estimate/auditors`
- **AND** the section `glossary/recall` has the title "recall", the anchor `#methods/glossary/recall` and a text that starts with "Also called:" and names "sensitivity"

#### Scenario: Defaults come from the protocol
- **WHEN** a topic's text contains `{default:recall.ci_level}`
- **THEN** the loaded text shows the default of `recall.ci_level` from the shipped protocol, and no `{default:` remains in any loaded text

#### Scenario: Unknown placeholder fails the test
- **WHEN** a topic names `{default:recall.no_such_key}`
- **THEN** the loader raises an error naming the topic and the key

### Requirement: Methods stay in step with the code
The system SHALL keep per topic the code it describes and a fingerprint of that code and of its settings' defaults. The gate fails when a fingerprint no longer matches, when a module of the recall, sample, rank, dedup or screen package or a method setting is named by no topic nor listed as outside, or when a code reference in a "For auditors" layer does not exist.
Surfaces: `wider/guide/methods/review.yaml`; `scripts/methods_review.py`; test `tests/unit/test_methods_current.py`.

#### Scenario: Changed code names its topic
- **WHEN** the body of a function named in the sources of `recall-estimate` changes, other than its comments, docstring or layout
- **THEN** the test fails and names `recall-estimate` and the command that stamps it after its text is updated

#### Scenario: Changed default names its topic
- **WHEN** the shipped default of `recall.ci_level` changes and `recall-estimate` lists that setting
- **THEN** the test fails and names `recall-estimate`

#### Scenario: Layout changes do not fail
- **WHEN** only comments, blank lines or a docstring change in a named function
- **THEN** the fingerprint is unchanged

#### Scenario: A new method module needs a topic
- **WHEN** a new module is added under `wider/recall/` and no topic names it and it is not listed as outside the methods
- **THEN** the test fails and names the module

#### Scenario: A new method setting needs a topic
- **WHEN** a numeric, choice or flag field is added to the recall, sampling, ranking, screening, dedup or identification protocol and no topic lists it and it is not listed as outside the methods
- **THEN** the test fails and names the key

#### Scenario: Stamping marks the text as drafted
- **WHEN** `scripts/methods_review.py stamp recall-estimate` runs after the text was updated
- **THEN** the topic's fingerprint matches again and its status is "drafted" with today's date, until `approve` records the owner's review

### Requirement: Methods page and CLI
The system SHALL show a Methods page listing every topic with its title and context, each topic's three layers, the glossary, and under each title its review status: "Reviewed by the author on <date>." or "Drafted; not yet reviewed by the author.". `wider guide methods [<topic>] [--json]` prints it from one function. Neither needs a project.
Surfaces: UI Methods page `#methods`, `#methods/<topic>`; CLI `wider guide methods [<topic>] [--json]`; `GET /api/methods`, `GET /api/methods/{topic}`.

#### Scenario: Read one topic on the page and the CLI
- **WHEN** the researcher opens `#methods/stratified-sampling`
- **THEN** the page shows that topic's title, context, review line and its "In short", "Why" and "For auditors" text
- **AND** `wider guide methods stratified-sampling --json` gives the same title, context, review status and layer texts

#### Scenario: CLI outside a project
- **WHEN** `wider guide methods` runs in a folder that is not a WIDER project
- **THEN** it lists every topic id with its title and context and exits with code 0

#### Scenario: Unknown topic
- **WHEN** `wider guide methods no-such-topic` runs or `#methods/no-such-topic` is opened
- **THEN** the CLI prints "there is no such methods topic: no-such-topic" and exits with code 1, and the page shows the same sentence above the topic list

#### Scenario: A layer anchor opens that layer
- **WHEN** `#methods/recall-estimate/auditors` is opened
- **THEN** the page shows `recall-estimate` scrolled to its "For auditors" heading

#### Scenario: Methods text is shown as text
- **WHEN** a topic's text contains `<b>` or a Markdown link
- **THEN** the page shows the characters as written, with no element or link made from them; only paragraphs, list items and inline code are formatted

#### Scenario: Methods is not a guide page
- **WHEN** the guide pages are listed
- **THEN** `methods` is not among them, so `wider guide methods` always means the methods document

### Requirement: Links to the methods from the guide and Help
The system SHALL show under the guide panel of a page a "Why WIDER works this way" line linking each topic whose `pages` include that page, and on Help a link to the Methods page. The guide's JSON and text output list the linked topics.
Surfaces: UI guide panel, Help page; CLI `wider guide <page> --json` (`methods`); `GET /api/guide/<page>`.

#### Scenario: Sampling guide links its topics
- **WHEN** the Samples and labelling guide panel is open
- **THEN** it shows a "Why WIDER works this way" line with a link to "Stratified sampling for validation" that opens `#methods/stratified-sampling`
- **AND** `wider guide sample --json` lists `stratified-sampling` under `methods`, and `wider guide sample` prints a line "Why WIDER works this way:" naming it

#### Scenario: Help links the Methods page
- **WHEN** the Help page is open
- **THEN** it has a link "Methods: why WIDER works this way" that opens the Methods page
