"""Ollama native ``/api/embed``."""
from __future__ import annotations

import httpx
import numpy as np

from .base import Embedder, EmbeddingError, http_status_error


class OllamaEmbedder(Embedder):
    kind = "ollama"

    def _embed_batch(self, texts):
        base = (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        try:
            r = httpx.post(f"{base}/api/embed", json={"model": self.cfg.model, "input": list(texts)}, timeout=300)
        except httpx.ConnectError as e:
            raise EmbeddingError(f"cannot reach Ollama at {base}: {e}", retryable=True) from e
        if r.status_code >= 400:
            raise http_status_error(r)
        data = r.json()
        self.last_usage = {"prompt_tokens": int(data.get("prompt_eval_count") or 0)}   # 0 when not reported
        return np.asarray(data["embeddings"], dtype=np.float32)
