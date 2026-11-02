# How often does the validation sample give a recall interval? Simulation evidence

Run 29 Oct 2026 (simulation only; nothing in WIDER was changed). Synthetic corpora only (numpy, fixed seeds): no project,
no review data, no network. The corpus, ranker and screen follow the stratified-vs-random study of 28 Oct 2026, which
found that WIDER's stratified validation sample gives a recall interval in only 2–48% of samples where a simple
random sample gives one in 79–100%.

## Set-up

- Corpus: N records (20,000 or 5,000) at prevalence 0.5%, 2% or 5%; a binormal ranker with AUC 0.90.
- Screen: keeps each relevant record with probability r (0.90 or 0.95), misses either random or rank-linked
  (correlation 0.5); it also keeps 5% of the irrelevant records.
- Each cell pools both kinds of miss and both values of r (4 settings × 200 repetitions = 800 samples per row).
- Interval: WIDER's shipped Korn–Graubard interval (`wider.recall.uncertainty.stratified_uncertainty`) with the band
  rule unchanged (no interval when a sampled band shows no relevant record). The exact bounds valid at every look
  (`wider.recall.anytime.recall_bounds`) are shown for comparison.
- Designs, all at the same total of labels:
  - **shipped**: WIDER's default bands S1 1–100 (census), S2 101–300, S3 301–1000, S4 1001–3000, S5 3001–end; the
    other labels split 70:60:46:40.
  - **fixed**: the same bands, split 20:26:43:127 (the square-root split at 20,000 records, kept as fixed numbers).
  - **sqrt / prop / ropt**: the same five bands, the sampled labels in proportion to √N_h, to N_h, or by the
    planner's recall-optimal rule with its default rate guesses (at least 20 per band for sqrt and prop).
  - **3band**: S1 1–100 census, S2 101–1000, S3 1001–end; split by √N_h, at least 20.
  - **top+rest**: S1 1–100 census and one band 101–end with every other label.
  - **srs**: one band over the whole ranking (a simple random sample).

## Results

"Interval given" is the share of samples where the Korn–Graubard interval is shown. Median width and coverage are
over those samples only. "False lower ≥ 0.90" is the share of all samples whose lower bound is at least 0.90 while
the true recall is below 0.90. The 3band and top+rest rows come from a second grid on the same seeds that re-ran
sqrt and srs alongside (their numbers matched the first grid within 2 points; the table shows the first grid's).

| records | prevalence | labels | design | interval given | median width | coverage where given | false lower ≥ 0.90 | exact bounds: median width |
|---|---|---|---|---|---|---|---|---|
| 5,000 | 0.5% | 316 | shipped | 0% | – | – | 0.0% | 0.98 |
| 5,000 | 0.5% | 316 | fixed | 0% | – | – | 0.0% | 0.98 |
| 5,000 | 0.5% | 316 | sqrt | 0% | – | – | 0.0% | 0.98 |
| 5,000 | 0.5% | 316 | prop | 0% | – | – | 0.0% | 0.98 |
| 5,000 | 0.5% | 316 | ropt | 0% | – | – | 0.0% | 0.98 |
| 5,000 | 0.5% | 316 | srs | 82% | 0.85 | 1.00 | 0.0% | 0.99 |
| 5,000 | 2.0% | 316 | shipped | 2% | 0.50 | (0.87, 15 given) | 0.0% | 0.94 |
| 5,000 | 2.0% | 316 | fixed | 1% | 0.49 | (1.00, 11 given) | 0.0% | 0.94 |
| 5,000 | 2.0% | 316 | sqrt | 3% | 0.38 | 0.92 | 0.0% | 0.93 |
| 5,000 | 2.0% | 316 | prop | 3% | 0.43 | 0.96 | 0.0% | 0.93 |
| 5,000 | 2.0% | 316 | ropt | 1% | 0.36 | (1.00, 10 given) | 0.0% | 0.93 |
| 5,000 | 2.0% | 316 | 3band | 49% | 0.41 | 0.98 | 0.0% | 0.88 |
| 5,000 | 2.0% | 316 | top+rest | 95% | 0.47 | 0.98 | 0.0% | 0.82 |
| 5,000 | 2.0% | 316 | srs | 100% | 0.52 | 0.99 | 0.0% | 0.86 |
| 5,000 | 5.0% | 316 | shipped | 8% | 0.34 | 0.95 | 0.0% | 0.86 |
| 5,000 | 5.0% | 316 | fixed | 17% | 0.32 | 0.99 | 0.0% | 0.88 |
| 5,000 | 5.0% | 316 | sqrt | 16% | 0.28 | 0.98 | 0.0% | 0.85 |
| 5,000 | 5.0% | 316 | prop | 17% | 0.31 | 0.99 | 0.0% | 0.86 |
| 5,000 | 5.0% | 316 | ropt | 13% | 0.26 | 0.99 | 0.0% | 0.85 |
| 5,000 | 5.0% | 316 | 3band | 85% | 0.26 | 0.98 | 0.0% | 0.73 |
| 5,000 | 5.0% | 316 | top+rest | 100% | 0.32 | 0.99 | 0.0% | 0.65 |
| 5,000 | 5.0% | 316 | srs | 100% | 0.31 | 0.98 | 0.0% | 0.59 |
| 20,000 | 0.5% | 316 | shipped | 2% | 0.91 | (1.00, 15 given) | 0.0% | 0.99 |
| 20,000 | 0.5% | 316 | sqrt | 3% | 0.74 | 0.91 | 0.0% | 0.99 |
| 20,000 | 0.5% | 316 | prop | 1% | 0.64 | (0.82, 11 given) | 0.0% | 0.99 |
| 20,000 | 0.5% | 316 | ropt | 2% | 0.66 | (0.83, 18 given) | 0.0% | 0.99 |
| 20,000 | 0.5% | 316 | srs | 83% | 0.90 | 0.99 | 0.0% | 1.00 |
| 20,000 | 2.0% | 316 | shipped | 20% | 0.73 | 0.97 | 0.0% | 0.96 |
| 20,000 | 2.0% | 316 | sqrt | 43% | 0.45 | 0.99 | 0.0% | 0.95 |
| 20,000 | 2.0% | 316 | prop | 37% | 0.44 | 0.97 | 0.0% | 0.95 |
| 20,000 | 2.0% | 316 | ropt | 39% | 0.43 | 0.98 | 0.0% | 0.95 |
| 20,000 | 2.0% | 316 | 3band | 84% | 0.46 | 0.99 | 0.0% | 0.90 |
| 20,000 | 2.0% | 316 | top+rest | 99% | 0.58 | 0.99 | 0.0% | 0.90 |
| 20,000 | 2.0% | 316 | srs | 100% | 0.52 | 0.99 | 0.0% | 0.88 |
| 20,000 | 2.0% | 632 | shipped | 41% | 0.47 | 0.99 | 0.0% | 0.89 |
| 20,000 | 2.0% | 632 | sqrt | 86% | 0.25 | 0.99 | 0.0% | 0.85 |
| 20,000 | 2.0% | 632 | prop | 80% | 0.32 | 1.00 | 0.0% | 0.90 |
| 20,000 | 2.0% | 632 | ropt | 86% | 0.28 | 0.99 | 0.0% | 0.88 |
| 20,000 | 2.0% | 632 | srs | 100% | 0.34 | 0.99 | 0.0% | 0.70 |
| 20,000 | 5.0% | 316 | shipped | 48% | 0.46 | 0.98 | 0.0% | 0.89 |
| 20,000 | 5.0% | 316 | sqrt | 86% | 0.27 | 0.98 | 0.0% | 0.85 |
| 20,000 | 5.0% | 316 | prop | 85% | 0.29 | 0.99 | 0.0% | 0.87 |
| 20,000 | 5.0% | 316 | ropt | 90% | 0.30 | 0.99 | 0.0% | 0.88 |
| 20,000 | 5.0% | 316 | 3band | 99% | 0.28 | 0.99 | 0.0% | 0.75 |
| 20,000 | 5.0% | 316 | top+rest | 100% | 0.37 | 0.99 | 0.0% | 0.72 |
| 20,000 | 5.0% | 316 | srs | 100% | 0.30 | 0.99 | 0.0% | 0.60 |
| 20,000 | 5.0% | 632 | shipped | 79% | 0.25 | 0.99 | 0.0% | 0.75 |
| 20,000 | 5.0% | 632 | sqrt | 100% | 0.18 | 0.97 | 0.5% | 0.64 |
| 20,000 | 5.0% | 632 | prop | 100% | 0.21 | 0.99 | 0.0% | 0.75 |
| 20,000 | 5.0% | 632 | ropt | 100% | 0.20 | 0.98 | 0.4% | 0.68 |
| 20,000 | 5.0% | 632 | srs | 100% | 0.21 | 0.98 | 0.0% | 0.43 |

## What it shows

1. **The number of sampled bands is the main lever, not the interval maths.** Every sampled band that holds few
   relevant records is another chance that its labelled records show none, and then the band rule refuses. At 2%
   prevalence and 316 labels the five shipped bands give an interval in 20% (20,000 records) and 2% (5,000 records)
   of samples; one census band plus one sampled band ("top+rest") gives one in 99% and 95%.
2. **The split over the five bands helps only in large corpora.** Square-root allocation doubles the shipped rate at
   20,000 records (2%: 20% → 44%; 5%: 48% → 86%) and narrows the interval, but at 5,000 records every five-band split
   stays at 1–17%: band S5 (ranks 3001–5000) holds almost no relevant record.
3. **Fewer bands are robust across corpus sizes.** top+rest gives an interval in ≥ 94% at 2% and 5% prevalence for
   both sizes. Its interval is somewhat wider than a five-band or three-band sample where those give one (2%,
   20,000: 0.58 against 0.44–0.46), and about as wide as a simple random sample. Three bands lie between.
4. **A simple random sample gives an interval as often as top+rest**, but keeps no census of the top band, which the
   vision asks for ("more papers are drawn from higher strata").
5. **At 0.5% prevalence no design helps at 316 labels.** About 1–2 relevant records are expected in the sample; the
   banded designs give an interval in 0–3% and the simple random sample's interval, when given, is 0.85–0.90 wide.
   The honest output is "too few relevant records: more labels are needed".
6. **The exact bounds are not a substitute.** They are always given but their median width is 0.82–0.99 at 316
   labels; leading with them would not give a useful interval. They stay what they are: the bounds that judge the
   target.
7. **Nothing here costs coverage.** Where an interval is given it holds the true recall in 0.95–1.00 of samples
   in every row with at least 40 intervals; rows with fewer are noisy (0.82 to 0.91 with 11 to 24 intervals; rows
   with fewer than 20 are shown in brackets). False assurance (lower bound ≥ 0.90 while the truth is below)
   is 0% everywhere at 316 labels and at most 0.5% at 632 labels. The Korn–Graubard refusal rule stays.

## Re-running

From the repository root (2 processes; each grid takes 20–60 minutes on a loaded machine):

```
P=sim/recall-interval-more-often
.venv/bin/python $P/sim.py x --check
.venv/bin/python $P/sim.py $P/raw/main316.jsonl --reps 200 --chunks 4 --procs 2 --budgets 316 --designs shipped,sqrt,prop,ropt,srs
.venv/bin/python $P/sim.py $P/raw/b632.jsonl --reps 200 --chunks 4 --procs 2 --budgets 632 --prev 0.02,0.05 --designs shipped,sqrt,prop,ropt,srs
.venv/bin/python $P/sim.py $P/raw/n5000.jsonl --N 5000 --reps 200 --chunks 4 --procs 2 --budgets 316 --designs shipped,fixed,sqrt,prop,ropt,srs
.venv/bin/python $P/sim.py $P/raw/fewer20000.jsonl --reps 200 --chunks 4 --procs 2 --budgets 316 --prev 0.02,0.05 --designs sqrt,srs,top+rest,3band
.venv/bin/python $P/sim.py $P/raw/fewer5000.jsonl --N 5000 --reps 200 --chunks 4 --procs 2 --budgets 316 --prev 0.02,0.05 --designs sqrt,srs,top+rest,3band
.venv/bin/python $P/analyse.py $P/raw/main316.jsonl > $P/raw/main316.md
```

`analyse.py` prints the full table per setting (kind of miss, r) and per interval (KG, the exact bounds AT, and KG
where given else AT); the `.md` files in `raw/` hold those tables for each grid.

## The brief's candidates, and the verdict

- **(a) Zero-safe bound first** (the exact any-look bound wherever the Korn–Graubard interval is refused, "KG|AT"):
  always gives an interval with coverage 0.98–1.00 and no false assurance, but on the shipped bands its median width
  is 0.85–0.99. It does not give a useful interval; it can only inform.
- **(b) More labels for the lowest bands** (the five bands split by √N_h, by N_h, recall-optimal, or the fixed split
  20:26:43:127): at 20,000 records an interval at 2% / 5% prevalence goes from 20% / 48% (shipped) to 37–44% /
  85–90%; at 5,000 records it stays at 1–17%. It helps only in large corpora.
- **(c) A simple random validation sample**: an interval in 100% of samples at 2% and 5% prevalence at both corpus
  sizes, median width 0.52 (2%) and 0.30 (5%), coverage 0.99, no false assurance. At 0.5% it gives an interval in
  82–83% but 0.85–0.90 wide.
- **Combinations**: (c) with (a) changes nothing at ≥ 2% (the interval is always given). A census of the top 100 plus
  one random band for the rest ("top+rest", between the shipped design and (c)) behaves like (c): 95–100%, width
  0.32–0.58, coverage 0.98–0.99, no false assurance.

Verdict: at 2% and 5% prevalence, (c) and top+rest are clearly better than what ships: the interval is given in
95–100% of samples instead of 2–48%, with coverage and false assurance kept. (a) and (b) are not. At 0.5% no candidate
gives a useful interval at 316 labels.
