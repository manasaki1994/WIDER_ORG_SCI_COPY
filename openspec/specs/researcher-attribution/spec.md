# Researcher Attribution Specification

## Purpose
Who signed each change, as WIDER can know it (declared or inferred), and how names appear in the files a review
shares: pseudonyms by default, real names or no names by one setting, with the list linking them kept on this computer.

## Requirements

### Requirement: Researcher list with stable pseudonyms
The system SHALL keep one entry per name it records as an author: the name, a pseudonym "Researcher n" (numbered from 1 by first recorded change, stable, never reused), declared or inferred, its source and when first seen. Entries are added when a change is journalled; the list is never pruned. Unnamed changes get no entry.
Surfaces: CLI `wider researchers list [--json]`; UI Recall page, card "Names in shared files".

#### Scenario: Pseudonyms by first appearance
- **WHEN** a synthetic project records a change by "ana" from the page, then one by "ben" with `--by ben`, then another by "ana"
- **THEN** `wider researchers list` shows "ana" as "Researcher 1" and "ben" as "Researcher 2", both declared, with the sources "typed on the page" and "given with --by"
- **AND** `--json` returns the same entries with the keys `name`, `pseudonym`, `status`, `source` and `first_seen`, and the card on the Recall page shows the same table
Pinned by: `tests/unit/test_researchers.py::test_pseudonyms_by_first_appearance`, `tests/unit/test_researchers_js_node.py::test_card_lists_researchers`

#### Scenario: A pseudonym is never reused
- **WHEN** "ben"'s only change is undone, and later "cleo" records a change
- **THEN** "ben" keeps "Researcher 2" and "cleo" is "Researcher 3"
Pinned by: `tests/unit/test_researchers.py::test_pseudonym_never_reused`

#### Scenario: A change without a name
- **WHEN** the page saves a change with an empty name box
- **THEN** no entry is added and the list is unchanged
Pinned by: `tests/unit/test_researchers.py::test_unnamed_change_adds_no_entry`

#### Scenario: A name of the form of a pseudonym
- **WHEN** a change is signed "Researcher 2" (any case), from the page or with `--author`
- **THEN** the change is refused with a message saying the name has the form of the pseudonyms and asking for another name, and nothing is recorded
Pinned by: `tests/unit/test_researchers.py::test_pseudonym_like_name_refused`

#### Scenario: A pseudonym-shaped name in a settings save, an undo or an import
- **WHEN** a settings save from the page or an undo is signed "Researcher 2", or a label import names such a reviewer (with `--reviewer` or inside a Rayyan file)
- **THEN** it is refused with the same message before anything changes, and a Rayyan file naming such a reviewer imports no row at all
- **AND** a project whose History already holds such an author still writes its shared files
Pinned by: `tests/unit/test_researchers.py::test_pseudonym_like_name_refused_everywhere`, `tests/unit/test_attribution_export.py::test_older_pseudonym_shaped_author_is_not_refused`

### Requirement: Declared and inferred names
The system SHALL mark a name declared when typed on the page or given with `--by`, `--author` or `--reviewer`, and inferred when taken from `$WIDER_REVIEWER`, `$USER`, an imported file or an older project. It stays declared unless the confirm that declared it is undone. Every surface listing entries SHALL say: "WIDER cannot verify who made a change. Declared means a researcher typed or confirmed the name."
Surfaces: CLI `wider researchers list`; UI Recall page, card "Names in shared files".

#### Scenario: Environment names are inferred
- **WHEN** `WIDER_REVIEWER=rev-env` signs one CLI change and, with it unset, `USER=login-x` signs another
- **THEN** both entries are inferred, with the sources "from $WIDER_REVIEWER" and "login name of this computer"
- **AND** a later `--by login-x` makes that entry declared, and the list and the card print the sentence above in normal black text
Pinned by: `tests/unit/test_researchers.py::test_environment_names_are_inferred`, `tests/unit/test_researchers_js_node.py::test_card_says_identity_is_not_verified`

#### Scenario: A CLI option that fell back to the environment
- **WHEN** `wider notes add` or `wider questions answer` runs without `--author`/`--by` and `USER=login-x`
- **THEN** the change is signed "login-x" and the entry is inferred, not declared
- **AND** the same holds for a library path that resolves the name before recording it: accepting a round, a dedup decision, a stream label and an adjudication, each without `--by`; none of them puts "login-x" into a package in mode `names`
Pinned by: `tests/unit/test_researchers.py::test_cli_fallbacks_are_inferred`

#### Scenario: Imported reviewer ids are inferred
- **WHEN** a synthetic Rayyan export with reviewers "rev-a" and "rev-b" is imported, and another sample is imported with the default reviewer
- **THEN** "rev-a" and "rev-b" are inferred with the source "from an imported label file", and the default id "original" gets no entry and is written "imported labels" in every mode
Pinned by: `tests/unit/test_researchers.py::test_imported_reviewers_are_inferred`

#### Scenario: Authors stored without a History change
- **WHEN** "cleo" makes only a configuration comparison and `wider export package` then runs in mode `pseudonyms`
- **THEN** the list holds "cleo" with its own pseudonym, and `comparisons.json` reads that pseudonym, not "an unlisted researcher"
Pinned by: `tests/unit/test_researchers.py::test_unjournalled_authors_are_registered`

### Requirement: Confirming and joining names
The system SHALL let a researcher confirm an inferred entry (it becomes declared) and join one entry into another (both then share the target's pseudonym and status; the joined entry's number is retired). Each is a History change of kind `researcher_confirm` or `researcher_join`, undoable; neither rewrites History or any recorded author.
Surfaces: CLI `wider researchers confirm <name>`, `wider researchers join <name> --into <name>`; UI Recall page card, "Confirm" and "Same researcher as".

#### Scenario: Confirm and undo
- **WHEN** inferred entry "login-x" is confirmed, then the change is undone
- **THEN** after the confirm it is declared, after the undo inferred again, and History's authors are byte-for-byte as before
Pinned by: `tests/unit/test_researchers.py::test_confirm_and_undo`

#### Scenario: Two names for one researcher
- **WHEN** inferred "login-x" (Researcher 3) is joined into declared "Ana Silva" (Researcher 1) from the card, and the share bundle is then written
- **THEN** the list shows "login-x" as "Researcher 1", declared, joined into "Ana Silva", and Researcher 3 is not given again
- **AND** every change by either name reads "Researcher 1" in the bundle; in mode `names` both read "Ana Silva" and "login-x" occurs nowhere; `wider researchers join login-x --into "Ana Silva"` gives the same list
- **AND** joining a name into itself, into an unknown name, into an entry already joined elsewhere, or joining a name that another name was joined into is refused with a message naming the reason, by the CLI (exit 1) and the route (400)
Pinned by: `tests/unit/test_researchers.py::test_join_two_names`, `tests/unit/test_researchers.py::test_join_refusals`, `tests/unit/test_researchers_js_node.py::test_join_select_sends_into`

### Requirement: One attribution setting for shared files
The system SHALL hold `exports.attribution` in `project.yaml`: `pseudonyms` (default), `names` (declared names; inferred ones stay pseudonyms) or `none` (no name or pseudonym). A change is journalled. The card SHALL show the choices with the explanation in normal black text; the bundle and package buttons and commands SHALL state the mode before writing.
Surfaces: CLI `wider researchers attribution pseudonyms|names|none`; UI Recall page card, its three choices.

#### Scenario: Default and change
- **WHEN** a new synthetic project has no `exports` section and `wider report --bundle` runs
- **THEN** it first prints "Names in shared files: pseudonyms (Researcher 1, Researcher 2 ...)", and the line beside "Write share bundle" and the package button reads the same
- **AND** choosing "Real names" on the card or running `wider researchers attribution names` writes `exports.attribution: names`, journals one `config` change that undo reverses, and the line then reads "Names in shared files: declared names; inferred names stay pseudonyms"
- **AND** an unknown mode is refused by the CLI (exit 1) and the route (400), and `project.yaml` is unchanged
Pinned by: `tests/unit/test_researchers.py::test_attribution_default_and_change`, `tests/unit/test_researchers_js_node.py::test_mode_line_beside_buttons`

#### Scenario: The explanation is shown with the choices
- **WHEN** the card is opened
- **THEN** each choice has a tick-box-style radio beside its text, and below them, once, in normal black text: what readers see in each mode, that the list linking names and pseudonyms stays on this computer, that names WIDER inferred from this computer are never written as names, and that sharing files written in different modes lets readers match pseudonyms to names
Pinned by: `tests/unit/test_researchers_js_node.py::test_card_explanation_once_in_black`

### Requirement: Attribution applied to every shared file
The system SHALL apply the mode to every name field and every WIDER-written text in every file under `exports/`, the share bundle and the replication package; keep dates, kinds, versions, decisions and justifications; write "no author recorded" for an unnamed change in every mode; state the mode in `REPRODUCE.md` and `manifest.json`; and leave History and every stored author unchanged.
Surfaces: CLI `wider report [--bundle]`, `wider export package`; UI Recall page, Replication package card.

#### Scenario: Pseudonyms across the package
- **WHEN** a synthetic project has changes by declared "Ana Silva", inferred "login-x" and one unnamed change, covering labels by both, an answered question, an unlock, an accepted round, a stream label, a configuration comparison, a validation choice and a sample extension, and the package is built in mode `pseudonyms`
- **THEN** every file in the zip writes "Researcher 1" and "Researcher 2" where the names were, neither name occurs in any file, the unnamed change reads "no author recorded", and each change keeps its date, kind and summary otherwise unchanged
- **AND** `manifest.json` holds `"attribution": "pseudonyms"` and `REPRODUCE.md` says "Researcher names are replaced by pseudonyms"
Pinned by: `tests/unit/test_attribution_export.py::test_pseudonyms_across_the_package`

#### Scenario: Real names keep inferred names hidden
- **WHEN** the same package is built in mode `names`
- **THEN** "Ana Silva" is written as recorded and "login-x" as "Researcher 2"
Pinned by: `tests/unit/test_attribution_export.py::test_names_mode_hides_inferred`

#### Scenario: The methods draft and local copies follow the setting
- **WHEN** the project has a validation target chosen by "Ana Silva" and, in mode `pseudonyms`, the methods draft, `exports/report.md` and `exports/prisma_2020_checklist.md` are written
- **THEN** each reads "Researcher 1" where the name was, the Recall page's report pane shows that shared copy under the mode line, and the History page still shows "Ana Silva"
Pinned by: `tests/unit/test_attribution_export.py::test_methods_draft_follows_the_setting`

#### Scenario: No attribution
- **WHEN** it is built in mode `none`
- **THEN** no name and no "Researcher n" occurs in any file, the interventions' author column reads "not shown", and `labels.csv` has a `reader` column numbering the readers of each sample 1, 2 instead of `reviewer`
- **AND** the agreement between the two readers computed from that `labels.csv` equals the report's
- **AND** the report's per-reader label counts keep one count per reader (after a join, the joined names' counts are added)
Pinned by: `tests/unit/test_attribution_export.py::test_none_mode`, `tests/unit/test_attribution_export.py::test_name_keyed_counts_survive_none_mode`

#### Scenario: History unchanged
- **WHEN** the package is built in each of the three modes
- **THEN** the `history` table and every author column read the same before and after, apart from the stage results the writes record
Pinned by: `tests/unit/test_attribution_export.py::test_history_unchanged_by_exports`

### Requirement: The mapping stays on this computer
The system SHALL keep the list linking names and pseudonyms in the project store only and MUST NOT write it into the report, the share bundle or the replication package.
Surfaces: CLI `wider researchers list`; UI Recall page card.

#### Scenario: No mapping in shared files
- **WHEN** the package is built in mode `names`
- **THEN** no file of the zip holds both a pseudonym and the name it stands for, and no file lists the researchers
Pinned by: `tests/unit/test_attribution_export.py::test_no_mapping_in_shared_files`

#### Scenario: A join in shared files
- **WHEN** an inferred name was joined into a declared one and the join undone, and the report, bundle and package are written in each mode
- **THEN** the join and its undo read "two researcher names counted as one", with neither name nor pseudonym
Pinned by: `tests/unit/test_attribution_export.py::test_join_line_names_no_one`

### Requirement: Name scan before sharing
The system SHALL scan every file of the bundle and the package, except bibliographic text (titles, journals, authors, search terms), for each name the mode hides (whole words, case-sensitive, 3+ characters), and name fields and paths for `$USER` and `$WIDER_REVIEWER` values that never signed. A hit refuses the output, removes the partial files and names the file, the line and the pseudonym.
Surfaces: CLI `wider report --bundle`, `wider export package`; UI Recall page, Replication package card.

#### Scenario: A name left in a file refuses the output
- **WHEN** a researcher's saved `methods.md` contains "Ana Silva" and the package is built in mode `pseudonyms`
- **THEN** `wider export package` exits 1 and `POST /api/outputs/package` answers 400 with "methods.md line 3: the name of Researcher 1; edit the file", and no partial package remains
- **AND** a hidden name placed only in `unlock.json`'s `by` is refused the same way, the message saying it is a WIDER bug to report
Pinned by: `tests/unit/test_attribution_export.py::test_name_left_in_a_file_refuses`

#### Scenario: The login name is never written
- **WHEN** `USER` is "login-x" and `WIDER_REVIEWER` is "rev-env", neither ever signed a change, a record title contains "login-x", and the bundle and the package are built in each mode
- **THEN** both are written (the title is bibliographic text) and neither value occurs anywhere else
- **AND** when "login-x" signed a change but was never confirmed, it does not occur in mode `names` either
Pinned by: `tests/unit/test_attribution_export.py::test_login_name_is_never_written`

### Requirement: Older projects become inferred entries
The system SHALL, on first opening a project made before this change, add an inferred entry with source "older version" for every author in History and every name in the reviewer table, numbered in order of each name's first change. History is not changed.
Surfaces: CLI any command opening the project; UI any page.

#### Scenario: An older project
- **WHEN** a synthetic project made by the previous schema has History authors "ui", "login-x" and "Ana Silva" (first changes in that order) and labels by "Ana Silva", and is opened
- **THEN** the list holds "ui" as Researcher 1, "login-x" as Researcher 2 and "Ana Silva" as Researcher 3, all inferred, source "older version"
- **AND** History's earlier rows are unchanged and opening it again adds nothing
Pinned by: `tests/unit/test_researchers.py::test_older_project_becomes_inferred`

### Requirement: Inferred names marked inside WIDER
The system SHALL show names as recorded inside WIDER and mark each author whose entry is inferred with "(inferred, not confirmed)" in History and the drawer's history.
Surfaces: CLI `wider history list`; UI History page, record drawer's history.

#### Scenario: The marker
- **WHEN** History holds one change by declared "Ana Silva" and one by inferred "login-x"
- **THEN** `wider history list` and the History page show "login-x (inferred, not confirmed)" and "Ana Silva" without a marker
Pinned by: `tests/unit/test_researchers.py::test_history_marks_inferred`, `tests/unit/test_researchers_js_node.py::test_history_marks_inferred`

### Requirement: No researcher name in model prompts
The system SHALL leave researcher names out of the decision texts it adds to model prompts; the ledger files and the Questions page keep them.
Surfaces: CLI `wider decisions list`; UI Questions page, "Decisions".

#### Scenario: An admitted name without its author
- **WHEN** "Ana Silva" admits a name to a block and a refinement round then builds its prompt with the fake backend
- **THEN** the prompt and `prompt_<tag>.md` hold the decision without "Ana Silva", and `wider decisions list` still shows "by Ana Silva"
Pinned by: `tests/unit/test_attribution_export.py::test_prompt_texts_carry_no_author`
