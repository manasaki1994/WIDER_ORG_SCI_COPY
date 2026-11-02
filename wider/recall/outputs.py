"""Outputs for the paper: PRISMA 2020 flow diagram (SVG), a methods paragraph drafted from the project's facts for
the researcher to rewrite, reference-manager exports of the included set, and a zipped replication package.

Numbers come from the store, never typed in. The diagram stops at "reports sought for retrieval": full-text
screening is a later stage, so its boxes say "not yet recorded" rather than inventing counts. The replication
package holds the share bundle (no abstracts, no raw exports, no raw model responses) plus the diagram, the methods
text and the search-strings archive, with a SHA-256 manifest; reference exports carry abstracts and stay out of it.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from xml.sax.saxutils import escape as xml_escape

from ..core.provenance import utcnow

if TYPE_CHECKING:
    from ..core.project import Project

OTHER_METHODS = {"citation_chain": "Citation searching (prior reviews, OpenAlex)"}
METHODS_FILE = "methods.md"
# the keys of each unlock.json row, in order (D2); ``seen`` lists {benchmark_id, doi, title_norm}, never an abstract
# ``seen_choice`` is the researcher's answer ("count" / "keep"), "" when nothing was asked (unlock-seen-choice D2)
UNLOCK_KEYS = ("unlock_no", "cause", "from_version", "new_version", "at", "by", "final_before", "counts_as_seen",
               "seen_choice", "seen")
# the keys of each rounds.json round and of each of its platforms (2a, 2.11b); development seed ids only, never a title
ROUND_KEYS = ("round", "status", "created_at", "frozen_query", "accepted", "accepted_no", "accepted_how", "accepted_by",
              "accepted_at", "lost", "platforms")
ROUND_PLATFORM_KEYS = ("platform", "count", "count_source", "recall", "uncertain")


def _default_protocol(project: Project, protocol: str | None, result_set: int | None = None) -> dict[str, Any] | None:
    """The set an output reads (:func:`~wider.screen.result_sets.reader`): ``protocol``'s active set or set
    ``result_set``; without a protocol the most recently screened one. None when nothing was screened."""
    from ..screen.decisions import protocols
    from ..screen.result_sets import reader

    if result_set is not None and not protocol:
        raise ValueError("--set needs a protocol")
    if protocol:
        info = reader(project, protocol, result_set)
        if not project.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (info["key"],)):
            raise ValueError(f"no screening decisions under protocol {protocol!r}"
                             + (f" set {result_set}" if result_set is not None else ""))
        return info
    ps = protocols(project)
    return reader(project, ps[-1]["protocol"]) if ps else None


def set_name(info: dict[str, Any] | None) -> str:
    """``P`` for set 1, ``P set n`` otherwise (how outputs name the set they read)."""
    if not info:
        return ""
    return info["protocol"] + (f" set {info['result_set']}" if (info.get("result_set") or 1) > 1 else "")


# ----------------------------------------------------------------------------------------------- PRISMA 2020
def provisional_line(project: Project, summary: dict, not_checked: int) -> str | None:
    """Why the PRISMA counts may still change, in one sentence every diagram, export and page gives the same way
    (whole-app inspection, 1 Nov 2026): records not checked, pairs undecided, decisions not applied, pairs Unsure."""
    from ..dedup.engine import flow_counts

    if not summary:
        return None
    if not_checked:
        return (f"Provisional: {not_checked:,} records imported since the last deduplication run are not checked for "
                "duplicates yet.")
    f = flow_counts(project, summary)
    if f["to_decide"]:
        return ("Provisional: 1 duplicate pair still waits for a manual decision." if f["to_decide"] == 1 else
                f"Provisional: {f['to_decide']:,} duplicate pairs still wait for a manual decision.")
    if f["decided_since"]:
        return ("Provisional: " + ("1 decided duplicate pair is" if f["decided_since"] == 1 else
                                   f"{f['decided_since']:,} decided duplicate pairs are")
                + " not applied yet; the counts change when deduplication runs again.")
    if f["unsure"]:
        return ("Provisional: " + ("1 duplicate pair is" if f["unsure"] == 1 else f"{f['unsure']:,} duplicate pairs are")
                + " marked Unsure.")
    return None


def _dedup_state(project: Project, summary: dict, raw: int, unique: int) -> tuple[bool, int, int, int, str | None]:
    """(provisional by the run's summary, records of excluded files, duplicates removed, records not checked, the
    provisional line) for the PRISMA counts and the Overview's records line."""
    from ..dedup.engine import excluded_file_records, is_provisional

    provisional = is_provisional(summary)
    # records of files kept out of deduplication (D5) are removed before screening, never counted as duplicates
    excluded_files = excluded_file_records(project, summary) if raw else 0
    # duplicates come from the last run; records imported (or merged) since are counted apart until the next run
    duplicates = int(summary.get("duplicate_records_removed") or 0) if raw else 0
    not_checked = max(raw - excluded_files - duplicates - unique, 0) if raw else 0
    line = provisional_line(project, summary, not_checked) if raw else None
    if provisional and line is None:   # the run's own summary still counts open pairs
        line = "Provisional: duplicate pairs still wait for a manual decision."
    return provisional, excluded_files, duplicates, not_checked, line


def records_provisional(project: Project) -> str | None:
    """The provisional line for the Overview's records count (the PRISMA counts' sentence), or None."""
    st = project.store
    raw = st.scalar("SELECT COUNT(*) FROM raw_records") or 0
    summary = json.loads(st.get_meta("dedup_summary") or "{}")
    return _dedup_state(project, summary, raw, st.scalar("SELECT COUNT(*) FROM records") or 0)[4]


def prisma_counts(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict[str, Any]:
    from ..search.catalogue import platform_labels

    st = project.store
    known = platform_labels()
    labels = {p.key: p.label or known.get(p.key, p.key) for p in project.config.platforms}
    by_platform = [(r["platform"], r["n"]) for r in st.query("SELECT platform, COUNT(*) n FROM raw_records GROUP BY platform ORDER BY platform")]
    databases = [{"key": k, "label": labels.get(k, known.get(k, k)), "n": n} for k, n in by_platform if k not in OTHER_METHODS]
    other = [{"key": k, "label": OTHER_METHODS[k], "n": n} for k, n in by_platform if k in OTHER_METHODS]
    raw = sum(n for _, n in by_platform)
    unique = st.scalar("SELECT COUNT(*) FROM records") or 0
    info = _default_protocol(project, protocol, result_set)
    protocol = info["protocol"] if info else None
    dec = {"include": 0, "maybe": 0, "exclude": 0}
    if info:
        for r in st.query("SELECT decision, COUNT(*) n FROM screen_decisions WHERE protocol=? GROUP BY decision", (info["key"],)):
            dec[r["decision"]] = r["n"]
    screened = sum(dec.values())
    summary = json.loads(st.get_meta("dedup_summary") or "{}")
    provisional, excluded_files, duplicates, not_checked, line = _dedup_state(project, summary, raw, unique)
    return {"protocol": protocol, "result_set": info["result_set"] if info else None,
            "result_set_key": info["key"] if info else None, "set_name": set_name(info),
            "role_note": info["role_note"] if info else None, "provisional": provisional or line is not None,
            "provisional_line": line, "databases": databases, "other": other, "identified": raw,
            "identified_databases": sum(d["n"] for d in databases), "identified_other": sum(d["n"] for d in other),
            "duplicates_removed": duplicates, "not_checked": not_checked,
            "excluded_file_records": excluded_files, "unique": unique, "screened": screened,
            "not_screened": max(unique - screened, 0), "excluded": dec["exclude"], "included_ta": dec["include"],
            "maybe_ta": dec["maybe"], "sought": dec["include"] + dec["maybe"]}


def _wrap(text: str, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def prisma_svg(c: dict[str, Any], title: str = "") -> str:
    """PRISMA 2020 flow (identification via databases and via other methods, screening, included), as plain SVG with
    black text on white so it can go into a manuscript as is."""
    W, colw, gap, x0 = 960, 400, 40, 110
    x1 = x0 + colw + gap
    boxes: list[tuple[float, float, float, list[str], bool]] = []   # x, y, w, lines, dashed
    arrows: list[tuple[float, float, float, float]] = []
    y = 70
    n = lambda v: f"{v:,}"  # noqa: E731

    def box(x, y, w, text_lines, dashed=False):
        lines = [ln for t in text_lines for ln in (_wrap(t, int(w / 7.4)) or [""])]
        boxes.append((x, y, w, lines, dashed))
        return y + 18 * len(lines) + 16

    db_lines = [f"Records identified from databases (n = {n(c['identified_databases'])}):"] + \
               [f"{d['label']} (n = {n(d['n'])})" for d in c["databases"]]
    other_lines = ([f"Records identified via other methods (n = {n(c['identified_other'])}):"] +
                   [f"{d['label']} (n = {n(d['n'])})" for d in c["other"]]) if c["other"] else \
                  ["Records identified via other methods: none recorded"]
    y_db_end = box(x0, y, colw, db_lines)
    y_ot_end = box(x1, y, colw, other_lines, dashed=not c["other"])
    y = max(y_db_end, y_ot_end) + 30
    arrows.append((x0 + colw / 2, y_db_end, x0 + colw / 2, y))
    if c["other"]:
        arrows.append((x1 + colw / 2, y_ot_end, x0 + colw * 0.8, y))
    y_rem_end = box(x0, y, colw, [f"Records after duplicates removed (n = {n(c['unique'])})",
                                  f"Duplicate records removed (n = {n(c['duplicates_removed'])})"] +
                    ([f"Records removed before screening: file excluded (n = {n(c['excluded_file_records'])})"]
                     if c.get("excluded_file_records") else []) +
                    ([f"Not yet checked for duplicates (n = {n(c['not_checked'])})"] if c.get("not_checked") else []) +
                    ([c["provisional_line"]] if c.get("provisional_line") else []))
    y2 = y_rem_end + 30
    arrows.append((x0 + colw / 2, y_rem_end, x0 + colw / 2, y2))
    proto = f" under screening protocol {c['protocol']}" if c["protocol"] else ""
    scr = [f"Records screened on title and abstract{proto} (n = {n(c['screened'])})"]
    if c["not_screened"]:
        scr.append(f"Not screened yet (n = {n(c['not_screened'])})")
    y_scr_end = box(x0, y2, colw, scr)
    box(x1, y2, colw, [f"Records excluded (n = {n(c['excluded'])})"])
    arrows.append((x0 + colw, y2 + 20, x1, y2 + 20))
    y3 = y_scr_end + 30
    arrows.append((x0 + colw / 2, y_scr_end, x0 + colw / 2, y3))
    y_sought_end = box(x0, y3, colw, [f"Reports sought for retrieval (n = {n(c['sought'])}): included (n = {n(c['included_ta'])}) "
                                      f"and marked maybe for full-text reading (n = {n(c['maybe_ta'])})"])
    box(x1, y3, colw, ["Reports not retrieved: not yet recorded"], dashed=True)
    y4 = y_sought_end + 30
    arrows.append((x0 + colw / 2, y_sought_end, x0 + colw / 2, y4))
    y_el_end = box(x0, y4, colw, ["Reports assessed for eligibility: not yet recorded (full-text screening is a later stage)"], dashed=True)
    box(x1, y4, colw, ["Reports excluded, with reasons: not yet recorded"], dashed=True)
    y5 = y_el_end + 30
    arrows.append((x0 + colw / 2, y_el_end, x0 + colw / 2, y5))
    y_end = box(x0, y5, colw, ["Studies included in review: not yet recorded"], dashed=True)
    H = y_end + 30
    phases = [("Identification", 70, max(y_db_end, y_ot_end) + 30 + (y_rem_end - (max(y_db_end, y_ot_end) + 30))),
              ("Screening", y2, y_el_end), ("Included", y5, y_end)]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
           'role="img" aria-labelledby="prisma-title" font-family="Helvetica, Arial, sans-serif" font-size="13">',
           f'<title id="prisma-title">PRISMA 2020 flow diagram{": " + xml_escape(title) if title else ""}</title>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0 0L10 5L0 10z" fill="#222"/></marker></defs>',
           f'<text x="{x0}" y="36" font-size="15" font-weight="bold" fill="#111">Identification of studies via databases</text>',
           f'<text x="{x1}" y="36" font-size="15" font-weight="bold" fill="#111">Identification via other methods</text>']
    for label, top, bottom in phases:
        h = max(bottom - top, 40)
        out.append(f'<rect x="20" y="{top}" width="70" height="{h}" rx="6" fill="#dbe6f3" stroke="#6b8bb5"/>')
        cx, cy = 55, top + h / 2
        out.append(f'<text x="{cx}" y="{cy}" transform="rotate(-90 {cx} {cy})" text-anchor="middle" dominant-baseline="middle" '
                   f'font-weight="bold" fill="#1d3557">{label}</text>')
    for x, yy, w, lines, dashed in boxes:
        h = 18 * len(lines) + 16
        out.append(f'<rect x="{x}" y="{yy}" width="{w}" height="{h}" rx="4" fill="#ffffff" stroke="#222"'
                   f'{" stroke-dasharray=" + chr(34) + "5 4" + chr(34) if dashed else ""}/>')
        for i, ln in enumerate(lines):
            out.append(f'<text x="{x + 10}" y="{yy + 22 + 18 * i}" fill="#111">{xml_escape(ln)}</text>')
    for xa, ya, xb, yb in arrows:
        out.append(f'<line x1="{xa}" y1="{ya}" x2="{xb}" y2="{yb}" stroke="#222" stroke-width="1.3" marker-end="url(#arr)"/>')
    out.append(f'<text x="{x0}" y="{H - 8}" font-size="11" fill="#555">Generated by WIDER from the project store; dashed boxes are '
               'stages not recorded yet.</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_prisma(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict[str, Any]:
    c = prisma_counts(project, protocol, result_set)
    if not c["identified"]:
        raise ValueError("nothing imported yet; the diagram starts from the database exports")
    out = project.path("exports")
    out.mkdir(exist_ok=True)
    svg = prisma_svg(c, project.config.title or project.config.name)
    (out / "prisma_2020.svg").write_text(svg, encoding="utf-8")
    (out / "prisma_2020.json").write_text(json.dumps(c, indent=1), encoding="utf-8")
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="prisma", headline=(
        f"PRISMA 2020 diagram written: {c['identified']:,} records identified, {c['unique']:,} after duplicates removed, "
        f"{c['screened']:,} screened, {c['sought']:,} sought for full text."),
        metrics={"identified": c["identified"], "unique": c["unique"], "screened": c["screened"], "excluded": c["excluded"],
                 "sought": c["sought"]}, outputs=["exports/prisma_2020.svg", "exports/prisma_2020.json"], next_stage=None,
        warnings=[c["provisional_line"]] if c.get("provisional_line") else []))
    return {**c, "svg": svg, "path": "exports/prisma_2020.svg"}


# ----------------------------------------------------------------------------------------------- methods paragraph
def _date_range(dates: list[str]) -> str:
    ds = sorted(d[:10] for d in dates if d)
    if not ds:
        return ""
    return ds[0] if ds[0] == ds[-1] else f"between {ds[0]} and {ds[-1]}"


def search_sentence(project: Project, c: dict[str, Any], note: dict | None) -> str | None:
    """"We searched ..." of the methods draft: the databases, the import dates, the query version and the benchmark
    sets (shared with the PRISMA reporting draft of item 7, prisma-reporting-assistant D9). None without databases."""
    from ..construct import artifacts
    from ..search.coverage import development_papers, validation_papers

    dbs = [d["label"] for d in c["databases"]]
    if not dbs:
        return None
    st = project.store
    when = _date_range([r["imported_at"] for r in st.query("SELECT imported_at FROM sources")])
    # the searched query is the version frozen last (not the highest frozen number); else the newest approved one
    syn = [r for r in artifacts.list_versions(project, "synonyms") if r["status"] in ("approved", "frozen")]
    sq = artifacts.latest_frozen(project, "synonyms") or (syn[-1] if syn else None)
    syn_txt = f" (synonym blocks version {sq['version']}, {sq['status']})" if sq else ""
    bm_dev, bm_val = len(development_papers(project)), len(validation_papers(project))   # the sets of D3
    # "held-out" and "tested" only when the note says the search is validated (never with the set hidden or not
    # hidden at all)
    val_txt = (f" and tested once on {bm_val} held-out validation paper{'' if bm_val == 1 else 's'}"
               if bm_val and note and note["validated"] else "")
    dev_txt = f", developed against {bm_dev} known-relevant development paper{'' if bm_dev == 1 else 's'}{val_txt}" if bm_dev else ""
    return (f"We searched {', '.join(dbs[:-1]) + ' and ' + dbs[-1] if len(dbs) > 1 else dbs[0]}"
            f"{' (exports imported ' + when + ')' if when else ''} with a Boolean query{syn_txt}{dev_txt}.")


def screening_sentences(project: Project, c: dict[str, Any]) -> list[str]:
    """The screening sentences of the methods draft for the set of ``c``: models, codebook, the rule, the counts, the
    pass models and the decisions after screening (shared with the PRISMA reporting draft, D9)."""
    from ..construct import artifacts
    from ..screen.engine import EXPERIMENTAL_NOTE
    from ..screen.export import decision_rows
    from ..screen.protocol import pass_models
    from ..screen.review import methods_sentences as review_sentences

    drows = decision_rows(project, c["result_set_key"])
    ms = list(dict.fromkeys(m for d in drows for m in d["models"]))
    models = ", ".join(ms) or "a language model"
    exp = f" ({EXPERIMENTAL_NOTE})" if any(d["experimental"] for d in drows) else ""
    cb = artifacts.load(project, "codebook")[1] or {}
    n_el = len(cb.get("elements", []))
    el_txt = f" of {n_el} elements" if n_el else ""
    sc = project.config.protocol.screening
    s = [f"Titles and abstracts were screened by {models} against a codebook{el_txt}; every grade had to be supported "
         f"by a verbatim quote, and a record was excluded only when the construct was shown to be absent or the study "
         f"unrelated. Two independent reads with a blind third read on disagreement were combined by a {sc.majority}-of-3 "
         f"majority (protocol {c['set_name']}): {c['included_ta']:,} records were included and {c['maybe_ta']:,} kept "
         f"for full-text reading, of {c['screened']:,} screened.{exp}"]
    pm = pass_models(project, c["result_set_key"])   # the model of each pass, and the passes sharing one (D13, D15)
    s += [x for x in (pm["sentence"], *pm["shared"]) if x]
    s += review_sentences(project, c["result_set_key"])   # decisions after screening and QC draws (manual-review D4)
    return s


def recall_sentences(project: Project, rec: dict[str, Any]) -> list[str]:
    """The recall sentence of the methods draft from the recall dict ``rec`` (``build_report``'s, or the stored
    estimate's for the PRISMA reporting draft, D9, D16), with the lines on independence, the researcher and a sealed
    sample changed."""
    cfg = project.config
    s: list[str] = []
    var = rec.get("variants", {})
    primary = cfg.protocol.recall.primary_variant
    v = var.get(primary)
    if not v or v.get("recall") is None:
        return []
    from .uncertainty import METHOD_NAMES, interval_words, papers_text

    how = (f", {METHOD_NAMES.get(v['interval_method'], v['interval_method'])} method"
           if v.get("interval") and v.get("interval_method") else "")
    ci_txt = (f" ({interval_words({**v, 'level': cfg.protocol.recall.ci_level})}{how}; "
              f"{papers_text(v.get('n_relevant') or 0)})")
    s.append(f"Against the human labels of the probability sample, the band-weighted recall of the screening "
             f"({primary} variant) was {v['recall']:.2f}{ci_txt}.")
    from .validation import NOT_INDEPENDENT
    nind = NOT_INDEPENDENT.format(sample=rec.get("sample"))
    s += [x for x in rec.get("lines") or [] if x == nind or x.startswith("The researcher (")
          or x.endswith("uses it for model selection.")]   # import-history-and-model-selection D3
    from ..sample.labels import sealed_change_line, sealed_disagreement_line
    changed = sealed_change_line(project, rec.get("sample"))   # one sentence (D6)
    if changed:
        s.append(changed)
    dis = sealed_disagreement_line(project, rec.get("sample"), commands=False)   # recall-final-check-fixes D6
    if dis:
        s.append(dis)
    return s


def methods_draft(project: Project, protocol: str | None = None, result_set: int | None = None,
                  choice: str | None = None, pending: dict | None = None) -> str:
    """A methods paragraph assembled from the store; each sentence appears only when its numbers exist. A recall set
    waiting for the choice of D8 gives its pending line, and ``pending`` (when passed) gets ``choice_needed`` and
    ``notice``; ``choice`` records it first."""
    from ..sample.design import sampling_log
    from ..sample.labels import agreement, progress
    from .report import build_report

    st, cfg = project.store, project.config
    c = prisma_counts(project, protocol, result_set)
    s: list[str] = []
    from ..search.coverage import validity_lines, validity_note
    note = validity_note(project) if project.read_benchmark() else None
    srch = search_sentence(project, c, note)
    if srch:
        s.append(srch)
    if note:   # whether the search is validated (D3), as in the report
        s.append(" ".join(validity_lines(note)))
    if c["other"]:
        s.append(f"Citation searching from the studies included in prior reviews added {c['identified_other']:,} records.")
    if c["identified"]:
        manual = st.scalar("SELECT COUNT(*) FROM dedup_pairs WHERE decision LIKE 'manual_%'") or 0
        man_txt = f", with {manual} uncertain pairs decided by hand" if manual else ""
        s.append(f"The searches returned {c['identified']:,} records; deduplication by DOI, exact title and corroborated fuzzy "
                 f"title matching{man_txt} left {c['unique']:,} unique records.")
    filled = st.scalar("SELECT COUNT(*) FROM enrichment_log WHERE status='filled'") or 0
    if filled:
        s.append(f"Missing or very short abstracts were completed for {filled:,} records from OpenAlex, Crossref, Europe PMC "
                 "and Semantic Scholar.")
    # the version the samples were drawn from (the latest probability sample's first), else the default ranking, so a
    # re-rank leaves the paragraph as it was (ui-redesign D5); each version names its own embedding model
    rk = st.one("SELECT r.anchors_version, r.top_n, COALESCE(r.embedding_model, e.model) AS model FROM rankings r "
                "LEFT JOIN embedding_sets e ON e.set_name=r.set_name WHERE r.ranking_id IN (SELECT ranking_id FROM "
                "samples) ORDER BY (SELECT MAX(CASE WHEN s.kind='probability' THEN s.sample_id END) FROM samples s "
                "WHERE s.ranking_id=r.ranking_id) DESC, r.ranking_id DESC LIMIT 1") or st.one(
        "SELECT r.anchors_version, r.top_n, COALESCE(r.embedding_model, e.model) AS model FROM rankings r "
        "LEFT JOIN embedding_sets e ON e.set_name=r.set_name WHERE r.name=?", (st.get_meta("default_ranking"),))
    if rk:
        s.append(f"Records were ranked by their similarity to one anchor sentence per construct cell (anchors version "
                 f"{rk['anchors_version']}, mean of the top {rk['top_n']} cells) using the embedding model {rk['model']}.")
    log = sampling_log(project)
    prob = [(k, v) for k, v in log.items() if v["kind"] == "probability"]
    purp = [(k, v) for k, v in log.items() if v["kind"] != "probability"]
    if prob:
        name, v = prob[-1]
        pr = progress(project, name)
        reviewers = list(pr["reviewers"])
        kap = ""
        if len(reviewers) >= 2:
            ag = agreement(project, name, reviewers[0], reviewers[1])
            if ag.get("kappa") is not None:
                lo = f"; {ag['left_out']['line']}" if ag.get("left_out") else ""   # an exclude choice (blinding D7)
                kap = f" (Cohen's kappa {ag['kappa']:.2f} on {ag['n']} records labelled by both{lo})"
        dev = f" and a purposive development set of {purp[-1][1]['n']} records" if purp else ""
        who = f"{len(reviewers)} reviewer{'s' if len(reviewers) != 1 else ''}" if reviewers else "the reviewers"
        s.append(f"A band-stratified probability sample of {v['n']} records across the ranking{dev} "
                 f"{'were' if dev else 'was'} labelled by {who}{kap}.")
        from ..sample.design import weights_basis
        wb = weights_basis(project, name)
        if wb:
            s.append(wb)
    if purp:   # the review protocol for the development sample (ui-redesign D4)
        s.append("The development sample was labelled by two reviewers, with disagreements adjudicated."
                 if cfg.protocol.sampling.development_reviewers == 2 else
                 "Each development sample record needed one reviewer's final label.")
    if c["protocol"]:
        s += screening_sentences(project, c)
        try:
            rep = build_report(project, protocol=c["protocol"], result_set=c["result_set"], choice=choice)
        except (KeyError, ValueError):
            rep = {}
        rec = (rep or {}).get("recall") or {}
        if rec.get("labels_incomplete"):   # no recall figure until every sampled record is final (D4)
            s.append(rec["notice"])
        if rec.get("choice_needed"):
            s += rec["lines"]
            if pending is not None:
                pending.update(choice_needed=True, notice=rec["notice"])
        s += recall_sentences(project, rec)
        s += validation_sentences(rec, project)
    from ..screen.compare import methods_sentence
    cmp_txt = methods_sentence(project)   # how many configuration comparisons, on which sample (D15)
    if cmp_txt:
        s.append(cmp_txt)
    from ..core.researchers import Attributor  # the draft becomes methods.md, a shared file (D5)
    att = Attributor(project)
    from ..sample.labels import unlock_sentences
    # every sample unlock, and whether its recall result had been seen (manual-review D5); a short name is not caught
    # by the scan of the whole text, so the author is written by the setting here
    s += unlock_sentences(project, att.who)
    from ..rank.versions import kept_sentences
    s += kept_sentences(project, att.who)   # a ranking kept after its records changed (ui-redesign D5)
    from ..sample.package import ONE_REVIEWER, report_section
    br = report_section(project, att.who)   # blinded independent review (reviewer-roles-and-blinding D11)
    if br["one_reviewer"]:
        s.append(ONE_REVIEWER)
    elif br["packages"]:
        s.append("Blinded independent review: " + "; ".join(x.rstrip(".") for x in br["lines"][:-1]) + ". "
                 + br["lines"][-1])
    s.append(f"The procedure, prompts, run specifications and all numbers are reproducible from the project with WIDER "
             f"(protocol hash {cfg.protocol.hash()[:12]}).")
    text = " ".join(s)
    return att.text(text, re.findall(r"The researcher \(([^)]+)\)", text))


def validation_sentences(rec: dict, project: Project | None = None) -> list[str]:
    """The validation sentences of the methods draft (D14, D15, D17): the target, the outcome (and, when it is
    inconclusive, what that means: recall-certification-workload D6), the caveats of the bounds, the model-based label,
    the validation-only re-check queue and the scope line."""
    from ..sample.labels import VALIDATION_ONLY
    from .validation import CONSERVATIVE, FIRST_DRAW, open_result_line

    vc = rec.get("validation_checks") or {}
    out = list((vc.get("target") or {}).get("lines") or [])[:1]
    vl = rec.get("validation_lines") or []
    out += [x for x in (rec.get("outcome") or {}).get("lines") or []]
    orl = open_result_line(project, rec.get("sample"), rec) if project is not None else None
    if orl:
        out.append(orl)
    if rec.get("bounds"):
        out += [FIRST_DRAW, CONSERVATIVE]
        out += [x for x in vl if x.startswith("Supplementary, model-based")]
    if vc.get("rechecked") or (vc.get("second_reviewer") or {}).get("n"):
        out.append(VALIDATION_ONLY)
    if rec.get("scope_line"):
        out.append(rec["scope_line"])
    return list(dict.fromkeys(out))


def read_methods(project: Project) -> str | None:
    f = project.root / METHODS_FILE
    return f.read_text(encoding="utf-8") if f.exists() else None


def save_methods(project: Project, text: str) -> str:
    """The researcher's own text, kept in the project folder (``methods.md``) and copied into the package."""
    (project.root / METHODS_FILE).write_text(text.rstrip() + "\n", encoding="utf-8")
    return METHODS_FILE


# ----------------------------------------------------------------------------------------------- reference exports
SETS = {"include": ("include",), "include+maybe": ("include", "maybe"), "maybe": ("maybe",), "exclude": ("exclude",)}
# sets chosen by how the decision was reached (design D6, :func:`~wider.screen.agreement.route_of`), whatever the decision
ROUTE_SETS = ("agreed", "third_read", "split", "researcher")
ALL_SETS = (*SETS, *ROUTE_SETS)


def _authors(raw: str | None) -> list[str]:
    return [a.strip() for a in re.split(r";|\n", raw or "") if a.strip()]


def _bibkey(rec: dict, taken: set[str]) -> str:
    first = re.sub(r"[^a-z]", "", (_authors(rec["authors"])[:1] or ["anon"])[0].split(",")[0].lower()) or "anon"
    word = next((w for w in re.findall(r"[a-z]+", (rec["title"] or "").lower()) if len(w) > 3), "untitled")
    key = f"{first}{rec['year'] or ''}{word}"
    k, i = key, 1
    while k in taken:
        i += 1
        k = f"{key}{chr(96 + i)}"
    taken.add(k)
    return k


def _bib_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


UNRESOLVED_NOTE = "maybe (unresolved: no majority)"
CSV_COLUMNS = ("record_id", "decision", "decided_by", "title", "authors", "year", "journal", "doi", "url", "abstract",
               "protocol", "result_set", "status", "pathway")


def _all_rows(project: Project, key: str) -> list[dict]:
    """Every decision of set ``key`` with its record's metadata, by record id, each with ``route``
    (:func:`~wider.screen.agreement.route_of`), ``status`` (``unresolved`` or ``final``, design D3) and ``pattern``
    (the pair of the first two reads, design D2; None with fewer than two)."""
    from ..screen.agreement import DECISIONS, _origin, _votes, reads, route_of, set_rules
    rows = [dict(r) for r in project.store.query(
        "SELECT m.record_id, m.title, m.authors, m.year, m.journal, m.doi, m.url, m.abstract, d.decision, d.reason, "
        "d.decided_by, d.votes, d.origin, d.vote_calls FROM screen_decisions d JOIN records m ON m.record_id=d.record_id "
        "WHERE d.protocol=? ORDER BY m.record_id", (key,))]
    majority = set_rules(project, key)["majority"]
    sources: dict[str, set[int]] = {}

    def split_in(src: str) -> set[int]:   # the records left at maybe by a split in the source set of a second screen
        if src not in sources:
            sources[src] = {r["record_id"] for r in project.store.query(
                "SELECT record_id FROM screen_decisions WHERE protocol=? AND decision='maybe' AND decided_by='split'",
                (src,))}
        return sources[src]

    for r in rows:
        origin, votes = _origin(r["origin"]), _votes(r["votes"])
        r["route"] = route_of(r["decided_by"], origin, votes, r["decision"], majority)
        db = r["decided_by"] or ""
        unresolved = r["decision"] == "maybe" and (
            r["route"] == "split" or (origin or {}).get("decided_by") == "split"
            or (db.startswith("carried from ") and r["record_id"] in split_in(db[len("carried from "):])))
        r["status"] = "unresolved" if unresolved else "final"
        rd = reads(votes)[:2]
        if len(rd) < 2 or not all(v in DECISIONS for v in rd):
            r["pattern"] = None
        elif rd[0] == rd[1]:
            r["pattern"] = "agreed"
        else:
            a, b = sorted(rd, key=DECISIONS.index)
            r["pattern"] = f"{a}_{b}"
    return rows


def _set_rows(project: Project, key: str, which: str) -> list[dict]:
    """The records of decision set ``which`` in set ``key``, by record id."""
    rows = _all_rows(project, key)
    if which in SETS:
        return [r for r in rows if r["decision"] in SETS[which]]
    return [r for r in rows if r["route"] == which]


def _stem(key: str) -> str:
    """The key part of a reference file's name: ``main`` for set 1, ``main.s2`` for set 2. A ``+`` in the protocol name
    stays, so ``main+human`` and ``main_human`` never share files."""
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", key.replace("#", ".s"))


def _note(protocol: str, r: dict) -> str:
    """The RIS and BibTeX note: the decision, or ``maybe (unresolved: no majority)`` for an unresolved record (D3)."""
    return f"WIDER {protocol}: {UNRESOLVED_NOTE if r.get('status') == 'unresolved' else r['decision']} (record {r['record_id']})"


def _ris(path: Path, rows: list[dict], protocol: str, abstracts: bool = True) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write("TY  - JOUR\n")
            if r["title"]:
                f.write(f"TI  - {r['title']}\n")
            for a in _authors(r["authors"]):
                f.write(f"AU  - {a}\n")
            for tag, val in (("PY", r["year"]), ("JO", r["journal"]), ("DO", r["doi"]), ("UR", r["url"]),
                             ("AB", r["abstract"] if abstracts else None)):
                if val:
                    f.write(f"{tag}  - {str(val).replace(chr(10), ' ')}\n")
            f.write(f"N1  - {_note(protocol, r)}\n")
            f.write("ER  - \n\n")


def _bib(path: Path, rows: list[dict], protocol: str, abstracts: bool = True) -> None:
    taken: set[str] = set()
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            fields = [("title", r["title"]), ("author", " and ".join(_authors(r["authors"]))), ("year", r["year"]),
                      ("journal", r["journal"]), ("doi", r["doi"]), ("url", r["url"]),
                      ("abstract", r["abstract"] if abstracts else None), ("note", _note(protocol, r))]
            body = ",\n".join(f"  {k} = {{{_bib_escape(str(v))}}}" for k, v in fields if v)
            f.write(f"@article{{{_bibkey(r, taken)},\n{body}\n}}\n\n")


def _csv(path: Path, rows: list[dict], info: dict[str, Any], abstracts: bool = True) -> None:
    """The older export's columns in their order, then ``status`` and ``pathway`` (design D1)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([r["record_id"], r["decision"], r["decided_by"] or "", r["title"], r["authors"], r["year"],
                        r["journal"], r["doi"], r["url"], r["abstract"] if abstracts else "", info["protocol"],
                        info["result_set"], r.get("status") or "final", r.get("route") or ""])


WRITERS = {"ris": _ris, "bib": _bib, "csv": _csv}


def write_format(fmt: str, path: Path, rows: list[dict], info: dict[str, Any], abstracts: bool = True) -> None:
    """Write ``rows`` of set ``info`` as one file of format ``fmt`` (ris, bib or csv)."""
    if fmt == "csv":
        _csv(path, rows, info, abstracts)
    else:
        WRITERS[fmt](path, rows, set_name(info), abstracts)


def _write_set(project: Project, info: dict[str, Any], which: str, rows: list[dict]) -> list[str]:
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{_stem(info['key'])}_{which.replace('+', '_and_')}"
    paths = []
    for fmt in ("ris", "bib", "csv"):
        write_format(fmt, out / f"{stem}.{fmt}", rows, info)
        paths.append(out / f"{stem}.{fmt}")
    return [str(x.relative_to(project.root)) for x in paths]


def _set_info(project: Project, protocol: str | None, result_set: int | None) -> dict[str, Any]:
    info = _default_protocol(project, protocol, result_set)
    if not info:
        raise ValueError("no screening decisions yet")
    return info


def export_references(project: Project, protocol: str | None = None, which: str = "include",
                      result_set: int | None = None) -> dict[str, Any]:
    """RIS, BibTeX and CSV of the records of one decision set (by decision, or by route: design D6), for a reference
    manager (Zotero, EndNote, ...). These files contain abstracts and stay on this computer; they are not part of the
    share bundle or the package."""
    from ..screen.devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    if which not in ALL_SETS:
        raise ValueError(f"which must be one of {', '.join(ALL_SETS)}")
    info = _set_info(project, protocol, result_set)
    protocol, key = set_name(info), info["key"]
    rows = _set_rows(project, key, which)
    files = _write_set(project, info, which, rows)
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="references", headline=(
        f"{len(rows):,} records ({which.replace('+', ' and ')}, protocol {protocol}) exported for a reference manager as RIS, "
        "BibTeX and CSV."), metrics={"records": len(rows)}, outputs=files, next_stage=None,
        warnings=[] if rows else [f"protocol {protocol} has no {which} records"]))
    return {"protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": key, "which": which,
            "n": len(rows), "files": files}


def export_all_references(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict[str, Any]:
    """Every decision set of one result set that holds a record, in the three formats, and ``<stem>_index.csv`` (set,
    records, files). A set without records writes no file and removes its files left from an earlier run, so the
    folder matches the index; other result sets' files are left alone."""
    from ..screen.devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    info = _set_info(project, protocol, result_set)
    name, key = set_name(info), info["key"]
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    sets: list[dict[str, Any]] = []
    for which in ALL_SETS:
        rows = _set_rows(project, key, which)
        if rows:
            files = _write_set(project, info, which, rows)
        else:
            files = []
            for ext in ("ris", "bib", "csv"):
                (out / f"{_stem(key)}_{which.replace('+', '_and_')}.{ext}").unlink(missing_ok=True)
        sets.append({"set": which, "records": len(rows), "files": files})
    index = out / f"{_stem(key)}_index.csv"
    with open(index, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["set", "records", "files"])
        for s in sets:
            w.writerow([s["set"], s["records"], ";".join(Path(x).name for x in s["files"])])
    idx = str(index.relative_to(project.root))
    written = [x for s in sets for x in s["files"]]
    full = [s["set"] for s in sets if s["records"]]
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="references", headline=(
        f"{len(full)} of {len(sets)} decision sets of protocol {name} exported for a reference manager as RIS, BibTeX "
        f"and CSV, with the index {index.name}."), metrics={"sets": len(full), "files": len(written)},
        outputs=[*written, idx], next_stage=None,
        warnings=[] if full else [f"protocol {name} has no records in any decision set"]))
    return {"protocol": info["protocol"], "result_set": info["result_set"], "result_set_key": key, "sets": sets,
            "index": idx, "files": written}


CORPUS_COLUMNS = ("record_id", "title", "authors", "year", "journal", "doi", "url", "abstract", "source_databases")
CORPUS_STEM = "corpus"


def export_corpus(project: Project) -> dict[str, Any]:
    """The plain corpus export (ui-redesign D4, owner round 4): every unique record with its bibliographic data only
    (title, authors, year, journal, DOI, URL, abstract and the source databases), as ``exports/references/corpus.ris``,
    ``.bib`` and ``.csv``. No screening decision, evaluation or model output goes in, so it is written while
    development labels are open. These files carry abstracts and stay on this computer."""
    labels = {pl.key: pl.label for pl in project.config.platforms}
    rows = [dict(r) for r in project.store.query(
        "SELECT record_id, title, authors, year, journal, doi, url, abstract, platforms FROM records ORDER BY record_id")]
    for r in rows:
        r["source_databases"] = "; ".join(labels.get(k, k) for k in (r.pop("platforms") or "").split(";") if k)
    out = project.path("exports", "references")
    out.mkdir(parents=True, exist_ok=True)
    paths = {fmt: out / f"{CORPUS_STEM}.{fmt}" for fmt in ("ris", "bib", "csv")}
    with open(paths["ris"], "w", encoding="utf-8") as f:
        for r in rows:
            f.write("TY  - JOUR\n")
            if r["title"]:
                f.write(f"TI  - {r['title']}\n")
            for a in _authors(r["authors"]):
                f.write(f"AU  - {a}\n")
            for tag, val in (("PY", r["year"]), ("JO", r["journal"]), ("DO", r["doi"]), ("UR", r["url"]),
                             ("AB", r["abstract"]), ("DB", r["source_databases"])):
                if val:
                    f.write(f"{tag}  - {str(val).replace(chr(10), ' ')}\n")
            f.write("ER  - \n\n")
    taken: set[str] = set()
    with open(paths["bib"], "w", encoding="utf-8") as f:
        for r in rows:
            fields = [("title", r["title"]), ("author", " and ".join(_authors(r["authors"]))), ("year", r["year"]),
                      ("journal", r["journal"]), ("doi", r["doi"]), ("url", r["url"]), ("abstract", r["abstract"])]
            body = ",\n".join(f"  {k} = {{{_bib_escape(str(v))}}}" for k, v in fields if v)
            f.write(f"@article{{{_bibkey(r, taken)},\n{body}\n}}\n\n")
    with open(paths["csv"], "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CORPUS_COLUMNS)
        for r in rows:
            w.writerow([r[c] if r[c] is not None else "" for c in CORPUS_COLUMNS])
    files = [str(paths[x].relative_to(project.root)) for x in ("ris", "bib", "csv")]
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="corpus_export", headline=(
        f"{len(rows):,} unique records exported with their bibliographic data only (no decisions) as RIS, BibTeX and "
        "CSV."), metrics={"records": len(rows)}, outputs=files, next_stage=None,
        warnings=[] if rows else ["the project has no records yet"]))
    return {"n": len(rows), "files": files}


# ----------------------------------------------------------------------------------------------- replication package
def write_package(project: Project, protocol: str | None = None, sample: str | None = None,
                  result_set: int | None = None, choice: str | None = None) -> dict[str, Any]:
    """One zip for a journal or a repository: the share bundle (no abstracts), the PRISMA diagram, the methods text and
    the search-strings archive, with MANIFEST.txt listing the SHA-256 of every file and ``manifest.json`` naming the
    result set of every protocol it reads. Every entry is built in memory and scanned first, for secrets and then
    for the names the attribution setting hides; on a finding nothing is written
    (:class:`~wider.core.sanitize.SecretFound`, :class:`~wider.core.sanitize.NameFound`). The zip is written to a
    temporary path and renamed at the end."""
    from ..screen.devgate import check_export

    check_export(project)   # ui-redesign D4: exports with model decisions wait
    from ..core.researchers import Attributor
    from ..core.sanitize import NameFound, SecretFound, env_names, scan_entries, scan_with
    from ..search.archive import archive_dir
    from ..search.coverage import unlocks, validity_lines, validity_note
    from .report import share_bundle

    bundle = share_bundle(project, protocol=protocol, sample=sample, result_set=result_set, choice=choice)
    att = Attributor(project)
    out = project.path("exports")
    try:
        write_prisma(project, protocol, result_set=result_set)
    except (KeyError, ValueError):
        pass
    stamp = utcnow()[:19].replace(":", "").replace("-", "")
    zpath = out / f"replication_package_{stamp}.zip"
    files: list[tuple[Path, str]] = [(f, f"share_bundle/{f.relative_to(bundle)}") for f in sorted(bundle.rglob("*")) if f.is_file()]
    for f, arc in ((out / "prisma_2020.svg", "prisma_2020.svg"), (out / "prisma_2020.json", "prisma_2020.json"),
                   (project.root / METHODS_FILE, METHODS_FILE)):
        if f.exists():
            files.append((f, arc))
    # the refinement records (2c, D17): the PRISMA-S report, the decision ledger (zero rows allowed) and the
    # provenance view of each round; never a seeds/ file, an extracted text or a cached response
    from ..interview import ledger
    from ..search import provenance
    from .prisma_s import write as write_prisma_s
    write_prisma_s(project)
    files += [(out / "prisma_s.md", "prisma_s.md"), (out / "prisma_s.json", "prisma_s.json")]
    for (n,) in project.store.query("SELECT round_no FROM query_rounds ORDER BY round_no"):
        _v, pp = provenance.write(project, n)
        files.append((pp, pp.name))
    sdir = archive_dir(project)
    if sdir.exists() and not (bundle / "search_strings").exists():
        files += [(f, f"search_strings/{f.name}") for f in sorted(sdir.iterdir()) if f.is_file()]
    entries: dict[str, bytes] = {arc: f.read_bytes() for f, arc in files}
    # the package's copy of the ledger follows the attribution setting; runs/query/ keeps names (D5)
    jl, cs = ledger.export_texts(project, att)
    entries["decisions.jsonl"], entries["decisions.csv"] = jl.encode("utf-8"), cs.encode("utf-8")
    # the PRISMA 2020 checklist (D7), built in a temporary folder so item 27 names this package and the copy in
    # exports/ stays as the researcher last wrote it; a package built before any import has none
    from .prisma_2020 import write as write_prisma_2020
    with tempfile.TemporaryDirectory() as td:
        try:
            write_prisma_2020(project, protocol, result_set, folder=Path(td), packaged=True)
        except (KeyError, ValueError):
            pass
        else:
            for name in ("prisma_2020_checklist.md", "prisma_2020_checklist.json"):
                entries[name] = (Path(td) / name).read_bytes()
    # the PRISMA reporting files (prisma-reporting-assistant D10): only what the zip root lacks, under
    # prisma_reporting/ (the CSV checklists, methods_text.md, the Word files, the flow svg and json); the root
    # checklists and prisma_2020.svg stay once each and the diagram's stage result is not recorded again
    from . import reporting
    with tempfile.TemporaryDirectory() as td:
        try:
            rep = reporting.export(project, protocol, result_set, folder=Path(td), packaged=True)
        except (KeyError, ValueError):
            pass
        else:
            for f in rep["files"]:
                entries[f"{reporting.EXPORT_DIR}/{Path(f).name}"] = Path(f).read_bytes()

    def dumps(obj: Any) -> bytes:
        return (json.dumps(att.obj(obj), indent=1, ensure_ascii=False) + "\n").encode("utf-8")

    # the unlock record (D2), only after an unlock: who reopened what, when, and which papers counted as seen
    rows = unlocks(project)
    if rows:
        entries["unlock.json"] = dumps([{**{k: r[k] for k in UNLOCK_KEYS[:-1]}, "seen_choice": r["seen_choice"] or "", "seen": [
            {k: x.get(k) for k in ("benchmark_id", "doi", "title_norm")} for x in r["seen"]]} for r in rows])
    # the query rounds (each with its counts, development recall and the seeds its acceptance lost, the frozen query
    # marked) and the limits applied in each database; neither holds a seed's title, abstract or PDF
    from ..search import limits
    from ..search import rounds as query_rounds
    rs = query_rounds.rounds(project)["rounds"]
    if rs:
        entries["rounds.json"] = dumps([{**{k: r[k] for k in ROUND_KEYS[:-1]}, "platforms": [
            {k: x[k] for k in ROUND_PLATFORM_KEYS} for x in r["platforms"]]} for r in rs])
    entries["limits.json"] = dumps(limits.limits(project))
    # which venues of the seeds' reference lists use which names, and the researcher's stream labels (2b, D11b);
    # counts and venue names only, no passage of a seed paper
    from ..seeds import streams
    st = streams.package_json(project)
    if st["venues"] or st["labels"]:
        entries["streams.json"] = dumps(st)
    # recall on independent papers and Chapman's estimate (D25): counts, benchmark ids, formulas and the caveat only
    if project.read_benchmark():
        from . import independent
        entries["independent_estimate.json"] = dumps(independent.package_json(project, protocol, result_set))
    # the configuration comparisons (D15): settings, counts and run ids; no title, abstract, response or record
    from ..screen import compare
    cmps = compare.to_json(project)
    if cmps:
        entries["comparisons.json"] = dumps(cmps)
    # sample unlocks (manual-review D5): sample, at, by and recall_seen; no record id, label or abstract
    from ..sample.labels import unlocks as sample_unlocks
    ul = sample_unlocks(project)
    if ul:
        entries["sample_unlocks.json"] = dumps(ul)
    sets_read = package_sets(project, protocol, result_set)
    manifest = [f"WIDER replication package for {project.config.name}", f"created {utcnow()}",
                "Contains no abstracts, no database exports and no raw model responses.", "", "sha256  file"]
    manifest += [f"{hashlib.sha256(b).hexdigest()}  {arc}" for arc, b in entries.items()]
    # every imported export by name and size (the exports themselves stay out), and whether it entered deduplication
    imported = [{"source_id": r["source_id"], "platform": r["platform"], "file": r["file_name"],
                 "records": r["n_records"], "included": bool(r["included"]), "sample_of": r["sample_of"]}
                for r in project.store.query("SELECT source_id, platform, file_name, n_records, included, sample_of "
                                             "FROM sources ORDER BY source_id")]
    from ..screen.protocol import pass_models
    mjson = {"project": project.config.name, "created": utcnow(), "attribution": att.mode, "result_sets": sets_read,
             "imported_files": imported,
             "models_per_pass": {x["protocol"]: pass_models(project, x["key"])["models"] for x in sets_read},
             "files": [{"file": arc, "sha256": hashlib.sha256(b).hexdigest()} for arc, b in entries.items()]}
    if project.read_benchmark():   # the validity note (D3), as in the report
        note = validity_note(project)
        mjson["search_validity"] = att.obj({**note, "text": validity_lines(note)})
    entries["MANIFEST.txt"] = ("\n".join(manifest) + "\n").encode("utf-8")
    entries["manifest.json"] = (json.dumps(mjson, indent=1) + "\n").encode("utf-8")
    found = scan_entries(list(entries.items()), env_names(project.config))
    if not found:
        names = scan_with([(arc, b) for arc, b in entries.items() if not arc.startswith("share_bundle/")], att)
        if names:   # the bundle was scanned when it was written
            shutil.rmtree(bundle, ignore_errors=True)
            raise NameFound(names)
    if found:
        shutil.rmtree(bundle, ignore_errors=True)
        raise SecretFound(found)
    tmp = zpath.with_name(zpath.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for arc, b in entries.items():
                z.writestr(arc, b)
        tmp.replace(zpath)
    except BaseException:
        tmp.unlink(missing_ok=True)
        zpath.unlink(missing_ok=True)
        raise
    n_files = len(entries)
    rel = str(zpath.relative_to(project.root))
    from ..core.stage_result import StageResult, record
    record(project, StageResult(stage="recall", action="package", headline=(
        f"Replication package written with {n_files} files (share bundle, PRISMA diagram, methods text, search strings)."),
        metrics={"files": n_files, "size_kb": round(zpath.stat().st_size / 1024)}, outputs=[rel], next_stage=None,
        warnings=[] if (project.root / METHODS_FILE).exists() else ["No methods.md yet: draft and save the methods paragraph to include it."]))
    last = json.loads(project.store.get_meta("last_report") or "{}")   # the report the bundle just wrote (D8)
    pend = {"choice_needed": True, "notice": last.get("notice")} if last.get("choice_needed") else {}
    return {"path": rel, "n_files": n_files, "size_bytes": zpath.stat().st_size, "result_sets": sets_read,
            "attribution": att.mode, **pend}


def package_sets(project: Project, protocol: str | None, result_set: int | None) -> list[dict[str, Any]]:
    """The result set a package or report reads for each protocol: the named one for ``protocol``, else the active."""
    from ..screen.decisions import protocols
    from ..screen.result_sets import reader

    out = []
    for pr in protocols(project):
        info = reader(project, pr["protocol"], result_set if pr["protocol"] == protocol else None)
        out.append({k: info[k] for k in ("protocol", "key", "result_set", "label", "role", "mixed")})
    return out
