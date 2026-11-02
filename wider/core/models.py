"""Pydantic models shared by the CLI, store and UI."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .protocol import DEFAULT_BANDS, ReviewProtocol, SamplingBand  # noqa: F401 (re-exported)

BenchmarkSet = Literal["Development", "Validation"]


class BenchmarkPaper(BaseModel):
    """A known-relevant paper used to develop (dev) or test once (validation) the search and ranking."""

    benchmark_id: str
    title: str
    doi: str | None = None
    set: BenchmarkSet = "Development"
    year: int | None = None
    first_author: str | None = None
    abstract: str | None = None
    note: str = ""
    source: str = "researcher"  # researcher | prior_review | query_results | seed_chain:<id> | ...
    added_at: str = ""
    validation_since: str = ""  # when the paper last entered Validation (shown only)
    validation_since_unlock: int = 0  # the seen count when it last entered Validation (the rule reads this)
    was_development: bool = False  # it was in Development at some time
    keywords: str = ""
    keywords_from: str = ""  # researcher | import


class ProviderConfig(BaseModel):
    """One LLM backend. API keys are never stored here: ``api_key_env`` names an environment variable."""

    name: str
    kind: Literal["openai_compat", "ollama", "anthropic", "cli_agent", "fake", "litellm", "typesafe"] = "openai_compat"
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    command: list[str] | None = None  # cli_agent: argv template, e.g. ["claude", "-p", ...]
    temperature: float = 0.0
    seed: int | None = 1
    repairs: int | None = Field(default=None, ge=1, le=2, description=(
        "Schema-repair rounds after a reply that does not match the required JSON (empty: one; 2 for a backend "
        "that often needs a second try). Each round is one more call; an item gets at most three calls."))
    max_concurrency: int = 4
    timeout_s: int = 300
    price_input_per_mtok: float | None = Field(default=None, ge=0, description=(
        "US dollars per million input (prompt) tokens, from the vendor's price list; used only to show costs. "
        "Leave empty for local backends (they cost nothing) or when unknown (costs then show as not priced)."))
    price_output_per_mtok: float | None = Field(default=None, ge=0, description=(
        "US dollars per million output (completion) tokens; see price_input_per_mtok."))
    price_cached_input_per_mtok: float | None = Field(default=None, ge=0, description=(
        "US dollars per million cached input tokens; when empty they are priced at the input price."))
    price_cache_write_per_mtok: float | None = Field(default=None, ge=0, description=(
        "US dollars per million tokens written to the vendor's prompt cache; when empty, the input price."))
    preset: str | None = None  # the catalogue entry it was added from; a label only, every value is copied
    extra: dict = Field(default_factory=dict)


class EmbeddingConfig(BaseModel):
    name: str
    kind: Literal["openai", "openai_compat", "ollama", "sentence_transformers", "fake"] = "sentence_transformers"
    model: str = "BAAI/bge-m3"
    base_url: str | None = None
    api_key_env: str | None = None
    dim: int | None = None  # Matryoshka truncation (the review used 1024 of text-embedding-3-large)
    batch_size: int = 64
    max_words: int = 3000
    price_input_per_mtok: float | None = Field(default=None, ge=0, description=(
        "US dollars per million input tokens for an embedding service; empty for local models."))
    preset: str | None = None
    extra: dict = Field(default_factory=dict)


class Platform(BaseModel):
    """A bibliographic platform the researcher will run a query on and export RIS files from."""

    key: str  # scopus | wos | ebsco | proquest | ovid | pubmed | gscholar, or a custom key
    label: str = ""
    role: Literal["development", "validation"] = "validation"
    syntax: str | None = Field(default=None, description=(
        "Syntax family from the catalogue (platforms.yaml) used to render the query for a platform that is not "
        "in the catalogue itself; None = the platform's own key."))
    limits: dict = Field(default_factory=dict, description=(
        "Limits applied in the database (dates, languages, reason), set with `wider query limits`; reported in "
        "the package's limits.json and the PRISMA-S report."))


class Thresholds(BaseModel):
    """Read-only compatibility view of the protocol's threshold fields (use ``config.protocol`` for edits)."""

    jw_benchmark: float = 0.88
    jw_dedup: float = 0.85
    thin_words: int = 20
    max_words: int = 3000
    top_n_cells: int = 2
    quote_min_chars: int = 8


class PriorReviewPolicy(BaseModel):
    """Data policy for prior-review files (``prior_reviews/``): local by default, two per-project opt-in switches."""

    none_known: bool = Field(default=False, description="The researcher stated that no previous or relevant review is known.")
    allow_openalex_lookup: bool = Field(default=False, description=(
        "Allow DOIs and titles of prior-review papers to be sent to OpenAlex to resolve identifiers and chain "
        "citations. Off: nothing leaves the computer."))
    allow_llm_excerpts: bool = Field(default=False, description=(
        "Allow titles of studies included in prior reviews to appear in construct proposal prompts sent to the LLM "
        "backend. Off: proposals use the seed papers only."))
    max_citing_per_paper: int = Field(default=2000, ge=1, description=(
        "Cap on citing works fetched per prior-review paper during forward chaining (a warning records the cap)."))


class ExportsConfig(BaseModel):
    """How researcher names are written in shared files (researcher-attribution D4)."""

    attribution: Literal["pseudonyms", "names", "none"] = Field(default="pseudonyms", description=(
        "Names in every file under exports/, the share bundle and the replication package: 'pseudonyms' (Researcher 1, "
        "Researcher 2 ...), 'names' (declared names; inferred names stay pseudonyms) or 'none'. History keeps names."))


class AssistantSettings(BaseModel):
    """Ask WIDER (guide-assistant D10)."""

    context_tokens: int | None = Field(default=None, ge=500, le=200_000, description=(
        "How many tokens (characters / 4) Ask WIDER may send the model per question, from 500 to 200,000. Unset: "
        "3,000 for a backend on this computer or an unknown place, 12,000 for the local network or a remote service."))


class OnlineLookups(BaseModel):
    """Consent to send paper identifiers to outside metadata services (seed-paper resolve, seed abstracts, abstract
    enrichment). Off by default; prior-review lookups keep their own switch in ``prior_reviews``."""

    allowed: bool = Field(default=False, description=(
        "Allow the DOI and title of each paper (and the WIDER_MAILTO contact address when set) to be sent to OpenAlex, "
        "Crossref, Europe PMC and Semantic Scholar to resolve seed papers and fetch abstracts. Off: nothing is sent."))
    allowed_at: str | None = Field(default=None, description="When consent was given (UTC).")
    allowed_by: str | None = Field(default=None, description="Who gave consent.")
    terms_allowed: bool = Field(default=False, description=(
        "Allow search terms or a whole query string to be sent to OpenAlex to count the works they find (yield), "
        "with Development seed DOIs only for the seed check, which also needs the DOI consent. Separate from the DOI "
        "consent above: giving one does not give the other. Off: no search term or query leaves the computer."))
    terms_allowed_at: str | None = Field(default=None, description="When the term switch was turned on (UTC).")
    terms_allowed_by: str | None = Field(default=None, description="Who turned the term switch on.")


class SeedPdfs(BaseModel):
    """What the model pass of seed-paper mining may send (vision change 2b, the privacy rule's first named exception).
    The PDF itself never leaves the computer; a ``local`` or ``test`` backend gets passages regardless."""

    passages_to_cloud: bool = Field(default=True, description=(
        "Allow extracted passages of seed PDFs (never the file) to go to a backend that is not local (network, cloud "
        "or unknown) once you allowed that backend for passages. Off: the model pass is skipped for such a backend "
        "and only the pattern pass runs."))
    passage_tokens: int = Field(default=1500, ge=100, le=8000, description=(
        "The most a passage may hold, in tokens (the cost estimate's count); one passage per model call."))
    grobid_url: str = Field(default="", description=(
        "A GROBID server you run on this computer (loopback address only, e.g. http://127.0.0.1:8070) that parses "
        "the references of seed PDFs; empty: not used. WIDER never installs or starts it."))


class BackendConsent(BaseModel):
    """Consent to contact one network, cloud or unknown backend (design Decision 17), per operation. ``fingerprint``
    covers what the consent was given for (destination, key variable, auth, header names, text version); when it no
    longer matches, the consent counts as missing and the stored ``parts`` (what the fingerprint was made of) name what
    changed."""

    fingerprint: str
    operations: list[str] = Field(default_factory=list)
    parts: dict = Field(default_factory=dict)
    allowed_at: str | None = None
    allowed_by: str | None = None


class ProjectConfig(BaseModel):
    """Contents of ``project.yaml``."""

    name: str
    title: str = ""
    questions: list[str] = Field(default_factory=list)
    include_notes: str = ""
    exclude_notes: str = ""
    platforms: list[Platform] = Field(default_factory=list)
    providers: list[ProviderConfig] = Field(default_factory=list)
    default_provider: str | None = None
    stage_providers: dict[str, str] = Field(default_factory=dict, description=(
        "Per-stage LLM backend overrides: stage key (construct, screen) -> provider name; stages not listed use "
        "default_provider."))
    embeddings: list[EmbeddingConfig] = Field(default_factory=list)
    default_embedding: str | None = None
    protocol: ReviewProtocol = Field(default_factory=ReviewProtocol)
    prior_reviews: PriorReviewPolicy = Field(default_factory=PriorReviewPolicy)
    online_lookups: OnlineLookups = Field(default_factory=OnlineLookups)
    seed_pdfs: SeedPdfs = Field(default_factory=SeedPdfs)
    exports: ExportsConfig = Field(default_factory=ExportsConfig)
    backend_consent: dict[str, BackendConsent] = Field(default_factory=dict, description=(
        "Consent per LLM backend name to contact it (network, cloud or unknown backends only), per operation: "
        "models, probe, research, passages."))
    embedding_consent: dict[str, BackendConsent] = Field(default_factory=dict, description=(
        "Consent per embedding backend name (network, cloud or unknown only), per operation: probe, research. Kept "
        "apart from backend_consent."))
    doubts: Literal["queue", "ask"] = Field(default="queue", description=(
        "When the tool is in doubt (a screening answer that could not be parsed, three reads that all disagree, a DOI "
        "that finds no abstract, a seed paper that cannot be resolved, a proposal the model could not produce): "
        "'queue' collects the questions on the Questions page; 'ask' opens each one in a dialog as it arrives while the "
        "run continues."))
    assistant: AssistantSettings = Field(default_factory=AssistantSettings)
    seed: int = 20260915
    created_at: str = ""
    wider_version: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data):
        """project.yaml files written before Phase 2 carry ``bands`` and ``thresholds`` at the top level."""
        if isinstance(data, dict) and ("bands" in data or "thresholds" in data):
            data = dict(data)
            bands, thr = data.pop("bands", None), data.pop("thresholds", None)
            base = data.get("protocol")
            if base:  # both present: the legacy keys override (they were edited last)
                proto = ReviewProtocol.model_validate(base) if not isinstance(base, ReviewProtocol) else base
                legacy = ReviewProtocol.from_legacy(bands, thr)
                merged = proto.model_dump(mode="json")
                if bands:
                    merged["sampling"]["bands"] = legacy.model_dump(mode="json")["sampling"]["bands"]
                for sec, key in (("identification", "jw_benchmark"), ("dedup", "jw_candidate"), ("corpus", "thin_words"),
                                 ("corpus", "max_words"), ("ranking", "top_n_cells"), ("screening", "quote_min_chars")):
                    if thr and (key if key != "jw_candidate" else "jw_dedup") in thr:
                        merged[sec][key] = getattr(getattr(legacy, sec), key)
                data["protocol"] = merged
            else:
                data["protocol"] = ReviewProtocol.from_legacy(bands, thr).model_dump(mode="json")
        return data

    # ----- compatibility views -----
    @property
    def bands(self) -> list[SamplingBand]:
        return self.protocol.sampling.bands

    @bands.setter
    def bands(self, value: list[SamplingBand]) -> None:
        self.protocol.sampling.bands = list(value)

    @property
    def thresholds(self) -> Thresholds:
        pr = self.protocol
        return Thresholds(jw_benchmark=pr.identification.jw_benchmark, jw_dedup=pr.dedup.jw_candidate,
                          thin_words=pr.corpus.thin_words, max_words=pr.corpus.max_words,
                          top_n_cells=pr.ranking.top_n_cells, quote_min_chars=pr.screening.quote_min_chars)
