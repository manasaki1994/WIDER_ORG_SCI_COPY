---
id: ranking
title: Ranking records by similarity
context: After deduplication and once the anchor sentences are approved, the ranking orders every record by closeness to your question, and every sample, cluster and benchmark check is drawn from it.
settings: [ranking.top_n_cells, ranking.cluster_top, ranking.cluster_k, ranking.cluster_terms, corpus.thin_words, corpus.max_words]
artifacts: [anchors]
pages: [rank]
---

## In short

WIDER turns each record (title, abstract and keywords) into a vector with an embedding model, a list of numbers that stands for its meaning. It does the same for each approved anchor sentence, one per cell of your question. Every record then gets a score per cell: how close in meaning it is to that cell's anchor. Its overall score is the average of its best {default:ranking.top_n_cells} cell scores, and the ranking sorts all records by it. Nothing is removed; the ranking only decides where effort goes first. You then check where the seed papers land and look at the topic clusters near the top.

## Why

Records that share no keyword with your question can still mean the same thing; embeddings catch that. One anchor per cell, rather than one text for the whole question, lets a record score well only when it matches the parts of the question, and averaging the best few cell scores rewards records that cover more than one cell over records that match a single cell strongly.

The ranking sorts and never filters. A probability sample drawn across the whole ranking (the screening validation sample) then measures how many relevant records sit low, so a weak ranking costs effort but does not hide misses. Records with a very short abstract are still ranked, but they are kept out of that sample, because their score says little.

Clusters of the top records show which topics the ranking pulls in. A cluster without any seed paper may be an adjacent literature; an LLM proposes a name with quotes, quotes that cannot be found in the records are dropped, and you decide.

Ranking again adds a version; samples keep the version they were drawn from. WIDER does not combine the embedding score with keyword scores, does not re-rank with a second model and does not fit a topic model beyond k-means; these ideas are recorded in the vision but not built.

## For auditors

Dense retrieval by cosine similarity. The record text is title, abstract with copyright lines removed and keywords without database check tags, cut at {default:corpus.max_words} words; a record whose abstract has fewer than {default:corpus.thin_words} words is marked thin. Vectors are truncated to the set dimension and L2-normalised, so a dot product is the cosine. The score is the mean of the top {default:ranking.top_n_cells} cell cosines; ranks come from a stable sort. A probe checks that the model scores a related sentence pair above an unrelated one.

The seed-paper check reports median and maximum rank and recall at fixed ranks and percentiles. An ablation compares embedding sets, anchor versions and top-N on that check.

Clusters: k-means with {default:ranking.cluster_k} clusters on the top {default:ranking.cluster_top} records, seeded, with split-half stability as the adjusted Rand index and {default:ranking.cluster_terms} top words each.

Assumptions: the embedding model represents the field's language and the anchors state the question; where either fails, relevant records rank low and only the probability sample shows it.

- `wider/rank/corpus.py:record_text`, `wider/rank/corpus.py:corpus_texts`
- `wider/embeddings/base.py:l2_normalise`, `wider/embeddings/registry.py:probe`
- `wider/rank/score.py:score_matrix`, `wider/rank/score.py:rank`, `wider/rank/score.py:validate`
- `wider/rank/ablate.py:ablation`, `wider/rank/cluster.py:cluster`, `wider/rank/adjacent.py:verify_quotes`
