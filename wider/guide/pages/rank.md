# Ranking

## What to do
1. Press **Embed corpus** to turn every record into a vector with the embedding model.
2. Press **Rank** to score every record against the approved anchor sentences.
3. Press **Check the seed papers** to see where the seed papers land, and **Clusters** for the topics at the top.

## Why it matters
- A ranking lets human and model effort concentrate where relevant papers are dense, while a probability sample over
  the whole ranking keeps the recall estimate honest. It sorts, never filters.

## What you need
- Unique records from Deduplication, ideally with their abstracts.
- Approved anchor sentences from Concepts and synonyms.

## Done when
- A ranking exists.

## Watch out
- Ranking again adds a version; samples keep the version they were drawn from.
- "Needs review" means something the ranking was made from changed after it (abstracts added, embeddings redone, a
  setting). **Update ranking** makes a new version from the current inputs; **Keep this ranking** records that the
  change does not matter for it (in History, undo there). A labelled development sample can only keep its ranking.
