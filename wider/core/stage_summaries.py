"""Pure summarisers: turn each engine's return value into a :class:`~wider.core.stage_result.StageResult`.

They never touch the store or the network, so they are easy to test and the engines stay the single place where a
result is recorded. Headlines are one sentence in plain words; metrics are the three to five numbers a researcher
checks first; warnings are the things that should make them pause before continuing.
"""
from __future__ import annotations

from typing import Any

from .stage_result import StageResult


def _n(x: Any) -> int:
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _rel(path: Any, root: Any = None) -> str:
    s = str(path)
    if root:
        r = str(root).rstrip("/") + "/"
        if s.startswith(r):
            return s[len(r):]
    return s


# ----------------------------------------------------------------------------- import
def summarize_import(results: list[dict], root: Any = None) -> StageResult:
    imported = [r for r in results if r.get("status") == "imported"]
    already = [r for r in results if r.get("status") == "already"]
    errors = [r for r in results if r.get("status") == "error"]
    n_rec = sum(_n(r.get("n")) for r in imported)
    if not results:
        head = "No export files found in inputs/<platform>/; add a file first."
    elif imported:
        head = f"Imported {_plural(len(imported), 'file')} with {_plural(n_rec, 'record')}" + \
               (f"; {len(already)} already imported" if already else "") + "."
    else:
        head = f"Nothing new: all {len(already)} file(s) were imported earlier."
    warnings = [f"{r['file']}: {r.get('error', 'could not be read')}" for r in errors]
    for r in imported:
        if r.get("mismatch"):
            warnings.append(f"{r['file']} looks like a {r.get('detected_platform')} export, not {r['platform']}")
        if r.get("n") and _n(r.get("no_abstract")) > 0.5 * _n(r.get("n")):
            warnings.append(f"{r['file']}: {r['no_abstract']} of {r['n']} records have no abstract")
    rows = [{"platform": r.get("platform"), "file": r.get("file"), "status": r.get("status"), "records": _n(r.get("n")),
             "format": r.get("format") or ""} for r in results]
    return StageResult(stage="import", action="import", ok=not errors or bool(imported), headline=head,
                       metrics={"files imported": len(imported), "records": n_rec, "already imported": len(already),
                                "errors": len(errors)}, rows=rows, warnings=warnings)


def summarize_upload(check: dict) -> StageResult:
    verdict = check.get("verdict", "ok")
    head = f"{check.get('file')}: {_plural(_n(check.get('n')), 'record')}, verdict {verdict.replace('_', ' ')}."
    warnings = [] if verdict == "ok" else [check.get("message") or verdict]
    return StageResult(stage="import", action="upload", ok=verdict not in ("empty", "off_topic"), headline=head,
                       metrics={"records": _n(check.get("n")), "benchmark hits": check.get("benchmark_hits", "–"),
                                "vocabulary overlap": (f"{int(check['vocab_overlap'] * 100 + 0.5)}%" if check.get("vocab_overlap") is not None else "–")},
                       warnings=warnings)


# ----------------------------------------------------------------------------- dedup
def summarize_dedup(out: dict) -> StageResult:
    s = out.get("summary", {})
    uniq, total = _n(s.get("unique_records")), _n(s.get("total_records_imported"))
    removed = _n(s.get("duplicate_records_removed"))
    pending = _n(out.get("pending", s.get("unresolved_manual_component_pairs_retained")))
    head = f"{uniq:,} unique records from {total:,} imported; {removed:,} duplicates merged."
    warnings = []
    uncertain = _n(s.get("uncertain_manual_component_pairs"))
    if pending:
        warnings.append(f"{pending} pairs wait for your decision in the queue (rerun after deciding).")
    if uncertain:
        warnings.append(f"{uncertain} pairs are decided uncertain; decide them as duplicate or not duplicate and rerun.")
    if pending or uncertain:
        warnings.append("The PRISMA count of records remaining is provisional until every queued pair is decided.")
    if total and uniq == total:
        warnings.append("No duplicates found: check that the exports overlap as expected.")
    by_rule = {k.replace("duplicate_records_removed_by_", ""): v for k, v in s.items() if k.startswith("duplicate_records_removed_by_")}
    rows = [{"rule": k, "records removed": v} for k, v in by_rule.items()]
    return StageResult(stage="dedup", action="run", headline=head,
                       metrics={"unique records": uniq, "duplicates merged": removed, "pending pairs": pending,
                                "manual decisions applied": _n(s.get("manually_confirmed_duplicate_pairs")) + _n(s.get("confirmed_not_duplicate_pairs"))},
                       rows=rows, warnings=warnings, outputs=["exports/dedup/"])


# ----------------------------------------------------------------------------- enrich
def summarize_enrich(stats: dict, summary: dict | None = None) -> StageResult:
    tried, filled = _n(stats.get("tried")), _n(stats.get("filled"))
    remaining = _n(stats.get("remaining_thin"))
    pending = _n((summary or {}).get("pending"))
    if not tried:
        head = "Nothing to fetch: every record already has an abstract or was tried."
    else:
        head = f"Looked up {_plural(tried, 'record')} and filled {_plural(filled, 'abstract')}; {remaining:,} thin or missing remain."
    failed = _n(stats.get("failed"))
    warnings = []
    if failed:
        warnings.append(f"{_plural(failed, 'lookup')} failed because a service could not be reached or answered with an "
                        "error; retry them with `wider enrich --retry` or \"Retry failed lookups\".")
    elif tried and filled == 0:
        warnings.append("No abstract was found: check the network connection and the enrichment sources.")
    if pending:
        warnings.append(f"{pending} records are still pending; run again to continue.")
    rows = [{"source": k, "filled": v} for k, v in (stats.get("by_source") or {}).items()]
    return StageResult(stage="enrich", action="fetch", headline=head,
                       metrics={"tried": tried, "filled": filled, "exhausted": _n(stats.get("exhausted")), "failed": failed,
                                "still thin": remaining},
                       rows=rows, warnings=warnings)


# ----------------------------------------------------------------------------- rank
def summarize_embed(res: dict, root: Any = None) -> StageResult:
    if res.get("skipped"):
        return StageResult(stage="rank", action="embed", ok=False, headline=res.get("error") or "Embedding skipped.",
                           warnings=[res.get("error") or "skipped"])
    head = f"Embedded {_n(res.get('n')):,} records with {res.get('set_name')} ({res.get('dim')} dimensions)" + \
           (" from the cache." if res.get("cached") else ".")
    return StageResult(stage="rank", action="embed", headline=head,
                       metrics={"records": _n(res.get("n")), "dimensions": res.get("dim"), "embedding": res.get("set_name"),
                                "cached": "yes" if res.get("cached") else "no"},
                       outputs=[_rel(res["file"], root)] if res.get("file") else [])


def summarize_rank(res: dict) -> StageResult:
    cells = list(res.get("cells") or [])
    head = f"Ranked {_n(res.get('n')):,} records against {_plural(len(cells), 'anchor cell')} (top-{res.get('top_n')} scoring); ranking {res.get('name')}."
    if res.get("reused"):   # ui-redesign D5: identical inputs reuse the stored version
        head += f" The inputs are unchanged, so version {res.get('version')} was reused and nothing was added."
    elif (res.get("version") or 1) > 1:
        head += f" Version {res.get('version')} was added; earlier versions are unchanged."
    return StageResult(stage="rank", action="rank", headline=head,
                       metrics={"records": _n(res.get("n")), "cells": len(cells), "top-N": res.get("top_n"),
                                "anchors": f"v{res.get('anchors_version')}", "embedding": res.get("set_name")},
                       rows=[{"cell": c} for c in cells])


# ----------------------------------------------------------------------------- sample
def summarize_samples(samples: list[dict]) -> StageResult:
    """``samples`` are rows of the samples table (name, kind, n, locked) or draw results (name, kind, n)."""
    if not samples:
        return StageResult(stage="sample", action="draw", ok=False, headline="No sample drawn yet.")
    def part(s):
        return f"{s['name']} ({s['kind']}, n={_n(s.get('n'))})"
    # a locked sample takes no more labels, so it is not counted as ready (the Sample page's count)
    ready = [s for s in samples if not s.get("locked")]
    locked = [s for s in samples if s.get("locked")]
    head = (f"{_plural(len(ready), 'sample')} ready for labelling: " + "; ".join(map(part, ready)) + "." if ready
            else "No sample is open for labelling.")
    if locked:
        head += " Locked: " + "; ".join(map(part, locked)) + "."
    metrics: dict[str, Any] = {s["name"]: _n(s.get("n")) for s in samples}
    rows = [{"sample": s["name"], "kind": s["kind"], "n": _n(s.get("n")), "locked": "yes" if s.get("locked") else "no"} for s in samples]
    return StageResult(stage="sample", action="draw", headline=head, metrics=metrics, rows=rows)


# ----------------------------------------------------------------------------- screen
def _backend_marks(res: dict) -> list[str]:
    """The mixed and experimental marks of a screening result card (Decisions 16 and 20)."""
    out = []
    if res.get("mixed") or res.get("backend_change"):
        out.append("mixed: evaluations by more than one model or backend")
    if res.get("experimental"):
        out.append(str(res["experimental"]))
    return out


def summarize_screen(res: dict, mode: str = "pass") -> StageResult:
    if mode == "protocol":
        n = _n(res.get("n"))
        runs = len(res.get("runs", []))   # passes A (and B), then the blind third read C where they disagreed
        reads = _plural(min(runs, 2), "pass", "passes") + (" and a blind third read" if runs > 2 else "")
        head = f"Protocol {res.get('protocol')}: {n:,} {'record' if n == 1 else 'records'} decided with {reads}; " \
               f"{_n(res.get('include'))} include, {_n(res.get('maybe'))} maybe, {_n(res.get('exclude'))} exclude."
        metrics = {"records": n, "include": _n(res.get("include")), "maybe": _n(res.get("maybe")),
                   "exclude": _n(res.get("exclude")), "disagreements": _n(res.get("disagreements"))}
        warnings = []
        if n and _n(res.get("maybe")) > n / 2:
            warnings.append("More than half of the decisions are maybe: the prompt may need sharper exclusion reasons.")
        warnings += _backend_marks(res)
        return StageResult(stage="screen", action="protocol", headline=head, metrics=metrics, warnings=warnings,
                           rows=[{"run": r} for r in res.get("runs", [])], usage=res.get("usage"))
    if mode == "second":
        head = f"Second screen of {res.get('protocol')}: {_n(res.get('maybes_reread'))} maybes re-read; " \
               f"{_n(res.get('include'))} include, {_n(res.get('maybe'))} maybe, {_n(res.get('exclude'))} exclude."
        return StageResult(stage="screen", action="second", headline=head, warnings=_backend_marks(res),
                           metrics={"maybes re-read": _n(res.get("maybes_reread")), "include": _n(res.get("include")),
                                    "maybe": _n(res.get("maybe")), "exclude": _n(res.get("exclude"))},
                           rows=[{"run": r} for r in res.get("runs", [])], usage=res.get("usage"))
    n, ok, err = _n(res.get("n_records")), _n(res.get("n_ok")), _n(res.get("n_err"))
    if res.get("phrase"):   # the round in words (screening-page-tasks D5); the run id stays in the rows
        where = f" of {res['phrase']}" + (f", pass {res['pass']}" if res.get("pass") else "") \
            + (" (test model)" if res.get("test") else "")
    else:
        where = f" (run {res.get('run_id')})"
    head = f"Screened {ok:,} of {_plural(n, 'record')}{where}" + (f"; {err} calls failed." if err else ".")
    warnings = [f"{err} records failed; rerun the same scope to resume them."] if err else []
    if res.get("resumed"):
        warnings.append(f"{res['resumed']} records were already done and skipped.")
    warnings += _backend_marks(res)
    return StageResult(stage="screen", action="pass", ok=err == 0 or ok > 0, headline=head, warnings=warnings,
                       metrics={"records": n, "screened": ok, "failed": err, "resumed": _n(res.get("resumed"))},
                       outputs=[_rel(res["dir"])] if res.get("dir") else [], usage=res.get("usage"),
                       rows=[{"run": res["run_id"]}] if res.get("phrase") and res.get("run_id") else [])


# ----------------------------------------------------------------------------- recall and report
def ci_label(level: Any) -> str:
    """An interval level as a percentage for labels: 0.95 -> "95%", 0.9 -> "90%", 0.975 -> "97.5%"."""
    return f"{round(float(level if level is not None else 0.95) * 100, 1):g}%"


def recall_phrase(v: dict, variant: Any, level: Any) -> str:
    """One variant's recall in a headline: "100.0% (lenient; 95% interval 0.43 to 1.00; 10 relevant validation
    papers)" (design D5 of recall-interval-honest); the estimate and the report share it."""
    from ..recall.uncertainty import interval_words, papers_text

    return (f"{v['recall']:.1%} ({variant}; {interval_words({**v, 'level': level})}; "
            f"{papers_text(v.get('n_relevant') or 0)})")


def summarize_recall(est: dict) -> StageResult:
    from ..recall.uncertainty import interval_text

    variants = est.get("variants", {})
    primary = est.get("primary_variant") or ("lenient" if "lenient" in variants else next(iter(variants), None))
    v = variants.get(primary) or {}
    r = v.get("recall")
    name = est.get("protocol")
    if est.get("result_set"):
        name = f"{name} set {est['result_set']}" + (f" ({est['result_set_label']})" if est.get("result_set_label") else "")
    if r is None:
        head = (f"Recall could not be estimated for {name} on {est.get('sample')}: the labelled sample holds no relevant "
                "validation papers.")
        ok = False
    else:
        head = f"Estimated recall of {name} on {est.get('sample')}: {recall_phrase(v, primary, est.get('ci_level'))}."
        ok = True
    metrics: dict[str, Any] = {"labelled records": _n(est.get("n_labelled"))}
    for k, vv in variants.items():
        metrics[f"{k} recall"] = f"{vv['recall']:.3f}" if vv.get("recall") is not None else "–"
    warnings = []
    if _n(est.get("unscreened_in_sample")):
        warnings.append(f"{est['unscreened_in_sample']} labelled records were not screened by this protocol.")
    if v.get("n_missed"):
        warnings.append(f"{v['n_missed']} relevant record{' was' if v['n_missed'] == 1 else 's were'} missed ({primary}); "
                        "see the missed list in the report.")
    if est.get("role_note"):
        warnings.append(f"Set {est.get('result_set')}: {est['role_note']}.")
    if _n(est.get("B_requested")) and est.get("B") and est["B_requested"] > est["B"]:
        warnings.append(f"Bootstrap replicates capped at {est['B']:,} ({est['B_requested']:,} were asked for); the "
                        "cross-check interval in the report uses the capped number.")
    rows = [{"variant": k, "recall": f"{vv['recall']:.3f}" if vv.get("recall") is not None else "–",
             f"{ci_label(est.get('ci_level'))} interval": interval_text(vv) if "interval" in vv else "–",
             "relevant": vv.get("n_relevant"),
             "missed": vv.get("n_missed")}
            for k, vv in variants.items()]
    return StageResult(stage="recall", action="estimate", ok=ok, headline=head, metrics=metrics, rows=rows, warnings=warnings)


def summarize_report(rep: dict, paths: tuple[Any, Any] | None = None, root: Any = None) -> StageResult:
    rec = rep.get("recall") or {}
    uniq = (rep.get("dedup") or {}).get("unique_records")
    if rec:
        vs = rec.get("variants", {})
        prim = rec.get("primary_variant") if rec.get("primary_variant") in vs else ("lenient" if "lenient" in vs else next(iter(vs), None))
        pv = rec.get("variants", {}).get(prim) or {}
        if pv.get("recall") is None:
            tail = ""
        elif "interval" in pv:
            tail = f"; recall {recall_phrase(pv, prim, rec.get('ci_level'))}"
        else:  # a report.json written before the interval was added
            tail = f"; recall {pv['recall']:.1%} ({prim})"
    else:
        tail = "; no recall estimate yet"
    head = f"Report written for {rep.get('title') or rep.get('project')}{tail}."
    protos = rep.get("screening", {}).get("protocols", [])
    metrics: dict[str, Any] = {"unique records": uniq if uniq is not None else "–",
                               "screening runs": len(rep.get("screening", {}).get("runs", [])),
                               "protocols": len(protos),
                               "benchmark with abstract": f"{_n(rep.get('benchmark', {}).get('with_abstract'))}/{_n(rep.get('benchmark', {}).get('n'))}"}
    warnings = [] if rec else ["The report has no recall estimate: label the probability sample and run a protocol first."]
    if rec.get("choice_needed"):   # D8: no estimate until the researcher chooses
        warnings = list(rec.get("lines") or [])
    if rec.get("labels_incomplete"):   # label-completion-and-sealing D4: no estimate until every record is final
        warnings = [rec["notice"]]
    outputs = [_rel(p, root) for p in (paths or [])]
    return StageResult(stage="recall", action="report", headline=head, metrics=metrics, warnings=warnings, outputs=outputs)


def summarize_bundle(path: Any, root: Any = None) -> StageResult:
    return StageResult(stage="recall", action="bundle", headline=f"Share bundle written to {_rel(path, root)} (no abstracts, no raw responses).",
                       outputs=[_rel(path, root)])


# ----------------------------------------------------------------------------- construct and query
def summarize_artifact(row: dict, action: str) -> StageResult:
    """A construct artifact was saved, approved or frozen. Synonyms frozen or imported belong to the query stage."""
    kind, version, status = row.get("kind"), row.get("version"), row.get("status")
    stage = "query" if kind == "synonyms" and (status == "frozen" or row.get("origin") == "import") else "construct"
    verbs = {"save": "saved", "propose": "proposed by the model and saved as a draft", "status": f"is now {status}", "import": "imported"}
    head = f"{kind} v{version} {verbs.get(action, action)}."
    if action == "status" and status == "frozen":
        head += " The validation seed papers are now reported in coverage."
    if row.get("accepted_line"):   # a freeze of a round never accepted accepts it (D8)
        head += f" {row['accepted_line']}."
    return StageResult(stage=stage, action=f"{action}:{kind}", headline=head,
                       metrics={"artifact": kind, "version": version, "status": status, "origin": row.get("origin") or ""},
                       outputs=[row["path"]] if row.get("path") else [])


# ----------------------------------------------------------------------------- prior reviews
def summarize_chain(res: dict) -> StageResult:
    """Citation chaining (prior reviews → OpenAlex → import file)."""
    n_new, n_exp = _n(res.get("n_new")), _n(res.get("n_exported"))
    if res.get("file"):
        head = (f"Chained {_plural(_n(res.get('papers_chained')), 'prior-review paper')}: {n_new:,} new works fetched, "
                f"{n_exp:,} written to {res['file']}.")
    else:
        head = f"Chained {_plural(_n(res.get('papers_chained')), 'prior-review paper')}: {n_new:,} new works, nothing new to export."
    return StageResult(stage="setup", action="chain", headline=head,
                       metrics={"papers chained": _n(res.get("papers_chained")), "references": _n(res.get("n_backward")),
                                "citing works": _n(res.get("n_forward")), "exported": n_exp},
                       warnings=list(res.get("warnings") or []), outputs=[res["file"]] if res.get("file") else [],
                       next_stage="import" if res.get("file") else None)
