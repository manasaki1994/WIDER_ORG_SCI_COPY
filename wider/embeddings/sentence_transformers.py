"""Local models through sentence-transformers (``pip install wider[local]``); keyless default."""
from __future__ import annotations

import os

import numpy as np

from .base import Embedder, EmbeddingError


class SentenceTransformersEmbedder(Embedder):
    kind = "sentence_transformers"
    _models: dict[str, object] = {}
    allow_download = False  # set by ``download_embedding`` only; every other load stays in the local cache

    def _model(self):
        key = self.cfg.model
        if key not in self._models:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise EmbeddingError("sentence-transformers is not installed; pip install 'wider[local]'")
            if self.cfg.extra.get("offline", False):
                os.environ.setdefault("HF_HUB_OFFLINE", "1")
            # A cached model is loaded from the cache only: transformers would otherwise look for a second
            # weight file (model.safetensors next to pytorch_model.bin) and start a download nobody asked for.
            local_only = not self.allow_download and self.is_downloaded()
            self._models[key] = SentenceTransformer(key, device=self.cfg.extra.get("device"),
                                                    trust_remote_code=bool(self.cfg.extra.get("trust_remote_code", False)),
                                                    local_files_only=local_only)
        return self._models[key]

    def is_downloaded(self) -> bool:
        """True when the weights are already in the Hugging Face cache (no download needed)."""
        try:
            from huggingface_hub import try_to_load_from_cache
        except ImportError:
            return False
        for fname in ("config.json", "modules.json"):
            r = try_to_load_from_cache(self.cfg.model, fname)
            if isinstance(r, str):
                return True
        return False

    _batch_size: int | None = None  # shrinks after an out-of-memory error and stays shrunk for the run

    def _embed_batch(self, texts):
        m = self._model()
        texts = list(texts)
        bs = self._batch_size or self.cfg.batch_size
        while True:
            try:
                out = m.encode(texts, batch_size=bs, normalize_embeddings=False, show_progress_bar=False)
                return np.asarray(out, dtype=np.float32)
            except RuntimeError as e:  # MPS/CUDA out of memory on a batch of long texts: halve and retry
                if "out of memory" not in str(e).lower() or bs <= 1:
                    raise
                bs = max(1, bs // 2)
                self._batch_size = bs
                _free_accelerator_memory()


def _free_accelerator_memory() -> None:
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # torch missing or no accelerator: nothing to free
        pass
