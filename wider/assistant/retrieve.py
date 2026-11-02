"""Finding the sections a question needs (guide-assistant D4-D6).

Keywords decide whether anything matches: BM25 over ``title + context + text`` of each section, with the question's
terms widened by the glossary's synonyms. When no section reaches ``KEYWORD_FLOOR``, or fewer than ``COVERAGE_FLOOR`` of
the question's words occur in WIDER's documents, nothing is embedded and no model is called. Meaning ranks the matched corpus again only when the project's default embedding backend runs without a
download or a new consent; both lists are merged by reciprocal rank fusion. Only the question and the shipped
sections are embedded."""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..guide.methods import Section
from .sections import all_sections, docs_key

K1, B = 1.2, 0.75
KEYWORD_FLOOR = 1.5         # the BM25 score the best section must reach (calibrated on questions.yaml, D11)
COVERAGE_FLOOR = 0.7        # the share of the question's words WIDER's documents must contain (calibrated there too)
MEANING_FLOOR = 0.3         # the cosine a section needs to enter the meaning list
RRF_K = 60
MAX_SECTIONS = 4
SYNONYM_WEIGHT = 0.7
IDLE_S = 600                # the UI server drops its loaded embedder after 10 minutes without a question

NOTE_NOT_DOWNLOADED = "Searched by keywords only: the embedding model is not downloaded."
NOTE_CONSENT = "Searched by keywords only: the embedding service needs your consent."
NOTE_UNAVAILABLE = "Searched by keywords only: the embedding model is not available."
NOTE_FAILED = "Searched by keywords only: the embedding backend did not answer."

STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can cannot could did do does doing done down during each else ever every few for from further get gets got
had has have having he her here hers him his how i if in into is it its itself just let me might more most must my
no nor not now of off on once only or other our ours out over own please same shall she should so some such than
that the their theirs them then there these they this those through to too under until up us very want was we were
what when where which while who whom why will with would you your yours
""".split())
KEEP_S = frozenset({"alias", "atlas", "bias", "canvas", "lens"})

# ------------------------------------------------------------------------------------------------ words
def stem(w: str) -> str:
    """A light suffix stemmer: -ing, -ed, -ies, -es (after s, x, z, ch, sh), -s (not -ss, -us, -is), then a final -e
    and a doubled consonant, so "sampled", "samples" and "sampling" meet at "sampl", "labelled" at "label"."""
    if len(w) <= 3 or w.isdigit():
        return w
    if w.endswith("ing") and len(w) > 5:
        w = w[:-3]
    elif w.endswith("ed") and len(w) > 4:
        w = w[:-2]
    elif w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("es") and w[:-2].endswith(("s", "x", "z", "ch", "sh")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us", "is")) and w not in KEEP_S:
        w = w[:-1]
    if w.endswith("e") and len(w) > 4:
        w = w[:-1]
    if len(w) > 3 and w[-1] == w[-2] and w[-1] not in "aeiouys":
        w = w[:-1]
    return w


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens without stop words, stemmed."""
    return [stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())
            if t not in STOP and (len(t) > 1 or t.isdigit())]


def _phrases(s: Section) -> list[str]:
    """A glossary section's term (a parenthesised part dropped) and its synonyms ("Also called: a; b. ...")."""
    head = re.sub(r"\s*\(.*?\)", "", s.title).strip()
    out = [head] + re.findall(r"\(([^)]*)\)", s.title)
    if s.text.startswith("Also called: "):
        out += [x.strip() for x in s.text[len("Also called: "):].split(". ", 1)[0].split(";") if x.strip()]
    return [p for p in out if p]


def synonym_groups(sections: list[Section]) -> list[list[tuple[str, ...]]]:
    """Per glossary entry, its phrases as stemmed token tuples."""
    groups = []
    for s in sections:
        if s.kind == "glossary":
            g = [t for t in (tuple(tokens(p)) for p in _phrases(s)) if t]
            if len(g) > 1:
                groups.append(g)
    return groups


def _contains(seq: list[str], sub: tuple[str, ...]) -> bool:
    n = len(sub)
    return any(tuple(seq[i:i + n]) == sub for i in range(len(seq) - n + 1))


def query_terms(question: str, groups: list[list[tuple[str, ...]]]) -> dict[str, float]:
    """The question's terms (weight 1) plus, for each glossary entry one of whose phrases it contains, the entry's
    term and one-word synonyms (weight ``SYNONYM_WEIGHT``): "sensitivity" also searches "recall"."""
    q = tokens(question)
    out = {t: 1.0 for t in q}
    for g in groups:
        if any(_contains(q, p) for p in g):
            for i, p in enumerate(g):
                if i == 0 or len(p) == 1:
                    for t in p:
                        out.setdefault(t, SYNONYM_WEIGHT)
    return out


# ------------------------------------------------------------------------------------------------ keywords
def _indexed(s: Section) -> str:
    """``title + context + text``; a guide section's context line already holds its title, so it is counted once."""
    return f"{s.context} {s.text}" if s.title and s.title in s.context else f"{s.title} {s.context} {s.text}"


NAMED_MAX = 2
TOPIC_MAX = 2               # the sections of a named methods topic sent ahead of the ranking
PREFIX = 6                  # coverage: a word sharing its first six letters with a document word is known


class KeywordIndex:
    """BM25 (k1 1.2, b 0.75) over ``title + context + text`` of each section."""

    def __init__(self, sections: list[Section]):
        self.sections = sections
        self.tf = [Counter(tokens(_indexed(s))) for s in sections]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df = Counter(t for c in self.tf for t in c)
        n = len(sections)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self.prefixes = {t[:PREFIX] for t in df if len(t) >= PREFIX}
        self.groups = synonym_groups(sections)
        self.phrases = {i: [t for t in (tuple(tokens(p)) for p in _phrases(s)) if t]
                        for i, s in enumerate(sections) if s.kind == "glossary"}

    def named(self, question: str) -> list[int]:
        """The glossary entries whose term or synonym the question contains, longest phrase first, at most
        ``NAMED_MAX``; an entry whose phrase lies inside another named entry's phrase is left out ("validation
        sample" names that entry, not "sample")."""
        q = tokens(question)
        hits = []
        for i, ps in self.phrases.items():
            found = [p for p in ps if _contains(q, p)]
            if found:
                hits.append((max(found, key=len), i))
        hits.sort(key=lambda h: -len(h[0]))
        out: list[tuple[tuple[str, ...], int]] = []
        for p, i in hits:
            if not any(len(o) > len(p) and _contains(list(o), p) for o, _ in out):
                out.append((p, i))
        return [i for _, i in out[:NAMED_MAX]]

    def named_topics(self, question: str) -> list[int]:
        """The sections of the methods topic the question names, best keyword score first, at most ``TOPIC_MAX``: its
        id's words in a row ("recall estimate" names ``recall-estimate``) or every word of its title. With meaning on,
        the fused ranking alone pushed such a topic out of the context (M123, 31 Oct)."""
        q = tokens(question)
        qs = set(q)
        best: tuple[int, str] | None = None
        for s in self.sections:
            if s.kind != "methods" or not s.topic:
                continue
            by_id = tuple(tokens(s.topic.replace("-", " ")))
            title = set(tokens(s.title))
            n = len(by_id) if by_id and _contains(q, by_id) else len(title) if title and title <= qs else 0
            if n and (best is None or n > best[0]):
                best = (n, s.topic)
        if best is None:
            return []
        sc = self.scores(question)
        idx = [i for i, s in enumerate(self.sections) if s.kind == "methods" and s.topic == best[1]]
        return sorted(idx, key=lambda i: -sc[i])[:TOPIC_MAX]

    def scores(self, question: str) -> list[float]:
        terms = query_terms(question, self.groups)
        out = []
        for c, dl in zip(self.tf, self.len):
            s = 0.0
            for t, w in terms.items():
                f = c.get(t)
                if f:
                    s += w * self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avg))
            out.append(s)
        return out


_INDEXES: dict[str, KeywordIndex] = {}
_LOCK = threading.Lock()


def keyword_index(sections: list[Section], key: str | None = None) -> KeywordIndex:
    """The keyword index of this documents key (D22), built on the first question after an edit."""
    key = key or docs_key(sections)
    with _LOCK:
        idx = _INDEXES.get(key)
        if idx is None:
            if len(_INDEXES) >= 4:
                _INDEXES.pop(next(iter(_INDEXES)))
            idx = _INDEXES[key] = KeywordIndex(sections)
    return idx


def coverage(sections: list[Section], question: str, key: str | None = None) -> float:
    """The share of the question's distinct words (stemmed, stop words dropped) that occur anywhere in WIDER's
    documents, or share their first ``PREFIX`` letters with one ("estimator" and "estimate"): a question about bread or
    another program shares a word or two with them, not most of its words."""
    idx = keyword_index(sections, key)
    q = set(tokens(question))
    known = sum(t in idx.idf or (len(t) >= PREFIX and t[:PREFIX] in idx.prefixes) for t in q)
    return known / len(q) if q else 0.0


def keyword_match(sections: list[Section], question: str, key: str | None = None) -> list[tuple[Section, float]]:
    """``keyword_rank`` when the question matches WIDER's documents (D4: the best section reaches ``KEYWORD_FLOOR``
    and the question's words reach ``COVERAGE_FLOOR``), else ``[]``. Keywords alone decide; meaning never does."""
    kw = keyword_rank(sections, question, key)
    if not kw or kw[0][1] < KEYWORD_FLOOR or coverage(sections, question, key) < COVERAGE_FLOOR:
        return []
    return kw


def keyword_rank(sections: list[Section], question: str, key: str | None = None) -> list[tuple[Section, float]]:
    """Sections with a keyword score above 0, best first (ties keep the section order), after the glossary entries
    the question names (D4: a matched term's entry comes first) and the sections of the methods topic it names."""
    idx = keyword_index(sections, key)
    sc = idx.scores(question)
    named = idx.named(question)
    named += [i for i in idx.named_topics(question) if i not in named]
    order = named + sorted((i for i, s in enumerate(sc) if s > 0 and i not in named), key=lambda i: -sc[i])
    return [(sections[i], sc[i]) for i in order]


# a question that only asks what to do now, next or first ("what should i do now", "What do I do first in WIDER?"):
# its words are stop words or these, so keywords cannot match it; it is answered from the page's "What to do" section
# with the page's guide and the project status (M124, 31 Oct: on the Model page it got "WIDER's documents do not
# explain this.")
WHAT_NEXT = re.compile(r"\b(?:what|where|how|which)\b.*\b(?:do|start|begin|go|step)\b.*\b(?:first|next|now|start|begin)\b"
                       r"|\bwhat\b\W*(?:now|next)\b|\bnext\s+steps?\b|\bget(?:ting)?\s+started\b|\bwhere\b.*\b(?:start|begin)\b",
                       re.I)
NEXT_WORDS = frozenset({"first", "next", "now", "start", "begin", "step", "go", "wider", "started", "getting", "get"})


def what_next(sections: list[Section], question: str, page: str | None) -> list[int]:
    """The sections that answer a bare what-to-do-next question on ``page`` (the page's "What to do", then the
    Overview's, which gives the order of the stages; in general the Overview's and the methods overview's short
    layer), or ``[]`` when the question asks something more."""
    if not WHAT_NEXT.search(question) or set(tokens(question)) - {stem(w) for w in NEXT_WORDS}:
        return []
    pos = {s.id: i for i, s in enumerate(sections)}
    ids = ([f"guide/{page}/what-to-do"] if page and page != "general" else []) + ["guide/overview/what-to-do"]
    if not page or page == "general":
        ids.append("methods/overview/short")
    return [pos[i] for i in dict.fromkeys(ids) if i in pos]


# ------------------------------------------------------------------------------------------------ meaning
_EMB: dict = {}             # {"ident": ..., "emb": Embedder, "timer": Timer}


def _drop_embedder() -> None:
    with _LOCK:
        _EMB.clear()


def _loaded(cfg):
    """One loaded embedder per process, dropped after ``IDLE_S`` without a question."""
    from ..embeddings.registry import make_embedder

    ident = (cfg.name, cfg.kind, cfg.model, cfg.dim, cfg.base_url)
    with _LOCK:
        if _EMB.get("ident") != ident:
            if _EMB.get("timer"):
                _EMB["timer"].cancel()
            _EMB.clear()
            _EMB.update(ident=ident, emb=make_embedder(cfg))
        elif _EMB.get("timer"):
            _EMB["timer"].cancel()
        t = threading.Timer(IDLE_S, _drop_embedder)
        t.daemon = True
        t.start()
        _EMB["timer"] = t
        return _EMB["emb"]


def _ollama_listed(cfg) -> bool | None:
    """Whether the local Ollama lists the model (None when its address is not the one WIDER reads tags from)."""
    from ..core.detect import _ollama_tags, http_get, tags_base

    base = tags_base((cfg.base_url or "http://127.0.0.1:11434").rstrip("/"))
    if base is None:
        return None
    tags = _ollama_tags(base, http_get, fresh=False) or {}
    names = {m.get("name") for m in tags.get("models") or [] if isinstance(m, dict)}
    model = cfg.model or ""
    return model in names or f"{model}:latest" in names or model.removesuffix(":latest") in names


def meaning_backend(project) -> tuple[object | None, str | None, dict | None]:
    """``(cfg, note, consent)``: the default embedding config when it may be used for Ask WIDER now, else the
    keyword-only note and, for a missing consent, the payload the "Allow <backend> for Ask WIDER…" button opens.
    Nothing is downloaded and nothing is sent here, apart from reading a loopback Ollama's tag list."""
    from ..core import backend_consent as bc
    from ..core.backends import is_downloaded

    if project is None:
        return None, None, None
    try:
        cfg = project.embedding()
    except (KeyError, TypeError):
        return None, None, None
    if cfg.kind == "fake":
        return cfg, None, None
    if cfg.kind == "sentence_transformers":
        if is_downloaded(cfg) is not True:
            return None, NOTE_NOT_DOWNLOADED, None
        return cfg, None, None
    try:
        bc.require_backend_consent(project, cfg, "assistant", embedding=True)
    except bc.BackendConsentRequired as e:
        return None, NOTE_CONSENT, e.payload()
    if cfg.kind == "ollama" and _ollama_listed(cfg) is False:
        return None, NOTE_UNAVAILABLE, None
    return cfg, None, None


def index_path(project, cfg, key: str) -> Path:
    """``assistant/index/<embedding>-<hash>.npy``; the hash covers the documents key, the model and the dimension."""
    h = hashlib.sha256(f"{key}\0{cfg.kind}\0{cfg.model}\0{cfg.dim or 'full'}".encode()).hexdigest()[:16]
    return project.path("assistant", "index", f"{cfg.name}-{h}.npy")


def section_matrix(project, cfg, sections: list[Section], key: str) -> np.ndarray:
    """The embedded sections of this documents key, built once and kept beside a JSON list of ids; a new file removes
    the older files of the same embedding."""
    path = index_path(project, cfg, key)
    ids_path = path.with_suffix(".json")
    ids = [s.id for s in sections]
    if path.exists() and ids_path.exists():
        try:
            if json.loads(ids_path.read_text(encoding="utf-8")) == ids:
                return np.load(path)
        except (ValueError, OSError, EOFError):   # a file cut short (a crash mid-write) is built again
            pass
    m = _loaded(cfg).embed([f"{s.context}\n{s.title}\n{s.text}" for s in sections])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in path.parent.glob(f"{cfg.name}-*"):
        if old.stem != path.stem and old.suffix in (".npy", ".json"):
            old.unlink(missing_ok=True)
    buf = io.BytesIO()
    np.save(buf, m)
    _write_private(path, buf.getvalue())
    _write_private(ids_path, json.dumps(ids).encode("utf-8"))
    return m


def _write_private(path: Path, data: bytes) -> None:
    """Write ``data`` readable by the owner only (0600), through a temporary file replaced in one step."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def meaning_rank(project, cfg, sections: list[Section], question: str, key: str) -> list[int]:
    """Section indexes with a cosine at or above ``MEANING_FLOOR``, best first."""
    m = section_matrix(project, cfg, sections, key)
    q = _loaded(cfg).embed([question])[0]
    cos = m @ q
    return [int(i) for i in np.argsort(-cos, kind="stable") if cos[i] >= MEANING_FLOOR]


# ------------------------------------------------------------------------------------------------ retrieval
@dataclass
class Retrieval:
    sections: list[Section]                  # what is sent, glossary first
    retrieval: str = "keywords"              # "keywords" | "keywords and meaning"
    notes: list[str] = field(default_factory=list)
    consent: dict | None = None              # BackendConsentRequired.payload() for the "Allow ..." button
    matched: bool = False                    # whether the question matched (keyword_match), or asks what to do next
    key: str = ""                            # the documents key of the snapshot
    ranked: list[str] = field(default_factory=list)   # section ids in rank order before the cut (check-retrieval)


def _depth_pick(ranked: list[Section], by_id: dict[str, Section], depth: str) -> list[Section]:
    """D6: the topics the question matches; ``simpler`` sends the matched glossary entries and those topics' "In
    short" layers, ``detail`` their "Why" and "For auditors" layers."""
    top = ranked[:MAX_SECTIONS]
    topics = list(dict.fromkeys(s.topic for s in ranked if s.kind == "methods"))[:MAX_SECTIONS]
    layers = ("short",) if depth == "simpler" else ("why", "auditors")
    out = [s for s in top if s.kind == "glossary"] if depth == "simpler" else []
    for t in topics:
        out += [by_id[i] for i in (f"methods/{t}/{lay}" for lay in layers) if i in by_id]
    return out[:MAX_SECTIONS] or top


def retrieve(question: str, project=None, sections: list[Section] | None = None, depth: str | None = None,
             methods_root: Path | None = None, meaning: bool = True, page: str | None = None) -> Retrieval:
    """The sections to send for ``question`` asked on ``page`` (None: in general), at most ``MAX_SECTIONS``.
    ``sections`` is the snapshot of this question (read once, D22); without it the shipped files are read now.
    Keywords gate (a bare what-to-do-next question passes with its page's "What to do"); meaning (when allowed)
    reorders, but the named entries and topic and the best keyword section are always sent."""
    if sections is None:
        sections = all_sections(methods_root)
    key = docs_key(sections)
    nxt = what_next(sections, question, page)
    kw = keyword_match(sections, question, key)
    if not kw and not nxt:
        return Retrieval(sections=[], key=key)
    res = Retrieval(sections=[], matched=True, key=key)
    pos = {s.id: i for i, s in enumerate(sections)}
    kw_idx = list(dict.fromkeys(nxt + [pos[s.id] for s, _ in kw]))
    ranked_idx = kw_idx
    idx = keyword_index(sections, key)
    pinned = list(dict.fromkeys(nxt + idx.named(question) + idx.named_topics(question)))   # these stay first
    best = next((i for i in kw_idx if i not in pinned), None)   # the best keyword section, never pushed out by meaning
    cfg, note, consent = meaning_backend(project) if meaning else (None, None, None)
    if note:
        res.notes.append(note)
        res.consent = consent
    if cfg is not None:
        try:
            mean_idx = meaning_rank(project, cfg, sections, question, key)
        except Exception:  # a failing backend leaves keywords; the question is still answered
            res.notes.append(NOTE_FAILED)
        else:
            fused: dict[int, float] = {}
            for lst in (kw_idx, mean_idx):
                for r, i in enumerate(lst):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + r + 1)
            ranked_idx = pinned + [i for i in sorted(fused, key=lambda i: -fused[i]) if i not in pinned]
            if best is not None and len(pinned) < MAX_SECTIONS and best not in ranked_idx[:MAX_SECTIONS]:
                ranked_idx.remove(best)                 # M123 lead: the fake embedder's vectors pushed it out
                ranked_idx.insert(len(pinned), best)
            res.retrieval = "keywords and meaning"
    ranked = [sections[i] for i in ranked_idx]
    res.ranked = [s.id for s in ranked]
    if depth in ("simpler", "detail"):
        chosen = _depth_pick(ranked, {s.id: s for s in sections}, depth)
    else:
        chosen = ranked[:MAX_SECTIONS]
    res.sections = [s for s in chosen if s.kind == "glossary"] + [s for s in chosen if s.kind != "glossary"]
    return res
