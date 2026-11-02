"""Cost ranges before a run and the projection while it runs (design Decision 15).

:func:`cost_range` turns the calls a run will make (system prompt + user text each) into token counts and a cost
range: the minimum assumes the most eligible caching (each system prompt written to the vendor cache once, then read
from it), the maximum no caching; both include output. Token counts come from characters ÷ 4, or from this backend's
earlier calls of the same stage when there are some. A local or test backend without prices gives 0–0; an unpriced
remote one gives "not priced" with its token counts. Prices come from :func:`wider.core.usage.call_cost`, so the
estimate and the logged cost follow the same rules.

:func:`projection` is the running figure of the UI job bar and of the CLI progress lines (one helper, so they cannot
differ): the cost so far plus the calls left at the observed mean cost per call, bounded by the pre-run range.
"""
from __future__ import annotations

import json
import statistics
from typing import Any

from .project import Project
from .usage import _GROUP, _totals, call_cost, embedding_call_cost, normalised, tokens_of

CHARS_PER_TOKEN = 4


def usd(x: float | None) -> str:
    """Dollars as the job bar writes them: 2 decimals from 1 USD, else 4."""
    if x is None:
        return "not priced"
    return f"${x:.2f}" if x >= 1 else f"${x:.4f}"


def _observed(project: Project, cfg, stage: str) -> dict[str, Any]:
    """Earlier calls of this backend and stage: output tokens (for the median) and, for screening, the tokens per
    character of the record texts they were given."""
    st = project.store
    outs = [r["completion_tokens"] for r in st.query(
        "SELECT completion_tokens FROM llm_usage WHERE provider=? AND stage=? AND ok=1 AND completion_tokens>0",
        (cfg.name, stage))]
    ratio = None
    if stage == "screen":
        from ..screen.engine import user_message

        toks = chars = 0
        for r in st.query("SELECT c.usage, m.title, m.abstract, r.dir FROM screen_calls c JOIN runs r ON r.run_id=c.run_id "
                          "JOIN records m ON m.record_id=c.record_id WHERE r.provider=? AND c.ok=1 AND c.usage IS NOT NULL",
                          (cfg.name,)):
            t = tokens_of(json.loads(r["usage"]) if r["usage"] else {})["prompt"]
            if not t:
                continue
            prompt = project.root / r["dir"] / "prompt.md"
            sys_chars = len(prompt.read_text(encoding="utf-8")) if prompt.exists() else 0
            toks += t
            chars += sys_chars + len(user_message(r["title"], r["abstract"]))
        if toks and chars:
            ratio = toks / chars
    return {"outputs": outs, "tokens_per_char": ratio}


def cost_range(project: Project, cfg, stage: str, calls: list[tuple[str, str]], max_tokens: int | None = None,
               embedding: bool = False, probe: bool = True) -> dict[str, Any]:
    """``calls``: one ``(system, user)`` pair per model call (embeddings: ``("", text)``). Returns ``{min_usd,
    max_usd, priced, calls, input_tokens, output_tokens, destination, locality, assumptions}``; the dollar figures
    are None when the backend is not priced. ``probe=False`` (the estimate a page shows when it opens) makes no
    request: an LLM backend is placed as its row is (``backends.llm_locality``)."""
    from .detect import destination, effective_locality
    from .summary import embedding_locality

    assumptions: list[str] = []
    obs = _observed(project, cfg, stage) if not embedding else {"outputs": [], "tokens_per_char": None}
    if obs["tokens_per_char"]:
        tpc = obs["tokens_per_char"]
        assumptions.append(f"input tokens from earlier calls of this backend ({1 / tpc:.1f} characters per token)")
    else:
        tpc = 1 / CHARS_PER_TOKEN
        assumptions.append(f"input tokens estimated as characters ÷ {CHARS_PER_TOKEN}")

    def tok(text: str) -> int:
        return round(len(text) * tpc)

    n = len(calls)
    if embedding:
        out_per_call = 0
        assumptions.append("embeddings: input tokens only, no caching; texts already in the embedding cache are not "
                           "sent, so the real cost can be lower")
    elif obs["outputs"]:
        out_per_call = int(statistics.median(obs["outputs"]))
        assumptions.append(f"output tokens: median of {len(obs['outputs'])} earlier calls ({out_per_call:,} per call)")
    else:
        out_per_call = max_tokens or 0
        assumptions.append(f"output tokens: max_tokens per call ({out_per_call:,})")
    user_tokens = sum(tok(u) for _, u in calls)
    sys_tokens = [tok(s) for s, _ in calls]
    input_tokens = user_tokens + sum(sys_tokens)
    output_tokens = out_per_call * n
    # minimum: each distinct system prompt is written to the cache on its first call and read from it afterwards
    first: set[str] = set()
    write = cached = 0
    for (s, _), t in zip(calls, sys_tokens):
        if not t:
            continue
        if s in first:
            cached += t
        else:
            first.add(s)
            write += t
    if embedding:
        loc = embedding_locality(cfg.kind, cfg.base_url)
        dest = destination(cfg, loc)
        lo = hi = embedding_call_cost(cfg, normalised(input_uncached=input_tokens))
    else:
        if probe:
            loc, _src, dest = effective_locality(cfg)
        else:
            from .backends import llm_locality

            loc, _src = llm_locality(project, cfg)
            dest = destination(cfg, loc)
        lo = call_cost(cfg, normalised(input_uncached=user_tokens, input_cached=cached, input_cache_write=write,
                                       output=output_tokens), loc)
        hi = call_cost(cfg, normalised(input_uncached=input_tokens, output=output_tokens), loc)
        if lo is not None and hi is not None and lo > hi:   # a cache-write premium without a cheaper cached price
            lo = hi
        elif n > 1 and cached:
            assumptions.append("minimum: the system prompt is cached after the first call (one cache write)")
        assumptions.append("maximum: no caching")
    priced = lo is not None and hi is not None
    if not priced:
        assumptions.append("not priced: this backend has no prices, so only token counts are shown")
    elif loc in ("local", "test") and not (lo or hi):
        assumptions.append("runs on this computer or a test backend without prices: no cost")
    return {"min_usd": round(lo, 6) if priced else None, "max_usd": round(hi, 6) if priced else None, "priced": priced,
            "calls": n, "input_tokens": input_tokens, "output_tokens": output_tokens, "destination": dest,
            "locality": loc, "backend": cfg.name, "assumptions": assumptions}


def range_text(r: dict[str, Any]) -> list[str]:
    """The estimate in plain words, one line per item (the CLI prints these; the UI builds the same from the dict)."""
    money = f"{usd(r['min_usd'])}–{usd(r['max_usd'])}" if r["priced"] else "not priced"
    lines = []
    if r.get("reuse"):
        u = r["reuse"]
        lines.append(f"{u['in_scope']:,} records in scope; {u['reusable']:,} identical evaluations can be reused; "
                     f"{u['new_calls']:,} new calls")
    lines += [f"Estimated cost: {money} for {r['calls']:,} call{'s' if r['calls'] != 1 else ''} to {r['destination']} "
             f"(input ≈ {r['input_tokens']:,} tokens, output ≈ {r['output_tokens']:,} tokens)"]
    lines += [pass_range_text(x) for x in r.get("per_pass") or []]
    lines += [f"  assumption: {a}" for a in r["assumptions"]]
    return lines


# ----------------------------------------------------------------- the calls of each stage
def screen_calls_for(project: Project, mode: str, scope: str | None = None, protocol: str = "main",
                     provider_name: str | None = None, prompt_version: int | None = None,
                     limit: int | None = None, reuse_evaluations: bool = False,
                     reuse: dict | None = None, provider_b: str | None = None,
                     provider_c: str | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """``(groups, notes)`` of a screening pass (``mode`` pass), a protocol or a second screen: one group per distinct
    pass backend, ``{passes, cfg, calls, max_tokens}`` (design D10: pass B and the second screen's S2 run on pass B's
    backend). With ``reuse_evaluations`` the calls whose evaluation key matches a stored successful call are left out,
    and ``reuse`` (when given) receives ``{in_scope, reusable, new_calls}``."""
    from ..screen.codebook import load_prompt
    from ..screen.engine import record_set, user_message
    from ..screen.protocol import pass_backends
    from .protocol import backend_output_limit

    _row, pdata = load_prompt(project, prompt_version)
    notes: list[str] = []
    if mode not in ("protocol", "second"):   # a plain pass
        cfg = project.provider(provider_name) if provider_name else project.provider_for("screen")
        passes = [("A", cfg)]
    else:
        bk = pass_backends(project, provider_name, provider_b, provider_c)
        if mode == "second":
            scope = f"maybe:{protocol}"
            passes = [("S1", bk["A"]), ("S2", bk["B"])]
            notes.append("two reads of each maybe; the tie-break read where they disagree is not counted")
        else:
            passes = [("A", bk["A"])] if project.config.protocol.screening.passes == 1 else [("A", bk["A"]),
                                                                                              ("B", bk["B"])]
            if len(passes) == 2:
                notes.append("passes A and B; the third read where they disagree is not counted")
    recs = record_set(project, scope or "corpus", limit)
    msgs = [user_message(r["title"], r["abstract"]) for r in recs]
    max_tokens = project.config.protocol.screening.max_tokens
    groups: dict[str, dict[str, Any]] = {}
    n_reused = n_new = 0
    for name, cfg in passes:
        calls = [(pdata["prompt"], m) for m in msgs]
        if reuse_evaluations and mode in ("protocol", "second"):
            calls, k = _not_reusable(project, cfg, pdata, msgs, mode, name, max_tokens, prompt_version)
            n_reused += k
        n_new += len(calls)
        g = groups.setdefault(cfg.name, {"passes": [], "cfg": cfg, "calls": [], "max_tokens": max_tokens})
        g["passes"].append(name)
        g["calls"] += calls
    if reuse is not None and reuse_evaluations and mode in ("protocol", "second"):
        reuse.update(in_scope=len(recs), reusable=n_reused, new_calls=n_new)
    for g in groups.values():
        limit_out = backend_output_limit(g["cfg"])
        if limit_out is not None and limit_out < max_tokens:   # the cost-range maximum uses the smaller (Decision 26)
            g["max_tokens"] = limit_out
            notes.append(f"output of {g['cfg'].name} capped at the backend's {limit_out:,} tokens per call"
                         if len(groups) > 1 else f"output capped at the backend's {limit_out:,} tokens per call")
    return list(groups.values()), notes


def _not_reusable(project: Project, cfg, pdata: dict, msgs: list[str], mode: str, pass_name: str, max_tokens: int,
                  prompt_version: int | None) -> tuple[list[tuple[str, str]], int]:
    """The calls of one pass of a protocol or second screen that no stored evaluation can stand in for, and how many
    can: the evaluation key of each record is computed as the run computes it (screen/engine.py ``eval_key``) with
    that pass's own backend ``cfg``."""
    from ..providers.registry import backend_identity
    from ..screen.codebook import load_prompt
    from ..screen.engine import eval_key
    from ..screen.result_sets import method_identity

    prow, _p = load_prompt(project, prompt_version)
    method = method_identity(backend_identity(cfg))
    offset = {"A": 0, "B": 1, "S1": 10, "S2": 11}[pass_name]
    kind = "second_screen" if mode == "second" else "screen"
    st = project.store
    left, n = [], 0
    seed = (cfg.seed or 0) + offset if cfg.seed is not None else offset
    for m in msgs:
        key = eval_key(pdata["prompt"], m, method, cfg.temperature, seed, kind, max_tokens, pass_name, prow["version"])
        if st.scalar("SELECT COUNT(*) FROM screen_calls WHERE eval_key=? AND ok=1 "
                     "AND COALESCE(provider,'')<>'researcher'", (key,)):
            n += 1
        else:
            left.append((pdata["prompt"], m))
    return left, n


def _sum_ranges(parts: list[dict[str, Any]]) -> dict[str, Any]:
    """One range from the ranges of several pass backends, each kept under ``per_pass``."""
    priced = all(r["priced"] for r in parts)
    locs = [r["locality"] for r in parts]
    far = [x for x in locs if x not in ("local", "test")]
    dests = list(dict.fromkeys(r["destination"] for r in parts))
    assumptions = list(dict.fromkeys(a for r in parts for a in r["assumptions"]))
    return {"min_usd": round(sum(r["min_usd"] for r in parts), 6) if priced else None,
            "max_usd": round(sum(r["max_usd"] for r in parts), 6) if priced else None, "priced": priced,
            "calls": sum(r["calls"] for r in parts), "input_tokens": sum(r["input_tokens"] for r in parts),
            "output_tokens": sum(r["output_tokens"] for r in parts), "destination": "; ".join(dests),
            "locality": far[0] if far else locs[0], "backend": ", ".join(r["backend"] for r in parts),
            "assumptions": assumptions, "per_pass": parts}


def estimate_screen(project: Project, mode: str = "pass", probe: bool = True, **kw) -> dict[str, Any]:
    """The cost range of a screening, summed over the pass backends (each priced at its own rates, design D10);
    with more than one backend ``per_pass`` holds each one's range with its ``passes``."""
    reuse: dict[str, int] = {}
    groups, notes = screen_calls_for(project, mode, reuse=reuse, **kw)
    parts = []
    for g in groups:
        r = cost_range(project, g["cfg"], "screen", g["calls"], g["max_tokens"], probe=probe)
        r["passes"] = g["passes"]
        parts.append(r)
    r = parts[0] if len(parts) == 1 else _sum_ranges(parts)
    r["assumptions"] = r["assumptions"] + notes
    if reuse:
        r["reuse"] = reuse
    return r


def pass_range_text(r: dict[str, Any]) -> str:
    """One pass backend's line of a per-pass estimate."""
    who = "pass " + " and ".join(r["passes"]) + f" on {r['backend']}"
    n = f"{r['calls']:,} call{'s' if r['calls'] != 1 else ''}"
    if r["locality"] in ("local", "test") and (not r["priced"] or not (r["min_usd"] or r["max_usd"])):
        return f"  {who}: local, no charge ({n})"
    money = f"{usd(r['min_usd'])}–{usd(r['max_usd'])}" if r["priced"] else "not priced"
    return f"  {who}: {money} for {n} to {r['destination']}"


def estimate_construct(project: Project, kind: str, provider_name: str | None = None,
                       probe: bool = True) -> dict[str, Any]:
    from ..construct.propose import USER_MESSAGE, build_prompt, check_kind

    check_kind(kind)
    cfg = project.provider(provider_name) if provider_name else project.provider_for("construct")
    if kind == "anchors":   # one call per cell (the default per-cell proposal)
        _p, info = build_prompt(project, kind)
        prompts = [build_prompt(project, kind, cell_keys=[k])[0] for k in info["cell_keys"]] or [_p]
    else:
        prompts = [build_prompt(project, kind)[0]]
    return cost_range(project, cfg, "construct", [(p, USER_MESSAGE) for p in prompts], max_tokens=8000, probe=probe)


def estimate_embed(project: Project, name: str | None = None) -> dict[str, Any]:
    from ..rank.corpus import corpus_texts

    cfg = project.embedding(name)
    _ids, texts, _thin = corpus_texts(project)
    return cost_range(project, cfg, "rank", [("", t) for t in texts], embedding=True)


# ----------------------------------------------------------------- during a run
def usage_since(project: Project, after: int) -> dict[str, Any]:
    """Totals of the calls logged after usage row ``after``."""
    rows = project.store.query(f"{_GROUP} WHERE usage_id > ? GROUP BY provider, model, locality", (after,))
    return _totals(rows)


def projection(usage: dict[str, Any] | None, calls_left: int, pre_range: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """``{cost_so_far_usd, projected_min_usd, projected_max_usd}``: the cost so far plus ``calls_left`` at the observed
    mean cost per call; with ``pre_range`` the per-call figure is bounded by its minimum and maximum per call. None when
    nothing is priced yet."""
    if not usage or not usage.get("calls") or not usage.get("cost_known"):
        return None
    so_far = float(usage.get("cost_usd") or 0.0)
    mean = so_far / usage["calls"]
    lo_call = hi_call = mean
    if pre_range and pre_range.get("priced") and pre_range.get("calls"):
        lo_call = min(mean, pre_range["min_usd"] / pre_range["calls"])
        hi_call = max(mean, pre_range["max_usd"] / pre_range["calls"])
    left = max(0, calls_left)
    return {"cost_so_far_usd": round(so_far, 6), "projected_min_usd": round(so_far + left * lo_call, 6),
            "projected_max_usd": round(so_far + left * hi_call, 6)}


def projection_text(p: dict[str, Any] | None) -> str:
    """"cost so far $x · projected $a–$b" (the job bar's ``usageText`` writes the same)."""
    if not p:
        return ""
    return (f"cost so far {usd(p['cost_so_far_usd'])} · projected {usd(p['projected_min_usd'])}–"
            f"{usd(p['projected_max_usd'])}")


def calls_left(n_done: int | None, n_total: int | None, usage: dict[str, Any] | None,
               pre_range: dict[str, Any] | None) -> int:
    """Calls still to make: the pre-run count minus the calls logged so far, else the job's own counter."""
    if pre_range and pre_range.get("calls"):
        return max(0, pre_range["calls"] - ((usage or {}).get("calls") or 0))
    return max(0, (n_total or 0) - (n_done or 0))
