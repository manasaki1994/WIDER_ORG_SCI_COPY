"""Embedding interface: texts in, L2-normalised float32 matrix out, with a persistent cache."""
from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence

import httpx
import numpy as np

from ..core.models import EmbeddingConfig
from ..core.text import truncate_words
from .cache import EmbeddingCache


class EmbeddingError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


API_KINDS = ("openai", "openai_compat", "ollama")   # these log one usage row per batch they send
EMBED_TRIES = 3
_sleep = time.sleep   # tests replace this one name, never time.sleep itself


def http_status_error(r) -> EmbeddingError:
    """The error of an HTTP answer >= 400: 429 and 5xx may be retried, any other 4xx (a bad key) is final."""
    return EmbeddingError(f"HTTP {r.status_code}: {r.text[:300]}",
                          retryable=r.status_code == 429 or r.status_code >= 500)


def l2_normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


class Embedder(ABC):
    kind = "base"

    def __init__(self, cfg: EmbeddingConfig, cache: EmbeddingCache | None = None):
        self.cfg = cfg
        self.cache = cache
        self.last_usage: dict | None = None   # set by API embedders per batch: {"prompt_tokens": n}

    def identity(self) -> dict:
        return {"name": self.cfg.name, "kind": self.cfg.kind, "model": self.cfg.model,
                "dim": self.cfg.dim, "base_url": self.cfg.base_url, "max_words": self.cfg.max_words}

    def cache_key(self) -> str:
        return f"{self.cfg.kind}:{self.cfg.model}:{self.cfg.dim or 'full'}"

    @abstractmethod
    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """Raw embeddings for one batch (any dimension; normalisation and truncation happen here)."""

    def _embed_with_retry(self, texts: list[str]) -> np.ndarray:
        """One batch, tried up to 3 times on a timeout, a transport error, 429 or 5xx (backoff ``min(30, 2**i +
        random)``); any other error is raised at once. An API backend logs one usage row per batch it sent, inside
        an open usage scope (``core/usage.py:record_embedding_call``)."""
        from ..core.usage import record_embedding_call

        api = self.cfg.kind in API_KINDS
        for i in range(EMBED_TRIES):
            self.last_usage = None
            try:
                m = self._embed_batch(texts)
            except (httpx.TimeoutException, httpx.TransportError, EmbeddingError) as e:
                retryable = not isinstance(e, EmbeddingError) or e.retryable
                if api:
                    record_embedding_call(self.cfg, None, ok=False)
                if not retryable or i == EMBED_TRIES - 1:
                    if isinstance(e, EmbeddingError):
                        raise
                    raise EmbeddingError(f"{type(e).__name__}: {e}"[:400], retryable=True) from e
                _sleep(min(30.0, 2 ** i + random.random()))
                continue
            if api:
                record_embedding_call(self.cfg, self.last_usage, ok=True)
            return m
        raise AssertionError("unreachable")

    def _post(self, m: np.ndarray) -> np.ndarray:
        m = np.asarray(m, dtype=np.float32)
        if self.cfg.dim and m.shape[1] > self.cfg.dim:
            m = m[:, : self.cfg.dim]  # Matryoshka truncation, then renormalise
        return l2_normalise(m)

    def embed(self, texts: Sequence[str], progress=None) -> np.ndarray:
        texts = [truncate_words(t or "", self.cfg.max_words) for t in texts]
        out: list[np.ndarray | None] = [None] * len(texts)
        todo: list[int] = []
        if self.cache is not None:
            hits = self.cache.get_many(self.cache_key(), texts)
            for i, v in enumerate(hits):
                if v is None:
                    todo.append(i)
                else:
                    out[i] = v
        else:
            todo = list(range(len(texts)))
        bs = max(1, self.cfg.batch_size)
        # Longest texts first: batches are padded to their longest member, so length-homogeneous batches waste
        # little, and a batch that exceeds the accelerator's memory fails at the start instead of hours in.
        todo.sort(key=lambda i: -len(texts[i]))
        for s in range(0, len(todo), bs):
            idx = todo[s : s + bs]
            m = self._post(self._embed_with_retry([texts[i] for i in idx]))
            if m.shape[0] != len(idx):
                raise EmbeddingError(f"backend returned {m.shape[0]} vectors for {len(idx)} texts")
            for j, i in enumerate(idx):
                out[i] = m[j]
            if self.cache is not None:
                self.cache.put_many(self.cache_key(), [texts[i] for i in idx], m)
            if progress:
                progress(min(s + bs, len(todo)), len(todo))
        if not texts:
            return np.zeros((0, self.cfg.dim or 0), dtype=np.float32)
        return np.vstack([v for v in out])  # type: ignore[list-item]
