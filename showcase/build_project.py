#!/usr/bin/env python3
"""Build the showcase project: the invented "employee voice and silence" example (examples/synthetic_project) run
through the whole pipeline on this computer, so the showcase video has real-looking results on every page.

Usage: .venv/bin/python showcase/build_project.py OUTDIR [--provider ollama|openai] [--model NAME]

By default nothing leaves the computer: screening uses a local Ollama model (already pulled; this script never
pulls one). With --provider openai screening goes to OpenAI (OPENAI_API_KEY must be set).
The embeddings always use a local sentence-transformers model that must already be in the Hugging Face cache (BAAI/bge-m3
by default; pass --fake-embeddings to use the fake embeddings instead). Human labels come from the example's marker
words, like tests/e2e, under the reviewer name "Reviewer A". The data is synthetic; the project is never committed.

The example is always generated fresh into OUTDIR.example by scripts/make_synthetic_example.py: a local
examples/synthetic_project/inputs folder (gitignored) may be left over from an older generator and is never used.
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
WIDER = str(REPO / ".venv" / "bin" / "wider")
sys.path.insert(0, str(REPO / "scripts"))
from make_synthetic_example import TOPIC_MARKERS  # noqa: E402

OPEN_FOR_VISITOR = 6
QUESTION = "What leads employees to speak up, stay silent or report problems at work, and what follows when they do?"


def wider(*args, proj: Path | None = None) -> str:
    cmd = [WIDER, *map(str, args)] + (["--dir", str(proj)] if proj else [])
    print("$ wider", " ".join(map(str, args)), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def on_topic(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in TOPIC_MARKERS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--provider", choices=("ollama", "openai"), default="ollama")
    ap.add_argument("--model", help="Default: qwen3:8b for ollama, the project default for openai.")
    ap.add_argument("--fake-embeddings", action="store_true")
    ap.add_argument("--no-screen", action="store_true", help="Stop before the model screens (time one call first).")
    ap.add_argument("--screen-only", action="store_true", help="Only screen and report, on a project built before.")
    ap.add_argument("--example", type=Path, help="Use this prepared example folder (showcase/demo/make_demo_example.py) "
                                                 "instead of generating the synthetic one; its reference_labels.csv "
                                                 "gives the human labels.")
    ap.add_argument("--stop-at", choices=("imported", "ranked", "sampled"),
                    help="Stop at this checkpoint of the interactive demo (showcase/demo/build_checkpoints.py), before "
                         "any model call: no key is needed and the providers are not probed (consent is still "
                         "recorded).")
    a = ap.parse_args()
    proj: Path = a.out
    if a.provider == "openai" and not a.stop_at and not os.environ.get("OPENAI_API_KEY"):
        sys.exit("--provider openai needs OPENAI_API_KEY in the environment")
    if a.screen_only:
        return screen_and_report(proj)
    if proj.exists():
        sys.exit(f"{proj} exists; give a new folder")
    example = a.example or proj.with_name(proj.name + ".example")
    if not a.example:
        shutil.rmtree(example, ignore_errors=True)
        subprocess.run([sys.executable, str(REPO / "scripts" / "make_synthetic_example.py"), str(example)], check=True,
                       capture_output=True)
    reference = {}
    if (example / "reference_labels.csv").exists():
        # every decision, label and journal entry of a prepared example is signed by the invented reviewer, never by
        # the login name of the computer that builds it
        os.environ["WIDER_REVIEWER"] = "Reviewer A"
        reference = {r["title"].rstrip(".").lower(): r["decision"]
                     for r in csv.DictReader(open(example / "reference_labels.csv", newline="", encoding="utf-8"))}

    wider("init", proj, "-q", QUESTION, "--title", "Employee voice and silence")
    for key in ("alpha", "beta", "gamma"):
        wider("query", "platforms", "add", key, "--syntax", "scopus", proj=proj)
    shutil.copytree(example / "inputs", proj / "inputs", dirs_exist_ok=True)
    shutil.copy(example / "benchmark.csv", proj / "benchmark.csv")
    cfg = yaml.safe_load((proj / "project.yaml").read_text())
    # one backend only: the showcase shows no cloud cards with missing keys
    if a.provider == "openai":
        cfg["providers"] = [dict(p, model=a.model or p["model"]) for p in cfg["providers"] if p["name"] == "openai"]
    else:
        # thinking off: qwen3 otherwise writes thousands of hidden tokens per call, hours on a laptop
        # a long timeout: a laptop model writes slowly
        cfg["providers"] = [dict(p, model=a.model or "qwen3:8b", timeout_s=1800,
                                 extra={**(p.get("extra") or {}), "think": False})
                            for p in cfg["providers"] if p["kind"] == "ollama"][:1]
    cfg["default_provider"] = cfg["providers"][0]["name"]
    cfg["stage_providers"] = {}
    if a.fake_embeddings:
        cfg["embeddings"] = [{"name": "fake", "kind": "fake", "model": "fake", "dim": 64, "batch_size": 64,
                              "max_words": 3000, "extra": {}}]
        cfg["default_embedding"] = "fake"
    else:
        cfg["embeddings"] = [e for e in cfg["embeddings"] if e["name"] in ("bge-m3", "minilm")]   # both already cached
        cfg["default_embedding"] = next(e["name"] for e in cfg["embeddings"] if e["model"] == "BAAI/bge-m3")
    cfg["bands"] = [{"name": "S1", "start": 1, "end": 8, "n": None}, {"name": "S2", "start": 9, "end": 30, "n": 8},
                    {"name": "S3", "start": 31, "end": None, "n": 6}]
    (proj / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    if a.provider == "openai":
        # choosing --provider openai is the consent; without it each command would stop at a y/N prompt. Recorded at
        # the demo's checkpoints too (a setting, no call), so their model cards agree with the screened ones.
        wider("providers", "allow", "openai", "--for", "probe", "--for", "research", proj=proj)
    if not a.stop_at:
        wider("providers", "probe", proj=proj)

    wider("import", proj=proj)
    for kind in ("cells", "anchors", "codebook", "near_miss"):
        wider("construct", "save", kind, example / "artifacts" / kind / "v1.yaml", proj=proj)
        wider("construct", "approve", kind, 1, proj=proj)
    # the example brings its own search terms (the interactive demo's two short blocks, or the synthetic example's
    # hand-written ones), so the build never waits for a laptop model to write the long synonym proposal
    synonyms = next((f for f in (example / "synonyms.yaml", example / "artifacts" / "synonyms" / "v1.yaml") if f.exists()),
                    None)
    if synonyms:
        wider("construct", "save", "synonyms", synonyms, "--note", "written for the example", proj=proj)
    else:
        wider("construct", "propose", "synonyms", "--note", "showcase", proj=proj)
    wider("construct", "approve", "synonyms", 1, proj=proj)
    wider("construct", "freeze", "synonyms", 1, proj=proj)
    wider("query", "render", proj=proj)
    wider("coverage", proj=proj)
    wider("dedup", "run", proj=proj)
    if a.stop_at == "imported":
        # the demo's first checkpoint: pairs still open for the visitor, embedded so the Rank button works
        wider("embed", "corpus", proj=proj)
        return print(f"done: {proj}")
    if reference:
        decide_pairs(proj)
    wider("embed", "corpus", proj=proj)
    wider("rank", "run", proj=proj)
    wider("rank", "validate", proj=proj)
    wider("rank", "cluster", "--k", "3", "--top", "30", proj=proj)
    if a.stop_at == "ranked":
        return print(f"done: {proj}")
    wider("sample", "draw", "both", proj=proj)
    wider("screen", "build-prompt", proj=proj)

    for sample in ("validation", "development"):
        path = proj / f"{sample}_labels.csv"
        wider("label", "export", sample, path, proj=proj)
        rows = list(csv.DictReader(open(path, newline="")))
        if a.stop_at == "sampled" and sample == "development":
            rows = rows[:-OPEN_FOR_VISITOR]        # the demo leaves the last few for the visitor to label
        for r in rows:
            r["decision"] = reference.get(r["title"].rstrip(".").lower()) or (
                "include" if on_topic(r["title"] + " " + r["abstract"]) else "exclude")
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        # Reviewer A labels from the records alone, never the model's output (label-completion-and-sealing D11)
        wider("label", "import", sample, path, "--reviewer", "Reviewer A",
              "--independent", "synthetic reviewer, labelled from the records alone", proj=proj)
        path.unlink()
    if a.stop_at:
        return print(f"done: {proj}")
    if not a.no_screen:
        screen_and_report(proj)


def decide_pairs(proj: Path) -> None:
    """Decide the open duplicate pairs as Reviewer A would (same title = the same paper), then apply them."""
    from wider.core.project import Project
    from wider.dedup.engine import queue_rows

    p = Project.find(proj)
    rows = queue_rows(p)
    p.close()
    for r in rows:
        same = (r["title_a"] or "").rstrip(".").lower() == (r["title_b"] or "").rstrip(".").lower()
        wider("dedup", "decide", r["raw_a"], r["raw_b"], "duplicate" if same else "not_duplicate", "--by", "Reviewer A",
              proj=proj)
    if rows:
        wider("dedup", "run", proj=proj)


def screen_and_report(proj: Path) -> None:
    wider("screen", "run", "sample:development", proj=proj)
    wider("screen", "protocol", "corpus", "--name", "main", proj=proj)
    wider("recall", "main", "--sample", "validation", proj=proj)
    wider("sample", "lock", "validation", proj=proj)
    wider("report", "--bundle", proj=proj)
    print(f"done: {proj}")


if __name__ == "__main__":
    main()
