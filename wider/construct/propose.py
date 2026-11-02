"""LLM proposals for construct artifacts, grounded in the development benchmark papers."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from ..core.project import Project
from ..core.provenance import RunSpec, sha256_json, sha256_text
from ..core.questions import clarifications
from ..core.text import truncate_words
from ..providers import ChatRequest, research_provider
from ..providers.registry import require_text
from . import artifacts
from .artifacts import dump_yaml, schema_for

TEMPLATES = Path(__file__).parent / "templates"
MAX_ABSTRACT_WORDS = 350


class ProposalError(Exception):
    pass


PROPOSABLE = ("cells", "codebook", "synonyms", "anchors", "near_miss")


def check_kind(kind: str) -> None:
    """ProposalError unless ``kind`` is one the general proposal can draft (checked before any model call)."""
    if kind in PROPOSABLE:
        return
    if kind == "adjacent":
        raise ProposalError("adjacent literatures are proposed with `wider construct adjacent propose` "
                            "(UI: Adjacent literatures card, \"Propose from question\")")
    if kind == "prompts":
        raise ProposalError("prompts are not proposed by the model; they are edited and saved")
    raise ProposalError(f"kind must be one of {', '.join(PROPOSABLE)}")


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)


def benchmark_context(project: Project, which: str = "Development") -> list[dict]:
    """Development benchmark papers with abstracts from the store (validation papers are never shown to the model)."""
    papers = [b for b in project.read_benchmark() if b.set == which]
    rows = {r["benchmark_id"]: dict(r) for r in project.store.query("SELECT * FROM benchmark_papers")}
    out = []
    for b in papers:
        r = rows.get(b.benchmark_id, {})
        ab = r.get("abstract") or b.abstract or ""
        out.append({"benchmark_id": b.benchmark_id, "title": b.title, "year": b.year or r.get("year"),
                    "abstract": truncate_words(ab, MAX_ABSTRACT_WORDS)})
    return out


def _artifact_or_none(project: Project, kind: str) -> tuple[dict | None, Any]:
    row = artifacts.latest(project, kind, status="approved") or artifacts.latest(project, kind)
    if row is None:
        return None, None
    return artifacts.load(project, kind, row["version"])


def prior_review_titles(project: Project, max_titles: int = 200) -> list[dict]:
    """Titles of studies included in prior reviews, **only** when the project's excerpt switch is on (else empty)."""
    if not project.config.prior_reviews.allow_llm_excerpts:
        return []
    rows = project.store.query("SELECT p.title, p.year, r.label FROM prior_review_papers p JOIN prior_reviews r "
                               "ON r.review_id=p.review_id WHERE p.title IS NOT NULL AND p.title<>'' "
                               "ORDER BY p.review_id, p.position LIMIT ?", (max_titles,))
    return [{"title": r["title"], "year": r["year"], "review": r["label"]} for r in rows]


def build_prompt(project: Project, kind: str, cell_keys: list[str] | None = None, style: str | None = None) -> tuple[str, dict]:
    """Render the system prompt for ``kind``; returns (prompt, context_summary)."""
    check_kind(kind)
    cfg = project.config
    bench = benchmark_context(project)
    if not bench:
        raise ProposalError("benchmark.csv has no Development papers; add them first (wider benchmark fetch)")
    n_abs = sum(1 for b in bench if b["abstract"])
    ctx: dict[str, Any] = {"questions": cfg.questions or [cfg.title or cfg.name], "include_notes": cfg.include_notes,
                           "exclude_notes": cfg.exclude_notes, "benchmark": bench, "cells": None, "cells_yaml": "",
                           "codebook": None, "codebook_yaml": "", "anchors": None, "anchors_style": "",
                           "anchors_example": "", "cell_keys": cell_keys or [], "style": style or "",
                           "prior_review_titles": prior_review_titles(project),
                           "clarifications": clarifications(project, "construct"),
                           # the refinement interview's decision ledger (2b, D12): synonyms proposals only
                           "decisions": clarifications(project, "query") if kind == "synonyms" else []}
    used = {}
    if kind in ("codebook", "synonyms", "anchors"):
        row, data = _artifact_or_none(project, "cells")
        if data:
            ctx["cells"], ctx["cells_yaml"] = data, dump_yaml(data)
            used["cells"] = row["version"]
        elif kind == "anchors":
            raise ProposalError("anchors need an approved cells artifact first (wider construct propose cells)")
    if kind in ("synonyms", "near_miss"):
        row, data = _artifact_or_none(project, "codebook")
        if data:
            ctx["codebook"], ctx["codebook_yaml"] = data, dump_yaml(data)
            used["codebook"] = row["version"]
        elif kind == "near_miss":
            raise ProposalError("near-miss texts need a codebook first (wider construct propose codebook)")
    if kind == "near_miss":
        row, data = _artifact_or_none(project, "anchors")
        if data and data.get("anchors"):
            ctx["anchors"] = data
            ctx["anchors_style"] = data.get("style", "")
            ctx["anchors_example"] = next(iter(data["anchors"].values()))
            used["anchors"] = row["version"]
    if kind == "anchors" and not cell_keys:
        ctx["cell_keys"] = [c["key"] for c in ctx["cells"]["cells"]]
    prompt = _env().get_template(f"{kind}.md.j2").render(**ctx)
    return prompt, {"benchmark_n": len(bench), "benchmark_with_abstract": n_abs, "uses": used,
                    "cell_keys": ctx["cell_keys"], "prior_review_titles_n": len(ctx["prior_review_titles"]),
                    "clarifications_n": len(ctx["clarifications"]), "decisions_n": len(ctx["decisions"])}


USER_MESSAGE = "Produce the proposal now as one JSON object matching the schema."


async def propose_async(project: Project, kind: str, provider_name: str | None = None, note: str = "",
                        per_cell: bool = True, style: str | None = None, confirm_revision: bool = False) -> dict:
    """Run the proposal and save it as a draft version. Returns the artifact row plus run info. A synonyms proposal
    after a look that kept papers as validation raises :class:`~wider.construct.artifacts.RevisionNeedsConfirm`
    before the model is called unless ``confirm_revision`` (D2 of validation-reuse); one that reopens the query with
    validation papers shown raises :class:`~wider.construct.artifacts.SeenChoiceNeeded` before the model is called
    unless the enclosing :func:`~wider.construct.artifacts.save_seen_choice` gives the choice (unlock-seen-choice D4)."""
    check_kind(kind)
    if not confirm_revision:
        warn = artifacts.revision_notice(project, kind)
        if warn:
            raise artifacts.RevisionNeedsConfirm(warn)
    seen = artifacts.need_save_choice(project, kind) if kind == "synonyms" else None
    pcfg = project.provider(provider_name) if provider_name else project.provider_for("construct")
    provider = research_provider(project, require_text(pcfg))
    schema = schema_for(kind)
    run_dir = project.run_dir("construct", RunSpec(kind="construct").stamp + f"_{kind}")
    calls: list[dict] = []
    prev = artifacts.latest(project, kind)   # the version the proposal is made from (2c D17: its round)
    from_version = prev["version"] if prev else None
    from ..core.usage import track, usage_for_runs
    with track(project, "construct", run_dir.name):
        try:
            with artifacts.revision_confirmed(confirm_revision), artifacts.save_seen_choice(seen):
                return await _propose_in_scope(project, kind, pcfg, provider, schema, run_dir, calls, note, per_cell, style,
                                               usage_for_runs, from_version)
        except ProposalError as e:
            from ..core.questions import raise_question
            raise_question(project, "proposal_failed", kind, f"{kind} proposal by {pcfg.name}",
                           f"The model could not produce a usable {kind} proposal ({str(e)[:200]}). Add a clarification that "
                           "later proposal prompts will show (for example what the construct is not, or a term to use), "
                           "or leave it.", context={"kind": kind, "provider": pcfg.name, "error": str(e)[:400]},
                           run_ref=run_dir.name)
            raise


async def _propose_in_scope(project, kind, pcfg, provider, schema, run_dir, calls, note, per_cell, style, usage_for_runs,
                            from_version=None) -> dict:

    async def one(prompt: str, sub_schema: dict, tag: str):
        req = ChatRequest(system=prompt, user=USER_MESSAGE, schema=sub_schema, max_tokens=8000)
        r = await provider.complete(req)
        calls.append({"tag": tag, "ok": r.ok, "attempts": r.attempts, "error": r.error, "usage": r.usage,
                      "input_sha256": r.input_sha256, "output_sha256": sha256_text(r.text) if r.text else None,
                      "elapsed_s": round(r.elapsed_s, 1)})
        (run_dir / f"prompt_{tag}.md").write_text(prompt, encoding="utf-8")
        (run_dir / f"response_{tag}.txt").write_text(r.text or "", encoding="utf-8")
        if not r.ok:
            if (r.error or "").startswith("invalid structured output"):   # plain words; the detail stays in the run
                raise ProposalError(f"the model's answer did not have the form a {kind} needs, so nothing was saved. "
                                    f"Try again, or write the {kind} by hand (detail in runs/{run_dir.name})")
            raise ProposalError(f"{kind} proposal failed ({tag}): {r.error}")
        return r.parsed

    if kind == "anchors" and per_cell:
        prompt0, info = build_prompt(project, kind, style=style)
        keys = info["cell_keys"]
        # first cell sets the style, the rest follow it in parallel
        first = await one(build_prompt(project, kind, cell_keys=[keys[0]], style=style)[0], schema, keys[0])
        style_used = first.get("style") or style or ""
        rest = await asyncio.gather(*[one(build_prompt(project, kind, cell_keys=[k], style=style_used)[0], schema, k)
                                      for k in keys[1:]])
        anchors = dict(first["anchors"])
        for r in rest:
            anchors.update(r["anchors"])
        missing = [k for k in keys if k not in anchors]
        if missing:
            raise ProposalError(f"model did not return anchors for {missing}")
        data = {"style": style_used, "anchors": {k: anchors[k] for k in keys}}
        prompt_hash = sha256_text(prompt0)
    else:
        prompt, info = build_prompt(project, kind, style=style)
        data = await one(prompt, schema, "all")
        prompt_hash = sha256_text(prompt)
    fixes: list[str] = []
    if kind == "synonyms":   # a proximity group as JSON text, an unquoted phrase: fixed and recorded, never silent
        from ..search.ir import tidy_terms
        try:
            data, fixes = tidy_terms(data)
        except ValueError as e:
            raise ProposalError(f"{kind} proposal unusable: {e}") from e
        if fixes:
            (run_dir / "fixes.txt").write_text("\n".join(fixes) + "\n", encoding="utf-8")

    spec = RunSpec(kind="construct", inputs={"prompt": prompt_hash, "schema": sha256_json(schema)},
                   settings={"artifact": kind, "context": info, "from_version": from_version, "fixes": fixes},
                   provider=provider.identity(),
                   notes=note)
    spec.write(run_dir)
    (run_dir / "calls.json").write_text(dump_yaml(calls), encoding="utf-8")
    saved_note = note or f"proposed by {pcfg.name}/{pcfg.model}; run {run_dir.name}"
    if fixes:
        saved_note += f"; WIDER fixed {len(fixes)} term(s): " + "; ".join(fixes)
    row = artifacts.save_new_version(project, kind, data, origin="llm", note=saved_note)
    row["run_dir"] = str(run_dir)
    row["fixes"] = fixes
    row["calls"] = calls
    row["usage"] = usage_for_runs(project, [run_dir.name])
    return row


def propose(project: Project, kind: str, **kw) -> dict:
    return asyncio.run(propose_async(project, kind, **kw))
