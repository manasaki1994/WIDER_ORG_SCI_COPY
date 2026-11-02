"""One shared "what just happened" record per stage, written at engine exit and read by the CLI and the UI.

Every engine (import, dedup, enrich, embed, rank, sample, screen, recall, report, construct) ends by calling
:func:`record` with a :class:`StageResult`. The result is stored in the ``meta`` table under ``last_result:<stage>``
so the two front ends show the same headline, metric tiles and warnings, and ``wider status`` can list the last
result of every stage. Results carry no abstracts: only counts, names, file paths and short titles.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .provenance import utcnow
from .stages import load_stages

if TYPE_CHECKING:
    from .project import Project

META_PREFIX = "last_result:"
Tone = Literal["success", "warning", "danger", "neutral"]


class StageResult(BaseModel):
    """A stage's last outcome, small enough to print and to render as a card."""

    stage: str
    action: str
    ran_at: str = Field(default_factory=utcnow)
    ok: bool = True
    headline: str
    metrics: dict[str, Any] = Field(default_factory=dict)   # label -> number or short string, in display order
    rows: list[dict[str, Any]] = Field(default_factory=list)  # one small table (per file, per sample, per variant)
    warnings: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)       # files written, relative to the project when possible
    next_stage: str | None = None
    usage: dict[str, Any] | None = None   # model calls of the run: tokens, cost, backend, local or cloud

    @property
    def tone(self) -> Tone:
        if not self.ok:
            return "danger"
        if self.warnings:
            return "warning"
        return "success"

    def as_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json")
        d["tone"] = self.tone
        return d


def stage_order() -> list[str]:
    return [s["key"] for s in load_stages()]


def following_stage(stage: str) -> str | None:
    """The stage after ``stage`` in the procedure (extension points excluded)."""
    keys = [s["key"] for s in load_stages() if s.get("status") != "extension"]
    if stage not in keys:
        return None
    i = keys.index(stage)
    return keys[i + 1] if i + 1 < len(keys) else None


def record(project: Project, result: StageResult) -> StageResult:
    """Persist ``result`` as the stage's last result (and fill ``next_stage`` when the summariser left it empty)."""
    if result.next_stage is None:
        result.next_stage = following_stage(result.stage)
    if result.usage is None:   # recorded inside a usage scope (a proposal run): attach that run's tokens and cost
        from .usage import _SCOPE, usage_for_runs
        scope = _SCOPE.get()
        if scope and scope.get("run_ref"):
            result.usage = usage_for_runs(project, [scope["run_ref"]])
    payload = json.dumps(result.as_dict(), default=str)
    project.store.set_meta(META_PREFIX + result.stage, payload)
    project.store.execute("INSERT INTO stage_results(stage, action, ran_at, result) VALUES(?,?,?,?)",
                          (result.stage, result.action, result.ran_at, payload))
    return result


def history(project: Project, stage: str, limit: int = 30) -> list[dict[str, Any]]:
    """Earlier results of a stage, newest first (the run selector on the stage page)."""
    rows = project.store.query("SELECT result_id, result FROM stage_results WHERE stage=? ORDER BY result_id DESC LIMIT ?",
                               (stage, limit))
    return [{"result_id": r["result_id"], **json.loads(r["result"])} for r in rows]


def last_result(project: Project, stage: str) -> dict[str, Any] | None:
    raw = project.store.get_meta(META_PREFIX + stage)
    return json.loads(raw) if raw else None


def all_last_results(project: Project) -> dict[str, dict[str, Any]]:
    rows = project.store.query("SELECT key, value FROM meta WHERE key LIKE ?", (META_PREFIX + "%",))
    return {r["key"][len(META_PREFIX):]: json.loads(r["value"]) for r in rows}


def format_result(res: dict[str, Any] | StageResult | None) -> str:
    """Plain-text rendering shared by the CLI: headline, metrics line, warnings."""
    if res is None:
        return ""
    d = res.as_dict() if isinstance(res, StageResult) else res
    lines = [d["headline"]]
    if d.get("metrics"):
        lines.append("  " + " · ".join(f"{k}: {v}" for k, v in d["metrics"].items()))
    for w in d.get("warnings", []):
        lines.append(f"  warning: {w}")
    for o in d.get("outputs", []):
        lines.append(f"  wrote {o}")
    u = d.get("usage")
    if u and u.get("calls"):
        from .usage import cache_text

        cost = f"${u['cost_usd']:.4f}" if u.get("cost_known") else "cost not priced (set the backend's prices)"
        lines.append(f"  model calls: {u['calls']} on {', '.join(u.get('backends') or [])} [{u.get('locality')}], "
                     f"{u['tokens']:,} tokens, {cost}, {cache_text(u)}")
    return "\n".join(lines)
