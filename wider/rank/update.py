"""Update ranking (ui-redesign D5, owner round 6): "Rank again" made guided.

:func:`check` says whether the default embedding's vectors match the current texts. When they do, :func:`update`
ranks. When they do not, :func:`check` names what re-embedding sends (only the texts not already in the embedding
cache), where it runs (from the backend's configuration) and what it may cost; the caller asks the usual consent or
download confirmation, then :func:`update` re-embeds and ranks. A failed embedding adds no version and changes
nothing. ``use_older`` ranks from the stored embeddings instead, and that version keeps "needs review". Every run adds
a version (or reuses an identical one) without touching earlier versions or existing samples.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.project import Project

OLDER = "Ranked from the older embeddings: this ranking still needs review until the texts are embedded again."


def _samples_keep(project: Project) -> list[dict[str, Any]]:
    """The samples drawn from versions of the default ranking's base name: they keep their version."""
    from .versions import base_of, versions

    d = project.store.get_meta("default_ranking")
    if not d:
        return []
    return [{"version": v["version"], "samples": v["samples"]} for v in versions(project, base_of(project, d))
            if v["samples"]]


def _chosen_top_n(project: Project) -> int | None:
    """The top-N of the default ranking, when it still fits the newest anchors; else None (the protocol default)."""
    from ..construct import artifacts

    d = project.store.get_meta("default_ranking")
    r = project.store.one("SELECT top_n FROM rankings WHERE name=?", (d,)) if d else None
    _, data = artifacts.load(project, "anchors")
    if r is None or not data or not 1 <= r["top_n"] <= len(data.get("anchors") or {}):
        return None
    return int(r["top_n"])


def check(project: Project) -> dict[str, Any]:
    """Whether the embeddings match the current texts; when not, what re-embedding sends, where, at what cost, and
    whether consent or a download is still needed. Makes no request."""
    from ..core.backend_consent import status_of
    from ..core.backends import is_downloaded
    from ..core.estimate import cost_range, range_text
    from ..core.send_notice import _day
    from ..embeddings.base import Embedder
    from ..embeddings.cache import EmbeddingCache
    from .corpus import corpus_texts, text_hash

    cfg = project.embedding(None)
    ids, texts, _ = corpus_texts(project)
    if not ids:
        raise ValueError("no unique records; run wider dedup run first")
    row = project.store.one("SELECT * FROM embedding_sets WHERE set_name=?", (cfg.name,))
    match = bool(row and row["text_hash"] == text_hash(ids, texts) and row["model"] == cfg.model
                 and project.path("embeddings", row["file"]).exists())
    from .score import keep_note

    kept = _samples_keep(project)
    out: dict[str, Any] = {"embedding": cfg.name, "model": cfg.model, "embeddings_match": match,
                           "older_available": row is not None, "records": len(ids), "samples_keep": kept,
                           "keep_note": keep_note({"samples_keep": kept})}
    if match:
        return {**out, "changed": 0, "notice": None, "needs_consent": False, "needs_download": False}
    cache = EmbeddingCache(project.path("embeddings", "cache.sqlite"))
    try:
        hits = cache.get_many(Embedder.cache_key(SimpleNamespace(cfg=cfg)), texts)
    finally:
        cache.close()
    changed = [t for t, h in zip(texts, hits) if h is None]
    est = cost_range(project, cfg, "rank", [("", t) for t in changed], embedding=True, probe=False)
    st = status_of(project, cfg, embedding=True, probe=False)
    needs_consent = bool(st["needs_consent"]) and "research" not in st["operations"]
    download = cfg.kind == "sentence_transformers" and is_downloaded(cfg) is False
    n = len(changed)
    where = st["locality_label"]
    notice = (f"The embeddings were made from older texts. Updating sends {n} changed text{'' if n == 1 else 's'} "
              f"(title, abstract and keywords of each record) to the embedding backend {cfg.name} ({cfg.model}) at "
              f"{st['destination']} ({where.lower()}) to embed them again; the other "
              f"{len(texts) - n} come from the cache. {range_text(est)[0]}.")
    if download:
        notice += f" {cfg.model} is not downloaded yet: updating downloads its weights first."
    notice += (" WIDER asks before sending." if needs_consent else
               f" You allowed this on {_day(st.get('allowed_at'))}; change it in Settings."
               if st["needs_consent"] else "")
    return {**out, "changed": n, "estimate": est, "locality": st["locality"], "where": where,
            "destination": st["destination"], "needs_consent": needs_consent, "needs_download": download,
            "notice": notice}


def update(project: Project, use_older: bool = False, progress=None) -> dict[str, Any]:
    """Re-embed when the embeddings do not match the current texts (unless ``use_older``), then rank with the
    defaults. Raises before anything is stored when the embedding fails or consent is missing."""
    from .corpus import embed_corpus
    from .score import rank

    chk = check(project)
    older = False
    if not chk["embeddings_match"]:
        if use_older:
            if not chk["older_available"]:
                raise ValueError(f"no older embeddings of {chk['embedding']}; embed the corpus first")
            older = True
        else:
            embed_corpus(project, chk["embedding"], progress=progress)   # raises before a ranking is stored
    res = rank(project, top_n=_chosen_top_n(project))   # the chosen ranking's top-N, not the protocol default
    res["from_older_embeddings"] = older
    res["re_embedded"] = not chk["embeddings_match"] and not older
    res["changed_texts"] = chk["changed"]
    if older:
        res["note"] = OLDER
    return res
