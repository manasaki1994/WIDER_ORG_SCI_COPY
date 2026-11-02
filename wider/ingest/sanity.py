"""Export sanity checks: is each file plausibly the result of the query, and is it complete?

Per file: record count and format, benchmark papers found (DOI, exact title, fuzzy title at
``identification.jw_benchmark``), overlap with the query vocabulary (share of records whose title or abstract
contains at least one term of the synonyms artifact), known export caps (``platforms.yaml`` ``export_cap``),
duplicate files (same sha256) and a plain-language verdict:

- ``empty``      no records could be read
- ``duplicate``  the same bytes as an earlier file
- ``off_topic``  every available signal fails (benchmark hits below ``imports.min_benchmark_hits`` and
                 vocabulary overlap below ``imports.min_vocabulary_overlap``)
- ``capped``     the count equals the platform's export cap for this format: probably one truncated batch
- ``ok``         nothing suspicious (warnings may still be listed)
"""
from __future__ import annotations

import re
from pathlib import Path

from ..construct import artifacts
from ..core.matching import CorpusRecord, locate_benchmark
from ..core.project import Project
from ..core.provenance import sha256_file
from ..core.text import norm_text
from ..search.coverage import development_papers, used_papers, validation_papers, validation_unlocked
from ..search.render import platforms as platform_table
from .importer import input_files
from .readers import read_standard

VERDICTS = ("ok", "capped", "off_topic", "empty", "duplicate")
# what the tables show for each verdict; an "ok" with one check below its threshold reads "ok, one check low"
VERDICT_LABEL = {"ok": "ok", "capped": "capped", "off_topic": "off topic", "empty": "empty", "duplicate": "duplicate"}
LOW_LABEL = "ok, one check low"
THRESHOLD_RULE = "a file is off topic only when it misses both"


def pct(x: float) -> int:
    """A share as a whole percent, halves rounded up as the pages round them (Python's round() rounds 20.5 to 20)."""
    return int(x * 100 + 0.5)


def verdict_label(r: dict) -> str:
    return LOW_LABEL if r.get("verdict") == "ok" and r.get("low") else VERDICT_LABEL.get(r.get("verdict"), r.get("verdict"))


# verdicts that make a newly imported file start out of deduplication (ui-redesign D32 F8)
FLAGGED = ("off_topic", "empty", "duplicate")


# ----- query vocabulary -----
def _term_strings(term) -> list[str]:
    """Flatten a synonyms term (word, phrase or proximity group) into plain strings."""
    if isinstance(term, str):
        return [term]
    items = term.get("near", []) if isinstance(term, dict) else getattr(term, "near", [])
    out: list[str] = []
    for it in items:
        if isinstance(it, list):
            out += [s for s in it if isinstance(s, str)]
        elif isinstance(it, str):
            out.append(it)
    return out


# " " + the first word of each term pattern: normalised text holds only letters, digits and single spaces, so a
# record without this at a word start (text padded with a space) cannot match and its regex is skipped
_LITERAL: dict[re.Pattern, str] = {}


def term_pattern(term: str) -> re.Pattern | None:
    """Regex over normalised text for one query term: ``*`` is right-truncation, quotes make a phrase."""
    s = term.strip().strip('"').strip("'").strip()
    if not s:
        return None
    parts, bases = [], []
    for word in s.split():
        trunc = word.endswith("*") or word.endswith("$")
        base = norm_text(word.rstrip("*$"))
        if not base:
            continue
        parts.append(re.escape(base) + (r"\w*" if trunc else ""))
        bases.append(base)
    if not parts:
        return None
    pat = re.compile(r"\b" + r"\s+".join(parts) + r"\b")
    _LITERAL[pat] = " " + bases[0]
    return pat


def vocabulary(project: Project) -> dict | None:
    """Compiled patterns per block of the synonyms artifact (approved/frozen first, else the latest draft)."""
    row = artifacts.latest(project, "synonyms", status="approved")
    note = None
    if row is None:
        row = artifacts.latest(project, "synonyms")
        note = "no approved synonyms artifact; using the latest draft" if row else None
    if row is None:
        return None
    _, data = artifacts.load(project, "synonyms", row["version"])
    blocks: dict[str, dict] = {}
    for b in (data or {}).get("blocks", []):
        pats = [p for t in b.get("terms", []) for s in _term_strings(t) if (p := term_pattern(s))]
        if pats:
            blocks[b["key"]] = {"role": b.get("role", "required"), "patterns": pats}
    return {"version": row["version"], "status": row["status"], "note": note, "blocks": blocks}


def _found(text: str, lit: str, pat: re.Pattern) -> bool:
    """Does ``pat`` match ``text`` (padded with a leading space)? Tried only where its first word starts."""
    if not lit:
        return pat.search(text) is not None
    i = text.find(lit)
    while i >= 0:
        if pat.match(text, i + 1):
            return True
        i = text.find(lit, i + 1)
    return False


def vocabulary_overlap(rows: list[dict], vocab: dict | None) -> dict | None:
    """Share of records matching any term, and per block, over normalised title + abstract."""
    if not vocab or not vocab["blocks"] or not rows:
        return None
    hits_any = 0
    hits_block = dict.fromkeys(vocab["blocks"], 0)
    # a regex without a literal start tries every position of the text; a substring test first is far cheaper
    checks = {key: [(_LITERAL.get(p, ""), p) for p in b["patterns"]] for key, b in vocab["blocks"].items()}
    for r in rows:
        text = " " + norm_text(f"{r.get('title') or ''} {r.get('abstract') or ''} {r.get('keywords') or ''}")
        matched = False
        for key, pats in checks.items():
            if any(_found(text, lit, p) for lit, p in pats):
                hits_block[key] += 1
                matched = True
        hits_any += matched
    n = len(rows)
    return {"any": round(hits_any / n, 3), "blocks": {k: round(v / n, 3) for k, v in hits_block.items()}, "n": n}


# ----- caps -----
def export_cap(platform: str, fmt: str, suffix: str | None = None) -> int | None:
    """The platform's per-batch cap: a ``.txt`` file uses the ``txt`` cap when one is known (a WoS or EBSCO text
    export may be sniffed as tab-delimited or RIS), otherwise the cap of the sniffed format."""
    entry = platform_table().get(platform) or {}
    caps = entry.get("export_cap") or {}
    cap = caps.get("txt") if (suffix or "").lower() == ".txt" and caps.get("txt") else caps.get(fmt)
    return int(cap) if cap else None


# ----- benchmark -----
def benchmark_list(project: Project, include_validation: bool | None = None) -> list[tuple[str, str, str | None]]:
    papers = project.read_benchmark()
    if include_validation is None:
        include_validation = validation_unlocked(project) or not project.config.protocol.identification.validation_after_freeze
    # one selection for every reader (D3): query-results papers in neither; the used validation papers were seen
    # already, so listing them is not a look (validation-reuse D1)
    keep = {b.benchmark_id for b in development_papers(project, papers) + used_papers(project, papers)
            + (validation_papers(project, papers) if include_validation else [])}
    return [(b.benchmark_id, b.title, b.doi) for b in papers if b.benchmark_id in keep]


# ----- per file -----
def check_rows(project: Project, platform: str, rows: list[dict], meta: dict, *, bench=None, vocab=None,
               sha256: str | None = None, duplicate_of: str | None = None, suffix: str | None = None) -> dict:
    """Sanity verdict for already-read rows (used by check_file and by the upload route); ``suffix`` is the file's."""
    pr = project.config.protocol.imports
    thr = project.config.protocol.identification.jw_benchmark
    bench = benchmark_list(project) if bench is None else bench
    vocab = vocabulary(project) if vocab is None else vocab
    n = len(rows)
    out: dict = {"platform": platform, "format": meta.get("format"), "profile": meta.get("profile"),
                 "detected_platform": meta.get("detected_platform"), "mismatch": bool(meta.get("mismatch")),
                 "n": n, "sha256": sha256, "duplicate_of": duplicate_of,
                 "no_title": sum(1 for r in rows if not r.get("title")),
                 "no_abstract": sum(1 for r in rows if not r.get("abstract")),
                 "no_doi": sum(1 for r in rows if not r.get("doi")),
                 "benchmark_hits": None, "benchmark_n": len(bench), "benchmark_found": [],
                 "vocab_overlap": None, "vocab_blocks": None, "vocab_version": vocab["version"] if vocab else None,
                 "cap": export_cap(platform, meta.get("format") or "", suffix), "warnings": []}
    if bench and n:
        corpus = [CorpusRecord(str(i), r.get("title") or "", r.get("doi")) for i, r in enumerate(rows)]
        matches = locate_benchmark(bench, corpus, jw_threshold=thr)
        found = [m.benchmark_id for m in matches if m.method != "none"]
        out["benchmark_hits"], out["benchmark_found"] = len(found), found
    ov = vocabulary_overlap(rows, vocab)
    if ov:
        out["vocab_overlap"], out["vocab_blocks"] = ov["any"], ov["blocks"]
    if vocab and vocab.get("note"):
        out["warnings"].append(vocab["note"])
    if out["mismatch"]:
        out["warnings"].append(f"columns look like a {meta.get('detected_platform')} export, not {platform}")
    if n and out["no_title"] == n:
        out["warnings"].append("no record has a title; the column profile may be wrong")
    # verdict
    signals: list[bool] = []
    if bench and n:
        signals.append(out["benchmark_hits"] >= pr.min_benchmark_hits)
    if out["vocab_overlap"] is not None:
        signals.append(out["vocab_overlap"] >= pr.min_vocabulary_overlap)
    if n == 0:
        verdict = "empty"
    elif duplicate_of:
        verdict = "duplicate"
    elif signals and not any(signals):
        verdict = "off_topic"
    elif pr.warn_on_export_cap and out["cap"] and n == out["cap"]:
        verdict = "capped"
    else:
        verdict = "ok"
        if bench and n and out["benchmark_hits"] < pr.min_benchmark_hits:
            out["warnings"].append(f"{out['benchmark_hits']} of {len(bench)} seed papers in this file")
        if out["vocab_overlap"] is not None and out["vocab_overlap"] < pr.min_vocabulary_overlap:
            out["warnings"].append(f"only {pct(out['vocab_overlap'])}% of records contain a query term")
        if not signals:
            out["warnings"].append("no seed papers and no synonyms yet: topic checks skipped")
    out["verdict"] = verdict
    # an "ok" file that misses one threshold: said in its label, not only in the note (inspection 1 Nov 2026)
    out["low"] = verdict == "ok" and bool(signals) and not all(signals)
    out["message"] = verdict_text(out)
    return out


def verdict_text(r: dict) -> str:
    n, v = r["n"], r["verdict"]
    if v == "empty":
        return "no records could be read from this file"
    if v == "duplicate":
        return f"identical to {r['duplicate_of']}; it will be imported once"
    if v == "off_topic":
        parts = []
        if r["benchmark_hits"] is not None:
            parts.append(f"{r['benchmark_hits']} of {r['benchmark_n']} seed papers")
        if r["vocab_overlap"] is not None:
            parts.append(f"only {pct(r['vocab_overlap'])}% vocabulary overlap")
        return "this file probably is not the result of your query: " + " and ".join(parts)
    if v == "capped":
        return (f"{n} records is exactly the {r['platform']} {r['format']} export cap; "
                "the export was probably truncated to one batch")
    return "; ".join(r["warnings"]) if r["warnings"] else "looks like a complete, on-topic export"


def check_file(project: Project, platform: str, path: str | Path, *, bench=None, vocab=None,
               seen: dict[str, str] | None = None) -> dict:
    """Sanity report for one export file (``seen`` maps sha256 -> file name of earlier files)."""
    path = Path(path)
    h = sha256_file(path)
    dup = (seen or {}).get(h)
    try:
        meta, rows = read_standard(path, platform)
    except Exception as e:
        return {"platform": platform, "file": path.name, "path": str(path), "format": None, "profile": None, "n": 0,
                "sha256": h, "verdict": "empty", "error": str(e)[:300], "warnings": [str(e)[:300]],
                "message": f"could not read this file: {str(e)[:200]}", "benchmark_hits": None,
                "benchmark_n": 0, "vocab_overlap": None, "cap": None, "duplicate_of": dup, "mismatch": False}
    out = check_rows(project, platform, rows, meta, bench=bench, vocab=vocab, sha256=h, duplicate_of=dup,
                     suffix=path.suffix)
    out["file"], out["path"] = path.name, str(path)
    if seen is not None and h not in seen:
        seen[h] = f"{platform}/{path.name}"
    return out


def check_all(project: Project) -> dict:
    """Sanity table for every file under inputs/, plus the context the verdicts were computed with."""
    bench = benchmark_list(project)
    vocab = vocabulary(project)
    imported = {r["sha256"]: dict(r) for r in project.store.query("SELECT sha256, file_name, platform FROM sources")}
    seen: dict[str, str] = {}
    files = []
    for platform, f in input_files(project):
        r = check_file(project, platform, f, bench=bench, vocab=vocab, seen=seen)
        r["imported"] = r.get("sha256") in imported
        files.append(r)
    pr = project.config.protocol.imports
    return {"files": files, "n_files": len(files),
            "counts": {v: sum(1 for r in files if r["verdict"] == v) for v in VERDICTS},
            "benchmark_n": len(bench),
            "synonyms": {"version": vocab["version"], "status": vocab["status"], "blocks": list(vocab["blocks"])}
            if vocab else None,
            "thresholds": {"min_benchmark_hits": pr.min_benchmark_hits,
                           "min_vocabulary_overlap": pr.min_vocabulary_overlap,
                           "warn_on_export_cap": pr.warn_on_export_cap}}
