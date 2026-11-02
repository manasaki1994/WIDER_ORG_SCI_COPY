# anchor-ranking Specification

## Purpose
Anchor ranking orders every deduplicated record by its semantic closeness to the construct, so that sampling and screening can concentrate effort where relevant records are likely. It embeds the corpus, scores each record against the anchor texts of the construct cells, checks the ranking against the benchmark papers, and clusters the top of the ranking to find adjacent literatures. In the WIDER procedure it runs after deduplication (stage key `rank`, which requires `dedup`) and before sampling.

## Requirements

### Requirement: Corpus text for embedding
The system SHALL embed each unique record as its title, its abstract with publisher boilerplate removed, and its cleaned keywords, cut to `protocol.corpus.max_words` words (default 3000). Keyword cleaning drops duplicate keywords (case-insensitive) and MeSH-style check tags such as "Adult" or "human".
Surfaces: CLI `wider embed corpus`; UI Anchor ranking page, "Embed corpus" button.

#### Scenario: Boilerplate is stripped from abstracts
- **WHEN** an abstract ends with a database record notice and a publisher copyright line
- **THEN** the embedded text keeps only the findings sentence before those notices
Pinned by: `tests/unit/test_rank.py::test_strip_boilerplate`

#### Scenario: Keywords are cleaned and text is truncated
- **WHEN** the keywords are "Adult; blame; Blame; meetings" and the word limit is 3
- **THEN** the keywords become "blame; meetings"
- **AND** the record text is cut to its first 3 words
Pinned by: `tests/unit/test_rank.py::test_keywords_and_text`

### Requirement: Cached corpus embeddings
The system SHALL store corpus embeddings as `embeddings/<set>.npy` (record ids in `embeddings/<set>.ids.npy`) with a row in `embedding_sets`, and SHALL reuse them without recomputing while the hash of record ids and texts is unchanged. Per-text vectors are cached in `embeddings/cache.sqlite`.
Surfaces: CLI `wider embed corpus [--embedding/-e NAME]`; UI Anchor ranking page, "Embed corpus" (background job).

#### Scenario: Second embedding run is served from the cache
- **WHEN** the researcher embeds an unchanged corpus a second time
- **THEN** the result reports `cached` true and no new vectors are computed
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Empty corpus
- **WHEN** `wider embed corpus` runs before deduplication has produced any records
- **THEN** it fails with the message "no unique records; run wider dedup run first"

### Requirement: Local model download is opt-in
The system MUST NOT download local embedding model weights unless the researcher allows it. Without permission, a local model is loaded from the local cache only.
Surfaces: CLI `wider embed corpus --allow-download`; UI Anchor ranking page, "Embed corpus" asks before downloading.

#### Scenario: Weights are not downloaded
- **WHEN** `wider embed corpus` uses a local sentence-transformers model whose weights are not cached and `--allow-download` is not given
- **THEN** the command exits with status 1 and a message that the model is not downloaded and should be rerun with `--allow-download`
- **AND** the UI embed job returns a skipped result instead of downloading
Pinned by: `tests/unit/test_embeddings.py::test_cached_local_model_loads_offline_unless_download_requested`

### Requirement: Score is the mean of the top-N cell cosines
The system SHALL score each record as the mean of its N highest cosine similarities to the anchor texts of the construct cells. Without a given N it uses `protocol.ranking.top_n_cells` (default 2), capped at the number of cells. A given N outside 1 to the number of cells, or not a whole number, MUST be refused with a message naming the range; no ranking is written.
Surfaces: CLI `wider rank run [--top-n N]`; UI Anchor ranking page, top-N input and "Rank" button.

#### Scenario: Two cells averaged
- **WHEN** a record has cosines 1.0 and 0.6 to two cells and N is 2
- **THEN** its score is 0.8
Pinned by: `tests/unit/test_rank.py::test_score_matrix_top_n`

#### Scenario: Top-N out of range
- **WHEN** ranking with two anchor cells is asked for N 0, -3, 3 or 99999999, through the engine or `POST /api/rank/run?top_n=0`
- **THEN** each is refused with "1 to 2" in the message (HTTP 400 on the route)
- **AND** the number of stored rankings is unchanged
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Top-N that is not a whole number
- **WHEN** `POST /api/rank/run` is called with `top_n` abc, 2.5 or empty
- **THEN** each returns 400 with "whole number" in the message, not a raw validation list
- **AND** the number of stored rankings is unchanged
Pinned by: `tests/unit/test_ui_followup_r11.py::test_rank_top_n_must_be_a_whole_number`

#### Scenario: Default capped at the number of cells
- **WHEN** the anchors have one cell and no N is given, with the protocol default of 2
- **THEN** the ranking uses N 1 and is named `…__top1`
- **AND** an explicit N of 2 is refused with "1 to 1"
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Top-N field on the page
- **WHEN** the top-N field is empty, or holds 2.5
- **THEN** an empty field sends no `top_n` (the default applies), and 2.5 shows "top-N must be a whole number" with no request
- **AND** a whole number such as 0 or -3 is sent, so the server's message names the allowed range
- **AND** the "Rank" button is disabled while the request runs, so a double click sends one run
- **AND** Enter in the field runs the ranking, and a keyboard user who pressed "Rank" has the focus on it again afterwards
Pinned by: `tests/unit/test_static_followup.py::test_rank_page_top_n_empty_uses_the_default_and_a_fraction_is_refused`, `tests/unit/test_static_followup.py::test_rank_keeps_the_keyboard_focus_and_enter_in_top_n_runs_it`, `tests/unit/test_ui_followup_r30.py::test_the_rank_button_sends_one_run_and_gives_the_keyboard_back`

#### Scenario: Two rank runs at once
- **WHEN** two rank runs on the same project start at the same moment (a double click, or the CLI and the page)
- **THEN** both succeed, the project holds one ranking with one row per record, and the rank stage result and the default ranking name that ranking
Pinned by: `tests/unit/test_rank.py::test_two_rank_runs_at_once_both_succeed`

### Requirement: Stored ranking
The system SHALL store each ranking in `rankings` as `<embedding set>__anchors_v<version>__top<N>`, one `ranking_rows` row per record (rank, score, best cell, per-cell scores), and make it the default. A re-rank adds a version and never changes an earlier one (merges still move a merged record's rows); an identical rerun reuses it; samples keep their version. `rank` is done once a ranking exists.
Surfaces: CLI `wider rank run [--embedding/-e] [--anchors/-a VERSION] [--top-n N]`; UI Ranking page.

#### Scenario: Ranking a corpus against two cells
- **WHEN** the corpus is embedded and an anchors artifact with two cells exists
- **THEN** every record receives a rank from 1 to n
- **AND** the result lists both cells in anchors order
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: No anchors
- **WHEN** ranking is requested before any anchors artifact exists
- **THEN** it fails with "no anchors artifact; propose or import one first"
- **AND** a draft anchors version is accepted and its status is noted on the ranking

#### Scenario: A re-rank adds a version
- **WHEN** a synthetic project's samples were drawn from a ranking, 3 records then got abstracts, the embeddings were redone, and the researcher ranks again
- **THEN** a second version of that ranking is stored and becomes the default, the first version's rows are unchanged, and both samples still read their bands and weights from the first version
- **AND** before the run the page says briefly that the development and validation samples keep version 1 (`wider rank run` prints the same note and does not ask), and the Ranking page shows one ranking with "2 versions" under a version history fold
Pinned by: `tests/unit/test_rank_versions.py::test_rerank_adds_a_version`

#### Scenario: New work uses the newest version, old samples keep theirs
- **WHEN** after a re-rank to version 2 the researcher draws a new sample and runs `wider recall` on the old validation sample
- **THEN** the new draw and "Draw a fresh sample" use version 2, while recall and the methods draft give the same figures, anchors and N as before the re-rank
Pinned by: `tests/unit/test_rank_versions.py::test_new_work_newest_old_samples_theirs`

#### Scenario: A new draw keeps the clustering
- **WHEN** the ranking was clustered, then updated to version 2, and a new development sample is drawn
- **THEN** the draw uses the clustering of the base name (topical pool and band order as before) and says it was made on version 1
Pinned by: `tests/unit/test_rank_versions.py::test_new_draw_keeps_clustering`

#### Scenario: An ablation leaves the chosen ranking alone
- **WHEN** `wider rank ablate` runs over combinations that include the chosen ranking's settings, once with unchanged inputs and once after `corpus.thin_words` changed
- **THEN** the default ranking, the ranking the page and new draws use, and its number of versions are unchanged both times; the changed run's result is stored under an ablation-only name
Pinned by: `tests/unit/test_rank_versions.py::test_ablation_leaves_default`

#### Scenario: A re-rank nothing depends on
- **WHEN** no sample was drawn from the ranking and it is ranked again after an input changed
- **THEN** a new version is stored without a confirmation, and nothing names ids or versions on the page unless the history is opened; ranking again with identical inputs reuses the version and says so
Pinned by: `tests/unit/test_rank_versions.py::test_rerank_without_samples`

### Requirement: Validation against the benchmark
The system SHALL report per set the benchmark papers found in a ranking, median and maximum rank, and recall at each depth in `protocol.ranking.recall_at` (default 100, 250, 1000, 3000). The Validation set is included once unlocked (query frozen or uploads final) or when asked (a look, as in coverage; the researcher first chooses if its papers count as seen).
Surfaces: CLI `wider rank validate [--ranking NAME] [--validation]`; UI Ranking page, "Check the seed papers".

#### Scenario: Validation set hidden before the freeze
- **WHEN** the query is not frozen and validation runs without `--validation`
- **THEN** only Development benchmark papers are reported, each with found, rank and best cell
- **AND** with `--validation` and a seen choice the Validation set appears in the summary as well
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Early peek at the validation set
- **WHEN** the Validation set is locked and holds B002 and B003, `validation_after_freeze` is on and the researcher runs `wider rank validate --validation` or `GET /api/rank/validate?validation=true`
- **THEN** without a choice the command prints "Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." and "Choose one: --count-seen or --keep-validation. Nothing was done." and exits 2, and the API answers 200 with `choice_needed: true`, that notice and no Validation rows; nothing is recorded
- **AND** with `--count-seen` or `seen_choice=count` the Validation set is reported, the command prints "validation set shown before the query was frozen; this look is logged in the history. Counted as seen: the 2 validation papers shown no longer validate the search." and the API answers `validation_peeked: true`; with `--keep-validation` or `seen_choice=keep` the line ends "Kept as validation: the 2 validation papers shown still count; every output states this choice."
- **AND** in both branches `validation_peeked_at` is set, the papers shown are recorded with `counted` true or false, and History gets a `validation_peek` row that cannot be undone, one per look
- **AND** on the Ranking page, "Check the seed papers" with the Validation set asked for shows that notice with "Count them as seen", "Keep them as validation" and "Cancel", neither choice pre-selected and the focus on "Cancel"; "Cancel" records nothing
- **AND** once the set is unlocked, asking for it again prints no warning and records nothing
Pinned by: `tests/unit/test_stage_pages.py::test_rank_validate_early_peek_is_warned_and_recorded`, `tests/unit/test_pickers_js_node.py::test_validate_on_benchmark_confirm_step`

#### Scenario: Alternative N compared
- **WHEN** validation runs on a ranking
- **THEN** the report also gives the median rank of the benchmark papers under each N in `protocol.ranking.top_n_compare` (default 1, 2, 3), recomputed from the stored cell scores
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: No ranking yet
- **WHEN** validation is requested before any ranking exists
- **THEN** `wider rank validate` exits 1 and `GET /api/rank/validate` answers 400, both with "no ranking; run wider rank run first"
Pinned by: `tests/unit/test_stage_pages.py::test_validate_without_a_ranking`

### Requirement: Ablation across embeddings, anchors and N
The system SHALL rank and validate each embedding set × anchors version × N in `protocol.ranking.top_n_compare`, with Development (and, if included, Validation; never used papers) median and maximum rank, recall@250 and number found. Including a hidden Validation set is a logged look; the researcher first chooses if its papers count as seen. The default ranking and rank stage result stay unchanged.
Surfaces: CLI `wider rank ablate [--validation]`; UI Ranking page, "Ablation".

#### Scenario: Two values of N
- **WHEN** one embedding set and one anchors version are ablated over N = 1 and 2
- **THEN** two rows are returned, each with a Development median rank that is a number of at least 1
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Nothing to ablate
- **WHEN** no combination can be ranked
- **THEN** `wider rank ablate` prints "nothing to ablate" and exits with status 1
Pinned by: `tests/unit/test_ui_followup_r17.py::test_nothing_to_ablate_is_said`

#### Scenario: Ablation keeps the chosen ranking
- **WHEN** a ranking has been run and chosen, and ablation then ranks other combinations
- **THEN** the default ranking, the explorer and the rank stage result still show the chosen ranking
Pinned by: `tests/unit/test_ui_followup_r17.py::test_ablation_leaves_the_chosen_ranking_alone`

#### Scenario: Ablation with the validation set
- **WHEN** the query is frozen and a ranking is ablated with the validation seed papers included (`wider rank ablate --validation`), one development and two validation papers, one of them not in the corpus
- **THEN** every row carries numbers for the development and validation median rank, maximum rank and recall@250
- **AND** the found columns read "1/1" for development and "1/2" for validation
- **AND** the command line prints the same columns
- **AND** a used validation paper (seen at a counting unlock) is in neither column, and `wider rank validate` lists it under "Validation, used"
Pinned by: `tests/unit/test_ui_followup_r30.py::test_ablation_with_the_validation_set_fills_every_column`, `tests/unit/test_validation_reuse.py::test_used_papers_left_out_of_ablation`

#### Scenario: Before the freeze
- **WHEN** no synonyms version is frozen, the uploads are not marked final and `validation_after_freeze` is on
- **THEN** `wider rank ablate --validation` without a flag prints "Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them as validation: they still count, and every output states that they were shown. The choice is final." and "Choose one: --count-seen or --keep-validation. Nothing was done." and exits 2, and `GET /api/rank/ablate?validation=true` without `seen_choice` answers 200 with `choice_needed` true and that notice but no validation columns; neither records anything
- **AND** with `--count-seen` (exit 0) or `seen_choice=count` (200) both report the validation columns with "validation set shown before the query was frozen; this look is logged in the history. Counted as seen: the 2 validation papers shown no longer validate the search.", the default ranking and the rank stage result are unchanged, History gains a `validation_peek` change that cannot be undone, the package's validity note adds "Validation set looked at on <date> before the freeze (2 papers shown); the researcher chose to count them as seen.", and after a freeze the papers shown are listed under "Validation, used"
- **AND** with `--keep-validation` or `seen_choice=keep` the same columns are reported with "… Kept as validation: the 2 validation papers shown still count; every output states this choice.", and after a freeze the papers stay in the validation set and the note says the researcher chose to keep them as validation
- **AND** after `wider import final` the same command runs and reports the validation columns, with no look notice and no validation_peek entry
- **AND** with v3 unlocked into v4 (counted as seen) and a paper added to Validation, `wider rank ablate --validation` without a flag prints "Validation set requested while the query is reopened; it holds 1 paper." with the choice text and the choose line and exits 2; with `--count-seen` it shows "validation set shown while the query is reopened; this look is logged in the history. Counted as seen: …", appends the paper shown with `choice` `count`, and a later freeze lists it under "Validation, used"; with `--keep-validation` a later freeze reports it in the validation set
Pinned by: `tests/unit/test_stage_pages.py::test_ablation_before_freeze_is_a_logged_look`, `tests/unit/test_stage_pages.py::test_ablation_while_reopened_counts_as_seen`

### Requirement: Clustering the top of the ranking
The system SHALL cluster the top `protocol.ranking.cluster_top` records (default 3000) with k-means into `protocol.ranking.cluster_k` clusters (default 8), seeded with the project seed, and SHALL report a split-half adjusted Rand index plus, per cluster, its size, median rank, top terms and medoid record.
Surfaces: CLI `wider rank cluster [--k K] [--top N]`; UI Anchor ranking page, "Clusters".

#### Scenario: Clustering 30 records into 2 clusters
- **WHEN** the top 30 records are clustered with k = 2
- **THEN** the two cluster sizes sum to 30
- **AND** the split-half adjusted Rand index lies between -1 and 1
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`

#### Scenario: Clusters are kept for later stages
- **WHEN** clustering finishes
- **THEN** each cluster lists `protocol.ranking.cluster_terms` top terms (default 8)
- **AND** the result is stored in the project store meta under `clusters:<ranking>:<k>`

### Requirement: Ranking explorer
The system SHALL let the researcher page through a ranking 50 rows at a time, optionally filtered to records whose best cell is a chosen cell (every cell is offered with its count among the top records, also when 0), showing rank, score and per-cell scores, together with a corpus overview. The page count and the last page follow the filter.
Surfaces: CLI `wider rank show [--ranking] [--cell] [--offset] [--limit]`; UI Anchor ranking page, "Explorer" card.

#### Scenario: First page of the explorer
- **WHEN** the explorer opens on a ranking
- **THEN** the first row has rank 1 and lists its score for every cell
- **AND** the page names the two best cells with their scores in the row, best first, and gives the others in the row's "+N more" hover text; `wider rank show` lists every cell best first
- **AND** the corpus overview reports the number of records
- **AND** `wider rank show` starts with the same corpus line as the page: "<N> unique records (<thin> thin) · embedding sets: <name> <dim>d"
Pinned by: `tests/unit/test_runs.py::test_rank_page_routes_regression`, `tests/unit/test_ui_followup_r10.py::test_explorer_rows_show_every_cell_score`, `tests/unit/test_plain_words_ui.py::test_the_cell_score_column_names_the_two_best_cells`, `tests/unit/test_ui_followup_r11.py::test_explorer_first_page_starts_at_rank_one_with_every_cell`, `tests/unit/test_ui_followup_r29.py::test_rank_show_lists_the_cell_scores_best_first`, `tests/unit/test_ui_followup_r32.py::test_rank_show_prints_the_corpus_line_of_the_page_and_ablate_help_says_beside`

#### Scenario: A cell that is best for no record is still offered
- **WHEN** the anchors have two cells and every top record is closest to the first
- **THEN** the explorer lists both cells in anchor order, the second with 0 records, each "in top N" where N is the pool size, never more than the ranking holds
- **AND** filtering to the second cell shows "no record has <cell> as its best cell" on the page and from `wider rank show --cell`, not "none of 0"
Pinned by: `tests/unit/test_ui_followup_r10.py::test_cell_filter_lists_every_cell_of_the_anchors`, `tests/unit/test_ui_followup_r11.py::test_cell_filter_counts_a_cell_best_for_no_record_as_zero`, `tests/unit/test_ui_followup_r17.py::test_an_empty_cell_filter_says_so`, `tests/unit/test_ui_followup_r30.py::test_a_cell_best_for_no_record_is_listed_in_anchor_order_and_said_on_the_page`

#### Scenario: A ranking smaller than the cell pool
- **WHEN** the ranking holds 6 records and the protocol's cell pool is 50
- **THEN** the pool size is 6 and the cell counts add up to 6
Pinned by: `tests/unit/test_ui_followup_r13.py::test_cell_pool_is_never_more_than_the_ranking_holds`

#### Scenario: Paging stops at the ends
- **WHEN** the explorer shows the first or the last page of a ranking
- **THEN** "Previous page" or "Next page" is disabled, so no page past the ranking is asked for
- **AND** a double click on "Next page" asks for one page, not two
Pinned by: `tests/unit/test_ui_followup_r13.py::test_page_controls_named_and_bounded`, `tests/unit/test_ui_followup_r16.py::test_a_double_click_on_next_asks_for_one_page`, `tests/unit/test_ui_followup_r17.py::test_the_page_and_the_command_line_show_the_same_cell_page`

#### Scenario: Paging one cell's records
- **WHEN** 12 records are ranked, 3 of them best matching `ocean_cell`, and the explorer's route is asked for `ocean_cell` 2 rows at a time (`limit=2`; the page itself asks 50 at a time)
- **THEN** the page, drawing those answers, shows the first page as "1–2 of 3", the count of the filtered records, and the next page, "3–3 of 3", is the last (Next page disabled)
- **AND** `wider rank show --cell ocean_cell --limit 2` prints the same rows and "1–2 of 3 records best matching ocean_cell"; an unknown cell or a project without a ranking exits 1
- **AND** the page route answers an unknown cell with 404 and the same words as the command line, not an empty page
Pinned by: `tests/unit/test_ui_followup_r16.py::test_explorer_counts_the_rows_its_cell_filter_holds`, `tests/unit/test_ui_followup_r16.py::test_rank_show_pages_the_ranking_on_the_command_line`, `tests/unit/test_ui_followup_r17.py::test_the_page_and_the_command_line_show_the_same_cell_page`, `tests/unit/test_ui_followup_r30.py::test_an_unknown_cell_is_refused_on_the_page_as_on_the_command_line`

### Requirement: Candidate clusters for adjacent literatures
The system SHALL select as candidates for adjacent-literature discovery the ranking clusters that contain no benchmark paper and at least `--min-size` records (default 3), each shown with its medoid and top-ranked records (up to 5, abstracts cut to 250 words), in order of median rank.
Surfaces: CLI `wider construct adjacent find [--ranking] [--k] [--top] [--min-size 3] [--max-clusters]`; UI Construct page, Adjacent literatures card, "Find in ranking (LLM)".

#### Scenario: The benchmark cluster is skipped
- **WHEN** three clusters exist and one contains a benchmark paper
- **THEN** two candidate clusters are returned, each with 5 example records and its terms
Pinned by: `tests/unit/test_adjacent.py::test_find_candidates_with_fake_provider`

### Requirement: Verified adjacent-literature proposals
The system SHALL accept a model's adjacent-literature proposal for a cluster only when it is adjacent, not already listed, and at least one of its quotes is verified verbatim in the shown abstracts. Accepted proposals join the list with origin `tool`, status `proposed` and their evidence, and the prompts, responses and candidates are saved under `construct/`.
Surfaces: CLI `wider construct adjacent find [--provider] [--no-merge]`; UI Construct page, "Find in ranking (LLM)".

#### Scenario: Unverified quotes are refused
- **WHEN** two candidate clusters are sent and one answer quotes text that is not in the abstracts
- **THEN** only the proposal with a verified quote is added
- **AND** the other is listed as skipped because no quote was verified
Pinned by: `tests/unit/test_adjacent.py::test_find_candidates_with_fake_provider`

#### Scenario: Repeated discovery does not duplicate
- **WHEN** discovery is run again and the model proposes a literature that is already listed
- **THEN** nothing new is added to the list
Pinned by: `tests/unit/test_adjacent.py::test_find_candidates_with_fake_provider`

### Requirement: Ranking-page clusters use the protocol cluster settings
The system SHALL cluster from the Anchor ranking page with `protocol.ranking.cluster_k` clusters over the top `protocol.ranking.cluster_top` records, exactly as `wider rank cluster` does without options, and the clusters heading MUST show the number of records actually clustered and the k used, never a fixed "top 3000".
Surfaces: UI Anchor ranking page, "Clusters" (`GET /api/rank/cluster`, optional `k` and `top` overrides); CLI `wider rank cluster [--k K] [--top N]`.

#### Scenario: Protocol with smaller cluster settings
- **WHEN** `ranking.cluster_k` is 4 and `ranking.cluster_top` is 500 and the researcher clicks "Clusters"
- **THEN** 4 clusters are returned over the top 500 records
- **AND** the heading reads "Clusters of the top 500 (k=4, ...)"

#### Scenario: Ranking shorter than the clustering depth
- **WHEN** the ranking holds 120 records and `ranking.cluster_top` is 3000
- **THEN** the heading names 120 records
- **AND** the cluster sizes sum to 120

### Requirement: Comparisons above the number of cells are said
The system SHALL compare benchmark ranks for each value of `protocol.ranking.top_n_compare` (each at least 1). A value above the number of cells SHALL be compared as top-<cells> with a note saying so; ablation ranks each capped value once and its row names the values it stands for. Validation tables SHALL show one column per compared value.
Surfaces: CLI `wider rank validate`, `wider rank ablate`; UI Anchor ranking page, "Validate" and "Ablation".

#### Scenario: Comparison above the number of cells
- **WHEN** a ranking with two anchor cells is validated with top-N values 1 and 3, and ablated with 1, 3 and 99
- **THEN** validation notes "top-3 is more than the 2 cells of these anchors, so it is compared as top-2", and the CLI table has a top3 column and no top2 column
- **AND** ablation gives two rows, top-1 and top-2, the second noting "top-3, 99 capped at the 2 cells"
- **AND** a protocol value of 0 is refused
Pinned by: `tests/unit/test_rank.py::test_rank_validate_ablate_cluster`, `tests/unit/test_static_followup.py::test_rank_validation_shows_one_column_per_compared_top_n_and_its_notes`

#### Scenario: Every capped value has its note on the command line
- **WHEN** a ranking with two anchor cells is validated from the CLI with top-N values 1, 7 and 99
- **THEN** the output says both "top-7 is more than the 2 cells of these anchors, so it is compared as top-2" and the same for top-99
Pinned by: `tests/unit/test_ui_followup_r17.py::test_every_comparison_above_the_cells_is_said_on_the_cli`

### Requirement: Embedding picker on the Ranking page
The system SHALL show the embedding models at the top of the Ranking page as picker cards, with add, download, check, set default, edit and remove, the same actions as the command line. The chosen card is the embedding used by "Embed corpus" and becomes the project's default embedding.
Surfaces: UI Ranking page, embedding picker; CLI `wider embed list|add|download|probe|set-default|edit|remove`, `wider embed corpus -e`.

#### Scenario: Choosing an embedding
- **WHEN** a project has the embedding models `e1` (default) and `e2`, and the researcher chooses the `e2` card
- **THEN** `project.yaml` has `default_embedding: e2`, as after `wider embed set-default e2`, and the `e2` card is marked chosen
Pinned by: `tests/unit/test_stage_pages.py::test_embedding_choice_matches_cli`, `tests/unit/test_pickers_js_node.py::test_embedding_picker`

#### Scenario: Weights not downloaded
- **WHEN** the chosen card is a local model whose weights are not downloaded
- **THEN** the card says "not downloaded" and offers "Download", which asks before downloading
- **AND** opening the Ranking page calls no download, probe or detection route
Pinned by: `tests/unit/test_pickers_js_node.py::test_embedding_picker`

#### Scenario: Checking a cloud embedding
- **WHEN** the researcher presses "Check" on a cloud embedding card without `probe` consent
- **THEN** the consent dialog opens first and nothing is sent until the researcher allows it, as on the model page
- **AND** once allowed, only the fixed test sentences are sent, and `wider embed list` shows the new last check
Pinned by: `tests/unit/test_pickers_js_node.py::test_embedding_check_asks_consent_first`

### Requirement: Ablation on the Ranking page
The system SHALL run the ablation from the Ranking page with an "Include the validation set" checkbox, as `wider rank ablate --validation`; while the Validation set is hidden the checkbox stays enabled and says that ticking it is a logged look. Both show the same readable column headers, such as "development median rank" and "validation found", not the stored keys.
Surfaces: UI Ranking page, Ablation; CLI `wider rank ablate [--validation]`.

#### Scenario: Validation set on the page
- **WHEN** the query is frozen and the researcher ticks "Include the validation set" and runs the ablation
- **THEN** the table has validation columns, as `wider rank ablate --validation` has
- **AND** no header contains an underscore
Pinned by: `tests/unit/test_stage_pages.py::test_page_ablation_with_validation`

#### Scenario: Checkbox while the validation set is locked
- **WHEN** no query version is frozen and the uploads are not marked final
- **THEN** the checkbox is enabled and reads "Include the validation set: it is hidden until the query is frozen or the uploads are final; including it now is logged, and you choose whether the papers shown count as seen"
- **AND** running the ablation with it ticked first shows the confirm step with the asking notice and the buttons "Count them as seen" and "Keep them as validation" (focus on "Cancel", which runs nothing), then the validation columns and the same look notice as `wider rank ablate --validation` with the matching flag
Pinned by: `tests/unit/test_stage_pages.py::test_page_ablation_with_validation`, `tests/unit/test_pickers_js_node.py::test_ablation_checkbox_states_the_look`

### Requirement: Benchmark hits by ranking percentile
The system SHALL report per benchmark set, in the ranking check, how many found benchmark papers rank within the top 1%, 5%, 10%, 25% and 50% of the ranking (`protocol.ranking.recall_at_pct`), as "k of n", beside recall at fixed depths, with the same rule for showing the Validation set.
Surfaces: CLI `wider rank validate [--ranking NAME] [--validation]`; UI Ranking page, "Validate on benchmark", benchmark table.

#### Scenario: Percentiles per set
- **WHEN** a ranking has 2000 records, 10 Development benchmark papers are found at ranks 5, 12, 30, 80, 150, 190, 400, 700, 1200 and 1900, and the Validation set is shown with 4 papers at ranks 3, 50, 260 and 990
- **THEN** the Development summary gives top 1% (rank 20) 2 of 10, top 5% 4 of 10, top 10% 6 of 10, top 25% 7 of 10 and top 50% 8 of 10, and the Validation summary 1, 2, 2, 3 and 4 of 4, in `wider rank validate --json`, the CLI table, `GET /api/rank/validate` and the Ranking page alike
- **AND** while the Validation set is hidden only the Development percentiles appear and nothing is recorded
Pinned by: `tests/unit/test_rank.py::test_validate_percentiles_per_set`, `tests/unit/test_ranking_page_node.py::test_validate_percentile_columns` (new; the table is built in `wider/ui/static/app.js`)

### Requirement: Keeping a ranking that needs review
The system SHALL let the researcher keep a ranking whose inputs changed ("Keep this ranking" on Ranking and the Overview, `wider rank keep`): a signed, undoable History change that stores the current input hash as kept for that ranking version, clears "needs review" until the inputs change again, and is named in the methods draft. Nothing is rerun.
Surfaces: CLI `wider rank keep [--version N]`; UI Ranking page and Overview; API `POST /api/rank/keep`.

#### Scenario: Keep and undo
- **WHEN** Ranking needs review on a synthetic project and `wider rank keep` runs as "ana"
- **THEN** `wider status` no longer shows "needs review" for Ranking, History lists "Kept the ranking after the records changed" (kind `ranking_kept`, also in History's "Show" list) signed by ana, and the page and the CLI show the same
- **AND** undoing that change brings the mark back, and a later change to the records marks Ranking again
Pinned by: `tests/unit/test_rank_keep.py::test_keep_and_undo`

#### Scenario: Keeping one of two versions
- **WHEN** versions 1 and 2 both need review and `wider rank keep` runs without `--version`
- **THEN** it exits 1 naming both versions and their samples; `wider rank keep --version 1` keeps version 1 only, as the page's button on that version does
Pinned by: `tests/unit/test_rank_keep.py::test_keep_one_of_two`

#### Scenario: A labelled development sample offers Keep only
- **WHEN** the development sample (labelled) and the validation sample were drawn from a version that needs review
- **THEN** the development sample's box offers "Keep this ranking" only and says a fresh development draw does not exist, while the validation sample's box also offers the existing "Draw a fresh sample"
Pinned by: `tests/unit/test_ranking_page_node.py::test_review_actions_per_sample`

#### Scenario: Nothing to keep
- **WHEN** `wider rank keep` runs while Ranking does not need review
- **THEN** it exits 1 with "the ranking does not need review" and records nothing
Pinned by: `tests/unit/test_rank_keep.py::test_nothing_to_keep`

### Requirement: Updating a ranking
The system SHALL offer "Update ranking", which checks whether the embeddings match the current texts and ranks if they do; otherwise it says what re-embedding sends, where and at what cost, asks consent unless given, re-embeds the changed texts and ranks. A failure adds no version. "Rank with the older embeddings" stays and keeps "needs review".
Surfaces: UI Ranking page and Overview (`POST /api/jobs/rank-update`); CLI `wider rank update [--use-older-embeddings] [--allow-download] [--yes]`.

#### Scenario: Abstracts changed
- **WHEN** 3 records got abstracts after ranking and "Update ranking" is pressed with a remote embedding backend
- **THEN** the page first says the 3 changed texts are sent to that backend, where it runs and the estimated cost, and asks consent unless already given; on Allow it embeds those 3 (the others come from the cache) and adds version 2, which does not need review
- **AND** on Cancel nothing is sent and no version is added
- **AND** in the CLI `--yes` confirms the cost note only; without the backend's consent already given it still refuses before sending
Pinned by: `tests/unit/test_rank_update.py::test_update_reembeds_after_consent`

#### Scenario: Older embeddings and failures
- **WHEN** `wider rank update --use-older-embeddings` runs, and another time the embedding call fails halfway
- **THEN** the first ranks from the older embeddings: when its inputs equal an existing version that version is reused, otherwise a version is added; either way it still needs review and the command says it was made from older embeddings; the second adds no version, keeps the samples and earlier versions unchanged, and says what failed
Pinned by: `tests/unit/test_rank_update.py::test_older_embeddings_and_failure`
