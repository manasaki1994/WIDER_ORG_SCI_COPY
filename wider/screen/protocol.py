"""Corpus protocol: passes A and B; where they disagree a blind third read C; final = 2-of-3 majority,
a three-way split is maybe (with ``screening.passes: 1`` pass A alone decides). Second screen of the maybes: two reads,
a tie-break where they disagree (refused while ``screening.second_screen`` is off).
Writes one decision per record into screen_decisions under a protocol name."""
from __future__ import annotations

import json
from collections import Counter

from ..core.project import Project
from .codebook import load_prompt
from .decisions import labels_from_parsed, set_decision
from .engine import check_protocol_name, screen_async

ORDER = {"include": 0, "maybe": 1, "exclude": 2}


def _label_groups(project: Project, run_id: str) -> list[str]:
    """Label fields of the prompt artifact a run used (cell plus project labels)."""
    r = project.store.one("SELECT prompt_version FROM runs WHERE run_id=?", (run_id,))
    try:
        _, pdata = load_prompt(project, r["prompt_version"] if r else None)
    except ValueError:
        return []
    return list(pdata.get("label_groups", []))


def _decisions(project: Project, run_id: str) -> dict[int, dict]:
    """The successful evaluations of a run (its own calls and the ones it reused), with ``call_id``."""
    from .engine import run_calls

    return {c["record_id"]: c for c in run_calls(project, run_id) if c["ok"]}


def majority(votes: list[str], needed: int = 2) -> str:
    c = Counter(v for v in votes if v)
    if not c:
        return "maybe"
    top, n = c.most_common(1)[0]
    return top if n >= needed else "maybe"


def _non_backend(settings: dict | None) -> dict:
    """The settings minus the backends (pass A's and the per-pass ones, design D11)."""
    return {k: v for k, v in (settings or {}).items() if k not in ("backend", "backends")}


# ----------------------------------------------------------------------------- a backend per pass (design D10, D13)
PASS_STAGE = {"B": "screen_b", "C": "screen_judge"}
SECOND_PASS = {"S1": "A", "S2": "B", "S3": "C"}   # the second screen's reads follow A, B and the judge
PASS_WORD = {"A": "A", "B": "B", "C": "the judge", "S1": "S1", "S2": "S2", "S3": "S3"}


def pass_backends(project: Project, provider_name: str | None = None, provider_b: str | None = None,
                  provider_c: str | None = None) -> dict:
    """``{"A", "B", "C": ProviderConfig}``: pass A is ``provider_name``, else stage ``screen``, else the default; B is
    ``provider_b``, else stage ``screen_b``, else A; the judge C is ``provider_c``, else ``screen_judge``, else A. A
    stage override naming a removed backend falls to A. An unknown flag name raises KeyError."""
    a = project.provider(provider_name) if provider_name else project.provider_for("screen")
    out = {"A": a}
    for p, name in (("B", provider_b), ("C", provider_c)):
        if name:
            out[p] = project.provider(name)
            continue
        override = project.config.stage_providers.get(PASS_STAGE[p])
        try:
            out[p] = project.provider(override) if override else a
        except KeyError:
            out[p] = a
    return out


def passes_run(project: Project, second: bool = False) -> list[str]:
    """The passes a protocol run can make (``A``, ``B``, ``C``; the second screen always has S1, S2 and S3)."""
    if second:
        return ["A", "B", "C"]
    sc = project.config.protocol.screening
    if sc.passes == 1:
        return ["A"]
    return ["A", "B"] + (["C"] if sc.blind_third_read else [])


def pass_off(project: Project) -> dict[str, str | None]:
    """Why pass B or the judge does not run in this protocol (None when it does): the picker's and the note's text."""
    sc = project.config.protocol.screening
    if sc.passes == 1:
        return {"B": "pass B does not run in this protocol (screening.passes: 1)",
                "C": "the judge does not run in this protocol (screening.passes: 1)"}
    return {"B": None, "C": None if sc.blind_third_read else
            "the judge does not run in this protocol (screening.blind_third_read: false)"}


def unused_pass_notes(project: Project, provider_b: str | None = None, provider_c: str | None = None) -> list[str]:
    """A note for each per-pass flag given for a pass the protocol does not run (not a refusal, design D10)."""
    off = pass_off(project)
    out = []
    for p, flag, given in (("B", "--provider-b", provider_b), ("C", "--provider-c", provider_c)):
        if given and off[p]:
            out.append(f"{off[p]}; {flag} is not used")
    return out


def pass_backend_names(project: Project, mode: str, provider_name: str | None = None, provider_b: str | None = None,
                       provider_c: str | None = None) -> list[str | None]:
    """The distinct backends a screening's passes may run on, pass A's first: what consent covers before any call.
    A plain pass (or an unknown name, which the run itself reports) gives ``[provider_name]``."""
    if mode not in ("protocol", "second"):
        return [provider_name]
    try:
        allb = pass_backends(project, provider_name, provider_b, provider_c)
    except KeyError:
        return [provider_name]
    return list(dict.fromkeys(allb[x].name for x in passes_run(project, second=mode == "second")))


def _pass_identity(cfg, temperature: float | None = None) -> dict:
    from .result_sets import method_identity

    ident = method_identity(cfg)
    ident["temperature"] = temperature if temperature is not None else cfg.temperature
    return ident


def _num(t) -> str:
    return f"{t:g}" if isinstance(t, (int, float)) else str(t)


def shared_passes(backends: dict, temperature: float | None = None, second: bool = False) -> list[str]:
    """A sentence for each group of passes that share one model and temperature (design D13; never a refusal).
    ``backends``: pass (``A``, ``B``, ``C``) -> ProviderConfig, the passes that run."""
    groups: list[tuple[dict, list[str]]] = []
    for p, cfg in backends.items():
        ident = _pass_identity(cfg, temperature)
        for g in groups:
            if g[0] == ident:
                g[1].append(p)
                break
        else:
            groups.append((ident, [p]))
    word = {"A": "S1", "B": "S2", "C": "S3"} if second else PASS_WORD
    out = []
    for ident, ps in groups:
        if len(ps) < 2:
            continue
        cfg = backends[ps[0]]
        out.append(_shared_sentence([word[p] for p in ps], getattr(cfg, "model", None) or cfg.name,
                                    ident["temperature"]))
    return out


def _join(names: list[str]) -> str:
    return " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]


def _shared_sentence(names: list[str], model, temperature) -> str:
    # "Passes A and B", "Passes A, B and the judge", but "Pass A and the judge" (one lettered pass)
    lettered = [n for n in names if not n.startswith("the ")]
    head = "Pass" if len(lettered) == 1 else "Passes"
    return (f"{head} {_join(names)} use the same model ({model}, temperature {_num(temperature)}): "
            "their readings are not independent.")


PASS_ORDER = ("A", "B", "C", "S1", "S2", "S3")
PASS_NOUN = {"A": "pass A", "B": "pass B", "C": "the judge", "S1": "read S1", "S2": "read S2", "S3": "read S3"}


def pass_models(project: Project, key: str) -> dict:
    """The models behind set ``key``'s votes per pass, ``{"models": {pass: [model, ...]}, "shared": [...],
    "sentence": str | None}`` (design D15): read from the calls its decisions count (carried votes included), so a
    pass that never ran (the judge without a disagreement) is not named. ``shared`` names the passes whose recorded
    settings give one model and temperature, in the words of :func:`shared_passes`."""
    from .result_sets import pass_settings, row_by_key

    st = project.store
    ids: list[int] = []
    for (vc,) in st.query("SELECT vote_calls FROM screen_decisions WHERE protocol=? AND vote_calls IS NOT NULL", (key,)):
        try:
            ids += [int(i) for i in json.loads(vc) or [] if i is not None]
        except (TypeError, ValueError):
            continue
    found: dict[str, list[str]] = {}
    uniq = sorted(set(ids))
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in st.query(f"SELECT c.pass, COALESCE(c.model, r.model) m FROM screen_calls c JOIN runs r "
                          f"ON r.run_id=c.run_id WHERE c.call_id IN ({q}) AND COALESCE(c.provider, '') != 'researcher' "
                          f"ORDER BY c.call_id", chunk):
            ms = found.setdefault(r["pass"], [])
            if r["m"] and r["m"] not in ms:
                ms.append(r["m"])
    models = {p: found[p] for p in PASS_ORDER if p in found}
    models.update({p: v for p, v in found.items() if p not in models})
    row = row_by_key(project, key) or {}
    settings = row.get("settings")
    shared: list[str] = []
    if settings and len(models) > 1:
        groups: list[tuple[dict, list[str]]] = []
        for p in models:
            ident = pass_settings(settings, p) or {}
            for g in groups:
                if g[0] == ident:
                    g[1].append(p)
                    break
            else:
                groups.append((ident, [p]))
        shared = [_shared_sentence([PASS_WORD.get(p, p) for p in ps], g.get("model") or g.get("name"),
                                   g.get("temperature")) for g, ps in groups if len(ps) > 1 and g]
    return {"models": models, "shared": shared, "sentence": _models_sentence(models)}


def _models_sentence(models: dict[str, list[str]]) -> str | None:
    if not models:
        return None
    every = {m for ms in models.values() for m in ms}
    nouns = [PASS_NOUN.get(p, p) for p in models]
    if len(every) == 1 and len(models) > 1:
        return f"Every pass ({_join(nouns)}) used {next(iter(every))}."
    groups: dict[tuple[str, ...], list[str]] = {}   # passes on the same model(s) in one clause, first-seen order
    for p, ms in models.items():
        groups.setdefault(tuple(ms), []).append(PASS_NOUN.get(p, p))
    parts = [f"{_join(ns)} used {_join(list(ms)) if ms else 'an unrecorded model'}" for ms, ns in groups.items()]
    out = _join(parts) + "."
    return out[0].upper() + out[1:]


def pass_view(project: Project, mode: str = "protocol", provider_name: str | None = None,
              provider_b: str | None = None, provider_c: str | None = None) -> dict:
    """What the Screening page's pass pickers show and the CLI prints before a run (design D13, D14): each pass's
    backend name, the stage backends that preselect B and the judge (None: same as pass A), why a pass does not run,
    the notes for flags of passes that do not run, and the sentences naming passes that share a model. An unknown
    backend name raises KeyError."""
    second = mode == "second"
    allb = pass_backends(project, provider_name, provider_b, provider_c)
    names = {p.name for p in project.config.providers}
    defaults = {p: (n if (n := project.config.stage_providers.get(PASS_STAGE[p])) in names else None)
                for p in ("B", "C")}
    off = {"B": None, "C": None} if second else pass_off(project)
    return {"mode": mode, "passes": {p: c.name for p, c in allb.items()}, "defaults": defaults, "pass_off": off,
            "notes": [] if second else unused_pass_notes(project, provider_b, provider_c),
            "shared": shared_passes({x: allb[x] for x in passes_run(project, second=second)}, second=second)}


def check_pass_tokens(project: Project, backends: dict) -> None:
    """``check_max_tokens`` for every distinct pass backend."""
    from ..core.protocol import check_max_tokens

    seen = set()
    for cfg in backends.values():
        if cfg.name not in seen:
            seen.add(cfg.name)
            check_max_tokens(project.config.protocol.screening.max_tokens, cfg)


def _set_runs(row: dict) -> dict[str, str]:
    """pass -> the run id a set recorded for it (a legacy run shared by several protocols is never resumed)."""
    out: dict[str, str] = {}
    for x in row.get("runs") or []:
        if isinstance(x, dict) and x.get("run_id"):
            if not x.get("shared_legacy"):   # the set's new records go into a run of its own (… __<key>.s1)
                out[x.get("pass") or ""] = x["run_id"]
    return out


def check_plan(project: Project, protocol: str, scope: str, settings: dict, new_set: bool = False,
               reuse_from: int | None = None, no_reuse: bool = False) -> dict:
    """``result_sets.plan_rerun`` plus the refusals (NewSetNeeded, ScopeNeeded), with no side effects; shared by the
    run and the preflight of the CLI and the routes."""
    from . import result_sets as rs

    plan = rs.plan_rerun(project, protocol, scope, settings, new_set=new_set, reuse_from=reuse_from, no_reuse=no_reuse)
    if plan["action"] == "refuse":
        raise rs.NewSetNeeded(plan)
    if plan["action"] == "continue" and plan["changed"]:
        row = rs.row_by_key(project, plan["key"])
        if _non_backend(row.get("settings")) != _non_backend(settings):
            plan = {**plan, "action": "refuse", "rule": "2", "message":
                    f"{protocol} set {plan['set']} is unfinished, and the settings changed ({', '.join(plan['changed'])}); "
                    "only a backend change may continue a set. Set them back, or add --new-set"}
            raise rs.NewSetNeeded(plan)
    return plan


def prepare_set(project: Project, protocol: str, scope: str, settings: dict, new_set: bool = False,
                set_label: str | None = None, reuse_from: int | None = None, no_reuse: bool = False,
                backend_change: str | None = None, author: str | None = None, source: tuple[str, int] | None = None,
                kind: str = "screening") -> tuple[dict, dict]:
    """Plan the protocol run (``result_sets.plan_rerun``) and, unless it is refused, create the set it writes.
    Returns ``(plan, set row)``. Raises NewSetNeeded (nothing ran, nothing written)."""
    from . import result_sets as rs

    plan = check_plan(project, protocol, scope, settings, new_set, reuse_from, no_reuse)
    if plan["action"] == "continue" and plan["changed"]:
        if backend_change == "new":   # the backend changed: the rest goes into a separate set
            plan = rs.plan_rerun(project, protocol, scope, settings, new_set=True, reuse_from=reuse_from,
                                 no_reuse=no_reuse)
    if plan["action"] == "create":
        row = rs.new_set(project, protocol, kind if kind != "screening" else "screening", settings, label=set_label,
                         scope=scope, source_protocol=source[0] if source else None,
                         source_set=source[1] if source else None, author=author)
        plan["reused"] = rs.copy_reused(project, plan, row["key"], rs._scope_ids(project, scope))
        row = rs.row_by_key(project, row["key"])
    else:
        row = rs.row_by_key(project, plan["key"])
        plan["reused"] = 0
        if plan["action"] == "continue" and not row.get("settings"):   # a migrated set: record what it continues with
            project.store.execute("UPDATE screen_result_sets SET settings=?, settings_hash=?, scope=COALESCE(scope, ?) "
                                  "WHERE key=?", (json.dumps(settings, sort_keys=True), plan["settings_hash"], scope, row["key"]))
            row = rs.row_by_key(project, plan["key"])
    return plan, row


async def run_protocol_async(project: Project, scope: str, protocol: str, provider_name: str | None = None,
                             prompt_version: int | None = None, limit: int | None = None, progress=None,
                             third_read: bool = True, new_set: bool = False, set_label: str | None = None,
                             reuse_from: int | None = None, no_reuse: bool = False, backend_change: str | None = None,
                             confirmed_via: str = "cli", reuse_evaluations: bool = False,
                             temperature: float | None = None, author: str | None = None,
                             provider_b: str | None = None, provider_c: str | None = None) -> dict:
    """Passes A and B (and the blind third read C) into one result set of ``protocol`` (design Decision 23): the
    first run creates set 1; an unfinished set continues; a finished one with equal settings is re-derived without a
    call; anything else is refused (NewSetNeeded) unless ``new_set``. Each pass runs on its own backend
    (``pass_backends``) at that backend's temperature; ``temperature``, when given, overrides every pass."""
    from . import result_sets as rs

    check_protocol_name(protocol)
    allb = pass_backends(project, provider_name, provider_b, provider_c)
    backends = {p: allb[p] for p in passes_run(project)}
    check_pass_tokens(project, backends)
    pcfg = backends["A"]
    prow, pdata = load_prompt(project, prompt_version)
    settings = rs.settings_for(project, pcfg, pdata, prow["version"], temperature,
                               passes={p: c for p, c in backends.items() if p != "A"})
    plan, row = prepare_set(project, protocol, scope, settings, new_set, set_label, reuse_from, no_reuse, backend_change,
                            author)
    key, n = row["key"], row["result_set"]
    recorded = _set_runs(row)
    skip = set(rs.carried_ids(project, key))
    if any(isinstance(x, dict) and x.get("shared_legacy") for x in row.get("runs") or []):
        skip |= set(rs._decided(project, key))   # decided by a frozen shared run: not screened again
    before = project.store.scalar("SELECT COALESCE(MAX(usage_id), 0) FROM llm_usage")  # the result shows this run's tokens only
    common = dict(prompt_version=prompt_version, progress=progress, protocol=protocol,
                  result_set=n, backend_change=None if backend_change == "new" else backend_change,
                  confirmed_via=confirmed_via, skip_ids=skip, reuse_evaluations=reuse_evaluations, author=author,
                  temperature=temperature)
    single = project.config.protocol.screening.passes == 1   # pass A decides alone: no pass B, no third read
    rederive = plan["action"] == "rederive"

    async def one_pass(name, sc, seed, lim):
        if rederive:
            return {"run_id": recorded[name]} if name in recorded else None
        return await screen_async(project, sc, pass_name=name, limit=lim, seed_offset=seed, run_id=recorded.get(name),
                                  provider_name=allb[name].name, **common)

    ra = await one_pass("A", scope, 0, limit)
    rb = None if single else await one_pass("B", scope, 1, limit)
    da = _decisions(project, ra["run_id"]) if ra else {}
    db = da if single else (_decisions(project, rb["run_id"]) if rb else {})
    groups = _label_groups(project, ra["run_id"]) if ra else []
    needed = project.config.protocol.screening.majority
    disagree = [rid for rid in da if rid in db and da[rid]["rule_decision"] != db[rid]["rule_decision"]]
    dc = {}
    rc = None
    if disagree and third_read and project.config.protocol.screening.blind_third_read:
        rc = await one_pass("C", "ids:" + ",".join(map(str, disagree)), 2, None)
        dc = _decisions(project, rc["run_id"]) if rc else {}
    counts = Counter()
    from ..core.questions import earlier_split_answer, raise_question
    researcher = {r["record_id"]: r["decision"] for r in project.store.query(
        "SELECT record_id, decision FROM screen_decisions WHERE protocol=? AND votes LIKE '%researcher:%'", (key,))}
    for rid in skip:   # decisions this set copied from another set: they count, they are not screened here
        d = project.store.scalar("SELECT decision FROM screen_decisions WHERE protocol=? AND record_id=?", (key, rid))
        if d:
            counts[d] += 1
    for rid, a in da.items():
        b = db.get(rid)
        if b is None:
            continue
        calls = [a] if single else [a, b] + ([dc[rid]] if rid in dc else [])
        votes = [c["rule_decision"] for c in calls]
        if single:
            final = votes[0]
        else:
            final = majority(votes, needed) if len(votes) == 3 else (votes[0] if votes[0] == votes[1] else "maybe")
        pa = json.loads(a["parsed"] or "{}")
        if rid in researcher:   # the researcher answered a question about this record: their decision stands
            counts[researcher[rid]] += 1
            continue
        now = project.store.one("SELECT decision FROM screen_decisions WHERE protocol=? AND record_id=? AND votes LIKE "
                                "'%researcher:%'", (key, rid))
        if now:   # decided on the Screening review page while this run was going (manual-review D3): it stands
            counts[now["decision"]] += 1
            continue
        if len(votes) == 3 and len(set(votes)) == 3:
            title = project.store.scalar("SELECT title FROM records WHERE record_id=?", (rid,))
            earlier = earlier_split_answer(project, key, rid)
            raise_question(project, "screen_split", f"{key}:{rid}", title or f"record {rid}",
                           f"The three reads of protocol {protocol}" + (f" (set {n})" if n > 1 else "") + " disagree "
                           f"completely ({', '.join(votes)}); the protocol records maybe. Decide it yourself, or leave "
                           "it as maybe." + (f" Your earlier answer in set {earlier['set']}: {earlier['choice']} (same "
                                             "codebook and rules); you can reuse it." if earlier else ""),
                           context={"protocol": key, "votes": votes, "cell": pa.get("cell"),
                                    "vote_calls": [c["call_id"] for c in calls],
                                    **({"earlier": earlier} if earlier else {})}, record_id=rid, run_ref=key,
                           extra_options=[{"value": "reuse", "label": f"Reuse this answer ({earlier['choice']}, set "
                                                                      f"{earlier['set']})"}] if earlier else None)
        set_decision(project, key, rid, final, votes=votes, cell=pa.get("cell"), labels=labels_from_parsed(pa, groups),
                     reason=pa.get("exclusion_reason"), vote_calls=[c["call_id"] for c in calls],
                     decided_by=rs.decided_by(votes, final))
        counts[final] += 1
    runs = [r for r in ((ra, "A"), (rb, "B"), (rc, "C")) if r[0]]
    rs.add_runs(project, key, [(r["run_id"], p) for r, p in runs])
    from .engine import run_mixed
    mixed = any(run_mixed(project, r["run_id"]) for r, _p in runs)
    if mixed:
        rs.mark_mixed(project, key)
    out = {"protocol": protocol, "result_set": n, "key": key, "set_action": plan["action"],
           "runs": [r["run_id"] for r, _p in runs], "n": sum(counts.values()), "disagreements": len(disagree),
           **dict(counts)}
    if plan.get("reused"):
        out["reused_decisions"] = plan["reused"]
    if plan.get("same_as"):
        out["same_as"] = plan["same_as"]
    if mixed:
        out["mixed"] = True
    _pass_info(out, backends, temperature, unused_pass_notes(project, provider_b, provider_c))
    for r, _p in runs:
        for k in ("backend_change", "experimental"):
            if r.get(k):
                out[k] = r[k]
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_screen
    from ..core.usage import usage_for_runs
    out["usage"] = usage_for_runs(project, out["runs"], after=before)
    record(project, summarize_screen(out, "protocol"))
    return out


def _pass_info(out: dict, backends: dict, temperature: float | None, notes: list[str], second: bool = False) -> None:
    """The run result's per-pass backends, shared-model sentences and unused-flag notes."""
    names = {"A": "S1", "B": "S2", "C": "S3"} if second else {"A": "A", "B": "B", "C": "C"}
    out["pass_backends"] = {names[p]: c.name for p, c in backends.items()}
    shared = shared_passes(backends, temperature, second)
    if shared:
        out["shared_models"] = shared
    if notes:
        out["notes"] = notes


def run_protocol(project: Project, scope: str, protocol: str, **kw) -> dict:
    import asyncio
    return asyncio.run(run_protocol_async(project, scope, protocol, **kw))


SECOND_SCREEN_OFF = "the second screen is switched off (protocol.screening.second_screen is off)"


def check_second_screen(project: Project) -> None:
    """Refuse a second screen (before any model call) while the protocol switches it off."""
    if not project.config.protocol.screening.second_screen:
        raise ValueError(SECOND_SCREEN_OFF)


def second_protocol_name(protocol: str, out_protocol: str | None = None) -> str:
    """The protocol a second screen writes; a bad name is quoted as typed, not as "<name>_second"."""
    check_protocol_name(protocol)
    if out_protocol is None and len(protocol) > 73:
        raise ValueError(f"the second screen is written as '<protocol>_second', so the protocol name may be at most 73 "
                         f"characters here (got {len(protocol)})")
    out_protocol = out_protocol or f"{protocol}_second"
    check_protocol_name(out_protocol)
    return out_protocol


async def second_screen_async(project: Project, protocol: str, out_protocol: str | None = None, provider_name: str | None = None,
                              prompt_version: int | None = None, limit: int | None = None, progress=None,
                              from_set: int | None = None, new_set: bool = False, set_label: str | None = None,
                              backend_change: str | None = None, confirmed_via: str = "cli",
                              reuse_evaluations: bool = False, temperature: float | None = None,
                              author: str | None = None, provider_b: str | None = None,
                              provider_c: str | None = None) -> dict:
    """Re-read every maybe of ``protocol``'s active set (or set ``from_set``) twice (plus a tie-break); write the result
    as a result set of ``out_protocol`` linked to its source set. S1 runs on pass A's backend, S2 on pass B's and S3 on
    the judge's (``pass_backends``)."""
    from . import result_sets as rs

    check_second_screen(project)
    out_protocol = second_protocol_name(protocol, out_protocol)
    src_key = rs.active_key(project, protocol, from_set)
    _p, src_n = rs.split_key(src_key)
    backends = pass_backends(project, provider_name, provider_b, provider_c)
    check_pass_tokens(project, backends)
    pcfg = backends["A"]
    prow, pdata = load_prompt(project, prompt_version)
    scope = f"maybe:{protocol}#{src_n}"
    settings = rs.settings_for(project, pcfg, pdata, prow["version"], temperature, kind="second_screen",
                               source=(protocol, src_n), passes={"S2": backends["B"], "S3": backends["C"]})
    plan, row = prepare_set(project, out_protocol, scope, settings, new_set, set_label, None, True, backend_change,
                            author, source=(protocol, src_n), kind="second")
    key, n = row["key"], row["result_set"]
    recorded = _set_runs(row)
    before = project.store.scalar("SELECT COALESCE(MAX(usage_id), 0) FROM llm_usage")  # the result shows this run's tokens only
    common = dict(prompt_version=prompt_version, progress=progress, kind="second_screen",
                  protocol=out_protocol, result_set=n, backend_change=None if backend_change == "new" else backend_change,
                  confirmed_via=confirmed_via, reuse_evaluations=reuse_evaluations, author=author,
                  temperature=temperature)
    rederive = plan["action"] == "rederive"

    async def one_pass(name, sc, seed, lim):
        if rederive:
            return {"run_id": recorded[name]} if name in recorded else None
        return await screen_async(project, sc, pass_name=name, limit=lim, seed_offset=seed, run_id=recorded.get(name),
                                  provider_name=backends[SECOND_PASS[name]].name, **common)

    r1 = await one_pass("S1", scope, 10, limit)
    r2 = await one_pass("S2", scope, 11, limit)
    d1 = _decisions(project, r1["run_id"]) if r1 else {}
    d2 = _decisions(project, r2["run_id"]) if r2 else {}
    groups = _label_groups(project, r1["run_id"]) if r1 else []
    needed = project.config.protocol.screening.majority
    disagree = [rid for rid in d1 if rid in d2 and d1[rid]["rule_decision"] != d2[rid]["rule_decision"]]
    d3 = {}
    r3 = None
    if disagree:
        r3 = await one_pass("S3", "ids:" + ",".join(map(str, disagree)), 12, None)
        d3 = _decisions(project, r3["run_id"]) if r3 else {}
    # carry every non-maybe decision of the source set, replace the maybes
    base = {r["record_id"]: dict(r) for r in project.store.query("SELECT * FROM screen_decisions WHERE protocol=?", (src_key,))}
    counts = Counter()
    for rid, row_ in base.items():
        now = project.store.one("SELECT decision FROM screen_decisions WHERE protocol=? AND record_id=? AND votes LIKE "
                                "'%researcher:%'", (key, rid))
        if now:   # decided on the Screening review page in this set (manual-review D3): a re-run leaves it as it is
            counts[now["decision"]] += 1
            continue
        if row_["decision"] != "maybe" or rid not in d1 or rid not in d2:
            set_decision(project, key, rid, row_["decision"], votes=json.loads(row_["votes"]) if row_["votes"] else None,
                         cell=row_["cell"], labels=json.loads(row_["labels"]) if row_["labels"] else None,
                         reason=row_["reason"],
                         vote_calls=json.loads(row_["vote_calls"]) if row_.get("vote_calls") else None,
                         decided_by=f"carried from {src_key}")
            counts[row_["decision"]] += 1
            continue
        calls = [d1[rid], d2[rid]] + ([d3[rid]] if rid in d3 else [])
        votes = [c["rule_decision"] for c in calls]
        final = majority(votes, needed)
        pa = json.loads(d1[rid]["parsed"] or "{}")
        set_decision(project, key, rid, final, votes=votes, cell=pa.get("cell"), labels=labels_from_parsed(pa, groups),
                     reason=pa.get("exclusion_reason"), vote_calls=[c["call_id"] for c in calls],
                     decided_by=rs.decided_by(votes, final, ("S1", "S2", "S3")))
        counts[final] += 1
    runs = [r for r in ((r1, "S1"), (r2, "S2"), (r3, "S3")) if r[0]]
    rs.add_runs(project, key, [(r["run_id"], p) for r, p in runs])
    from .engine import run_mixed
    mixed = any(run_mixed(project, r["run_id"]) for r, _p in runs)
    if mixed:
        rs.mark_mixed(project, key)
    out = {"protocol": out_protocol, "result_set": n, "key": key, "source": src_key, "set_action": plan["action"],
           "runs": [r["run_id"] for r, _p in runs], "maybes_reread": len(d1), "disagreements": len(disagree),
           **dict(counts)}
    if mixed:
        out["mixed"] = True
    _pass_info(out, backends, temperature, [], second=True)
    for r, _p in runs:
        for k in ("backend_change", "experimental"):
            if r.get(k):
                out[k] = r[k]
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_screen
    from ..core.usage import usage_for_runs
    out["usage"] = usage_for_runs(project, out["runs"], after=before)
    record(project, summarize_screen(out, "second"))
    return out


def second_screen(project: Project, protocol: str, **kw) -> dict:
    import asyncio
    return asyncio.run(second_screen_async(project, protocol, **kw))
