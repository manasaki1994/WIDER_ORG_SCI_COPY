"""Protocol exports: one row per decision and per evaluation, with models, set columns and cost (Decision 16)."""
import csv
import json

import pytest

from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401
from wider.providers import fake as fakemod
from wider.screen.export import DECISIONS_COLUMNS, EVALUATIONS_COLUMNS, decision_rows, export_protocol
from wider.screen.protocol import run_protocol


@pytest.fixture
def screened(_project_fixture, monkeypatch):  # noqa: F811
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    run_protocol(_project_fixture, "sample:development", "main")
    return _project_fixture


def _rows(project, rel):
    with open(project.root / rel, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_export_writes_decisions_and_evaluations(screened):
    out = export_protocol(screened, "main")
    assert out["key"] == "main" and out["result_set"] == 1 and out["decisions"].startswith("exports/screening/")
    dec, ev = _rows(screened, out["decisions"]), _rows(screened, out["evaluations"])
    assert list(dec[0]) == DECISIONS_COLUMNS and list(ev[0]) == EVALUATIONS_COLUMNS
    assert len(dec) == out["n_decisions"] > 0 and len(ev) == out["n_evaluations"] >= len(dec)
    assert {d["decided_by_models"] for d in dec} == {"fake"} and not any(d["mixed_models"] for d in dec)
    assert {e["evaluation"] for e in ev} == {"new"} and {e["result_set"] for e in ev} == {"1"}
    # a local fake backend costs 0; every agreeing vote is used for the final decision
    assert {e["cost_usd"] for e in ev} == {"0.0"}
    assert any(e["used_for_final"] == "yes" for e in ev)
    for e in ev:
        if e["used_for_final"]:
            assert e["decision"] == e["final_decision"]
    text = (screened.root / out["evaluations"]).read_text()
    assert "abstract" not in text.lower() and "extra words" not in text


def test_decision_rows_name_the_models_and_a_mixed_set(screened):
    st = screened.store
    rows = decision_rows(screened, "main")
    assert rows and all(r["models"] == ["fake"] and not r["mixed_models"] for r in rows)
    # one call of a decision's votes recorded under another model: that decision is mixed
    r = next(r for r in rows if len(r["vote_call_ids"]) > 1)
    st.execute("UPDATE screen_calls SET model='fake-2' WHERE call_id=?", (r["vote_call_ids"][-1],))
    again = {x["record_id"]: x for x in decision_rows(screened, "main")}
    assert again[r["record_id"]]["mixed_models"] and again[r["record_id"]]["models"] == ["fake", "fake-2"]


def test_export_refuses_a_protocol_without_decisions(screened):
    with pytest.raises(ValueError, match="no screening decisions"):
        export_protocol(screened, "nothing")


def test_export_route_matches_cli(screened):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    out = CliRunner().invoke(cli_app, ["screen", "export", "main", "--dir", str(screened.root)])
    assert out.exit_code == 0, out.output
    cli_text = {n: (screened.root / "exports" / "screening" / n).read_bytes() for n in
                ("main_decisions.csv", "main_evaluations.csv")}
    c = TestClient(create_app(screened))
    r = c.post("/api/screen/export", params={"protocol": "main"})
    assert r.status_code == 200
    body = r.json()
    assert body["decisions"] == "exports/screening/main_decisions.csv"
    for rel in (body["decisions"], body["evaluations"]):
        f = c.get("/api/outputs/file", params={"path": rel})
        assert f.status_code == 200 and f.content == cli_text[rel.rsplit("/", 1)[1]]
    assert c.post("/api/screen/export", params={"protocol": "main", "result_set": 5}).status_code == 404
    assert c.post("/api/screen/export", params={"protocol": "nothing"}).status_code == 400
    out = CliRunner().invoke(cli_app, ["screen", "export", "main", "--set", "5", "--dir", str(screened.root)])
    assert out.exit_code == 1 and "no result set 5 of 'main'" in out.output


def test_protocol_export_marks_final_evaluations(screened):
    st = screened.store
    out = export_protocol(screened, "main")
    ev = _rows(screened, out["evaluations"])
    dec = {int(d["record_id"]): d for d in _rows(screened, out["decisions"])}
    finals = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM screen_decisions WHERE protocol='main'")}
    for e in ev:
        rid = int(e["record_id"])
        assert e["final_decision"] == finals[rid]["decision"] == dec[rid]["final_decision"]
        votes = json.loads(finals[rid]["vote_calls"] or "[]")
        expected = int(e["call_id"]) in votes and e["decision"] == e["final_decision"]
        assert (e["used_for_final"] == "yes") == expected
        assert e["provider"] == "fake" and e["model"] == "fake" and len(e["backend_hash"]) == 64
    # a call whose vote lost the majority stays in the export, not used for the final decision
    cid = next(int(e["call_id"]) for e in ev if e["used_for_final"] == "yes")
    st.execute("UPDATE screen_calls SET rule_decision='maybe' WHERE call_id=?", (cid,))
    ev2 = {int(e["call_id"]): e for e in _rows(screened, export_protocol(screened, "main")["evaluations"])}
    assert ev2[cid]["used_for_final"] == "" and ev2[cid]["decision"] == "maybe"
    # a researcher's decision uses no model evaluation
    rid = int(ev[0]["record_id"])
    st.execute("UPDATE screen_decisions SET decided_by='researcher' WHERE protocol='main' AND record_id=?", (rid,))
    ev3 = _rows(screened, export_protocol(screened, "main")["evaluations"])
    assert not any(e["used_for_final"] for e in ev3 if int(e["record_id"]) == rid)


def test_run_decisions_csv_has_model_columns(screened):
    from wider.screen.engine import DECISION_COLUMNS, write_decisions_csv

    st = screened.store
    rid = st.scalar("SELECT run_id FROM runs WHERE kind='screen' ORDER BY run_id LIMIT 1")
    path = write_decisions_csv(screened, rid)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == DECISION_COLUMNS
    assert {"provider", "model", "model_reported", "backend_hash"} <= set(DECISION_COLUMNS)
    assert len(rows) == st.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (rid,))
    assert {r["provider"] for r in rows} == {"fake"} and {r["model"] for r in rows} == {"fake"}
    hashes = {r["backend_hash"] for r in rows}
    assert len(hashes) == 1 and st.scalar("SELECT COUNT(*) FROM backend_snapshots WHERE backend_hash=?",
                                          (hashes.pop(),)) == 1
    assert {r["evaluation"] for r in rows} == {"new"}


def test_second_screen_carries_vote_calls(_project_fixture, monkeypatch):  # noqa: F811
    from wider.screen.protocol import second_screen

    good = _responder({"calls": 0})
    mode = {"first": True}

    def respond(req):   # papers 0 and 3 are maybes in the first screen, includes when re-read
        out = good(req)
        if mode["first"] and ("Paper 0 " in req.user or "Paper 3 " in req.user):
            out["decision"] = "maybe"
        return out

    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, respond))
    p = _project_fixture
    run_protocol(p, "corpus", "main")
    mode["first"] = False
    st = p.store
    src = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM screen_decisions WHERE protocol='main'")}
    assert any(r["decision"] == "maybe" for r in src.values())
    out = second_screen(p, "main")
    key = out["key"]
    dst = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM screen_decisions WHERE protocol=?", (key,))}
    carried = [rid for rid, r in src.items() if r["decision"] != "maybe"]
    assert carried
    for rid in carried:
        assert dst[rid]["vote_calls"] == src[rid]["vote_calls"] and dst[rid]["vote_calls"]
        assert dst[rid]["decided_by"] == "carried from main"
    reread = [rid for rid, r in src.items() if r["decision"] == "maybe" and rid in dst
              and not dst[rid]["decided_by"].startswith("carried")]
    assert reread
    second_runs = {r["run_id"] for r in st.query("SELECT run_id FROM runs WHERE kind='second_screen'")}
    for rid in reread:
        calls = json.loads(dst[rid]["vote_calls"])
        assert len(calls) >= 2 and {st.scalar("SELECT run_id FROM screen_calls WHERE call_id=?", (c,))
                                    for c in calls} <= second_runs
    # the export of the second set names the carried votes' models too
    rows = decision_rows(p, key)
    assert all(r["models"] == ["fake"] for r in rows if r["record_id"] in carried)


def _import_file(p, tmp_path, n=3):
    titles = [r["title"] for r in p.store.query("SELECT title FROM records ORDER BY record_id LIMIT ?", (n,))]
    f = tmp_path / "earlier.csv"
    with open(f, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "final_decision"])
        for t in titles:
            w.writerow([t, "include"])
    return f


def test_import_never_replaces_screening_results(screened, tmp_path):
    from wider.screen.decisions import import_csv

    st = screened.store
    snap = lambda key: [dict(r) for r in st.query(  # noqa: E731
        "SELECT * FROM screen_decisions WHERE protocol=? ORDER BY record_id", (key,))]
    before = snap("main")
    f = _import_file(screened, tmp_path)
    out = import_csv(screened, "main", f)
    assert out["result_set"] == 2 and out["key"] == "main#2" and not out["active"]
    assert snap("main") == before
    s2 = st.one("SELECT * FROM screen_result_sets WHERE key='main#2'")
    assert s2["source"] == "import" and s2["import_file"] == "earlier.csv" and len(s2["import_sha256"]) == 64
    assert st.scalar("SELECT key FROM screen_result_sets WHERE protocol='main' AND active=1") == "main"
    # an import over an earlier import adds one more set; neither earlier set changes
    first_import = snap("main#2")
    again = import_csv(screened, "main", f)
    assert again["key"] == "main#3"
    assert snap("main") == before and snap("main#2") == first_import
    # into a new protocol the import is set 1
    fresh = import_csv(screened, "earlier", f)
    assert fresh["key"] == "earlier" and fresh["result_set"] == 1
    # listed in History and final, as every import (import-history-and-model-selection)
    from wider.core import journal

    h = journal.changes(screened, kind="decisions_import")[0]
    assert h["summary"].startswith("decisions imported into earlier set 1 from earlier.csv") and not h["undoable"]


def test_import_listed_in_history(screened, tmp_path, monkeypatch):
    """import-history-and-model-selection D1, D2: every import is a final History entry, the same on both surfaces."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core import journal
    from wider.core.provenance import sha256_bytes
    from wider.screen import decisions as dmod
    from wider.screen import result_sets as rs
    from wider.ui.app import create_app

    monkeypatch.setenv("WIDER_REVIEWER", "Ana")
    p, st = screened, screened.store
    rs.label(p, "main", 1, "first screening")   # Ana's change before the import
    f = _import_file(p, tmp_path)
    f = f.rename(tmp_path / "revised.csv")
    digest = sha256_bytes(f.read_bytes())
    r = CliRunner().invoke(cli_app, ["screen", "import-decisions", "main", str(f), "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    summary = f"decisions imported into main set 2 from revised.csv (sha256 {digest[:12]})"
    h = journal.changes(p, kind="decisions_import")
    assert len(h) == 1 and h[0]["summary"] == summary and h[0]["author"] == "Ana" and h[0]["at"]
    assert h[0]["kind_label"] == "decisions imported" and not h[0]["undoable"]
    assert h[0]["why_not"] == journal.IMPORT_FINAL == ("imported decisions stay on record as their own result set; "
                                                      "make another set active instead")
    s2 = st.one("SELECT created_by, import_sha256 FROM screen_result_sets WHERE key='main#2'")
    assert s2["created_by"] == "Ana" and s2["import_sha256"] == digest
    # the History page reads the same entry, kinds and refusal
    c = TestClient(create_app(p))
    api = c.get("/api/history", params={"kind": "decisions_import"}).json()
    assert [(x["change_id"], x["summary"], x["author"], x["at"], x["undoable"], x["why_not"]) for x in api] == \
        [(h[0]["change_id"], summary, "Ana", h[0]["at"], False, journal.IMPORT_FINAL)]
    assert {"kind": "decisions_import", "label": "decisions imported"} in c.get("/api/history/kinds").json()
    out = CliRunner().invoke(cli_app, ["history", "list", "--kind", "decisions_import", "--dir", str(p.root)])
    flat = " ".join(out.output.split())
    assert out.exit_code == 0 and "decisions imported" in flat and "revised.csv" in flat and "Ana" in flat
    assert "make another set active instead" in flat
    assert "decisions_import\tdecisions imported" in CliRunner().invoke(cli_app, ["history", "kinds"]).output
    # undo is refused on both surfaces; undo-last skips the import and takes back Ana's label
    cid = h[0]["change_id"]
    u = c.post(f"/api/history/{cid}/undo", headers={"x-wider-author": "Ana"})
    assert u.status_code == 409 and journal.IMPORT_FINAL in u.json()["detail"]
    u = CliRunner().invoke(cli_app, ["history", "undo", str(cid), "--dir", str(p.root)])
    assert u.exit_code == 1 and "make another set active instead" in " ".join(u.output.split())
    assert st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main#2'") > 0
    back = c.post("/api/history/undo-last", headers={"x-wider-author": "Ana"})
    assert back.status_code == 200 and "first screening" in back.json()["summary"]
    assert st.scalar("SELECT label FROM screen_result_sets WHERE key='main'") is None
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE key='main#2'") == 1
    # a set switch before a later import cannot be undone: putting the rows back would delete the imported set; both
    # surfaces name the import as final and the way out
    switch = rs.use(p, "main", 2)
    assert switch["active"]
    # --author signs the import (History and the set's created_by) over $WIDER_REVIEWER, as on other signing commands
    r = CliRunner().invoke(cli_app, ["screen", "import-decisions", "main", str(f), "--dir", str(p.root), "--author", "Bea"])
    assert r.exit_code == 0, r.output
    imp3 = journal.changes(p, kind="decisions_import")[0]
    assert "main set 3" in imp3["summary"] and imp3["author"] == "Bea"
    assert st.scalar("SELECT created_by FROM screen_result_sets WHERE key='main#3'") == "Bea"
    why = "set 3 was imported into main since, and imported decisions stay on record as their own result set"
    u = c.post("/api/history/undo-last", headers={"x-wider-author": "Ana"})
    assert u.status_code == 409 and why in u.json()["detail"]
    assert f"blocked by change {imp3['change_id']} by Bea: {imp3['summary']}" in u.json()["detail"]
    assert "undo the later change first" not in u.json()["detail"]
    u = CliRunner().invoke(cli_app, ["history", "undo", "--dir", str(p.root)])
    flat = " ".join(u.output.split())
    assert u.exit_code == 1 and why in flat and "wider screen sets use main <set>" in flat, u.output
    assert f"blocked by change {imp3['change_id']} by Bea: {imp3['summary']}" in flat
    sw = journal.changes(p, kind="result_set")[0]
    assert not sw["undoable"] and why in sw["why_not"]
    assert st.scalar("SELECT result_set FROM screen_result_sets WHERE protocol='main' AND active=1") == 2
    # an import that fails after its set row was written stores neither the set nor the entry

    def boom():
        raise RuntimeError("disk full")
    monkeypatch.setattr(dmod, "utcnow", boom)
    with pytest.raises(RuntimeError):
        dmod.import_csv(p, "main", f)
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol='main'") == 3
    assert len(journal.changes(p, kind="decisions_import")) == 2
    monkeypatch.undo()
    # the entry itself failing takes back the set and its decisions too: one transaction
    monkeypatch.setattr(journal, "_store_change", lambda *a, **k: boom())
    with pytest.raises(RuntimeError):
        dmod.import_csv(p, "main", f)
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol='main'") == 3
    assert st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main#4'") == 0
    assert len(journal.changes(p, kind="decisions_import")) == 2
