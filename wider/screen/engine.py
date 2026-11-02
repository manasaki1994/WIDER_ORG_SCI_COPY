"""Screening engine: one isolated call per record, resumable, every raw response stored as JSONL.

A run = (prompt artifact version, provider, pass name, record set). The run directory holds run_spec.json, prompt.md,
schema.json, responses.jsonl, decisions.csv and errors.csv. ``replay`` rebuilds decisions from the JSONL offline.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from pathlib import Path

from ..core import jsonl
from ..core.project import Project
from ..core.provenance import RunSpec, sha256_json, sha256_text, stamp, utcnow
from ..core.text import word_count
from ..enrich.abstracts import current_version
from ..providers import ChatRequest, research_provider
from .codebook import load_prompt
from .rule import apply_rule


def user_message(title: str | None, abstract: str | None) -> str:
    return f"Title: {(title or '').strip()}\n\nAbstract: {(abstract or '').strip()}"


PASS_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,19}")
# a protocol name is shown on pages, written into export file names and the report; '+' is kept for derived protocols
# such as main+human
PROTOCOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+-]{0,79}")

# far above any corpus; a larger number would not fit the store's whole numbers
MAX_LIMIT = 1_000_000_000


def whole_limit(limit) -> int | None:
    """A record limit as the API received it (number or text) -> int, or None for every record."""
    if limit is None or (isinstance(limit, str) and not limit.strip()):
        return None
    text = str(limit).strip()
    if isinstance(limit, bool) or not re.fullmatch(r"-?\d+", text):
        raise ValueError(f"the record limit must be a whole number, e.g. 20 (got {limit!r}); "
                         "leave it empty to screen every record")
    return int(text)


def check_run_args(pass_name: str = "A", limit: int | None = None) -> None:
    """Refuse inputs that would silently screen the wrong records or write an unnamed run (CLI and page alike)."""
    if limit is not None and limit < 1:
        raise ValueError(f"the record limit must be at least 1 (got {limit}); leave it empty to screen every record")
    if limit is not None and limit > MAX_LIMIT:
        raise ValueError(f"the record limit must be at most {MAX_LIMIT:,} (got {limit}); "
                         "leave it empty to screen every record")
    if not (pass_name or "").strip():
        raise ValueError("the pass needs a name, e.g. A")
    # the name becomes part of the run's folder name, so it never holds a slash, a dot or a space
    if not PASS_NAME.fullmatch(pass_name):
        raise ValueError("a pass name is 1-20 letters A-Z, digits, '-' or '_', starting with a letter or digit, "
                         f"e.g. A or B2 (got {pass_name!r})")


def check_protocol_name(name: str) -> None:
    """Refuse a protocol name that is not a plain word (markup, a path, spaces), on the page and the CLI alike."""
    if not PROTOCOL_NAME.fullmatch(name or ""):
        raise ValueError("a protocol name is 1-80 letters A-Z, digits, '-', '_' or '+', starting with a letter or digit, "
                         f"e.g. main or main+human (got {name!r})")


def record_set(project: Project, scope: str, limit: int | None = None) -> list[dict]:
    """scope: 'sample:<name>' | 'corpus' | 'maybe:<protocol>' | 'ids:1,2,3'."""
    st = project.store
    if scope.startswith("sample:"):
        name = scope.split(":", 1)[1]
        rows = st.query("SELECT m.record_id, m.title, m.abstract FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                        "JOIN records m ON m.record_id=sr.record_id WHERE s.name=? ORDER BY sr.queue_pos", (name,))
    elif scope == "corpus":
        rows = st.query("SELECT record_id, title, abstract FROM records ORDER BY record_id")
    elif scope.startswith("maybe:"):
        proto = maybe_key(project, scope.split(":", 1)[1])
        rows = st.query("SELECT m.record_id, m.title, m.abstract FROM screen_decisions d JOIN records m ON m.record_id=d.record_id "
                        "WHERE d.protocol=? AND d.decision='maybe' ORDER BY m.record_id", (proto,))
    elif scope.startswith("ids:"):
        ids = [int(x) for x in scope.split(":", 1)[1].split(",") if x.strip()]
        rows = st.query(f"SELECT record_id, title, abstract FROM records WHERE record_id IN ({','.join('?' * len(ids))}) "
                        "ORDER BY record_id", ids)
    else:
        raise ValueError(f"unknown scope {scope!r}")
    out = [dict(r) for r in rows]
    return out[:limit] if limit else out


def maybe_key(project: Project, ref: str) -> str:
    """The decisions key a ``maybe:`` scope reads: ``P`` -> P's active set, ``P#n`` -> that set (``P#1`` is ``P``)."""
    from .result_sets import active_key, set_key, split_key

    if "#" in ref:
        p, n = split_key(ref)
        return set_key(p, n)
    return active_key(project, ref)


def _run_id(project: Project, kind: str, pass_name: str, prompt_version: int, provider: str, scope: str,
            protocol: str | None = None, result_set: int | None = None, tag: str | None = None) -> str:
    """Plain runs keep the ids they always had; a protocol run carries ``__<protocol>.s<n>`` (so two protocols or two
    sets never share a run), ``#`` as ``.`` and a long scope cut with a short sha256 (so two scopes never collide).
    A comparison run carries ``__<tag>`` (``cmp.<name>.c<i>.r<k>``, :func:`comparison_tag`) after that."""
    safe_scope = scope.replace(":", "-").replace(",", "_")
    if protocol is None and tag is None:
        return f"{kind}__{safe_scope[:40]}__p{prompt_version}__{provider}__{pass_name}"
    safe_scope = safe_scope.replace("#", ".")
    if len(safe_scope) > 40:
        safe_scope = safe_scope[:31] + "~" + sha256_text(scope)[:8]
    rid = f"{kind}__{safe_scope}__p{prompt_version}__{provider}__{pass_name}"
    if protocol is not None:
        rid += f"__{protocol}.s{result_set or 1}"
    return rid + (f"__{tag}" if tag else "")


def comparison_tag(name: str, config_index: int, repeat: int) -> str:
    """The run-id tag of repeat ``repeat`` of configuration ``config_index`` (both from 1) of comparison ``name``."""
    return f"cmp.{name}.c{config_index}.r{repeat}"


def is_comparison_run(run_id: str) -> bool:
    """A comparison's own run (design D1): its id ends in ``__cmp.<name>.c<i>.r<k>``."""
    return "__cmp." in (run_id or "")


RULE_SETTINGS = {"maybe_flags": "protocol.screening.maybe_flags", "recall_first": "protocol.screening.recall_first",
                 "quote_min_chars": "protocol.screening.quote_min_chars", "thin_words": "protocol.corpus.thin_words"}


def rule_spec(project: Project, pdata: dict) -> dict:
    """The rule settings a run applies to every answer: the prompt's elements and checks plus the protocol's rule
    switches. Live runs record it in run_spec.json (``settings.rule_spec``); replay reads it back from there."""
    sc = project.config.protocol.screening
    return {"elements": pdata["elements"], "include_checks": pdata.get("include_checks", []),
            "maybe_checks": pdata.get("maybe_checks", []), "absent_checks": pdata.get("absent_checks", []),
            "maybe_edge_cases": [k for k in ["non_empirical"] if k in json.dumps(pdata["schema"])],
            "maybe_flags": list(sc.maybe_flags), "label_groups": pdata.get("label_groups", []),
            "recall_first": sc.recall_first, "quote_min_chars": sc.quote_min_chars,
            "thin_words": project.config.protocol.corpus.thin_words}


def _recorded_rule_spec(run_dir: Path) -> dict | None:
    try:
        return json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8")).get("settings", {}).get("rule_spec")
    except (OSError, ValueError):
        return None


def _check_resume(run_id: str, run_dir: Path, spec: dict) -> None:
    """A run resumes only under the rule settings it started with (runs recorded before rule specs resume as before)."""
    old = _recorded_rule_spec(run_dir)
    if old is None or old == spec:
        return
    changed = ", ".join(RULE_SETTINGS.get(k, k) for k in sorted(set(old) | set(spec)) if old.get(k) != spec.get(k))
    raise ValueError(f"run {run_id} was started with different rule settings ({changed}); set them back to resume it")


class BackendChangeNeeded(ValueError):
    """The backend of an unfinished run changed: nothing ran; the researcher chooses ``continue`` or ``new``."""

    def __init__(self, change: dict):
        self.change = change
        f, t = change["from"], change["to"]
        super().__init__(f"The backend changed since run {change['run_id']} started ({change['done']} of "
                         f"{change['total']} records done): {f['provider']} {f['model']} -> {t['provider']} {t['model']}"
                         f" (changed: {', '.join(change['changed']) or 'nothing'}). Choose --backend-change continue "
                         "to finish it on the new backend" + (", or --backend-change new for a separate run"
                                                              if change["case"] == "b" else ""))


def _snapshot(project: Project, backend_hash: str | None) -> dict | None:
    if not backend_hash:
        return None
    r = project.store.one("SELECT snapshot FROM backend_snapshots WHERE backend_hash=?", (backend_hash,))
    return json.loads(r["snapshot"]) if r else None


def run_backend(project: Project, run_id: str) -> dict | None:
    """The backend a run last used: the snapshot of its latest call, else run_spec.json's provider."""
    st = project.store
    h = st.scalar("SELECT backend_hash FROM screen_calls WHERE run_id=? AND backend_hash IS NOT NULL "
                  "AND COALESCE(provider,'')<>'researcher' ORDER BY call_id DESC LIMIT 1", (run_id,))
    snap = _snapshot(project, h)
    if snap is not None:
        return snap
    run = st.one("SELECT dir FROM runs WHERE run_id=?", (run_id,))
    if run is None:
        return None
    try:
        return json.loads((project.root / run["dir"] / "run_spec.json").read_text(encoding="utf-8")).get("provider")
    except (OSError, ValueError):
        return None


def _done_ids(project: Project, run_id: str) -> set[int]:
    st = project.store
    return ({r["record_id"] for r in st.query("SELECT record_id FROM screen_calls WHERE run_id=? AND ok=1", (run_id,))}
            | {r["record_id"] for r in st.query("SELECT record_id FROM screen_call_reuse WHERE run_id=?", (run_id,))})


def _change(project: Project, run_id: str, case: str, old: dict | None, new: dict, ids: set[int]) -> dict | None:
    from .result_sets import changed_fields, method_identity

    done = _done_ids(project, run_id) & ids
    if not (ids - done):   # nothing left: no question, nothing recorded
        return None
    if old is not None and sha256_json(old) == sha256_json(new):
        return None
    mo, mn = method_identity(old or {}), method_identity(new)
    changed = changed_fields(mo, mn)
    full = changed or [f for f in changed_fields(old or {}, new) if f not in ("locality_source",)]
    if old is not None and not full:
        return None
    side = lambda d: {"provider": (d or {}).get("name"), "model": (d or {}).get("model"),   # noqa: E731
                      "hash": sha256_json(d)[:12] if d else None}
    return {"run_id": run_id, "case": case, "done": len(done), "total": len(ids), "from": side(old), "to": side(new),
            "changed": changed or full, "operational_only": not changed, "from_hash": sha256_json(old) if old else None,
            "to_hash": sha256_json(new)}


def backend_change(project: Project, kind: str, scope: str, pass_name: str, prompt_version: int | None,
                   provider_name: str | None = None, protocol: str | None = None, result_set: int | None = None,
                   skip_ids=None, limit: int | None = None, run_id: str | None = None,
                   identity: dict | None = None, tag: str | None = None) -> dict | None:
    """Preflight (no side effects): would this pass continue a run whose backend changed? Case (a): the run exists,
    records are left and the backend differs from the one its latest call used. Case (b): no run under this backend
    yet, but an unfinished run of the same kind, scope, prompt version, pass (and result set) exists under another
    backend name. Returns ``{run_id, case, done, total, from, to, changed, operational_only}`` or None. A change of
    operational fields only (CLI version or path, prices, timeout, name) is ``operational_only``: recorded, not asked."""
    from ..providers.registry import backend_identity

    st = project.store
    cfg = project.provider(provider_name) if provider_name else project.provider_for("screen")
    prow, _pdata = load_prompt(project, prompt_version)
    new = identity if identity is not None else backend_identity(cfg)
    skip = set(skip_ids or ())
    ids = {r["record_id"] for r in record_set(project, scope, limit)} - skip
    rid = run_id or _run_id(project, kind, pass_name, prow["version"], cfg.name, scope, protocol, result_set, tag)
    if st.one("SELECT 1 FROM runs WHERE run_id=?", (rid,)):
        return _change(project, rid, "a", run_backend(project, rid), new, ids)
    if run_id is not None:
        return None
    # case (b): the same run under another backend name (protocol runs: the same result set only)
    head = (f"{kind}__%__p{prow['version']}__%__{pass_name}" + (f"__{protocol}.s{result_set or 1}" if protocol else "")
            + (f"__{tag}" if tag else ""))
    for r in st.query("SELECT run_id FROM runs WHERE run_id LIKE ? ESCAPE '\\' AND kind=? ORDER BY started_at DESC",
                      (head.replace("_", r"\_"), kind)):
        other = r["run_id"]
        parts = other.split("__")
        if other == rid or len(parts) < 5:
            continue
        name = parts[3]
        if _run_id(project, kind, pass_name, prow["version"], name, scope, protocol, result_set, tag) != other:
            continue
        ch = _change(project, other, "b", run_backend(project, other), new, ids)
        if ch is not None:
            return ch
    return None


def _record_change(project: Project, run_dir: Path, change: dict, via: str, snapshot: dict) -> None:
    st = project.store
    at = utcnow()
    _write_snapshot(project, run_dir, snapshot)
    st.execute("INSERT INTO run_backend_changes(run_id,at,from_hash,to_hash,from_provider,from_model,to_provider,"
               "to_model,changed,confirmed_via,records_done) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (change["run_id"], at, change.get("from_hash"), change["to_hash"], change["from"]["provider"],
                change["from"]["model"], change["to"]["provider"], change["to"]["model"], json.dumps(change["changed"]),
                via, change["done"]))
    jsonl.append(run_dir / "backend_changes.jsonl",
                 {"at": at, "run_id": change["run_id"], "from": change["from"], "to": change["to"],
                  "changed": change["changed"], "confirmed_via": via, "records_done": change["done"]})


def _write_snapshot(project: Project, run_dir: Path, snapshot: dict) -> str:
    h = sha256_json(snapshot)
    project.store.execute("INSERT OR IGNORE INTO backend_snapshots(backend_hash,snapshot,first_used) VALUES(?,?,?)",
                          (h, json.dumps(snapshot, sort_keys=True), utcnow()))
    f = run_dir / f"backend_{h[:12]}.json"
    if not f.exists():
        run_dir.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(snapshot, indent=1, sort_keys=True), encoding="utf-8")
    return h


def eval_key(prompt: str, msg: str, method: dict, temperature, seed, kind: str, max_tokens: int, pass_name: str,
             prompt_version: int | None = None) -> str:
    """The evaluation key of one call (recorded decision 32): equal keys mean identical effective inputs. The prompt
    version is part of it, so a new version with the same text is never reused (design, evaluation reuse)."""
    return sha256_json({"input": sha256_text(prompt + "\n\x1e\n" + msg), "prompt_version": prompt_version,
                        "method": method, "temperature": temperature, "seed": seed, "kind": kind,
                        "max_tokens": max_tokens, "pass": pass_name})


def run_calls(project: Project, run_id: str) -> list[dict]:
    """The evaluations of a run: its own calls (``evaluation`` new) and the calls of other runs it reused (``reused``,
    with ``source_run_id``). Provider and model of a call recorded before v4 come from the run ("recorded per run")."""
    st = project.store
    out = []
    for r in st.query("SELECT c.*, r.provider run_provider, r.model run_model FROM screen_calls c "
                      "JOIN runs r ON r.run_id=c.run_id WHERE c.run_id=? ORDER BY c.record_id, c.pass", (run_id,)):
        d = dict(r)
        d.update(evaluation="new", source_run_id=None, provenance="recorded per call" if d["provider"] else
                 "recorded per run", provider=d["provider"] or d["run_provider"], model=d["model"] or d["run_model"])
        out.append(d)
    # a reused call may be another record's (same title and abstract, so the same evaluation key): it is filed under
    # the record it was reused for
    for r in st.query("SELECT c.*, u.run_id own_run, u.pass own_pass, u.record_id own_record, r.provider run_provider, "
                      "r.model run_model "
                      "FROM screen_call_reuse u JOIN screen_calls c ON c.call_id=u.source_call_id "
                      "JOIN runs r ON r.run_id=c.run_id WHERE u.run_id=? ORDER BY u.record_id", (run_id,)):
        d = dict(r)
        d.update(evaluation="reused", source_run_id=d["run_id"], run_id=d.pop("own_run"), pass_=None,
                 provenance="recorded per call" if d["provider"] else "recorded per run",
                 provider=d["provider"] or d["run_provider"], model=d["model"] or d["run_model"])
        d["pass"] = d.pop("own_pass")
        d["record_id"] = d.pop("own_record")
        d.pop("pass_", None)
        out.append(d)
    return out


def run_mixed(project: Project, run_id: str) -> bool:
    """A run is mixed when it continued on a changed method, or its evaluations name more than one model."""
    if project.store.scalar("SELECT COUNT(*) FROM run_backend_changes WHERE run_id=? AND confirmed_via<>'auto'",
                            (run_id,)):
        return True
    return len({c["model"] for c in run_calls(project, run_id) if c["ok"] and c["provider"] != "researcher"}) > 1


def run_experimental(project: Project, run_id: str) -> bool:
    """Some evaluation of the run was made on a backend marked experimental (Decision 20)."""
    hashes = {c["backend_hash"] for c in run_calls(project, run_id) if c.get("backend_hash")}
    return any((_snapshot(project, h) or {}).get("experimental") for h in hashes)


async def screen_async(project: Project, scope: str, pass_name: str = "A", provider_name: str | None = None,
                       prompt_version: int | None = None, limit: int | None = None, seed_offset: int = 0,
                       temperature: float | None = None, progress=None, kind: str = "screen",
                       protocol: str | None = None, result_set: int | None = None, backend_change: str | None = None,
                       confirmed_via: str = "cli", run_id: str | None = None, skip_ids=None,
                       reuse_evaluations: bool = False, author: str | None = None,
                       comparison: dict | None = None) -> dict:
    """``comparison`` (``{name, config_index, config, repeat}``) makes this a comparison run (design D1): its run id
    carries the comparison's tag, ``run_spec.json`` records it, and it raises no ``screen_failed`` question, writes no
    ``decisions.csv`` and records no stage result of its own."""
    check_run_args(pass_name, limit)
    tag = comparison_tag(comparison["name"], comparison["config_index"], comparison["repeat"]) if comparison else None
    if backend_change not in (None, "continue", "new"):
        raise ValueError(f"backend_change is continue or new (got {backend_change!r})")
    from ..core.protocol import check_max_tokens
    from .result_sets import method_identity

    st = project.store
    pcfg = project.provider(provider_name) if provider_name else project.provider_for("screen")
    max_tokens = project.config.protocol.screening.max_tokens
    check_max_tokens(max_tokens, pcfg)   # before consent, a run folder or a call
    from .devgate import check_scope

    check_scope(project, scope, skip_ids)   # ui-redesign D4: the second check, after the CLI's and the route's
    provider = research_provider(project, pcfg)   # consent first, as in the CLI and the job route
    prow, pdata = load_prompt(project, prompt_version)
    spec = rule_spec(project, pdata)
    identity = provider.identity()
    change = detect_backend_change(project, kind, scope, pass_name, prow["version"], pcfg.name, protocol, result_set,
                                   skip_ids, limit, run_id=run_id, identity=identity, tag=tag)
    if change is not None and not change["operational_only"]:
        if backend_change is None:
            raise BackendChangeNeeded(change)
        if backend_change == "new" and change["case"] == "a":
            raise ValueError(f"run {change['run_id']} is unfinished under {change['from']['model']}; a separate run needs "
                             f"another backend name (wider providers add --preset … --name {pcfg.name}2) or set the "
                             "backend back" if protocol is None else
                             "a separate run of a protocol is a new result set: add --new-set")
    if change is not None and (change["operational_only"] or backend_change == "continue"):
        run_id = change["run_id"]
    elif change is not None:   # case (b), "new": this backend's own run
        change = None
    run_id = run_id or _run_id(project, kind, pass_name, prow["version"], pcfg.name, scope, protocol, result_set, tag)
    existing = st.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    run_dir = project.root / existing["dir"] if existing else project.run_dir(kind, f"{stamp()}_{run_id}")
    if existing:
        _check_resume(run_id, run_dir, spec)
    recs = record_set(project, scope, limit)
    skip = set(skip_ids or ())
    recs = [r for r in recs if r["record_id"] not in skip]
    # records of this set already done in the run (a limit on a rerun counts only its own records)
    ids = {r["record_id"] for r in recs}
    done = _done_ids(project, run_id) & ids
    todo = [r for r in recs if r["record_id"] not in done]
    if existing and not todo and change is None:
        old = run_backend(project, run_id)
        if old is not None and method_identity(old) != method_identity(identity) and protocol is None:
            return {"run_id": run_id, "already_done": {"run_id": run_id, "model": (old or {}).get("model")},
                    "dir": str(run_dir), "n_records": len(recs), "n_ok": len(done), "n_err": 0, "resumed": len(done)}
    spec_obj = RunSpec(kind=kind, inputs={"prompt": pdata.get("prompt_sha256") or sha256_text(pdata["prompt"]),
                                           "schema": sha256_json(pdata["schema"])},
                       settings={"scope": scope, "pass": pass_name, "prompt_version": prow["version"], "n_records": len(recs),
                                 "seed_offset": seed_offset, "temperature": temperature, "rule": "rule-v3",
                                 "protocol_hash": project.config.protocol.hash(), "rule_spec": spec,
                                 **({"protocol": protocol, "result_set": result_set or 1} if protocol else {}),
                                 **({"comparison": {"name": comparison["name"], "config": comparison["config"],
                                                    "repeat": comparison["repeat"]}} if comparison else {})},
                       provider=identity)
    if not existing:
        spec_obj.write(run_dir)
        (run_dir / "prompt.md").write_text(pdata["prompt"], encoding="utf-8")
        (run_dir / "schema.json").write_text(json.dumps(pdata["schema"], indent=1), encoding="utf-8")
        st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,provider,model,prompt_version,status,n_items,n_ok,started_at,dir) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, kind, spec_obj.stamp, spec_obj.spec_hash(), pcfg.name, pcfg.model, prow["version"], "running",
                    len(recs), len(done), utcnow(), str(run_dir.relative_to(project.root))))
    else:
        # a rerun with a limit covers part of the run: the run keeps the size of the largest set it was given
        st.execute("UPDATE runs SET status='running', n_items=MAX(COALESCE(n_items, 0), ?) WHERE run_id=?", (len(recs), run_id))
    # the snapshot (and a confirmed change) is on disk before the first call
    backend_hash = _write_snapshot(project, run_dir, identity)
    if change is not None:
        _record_change(project, run_dir, change, "auto" if change["operational_only"] else confirmed_via, identity)
    thin_w = spec["thin_words"]
    qmin = spec["quote_min_chars"]
    method = method_identity(identity)
    eff_temp = temperature if temperature is not None else pcfg.temperature
    resp_path = run_dir / "responses.jsonl"
    n_ok = len(done)
    n_err = 0
    n_reused = 0
    lock = asyncio.Lock()

    def seed_of() -> int | None:
        return (pcfg.seed or 0) + seed_offset if pcfg.seed is not None else None

    async def one(i, r):
        nonlocal n_ok, n_err, n_reused
        msg = user_message(r["title"], r["abstract"])
        av = current_version(st, r["record_id"])   # the abstract version this call reads (D8)
        key = eval_key(pdata["prompt"], msg, method, eff_temp, seed_of() if seed_of() is not None else seed_offset,
                       kind, max_tokens, pass_name, prow["version"])
        if reuse_evaluations:
            src = st.one("SELECT call_id, run_id, rule_decision FROM screen_calls WHERE eval_key=? AND ok=1 AND run_id<>? "
                         "AND COALESCE(provider,'')<>'researcher' ORDER BY call_id LIMIT 1", (key, run_id))
            if src is not None:
                async with lock:
                    from ..core import researchers
                    st.execute("INSERT OR IGNORE INTO screen_call_reuse(run_id,record_id,pass,source_call_id,reused_at,"
                               "reused_by) VALUES(?,?,?,?,?,?)", (run_id, r["record_id"], pass_name, src["call_id"],
                                                                  utcnow(), researchers.sign(project, author)))
                    jsonl.append(resp_path, {"record_id": r["record_id"], "pass": pass_name, "reused": True,
                                             "source_run_id": src["run_id"], "source_call_id": src["call_id"],
                                             "at": utcnow()})
                    n_ok += 1
                    n_reused += 1
                    if progress:
                        progress(n_ok + n_err, len(recs), r["record_id"], src["rule_decision"])
                return
        req = ChatRequest(system=pdata["prompt"], user=msg, schema=pdata["schema"], max_tokens=max_tokens,
                          seed=seed_of(), temperature=temperature)
        res = await provider.complete(req)
        text = f"{r['title'] or ''} {r['abstract'] or ''}"
        thin = word_count(r["abstract"]) < thin_w
        rule = apply_rule(res.parsed, text, spec, thin=thin, quote_min_chars=qmin) if res.ok else None
        meta = res.meta if isinstance(res.meta, dict) else {}
        reported = ",".join(meta["models"]) if meta.get("models") else meta.get("model")
        line = {"record_id": r["record_id"], "pass": pass_name, "ok": res.ok, "input_sha256": res.input_sha256,
                "repaired": res.repaired, "attempts": res.attempts, "usage": res.usage, "meta": res.meta, "error": res.error, "text": res.text,
                "parsed": res.parsed, "rule": rule, "at": utcnow(), "backend_hash": backend_hash, "provider": pcfg.name,
                "model": pcfg.model, "model_reported": reported, "eval_key": key, "abstract_version": av}
        async with lock:
            jsonl.append(resp_path, line)
            # a successful evaluation is never replaced: only a failed row is retried in place
            st.execute(
                "INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,raw_decision,rule_decision,parsed,usage,attempts,error,at,"
                "backend_hash,provider,model,model_reported,eval_key,abstract_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id,record_id,pass) DO UPDATE SET input_sha256=excluded.input_sha256, "
                "ok=excluded.ok, raw_decision=excluded.raw_decision, rule_decision=excluded.rule_decision, parsed=excluded.parsed, "
                "usage=excluded.usage, attempts=excluded.attempts, error=excluded.error, at=excluded.at, "
                "backend_hash=excluded.backend_hash, provider=excluded.provider, model=excluded.model, "
                "model_reported=excluded.model_reported, eval_key=excluded.eval_key, "
                "abstract_version=excluded.abstract_version WHERE screen_calls.ok=0",
                (run_id, r["record_id"], pass_name, res.input_sha256, int(res.ok), rule["raw"] if rule else None,
                 rule["final"] if rule else None, json.dumps(res.parsed) if res.ok else None, json.dumps(res.usage),
                 res.attempts, res.error, utcnow(), backend_hash, pcfg.name, pcfg.model, reported, key, av))
            if res.ok:
                n_ok += 1
            elif comparison is not None:   # counted per configuration and retried on the next run (D1)
                n_err += 1
            else:
                n_err += 1
                from ..core.questions import raise_question
                raise_question(project, "screen_failed", f"{run_id}:{r['record_id']}:{pass_name}", r["title"] or f"record {r['record_id']}",
                               f"The model's answer for this record could not be used after {res.attempts} attempt"
                               f"{'s' if res.attempts != 1 else ''} ({(res.error or 'no answer')[:160]}). Pass {pass_name} has no "
                               f"decision for it. Decide it yourself, or leave it for the next run of the pass.",
                               context={"pass": pass_name, "error": (res.error or "")[:400]}, record_id=r["record_id"],
                               run_ref=run_id)
            if progress:
                progress(n_ok + n_err, len(recs), r["record_id"], rule["final"] if rule else f"error: {res.error}")

    from ..core.usage import track, usage_for_runs
    before = st.scalar("SELECT COALESCE(MAX(usage_id), 0) FROM llm_usage")  # the result card shows this call's tokens only
    try:
        with track(project, "screen", run_id):
            await asyncio.gather(*[one(i, r) for i, r in enumerate(todo)])
    except BaseException:   # cancelled or failed mid-run: finished calls are kept; the same run resumes on restart
        st.execute("UPDATE runs SET status='interrupted', n_ok=(SELECT COUNT(*) FROM screen_calls WHERE run_id=? AND ok=1) "
                   "WHERE run_id=?", (run_id, run_id))
        raise
    write_decisions_csv(project, run_id)
    # the runs row counts every record the run has screened, not only this call's set
    n_ok_run = len(_done_ids(project, run_id))
    st.execute("UPDATE runs SET status=?, n_ok=?, finished_at=? WHERE run_id=?",
               ("done" if n_err == 0 else "done_with_errors", n_ok_run, utcnow(), run_id))
    out = {"run_id": run_id, "dir": str(run_dir), "n_records": len(recs), "n_ok": n_ok, "n_err": n_err, "resumed": len(done),
           "usage": usage_for_runs(project, [run_id], after=before)}
    if n_reused:
        out["reused"] = n_reused
    if change is not None and not change["operational_only"]:
        out["backend_change"] = change
    if identity.get("experimental"):
        out["experimental"] = EXPERIMENTAL_NOTE
    if comparison is None:   # a comparison records one stage result of its own at the end (D1)
        from ..core.stage_result import record
        from ..core.stage_summaries import summarize_screen
        from .progress import run_name
        try:
            named = run_name(project, out["run_id"]) or {}
        except Exception:   # the headline falls back to the run id; a naming problem never fails a finished round
            named = {}
        record(project, summarize_screen({**out, **{k: named[k] for k in ("phrase", "pass", "test") if k in named}}, "pass"))
    return out


detect_backend_change = backend_change   # screen_async's parameter of the same name shadows the function
EXPERIMENTAL_NOTE = "experimental backend: not validated"


def screen(project: Project, scope: str, **kw) -> dict:
    return asyncio.run(screen_async(project, scope, **kw))


DECISION_COLUMNS = ["record_id", "pass", "ok", "raw_decision", "rule_decision", "input_sha256", "attempts", "error", "title",
                    "parsed", "provider", "model", "model_reported", "backend_hash", "evaluation", "source_run_id",
                    "experimental"]


def write_decisions_csv(project: Project, run_id: str) -> Path | None:
    """decisions.csv of a run: one row per evaluation, its own calls and those it reused, with who evaluated it.
    A comparison run writes none (its per-record table is the comparison's ``comparison.csv``, design D15): None."""
    st = project.store
    run = st.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if _spec_comparison(project.root / run["dir"]) is not None:
        return None
    out = project.root / run["dir"] / "decisions.csv"
    titles = {r["record_id"]: r["title"] for r in st.query(
        "SELECT m.record_id, m.title FROM records m WHERE m.record_id IN (SELECT record_id FROM screen_calls WHERE run_id=? "
        "UNION SELECT record_id FROM screen_call_reuse WHERE run_id=?)", (run_id, run_id))}
    rows = sorted(run_calls(project, run_id), key=lambda c: (c["record_id"], c["pass"]))
    snaps: dict[str | None, dict | None] = {}
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DECISION_COLUMNS)
        for r in rows:
            if r["backend_hash"] not in snaps:
                snaps[r["backend_hash"]] = _snapshot(project, r["backend_hash"])
            exp = bool((snaps[r["backend_hash"]] or {}).get("experimental"))
            w.writerow([r["record_id"], r["pass"], r["ok"], r["raw_decision"], r["rule_decision"], r["input_sha256"],
                        r["attempts"], r["error"] or "", titles.get(r["record_id"]), r["parsed"] or "", r["provider"] or "",
                        r["model"] or "", r["model_reported"] or "", r["backend_hash"] or "", r["evaluation"],
                        r["source_run_id"] or "", EXPERIMENTAL_NOTE if exp else ""])
    errs = [r for r in rows if not r["ok"]]
    with open(out.with_name("errors.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "pass", "error"])
        for r in errs:
            w.writerow([r["record_id"], r["pass"], r["error"]])
    return out


def _spec_comparison(run_dir: Path) -> dict | None:
    """The ``comparison`` entry of a run's ``run_spec.json``, or None (an ordinary run, or no spec on disk)."""
    try:
        return (json.loads((run_dir / "run_spec.json").read_text(encoding="utf-8")).get("settings") or {}).get("comparison")
    except (OSError, ValueError):
        return None


COMPARISON_NO_CSV = "comparison run: no decisions.csv"


def replay(project: Project, run_id: str) -> dict:
    """Rebuild rule decisions from the stored responses without any network call (for a changed rule or audit)."""
    st = project.store
    run = st.one("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if run is None:
        raise KeyError(run_id)
    from .devgate import check

    check(project, {r[0] for r in st.query("SELECT DISTINCT record_id FROM screen_calls WHERE run_id=?", (run_id,))})
    prow, pdata = load_prompt(project, run["prompt_version"])
    spec = _recorded_rule_spec(project.root / run["dir"])
    note = None
    if spec is None:   # a run recorded before rule specs: the current protocol's settings are the best guess
        spec = rule_spec(project, pdata)
        note = "no rule settings recorded; replayed with the current protocol's settings"
    thin_w = spec["thin_words"]
    n = changed = 0
    run_dir = project.root / run["dir"]
    for f in sorted(run_dir.glob("backend_*.json")):   # the backend snapshots the calls name
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        st.execute("INSERT OR IGNORE INTO backend_snapshots(backend_hash,snapshot,first_used) VALUES(?,?,?)",
                   (sha256_json(snap), json.dumps(snap, sort_keys=True), utcnow()))
    lines = list(jsonl.read(run_dir / "responses.jsonl"))
    retracted: dict[int, bool] = {}   # question_id -> retracted? (the last retraction line wins; undo of an undo restores)
    for line in lines:
        if "human_retracted" in line:
            retracted[line["question_id"]] = bool(line["human_retracted"])
    for line in lines:
        if line.get("reused"):   # an evaluation of another run: restored when that call is still there
            if st.one("SELECT 1 FROM screen_calls WHERE call_id=? AND run_id=?", (line["source_call_id"], line["source_run_id"])):
                st.execute("INSERT OR IGNORE INTO screen_call_reuse(run_id,record_id,pass,source_call_id,reused_at) "
                           "VALUES(?,?,?,?,?)", (run_id, line["record_id"], line["pass"], line["source_call_id"], line["at"]))
            continue
        if "human_retracted" in line or not line.get("ok"):
            continue
        if line.get("human"):   # the researcher's answer to a question: no model input to verify, the decision is theirs
            if retracted.get(line.get("question_id")):
                continue
            d = line["rule"]["final"]
            st.execute("UPDATE screen_calls SET raw_decision=?, rule_decision=?, ok=1 WHERE run_id=? AND record_id=? AND pass=?",
                       (d, d, run_id, line["record_id"], line["pass"]))
            n += 1
            continue
        r = st.one("SELECT title, abstract FROM records WHERE record_id=?", (line["record_id"],))
        if r is None:
            continue
        msg = user_message(r["title"], r["abstract"])
        if sha256_text(pdata["prompt"] + "\n\x1e\n" + msg) != line["input_sha256"]:
            raise ValueError(f"record {line['record_id']}: stored response was produced from different text")
        rule = apply_rule(line["parsed"], f"{r['title'] or ''} {r['abstract'] or ''}", spec,
                          thin=word_count(r["abstract"]) < thin_w, quote_min_chars=spec["quote_min_chars"])
        prev = st.one("SELECT rule_decision FROM screen_calls WHERE run_id=? AND record_id=? AND pass=?", (run_id, line["record_id"], line["pass"]))
        if prev is None or prev["rule_decision"] != rule["final"]:
            changed += 1
        # the provenance of the call comes back from its line (lines written before v4 have none: kept as they are)
        st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,raw_decision,rule_decision,parsed,usage,attempts,error,at,"
                   "backend_hash,provider,model,model_reported,eval_key,abstract_version) VALUES(?,?,?,?,1,?,?,?,?,?,NULL,?,?,?,?,?,?,?) "
                   "ON CONFLICT(run_id,record_id,pass) DO UPDATE SET raw_decision=excluded.raw_decision, "
                   "rule_decision=excluded.rule_decision, parsed=excluded.parsed, "
                   "backend_hash=COALESCE(excluded.backend_hash, screen_calls.backend_hash), "
                   "provider=COALESCE(excluded.provider, screen_calls.provider), model=COALESCE(excluded.model, screen_calls.model), "
                   "model_reported=COALESCE(excluded.model_reported, screen_calls.model_reported), "
                   "eval_key=COALESCE(excluded.eval_key, screen_calls.eval_key), "
                   "abstract_version=COALESCE(screen_calls.abstract_version, excluded.abstract_version)",
                   (run_id, line["record_id"], line["pass"], line["input_sha256"], rule["raw"], rule["final"], json.dumps(line["parsed"]),
                    json.dumps(line.get("usage")), line.get("attempts"), utcnow(), line.get("backend_hash"), line.get("provider"),
                    line.get("model"), line.get("model_reported"), line.get("eval_key"),
                    line.get("abstract_version") or current_version(st, line["record_id"])))
        n += 1
    written = write_decisions_csv(project, run_id)
    out = {"run_id": run_id, "replayed": n, "changed": changed}
    if written is None:
        note = f"{note}; {COMPARISON_NO_CSV}" if note else COMPARISON_NO_CSV
    if note:
        out["note"] = note
    return out


def runs(project: Project) -> list[dict]:
    return [dict(r) for r in project.store.query("SELECT * FROM runs ORDER BY started_at")]
