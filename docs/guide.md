# WIDER guide: running a review

WIDER runs a systematic review of a *broad* construct: a phenomenon that is studied under many names, in
several fields, and that no single database field catches. The procedure is fixed; what you bring is your
research question, your known-relevant papers, your databases, your LLM and embedding backends, your reviewers
and your knowledge of the literatures next door. This guide walks through the procedure one stage at a time in
the order WIDER runs it. Each section says what the stage does, what you decide, what the defaults are and why,
what to check before moving on, and which commands and UI page do it.

Conventions used below:

- Every command is run as `wider <command>` from inside the project folder, or from anywhere with `--dir <folder>`
  (`-d`). If the `wider` entry point is not on your PATH, `python -m wider <command>` is the same thing.
- Every stage has a page in the local web UI (`wider ui`, served on 127.0.0.1 only). The page offers the same
  actions as the commands; the two can be mixed freely because both read and write the same project folder.
- `wider stages` lists the stages; `wider status` shows the project and what is in its store.
- [names.md](names.md) maps each page's name to its address and commands, and holds the glossary of the page
  words.
- "Protocol settings" are the methodological constants (thresholds, sample sizes, screening reads). They live in
  `project.yaml` under `protocol:` with the original review's values as defaults. `wider protocol explain` prints
  what each one does; [protocol_reference.md](protocol_reference.md) is the same text as a document.

## Try the demo first

To see WIDER before installing it, open the interactive demo at <https://wider-showcase.vercel.app/demo/>. It is the
real web UI on an invented project: the papers are made up, every result is simulated in your browser tab from a
recording, and nothing you do there leaves the tab or reaches a model. Its start card offers three paths: the
**Quick tour** (recommended; 11 steps, about five minutes), **All chapters** (one chapter per stage of the review,
each with its own short introduction) and **Explore freely**. The **Chapters** button in the tour card lists the
chapters with what you have seen and jumps to any of them; a jump ahead does the steps in between the way the invented
Reviewer A did. All changes in the demo are signed "Reviewer A", and the Guide panel is not part of it. Ask WIDER
offers a few example questions with answers recorded from the real assistant (marked as recorded, with the model and
the date, and with their sources); you cannot type a question of your own there. Numbers in the demo illustrate how
the pages work and are not evidence about any model or method.

The showcase page at <https://wider-showcase.vercel.app/> also has a silent video that walks through the same demo
chapter by chapter, with chapter buttons, captions and the whole text as a list.

The local web UI links to the same demo: the Home page's box "New to WIDER? Try the interactive demo first." has
**Explore interactive demo**, which opens it in a new tab. The demo also shows the rule that the development sample is
labelled before the model screens: its screening run waits until the open development records are labelled, and the
page then says "Everything this step needs is done, but it waits for this: …" and offers **Continue labelling** (Skip
in the tour labels them as the invented Reviewer A did).

## 1. Review plan

For scripts: `wider init`, `wider plan`, `wider benchmark`, `wider protocol`, `wider providers`.

**What happens.** `wider init` creates a project folder with `project.yaml` (questions, platforms, backends,
protocol settings), an empty `benchmark.csv`, the folders later stages fill, and a local SQLite store. Everything
that follows is checked against what you put here.

**What you decide.**

- *The research question(s).* One or more; they go into every LLM prompt and into the report.
- *What to include and exclude*, as free-text notes. Optional, but the construct proposals and the screening
  codebook read them.
- *The seed papers*: papers you already know belong in the review. Put them in `benchmark.csv` (columns
  `benchmark_id,title,doi,set,year,first_author,note`, then `source`, `added_at`, `validation_since`,
  `validation_since_unlock`, `was_development`, `keywords` and `keywords_from`, which WIDER fills itself and which a
  file edited by hand may leave out; a DOI makes everything later more reliable). Split them
  into a **Development** set and a **Validation** set in the `set` column. Each paper records where it came from in
  `source`: `prior_review`, `researcher` (the default), `colleague`, `other_database`, `query_results` or
  `seed_chain:<id>` (`wider benchmark add --source S`, `wider benchmark source <id> S`, the source select on the
  Seed papers task page; a change is listed in History as `benchmark_source`). A paper the query's
  own results gave you can be put in Validation, but it is listed as "from the query's results: kept, not counted in
  validation", because a paper the query found cannot test the query. A Validation paper from any other source whose
  DOI or title matches an imported record is added with a warning naming that record. The development set is used to write
  the construct, tune the query and check the ranking. The validation set is held back and reported once, after the
  query is frozen, so that the search is tested on papers it was not tuned on. Keep the split roughly even, and
  do not look at the validation papers while you tune.
- *The databases* ("platforms") in the order you will search them. The first one is the development platform,
  the one you iterate on. `wider init --help` lists the catalogue keys (`scopus`, `wos`, `ebsco`, `proquest`,
  `ovid`, `pubmed`, `gscholar`, plus `citation_chain` for chained works); `init` refuses any other key. Add a
  database outside the catalogue with `wider query platforms add KEY --syntax FAMILY`.
- *The LLM backend and the embedding model*, chosen from the `providers:` and `embeddings:` lists in
  `project.yaml` on the Model for query creation page and the Ranking page (see
  [Choosing an LLM backend and embedding model](#choosing-an-llm-backend-and-embedding-model)). Setup does not need
  them: the setup steps are done without an embedding model.
- *Protocol settings*, if you want to depart from the original review (`wider protocol set <section.key> <value>`,
  `wider protocol reset` to go back; in the UI the *Advanced: review protocol* card on Settings). Any change is
  recorded in the protocol hash of every later run.

**Defaults and why.** `project.yaml` starts with four providers (a local Ollama model, an OpenAI-compatible API,
the Anthropic API, Claude Code as an agentic CLI) and five embedding models, with Ollama and the keyless local
`bge-m3` as defaults, so a project works without any API key. The protocol defaults are the values of the review
the tool was built from; the `identification` section holds the benchmark matching rule (after DOI and exact
title, a Jaro-Winkler title similarity of 0.88 counts as a fuzzy match, which coverage always flags for a look) and
the rule that the validation set is only reported after the query is frozen. `jw_confirm` (0.95) is the stricter
similarity used when imported labels or decisions are joined to the corpus by title.

**Check before moving on.** The stage counts as done when at least one seed paper is in the store and a
question is set (the Research question and Seed papers tasks of the Review plan; Prior reviews and Databases are checked when their stages run). Run `wider benchmark list` once so the store reads `benchmark.csv`, and `wider benchmark fetch`
to pull missing abstracts by DOI (OpenAlex, Crossref, Europe PMC, Semantic Scholar): the construct stage needs the
development papers' abstracts. Both lookups send DOIs and titles to outside services, so they need the project's
consent for [online lookups](#online-lookups) first. Probe your backends: `wider providers probe` and `wider embed probe
--allow-download` (or **Check models** on Settings) must both say `ok` before any stage that calls a model.

**Commands.** `wider init <folder> -q "<question>" -p ebsco -p scopus -p wos --title "<title>"`, `wider status`,
`wider stages`, `wider benchmark list`, `wider benchmark fetch [--all]`, `wider providers list`,
`wider providers probe [name...]`, `wider embed list`, `wider embed probe [name...] [--allow-download]`,
`wider protocol show [section] [--changed]`, `wider protocol explain [section.key] [--markdown]`,
`wider protocol set <section.key> <value>` (a negative value goes after `--`, e.g.
`wider protocol set -- section.key -5`), `wider protocol reset [section.key]`.
**UI:** the *Review plan* page (`#setup`) shows one card per task, each with its state (Done, To do, Waiting for
you, Optional), one line on what it holds and the count of open questions from WIDER; the line under the cards says
whether the project is ready for the construct stage. Each card opens its task on its own page
(`#setup?task=<key>`), with *‹ Review plan* and *Next task* links. `wider plan` prints the same cards. The models are
chosen on the Model for query creation and Ranking pages and the protocol is edited on Settings, so neither is a task
here:

1. *Research question* (`question`): title, research question(s), include and exclude notes.
2. *Seed papers* (`seeds`): paste DOIs or titles, upload a CSV or RIS, resolve metadata and abstracts, and split into
   development and validation sets (a suggested random split is shown with the rule explained; nothing changes until
   you apply it).
3. *Prior reviews* (`prior`): add their reference lists (a reference-manager export, a pasted list of DOIs or titles,
   or the review article's PDF) or say you know of none. The page shows the data policy and the two opt-in switches
   described under [Prior reviews](#prior-reviews).
4. *Databases* (`databases`): pick platforms from the catalogue (coverage, export formats and caps are shown), set
   their order and the development role, or add a custom platform with a syntax family.
5. *Find more seed papers* (`more`, optional): the reference lists of the development seeds and the citation
   candidates, described below.

What `project.yaml` records, with the protocol hash, is folded under *Plan summary* on the Review plan page.

**The question window.** A question from WIDER about the page you are on opens in a small window at the bottom right
of that page, as a chat: the question, what each answer does, quick replies (the suggested one has a violet border),
a box for a value such as a corrected DOI, and *Later*, which keeps the question and changes nothing. Your answer and
what WIDER did with it stay in the thread, with your name and the time; *Change* undoes an answer through History
(not for refinement answers, whose effects run outside the journal). The window can be minimized or closed to a pill
"Waiting for you"; Esc minimizes it. In ask mode a question about another page still opens the dialog; in queue mode
neither opens and the questions wait on the Questions page. `wider questions list --page <page>` lists the same
questions.

### Seed papers' full texts

For scripts: `wider benchmark pdf`.

**What happens.** You can add the PDF of a seed paper's full text. `wider benchmark pdf add B001 paper.pdf` copies
it into `seeds/B001.pdf` inside the project (replacing an earlier copy) and records its size and SHA-256 hash;
`wider benchmark pdf list` shows which seeds have one and whether its text was extracted, and
`wider benchmark pdf remove B001` deletes the copy and its extracted text (your original file is untouched). A
`.gitignore` inside `seeds/` keeps the PDFs out of git, and they never go into an export or the replication package;
only the hash is recorded. Nothing leaves the computer: the PDFs are read locally (`pip install "wider[pdf]"`). The
full texts feed the name mining described under [Names from seed papers](#names-from-seed-papers).

**Commands.** `wider benchmark pdf add <id> <file.pdf>`, `wider benchmark pdf list`, `wider benchmark pdf remove <id>`.
**UI:** the *Seed papers* task page, the *full text* column of the seed table (add, replace or
remove a PDF per seed).

### Candidates from seed papers

For scripts: `wider benchmark chain`.

**What happens.** `wider benchmark chain` asks OpenAlex for the works your Development seed papers cite
(`--direction references`) and the works that cite them (`cited_by`; `both` is the default) and lists them as
candidates, each with the seeds that link to it and whether the imported records already hold it ("retrieved by the
query"). Only the DOIs of Development seeds are sent, then their OpenAlex ids; a Validation seed is never chained, so
its DOI is in no request. The requests run under the online-lookup consent (see
[Online lookups](#online-lookups)) and are logged with purpose `seed_chain`; their answers are kept in
the lookup cache, so running the chain again sends nothing for a seed already chained. `--limit N` chains only the
first N Development seeds.

**What you decide.** A candidate never becomes a seed paper on its own. `wider benchmark add --from-candidate N
[--set Development|Validation]` adds it through the ordinary placing rule, with the source `seed_chain:<id>` of the
first seed that links to it; `wider benchmark candidates --dismiss N` keeps the row and marks it dismissed, with who
and when. A dismissed candidate can still be added later.

**Ranked list and snowball rounds.** The list is ranked by the number of seed papers a candidate links to directly
(it cites them or they cite it), then by being found in both directions, then by year, newest first; new and added
candidates come first, dismissed ones last. The rank says how connected a paper is, not whether it is relevant: a
paper with one link may still be relevant. Each row shows its round: 0 for a file import, 1 for the seed papers
themselves, 2 and higher for snowball rounds. `wider benchmark chain --from-candidates` chains only the Development
seeds you added from candidates, in the directions not chained for them yet, and puts what it finds in the next
round; a candidate you added to Validation is not chained, and the summary counts it. A round with nothing to chain
sends nothing. When the newest round has been looked at and none of it was added, the list says "round k added no
paper; snowballing usually stops here" (the usual stop rule of snowballing, as information: you can still chain
another round).

**Candidates from any tool (nothing sent).** You can also find candidates with another tool (citationchaser,
ResearchRabbit, Scopus "Cited by", Elicit, Consensus or your own script), export RIS, CSV or BibTeX, and import the
file with `wider benchmark candidates --import FILE --origin LABEL`. The file is read on this computer and nothing is
sent, so this route needs no consent. A paper already in the list gains the new origin, importing the same file again
adds nothing, and each import is recorded in History with who and when. `wider benchmark candidates --dismiss-import
N` dismisses the rows of import N that were not added as seeds. A paper added from an import gets the source
`seed_chain:<seed>` when the file names a linking Development seed, else `other_database` (a Validation seed never
links a candidate).

**Commands.** `wider benchmark chain [--direction references|cited_by|both] [--limit N] [--from-candidates]`,
`wider benchmark candidates [--round K] [--import FILE --origin LABEL] [--dismiss N] [--dismiss-import N] [--json]`,
`wider benchmark add --from-candidate N [--set S]`.
**UI:** the *Find more seed papers* task page, *Candidates from seed papers and other tools*: direction,
**Find candidates**, **Chain again from added papers**, a file and its origin with **Import candidates**, **Dismiss
this import** on each import, a round filter, and **Add** and **Dismiss** on each row.

### Reference lists of the seed papers

For scripts: `wider benchmark references`.

**What happens.** For each Development seed, WIDER compares two reference lists: the one OpenAlex holds (reused from
backward chaining, or one request under the online-lookup consent, logged with purpose `seed_refs`) and the one parsed
from the seed's PDF on this computer. It counts both and lists the entries the PDF has and OpenAlex lacks. When such
an entry has a DOI, it becomes a candidate with origin `pdf` (see above); nothing is added to the seed papers.
`--route auto` (the default) asks OpenAlex when online lookups are on and reads the PDF in any case; with lookups off
it says that only the PDF route ran and nothing was sent. `--route pdf` sends nothing; `--route openalex` asks for the
consent first. `--pdf PATH` reads another PDF for one seed. A Validation seed is never processed: naming one exits
with an error, and a run over all seeds says how many Validation seeds it left out.

**Parsing tiers.** Plain parsing of a PDF misses DOIs and splits some entries. Three optional tiers parse further,
and each keeps only what the entry itself says:
- `--tier grobid`: GROBID, a reference parser you run yourself as a server on this computer. WIDER never installs or
  starts it. Set its address with `wider seeds policy grobid-url http://127.0.0.1:8070` (or on Settings, *Seed
  papers*); `off` clears it. GROBID gets the whole PDF, so only an address on this computer (127.0.0.1, localhost or
  ::1) is accepted, and any other is refused before anything is read. When nothing answers, the run says "GROBID not
  reachable at <url>" and keeps the plain entries.
- `--tier crossref`: for an entry without a DOI, only its parsed title goes to Crossref (purpose `ref_match`, under
  the online-lookup consent). A match is marked "fuzzy (confirm)": it is listed with its DOI for you to check and does
  not become a candidate on its own.
- `--tier model`: the construct backend structures each entry into authors, title, year, venue and DOI. A field is
  kept only when the entry holds those words; any other is dropped and counted. A reference entry goes to a backend
  that is not on this computer only when the passages switch is on and that backend has its `passages` consent
  (`wider seeds policy`, Settings *Seed papers*), as for name mining.

**Commands.** `wider benchmark references [ID] [--route auto|openalex|pdf] [--pdf PATH]
[--tier grobid|crossref|model] [--json]`, `wider seeds policy grobid-url URL|off`.
**UI:** the *Find more seed papers* task page, *Reference lists of the development seeds*: seed, route and
tier, and **Compare reference lists**; the GROBID address on Settings, *Seed papers*.

### Prior reviews

For scripts: `wider prior-reviews`.

**What happens.** Earlier reviews of the same or a neighbouring construct are the second kind of seed the tool can
use, next to the papers you name yourself. You add each review's list of included studies; WIDER parses it on your
computer into titles, DOIs, years and first authors and stores them in the project. Accepted inputs: any export the
import stage reads (RIS, CSV/TSV, XLS/XLSX, BibTeX), a text file or pasted text with one DOI or title per line or a
reference list, and the review article's PDF (`pip install "wider[pdf]"`; the reference section is located by its
heading and split into entries; two-column layouts parse imperfectly, so check the parsed titles or paste the list
instead). Duplicate files are refused by content hash; within a review, entries are deduplicated by DOI, then by
normalised title. The copied files live in `prior_reviews/` inside the project, which the project `.gitignore`
excludes.

**Data policy.** The files stay on this computer: they are parsed and matched locally and are never uploaded or
sent to any model or service. Two per-project switches, both off by default, widen that:

- *Allow identifier lookups on OpenAlex* (`--allow-lookup`): needed by `resolve` (DOIs and titles are sent to
  OpenAlex to find each study's OpenAlex id) and by `chain` (the ids are sent to fetch references and citing
  works). Nothing else is sent, never abstracts or the files.
- *Allow excerpts in LLM prompts* (`--allow-excerpts`): the titles of the included studies (up to 200) are added to
  the construct-proposal prompts as one more source of vocabulary. The run spec of every proposal records how many
  titles were used (`prior_review_titles_n`).

**What it is used for.** Two things, both optional. *Coverage* (`wider prior-reviews coverage`, or the
*Prior-review coverage* card on the Search results page) reports, per platform export or for the deduplicated corpus, which
of the included studies your search retrieved, with the misses listed. It is a descriptive second benchmark: the
prior reviews were not used to tune the query, so it is reported at any time, unlike the validation set. *Citation
chaining* (`wider prior-reviews chain`, or the *Citation chaining* card) fetches, with the lookup switch on, the
works each included study cites (backward) and the works that cite it (forward, capped at
`max_citing_per_paper`, 2000, with a warning when cut), and writes them as `inputs/citation_chain/chain_<stamp>.csv`
with the title, abstract, DOI, year, authors, journal and OpenAlex id of each work plus the source study and
direction. The next `wider import run` ingests that file as the platform `citation_chain`, so the works go through
the ordinary dedup, enrichment, ranking and screening. Chaining is resumable: each study is chained once per
direction, and a work exported by an earlier run is never written again, so a later chain file holds only new
works. `resolve --limit N` resolves only the first N unresolved papers, for a quick trial. Every request these
commands send is logged in the project with the other outside requests (`wider lookups log`); they do not need the
online-lookup consent below, only their own switch.

**Commands.** `wider prior-reviews add <file> [--label]` or `wider prior-reviews add --text "<lines>"`,
`wider prior-reviews list`, `wider prior-reviews papers <id>`, `wider prior-reviews remove <id>`,
`wider prior-reviews policy [--allow-lookup/--no-allow-lookup] [--allow-excerpts/--no-allow-excerpts]
[--none-known/--some-known] [--max-citing N]`, `wider prior-reviews resolve [--limit N]`, `wider prior-reviews
coverage [--scope platforms|corpus]`, `wider prior-reviews chain [--direction both|backward|forward] [--limit N]`.
`resolve` and `chain` exit with code 2 while the lookup switch is off (`coverage` never needs it). `coverage` also
prints how many matches still wait for confirmation, and a pasted entry that repeats one already in the review is
reported as a duplicate, not as unreadable.

### Online lookups

For scripts: `wider lookups`.

**What happens.** Four steps look papers up on outside metadata services: resolving seed papers
(`wider benchmark resolve`, `wider benchmark add --resolve`; OpenAlex and Crossref; the summary reads "k resolved,
u unresolved of n looked up; m already resolved", counting only the papers looked up in that run), fetching seed-paper abstracts
(`wider benchmark fetch`) and enriching the corpus (`wider enrich`; both on OpenAlex, Crossref, Europe PMC and
Semantic Scholar). For each paper they send its DOI and title, plus your contact address when `WIDER_MAILTO` is set.
Nothing else leaves the computer: no abstracts, no files, no decisions.

**OpenAlex budget and API key.** Without a key, OpenAlex counts each request against a free daily budget shared by
everyone on your network; once it is spent, OpenAlex refuses requests until midnight UTC, and WIDER says so in those
words (with the time left) instead of "HTTP 429". An API key of your own from openalex.org has a budget of its own: put
it in the environment variable `OPENALEX_API_KEY` before starting WIDER. It is sent to OpenAlex only, with every
OpenAlex request, and never stored, cached, logged or printed. A request OpenAlex refuses for want of a key (paging past
the first page) says so too.

**Consent.** Online lookups are off in a new project. The first lookup asks before anything is sent: in the
interface a dialog names the services and the fields sent, with **Allow and continue** and **Cancel** (the dialog
opens on Cancel, Esc cancels, and the focus goes back to the button you pressed); in a terminal
the command asks y/N; in a script (no terminal) it exits with code 2 and says how to allow lookups. Cancelling sends
nothing. The consent is stored in `project.yaml` (`online_lookups`: when and by whom) and holds for the project until
you revoke it, after which the next lookup asks again. Prior-review lookups keep their own switch (see
[Prior reviews](#prior-reviews)); this consent neither needs nor changes it. Listing candidates
from the seed papers (`wider benchmark chain`, see [Candidates from seed
papers](#candidates-from-seed-papers)) runs under this consent too: it sends the DOIs of the
Development seeds only. So do the reference lists of the seed papers (see [Reference lists of the seed
papers](#reference-lists-of-the-seed-papers)): OpenAlex gets the Development seeds' DOIs
(purpose `seed_refs`), and the Crossref tier gets the parsed title of a reference entry that has no DOI (purpose
`ref_match`). Importing candidates from a file sends nothing.

**Search terms: a second switch.** A count of the works one search term, or the whole query, finds sends search
terms or a query string, not a paper, to OpenAlex (purposes `term_yield` and `query_yield`; see [The OpenAlex
check](#the-openalex-check)). Search terms are a different kind of data, so they have a switch of their own, off by default:
`wider lookups terms allow` turns it on and `wider lookups terms revoke` off, each without touching the DOI consent,
and the DOI consent does not turn it on either. While it is off, a terminal asks y/N at the point of use, the page
shows a dialog naming the search-term switch, and a script stops with code 2; nothing is sent without a yes. The seed check
(`query_seed_check`) needs both switches.

**The cache.** The answers to seed chaining, reference lists and Crossref title matches, term counts and the OpenAlex check are kept in the project store, keyed by the
service, the address and the parameters of the request. A repeated request is answered from there: nothing is sent
and nothing is logged. `wider lookups cache` says how many answers are stored; `wider lookups cache --clear` empties
the store, and the next run asks the service again. The cache never goes into the share bundle or the replication
package.

**The log.** Every lookup run writes one row per service contacted to the project store: time, service, purpose,
number of papers sent and number of failed requests. It never stores a URL, a query or an answer.

**Commands.** `wider lookups status`, `wider lookups allow`, `wider lookups revoke`, `wider lookups log [--limit N]`
(newest first, 50 by default), `wider lookups terms allow|revoke`, `wider lookups cache [--clear]`. **UI:** the *Online lookups* card on the Settings page and on the Seed papers and Find more seed papers task pages shows the
state, the services, the fields sent, a switch, and the newest logged requests.

### Where am I?

For scripts: `wider status`, `wider next`, `wider info`.

Every stage is either **done** (its `done_when` checks pass against the store), **ready** (not done, all
required stages done), **blocked** (a required stage is not done) or an **extension** (registered, no commands
yet).

**The interface.** A sidebar on the left starts with the project box (the open project's name; "Open project" goes
back to Home) and then lists the pages of the project under the four sections of the procedure: **1 Literature
search** (Model for query creation, Review plan, Concepts and synonyms, Search strings, Search results),
**2 Data cleaning** (Deduplication, Missing abstracts), **3 Model tuning and validation** (Ranking, Samples and
labelling) and **4 Screening and outputs** (Screening, Recall and report, Results dashboard). Each section number shows a check when all
its steps are done and is marked while you are in it. Each step carries an icon and a mark: a green check when done,
a blue dot for the next step, a ring otherwise; the word is read out to screen readers and shown on hover. The words
are the same in the sidebar and in each page's status chip: **completed**, **in progress** (while the step's run is
going), **needs attention** and **not started**. Only the next stage reads "not started, work here next"; a stage
that waits for an earlier one says which ("not started: needs Ranking first"). A step that is done but still has work
for you keeps its check and adds a rose mark: Deduplication with pairs waiting reads "needs attention: 3 pairs to
decide"; `wider status` prints the same reason as "done, needs you: 3 pairs to decide". Below the sections are Questions (with the number of
open questions), Notes and tags, History, Help and Settings, and the line "N of M steps done" with a bar. The page
headings and `wider stages` use the same names. The later stages that are not built yet (full-text screening, data
extraction, synthesis) are not in the sidebar; `wider stages` lists them as "later". The journey line under the top bar lists the same pages in the same order. The project title follows a
rename at once. The button at the top collapses the sidebar to an icon rail; the choice is remembered in the browser,
and below 900 px the rail is the default.

Above every page inside a project sits the top bar: the breadcrumb (project / page; no step number),
and on the right the Notebook and Guide buttons and the page's one main button, on the pages that have been through the
design pass (Deduplication first). Stage pages then show the page title with a status chip and a quiet **Explain this
step** button that opens what the stage does, why it comes now and its command. All text is set in Public Sans, which
ships with WIDER; nothing is loaded from an online font service. Every status shows a word and an icon, never colour
alone. The interface works with the keyboard alone: the first Tab stop skips the sidebar, focus is always visible, and
the Help page lists the shortcuts.

**Overview, journey line and guide.** A project opens on its **Overview**: *Continue* names the recommended step
with why it comes now, its button, and "Back to <page>, where you stopped" when you last worked elsewhere; *Needs
attention* lists each thing that needs you with one action (pairs to decide, a ranking that needs review, the
development sample still open, open questions, review packages still out); *Research journey* lists every stage's
state, and *Project summary* the counts. Result headlines of the stages that can hold a judgment are left out, so a
blind second reviewer reads no model decision there. `wider status` prints the same items and the same next step. Under
the top bar, every project page shows the **journey line**: the section of the current step, the current step (the one
`wider next` names) with its state, and the step after it; **Hide** folds it, remembered in this browser, and "Show the
journey line" brings it back. The project's title in the breadcrumb is the **Switch project** menu: the projects opened
before on this computer, and Home. Under each stage page's title, the box **What you can do here now** says what the
page lets you do in the stage's present state (work here; completed and still changeable; locked, with **Unlock to
change…**; or not yet); it never disables anything, and `wider guide <page>` prints the same sentence first. The
**Guide** button in the top bar opens the guide panel beside the page: what the step does, why it comes now, and its
commands; pressing **Guide** again, or Esc in the panel, closes it.

**What is sent, beside the button.** A button that sends text off this computer (a backend check, a model list, a
seed-paper lookup, a screening run on a cloud backend, names for missed papers) carries one line beside it that says
what is sent and to whom, before you press it. On a page whose models run on this computer the line "Runs on this computer; nothing leaves it." shows once, and a
local model's card adds no consent line. The line
informs; consent for a cloud backend is still asked as before (see
[Consent for cloud backends](#consent-for-cloud-backends)).

**Home.** The home page shows the open project and every project opened before on this computer as cards: the title,
the folder, "k of n stages done" (or "All n stages done"), the record count and a segmented bar with one segment per
stage. **Info** opens the project's facts: folder, records, seed-paper coverage, each AI model (LLM backend) with what it is used
for and the embedding model, each marked **local** (nothing leaves the computer) or **cloud** (text is sent to a
remote service), tokens used by screening calls, the last run and the last backend check (money costs per stage and
backend: `wider costs`, see [Runs, progress and costs](#runs-progress-and-costs)); `wider info` prints the
same lines. **Open** opens a listed project; **Remove from list** takes a card off the list and never touches the
folder (the open project has no such button). Folders in the list that no longer hold a project are hidden;
**Show N hidden folders** names them. **Create project** makes a new project folder (the same as
`wider init`) and opens it on the Model for query creation page; **Open a project** opens any folder that holds a
`project.yaml`.

**Which page opens.** A project opens on the page last used in it in this browser (remembered under a key made from a
hash of the project folder, never the path or project data). Without one it opens on Model for query creation while
no model is chosen for query creation, else on the next stage (the one `wider next` names), or on Recall and report
when every stage is done. An address with a page in it (`#rank`) opens that page.

**Pages keep themselves up to date.** After an action (a run, a decision, an undo, an answered question, a report)
the sidebar states, the project title, the result cards, the counts and the selects of the page redraw from the
project, so no reload is needed; a page opened later shows what was done on another page. **Notices** appear at the
bottom right: a newer notice closes the earlier plain ones, a notice with an action (such as **Undo**) stays for 20
seconds or until closed, and an error stays until it is closed.

**Backend check.** On the Settings page, **Check models** runs the same probes as `wider providers probe` and
`wider embed probe` on the default LLM backend and embedding model, with fixed test sentences and no project data,
and never downloads local model weights. The result is stored with the project and shown in the home page's Info
panel and on the Settings page; `wider check-backends` does the same on the command line and exits with 1 when a
backend fails. Each result names what the backend is used for, such as "LLM b (screen)".

**Settings.** The Settings page holds what applies to the whole project. **Backends:** every AI model (LLM backend) and
embedding model with **Edit** (an AI model opens the backend form in place, with its price fields; an embedding
model opens its card on the Ranking page, where its form is), **Remove** after a confirmation (`wider providers remove
NAME`, `wider embed remove NAME`), the AI model per stage (`construct` and `screen`, the screen row with three selects for pass A, pass B
(`screen_b`) and the judge (`screen_judge`); "project default" or "Same as pass A" removes the choice; `wider
providers stage STAGE NAME`, see [A backend per pass](#a-backend-per-pass)), the "review" row with the second reader's two backends (`wider providers
stage review NAME [NAME2]`; one name reads twice, no name leaves it to the construct backend), and, for each backend off this computer, its consented operations with
**Revoke** (`wider providers revoke NAME`, `wider embed revoke NAME`). **Add or find AI models** opens the Model for
query creation page, and **Check models** runs the backend check. Below come the prior-review data policy (changed
in the Prior reviews task of the Review plan), the *Online lookups* card, the *Advanced: review protocol* card (every setting
with its description, default and current value, a changed setting marked, with **Set** and **Reset**, as
`wider protocol show|set|reset`), what happens when the tool is in doubt, and where this computer keeps the project.

After a stage runs, its page stays open and shows a **result card**: a one-sentence headline, and under **Details** a
few metric tiles, any warnings, a details table and the files written, followed by a **Continue to <next stage>** button.
A result from an earlier visit is one line, "Last run <when>. Show the result", until you open it. The
same result is printed by the command line after each run command and listed in the "last result" column of
`wider status`, because both read the one record the engine stored at the end of the run (`last_result:<stage>` in
the store's meta table). Each page also shows a one-line summary of the preceding stage at the top when that stage ran in this visit. On the command line:

```bash
wider status        # project facts, then the stage table and a `next:` line
wider next          # only the next command to run
wider next --why    # the same, with what the stage does and why it comes now
wider stages        # the registry itself, in order
wider info          # the home page's info panel: stage, records, seed coverage, backends, tokens, last run
wider check-backends  # probe the default LLM backend and embedding model, store the result
```

### Runs, progress and costs

For scripts: `wider costs`.

**Background runs.** Screening, embedding the corpus, fetching abstracts and LLM proposals start as background runs
in the interface. A bar at the top of every page shows each running job with its progress, the time left (from the
pace so far, restarted for each screening pass), the elapsed time, the tokens and cost so far, whether the backend is
local or cloud, and a Cancel button. You can open other pages or close the tab; the run continues as long as
`wider ui` runs. When it ends, a message names the result and, if you allowed it, the browser shows a notification
while the tab is in the background. A cancelled or interrupted run keeps every finished item; starting the same run
again resumes it. On the command line the same stages run in the foreground (`wider screen`, `wider embed`,
`wider enrich`), and a cancelled screening run is marked interrupted.

**Tokens and costs.** Every model call (screening, proposals, adjacent literatures) is logged with its stage, run,
backend, token counts and cost; the prompt and the answer are not part of this log. The cost is the one the backend
reports itself (agentic CLIs such as Claude Code report it), zero for a local backend, or computed from the prices
you set on the backend (**Edit** on Settings) or with `wider providers add --price-in <usd> --price-out <usd>` (US dollars
per million input and output tokens, from the vendor's price list). A cloud backend without prices shows its tokens
and "not priced" rather than a guess. Cached-token prices, cost ranges before a run and projections during it are
described under [Costs and estimates](#costs-and-estimates). Each result card shows the run's tokens, cost and a **local** or **cloud**
marker, the screening runs table shows them per run, and the home page's info panel shows the project total.
`wider costs` prints the same totals grouped by stage, backend (`--by backend`) or run (`--by run`).

**Earlier results.** Every stage result is kept. When a stage has run more than once, a selector in the page's title
row shows the earlier results; choosing one shows it in the result card until you go back to the latest.

### When the tool is in doubt

For scripts: `wider questions`.

WIDER asks instead of guessing at five points, and each answer is applied at once:

| question | raised when | your answers and what they change |
|---|---|---|
| screening answer unusable | the model's answer for a record could not be parsed after the retries | include, maybe or exclude becomes that pass's read, marked as yours (the protocol majority uses it); "retry" leaves it for the next run of the pass |
| three reads disagree | the two passes and the third read of the protocol give include, maybe and exclude | your decision replaces the protocol's maybe and stays when the protocol runs again |
| no abstract found | enrichment found no abstract for a record's DOI or title (at most 50 questions per run) | a corrected DOI is looked up on the next run; a pasted abstract is stored as the researcher's; or leave it |
| seed paper unresolved | the seed paper's DOI finds no abstract, or it has none | a corrected DOI is written to `benchmark.csv`; or leave it |
| proposal failed | the model could not produce a usable construct proposal | a clarification that every later proposal prompt shows under "Clarifications from the researcher" |

Choose per project whether questions **wait** on the Questions page (`doubts: queue`, the default) or are **asked at
once** in a dialog as they arrive while the run continues (`doubts: ask`; "Later" puts them back in the queue). The
sidebar shows how many are open. Screening prompts are not changed in the middle of a run, so every run keeps one
prompt and one hash; a clarification about screening belongs in the codebook, from which the next prompt is built.

```bash
wider questions list                     # open questions with their possible answers (--all for the history)
wider questions answer 12 include        # decide a record; --value "<DOI, abstract or clarification>" where needed
wider questions dismiss 13
wider questions mode ask                 # or queue
```

#### Refinement questions while the query is refined

For scripts: `wider decisions`.

While you refine the query, WIDER also asks when the evidence calls for a decision: after a dry run, a mining run
of the seed papers, a diagnosis and an import. These questions appear on the same Questions page (and in the dialog
in ask mode), with the stage "query". Each says **why** it was asked and, for every answer, **what it changes**:
seeds gained or lost and records added or removed, where WIDER can count them. One answer is the default; choosing
it is recorded as "default", any other as "chosen", a dismissal as "dismissed". No answer is forced on you: leave or
dismiss is always possible.

| question | raised when | the default answer |
|---|---|---|
| name-add | a name mined from the seed papers would retrieve a missed seed | add it to that block (a new synonyms version) |
| same-construct | a seed paper introduces two names as the same thing ("also called") | leave; "same" or "different" sets the name map |
| noise-term | the diagnosis reports a term that only brings records no seed holds | drop it (a new synonyms version) |
| variant | the truncation report finds a stem that gains a seed or loses only noise | change it (a new synonyms version) |
| context-AND | a missed seed fails a block joined by AND | add the suggested term to that block |
| seed-not-retrieved | the dry run misses a seed | 2a's best edit; or record the seed as not indexed or indexed |
| boundary-record | a cluster of at least 5 records has no seed and no mark | leave; "relevant" or "noise" marks the cluster |
| limit | the records are over the budget and some are older than the earliest seed | no limit; or a date limit from the seed's year or 5 years earlier |
| screening-budget | the records are over the record budget | keep the budget; or raise it to the next 500 |
| round-stop | the last 3 answers moved recall and records by less than 2% each | one more round; or freeze, or stop asking until the next dry run or import |

At most **5** refinement questions are open at once and at most **10** are asked about one block in one interview
round; the rest wait ("waiting for the budget") and open as you answer or dismiss. A question already answered or
dismissed is not asked again. An answer that writes a new synonyms version asks the same confirmation as any save,
and it is one change in the history, so one undo takes back the version, the answer and its ledger row; an answer
that reopened a frozen query cannot be undone (the unlock is final).

Every answer, accepted default, dismissal and admitted name is a row of the **decision ledger**. The proposal and
mining prompts show the newest 30 under "Decisions from the researcher" (the name or term, the reason, the choice and
your note), so the model does not propose a dropped term again. Evidence from a seed PDF is kept as references
(seed, section, offset, the quote's sha256), never the quote itself. The Questions page lists the ledger under
"Decisions" and writes it out with the Export button.

```bash
wider decisions list                     # the ledger, oldest first (--round N keeps one synonyms version)
wider decisions export                   # runs/query/decisions.jsonl and decisions.csv
```

### Why WIDER works this way

For scripts: `wider guide methods`, `wider guide methods <topic>`, `wider guide methods glossary` (`--json` for the
same text as data). None of them needs a project.

The Methods page (`#methods`) explains each method choice WIDER makes: how the procedure runs, how recall is
estimated and against which target, how the samples are drawn, why development and validation are kept apart, how
blinding, consent and privacy work, and so on. Each topic has three parts: **In short** (plain words), **Why** (the
reason for the choice) and **For auditors** (the functions, settings and files behind it, with the shipped
defaults filled in). A glossary at the end gives every word with the other names it goes by. Under each title a line
says whether the author has reviewed that text ("Reviewed by the author on <date>.") or whether it is still a draft.

The page is not in the sidebar: Help links to it, and the guide panel of a page ends with "Why WIDER works this
way:" and links to the topics about that page (`wider guide <page>` prints the same line). An address can open one
part directly: `#methods/recall-estimate/auditors` opens that topic at "For auditors", `#methods/glossary/recall` at
the word "recall".

The text stays current with the code: each topic names the functions and settings it describes, and a test fails
when one of them changes until the text is checked and re-stamped (`python3 scripts/methods_review.py stamp
<topic>`). A changed topic shows as a draft again until the author reviews it.

### Ask WIDER

For scripts: `wider assistant ask "<question>" [--page <page>] [--depth simpler|detail] [--allow-once] [--json]`,
`wider assistant history [--unanswered] [--json]`, `wider assistant unhelpful <turn> [--undo]`,
`wider assistant forget [--yes]`, `wider assistant budget [N|--default]`, `wider assistant index`,
`wider assistant check-retrieval`.

Ask WIDER is the second tab of the side panel, beside the guide: press **Ask WIDER** in the top bar, or pick the
"Ask WIDER" tab in the panel (the arrow keys switch between the two tabs). Only one side panel shows at a time: while
a record's drawer is open the panel steps aside, and it comes back when the drawer closes. At the top, "Asking about:"
names the page you are on; change it to another page or to "WIDER in general". That page's guide goes with your question (on the command line `--page`;
without it the question is about WIDER in general). Your conversation is kept under the one name the other pages sign
with: the tab says "Asking as <name>", and you change the name with Change beside "Working as" in the top bar. With
no name set yet, a small "Your name" field there takes it. Type a
question about WIDER, a page or a method and press **Ask** (or Ctrl+Enter). Under the box two lines stay in view: it explains from WIDER's documents and can be wrong, so check
the sources; and what you type is sent as written, so do not paste a record's text. Beside **Ask** WIDER says what a
question sends and to which backend.

The answer comes from WIDER's own documents only: the guide pages, the Methods topics and the glossary. Under it
are the lines **Based on:** (the sections used, each with the `wider guide ...` command that prints it; a section
the author has not reviewed yet carries "(not yet reviewed by the author)"), a **Show** link where the answer names
a button on the page (it opens that page and moves the focus to the button, and never presses it; the panel stays open), a draft you can copy where you asked
for one (for a methods section, say; nothing is recorded), "Read:" (how much was sent and whether WIDER searched by
keywords or also by meaning) and "Answered by" (the backend and model). **Simpler** and **More detail** ask the same
question again in plainer words or with the reasons and the auditor's detail. A question WIDER's documents do not
explain gets "WIDER's documents do not explain this." with no model call.

What it reads: the page's guide, WIDER's documents, your project's settings and stage status, and your earlier
questions in this project. It never reads a record, a label, a note, a tag or a model's decision, and while a
labelling queue shows records Ask WIDER is not offered (no top-bar button, and the Ask WIDER tab says so),
so your labels stay your own. It never acts: it cannot press a button,
change a setting or write anything to the project.

It uses the backend set for query creation (Model for query creation); without one the page says so and links
there. A backend that is not on this computer asks for consent first, as for any other operation (see "Consent for
cloud backends"); the dialog for Ask WIDER has "Remember my choice for Ask WIDER", ticked by default. Untick it to
allow this one question only: nothing is remembered and the next question asks again (`--allow-once` on the
command line). An embedding model, where one is set, also searches by meaning; it embeds only WIDER's documents and
your question, and asks its own consent.

Settings, "Ask WIDER", sets how much text one question may send (its budget in tokens): by default 3,000 for a
model on this computer and 12,000 for one elsewhere, from 500 to 200,000. **Use the default** goes back to the
default for the model's place.

The conversation is kept on this computer, in the project's `assistant/` folder, one file per reviewer name,
readable only by you; it is listed in the project's `.gitignore` and never goes into a share bundle or replication
package. **Forget this conversation** (or `wider assistant forget`) deletes yours and no one else's; it cannot be
undone. **Didn't help** under an answer marks it; `wider assistant history --unanswered` lists the questions that
matched no section or were marked, so WIDER's documents can be improved.

### Editing WIDER's documents

The Methods topics and the glossary are Markdown files in `wider/guide/methods/`. Each topic starts with front
matter between `---` lines (`id`, `title`, `context`, `settings`, `artifacts`, `pages`) and has three headings:
`## In short`, `## Why` and `## For auditors`. A shipped default is written as `{default:<setting>}` (for example
`{default:identification.development_share}`) and filled in from the code, so the text never goes out of date. In
`glossary.md` each word is a `## word` heading, then a `synonyms:` line with the other names separated by `;`,
then the definition.

Run the format test first: `.venv/bin/python -m pytest tests/unit/test_methods.py -k format`. The next question to
Ask WIDER uses the edited text, with no restart. Until the edit is stamped and the author has approved it again, the
topic reads "Edited since the author's review" on the Methods page and its sections carry "(not yet reviewed by the
author)" under Ask WIDER's answers. Stamp with `python3 scripts/methods_review.py stamp <topic>`; the author's
review is recorded with `python3 scripts/methods_review.py approve <topic> --answer <dashboard question id>`.

### Keyboard and screen readers

For scripts: `wider history undo`, `wider history redo`.

Every page works without a mouse. <kbd>Tab</kbd> and <kbd>Shift</kbd>+<kbd>Tab</kbd> move between controls, and
<kbd>Enter</kbd> or <kbd>Space</kbd> presses the focused button; <kbd>Enter</kbd> in a field does what the button
beside it does. Opening a page puts the focus on its heading, and after an action the focus stays on the control you
used, or on the row that took the place of the one you acted on.

- **Dialogs.** Questions the tool asks (confirm, name a label, Unlock, the consent to send records to a model) open
  in the page, never as a browser box. The focus starts on the safe choice (Cancel or Later), <kbd>Tab</kbd> stays
  inside the dialog, and <kbd>Esc</kbd> cancels it. A dialog that fails keeps its text and says what went wrong.
- **Esc** also closes the record drawer (or leaves its note or tag field), and the Menu on a narrow window.
- **Undo and redo.** <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Z</kbd> outside a text field undoes your last change;
  <kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd> puts back the last undo, until you make a new change
  (see [History and undo](#history-and-undo)).
- **Tabs.** A row of tabs is one stop for <kbd>Tab</kbd>; the arrow keys move between its tabs.
- **Phone width.** Below 640 pixels the sidebar folds into a *Menu* button at the top. It opens the page list;
  choosing a page or pressing <kbd>Esc</kbd> closes it, and the focus goes back to Menu.
- **Screen readers.** Every control and table-row button has a name, charts are named pictures with their numbers
  in the table beside them, and messages are read once when they appear; refusals are read at once. Help lists every
  key (*Keyboard* card).

### History and undo

For scripts: `wider history`.

Every change you make by hand is kept with who made it, when, and what it touched before and after: labels,
adjudications, duplicate decisions, artifact approvals and freezes, notes, tags, answered questions (with what the
answer changed), and every edit of the settings or the review protocol (each save of `project.yaml`, summarised as
"setting: old → new"). The *History* page lists them, newest first, with a filter by kind and an "only mine" switch;
the record drawer shows the history of the open record.

**Undo** puts back what was there before the change, but only when nothing has changed it since; otherwise it says
what changed and asks you to undo the later change first. An undo is itself a change, so it can be undone (redo).
After a decision, a note, a tag or an answer, the message at the bottom of the page offers Undo; on any page,
<kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Z</kbd> outside a text field undoes your last change. Undoing again goes one
change further back (it never just redoes what the previous undo took back);
<kbd>Ctrl</kbd>/<kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>Z</kbd>, or `wider history redo`, puts back what your last undo
took back, as long as you have made no change since. Only your own changes are undone or redone
this way: without a remembered reviewer name nothing is undone, and the History page puts the cursor in its "Your
name" field, which remembers the name for later undos. When the next change back is blocked by a later
change, undo stops and says which. Undoing an answered or dismissed question reopens it (a dismissal is recorded in
the history like an answer); undoing a researcher's read added to a screening run appends a retraction to the run's response
log, so `wider screen replay` leaves it out as well.

Three things are not undone, by design. A **freeze** is final, because the validation seed papers are tested once.
An **import of decisions** stays on record as its own result set (make another set active instead). Saved
**artifact versions** never change; to go back, restore an older version as the newest draft (*Restore as new
version* on the Concepts and synonyms page, or `wider construct restore`). Automated runs (deduplication, screening passes,
enrichment, ranking) are not journaled; they are reproduced from their inputs. In blind labelling the history shows
who labelled a record but never the decision, and the drawer shows only your own changes. While **Blind** is ticked
(History page or Sampling page, one switch), History says "in a sample" instead of the sample's name, so it does not
tell development from validation records.

```bash
wider history list                    # newest first; --kind label|config|answer|..., --record <id>, --author <name>, --blind
wider history undo                    # your newest change not yet undone; again to go further back; --blind as the list
wider history undo 42                 # a specific change; --other when another person made it
wider history kinds                   # every kind --kind takes
```

Changes are attributed to the reviewer name the page remembers, and on the command line to `--author`, else
`$WIDER_REVIEWER`, else `$USER`. A change made on the page is signed only with the name given on the page (also for
runs that continue in the background), never with the computer's login name; clearing the reviewer field forgets
the remembered name, and a duplicate decision is not sent until a name is known (the drawer then puts the cursor in
its "Your name" field). `wider history undo` without an id undoes only that author's changes and exits with code 1
when none is left.

**Undoing another person's change.** Undo by id works on any change, but a change signed with another name is not
undone without a word. The History page asks first ("Undo bo's change?") and says that the undo is signed with your
name; History keeps both names. On the command line, `wider history undo 42` refuses another person's change and
names its author; `--other` undoes it, signed with yours. This holds for labels too: undoing another reviewer's label
takes it out of the sample, and it is listed as your undo of their change. Changes from before names were recorded,
and changes signed "ui", are undone without asking.

**Names that differ only in case or spacing** ("Ana" and "ana") are different reviewers to WIDER, and are never
merged. When *Undo my last change* finds nothing under your name but changes under such a name exist, it says so in
one line on the History page (`nothing to undo by Ana; there are changes by 'ana'`), and nothing is undone; type the
name as it was recorded to undo those. The *Show* list offers every kind of change WIDER records; `wider history
kinds` lists the same kinds, and `wider history list --kind` refuses a kind not in that list.

**Step notebook.** A step's notebook puts its runs (signed "WIDER") and the decisions on it, with their undos, in one
list, newest first; a decision undone now says "(undone)". It reads what the journal and the stored runs already
hold and keeps nothing of its own. In this version only Deduplication has one: the *Notebook* button in its top bar,
and under the open pair "This pair's history", which also says in words what the last run did with that pair ("Kept
this pair for you: same title, but the journal differs", "Removed: same DOI"). Undo from the notebook is the
journal's undo, refused in the same words as `wider history undo` when a later run rewrote the pair.

```bash
wider notebook dedup                  # newest first; --limit 50, --json
wider notebook dedup --pair 3 7       # one raw pair's history (the order of the ids does not matter)
```

### Record drawer, notes and tags

For scripts: `wider notes`.

Record work happens in one panel on the right, the **record drawer**: the dedup manual queue, the labelling queue,
adjudication, a screening run's calls and the development evaluation all list their records in a table, and a click
or Enter opens a record in the drawer. It shows the title, authors, year, venue and DOI and the abstract; on the
screening views the model's quotes are highlighted in the abstract, with its grades and summary above it. The keys
are the same everywhere: **1**, **2** and **3** take the page's three decisions (include, exclude, maybe;
duplicate, not duplicate, uncertain; or an adjudication), **j** and **k** move to the next and previous record,
**n** writes a note, **t** adds a tag and **Esc** closes the drawer. The screening views are for reading, so they
take no decisions.

The labelling queue and adjudication open the drawer over the whole window, as a focus page with no sidebar, guide
or journey line. A dark bar names the page and who signs ("signing as ana", "deciding as mara"), with previous, next
and *Leave labelling* (or *Leave adjudication*, also **Esc**). Under the heading are the queue's own count ("Record 3
of 5 · you have labelled 2 of 5") with its bar and, in a blind queue, what stays hidden. The record card holds the
title, authors and abstract, the three decisions under the abstract and the fields saved with them; your criteria
from the codebook (with its version) are beside it, or under it on a narrow screen. The exclusion reason appears once
you press Exclude: pick a reason to save (with the keyboard, move to it and press Enter), or press Exclude again to
save without one. On adjudication, both
reviewers' labels, signed and coloured by decision, come above the record, and the agreement line is under the count;
the AI model's decisions, the rank and the score are not shown.

Notes and tags belong to a unique record and carry their author's name (the reviewer name on the labelling page,
remembered in the browser). Tables can be searched by title, note text and tag, and the **Notes and tags** page (in the
sidebar's utility pages) searches all notes and tags of the project, the same as `wider notes list`. In the blinded labelling queue you see only your own notes and tags, so another reviewer's
opinion cannot reach you through them: writing a note, searching and the tag counts all answer with your own only,
and a blind page refuses them until you give your name. Removing a note or a tag removes only your own; another
reviewer's stays. On the command line:

```bash
wider notes add 123 "Reads like the participation literature"   # --author, or $WIDER_REVIEWER, or $USER
wider notes tag 123 edge-case recheck
wider notes list --tag edge-case                                 # also --search <words>, --author <name>
wider notes untag 123 recheck                                     # your own tags only
wider notes remove 7                                             # your own notes only
```

## 2. Concepts and synonyms

For scripts: `wider construct`.

**What happens.** The LLM proposes, from your question and the development papers' abstracts, five artifacts:
the construct **cells** (the facets of the phenomenon), the screening **codebook** (the elements a record must
show, with grades), **synonym blocks** for the Boolean query, one **anchor** sentence per cell (used for the
semantic ranking), and **near-miss** descriptions of adjacent literatures that look relevant but are not. You edit
each proposal, save it as a new version, approve the version later stages should use, and freeze it when it is
final. Every version is kept with its hash and origin (LLM, manual edit, import).

**What you decide.** The content of all five artifacts: which cells the construct has, which codebook elements and
grades distinguish an include from an exclude, which synonym blocks and spelling variants go into the query, how
each anchor sentence reads (aims only, no method or result), and which adjacent literatures to describe as
near-misses. This is where your knowledge of the field and of the literatures next door goes in; the LLM only
drafts. You can also skip the LLM entirely and write the YAML yourself (`wider construct save`), or start from an
existing search string (`wider query import`).

**Defaults and why.** Proposals are grounded in the *development* papers only, so the construct stays specific to
your literature and the validation papers remain untouched. The near-miss descriptions matter later twice: the
development sample draws records close to each one, and the screening evaluation reports misses per adjacent
literature, which is where a screening prompt usually fails. `approved` means "later stages use this version";
`frozen` marks the query you searched with, and freezing the synonyms is what opens the validation set.

**Unlocking the query.** A frozen synonyms version stays frozen, but you can go back to editing:
`wider construct unlock synonyms <version>` (or *Unlock* on the page, after a warning) copies it into a new draft,
and saving a new synonyms version after a freeze does the same. Either reopens the query. When validation papers
have been shown, WIDER first asks what they mean for the test (change `unlock-seen-choice`): *Count them as seen*
(`--count-seen`) or *Keep them as validation* (`--keep-validation`), on `wider construct unlock`, `save`, `restore`
and `propose`, `wider query import`, `wider query edit apply`, `wider seeds names --admit` and `wider questions
answer`. Without a flag, or with both, nothing is done and the command exits 2; on the page the dialog has the two
answers and *Cancel*, neither preset. The choice is final, recorded with the unlock, and every output states it
("v3 was unlocked on <date> into v4 (2 validation papers shown); the researcher chose to keep them as validation.").
Kept papers still count as validation papers. Counted papers stay in Validation as used validation papers: coverage lists them under
"Validation, used" ("Val (used)" in tables) with their own recall, marked not independent, and they are no longer
development seeds (the dry run, term suggestions and the ranking ablation leave them out). Only papers added to
Validation after the unlock count, once the newest version is frozen (or the uploads are marked final) again. The coverage note then reads "Validation set seen on <date> (1 unlock); 1 paper
added since.", or "Search not validated: …; no paper added since." when none was added. An unlock is recorded in
History (`artifact_unlock`) and in `unlock.json` in the replication package, and it is final: it cannot be undone,
because the papers have been seen. A paper moved from Development to Validation, or added again after it was seen,
never counts. Freezing a different synonyms version while a frozen query stands asks first whether the validation
papers shown so far count as seen (*Count them as seen* or *Keep them as validation* on the page, `--count-seen` or
`--keep-validation` on `wider construct freeze`; without one, or with both, nothing is frozen and the command exits
2). Neither answer is preset: you decide, the choice is final, and every output states it.

**Reusing the validation set.** If recall on the validation set is poor, you may revise the query with your own input
and the development papers. After a look at the hidden set that you kept as validation (`--keep-validation`), saving
a new synonyms version first warns that the papers shown stay in Validation, recorded as used (the page's confirm
step; on the command line the save stops with exit 2 until you add `--confirm`, before any model is called). The save
is recorded in History as a final `validation_used` change, and the report, the methods draft and `manifest.json`
then say "Recall on the 2 used validation papers (not independent): k of 2." and that the search is not validated.
To use those papers as development papers instead, press *Move used validation papers to Development* on the
seed-papers step (`wider benchmark move-used --confirm`). Moving one shown validation paper with the set select (or
`wider benchmark set ID Development --confirm`, or an applied split with `--confirm`) works the same way: it warns
first, cannot be undone, and the paper is listed as a former validation paper ("Dev (former val)") with the date of
the move and whether the query found it. WIDER then invites you to add new validation papers from sources
independent of the query: prior reviews, citation chaining from seed papers, colleagues or other databases. A move of
a paper that was never shown is an ordinary change that History can undo (`benchmark_set`).

**Check before moving on.** The stage counts as done when a `cells` version and a `synonyms` version are
approved. Approve `anchors` before ranking, `codebook` before screening, and have a `near_miss` version before
drawing the development sample. `wider construct diff <kind> <a> <b>` shows what changed between two versions.

**Commands.** `wider construct propose <kind> [--provider <name>] [--note "<text>"] [--show-prompt]` with `kind`
one of `cells`, `codebook`, `synonyms`, `anchors`, `near_miss`; `wider construct list`;
`wider construct show <kind> [version]`; `wider construct save <kind> <file.yaml>`;
`wider construct approve <kind> <version>`; `wider construct freeze <kind> <version> [--count-seen |
--keep-validation]`; `wider construct unlock synonyms <version>`;
`wider construct restore <kind> <version>` (bring an older version back as the newest draft);
`wider construct diff <kind> <a> <b>`.
**UI:** the *Concepts and synonyms* page (one tab per artifact; propose, show the prompt, edit the YAML, save as a
new version, approve, freeze, unlock a frozen synonyms version, diff with the previous version; fetch missing
benchmark abstracts).

### Names from seed papers

For scripts: `wider seeds`.

**What happens.** The seed papers often name the construct in ways your synonym blocks miss. `wider seeds extract`
turns each seed PDF into text on this computer (de-hyphenated, running headers and footers removed, sections found,
the reference list parsed) under `runs/seeds/<id>/`. `wider seeds mine` then lists candidate names: the Keywords
line, phrases introduced by "also called", "also known as" and the like, quoted and recurring capitalised phrases,
and word pairs that co-occur with them in the retrieved titles; each candidate keeps the seed, section and verbatim
quote it came from. Without `--no-model` a model pass reads the sections too, and a name it proposes is kept only
when its quote is found verbatim in the text. Each model call carries one passage of the seed text (at most 1,500
tokens by default), never the PDF. A backend on this computer always gets them; a backend that is not local (cloud,
local network, unknown) gets them only while the project's passages switch is on (the default) and after you
allowed it once for `passages` (see [Consent for cloud backends](#consent-for-cloud-backends)). Otherwise the model
pass is skipped with a message saying why, and the pattern pass still runs. `wider seeds policy` shows the switch,
the passage size and what they let out; `wider seeds policy passages-to-cloud off` stops all passages to backends
that are not local, `--passage-tokens N` (100-8,000) sets the size; the same controls are the Settings "Seed papers"
card. Validation seeds are never mined: their text, keywords and references stay out of every pass and of the
streams table, and a seed moved to Validation after mining drops out of the lists. Each run is written to `runs/seeds/mine/<stamp>/`.
Mining never changes the query by itself.

**What you decide.** Which names go into the query. `wider seeds names` shows, for each candidate, what the dry run
says it would do in a block of the newest synonyms version: "retrieves B002" (a development seed the base query
misses and the edited one finds) or "not admitted: retrieves no development seed". `wider seeds names --admit
"<name>" [--block K] [--note "..."]` adds a name as a new draft synonyms version, whose note names the gain or your
reason; a name that retrieves no development seed is added only with a note. When the newest synonyms version is
frozen, the save reopens the query (see *Unlocking the query* above; the card asks first). An admission is recorded
in History (*name admitted*) and can be undone, unless it unlocked a frozen query: an unlock is final. Undo removes
the version from the list; its file stays in the project. An admitted name is also offered under
[Suggested edits](#suggested-edits-and-the-record-budget) for the blocks that miss a seed.

**Name map and streams.** `wider seeds names --map` groups the names that stand for one construct (from "also
called" sentences and your *same construct* answers) and lists look-alikes (names you answered are a different
construct), and writes `name_map.json` and `name_map.svg` into the newest mining run. `wider seeds streams` counts each
candidate or admitted name in the reference titles of each venue of the seeds' reference lists; `--label "<venue>"
"<stream>"` labels a venue as a research stream (`""` removes the label), recorded in History (*stream labelled*)
and undoable. The table only counts; you decide what a stream is.

**Commands.** `wider seeds extract [--benchmark <id>]`, `wider seeds mine [--benchmark <id>] [--no-model]`,
`wider seeds names [--admit <name> [--block K] [--note "<text>"]] [--map]`,
`wider seeds streams [--label <venue> <stream>]`.
**UI:** the *Concepts and synonyms* page, *Names from seed papers* card: *Extract*, *Mine (patterns only)*, *Mine with the
model*, the candidate list with each name's dry-run status and *Add to block* (block and note fields), the
name map, and the name-by-venue table with a stream label field.

## 3. Search strings

For scripts: `wider query`.

**What happens.** The approved synonym blocks are one query in a platform-neutral form; WIDER renders it as a
Boolean string in each platform's own syntax (field tags, proximity, truncation, length limits) and tells you where
to paste it. Google Scholar gets a reduced string within its length limit: the first few terms of each required
block (a truncated term such as `silence*` is kept as `silence`; NOT blocks are left out, since Scholar
has no reliable NOT for a whole group, and so are blocks that hold only proximity terms, since Scholar has no
proximity), and, when even one term per block is too long, the last blocks are left out. The Google Scholar card and
the header of `wider query render gscholar` name the blocks left out ("Left out: c (proximity only), d (excluded)"),
and, when no string can be built at all, say so and why (only excluded blocks, no required block, or required blocks
with only proximity terms). You run the searches yourself in the databases and export the results; WIDER never queries a
database for you. A linter flags short stems, unquoted phrases, missing spelling variants and unused blocks. Before
you search, a dry run reads the query against your seed papers on your computer; after you search, the diagnostic
report shows what each term retrieved. Both are described under their own headings below.

**What you decide.** Which platforms to render for, when the query is good enough on the development platform
(after a few rounds of edit, dry run, render, run, export, check coverage), which round to accept, and when to freeze
the synonyms version. Freeze when the query is the one you searched with: after it, the validation papers are
reported. You can unlock it later (see *Unlocking the query* under
[Concepts and synonyms](#2-concepts-and-synonyms)), but the papers shown by then count as seen.

**Defaults and why.** Platforms are rendered in the order you listed at setup; the first is the development
platform. One intermediate representation for all platforms keeps the strings consistent and reproducible. The
validation set is hidden from coverage reports until the synonyms artifact is frozen
(`identification.validation_after_freeze`), because a query tuned on the test papers has no honest test left; a
look before the freeze (`wider coverage --validation`) is possible: you first choose whether the papers shown
count as seen, and the look is listed in History as a `validation_peek`. Turning the hiding off shows the validation
papers everywhere; turning it back on (`wider protocol set identification.validation_after_freeze true`, or Set on
the Settings page) records the papers then in the validation set as shown, kept as validation, so a later revision
records them as used and moving one warns. Every check below reads the development
papers (seen papers included) and reads the validation papers only while they are shown.

**Check before moving on.** `wider query lint` reports no errors, the development-set coverage on the development
platform (next stage) is as high as you can get it, the dry run has no development seed that is a miss for a reason
you have not looked at, and the synonyms version you ran is frozen (`wider construct freeze synonyms <version>`).
Save the rendered strings with `wider query render --out exports/queries` so they are included in the share bundle.

**Commands.** `wider query render [platform...] [--version <n>] [--blocks] [--compact] [--out <folder>]`
(`--blocks` lists each block with the fields it searches),
`wider query lint [--version <n>] [--platform <key>]`, `wider query platforms`, `wider query import <file>
[--platform <key>] [--note "<text>"]` (parse an existing string into a new synonyms version; EBSCO syntax unless
`--platform` names another), `wider query archive [--version <n>] [--list]`
(keep the rendered strings with date, platform and synonyms version in `exports/search_strings/`; freezing the
synonyms does this automatically, and an unchanged string is not stored twice). The commands of the checks are
named under the headings below.
**UI:** the *Search strings* page (strings per platform with paste instructions and, for Google Scholar, the blocks
left out; the blocks and the fields they search; the translation ledger under each string; the linter, "Import a
string", the second reader, the dry run, the rounds and the search-strings archive) and, for the checks after an import, the *Search results* page.

### Checking the strings

For scripts: `wider query ledger`, `wider query parse`, `wider query review`.

Three checks look at the strings themselves before you paste them. None of them changes the query.

**Translation ledger.** Under each string the page lists, as *translation notes*, what that platform's syntax cannot say, or says more widely
than the query, each row with its kind: `lost` (for example "proximity rendered as AND" on PubMed, or ProQuest's `*`
covering at most five characters), `wider` (Scopus's quotes also find variants of a phrase; PubMed searches a
title-only block in title and abstract) or `unverified` (a field code not yet checked against the platform's help
page). Without a `lost` or `wider` row it says "nothing lost". The ledger is computed from the query and the platform
catalogue, not read off the string, so the two cannot disagree. `wider query ledger [--platform <key>]` prints it, and
`wider query render --ledger` prints it under each string. The archive keeps `<string>_ledger.json` next to each
string; on the page a string archived before ledgers existed shows "none (archived before translation notes were kept)"
(the command line says "no ledger (archived before ledgers)").

**Linter per platform.** The linter's *Platform limits* select (`wider query lint --platform <key>`) applies one
platform's limits, such as PubMed's minimum of four letters before `*`. Without a choice it applies the strictest of
all platforms. It also warns about a wildcard after a hyphen (`self-*`) and a term already covered by a truncation
(`worker` next to `wor*`), and a lone `*` is one short-stem error.

**Importing and checking a string.** "Import a string" (and `wider query import --platform <key> <file>`) reads a
string written in EBSCOhost, Scopus, Web of Science, ProQuest, Ovid or PubMed syntax. Google Scholar strings are not
read. Before parsing, a check lists what the parser has to read one way or another, each with its position: curly
quotes, a lower-case operator, AND and OR mixed without parentheses (read AND first, with the reading shown), needless
parentheses, a NEAR over a grouped AND (Web of Science) and a field tag nested in a field group (PubMed). Web of
Science strings are read as Web of Science reads them, and each reading is listed: two words with no operator between
them are ANDed (`late arriv*` is `late AND arriv*`), an untagged group after a tagged one takes that tag
(`AB=(a OR b) AND (c OR d)` searches c and d in the abstract too), and groups nested in one tag keep the tag. Unbalanced
parentheses are an error, and nothing is saved. **Preview** (`wider query parse --platform <key> <file|->`) shows the
findings and the blocks it would make and saves nothing; **Save** writes a new draft synonyms version.

**Second reader.** A language model reads each string against the PRESS checklist (translation of the question,
Boolean and proximity operators, subject headings, text words, spelling and syntax, limits and filters). It gets the
query, the string, its ledger, the linter and check findings and the newest 30 query decisions you did not dismiss,
and never a seed-paper passage. It reads each string twice: with two readers once each, with one reader twice. A
finding is **agreed** when both runs report the same item about the same words, and **unsure** otherwise. A finding
that quotes words not in the string is dropped ("N dropped: quote not found"). The readers come from the "review" row
on Settings (`wider providers stage review NAME [NAME2]`); without one the construct backend reads twice. For one run,
pick the readers in the card's two selects or with `wider query review --reader NAME` (once or twice). The card names
each reader, its model family and where it runs before you start. When both runs use the same model it says "same
model twice", and two models of one family say "same family twice": agreement then says less than two families would.
A backend off this computer asks for its consent first (one dialog per reader). A command-line agent whose command
lacks the flags that switch off its tools is refused before anything is sent (exit 2). What the second reader does
not do: it is a model, not a human peer review under PRESS. Its findings are suggestions only: they change no
string, no synonyms version and no decision, and an empty list does not mean the string is right. Each run is kept
in `runs/query/review/<stamp>/` (`review.json`, and `calls.json` with each call's input and output hashes), and its
calls are logged under the stage `press_review` and counted in `wider costs`. `wider query review [--platform <key>] [--reader NAME] [--json]`
is the command.

### Dry run

For scripts: `wider query dry-run`.

**What happens.** The dry run reads the query against the title, abstract and keywords of each seed paper on your
computer, the way each database would read it. Each database has a profile in `wider/search/platforms.yaml`: the
record fields that each block field searches, the shortest stem allowed before `*` (PubMed 4; EBSCOhost, Scopus and
Web of Science 3; ProQuest and Ovid none), whether `*` still works inside a phrase (not in PubMed), and the range of
the proximity operator and how it counts. Each seed paper gets one of three states on each platform.

- **hit**: every block the query requires has a term that matches under the profile. The terms are listed.
- **miss**: a block has no matching term. The block is named ("no match in block a, b").
- **uncertain**: the answer depends on something WIDER cannot copy, so it does not guess. The reason is shown:
  the paper has no abstract (or no keywords, for a block that searches keywords), PubMed's automatic term mapping,
  the lemmatisation of Web of Science (on unless the term is quoted or truncated), or the stop words of EBSCOhost
  and Ovid. The database may return the paper or not.

An uncertain seed is never counted as retrieved. The recall line says so: "scopus: recall 3/6, 1 uncertain (not
counted)" means three hits, one uncertain and two misses among six seeds. Google Scholar has no profile; asking for
it prints "no profile for gscholar" and exits with code 1. The dry run searches nothing and sends nothing. It is
only as good as the profile: the profile values come from the databases' help pages and are checked by hand
(manual check M46), so treat a hit as "should be found", not as "was found".

**What you decide.** Which synonyms version to check (`--version`; the newest by default) and which platform
(`--platform`; without it every platform that has a profile is run). Whether a miss is a gap in the query or a
seed paper that is hard to find (its "why" column says which block had no match). Whether an uncertain seed needs
a look in the database itself. Three options show more.

- `--terms` lists each term with the seeds it hits and, for a truncated term, the words that `*` matched and what
  a longer or shorter stem would keep or lose: "collaborat* matches collaborate (B002), collaboration (B001,
  B002)" and "collaborate* would keep collaborate and lose collaboration".
- `--grid` prints the term-by-seed grid (x hit, ? uncertain, blank no match), the minimal cover (the fewest terms
  that still retrieve every seed the query retrieves; a greedy choice with a fixed order for ties), the terms that
  match no seed paper, and the two files it wrote beside `dry_run.json` in `runs/query/<stamp>/`: `grid.csv` and
  `grid.svg`, a heatmap with the same cells. A term that is in no minimal cover and matches no seed is a candidate
  for a noise check, not a reason to drop it: seed papers are few.
- Every run also draws the coverage plot, `coverage_by_platform.svg`: for each platform the hits, uncertain and
  misses as bars, with the same numbers as the table below it.

**Defaults and why.** Uncertain is its own state because a wrong miss would send you to widen a query that works,
and a wrong hit would hide a gap. Validation seeds appear in the dry run only for a frozen query; until then the
run says "validation seeds are shown only for a frozen query; none stands". A dry run on a project with no seed
papers exits with code 1 and points to the Seed papers task of the Review plan.

**Commands.** `wider query dry-run [--version <n>] [--platform <key>] [--terms] [--grid]`.
**UI:** the *Search strings* page, *Dry run* card: *Synonyms version*, *Platform* ("Every platform with a profile"
or one), **Run the dry run**; then a table per platform (seed, set, state, terms matched, why), the foldouts
*Terms and what each * matched*, *Grid and minimal cover* and *Heatmap of terms against seed papers*, and the
coverage bars.

### Rounds and acceptance

For scripts: `wider query rounds`.

**What happens.** Each synonyms version is one round of refinement. `wider query rounds` lists the rounds, oldest
first, with the development recall of each platform from its last dry run, the number of records its string
returned, and which round is accepted ("accepted", "accepted (by freeze)" or "accepted earlier"). It also draws
`exports/recall_vs_yield.svg`, recall against records, with the numbers in a text table. The record count is the
number of records in the export you imported for that version and platform. Before you export, you can type the
number the database shows for the string: `wider query count <round> <platform> <n>` stores it and marks it
"(pasted)". Importing an export for that round and platform replaces the pasted number. An export of an earlier
draft attaches to its round with `wider import run --round <n>` (the upload on the *Search results* page has a round
selector); without it the file goes to the newest version. A recall figure marked "seeds changed since this dry run"
is out of date, and `wider query rounds --refresh` runs the dry run again for every round.

**What you decide.** Which round is the accepted one: `wider query accept <round>`. Any round can be accepted, an
older one included. Accepting does not ask for permission and does not stop you; it states what it costs. The
round and the round accepted before are run again on today's seeds on every platform with a profile, and each seed
that the new round loses is named: "v3 loses B002 on scopus, which v4 retrieves." Uncertain seeds are listed
separately and are not counted as lost or found. `wider query accept <round> --check` prints those lines and
changes nothing. The accepted round is the default of `wider query render` and of the Search strings page.

**Defaults and why.** The loss is recorded with the round, so a reviewer can see which seeds a choice gave up. If
you freeze a version that is not the accepted one, the freeze accepts it for you, because a freeze needs no accepted
round: `wider status` shows "v4 was accepted implicitly by the freeze" in the result of the query step, and
`wider query rounds` reads "accepted (by freeze)". A typed count and an acceptance are listed in History and can be
undone.

**Commands.** `wider query rounds [--refresh]`, `wider query count <round> <platform> <n>`,
`wider query accept <round> [--check]`.
**UI:** the *Search strings* page, *Rounds* card: the table of rounds, **Dry-run every round**, *Type a count*
with **Save counts** under each round, and **Accept this round**. A round that loses seeds opens a step first,
"Accept v3?", with the lost seeds listed, **Accept this round** and **Cancel**.

### Replay and provenance

For scripts: `wider query replay`, `wider query provenance`.

**Replay.** Each dry run records a digest of what it computed for its round: the dry run, the grid, the minimal
cover, the diagnosis, the suggested edits and the rendered strings. `wider query replay <round>` computes all of them
again from what the project holds, with no model and no network, and says either that the round replays to the same
result or which part differs. It writes nothing. A round with no record yet says "no record to compare"; its next
dry run writes one.

**Provenance.** `wider query provenance <round>` puts the three ledgers of a round in one view: the refinement
decisions taken in it (a dismissed one marked), the model calls that shaped the query (synonym proposals, name mining
from the seeds, the second reader) with the input and output hash of each call, never a prompt or an answer, and the
outside requests with the switch that allowed each (the DOI consent, the search-term switch or the prior-review
switch). A proposal run made before output hashes were recorded says "output hash not recorded". The command also
writes `exports/provenance_<round>.json`, which the replication package includes for every round.

**Commands.** `wider query replay <round>`, `wider query provenance <round>`.
**UI:** the *Search strings* page, *Rounds* card: **Replay** and **Provenance** on each round; the *Provenance* card
below it shows the view.

### Diagnostic report and clusters

For scripts: `wider query diagnose`.

**What happens.** After you import the exports of a round, the report shows, for each platform, how many records
each block and each term retrieved (a record counts for every term it matches), how many records only one term
retrieved ("by this term alone"), and how many records no term explains: records the database returned for a
reason the profile cannot see, such as a subject heading. It then gives the recall of each seed set, "development
seeds found: 1 of 6, 95% interval 0.00 to 0.64", with the exact (Clopper-Pearson) interval, and names the seeds
the platform missed. The report always ends with the sentence "Recall on known papers overstates recall on the
scattered literature: the papers a researcher already knows are the ones a search finds most easily." A high figure
on your seed papers does not show that the search found the rest of the literature. `wider coverage`, the Coverage
card and the *Seed paper coverage* table of the report show the same "k of n, 95% interval a to b" for each set, each
followed by the same sentence.

**Clusters and noise.** `wider query clusters` groups the imported records by the set of terms that matched them,
with each cluster's size, three sample titles and the seeds in it. A cluster that no seed shares terms with is
where noise is. You mark a cluster `relevant` or `noise` with `wider query cluster <id> <mark>` (`--by` names who
**OpenAlex exports.** An export imported as `openalex` is diagnosed like the catalogue platforms, also when OpenAlex
is not one of your chosen databases, and its records removed appear in the suggested edits. The records are read the
way WIDER sends the query to OpenAlex: every block in title and abstract only (a keywords field is not searched), a
Near group as AND, word variants uncertain because OpenAlex stems its title-and-abstract search, `*` only through the
unstemmed exact field, and no truncation inside a phrase. OpenAlex is still not in `wider query platforms` and gets
no rendered string; the *Dry run* card offers it, unticked. The values are checked by hand against OpenAlex's help
page (hand check M131).

**A sampled export.** When OpenAlex (or any platform) returns more results than you can download, export a random
sample and mark the file with its size: `wider import sample <id|file> --of <N>`, or the *Random sample of* field
and **Save** on the file's row of the Search results page (empty and **Save** clears it, as does `--clear`). Every
record of the sample then counts as N divided by the records in the sample, in the budget line, the counts of the
report, the noise terms, the clusters and the records an edit removes, and each scaled number says so: "about
650,000 records, 645,000 over the budget of 5000 (estimate: openalex is a random sample of 1,249 of 650,000
results)", "a: chart*: about 156,000 records (300 in the sample)". Edits are ranked by the estimated records they
save, and where the sample is the platform's only file the records removed carry a 95% interval ("-about 2,000
records on openalex (20 of 1,000 in a random sample of 100,000; 95% interval 1,230 to 3,070)"). Files of one
platform and version marked with the same N are one sample drawn in parts. Recall is not measured on a sampled
platform: a seed missing from a sample may still be retrieved, so the report counts the seeds in the sample and lists
none as missed, and the indexing check offers none. The mark is listed in History (`source_sample`) and can be undone;
screening, deduplication and the PRISMA counts still count the records you imported, and the report's database line
and the package manifest name the sample and its N.

marks it; `--platform` and `--version` pick one when the id exists on more than one). The mark is your judgement
about the records' topic and nothing more: no record is screened or excluded by it, and History lists the mark and
can undo it. A noise term is a term whose records share no matched term with any seed paper and belong to no
cluster marked relevant; the report gives its record count and its share of the platform's records ("a: "mobile
working": 3 records, 75.0% of scopus"). A term in a cluster you marked relevant is not listed as noise.

**What you decide.** Which cluster is noise and which is on topic; whether a noise term should go. Nothing is
removed for you: the suggested edits below carry the consequence of each change.

**Defaults and why.** Like every check here, the report reads the development seeds and the validation seeds only
while they are shown. The version defaults to the newest one an export was imported against.

**Commands.** `wider query diagnose [--version <n>] [--platform <key>]`, `wider query clusters [--version <n>]
[--platform <key>]`, `wider query cluster <id> relevant|noise [--by <name>] [--platform <key>] [--version <n>]`.
**UI:** the *Search results* page, *Diagnostic report* card (**Diagnose**, *synonyms version*, *platform*): counts,
recall with the interval, noise terms, and the clusters with **Relevant** and **Noise** buttons.

### Indexing check

For scripts: `wider query indexing`.

**What happens.** A missed seed paper has two possible causes: the query did not find it, or the database does not
hold it. For each seed a platform did not retrieve, WIDER writes a known-item string in that platform's syntax: the
DOI form where the platform can search a DOI and the paper has one, with the title phrase as the alternative, and
otherwise the title phrase alone. For example, Scopus gets `DOI("10.1000/d3")`, with
`TITLE("Telework adoption in organisations")` as the alternative. You paste the string into the database's own
advanced search and record what you found. You never run anything from WIDER, and the check is only offered for
platforms with an export imported against them.

**What you decide.** The verdict for each missed seed and platform: `indexed` (the database holds the paper),
`not_indexed` (it does not) or `unknown`. The diagnosis then says "not in the database" or "indexed but not
retrieved" for each missed seed, and "indexing not checked yet" for those without a verdict. "Indexed but not
retrieved" is the case where the query is at fault; look at it first.

**Defaults and why.** Every template is marked "not checked by hand yet" in the list, because the strings are
facts about each database's syntax that only a live search can confirm (manual check M47). If a string is refused or
finds the wrong paper, search the title by hand and record the verdict anyway; the template is what is wrong, not your
verdict. A verdict is listed in History and can be undone. A verdict that is not one of the three is refused with
exit code 1.

**Commands.** `wider query indexing list` (the strings and any verdicts, per platform), `wider query indexing set <seed>
<platform> indexed|not_indexed|unknown`.
**UI:** the *Search results* page, *Indexing check* card: per platform a table with the string, **Copy**, and the
buttons **Indexed**, **Not indexed** and **Unknown**.

### Suggested edits and the record budget

For scripts: `wider query edits`.

**What happens.** `wider query edits` lists edits you could make, each with what the dry run says it would do: add a
name from a missed development seed to a block, add the keywords field to a block, change a stem (the truncation
report shows what a longer or shorter stem keeps), or drop a noise term. Each line gives the seeds gained or lost on
each platform and, once an export is imported, the records the edit would remove. Edits are ranked by development seeds
gained. When the number of records of the accepted round is over the record budget, the edits that save the most
records per seed kept come next. The budget is `identification.record_budget` in the protocol (default 5000); set it
with `wider protocol set identification.record_budget <n>`. With a budget of 3 the report says "4 records, 1 over the
budget of 3".

**What you decide.** Whether to apply an edit. `wider query edit apply <id>` (for example `E2`) writes a new draft
synonyms version whose note names the edit. A frozen version is never changed, and nothing changes until you apply.
The budget is a number that you set from the size of the screening you can do; WIDER only reports against it.

**Commands.** `wider query edits [--version <n>]`, `wider query edit apply <id> [--version <n>]`.
**UI:** the *Search results* page, in the *Diagnostic report* card: the *Suggested edits* list, each edit with its
consequence and **Apply**. Applying makes a new draft on the Concepts and synonyms page.

### Names from connected papers

For scripts: `wider query missed-names`.

**What happens.** Candidates linked to several of your seed papers (see [Candidates from seed
papers](#candidates-from-seed-papers)) are often on your topic. `wider query missed-names` runs
the dry run over those that are linked to at least two seeds (`--min-links N`) and lists the words of their titles
and keywords that two or more missed candidates share and that no block holds. Each name comes with the block its
candidates fail and its consequence, for example "turns 2 connected candidates into hits, +0 development seeds;
connected, not judged": connected means linked to your seeds, not judged relevant by anyone. Validation and
dismissed candidates are left out and counted, so a validation paper never shapes the query. A candidate the dry run
cannot decide (often one without an abstract) is counted as uncertain and not used. `--model` also asks the
construct backend which phrases of each missed candidate name a failed block; a phrase that is not in that
candidate's title, abstract or keywords is dropped and counted. The model gets each candidate's title, abstract and
keywords under the backend's `research` consent.

**What you decide.** Whether to add a name. Nothing changes until you do: `wider seeds names --admit NAME --block K
--note "..."` writes a new draft synonyms version, and the note (prefilled with the candidates the name came from)
records why, with who and when in History.

**Commands.** `wider query missed-names [--version V] [--platform P] [--min-links N] [--model] [--json]`,
`wider seeds names --admit NAME --block K --note "..."`.
**UI:** the *Search results* page, *Diagnostic report*, the *Names from connected papers* block: each name with its candidates, a note
field prefilled with the candidates it came from, and **Add to block**, which asks before it saves.

### The OpenAlex check

For scripts: `wider query yield --query`.

**What happens.** The whole query, rendered for OpenAlex's title-and-abstract search, is counted there: "N works on
OpenAlex (field), date". The check shows the exact string sent and a ledger of what OpenAlex cannot say or says more
widely than the query: a Near group becomes AND, keywords are not searched, a block limited to titles is searched in
title and abstract, a comma is sent as a space, and either every word is stemmed (no truncation in the query) or, with
truncation, nothing is stemmed (`title_and_abstract.search.exact`). This count is not comparable with a per-term
count, which uses OpenAlex's general search; the check says so. `--seeds` also asks which Development seeds the query
finds on OpenAlex and sets this beside the dry run of the first database with a profile: "dry run hit, OpenAlex miss"
points to an indexing or field difference, not necessarily a gap in the query. Only Development seeds with a DOI are
asked about; a seed without a DOI, whose DOI cell holds more than one DOI, or whose DOI a Validation paper also has,
is listed as not checked and its DOI is not sent. When OpenAlex does not answer a batch in full, its seeds read "not
checked", never as a miss.

**A query too long for one request.** OpenAlex refuses a request URL over 8,190 bytes. WIDER then sends the query in
parts, each with one OR block cut into pieces, and reports a range: "between L and H works", where L is the largest
part's count and H the sum of the parts' counts (works found by more than one part are counted more than once; the
exact union would need paging through every work, which needs an API key). The counts of the parts are listed. A block
under NOT or used twice in the logic is never cut; when no block can be cut, nothing is sent and the check says so.
When OpenAlex refuses a request, the check says why (its message and the HTTP status). The seed check sends at most 50
DOIs per request and keeps every request under the limit.

**What leaves the computer.** The count sends the query string under the search-term switch (`wider lookups terms
allow`) and nothing else from the project. The seed check also sends the DOIs of the Development seeds, never a
Validation paper's, and needs the online-lookup consent as well; with either off, nothing at all is sent. Answers are
cached like the term counts (work ids only, no titles), and each request is logged as `query_yield` or
`query_seed_check`. Allowing the online-lookup consent here allows it for the whole project; the dialog says so.

**Commands.** `wider query yield --query [--version V] [--seeds]`; `wider query yield TERM` counts one term. Exactly
one of TERM or `--query`.
**UI:** the *Search strings* page, under *Yield*: the *OpenAlex check* with **Count the query** and **Count and check
seeds**; a consent dialog names the switch that is off.

### Model passes on any backend

The model passes of this part (names from seed papers, names from connected papers, the model tier of the reference
lists) use the backend of the stage `construct` and run on any backend kind: a local model (Ollama, or an
OpenAI-compatible server on 127.0.0.1), an API (Anthropic, OpenAI-compatible) or a CLI agent with its tools off. Each
asks the same template at temperature 0 with the project seed, at most three calls per item, and keeps only text that
is in what it was given, so the verified result does not depend on the kind. Each run records the backend's kind
and identity, and `wider costs` lists its calls under the pass's stage (`missed_names`, `ref_structure`). With no
backend configured, `wider query missed-names --model` and `wider benchmark references --tier model` give the result
without the model and say that the model pass did not run.

### Platform limits

For scripts: `wider query limits`.

**What happens.** Limits you set in a database's own form (a date range, languages) are outside the query. Record
them so the string and its limits are kept together: `wider query limits scopus --dates 2010..2024 --languages en,de
--reason "<why>"`. They appear under the rendered string, "# limits: dates 2010..2024; languages en, de; reason:
protocol limits", in the package's `limits.json` and in the History as `platform_limits`, which you can undo. A date
range that does not parse, or ends before it starts, is refused (exit code 1). Without options the command shows the
limits now set.

**What you decide.** Whether the limits are part of the protocol and what the reason is. An empty `--dates ""`
clears the range.

**Commands.** `wider query limits <platform> [--dates <a..b>] [--languages <list>] [--reason "<text>"] [--by <name>]`.
**UI:** the *Search strings* page, under each string, *Limits applied in this database*: *Dates*, *Languages*,
*Reason* and **Save limits**.

### Keyword fields and field codes

**What happens.** A block can search `title`, `abstract` or `keywords` (author keywords). Without a field a block
searches title and abstract. Set the fields of a block in the synonyms file (`fields: [keywords]` on the block) and
save it with `wider construct save synonyms <file>`. `wider query render --blocks` and the Search strings page list
them ("Wellbeing: keywords"). The dry run reads seed keywords, which you type with
`wider benchmark keywords <id> "a; b"` or in the Setup step; an import fills empty ones from records with the same
DOI. Each platform renders the field with its own code: EBSCOhost `KW`, Scopus `TITLE-ABS-KEY`,
Web of Science `AK=` (and `TS=` when a block names title, abstract and keywords), ProQuest `IF`, Ovid `.kw.`,
PubMed `[tiab]`.

**Not yet verified.** None of these codes has been checked against the database's help page. Each rendered string
says so in its header ("# KW: keyword field code not yet verified against the platform's help page"), and the
platform catalogue marks each `verified: false`. Two codes also search more than the field: Scopus
`TITLE-ABS-KEY` covers title and abstract too ("keywords searched more widely: the code also covers title and
abstract"), and PubMed has no separate keyword tag, so every term is `[tiab]`, which includes author keywords. Open
the database's help page, or run the string, before you rely on a keywords block (manual check M46).

**Commands.** `wider query render --blocks`, `wider benchmark keywords <id> "<a; b>"`.
**UI:** the *Search strings* page, *Blocks and the fields they search*, and the notes under each string; seed
keywords in the Seed papers task of the Review plan.

## 4. Search results

For scripts: `wider import`.

**What happens.** Each database export goes under `inputs/<platform>/`, either by hand or with
`wider import add <platform> <file...>`, which checks every file first and copies none when one is unreadable
(exit code 2). Neither `import add` nor an upload on the Search results page ever overwrites a file: the same bytes as a file
already there are not saved again (the verdict says `duplicate`), and a file whose name is taken is saved as
`name-2.ext`. WIDER reads RIS, CSV and tab-delimited
exports, XLS/XLSX (`pip install "wider[import]"`) and BibTeX, recognises the column layout of each platform from
the header (`wider/ingest/profiles.yaml`), standardises the fields (title, authors, year, journal, DOI, abstract,
keywords) and stores the records with their source file and format. Each file is imported once, by content hash,
so re-running is safe. Every file then gets a sanity verdict: `ok`, `capped` (the record count equals the
platform's known export cap for that format, so the download was probably truncated to one batch; a `.txt` export
is checked against the platform's text-export cap), `off_topic`
(no seed paper found and too few records carry a term of the query), `empty`, or `duplicate` (the same bytes as
an earlier file). An `off_topic` or `empty` file is imported but starts out of deduplication, with a line naming it
and the check's reason; tick *Enters deduplication* (or `wider import include <id>`) if the file is right. Coverage then checks, per platform or for the deduplicated corpus, which seed papers came
back: by DOI, then exact normalised title, then fuzzy title.

**What you decide.** The export settings in each database (full records with abstracts; all results, not the
first page; a format whose cap is not below your result count, see the catalogue in the Databases task of the Review plan), one folder
per platform, what to do with a `capped` or `off_topic` file (export again, or page through the results), and
whether a fuzzy coverage match is really the seed paper (those are marked "confirm"). If a seed paper has no
abstract yet, `wider coverage --backfill-abstracts` copies it from the matched record.

**Defaults and why.** The `imports` section of the protocol holds the sanity thresholds: a file fails the
benchmark signal below `imports.min_benchmark_hits` (1) seed papers, fails the vocabulary signal below
`imports.min_vocabulary_overlap` (0.5, the share of records whose title or abstract contains at least one term of
the approved synonym blocks), is `off_topic` only when every available signal fails, and is flagged as `capped`
when `imports.warn_on_export_cap` is on. The original review lost half of one platform's records to a 200-record
RIS cap and received a whole export that belonged to a different search; both would have been caught here. A
seed paper counts as found by DOI or exact normalised title; a fuzzy title match is a candidate above
`identification.jw_benchmark` (0.88) and is always marked "confirm" until you confirm it. A confirmation belongs to
one match: when a later check finds a different record or method for the seed paper, it must be confirmed again.

**The validation set before the freeze.** While the query is not frozen and the uploads are not marked final,
coverage reports only the development set (`identification.validation_after_freeze`, in the CLI and the UI alike). `wider coverage --validation`, or the *include validation set* box on the Search results page, shows it anyway, but
first asks what the papers shown mean for the test. *Count them as seen* (`--count-seen`): they no longer validate
the search, and only papers added to Validation later can. *Keep them as validation* (`--keep-validation`): they
still count, and every output states that they were shown. Neither button is preselected; without a flag, or with
both, the command prints the question and exits 2. The same choice is asked by `wider rank validate --validation`
and `wider rank ablate --validation` before the freeze. The look is recorded in the history as a `validation_peek`
with the date, the papers shown and your choice; it cannot be undone, because the validation set is meant to be
tested once, and the validity note in the report, the methods draft and `manifest.json` names it.

**These uploads are final.** When every export you mean to use is imported, `wider import final` (or *These uploads
are final* on the Search results page) records that the uploads are complete. It unlocks the validation set for
coverage, the sanity check, `wider rank validate` and the ablation, as a frozen query does, without a
`validation_peek`. On the page the button asks first, and once the mark stands it is replaced by the time of the
mark. It is refused while no file is imported ("no files imported yet") or while a saved file waits to
be imported (the message names it and says "Import the uploaded files first"; a file that cannot be read is named with its
error and "Fix or delete it in inputs/<platform>/, then try again"); the page shows the refusal under the button and
keeps the button. Redoing an undone mark (undoing its undo on History) is checked the same way. Marking again only reports the time of the existing mark. The mark is listed in History (`uploads_final`) and
`wider history undo ID` removes it. A file added after the mark (an upload, `wider import add`, `wider import run`
or citation chaining) reopens the uploads: it is imported, the mark is cleared, and an unlock with cause `uploads`
is recorded ("Uploads reopened: <file> was added after they were marked final on <date>. …"). When the validation set
holds papers, WIDER asks first whether they count as seen, as for an unlock of the query, with the same two answers
and the `--count-seen` / `--keep-validation` flags on `wider import add`, `wider import run`, `wider prior-reviews
chain` and `wider history undo` (undoing the mark, by id or your newest change). A synonyms save while the uploads
are final asks the same.

**Files that enter deduplication.** Every imported file has an id (`source_id` in `wider import list`) and enters
deduplication unless you exclude it: `wider import exclude <id>` and `wider import include <id>`, or the *Enters
deduplication* checkbox on each imported row of the Search results page. A file name works instead of the id only
when one imported file has it; otherwise the command lists the candidates with their ids and changes nothing. An
excluded file stays listed and in coverage, and the PRISMA counts show its records as "Records not used (file
excluded)". Excluding is refused while later stages hold decisions on records that only that file brought. Each
switch is listed in History (`source_included`) and can be undone.

**Check before moving on.** The stage counts as done once records are in the store. Before dedup, run
`wider import check` and look at the verdicts, then at the coverage table per platform: the development papers
should be found on the development platform; a missing one usually means a missing synonym block or an export
problem. The footer of the check shows the real state of the synonyms (approved or frozen version) and the number of
seed papers it checked against.

**Commands.** `wider import add <platform> <file...>`, `wider import check`, `wider import run` (or plain
`wider import`), `wider import final`, `wider import list [--json]` (every imported file with its id, platform,
records, import time, whether it enters deduplication and the query version that was frozen when it was imported,
"not recorded" for files imported before versions were kept, and the final mark; it changes nothing),
`wider import include <id|file>`, `wider import exclude <id|file>`, `wider import sample <id|file> --of <N> | --clear`
(see [Diagnostic report and clusters](#diagnostic-report-and-clusters)),
`wider coverage [--corpus] [--validation [--count-seen | --keep-validation]] [--backfill-abstracts]`.
**UI:** the *Search results* page (files found per platform with their format and verdict, upload
of new files, *These uploads are final*, the *Enters deduplication* checkbox and the *Random sample of* field per imported file, coverage per platform or for the deduplicated corpus with the
*include validation set* box in the toolbar under the heading, prior-review coverage, and the citation
chaining card described under [Prior reviews](#prior-reviews)).

## 5. Deduplication

For scripts: `wider dedup`.

**What happens.** Records from all platforms are merged into unique records in three passes: shared DOI, exact
normalised title, then fuzzy title with corroboration from year, first author and journal. Pairs the rules cannot
settle go to a manual queue; your decisions are stored and re-applied on every rerun. The run writes PRISMA
identification counts and audit CSVs (`exports/dedup/`) listing every pair with the rule that decided it.

**What you decide.** The manual queue: `duplicate`, `not_duplicate` or `uncertain` for each pair, with your name on
the decision. Decisions made earlier elsewhere can be imported from a CSV.

**Defaults and why.** The `dedup` protocol section carries the original rule set. A pair enters the fuzzy pass at
title similarity 0.85 (`jw_candidate`). It is merged automatically at 0.97 with a full citation match
(`jw_auto_merge`: same year, first author and journal, no conflicting publication type, and the article number or
volume plus first page), or at 0.90 when the year and the first author or journal agree, at least two locators
(volume, issue, first page, article number) match and neither the DOIs nor the publication types conflict
(`jw_corroborated`). `jw_strong_title` (0.95) merges nothing by itself: a pair with the same first author and year
at or above it is put near the top of the manual queue, and a pair with different DOIs and different first authors
below it is ruled not a duplicate. Records sharing a DOI go to the queue as DOI conflicts when their titles are below
0.70 (`doi_conflict_title`) or their years are more than two apart, unless year, first author, journal, volume and
issue or first page all match. No rule merges on the title alone, at any length; for short titles of four words or
fewer (`generic_title_max_words`; "Introduction", "Erratum") an exact-title merge on two locators also needs the
first author or journal to match. Rules alone over- or under-merge; a queue with named rules keeps the PRISMA counts
auditable.

**Check before moving on.** `wider dedup run` reports how many pairs still wait for a manual decision; work through
`wider dedup queue` (or the UI page) and rerun until none are left. `wider dedup prisma` shows the identification
counts that will appear in the report; while uncertain pairs remain, the count of unique records is labelled
"Provisional records remaining after automated deduplication".

**Re-running after later stages.** You can run `wider dedup run` again at any time, also after enrichment, ranking,
samples or screening exist (new exports in a living review, a decided queue pair, a changed threshold). Unique record
ids are stable: a record keeps its id as long as its cluster keeps the same root raw record; records that appear new
get fresh ids (retired ids are never reused); when two unique records merge, the decisions, labels, sample rows,
rankings, enrichment log, notes and tags of the merged-away record move to the surviving one; an enriched abstract is kept unless
the exports now carry a longer one. The summary reports `unique_records_kept_from_previous_run`,
`unique_records_new`, `unique_records_merged_into_existing` and `unique_records_dropped`. The run is refused, and
nothing is changed, in two cases: two merging records were both screened under the same protocol (or labelled by the
same reviewer in the same sample), or a record that later stages refer to has no
successor because its source file was removed (a note or tag on such a record also counts, so no note vanishes). The message names the records and tables; reconcile those rows and
run again (no page offers this yet). Where both merging records are in one ranking, the survivor's ranking row is kept.

**Commands.** `wider dedup run`, `wider dedup queue [--limit <n>]`,
`wider dedup decide <raw_a> <raw_b> duplicate|not_duplicate|uncertain --by "<name>"`,
`wider dedup import-decisions <file.csv>` (WIDER's own `05_manual_duplicate_review.csv`, matched on the raw ids,
or the original pipeline's file, matched on title, platform and year), `wider dedup prisma`. `wider guide dedup [--json]` prints the
guide the page's *Guide* button opens (what the step is for, what to do now, done when, watch out), with the notices
for the project's state first ("12 pairs wait for your decision. …"); for a page without guide text it says so and
exits with code 1. `decide` refuses an
unknown decision word, an unknown record id or a pair of one record with itself (exit code 1).
**UI:** the *Deduplication* page. The top bar carries the one main button, *Run deduplication* (after the first
run *Apply decisions and check again*), and the *Notebook* and *Guide* buttons. Under the heading a flow chart shows
the counts that go into the PRISMA diagram: *Records imported*, *Duplicates removed* (split into "by WIDER" and "by
your decisions"), *Pairs to decide* and *Records left*; while pairs are open or unsure, records left is marked
provisional. Each box is a button that fills the list below it: the imported files (as `wider import list`), the
merged pairs with who merged them (`wider dedup merged`), the pairs to decide, decided ones included with their
signer (`wider dedup queue --all`), or the records left (`wider dedup records`). Beside the chart the page says in one
line what to do next, and errors and the run's Details stay there too. The page opens on *Pairs to decide* while any
pair is open, else on *Records left*.

The list sits beside a large pair view: both records side by side, each field with a match word ("Same", "Spelling
differs", "Only in A") and the differences marked, and big buttons **1** *Duplicate*, **2** *Not duplicate* and
**3** *Unsure*. **j** and **k** move through the list, **Esc** goes back to the list (the pair stays shown), and the
keys work only on this page. A decision needs your name; the view says "Deciding as …", and the name is changed beside "Working as" in the top
bar. Under
the pair, "This pair's history" lists what the runs and reviewers did with it; *Full step notebook* opens the
notebook panel, where a decision can be undone and redone and an earlier run's result shown. *Guide* opens a short
guide in four parts (`wider guide dedup`). A merged pair and a record left are shown read-only.

## 6. Missing abstracts

For scripts: `wider enrich`.

**What happens.** Unique records with a missing or thin abstract are looked up by DOI in OpenAlex, Crossref,
Europe PMC and Semantic Scholar, in that order, and records without a DOI by exact title. A found abstract is
stored with its source; nothing already present is overwritten, every fetch is logged, and the run resumes where
it stopped.

**What you decide.** Whether to run it at all (it sends DOIs and titles to those four public services, so it needs
the project's consent for [online lookups](#online-lookups)), how many records per run (`--limit`, at
least 1), and whether to retry records that failed or were exhausted earlier (`--retry`; the earlier log rows are
kept and new ones appended).

A Semantic Scholar API key is read from the environment variable `S2_API_KEY`; set it before starting WIDER. The
page and `wider enrich --status` say "S2_API_KEY set" or "S2_API_KEY not set" and never show the value. Without a key
the lookups still work, but Semantic Scholar may limit the rate, and records it turned away wait for
`wider enrich --retry`.

A lookup that could not be completed (no connection, a 429 or 5xx answer, an unreadable answer) is marked `failed`,
not `exhausted`, and raises no question: run again with `--retry` later. `exhausted` means every source answered and
none had the abstract.

**Defaults and why.** An abstract with fewer than `corpus.thin_words` (20) words is "thin": it is enriched,
excluded from the embedding of its own text, sampled as a separate pool for labelling and never leads to an
"absent" grade in screening. A title lookup must reach similarity 0.95 and the year within one
(`enrichment.title_search_min_jw`). Ranking and screening read the abstract, so a missing one would otherwise be
scored as irrelevant.

**Check before moving on.** `wider enrich --status` shows how many records are still thin. Some always remain
(book chapters, old items); they are handled by the thin pool later, so do not chase them forever.

**Commands.** `wider enrich [--limit <n>] [--retry] [--status]`.
**UI:** the *Missing abstracts* page (a batch-size field, **Fetch next N**, **Retry failed lookups**, the
`S2_API_KEY` line and the summary table; the first fetch asks for online-lookup consent).

## 7. Ranking

For scripts: `wider rank`.

**What happens.** Every unique record (title, abstract and cleaned keywords) is embedded once per embedding model
and cached. Each record is then scored by its cosine similarity to the approved anchor sentences, one per cell, and
its score is the mean of its top-N cell similarities. The ranking **sorts, never filters**: nothing is excluded
here. Validation shows where each seed paper lands and recall@K; an ablation compares anchor versions, embedding
models and top-N without changing the ranking you chose; clustering of the top of the ranking describes the topics found and feeds the development
sample.

**What you decide.** The embedding model (one of the project's `embeddings:`, chosen on the cards at the top of the
Ranking page, see [The embedding picker on Ranking](#the-embedding-picker-on-ranking)), the anchors version, whether to
compare alternatives with the ablation, and how far down the ranking to look in the explorer. Seed papers that
rank poorly are a signal that an anchor sentence is off; fix the anchor and re-rank.

**Defaults and why.** `ranking.top_n_cells` is 2 (the review found one cell too narrow and three too diffuse; 1 and
3 are still reported for comparison in `top_n_compare`). Recall is reported at ranks 100, 250, 1000 and 3000
(`recall_at`). Top-N can be 1 up to the number of cells of the anchors version: the default is lowered to the
number of cells when there are fewer, while a value you give outside that range is refused (an empty top-N field on
the page means the default). The top 3000 are clustered into 8 topics with 8 distinctive terms each (`cluster_top`,
`cluster_k`, `cluster_terms`), and text is cut at `corpus.max_words` (3000) before embedding. A ranking lets the
human and LLM effort concentrate where relevant papers are dense while the probability sample over the whole
ranking keeps the recall estimate honest.

**Check before moving on.** `wider rank validate`: the development papers should sit near the top (median rank
well inside the first band); after the freeze, add `--validation` to see the held-back set once. Before the
freeze `--validation` still shows it, as an early peek: the command warns and the look is recorded in the history as
a `validation_peek`, as in coverage. The clusters'
terms should read as topics of your literature, not as artefacts of one platform.

**Commands.** `wider embed corpus [--embedding <name>] [--allow-download]`,
`wider rank run [--embedding <name>] [--anchors <version>] [--top-n <n>]`, `wider rank validate [--validation]`,
`wider rank ablate [--validation]` (the validation set only once the query is frozen or the uploads are marked final;
before that `--validation` is refused with exit 1, as the page refuses it; the table's headers read "development
median rank", "validation found" and so on, the same as on the page), `wider rank cluster [--k <n>] [--top <n>]`,
`wider rank show [--ranking <name>] [--cell <cell>] [--offset <n>] [--limit <n>]` (the explorer: one page of the
ranking, or of the records whose best cell is `<cell>`, with the count the filter holds).
**UI:** the *Ranking* page (embed, rank, validate, ablation, clusters, and an explorer that pages through
the ranking by cell). Beside **Ablation**, the box **Include the validation set** adds the validation columns, as
`--validation` does; while the validation set is locked the box is greyed out and the line under the buttons says it
opens once the query is frozen or the uploads are marked final.


**Versions and "needs review".** Every ranking run is kept as a version (`version 1`, `version 2`, …) of one ranking
name; the explorer and the samples use the newest unless a sample was drawn from an earlier one, and **samples keep the
version they were drawn from**: updating the ranking never redraws or changes a sample. The select on the Ranking page
lists ranking names, and "N versions" under a fold lists each version with the samples drawn from it. A version
**needs review** when its records or settings changed after it was made (records added, merged or given an abstract;
anchors or the embedding text changed). The Ranking page then shows a box per version: the reason, one row per sample
drawn from it, and **Keep this ranking** (or **Keep version N** when several need review). Keep clears "needs review"
until something changes again, is undoable in History and is named in the methods draft; nothing is rerun. A
development sample that is already labelled can only be kept (a fresh purposive draw would not replace its labels); an
unlabelled development sample also offers **Draw it again**, and a validation or other probability sample **Draw a
fresh sample**. **Update ranking** (on Ranking and on the Overview's needs-review item) ranks again; when the stored
embeddings were made from older texts, it first shows what re-embedding sends and costs, with **Embed the changed
texts and rank**, **Rank with the older embeddings** (the new version keeps "needs review") and **Cancel** (nothing is sent and
no version is added). A cloud embedding backend still asks its consent. On the command line:
`wider rank update [--use-older-embeddings] [--allow-download] [--yes]` and `wider rank keep [--version N] [--by NAME]`;
`wider rank show` prints the versions, each needs-review line and the keep note.

## 8. Samples and labelling

For scripts: `wider sample`, `wider label`.

**What happens.** Two samples are drawn from the ranking. The **probability sample** (named `validation`) is
stratified by rank band and is the basis of the recall estimate: every record in the first band, a random draw
from each later band, each with a known weight. The **development sample** (named `development`) is purposive:
likely includes per cell, records close to each near-miss description, cluster medoids for topical coverage, thin
abstracts, a few clearly irrelevant records from the tail, and boundary cases: records close to both a criterion and
a near-miss or adjacent entry. Both are labelled in a blinded queue in the UI (or
exported to CSV and labelled elsewhere, including in Rayyan) by one or two reviewers. Agreement, adjudication and a
lock finish the stage.

**The development sample comes first.** The development sample is complete before any model output on it: a screening
run, evaluation, comparison or replay that includes its records is refused while it has records without a final label
(`wider` exits with 1; the page shows the same sentence), for example "the development sample has 3 records without a
final label; label them all first". How many reviewers make a label final is the protocol setting
`sampling.development_reviewers`: 1 (the default) or 2, in which case two reviewers label each record and
disagreements are adjudicated; an open review package with development records also keeps the sample incomplete. The
setting is on this page's settings card, with a line that says, before you save, which samples the other value leaves
incomplete and which model output it would hide (`wider protocol set sampling.development_reviewers 2` prints the same
line). Model decisions made earlier on its records are hidden, not deleted, until the sample is complete, and exports
with model decisions wait.

**What you decide.** Who labels (one reviewer, or two with agreement and adjudication), the decisions themselves
(`include`, `exclude`, `maybe`, with an exclusion reason and any project labels), and when to lock each sample.
Label the probability sample before you look at any LLM output on it; it is the ground truth the recall estimate
is measured against. The development sample is where you tune the screening prompt, so it should be labelled
first.

**Defaults and why.** The bands in `sampling.bands` are the original design: ranks 1–100 are a census, then 70 of
101–300, 60 of 301–1000, 46 of 1001–3000 and 40 of the rest; the recall estimate weights each band by
N_h/n_h, so relevant papers deep in the ranking count for more. Here n_h is the band's sampled records, and recall
is estimated only once every one of them is labelled (section 10). The development pools follow the review's
quotas: 4 likely includes per cell from its top 100 (`per_cell`, `pool_size`), 4 records per near-miss description
from the 12 most similar within rank 3000 (`per_near_miss`, `near_miss_pool`, `near_miss_max_rank`), 2 per cluster
with the medoid first (`per_cluster`), 5 thin abstracts (`n_thin`) and 3 from the tail (`n_tail`). Last come 4
boundary cases per near-miss entry and per approved adjacent literature (`per_boundary`): at random from the 12
records within rank 3000 that score highest on both the best cell and that entry (the lower of the two rank
percentiles). They are drawn after every other pool, so the other pools keep their records for the same seed; their
pools are named `boundary:near_miss:<key>` and `boundary:adjacent:<key>` and are reported under `boundary`. With no
near-miss entry and no approved adjacent literature there is no boundary pool, and `wider sample status` says "no
near-miss or adjacent entries". `per_boundary` is a new quota, so the protocol hash of every project changes once;
the hash is recorded, not enforced. Human labels on a known design are what make the LLM's recall measurable; the
purposive set puts the hardest cases in front of the prompt.

**Adding boundary cases to a sample you already have.** `wider sample extend development --pool boundary` adds the
boundary pool to an unlocked purposive sample, for every near-miss entry and approved adjacent literature that has
none in it yet (an entry added later gets its own pool; a second run adds nothing and says so). Your labels stay,
labelled records keep their places in the queue, and the new records are shuffled in among the unlabelled ones, so
the queue does not show which records are boundary cases. When every earlier record is already labelled, WIDER says
that the added records are recognisable as the boundary pool. The output gives the number added per pool and names
no record. A locked sample is refused (unlock it first), and so is the probability sample. A sample imported from a
CSV file extends with the project seed, and one imported without a ranking is refused. The extension is in
`wider history` as "sample extended"; undo removes the added records and restores the queue, unless one of them has
a label or an adjudication.

**Check before moving on.** `wider sample status` shows progress per reviewer. With two reviewers, run
`wider label agreement` and adjudicate the disagreements; then lock the sample. Locking freezes the sample's
design (no redraw, no added records) and allows the blinded fields (band, pool, rank) to be shown; missing labels can
still be added, and a change to a saved label becomes a correction (see *Locked samples, corrections and the seal*
below). `wider sample unlock` reopens the design. Locking or
unlocking a sample that does not exist is an error, and redrawing a sample nobody has labelled yet keeps its id.

**Locking and unlocking are recorded.** Both are signed changes in History (*sample locked*, *sample unlocked*; the
name comes from `--by`, else `WIDER_REVIEWER`, else your login; on the page, the name you work under). You can undo
a lock in History until a recall result on that sample is seen; after that, History says "recall on <sample> was
seen after the lock; unlock it instead, which is recorded". An unlock is final: it cannot be undone. When a recall
result on the sample was seen before you unlock it, WIDER says "The recall estimate on <sample> is no longer blind:
its result was seen before this unlock." (in a project whose recall looks were taken before WIDER recorded them:
"may have been seen"); the command then changes nothing and exits with code 2 until you add `--yes`, and the page's
dialog unlocks only when you choose *Unlock*. Every unlock gets one sentence in the methods draft (sample, date, who,
and whether the recall result had been seen) and one row in `sample_unlocks.json` in the replication package.

**Commands.** `wider sample draw [probability|development|both] [--ranking <name>] [--seed <n>]`,
`wider sample status`, `wider sample extend <sample> --pool boundary [--author "<name>"] [--json]`,
`wider sample lock <sample> [--by "<name>"]`, `wider sample unlock <sample> [--by "<name>"] [--yes]`,
`wider sample import <name> <file.csv> [--kind probability|purposive] [--reviewer <id>]`;
`wider label export <sample> <out.csv> [--reviewer <id>]`,
`wider label import <sample> <file.csv> [--reviewer <id>] [--overwrite]`,
`wider label agreement <sample> <reviewer_a> <reviewer_b>`,
`wider label adjudicate <sample> <record_id> <decision> [--by "<name>"] --reason "<text>"` (the adjudicator defaults
to `$WIDER_REVIEWER`, then `$USER`; an adjudication needs a name, and an unknown decision word is refused).
**UI:** the *Samples and labelling* page (draw samples; "Add boundary cases" on the card of an unlocked
purposive sample; the blinded screening queue as a searchable table that opens
each record in the record drawer, keys 1 include, 2 exclude, 3 maybe, j/k next and previous; second reviewer,
agreement, and adjudication in the drawer with both reviewers' decisions side by side; lock).

**The labelling queue.** The page says which name signs your labels ("Signing as ana"); when the server was started
with a different `WIDER_REVIEWER`, it also says "the command line signs as bo", because commands sign with that name.
With no name the page says "no name yet: labels need your name". In the blind queue each row is a blind item: the
page never learns which sample a record came from, so it cannot show it by mistake, and a label saved there names no
sample in History ("ana labelled record 12"). Choose one sample in *Sample* to open only its queue; a locked sample
opens like any other: a missing label saves at once, and a change to one of your saved labels first asks for a
correction reason.
Untick *Blind* to see the sample, band and rank of each row (for a sample whose fields you may see). When the sample was locked in another tab, a
missing label is still saved, and a change to a saved label opens the correction dialog and saves nothing until you
give a reason. `wider label export <sample> <file.csv>` writes the same rows with a `read_only` column, now always
"no", which `wider label import` ignores. The re-check and second-reviewer queues open
only for the name you sign with ("this queue is for the name you sign with"; on the command line `--reviewer` names
the queue); their drawer says "earlier labels are not shown in this queue". While the drawer moves to the next record
it shows "loading" and nothing of the previous one; a key pressed then decides nothing.

**Plan the validation sample.** `wider validation plan --budget <n> [--precision <m>] [--rates "S1=0.2,S2=0.01"]
[--expected-recall <r>] [--allocation "S1=100,S2=200"] [--apply <rule>] [--json]` compares allocations of the labels
across the bands: for each it gives the relevant papers expected, how often the sample gives an interval, the margin
and how often the target gets a clear answer, with rows for lower and higher relevant rates beneath. `--apply` writes
the chosen row into `protocol.sampling.bands` as one History entry (undo restores the old bands). **UI:** the card
"Plan the validation sample" on the *Samples and labelling* page, with one "Use this allocation" button per row.

**What the plan's numbers mean.** The margin is the distance from the recall estimate down to its lower bound; the
chance of a clear answer is how often, in simulated samples at the stated relevant rates, the bounds land wholly
above or wholly below the target. WIDER sets no default margin: give `--precision` if you want one, or leave it out
and read the chance of a clear answer instead. The sensitivity rows beneath each allocation repeat it at lower and
higher relevant rates, because the rates are guesses before any label exists. `--allocation` adds your own row next
to the computed ones; `--apply yours` uses it, and the report records "used: yours; recommended: ..." together with
the plan's inputs. Deep bands with rare relevant papers are the costly part: the bounds there stay wide until many
records are labelled. Where a band shows no relevant paper, a supplementary, model-based interval (a Jeffreys prior
per band and screening decision) is shown, labelled as an assumption; it never decides the target.

**The criteria freeze, re-checks and a second reviewer.** Every label records the codebook version it was made
under and whether the criteria were frozen then; `wider sample status` and the sample card count labels before and
after the freeze. Labels made before the freeze are not re-checked automatically. `wider label criteria <sample>`
shows what changed between each early label's codebook version and the frozen one, the labels that change may
affect (includes and excludes alike) and a recommendation: keep the labels when nothing substantive changed,
re-check the affected ones, or re-check all early labels when the change is large or the affected ones cannot be
identified. `--assess yes|no --scope affected|all|none --note "..."` records your assessment, and only that makes
labels due. `wider label recheck <sample>` gives the due labels blind (earlier labels and screening decisions
hidden) and shuffled; `wider label second-reviewer <sample> --reviewer <id>` gives an independent reviewer a blind
queue, never a gate. Both queues hold validation records only, so a reviewer can tell they are validation records,
and they are blind to the queue only: the History page still lists earlier labels. A filled export goes back with
`wider label import <sample> <file.csv> --queue`. `wider label revisions <sample>` lists the original and revised
labels with both codebook versions; if you re-check only part of them, the outputs say how many were not
re-checked and do not count them as verified. **UI:** the criteria section on the sample card, and the record
drawer's re-check and second-reviewer modes.

### Locked samples, corrections and the seal

**What a lock freezes.** A lock freezes which records are in the sample, not their labels. Labelling a locked sample
to the end is normal: open it in the queue's *Sample* select or import a file with `wider label import`. A first
label, a re-check save and a second reviewer's label are not corrections and need no reason.

**A correction and its reason.** Changing a label or an adjudication that is already saved on a locked sample, or on
a sealed validation record, is a correction. The drawer asks for a "Reason for this correction" and saves only after you
give a reason and press *Save correction*; on the command line add `--correction-reason "<text>"` to
`wider label import ... --overwrite` or `wider label adjudicate`. Without it the command saves nothing and exits 1
("1 row changes a saved label on the locked sample dev; give --correction-reason"). A save that changes nothing
writes nothing. Each correction keeps the old and new values, the reason, who and when in `label_corrections`; History
lists it as *label correction* ("R1 corrected record 1 in sample dev", without the decisions), `wider history list
--kind label_correction` filters them, and `wider label revisions <sample>` shows the old and new decision and why.
Corrections can be undone in History like other changes.

**When recall is shown.** On a probability sample, recall waits until every sampled record has a final label:
each reviewer's labels agree or an adjudication settles them, at least one label is independent (below), and no
review package for it is still out. Until then the Recall page, `wider recall` (exit code 1), the report and the
methods draft say "Recall on <sample> is revealed once every sampled record has a final label." with what is left;
`wider sample status --json` gives the counts under `completeness`. Nothing is revealed early, so labelling more
records cannot be steered by a recall already seen.

**The seal.** The first time a recall result uses a record's final label, WIDER seals that label as the record's
reference. A later correction moves the reference (the correction is documented), but a package import never does.
When a sealed record's reference differs from the label it was first sealed with, every recall result adds one line:
"1 record of <sample> had its labels changed after its recall was first revealed on <date>; results revealed before
then used the earlier labels (wider label revisions <sample>)." The share bundle carries `reference_labels.csv` (each
sealed record's reference, its source, when it was sealed, whether it was corrected) and `label_corrections.csv`.
A label saved after the seal (a blind second review, say) that disagrees with the reference changes nothing and is
not counted as an open disagreement, but every recall result says how many such records there are, counts only:
"10 records of <sample> have another label that disagrees with their reference label; recall uses the reference
labels. List them with wider label agreement …; change a reference with wider label adjudicate … --correction-reason
WHY." The report's package line then says the package's labels were used "for agreement only" on sealed records.
On the development sample, an evaluation or comparison whose labels changed after it ran says so: "2 records of dev
had their labels changed after run 3 ran (<date>); this evaluation uses the current labels."

**Label independence.** A recall estimate needs labels made without seeing the model's output. WIDER records how each
label was made (`made_in`): labels from the blind queue and from review packages are independent; labels from the
queue with *Blind* unticked are not, and saving them again blind does not change that. Labels from a file import, a
sample import or from before WIDER recorded this count only once you state how they were made blind:
`wider label import <sample> <file.csv> --reviewer R4 --independent "labelled blind in Rayyan"`,
`wider sample import ... --independent "<basis>"`, or for labels already saved
`wider label provenance <sample> --reviewer R4 --independent "<basis>"`. The basis is kept; a later change of that
label outside the blind queue drops it. **Projects made before this change:** main-queue validation labels need this
declaration once before recall shows; the Recall page and `wider recall` print the exact command for each reviewer.

### Second reviewer

**One or two reviewers.** The choice is yours, and two are never required. The "Review packages" card on Samples
and labelling and `wider blinding status` say once: "One reviewer: the results rest on one person's labels, and their mistakes are not caught. Two independent reviewers: a second, blinded set of labels on the same records shows where the first may be wrong, and the disagreements are resolved before the results. A second reviewer is recommended for the validation includes and maybes, never required."

**Making a package.** A review package is one HTML page per reviewer that opens in any browser on any computer,
with nothing to install and no account: `wider blinding package R2 --sample validation --mode second --out
r2.html` (the first reviewer's includes and maybes plus as many of their excludes, validation samples only), or
`--mode independent` for every record of development or validation samples (one kind per package). The page holds,
per record, an opaque token, the title, the abstract the model judged (with a small mark that shows its source,
words and date), the authors, year, journal and DOI, and once the research questions, construct definitions and
criteria in the version the first reviewer used (`--codebook-version` when their labels used several). It holds no
decision, note, tag, model output, sample name or record id, so it cannot show another judgment. Before writing,
WIDER says that the file leaves your computer when you send it and what it holds; a copy stays in
`review-packages/` (`wider blinding package-file ID --out FILE` writes it again). Making a package cannot be undone,
because the file may already have been sent; `wider blinding withdraw ID --reason "..."` withdraws it instead.

**Earlier labels.** When the reviewer already has labels on the samples that were not made in a package (in WIDER
or in the blind queue), `--earlier` is needed: `retain` (they count as they are), `re-review` (the reviewer labels
those records again in the package, marked a repeat; this does not restore independence if they have seen other
judgments) or `exclude` (they are left out of reviewer agreement and the blinded-review counts and stay in final
labels and recall).

**Sending, labelling and importing.** You send the file however you choose; WIDER sends nothing. The reviewer
decides with the mouse or the keys 1, 2, 3 (j and k move), adds an exclusion reason, criteria ticks, tags and a
note, and presses "Export my decisions". The page keeps the work in the browser where it can and says once when it
cannot; "Continue from a decisions file" loads an export back. `wider blinding import wider-decisions-R2-ID.json`
(or "Import decisions file" on the card) stores the labels under the package's reviewer, marked as made in that
package with its criteria version and the abstract version of each record, as one change you can undo. A later
export updates the labels; an older one is refused. The limits, said once on the card, the status and the page: "A review package keeps other judgments out of the reviewer's view because it holds none. It does not prove who labelled: WIDER checks that a decisions file matches its package, not who made it."

**Resolving disagreements.** The reviewer-versus-reviewer table on Samples and labelling and `wider blinding
disagreements validation --a R1 --b R2` list the records where the two labels differ, with each side's reason,
ticks, tags, note, how the label was made and the abstract version it read (and a line when the two read different
abstracts); no model output. Resolve one in the drawer or with `wider label adjudicate`; both labels stay.

**Abstract versions.** WIDER keeps every abstract a record has had (imported, filled by a lookup, pasted by you, or
taken by a dedup re-run) and records which one each label and model call read: `wider enrich --versions RECORD_ID`
and the drawer's "Abstract versions" line.

The blind queue CSV (`wider label second-reviewer <sample> --reviewer <id> --export`, imported with `wider label
import --queue`) works as before.

## 9. Screening

For scripts: `wider screen`, `wider review`.

**What happens.** The approved codebook (and cells) become a screening prompt and a JSON schema, saved as a
`prompts` artifact version. Each record is screened in one isolated model call that must return a grade per
codebook element with a verbatim quote from the title or abstract; a decision rule turns the grades into
`include`, `maybe` or `exclude`. You first run single passes on the development sample and compare them with the
human labels; once the prompt is right, the corpus protocol runs two passes over every record, a blind third read
where they disagree, a 2-of-3 majority, and a second screen of the maybes. Every raw response is kept, and
decisions can be rebuilt offline.

**What you decide.** Which backend screens (any configured provider; compare them on the development sample), how
to revise the codebook when the evaluation shows misses in a pool or an adjacent literature, and when the prompt
is good enough to run on the corpus. Never tune on the validation sample: `wider screen evaluate` refuses it.
While the development sample has records without a final label, a run that includes them is refused with the sentence
from [Samples and labelling](#8-samples-and-labelling), and the page shows it with **Continue labelling**, which opens
the labelling queue; nothing is started or sent. The Overview lists the same item.

**Defaults and why.** The `screening` section encodes the review's reads: every grade needs a quote of at least
8 characters found in the text (`quote_min_chars`); 2 independent passes (`passes`), a blind third read on
disagreement (`blind_third_read`), 2 votes to decide (`majority`, a three-way split becomes `maybe`), and a second
screen of the maybes (`second_screen`). The **recall-first rule** (`recall_first`) makes an "absent" grade without a
verified quote into "unclear", so a record is excluded only when it is unrelated or verifiably lacks an element:
verbatim quotes and this rule keep a small model from excluding on a hunch, and repeated reads make the decision
reproducible rather than a single sample from a stochastic model. Project-specific labels (`labelling.labels`)
become extra schema fields, decision columns and report breakdowns.

These settings act on the runs: with `passes: 1` pass A alone decides (no pass B, no third read, `majority`
unused); with `second_screen` off, `wider screen second` and the UI's second screen are refused; with
`recall_first` off the model's grades and exclusions stand (the maybe flags still apply). Each run records the rule
settings it used in its run spec, and `wider screen replay` rebuilds decisions with those settings (runs made before
they were recorded use the current protocol).

**Check before moving on.** `wider screen evaluate <run_id>` on the development sample: agreement with the human
labels overall and per pool, and in particular no systematic misses in a near-miss pool. Then `wider screen
protocols` should show one decision per corpus record under your protocol name. `wider screen runs` lists run ids.
Two sentences may follow the figures. When two reviewers disagree on labelled records that nobody adjudicated, the
evaluation says how many and whose labels it uses there, for example "2 labelled records where the reviewers
disagree are not adjudicated; the evaluation uses ana's labels there"; adjudicate them for a firmer evaluation. When
the sample is not locked, it says "this sample is not locked: its labels can still change, and so can this
evaluation". Neither stops the evaluation. A comparison names unadjudicated labels the same way ("the comparison
uses ...").

**Commands.** `wider screen build-prompt [--codebook <version>] [--show]`,
`wider screen run <scope> [--pass <name>] [--provider <name>] [--prompt <version>] [--limit <n>] [--temperature <t>]`
with `scope` one of `sample:<name>`, `corpus`, `maybe:<protocol>`, `ids:1,2,3` (a limit below 1, or a pass name that
is not 1-20 letters A-Z, digits, `-` or `_`, is refused before any call; leave the limit out to screen every record);
`wider screen evaluate <run_id> [--sample development]`, `wider screen protocol [corpus] [--name main]
[--provider <name>] [--prompt <version>] [--limit <n>]` (a protocol name is 1-80 letters, digits, `-`, `_` or
`+`, e.g. `main+human`), `wider screen second [<protocol>]`,
`wider screen runs`, `wider screen protocols`, `wider screen replay <run_id>`,
`wider screen import-decisions <protocol> <file.csv> [--column <name>] [--others-exclude|--others-unscreened]
[--author <name>]`.
When several rows of the file match one record, the first row counts; the command says how many later rows matched
a record already listed and names, in red, each later row whose decision differs and the row that was kept (rows numbered as in a spreadsheet, header = row 1;
fully blank lines are not counted).
Every import is listed in *History* (kind `decisions_import`: protocol, new set, file name and the start of its
sha256, with your name (`--author`, else `WIDER_REVIEWER`, else your login) and the time). It cannot be undone, because a result set is never deleted; to stop using
imported decisions, make another set active. For the same reason a switch of the active set made before the import
can no longer be undone; the refusal names the import, and `wider screen sets use <protocol> <set>` switches again.
**An adjudicator's decisions for some records** (change `partial-adjudication-import`): export the records from
Results (*Export records*, CSV), give the file to the adjudicator, who fills an `adjudication` column with include,
exclude or maybe for the records they decide and leaves the others blank (an `adjudicator` column may name a person per
row), then `wider screen import-adjudication <protocol> <file.csv> --adjudicator <name> [--column <name>] [--set <n>]
[--dry-run] [--author <name>]`. Run it with `--dry-run` first: it prints the counts and every row it leaves out, with
the reason (no match, a similar title only, a record whose title differs, a record of an unfinished development sample,
an open or answered-but-unwritten failed read, a second row for the same record, in red when its decision differs, a
value that is not a decision), and writes nothing. The import makes a new result set from the active set (or `--set`;
it must be finished) and makes it active: each adjudicated record is decided by `researcher` with the reason
"researcher (<name>) decided as adjudicator" and its earlier decision kept in its origin; every other record keeps its
decision, votes and reason. The set it was built from is not changed; `wider screen sets use <protocol> <set>` makes it
active again. The adjudicator is added to the researchers, and shared files write the name as *Settings → Attribution*
says. A file made for another set is still imported into the chosen set, and the result says so. Adjudicated records of
a validation sample count as decided by a person who saw the LLM's reads. A second screen built on the earlier set is
named as out of step. A set made this way is never reused for model calls when the protocol is screened again.
The import is one *History* change (kind `adjudication_import`, "adjudicator decisions imported", with your name as
author, which may differ from the adjudicator's). **Undo** removes the new set and makes the earlier set active again;
**Redo** brings it back. Undo is refused once something was built on the new set: a review decision in it, a switch of
the active set, a later import, a recall look on it, a quality-control sample drawn from it, questions on it or a second
screen built on it; the refusal names it. Set numbers are not reused after an undo. Each import, undo or redo of a
2,000-record set adds about 0.5 MB to History.
**UI:** the *Results* page, card "Import adjudicator's decisions" below *Export records*: choose the file and type the
adjudicator; the counts and the rows left out show at once (nothing written), and **Import** writes into the set the
page shows, then the page shows the new active set.
The record limit caps the new model calls at that many per pass: only the first records of the scope are screened,
records already screened are not called again, and a protocol run whose passes are finished still decides every
record without new calls.
**UI:** the *Screening* page. Its "Model for this run" card grid marks the backend set for the screen stage on
Settings, else the project default; choosing another card screens the next run only and changes no setting (the
run sends it as `--provider` does). The line next to Run names the chosen backend, where the text goes (local, or
remote with its destination) and what it costs (no charge, its price per million tokens, or not priced); the record
limit says what it caps, as above. The page also builds the prompt; runs one pass, the A/B + third-read protocol or the second
screen; lists the runs and the protocol decision counts; shows the development evaluation, whose disagreements
between the model and the human labels open in the record drawer; and lists a run's calls by decision, each record
opening in the drawer with the model's quotes highlighted in the abstract.

**Where screening stands.** The card at the top of the Screening page lists the steps in words: the screening
prompt, trying the model on the development sample (optional), your labels on the validation sample (counts only),
the whole corpus screened by a protocol, and the maybes read again (optional). Each step reads completed, in
progress, needs attention or not started. A round made by the test model, a corpus where every record was excluded
or included, or one with more than half maybe needs attention: these are never a screening result. Below the steps
is **one next step** with its button; it points to Recall and report only when the required steps are completed,
and a quiet link "Go to Recall and report anyway" stays while they are not (nothing is locked). Under **Round shown**
you pick a round by its name, e.g. "Whole corpus · protocol main · pass A · prompt v1", and see its kind (development
sample, validation sample, whole corpus, maybes, chosen records, and "test model" when the test backend answered),
how many records were screened and failed, the include · maybe · exclude counts, the cost, and a red note when the
result looks unusual. The run id, folder, backend, tokens and times sit under *Technical details*.
The page has three tabs: **1 Set up and run** (model cards, start screening, and the *Screening history* with one
row per round and a Show button), **2 Decisions and result sets** (protocol decisions with Export CSV, records of one round,
result sets, models used) and **3 Check quality** (development evaluation, adjacent cases). `wider screen status
[--json]` prints the same steps, next step and latest round.

### Which backend screened what

Every screening call records its backend, the model asked for, the model the backend says it used, and a hash of
the backend's full settings (no key values); each distinct setting is kept once with the run. If you change a backend
while a run is unfinished (another model, endpoint or `extra` setting under the same name, or another backend for the
same pass), WIDER asks before the next call: the dialog "The backend changed since this run started" lists from, to,
the changed fields and how many records are done, with **Continue this run on the new backend**, **Start a separate
run** (when the backend name differs; it screens every record again) and Cancel. On the command line `wider screen
run|protocol|second` prints the change and exits 1 until you add `--backend-change continue` or `new`. A change of
prices, timeout, concurrency, key variable or CLI version only is recorded without asking. A run whose calls used more
than one model is marked **mixed models**, and a protocol whose decisions rest on more than one model is marked
mixed, on the runs table, the protocol line and the result card.

Each final decision records which evaluations made it (`decided_by`: the agreeing passes such as `A,B`, `split`,
`researcher`, `import`, or `carried from <protocol>` for the second screen). **Export CSV** on the protocol
decisions card (`wider screen export PROTOCOL [--set N]`) writes `exports/screening/<protocol>_decisions.csv` and
`_evaluations.csv`: one row per evaluation with run, call, backend, model, reported model, settings hash, CLI
version, prompt version, tokens, cost and whether it was used for the final; no abstract, prompt or answer text. The
**Models used** card (`wider screen models [--protocol P | --run RUN_ID]`) shows per model the records screened, the
evaluations, the calls (failed requests and retries included), the include / maybe / exclude counts, tokens, cost
and the records whose final used it, with the line "N unique records, M model calls".

### Result sets

A rerun never replaces a finished protocol. A protocol holds one or more **result sets**, each a complete set of
decisions for one scope (a sample or the corpus) with its role: development, validation, corpus or other. A
development set is labelled "development set: not an independent validation" wherever it is reported.

- Rerunning a finished set with the same method settings re-derives the same decisions and makes no call. With other
  *method* settings (model, endpoint, temperature, `extra` settings, CLI command, prompt or codebook version,
  screening rules) nothing runs: WIDER names what differs and the cost range and asks for a new set. *Operational*
  settings (prices, timeout, concurrency, key variable, CLI version or path) never count as a change.
- A new set is made only on request: `--new-set [--set-label TEXT]`, or **Screen again as a new result set** on the
  Result sets card or in the dialog. When its settings equal an earlier finished set, WIDER names that set first.
  `--new-set --reuse-from N` copies the decisions of set N (an import, or a set with the same method) and screens
  only the rest; `--no-reuse` screens everything. `--reuse-evaluations` reuses earlier evaluations whose every input
  was identical (model, temperature, prompt, codebook, record text, endpoint, `extra`), marked as reused and costed
  once, under their source run.
- **Use for analysis** (`wider screen sets use P N`) chooses the one active set that recall, reports and exports read;
  a new set never becomes active by itself, switching starts nothing and can be undone. `wider screen sets list`,
  `label`, and `compare P A B` (never changes the active set); recall, reports and exports take `--set N` to read
  another set, and record the set keys they read.
- An import (`wider screen import-decisions`) becomes its own set and is never continued by a model.
- The second screen has its own sets, each tied to the first-screen set whose maybes it screened (`wider screen
  second P --from-set N`, the "Maybes from" select). Using a second-screen set whose source is not the active
  first-screen set is refused until you switch both (`--with-source`, or **Use both**).
- Your answer to a three-way split belongs to its set. In a new set with the same codebook and rules, the question
  shows your earlier answer and offers to reuse it (`wider questions answer ID reuse`); nothing is taken over silently.
- Costs are history: usage and cost totals count each call once, under the run that made it.
- Exports, the report and the share bundle name the set (number, label, role, models); the bundle keeps each run's
  backend settings and backend changes.

### Comparing configurations on the development sample

A **comparison** screens the records of the locked development sample once per *configuration* and *repeat*, and
scores every run against the sample's final labels, the same scoring as `wider screen evaluate`. A configuration is
a backend, optionally a prompt version and a temperature: `a`, `a:p2` (prompt version 2), `a@0.7` (temperature
0.7) or `a:p2@0.7`. This is different from `wider screen sets compare`, which compares two result sets of one
protocol record by record.

```
wider sample lock development
wider screen compare run cmp1 --config fake --config fake_b --repeats 3 --estimate-only
wider screen compare run cmp1 --config fake --config fake_b --repeats 3
wider screen compare show cmp1 --by pool
wider screen compare list
```

- **What the figures mean.** Per configuration, in the order you gave: misses (included by the labels, excluded by
  the model), false includes, false maybes and agreement, each as the lowest and highest value over the repeats;
  how many records were screened and how many had a final label; failed calls; tokens and cost. `--by pool` and
  `--by area` split the figures by sampling pool and by research area (the clusters of the sample's ranking; "not
  clustered" last). These figures describe the purposive development sample, not the corpus: the pools are chosen
  to contain hard cases, so the counts say where a configuration fails, not how well it would do on every record.
- **No winner.** No configuration is marked best or recommended. Which miss matters more than which false include is
  your decision, and a difference of one record on a small sample may be chance.
- **Repeats and consistency.** Each repeat sends the backend's seed plus the repeat number minus 1, so repeats are
  separate draws. Consistency is the share of records that got the same decision in every repeat, and the records
  that changed are listed with their decisions ("include, exclude"). With one repeat consistency is not measured.
  At temperature 0 the repeats test only whether the backend gives the same answer twice. A backend that sends no
  temperature (a CLI agent, or one whose `extra` settings fix it) refuses `@T`, and its row says "temperature not
  sent by this backend".
- **Cost.** The estimate (`--estimate-only`, or the card's estimate step) gives records, runs, new calls and each
  backend's cost range and where its text goes, before anything runs; cloud backends ask for consent as screening
  does. `--reuse-evaluations` reuses evaluations whose every input was identical (an earlier comparison's, or a
  protocol's), marked as reused and costed once. The other way round, a protocol run or `wider screen run` with
  `--reuse-evaluations` reuses a comparison's evaluations for the same records.
- **Failed calls** are counted on the row and not asked about; running the same comparison again (same name, same
  configurations and repeats) finishes what is missing, calling the model only for those records. A comparison whose
  backend changed or was removed since it first ran is not resumed: start a new comparison.
- **A comparison needs a locked sample.** While a reviewer still labels or adjudicates, no comparison may show a
  model's decision for a record, so `compare run` refuses an unlocked sample ("sample development is not locked;
  lock it before comparing: wider sample lock development"). The estimate, `show` and `list` are never refused.
  Unlocking the sample after a comparison is not refused either: the comparison's per-record lists (changed records,
  misses, `comparison.csv`) stay visible to whoever relabels, and `show` then scores the current labels and says once
  that the sample is unlocked.
- **When the sample changes.** Records added to the sample later are counted ("4 records were added to development
  after this comparison"), not scored and not named; records that left the sample in a redraw are no longer scored,
  and `show` says how many.
- **Files.** Each run keeps its folder (run spec, prompt, schema, raw responses; no `decisions.csv`), and
  `runs/compare_<name>/comparison.csv` lists per record its title, pool, area, final label and each configuration's
  decision per repeat; it stays on your computer. Comparison runs never enter a protocol, a result set, the screening
  exports or the recall estimate; their calls are in `wider costs --by run`, and the Screening stage records one
  result per comparison. The report's section *Configuration comparisons* lists each comparison's settings and
  figures ("not finished" for one that was stopped, "No configuration comparison was run." when there is none), and
  the methods draft and the replication package carry them as counts, never per record (see
  [Outputs for the paper](#outputs-for-the-paper)).
- **Using what you found.** A comparison changes no setting. To screen with a temperature found in a comparison,
  add a backend entry with that temperature (`wider providers add`, or Settings) and choose it for the screen
  stage or the run.

**UI:** the *Compare configurations* card on the Screening page: sample, configuration rows, repeats, the reuse tick
box, the estimate as a confirm step, then the results table, consistency and the pool/area toggle.

### A backend per pass

Pass A, pass B and the blind third read (the *judge*) can each run on their own backend. Independent readings from two
different models catch more of each other's misses than two readings from one model.

- **Which backend a pass uses.** Pass A runs on `--provider`, else the backend set for the `screen` stage, else the
  project default. Pass B runs on `--provider-b`, else the `screen_b` stage backend, else pass A's. The judge runs on
  `--provider-c`, else the `screen_judge` stage backend, else pass A's. A stage backend that was removed falls back to
  pass A. Each pass runs at its own backend's temperature unless `--temperature` sets one for every pass.
- **The second screen.** `wider screen second` reads each maybe with S1 on pass A's backend, S2 on pass B's and S3
  on the judge's, with the same flags and stage keys.
- **Setting the defaults.** `wider providers stage screen_b NAME` and `wider providers stage screen_judge NAME` (no
  name removes the choice). On Settings the screen row has three selects, "Pass A", "Pass B" and "Judge"; the last two
  start with "Same as pass A". `wider providers list` shows such a backend as "screen (pass B)" or "screen (judge)",
  and the backend check names the same use.
- **For one run.** In protocol and second-screen mode the Screening page shows "Backends of the other passes" below
  the model cards: a Pass B and a Judge select, preselected from the stage keys. They screen the next run only and
  change no setting, as `--provider-b` and `--provider-c` do. A pass that does not run (`screening.passes: 1`, or
  `blind_third_read: false`) has its select disabled with the reason beside it; on the command line its flag is
  noted ("the judge does not run in this protocol (screening.blind_third_read: false); --provider-c is not used"),
  not refused.
- **Shared models.** When two passes use the same model and temperature, the page and the command print, once:
  "Passes A and B use the same model (MODEL, temperature 0): their readings are not independent." Nothing is
  refused; the methods draft repeats the sentence.
- **Cost and consent.** The estimate sums the calls per pass backend and names each one ("pass A on a: local, no
  charge", "pass B on r: ..."). Every backend off this computer asks for consent before the first call of any pass.
- **Result sets.** A set records the backend of each pass that differs from pass A. Changing pass B's backend under
  a finished set is a method change: WIDER names `backends.B.model` and asks for a new set (`--new-set`).
- **Mixed models.** A run or protocol is marked *mixed models* only when a pass's calls used a model other than its
  own pass backend's. A deliberate pass-B backend is not mixed. A set with no recorded settings keeps the older rule
  (more than one model). The Models used card and `wider screen models` list the passes each model read.

**Commands.** `wider screen protocol [corpus] [--provider A] [--provider-b B] [--provider-c C]`,
`wider screen second [<protocol>] [--provider S1] [--provider-b S2] [--provider-c S3]`,
`wider providers stage screen_b|screen_judge [NAME]`.

### Screening review

For scripts: `wider review list [PROTOCOL] [--set N] [--show flagged|majority|qc|all]`, `wider review show RECORD`,
`wider review decide RECORD include|exclude|maybe --by NAME [--reason TEXT]`, `wider review criteria`,
`wider review qc draw --n N [--seed S] --by NAME`.

The Screening review page (sidebar, after Screening) lists the records of one protocol and result set that need a
person, and shows the LLM's reads of each. If you still label a validation sample, label it first: the reads can
sway a label you give afterwards.

- **Which records are listed, and why.** A record is listed when its three reads disagree (an open split question),
  a pass failed to read it, its final decision is maybe, or it is in a quality-control sample. Each row says why in
  one sentence. A failed read is answered on the Questions page; once answered, the row waits for the next screening
  run of the set and still counts as undecided. *Show* adds the records the third read settled, the quality-control
  records only, or every record of the set.
- **The record view.** Title, abstract, each pass's read with its reason and model, the third read, the LLM outcome
  and the final decision with who reached it. A row with no pass reads (imported, or from an older project) says so
  and shows its earlier decision instead of an LLM outcome. The view shows no sample, band, rank or score.
- **Deciding.** Include, exclude or maybe, signed with your name. An open split is decided through its question, so
  the Questions page and the review page agree. The passes' reads are kept beside your decision; the decision is
  journalled and undone from History (`wider history undo ID`), which puts back the earlier row. A screening run of
  the set keeps a decision made here, even one made while the run was going.
- **The criteria panel.** The codebook the set was screened with: the version behind its prompt and every section
  the prompt holds. When the version was not recorded, the panel says so and shows the current codebook.
- **Quality-control samples.** A seeded random draw of records the LLM settled (not decided by a person, not in a
  labelling sample, not already listed), to check them by hand. It is not a recall estimate. The draw stores its
  seed and its pool, is signed and listed in History, and can be undone until one of its records has a review
  decision.
- **Where it shows.** The report lists each review decision and each draw under "Researcher interventions"; the
  methods draft says "A researcher decided N records after screening; M of them changed the LLM outcome." and one
  sentence per quality-control draw. The Screening page, the Home project card, `wider screen status` and `wider info`
  say "N records wait for a person" with a link to this page. A sample row (Samples and labelling, `wider sample
  status`) counts its records decided by a person who saw the LLM's reads, so a validation label given after that can
  be weighed; nothing is refused.

## 10. Recall and report

For scripts: `wider recall`.

**What happens.** The protocol's decisions are compared with the human labels of the probability sample, band by
band, and combined with the band weights into a recall estimate with an interval. The estimate is shown only once
every sampled record has a final label (see *Locked samples, corrections and the seal* in section 8). The report gathers every number of the review (PRISMA counts, coverage, enrichment, ranking
validation, screening counts, recall) from the store into `exports/report.md` and `report.json`, and a share
bundle packages what a reader needs to audit the review without the copyrighted text.

**What you decide.** Which protocol and sample to report, which reviewer is primary when several labelled, and
which recall variant to headline. `strict` counts an include as kept only if the LLM said include; `lenient`
counts include or maybe as kept; `cautious` also treats human maybes as relevant.

**Defaults and why.** `recall.primary_variant` is `lenient`, because maybes go to full-text reading anyway; the
report's headline and the methods paragraph use that variant. Intervals are at the 0.95 level (`ci_level`); every
interval and its label follow `ci_level` when you change it. The claim a review makes is "we found nearly all of it";
this stage puts a number and an interval on it rather than a hope.

**How sure the recall is.** Every recall result (the Recall page, `wider recall`, the report and the methods draft)
says the same things in the same words; the stage result's headline gives the recall, the interval and the count:

- *How many relevant validation papers it rests on.* "Based on 7 relevant validation papers." Below 20 a second
  sentence says that this is few: one paper more or fewer missed would change the estimate a lot, and labelling more
  of the sample gives a firmer estimate.
- *The interval.* "95% interval: 0.16 to 1.00." It is an approximate interval: Clopper–Pearson bounds on an
  *effective* number of relevant papers, which takes the band weights into account: a paper from a band sampled at
  1 in 50 counts for more than a paper from a fully read band, so unequal weights leave fewer effective papers and a
  broader interval. With equal weights it is the plain Clopper–Pearson interval (10 of 10 found: 0.69 to 1.00).
  Between 0 and 1 the effective number comes from the stratified variance (Korn and Graubard); at 0 or 1, where that
  variance is zero, from the weights alone (Kish's effective sample size), which is also the most it can ever be, so
  one more missed paper never narrows the interval. The interval is never the single point 1.00 to 1.00. It is not an
  exact or validated confidence interval for every sampling design: in a simulation study of WIDER's sampling (24 Oct
  2026, `scripts/recall_interval_sim.py`; rare relevant papers, weights up to 1,000, bands of five labelled records,
  true recall 0, 0.06, 0.94 and 1) it held the true recall in 95% to 100% of the samples in which it is shown, so it
  errs on the wide side. One sentence under the table says this on the Recall page, in `wider recall` and in the
  report, in the same words.
- *When no interval can be defended,* "Uncertainty could not be reliably estimated" and why; the recall itself is
  still shown. This happens when a sampled band shows no relevant paper among its labelled records (what such a band
  hides cannot be estimated, and the simulation showed that an interval given then holds the true recall in only 19%
  of cases; the per-band table's rule-of-three note bounds the band's relevant rate, and labelling more of that band
  is the remedy), when a sampled band holds a relevant paper but only one labelled record, or when there are too few
  relevant papers across the bands. With no relevant paper at all, recall cannot be estimated.
- *Reviewer disagreements.* When two reviewers disagree on a labelled record and nobody has adjudicated it, the
  estimate says how many such records there are and whose labels it uses there ("3 labelled records where the
  reviewers disagree are not adjudicated; the estimate uses alice's labels there. Adjudicating them gives a firmer
  estimate.").
- *Perfect observed recall is not perfect screening.* When no relevant validation paper was missed, WIDER says so and
  adds that this does not mean the screening keeps every relevant paper, with the lowest recall the interval still
  allows ("a recall as low as 0.16 fits what was seen"). Report that lower bound, not "100%".

The method details are in the report's **Recall: method** subsection: the method per variant with its effective
number and degrees of freedom, and two cross-checks, a seeded stratified bootstrap interval (10,000 replicates,
`bootstrap_b`, at most 50,000: a larger value is capped and the stage result says so; census bands fixed) and a
linearised (Taylor) interval. Both cross-checks shrink to a point at recall 0 or 1, which is why neither is the
primary interval.

**Independent check.** Papers found without the query (source `researcher`, `colleague`, `other_database`,
`prior_review` or `seed_chain:<id>`; never `query_results`) give a second look at the search. While the validation
set is shown, "Recall on independent validation papers: 8 of 10 (researcher 4 of 4, seed_chain 4 of 5, colleague 0
of 1), 95% interval 0.44 to 0.97, for these 10 papers only; they are not a random sample of all relevant papers."
Below 10 papers a note says that this is few; when all are found, "All found here does not mean none are missing."
After screening, the included records of the protocol and the independent validation papers act as two captures,
and Chapman's estimate gives the number of relevant papers and how many may be missing from both ("about 49 (SE
6.0); about 7 relevant papers may be missing from both"); with no paper in both it is "not defined (no overlap)".
Independent Development papers are counted apart ("not a validation figure (seen during development)") and never
enter the estimate. Every figure comes with one caveat: the two captures are not independent (well-cited papers are
found by both chaining and the databases), so recall tends to be overestimated and the total underestimated; the
figures are a check, not proof. While the validation set is hidden the check says "Validation set hidden:
independent check not shown." and gives only the Development count; reading it is not a look. The same lines are in
`wider coverage --independent [--protocol P]` (default: the protocol screened most recently), the *Independent
check* card on the Recall and report page (for the protocol chosen there), the report's *Independent check* section,
and `independent_estimate.json` in the package (counts, benchmark ids, the formulas and the caveat; no titles).

**Screening agreement and researcher interventions in the report.** The report (`report.md` and `report.json`) has
two further sections. *Screening agreement* (after the screening table) gives, for the report's result set, a 3 by 3
table of what pass A and pass B said on the records both read (a second screen: reads S1 and S2), the share of
records on which they agreed with Cohen's kappa ("kappa not defined" with the reason when one pass gave only one
decision), and how each decision was reached: both passes agreed, settled by the third read, no majority (left at
maybe), decided by the researcher, imported, carried from an earlier set, or not recorded (decisions written before
WIDER kept the votes). A set screened with one pass says that there is no agreement to report. *Researcher
interventions* (at the end) is the owner's view of what people changed: one line per kind of History entry with the
count, the authors and the first and last date, then a table of the other entries (configuration edits, answers to
questions, unlocks such as "uploads reopened", screening runs), each with its date, author and summary, and
"(undone)" when it is undone now; a redone entry is not marked. Labels, adjudications, notes, tags, deduplication
marks and other per-record entries are counted, never listed, so the section names no record; a path in a summary
is written from `~`. With no such entry the section says "No researcher intervention
recorded." Both sections are rebuilt from the store each time the report is built.

**Check before moving on.** Every record in the probability sample has a protocol decision (the recall table warns
about unscreened ones), the missed human includes are listed and understood, and the sample is locked. Then write
the report and the bundle.

**Commands.** `wider recall <protocol> [--sample validation] [--primary <reviewer>] [--bootstrap <n>]`,
`wider report [--protocol <name>] [--sample <name>] [--bundle]`, `wider dedup prisma`. Without `--protocol` the
report uses the protocol screened most recently; without `--sample`, the probability sample labelled last, else the
first probability sample. An unknown protocol or sample name is an error (exit code 1), not an empty report.
**UI:** the *Recall and report* page (estimate recall for a protocol and sample; build the report; write the share
bundle, whose contents are listed under [Privacy and reproducibility](#privacy-and-reproducibility); and the four
outputs below). Opening the page only reads the last report; **Build report** writes it once and records one stage
result.

### The recall target and the validation outcome

For scripts: `wider validation`.

**Set the target first.** `wider validation target <recall> --rule maybes-as-includes|top-strata|revise-prompt
[--depth <rank>] [--precision <m>]` records the target and what you will do if it is not met, timed in History.
Every recall output says when it was set; a target or rule set after a recall result was seen is said as such
("... after a recall result was seen (date)"), and one set outside WIDER is marked as of unknown time. Without a
target the outputs read "No recall target was set before the results; set one with wider validation target."

**Three outcomes.** Each recall estimate on the probability sample is a look. The verdict comes from exact bounds
for sampling without replacement, valid at every look (a confidence sequence; Waudby-Smith and Ramdas 2020), so you
may look, add labels and look again without spoiling the stated level. The outcome is "Target met" (the bounds lie
at or above the target), "Target not met" (below it) or "Inconclusive" (they contain it). Inconclusive is not a
miss: the sentence says so ("This is not evidence that the target was missed; the labels so far cannot tell met from
not met"), and only "not met" is drawn as a warning, on the Recall page and on the Results dashboard. The stored key
stays `cannot_tell`, so `--json` output and older looks read as before. With an inconclusive result WIDER suggests
how many more records to label in which bands, with a range, and says that more labels do not guarantee a clear
answer. The bounds are conservative: the error is split over every band and screening decision and each is bounded
on its own, so they may need substantially more labels than a sharper method to show the same result; the report and
the methods draft say this once, and the band that widens the bounds most is named. The first draw is systematic and
is treated as random within each band. `wider validation status` shows the target and the last look without
recording one.

**How many more labels, and when that is impractical.** The suggestion is not capped at 300 records: it follows the
totals past 300 (in steps of 50 to 1,000, of 250 to 5,000, then of 1,000) up to every record left in the sampled
bands, and when only a census would settle the question it says "Only labelling all N records left in the sampled
bands is expected to settle whether the target T is met." Past 1,000 labels the chances are judged on fewer simulated
samples, and the suggestion says they are rougher. The protocol setting `recall.practical_labels` (default 500;
`wider protocol set recall.practical_labels 800`, or the Settings protocol table) is how many more labels you
consider practical. When the expected number is above it, WIDER says "Settling whether the target T is met may be
impractical: about M more labels (likely between a and b) are expected before the bounds say met or not met, more
than the L you set as practical (recall.practical_labels). You may stop here and report the result as inconclusive;
nothing requires more labels.", and when the number is half or more of the records left, "That is X% of the N records
left." The warning is information in black, not a refusal: it blocks no button, command, report or export, and it is
worked out again from the current setting each time, so changing the setting changes it at once.

**Stopping with an inconclusive result.** `wider validation stop <sample>` is allowed with any outcome. History,
the report and the methods draft then say "Validation ended at look L with an inconclusive result: recall lies between
lo and hi (exact bounds valid at every look, 95%); the target T was neither shown met nor shown missed." Before a
stop the same line reads "At look L the result is inconclusive: ..."; neither form refuses a report, an export or a
package.

**Extending after a look.** `wider validation extend <sample> --suggested` (or `--band S3=40`) adds random frame
records in no other sample to the chosen bands; the weights become N_h/n_h, labels stay, and undo removes the added
records. An extension made after a look is said in every output. `wider validation stop <sample> [--note "..."]`
records the decision to stop at the last look; it stays on record (its undo is refused), and adding records later is
allowed and said. **UI:** the outcome block on the *Recall and report* page ("Add the suggested records", "Stop
validation here", "Add records to bands" on the sample card).

**Used versus not informed.** A result set or configuration made after recall on a sample was seen must be marked:
`wider recall <protocol> --sample-used` (the sample informed the change, so its estimate is not independent: draw a
fresh sample) or `--not-informed` (you state the recall result did not inform the change; the statement and your
name go into the methods draft). Until you choose, the estimate waits with "choose one" and nothing is done.
Once recall on one sample has been seen for two or more result sets with different settings, every estimate on
that sample, the report and the methods draft add "Recall on validation was seen for 2 result sets with different
settings (sets 2, 3); choosing between them on this sample uses it for model selection." An imported set has no
recorded settings, so each imported set counts as a setting of its own here, as it does for the choice above, and
the line then says "different or unknown settings". A choice alone is not a look; the set counts once it is estimated.

**The fresh sample.** `wider sample draw probability --name validation2 --fresh-from validation` draws a new
probability sample that leaves the used one and the development sample out of every draw; it starts at look 1. The
used sample's records are reported apart ("not in this estimate ... Not independent; not pooled."), and every
output ends with the scope line: how many ranked records the estimate covers, what it leaves out, and that it is on
title and abstract only.

**Seed papers apart.** Known seed papers are not a random sample, so they are never pooled into the
estimate. The report's "Seed papers apart" section gives, per set, how many were kept by the screening with an
interval; the Validation seed papers stay hidden until the query is frozen. **In the report.** `report.json`
`validation_checks` and "Recall: method" list the plan used (or "No validation plan was used."), the target with
when it was set, the method of the bounds, extensions, stops, looks and choices, the label counts by criteria state,
re-checks and second-reviewer coverage; the methods draft names the target, the outcome, any not-independent or
not-informed statement, the caveats of the bounds and the scope line.

### Results dashboard

For scripts: `wider results`.

**One view of a result set.** `wider results [PROTOCOL] [--set N] [--sample NAME] [--json]` prints, for the
protocol (the latest one with screening decisions unless named), the set (the active one unless named) and the
probability sample (`protocol.sampling.probability_sample`, `validation` by default), a heading with the set and the model of each pass ("main (set 1)", "Pass A: a; pass B: b; third read: c"),
then seven sections: the agreement between the passes (pass A by pass B table, agreement with Cohen's kappa, how each
decision was reached), the configuration comparisons on the development sample (kept apart: they are not a recall
estimate), recall, the relevant found along the ranking, the PRISMA counts, the reference files per decision set
(records per export preset and per pathway, the unresolved ones counted apart, with the *Export records* card) and the documents in `exports/` with when each was written. `--json` gives
the same content. Without any screening decision it says "no screening decisions yet" (exit code 1).

**It records nothing.** The recall section shows the lines and outcome of the last recall estimate on the sample, as
of their time, as `wider validation status` gives them; opening the dashboard computes no estimate and records no
look, so it does not spend one of your looks at the validation sample. When labels or screening decisions changed
since that estimate, a sentence says so above the old lines; with no estimate yet, one sentence says what is missing
and which command makes it.

**The chart "Relevant found along the ranking".** It draws, at each band end of the sample, three lines from the
series stored with that estimate: the estimated relevant records (blue, solid), those kept by screening in the
protocol's primary variant (green, dashed) and those missed (violet, dotted), with the band boundaries and the label
"Estimate from the validation sample; no stopping rule." Hovering a band's mark, or moving to it with Tab, shows its
numbers: ranks, records, sampled, labelled and relevant observed, then the estimated relevant, kept and missed in the
band and up to its end; Escape hides them. A table under the chart gives the same rows under the headings
"observed" and "estimated", and `wider results` prints that table. A band with fewer than 3 relevant observed is
marked "few relevant observed": its estimate rests on one or two papers.

- *The ranges.* Where the estimate has exact bounds, a shaded bar beside each point shows its range, "Exact range
  (valid at every look)": the kept and the not-kept records of each band are bounded apart, at a level that holds for
  all of them together at every look, and those bounds are added up. So the range is broader than the interval of the
  recall estimate and need not be centred on the point.
- *A point outside its range.* The point and the range are worked out in two different ways, which can disagree when
  few relevant records were found (in a simulation, up to about 3 in 100 runs at very low prevalence). WIDER draws
  both as computed and says so in that band's row and tooltip, never moving one to fit the other.
- *Records drawn into another sample later.* When another sample (the development sample, say) was drawn after this
  one and took records of a band, the estimate, the range and the verdict all still count those records as records of
  their band that this sample did not label, as the sample's design did at the draw (or its last extension); the
  band's row and tooltip say how many. Records left out at the draw stay out ("10 records of the development sample
  are not covered."). A result stored before WIDER counted them this way says so and asks you to estimate recall
  again; the same labels add no look.
- *A band not labelled yet.* When a band has sampled records that are not labelled, the lines end at the band end
  before it and one sentence names the band, how many are unlabelled and that nothing is estimated from there on:
  within and after that band nothing is known yet, and drawing the unlabelled records as not relevant would show a
  too-low curve. The sentence links to *Samples and labelling*; the CLI names `wider label export` and `wider label
  import`. A band labelled in full with no relevant record keeps its point, with its range above it.
- *Stale.* When labels or decisions changed since the estimate, the chart is not drawn; the sentence says so, with
  the button **Estimate recall again**. It makes the same estimate as **Estimate recall** on the Recall page (the twin
  of `wider recall PROTOCOL [--set N] --sample S`) and so records one look; opening or refreshing the dashboard never
  does. A series stored by an older WIDER draws its lines without ranges or band details and says so.

**An owner view.** The dashboard shows the screening decisions and the labels' totals, so it is a page for the
review's owner, not for a blind second reviewer, who labels in the blind queue on Samples and labelling.
Choosing another set on the dashboard only shows it; it never makes it the active set. **UI:** *Results dashboard*,
the last link under **4 Screening and outputs**, with protocol, set and sample selects (the twins of `PROTOCOL`,
`--set` and `--sample`) and the *Export records* card (below, under *Outputs for the paper*).

### PRISMA reporting

For scripts: `wider report prisma`.

**Two checklists, one page.** The page lists the items of PRISMA 2020 and of PRISMA-S under their official section
headings, for one protocol and result set (the latest protocol with screening decisions and its active set unless you
choose others; choosing only shows a set, it never makes it the active one). PRISMA 2020 asks how the whole review was
done and reported; PRISMA-S asks, in more detail, how the search was done and reported. Several items of the two
checklists ask about the same thing (PRISMA 2020 item 7, the full search strategies, and PRISMA-S items 8 to 12 and 14,
for example): an item shows these **related items** with their status and the first line of your text there; a link opens
the related item on its own tab, and **Start from this text** copies its text into your text box, saved only when you
press Save. `wider report prisma status [--checklist prisma-2020|prisma-s] [--to-check] [--json]` prints the rows,
`wider report prisma show NO [--checklist ...]` one item.

**Statuses.** Each item has one of four statuses: *Automatically documented* (WIDER recorded every element the item
asks for), *Partially documented* (some elements are recorded, the rest is listed under "Still missing"),
*Researcher input needed* (WIDER records nothing for it: eligibility criteria, risk of bias, synthesis, funding and
other parts of a review that happen outside WIDER) and *Not applicable* (you marked it so, with a reason). The status
comes from WIDER's records only; your own text never raises it, and where your status and WIDER's differ the row
shows both ("Not applicable (WIDER: Partially documented)").

**What an item shows.** What PRISMA asks (the official wording and its source, then the elements), what WIDER
recorded (each line with where it comes from), what is still missing (with what to say about it; an element you
ticked as covered by your text says so), the related items, your text, and WIDER's draft when it can write one.

**Your text, kept apart.** Your text, the elements it covers and where the item is reported in your paper ("p. 6")
are saved apart from WIDER's records: they never change a record, a count or a status. Gaps WIDER cannot fill are
written in square brackets in a draft, and the item counts them until you replace them. **Use the draft** saves
WIDER's draft as your text; **Clear my text** removes it (earlier versions stay in History). An item that does not
apply is marked **Not applicable** with a reason; **Remove the not-applicable mark** takes it back. Every save, clear
and mark is listed in History (kind "PRISMA reporting text") and can be undone. The CLI twins are
`wider report prisma edit NO --text ... | --file F | --from-draft | --from-item prisma-s:8 [--covers a,b] [--where "p. 6"]`,
`edit NO --keep`, `na NO --reason ...`, `na NO --clear` and `clear NO`.

**Outdated text and "To check".** When WIDER's evidence for an item changes after you saved your text (a new search
round, more screening), your text is marked **outdated** in red, with what it was saved with and what WIDER records
now; a line that contradicts the records (a count that no longer matches, say) is named in red too. **Keep my text**
(`edit NO --keep`) keeps it for the current evidence. The **To check** filter shows the items to look at: outdated
text, text that could not be read, and text with gaps still in brackets. The status filters show one status; each
button shows its count for the tab.

**The flow diagram.** The *Flow diagram* tab shows the PRISMA 2020 flow as the export draws it. WIDER fills the
boxes it records (records identified per database, duplicates removed, records screened and excluded); the boxes it
cannot know (records from registers, records removed by automation tools, reports not retrieved, studies and reports
included) and the reports excluded with their reasons are numbers you enter, marked † in the diagram. An empty field
removes your number. Red lines name counts that do not add up, said once; when WIDER's counts change after you saved
yours, the changed boxes are listed. `wider report prisma flow [--count NAME=N ...] [--excluded "REASON=N" ...]
[--clear] [--json]` is the twin.

**Exports.** **Export checklists** (top bar; `wider report prisma export`) writes `exports/prisma_reporting/`: both
checklists as Markdown, CSV and JSON, the methods text (`methods_text.md`), and, when Word export is available,
`prisma_2020_checklist.docx` and `prisma_s_checklist.docx` in the official checklist layout, the methods
documentation `methods_documentation.docx` and an editable flow diagram `prisma_2020_flow.docx`, besides
`prisma_2020_flow.svg`; it also redraws `exports/prisma_2020.svg`. In the Word checklists, fill the column "Location
where item is reported" with your paper's pages, and the items marked *Researcher input needed*. If you edited a Word
file WIDER wrote, the next export keeps your copy under a dated name and says so. The page lists every file written,
the Word files with a download link. **UI:** *PRISMA reporting*, the link after *Results dashboard* under
**4 Screening and outputs**; like the dashboard it is an owner view, since it shows the screening results, and a
blind second reviewer labels in the queue on Samples and labelling only.

### Outputs for the paper

For scripts: `wider export`.

- **PRISMA 2020 diagram.** `wider export prisma` writes `exports/prisma_2020.svg` (black on white, ready for a
  manuscript) and `prisma_2020.json` with the counts. Records identified per database and via other methods
  (citation chaining) come from the imported exports, duplicates removed from deduplication, and records screened,
  excluded and sought for full text from the chosen screening protocol. The full-text boxes say "not yet recorded",
  because full-text screening is a later stage; WIDER never fills them with guesses.
- **PRISMA-S search report.** `wider export prisma-s` writes `exports/prisma_s.md` and `prisma_s.json` with the 16
  items of PRISMA-S (Rethlefsen et al., *Systematic Reviews* 2021;10:39), each filled from what the project recorded:
  the databases and their exports, citation searching (the chaining runs from the seed papers), other methods
  (seed papers counted by set and source, never named), the full strings with their translation ledgers, the limits
  applied, prior reviews, the dates, the second reader, the total records and deduplication. The dates are import
  dates and say "import date; search date not recorded". The peer-review item names each second-reader run and says
  "a model second reader, not a human peer review"; it gives counts, never the model's text. An item WIDER does not
  record (multi-database searching, study registries, online resources, contacts, search filters, updates) says "not
  recorded". **UI:** the PRISMA reporting page; the plain files from **PRISMA-S** on the Recall page's *PRISMA report*
  card.
- **PRISMA 2020 checklist.** `wider export prisma-checklist [--protocol P] [--set N]` writes
  `exports/prisma_2020_checklist.md` and `.json` with the 27 items and sub-items of PRISMA 2020 (Page et al., *BMJ*
  2021;372:n71), under the published sections. WIDER fills the items it records: 5 (the research questions and the
  codebook version the protocol screened with), 6 (the databases with their import dates), 7 (the archived strings,
  pointing to `prisma_s.md` for the full strategies), 8 (each pass's model, the third read, the majority rule, how
  many records were left at maybe or decided by the researcher, and each probability sample with its size and
  labellers), 16a (the counts of `prisma_2020.json` for the set), 24b (the hash of WIDER's screening settings, said
  to be not a review protocol) and 27 (the latest replication package). Every other item says "not recorded by
  WIDER" for you to complete; no model writes any of it and no record or seed paper is named. Before any import it is
  refused ("nothing imported yet"). The replication package carries its own copy, whose item 27 names the package
  itself. **UI:** the PRISMA reporting page; the plain files from **Checklist** on the Recall page's *PRISMA
  report* card (for the protocol and set chosen above; since 30 Oct 2026 the Recall page keeps Recall only, D32 F17).
- **Methods paragraph.** `wider export methods` prints a draft assembled from the project's facts: databases and
  import dates, the query version and seed papers, deduplication, enrichment, ranking, the samples and reviewers
  (with Cohen's kappa when two reviewers labelled the same records), the screening model and protocol, the model of
  each pass ("Pass A used a-m, pass B used b-m and the judge used c-m."; a second screen names its reads S1, S2 and
  S3, and "Every pass (pass A and pass B) used a-m." when one model read them all), a sentence for passes that share a
  model and temperature ("Passes A and B use the same model (a-m, temperature 0): their readings are not
  independent."), and the recall estimate of the protocol's primary variant. After a configuration comparison it adds
  one sentence: "2 configuration comparisons were run on the purposive development sample; their figures describe
  that sample, not the corpus." It is a starting point to rewrite in your words; `--save` (or
  Save on the page) writes `methods.md` in the project folder, and an existing text is replaced only when you save.
- **Reference-manager exports.** `wider export references [--which SET]` writes RIS, BibTeX and CSV files of one
  decision set to `exports/references/` for Zotero, EndNote or Mendeley, named `<protocol>_<set>.ris` (`main.s2_…` for
  result set 2). The sets by decision are `include` (the default), `include+maybe`, `maybe` and `exclude`; the sets by
  how the decision was reached are `agreed` (both passes agreed), `third_read` (settled by the third read), `split`
  (no majority, left at maybe) and `researcher` (decided by the researcher), the same routes as the report's
  *Screening agreement*. Each file notes the decision and the record id; the CSV also has a `decided_by` column.
  `--all` (without `--which`) writes every set that holds a record and `<protocol>_index.csv` with each set, its
  number of records and its files; a set that is empty now has its old files removed, so the folder matches the
  index. **UI:** the *Export records* card on the Results dashboard.
  The files carry the abstracts, so they stay on your computer and are never part of the share bundle or the package.
- **Export records (choose what to export).** `wider export selection` writes the records of the active result set
  (`--set N` for another, `--protocol P`) that match a preset, to `exports/references/`. With nothing chosen it writes
  `main_include_and_maybe.ris`: the include and maybe records, the same file as `wider export references --which
  include+maybe`. The choices:
  - `--preset`: `include`, `include+maybe` (the default), `maybe`, `exclude`, `unresolved` (only the records left
    unresolved, at maybe after a split) or `every`.
  - `--pathway` (how the decision was reached, one or more, comma-separated): `agreed`, `third_read`, `split`,
    `researcher`. With none, every route is in, including imported, carried, one-pass and older records, which the
    count line names as "by another route"; once one is ticked those are left out.
  - `--pattern` (the first two reads, one or more): `include_exclude`, `include_maybe`, `maybe_exclude`, or `agreed`
    for both reads the same; a pattern and a pathway together select records matching both.
  - `--final-only` leaves out unresolved records; `--records 3,7,12` keeps only those ids of the selection (an id
    outside it is refused, named).
  - `--format`: `ris` (the default), `bib`, `csv`; several formats make one ZIP of only those files.
    `--no-abstracts` leaves the abstracts out.

  The file name says what was chosen: `<stem>_<preset>[_by_<pathways>][_reads_<patterns>][_final][_picked]`
  (`main_exclude_by_researcher.ris`). `--dry-run` prints the count (how many records, how many unresolved, per
  pathway and pattern), the formats and the file name, and writes nothing; the card's count line shows the same as
  you change the choices, and the export writes that number. An empty selection writes nothing and leaves an earlier
  file of that name as it was. Exporting changes no decision.
- **Audit CSV.** `wider export selection --audit` writes `<stem>_audit.csv` with one row per record of the set:
  final decision, status (`final`, `unresolved`, `not screened`), pathway, the first-two-read pattern, each pass's read
  and model, the researcher's decision and note (no name), origin, the set's rules and settings hash. It covers the
  whole set, so the selection options are refused with it. It has no abstract.
- **Export package.** `wider export selection --package` writes `<stem>_export_package_<time>.zip` with the Include +
  Maybe RIS (unresolved marked), the audit CSV, `summary.md` (counts, models, rules, set, date) and `MANIFEST.txt`
  (the SHA-256 of every file); `--add include,maybe,exclude,unresolved,selection` adds those sets (or the current
  selection) in the chosen formats.
- **UI and what stays here.** Results dashboard, *Export records* card: the preset select, the closed line "Filter by
  how the decision was reached" (pathways, patterns, "Leave out unresolved records"), "Choose records one by one", the
  formats and the abstracts box, the count line, then **Export**, **Audit CSV**, **Download export package** (with
  *Customize package*) and **Every set at once** (the twin of `wider export references --all`). The record exports
  and the package carry abstracts and the audit CSV carries the researcher's notes, so they all stay on your computer:
  none is part of the share bundle or the replication package.
- **Replication package.** `wider export package` writes `exports/replication_package_<time>.zip` with the share
  bundle (no abstracts, no database exports, no raw model responses), the PRISMA diagram, `methods.md` and the
  search-strings archive, `rounds.json` (every query round with its record counts, development recall, the seeds an
  acceptance lost and the frozen query marked), `limits.json` (the date and language limits per database, empty
  where none were applied) and, after an unlock, `unlock.json`, plus `MANIFEST.txt` listing the SHA-256 of every
  file. None of them holds a seed paper's title, abstract or PDF. It also holds the PRISMA-S report, the decision
  ledger (`decisions.jsonl` and `decisions.csv`, dismissed decisions marked, with no rows when there are none), the
  translation ledger next to each archived string and `provenance_<round>.json` for every round. It never holds a file
  from `seeds/`, extracted text or a cached lookup answer. After a configuration comparison it holds
  `comparisons.json`: per comparison its sample, repeats, author, start and finish time (`finished: false` for one
  that was stopped), and per configuration its backend, model, prompt version, temperature, repeats done, the lowest
  and highest misses, false includes, false maybes and agreements, consistency, failed calls, tokens, cost and run
  ids. It names no record (no id, title, abstract or response), and `comparison.csv`, which lists records, stays in
  the run folder. `manifest.json` gains `models_per_pass`: per result set, the models of each pass (`A`, `B`, `C`, or
  `S1`, `S2`, `S3`), read from the calls its decisions count. Once anything is imported it also holds
  `prisma_2020_checklist.md` and `.json`.

On the page, the files open through links that only reach the project's `exports/` folder.

### Names in shared files

For scripts: `wider researchers`.

Every name WIDER records as the author of a change gets one entry in the project's researcher list, with a stable
pseudonym ("Researcher 1", "Researcher 2" ... in the order of each name's first change; a number is never given again).
The card "Names in shared files" on the Recall page and `wider researchers list [--json]` show the list. History, the
record drawer and every page inside WIDER keep the names as recorded.

- **Declared or inferred.** A name typed on the page or given with `--by`, `--author` or `--reviewer` is *declared*.
  A name WIDER took from `$WIDER_REVIEWER` or `$USER` (the login name of this computer), from an imported label file,
  or from a project made before this version is *inferred*; History shows it with "(inferred, not confirmed)". WIDER
  cannot verify who made a change: declared means a researcher typed or confirmed the name.
- **Confirm and join.** "Confirm" (`wider researchers confirm NAME`) makes an inferred name declared. "Same researcher
  as" (`wider researchers join NAME --into OTHER`) says two names are one person: both then share OTHER's pseudonym
  and status. Both are History changes you can undo; neither rewrites a recorded author.
- **The setting.** `wider researchers attribution pseudonyms|names|none` (the card's three choices) decides how every
  file under `exports/`, the share bundle and the replication package write names: `pseudonyms` (the default),
  `names` (declared names as recorded; inferred names stay pseudonyms) or `none` (no name and no pseudonym; the
  bundle's `labels.csv` then numbers the readers of each sample in a `reader` column; decisions imported with a
  sample read "imported labels" in every mode). The bundle and package
  commands and buttons print the mode first. The list linking names and pseudonyms stays on this computer. Sharing
  files written in different modes lets readers match pseudonyms to names.
- **The scan.** Before a bundle or package is written, WIDER scans every file (titles, journals, authors and search
  terms excepted) for each name the mode hides, as a whole word and case-sensitive, and name fields and paths for
  the login name and `$WIDER_REVIEWER` value. A hit refuses the output and names the file, the line and the
  pseudonym: edit a file you wrote (a `methods.md` saved before this version may still hold a name: regenerate or
  edit it); a name in a file WIDER wrote is a WIDER bug to report. Names shorter than 3 characters are not scanned;
  WIDER replaces them in name fields and beside their own change only.
- Exports written before this version are not rewritten; the next write follows the setting.

## 11. Extension points

For scripts: no commands yet; the registry names them `fulltext`, `extract` and `synth`.

Full-text screening, data extraction and synthesis are registered in `wider/stages.yaml` as extension stages
(`wider stages` lists them with status `extension`) but have no commands yet; the abstract-level review ends with
the report. An extension adds its entry to the registry (its `requires`, what it `produces` and the `done_when`
checks that mark it done), a CLI sub-app, and a UI route module, without touching the core stages. Until then,
take the included and maybe records from `exports/share_bundle/screen_decisions.csv` (or the store) into your
full-text tool of choice.

## Choosing an LLM backend and embedding model

WIDER is backend-agnostic: the screening procedure is the same whichever model answers. Backends are listed in
`project.yaml` under `providers:` (LLMs) and `embeddings:`; `default_provider` and `default_embedding` name the
ones used when a command has no `--provider` / `--embedding` option. API keys are never written in the file:
`api_key_env` names an environment variable, and `wider providers list` / `wider embed list` tell you whether it is
set.

### The picker cards

The model page, the Ranking page and Settings show each backend as a card: its name and model, a **local**, **local
network**, **cloud** or **unknown** chip, where its text goes ("Sends to: api.openai.com (OpenAI)", "this computer"),
whether its key variable is set (never the key), its last check, and a **Choose** button. The chosen card shows a
**Chosen** chip instead of the button. The built-in test model is hidden behind **Show test models** unless it is the
chosen one, and the Pass B and Judge lists leave it out unless it is already picked: it gives no real answers. The cards are drawn from the same rows `wider providers list --json` and
`wider embed list --json` print; drawing them contacts nothing.

### The Model for query creation page

The first page of a project chooses the LLM that writes the concept, synonym and search-string proposals (the
*construct* backend). Opening it reads the project's backends only; detection, the preset list, model lists and Check
run when you press them.

**The first time** (no construct backend set) the page shows three steps:

1. *Where should the model run?* **CLI** (an agentic command-line tool you already use; it sends the text to its
   vendor), **Cloud** (an API that needs a key variable; the text leaves this computer) or **Local** (a model server on
   this computer; nothing leaves it). The choice filters the cards and the backends found, with **Find AI models on this
   computer** and **Add an AI model by hand** (see below).
2. *Which model?* The cards of that kind. **Choose** here only marks the card for step 3; nothing is saved.
3. *Check it.* The marked backend with where its text goes. **Check NAME** sends one fixed test question (a backend
   off this computer asks for consent first, see [Consent](#consent-for-cloud-backends)); **Use NAME for query
   creation** saves the choice.

**Afterwards** the page shows every backend as a card with the chosen one marked; **Choose** on another card saves at
once. Saving makes the backend the construct backend and then the project default, two History entries, the same as
`wider providers stage construct NAME` followed by `wider providers set-default NAME`. The *Concepts and synonyms*
page then preselects it in its backend list (else the project default). Edit, Remove, Revoke and the backend per
stage are on Settings. If the construct backend is removed, the three steps come back. Check on a card is
`wider providers probe NAME`.

### The embedding picker on Ranking

The Ranking page opens with the embedding models as cards. **Choose** makes a model the project's embedding model,
used by Embed corpus and Rank (`wider embed set-default NAME`, one History entry). **Check** sends fixed test
sentences (`wider embed probe NAME`; an API model asks for consent first). A local sentence-transformers model shows
its size and a **Download** button: the weights are fetched only when you press it and confirm
(`wider embed download NAME`). **Edit**, **Remove** and **Add an embedding model** open the embedding form
(`wider embed edit|remove|add`). Embed corpus and Rank stay disabled until a model is chosen.

### Finding and adding a backend

**Find AI models.** The Model for query creation page has **Find AI models on this computer** (**Add or find AI models**
on Settings opens it); on the command
line it is `wider providers detect`. It runs only when you ask, and nothing leaves the computer: it looks for the
programs on the PATH and in the usual app bundles, asks local servers on 127.0.0.1 (Ollama, LM Studio, vLLM,
llama.cpp) which models they hold, and checks whether the key variables are set (never their values). A sign-in is
read from a key variable or the existence of the tool's credential file, which is never opened. WIDER installs
nothing; when nothing is found, add a backend by hand.

**Presets.** The results are cards in three groups: CLI (Claude Code, Codex, Gemini CLI, Antigravity CLI, another
command-line tool), Cloud (OpenAI, Anthropic, OpenRouter, Google Gemini, Azure OpenAI, Mistral, Groq, Together, Jev)
and Local (Ollama, LM Studio, vLLM, llama.cpp). Each card says whether the tool was found, its version and place,
whether it is signed in or its key variable is set, where text is sent ("Sends to: api.openai.com", "OpenAI (via
Codex)", "this computer"), how it bills, and when it was checked. "Runs: checked against the docs, not yet run on this
computer" means the preset follows the vendor's documentation but no real call was made here; it says nothing about
screening quality. `wider providers presets` prints the catalogue.

**Add and Edit.** **Add** on a card opens the form filled from the preset; nothing is saved until you press Save,
and Cancel saves nothing. The name defaults to the preset id, or the first free `<id>-2`, `<id>-3`: a new project
already holds `ollama`, `openai`, `anthropic` and `claude-code`, so a card whose id names an existing backend also
shows **Edit <name>**. **Edit** changes some settings and keeps all others (`wider providers edit NAME --model m2`;
every `extra` key you do not name stays, and an empty value removes it); the name and kind cannot be changed.
Adding over an existing name *replaces* the backend: settings you did not give go back to their defaults. The form
then asks first, listing the fields that would be reset, with **Replace**, **Edit instead** and **Cancel**; on the
command line `wider providers add --name NAME` over an existing name replaces it and prints the reset fields.
`wider providers add --preset ID [--name NAME]` is the command-line twin of Add. `wider embed add|edit` do the same
for embedding models.

**The form.** Name, kind, model, base URL, key variable and the input and output prices are always shown. The
**Advanced** section holds the cached-input and cache-write prices, `max_output_tokens` (the most output tokens the
backend allows; a screening run whose `screening.max_tokens` is above it is refused before any call), temperature,
timeout, concurrency, extra headers and the other `extra` settings, one `key: value` per line.

**Model lists.** The model field suggests the models the preset lists (only ids checked against the vendor's own
page) and accepts any id you type. **Load models from <vendor>** asks the backend for its list. For a local server
and a CLI tool this happens when the form opens; for a cloud or local-network backend only when you press the
button, and only with your consent for the `models` operation (it sends the key header and no project data;
`wider providers models NAME`). On a form filled in by hand the button asks a server on this computer for its
list right away (`wider providers models --kind openai_compat --base-url URL`); any other address must be saved
and allowed first. A backend name may not contain `/`, `\` or control characters.

**LLM backend kinds** (`kind`):

- `openai_compat`: any OpenAI-compatible chat API (`base_url`, `model`, `api_key_env`). This covers OpenAI itself,
  OpenRouter, Gemini, Azure OpenAI, Mistral, Groq, Together, and local servers that speak the same protocol.
- `anthropic`: the Anthropic API (`model`, `api_key_env`).
- `ollama`: an Ollama server (`base_url`, `model`; `extra: {think: false}` turns off thinking for models that
  have it). Keyless and free on this computer, slower. An Ollama model that Ollama itself runs in its cloud (its
  `/api/tags` entry names a remote host, or its name ends in `-cloud`) is a cloud backend: its destination is
  "ollama.com (via Ollama)" and it needs consent. When Ollama does not say where a model runs, the locality is
  "unknown" and is treated like cloud; it is never shown as local. An Ollama on another computer names that host as
  its destination (for example "ollama-host (via Ollama)"); only a model whose name ends in `-cloud` names ollama.com
  there ("ollama.com (via Ollama at ollama-host)"), because WIDER reads the tag list on this computer only.
- `cli_agent`: an agentic CLI, run as one isolated process per record in an empty temporary folder with no tools.
  `command` is an argv template (a list, or one string) with the placeholders `{model}`, `{system_file}`,
  `{schema_file}`, `{system_toml}`, `{system}`, `{user}`; the user message goes on stdin (`extra.stdin: user`) and the
  answer is parsed according to `extra.output` (`claude_json`, `codex_jsonl`, `gemini_json`, `agy_json`, `text`,
  `last_line` or `json`). A tool that is not signed in fails at once with the hint of its preset.
- `typesafe`: Jev (below).
- `litellm`: the LiteLLM router (optional extra) for providers not covered above.

Settings common to all: `temperature` (0 by default), `seed`, `max_concurrency`, `timeout_s`.

### Notes on particular backends

- **Claude Code** uses your Claude plan or `ANTHROPIC_API_KEY` and reports a cost per call.
- **Codex** is found on the PATH or inside the Codex and ChatGPT apps and the VS Code extension, so a bundled copy
  works without an install. WIDER's protocol text goes into Codex's `developer_instructions`, which add to its
  built-in instructions rather than replacing them; the record goes on stdin. Sign in once in a terminal
  (`codex login`) or set `OPENAI_API_KEY`. No cost is reported per call. The protocol and codebook text (never a
  record) is visible in this computer's process list while a call runs.
- **Gemini CLI** works with a paid API key or Gemini Code Assist only; the free tier moved to Antigravity CLI. It is
  experimental: whether your personal `~/.gemini` settings, `GEMINI.md`, MCP servers or extensions take part in a
  call is not verified.
- **Antigravity CLI** is experimental. It has no documented system-prompt channel, so the protocol text and the
  record travel together in the prompt, which is a process argument: both are visible to other programs on this
  computer during each call. Whether it follows the protocol as instructions is unverified.
- **Jev (TypeSafe AI)** cannot write text: it answers yes/no, choice and score questions with probabilities. It can
  screen, but construct proposals and adjacent-literature searches refuse it ("Jev cannot write text; choose a
  generative backend"). Its quotes are empty, so no grade has a verified quote: under the recall-first rule an
  "absent" grade becomes "unclear", and more records end as maybe. The summary field is empty. Its data retention
  policy was not found in the vendor's documentation; check it before sending confidential records.
- Gemini CLI and Antigravity are "not yet run on this computer"; the real calls are still
  to be made.

### Experimental backends

A preset marked **Experimental** has a known limit the card names (record text in the process list, instructions
sent inside the prompt, isolation not verified). It is never a default or preselected, and adding it needs an
explicit acknowledgement: the ticked box in the form, or `--accept-experimental`; the history records who gave it.
Results are marked, not hidden: the run, the models summary, the result card, the exports, the share bundle and the
report's methods line say "experimental backend: not validated". A backend that runs is not validated because it
runs: compare it with a validated backend on the development sample before trusting it, and do not use it for
confidential data.

### Keys and headers

Keys live in environment variables, never in `project.yaml`. Set the variable in your shell profile
(`~/.zshrc` or `~/.bashrc`):

```bash
export OPENAI_API_KEY="..."        # then open a new terminal, or run: source ~/.zshrc
```

and give WIDER its *name*: `--api-key-env OPENAI_API_KEY`, or the key-variable field of the form. Start `wider ui`
from a terminal that has the variable. A header whose name contains `key`, `token`, `auth`, `secret` or `cookie`
(for example `X-Gateway-Key`) is sensitive: its value must be a reference, `{env:NAME}`, and the form shows a
variable-name field for it. WIDER reads the variable only when it builds the request; a missing variable stops the
call before anything is sent. A plain value in a sensitive header, or anything in `extra` that looks like a key, is
refused and nothing is saved. Only the reference is ever stored, in `project.yaml`, run snapshots, exports, logs and
the share bundle. HTTP backends do not follow redirects, so a key is never sent to another host.

### Consent for cloud backends

Before WIDER contacts a backend that is not on this computer (cloud, local network, or unknown), it asks, once per
backend and operation:

- `models`: the model-list request; sends the key header and no project data;
- `probe`: the fixed test question (17 + 25) and the key header;
- `research`: screening, construct and adjacent proposals; sends the protocol and codebook text and, per call, one
  record's title and abstract;
- `passages`: the model pass of seed-paper mining; sends, per call, one passage of a seed PDF's extracted text,
  never the file (asked after `research`, only while the passages switch is on).

Embedding services have their own consent (`probe`: fixed test sentences; `research`: record titles and abstracts,
anchor and construct texts), kept apart from LLM consent. The dialog names the backend, where text goes and what is
sent, with **Allow and continue** and Cancel; Cancel sends nothing, and everything local keeps working. The
command line asks y/N in a terminal and otherwise exits 2 with nothing sent. Consent is remembered in `project.yaml`
with who gave it, and asked again when the destination, key variable, header names or the description of what is
sent change (a model change on the same endpoint keeps it). The consent cards on Settings list what each
backend may do, with **Revoke**. Commands: `wider providers consent`, `wider providers allow NAME --for
models|probe|research|passages`, `wider providers revoke NAME [--for OP]`, and the same under `wider embed`. Scripts without a
terminal need `allow` first. Detection needs no consent: it never leaves 127.0.0.1.

### Local network backends

Only `localhost`, `127.0.0.0/8` and `::1` count as this computer. A server on another machine of your network
(a private address such as `192.168.1.20`, or a name ending in `.local`, `.lan`, `.internal`, `.home.arpa`, or a
single-label name) is shown as **Local network**, with the host and "another computer on your network: data leaves
this computer", and needs the same consent as a cloud backend; a new host asks again. An `http://` address on the
network or the internet adds the warning "research data would travel unencrypted (http) to <host>". `0.0.0.0` is a
listening address, not a destination, so WIDER cannot tell where it leads and calls it "unknown" (treated like
cloud). Locality is read from the address only; no name is looked up.

### Costs and estimates

The cost of a call is the one the backend reports, else the prices you set (input, output, and in Advanced the
cached-input and cache-write prices; without them cached tokens are priced as input), else zero on this computer,
else "not priced", never a guess. A logged call keeps the cost computed when it was made; a later price edit
changes only estimates. Before a screening run, a construct proposal and `embed corpus`, the run form and the
command print a range: the low end assumes the system prompt is cached after the first call, the high end no
caching; the assumptions are printed with it, and `--estimate-only` prints it without a call. Embedding estimates
count input only, with the embedding model's price. During a run the job bar and the command line show "cost so far
$x · projected $a–$b". Result cards, `wider costs` and `wider info` show cached tokens, or "cache use not
reported".

### Checking the backends

**Check backends** (`wider check-backends`) probes each distinct backend once: the default and every stage
override, each row naming its uses ("LLM a (default, construct)"). The check sends one fixed test request (fixed
test sentences, no project data) to each checked backend; on a paid backend it may cost a little. A cloud backend
without `probe` consent is not called; its row says "not checked: needs your consent" and names `wider providers
allow NAME --for probe`. Jev gets its own yes/no probe.

Listing the backends never contacts them: `wider providers list` and `wider embed list` (add `--json` for the same
fields the picker cards show) report where each backend runs from its address alone, and an Ollama backend whose
models may run elsewhere reads "unknown, press Check" until a check has looked. Each check is kept with its time and
shown as the backend's last check until the backend's address or model changes.

### Probes and embedding models

**What a probe tells you.** `wider providers probe` sends one tiny request with a JSON schema and expects the
model to answer `status: ok` and the sum 17 + 25. A green `yes` means the endpoint is reachable, the key is
accepted, the model name exists, and the model can follow a schema, which the screening relies on; `attempts`
and `seconds` give a feel for reliability and speed. A probe says nothing about screening quality: that is what
the development-sample evaluation is for, and the honest way to choose between two backends is to run
`wider screen run sample:development --provider <name>` with each and compare `wider screen evaluate`.

**Embedding models** (`kind`): `sentence_transformers` (local, keyless; weights are downloaded once when you pass
`--allow-download`), `openai` / `openai_compat` (API; `dim` truncates the vector, which the original review used
with `text-embedding-3-large` at 1024 dimensions) and `ollama`. The project default is `bge-m3`
(`BAAI/bge-m3`, multilingual, keyless), so a new project ranks without any key; `openai-large-1024` reproduces the
original review's model. `wider embed probe` embeds three sentences and checks that the related pair scores above
the unrelated one, reporting the dimension and the time per call. Anchors are embedded with the same model as the
corpus, automatically, and `wider rank ablate` compares models on how well they recover the seed papers.
Corpus embeddings are cached in `embeddings/cache.sqlite` per model, so trying a second model costs only its own
run.

## Privacy and reproducibility

**What is stored where.** Everything lives in the project folder:

| path | contents |
|---|---|
| `project.yaml` | questions, notes, platforms, backends (key *names* only), protocol settings, seed, online-lookup consent (when and by whom) |
| `benchmark.csv` | the seed papers |
| `inputs/<platform>/` | the raw database exports, as you provided them; `inputs/citation_chain/` holds the files written by citation chaining |
| `prior_reviews/` | the reference lists and PDFs of prior reviews you added, copied as provided; parsed locally, never sent anywhere |
| `store.sqlite` | records, benchmark matches, dedup pairs and decisions, enrichment log, rankings, samples, labels, runs, screening calls and decisions, artifact index, the log of outside requests (service, purpose, count, time), the lookup cache (answers to seed chaining) |
| `artifacts/<kind>/vN.yaml` | every version of cells, codebook, synonyms, anchors, near_miss and prompts, with its hash and status in the store |
| `embeddings/cache.sqlite` | embedding vectors per model |
| `runs/<kind>/<stamp>_<run_id>/` | `run_spec.json`, `prompt.md`, `schema.json`, `responses.jsonl` (raw model output), `decisions.csv`, `errors.csv`, `eval_report.md` |
| `exports/` | dedup audit CSVs, rendered queries, `report.md`, `report.json`, `share_bundle/`, `prisma_s.md`, `provenance_<round>.json` |
| `~/.wider/recent_projects.json` | outside the project: the folder paths of the projects opened with `wider ui` and when, for the home page's list (`$WIDER_HOME` moves it); nothing else |

The project folder gets a `.gitignore` that excludes `inputs/`, `prior_reviews/`, the store, embeddings, runs and
exports, so a project under version control carries only configuration, seed papers and artifacts.

**Run specs and hashes.** Every model run writes a `run_spec.json` with the WIDER version, Python and platform,
the sha256 of each input (prompt, schema, codebook version), the settings, the provider identity (kind, model,
endpoint or command, never the key) and a `spec_hash` over all of it. Artifacts are versioned with their sha256;
samples record their ranking, seed and design; the protocol hash (`wider protocol show`) is the sha256 of the
whole protocol section and changes whenever a setting departs from the defaults, so two reports with the same
hash were produced under the same rules. `wider screen replay <run_id>` rebuilds decisions from the stored
responses without any network call, so a changed decision rule can be re-applied to old reads.

**Share bundle.** `wider report --bundle` writes `exports/share_bundle/`: `project.yaml`, `benchmark.csv`, all
artifact versions, every run's spec, prompt and schema, its decisions (without the `parsed` JSON, which carries
quotes from abstracts, and without the `error` text) and its `errors.csv` reduced to record id and pass (the error
text can echo model output; the runs' `eval_report.md` is left out for the same reason), `records.csv` (ids, titles, years, journals, DOIs, platforms, abstract
word counts; no abstracts), `screen_decisions.csv`, `labels.csv`, the report, and the dedup and query exports. It
contains no abstracts, no RIS exports and no raw model responses, so it can be attached to a submission or put in a
repository.

**What leaves the machine.** Nothing, except: the calls to the LLM backend you chose (each call carries the
prompt and one record's title and abstract), the calls to an embedding API if you chose one instead of a local
model, DOI and title lookups to OpenAlex, Crossref, Europe PMC and Semantic Scholar when resolving and fetching
seed papers and during `wider enrich`, and a one-time download of local model weights. Those lookups are off until
the project allows them: the first one asks, cancelling sends nothing, and every request is logged in the project
(see [Online lookups](#online-lookups); `wider lookups revoke` withdraws the consent). Prior-review files never leave; the
DOIs, titles and OpenAlex ids of their included studies are sent to OpenAlex only when the project's lookup switch
is on (`wider prior-reviews resolve` and `chain`), and their titles enter construct-proposal prompts only when the
excerpt switch is on (see [Prior reviews](#prior-reviews)). Listing candidates from the seed
papers sends the DOIs of the Development seeds, then their OpenAlex ids, to OpenAlex under the online-lookup consent;
a Validation seed's DOI is never sent. Comparing the seeds' reference lists sends the same DOIs to OpenAlex, and its
Crossref tier sends the parsed title of a reference entry without a DOI, both under the same consent; its GROBID tier
sends the PDF only to a GROBID server on this computer; importing candidates from a file sends nothing. A search term or the query string leaves only under its own switch (`wider lookups terms
allow`, off by default); the OpenAlex seed check sends Development seed DOIs with the query and needs both switches. The second reader sends the rendered search strings to the backends you chose for it, as
the query-building calls send the construct. A repeated lookup is answered from the project's cache and sends
nothing. With Ollama and a local embedding
model, the review runs entirely offline after that download; with an agentic CLI, the data goes wherever that tool
sends it. Seed PDFs never leave: the mining model pass sends one extracted passage per call, to a backend that is
not local only under the passages switch and its `passages` consent (`wider seeds policy`, Settings "Seed papers"),
and so does the model tier of the reference lists with one reference entry per call; names from connected papers send
a candidate's title, abstract and keywords per call under the backend's `research` consent;
the PDFs, their text and the passages are left out of the share bundle, the replication package and every export.
The UI binds to 127.0.0.1 and is never exposed to the network.

**Reproducing a run.** The seed in `project.yaml` fixes sampling and the bootstrap; the protocol fixes the rules;
the artifacts fix the inputs; the run specs fix what each model saw. Rerunning a stage with the same inputs and
settings reproduces its numbers; the model calls themselves are reproducible only as far as the backend is
(temperature 0 and a seed are requested where the API accepts them), which is why decisions are stored and
replayed rather than recomputed.

## For developers: the message list

`scripts/list_messages.py` lists every message WIDER shows to a researcher: the text of the web pages, toasts and
dialogs, API errors and result texts, CLI output and help, stage texts from `wider/stages.yaml` and the catalogue
texts of `presets.yaml` and `platforms.yaml`. Each line gives the text, its `file:line`, its kind and the page
where it is seen (or "every page", "any page that calls this", "page unknown", "CLI only"), and flags the terms of
the word list in `docs/guide-text.md`, snake_case names, inline commands and raw timestamps. A count table comes
first.

    .venv/bin/python scripts/list_messages.py [--out FILE] [--summary] [--root DIR]

`--out` writes the list to a file instead of standard output, `--summary` writes the count table only, and `--root`
names the checkout to read (default: the folder above `scripts/`). Run it with `.venv/bin/python`, which has
PyYAML. It reads only source files under `wider/` and the word list; it opens no project, contacts nothing and
writes nothing but the `--out` file. The spec is `openspec/specs/message-inventory/spec.md`.
