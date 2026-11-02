# Guide text (draft, 17 October 2026)

The text of the guide panel for each page, written before the design pass. The
first two lines of each page also become the plain sentences at the top of that page.

Rules for the text:
- Short sentences, everyday words, "you" for the researcher.
- Use words from the word list at the end. Leave out internal names, file names and commands (the CLI has its own
  help).
- Online services: whenever a step can send something to an outside service (OpenAlex, Crossref, Europe PMC,
  Semantic Scholar, or an online AI model), the guide says which service, what is sent (for papers: DOI and title),
  and that WIDER asks first. Where a service limits free use per day, the guide says that the researcher's own key
  (set as an environment variable, never typed into WIDER) avoids the limit.
- Each page has four parts: **What this step is for**, **What to do now**, **Done when**, **Watch out**.
- **The guide notices** lists what the panel says when the project is in a certain state. These lines replace the
  general text when they apply.

## Before the review

### Your projects (Home)
- **What this step is for:** a project is one review. It keeps everything in one folder on this computer.
- **What to do now:** open a project you already have, or create a new one in an empty folder.
- **Done when:** a project is open. WIDER then takes you to the first step.
- **Watch out:** "Remove from list" only hides a project here. The folder and its contents stay on your computer.

## 1 Literature search

### Model for query creation
- **What this step is for:** WIDER uses an AI model to suggest concepts and search terms for you. Here you choose
  which model, and where it runs.
- **What to do now:**
  1. Choose where the model runs: on this computer (private, free, slower) or with an online service (faster,
     usually costs money, and your question and seed papers are sent to that service).
  2. Choose a model.
  3. Press **Check** to send one short test question. Nothing is sent anywhere before you press it.
- **Done when:** the check answers, and you choose the model for query creation.
- **Watch out:** you can change the model later. Earlier suggestions keep the name of the model that made them.
- **The guide notices:**
  - No model on this computer: "No model was found on this computer. You can install one, or use an online service."
  - The check failed: "The model did not answer. Check that the program is running, then try again."

### Review plan
- **What this step is for:** you tell WIDER what your review is about. You also give it papers you already know
  belong in the review (seed papers). WIDER uses the seed papers to test whether the search finds what it should.
  The page shows one card per task; each card opens the task on its own page.
- **What to do now:**
  1. **Research question:** write your research question.
  2. **Seed papers:** add your seed papers, pasted or from a file. WIDER splits them in two, as in any
     out-of-sample test: the **query development set** helps you build the search, and the **query validation set**
     stays hidden until the search is locked, so the search is tested once on papers it was not built on. These two
     sets are for the search only; the ranking and screening models are tested later on a separate sample.
  3. **Prior reviews:** say whether reviews on your topic already exist, and add them if they do.
  4. **Databases:** choose the databases you will search, in the order you will search them.
  5. **Find more seed papers** (optional): look through the reference lists and citations of your seed papers.
- **Done when:** the four required cards read Done. Questions from WIDER about a task open in the window at the
  bottom right of the page.
- **Watch out:**
  - Add enough seed papers. With very few, the final test says little.
  - Looking up papers online sends their titles and DOIs out, so WIDER asks you first.
- **The guide notices:**
  - No development papers: "All your seed papers are in the query validation set. Move some to the query
    development set, or the search cannot be checked while you build it."

### Concepts and synonyms
- **What this step is for:** you build the search from the parts of your question (concepts) and the words authors
  use for each part (synonyms). The AI model can suggest these. You decide what stays.
- **What to do now:**
  1. Ask the model for a suggestion, or write your own.
  2. Edit it and save it. Every save is kept as a new version.
  3. When you are happy, approve the version. Later steps use the approved version.
  4. When the search is final, lock it. After that, WIDER tests it once on the query validation set.
- **Done when:** concepts and synonyms are approved. Locking comes once the search is final.
- **Watch out:**
  - Lock only when the search is final. You can unlock it, but WIDER first warns you: the query validation papers
    shown so far count as seen, so they become part of query development, and only papers you add to the query
    validation set afterwards can test the search. The unlock is recorded in History and in the report, and it
    cannot be undone.
  - Looking at the query validation set before you lock, or locking a different version, asks you first whether the
    papers shown count as seen or stay in the query validation set. You decide; WIDER records the choice and every
    output states it.
  - The seed-paper table shows which query development papers the current search finds. Look at it before you lock.
- **The guide notices:**
  - Development papers not found: "3 of your query development papers are not found by this search. Look at their titles and
    abstracts for words to add."
  - Saved but not approved: "You saved a new version but have not approved it. Later steps still use the old one."

### Search strings
- **What this step is for:** WIDER writes your search for each database, in that database's own syntax.
- **What to do now:**
  1. Copy the search for the first database.
  2. Run it on the database's website.
  3. Export the results, then upload them on the next page.
  4. Do the same for each database.
- **Done when:** the search is locked (on Concepts and synonyms). Then this page shows the final strings.
- **Watch out:** run the exact string shown. If you change it on the website, WIDER no longer knows what you
  searched.
- **The guide notices:**
  - Not locked yet: "The search is not locked yet. You can try it on a database now, but lock it before the
    exports you keep."

### Search results
- **What this step is for:** you bring the results from each database into WIDER.
- **What to do now:**
  1. Choose the database, choose the export file and press **Upload and check**.
  2. Read the check. It says whether the file looks right, is empty, is a duplicate or looks off topic.
  3. When every database is in, press **These uploads are final**.
- **Done when:** at least one file is in. The review moves on once you mark the uploads final.
- **Watch out:**
  - Marking uploads final opens the query validation set. A file you add afterwards reopens the uploads: WIDER
    imports it and records it like an unlock, so the query validation papers shown so far count as seen (when the
    search is locked, it asks you first). You can undo the mark in History.
  - Each uploaded file has a box "Enters deduplication". Untick it to keep a file out; it stays listed and is counted
    as not used.
  - "Include the validation set" on the coverage table shows the query validation set early. If you do this before
    the search is locked, WIDER records it, because it weakens the out-of-sample test.
- **The guide notices:**
  - New files after cleaning: "You added 2 files after removing duplicates. Run Remove duplicates again."
  - An empty file: "This file has no records. Export it again from the database."

## 2 Data cleaning

### Deduplication
Shipped with WIDER in `wider/guide/pages/dedup.md` (the one source; the page's guide panel and `wider guide dedup`
show it). Its notices are written in `wider/guide/__init__.py`:
  - Pairs waiting: "12 pairs wait for your decision. Decide them, then press Apply decisions and check again."
  - New uploads since the last run: "You added 2 files since the last run. Press Apply decisions and check again."
  - No duplicates at all: "No duplicates were found. Check that every database's export is uploaded."

### Missing abstracts
- **What this step is for:** some records come without an abstract, or with a very short one. Screening works
  much better with abstracts, so WIDER can look them up by DOI.
- **What to do now:**
  1. Look at how many records lack an abstract. If none do, you can move on.
  2. Press **Fetch**. WIDER looks them up in batches.
- **Done when:** a lookup has run, or no record lacks an abstract.
- **Watch out:**
  - The lookup sends each record's DOI and title to OpenAlex, Crossref, Europe PMC and Semantic Scholar. WIDER asks
    you first; the consent is kept for the project and can be withdrawn on Settings. Nothing else from your review
    is sent.
  - Free use of some services is limited per day. With your own key (Semantic Scholar: an environment variable
    named S2_API_KEY) the lookup is faster and is not turned away. Without a key, turned-away records can be tried
    again later.
  - An abstract you already have is never replaced.
- **The guide notices:**
  - Nothing to do: "Every record has an abstract. Go on to Ranking."
  - Turned away: "18 lookups were turned away by a daily limit. Try again tomorrow, or set your own key."

## 3 Model tuning and validation

### Ranking
- **What this step is for:** WIDER orders all records from most to least likely relevant, so you and the
  screening model see the likely papers first. It compares each record with one short sentence per concept that
  describes what a relevant paper aims at (the anchor sentences you approved on Concepts and synonyms).
- **What to do now:**
  1. Choose the model that turns text into numbers for the comparison (the embedding model).
  2. Press **Embed corpus**. This reads every record once; it can take a while.
  3. Press **Rank**.
  4. Press **Check with seed papers** to see where your query development papers land. Seed papers far down the
     list mean an anchor sentence is off: fix it and rank again.
- **Done when:** a ranking exists and your seed papers land near the top.
- **Watch out:**
  - The query validation set stays hidden here until the search is locked or the uploads are final.
  - An online embedding model sends every record's title and abstract to that service. WIDER asks first.
- **The guide notices:**
  - No approved anchor sentences: "Approve the anchor sentences on Concepts and synonyms first."
  - New records since the ranking: "Records changed since the last ranking. Embed and rank again."

### Samples and labelling
- **What this step is for:** you label a random sample of records yourself (include, exclude, maybe). These labels
  are the yardstick: they test the screening model and give the recall estimate in the report. This sample is
  separate from the query development and query validation sets.
- **What to do now:**
  1. Press **Draw samples**. WIDER draws from the top, middle and bottom of the ranking.
  2. Open the queue and label each record (keys 1, 2, 3).
  3. If a second reviewer labels the same sample, compare your labels and settle the differences.
  4. When labelling is complete, lock the sample.
- **Done when:** the sample is labelled and locked.
- **Watch out:**
  - Label blind: while you label, WIDER hides the rank and the model's score.
  - The development sample also holds boundary cases: records close to both one of your criteria and a near miss or
    a neighbouring field. They are the hardest records for the screening model. On a sample drawn before this, press
    **Add boundary cases**; your labels stay, and the new records are mixed in among the ones you have not labelled.
  - After locking, no labels can be added or changed. You can unlock, but WIDER warns you first: the recall
    estimate is then no longer blind. The unlock is recorded.
- **The guide notices:**
  - Labels missing: "42 of 150 records in the sample have no label yet."
  - Disagreement: "You and the second reviewer disagree on 9 records. Settle them before you lock."

## 4 Screening and outputs

### Screening
- **What this step is for:** an AI model reads each record's title and abstract and decides include, exclude or
  maybe, following the rules you wrote (the codebook).
- **What to do now:**
  1. Build the screening instructions from your codebook.
  2. Try them on your labelled sample first and look at the mistakes.
  3. When the mistakes are acceptable, screen the whole corpus.
  4. Choose the result you will report (**Use for analysis**).
- **Done when:** the corpus is screened and one result is chosen for analysis.
- **Watch out:**
  - An online model costs money and sends titles and abstracts to the service. WIDER shows the estimated cost and
    asks first.
  - Do not tune the instructions on the sample that measures recall: WIDER warns you if you try.
- **The guide notices:**
  - Many maybes: "More than half of the answers are maybe. Sharper exclusion rules in the codebook may help."
  - The model changed: "This round was started with another model. Start a new round, or go back to that model."
- **Comparing set-ups (Compare configurations):** before screening the corpus you can try several models, prompt
  versions or temperatures on your locked labelled sample, each a few times. WIDER shows, for each set-up in the
  order you chose, how many included papers it missed, how many it wrongly included and how often its answers
  stayed the same. It never names a winner: you decide which mistakes matter. These numbers describe your hand-picked
  sample, not the whole corpus. Lock the sample first; if you unlock it later, the comparison's lists stay visible to
  whoever relabels. This is not the same as comparing two result sets of one screening round. A comparison changes no
  setting: to screen at a temperature you found, add a backend with that temperature.
- **Two different models (a backend per pass):** the two independent readings and the tie-breaking third read can each
  use their own model. Pick them under "Backends of the other passes" on the Screening page for one round, or set
  the defaults on Settings. If two readings use the same model, WIDER says so once: their readings are then not
  independent. Each online model asks for your consent before it is used.

### Recall and report
- **What this step is for:** WIDER estimates how many of the relevant papers the review found (recall) and writes
  what you need to report it.
- **What to do now:**
  1. Press **Estimate recall**.
  2. Build the PRISMA diagram and the methods paragraph, and edit the paragraph.
  3. Export the included records to your reference manager.
  4. Build the replication package to share.
- **Done when:** the report is built.
- **Watch out:** the share bundle leaves out abstracts and the model's raw answers, so it can be shared.
- **The guide notices:**
  - No locked sample: "Recall needs a locked, labelled sample. Finish Samples and labelling first."

## Other pages

### Questions
- **What this step is for:** when WIDER is unsure, it asks you instead of guessing. Your open questions are here.
- **What to do now:** answer each question, or dismiss it. You can choose to be asked at once or to collect them.

### Notes and tags
- **What this step is for:** all notes and tags you added to records, in one place. Add them in a record's
  window on the labelling and screening tables.

### History
- **What this step is for:** every change you made by hand, with who made it and when. Most changes can be undone.
- **What to do now:** enter your name first, so your changes are signed. To undo, press **Undo** on the row.
- **Watch out:** unlocking a locked search is recorded here, with a warning that the search is no longer
  validated.

### Settings
- **What this step is for:** the models used at each step, the online services you allowed, and the review
  protocol (the rules WIDER follows). Change these only when you know why; every change is recorded in History.

## Word list (first entries)

The older words `scripts/list_messages.py` flags on the pages, each with its plain word. What each page word
means is in the glossary of [names.md](names.md#glossary), the one list of meanings.

| In WIDER today | In plain words |
|---|---|
| construct, construct workbench | concepts and synonyms |
| benchmark papers, seed papers | seed papers |
| development set | query development set (explained in the guide) |
| validation set | query validation set (explained in the guide) |
| freeze, frozen | lock, locked |
| unlock (copy a frozen version into a new draft) | unlock |
| used validation paper (the label "Validation, used" or "Val (used)") | used validation paper (already seen: not an independent check) |
| former validation paper (Dev (former val)) | a validation paper moved to development |
| approve | approve (later steps use this version) |
| artifact | (leave out) |
| provider, backend | model |
| probe | check |
| dedup, deduplication | deduplication (author, 17 Oct: keep, it only removes duplicates) |
| enrich, thin abstract | missing abstracts, short abstracts |
| anchors | anchor sentences (author, 17 Oct; one per concept; ranking compares each record with them) |
| top-N | (leave out; under Details) |
| ablation | test without one part |
| oracle | test labels (practice project only) |
| N_h, n_h, weight | (under Details) |
| screening queue | labelling queue |

Open questions for the author:
- Decided (17 Oct): keep "development" and "validation", as "query development set" and "query validation set",
  so they are not confused with the later sample for the ranking and screening models; the guide explains them.
- Decided (17 Oct): "anchor sentences"; a locked search can be unlocked, with the warning above, recorded.
  Built with change `query-refinement-loop` (19 Oct): an unlock is final; whether papers shown early count as seen is
  the researcher's choice (author, 19 Oct: "fully inform the researcher but do not control the researcher").
- "Lock" for freeze: is the word clear?
- Decided (17 Oct): a locked sample can be unlocked the same way, with a warning that the recall estimate is no
  longer blind, recorded.
- Not in WIDER today: an OpenAlex key. Lookups send only the contact address (WIDER_MAILTO) to OpenAlex and
  Crossref; whether OpenAlex now limits free use per day needs checking against its current rules before the guide
  says so.
