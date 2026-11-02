# Screening

## What to do
1. Press **Build prompt from codebook** to turn the approved codebook into the screening prompt.
2. In the **Start screening** card, choose the model and the scope, then press **Run**.
3. Press **Evaluate** to compare the model with the human labels of the development sample.

## Why it matters
- Each record is screened in its own call that quotes the words behind every grade; an "absent" grade whose quote is
  not in the record becomes unclear, so the model cannot exclude on a hunch.
- Repeated reads show whether the model answers consistently and catch some slips; they do not make a decision
  right, since reads by one model tend to err together.

## What you need
- The approved codebook and a model for screening.
- The labelled samples from Samples and labelling.

## Done when
- The whole corpus is screened by a model and the required steps under **Where screening stands** are done.

## Watch out
- A remote model receives the titles and abstracts; WIDER asks before the first call.
