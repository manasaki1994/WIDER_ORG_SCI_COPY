# Results Dashboard Specification

## Purpose
One owner view of a result set's results after screening: agreement between the passes, recall lines already
estimated, the relevant found along the ranking, decision counts, and the reference sets and documents written. It
estimates nothing and records no look.

## Requirements

### Requirement: Results dashboard for one result set
The system SHALL show, for a chosen protocol, result set (the active one unless named) and sample, headed by the set and each pass's model: pass agreement, configuration comparisons, stored recall lines, the relevant-found chart, PRISMA counts, records per decision set, files in `exports/`. Choosing a set never activates it. Without decisions: "no screening decisions yet".
Surfaces: CLI `wider results [PROTOCOL] [--set N] [--sample NAME] [--json]`; UI Results dashboard page, its selects.

#### Scenario: Dashboard of the active set
- **WHEN** protocol `main` has sets 1 (active) and 2, pass A ran on model `a`, pass B on `b` and the judge on `c`, and `wider results main` runs
- **THEN** it prints the heading "main (set 1)" with "Pass A: a; pass B: b; third read: c", then the sections "Agreement between the passes", "Configuration comparisons (development sample)", "Recall", "Relevant found along the ranking", "PRISMA counts", "Reference files" and "Documents", in this order
- **AND** `--json` returns the same content with the keys `heading`, `agreement`, `comparisons`, `recall`, `curve`, `counts`, `reference_sets` and `documents`, and the Results dashboard page shows the same heading and sections
Pinned by: `tests/unit/test_results_dashboard.py::test_dashboard_sections_cli_and_json`, `tests/unit/test_results_js_node.py::test_dashboard_sections`

#### Scenario: Another set, protocol or sample chosen
- **WHEN** `wider results main --set 2` runs, or set 2 is chosen in the page's set select
- **THEN** the heading reads "main (set 2)" and every section reads set 2's decisions
- **AND** set 1 stays the active set, and the page's protocol and sample selects send the same `protocol` and `sample` the CLI takes as `PROTOCOL` and `--sample`
Pinned by: `tests/unit/test_results_dashboard.py::test_other_set_leaves_active_set`, `tests/unit/test_results_js_node.py::test_selects_send_choice_without_activating`

#### Scenario: Nothing screened yet
- **WHEN** the project has no screening decisions and `wider results` runs, or the page is opened
- **THEN** the CLI exits with code 1 and the message "no screening decisions yet", and the page shows that sentence in normal black text with a link to the Screening page
Pinned by: `tests/unit/test_results_dashboard.py::test_nothing_screened`, `tests/unit/test_results_js_node.py::test_nothing_screened`

### Requirement: Agreement between the passes
The system SHALL give, over the set's records with a pass A and pass B decision made by its own passes (S1 and S2 in a second screen), the 3 × 3 table, percent agreement and Cohen's kappa (two decimals), and count every record by how its decision was reached: both passes agreed, third read, no majority, researcher, imported, carried from another set, not recorded, one pass, other (value shown). Undefined kappa says why.
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: Agreement on a synthetic set
- **WHEN** of 10 synthetic records both passes read, A and B agree on 6 (4 exclude, 2 include) and differ on 4 (A include and B exclude twice, A maybe and B exclude once, A include and B maybe once); the third read settles 3, the fourth gets three different reads and the researcher then decides it; 2 more records were imported (decided by "imported from list.csv")
- **THEN** the table holds those counts, agreement is "6 of 10 (60%)" and kappa is "0.34"
- **AND** the routes read: both passes agreed 6, settled by the third read 3, no majority (left at maybe) 0, decided by the researcher 1, imported 2, carried from another set 0, not recorded (older decision) 0
Pinned by: `tests/unit/test_agreement.py::test_table_percent_kappa_routes`

#### Scenario: Kappa not defined
- **WHEN** both passes gave every one of 5 records the decision exclude, or no record has two pass decisions
- **THEN** agreement reads "5 of 5 (100%)" with "kappa not defined: both passes gave only one decision", or the section reads "No record was read by both passes."
Pinned by: `tests/unit/test_agreement.py::test_kappa_not_defined`

#### Scenario: Second screen
- **WHEN** the set is a second screen that carried 8 decisions of its source set with their A and B votes and re-read 4 maybes with S1, S2 and S3
- **THEN** the table compares S1 with S2 over the 4 re-read records only, the section names them "read S1" and "read S2", records settled by S3 count as settled by the third read, and the 8 carried records count as carried from another set and not in the table
Pinned by: `tests/unit/test_agreement.py::test_second_screen_reads`

#### Scenario: Third read off, or a stricter majority
- **WHEN** the protocol has `blind_third_read` off and A read include and B maybe on one record, or has `majority: 3` and the three reads were maybe, include and maybe
- **THEN** both records end at maybe and count as no majority (left at maybe), whichever pass the stored route names, and both are in the `split` reference set
- **AND** changing the protocol's `majority` after screening leaves the set's routes as they were, since they follow the rules the set was screened with
Pinned by: `tests/unit/test_agreement.py::test_third_read_off_and_majority_three`, `tests/unit/test_agreement.py::test_routes_use_set_rules`

#### Scenario: One pass
- **WHEN** the set was screened with `passes: 1`
- **THEN** the section reads "This set was screened with one pass; there is no agreement to report." and the routes count every record under one pass
Pinned by: `tests/unit/test_agreement.py::test_one_pass`

#### Scenario: Copied and older decisions
- **WHEN** a set holds decisions copied from a reused set (with that set's votes), decisions stored before the route was recorded, and decisions its own reads gave that were re-derived when the project was migrated
- **THEN** the copied ones count as carried from another set and stay out of the table, the older ones count as not recorded (older decision) and stay out of the table, and the migrated ones stay in the table
Pinned by: `tests/unit/test_agreement.py::test_copied_and_older_decisions`

### Requirement: Configuration comparisons kept apart on the dashboard
The system SHALL show on the dashboard, below the agreement and under the heading "Configuration comparisons (development sample)", the same comparison lines the report gives, so the figures that describe the development sample are never shown as figures of the corpus.
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: One comparison
- **WHEN** comparison `cmp1` ran on `development` and the dashboard is shown
- **THEN** the heading "Configuration comparisons (development sample)" is followed by the lines the report's "Configuration comparisons" section gives for `cmp1`
- **AND** without a comparison the heading is followed by "No configuration comparison was run."
Pinned by: `tests/unit/test_results_dashboard.py::test_comparisons_apart`

### Requirement: Recall lines shown without a new look
The system SHALL show in the Recall section, "as of" their time, the lines and outcome stored with the last recall estimate on the sample, as `wider validation status` gives them, if they are for the shown set; it computes no estimate and records no look. If labels or decisions changed since, a black sentence says so above them. Otherwise one black sentence says what is missing and the command that makes it.
Surfaces: CLI `wider results [--sample NAME]`; UI Results dashboard page.

#### Scenario: Stored estimate of the shown set
- **WHEN** `wider recall main` ran on `validation` for set 1, and `wider results main` then runs and the page is opened
- **THEN** the Recall section gives the same lines, in the same order, as `wider validation status`, after "as of" and the estimate's time
- **AND** `validation_looks` for (validation, set 1) keeps the count it had before, and no stage result or History entry is added
Pinned by: `tests/unit/test_results_dashboard.py::test_recall_lines_without_look`

#### Scenario: No estimate yet
- **WHEN** no recall estimate is stored for `validation`
- **THEN** the Recall section reads "No recall estimate on validation yet. Estimate recall on the Recall and report page (wider recall main)." and no chart is drawn
- **AND** with `--sample nosuch` it reads "No sample nosuch.", and with the purposive `--sample development` "Recall is estimated on probability samples only; development is purposive."
- **AND** with set 2 shown (set 1 active) and the sample `validation2`, the command named reads `wider recall main --set 2 --sample validation2`
Pinned by: `tests/unit/test_results_dashboard.py::test_recall_none_yet`, `tests/unit/test_results_js_node.py::test_recall_none_yet_black_text`

#### Scenario: Estimate of another set
- **WHEN** the stored estimate on `validation` is for `main` set 1 and set 2 is shown
- **THEN** the Recall section reads "The last recall estimate on validation is for main (set 1), not set 2. Estimate recall for set 2 on the Recall and report page (wider recall main --set 2); this records a look on set 2." and gives none of set 1's lines
Pinned by: `tests/unit/test_results_dashboard.py::test_recall_other_set`

#### Scenario: Labels or decisions changed since the estimate
- **WHEN** a label of a sampled record, or the shown set's decision on a sampled record, changed after the estimate was stored
- **THEN** the Recall section keeps the stored lines under the black sentence "The labels or screening decisions changed since this estimate (<time>); estimate recall again to update it."
- **AND** the chart section draws no chart or table and reads "The labels or screening decisions changed since the last recall estimate; estimate recall again to redraw the chart."; once the change is undone, both sentences go and the chart returns
- **AND** a changed decision on a record outside the sample changes neither the lines nor the chart, and the page shows the same sentences as the CLI
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_stale`, `tests/unit/test_results_js_node.py::test_stale_and_missing_series_sentences`

### Requirement: Relevant found along the ranking
The system SHALL draw, from the series stored with the shown set's last recall estimate, one point per band end for estimated relevant, kept (primary variant) and missed, joined by straight lines, with band boundaries, range bars beside the points where exact bounds exist, titled axes, an accessible name, the label "Estimate from the validation sample; no stopping rule." and a table. Without a current series a sentence says why.
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: Chart and table from the stored series
- **WHEN** the stored series for set 1 has points at the band ends 50, 200 and 400
- **THEN** the page draws three lines through those points with marks at 50, 200 and 400, a legend naming "Estimated relevant", "Estimated relevant kept by screening" and "Estimated missed by screening", a vertical boundary line at 50 and at 200, the label above, and no target, threshold or stopping line
- **AND** under the chart one sentence reads "Points are at band ends; the lines only join them, since the sample says nothing about where in a band the relevant records sit."
- **AND** the table under it and the CLI table list, for each point, the band end with estimated relevant, kept and missed, the last row's kept divided by relevant equal to the stored primary estimate and its missed equal to relevant minus kept
Pinned by: `tests/unit/test_results_js_node.py::test_chart_from_series`, `tests/unit/test_results_dashboard.py::test_curve_table_matches_estimate`

#### Scenario: Accessible chart
- **WHEN** the chart is drawn
- **THEN** the SVG has `role="group"` and `aria-labelledby` naming its `<title>` "Estimated relevant records along the ranking" and its `<desc>`, which gives the last point's estimated relevant, kept and missed and names the table below as the same numbers
- **AND** each band mark has `role="img"`, `tabindex="0"` and an `aria-label` with its tooltip text, so a screen reader reaches each band; lines, range bars and boundaries are `aria-hidden`
- **AND** the x-axis is titled "Position in the ranking (1 = highest score)" and the y-axis "Cumulative estimated relevant records", in normal-size black text
Pinned by: `tests/unit/test_results_js_node.py::test_chart_accessible_name_and_axes`

#### Scenario: Ranges from the exact bounds
- **WHEN** the stored series holds bounds for each band end
- **THEN** each point of the relevant, kept and missed lines has a shaded bar from its low to its high bound beside the point (not centred on it, no caps), the legend names it "Exact range (valid at every look)", and one sentence reads "Ranges: exact bounds valid at every look (<level>%), summed over the bands up to each end; conservative.", with the level stored with the estimate (95% by default)
- **AND** when a point lies outside its own range, it is drawn where it was computed, never moved into the range, and that band's tooltip and table row add, in black, "The estimate lies outside its range here. Both are right: the estimate and the range are worked out in two different ways, which can disagree when few relevant records were found."; a closed line "How the estimate and the range are made" under the chart explains the two methods
- **AND** when the stored series holds no bounds (its labels differ from those of the bounds), no range bar is drawn and the sentence reads "No ranges: the chart's labels are not those of the exact bounds, so it shows point estimates only."
Pinned by: `tests/unit/test_results_js_node.py::test_range_bars_from_bounds`, `tests/unit/test_results_dashboard.py::test_curve_without_bounds`, `tests/unit/test_results_dashboard.py::test_point_below_within_above_range`, `tests/unit/test_results_js_node.py::test_point_outside_range_sentence_node`

#### Scenario: No series stored with the estimate
- **WHEN** the stored estimate was made before series were stored
- **THEN** the Recall section shows the stored lines, and the chart section reads "No chart is stored with this estimate; estimate recall again to draw it."
- **AND** for a sample without score bands (an imported sample, whose estimates store no lines) the Recall section reads "validation has no score bands, so the dashboard keeps no recall lines for it; its recall is shown on the Recall and report page (wider recall main)." and the chart section "This sample has no score bands, so no chart is drawn."
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_missing`, `tests/unit/test_results_dashboard.py::test_recall_imported_sample`, `tests/unit/test_results_js_node.py::test_stale_and_missing_series_sentences`

#### Scenario: Series stored before band details
- **WHEN** the stored series has points but no band details (stored before this change)
- **THEN** the chart draws the relevant, kept and missed lines through its points with no range bars and no tooltips, and one black sentence reads "Ranges and band details are stored with newer estimates; estimate recall again to show them (this records a look)."
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_old_series`

#### Scenario: No relevant record in the labelled sample
- **WHEN** the shown set's last recall estimate found no relevant record in the labelled validation sample
- **THEN** the Recall section reads "Recall could not be estimated: the labelled sample holds no relevant validation papers." above the stored lines, and the chart section reads "The labelled sample holds no relevant record, so no chart is drawn." instead of a flat line at zero
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_no_relevant`

### Requirement: Counts, reference sets and documents on the dashboard
The system SHALL show the shown set's PRISMA counts as the PRISMA 2020 diagram counts them, the records per export preset and per pathway with the unresolved ones counted apart, the Export records card, and each output file under `exports/` (report, PRISMA 2020 diagram and checklist, PRISMA-S, methods, reference files, packages) with its time and link, or "not written yet" and its command (on the page in a closed "Command line" fold).
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: Counts match the exports
- **WHEN** the shown set has 3 include, 2 maybe and 5 exclude decisions, of which 6 agreed, 3 settled by the third read and 1 decided by the researcher
- **THEN** the counts equal those of `wider export prisma` for the set, and the export counts read Include 3, Include + Maybe 5 (of which 0 unresolved), Maybe only 2, Exclude 5; both configurations agreed 6, resolved by the third model 3, unresolved disagreement 0, decided by a researcher 1
- **AND** each count equals what `wider export selection --dry-run` gives for that preset or pathway, the Export records card sits under the counts, and the Recall page holds only the line "Reference files are exported on the Results dashboard (Export records)." with a link to it
- **AND** `wider results` prints the same counts and the command `wider export selection`
Pinned by: `tests/unit/test_results_dashboard.py::test_counts_match_exports`, `tests/unit/test_outputs.py::test_recall_page_links_to_card_node`

#### Scenario: Documents written and missing
- **WHEN** only `exports/report.md` and `exports/prisma_2020.svg` exist
- **THEN** the Documents section lists both with their times and links served by the exports download route, and lists the PRISMA 2020 checklist as "not written yet (wider export prisma-checklist)" in `wider results`, and on the page as "not written yet" with `wider export prisma-checklist` in a closed "Command line" fold
- **AND** once `main_include_and_maybe.ris`, `main_include_by_third_read.zip`, `main_audit.csv` and a `main_export_package_<time>.zip` exist, each is listed with its time and link, and the reference files' not-written line names `wider export selection`
Pinned by: `tests/unit/test_results_dashboard.py::test_documents_list`, `tests/unit/test_results_js_node.py::test_dashboard_commands_in_folds`

### Requirement: Band tooltips and table of the relevant-found chart
The system SHALL give per band of the chart, in a tooltip on hover and on keyboard focus of its mark and in a table row with the same values: ranks, records, sampled, labelled and relevant observed (headed "observed"), estimated relevant, kept and missed in the band and up to its end, and the band's and cumulative ranges where bounds exist (headed "estimated"). A band with fewer than 3 relevant observed is marked "few relevant observed".
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: Tooltip and table row of a band
- **WHEN** the stored series has a band of ranks 51-200 with 150 records, 10 sampled, 10 labelled and 2 relevant observed, all kept
- **THEN** focusing its mark with the Tab key, or hovering it, shows "Ranks 51-200: 150 records, 10 sampled, 10 labelled, 2 relevant observed (few relevant observed). Estimated in this band: 30 relevant (range <low>-<high>), 30 kept, 0 missed. Up to rank 200: <relevant> relevant (range <low>-<high>), <kept> kept (range <low>-<high>), <missed> missed (range <low>-<high>)."
- **AND** the mark's accessible name holds the same text, Escape or moving the focus away hides the tooltip, and the table row of that band gives the same numbers under the "observed" and "estimated" headings
- **AND** `wider results` prints the same table with the same headings
Pinned by: `tests/unit/test_results_js_node.py::test_band_tooltip_on_focus_and_hover`, `tests/unit/test_results_dashboard.py::test_band_table_cli_matches_page`

#### Scenario: Records of a band drawn into another sample
- **WHEN** after the validation sample was drawn, a development sample took 20 unsampled records of ranks 51-200, and recall was estimated
- **THEN** the band's row and tooltip give 150 records, the band's records when the validation sample was drawn, and add "20 of these records were drawn into another sample after this one; the estimate and the range both count them as records not labelled here."
- **AND** a point of that band outside its range keeps the general explanation of a point outside its range, and `wider results` prints the same sentence in that row
Pinned by: `tests/unit/test_results_dashboard.py::test_band_records_in_another_sample`

### Requirement: Missing validation data explained on the chart
The system SHALL, when a band of the sample's design has sampled records not yet labelled, end the chart's lines at the last band end before it and say in one black sentence which band, how many of its sampled records are unlabelled and what that leaves unknown, rather than drawing a too-low estimate. A band with labelled records and none relevant keeps its point, with its range where bounds exist.
Surfaces: CLI `wider results`; UI Results dashboard page.

#### Scenario: A band with no labelled record
- **WHEN** a relevant-found series stored before this change (when recall could be estimated on a partly labelled sample) holds the band of ranks 201-400 with 10 sampled records and none labelled
- **THEN** the lines end at rank 200, the table row of ranks 201-400 reads "no labelled record: not estimated", and one black sentence reads "Ranks 201-400 hold no labelled record, so nothing is estimated from rank 201 on; label that band's sampled records and estimate recall again."
- **AND** `wider results` prints the same sentence and row
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_band_without_labels`, `tests/unit/test_results_js_node.py::test_band_without_labels_sentence`

#### Scenario: A band partly labelled
- **WHEN** a series stored before this change holds the band of ranks 51-200 with 10 sampled records of which 5 are labelled
- **THEN** the lines end at rank 50, the band's row gives its observed counts and "5 of 10 sampled unlabelled: not estimated", and one black sentence reads "Ranks 51-200: 5 of 10 sampled records are not labelled yet, so nothing is estimated from rank 51 on; label them and estimate recall again."
- **AND** the sentence is followed by the link "Label them on Samples and labelling", which opens that page, and `wider results` prints the same sentence and row and the commands `wider label export validation <file.csv>` and `wider label import validation <file.csv>`
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_band_partly_labelled`, `tests/unit/test_results_js_node.py::test_label_link_node`

#### Scenario: A band with no relevant observed
- **WHEN** a band of 200 records has 10 labelled and none relevant, and the estimate has exact bounds
- **THEN** its point adds 0 estimated relevant, its tooltip and row say "0 relevant observed" and "few relevant observed", and its range up to that end shows the upper bound above the point
Pinned by: `tests/unit/test_results_dashboard.py::test_curve_band_no_relevant_keeps_range`

### Requirement: Estimate recall again from a stale chart
The system SHALL, when the chart's stored series no longer matches the labels and decisions, offer beside its sentence a button **Estimate recall again** that makes the same estimate as the Recall page's **Estimate recall** for the shown protocol, set and sample, and so records one look; opening or refreshing the dashboard never estimates.
Surfaces: CLI `wider recall PROTOCOL [--set N] --sample S`; UI Results dashboard, **Estimate recall again**.

#### Scenario: Stale chart estimated again
- **WHEN** a sampled record's label changed after the estimate on `validation` for `main` set 1, and the dashboard is opened
- **THEN** the chart section shows the stale sentence with the button **Estimate recall again**, and opening the page again leaves `validation_looks` as it was
- **AND** pressing the button sends the same request as **Estimate recall** on the Recall page (protocol `main`, set 1, sample `validation`), adds one look, and the chart is drawn from the new series with no stale sentence, focus on the chart's heading
- **AND** `wider recall main --sample validation` gives the same estimate and look
Pinned by: `tests/unit/test_results_js_node.py::test_estimate_again_button_node`, `tests/unit/test_results_dashboard.py::test_estimate_again_matches_cli`

#### Scenario: Estimate recall again while labels are incomplete
- **WHEN** records were added to `validation` after the estimate and are not labelled yet, and **Estimate recall again** is pressed
- **THEN** no look is recorded, the stale sentence stays, and the chart's message line shows the sentence of recall-and-outputs "Recall revealed only on a complete validation sample", in normal black text
Pinned by: `tests/unit/test_results_js_node.py::test_estimate_again_while_incomplete`

### Requirement: Review packages on the Results dashboard
When a project has review packages, the system SHALL say once, in normal-size black text, above the Results dashboard's sections, how many labels on the validation samples were made blind in a package (repeats apart), in the blind queue, otherwise in WIDER and by import, and name each package neither imported nor withdrawn; with no package it says nothing new.
Surfaces: CLI `wider results [PROTOCOL] [--json]`; UI Results dashboard page.

#### Scenario: Dashboard line with a package not imported
- **WHEN** R1 made 200 labels in WIDER on `validation` outside the blind queue (the unblinded queue, or before label provenance was recorded), 5 were imported with `wider label import`, R3's package P was imported with 28 labels (3 of them repeats), R4 made 10 in the blind queue, and R2's package Q on `validation` is not imported yet, and the owner runs `wider results` or opens the Results dashboard
- **THEN** both show once "Validation labels: 25 made blind in a review package, 3 repeated in a review package, 10 in the blind queue in WIDER, 200 made in WIDER, 5 imported. R2's review package is out and not imported yet." and `--json` carries the same counts under the key `blinded_review`
- **AND** in a project with no package neither the line nor the key appears
Pinned by: `tests/unit/test_results_dashboard.py::test_blinded_review_line`, `tests/unit/test_results_js_node.py::test_blinded_review_line_once`
