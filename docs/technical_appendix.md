# Technical appendix (method, 28 October 2026)

This appendix describes how WIDER works. The numbers of the case-study replication (identification counts, benchmark
coverage, ranks, sample sizes, screening decisions and recall) and the script that produced them are kept with the
case study's own data, outside this repository, together with the 9 October version of this appendix that quoted them.

## 1. Software

| item | value |
|---|---|
| package | `wider` 0.0.1, Python ≥ 3.11, MIT licence |
| interfaces | CLI (`wider …`), local web UI (`wider ui`, 127.0.0.1 only) |
| storage | one SQLite store per review project (WAL), YAML artifacts with version, status, hash and parent, `.npy` embeddings, JSONL raw model responses |
| model backends | OpenAI-compatible chat APIs, Anthropic Messages API, Ollama, agentic CLIs (templated command, one isolated process per record), fake provider for tests; LiteLLM optional |
| embedding backends | OpenAI `text-embedding-3-large` (Matryoshka truncation to 1024), sentence-transformers (`BAAI/bge-m3`, `bge-base-en-v1.5`, `all-MiniLM-L6-v2`), Ollama, OpenAI-compatible endpoints |
| tests | unit tests, an end-to-end test on a synthetic project, and one golden test on a real search string (gated by `WIDER_CASE_DATA`, skipped without it) |
| privacy | pre-commit guard refuses bibliographic exports, abstract-bearing CSVs and private paths; real data is read from an environment variable and never written into the repository |

## 2. Identification

Database exports are imported per platform and deduplicated by automatic rules (DOI, normalised title, authors,
year and citation fields), each rule counted separately, plus any manual decisions the researcher records.

## 3. Benchmark coverage

Seed papers are located in the deduplicated corpus and in each platform's export. Matching: DOI, then exact
normalised title, then Jaro–Winkler ≥ 0.88 (flagged for confirmation). The validation half of the benchmark is
hidden until the query artifact is frozen.

## 4. Abstract enrichment

Records with fewer than 20 words of abstract are looked up (OpenAlex, Crossref, Europe PMC, Semantic Scholar, then
exact-title search in OpenAlex) after the project's consent; records that stay thin are flagged and kept out of the
probability frame.

## 5. Ranking

Corpus text = title + abstract (database boilerplate stripped) + cleaned keywords, cut at 3,000 words. Score = mean
cosine similarity to the closest *k* cell anchors (default two). Rankings are compared on the development seed papers
by median rank, maximum rank and recall at fixed depths.

## 6. Samples

Probability sample: a stratified draw over score bands (defaults S1 ranks 1–100, S2 101–300, S3 301–1,000,
S4 1,001–3,000, S5 3,001+) from the ranked records minus thin records and benchmark papers, with weight N_h/n_h per
band and a fixed seed. Development set (purposive): likely includes per cell, near misses per category, cluster
medoids and members, thin records and tail records. Labelled sets from an earlier review can be imported and matched
to the corpus.

## 7. Screening

Prompt and schema are rendered from the codebook artifact: element grades with quotes, checks, target fit, edge case,
decision, reason and label groups. A hand-written prompt can be stored verbatim as a second prompt version for exact
replication. Decision rule: absent grade without a verified quote → unclear; exclude without grounds or reason →
maybe; include contradicted by a check → maybe. Protocol: passes A and B, blind third read on disagreement, 2-of-3
majority, three-way split → maybe; second screen of maybes with two reads and a tie-break. Decisions made outside
WIDER can be imported as a protocol of their own.

**Configuration comparisons.** A comparison screens the locked purposive development sample once per configuration (backend, prompt version, temperature) and repeat; repeat *k* sends the backend's seed plus *k* − 1. Each run is scored against the sample's final labels as in `wider screen evaluate`: misses (label include or maybe, model exclude), false includes (label exclude, model include), false maybes (label exclude, model maybe) and agreements, each reported as the lowest and highest value over the repeats, never as a mean or a ranking. Consistency is the share of the records screened in every repeat that got the same decision in each; with one repeat it is not measured. The figures can be split by sampling pool and by research area. Because the pools are chosen to hold hard cases, the figures describe where a configuration fails on that sample, not its accuracy on the corpus, and they never enter the recall estimate. The report, the methods draft and `comparisons.json` in a replication package carry these counts and the settings only; the per-record table `comparison.csv` stays with the project.

## 8. Recall

A screening protocol against the human labels of the validation sample: stratified Horvitz–Thompson ratio with
weights N_h/n_h, bootstrap (default B = 10,000, fixed seed, census stratum fixed) and a linearised interval.

| variant | relevant | kept |
|---|---|---|
| strict | include | include |
| lenient | include | include + maybe |
| cautious | include + maybe | include + maybe |

Bands with no relevant record in the sample are reported with the rule of three, and missed records are listed by
record id in the report.

## 9. Reproducibility

**Phase 2 (9 October 2026).** The procedure is unchanged; what the researcher brings is now explicit and editable: the research question and notes, their own seed papers (entered by DOI or title, resolved against OpenAlex and Crossref, split into development and validation sets), the databases and their roles, the LLM backend (any OpenAI-compatible API, Anthropic, Ollama, LiteLLM, or an agentic CLI such as Claude Code) and the embedding model, and the adjacent literatures that may contaminate the search. Every methodological constant lives in one `ReviewProtocol` model with a description, default and hash (`wider protocol explain`). Database exports are accepted as RIS, CSV/TSV, XLS/XLSX or BibTeX and checked for benchmark hits, query-vocabulary overlap and known export caps before they enter the store. The stage registry carries prerequisites and completion checks, so the UI and `wider next` always name the next command. The synthetic example (`examples/synthetic_project`, 119 invented records on three fake platforms) runs the whole pipeline in under a minute with the fake provider and fake embeddings and is exercised in CI.
