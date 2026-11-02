"""Tables from sim.py's output: per scenario, design and interval, how often an interval is given, its median width,
coverage where given, P(lower >= 0.80), and false assurance P(lower >= 0.90 while the true recall is below 0.90).

Usage: python analyse.py RAW.jsonl [RAW2.jsonl ...] > table.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import numpy as np

METHODS = ("KG", "AT", "KG|AT")


def intervals(row):
    errors, r, B, d, true, R, klo, khi, alo, ahi, nrel = row
    kg = (klo, khi) if klo is not None else None
    at = (alo, ahi)
    return {"KG": kg, "AT": at, "KG|AT": kg or at}


def main(paths):
    cells = defaultdict(list)
    for p in paths:
        for line in open(p):
            o = json.loads(line)
            for row in o["rows"]:
                cells[(o["N"], o["p"], o["auc"], row[0], row[1], row[2], row[3])].append(row)
    out = defaultdict(dict)
    for key, rows in cells.items():
        true = np.array([r[4] for r in rows])
        for m in METHODS:
            iv = [intervals(r)[m] for r in rows]
            given = np.array([x is not None for x in iv])
            lo = np.array([x[0] if x else np.nan for x in iv])
            hi = np.array([x[1] if x else np.nan for x in iv])
            cov = (lo <= true + 1e-12) & (true <= hi + 1e-12)
            w = (hi - lo)[given]
            out[key][m] = {
                "n": len(rows), "given": given.mean(),
                "width": float(np.median(w)) if len(w) else None,
                "cov": float(cov[given].mean()) if given.any() else None,
                "lo80": float(np.nanmean(np.where(given, lo >= 0.80, False))),
                "fa90": float(np.mean(given & (lo >= 0.90) & (true < 0.90))),
                "p_true_below": float(np.mean(true < 0.90)),
                "useful": float(np.mean(given & ((hi - lo) <= 0.30))),
            }
    print("| N | AUC | prev | misses | LLM r | B | design | interval | reps | given | median width | coverage | "
          "width <= 0.30 | lower >= 0.80 | false lower >= 0.90 (true < 0.90 in) |")
    print("|" + "---|" * 16)
    for key in sorted(out, key=lambda k: (k[0], k[2], k[1], k[3], k[4], k[5], ("shipped", "sqrt", "prop", "ropt", "srs", "fixed", "top+rest", "3band").index(k[6]))):
        N, p, auc, errors, r, B, d = key
        for m in METHODS:
            s = out[key][m]
            f = lambda x, fmt="{:.2f}": "–" if x is None else fmt.format(x)  # noqa: E731
            print(f"| {N} | {auc} | {p:.1%} | {errors} | {r} | {B} | {d} | {m} | {s['n']} | {s['given']:.0%} | "
                  f"{f(s['width'])} | {f(s['cov'])} | {s['useful']:.0%} | {s['lo80']:.0%} | "
                  f"{s['fa90']:.1%} ({s['p_true_below']:.0%}) |")


if __name__ == "__main__":
    main(sys.argv[1:])
