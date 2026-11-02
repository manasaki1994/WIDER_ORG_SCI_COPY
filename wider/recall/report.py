"""Project report: every number of the review in one JSON + Markdown, generated from the store (nothing typed by hand).
Also a share bundle: the report, artifacts, run specs and decisions without abstracts or raw exports."""
from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

from ..construct import artifacts
from ..core.project import Project
from ..core.provenance import local_time, utcnow
from ..core.researchers import PLACEHOLDERS
from ..core.stage_summaries import ci_label
from ..dedup.engine import prisma_rows
from ..sample.design import sampling_log
from ..sample.labels import progress
from ..screen.decisions import protocols
from ..search.coverage import (
    development_papers,
    query_results_papers,
    used_papers,
    validation_papers,
    validity_lines,
    validity_note,
)
from .adjacent import adjacent_markdown, adjacent_section
from .estimate import check_names, estimate
from .uncertainty import METHOD_SENTENCE, interval_text, method_text

# what report.json keeps of each recall variant: the estimate, the primary interval with its method and plain lines,
# and the bootstrap and linearised intervals as cross-checks (design D5 of recall-interval-honest)
REPORT_VARIANT_KEYS = ("recall", "interval", "interval_method", "n_effective", "df", "interval_reason", "lines",
                       "ci_bootstrap", "ci_linearised", "n_relevant", "n_missed")


def build_report(project: Project, protocol: str | None = None, sample: str | None = None, B: int | None = None,
                 result_set: int | None = None, choice: str | None = None) -> dict:
    """Every number of the review. A protocol or sample named explicitly must exist (ValueError otherwise).
    ``result_set``: the set of ``protocol`` the recall section reads (default its active set). A set that needs the
    choice of D8 gets the pending section ``{"choice_needed": true, "notice": ...}`` unless ``choice`` is given."""
    from .validation import LabelsIncomplete, SampleChoiceNeeded, pending_line, validation_checks
    if result_set is not None and not protocol:
        raise ValueError("--set needs a protocol")
    check_names(project, sample or None, protocol or None, result_set)
    st = project.store
    cfg = project.config
    B = B or cfg.protocol.recall.bootstrap_b
    rep: dict = {"project": cfg.name, "title": cfg.title, "generated_at": utcnow(), "wider_version": cfg.wider_version,
                 "questions": cfg.questions, "platforms": [p.model_dump() for p in cfg.platforms],
                 "protocol_hash": cfg.protocol.hash(), "protocol_changes": [r for r in cfg.protocol.explain() if r["changed"]]}
    rep["artifacts"] = {k: [{"version": r["version"], "status": r["status"], "origin": r["origin"]} for r in artifacts.list_versions(project, k)]
                        for k in artifacts.KINDS}
    rep["benchmark"] = {"n": st.scalar("SELECT COUNT(*) FROM benchmark_papers"),
                        # the sets of D3: query-results papers in neither; used validation papers apart (D1 of
                        # validation-reuse)
                        "development": len(development_papers(project)), "validation": len(validation_papers(project)),
                        "used": len(used_papers(project)), "not_counted": len(query_results_papers(project)),
                        "with_abstract": st.scalar("SELECT COUNT(*) FROM benchmark_papers WHERE COALESCE(abstract_words,0)>=?", (cfg.protocol.corpus.thin_words,))}
    rep["sources"] = [dict(r) for r in st.query("SELECT platform, COUNT(*) files, SUM(n_records) records FROM sources GROUP BY platform")]
    rep["dedup"] = json.loads(st.get_meta("dedup_summary") or "{}")
    rep["prisma"] = prisma_rows(project) if rep["dedup"] else []
    rep["coverage"] = _coverage_by_set(project)
    if project.read_benchmark():   # whether the search is validated on a held-out set (D3), the note every output shares
        note = validity_note(project)
        rep["search_validity"] = {**note, "text": validity_lines(note)}
        # recall on independent papers and the estimate (D25)
        from .independent import check as independent_check
        rep["independent_check"] = independent_check(project, protocol, result_set)
    else:
        rep["search_validity"] = rep["independent_check"] = None
    rep["prior_reviews"] = {"n_reviews": st.scalar("SELECT COUNT(*) FROM prior_reviews"),
                            "n_papers": st.scalar("SELECT COUNT(*) FROM prior_review_papers"), "coverage": {}}
    for r in st.query("SELECT scope, COUNT(*) n, SUM(method!='none') found FROM prior_review_matches GROUP BY scope"):
        rep["prior_reviews"]["coverage"][r["scope"]] = {"n": r["n"], "found": r["found"]}
    rep["enrichment"] = {"thin": st.scalar("SELECT COUNT(*) FROM records WHERE COALESCE(abstract_words,0) < ?", (cfg.protocol.corpus.thin_words,)),
                         "filled": st.scalar("SELECT COUNT(*) FROM enrichment_log WHERE status='filled'"),
                         "records": st.scalar("SELECT COUNT(*) FROM records")}
    rep["embeddings"] = [dict(r) for r in st.query("SELECT set_name, model, dim, n FROM embedding_sets")]
    rep["rankings"] = [dict(r) for r in st.query("SELECT name, set_name, anchors_version, top_n, n, md5 FROM rankings")]
    try:
        from ..rank.score import validate
        rep["ranking_validation"] = {k: v for k, v in validate(project).items() if k in ("ranking", "summary", "top_n_compare", "validation_included")}
    except (KeyError, ValueError):
        rep["ranking_validation"] = None
    rep["samples"] = {k: {**v, "progress": progress(project, k)} for k, v in sampling_log(project).items()}
    rep["screening"] = {"runs": [dict(r) for r in st.query("SELECT run_id, kind, status, provider, model, prompt_version, n_items, n_ok FROM runs")],
                        "protocols": protocols(project)}
    from ..screen import compare
    rep["comparisons"] = compare.report_lines(project)   # counts only, never a record (D15)
    from ..screen.devgate import open_samples

    # ui-redesign D4: written while a development sample is open, so no comparison figure on it (the file is served)
    rep["comparisons_hidden"] = bool(open_samples(project))
    rep["adjacent"] = adjacent_section(project)
    if rep["prisma"]:
        rep["prisma"] = rep["prisma"] + rep["adjacent"]["prisma_rows"]
    rep["recall"] = None
    # defaults: the most recently screened protocol (protocols() is ordered by the last decision) and the last
    # labelled probability sample, else the first probability sample
    protos = [p["protocol"] for p in rep["screening"]["protocols"]]
    explicit = bool(protocol or sample)
    protocol = protocol or (protos[-1] if protos else None)
    labelled = [k for k, v in rep["samples"].items() if v["kind"] == "probability" and v["progress"]["reviewers"]]
    use = cfg.protocol.sampling.probability_sample   # the sample in use (D9), else as before
    if not sample and rep["samples"].get(use, {}).get("kind") == "probability":
        sample = use
    sample = sample or (labelled[-1] if labelled else next((k for k, v in rep["samples"].items() if v["kind"] == "probability"), None))
    if protocol and sample:
        try:
            r = estimate(project, sample, protocol, B=B, record_result=False, result_set=result_set, choice=choice)
            rep["recall"] = {"sample": sample, "protocol": protocol, "n_labelled": r["n_labelled"],
                             "result_set": r["result_set"], "result_set_key": r["result_set_key"],
                             "result_set_label": r["result_set_label"], "result_set_role": r["result_set_role"],
                             "role_note": r["role_note"], "models": r["models"], "run_ids": r["run_ids"],
                             "primary_variant": r["primary_variant"], "ci_level": r["ci_level"],
                             "B": r["B"], "B_requested": r.get("B_requested"), "seed": r["seed"], "lines": r["lines"],
                             "method_note": r.get("method_note"), "open_disagreements": r.get("open_disagreements"),
                             "labels_used": r.get("labels_used"),
                             "validation_lines": r.get("validation_lines", []), "outcome": r.get("outcome"),
                             "bounds": r.get("bounds"), "benchmark_apart": r.get("benchmark_apart"),
                             "used_lines": r.get("used_lines", []), "scope_line": r.get("scope_line"),
                             "validation_checks": validation_checks(project, sample, r),
                             "variants": {k: {kk: vv for kk, vv in v.items() if kk in REPORT_VARIANT_KEYS}
                                          for k, v in r["variants"].items()}, "bands": r["bands"]}
        except LabelsIncomplete as e:   # the files are still written, the recall section pending (D4)
            rep["recall"] = {"sample": sample, "protocol": protocol, "labels_incomplete": True, "notice": e.notice,
                             "lines": []}
        except SampleChoiceNeeded as e:   # the files are still written, with the pending section (D8)
            from ..screen.result_sets import split_key

            rep["recall"] = {"sample": sample, "protocol": protocol, "choice_needed": True, "notice": e.notice,
                             "result_set_key": e.key, "result_set": split_key(e.key)[1],
                             "lines": [pending_line(sample, split_key(e.key)[1])]}
        except (KeyError, ValueError):
            if explicit:
                raise
            rep["recall"] = None
    rep["agreement"] = _agreement(project, protocol, result_set)
    from ..sample.package import report_section
    rep["blinded_review"] = report_section(project)   # reviewer-roles-and-blinding D11; names attributed on writing
    rep["interventions"] = interventions(project)
    return rep


def _agreement(project: Project, protocol: str | None, result_set: int | None) -> dict | None:
    """The agreement of the report's set (the dashboard's, results-dashboard-and-ris D2); None before screening."""
    from ..screen.agreement import pass_agreement
    from ..screen.result_sets import reader
    from .outputs import set_name

    if not protocol:
        return None
    try:
        info = reader(project, protocol, result_set)
    except (KeyError, ValueError):
        return None
    if not project.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (info["key"],)):
        return None
    return {**pass_agreement(project, info["key"]), "set_name": set_name(info)}


# History kinds counted per kind but never listed (D8): a label's or an adjudication's summary names the record and
# its decision, notes and tags are free text about one record, and the per-record kinds would bury the rest
COUNTED_ONLY = frozenset({"label", "adjudication", "note", "tag", "dedup_decision", "cluster_mark", "seed_indexing",
                          "stream_label", "name_admit", "label_correction", "label_provenance"})
NO_INTERVENTION = "No researcher intervention recorded."


def _target_kind(rows: dict[int, dict], r: dict) -> str:
    """The kind an undo finally acts on: its ``undo_of`` chain followed to the first entry that is not an undo (a
    redo undoes an undo)."""
    seen = 0
    while r["kind"] == "undo" and r.get("undo_of") in rows and seen < 1000:
        r, seen = rows[r["undo_of"]], seen + 1
    return r["kind"]


PACKAGE_KINDS = ("review_package", "package_import")
PACKAGE_SUMMARY = re.compile(r"Review package made for (.+) \(\d+ records\)$|^(?:(?:undo|redo): )*(.+)'s decisions imported from")


NOT_INTERVENTION = {"report_text"}


def interventions(project: Project) -> dict:
    """What the researchers did by hand, from ``history`` (D8), read in one query: ``by_kind`` (count, authors, first
    and last time per kind) and ``entries`` (every entry outside :data:`COUNTED_ONLY` and the undos of those kinds;
    undone ones marked while :func:`~wider.core.journal.undone_now` says so). Entries authored ``migration``, and
    reporting text with its undos (:data:`NOT_INTERVENTION`), are left out of both; items are never read; summaries
    have home paths written from ``~``."""
    from ..core.journal import KIND_LABELS, undone_now
    from ..core.sanitize import portable_text

    st = project.store
    rows = {r["change_id"]: dict(r) for r in st.query(
        "SELECT change_id, at, author, kind, summary, undo_of, undone_by FROM history "
        "WHERE COALESCE(author,'')<>'migration' ORDER BY change_id")}
    # writing the paper is not an intervention in the screening (prisma-reporting-assistant D11): reporting saves
    # and their undos are neither counted nor listed
    rows = {i: r for i, r in rows.items() if NOT_INTERVENTION.isdisjoint({r["kind"], _target_kind(rows, r)})}
    by_kind: dict[str, dict] = {}
    groups: dict[tuple, list[dict]] = {}
    for r in rows.values():
        groups.setdefault((r["kind"], r["author"] or ""), []).append(r)
    for (kind, author), rs in sorted(groups.items()):
        first, last = min(r["at"] for r in rs), max(r["at"] for r in rs)
        k = by_kind.setdefault(kind, {"kind": kind, "label": KIND_LABELS.get(kind, kind), "count": 0, "authors": [],
                                      "first": first, "last": last})
        k["count"] += len(rs)
        k["authors"] += [author] if author else []
        k["first"], k["last"] = min(k["first"], first), max(k["last"], last)
    entries = []
    for r in rows.values():
        if r["kind"] in COUNTED_ONLY or _target_kind(rows, r) in COUNTED_ONLY:
            continue
        summary = str(r["summary"] or "")
        label = KIND_LABELS.get(r["kind"], r["kind"])
        if r["kind"] == "artifact_unlock" and summary.startswith("uploads reopened:"):
            label = "uploads reopened"
        e = {"change_id": r["change_id"], "at": r["at"], "author": r["author"], "kind": r["kind"],
             "kind_label": label, "summary": portable_text(summary), "undone": bool(undone_now(project, r))}
        m = PACKAGE_SUMMARY.search(summary) if _target_kind(rows, r) in PACKAGE_KINDS or r["kind"] in PACKAGE_KINDS else None
        if m:   # the package's reviewer, so a short name in the summary is written by the attribution too
            e["reviewer"] = m.group(1) or m.group(2)
        entries.append(e)
    return {"by_kind": list(by_kind.values()), "entries": entries}


def _when(at: str | None) -> str:
    return local_time(at)


def interventions_markdown(iv: dict | None) -> list[str]:
    L = ["## Researcher interventions", ""]
    iv = iv or {"by_kind": [], "entries": []}
    if iv["by_kind"]:
        L += ["Entries in History per kind (labels, adjudications, notes, tags and per-record changes are counted "
              "here only):", ""]
        L += [f"- {k['label']}: {k['count']} {'entry' if k['count'] == 1 else 'entries'}; "
              f"{', '.join(k['authors']) or 'no author recorded'}; {_when(k['first'])} to {_when(k['last'])}"
              for k in iv["by_kind"]] + [""]
    if not iv["entries"]:
        return L + [NO_INTERVENTION, ""]
    L += ["| date | author | kind | change |", "|---|---|---|---|"]
    for e in iv["entries"]:
        text = e["summary"].replace("|", "\\|").replace("\n", " ") + (" (undone)" if e["undone"] else "")
        L.append(f"| {_when(e['at'])} | {e['author'] or ''} | {e['kind_label']} | {text} |")
    return L + [""]


def agreement_markdown(ag: dict | None) -> list[str]:
    from ..screen.agreement import markdown

    if not ag:
        return []
    return [f"## Screening agreement ({ag['set_name']})", "", *markdown(ag), ""]


def _coverage_by_set(project: Project) -> dict:
    """Benchmark recall per scope and set from the last coverage check: found of total with the exact 95% interval
    (D7). The sets are D3's; the validation set only while it is shown, the used validation papers apart ("Validation,
    used", not independent), and the query-results papers, counted in neither, are left out."""
    from ..search.coverage import USED, set_members, validation_unlocked
    from ..search.diagnostics import clopper_pearson, recall_text

    shown = validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
    group = {b.benchmark_id: g for g, b in set_members(project, shown) if g in ("Development", "Validation", USED)}
    tally: dict[str, dict[str, list[int]]] = {}
    for r in project.store.query("SELECT benchmark_id, scope, method FROM benchmark_matches ORDER BY scope"):
        g = group.get(r["benchmark_id"])
        if g:
            t = tally.setdefault(r["scope"], {}).setdefault(g, [0, 0])
            t[0] += r["method"] != "none"
            t[1] += 1
    out: dict = {}
    for sc, sets in tally.items():
        for g in ("Development", "Validation", USED):
            if g in sets:
                k, n = sets[g]
                ci = clopper_pearson(k, n)
                out.setdefault(sc, {})[g] = {"found": k, "n": n, "interval": list(ci) if ci else None,
                                             "text": recall_text(k, n)}
    return out


def to_markdown(rep: dict) -> str:
    L = [f"# {rep['title'] or rep['project']}", "", f"Generated {rep['generated_at']} by WIDER {rep['wider_version']}.", ""]
    if rep["questions"]:
        L += ["## Question", ""] + [f"- {q}" for q in rep["questions"]] + [""]
    L += ["## Identification", "", "| stage | n |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rep["prisma"]] + [""]
    if rep["coverage"]:
        from ..search.diagnostics import KNOWN_PAPERS_NOTE
        L += ["## Seed paper coverage", "", "| scope | set | found |", "|---|---|---|"] + [
            f"| {sc} | {g} | {v['text']} |" for sc, sets in rep["coverage"].items() for g, v in sets.items()] + [
            "", KNOWN_PAPERS_NOTE, ""]
    sv = rep.get("search_validity")
    if sv:
        L += ["## Search validity", ""] + [x for t in sv["text"] for x in (t, "")]
    ic = rep.get("independent_check")
    if ic and ic["lines"]:
        L += ["## Independent check", ""] + [x for t in ic["lines"] for x in (t, "")]
    pr = rep.get("prior_reviews") or {}
    if pr.get("coverage"):
        L += [f"## Prior-review coverage ({pr['n_papers']} studies from {pr['n_reviews']} prior review(s); descriptive, not used to tune the query)", "",
              "| scope | found |", "|---|---|"] + [f"| {k} | {v['found']}/{v['n']} |" for k, v in pr["coverage"].items()] + [""]
    e = rep["enrichment"]
    L += ["## Abstracts", "", f"{e['records']} unique records; {e['filled']} abstracts recovered; {e['thin']} still thin.", ""]
    rv = rep.get("ranking_validation")
    if rv and rv.get("summary"):
        L += ["## Ranking", "", f"Ranking `{rv['ranking']}`.", "", "| set | found | median rank | recall@250 |", "|---|---|---|---|"]
        L += [f"| {s} | {v['found']}/{v['n']} | {v['median_rank']} | {v['recall_at_250']} |" for s, v in rv["summary"].items()] + [""]
    for name, s in rep["samples"].items():
        L += [f"## Sample: {name}", "", f"{s['kind']}, n = {s['n']}, {'locked' if s['locked'] else 'open'}."]
        if s.get("bands"):
            L += ["", "| band | N_h | n_h | weight |", "|---|---|---|---|"] + [
                f"| {b.get('band')} | {b.get('N_h', '')} | {b.get('n_h', b.get('n_rows', ''))} | {round(b['weight'], 2) if b.get('weight') else ''} |" for b in s["bands"]]
        L.append("")
    if rep["screening"]["protocols"]:
        L += ["## Screening", "", "| protocol | n | include | maybe | exclude |", "|---|---|---|---|---|"]
        L += [f"| {p['protocol']} | {p['n']} | {p['include']} | {p['maybe']} | {p['exclude']} |" for p in rep["screening"]["protocols"]] + [""]
    L += agreement_markdown(rep.get("agreement"))
    L += blinded_markdown(rep.get("blinded_review"))
    if rep.get("comparisons"):
        L += ["## Configuration comparisons", ""]
        for line in rep["comparisons"]:
            L += [line] if line.startswith("- ") else ([""] if L[-1] else []) + [line, ""]
        L += [""] if L[-1] else []
    L += adjacent_markdown(rep.get("adjacent"))
    r = rep.get("recall")
    if r and r.get("labels_incomplete"):
        L += [f"## Recall ({r['protocol']} vs {r['sample']})", "", r["notice"], ""]
    elif r and r.get("choice_needed"):
        L += [f"## Recall ({r['protocol']} set {r['result_set']} vs {r['sample']})", "", *r["lines"], "", r["notice"], ""]
    elif r:
        lv = ci_label(r.get("ci_level"))
        prim = f" Primary variant: {r['primary_variant']}." if r.get("primary_variant") else ""
        rs = f" set {r['result_set']}" if (r.get("result_set") or 1) > 1 else ""   # set 1 is the protocol's name
        note = f" {r['role_note'].capitalize()}." if r.get("role_note") else ""
        by = f"; models: {', '.join(r['models'])}" if r.get("models") else ""
        L += [f"## Recall ({r['protocol']}{rs} vs {r['sample']}, {r['n_labelled']} labelled records{by})", "",
              f"Intervals at the {lv} level.{prim}{note}", ""]
        from .validation import CONSERVATIVE, CONSERVATIVE_OLD  # said once, in "Validation checks" (D3)
        L += [x for line in r.get("lines") or [] if line not in (CONSERVATIVE, CONSERVATIVE_OLD) for x in (line, "")]
        L += [f"| variant | recall | {lv} interval | relevant papers | missed |", "|---|---|---|---|---|"]
        for k, v in r["variants"].items():
            rec = f"{v['recall']:.3f}" if v["recall"] is not None else "-"
            L.append(f"| {k} | {rec} | {interval_text(v) if 'interval' in v else '-'} | {v['n_relevant']} | {v['n_missed']} |")
        L += ["", *recall_method_markdown(r)]
    L += interventions_markdown(rep.get("interventions"))
    return "\n".join(L)


def blinded_markdown(br: dict | None) -> list[str]:
    """The "Blinded independent review" section (D11), or the one-reviewer sentence, or nothing."""
    from ..sample.package import ONE_REVIEWER
    if not br:
        return []
    if br.get("one_reviewer"):
        return [ONE_REVIEWER, ""]
    if not br.get("packages"):
        return []
    return ["## Blinded independent review", ""] + [x for line in br["lines"] for x in (line, "")]


def recall_method_markdown(r: dict) -> list[str]:
    """The technical part of the Recall section: how the primary interval was computed, per variant, and the
    bootstrap and linearised intervals as cross-checks (design D5)."""
    lv = ci_label(r.get("ci_level"))
    ci = lambda c: f"{c[0]:.2f}–{c[1]:.2f}" if c else "-"  # noqa: E731
    L = ["### Recall: method", "",
         "The recall estimate is the stratified Horvitz–Thompson ratio (band weights N_h/n_h). " + METHOD_SENTENCE + " "
         "When recall lies strictly between 0 and 1, the effective number is the Korn–Graubard one: recall × (1 − recall) "
         "divided by the stratified linearised variance (finite-population factor included), times a degrees-of-freedom "
         "factor (df = relevant papers − bands holding one) (Korn and Graubard (1998), Survey Methodology 24(2), "
         "193–201). It is never more than Kish's effective sample size (Σw)²/Σw² of the relevant papers' weights (Kish "
         "1965), and at recall 0 or 1, where that variance is zero, it is that Kish size itself; so one more missed paper "
         "never narrows the interval, unequal weights widen it and equal weights give the plain Clopper–Pearson interval. "
         "The NCHS standard (Parker et al. 2017, Vital Health Stat 2(175)) instead caps at the raw count and uses it at 0 "
         "or 1; WIDER departs from it on purpose, so that unequal weights also widen the interval at perfect observed "
         "recall. The interval is not an exact or validated confidence interval for every design: in a simulation study "
         "of WIDER's sampling (24 Oct 2026, scripts/recall_interval_sim.py: rare relevant papers, weights up to 1,000, "
         "bands of five labelled records, true recall 0, 0.06, 0.94 and 1) it held the true recall in 95% to 100% of "
         "the samples in which it is shown (the cap at the raw count gave 95% to 96% there, the cap at Kish's size "
         "99% to 100%). It fails only when a sampled band shows no relevant paper while hiding missed ones (coverage "
         "19% in that scenario), so no interval is given then: what such a band hides is unknown, and the per-band "
         "table's rule-of-three note bounds its relevant rate. No interval is given either when the variance cannot be "
         "estimated (a sampled band with a relevant paper and a single labelled record, or df below 1). The bootstrap "
         "and linearised intervals are cross-checks only: both shrink to a point at recall 0 or 1.", ""]
    vc = r.get("validation_checks")
    if vc:
        # the change line of D6 is said once, among the recall lines above
        L += ["Validation checks:", "", *[f"- {x}" for x in vc["lines"] if not (x in r["lines"] and x.endswith(
            f"(wider label revisions {r.get('sample')}).") and "changed after its recall was first revealed" in x)], ""]
    L += [f"| variant | interval method | bootstrap {lv} interval (cross-check, B {r.get('B')}, seed {r.get('seed')}) "
         f"| linearised {lv} interval (cross-check) |", "|---|---|---|---|"]
    for k, v in r["variants"].items():
        m = method_text({"method": v.get("interval_method"), "n_effective": v.get("n_effective"), "df": v.get("df")})
        if not v.get("interval_method") and v.get("interval_reason"):
            m = f"none ({v['interval_reason']})"
        L.append(f"| {k} | {m} | {ci(v.get('ci_bootstrap'))} | {ci(v.get('ci_linearised'))} |")
    return L + [""]


def write_report(project: Project, record_result: bool = True, **kw) -> tuple[Path, Path]:
    """Build the report once and write report.md and report.json; record one ``recall``/``report`` stage result
    unless ``record_result`` is False (the share bundle writing its own copy)."""
    from ..core.researchers import attribute_report

    rep = build_report(project, **kw)
    out = project.path("exports")
    out.mkdir(exist_ok=True)
    shared = attribute_report(project, rep)   # names as exports.attribution says (researcher-attribution D5)
    if (rep.get("blinded_review") or {}).get("packages"):   # its lines name reviewers of any length: written by the setting
        from ..core.researchers import Attributor
        from ..sample.package import report_section
        att = Attributor(project)   # reviewers through who, and every line (a withdraw reason) through text (risk R1)
        shared["blinded_review"] = att.obj(report_section(project, att.who), all_text=True)
    (out / "report.json").write_text(json.dumps(shared, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(to_markdown(shared), encoding="utf-8")
    paths = (out / "report.md", out / "report.json")
    rec = rep.get("recall") or {}
    project.store.set_meta("last_report", json.dumps({"at": rep["generated_at"], "protocol": rec.get("protocol"),
                                                      "result_set": rec.get("result_set"),
                                                      "sample": rec.get("sample"), "path": str(paths[0].relative_to(project.root)),
                                                      "choice_needed": bool(rec.get("choice_needed")),
                                                      "labels_incomplete": bool(rec.get("labels_incomplete")),
                                                      "notice": rec.get("notice")}))
    if record_result:
        from ..core.stage_result import record
        from ..core.stage_summaries import summarize_report
        record(project, summarize_report(rep, paths, project.root))
    return paths


def _copy_columns(src: Path, dst: Path, keep: tuple[str, ...] | None = None, drop: tuple[str, ...] = ()) -> None:
    """Copy a CSV keeping the columns named in ``keep`` (in file order), or all but those named in ``drop``."""
    with open(src, newline="", encoding="utf-8") as fi, open(dst, "w", newline="", encoding="utf-8") as fo:
        r = csv.reader(fi)
        w = csv.writer(fo)
        header = next(r, None)
        if header is None:
            return
        idx = [i for i, h in enumerate(header) if (h in keep if keep is not None else h not in drop)]
        w.writerow([header[i] for i in idx])
        for row in r:
            w.writerow([row[i] if i < len(row) else "" for i in idx])


def _write_rows(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(list(r))


LABEL_COLUMNS = ["sample", "record_id", "band", "weight", "pool", "reviewer", "decision", "reason", "labels",
                 "made_in", "blinded", "left_out", "abstract_version"]   # the last four: reviewer-roles-and-blinding D11


def _source(source: str | None, att) -> str:
    """A reference's source is "adjudication" or the reviewer whose label it is; a reviewer is named as the
    attribution says."""
    if not source or source == "adjudication":
        return source or ""
    return att.who(source)


def _write_reference(project: Project, dest: Path, att) -> None:
    """``reference_labels.csv`` (the sealed reference of each record) and ``label_corrections.csv`` (each correction
    on a locked or sealed sample), each only when it has a row; no reason, note or abstract
    (label-completion-and-sealing D12)."""
    st = project.store
    seals = [[r["name"], r["record_id"], r["decision"] or "", _source(r["source"], att), r["sealed_at"],
              "yes" if r["corrected_at"] else "no"]
             for r in st.query("SELECT s.name, ls.* FROM label_seals ls JOIN samples s ON s.sample_id=ls.sample_id "
                               "ORDER BY s.name, ls.record_id")]
    if seals:
        _write_rows(dest / "reference_labels.csv",
                    ["sample", "record_id", "decision", "source", "sealed_at", "corrected"], seals)
    cors = [[r["name"], r["record_id"], r["target"], r["old_decision"] or "", r["new_decision"] or "", r["at"],
             att.who(r["author"])]
            for r in st.query("SELECT s.name, c.* FROM label_corrections c JOIN samples s ON s.sample_id=c.sample_id "
                              "ORDER BY c.correction_id")]
    if cors:
        _write_rows(dest / "label_corrections.csv",
                    ["sample", "record_id", "target", "old_decision", "new_decision", "at", "by"], cors)


def _ticks(raw: str | None) -> str:
    """The ``labels`` cell: the criterion ticks as "criterion=answer; ...", empty when none was ticked (not "{}")."""
    try:
        ticks = json.loads(raw or "{}")
    except ValueError:
        return raw or ""
    if not isinstance(ticks, dict):
        return str(ticks)
    return "; ".join(f"{k}={v}" for k, v in ticks.items())


def _write_labels(project: Project, path: Path, att) -> None:
    """``labels.csv`` of the bundle: each reviewer as the attribution says; in ``none`` mode the column is ``reader``,
    the 1-based order of each reader's first label in that sample (one number per joined researcher); imported labels
    stay "imported labels"."""
    from ..sample.package import label_blinded, left_out, repeats
    reps, out_ids = repeats(project), left_out(project)
    rows = []
    for r in project.store.query(
            "SELECT s.name, sr.record_id, sr.band, sr.weight, sr.pool, l.reviewer_id, l.decision, l.reason, l.labels, "
            "l.label_id, l.made_in, l.abstract_version, l.sample_id FROM sample_rows sr JOIN samples s "
            "ON s.sample_id=sr.sample_id LEFT JOIN labels l ON l.sample_id=sr.sample_id AND l.record_id=sr.record_id"):
        how = ([r["made_in"], label_blinded(project, {"made_in": r["made_in"], "sample_id": r["sample_id"],
                                                      "record_id": r["record_id"]}, reps),
                "yes" if r["label_id"] in out_ids else "no", r["abstract_version"] if r["abstract_version"] is not None else ""]
               if r["label_id"] is not None else ["", "", "", ""])
        rows.append([*list(r)[:8], _ticks(r["labels"]), *how])
    header = list(LABEL_COLUMNS)
    if att.mode == "none":
        header[5] = "reader"
        order: dict[tuple[str, str], int] = {}
        for r in project.store.query("SELECT s.name, l.reviewer_id, MIN(l.label_id) first FROM labels l "
                                     "JOIN samples s ON s.sample_id=l.sample_id GROUP BY s.name, l.reviewer_id "
                                     "ORDER BY s.name, first"):
            key = (r["name"], att.person(r["reviewer_id"]))
            if r["reviewer_id"] in PLACEHOLDERS or key in order:   # imported labels are no reader
                continue
            order[key] = 1 + sum(1 for k in order if k[0] == r["name"])
        for r in rows:
            if r[5] in PLACEHOLDERS:
                r[5] = att.who(r[5])
            elif r[5] is not None:
                r[5] = order.get((r[0], att.person(r[5])), "")
    else:
        for r in rows:
            if r[5] is not None:
                r[5] = att.who(r[5])
    _write_rows(path, header, rows)


def _sanitized_json_copy(src: Path, dst: Path, root: Path, notes: list[str]) -> None:
    """A run folder's JSON with home and absolute paths made portable (the original is never changed)."""
    from ..core.sanitize import json_dumps, sanitize_json

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except ValueError:
        return
    clean, dropped = sanitize_json(data, root)
    if clean != data:
        notes.append(f"{dst.name} in {dst.parent.name}: paths made portable")
    notes += [f"value not included: {d} ({dst.parent.name}/{dst.name})" for d in dropped]
    dst.write_text(json_dumps(clean), encoding="utf-8")


def _reproduce_md(project: Project, notes: list[str], mode: str | None = None) -> str:
    """What running the bundle's runs again needs: variable names, CLIs, local servers, presets, consent; the first
    change made for sharing is the attribution ``mode``."""
    from ..core.sanitize import env_names

    cfg = project.config
    snaps = [json.loads(r["snapshot"]) for r in project.store.query("SELECT snapshot FROM backend_snapshots ORDER BY first_used")]
    L = [f"# Reproducing {cfg.name}", "",
         "Running these runs again needs the listed backends; this bundle is not executable on its own.", ""]
    names = sorted(env_names(cfg))
    L += ["## Environment variables to set (names only)", ""] + ([f"- `{n}`" for n in names] or ["- none"]) + [""]
    clis = sorted({(s.get("name") or "", s.get("model") or "", s.get("version") or "unknown version")
                   for s in snaps if s.get("kind") == "cli_agent"})
    L += ["## Command-line tools", ""] + ([f"- {n}: model {m}, {v}" for n, m, v in clis] or ["- none"]) + [""]
    local = sorted({(s.get("name") or "", s.get("kind") or "", s.get("model") or "", s.get("base_url") or "")
                    for s in snaps if s.get("locality") == "local" and s.get("kind") not in ("cli_agent", "fake")})
    L += ["## Local servers and models", ""] + ([f"- {n} ({k}): model {m} at {u}" for n, k, m, u in local] or ["- none"]) + [""]
    presets = sorted({s.get("preset") for s in snaps if s.get("preset")} | {p.preset for p in cfg.providers if p.preset})
    L += ["## Presets", ""] + ([f"- {x}" for x in presets] or ["- none"]) + [""]
    need = sorted({(s.get("name") or "", s.get("locality") or "") for s in snaps
                   if s.get("locality") in ("cloud", "network", "unknown")})
    L += ["## Consent needed before a run", ""] + ([f"- {n} ({loc}): research consent (`wider providers allow {n} --for research`)"
                                                     for n, loc in need] or ["- none"]) + [""]
    exp = sorted({s.get("name") or "" for s in snaps if s.get("experimental")})
    if exp:
        from ..screen.engine import EXPERIMENTAL_NOTE
        L += ["## Experimental backends", ""] + [f"- {n}: {EXPERIMENTAL_NOTE}" for n in exp] + [""]
    from ..core.researchers import REPRODUCE_LINES, mode_of
    notes = [REPRODUCE_LINES[mode or mode_of(project)], *notes]
    L += ["## Changes made for sharing", ""] + [f"- {n}" for n in notes] + [""]
    return "\n".join(L)


def _bundle_reporting(project: Project, dest: Path, att: object) -> None:
    """The PRISMA-S checklist and the reporting exports (``prisma_reporting/*.csv``, ``methods_text.md``) written
    fresh into the bundle; their researcher fields are name-scanned with it (prisma-reporting-assistant D25)."""
    from . import prisma_s, reporting

    title = project.config.title or project.config.name
    rep = prisma_s.build(project, att)
    (dest / "prisma_s.md").write_text(prisma_s.markdown(rep, title), encoding="utf-8")
    (dest / "prisma_s.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        reporting.export(project, folder=dest / reporting.EXPORT_DIR, packaged=True)
    except ValueError as e:   # nothing imported yet: no reporting files
        if not str(e).startswith(reporting.NOT_IMPORTED):
            raise


def share_bundle(project: Project, dest: str | Path | None = None, protocol: str | None = None, sample: str | None = None,
                 report: tuple[Path, Path] | None = None, result_set: int | None = None,
                 choice: str | None = None) -> Path:
    """Everything needed to audit the review except the copyrighted text: artifacts, run specs, prompts, decisions
    (record ids, titles, DOIs), samples and labels, report. No abstracts, no RIS exports, no raw model responses, no
    error text or evaluation reports (both can echo model output).

    ``project.yaml``, ``run_spec.json`` and ``backend_*.json`` are sanitized copies (home folder as ``~``; Decision 21)
    and ``REPRODUCE.md`` lists what a rerun needs. Every file is scanned before the bundle is finished: on a key value,
    key shape or home path the bundle is deleted and :class:`~wider.core.sanitize.SecretFound` names file, line and kind.

    ``report`` is the (report.md, report.json) pair just written by ``write_report``; without it the report is written
    here without recording a report result. Either way only the bundle's own stage result is recorded."""
    from ..screen.devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    from ..core.researchers import Attributor
    from ..core.sanitize import (
        NameFound,
        SecretFound,
        env_names,
        sanitized_project_yaml,
        scan_names_in_tree,
        scan_tree,
    )
    from ..screen.engine import EXPERIMENTAL_NOTE
    from ..screen.export import decision_rows
    from ..screen.result_sets import split_key
    from ..screen.review import _reviewer

    if report is None:
        report = write_report(project, record_result=False, protocol=protocol, sample=sample, result_set=result_set,
                              choice=choice)
    dest = Path(dest) if dest else project.path("exports", "share_bundle")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    notes: list[str] = []
    att = Attributor(project)
    try:
        text, dropped = sanitized_project_yaml(project.config, project.root, att)
        (dest / "project.yaml").write_text(text, encoding="utf-8")
        notes += [f"value not included: {d} (project.yaml)" for d in dropped]
        if (project.root / "benchmark.csv").exists():
            shutil.copy2(project.root / "benchmark.csv", dest / "benchmark.csv")
        for kind in artifacts.KINDS:
            src = project.artifact_dir(kind)
            if src.exists():
                shutil.copytree(src, dest / "artifacts" / kind)
        runs_dir = project.root / "runs"
        for spec in runs_dir.rglob("run_spec.json"):
            rel = spec.parent.relative_to(project.root)
            (dest / rel).mkdir(parents=True, exist_ok=True)
            for f in [spec, *sorted(spec.parent.glob("backend_*.json"))]:
                _sanitized_json_copy(f, dest / rel / f.name, project.root, notes)
            for name in ("prompt.md", "schema.json", "backend_changes.jsonl"):
                f = spec.parent / name
                if f.exists():
                    shutil.copy2(f, dest / rel / name)
            if (spec.parent / "errors.csv").exists():  # which calls failed, not the error text (it can echo model output)
                _copy_columns(spec.parent / "errors.csv", dest / rel / "errors.csv", keep=("record_id", "pass"))
            if (spec.parent / "decisions.csv").exists():  # drop the error text and the parsed JSON (quotes from abstracts)
                _copy_columns(spec.parent / "decisions.csv", dest / rel / "decisions.csv", drop=("error", "parsed"))
        st = project.store
        _write_rows(dest / "records.csv", ["record_id", "title", "year", "journal", "doi", "platforms", "abstract_words"],
                    st.query("SELECT record_id, title, year, journal, doi, platforms, abstract_words FROM records ORDER BY record_id"))
        # every result set of every protocol, so a reader can see what was not used for analysis
        set_rows = [dict(r) for r in st.query("SELECT * FROM screen_result_sets ORDER BY protocol, result_set")]
        keys = {r["key"]: r for r in set_rows}
        for k in [r["protocol"] for r in st.query("SELECT DISTINCT protocol FROM screen_decisions")]:
            if k not in keys:   # legacy rows without a set row
                keys[k] = {"protocol": split_key(k)[0], "result_set": split_key(k)[1], "active": 1}
        rows = []
        for k, sr in sorted(keys.items()):
            for d in decision_rows(project, k):
                o = d["origin"] or {}
                rows.append([sr["protocol"], sr["result_set"], "yes" if sr.get("active") else "", d["record_id"],
                             d["decision"], d.get("votes") or "", d.get("decided_by") or "", ";".join(d["models"]),
                             "yes" if d["mixed_models"] else "", d.get("cell") or "", d.get("labels") or "",
                             att.text(d.get("reason") or "", own=(_reviewer(d.get("reason")),)), o.get("kind", ""), o.get("set", "") or "",
                             EXPERIMENTAL_NOTE if d["experimental"] else ""])
        _write_rows(dest / "screen_decisions.csv",
                    ["protocol", "result_set", "active", "record_id", "decision", "votes", "decided_by", "models",
                     "mixed_models", "cell", "labels", "reason", "origin", "origin_set", "experimental"], rows)
        _write_rows(dest / "result_sets.csv",
                    ["protocol", "result_set", "key", "label", "source", "created_at", "scope", "role", "active", "mixed",
                     "source_protocol", "source_set", "settings_hash", "runs", "import_file", "import_sha256"],
                    [[r["protocol"], r["result_set"], r["key"], r["label"] or "", r["source"], r["created_at"],
                      r["scope"] or "", r["role"] or "", "yes" if r["active"] else "", "yes" if r["mixed"] else "",
                      r["source_protocol"] or "", r["source_set"] or "", r["settings_hash"] or "",
                      ";".join(x["run_id"] if isinstance(x, dict) else x for x in json.loads(r["runs"] or "[]")),
                      r["import_file"] or "", r["import_sha256"] or ""] for r in set_rows])
        _write_labels(project, dest / "labels.csv", att)
        _write_reference(project, dest, att)
        from ..sample.package import bundle_json
        pkgs = bundle_json(project, att.who)
        if pkgs:   # the facts of each review package, no item token (D11)
            (dest / "review_packages.json").write_text(json.dumps(pkgs, indent=2, ensure_ascii=False), encoding="utf-8")
        md, js = report
        shutil.copy2(md, dest / "report.md")
        shutil.copy2(js, dest / "report.json")
        for extra in ("dedup", "queries", "search_strings"):
            src = project.path("exports", extra)
            if src.exists():
                shutil.copytree(src, dest / extra)
        _bundle_reporting(project, dest, att)
        (dest / "REPRODUCE.md").write_text(_reproduce_md(project, notes, att.mode), encoding="utf-8")
        found = scan_tree(dest, env_names(project.config))
        names = [] if found else scan_names_in_tree(dest, att)   # names after secrets (D6)
    except BaseException:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    if found:
        shutil.rmtree(dest, ignore_errors=True)
        raise SecretFound(found)
    if names:
        shutil.rmtree(dest, ignore_errors=True)
        raise NameFound(names)
    from ..core.stage_result import record
    from ..core.stage_summaries import summarize_bundle
    record(project, summarize_bundle(dest, project.root))
    return dest
