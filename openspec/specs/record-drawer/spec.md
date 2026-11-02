# record-drawer Specification

## Purpose
The record drawer is one right-hand panel for record-by-record work in the UI. It is used for manual duplicate pairs in the `dedup` stage, blind labelling and adjudication of samples in the `sample` stage, and development evaluation and run inspection in the `screen` stage. It shows one record at a time, with keyboard navigation and decisions. Reviewers attach attributed notes and tags to unique records. These are searchable, shown blind during labelling, and also available on the command line through `wider notes`.

## Requirements

### Requirement: Drawer opened from record tables
The system SHALL open the record drawer when a row of a record table is clicked or a focused row gets Enter. The drawer shows the table's label, the position "i / n" and previous and next buttons, and moves the focus to the record title. On close, focus returns to the row of the record last shown. Deduplication docks it.
Surfaces: UI Sample page (Labelling queue, Second reviewer and adjudication), Screen page (Development evaluation, Inspect a run), Notes and tags page; CLI none.

#### Scenario: Drawer wiring on the page
- **WHEN** the UI page is loaded
- **THEN** it contains the drawer with its decision bar, notes area and close button, the tables `lb-table` and `an-table`, the Deduplication list `dd-list` with its pair view `dd-pair`, the search fields `lb-search` and `in-search`, and the filter `ev-filter`
- **AND** `an-table` sits on the Notes and tags page (`#notes`), not on the home page
- **AND** `drawer.js` loads before `records.js` and `dedup.js`, which load before `shell.js`
Pinned by: `tests/unit/test_records.py::test_static_drawer`

### Requirement: Keyboard control
The system SHALL handle these keys while the drawer is open and no modifier key is held: `j`/`k` next/previous record, `1`/`2`/`3` the three decisions, `n`/`t` focus the note/tag field, `Esc` close (docked: to the list). In a field only `Esc` is handled; it leaves the field. After a move, focus outside the drawer goes to the new title. A decision key pressed before the drawer moved on decides nothing. Without notes, the key hint leaves out `n` and `t`.
Surfaces: UI record drawer; CLI none.

#### Scenario: Key handlers present
- **WHEN** the drawer script is loaded
- **THEN** it handles `j`, `k`, `n`, `t` and `Escape`
- **AND** `Esc` in the note field leaves the field for the drawer, which stays open, and a second `Esc` closes the drawer
Pinned by: `tests/unit/test_records.py::test_static_drawer`, `tests/unit/test_ui_followup_r32.py::test_a_second_esc_closes_the_drawer`

#### Scenario: Decide and advance
- **WHEN** a reviewer presses `2` on the third record of the labelling queue
- **THEN** the record is labelled exclude, the drawer shows "saved: exclude", and after 200 ms it moves to the fourth record
Pinned by: `tests/unit/test_drawer_keys_node.py::test_decide_with_a_key_saves_and_advances_after_200_ms`, `tests/unit/test_drawer_keys_node.py::test_labelling_queue_decisions_are_include_exclude_maybe`, `tests/unit/test_ui_followup_r30.py::test_the_labelling_queue_saves_the_key_s_decision_through_its_own_request`

#### Scenario: A second decision key during the pause
- **WHEN** the reviewer presses `1` and then `2` before the drawer moves on
- **THEN** only the first decision is saved, the drawer says "already saved, moving to the next record: nothing more was decided" and moves on once
- **AND** a `j` pressed during the pause, or while the decision is still being saved, cancels the pending move, so the drawer moves one record, not two
- **AND** a second key pressed while the first decision is still being saved gets the same answer, and it stays after the save instead of "saved: ..."
Pinned by: `tests/unit/test_ui_followup_r20.py::test_a_second_decision_key_during_the_pause_decides_nothing`, `tests/unit/test_ui_followup_r20.py::test_j_while_the_decision_is_saving_moves_once`, `tests/unit/test_ui_followup_r22.py::test_a_second_key_during_the_save_keeps_its_answer`

#### Scenario: Typing a note
- **WHEN** the note field has focus and the reviewer types "j"
- **THEN** the letter goes into the note and the drawer does not move
Pinned by: `tests/unit/test_drawer_keys_node.py::test_a_letter_typed_in_the_note_field_never_moves_the_drawer`

#### Scenario: A note is saved once
- **WHEN** the reviewer presses Enter twice in the note field while the first save is on its way
- **THEN** one note is saved and the field is empty
- **AND** a refused save gives the text back to the field
Pinned by: `tests/unit/test_drawer_keys_node.py::test_a_note_is_saved_once_and_the_field_empties`

#### Scenario: Focus stays in the drawer after a move
- **WHEN** the reviewer presses Next on the last-but-one record (Next then turns disabled), or decides with a button that the next record replaces
- **THEN** once the next record is shown, the focus is on its title, never on the page body
- **AND** after `j`, `k` or Next with the focus still on a control of the drawer, the focus stays there
- **AND** while the next record loads the focus is on the drawer, and an `n` or `t` pressed meanwhile focuses the note or tag field once the record is shown
- **AND** a `1` pressed meanwhile saves no decision and says "the next record is still loading: nothing was decided"
Pinned by: `tests/unit/test_drawer_keys_node.py::test_focus_stays_in_the_drawer_after_a_move`, `tests/unit/test_drawer_keys_node.py::test_keys_pressed_while_the_next_record_loads_are_kept_in_the_drawer`, `tests/unit/test_drawer_keys_node.py::test_t_and_decision_keys_pressed_while_the_next_record_loads`, `tests/unit/test_static_followup.py::test_drawer_keeps_the_focus_after_moving_to_another_record`, `tests/unit/test_ui_followup_r30.py::test_the_strict_page_has_no_drawer_field_until_the_drawer_draws_it`, `tests/unit/test_ui_followup_r30.py::test_n_or_t_pressed_while_the_next_record_loads_focuses_the_field_the_drawer_draws`, `tests/unit/test_ui_followup_r30.py::test_a_decision_button_the_next_record_replaces_hands_the_focus_to_the_new_title`, `tests/unit/test_ui_followup_r30.py::test_next_on_the_last_but_one_record_and_k_or_next_on_a_kept_control`

#### Scenario: Duplicate-pair key hint
- **WHEN** a duplicate pair is shown in the docked pair view
- **THEN** its key hint names no `n` note or `t` tag key, and the big decision buttons show the keys 1, 2 and 3
Pinned by: `tests/unit/test_static_followup.py::test_a_long_name_can_remove_its_own_notes_and_the_pair_drawer_hint_has_no_note_keys`, `tests/unit/test_ui_followup_r30.py::test_the_pair_drawer_hides_the_note_keys_and_the_labelling_drawer_shows_them`

### Requirement: Record content without ranking information
The system SHALL show in the drawer the record's title, authors, year, venue, DOI link and abstract, and MUST NOT return its rank, score, band, cell or sample. Model quotes are highlighted in the abstract: each quote is split at "...", and each fragment of at least 8 characters is marked wherever it occurs, ignoring case and whitespace. All other text is escaped.
Surfaces: UI record drawer (`GET /api/records/{id}`); CLI none.

#### Scenario: No ranking fields
- **WHEN** the drawer loads a record
- **THEN** the record has a title and none of the fields rank, score, band, cell or sample
- **AND** an unknown record id returns 404
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`

### Requirement: Attributed notes
The system SHALL store notes in the table `record_notes` with record, author, text and time. A note needs an author (first 80 characters kept), non-empty text of at most 2000 characters, and an existing unique record. Only its author can delete a note. Adding and deleting notes are written to the journal. In the drawer, Enter saves a note and Shift+Enter starts a new line.
Surfaces: CLI `wider notes add RECORD_ID TEXT`, `wider notes remove NOTE_ID`; UI record drawer, Notes and tags panel.

#### Scenario: Rules for notes
- **WHEN** a note is added with only spaces, without an author, or to record 99999, which does not exist
- **THEN** the first two are refused as invalid and the third as an unknown record
- **AND** a note cannot be removed in another author's name, but its author can remove it
Pinned by: `tests/unit/test_records.py::test_notes_tags_blind_and_search`

#### Scenario: Name needed in the drawer
- **WHEN** no reviewer name is known and the reviewer presses Enter in the note field
- **THEN** the drawer shows "enter your name first" and saves nothing

### Requirement: Normalised tags per author
The system SHALL store tags in the table `record_tags`, one row per record, tag and author. A tag is lower-cased with spaces turned into hyphens, and must be 1 to 40 characters of letters or digits of any script, "-", "_" or ".", starting with a letter or digit. The same tag added twice by the same author is kept once. A reviewer removes only their own tags.
Surfaces: CLI `wider notes tag RECORD_ID TAG...`, `wider notes untag RECORD_ID TAG...`; UI record drawer (tag field, × on your own tags).

#### Scenario: Tag normalisation
- **WHEN** a reviewer tags a record "  Edge Case " and then "edge-case"
- **THEN** the record has one tag `edge-case` by that reviewer
- **AND** the tag "no/slashes" is refused
- **AND** "Ünïcødé 漢字" is kept as `ünïcødé-漢字`, while "_lead", an emoji and 41 letters are refused
Pinned by: `tests/unit/test_records.py::test_notes_tags_blind_and_search`

#### Scenario: Tags through the API
- **WHEN** the UI posts the tag "Hard Case" and then "bad/tag"
- **THEN** the first returns `hard-case` and the second returns 400
- **AND** deleting `hard-case` in another reviewer's name returns 404, and `wider notes untag` of another reviewer's tag exits 1 with "no such tag by this author"
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`, `tests/unit/test_ui_followup_r16.py::test_cli_untag_refuses_another_reviewers_tag`

#### Scenario: The refusal names the rule
- **WHEN** a reviewer tags a record "_lead" or ".x"
- **THEN** the refusal says a tag starts with a letter or digit
Pinned by: `tests/unit/test_ui_followup_r10.py::test_tag_message_names_the_first_character_rule`

#### Scenario: A refusal right after a good tag
- **WHEN** the reviewer adds "Hard Case" and, while it is saved, "bad/tag"
- **THEN** after the drawer re-renders the refusal is still shown and "bad/tag" is back in the tag field
Pinned by: `tests/unit/test_drawer_keys_node.py::test_a_tag_refusal_survives_the_re_render_of_an_earlier_tag`

### Requirement: Blind notes and tags in labelling
The system SHALL show only the reader's own notes and tags in blind mode. The labelling queue always opens the drawer in blind mode: its table search and tag chips use only the reviewer's own notes and tags, and the drawer marks the panel "(yours only, blind mode)". A name longer than 80 characters is matched by its first 80, as it is stored.
Surfaces: UI Sample page, Labelling queue (`blind=true` on the record and annotation routes); CLI none (not blind).

#### Scenario: Blind reader sees only their own notes
- **WHEN** two reviewers have each written a note on a record and the first opens it in blind mode
- **THEN** only the first reviewer's note is returned, while the open view returns both
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`

#### Scenario: A long name still sees its own notes
- **WHEN** a reviewer with a 105-character name adds a note and a tag, another reviewer adds a note, and the first opens the record in blind mode
- **THEN** only the first reviewer's note and tag are shown, signed with the first 80 characters of the name
- **AND** both carry the × that removes them, as the reviewer's own
Pinned by: `tests/unit/test_records.py::test_a_long_name_sees_its_own_notes_in_blind_mode`, `tests/unit/test_static_followup.py::test_a_long_name_can_remove_its_own_notes_and_the_pair_drawer_hint_has_no_note_keys`, `tests/unit/test_ui_followup_r30.py::test_a_long_name_s_own_note_and_tag_carry_the_remove_button_and_another_s_does_not`

### Requirement: Searching notes and tags
The system SHALL find the records that have notes or tags by words (all must occur in the title, a note or a tag), by tag and by author, at most 200 by default, each with its notes and tags. It also lists the tags in use with their record counts. The labelling queue and "Inspect a run" tables filter by words in title, notes and tags.
Surfaces: CLI `wider notes list [--search] [--tag] [--author]`; UI Notes and tags page, and the table search fields.

#### Scenario: Search by words, tag and author
- **WHEN** one record has a note containing "upward voice" by one reviewer and another record has the tag `check-doi`
- **THEN** a search for "upward voice" returns the first record, the tag `check-doi` returns the second, and an author filter returns only that author's records
- **AND** the tag list gives each tag with its record count
Pinned by: `tests/unit/test_records.py::test_notes_tags_blind_and_search`

### Requirement: Notes and tags follow deduplication
The system SHALL move the notes and tags of a record that deduplication merges away to the surviving record, keeping a tag once when the same author already put it there. A deduplication re-run that would remove a record still carrying a note or tag MUST be refused, naming `record_notes` or `record_tags`, and nothing changes.
Surfaces: CLI `wider dedup run`; UI Dedup page (run button).

#### Scenario: Merge and refusal
- **WHEN** two records with notes and tags are decided as duplicates and deduplication runs again
- **THEN** the survivor holds the merged record's note and each author's tags once, and the merged record has no tags left
- **AND** a re-run that would drop a record with a note fails, and the note is still there
Pinned by: `tests/unit/test_records.py::test_dedup_merge_moves_notes_and_tags_and_refuses_orphans`

### Requirement: Adjudication side by side
The system SHALL show two reviewers' labels on a sample side by side (decision, reason, note) with any adjudication, listing only disagreements unless `all` is set. In the adjudication drawer, `1`/`2`/`3` record include, exclude or maybe as the final decision. An unknown sample returns 404.
Surfaces: CLI `wider label agreement`, `wider label adjudicate`; UI Sample page, "Second reviewer and adjudication".

#### Scenario: Side-by-side comparison
- **WHEN** two reviewers labelled three records and disagree on two
- **THEN** the comparison lists the two disagreements with each reviewer's decision, reason and note, and `all=true` lists all three
- **AND** after a third person adjudicates one, that row shows the adjudicated decision and who decided
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`

### Requirement: Evaluation disagreements
The system SHALL list the development-evaluation disagreements between the model and the human labels and filter them to all disagreements, `MISS`, `FALSE INCLUDE` or `FALSE MAYBE`. Its drawer shows the human and model decisions, the model's summary and quotes, with the quotes highlighted in the abstract.
Surfaces: CLI `wider screen evaluate`; UI Screen page, "Development evaluation".

#### Scenario: Filter to misses
- **WHEN** the reviewer chooses `MISS` in the evaluation filter
- **THEN** only disagreements in category `MISS` are listed, and opening one shows the human and model decisions side by side

### Requirement: Locked samples read-only in the drawer
The system SHALL keep `locked` on a locked sample's queue rows, none read-only: the drawer saves a missing label or adjudication at once and asks a reason for a change, its blind text naming no sample ("a change to a saved label here needs a reason for the correction"), its unblinded text "sample '<name>' is locked: a change to a saved label needs a reason for the correction".
Surfaces: UI Labelling queue and drawer; CLI `wider label adjudicate`, `wider label import` (`--correction-reason`).

#### Scenario: Locking a labelled sample
- **WHEN** a sample is locked after labelling
- **THEN** every queue row has `locked` true and no band, pool, rank or sample field
- **AND** a new label on that sample is saved, and a change to a stored one asks for a correction reason
Pinned by: `tests/unit/test_records.py::test_queue_marks_locked_samples`

#### Scenario: The drawer of a locked record, blind and unblinded
- **WHEN** samples "dev" and "probe" are both locked and "ana" opens record 7 of "dev" in the drawer, first from the blind queue, then from the unblinded queue
- **THEN** neither drawer says the record is closed; when ana changes her stored label, the blind drawer's dialog text contains neither "dev" nor "sample"
- **AND** the unblinded drawer's text is "sample 'dev' is locked: a change to a saved label needs a reason for the correction", and `wider label adjudicate` on "dev" stores a first adjudication and asks `--correction-reason` for a changed one
Pinned by: `tests/unit/test_drawer_keys_node.py::test_locked_record_drawer_blind_and_unblinded`, `tests/unit/test_records.py::test_queue_marks_locked_samples`

### Requirement: Duplicate pair view
The system SHALL show a manual duplicate pair in the drawer docked in the Deduplication page: the fields of A and B side by side, each with its match word, words in one record only marked pink, in both green, long abstracts folded, the pair's history under it. Decisions (Duplicates, keep A or B; Not duplicates; Unsure) apply on the next run. No notes panel.
Surfaces: CLI `wider dedup queue`, `wider dedup decide`; UI Deduplication pair view.

#### Scenario: Pair carries both abstracts
- **WHEN** the Dedup page loads the manual queue
- **THEN** each pending pair carries `abstract_a` and `abstract_b` for the side-by-side view
Pinned by: `tests/unit/test_records.py::test_record_routes_blind_and_compare`

#### Scenario: Header of the pair view
- **WHEN** the second of 12 pairs is selected and it was queued for the same title
- **THEN** the pair view's header reads "Pair 2 of 12" with "Matched on: Same title", the pair's status chip and Previous and Next buttons
Pinned by: `tests/unit/test_dedup_page_node.py::test_the_pair_view_header_buttons_and_signing_name`

### Requirement: Notes on the command line
The system SHALL provide `wider notes add`, `remove`, `tag`, `untag` and `list` on the same notes and tags the drawer shows. The author comes from `--author`, else `$WIDER_REVIEWER`, else `$USER`. Invalid input or an unknown record prints the reason in red and exits with code 1. `list` prints the tags in use with counts and a table of record, title, tags and notes.
Surfaces: CLI `wider notes add|remove|tag|untag|list`; UI record drawer and Notes and tags page.

#### Scenario: Command-line round trip
- **WHEN**, with `WIDER_REVIEWER` set, a reviewer adds a note, tags the record "Borderline" and "recheck", and lists by tag `recheck`
- **THEN** the output shows "note 1 added", "borderline, recheck", the note text and "recheck (1)"
- **AND** removing note 1 with `--author` set to another name exits with code 1, while removing it as its author exits with code 0
Pinned by: `tests/unit/test_records.py::test_notes_cli`

#### Scenario: Nothing matches
- **WHEN** `wider notes list --search` is given words that occur nowhere
- **THEN** it prints "no notes or tags match"
Pinned by: `tests/unit/test_records.py::test_notes_cli`

### Requirement: Removing a note or tag needs its author
The system SHALL remove a note or tag only for a named author, and only that author's own. An API removal without an author SHALL be refused (HTTP 400) and change nothing; a note or tag the author did not write returns 404 and stays. The CLI author is `--author`, else `$WIDER_REVIEWER`, else `$USER`; with none set the command exits with code 1.
Surfaces: CLI `wider notes remove`, `wider notes untag`; UI record drawer (note delete, tag ×).

#### Scenario: API removal without an author
- **WHEN** "ana" wrote a note on a record, "ana" and "ben" both tagged it `recheck`, and the note and the tag are deleted through the API without an author
- **THEN** both requests return 400
- **AND** the note and both `recheck` tags are still there and no change is recorded

#### Scenario: A reviewer removes only their own tag
- **WHEN** "ben" removes the tag `recheck` from that record with author "ben"
- **THEN** ben's `recheck` tag is gone and ana's `recheck` tag stays

#### Scenario: Another author's note is not removed
- **WHEN** "ben" deletes ana's note with author "ben"
- **THEN** the response is 404 and the note stays

#### Scenario: CLI without any author
- **WHEN** `wider notes remove 1` runs without `--author` and with neither `$WIDER_REVIEWER` nor `$USER` set
- **THEN** it prints that an author is needed and exits with code 1
- **AND** note 1 is still there

### Requirement: Blind mode in note responses and search
The system SHALL apply blind mode to every response carrying notes or tags: a note saved in blind mode returns only the author's own notes and tags, and a blind search matches, returns and counts only the title and the reader's own notes and tags. A blind request without a reviewer name SHALL be refused (HTTP 400).
Surfaces: UI record drawer in the labelling queue and table searches (`blind=true`); CLI none (`wider notes list` is not blind).

#### Scenario: Saving a note in blind mode
- **WHEN** "ana" has a note on a record and "ben" saves a note on the same record in blind mode
- **THEN** the response lists only ben's notes and tags
- **AND** the same save without blind mode lists the notes of both reviewers

#### Scenario: Blind search sees only the reader's own annotations
- **WHEN** "ana" tagged a record `recheck` and wrote the note "unclear design", and "ben" searches blind for the words "unclear design" and for the tag `recheck`
- **THEN** both searches return no records and ben's tag list does not contain `recheck`
- **AND** the same blind searches by "ana" return the record with only ana's note and tag

#### Scenario: Blind search without a name
- **WHEN** a blind annotation search is requested without a reviewer name
- **THEN** it returns 400 and no records

### Requirement: The drawer stays with the page it was opened on
The system SHALL close the record drawer when the researcher goes to another page, without moving the focus back to the left page, and SHALL NOT open a drawer for a table on a hidden page (a queue that finishes loading after its page was left). Keys pressed afterwards save nothing.
Surfaces: UI record drawer on every page that opens it, sidebar and Continue links; CLI none.

#### Scenario: Leaving the page with the drawer open
- **WHEN** the drawer is open on the labelling queue and the researcher opens another page, or a queue finishes loading after its page was left
- **THEN** the drawer is closed (or never opens) and the focus stays on the new page
- **AND** pressing `1` or `j` afterwards sends no decision
Pinned by: `tests/unit/test_drawer_keys_node.py::test_the_drawer_stays_with_the_page_it_was_opened_on`

### Requirement: An id too large for the store names nothing
The system SHALL treat an id too large for the store (beyond a 64-bit integer) as an id that names nothing: HTTP 404 on the page, exit 1 on the CLI, with "no such id: the number is too large to be one", never HTTP 500 or a traceback.
Surfaces: UI record drawer (`GET /api/records/{id}`); CLI every command taking an id (`wider notes`, `wider history undo`, `wider questions`).

#### Scenario: A record id of 23 digits
- **WHEN** the page asks for record 99999999999999999999999, or `wider notes add 99999999999999999999999 x`, `wider history undo 99999999999999999999999` or `wider questions dismiss 99999999999999999999999` is run
- **THEN** the page gets HTTP 404 with "no such id: the number is too large to be one" and the CLI prints that message and exits 1
- **AND** an id that fits but is unknown (999999) is still 404 on the page, and `wider notes add 999999 x` exits 1 with "no unique record 999999"
Pinned by: `tests/unit/test_ui_followup_r16.py::test_a_record_id_too_large_for_the_store_is_not_found`, `tests/unit/test_ui_followup_r17.py::test_an_id_too_large_for_the_store_is_not_found_on_the_command_line`

### Requirement: Drawer docked in a page
The system SHALL let a page show the record drawer docked beside its list instead of over the page. A docked drawer opens on the list's first item, has no close button, and Esc moves the focus to the list. Keys 1, 2, 3, j and k work on that page outside fields; j and k also move the list's selection. Saving, the pause and the name rules are the drawer's.
Surfaces: UI Deduplication page, pair view; CLI not applicable (a layout; `wider dedup decide` decides).

#### Scenario: Keys on the docked pair view
- **WHEN** the Deduplication page shows pairs to decide, the remembered name is "ana" and the focus is on the list
- **THEN** pressing 1 saves "duplicate" for the selected pair signed "ana", says "saved: duplicate", and after the short pause selects the next pair in both the list and the pair view
- **AND** a second 1 pressed during the pause decides nothing
Pinned by: `tests/unit/test_drawer_keys_node.py::test_docked_pair_view_keys_decide_once_and_esc_goes_back_to_the_list`, `tests/unit/test_dedup_page_node.py::test_keys_on_the_docked_view_and_the_selection_after_the_refresh`

#### Scenario: Typing in the search box
- **WHEN** the focus is in the list's search box and the researcher types "1 j"
- **THEN** no decision is sent and the selection does not move
Pinned by: `tests/unit/test_drawer_keys_node.py::test_docked_pair_view_keys_decide_once_and_esc_goes_back_to_the_list`

#### Scenario: Esc on the docked view
- **WHEN** the focus is on a decision button of the docked pair view and the researcher presses Esc
- **THEN** the pair view stays open and the focus moves to the selected row of the list
Pinned by: `tests/unit/test_drawer_keys_node.py::test_docked_pair_view_keys_decide_once_and_esc_goes_back_to_the_list`

#### Scenario: Leaving the page
- **WHEN** the researcher opens another page from the sidebar while the docked pair view is shown
- **THEN** pressing 1 or j on the new page sends no decision and moves nothing
Pinned by: `tests/unit/test_drawer_keys_node.py::test_docked_keys_do_nothing_after_leaving_the_page_and_close_puts_the_drawer_back`

### Requirement: The drawer shows no stale record while loading
The system SHALL clear the previous record's title, abstract, notes, tags and decision buttons as soon as the drawer moves to another record, showing "loading" until the new record arrives, so nothing of the earlier record can be read or acted on as the new one's. A decision key pressed meanwhile keeps the existing answer "the next record is still loading: nothing was decided".
Surfaces: UI record drawer (j/k, row click); CLI none (`wider notes list RECORD` prints one record's notes).

#### Scenario: Slow load of the next record
- **WHEN** record 3 with note "check the design" is open and j is pressed while record 4's answer is delayed
- **THEN** the drawer shows "loading" and neither the note "check the design" nor record 3's title
- **AND** a 1 pressed before record 4 is shown saves nothing and says "the next record is still loading: nothing was decided"
Pinned by: `tests/unit/test_drawer_keys_node.py::test_drawer_clears_the_previous_record_while_loading`

### Requirement: The re-check drawer says its history is not shown
The system SHALL show, in the drawer of a re-check or second-reviewer queue item, "earlier labels are not shown in this queue" where the record's history would be, since those items carry no record id.
Surfaces: UI record drawer in the re-check and second-reviewer queues; CLI none (`wider history list --author NAME` lists one's own changes).

#### Scenario: Re-check item
- **WHEN** "ana" opens a re-check queue item in the drawer
- **THEN** the history part reads "earlier labels are not shown in this queue" and no history request is sent
Pinned by: `tests/unit/test_drawer_keys_node.py::test_recheck_drawer_says_history_is_not_shown`

### Requirement: The drawer signs with the name WIDER works as
The system SHALL sign a drawer change (note, tag, decision, duplicate pair) with the name WIDER works as (D33); a labelling queue's drawer signs with the name the queue was loaded for. The drawer has no name field: without a name nothing is sent, the drawer says "enter your name first: use Change beside “Working as” in the top bar" and offers "Set your name", which opens the name dialog.
Surfaces: UI record drawer, notes, tags and decisions; CLI `--by` / `--author`.

#### Scenario: A note signed with the name WIDER works as
- **WHEN** WIDER works as "ana" and the researcher adds a note in the drawer
- **THEN** the note is saved under "ana" without a name prompt, and the drawer shows no name field
Pinned by: `tests/unit/test_drawer_keys_node.py::test_a_note_is_saved_under_the_name_wider_works_as`

#### Scenario: A note without a name
- **WHEN** no name is set and the researcher types a note and presses Enter
- **THEN** nothing is sent and the drawer says "enter your name first: use Change beside “Working as” in the top bar"
- **AND** its "Set your name" button opens the name dialog; after "ana" is saved there the same note is saved with Enter without retyping it
Pinned by: `tests/unit/test_ui_followup_r17.py::test_a_note_without_a_name_points_at_change`

#### Scenario: A queue signs with its own name
- **WHEN** the labelling queue was loaded for "ana" and a note or tag is added from its drawer
- **THEN** it is signed "ana"
Pinned by: `tests/unit/test_ui_followup_r30.py::test_notes_and_tags_in_the_queue_are_signed_with_the_name_the_queue_was_loaded_for`, `tests/unit/test_ui_followup_r30.py::test_the_drawer_signs_with_the_queue_name_then_the_name_wider_works_as`

### Requirement: Labelling and adjudication on a focus page
The system SHALL open the labelling queue and the adjudication drawer as a focus page filling the window, with no sidebar, guide or journey line: a dark top bar with the page name, "signing as NAME", previous, next and "Leave labelling"; a progress bar; the blinding note; the record card with the decisions under the abstract; and the codebook criteria beside it. Other drawers do not open full.
Surfaces: UI Samples and labelling; CLI not applicable (layout only; `wider label` decides).

#### Scenario: The labelling focus page
- **WHEN** "ana" opens a blind queue of 5 records, 2 of them labelled, on a codebook with elements e1 and e2 and exclusion reasons a and b
- **THEN** the page reads "signing as ana", "you have labelled 2 of 5" and the blinding note, lists e1 and e2 under "Include when" and a and b under "Exclude when", and shows no sample, band, rank or model decision
- **AND** pressing 2 and choosing reason a posts the same label body as the drawer did, with the reason, label groups and note
Pinned by: `tests/unit/test_focus_page_node.py::test_label_focus_page_layout_and_save`, `tests/unit/test_focus_page_node.py::test_label_focus_settings`, `tests/unit/test_focus_page_node.py::test_label_save_reads_fields_under_the_buttons`

#### Scenario: Other drawers stay drawers
- **WHEN** a screening run's record is opened from Inspect a run
- **THEN** it opens in the side drawer, not full
Pinned by: `tests/unit/test_focus_page_node.py::test_other_drawers_not_full`

### Requirement: Fields and labels on a focus page
The system SHALL put the fields saved with a decision under the decision buttons and show the exclusion reason only once Exclude is chosen or saved; adjudication SHALL show each reviewer's signed label above the record, the agreement line and an optional reason. Keys, saving and Esc are the drawer's.
Surfaces: UI Samples and labelling; CLI not applicable (layout only; `wider label adjudicate` decides).

#### Scenario: The exclusion reason only with Exclude
- **WHEN** a record without a label is shown, then Exclude is pressed
- **THEN** the exclusion reason field is hidden before and shown after
Pinned by: `tests/unit/test_focus_page_node.py::test_reason_field_only_with_exclude`

#### Scenario: The adjudication focus page
- **WHEN** "mara" opens a disagreement between "lena" (include) and "tomas" (exclude, reason b)
- **THEN** the page reads "deciding as mara", shows both labels with "Signed lena" and "Signed tomas" above the record and the agreement line, and saving include posts the adjudication with mara and the optional reason
- **AND** when "lena" opens it, the page says once that lena labelled this sample too, and the decision still saves
Pinned by: `tests/unit/test_focus_page_node.py::test_adjudication_focus_page`, `tests/unit/test_focus_page_node.py::test_adjudication_saves_with_adjudicator_and_reason`, `tests/unit/test_focus_page_node.py::test_adjudicator_who_labelled_the_sample`
