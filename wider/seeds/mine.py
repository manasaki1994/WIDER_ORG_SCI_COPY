"""Candidate names from the seed papers (vision change 2b, design D11).

Pass 1, without a model (:func:`mine_deterministic`): the Keywords line, phrases introduced by "also called", "also
known as", "known as", "termed", "referred to as" or "so-called", quoted phrases, capitalised multi-word phrases that
recur, and the word pairs that co-occur with a mined name in the titles of the retrieved records. Each candidate
keeps every source (seed, section, quote), its strongest pattern and a count. Pass 2 (the model) lives in
:func:`mine_model`. A run is written to ``runs/seeds/mine/<stamp>/`` (``candidates.json``, ``run.json``,
``calls.json`` when a model ran); the newest run is the current candidate list. **No network in pass 1.**
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.project import Project

# strongest first: a candidate seen through several patterns is shown with the first one in this list
PATTERNS = ("also called", "also known as", "known as", "termed", "referred to as", "so-called", "keywords",
            "quoted", "capitalised", "title co-occurrence", "model")
INTRO_RE = re.compile(r"\b(also called|also known as|(?<!also )known as|termed|referred to as|so-called)\s+"
                      r"[\"“‘']?([A-Za-z][\w-]*(?:\s+[A-Za-z][\w-]*){0,5})", re.IGNORECASE)
KEYWORDS_RE = re.compile(r"^\s*key\s?words?\s*[:—–-]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
QUOTED_RE = re.compile(r"[\"“]([^\"”\n]{3,80})[\"”]")
CAPS_RE = re.compile(r"\b([A-Z][a-z]+(?:[ -](?:of |and |the )?[A-Z][a-z]+)+)\b")
# a phrase ends at the first of these words (the rest of the sentence is not part of the name)
STOP = {"a", "an", "and", "are", "as", "at", "be", "by", "can", "for", "from", "has", "have", "in", "into", "is",
        "it", "its", "links", "may", "of", "on", "or", "that", "the", "their", "these", "this", "to", "was", "were",
        "which", "while", "who", "with", "we", "our", "but", "not", "also", "such"}
LEAD = {"a", "an", "the", "this", "that", "these", "our", "its", "their"}
# a name also ends before these (adverbs, verbs and conjunctions that follow a name in a sentence); not "than", which
# is part of names such as "better than average effect" (spec-checker F-R2-5)
NAME_END = {"here", "there", "now", "then", "today", "grew", "grows", "became", "becomes", "remains", "remain",
            "matters", "matter", "rose", "fell", "appears", "appear", "emerged", "emerges", "below", "above",
            "throughout", "because", "when", "where", "if", "since", "after", "before", "during", "until",
            "whereas", "although", "though", "however", "thus", "hence"}
# words between a name and "termed" / "known as" / "referred to as" that are not part of the name before it
AUX = {"is", "are", "was", "were", "be", "been", "being", "often", "commonly", "sometimes", "usually", "also",
       "here", "now", "generally", "widely", "frequently", "typically", "hereafter", "henceforth"}
SKIP_SECTIONS = ("references", "reference list", "bibliography")   # never mined, never sent
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def normalise(s: str) -> str:
    """Lower case, hyphens at line ends joined, quotes dropped, whitespace collapsed (the quote check and counts)."""
    s = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", s or "")
    s = re.sub(r"[\"“”‘’'`«»]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def name_key(s: str) -> str:
    """The key a name is merged on: normalised, without punctuation at the ends."""
    return normalise(s).strip(" .,;:()[]{}-–—")


@dataclass
class Source:
    seed: str
    section: str
    quote: str
    pattern: str
    passage: str | None = None   # a model candidate's passage reference ("B001:introduction:2")


@dataclass
class Candidate:
    name: str
    key: str
    pattern: str
    count: int = 0
    sources: list[Source] = field(default_factory=list)
    same_as: list[str] = field(default_factory=list)   # names introduced as the same thing ("X, also called Y")
    origin: str = "deterministic"                      # or "model"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trim(words: list[str]) -> str:
    out: list[str] = []
    for w in words:
        if (w.lower() in STOP or w.lower() in NAME_END) and out:
            break
        if w.lower() in STOP:
            continue
        out.append(w)
    return " ".join(out[:5]).strip(" .,;:-")


def _antecedent(before: str) -> str:
    """The phrase a sentence names before ", also called": the words back to the clause start, at most four."""
    clause = re.split(r"[.;:()!?]|\b(?:and|or|which|that)\b", before)[-1]
    words = [w for w in re.findall(r"[A-Za-z][\w-]*", clause)]
    while words and words[-1].lower() in AUX:   # "Silence is termed ..." names "Silence"
        words = words[:-1]
    words = words[-4:]
    led = bool(words) and words[0].lower() in LEAD
    while words and words[0].lower() in LEAD:
        words = words[1:]
    if led and len(words) == 1:   # "The effect is referred to as ...": a pointer back, not a name
        return ""
    return "" if all(w.lower() in STOP for w in words) else " ".join(words)


def _sentence(text: str, start: int, end: int) -> str:
    a = max(text.rfind(". ", 0, start), text.rfind("; ", 0, start))
    b = min([x for x in (text.find(". ", end), text.find("; ", end)) if x >= 0] or [len(text) - 1])
    return re.sub(r"\s+", " ", text[a + 1:b + 1]).strip()


class _Bag:
    def __init__(self) -> None:
        self.by_key: dict[str, Candidate] = {}

    def add(self, name: str, pattern: str, seed: str, section: str, quote: str, same_as: str | None = None,
            origin: str = "deterministic", passage: str | None = None) -> None:
        k = name_key(name)
        if len(k) < 3 or k in STOP or not re.search(r"[a-z]", k):
            return
        c = self.by_key.get(k)
        if c is None:
            c = self.by_key[k] = Candidate(name=re.sub(r"\s+", " ", name).strip(" .,;:"), key=k, pattern=pattern,
                                           origin=origin)
        elif PATTERNS.index(pattern) < PATTERNS.index(c.pattern):
            c.pattern = pattern
        if origin == "deterministic":
            c.origin = "deterministic"
        c.count += 1
        c.sources.append(Source(seed=seed, section=section, quote=quote[:400], pattern=pattern, passage=passage))
        if same_as and name_key(same_as) != k and name_key(same_as) not in c.same_as:
            c.same_as.append(name_key(same_as))

    def sorted(self) -> list[Candidate]:
        return sorted(self.by_key.values(), key=lambda c: (PATTERNS.index(c.pattern), -c.count, c.key))


def mine_deterministic(sections: dict[str, list[dict[str, str]]], retrieved_titles: list[str] | None = None,
                       keywords: dict[str, str] | None = None) -> list[Candidate]:
    """Candidates from ``sections`` (seed id → ``[{name, text}]``), the seeds' keywords fields (seed id → "a; b")
    and the titles of the retrieved records. The reference section is not mined for names (it feeds the streams)."""
    bag = _Bag()
    for seed, secs in sections.items():
        for sec in secs:
            if sec["name"] in SKIP_SECTIONS:
                continue
            for m in KEYWORDS_RE.finditer(sec["text"]):
                for part in re.split(r"[;,·•|]", m.group(1)):
                    if part.strip():
                        bag.add(part.strip(), "keywords", seed, sec["name"], m.group(0).strip())
            text = re.sub(r"\s*\n\s*", " ", sec["text"])   # sentences run across the PDF's line breaks
            intro_spans = []
            for m in INTRO_RE.finditer(text):
                name = _trim(m.group(2).split())
                if not name:
                    continue
                pattern = m.group(1).lower()
                ante = "" if pattern == "so-called" else _antecedent(text[:m.start()].rstrip(" ,("))
                bag.add(name, pattern, seed, sec["name"], _sentence(text, m.start(), m.end()), same_as=ante or None)
                intro_spans.append((m.start(2), m.start(2) + len(name) + 2))
            for m in QUOTED_RE.finditer(text):
                inner = m.group(1).strip()
                if 1 <= len(inner.split()) <= 5 and not any(a <= m.start(1) < b for a, b in intro_spans):
                    bag.add(inner, "quoted", seed, sec["name"], _sentence(text, m.start(), m.end()))
            caps = Counter(m.group(1) for m in CAPS_RE.finditer(text))
            for phrase, n in caps.items():
                if n >= 2 and phrase.split()[0].lower() not in LEAD:
                    first = text.find(phrase)
                    for _ in range(n):
                        bag.add(phrase, "capitalised", seed, sec["name"], _sentence(text, first, first + len(phrase)))
    for seed, kw in (keywords or {}).items():
        for part in re.split(r"[;,]", kw or ""):
            if part.strip():
                bag.add(part.strip(), "keywords", seed, "keywords field", kw)
    if retrieved_titles:
        _title_cooccurrence(bag, retrieved_titles)
    return bag.sorted()


def _title_cooccurrence(bag: _Bag, titles: list[str]) -> None:
    """Word pairs (no stop word) that appear in at least two retrieved titles holding an already mined name."""
    names = [c.key for c in bag.by_key.values()]
    pairs: Counter[str] = Counter()
    quote: dict[str, str] = {}
    for t in titles:
        nt = normalise(t)
        if not any(n in nt for n in names):
            continue
        words = re.findall(r"[a-z][a-z-]+", nt)
        seen = set()
        for a, b in zip(words, words[1:]):
            pair = f"{a} {b}"
            if a in STOP or b in STOP or pair in seen or any(pair == n or pair in n for n in names):
                continue
            seen.add(pair)
            pairs[pair] += 1
            quote.setdefault(pair, t)
    for pair, n in pairs.items():
        if n >= 2:
            for _ in range(n):
                bag.add(pair, "title co-occurrence", "retrieved", "titles", quote[pair])


# ------------------------------------------------------------------------------------------------ runs
def mine_dir(project: Project) -> Path:
    return project.path("runs", "seeds", "mine")


def latest_run(project: Project) -> Path | None:
    d = mine_dir(project)
    runs = sorted(x for x in d.iterdir() if x.is_dir() and (x / "candidates.json").exists()) if d.exists() else []
    return runs[-1] if runs else None


def candidates(project: Project) -> list[dict[str, Any]]:
    """The newest mining run's candidates (``[]`` before the first run). A seed moved to Validation since that run
    drops out at once: its sources are left out and a name found only in it is not listed, asked about or admitted
    (blinding; spec-checker F-R2-3). The run folder keeps what was mined."""
    run = latest_run(project)
    if run is None:
        return []
    out = json.loads((run / "candidates.json").read_text(encoding="utf-8"))["candidates"]
    held = {b.benchmark_id for b in project.read_benchmark() if b.set != "Development"}
    if not held:
        return out
    kept = []
    for c in out:
        src = [s for s in c["sources"] if s["seed"] not in held]
        if src:
            kept.append({**c, "sources": src, "count": len(src)} if len(src) != len(c["sources"]) else c)
    return kept


VALIDATION_SKIPPED = ("{ids}: validation paper(s), not mined: names from a validation paper would shape the query "
                      "it is meant to test. Move a paper to Development on the Setup page (`wider benchmark set ID "
                      "Development`) to mine it.")


def _inputs(project: Project, benchmark_id: str | None) -> tuple[dict[str, list[dict[str, str]]], dict[str, str], list[str]]:
    from . import pdf

    papers = [b for b in project.read_benchmark() if benchmark_id in (None, b.benchmark_id)]
    if benchmark_id and not papers:
        raise KeyError(f"no seed paper {benchmark_id}")
    if benchmark_id and papers[0].set != "Development":
        raise ValueError(VALIDATION_SKIPPED.format(ids=benchmark_id))
    papers = [b for b in papers if b.set == "Development"]   # validation seeds stay out of query development
    sections = {}
    for b in papers:
        t = pdf.load_text(project, b.benchmark_id)
        if t is not None:
            sections[b.benchmark_id] = t["sections"]
    kw = {r["benchmark_id"]: r["keywords"] for r in project.store.query(
        "SELECT benchmark_id, keywords FROM benchmark_papers WHERE keywords IS NOT NULL AND keywords != ''")
        if r["benchmark_id"] in {b.benchmark_id for b in papers}}
    titles = [r["title"] for r in project.store.query("SELECT title FROM records WHERE title IS NOT NULL")]
    return sections, kw, titles


def validation_skipped(project: Project) -> list[str]:
    """Validation seeds that have an extracted text or a keywords field, which mining leaves out."""
    from . import pdf

    kw = {r["benchmark_id"] for r in project.store.query(
        "SELECT benchmark_id FROM benchmark_papers WHERE keywords IS NOT NULL AND keywords != ''")}
    return [b.benchmark_id for b in project.read_benchmark()
            if b.set != "Development" and (b.benchmark_id in kw or pdf.load_text(project, b.benchmark_id) is not None)]


def mine(project: Project, benchmark_id: str | None = None, model: bool = True, provider=None) -> dict[str, Any]:
    """Run pass 1 (and pass 2 unless ``model`` is False) over the extracted seed texts and the keywords fields;
    write the run folder. ``ValueError`` when there is nothing to mine (no extracted text and no keywords)."""
    from ..core.provenance import stamp, utcnow

    sections, kw, titles = _inputs(project, benchmark_id)
    skipped = validation_skipped(project) if benchmark_id is None else []
    if not sections and not kw:
        if skipped:   # the texts are there, but only on validation papers (spec-checker F-R2-2)
            raise ValueError("nothing to mine: " + VALIDATION_SKIPPED.format(ids=", ".join(skipped)))
        raise ValueError("nothing to mine: add a PDF to a seed paper and extract it (wider seeds extract), "
                         "or type the seeds' keywords")
    cands = mine_deterministic(sections, titles, kw)
    det = len(cands)
    st = stamp()
    model_res: dict[str, Any] = {"ran": False, "message": "model pass not asked for (--no-model)"}
    if model:
        model_res = mine_model(project, sections, provider, run_ref=f"seeds/mine/{st}")
        cands = _merge(cands, model_res.pop("candidates", []))
    run = mine_dir(project) / st
    run.mkdir(parents=True, exist_ok=True)
    body = {"stamp": st, "at": utcnow(), "seeds": sorted(sections), "keywords_fields": sorted(kw),
            "candidates": [c.to_dict() for c in cands]}
    (run / "candidates.json").write_text(json.dumps(body, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    calls = model_res.pop("calls", None)
    if calls is not None:
        (run / "calls.json").write_text(json.dumps(calls, indent=1) + "\n", encoding="utf-8")
    (run / "run.json").write_text(json.dumps({"stamp": st, "seeds": sorted(sections), "deterministic": det,
                                              "model": {k: v for k, v in model_res.items()}}, indent=1,
                                             ensure_ascii=False) + "\n", encoding="utf-8")
    from ..interview import bank  # new names can raise name-add and same-construct questions (2b, F14)
    iv = bank.safe_trigger(project, "mine")
    return {"folder": str(run.relative_to(project.root)), "seeds": sorted(sections), "deterministic": det,
            "validation_skipped": skipped,
            "validation_notice": VALIDATION_SKIPPED.format(ids=", ".join(skipped)) if skipped else "",
            "candidates": [c.to_dict() for c in cands], "model": model_res, "interview": iv,
            "interview_notice": bank.notice(iv)}


def _merge(cands: list[Candidate], extra: list[Candidate]) -> list[Candidate]:
    bag = _Bag()
    bag.by_key = {c.key: c for c in cands}
    for c in extra:
        for s in c.sources:
            bag.add(c.name, "model", s.seed, s.section, s.quote, origin="model", passage=s.passage)
    return bag.sorted()


MODEL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["candidates"], "properties": {
    "candidates": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["name", "kind", "quote", "reasoning"],
        "properties": {"name": {"type": "string"}, "kind": {"type": "string"}, "quote": {"type": "string"},
                       "reasoning": {"type": "string"}}}}}}
MODEL_SYSTEM = ("You read one passage of a research paper and list the names the passage uses for the constructs, "
                "methods or phenomena it studies, including alternative names (\"also called\") and abbreviations. "
                "For each name give its kind (construct, method, phenomenon, measure or other), a quote copied "
                "word for word from the passage that contains the name, and one sentence of reasoning. List only "
                "names the passage itself uses; an empty list is a valid answer.")


def system_prompt(project: Project) -> str:
    """:data:`MODEL_SYSTEM` with the refinement interview's decisions under "Decisions from the researcher" (2b)."""
    from ..core.questions import clarifications
    ds = clarifications(project, "query")
    if not ds:
        return MODEL_SYSTEM
    return MODEL_SYSTEM + "\n\nDecisions from the researcher (oldest first):\n" + "\n".join(f"- {d}" for d in ds)


CALLS_PER_ITEM = 3            # one call plus at most two repair rounds or retries (llm-backends delta)
USAGE_STAGE = "seed_mining"   # the llm_usage stage of mining calls (2c's provenance joins on it); backend: construct
# the tool gate moved to providers/gate.py (2c, D15), shared with the second reader; re-exported for older imports
from ..providers.gate import TOOL_OFF_FLAGS, missing_tool_flags, tool_gate  # noqa: E402,F401


def passages(sections: dict[str, list[dict[str, str]]], tokens: int = 1500) -> list[dict[str, str]]:
    """Every mined section cut into passages of at most ``tokens`` (characters ÷ the cost code's 4), at sentence
    ends where it can: ``[{id, seed, section, text}]``. The reference list is not sent."""
    from ..core.estimate import CHARS_PER_TOKEN

    limit = max(200, tokens * CHARS_PER_TOKEN)
    out: list[dict[str, str]] = []
    for seed, secs in sections.items():
        for sec in secs:
            if sec["name"] in SKIP_SECTIONS:
                continue
            text = re.sub(r"\s+", " ", re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", sec["text"])).strip()
            n, buf = 0, ""
            for sent in SENTENCE_RE.split(text):
                while len(sent) > limit:          # a sentence longer than a passage is cut at the limit
                    if buf:
                        out.append({"seed": seed, "section": sec["name"], "text": buf})
                        buf = ""
                    out.append({"seed": seed, "section": sec["name"], "text": sent[:limit]})
                    sent = sent[limit:]
                if buf and len(buf) + 1 + len(sent) > limit:
                    out.append({"seed": seed, "section": sec["name"], "text": buf})
                    buf = ""
                buf = f"{buf} {sent}".strip()
            if buf:
                out.append({"seed": seed, "section": sec["name"], "text": buf})
            for p in out:
                if "id" not in p:
                    n += 1
                    p["id"] = f"{seed}:{sec['name']}:{n}"
    return out


def rules_request(system: str, user: str, schema: dict, seed: int):
    """One call under the call rules (llm-backends delta): temperature 0 and the project seed; pass it to
    ``provider.complete(req, retries=CALLS_PER_ITEM)`` so an item takes at most three calls, the schema repairs
    (one, or two when the backend's ``repairs`` is 2) among them. 2c's second reader opts in through this."""
    from ..providers.base import ChatRequest

    return ChatRequest(system=system, user=user, schema=schema, temperature=0.0, seed=seed)


SWITCH_OFF = ("the model pass is skipped: the backend {name} is {loc}, and seed_pdfs.passages_to_cloud is off, so no "
              "seed-paper passage goes to a backend that is not local (turn it on with `wider seeds policy "
              "passages-to-cloud on` or in Settings, Seed papers)")


def target(project: Project, provider=None):
    """The backend the model pass would send to and where it runs, without sending anything: ``(cfg, locality)``.
    A ready ``provider`` (tests) carries its own locality; otherwise the stage ``construct`` backend is assessed as
    the consent check does (a loopback Ollama's tag list is the only request)."""
    from ..core import backend_consent as bc
    from ..core.summary import provider_locality

    if provider is not None:
        cfg = provider.cfg
        return cfg, provider.locality or provider_locality(cfg.kind, cfg.base_url)
    cfg = project.provider_for("construct")
    return cfg, bc.assess(cfg)["locality"]


def model_gate(project: Project, cfg, loc: str) -> str | None:
    """Why the model pass may not send a passage to this backend, or ``None`` when it may (consent is asked after
    this, so a skipped pass never asks). A CLI backend whose command does not hold its tool's tool-disabling flags
    gets no passage (the command is read, not the preset label); a backend that is not ``local`` or ``test`` gets
    passages only under ``seed_pdfs.passages_to_cloud`` (the privacy rule's named exception)."""
    why = tool_gate(cfg)
    if why:
        return f"the model pass is skipped: {why}, so it gets no passage"
    if loc not in ("local", "test") and not project.config.seed_pdfs.passages_to_cloud:
        return SWITCH_OFF.format(name=cfg.name, loc=loc)
    return None


def needs_consent(project: Project, provider=None) -> list[tuple[Any, str]]:
    """The consents the model pass still lacks, as ``(cfg, operation)`` in asking order (``research``, then
    ``passages``); empty when the pass is skipped anyway or the backend needs none. The CLI asks for them first."""
    from ..core import backend_consent as bc

    try:
        cfg, loc = target(project, provider)
    except (KeyError, TypeError):
        return []   # no backend: the run itself reports it
    if model_gate(project, cfg, loc):
        return []
    out = []
    for op in ("research", "passages"):
        try:
            bc.require_backend_consent(project, cfg, op)
        except bc.BackendConsentRequired:
            out.append((cfg, op))
    return out


def mine_model(project: Project, sections, provider=None, run_ref: str | None = None) -> dict[str, Any]:
    """Pass 2: each passage to the stage ``construct`` backend with a flat schema; a candidate is kept only when its
    quote is in the passage after :func:`normalise`. Temperature 0 and the project seed, at most three calls per
    passage; ``calls`` holds hashes, attempts and repairs, never text. ``provider`` is a ready adapter (tests)."""
    import asyncio

    from ..core.provenance import sha256_text
    from ..core.usage import track

    if not sections:
        return {"ran": False, "message": "the model pass needs an extracted seed text (wider seeds extract)"}
    from ..core import backend_consent as bc

    cfg, loc = target(project, provider)
    why = model_gate(project, cfg, loc)
    if why:   # the run record still names the backend that was chosen (its snapshot, with its command line)
        if provider is None:
            from ..providers.registry import backend_identity
            return {"ran": False, "message": why, "provider": backend_identity(cfg)}
        return {"ran": False, "message": why, "provider": provider.identity()}
    if provider is None:   # research consent (raises before anything is sent), then the adapter
        from ..providers.registry import research_provider
        provider = research_provider(project, cfg)
    bc.require_backend_consent(project, cfg, "research")
    bc.require_backend_consent(project, cfg, "passages")   # asked once per backend, remembered (2b, 3.5)
    tokens = int(project.config.seed_pdfs.passage_tokens or 1500)
    items = passages(sections, tokens)
    seed = cfg.seed if cfg.seed is not None else 1
    system = system_prompt(project)

    async def one(p: dict[str, str]):
        req = rules_request(system, f"Passage ({p['section']}):\n{p['text']}", MODEL_SCHEMA, seed)
        return p, req, await provider.complete(req, retries=CALLS_PER_ITEM)

    async def run_all():
        return await asyncio.gather(*(one(p) for p in items))

    with track(project, USAGE_STAGE, run_ref):
        results = asyncio.run(run_all())
    kept: list[Candidate] = []
    calls, dropped, failed = [], 0, 0
    for p, req, res in results:
        rec = {"passage": p["id"], "seed": p["seed"], "section": p["section"], "chars": len(p["text"]),
               "input_sha256": res.input_sha256 or req.input_sha256, "passage_sha256": sha256_text(p["text"]),
               "output_sha256": sha256_text(res.text) if res.text else None, "attempts": res.attempts,
               "repairs": res.repairs, "ok": res.ok, "error": (res.error or "")[:200] or None,
               "temperature": req.temperature, "sampling_seed": req.seed, "kept": 0, "dropped": 0}
        if not res.ok:
            failed += 1
        else:
            hay = normalise(p["text"])
            for c in (res.parsed or {}).get("candidates", []):
                q, name = normalise(c.get("quote") or ""), (c.get("name") or "").strip()
                if not q or not name or q not in hay or name_key(name) not in hay:
                    dropped += 1
                    rec["dropped"] += 1
                    continue
                rec["kept"] += 1
                kept.append(Candidate(name=name, key=name_key(name), pattern="model", count=1, origin="model",
                                      sources=[Source(seed=p["seed"], section=p["section"], quote=c["quote"][:400],
                                                      pattern="model", passage=p["id"])]))
        calls.append(rec)
    parts = [f"{len(kept)} kept"]
    if dropped:
        parts.append(f"{dropped} dropped: quote not found")
    if failed:
        parts.append(f"{failed} passage(s) failed after {CALLS_PER_ITEM} calls at most")
    out = {"ran": True, "message": "model pass: " + ", ".join(parts), "kept": len(kept), "dropped": dropped,
           "failed": failed, "passages": len(items), "calls": calls, "candidates": kept,
           "provider": provider.identity()}
    if cfg.kind == "cli_agent":
        out["sampling"] = "a cli_agent backend takes no temperature or seed; the request carried them unused"
    return out
