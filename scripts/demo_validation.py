"""A synthetic validation demo project (validation-sample-planner 7.2): 600 ranked records, three bands, a screened
protocol `main`, a recall target and a labelled probability sample whose labels reach the chosen outcome. Half the
labels are made before the codebook is frozen, so the sample card shows criteria counts. Synthetic records only.

Usage: PYTHONPATH=. .venv/bin/python scripts/demo_validation.py DIR [--outcome met|not-met|cannot-tell]
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from tests.unit.test_screen import CODEBOOK
from wider.construct import artifacts
from wider.core.models import SamplingBand as Band
from wider.core.project import Project
from wider.recall import validation as va
from wider.sample.design import draw_probability
from wider.sample.labels import label
from wider.screen.decisions import set_decision

N = 600
# which records are relevant, and which the screening keeps, per outcome
RELEVANT = lambda r: (r * 7919) % 100 < (40 if r <= 100 else 15 if r <= 300 else 4)  # noqa: E731
KEEP = {"met": lambda r: True, "not-met": lambda r: (r * 31) % 10 < 4, "cannot-tell": lambda r: (r * 31) % 10 < 9}


def build(root: Path, outcome: str) -> Project:
    os.environ.setdefault("WIDER_REVIEWER", "demo")
    p = Project.init(root, platforms=["scopus"])
    p.config.bands = [Band(name="S1", start=1, end=100, n=100), Band(name="S2", start=101, end=300, n=150),
                      Band(name="S3", start=301, end=None, n=200)]
    p.save()
    st = p.store
    st.executemany("INSERT INTO records(record_id, cluster_id, key, title, abstract_words, thin) VALUES(?,?,?,?,?,0)",
                   [(i, i, f"k{i}", f"Synthetic record {i}", 200) for i in range(1, N + 1)])
    st.execute("INSERT INTO rankings(ranking_id,name,set_name,anchors_version,top_n,n,md5,created_at) "
               "VALUES(1,'r1','fake',1,100,?,'x','2026-01-01')", (N,))
    st.executemany("INSERT INTO ranking_rows(ranking_id,record_id,rank,score,cell_scores) VALUES(1,?,?,?,?)",
                   [(i, i, 1 - i / N, '{"c": 0.5}') for i in range(1, N + 1)])
    st.set_meta("default_ranking", "r1")
    va.set_target(p, 0.8, "maybes-as-includes", author="demo")
    draw_probability(p)
    keep = KEEP[outcome]
    for r in range(1, N + 1):
        set_decision(p, "main", r, "include" if RELEVANT(r) and keep(r) else "exclude")
    ids = [x["record_id"] for x in st.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")]
    half = len(ids) // 2
    for i, rid in enumerate(ids):
        if i == half:
            v = artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")["version"]
            artifacts.set_status(p, "codebook", v, "frozen")
        label(p, "validation", rid, "demo", "include" if RELEVANT(rid) else "exclude")
    return p


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", type=Path)
    ap.add_argument("--outcome", choices=sorted(KEEP), default="cannot-tell")
    a = ap.parse_args()
    build(a.dir, a.outcome)
    print(f"demo project at {a.dir} ({a.outcome}); try: wider recall main --dir {a.dir}")
