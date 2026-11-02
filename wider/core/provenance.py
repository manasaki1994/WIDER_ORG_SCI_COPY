"""Hashes, run specs and artifact versioning. Every run records what it saw and with what."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .. import __version__


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def sha256_json(obj: Any) -> str:
    return sha256_text(canonical_json(obj))


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def local_time(at: str | None, seconds: bool = False) -> str:
    """A stored UTC time as people read it everywhere (CLI, pages, reports): this computer's local time with the zone
    named, e.g. "2026-10-27 22:59 CET". A time stored without a zone counts as UTC; text that is not a time is shown
    as it is."""
    if not at:
        return ""
    try:
        d = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return str(at)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone().strftime("%Y-%m-%d %H:%M" + (":%S" if seconds else "") + " %Z")


def stamp() -> str:
    """Filesystem-safe UTC timestamp used for run folders."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


class RunSpec(BaseModel):
    """What a run was: inputs by hash, settings, software. Written to ``runs/<kind>/<stamp>/run_spec.json``."""

    kind: str
    stamp: str = Field(default_factory=stamp)
    started_at: str = Field(default_factory=utcnow)
    wider_version: str = __version__
    python: str = sys.version.split()[0]
    platform: str = platform.platform()
    inputs: dict[str, str] = Field(default_factory=dict)  # name -> sha256
    settings: dict[str, Any] = Field(default_factory=dict)
    provider: dict[str, Any] | None = None
    notes: str = ""

    def spec_hash(self) -> str:
        d = self.model_dump()
        d.pop("stamp", None)
        d.pop("started_at", None)
        return sha256_json(d)

    def write(self, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / "run_spec.json"
        d = self.model_dump()
        d["spec_hash"] = self.spec_hash()
        p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
        return p


class ArtifactVersion(BaseModel):
    """One versioned, human-approved artifact (cells, codebook, synonyms, anchors, queries, prompts)."""

    kind: str
    version: int
    path: str
    sha256: str
    created_at: str = Field(default_factory=utcnow)
    status: str = "draft"  # draft | approved | frozen
    parent_version: int | None = None
    origin: str = "manual"  # manual | llm | import
    note: str = ""


def next_version(directory: str | Path, suffix: str) -> int:
    directory = Path(directory)
    if not directory.exists():
        return 1
    versions = []
    for p in directory.glob(f"v*{suffix}"):
        stem = p.name[1 : len(p.name) - len(suffix)]
        if stem.isdigit():
            versions.append(int(stem))
    return (max(versions) + 1) if versions else 1
