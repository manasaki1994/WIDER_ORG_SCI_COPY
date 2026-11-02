"""The interactive demo's invented example (openspec change interactive-demo, design D3): the files
make_demo_example.py writes agree with showcase/demo/papers.yaml, the one source of the papers and their decisions."""
import csv
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

DEMO = Path(__file__).resolve().parents[2] / "showcase" / "demo"


def _papers():
    return yaml.safe_load((DEMO / "papers.yaml").read_text(encoding="utf-8"))


def _example(tmp_path):
    out = tmp_path / "example"
    subprocess.run([sys.executable, str(DEMO / "make_demo_example.py"), str(out)], check=True, capture_output=True)
    return out


def test_reference_labels_match_papers(tmp_path):
    data = _papers()
    papers = data["papers"]
    out = _example(tmp_path)
    with open(out / "reference_labels.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["paper"], r["title"], r["decision"], r["kind"], r["why"]) for r in rows] == [
        (p["id"], p["title"], p["decision"], p["kind"], p.get("why", "")) for p in papers]

    kinds = Counter(p["kind"] for p in papers)
    assert kinds == {"include": 16, "borderline": 11, "near": 6, "unrelated": 6}
    assert all(p["decision"] == "include" for p in papers if p["kind"] == "include")
    assert all(p["decision"] == "exclude" for p in papers if p["kind"] in ("near", "unrelated"))
    assert all(p.get("why") for p in papers if p["kind"] == "borderline")
    assert sum(p["decision"] == "include" for p in papers) == 19

    # the three fake databases hold one record per paper and database, plus one planted near-copy
    records = sum((out / "inputs" / "ris" / db / f"{db}_export.ris").read_text(encoding="utf-8").count("\nER  -")
                  for db in ("alpha", "beta", "gamma"))
    assert records == sum(len(p["found_in"]) for p in papers) + 1 == 74

    # the benchmark names papers of the list, each once, with the DOI the exports give that paper
    ids = [p["id"] for p in papers]
    bench = data["benchmark"]["development"] + data["benchmark"]["validation"]
    assert len(bench) == len(set(bench)) and set(bench) <= set(ids)
    with open(out / "benchmark.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["doi"] for r in rows] == [f"10.9999/demo.{ids.index(b) + 1:03d}" for b in bench]
