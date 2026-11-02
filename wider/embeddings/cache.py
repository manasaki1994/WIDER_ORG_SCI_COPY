"""SQLite cache of embeddings keyed by model and text hash, so interrupted runs resume for free."""
from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ..core.provenance import sha256_text


class EmbeddingCache:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS vec (model TEXT, h TEXT, dim INTEGER, v BLOB, PRIMARY KEY(model,h))")

    def get_many(self, model: str, texts: Sequence[str]) -> list[np.ndarray | None]:
        out: list[np.ndarray | None] = []
        for t in texts:
            r = self.conn.execute("SELECT dim, v FROM vec WHERE model=? AND h=?", (model, sha256_text(t))).fetchone()
            out.append(np.frombuffer(r[1], dtype=np.float32).copy() if r else None)
        return out

    def put_many(self, model: str, texts: Sequence[str], m: np.ndarray) -> None:
        rows = [(model, sha256_text(t), int(m.shape[1]), np.asarray(m[i], dtype=np.float32).tobytes())
                for i, t in enumerate(texts)]
        self.conn.execute("BEGIN")
        self.conn.executemany("INSERT OR REPLACE INTO vec(model,h,dim,v) VALUES(?,?,?,?)", rows)
        self.conn.execute("COMMIT")

    def count(self, model: str | None = None) -> int:
        if model:
            return self.conn.execute("SELECT COUNT(*) FROM vec WHERE model=?", (model,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM vec").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
