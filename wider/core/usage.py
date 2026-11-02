"""Tokens and costs of model calls.

Every call goes through ``Provider.complete``; when an engine has opened a usage scope (:func:`track`), the call is
logged in the store's ``llm_usage`` table with its stage, run, background job, backend, locality, tokens (cached ones
apart) and cost. Nothing of the prompt or the answer is stored here. Adapters return a normalised usage dict
(``input_uncached``, ``input_cached``, ``input_cache_write``, ``output``, ``raw``); rows stored before that are read by
their vendor keys. Cost comes from, in order: the cost the backend reports itself (``total_cost_usd``), the prices set
on the backend (at any locality), zero for a local or test backend; otherwise it stays unknown (NULL) and is shown as
"not priced". ``network`` and ``unknown`` backends are never assumed free.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from .provenance import utcnow
from .summary import embedding_locality, provider_locality

if TYPE_CHECKING:
    from .models import EmbeddingConfig, ProviderConfig
    from .project import Project

_SCOPE: contextvars.ContextVar[dict | None] = contextvars.ContextVar("wider_usage_scope", default=None)
JOB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("wider_job_id", default=None)


NORMALISED_KEYS = ("input_uncached", "input_cached", "input_cache_write", "output")


def _int(v: Any) -> int | None:
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None


def normalised(input_uncached: Any = None, input_cached: Any = None, input_cache_write: Any = None,
               output: Any = None, raw: Any = None, total_cost_usd: Any = None) -> dict:
    """The usage dict every adapter returns (a field the vendor does not report is None)."""
    out = {"input_uncached": _int(input_uncached), "input_cached": _int(input_cached),
           "input_cache_write": _int(input_cache_write), "output": _int(output), "raw": raw}
    if isinstance(total_cost_usd, int | float) and not isinstance(total_cost_usd, bool):
        out["total_cost_usd"] = float(total_cost_usd)
    return out


def tokens_of(usage: dict | None) -> dict[str, int | None]:
    """``{prompt, cached, cache_write, completion}`` from a normalised usage dict; ``prompt`` counts every input token,
    cached ones included. ``cached``/``cache_write`` are None when not reported. Legacy dicts (vendor keys, stored
    before C2) are read as before."""
    u = usage or {}
    if any(k in u for k in NORMALISED_KEYS):
        unc, cached, cw = _int(u.get("input_uncached")), _int(u.get("input_cached")), _int(u.get("input_cache_write"))
        return {"prompt": (unc or 0) + (cached or 0) + (cw or 0), "cached": cached, "cache_write": cw,
                "completion": _int(u.get("output")) or 0}
    p = _int(u.get("prompt_tokens") or u.get("input_tokens")) or 0
    c = _int(u.get("completion_tokens") or u.get("output_tokens")) or 0
    return {"prompt": p, "cached": None, "cache_write": None, "completion": c}


def call_cost(cfg: ProviderConfig, usage: dict | None, locality: str | None = None) -> float | None:
    """Reported cost, else the backend's prices (any locality), else 0 for local/test, else None (not priced).
    ``locality`` is the effective one (Ollama routing); without it the endpoint decides."""
    u = usage or {}
    reported = u.get("total_cost_usd")
    if isinstance(reported, int | float) and not isinstance(reported, bool):
        return float(reported)
    # a cached or cache-write price alone does not price a backend (its input and output would count as free)
    if cfg.price_input_per_mtok is not None or cfg.price_output_per_mtok is not None:
        t = tokens_of(u)
        pin = cfg.price_input_per_mtok or 0
        cached, cw = t["cached"] or 0, t["cache_write"] or 0
        pcached = cfg.price_cached_input_per_mtok if cfg.price_cached_input_per_mtok is not None else pin
        pcw = cfg.price_cache_write_per_mtok if cfg.price_cache_write_per_mtok is not None else pin
        return ((t["prompt"] - cached - cw) * pin + cached * pcached + cw * pcw
                + t["completion"] * (cfg.price_output_per_mtok or 0)) / 1_000_000
    loc = locality or provider_locality(cfg.kind, cfg.base_url)
    if loc in ("local", "test"):
        return 0.0
    return None


@contextlib.contextmanager
def track(project: Project, stage: str, run_ref: str | None = None) -> Iterator[None]:
    """Log every model call made inside the block (including in tasks it starts) against ``stage`` and ``run_ref``."""
    token = _SCOPE.set({"project": project, "stage": stage, "run_ref": run_ref})
    try:
        yield
    finally:
        _SCOPE.reset(token)


def in_scope() -> bool:
    return _SCOPE.get() is not None


def set_run_ref(run_ref: str) -> None:
    """Name the run inside an open scope once its id is known."""
    scope = _SCOPE.get()
    if scope is not None:
        scope["run_ref"] = run_ref


def record_call(cfg: ProviderConfig, usage: dict | None, ok: bool, locality: str | None = None) -> None:
    """Called by Provider.complete after every call; a no-op outside a usage scope. ``locality`` is the provider's
    effective locality (falls back to the endpoint rule)."""
    scope = _SCOPE.get()
    if scope is None:
        return
    t = tokens_of(usage)
    loc = locality or provider_locality(cfg.kind, cfg.base_url)
    try:
        scope["project"].store.execute(
            "INSERT INTO llm_usage(at,stage,run_ref,job_id,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,"
            "cost_usd,cached_tokens,cache_write_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), scope["stage"], scope.get("run_ref"), JOB_ID.get(), cfg.name, cfg.kind, cfg.model, loc, int(ok),
             t["prompt"], t["completion"], call_cost(cfg, usage, loc), t["cached"], t["cache_write"]))
    except Exception:  # logging must never break a run
        pass


_LOCALITY_RANK = ("cloud", "unknown", "network", "local", "test")


def rank_locality(localities: list[str]) -> str | None:
    """The locality a total is shown under: the furthest one any call went (cloud > unknown > network > local)."""
    for loc in _LOCALITY_RANK:
        if loc in localities:
            return loc
    return localities[0] if localities else None


def embedding_call_cost(cfg: EmbeddingConfig, usage: dict | None) -> float | None:
    """The backend's input price when set (any locality), else 0 on this computer or a test backend, else None."""
    t = tokens_of(usage)
    if cfg.price_input_per_mtok is not None:
        return t["prompt"] * cfg.price_input_per_mtok / 1_000_000
    if embedding_locality(cfg.kind, cfg.base_url) in ("local", "test"):
        return 0.0
    return None


def record_embedding_call(cfg: EmbeddingConfig, usage: dict | None, ok: bool) -> None:
    """One row per embedding API batch (``embeddings/base.py``); a no-op outside a usage scope. Only the token count
    is stored, never a text."""
    scope = _SCOPE.get()
    if scope is None:
        return
    t = tokens_of(usage)
    try:
        scope["project"].store.execute(
            "INSERT INTO llm_usage(at,stage,run_ref,job_id,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,"
            "cost_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), scope["stage"], scope.get("run_ref"), JOB_ID.get(), cfg.name, f"embedding:{cfg.kind}", cfg.model,
             embedding_locality(cfg.kind, cfg.base_url), int(ok), t["prompt"], 0,
             embedding_call_cost(cfg, usage) if ok else 0.0))
    except Exception:  # logging must never break a run
        pass


def _totals(rows) -> dict[str, Any]:
    out = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "unpriced_calls": 0, "localities": [],
           "backends": [], "cached_tokens": None}
    for r in rows:
        out["calls"] += r["n"]
        out["prompt_tokens"] += r["p"] or 0
        out["completion_tokens"] += r["c"] or 0
        out["cost_usd"] += r["cost"] or 0.0
        out["unpriced_calls"] += r["unpriced"] or 0
        if r["cache_reported"]:
            out["cached_tokens"] = (out["cached_tokens"] or 0) + (r["cached"] or 0)
        if r["locality"] not in out["localities"]:
            out["localities"].append(r["locality"])
        b = f"{r['provider']} ({r['model']})"
        if b not in out["backends"]:
            out["backends"].append(b)
    out["tokens"] = out["prompt_tokens"] + out["completion_tokens"]
    out["cost_usd"] = round(out["cost_usd"], 4)
    out["cost_known"] = out["unpriced_calls"] == 0
    out["locality"] = rank_locality(out["localities"])
    return out


_GROUP = ("SELECT provider, model, locality, COUNT(*) n, SUM(prompt_tokens) p, SUM(completion_tokens) c, SUM(cost_usd) cost, "
          "SUM(cost_usd IS NULL) unpriced, SUM(cached_tokens) cached, SUM(cached_tokens IS NOT NULL) cache_reported "
          "FROM llm_usage")


def cache_text(u: dict[str, Any] | None) -> str:
    """The cache use of a set of calls: their cached input tokens, or "cache use not reported" when no call gave any."""
    if not u or u.get("cached_tokens") is None:
        return "cache use not reported"
    return f"{u['cached_tokens']:,} cached tokens"


def _destinations(project: Project, rows) -> list[str]:
    """Where the logged calls went, in words (``core/detect.py:destination``), for the usage chip's tooltip."""
    from .detect import destination

    out: list[str] = []
    for r in rows:
        try:
            cfg = project.provider(r["provider"])
        except (KeyError, TypeError):
            continue
        d = destination(cfg, r["locality"])
        if d not in out:
            out.append(d)
    return out


def usage_for_runs(project: Project, run_refs: list[str], after: int = 0) -> dict[str, Any] | None:
    """Calls logged against ``run_refs``; with ``after`` (a usage_id) only the calls made since then."""
    if not run_refs:
        return None
    rows = project.store.query(f"{_GROUP} WHERE run_ref IN ({','.join('?' * len(run_refs))}) AND usage_id > ? "
                               "GROUP BY provider, model, locality", [*run_refs, after])
    t = _totals(rows)
    t["destinations"] = _destinations(project, rows)
    return t if t["calls"] else None


def usage_for_job(project: Project, job_id: str) -> dict[str, Any]:
    rows = project.store.query(f"{_GROUP} WHERE job_id=? GROUP BY provider, model, locality", (job_id,))
    t = _totals(rows)
    t["destinations"] = _destinations(project, rows)
    return t


def per_run(project: Project) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in project.store.query(f"{_GROUP.replace('SELECT ', 'SELECT run_ref, ')} WHERE run_ref IS NOT NULL GROUP BY run_ref, provider, model, locality"):
        out.setdefault(r["run_ref"], []).append(r)
    res = {}
    for k, v in out.items():
        res[k] = _totals(v)
        res[k]["destinations"] = _destinations(project, v)
    return res


LEGACY_COST_LABEL = "estimated at today's prices"


def project_totals(project: Project) -> dict[str, Any]:
    """All logged calls, plus screening calls stored before the log existed. Those have no stored cost: they are priced
    at the backend's current prices and kept apart (``legacy_cost_usd``, :data:`LEGACY_COST_LABEL`); ``cost_usd`` is
    the sum of both and ``logged_cost_usd`` the cost logged when the calls ran (never recomputed)."""
    st = project.store
    t = _totals(st.query(f"{_GROUP} GROUP BY provider, model, locality"))
    logged_cost = t["cost_usd"]
    legacy_cost = 0.0
    logged_runs = {r["run_ref"] for r in st.query("SELECT DISTINCT run_ref FROM llm_usage WHERE run_ref IS NOT NULL")}
    legacy = 0
    for r in st.query("SELECT c.run_id, c.usage, r.provider FROM screen_calls c LEFT JOIN runs r ON r.run_id=c.run_id"):
        if r["run_id"] in logged_runs:
            continue
        legacy += 1
        tk = tokens_of(json.loads(r["usage"] or "{}") if r["usage"] else {})
        t["prompt_tokens"] += tk["prompt"]
        t["completion_tokens"] += tk["completion"]
        t["calls"] += 1
        try:
            cfg = project.provider(r["provider"])
        except (KeyError, TypeError):
            t["unpriced_calls"] += 1
            continue
        cost = call_cost(cfg, json.loads(r["usage"] or "{}") if r["usage"] else {})
        if cost is None:
            t["unpriced_calls"] += 1
        else:
            t["cost_usd"] += cost
            legacy_cost += cost
        loc = provider_locality(cfg.kind, cfg.base_url)
        if loc not in t["localities"]:
            t["localities"].append(loc)
    t["tokens"] = t["prompt_tokens"] + t["completion_tokens"]
    t["cost_usd"] = round(t["cost_usd"], 4)
    t["cost_known"] = t["unpriced_calls"] == 0
    t["locality"] = rank_locality(t["localities"])
    t["legacy_calls"] = legacy
    t["logged_cost_usd"] = round(logged_cost, 4)
    t["legacy_cost_usd"] = round(legacy_cost, 4)
    t["legacy_label"] = LEGACY_COST_LABEL
    return t
