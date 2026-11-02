# history-and-undo Specification

## Purpose
Keeps a journal of every manual change the researcher makes (labels, adjudications, duplicate decisions, artifact
statuses, notes, tags, answered questions and settings) with who made it and when, and lets any such change be undone
as long as nothing later depends on it. Automated runs are not journalled; they are reproduced from their run folders
instead. The journal serves every stage (`setup`, `construct`, `dedup`, `sample`, `screen`, `enrich`) and backs the
Undo offers and Ctrl/Cmd+Z in the local UI.

## Requirements

### Requirement: Journal of manual changes
The system SHALL record each manual change as one row in the `history` table with time, author, kind, a summary of at most 400 characters, the record id where one applies, and the before and after state of every row or file it touched. A change that fails or leaves everything as it was SHALL NOT be recorded. Label summaries SHALL NOT name the decision.
Surfaces: CLI `wider history list`; UI History page, record drawer history.

#### Scenario: Two labels make two changes, newest first
- **WHEN** reviewer "ana" labels a record include and then exclude
- **THEN** the history lists the second change before the first, both of kind `label` with that record id
- **AND** neither summary contains "include" or "exclude"
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

#### Scenario: Saving the same value records nothing
- **WHEN** a label is saved with the value it already has
- **THEN** no new change is recorded
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

### Requirement: Journalled kinds of change
The system SHALL journal labels (`labels`), adjudications (`adjudications`), duplicate decisions (`dedup_pairs`), artifact statuses (`artifacts`), notes (`record_notes`), tags (`record_tags`), answers (what an answer changes), settings (`project.yaml`), uploads final marks (`uploads_final`) and kept rankings (`ranking_kept`), and record undos as kind `undo`.
Surfaces: CLI `wider history list --kind K`, `wider history undo`; UI the pages that change these, History.

#### Scenario: Each kind of manual change can be undone
- **WHEN** an adjudication, a duplicate decision, an artifact approval, a note removal and a tag addition are each undone
- **THEN** the adjudication is gone, the pair is back in the queue and the artifact is draft
- **AND** the note is back with the same id and the record has no tags
Pinned by: `tests/unit/test_history.py::test_adjudication_dedup_artifact_and_annotations`

#### Scenario: The final mark by kind
- **WHEN** the uploads are marked final
- **THEN** `wider history list --kind uploads_final` lists the mark, and the History page's "Show" select offers "uploads marked final", which lists the same entry
Pinned by: `tests/unit/test_stage_pages.py::test_uploads_final_mark_matches_cli`

#### Scenario: A kept ranking by kind
- **WHEN** a ranking that needs review is kept
- **THEN** `wider history list --kind ranking_kept` lists it, the History page's "Show" select offers "ranking kept", and undoing it brings "needs review" back
Pinned by: `tests/unit/test_rank_keep.py::test_keep_and_undo`

### Requirement: Settings changes are journalled
The system SHALL record a change to `project.yaml` from the CLI or the UI as a `config` change summarised as "key: old → new" (at most six keys, then "and N more"), and SHALL NOT record creating a project. A `project.yaml` save made inside another change becomes part of that change. An older layout rewritten on save is not listed.
Surfaces: CLI `wider protocol set`, `wider history list --kind config`; UI Settings and Project setup pages, History page.

#### Scenario: A protocol setting is journalled with old and new value
- **WHEN** a new project is created and `wider protocol set screening.majority 3` is run with `WIDER_REVIEWER=ana`
- **THEN** the history was empty after creation
- **AND** it now starts with a `config` change by "ana" whose summary contains "protocol.screening.majority: 2 → 3"
Pinned by: `tests/unit/test_history.py::test_settings_changes_are_journaled_and_undone`

#### Scenario: A project in an older file layout
- **WHEN** a `project.yaml` with top-level `bands` (the older layout) is changed by `wider protocol set screening.passes 1`, then its title is changed on the page
- **THEN** the summaries are exactly "protocol.screening.passes: 2 → 1" and "title: '' → 'Voice review'"
- **AND** the bands are kept
Pinned by: `tests/unit/test_history.py::test_settings_summary_names_the_edit_not_an_older_file_layout`

#### Scenario: A value out of range is refused in plain words
- **WHEN** `wider protocol set ranking.top_n_compare "[0, 2]"` is run, or the page sets `corpus.thin_words` to "x"
- **THEN** the CLI exits 1 and the page gets HTTP 400 with "ranking.top_n_compare was not changed: item 1: input should be greater than or equal to 1" (or "corpus.thin_words was not changed: input should be a valid integer"), never a raw validation dump
- **AND** `project.yaml` is unchanged, so no change is journalled
- **AND** the page's number field sends "x" or an empty field as typed, never as 0
- **AND** a number too large to read is refused as "screening.max_tokens was not changed: the number is too large (got 1e+23)"
- **AND** after a refusal the page's field shows the stored value again, so it never looks saved
Pinned by: `tests/unit/test_ui_followup_r16.py::test_a_protocol_value_out_of_range_is_refused_in_plain_words`, `tests/unit/test_ui_followup_r17.py::test_a_refused_protocol_value_leaves_the_history_alone`, `tests/unit/test_ui_followup_r19.py::test_a_protocol_number_field_sends_what_was_typed`, `tests/unit/test_ui_followup_r23.py::test_a_number_too_large_to_read_is_refused_in_plain_words`, `tests/unit/test_ui_followup_r23.py::test_a_refused_protocol_value_goes_back_and_the_keyboard_stays_on_the_row`, `tests/unit/test_ui_followup_r29.py::test_a_number_too_large_to_read_is_refused_through_the_page`

#### Scenario: The keyboard after Set or Reset on the page
- **WHEN** a keyboard user presses Set or Reset on a protocol row and the table is redrawn
- **THEN** the focus is on that row's Set button, and after "Reset everything to the defaults" on that button
Pinned by: `tests/unit/test_ui_followup_r23.py::test_a_refused_protocol_value_goes_back_and_the_keyboard_stays_on_the_row`, `tests/unit/test_ui_followup_r23.py::test_set_and_reset_everything_put_the_keyboard_back`

#### Scenario: More than six settings at once
- **WHEN** eight settings are changed in one save
- **THEN** the summary names six of them as "key: old → new" and ends "; and 2 more"
- **AND** a save that changes no setting is summarised "project.yaml rewritten"
Pinned by: `tests/unit/test_ui_followup_r30.py::test_a_settings_summary_names_six_keys_and_counts_the_rest`

#### Scenario: A settings save inside another change
- **WHEN** the ranking's cluster count is saved inside a construct change
- **THEN** the history gains one change, the construct change, which lists `project.yaml` among what it touched
- **AND** the same save on its own is a `config` change whose summary starts "protocol.ranking.cluster_k: "
Pinned by: `tests/unit/test_ui_followup_r30.py::test_a_settings_save_inside_another_change_is_part_of_that_change`

### Requirement: Who made a change
The system SHALL attribute a change to the name passed explicitly (`--by`, a label's reviewer, a note's author). Else a page uses its `X-Wider-Author` header or no name, never an environment name. Else the CLI uses `$WIDER_REVIEWER`, then `$USER` (not for undo, redo, provenance, blinding). A page's job keeps its name. Names are kept to 80 characters. It is marked declared or inferred.
Surfaces: CLI `--by` options, `$WIDER_REVIEWER`; UI reviewer name kept in the browser, sent as `X-Wider-Author`.

#### Scenario: CLI and UI authors
- **WHEN** a setting is changed on the CLI with `WIDER_REVIEWER=ana` and the title is changed through the UI with header `X-Wider-Author: ben`
- **THEN** the first change is by "ana" and the second by "ben"
Pinned by: `tests/unit/test_history.py::test_settings_changes_are_journaled_and_undone`

#### Scenario: The UI sends the author header
- **WHEN** the UI script is loaded
- **THEN** it sends the `X-Wider-Author` header
Pinned by: `tests/unit/test_drawer_keys_node.py::test_page_requests_send_a_name_in_any_script`, `tests/unit/test_history.py::test_static_history`

#### Scenario: A page change without a name is never signed with the login name
- **WHEN** `$USER` is "os-login-name", a duplicate decision is posted from the page without a name, and a second one with `X-Wider-Author: tester`
- **THEN** the history authors are "" and "tester", and "os-login-name" appears nowhere in the journal or the pair table
- **AND** "Undo my last change" for "tester" undoes the second decision
- **AND** on the CLI the author is still `$USER`
Pinned by: `tests/unit/test_records.py::test_page_changes_are_signed_with_the_page_name_never_the_login_name`, `tests/unit/test_ui_followup_r17.py::test_the_cli_signs_with_wider_reviewer_then_user`

#### Scenario: A page change ignores the CLI's reviewer variable
- **WHEN** the server runs with `$WIDER_REVIEWER` and `$USER` set and the page saves a change without a name
- **THEN** the change has no author, and neither name appears in the history
- **AND** the CLI in the same shell still signs with `$WIDER_REVIEWER`
Pinned by: `tests/unit/test_ui_followup_r11.py::test_page_changes_ignore_wider_reviewer`, `tests/unit/test_ui_followup_r17.py::test_the_cli_signs_with_wider_reviewer_then_user`

#### Scenario: A job started from the page keeps the page's name
- **WHEN** a background job is started from the page with the name "tester", and once with no name
- **THEN** a change the job journals (a proposal job that changes a setting) is signed "tester", or left unsigned
Pinned by: `tests/unit/test_records.py::test_jobs_keep_the_page_name`, `tests/unit/test_records.py::test_a_job_started_from_the_page_signs_its_changes_with_the_page_name`

#### Scenario: A name in any script
- **WHEN** the page's name is "Łucja" or a name in Persian script
- **THEN** every page request still succeeds, and the change is signed with the name exactly as typed
- **AND** a hand-made header that is not valid percent-encoded UTF-8 counts as no name, and control characters are dropped
Pinned by: `tests/unit/test_history.py::test_a_name_in_any_script_is_recorded_as_typed`, `tests/unit/test_drawer_keys_node.py::test_page_requests_send_a_name_in_any_script`

#### Scenario: A long name
- **WHEN** a reviewer with a 105-character name adds a tag and a note from the page
- **THEN** both history entries are signed with the first 80 characters of the name, as the note and the tag are
- **AND** a duplicate-pair decision with that name stores the same 80 characters in the pair table and its history entry
- **AND** the drawer's own-changes history and "undo my last change" with the whole name find that reviewer's changes; undo-last undoes the pair decision, the newest of them, and leaves the note and the tag
- **AND** the history summaries of a label and an adjudication by that reviewer carry the same 80 characters
- **AND** `wider history list --author` with the whole name, or with spaces around it, lists the same changes as the page's filter
- **AND** a long name without spaces wraps inside the History table and the undo toast, so the Undo and Redo buttons stay in view
Pinned by: `tests/unit/test_history.py::test_a_long_name_is_kept_to_80_characters_everywhere`, `tests/unit/test_ui_followup_r17.py::test_a_long_name_is_cut_in_label_summaries`, `tests/unit/test_ui_followup_r17.py::test_long_names_wrap_in_toasts_and_the_history_table`, `tests/unit/test_ui_followup_r32.py::test_history_list_with_a_whole_long_name_finds_that_reviewer_s_changes`

#### Scenario: A pair decision without a name in the call
- **WHEN** a duplicate pair is decided from the command line with `$USER` set, or from the page with a name in the request header and none in the body
- **THEN** the pair table and the history entry carry the same name
Pinned by: `tests/unit/test_history.py::test_a_pair_decision_is_signed_with_the_history_author`, `tests/unit/test_history.py::test_a_pair_decision_on_the_command_line_is_signed_with_user`

#### Scenario: How the name was given is recorded
- **WHEN** one CLI change is made with `--by ana`, one with `WIDER_REVIEWER=rev-env`, and one from the page with `X-Wider-Author: ben`
- **THEN** History signs them "ana", "rev-env" and "ben", as before
- **AND** the researcher list marks "ana" and "ben" declared and "rev-env" inferred
Pinned by: `tests/unit/test_researchers.py::test_environment_names_are_inferred`, `tests/unit/test_researchers.py::test_pseudonyms_by_first_appearance`

### Requirement: Listing the history
The system SHALL list changes newest first, filtered by kind, record and author (CLI default 30, API default 100), each with the tables or files it touched and whether it can be undone or why not. The API SHALL NOT return stored rows. A blind history shows only the given reviewer's changes and SHALL be refused (HTTP 400) without a name.
Surfaces: CLI `wider history list [--kind] [--record] [--author] [--limit]`; UI History page, record drawer history (blind in blind mode).

#### Scenario: Blind view and hidden row contents
- **WHEN** "ana" and "ben" each label a record and ana's blind history of ben's record is requested
- **THEN** it is empty and a blind request without a name returns 400
- **AND** no history row carries its stored items
Pinned by: `tests/unit/test_history.py::test_history_routes_blind_and_undo_last`

#### Scenario: The CLI lists changes with their undo state
- **WHEN** a label is saved and `wider history list` is run
- **THEN** the output shows "ana labelled record" and "yes" in the undo column
Pinned by: `tests/unit/test_history.py::test_history_cli`

### Requirement: Undo only when nothing depends on the change
The system SHALL refuse to undo a change when a row or file it touched changed since ("changed since; undo the later change first"), when undone, when it froze an artifact ("a freeze is final"), or when it changed labels or adjudications of a sample locked then or since, or a validation record's before its latest look ("correct the label instead"). Refusals are HTTP 409 (CLI exit 1), an unknown id 404.
Surfaces: CLI `wider history undo <id>`; UI History page (Undo, or "cannot undo" and why).

#### Scenario: A later change blocks the earlier one
- **WHEN** a record is labelled include, then exclude, and the first change is undone
- **THEN** the undo is refused with "changed since"
- **AND** after undoing the second change and then the first, undoing the first again is refused with "already undone"
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

#### Scenario: A freeze cannot be undone
- **WHEN** an artifact version is frozen
- **THEN** its change is not undoable and the reason contains "freeze is final"
Pinned by: `tests/unit/test_history.py::test_adjudication_dedup_artifact_and_annotations`

#### Scenario: Settings undo in order through the API
- **WHEN** two settings changes were made and the older one is undone first through the API
- **THEN** the API returns 409
- **AND** undoing the newer and then the older returns 200 and restores both values
Pinned by: `tests/unit/test_history.py::test_settings_changes_are_journaled_and_undone`

#### Scenario: A locked sample's labels are not undone
- **WHEN** a label and an adjudication were saved in a sample that is then locked, and either change is undone by id or by undo-last
- **THEN** the undo is refused with HTTP 409 "sample 'dev' was locked after this change: correct the label instead, with a reason" and the label stays
- **AND** after unlocking, the label change is undone; a Redo of it is refused again once the sample is locked
Pinned by: `tests/unit/test_history.py::test_a_locked_samples_labels_are_not_undone`

#### Scenario: Changes made after the lock or the seal
- **WHEN** after `dev` was locked, R1 adds a missing label and corrects another with a reason, and on `validation` R1 labelled record 7 before its first recall estimate and corrects record 8 after it
- **THEN** the missing label and the correction on `dev` each undo as usual (the correction's row is gone and the earlier label is back) and both stay listed in History
- **AND** the undo of record 7's label is refused with "record 7 of validation was sealed after this change: correct the label instead, with a reason", while the correction of record 8 undoes as usual until recall is estimated again, after which its undo is refused in the same way
- **AND** on a sample imported locked (no lock in History), a label added afterwards undoes as usual
- **AND** for a record both locked and sealed, the seal's text is shown
- **AND** the History row of a label saved from the blind queue before the seal says "cannot undo: this label was locked or sealed after this change: correct it instead, with a reason", which contains neither "validation" nor "sample", also for a blind save made before this change
Pinned by: `tests/unit/test_label_completion.py::test_undo_before_lock_refused_after_lock_allowed`, `tests/unit/test_label_completion.py::test_undo_before_seal_refused`, `tests/unit/test_label_completion.py::test_blind_save_from_before_this_change_names_no_sample_once_sealed`

#### Scenario: The reason is on the page
- **WHEN** the History page lists a change that cannot be undone
- **THEN** its row says "cannot undo: " followed by the reason as text that a keyboard or screen-reader user reaches, not only in a tooltip
- **AND** how a screen reader reads it is checked by hand (hand check M19)
Pinned by: `tests/unit/test_ui_followup_r22.py::test_history_says_why_a_change_cannot_be_undone_and_refusals_are_alerts`, `tests/unit/test_ui_followup_r23.py::test_a_danger_toast_is_an_alert_that_stays`, `tests/unit/test_ui_followup_r30.py::test_the_history_table_says_why_a_change_cannot_be_undone`, `tests/unit/test_ui_followup_r30.py::test_history_writes_why_a_change_cannot_be_undone_in_its_own_row`

### Requirement: Undo is itself a change, and undoing it redoes
The system SHALL restore the before state of every touched row and file in one transaction, record the undo as a change of kind `undo` summarised "undo: <summary>", mark the original as undone by it, and treat undoing an `undo` change as a redo summarised "redo: <summary>".
Surfaces: CLI `wider history undo`; UI undo message ("Undone: ..." with Redo, "Redone: ..." with Undo), History page.

#### Scenario: Undo and redo of a label
- **WHEN** a label change is undone and then the resulting undo change is undone
- **THEN** the first produces a change summarised "undo: ana labelled record ..."
- **AND** the second puts the label back with a summary starting "redo: ana labelled record"
Pinned by: `tests/unit/test_history.py::test_label_undo_redo_and_changed_since`

### Requirement: Undo the latest change
Without a change id, the system SHALL undo the current reviewer's newest change that is neither an undo or redo nor already undone, so a repeated undo-last goes further back. A later edit that blocks it SHALL stop the undo and be named. A request without a name SHALL be refused; "nothing to undo" (HTTP 409, CLI exit 1) SHALL be reported when none is left.
Surfaces: CLI `wider history undo [--author]` (no id); UI Ctrl/Cmd+Z, History page "Undo my last change" (`POST /api/history/undo-last`).

#### Scenario: Undo the last change of one reviewer
- **WHEN** "ana" and then "ben" label records and undo-last is called for "ben"
- **THEN** the undone change is ben's label and ben has no labels left
Pinned by: `tests/unit/test_history.py::test_history_routes_blind_and_undo_last`

#### Scenario: Undo the last change on the CLI
- **WHEN** a label is saved and `wider history undo` is run without an id
- **THEN** it prints "undo: ana labelled record ..." and exits 0
Pinned by: `tests/unit/test_history.py::test_history_cli`

#### Scenario: Repeated undo-last goes further back
- **WHEN** "ana" labels record A, then labels record B, and undo-last is called for "ana" three times
- **THEN** the first call undoes the label of B and the second undoes the label of A, so ana has no labels left
- **AND** no call redoes an earlier undo, and the third call returns 409 with "nothing to undo"
Pinned by: `tests/unit/test_history.py::test_undo_last_goes_back_through_one_reviewers_changes`

#### Scenario: Repeated undo on the CLI
- **WHEN** two settings changes are made and `wider history undo` is run twice without an id
- **THEN** the first run undoes the newer change and the second undoes the older one, so both settings have their original values
Pinned by: `tests/unit/test_history.py::test_repeated_cli_undo_restores_two_settings`, `tests/unit/test_history.py::test_undo_last_cli_and_a_blocking_change`

#### Scenario: The CLI undoes only the current reviewer's change
- **WHEN** "ana" labels a record, then "ben" labels one, and `wider history undo --author ana` runs
- **THEN** ana's label is undone and ben's label stays
- **AND** without `--author` the CLI uses `$WIDER_REVIEWER` as the reviewer, never `$USER`: with only `$USER` set, `wider history undo` and `wider history redo` exit 1 with "whose change? give --author or set $WIDER_REVIEWER" and nothing changes
Pinned by: `tests/unit/test_history.py::test_cli_undo_takes_only_the_named_reviewers_change`

#### Scenario: Blocked by a later edit
- **WHEN** "ana" changes a setting and "ben" then changes the same setting, and undo-last is called for "ana"
- **THEN** nothing is undone and the answer names ben's change as the one blocking it
Pinned by: `tests/unit/test_history.py::test_undo_last_cli_and_a_blocking_change`

#### Scenario: Undo-last needs a reviewer name
- **WHEN** `POST /api/history/undo-last` is called without an author or with an empty one
- **THEN** it returns 400 and no change is undone
Pinned by: `tests/unit/test_history.py::test_undo_last_goes_back_through_one_reviewers_changes`

#### Scenario: The UI asks for the name first
- **WHEN** no reviewer name is remembered in the browser and the reviewer presses Ctrl/Cmd+Z or "Undo my last change"
- **THEN** the UI sends no undo request and says "enter your name first: use Change beside “Working as” in the top bar" and "Nothing was undone", with a "Set your name" button that opens the name dialog
- **AND** History has no name field of its own, and no browser prompt is used
Pinned by: `tests/unit/test_ui_followup_r10.py::test_undo_last_without_a_name_points_at_change`, `tests/unit/test_ui_followup_r11.py::test_undo_last_without_a_name_sends_nothing`, `tests/unit/test_ui_followup_r11.py::test_undo_last_sends_the_stored_name`, `tests/unit/test_ui_followup_r17.py::test_ctrl_or_cmd_z_undoes_unless_a_field_has_the_focus`

#### Scenario: Ctrl/Cmd+Z without a name on another page
- **WHEN** no reviewer name is remembered and the reviewer presses Ctrl/Cmd+Z on a page other than History
- **THEN** no undo request is sent, the message says to set the name with Change in the top bar and "Nothing was undone", and the focus stays where it was
Pinned by: `tests/unit/test_ui_followup_r13.py::test_undo_last_without_a_name_off_the_history_page_only_says_so`

#### Scenario: Redo stays available by change id
- **WHEN** a change was undone with undo-last
- **THEN** the undo message still offers Redo, which undoes that undo change by its id
Pinned by: `tests/unit/test_ui_followup_r11.py::test_redo_after_undo_last`

#### Scenario: Undo-last after Redo
- **WHEN** ana's label was undone with undo-last and put back with Redo
- **THEN** History shows the label as in effect and offers Undo, and undo-last for ana undoes it again
- **AND** one more undo-last for ana says there is nothing to undo
Pinned by: `tests/unit/test_ui_followup_r13.py::test_undo_last_after_redo_undoes_the_change_again`

#### Scenario: A cleared name is forgotten on every page
- **WHEN** WIDER works as "ana" and the History page opens
- **THEN** "Undo my last change" acts for "ana", and History shows no name field
Pinned by: `tests/unit/test_ui_followup_r19.py::test_history_undoes_as_the_name_wider_works_as`

#### Scenario: The newest change is a freeze
- **WHEN** a reviewer's newest change is a freeze, which cannot be undone, and an older change of theirs is a note
- **THEN** undo-last skips the freeze and undoes the note
- **AND** the frozen artifact stays frozen
Pinned by: `tests/unit/test_ui_followup_r30.py::test_undo_last_skips_a_freeze_and_undoes_the_reviewer_s_older_change`

### Requirement: Undoing an answer reverses all its effects
The system SHALL reopen the question and restore every row an undone answer changed; for a researcher read in a screening run it SHALL append a retraction line to `responses.jsonl` so a later replay leaves the read out, and for a seed DOI it SHALL restore `benchmark.csv` and resynchronise the store.
Surfaces: CLI `wider history undo`; UI Undo offered after an answer on the Questions page, History page.

#### Scenario: Undoing a researcher read survives a replay
- **WHEN** a failed screening record is answered include, the answer is undone and the run is replayed
- **THEN** the answer change touched `questions`, `screen_calls` and `runs`, the read is no longer successful and the question is open again
- **AND** the log contains `"human_retracted": true` and after the replay the read is still not successful
Pinned by: `tests/unit/test_history.py::test_answers_undo_including_screening_log_and_replay`

#### Scenario: Undoing a seed DOI answer
- **WHEN** a seed paper's DOI is corrected through its question and the change is undone
- **THEN** `benchmark.csv` and the stored seed paper hold the old DOI again
Pinned by: `tests/unit/test_history.py::test_seed_answer_undo_restores_benchmark_csv`

### Requirement: Artifact versions are restored as new versions
The system SHALL NOT overwrite or delete a saved artifact version; restoring an older version SHALL save its content as a new draft with origin `restore`, and restoring the newest version SHALL be refused ("already the newest version", HTTP 409).
Surfaces: CLI `wider construct restore <kind> <version>`; UI Construct workbench, restore button (`POST /api/construct/{kind}/{version}/restore`).

#### Scenario: Restore version 1 of two
- **WHEN** versions 1 and 2 of the anchors exist and version 1 is restored
- **THEN** version 3 is created with the content of version 1, and restoring version 3 is refused
Pinned by: `tests/unit/test_history.py::test_adjudication_dedup_artifact_and_annotations`

#### Scenario: Restore from the CLI
- **WHEN** `wider construct restore anchors 1` is run with two versions saved
- **THEN** it prints "restored as v3"
Pinned by: `tests/unit/test_history.py::test_history_cli`

### Requirement: History page, Undo offers and keyboard undo
The system SHALL provide a History page with an Undo button or the reason undo is impossible per change, SHALL offer Undo after a label, adjudication, duplicate decision, note, tag or answer, and SHALL undo the reviewer's last change on Ctrl/Cmd+Z outside text fields, selects and editable areas, then refresh the pages.
Surfaces: UI History page, undo messages, Ctrl/Cmd+Z; CLI equivalents `wider history list`, `wider history undo`.

#### Scenario: The page, the restore button and the shortcut are present
- **WHEN** the UI shell and the history script are loaded
- **THEN** the shell contains the History page, its table, the sidebar entry and the restore button
- **AND** the script calls undo-last, defines `offerUndo` and handles the Z key
Pinned by: `tests/unit/test_history.py::test_static_history`

### Requirement: Dismissed questions are journalled
The system SHALL record dismissing a question as a change of kind `dismiss` by the dismissing reviewer, touching only that question, and undoing it SHALL reopen the question with no answerer and no answer time. A refused dismissal (unknown question, or one already answered or dismissed) SHALL NOT be recorded.
Surfaces: CLI `wider questions dismiss <id>`, `wider history list --kind dismiss`, `wider history undo`; UI Questions page, History page.

#### Scenario: Dismissal appears in the history
- **WHEN** reviewer "ana" dismisses an open question
- **THEN** the history starts with a change of kind `dismiss` by "ana" that touches `questions` and can be undone

#### Scenario: Undoing a dismissal reopens the question
- **WHEN** a dismissed question's change is undone
- **THEN** the question is open again, with no answerer and no answer time
- **AND** it can then be answered or dismissed again

#### Scenario: A refused dismissal records nothing
- **WHEN** a question that is already answered is dismissed
- **THEN** the dismissal is refused and no new change is recorded

### Requirement: Changes without the reviewer's name are undone by id
The system SHALL keep the name a change was journalled with, including an older login name. Undo-last SHALL undo only changes signed with the given name and is refused without one. By id, an unsigned change or one signed "ui" is undone without asking; one signed with another name, an older login too, asks first and is refused if the undoer gives no name.
Surfaces: CLI `wider history undo <change_id> [--author NAME] [--other]`; UI History page, Undo (`POST /api/history/{id}/undo`).

#### Scenario: Unsigned and older signed changes
- **WHEN** a pair is decided from the page with no name, and a tag is kept with an older login name
- **THEN** undo-last without a name answers 400 "undo-last needs the reviewer's name", and undo-last for "tester" answers 409 "nothing to undo by tester"
- **AND** the older change keeps its login name; the unsigned pair decision is undone by its id with no name given
- **AND** undoing the tag by id with no name is refused with 400 "give your name to undo old-login's change", and as "ana" with `--other` it is undone and the undo is signed "ana"
- **AND** a change an older version signed "ui" keeps that name (history is not rewritten) and is undone by its id without asking
Pinned by: `tests/unit/test_history.py::test_unsigned_and_older_signed_changes_are_undone_by_id`

#### Scenario: Undo-last on the command line with no name known
- **WHEN** `wider history undo` is run with no `--author` (or a blank one), `$WIDER_REVIEWER` unset and `$USER` empty
- **THEN** it exits 1 with "whose change? give --author or set $WIDER_REVIEWER"
- **AND** nothing is undone and no change is journalled
Pinned by: `tests/unit/test_ui_followup_r32.py::test_cli_undo_last_with_no_name_known_is_refused`

### Requirement: Step notebook
The system SHALL give a step a notebook: every change to the step, newest first, dated and signed, from the journal and runs. A run is signed "WIDER" and can show its result; a change is signed by its author ("You" for the current name) and offers the journal's undo; one undone now says "(undone)". It stores nothing. In A1 only Deduplication has one.
Surfaces: CLI `wider notebook dedup [--json]`, `wider history undo ID`; UI top-bar "Notebook"; `GET /api/notebook/dedup`.

#### Scenario: Runs and decisions in one list
- **WHEN** deduplication ran twice and "ana" decided two pairs between the runs, one of them later undone
- **THEN** `wider notebook dedup` lists five entries newest first: the undo, the second run signed "WIDER", the two decisions signed "ana" (the undone one says "(undone)") and the first run signed "WIDER"
- **AND** the page's Notebook button shows the count 5, and pressing it opens the same five entries with `aria-expanded` true and the focus in the panel
- **AND** after Undo, Show this result or Back to the latest result the focus stays in the panel, and Esc inside it closes it
Pinned by: `tests/unit/test_notebook.py::test_runs_and_decisions_in_one_list`, `tests/unit/test_dedup_page_node.py::test_the_notebook_opens_undoes_redoes_and_shows_an_earlier_result`

#### Scenario: Closed by default
- **WHEN** the Deduplication page opens
- **THEN** the notebook is closed and its button has `aria-expanded` false
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_notebook_opens_undoes_redoes_and_shows_an_earlier_result`

#### Scenario: Undo and redo
- **WHEN** "ana" undoes one of ana's decisions from the notebook and then undoes that undo
- **THEN** the decision's entry first says "(undone)" and, after the redo, no longer does
- **AND** `wider notebook dedup` shows the same marks
Pinned by: `tests/unit/test_notebook.py::test_undo_then_redo_leaves_the_decision_in_effect`, `tests/unit/test_dedup_page_node.py::test_the_notebook_opens_undoes_redoes_and_shows_an_earlier_result`

#### Scenario: Undo after a plain rerun
- **WHEN** "ana" decided a pair, deduplication ran again with no new import, and ana undoes the decision from the notebook
- **THEN** the undo goes through, as `wider history undo` with that change id does, and the entry says "(undone)"

Pinned by: `tests/unit/test_notebook.py::test_undo_after_a_plain_rerun`

#### Scenario: Undo refused after the pair changed
- **WHEN** a decision is undone from the notebook after a later run, following a new import, rewrote that pair's stored row
- **THEN** the notebook shows the journal's refusal in words, the same as `wider history undo` with that change id, and nothing changes

Pinned by: `tests/unit/test_notebook.py::test_undo_refused_after_the_pair_changed`

#### Scenario: An earlier run's result
- **WHEN** the researcher presses "Show this result" on the first run's entry
- **THEN** the entry shows that run's headline, metrics and files written, with "Back to the latest result"
Pinned by: `tests/unit/test_notebook.py::test_a_run_entry_carries_its_stored_result`, `tests/unit/test_dedup_page_node.py::test_the_notebook_opens_undoes_redoes_and_shows_an_earlier_result`

### Requirement: A pair's own history
The system SHALL show under a pair, and print with `--pair`, that raw pair's own entries: what the last run did with it, in words ("Left for you to decide: same title, but the journal differs", "Removed: same DOI"), and each decision and undo on it, dated and signed. Other raw pairs, even of the same two clusters, are left to the full notebook.
Surfaces: CLI `wider notebook dedup --pair A B`; UI "This pair's history" under the pair view, link "Full step notebook".

#### Scenario: History of one pair
- **WHEN** a pair queued for the same title was decided "duplicate" by "ana" and another pair was decided by "ben"
- **THEN** `wider notebook dedup --pair` with the first pair's raw ids prints the run's entry "Left for you to decide: …" signed "WIDER" and ana's decision, and nothing of ben's
- **AND** the pair view of that pair shows the same two entries under "This pair's history", the WIDER one marked "WIDER" and ana's marked "You" when the current name is "ana"
Pinned by: `tests/unit/test_notebook.py::test_pair_history_in_either_order_and_without_other_pairs`, `tests/unit/test_dedup_page_node.py::test_the_pair_history_under_the_pair_view_marks_wider_and_you`

#### Scenario: The order of the raw ids does not matter
- **WHEN** `wider notebook dedup --pair 7 3` is run for the pair decided as raw records 3 and 7
- **THEN** it prints the same entries as `--pair 3 7`

Pinned by: `tests/unit/test_notebook.py::test_pair_history_in_either_order_and_without_other_pairs`

### Requirement: Refinement changes are journalled; an unlock is final
The system SHALL journal every unlock (`artifact_unlock`, not undoable: "an unlock is final") and, undoable, the kinds `source_included`, `cluster_mark`, `seed_indexing`, `benchmark_source`, `benchmark_keywords`, `round_count`, `round_accept` and `platform_limits`. Undoing the uploads-final mark after the set was shown records an unlock, after the researcher chooses whether the papers count as seen.
Surfaces: CLI `wider history list|undo`; UI History page, "Show" select.

#### Scenario: Unlock cannot be undone, a cluster mark can
- **WHEN** a query is unlocked, a cluster is marked noise and a round is accepted, and all three are undone
- **THEN** the unlock is refused with HTTP 409 and the reason "an unlock is final", the cluster mark is reversed and the earlier accepted round is accepted again
- **AND** the History page's "Show" select lists "query unlocked", "uploads reopened", "cluster marked" and "round accepted"
Pinned by: `tests/unit/test_history.py::test_unlock_is_final_and_refinement_changes_undo`

#### Scenario: Undoing the final mark after the set was shown
- **WHEN** the uploads were marked final with `validation_after_freeze` on, no synonyms version is frozen, Validation papers B002 and B003 were shown, and the mark is undone on the History page or with `wider history undo ID`
- **THEN** the page's undo confirm shows "The uploads were marked final on <date>; undoing the mark reopens them; 2 validation papers have been shown." with the choice text, "Count them as seen", "Keep them as validation" and "Cancel" (focus on "Cancel", which undoes nothing), and `GET /api/history` gives the entry `undo_choice` true with that `undo_notice`; `wider history undo ID` without a flag prints the same notice and "Choose one: --count-seen or --keep-validation. Nothing was done." and exits 2, and the mark stays
- **AND** with `--count-seen` the undo goes through, the list of unlocks gains one entry with cause `uploads` whose `seen` lists B002 and B003, with `counts_as_seen` 1 and `seen_choice` `count`, the page and the CLI show "Counted as seen: the 2 validation papers shown so far no longer validate the search.", and after a later freeze B002 and B003 are listed under "Validation, used"
- **AND** with `--keep-validation` the entry has `counts_as_seen` 0 and `seen_choice` `keep`, and after a later freeze B002 and B003 are reported in the validation set
- **AND** undoing a mark made while the Validation set was empty asks nothing and records nothing
- **AND** History lists the `artifact_unlock` change "uploads reopened: final mark undone (counted as seen)" or "(kept as validation)" (not undoable), and the package's `unlock.json` lists the entry with cause `uploads`
Pinned by: `tests/unit/test_history.py::test_undo_of_final_mark_records_what_was_seen`

#### Scenario: Undoing the final mark under a frozen query asks
- **WHEN** synonyms v3 is the frozen query, the uploads were marked final, Validation papers B002 and B003 were shown, and the mark is undone on the History page or with `wider history undo ID`
- **THEN** the page's undo confirm shows "The uploads were marked final on <date> and the frozen query v3 stands; undoing the mark reopens them; 2 validation papers have been shown. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." with the buttons "Count them as seen" and "Keep them as validation" (focus on "Cancel", which undoes nothing), and `GET /api/history` gives the entry `undo_choice` true with that `undo_notice`
- **AND** `wider history undo ID` without a flag prints the same notice and "Choose one: --count-seen or --keep-validation. Nothing was done." and exits 2, and `POST /api/history/{change_id}/undo` without `seen_choice` answers 400; the mark stays
- **AND** with `--count-seen` the undo goes through, the list of unlocks gains an entry with cause `uploads` and `counts_as_seen` 1, History lists "uploads reopened: final mark undone (counted as seen)", and B002 and B003 are listed under "Validation, used"
- **AND** with `--keep-validation` the entry has `counts_as_seen` 0, History lists "uploads reopened: final mark undone (kept as validation)", coverage still reports B002 and B003 as Validation, and the note in every output adds "Uploads reopened on <date> while v3 was the frozen query (2 validation papers shown); the researcher chose to keep them as validation."
Pinned by: `tests/unit/test_history.py::test_undo_of_final_mark_under_frozen_query_asks`

#### Scenario: Undo last when it is the final mark after papers were shown
- **WHEN** the reviewer's newest change is the uploads-final mark, Validation papers B002 and B003 were shown, and "Undo last" is pressed on the History page or `wider history undo` is run without an id
- **THEN** the page shows the same notice and buttons as the undo by id (focus on "Cancel", which undoes nothing) and `POST /api/history/undo-last` without `seen_choice` answers 400 with that notice; `wider history undo` without a flag prints the notice and "Choose one: --count-seen or --keep-validation. Nothing was done." and exits 2; the mark stays
- **AND** with `--count-seen` or `--keep-validation` (or the matching button) the mark is undone and the unlock entry carries that `seen_choice`, as in the undo by id
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_undo_last_of_final_mark_asks`

### Requirement: Admissions and stream labels are journalled
The system SHALL journal the admission of a name into a block (`name_admit`) and the labelling of a venue as a stream (`stream_label`), both undoable under the usual rules; answers to refinement questions use the existing `answer` kind. The History page's "Show" select lists both kinds.
Surfaces: CLI `wider history list --kind name_admit|stream_label`, `wider history undo`; UI History page.

#### Scenario: An admission and a label are undone
- **WHEN** a name is admitted with a note and a venue is labelled a stream, and both are undone
- **THEN** the version written by the admission is no longer listed by `wider construct list` (its file stays) and the label is gone, and the "Show" select lists "name admitted" and "stream labelled"
Pinned by: `tests/unit/test_history.py::test_admissions_and_stream_labels_undo`

### Requirement: Seed-paper set changes in History
The system SHALL journal a seed paper's change of set as `benchmark_set` (undoable), and as final a move of shown validation papers to Development (`validation_moved`) and the papers recorded as used by a revision after a kept look (`validation_used`). Undoing a set change that would move a shown validation paper to Development is refused. The History "Show" select lists all three.
Surfaces: CLI `wider history list [--kind]|undo`; UI History page, "Show" select.

#### Scenario: Set changes listed and undone
- **WHEN** B002 and B003 were shown by a kept look, B004 (never shown) is moved from Validation to Development, and that change is undone
- **THEN** B004 is back in Validation (`benchmark.csv` restored) and History lists "seed paper set changed" with its undo
- **AND** after v4 is saved after the look and B002 is then moved with confirmation, History lists "validation papers recorded as used" and "validation paper moved to Development", `wider history list --kind` and the "Show" select offer the three kinds, and undoing either is refused on the page (409) and with `wider history undo ID` (exit 1), with nothing changed
Pinned by: `tests/unit/test_validation_reuse.py::test_set_changes_in_history`

#### Scenario: Undoing a set change of a paper shown since
- **WHEN** B005 is moved from Development to Validation (an undoable change), and v3 is then frozen, so the validation set holding B005 is shown
- **THEN** undoing that change is refused with "B005 has been shown as a validation paper since; move it with wider benchmark set B005 Development --confirm" (409, exit 1), and B005 stays in Validation
- **AND** when the move of B005 to Development was undone before the freeze, a redo of it after the freeze is refused the same way
Pinned by: `tests/unit/test_validation_reuse.py::test_undo_of_set_change_after_shown_is_refused`

### Requirement: Undoing another person's change
The system SHALL ask before undoing a change signed with another name than the undoer's, naming its author, and SHALL record the undo under the undoer's name. Labels and adjudications follow the same rule. Unconfirmed, the CLI and the route refuse and change nothing; confirmed, the undo goes ahead (inform, not control).
Surfaces: CLI `wider history undo ID [--author NAME] [--other]`; UI History page, Undo (in-page dialog naming the author).

#### Scenario: Undoing a colleague's setting change
- **WHEN** "bo" changed a setting and "ana" undoes that change by id without `--other`
- **THEN** the CLI exits 1 with "change <id> was made by bo; add --other to undo it as ana", the route answers 409 with the same author, and nothing changes
- **AND** with `--other` (CLI) or the dialog's "Undo bo's change" (page) the setting is restored and History shows the undo by "ana"
Pinned by: `tests/unit/test_history.py::test_undo_of_another_persons_change_asks_first`, `tests/unit/test_history_js_node.py::test_undo_dialog_names_the_author`

#### Scenario: Another reviewer's label
- **WHEN** "ana" labelled record 4 and "bo" undoes that change, first without and then with `--other`
- **THEN** the first is refused (exit 1, HTTP 409) and the label stays; the second removes ana's label and History shows the undo by "bo"
Pinned by: `tests/unit/test_history.py::test_another_reviewers_label_is_undone_after_asking`

### Requirement: Undo-last names a near match
The system SHALL, when undo-last finds nothing under the given name but changes exist under a name that differs only in case or spacing, say so ("nothing to undo by Ana; there are changes by 'ana'") and undo nothing; names are not merged. The route SHALL take the page's author header when no `author` is given.
Surfaces: CLI `wider history undo [--author NAME]`; UI History page "Undo my last change", Ctrl/Cmd+Z (`POST /api/history/undo-last`).

#### Scenario: Ana and ana
- **WHEN** "ana" labelled a record and undo-last is called for "Ana"
- **THEN** it answers "nothing to undo by Ana; there are changes by 'ana'" (CLI exit 1, route 409) and the label stays
- **AND** the History page shows the same sentence in normal black text
- **AND** `POST /api/history/undo-last` with header `X-Wider-Author: ana` and no `author` undoes ana's label
Pinned by: `tests/unit/test_history.py::test_undo_last_names_a_near_match_and_reads_the_header`, `tests/unit/test_history_js_node.py::test_near_match_sentence_is_shown`

### Requirement: The History "Show" list holds every kind
The system SHALL offer in the History page's "Show" list one option per kind the journal writes, labelled as in History, built from the same list of kinds the engine uses, plus the list's existing grouped choices such as "uploads reopened". The CLI SHALL refuse an unknown `--kind`, naming the known kinds.
Surfaces: UI History page, "Show" list; CLI `wider history list --kind KIND`.

#### Scenario: Every kind in the list
- **WHEN** the History page loads
- **THEN** its "Show" list has one option per kind of the engine's list, including "recall seen on a validation sample", "sample locked", "sample unlocked" and "review decision", and still offers "uploads reopened"
- **AND** `wider history list --kind nosuch` exits 1 naming the known kinds
Pinned by: `tests/unit/test_history_js_node.py::test_show_list_holds_every_kind`, `tests/unit/test_history.py::test_history_list_refuses_an_unknown_kind`

### Requirement: Undo edge cases are stated
The system SHALL refuse an unknown change id with "no change <id>" (CLI exit 1, HTTP 404). A settings undo after `project.yaml` was edited by hand SHALL be refused with "project.yaml changed since; undo the later change first" and keep the hand edit. An undo then redo of a first reviewer's label SHALL keep that label first.
Surfaces: CLI `wider history undo ID`; UI History page, Undo.

#### Scenario: Unknown id and hand-edited settings
- **WHEN** `wider history undo 999999` runs, and a title change is undone after `project.yaml` was edited by hand to another title
- **THEN** the first exits 1 with "no change 999999" (route 404); the second is refused with "project.yaml changed since; undo the later change first", and the file keeps the hand edit
Pinned by: `tests/unit/test_history.py::test_unknown_change_id_and_a_hand_edited_setting`

#### Scenario: Undo and redo of a first label
- **WHEN** "ana" then "bo" label record 4 differently, and ana's label is undone and redone
- **THEN** the final labels still name ana's label as the first reviewer's
Pinned by: `tests/unit/test_history.py::test_undo_redo_keeps_the_first_label`

### Requirement: Redo the last undone change
The system SHALL redo the reviewer's newest "undo:" change (never a "redo:") that is not undone now and is newer than their newest ordinary change, so repeated redos walk back and a new change ends them. A blocking later change is named in the refusal; with none left it says "nothing to redo by <name>"; without a name it is refused. Undo-last skips undo and redo changes.
Surfaces: CLI `wider history redo [--author NAME]`; UI Ctrl/Cmd+Shift+Z outside text fields (`POST /api/history/redo-last`).

#### Scenario: Two undos and two redos
- **WHEN** "ana" labels records 4 and 5 in a synthetic sample, presses Ctrl+Z twice on the Labelling queue and then Ctrl+Shift+Z twice
- **THEN** both labels are back, the first redo put back the label of record 4 (the last undone) and the second that of record 5, and History holds two "redo:" changes by "ana"
- **AND** a third Ctrl+Shift+Z or `wider history redo --author ana` undoes nothing and says "nothing to redo by ana" (CLI exit 1, route 409, an error toast on the page)
Pinned by: `tests/unit/test_history.py::test_redo_last_walks_back_through_undos`, `tests/unit/test_a11y_node.py::test_redo_by_key`

#### Scenario: A new change ends the redos
- **WHEN** "ana" labels record 4, undoes it with Ctrl+Z, then labels record 6 and presses Ctrl+Shift+Z
- **THEN** it says "nothing to redo by ana", record 4 stays without a label and record 6 keeps its label
Pinned by: `tests/unit/test_history.py::test_redo_stops_after_a_new_change`

#### Scenario: Redo without a name
- **WHEN** `wider history redo` runs with no `--author`, `$WIDER_REVIEWER` unset and `$USER` empty, the route is called with no `author` and no author header, and Ctrl+Shift+Z is pressed with no name set
- **THEN** the CLI exits 1 with "whose change? give --author or set $WIDER_REVIEWER", the route answers 400 "redo-last needs the reviewer's name", and nothing is journalled
- **AND** the page shows the error toast "Redo needs your name: set it with Change in the top bar. Nothing was redone." and sends no request
Pinned by: `tests/unit/test_history.py::test_redo_last_needs_a_name`, `tests/unit/test_a11y_node.py::test_redo_by_key`

#### Scenario: A redo blocked by a later change
- **WHEN** "ana" changed the project title, undid that change, "bo" then changed the title, and ana presses Ctrl+Shift+Z
- **THEN** the redo is refused with a message containing "undo the later change first" and naming bo's change (CLI exit 1, route 409), and the title stays bo's
Pinned by: `tests/unit/test_history.py::test_redo_last_refused_after_a_later_change`

### Requirement: Corrections are journalled
The system SHALL journal each correction as one change of kind `label_correction` ("label correction" in History and its "Show" list) touching the label or adjudication row and its `label_corrections` row. Its summary names the author and the record as the save it replaces does, and never the decision or the reason.
Surfaces: CLI `wider history list --kind label_correction`; UI History page.

#### Scenario: A correction in History
- **WHEN** R1 corrects record 1 of the locked `dev` with a reason
- **THEN** `wider history list --kind label_correction` and the History page list one change by R1 summarised "R1 corrected record 1 in sample dev", whose summary holds neither decision nor reason, and the "Show" list offers "label correction"
- **AND** the same correction made from the blind queue is summarised "R1 corrected a label" and the change names no record
Pinned by: `tests/unit/test_label_completion.py::test_correction_in_history_and_show_list`

### Requirement: Blind History leaves out a labelled record's sample
The system SHALL write "in a sample" for the sample of a labelled, corrected or adjudicated record in History entries, their cannot-undo reasons and Undo or Redo messages while Blind is on (the default). Whole-sample entries (draw, lock, extend) keep the name; stored entries keep their words. History's Blind box is Sampling's switch.
Surfaces: CLI `wider history list|undo|redo --blind`; UI History page, Undo, record drawer; API `hide_samples`, implied by `blind`.

#### Scenario: A blind reader sees no sample names
- **WHEN** ana labels record 1 in development, ben labels record 2 in validation, and History is listed with Blind on
- **THEN** the entries read "ana labelled record 1 in a sample" and "ben labelled record 2 in a sample", and `wider history list --blind` prints neither sample name
- **AND** after validation is locked and ben corrects record 2, the correction reads "in a sample", the earlier label's reason reads "a sample was locked after this change", and the Undo answer of the page and of `wider history undo --blind` names no sample
- **AND** without Blind the entries still name the sample
Pinned by: `tests/unit/test_history.py::test_blind_history_hides_the_sample_of_a_labelled_record`, `tests/unit/test_history_js_node.py::test_history_blind_box_hides_sample_names`, `tests/unit/test_history_js_node.py::test_history_blind_is_on_by_default`
