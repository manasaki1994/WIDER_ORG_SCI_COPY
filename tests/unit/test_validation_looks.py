"""Validation looks (validation-sample-planner D7, D15). Synthetic projects only."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_recall import _labelled as _imported
from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.core.store import SCHEMA_VERSION, V7_COLUMNS, V7_TABLES, V9_COLUMNS
from wider.recall import validation as va
from wider.recall.estimate import estimate
from wider.screen.decisions import set_decision
from wider.screen.result_sets import new_set
from wider.ui.app import create_app

V7_NAMES = ("validation_looks", "validation_look_states", "validation_stops", "queue_items", "criteria_assessments",
            "label_revisions")


def _labelled(tmp_path):
    """The hand-computed sample of test_recall, its imported labels declared independent so the sample is complete
    (label-completion-and-sealing D4: recall is estimated only once every sampled record has a final label)."""
    from wider.sample import labels as lb

    p = _imported(tmp_path)
    lb.declare_independent(p, "validation", "original", "labelled from abstracts before any model output", by="Owner")
    return p


def _looks(p):
    return [dict(r) for r in p.store.query("SELECT * FROM validation_looks ORDER BY look_id")]


def _second_set(p, settings=None):
    row = new_set(p, "main", "screening", settings=settings if settings is not None else {"model": "m2"})
    for r in p.store.query("SELECT record_id, decision FROM screen_decisions WHERE protocol='main'"):
        set_decision(p, row["key"], r["record_id"], "include")


def test_looks_recorded_once_per_set(tmp_path):
    p = _labelled(tmp_path)
    e = estimate(p, "validation", "main", B=50)
    assert e["look"] == {"index": 1, "first": True, "checksum": e["look"]["checksum"]}
    assert CliRunner().invoke(cli_app, ["recall", "main", "-B", "50", "--dir", str(p.root)]).exit_code == 0
    TestClient(create_app(p)).get("/api/recall", params={"protocol": "main", "B": 50})
    looks = _looks(p)
    assert len(looks) == 1 and looks[0]["n"] == 3 and looks[0]["result_set_key"] == "main"
    h = journal.changes(p, kind="sample_look")
    assert len(h) == 1 and h[0]["kind_label"] == "recall seen on a validation sample" and not h[0]["undoable"]
    assert h[0]["why_not"] == "a recall result seen on a validation sample stays on record"
    # the same labels are the same look; a changed label is the next
    assert estimate(p, "validation", "main", B=50)["look"]["index"] == 1
    from wider.sample import labels as lb
    rid = p.store.scalar("SELECT record_id FROM labels WHERE decision='include' ORDER BY record_id LIMIT 1")
    # the look sealed the record, so a changed label is a correction with a reason (label-completion-and-sealing D2)
    lb.label(p, "validation", rid, "original", "exclude", correction_reason="misread the abstract")
    assert estimate(p, "validation", "main", B=50)["look"]["index"] == 2
    # the report's internal re-reads record nothing
    n = _looks(p)[0]["n"]
    estimate(p, "validation", "main", B=50, record_look=False)
    assert _looks(p)[0]["n"] == n


def test_purposive_sample_records_no_look(tmp_path):
    p = _labelled(tmp_path)
    p.store.execute("UPDATE samples SET kind='purposive'")
    assert estimate(p, "validation", "main", B=50)["look"] is None and not _looks(p)


def test_status_records_no_look(tmp_path):
    p = _labelled(tmp_path)
    assert CliRunner().invoke(cli_app, ["validation", "status", "--dir", str(p.root)]).exit_code == 0
    TestClient(create_app(p)).get("/api/validation/status")
    assert not _looks(p) and not journal.changes(p, kind="sample_look")


def test_compare_records_look(tmp_path):
    p = _labelled(tmp_path)
    _second_set(p)
    estimate(p, "validation", "main", B=50, result_set=2, compare=1)
    assert sorted(x["result_set_key"] for x in _looks(p)) == ["main", "main#2"]
    assert len(journal.changes(p, kind="sample_look")) == 2


def test_model_selection_line(tmp_path):
    from wider.recall.report import build_report

    p = _labelled(tmp_path)
    p.store.execute("UPDATE screen_result_sets SET settings_hash='h1' WHERE key='main'")   # set 1 has settings too
    estimate(p, "validation", "main", B=50)
    assert va.model_selection_line(p, "validation") is None
    _second_set(p)
    e = estimate(p, "validation", "main", B=50, result_set=2, choice="not_informed")
    line = ("Recall on validation was seen for 2 result sets with different settings (sets 1, 2); choosing between them "
            "on this sample uses it for model selection.")
    assert va.model_selection_line(p, "validation") == line
    assert line in e["lines"] and line in estimate(p, "validation", "main", B=50)["lines"]
    assert line in build_report(p, protocol="main", sample="validation")["recall"]["lines"]


def test_model_selection_counts_imported_sets(tmp_path, monkeypatch):
    """import-history-and-model-selection D3: imported sets have no settings hash; each counts as its own setting,
    as in needs_choice, so looks at two of them give the line."""
    import csv

    from wider.recall.outputs import methods_draft
    from wider.recall.report import build_report
    from wider.screen.decisions import import_csv

    monkeypatch.setenv("WIDER_REVIEWER", "R1")
    p = _labelled(tmp_path)
    f = tmp_path / "revised.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["doi", "title", "final_decision"])
        for r in p.store.query("SELECT r.doi, r.title, d.decision FROM screen_decisions d JOIN records r "
                               "ON r.record_id=d.record_id WHERE d.protocol='main'"):
            w.writerow([r["doi"], r["title"], r["decision"]])
    assert import_csv(p, "main", f)["key"] == "main#2"
    estimate(p, "validation", "main", B=50, result_set=2)
    p.store.execute("UPDATE validation_looks SET first_at='2026-10-21T09:00:00+00:00'")
    assert import_csv(p, "main", f)["key"] == "main#3"   # the same file again: no settings hash either
    assert p.store.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE key IN ('main#2','main#3') "
                          "AND settings_hash IS NULL") == 2
    assert "its settings are unknown (imported)" in va.needs_choice(p, "validation", "main#3")
    assert va.model_selection_line(p, "validation") is None   # one set seen so far
    assert va.record_choice(p, "validation", "main#3", "not_informed")
    assert va.model_selection_line(p, "validation") is None   # a choice without a look is not a look (n=0)
    e = estimate(p, "validation", "main", B=50, result_set=3)
    line = ("Recall on validation was seen for 2 result sets with different or unknown settings (sets 2, 3); "
            "choosing between them on this sample uses it for model selection.")
    assert va.model_selection_line(p, "validation") == line
    assert line in e["lines"]
    assert line in build_report(p, protocol="main", sample="validation", result_set=3)["recall"]["lines"]
    p.store.execute("UPDATE screen_result_sets SET active=(key='main#3') WHERE protocol='main'")
    assert line in " ".join(methods_draft(p, "main", 3).split())


def _as_v6(path, with_result=True):
    """Turn a fresh store into one written at version 6: no v7 tables or columns, no key."""
    c = sqlite3.connect(path)
    for t, cols in (*V9_COLUMNS.items(), *V7_COLUMNS.items()):   # a v6 store had neither (v9: review packages)
        for col in cols:
            c.execute(f"ALTER TABLE {t} DROP COLUMN {col.split()[0]}")
    for t in V7_NAMES:
        c.execute(f"DROP TABLE IF EXISTS {t}")
    for t in ("record_abstracts", "review_packages", "review_package_items"):
        c.execute(f"DROP TABLE IF EXISTS {t}")
    c.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    c.execute("DELETE FROM meta WHERE key='recall_looks_before_v7'")
    if not with_result:
        c.execute("DELETE FROM meta WHERE key='last_result:recall'")
    c.commit()
    c.close()


def test_migrate_v6_store(tmp_path):
    p = _labelled(tmp_path)
    estimate(p, "validation", "main", B=50)        # a stored recall result
    p.store.execute("DELETE FROM validation_looks")
    db = p.root / "store.sqlite"
    p.store.conn.close()
    _as_v6(db)
    q = Project.load(p.root)
    st = q.store
    assert int(st.get_meta("schema_version")) == SCHEMA_VERSION >= 7
    for t in V7_NAMES:
        assert st.columns(t), t
    for t, cols in V7_COLUMNS.items():
        assert all(c.split()[0] in st.columns(t) for c in cols)
    assert st.get_meta("recall_looks_before_v7") is not None and va.looks_before_line(q) == va.LOOKS_BEFORE
    fresh = Project.init(tmp_path / "fresh", platforms=["scopus"]).store
    for t in ("labels", "adjudications", *V7_NAMES):
        assert fresh.columns(t) == st.columns(t), t
    assert "CREATE TABLE IF NOT EXISTS validation_looks" in V7_TABLES


def test_migration_marks_earlier_results(tmp_path):
    p = _labelled(tmp_path)
    db = p.root / "store.sqlite"
    p.store.conn.close()
    _as_v6(db, with_result=False)                  # labels but no stored result: no key
    assert Project.load(p.root).store.get_meta("recall_looks_before_v7") is None


def test_migration_runs_twice(tmp_path):
    p = _labelled(tmp_path)
    estimate(p, "validation", "main", B=50, record_look=False)
    db = p.root / "store.sqlite"
    p.store.conn.close()
    _as_v6(db)
    q = Project.load(p.root)
    key = q.store.get_meta("recall_looks_before_v7")
    q.store._migrate_v7()
    q.store.conn.close()
    r = Project.load(p.root)
    assert r.store.get_meta("recall_looks_before_v7") == key and int(r.store.get_meta("schema_version")) == SCHEMA_VERSION


# ------------------------------------------------------------------------- a changed configuration after a look (D8)
S1 = {"model": "m1", "prompt_version": 1}
NOTICE = ("Set 2 of main was made after recall on validation was seen (2026-10-21), and its settings differ "
          "(prompt_version). Sample used: this estimate is marked not independent, and a fresh sample is recommended. "
          "Not informed: you state the change was not informed by the validation result; every output says so. The "
          "choice is final.")
NOT_INDEPENDENT = ("Not independent: the configuration was changed after recall on validation was seen. Draw a fresh "
                   "sample for an independent estimate.")


def _looked(tmp_path, monkeypatch, settings2=None):
    """Set 1 of main estimated on validation (look dated 2026-10-21), then set 2 made with another prompt version."""
    import json

    from wider.core.provenance import sha256_json

    monkeypatch.setenv("WIDER_REVIEWER", "R1")
    p = _labelled(tmp_path)
    p.store.execute("UPDATE screen_result_sets SET settings=?, settings_hash=? WHERE key='main'",
                    (json.dumps(S1, sort_keys=True), sha256_json(S1)))
    estimate(p, "validation", "main", B=50)
    p.store.execute("UPDATE validation_looks SET first_at='2026-10-21T09:00:00+00:00'")
    _second_set(p, settings2 if settings2 is not None else {**S1, "prompt_version": 2})
    return p


def test_changed_set_needs_choice(tmp_path, monkeypatch):
    p = _looked(tmp_path, monkeypatch)
    n_hist, n_results = len(journal.changes(p)), p.store.scalar("SELECT COUNT(*) FROM stage_results")
    r = CliRunner().invoke(cli_app, ["recall", "main", "--set", "2", "-B", "50", "--dir", str(p.root)])
    out = " ".join(r.output.split())
    assert r.exit_code == 2 and NOTICE in out and va.CHOOSE_SAMPLE in out
    assert [x["result_set_key"] for x in _looks(p)] == ["main"] and len(journal.changes(p)) == n_hist
    assert p.store.scalar("SELECT COUNT(*) FROM stage_results") == n_results
    j = TestClient(create_app(p)).get("/api/recall", params={"protocol": "main", "set": 2, "B": 50})
    assert j.status_code == 200 and j.json()["choice_needed"] is True and j.json()["notice"] == NOTICE
    assert "variants" not in j.json() and [x["result_set_key"] for x in _looks(p)] == ["main"]
    both = CliRunner().invoke(cli_app, ["recall", "main", "--set", "2", "--sample-used", "--not-informed",
                                        "--dir", str(p.root)])
    assert both.exit_code == 2 and va.CHOOSE_BOTH in " ".join(both.output.split())
    # a nested comparison gives the block without numbers, set 1 as usual
    e = estimate(p, "validation", "main", B=50, result_set=1, compare=2)
    assert e["compare"] == {"choice_needed": True, "notice": NOTICE} and e["variants"]


def test_sample_used_marks_later_sets(tmp_path, monkeypatch):
    p = _looked(tmp_path, monkeypatch)
    r = CliRunner().invoke(cli_app, ["recall", "main", "--set", "2", "-B", "50", "--sample-used", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    h = journal.changes(p, kind="sample_used")
    assert len(h) == 1 and not h[0]["undoable"] and h[0]["why_not"] == "a validation sample recorded as used stays on record"
    assert len(journal.changes(p, kind="sample_look")) == 2
    assert NOT_INDEPENDENT in estimate(p, "validation", "main", B=50, result_set=2)["lines"]
    _second_set(p, {**S1, "prompt_version": 3})   # a later set: asked again, and marked too
    assert va.needs_choice(p, "validation", "main#3")
    assert NOT_INDEPENDENT in estimate(p, "validation", "main", B=50, result_set=3, choice="not_informed")["lines"]
    assert NOT_INDEPENDENT not in estimate(p, "validation", "main", B=50, result_set=1)["lines"]
    # final: a second choice records nothing
    assert va.record_choice(p, "validation", "main#2", "not_informed") is False


def test_not_informed_disclosed(tmp_path, monkeypatch):
    from wider.recall.outputs import methods_draft
    from wider.recall.report import build_report

    p = _looked(tmp_path, monkeypatch)
    line = ("The researcher (R1) states that set 2 of main was not informed by the recall result on validation seen on "
            "2026-10-21.")
    j = TestClient(create_app(p)).get("/api/recall", params={"protocol": "main", "set": 2, "B": 50,
                                                             "choice": "not_informed"},
                                                     headers={"X-Wider-Author": "R1"})
    assert j.status_code == 200 and line in j.json()["lines"] and NOT_INDEPENDENT not in j.json()["lines"]
    h = journal.changes(p, kind="sample_not_informed")
    assert len(h) == 1 and h[0]["author"] == "R1" and not h[0]["undoable"]
    assert line in build_report(p, protocol="main", sample="validation", result_set=2)["recall"]["lines"]
    p.store.execute("UPDATE screen_result_sets SET active=(key='main#2') WHERE protocol='main'")
    from wider.core.researchers import Attributor
    assert line.replace("(R1)", f"({Attributor(p).who('R1')})") in methods_draft(p, "main", 2)   # shared text (D5)
    assert "(R1)" not in methods_draft(p, "main", 2) and Attributor(p).who("R1").startswith("Researcher ")
    from wider.core.researchers import set_attribution
    set_attribution(p, "names")
    assert Attributor(p).who("R1") == "R1"                           # typed on the page: declared
    assert line in methods_draft(p, "main", 2)                       # names mode: the declared name as recorded
    set_attribution(p, "none")
    assert "(R1)" not in methods_draft(p, "main", 2)


def test_same_settings_no_choice(tmp_path, monkeypatch):
    p = _looked(tmp_path, monkeypatch, settings2=S1)
    assert va.needs_choice(p, "validation", "main#2") is None
    e = estimate(p, "validation", "main", B=50, result_set=2)
    assert NOT_INDEPENDENT not in e["lines"] and not any(x.startswith("The researcher (") for x in e["lines"])


def test_null_hash_set_asks(tmp_path, monkeypatch):
    p = _looked(tmp_path, monkeypatch)
    p.store.execute("UPDATE screen_result_sets SET settings=NULL, settings_hash=NULL WHERE key='main#2'")
    notice = va.needs_choice(p, "validation", "main#2")
    assert "and its settings are unknown (imported). Sample used:" in notice
    assert "differ" not in notice


def test_report_with_pending_choice(tmp_path, monkeypatch):
    import json

    p = _looked(tmp_path, monkeypatch)
    pend = "Recall on validation for set 2 is not given until you choose: sample used or not informed."
    run = CliRunner()
    for args in (["report", "--protocol", "main", "--set", "2"], ["report", "--protocol", "main", "--set", "2", "--bundle"],
                 ["export", "methods", "--protocol", "main", "--set", "2"],
                 ["export", "package", "--protocol", "main", "--set", "2"]):
        r = run.invoke(cli_app, [*args, "--dir", str(p.root)])
        assert r.exit_code == 0, (args, r.output)
        assert NOTICE in " ".join(r.output.split()), args
        rec = json.loads((p.root / "exports" / "report.json").read_text())["recall"]
        if args[0] == "report":
            assert rec["choice_needed"] is True and rec["notice"] == NOTICE and pend in rec["lines"]
            assert pend in (p.root / "exports" / "report.md").read_text()
        if args[1] == "methods":
            assert pend in " ".join(r.output.split())
    c = TestClient(create_app(p))
    for method, url in (("post", "/api/report"), ("post", "/api/report/bundle"), ("get", "/api/outputs/methods/draft"),
                        ("post", "/api/outputs/package")):
        j = getattr(c, method)(url, params={"protocol": "main", "result_set": 2})
        assert j.status_code == 200 and j.json()["choice_needed"] is True and j.json()["notice"] == NOTICE, url
    assert [x["result_set_key"] for x in _looks(p)] == ["main"]
    j = c.post("/api/report", params={"protocol": "main", "result_set": 2, "choice": "used"})
    assert j.status_code == 200 and "choice_needed" not in j.json()
    assert NOT_INDEPENDENT in j.json()["report"]["recall"]["lines"]
