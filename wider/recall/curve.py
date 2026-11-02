"""The relevant-found series stored with a recall estimate (results-dashboard-and-ris, design D5).

Per score band end of the sample's design, the Horvitz–Thompson sums over the sampled rows of the bands up to it:
``relevant = Σ w·rel`` and ``kept = Σ w·rel·kept`` for the primary variant, cumulated by band (not by rank, so rows
without a rank need no special case and no point reveals one sampled record). The last point's kept/relevant is the
primary point estimate. Stored as ``last_recall_curve:<sample>`` next to change 4's ``last_validation_lines:<sample>``,
with the same set key and time and a checksum of what the estimate read; it holds no record id, title, abstract or
rank of a sampled record.

Since decision-set-exports-and-found-chart (design D9) each band also has its details (ranks, records, sampled,
labelled, relevant observed, weighted relevant and kept) and each band end its cumulative relevant, kept and missed,
each with the sum of the exact cell bounds of the bands up to it when the estimate's bounds read the same labels.
"""
from __future__ import annotations

import hashlib
import json

from ..core.project import Project

META = "last_recall_curve:{sample}"
# stored with a series and the validation lines whose cells count the band as drawn (recall-final-check-fixes D9)
POPULATION = "at_draw"


def _band_ends(project: Project, sample: str) -> list[tuple[str, int]] | None:
    """[(band name, end rank)] in the design's order, the open last band ending at the ranking's last rank; None for
    a sample whose design has no score bands (an imported sample)."""
    spans = _band_spans(project, sample)
    return None if spans is None else [(name, end) for name, _start, end in spans]


def _band_spans(project: Project, sample: str) -> list[tuple[str, int, int]] | None:
    """[(band name, first rank, last rank)] as :func:`_band_ends`."""
    from ..sample.design import probability_frame, sampling_log

    log = sampling_log(project).get(sample) or {}
    bands = log.get("bands") or []
    if not bands or not all("start" in b and "band" in b for b in bands):
        return None
    n_ranked = None
    if any(b.get("end") is None for b in bands):
        from ..rank.versions import sample_ranking

        rk = sample_ranking(project, sample, log.get("ranking"))   # the version the sample was drawn from
        n_ranked = probability_frame(project, dict(rk))["n_ranked"] if rk else None
    out = []
    for b in bands:
        end = b.get("end") if b.get("end") is not None else n_ranked
        if end is None:
            return None
        out.append((b["band"], int(b["start"]), int(end)))
    return out


def series(project: Project, sample: str, key: str, variant: str | None = None, primary: str | None = None,
           bounds: dict | None = None) -> dict:
    """``{"variant", "primary", "points": [[band end rank, relevant, kept]], "bands": bool, "band_details", "ends"}``
    for set ``key``; no points for a sample without design bands. ``primary`` is the primary labeller, as in
    ``estimate``. ``bounds`` (``recall_bounds``' result) adds the summed cell bounds to each band and end (D9)."""
    from .estimate import VARIANTS, _rows

    variant = variant or project.config.protocol.recall.primary_variant
    relevant, kept = VARIANTS[variant]
    spans = _band_spans(project, sample)
    out = {"variant": variant, "primary": primary, "points": [], "bands": spans is not None, "band_details": [],
           "ends": []}
    if spans is None:
        return out
    rows = _rows(project, sample, key, primary)
    order = {name: i for i, (name, _start, _end) in enumerate(spans)}
    sampled, (records, in_other) = _sampled(project, sample, primary), _records(project, sample, key, variant)
    cells = {}
    for c in (bounds or {}).get("cells") or []:
        cells.setdefault(c["band"], []).append(c)
    rel = kep = 0.0
    lo = {"rel": 0, "kept": 0, "missed": 0}
    hi = dict(lo)
    for i, (name, start, end) in enumerate(spans):
        mine = [r for r in rows if order.get(r["band"], len(spans) - 1) == i]   # an unnamed band counts in the last
        b_rel = sum(r["w"] for r in mine if r["human"] in relevant)
        b_kept = sum(r["w"] for r in mine if r["human"] in relevant and r["screen"] in kept)
        rel, kep = rel + b_rel, kep + b_kept
        out["points"].append([end, round(rel, 6), round(kep, 6)])
        band = {"band": name, "first": start, "last": end, "records": records.get(name), "sampled": sampled.get(name, 0),
                "labelled": len(mine), "relevant": sum(1 for r in mine if r["human"] in relevant),
                "rel": round(b_rel, 6), "kept": round(b_kept, 6)}
        if in_other.get(name):
            band["in_other"] = in_other[name]   # drawn into another sample after this one's draw; counted in records
        point = {"end": end, "rel": round(rel, 6), "kept": round(kep, 6), "missed": round(rel - kep, 6)}
        if bounds is not None:
            mc = cells.get(name, [])
            band["rel_low"], band["rel_high"] = sum(c["low"] for c in mc), sum(c["high"] for c in mc)
            for part, keep in (("rel", None), ("kept", True), ("missed", False)):
                sel = [c for c in mc if keep is None or (c["decision"] == "kept") == keep]
                lo[part] += sum(c["low"] for c in sel)
                hi[part] += sum(c["high"] for c in sel)
                point[f"{part}_low"], point[f"{part}_high"] = lo[part], hi[part]
        out["band_details"].append(band)
        out["ends"].append(point)
    return out


def _sampled(project: Project, sample: str, primary: str | None) -> dict:
    """The sampled records per band (labelled or not), as ``estimate._rows`` counts them."""
    from ..sample.labels import final_labels

    out: dict = {}
    for r in final_labels(project, sample, primary):
        if r["band"] is not None and r["weight"] is not None:
            out[r["band"]] = out.get(r["band"], 0) + 1
    return out


def _records(project: Project, sample: str, key: str, variant: str) -> tuple[dict, dict]:
    """The records per band as the cells' N count them (the design's N_h, the range's population), and per band those
    of them another sample took since (recall-final-check-fixes D4)."""
    from ..sample.plan import sample_cells

    sc = sample_cells(project, sample, key, variant)
    if sc is None:
        return {}, {}
    return ({b["name"]: sum(c["N"] for c in b["cells"].values()) for b in sc["bands"]},
            {b["name"]: b["in_other"] for b in sc["bands"]})


def checksum(project: Project, sample: str, key: str, primary: str | None = None) -> str:
    """SHA-256 (first 16 hex) of what the estimate reads: the sampled rows' record, band, weight and final label (with
    ``primary``) and set ``key``'s decision on each. A decision on an unsampled record leaves it equal."""
    from .estimate import _rows

    rows = sorted(_rows(project, sample, key, primary), key=lambda r: r["record_id"])
    text = ";".join(f"{r['record_id']}:{r['band']}:{r['w']:.9g}:{r['human']}:{r['screen']}" for r in rows)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def store(project: Project, sample: str, key: str, at: str, primary: str | None = None,
          bounds: dict | None = None) -> dict:
    """Store the series of set ``key`` on ``sample`` with time ``at`` (the stored lines' time) and its checksum.
    ``bounds`` are the estimate's exact bounds; they are stored only when the series reads the final labels, the
    labels the bounds were computed from (D9)."""
    bounds = bounds if primary is None else None
    s = series(project, sample, key, primary=primary, bounds=bounds)
    rec = {"result_set_key": key, "at": at, "variant": s["variant"], "primary": primary, "bands": s["bands"],
           "checksum": checksum(project, sample, key, primary), "points": s["points"],
           "level": bounds["level"] if bounds else project.config.protocol.recall.ci_level,
           "has_bounds": bounds is not None, "band_details": s["band_details"], "ends": s["ends"],
           "population": POPULATION}
    project.store.set_meta(META.format(sample=sample), json.dumps(rec))
    return rec


def stored(project: Project, sample: str) -> dict | None:
    raw = project.store.get_meta(META.format(sample=sample))
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None
