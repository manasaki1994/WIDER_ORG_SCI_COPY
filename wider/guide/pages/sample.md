# Samples and labelling

## What to do
1. Set the recall target and the rule for a missed target, and press **Save target**, before you see any recall result.
2. Plan the validation sample: give the labels you can afford and press **Plan**, then **Use this allocation**.
3. Press **Draw samples from the ranking** to draw the development sample and the probability (validation) sample.
4. Press **Open queue** and label each record: include, exclude or maybe. Press **Lock sample** when a sample's
   membership is final: from then on a change to a saved label in it is a correction that needs a reason; **Make a review package** for a second reviewer.

## Why it matters
- Human labels on a known design make the model's recall measurable; the development sample is where the prompt is
  tuned on the hardest cases.

## What you need
- A ranking.
- One or two reviewers with time to label blind.

## Done when
- Both samples are drawn and every development record has a final label (under the review protocol for the
  development sample); screening scores the model against the labels.

## Watch out
- The labelling queue is blind by default. Unticking Blind shows each record's rank, band and sample, never the model's output; while Blind is ticked, this page also hides how other reviewers labelled.
