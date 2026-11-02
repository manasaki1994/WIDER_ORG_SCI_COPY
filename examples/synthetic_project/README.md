# Synthetic example project

An invented review of **employee voice and silence** (voice, speaking up, whistleblowing, issue selling, upward communication, dissent, employee silence), with adjacent literatures that the same search terms retrieve (participation schemes, grievance procedures, citizenship behaviour, psychological safety). 89 invented records (no real papers, invented authors) on three fake platforms with planted duplicates and 3 thin abstracts, a benchmark set of six invented seed papers and hand-written artifacts (cells, anchors, codebook, near-miss texts). `tests/e2e` runs the whole pipeline on it with the fake provider and fake embeddings.

The RIS files under `inputs/` are not committed (the repository ignores bibliographic exports); regenerate them with `python3 scripts/make_synthetic_example.py`.
