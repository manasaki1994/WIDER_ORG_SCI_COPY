"""Prior reviews: previous or relevant reviews the researcher knows of.

Their reference lists are parsed **locally** (reference exports, pasted lists, PDFs) into
``prior_review_papers`` and used as a second benchmark (coverage check) and as seeds for
citation chaining. Nothing in this package talks to the network except ``chain.py``, and every
function there first checks the project's opt-in switch (``config.prior_reviews``).
"""
