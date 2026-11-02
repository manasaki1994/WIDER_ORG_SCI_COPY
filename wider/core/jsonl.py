"""Append-only JSONL used for raw model responses; readers tolerate a truncated last line."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


def append(path: str | Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def read(path: str | Path) -> Iterator[dict]:
    path = Path(path)
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue  # a truncated last line from an interrupted run


def read_all(path: str | Path) -> list[dict]:
    return list(read(path))
