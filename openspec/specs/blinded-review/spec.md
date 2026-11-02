# blinded-review Specification

## Purpose
Blinded independent review lets a second reviewer label development or validation records in a standalone page
that holds no other judgment, and lets the researcher import those labels, resolve disagreements and report which
labels were made blind. It is optional; a project with one reviewer is unchanged.

## Requirements

### Requirement: Making a review package
The system SHALL make, for a reviewer and samples of one kind (development or validation), one self-contained review file in mode `second` or `independent`, fix its items and criteria version, keep a copy, and record it in History as a change that cannot be undone, first saying once what it holds and that it leaves the computer when sent.
Surfaces: CLI `wider blinding package R --sample S --mode M [--earlier E] [--codebook-version N] --out F`, `package-file`; UI card "Review packages".

#### Scenario: Independent and second modes
- **WHEN** synthetic sample `dev` (purposive) has 20 records and `validation` (probability) has 14 includes and maybes and 186 excludes by R1, and `wider blinding package R2 --sample dev --mode independent --out r2-dev.html` and `wider blinding package R3 --sample validation --mode second --out r3-val.html` run
- **THEN** the first file holds 20 items and the second 28 (the 14 and 14 excludes chosen with the project seed), each in an order shuffled with the project seed and keyed by an item token, and `wider blinding status --json` lists both packages with reviewer, mode, sample names, item count, codebook version and made time
- **AND** a copy of each file is in the project's `review-packages/` folder under the package id, and `wider blinding package-file <id> --out again.html` writes a byte-identical copy
Pinned by: `tests/unit/test_package.py::test_make_independent_and_second`, `tests/unit/test_package.py::test_copy_kept_and_written_again`

#### Scenario: Told that the file leaves the computer
- **WHEN** `wider blinding package R3 --sample validation --mode second --out r3-val.html` runs, and the card's "Make a review package" dialog is filled with the same values
- **THEN** the CLI prints once, before "wrote r3-val.html", "This file leaves your computer when you send it. It holds the titles, abstracts, authors, years, journals and DOIs of 28 records, the research questions, construct definitions and criteria (codebook version 3), the project title and the reviewer name R3. It holds no decision, note, tag, model output or sample name."
- **AND** the dialog shows the same text once, in normal-size black text, above its "Download" button, has a codebook-version choice that lists the versions the CLI accepts, and the download saves the file the CLI would write for the same package, named `review-package-R3-<package id>.html` with the reviewer name made safe for a file name
Pinned by: `tests/unit/test_package.py::test_leaves_computer_sentence_cli`, `tests/unit/test_blinding_js_node.py::test_dialog_says_file_leaves_computer`, `tests/unit/test_blinding_js_node.py::test_dialog_asks_codebook_version`

#### Scenario: One kind per package, and bad input
- **WHEN** `wider blinding package R2` runs with `--sample dev --sample validation`, with `--sample nosuch`, with `--mode both`, with `--sample dev --mode second`, for R2 on `dev` while R2's earlier package on `dev` is still open, in a project with no saved codebook, for R1 in `second` mode on `validation` where R1 made the first labels, or on a sample with no records
- **THEN** it prints "A package holds development or validation records, not both.", "no sample 'nosuch'", "mode must be second or independent", "second mode is for validation samples; use independent for development records.", "R2 already has an open package on dev; import or withdraw it first.", "Save the criteria first: the package carries them.", "R1 made the first labels on validation; a second-reviewer package is for someone else." or "The package would hold no record.", exits with code 1 and writes no file and no package
- **AND** `POST /api/blinding/package` answers 400, 404, 400, 400, 409, 409, 400 and 400 with the same texts
Pinned by: `tests/unit/test_package.py::test_bad_package_refused`

#### Scenario: A package for a locked sample
- **WHEN** `dev` is locked with 10 of its 20 records labelled by R1, and `wider blinding package R2 --sample dev --mode independent --out r2.html` runs, and the same through the card
- **THEN** the package is made with the 20 records, as for an open sample, and no text says that the sample is locked
Pinned by: `tests/unit/test_label_completion.py::test_package_on_a_locked_sample`

#### Scenario: Making a package cannot be undone
- **WHEN** the owner runs `wider history undo` on the change "Review package made for R3 (28 records)", or presses Undo on it in History
- **THEN** both refuse with "A review package made stays on record, because the file may already have been sent; withdraw it instead." and the package stays open
Pinned by: `tests/unit/test_package.py::test_package_made_is_permanent`

#### Scenario: A second package holds only new records
- **WHEN** R2's independent package on `dev` (20 items) was imported for 15 items, a new package for R2 on `dev` is asked for; then R2 finishes, the package is imported in full, `dev` is extended by 10 records, and a new independent package for R2 on `dev` is made
- **THEN** the first request is refused with "R2 already has an open package on dev; import or withdraw it first.", since a package with undecided items stays open, and the new package holds only the 10 new records, with no `--earlier` choice asked for R2's package labels
Pinned by: `tests/unit/test_package.py::test_second_package_only_new_records`

#### Scenario: A new project can make and import a package
- **WHEN** a project created with this version, which never ran an upgrade, makes a package for R2, and R2's export is imported
- **THEN** the package carries the project's id, created on first use, and the import is accepted
Pinned by: `tests/unit/test_package.py::test_new_project_has_project_id`

### Requirement: What a review package holds
The system SHALL put into a review file only: per item, a token, title, the abstract the model judged with its source, authors, year, journal and DOI; once, the package id, a random project id, the made time, project title, reviewer name, research questions, construct definitions, the criteria and their version that the first reviewer used, and the limits text; never anything that reveals a judgment or how the record was chosen.
Surfaces: CLI `wider blinding package`; UI the review page.

#### Scenario: Nothing that reveals a judgment
- **WHEN** a synthetic project holds marker strings in R1's reasons, notes and tags, a model decision's reason and confidence, a band name, a sample name, an owner note and an adjudication, and packages are made in both modes
- **THEN** no package file, and no text the page shows when run, contains any marker, sample name or kind, `record_id`, score, band, pool, rank, weight, model or backend name, selection reason, decision, note or tag of another person, in the page's own text (outside the records' fields and the criteria) the words "second-reviewer", "independent", "development", "validation" or "probability", or any person's name other than the reviewer's
Pinned by: `tests/unit/test_package.py::test_marker_strings_never_in_package`, `tests/unit/test_package_page_node.py::test_page_text_has_no_marker`

#### Scenario: The abstract the model judged, with its source
- **WHEN** record A was screened on its imported abstract (version 1), then enriched from OpenAlex (version 2); record B was enriched before screening and screened on version 2; record C was never screened; and a package holds all three
- **THEN** A shows version 1, B and C their latest version, each with a small mark "abstract: imported" or "abstract: OpenAlex" that opens the source, words and retrieval date on request
- **AND** making the package says once "1 item shows an older abstract than the current one, because the model judged that one.", and the package records each item's abstract version
Pinned by: `tests/unit/test_package.py::test_abstract_version_the_model_judged`, `tests/unit/test_package_page_node.py::test_source_mark_opens_provenance`

#### Scenario: The criteria version the first reviewer used
- **WHEN** R1's labels on `validation` all carry codebook version 3 while version 4 is frozen, and a package for R3 on `validation` is made; then R1 relabels 5 records under version 4 and another package is made
- **THEN** the first package carries version 3's questions, construct definitions, elements, rules and exclusion reasons; the second refuses with "R1's labels on validation use codebook versions 3 and 4; choose one with --codebook-version."
- **AND** with `--codebook-version 4` it carries version 4; with no first-reviewer labels that carry a version (development labels saved before this change) it carries the latest frozen version, else the latest approved one marked "These criteria are not frozen yet.", and says once "The first reviewer's criteria version is not recorded; the package carries version 4."; with neither, it refuses with "Approve the criteria first"
Pinned by: `tests/unit/test_package.py::test_criteria_version_of_first_reviewer`

#### Scenario: Development labels record their criteria version
- **WHEN** R1 labels a record of the purposive sample `dev` under codebook version 3
- **THEN** the label stores codebook version 3, and a later package on `dev` carries version 3
Pinned by: `tests/unit/test_package.py::test_development_labels_record_version`

#### Scenario: The page asks nothing of the network
- **WHEN** a package is made for records whose titles and abstracts hold `<script>alert(1)</script>`, `<img src=https://example.org/x>` and `</script>`
- **THEN** the file has a content-security rule that allows no outside request, no `src` or `href` to another host except DOI links with `rel="noreferrer noopener"`, and the page shows those texts as plain text without running or loading anything
Pinned by: `tests/unit/test_package.py::test_page_self_contained_no_requests`, `tests/unit/test_package_page_node.py::test_texts_rendered_as_text`

### Requirement: Working in the review page
The review page SHALL let the reviewer set per item include, exclude or maybe, an exclusion reason, criteria ticks, their own tags and a note, by mouse or keys; keep the work in the browser if it can, else say so once; and export the decisions as one JSON file holding the package id, the project id, an export counter, item tokens and the reviewer's entries only.
Surfaces: UI review page (no CLI twin: the CLI writes and reads its files, `wider blinding package`, `wider blinding import`).

#### Scenario: Deciding with keys and the mouse
- **WHEN** the reviewer opens the page, presses `1` on the first item, picks exclusion reason "E2" and ticks element `population` as "no" on the second and presses `2`, and clicks "Maybe" on the third, adding the tag "check full text" and a note
- **THEN** the page shows "3 of 28 decided", and "Export my decisions" saves `wider-decisions-R3-<package id>.json` holding the package id, the project id, the reviewer name, the codebook version, the export counter, the export time and three entries with item token, decision, reason, criteria ticks, tags, note and time, and nothing else
- **AND** typing "1", "2", "j" or "k" in the note, tag or reason field changes no decision and moves to no other item
Pinned by: `tests/unit/test_package_page_node.py::test_keys_and_mouse_decide`, `tests/unit/test_package_page_node.py::test_export_holds_only_entries`, `tests/unit/test_package_page_node.py::test_keys_ignored_in_text_fields`

#### Scenario: Work kept in the browser, or not
- **WHEN** the reviewer decides 5 items and reloads the page; and the page is opened where the browser's storage throws on write
- **THEN** after the reload the 5 decisions are back; where storage fails the page says once, in normal-size black text, "This browser does not keep your work in this page. Export your decisions before you close it." and does not say that the work stays in the browser
- **AND** "Continue from a decisions file" with an earlier export of the same package restores its entries, and a file of another package is refused with "This decisions file belongs to another package."
Pinned by: `tests/unit/test_package_page_node.py::test_work_restored_after_reload`, `tests/unit/test_package_page_node.py::test_storage_failure_said_once`, `tests/unit/test_package_page_node.py::test_continue_from_file`

#### Scenario: The criteria beside the records
- **WHEN** the page is opened where storage works
- **THEN** it shows the research questions, construct definitions and criteria once in a panel the reviewer can open beside the records, and above the records, once, "This page holds no other person's decisions and no model output. Your work stays in this browser until you export it."
- **AND** the page shows the limits text of "One or two reviewers, and the limits, said once" once, and never the one-or-two-reviewers text
Pinned by: `tests/unit/test_package_page_node.py::test_criteria_panel_and_notice`

### Requirement: Importing a reviewer's decisions
The system SHALL import a decisions file only into the project and package that made it, signing each label with the package's reviewer and marking the package, criteria and abstract versions, as one undoable change. A later export updates its labels; older or withdrawn ones are refused. On a locked sample no correction reason is asked: new labels are missing ones, re-reviews keep revision rows.
Surfaces: CLI `wider blinding import FILE`; UI card "Review packages", "Import decisions file".

#### Scenario: Import matches and signs
- **WHEN** R3's export of package P (28 items, 20 decided) is imported with `wider blinding import`, and once through the card, each into its own copy of the synthetic project
- **THEN** both store 20 labels by R3 on the right records of `validation`, each with P's id, codebook version 3 as frozen in P, the item's abstract version, R3's tags and `after_look` = 1 only when a validation look on `validation` existed before P was made, print or show "R3: 20 of 28 decided; 20 imported.", and History has one change "R3's decisions imported from review package P (20 labels)"
- **AND** a name other than R3 inside the file is ignored and said once: "The file names Ana; its labels are stored as R3's, the package's reviewer."
Pinned by: `tests/unit/test_package.py::test_import_matches_and_signs`, `tests/unit/test_package.py::test_imported_label_criteria_state`, `tests/unit/test_blinding_js_node.py::test_import_posts_file`

#### Scenario: Files that do not belong
- **WHEN** an export of a package made by another project, a file with an unknown package id, a file whose package was withdrawn, and a file with 2 tokens not in its package and one decision "perhaps" are imported
- **THEN** the first two are refused with "This decisions file is not from a package of this project.", the third with "Package P was withdrawn; its decisions are not imported.", and the fourth imports the rest and says "2 items are not in this package and 1 decision is not include, exclude or maybe; they were skipped."
- **AND** each refusal exits with code 1, stores nothing, and the route answers 400, 404 or 409 with the same text
Pinned by: `tests/unit/test_package.py::test_foreign_or_bad_files`

#### Scenario: A later export on a locked sample
- **WHEN** `dev` is locked after R2's first import from package P, and R2's later export changes record 4 from include to exclude
- **THEN** the label is updated, and `wider label revisions dev --json` lists a revision with cause "package update", the old and new decision and P's id
Pinned by: `tests/unit/test_label_completion.py::test_later_export_on_a_locked_sample_is_recorded`

#### Scenario: Import into a locked sample
- **WHEN** R2's package on `dev` was made while `dev` was open, `dev` is then locked, and R2's export with 20 decisions is imported
- **THEN** the 20 labels are stored as in "Import matches and signs", no correction reason is asked, and the import is one undoable change
Pinned by: `tests/unit/test_label_completion.py::test_package_on_a_locked_sample`

#### Scenario: Records that left the sample
- **WHEN** after P was made, a dedup re-run merged one of its records into another and a redraw dropped two, and R3's export is imported
- **THEN** the merged record's entry is stored on the record it was merged into, the two dropped ones are skipped, and the import says once "2 items left the sample since the package was made; they were skipped."
- **AND** a later dedup re-run whose source file no longer holds one of P's records is not refused because of P's items, and the import skips that item in the same way
Pinned by: `tests/unit/test_package.py::test_items_that_left_the_sample`, `tests/unit/test_package.py::test_package_item_record_dropped_by_dedup`

#### Scenario: A later export updates, an older one is refused
- **WHEN** R3's export at 14:00 (20 decided) is imported, then R3's export at 16:00 (28 decided, 2 of the 20 changed), then the 14:00 file again
- **THEN** the second import adds 8 labels and changes 2 in one change, the History entry of each changed label holds its earlier decision, and the third answers "This file is older than the one already imported from package P (exported 16:00)." and changes nothing
- **AND** importing the 16:00 file a second time says "Nothing new: this file was already imported."
Pinned by: `tests/unit/test_package.py::test_later_export_updates_older_refused`

#### Scenario: Undoing an import
- **WHEN** the owner undoes the change "R3's decisions imported from review package P (20 labels)"
- **THEN** R3's 20 labels are removed, the package shows as not imported in the status, the same file then imports again, and redo stores them again as one change; an undo after a later change to one of those labels is refused with "undo the later change first"
Pinned by: `tests/unit/test_package.py::test_import_undo_redo`

#### Scenario: An import whose label was adjudicated since is not undone
- **WHEN** ana adjudicates a record on which R3's imported label disagrees, and the owner then undoes the import
- **THEN** the undo is refused, naming the record, the sample, ana and the adjudication's change with "undo that adjudication first", and nothing is removed; after ana's adjudication is undone, the import undoes and leaves no adjudication or reference correction behind
- **AND** History names a first adjudication on a sealed record "adjudicated record N in sample S, correcting its sealed reference", never "corrected the adjudication"
Pinned by: `tests/unit/test_package.py::test_import_undo_refused_while_its_adjudication_stands`, `tests/unit/test_label_completion.py::test_package_re_review_on_sealed_record`

#### Scenario: A package label changed in WIDER is no longer blind
- **WHEN** R3's imported label on a record is changed to include in the drawer
- **THEN** the label loses its package mark and counts as made in WIDER from then on (in the unblinded queue, or as a blind-queue label when changed in a blind queue), and its History entry holds the earlier, blinded decision
Pinned by: `tests/unit/test_package.py::test_label_changed_in_wider_loses_package_mark`

#### Scenario: Validation labels stay out of tuning
- **WHEN** R3's validation package is imported, and `wider screen evaluate`, `wider screen compare run` and their routes are asked to use `validation`
- **THEN** each still refuses as today, and no development figure, prompt comparison or calibration reads a label from a validation package
Pinned by: `tests/unit/test_package.py::test_validation_package_labels_never_tune`

#### Scenario: The other reviewer has not finished
- **WHEN** R2's independent package on `dev` is imported while R1 has labelled 12 of its 20 records
- **THEN** the import says once "R1 has not labelled 8 of these records yet. R2's labels are now visible in WIDER; for independent labels, R1 labels those first or works from a review package too."
Pinned by: `tests/unit/test_package.py::test_import_warns_other_reviewer_unfinished`

### Requirement: Package status and withdrawing a package
The system SHALL list each package with reviewer, mode, samples, items, codebook version, and made, imported and withdrawn times, saying when a sample changed after the package was made, and let the owner withdraw one with a reason as one undoable change; a withdrawn package's decisions are refused, and its `exclude` choice ends only if no import came first.
Surfaces: CLI `wider blinding status [--json]`, `wider blinding withdraw ID --reason TEXT`; UI card "Review packages", "Withdraw".

#### Scenario: Status on the card and the CLI
- **WHEN** packages P (R3, `validation`, imported) and Q (R2, `dev`, open) exist, and `validation` was extended by 40 records after P was made
- **THEN** the card's list and `wider blinding status` show both with the fields above, Q as "out since 25 Oct, not imported", P with "validation gained 40 records after this package was made; they are not in it", and `--json` gives the same values
Pinned by: `tests/unit/test_package.py::test_status_cli_and_route_same`, `tests/unit/test_blinding_js_node.py::test_package_list`

#### Scenario: Withdraw with a reason
- **WHEN** R2 stopped, and the owner runs `wider blinding withdraw Q --reason "R2 left the project"` or presses "Withdraw" on the card and gives the reason
- **THEN** Q shows as withdrawn with the reason, History has "Review package Q withdrawn: R2 left the project", and without a reason both refuse with "Give a reason."
- **AND** undoing the withdrawal reopens Q
Pinned by: `tests/unit/test_package.py::test_withdraw_needs_reason_and_undoes`, `tests/unit/test_blinding_js_node.py::test_withdraw_asks_reason`

#### Scenario: Withdrawing a package already imported
- **WHEN** P was imported with 20 labels and the owner withdraws P with a reason
- **THEN** the 20 labels stay stored and counted, still marked as made in P, the earlier labels P excluded stay left out of reviewer agreement, the status and the card say once "P was withdrawn after its decisions were imported; its 20 labels stay, and the labels it left out stay left out.", and undoing the withdrawal restores P's state
- **AND** a package with an `exclude` choice withdrawn before any import gives its excluded labels back to reviewer agreement
Pinned by: `tests/unit/test_package.py::test_withdraw_after_import`

### Requirement: A reviewer's earlier labels
When the reviewer already has labels not made in a review package on the package's samples, the system SHALL need a choice, retain, re-review or exclude, say once what it means, and record it on the package. Every label stays stored, in History, in final labels and in recall; none made in WIDER is marked blinded, and a re-reviewed one is marked a repeat.
Surfaces: CLI `wider blinding package ... --earlier retain|re-review|exclude`; UI the dialog's choice.

#### Scenario: A choice is needed
- **WHEN** R2 has 12 labels made in WIDER on `validation` and `wider blinding package R2 --sample validation --mode second` runs without `--earlier`, and the card's dialog is opened for the same values
- **THEN** the CLI exits with code 1 and prints "R2 already has 12 labels on these samples that were not made in a review package; choose --earlier retain, re-review or exclude." (with ", 4 of them from the blind queue" after "package" when some are), and both print and show once "Retain: they count as they are." Re-review: R2 labels those records again in the package, marked a repeat; this does not restore independence if R2 has seen other judgments. Exclude: they are left out of reviewer agreement and the blinded-review counts, and stay in final labels and recall."
Pinned by: `tests/unit/test_package.py::test_earlier_choice_needed`, `tests/unit/test_blinding_js_node.py::test_dialog_asks_earlier_choice`

#### Scenario: Retain, re-review, exclude
- **WHEN** the package is made with `--earlier retain`, in a second copy with `re-review`, and in a third with `exclude`, and each is then imported
- **THEN** with retain, the 12 records stay out of the package and R2's 12 labels count, each keeping its own `made_in` and `blinded` value
- **AND** with re-review, the 12 records are in the package, each imported label replaces R2's earlier one as current with `blinded` = repeat, the earlier kept in History and listed by `wider label revisions validation` with the cause "re-reviewed in review package Q", and the recall re-check line does not count them
- **AND** with exclude, the 12 records stay out of the package; `wider label agreement validation R1 R2`, the reviewer-agreement summary on Samples and labelling and the methods-draft kappa sentence leave the 12 out of kappa and of their disagreement count and say once "12 labels left out by review package Q"; `/api/labels/final`, `wider recall` and the reviewer-disagreement table still use them, marked left out; and `labels.csv` lists them with `left_out` = yes
- **AND** with each, the second-reviewer coverage in `wider validation status --json` and the recall review line count the package's items with the blind-queue items
Pinned by: `tests/unit/test_package.py::test_earlier_retain_rereview_exclude`, `tests/unit/test_blinding_js_node.py::test_agreement_summary_says_left_out`, `tests/unit/test_package.py::test_package_items_count_as_second_reviewer`

### Requirement: Resolving disagreements between reviewers
The system SHALL list per sample the records where two reviewers' labels differ, showing each label's decision, reason, criteria ticks, tags, note, how it was made, and its abstract version, with no model output; resolving one records an adjudication and keeps both labels.
Surfaces: CLI `wider blinding disagreements S --a R1 --b R2 [--all] [--json]`, `wider label adjudicate`; UI Samples and labelling, reviewer-versus-reviewer table, "Resolve".

#### Scenario: Disagreements listed and resolved
- **WHEN** R1 and R3 disagree on 4 records of `validation`, R3's labels came from package P, and one record's two labels used different abstract versions
- **THEN** the table and `wider blinding disagreements validation --a R1 --b R3 --json` list the same 4 records with the fields above, the one marked "R1 and R3 read different abstracts (imported, 80 words; OpenAlex, 240 words)", and no model decision, reason or confidence
- **AND** resolving one as include with a reason stores an adjudication, the record's final label is include, and R1's and R3's labels are unchanged
Pinned by: `tests/unit/test_package.py::test_disagreements_cli_and_route_same`, `tests/unit/test_blinding_js_node.py::test_reviewer_table_shows_details_no_model`

### Requirement: One or two reviewers, and the limits, said once
The system SHALL say once, in normal-size black text, on the "Review packages" card and in `wider blinding status`, what one reviewer and two independent reviewers mean for the results, never requiring two, and that a package keeps judgments out of the reviewer's view but does not prove who labelled.
Surfaces: CLI `wider blinding status`; UI card "Review packages".

#### Scenario: The two texts appear once
- **WHEN** the card is rendered and `wider blinding status` runs, in a project with no package and in one with two
- **THEN** each holds exactly once "One reviewer: the results rest on one person's labels, and their mistakes are not caught. Two independent reviewers: a second, blinded set of labels on the same records shows where the first may be wrong, and the disagreements are resolved before the results. A second reviewer is recommended for the validation includes and maybes, never required."
- **AND** exactly once "A review package keeps other judgments out of the reviewer's view because it holds none. It does not prove who labelled: WIDER checks that a decisions file matches its package, not who made it." — on the card in an element with neither a grey colour nor a font size below the body text
- **AND** a project with no package works everywhere as before, with no new step
Pinned by: `tests/unit/test_package.py::test_texts_once_in_status`, `tests/unit/test_blinding_js_node.py::test_texts_once_on_card`, `tests/unit/test_package.py::test_no_package_changes_nothing`
