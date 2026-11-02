"""Search-strings archive: every rendered search string kept with its date, platform and synonyms version.

Files go to ``exports/search_strings/<date>_synonyms-v<N>_<platform>.txt`` with an index ``archive.csv``; each string's
translation ledger goes next to it as ``<stem>_ledger.json`` (change 2c, D13). The
archive is written automatically when a synonyms version is frozen and on request (`wider query archive`, the
query page); a string that is already archived (same platform, same text) is not written again. A methods section
or a reviewer can then see exactly which string ran on which database and when.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.provenance import sha256_text, utcnow

if TYPE_CHECKING:
    from ..core.project import Project

FIELDS = ["archived_at", "platform", "label", "synonyms_version", "synonyms_status", "sha256", "file", "reason"]


def archive_dir(project: Project) -> Path:
    return project.path("exports", "search_strings")


NO_LEDGER = "no ledger (archived before ledgers)"


def ledger_name(file: str) -> str:
    """The ledger file next to an archived string: ``<its stem>_ledger.json``, any ``_<h8>`` suffix included (D13)."""
    return f"{Path(file).stem}_ledger.json"


def entries(project: Project) -> list[dict[str, Any]]:
    """The archive index; each entry also names its ledger file, or None for a string archived before ledgers."""
    idx = archive_dir(project) / "archive.csv"
    if not idx.exists():
        return []
    with open(idx, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        name = ledger_name(r["file"])
        r["ledger"] = name if (archive_dir(project) / name).exists() else None
    return rows


def archive(project: Project, version: int | None = None, reason: str = "requested") -> dict[str, Any]:
    """Render the synonyms version (default newest) for every chosen platform and archive the new strings."""
    from ..construct import artifacts
    from .catalogue import project_platforms, render_project
    from .ir import Query
    from .render import render_all
    from .translation import project_ledgers

    row, data = artifacts.load(project, "synonyms", version)
    if row is None:
        raise ValueError("no synonyms artifact yet; propose one or import a search string first")
    q = Query.model_validate(data)
    if project.config.platforms:
        strings = render_project(project, q, pretty=True)
        labels = {c["key"]: c["label"] for c in project_platforms(project)}
    else:
        strings = render_all(q, ["ebsco", "scopus", "wos", "proquest", "ovid"], pretty=True)
        labels = {k: k for k in strings}
    out = archive_dir(project)
    out.mkdir(parents=True, exist_ok=True)
    ledgers = project_ledgers(project, q)
    seen = {(e["platform"], e["sha256"]) for e in entries(project)}
    now = utcnow()
    day = now[:10]
    new = []
    for key, text in strings.items():
        h = sha256_text(text)
        if (key, h) in seen:
            continue
        name = f"{day}_synonyms-v{row['version']}_{key}.txt"
        if (out / name).exists():   # same day, same version, changed platform syntax: keep both
            name = f"{day}_synonyms-v{row['version']}_{key}_{h[:8]}.txt"
        header = (f"# {project.config.name}: search string for {labels.get(key, key)}\n# synonyms v{row['version']} "
                  f"({row['status']}), archived {now} ({reason})\n# sha256 of the string: {h}\n")
        (out / name).write_text(header + text + "\n", encoding="utf-8")
        led = {"platform": key, "synonyms_version": row["version"], "string_sha256": h, "rows": ledgers.get(key, [])}
        (out / ledger_name(name)).write_text(json.dumps(led, indent=2) + "\n", encoding="utf-8")
        new.append({"archived_at": now, "platform": key, "label": labels.get(key, key), "synonyms_version": row["version"],
                    "synonyms_status": row["status"], "sha256": h, "file": name, "reason": reason})
    if new:
        idx = out / "archive.csv"
        write_header = not idx.exists()
        with open(idx, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if write_header:
                w.writeheader()
            w.writerows(new)
    return {"synonyms_version": row["version"], "new": new, "n_new": len(new), "n_total": len(entries(project)),
            "dir": str(out.relative_to(project.root))}
