## MODIFIED Requirements

### Requirement: PRISMA 2020 checklist
The system SHALL write `exports/prisma_2020_checklist.md` and `.json` with the 42 entries of PRISMA 2020 (Page et al. 2021) for a protocol and set, each with WIDER's lines from the reporting items (or "not recorded by WIDER"), its status, what is missing and the researcher's text under its own label. No model writes it. The replication package carries it.
Surfaces: CLI `wider export prisma-checklist [--protocol] [--set N]`, `wider export package`; UI Recall page, PRISMA 2020 checklist card.

#### Scenario: Items filled and not recorded
- **WHEN** a synthetic project has imported two platforms, screened under `main` with passes A, B and the judge, labelled a probability sample `validation`, and built a replication package
- **THEN** item 5 gives the research question and the codebook version the protocol used, item 6 lists both platforms with their import dates, item 7 points to the search strings and `prisma_s.md`, item 8 names each pass's model, the third read, the majority rule, the researcher's review of records left without a majority and the validation sample with its size, and item 16a gives the counts of `prisma_2020.json` for the set
- **AND** items 24a and 24c read "not recorded by WIDER", 24b reads "not recorded by WIDER" and "WIDER screening settings (not a review protocol): hash" with 12 characters, item 27 names the package file and its manifest, and items 11 and 16b read "not recorded by WIDER"
- **AND** each item carries its status as on the PRISMA reporting page (items 5, 6, 8, 16a and 27 "Partially documented"; 7, with no search string archived, and 11 "Researcher input needed"), the message counts the items by status, and "Write from the store" on the card writes the same two files as the CLI
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_items_filled_and_not_recorded`, `tests/unit/test_prisma_2020_checklist.py::test_cli_and_route_write_same_files`, `tests/unit/test_outputs.py::test_checklist_card_and_write_every_set_node`

#### Scenario: Researcher text in the checklist
- **WHEN** the researcher saved text for item 9 and marked item 12 not applicable with a reason, and the checklist is written
- **THEN** item 9 shows WIDER's "not recorded by WIDER" under "WIDER recorded" and the text under "Researcher text", item 12 reads "Not applicable" with the reason, and the JSON gives both under `researcher` with no researcher name
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_researcher_text_in_checklist`

#### Scenario: Checklist in the replication package
- **WHEN** the replication package is built
- **THEN** the zip holds `prisma_2020_checklist.md` and `prisma_2020_checklist.json`, rebuilt from the store at that moment, with their SHA-256 in `MANIFEST.txt`, and still no abstract
- **AND** item 27 of the packaged copy reads "this replication package (MANIFEST.txt, manifest.json)" while `exports/prisma_2020_checklist.md` keeps its own item 27, and a package built before any import still builds, without the checklist
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_checklist_in_package`

#### Scenario: Nothing imported
- **WHEN** the checklist is requested before any import
- **THEN** the system refuses with a message starting "nothing imported yet", and no file is written
Pinned by: `tests/unit/test_prisma_2020_checklist.py::test_nothing_imported`

### Requirement: PRISMA-S search report
The system SHALL write `exports/prisma_s.md` and `.json` with the 16 items of PRISMA-S (Rethlefsen et al. 2021), each with WIDER's lines from the reporting items or "not recorded", its status, what is missing and the researcher's text apart; the dates are import dates unless a search date was recorded, and say so; the peer-review item names the model second reader and says it is not a human peer review.
Surfaces: CLI `wider export prisma-s`; UI Recall page, PRISMA-S card, "Write from the store".

#### Scenario: Items filled and missing
- **WHEN** the synthetic project has imported two platforms, run one second-reader review and one seed chaining run
- **THEN** `prisma_s.md` lists both platforms with their strings and ledgers, the citation-searching item names the chaining run, the peer-review item names the review run as a model second reader, and the dates item says "import date; search date not recorded"
- **AND** the "updates" item says "not recorded", item 15 reads "Automatically documented" and item 14 "Partially documented"
Pinned by: `tests/unit/test_outputs.py::test_prisma_s_export`

#### Scenario: Before any import
- **WHEN** `wider export prisma-s` runs, or a replication package is built, before any import
- **THEN** `prisma_s.md` lists the 16 items, each "Researcher input needed" with the one line "nothing imported yet", and the package still builds
Pinned by: `tests/unit/test_reporting.py::test_prisma_s_before_import`

#### Scenario: Researcher fields are name-scanned
- **WHEN** PRISMA-S item 9's text mentions a hidden reviewer and the share bundle is built
- **THEN** the bundle is refused naming `prisma_s.md` and item 9, while the archived search strings in `prisma_s.md` are not scanned for names
Pinned by: `tests/unit/test_reporting.py::test_prisma_s_researcher_fields_name_scanned`

#### Scenario: Citation-chain imports and prior reviews
- **WHEN** the project imported a file as citation searching (`citation_chain`) and registered one prior review, with no seed chaining run
- **THEN** item 5 names the citation-searching import with its record count and reads "Partially documented", and item 11 lists the prior review as context and reads "Researcher input needed"
- **AND** with `scopus` also imported, item 1 lists Scopus and not the citation-searching import, and PRISMA 2020 item 6 gives `databases` as for Scopus alone and the import under other sources
Pinned by: `tests/unit/test_reporting.py::test_prisma_s_citation_imports_and_prior_reviews`

#### Scenario: Multi-database platform
- **WHEN** the project imported from `ebsco` and `scopus`
- **THEN** item 1 covers Scopus fully and EBSCOhost in part with "which databases on this platform: not recorded by WIDER", and item 2 lists EBSCOhost as a platform that searches several databases
Pinned by: `tests/unit/test_reporting.py::test_prisma_s_multi_database_platform`
