"""Recall on independent papers, and Chapman's capture-recapture estimate (change papers-beyond-the-query, D25).

Independent papers are benchmark papers whose source is not ``query_results``: the researcher, a colleague, another
database, a prior review or seed chaining found them without the query. The check reads the validation set of
:func:`wider.search.coverage.validation_papers`, so it follows the same hiding and used-paper rules: while the set is
hidden it says so and reads nothing about the Validation papers (that would be a look). Independent Development papers
are counted apart and never enter the estimate, because the query was built to find them.

After screening, ``chapman`` treats the included records of a protocol (n1) and the independent validation papers
(n2) as two captures; m papers are in both. N = (n1+1)(n2+1)/(m+1) − 1. The two captures are not independent, which
:data:`CAVEAT` says beside every figure.
"""
from __future__ import annotations

from ..core.project import Project

HIDDEN_LINE = "validation set hidden: independent check not shown"
SCOPE = "for these {n} papers only; they are not a random sample of all relevant papers"
ALL_FOUND = "all found here does not mean none are missing"
DEV_TAIL = "not a validation figure (seen during development)"
NO_OVERLAP = "not defined (no overlap)"
NO_PAPERS = "no independent validation papers"
FEW_PAPERS = ("not given: {n2} independent validation papers are too few (fewer than {few}) to estimate how many are "
              "missing")
ALL_CAUGHT = ("not given: every independent validation paper is also among the includes, so the formula would say "
              "none are missing with no uncertainty (SE 0), which these papers cannot show")
CAVEAT = ("The two captures are not independent (well-cited papers are found by both chaining and the databases), "
          "which tends to overestimate recall and underestimate the total: these figures are a check, not proof.")
FORMULAS = {"recall": "m of n independent validation papers the query retrieves; exact (Clopper-Pearson) interval",
            "chapman": "N = (n1+1)(n2+1)/(m+1) - 1",
            "variance": "Var(N) = (n1+1)(n2+1)(n1-m)(n2-m) / ((m+1)^2 (m+2))",
            "missing": "N - (n1 + n2 - m)"}
FEW_BELOW = 10


def independent_source(source: str | None) -> bool:
    return (source or "researcher") != "query_results"


def _group(source: str | None) -> str:
    """The source a by-source count names: ``seed_chain:<id>`` counts as ``seed_chain``."""
    return (source or "researcher").split(":", 1)[0]


def _tally(project: Project, papers: list) -> dict:
    """found of n, by source, and the ids found and missed, for papers the query's imports are checked against."""
    from ..construct.seed import retrieved_by_query

    by: dict[str, list[int]] = {}
    found, missed = [], []
    for b in papers:
        hit = retrieved_by_query(project, b.doi, b.title) is not None
        (found if hit else missed).append(b.benchmark_id)
        t = by.setdefault(_group(b.source), [0, 0])
        t[0] += hit
        t[1] += 1
    return {"found": len(found), "n": len(papers), "by_source": dict(sorted(by.items())),
            "found_ids": found, "missed_ids": missed}


def _by_source_text(by: dict) -> str:
    from ..search.coverage import source_counts
    return source_counts(by)


def chapman_numbers(n1: int, n2: int, m: int) -> dict:
    """Chapman's estimate from two captures: {n1, n2, m, union, N, se, missing, reason}. ``reason`` is set (and N is
    None) with no independent validation papers, no overlap, fewer than :data:`FEW_BELOW` of them, or all of them in
    both captures (m = n2 makes the variance 0 and the missing count 0, a claim the data cannot support)."""
    out = {"n1": n1, "n2": n2, "m": m, "union": n1 + n2 - m, "N": None, "se": None, "missing": None, "reason": None}
    if n2 <= 0:
        return {**out, "reason": NO_PAPERS}
    if n2 < FEW_BELOW:   # said first: with too few papers an overlap would not help either
        return {**out, "reason": FEW_PAPERS.format(n2=n2, few=FEW_BELOW)}
    if m <= 0:
        return {**out, "reason": NO_OVERLAP}
    if m >= n2:
        return {**out, "reason": ALL_CAUGHT}
    N = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    var = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    return {**out, "N": N, "se": var ** 0.5, "missing": N - out["union"]}


def _included_keys(project: Project, protocol: str, result_set: int | None = None) -> tuple[int, set[str], set[str]]:
    """The number of included records of the protocol's active set (or ``result_set``) and their DOIs and titles."""
    from ..core.text import clean_doi, norm_title
    from ..screen.result_sets import active_key

    key = active_key(project, protocol, result_set)
    rows = project.store.query("SELECT r.doi, r.title FROM screen_decisions d JOIN records r USING(record_id) "
                               "WHERE d.protocol=? AND d.decision='include'", (key,))
    return len(rows), {clean_doi(r["doi"]) for r in rows} - {None, ""}, {norm_title(r["title"]) for r in rows} - {None, ""}


def _default_protocol(project: Project) -> str | None:
    from ..screen.decisions import protocols

    ps = protocols(project)
    return ps[-1]["protocol"] if ps else None


def chapman(project: Project, protocol: str | None = None, result_set: int | None = None,
            papers: list | None = None) -> dict | None:
    """The estimate for ``protocol`` (default the most recently screened one) from its included records and the
    independent validation papers, or None when nothing was screened. Call it only while the validation set is shown
    (:func:`check` does)."""
    from ..core.text import clean_doi, norm_title
    from ..search.coverage import validation_papers

    if protocol:
        from ..screen.decisions import protocols

        if protocol not in {r["protocol"] for r in protocols(project)}:
            raise KeyError(f"no screening decisions for protocol {protocol!r}")
    protocol = protocol or _default_protocol(project)
    if not protocol:
        return None
    n1, dois, titles = _included_keys(project, protocol, result_set)
    val = [b for b in validation_papers(project, papers) if independent_source(b.source)]
    both = [b.benchmark_id for b in val
            if (clean_doi(b.doi) or "") in dois or (norm_title(b.title) or "") in titles]
    return {"protocol": protocol, **chapman_numbers(n1, len(val), len(both)), "in_both_ids": both}


def check(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict:
    """The independent check (D25): ``shown``, the validation part (counts, by source, interval, ids) while the set is
    shown, the independent Development papers always, the estimate after screening, the caveat, and ``lines`` (what
    the CLI, the Recall page card and the report print)."""
    from ..search.coverage import development_papers, validation_papers, validation_unlocked
    from .uncertainty import interval_words, recall_uncertainty, uncertainty_lines

    papers = project.read_benchmark()
    shown = validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
    dev = _tally(project, [b for b in development_papers(project, papers) if independent_source(b.source)])
    dev_line = f"Independent Development papers: {dev['found']} of {dev['n']}, {DEV_TAIL}." if dev["n"] else None
    out = {"shown": shown, "development": dev, "validation": None, "estimate": None, "caveat": CAVEAT,
           "formulas": FORMULAS, "lines": []}
    if not shown:
        out["lines"] = [f"{HIDDEN_LINE[:1].upper()}{HIDDEN_LINE[1:]}."] + ([dev_line] if dev_line else [])
        return out
    val = _tally(project, [b for b in validation_papers(project, papers) if independent_source(b.source)])
    u = recall_uncertainty(val["found"], val["n"])
    out["validation"] = {**val, "uncertainty": u}
    lines = []
    if val["n"]:
        lines.append(f"Recall on independent validation papers: {val['found']} of {val['n']} "
                     f"({_by_source_text(val['by_source'])}), {interval_words(u)}, {SCOPE.format(n=val['n'])}.")
        notes = uncertainty_lines(u, noun="independent validation paper", few_below=FEW_BELOW, subject="the query",
                                  keeps="finds", kept="found", remedy=None)[2:]
        lines += [n for n in notes if not n.startswith("No independent validation paper was missed")]
        if val["found"] == val["n"]:
            lines.append(f"{ALL_FOUND[:1].upper()}{ALL_FOUND[1:]}.")
    else:
        lines.append(f"Recall on independent validation papers: {NO_PAPERS}.")
    if dev_line:
        lines.append(dev_line)
    est = chapman(project, protocol, result_set, papers) if val["n"] else None
    out["estimate"] = est
    if est:
        lines.append(estimate_line(est))
    if val["n"]:
        lines.append(CAVEAT)
    out["lines"] = lines
    return out


def estimate_line(est: dict) -> str:
    head = (f"Estimate after screening ({est['protocol']}: {est['n1']} included by screening, {est['n2']} independent validation "
            f"papers, {est['m']} in both)")
    if est["reason"]:
        return f"{head}: {est['reason']}."
    return (f"{head}: Chapman's estimate of all relevant papers is about {round(est['N'])} (SE {est['se']:.1f}); "
            f"about {max(0, round(est['missing']))} relevant papers may be missing from both.")


def package_json(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict:
    """``independent_estimate.json`` of the package: counts, benchmark ids, the formulas and the caveat; no titles or
    abstracts. While the validation set is hidden only the hidden line and the Development counts."""
    d = check(project, protocol, result_set)
    keep = ("found", "n", "by_source", "found_ids", "missed_ids")
    v = d["validation"]
    return {"shown": d["shown"], "hidden_line": None if d["shown"] else HIDDEN_LINE,
            "validation": ({**{k: v[k] for k in keep}, "interval": list(v["uncertainty"]["interval"])
                            if v["uncertainty"]["interval"] else None,
                            "scope": SCOPE.format(n=v["n"])} if v else None),
            "development": {**{k: d["development"][k] for k in keep}, "note": DEV_TAIL},
            "estimate": d["estimate"], "formulas": FORMULAS, "caveat": CAVEAT, "lines": d["lines"]}
