"""One preflight for every way a screening starts (CLI, job route, synchronous route): the refusals of Decisions 16,
23 and 26 before consent, a job, a run folder or a call. Nothing is written.

:func:`preflight_screen` returns ``{"status": "ok"}`` or ``{"status": "already_done", ...}`` (a plain pass whose run
finished under another model) and raises BackendChangeNeeded, NewSetNeeded, ScopeNeeded, ValueError or KeyError.
"""
from __future__ import annotations

from typing import Any

from ..core.project import Project


def _backend_check(project: Project, kind: str, scope: str, passes: dict[str, str | None], prompt_version: int | None,
                   provider_name: str | None, protocol: str | None, result_set: int | None, limit: int | None,
                   choice: str | None, skip_ids=None, pass_cfgs: dict | None = None) -> None:
    """Each pass's own backend (``pass_cfgs``: pass -> backend, else ``provider_name`` for all) against that pass's
    stored run (design D10)."""
    from ..providers.registry import backend_identity
    from .engine import BackendChangeNeeded, backend_change

    base = project.provider(provider_name) if provider_name else project.provider_for("screen")
    for name, rid in passes.items():
        cfg = (pass_cfgs or {}).get(name) or base
        ch = backend_change(project, kind, scope, name, prompt_version, cfg.name if pass_cfgs else provider_name,
                            protocol, result_set, skip_ids, limit, run_id=rid, identity=backend_identity(cfg))
        if ch is None or ch["operational_only"]:
            continue
        if choice is None:
            raise BackendChangeNeeded(ch)
        if choice == "new" and ch["case"] == "a" and protocol is None:
            raise ValueError(f"run {ch['run_id']} is unfinished under {ch['from']['model']}; a separate run needs "
                             f"another backend name (wider providers add --preset … --name {cfg.name}2) or set the "
                             "backend back")


def preflight_screen(project: Project, mode: str = "pass", scope: str = "corpus", protocol: str = "main",
                     pass_name: str = "A", provider_name: str | None = None, prompt_version: int | None = None,
                     limit: int | None = None, backend_change: str | None = None, new_set: bool = False,
                     reuse_from: int | None = None, no_reuse: bool = False, from_set: int | None = None,
                     temperature: float | None = None, provider_b: str | None = None,
                     provider_c: str | None = None) -> dict[str, Any]:
    from ..core.protocol import check_max_tokens
    from . import result_sets as rs
    from .codebook import load_prompt
    from .engine import _done_ids, _run_id, check_protocol_name, check_run_args, record_set, run_backend
    from .protocol import (
        SECOND_PASS,
        _set_runs,
        check_pass_tokens,
        check_plan,
        check_second_screen,
        pass_backends,
        passes_run,
        second_protocol_name,
    )
    from .result_sets import method_identity

    if backend_change not in (None, "continue", "new"):
        raise ValueError(f"backend_change is continue or new (got {backend_change!r})")
    check_run_args(pass_name if mode == "pass" else "A", limit)
    from .devgate import check_run

    check_run(project, mode, scope, protocol)   # ui-redesign D4, before every other check
    if mode in ("protocol", "second"):   # a bad name is refused before anything else is read
        check_protocol_name(protocol)
    if mode in ("protocol", "second"):
        allb = pass_backends(project, provider_name, provider_b, provider_c)
        run = passes_run(project, second=mode == "second")
        check_pass_tokens(project, {p: allb[p] for p in run})
        cfg = allb["A"]
    else:
        cfg = project.provider(provider_name) if provider_name else project.provider_for("screen")
        check_max_tokens(project.config.protocol.screening.max_tokens, cfg)
    prow, pdata = load_prompt(project, prompt_version)
    if mode == "pass":
        _backend_check(project, "screen", scope, {pass_name: None}, prompt_version, provider_name, None, None, limit,
                       backend_change)
        rid = _run_id(project, "screen", pass_name, prow["version"], cfg.name, scope, None, None)
        run = project.store.one("SELECT run_id FROM runs WHERE run_id=?", (rid,))
        if run:
            from ..providers.registry import backend_identity
            ids = {r["record_id"] for r in record_set(project, scope, limit)}
            old = run_backend(project, rid)
            if not (ids - _done_ids(project, rid)) and old is not None \
                    and method_identity(old) != method_identity(backend_identity(cfg)):
                return {"status": "already_done", "already_done": {"run_id": rid, "model": old.get("model")}}
        return {"status": "ok"}
    if mode == "protocol":
        settings = rs.settings_for(project, cfg, pdata, prow["version"], temperature,
                                   passes={p: allb[p] for p in run if p != "A"})
        out, kind, passes = protocol, "screen", ("A",) if project.config.protocol.screening.passes == 1 else ("A", "B")
        pass_cfgs = {p: allb[p] for p in ("A", "B", "C")}
    elif mode == "second":
        check_second_screen(project)
        out = second_protocol_name(protocol)
        src_key = rs.active_key(project, protocol, from_set)
        src_n = rs.split_key(src_key)[1]
        scope = f"maybe:{protocol}#{src_n}"
        settings = rs.settings_for(project, cfg, pdata, prow["version"], temperature, kind="second_screen",
                                   source=(protocol, src_n), passes={"S2": allb["B"], "S3": allb["C"]})
        kind, passes = "second_screen", ("S1", "S2")
        pass_cfgs = {p: allb[a] for p, a in SECOND_PASS.items()}
        no_reuse, reuse_from = True, None
    else:
        raise ValueError(f"mode is pass, protocol or second (got {mode!r})")
    plan = check_plan(project, out, scope, settings, new_set, reuse_from, no_reuse)
    if plan["action"] == "continue":
        row = rs.row_by_key(project, plan["key"])
        recorded = _set_runs(row)
        _backend_check(project, kind, scope, {p: recorded.get(p) for p in passes}, prompt_version, provider_name, out,
                       plan["set"], limit, backend_change, rs.carried_ids(project, plan["key"]), pass_cfgs)
    return {"status": "ok", "plan": {k: plan.get(k) for k in ("action", "set", "to_screen", "reuse", "same_as")}}


def refusal_body(e: Exception) -> dict[str, Any] | None:
    """The 409 body of a preflight refusal, or None for an ordinary error (400)."""
    from .engine import BackendChangeNeeded
    from .result_sets import NewSetNeeded, ScopeNeeded

    if isinstance(e, BackendChangeNeeded):
        return {"detail": str(e), "backend_change": e.change}
    if isinstance(e, NewSetNeeded):
        pl = e.plan
        return {"detail": str(e), "new_set_needed": {k: pl.get(k) for k in
                                                     ("protocol", "set", "changed", "reusable", "to_screen", "in_scope",
                                                      "same_as", "rule")}}
    if isinstance(e, ScopeNeeded):
        return {"detail": str(e), "scope_needed": {"protocol": e.protocol, "set": e.set}}
    from .devgate import DevSampleIncomplete

    if isinstance(e, DevSampleIncomplete):   # ui-redesign D4: the counts with the sentence
        return {"detail": str(e), "dev_incomplete": e.counts}
    return None
