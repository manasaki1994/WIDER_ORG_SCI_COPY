"""WP14 part 1: tokens and costs of model calls, stage-result history, background runs (jobs) with progress, ETA,
cost so far and cancel, prices on backends, `wider costs`, and the static job bar."""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core.models import ProviderConfig
from wider.core.project import Project
from wider.core.stage_result import StageResult, history, record
from wider.core.usage import call_cost, project_totals, track, usage_for_runs
from wider.providers import fake as fakemod
from wider.providers.base import ChatRequest
from wider.providers.registry import make_provider
from wider.screen.engine import screen
from wider.screen.protocol import run_protocol
from wider.ui.app import create_app
from wider.ui.jobs import JobRunner

runner = CliRunner()


def _wait(client_or_runner, job_id, p=None, timeout=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = client_or_runner.get(f"/api/jobs/{job_id}").json() if p is None else client_or_runner.get(p, job_id)
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_call_cost_rules():
    local = ProviderConfig(name="o", kind="ollama", base_url="http://127.0.0.1:11434")
    cloud = ProviderConfig(name="c", kind="openai_compat", base_url="https://api.example.org/v1")
    priced = ProviderConfig(name="p", kind="anthropic", price_input_per_mtok=3.0, price_output_per_mtok=15.0)
    agent = ProviderConfig(name="a", kind="cli_agent", command=["x"])
    assert call_cost(local, {"prompt_tokens": 1000}, "local") == 0.0
    assert call_cost(local, {"prompt_tokens": 1000}) is None  # Ollama routing not established: never free
    assert call_cost(cloud, {"prompt_tokens": 1000}) is None
    assert call_cost(priced, {"input_tokens": 1_000_000, "output_tokens": 100_000}) == pytest.approx(4.5)
    assert call_cost(agent, {"total_cost_usd": 0.0123}) == pytest.approx(0.0123)
    with pytest.raises(ValueError):
        ProviderConfig(name="x", price_input_per_mtok=-1)


def test_usage_logged_only_inside_a_scope(tmp_path):
    p = Project.init(tmp_path / "r")
    prov = make_provider(ProviderConfig(name="f", kind="fake", model="fake"))
    req = ChatRequest(system="s", user="one two three", schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    asyncio.run(prov.complete(req))
    assert p.store.scalar("SELECT COUNT(*) FROM llm_usage") == 0

    async def two():
        with track(p, "construct", "run-x"):
            await asyncio.gather(prov.complete(req), prov.complete(req))
    asyncio.run(two())
    rows = p.store.query("SELECT * FROM llm_usage")
    assert len(rows) == 2 and {r["run_ref"] for r in rows} == {"run-x"} and rows[0]["locality"] == "test" and rows[0]["cost_usd"] == 0
    u = usage_for_runs(p, ["run-x"])
    assert u["calls"] == 2 and u["cost_known"] and u["tokens"] > 0 and u["backends"] == ["f (fake)"]
    assert "one two three" not in json.dumps([dict(r) for r in rows])   # no prompt text in the log


def test_screening_usage_in_results_and_history(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    res = screen(p, "sample:development", pass_name="A")
    assert res["usage"]["calls"] == res["n_records"] == counter["calls"]
    last = json.loads(p.store.get_meta("last_result:screen"))
    assert last["usage"]["locality"] == "test" and last["usage"]["cost_known"]
    out = run_protocol(p, "sample:development", "main")
    # pass A was already done: the protocol shows only the calls it made (pass B and any third reads), 21st round
    assert out["usage"]["calls"] == counter["calls"] - res["n_records"] >= res["n_records"]
    hist = history(p, "screen")
    assert [h["action"] for h in hist[:2]] == ["protocol", "pass"] and hist[0]["result_id"] > hist[1]["result_id"]
    tot = project_totals(p)
    assert tot["calls"] == counter["calls"] and tot["legacy_calls"] == 0
    c = TestClient(create_app(p))
    assert len(c.get("/api/stages/screen/results").json()) >= 3
    runs = c.get("/api/screen").json()["runs"]
    assert all(r["usage"] and r["locality"] == "test" for r in runs)


def test_project_cost_with_prices_and_legacy(tmp_path):
    p = Project.init(tmp_path / "r")
    p.config.providers.append(ProviderConfig(name="cloud", kind="openai_compat", model="m", base_url="https://api.example.org/v1",
                                             price_input_per_mtok=2.0, price_output_per_mtok=8.0))
    p.save()
    p = Project.load(p.root)
    st = p.store
    st.execute("INSERT INTO llm_usage(at,stage,run_ref,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,cost_usd) "
               "VALUES('t','construct','r1','cloud','openai_compat','m','cloud',1,500000,100000,1.8)")
    st.execute("INSERT INTO llm_usage(at,stage,run_ref,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,cost_usd) "
               "VALUES('t','screen','r2','other','openai_compat','m','cloud',1,10,10,NULL)")
    st.execute("PRAGMA foreign_keys=OFF")
    st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,provider,model,status,started_at,dir) VALUES('old','screen','s','h','cloud','m','done','t','d')")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,usage,at) VALUES('old',1,'A','x',1,?, 't')",
               (json.dumps({"prompt_tokens": 1_000_000, "completion_tokens": 0}),))
    t = project_totals(p)
    assert t["calls"] == 3 and t["legacy_calls"] == 1 and t["unpriced_calls"] == 1 and not t["cost_known"]
    assert t["cost_usd"] == pytest.approx(3.8) and t["locality"] == "cloud"
    from wider.core.summary import cost_text, project_summary
    s = project_summary(p)
    assert "plus 1 calls without prices" in cost_text(s["cost"])
    r = runner.invoke(cli_app, ["costs", "--dir", str(p.root), "--by", "backend"], env={"COLUMNS": "200"})
    assert r.exit_code == 0 and "cloud (m, cloud)" in r.output and "1 unpriced" in r.output and "project total: 3 calls" in r.output
    assert runner.invoke(cli_app, ["costs", "--dir", str(p.root), "--by", "nope"]).exit_code == 1


def _logged_and_legacy(tmp_path):
    p = Project.init(tmp_path / "r")
    p.config.providers.append(ProviderConfig(name="cloud", kind="openai_compat", model="m", base_url="https://api.example.org/v1",
                                             price_input_per_mtok=2.0, price_output_per_mtok=8.0))
    p.save()
    p = Project.load(p.root)
    st = p.store
    st.execute("INSERT INTO llm_usage(at,stage,run_ref,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,cost_usd) "
               "VALUES('t','construct','r1','cloud','openai_compat','m','cloud',1,500000,100000,1.8)")
    st.execute("PRAGMA foreign_keys=OFF")
    st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,provider,model,status,started_at,dir) VALUES('old','screen','s','h','cloud','m','done','t','d')")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,usage,at) VALUES('old',1,'A','x',1,?, 't')",
               (json.dumps({"prompt_tokens": 1_000_000, "completion_tokens": 0}),))
    return p


def test_legacy_cost_labelled_estimate(tmp_path):
    p = _logged_and_legacy(tmp_path)
    t = project_totals(p)
    assert t["calls"] == 2 and t["cost_usd"] == pytest.approx(3.8)
    assert t["logged_cost_usd"] == pytest.approx(1.8) and t["legacy_cost_usd"] == pytest.approx(2.0)
    assert t["legacy_label"] == "estimated at today's prices"
    r = runner.invoke(cli_app, ["costs", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "logged cost: $1.8000" in r.output and "$2.0000, estimated at today's prices" in r.output
    from wider.core.summary import cost_text, project_summary
    c = project_summary(p)["cost"]
    assert c["legacy_usd"] == pytest.approx(2.0) and c["logged_usd"] == pytest.approx(1.8)
    assert "estimated at today's prices" in cost_text(c) and "logged $1.80" in cost_text(c)
    home = TestClient(create_app(p)).get("/api/project/summary").json()
    assert home["cost"]["legacy_label"] == "estimated at today's prices"


def test_logged_cost_survives_price_edit(tmp_path):
    from wider.core.backends import update_provider

    p = _logged_and_legacy(tmp_path)
    update_provider(p, "cloud", price_input_per_mtok=4.0)
    p = Project.load(p.root)
    t = project_totals(p)
    assert t["logged_cost_usd"] == pytest.approx(1.8) and t["legacy_cost_usd"] == pytest.approx(4.0)
    assert t["cost_usd"] == pytest.approx(5.8)


def test_prices_via_api_and_cli(tmp_path):
    p = Project.init(tmp_path / "r")
    c = TestClient(create_app(p))
    r = c.post("/api/providers", json={"name": "vendor", "kind": "anthropic", "model": "m", "api_key_env": "X_KEY",
                                       "price_input_per_mtok": 3, "price_output_per_mtok": 15})
    assert r.status_code == 200 and r.json()["price_output_per_mtok"] == 15
    row = [x for x in c.get("/api/providers").json()["providers"] if x["name"] == "vendor"][0]
    assert row["price_input_per_mtok"] == 3
    r = runner.invoke(cli_app, ["providers", "add", "--name", "v2", "--kind", "openai_compat", "--model", "m",
                                "--price-in", "0.5", "--price-out", "1.5", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert Project.load(p.root).provider("v2").price_output_per_mtok == 1.5


def test_stage_result_history_and_scope_usage(tmp_path):
    p = Project.init(tmp_path / "r")
    record(p, StageResult(stage="enrich", action="fetch", headline="first"))
    record(p, StageResult(stage="enrich", action="fetch", headline="second"))
    assert [h["headline"] for h in history(p, "enrich")] == ["second", "first"]
    p.store.execute("INSERT INTO llm_usage(at,stage,run_ref,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,cost_usd) "
                    "VALUES('t','construct','prop-1','f','fake','f','test',1,5,5,0)")
    with track(p, "construct", "prop-1"):
        r = record(p, StageResult(stage="construct", action="propose", headline="proposed"))
    assert r.usage["calls"] == 1 and r.usage["tokens"] == 10


def test_job_runner_progress_cancel_fail_and_interrupted(tmp_path):
    p = Project.init(tmp_path / "r")
    jr = JobRunner()

    def ok_fn(proj, progress):
        for i in range(1, 4):
            progress(i, 3, f"item {i}")
        return {"n": 3}
    jid = jr.start(p.root, "test", "enrich", "Test run", {}, ok_fn)
    j = _wait(jr, jid, p)
    assert j["status"] == "done" and j["result"] == {"n": 3} and j["n_done"] == 3 and j["n_total"] == 3

    gate = {"go": False}

    def slow_fn(proj, progress):
        i = 0
        while True:
            i += 1
            progress(i, 1000)
            time.sleep(0.01)
    jid = jr.start(p.root, "test", "enrich", "Slow run", {}, slow_fn)
    deadline = time.time() + 10  # a loaded machine (pytest -n auto) may take longer than a fixed sleep to report progress
    while (running := jr.get(p, jid))["n_done"] == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert running["status"] == "running" and running["n_done"] > 0 and running["eta_s"] is not None
    with pytest.raises(RuntimeError, match="still running"):
        jr.start(p.root, "test", "enrich", "Second", {}, ok_fn)
    assert jr.cancel(jid)
    assert _wait(jr, jid, p)["status"] == "cancelled"
    assert not gate["go"]

    async def async_slow(proj, progress):
        progress(0, 10)
        await asyncio.sleep(30)
    jid = jr.start(p.root, "test", "screen", "Async run", {}, async_slow)
    time.sleep(0.2)
    assert jr.cancel(jid)
    assert _wait(jr, jid, p)["status"] == "cancelled"

    def boom(proj, progress):
        raise ValueError("bad input")
    jid = jr.start(p.root, "test", "rank", "Failing run", {}, boom)
    j = _wait(jr, jid, p)
    assert j["status"] == "failed" and "bad input" in j["error"]
    # a job left "running" by a server that stopped is shown as interrupted
    p.store.execute("INSERT INTO jobs(job_id,kind,stage,label,status,started_at,updated_at) VALUES('old','x','screen','Old','running','t','t')")
    assert jr.get(p, "old")["status"] == "interrupted"
    assert jr.cancel("old") is False
    # a row read as running just before its thread wrote its own end and stopped keeps that end, not interrupted
    p.store.execute("INSERT INTO jobs(job_id,kind,stage,label,status,started_at,updated_at) VALUES('late','x','rank','Late','running','t','t')")
    stale = p.store.one("SELECT * FROM jobs WHERE job_id='late'")
    p.store.execute("UPDATE jobs SET status='cancelled' WHERE job_id='late'")
    assert jr.describe(p, stale)["status"] == "cancelled"


def test_job_routes_screen_in_background(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    c = TestClient(create_app(p))
    r = c.post("/api/jobs/screen", json={"scope": "sample:development", "pass_name": "J", "provider": "fake"})
    assert r.status_code == 200
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done" and j["result"]["stage_result"]["stage"] == "screen"
    assert j["usage"]["calls"] == counter["calls"] > 0 and j["n_done"] == j["n_total"]
    assert c.get("/api/jobs").json()[0]["job_id"] == j["job_id"] and c.get("/api/jobs?active=true").json() == []
    assert c.get("/api/jobs/nope").status_code == 404
    assert c.post(f"/api/jobs/{j['job_id']}/cancel").status_code == 409
    assert c.post("/api/jobs/propose/nope").status_code == 400
    r = c.post("/api/jobs/embed?embedding=fake")
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    from wider.core.models import EmbeddingConfig
    p.config.embeddings.append(EmbeddingConfig(name="st", kind="sentence_transformers", model="not/downloaded-model"))
    p.save()
    from wider.embeddings import sentence_transformers as st_mod
    monkeypatch.setattr(st_mod.SentenceTransformersEmbedder, "is_downloaded", lambda self: False)
    assert c.post("/api/jobs/embed?embedding=st").json()["skipped"] is True


def test_job_projection(screen_project, monkeypatch):  # noqa: F811
    """A running screening job's usage carries the cost so far and the projected range, bounded by the pre-run
    estimate stored with the job (the CLI progress lines use the same helper)."""
    from wider.core.backends import update_provider

    p = screen_project
    update_provider(p, "fake", price_input_per_mtok=1000.0, price_output_per_mtok=2000.0, extra={"delay_s": "0.05"})
    orig = fakemod.FakeProvider.__init__
    counter, seen_it = {"calls": 0}, threading.Event()
    inner = _responder(counter)

    def held(req):   # later calls wait until the test has read the projection, so the job cannot finish first
        if counter["calls"] >= 2:
            seen_it.wait(20)
        return inner(req)
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, held))
    c = TestClient(create_app(p))
    jid = c.post("/api/jobs/screen", json={"scope": "sample:development", "pass_name": "Q", "provider": "fake"}).json()["job_id"]
    seen = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < 20:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        if (j.get("usage") or {}).get("projected_min_usd") is not None:
            seen = j
            break
        time.sleep(0.02)
    seen_it.set()
    assert seen is not None, "no projection while the job ran"
    est, u = seen["params"]["estimate"], seen["usage"]
    assert est["priced"] and est["calls"] > 0 and est["min_usd"] <= est["max_usd"]
    assert u["cost_so_far_usd"] <= u["projected_min_usd"] <= u["projected_max_usd"]
    done = _wait(c, jid)
    assert done["status"] == "done" and "projected_min_usd" not in done["usage"]   # only while running
    assert "cost so far" in c.get("/static/jobs.js").text and "projected" in c.get("/static/jobs.js").text


def test_static_job_bar(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    html = c.get("/").text
    for needle in ['id="job-bar"', 'id="toasts"', "/static/jobs.js", 'class="run-select']:
        assert needle in html, needle
    js = c.get("/static/jobs.js").text
    assert "/api/jobs?active=true" in js and "Notification" in js and "/cancel" in js
    app_js = c.get("/static/app.js").text
    for route in ["/api/jobs/screen", "/api/jobs/embed", "/api/jobs/enrich", "/api/jobs/propose/"]:
        assert route in app_js, route


def test_rank_page_routes_regression(screen_project):  # noqa: F811
    """The ranking page reads GET /api/rank (explorer + corpus overview) and protocol.ranking.top_n_cells from
    GET /api/project; both broke silently before (wrong import; a config property that is not serialised)."""
    c = TestClient(create_app(screen_project))
    r = c.get("/api/rank")
    n = screen_project.store.scalar("SELECT COUNT(*) FROM records")
    assert r.status_code == 200 and r.json()["rows"] and n > 0 and r.json()["corpus"]["records"] == n
    assert c.get("/api/project").json()["config"]["protocol"]["ranking"]["top_n_cells"] >= 1
    js = c.get("/static/app.js").text
    assert "cfg.thresholds." not in js


# ------------------------------------------------------------------ C2 (llm-backend-choice) section 7
def _v3_store(db):
    """A store as version 3 wrote it: no cache columns, no backend columns on screening calls and decisions."""
    import sqlite3

    from wider.core.store import Store

    Store(db).close()
    con = sqlite3.connect(db)
    for table, cols in {"llm_usage": ["cached_tokens", "cache_write_tokens"],
                        "screen_calls": ["backend_hash", "provider", "model", "model_reported"],
                        "screen_decisions": ["vote_calls", "decided_by"]}.items():
        for c in cols:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {c}")
    con.execute("INSERT INTO llm_usage(at,stage,run_ref,provider,kind,model,locality,ok,prompt_tokens,completion_tokens,"
                "cost_usd) VALUES('t','screen','r1','f','fake','f','test',1,5,5,0)")
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.commit()
    con.close()


def test_migrate_v4_adds_cache_columns(tmp_path):
    from wider.core.store import SCHEMA_VERSION, Store

    db = tmp_path / "store.sqlite"
    _v3_store(db)
    st = Store(db)
    assert SCHEMA_VERSION >= 4 and st.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert {"cached_tokens", "cache_write_tokens"} <= set(st.columns("llm_usage"))
    assert {"backend_hash", "provider", "model", "model_reported"} <= set(st.columns("screen_calls"))
    assert {"vote_calls", "decided_by"} <= set(st.columns("screen_decisions"))
    row = st.one("SELECT cached_tokens, cache_write_tokens FROM llm_usage")
    assert row["cached_tokens"] is None and row["cache_write_tokens"] is None
    assert {"backend_snapshots", "run_backend_changes"} <= set(st.tables())
    st.close()


def test_migrate_v4_is_idempotent(tmp_path):
    from wider.core.store import Store

    db = tmp_path / "store.sqlite"
    _v3_store(db)
    st = Store(db)
    before = {t: st.columns(t) for t in ("llm_usage", "screen_calls", "screen_decisions")}
    st._migrate_v4()   # a second run (e.g. after a rollback that kept the file) changes nothing and does not fail
    assert {t: st.columns(t) for t in before} == before
    assert st.scalar("SELECT COUNT(*) FROM llm_usage") == 1
    st.close()
    Store(db).close()


def test_cached_tokens_counted(tmp_path):
    from wider.providers.anthropic_sdk import usage_anthropic

    p = Project.init(tmp_path / "r")
    cfg = ProviderConfig(name="a", kind="anthropic", model="m")
    from wider.core.usage import record_call
    with track(p, "screen", "run-c"):
        record_call(cfg, usage_anthropic({"input_tokens": 100, "cache_read_input_tokens": 2000,
                                          "cache_creation_input_tokens": 300, "output_tokens": 50}), True)
        record_call(cfg, usage_anthropic({"input_tokens": 100, "output_tokens": 50}), True)
    rows = p.store.query("SELECT prompt_tokens, cached_tokens, cache_write_tokens, completion_tokens FROM llm_usage "
                         "ORDER BY usage_id")
    assert tuple(rows[0]) == (2400, 2000, 300, 50)
    assert rows[1]["prompt_tokens"] == 100 and rows[1]["cached_tokens"] is None and rows[1]["cache_write_tokens"] is None


def test_cached_price_rules():
    from wider.core.usage import normalised

    u = normalised(input_uncached=300_000, input_cached=600_000, input_cache_write=100_000, output=0)
    priced = ProviderConfig(name="p", kind="anthropic", price_input_per_mtok=3.0, price_cached_input_per_mtok=0.3,
                            price_cache_write_per_mtok=3.75)
    assert call_cost(priced, u) == pytest.approx(0.9 + 0.18 + 0.375)
    plain = ProviderConfig(name="q", kind="anthropic", price_input_per_mtok=3.0)
    assert call_cost(plain, u) == pytest.approx(3.0)   # cached and cache-write default to the input price


def test_remote_self_hosted_pricing(tmp_path):
    from wider.core.detect import destination
    from wider.core.summary import provider_locality
    from wider.core.usage import normalised

    u = normalised(input_uncached=1_000_000, output=0)
    priced = ProviderConfig(name="v1", kind="openai_compat", base_url="https://gpu.example.test/v1", price_input_per_mtok=1.0)
    unpriced = ProviderConfig(name="v2", kind="openai_compat", base_url="https://gpu.example.test/v1")
    assert call_cost(priced, u) == pytest.approx(1.0)
    assert call_cost(unpriced, u) is None
    assert provider_locality(unpriced.kind, unpriced.base_url) == "cloud" and destination(unpriced) == "gpu.example.test"
    # the totals and the info line name it "not priced", never "paid"
    p = Project.init(tmp_path / "r")
    from wider.core.usage import record_call
    with track(p, "screen", "run-v"):
        record_call(unpriced, u, True)
    from wider.core.summary import cost_text, project_summary
    txt = cost_text(project_summary(p)["cost"])
    assert "without prices" in txt and "paid" not in txt


def test_embedding_cost_rules():
    from wider.core.models import EmbeddingConfig
    from wider.core.usage import embedding_call_cost

    u = {"prompt_tokens": 1_000_000}
    assert embedding_call_cost(EmbeddingConfig(name="e", kind="openai", model="m", price_input_per_mtok=0.02), u) \
        == pytest.approx(0.02)
    assert embedding_call_cost(EmbeddingConfig(name="e2", kind="openai", model="m"), u) is None
    assert embedding_call_cost(EmbeddingConfig(name="e3", kind="ollama", model="m"), u) == 0.0
    assert embedding_call_cost(EmbeddingConfig(name="e4", kind="openai_compat", model="m",
                                               base_url="http://192.168.1.20:1234/v1"), u) is None  # network: not free


def test_embedding_price_via_api_and_cli(tmp_path):
    p = Project.init(tmp_path / "r")
    c = TestClient(create_app(p))
    r = c.post("/api/embeddings", json={"name": "e1", "kind": "openai", "model": "m", "api_key_env": "X_KEY",
                                        "price_input_per_mtok": 0.02})
    assert r.status_code == 200, r.text
    r = runner.invoke(cli_app, ["embed", "add", "--name", "e2", "--kind", "openai", "--model", "m",
                                "--price-in", "0.02", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    assert q.embedding("e1").price_input_per_mtok == 0.02 and q.embedding("e2").price_input_per_mtok == 0.02


def test_cache_use_after_run(tmp_path):
    from wider.core.stage_result import format_result
    from wider.core.usage import cache_text, normalised, record_call

    p = Project.init(tmp_path / "r")
    cfg = ProviderConfig(name="a", kind="anthropic", model="m", price_input_per_mtok=3.0, price_output_per_mtok=15.0)
    with track(p, "screen", "run-1"):
        record_call(cfg, normalised(input_uncached=1000, input_cached=2000, output=10), True)
    with track(p, "screen", "run-2"):
        record_call(cfg, normalised(input_uncached=1000, output=10), True)
    u1, u2 = usage_for_runs(p, ["run-1"]), usage_for_runs(p, ["run-2"])
    assert u1["cached_tokens"] == 2000 and cache_text(u1) == "2,000 cached tokens" and u1["cost_known"]
    assert u2["cached_tokens"] is None and cache_text(u2) == "cache use not reported" and u2["cost_known"]
    line1 = format_result({"headline": "h", "usage": u1})
    line2 = format_result({"headline": "h", "usage": u2})
    assert "$" in line1 and "2,000 cached tokens" in line1
    assert "$" in line2 and "cache use not reported" in line2
    r = runner.invoke(cli_app, ["costs", "--by", "run", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "2,000" in r.output and "not reported" in r.output


def test_usage_normalised_across_vendors():
    from wider.providers.anthropic_sdk import usage_anthropic
    from wider.providers.cli_agent import _parse_claude as parse_claude_json
    from wider.providers.openai_compat import usage_openai

    a = usage_anthropic({"input_tokens": 100, "cache_read_input_tokens": 50, "output_tokens": 20})
    o = usage_openai({"prompt_tokens": 150, "prompt_tokens_details": {"cached_tokens": 50}, "completion_tokens": 20})
    strip = lambda u: {k: v for k, v in u.items() if k != "raw"}  # noqa: E731
    assert strip(a) == strip(o) == {"input_uncached": 100, "input_cached": 50, "input_cache_write": None, "output": 20}
    cfg = ProviderConfig(name="p", kind="anthropic", price_input_per_mtok=3.0, price_output_per_mtok=15.0)
    assert call_cost(cfg, a) == call_cost(cfg, o)
    env = json.dumps({"type": "result", "result": '{"ok": true}', "total_cost_usd": 0.0123,
                      "usage": {"input_tokens": 10, "output_tokens": 2}})
    _text, u, _meta = parse_claude_json(env)
    assert u["total_cost_usd"] == pytest.approx(0.0123)
    assert call_cost(ProviderConfig(name="c", kind="cli_agent", command=["x"]), u) == pytest.approx(0.0123)


def test_usage_row_takes_ollama_routing(tmp_path, monkeypatch):
    import httpx

    from wider.core.detect import effective_locality

    p = Project.init(tmp_path / "r")
    cfg = ProviderConfig(name="o", kind="ollama", model="m3:7b-cloud", base_url="http://127.0.0.1:11434")
    loc, src, _dest = effective_locality(cfg)   # tag list unreachable in tests: the name suffix decides
    assert (loc, src) == ("cloud", "name_suffix")
    real = httpx.AsyncClient
    monkeypatch.setattr("wider.providers.ollama.httpx.AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"model": "m3:7b-cloud", "message": {"content": '{"a": "x"}'},
                                              "prompt_eval_count": 9, "eval_count": 3})), **kw))
    prov = make_provider(cfg, (loc, src))
    req = ChatRequest(system="s", user="u", schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
    with track(p, "screen", "run-o"):
        assert asyncio.run(prov.complete(req)).ok
    row = p.store.one("SELECT locality, cost_usd FROM llm_usage")
    assert row["locality"] == "cloud" and row["cost_usd"] is None   # never priced as a free local call


def test_network_cost_not_assumed():
    from wider.core.usage import normalised

    lan = ProviderConfig(name="n", kind="openai_compat", base_url="http://192.168.1.20:8000/v1")
    assert call_cost(lan, normalised(input_uncached=1000, output=10)) is None
    assert call_cost(lan, normalised(input_uncached=1000, output=10), "network") is None
    assert call_cost(lan, {**normalised(input_uncached=1000), "total_cost_usd": 0.5}) == 0.5
    priced = lan.model_copy(update={"price_input_per_mtok": 1.0})
    assert call_cost(priced, normalised(input_uncached=1_000_000)) == pytest.approx(1.0)


def _fake_counted(monkeypatch):
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    return counter


def _model(p, model):
    next(x for x in p.config.providers if x.name == "fake").model = model
    p.save()


def _run_folders(p):
    return sorted((p.root / "runs").rglob("run_spec.json"))


def test_screen_job_backend_change_409(screen_project, monkeypatch):  # noqa: F811
    """Decision 16: an unfinished run whose backend changed answers 409 backend_change before a job starts."""
    p = screen_project
    counter = _fake_counted(monkeypatch)
    first = screen(p, "corpus", pass_name="A", limit=3)
    _model(p, "fake-2")
    n, folders = counter["calls"], _run_folders(p)
    c = TestClient(create_app(Project.find(p.root)))
    for path in ("/api/jobs/screen", "/api/screen/run"):
        r = c.post(path, json={"scope": "corpus", "pass_name": "A"})
        assert r.status_code == 409, r.text
        ch = r.json()["backend_change"]
        assert ch["run_id"] == first["run_id"] and ch["case"] == "a" and ch["done"] == 3
        assert ch["from"]["model"] == "fake" and ch["to"]["model"] == "fake-2" and "model" in ch["changed"]
    assert p.store.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert counter["calls"] == n and _run_folders(p) == folders
    assert p.store.scalar("SELECT COUNT(*) FROM run_backend_changes") == 0
    # with the choice the job starts and records the change
    r = c.post("/api/jobs/screen", json={"scope": "corpus", "pass_name": "A", "backend_change": "continue"})
    assert r.status_code == 200, r.text
    assert _wait(c, r.json()["job_id"])["status"] == "done"
    row = p.store.one("SELECT * FROM run_backend_changes")
    assert row["run_id"] == first["run_id"] and row["confirmed_via"] == "ui" and row["to_model"] == "fake-2"


def test_run_folder_has_snapshot(screen_project, monkeypatch):  # noqa: F811
    """A run folder holds one backend_<hash12>.json per backend it used, and backend_changes.jsonl after a change."""
    p = screen_project
    _fake_counted(monkeypatch)
    done = screen(p, "sample:development", pass_name="A")
    d1 = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (done["run_id"],))["dir"]
    snaps = sorted(d1.glob("backend_*.json"))
    assert len(snaps) == 1 and not (d1 / "backend_changes.jsonl").exists()
    h = p.store.scalar("SELECT DISTINCT backend_hash FROM screen_calls WHERE run_id=?", (done["run_id"],))
    assert snaps[0].name == f"backend_{h[:12]}.json"
    assert json.loads(snaps[0].read_text(encoding="utf-8"))["model"] == "fake"
    part = screen(p, "corpus", pass_name="B", limit=3)
    _model(p, "fake-2")
    screen(Project.find(p.root), "corpus", pass_name="B", backend_change="continue")
    d2 = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (part["run_id"],))["dir"]
    assert sorted(json.loads(f.read_text(encoding="utf-8"))["model"] for f in d2.glob("backend_*.json")) == ["fake", "fake-2"]
    lines = (d2 / "backend_changes.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(lines[0])
    assert len(lines) == 1 and line["from"]["model"] == "fake" and line["to"]["model"] == "fake-2"
    assert len(sorted(d1.glob("backend_*.json"))) == 1   # the first folder is untouched


def test_screen_job_refusals_start_nothing(screen_project, monkeypatch):  # noqa: F811
    """409 new_set_needed and scope_needed, 403 consent and 200 already_done: no job row, no call, no run folder,
    on both the job route and the synchronous route."""
    p = screen_project
    counter = _fake_counted(monkeypatch)
    run_protocol(p, "sample:development", "main")
    plain = screen(p, "sample:development", pass_name="P")
    p.config.providers.append(ProviderConfig(name="cloud", kind="openai_compat", model="m",
                                             base_url="https://llm.example.invalid/v1", api_key_env="WIDER_TEST_KEY"))
    _model(p, "fake-2")
    monkeypatch.setenv("WIDER_TEST_KEY", "synthetic-not-a-key")
    n, folders = counter["calls"], _run_folders(p)
    c = TestClient(create_app(Project.find(p.root)))
    proto = {"scope": "sample:development", "mode": "protocol", "protocol": "main"}
    for path in ("/api/jobs/screen", "/api/screen/run"):
        r = c.post(path, json=proto)
        assert r.status_code == 409 and r.json()["new_set_needed"]["set"] == 2, r.text
        assert "model" in r.json()["new_set_needed"]["changed"]
        r = c.post(path, json={"scope": "sample:development", "provider": "cloud", "pass_name": "Q"})
        assert r.status_code == 403 and r.json()["detail"]["backend_consent_required"]["backend"] == "cloud", r.text
        r = c.post(path, json={"scope": "sample:development", "pass_name": "P"})
        assert r.status_code == 200 and r.json()["already_done"] == {"run_id": plain["run_id"], "model": "fake"}, r.text
        assert "job_id" not in r.json()
    p.store.execute("UPDATE screen_result_sets SET scope='unknown' WHERE protocol='main'")
    for path in ("/api/jobs/screen", "/api/screen/run"):
        r = c.post(path, json=proto)
        assert r.status_code == 409 and r.json()["scope_needed"] == {"protocol": "main", "set": 1}, r.text
    assert p.store.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert counter["calls"] == n and _run_folders(p) == folders
