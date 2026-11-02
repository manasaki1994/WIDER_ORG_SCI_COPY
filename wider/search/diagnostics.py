"""Search diagnostics read by the dry run, the validity note and the Recall page (vision change 2a, D7).

:func:`clopper_pearson` gives the exact binomial interval for a recall of ``found`` out of ``total`` known papers, so a
small validation set is reported with its uncertainty (ten of ten found is 0.69 to 1.00, not "100 %").
:func:`grid` lays the terms against the development seeds per platform, :func:`minimal_cover` picks a smallest set of
terms that keeps every retrieved seed retrieved, and :func:`truncation_report` lists what each ``*`` matched on the
seeds and what a stem one letter shorter or longer (or a spelling wildcard) would gain or lose. :func:`attach` adds
all three to a dry run, so `wider query dry-run --terms|--grid` and the "Dry run" card read the same result.
"""
from __future__ import annotations

import bisect
import csv
import io
import json
import re


def clopper_pearson(found: int, total: int, level: float = 0.95) -> tuple[float, float] | None:
    """The exact (Clopper-Pearson) interval for ``found`` of ``total``; None when ``total`` is 0. The equal-weight case
    of the recall interval (``wider.recall.uncertainty``, change recall-interval-honest D7)."""
    from ..recall.uncertainty import beta_interval

    if total <= 0:
        return None
    if not 0 <= found <= total:
        raise ValueError("found must be between 0 and total")
    return beta_interval(found, total, level)


KNOWN_PAPERS_NOTE = ("Recall on known papers overstates recall on the scattered literature: the papers a researcher "
                     "already knows are the ones a search finds most easily.")


def recall_text(found: int, total: int) -> str:
    """"10 of 10, 95% interval 0.69 to 1.00" (``wider coverage``, the diagnosis, the report and their cards)."""
    ci = clopper_pearson(found, total)
    return f"{found} of {total}" + (f", 95% interval {ci[0]:.2f} to {ci[1]:.2f}" if ci else "")


# ----------------------------------------------------------------- grid and minimal cover (D7)
NO_SEED = "matches no seed paper"


def _positive_blocks(query) -> list[str]:
    from .evaluate import _positive_refs

    return list(dict.fromkeys(_positive_refs(query.logic)))


def grid(query, platform_result: dict) -> dict:
    """Terms (every block, in block and term order) against the development seeds of one platform: a cell is
    ``hit``, ``uncertain`` (the term matched, but the profile cannot decide) or ``""``."""
    from .evaluate import term_label

    seeds = [s for s in platform_result["seeds"] if s["set"] == "Development"]
    positive = set(_positive_blocks(query))
    rows = []
    for b in query.blocks:
        for t in b.terms:
            label = term_label(t)
            cells = []
            for s in seeds:
                blk = s["blocks"].get(b.key, {})
                cells.append("hit" if label in blk.get("hits", []) else
                             "uncertain" if label in blk.get("words", {}) else "")
            rows.append({"block": b.key, "block_name": b.name or b.key, "term": label, "positive": b.key in positive,
                         "cells": cells})
    return {"seeds": [s["benchmark_id"] for s in seeds], "retrieved": [s["benchmark_id"] for s in seeds
                                                                      if s["state"] == "hit"], "rows": rows}


def minimal_cover(g: dict) -> dict:
    """A smallest set of terms that keeps every retrieved seed retrieved: per block of the query (not under NOT), a
    greedy set cover of the retrieved seeds that block hits, ties broken by term order in the block, then
    alphabetically. ``no_seed`` lists the terms of those blocks that match no seed paper at all."""
    retrieved = set(g["retrieved"])
    cover: list[dict] = []
    no_seed: list[dict] = []
    blocks: dict[str, list[tuple[int, dict]]] = {}
    for i, r in enumerate(g["rows"]):
        if r["positive"]:
            blocks.setdefault(r["block"], []).append((i, r))
    for key, rows in blocks.items():
        hits = {i: {g["seeds"][j] for j, c in enumerate(r["cells"]) if c == "hit"} & retrieved for i, r in rows}
        left = set().union(*hits.values()) if hits else set()
        chosen: list[int] = []
        while left:
            best = min((i for i, _ in rows if i not in chosen),
                       key=lambda i: (-len(hits[i] & left), i, g["rows"][i]["term"]))
            if not hits[best] & left:
                break
            chosen.append(best)
            left -= hits[best]
        cover += [{"block": key, "term": g["rows"][i]["term"], "seeds": sorted(hits[i])} for i in chosen]
        no_seed += [{"block": key, "term": r["term"]} for i, r in rows if not any(r["cells"])]
    return {"cover": cover, "no_seed": no_seed}


def grid_csv(grids: dict[str, dict]) -> str:
    """``grid.csv``: one row per platform and term, one column per seed (1 hit, ? uncertain, empty no match)."""
    seeds = list(dict.fromkeys(s for g in grids.values() for s in g["seeds"]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["platform", "block", "term", *seeds])
    for plat, g in grids.items():
        col = {s: j for j, s in enumerate(g["seeds"])}
        for r in g["rows"]:
            w.writerow([plat, r["block"], r["term"],
                        *[{"hit": "1", "uncertain": "?"}.get(r["cells"][col[s]], "") if s in col else "" for s in seeds]])
    return buf.getvalue()


# ----------------------------------------------------------------- truncation and term report (D7)
_VARIANT = re.compile(r"(?<=i)[sz](?=[aei])")   # -isation / -ization, -ise / -ize


def _pattern_re(piece: str, profile) -> re.Pattern:
    from .evaluate import _piece_regex

    return _piece_regex(piece.lower(), profile)


def _matching(words: list[str], piece: str, profile) -> list[str]:
    """The words of the sorted list ``words`` a truncated piece matches. Only the words that start with the piece's
    literal start are tried (a bisect), not the whole vocabulary for every term."""
    from .evaluate import _compiled

    start, rx = _compiled(piece.lower(), profile.max_truncation)
    out = []
    for w in words[bisect.bisect_left(words, start):]:
        if not w.startswith(start):
            break
        if rx.fullmatch(w):
            out.append(w)
    return out


def _variant_patterns(stem: str, words: list[str]) -> list[str]:
    """Spelling wildcards narrower than the stem: an s/z after "i" past the stem becomes ``?``, cut where the shortest
    such form ends (``organization``, ``organizational`` → ``organi?ation*``)."""
    forms = sorted({w[:len(stem)] + _VARIANT.sub("?", w[len(stem):]) for w in words})
    forms = [f for f in forms if "?" in f]
    out: list[str] = []
    for f in forms:
        if not any(f.startswith(o[:-1]) for o in out):
            out.append(f + "*")
    return out


def _vocabulary(docs, fields: list[str]) -> dict[str, set[str]]:
    vocab: dict[str, set[str]] = {}
    for d in docs:
        for f in fields:
            for tok in d.text(f) or []:
                vocab.setdefault(tok.word, set()).add(d.benchmark_id)
    return vocab


def _pieces(term) -> list[str]:
    from .evaluate import term_label

    return list(dict.fromkeys(w for w in re.split(r"[\s()\"]+|\bOR\b|\b(?:NEAR|PRE)/\d+", term_label(term))
                              if w and ("*" in w or "?" in w)))


def truncation_report(query, docs, profile, platform_result: dict | None = None) -> list[dict]:
    """Per term: the seeds it hits (when ``platform_result`` is given) and, per truncated word, the seed words it
    matches (with the seeds holding them), what one letter less would gain, what each one-letter-longer stem keeps
    and loses, and spelling wildcards that keep some words and drop others."""
    from .evaluate import _stem_len, record_fields, term_label

    out: list[dict] = []
    seeds = (platform_result or {}).get("seeds", [])
    for b in query.blocks:
        vocab = _vocabulary(docs, record_fields(b.fields, profile))
        words = sorted(vocab)
        for t in b.terms:
            label = term_label(t)
            hit = [s["benchmark_id"] for s in seeds if label in s["blocks"].get(b.key, {}).get("hits", [])]
            unc = [s["benchmark_id"] for s in seeds if label in s["blocks"].get(b.key, {}).get("words", {})
                   and s["benchmark_id"] not in hit]
            stems = []
            for piece in _pieces(t):
                matched = _matching(words, piece, profile)
                n = _stem_len(piece)
                stem, rest = piece[:n].lower(), piece[n:]
                entry = {"piece": piece, "words": [{"word": w, "seeds": sorted(vocab[w])} for w in matched],
                         "shorter": None, "longer": [], "variants": []}
                if rest.startswith("*") and n > 1:
                    pat = stem[:-1] + rest
                    have = set(matched)
                    gains = [w for w in _matching(words, pat, profile) if w not in have]
                    entry["shorter"] = {"pattern": pat, "gains": gains,
                                        "under_minimum": bool(profile.min_stem and n - 1 < profile.min_stem)}
                if rest.startswith("*"):
                    for letter in sorted({w[n] for w in matched if len(w) > n}):
                        pat = stem + letter + rest
                        rx = _pattern_re(pat, profile)
                        keeps = [w for w in matched if rx.fullmatch(w)]
                        entry["longer"].append({"pattern": pat, "keeps": keeps,
                                                "loses": [w for w in matched if w not in keeps]})
                    for pat in _variant_patterns(stem, matched):
                        rx = _pattern_re(pat, profile)
                        keeps = [w for w in matched if rx.fullmatch(w)]
                        if keeps and len(keeps) < len(matched):
                            entry["variants"].append({"pattern": pat, "keeps": keeps,
                                                      "drops": [w for w in matched if w not in keeps]})
                stems.append(entry)
            out.append({"block": b.key, "term": label, "seeds_hit": hit, "seeds_uncertain": unc, "stems": stems})
    return out


def term_lines(report: list[dict]) -> list[str]:
    """The term report as text lines: the CLI's `--terms` output and the card's expanded term row."""
    lines = []
    for r in report:
        head = f"{r['block']}: {r['term']}: hits {', '.join(r['seeds_hit']) or 'no seed'}"
        if r["seeds_uncertain"]:
            head += f"; uncertain {', '.join(r['seeds_uncertain'])}"
        lines.append(head)
        for s in r["stems"]:
            words = ", ".join(f"{w['word']} ({', '.join(w['seeds'])})" for w in s["words"]) or "no word of the seeds"
            lines.append(f"  {s['piece']} matches {words}")
            if s["shorter"] is not None:
                sh = s["shorter"]
                note = " (under the platform's minimum stem)" if sh["under_minimum"] else ""
                lines.append(f"  {sh['pattern']} would also match {', '.join(sh['gains']) or 'nothing more'}{note}")
            for lo in s["longer"]:
                if lo["loses"]:
                    lines.append(f"  {lo['pattern']} would keep {', '.join(lo['keeps'])} and lose {', '.join(lo['loses'])}")
            for v in s["variants"]:
                lines.append(f"  {v['pattern']} would keep {', '.join(v['keeps'])} and drop {', '.join(v['drops'])}")
    return lines


def grid_lines(g: dict, cover: dict) -> list[str]:
    """The grid as text (one row per term, one column per development seed: x hit, ? uncertain, . none), then the
    minimal cover and the terms that match no seed paper: the CLI's `--grid` output and the card's grid."""
    w = max([len(f"{r['block']}: {r['term']}") for r in g["rows"]] + [4])
    lines = [" " * w + "  " + " ".join(g["seeds"])]
    for r in g["rows"]:
        cells = " ".join({"hit": "x", "uncertain": "?"}.get(c, ".").center(len(s)) for c, s in zip(r["cells"], g["seeds"],
                                                                                                    strict=True))
        lines.append(f"{r['block']}: {r['term']}".ljust(w) + "  " + cells)
    cov = "; ".join(f"{c['block']}: {c['term']}" for c in cover["cover"]) or "none (no seed is retrieved)"
    lines.append(f"minimal cover: {cov}")
    if cover["no_seed"]:
        lines.append(f"{NO_SEED}: " + "; ".join(f"{c['block']}: {c['term']}" for c in cover["no_seed"]))
    return lines


def attach(out: dict, query, docs, profiles, run_dir=None) -> None:
    """Add the grid, the minimal cover, the terms without a seed and the term report to each platform of a dry run;
    with ``run_dir``, write ``grid.csv``, ``grid.svg`` (the heatmap) and ``truncation.json`` there and name them in ``grid_files``."""
    from . import plots

    grids = {}
    for prof in profiles:
        pl = out["platforms"][prof.platform]
        g = grid(query, pl)
        cover = minimal_cover(g)
        pl["grid"] = g
        pl.update(cover)
        pl["terms_report"] = truncation_report(query, docs, prof, pl)
        pl["grid_text"] = grid_lines(g, cover)
        pl["terms_text"] = term_lines(pl["terms_report"])
        grids[prof.platform] = g
    out["grid_svg"] = plots.heatmap(grids)
    if run_dir is not None:
        (run_dir / "grid.csv").write_text(grid_csv(grids), encoding="utf-8")
        (run_dir / "grid.svg").write_text(out["grid_svg"], encoding="utf-8")
        report = {k: pl["terms_report"] for k, pl in out["platforms"].items()}
        (run_dir / "truncation.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------- what did what, clusters and noise (D9)
NO_TERM_NOTE = "retrieved by the database for a reason the profile does not see"
NO_TERM = "(no term)"
MARKS = ("relevant", "noise")


class DiagnoseError(ValueError):
    """A diagnosis that cannot run (no export imported against a synonyms version, an unknown cluster): exit 1 or
    HTTP 400."""


def unrecorded_imports(project) -> int:
    """Files in deduplication imported with no synonyms version recorded (imported before any version existed)."""
    return int(project.store.scalar("SELECT COUNT(*) FROM sources WHERE included=1 AND query_version IS NULL") or 0)


def unrecorded_text(n: int) -> str:
    """Why a diagnosis has no export to read while ``n`` files are imported without a synonyms version."""
    files = "file was" if n == 1 else "files were"
    return (f"the {n} imported {files} imported before any synonyms version existed, so no version is recorded for "
            f"{'it' if n == 1 else 'them'} and the diagnosis cannot tell which search found the records; files "
            f"imported from now on are recorded under the frozen query")


def _target(project, version: int | None, platform: str | None) -> tuple[int, list[str]]:
    """The synonyms version to diagnose (default: the newest one an export was imported against) and its platforms,
    the project's own first. Only files that enter deduplication count (a file kept out is not part of the search)."""
    rows = project.store.query("SELECT DISTINCT platform, query_version FROM sources "
                               "WHERE included=1 AND query_version IS NOT NULL")
    pairs = {(r["platform"], int(r["query_version"])) for r in rows}
    if not pairs:
        n = unrecorded_imports(project)
        raise DiagnoseError(unrecorded_text(n) if n else
                            "no export imported against a synonyms version yet; import one on the Search results page")
    if version is None:
        version = max(v for _, v in pairs)
    plats = {p for p, v in pairs if v == version}
    if not plats:
        raise DiagnoseError(f"no export imported against synonyms v{version}")
    if platform is not None:
        if platform not in plats:
            raise DiagnoseError(f"no {platform} export imported against synonyms v{version}")
        return version, [platform]
    order = [p.key for p in project.config.platforms]
    return version, sorted(plats, key=lambda p: (order.index(p) if p in order else len(order), p))


def shown_seeds(project) -> tuple[list[tuple[str, object]], bool]:
    """``(set, paper)`` for the seeds a diagnostic may read (D9, F2), and whether the validation set is shown: the
    development papers, the validation set only while it is shown and the used validation papers as ``USED`` rows
    (recall per set and the known-item lists only: nothing that shapes the query reads them, validation-reuse D1);
    papers from the query's results are in none."""
    from . import coverage

    papers = project.read_benchmark()
    out = [("Development", b) for b in coverage.development_papers(project, papers)]
    shown = coverage.validation_unlocked(project)
    if shown:
        out += [("Validation", b) for b in coverage.validation_papers(project, papers)]
    out += [(coverage.USED, b) for b in coverage.used_papers(project, papers)]
    return out, shown


def _signature(res, positive: list[str], query) -> list[str]:
    from .evaluate import term_label

    out = []
    for b in query.blocks:
        if b.key in positive:
            matched = res.per_block[b.key].words
            out += [f"{b.key}: {term_label(t)}" for t in b.terms if term_label(t) in matched]
    return out


def what_did_what(project, version: int | None = None, platform: str | None = None) -> dict:
    """Per platform imported against ``version``: the records each block and term retrieved (a record counts for every
    term it matches in title, abstract or keywords under the platform's profile), the records only one term of a block
    retrieved, the records no term explains, the clusters (records grouped by the terms that matched them, with three
    sample titles, the seeds in them and the researcher's mark), the noise terms and recall per set (D9). A term is noise
    when no record it retrieved holds a term of the same block that a shown seed matches (on the seed itself or on its
    record in the export) and no cluster holding it is marked relevant."""
    from ..construct import artifacts
    from ..core.matching import CorpusRecord, locate_benchmark
    from . import coverage
    from .evaluate import NoProfile, Query, SeedDoc, _abstracts, evaluate, load_profile, seed_doc, term_label

    version, plats = _target(project, version, platform)
    row, data = artifacts.load(project, "synonyms", version)
    if row is None:
        raise DiagnoseError(f"no synonyms version {version}")
    query = Query.model_validate(data)
    positive = _positive_blocks(query)
    seeds, shown = shown_seeds(project)
    used = {b.benchmark_id for s, b in seeds if s == coverage.USED}
    ab = _abstracts(project)
    st = project.store
    marks = {(r["platform"], r["signature"]): dict(r) for r in
             st.query("SELECT * FROM cluster_marks WHERE version=?", (version,))}
    out: dict = {"version": version, "validation_shown": shown, "budget": budget(project, version), "platforms": {},
                 "known_papers_note": KNOWN_PAPERS_NOTE}
    from .indexing import group_of, verdicts
    held = verdicts(project)   # the researcher's indexing verdicts (D10), read for the missed seeds below
    sampled = sample_weights(project, version)
    for plat in plats:
        try:
            prof = load_profile(plat, query)
        except NoProfile:
            out["platforms"][plat] = {"profile": False, "note": f"no profile for {plat}: not diagnosed"}
            continue
        recs = st.query("SELECT r.raw_id, r.title, r.abstract, r.keywords, r.doi, s.source_id FROM raw_records r "
                        "JOIN sources s ON s.source_id=r.source_id WHERE s.platform=? AND s.query_version=? "
                        "AND s.included=1 ORDER BY s.source_id, r.position", (plat, version))
        found = {m.benchmark_id: m for m in locate_benchmark(
            [(b.benchmark_id, b.title, b.doi) for _, b in seeds],
            [CorpusRecord(str(r["raw_id"]), r["title"] or "", r["doi"]) for r in recs],
            jw_threshold=project.config.thresholds.jw_benchmark) if m.method != "none"}
        seeds_of: dict[str, list[str]] = {}   # clusters and noise read no used validation paper (D1)
        for bid, m in found.items():
            if bid not in used:
                seeds_of.setdefault(str(m.record_id), []).append(bid)
        terms = {(b.key, term_label(t)): {"block": b.key, "term": term_label(t), "records": 0, "only": 0}
                 for b in query.blocks if b.key in positive for t in b.terms}
        blocks = {k: 0 for k in positive}
        no_term = unc = 0
        # a sampled file's records weigh N/K (D5, D6); the plain counts stay under the old keys
        sw = sampled.get(plat) or {}
        wts = sw.get("weights", {}) if sw.get("sampled") else {}
        est: dict = {"terms": dict.fromkeys(terms, 0.0), "only": dict.fromkeys(terms, 0.0),
                     "blocks": dict.fromkeys(positive, 0.0), "no_term": 0.0, "uncertain": 0.0, "clusters": {}}
        clusters: dict[str, dict] = {}
        rec_sigs: list[tuple[list[str], list[str]]] = []
        for r in recs:
            res = evaluate(query, SeedDoc(str(r["raw_id"]), "", r["title"] or "", r["abstract"] or "",
                                          r["keywords"] or ""), prof)
            sig = _signature(res, positive, query)
            w = wts.get(r["source_id"], 1.0)
            for k in positive:
                matched = list(res.per_block[k].words)   # hit and uncertain terms: either may have retrieved it
                if res.per_block[k].state != "miss":
                    blocks[k] += 1
                    est["blocks"][k] += w
                for lab in matched:
                    if (k, lab) in terms:
                        terms[(k, lab)]["records"] += 1
                        est["terms"][(k, lab)] += w
                if len(matched) == 1 and (k, matched[0]) in terms:
                    terms[(k, matched[0])]["only"] += 1
                    est["only"][(k, matched[0])] += w
            no_term += res.state == "miss"
            unc += res.state == "uncertain"
            est["no_term"] += w * (res.state == "miss")
            est["uncertain"] += w * (res.state == "uncertain")
            key = "; ".join(sorted(sig))
            c = clusters.setdefault(key, {"signature": key, "terms": sorted(sig), "size": 0, "samples": [], "seeds": []})
            c["size"] += 1
            est["clusters"][key] = est["clusters"].get(key, 0.0) + w
            if len(c["samples"]) < 3 and r["title"]:
                c["samples"].append(r["title"])
            c["seeds"] += seeds_of.get(str(r["raw_id"]), [])
            rec_sigs.append((sig, seeds_of.get(str(r["raw_id"]), [])))
        ordered = sorted(clusters.values(), key=lambda c: (-c["size"], not c["signature"], c["signature"]))
        for i, c in enumerate(ordered, 1):
            m = marks.get((plat, c["signature"]))
            c.update({"id": f"C{i}", "label": c["signature"] or NO_TERM, "seeds": sorted(set(c["seeds"])),
                      "mark": m["mark"] if m else None, "by": m["by"] if m else None, "at": m["at"] if m else None})
        # the terms any shown seed matches: under the profile on the seed itself, and on its record in the export
        seed_terms = {t for s, b in seeds if s != coverage.USED for t in _signature(
            evaluate(query, seed_doc(b, None, ab.get(b.benchmark_id)), prof), positive, query)}
        seed_terms |= {t for sig, held in rec_sigs if held for t in sig}
        relevant = {t for c in ordered if c["mark"] == "relevant" for t in c["terms"]}
        n = len(recs)
        n_est = sum(wts.get(r["source_id"], 1.0) for r in recs) if wts else n
        noise = []
        for (k, lab), t in terms.items():
            name = f"{k}: {lab}"
            if not t["records"] or name in relevant:
                continue
            # within the term's block: in an AND query every record shares the other blocks' terms with the seeds
            mine = [{t for t in sig if t.startswith(f"{k}: ")} for sig, _ in rec_sigs if name in sig]
            if any(sig & seed_terms for sig in mine):
                continue
            x_est = est["terms"][(k, lab)] if wts else t["records"]
            noise.append({"block": k, "term": lab, "records": t["records"],
                          "share": round(x_est / n_est, 4) if n else 0, **({"est": x_est} if wts else {})})
        recall = {}
        for group in ("Development", "Validation", coverage.USED):
            ids = [b.benchmark_id for s, b in seeds if s == group]
            if not ids:   # a set with no paper (or the validation set while hidden) has no recall line
                continue
            k = sum(1 for i in ids if i in found)
            if wts:   # a seed missing from a random sample may still be retrieved: not missed, recall not measured
                recall[group] = {"found": k, "total": len(ids), "interval": None, "missed": [], "sampled": True,
                                 "not_in_sample": [i for i in ids if i not in found],
                                 "text": f"{k} of {len(ids)} ({NOT_IN_SAMPLE})"}
                continue
            ci = clopper_pearson(k, len(ids))
            recall[group] = {"found": k, "total": len(ids), "interval": list(ci) if ci else None,
                             "missed": [i for i in ids if i not in found]}
        entry = {
            "profile": True, "records": n, "blocks": [{"block": k, "records": blocks[k]} for k in positive],
            "terms": list(terms.values()), "no_term": no_term, "uncertain": unc, "no_term_note": NO_TERM_NOTE,
            "clusters": ordered, "noise": noise, "recall": recall,
            "indexing": _indexing_groups(plat, [i for r in recall.values() for i in r["missed"]], held, group_of)}
        if wts:
            _add_estimates(entry, plat, sw, est, n_est)
        out["platforms"][plat] = entry
    return out


NOT_IN_SAMPLE = "a seed not in the sample may still be retrieved; recall not measured"


def _count_text(n: int, x: float, sw: dict, unit: str = " records") -> str:
    """"about 156,000 records (300 in the sample)": an estimate and the records it stands on (D6); none is "0"."""
    if not n:
        return f"0{unit}"
    return f"about {about(x)}{unit} ({n:,} {'imported' if sw['mixed'] else 'in the sample'})"


def _heading(plat: str, sw: dict) -> str:
    """"openalex: 1,249 records imported, a random sample of 650,000 results; the counts below are estimates: records
    in the sample × 650,000 / 1,249" (D6)."""
    if sw["sample"]:
        s = sw["sample"]
        files = f" in {s['files']} files" if s["files"] > 1 else ""
        return (f"{plat}: {s['k']:,} records imported{files}, a random sample of {s['of']:,} results; the counts below "
                f"are estimates: records in the sample × {s['of']:,} / {s['k']:,}")
    parts = "; ".join(f"{g['k']:,} of them a random sample of {g['of']:,} results (counted × {g['of']:,} / {g['k']:,})"
                      for g in sw["groups"])
    rest = f"; {sw['complete']:,} from complete exports (counted once)" if sw["complete"] else ""
    return (f"{plat}: {sw['imported']:,} records imported, {parts}{rest}; the counts below are estimates, about "
            f"{about(sw['estimated'])} records in all")


def _add_estimates(entry: dict, plat: str, sw: dict, est: dict, n_est: float) -> None:
    """The estimates beside the plain counts of a platform with a sampled file, and the sentences the CLI and the card
    print (D6)."""
    entry.update({"estimated": True, "sample": sw["sample"], "mixed": sw["mixed"], "records_est": n_est,
                  "heading": _heading(plat, sw)})
    for b in entry["blocks"]:
        b["est"] = est["blocks"][b["block"]]
        b["text"] = _count_text(b["records"], b["est"], sw)
    for t in entry["terms"]:
        key = (t["block"], t["term"])
        t["est"], t["only_est"] = est["terms"][key], est["only"][key]
        t["text"] = _count_text(t["records"], t["est"], sw)
        t["only_text"] = _count_text(t["only"], t["only_est"], sw, "") if t["only"] else ""
    entry["no_term_est"], entry["uncertain_est"] = est["no_term"], est["uncertain"]
    entry["no_term_text"] = _count_text(entry["no_term"], est["no_term"], sw, "")
    entry["uncertain_text"] = _count_text(entry["uncertain"], est["uncertain"], sw, "") if entry["uncertain"] else ""
    for x in entry["noise"]:
        x["text"] = f"{_count_text(x['records'], x['est'], sw)}, {_pct(x['share'])} of {plat}"
    for c in entry["clusters"]:
        c["est"] = est["clusters"][c["signature"]]
        c["text"] = _count_text(c["size"], c["est"], sw)


INDEXING_GROUPS = {"not_indexed": "not in the database", "indexed_missed": "indexed but not retrieved",
                   "unknown": "indexing unknown", "unchecked": "indexing not checked yet"}


def _indexing_groups(plat: str, missed: list[str], held: dict, group_of) -> dict[str, list[str]]:
    """The missed shown seeds of ``plat`` by the researcher's indexing verdict (only the groups that hold a seed)."""
    groups: dict[str, list[str]] = {}
    for bid in missed:
        groups.setdefault(group_of((held.get((bid, plat)) or {}).get("verdict")), []).append(bid)
    return {g: groups[g] for g in INDEXING_GROUPS if g in groups}


def _find_cluster(diag: dict, key: str, platform: str | None) -> tuple[str, dict]:
    hits = [(p, c) for p, pl in diag["platforms"].items() if pl.get("profile") and (platform in (None, p))
            for c in pl["clusters"] if key in (c["id"], c["signature"], c["label"])]
    if not hits:
        raise DiagnoseError(f"no cluster {key}" + (f" on {platform}" if platform else "")
                            + f" for synonyms v{diag['version']}; `wider query clusters` lists them")
    if len(hits) > 1:
        raise DiagnoseError(f"cluster {key} exists on " + ", ".join(p for p, _ in hits) + "; name the platform")
    return hits[0]


def mark_cluster(project, key: str, mark: str, platform: str | None = None, version: int | None = None,
                 by: str | None = None) -> dict:
    """Mark a cluster (its id such as ``C2``, or its signature) ``relevant`` or ``noise``: stored with the author,
    journalled ``cluster_mark`` (undoable). No screening decision is made for its records (D9)."""
    from ..core import journal

    if mark not in MARKS:
        raise DiagnoseError(f"mark must be relevant or noise, not {mark}")
    diag = what_did_what(project, version, platform)
    plat, c = _find_cluster(diag, key, platform)
    v = diag["version"]
    author = journal.current_author(by)
    with journal.recording(project, "cluster_mark", f'{plat} v{v} cluster "{c["label"]}" marked {mark}',
                           author=by) as rec:
        rec.rows("cluster_marks", "platform=? AND version=? AND signature=?", [plat, v, c["signature"]])
        if c["mark"] != mark:
            _apply_mark(project, plat, v, c["signature"], mark, author)
    return {**c, "platform": plat, "version": v, "mark": mark, "by": author if c["mark"] != mark else c["by"],
            "change_id": rec.change_id}


def _apply_mark(project, platform: str, version: int, signature: str, mark: str, author: str) -> None:
    """Store a cluster mark (inside the caller's journalled change: a ``refine_boundary_record`` answer is one
    ``answer`` change, F3)."""
    from ..core.provenance import utcnow

    project.store.execute("INSERT INTO cluster_marks(platform,version,signature,mark,by,at) VALUES(?,?,?,?,?,?) "
                          "ON CONFLICT(platform,version,signature) DO UPDATE SET mark=excluded.mark, "
                          "by=excluded.by, at=excluded.at", (platform, version, signature, mark, author, utcnow()))


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def diagnose_lines(diag: dict) -> list[str]:
    """`wider query diagnose` as text: per platform the records, the counts per block and term (``only`` marks the
    records that term alone retrieved in its block), the records no term explains, recall per set with its interval,
    and the noise terms. The card shows the same numbers."""
    lines = [f"synonyms v{diag['version']}" + ("" if diag["validation_shown"] else
                                               " (validation set hidden: development seeds only)")]
    if diag.get("budget"):
        lines.append(diag["budget"]["text"])
    for plat, pl in diag["platforms"].items():
        if not pl.get("profile"):
            lines.append(f"{plat}: {pl['note']}")
            continue
        if pl.get("estimated"):   # a sampled file: every count is an estimate with the records it stands on (D6)
            lines.append(pl["heading"])
            for b in pl["blocks"]:
                lines.append(f"  block {b['block']}: {b['text']}")
            for t in pl["terms"]:
                only = f", {t['only_text']} by this term alone" if t["only"] else ""
                lines.append(f"    {t['block']}: {t['term']}: {t['text']}{only}")
            lines.append(f"  no term explains: {pl['no_term_text']} ({NO_TERM_NOTE})")
            if pl["uncertain"]:
                lines.append(f"  uncertain under the profile: {pl['uncertain_text']}")
        else:
            lines.append(f"{plat}: {pl['records']} records")
            for b in pl["blocks"]:
                lines.append(f"  block {b['block']}: {b['records']} records")
            for t in pl["terms"]:
                only = f", {t['only']} by this term alone" if t["only"] else ""
                lines.append(f"    {t['block']}: {t['term']}: {t['records']} records{only}")
            lines.append(f"  no term explains: {pl['no_term']} ({NO_TERM_NOTE})")
            if pl["uncertain"]:
                lines.append(f"  uncertain under the profile: {pl['uncertain']}")
        for group, r in pl["recall"].items():
            name = "used validation (not independent)" if group == "Validation, used" else group.lower()
            if r.get("sampled"):
                lines.append(f"  {name} seeds in the sample: {r['text']}")
                continue
            miss = f"; missed {', '.join(r['missed'])}" if r["missed"] else ""
            lines.append(f"  {name} seeds found: {recall_text(r['found'], r['total'])}{miss}")
        for g, ids in pl.get("indexing", {}).items():   # the indexing verdicts of the missed seeds (D10)
            lines.append(f"    {INDEXING_GROUPS[g]}: {', '.join(ids)}")
        if pl["noise"]:
            lines.append("  Noise (no seed and no cluster marked relevant shares a term of the same block with these records):")
            for x in pl["noise"]:
                lines.append(f"    {x['block']}: {x['term']}: " + (x["text"] if "text" in x else
                                                                 f"{x['records']} records, {_pct(x['share'])} of {plat}"))
        else:
            lines.append("  Noise: none")
    if any(pl.get("recall") for pl in diag["platforms"].values()):
        lines.append(diag.get("known_papers_note") or KNOWN_PAPERS_NOTE)
    return lines


def cluster_lines(diag: dict) -> list[str]:
    """`wider query clusters` as text: per platform each cluster's id, size, terms, mark, seeds and sample titles."""
    lines = [f"synonyms v{diag['version']}"]
    for plat, pl in diag["platforms"].items():
        if not pl.get("profile"):
            lines.append(f"{plat}: {pl['note']}")
            continue
        lines.append(f"{plat}: {len(pl['clusters'])} clusters")
        if pl.get("estimated"):
            lines.append(f"  {pl['heading']}")
        for c in pl["clusters"]:
            mark = (f" [{c['mark']}" + (f" by {c['by']}" if c["by"] else "") + "]") if c["mark"] else ""
            seeds = f"; seeds {', '.join(c['seeds'])}" if c["seeds"] else ""
            lines.append(f"  {c['id']} ({c.get('text') or str(c['size']) + ' records'}){mark}: {c['label']}{seeds}")
            for s in c["samples"]:
                lines.append(f"      {s}")
    return lines


# ----------------------------------------------------------------- the record budget and suggested edits (D10)
NO_IMPORT = "no import for this version yet"
ADDED_UNKNOWN = "records added: not known from the export (run the edited string in the database)"
KIND_ORDER = ("add_name", "add_field", "change_stem", "drop_term")
_STOP = frozenset("""a about across after against among an and are as at be been before between both but by during
each for from has have how in into is it its more not of on or our over should than that the their them these they
this those through to toward towards under upon using via was were what when where which while who why with within
without""".split())


class EditError(ValueError):
    """An edit that cannot be listed or applied (no synonyms version, an unknown edit id): exit 1 or HTTP 400."""


def imported_records(project, version: int) -> dict[str, int]:
    """Records per platform in the files kept in deduplication and imported against ``version``."""
    rows = project.store.query("SELECT s.platform, COUNT(r.raw_id) AS n FROM sources s JOIN raw_records r "
                               "ON r.source_id=s.source_id WHERE s.included=1 AND s.query_version=? "
                               "GROUP BY s.platform", (version,))
    return {r["platform"]: int(r["n"]) for r in rows}


def about(x: float) -> str:
    """An estimate for display: rounded to three significant figures, with thousands separators ("156,000")."""
    n = int(round(x))
    d = len(str(abs(n)))
    return f"{round(n, 3 - d) if d > 3 else n:,}"


def sample_weights(project, version: int) -> dict[str, dict]:
    """Per platform imported against ``version`` (included files): the records imported, the estimated records of the
    results they come from, and each file's weight. A complete file's records weigh 1; the files of one platform marked
    a random sample of the same N are one sample of K records, each weighing N/K (two draws from one result are not two
    results). ``sample`` is ``{"of", "k", "files"}`` when the platform's files are one sample; ``mixed`` when complete
    and sampled files, or samples of different N, meet; ``estimated`` when any file is sampled (D5)."""
    rows = project.store.query("SELECT s.source_id, s.platform, s.sample_of, COUNT(r.raw_id) AS n FROM sources s "
                               "JOIN raw_records r ON r.source_id=s.source_id WHERE s.included=1 AND s.query_version=? "
                               "GROUP BY s.source_id ORDER BY s.source_id", (version,))
    out: dict[str, dict] = {}
    for r in rows:
        pl = out.setdefault(r["platform"], {"imported": 0, "complete": 0, "groups": {}, "files": []})
        pl["imported"] += int(r["n"])
        pl["files"].append((r["source_id"], r["sample_of"], int(r["n"])))
        if r["sample_of"]:
            g = pl["groups"].setdefault(int(r["sample_of"]), {"k": 0, "files": 0})
            g["k"] += int(r["n"])
            g["files"] += 1
        else:
            pl["complete"] += int(r["n"])
    res = {}
    for plat, pl in out.items():
        groups = pl["groups"]
        weights = {sid: (max(1.0, of / groups[of]["k"]) if of else 1.0) for sid, of, _n in pl["files"]}
        est = pl["complete"] + sum(max(of, g["k"]) for of, g in groups.items())
        one = len(groups) == 1 and not pl["complete"]
        of = next(iter(groups)) if one else None
        res[plat] = {"imported": pl["imported"], "estimated": est, "weights": weights,
                     "sample": {"of": of, "k": groups[of]["k"], "files": groups[of]["files"]} if one else None,
                     "groups": [{"of": n, **g} for n, g in sorted(groups.items())], "complete": pl["complete"],
                     "mixed": bool(groups) and (len(groups) > 1 or pl["complete"] > 0), "sampled": bool(groups)}
    return res


def _sample_phrase(plat: str, w: dict) -> str:
    """"openalex is a random sample of 1,249 of 650,000 results" (the budget sentence and the interview)."""
    parts = [f"{g['k']:,} of {g['of']:,} results" + (f" in {g['files']} files" if g["files"] > 1 else "")
             for g in w["groups"]]
    if w["sample"]:
        return f"{plat} is a random sample of {parts[0]}"
    tail = f", and {w['complete']:,} records from complete exports" if w["complete"] else ""
    return f"{plat} holds a random sample of " + "; a random sample of ".join(parts) + tail


def budget(project, version: int) -> dict:
    """The imported records of ``version`` (every platform, before deduplication) against
    ``protocol.identification.record_budget``, with the sentence the diagnosis and the edits list show. A file marked as
    a random sample counts as the estimated records of its result: ``records`` is then the estimate, ``imported`` the
    records imported, ``estimated`` true, and the sentence says "about" and names the sample (D5)."""
    b = project.config.protocol.identification.record_budget
    per = imported_records(project, version)
    if not per:
        n = unrecorded_imports(project)
        why = f"no import recorded under v{version} ({n} file{'s' if n != 1 else ''} imported with no version)" if n else NO_IMPORT
        return {"budget": b, "records": None, "over": 0, "per_platform": {},
                "text": f"{why}; the budget of {b} records is checked after one"}
    n = sum(per.values())
    w = sample_weights(project, version)
    if any(x["sampled"] for x in w.values()):
        est = int(round(sum(x["estimated"] for x in w.values())))
        over = max(0, est - b)
        text = (f"about {about(est)} records, " + (f"{about(over)} over the budget of {b}" if over else
                                                  f"within the budget of {b}")
                + " (estimate: " + "; ".join(_sample_phrase(p, x) for p, x in w.items() if x["sampled"]) + ")")
        return {"budget": b, "records": est, "over": over, "per_platform": per, "imported": n, "estimated": True,
                "per_platform_estimated": {p: x["estimated"] for p, x in w.items()}, "text": text}
    over = max(0, n - b)
    text = f"{n} records, {over} over the budget of {b}" if over else f"{n} records, within the budget of {b}"
    return {"budget": b, "records": n, "over": over, "per_platform": per, "text": text}


def _norm_term(label: str) -> str:
    return re.sub(r"\s+", " ", label.replace('"', "")).strip().lower()


def _name_candidates(doc) -> list[tuple[str, str]]:
    """``(term, where)`` from a seed's keywords (a phrase when it has a space) and its title words."""
    from .evaluate import tokenize

    out: list[tuple[str, str]] = []
    for kw in re.split(r"[;,]", doc.keywords or ""):
        kw = re.sub(r"\s+", " ", kw).strip().lower()
        if kw:
            out.append((f'"{kw}"' if " " in kw else kw, "keywords"))
    for w in tokenize(doc.title or ""):
        if len(w) >= 4 and w not in _STOP and not any(ch.isdigit() for ch in w):
            out.append((w, "title"))
    return list(dict.fromkeys(out))


def _swap_piece(value, old: str, new: str):
    """Replace the truncated word ``old`` by ``new`` in a term (a string, or a proximity term's items)."""
    if isinstance(value, str):
        return re.sub(r"(?<![\w*?])" + re.escape(old) + r"(?![\w*?])", new, value)
    if isinstance(value, list):
        return [_swap_piece(v, old, new) for v in value]
    if isinstance(value, dict):
        return {**value, "near": _swap_piece(value.get("near", []), old, new)}
    return value


def edited_data(data: dict, change: dict) -> dict:
    """A copy of the synonyms data with one edit applied; the stored version is never touched."""
    import copy

    from .evaluate import term_label
    from .ir import Block

    d = copy.deepcopy(data)
    blk = next(b for b in d["blocks"] if b["key"] == change["block"])
    labels = [term_label(t) for t in Block.model_validate(blk).terms]
    op = change["op"]
    if op == "add_term":
        blk.setdefault("terms", []).append(change["term"])
    elif op == "add_field":
        blk["fields"] = list(blk.get("fields") or ["title", "abstract"]) + [change["field"]]
    elif op == "drop_term":
        blk["terms"] = [t for t, lab in zip(blk["terms"], labels, strict=True) if lab != change["term"]]
    elif op == "replace_piece":
        blk["terms"] = [_swap_piece(t, change["old"], change["new"]) if lab == change["term"] else t
                        for t, lab in zip(blk["terms"], labels, strict=True)]
    else:
        raise EditError(f"unknown edit {op!r}")
    return d


def _term_diff(old_blk, new_blk) -> tuple[dict, list] | None:
    """The terms one edit took out of a block (label: count) and put in (term, count); None when it changed the
    fields."""
    from collections import Counter

    from .evaluate import term_label

    if list(old_blk.fields) != list(new_blk.fields):
        return None
    old = Counter(term_label(t) for t in old_blk.terms)
    terms = {}
    for t in new_blk.terms:
        terms.setdefault(term_label(t), t)
    new = Counter(term_label(t) for t in new_blk.terms)
    return dict(old - new), [(terms[label], n) for label, n in (new - old).items()]


def _edited_state(old_blk, new_blk, before, doc, profile, diff=False) -> str:
    """The state of ``new_blk`` (one edit of ``old_blk``) on ``doc``, from ``before`` (``old_blk``'s result there):
    the terms the edit removed are taken out, only the terms it added are matched, and the block is a hit when a term
    hits, else uncertain when a term is uncertain or a field it names is missing, as in ``evaluate_block``. An edit
    of the fields evaluates the block again. ``diff`` is ``_term_diff(old_blk, new_blk)``, worked out once per edit."""
    from .evaluate import RECORD_FIELDS, evaluate_block, record_fields, term_matches

    if diff is False:
        diff = _term_diff(old_blk, new_blk)
    if diff is None:
        return evaluate_block(new_blk, doc, profile).state
    removed, added = diff
    hits, seen = set(before.hits), set(before.terms)
    n_hit, n_unc = len(before.hits), len(before.terms) - len(before.hits)
    for label, n in removed.items():
        if label in hits:
            n_hit -= n
        elif label in seen:
            n_unc -= n
    for t, n in added:
        m = term_matches(t, doc, profile, new_blk.fields)
        if m is not None:
            n_hit, n_unc = (n_hit + n, n_unc) if m.state == "hit" else (n_hit, n_unc + n)
    if n_hit:
        return "hit"
    searched = record_fields(new_blk.fields, profile)
    missing = any(f in RECORD_FIELDS and f in searched and doc.text(f) is None for f in new_blk.fields)
    return "uncertain" if n_unc or missing else "miss"


def _seed_text(deltas: dict[str, int]) -> str:
    parts = [f"{n:+d} seed{'' if abs(n) == 1 else 's'} on {plat}" for plat, n in deltas.items() if n]
    return ", ".join(parts) or "no seed gained or lost"


def suggested_edits(project, version: int | None = None) -> dict:
    """Candidate edits to a synonyms version (default: the newest), each with its consequence computed by re-running
    the evaluator on an edited copy: development seeds gained or lost per platform (the project's profiled platforms)
    and, where an export was imported against the version, the records an edit would remove. Kinds: ``add_name`` (a
    keyword or title word of a missed development seed that no block holds, or a name admitted from the seed papers
    (2b) that the block does not hold, put in a block the seed fails),
    ``add_field`` (search a block in the keywords too), ``change_stem`` (from the truncation report) and
    ``drop_term`` (from the noise report). Ranked by development seeds gained, then, when the imported records are
    over ``record_budget``, by records saved per seed kept; ids E1, E2, ... follow the ranking. Validation seeds
    never drive an edit (D9, D10)."""
    from ..construct import artifacts
    from .evaluate import (
        BlockResult,
        NoProfile,
        Query,
        SeedDoc,
        _abstracts,
        _logic,
        evaluate,
        evaluate_block,
        load_profile,
        profiled_platforms,
        seed_doc,
        term_label,
    )

    row, data = artifacts.load(project, "synonyms", version)
    if row is None:
        raise EditError(f"no synonyms version {version}" if version is not None else "no synonyms version yet")
    version = row["version"]
    query = Query.model_validate(data)
    positive = _positive_blocks(query)
    seeds, _shown = shown_seeds(project)
    ab = _abstracts(project)
    docs = [seed_doc(b, "Development", ab.get(b.benchmark_id)) for s, b in seeds if s == "Development"]
    chosen = [p.key for p in project.config.platforms]
    keys = [k for k in chosen if k in profiled_platforms()] or profiled_platforms()
    # then every other platform imported against the version that has a profile (OpenAlex, outside the catalogue)
    from .render import platforms as catalogue

    keys += [r["platform"] for r in project.store.query(
        "SELECT platform FROM sources WHERE included=1 AND query_version=? ORDER BY source_id", (version,))
        if r["platform"] not in keys and r["platform"] not in catalogue()]   # catalogue platforms read as before
    profiles = []
    for k in dict.fromkeys(keys):
        try:
            profiles.append(load_profile(k, query))
        except NoProfile:
            continue
    bud = budget(project, version)
    sw = sample_weights(project, version) if bud.get("estimated") else {}
    base = {pr.platform: {d.benchmark_id: evaluate(query, d, pr) for d in docs} for pr in profiles}
    held = {_norm_term(term_label(t)) for b in query.blocks for t in b.terms}

    cands: dict[tuple, dict] = {}

    def add(kind: str, change: dict, label: str, source: str):
        cands.setdefault((kind, *sorted(change.items())), {"kind": kind, "change": change, "label": label,
                                                           "source": source})

    for pr in profiles:
        for d in docs:
            res = base[pr.platform][d.benchmark_id]
            if res.state != "miss":
                continue
            for k in res.failed_blocks:
                for term, where in _name_candidates(d):
                    if _norm_term(term) not in held:
                        add("add_name", {"op": "add_term", "block": k, "term": term},
                            f"add {term} to block {k}", f"{d.benchmark_id} {where}")
    # 2b: names admitted from the seed papers' full texts, offered for the other blocks a missed seed fails
    from ..seeds.names import _term, admitted_names
    admitted = [_term(n) for n in admitted_names(project)]
    for pr in profiles:
        for d in docs:
            res = base[pr.platform][d.benchmark_id]
            for k in (res.failed_blocks if res.state == "miss" else []):
                in_k = {_norm_term(term_label(t)) for b in query.blocks if b.key == k for t in b.terms}
                for term in admitted:
                    if _norm_term(term) not in in_k:
                        add("add_name", {"op": "add_term", "block": k, "term": term},
                            f"add {term} to block {k}", "a name admitted from the seed papers")
    for b in query.blocks:
        if b.key in positive and "keywords" not in b.fields:
            add("add_field", {"op": "add_field", "block": b.key, "field": "keywords"},
                f"search block {b.key} in the keywords too", "seed keywords")
    for pr in profiles:
        for r in truncation_report(query, docs, pr):
            if r["block"] not in positive:
                continue
            for s in r["stems"]:
                # each option says which way it goes: a longer stem or a spelling variant narrows the term and
                # stops matching some words of the seed papers (a reader took "change employee* to employees*,
                # loses employee" for a widening; inspection 1 Nov 2026)
                opts = []
                if s["shorter"] and s["shorter"]["gains"]:
                    opts.append(("widen", s["shorter"]["pattern"],
                                 "broader: also matches " + ", ".join(s["shorter"]["gains"])))
                opts += [("narrow", lo["pattern"], f"narrower: {lo['pattern']} no longer matches "
                          f"{', '.join(lo['loses'])}, which the seed papers use") for lo in s["longer"] if lo["loses"]]
                opts += [("narrow", v["pattern"], f"narrower: {v['pattern']} no longer matches "
                          f"{', '.join(v['drops'])}, which the seed papers use") for v in s["variants"]]
                for way, pat, why in opts:
                    add("change_stem", {"op": "replace_piece", "block": r["block"], "term": r["term"],
                                        "old": s["piece"], "new": pat},
                        f"{way} {s['piece']} to {pat} in {r['block']}: {r['term']}", f"truncation report: {why}")
    diag = None
    if bud["records"]:
        try:
            diag = what_did_what(project, version)
        except DiagnoseError:
            diag = None
    for plat, pl in (diag or {}).get("platforms", {}).items():
        for x in pl.get("noise", []):
            n_text = _count_text(x["records"], x["est"], sw[plat]) if "est" in x else f"{x['records']} records"
            add("drop_term", {"op": "drop_term", "block": x["block"], "term": x["term"]},
                f"drop {x['term']} from block {x['block']}", f"noise on {plat}: {n_text}")

    rec_cache: dict[str, list] = {}

    def records(pr):
        if pr.platform not in rec_cache:
            rows = project.store.query(
                "SELECT r.raw_id, r.title, r.abstract, r.keywords, s.source_id FROM raw_records r JOIN sources s "
                "ON s.source_id=r.source_id WHERE s.platform=? AND s.query_version=? AND s.included=1",
                (pr.platform, version))
            out = []
            for r in rows:
                doc = SeedDoc(str(r["raw_id"]), "", r["title"] or "", r["abstract"] or "", r["keywords"] or "")
                res = evaluate(query, doc, pr)
                out.append((doc, res.per_block, res.state, r["source_id"]))
            rec_cache[pr.platform] = out
        return rec_cache[pr.platform]

    edits = []
    for c in cands.values():
        try:
            new = Query.model_validate(edited_data(data, c["change"]))
        except (ValueError, StopIteration):
            continue
        seeds_out, deltas, kept = {}, {}, 0
        key = c["change"].get("block")
        # an edit of one block: only the terms it adds are matched, the rest is the base run's (a query of ~660 terms
        # took over a minute per candidate when the whole query was evaluated for each)
        one = (new.logic == query.logic and key in {b.key for b in query.blocks}
               and [b.key for b in new.blocks] == [b.key for b in query.blocks])
        if one:
            blk, was = new.block(key), query.block(key)
            diff = _term_diff(was, blk)
        for pr in profiles:
            before = base[pr.platform]
            if one:
                after = {}
                for d in docs:
                    pb = before[d.benchmark_id].per_block
                    after[d.benchmark_id] = _logic(new.logic, {**pb, key: BlockResult(
                        _edited_state(was, blk, pb[key], d, pr, diff))})
            else:
                after = {d.benchmark_id: evaluate(new, d, pr).state for d in docs}
            gained = [i for i, st in after.items() if st == "hit" and before[i].state != "hit"]
            lost = [i for i, st in after.items() if st != "hit" and before[i].state == "hit"]
            deltas[pr.platform] = len(gained) - len(lost)
            kept += sum(st == "hit" for st in after.values())
            seeds_out[pr.platform] = {"gained": gained, "lost": lost}
        if c["kind"] in ("add_name", "add_field") and not any(v["gained"] for v in seeds_out.values()):
            continue   # only an add that turns a miss into a hit is suggested
        additive = c["kind"] in ("add_name", "add_field") or c["source"].startswith("truncation report: also")
        removed: dict[str, int] = {}
        removed_est: dict[str, float] = {}
        if bud["records"] and not additive:
            blk = new.block(c["change"]["block"])
            for pr in profiles:
                if pr.platform not in bud["per_platform"]:
                    continue
                n, x = 0, 0.0
                wts = (sw.get(pr.platform) or {}).get("weights", {})
                for doc, per_block, state, sid in records(pr):
                    if state == "miss":
                        continue
                    pb = dict(per_block)
                    pb[blk.key] = (BlockResult(_edited_state(was, blk, per_block[blk.key], doc, pr, diff))
                                   if one else evaluate_block(blk, doc, pr))
                    gone = _logic(new.logic, pb) == "miss"
                    n += gone
                    x += wts.get(sid, 1.0) * gone
                removed[pr.platform] = n
                removed_est[pr.platform] = x
        if not bud["records"]:
            rec_text = f"+0 records ({NO_IMPORT})"
        elif additive:
            rec_text = ADDED_UNKNOWN
        else:
            rec_text = ", ".join(_removed_text(p, n, removed_est[p], sw.get(p)) for p, n in removed.items()) \
                or "-0 records"
        extra = {}
        if sw:   # estimates from a sampled file (D7): the plain counts and, for one sample, the 95% interval
            extra = {"records_removed_sample": removed,
                     "removed_interval": {p: _removed_interval(n, sw[p]) for p, n in removed.items()
                                          if (sw.get(p) or {}).get("sample")}}
            removed = {p: int(round(removed_est[p])) for p in removed}
        saved = sum(removed.values())
        edits.append({**c, "seeds": seeds_out, "deltas": deltas, "records_removed": removed, **extra,
                      "saved_per_seed": round(saved / max(1, kept), 4),
                      "consequence": f"{_seed_text(deltas)}, {rec_text}"})
    over = bud["over"] > 0
    edits.sort(key=lambda e: (-sum(e["deltas"].values()), -e["saved_per_seed"] if over else 0,
                              KIND_ORDER.index(e["kind"]), e["label"]))
    for i, e in enumerate(edits, 1):
        e["id"] = f"E{i}"
    return {"version": version, "status": row["status"], "budget": bud, "platforms": [p.platform for p in profiles],
            "edits": edits}


def _removed_interval(x: int, sw: dict) -> list[int]:
    """N times the exact 95% interval of ``x`` removed of the K records of one sample (D7)."""
    s = sw["sample"]
    lo, hi = clopper_pearson(x, s["k"])
    return [int(round(s["of"] * lo)), int(round(s["of"] * hi))]


def _removed_text(plat: str, n: int, x: float, sw: dict | None) -> str:
    """"-40 records on scopus", or for a sampled platform "-about 2,000 records on openalex (20 of 1,000 in a random
    sample of 100,000; 95% interval 1,230 to 3,070)" (D7)."""
    if not sw or not sw["sampled"]:
        return f"-{n} records on {plat}"
    if sw["sample"]:
        s = sw["sample"]
        lo, hi = _removed_interval(n, sw)
        return (f"-about {about(x)} records on {plat} ({n:,} of {s['k']:,} in a random sample of {s['of']:,}; "
                f"95% interval {about(lo)} to {about(hi)})")
    return f"-about {about(x)} records on {plat} ({n:,} of {sw['imported']:,} imported, some in a random sample)"


def edit_lines(res: dict) -> list[str]:
    """`wider query edits` as text; the page's "Suggested edits" list shows the same lines."""
    lines = [f"synonyms v{res['version']} ({res['status']}): {res['budget']['text']}"]
    if not res["edits"]:
        lines.append("no edit to suggest")
    for e in res["edits"]:
        lines.append(f"{e['id']} {e['label']} ({e['source']}): {e['consequence']}")
    return lines


def apply_edit(project, edit_id: str, version: int | None = None, by: str | None = None,
               confirm_revision: bool = False) -> dict:
    """Write the edit as a new draft synonyms version (origin ``edit``, its note naming the edit); the version it
    came from, frozen or not, is unchanged. A save while a frozen query stands reopens it as any save does (D2)."""
    from ..construct import artifacts

    res = suggested_edits(project, version)
    e = next((x for x in res["edits"] if x["id"].lower() == edit_id.strip().lower()), None)
    if e is None:
        ids = ", ".join(x["id"] for x in res["edits"]) or "none"
        raise EditError(f"no edit {edit_id} for synonyms v{res['version']} (listed: {ids}); `wider query edits` lists them")
    _row, data = artifacts.load(project, "synonyms", res["version"])
    row = artifacts.save_new_version(project, "synonyms", edited_data(data, e["change"]), origin="edit",
                                     note=f"edit {e['id']} on v{res['version']}: {e['label']} ({e['consequence']})",
                                     parent_version=res["version"], by=by, confirm_revision=confirm_revision)
    return {"edit": e, "from_version": res["version"], "version": row["version"], "note": row["note"],
            "notice": row.get("notice")}


# the indexing check lives in its own module (stream C of 2a); task 2.10 names it by this module
from .indexing import known_item_strings, verdict_groups  # noqa: E402,F401
