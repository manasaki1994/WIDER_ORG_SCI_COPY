## ADDED Requirements

### Requirement: Reporting items with what PRISMA asks and what WIDER recorded
The system SHALL give each item of PRISMA 2020 (42 entries) and PRISMA-S (16), under its official section and number: the official wording and WIDER's paraphrase as a list of elements; the lines WIDER recorded, each naming its source and the elements it covers fully or in part; and the elements still missing. Evidence is recomputed on every view and export, never stored.
Surfaces: CLI `wider report prisma status`, `wider report prisma show ITEM`; UI PRISMA reporting page.

#### Scenario: Item 7 on a synthetic project
- **WHEN** a synthetic project imported `scopus` and `ebsco`, archived a string for `scopus` only and recorded no limits, and `wider report prisma show 7` runs
- **THEN** it prints "PRISMA asks" with item 7's official wording and its source, then the elements "search strategies for every source" and "filters and limits", "WIDER recorded" with the archived `scopus` string (source "search strings archive"), and "Still missing" with "WIDER records part of this: no archived string for EBSCOhost; not confirmed as run as archived" for the strategies and "WIDER does not record this" for filters and limits
- **AND** `--json` returns the same as `official`, `section`, `elements`, `evidence` and `missing`, and the item panel of the page shows the same three blocks
Pinned by: `tests/unit/test_reporting.py::test_item_7_blocks`, `tests/unit/test_reporting_js_node.py::test_item_panel_blocks`

#### Scenario: Both checklists listed
- **WHEN** `wider report prisma status` and `wider report prisma status --checklist prisma-s` run
- **THEN** they list 42 and 16 items in PRISMA order with item, topic, WIDER status and "Your text", and `--json` returns the same rows
Pinned by: `tests/unit/test_reporting.py::test_status_lists_both_checklists`

### Requirement: Statuses from WIDER evidence only
The system SHALL give each item one status: "Automatically documented" when a WIDER record covers every element fully, "Partially documented" when some element is covered fully or in part but not all fully, "Researcher input needed" when none is, or "Not applicable" when the researcher marked it. A context line covers nothing. The researcher's text never changes the status.
Surfaces: CLI `wider report prisma status`; UI PRISMA reporting page, item table and status filter.

#### Scenario: Statuses on a synthetic project
- **WHEN** the synthetic project imported `scopus` and `ebsco`, archived a string for `scopus` only, has a research question, codebook v1, screening of `main` with passes A, B and the judge on models `a`, `b` and `c`, and no package
- **THEN** item 4 reads "Automatically documented", items 5, 6, 7, 8 and 16a "Partially documented", item 27 "Researcher input needed", and PRISMA-S item 15 "Automatically documented"
- **AND** after the researcher saves text for item 5 that ticks every missing element, item 5 still reads "Partially documented"
Pinned by: `tests/unit/test_reporting.py::test_statuses_from_evidence_only`

#### Scenario: Context does not cover
- **WHEN** the project's screening settings hash exists and History holds two settings changes
- **THEN** item 24b shows the hash as "WIDER screening settings (not a review protocol)", a context line, and reads "Researcher input needed"
- **AND** item 24c reads "not recorded by WIDER" and "Researcher input needed", with no History line
Pinned by: `tests/unit/test_reporting.py::test_context_lines_cover_nothing`, `tests/unit/test_reporting.py::test_context_lines_24b_24c`

#### Scenario: Filters on the command line
- **WHEN** item 6 has outdated text and item 9 text that covers its gaps, and `wider report prisma status --to-check --json` and `--status "Researcher input needed"` run
- **THEN** the first lists item 6 only, the second the same rows as the page's filter, and each `--json` row has `your_text` (none, saved, outdated, unreadable) and `gaps_left`
Pinned by: `tests/unit/test_reporting.py::test_status_filters`

#### Scenario: Filter by status
- **WHEN** the page's "Partially documented" filter is chosen
- **THEN** the table shows only those items, and each filter button shows its count, equal to `wider report prisma status --json` counted by status
Pinned by: `tests/unit/test_reporting_js_node.py::test_status_filter_counts`

### Requirement: Items about stages WIDER does not run
The system SHALL mark the items about stages WIDER does not run (full-text assessment, data collection, risk of bias, effect measures, synthesis, certainty, results, discussion, registration, support, competing interests) as "Researcher input needed" with "WIDER does not run this stage; write what you did, or mark the item not applicable", and SHALL NOT assume such a stage was done.
Surfaces: CLI `wider report prisma show ITEM`; UI PRISMA reporting page, item panel.

#### Scenario: Synthesis item
- **WHEN** item 13d is shown on the synthetic project
- **THEN** it reads "Researcher input needed", lists no WIDER evidence and says "WIDER does not run this stage; write what you did, or mark the item not applicable"
Pinned by: `tests/unit/test_reporting.py::test_outside_wider_item`

### Requirement: Researcher text kept apart from WIDER records
The system SHALL keep the researcher's text, the elements they mark as covered by it and where it is reported, per item, in the project's `reporting/` folder with earlier versions. Saving, marking n/a or clearing changes no screening decision, label, setting or other record; only History gains an entry.
Surfaces: CLI `wider report prisma edit ITEM [--text T|--file F|--from-draft|--from-item C:I] [--covers K,..] [--where W]`, `clear ITEM`; UI item panel, Save, "Clear my text".

#### Scenario: Records unchanged by a save
- **WHEN** the researcher saves text for item 8, saves it again changed, then clears it
- **THEN** every table of the store except `history` and `researchers` (History's record of who changed what), and `project.yaml`, are byte-for-byte as before, and the item's file keeps the two earlier texts as versions with their author and time
- **AND** saving the same text, covers and "where reported" again records no History entry, and the page's Save sends the same request body as `wider report prisma edit 8 --text ...` maps to
Pinned by: `tests/unit/test_reporting.py::test_save_leaves_records_unchanged`, `tests/unit/test_reporting.py::test_same_save_records_nothing`, `tests/unit/test_reporting_js_node.py::test_save_sends_edit_fields`

#### Scenario: Location only
- **WHEN** the researcher saves "p. 3" as "where reported" for item 1 with no text, on the page or with `wider report prisma edit 1 --where "p. 3"`
- **THEN** item 1's file holds `where` "p. 3", its "Your text" stays "none", History gains "PRISMA 2020 item 1: location saved", and the Word checklist's location for item 1 reads "p. 3"
- **AND** on the page, with the textarea unchanged from the saved text (empty when none is saved), Save sends no `text`, as the CLI call maps to
Pinned by: `tests/unit/test_reporting.py::test_where_only_save`, `tests/unit/test_reporting_js_node.py::test_save_sends_edit_fields`

#### Scenario: Versions kept and an unreadable file
- **WHEN** item 8's text is saved 22 times and then cleared, and item 9's file holds text that is not JSON
- **THEN** item 8's file keeps the 20 newest earlier texts as versions after the clear
- **AND** item 9 shows "could not read your text for this item" with its WIDER status, and its file is unchanged until the researcher saves
Pinned by: `tests/unit/test_reporting.py::test_versions_capped`, `tests/unit/test_reporting.py::test_clear_keeps_versions`, `tests/unit/test_reporting.py::test_unreadable_item_file`

#### Scenario: Text from a file and from the draft
- **WHEN** `wider report prisma edit 6 --file text.md` runs, then `wider report prisma edit 6 --from-draft`
- **THEN** the first save holds the file's text, the second the current draft of item 6 marked as taken from the draft, and "Use the draft" on the page sends the same fields as `--from-draft`
Pinned by: `tests/unit/test_reporting.py::test_text_from_file_and_draft`, `tests/unit/test_reporting_js_node.py::test_use_draft_and_keep_send_edit_fields`

#### Scenario: Unknown item or element
- **WHEN** `wider report prisma edit 99 --text x` or `--covers nonsense` runs
- **THEN** the CLI exits with code 1 naming the unknown item or element, the route returns 400 with the same message, and nothing is written
- **AND** `edit 9 --from-draft` exits with code 1 "no draft for this item", the route returns 400, and item 9's panel shows no "Use the draft"
Pinned by: `tests/unit/test_reporting.py::test_unknown_item_or_element`, `tests/unit/test_reporting_js_node.py::test_no_draft_hides_use_draft`

### Requirement: Your text column
The system SHALL show beside each item's status the state of the researcher's text: "none", "written", "written, covers what was missing" when every missing element is marked covered by it, or "outdated". Marks are the researcher's own and are shown as "you marked as covered by your text"; WIDER does not read the text to set them.
Surfaces: CLI `wider report prisma status`; UI item table "Your text" column, element tick boxes.

#### Scenario: Covers what was missing
- **WHEN** item 5 misses only "how studies were grouped for syntheses" and the researcher saves text with `--covers synthesis_groups`
- **THEN** "Your text" reads "written, covers what was missing", the status stays "Partially documented", and the element reads "you marked as covered by your text"
Pinned by: `tests/unit/test_reporting.py::test_your_text_covers_missing`

### Requirement: Not applicable with a reason
The system SHALL let the researcher mark any item not applicable only with a non-empty reason, show WIDER's own status and evidence beside the mark, say once in black when WIDER recorded evidence for the item, and let the mark be cleared.
Surfaces: CLI `wider report prisma na ITEM (--reason R|--clear) [--by NAME]`; UI item panel, "Not applicable" with its reason field, "Remove the not-applicable mark".

#### Scenario: Mark and clear
- **WHEN** `wider report prisma na 12 --reason "no quantitative synthesis"` runs, then `--clear`
- **THEN** item 12 reads "Not applicable" with the reason, then "Researcher input needed" again
- **AND** `na 12` without a reason exits with code 1 "a reason is required", the page's button stays disabled until a reason is typed, and "Remove the not-applicable mark" sends the same as `na 12 --clear`
Pinned by: `tests/unit/test_reporting.py::test_not_applicable_mark_and_clear`, `tests/unit/test_reporting_js_node.py::test_na_needs_reason`, `tests/unit/test_reporting_js_node.py::test_na_clear_button`

#### Scenario: Evidence exists
- **WHEN** item 16a, which has WIDER evidence, is marked not applicable
- **THEN** the mark is saved, and the item says "WIDER recorded evidence for this item" with the evidence still shown
Pinned by: `tests/unit/test_reporting.py::test_not_applicable_with_evidence`

### Requirement: Generated evidence refreshed, outdated text flagged
The system SHALL store with each save its protocol and set and a fingerprint of the item's evidence lines for them, and SHALL mark the text "outdated" when that evidence now differs, showing saved and current lines. Rebuilding without a data change, or choosing another set, never outdates text. "Keep my text" re-saves it against the current evidence.
Surfaces: CLI `wider report prisma status|show|edit|na|clear [--protocol P] [--set N]`, `edit --keep`; UI set selects, "Keep my text".

#### Scenario: New import outdates item 6
- **WHEN** text for items 6 and 24a is saved, then a third platform is imported
- **THEN** item 6's text reads "outdated" with the old and the current evidence lines, item 24a's does not, and the text itself is unchanged
- **AND** "Keep my text" (or `edit 6 --keep`) clears the flag and records one History entry
Pinned by: `tests/unit/test_reporting.py::test_new_import_outdates_item_6`

#### Scenario: Another set chosen
- **WHEN** text for item 16a is saved under protocol `main` set 1, and the status is then listed with `--protocol main --set 2` (or set 2 is chosen on the page)
- **THEN** item 16a's text does not read "outdated", and the item says the text was saved for `main` set 1
- **AND** `status --protocol main --set 2 --json` gives item 16a `saved_for` "main, set 1", and `export --protocol main --set 2` writes "written for main, set 1" in its "saved for" column
Pinned by: `tests/unit/test_reporting.py::test_switching_set_does_not_outdate`

#### Scenario: Text saved before screening
- **WHEN** after the first import and before any screening, text is saved for PRISMA 2020 item 16a and PRISMA-S item 3, and one screening run follows
- **THEN** neither save shows a "saved for" set, item 16a's text then reads "outdated" and PRISMA-S item 3's does not
- **AND** before any import, `status`, `show` and `edit` refuse with a message starting "nothing imported yet", and the page says the same
Pinned by: `tests/unit/test_reporting.py::test_text_saved_before_screening`, `tests/unit/test_reporting.py::test_status_nothing_imported`

#### Scenario: Two builds, same fingerprint
- **WHEN** the status is listed twice with no change in between, on a different day
- **THEN** no item's text reads "outdated"
Pinned by: `tests/unit/test_reporting.py::test_rebuild_does_not_outdate`

### Requirement: Possible inconsistencies in outdated text
The system SHALL list, for outdated text, each number in the text that occurred in the item's evidence at save time but no longer does, with the current numbers, as "Your text says N; WIDER's evidence for this item no longer contains N (now: M)", in red, said once. It never blocks saving or export.
Surfaces: CLI `wider report prisma show ITEM`; UI item panel.

#### Scenario: Screened count changed
- **WHEN** item 16a's text says "62 records were screened" and two more records are screened afterwards
- **THEN** the item lists "Your text says 62; WIDER's evidence for this item no longer contains 62 (now: 64)"
Pinned by: `tests/unit/test_reporting.py::test_number_inconsistency`

### Requirement: Draft text from recorded facts only
The system SHALL offer a draft for items WIDER has facts for, built from fixed sentence templates filled with recorded values, never by an AI model, with each missing fact written as a bracketed gap such as "[search date: not recorded by WIDER]". The draft is not the researcher's text until they save it.
Surfaces: CLI `wider report prisma show ITEM` (draft), `edit ITEM --from-draft`; UI item panel, draft and "Use the draft".

#### Scenario: Item 6 draft
- **WHEN** item 6's draft is shown on the synthetic project
- **THEN** it names Scopus and EBSCOhost with their import dates as import dates, contains "[search date: not recorded by WIDER]", and no backend was called
- **AND** it does not contain "PsycINFO" and contains "[databases searched on EBSCOhost: not recorded by WIDER]"
Pinned by: `tests/unit/test_reporting.py::test_item_6_draft_no_model`

#### Scenario: Item 8 draft from the set's rules
- **WHEN** set 1 of `main` was screened with one pass per record and item 8's draft is shown
- **THEN** the draft says one pass per record and does not say "two independent reads"
Pinned by: `tests/unit/test_reporting.py::test_item_8_draft_reads_set_rules`

#### Scenario: Methods paragraph unchanged
- **WHEN** `wider export methods` runs on the synthetic project before and after the draft sentences are shared with the reporting items
- **THEN** the paragraph is identical
Pinned by: `tests/unit/test_reporting.py::test_methods_draft_unchanged`

#### Scenario: Viewing records no look
- **WHEN** a recall estimate is stored for the labelled probability sample, and item 8's draft, the status and `show 8` are produced
- **THEN** the draft's recall sentence is the stored estimate's outcome line with its exact bounds, level and method, the sample's look count is unchanged, and History gains no entry
- **AND** with no stored estimate the draft holds "[recall estimate: not recorded by WIDER]"
Pinned by: `tests/unit/test_reporting.py::test_viewing_records_no_look`, `tests/unit/test_reporting.py::test_viewing_records_no_look_with_stored_estimate`

### Requirement: Reporting exports
The system SHALL write to `exports/prisma_reporting/` both checklists as Markdown, CSV and JSON (item, topic, status, your text state, saved for, where reported, WIDER evidence, researcher text, n/a reason), and `methods_text.md`, and redraws `exports/prisma_2020.svg` as the Recall page does, keeping WIDER's lines and the researcher's text apart. Refused before any import.
Surfaces: CLI `wider report prisma export [--protocol] [--set N]`; UI PRISMA reporting page, "Export checklists".

#### Scenario: Export on a synthetic project
- **WHEN** the researcher saved text for item 8 and marked item 12 not applicable, and `wider report prisma export` runs
- **THEN** the folder holds the six checklist files and `methods_text.md`, and `exports/prisma_2020.svg` is redrawn; item 8 shows "WIDER recorded" and "Researcher text" apart; item 12 shows its reason; `methods_text.md` gives item 8's text and, for item 6, the draft under "WIDER draft, not reviewed by the researcher"
- **AND** the page's button writes the same files, equal except for their write time
Pinned by: `tests/unit/test_reporting.py::test_export_files_and_labels`, `tests/unit/test_reporting.py::test_export_cli_and_route_same`

#### Scenario: Nothing imported
- **WHEN** the export is asked for before any import
- **THEN** it refuses with a message starting "nothing imported yet" and writes nothing
Pinned by: `tests/unit/test_reporting.py::test_export_nothing_imported`

### Requirement: Word checklists in the official PRISMA structure
The system SHALL also write `prisma_2020_checklist.docx` and `prisma_s_checklist.docx`: one table each with the published columns and wording, citation and licence, and as location the researcher's "where reported" note, else "Researcher input needed: page or section" highlighted or "Not applicable: <reason>"; no status, tick or claim of compliance. A file edited since WIDER wrote it is kept.
Surfaces: CLI `wider report prisma export`; UI PRISMA reporting page, "Export checklists".

#### Scenario: Official checklist in Word
- **WHEN** item 8's "where reported" is "p. 6", item 12 is marked not applicable with the reason "no meta-analysis", and `wider report prisma export` runs
- **THEN** `prisma_2020_checklist.docx` has one table headed "Section and Topic", "Item #", "Checklist item", "Location where item is reported" with the section rows and 42 item rows, each item's text equal to its published wording in `prisma_items.yaml`; item 8's location reads "p. 6", item 12's "Not applicable: no meta-analysis", and item 5's "Researcher input needed: page or section" in a highlighted run
- **AND** no cell holds a WIDER status word, the document ends with the PRISMA 2020 citation and "CC BY 4.0", and `prisma_s_checklist.docx` has 16 item rows under the published PRISMA-S columns
- **AND** "Export checklists" on the page writes the same Word files, equal apart from their write time, and lists every file written with a download link, as the CLI prints their paths
Pinned by: `tests/unit/test_reporting_docx.py::test_checklist_docx_official`, `tests/unit/test_reporting.py::test_export_cli_and_route_same`, `tests/unit/test_reporting_js_node.py::test_export_lists_word_files`

#### Scenario: Second export after editing in Word
- **WHEN** the researcher edits `prisma_2020_checklist.docx` in place and exports again from the CLI or the page
- **THEN** the edited file is kept as "prisma_2020_checklist (edited <time>).docx", a new one is written, and the message, on the page as in the CLI, names the kept copy; an unedited file is replaced without a copy
- **AND** two such exports within the same second keep two copies, the second with " (2)"; a .docx or the flow svg with no recorded hash, or edited, is kept the same way
- **AND** when the file cannot be opened for writing because Word holds it open, the export refuses with "close prisma_2020_checklist.docx in Word and export again" before writing any file or stage result
Pinned by: `tests/unit/test_reporting_docx.py::test_export_keeps_edited_docx`, `tests/unit/test_reporting_docx.py::test_locked_docx_writes_nothing`, `tests/unit/test_reporting_js_node.py::test_export_message_shown`

#### Scenario: Without python-docx
- **WHEN** `python-docx` cannot be imported and the export runs from the CLI or the page
- **THEN** the Markdown, CSV, JSON and SVG files are written, no .docx file is, and the message ends "Word files not written: python-docx is not installed (pip install -e .)."
Pinned by: `tests/unit/test_reporting_docx.py::test_export_without_python_docx`, `tests/unit/test_reporting_js_node.py::test_export_message_shown`

### Requirement: Methods documentation in Word
The system SHALL also write `methods_documentation.docx`: the methods text item by item as `methods_text.md` gives it, WIDER drafts labelled as not reviewed and each bracketed gap highlighted; each platform's archived search string with its record counts; and a table of each item's WIDER status, your text state and what is still missing.
Surfaces: CLI `wider report prisma export`; UI PRISMA reporting page, "Export checklists".

#### Scenario: Methods documentation
- **WHEN** the researcher saved text for item 8, the synthetic project has two database imports with archived strings, and the export runs
- **THEN** "Methods text" gives item 8's text under "Researcher text" and item 6's draft under "WIDER draft, not reviewed by the researcher" with "[search date: not recorded by WIDER]" highlighted; "Search strategies" holds both strings verbatim under their platform names with their record counts; "Reporting status" has 58 rows whose statuses equal those of `wider report prisma status --json` for both checklists
- **AND** the document's author and last-modified-by properties read "WIDER"
Pinned by: `tests/unit/test_reporting_docx.py::test_methods_docx_content`

### Requirement: Editable PRISMA flow diagram
The system SHALL also write `prisma_2020_flow.docx`, the official PRISMA 2020 flow boxes as Word shapes and text boxes in one editable group, its source `prisma_2020_flow.svg` and `prisma_2020_flow.json`. Every count WIDER records equals `prisma_2020.json` for the protocol and set; a box of a stage WIDER does not run shows the researcher's count marked "†", else "researcher input needed" in a dashed box.
Surfaces: CLI `wider report prisma export`; UI PRISMA reporting page, "Export checklists".

#### Scenario: Counts consistent with the recorded workflow
- **WHEN** the synthetic set has 120 records from two databases (`scopus` and the multi-database `ebsco`), 20 duplicates removed, 100 screened, 85 excluded, 10 include and 5 maybe; the researcher entered 2 reports not retrieved, exclusions "wrong design" 4 and "no outcome" 3, 6 reports and 5 studies; and the export runs
- **THEN** the docx holds one `wpg:wgp` group of shapes whose texts read 120, 20, 100, 85, "Reports sought for retrieval (n = 15)" with "includes 5 unresolved maybe", 2†, "Reports assessed for eligibility (n = 13†)", "wrong design (n = 4†)", "no outcome (n = 3†)", 6† and 5†, every WIDER count equal to `prisma_2020.json`
- **AND** the `ebsco` line reads "EBSCOhost" with "databases searched on this platform: researcher input needed", and no file of the three contains "PsycINFO"
- **AND** the registers and automation-tools counts read "researcher input needed", the svg carries the same texts in the `editable-figures` SVG subset, and `prisma_2020_flow.json` gives each box's count with its source (wider, researcher, derived or missing)
Pinned by: `tests/unit/test_reporting_docx.py::test_flow_docx_counts_match`, `tests/unit/test_reporting_docx.py::test_flow_svg_in_skill_subset`

### Requirement: Flow counts entered by the researcher
The system SHALL store, per protocol and set, the researcher's counts for flow boxes WIDER does not record (registers, automation tools, not retrieved, excluded with reasons, studies, reports) apart from the records, journalled and undoable, and flag in red, without refusing, counts that do not add up or were saved before WIDER's counts changed. WIDER's counts cannot be entered.
Surfaces: CLI `wider report prisma flow [--count NAME=N] [--excluded "R=N"] [--clear]`; UI Flow diagram tab.

#### Scenario: Counts that do not add up
- **WHEN** with 120 records from databases, 20 duplicates removed, 100 screened and 15 reports sought the researcher saves on the Flow diagram tab 2 not retrieved, exclusions totalling 7 and 8 reports of included studies
- **THEN** the counts are saved and the tab shows once, in red, "Reports assessed (13) minus reports excluded (7) is 6, but reports of included studies is 8."; `wider report prisma flow` prints the same line
- **AND** with 10 registers entered, the tab and the CLI also show "Records identified (130) minus records removed (20) is 110, but WIDER holds 100 records after removal, screened or not yet screened (registers and automation-tool records are not in WIDER)."
- **AND** the store's tables other than `history` and `researchers` are byte-for-byte unchanged; `wider history undo ID` (or the tab's Undo offer) restores the previous counts, and "Clear my counts" (`flow --clear`) removes them, undoable the same way
Pinned by: `tests/unit/test_reporting.py::test_flow_counts_checks`, `tests/unit/test_reporting.py::test_flow_save_and_undo`, `tests/unit/test_reporting_js_node.py::test_flow_tab_checks_red`, `tests/unit/test_reporting_js_node.py::test_flow_tab_sends_counts`

#### Scenario: One count changed
- **WHEN** after a full save `wider report prisma flow --count registers=10` runs, then `--count registers=`
- **THEN** the first changes only the registers count and keeps the other counts and the reasons, the second removes the registers count, and `--excluded none` empties the reason list
- **AND** on the tab, emptying the registers field and saving sends the same removal, every field being sent
Pinned by: `tests/unit/test_reporting.py::test_flow_merge_and_remove`, `tests/unit/test_reporting_js_node.py::test_flow_tab_sends_counts`

#### Scenario: Before import or screening
- **WHEN** nothing is imported, or records are imported but none screened, and the tab, `GET /api/reporting/flow` or `wider report prisma flow` is used
- **THEN** before import each refuses with "nothing imported yet"; before screening each shows the boxes with "no screening yet" and a save is refused with that message, and nothing is written
Pinned by: `tests/unit/test_reporting.py::test_flow_before_import_or_screening`

#### Scenario: WIDER's counts changed
- **WHEN** counts were saved, then one more synthetic file is imported and deduplicated
- **THEN** the tab and `wider report prisma flow` say "WIDER's counts changed since you saved these counts: check them." with the old and new WIDER counts side by side
Pinned by: `tests/unit/test_reporting.py::test_flow_outdated`

#### Scenario: Another set chosen
- **WHEN** counts were saved for set 1 and the tab or `wider report prisma flow --set 2` shows set 2
- **THEN** set 2's entered boxes read "researcher input needed", the line "Counts were entered for main, set 1." shows once, no "WIDER's counts changed" line shows, and set 2's export uses none of set 1's counts
Pinned by: `tests/unit/test_reporting.py::test_flow_counts_per_set`, `tests/unit/test_reporting_js_node.py::test_flow_tab_other_set_notice`

#### Scenario: Bad counts refused
- **WHEN** the researcher enters -1 or 2.5 on the tab or with `wider report prisma flow`, a reason without a count or a count without a reason, or with the CLI or the route a count for "screened", "excluded" or "foo"
- **THEN** the save is refused, and nothing is written, with "counts are whole numbers of 0 or more", "each excluded row needs a reason and a count", "Records screened is recorded by WIDER and cannot be entered" (likewise "Records excluded ...") or "unknown count foo; one of registers, automation_removed, not_retrieved, studies, reports"
- **AND** the tab shows the refusal message as the route returns it
Pinned by: `tests/unit/test_reporting.py::test_flow_bad_input`, `tests/unit/test_reporting_js_node.py::test_flow_tab_sends_counts`

### Requirement: No researcher named for reporting text
The system SHALL NOT name the saver of reporting text in any export, share bundle or replication package; the text is labelled "Researcher text". Names in WIDER's lines follow the attribution setting; the researcher's text is exported as typed, and a hidden name in it refuses the share bundle and package.
Surfaces: CLI `wider report prisma export`, `wider export prisma-checklist`, `wider export prisma-s`, `wider report --bundle`, `wider export package`; UI "Export checklists".

#### Scenario: Name absent from exports
- **WHEN** reviewer "rev-one" saves text for item 8 and PRISMA-S item 9 and undoes the item 8 save, the validation sample was labelled by "lab-two", and the reporting exports, both checklists, `wider report --bundle` and the replication package are written
- **THEN** "rev-one" appears in no file under `exports/` (the .docx files read unzipped) and in no file of the package zip, and with the default attribution item 8's WIDER lines name lab-two's pseudonym "Researcher n", not "lab-two"
- **AND** `wider history list --kind report_text` shows "rev-one" as the author
Pinned by: `tests/unit/test_reporting.py::test_saver_name_not_exported`

#### Scenario: A hidden name in the researcher's text
- **WHEN** PRISMA-S item 9's text mentions "lab-two", whom the attribution setting hides, and the reporting export, `wider report --bundle` and the replication package are asked for
- **THEN** `exports/prisma_reporting/` and `reporting/prisma_s/9.json` keep "lab-two" as typed, and the bundle and the package are refused, exit 1, naming "PRISMA-S item 9" and "edit your text"
- **AND** a .docx whose hidden name is split over two runs is refused the same way, while a search string in the methods documentation that equals the name is kept and not refused
Pinned by: `tests/unit/test_reporting.py::test_hidden_name_in_text_refuses_package`, `tests/unit/test_reporting_docx.py::test_docx_is_name_scanned`, `tests/unit/test_reporting_docx.py::test_search_term_equal_to_hidden_name`

### Requirement: Reporting text is not a researcher intervention
The system SHALL leave `report_text` History entries, and undos of them, out of the "Researcher interventions" of `report.md`, `report.json`, the share bundle and the replication package, neither counted nor listed, as an exception to "Screening agreement and researcher interventions in the report"; History keeps them.
Surfaces: CLI `wider report`, `wider report --bundle`, `wider export package`; UI Recall page, report and package cards.

#### Scenario: Saves not in the report
- **WHEN** the researcher saves text for items 8 and 9, undoes the item 9 save, adds one note, and `wider report` runs
- **THEN** "Researcher interventions" counts the note only and shows no "PRISMA reporting text" kind, no entry for the saves and no entry for the undo
Pinned by: `tests/unit/test_reporting.py::test_report_text_not_an_intervention`

### Requirement: PRISMA reporting page
The system SHALL show a page "PRISMA reporting", tabs "PRISMA 2020", "PRISMA-S" and "Flow diagram", protocol and set selects; status and "To check" filters, items and one open item. Each checklist tab says how the two relate; items link to and can reuse related items across tabs. It says once in black it is an owner view. Status words are normal size and black; outdated and inconsistency lines are red.
Surfaces: UI PRISMA reporting page (`#reporting`); CLI `wider report prisma status`/`show`.

#### Scenario: Tabs and link
- **WHEN** the page opens and item 7 is opened, then its PRISMA-S link is followed
- **THEN** the page says once "An owner view", the PRISMA 2020 tab shows 42 rows under the official section headings, item 7's panel shows "PRISMA-S reports this in detail:" with links to PRISMA-S items 8, 9, 10, 11, 12 and 14 and their statuses, and the item 8 link opens the PRISMA-S tab with 16 rows and item 8 open
- **AND** `wider report prisma show 7` prints the same block
Pinned by: `tests/unit/test_reporting_js_node.py::test_tabs_and_item_7_link`, `tests/unit/test_reporting.py::test_show_7_points_to_prisma_s`

#### Scenario: Related items on the other tab
- **WHEN** on the PRISMA-S tab set 1 and the "Researcher input needed" filter are chosen, item 10 is opened and its link "Part of PRISMA 2020 item 7:" is followed
- **THEN** the PRISMA 2020 tab opens with item 7 open, set 1 still selected and the shared filter reset to all, and each checklist tab shows its one sentence on how the two relate
- **AND** `wider report prisma show 10 --checklist prisma-s --json` returns `related` with PRISMA 2020 item 7, its topic and status
Pinned by: `tests/unit/test_reporting_js_node.py::test_related_links_switch_tab`, `tests/unit/test_reporting.py::test_related_both_ways`

#### Scenario: Start from a related item's text
- **WHEN** PRISMA-S item 13 holds the text "Searched on 3 March." and PRISMA 2020 item 6 is opened
- **THEN** its block shows item 13's first line and "Start from this text", which fills the text box and saves nothing until Save
- **AND** `wider report prisma edit 6 --from-item prisma-s:13` saves that text for item 6 with `from_item` "prisma-s:13"; `--from-item prisma-s:8` exits 1 "not a related item" and `--from-item prisma-s:7` exits 1 "PRISMA-S item 7 has no text"
Pinned by: `tests/unit/test_reporting_js_node.py::test_start_from_related_text`, `tests/unit/test_reporting.py::test_edit_from_item`

#### Scenario: To check
- **WHEN** the researcher saved text for item 6 and another platform is imported afterwards
- **THEN** the "To check" filter reads 1 and shows only item 6 with its red "outdated" line, matching the rows of `wider report prisma status --json` whose "Your text" is "outdated"
- **AND** after PRISMA-S item 13's draft is taken with its gap "[search date: not recorded by WIDER]" left in, the PRISMA-S tab's "To check" reads 1, item 13 says "Your text still has 1 gap in brackets", and its `status --checklist prisma-s --json` row has `gaps_left` 1
Pinned by: `tests/unit/test_reporting_js_node.py::test_to_check_filter`, `tests/unit/test_reporting.py::test_new_import_outdates_item_6`, `tests/unit/test_reporting.py::test_gaps_left_to_check`

#### Scenario: Undo after a save
- **WHEN** text is saved on the page
- **THEN** the page offers Undo for that change, and Undo restores the item's previous text, as `wider history undo ID` does
Pinned by: `tests/unit/test_reporting.py::test_undo_restores_previous_text`, `tests/unit/test_reporting_js_node.py::test_undo_offer_after_save`

### Requirement: Reporting exports in the replication package
The system SHALL put the reporting CSV and Word files, the flow svg and json and `methods_text.md`, rebuilt when the package is built, under `prisma_reporting/` in the replication package, each with its SHA-256 in the manifest and through its secret scan; the Markdown and JSON checklists stay once at the zip root, and item 27 names the package. Files under `reporting/` are not packaged.
Surfaces: CLI `wider export package`; UI Recall page, Replication package card.

#### Scenario: Package carries the reporting exports
- **WHEN** the researcher saved text for item 8 and the replication package is built
- **THEN** the zip holds `prisma_reporting/prisma_2020_checklist.csv`, `prisma_reporting/prisma_s_checklist.csv`, `prisma_reporting/methods_text.md`, the four .docx files, `prisma_2020_flow.svg` and `.json` under `prisma_reporting/`, each listed with its SHA-256 in `MANIFEST.txt`; `prisma_2020_checklist.md`, `.json`, `prisma_s.md`, `.json` and `prisma_2020.svg` once each at the zip root, with item 8's researcher text; and no file from `reporting/`
- **AND** `scan_for_secrets` given a .docx whose key-shaped synthetic string is split over two runs reports it under `prisma_reporting/<name>.docx`, and the package build refuses
- **AND** a package built before any import still builds, without them
Pinned by: `tests/unit/test_reporting.py::test_package_carries_reporting_exports`, `tests/unit/test_reporting_docx.py::test_docx_is_secret_scanned`

### Requirement: Report command keeps working as a group
The system SHALL keep `wider report` with its options writing the report when no subcommand is given, and offer the reporting commands as `wider report prisma ...`.
Surfaces: CLI `wider report [--protocol] [--sample] [--set N] [--bundle]`, `wider report prisma --help`; UI none.

#### Scenario: Old invocation and new group
- **WHEN** `wider report --protocol main --set 1` runs, then `wider report prisma --help`
- **THEN** the first writes `exports/report.md` and `report.json` as before, and the second lists `status`, `show`, `edit`, `na`, `clear`, `flow` and `export`
- **AND** `wider report --help` still names the latest protocol with screening decisions and the last labelled probability sample as defaults
Pinned by: `tests/unit/test_reporting.py::test_report_group_keeps_old_invocation`, `tests/unit/test_recall.py::test_report_help_states_the_real_defaults`
