# Glossary

## recall

synonyms: sensitivity; share of relevant papers found

The share of all relevant papers the screening keeps, estimated from the validation sample's human labels; by default a paper counts as kept when the model said include or maybe. The Recall page names two others: search recall (validation seed papers the frozen search finds) and recall on independent papers (relevant papers found outside the search that it also found).

## precision

synonyms: positive predictive value

The share of the records a screening keeps that are relevant. WIDER does not report it as a headline; the recall setting named precision is something else, the planned margin.

## prevalence

synonyms: relevant rate; share of relevant records

How common relevant papers are among the records, overall or within one band. Deep in the ranking relevant papers are rare, which is why those bands need many labels before the recall bounds become narrow.

## relevant paper

synonyms: relevant record

A record that belongs in the review by the codebook's criteria. For recall, relevance is what the human labels say, not what the model said.

## record

synonyms: reference; search result

One entry of a database export: title, abstract, authors and other details of one paper. After deduplication each paper is one unique record, and every later step works on these.

## seed paper

synonyms: benchmark paper; known paper

A paper you already know belongs in the review. Seed papers are split into a query development set, used to build the search, and a query validation set, kept apart to check the frozen search.

## query development set

synonyms: development seed papers; search-query development set

The seed papers the search strings, the concepts and the ranking may be tuned on. The model proposals and the dry run see only these papers.

## query validation set

synonyms: validation seed papers; search-query validation set

The seed papers kept hidden until the search is frozen, then used to check whether the search and the ranking find them. When the search is reopened after some were shown, you choose whether they count as used, so they no longer give an independent check, or still count as validation; every output states your choice.

## freeze

synonyms: frozen; final search

Marking the approved synonyms version as final so later steps use it. Freezing fixes the search you ran and opens the query validation set; the search pages call it frozen, and Unlock reopens it.

## lock (a sample)

synonyms: locked sample

Fixing a sample. Locking fixes which records are in it, and from then on a change to a saved label in it is a correction that needs a reason, recorded in History.

## unlock

synonyms: reopen

Copying the frozen synonyms version into a new draft to edit it again; a locked sample is unlocked by name. An unlock is recorded in History and cannot be undone. If validation seed papers were shown before it, you choose whether they count as seen or stay validation; the choice is final and every output states it.

## search string

synonyms: query; Boolean query

The text you paste into a database's search box. WIDER builds one per database from the approved synonyms, usually joining the synonyms of a concept with OR and the required concepts with AND.

## concept

synonyms: synonym block

One part of the research question that a record must mention to be found, such as the population or the phenomenon. Each concept gets its own list of synonyms in the search.

## synonym

synonyms: search term

A word or phrase that names a concept, including spelling variants and truncated forms. The synonyms of all concepts make up the search strings.

## cell

synonyms: construct cell; facet

One facet of the phenomenon the review is about. Each cell has an anchor sentence for the ranking, and the development sample draws likely includes per cell.

## anchor sentence

synonyms: anchor

One sentence per cell that states the aims of a typical relevant paper, without methods or results. The ranking compares every record with these sentences.

## codebook

synonyms: screening criteria; criteria

The written criteria a record must meet to be included, element by element, with grades. The screening prompt is built from it, and every label records the codebook version it was made under.

## near miss

synonyms: near-miss description

A description of a nearby literature that looks relevant but is not. Near misses feed the development sample and the screening evaluation, where a prompt usually fails first. An adjacent literature is something else: a neighbouring field you name on its own, with its own pool in the development sample.

## boundary case

synonyms:

A record that scores high both on a cell and on a near-miss or adjacent-literature description. The development sample draws a few at random so the prompt meets the hardest decisions.

## deduplication

synonyms: duplicate removal

Finding records that are the same paper exported from several databases, so each paper is screened once. WIDER merges only on matching identifiers or corroborated details and leaves doubtful pairs for you to decide.

## research stream (cluster)

synonyms: research area; topic

A group of similar records found by clustering the top of the ranking, described by its most distinctive terms. Clusters give the development sample topical coverage and split the comparison of configurations by area.

## embedding

synonyms: vector

A list of numbers a ranking model computes from a record's title, abstract and keywords, so that similar texts get similar numbers. WIDER embeds each record once per model and keeps the result.

## ranking

synonyms: similarity ranking

The order of all records by how close each one is to the anchor sentences. It sorts and never filters: every record stays in the corpus, and the samples are drawn along the ranking.

## band (stratum)

synonyms: rank band; score band; stratum

A stretch of the ranking, such as ranks 101 to 300, sampled on its own; statisticians call it a stratum, and the pages call it a band. Each band has its own sample size, so the recall estimate can give records from sparse deep bands their proper weight.

## census

synonyms:

A band in which every record is labelled rather than a sample. By default the top band of the ranking is a census, so it adds no sampling uncertainty.

## development sample

synonyms: screening development set; purposive sample

The records chosen on purpose to tune the screening: likely includes per cell, near misses, boundary cases, cluster examples, thin abstracts and a few clear excludes. It must be fully labelled before any model output on it is shown.

## validation sample

synonyms: screening validation set; probability sample

A random sample drawn band by band from the whole ranking, labelled by people and never used for tuning. The recall of the final screening is estimated on it.

## systematic sample

synonyms: systematic draw

A draw that takes every so many records along the ranking from a random start. WIDER draws each sampled band this way and analyses it as a simple random sample within the band.

## inclusion probability

synonyms: selection probability

The chance that a record of a band is drawn into the validation sample: the records sampled from the band divided by all records of the band.

## weight

synonyms: sampling weight; band weight

How many records of its band one labelled record stands for: all records of the band divided by its labelled records. A relevant paper found deep in the ranking, where few records are sampled, counts for more.

## estimate

synonyms: point estimate; recall estimate

The single best figure for recall, combining the bands with their weights. It is shown only once every sampled record has a final label, and always with its interval and the number of relevant papers it rests on.

## confidence interval

synonyms: interval

The range of recall values that fit the labels seen, at the chosen confidence level. WIDER's primary interval is approximate and errs on the wide side; when a band shows no relevant paper it gives no interval and says why.

## confidence level

synonyms: interval level

How often, over repeated samples, intervals made this way contain the true value. It is a protocol setting, and every interval and its label follow it.

## margin

synonyms: planned margin; margin wanted; planned precision

The distance you plan for between the recall estimate and its lower bound. WIDER sets none by default; the planner uses it, if given, to compare how many labels each band needs.

## recall target

synonyms: target

The recall the screening should reach, set before any recall result is seen, together with what you will do if it is not met. The outcome is target met, target not met or inconclusive.

## anytime-valid bound

synonyms: exact bound; confidence sequence

A recall bound that stays valid however often you look and add labels. WIDER uses these exact bounds for sampling without replacement to judge the recall target, at the price of needing more labels than a single-look interval.

## look

synonyms:

One time a recall result on the validation sample is seen. Every look is recorded, so outputs can say whether a target, a change or an extension came before or after a result was seen.

## LLM configuration

synonyms: configuration; screening configuration

A language model together with a prompt version and a temperature. Configurations are compared on the development sample, and the chosen one screens the whole corpus.

## prompt

synonyms: screening prompt

The instructions sent to the model with each record, built from the approved codebook. It asks for a grade per element with a quote from the title or abstract, and each prompt version is kept.

## pass

synonyms: read; independent read

One reading of every record by the model, in its own call. By default two passes read each record independently, so a decision does not rest on one answer of a model that varies.

## third read

synonyms: tie-break; blind third read

An extra pass with its own seed, made only where the first two passes disagree. Its answer settles the decision by majority.

## majority

synonyms: majority vote

The number of agreeing reads a decision needs. When three reads all differ there is no majority and the record becomes maybe.

## maybe (uncertain)

synonyms:

The decision between include and exclude, for a label or a screening result. Maybes go on to a second screen or to a person; Unsure in deduplication is a different thing.

## label

synonyms: human label

A person's decision on a sampled record: include, exclude or maybe, signed with the reviewer's name. Labels are the reference the model is measured against.

## reviewer

synonyms: labeller; screener

A person who labels records or decides what the screening left open. Every label and decision carries the reviewer's name, and one or two reviewers can label each development record.

## agreement

synonyms: Cohen's kappa; kappa

How often two reviewers, or two model passes, gave the same decision, reported with Cohen's kappa, which corrects for agreement by chance.

## adjudication

synonyms:

The final, signed decision on a record where two reviewers disagreed, with a reason. Unadjudicated disagreements are named in every estimate and evaluation that uses them.

## blinding

synonyms: blind labelling

Hiding what could sway a decision. The labelling queue is blind by default: it shows no model output, sample or rank, and unticking Blind shows rank, band and sample, never model output; the validation seed papers stay hidden until the search is frozen; and model output on the development sample stays hidden until its labels are final.

## consent

synonyms: consent to send

Your yes before WIDER sends anything to a model that is not on this computer. It is asked once per model and kind of use, remembered with your name, asked again when the destination changes, and can be revoked.

## local and cloud backend

synonyms: backend; provider; local network backend

Where a model runs: on this computer, on another computer of your network, or online. Only a model on this computer keeps titles and abstracts here; the others need consent.

## provenance

synonyms: audit trail

The record of who did what and when: every change in History signed and timed, every version kept with its origin, and every model call stored. It lets a reader rebuild and audit the review.

## protocol

synonyms: review protocol; screening protocol

Three things carry this word. The review protocol is the method settings in Settings and data, fingerprinted in every output; a screening protocol is one named screening run, such as "Protocol main". PRISMA item 24b asks where the plan written before the review can be read: WIDER adds the settings fingerprint, and the published protocol's location is yours to add.

## PRISMA

synonyms: PRISMA 2020; flow diagram

The reporting guideline for systematic reviews and its flow diagram of records found, removed, screened and included. WIDER fills the diagram's counts from the project.
