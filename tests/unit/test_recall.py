import csv
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core.models import SamplingBand
from wider.core.project import Project
from wider.recall.estimate import estimate
from wider.sample.labels import import_sample_csv
from wider.screen.decisions import import_csv, protocols, set_decision

BASIS = "screened before the ranking existed"   # imported labels are declared independent (label-completion D11)


def _corpus(tmp_path, n=60):
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.bands = [SamplingBand(name="S1", start=1, end=10, n=None), SamplingBand(name="S2", start=11, end=None, n=10)]
    p.save()
    ris = "\n".join(f"TY  - JOUR\nTI  - Record number {i} about things\nPY  - 2020\nDO  - 10.1000/r{i}\nAB  - {'words ' * 25}\nER  -\n" for i in range(n))
    (tmp_path / "a.ris").write_text(ris)
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    return p


def test_recall_estimator_hand_computed(tmp_path):
    p = _corpus(tmp_path)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    # sample: S1 census of 4 (w=1), S2 4 of 40 (w=10); human: includes at r0,r1 (S1) and r10,r11 (S2); maybe r2
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for i, dec in [(0, "include"), (1, "include"), (2, "maybe"), (3, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S1", 1.0, dec])
        for i, dec in [(10, "include"), (11, "include"), (12, "exclude"), (13, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S2", 10.0, dec])
    import_sample_csv(p, "validation", f, independent=BASIS)
    # screening: keeps r0 (include), r1 (maybe), r10 (include), misses r11; r2 maybe->include
    for i, d in [(0, "include"), (1, "maybe"), (2, "include"), (3, "exclude"), (10, "include"), (11, "exclude"), (12, "exclude"), (13, "exclude")]:
        set_decision(p, "test", recs[i]["record_id"], d)
    r = estimate(p, "validation", "test", B=500, seed=1)
    v = r["variants"]
    # strict: kept include among human include = r0 (1) + r10 (10) = 11 of 1+1+10+10 = 22
    assert abs(v["strict"]["recall"] - 11 / 22) < 1e-9
    # lenient: r0, r1 (1+1) + r10 (10) = 12 / 22
    assert abs(v["lenient"]["recall"] - 12 / 22) < 1e-9
    # cautious: relevant include+maybe = r0,r1,r2 (3) + r10,r11 (20) = 23; kept include/maybe: r0,r1,r2,r10 = 13
    assert abs(v["cautious"]["recall"] - 13 / 23) < 1e-9
    assert v["strict"]["ci_bootstrap"][0] <= v["strict"]["recall"] <= v["strict"]["ci_bootstrap"][1]
    assert v["strict"]["ci_linearised"] is not None and v["strict"]["n_missed"] == 2
    assert [b["band"] for b in r["bands"]] == ["S1", "S2"] and r["bands"][1]["weight"] == 10.0
    assert len(r["missed_records"]) == 1 and r["missed_records"][0]["record_id"] == recs[11]["record_id"]


def test_partly_labelled_band_weighted_by_labelled(tmp_path):
    """Design D13 under label-completion-and-sealing D4: a partly labelled sample is refused, but the rows the curve
    and the checksums read (``estimate._rows``) weigh a band N_h over its labelled sampled records, so an unlabelled
    sampled record counts neither as relevant nor as not relevant (S2: 40 records, 10 sampled, 5 labelled -> weight
    8, not 4); once every record is labelled the estimate is the design-weighted one."""
    from wider.recall.estimate import VARIANTS, _ratio, _rows
    from wider.recall.validation import LabelsIncomplete
    from wider.sample.labels import label

    p = _corpus(tmp_path)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for i, dec in [(0, "include"), (1, "include"), (2, "maybe"), (3, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S1", 1.0, dec])
        s2 = [(10, "include"), (11, "include"), (12, "exclude"), (13, "exclude"), (14, "exclude")]
        s2 += [(i, "") for i in range(15, 20)]          # sampled, not labelled yet
        for i, dec in s2:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S2", 4.0, dec])
    import_sample_csv(p, "validation", f, independent=BASIS)
    for i, d in [(0, "include"), (1, "maybe"), (2, "include"), (3, "exclude"), (10, "include"), (11, "exclude")]:
        set_decision(p, "test", recs[i]["record_id"], d)
    for i in range(12, 20):
        set_decision(p, "test", recs[i]["record_id"], "exclude")
    with pytest.raises(LabelsIncomplete) as e:
        estimate(p, "validation", "test", B=200, seed=1)
    assert "5 of 14 records have no label" in e.value.notice
    assert not p.store.scalar("SELECT COUNT(*) FROM validation_looks")   # nothing recorded
    rows = _rows(p, "validation", "test", None)
    weights = {r["band"]: {x["w"] for x in rows if x["band"] == r["band"]} for r in rows}
    assert weights == {"S1": {1.0}, "S2": {8.0}}                       # weight 40/5 = 8; S1 unchanged
    assert sum(r["band"] == "S2" for r in rows) == 5
    rec, _, _ = _ratio(rows, *VARIANTS["strict"])
    assert abs(rec - (1 + 8) / (2 + 16)) < 1e-9
    for i in range(15, 20):   # the five left are labelled (not relevant): the design weight 40/10 again
        label(p, "validation", recs[i]["record_id"], "r2", "exclude")
    r = estimate(p, "validation", "test", B=200, seed=1)
    v = r["variants"]
    assert abs(v["strict"]["recall"] - (1 + 4) / (2 + 8)) < 1e-9
    assert r["bands"][1]["weight"] == 4.0 and r["bands"][1]["n"] == 10
    assert r["bands"][0]["weight"] == 1.0
    assert v["strict"]["ci_linearised"] is not None


def test_import_decisions_csv_with_absent_as_exclude(tmp_path):
    p = _corpus(tmp_path, n=10)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "dec.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "doi", "final_decision", "cell"])
        w.writerow([recs[0]["title"], "", "include", "c1"])
        w.writerow(["", recs[1]["doi"], "maybe", ""])
        w.writerow(["nothing like this", "", "include", ""])
    res = import_csv(p, "orig", f)
    assert res["include"] == 1 and res["maybe"] == 1 and res["unmatched"] == 1 and res["exclude_by_absence"] == 8
    pr = protocols(p)[0]
    assert pr["n"] == 10 and pr["include"] == 1 and pr["exclude"] == 8


def test_import_decisions_duplicate_rows_first_counts(tmp_path):
    """Scenario "Two rows for one record": the first row counts; the later rows are counted as duplicate and stored
    nowhere; a later row with another decision is listed under conflicts with both rows, and the CLI says so."""
    p = _corpus(tmp_path, n=10)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "dup.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "doi", "final_decision"])
        w.writerow([recs[0]["title"], "", "include"])
        w.writerow(["", recs[0]["doi"], "exclude"])
        w.writerow([recs[1]["title"], "", "maybe"])
        w.writerow([recs[1]["title"], "", "maybe"])
    res = import_csv(p, "orig", f)
    assert res["include"] == 1 and res["maybe"] == 1 and res["duplicate"] == 2 and "exclude" not in res
    assert res["exclude_by_absence"] == 8
    assert res["conflicts"] == [{"row": 3, "record_id": recs[0]["record_id"], "decision": "exclude",
                                 "kept_row": 2, "kept": "include"}]
    pr = protocols(p)[0]
    assert pr["n"] == 10 and pr["include"] == 1 and pr["maybe"] == 1 and pr["exclude"] == 8
    d = str(p.root)
    p.close()
    out = CliRunner().invoke(cli_app, ["screen", "import-decisions", "again", str(f), "--dir", d])
    text = " ".join(out.output.split())
    assert out.exit_code == 0, out.output
    assert "2 later row(s) matched a record already listed; the first row of each record counts." in text
    assert (f"Row 3 says exclude for record {recs[0]['record_id']}; row 2 says include and is kept. Check the file."
            in text)


def test_report_and_bundle(tmp_path):
    from wider.recall.report import build_report, share_bundle, to_markdown, write_report

    p = _corpus(tmp_path, n=12)
    rep = build_report(p)
    assert rep["dedup"]["total_records_imported"] == 12 and rep["prisma"][-1][1] == 12
    md, js = write_report(p)
    assert md.exists() and "Identification" in md.read_text() and "Identification" in to_markdown(rep)
    b = share_bundle(p)
    assert (b / "records.csv").exists() and (b / "report.md").exists()
    assert "abstract" not in (b / "records.csv").read_text().splitlines()[0].replace("abstract_words", "")
    assert not list(b.rglob("*.ris")) and not list(b.rglob("responses.jsonl"))



# ----------------------------------------------------------------------------- results-dashboard-and-ris D8
def _sample_of(p, tmp_path, n=6):
    """A probability sample ``validation`` of the first ``n`` records, labelled outside WIDER (no History entry)
    and opened for labelling."""
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id LIMIT ?", (n,))]
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for r in recs:
            w.writerow([r["doi"], r["title"], "S1", 1.0, "exclude"])
    import_sample_csv(p, "validation", f)
    p.store.execute("UPDATE samples SET locked=0 WHERE name='validation'")   # an import is locked; label it here
    return [r["record_id"] for r in recs]


def test_report_agreement_and_interventions(tmp_path, monkeypatch):
    from tests.unit.test_agreement import SYNTHETIC, fill, rules
    from wider.construct.artifacts import record_unlock
    from wider.core import journal
    from wider.core.questions import answer, raise_question
    from wider.recall.report import build_report, share_bundle, write_report
    from wider.sample.labels import adjudicate, label

    monkeypatch.setenv("WIDER_REVIEWER", "rev1")
    monkeypatch.setenv("USER", "login-x")   # this computer's login name never enters the test
    p = _corpus(tmp_path, n=12)
    fill(p, "main", SYNTHETIC, rules())
    ids = _sample_of(p, tmp_path)
    for rid in ids:   # 12 labels; the two reviewers disagree on every record
        label(p, "validation", rid, "rev1", "include")
        label(p, "validation", rid, "rev2", "exclude")
    adjudicate(p, "validation", ids[0], "include", by="rev2")
    adj = journal.last_change_id()
    redo_adj = journal.undo(p, journal.undo(p, adj, author="rev2")["change_id"], author="rev2")["change_id"]
    qid = raise_question(p, "enrich_unresolved", "r1", "a synthetic record without an abstract", "Not found.",
                         record_id=ids[1])
    answer(p, qid, "leave", by="rev1")
    home_value = str(Path.home() / "synthetic_notes")
    p.config.include_notes = home_value       # a setting holding a home path, then undone
    p.save()
    undone_cfg = journal.last_change_id()
    journal.undo(p, undone_cfg, author="rev1")
    p.config.exclude_notes = "synthetic exclusion note"   # undone and redone: in effect again
    p.save()
    redone_cfg = journal.last_change_id()
    journal.undo(p, journal.undo(p, redone_cfg, author="rev1")["change_id"], author="rev1")
    record_unlock(p, "uploads", None, None, by="rev1", detail="a file added")
    p.config.include_notes = "/Volumes/Syn/a/notes.txt"   # a path outside home: cut as portable_path cuts it
    p.save()

    rep = build_report(p)
    ag, iv = rep["agreement"], rep["interventions"]
    assert ag["agreement"] == "6 of 10 (60%)" and ag["kappa_text"] == "0.34" and ag["set_name"] == "main"
    kinds = {k["kind"]: k for k in iv["by_kind"]}
    assert kinds["label"]["count"] == 12 and kinds["label"]["authors"] == ["rev1", "rev2"]
    assert kinds["adjudication"]["count"] == 1 and kinds["adjudication"]["authors"] == ["rev2"]
    listed = {e["change_id"]: e for e in iv["entries"]}
    assert {"label", "adjudication"}.isdisjoint(e["kind"] for e in listed.values())
    assert adj not in listed and redo_adj not in listed
    assert not any("adjudicated" in e["summary"] or "labelled record" in e["summary"] for e in listed.values())
    assert listed[undone_cfg]["undone"] and "'~/synthetic_notes'" in listed[undone_cfg]["summary"]
    assert not listed[redone_cfg]["undone"] and "synthetic exclusion note" in listed[redone_cfg]["summary"]
    unlock = next(e for e in listed.values() if e["kind"] == "artifact_unlock")
    assert unlock["kind_label"] == "uploads reopened" and unlock["author"] == "rev1"
    assert any(e["kind"] == "answer" and e["author"] == "rev1" for e in listed.values())

    md, js = write_report(p)
    text = md.read_text()
    assert "## Screening agreement (main)" in text and "6 of 10 (60%)" in text and "0.34" in text
    assert "settled by the third read: 3" in text
    sec = text[text.index("## Researcher interventions"):]
    assert "- label: 12 entries; Researcher 1, Researcher 2; " in sec and "- adjudication: 1 entry; Researcher 2; " in sec
    cfg_lines = [ln for ln in sec.splitlines() if ln.startswith("| ") and "| settings |" in ln]
    assert any("~/synthetic_notes" in ln and ln.endswith("(undone) |") for ln in cfg_lines)
    assert any("synthetic exclusion note" in ln and "(undone)" not in ln for ln in cfg_lines)
    assert "| uploads reopened |" in sec and "answered 'a synthetic record without an abstract'" in sec
    assert "No researcher intervention recorded." not in sec
    for rid in ids:
        assert f"record {rid} " not in sec
    assert home_value not in text and home_value not in js.read_text()
    data = json.loads(js.read_text())
    assert data["agreement"]["agreement"] == "6 of 10 (60%)" and data["interventions"]["entries"]
    assert "/Volumes" not in text and "<path>/notes.txt" in sec
    b = share_bundle(p)   # the scan finds no home path
    assert "## Researcher interventions" in (b / "report.md").read_text()
    assert "/Volumes" not in (b / "report.md").read_text()


def test_report_no_interventions(tmp_path):
    from wider.core.annotations import add_note
    from wider.recall.report import build_report, to_markdown
    from wider.sample.labels import label

    p = _corpus(tmp_path, n=12)
    p.store.execute("DELETE FROM history")   # the corpus helper's band setting: History holds only a label and a note
    ids = _sample_of(p, tmp_path, n=2)
    label(p, "validation", ids[0], "rev1", "include")
    add_note(p, ids[1], "rev1", "a synthetic note")
    rep = build_report(p)
    assert rep["interventions"]["entries"] == [] and rep["agreement"] is None
    sec = to_markdown(rep).split("## Researcher interventions", 1)[1]
    assert "No researcher intervention recorded." in sec
    assert "- label: 1 entry; rev1; " in sec and "- note: 1 entry; rev1; " in sec
    assert "a synthetic note" not in sec and "## Screening agreement" not in to_markdown(rep)

# ----------------------------------------------------------------------------- fix-defects: errors, POST, variant, level
def _labelled(tmp_path, protocol="main"):
    """The hand-computed design above (strict 11/22, lenient 12/22), screened under ``protocol``."""
    p = _corpus(tmp_path)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for i, dec in [(0, "include"), (1, "include"), (2, "maybe"), (3, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S1", 1.0, dec])
        for i, dec in [(10, "include"), (11, "include"), (12, "exclude"), (13, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S2", 10.0, dec])
    import_sample_csv(p, "validation", f, independent=BASIS)
    for i, d in [(0, "include"), (1, "maybe"), (2, "include"), (3, "exclude"), (10, "include"), (11, "exclude"), (12, "exclude"), (13, "exclude")]:
        set_decision(p, protocol, recs[i]["record_id"], d)
    return p


def _count_results(monkeypatch):
    """Record every stage result written (stage, action) while still storing it."""
    from wider.core import stage_result

    seen = []
    orig = stage_result.record

    def spy(project, result):
        seen.append((result.stage, result.action))
        return orig(project, result)
    monkeypatch.setattr(stage_result, "record", spy)
    return seen


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_unknown_protocol_or_sample_is_refused(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.ui.app import create_app

    p = _labelled(tmp_path)
    d = str(p.root)
    p.close()
    seen = _count_results(monkeypatch)
    runner = CliRunner()
    out = runner.invoke(app, ["recall", "nosuch", "--dir", d])
    assert out.exit_code == 1 and "no screening decisions under protocol 'nosuch'" in _flat(out.output)
    assert "Traceback" not in out.output
    out = runner.invoke(app, ["report", "--protocol", "nosuch", "--dir", d])
    assert out.exit_code == 1 and "no screening decisions under protocol 'nosuch'" in _flat(out.output)
    for args in (["recall", "main", "--sample", "nosuch"], ["report", "--sample", "nosuch"]):
        out = runner.invoke(app, [*args, "--dir", d])
        assert out.exit_code == 1 and "no sample 'nosuch'" in _flat(out.output), out.output
        assert "Traceback" not in out.output
    assert not (p.root / "exports" / "report.md").exists() and seen == []
    c = TestClient(create_app(p))
    r = c.get("/api/recall?protocol=nosuch&sample=validation")
    assert r.status_code == 404 and r.json()["detail"] == "no screening decisions under protocol 'nosuch'"
    r = c.get("/api/recall?protocol=main&sample=nosuch")
    assert r.status_code == 404 and r.json()["detail"] == "no sample 'nosuch'"
    for q in ("protocol=nosuch&sample=validation", "protocol=main&sample=nosuch"):
        assert c.post(f"/api/report?{q}").status_code == 404
        assert c.post(f"/api/report/bundle?{q}").status_code == 404
    assert not any((p.root / "exports" / n).exists() for n in ("report.md", "report.json", "share_bundle")) and seen == []


def test_report_defaults_without_decisions_have_no_recall(tmp_path):
    from wider.recall.report import write_report

    p = _corpus(tmp_path, n=12)
    md, js = write_report(p)
    assert json.loads(js.read_text())["recall"] is None and "## Recall" not in md.read_text()


def test_report_post_writes_once_and_get_only_reads(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.ui.app import create_app

    p = _labelled(tmp_path)
    seen = _count_results(monkeypatch)
    c = TestClient(create_app(p))
    assert c.get("/api/report").status_code == 404 and not (p.root / "exports" / "report.md").exists()
    r = c.post("/api/report?protocol=main&sample=validation")
    assert r.status_code == 200 and seen == [("recall", "report")]   # no extra estimate result
    body = r.json()
    assert body["stage_result"]["action"] == "report" and "## Recall (main vs validation" in body["markdown"]
    md = p.root / "exports" / "report.md"
    mtime, last = md.stat().st_mtime_ns, p.store.get_meta("last_report")
    g = c.get("/api/report")
    assert g.status_code == 200 and g.json()["markdown"] == body["markdown"]
    assert md.stat().st_mtime_ns == mtime and p.store.get_meta("last_report") == last and len(seen) == 1
    seen.clear()
    assert c.post("/api/report/bundle").status_code == 200 and seen == [("recall", "bundle")]
    seen.clear()
    d = str(p.root)
    p.close()
    out = CliRunner().invoke(app, ["report", "--bundle", "--dir", d])
    assert out.exit_code == 0, out.output
    assert seen == [("recall", "report"), ("recall", "bundle")]


def test_default_protocol_is_the_most_recently_screened(tmp_path):
    from wider.recall.report import build_report

    p = _labelled(tmp_path, protocol="zeta")
    p.store.execute("UPDATE screen_decisions SET decided_at='2020-01-01T00:00:00+00:00'")
    for r in p.store.query("SELECT record_id, decision FROM screen_decisions WHERE protocol='zeta'"):
        set_decision(p, "alpha", r["record_id"], r["decision"])   # screened later; sorts first by name
    assert [x["protocol"] for x in protocols(p)] == ["zeta", "alpha"]
    assert build_report(p, B=20)["recall"]["protocol"] == "alpha"


def test_report_help_states_the_real_defaults():
    from typer.testing import CliRunner

    from wider.cli import app

    out = CliRunner().invoke(app, ["report", "--help"], env={"COLUMNS": "200"})
    text = _flat(out.output)
    assert "latest protocol with screening decisions" in text and "last labelled probability sample" in text
    assert "default: main" not in text and "default: validation" not in text


def test_headline_uses_the_primary_variant(tmp_path):
    from wider.core.stage_result import last_result
    from wider.recall.report import write_report

    p = _labelled(tmp_path)
    p.config.protocol = p.config.protocol.set("recall.primary_variant", "strict")
    p.save()
    est = estimate(p, "validation", "main", B=50, seed=1)
    assert "50.0%" in last_result(p, "recall")["headline"] and "strict" in last_result(p, "recall")["headline"]
    assert abs(est["variants"]["lenient"]["recall"] - 12 / 22) < 1e-9
    md, _ = write_report(p, protocol="main", sample="validation", B=50)
    head = last_result(p, "recall")["headline"]
    assert "50.0%" in head and "strict" in head and "54.5%" not in head
    assert "Primary variant: strict." in md.read_text()


def test_ci_level_sets_z_and_labels(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.core.stage_result import last_result
    from wider.recall.report import write_report

    p = _labelled(tmp_path)
    wide = estimate(p, "validation", "main", B=50, seed=1)["variants"]["strict"]["ci_linearised"]
    assert "95% interval" in last_result(p, "recall")["headline"]
    p.config.protocol = p.config.protocol.set("recall.ci_level", 0.9)
    p.save()
    narrow = estimate(p, "validation", "main", B=50, seed=1)["variants"]["strict"]["ci_linearised"]
    assert wide[0] < narrow[0] < narrow[1] < wide[1]
    from math import log

    from scipy.stats import norm

    def logit_width(ci):
        return log(ci[1] / (1 - ci[1])) - log(ci[0] / (1 - ci[0]))
    assert logit_width(narrow) / logit_width(wide) == pytest.approx(norm.ppf(0.95) / norm.ppf(0.975), rel=1e-9)  # z 1.645
    res = last_result(p, "recall")
    assert "90% interval" in res["headline"] and "90% interval" in res["rows"][0] and "95%" not in res["headline"]
    md, _ = write_report(p, protocol="main", sample="validation", B=50)
    recall_md = md.read_text().split("## Recall", 1)[1]
    assert "| 90% interval |" in recall_md and "bootstrap 90% interval (cross-check" in recall_md
    assert "90% interval: " in recall_md and "95% interval" not in recall_md
    d = str(p.root)
    p.close()
    out = CliRunner().invoke(app, ["recall", "main", "-B", "50", "--dir", d], env={"COLUMNS": "200"})
    assert out.exit_code == 0, out.output
    assert "90% interval" in out.output and "95%" not in out.output
    for bad in ("0", "-3"):   # no silent default for 0, no negative count of replicates
        out = CliRunner().invoke(app, ["recall", "main", "-B", bad, "--dir", d])
        assert out.exit_code == 2 and "Traceback" not in out.output, out.output


def test_bundle_carries_no_error_text(tmp_path):
    from wider.recall.report import share_bundle

    p = _labelled(tmp_path)
    run = p.root / "runs" / "screen_x"
    run.mkdir(parents=True)
    (run / "run_spec.json").write_text("{}")
    (run / "eval_report.md").write_text("the model said: SECRET PHRASE")
    phrase = "model echoed SECRET PHRASE"
    with open(run / "decisions.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "pass", "ok", "raw_decision", "rule_decision", "input_sha256", "attempts", "error", "title", "parsed"])
        w.writerow([1, "A", 1, "include", "include", "abc", 1, "", "Record one", '{"quote": "x"}'])
        w.writerow([2, "A", 0, "", "", "def", 3, phrase, "Record two", ""])
    with open(run / "errors.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record_id", "pass", "error"])
        w.writerow([2, "A", phrase])
    b = share_bundle(p)
    out = b / "runs" / "screen_x"
    assert (out / "errors.csv").read_text().splitlines() == ["record_id,pass", "2,A"]
    with open(out / "decisions.csv", newline="") as f:
        dec = list(csv.reader(f))
    assert dec[0] == ["record_id", "pass", "ok", "raw_decision", "rule_decision", "input_sha256", "attempts", "title"]
    assert len(dec) == 3 and dec[2][-1] == "Record two"
    assert "SECRET PHRASE" not in (out / "decisions.csv").read_text() and not list(b.rglob("eval_report.md"))


def test_bundle_content_from_a_screened_project(screen_project, monkeypatch):  # noqa: F811
    """Scenario: Bundle content, from a real (fake-provider) screening run with one failed call."""
    from tests.unit.test_questions import _fake
    from tests.unit.test_screen import _responder
    from wider.construct import artifacts
    from wider.core.models import BenchmarkPaper
    from wider.recall.report import share_bundle, write_report
    from wider.sample.labels import label
    from wider.screen.engine import screen

    p = screen_project
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Paper 0", doi="10.1000/p0", set="Development")])
    good = _responder({"calls": 0})
    _fake(monkeypatch, lambda req: "not json" if "Paper 3 " in req.user else good(req))
    res = screen(p, "corpus", pass_name="A")
    assert res["n_err"] == 1
    run_dir = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    assert (run_dir / "responses.jsonl").exists()
    recs = [r["record_id"] for r in p.store.query("SELECT record_id FROM records ORDER BY record_id")]
    set_decision(p, "main", recs[0], "include")
    set_decision(p, "main", recs[1], "exclude")
    dev = p.store.query("SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='development'")
    # ana agrees with alice: a disagreement would leave the development sample open, and the bundle waits (D4)
    voice = "voice" in p.store.scalar("SELECT title FROM records WHERE record_id=?", (dev[0]["record_id"],))
    agreed = "include" if voice else "exclude"   # alice's label (label_development)
    label(p, "development", dev[0]["record_id"], "ana", agreed, reason="" if agreed == "include" else "a")
    md, js = write_report(p)
    b = share_bundle(p, report=(md, js))

    def rows(f):
        with open(f, newline="", encoding="utf-8") as fh:
            return list(csv.reader(fh))

    # THEN it holds project.yaml, benchmark.csv, the artifact folders, records.csv, screen_decisions.csv, labels.csv,
    # report.md and report.json (besides the run folders and the plain-text exports)
    top = {x.name for x in b.iterdir()}
    want = {"project.yaml", "benchmark.csv", "artifacts", "records.csv", "screen_decisions.csv", "labels.csv", "report.md",
            "report.json", "result_sets.csv", "REPRODUCE.md"}
    assert want <= top and top - want <= {"runs", "dedup", "queries", "search_strings", "prisma_reporting", "prisma_s.md", "prisma_s.json"} and "runs" in top
    assert (b / "benchmark.csv").read_bytes() == (p.root / "benchmark.csv").read_bytes()
    # project.yaml is the sanitized copy: every setting kept, no home-folder path
    import yaml
    shared = yaml.safe_load((b / "project.yaml").read_text())
    assert shared["name"] == p.config.name and [x["name"] for x in shared["providers"]] == [x.name for x in p.config.providers]
    assert str(Path.home()) not in (b / "project.yaml").read_text()
    assert (b / "report.md").read_bytes() == md.read_bytes() and (b / "report.json").read_bytes() == js.read_bytes()
    kinds = {k for k in artifacts.KINDS if p.artifact_dir(k).exists()}
    assert {"anchors", "codebook", "prompts"} <= kinds and {x.name for x in (b / "artifacts").iterdir()} == kinds
    for k in kinds:
        src = sorted(f.relative_to(p.artifact_dir(k)) for f in p.artifact_dir(k).rglob("*") if f.is_file())
        assert bool(src) or k not in {"anchors", "codebook", "prompts"}
        assert sorted(f.relative_to(b / "artifacts" / k) for f in (b / "artifacts" / k).rglob("*") if f.is_file()) == src
    rc = rows(b / "records.csv")
    assert rc[0] == ["record_id", "title", "year", "journal", "doi", "platforms", "abstract_words"]
    assert [int(r[0]) for r in rc[1:]] == recs and len(recs) == 40
    sd = rows(b / "screen_decisions.csv")
    assert sd[0] == ["protocol", "result_set", "active", "record_id", "decision", "votes", "decided_by", "models",
                     "mixed_models", "cell", "labels", "reason", "origin", "origin_set", "experimental"]
    assert sorted((r[0], int(r[3]), r[4]) for r in sd[1:]) == sorted([("main", recs[0], "include"), ("main", recs[1], "exclude")])
    assert {(r[1], r[2]) for r in sd[1:]} == {("1", "yes")}
    assert rows(b / "result_sets.csv")[1][:3] == ["main", "1", "main"]
    lb = rows(b / "labels.csv")
    assert lb[0] == ["sample", "record_id", "band", "weight", "pool", "reviewer", "decision", "reason", "labels",
                     "made_in", "blinded", "left_out", "abstract_version"]   # how each label was made (blinding D11)
    # one row per label, and one per unlabelled record: dev[0] carries alice's label (the fixture) and ana's
    assert len(lb) - 1 == p.store.scalar("SELECT COUNT(*) FROM sample_rows") + 1
    from wider.core.researchers import Attributor
    ana = Attributor(p).who("ana")   # labels.csv writes the reviewer as exports.attribution says (D5)
    assert ana != "ana" and [(r[0], int(r[1]), r[6]) for r in lb[1:] if r[5] == ana] == [("development", dev[0]["record_id"], agreed)]
    # AND from each run folder: run_spec.json, prompt.md, schema.json, errors.csv and decisions.csv, and nothing else
    out = b / run_dir.relative_to(p.root)
    snaps = {x.name for x in run_dir.glob("backend_*.json")}
    assert snaps and {x.name for x in out.iterdir()} == {"run_spec.json", "prompt.md", "schema.json", "errors.csv",
                                                         "decisions.csv"} | snaps
    for name in ("prompt.md", "schema.json"):
        assert (out / name).read_bytes() == (run_dir / name).read_bytes()
    assert json.loads((out / "run_spec.json").read_text()) == json.loads((run_dir / "run_spec.json").read_text())
    err = rows(out / "errors.csv")
    assert err[0] == ["record_id", "pass"] and err[1:] == [r[:2] for r in rows(run_dir / "errors.csv")[1:]] and len(err) == 2
    dec, src = rows(out / "decisions.csv"), rows(run_dir / "decisions.csv")
    assert dec[0] == ["record_id", "pass", "ok", "raw_decision", "rule_decision", "input_sha256", "attempts", "title", "provider",
                      "model", "model_reported", "backend_hash", "evaluation", "source_run_id", "experimental"]
    keep = [src[0].index(c) for c in dec[0]]
    assert dec[1:] == [[r[i] for i in keep] for r in src[1:]] and len(dec) - 1 == res["n_records"]


def _screened_bundle_project(proj, monkeypatch, limit=None):
    from tests.unit.test_screen import _responder
    from wider.providers import fake as fakemod
    from wider.screen.engine import screen

    orig = fakemod.FakeProvider.__init__
    counter = {"calls": 0}
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    res = screen(proj, "corpus", pass_name="A", limit=limit)
    run_dir = proj.root / proj.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    return res, run_dir


def test_bundle_sanitizes_legacy_run_spec(screen_project, monkeypatch):  # noqa: F811
    """A v3-era run_spec.json holding the bundled CLI's path under the home folder still gives a bundle that passes
    the scan; the original file is never changed."""
    from wider.recall.report import share_bundle

    p = screen_project
    _, run_dir = _screened_bundle_project(p, monkeypatch)
    spec = json.loads((run_dir / "run_spec.json").read_text())
    spec["provider"] = {"name": "claude-code", "kind": "cli_agent",
                        "executable": f"{Path.home()}/Library/Application Support/Claude/claude-code/9.9.9/claude",
                        "command": [f"{Path.home()}/bin/claude", "-p"]}
    (run_dir / "run_spec.json").write_text(json.dumps(spec))
    original = (run_dir / "run_spec.json").read_bytes()
    b = share_bundle(p)
    copy = (b / run_dir.relative_to(p.root) / "run_spec.json").read_text()
    assert str(Path.home()) not in copy and "~/Library/Application Support/Claude" in copy and "~/bin/claude" in copy
    assert (run_dir / "run_spec.json").read_bytes() == original
    assert "paths made portable" in (b / "REPRODUCE.md").read_text()


def test_bundle_reproduce_md(screen_project, monkeypatch):  # noqa: F811
    from wider.core.models import ProviderConfig
    from wider.recall.report import share_bundle

    p = screen_project
    p.config.providers.append(ProviderConfig(name="cloudy", kind="openai_compat", model="m", base_url="https://api.example.org/v1",
                                             api_key_env="WIDER_SYNTH_KEY"))
    p.save()
    _screened_bundle_project(p, monkeypatch)
    md = (share_bundle(p) / "REPRODUCE.md").read_text()
    assert "Running these runs again needs the listed backends; this bundle is not executable on its own." in md
    assert "`WIDER_SYNTH_KEY`" in md and "## Command-line tools" in md and "## Local servers and models" in md
    assert "## Presets" in md and "## Consent needed before a run" in md


def test_bundle_refused_on_secret(screen_project, monkeypatch):  # noqa: F811
    from wider.core.sanitize import SecretFound
    from wider.recall.report import share_bundle

    p = screen_project
    p.config.providers[-1].api_key_env = "WIDER_SYNTH_KEY"
    p.save()
    monkeypatch.setenv("WIDER_SYNTH_KEY", "synthetic-value-abcdef-123")
    _, run_dir = _screened_bundle_project(p, monkeypatch)
    (run_dir / "prompt.md").write_text("A prompt that leaked synthetic-value-abcdef-123 by mistake\n")
    with pytest.raises(SecretFound) as e:
        share_bundle(p)
    msg = str(e.value)
    assert f"{run_dir.relative_to(p.root)}/prompt.md:1: value of environment variable WIDER_SYNTH_KEY" in msg
    assert "synthetic-value" not in msg and not (p.root / "exports" / "share_bundle").exists()
    r = CliRunner().invoke(cli_app, ["report", "--bundle", "--dir", str(p.root)])
    assert r.exit_code == 1 and "WIDER_SYNTH_KEY" in r.output and "synthetic-value" not in r.output
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    resp = TestClient(create_app(p)).post("/api/report/bundle")
    assert resp.status_code == 400 and "WIDER_SYNTH_KEY" in resp.json()["detail"] and "synthetic-value" not in resp.text
    assert not (p.root / "exports" / "share_bundle").exists()


def test_bundle_marks_experimental(screen_project, monkeypatch):  # noqa: F811
    from wider.recall.report import share_bundle
    from wider.screen.engine import EXPERIMENTAL_NOTE
    from wider.screen.protocol import run_protocol

    p = screen_project
    _screened_bundle_project(p, monkeypatch)
    run_protocol(p, "corpus", "main")
    for r in p.store.query("SELECT backend_hash, snapshot FROM backend_snapshots"):
        snap = json.loads(r["snapshot"])
        snap["experimental"] = True
        p.store.execute("UPDATE backend_snapshots SET snapshot=? WHERE backend_hash=?", (json.dumps(snap), r["backend_hash"]))
    b = share_bundle(p)
    with open(b / "screen_decisions.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and {r["experimental"] for r in rows} == {EXPERIMENTAL_NOTE}
    assert EXPERIMENTAL_NOTE in (b / "REPRODUCE.md").read_text()


def test_bundle_backend_changes(screen_project, monkeypatch):  # noqa: F811
    """A run continued on a changed backend keeps backend_changes.jsonl and both snapshots in the bundle."""
    from wider.recall.report import share_bundle
    from wider.screen.engine import screen

    p = screen_project
    first, _ = _screened_bundle_project(p, monkeypatch, limit=3)
    p.config.providers[-1].model = "fake-2"
    p.save()
    screen(p, "corpus", pass_name="A", backend_change="continue")
    run_dir = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (first["run_id"],))["dir"]
    assert (run_dir / "backend_changes.jsonl").exists() and len(list(run_dir.glob("backend_*.json"))) == 2
    out = share_bundle(p) / run_dir.relative_to(p.root)
    assert (out / "backend_changes.jsonl").read_bytes() == (run_dir / "backend_changes.jsonl").read_bytes()
    assert {x.name for x in out.glob("backend_*.json")} == {x.name for x in run_dir.glob("backend_*.json")}


# ----------------------------------------------------------------------------- recall-interval-honest: one wording
SEVEN_LINES = [
    "Based on 7 relevant validation papers.",
    "95% interval: 0.16 to 1.00.",
    "That is few: one paper more or fewer missed would change the estimate a lot. Labelling more of the sample gives "
    "a firmer estimate.",
    "No relevant validation paper was missed. This does not mean the screening keeps every relevant paper: a recall "
    "as low as 0.16 fits what was seen."]


def _seven_of_seven(tmp_path):
    """Seven relevant papers (weights 4 x 1, 2 x 10, 1 x 50), all kept by protocol ``main``; two to four irrelevant
    records per band so no sampled band has a single labelled record."""
    p = _corpus(tmp_path)
    recs = [dict(r) for r in p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    plan = [("S1", 1.0, range(0, 4), range(4, 6)), ("S2", 10.0, range(10, 12), range(12, 14)),
            ("S3", 50.0, range(20, 21), range(21, 23))]
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for band, wt, inc, exc in plan:
            for i in inc:
                w.writerow([recs[i]["doi"], recs[i]["title"], band, wt, "include"])
            for i in exc:
                w.writerow([recs[i]["doi"], recs[i]["title"], band, wt, "exclude"])
    import_sample_csv(p, "validation", f, independent=BASIS)
    for band, wt, inc, exc in plan:
        for i in inc:
            set_decision(p, "main", recs[i]["record_id"], "include")
        for i in exc:
            set_decision(p, "main", recs[i]["record_id"], "exclude")
    return p


def test_estimate_carries_interval_and_lines(tmp_path):
    p = _seven_of_seven(tmp_path)
    r = estimate(p, "validation", "main", B=200, seed=1)
    v = r["variants"]["lenient"]
    assert r["lines"] == SEVEN_LINES == v["lines"]
    assert v["interval"][0] == pytest.approx(0.16178, abs=1e-5) and v["interval"][1] == 1.0
    assert v["interval_method"] == "kish" and v["n_effective"] == pytest.approx(2.025, abs=1e-3)
    assert v["interval_reason"] is None and tuple(v["ci_bootstrap"]) == (1.0, 1.0)   # the cross-check stays, collapsed
    # no relevant record for a variant: no interval of any kind, and the text says so
    q = _corpus(tmp_path / "none", n=12)
    recs = [dict(x) for x in q.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")]
    f = tmp_path / "none.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "band", "weight", "decision"])
        for i, dec in [(0, "maybe"), (1, "exclude"), (2, "exclude")]:
            w.writerow([recs[i]["doi"], recs[i]["title"], "S1", 1.0, dec])
    import_sample_csv(q, "validation", f, independent=BASIS)
    for i in range(3):
        set_decision(q, "main", recs[i]["record_id"], "include")
    s = estimate(q, "validation", "main", B=100, seed=1)["variants"]["strict"]
    assert s["recall"] is None and s["interval"] is None and s["ci_bootstrap"] is None and s["ci_linearised"] is None
    assert s["lines"] == ["Recall could not be estimated: the labelled sample holds no relevant validation papers."]


def test_same_words_on_every_surface(tmp_path):
    from fastapi.testclient import TestClient

    from wider.core.stage_result import last_result
    from wider.recall.outputs import methods_draft
    from wider.ui.app import create_app

    p = _seven_of_seven(tmp_path)
    api = TestClient(create_app(p)).get("/api/recall?protocol=main&sample=validation&B=200").json()
    assert api["lines"] == SEVEN_LINES
    res = last_result(p, "recall")
    assert "7 relevant validation papers" in res["headline"] and "95% interval 0.16 to 1.00" in res["headline"]
    assert not any(w in SEVEN_LINES for w in res["warnings"])   # the notes are said once, in the lines
    from wider.recall.report import write_report
    md, js = write_report(p, protocol="main", sample="validation", B=200)
    # the report's headline (summarize_report) gives the same recall, interval and count
    assert ("recall 100.0% (lenient; 95% interval 0.16 to 1.00; 7 relevant validation papers)"
            in last_result(p, "recall")["headline"])
    assert all(x in md.read_text() for x in SEVEN_LINES)
    assert json.loads(js.read_text())["recall"]["variants"]["lenient"]["lines"] == SEVEN_LINES
    draft = _flat(methods_draft(p, protocol="main"))
    assert "95% interval 0.16 to 1.00" in draft and "7 relevant validation papers" in draft
    d = str(p.root)
    p.close()
    runner = CliRunner()
    out = runner.invoke(cli_app, ["recall", "main", "-B", "200", "--dir", d], env={"COLUMNS": "250"})
    assert out.exit_code == 0, out.output
    assert all(x in _flat(out.output) for x in SEVEN_LINES)
    assert "bootstrap" not in out.output.lower() and "linearised" not in out.output.lower()
    js_out = runner.invoke(cli_app, ["recall", "main", "-B", "200", "--json", "--dir", d])
    assert json.loads(js_out.output)["lines"] == SEVEN_LINES


def test_report_method_section(tmp_path):
    from wider.recall.report import write_report

    p = _seven_of_seven(tmp_path)
    md, _ = write_report(p, protocol="main", sample="validation", B=200)
    text = md.read_text()
    head, method = text.split("### Recall: method", 1)
    table = head.split("## Recall", 1)[1]
    assert "| variant | recall | 95% interval | relevant papers | missed |" in table
    assert "bootstrap" not in table and "linearised" not in table
    assert "| lenient | Kish effective size, effective n 2.03 | 1.00–1.00 | 1.00–1.00 |" in method
    assert "bootstrap 95% interval (cross-check, B 200, seed" in method and "linearised 95% interval (cross-check)" in method
    assert "Korn and Graubard (1998)" in method


def test_method_sentence_on_every_surface(tmp_path):
    """Owner answer a-20261024-094201-8c08 point 7: the Recall page (API), ``wider recall`` and the report say the one
    method sentence in the same words, and none calls the interval exact or validated."""
    from fastapi.testclient import TestClient

    from wider.recall.report import write_report
    from wider.recall.uncertainty import METHOD_SENTENCE
    from wider.ui.app import create_app

    p = _seven_of_seven(tmp_path)
    api = TestClient(create_app(p)).get("/api/recall?protocol=main&sample=validation&B=200").json()
    assert api["method_note"] == METHOD_SENTENCE and METHOD_SENTENCE not in api["lines"]
    md, js = write_report(p, protocol="main", sample="validation", B=200)
    method = md.read_text().split("### Recall: method", 1)[1]
    assert METHOD_SENTENCE in method
    assert "not an exact or validated confidence interval" in method and "scripts/recall_interval_sim.py" in method
    assert "exact" not in method.replace("not an exact or validated", "")
    assert json.loads(js.read_text())["recall"]["method_note"] == METHOD_SENTENCE
    d = str(p.root)
    p.close()
    out = CliRunner().invoke(cli_app, ["recall", "main", "-B", "200", "--dir", d], env={"COLUMNS": "250"})
    assert out.exit_code == 0, out.output
    assert METHOD_SENTENCE in _flat(out.output) and _flat(out.output).count("The interval is approximate") == 1


def test_first_stored_label_wins_without_primary_or_adjudication(tmp_path):
    """Risk-checker R1 (24 Oct 2026): a second reviewer whose name sorts before the first's must not replace the first
    stored label in the estimate. Spec: adjudication, else the primary reviewer's label, else the first stored label."""
    from wider.sample.labels import final_labels, label

    p = _seven_of_seven(tmp_path)
    rows = final_labels(p, "validation")
    rid = rows[0]["record_id"]
    assert rows[0]["source"] == "original" and rows[0]["decision"] == "include"
    label(p, "validation", rid, "aaa-second", "exclude", reason="disagrees")   # sorts before "original"
    row = next(r for r in final_labels(p, "validation") if r["record_id"] == rid)
    assert row["source"] == "original" and row["decision"] == "include" and row["open_disagreement"]
    # an open disagreement keeps the sample incomplete (label-completion-and-sealing D4): no estimate, no look
    from wider.recall.validation import LabelsIncomplete

    looks = p.store.scalar("SELECT COUNT(*) FROM validation_looks")
    with pytest.raises(LabelsIncomplete) as e:
        estimate(p, "validation", "main", B=50, seed=1)
    assert "1 record has labels that disagree and is not adjudicated" in e.value.notice
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == looks


def test_open_disagreements_named_once(tmp_path):
    """Later item (ui-tester, blind second reviewer), under label-completion-and-sealing D4: records where reviewers
    disagree and nobody adjudicated keep the sample incomplete, so recall is refused with their count said once and
    no look recorded; after adjudication the estimate is given and no line mentions a disagreement."""
    from wider.core.stage_result import last_result
    from wider.recall.estimate import disagreement_line
    from wider.recall.validation import LabelsIncomplete
    from wider.sample.labels import adjudicate, final_labels, label

    p = _seven_of_seven(tmp_path)
    recs = [dict(r) for r in p.store.query("SELECT record_id FROM records ORDER BY record_id")]
    rid = recs[0]["record_id"]
    # a second reviewer labels one record of the imported (locked) sample: a new label, not a correction
    label(p, "validation", rid, "bob", "exclude", reason="not a trial")
    fl = {r["record_id"]: r for r in final_labels(p, "validation")}
    assert fl[rid]["open_disagreement"] is True and fl[recs[1]["record_id"]]["open_disagreement"] is False
    with pytest.raises(LabelsIncomplete) as e:
        estimate(p, "validation", "main", B=50, seed=1)
    assert e.value.notice.count("1 record has labels that disagree and is not adjudicated") == 1
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == 0
    d = p.root
    p.close()
    out = CliRunner().invoke(cli_app, ["recall", "main", "-B", "50", "--dir", str(d)], env={"COLUMNS": "250"})
    assert out.exit_code == 1, out.output
    assert _flat(out.output).count("1 record has labels that disagree and is not adjudicated") == 1
    p = Project.load(d)
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == 0
    # the estimate's own sentence, kept for the lines of evaluation and comparison
    assert disagreement_line(3, ["alice", "bob"]).startswith("3 labelled records where the reviewers disagree are not "
                                                             "adjudicated; the estimate uses alice's and bob's labels there.")
    adjudicate(p, "validation", rid, "include", by="chair")
    r2 = estimate(p, "validation", "main", B=50, seed=1)
    assert r2["open_disagreements"] == 0 and not any("disagree" in ln for ln in r2["lines"])
    assert not any("disagree" in w for w in last_result(p, "recall")["warnings"])


def test_huge_bootstrap_b_is_capped(tmp_path, monkeypatch):
    """Later item: a huge ``recall.bootstrap_b`` (or ``-B``) must not make Estimate recall run for ever: replicates
    are capped, the asked number is kept, and the stage result says so."""
    import wider.recall.estimate as est
    from wider.core.stage_result import last_result

    monkeypatch.setattr(est, "MAX_BOOTSTRAP_B", 300)
    p = _seven_of_seven(tmp_path)
    r = est.estimate(p, "validation", "main", B=1000, seed=1)
    assert r["B"] == 300 and r["B_requested"] == 1000
    assert ("Bootstrap replicates capped at 300 (1,000 were asked for); the cross-check interval in the report uses "
            "the capped number.") in last_result(p, "recall")["warnings"]
    p.config.protocol = p.config.protocol.set("recall.bootstrap_b", 10**9)
    p.save()
    r = est.estimate(p, "validation", "main", seed=1)
    assert r["B"] == 300 and r["B_requested"] == 10**9
    r = est.estimate(p, "validation", "main", B=200, seed=1)
    assert r["B"] == 200 and r["B_requested"] == 200 and not any("capped" in w for w in last_result(p, "recall")["warnings"])


def test_bootstrap_crosscheck_unchanged(tmp_path):
    """The cross-checks keep their values at 0.95 (same seed, same interval); at recall 1 both are the point 1 while
    the primary interval is not."""
    p = _labelled(tmp_path)
    a = estimate(p, "validation", "main", B=200, seed=1)
    b = estimate(p, "validation", "main", B=200, seed=1)
    assert a["B"] == 200 and a["seed"] == 1
    for k in a["variants"]:
        assert a["variants"][k]["ci_bootstrap"] == b["variants"][k]["ci_bootstrap"]
    # the values before this change (estimate() at e81d13a gives the same numbers)
    before = {"strict": [[0.03125, 0.9545454545454546], [0.059505887304269915, 0.9404941126957301]],
              "lenient": [[0.0625, 1.0], [0.06870487365054498, 0.9512651846129819]],
              "cautious": [[0.09090909090909091, 1.0], [0.08053874447546863, 0.9507235978871048]]}
    for k, (boot, lin) in before.items():
        assert list(a["variants"][k]["ci_bootstrap"]) == pytest.approx(boot, abs=1e-12)
        assert list(a["variants"][k]["ci_linearised"]) == pytest.approx(lin, abs=1e-12)
    q = _seven_of_seven(tmp_path / "seven")
    v = estimate(q, "validation", "main", B=200, seed=1)["variants"]["lenient"]
    assert v["recall"] == 1 and tuple(v["ci_bootstrap"]) == tuple(v["ci_linearised"]) == (1.0, 1.0)
    assert v["interval"][0] < 1.0


def test_band_note_census_band_hides_nothing():
    """A band with no relevant paper: the rule-of-three bound only for a sampled band; a census band (weight 1)
    had every record labelled."""
    from wider.recall.estimate import _band_note

    sampled = [{"w": 4.0, "human": "exclude"}] * 10
    census = [{"w": 1.0, "human": "exclude"}] * 7
    assert _band_note(sampled, []) == "no positives in sample; rule of three: missed rate ≤ 0.30"
    assert _band_note(census, []) == "no positives; every record in the band was labelled"
    assert _band_note(sampled, sampled[:1]) == "" and _band_note([], []) == ""
