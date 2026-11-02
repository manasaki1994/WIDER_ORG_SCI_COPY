"""The local dry run (vision change 2a, D6): a synonyms version read against the seed papers' title, abstract and
keywords the way each database would read it, by a profile per platform in ``platforms.yaml``.

Every seed gets one of three states per platform: ``hit``, ``miss`` or ``uncertain``. ``uncertain`` carries a reason
whenever the outcome depends on something the profile cannot emulate (automatic term mapping, lemmatisation, stop
words, truncation inside a phrase, a stem under the platform's minimum) or on a field the seed does not have (no
abstract, no keywords). It is never counted as retrieved. Nothing here runs a query in any database.

The functions are pure except :func:`dry_run`, which reads the project and writes ``runs/query/<stamp>/dry_run.json``.
"""
from __future__ import annotations

import functools
import itertools
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from .ir import Near, Query, Term, is_phrase, phrase_text

State = Literal["hit", "miss", "uncertain"]
RECORD_FIELDS = ("title", "abstract", "keywords")
NO_SEEDS = "no seed papers: add them on the Review plan (Seed papers)"
REASONS = {"automatic_term_mapping": "automatic term mapping", "lemmatisation": "lemmatisation",
           "stop_words": "stop words", "phrase_truncation_differs": "truncation inside a phrase"}


class Profile(BaseModel):
    """How a platform reads a string (D6). Every value is a fact to check against the help page (manual check M46)."""

    platform: str
    fields: dict[str, list[str]] = Field(default_factory=lambda: {f: [f] for f in RECORD_FIELDS})
    min_stem: int | None = None
    max_truncation: int | None = None
    phrase_truncation: bool = True
    proximity_range: list[int | None] | None = None
    proximity_default: int | None = None
    proximity_counts: Literal["between", "within"] = "between"
    proximity_ordered_available: bool = False
    near_as_and: bool = False
    stop_words: list[str] = Field(default_factory=list)
    uncertain_when: list[Literal["automatic_term_mapping", "lemmatisation", "stop_words",
                                 "phrase_truncation_differs"]] = Field(default_factory=list)


class NoProfile(ValueError):
    """The platform has no profile (Google Scholar, a custom platform): reported and skipped, never guessed."""


def load_profile(platform: str, query: Query | None = None) -> Profile:
    """The profile of a catalogue platform, or OpenAlex's (kept next to its renderer, outside the catalogue); with
    ``query``, OpenAlex's drops lemmatisation when the query goes to the unstemmed exact field. :class:`NoProfile`
    ("no profile for gscholar") when there is none."""
    from .render import openalex, platforms

    entry = platforms().get(platform)
    if entry is not None and entry.get("profile"):
        return Profile(platform=platform, **entry["profile"])
    if platform == "openalex":
        prof = Profile(platform=platform, **openalex.PROFILE)
        if query is not None and openalex.field_for(query) == openalex.EXACT:
            prof.uncertain_when = [u for u in prof.uncertain_when if u != "lemmatisation"]
        return prof
    raise NoProfile(f"no profile for {platform}")


def profiled_platforms() -> list[str]:
    """Catalogue keys that have a profile, in catalogue order."""
    from .render import platforms

    return [k for k, v in platforms().items() if v.get("profile")]


# ----------------------------------------------------------------- text
@dataclass(frozen=True)
class Token:
    word: str
    start: int
    end: int  # a joined hyphen form ("selfefficacy") spans the positions of its parts


_WORD = re.compile(r"[^\W_]+(?:-[^\W_]+)*")


def tokenize(text: str) -> list[str]:
    """Lower-case words of letters and digits; a hyphen breaks a word ("self-efficacy" gives "self", "efficacy")."""
    return [t.word for t in _tokens(text) if t.start == t.end]


def _tokens(text: str | None, gap: int = 0) -> list[Token]:
    """Tokens with positions; a hyphenated word adds its joined form, so "self-efficacy" matches "self efficacy" and
    "selfefficacy" (what the platforms do within a phrase)."""
    out: list[Token] = []
    pos = gap
    for m in _WORD.finditer((text or "").lower()):
        parts = m.group(0).split("-")
        for i, w in enumerate(parts):
            out.append(Token(w, pos + i, pos + i))
        if len(parts) > 1:
            out.append(Token("".join(parts), pos, pos + len(parts) - 1))
        pos += len(parts)
    return out


def _keyword_tokens(text: str | None) -> list[Token]:
    """Each keyword on its own, far apart, so a phrase never runs across two keywords."""
    out: list[Token] = []
    for i, kw in enumerate(k for k in re.split(r"[;\n]", text or "") if k.strip()):
        out.extend(_tokens(kw, gap=i * 1000))
    return out


@dataclass
class SeedDoc:
    benchmark_id: str
    set: str = "Development"
    title: str = ""
    abstract: str = ""
    keywords: str = ""
    _cache: dict = field(default_factory=dict, init=False, repr=False, compare=False)

    def text(self, fld: str) -> list[Token] | None:
        """The tokens of one record field; None when the seed has nothing in it. Kept per field text, since every term
        of the query reads them."""
        raw = getattr(self, fld, "") or ""
        if not raw.strip():
            return None
        key = (fld, raw)
        if key not in self._cache:
            self._cache[key] = _keyword_tokens(raw) if fld == "keywords" else _tokens(raw)
        return self._cache[key]


# ----------------------------------------------------------------- matching
@dataclass
class Match:
    """A term found in a field: ``state`` is ``hit`` or ``uncertain`` (with reasons); ``words`` are the seed's words."""

    state: State
    reasons: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    spans: list[tuple[int, int]] = field(default_factory=list)


def _better(a: Match | None, b: Match | None) -> Match | None:
    if a is None:
        return b
    if b is None:
        return a
    if a.state == b.state:
        return Match(a.state, sorted(set(a.reasons + b.reasons)), sorted(set(a.words + b.words)), a.spans + b.spans)
    return a if a.state == "hit" else b


def _stem_len(piece: str) -> int:
    return len(re.split(r"[*?]", piece, 1)[0])


def _piece_regex(piece: str, profile: Profile) -> re.Pattern:
    return _compiled(piece, profile.max_truncation)[1]


@functools.lru_cache(maxsize=65536)
def _compiled(piece: str, max_truncation: int | None) -> tuple[str, re.Pattern]:
    """The literal start of a truncated piece and its pattern, kept per piece: a long query has more truncated pieces
    than ``re``'s own cache holds, and recompiling them for every token made a dry run of a long query take over 25
    minutes. The start rules out most tokens before the pattern is tried."""
    tail = r"[^\W_]*" if max_truncation is None else rf"[^\W_]{{0,{max_truncation}}}"
    return (re.split(r"[*?]", piece, 1)[0],
            re.compile("".join(tail if c == "*" else r"[^\W_]" if c == "?" else re.escape(c) for c in piece)))


@functools.lru_cache(maxsize=65536)
def variants(word: str) -> frozenset[str]:
    """Spelling and plural variants a platform may add on its own (term mapping, lemmatisation): -s/-es, -ise/-ize,
    -our/-or, -yse/-yze. Used only to mark a result ``uncertain``, never to make a hit."""
    out = {word + "s", word + "es"}
    if word.endswith("es"):
        out.add(word[:-2])
    if word.endswith("s"):
        out.add(word[:-1])
    for a, b in (("is", "iz"), ("our", "or"), ("ys", "yz")):
        for x, y in ((a, b), (b, a)):
            if x in word:
                out.add(word.replace(x, y))
    out.discard(word)
    return frozenset(out)


def _word_matches(piece: str, tok: Token, profile: Profile, in_phrase: bool) -> tuple[State, list[str]] | None:
    """One query word (``*`` and ``?`` allowed) against one token."""
    piece = piece.lower()
    if "*" in piece or "?" in piece:
        start, rx = _compiled(piece, profile.max_truncation)
        if not tok.word.startswith(start) or not rx.fullmatch(tok.word):
            return None
        if profile.min_stem and _stem_len(piece) < profile.min_stem:
            return "uncertain", ["stem under minimum"]
        return "hit", []
    if tok.word == piece:
        return "hit", []
    if not in_phrase and tok.word in variants(piece):
        for flag in ("automatic_term_mapping", "lemmatisation"):
            if flag in profile.uncertain_when:
                return "uncertain", [REASONS[flag]]
    return None


def _phrase_matches(words: list[str], toks: list[Token], profile: Profile) -> Match | None:
    """Ordered adjacent words; stop words and truncation inside a phrase follow the profile."""
    truncated = any("*" in w for w in words)
    drop_trunc = truncated and not profile.phrase_truncation
    by_start: dict[int, list[Token]] = {}
    for t in toks:
        by_start.setdefault(t.start, []).append(t)
    best: Match | None = None
    for first in toks:
        found = _phrase_from(words, 0, first, by_start, profile, drop_trunc)
        if found is not None:
            best = _better(best, found)
    return best


def _phrase_from(words, i, tok, by_start, profile, drop_trunc) -> Match | None:
    w = words[i].lower()
    reasons: list[str] = []
    stop = "stop_words" in profile.uncertain_when and w.replace("*", "") in profile.stop_words
    r = _word_matches(w.replace("*", "") if drop_trunc else w, tok, profile, in_phrase=True)
    if r is None and drop_trunc and "*" in w:
        r = _word_matches(w, tok, profile, in_phrase=True)
        if r is not None:
            r = ("uncertain", r[1] + [REASONS["phrase_truncation_differs"]])
    if r is None and stop:
        r = ("uncertain", [REASONS["stop_words"]])
    if r is None:
        return None
    state, reasons = r
    if i == len(words) - 1:
        return Match(state, reasons, [tok.word], [(tok.start, tok.end)])
    best: Match | None = None
    for nxt in by_start.get(tok.end + 1, []):
        rest = _phrase_from(words, i + 1, nxt, by_start, profile, drop_trunc)
        if rest is None:
            continue
        st = "uncertain" if "uncertain" in (state, rest.state) else "hit"
        best = _better(best, Match(st, sorted(set(reasons + rest.reasons)), [tok.word] + rest.words,
                                   [(tok.start, rest.spans[0][1])]))
    return best


def _simple_matches(term: str, toks: list[Token], profile: Profile) -> Match | None:
    """A word or a quoted phrase (no proximity) against one field's tokens."""
    if is_phrase(term) or re.search(r"[\s\-]", phrase_text(term).strip()):
        words = [w for w in re.split(r"[\s\-]+", phrase_text(term)) if w]
        return _phrase_matches(words, toks, profile)
    best: Match | None = None
    for tok in toks:
        r = _word_matches(term.strip(), tok, profile, in_phrase=False)
        if r is not None:
            best = _better(best, Match(r[0], r[1], [tok.word], [(tok.start, tok.end)]))
    return best


def _near_matches(n: Near, toks: list[Token], profile: Profile) -> Match | None:
    items: list[list[Match]] = []
    for it in n.near:
        alts = it if isinstance(it, list) else [it]
        found = [m for m in (_simple_matches(a, toks, profile) for a in alts) if m is not None]
        if not found:
            return None
        # one entry per position, so each placement is tried
        items.append([Match(m.state, m.reasons, m.words, [s]) for m in found for s in m.spans])
    if profile.near_as_and:
        return _combine([min(alts, key=lambda m: m.state != "hit") for alts in items])
    allowed = n.distance if profile.proximity_counts == "between" else n.distance - 1
    best: Match | None = None
    for combo in itertools.product(*items):
        spans = [m.spans[0] for m in combo]
        if n.ordered and any(spans[i][0] <= spans[i - 1][1] for i in range(1, len(spans))):
            continue
        order = sorted(spans)
        if any(b[0] <= a[1] or b[0] - a[1] - 1 > allowed for a, b in zip(order, order[1:], strict=False)):
            continue
        best = _better(best, _combine(list(combo)))
    if best is not None:
        lo, hi = (list(profile.proximity_range or []) + [None, None])[:2]
        if (lo is not None and n.distance < lo) or (hi is not None and n.distance > hi):
            best = Match("uncertain", best.reasons + [f"proximity {n.distance} outside the range "
                                                      f"{lo}–{hi if hi is not None else 'any'}"], best.words)
    return best


def _combine(ms: list[Match]) -> Match:
    st: State = "uncertain" if any(m.state == "uncertain" for m in ms) else "hit"
    return Match(st, sorted({r for m in ms for r in m.reasons}), [w for m in ms for w in m.words],
                 [s for m in ms for s in m.spans])


def term_label(term: Term) -> str:
    """A term as the researcher wrote it; a proximity group as ``a NEAR/5 b``."""
    if isinstance(term, Near):
        op = f" {'PRE' if term.ordered else 'NEAR'}/{term.distance} "
        return op.join("(" + " OR ".join(it) + ")" if isinstance(it, list) else it for it in term.near)
    return term


def record_fields(block_fields: list[str], profile: Profile) -> list[str]:
    """The record fields a block's fields search on this platform (the profile's field map)."""
    out: list[str] = []
    for f in block_fields:
        for r in profile.fields.get(f, [f]):
            if r not in out:
                out.append(r)
    return out


def term_matches(term: Term, doc: SeedDoc, profile: Profile, fields: list[str] | None = None) -> Match | None:
    """The best match of one term in the record fields searched (proximity counts within one field)."""
    flds = record_fields(fields or ["title", "abstract"], profile)
    if isinstance(term, Near) and profile.near_as_and:   # AND within the fields: the items may sit in different ones
        toks = [Token(t.word, t.start + i * 100000, t.end + i * 100000)
                for i, f in enumerate(flds) for t in (doc.text(f) or [])]
        return _near_matches(term, toks, profile) if toks else None
    best: Match | None = None
    for fld in flds:
        toks = doc.text(fld)
        if toks is None:
            continue
        m = _near_matches(term, toks, profile) if isinstance(term, Near) else _simple_matches(term, toks, profile)
        best = _better(best, m)
    return best


# ----------------------------------------------------------------- query
@dataclass
class BlockResult:
    state: State
    terms: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    words: dict[str, list[str]] = field(default_factory=dict)
    hits: list[str] = field(default_factory=list)   # the terms that matched for certain (the grid's and cover's cells)


@dataclass
class SeedResult:
    benchmark_id: str
    set: str
    state: State
    per_block: dict[str, BlockResult]
    terms_matched: list[str]
    reasons: list[str]
    failed_blocks: list[str]

    def as_dict(self) -> dict:
        return {"benchmark_id": self.benchmark_id, "set": self.set, "state": self.state,
                "terms": self.terms_matched, "reasons": self.reasons, "failed_blocks": self.failed_blocks,
                "blocks": {k: {"state": b.state, "terms": b.terms, "reasons": b.reasons, "words": b.words,
                               "hits": b.hits}
                           for k, b in self.per_block.items()}}


def evaluate_block(block, doc: SeedDoc, profile: Profile) -> BlockResult:
    hits, unc, reasons, words = [], [], [], {}
    for t in block.terms:
        m = term_matches(t, doc, profile, block.fields)
        if m is None:
            continue
        label = term_label(t)
        words[label] = sorted(set(m.words))
        (hits if m.state == "hit" else unc).append(label)
        if m.state == "uncertain":
            reasons.extend(m.reasons)
    if hits:
        return BlockResult("hit", hits + unc, [], words, hits)
    # a missing field makes the block uncertain only when the block names it and the platform searches it: a field the
    # platform adds on its own (PubMed's [tiab] includes author keywords) is searched when the seed has it, but its
    # absence decides nothing, nor does a named field the profile does not read (OpenAlex reads no keywords)
    searched = record_fields(block.fields, profile)
    for fld in block.fields:
        if fld in RECORD_FIELDS and fld in searched and doc.text(fld) is None:
            reasons.append(f"no {fld}")
    if unc or reasons:
        return BlockResult("uncertain", unc, sorted(set(reasons)), words)
    return BlockResult("miss")


def _logic(node: Any, per_block: dict[str, BlockResult]) -> State:
    if isinstance(node, str):
        return per_block[node].state
    (op, val), = node.items()
    if op == "not":
        s = _logic(val, per_block)
        return {"hit": "miss", "miss": "hit"}.get(s, "uncertain")
    states = [_logic(k, per_block) for k in val]
    if op == "and":
        return "miss" if "miss" in states else "uncertain" if "uncertain" in states else "hit"
    return "hit" if "hit" in states else "uncertain" if "uncertain" in states else "miss"


def _positive_refs(node: Any, negated: bool = False) -> list[str]:
    if isinstance(node, str):
        return [] if negated else [node]
    (op, val), = node.items()
    if op == "not":
        return _positive_refs(val, not negated)
    return [r for k in val for r in _positive_refs(k, negated)]


def evaluate(query: Query, doc: SeedDoc, profile: Profile) -> SeedResult:
    """One seed under one profile, following ``query.logic`` with three-valued AND, OR and NOT."""
    per_block = {b.key: evaluate_block(b, doc, profile) for b in query.blocks}
    state = _logic(query.logic, per_block)
    positive = _positive_refs(query.logic)
    terms = [t for k in positive for t in per_block[k].terms]
    reasons = sorted({r for k, b in per_block.items() for r in b.reasons}) if state == "uncertain" else []
    failed = [k for k in positive if per_block[k].state == "miss"] if state != "hit" else []
    return SeedResult(doc.benchmark_id, doc.set, state, per_block, terms, reasons, failed)


def why(seed: dict) -> str:
    """The "why" column of the CLI table and the page: the reasons of an uncertain seed, the blocks a miss failed."""
    if seed["state"] == "uncertain":
        return ", ".join(seed["reasons"])
    return "no match in block " + ", ".join(seed["failed_blocks"]) if seed["failed_blocks"] else ""


def recall(results: list[SeedResult]) -> dict:
    """Hits over all seeds evaluated; uncertain seeds are never retrieved and are reported on their own."""
    n = len(results)
    hits = sum(r.state == "hit" for r in results)
    unc = sum(r.state == "uncertain" for r in results)
    return {"hits": hits, "uncertain": unc, "misses": n - hits - unc, "total": n,
            "text": f"recall {hits}/{n}" + (f", {unc} uncertain (not counted)" if unc else "")}


# ----------------------------------------------------------------- the dry run of a project
def seed_doc(b, group: str | None = None, abstract: str | None = None) -> SeedDoc:
    return SeedDoc(b.benchmark_id, group or b.set, b.title or "", abstract or b.abstract or "", b.keywords or "")


def _abstracts(project) -> dict[str, str]:
    """The seed abstracts kept in the store (fetched, or taken from the corpus); benchmark.csv holds none."""
    return {r["benchmark_id"]: r["abstract"] for r in
            project.store.query("SELECT benchmark_id, abstract FROM benchmark_papers WHERE abstract IS NOT NULL")}


def validation_notice(project, version: int) -> str | None:
    """None for the frozen query's own version, else why the validation seeds are left out (Q3's dry-run limit)."""
    from ..construct import artifacts

    fq = artifacts.frozen_query(project)
    if fq is None:
        return "validation seeds are shown only for a frozen query; none stands"
    if fq["version"] != version:
        return f"validation seeds are shown only for the frozen query v{fq['version']}"
    return None


def seeds_for(project, version: int) -> tuple[list[SeedDoc], str | None]:
    """The development seeds (no used validation paper, validation-reuse D1), plus the validation set only for the
    frozen query's version."""
    from . import coverage

    papers = project.read_benchmark()
    ab = _abstracts(project)
    docs = [seed_doc(b, "Development", ab.get(b.benchmark_id)) for b in coverage.development_papers(project, papers)]
    notice = validation_notice(project, version)
    if notice is None and coverage.validation_unlocked(project):
        docs += [seed_doc(b, "Validation", ab.get(b.benchmark_id)) for b in coverage.validation_papers(project, papers)]
    return docs, notice


def default_platforms(project) -> list[str]:
    """Every profiled catalogue platform, the project's chosen ones first in its search order."""
    chosen = [p.key for p in project.config.platforms]
    prof = profiled_platforms()
    return [k for k in chosen if k in prof] + [k for k in prof if k not in chosen]


class DryRunError(ValueError):
    """A dry run that cannot start (no seed papers, no synonyms version, no profile): exit 1 or HTTP 400."""


def dry_run(project, version: int | None = None, platform: str | None = None, write: bool = True) -> dict:
    """Evaluate a synonyms version (default: the newest) on every profiled platform, or on ``platform``. Writes
    ``runs/query/<stamp>/dry_run.json`` with the profile values used, so a later profile change shows in the folder."""
    from ..construct import artifacts
    from ..core.provenance import stamp, utcnow
    from . import coverage_plot

    if not project.read_benchmark():
        raise DryRunError(NO_SEEDS)
    row, data = artifacts.load(project, "synonyms", version)
    if row is None:
        raise DryRunError(f"no synonyms version {version}" if version is not None else "no synonyms version yet")
    query = Query.model_validate(data)   # before the profiles: OpenAlex's depends on the field the query goes to
    if platform is not None:
        profiles = [load_profile(platform, query)]   # NoProfile is a DryRunError's sibling: both exit 1
    else:
        profiles = [load_profile(k, query) for k in default_platforms(project)]
    docs, notice = seeds_for(project, row["version"])
    out: dict = {"version": row["version"], "status": row["status"], "at": utcnow(), "notices": [notice] if notice else [],
                 "seeds": [{"benchmark_id": d.benchmark_id, "set": d.set} for d in docs], "platforms": {}}
    for prof in profiles:
        results = [evaluate(query, d, prof) for d in docs]
        out["platforms"][prof.platform] = {"profile": prof.model_dump(), "recall": recall(results),
                                           "seeds": [{**r.as_dict(), "why": why(r.as_dict())} for r in results]}
    from . import diagnostics

    d = None
    coverage_plot.attach(out)
    if write:
        st = stamp()
        d = project.run_dir("query", st)
        i = 1
        while (d / "dry_run.json").exists():
            i += 1
            d = project.run_dir("query", f"{st}-{i}")
    diagnostics.attach(out, query, docs, profiles, d)   # grid, minimal cover, term report (D7); grid.csv/.svg in d
    if d is not None:
        (d / "dry_run.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        from .rounds import record_dry_run; record_dry_run(project, out)   # noqa: E702, I001 (the rounds list reads it, D8)
        coverage_plot.write_plot(d, out)
        out["run_dir"] = str(d.relative_to(project.root))
        out["grid_files"] = [f"{out['run_dir']}/grid.csv", f"{out['run_dir']}/grid.svg"]
        from ..interview import bank  # the refinement interview reads the new evidence (2b, F14)
        iv = bank.safe_trigger(project, "dry_run")
        out["interview"] = {**iv, "line": bank.notice(iv)}
    return out
