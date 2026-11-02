"""The recall-versus-yield plot of the rounds (D7, D8), hand-written SVG with the same numbers in a text table.

One point per round and platform that has a dry run: development recall up, records exported (or typed) across. A
count typed by hand is drawn hollow and says "pasted"; a round with no count yet sits on the recall axis only.
"""
from __future__ import annotations

from html import escape

W, H, L, R, T, B = 640, 360, 70, 30, 40, 50   # size and margins


def points(rounds: list[dict]) -> list[dict]:
    """The plotted numbers, one per round and platform with a dry run (the table and the SVG share them)."""
    out = []
    for r in rounds:
        for p in r["platforms"]:
            if not p.get("recall"):
                continue
            rc = p["recall"]
            out.append({"round": r["round"], "platform": p["platform"], "hits": rc["hits"], "total": rc["total"],
                        "recall": rc["hits"] / rc["total"] if rc["total"] else 0.0, "count": p.get("count"),
                        "source": p.get("count_source")})
    return out


def _count_text(pt: dict) -> str:
    if pt["count"] is None:
        return "no export yet (recall axis only)"
    from .rounds import records
    return records(pt["count"]) + (" (pasted)" if pt["source"] == "pasted" else " (export)")


def _label(pt: dict) -> str:
    return f"v{pt['round']} {pt['platform']}: recall {pt['hits']}/{pt['total']}, {_count_text(pt)}"


def recall_vs_yield_table(rounds: list[dict]) -> list[str]:
    """The text table next to the plot: the same numbers as its ``<desc>``."""
    pts = points(rounds)
    return [_label(pt) for pt in pts] or ["no dry run yet: nothing to plot"]


def recall_vs_yield(rounds: list[dict], title: str = "Recall versus yield by round") -> str:
    """``recall_vs_yield.svg``: development recall (0-1) against the record count, per round and platform."""
    pts = points(rounds)
    xmax = max([pt["count"] for pt in pts if pt["count"] is not None] or [1]) * 1.1 or 1
    pw, ph = W - L - R, H - T - B
    sx = lambda n: L + pw * (n / xmax)        # noqa: E731
    sy = lambda v: T + ph * (1 - v)           # noqa: E731
    desc = "; ".join(_label(pt) for pt in pts) or "no dry run yet: nothing to plot"
    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
         f'font-family="sans-serif" font-size="12">',
         f"<title>{escape(title)}</title>", f"<desc>{escape(desc)}</desc>",
         f'<rect width="{W}" height="{H}" fill="white"/>',
         f'<line x1="{L}" y1="{T}" x2="{L}" y2="{T + ph}" stroke="black"/>',
         f'<line x1="{L}" y1="{T + ph}" x2="{L + pw}" y2="{T + ph}" stroke="black"/>',
         f'<text x="{L + pw / 2}" y="{H - 12}" text-anchor="middle">records (export, or pasted: hollow)</text>',
         f'<text x="16" y="{T + ph / 2}" text-anchor="middle" transform="rotate(-90 16 {T + ph / 2})">development recall</text>']
    for v in (0, 0.5, 1):
        s.append(f'<text x="{L - 8}" y="{sy(v) + 4:.1f}" text-anchor="end">{v:g}</text>')
    s.append(f'<text x="{L + pw}" y="{T + ph + 16}" text-anchor="end">{int(xmax):,}</text>')
    for pt in pts:
        y = sy(pt["recall"])
        if pt["count"] is None:   # no count yet: a mark on the recall axis, no point in the plane
            s.append(f'<g class="axis-only"><desc>{escape(_label(pt))}</desc>'
                     f'<line x1="{L - 6}" y1="{y:.1f}" x2="{L + 6}" y2="{y:.1f}" stroke="black" stroke-width="2"/>'
                     f'<text x="{L + 10}" y="{y - 4:.1f}">v{pt["round"]} {escape(pt["platform"])}</text></g>')
            continue
        x = sx(pt["count"])
        fill = "none" if pt["source"] == "pasted" else "black"
        s.append(f'<g class="point{" pasted" if pt["source"] == "pasted" else ""}"><desc>{escape(_label(pt))}</desc>'
                 f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{fill}" stroke="black" stroke-width="1.5"/>'
                 f'<text x="{x + 8:.1f}" y="{y - 6:.1f}">v{pt["round"]} {escape(pt["platform"])}</text></g>')
    s.append("</svg>")
    return "\n".join(s)
