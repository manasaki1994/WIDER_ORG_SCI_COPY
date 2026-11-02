"""Offline replay of a round (change 2c, D17 and D0b): recompute the round's dry run, grid, cover, diagnosis, suggested
edits and strings from the stored inputs, with no model and no network, and compare their hashes with the round's
record in ``query_rounds.hashes_json``.

:func:`compute` is the one recomputation. ``rounds.record_dry_run`` calls it at a round's first dry run and stores the
result while the record is NULL (never overwritten); :func:`replay` calls it again and compares: first the inputs,
naming any that changed, then the outputs in order, reporting "replay matches" or the first difference. Only the
Development seeds count (the validation set is treated as hidden), on every profiled platform whatever the
triggering dry run covered, and the volatile fields ``at``, ``status`` and ``run_dir`` are left out, so freezing the
version or a new run folder changes no hash. A replay writes nothing.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow

VOLATILE = {"at", "status", "run_dir"}
INPUTS = ("seeds", "imports", "profiles", "catalogue", "synonyms", "platforms")
OUTPUTS = ("dry_run", "grid", "cover", "diagnosis", "edits", "strings")
LABELS = {"dry_run": "dry run", "grid": "grid", "cover": "minimal cover", "diagnosis": "diagnosis",
          "edits": "suggested edits", "strings": "search strings"}
NO_RECORD = "no record to compare: this round has no replay record yet; its next dry run writes one"


def _strip(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in VOLATILE}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def digest(x: Any) -> str:
    """sha256 of the JSON with sorted keys, the volatile fields left out."""
    raw = json.dumps(_strip(x), sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _inputs(project: Project, row: dict) -> dict[str, str]:
    from . import coverage
    from .evaluate import _abstracts, seed_doc
    from .render import platforms as catalogue

    ab = _abstracts(project)
    papers = coverage.development_papers(project, project.read_benchmark())
    seeds = []
    for b in papers:
        d = seed_doc(b, "Development", ab.get(b.benchmark_id))
        seeds.append([d.benchmark_id, b.doi or "", d.title, d.abstract, d.keywords])
    imports = [[r["source_id"], r["sha256"]] for r in
               project.store.query("SELECT source_id, sha256 FROM sources ORDER BY source_id")]
    cat = catalogue()
    return {
        "seeds": digest(seeds),
        "imports": digest(imports),
        "profiles": digest({k: v.get("profile") for k, v in cat.items()}),
        "catalogue": digest({k: v.get("field_codes") for k, v in cat.items()}),
        "synonyms": row["sha256"],
        "platforms": digest([p.key for p in project.config.platforms]),
    }


def _outputs(project: Project, version: int) -> dict[str, Any]:
    from ..construct import artifacts
    from .diagnostics import DiagnoseError, EditError, suggested_edits, what_did_what
    from .evaluate import Query, dry_run
    from .render import RENDERERS, render

    out = dry_run(project, version, write=False)
    plats = out["platforms"]
    res: dict[str, Any] = {
        "dry_run": {k: digest({f: pl.get(f) for f in ("profile", "recall", "seeds", "terms_report")})
                    for k, pl in plats.items()},
        "grid": {k: digest(pl.get("grid")) for k, pl in plats.items()},
        "cover": {k: digest({"cover": pl.get("cover"), "no_seed": pl.get("no_seed")}) for k, pl in plats.items()},
    }
    try:
        res["diagnosis"] = digest(what_did_what(project, version))
    except DiagnoseError:
        res["diagnosis"] = None   # no export imported against this round
    try:
        res["edits"] = digest(suggested_edits(project, version))
    except EditError:
        res["edits"] = None
    _row, data = artifacts.load(project, "synonyms", version)
    q = Query.model_validate(data)
    res["strings"] = {k: digest(render(q, k)) for k in plats if k in RENDERERS}
    return res


def compute(project: Project, round_no: int) -> dict[str, Any]:
    """The round's input and output hashes, recomputed now (Development seeds only, every profiled platform, no
    run folder, no model, no network). A ``ValueError`` when the round has no synonyms version."""
    from ..construct import artifacts
    from . import coverage

    row, _data = artifacts.load(project, "synonyms", round_no)
    if row is None:
        raise ValueError(f"no round {round_no}")
    with coverage.development_only():
        return {"inputs": _inputs(project, row), "outputs": _outputs(project, round_no)}


def record(project: Project, round_no: int) -> bool:
    """Store the round's record at its first dry run, only while none is set (a later dry run never changes it).
    Returns whether a record was written."""
    have = project.store.one("SELECT hashes_json FROM query_rounds WHERE round_no=?", (round_no,))
    if have is None or have["hashes_json"]:
        return False
    rec = {"recorded_at": utcnow(), **compute(project, round_no)}
    cur = project.store.conn.execute("UPDATE query_rounds SET hashes_json=? WHERE round_no=? AND hashes_json IS NULL",
                                     (json.dumps(rec, sort_keys=True), round_no))
    return cur.rowcount == 1


def stored(project: Project, round_no: int) -> dict | None:
    r = project.store.one("SELECT hashes_json FROM query_rounds WHERE round_no=?", (round_no,))
    return json.loads(r["hashes_json"]) if r and r["hashes_json"] else None


def _first_difference(old: dict, new: dict) -> str | None:
    for k in OUTPUTS:
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict):
            plats = [p for p in [*a, *[p for p in b if p not in a]] if a.get(p) != b.get(p)]
            return f"{LABELS[k]} ({', '.join(plats)})"
        return LABELS[k]
    return None


def replay(project: Project, round_no: int) -> dict[str, Any]:
    """Recompute round ``round_no`` and compare with its record. Writes nothing."""
    rec = stored(project, round_no)
    if rec is None:
        from ..construct import artifacts

        if artifacts.load(project, "synonyms", round_no)[0] is None:
            raise ValueError(f"no round {round_no}")
        return {"round": round_no, "result": "no_record", "changed_inputs": [], "first_difference": None,
                "recorded_at": None, "message": NO_RECORD}
    now = compute(project, round_no)
    changed = [k for k in INPUTS if rec["inputs"].get(k) != now["inputs"].get(k)]
    diff = _first_difference(rec["outputs"], now["outputs"])
    when = rec.get("recorded_at")
    if not changed and diff is None:
        msg = f"replay matches the record of {when}"
        result = "matches"
    else:
        parts = []
        if changed:
            parts.append(f"inputs changed since the record of {when}: {', '.join(changed)}")
        parts.append(f"first difference: {diff}" if diff else "the outputs still match")
        msg = "; ".join(parts)
        result = "differs"
    return {"round": round_no, "result": result, "changed_inputs": changed, "first_difference": diff,
            "recorded_at": when, "message": msg}
