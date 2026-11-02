"""Shared stage results: summarisers, meta round trip, API wrapping, CLI print and the recall done_when fix."""
import csv
import json

from fastapi.testclient import TestClient

from wider.core.project import Project
from wider.core.stage_result import (
    META_PREFIX,
    StageResult,
    all_last_results,
    following_stage,
    format_result,
    last_result,
    record,
)
from wider.core.stage_summaries import (
    summarize_artifact,
    summarize_dedup,
    summarize_import,
    summarize_recall,
    summarize_report,
    summarize_samples,
    summarize_screen,
)
from wider.core.stages import stage_states
from wider.ui.app import create_app

RIS = "\n".join(f"TY  - JOUR\nTI  - Record number {i} about things\nPY  - 2020\nDO  - 10.1000/r{i}\nAB  - {'words ' * 25}\nER  -\n" for i in range(12))


def _project(tmp_path, with_file=True):
    from wider.ingest.importer import add_file

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    if with_file:
        (tmp_path / "a.ris").write_text(RIS)
        add_file(p, "scopus", tmp_path / "a.ris")
    return p


# ----------------------------------------------------------------------------- summarisers (pure)
def test_summarize_import_counts_and_warnings():
    r = summarize_import([
        {"platform": "scopus", "file": "a.ris", "status": "imported", "n": 10, "format": "ris", "no_abstract": 8},
        {"platform": "wos", "file": "b.csv", "status": "already", "n": 5},
        {"platform": "wos", "file": "c.txt", "status": "error", "error": "unknown format"},
    ])
    assert r.stage == "import" and r.ok and r.tone == "warning"
    assert r.metrics == {"files imported": 1, "records": 10, "already imported": 1, "errors": 1}
    assert "Imported 1 file with 10 records; 1 already imported." == r.headline
    assert any("no abstract" in w for w in r.warnings) and any("unknown format" in w for w in r.warnings)
    assert len(r.rows) == 3
    assert summarize_import([]).ok is True and "No export files" in summarize_import([]).headline


def test_summarize_dedup_pending_is_a_warning():
    s = {"unique_records": 90, "total_records_imported": 100, "duplicate_records_removed": 10,
         "unresolved_manual_component_pairs_retained": 3, "duplicate_records_removed_by_doi": 6,
         "duplicate_records_removed_by_exact_title": 4, "manually_confirmed_duplicate_pairs": 0, "confirmed_not_duplicate_pairs": 0}
    r = summarize_dedup({"summary": s, "rule_summary": {}, "queue": [], "pending": 3})
    assert r.stage == "dedup" and r.tone == "warning" and r.metrics["unique records"] == 90
    assert "90" in r.headline and "10" in r.headline
    r2 = summarize_dedup({"summary": {**s, "unresolved_manual_component_pairs_retained": 0}, "rule_summary": {}, "queue": [], "pending": 0})
    assert r2.tone == "success"


def test_summarize_samples_screen_recall_report_artifact():
    s = summarize_samples([{"name": "validation", "kind": "probability", "n": 20, "locked": 0}, {"name": "development", "kind": "purposive", "n": 8, "locked": 1}])
    assert s.metrics == {"validation": 20, "development": 8} and s.rows[1]["locked"] == "yes"
    assert summarize_samples([]).ok is False

    sc = summarize_screen({"run_id": "r1", "dir": "runs/r1", "n_records": 10, "n_ok": 9, "n_err": 1, "resumed": False}, "pass")
    assert sc.stage == "screen" and sc.tone == "warning" and sc.metrics["failed"] == 1

    est = {"sample": "validation", "protocol": "main", "n_labelled": 8, "B": 100, "seed": 1, "unscreened_in_sample": 0,
           "primary_variant": "lenient", "ci_level": 0.95, "bands": [], "missed_records": [],
           "variants": {"strict": {"recall": 0.5, "ci_bootstrap": [0.2, 0.8], "ci_linearised": None, "n_relevant": 4, "n_missed": 2, "relevant": ["include"], "kept": ["include"],
                                   "interval": None, "interval_method": None, "interval_reason": "too few relevant papers across the bands to estimate the variance"},
                        "lenient": {"recall": 0.75, "ci_bootstrap": [0.4, 1.0], "ci_linearised": None, "n_relevant": 4, "n_missed": 1, "relevant": ["include"], "kept": ["include", "maybe"],
                                    "interval": [0.31, 0.97], "interval_method": "korn_graubard", "n_effective": 3.1, "df": 2, "interval_reason": None,
                                    "lines": ["Based on 4 relevant validation papers.", "95% interval: 0.31 to 0.97.", "That is few: ..."]}}}
    rc = summarize_recall(est)
    assert rc.stage == "recall" and "75" in rc.headline and len(rc.rows) == 2
    assert "95% interval 0.31 to 0.97" in rc.headline and "4 relevant validation papers" in rc.headline
    assert rc.rows[1]["95% interval"] == "0.31–0.97"
    assert not any(w.startswith("That is few") for w in rc.warnings)   # the notes are said once, in the lines
    assert "1 relevant record was missed (lenient); see the missed list in the report." in rc.warnings
    assert "2 relevant records were missed (strict); see the missed list in the report." in summarize_recall(
        {**est, "primary_variant": "strict"}).warnings
    assert "could not be reliably estimated" in summarize_recall(  # strict has no defensible interval
        {**est, "primary_variant": "strict"}).headline

    rp = summarize_report({"title": "T", "project": "p", "dedup": {"unique_records": 90},  # the shape build_report writes
                           "prisma": [("Records remaining for title and abstract screening", 90)], "recall": est,
                           "screening": {"runs": [1, 2], "protocols": ["main"]}, "benchmark": {"n": 4, "with_abstract": 3}},
                          ("/x/exports/report.md", "/x/exports/report.json"), "/x")
    assert "recall 75.0% (lenient; 95% interval 0.31 to 0.97; 4 relevant validation papers)" in rp.headline
    old = {k: {f: x for f, x in v.items() if not f.startswith("interval") and f != "lines"} for k, v in est["variants"].items()}
    assert summarize_report({"title": "T", "recall": {**est, "variants": old}}).headline == (  # report.json from before
        "Report written for T; recall 75.0% (lenient).")
    assert rp.stage == "recall" and rp.outputs == ["exports/report.md", "exports/report.json"] and rp.metrics["unique records"] == 90
    assert "no recall" in summarize_report({"title": "T", "prisma": [], "screening": {}, "benchmark": {}}).warnings[0]

    a = summarize_artifact({"kind": "synonyms", "version": 2, "status": "frozen", "origin": "manual", "path": "artifacts/synonyms/v2.yaml"}, "status")
    assert a.stage == "query" and "frozen" in a.headline
    assert summarize_artifact({"kind": "cells", "version": 1, "status": "draft", "origin": "llm:fake"}, "propose").stage == "construct"


# ----------------------------------------------------------------------------- store round trip
def test_record_and_last_result_roundtrip(tmp_path):
    p = _project(tmp_path, with_file=False)
    assert last_result(p, "dedup") is None and all_last_results(p) == {}
    r = record(p, StageResult(stage="dedup", action="run", headline="h", metrics={"a": 1}, warnings=["w"]))
    assert r.next_stage == "enrich" == following_stage("dedup")
    got = last_result(p, "dedup")
    assert got["headline"] == "h" and got["tone"] == "warning" and got["next_stage"] == "enrich" and got["ran_at"]
    assert json.loads(p.store.get_meta(META_PREFIX + "dedup"))["metrics"] == {"a": 1}
    record(p, StageResult(stage="dedup", action="run", headline="h2"))
    assert last_result(p, "dedup")["headline"] == "h2" and set(all_last_results(p)) == {"dedup"}
    assert following_stage("recall") is None and following_stage("nope") is None
    txt = format_result(got)
    assert "h" in txt and "a: 1" in txt and "w" in txt


# ----------------------------------------------------------------------------- engines record, API returns them
def test_engines_record_and_api_wraps(tmp_path):
    from wider.construct.artifacts import save_new_version, set_status

    p = _project(tmp_path)
    c = TestClient(create_app(p))
    d = c.get("/api/stages").json()
    assert d["last_results"] == {}
    r = c.post("/api/import/run").json()
    assert r["files"][0]["status"] == "imported" and r["stage_result"]["stage"] == "import"
    assert "12 records" in r["stage_result"]["headline"] and r["stage_result"]["next_stage"] == "dedup"
    r = c.post("/api/dedup/run").json()
    assert r["summary"]["unique_records"] == 12 and r["stage_result"]["stage"] == "dedup" and r["stage_result"]["tone"] in ("success", "warning")
    d = c.get("/api/stages").json()
    assert set(d["last_results"]) == {"import", "dedup"}
    from tests.unit.test_construct import CELLS

    row = save_new_version(p, "cells", CELLS, note="t", origin="manual")
    assert last_result(p, "construct")["action"] == "save:cells"
    set_status(p, "cells", row["version"], "approved")
    assert "approved" in last_result(p, "construct")["headline"]
    r = c.post(f"/api/construct/cells/{row['version']}/status/draft").json()
    assert r["stage_result"]["stage"] == "construct" and r["status"] == "draft"


def test_write_report_sets_last_report_so_recall_is_done(tmp_path):
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import import_all
    from wider.recall.report import write_report
    from wider.sample.labels import import_sample_csv
    from wider.screen.decisions import set_decision

    p = _project(tmp_path)
    import_all(p)
    dedup_run(p)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for i, dec in [(0, "include"), (1, "include"), (2, "exclude"), (3, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S1", 1.0, dec])
    import_sample_csv(p, "validation", f)
    for i, d in [(0, "include"), (1, "maybe"), (2, "exclude"), (3, "exclude")]:
        set_decision(p, "main", recs[i]["record_id"], d)
    assert p.store.get_meta("last_report") is None
    md, js = write_report(p, protocol="main", sample="validation", B=50)
    meta = json.loads(p.store.get_meta("last_report"))
    assert meta["protocol"] == "main" and meta["sample"] == "validation" and meta["path"] == "exports/report.md"
    res = last_result(p, "recall")
    assert res["action"] == "report" and res["outputs"] == ["exports/report.md", "exports/report.json"] and res["next_stage"] is None
    # the card's count is the report's own count, not a dash
    uniq = p.store.scalar("SELECT COUNT(*) FROM records")
    assert res["metrics"]["unique records"] == uniq and f"{uniq} unique records" in md.read_text()
    state = {s["key"]: s["state"] for s in stage_states(p)}
    assert state["recall"] == "done"
    c = TestClient(create_app(p))
    r = c.post("/api/report?protocol=main&sample=validation").json()
    assert r["stage_result"]["stage"] == "recall" and r["stage_result"]["action"] == "report" and "markdown" in r
    assert r["report"]["recall"]["protocol"] == "main"
    g = c.get("/api/report").json()   # reading returns the written report and has no stage result of its own
    assert g["markdown"] == r["markdown"] and "stage_result" not in g


def test_cli_status_shows_last_result(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app

    p = _project(tmp_path)
    p.close()
    runner = CliRunner()
    out = runner.invoke(app, ["import", "--dir", str(tmp_path / "r")])
    assert out.exit_code == 0, out.output
    assert "Imported 1 file with 12 records" in out.output and "next: wider dedup" in out.output
    out = runner.invoke(app, ["status", "--dir", str(tmp_path / "r")])
    assert out.exit_code == 0, out.output
    assert "last result" in out.output and "Imported 1 file" in out.output


def test_a_one_record_protocol_headline_says_record():
    # bad-input tester (21st round): "1 records decided with 2 passes"
    from wider.core.stage_summaries import summarize_screen
    one = summarize_screen({"protocol": "main", "n": 1, "runs": ["a", "b"], "include": 1}, mode="protocol").headline
    two = summarize_screen({"protocol": "main", "n": 2, "runs": ["a"], "include": 2}, mode="protocol").headline
    assert one.startswith("Protocol main: 1 record decided with 2 passes;")
    assert two.startswith("Protocol main: 2 records decided with 1 pass;")
    # the blind third read is not a third pass (spec-checker, 25th round)
    three = summarize_screen({"protocol": "main", "n": 2, "runs": ["a", "b", "c"], "include": 2}, mode="protocol").headline
    assert three.startswith("Protocol main: 2 records decided with 2 passes and a blind third read;")
