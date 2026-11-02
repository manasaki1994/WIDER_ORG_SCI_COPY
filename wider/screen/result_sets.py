"""Result sets of a screening protocol (design Decision 23): a rerun never replaces a finished protocol.

A protocol holds one or more result sets. Set 1 keeps the key ``<protocol>``; set n >= 2 stores its decisions in
``screen_decisions`` under ``<protocol>#<n>``. One set per protocol is *active* (the one recall, reports and exports
read); only the researcher switches it (:func:`use`). :func:`plan_rerun` is the preflight that decides, before any
call, whether a protocol run creates a set, continues one, re-derives a finished one or is refused (rules 1-4, 2a).

The *method identity* of a backend (:func:`method_identity`) is its snapshot minus the operational fields; it is the
one definition used by a set's ``settings_hash``, the evaluation key of a call and the backend-change check.
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..core.project import Project
from ..core.provenance import sha256_json, sha256_text, utcnow

# fields of ProviderConfig that never change what a call evaluates (recorded decision 25); every other field is method
OPERATIONAL_FIELDS = frozenset({"name", "price_input_per_mtok", "price_output_per_mtok", "price_cached_input_per_mtok",
                                "price_cache_write_per_mtok", "timeout_s", "max_concurrency", "api_key_env", "preset"})
METHOD_FIELDS = frozenset({"kind", "model", "base_url", "command", "temperature", "seed", "repairs", "extra"})
# keys a run snapshot adds to the config (derived, or operational by recorded decision 29: CLI version and path)
DERIVED_KEYS = frozenset({"locality", "locality_source", "destination", "experimental", "limitations", "executable",
                          "version", "label"})
ROLES = {"purposive": "development", "probability": "validation"}
DEVELOPMENT_NOTE = "development set: not an independent validation"
SOURCES = ("screening", "import", "second", "migrated", "adjudication")


class NewSetNeeded(ValueError):
    """A protocol run that would start a paid rerun without ``new_set`` (rules 2a and 3): nothing ran."""

    def __init__(self, plan: dict[str, Any]):
        super().__init__(plan["message"])
        self.plan = plan


class ScopeNeeded(ValueError):
    """A protocol with a set of unknown scope (migration): the researcher names it first."""

    def __init__(self, protocol: str, n: int):
        super().__init__(f"{protocol} set {n} has an unknown scope. Tell WIDER which records it screened: "
                         f"wider screen sets scope {protocol} {n} SCOPE")
        self.protocol, self.set = protocol, n


# ----------------------------------------------------------------------------- method identity
def _tilde_argv(argv, home: str):
    return [a.replace(home + "/", "~/") if a != home else "~" for a in argv] if argv else argv


def _method_extra(extra: Any, home: str) -> Any:
    from ..providers.base import identity_extra

    ex = identity_extra(extra or {}, home)
    if isinstance(ex, dict) and isinstance(ex.get("headers"), dict):
        # a header whose value is an {env:NAME} reference is a key: its name is method, the variable is not
        ex["headers"] = {k: ("{env}" if isinstance(v, str) and v.strip().startswith("{env:") else v)
                         for k, v in ex["headers"].items()}
    return ex


def method_identity(cfg: Any) -> dict[str, Any]:
    """The backend's identity minus the operational fields. ``cfg`` is a ``ProviderConfig``, its ``model_dump()`` or a
    run snapshot (``Provider.identity()``)."""
    d = cfg.model_dump() if hasattr(cfg, "model_dump") else dict(cfg or {})
    home = os.path.expanduser("~")
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in OPERATIONAL_FIELDS or k in DERIVED_KEYS:
            continue
        if k == "command":
            v = _tilde_argv(v, home)
        elif k == "extra":
            v = _method_extra(v, home)
        elif k == "system_channel":
            continue
        out[k] = v
    if d.get("kind") == "cli_agent":
        out["system_channel"] = d.get("system_channel") or (d.get("extra") or {}).get("system_channel", "file")
    # a snapshot recorded before a field existed equals one where the field is empty (extra None and {} alike)
    return {k: v for k, v in out.items() if v is not None and v != {}}


def method_hash(cfg: Any) -> str:
    return sha256_json(method_identity(cfg))


def _with_passes(old: dict, new: dict) -> tuple[dict | None, dict | None]:
    """The two settings' ``backends``, a pass missing on one side filled with that side's pass-A backend (design D11):
    an older set meeting a new pass-B backend reads ``backends.B.model``."""
    a, b = old.get("backends"), new.get("backends")
    if a is None and b is None:
        return None, None
    a, b = dict(a or {}), dict(b or {})
    for k in set(a) | set(b):
        a.setdefault(k, old.get("backend"))
        b.setdefault(k, new.get("backend"))
    return a, b


def changed_fields(old: dict | None, new: dict | None, prefix: str = "") -> list[str]:
    """The keys whose values differ, ``extra`` and other nested keys as ``extra.body``, a per-pass backend as
    ``backends.B.model``."""
    old, new = old or {}, new or {}
    out: list[str] = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if k == "backends" and not prefix:
            a, b = _with_passes(old, new)
        if a == b:
            continue
        if isinstance(a, dict) and isinstance(b, dict) and (k in ("extra", "backend", "rules", "backends")
                                                            or prefix == "backends."):
            out += changed_fields(a, b, f"{prefix}{k}.")
        elif k == "backend" and isinstance(a, dict) != isinstance(b, dict):
            out.append(f"{prefix}{k}")
        else:
            out.append(f"{prefix}{k}")
    return [f.removeprefix("backend.") for f in out]


# ----------------------------------------------------------------------------- keys
def set_key(protocol: str, n: int) -> str:
    return protocol if int(n) == 1 else f"{protocol}#{int(n)}"


def split_key(key: str) -> tuple[str, int]:
    if "#" in key:
        p, n = key.rsplit("#", 1)
        if n.isdigit():
            return p, int(n)
    return key, 1


def _row(r) -> dict[str, Any]:
    d = dict(r)
    for k in ("settings", "runs", "derived_from"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    d["runs"] = d.get("runs") or []
    return d


def set_row(project: Project, protocol: str, n: int) -> dict[str, Any] | None:
    r = project.store.one("SELECT * FROM screen_result_sets WHERE protocol=? AND result_set=?", (protocol, int(n)))
    return _row(r) if r else None


def row_by_key(project: Project, key: str) -> dict[str, Any] | None:
    r = project.store.one("SELECT * FROM screen_result_sets WHERE key=?", (key,))
    return _row(r) if r else None


def run_ids(row: dict[str, Any]) -> list[str]:
    return [x["run_id"] if isinstance(x, dict) else x for x in row.get("runs") or []]


def _models(project: Project, row: dict[str, Any]) -> list[str]:
    ids = run_ids(row)
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    ms = {r["m"] for r in project.store.query(
        f"SELECT COALESCE(c.model, r.model) m FROM screen_calls c JOIN runs r ON r.run_id=c.run_id "
        f"WHERE c.run_id IN ({q}) AND c.ok=1 AND COALESCE(c.provider,'')<>'researcher'", ids) if r["m"]}
    return sorted(ms)


def sets(project: Project, protocol: str) -> list[dict[str, Any]]:
    """Every set of ``protocol`` with its counts, models, run ids and whether it is finished. The counts leave out
    the records of an incomplete development sample (ui-redesign D4); ``hidden`` then says how many are left out."""
    from .devgate import HIDDEN, hidden_ids

    hid = sorted(hidden_ids(project))
    q = ",".join("?" * len(hid)) or "NULL"
    out = []
    for r in project.store.query("SELECT * FROM screen_result_sets WHERE protocol=? ORDER BY result_set", (protocol,)):
        d = _row(r)
        c = project.store.one("SELECT COUNT(*) n, SUM(decision='include') include, SUM(decision='maybe') maybe, "
                              "SUM(decision='exclude') exclude, MAX(decided_at) last FROM screen_decisions WHERE protocol=?"
                              + (f" AND record_id NOT IN ({q})" if hid else ""), (d["key"], *hid))
        k = project.store.scalar(f"SELECT COUNT(*) FROM screen_decisions WHERE protocol=? AND record_id IN ({q})",
                                 (d["key"], *hid)) if hid else 0
        d["hidden"] = HIDDEN.format(n=k) if k else None
        d.update({"n": c["n"] or 0, "include": c["include"] or 0, "maybe": c["maybe"] or 0, "exclude": c["exclude"] or 0,
                  "last_decided_at": c["last"], "models": _models(project, d), "run_ids": run_ids(d),
                  "shared_legacy": [x["run_id"] for x in d["runs"] if isinstance(x, dict) and x.get("shared_legacy")],
                  "prompt_version": (d.get("settings") or {}).get("prompt_version") if isinstance(d.get("settings"), dict) else None,
                  "finished": finished(project, d)})
        d["role_note"] = DEVELOPMENT_NOTE if d.get("role") == "development" else None
        out.append(d)
    return out


def finished(project: Project, row: dict[str, Any]) -> bool | None:
    """Every record of the set's scope has a decision in it (None when the scope is unknown or mixed)."""
    from .engine import record_set

    scope = row.get("scope")
    if not scope or scope in ("unknown", "mixed"):
        return None
    try:
        ids = {r["record_id"] for r in record_set(project, scope)}
    except (ValueError, KeyError):
        return None
    have = {r["record_id"] for r in project.store.query("SELECT record_id FROM screen_decisions WHERE protocol=?",
                                                         (row["key"],))}
    return ids <= have


def out_of_step(project: Project, row: dict[str, Any]) -> str | None:
    """The refusal of a second-screen set whose source set is no longer its source protocol's active set."""
    sp, sn = row.get("source_protocol"), row.get("source_set")
    if not sp or not sn:
        return None
    act = project.store.one("SELECT result_set FROM screen_result_sets WHERE protocol=? AND active=1", (sp,))
    if act is None or act["result_set"] == sn:
        return None
    return (f"{row['protocol']} set {row['result_set']} is out of step: it was built from {sp} set {sn}; {sp} uses set "
            f"{act['result_set']}. Use {sp} set {sn} first (wider screen sets use {sp} {sn}), use both together (wider "
            f"screen sets use {row['protocol']} {row['result_set']} --with-source), or screen the maybes of set "
            f"{act['result_set']} again (wider screen second {sp} --new-set)")


def active_key(project: Project, protocol: str, set: int | None = None, check_step: bool = False) -> str:
    """The ``screen_decisions`` key a reader uses: the active set of ``protocol``, or set ``set`` when given (KeyError
    "no result set" when it does not exist). A protocol without set rows reads its name (nothing, or legacy rows).
    ``check_step`` refuses an out-of-step second-screen set (recall, report, export)."""
    st = project.store
    if set is not None:
        r = set_row(project, protocol, int(set))
        if r is None:
            raise KeyError(f"no result set {set} of {protocol!r}")
    else:
        r = st.one("SELECT * FROM screen_result_sets WHERE protocol=? AND active=1", (protocol,))
        r = _row(r) if r else None
        if r is None:
            return protocol
    if check_step:
        msg = out_of_step(project, r)
        if msg:
            raise ValueError(msg)
    return r["key"]


def reader(project: Project, protocol: str, set: int | None = None) -> dict[str, Any]:
    """What a reader (recall, report, export, package) records about the set it reads: ``{protocol, key, result_set,
    label, role, role_note, mixed}``. Unknown set: KeyError "no result set"; out-of-step second screen: ValueError."""
    key = active_key(project, protocol, set, check_step=True)
    r = row_by_key(project, key) or {}
    return {"protocol": protocol, "key": key, "result_set": r.get("result_set", 1), "label": r.get("label"),
            "role": r.get("role"), "role_note": DEVELOPMENT_NOTE if r.get("role") == "development" else None,
            "mixed": bool(r.get("mixed"))}


def active_set(project: Project, protocol: str) -> dict[str, Any] | None:
    r = project.store.one("SELECT * FROM screen_result_sets WHERE protocol=? AND active=1", (protocol,))
    return _row(r) if r else None


def role_for(project: Project, scope: str | None) -> str:
    if not scope:
        return "other"
    if scope == "corpus":
        return "corpus"
    if scope.startswith("sample:"):
        k = project.store.scalar("SELECT kind FROM samples WHERE name=?", (scope.split(":", 1)[1],))
        return ROLES.get(k or "", "other")
    return "other"


def ensure_set(project: Project, key: str, source: str = "screening") -> dict[str, Any]:
    """The set row of ``key``, creating set 1 of a protocol that has none (decisions written outside a protocol run,
    e.g. by a test or an older caller). A ``P#n`` key without its row is an error."""
    r = row_by_key(project, key)
    if r is not None:
        return r
    protocol, n = split_key(key)
    if n != 1 or project.store.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol=?", (protocol,)):
        raise KeyError(f"no result set {n} of {protocol!r}")
    return new_set(project, protocol, source)


def next_number(project: Project, protocol: str) -> int:
    """The number the protocol's next set gets: above every set it has and every set it had before an undo removed
    it (partial-adjudication-import D7), so a number a recall result or export named never names another set."""
    st = project.store
    n = int(st.scalar("SELECT COALESCE(MAX(result_set), 0) FROM screen_result_sets WHERE protocol=?", (protocol,)))
    return max(n, int(st.get_meta(f"set_high:{protocol}") or 0)) + 1


def new_set(project: Project, protocol: str, source: str, settings: dict | None = None, label: str | None = None,
            scope: str | None = None, source_protocol: str | None = None, source_set: int | None = None,
            derived_from: dict | None = None, import_file: str | None = None, import_sha256: str | None = None,
            author: str | None = None) -> dict[str, Any]:
    """Add the next set of ``protocol``; it is active only when it is the protocol's first."""
    from ..core import researchers

    st = project.store
    n = next_number(project, protocol)
    st.set_meta(f"set_high:{protocol}", str(n))   # never lowered by an undo: a set number is never used twice
    st.execute("INSERT INTO screen_result_sets(protocol,result_set,key,label,source,created_at,created_by,settings,"
               "settings_hash,runs,import_file,import_sha256,source_protocol,source_set,scope,role,derived_from,mixed,active) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
               (protocol, n, set_key(protocol, n), label, source, utcnow(), researchers.sign(project, author),
                json.dumps(settings, sort_keys=True) if settings is not None else None,
                sha256_json(settings) if settings is not None else None, json.dumps([]), import_file, import_sha256,
                source_protocol, source_set, scope, role_for(project, scope) if scope else None,
                json.dumps(derived_from) if derived_from else None, int(n == 1)))
    return set_row(project, protocol, n)


def add_runs(project: Project, key: str, runs: list[tuple[str, str]]) -> None:
    """Append ``(run_id, pass)`` pairs to the set's run list (each run once)."""
    r = row_by_key(project, key)
    if r is None:
        return
    have = run_ids(r)
    lst = list(r["runs"])
    for rid, p in runs:
        if rid and rid not in have:
            lst.append({"run_id": rid, "pass": p})
            have.append(rid)
    project.store.execute("UPDATE screen_result_sets SET runs=? WHERE key=?", (json.dumps(lst), key))


def mark_mixed(project: Project, key: str) -> None:
    project.store.execute("UPDATE screen_result_sets SET mixed=1 WHERE key=?", (key,))


# ----------------------------------------------------------------------------- settings
def settings_for(project: Project, cfg, pdata: dict, prompt_version: int, temperature: float | None = None,
                 kind: str = "screen", source: tuple[str, int] | None = None,
                 passes: dict | None = None) -> dict[str, Any]:
    """A set's methodological settings (recorded decision 25): method identity with the effective temperature, the
    prompt version, the codebook hash, the screening rules and, for a second screen, its source set. ``passes``
    (pass -> backend, pass A's excluded) adds ``backends`` holding only the passes whose identity or temperature
    differ from pass A's, so a set with every pass on A keeps the settings it had (design D11)."""
    sc = project.config.protocol.screening

    def ident(c):
        d = method_identity(c)
        d["temperature"] = temperature if temperature is not None else c.temperature
        return d

    backend = ident(cfg)
    out = {"backend": backend, "kind": kind, "prompt_version": prompt_version,
           "codebook": sha256_text(pdata["prompt"] + "\n" + json.dumps(pdata["schema"], sort_keys=True)),
           "rules": {"passes": sc.passes, "majority": sc.majority, "blind_third_read": sc.blind_third_read,
                     "recall_first": sc.recall_first, "maybe_flags": list(sc.maybe_flags),
                     "quote_min_chars": sc.quote_min_chars, "thin_words": project.config.protocol.corpus.thin_words}}
    if source:
        out["source_set"] = f"{source[0]}#{source[1]}"
    own = {p: ident(c) for p, c in (passes or {}).items() if c is not None}
    own = {p: d for p, d in own.items() if d != backend}
    if own:
        out["backends"] = own
    return out


def pass_settings(settings: dict | None, pass_name: str) -> dict | None:
    """The backend identity a set's settings give one pass (``A``/``S1``: ``backend``; others: ``backends``, else
    ``backend``). None when the set recorded no settings."""
    if not settings:
        return None
    return (settings.get("backends") or {}).get(pass_name) or settings.get("backend")


def settings_changed(old: dict | None, new: dict) -> list[str]:
    if not old:
        return ["settings not recorded"]
    return changed_fields(old, new)


# ----------------------------------------------------------------------------- rerun plan (rules 1-4, 2a)
def _scope_ids(project: Project, scope: str) -> list[int]:
    from .engine import record_set

    return [r["record_id"] for r in record_set(project, scope)]


def _decided(project: Project, key: str) -> dict[int, dict]:
    return {r["record_id"]: dict(r) for r in project.store.query("SELECT * FROM screen_decisions WHERE protocol=?", (key,))}


def reusable(project: Project, protocol: str, settings_hash: str | None, scope_ids: list[int],
             reuse_from: int | None = None) -> dict[str, Any] | None:
    """The set whose decisions a derived set may copy: an import set, or a set with equal method settings (the one
    with the most decisions in scope, or ``reuse_from``). None when there is none."""
    best = None
    want = set(scope_ids)
    for s in project.store.query("SELECT * FROM screen_result_sets WHERE protocol=? ORDER BY result_set", (protocol,)):
        s = _row(s)
        if s["source"] == "adjudication":   # a researcher's decisions, never copied as model calls (D10)
            continue
        if reuse_from is not None and s["result_set"] != reuse_from:
            continue
        if s["source"] != "import" and (not settings_hash or s.get("settings_hash") != settings_hash or s.get("mixed")):
            continue
        n = len(want & set(_decided(project, s["key"])))
        if n and (best is None or n > best["count"]):
            best = {"set": s["result_set"], "key": s["key"], "count": n, "source": s["source"], "role": s.get("role")}
    return best


def plan_rerun(project: Project, protocol: str, scope: str, settings: dict, new_set: bool = False,
               reuse_from: int | None = None, no_reuse: bool = False) -> dict[str, Any]:
    """What a protocol run would do, with no side effects. ``action``: ``create`` (a new set ``n``), ``continue``
    (an unfinished set ``key``), ``rederive`` (a finished set with equal settings: no call) or ``refuse`` (rules 2a and
    3: ``message``, ``changed``, ``reusable``, ``to_screen``). Raises ScopeNeeded for a set of unknown scope and
    ValueError for a refused ``reuse_from``."""
    st = project.store
    rows = [_row(r) for r in st.query("SELECT * FROM screen_result_sets WHERE protocol=? ORDER BY result_set", (protocol,))]
    for r in rows:
        if r.get("scope") == "unknown":
            raise ScopeNeeded(protocol, r["result_set"])
    h = sha256_json(settings)
    ids = _scope_ids(project, scope)
    nxt = next_number(project, protocol)
    base = {"protocol": protocol, "scope": scope, "in_scope": len(ids), "settings_hash": h}
    if not rows:   # rule 1
        return {**base, "action": "create", "set": 1, "to_screen": len(ids), "reuse": None}
    if new_set:    # rule 4
        same = next((r for r in reversed(rows) if r.get("settings_hash") == h and not r.get("mixed")
                     and r.get("scope") == scope and finished(project, r)), None)
        reuse = None
        if reuse_from is not None and not no_reuse:
            src = next((r for r in rows if r["result_set"] == reuse_from), None)
            if src is None:
                raise KeyError(f"no result set {reuse_from} of {protocol!r}")
            if src["source"] == "adjudication":
                was = (src.get("derived_from") or {}).get("set")
                raise ValueError(f"set {reuse_from} of {protocol} holds an adjudicator's decisions, not model calls; "
                                 + (f"reuse set {was}, the set it was built from" if was else "reuse another set"))
            if src["source"] != "import" and src.get("settings_hash") != h:
                ch = ", ".join(settings_changed(src.get("settings"), settings)) or "settings"
                raise ValueError(f"{protocol} set {reuse_from} cannot be reused: the method changed ({ch}); earlier "
                                 "model decisions are not reused when the method changed. Use --no-reuse to screen "
                                 "everything")
            have = set(_decided(project, src["key"]))
            n = len(set(ids) & have)
            reuse = {"set": reuse_from, "key": src["key"], "count": n, "source": src["source"], "role": src.get("role")}
        return {**base, "action": "create", "set": nxt, "same_as": same["result_set"] if same else None,
                "reuse": reuse, "to_screen": len(ids) - (reuse["count"] if reuse else 0)}
    of_scope = [r for r in rows if r.get("scope") == scope]
    newest = of_scope[-1] if of_scope else None
    rus = None if no_reuse else reusable(project, protocol, h, ids)
    refusal = {**base, "action": "refuse", "set": nxt, "reusable": rus,
               "to_screen": len(ids) - (rus["count"] if rus else 0), "changed": []}
    if newest is None or newest["source"] == "import":   # rule 2a
        if newest is None:
            others = "; ".join(f"set {r['result_set']} screens {r.get('scope') or 'an unknown scope'}"
                               + (f" ({r['role']})" if r.get("role") else "") for r in rows)
            msg = f"{protocol} has no set for {scope}; {others}."
        else:
            missing = len(set(ids) - set(_decided(project, newest["key"])))
            msg = (f"{protocol} set {newest['result_set']} is an import"
                   + (f" ({missing} of {len(ids)} records have no decision)" if missing else "") + "; an import set is "
                   "never continued by a model.")
        return {**refusal, "rule": "2a", "message": msg + " " + _choices(protocol, rus)}
    if not finished(project, newest):   # rule 2
        return {**base, "action": "continue", "set": newest["result_set"], "key": newest["key"],
                # a set migrated from before result sets recorded no settings: nothing to compare, it continues
                "changed": settings_changed(newest.get("settings"), settings)
                if newest.get("settings") and newest.get("settings_hash") != h else []}
    if newest.get("settings_hash") == h:   # rule 3, equal settings: re-derive, no call
        return {**base, "action": "rederive", "set": newest["result_set"], "key": newest["key"], "changed": []}
    ch = settings_changed(newest.get("settings"), settings)
    models = ", ".join(_models(project, newest)) or "its model"
    same = next((r for r in reversed(of_scope) if r.get("settings_hash") == h and not r.get("mixed")
                 and finished(project, r)), None)
    msg = (f"{protocol} set {newest['result_set']} is finished under {models}; your settings differ in: {', '.join(ch)}. "
           + (f"Your settings equal set {same['result_set']}: use it with wider screen sets use {protocol} "
              f"{same['result_set']}, or " if same else "To screen again as a new result set, ")
           + "add --new-set")
    return {**refusal, "rule": "3", "changed": ch, "finished_set": newest["result_set"],
            "same_as": same["result_set"] if same else None,
            "message": msg + (f" (or --new-set --reuse-from {rus['set']} to reuse {rus['count']} decisions)" if rus else "")}


def _choices(protocol: str, rus: dict | None) -> str:
    a = f"wider screen protocol … {protocol} --new-set --reuse-from {rus['set']}" if rus else None
    b = f"wider screen protocol … {protocol} --new-set --no-reuse"
    return ("Choose " + (f"{a} (reuse {rus['count']} decisions) or " if a else "") + f"{b} (screen everything).")


def copy_reused(project: Project, plan: dict, key: str, scope_ids: list[int]) -> int:
    """Copy the reusable set's final decisions of the scope into the new set ``key`` with their origin."""
    reuse = plan.get("reuse")
    if not reuse:
        return 0
    src = row_by_key(project, reuse["key"])
    kind = "import" if src["source"] == "import" else "reused"
    want = set(scope_ids)
    n = 0
    for d in _decided(project, reuse["key"]).values():
        if d["record_id"] not in want:
            continue
        origin = {"kind": kind, "set": src["result_set"], "decided_by": d.get("decided_by"),
                  "decided_at": d.get("decided_at"), "run_id": None, "model": None}
        project.store.execute(
            "INSERT OR IGNORE INTO screen_decisions(protocol,record_id,decision,votes,cell,labels,reason,decided_at,"
            "vote_calls,decided_by,origin) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (key, d["record_id"], d["decision"], d["votes"], d["cell"], d["labels"], d["reason"], utcnow(),
             d.get("vote_calls"), d.get("decided_by"), json.dumps(origin)))
        n += 1
    project.store.execute("UPDATE screen_result_sets SET derived_from=? WHERE key=?",
                          (json.dumps({"set": src["result_set"], "kind": kind, "count": n}), key))
    return n


def carried_ids(project: Project, key: str) -> set[int]:
    """Records whose decision in ``key`` came from another set (reused or imported): never screened in it."""
    return {r["record_id"] for r in project.store.query(
        "SELECT record_id, origin FROM screen_decisions WHERE protocol=? AND origin IS NOT NULL", (key,))
            if json.loads(r["origin"]).get("kind") in ("reused", "import")}


# ----------------------------------------------------------------------------- researcher choices
def use(project: Project, protocol: str, n: int, with_source: bool = False, author: str | None = None) -> dict[str, Any]:
    """Make set ``n`` the active set (journalled with undo; never starts a run)."""
    from ..core.journal import recording

    r = set_row(project, protocol, n)
    if r is None:
        raise KeyError(f"no result set {n} of {protocol!r}")
    switch_source = None
    if r.get("source_protocol") and r.get("source_set"):
        sp, sn = r["source_protocol"], r["source_set"]
        act = active_set(project, sp)
        if act is None or act["result_set"] != sn:
            if not with_source:
                raise ValueError(f"set {n} was built from {sp} set {sn}; {sp} uses set "
                                 f"{act['result_set'] if act else '?'}. Use {sp} set {sn} first, or add --with-source "
                                 "to switch both")
            switch_source = (sp, sn)
    summary = f"result set: {protocol} uses set {n}" + (f" (and {switch_source[0]} set {switch_source[1]})"
                                                         if switch_source else "")
    with recording(project, "result_set", summary, author=author) as rec:
        rec.rows("screen_result_sets", "protocol=?", (protocol,))
        project.store.execute("UPDATE screen_result_sets SET active=(result_set=?) WHERE protocol=?", (int(n), protocol))
        if switch_source:
            rec.rows("screen_result_sets", "protocol=?", (switch_source[0],))
            project.store.execute("UPDATE screen_result_sets SET active=(result_set=?) WHERE protocol=?",
                                  (switch_source[1], switch_source[0]))
    return set_row(project, protocol, n)


def label(project: Project, protocol: str, n: int, text: str, author: str | None = None) -> dict[str, Any]:
    from ..core.journal import recording

    if set_row(project, protocol, n) is None:
        raise KeyError(f"no result set {n} of {protocol!r}")
    text = (text or "").strip()[:120]
    with recording(project, "result_set", f"result set: {protocol} set {n} labelled {text!r}", author=author) as rec:
        rec.rows("screen_result_sets", "protocol=? AND result_set=?", (protocol, int(n)))
        project.store.execute("UPDATE screen_result_sets SET label=? WHERE protocol=? AND result_set=?",
                              (text or None, protocol, int(n)))
    return set_row(project, protocol, n)


def set_scope(project: Project, protocol: str, n: int, scope: str, author: str | None = None) -> dict[str, Any]:
    """Name the scope of a set whose scope is unknown (after checking every decided record lies in it)."""
    from ..core.journal import recording

    r = set_row(project, protocol, n)
    if r is None:
        raise KeyError(f"no result set {n} of {protocol!r}")
    if r.get("scope") not in (None, "unknown"):
        raise ValueError(f"{protocol} set {n} already has the scope {r['scope']}")
    ids = set(_scope_ids(project, scope))
    outside = [rid for rid in _decided(project, r["key"]) if rid not in ids]
    if outside:
        raise ValueError(f"{len(outside)} records with a decision in {protocol} set {n} are not in {scope}; "
                         "choose the scope the set screened")
    with recording(project, "result_set", f"result set: {protocol} set {n} scope set to {scope}", author=author) as rec:
        rec.rows("screen_result_sets", "protocol=? AND result_set=?", (protocol, int(n)))
        project.store.execute("UPDATE screen_result_sets SET scope=?, role=? WHERE protocol=? AND result_set=?",
                              (scope, role_for(project, scope), protocol, int(n)))
    return set_row(project, protocol, n)


def _model_final(d: dict) -> str:
    """The decision of the model votes alone (researcher answers ignored): a split stays maybe."""
    from .protocol import majority

    votes = [v for v in (json.loads(d["votes"]) if d.get("votes") else []) if not str(v).startswith("researcher:")]
    if not votes or not any(str(v).startswith("researcher:") for v in json.loads(d["votes"] or "[]")):
        return d["decision"]
    if len(votes) == 1:
        return votes[0]
    if len(votes) == 2:
        return votes[0] if votes[0] == votes[1] else "maybe"
    return majority(votes)


def compare(project: Project, protocol: str, a: int, b: int, models_only: bool = False) -> dict[str, Any]:
    """A 3x3 include/maybe/exclude table between sets ``a`` and ``b`` over the records both decided, the agreement
    share and the records that differ (id and title only). Never changes the active set."""
    from .devgate import LEFT_OUT_LIST, hidden_ids

    ka, kb = active_key(project, protocol, a), active_key(project, protocol, b)
    da, db = _decided(project, ka), _decided(project, kb)
    hid = hidden_ids(project) & (set(da) | set(db))   # left out of the table and the list (D4)
    da, db = ({k: v for k, v in d.items() if k not in hid} for d in (da, db))
    order = ("include", "maybe", "exclude")
    table = {x: {y: 0 for y in order} for x in order}
    differ = []
    both = sorted(set(da) & set(db))
    for rid in both:
        x = _model_final(da[rid]) if models_only else da[rid]["decision"]
        y = _model_final(db[rid]) if models_only else db[rid]["decision"]
        table[x][y] += 1
        if x != y:
            differ.append({"record_id": rid, "a": x, "b": y})
    titles = {r["record_id"]: r["title"] for r in project.store.query(
        f"SELECT record_id, title FROM records WHERE record_id IN ({','.join('?' * len(differ))})",
        [d["record_id"] for d in differ])} if differ else {}
    for d in differ:
        d["title"] = titles.get(d["record_id"])
    agree = sum(table[x][x] for x in order)
    return {"protocol": protocol, "a": a, "b": b, "keys": [ka, kb], "models_only": models_only, "n": len(both),
            "agree": agree, "agreement": round(agree / len(both), 4) if both else None, "table": table,
            "only_a": len(set(da) - set(db)), "only_b": len(set(db) - set(da)), "differ": differ,
            "hidden": LEFT_OUT_LIST if hid else None}


# ----------------------------------------------------------------------------- migration of existing protocols
MIGRATED_META = "result_sets_migrated"
NOTES_META = "result_sets_migration_notes"


def migration_notes(project: Project) -> list[str]:
    raw = project.store.get_meta(NOTES_META)
    return json.loads(raw) if raw else []


def _free_name(project: Project, name: str) -> str:
    base = name.replace("#", "-h")
    cand, i = base, 2
    taken = {r[0] for r in project.store.query("SELECT DISTINCT protocol FROM screen_result_sets")} | \
            {r[0] for r in project.store.query("SELECT DISTINCT protocol FROM screen_decisions")}
    while cand in taken:
        cand, i = f"{base}{i}", i + 1
    return cand


def _rename_protocol(project: Project, old: str, new: str) -> None:
    st = project.store
    st.execute("UPDATE screen_decisions SET protocol=? WHERE protocol=?", (new, old))
    st.execute("UPDATE screen_result_sets SET protocol=?, key=? WHERE protocol=? AND result_set=1", (new, new, old))
    for r in st.query("SELECT result_id, result FROM stage_results WHERE stage='screen'"):
        try:
            d = json.loads(r["result"])
        except ValueError:
            continue
        head = d.get("headline") or ""
        for pre in ("Protocol ", "Second screen of "):
            if head.startswith(f"{pre}{old}:"):
                d["headline"] = f"{pre}{new}:" + head[len(f"{pre}{old}:"):]
                st.execute("UPDATE stage_results SET result=? WHERE result_id=?", (json.dumps(d), r["result_id"]))
    from ..core.stage_result import META_PREFIX
    for k in (META_PREFIX + "screen", "last_report"):
        raw = st.get_meta(k)
        if not raw:
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        changed = False
        if d.get("protocol") == old:
            d["protocol"], changed = new, True
        head = d.get("headline") or ""
        for pre in ("Protocol ", "Second screen of "):
            if head.startswith(f"{pre}{old}:"):
                d["headline"], changed = f"{pre}{new}:" + head[len(f"{pre}{old}:"):], True
        if changed:
            st.set_meta(k, json.dumps(d))
    for q in st.query("SELECT question_id, dedupe_key, ref, run_ref, context FROM questions"):
        ctx = json.loads(q["context"]) if q["context"] else {}
        new_ctx = dict(ctx)
        if ctx.get("protocol") == old:
            new_ctx["protocol"] = new
        dk = q["dedupe_key"] or ""
        if dk.startswith(f"screen_split:{old}:"):
            dk = f"screen_split:{new}:" + dk[len(f"screen_split:{old}:"):]
        st.execute("UPDATE questions SET dedupe_key=?, ref=?, run_ref=?, context=? WHERE question_id=?",
                   (dk, new if q["ref"] == old else q["ref"], new if q["run_ref"] == old else q["run_ref"],
                    json.dumps(new_ctx) if q["context"] else q["context"], q["question_id"]))


def _linked_runs(project: Project) -> dict[str, list[list[str]]]:
    """protocol -> the run-id lists of its protocol and second-screen results (one list per recorded run)."""
    out: dict[str, list[list[str]]] = {}
    for r in project.store.query("SELECT result FROM stage_results WHERE stage='screen' AND action IN ('protocol','second') "
                                 "ORDER BY result_id"):
        try:
            d = json.loads(r["result"])
        except ValueError:
            continue
        head = d.get("headline") or ""
        name = None
        for pre in ("Protocol ", "Second screen of "):
            if head.startswith(pre) and ":" in head:
                name = head[len(pre):].split(":", 1)[0]
                if pre == "Second screen of ":
                    name = f"{name}_second" if not name.endswith("_second") else name
                break
        if not name:
            continue
        ids = [x.get("run") for x in d.get("rows") or [] if isinstance(x, dict) and x.get("run")]
        if ids:
            out.setdefault(name, []).append(ids)
    return out


def _run_scope(project: Project, run_id: str) -> str | None:
    run = project.store.one("SELECT dir FROM runs WHERE run_id=?", (run_id,))
    if run:
        try:
            spec = json.loads((project.root / run["dir"] / "run_spec.json").read_text(encoding="utf-8"))
            sc = (spec.get("settings") or {}).get("scope")
            if sc:
                return sc
        except (OSError, ValueError):
            pass
    parts = run_id.split("__")
    if len(parts) < 2:
        return None
    safe = parts[1]
    cands = []
    if safe == "corpus":
        cands.append("corpus")
    for s in project.store.query("SELECT name FROM samples"):
        if safe == f"sample-{s['name']}"[:40]:
            cands.append(f"sample:{s['name']}")
    for p in project.store.query("SELECT DISTINCT protocol FROM screen_result_sets"):
        if safe == f"maybe-{p['protocol']}"[:40]:
            cands.append(f"maybe:{p['protocol']}")
    return cands[0] if len(cands) == 1 else None


def _pass_of(project: Project, run_id: str) -> str:
    p = project.store.scalar("SELECT pass FROM screen_calls WHERE run_id=? LIMIT 1", (run_id,))
    return p or run_id.rsplit("__", 1)[-1]


def _rederive(project: Project, key: str, runs: list[tuple[str, str]], origin_run: str) -> int:
    """Final decisions of a migrated set from the stored calls of its runs (no model call)."""
    from .decisions import labels_from_parsed, set_decision
    from .engine import run_calls
    from .protocol import majority

    by_pass: dict[str, dict[int, dict]] = {}
    for rid, p in runs:
        for c in run_calls(project, rid):
            if c["ok"]:
                by_pass.setdefault(p, {})[c["record_id"]] = c
    first, second, third = ("S1", "S2", "S3") if "S1" in by_pass else ("A", "B", "C")
    da, db, dc = by_pass.get(first, {}), by_pass.get(second), by_pass.get(third, {})
    needed = project.config.protocol.screening.majority
    n = 0
    for rid, a in da.items():
        if db is not None and rid not in db:
            continue
        cs = [a] + ([db[rid]] if db is not None else []) + ([dc[rid]] if rid in dc else [])
        votes = [c["rule_decision"] for c in cs]
        final = votes[0] if len(votes) == 1 else (majority(votes, needed) if len(votes) == 3 else
                                                  (votes[0] if votes[0] == votes[1] else "maybe"))
        pa = json.loads(a["parsed"] or "{}")
        set_decision(project, key, rid, final, votes=votes, cell=pa.get("cell"), labels=labels_from_parsed(pa, []),
                     reason=pa.get("exclusion_reason"), vote_calls=[c["call_id"] for c in cs],
                     decided_by=decided_by(votes, final), origin={"kind": "migrated", "set": 1, "run_id": a["run_id"]})
        n += 1
    return n


def decided_by(votes: list[str], final: str, passes: tuple[str, ...] = ("A", "B", "C")) -> str:
    if len(votes) == 3 and len(set(votes)) == 3:
        return "split"
    return ",".join(p for p, v in zip(passes, votes) if v == final) or "split"


def migrate_legacy(project: Project) -> None:
    """Once per project (meta ``result_sets_migrated``): rename protocol keys holding ``#``, give each set 1 its
    scope, role and runs, and split a set screened over several scopes into migrated sets (offline, no call)."""
    st = project.store
    if st.get_meta(MIGRATED_META):
        return
    notes: list[str] = []
    for r in st.query("SELECT DISTINCT protocol FROM screen_result_sets WHERE protocol LIKE '%#%'"):
        old = r["protocol"]
        new = _free_name(project, old)
        _rename_protocol(project, old, new)
        notes.append(f"protocol {old!r} renamed to {new!r} ('#' now marks a result set)")
    linked = _linked_runs(project)
    users: dict[str, set[str]] = {}
    for proto, lists in linked.items():
        for ids in lists:
            for rid in ids:
                users.setdefault(rid, set()).add(proto)
    for s in [_row(r) for r in st.query("SELECT * FROM screen_result_sets WHERE result_set=1 AND scope IS NULL")]:
        proto = s["protocol"]
        if s["source"] == "import" and proto not in linked:
            st.execute("UPDATE screen_result_sets SET scope='corpus', role='corpus' WHERE key=?", (s["key"],))
            continue
        runs: list[dict] = []
        groups: dict[str, list[tuple[str, str]]] = {}
        for ids in linked.get(proto, []):
            first_scope = None
            for rid in ids:
                sc = _run_scope(project, rid)
                p = _pass_of(project, rid)
                if sc and sc.startswith("ids:"):
                    sc = first_scope
                elif first_scope is None:
                    first_scope = sc
                runs.append({"run_id": rid, "pass": p, **({"shared_legacy": True} if len(users.get(rid, ())) > 1 else {})})
                groups.setdefault(sc or "?", []).append((rid, p))
        dedup: dict[str, dict] = {}
        for x in runs:
            dedup.setdefault(x["run_id"], x)
        st.execute("UPDATE screen_result_sets SET runs=? WHERE key=?", (json.dumps(list(dedup.values())), s["key"]))
        scopes = [k for k in groups if k != "?"]
        if not runs or "?" in groups or not scopes:
            st.execute("UPDATE screen_result_sets SET scope='unknown' WHERE key=?", (s["key"],))
            notes.append(f"{proto}: set 1 kept as it was; its scope is unknown (wider screen sets scope {proto} 1 SCOPE)")
        elif len(scopes) == 1:
            st.execute("UPDATE screen_result_sets SET scope=?, role=? WHERE key=?",
                       (scopes[0], role_for(project, scopes[0]), s["key"]))
            notes.append(f"{proto}: set 1 kept as it was; scope {scopes[0]}")
        else:
            st.execute("UPDATE screen_result_sets SET scope='mixed', label=COALESCE(label, 'legacy set, several scopes') "
                       "WHERE key=?", (s["key"],))
            added = []
            for sc in scopes:
                ns = new_set(project, proto, "migrated", scope=sc, author="migration")
                pairs = list(dict.fromkeys(groups[sc]))
                add_runs(project, ns["key"], pairs)
                _rederive(project, ns["key"], pairs, pairs[0][0])
                added.append(f"set {ns['result_set']} for {sc}")
            notes.append(f"{proto}: set 1 kept as it was (several scopes); " + ", ".join(added)
                         + " added from runs " + ", ".join(dedup))
    if notes:
        from ..core.journal import _store_change
        for n in notes:
            _store_change(project, "result_set", f"migration: {n}", [], "migration", None)
        st.set_meta(NOTES_META, json.dumps(migration_notes(project) + notes))
    st.set_meta(MIGRATED_META, utcnow())
