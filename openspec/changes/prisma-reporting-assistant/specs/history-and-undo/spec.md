## ADDED Requirements

### Requirement: Reporting text journalled
The system SHALL journal each save, not-applicable mark, clear and "Keep my text" of PRISMA reporting text, and each save or clear of flow counts, as kind `report_text`, with the file's content before and after, so that `wider history undo` restores the previous text or counts; the History summary names the checklist and item, or the flow diagram, and the action, never the text.
Surfaces: CLI `wider history list --kind report_text`, `wider history undo ID`; UI History page, "Show" select.

#### Scenario: Reporting text by kind and undone
- **WHEN** the researcher saves text for PRISMA 2020 item 9, then saves a second version, and undoes the second save
- **THEN** `wider history list --kind report_text` lists both saves as "PRISMA 2020 item 9: text saved", and the History page's "Show" select offers "PRISMA reporting text", which lists the same entries
- **AND** after the undo item 9 holds the first text again, and the store's tables other than `history` and `researchers` are byte-for-byte the same as before the first save
Pinned by: `tests/unit/test_reporting.py::test_report_text_history_and_undo`, `tests/unit/test_reporting_js_node.py::test_history_show_offers_report_text`
