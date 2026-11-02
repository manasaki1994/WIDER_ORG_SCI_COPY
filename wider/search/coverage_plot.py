"""The per-database coverage plot of a dry run (vision change 2a, D7, task 2.7): one stacked bar per platform with its
hits, uncertain seeds and misses, and the text table with the same numbers next to it.

Hand-written SVG, no plotting dependency: :func:`coverage_bars` draws the bars from :func:`coverage_table`, so the plot
and the table cannot disagree; the ``<desc>`` lists the numbers and every bar segment carries them as ``data-*``.
The dry run writes the plot to ``runs/query/<stamp>/coverage_by_platform.svg`` (:data:`SVG_NAME`).
"""
from __future__ import annotations

import hashlib
from html import escape

SVG_NAME = "coverage_by_platform.svg"
STATES = ("hits", "uncertain", "misses")
# colour-blind-safe pair plus a neutral grey; text uses currentColor so the plot reads in light and dark pages
COLOURS = {"hits": "#1b7837", "uncertain": "#e08214", "misses": "#8c8c8c"}
WORDS = {"hits": "hit", "uncertain": "uncertain", "misses": "miss"}


def _label(key: str) -> str:
    from .render import platforms

    return str(platforms().get(key, {}).get("label", key))


def coverage_table(dry_run: dict) -> list[dict]:
    """One row per platform of the dry run, in its order: hits, uncertain, misses and the seeds evaluated."""
    rows = []
    for key, pl in (dry_run.get("platforms") or {}).items():
        rec = pl["recall"]
        rows.append({"platform": key, "label": _label(key), "hits": rec["hits"], "uncertain": rec["uncertain"],
                     "misses": rec["misses"], "total": rec["total"]})
    return rows


def table_lines(rows: list[dict]) -> list[str]:
    """The table as plain text lines (the CLI prints them; the SVG's ``<desc>`` repeats them)."""
    return [f"{r['platform']}: {r['hits']} hit, {r['uncertain']} uncertain, {r['misses']} miss of {r['total']}"
            for r in rows]


def coverage_bars(dry_run: dict) -> str:
    """The SVG text: platforms down the side, hits, uncertain and misses stacked left to right per platform."""
    rows = coverage_table(dry_run)
    left, bar_w, row_h, top = 210, 360, 26, 34
    most = max([r["total"] for r in rows] + [1])
    height = top + row_h * len(rows) + 30
    width = left + bar_w + 70
    title = f"Dry run coverage per database, synonyms v{dry_run.get('version', '?')}"
    desc = "; ".join(table_lines(rows)) or "no platform evaluated"
    # ids from the plot's own text: two different plots drawn into one page share none, and the same result
    # always draws the same SVG (a stored dry run equals its recomputation)
    pid = "cov-" + hashlib.sha1(f"{title}\n{desc}".encode()).hexdigest()[:8]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'role="img" aria-labelledby="{pid}-title {pid}-desc" font-family="sans-serif" font-size="12">',
           f'<title id="{pid}-title">{escape(title)}</title>', f'<desc id="{pid}-desc">{escape(desc)}</desc>']
    x = left
    for s in STATES:   # the legend
        out.append(f'<rect x="{x}" y="8" width="12" height="12" fill="{COLOURS[s]}"/>'
                   f'<text x="{x + 16}" y="18" fill="currentColor">{WORDS[s]}</text>')
        x += 90
    for i, r in enumerate(rows):
        y = top + i * row_h
        out.append(f'<g data-platform="{escape(r["platform"])}">')
        out.append(f'<text x="{left - 8}" y="{y + 15}" text-anchor="end" fill="currentColor">{escape(r["label"][:32])}</text>')
        x = left
        for s in STATES:
            n = r[s]
            w = round(bar_w * n / most, 1)
            out.append(f'<rect x="{x}" y="{y + 2}" width="{w}" height="{row_h - 8}" fill="{COLOURS[s]}" '
                       f'data-state="{s}" data-n="{n}"><title>{escape(r["platform"])}: {n} {WORDS[s]}</title></rect>')
            if n and w >= 18:
                out.append(f'<text x="{x + w / 2}" y="{y + 15}" text-anchor="middle" fill="#fff">{n}</text>')
            x += w
        out.append(f'<text x="{x + 6}" y="{y + 15}" fill="currentColor">{r["hits"]}/{r["total"]}</text></g>')
    out.append(f'<text x="{left}" y="{height - 8}" fill="currentColor">seed papers per platform; uncertain is never '
               'counted as retrieved</text>')
    out.append("</svg>")
    return "\n".join(out)


def attach(dry_run: dict) -> dict:
    """Add the table (``coverage``) and the SVG text (``coverage_svg``) to a dry-run result; the CLI and the route
    return the same dict, so both show the same numbers. Returns the result."""
    dry_run["coverage"] = coverage_table(dry_run)
    dry_run["coverage_svg"] = coverage_bars(dry_run)
    return dry_run


def write_plot(run_dir, dry_run: dict) -> None:
    """Write :data:`SVG_NAME` into the run folder."""
    from pathlib import Path

    (Path(run_dir) / SVG_NAME).write_text(dry_run.get("coverage_svg") or coverage_bars(dry_run), encoding="utf-8")


def print_table(res: dict) -> None:
    """The CLI's coverage table and the plot's path (one call from ``print_dry_run``)."""
    from rich import print as rprint
    from rich.table import Table

    t = Table("platform", "hits", "uncertain", "misses", "seeds", title="coverage per database")
    for r in res.get("coverage") or coverage_table(res):
        t.add_row(r["platform"], str(r["hits"]), str(r["uncertain"]), str(r["misses"]), str(r["total"]))
    rprint(t)
    if res.get("run_dir"):
        rprint(f"plot written to {res['run_dir']}/{SVG_NAME}")
