from __future__ import annotations

from pathlib import Path

from ..core.models import EmbeddingConfig
from .base import Embedder
from .cache import EmbeddingCache


def make_embedder(cfg: EmbeddingConfig, cache_path: str | Path | None = None) -> Embedder:
    cache = EmbeddingCache(cache_path) if cache_path else None
    k = cfg.kind
    if k in ("openai", "openai_compat"):
        from .openai_compat import OpenAICompatEmbedder
        return OpenAICompatEmbedder(cfg, cache)
    if k == "ollama":
        from .ollama import OllamaEmbedder
        return OllamaEmbedder(cfg, cache)
    if k == "sentence_transformers":
        from .sentence_transformers import SentenceTransformersEmbedder
        return SentenceTransformersEmbedder(cfg, cache)
    if k == "fake":
        from .fake import FakeEmbedder
        return FakeEmbedder(cfg, cache)
    raise ValueError(f"unknown embedding kind {k!r}")


PROBE_TEXTS = [
    "Short daily team meetings reduce coordination errors on shared tasks.",
    "Daily team meetings reduce coordination errors on shared projects.",
    "Phytoplankton bloom dynamics respond to sea-surface temperature anomalies.",
]


SANITY_ERROR = "sanity check failed: related pair scored no higher than the unrelated pair"


def probe(cfg: EmbeddingConfig, cache_path=None, project=None) -> dict:
    """Embed the fixed synthetic sentences. A network, cloud or unknown backend needs ``probe`` consent in
    ``project`` (``core/backend_consent.py``); without a project only local and test backends are probed."""
    import time

    from ..core.backend_consent import assess, require_backend_consent

    if project is not None:
        require_backend_consent(project, cfg, "probe", embedding=True)
    elif assess(cfg, embedding=True)["needs"]:
        raise PermissionError(f"embedding backend {cfg.name} needs a project to check consent; nothing was sent")
    e = make_embedder(cfg, cache_path)
    t0 = time.monotonic()
    try:
        m = e.embed(PROBE_TEXTS)
    except Exception as ex:  # report, do not crash the CLI/UI
        return {"name": cfg.name, "kind": cfg.kind, "model": cfg.model, "ok": False, "error": str(ex)[:400]}
    sims = m @ m.T
    sanity = bool(sims[0, 1] > sims[0, 2])
    out = {"name": cfg.name, "kind": cfg.kind, "model": cfg.model, "ok": sanity, "dim": int(m.shape[1]),
           "elapsed_s": round(time.monotonic() - t0, 2),
           "sim_related": round(float(sims[0, 1]), 3), "sim_unrelated": round(float(sims[0, 2]), 3),
           "sanity": sanity, "identity": e.identity()}
    if not sanity:   # it embedded, but the vectors do not tell related from unrelated text
        out["error"] = SANITY_ERROR
    return out
