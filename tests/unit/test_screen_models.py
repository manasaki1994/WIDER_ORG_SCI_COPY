"""The models summary: which models screened what, per run and per result set (Decision 16). The Node tests of the
"Models used" card and the backend-change dialog live with the other text builders in test_result_set_select_node.py
and are collected here too, under the names the spec pins."""
import pytest

from tests.unit.test_result_set_select_node import (  # noqa: F401
    test_backend_change_dialog_text,
    test_models_card_text,
)
from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401
from wider.providers import fake as fakemod
from wider.screen.models_summary import NOT_PRICED, by_model
from wider.screen.protocol import run_protocol


@pytest.fixture
def screened(_project_fixture, monkeypatch):  # noqa: F811
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    run_protocol(_project_fixture, "sample:development", "main")
    return _project_fixture


def test_models_summary_counts(screened):
    st = screened.store
    s = by_model(screened, protocol="main")
    assert s["scope"]["key"] == "main" and s["scope"]["result_set"] == 1
    (m,) = s["models"]
    n_rec = st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main'")
    n_calls = st.scalar("SELECT COUNT(*) FROM screen_calls")
    n_ok = st.scalar("SELECT COUNT(*) FROM screen_calls WHERE ok=1")
    assert m["model"] == "fake" and m["records"] == n_rec == s["totals"]["records"]
    assert m["calls"] == n_calls and m["evaluations"] == n_ok and m["reused"] == 0
    assert m["include"] + m["maybe"] + m["exclude"] == n_ok
    assert 0 < m["final_records"] <= n_rec
    assert m["cost"] == "$0.0000" and not s["mixed"] and not s["experimental"]
    assert s["headline"] == f"{n_rec} unique records, {n_calls} model calls"
    assert s["origins"] == {"screened": n_rec}
    # the same numbers per run
    rid = st.scalar("SELECT run_id FROM runs WHERE kind='screen' ORDER BY run_id LIMIT 1")
    r = by_model(screened, run_id=rid)
    assert r["scope"]["run_id"] == rid and r["scope"]["key"] == "main"
    assert r["totals"]["calls"] == st.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (rid,))
    # one call recorded under a second model: two rows and the set is mixed
    cid = st.scalar("SELECT MAX(call_id) FROM screen_calls WHERE ok=1")
    st.execute("UPDATE screen_calls SET model='fake-2' WHERE call_id=?", (cid,))
    s2 = by_model(screened, protocol="main")
    assert [x["model"] for x in s2["models"]] == ["fake", "fake-2"] and s2["mixed"]
    assert s2["totals"]["calls"] == n_calls


def test_models_summary_unpriced_and_refusals(screened):
    st = screened.store
    st.execute("UPDATE backend_snapshots SET snapshot=json_set(snapshot, '$.kind', 'openai_compatible', "
               "'$.base_url', 'https://api.example.invalid/v1')")
    s = by_model(screened, protocol="main")
    assert s["models"][0]["cost"] == NOT_PRICED and s["totals"]["cost"] == NOT_PRICED
    with pytest.raises(KeyError, match="no screening decisions"):
        by_model(screened, protocol="nosuch")
    with pytest.raises(KeyError, match="no result set"):
        by_model(screened, protocol="main", result_set=9)
    with pytest.raises(KeyError, match="no run"):
        by_model(screened, run_id="nosuch")
    with pytest.raises(ValueError):
        by_model(screened)


def test_models_summary_cli_matches_api(screened):
    import json

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    d = str(screened.root)
    out = CliRunner().invoke(cli_app, ["screen", "models", "--protocol", "main", "--json", "--dir", d])
    assert out.exit_code == 0, out.output
    cli = json.loads(out.output)
    api = TestClient(create_app(screened)).get("/api/screen/models", params={"protocol": "main"})
    assert api.status_code == 200 and api.json() == cli
    text = CliRunner().invoke(cli_app, ["screen", "models", "--dir", d]).output
    assert cli["headline"] in text and "fake" in text
    out = CliRunner().invoke(cli_app, ["screen", "models", "--protocol", "nosuch", "--dir", d])
    assert out.exit_code == 1 and "no screening decisions under protocol 'nosuch'" in out.output
    assert TestClient(create_app(screened)).get("/api/screen/models", params={"protocol": "nosuch"}).status_code == 404


def test_runs_table_mixed_marker(screened):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    st = screened.store
    rid = st.scalar("SELECT run_id FROM runs WHERE kind='screen' ORDER BY run_id LIMIT 1")
    runs = {r["run_id"]: r for r in TestClient(create_app(screened)).get("/api/screen").json()["runs"]}
    assert not any(r["mixed"] for r in runs.values())
    cid = st.scalar("SELECT MAX(call_id) FROM screen_calls WHERE run_id=? AND ok=1", (rid,))
    st.execute("UPDATE screen_calls SET model='fake-2' WHERE call_id=?", (cid,))
    runs = {r["run_id"]: r for r in TestClient(create_app(screened)).get("/api/screen").json()["runs"]}
    assert runs[rid]["mixed"] and sum(r["mixed"] for r in runs.values()) == 1
    out = CliRunner().invoke(cli_app, ["screen", "runs", "--dir", str(screened.root)], env={"COLUMNS": "300"})
    line = next(x for x in out.output.splitlines() if rid in x)
    assert "mixed" in line
