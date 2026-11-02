"""Which venues use which names (vision change 2b, design D11b, answer 3): a table of candidate names by venue from
the seed papers' parsed reference sections, and the researcher's labels of venues as streams of the literature.

No model and no question kind: the table only counts, the researcher decides what a stream is. A label is stored in
``stream_labels``, journalled ``stream_label`` (undoable) and written to the package as ``streams.json``.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from ..core import journal
from ..core.project import Project
from ..core.provenance import utcnow
from ..core.text import norm_text

UNKNOWN = "unknown venue"
# where a venue ends: the volume ("12(3)", ", vol. 4", " 4,"), a year in brackets, a DOI, or the end of a sentence
_VENUE_END = re.compile(r",\s*(?:vol\.?\s*)?\d|\s\d|\(|\bdoi\b|https?://|\.\s|\.$|;", re.IGNORECASE)


def venue(raw_ref: str, title: str | None) -> str:
    """The venue of a reference entry: the words after its title up to the volume, a year, a DOI or the next
    sentence ("Journal of Synthetic Signals" from "... Alpha coupling in pairs. Journal of Synthetic Signals, 12(3),
    1-10."). ``unknown venue`` when the title is not found in the entry or nothing venue-like follows it."""
    raw = re.sub(r"\s+", " ", raw_ref or "")
    if not title:
        return UNKNOWN
    i = raw.lower().find(title.lower())
    if i < 0:
        return UNKNOWN
    rest = raw[i + len(title):].lstrip(" .,:\"”’'")
    rest = re.sub(r"^(?:in:?\s+)", "", rest, flags=re.IGNORECASE)
    m = _VENUE_END.search(rest)
    v = (rest[:m.start()] if m else rest).strip(" .,:;\"“”")
    return v if len(v) >= 3 and re.search(r"[A-Za-z]{2}", v) else UNKNOWN


def _references(project: Project) -> list[dict[str, Any]]:
    from . import pdf
    out = []
    for b in project.read_benchmark():
        if b.set != "Development":   # a validation seed's reference list stays out of query development
            continue
        t = pdf.load_text(project, b.benchmark_id)
        for r in (t or {}).get("references", []):
            out.append({**r, "seed": b.benchmark_id})
    return out


def _names(project: Project) -> list[str]:
    """The candidate names of the newest mining run, plus the names admitted into a block."""
    from . import mine
    names = [c["key"] for c in mine.candidates(project)]
    try:
        from . import names as names_mod
        names += [n for n in names_mod.admitted_names(project) if n not in names]
    except (ImportError, AttributeError):
        pass
    return names


def labels(project: Project) -> dict[str, dict[str, Any]]:
    return {r["venue_norm"]: {"label": r["label"], "by": r["by"], "at": r["at"]}
            for r in project.store.query("SELECT venue_norm, label, by, at FROM stream_labels ORDER BY venue_norm")}


def table(project: Project) -> dict[str, Any]:
    """``{venues: [{venue_norm, venue, references, label}], names: [{name, counts: {venue_norm: n}, total}]}``.

    A venue is every distinct normalised venue among the seeds' parsed references (``norm_text`` of the dedup), shown
    with its most frequent spelling; a venue with one reference is still a row. A name's count for a venue is the
    number of that venue's reference titles holding the name. Names that no reference title holds are left out."""
    from .mine import normalise

    refs = _references(project)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    titles: dict[str, list[str]] = defaultdict(list)
    for r in refs:
        v = venue(r.get("raw_ref") or "", r.get("title"))
        vn = norm_text(v) or UNKNOWN
        spellings[vn][v] += 1
        titles[vn].append(normalise(r.get("title") or ""))
    lab = labels(project)
    venue_rows = [{"venue_norm": vn, "venue": spellings[vn].most_common(1)[0][0], "references": len(titles[vn]),
                   "label": (lab.get(vn) or {}).get("label")}
                  for vn in sorted(spellings, key=lambda k: (-len(titles[k]), k))]
    name_rows = []
    for name in _names(project):
        n = normalise(name)
        counts = {vn: sum(1 for t in ts if n in t) for vn, ts in titles.items()}
        total = sum(counts.values())
        if total:
            name_rows.append({"name": name, "counts": {v["venue_norm"]: counts[v["venue_norm"]] for v in venue_rows},
                              "total": total})
    name_rows.sort(key=lambda r: (-r["total"], r["name"]))
    return {"venues": venue_rows, "names": name_rows, "references": len(refs),
            "labels": {k: v for k, v in lab.items()}}


def label(project: Project, venue_name: str, stream: str, by: str | None = None) -> dict[str, Any]:
    """Label a venue of the table as a stream (an empty ``stream`` removes the label); journalled ``stream_label``.
    ``KeyError`` for a venue the table does not hold."""
    vn = norm_text(venue_name)
    known = {v["venue_norm"] for v in table(project)["venues"]} | set(labels(project))
    if vn not in known:
        raise KeyError(f'no venue "{venue_name}" in the streams table (run wider seeds streams to see the venues)')
    stream = (stream or "").strip()[:80]
    author = journal.current_author(by)
    summary = f'venue "{venue_name}" labelled stream "{stream}"' if stream else f'stream label of "{venue_name}" removed'
    with journal.recording(project, "stream_label", summary, author) as rec:
        rec.rows("stream_labels", "venue_norm=?", [vn])
        with project.store.tx() as c:
            c.execute("DELETE FROM stream_labels WHERE venue_norm=?", (vn,))
            if stream:
                c.execute("INSERT INTO stream_labels(venue_norm, label, by, at) VALUES(?,?,?,?)",
                          (vn, stream, author, utcnow()))
    return {"venue_norm": vn, "label": stream or None, "change_id": rec.change_id}


def package_json(project: Project) -> dict[str, Any]:
    """What ``streams.json`` in the replication package holds: the table and the labels (counts and venue names
    from reference lists, no passage text)."""
    t = table(project)
    return {"venues": t["venues"], "names": t["names"], "labels": t["labels"]}
