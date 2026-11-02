# WIDER features

The feature file for WIDER, a literature review AI tool for broad, multi-vocabulary constructs. It records what the
tool does and the decisions behind the user experience. It was written from a structured interview
with the author on 10 October 2026 and is kept current: every feature line carries a status, and a test checks that
every pipeline stage in `wider/stages.yaml` is covered here.

Behaviour is specified in OpenSpec capability specs under `openspec/specs/` (requirements with scenarios); each
section below links its spec. This file stays the readable overview.

Status words used below: **built** (in the repository, tested). Labels such as WP12, C3, A1 or phase B name the
development step that built a feature.

## 1. Purpose and users

- **Who it is for**: management and organisation researchers who review a construct that is studied under many
  names across fields, and who want the recall of a systematic search without reading ten thousand abstracts by hand.
- **What it is**: a Python package, a CLI (`wider`) and a local web UI served on 127.0.0.1. The web UI is the primary
  surface; every UI action has a CLI command that does the same thing, so a review can be scripted and replicated.
- **Delivery**: `pip install`.
- **Scope today**: construct setup to recall estimate and PRISMA report at the title-and-abstract level. Full-text
  screening, data extraction and synthesis are registered extension points (`fulltext`, `extract`, `synthesis`),
  not built.

## 2. Principles

1. **The procedure is fixed, the preferences are the researcher's.** Benchmark-guided query development,
   deduplication with an audit trail, anchor ranking that sorts and never filters, stratified human samples, frozen
   codebook, recall-first LLM screening with verbatim quotes and majority reads, stratified recall with a confidence
   interval. The researcher brings the research question, seed papers, databases, LLM and embedding backends,
   reviewers, adjacent literatures and prior reviews. Methodological constants are documented advanced settings with
   the original values as defaults (`wider protocol`).
2. **Local by default.** The project folder is the database. Files the researcher adds (exports, prior reviews, PDFs)
   are parsed on their computer and never uploaded. Only DOIs and titles ever leave the machine, and only after the
   researcher allowed online lookups for the project (asked at the first lookup) or turned on the prior-review
   lookup switch; every outside request is logged in the project. No telemetry. Keys live in environment variables only.
3. **Visible results.** Every stage ends with a visible summary of what happened, in the UI and on the command line,
   and tells the researcher what to do next.
4. **Reproducible.** Every artifact is versioned, every model call is hashed and stored, every run can be replayed
   without network access, and the report regenerates every number from the store.
5. **Explain on demand.** The interface stays quiet; explanations open when asked.

## 3. Navigation and shell

Spec: [`guided-workflow-and-shell`](../openspec/specs/guided-workflow-and-shell/spec.md)

| Feature | Status | Notes |
|---|---|---|
| Left sidebar with an icon and label per page, collapsible to an icon rail | built (10 Oct 2026, WP12) | collapse state remembered in the browser; icon rail by default below 900 px |
| Sidebar grouped under four numbered sections: 1 Literature search (Model for query creation, Review plan, Concepts and synonyms, Search strings, Search results), 2 Data cleaning (Deduplication, Missing abstracts), 3 Model tuning and validation (Ranking, Samples and labelling), 4 Screening and outputs (Screening, Recall and report) | built (15 Oct 2026, C3 `unify-stage-pages`) | `section` and `sections:` in `wider/stages.yaml`; the same names in the top bar, `wider status` and the guide |
| An existing project opens on the last page used in this browser, else on Model for query creation while no query-creation model is set, else on the next stage; a new one on Model for query creation | built (15 Oct 2026, C3 `unify-stage-pages`) | page name kept in browser storage under a hashed key, never sent to the server; `wider next` names the fallback stage |
| Pages redraw after an action; a newer notice closes the earlier ones | built (15 Oct 2026, C3 `unify-stage-pages`) | |
| Next step named on the command line after each stage command | built (15 Oct 2026, C3 `unify-stage-pages`) | |
| Stage status marks in the sidebar (done, current, ready, blocked) | built (WP12) | from `GET /api/stages`; word for screen readers and on hover |
| Home page | built (WP12) | see section 4 |
| Top bar on every page inside a project: breadcrumb (no step number since 30 Oct 2026, D32 F16), Notebook, Guide and the one main button where the page has them; replaces the slim stepper | built (18 Oct 2026, A1 `design-pass-a1-dedup`) | `wider status` (the stages in order) |
| Sidebar of steps: project box, section marks, a check, current dot or ring per step, rose "needs you" mark, footer with "N of M steps done" | built (18 Oct 2026, A1) | `wider status` prints "done, needs you: …" |
| Public Sans shipped as font files; tone tokens (blue you, violet WIDER, stone open, rose needs you, cyan unsure, green done) passing WCAG 2.2 AA | built (18 Oct 2026, A1) | `tests/unit/test_style_tokens.py` |
| Help and settings entries at the bottom of the sidebar | built (WP12) | settings = LLM and embedding backends (edit in place, remove, per-stage choice), online lookups, prior-review policy, review protocol (moved from setup, C3) |
| Works at 1280×800 (13-inch laptop) without horizontal scrolling | built (WP12) | content column max ~1100 px; checked at 375 px too (wide tables scroll inside their card) |
| Keyboard navigation and screen-reader labels on every control | built (WP12 shell, WP13 drawer); Ctrl/Cmd+Z undo (WP15) | skip link, landmarks, visible focus, `aria-current`, `aria-expanded`, `aria-label` on icon buttons |
| English interface only | decision | no translation layer |
| Overview: Continue (recommended step, why, Back to where you stopped), Needs attention with one action each, Research journey, Project summary; a project opens on it | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026) | `wider status`, `wider next`; result headlines of stages that can hold a judgment left out (blinding) |
| Journey line under the top bar (section, current step with its state, next step; Hide remembered in the browser); Switch project menu on the breadcrumb's title | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026) | `wider next`, `wider status` |
| "What you can do here now" box under each stage page's title (work here, completed, locked with Unlock to change…, not yet); never disables anything | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026) | `wider guide <page>` prints the same sentence first ([`step-guide`](../openspec/specs/step-guide/spec.md)) |
| What a run sends, said beside its button (backend check, model list, seed lookup, screening on a cloud backend, missed names) | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026) | `GET /api/send-notice/<action>`; the command line prints the same line ([`privacy-and-data-policy`](../openspec/specs/privacy-and-data-policy/spec.md)) |
| UX review fixes (30 Oct 2026, D32): plain words on every page (no file names, flags or internal terms outside the Command line fold); "Runs on this computer; nothing leaves it." once per page; a result card only for this visit's run (earlier ones: "Last run <when>. Show the result"); one h1 and h2 style; the open project has no Remove button; the explorer names the two best cells | built (change `ui-redesign`, archived 31 Oct 2026) | `tests/unit/test_plain_words_ui.py`, `tests/unit/test_d32_layout_css.py` |

## 4. Home page

Spec: [`guided-workflow-and-shell`](../openspec/specs/guided-workflow-and-shell/spec.md)

| Feature | Status | Notes |
|---|---|---|
| Project switcher (list of project folders opened before) | built (WP12) | `~/.wider/recent_projects.json` holds folder paths only; open any folder by path |
| Create project from the home page | built (WP12) | wraps `wider init` |
| Project card: title, "stage k of n · N records", segmented progress bar, Open and Info | built (WP12; "Next" link removed in C3) | |
| Info button per project: cost so far, tokens, backend kind (local or cloud), benchmark coverage, last run | built (WP12, costs WP14) | `wider info` prints the same summary |
| Active-run banner with progress, ETA and cost so far | built (WP14) | a job bar on every page, not only home |
| First-launch backend check (which LLM and embedding backends are reachable) | built (WP12); home banner removed in C3 | `wider check-backends`; the check now lives on Model for query creation and Settings, nothing is contacted until the researcher presses Check; never downloads weights |
| "New to WIDER? Try the interactive demo first." box with Explore interactive demo (new tab) | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026) | [`interactive-demo`](../openspec/specs/interactive-demo/spec.md) |

## 5. Stage pages: the common pattern

Every stage page follows one layout (decided 10 October 2026, see section 15):

1. Title with a status chip, and a **run selector** on the right to view earlier runs of the same stage.
2. A **result card** tinted by outcome with one headline sentence ("2,480 records became 1,912 unique ones. 37
   pairs still need your decision.").
3. Three or four **metric tiles** with the key numbers, plus per-run cost and tokens and a local-or-cloud marker.
4. **Collapsible sections** for the detail: record-level tables with search, charts where they help, downloadable
   outputs listed with their paths.
5. A one-line reminder of the previous stage's result, an **Explain this step** button, and one primary
   **Continue to next stage** button. The page does not navigate on its own.

| Feature | Status | Notes |
|---|---|---|
| Shared stage result (headline, metrics, warnings, next stage) recorded by every engine | built (WP8) | `last_result:<stage>` in the store; CLI prints the same summary |
| Result card, metric tiles, Continue button in the UI | built (WP8) | |
| Run selector showing earlier runs | built (WP14) | every stage result is kept (`stage_results`) |
| Collapsible sections with search in tables | partly built: collapsible details in result cards (WP8); search in the record tables (WP13) | not every table yet |
| Explain this step (why this step, what to check), on demand | built (WP12) | text from `stages.yaml` |

## 6. Per-stage features

### `setup` Review plan

Spec: [`project-setup`](../openspec/specs/project-setup/spec.md), [`llm-backends`](../openspec/specs/llm-backends/spec.md)

| Feature | Status | Command |
|---|---|---|
| Create a project folder with `project.yaml`, benchmark file, folders, store | built | `wider init` |
| Review plan page: five task cards (Research question, Seed papers, Prior reviews, Databases, Find more seed papers) with state, line and open questions; each task on its own page `#setup?task=<key>` with ‹ Review plan and Next task; the Plan summary folded | built (29 Oct 2026, change `review-plan-and-question-window`) | `wider plan [--json]`; Review plan |
| Question window: the open questions about the page in view as a chat at the bottom right (consequences, the suggested answer bordered, a value box, Later, Change through undo, minimize, close to the pill); ask mode keeps the dialog for other pages | built (29 Oct 2026, change `review-plan-and-question-window`) | `wider questions list --page`, `wider questions answer`; every page with questions |
| Research question and notes | built | Research question task (no models or protocol since C3) |
| Seed papers by DOI list, pasted titles, CSV or RIS; development and validation split with a suggested random split | built | `wider benchmark` |
| **Prior reviews**: "Do you know of previous or relevant reviews?"; add reference exports, pasted DOI or title lists, or the review PDF; parsed locally; policy box visible; two opt-in switches (identifier lookup on OpenAlex, excerpts in LLM prompts), both off by default | built (10 Oct 2026, WP9) | `wider prior-reviews`; Prior reviews task |
| Databases: platform catalogue with descriptions, export formats and caps; order = development first, then validation | built | `wider query`, UI |
| LLM and embedding backends: add, probe, set default and per-stage overrides; keys by environment variable name only | built | `wider providers`, `wider embed` |
| Backend presets (CLI, cloud, local, embeddings) and **Find backends on this computer** (nothing leaves 127.0.0.1); add from a preset, edit that keeps other settings, replace only after a warning; model lists | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) | `wider providers presets\|detect\|add --preset\|edit\|models`, `wider embed edit`; Model for query creation and Settings (C3) |
| Codex, Gemini CLI (experimental) and Antigravity CLI (experimental) as CLI backends; Jev (TypeSafe AI) as a screening-only backend | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) | `wider providers add --preset` |
| Consent before contacting a cloud, local-network or unknown backend, per operation (models, probe, research, and passages for seed-paper mining), with revoke | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) | `wider providers\|embed consent\|allow\|revoke` |
| Sensitive headers and `extra` values only as `{env:NAME}` references | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) | `wider providers add\|edit` |
| Review protocol: every methodological setting with the original value as default and an explanation | built; card on Settings (C3) | `wider protocol show`, `set`, `explain` |
| **Model for query creation** page: three guided steps the first time (where it runs, which model, check it), a card grid once a model is set; add, preset, find on this computer, consent, Check; the choice sets the construct backend and the project default | built (15 Oct 2026, C3 `unify-stage-pages`) | `wider providers add\|stage\|set-default\|probe` |
| Adjacent literatures that could contaminate the results | built | `wider construct adjacent` |
| Summary step writes everything to `project.yaml` | built | |
| Doubt handling preference: queue questions or ask immediately (section 9) | built (WP14) | `wider questions mode`; Questions page |
| Refinement questions after a dry run, seed mining, a diagnosis or an import (ten kinds, at most 5 open and 10 per block per round, the rest waiting), each with why, each answer's consequence and the default; one answer is one undoable change; a decision ledger of every answer and dismissal; a round-stop question when answers stop moving recall | built (change `seed-paper-mining-interview`, 20 Oct 2026; [`refinement-interview`](../openspec/specs/refinement-interview/spec.md)) | `wider questions list\|answer\|dismiss`, `wider decisions list\|export`; Questions page, the ask dialog, the Decisions list |
| Candidates from the seed papers: the works the Development seeds cite and the works citing them (OpenAlex, under the DOI consent; Validation seeds never sent), each added or dismissed by the researcher | built (change `query-checking-provenance`, Oct 2026) | `wider benchmark chain`, `wider benchmark candidates`, `wider benchmark add --from-candidate`; step 2, Candidates from seed papers |
| Candidates ranked by links to the seeds (direct links only; a paper with one link may still be relevant), with their round; snowball rounds from the papers added from candidates, and the stop notice when a round adds nothing | built (change `papers-beyond-the-query`, 23 Oct 2026; [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)) | `wider benchmark candidates [--round K]`, `wider benchmark chain --from-candidates`; step 2, Candidates from seed papers and other tools |
| Candidates from a file of any tool (RIS, CSV, BibTeX; citationchaser, ResearchRabbit, Scopus "Cited by", Elicit and others), read on this computer with nothing sent; each import journalled and dismissable | built (change `papers-beyond-the-query`, 23 Oct 2026; [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)) | `wider benchmark candidates --import FILE --origin LABEL`, `--dismiss-import N`; Import candidates, Dismiss this import |
| Reference lists of the Development seeds: OpenAlex first, the seed's PDF as the fallback, the entries the PDF has and OpenAlex lacks (their DOIs become candidates); parsing tiers GROBID (a server on this computer only), Crossref (the parsed title only, fuzzy until confirmed) and a model (only what the entry says) | built (change `papers-beyond-the-query`, 23 Oct 2026; [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)) | `wider benchmark references [ID] [--route] [--pdf] [--tier]`, `wider seeds policy grobid-url`; step 2, Reference lists of the development seeds; Settings, Seed papers |
| Model passes on any backend kind (local, API, CLI agent), each with a verbatim check, the backend kind in its run record and its own `wider costs` stage (`missed_names`, `ref_structure`); without a backend the result without the model, said | built (change `papers-beyond-the-query`, 23 Oct 2026; [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)) | `wider query missed-names --model`, `wider benchmark references --tier model`, `wider costs` |

### `construct` Concepts and synonyms

Spec: [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)

| Feature | Status |
|---|---|
| LLM proposals for cells, codebook, synonym blocks, anchors, near-miss texts, grounded in the development papers | built |
| Versioned artifacts, edit, approve, freeze, diff between versions | built |
| Adjacent-literature loop: name, propose from benchmark-free clusters, approve, use in sampling and screening labels | built |
| Optional titles from prior reviews in the proposal prompt (only when the excerpt switch is on) | built (10 Oct 2026, WP10) |
| Unlock a frozen query (copy it into a new draft; saving after a freeze does the same); final, listed in History and in `unlock.json`; when validation papers were shown, the researcher chooses each time whether they count as seen or stay as validation, recorded and stated in every output | built (change `query-refinement-loop`, phase A, 19 Oct 2026; the choice: change `unlock-seen-choice`, 30 Oct 2026; [`query-generation`](../openspec/specs/query-generation/spec.md)): `wider construct unlock synonyms N --count-seen\|--keep-validation`; Unlock on the Construct workbench |
| Freezing another version, or looking at the validation set early, asks whether the papers shown count as seen; the choice is recorded and stated in every output | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`query-generation`](../openspec/specs/query-generation/spec.md), [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `--count-seen` / `--keep-validation`; Count them as seen / Keep them as validation |
| Seed paper source (`prior_review`, `researcher`, `colleague`, `other_database`, `query_results`, `seed_chain:<id>`); a Validation paper from the query's results is kept but not counted; one matching an imported record is added with a warning | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`construct-workbench`](../openspec/specs/construct-workbench/spec.md)): `wider benchmark add --source`, `wider benchmark source`; Seed papers task |
| A PDF per seed paper, kept in `seeds/<id>.pdf` (gitignored, never exported, only its hash recorded); text extracted locally into sections and a parsed reference list | built (change `seed-paper-mining-interview`, 20 Oct 2026; [`seed-paper-mining`](../openspec/specs/seed-paper-mining/spec.md)): `wider benchmark pdf add|list|remove`, `wider seeds extract`; Seed papers task, full text column; Names from seed papers card |
| Candidate names mined from the seed texts (Keywords line, "also called" phrases, quoted and recurring phrases, title co-occurrence; validation seeds never) and by a model whose quote must be verbatim (one passage per call; a backend that is not local only under the passages switch, default on, and its `passages` consent: `wider seeds policy`, Settings "Seed papers"); each name's dry-run gain ("retrieves B002" or "not admitted: retrieves no development seed"); adding a name to a block as a new draft, with a note when it gains nothing, journalled and undoable unless it unlocked the query; admitted names offered as suggested edits | built (change `seed-paper-mining-interview`, 20 Oct 2026; [`seed-paper-mining`](../openspec/specs/seed-paper-mining/spec.md)): `wider seeds mine [--no-model]`, `wider seeds names [--admit --block --note]`; Names from seed papers card, Add to block |
| Name map (same construct, look-alikes) as JSON and SVG; name-by-venue table of the seeds' reference lists with stream labels (journalled, in the package as `streams.json`) | built (change `seed-paper-mining-interview`, 20 Oct 2026; [`seed-paper-mining`](../openspec/specs/seed-paper-mining/spec.md)): `wider seeds names --map`, `wider seeds streams [--label]`; Names from seed papers card |

### `query` Search strings

Spec: [`query-generation`](../openspec/specs/query-generation/spec.md), [`query-dry-run`](../openspec/specs/query-dry-run/spec.md), [`search-diagnostics`](../openspec/specs/search-diagnostics/spec.md)

| Feature | Status |
|---|---|
| Render approved synonym blocks to Scopus, Web of Science, EBSCO, ProQuest, Ovid, PubMed, Google Scholar with paste instructions | built |
| Linter; import of an existing search string | built |
| Freezing the synonyms opens the validation set | built |
| Freezing an older version makes it the frozen query (newer drafts are kept and named) | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`query-generation`](../openspec/specs/query-generation/spec.md)): `wider construct freeze synonyms N` |
| Search-strings archive in the exports (every rendered string with date and platform) | built (WP14); `wider query archive`; automatic on freeze |
| Google Scholar notes name the blocks left out of the string (excluded or proximity-only) and say when no string can be built | built (15 Oct 2026, C3 `unify-stage-pages`): `wider query render`; Search strings page |
| Dry run: the query read against the seed papers' titles, abstracts and keywords under a per-database profile (fields, minimum stem, truncation in phrases, proximity); each seed `hit`, `miss` or `uncertain` with the reason; nothing is searched | built (change `query-refinement-loop`, phase B, 19 Oct 2026; [`query-dry-run`](../openspec/specs/query-dry-run/spec.md)): `wider query dry-run`; Search strings page, Dry run card |
| Truncation report per term (words each `*` matched, what a longer or shorter stem keeps or loses), term-by-seed grid, minimal cover, terms that match no seed, heatmap and per-database coverage plot | built (phase B): `wider query dry-run --terms`, `--grid`; Dry run card |
| Rounds: one per synonyms version, with development recall, record count (imported, or typed and marked pasted) and the recall-versus-yield plot; an export of an earlier draft attaches to its round | built (phase B): `wider query rounds`, `wider query count`, `wider import run --round`; Rounds card |
| Accepting a round states each seed it loses against the round accepted before and does not block; a freeze accepts its round implicitly | built (phase B): `wider query accept [--check]`; Accept this round, with a confirm step when seeds are lost |
| Diagnostic report: records per block and term, records no term explains, recall per set with the exact 95% interval and the note that recall on known papers overstates recall; also in `wider coverage` and the report | built (phase B; [`search-diagnostics`](../openspec/specs/search-diagnostics/spec.md)): `wider query diagnose`; Search results page, Diagnostic report card |
| Clusters of retrieved records by matched terms, marked relevant or noise by the researcher (no record is screened by a mark); noise terms with their share of the platform's records | built (phase B): `wider query clusters`, `wider query cluster`; Diagnostic report card |
| Indexing check: a known-item string per missed seed and platform, verdict `indexed`, `not_indexed` or `unknown`, and a diagnosis "not in the database" or "indexed but not retrieved"; the strings are unverified until manual check M47 | built (phase B): `wider query indexing list`, `wider query indexing set`; Indexing check card |
| Suggested edits with computed consequences (add a name, add the keywords field, change a stem, drop a noise term), ranked by seeds gained and, over the record budget (`identification.record_budget`, default 5000), by records saved per seed kept; applying one writes a new draft | built (phase B): `wider query edits`, `wider query edit apply`; Suggested edits list with Apply |
| OpenAlex exports diagnosed under a profile kept next to its renderer (title and abstract only, Near as AND, stemming uncertain, `*` only in the exact field, no truncation in a phrase), also when OpenAlex is not a chosen database; OpenAlex stays out of the platform list and the renderings; values unverified until hand check M131 | built (change `openalex-profile-and-sampled-exports`, 31 Oct 2026; [`query-dry-run`](../openspec/specs/query-dry-run/spec.md), [`search-diagnostics`](../openspec/specs/search-diagnostics/spec.md)): `wider query dry-run --platform openalex`, `wider query diagnose`, `wider query edits`; Dry run card (OpenAlex unticked), Diagnostic report card |
| An export marked as a random sample of N results (journalled, undoable); the budget, the report's counts, noise, clusters and the records an edit removes become estimates ("about 156,000 records (300 in the sample)"), edits rank by estimated records saved with a 95% interval for a single sample; recall not measured on a sampled platform; the report's database line and the package manifest name the sample | built (change `openalex-profile-and-sampled-exports`, 31 Oct 2026; [`search-diagnostics`](../openspec/specs/search-diagnostics/spec.md), [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider import sample <id\|file> --of N\|--clear`, `wider import list`; Search results files table, *Random sample of* |
| Names from connected papers the query misses: shared words of chaining candidates linked to several seeds that the dry run misses, each with "turns N connected candidates into hits, +k development seeds"; Validation and dismissed candidates left out; added only with a note | built (change `papers-beyond-the-query`, 23 Oct 2026; [`query-generation`](../openspec/specs/query-generation/spec.md)) | `wider query missed-names [--model]`, `wider seeds names --admit`; Search results, Diagnostic report, Names from connected papers |
| Limits applied in a database (dates, languages, reason) kept with the string, in History and in the package's `limits.json` | built (phase B): `wider query limits`; Limits applied in this database, under each string |
| Keywords field per block, with the field code of each platform from the catalogue; every code is still unverified and each string says so; seed keywords typed or filled from an import | built (phase B): `wider query render --blocks`, `wider benchmark keywords`; block list on the Search strings page; field codes checked by hand in M46 |
| While the validation set is hidden, an import copies no keywords onto a Validation paper and does not name it, and the seed list repeats "the query already finds it" only once the set is shown | built (phase B): `wider import run`, `wider benchmark list`; Setup page, seed-papers step |
| Translation ledger under each string: what the platform cannot say (`lost`), says more widely (`wider`) or has not been verified (`unverified`), "nothing lost" otherwise; kept next to each archived string | built (change `query-checking-provenance`, Oct 2026; [`query-checking`](../openspec/specs/query-checking/spec.md)): `wider query ledger`, `wider query render --ledger`; under each string on the Search strings page |
| Linter with one platform's limits (strictest without a choice), a wildcard after a hyphen, a term covered by a truncation | built (change `query-checking-provenance`, Oct 2026): `wider query lint --platform`; Linter, Platform limits select |
| Import of a string from EBSCOhost, Scopus, Web of Science, ProQuest, Ovid or PubMed syntax, after a check with positions (unbalanced parentheses refused; mixed AND/OR, curly quotes, lower-case operators, needless parentheses, NEAR over a grouped AND, nested field tags warned); render then parse gives the query back | built (change `query-checking-provenance`, Oct 2026): `wider query parse --platform`, `wider query import --platform`; Import a string (Preview, Save) |
| Second reader: one or two model backends read each string against the PRESS items; findings agreed by both runs or "unsure", a quote not in the string dropped, "same model twice" / "same family twice" stated; a CLI agent with tools refused; findings change nothing | built (change `query-checking-provenance`, Oct 2026): `wider query review [--reader NAME]`, `wider providers stage review NAME [NAME2]`; Second reader card, Settings "review" row |
| Replay of a round with no model and no network: the same result, or which part differs | built (change `query-checking-provenance`, Oct 2026; [`search-provenance`](../openspec/specs/search-provenance/spec.md)): `wider query replay <round>`; **Replay** on each round |
| Provenance of a round: its decisions, the model calls that shaped the query (hashes only) and the outside requests with the switch that allowed each | built (change `query-checking-provenance`, Oct 2026): `wider query provenance <round>`; **Provenance** on each round, Provenance card |
| Yield of each search term (works one term finds on OpenAlex), under the search-term switch; a cached count says so with its date, a failed request reads "not available" | built (change `query-checking-provenance`, 23 Oct 2026; [`online-lookups`](../openspec/specs/online-lookups/spec.md)): `wider query yield <term>`, `wider lookups terms allow\|revoke`; Yield column with Count on the Search strings page |
| OpenAlex check of the whole query: the string sent with its translation ledger, a count labelled as not comparable with a term count; with `--seeds`, which Development seeds OpenAlex finds beside the dry run (no Validation seed's DOI sent; needs the term switch and the DOI consent) | built (change `papers-beyond-the-query`, 24 Oct 2026; [`online-lookups`](../openspec/specs/online-lookups/spec.md)): `wider query yield --query [--version V] [--seeds]`; OpenAlex check on the Search strings page |

### `import` Search results

Spec: [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md), [`prior-reviews`](../openspec/specs/prior-reviews/spec.md)

| Feature | Status |
|---|---|
| Import RIS, CSV, tab-delimited, XLS/XLSX, BibTeX per platform; each file once by hash | built |
| Export sanity check per file: seed paper hits, query-vocabulary overlap, export caps, duplicates, verdict in plain words | built |
| Seed paper coverage per platform and corpus, development set while editing, validation set after freeze | built |
| Validation set after an unlock: papers seen stay in Validation as "Validation, used" (change `validation-reuse`), only papers added since count; validity note in the report, methods draft and `manifest.json` | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider coverage`; Search results, Recall card |
| Used validation papers reported apart ("Validation, used", own recall, not independent), not development seeds; a revision after a kept look warns and records them as used (`validation_used`, final) | built (change `validation-reuse`, 20 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider coverage`, `wider construct save --confirm`; Search results, Construct page |
| Moving a shown validation paper to Development warns, is final (`validation_moved`) and is reported as a former validation paper; a never-shown move is undoable (`benchmark_set`) | built (change `validation-reuse`, 20 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider benchmark set\|split --confirm`; Setup page, seed-papers step |
| Turning validation hiding back on records the papers then in the validation set as shown (kept as validation, final `validation_peek`) | built (change `validation-reuse`, 23 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider protocol set\|reset`; Settings, protocol table |
| Move all used validation papers to Development, then an invitation to add new validation papers from independent sources | built (change `validation-reuse`, 20 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider benchmark move-used --confirm`; Setup page, seed-papers step |
| Choose which imported files enter deduplication (by file id); excluded files counted as "Records not used (file excluded)" | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider import include\|exclude`, `wider import list`; Search results checkbox |
| Prior-review coverage: which papers of the prior reviews did the search retrieve | built (10 Oct 2026, WP10): `wider prior-reviews coverage`; Search results page |
| Citation chaining backward and forward from prior-review papers through OpenAlex, exported as a new input file; only with the lookup switch on | built (10 Oct 2026, WP10): `wider prior-reviews chain`; Search results page |
| Query version recorded per uploaded file; list of uploads | built (15 Oct 2026, C3 `unify-stage-pages`): `wider import list`; `GET /api/import/sources` |
| **These uploads are final**: marks the uploads final (journalled and undoable; a Redo is checked as marking is), which also unlocks the validation set | built (15 Oct 2026, C3 `unify-stage-pages`): `wider import final`; Search results page |
| A file added after the final mark reopens the uploads (recorded as an unlock; asks first whenever validation papers were shown, change `unlock-seen-choice`) instead of being refused | built (change `query-refinement-loop`, phase A, 19 Oct 2026; [`import-and-coverage`](../openspec/specs/import-and-coverage/spec.md)): `wider import add\|run`, `wider prior-reviews chain`; Search results page |
| Upload verdict shown as text; coverage switch buttons in a toolbar under the heading | built (15 Oct 2026, C3 `unify-stage-pages`) |

### `dedup` Deduplication

Spec: [`deduplication`](../openspec/specs/deduplication/spec.md)

| Feature | Status |
|---|---|
| DOI, exact title, corroborated fuzzy title; conflict-aware clustering; masters with field provenance | built |
| Re-run safe for living reviews: stable record ids, downstream rows re-mapped on merge, refused with a clear message on conflict | built |
| Manual queue of pending pairs with decisions saved | built; searchable table and side-by-side drawer with keys (WP13) |
| PRISMA counts and audit CSVs | built |
| Uploads card: platform, file, records, imported at, query version, read-only, with a link back to Search results | built (15 Oct 2026, C3 `unify-stage-pages`): `wider import list`; Deduplication page; since A1 the list of the box "Records imported" |
| Flow chart of the counts (records imported, duplicates removed by WIDER and by your decisions, pairs to decide, records left, provisional while pairs are open); each box filters the list | done in A1 (18 Oct 2026, `design-pass-a1-dedup`): `wider dedup prisma`, `wider dedup queue`, `wider dedup merged`, `wider dedup records`; `GET /api/dedup` `flow` |
| List beside a large docked pair view: match words and marked differences, big Duplicate / Not duplicate / Unsure buttons with keys 1 2 3, j/k, Esc back to the list, decided pairs kept with their signer, read-only merged pairs and records left | done in A1: `wider dedup queue --all`, `wider dedup decide` |
| Step notebook behind the Notebook button and the pair's history under the pair view, with undo, redo and an earlier run's result | done in A1 ([`history-and-undo`](../openspec/specs/history-and-undo/spec.md)): `wider notebook dedup [--pair A B]`; `GET /api/notebook/dedup` |
| Guide panel behind the Guide button (what the step is for, what to do now, done when, watch out, with the project's notices first) | done in A1 ([`step-guide`](../openspec/specs/step-guide/spec.md)): `wider guide dedup [--json]`; `GET /api/guide/dedup` |
| Methods page: why WIDER works this way, fourteen topics in three layers (In short, Why, For auditors) and a glossary with synonyms; each topic says whether the author reviewed it; guide panels and Help link to it; a test sends a topic back to draft when its code or settings change | built (29 Oct 2026, change `methods-explainer`, archived 30 Oct 2026; spec [`methods-document`](../openspec/specs/methods-document/spec.md)); all topics and the glossary reviewed by the author (30 Oct 2026) | `wider guide methods [<topic>\|glossary] [--json]`; `GET /api/methods`, `GET /api/methods/<topic>`; `scripts/methods_review.py stamp <topic>` |
| Ask WIDER as the second tab of the side panel beside the guide (top-bar button, tabs by arrow keys, one side panel at a time with the record drawer, `#ask` for old links; "Asking about:" the page shown or WIDER in general, the one name of the app, with a "Your name" field only while none is set; owner feedback f-20261031-112044-639f and f-20261031-131750-1737): questions about WIDER answered from its own documents (guide, Methods, glossary) with sources, Show links that never press, copy-only drafts; never reads records, labels or notes; own consent with "Remember my choice"; conversation per reviewer on this computer, forget and "Didn't help"; Settings budget card | built (31 Oct 2026, change `guide-assistant`, not archived yet; the spec is under [`openspec/changes/guide-assistant/specs/`](../openspec/changes/guide-assistant/specs/guide-assistant/spec.md) until the archive) | `wider assistant ask\|history\|unhelpful\|forget\|budget\|index\|check-retrieval`; `POST /api/assistant/ask`, `GET`/`DELETE /api/assistant/history`, `POST /api/assistant/unhelpful`, `GET`/`POST /api/assistant/budget` |

### `enrich` Missing abstracts

Spec: [`abstract-enrichment`](../openspec/specs/abstract-enrichment/spec.md)

| Feature | Status |
|---|---|
| Missing abstracts from OpenAlex, Crossref, Europe PMC, Semantic Scholar; never overwrites; resumable | built |
| Online-lookup consent before the first request; failed lookups retryable (`--retry`, audit kept); batch size and **Retry failed lookups** on the page | built (11 Oct 2026, C1 `fix-defects`): `wider enrich --limit/--retry`; Missing abstracts page |
| Source counts on the Missing abstracts page in words; consent dialog opens on Cancel and returns the focus | built (11 Oct 2026, `fix-defects-followup`): Missing abstracts page |
| `S2_API_KEY` named (set or not set, never its value) with what happens without it | built (15 Oct 2026, C3 `unify-stage-pages`): `wider enrich`, `wider enrich --status`; Missing abstracts page |

### `rank` Ranking

Spec: [`anchor-ranking`](../openspec/specs/anchor-ranking/spec.md)

| Feature | Status |
|---|---|
| Corpus embeddings cached per model; anchor scoring per cell; sorts, never filters | built |
| Benchmark validation (median rank, recall at K), ablation across anchor versions and models, clustering; asking for the validation set before it unlocks is an early peek, warned and recorded as in coverage | built (early peek: 15 Oct 2026, C3 `unify-stage-pages`) |
| Top-N limited to 1..number of cells (default lowered when there are fewer cells; a wrong value refused, also on the page) | built (11 Oct 2026, `fix-defects-followup`): `wider rank run --top-n`; Ranking page |
| Embedding model picker (card grid) at the top of the page; readable ablation headers; **Include the validation set** checkbox once unlocked | built (15 Oct 2026, C3 `unify-stage-pages`): `wider embed set-default`, `wider rank ablate`; Ranking page |
| Ranking versions: each run kept as `<name>@vN`, samples keep the version they were drawn from, "N versions" fold; "needs review" when records or settings changed, with a box per sample (Keep only for a labelled development sample; Draw again or Draw a fresh sample otherwise), Keep this ranking / Keep version N (undoable, in the methods draft), Update ranking with the re-embedding note and Rank with the older embeddings, also on the Overview | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026): `wider rank update [--use-older-embeddings] [--allow-download] [--yes]`, `wider rank keep [--version N]`, `wider rank show` |

### `sample` Samples and labelling

Spec: [`sampling-and-labelling`](../openspec/specs/sampling-and-labelling/spec.md)

| Feature | Status |
|---|---|
| Band-stratified probability sample with weights; purposive development set; blinded queue | built |
| Validation plan: allocations over the bands with the margin, the chance of a clear answer and sensitivity rows; your own allocation; no default margin | built (change `validation-sample-planner`, 24 Oct 2026): `wider validation plan`; Samples page "Plan the validation sample" |
| Criteria state on every label (codebook version, frozen, after a look); criteria-change assessment with the affected labels and a recommendation; blind, shuffled re-check and second-reviewer queues; revisions kept | built (change `validation-sample-planner`, 24 Oct 2026): `wider label criteria`, `recheck`, `second-reviewer`, `revisions`; sample card, drawer modes |
| Extend the probability sample after a look (suggested or by band), and a fresh sample that leaves the used one and the development sample out | built (change `validation-sample-planner`, 24 Oct 2026): `wider validation extend`, `wider sample draw --fresh-from`; sample card, Recall page |
| In-browser labelling with blind mode, second reviewer, agreement, adjudication, lock; CSV and Rayyan round trip | built |
| Record drawer, keyboard-first labelling, notes and tags, disagreement view | built (WP13) |
| Boundary pool in the development sample (records close to both a criterion and a near-miss or adjacent entry, `per_boundary`), and adding it to an unlocked purposive sample with labels kept, records shuffled among the unlabelled ones, counts only, undo | built (24 Oct 2026, change `screening-config-comparison`): `wider sample extend NAME --pool boundary`; "Add boundary cases" on the Sampling page |
| Blinded independent review: a review package per reviewer (one self-contained HTML page with the records, the abstract the model judged and the criteria the first reviewer used, no other judgment and no network request), said once that it leaves the computer when sent; the reviewer's decisions file imported as one undoable change signed as the package's reviewer; earlier labels retained, re-reviewed or excluded; withdraw with a reason; the reviewer-versus-reviewer table with each side's details; every abstract version kept and named on each label and model call; blinded review in the report, methods, bundle, Results dashboard and Recall | built (26 Oct 2026, change `reviewer-roles-and-blinding`; [`blinded-review`](../openspec/specs/blinded-review/spec.md)): `wider blinding package\|package-file\|import\|status\|withdraw\|disagreements`, `wider enrich --versions`; "Review packages" card on Samples and labelling |
| Locked samples: a blind merged queue, unlocks named in the methods text and the package; the page says who signs and a re-check queue is only for its own name | built (25 Oct 2026, change `manual-review-workbench`): `wider sample lock\|unlock`; Samples and labelling page |
| Locked samples are labelled to the end: missing labels save at once, a change to a saved label or adjudication is a correction with a reason (kept with old and new values, undoable); recall is revealed only when every sampled record has a final label and no package is out; the first look seals each label as its reference, a later correction moves it and adds one line to every result; each label records how it was made and older or imported labels count as independent only with a stated basis; development evaluations and comparisons say when labels changed after they ran; the share bundle carries `reference_labels.csv` and `label_corrections.csv` | built (28 Oct 2026, change `label-completion-and-sealing`; [`sampling-and-labelling`](../openspec/specs/sampling-and-labelling/spec.md), [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md), [`history-and-undo`](../openspec/specs/history-and-undo/spec.md), [`blinded-review`](../openspec/specs/blinded-review/spec.md), [`record-drawer`](../openspec/specs/record-drawer/spec.md), [`results-dashboard`](../openspec/specs/results-dashboard/spec.md), [`llm-screening`](../openspec/specs/llm-screening/spec.md), [`interactive-demo`](../openspec/specs/interactive-demo/spec.md); archived 28 Oct 2026): `wider label import\|adjudicate --correction-reason`, `wider label provenance --independent`, `wider label revisions`, `wider history list --kind label_correction`; Labelling queue, drawer correction dialog, Recall page |
| One population for recall: the estimate, its exact bounds and the verdict all count a band as it was at the draw or last extension, so records another sample takes later stay in the band as records not labelled here; a result stored before says to estimate again (no new look). Labels saved after the seal that disagree with the reference are counted (counts only) on every recall result, with the agreement and correction commands; the package report line says when labels were used for agreement only | built (31 Oct 2026, change `recall-final-check-fixes`; [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md), [`results-dashboard`](../openspec/specs/results-dashboard/spec.md)): `wider recall`, `wider validation status`, `wider label adjudicate --correction-reason`; Recall page, dashboard chart, report |
| The development sample is complete before model output on it: screening runs, evaluations, comparisons and replays that include its records are refused (exit 1, API 409) with the records left; earlier model decisions hidden, not deleted; exports with model decisions wait; `sampling.development_reviewers` (1 or 2) with its effect said before saving; Continue labelling on Screening and the Overview | built (29 Oct 2026, change `ui-redesign`, archived 31 Oct 2026): `wider protocol set sampling.development_reviewers 2`, `wider screen run` refusal, `wider status` item |

### `screen` Screening

Spec: [`llm-screening`](../openspec/specs/llm-screening/spec.md)

| Feature | Status |
|---|---|
| Codebook to prompt and schema; verbatim quotes; recall-first rule; two passes, blind third read, majority; second screen of maybes | built |
| Development evaluation against the human labels, misses per adjacent literature | built |
| Offline replay from stored responses | built |
| Live progress with ETA and cost, background run with notification | built (WP14) |
| Disagreement view LLM versus human with record drawer | built (WP13) |
| Per-call backend provenance, confirmation when the backend changes during a run, mixed-model marks, which evaluation made each final; Export CSV; Models used | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) |
| Result sets: a rerun never replaces a finished protocol; new set on request, reuse, use for analysis, compare | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`) |
| One model picker (card grid) at the top with locality and a cost line; record limit explained where it is set | built (15 Oct 2026, C3 `unify-stage-pages`): `wider screen run --provider/--limit`; Screening page |
| Configuration comparison on the locked development sample: backends, prompt versions and temperatures, repeated, scored against the final labels (min–max misses, false includes, consistency, per pool and area, cost); no winner; `comparison.csv` | built (24 Oct 2026, change `screening-config-comparison`): `wider screen compare run\|show\|list`; Compare configurations card |
| A backend per pass: pass B and the judge (and the second screen's S2 and S3) on their own backend, stage keys `screen_b` and `screen_judge`, cost and consent per pass, the per-pass backend in the result set's settings, mixed judged per pass, the shared-model sentence | built (24 Oct 2026, change `screening-config-comparison`): `wider screen protocol\|second --provider-b --provider-c`, `wider providers stage screen_b\|screen_judge`; Settings screen row, Screening page pass selects |
| Where screening stands: steps in words, one next step (Recall only when the required steps are completed), rounds named in words with kind and test-model badges, unusual results explained, technical details folded; three task tabs; status words completed / in progress / needs attention / not started in the sidebar and every page chip | built (25 Oct 2026, change `screening-page-tasks`): `wider screen status [--json]`; Screening page |
| Decisions from an earlier screening imported as their own result set (DOI, then title; unlisted records exclude by default); when several rows match one record the first counts, later ones are counted as duplicates and a differing one is named with both rows | built; duplicates since change `import-decisions-duplicate-rows` (25 Oct 2026); each import listed in History, final, and imported sets counted apart in the model-selection line since change `import-history-and-model-selection` (28 Oct 2026, [`llm-screening`](../openspec/specs/llm-screening/spec.md), [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md)): `wider screen import-decisions`, `wider history list --kind decisions_import` |
| An outside adjudicator's decisions for some records imported into a new active result set built from the shown set (records without a decision keep theirs; rows that cannot be used are listed with the reason; a dry run first; the adjudicator recorded and written by the attribution setting in shared files, also the screening decisions CSV's reasons); one History change that undo removes, refused once the set is used | built (30 Oct 2026, change `partial-adjudication-import`, [`llm-screening`](../openspec/specs/llm-screening/spec.md): "Importing an adjudicator's decisions for some records", "An adjudicator import is listed in History and can be undone"): `wider screen import-adjudication`, `wider history list --kind adjudication_import`; Results page card "Import adjudicator's decisions" |
| Screening review: the records the screening left to a person (maybes, splits, failures, unresolved third reads) with each pass's read and the criteria beside them, signed decisions written into the result set, undo, quality-control samples of settled records, pending reviews on the Screening page and Home, interventions in the report and methods; the second Screening tab is "2 Decisions and result sets" | built (25 Oct 2026, change `manual-review-workbench`; [`manual-review`](../openspec/specs/manual-review/spec.md)): `wider review`; Screening review page |

### `recall` Recall and report

Spec: [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md), [`results-dashboard`](../openspec/specs/results-dashboard/spec.md)

| Feature | Status |
|---|---|
| Stratified recall estimate with bootstrap and linearised intervals | built |
| Recall interval for unequal band weights (approximate: Korn–Graubard, Kish effective size at 0 and 1, checked by simulation, `scripts/recall_interval_sim.py`), never a single point; "uncertainty could not be reliably estimated" when no interval holds (a sampled band without a relevant paper, one labelled record, too few relevant papers); the number of relevant validation papers with a note when few, a caution at perfect observed recall, a note on unadjudicated reviewer disagreements and one method sentence, the same sentences on every surface; bootstrap (at most 50,000 replicates) and linearised intervals as cross-checks in the report's "Recall: method" | built (change `recall-interval-honest`, 24 Oct 2026): `wider recall`, `wider report`; Recall page |
| Report with every number regenerated from the store; share bundle without abstracts | built |
| PRISMA 2020 diagram (SVG) and a methods paragraph in the researcher's words | built (WP14); `wider export prisma`, `wider export methods` |
| Reference-manager exports of the included set (RIS, BibTeX, CSV) | built (WP14); `wider export references` |
| Technical appendix and replication package from the project | built (report; package WP14, `wider export package`) |
| PRISMA-S search report: the 16 items filled from the store, "not recorded" where WIDER has nothing | built (change `query-checking-provenance`, Oct 2026): `wider export prisma-s`; PRISMA reporting page, and **PRISMA-S** on the Recall page's PRISMA report card (D32 F17) |
| Recall target and rule set first, timed; three outcomes (met, not met, inconclusive; only not met drawn as a warning) from exact anytime bounds; suggestion of more labels when the answer is open; stop on record; used versus not informed; benchmark papers apart; the scope line; `validation_checks` in the report (spec [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md)) | built (change `validation-sample-planner`, 24 Oct 2026): `wider validation target`, `status`, `stop`, `wider recall --sample-used/--not-informed`; Recall page outcome block |
| Workload estimate and impracticality warning: the suggestion of more labels past 300 up to a census, the census sentence, the cost of conservative bounds said once, `recall.practical_labels` (default 500) and the warning that settling the target may be impractical (information, never a refusal), the inconclusive end of validation in the report and methods draft (spec [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md)) | built (change `recall-certification-workload`, 26 Oct 2026): `wider validation status`, `wider recall`, `wider protocol set recall.practical_labels`; Recall page "Validation checks", Settings protocol table |
| RIS, BibTeX and CSV per decision set: include, include and maybe, maybe, exclude, and by route (agreed, settled by the third read, no majority, decided by the researcher); a `decided_by` column; every set at once with `<protocol>_index.csv`, emptied sets' old files removed | built (24 Oct 2026, change `results-dashboard-and-ris`, part 1): `wider export references [--which SET \| --all]`; since change `decision-set-exports-and-found-chart` the Results dashboard's Export records card, **Every set at once** |
| PRISMA 2020 checklist: the 27 items and sub-items, items 5, 6, 7, 8, 16a, 24b and 27 filled from the store, every other item "not recorded by WIDER"; a copy in the replication package | built (24 Oct 2026, change `results-dashboard-and-ris`, part 1): `wider export prisma-checklist`; PRISMA reporting page, and **Checklist** on the Recall page's PRISMA report card (D32 F17) |
| Report sections "Screening agreement" (pass A by pass B table, agreement with Cohen's kappa, how each decision was reached) and "Researcher interventions" (History entries per kind with authors and dates; every non-per-record entry listed, undone ones marked; labels counted only) | built (24 Oct 2026, change `results-dashboard-and-ris`, part 1): `wider report` |
| Results dashboard (an owner view outside the blind labelling queue) and `wider results`: heading with each pass's model, pass agreement, configuration comparisons apart, the stored recall lines (no new look), the relevant-found chart with its table, PRISMA counts, reference files per decision set, documents | built (24 Oct 2026, change `results-dashboard-and-ris`, part 2): `wider results [PROTOCOL] [--set N] [--sample NAME] [--json]`; Results dashboard page |
| Record export by preset (include, include and maybe by default, maybe, exclude, unresolved only, every decision), pathway, first-two-read pattern, final only and records chosen one by one; RIS by default, BibTeX and CSV optional (several as one ZIP), abstracts on or off; a count line before export; exporting changes no decision | built (25 Oct 2026, change `decision-set-exports-and-found-chart`): `wider export selection [--preset --pathway --pattern --final-only --records --format --no-abstracts --dry-run]`; Results dashboard, Export records card |
| Audit CSV of the whole result set: final decision, status, pathway, read pattern, each pass's read and model, the researcher's decision and note (no name), origin, rules and settings hash; kept on this computer | built (25 Oct 2026, change `decision-set-exports-and-found-chart`): `wider export selection --audit`; **Audit CSV** |
| Export package ZIP: Include + Maybe RIS, the audit CSV, `summary.md` and `MANIFEST.txt`, with other sets or the selection on request; kept on this computer, not in the replication package | built (25 Oct 2026, change `decision-set-exports-and-found-chart`): `wider export selection --package [--add ...]`; **Download export package** |
| Relevant-found chart with three lines (relevant, kept, missed), band boundaries, exact range bars, a tooltip per band on hover and keyboard focus, a band table under "observed" and "estimated" (also in `wider results`), "few relevant observed" below 3, a sentence instead of a too-low curve at a band not labelled yet, and **Estimate recall again** on a stale chart | built (25 Oct 2026, change `decision-set-exports-and-found-chart`): `wider results`, `wider recall`; Results dashboard page |
| Recall weights N_h/n_h over the band's labelled sampled records, so a partly labelled band is no longer counted as if its unlabelled records were not relevant | built (25 Oct 2026, change `decision-set-exports-and-found-chart`): `wider recall`; Recall page |
| Recall stage shows "done" after the report | built (WP8) |
| Report and share bundle from the page before any screening decision (protocol section says so) | built (15 Oct 2026, C3 `unify-stage-pages`): `wider report --bundle` |

### `fulltext`, `extract`, `synthesis`

Registered extension points, not built.

## 7. Record work

Spec: [`record-drawer`](../openspec/specs/record-drawer/spec.md)

For dedup pairs, labelling queues and screening review the same drawer applies.

| Feature | Status |
|---|---|
| Labelling queue and adjudication as focus pages over the whole window: dark bar with who signs and Leave, the queue's own count with a bar, the blinding note, the record card with the decisions under the abstract, the exclusion reason only after Exclude, the criteria beside it; adjudication with both signed labels above the record | built (31 Oct 2026, change `label-focus-pages`, [`record-drawer`](../openspec/specs/record-drawer/spec.md)): same commands as before (`wider label`, `wider label adjudicate`); Labelling queue, Adjudication |
| Right-hand record drawer: title, venue and year, abstract with highlighted quotes | built (10 Oct 2026, WP13) |
| Keyboard-first: 1/2/3 for decisions, n note, t tag, j/k next and previous | built (WP13); Esc closes |
| Notes and tags per record, searchable | built (WP13); `wider notes`; home-page search; blind queue shows only your own |
| Disagreement views: LLM versus human, reviewer versus reviewer, with filters | built (WP13): development evaluation by category, adjudication side by side |
| Record-level tables with search on every stage page | partly built (WP13): labelling queue, dedup pairs, screening runs, notes |

## 8. Runs, progress and costs

Spec: [`runs-provenance-and-costs`](../openspec/specs/runs-provenance-and-costs/spec.md)

| Feature | Status |
|---|---|
| Per-run cost and token count shown with each result | built (WP14) |
| Project total cost on the home-page info button | built (WP14); `wider costs` |
| Local-versus-cloud marker on every run and project | built (WP14) |
| Destination of each backend (an Ollama on another computer names that computer; ollama.com only for a cloud-tagged model); cached-token prices; cost range before a run and projection during it (the estimate a page shows when it opens contacts nothing) | built (Oct 2026, `llm-backends` spec, `llm-backend-choice`; Ollama host and page estimate: 15 Oct 2026, C3 `unify-stage-pages`); `--estimate-only` |
| Live progress with ETA for long runs; run continues in the background; notification when done | built (WP14): screening, embedding, enrichment, proposals |
| Resumable runs after interruption | built (checkpoints) |

## 9. When the tool is in doubt

Spec: [`doubt-handling`](../openspec/specs/doubt-handling/spec.md)

Ambiguous records, unresolved DOIs, low-confidence proposals and failed parses raise a question. The researcher sets
per project whether questions are **queued** (answered later from a list) or **asked immediately** (a dialog while the
run proceeds). When asked immediately, the answer is used as much as possible: a decision is written to the record,
a resolved identifier replaces the guess, and a clarification is appended to the prompt context for the rest of the run.
Status: built (WP14) for five doubts: unusable screening answers, three-way splits in the protocol, records and seed
papers without an abstract, and failed construct proposals (`wider questions`). Deviation, decided for
reproducibility: a running screening pass keeps its prompt; clarifications enter the construct proposal prompts, and
screening clarifications go into the codebook for the next prompt version.

## 10. Outputs and exports

Spec: [`recall-and-outputs`](../openspec/specs/recall-and-outputs/spec.md)

| Output | Status |
|---|---|
| PRISMA 2020 diagram and methods paragraph | built (WP14) |
| Reference-manager exports of included and maybe sets | built (WP14) |
| Technical appendix and replication package | built (WP14) |
| Search-strings archive | built (WP14) |
| PRISMA-S report; decision ledger and per-round provenance in the replication package | built (change `query-checking-provenance`, Oct 2026) |
| Dedup audit CSVs, recall tables, labelled samples | built |

## 11. Teamwork and sharing

Collaboration works by sharing the project folder (or a share bundle without abstracts). There are no accounts and no
server. Reviewer identities are names in the project; blind mode hides decisions between reviewers until adjudication.

Spec: [`researcher-attribution`](../openspec/specs/researcher-attribution/spec.md)

| Feature | Status |
|---|---|
| A researcher list kept on this computer: every name that signed a change, declared (typed, `--by`, `--author`, `--reviewer`) or inferred (login name, `$WIDER_REVIEWER`, an imported file, an older project), with a stable pseudonym by first appearance; Confirm and "Same researcher as" as undoable changes; names of the form "Researcher n" refused | built (25 Oct 2026, change `researcher-attribution`): `wider researchers list/confirm/join`; Recall page, card **Names in shared files**; "(inferred, not confirmed)" in History |
| One setting `exports.attribution` (pseudonyms by default, real names, no names) for the report, the share bundle, the replication package and the methods draft; the mapping never leaves this computer; a name scan refuses a shared file that still holds a hidden name; no researcher name in model prompts | built (25 Oct 2026, change `researcher-attribution`): `wider researchers attribution MODE`; mode line beside **Write share bundle** and the package button |

## 12. Undo and version history

Spec: [`history-and-undo`](../openspec/specs/history-and-undo/spec.md)

| Feature | Status |
|---|---|
| Undo the last change on editable artifacts and manual decisions | built (11 Oct 2026, WP15): History page, Undo in messages, Ctrl/Cmd+Z, `wider history undo`; refused when changed since; freezes final; artifacts restored as new versions |
| Version history for artifacts, manual decisions, answers, notes, tags and protocol or settings edits | built (WP15); per-record history in the drawer; `wider history list` |
| Repeated undo goes further back; undo-last only undoes the current reviewer's changes; dismissed questions are journalled | built (11 Oct 2026, C1 `fix-defects`): `wider history undo [--author]`; Ctrl/Cmd+Z and "Undo my last change" need the reviewer name, the one name WIDER works as (top bar "Working as: name · Change", ui-redesign D33, 31 Oct 2026) |
| Page changes (also background runs and duplicate decisions) are signed only with the name given on the page, never the login name; saving an empty name with Change forgets it | built (11 Oct 2026, `fix-defects-followup`): `X-Wider-Author`; CLI `--by`/`--author` |
| While Blind is ticked, History says "in a sample" instead of the sample a labelled, corrected or adjudicated record is in, so a blind reader cannot tell development from validation records (the stored entry keeps its words) | built (30 Oct 2026, change `ui-redesign`, owner answer 37e6): `wider history list --blind`, `undo --blind` and `redo --blind`, also the Undo and Redo messages; Blind box on the History page, the same switch as the Sampling page's |
| Undoing another person's change by id asks first and is signed by the undoer; undo-last names a near match; the History "Show" list holds every kind; the undo edge cases are stated | built (25 Oct 2026, change `manual-review-workbench`): `wider history undo ID --author NAME --other`; History page |

## 13. Accessibility

English interface; keyboard reachable controls with visible focus; screen-reader labels on icon buttons; status never
conveyed by colour alone (a word or icon accompanies it); works on a 13-inch laptop without horizontal scrolling.
Status: built (WP12 shell, WP13 record drawer).

Spec: [`ui-accessibility`](../openspec/specs/ui-accessibility/spec.md)

| Feature | Status |
|---|---|
| **Accessibility and naming**: in-page dialogs instead of browser boxes (Cancel focused, Esc cancels, focus returns), focus on the heading after navigation and on the result after an action, toasts' Undo and Redo by keyboard, Ctrl/Cmd+Shift+Z redo of the name's own undo, tab strips with arrow keys, named controls and charts, one time format, pages at phone width with a Menu button, Enter in a field does what its button does; one name per page and thing (`docs/names.md`, Help's "Page names and commands" card, "seed papers") | **built** (25 Oct 2026, change `accessibility-and-naming`): `wider history redo --author NAME`, `wider stages`, `wider dedup decide unsure`; every page |

## 14. Privacy and data policy

Spec: [`privacy-and-data-policy`](../openspec/specs/privacy-and-data-policy/spec.md)

- The project folder stays on the researcher's computer. Nothing in it is uploaded by the tool.
- Added files (database exports, prior-review exports, PDFs) are parsed locally. Their contents are never sent to a
  model or a service.
- Online lookups (DOIs and titles sent to OpenAlex, Crossref, Europe PMC and Semantic Scholar to resolve seed papers
  and fetch abstracts) need per-project consent, off by default: the first lookup names the services and the fields
  sent and asks; declining sends nothing. Every outside request is logged locally (service, purpose, count, time).
  Built 11 Oct 2026 (C1 `fix-defects`): `wider lookups status|allow|revoke|log`; *Online lookups* card on Settings
  and the Seed papers and Find more seed papers tasks of the Review plan. Spec: [`online-lookups`](../openspec/specs/online-lookups/spec.md)
- Prior-review identifier lookups (resolution and citation chaining on OpenAlex) and excerpts of prior-review titles
  in LLM prompts each keep their own per-project switch, off by default and visible on the setup page.
- Search terms have a switch of their own (`wider lookups terms allow|revoke`), off by default and separate from the
  DOI consent. Seed chaining sends Development seeds' DOIs only. Repeated lookups are answered from the project's
  cache (`wider lookups cache [--clear]`), which never enters an export.
- LLM screening sends titles and abstracts of the researcher's own corpus to the backend the researcher chose; with a
  local backend nothing leaves the machine. The backend kind is shown on every run.
- The repository holds no review data; a pre-commit guard refuses data files and private paths.

## 15. Design direction (decided 10 October 2026)

Three mockups were compared: A "soft cards" (rounded cards, tinted status, icon rail), B "workbench" (labelled sidebar
with status dots, dense bordered rows, record drawer with keyboard shortcuts) and C "guided" (thin rail, stage stepper
across the top, one hero card for the current project). Decision: **A's look everywhere, B's record drawer for record
work, and the stage map in both the sidebar and a slim top stepper.**

- Shell: sidebar 168 px (icon, label, status dot per stage) collapsing to a 48–56 px icon rail; Home at the top, stages
  in pipeline order, Help and Settings at the bottom; content column up to ~1100 px with 16 px gutters.
- Home: "Your projects" with Create project; project cards with segmented progress bars and a "Next" link; info panel as
  a card; active-run banner.
- Stage page: title and status chip, run selector, slim stepper, tinted result card with the headline, metric tiles,
  collapsible bordered sections, previous-stage line, Explain button, one accent-filled Continue button.
- Record drawer: 280–320 px on the right; decision keys 1/2/3, n note, t tag, j/k navigation; disagreement filter and
  search above the table.
- Look: CSS variables for surfaces, text, borders and the accent, success, warning and danger roles, light and dark
  via `prefers-color-scheme`; 12 px card radius, 8 px control radius, hairline borders, no shadows or gradients;
  outline icons from an SVG sprite inside the page, drawn for WIDER in the Tabler outline style (no CDN); two font weights; sentence case; colour only on chips, dots, bars
  and the result card.

## 16. Checks and the demo

Each change is checked by the unit tests, a spec checker and browser testers. A scenario they cannot reach (an
online lookup, a file chosen by a person, a screen reader, a real model backend) is marked not tested, never passed,
and is checked by hand.

**Interactive demo** (**built**): a static copy of the web UI on an invented project at
`/demo` of the showcase site, with a guided tour (what each stage is for, "You decide" or "WIDER does", trade-offs,
concept cards on samples, scores and percentiles, disagreements and recall) and a Free mode. Answers come from a
recorded snapshot and the visitor's actions are simulated in the tab; no request leaves the page. Not part of the
`wider` package. Rebuilt for the current app (31 Oct 2026): the tour in
chapters with a Quick tour of 11 core steps, a Chapters dialog and a start card with three paths; no Guide; the fixed
name Reviewer A; Ask WIDER as recorded example questions (local qwen3:8b); the showcase video re-filmed from the demo
in chapters, with chapter and caption tracks. Spec: [`interactive-demo`](../openspec/specs/interactive-demo/spec.md)

**Message list** (**built**): `scripts/list_messages.py` lists every message WIDER shows to a researcher, with its
`file:line`, kind and page, and flags word-list terms, snake_case names, inline commands and raw timestamps; the
input for the plain-language pass. Spec: [`message-inventory`](../openspec/specs/message-inventory/spec.md)
