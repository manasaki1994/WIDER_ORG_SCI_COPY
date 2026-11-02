"""Which backend made each screening call, and what happens when the backend of an unfinished run changes
(design Decisions 16 and 29)."""
import json

import pytest

from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401
from wider.core.backends import update_provider
from wider.core.models import ProviderConfig
from wider.providers import fake as fakemod
from wider.screen.engine import BackendChangeNeeded, replay, run_calls, run_mixed, screen


@pytest.fixture
def counted(_project_fixture, monkeypatch):  # noqa: F811
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    return _project_fixture, counter


def _partial(p):
    """A run of pass A over the corpus that stopped after 3 records."""
    return screen(p, "corpus", pass_name="A", limit=3)


def _set_model(p, model):
    p.config.providers[-1].model = model
    p.save()


def _changes(p, run_id):
    return [dict(r) for r in p.store.query("SELECT * FROM run_backend_changes WHERE run_id=? ORDER BY change_id",
                                           (run_id,))]


def _run_dir(p, run_id):
    return p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (run_id,))["dir"]


def test_resume_after_model_edit_needs_confirmation(counted):
    p, counter = counted
    first = _partial(p)
    _set_model(p, "fake-2")
    n = counter["calls"]
    with pytest.raises(BackendChangeNeeded) as e:
        screen(p, "corpus", pass_name="A")
    ch = e.value.change
    assert ch["case"] == "a" and ch["run_id"] == first["run_id"] and ch["done"] == 3
    assert ch["from"]["model"] == "fake" and ch["to"]["model"] == "fake-2" and "model" in ch["changed"]
    assert not ch["operational_only"] and "--backend-change continue" in str(e.value)
    # nothing ran, nothing was recorded
    assert counter["calls"] == n and not _changes(p, first["run_id"])
    # "new" needs another backend name for a plain run
    with pytest.raises(ValueError, match="another backend name"):
        screen(p, "corpus", pass_name="A", backend_change="new")
    assert counter["calls"] == n


def test_continue_records_change_and_marks_mixed(counted):
    p, counter = counted
    first = _partial(p)
    assert not run_mixed(p, first["run_id"])
    _set_model(p, "fake-2")
    res = screen(p, "corpus", pass_name="A", backend_change="continue")
    assert res["run_id"] == first["run_id"] and res["backend_change"]["case"] == "a" and res["n_err"] == 0
    (row,) = _changes(p, first["run_id"])
    assert row["confirmed_via"] == "cli" and row["from_model"] == "fake" and row["to_model"] == "fake-2"
    assert row["records_done"] == 3 and "model" in json.loads(row["changed"])
    lines = (_run_dir(p, first["run_id"]) / "backend_changes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["confirmed_via"] == "cli"
    assert run_mixed(p, first["run_id"])
    # finishing it again asks nothing more
    assert screen(p, "corpus", pass_name="A")["resumed"] == res["n_records"]
    assert len(_changes(p, first["run_id"])) == 1


def test_unfinished_run_under_other_backend_offers_choice(counted):
    p, counter = counted
    first = _partial(p)
    p.config.providers.append(ProviderConfig(name="fake2", kind="fake", model="fake-2"))
    p.config.default_provider = "fake2"
    p.save()
    with pytest.raises(BackendChangeNeeded) as e:
        screen(p, "corpus", pass_name="A")
    ch = e.value.change
    assert ch["case"] == "b" and ch["run_id"] == first["run_id"] and ch["to"]["provider"] == "fake2"
    assert "--backend-change new" in str(e.value)
    # "new": a separate run under the new backend's name; the first run is left as it was
    new = screen(p, "corpus", pass_name="A", backend_change="new")
    assert new["run_id"] != first["run_id"] and "fake2" in new["run_id"] and new["resumed"] == 0
    assert not _changes(p, first["run_id"]) and not _changes(p, new["run_id"])
    assert p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (first["run_id"],)) == 3


def test_unfinished_run_under_other_backend_continue(counted):
    p, _ = counted
    first = _partial(p)
    p.config.providers.append(ProviderConfig(name="fake2", kind="fake", model="fake-2"))
    p.config.default_provider = "fake2"
    p.save()
    res = screen(p, "corpus", pass_name="A", backend_change="continue")
    assert res["run_id"] == first["run_id"] and res["resumed"] == 3
    (row,) = _changes(p, first["run_id"])
    assert row["from_provider"] == "fake" and row["to_provider"] == "fake2"


def test_finished_run_never_asks(counted):
    p, counter = counted
    first = screen(p, "corpus", pass_name="A")
    _set_model(p, "fake-2")
    n = counter["calls"]
    res = screen(p, "corpus", pass_name="A")
    assert res["already_done"] == {"run_id": first["run_id"], "model": "fake"}
    assert counter["calls"] == n and not _changes(p, first["run_id"])


def test_cli_version_change_recorded_without_asking(counted, monkeypatch):
    p, counter = counted
    ver = {"v": "1.0.0"}
    orig = fakemod.FakeProvider.identity
    monkeypatch.setattr(fakemod.FakeProvider, "identity", lambda self: {**orig(self), "version": ver["v"]})
    first = _partial(p)
    ver["v"] = "1.0.1"
    res = screen(p, "corpus", pass_name="A")   # no backend_change given: not asked
    assert res["run_id"] == first["run_id"] and "backend_change" not in res
    (row,) = _changes(p, first["run_id"])
    assert row["confirmed_via"] == "auto" and "version" in json.loads(row["changed"])
    assert not run_mixed(p, first["run_id"])
    assert len(list(_run_dir(p, first["run_id"]).glob("backend_*.json"))) == 2


def test_operational_edit_recorded_without_asking(counted):
    p, _ = counted
    first = _partial(p)
    update_provider(p, "fake", timeout_s=77, price_input_per_mtok=1.5)
    res = screen(p, "corpus", pass_name="A")
    assert res["run_id"] == first["run_id"] and "backend_change" not in res
    (row,) = _changes(p, first["run_id"])
    assert row["confirmed_via"] == "auto"
    assert {"timeout_s", "price_input_per_mtok"} <= set(json.loads(row["changed"]))
    assert not run_mixed(p, first["run_id"])


def test_calls_record_backend_per_call(counted):
    p, _ = counted
    first = _partial(p)
    _set_model(p, "fake-2")
    screen(p, "corpus", pass_name="A", backend_change="continue")
    calls = run_calls(p, first["run_id"])
    assert all(c["provenance"] == "recorded per call" and c["provider"] == "fake" for c in calls)
    by_model = {}
    for c in calls:
        by_model.setdefault(c["model"], set()).add(c["backend_hash"])
    assert sum(1 for c in calls if c["model"] == "fake") == 3 and set(by_model) == {"fake", "fake-2"}
    hashes = by_model["fake"] | by_model["fake-2"]
    assert len(by_model["fake"]) == len(by_model["fake-2"]) == 1 and len(hashes) == 2
    for h in hashes:
        snap = json.loads(p.store.scalar("SELECT snapshot FROM backend_snapshots WHERE backend_hash=?", (h,)))
        assert snap["model"] in ("fake", "fake-2")
        assert (_run_dir(p, first["run_id"]) / f"backend_{h[:12]}.json").exists()


def test_successful_evaluation_never_replaced(counted):
    p, _ = counted
    first = _partial(p)
    st = p.store
    rows = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM screen_calls WHERE run_id=?", (first["run_id"],))}
    failed = min(rows)
    st.execute("UPDATE screen_calls SET ok=0, rule_decision=NULL, error='synthetic' WHERE run_id=? AND record_id=?",
               (first["run_id"], failed))
    _set_model(p, "fake-2")
    screen(p, "corpus", pass_name="A", backend_change="continue")
    after = {r["record_id"]: dict(r) for r in st.query("SELECT * FROM screen_calls WHERE run_id=?", (first["run_id"],))}
    for rid, before in rows.items():
        if rid == failed:   # only the failed row is retried in place, on the new backend
            assert after[rid]["ok"] == 1 and after[rid]["model"] == "fake-2"
        else:
            assert after[rid] == before
    # a direct second insert for a done record changes nothing either
    rid = next(r for r in rows if r != failed)
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,model) VALUES(?,?,?,?,?,?,?) "
               "ON CONFLICT(run_id,record_id,pass) DO UPDATE SET model=excluded.model WHERE screen_calls.ok=0",
               (first["run_id"], rid, "A", "x", 1, "t", "other"))
    assert st.scalar("SELECT model FROM screen_calls WHERE run_id=? AND record_id=?", (first["run_id"], rid)) == "fake"


def test_rerun_keeps_final_decisions(counted):
    from wider.screen.protocol import run_protocol

    p, counter = counted
    run_protocol(p, "sample:development", "main")
    st = p.store
    before = [dict(r) for r in st.query("SELECT * FROM screen_decisions WHERE protocol='main' ORDER BY record_id")]
    assert before and all(r["vote_calls"] for r in before)
    n = counter["calls"]
    run_protocol(p, "sample:development", "main")
    after = [dict(r) for r in st.query("SELECT * FROM screen_decisions WHERE protocol='main' ORDER BY record_id")]
    assert counter["calls"] == n
    strip = lambda rows: [{k: v for k, v in r.items() if k != "decided_at"} for r in rows]  # noqa: E731
    assert strip(after) == strip(before)


def test_migrate_v4_screen_columns(tmp_path):
    import sqlite3

    from tests.unit.test_runs import _v3_store
    from wider.core.store import Store

    db = tmp_path / "store.sqlite"
    _v3_store(db)
    con = sqlite3.connect(db)
    con.execute("INSERT INTO screen_decisions(protocol,record_id,decision,votes,decided_at) "
                "VALUES('main',1,'include','[\"include\",\"include\"]','t')")
    con.execute("INSERT INTO screen_decisions(protocol,record_id,decision,decided_at) VALUES('imported',1,'exclude','t')")
    con.commit()
    con.close()
    st = Store(db)
    assert {"backend_hash", "provider", "model", "model_reported", "eval_key"} <= set(st.columns("screen_calls"))
    assert {"vote_calls", "decided_by", "origin"} <= set(st.columns("screen_decisions"))
    sets = {r["protocol"]: dict(r) for r in st.query("SELECT * FROM screen_result_sets")}
    assert sets["main"]["key"] == "main" and sets["main"]["result_set"] == 1 and sets["main"]["source"] == "screening"
    assert sets["imported"]["source"] == "import" and sets["main"]["active"] == 1
    row = st.one("SELECT vote_calls, decided_by FROM screen_decisions WHERE protocol='main'")
    assert row["vote_calls"] is None and row["decided_by"] is None
    st.close()
    # opening it again changes nothing
    st = Store(db)
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets") == 2
    st.close()


def test_old_store_calls_fall_back_to_run_model(counted):
    p, _ = counted
    first = _partial(p)
    p.store.execute("UPDATE screen_calls SET provider=NULL, model=NULL, backend_hash=NULL WHERE run_id=?",
                    (first["run_id"],))
    calls = run_calls(p, first["run_id"])
    assert calls and all(c["provenance"] == "recorded per run" for c in calls)
    assert {(c["provider"], c["model"]) for c in calls} == {("fake", "fake")}


def test_replay_keeps_call_provenance(counted):
    p, _ = counted
    first = _partial(p)
    _set_model(p, "fake-2")
    screen(p, "corpus", pass_name="A", backend_change="continue")
    cols = "record_id, provider, model, backend_hash, eval_key"
    st = p.store
    before = [dict(r) for r in st.query(f"SELECT {cols} FROM screen_calls WHERE run_id=? ORDER BY record_id",
                                        (first["run_id"],))]
    st.execute("DELETE FROM backend_snapshots")   # replay restores them from the run folder
    replay(p, first["run_id"])
    after = [dict(r) for r in st.query(f"SELECT {cols} FROM screen_calls WHERE run_id=? ORDER BY record_id",
                                       (first["run_id"],))]
    assert after == before
    hashes = {c["backend_hash"] for c in before}
    assert {r["backend_hash"] for r in st.query("SELECT backend_hash FROM backend_snapshots")} >= hashes
