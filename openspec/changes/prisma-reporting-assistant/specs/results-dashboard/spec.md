## MODIFIED Requirements

### Requirement: Counts, reference sets and documents on the dashboard
The system SHALL show the shown set's PRISMA counts as the PRISMA 2020 diagram counts them, the records per export preset and per pathway with the unresolved ones counted apart, the Export records card, and each output file under `exports/` (report, PRISMA diagram, checklists and reporting files, methods, reference files, packages) with its time and link, or "not written yet" and its command (on the page in a closed "Command line" fold).
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
- **AND** it lists the PRISMA reporting checklists as "not written yet (wider report prisma export)", and once that command has run, lists `exports/prisma_reporting/prisma_2020_checklist.md`, `prisma_2020_checklist.docx`, `prisma_s_checklist.docx`, `methods_documentation.docx` and `prisma_2020_flow.docx` with their times and links; a .docx missing after that export reads "not written: python-docx is not installed" only when python-docx cannot be imported, else "not written yet (wider report prisma export)"
Pinned by: `tests/unit/test_results_dashboard.py::test_documents_list`, `tests/unit/test_results_js_node.py::test_dashboard_commands_in_folds`
