# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary:** researchers running a systematic literature review, usually in organisational and social science. They
know review methods (or are learning them) but not the command line, AI models or file formats. They work on their
own computer, often over weeks, coming back to a project between other work. The UI is designed for them first; the
CLI stays for technical users and gives the same results.

**Second reviewers:** colleagues who label a sample blind or decide possible duplicates. They often see only one or
two pages, must not see the AI model's decisions or validation results while they label, and need to understand the
page without the rest of the review.

(Confirmed by the author, 17 Oct 2026. Journal reviewers and co-authors who only read the report were not chosen as
design audiences.)

## Product Purpose

WIDER helps one researcher (with a second reviewer) carry out a systematic review of a literature that is hard to
search: the same phenomenon studied under many names across fields, so keyword searches either miss papers or return
far too many. It leads the researcher from the research question and seed papers through query building, importing
database exports, deduplication, ranking, sampling, AI screening, a recall estimate and the methods report.

Success: a researcher who is not technical finishes a review whose search and screening can be defended in a
journal, with a recall estimate and a report that state every number, and could repeat every step.

## Positioning

- **Free and runs on the researcher's computer.** Nothing leaves the computer unless the researcher switches it on
  for that project; no telemetry. Local AI models are first-class.
- **Validated by the researcher's own papers.** Known seed papers are split into a query development set and a
  held-out query validation set; the search is built on one and tested on the other after it is locked.
- **Recall is measured, not assumed.** A stratified sample labelled by people gives a recall estimate with a
  confidence interval.
- **Screening under a locked codebook**, with verbatim quotes, a recall-first rule and a majority of reads.
- **Every step can be replayed offline**; every model call is stored and every manual change is recorded.

## Operating Context

- A local web UI (FastAPI server, plain HTML/CSS/JS, opened in the researcher's browser) with a CLI twin for every
  action. One project is one folder on the researcher's computer.
- Pages follow the review in order: Your projects; Literature search (model, question and seed
  papers, concepts and synonyms, query, search results); Deduplication; Screening (ranking, samples, labelling,
  screening, recall, report). The sidebar groups pages under these sections.
- Records arrive only as exports the researcher downloads from databases (Scopus, Web of Science, EBSCO, ProQuest,
  Ovid, PubMed, Google Scholar) and uploads; WIDER queries no database itself.
- Optional online services: OpenAlex, Crossref, Europe PMC and Semantic Scholar for missing abstracts (DOI and title
  only), and online AI models. Each needs the researcher's consent per project.
- Long jobs (screening, ranking, model checks) run for minutes to hours; a local model may take a minute to answer.

## Capabilities and Constraints

- **Privacy is non-negotiable:** real review data never enters the repository and is never sent anywhere without
  the per-project switch; API keys are named by environment variable only and never typed into WIDER; WIDER never
  downloads a model or installs software by itself.
- **CLI/UI parity:** every UI action has a CLI command that calls the same engine and shows the same summary.
- **The procedure is fixed;** preferences (models, thresholds, seed papers, databases) are editable with documented
  defaults.
- **Hard-to-reverse steps** (lock the query, lock a sample, mark final) are recorded in History, the methods text
  and the replication package; unlocking warns that validation is no longer blind.
- **Blinding:** validation results stay hidden until the query is locked; a blind labeller does not see model
  decisions.
- **Plain words:** the UI uses the word list in `docs/guide-text.md` (for example "model", not "backend";
  "screening round", not "run"; "lock", not "freeze"). Approved plain versions of each message are in
  `docs/plain-words.md`. No file names or commands in UI text.
- **Online services are named:** wherever a step can send something out, the page says which service, what is sent
  and that WIDER asks first.

## Brand Commitments

- Name: **WIDER** (all capitals). No logo or other brand assets exist.
- Look: a clean, familiar web-app look (white, near-black text, one blue for the main action, violet for what
  WIDER did on its own, three status colours for open, needs you and unsure, Public Sans shipped with WIDER), with the step's counts as a flow chart and the notebook available on demand; chosen by the author over the
  calmer notebook looks, 18 Oct 2026 (`DESIGN.md`, "Decided direction for A1").
- Voice: short sentences, everyday words, "you" for the researcher, calm and exact; it explains what a step is for,
  what to do now, what "done" means and the common mistakes (`docs/guide-text.md`).

## Evidence on Hand

- A synthetic, topic-neutral demo project (`examples/synthetic_project`, served as `.demo/practice` by the
  `wider-ui-demo` launch entry) for screenshots and tests.
- The proposal context: a Stage-1 prototype for the *Organization Science* special issue "AI-Enabled Frontiers in
  Organizational Science" (`README.md`; the proposal drafts are kept outside this repository).
- No users, testimonials, benchmarks or published results yet; future work must not invent them. Real review data
  must never appear in screenshots, mocks or examples.

## Product Principles

1. **One step at a time.** Each page says what the step is for and what to do now, and offers one clear main action.
2. **Nothing leaves the computer silently.** Every outside contact is named, explained and opt-in.
3. **Defensible by default.** Locks, blinding and the record of every change matter more than speed; the UI makes
   hard-to-reverse steps look and feel different.
4. **Plain first, details on demand.** Counts and states in everyday words; model names, tokens, parameters and
   file paths sit under Details.
5. **The UI and the CLI tell the same story.** The same numbers and the same words in both.

## Accessibility & Inclusion

- Target: **WCAG 2.2 AA** (confirmed by the author, 17 Oct 2026): contrast, full keyboard use with visible focus,
  labels on every control, status never shown by colour alone.
- The UI is tested by a keyboard-only persona and a blind-second-reviewer persona.
