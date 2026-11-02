"""Configuration comparison on the development sample (change screening-config-comparison, design D1-D7, D15).

A comparison is a stored object (``screen_comparisons``) with its own tagged runs (``screen_comparison_runs``): each
configuration (backend, prompt version, temperature) screens the sample's records R times, and the figures come from
the current final labels through ``evaluate.score``. No configuration is named best, and the figures describe the
purposive sample, not the corpus. Engines here are pure: the CLI (``wider screen compare``) and the routes
(``routes_compare.py``) call the same functions.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow
from .engine import (
    _done_ids,
    _run_id,
    check_protocol_name,
    comparison_tag,
    eval_key,
    record_set,
    user_message,
)

NOT_LOCKED = "sample {name} is not locked; lock it before comparing: wider sample lock {name}"
NOT_CORPUS = "These figures describe the development sample (records picked by hand), not the corpus."
UNLOCKED_NOW = "sample {name} is unlocked: these figures use its current labels"
ADDED = ("{n} record{s} {were} added to {sample} after this comparison; run a new comparison to include "
         "them")
GONE = "{n} record{s} of this comparison {are} no longer in the sample"
NO_TEMPERATURE = "temperature not sent by this backend"
NO_TEMPERATURE_SET = "temperature not set (the backend's default)"
TEMP_ZERO = "temperature 0: tests only whether the backend gives the same answer twice"
ONE_REPEAT = "consistency not measured (one repeat)"
NO_LABELS = "no record of this comparison has a final label: nothing was scored"
NOT_RUN = "not run"
NO_CLUSTERS = "no research areas: the ranking has no clusters (wider rank cluster)"
NO_RANKING = "no research areas: the sample has no ranking"
NOT_CLUSTERED = "not clustered"
NO_DOUBLE_UNDERSCORE = "a comparison name may not contain '__'"
NEED_CONFIG = "at least one --config is needed"
MAX_REPEATS = 10
POOL_ORDER = ["likely_include", "near_miss", "adjacent", "boundary", "topical_coverage", "no_abstract",
              "clearly_irrelevant"]
CONFIG = re.compile(r"^([^:@\s]+)(?::p(\d+))?(?:@(\d+(?:\.\d+)?))?$")


class Conflict(ValueError):
    """A refusal the routes answer with 409: an unlocked sample, another configuration list under a stored name, or a
    resume whose backend changed or was removed. Every other refusal is a plain ``ValueError`` (400)."""


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


# ------------------------------------------------------------------------------------------- configurations
def parse_config(text: str) -> dict[str, Any]:
    """``NAME[:pN][@T]`` (``a``, ``a:p2``, ``a:p2@0.7``) as ``{label, provider, prompt_version, temperature}``; the
    omitted parts are None until :func:`resolve_config` fills them."""
    raw = (text or "").strip()
    m = CONFIG.match(raw)
    if not m:
        raise ValueError(f"configuration {text!r} is not NAME, NAME:pN, NAME@T or NAME:pN@T (for example a:p2@0.7)")
    return {"label": raw, "provider": m[1], "prompt_version": int(m[2]) if m[2] else None,
            "temperature": float(m[3]) if m[3] else None, "temperature_text": m[3]}


def resolve_config(project: Project, c: dict[str, Any]) -> dict[str, Any]:
    """The stored form of a parsed configuration: the backend's model and method identity, the prompt version (the
    current one when omitted) and the temperature (the backend's when omitted, None when it sends none)."""
    from ..construct import artifacts
    from ..core.backends import sends_temperature
    from ..providers.registry import backend_identity
    from .codebook import load_prompt
    from .result_sets import method_identity

    try:
        cfg = project.provider(c["provider"])
    except KeyError:
        raise ValueError(f"backend {c['provider']} is not configured (wider providers list)") from None
    sent = sends_temperature(cfg)
    if c["temperature"] is not None and not sent:
        raise ValueError(f"backend {cfg.name} does not send a temperature; compare it without "
                         f"@{c.get('temperature_text') or c['temperature']}")
    if c["prompt_version"] is not None and not any(r["version"] == c["prompt_version"]
                                                   for r in artifacts.list_versions(project, "prompts")):
        raise ValueError(f"prompt version {c['prompt_version']} does not exist (wider screen build-prompt)")
    row, _data = load_prompt(project, c["prompt_version"])
    given = c["temperature"] is not None
    return {"label": c["label"], "provider": cfg.name, "kind": cfg.kind, "model": cfg.model,
            "prompt_version": row["version"],
            "temperature": c["temperature"] if given else (cfg.temperature if sent else None),
            "temperature_given": given, "temperature_sent": sent,
            "method": method_identity(backend_identity(cfg))}


def public_config(c: dict[str, Any]) -> dict[str, Any]:
    """A stored configuration without its method identity, for outputs and ``run_spec.json``."""
    return {k: c[k] for k in ("label", "provider", "kind", "model", "prompt_version", "temperature",
                              "temperature_given", "temperature_sent") if k in c}


def temperature_text(c: dict[str, Any]) -> str:
    if not c.get("temperature_sent", True):
        return NO_TEMPERATURE
    return NO_TEMPERATURE_SET if c.get("temperature") is None else f"{c['temperature']:g}"


# ------------------------------------------------------------------------------------------- refusals
def _sample(project: Project, name: str) -> dict:
    """The sample row; refuses a missing sample and the probability sample (ValueError, 400)."""
    from ..sample.labels import sample_by_name

    try:
        s = sample_by_name(project, name)
    except KeyError:
        raise ValueError(f"sample {name} does not exist; draw it with wider sample draw {name}") from None
    if s["kind"] == "probability":
        raise ValueError(f"{name!r} is the probability sample; a comparison there would select the model on the "
                         "recall set")
    return s


def check_sample(project: Project, name: str) -> dict:
    """D7: a comparison runs only on an existing, purposive, locked sample. ``Conflict`` (409) with
    :data:`NOT_LOCKED` for an unlocked one, before anything is stored or sent."""
    s = _sample(project, name)
    if not s["locked"]:
        raise Conflict(NOT_LOCKED.format(name=name))
    return s


def _dev_gate(project: Project, ids: list[int]) -> None:
    """ui-redesign D4: after the lock check, so an unlocked sample keeps :data:`NOT_LOCKED`."""
    from .devgate import check

    check(project, ids)


def check_name(name: str) -> None:
    if "__" in (name or ""):
        raise ValueError(NO_DOUBLE_UNDERSCORE)
    check_protocol_name(name)


def _stored(project: Project, name: str) -> dict | None:
    r = project.store.one("SELECT * FROM screen_comparisons WHERE name=?", (name,))
    return dict(r) if r else None


def _value_at(d: dict | None, dotted: str):
    for part in dotted.split("."):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


def backend_changes(project: Project, name: str, configs: list[dict]) -> list[str]:
    """One phrase per stored backend that is gone or whose method identity changed since the comparison first ran."""
    from ..providers.registry import backend_identity
    from .result_sets import changed_fields, method_identity

    out: list[str] = []
    for prov, c in {c["provider"]: c for c in reversed(configs)}.items():
        try:
            cfg = project.provider(prov)
        except KeyError:
            out.append(f"backend {prov} is no longer configured")
            continue
        now = method_identity(backend_identity(cfg))
        fields = changed_fields(c["method"], now)
        if fields:
            f = fields[0]
            out.append(f"backend {prov} changed since {name} first ran ({f} {_value_at(c['method'], f)}, now "
                       f"{_value_at(now, f)})")
    order = list(dict.fromkeys(c["provider"] for c in configs))
    return sorted(out, key=lambda s: order.index(s.split(" ", 2)[1]))


def _scope(ids: list[int]) -> str:
    return "ids:" + ",".join(str(i) for i in ids)


def _run_ids(project: Project, name: str, configs: list[dict], repeats: int, ids: list[int]):
    scope = _scope(ids)
    for i, c in enumerate(configs, 1):
        for k in range(1, repeats + 1):
            yield i, k, c, _run_id(project, "screen", "A", c["prompt_version"], c["provider"], scope, None, None,
                                   comparison_tag(name, i, k))


def _unfinished(project: Project, name: str, configs: list[dict], repeats: int, ids: list[int]) -> bool:
    st = project.store
    return any(st.scalar("SELECT status FROM runs WHERE run_id=?", (rid,)) != "done"
               for _i, _k, _c, rid in _run_ids(project, name, configs, repeats, ids))


def prepare(project: Project, name: str, sample: str | None, configs: list[str], repeats: int = 3,
            lock: bool = True) -> dict[str, Any]:
    """Every refusal of a comparison, in order, storing nothing: the name, at least one configuration, the repeats,
    the sample (exists, purposive, locked unless ``lock`` is False), and for a stored name the same list and backends
    unchanged. Returns ``{sample, sample_row, configs (resolved or stored), stored, ids}``."""
    check_name(name)
    if not configs:
        raise ValueError(NEED_CONFIG)
    parsed = [parse_config(c) for c in configs]
    labels = [c["label"] for c in parsed]
    dup = next((x for x in labels if labels.count(x) > 1), None)
    if dup:
        raise ValueError(f"configuration {dup} is given twice")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or not 1 <= repeats <= MAX_REPEATS:
        raise ValueError(f"repeats is a whole number from 1 to {MAX_REPEATS}")
    sample = sample or project.config.protocol.sampling.development_sample
    s = check_sample(project, sample) if lock else _sample(project, sample)
    row = _stored(project, name)
    if row is not None:
        stored = json.loads(row["configs"])
        if [c["label"] for c in stored] != labels or row["repeats"] != repeats or row["sample"] != sample:
            given = ", ".join(c["label"] for c in stored)
            raise Conflict(f"comparison {name} already exists with configuration"
                           f"{_plural(len(stored), '', 's')} {given}, {row['repeats']} repeat"
                           f"{'s' if row['repeats'] != 1 else ''} on {row['sample']}; give another list a new name")
        ids = json.loads(row["record_ids"] or "[]")
        if lock:
            _dev_gate(project, ids)
        if _unfinished(project, name, stored, repeats, ids):
            changed = backend_changes(project, name, stored)
            if changed:
                raise Conflict("; ".join(changed) + "; start a new comparison")
        return {"sample": sample, "sample_row": s, "configs": stored, "stored": row, "ids": ids}
    resolved = [resolve_config(project, c) for c in parsed]
    ids = sorted(r["record_id"] for r in record_set(project, f"sample:{sample}"))
    if not ids:
        raise ValueError(f"sample {sample} has no records")
    if lock:
        _dev_gate(project, ids)
    return {"sample": sample, "sample_row": s, "configs": resolved, "stored": None, "ids": ids}


# ------------------------------------------------------------------------------------------- estimate (D5)
def estimate(project: Project, name: str, sample: str | None, configs: list[str], repeats: int = 3,
             reuse_evaluations: bool = False, probe: bool = True) -> dict[str, Any]:
    """Records, runs and new calls (configurations × repeats × records, less records done in a resumed run and,
    with ``reuse_evaluations``, less stored identical evaluations) and each backend's locality and cost range. Not
    refused for an unlocked sample (D7); writes nothing."""
    from ..core.estimate import cost_range, range_text
    from ..core.protocol import backend_output_limit
    from ..providers.registry import backend_identity
    from .codebook import load_prompt
    from .result_sets import method_identity

    prep = prepare(project, name, sample, configs, repeats, lock=False)
    ids, cfgs = prep["ids"], prep["configs"]
    st = project.store
    recs = {r["record_id"]: r for r in record_set(project, _scope(ids))} if ids else {}
    msgs = {rid: user_message(r["title"], r["abstract"]) for rid, r in recs.items()}
    max_tokens = project.config.protocol.screening.max_tokens
    per_backend: dict[str, list[tuple[str, str]]] = {}
    n_new = n_reusable = n_done = runs_done = 0
    for _i, k, c, rid in _run_ids(project, name, cfgs, repeats, ids):
        pcfg = project.provider(c["provider"])
        _row, pdata = load_prompt(project, c["prompt_version"])
        done = _done_ids(project, rid) & set(recs)
        n_done += len(done)
        if st.scalar("SELECT status FROM runs WHERE run_id=?", (rid,)) == "done":
            runs_done += 1
        method = method_identity(backend_identity(pcfg))
        temp = c["temperature"] if c.get("temperature_given") else pcfg.temperature
        seed = (pcfg.seed or 0) + (k - 1) if pcfg.seed is not None else k - 1
        calls = per_backend.setdefault(c["provider"], [])
        for rid_ in recs:
            if rid_ in done:
                continue
            if reuse_evaluations:
                key = eval_key(pdata["prompt"], msgs[rid_], method, temp, seed, "screen", max_tokens, "A",
                               c["prompt_version"])
                if st.scalar("SELECT COUNT(*) FROM screen_calls WHERE eval_key=? AND ok=1 AND run_id<>? "
                             "AND COALESCE(provider,'')<>'researcher'", (key, rid)):
                    n_reusable += 1
                    continue
            calls.append((pdata["prompt"], msgs[rid_]))
            n_new += 1
    backends = []
    for prov, calls in per_backend.items():
        pcfg = project.provider(prov)
        cap = backend_output_limit(pcfg)
        r = cost_range(project, pcfg, "screen", calls, min(max_tokens, cap) if cap else max_tokens, probe=probe)
        r["lines"] = range_text(r)
        backends.append(r)
    runs = len(cfgs) * repeats
    out = {"name": name, "sample": prep["sample"], "records": len(ids), "runs": runs, "runs_done": runs_done,
           "new_calls": n_new, "done_calls": n_done, "configs": [public_config(c) for c in cfgs],
           "repeats": repeats, "backends": backends, "resume": prep["stored"] is not None,
           "locked": bool(prep["sample_row"]["locked"])}
    if reuse_evaluations:
        out["reusable"] = n_reusable
    out["lines"] = estimate_lines(out)
    return out


def estimate_lines(e: dict[str, Any]) -> list[str]:
    """The estimate in plain words (the CLI prints these; the card builds the same from the dict)."""
    head = (f"Comparison {e['name']}: {e['records']} records, {len(e['configs'])} configuration"
            f"{_plural(len(e['configs']), '', 's')} × {e['repeats']} repeat{_plural(e['repeats'], '', 's')} = "
            f"{e['runs']} run{_plural(e['runs'], '', 's')}, {e['new_calls']} new call{_plural(e['new_calls'], '', 's')}")
    lines = [head]
    if e.get("runs_done"):
        lines.append(f"{e['runs_done']} of {e['runs']} runs already finished")
    if "reusable" in e:
        lines.append(f"{e['reusable']} identical evaluation{_plural(e['reusable'], ' is', 's are')} reused")
    for b in e["backends"]:
        lines.append(f"backend {b['backend']} ({b['locality']}):")
        lines += ["  " + x for x in b["lines"]]
    return lines


# ------------------------------------------------------------------------------------------- run (D1)
async def run_async(project: Project, name: str, sample: str | None, configs: list[str], repeats: int = 3,
                    reuse_evaluations: bool = False, author: str | None = None, progress=None,
                    on_run=None) -> dict[str, Any]:
    """Run (or resume) comparison ``name``: refusals first (:func:`prepare`), then consent for each distinct backend,
    then the comparison row, then each configuration × repeat as a tagged ``screen_async`` on the stored ids; at the
    end ``finished_at``, ``comparison.csv`` and one ``screen`` stage result. Returns :func:`results`."""
    from ..core.backend_consent import require_backend_consent
    from .engine import screen_async

    prep = prepare(project, name, sample, configs, repeats)
    cfgs, ids, sample = prep["configs"], prep["ids"], prep["sample"]
    for prov in dict.fromkeys(c["provider"] for c in cfgs):
        require_backend_consent(project, project.provider(prov), "research")
    from ..core import researchers

    st = project.store
    if prep["stored"] is None:
        st.execute("INSERT INTO screen_comparisons(name,sample,configs,repeats,record_ids,author,created_at) "
                   "VALUES(?,?,?,?,?,?,?)", (name, sample, json.dumps(cfgs), repeats, json.dumps(ids),
                                             researchers.sign(project, author), utcnow()))
    cid = st.scalar("SELECT comparison_id FROM screen_comparisons WHERE name=?", (name,))
    scope = _scope(ids)
    for i, k, c, rid in _run_ids(project, name, cfgs, repeats, ids):
        st.execute("INSERT OR IGNORE INTO screen_comparison_runs(comparison_id,config_index,repeat,run_id) "
                   "VALUES(?,?,?,?)", (cid, i, k, rid))
        if st.scalar("SELECT status FROM runs WHERE run_id=?", (rid,)) == "done":
            continue
        if on_run:
            on_run(c, k, rid)
        out = await screen_async(project, scope, pass_name="A", provider_name=c["provider"],
                                 prompt_version=c["prompt_version"], seed_offset=k - 1,
                                 temperature=c["temperature"] if c.get("temperature_given") else None,
                                 reuse_evaluations=reuse_evaluations, author=author, progress=progress,
                                 comparison={"name": name, "config_index": i, "config": public_config(c),
                                             "repeat": k})
        if out["run_id"] != rid:
            st.execute("UPDATE screen_comparison_runs SET run_id=? WHERE comparison_id=? AND config_index=? "
                       "AND repeat=?", (out["run_id"], cid, i, k))
    st.execute("UPDATE screen_comparisons SET finished_at=? WHERE comparison_id=?", (utcnow(), cid))
    res = results(project, name)
    path = write_csv(project, name, res)
    res["csv"] = str(path.relative_to(project.root))
    _record_stage(project, res)
    return res


def run(project: Project, name: str, sample: str | None, configs: list[str], **kw) -> dict[str, Any]:
    return asyncio.run(run_async(project, name, sample, configs, **kw))


def _record_stage(project: Project, res: dict[str, Any]) -> None:
    from ..core.stage_result import StageResult, record

    n, r = len(res["configs"]), res["repeats"]
    warnings = [f"{row['label']}: {row['failed_text']}" for row in res["configs"] if row["failed"]]
    record(project, StageResult(
        stage="screen", action="compare",
        headline=(f"Comparison {res['name']}: {n} configuration{_plural(n, '', 's')} × {r} repeat"
                  f"{_plural(r, '', 's')} on {res['sample']}"),
        metrics={"records": res["records"], "configurations": n, "repeats": r},
        rows=[{"configuration": row["label"], "misses": row["misses_text"],
               "false includes": row["false_includes_text"], "failed": row["failed"]} for row in res["configs"]],
        warnings=warnings, outputs=[res["csv"]] if res.get("csv") else []))


# ------------------------------------------------------------------------------------------- results (D2-D4)
def _range(values: list[int]) -> list[int] | None:
    return [min(values), max(values)] if values else None


def _range_text(r: list[int] | None) -> str:
    return "–" if r is None else f"{r[0]}–{r[1]}"


def _areas(project: Project, s: dict | None) -> tuple[dict[int, int] | None, dict[int, list[str]], str | None]:
    from ..sample.design import cluster_labels, cluster_terms

    if s is None or s.get("ranking_id") is None:
        return None, {}, NO_RANKING
    rname = project.store.scalar("SELECT name FROM rankings WHERE ranking_id=?", (s["ranking_id"],))
    if rname is None:
        return None, {}, NO_RANKING
    labels = cluster_labels(project, rname)
    if not labels:
        return None, {}, NO_CLUSTERS
    return labels, cluster_terms(project, rname), None


def _pool(p: str | None) -> str:
    return (p or "").split(":")[0] or "(no pool)"


def _breakdown(keys: dict[int, Any], labels: dict[int, dict], repeats: list[dict], order: list) -> list[dict]:
    """Per key (pool or area): labelled n, and misses and false includes as min–max over the repeats."""
    rows = []
    for key in order:
        members = {rid for rid, k in keys.items() if k == key}
        n = sum(1 for rid in members if rid in labels)
        misses = [sum(1 for x in rep["rows"] if x["record_id"] in members and x["category"] == "MISS")
                  for rep in repeats]
        fi = [sum(1 for x in rep["rows"] if x["record_id"] in members and x["category"] == "FALSE INCLUDE")
              for rep in repeats]
        rows.append({"key": key, "n": n, "records": len(members), "misses": _range(misses),
                     "false_includes": _range(fi), "misses_text": _range_text(_range(misses)),
                     "false_includes_text": _range_text(_range(fi))})
    return rows


FIGURES_KEPT = ("index", "label", "provider", "kind", "model", "prompt_version", "temperature", "temperature_sent",
                "temperature_text", "repeats_done")


def results(project: Project, name: str) -> dict[str, Any]:
    """The comparison's figures from its runs and the current final labels. KeyError for an unknown name. While its
    sample is a purposive sample that is not complete, the figures are left out and ``hidden`` says why (ui-redesign
    D4); the configurations keep their names and settings."""
    from .devgate import HIDDEN, _records, open_samples

    res = _results(project, name)
    opened = next((c for c in open_samples(project) if c["sample"] == res["sample"]), None)
    if opened:
        said = HIDDEN.format(n=len(_records(project, [res["sample"]])))
        # the label count under the protocol for the development sample (two reviewers: one label is not final), as
        # on Screening; the comparison's own count reads single labels as final (ui-tester finding, 29 Oct)
        left = len(opened["missing"])
        res = {**res, "hidden": said, "notes": [said], "labelled": opened["total"] - left,
               "labelled_text": f"{left} of {opened['total']} records without a final label",
               "configs": [{k: c[k] for k in FIGURES_KEPT if k in c} for c in res["configs"]]}
    return res


def _results(project: Project, name: str) -> dict[str, Any]:
    from ..recall.estimate import disagreement_line, open_disagreements
    from ..sample.labels import changed_after_line, final_labels, sample_by_name
    from .evaluate import calls_by_record, score
    from .models_summary import by_model

    row = _stored(project, name)
    if row is None:
        raise KeyError(f"no comparison {name!r}")
    st = project.store
    cfgs = json.loads(row["configs"])
    ids = json.loads(row["record_ids"] or "[]")
    sample = row["sample"]
    try:
        s = sample_by_name(project, sample)
    except KeyError:
        s = None
    current = {r["record_id"]: r for r in final_labels(project, sample)} if s else {}
    in_sample = [i for i in ids if i in current]
    keep = set(in_sample)
    added = len(set(current) - set(ids))
    gone = len(ids) - len(in_sample)
    labels = {rid: current[rid] for rid in in_sample if current[rid]["decision"]}
    titles = {r["record_id"]: r["title"] for r in current.values()}
    area_of, terms, area_msg = _areas(project, s)
    pools = {rid: _pool(current[rid]["pool"]) for rid in in_sample}
    pool_order = [p for p in POOL_ORDER if p in pools.values()] + sorted(set(pools.values()) - set(POOL_ORDER))
    areas = None
    if area_of is not None:
        areas = {rid: area_of.get(rid, NOT_CLUSTERED) for rid in in_sample}
        nums = sorted({a for a in areas.values() if a != NOT_CLUSTERED})
        area_order = nums + ([NOT_CLUSTERED] if NOT_CLUSTERED in areas.values() else [])
    runs = {(r["config_index"], r["repeat"]): r["run_id"] for r in st.query(
        "SELECT config_index, repeat, run_id FROM screen_comparison_runs WHERE comparison_id=?",
        (row["comparison_id"],))}
    out_rows = []
    for i, c in enumerate(cfgs, 1):
        reps, failed, ptok, ctok, cost, priced = [], 0, 0, 0, 0.0, True
        for k in range(1, row["repeats"] + 1):
            rid = runs.get((i, k))
            status = st.scalar("SELECT status FROM runs WHERE run_id=?", (rid,)) if rid else None
            if status is None:
                continue
            calls = {r: v for r, v in calls_by_record(project, rid).items()
                     if r in keep and (v.get("provider") or "") != "researcher"}
            scored = score(calls, labels)
            cats = Counter(x["category"] for x in scored)
            failed += st.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=? AND ok=0", (rid,)) or 0
            tot = by_model(project, run_id=rid)["totals"]
            ptok += tot["prompt_tokens"] or 0
            ctok += tot["completion_tokens"] or 0
            if tot["cost_usd"] is None:
                priced = False
            else:
                cost += tot["cost_usd"]
            reps.append({"repeat": k, "run_id": rid, "status": status, "screened": len(calls), "scored": len(scored),
                         "misses": cats["MISS"], "false_includes": cats["FALSE INCLUDE"],
                         "false_maybes": cats["FALSE MAYBE"], "agree": cats["agree"], "rows": scored,
                         "decisions": {r: v["rule_decision"] for r, v in calls.items()}})
        rng = {key: _range([r[key] for r in reps]) for key in ("misses", "false_includes", "false_maybes", "agree",
                                                                 "scored", "screened")}
        cons: dict[str, Any] = {"measured": False, "share": None, "text": None, "changed": [], "notes": []}
        if row["repeats"] == 1:
            cons["notes"].append(ONE_REPEAT)
        elif reps:
            base = [r for r in in_sample if all(r in rep["decisions"] for rep in reps)]
            changed = [r for r in base if len({rep["decisions"][r] for rep in reps}) > 1]
            same = len(base) - len(changed)
            cons.update(measured=True, consistent=same, base=len(base),
                        share=round(same / len(base), 4) if base else None,
                        text=f"{same} of {len(base)} record{_plural(len(base), '', 's')}"
                             + (f" ({round(100 * same / len(base))}%)" if base else ""),
                        changed=[{"record_id": r, "title": titles.get(r),
                                  "decisions": [rep["decisions"][r] for rep in reps],
                                  "decisions_text": ", ".join(rep["decisions"][r] for rep in reps)} for r in changed])
        if reps and row["repeats"] > 1 and c.get("temperature_sent", True) and c.get("temperature") == 0:
            cons["notes"].append(TEMP_ZERO)
        rowd = {**public_config(c), "index": i, "temperature_text": temperature_text(c),
                "runs": [{k: v for k, v in r.items() if k not in ("rows", "decisions")} for r in reps],
                "repeats_done": sum(1 for r in reps if r["status"] in ("done", "done_with_errors")),
                **{key: v for key, v in rng.items()},
                **{f"{key}_text": _range_text(v) for key, v in rng.items()},
                "labelled": len(labels), "unlabelled": len(in_sample) - len(labels),
                "failed": failed, "failed_text": f"{failed} failed call{_plural(failed, '', 's')}",
                "prompt_tokens": ptok, "completion_tokens": ctok,
                "cost_usd": round(cost, 6) if priced else None,
                "cost": f"${cost:.4f}" if priced else "not priced", "consistency": cons,
                "by_pool": _breakdown(pools, labels, reps, pool_order)}
        if areas is not None:
            rowd["by_area"] = _breakdown(areas, labels, reps, area_order)
            for a in rowd["by_area"]:
                a["terms"] = terms.get(a["key"], []) if a["key"] != NOT_CLUSTERED else []
        out_rows.append(rowd)
    notes = []
    unlocked = s is not None and not s["locked"]
    if unlocked:
        notes.append(UNLOCKED_NOW.format(name=sample))
    if added:
        notes.append(ADDED.format(n=added, s=_plural(added, "", "s"), were=_plural(added, "was", "were"),
                                  sample=sample))
    if gone:
        notes.append(GONE.format(n=gone, s=_plural(gone, "", "s"), are=_plural(gone, "is", "are")))
    if in_sample and not labels:
        notes.append(NO_LABELS)
    n_open, used = open_disagreements([current[rid] for rid in in_sample])
    if n_open:   # whose labels stand where reviewers disagree (D9)
        notes.append(disagreement_line(n_open, used, "comparison"))
    if s is not None:
        changed = changed_after_line(project, sample, row["finished_at"] or row["created_at"], "this comparison",
                                     tail=not unlocked)   # beside UNLOCKED_NOW the tail would say it twice (D6)
        if changed:
            notes.append(changed)
    return {"name": name, "sample": sample, "repeats": row["repeats"], "author": row["author"],
            "created_at": row["created_at"], "finished_at": row["finished_at"], "records": len(ids),
            "records_in_sample": len(in_sample), "labelled": len(labels),
            "labelled_text": f"{len(labels)} of {len(in_sample)} records have a final label",
            "added": added, "gone": gone, "unlocked": unlocked, "notes": notes, "not_corpus": NOT_CORPUS,
            "areas_message": area_msg, "configs": out_rows}


def show_lines(res: dict[str, Any], by: str | None = None) -> list[str]:
    """``wider screen compare show`` in plain words: rows in the order given, no winner."""
    if by not in (None, "pool", "area"):
        raise ValueError("--by is pool or area")
    lines = [f"Comparison {res['name']} on {res['sample']}: {len(res['configs'])} configuration"
             f"{_plural(len(res['configs']), '', 's')} × {res['repeats']} repeat{_plural(res['repeats'], '', 's')}",
             res["not_corpus"], *res["notes"], res["labelled_text"]]
    if res.get("hidden"):
        return lines + [f"{c['label']}: backend {c['provider']}, model {c['model']}, prompt v{c['prompt_version']}, "
                        f"temperature {c['temperature_text']}" for c in res["configs"]]
    for c in res["configs"]:
        lines.append(f"{c['label']}: backend {c['provider']}, model {c['model']}, prompt v{c['prompt_version']}, "
                     f"temperature {c['temperature_text']}")
        if not c["repeats_done"]:
            lines.append(f"  {NOT_RUN}" + (f"; {c['failed_text']}" if c["failed"] else ""))
            continue
        lines.append(f"  misses {c['misses_text']}, false includes {c['false_includes_text']}, false maybes "
                     f"{c['false_maybes_text']}, agree {c['agree_text']} (scored {c['scored_text']}; range over "
                     f"{c['repeats_done']} repeat{_plural(c['repeats_done'], '', 's')})")
        lines.append(f"  tokens {c['prompt_tokens']:,} in, {c['completion_tokens']:,} out; cost {c['cost']}; "
                     f"{c['failed_text']}")
        cons = c["consistency"]
        if cons["measured"]:
            lines.append(f"  consistency {cons['text']}")
            lines += [f"    changed: record {x['record_id']} {x['title'] or ''}: {x['decisions_text']}"
                      for x in cons["changed"]]
        lines += [f"  {n}" for n in cons["notes"]]
        if by == "pool":
            lines += [f"    pool {b['key']}: n {b['n']}, misses {b['misses_text']}, false includes "
                      f"{b['false_includes_text']}" for b in c["by_pool"]]
        elif by == "area" and "by_area" in c:
            lines += [f"    area {b['key']}{' (' + ', '.join(b['terms'][:5]) + ')' if b.get('terms') else ''}: "
                      f"n {b['n']}, misses {b['misses_text']}, false includes {b['false_includes_text']}"
                      for b in c["by_area"]]
    if by == "area" and res["areas_message"]:
        lines.append(res["areas_message"])
    return lines


def list_comparisons(project: Project) -> list[dict[str, Any]]:
    out = []
    for r in project.store.query("SELECT * FROM screen_comparisons ORDER BY comparison_id"):
        cfgs = json.loads(r["configs"])
        out.append({"name": r["name"], "sample": r["sample"], "configs": [c["label"] for c in cfgs],
                    "repeats": r["repeats"], "records": len(json.loads(r["record_ids"] or "[]")),
                    "author": r["author"], "created_at": r["created_at"], "finished_at": r["finished_at"]})
    return out


# ------------------------------------------------------------------------------------------- outputs (D15)
NOT_FINISHED = "not finished"
NO_COMPARISON = "No configuration comparison was run."


def _all_results(project: Project) -> list[dict[str, Any]]:
    return [results(project, c["name"]) for c in list_comparisons(project)]


def report_lines(project: Project) -> list[str]:
    """The report's "Configuration comparisons" section body: per comparison its sample, configurations, repeats,
    labelled n, and misses and false includes as ranges; counts only, no record."""
    res = _all_results(project)
    if not res:
        return [NO_COMPARISON]
    if any(r.get("hidden") for r in res):
        from .devgate import REPORT_HIDDEN

        return [REPORT_HIDDEN]
    lines = [NOT_CORPUS]
    for r in res:
        fin = "" if r["finished_at"] else f" ({NOT_FINISHED})"
        lines.append(f"Comparison {r['name']} on {r['sample']}{fin}: {len(r['configs'])} configuration"
                     f"{_plural(len(r['configs']), '', 's')} × {r['repeats']} repeat{_plural(r['repeats'], '', 's')}; "
                     f"{r['labelled_text']}")
        for c in r["configs"]:
            cons = c["consistency"]
            head = (f"- {c['label']} ({c['provider']}, model {c['model']}, prompt v{c['prompt_version']}, "
                    f"temperature {c['temperature_text']})")
            if not c["repeats_done"]:
                lines.append(f"{head}: {NOT_RUN}" + (f"; {c['failed_text']}" if c["failed"] else ""))
                continue
            lines.append(f"{head}: misses {c['misses_text']}, false includes "
                         f"{c['false_includes_text']}, false maybes {c['false_maybes_text']} over {c['repeats_done']} "
                         f"repeat{_plural(c['repeats_done'], '', 's')}"
                         + (f"; consistency {cons['text']}" if cons["measured"] else "")
                         + (f"; {c['failed_text']}" if c["failed"] else ""))
    return lines


def to_json(project: Project) -> list[dict[str, Any]]:
    """``comparisons.json``: per comparison its settings and counts and its run ids; no title, abstract, response or
    per-record decision (the consistency's changed records stay out)."""
    out = []
    for r in _all_results(project):
        if r.get("hidden"):
            out.append({k: r[k] for k in ("name", "sample", "repeats", "author", "created_at", "finished_at", "records")}
                       | {"hidden": r["hidden"], "configurations": r["configs"]})
            continue
        cfgs = []
        for c in r["configs"]:
            cons = c["consistency"]
            cfgs.append({**{k: c[k] for k in ("label", "provider", "kind", "model", "prompt_version", "temperature",
                                              "temperature_sent") if k in c},
                         "repeats_done": c["repeats_done"], "labelled": c["labelled"],
                         **{k: c[k] for k in ("misses", "false_includes", "false_maybes", "agree", "scored",
                                              "screened")},
                         "failed_calls": c["failed"], "prompt_tokens": c["prompt_tokens"],
                         "completion_tokens": c["completion_tokens"], "cost_usd": c["cost_usd"],
                         "consistency": {"measured": cons["measured"], "share": cons["share"],
                                         "consistent": cons.get("consistent"), "base": cons.get("base"),
                                         "notes": cons["notes"]},
                         "run_ids": [x["run_id"] for x in c["runs"]]})
        out.append({"name": r["name"], "sample": r["sample"], "repeats": r["repeats"], "author": r["author"],
                    "created_at": r["created_at"], "finished_at": r["finished_at"], "finished": bool(r["finished_at"]),
                    "records": r["records"], "records_in_sample": r["records_in_sample"], "labelled": r["labelled"],
                    "note": NOT_CORPUS, "configurations": cfgs})
    return out


def methods_sentence(project: Project) -> str | None:
    """The methods draft's sentence: how many comparisons, on which sample, and that they describe it only."""
    cs = list_comparisons(project)
    if not cs:
        return None
    samples = list(dict.fromkeys(c["sample"] for c in cs))
    n = len(cs)
    on = (f"the purposive {samples[0]} sample" if len(samples) == 1
          else "the purposive samples " + ", ".join(samples[:-1]) + " and " + samples[-1])
    return (f"{n} configuration comparison{_plural(n, '', 's')} {_plural(n, 'was', 'were')} run on {on}; "
            f"{_plural(n, 'its', 'their')} figures describe {'that sample' if len(samples) == 1 else 'those samples'}, "
            "not the corpus.")


# ------------------------------------------------------------------------------------------- comparison.csv (D15)
def csv_path(project: Project, name: str) -> Path:
    return project.root / "runs" / f"compare_{name}" / "comparison.csv"


def write_csv(project: Project, name: str, res: dict[str, Any] | None = None) -> Path:
    """Per record of the comparison still in the sample: title, pool, area, final label and each configuration's
    decision per repeat. Written in the project folder only, outside the run folders."""
    from ..sample.labels import final_labels, sample_by_name

    res = res or results(project, name)
    row = _stored(project, name)
    ids = json.loads(row["record_ids"] or "[]")
    try:
        s = sample_by_name(project, res["sample"])
        current = {r["record_id"]: r for r in final_labels(project, res["sample"])}
    except KeyError:
        s, current = None, {}
    area_of, _terms, _msg = _areas(project, s)
    from .evaluate import calls_by_record

    cols, decisions = [], {}
    for c in res["configs"]:
        for r in c["runs"]:
            col = f"{c['label']} r{r['repeat']}"
            cols.append(col)
            decisions[col] = {rid: v["rule_decision"] for rid, v in calls_by_record(project, r["run_id"]).items()}
    path = csv_path(project, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "title", "pool", "area", "final_label", *cols])
        for rid in ids:
            if rid not in current:
                continue
            lab = current[rid]
            area = "" if area_of is None else area_of.get(rid, NOT_CLUSTERED)
            w.writerow([rid, lab["title"] or "", lab["pool"] or "", area, lab["decision"] or "",
                        *[decisions[c].get(rid, "") for c in cols]])
    return path
