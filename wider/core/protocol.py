"""The review protocol: every methodological constant of the WIDER procedure in one documented place.

The defaults are the values used in the original review, so a project that never touches them reproduces
the replication numbers. Each field carries a ``description`` that the UI, ``wider protocol explain`` and
``docs/protocol_reference.md`` render, so the explanation lives in one source.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class SamplingBand(BaseModel):
    """One rank band of the probability sample (census when ``n`` is None)."""

    name: str = Field(description="Band label used in reports, e.g. S1.")
    start: int | None = Field(default=None, description=(
        "First rank in the band (1-based, inclusive); optional when the band is given as percentiles."))
    end: int | None = Field(default=None, description="Last rank in the band; None = to the end of the ranking.")
    n: int | None = Field(default=None, ge=1, description="Records drawn from the band; None = census (every record).")
    start_pct: float | None = Field(default=None, ge=0, le=100, description=(
        "Start of the band as a percentile of the ranking (0-100); resolved to ranks when the sample is drawn."))
    end_pct: float | None = Field(default=None, ge=0, le=100, description=(
        "End of the band as a percentile of the ranking (0-100); the last band ends at 100."))

    @property
    def is_pct(self) -> bool:
        return self.start_pct is not None or self.end_pct is not None


DEFAULT_BANDS = [
    SamplingBand(name="S1", start=1, end=100, n=None),
    SamplingBand(name="S2", start=101, end=300, n=70),
    SamplingBand(name="S3", start=301, end=1000, n=60),
    SamplingBand(name="S4", start=1001, end=3000, n=46),
    SamplingBand(name="S5", start=3001, end=None, n=40),
]


class LabelSpec(BaseModel):
    """An extra categorical label the screening LLM assigns and the reports tabulate (replaces fixed columns)."""

    key: str = Field(description="Machine key, e.g. responder or adjacent_literature.")
    label: str = Field(default="", description="Human-readable name shown in the UI and reports.")
    values: list[str] = Field(default_factory=list, description="Allowed values; empty = free text.")
    description: str = Field(default="", description="One sentence telling the LLM what the label means.")
    required: bool = Field(default=False, description="Whether the screening schema requires the label.")


class ImportProtocol(BaseModel):
    """Export sanity checks at import: is each file plausibly the result of the query, and is it complete?"""

    min_benchmark_hits: int = Field(1, ge=0, description=(
        "A file that contains fewer benchmark papers than this (DOI, exact title or fuzzy title at "
        "identification.jw_benchmark) fails the benchmark signal of the off-topic check."))
    min_vocabulary_overlap: float = Field(0.5, ge=0.0, le=1.0, description=(
        "Minimum share of records whose title or abstract contains at least one term of the synonyms artifact "
        "(approved or frozen version; a draft is used with a note when none is approved). Below this the file "
        "fails the vocabulary signal. A file is 'off_topic' only when every available signal fails."))
    warn_on_export_cap: bool = Field(True, description=(
        "Flag a file whose record count equals the platform's known export cap for that format "
        "(platforms.yaml export_cap): the export was probably truncated to one batch."))


class IdentificationProtocol(BaseModel):
    """Benchmark coverage: do the seed papers come back from each database export?"""

    jw_benchmark: float = Field(0.88, ge=0.5, le=1.0, description=(
        "Jaro-Winkler title similarity above which an export record counts as a candidate match for a benchmark "
        "paper (after DOI and exact normalised title). Candidates below the confirm threshold are flagged for a look."))
    jw_confirm: float = Field(0.95, ge=0.5, le=1.0, description=(
        "Minimum title similarity for joining imported labels or decisions to the corpus by title. Coverage flags "
        "every fuzzy benchmark match for a look, whatever its similarity."))
    validation_after_freeze: bool = Field(True, description=(
        "Report the validation benchmark set only after the query is frozen (develop on one set, test once on the other)."))
    development_share: float = Field(0.57, ge=0.0, le=1.0, description=(
        "Share of the seed (benchmark) papers the suggested random split assigns to the development set "
        "(the original review used 12 of 21); the rest form the validation set, which is tested once."))
    record_budget: int = Field(5000, ge=1, description=(
        "Records the team can screen from the accepted query. Over it, the suggested edits that save records "
        "without losing a development seed are listed first; the diagnosis says how far over it is."))


class DedupProtocol(BaseModel):
    """Deduplication stages A (DOI), B (exact normalised title), C (fuzzy title with corroboration)."""

    jw_candidate: float = Field(0.85, ge=0.5, le=1.0, description=(
        "Minimum fuzzy title similarity for a pair to enter stage C at all."))
    jw_auto_merge: float = Field(0.97, ge=0.5, le=1.0, description=(
        "Similarity above which a pair with a full citation match is merged automatically."))
    jw_corroborated: float = Field(0.90, ge=0.5, le=1.0, description=(
        "Similarity above which a pair is merged when the year and the first author or journal agree, at least two "
        "locators (volume, issue, first page, article number) match and neither the DOIs nor the publication types "
        "conflict."))
    jw_strong_title: float = Field(0.95, ge=0.5, le=1.0, description=(
        "Merges nothing by itself: pairs with the same first author and year at or above it are put near the top of "
        "the manual queue, and pairs with different DOIs and different first authors below it are ruled not "
        "duplicates."))
    doi_conflict_title: float = Field(0.70, ge=0.0, le=1.0, description=(
        "Pairs sharing a DOI but with title similarity below this (or years more than two apart) go to the manual "
        "queue as DOI conflicts, unless a full citation match confirms them."))
    generic_title_max_words: int = Field(4, ge=1, description=(
        "Short titles (Introduction, Erratum): when the first record of a pair has at most this many title words, "
        "an exact-title merge on two locators also needs the first author or journal to match. No rule merges on "
        "title alone, at any length."))


class CorpusProtocol(BaseModel):
    """What counts as a usable abstract and how much text is embedded."""

    thin_words: int = Field(20, ge=1, description=(
        "An abstract with fewer words is 'thin': it is enriched, excluded from the embedding of its own text, "
        "sampled as a separate pool and never leads to an 'absent' grade in screening."))
    max_words: int = Field(3000, ge=100, description=(
        "Title + abstract + keywords are cut at this many words before embedding."))


class EnrichmentProtocol(BaseModel):
    """Where missing abstracts are fetched from, in order; a found abstract is never overwritten."""

    sources: list[Literal["openalex", "crossref", "europepmc", "semantic_scholar"]] = Field(
        default_factory=lambda: ["openalex", "crossref", "europepmc", "semantic_scholar"],
        description="Metadata services tried in this order for each thin record with a DOI.")
    title_search_min_jw: float = Field(0.95, ge=0.5, le=1.0, description=(
        "Records without a DOI are looked up by title; a hit must reach this title similarity and the year within one."))


class RankingProtocol(BaseModel):
    """Anchor ranking: cosine between each record and one aims-only anchor per cell; sorts, never filters."""

    top_n_cells: int = Field(2, ge=1, description=(
        "A record's score is the mean of its N highest cell similarities (the review used 2; 1 and 3 are compared)."))
    top_n_compare: list[Annotated[int, Field(ge=1)]] = Field(default_factory=lambda: [1, 2, 3], description=(
        "Alternative N values reported in the validation table."))
    recall_at: list[int] = Field(default_factory=lambda: [100, 250, 1000, 3000], description=(
        "Ranks K at which benchmark recall is reported."))
    cluster_top: int = Field(3000, ge=50, description=(
        "Depth of the ranking that is clustered and shown in the explorer; also the deepest band that is cluster-balanced."))
    cluster_k: int = Field(8, ge=2, description="Number of k-means clusters (topics) in the top of the ranking.")
    cluster_terms: int = Field(8, ge=1, description="Distinctive terms listed per cluster.")
    recall_at_pct: list[Annotated[float, Field(gt=0, le=100)]] = Field(
        default_factory=lambda: [1.0, 5.0, 10.0, 25.0, 50.0], description=(
            "Percentiles p of the ranking at which benchmark recall is also reported: a paper counts when its rank "
            "is at most floor(p/100 x ranked records)."))


class SamplingProtocol(BaseModel):
    """Human labelling samples: a band-stratified probability sample (recall estimate) and a purposive development set."""

    bands: list[SamplingBand] = Field(default_factory=lambda: [b.model_copy() for b in DEFAULT_BANDS], description=(
        "Rank bands of the probability sample with the number drawn per band (None = census). "
        "The recall estimate weights each band by N_h/n_h."))
    probability_sample: str = Field("validation", description="Name of the probability sample.")
    development_sample: str = Field("development", description="Name of the purposive development sample.")
    development_reviewers: int = Field(1, ge=1, le=2, description=(
        "Reviewers for the development sample: one, or two (agreement or adjudication needed). With one, a record "
        "is complete with one final label; with two, at least two reviewers labelled it and all agree, or it is "
        "adjudicated. Applies to every purposive sample; until every record is complete, screening runs that include "
        "its records are refused and earlier model output on them is hidden."))
    per_cell: int = Field(4, ge=0, description="Likely includes drawn per construct cell from its top-scoring records.")
    per_near_miss: int = Field(4, ge=0, description="Records drawn per near-miss description (adjacent literatures have their own count, per_adjacent).")
    near_miss_pool: int = Field(12, ge=1, description="Highest-ranked records considered per near-miss description.")
    near_miss_max_rank: int = Field(3000, ge=1, description="Near-miss candidates must rank within this depth.")
    per_adjacent: int = Field(4, ge=0, description=(
        "Marginal cases drawn per approved adjacent literature: the records within near_miss_max_rank closest to the "
        "literature's description and example phrases. Draws nothing while no literature is approved, so the "
        "development set is unchanged unless the researcher names adjacent literatures."))
    per_cluster: int = Field(2, ge=0, description="Records per cluster for topical coverage (medoid first).")
    per_boundary: int = Field(4, ge=0, description=(
        "Boundary cases drawn per near-miss entry and per approved adjacent literature, after every other pool: at "
        "random from the near_miss_pool records within near_miss_max_rank that score highest on both the best cell "
        "and that entry (the lower of the two rank percentiles). No model call."))
    n_thin: int = Field(5, ge=0, description="Thin-abstract records in the development set.")
    n_tail: int = Field(3, ge=0, description="Clearly irrelevant records drawn from beyond the cluster depth.")
    pool_size: int = Field(100, ge=10, description="Top candidates per cell considered when drawing likely includes.")
    min_per_band: int = Field(10, ge=1, description=(
        "Fewest records the validation planner gives a sampled band (never more than the band holds)."))
    plan_sims: int = Field(400, ge=1, le=2000, description=(
        "Simulated samples per allocation in the validation planner (seeded; at most 2000)."))
    plan_seconds: int = Field(30, ge=1, le=600, description=(
        "Time limit of one validation plan in seconds; past it the table is marked approximate."))
    plan_power: float = Field(0.8, ge=0.5, le=0.99, description=(
        "Chance of a clear answer (target met or not met) the planner and the suggestion ask for when they search "
        "for the number of labels."))


# the largest screening.max_tokens any call may ask for (not on the field, so an older project.yaml still loads)
MAX_TOKENS_CEILING = 32000


def check_max_tokens(max_tokens: int, cfg=None) -> None:
    """Refuse a screening output budget above the ceiling, or above the backend's ``extra.max_output_tokens``."""
    if max_tokens > MAX_TOKENS_CEILING:
        raise ValueError(f"screening.max_tokens must be at most {MAX_TOKENS_CEILING}")
    lim = backend_output_limit(cfg)
    if lim is not None and max_tokens > lim:
        raise ValueError(f"screening.max_tokens {max_tokens} is above the {lim} output tokens backend {cfg.name} "
                         f"allows; lower it with wider protocol set screening.max_tokens {lim}")


def backend_output_limit(cfg) -> int | None:
    """The backend's ``extra.max_output_tokens`` as a whole number, or None."""
    v = ((getattr(cfg, "extra", None) or {}) if cfg is not None else {}).get("max_output_tokens")
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


class ScreeningProtocol(BaseModel):
    """LLM screening calls and the corpus protocol built on them."""

    quote_min_chars: int = Field(8, ge=1, description=(
        "The shortest verbatim fragment that counts as a found quote. The model quotes the words behind every grade; an "
        "absent grade whose quote has no fragment this long in the title or abstract becomes unclear (recall first)."))
    max_tokens: int = Field(2000, ge=200, description="Output token budget of one screening call.")
    passes: int = Field(2, ge=1, le=2, description=(
        "Independent reads of every record before the tie-break. With 1, pass A alone decides: no pass B, no third "
        "read, and the majority is not used."))
    blind_third_read: bool = Field(True, description="Where the two reads disagree, a third read with its own seed decides.")
    majority: int = Field(2, ge=1, description="Votes needed for a decision; a three-way split becomes 'maybe'.")
    second_screen: bool = Field(True, description=(
        "Re-read every 'maybe' twice (plus tie-break) in a second protocol. Off: `wider screen second` and the UI "
        "job are refused."))
    recall_first: bool = Field(True, description=(
        "Rule: 'absent' without a verified quote becomes 'unclear'; exclude only when unrelated or verifiably absent. "
        "Off: the model's grades and exclusions stand (maybe flags and unverified-quote flags still apply)."))
    maybe_flags: list[str] = Field(default_factory=list, description=(
        "Boolean fields of the screening schema that force 'maybe' when true (e.g. a contamination flag)."))


class RecallProtocol(BaseModel):
    """Stratified recall estimate on the probability sample against human labels."""

    primary_variant: Literal["strict", "lenient", "cautious"] = Field("lenient", description=(
        "strict = includes kept as include; lenient = includes kept as include or maybe; "
        "cautious = includes and maybes kept as include or maybe."))
    bootstrap_b: int = Field(10000, ge=100, description=(
        "Bootstrap replicates for the cross-check interval in the technical report (stratified, seeded; census "
        "strata fixed); at most 50,000 are run, a larger value is capped with a note. The primary interval is the "
        "approximate design-based interval on the effective number of relevant papers (report section "
        "\"Recall: method\")."))
    ci_level: float = Field(0.95, gt=0.5, lt=1.0, description="Confidence level of the intervals.")
    target: float | None = Field(None, gt=0.0, lt=1.0, description=(
        "Recall the screening should reach, judged on exact bounds valid at every look; set it before any recall "
        "result is seen (wider validation target). Empty = no target."))
    low_recall_rule: Literal["maybes-as-includes", "top-strata", "revise-prompt"] | None = Field(None, description=(
        "What you will do when the target is not met: count maybes as kept, screen the top strata by hand, or revise "
        "the prompt and validate on a fresh sample."))
    top_strata_depth: int | None = Field(None, ge=1, description=(
        "Depth of the ranking screened by hand under the top-strata rule."))
    precision: float | None = Field(None, gt=0.0, lt=1.0, description=(
        "Planned margin: the estimate minus the exact lower bound should not exceed it. Empty = none (no default)."))
    suggest_seconds: int = Field(20, ge=1, le=600, description=(
        "Time limit of the suggestion of how many more labels may be needed; past it the suggestion is approximate."))
    practical_labels: int = Field(500, ge=1, description=(
        "How many more labels you consider practical for validation. When settling whether the recall target is met "
        "is expected to need more, WIDER warns that it may be impractical; the warning never blocks anything, and you "
        "may stop and report the result as inconclusive."))


class LabellingProtocol(BaseModel):
    """Extra categorical labels the screening LLM assigns per record (beyond the fixed decision vocabulary)."""

    labels: list[LabelSpec] = Field(default_factory=list, description=(
        "Project-specific labels, e.g. the actor type, the setting, or an adjacent literature. Each becomes a field of the "
        "screening schema, a column of the decisions export and a breakdown in the recall report."))


class ReviewProtocol(BaseModel):
    identification: IdentificationProtocol = Field(default_factory=IdentificationProtocol)
    imports: ImportProtocol = Field(default_factory=ImportProtocol)
    dedup: DedupProtocol = Field(default_factory=DedupProtocol)
    corpus: CorpusProtocol = Field(default_factory=CorpusProtocol)
    enrichment: EnrichmentProtocol = Field(default_factory=EnrichmentProtocol)
    ranking: RankingProtocol = Field(default_factory=RankingProtocol)
    sampling: SamplingProtocol = Field(default_factory=SamplingProtocol)
    screening: ScreeningProtocol = Field(default_factory=ScreeningProtocol)
    recall: RecallProtocol = Field(default_factory=RecallProtocol)
    labelling: LabellingProtocol = Field(default_factory=LabellingProtocol)

    # ----- introspection -----
    def hash(self) -> str:
        """sha256 of the canonical JSON; recorded in every run spec."""
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def explain(self) -> list[dict[str, Any]]:
        """One row per field: section, key, value, default, description, changed."""
        default = ReviewProtocol()
        rows: list[dict[str, Any]] = []
        for section, sub in self:
            dsub = getattr(default, section)
            for key, info in type(sub).model_fields.items():
                val = getattr(sub, key)
                dval = getattr(dsub, key)
                rows.append({"section": section, "key": key, "path": f"{section}.{key}",
                             "value": _plain(val), "default": _plain(dval), "changed": _plain(val) != _plain(dval),
                             "description": info.description or "", "section_doc": (type(sub).__doc__ or "").strip()})
        return rows

    def _split(self, path: str) -> tuple[str, str]:
        """``section.key`` -> (section, key); KeyError for anything that is not a protocol field."""
        section, _, key = (path or "").partition(".")
        if section not in type(self).model_fields or key not in type(getattr(self, section)).model_fields:
            raise KeyError(f"unknown protocol field {path!r}: a setting is given as section.key "
                           "(see `wider protocol show` for the list)")
        return section, key

    def get(self, path: str) -> Any:
        section, key = self._split(path)
        return getattr(getattr(self, section), key)

    def set(self, path: str, value: Any) -> ReviewProtocol:
        """Return a validated copy with one field changed; ``value`` may be a JSON string."""
        section, key = self._split(path)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        data = self.model_dump(mode="json")
        data[section][key] = value
        try:
            out = ReviewProtocol.model_validate(data)
        except ValidationError as e:   # plain words, not pydantic's report: what is wrong and what was given
            def where(loc):   # the place inside the setting: list items counted from 1
                return " ".join(f"item {x + 1}" if isinstance(x, int) else str(x) for x in loc[2:])
            def msg(err):   # pydantic's parser wording for a number too large to read
                return "the number is too large" if err["type"] == "int_parsing_size" else err["msg"].lower()
            why = "; ".join(f"{where(err['loc']) + ': ' if err['loc'][2:] else ''}{msg(err)}" for err in e.errors())
            raise ValueError(f"{path} was not changed: {why} (got {json.dumps(value)})") from None
        if (section, key) == ("screening", "max_tokens"):   # the ceiling is not on the field (older files still load)
            check_max_tokens(out.screening.max_tokens)
        return out

    def markdown(self) -> str:
        lines = ["# Review protocol reference", "",
                 "Defaults are the values of the original review; `changed` marks project overrides.", ""]
        cur = None
        for r in self.explain():
            if r["section"] != cur:
                cur = r["section"]
                lines += [f"## {cur}", "", r["section_doc"], "", "| key | value | default | what it does |", "|---|---|---|---|"]
            mark = " **(changed)**" if r["changed"] else ""
            lines.append(f"| `{r['key']}` | `{json.dumps(r['value'])}`{mark} | `{json.dumps(r['default'])}` | {r['description']} |")
        return "\n".join(lines) + "\n"

    # ----- legacy -----
    @classmethod
    def from_legacy(cls, bands: list | None = None, thresholds: dict | None = None) -> ReviewProtocol:
        """Build from the pre-Phase-2 ``bands`` / ``thresholds`` keys of project.yaml."""
        p = cls()
        if bands:
            p.sampling.bands = [SamplingBand.model_validate(b) if not isinstance(b, SamplingBand) else b for b in bands]
        t = dict(thresholds or {})
        if "jw_benchmark" in t:
            p.identification.jw_benchmark = t["jw_benchmark"]
        if "jw_dedup" in t:
            p.dedup.jw_candidate = t["jw_dedup"]
        if "thin_words" in t:
            p.corpus.thin_words = t["thin_words"]
        if "max_words" in t:
            p.corpus.max_words = t["max_words"]
        if "top_n_cells" in t:
            p.ranking.top_n_cells = t["top_n_cells"]
        if "quote_min_chars" in t:
            p.screening.quote_min_chars = t["quote_min_chars"]
        return p

    @model_validator(mode="after")
    def _check(self) -> ReviewProtocol:
        bands = self.sampling.bands
        if not bands:
            raise ValueError("sampling.bands must not be empty")
        pct = [b.is_pct for b in bands]
        if any(pct) and not all(pct):
            raise ValueError("sampling.bands must be all ranks or all percentiles, not mixed")
        if all(pct):
            if bands[0].start_pct != 0:
                raise ValueError("the first band must start at percentile 0")
            for b in bands:
                if b.start_pct is None or b.end_pct is None or not b.start_pct < b.end_pct:
                    raise ValueError(f"band {b.name} needs start_pct below end_pct")
            for a, b in zip(bands, bands[1:]):
                if b.start_pct != a.end_pct:
                    raise ValueError(f"bands must be contiguous: {a.name} ends at percentile {a.end_pct:g}, "
                                     f"{b.name} starts at {b.start_pct:g}")
            if bands[-1].end_pct != 100:
                raise ValueError("the last band must end at percentile 100")
        else:
            if bands[0].start != 1:
                raise ValueError("the first band must start at rank 1")
            for a, b in zip(bands, bands[1:]):
                if a.end is None or b.start != a.end + 1:
                    raise ValueError(f"bands must be contiguous: {a.name} ends at {a.end}, {b.name} starts at {b.start}")
            if bands[-1].end is not None:
                raise ValueError(f"the last band must run to the end of the ranking: leave the end of {bands[-1].name} "
                                 "empty, so recall covers every ranked record")
        # one pass decides alone, so the majority is only checked for two passes
        if self.screening.passes == 2 and self.screening.majority > 2 + (1 if self.screening.blind_third_read else 0):
            raise ValueError("screening.majority cannot exceed the number of reads")
        return self


def _plain(v: Any) -> Any:
    if isinstance(v, BaseModel):
        return v.model_dump(mode="json")
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def iter_fields() -> Iterator[tuple[str, str, Any]]:
    """(section, key, FieldInfo) for every protocol field — used by the UI and docs."""
    for section, info in ReviewProtocol.model_fields.items():
        sub = info.default_factory()  # type: ignore[misc]
        for key, finfo in type(sub).model_fields.items():
            yield section, key, finfo
