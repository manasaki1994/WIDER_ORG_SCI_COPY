# Plain words for the UI messages (approved by the author)

The plain versions of the messages WIDER shows, page by page, from `scripts/list_messages.py` and the word list in
`docs/guide-text.md`. The author approved each batch; the texts went into the code in the design pass (A1),
together with the CLI twin, so both say the same. Placeholders in braces are filled in by WIDER.

## Batch 1: Your projects (Home), approved 17 Oct 2026

All 37 rows approved as proposed.

**Project card**

| # | Now | Plain |
|---|---|---|
| 1 | open (chip) | open now |
| 2 | 9 of 10 stages done; All 10 stages done | 9 of 10 steps done; All 10 steps done |
| 3 | the open project cannot be removed from the list (text beside the button) | You can't remove the project that is open. (tooltip only) |
| 4 | Remove from this list; the folder is not touched | Hides it here. The folder stays on your computer. |
| 5 | The list of recent projects is kept on this computer only. / … for this session only. | This list is stored only on this computer. / This list is kept until you close WIDER. |
| 6 | {n} folder{s} in the list no longer {holds} a project and {is} hidden. | 1 folder no longer holds a WIDER project, so it is hidden. / {n} folders no longer hold a WIDER project, so they are hidden. |
| 7 | {n} imported records | {n} records imported, duplicates not removed yet |

**Info panel** (the same lines are printed by `wider status`)

| # | Now | Plain |
|---|---|---|
| 8 | records: {records} unique of {raw} imported | Records: {records} after removing duplicates ({raw} imported) |
| 9 | {found}/{n} found (in at least one export) / (in the deduplicated corpus) | {found} of {n} found in your search results / {found} of {n} found after removing duplicates |
| 10 | {n} seed papers, coverage not checked yet | {n} seed papers, not yet looked for in your search results |
| 11 | LLM backend | AI model |
| 12 | {name} ({model}) … for default, construct | {model} via {name} … used for all steps / used for concepts and synonyms |
| 13 | embedding model | Ranking model |
| 14 | this computer / Local network / remote / test backend (chips) | on this computer / on your network / online / test model |
| 15 | text is sent off this computer / text is sent to {destination} / fake backend for tests (tips) | Titles and abstracts are sent to an online service. / Titles and abstracts are sent to {destination}. / A stand-in model for practice. It gives no real answers. |
| 16 | tokens: {total} ({in} in, {out} out, {calls} screening calls) | Model use: {total} tokens ({in} sent, {out} received, in {calls} screening calls) |
| 17 | no model calls yet / no model calls stored yet | No model used yet. |
| 18 | {usd} (local backends only) | {usd} (models on this computer are free) |
| 19 | {usd} so far, plus {n} calls without prices (set the backend's prices on Settings) | {usd} so far. {n} calls have no price yet: add the model's prices in Settings. |
| 20 | (logged ${x}; ${y} for {n} older screening calls, {label}) | (${x} recorded; about ${y} estimated for {n} older screening calls); {label} under Details |
| 21 | last run / nothing run yet | Last step done / No step done yet |
| 22 | Ranked {n} records against {k} anchor cells (top-2 scoring); ranking {name}. | {n} records ranked against {k} anchor sentences. (top-N and the name under Details; changed on the Ranking page) |
| 23 | backend check: not run yet / LLM … ok / embeddings … failed | Model check: not done yet / AI model … works / ranking model … does not answer |
| 24 | probing… (a local model can take a minute) | Checking… (a model on this computer can take a minute) |
| 25 | CLI: not run yet (wider check-backends) | CLI: not done yet (wider check-backends); the CLI keeps its commands |

**Create a project**

| # | Now | Plain |
|---|---|---|
| 26 | A new folder with project.yaml, an empty seed-paper file and the store, the same as wider init. The folder stays on this computer; give its full path. | WIDER makes a new folder for this review on this computer. |
| 27 | Research question(s), one per line (optional, you can add them on the Question and seed papers page) | Research questions, one per line (optional, you can add them later) |
| 28 | Databases in search order, comma-separated keys (optional; the first starts as the development platform, …) | Databases you will search, in order, separated by commas (optional); hint: WIDER builds and tests your search on the first database. You can change this later. (A1 idea: tick boxes with the names) |
| 29 | creating… | Creating the project… |

**Open a project**

| # | Now | Plain |
|---|---|---|
| 30 | Any folder that holds a project.yaml; it is added to the list below. The same as wider ui --dir <folder>. | Give the full path of a project folder. It is added to the list below. |

**Error messages**

| # | Now | Plain |
|---|---|---|
| 31 | no project.yaml in {folder}; create a project there instead | There is no WIDER project in {folder}. Check the path, or create a new project there. |
| 32 | {folder} already holds a project; open it instead | {folder} already holds a WIDER project. Open it instead. |
| 33 | the parent folder {parent} does not exist | The folder {parent} does not exist. Check the path. |
| 34 | {folder} is not empty; choose a new or empty folder | {folder} already has files in it. Choose a new or empty folder. |
| 35 | could not create the project: {error} | WIDER could not create the project. Check that you may write to this folder. ({error} under Details) |
| 36 | the open project cannot be removed from the list (error) | You can't remove the project that is open. |
| 37 | the raw problem text on a broken card | This folder can't be read as a WIDER project. (the problem under Details) |

**Noted for A1 (not words):** the left menu cuts page names short, and the subtitle under WIDER is cut too; the folder
path in code font on a grey band; databases typed as keys; the Info table mixes project state with model and cost
details.
