"""Resumable batch jobs: a checkpoint of finished item keys so interrupted runs continue."""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


class Checkpoint:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.done: set[str] = set()
        if self.path.exists():
            try:
                self.done = set(json.loads(self.path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                self.done = set()

    def mark(self, key: str) -> None:
        self.done.add(key)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(self.done)), encoding="utf-8")
        tmp.replace(self.path)

    def pending(self, items: Iterable[T], key: Callable[[T], str]) -> list[T]:
        return [it for it in items if key(it) not in self.done]


def batched(items: list[T], size: int) -> Iterable[list[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
