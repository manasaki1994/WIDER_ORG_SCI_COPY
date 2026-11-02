# Page names and commands

One row per page: the name the sidebar and the page heading show, its address in the browser, the stage key (from
`wider/stages.yaml`) or the internal id, the commands that do the same work for scripts, the section of
[the guide](guide.md) that explains it, and the spec capability under `openspec/specs/` that defines it. Help's
"Page names and commands" card shows the same pairs. `tests/unit/test_names.py` reads this table against
`stages.yaml`, the list of fixed sidebar pages in `wider/ui/static/shell.js` (`FIXED_PAGES`) and the guide headings,
so a renamed page fails the tests until this map follows.

## Pages

| Page | Address | Key or id | Commands | Guide section | Spec capability |
|---|---|---|---|---|---|
| Home | `#home` | `home` | `wider status`, `wider init` | [Where am I?](guide.md#where-am-i) | guided-workflow-and-shell |
| Overview | `#overview` | `overview` | `wider status`, `wider next` | [Where am I?](guide.md#where-am-i) | guided-workflow-and-shell |
| Review plan | `#setup` | `setup` | `wider init`, `wider plan`, `wider benchmark`, `wider protocol`, `wider providers` | [1. Review plan](guide.md#1-review-plan) | project-setup |
| Model for query creation | `#model` | `model` | `wider providers` | [The Model for query creation page](guide.md#the-model-for-query-creation-page) | llm-backends |
| Concepts and synonyms | `#construct` | `construct` | `wider construct` | [2. Concepts and synonyms](guide.md#2-concepts-and-synonyms) | construct-workbench |
| Search strings | `#query` | `query` | `wider query` | [3. Search strings](guide.md#3-search-strings) | query-generation |
| Search results | `#import` | `import` | `wider import` | [4. Search results](guide.md#4-search-results) | import-and-coverage |
| Deduplication | `#dedup` | `dedup` | `wider dedup` | [5. Deduplication](guide.md#5-deduplication) | deduplication |
| Missing abstracts | `#enrich` | `enrich` | `wider enrich` | [6. Missing abstracts](guide.md#6-missing-abstracts) | abstract-enrichment |
| Ranking | `#rank` | `rank` | `wider rank` | [7. Ranking](guide.md#7-ranking) | anchor-ranking |
| Samples and labelling | `#sample` | `sample` | `wider sample`, `wider label` | [8. Samples and labelling](guide.md#8-samples-and-labelling) | sampling-and-labelling |
| Screening | `#screen` | `screen` | `wider screen`, `wider review` | [9. Screening](guide.md#9-screening) | llm-screening |
| Screening review | `#review` | `review` | `wider review` | [Screening review](guide.md#screening-review) | manual-review |
| Recall and report | `#recall` | `recall` | `wider recall` | [10. Recall and report](guide.md#10-recall-and-report) | recall-and-outputs |
| Results dashboard | `#results` | `results` | `wider report`, `wider export` | [Results dashboard](guide.md#results-dashboard) | results-dashboard |
| PRISMA reporting | `#reporting` | `reporting` | `wider report prisma` | [PRISMA reporting](guide.md#prisma-reporting) | prisma-reporting |
| Questions | `#questions` | `questions` | `wider questions` | [When the tool is in doubt](guide.md#when-the-tool-is-in-doubt) | doubt-handling |
| Ask WIDER | `#ask` | `ask` | `wider assistant ask`, `wider assistant history` | [Ask WIDER](guide.md#ask-wider) | guide-assistant |
| Notes and tags | `#notes` | `notes` | `wider notes` | [Record drawer, notes and tags](guide.md#record-drawer-notes-and-tags) | record-drawer |
| History | `#history` | `history` | `wider history` | [History and undo](guide.md#history-and-undo) | history-and-undo |
| Help | `#help` | `help` | `wider guide`, `wider next` | [Where am I?](guide.md#where-am-i) | guided-workflow-and-shell |
| Settings and data | `#settings` | `settings` | `wider protocol`, `wider providers`, `wider check-backends` | [Choosing an LLM backend and embedding model](guide.md#choosing-an-llm-backend-and-embedding-model) | llm-backends |
| Methods | `#methods` | `methods` | `wider guide methods` | [Why WIDER works this way](guide.md#why-wider-works-this-way) | methods-document |

The Review plan's task pages share the key `setup` (its guide and its sidebar link) and open at their own address:

| Task page | Address | Commands |
|---|---|---|
| Research question | `#setup?task=question` | `wider init`, `wider plan` |
| Seed papers | `#setup?task=seeds` | `wider benchmark` |
| Prior reviews | `#setup?task=prior` | `wider prior-reviews` |
| Databases | `#setup?task=databases` | `wider query platforms` |
| Find more seed papers (optional) | `#setup?task=more` | `wider seeds`, `wider benchmark candidates` |

The **question window** (id `qwindow`, bottom right of a page) shows the open questions from WIDER about that page;
closed, it is the pill "Waiting for you". Commands: `wider questions list --page <page>`, `wider questions answer`.

Extension stages are registered but have no page and no commands yet:

| Page | Address | Key or id | Commands | Guide section | Spec capability |
|---|---|---|---|---|---|
| Full-text screening | none | `fulltext` | none yet | [11. Extension points](guide.md#11-extension-points) | guided-workflow-and-shell |
| Data extraction | none | `extract` | none yet | [11. Extension points](guide.md#11-extension-points) | guided-workflow-and-shell |
| Synthesis | none | `synthesis` | none yet | [11. Extension points](guide.md#11-extension-points) | guided-workflow-and-shell |

## Glossary

The one list of the words the pages use. When a page, Help or the guide needs one of these things, it uses the word
in the first column.

| Word on the pages | What it means |
|---|---|
| Concepts and synonyms | the concepts of the question and their synonyms, edited on the page of that name (older word: construct, construct workbench) |
| Seed papers | the papers you already know belong in the review (older word: benchmark papers; the file `benchmark.csv` and the command `wider benchmark` keep their names) |
| Query development set | the seed papers the search strings may be tuned on (explained in the guide) |
| Query validation set | the seed papers kept apart to check the locked search (explained in the guide) |
| Used validation paper | a validation paper already seen ("Validation, used", "Val (used)"): no longer an independent check |
| Former validation paper | a validation paper moved to development ("Dev (former val)") |
| Lock, locked | a search version that later steps use (older word: freeze, frozen) |
| Unlock | copy a locked version into a new draft; final, recorded in History with a warning that the search is no longer validated |
| Approve | later steps use this version |
| Model | the LLM or embedding model a step uses (older word: provider, backend) |
| Check | a short test call to a model (older word: probe) |
| Deduplication | removing duplicate records (author, 17 Oct 2026: keep the word, it only removes duplicates) |
| Unsure | the third deduplication decision; the CLI takes `unsure`, and `uncertain` as before |
| Missing abstracts, short abstracts | records with no or a thin abstract (older word: enrich, thin abstract) |
| Anchor sentences | one sentence per concept; ranking compares each record with them (author, 17 Oct 2026) |
| Test without one part | a run with one part left out (older word: ablation) |
| Test labels | the known answers of the practice project only (older word: oracle) |
| Labelling queue | the blind overlay where a person labels a sample's records one by one ("Labelling queue (blind)"); an overlay, not a sidebar page (older word: screening queue) |
| Screening round | one screening run: a test round or the full run, listed on the Screening page |
| Result set | the numbered set (`--set N`) a screening round's decisions go to; the Results dashboard and Screening review show one result set |
| Screening review | the page where a person decides the records the screening left to a person |

Words left out of the pages: artifact; top-N and N_h, n_h, weight (under Details only).

Older names still found in the main specs' Surfaces lines and in the change history, with today's word:

| Older name | Today |
|---|---|
| Project setup, Question and seed papers | Review plan |
| Construct workbench | Concepts and synonyms |
| Query generation | Search strings |
| Import and coverage | Search results |
| Abstract enrichment | Missing abstracts |
| Anchor ranking | Ranking |
| Sampling and labelling page | Samples and labelling |
| LLM screening | Screening |
| Recall and PRISMA | Recall and report |
| Benchmark papers | Seed papers |
| Benchmark coverage (a card and a report section) | Seed paper coverage |
| Validate on benchmark (a Ranking button), Benchmark recovery | Check the seed papers, Seed paper recovery |
| Benchmark papers apart (Recall) | Seed papers apart |
| Screening queue | Labelling queue |
| "2 Review decisions" (a Screening tab) | "2 Decisions and result sets" |
