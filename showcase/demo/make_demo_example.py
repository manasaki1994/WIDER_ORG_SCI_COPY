#!/usr/bin/env python3
"""Write the interactive demo's example: the synthetic example's artifacts (codebook, cells, anchors, near-miss
texts from scripts/make_synthetic_example.py) with the readable invented papers of showcase/demo/papers.yaml as the
three fake databases' exports, the benchmark, and the reference decisions of "Reviewer A" (reference_labels.csv).

Usage: python3 showcase/demo/make_demo_example.py OUTDIR

showcase/build_project.py OUTPROJECT --example OUTDIR then runs the whole pipeline on it. Everything is invented.
"""
from __future__ import annotations

import csv
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SURNAMES = ["Abara", "Bello", "Corvin", "Dalby", "Esposito", "Farrow", "Gunnarsdottir", "Hale", "Ivanova", "Jansen",
            "Kowal", "Lindqvist", "Moreau", "Nwosu", "Ortega", "Park"]
JOURNALS = ["Journal of Invented Workplace Studies", "Invented Organisation Review", "Studies of Invented Work"]


def load() -> dict:
    return yaml.safe_load((HERE / "papers.yaml").read_text(encoding="utf-8"))


def ris(p: dict, n: int, plat: str, doi: bool = True, title_suffix: str = "", year_shift: int = 0) -> str:
    """One RIS record; beta writes the ProQuest-like tags, the others the Scopus-like ones (as in the example)."""
    authors = [f"{SURNAMES[(n * 3 + k) % len(SURNAMES)]}, {'ABCDEFGH'[(n + k) % 8]}." for k in range(1 + n % 3)]
    journal = JOURNALS[n % len(JOURNALS)]
    title, year, ab = p["title"] + title_suffix, p["year"] + year_shift, " ".join(p["abstract"].split())
    doi_s = f"10.9999/demo.{n:03d}"
    if plat == "beta":
        lines = [f"T1  - {title}", *[f"AU  - {a}" for a in authors], f"JF  - {journal}", f"Y1  - {year}-01-01",
                 *([f"DO  - https://doi.org/{doi_s}"] if doi else []), f"AB  - {ab}"]
    else:
        lines = [f"TI  - {title}", *[f"AU  - {a}" for a in authors], f"T2  - {journal}", f"PY  - {year}",
                 *([f"DO  - {doi_s}"] if doi else []), f"AB  - {ab}", "KW  - invented"]
    return "\n".join(["TY  - JOUR", *lines, "ER  -", ""])


def main() -> None:
    out = Path(sys.argv[1])
    if out.exists():
        sys.exit(f"{out} exists; give a new folder")
    # the synthetic example's artifacts; its own records, benchmark and seed PDF are replaced below
    subprocess.run([sys.executable, str(REPO / "scripts" / "make_synthetic_example.py"), str(out)], check=True,
                   capture_output=True)
    shutil.rmtree(out / "inputs", ignore_errors=True)
    shutil.rmtree(out / "seed_pdfs", ignore_errors=True)
    data = load()
    papers = data["papers"]
    texts: dict[str, list[str]] = {"alpha": [], "beta": [], "gamma": []}
    for n, p in enumerate(papers, 1):
        for k, plat in enumerate(p["found_in"]):
            # planted duplicates without a DOI: every third paper's last copy
            texts[plat].append(ris(p, n, plat, doi=not (n % 3 == 0 and k == len(p["found_in"]) - 1 and k > 0)))
    # one more planted duplicate: a copy with a trailing period and the wrong year
    texts["gamma"].append(ris(papers[1], 2, "gamma", doi=False, title_suffix=".", year_shift=1))
    for plat, recs in texts.items():
        d = out / "inputs" / "ris" / plat
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{plat}_export.ris").write_text("\n".join(recs) + "\n", encoding="utf-8")
    by_id = {p["id"]: (n, p) for n, p in enumerate(papers, 1)}
    with open(out / "benchmark.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["benchmark_id", "title", "doi", "set"])
        j = 0
        for s in ("development", "validation"):
            for pid in data["benchmark"][s]:
                j += 1
                n, p = by_id[pid]
                w.writerow([f"B{j}", p["title"], f"10.9999/demo.{n:03d}", s.capitalize()])
    with open(out / "reference_labels.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["paper", "title", "decision", "kind", "why"])
        for p in papers:
            w.writerow([p["id"], p["title"], p["decision"], p["kind"], p.get("why", "")])
    # the search terms: two short hand-written blocks a first-time visitor can read (no model proposal)
    shutil.copy(HERE / "synonyms.yaml", out / "synonyms.yaml")
    (out / "README.md").write_text("# Interactive demo example\n\nInvented papers from showcase/demo/papers.yaml with the "
                                   "synthetic example's artifacts. Regenerate with showcase/demo/make_demo_example.py.\n",
                                   encoding="utf-8")
    print("wrote", out, "papers:", len(papers), "records:", sum(len(v) for v in texts.values()))


if __name__ == "__main__":
    main()
