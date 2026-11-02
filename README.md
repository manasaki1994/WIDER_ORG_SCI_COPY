# WIDER

**A literature review tool for topics that are studied under many names.**

Some review topics are hard not because there are few papers, but because the same idea is studied under dozens of
names, in several fields, and no single keyword finds it. WIDER helps you review such a topic step by step:

- it helps you write the concepts, synonyms and search strings, and checks them on papers you already know belong
  (your **seed papers**);
- it imports the database exports, removes duplicates and ranks the records by how close they are to your concepts;
- you label a random sample by hand, an AI model screens every record, and WIDER estimates **recall** (the share of
  relevant papers the screening kept) with a 95% interval;
- it writes the report, the PRISMA 2020 flow diagram, a methods paragraph and a replication package.

You decide at every step; the AI model only proposes. Every version, decision and model call is stored in the
project folder, and the screening can be replayed without a network connection.

WIDER is for researchers who run systematic or scoping reviews. You can use it in a **web page on your own computer**
or from the **command line**: every page has matching commands.

**Contents:** [What you need](#what-you-need) · [Install](#install) · [Start WIDER](#start-wider) ·
[Try it on the invented example](#try-it-on-the-invented-example) · [A review, step by step](#a-review-step-by-step) ·
[Set up the AI models](#set-up-the-ai-models) · [What leaves your computer](#what-leaves-your-computer-and-when) ·
[Troubleshooting](#troubleshooting) · [Running the tests](#running-the-tests) · [Read more](#where-to-read-more)

## What you need

- **Python 3.11 or newer** and a terminal (macOS, Linux or Windows).
- **An AI model** for writing proposals and for screening. Any one of these works:
  - a model on your own computer, for example [Ollama](https://ollama.com) (free, nothing leaves the computer);
  - an API key for a service such as OpenAI or Anthropic (paid per use);
  - a command-line tool you already use, such as Claude Code or Codex.

  You do not need a model to try the buttons: the invented example runs on a **test model** that costs nothing
  (its answers mean nothing).
- **For a real review:** your research question, a few seed papers, and access to the databases you will search
  (for example Scopus, Web of Science, EBSCOhost).

## Install

Open a terminal in the WIDER folder (the folder that holds this README) and run:

```bash
python3 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

On Windows, the second command is `.venv\Scripts\activate`. `pip install -e .` (without `[dev]`) is enough for
your own review; the invented example below and the tests need `dev`, which adds the test tools and the PDF reader.

Check that it worked:

```bash
wider --version
```

You should see `wider 0.0.1`. Run `source .venv/bin/activate` again in every new terminal before you use `wider`
(or call it as `.venv/bin/wider`).

**Optional extras** (add them inside the brackets, for example `pip install -e ".[dev,local]"`):

| Extra | What it adds | When you need it |
|---|---|---|
| `local` | sentence-transformers and torch | the default ranking model (`bge-m3`), which runs on your computer |
| `import` | openpyxl, xlrd, bibtexparser | importing Excel (XLS, XLSX) or BibTeX exports |
| `pdf` | pypdf | reading seed-paper PDFs (already part of `dev`) |
| `litellm` | LiteLLM | reaching a model through LiteLLM |

## Start WIDER

### In your browser

```bash
wider ui --dir ~/reviews/myreview
```

WIDER prints an address and opens it in your browser: `http://127.0.0.1:8765`. The page runs only on your own
computer; nobody else on the network can open it.

- `--dir` is the project folder: a folder made by `wider init` (it holds `project.yaml`), or any folder inside it.
  Leave `--dir` out to use the folder you are in. In a folder that is not a project, `wider ui` stops with
  an error that ends in `no project.yaml found`, so for your **first** project run `wider init` first
  ([step 0](#0-create-a-project)). After that, the **Home** page opens your recent projects, opens another
  project folder, or starts a new project.
- `--port 8790` uses another port if 8765 is taken. `--no-open` starts the server without opening the browser.
- Stop the server with **Ctrl+C** in the terminal.

The first time, WIDER asks **your name**. Your decisions, labels and notes are signed with it. There is no login:
anyone at this computer can change it.

**How the pages work.** The left sidebar lists the steps in order, and each shows its state (for example
*not started*, *in progress*, *needs attention*, *completed*). Every page starts with **What you can do here now**, and **Explain this step** opens
the guide for that page. **Continue to …** at the bottom takes you to the next step. Help, History, Notes and tags,
Questions, Ask WIDER and Settings and data are open at any time.

### From the command line

Run commands inside the project folder, or add `--dir PATH` to any command. These three tell you where you are:

```bash
wider status      # the project and every step's state
```

```bash
wider next        # only the next command to run
```

```bash
wider guide rank  # the guide for one page (here: Ranking)
```

`wider --help` lists every command, and `wider COMMAND --help` explains one. `python -m wider` works as well.

## Try it on the invented example

WIDER comes with an **invented** example topic (employee voice and silence): 89 invented records exported from three
made-up databases (`alpha`, `beta`, `gamma`), with planted duplicates, six invented seed papers, and ready-made
concepts, synonyms and codebook. There are three ways to try it:

| Way | What you need | What you get |
|---|---|---|
| [A. The online demo](#a-the-online-demo) | a web browser; nothing to install | a guided tour of a finished review |
| [B. Step by step on the example files](#b-step-by-step-on-the-example-files) | WIDER installed; a model only if you want real answers | you press every button yourself |
| [C. A finished example project](#c-a-finished-example-project) | WIDER installed and a model | a project with results on every page |

### A. The online demo

Open [wider-showcase.vercel.app/demo](https://wider-showcase.vercel.app/demo/) in your browser. It is a guided tour
of a whole review on the invented example, with nothing to install. The **Explore the demo** button on WIDER's
**Home** page opens the same page. Nothing from your projects is sent to it.

### B. Step by step on the example files

You create a practice project, import the example's database exports and go through the pages yourself.

**Which model?** You can do every step with the **test model** (the commands below set it up): it costs nothing,
needs nothing installed and sends nothing off your computer, and it takes about 15 minutes. But the test model
only lets you **try the buttons**: its answers mean nothing, and every page that uses it says so. For real answers,
leave out the two `--kind fake` commands in part 2. A new project then uses Ollama with `qwen3:8b` for screening
and `bge-m3` for ranking, so first set them up as in [Set up the AI models](#set-up-the-ai-models)
(`ollama pull qwen3:8b`, the `local` extra and `wider embed download bge-m3`), or choose an API-key service there.
With ready-made concepts, only two steps call a model: **Ranking** (the embedding model) and **Screening** (the AI
model). **Propose with LLM** on Concepts and synonyms and the **Check** button on the Model for query creation page
(one short test call) also call the AI model, if you use them.

**1. Make the example files.** In the WIDER folder, with the virtual environment active:

```bash
python scripts/make_synthetic_example.py ~/wider-try/files
```

This writes the database exports (`inputs/ris/`), the seed papers (`benchmark.csv`) and the ready-made concepts,
synonyms and codebook (`artifacts/`) into `~/wider-try/files`. The exports are not shipped with WIDER, so this step
is needed. Without a folder name the script writes into `examples/synthetic_project/`.

**2. Create a practice project and give it the example's seed papers, databases and test models.**

```bash
wider init ~/wider-try/practice -q "What leads employees to speak up, stay silent or report problems at work?"
```

```bash
cd ~/wider-try/practice
```

```bash
cp ../files/benchmark.csv .
```

```bash
wider query platforms add alpha --syntax scopus
```

```bash
wider query platforms add beta --syntax scopus
```

```bash
wider query platforms add gamma --syntax scopus
```

```bash
wider providers add --name practice --kind fake --model fake --set-default
```

```bash
wider embed add --name practice --kind fake --model fake --set-default
```

```bash
wider prior-reviews policy --none-known
```

The first database you add (`alpha`) is the one the search is developed on; the others check it.

**3. Load the ready-made concepts and synonyms, approve them, and freeze the synonyms.**

```bash
for k in cells codebook synonyms anchors near_miss; do wider construct save $k ../files/artifacts/$k/v1.yaml && wider construct approve $k 1; done
```

```bash
wider construct freeze synonyms 1
```

**4. Import the example's database exports.**

```bash
wider import add alpha ../files/inputs/ris/alpha/alpha_export.ris
```

```bash
wider import add beta ../files/inputs/ris/beta/beta_export.ris
```

```bash
wider import add gamma ../files/inputs/ris/gamma/gamma_export.ris
```

```bash
wider import run
```

You should see `Imported 3 files with 89 records.`

**5. Open it in the browser and do the rest by hand.**

```bash
wider ui
```

Then, in the sidebar:

1. **Deduplication:** press **Run deduplication**. 16 pairs of records need your decision; choose *duplicate* or
   *not duplicate* for each pair, then press **Apply decisions and check again**. (WIDER may also suggest
   **Missing abstracts**; that step is optional and you can skip it.)
2. **Ranking:** press **Embed corpus**, then **Rank**.
3. **Samples and labelling:** first set the **recall target** (for example 0.95, and what you will do if it is
   not met) and press **Save target**; without it the report warns that no target was set. Then press **Draw
   samples from the ranking**: a **validation** sample (it measures recall) and a small **development** sample (for
   trying the screening prompt), about 36 records together. Press **Open queue**, leave **Blind** ticked and
   label every record of both samples (keys **1** include, **2** exclude, **3** maybe; **j**/**k** next and
   previous). Blind mode hides the rank and the model's decisions, so your labels count as independent. To label
   in a spreadsheet instead, use `wider label export` and `wider label import` (see step 9 below).
4. **Screening:** press **Build prompt from codebook**, choose the whole corpus and *protocol A/B + third read*
   (each record is read twice, and a third time where the two readings disagree), and press **Run**.
5. **Recall and report:** press **Estimate recall**, then **Build report**.
6. **Results dashboard** and **PRISMA reporting** show the numbers, the flow diagram and the checklists.

The same steps from the command line are listed in [A review, step by step](#a-review-step-by-step) below
(steps 6 to 12). The example's own notes are in [examples/synthetic_project/README.md](examples/synthetic_project/README.md).

### C. A finished example project

One command builds the invented example into a project and runs the whole review on it: import, deduplication,
ranking, samples, labels, screening and the report. Most pages then have results to look at; on the invented
example the duplicate pairs and the question about earlier reviews stay open for you to decide.

```bash
python showcase/build_project.py ~/wider-try/finished
```

```bash
wider ui --dir ~/wider-try/finished
```

It needs, before you start:

- **An AI model for the screening.** By default Ollama, running, with `qwen3:8b` already pulled (choose
  another with `--model NAME`, written exactly as `ollama list` shows it, for example `llama3.2:latest`; with any
  other spelling WIDER cannot tell the model is local and the script stops at the consent question). Small models
  of about 3B often fail the answer format; use `qwen3:8b` or larger. Or OpenAI: set `OPENAI_API_KEY` and add `--provider openai`; the records are then sent to OpenAI
  and you pay per call. There is no test-model option here, because the point is real screening answers.
- **A ranking model.** `bge-m3` already downloaded (`wider embed download bge-m3`, with the `local` extra). Or add
  `--fake-embeddings` to use the test embedding model: then the ranking means nothing, but nothing needs downloading.

The script never downloads a model. The human labels are made automatically from the example's marker words and
signed "Reviewer A". The folder must not exist yet. The script also writes the example files into a folder beside
it, named like the project with `.example` added (here `~/wider-try/finished.example`), and **deletes and replaces**
any folder of that name. Screening takes about 200 model calls; on a local model that is an hour or more, and
much longer on a slow computer. `--no-screen` stops before the screening. If the script stops halfway, delete the project folder
(or choose a new name) and run it again.

## A review, step by step

The steps below follow the sidebar from top to bottom. For each step: what you do, what you get, and the matching
command. The full guide for every step (the decisions, the defaults and why, what to check before moving on) is
in [docs/guide.md](docs/guide.md).

### 0. Create a project

**You do:** for your first project, create it in the terminal (the command below) and start `wider ui` in its
folder. For later projects you can also press **New project** on **Home** and choose how you start:

- **A research question** (recommended for a new review): WIDER helps you write concepts, synonyms and search
  strings.
- **A search strategy you already have:** you import your search strings and check them on seed papers.
- **Records you already retrieved:** you start from the database exports.

All three lead into the same method; WIDER works out the next step from what the project holds.

**You get:** a project folder with `project.yaml` (the settings), `benchmark.csv` (the seed papers) and
`store.sqlite` (the records, decisions and runs).

```bash
wider init ~/reviews/myreview -q "Your research question?" -p scopus -p wos
```

`-p` adds a database in the order you will search them (`ebsco`, `scopus`, `wos`, `proquest`, `ovid`, `pubmed`,
`gscholar`); the first is the one the search is developed on.

### 1. Review plan

**You do:** five tasks, in any order:

- **Research question:** write it, and add more questions if the review has several.
- **Seed papers:** add papers you already know belong, by DOI, title, CSV or RIS file. WIDER splits them into a
  **development** set (used to build the search) and a **validation** set (kept hidden to test the search once,
  later).
- **Prior reviews:** add the reference lists of earlier reviews on the topic, or say that you know of none.
- **Databases:** choose the databases in your search order.
- **Find more seed papers** (optional): candidates from the papers your seed papers cite and are cited by.

**You get:** the **Plan summary**, and the project is ready for the next step.

```bash
wider benchmark add --doi 10.1234/abcd --set Development
```

```bash
wider benchmark split --apply
```

```bash
wider prior-reviews add FILE
```

```bash
wider query platforms add scopus
```

```bash
wider plan
```

`wider prior-reviews policy --none-known` records that you know of no prior review. Looking seed papers up online
(`wider benchmark resolve`, `wider benchmark chain`) needs your consent first; see
[What leaves your computer](#what-leaves-your-computer-and-when).

### 2. Model for query creation

**You do:** choose the AI model that writes the proposals. Three steps on the page: **where it runs** (CLI, Cloud
or Local), **which model**, and **Check**, which sends one test question and shows whether the model answers.
**Find AI models on this computer** looks for installed tools, key variables and local model servers.

**You get:** a checked model, shown as the project's default.

```bash
wider providers detect
```

```bash
wider providers add --preset ollama --model qwen3:8b --set-default
```

```bash
wider providers probe ollama
```

All the choices are in [Set up the AI models](#set-up-the-ai-models).

### 3. Concepts and synonyms

**You do:** open each tab and press **Propose with LLM** for a draft from the model, or write it yourself. Edit,
save it as a new version, then press **Approve**. The tabs are:

- **cells:** the parts of your topic (for example, kind of behaviour × cause or consequence);
- **codebook:** the inclusion and exclusion criteria the screening will use;
- **synonyms:** the blocks of search terms;
- **anchors:** one short sentence per cell, used for ranking;
- **near_miss:** texts that look relevant but are not.

When the synonyms are ready, press **Freeze**. Freezing makes them the search you run, and it shows the
validation seed papers for the first time. **Unlock** copies a frozen version into a new draft; WIDER records
that.

**You get:** numbered versions of each part. Old versions are never changed; you can compare or bring one back.

```bash
wider construct propose cells
```

```bash
wider construct show cells
```

```bash
wider construct save cells my_cells.yaml
```

```bash
wider construct approve cells 2
```

```bash
wider construct freeze synonyms 2
```

`wider construct list` shows every version and its state. Repeat for `codebook`, `synonyms`, `anchors` and
`near_miss`.

### 4. Search strings

**You do:** copy the string for each database, run it there, and export the results (RIS, CSV, Excel or BibTeX).
Optional checks: **dry run** (does the string find your seed papers?), **lint** (short stems, unquoted phrases,
missing spellings) and a second reading by a model. On the invented example, lint reports multi-word terms that
are not quoted (such as *speaking up*), because the example's made-up databases get no quotes in the rendered
string; you can ignore that there.

**You get:** a search string in each database's own syntax, with what each database cannot express.

```bash
wider query render
```

```bash
wider query lint
```

```bash
wider query dry-run
```

```bash
wider query archive
```

If you already have a search string, `wider query import` checks it and saves it as a synonyms version.

### 5. Search results

**You do:** choose the database, upload its export files, then press **Import the uploaded files**. When every
database is in, mark the uploads final.

**You get:** for each file, a check: number of records, how many seed papers it holds, whether it looks capped or
off topic. **Coverage** shows which seed papers each database found.

```bash
wider import add scopus ~/Downloads/scopus.ris
```

```bash
wider import run
```

```bash
wider coverage
```

```bash
wider import final
```

You can also put the files straight into the project folder, under `inputs/<database>/` (for example
`inputs/scopus/`), and run `wider import run`.

### 6. Deduplication

**You do:** press **Run deduplication**. WIDER merges records with the same DOI, the same title, or a very similar title that
other fields confirm. Pairs it is unsure about wait for you: decide each one, then press **Apply decisions and check again**.

**You get:** the number of unique records, and the counts for the PRISMA flow diagram.

```bash
wider dedup run
```

```bash
wider dedup queue
```

```bash
wider dedup decide 2 37 duplicate --keep A
```

The two numbers are the records of a pair, as `wider dedup queue` shows them. The decision is `duplicate`,
`not_duplicate` or `unsure`; with `duplicate`, `--keep A` shows the first record's title, authors, year, journal
and DOI on the merged record (`--keep B` the second's). Run `wider dedup run` again after your decisions.

### 7. Missing abstracts

**You do:** optional. Fetch abstracts for records that have none or a very short one, from OpenAlex, Crossref,
Europe PMC and Semantic Scholar. This is an online lookup and needs your consent.

**You get:** more abstracts, so ranking and screening have text to read. An existing abstract is never overwritten.

```bash
wider lookups allow
```

```bash
wider enrich
```

### 8. Ranking

**You do:** choose the ranking model (an **embedding model**, which turns text into numbers so that similar texts
are close), press **Embed corpus**, then **Rank**. **Check the seed papers** shows where your seed papers land in the ranking.

**You get:** every record ordered by how close it is to your anchor sentences. Ranking only orders; it removes
nothing.

```bash
wider embed corpus
```

```bash
wider rank run
```

```bash
wider rank validate
```

### 9. Samples and labelling

**You do:**

1. Set the **recall target** and what you will do if it is not met, before you see any result, and press
   **Save target** (`wider validation target`).
2. **Draw samples from the ranking:** a random **validation** sample spread over the ranking (it measures recall), and a small
   **development** sample (for trying the screening prompt).
3. Press **Open queue** and label every record of both samples (include, exclude, maybe). Leave **Blind** ticked
   (it is on by default): it hides the rank, the band, the sample and any model decision. A second reviewer can label the same records blind, and
   disagreements are settled side by side.

**You get:** human labels that the recall estimate compares the screening with.

```bash
wider sample draw both
```

```bash
wider label export validation labels.csv
```

```bash
wider label import validation labels.csv --reviewer me --independent "labelled from the file, model decisions not shown"
```

`wider label export` writes a CSV with an empty `decision` column. Fill it with `include`, `exclude` or `maybe`
and import it. `--independent` states how the labels were made without seeing the model's decisions; labels need
that statement (or the blind queue) to count for recall. For labels you already imported, use
`wider label provenance SAMPLE --reviewer NAME --independent "..."`.
`wider label agreement` and `wider label adjudicate` handle a second reviewer.

### 10. Screening

**You do:** press **Build prompt from codebook**. Optionally screen the development sample first and press
**Evaluate** to compare the model with your labels. Then screen the whole corpus with *protocol A/B + third read*:
each record is read twice, independently, and a third time where the two disagree; the majority decides.

**You get:** include, maybe or exclude for every record, with the model's quoted evidence. **Screening review**
lists the records that need a person.

```bash
wider screen build-prompt
```

```bash
wider screen run sample:development
```

```bash
wider screen evaluate --help
```

```bash
wider screen protocol corpus --name main --estimate-only
```

```bash
wider screen protocol corpus --name main
```

`--estimate-only` prints the expected cost and calls no model. A screening run can be stopped and resumed, and
`wider screen replay` rebuilds the decisions from the stored answers, offline.

### 11. Recall and report

**You do:** press **Estimate recall**, then **Build report**.

**You get:** the estimated recall with its 95% interval and the number of relevant papers behind it, and
`exports/report.md` and `exports/report.json` with every number of the review. **Results dashboard** shows
agreement between the reads, the comparisons and the PRISMA counts.

```bash
wider recall main
```

```bash
wider report --bundle
```

```bash
wider results
```

`--bundle` also writes `exports/share_bundle/`, a copy for sharing: it holds titles and DOIs, decisions, samples,
labels, the concepts and the report, but no abstracts, no exported record files and no raw model answers.

### 12. Outputs for the paper

**You do:** on **PRISMA reporting**, go through the PRISMA 2020 and PRISMA-S items: what WIDER recorded is filled
in, and you write the rest. Then export.

**You get:** the PRISMA 2020 flow diagram, the checklists, a methods paragraph drafted from the project, reference
files for your reference manager, and a zipped replication package.

```bash
wider export prisma
```

```bash
wider export prisma-checklist
```

```bash
wider export prisma-s
```

```bash
wider export methods
```

```bash
wider export references --help
```

```bash
wider export package
```

Full-text screening, data extraction and synthesis are not part of WIDER: the review in WIDER ends with the
title-and-abstract screening and its report.

**Any time:** **History** lists every manual change and undoes it (`wider history list`, `wider history undo ID`);
**Notes and tags** keeps notes and tags on records (`wider notes add`, `wider notes list`); **Questions** lists the points where WIDER is unsure
(`wider questions list`); **Ask WIDER** answers questions about WIDER from its own documents, never from your records
(`wider assistant ask`).

## Set up the AI models

WIDER uses two kinds of model:

- an **AI model** (a language model) writes the proposals and screens the records;
- an **embedding model** ranks the records.

You can choose a different AI model for some steps, for example a cheaper one for screening:
`wider providers stage screen NAME` (stages: `construct`, `screen`, `screen_b`, `screen_judge`, `review`,
`review_2`).

To see every ready-made setup, run `wider providers presets`. To add one, use
`wider providers add --preset ID`, or **Model for query creation** in the browser. WIDER installs nothing itself:
it finds what is already on your computer and tells you where each model sends text and how it bills.

### A model on your computer: Ollama

Nothing leaves your computer, and there is no cost per call.

1. Install Ollama from [ollama.com](https://ollama.com) and start it.
2. Download a model (about 5 GB):

   ```bash
   ollama pull qwen3:8b
   ```

3. In your project, add it and check it:

   ```bash
   wider providers add --preset ollama --model qwen3:8b --set-default
   ```

   ```bash
   wider providers probe ollama
   ```

   You should see the model answer, with `yes` in the column for valid JSON. A new project already lists Ollama with
   `qwen3:8b` as its default, so after `ollama pull` the check alone is often enough.

   WIDER treats an Ollama model as local only when its name matches `ollama list` exactly (`llama3.2:latest`, not
   `llama3.2`). With another spelling it cannot tell where the model runs and asks for consent first.

### llama.cpp, LM Studio or vLLM

Start the server, then add the preset with the model name the server uses.

| Preset | Default address | Start the server with |
|---|---|---|
| `llama-cpp` | `http://127.0.0.1:8080/v1` | `llama-server -m MODEL.gguf --port 8080` |
| `lm-studio` | `http://127.0.0.1:1234/v1` | the LM Studio app's local server |
| `vllm` | `http://127.0.0.1:8000/v1` | `vllm serve MODEL` |

```bash
wider providers add --preset llama-cpp --model MODEL --set-default
```

```bash
wider providers probe llama-cpp
```

Use `--base-url` if the server runs on another port. Any other server that speaks the OpenAI API can be added by
hand: `wider providers add --name NAME --kind openai_compat --base-url URL --model MODEL`.

### A service with an API key

Your text goes to the service, and you pay it per call. WIDER never stores the key itself, only the **name** of the
environment variable that holds it.

1. Put the key in an environment variable, for example in `~/.zshrc` or `~/.bashrc`:

   ```bash
   export OPENAI_API_KEY="sk-..."
   ```

2. Open a **new** terminal (so it has the variable), start WIDER from it, and add the preset:

   ```bash
   wider providers add --preset openai --set-default
   ```

3. Allow WIDER to contact it, then check it:

   ```bash
   wider providers allow openai --for probe
   ```

   ```bash
   wider providers probe openai
   ```

| Preset | Environment variable | Note |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | |
| `anthropic` | `ANTHROPIC_API_KEY` | |
| `openrouter` | `OPENROUTER_API_KEY` | |
| `gemini` | `GEMINI_API_KEY` | |
| `azure-openai` | `AZURE_OPENAI_API_KEY` | also needs `--base-url` |
| `mistral` | `MISTRAL_API_KEY` | |
| `groq` | `GROQ_API_KEY` | |
| `together` | `TOGETHER_API_KEY` | |
| `jev` | `TYPESAFE_API_KEY` | screening only; it cannot write proposals |

Before WIDER sends review text (the codebook and, per call, one record's title and abstract), it asks once:
`wider providers allow NAME --for research`. Other operations have their own consent: `models` (list the models),
`probe` (the test question), `passages` (passages of seed PDFs) and `assistant` (Ask WIDER).
`wider providers consent` shows what each model may do, and `wider providers revoke NAME --for OPERATION` takes it
back. In a terminal WIDER asks yes or no; without a terminal it stops and says which command to run.

### A command-line tool you already use

| Preset | Sign in with |
|---|---|
| `claude-code` | your Claude plan, or `ANTHROPIC_API_KEY` |
| `codex` | `codex login`, or `OPENAI_API_KEY` |
| `gemini-cli` (experimental) | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| `antigravity` (experimental) | the tool's own sign-in |
| `custom-cli` | any command you describe with `--command` |

WIDER runs the tool once per record, with no tools of its own and no access to your files. The text goes to the
tool's vendor. Experimental presets need `--accept-experimental`.

### Ranking models (embeddings)

| Preset | Runs | Notes |
|---|---|---|
| `bge-m3` | on your computer | the default; needs the `local` extra and a one-time download |
| `bge-base`, `minilm` | on your computer | smaller and faster |
| `ollama-embed` | in Ollama | run `ollama pull nomic-embed-text` first |
| `lm-studio-embed` | in LM Studio | |
| `openai-3-small`, `openai-3-large` | OpenAI | `OPENAI_API_KEY` |
| `gemini-embedding` | Google | `GEMINI_API_KEY` |

For the default model:

```bash
pip install -e ".[dev,local]"
```

```bash
wider embed download bge-m3
```

```bash
wider embed probe
```

`wider embed download` is the only command that downloads model files. `wider embed probe` embeds three test
sentences and checks that the two related ones score closer than the unrelated one. Add another model with
`wider embed add --preset ID --set-default`; consent works as for AI models (`wider embed allow NAME --for research`).

### Check everything at once

```bash
wider check-backends
```

It checks every model the project uses, with fixed test sentences and no project data.

### Where the settings live

Everything belongs to the project folder, so a project can be moved or archived as one folder.

| Where | What |
|---|---|
| `project.yaml` | research questions, databases, AI and embedding models (key variable **names** only), protocol settings, random seed, consents |
| `benchmark.csv` | the seed papers (`benchmark_id`, `title`, `doi`, `set` = Development or Validation) |
| `inputs/<database>/` | the database exports you imported |
| `store.sqlite` | records, decisions, labels, model calls and history |
| `artifacts/<part>/vN.yaml` | every version of cells, codebook, synonyms, anchors and near_miss |
| `embeddings/`, `runs/` | cached embeddings, and the record of every run |
| `prior_reviews/`, `review-packages/`, `reporting/`, `assistant/` | prior reviews you added, review packages, PRISMA reporting drafts, Ask WIDER conversations |
| `exports/` | report, PRISMA files, search strings, share bundle and replication package |
| `~/.wider/recent_projects.json` | the Home page's list of recent projects (set `WIDER_HOME` to move it) |

**Protocol settings** (sample sizes, bands, decision rules, thresholds) all have defaults. See them with
`wider protocol show`, read about one with `wider protocol explain KEY`, change one with `wider protocol set KEY VALUE`
and go back with `wider protocol reset KEY`. In the browser they are under **Settings and data**. Every setting is
described in [docs/protocol_reference.md](docs/protocol_reference.md).

`WIDER_REVIEWER` sets your name for the command line (otherwise your login name is used).

## What leaves your computer, and when

By default, nothing. Each of the following happens only when you choose it:

- **Calls to the AI and embedding models you chose.** A model on your computer (Ollama, llama.cpp, LM Studio, vLLM,
  `bge-m3`) sends nothing off it. A cloud service or command-line tool receives the text of each call, and WIDER
  asks your consent before the first one. Each model's card says where it sends text.
- **Online lookups** of papers on OpenAlex, Crossref, Europe PMC and Semantic Scholar (for seed papers, prior
  reviews and missing abstracts). They send DOIs and titles only, are **off** until you allow them
  (`wider lookups allow`, or the question at the first lookup), and every request is logged
  (`wider lookups log`). Following citations (`wider benchmark chain`) and checking the search on OpenAlex use the
  development seed papers only; looking up seed papers and fetching their abstracts covers all of them. Counting
  search terms on OpenAlex has its own switch: `wider lookups terms allow`.
- **Model downloads**, only with `wider embed download` or `--allow-download`.
- **The online demo** (the Home page's **Explore the demo** button) opens a page on the internet; nothing from your
  projects is sent.

Other safeguards:

- The web page listens on `127.0.0.1` only.
- The project's `.gitignore` keeps the imported files, prior reviews, the store, embeddings, runs, exports, review
  packages, reporting drafts, Ask WIDER conversations and `.env` out of version control.
- The share bundle (`wider report --bundle`) leaves out abstracts, exported record files and raw model answers.
- Shared files name researchers by pseudonym (`wider researchers list`).
- If you keep a project or WIDER itself in git, `python3 scripts/check_no_private_data.py --install` adds a
  pre-commit check that refuses review data.

## Troubleshooting

| What you see | What to do |
|---|---|
| `command not found: wider` | Activate the environment: `source .venv/bin/activate` (or use `.venv/bin/wider`). |
| The browser page does not open, or the port is in use | Start with another port: `wider ui --port 8790`, and open the printed address. |
| The start line is printed, then `address already in use` | The server did not start, because another program uses the port. Start with another port: `wider ui --port 8790`. |
| A model card says `OPENAI_API_KEY MISSING` | The variable is not set in the terminal that started WIDER. Set it, open a new terminal, and start `wider ui` again. |
| `cannot reach` when checking Ollama | Start Ollama, then run `ollama list` to see the model names. |
| The check fails with a timeout | Large local models are slow at first; try again, or raise the timeout with `wider providers edit NAME --timeout 3600`. |
| WIDER stops and asks for consent | It is about to send something off your computer. Read what it would send, then allow it (`wider providers allow …`, `wider lookups allow`) or choose a local model. |
| `wider next` keeps saying `wider dedup` | Pairs wait for your decision: `wider dedup queue`, then `wider dedup decide …`, then `wider dedup run`. |
| `wider next` keeps saying `wider enrich` | Fetching missing abstracts is optional and needs your consent for the online lookup. You can skip it and go on with ranking. |
| Screening the development sample is refused | Label every record of the development sample first. |
| Recall is refused: the labels have no stated independence | Label in the blind queue, or declare imported labels: `wider label provenance SAMPLE --reviewer NAME --independent "how"`. |
| Excel or BibTeX import fails | Install the extra: `pip install -e ".[dev,import]"`. |
| You changed something by mistake | **History** lists every manual change; `wider history undo ID` takes it back. |

## Running the tests

From the WIDER folder, with the `dev` extra installed:

```bash
pytest
```

```bash
pytest -n auto
```

The second runs the tests in parallel. The long simulation checks are left out by default; run them with
`pytest -m slow`. `ruff check .` checks the code style. The end-to-end test `tests/e2e/test_synthetic_pipeline.py`
runs a whole review on the invented example with the test model.

## Where to read more

- [docs/guide.md](docs/guide.md): the full guide for every step: what you decide, the defaults and why, what to
  check before moving on, and the page and commands. `wider guide PAGE` prints one page's part.
- [docs/names.md](docs/names.md): every page's name, address and commands, and a glossary.
- [docs/plain-words.md](docs/plain-words.md): the words WIDER uses and what they mean.
- [docs/protocol_reference.md](docs/protocol_reference.md): every protocol setting with its default, generated from
  the code (`python scripts/build_docs.py`).
- [docs/features.md](docs/features.md): what WIDER does, feature by feature.
- [docs/technical_appendix.md](docs/technical_appendix.md): the methods and statistics in detail.
- `wider guide methods`: the methods explained, in the terminal.

## Licence

MIT. See [LICENSE](LICENSE).
