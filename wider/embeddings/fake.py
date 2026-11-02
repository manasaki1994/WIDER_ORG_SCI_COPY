"""Deterministic hashed embeddings for tests: similar texts get similar vectors (bag of hashed words)."""
from __future__ import annotations

import hashlib

import numpy as np

from .base import Embedder


class FakeEmbedder(Embedder):
    kind = "fake"

    def _embed_batch(self, texts):
        d = self.cfg.dim or 64
        out = np.zeros((len(texts), d), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().split():
                h = int(hashlib.md5(w.encode()).hexdigest(), 16)
                out[i, h % d] += 1.0 if (h >> 8) % 2 else -1.0
        return out
