"""OpenAI ``/embeddings`` and compatible servers (vLLM, LM Studio, OpenRouter, Ollama /v1)."""
from __future__ import annotations

import os

import httpx
import numpy as np

from .base import Embedder, EmbeddingError, http_status_error


class OpenAICompatEmbedder(Embedder):
    kind = "openai_compat"

    def _embed_batch(self, texts):
        base = (self.cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.cfg.api_key_env) if self.cfg.api_key_env else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif "api.openai.com" in base:
            raise EmbeddingError(f"environment variable {self.cfg.api_key_env or 'OPENAI_API_KEY'} is not set")
        body = {"model": self.cfg.model, "input": [t if t.strip() else " " for t in texts]}
        # OpenAI's text-embedding-3 models accept a dimensions parameter (Matryoshka); others may reject it
        if self.cfg.dim and (self.cfg.kind == "openai" or self.cfg.extra.get("send_dimensions")):
            body["dimensions"] = self.cfg.dim
        r = httpx.post(f"{base}/embeddings", json=body, headers=headers, timeout=120)
        if r.status_code >= 400:
            raise http_status_error(r)
        payload = r.json()
        usage = payload.get("usage") or {}
        self.last_usage = {"prompt_tokens": int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)}
        data = sorted(payload["data"], key=lambda d: d["index"])
        return np.asarray([d["embedding"] for d in data], dtype=np.float32)
