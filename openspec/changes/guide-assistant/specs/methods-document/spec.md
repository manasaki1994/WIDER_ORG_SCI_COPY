## ADDED Requirements

### Requirement: Edited methods text is read at once and counts as not reviewed
The system SHALL read the methods files anew on each use and SHALL show a reviewed topic or glossary whose file differs from the text the author reviewed as "drafted" with "Edited since the author's review on <date>; not yet reviewed again." until approved again. This line is a third beside the two of "Methods page and CLI".
Surfaces: Methods page; CLI `wider guide methods [<topic>] --json` (`review`); `scripts/methods_review.py`.

#### Scenario: An edit is read without a restart
- **WHEN** a sentence of `recall-target.md` is replaced while the UI server runs
- **THEN** the next `GET /api/methods/recall-target` and `wider guide methods recall-target` show the new sentence
Pinned by: `tests/unit/test_methods.py::test_loader_sees_edit_without_restart`

#### Scenario: An edited reviewed topic
- **WHEN** `recall-target` is reviewed and one sentence of its file is then changed
- **THEN** `wider guide methods recall-target --json` gives `review` with status "drafted", the review date and `edited: true` (the key is absent when the text is unchanged), and the page and the CLI show "Edited since the author's review on <date>; not yet reviewed again."
Pinned by: `tests/unit/test_methods.py::test_edited_reviewed_topic_reads_drafted`, `tests/unit/test_methods_page_node.py::test_edited_review_line`

#### Scenario: An edited reviewed glossary
- **WHEN** the glossary is reviewed and a synonym is then added to one entry
- **THEN** the glossary's review reads "drafted" with `edited: true` and the same line
Pinned by: `tests/unit/test_methods.py::test_edited_glossary_reads_drafted`

#### Scenario: Reviewed again after the edit
- **WHEN** the edited topic is stamped and the owner's approval is then recorded
- **THEN** its status is "reviewed" with the new date, and `edited` is absent
- **AND** an approval recorded before the stamp is refused with the stamp command, so the owner never approves text they were not asked about
Pinned by: `tests/unit/test_methods_review.py::test_reapprove_after_edit`

#### Scenario: A file that cannot be read
- **WHEN** a topic file is saved with its headings out of order while the UI server runs
- **THEN** the Methods page and `wider guide methods` list the other topics, with "WIDER's documents: <file> cannot be read (<problem>); it is left out until it is fixed." above the list (exit code 0); `wider guide methods <that topic>` prints the same sentence and exits 1, and `GET /api/methods/<that topic>` answers 404 with it
- **AND** the shipped-file format test still fails on it, so a broken file cannot pass the gate
Pinned by: `tests/unit/test_methods.py::test_bad_topic_file_left_out_and_named`, `tests/unit/test_methods_surfaces.py::test_bad_topic_file_cli_and_route`
