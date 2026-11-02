# recall-and-outputs Specification

## Purpose
Recall and outputs is the last available stage of the WIDER procedure (stage key `recall`). It estimates how much of the relevant literature a screening protocol kept, measured against the human-labelled, band-stratified probability sample. It then writes the report, the PRISMA 2020 diagram, a methods paragraph, reference-manager exports and a replication package, all generated from the project store. Only the reference exports carry abstracts; the report, the share bundle and the package do not, and none of them carries raw model responses.

## Requirements

### Requirement: Stratified Horvitz–Thompson recall estimate
The system SHALL estimate recall as a stratified Horvitz–Thompson ratio over a probability sample whose every record has a final label (w_h = N_h/n_h), in three variants: `strict` (relevant = human include, kept = screening include), `lenient` (kept = include or maybe) and `cautious` (relevant and kept = include or maybe). The primary variant is `protocol.recall.primary_variant` (default `lenient`).
Surfaces: CLI `wider recall PROTOCOL`; UI Recall page, button "Estimate recall".

#### Scenario: Hand-computed estimate on a two-band sample
- **WHEN** band S1 is a census of 4 records (weight 1) with two human includes and one maybe, band S2 has 4 of 40 records (weight 10) with two human includes, and the screening includes one S1 include, marks the other maybe, includes the S1 maybe, includes one S2 include and excludes the other
- **THEN** `strict` recall is 11/22, `lenient` recall is 12/22 and `cautious` recall is 13/23
- **AND** `strict` reports `n_missed` 2, and the missed-records list holds the one excluded S2 include
Pinned by: `tests/unit/test_recall.py::test_recall_estimator_hand_computed`

#### Scenario: Partly labelled band
- **WHEN** band S2 of 40 records has 10 sampled, of which 5 are labelled (2 human includes, one kept), and band S1 is a fully labelled census as above
- **THEN** `wider recall` refuses it as in "Recall revealed only on a complete validation sample"
- **AND** the rows the curve and the checksums read (`estimate._rows`) give S2 the weight 40/5 = 8, not 40/10, so its unlabelled sampled records count neither as relevant nor as not relevant, and a fully labelled sample gives the same estimate as before
Pinned by: `tests/unit/test_recall.py::test_partly_labelled_band_weighted_by_labelled`

#### Scenario: No relevant records
- **WHEN** no labelled record in the sample counts as relevant for a variant
- **THEN** that variant's recall is null, it gets no interval of any kind, and its text says recall could not be estimated

Pinned by: `tests/unit/test_recall.py::test_estimate_carries_interval_and_lines`

### Requirement: Recall reads a named result set
The system SHALL estimate recall from the protocol's active result set unless a set is named, state that set's number, label, role, models, run ids and date, and label a development set "development set: not an independent validation".
Surfaces: CLI `wider recall PROTOCOL [--set N] [--compare M]`; UI Recall page, "Result set" select (default the active set), "Compare with set" select.

#### Scenario: Recall names the set it read
- **WHEN** protocol `main` has sets 1 (active) and 2, and `wider recall main` and `wider recall main --set 2 --json` run
- **THEN** the first reads set 1 and the second set 2, and each output names its set's number, label, role, models, run ids and date
- **AND** the Recall page's estimate (`GET /api/recall?protocol=main&set=2`) gives the same numbers and names set 2
Pinned by: `tests/unit/test_result_sets.py::test_readers_use_active_set`

#### Scenario: Choosing a set on the Recall page
- **WHEN** the Recall page opens for `main` with set 1 active and set 2 finished
- **THEN** the "Result set" select shows set 1 with protocol `main`, its label, role, model and run ids; choosing set 2 and pressing "Estimate recall" shows set 2's estimate, and choosing "Compare with set" 1 shows both estimates side by side as `wider recall main --set 2 --compare 1` does
- **AND** the "Result sets" card on the LLM screening page still marks set 1 active
Pinned by: `tests/unit/test_result_sets.py::test_set_select_keeps_active`, `tests/unit/test_result_sets.py::test_recall_compare_sets`

### Requirement: Human decision and screening decision per sampled record
The system SHALL take one human decision per sampled record: the adjudication if there is one, else the label of the reviewer named with `--primary`, else the first reviewer's label. Records without a decision, band or weight are left out. A sampled record without a screening decision under the protocol counts as `unscreened` and never as kept.
Surfaces: CLI `wider recall --primary REVIEWER`; UI Recall page.

#### Scenario: Sampled record not yet screened
- **WHEN** a labelled include in the sample has no decision under the protocol
- **THEN** it counts as missed in every variant
- **AND** `unscreened_in_sample` is at least 1, and the recall stage result warns about unscreened records

### Requirement: Seeded bootstrap and linearised intervals
The system SHALL report for each variant, as cross-checks of the primary, a seeded stratified percentile bootstrap interval and a linearised (Taylor) logit Wald interval. Census bands (weight 1) are held fixed, other bands resampled. B defaults to `protocol.recall.bootstrap_b` (10000, at least 100), capped at 50,000 with a warning; seed and level are the project seed and `protocol.recall.ci_level` (0.95). Shown in JSON and method section.
Surfaces: CLI `wider recall -B N`, `wider report`.

#### Scenario: Same seed, same interval
- **WHEN** the estimate is run twice with B = 200 on an unchanged project
- **THEN** both runs give the same bootstrap interval for every variant
- **AND** the estimate reports B and the seed used

Pinned by: `tests/unit/test_recall.py::test_bootstrap_crosscheck_unchanged`

#### Scenario: Bootstrap replicates are capped
- **WHEN** `protocol.recall.bootstrap_b` or `-B` asks for more replicates than the cap (50,000; 300 in the test)
- **THEN** the estimate runs the cap, reports both `B` and `B_requested`, and the stage result warns "Bootstrap replicates capped at 300 (1,000 were asked for); the cross-check interval in the report uses the capped number."
- **AND** a value at or under the cap runs unchanged without the warning

Pinned by: `tests/unit/test_recall.py::test_huge_bootstrap_b_is_capped`

#### Scenario: Recall at the boundary
- **WHEN** a variant's recall is exactly 1
- **THEN** its linearised interval is a plain Wald interval clipped to [0, 1], and both cross-checks are the point 1 while the primary interval is not

Pinned by: `tests/unit/test_recall.py::test_bootstrap_crosscheck_unchanged`

### Requirement: Per-band table, rule of three and missed records
The system SHALL report for each band n, weight, human includes, human maybes, kept-strict and kept-lenient counts, and add a rule-of-three note to a band without human includes. It SHALL list as missed the human includes that the screening neither included nor marked maybe, with record id, band, weight and screening decision.
Surfaces: CLI `wider recall` (variant table, band table, missed list); UI Recall page result card.

#### Scenario: Band with no positives
- **WHEN** a band of 30 labelled records contains no human include
- **THEN** that band's note reads "no positives in sample; rule of three: missed rate ≤ 0.10"

### Requirement: Recall stage result
The system SHALL record every estimate as a stage result for stage `recall`, action `estimate`, headlined by the primary-variant recall in percent, its primary interval (or why there is none) and its number of relevant validation papers. It is not ok without a primary recall value; it warns of unscreened sampled records and missed records, not of few papers or perfect recall (said once, in the lines).
Surfaces: CLI `wider recall` (stage result after the tables); UI Recall page result card.

#### Scenario: Summary of an estimate
- **WHEN** an estimate with `lenient` recall 0.75 is summarised
- **THEN** the stage result has stage `recall`, a headline containing "75", "95% interval" and the number of relevant validation papers, and one row per variant
Pinned by: `tests/unit/test_stage_result.py::test_summarize_samples_screen_recall_report_artifact`

### Requirement: Report generated from the store
The system SHALL write `exports/report.md` and `exports/report.json` from the store and set the meta key `last_report` to {at, protocol, result_set, sample, path}, marking the `recall` stage done. It reads the active result set unless one is named and records its key. Without a protocol it uses the most recently screened one; without a sample, the last probability sample with reviewers, else the first.
Surfaces: CLI `wider report [--set N]`; UI Recall page, "Build report".

#### Scenario: Report marks the recall stage done
- **WHEN** the report is written with protocol `main` and sample `validation`
- **THEN** meta `last_report` names protocol `main`, sample `validation` and path `exports/report.md`
- **AND** the last `recall` result has action `report`, outputs `exports/report.md` and `exports/report.json`, and no next stage
Pinned by: `tests/unit/test_stage_result.py::test_write_report_sets_last_report_so_recall_is_done`

#### Scenario: Report sections
- **WHEN** the report is written for an imported and deduplicated project
- **THEN** `report.md` has the sections Question, Identification, Seed paper coverage, Prior-review coverage, Abstracts, Ranking, Sample, Screening, Adjacent literatures and Recall
- **AND** the Identification rows list records retrieved per platform, the total before deduplication, the duplicates removed and the records remaining for screening, followed by any adjacent-literature exclusions
Pinned by: `tests/unit/test_recall.py::test_report_and_bundle`, `tests/unit/test_diagnostics.py::test_interval_in_coverage_diagnosis_and_report` (the section name)

#### Scenario: Report without a recall estimate
- **WHEN** the project has no screening decisions under any protocol yet
- **THEN** the recall section is empty and the stage result warns that the report has no recall estimate and to label the probability sample and run a protocol first

#### Scenario: Report tied to its set
- **WHEN** the report is written for `main` while set 1 is active, and set 2 is then made active
- **THEN** `report.json` and meta `last_report` name result set `main` (set 1), and the methods line names set 1 with its models
- **AND** the report already written is not changed by the switch
Pinned by: `tests/unit/test_result_sets.py::test_exports_carry_set`

### Requirement: Share bundle without abstracts
The system SHALL write a share bundle (default `exports/share_bundle`, replaced on each write) with no abstracts, database exports, raw model responses, screening error text or evaluation reports. It carries the models behind each screening decision and each run's backend changes and snapshots, without key values or the home path. Names follow the attribution mode. Writing it records a stage result with action `bundle`.
Surfaces: CLI `wider report --bundle`; UI Recall page, "Write share bundle".

#### Scenario: Bundle content
- **WHEN** the share bundle is written for a project with imported records
- **THEN** it holds `project.yaml`, `REPRODUCE.md`, `benchmark.csv`, the artifact folders, `records.csv`, `screen_decisions.csv`, `labels.csv`, `report.md` and `report.json`
- **AND** from each run folder it holds `run_spec.json`, `prompt.md`, `schema.json`, `errors.csv` with only the columns `record_id` and `pass`, and `decisions.csv` with `record_id`, `pass`, `ok`, `raw_decision`, `rule_decision`, `input_sha256`, `attempts`, `title`, `provider`, `model`, `model_reported` and `backend_hash`
- **AND** `screen_decisions.csv` has the columns `decided_by`, `models` and `mixed_models`
Pinned by: `tests/unit/test_recall.py::test_report_and_bundle`

#### Scenario: Bundle carries no abstracts or raw files
- **WHEN** the share bundle is written
- **THEN** the `records.csv` header has no abstract column, only `abstract_words`
- **AND** the bundle contains no `*.ris` file, no `responses.jsonl` and no `eval_report.md`
Pinned by: `tests/unit/test_recall.py::test_report_and_bundle`

#### Scenario: Bundle carries no error text
- **WHEN** a screening run has a failed call whose stored error text contains a phrase from the model's output, and the share bundle is written
- **THEN** the run's `errors.csv` in the bundle has the header `record_id,pass` and one row for that call
- **AND** the phrase occurs neither in that `errors.csv` nor in the run's `decisions.csv` in the bundle

#### Scenario: Bundle carries backend changes
- **WHEN** a run was continued from model `m1` on `m2` after confirmation, its backend's key variable holds a synthetic secret, and the share bundle is written
- **THEN** the run's folder in the bundle holds `backend_changes.jsonl` and both `backend_<hash>.json` snapshots
- **AND** neither the secret value nor the home-folder path occurs anywhere in the bundle
Pinned by: `tests/unit/test_recall.py::test_bundle_backend_changes`

#### Scenario: Experimental backend marked in the bundle
- **WHEN** a screening run used a backend added from an experimental preset, and the share bundle is written
- **THEN** the run's `run_spec.json` carries `experimental: true` and its `limitations`, and its `decisions.csv` rows carry "experimental backend: not validated"
- **AND** `report.md` names the experimental backend in its methods line
Pinned by: `tests/unit/test_recall.py::test_bundle_marks_experimental`

#### Scenario: Labels in the bundle follow the setting
- **WHEN** a sample is labelled by declared "Ana Silva" and inferred "login-x", and the bundle is written in mode `pseudonyms`
- **THEN** `labels.csv`'s `reviewer` column holds "Researcher 1" and "Researcher 2", and in mode `names` "Ana Silva" and "Researcher 2"
- **AND** `project.yaml`'s `terms_allowed_by` follows the same mode
Pinned by: `tests/unit/test_attribution_export.py::test_bundle_labels_follow_the_setting`

### Requirement: Sanitized and scanned share outputs
The system SHALL put in the bundle a sanitized `project.yaml` that keeps every methodological setting and a `REPRODUCE.md` listing what must be restored to run it again. Before the bundle or a package is finished, every file is scanned for secrets and for names the attribution hides; a finding refuses the output and removes the partial files.
Surfaces: CLI `wider report --bundle`, `wider export package`; UI Recall page, Outputs page package button.

#### Scenario: Sanitized project file
- **WHEN** `project.yaml` has a backend with `api_key_env: SOME_KEY_VAR`, a header `X-Gateway-Key: {env:SOME_HEADER_VAR}`, a CLI command whose argv[0] is under the home folder, and protocol settings, and the bundle is written
- **THEN** the bundle's `project.yaml` keeps the provider, model, `SOME_KEY_VAR`, `{env:SOME_HEADER_VAR}` and every protocol setting, writes the argv[0] starting with `~`, and contains no home-folder path
- **AND** `REPRODUCE.md` names `SOME_KEY_VAR`, `SOME_HEADER_VAR`, the CLI and its version, and says the bundle is not executable on its own
Pinned by: `tests/unit/test_sanitize.py::test_sanitized_project_yaml`, `tests/unit/test_recall.py::test_bundle_reproduce_md`

#### Scenario: Secret scan refuses a leak
- **WHEN** a synthetic key value of 20 characters, set in `SOME_KEY_VAR`, or a string shaped like `sk-` plus 30 characters, has been typed into a record title, and the bundle or the package is written
- **THEN** `wider report --bundle` and `wider export package` exit with code 1, `POST /api/report/bundle` and `POST /api/outputs/package` answer 400, each naming the file, the line and the kind of finding but not the value
- **AND** no partial bundle or package remains
Pinned by: `tests/unit/test_sanitize.py::test_secret_scan_refuses`, `tests/unit/test_recall.py::test_bundle_refused_on_secret`

#### Scenario: Name scan after the secret scan
- **WHEN** a hidden researcher name is left in `methods.md` and the bundle or the package is written
- **THEN** the output is refused as in "Name scan before sharing" and no partial files remain
Pinned by: `tests/unit/test_attribution_export.py::test_name_left_in_a_file_refuses`

### Requirement: PRISMA 2020 flow diagram
The system SHALL draw a PRISMA 2020 flow diagram from the store into `exports/prisma_2020.svg`, counts in `exports/prisma_2020.json`. Identification is split into databases and other methods (`citation_chain`); screening counts come from the chosen protocol, by default the latest. Full-text boxes are dashed "not yet recorded" boxes; all text is escaped.
Surfaces: CLI `wider export prisma [--protocol]`; UI PRISMA reporting (Flow diagram tab); Recall, PRISMA report card, "Diagram".

#### Scenario: Counts and diagram
- **WHEN** a project's records have been imported, deduplicated and screened under protocol `main`
- **THEN** duplicates removed equal identified minus unique, and excluded plus sought equals screened
- **AND** the SVG shows "Records excluded (n = ...)" and a "not yet recorded" box, and the JSON file holds the counts

#### Scenario: Other methods and escaped text
- **WHEN** the counts include citation searching labelled "Citation <searching>" and the title is "A & B"
- **THEN** the SVG shows "Citation &lt;searching&gt; (n = 7)" and "A &amp; B"
- **AND** an unknown protocol is refused
Pinned by: `tests/unit/test_outputs.py::test_prisma_counts_and_svg`

#### Scenario: Nothing imported
- **WHEN** the diagram is requested before any import
- **THEN** the system refuses with a message starting "nothing imported yet"

### Requirement: Methods paragraph draft and methods.md
The system SHALL draft a methods paragraph from the project's facts, ending with the first 12 characters of the protocol hash, and keep the researcher's own text in `methods.md` in the project folder. On save, trailing whitespace is trimmed to one newline, and empty text is refused with "the methods text is empty".
Surfaces: CLI `wider export methods [--save]`; UI Recall page, Methods card, buttons "Draft from the project" and "Save methods.md".

#### Scenario: Draft and save
- **WHEN** the methods draft is requested and the text "My own words." followed by two newlines is saved
- **THEN** the draft names the protocol and its "protocol hash"
- **AND** `methods.md` holds "My own words." followed by exactly one newline
Pinned by: `tests/unit/test_outputs.py::test_methods_draft_and_save`

### Requirement: Reference-manager exports
The system SHALL export a chosen decision set's records (`include` by default, `include+maybe`, `maybe`, `exclude` or a route set) as RIS, BibTeX and CSV in `exports/references/`, named `<protocol>_<set>` (`+` as `_and_`), from the active result set unless named; RIS note and CSV name the set, mark unresolved; CSV adds `status`, `pathway`. Abstracts included. An unknown decision set, or no decisions, is refused.
Surfaces: CLI `wider export references [--which] [--set N]`; UI Export records card.

#### Scenario: Include export
- **WHEN** references are exported for protocol `main` with set `include`
- **THEN** three files are written with one entry per included record, the RIS file carries "N1  - WIDER main: include" and every BibTeX key is unique
- **AND** the set `include+maybe` writes files named `main_include_and_maybe`, and the set `all` is refused
Pinned by: `tests/unit/test_outputs.py::test_reference_exports`

#### Scenario: Export of another set
- **WHEN** `main` has sets 1 (active) and 2, and references are exported with `--set 2`
- **THEN** the files list set 2's includes, the RIS file carries "N1  - WIDER main set 2: include" and the CSV has a `result_set` column with 2
Pinned by: `tests/unit/test_result_sets.py::test_exports_carry_set`

#### Scenario: Exclusions
- **WHEN** protocol `main` has 5 exclude decisions, 4 reached by both passes and 1 by the third read, and references are exported with set `exclude`
- **THEN** the files list the 5 records, each RIS note reads "N1  - WIDER main: exclude (record n)", and the CSV's `decided_by` column holds "A,B" for four and "A,C" or "B,C" for one
- **AND** the Export records card reproduces every older set in all three formats, byte for byte and with the same names inside its ZIP: include, include+maybe, maybe and exclude by their presets, each route set by preset Every decision with that one pathway
Pinned by: `tests/unit/test_outputs.py::test_reference_exclude_set`, `tests/unit/test_record_selection.py::test_older_export_parity`

#### Scenario: Unresolved record marked
- **WHEN** synthetic protocol `main` has 2 maybe decisions, one with a majority of maybe reads and one whose three reads disagree (stored `decided_by` "split"), and references are exported with set `maybe`
- **THEN** the first record's RIS note reads "N1  - WIDER main: maybe (record n)" and the second's "N1  - WIDER main: maybe (unresolved: no majority) (record m)", the BibTeX notes say the same, and the CSV's `status` column holds `final` and `unresolved` and its `pathway` column the route
- **AND** an include or exclude record has `status` `final`, and a record carried or imported from a set where it was unresolved is marked unresolved too
Pinned by: `tests/unit/test_outputs.py::test_unresolved_marked_in_every_format`

### Requirement: Replication package with a manifest
The system SHALL zip the bundle, PRISMA diagram, `methods.md`, search strings and refinement records into `exports/replication_package_<timestamp>.zip` with a `MANIFEST.txt` (SHA-256 per file) and a `manifest.json` naming each output's result set and the attribution mode, stating it has no abstracts, database exports or raw model responses. It warns when `methods.md` is missing.
Surfaces: CLI `wider export package [--protocol] [--set N] [--sample]`; UI Recall page, Replication package card.

#### Scenario: Package content
- **WHEN** the replication package is built after a reference export and a saved `methods.md`
- **THEN** the zip contains `MANIFEST.txt` with SHA-256 hashes, `methods.md`, `prisma_2020.svg`, `share_bundle/report.md`, `share_bundle/records.csv` and the search strings
- **AND** it contains no reference export, no `.ris` file and no record abstract
Pinned by: `tests/unit/test_outputs.py::test_package_has_no_abstracts_and_a_manifest`

#### Scenario: Package names the sets
- **WHEN** the package is built for `main` with set 2 active
- **THEN** `manifest.json` names result set `main#2` for the report, the bundle and the exports it holds
- **AND** `wider export package --set 1` and the same set chosen in the Recall page's select name `main` (set 1) instead, and set 2 stays active
Pinned by: `tests/unit/test_result_sets.py::test_exports_carry_set`

#### Scenario: Rounds and the unlock record in the package
- **WHEN** the package is built after two rounds (one count pasted) and an unlock
- **THEN** the zip holds `rounds.json` with both rounds, the `pasted` source, each round's `lost` seeds and the frozen query marked, `limits.json`, and `unlock.json` listing the unlock with exactly `unlock_no`, `cause`, `from_version`, `new_version`, `at`, `by` (as the attribution mode writes it), `final_before`, `counts_as_seen`, `seen_choice` (`count`, `keep`, or empty when nothing was asked) and `seen` (each `{benchmark_id, doi, title_norm}`, no abstract), and `MANIFEST.txt` hashes all three
- **AND** a project never unlocked has no `unlock.json`
Pinned by: `tests/unit/test_outputs.py::test_package_holds_rounds_and_unlock`, `tests/unit/test_outputs.py::test_package_has_no_abstracts_and_a_manifest`

### Requirement: Exports served only from the exports folder
The system SHALL serve output files to the UI only from inside `<project>/exports`; any other path returns 404 "no such file in the exports folder". Output routes answer engine errors with status 400 and the message.
Surfaces: UI Recall page download links (`GET /api/outputs/file`); CLI `wider export` writes files to disk instead.

#### Scenario: Path outside the exports folder
- **WHEN** the UI requests `/api/outputs/file` with path `project.yaml`, `../project.yaml`, `exports/../project.yaml`, `/etc/hosts` or `store.sqlite`
- **THEN** each response is 404
- **AND** a file written under `exports/`, such as a reference export or the package zip, is served with status 200
Pinned by: `tests/unit/test_outputs.py::test_output_routes_and_download_guard`

#### Scenario: Export commands from the command line
- **WHEN** `wider export prisma`, `wider export methods`, `wider export references` and `wider export package` run on a screened project
- **THEN** each exits with code 0 and prints the file it wrote
- **AND** `wider export references --which bad` exits with code 1
Pinned by: `tests/unit/test_outputs.py::test_export_cli`

### Requirement: Unknown protocol or sample is refused
The system SHALL refuse a recall estimate, or a report with an explicitly named protocol or sample, when the protocol has no screening decisions ("no screening decisions under protocol '<name>'") or the sample does not exist ("no sample '<name>'"), writing nothing and recording no stage result. The CLI prints the message without a traceback and exits 1; the API returns 404.
Surfaces: CLI `wider recall`, `wider report`; UI Recall page (`GET /api/recall`, `POST /api/report`).

#### Scenario: Unknown protocol on the command line
- **WHEN** `wider recall nosuch` runs on a project whose screening decisions are all under protocol `main`
- **THEN** it prints "no screening decisions under protocol 'nosuch'" and exits with code 1
- **AND** no new `recall` stage result is recorded

#### Scenario: Unknown sample on the command line
- **WHEN** `wider recall main --sample nosuch` or `wider report --sample nosuch` runs
- **THEN** each prints "no sample 'nosuch'", shows no traceback and exits with code 1
- **AND** `exports/report.md` is not written or changed

#### Scenario: Unknown names through the API
- **WHEN** the Recall page asks for an estimate or writes the report with an unknown protocol or sample
- **THEN** the response is 404 with the message, and the page shows it in the result card

#### Scenario: Defaults still write a report without recall
- **WHEN** the report is written without naming a protocol or sample on a project with no screening decisions
- **THEN** the report is written without a recall section, as before

### Requirement: Unknown result set or out-of-step second screen is refused
The system SHALL refuse a recall estimate or report naming a result set that does not exist ("no result set <n> of '<name>'"), or reading a second-screen protocol out of step with its source ("out of step", naming the source set and `wider screen sets use <name> <n> --with-source`), writing nothing and recording no stage result.
Surfaces: CLI `wider recall --set`, `wider report --set`; UI Recall page.

#### Scenario: Unknown set and out-of-step second screen
- **WHEN** `wider recall main --set 9` runs on a protocol with two sets, and `wider recall main_second` runs after `main`'s active set was switched away from `main_second`'s source
- **THEN** the first prints "no result set 9 of 'main'" and the second the "out of step" message with the fix, each exits 1 and records no stage result
- **AND** `GET /api/recall` answers 404 with the same messages
Pinned by: `tests/unit/test_result_sets.py::test_incompatible_combination_refused`

### Requirement: Report help states the real defaults
The system SHALL describe in `wider report --help` the defaults it applies: without `--protocol` the latest protocol with screening decisions (the newest decision over all its result sets) and its active set, and without `--sample` the last probability sample with labels, else the first probability sample.
Surfaces: CLI `wider report --help`; UI none.

#### Scenario: Help text
- **WHEN** `wider report --help` runs
- **THEN** the `--protocol` help names the latest protocol with screening decisions as the default and the `--sample` help names the last labelled probability sample
- **AND** neither option's help claims `main` or `validation` as its default

#### Scenario: Latest protocol means the most recently screened one
- **WHEN** protocol `zeta` was screened first and protocol `alpha` later, and `wider report` runs without `--protocol`
- **THEN** the report uses `alpha`

#### Scenario: Latest protocol over result sets
- **WHEN** protocol `alpha` was screened after `zeta`, and then `zeta` gains set 2 screened later than `alpha`
- **THEN** `wider report` without `--protocol` uses `zeta`, with its active set
Pinned by: `tests/unit/test_result_sets.py::test_protocols_order_over_sets`

### Requirement: Report written by POST, read by GET
The system SHALL write the report only on an explicit write: `wider report` and `POST /api/report` build it once, write `report.md` and `report.json`, set `last_report` and record exactly one `recall` stage result (action `report`, no extra `estimate` result). `GET /api/report` SHALL only return the last written report and write nothing (404 before any report).
Surfaces: CLI `wider report`; UI Recall page, "Build report" (`POST /api/report`), report preview (`GET /api/report`).

#### Scenario: Building the report from the UI
- **WHEN** the researcher clicks "Build report" on a project with a labelled probability sample and screening decisions
- **THEN** one POST request writes `report.md` and `report.json` and returns the markdown and the stage result
- **AND** exactly one new `recall` stage result is recorded, with action `report`

#### Scenario: Reading does not write
- **WHEN** `GET /api/report` is called after a report was written
- **THEN** it returns that report's markdown, and no file, `last_report` value or stage result changes

#### Scenario: Report with bundle
- **WHEN** `wider report --bundle` runs
- **THEN** the report is built and written once and exactly one `recall` stage result with action `report` is recorded
- **AND** the bundle is made from that written report

#### Scenario: No report yet
- **WHEN** `GET /api/report` is called before any report was written
- **THEN** it returns 404 and nothing is written under `exports/`

### Requirement: Report headline uses the primary variant
The system SHALL give the stage-result headline of a written report the recall of `protocol.recall.primary_variant`, name that variant and add its primary interval (or "uncertainty could not be reliably estimated") and its number of relevant validation papers, in the words of the estimate's headline. A report.json without the interval keeps the old headline.
Surfaces: CLI `wider report` (stage result printed); UI Recall page result card after "Build report".

#### Scenario: Strict as primary variant
- **WHEN** `protocol.recall.primary_variant` is `strict`, strict recall is 0.50, lenient recall is 0.75, and the report is written
- **THEN** the headline contains "50.0%" and "strict" and does not contain "75.0%"

Pinned by: `tests/unit/test_recall.py::test_headline_uses_the_primary_variant`

#### Scenario: Interval and count in the report headline
- **WHEN** the report is written for the estimate of "Seven of seven kept"
- **THEN** its headline contains "recall 100.0% (lenient; 95% interval 0.16 to 1.00; 7 relevant validation papers)"
Pinned by: `tests/unit/test_recall.py::test_same_words_on_every_surface`, `tests/unit/test_stage_result.py::test_summarize_samples_screen_recall_report_artifact`

#### Scenario: Report written before this change
- **WHEN** the headline is built from a report.json whose variants carry no interval fields
- **THEN** it reads "recall 75.0% (lenient)" without an interval or a count

Pinned by: `tests/unit/test_stage_result.py::test_summarize_samples_screen_recall_report_artifact`

### Requirement: Interval level follows ci_level
The system SHALL compute every recall interval at `protocol.recall.ci_level`: the linearised interval SHALL use the normal quantile for that level instead of a fixed 1.96, and every interval label (CLI tables, UI Recall page, stage results, `report.md`) SHALL show that level as a percentage. The default 0.95 gives z = 1.96 and the label "95% interval".
Surfaces: CLI `wider recall`, `wider report`; UI Recall page.

#### Scenario: A 90% level
- **WHEN** `protocol.recall.ci_level` is 0.90 and recall is estimated on a sample with a sampled band
- **THEN** the linearised interval uses z ≈ 1.645 and is narrower than at 0.95
- **AND** the CLI table, the UI table, the stage result and the Recall section of `report.md` label the recall intervals "90% interval" and never "95%"; the search side's known-paper intervals stay at 95%

Pinned by: `tests/unit/test_recall.py::test_ci_level_sets_z_and_labels`

#### Scenario: Default level unchanged
- **WHEN** `protocol.recall.ci_level` is left at 0.95
- **THEN** the labels read "95% interval" and the bootstrap and linearised cross-checks have the same values as before this change

Pinned by: `tests/unit/test_recall.py::test_bootstrap_crosscheck_unchanged`, `tests/unit/test_recall.py::test_ci_level_sets_z_and_labels`

### Requirement: Report card counts unique records
The system SHALL show on the report's stage result the number of unique records that the report itself states, taken from the report's deduplication counts.
Surfaces: CLI `wider report`; UI Recall and outputs page, stage result card after "Build report".

#### Scenario: Report built
- **WHEN** the report of a screened synthetic project is written
- **THEN** the result's "unique records" metric equals the number of records in the corpus
- **AND** the report text says "<that number> unique records"
Pinned by: `tests/unit/test_stage_result.py::test_write_report_sets_last_report_so_recall_is_done`

### Requirement: One report build per press
The system SHALL send one report build, and one share-bundle write, per press: while the request runs its button is disabled and a second press sends nothing; afterwards the button takes the keyboard back if the focus was lost.
Surfaces: UI Recall and outputs page, "Build report" and "Write share bundle"; CLI `wider report` and `wider report --bundle` run once per command.

#### Scenario: Build report pressed twice
- **WHEN** "Build report" is pressed twice before the first build has answered
- **THEN** one build request is sent and the button is disabled until it answers
- **AND** afterwards the button is enabled and holds the focus
Pinned by: `tests/unit/test_ui_followup_r23.py::test_build_report_twice_builds_once_and_keeps_the_keyboard`

#### Scenario: Share bundle pressed twice and a failed build
- **WHEN** "Share bundle" is pressed twice before the first request answers, or a report build fails
- **THEN** one bundle request is sent and the button is pressed again only after it answers
- **AND** after a failed build the button can be pressed again and has the keyboard, and the error is shown
Pinned by: `tests/unit/test_ui_followup_r30.py::test_the_share_bundle_pressed_twice_sends_one_request_and_a_failed_build_gives_the_button_back`

### Requirement: Report and bundle from the page without screening decisions
The system SHALL build the report and the share bundle from the Recall and report page on a project with no screening decisions, as `wider report` and `wider report --bundle` do: the same files are written and the page does not refuse or disable the buttons for that reason.
Surfaces: UI Recall and report page, Build report and Share bundle; CLI `wider report [--bundle]`.

#### Scenario: No screening decisions
- **WHEN** a synthetic project has records but no screening decisions, and the researcher presses Build report and then Share bundle
- **THEN** both answer 200, the report and bundle files exist, and the report text matches what `wider report --bundle` writes on a copy of the project
- **AND** the protocol select says "no protocol decisions yet" while both buttons stay enabled
Pinned by: `tests/unit/test_stage_pages.py::test_report_and_bundle_without_decisions`

### Requirement: The report, methods draft and package say whether the search is validated
`report.md` ("Search validity"), the methods draft and the package manifest SHALL say whether the search was validated on a held-out set, from one note (looks, final mark, unlocks): when the set was first seen, the unlock count, papers added to Validation since, by source, how many the query also found, each choice whether shown papers count as seen, "Search not validated: …" when none, and if gating was off.
Surfaces: CLI `wider report`, `wider export methods|package`; UI Recall page.

#### Scenario: Unlocked, then a new validation paper
- **WHEN** the report and methods draft are built after an unlock with no new Validation paper, and again after one is added with source `colleague`
- **THEN** the first pair says "Search not validated: validation set seen on <date> (1 unlock); no paper added since.", and the second "Validation set seen on <date> (1 unlock); 1 paper added since.", then the line stating the unlock's choice, then "Where the validation papers came from: 1 from a colleague." and its exact interval
- **AND** `manifest.json` carries the first unlock's date, the number of unlocks and the count
Pinned by: `tests/unit/test_outputs.py::test_search_validity_in_report_methods_and_package`

#### Scenario: A look before the freeze and an unlock both show
- **WHEN** the validation set (B002, B003) was shown before the freeze and the researcher counted the papers as seen (the look is logged), the uploads were marked final, and the query was later unlocked
- **THEN** the report, the methods draft and the Recall page give the same note, naming the look's date, the uploads-final date and the unlock's date, and B002 and B003 count as seen
- **AND** with no unlock, a counted look that showed every Validation paper gives "Search not validated: validation set seen on <look date> (looked at before the freeze); no paper added since."
- **AND** a look logged before this change, with no list of papers, gives "Search not validated: validation set looked at on <date> before the freeze; the papers shown were not recorded."
- **AND** a look with no hidden Validation paper (none, or only papers from the query's results) asks nothing, adds no `validation_seen_by_look` entry and never gives the "not recorded" line
Pinned by: `tests/unit/test_outputs.py::test_validity_note_reads_look_final_and_unlocks`, `tests/unit/test_outputs.py::test_early_look_before_this_change_is_reported_by_date`

#### Scenario: Each choice on shown papers is stated
- **WHEN** B002 and B003 were shown before the freeze and kept as validation, v3 was frozen, a file was added while the uploads were final and the papers were again kept as validation, and the report, the methods draft, the package and the Recall page are built
- **THEN** each carries "Validation set looked at on <date> before the freeze (2 papers shown); the researcher chose to keep them as validation." and "Uploads reopened on <date> while v3 was the frozen query (2 validation papers shown); the researcher chose to keep them as validation.", and otherwise reads as with no look, so the search is reported as validated on B002 and B003
- **AND** `manifest.json` carries `looks_before_freeze` with `counted` false, and `unlock.json` the uploads entry with `counts_as_seen` 0
- **AND** after v2 is then frozen in place of v3 with "Count them as seen", the note reads "Search not validated: validation set seen on <date> (1 unlock); no paper added since." followed by the two lines and "v2 was frozen on <date> in place of the frozen query v3 (2 validation papers shown); the researcher chose to count them as seen."
Pinned by: `tests/unit/test_outputs.py::test_validity_note_states_the_seen_choice`

#### Scenario: Each unlock choice is stated
- **WHEN** v3 is frozen with V001 and V002 in the validation set, v3 is unlocked into v4 with "Keep them as validation", v4 is frozen, v5 is saved with "Keep them as validation", v5 is frozen, and the report, the methods draft, the package and the Recall page are built
- **THEN** each carries "v3 was unlocked on <date> into v4 (2 validation papers shown); the researcher chose to keep them as validation." and "v5 was saved on <date> after the frozen query v4 (2 validation papers shown); the researcher chose to keep them as validation.", the note otherwise reads "Development and validation sets.", and the search is reported as validated on V001 and V002
- **AND** `unlock.json` lists both entries with `seen_choice` `keep` and `counts_as_seen` 0, and `manifest.json` carries `n_unlocks` 2 and `unlocks_kept` 2
- **AND** in a second project the same steps with "Count them as seen" give "Search not validated: validation set seen on <date> (2 unlocks); no paper added since." followed by the two lines ending "the researcher chose to count them as seen."
- **AND** a save while the uploads were final gives "v1 was saved on <date> while the uploads were final (3 validation papers shown); the researcher chose to …", and undoing the mark with no frozen query gives "Uploads reopened on <date> (2 validation papers shown); the researcher chose to …"
- **AND** an unlock entry written before this change (`seen_choice` empty, papers shown) adds no choice line and reads as before
Pinned by: `tests/unit/test_unlock_seen_choice.py::test_every_output_states_each_unlock_choice`

#### Scenario: Gating off is stated in the note
- **WHEN** `identification.validation_after_freeze` is false and the report, the methods draft and the package are built
- **THEN** the note adds "Validation set not hidden (validation_after_freeze off)." and none of them says the search was validated
- **AND** `manifest.json` carries `validated` false and `validation_after_freeze` false
Pinned by: `tests/unit/test_outputs.py::test_gating_off_is_stated_in_the_note`

#### Scenario: Validation papers the query also found
- **WHEN** two Validation papers with source `prior_review` have DOIs that match imported records, and a third does not
- **THEN** the report, the methods draft, the package manifest and the Recall page add "2 validation papers were also found by the query" to the note
- **AND** a fourth Validation paper with source `query_results` adds "1 validation paper came from the query results and is not counted"
Pinned by: `tests/unit/test_outputs.py::test_validity_note_counts_papers_the_query_found`

### Requirement: The validity note names used and former validation papers
The search validity note in `report.md`, the methods draft, `manifest.json` and the Recall page SHALL give the recall on the used validation papers marked "not independent", a line for each revision after a kept look, and the validation papers moved to Development after they had been shown (former validation papers), with the date of the move and, while the query results are readable, how many the query found.
Surfaces: CLI `wider report`, `wider export methods|package`; UI Recall page.

#### Scenario: Used papers in the note
- **WHEN** B002 and B003 are used validation papers, the query found B002, and the report, the methods draft, the package and the Recall page are built
- **THEN** each carries "Recall on the 2 used validation papers (not independent): 1 of 2." and `manifest.json` carries `used_validation` with the ids, found and n
Pinned by: `tests/unit/test_validation_reuse.py::test_note_names_used_and_former_papers`

#### Scenario: Former validation papers in the note
- **WHEN** B002 and B003 had been shown, were moved to Development on <date>, and the query found neither
- **THEN** the report, the methods draft, the package and the Recall page carry "2 validation papers that had been shown were moved to Development on <date> (former validation papers: B002, B003)." and "Former validation papers: 0 of 2 found by the query."
- **AND** `manifest.json` carries `former_validation` with each paper's id, the move's time and author, and whether the query found it
- **AND** with a fresh validation paper B007 added since, the note still reads as validated on B007, followed by these lines
Pinned by: `tests/unit/test_validation_reuse.py::test_note_names_used_and_former_papers`

### Requirement: PRISMA-S search report
The system SHALL write `exports/prisma_s.md` and `.json` with the 16 items of PRISMA-S (Rethlefsen et al. 2021), each filled from the store by a fixed map or "not recorded"; dates are import dates unless a search date was recorded, and say so; the peer-review item names the model second reader and says it is not a human peer review.
Surfaces: CLI `wider export prisma-s`; UI PRISMA reporting (PRISMA-S tab); Recall, PRISMA report card, "PRISMA-S".

#### Scenario: Items filled and missing
- **WHEN** the synthetic project has imported two platforms, run one second-reader review and one seed chaining run
- **THEN** `prisma_s.md` lists both platforms with their strings and ledgers, the citation-searching item names the chaining run, the peer-review item names the review run as a model second reader, and the dates item says "import date; search date not recorded"
- **AND** the "updates" item says "not recorded"
Pinned by: `tests/unit/test_outputs.py::test_prisma_s_export`

### Requirement: Checking and provenance records in the package
The system SHALL add to the replication package the PRISMA-S report (`prisma_s.md`, `prisma_s.json`), the translation ledger per archived string (`<stem>_ledger.json`), the decision ledger (`decisions.jsonl`, `.csv`, dismissed ones marked, empty if none) and the provenance per round, authors as the attribution mode writes them, and MUST NOT add any file from `seeds/`, any extracted text or any cached response.
Surfaces: CLI `wider export package`; UI Recall page, Replication package card.

#### Scenario: Refinement records in the package
- **WHEN** the package is built after two rounds with three decisions in the ledger, one of them dismissed
- **THEN** the zip holds `decisions.jsonl` with three lines and `decisions.csv` with three rows, the dismissed one marked, `prisma_s.md`, a ledger file next to each archived string and `provenance_1.json`, and no file from `seeds/` and nothing from `lookup_cache`
Pinned by: `tests/unit/test_search_provenance.py::test_package_holds_the_refinement_records`

#### Scenario: Ledger authors follow the setting
- **WHEN** a refinement decision was accepted by inferred "login-x" and the package is built in mode `pseudonyms`
- **THEN** its `author` in `decisions.jsonl`, `decisions.csv` and `provenance_1.json` and its consequence text read "Researcher n", not "login-x"
Pinned by: `tests/unit/test_attribution_export.py::test_ledger_authors_follow_the_setting`

### Requirement: Recall interval for unequal weights
The system SHALL give each recall variant an approximate primary interval at `protocol.recall.ci_level` for unequal band weights: inside (0, 1) Clopper–Pearson bounds on the Korn–Graubard effective number (stratified linearised variance, df correction, at most Kish's effective size of the relevant papers' weights); at recall 0 or 1, or zero variance, on that Kish size. Never a single point; never called exact or validated.
Surfaces: CLI `wider recall PROTOCOL`; UI Recall page, "Estimate recall".

#### Scenario: Matches the survey beta interval
- **WHEN** recall is estimated on the two-band sample of "Hand-computed estimate on a two-band sample"
- **THEN** the `strict` interval is 0.00075 to 0.99925 (to 5 decimals), as R `survey::svyciprop(method = "beta")` gives
- **AND** a three-band sample (census, weight 10, weight 100) with recall 0.5256 gives 0.01596 to 0.98983
Pinned by: `tests/unit/test_recall_uncertainty.py::test_matches_survey_beta_interval`

#### Scenario: Perfect observed recall with equal and unequal weights
- **WHEN** all 10 relevant validation papers were kept, once with equal weights and once with 4 papers of weight 1, 3 of weight 10 and 3 of weight 50
- **THEN** the equal-weight interval is 0.69 to 1.00 and the unequal-weight interval is 0.43 to 1.00
- **AND** neither interval is the single point 1.00 to 1.00
Pinned by: `tests/unit/test_recall_uncertainty.py::test_perfect_recall_equal_and_unequal_weights`

#### Scenario: One more missed paper never narrows the interval
- **WHEN** one of the 10 unequal-weight relevant papers of "Perfect observed recall with equal and unequal weights" is missed, in any band
- **THEN** the lower bound is never above the 0.43 of none missed (0.42 for a missed weight-1 paper)
Pinned by: `tests/unit/test_recall_uncertainty.py::test_one_miss_never_raises_the_lower_bound`

#### Scenario: Zero observed recall
- **WHEN** none of 10 relevant validation papers of equal weight was kept
- **THEN** recall is 0 and its interval is 0.00 to 0.31
Pinned by: `tests/unit/test_recall_uncertainty.py::test_zero_recall`

#### Scenario: Level 0.90
- **WHEN** `protocol.recall.ci_level` is 0.90 and all 10 equal-weight relevant papers were kept
- **THEN** the lower bound is 0.74, above the 0.69 of the 0.95 level
Pinned by: `tests/unit/test_recall_uncertainty.py::test_level_090`

#### Scenario: Coverage in simulated samples
- **WHEN** 300 stratified samples are simulated per scenario of `scripts/recall_interval_sim.py` (rare relevant papers, weights up to 500, small sampled bands, true recall 0, 0.06, 0.8, 0.85, 0.94 and 1)
- **THEN** wherever an interval is shown it holds the true recall in at least 95% of the samples and is never a point
Pinned by: `tests/unit/test_recall_uncertainty.py::test_coverage_on_simulated_samples`

### Requirement: Uncertainty that cannot be reliably estimated
The system SHALL show no interval when it cannot defend one, and say why: with no relevant validation paper, "Recall could not be estimated: the labelled sample holds no relevant validation papers"; when a sampled band shows no relevant paper, or inside (0, 1) when a sampled band holds a relevant paper but one labelled record or df is under 1, "Uncertainty could not be reliably estimated:" and the reason. Recall and exact bounds show.
Surfaces: CLI `wider recall`; UI Recall page; `report.md`.

#### Scenario: Sampled band without a relevant paper
- **WHEN** a census band holds 3 kept relevant papers and a sampled band S2 of weight 10 has 4 labelled records, none relevant
- **THEN** recall is 1 and shown, the variant has no interval, and its text reads "Uncertainty could not be reliably estimated: band S2 shows no relevant paper in its 4 labelled records (1 in 10 sampled), so what it hides cannot be estimated."
- **AND** the same band fully read (weight 1) does not remove the interval
- **AND** on a probability sample the exact bounds of "Exact bounds valid at every look" follow that text
Pinned by: `tests/unit/test_recall_uncertainty.py::test_sampled_band_without_relevant_paper_gives_no_interval`, `tests/unit/test_validation_anytime.py::test_exact_bounds_where_no_design_interval`

#### Scenario: Hidden misses get no interval instead of a wrong one
- **WHEN** samples are simulated from a population whose sampled band hides every one of its missed relevant papers
- **THEN** an interval given regardless would hold the true recall in under 30% of the samples, and the shipped estimate gives one in under 40% of the samples, holding the true recall in at least 95% of those
Pinned by: `tests/unit/test_recall_uncertainty.py::test_hidden_misses_get_no_interval_instead_of_a_wrong_one`

#### Scenario: A band with one labelled record
- **WHEN** a sampled band S3 of weight 50 has one labelled record, a relevant paper the screening missed, and another band has relevant papers that were kept
- **THEN** recall is shown, the variant has no interval, and its text reads "Uncertainty could not be reliably estimated: band S3 holds a relevant paper but only one labelled record."
Pinned by: `tests/unit/test_recall_uncertainty.py::test_one_record_band_has_no_interval`

#### Scenario: No relevant validation paper
- **WHEN** the labelled sample holds no human include
- **THEN** `strict` recall is null, it has no interval, and its text reads "Recall could not be estimated: the labelled sample holds no relevant validation papers."
Pinned by: `tests/unit/test_recall_uncertainty.py::test_no_relevant_paper`

### Requirement: Count of relevant papers and plain notes
The system SHALL state the number of relevant validation papers behind each recall estimate; below 20 add that this is few and that labelling more firms it up; at observed recall 1 say that no relevant validation paper was missed, that this does not mean the screening keeps every relevant paper, and the lowest recall the interval allows. All surfaces use the same sentences.
Surfaces: CLI `wider recall`, `wider report`, `wider export methods`; UI Recall page.

#### Scenario: Seven of seven kept
- **WHEN** all 7 relevant papers (weights 4×1, 2×10, 1×50) were kept under the primary variant
- **THEN** the lines read "Based on 7 relevant validation papers.", "95% interval: 0.16 to 1.00.", the few note, and "No relevant validation paper was missed. This does not mean the screening keeps every relevant paper: a recall as low as 0.16 fits what was seen."
Pinned by: `tests/unit/test_recall_uncertainty.py::test_seven_of_seven_lines`, `tests/unit/test_recall.py::test_estimate_carries_interval_and_lines`

#### Scenario: Same words on every surface
- **WHEN** the estimate of "Seven of seven kept" is made with `wider recall main`, `wider recall main --json`, `GET /api/recall?protocol=main` and `wider report`
- **THEN** the CLI output, the API `lines`, the Recall page and `report.md` hold the same sentences, said once (not again as stage-result warnings), and the stage result and the methods draft name the interval and "7 relevant validation papers"
Pinned by: `tests/unit/test_recall.py::test_same_words_on_every_surface`, `tests/unit/test_recall_page_node.py::test_recall_table_lines_and_columns`

### Requirement: Technical report states the interval method
The system SHALL write under the report's Recall section a subsection "Recall: method" naming, per variant, the interval method (Korn–Graubard or Kish), the effective number, df, and the bootstrap (B, seed) and linearised intervals as cross-checks, with references and the simulation study. Page, `wider recall` and report say the one method sentence in the same words; only the report names methods.
Surfaces: CLI `wider recall -B N`, `wider report`; UI Recall page, "Build report" (report preview).

#### Scenario: Method section
- **WHEN** the report is written for an estimate with recall 1 under unequal weights
- **THEN** `report.md` has "Recall: method" naming "Kish" for that variant with its effective number, the bootstrap and linearised intervals labelled cross-checks, and "Korn and Graubard (1998)"
- **AND** the CLI table and the Recall page table have no "bootstrap" or "linearised" column
Pinned by: `tests/unit/test_recall.py::test_report_method_section`, `tests/unit/test_recall.py::test_same_words_on_every_surface`

#### Scenario: Method sentence on every surface
- **WHEN** the estimate of "Seven of seven kept" is made with `wider recall main`, `GET /api/recall?protocol=main` and `wider report`
- **THEN** the CLI output (after the variant table), the API `method_note` (under the Recall page table) and the report's "Recall: method" hold the sentence "The interval is approximate: Clopper–Pearson bounds on an effective number of relevant papers (Korn–Graubard from the stratified variance; Kish's effective size at recall 0 or 1), never more than that Kish size. In simulated samples like WIDER's it held the true recall at least as often as its level promises wherever it is shown, usually more often, so it errs on the wide side."
- **AND** the report says "not an exact or validated confidence interval" and names `scripts/recall_interval_sim.py`; no surface calls the interval exact
Pinned by: `tests/unit/test_recall.py::test_method_sentence_on_every_surface`, `tests/unit/test_recall_uncertainty.py::test_method_sentence_is_honest`, `tests/unit/test_recall_page_node.py::test_method_sentence_under_the_table`

### Requirement: Open reviewer disagreements in the estimate
The system SHALL count the labelled records on which reviewers disagree and nobody adjudicated, and while there are any on a validation sample refuse the recall estimate, saying how many ("1 record has labels that disagree and is not adjudicated") in the refusal of "Recall revealed only on a complete validation sample". The evaluation and the comparison keep their own line (llm-screening).
Surfaces: CLI `wider recall`; UI Recall page; `report.md`.

#### Scenario: One open disagreement, then adjudicated
- **WHEN** a second reviewer labels one record of the "Seven of seven kept" sample "exclude" against the first reviewer's "include"
- **THEN** `final_labels` marks that record `open_disagreement`, and `wider recall` refuses with a sentence holding "1 record has labels that disagree and is not adjudicated", recording no look
- **AND** after adjudication the estimate is given, its open disagreement count is 0 and no line mentions a disagreement
Pinned by: `tests/unit/test_recall.py::test_open_disagreements_named_once`

### Requirement: Recall on independent papers and a capture-recapture estimate
The system SHALL report, while the validation set is shown, how many independent validation papers (source not `query_results`) the query retrieves as "m of n" with the note's interval and a scope label, and after screening Chapman's estimate from them. Each number MUST carry the dependence caveat; no overlap gives no estimate; all found gets a warning; while hidden a hidden-set line shows.
Surfaces: CLI `wider coverage --independent [--protocol P]`; UI Recall, "Independent check" card.

#### Scenario: Recall on independent papers, then a total estimate
- **WHEN** the validation set is shown, 10 independent validation papers exist, the query retrieves 8, and after screening protocol P has 40 includes of which 8 are independent validation papers judged relevant out of 10
- **THEN** the report gives "8 of 10" by source with its interval and "for these 10 papers only; they are not a random sample of all relevant papers", Chapman's estimate (41·11/9 − 1, about 49) and "about 7 relevant papers may be missing from both", each with the dependence caveat
- **AND** with no overlap the estimate reads "not defined (no overlap)", and the package holds `independent_estimate.json` with counts, benchmark ids and the caveat and no title or abstract
Pinned by: `tests/unit/test_seed_chain.py::test_independent_estimate`

#### Scenario: All independent papers found
- **WHEN** the query retrieves all 4 of 4 independent validation papers
- **THEN** the report gives "4 of 4" with its interval, a note that few papers stand behind it, and "all found here does not mean none are missing"
Pinned by: `tests/unit/test_seed_chain.py::test_interval_and_chapman`

#### Scenario: Independent check while the validation set is hidden
- **WHEN** the validation set is hidden and `wider coverage --independent` runs
- **THEN** it says "validation set hidden: independent check not shown", records no look, and gives the independent Development papers as "m of n, not a validation figure (seen during development)"
Pinned by: `tests/unit/test_seed_chain.py::test_independent_check_hidden_set`

### Requirement: Comparisons in the report, methods draft and package
The system SHALL list every configuration comparison, finished or not, in `report.md` (sample, configurations, repeats, labelled n, misses and false includes), in the methods draft (how many, on which sample, not a corpus estimate) and in the package as `comparisons.json` (settings and counts). The methods draft and `manifest.json` MUST name each pass's model.
Surfaces: CLI `wider report`, `wider export methods|package`; UI Recall page, report, Methods and package cards.

#### Scenario: Two comparisons listed
- **WHEN** comparisons `cmp1` (2 configurations) and `cmp2` (3 configurations, stopped after 2) ran on `development`, and the report, the methods draft and the package are built
- **THEN** `report.md` has a "Configuration comparisons" section with both, `cmp2` marked "not finished", and the methods draft says "2 configuration comparisons were run on the purposive development sample; their figures describe that sample, not the corpus."
- **AND** the zip holds `comparisons.json` with both comparisons' settings and counts, hashed in `MANIFEST.txt`, and no title, abstract or model response
Pinned by: `tests/unit/test_compare_outputs.py::test_comparisons_in_report_methods_package`

#### Scenario: No comparison
- **WHEN** no comparison was run
- **THEN** `report.md` says "No configuration comparison was run." and the package has no `comparisons.json`
Pinned by: `tests/unit/test_compare_outputs.py::test_no_comparison`

#### Scenario: The model per pass
- **WHEN** protocol `main` ran pass A on `a`, pass B on `b` and the judge on `c`
- **THEN** the methods draft names the three models with their pass, and `manifest.json` carries `models_per_pass` with `A`, `B` and `C`
- **AND** when every pass ran on `a`, the draft names one model for all passes, and a second screen names its reads S1, S2 and S3
Pinned by: `tests/unit/test_compare_outputs.py::test_model_per_pass_named`

### Requirement: Recall target and rule set before the results
The system SHALL store a recall target and one rule for a recall below the target (`maybes-as-includes`, `top-strata` with a rank depth, or `revise-prompt`) with time and author, by any write path. When a recall result was seen before one of these, the primary variant or the interval level was set or changed, outputs SHALL say so.
Surfaces: CLI `wider validation target`, `wider protocol set|reset`; UI plan card "Recall target and rule"; Setup page.

#### Scenario: Target set before any result
- **WHEN** no recall estimate has been made and `wider validation target 0.90 --rule maybes-as-includes` runs (or the card's "Save target")
- **THEN** `protocol.recall.target` is 0.90, `protocol.recall.low_recall_rule` is `maybes-as-includes`, History gets a `recall_target` entry with the author, and `wider validation status` says "Recall target 0.90, rule: treat maybes as includes; set 2026-10-20 before any recall result was seen"
- **AND** `POST /api/validation/target` with `{"target": 0.9, "rule": "maybes-as-includes"}` stores the same, and `GET /api/validation/status` gives the same sentence; `--precision 0.05` (or `"precision": 0.05`) stores `protocol.recall.precision` 0.05, which is empty until then
Pinned by: `tests/unit/test_validation_target.py::test_target_before_results`, `tests/unit/test_validation_target.py::test_target_cli_matches_api`

#### Scenario: Target set after a result was seen
- **WHEN** a recall estimate on `validation` was made on 2026-10-21 and the target is set, or changed from 0.90 to 0.80, on 2026-10-22
- **THEN** the command says "a recall result on validation was seen on 2026-10-21, before this target was set; every output will say so", and `wider recall`, the Recall page and the report add "Recall target set on 2026-10-22, after a recall result was seen (2026-10-21)"
- **AND** a change keeps the earlier value in History ("target 0.90 → 0.80"), and undo restores it
Pinned by: `tests/unit/test_validation_target.py::test_target_after_look_is_said`

#### Scenario: Target set outside WIDER
- **WHEN** `project.yaml` is edited by hand so that `recall.target` differs from the value of the last `recall_target` History entry (or no entry exists)
- **THEN** every recall output says "Recall target set outside WIDER (time unknown); it may have been set after a recall result was seen"
Pinned by: `tests/unit/test_validation_target.py::test_target_set_outside_wider`

#### Scenario: Changed through the protocol after a look
- **WHEN** after a look on 2026-10-21, `wider protocol set recall.target 0.80`, `POST /api/protocol/set` with `recall.low_recall_rule`, `wider protocol reset recall.target`, a reset of the whole protocol (`wider protocol reset` with no path, or `POST /api/protocol/reset` with no path), `wider validation target 0.9 --precision 0.05`, or the Setup page changes the target, rule or precision, or `recall.primary_variant` or `recall.ci_level` is changed, on 2026-10-22
- **THEN** each write is timed and recorded like `wider validation target`, and every recall output adds "Recall target set on 2026-10-22, after a recall result was seen (2026-10-21)" (for the target, rule or precision), or "Primary variant changed on 2026-10-22, after a recall result was seen (2026-10-21)" / "Interval level changed on ..." for those two
- **AND** a whole-protocol reset that leaves these fields as they were records nothing for them
Pinned by: `tests/unit/test_validation_target.py::test_target_via_protocol_route_is_timed`, `tests/unit/test_validation_target.py::test_whole_protocol_reset_is_timed`

#### Scenario: Set, undone and set again
- **WHEN** the target is set to 0.90, the entry is undone (the target goes back to empty), and the target is then set to 0.85 with `wider validation target 0.85`
- **THEN** no output says "Recall target set outside WIDER": the undone entry's value is not the standing one, and the last entry not undone gives 0.85 with its time
Pinned by: `tests/unit/test_validation_target.py::test_undone_target_not_outside`

#### Scenario: Status shows the current target only
- **WHEN** a look on `validation` was made without a target and `wider validation target 0.80` then runs, or a look was judged against 0.80 with a rule and that target is then undone with `wider history undo`
- **THEN** `wider validation status` (and `GET /api/validation/status`, the Results dashboard) gives the current target's line only: no "No recall target was set before the results" beside a set target, and no stored verdict or "Your rule:" line for a target no longer set; `outcome` is empty and `target_stale` true
- **AND** it adds "The last recall result on validation (<date>) was judged without a target; estimate recall again for a verdict on the target 0.80." (or "... was judged against the target 0.80; no target is set now, so it gives no verdict."), until the next estimate
Pinned by: `tests/unit/test_validation_target.py::test_target_set_after_a_look_shows_current_state_only`, `tests/unit/test_validation_target.py::test_target_undone_after_a_look_shows_current_state_only`

#### Scenario: Top strata rule needs a depth
- **WHEN** `wider validation target 0.9 --rule top-strata` runs without `--depth`, and with `--depth 0` or a depth beyond the ranking
- **THEN** it exits 1 with "the top-strata rule needs --depth: the rank down to which humans screen every record", or "depth must be between 1 and N", the route answers 400, and nothing is stored
Pinned by: `tests/unit/test_validation_target.py::test_top_strata_needs_depth`

### Requirement: Target outcome at each look
The system SHALL give with each probability-sample estimate, on the primary variant's exact anytime-valid bounds, "target met" (lower bound ≥ target), "target not met" (upper bound < target) or "inconclusive" (else; no evidence of a miss); unless met, the rule's line with its outcome. Without a target, more labels are suggested when the margin exceeds a set precision.
Surfaces: CLI `wider recall`, `wider validation status`, `wider results`, `wider report`; UI Recall page, Results dashboard.

#### Scenario: Target met
- **WHEN** the target is 0.80, it is look 3, and the primary variant's exact bounds at 95% are 0.84 to 0.98
- **THEN** the lines say "Target 0.80 met: at look 3, the exact bounds valid at every look (95%), 0.84 to 0.98, lie at or above it." and `outcome` is `met` in `--json` and the API, with `outcome.basis` `exact`, and no rule line follows
Pinned by: `tests/unit/test_validation_target.py::test_outcome_met`

#### Scenario: Target not met, with the rule
- **WHEN** the target is 0.95, the rule is `maybes-as-includes`, the primary variant is `strict` with exact bounds 0.70 to 0.88, and the lenient estimate is 0.97 with exact bounds 0.95 to 0.99
- **THEN** the lines say "Target 0.95 not met: at look 1, the exact bounds valid at every look (95%), 0.70 to 0.88, lie below it." and "Your rule: treat maybes as includes. With it, recall is 0.97 (exact bounds 0.95 to 0.99): target met under your rule.", the verdict under the rule coming from the same outcome test on the rule's own bounds, each verdict holding at `ci_level` for its own variant and result set
- **AND** with rule `top-strata --depth 500` the second line is "Your rule: humans screen every record ranked 1 to 500 (K records the screening excluded there). With it, recall is R (exact bounds L to U): " followed by their outcome, counting sampled records ranked within 500 as kept; with `revise-prompt` it is "Your rule: revise the prompt. This uses up validation: draw a fresh sample before estimating again (wider sample draw probability --name NAME --fresh-from validation)."
Pinned by: `tests/unit/test_validation_target.py::test_outcome_not_met_rule_lines`

#### Scenario: Rule already applied
- **WHEN** the target 0.95 is not met, the rule is `maybes-as-includes`, and the primary variant is `lenient` or `cautious` (both count maybes as kept)
- **THEN** the rule line reads "Your rule: treat maybes as includes. Your primary estimate already counts maybes as kept, so the rule changes nothing." for either variant
Pinned by: `tests/unit/test_validation_target.py::test_rule_already_applied`

#### Scenario: Cannot tell
- **WHEN** the target is 0.90, the rule is `maybes-as-includes`, the primary variant is `strict` with estimate 0.93 and exact bounds 0.84 to 0.99, and the lenient bounds are 0.87 to 0.99; or a sampled band shows no relevant paper, so the Korn–Graubard interval is missing
- **THEN** the lines say "Inconclusive for the target 0.90: at look 1, the exact bounds valid at every look (95%), 0.84 to 0.99, contain it. This is not evidence that the target was missed; the labels so far cannot tell met from not met." and "Your rule: treat maybes as includes. With it, recall is R (exact bounds 0.87 to 0.99): inconclusive.", followed by the suggestion of "More validation needed, with the labels it may take"
- **AND** `outcome` stays `cannot_tell` and `more_validation` stays true in `--json`, the API and the stored look, so stored results and scripts read as before; lines stored before this change are repeated as stored (`wider validation status`, the Results dashboard) until the next estimate, and are drawn in black
Pinned by: `tests/unit/test_validation_target.py::test_outcome_cannot_tell`, `tests/unit/test_validation_target.py::test_inconclusive_key_kept`

#### Scenario: No target
- **WHEN** no target is set, the researcher set `protocol.recall.precision` to 0.10, and the margin (estimate minus lower bound) is 0.14
- **THEN** the lines say "No recall target was set before the results; set one with wider validation target." and "The margin is wider than planned: the lower bound is 0.14 below the estimate, more than the 0.10 planned.", followed by the precision part of the suggestion
- **AND** with a margin of 0.06 only the first line appears; with no precision set only the first line appears and no suggestion is given; the margin is always read from the exact lower bound
Pinned by: `tests/unit/test_validation_target.py::test_outcome_without_target`

#### Scenario: Same words on every surface
- **WHEN** the estimate of "Inconclusive" is made with `wider recall main`, `wider recall main --json`, `GET /api/recall?protocol=main`, `wider validation status`, `wider results` and `wider report`
- **THEN** the CLI, the API `lines`, the Recall page outcome block (in the section "Validation checks"), the Results dashboard's recall lines and `report.md` hold the same sentences, each once, in normal-size text
- **AND** the not-met line is drawn in the warning colour; the met and inconclusive lines are drawn in black, none grey, and the dashboard lists no inconclusive line among its warnings
Pinned by: `tests/unit/test_validation_target.py::test_outcome_same_words`, `tests/unit/test_recall_page_node.py::test_outcome_block_lines`, `tests/unit/test_results_dashboard.py::test_inconclusive_not_a_warning`

### Requirement: More validation needed, with the labels it may take
The system SHALL, when more validation is needed, suggest how many more records to label per band, from the labels by band and screening decision, the records left and the design, with a range: with a target for a decision at the next look (the chance of each verdict), and for a set precision. It SHALL show an alternative split and say if more labels are unlikely to show it met.
Surfaces: CLI `wider recall`, `wider validation status`, `wider validation extend --suggested`; UI Recall page.

#### Scenario: Labels for a decision and for the precision
- **WHEN** the outcome "Inconclusive" is given, `protocol.recall.precision` is 0.05, and the bands S2 and S3 are sampled
- **THEN** the lines say "More labels could settle it. To tell whether the target 0.90 is met at the next look: label about M more records (S2 a, S3 b), likely between L and U given what the labels so far leave uncertain. With M more, the chance is about P% that the target is then shown met and Q% that it is shown not met." and "For a margin of 0.05: about M2 more records (S2 c, S3 d), likely between L2 and U2." and "More labels make the answer clearer; they do not make the target more likely to be met."
- **AND** M, M2 and the band numbers are multiples of 10, a + b = M, L ≤ M ≤ U, and `--json` and the API give `more_validation.target` and `more_validation.precision`, each with `m`, `by_band`, `range`, and for the target `p_met` and `p_not_met`
- **AND** with no precision set, only the target part is given and `more_validation.precision` is null
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_target_and_precision`

#### Scenario: An alternative split
- **WHEN** the suggestion for the target is given
- **THEN** the next line says "Alternative: the same M spread in proportion to the records left (S2 e, S3 f): chance about P2% met, Q2% not met.", with the chances from the same simulated samples, and `more_validation.target.alternative` gives `by_band`, `p_met` and `p_not_met`
- **AND** `wider validation extend validation --band S2=30` (or the card's own numbers) takes any split; the suggestion is advice, not a limit
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_alternative_split`

#### Scenario: Stored with the look and used by the extension
- **WHEN** the suggestion was made at look 1, the page is reloaded and `wider validation status` runs, and then `wider validation extend validation --suggested` runs
- **THEN** the reload and the status repeat the stored suggestion without simulating again, and the extension adds the stored per-band numbers minus each band's unlabelled sample records
- **AND** after a label is changed with no new estimate, `--suggested` exits 1 with "the labels changed since the last recall estimate; estimate recall again to update the suggestion" (the route answers 409) and adds nothing
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_stored_with_look`, `tests/unit/test_validation_suggest.py::test_suggested_refused_after_label_change`

#### Scenario: Time limit
- **WHEN** `protocol.recall.suggest_seconds` is 1 and the suggestion would take longer
- **THEN** it is given from the draws done so far, the line adds "(approximate: from B' of 50 draws; time limit reached)", and `more_validation.approximate` is true
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_time_limit`

#### Scenario: What the suggestion is built from
- **WHEN** the suggestion is computed for `validation` with labels in S2 and S3, 5 development records in S3's frame, and a result set whose screening decisions are known for every record
- **THEN** each simulated sample keeps every label made and adds only records drawn without replacement from the band's unlabelled frame records outside the development sample; the relevance of an added record is drawn from its band's and screening decision's rate in the labels so far (a Jeffreys posterior, so a cell with no relevant label still has a small rate); each simulated sample is judged as the real one would be (the exact bounds); the rates are a planning model only and never decide the verdict; the extra labels go step by step to the band where they most lower the estimate's variance; and the range comes from repeating this over draws of those rates
- **AND** no screening score, rank or model probability stands in for a label: with every label removed, the suggestion is not given and the line says "no validation labels yet: plan the sample with wider validation plan"
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_uses_labels_only`

#### Scenario: Labels beyond 300
- **WHEN** a synthetic low-prevalence frame leaves 6,000 unlabelled records in the sampled bands, its deep band shows no relevant label, and settling the target 0.90 is expected only after about 2,400 more labels
- **THEN** the suggestion gives that number, not a cap: the totals tried go up by 10 to 300, by 50 to 1,000, by 250 to 5,000 and by 1,000 beyond, up to the records left; the band numbers sum to M and no band gets more than its records left
- **AND** when M is above 300 the line after the suggestion says "Recall is estimated again once the added records are labelled; adding them in smaller batches (wider validation extend --band) gives more looks. Once the result is settled, the rest need not be labelled."
- **AND** the time limit of "Time limit" still applies, and `--suggested` adds the M records like any other suggestion
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_beyond_300`

#### Scenario: Recall below the target
- **WHEN** the target is 0.90 and the estimate is 0.84 with exact bounds 0.72 to 0.93
- **THEN** after the suggestion the lines add "Recall is estimated at 0.84, below the target 0.90: more labels are unlikely to show the target met (chance about P% even with N more); they would more likely show it not met.", given when the chance of "met" at the largest total tried is under 10%
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_below_target_said`

#### Scenario: Recall too close to the target to settle
- **WHEN** the estimate is 0.91 with target 0.90, and only the last total tried, every record left in the sampled bands, reaches a decision in `plan_power` (80%) of the simulated samples
- **THEN** the target part says "Only labelling all N records left in the sampled bands is expected to settle whether the target 0.90 is met.", with the chances as in "Labels for a decision and for the precision", followed by the alternative split and, when they apply, the warning and share line of "A warning when settling the target may be impractical"; `--suggested` adds those N records like any other suggestion
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_cap_said`

#### Scenario: Before every sampled record is labelled
- **WHEN** the outcome at look 1 is inconclusive, the suggested records are added with `--suggested`, and 10 of them are not labelled yet when `wider recall main` runs
- **THEN** no estimate is given and no look is recorded: the refusal of "Recall revealed only on a complete validation sample" says "10 of N records have no label"; `wider validation status` repeats the stored suggestion of look 1
- **AND** once the 10 are labelled the estimate is look 2, as in "The suggestion follows the labels"
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_with_unlabelled_sample_records`

#### Scenario: A suggestion stored before this change
- **WHEN** a look made before this change stored a suggestion capped at 300 labels, and recall is estimated again with the same labels
- **THEN** the suggestion is computed afresh (no cap, the new words, the warning when it applies) and stored in its place; `wider validation status` before that estimate repeats the old suggestion as stored
Pinned by: `tests/unit/test_validation_suggest.py::test_old_stored_suggestion_recomputed`

#### Scenario: The suggestion follows the labels
- **WHEN** the suggested records are added and labelled, and recall is estimated again
- **THEN** the outcome is judged at look 2 with the same exact bounds, and the suggestion is computed afresh from all labels and the records now left
Pinned by: `tests/unit/test_validation_suggest.py::test_suggest_updates_with_labels`

### Requirement: Exact bounds valid at every look
The system SHALL bound recall with exact confidence sequences for the relevant count of each band and screening decision (sampling without replacement), combined with the level split equally over the cells, so the bounds hold at every look at once at `ci_level`, also with no relevant paper in a band, under any extension. Looks SHALL be numbered per sample, with no limit.
Surfaces: CLI `wider recall`, `wider validation status`, `wider report`, `wider export methods`; UI Recall page.

#### Scenario: Looks counted by labels
- **WHEN** `wider recall main` runs on `validation`, runs again with no label changed, set 2 is then estimated on the same labels, and after 10 more labels `wider recall main` runs once more
- **THEN** the first three estimates are look 1 and the last is look 2; its outcome line says "at look 2, the exact bounds valid at every look (95%) ..." and `--json` gives `look: {index: 2}` and `bounds: {low, high, level: 0.95, method: "anytime-hypergeometric", cells}`, while the estimate's own Korn–Graubard interval stays at `ci_level`
- **AND** a fresh sample `validation2` starts at look 1, and `validation` keeps its looks
Pinned by: `tests/unit/test_validation_anytime.py::test_looks_counted_by_label_state`, `tests/unit/test_validation_anytime.py::test_fresh_sample_starts_at_look_1`

#### Scenario: Many looks
- **WHEN** a sample has had 9 looks and is extended and estimated again
- **THEN** look 10 is judged with the same exact bounds, and no line says the looks exceed a plan
Pinned by: `tests/unit/test_validation_anytime.py::test_no_look_limit`

#### Scenario: A band with no relevant paper
- **WHEN** band S3 of `validation` has 120 labelled records of 3000, none relevant, and the other bands have relevant papers
- **THEN** the bounds are given, S3's upper bound counts the relevant papers it may still hide, and the lines add "Band S3 shows no relevant paper in 120 labelled records of 3000; it may still hold up to U relevant papers (exact bound), counting its kept and not-kept records together."
Pinned by: `tests/unit/test_validation_anytime.py::test_zero_band_bounds`

#### Scenario: The bounds per cell
- **WHEN** a cell of N records has t labelled and k relevant, for N in 50, 500 and 5000 and k from 0 to t
- **THEN** its bounds contain k and at most N − t + k, a census cell (t = N) gives exactly k, and the box bound on recall equals a brute-force search over every count inside the cells' bounds
Pinned by: `tests/unit/test_validation_anytime.py::test_cell_bounds_properties`, `tests/unit/test_validation_anytime.py::test_bounds_match_brute_force`

#### Scenario: Coverage when looking after every batch
- **WHEN** for N in 50, 500 and 5000 and true counts 0, 1, 5, N/100 and N/2, 300 seeded runs draw without replacement and look after every 10 draws to the end
- **THEN** the share of runs whose bounds ever exclude the true count is at most 1 − level plus three standard errors
Pinned by: `tests/unit/test_validation_anytime.py::test_cell_bounds_monitored_coverage`

#### Scenario: The false "met" rate under adaptive extension
- **WHEN** seeded synthetic populations with true recall equal to the target, one of low prevalence (0.2%) and one with highly imbalanced bands (500 records at 30%, 15000 at 0.1%), are each sampled 1000 times with 200 records, and while the outcome is "cannot tell" the suggestion is taken and the sample extended, up to 8 looks
- **THEN** the share of runs that ever say "target met" is at most 1 − `ci_level` + 0.015 (marked slow)
Pinned by: `tests/unit/test_validation_anytime.py::test_anytime_false_met_rate`

#### Scenario: What the bounds rest on
- **WHEN** the methods draft or "Recall: method" is written for a probability sample with unlabelled records
- **THEN** it says the bounds are exact confidence sequences for sampling without replacement, valid at every look; that the first draw is systematic and is treated as random within each band; that the labelled records are treated as a random part of the sample; and that the bounds are conservative, naming the band that widens them most
Pinned by: `tests/unit/test_validation_anytime.py::test_method_sentences`

### Requirement: A model-based interval as a supplement only
The system SHALL, where the Korn–Graubard interval is missing, add a model-based interval (a Jeffreys prior per band and screening decision) labelled as an assumption that does not decide the target; the verdict SHALL always come from the exact bounds.
Surfaces: CLI `wider recall`, `wider report`, `wider export methods`; UI Recall page.

#### Scenario: Shown as a supplement
- **WHEN** band S3 shows no relevant paper, so the Korn–Graubard interval is missing
- **THEN** a line reads "Supplementary, model-based (an assumption: a Jeffreys prior for each band and screening decision; it does not decide the target): L to U.", `--json` gives `model_interval` with `assumption`, and `outcome.basis` stays `exact`
- **AND** with a Korn–Graubard interval no model-based line appears and `model_interval` is null
Pinned by: `tests/unit/test_validation_anytime.py::test_model_interval_supplement`, `tests/unit/test_validation_anytime.py::test_model_interval_is_supplementary`

### Requirement: Continue or stop validation, on record
The system SHALL let the researcher record after any look a decision to stop validation, with the look and its outcome, or to continue by an extension; each SHALL be in History with author and time and listed in the report. A stop SHALL stay on record, and an extension after a stop SHALL be said.
Surfaces: CLI `wider validation stop [SAMPLE] [--note TEXT]`, `wider validation extend`; UI Recall page outcome block, "Stop validation here" (with "Note (optional)"), "Add the suggested records".

#### Scenario: Stopping with an open outcome
- **WHEN** the outcome at look 2 on `validation` is "inconclusive" and `wider validation stop validation --note "budget used"` runs (or "Stop validation here" with "budget used" in its "Note (optional)" field, or `POST /api/validation/stop` with `{"sample": "validation", "note": "budget used"}`)
- **THEN** History gets a `validation_stop` entry "validation on validation stopped at look 2: inconclusive (budget used)" with the author, its undo is refused with "a decision to stop validation stays on record", and the report's "Recall: method" lists "Validation stopped on 2026-10-23 at look 2; outcome: inconclusive. Note: budget used."
- **AND** a stop entry recorded before this change keeps its words "cannot tell" in History; the report lists it with "inconclusive"
Pinned by: `tests/unit/test_validation_stop.py::test_stop_recorded`, `tests/unit/test_validation_stop.py::test_stop_cli_matches_api`, `tests/unit/test_validation_stop.py::test_old_stop_entry_said_inconclusive`

#### Scenario: Continuing after a stop
- **WHEN** validation was stopped and `wider validation extend validation --suggested` runs later
- **THEN** the extension is made, and every later recall output says "Validation was continued on 2026-10-25 after it was stopped on 2026-10-23."
Pinned by: `tests/unit/test_validation_stop.py::test_continue_after_stop_said`

#### Scenario: Nothing to stop
- **WHEN** `wider validation stop validation` runs before any look on `validation`
- **THEN** it exits 1 with "no recall result on validation yet; nothing to stop", the route answers 400, and nothing is recorded
Pinned by: `tests/unit/test_validation_stop.py::test_stop_before_look_refused`

### Requirement: Every recall result on a validation sample is recorded
The system SHALL record each recall estimate on a probability sample, from any surface and for the compared set of a comparison, as a look in `validation_looks` (sample, result set key and settings hash, prompt and codebook versions, first and last time, count, author), adding a History entry for each first look at a result set. A look cannot be undone.
Surfaces: CLI `wider recall [--compare N]`, `wider report`; UI Recall and report page, History page.

#### Scenario: First and later looks
- **WHEN** `wider recall main` runs twice on `validation` for set 1, then `GET /api/recall?protocol=main` once, then `wider report`
- **THEN** `validation_looks` holds one row for (validation, set 1) with count 4 and first and last times, History holds one `sample_look` entry "recall on validation seen for main set 1", and undo of it is refused with "a look at the recall on a validation sample stays on record"
- **AND** an estimate on the purposive `development` records no look, and `wider validation status` (or `GET /api/validation/status`) repeats the lines stored with the last estimate without recording a look; after `wider report`, status gives the report's lines
Pinned by: `tests/unit/test_validation_looks.py::test_looks_recorded_once_per_set`, `tests/unit/test_validation_looks.py::test_status_records_no_look`

#### Scenario: A comparison is a look at both sets
- **WHEN** `wider recall main --set 1 --compare 2` runs on `validation` (or the Recall page's compare select)
- **THEN** a look is recorded for set 1 and for set 2; when set 2 needs the choice of "A configuration changed after a validation result was seen", the `compare` block is `{"choice_needed": true, "notice": ...}` without numbers, and set 1's estimate is given as usual
Pinned by: `tests/unit/test_validation_looks.py::test_compare_records_look`

#### Scenario: Several result sets seen on one sample
- **WHEN** sets 1 and 2 of `main`, with different settings, have both been estimated on `validation` (set 2 after the choice "not informed")
- **THEN** every estimate on `validation` and the report add "Recall on validation was seen for 2 result sets with different settings (sets 1, 2); choosing between them on this sample uses it for model selection."
Pinned by: `tests/unit/test_validation_looks.py::test_model_selection_line`

#### Scenario: A project from before this change
- **WHEN** a project made before this change holds validation labels and a stored recall result on `validation`, and is opened after the upgrade
- **THEN** a one-time step records that recall results may have been seen before looks were recorded, and every recall output on `validation` adds "Recall results seen before this version are not recorded; a target set now may come after a result was seen."
- **AND** a project without a stored recall result gets no such line, and opening the project again changes nothing
Pinned by: `tests/unit/test_validation_looks.py::test_migration_marks_earlier_results`, `tests/unit/test_validation_looks.py::test_migration_runs_twice`

### Requirement: A configuration changed after a validation result was seen
The system SHALL, at the first estimate of a result set created after a look on that sample whose settings differ from every set looked at, ask: the sample is used (estimate "not independent", fresh sample advised) or the change was not informed by the result (recorded, disclosed). Without a choice nothing is estimated.
Surfaces: CLI `wider recall`, `wider report [--bundle]`, `wider export methods|package` (`--sample-used|--not-informed`); UI Recall and report page, choice dialog.

#### Scenario: A revised prompt after a look
- **WHEN** set 1 of `main` was estimated on `validation`, then set 2 is made with another prompt version, and `wider recall main --set 2` runs without a choice
- **THEN** it prints "Set 2 of main was made after recall on validation was seen (2026-10-21), and its settings differ (prompt_version). Sample used: this estimate is marked not independent, and a fresh sample is recommended. Not informed: you state the change was not informed by the validation result; every output says so. The choice is final." and "Choose one: --sample-used or --not-informed. Nothing was done.", exits 2, and records no look and no stage result
- **AND** `GET /api/recall?protocol=main&set=2` answers 200 with `choice_needed: true` and the notice; the Recall page shows it with "Sample used", "Not informed" and "Cancel", none pre-selected, the focus on "Cancel"
Pinned by: `tests/unit/test_validation_looks.py::test_changed_set_needs_choice`, `tests/unit/test_recall_page_node.py::test_used_choice_dialog`

#### Scenario: Sample used
- **WHEN** the researcher chooses `--sample-used` (or `choice=used`)
- **THEN** History gets a `sample_used` entry that cannot be undone ("a sample recorded as used stays on record"), and every estimate on `validation` of set 2 or a later set adds "Not independent: the configuration was changed after recall on validation was seen. Draw a fresh sample for an independent estimate." while set 1 keeps its lines
Pinned by: `tests/unit/test_validation_looks.py::test_sample_used_marks_later_sets`

#### Scenario: Not informed
- **WHEN** the researcher chooses `--not-informed` (or `choice=not_informed`)
- **THEN** History gets a `sample_not_informed` entry with the author that cannot be undone, and the estimate, the report and the methods draft add "The researcher (R1) states that set 2 of main was not informed by the recall result on validation seen on 2026-10-21."
Pinned by: `tests/unit/test_validation_looks.py::test_not_informed_disclosed`

#### Scenario: A re-run with the same settings
- **WHEN** set 3 is made after the look with the same settings hash as set 1
- **THEN** no choice is asked and the estimate shows no not-independent line
Pinned by: `tests/unit/test_validation_looks.py::test_same_settings_no_choice`

#### Scenario: Imported set with unknown settings
- **WHEN** after a look, a set imported without a settings hash is estimated on `validation`
- **THEN** the choice is always asked, and the notice says "its settings are unknown (imported)" in place of the list of differing settings
Pinned by: `tests/unit/test_validation_looks.py::test_null_hash_set_asks`

#### Scenario: Report and exports with a pending choice
- **WHEN** set 2 needs a choice and `wider report`, `wider report --bundle`, `wider export methods` or `wider export package` runs without `--sample-used` or `--not-informed`
- **THEN** each writes its files, prints the notice and exits 0 (only `wider recall` exits 2): the recall section holds `choice_needed: true` and the notice in place of the estimate, with the line "Recall on validation for set 2 is not given until you choose: sample used or not informed.", and the bundle (also `POST /api/report/bundle`), `GET /api/outputs/methods/draft` and `POST /api/outputs/package` carry the same; the page's methods draft and package cards show the notice with the choice dialog, which sends `choice=used` or `choice=not_informed`, and nothing raises
Pinned by: `tests/unit/test_validation_looks.py::test_report_with_pending_choice`, `tests/unit/test_outputs_page_node.py::test_pending_choice_on_methods_and_package`

### Requirement: Benchmark papers reported apart from the estimate
The system SHALL report the seed papers (the benchmark papers of `benchmark.csv`) in the corpus beside each recall estimate, per set (Development; Validation only while shown; used Validation apart), with how many screening kept as include or maybe, excluded or left unscreened, the exact interval and "known papers, not a random sample; not pooled into the estimate".
Surfaces: CLI `wider recall`, `wider report`, `wider export methods`; UI Recall page, "Seed papers apart".

#### Scenario: Development and validation benchmark papers
- **WHEN** the corpus holds 12 Development seed papers (10 kept as include, 1 as maybe, 1 excluded) and 8 Validation papers, the Validation set is shown, and `wider recall main` runs
- **THEN** the output, and the Recall page's section "Seed papers apart", have "Development seed papers: 11 of 12 kept (10 include, 1 maybe, 1 excluded), 95% interval L to U. Known papers, not a random sample; not pooled into the estimate." and a Validation line of the same form, and `--json`, the API and `report.json` carry the counts in `benchmark_apart` (the key unchanged)
- **AND** while the Validation set is hidden the Validation line reads "Validation seed papers: hidden until the query is frozen" and no look is recorded
Pinned by: `tests/unit/test_validation_scope.py::test_benchmark_apart_lines`, `tests/unit/test_validation_scope.py::test_benchmark_apart_hidden_set`

#### Scenario: The used sample after a fresh draw
- **WHEN** recall is estimated on `validation2`, drawn fresh from `validation`
- **THEN** the output adds "The 216 records of validation (used) are not in this estimate: 34 relevant, 33 kept by this set. Not independent; not pooled."
Pinned by: `tests/unit/test_validation_scope.py::test_used_sample_reported_apart`

### Requirement: The recall estimate says what it covers
The system SHALL state with each recall estimate that it covers the ranked records except thin records and seed papers (and the records of the samples a fresh sample was drawn apart from, and development records left out), screened on title and abstract only, with the number of records covered.
Surfaces: CLI `wider recall`, `wider report`, `wider export methods`; UI Recall page.

#### Scenario: Scope line
- **WHEN** a ranking of 2000 records holds 40 thin records and 20 seed papers and `wider recall main` runs on `validation`
- **THEN** the lines end with "This estimate covers 1940 ranked records (not the 40 records with too short an abstract, or the 20 seed papers), screened on title and abstract only; it says nothing about full-text screening."
- **AND** the same sentence is on the Recall page, in `report.md` and in the methods draft
Pinned by: `tests/unit/test_validation_scope.py::test_scope_line_everywhere`

### Requirement: Validation checks in the technical report
The system SHALL add to `report.md` "Recall: method" and `report.json` the validation plan used (or none), the target and rule with when set, the method of the bounds, extensions, stop decisions, looks and choices, label counts by criteria state, re-checks and second-reviewer coverage; the methods draft SHALL name the target, the outcome and any not-independent or not-informed statement.
Surfaces: CLI `wider report`, `wider export methods`, `wider export package`; UI Recall and report page.

#### Scenario: A full validation record
- **WHEN** a project used a plan, set the target before any look, extended `validation` once after a look, stopped validation at look 2, chose "not informed" for set 2, and has 10 labels before the freeze, a criteria assessment with scope "affected", 8 re-checked (2 revised), 14 of 14 second-reviewed
- **THEN** `report.md` "Recall: method" lists each of these in that order with dates, `report.json` `validation_checks` holds the same fields, and the methods draft names the target, the outcome and the not-informed statement with its author
- **AND** a project without any of them reads "No validation plan was used.", "No recall target was set before the results." and the label counts only
Pinned by: `tests/unit/test_validation_report.py::test_validation_checks_in_report`, `tests/unit/test_validation_report.py::test_validation_checks_absent`

### Requirement: Reference sets by how the decision was reached
The system SHALL offer route sets for the reference exports, from each decision's stored route: `agreed` (both first passes), `third_read` (the third pass with one of them), `split` (no majority) and `researcher`. A route set holds every decision with that route, whatever the decision; imported, carried, copied, one-pass and older records are in none. A second screen's passes are S1, S2, S3.
Surfaces: CLI `wider export references --which SET`; UI Export records card.

#### Scenario: Sets by how the decision was reached
- **WHEN** protocol `main` has synthetic decisions reached by A and B agreeing (4 exclude, 2 include), by the third read (decided by "A,C" and "B,C"), by reads without a majority ("split"), by the researcher, and by import ("imported from list.csv")
- **THEN** `agreed` lists the 6 records decided by both passes, `third_read` those decided with the third read and one pass, `split` the record left at maybe without a majority, and `researcher` the record the researcher decided; the imported records, and records copied from a reused set, are in no route set
- **AND** each RIS note gives the record's decision, the CSV's `decided_by` column holds the stored route ("A,B", "A,C", "split", "researcher"), and for a second screen `agreed` means S1 and S2, `third_read` S3 with one of them
Pinned by: `tests/unit/test_outputs.py::test_reference_sets_by_route`, `tests/unit/test_record_selection.py::test_older_export_parity`

### Requirement: Every decision set exported at once
The system SHALL write, on one request, every decision set of the chosen result set that holds a record, in the three formats, and per result set an index `<stem>_index.csv` (the files' stem; set, records, files; counts and names only), listing empty sets with 0 records and no files and removing their earlier files.
Surfaces: CLI `wider export references --all [--set N]`; UI **Every set at once** on the Results dashboard's Export records card.

#### Scenario: All sets written
- **WHEN** `wider export references --all` runs on `main` with include, maybe and exclude decisions, none of them split
- **THEN** files are written for `include`, `include+maybe`, `maybe`, `exclude`, `agreed`, `third_read` and `researcher` as far as each holds a record, none for `split`, and `main_index.csv` lists all eight sets with `split` at 0 records and no files
- **AND** **Every set at once** on the card writes the same files and shows the index as a table; the dashboard has no other such button, the Recall page none; and `--all` together with `--which` is refused
Pinned by: `tests/unit/test_outputs.py::test_reference_export_all`, `tests/unit/test_outputs.py::test_references_all_and_which_refused`, `tests/unit/test_results_js_node.py::test_every_set_at_once_on_card_node`

#### Scenario: A set emptied since the last run
- **WHEN** `--all` ran while one record was split, the researcher then answered it, and `--all` runs again
- **THEN** the `main_split` files of the first run are gone and `main_index.csv` lists `split` with 0 records and no files
- **AND** `--all --set 2` then writes `main.s2_index.csv` and set 2's files, and leaves `main_index.csv` and set 1's files as they were
Pinned by: `tests/unit/test_outputs.py::test_reference_export_all`

### Requirement: PRISMA 2020 checklist
The system SHALL write `exports/prisma_2020_checklist.md` and `.json` with the 27 items and sub-items of PRISMA 2020 (Page et al. 2021) for a protocol and set: 5, 6, 7, 8, 16a, 24b and 27 filled from the store by a fixed map, all else "not recorded by WIDER". No model writes it. The replication package carries it.
Surfaces: CLI `wider export prisma-checklist [--protocol] [--set N]`, `wider export package`; UI PRISMA reporting (PRISMA 2020 tab); Recall, "Checklist".

#### Scenario: Items filled and not recorded
- **WHEN** a synthetic project has imported two platforms, screened under `main` with passes A, B and the judge, labelled a probability sample `validation`, and built a replication package
- **THEN** item 5 gives the research question and the codebook version the protocol used, item 6 lists both platforms with their import dates, item 7 points to the search strings and `prisma_s.md`, item 8 names each pass's model, the third read, the majority rule, the researcher's review of records left without a majority and the validation sample with its size, and item 16a gives the counts of `prisma_2020.json` for the set
- **AND** items 24a and 24c read "not recorded by WIDER", 24b reads "not recorded by WIDER" and "WIDER screening settings (not a review protocol): hash" with 12 characters, item 27 names the package file and its manifest, and items 11 and 16b read "not recorded by WIDER"
- **AND** "Checklist" on the Recall page's PRISMA report card writes the same two files as the CLI
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_items_filled_and_not_recorded`, `tests/unit/test_prisma_2020_checklist.py::test_cli_and_route_write_same_files`, `tests/unit/test_outputs.py::test_checklist_card_and_write_every_set_node`

#### Scenario: Checklist in the replication package
- **WHEN** the replication package is built
- **THEN** the zip holds `prisma_2020_checklist.md` and `prisma_2020_checklist.json`, rebuilt from the store at that moment, with their SHA-256 in `MANIFEST.txt`, and still no abstract
- **AND** item 27 of the packaged copy reads "this replication package (MANIFEST.txt, manifest.json)" while `exports/prisma_2020_checklist.md` keeps its own item 27, and a package built before any import still builds, without the checklist
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_checklist_in_package`

#### Scenario: Nothing imported
- **WHEN** the checklist is requested before any import
- **THEN** the system refuses with a message starting "nothing imported yet", and no file is written
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_nothing_imported`

### Requirement: Screening agreement and researcher interventions in the report
The system SHALL add to `report.md` and `report.json` the sections "Screening agreement" (the dashboard's agreement for its set) and "Researcher interventions": per History kind the count, authors, first and last date, then each entry's date, author and summary (home paths as `~`; authors per attribution, else "no author recorded"), undone ones marked; labels, adjudications, notes, tags, per-record kinds and their undos only counted.
Surfaces: CLI `wider report`; UI Recall page, "Build report".

#### Scenario: Agreement and interventions in the report
- **WHEN** the synthetic project of "Agreement on a synthetic set" has one answered split question and one answered enrichment question by `rev1`, one adjudication by `rev2`, one undone configuration change whose value is a path in the home folder, and 12 labels, and the report is built
- **THEN** `report.md` has the section "Screening agreement" with "6 of 10 (60%)" and kappa "0.34" and the routes, and the section "Researcher interventions" listing the answers and the configuration change (marked "undone", its path written from `~`) with dates and authors
- **AND** a second configuration change that was undone and then redone is listed without "undone"
- **AND** labels and the adjudication appear only as the rows "label: 12 entries" and "adjudication: 1 entry" with their authors and dates, no record id or label decision of theirs appears, `report.json` holds both sections under `agreement` and `interventions`, and the share bundle is built without a finding
Pinned by: `tests/unit/test_recall.py::test_report_agreement_and_interventions`

#### Scenario: Nothing to report
- **WHEN** History holds only label and note entries
- **THEN** the section "Researcher interventions" reads "No researcher intervention recorded."
Pinned by: `tests/unit/test_recall.py::test_report_no_interventions`

#### Scenario: Interventions under each mode
- **WHEN** the interventions hold a change by declared "rev1", one by inferred "login-x" and one unnamed, and the report is built in each mode
- **THEN** the authors read "Researcher 1", "Researcher 2" and "no author recorded" in mode `pseudonyms`; "rev1", "Researcher 2" and "no author recorded" in mode `names`; "not shown" and "no author recorded" in mode `none`
- **AND** dates, kinds, counts and summaries are the same in all three, apart from the names in summaries
Pinned by: `tests/unit/test_attribution_export.py::test_interventions_under_each_mode`

### Requirement: Relevant-found series stored with the recall estimate
The system SHALL store, whenever the lines of a recall estimate on a probability sample are stored, the primary variant's relevant-found series for that set: per band end the weighted relevant and kept at or above it; per band its ranks, records, sampled, labelled, relevant observed and summed exact bounds; a checksum of the sample's labels and the set's decisions. No title, abstract, record id or record rank.
Surfaces: CLI `wider recall`, `wider report`; UI Recall page, Results dashboard.

#### Scenario: Series stored and matching the estimate
- **WHEN** `wider recall main` runs on the synthetic probability sample `validation`
- **THEN** a series is stored for (validation, main set 1) with one point per band end of the sample's design and no other rank, whose last point's kept divided by relevant equals the primary estimate
- **AND** an estimate on the purposive `development` stores no series, and the stored series holds no title, abstract, record id or rank of a sampled record
Pinned by: `tests/unit/test_recall_curve.py::test_series_stored_and_matches_estimate`

#### Scenario: Checksum follows labels and decisions
- **WHEN** after the estimate a sampled record's final label changes, or set 1's decision on a sampled record changes
- **THEN** the checksum computed now differs from the stored one, and it is equal again once the change is undone
- **AND** a changed decision on a record outside the sample leaves it equal
Pinned by: `tests/unit/test_recall_curve.py::test_checksum_follows_labels_and_decisions`

#### Scenario: Band details and bounds stored
- **WHEN** `wider recall main` runs on a synthetic sample with bands of 50, 150 and 200 records (ranks 1-50, 51-200, 201-400), 10 sampled from each, all labelled
- **THEN** each band's entry holds its first and last rank, its records, 10 sampled, 10 labelled, the relevant observed among them, its weighted relevant and kept, and the cumulative relevant, kept and missed with a low and high bound each
- **AND** each cumulative bound equals the sum of the estimate's stored cell bounds of the bands up to that end (both cells for relevant, the kept cells for kept, the not-kept cells for missed), and the entries hold no record id
- **AND** a band counts as fully labelled when its sampled records are labelled, however many of its other records are not (150 records, 10 sampled, 10 labelled: not flagged)
- **AND** when the series' labels differ from those the bounds were computed from (a primary labeller other than the final labels), the entries hold the counts and estimates and no bounds
Pinned by: `tests/unit/test_recall_curve.py::test_band_details_and_bounds_stored`

### Requirement: Record export by preset and filters
The system SHALL export the set's records matching a preset (`include`, `include+maybe` default, `maybe`, `exclude`, `unresolved`, `every`), any given pathways and first-two-read patterns, optionally final only and optionally chosen ids, to `exports/references/` named `<stem>_<preset>[_by_<paths>][_reads_<patterns>][_final][_picked]`, same ids and metadata per format. Exporting changes no decision.
Surfaces: CLI `wider export selection [options]`; UI Results dashboard, Export records card.

#### Scenario: One-click default
- **WHEN** synthetic protocol `main` has 3 include, 2 maybe and 5 exclude decisions and the researcher presses **Export** on the Export records card without changing anything (or runs `wider export selection`)
- **THEN** one file `main_include_and_maybe.ris` is written with the 5 include and maybe records, each with its title, authors, year, journal, DOI, URL, abstract and the note "WIDER main: <decision> (record n)", and no BibTeX, CSV or ZIP file is written; one stage result is recorded for the export
- **AND** the file is the same, byte for byte, as the RIS file of `wider export references --which include+maybe`, which shares its name
- **AND** the card's preset select offers Include, Include + Maybe (selected), Maybe only, Exclude, Unresolved only and Every decision, and the filters sit under a closed line "Filter by how the decision was reached"
- **AND** the decisions, sets and questions in the store are the same after the export as before
Pinned by: `tests/unit/test_record_selection.py::test_default_include_and_maybe_ris`, `tests/unit/test_record_selection.py::test_export_changes_no_decision`, `tests/unit/test_outputs.py::test_export_records_card_defaults_node`

#### Scenario: Preset and pathway combined
- **WHEN** of the 5 exclude decisions 4 were reached by both passes and 1 by the researcher, and of the 3 include decisions 1 by the third read, and the researcher exports preset Exclude with pathway "decided by a researcher", then preset Include with pathway "resolved by the third model"
- **THEN** the first writes `main_exclude_by_researcher.ris` with the 1 record and the second `main_include_by_third_read.ris` with the 1 record
- **AND** ticking two pathways (`--pathway agreed,third_read`) selects records reached by either, and the file is named `main_include_by_agreed_third_read.ris`, whatever order the pathways are given in
Pinned by: `tests/unit/test_record_selection.py::test_preset_and_pathway_combined`

#### Scenario: No pathway ticked holds every route
- **WHEN** the include decisions also hold 1 imported and 1 carried from a reused set, and no pathway is ticked
- **THEN** the selection holds them too, and the count line names them as "2 by another route (imported, carried, one pass or older)"
- **AND** once any pathway is ticked, the imported and carried records are left out, except carried or imported unresolved records under "unresolved disagreement"
- **AND** on the card, under the count line, the same counts per route and per pattern of the first two reads stand as `--dry-run` prints them
Pinned by: `tests/unit/test_record_selection.py::test_no_pathway_holds_every_route`, `tests/unit/test_outputs.py::test_count_breakdown_names_other_route_node`

#### Scenario: Disagreement pattern of the first two reads
- **WHEN** of the 2 maybe and 5 exclude decisions, one record's first two reads were include and exclude, one include and maybe, one maybe and exclude, and the rest agreed, and the researcher picks preset Every decision with the pattern "include / exclude" (`--preset every --pattern include_exclude`)
- **THEN** `main_every_reads_include_exclude.ris` holds the 1 record, whatever its final decision
- **AND** ticking two patterns selects records with either; "both reads the same" (`agreed`) selects the records whose first two reads matched; an imported record with no reads is left out once a pattern is ticked; a pattern and a pathway together select records matching both
Pinned by: `tests/unit/test_record_selection.py::test_pattern_of_first_two_reads`

#### Scenario: Unresolved only, or unresolved left out
- **WHEN** the 5 include and maybe records hold 1 split record and 1 carried from a set where it was unresolved
- **THEN** preset Unresolved only writes `main_unresolved.ris` with those 2, each marked unresolved
- **AND** with preset Include + Maybe and "Leave out unresolved records" ticked (`--final-only`), `main_include_and_maybe_final.ris` holds the 3 other records, imported and carried final records stay in, and the count line reads "3 records (2 unresolved left out) · RIS"
- **AND** the pathway "unresolved disagreement" selects both unresolved records, so its count equals the count line's unresolved number
Pinned by: `tests/unit/test_record_selection.py::test_unresolved_only_and_final_only`, `tests/unit/test_outputs.py::test_final_only_box_node`

#### Scenario: Records chosen one by one
- **WHEN** the researcher opens "Choose records one by one", which lists the selection's 5 records (title, year, decision, status, pathway) all ticked, and unticks 2 of them (or runs `--records <id>,<id>,<id>` with the other 3)
- **THEN** `main_include_and_maybe_picked.ris` holds the 3 ticked records and the count line reads "3 of 5 records chosen · RIS"
- **AND** changing the preset or a filter lists the new selection with every box ticked again, and an id outside the selection is refused, named, with no file written
- **AND** with no record ticked, **Export** is disabled with "No record is ticked; tick at least one to export.", and when the selection is in the package **Download export package** is disabled too and nothing is written
Pinned by: `tests/unit/test_record_selection.py::test_records_chosen_one_by_one`, `tests/unit/test_outputs.py::test_choose_records_node`, `tests/unit/test_outputs.py::test_package_none_ticked_node`

#### Scenario: Empty selection and bad choices
- **WHEN** preset Include with pathway "unresolved disagreement" matches no record
- **THEN** no file is written, and the CLI and the card say "No records match Include, unresolved disagreement; nothing was written."
- **AND** when an earlier file of that name exists it is left as it was and the message adds "The earlier file main_include_by_split.ris (<time>) no longer matches this selection."
- **AND** `--preset all`, `--pathway import`, `--pattern include_include` and `--format docx` are refused, naming the allowed values, with exit 1 and no file written
- **AND** `--audit` with `--preset`, `--pathway`, `--pattern`, `--final-only`, `--records`, `--format` or `--no-abstracts` is refused the same way, since it covers the whole set
Pinned by: `tests/unit/test_record_selection.py::test_empty_selection_and_bad_choices`

### Requirement: Count, formats and abstracts before export
The system SHALL give, for a selection and without writing, the number of matching records, how many are unresolved, the count per pathway and pattern, the formats and the file name; the card shows it as its choices change, and the export writes that number. RIS is the default; BibTeX and CSV are optional; several formats are one ZIP of only those files. Abstracts are in unless unticked.
Surfaces: CLI `wider export selection --dry-run`; UI Export records card, count line.

#### Scenario: Count before export
- **WHEN** the 5 include and maybe records hold 1 unresolved and `wider export selection --dry-run` runs
- **THEN** it prints "5 records (of which 1 unresolved: no majority) · RIS", the counts per pathway and pattern, and the file name it would write, writes no file and adds no stage result
- **AND** the card's count line reads the same text before **Export** is pressed and changes when the preset, a filter, a ticked record or a format box changes, and after **Export** the file holds 5 records
Pinned by: `tests/unit/test_record_selection.py::test_dry_run_counts_and_writes_nothing`, `tests/unit/test_outputs.py::test_count_line_follows_choices_node`

#### Scenario: Several formats in one ZIP
- **WHEN** the researcher ticks RIS, BibTeX and CSV (or runs `--format ris,bib,csv`)
- **THEN** one file `main_include_and_maybe.zip` is written holding `main_include_and_maybe.ris`, `.bib` and `.csv`, and no loose copy of them
- **AND** the three files hold the same record ids and the same title, authors, year and DOI per record, and the CSV has the columns record_id, decision, decided_by, title, authors, year, journal, doi, url, abstract, protocol, result_set, status, pathway (the older export's columns first, in their order)
- **AND** with BibTeX alone one `.bib` file is written and no ZIP, and with no format ticked the card's Export button is disabled and the CLI refuses `--format ""`
Pinned by: `tests/unit/test_record_selection.py::test_formats_and_zip`

#### Scenario: Abstracts left out
- **WHEN** the researcher unticks "Include abstracts" (ticked by default; `--no-abstracts`) and exports in all three formats
- **THEN** the RIS has no `AB` line, the BibTeX no `abstract` field and the CSV an empty `abstract` column, and every other field is as with abstracts
- **AND** the box's sentence, in normal text beside it, reads "Abstracts are the publishers' text; leave them out if you pass these files to someone else."
Pinned by: `tests/unit/test_record_selection.py::test_no_abstracts`, `tests/unit/test_outputs.py::test_abstracts_box_node`

### Requirement: Audit CSV of the screened set
The system SHALL write `exports/references/<stem>_audit.csv` with one row per record in the result set's scope: final decision, status (`final`, `unresolved`, `not screened`), pathway, the first-two-read pattern, each pass's read and model, the researcher's decision and note (no name), origin, the set's rules and settings hash. No abstract; not in the replication package.
Surfaces: CLI `wider export selection --audit [--set N]`; UI **Audit CSV** on the Export records card.

#### Scenario: Audit rows
- **WHEN** synthetic set 1 of `main` has 13 records in scope: 10 decided (one by the researcher after three disagreeing reads, one unresolved), 2 whose calls failed and 1 with no call yet, and `wider export selection --audit` runs
- **THEN** the file has 13 rows with the columns record_id, title, year, doi, final_decision, status, screen_call, pathway, pathway_label, pattern, decided_by, pass_1_read, pass_1_model, pass_2_read, pass_2_model, pass_3_read, pass_3_model, researcher_decision, researcher_note, origin, protocol, result_set, majority, third_read, settings_hash
- **AND** the researcher's row has its three reads, pathway `researcher`, its pattern and the researcher's note; the unresolved row has status `unresolved` and final decision `maybe`; the 2 failed rows and the uncalled row have status `not screened`, an empty decision and `screen_call` `failed` or `none`
- **AND** a pass read the researcher supplied for a failed call shows model `researcher`
- **AND** a second screen's passes S1, S2, S3 fill the columns pass_1 to pass_3, and the file holds no abstract
Pinned by: `tests/unit/test_record_selection.py::test_audit_rows`

#### Scenario: No researcher name in the audit or package
- **WHEN** the login name `synth_user` (from `USER`) is recorded as the researcher who answered the split (note "methods unclear"), as the `by` of a read supplied for a failed pass, in the decision's reason and as the set's creator
- **THEN** neither the audit CSV nor any file of the export package (`summary.md` included) holds `synth_user`; the note column reads "methods unclear"
- **AND** the RIS, BibTeX and CSV selection files hold no researcher name either
Pinned by: `tests/unit/test_record_selection.py::test_no_researcher_name`

#### Scenario: Not duplicated in the replication package
- **WHEN** the audit CSV was written and `wider export package` runs
- **THEN** the replication package holds no audit CSV, no `.ris` file and no abstract, as before
Pinned by: `tests/unit/test_record_selection.py::test_audit_not_in_replication_package`

### Requirement: Export package
The system SHALL write, on request, `exports/references/<stem>_export_package_<timestamp>.zip` holding by default the Include + Maybe RIS (unresolved marked), the audit CSV, a `summary.md` (counts, models, rules, set, date) and a `MANIFEST.txt` (SHA-256 per file); on request also Include, Maybe, Exclude, Unresolved only or the current selection, in the chosen formats.
Surfaces: CLI `wider export selection --package [--add S,...]`; UI **Download export package**.

#### Scenario: Default package
- **WHEN** `wider export selection --package` runs on synthetic `main` set 1 with 3 include, 2 maybe (1 unresolved) and 5 exclude
- **THEN** the ZIP holds `main_include_and_maybe.ris` (5 records, 1 marked unresolved), `main_audit.csv`, `summary.md` and `MANIFEST.txt` whose hashes match the files, and no other file
- **AND** `summary.md` gives include 3, maybe 2 (of which 1 unresolved), exclude 5, the count per pathway, each pass's model, the majority rule and the set, and says whether the package carries abstracts and that it stays on this computer
- **AND** before writing, the card lists the files and each one's count, and `--package --dry-run` prints the same and writes nothing
Pinned by: `tests/unit/test_record_selection.py::test_default_package`, `tests/unit/test_outputs.py::test_package_contents_listed_node`

#### Scenario: Customized package
- **WHEN** the researcher opens "Customize package", ticks Include only and Exclude only and the formats RIS and CSV (or runs `--package --add include,exclude --format ris,csv`)
- **THEN** the ZIP adds `main_include.ris` (3), `main_include.csv`, `main_exclude.ris` (5) and `main_exclude.csv`, and the Include + Maybe file in both formats, besides the audit, summary and manifest
- **AND** "the current selection" (`--add selection`) adds the card's selection file with its filters and chosen records; `--package` with a filter or `--records` but without `--add selection` is refused, naming it
- **AND** an earlier package is kept, since each has its own timestamp
- **AND** a part that holds no record is listed as "no records, so no file is written" and puts no empty file in the ZIP
Pinned by: `tests/unit/test_record_selection.py::test_customized_package`, `tests/unit/test_record_selection.py::test_package_empty_part_writes_no_file`

### Requirement: Blinded review in the report, the methods draft and the bundle
With a review package, the system SHALL add to the report and methods draft a section "Blinded independent review" giving per package the reviewer, mode, sample kind, items, criteria version, dates, earlier-labels choice and use of the labels, and that a repeat review does not restore independence; the bundle adds `review_packages.json` and `labels.csv` columns on how labels were made. One reviewer is said once.
Surfaces: CLI `wider report [--bundle]`, `wider export methods`; UI Recall, Outputs.

#### Scenario: Section, JSON and columns after a blinded review
- **WHEN** R3's package P (second, validation, 28 items, codebook version 3, made 2 and imported 9 October) and R2's package Q (independent, development, earlier labels re-reviewed, 12 records) were imported, and `wider report`, `wider report --bundle` and `wider export methods` run
- **THEN** the report and the methods draft hold the section with "R3: second reviewer on 28 validation records (criteria version 3), blind in a review package, 2 to 9 October 2026; labels used for agreement and final labels", the line for Q with "R2 labelled 12 records again; a repeat review does not restore independence when the reviewer has seen earlier judgments", and the limits sentence
- **AND** the bundle holds `review_packages.json` with the same facts and no item token, and `labels.csv` has `made_in` (the package id, `blind queue`, `unblinded queue`, `file import`, `sample import` or `wider`), `blinded` (`yes` for a first label in a package, `repeat` for a re-reviewed one, `queue` for a label made in a blind queue in WIDER, `imported` for a file or sample import, else `no`), and the report's "Validation labels:" line counts imported labels apart, `left_out` (yes or no) after its present columns, and no `tags` column
- **AND** agreement on blinded labels is given with and without the repeats, and labels from the blind queue are counted apart as "blind queue in WIDER (History visible)"
Pinned by: `tests/unit/test_package_outputs.py::test_report_methods_bundle_section`, `tests/unit/test_package_outputs.py::test_labels_csv_columns`

#### Scenario: Different abstracts documented
- **WHEN** R1 labelled 3 validation records on the imported abstract and R3 on their enriched abstract, and the report is made
- **THEN** the section says once "3 records were labelled by R1 and R3 on different abstract versions" and the bundle's `labels.csv` has the column `abstract_version`
Pinned by: `tests/unit/test_package_outputs.py::test_abstract_differences_reported`

#### Scenario: Never used
- **WHEN** a project has no review package and the report, methods draft and bundle are made
- **THEN** none has the section, the bundle has no `review_packages.json`, and `labels.csv` still gains the columns, with each label's `made_in`, its `blinded` value and `no`
Pinned by: `tests/unit/test_package_outputs.py::test_no_section_without_package`

#### Scenario: One reviewer said once
- **WHEN** only R1 labelled the samples of a project, and the report and methods draft are made
- **THEN** each says once "No independent second review was done; one reviewer labelled the samples." and nothing else about blinded review; a project with a second reviewer does not say it
Pinned by: `tests/unit/test_package_outputs.py::test_one_reviewer_said_once`

### Requirement: Recall while a review package is out
While a review package on a validation sample is open (not withdrawn, an item still undecided), the system SHALL not reveal recall on that sample, and its refusal (see "Recall revealed only on a complete validation sample") SHALL say once, in normal-size black text, on the Recall page, in `wider recall` and in the report, "R3's review package on validation is not finished", until every item is decided or the package is withdrawn.
Surfaces: CLI `wider recall`, `wider report`; UI Recall page.

#### Scenario: Recall before and after the import
- **WHEN** R3's package P on `validation` is not imported, and the owner runs `wider recall` and `wider report` and opens the Recall page; then P is imported with 10 of its 20 items decided
- **THEN** before the import, and after it while P still has undecided items, each refuses with the sentence once and records no look; once P is withdrawn or its last items are imported the sentence is gone, and while R1 and R3 differ on records nobody adjudicated (records not yet sealed) the refusal says how many, until they are resolved
- **AND** a withdrawn package and a development package never bring the sentence
Pinned by: `tests/unit/test_package_outputs.py::test_recall_says_package_out_once`, `tests/unit/test_recall_page_node.py::test_package_out_line_once`

### Requirement: The cost of conservative bounds is said
The system SHALL say once in each output that describes the exact bounds valid at every look, in normal-size black text, that the bounds are conservative and may need substantially more labels than a sharper method to show the same result. It SHALL give no number for that cost until one has been measured.
Surfaces: CLI `wider recall`, `wider validation status`, `wider report`, `wider export methods`; UI Recall page "Validation checks", Methods card "Draft from the project".

#### Scenario: The sentence
- **WHEN** recall is estimated on a probability sample with labels, with any outcome, and the report and the methods draft are written
- **THEN** the lines of `wider recall`, `--json`, `GET /api/recall`, `wider validation status`, the report's "Recall: method" and the methods draft each hold once "The bounds are conservative: the error is split over every band and screening decision and each is bounded on its own, so they may need substantially more labels than a sharper method to show the same result."
- **AND** the Recall page draws it as an ordinary paragraph in "Validation checks" (not small, not grey, not the warning colour), and the sentence holds no number
Pinned by: `tests/unit/test_validation_report.py::test_conservative_cost_said`, `tests/unit/test_recall_page_node.py::test_conservative_line_black`

### Requirement: A warning when settling the target may be impractical
The system SHALL, when the labels suggested to settle the target exceed the researcher's practical limit, say so in black after the suggestion, with the number, its range, the limit and that stopping inconclusive is allowed. It SHALL refuse no step for it, and estimate from the labels only.
Surfaces: CLI `wider recall`, `wider validation status`, `wider results`, `wider report`, `wider protocol set recall.practical_labels N`; UI Recall page, Results dashboard, Settings protocol table.

#### Scenario: Above the limit
- **WHEN** the outcome for the target 0.90 is inconclusive, every sampled record is labelled, `recall.practical_labels` is its default 500, and the target part of the suggestion is (as an example fed to the warning) about 2,400 more labels, likely between 1,500 and 4,000, with 6,000 records left in the sampled bands
- **THEN** after the suggestion the lines say "Settling whether the target 0.90 is met may be impractical: about 2,400 more labels (likely between 1,500 and 4,000) are expected before the bounds say met or not met, more than the 500 you set as practical (recall.practical_labels). You may stop here and report the result as inconclusive; nothing requires more labels."
- **AND** `--json` and the API give `more_validation.impractical` with `m` 2400, `range` [1500, 4000], `limit` 500 and `share_left` 0.40; the Recall page draws the line in black in "Validation checks", and "Stop validation here" and "Add the suggested records" are both offered, neither pre-selected
- **AND** with an approximate suggestion (time limit reached) the warning ends with the same "(approximate: ...)" note as the suggestion
Pinned by: `tests/unit/test_validation_suggest.py::test_impractical_warning`, `tests/unit/test_recall_page_node.py::test_impractical_line_black`

#### Scenario: A large share of the records left
- **WHEN** the suggestion is about 2,400 more labels with 4,000 records left in the sampled bands, above the limit
- **THEN** the warning is followed by "That is 60% of the 4,000 records left."; with 6,000 records left (40%) no share line is given
Pinned by: `tests/unit/test_validation_suggest.py::test_impractical_share_line`

#### Scenario: Within the limit, and a limit of one's own
- **WHEN** the suggestion is about 600 more labels with the default limit, and then `wider protocol set recall.practical_labels 500` runs (or the Settings protocol table's row `recall.practical_labels` is set to 500)
- **THEN** with the default no warning is given and `more_validation.impractical` is null; after the change the next estimate gives the warning with "more than the 500 you set as practical", also when no label changed (the warning is worked out at each estimate from the stored suggestion and the current limit); a value below 1 is refused like any invalid protocol value and nothing changes
- **AND** with no target, or with only the precision part of the suggestion, no warning is given
Pinned by: `tests/unit/test_validation_suggest.py::test_impractical_limit_set`

#### Scenario: From the labels only
- **WHEN** the same labels are estimated twice, once with the LLM screening scores as stored and once with every screening score and model vote changed (ranks kept, since they define the bands)
- **THEN** the suggestion and the warning give the same numbers both times; no screening score, rank or model probability stands in for a label
Pinned by: `tests/unit/test_validation_suggest.py::test_impractical_uses_labels_only`

#### Scenario: Nothing is held back
- **WHEN** the warning is given
- **THEN** `wider validation extend validation --suggested` still adds the suggested records, `wider validation stop validation` records the stop, and `wider report`, `wider export methods` and `wider export package` exit 0
Pinned by: `tests/unit/test_validation_suggest.py::test_impractical_never_blocks`

### Requirement: Reporting an open result
The system SHALL, when the last outcome on the validation sample is inconclusive, add to the report's "Recall: method" and the methods draft one sentence with the look, the exact bounds, whether validation was stopped, and that the target was neither shown met nor missed. No output, export or step SHALL be refused because the outcome is inconclusive or not met.
Surfaces: CLI `wider report`, `wider export methods`, `wider validation stop`; UI report page, Methods card "Draft from the project".

#### Scenario: Stopped with an inconclusive result
- **WHEN** the outcome at look 2 for the target 0.90 is inconclusive with exact bounds 0.84 to 0.99 at 95%, validation is stopped, and the report and the methods draft are written
- **THEN** each holds once "Validation ended at look 2 with an inconclusive result: recall lies between 0.84 and 0.99 (exact bounds valid at every look, 95%); the target 0.90 was neither shown met nor shown missed."
Pinned by: `tests/unit/test_validation_report.py::test_open_result_sentence`

#### Scenario: Not stopped
- **WHEN** the same outcome is the last one and validation was not stopped
- **THEN** the sentence reads "At look 2 the result is inconclusive: recall lies between 0.84 and 0.99 (exact bounds valid at every look, 95%); the target 0.90 has been neither shown met nor shown missed."
- **AND** with the outcome met or not met, neither sentence is given
Pinned by: `tests/unit/test_validation_report.py::test_open_result_sentence`

#### Scenario: Continued after the stop
- **WHEN** validation was stopped with an inconclusive outcome and then continued by an extension of the sample
- **THEN** the sentence reads "At look 2 the result is inconclusive: ..." and no longer "Validation ended at look 2 ...", next to "Validation was continued on DATE after it was stopped on DATE."
Pinned by: `tests/unit/test_validation_report.py::test_open_result_after_continued_stop`

#### Scenario: Nothing refused for an open or missed target
- **WHEN** the last outcome is inconclusive, and in a second synthetic project not met
- **THEN** `wider report`, `wider export methods`, `wider export references`, `wider export prisma` and `wider export package` each exit 0, and the matching routes answer 200
Pinned by: `tests/unit/test_validation_report.py::test_open_result_refuses_nothing`

### Requirement: A result set without settings counts as its own setting
The model-selection line of a validation sample SHALL count only looks seen, and a look at a result set without a settings hash (an imported set) as its own setting, as the choice of "A configuration changed after a validation result was seen" does, so that looks at two such sets give the line; it then says "different or unknown settings".
Surfaces: CLI `wider recall`, `wider report`, `wider export methods`; UI Recall and report page.

#### Scenario: Two imported sets seen on one sample
- **WHEN** sets 2 and 3 of `main` are two imports of a synthetic file, set 2 is estimated on `validation`, then set 3 (after the choice "not informed")
- **THEN** every estimate on `validation`, the report and the methods draft add "Recall on validation was seen for 2 result sets with different or unknown settings (sets 2, 3); choosing between them on this sample uses it for model selection."
- **AND** `needs_choice` asked for set 3 before its choice, so the two checks agree that the sets differ, and the choice alone, before set 3's estimate, gives no line
Pinned by: `tests/unit/test_validation_looks.py::test_model_selection_counts_imported_sets`

### Requirement: Recall revealed only on a complete validation sample
The system SHALL reveal recall on a probability sample, with everything a look computes (outcome, bounds, suggestion, stored lines, relevant-found series), only when the sample is complete (sampling-and-labelling, "Completeness of a sample's labels"); otherwise it SHALL refuse before recording anything, saying what is left. Results stored at an earlier look stay shown.
Surfaces: CLI `wider recall`, `wider report`; UI Recall page, report card.

#### Scenario: Refused until complete
- **WHEN** `validation` has 20 records, 3 unlabelled and one with an open disagreement, and `wider recall main`, `GET /api/recall?protocol=main` and the Recall page's "Estimate recall" are used
- **THEN** the CLI prints "Recall on validation is revealed once every sampled record has a final label. Not yet: 3 of 20 records have no label; 1 record has labels that disagree and is not adjudicated. Nothing was estimated or recorded." and exits 1; the API answers 200 with `labels_incomplete` true, that `notice` and the `completeness` dict; the page shows the notice in its message line, in normal black text, and no tables
- **AND** no look, choice, stage result, stored line, series or seal is recorded, and `wider validation status` repeats what was stored before
- **AND** once the 3 are labelled and the record adjudicated, the same command gives the estimate as look 1
- **AND** when instead 20 labels by R1 were saved before provenance was recorded, the record-count part stays and is followed by the part "20 labels by R1 have no stated independence: declare it on the sample card (Declare labels independent) or with wider label provenance validation --reviewer R1 --independent BASIS", the same in the CLI, `GET /api/recall` and the Recall page; a label made in an `unblinded queue` gets no declaration hint
Pinned by: `tests/unit/test_label_completion.py::test_recall_refused_until_complete`, `tests/unit/test_label_completion.py::test_recall_refusal_names_both_declaration_paths`, `tests/unit/test_label_completion.py::test_recall_refusal_records_nothing`, `tests/unit/test_recall_page_node.py::test_labels_incomplete_notice`

#### Scenario: The report while incomplete
- **WHEN** the sample above is incomplete and `wider report` runs, with and without `--protocol main --sample validation`
- **THEN** the report and the bundle are written, their recall section holds the same notice in place of the estimate, and the methods draft gives no recall figure
Pinned by: `tests/unit/test_label_completion.py::test_recall_refusal_cli_api_report`

### Requirement: Sealed validation labels and flagged estimates
The system SHALL seal for good, at each recorded look on a probability sample, the records it used; older projects count the labels their earlier looks used as sealed. Each keeps its final label then as a reference that scores it; only a documented correction changes it. Corrected references SHALL be counted in the outputs.
Surfaces: CLI `wider recall`, `wider validation status`, `wider report`, `wider export methods`, `wider validation plan`; UI Recall page.

#### Scenario: A look seals its records
- **WHEN** every record of `validation` (20) has a final label and `wider recall main` runs
- **THEN** `label_seals` holds 20 rows for `validation` with look 1 and the time; a later unlock and an undo of any change leave them in place
- **AND** `wider validation plan` on the same project records no look and writes no seal; an expected recall the researcher gives wins first; without one it gives the primary recall stored with look 1 while the labels are unchanged, and on a complete sample never estimated it gives "the target plus 0.05", never a new estimate; a stored look from before this change, which holds no recall, gives the target plus 0.05, else 0.90
Pinned by: `tests/unit/test_label_completion.py::test_look_seals_its_records`, `tests/unit/test_label_completion.py::test_expected_recall_from_the_stored_look`

#### Scenario: A correction after the reveal
- **WHEN** after that look R1, whose label on record 7 is its reference, changes it from include to exclude, first without a reason and then with "data entry slip"
- **THEN** the first is refused as a correction without a reason, the second is saved and recorded with `sealed` 1, the reference follows it (exclude, corrected by R1), an unlock and an undo of other changes keep the seals, and `wider recall main`, `wider validation status`, `report.md` and the methods draft each say once "1 record of validation had its labels changed after its recall was first revealed on <date>; results revealed before then used the earlier labels (wider label revisions validation)."
- **AND** the line names no record, decision or reason; `wider validation status` before the next estimate adds "This result is from <date>; 1 reference label was corrected since. Estimate recall again."
Pinned by: `tests/unit/test_label_completion.py::test_sealed_change_needs_reason_and_is_flagged`, `tests/unit/test_label_completion.py::test_sealed_flag_in_status_report_methods`

#### Scenario: A later second reviewer
- **WHEN** after the look R2, who had no label on record 7 of `validation`, labels it exclude against R1's include, which is the reference
- **THEN** the supplementary label is saved without a reason, with `after_look` 1 and its reviewer and time kept; the record's final label stays include, `sealed` true, with no open disagreement; no change line appears, and a second estimate gives the same recall as the first
- **AND** `wider label adjudicate validation 7 exclude --by dana` without `--correction-reason` exits 1 asking for a reason; with one it stores the adjudication, the reference becomes exclude with dana and the time, the change line counts record 7, and the next estimate is a new look
Pinned by: `tests/unit/test_label_completion.py::test_second_reviewer_label_on_sealed_record_is_an_addition`

#### Scenario: A package re-review on a sealed record
- **WHEN** after the look R1's package with `--earlier re-review` brings back record 7, whose reference is R1's include, and its import gives exclude
- **THEN** R1's label becomes exclude with a `package re-review` revision row and a History entry; the reference stays include, the record stays final with no open disagreement, no change line appears, and a second estimate gives the same recall
- **AND** only a correction with a reason (`wider label adjudicate ... --correction-reason`) changes the reference
Pinned by: `tests/unit/test_label_completion.py::test_package_re_review_on_sealed_record`

#### Scenario: A project from before looks were recorded
- **WHEN** a project's recall looks were taken before looks were recorded (its validation sample "may have been seen") and R1 changes a label of it saved before that time
- **THEN** the change needs a correction reason, as on a sealed record
- **AND** a probability sample drawn after the upgrade, and a label first saved after the latest older recorded look, are not sealed until a look uses them; with two recorded looks and an extension between them, the records added before look 2 are sealed
- **AND** an older project with a recorded look and no label change after it gives no change line, and the undo of a label saved before that look is refused; after R1 corrects one of its sealed labels from include to exclude, the next estimate and `wider validation status` say "1 record of validation had its labels changed after its recall was first revealed on <date>; ..."
Pinned by: `tests/unit/test_label_completion.py::test_old_project_counts_as_sealed`

### Requirement: Reference labels and corrections in the share bundle
The system SHALL write into the share bundle, and so the replication package, `reference_labels.csv` (sample, record_id, decision, source, sealed_at, corrected) and `label_corrections.csv` (sample, record_id, target, old_decision, new_decision, at, by as the attribution mode names it), each only when it has a row, with no reason text, note or abstract.
Surfaces: CLI `wider report --bundle`, `wider export package`; UI Recall page, "Write share bundle" and the Replication package card.

#### Scenario: A corrected reference in the bundle
- **WHEN** `validation` was sealed at a look and dana corrected the reference of record 7 with a reason, and the bundle is written in pseudonym mode
- **THEN** `reference_labels.csv` has one row per sealed record, record 7 with decision exclude and corrected yes, and `label_corrections.csv` one row with target adjudication, old include, new exclude and by "Researcher N"
- **AND** neither file holds the reason text, and a project without a seal or correction has neither file
- **AND** the recall computed from `reference_labels.csv` and the screening decisions equals the reported recall
Pinned by: `tests/unit/test_label_completion.py::test_bundle_carries_reference_and_corrections`

### Requirement: Methods text names a kept ranking
The system SHALL add to the methods draft, when a ranking version the samples were drawn from was kept after its records or abstracts changed, one sentence saying so with the date it was kept and who kept it (by the attribution setting), and nothing when no ranking was kept.
Surfaces: CLI `wider report`; UI Recall and report (methods draft).

#### Scenario: A kept ranking in the methods
- **WHEN** a synthetic project's ranking was kept by "ana" on 26 Oct after 3 abstracts were added
- **THEN** the methods draft says "The ranking was kept after the records or their abstracts changed (kept on 26 Oct 2026 by Researcher 1)", with the name written by the attribution setting
Pinned by: `tests/unit/test_rank_keep.py::test_methods_sentence`

### Requirement: Plain corpus export
The system SHALL export every unique record as RIS, BibTeX or CSV with its bibliographic data only (title, authors, year, journal, DOI, abstract, source database), with no screening decision, evaluation or model output, from the existing export area on Recall and report and as `wider export references --corpus`, using the existing writers. It works while development labels are open.
Surfaces: UI Recall and report export area; CLI `wider export references --corpus` (RIS, BibTeX and CSV).

#### Scenario: Export while labelling is open
- **WHEN** a synthetic project has 56 unique records, a development sample with open labels, and `wider export references --corpus` runs, and the page's plain corpus export is chosen
- **THEN** both write the RIS, BibTeX and CSV files with 56 records and their bibliographic fields and no field or note naming a decision, an evaluation or a model, under the exports folder, and the decision-set exports beside it stay refused with their reason
Pinned by: `tests/unit/test_corpus_export.py::test_plain_export_while_labelling_open`

### Requirement: Records not yet checked for duplicates
The system SHALL count PRISMA duplicates removed from the last deduplication run and report records imported since as a separate "not yet checked for duplicates" count, so identified records equal duplicates removed plus file-excluded plus not yet checked plus unique. The counts are provisional while that count is above zero; the PRISMA card says so in its first line.
Surfaces: CLI `wider export prisma`, `wider status`; UI Results PRISMA card, Overview summary, Deduplication.

#### Scenario: Imports after the last run
- **WHEN** a synthetic project had 120 records deduplicated to 100 and then 30 more were imported
- **THEN** the PRISMA counts read 20 duplicates removed and 30 not yet checked for duplicates, marked provisional, and the card's first line says so
Pinned by: `tests/unit/test_outputs.py::test_prisma_not_checked`

### Requirement: One population for the recall estimate, its bounds and its verdict
The system SHALL count each band of a probability sample, for the estimate, the bounds, the verdict, the suggestion and the chart, as its frame when the sample was drawn or last extended, leaving out only the records left out then. Records drawn into another sample afterwards SHALL count as records of the band the sample did not label.
Surfaces: CLI `wider recall`, `wider validation status`, `wider validation extend --suggested`, `wider results`, `wider report`; UI Recall, Results.

#### Scenario: Development sample drawn after the validation sample
- **WHEN** a synthetic ranking of 60 records has bands S1 (ranks 1-20, n 10) and S2 (ranks 21-60, n 10), `validation` is drawn, then a development sample takes the 30 other records of S2, every validation record is labelled, the target is 0.75, and `wider recall main --sample validation` runs
- **THEN** S2's kept and not-kept cells count 40 records together, the design's N_h of S2, with 10 labelled, so neither is a census, and the exact bounds are wider than with S2 counted as 10 records
- **AND** the outcome is not "met" while the exact bounds contain the target, the Horvitz–Thompson estimate is the same as before this change, and the lines add "30 records of the development sample were drawn after validation; its estimate and bounds count them as records of their bands that validation did not label."
- **AND** the suggestion never counts those 30 records as records left to label, and when it cannot settle it says "30 records of these bands are in another sample and cannot be labelled here, so the bounds may not close; a fresh sample (wider sample draw probability --name NEW --fresh-from validation) can settle it."
Pinned by: `tests/unit/test_recall_one_population.py::test_development_drawn_after_validation`

#### Scenario: Development sample drawn before
- **WHEN** the development sample is drawn first and `validation` after it
- **THEN** each band's cells count the band minus the development records, equal to the design's N_h, and the lines say "{n} records of the development sample are not covered." as before
Pinned by: `tests/unit/test_recall_one_population.py::test_development_drawn_before_validation`

#### Scenario: An extension after a later development sample
- **WHEN** a development sample takes S2 records after the draw and `wider validation extend validation --band S2=5` runs
- **THEN** S2's cells count the band minus the development records as they were at the extension, equal to the new N'_h, and a development sample extended later again counts in the cells as records not labelled here
Pinned by: `tests/unit/test_recall_one_population.py::test_extension_resets_the_population`

#### Scenario: A sample stored before this change
- **WHEN** `validation` has no stored list of left-out records, a development sample was created before it and another purposive sample after it
- **THEN** the cells leave out the earlier sample's records and count the later sample's records, and nothing is written to the sample
Pinned by: `tests/unit/test_recall_one_population.py::test_older_sample_fallback`

#### Scenario: The list of left-out records stays inside
- **WHEN** a probability sample is drawn with development records left out
- **THEN** `wider sample status --json` (its sampling log), the draw's output and the report's samples give the counts (`development_excluded`) and no list of record ids, and undoing the draw restores the earlier list with the sample
Pinned by: `tests/unit/test_recall_one_population.py::test_left_out_list_not_shown`

#### Scenario: A result stored before this change
- **WHEN** the stored validation lines and chart series of `validation` carry no `population` marker and a band has records drawn into another sample after the draw
- **THEN** `wider validation status`, the report's validation checks and the Results chart's notes add "The stored recall result of validation was worked out before records drawn into another sample after it were counted in its bands; estimate recall again (the same labels add no look).", and after `wider recall main --sample validation` the line is gone and no look was added
Pinned by: `tests/unit/test_recall_one_population.py::test_stored_result_before_the_fix`

### Requirement: Disagreements with sealed reference labels stated
The system SHALL state, wherever recall is read, how many sealed records of the probability sample have another label that disagrees with their reference label and that recall uses the reference labels. Left-out labels and labels saved before the record's adjudication SHALL NOT count. A review package's report line SHALL say when its labels were not used for final labels.
Surfaces: CLI `wider recall`, `wider validation status`, `wider report`, `wider export methods`; UI Recall page.

#### Scenario: A blind second review that disagrees on every record
- **WHEN** after a look on `validation` (10 records, all labelled include by the oracle and sealed), a second-mode review package for rev2 is made and its decisions file, giving exclude for all 10 records, is imported (no `--earlier` choice), and `wider recall main --sample validation` runs
- **THEN** recall is unchanged, `--json` gives `open_disagreements: 0` and `sealed_disagreements: 10`, and the lines add "10 records of validation have another label that disagrees with their reference label; recall uses the reference labels. List them with wider label agreement validation REVIEWER OTHER_REVIEWER; change a reference with wider label adjudicate validation RECORD DECISION --by NAME --correction-reason WHY."
- **AND** `wider validation status` and the report's validation checks give the same line, the Recall page's estimate (`GET /api/recall`), the validation status and the Results dashboard routes give it with its last sentence as "List them in the agreement table (Samples and labelling, Second reviewer and adjudication); change a reference there, with a correction reason.", the methods draft gives its first sentence, and the line names no record, decision or reviewer
- **AND** `wider label agreement validation oracle rev2` lists the 10 records, one line each ("record ID: oracle include, rev2 exclude")
- **AND** the report's line for rev2's package ends "labels used for agreement only; all its records had reference labels sealed by an earlier recall look"
Pinned by: `tests/unit/test_recall_sealed_disagreements.py::test_blind_second_review_disagreements_stated`, `tests/unit/test_recall_sealed_disagreements.py::test_hint_commands_list_the_records`

#### Scenario: A correction settles a disagreement
- **WHEN** in that state `wider label adjudicate validation <one record> exclude --by dana --correction-reason "second review"` runs
- **THEN** the count becomes 9 and the change line counts the corrected record
Pinned by: `tests/unit/test_recall_sealed_disagreements.py::test_correction_settles_a_disagreement`

#### Scenario: Labels left out and agreeing labels
- **WHEN** a package with `--earlier exclude` leaves out a reviewer's earlier label that disagrees with the reference, and another reviewer's later label agrees with it
- **THEN** neither counts, and no disagreement line appears
Pinned by: `tests/unit/test_recall_sealed_disagreements.py::test_left_out_and_agreeing_labels_not_counted`

#### Scenario: One later label disagrees
- **WHEN** after a look a second reviewer labels one sealed validation record exclude in the blind queue
- **THEN** `open_disagreements` is 0 and `sealed_disagreements` is 1, and the line says "1 record of validation has another label that disagrees with its reference label; ..."
Pinned by: `tests/unit/test_recall_sealed_disagreements.py::test_one_later_disagreement_counted`

#### Scenario: The package line with some records sealed
- **WHEN** a second-mode package holds 10 records, 4 of them sealed before it was imported
- **THEN** its report line ends "labels used for agreement, and for final labels except on 4 records whose reference labels were sealed by an earlier recall look", and a package imported before any look keeps "labels used for agreement and final labels"
Pinned by: `tests/unit/test_recall_sealed_disagreements.py::test_package_line_by_sealed_records`
