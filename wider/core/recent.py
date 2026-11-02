"""Recently opened projects for the home page's project switcher.

The list holds folder paths and the time each was last opened, nothing else, in
``$WIDER_HOME/recent_projects.json`` (default ``~/.wider``) on this computer. The UI server only writes it when
started from the command line (``wider ui``); tests and embedded apps keep the list in memory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .provenance import utcnow

MAX_RECENT = 20


def default_recent_file() -> Path:
    return Path(os.environ.get("WIDER_HOME") or "~/.wider").expanduser() / "recent_projects.json"


class RecentProjects:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._mem: list[dict[str, str]] = []

    def _read(self) -> list[dict[str, str]]:
        if not self.path:
            return list(self._mem)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [d for d in data if isinstance(d, dict) and d.get("path")] if isinstance(data, list) else []

    def _write(self, items: list[dict[str, str]]) -> None:
        items = items[:MAX_RECENT]
        if not self.path:
            self._mem = items
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[dict[str, str]]:
        return self._read()

    def add(self, root: str | Path) -> None:
        root = str(Path(root).resolve())
        items = [d for d in self._read() if d["path"] != root]
        self._write([{"path": root, "opened_at": utcnow()}, *items])

    def remove(self, root: str | Path) -> None:
        root = str(Path(root).resolve())
        self._write([d for d in self._read() if d["path"] != root])
