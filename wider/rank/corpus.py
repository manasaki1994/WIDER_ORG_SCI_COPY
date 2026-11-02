"""Corpus embeddings: title + abstract + cleaned keywords, cut at max_words, cached per model, stored as .npy."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import numpy as np

from ..core.backend_consent import require_backend_consent
from ..core.project import Project
from ..core.provenance import sha256_text, utcnow
from ..core.text import truncate_words, word_count
from ..core.usage import in_scope, track
from ..embeddings import make_embedder

STOP_KEYWORDS = {  # database check tags and indexing boilerplate (MeSH/Embase style), not topic words
    "human", "humans", "adult", "adults", "female", "male", "animal", "animals", "article", "review", "child",  # topic-ok: MeSH check tags
    "children", "adolescent", "adolescents", "aged", "middle aged", "middle-aged", "young adult", "infant",
    "priority journal", "controlled study", "major clinical study", "retrospective study", "prospective study",
    "cross-sectional study", "questionnaire", "procedures", "statistics", "aged, 80 and over", "very elderly",
    "school child", "clinical article", "note", "letter", "editorial", "united states", "usa",
}


def clean_keywords(kw: str | None) -> str:
    if not kw:
        return ""
    terms = [t.strip() for t in re.split(r"[;,|]", kw) if t.strip()]
    out, seen = [], set()
    for t in terms:
        low = t.lower()
        if low in STOP_KEYWORDS or low in seen:
            continue
        seen.add(low)
        out.append(t)
    return "; ".join(out)


BOILERPLATE = [
    re.compile(r"\(?\s*PsycI(?:nfo|NFO) Database Record.*?all rights reserved\.?\)?", re.I | re.S),
    re.compile(r"\(?\s*PsycI(?:nfo|NFO) Database Record[^)]*\)", re.I),
    re.compile(r"(?:©|\(c\)|copyright)\s*\d{4}[^.]*\.?", re.I),
    re.compile(r"all rights reserved\.?", re.I),
    re.compile(r"\[?abstract from author\]?", re.I),
]


def strip_boilerplate(text: str | None) -> str:
    """Remove database copyright notices that otherwise dominate embeddings and cluster terms."""
    t = text or ""
    for rx in BOILERPLATE:
        t = rx.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def record_text(title: str | None, abstract: str | None, keywords: str | None, max_words: int, use_keywords: bool = True) -> str:
    parts = [title or "", strip_boilerplate(abstract), clean_keywords(keywords) if use_keywords else ""]
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return truncate_words(text, max_words)


def corpus_texts(project: Project) -> tuple[list[int], list[str], list[bool]]:
    mw = project.config.thresholds.max_words
    thin_w = project.config.thresholds.thin_words
    ids, texts, thin = [], [], []
    for r in project.store.query("SELECT record_id, title, abstract, keywords FROM records ORDER BY record_id"):
        ids.append(r["record_id"])
        texts.append(record_text(r["title"], r["abstract"], r["keywords"], mw))
        thin.append(word_count(r["abstract"]) < thin_w)
    return ids, texts, thin


def text_hash(ids: list[int], texts: list[str]) -> str:
    h = hashlib.sha256()
    for i, t in zip(ids, texts):
        h.update(f"{i}\x1f{t}\x1e".encode())
    return h.hexdigest()


def embed_corpus(project: Project, embedding_name: str | None = None, progress=None) -> dict:
    """Embed every unique record with the named embedding; cached in embeddings/cache.sqlite and saved as .npy."""
    cfg = project.embedding(embedding_name)
    ids, texts, _ = corpus_texts(project)
    if not ids:
        raise ValueError("no unique records; run wider dedup run first")
    th = text_hash(ids, texts)
    set_name = cfg.name
    row = project.store.one("SELECT * FROM embedding_sets WHERE set_name=?", (set_name,))
    out_file = project.path("embeddings", f"{set_name}.npy")
    if row and row["text_hash"] == th and out_file.exists():
        return _done(project, {"set_name": set_name, "n": len(ids), "dim": row["dim"], "cached": True, "file": str(out_file)})
    require_backend_consent(project, cfg, "research", embedding=True)   # before any text is sent
    emb = make_embedder(cfg, project.path("embeddings", "cache.sqlite"))
    with track(project, "rank"):   # one usage row per API batch (cached texts are not sent and add none)
        m = emb.embed(texts, progress=progress)
    np.save(out_file, m.astype(np.float32))
    np.save(project.path("embeddings", f"{set_name}.ids.npy"), np.asarray(ids, dtype=np.int64))
    project.store.execute(
        "INSERT INTO embedding_sets(set_name,model,kind,dim,n,text_hash,file,created_at) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(set_name) DO UPDATE SET model=excluded.model, kind=excluded.kind, dim=excluded.dim, n=excluded.n, "
        "text_hash=excluded.text_hash, file=excluded.file, created_at=excluded.created_at",
        (set_name, cfg.model, cfg.kind, int(m.shape[1]), len(ids), th, out_file.name, utcnow()))
    return _done(project, {"set_name": set_name, "n": len(ids), "dim": int(m.shape[1]), "cached": False, "file": str(out_file)})


def _done(project: Project, res: dict) -> dict:
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_embed
    record(project, summarize_embed(res, project.root))
    return res


def load_corpus_embeddings(project: Project, set_name: str) -> tuple[np.ndarray, np.ndarray]:
    row = project.store.one("SELECT * FROM embedding_sets WHERE set_name=?", (set_name,))
    if row is None:
        raise KeyError(f"embedding set {set_name!r} not found; run wider embed corpus")
    m = np.load(project.path("embeddings", row["file"]))
    ids = np.load(project.path("embeddings", f"{set_name}.ids.npy"))
    return ids, m


def embed_texts(project: Project, embedding_name: str | None, texts: list[str]) -> np.ndarray:
    cfg = project.embedding(embedding_name)
    require_backend_consent(project, cfg, "research", embedding=True)   # before any text is sent
    emb = make_embedder(cfg, project.path("embeddings", "cache.sqlite"))
    if in_scope():   # the calling engine already logs against its own stage
        return emb.embed(texts)
    with track(project, "rank"):
        return emb.embed(texts)


def anchors_hash(anchors: dict[str, str]) -> str:
    return sha256_text("\x1e".join(f"{k}\x1f{v}" for k, v in sorted(anchors.items())))


def embeddings_dir(project: Project) -> Path:
    return project.path("embeddings")
