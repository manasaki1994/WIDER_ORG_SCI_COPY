"""End to end on the synthetic example (employee voice and silence): init → import → coverage → dedup → embed → rank →
sample → label → screen → recall → report, with the fake provider and fake embeddings."""
import csv as _csv
import importlib.util
import shutil
import sqlite3
import sys
from pathlib import Path

import yaml
from typer.testing import CliRunner

from wider.cli import app
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.screen import compare

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "synthetic_project"
GENERATOR = ROOT / "scripts" / "make_synthetic_example.py"
runner = CliRunner()

# A record is on topic when its title or abstract carries one of these words (see scripts/make_synthetic_example.py).
MARKERS = ("voice", "speaking up", "speak up", "silence", "whistleblow", "issue selling", "upward communication", "dissent")


# Synthetic exports of two citation tools (papers-beyond-the-query, task 8.2): invented papers that cite the seeds.
# The first is imported below; the second stays in the project folder for an import through the UI.
CANDIDATES_CSV = """title,doi,year,abstract,keywords,cites
Prosocial rule breaking by frontline employees,10.9999/cand.001,2021,Frontline employees bend rules to help customers.,prosocial rule breaking; service,10.9999/syn.001; 10.9999/syn.003
Prosocial rule breaking and supervisor trust in nursing teams,10.9999/cand.002,2022,Nurses bend rules for patients and supervisors respond.,prosocial rule breaking; trust,10.9999/syn.001; 10.9999/syn.007
Speaking up about safety in surgical teams,10.9999/cand.003,2020,Team members raise concerns about safety.,voice; safety,10.9999/syn.003; 10.9999/syn.005
Coral bleaching under warming seas,10.9999/cand.004,2019,Reef surveys of temperature stress.,coral; ocean,10.9999/syn.005
"""
CANDIDATES_RIS = """TY  - JOUR
TI  - Constructive deviance among hotel staff
PY  - 2023
DO  - 10.9999/cand.005
AB  - Staff depart from rules to serve guests.
KW  - constructive deviance
ER  -
TY  - JOUR
TI  - Speaking up about safety in surgical teams
PY  - 2020
DO  - 10.9999/cand.003
ER  -
"""


def on_topic(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in MARKERS)


def example_dir(tmp_path: Path) -> Path:
    """The committed example, or a fresh copy generated into tmp when its (gitignored) RIS inputs or seed PDF are
    missing."""
    if (EXAMPLE / "inputs").exists() and (EXAMPLE / "seed_pdfs").exists():
        return EXAMPLE
    out = tmp_path / "example"
    spec = importlib.util.spec_from_file_location("make_synthetic_example", GENERATOR)
    mod = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    sys.argv = [str(GENERATOR), str(out)]
    try:
        spec.loader.exec_module(mod)
        mod.OUT = out
        mod.main()
    finally:
        sys.argv = old_argv
    return out


def run(*args):
    r = runner.invoke(app, [str(a) for a in args])
    assert r.exit_code == 0, f"wider {' '.join(map(str, args))}\n{r.stdout}\n{r.exception}"
    return r.stdout


def raise_demo_questions(project: Project) -> list[int]:
    """Open questions for the walkthrough and the ui-tester (review-plan-and-question-window D15): two seed papers
    whose abstract was "not found" and one record without an abstract, raised as the lookups would raise them, so
    the question window can be reached without an online lookup."""
    from wider.core.questions import raise_question

    ids = []
    for r in [b for b in project.read_benchmark() if b.set == "Development"][1:3]:
        ids.append(raise_question(project, "seed_unresolved", r.benchmark_id, r.title or r.benchmark_id,
                                  f"No source found an abstract for seed paper {r.benchmark_id} by its DOI {r.doi}. "
                                  "Give the correct DOI, or leave it; proposals are grounded in the seed papers' abstracts.",
                                  context={"doi": r.doi, "set": r.set}, ref=r.benchmark_id))
    rec = project.store.query("SELECT record_id, title, doi, year FROM records ORDER BY record_id LIMIT 1")[0]
    ids.append(raise_question(project, "enrich_unresolved", str(rec["record_id"]), rec["title"],
                              f"No source had an abstract for this record by its DOI {rec['doi']}. Correct the DOI, "
                              "paste the abstract, or leave it; ranking and screening read the abstract.",
                              context={"doi": rec["doi"], "year": rec["year"]}, record_id=rec["record_id"]))
    return ids


def test_pipeline(tmp_path, monkeypatch):
    example = example_dir(tmp_path)
    proj = tmp_path / "syn"
    run("init", proj, "-q", "What leads employees to speak up, stay silent or report problems at work, and what follows when they do?")
    for key in ("alpha", "beta", "gamma"):  # synthetic platforms are custom entries (init takes catalogue keys only)
        run("query", "platforms", "add", key, "--syntax", "scopus", "--dir", proj)
    shutil.copytree(example / "inputs", proj / "inputs", dirs_exist_ok=True)
    shutil.copy(example / "benchmark.csv", proj / "benchmark.csv")
    cfg = yaml.safe_load((proj / "project.yaml").read_text())
    cfg["providers"].append({"name": "fake", "kind": "fake", "model": "fake", "temperature": 0, "seed": 1, "max_concurrency": 4, "timeout_s": 10, "extra": {}})
    for key in ("a", "b"):  # two fake-kind backends with distinct models, for the configuration comparison below
        cfg["providers"].append({"name": f"fake_{key}", "kind": "fake", "model": f"fake-{key}1", "temperature": 0, "seed": 1,
                                 "max_concurrency": 4, "timeout_s": 10, "extra": {}})
    cfg["default_provider"] = "fake"
    cfg["embeddings"] = [{"name": "fake", "kind": "fake", "model": "fake", "dim": 64, "batch_size": 64, "max_words": 3000, "extra": {}}]
    cfg["default_embedding"] = "fake"
    cfg["bands"] = [{"name": "S1", "start": 1, "end": 8, "n": None}, {"name": "S2", "start": 9, "end": 30, "n": 8}, {"name": "S3", "start": 31, "end": None, "n": 6}]
    (proj / "project.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    out = run("import", "--dir", proj)
    assert "alpha" in out and "beta" in out
    # the seed PDF of B1: extracted and mined on this computer (pattern pass only)
    run("benchmark", "pdf", "add", "B1", example / "seed_pdfs" / "B1.pdf", "--dir", proj)
    out = run("seeds", "extract", "--dir", proj)
    assert "B1: 2 pages" in out and "3 references" in out
    listed = run("benchmark", "pdf", "list", "--dir", proj).splitlines()
    assert sum("extracted" in line for line in listed) == 1 and listed[0].startswith("B1")
    out = run("seeds", "mine", "--no-model", "--dir", proj)
    assert "upward voice" in out and "same as: employee voice" in out
    # papers beyond the query, with the online switches off: a synonyms draft, candidates from a file (nothing sent),
    # the names they share that the query misses, and the reference list of B1 from its PDF
    run("construct", "save", "synonyms", ROOT / "showcase" / "demo" / "synonyms.yaml", "--note", "synthetic demo", "--dir", proj)
    (proj / "candidates").mkdir()
    (proj / "candidates" / "citationchaser.csv").write_text(CANDIDATES_CSV, encoding="utf-8")
    (proj / "candidates" / "researchrabbit.ris").write_text(CANDIDATES_RIS, encoding="utf-8")
    out = run("benchmark", "candidates", "--import", proj / "candidates" / "citationchaser.csv", "--origin", "citationchaser", "--dir", proj)
    assert "4 new candidate(s)" in out and "nothing was sent" in out
    out = run("query", "missed-names", "--dir", proj)
    assert "prosocial rule breaking → block speaking_up" in out
    out = run("benchmark", "references", "B1", "--route", "pdf", "--dir", proj)
    assert "B1: PDF 3 entries" in out
    for kind in ("cells", "anchors", "codebook", "near_miss"):
        run("construct", "save", kind, example / "artifacts" / kind / "v1.yaml", "--dir", proj)
        run("construct", "approve", kind, 1, "--dir", proj)
    out = run("coverage", "--dir", proj)
    assert "Development" in out
    out = run("dedup", "run", "--dir", proj)
    assert "unique_records" in out
    run("embed", "corpus", "--dir", proj)
    run("rank", "run", "--dir", proj)
    out = run("rank", "validate", "--dir", proj)
    assert "median_rank" in out
    run("rank", "cluster", "--dir", proj, "--k", "3", "--top", "30")
    out = run("sample", "draw", "both", "--dir", proj)
    assert "validation" in out and "development" in out
    run("screen", "build-prompt", "--dir", proj)

    def responder(req):
        hit = on_topic(req.user)
        return {"summary": "s", "e1": "present" if hit else "absent", "e1_quote": "voice" if hit else "plankton ocean temperature",
                "e2": "present", "e2_quote": "supervisors", "e3": "present", "e3_quote": "antecedents consequences",
                "e4": "present", "e4_quote": "employees", "voice_subscale_only": False, "formal_scheme_only": False,
                "target_fit": "core" if hit else "unrelated", "edge_case": "none",
                "decision": "include" if hit else "exclude", "exclusion_reason": "none" if hit else "a",
                "missing_for_maybe": "", "cell": "voice_antecedent" if hit else "not_applicable",
                "target": "supervisor" if hit else "not_applicable"}

    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, responder))
    # human labels for both samples from the same oracle, via CSV export/import
    for sample in ("validation", "development"):
        csv_path = tmp_path / f"{sample}.csv"
        run("label", "export", sample, csv_path, "--dir", proj)
        rows = list(_csv.DictReader(open(csv_path)))
        for r in rows:
            r["decision"] = "include" if on_topic(r["title"] + " " + r["abstract"]) else "exclude"
        with open(csv_path, "w", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        # the oracle never saw model output: its labels are declared independent (label-completion-and-sealing D11)
        out = run("label", "import", sample, csv_path, "--reviewer", "oracle",
                  "--independent", "synthetic oracle, labelled from the records alone", "--dir", proj)
        assert "'imported'" in out
    out = run("screen", "run", "sample:development", "--dir", proj)
    assert "'n_err': 0" in out
    run_id = sqlite3.connect(proj / "store.sqlite").execute("SELECT run_id FROM runs").fetchone()[0]
    out = run("screen", "evaluate", run_id, "--dir", proj)
    assert "'misses': 0" in out
    out = run("screen", "protocol", "corpus", "--name", "main", "--dir", proj)
    assert "'protocol': 'main'" in out
    out = run("recall", "main", "--sample", "validation", "--dir", proj)
    assert "lenient" in out
    run("sample", "lock", "validation", "--dir", proj)
    run("report", "--dir", proj, "--bundle")
    assert (proj / "exports" / "report.md").exists() and (proj / "exports" / "share_bundle" / "records.csv").exists()
    out = run("screen", "replay", run_id, "--dir", proj)
    assert "'changed': 0" in out
    # compare two configurations on the locked development sample; the project is left with development locked
    run("sample", "lock", "development", "--dir", proj)
    out = run("screen", "compare", "run", "cmp1", "--config", "fake_a", "--config", "fake_b", "--repeats", "3", "--dir", proj)
    assert "fake_a repeat 3" in out and "fake_b repeat 3" in out
    out = run("screen", "compare", "show", "cmp1", "--dir", proj)
    assert out.index("fake_a") < out.index("fake_b")
    assert out.count(compare.NOT_CORPUS) == 1 and "best" not in out.lower()
    pr = Project.find(proj)
    assert compare.check_sample(pr, "development")["locked"]
    assert all(raise_demo_questions(pr))   # left open for the walkthrough and the ui-tester
    pr.close()
