"""What a run sends, beside its button (ui-redesign D17): one builder for the sentence every sending action shows on its
page (``GET /api/send-notice/<action>``) and prints in the CLI before it starts, so the two cannot drift.

The sentence names what is sent, where, why and the consent state ("WIDER asks before sending", or "you allowed this
on <date>; change it in Settings"). Where a model runs comes from :func:`wider.core.backends.backend_place`, the
locality the model cards and Info show, so a backend whose place is not established reads "unknown until checked" and
is never called "this computer". The registry :data:`ACTIONS` is built from the lookup purposes
(:data:`wider.core.lookups.SERVICES`) and every model and embedding call site; ``sites`` names the modules that send
for each action, and a test scans ``wider/`` for model calls, consent checks and lookup clients against it.
This module only describes what the actions already send; it sends nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from . import lookups
from .backend_consent import EMBED_SENT, SENT

if TYPE_CHECKING:
    from .project import Project

SETTINGS = "change it in Settings"


@dataclass(frozen=True)
class Action:
    key: str
    label: str            # the button or command, in the page's words
    kind: str             # lookup | model | embedding
    sent: str             # what is sent
    why: str              # why it is sent
    sites: tuple[str, ...]    # modules under wider/ that send for this action
    cli: tuple[str, ...]      # "module:function" of the CLI commands that print the sentence first
    purpose: str | None = None          # the lookup purpose
    stage: str | None = None            # the stage whose model is used (provider_for), when no backend is named
    operation: str = "research"         # the backend consent operation
    extra: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------------------------------------------- the registry
_LOOKUP_SITES = {"enrich": ("enrich/fetch.py",), "seed_resolve": ("construct/seed.py",),
                 "seed_abstracts": ("construct/benchmark.py",), "prior_resolve": ("prior/chain.py",),
                 "prior_chain": ("prior/chain.py",), "seed_chain": ("seeds/chain.py",),
                 "term_yield": ("search/term_yield.py",), "query_yield": ("search/term_yield.py",),
                 "query_seed_check": ("search/term_yield.py",), "seed_refs": ("seeds/references.py",),
                 "ref_match": ("seeds/references.py",)}
_LOOKUP_CLI = {"enrich": ("cli:enrich",), "seed_resolve": ("cli_setup:benchmark_add", "cli_setup:benchmark_resolve"),
               "seed_abstracts": ("cli:benchmark_fetch",), "prior_resolve": ("cli_prior:prior_resolve",),
               "prior_chain": ("cli_prior:prior_chain",), "seed_chain": ("cli_setup:benchmark_chain",),
               "term_yield": ("cli_rounds:query_yield",), "query_yield": ("cli_rounds:query_yield",),
               "query_seed_check": ("cli_rounds:query_yield",), "seed_refs": ("cli_setup:benchmark_references",),
               "ref_match": ("cli_setup:benchmark_references",)}
_LOOKUP_WHY = {"enrich": "ranking and screening read the abstract",
               "seed_resolve": "each seed paper's record has to be found",
               "seed_abstracts": "the concepts are drafted from the seed papers' abstracts",
               "prior_resolve": "the papers of the prior reviews have to be found",
               "prior_chain": "the papers the prior reviews cite, and those citing them, are looked up",
               "seed_chain": "candidate seed papers are found by citation",
               "term_yield": "the count shows what one term finds",
               "query_yield": "the count shows what the whole query finds",
               "query_seed_check": "the check shows which Development seed papers the whole query finds",
               "seed_refs": "the references of the seed papers are listed from it",
               "ref_match": "a parsed reference is matched to its record"}

ACTIONS: dict[str, Action] = {
    k: Action(k, lookups.PURPOSE_LABELS[k], "lookup", "", _LOOKUP_WHY[k], _LOOKUP_SITES[k], _LOOKUP_CLI[k], purpose=k)
    for k in lookups.SERVICES}
ACTIONS |= {a.key: a for a in (
    Action("construct", "Propose from question (LLM)", "model",
           "your research question, the inclusion and exclusion notes and the titles and abstracts of the Development "
           "seed papers", "the model drafts the concepts, synonyms, anchor sentences and codebook from them",
           ("construct/propose.py", "construct/adjacent.py"), ("cli:construct_propose", "cli_adjacent:adjacent_propose"),
           stage="construct"),
    Action("rank_adjacent", "Find in ranking (LLM)", "model",
           "the titles and abstracts of top-ranked records in clusters that hold no seed paper",
           "the model describes the adjacent literature each cluster may be", ("rank/adjacent.py",),
           ("cli_adjacent:adjacent_find",), stage="construct"),
    Action("search_review", "Read the strings", "model", "the search strings of the chosen version",
           "a second reader checks them for errors", ("search/review.py",), ("cli:query_review",), stage="construct"),
    Action("seed_mine", "Mine with the model", "model",
           "the titles and abstracts of the seed papers, and passages of their PDFs when seed_pdfs.passages_to_cloud "
           "allows it (never the file)", "the model lists the names the seed papers use for the concepts",
           ("seeds/mine.py",), ("cli_seeds:seeds_mine",), stage="construct"),
    Action("seed_references", "Extract (references)", "model",
           "passages of the seed papers' reference lists (never the file)",
           "the model parses the references the PDF route found", ("seeds/references.py",),
           ("cli_setup:benchmark_references",), stage="construct"),
    Action("missed_names", "Names from seed papers", "model",
           "the names the seed papers use that the query misses, with their public metadata",
           "the model sorts which names belong in the query", ("seeds/missed_names.py",),
           ("cli_rounds:query_missed_names",), stage="construct"),
    Action("screen", "Run", "model", "the title and abstract of each record in scope",
           "the model screens each record against the codebook", ("screen/engine.py", "screen/compare.py",
                                                                    "ui/routes_compare.py", "ui/routes_jobs.py"),
           ("cli:screen_run", "cli:_ensure_pass_research", "cli:compare_run"), stage="screen"),
    Action("embed", "Embed corpus", "embedding",
           "the title, abstract and keywords of each record not already in the embedding cache",
           "ranking compares each record with the anchor sentences", ("rank/corpus.py", "ui/routes_jobs.py"),
           ("cli:embed_corpus_cmd", "cli:rank_update")),
    Action("embed_short", "Rank", "embedding",
           "the anchor sentences and the near-miss and adjacent descriptions (no record)",
           "ranking and the development draw compare the records with them", ("rank/corpus.py",),
           ("cli:rank_run", "cli:rank_ablate", "cli:sample_draw", "cli:sample_extend")),
    Action("backend_check", "Check", "model", "a short test message (no project text)",
           "the test shows that the model answers and where it runs", ("core/backend_check.py", "ui/app.py",
                                                               "embeddings/registry.py"),
           ("cli:providers_probe", "cli:embed_probe", "cli:check_backends_cmd"), operation="probe"),
    Action("models", "Load models", "model", "a request for the list of models (no project text)",
           "the backend lists the models it offers", ("core/detect.py",), ("cli_setup:providers_models",),
           operation="models"),
    # Ask WIDER (guide-assistant D8): the query-creation model only (D1, no fallback to the default model), and the
    # embedding model for search by meaning; both under the backend consent operation "assistant"
    Action("assistant", "Ask", "model", SENT["assistant"],
           "the model answers your question from WIDER's documents", ("assistant/ask.py", "ui/routes_assistant.py"),
           ("cli_assistant:ask",),
           stage="construct", operation="assistant", extra={"stage_only": True}),
    Action("assistant_embed", "Ask (search by meaning)", "embedding", EMBED_SENT["assistant"],
           "the sections that match your question by meaning are found", ("assistant/retrieve.py",),
           ("cli_assistant:ask",), operation="assistant"),
)}


# --------------------------------------------------------------------------------------------------- the sentence
def _day(at: str | None) -> str:
    if not at:
        return "an earlier date"
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return str(at)[:10]
    return f"{d.day} {d:%b}"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _lookup(project: Project, a: Action) -> dict[str, Any]:
    ol = project.config.online_lookups
    purpose = a.purpose or a.key
    if purpose == "enrich":
        order = [lookups.SERVICE_LABELS[s] for s in project.config.protocol.enrichment.sources]
        where = order
        what = (f"each record's DOI goes to {_and(order)}" + (" in that order" if len(order) > 1 else "")
                + "; for a record without a DOI its title goes to OpenAlex")
    else:
        where = lookups.service_labels(purpose)
        if purpose in lookups.TERM_PURPOSES:
            what = f"search terms or a query go to {_and(where)}"
        elif purpose in lookups.TERM_AND_DOI_PURPOSES:
            what = (f"the query and the DOI of each Development seed paper (never a Validation paper) go to "
                    f"{_and(where)}")
        else:
            what = f"the DOI and title of each paper go to {_and(where)}"
    if purpose in lookups.PRIOR_PURPOSES:
        on = bool(project.config.prior_reviews.allow_openalex_lookup)
        consent = ("the prior-review lookup switch (prior_reviews.allow_openalex_lookup) is on; "
                   f"{SETTINGS}" if on else "only with the prior-review lookup switch "
                   "(prior_reviews.allow_openalex_lookup) on, which is off")
        allowed = on
    elif purpose in lookups.TERM_PURPOSES:
        allowed = bool(ol.terms_allowed)
        consent = (f"you allowed search-term lookups on {_day(ol.terms_allowed_at)}; {SETTINGS}" if allowed
                   else "WIDER asks before sending")
    elif purpose in lookups.TERM_AND_DOI_PURPOSES:
        allowed = bool(ol.terms_allowed and ol.allowed)
        consent = (f"you allowed this on {_day(max(ol.allowed_at or '', ol.terms_allowed_at or ''))}; {SETTINGS}"
                   if allowed else "WIDER asks before sending")
    else:
        allowed = bool(ol.allowed)
        consent = f"you allowed this on {_day(ol.allowed_at)}; {SETTINGS}" if allowed else "WIDER asks before sending"
    sentence = (f"To {lookups.PURPOSE_LABELS[purpose]}, {what}, because {a.why}; a contact address goes along when "
                f"one is set (WIDER_MAILTO). {consent[0].upper()}{consent[1:]}.")
    return {"what": what, "where": where, "consent": consent, "allowed": allowed, "sentence": sentence,
            "locality": "cloud"}


_PLACE = {"local": "on this computer", "network": "on another computer on your network",
          "cloud": "a remote service", "test": "a test backend that sends nothing"}


def _backend_cfg(project: Project, a: Action, backend: str | None):
    embedding = a.kind == "embedding"
    try:
        if embedding:
            return project.embedding(backend), True
        if backend:
            if a.stage is None and all(pr.name != backend for pr in project.config.providers) \
                    and any(e.name == backend for e in project.config.embeddings):
                return project.embedding(backend), True     # checking an embedding model (Check on its card)
            return project.provider(backend), False
        if a.extra.get("stage_only"):
            return project.provider(project.config.stage_providers[a.stage]), False   # KeyError: no such model set
        return project.provider_for(a.stage) if a.stage else project.provider(None), False
    except (KeyError, TypeError, ValueError):
        return None, embedding


def _model(project: Project, a: Action, backend: str | None) -> dict[str, Any]:
    from .backend_consent import status_of
    from .backends import backend_place

    cfg, embedding = _backend_cfg(project, a, backend)
    if cfg is None:
        sentence = (f"To {a.label.lower()}, WIDER would send {a.sent} to a model, but no "
                    f"{'embedding model' if embedding else 'model'} is set yet, so nothing can be sent.")
        return {"what": a.sent, "where": None, "consent": None, "allowed": False, "sentence": sentence,
                "locality": None, "backend": None}
    place = backend_place(project, cfg, embedding=embedding)
    loc = place["locality"]
    dest = str(place["destination"] or "").split(" (")[0]   # the address, without the locality words
    if loc == "unknown":
        where = f"the {'embedding ' if embedding else ''}model {cfg.name} ({cfg.model})"
    elif loc == "network":
        where = (f"another computer on your network: the {'embedding ' if embedding else ''}model {cfg.name} "
                 f"({cfg.model}) at {dest}")
    elif loc == "cloud":
        where = (f"the {'embedding ' if embedding else ''}model {cfg.name} ({cfg.model}), a remote service at "
                 f"{dest}")
    else:
        where = f"the {'embedding ' if embedding else ''}model {cfg.name} ({cfg.model}), {_PLACE.get(loc, loc)}"
    st = status_of(project, cfg, embedding=embedding, probe=False)
    if st["needs_consent"] is False:
        consent, allowed = "no consent is needed for this place", True
    elif a.operation in st["operations"]:
        consent, allowed = f"you allowed this on {_day(st.get('allowed_at'))}; {SETTINGS}", True
    else:
        consent, allowed = "WIDER asks before sending", False
    unknown = " Where it runs is unknown until checked." if loc == "unknown" else ""
    sentence = f"WIDER sends {a.sent} to {where}, because {a.why}.{unknown} {consent[0].upper()}{consent[1:]}."
    return {"what": a.sent, "where": where, "consent": consent, "allowed": allowed, "sentence": sentence,
            "locality": loc, "backend": cfg.name}


def notice(project: Project, action: str, backend: str | None = None) -> dict[str, Any]:
    """The sentence of one sending action with its parts; ``KeyError`` for an unknown action. ``backend`` names the
    model or embedding model the run will use (default: the stage's or the default one)."""
    a = ACTIONS[action]
    out = _lookup(project, a) if a.kind == "lookup" else _model(project, a, backend)
    # the two halves a page with many cards shows apart (ui-redesign D29): what the button sends and why, said once
    # above the cards, and each card's consent state beside where it sends
    c = out.get("consent")
    return {"action": action, "label": a.label, "kind": a.kind, "why": a.why, **out,
            "general": f"{a.label} sends {a.sent}, because {a.why}.", "consent_line": f"{c[0].upper()}{c[1:]}." if c else ""}


def sentence(project: Project, action: str, backend: str | None = None) -> str:
    return notice(project, action, backend)["sentence"]
