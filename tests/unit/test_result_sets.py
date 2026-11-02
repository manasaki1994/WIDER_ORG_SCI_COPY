"""Result sets of a screening protocol (design Decision 23, recorded decisions 23-35): a rerun with changed method
settings starts a new set only on request, the active set is what every reader uses, evaluations are reused only for
identical inputs, and older stores migrate by scope. Synthetic records and the fake backend only."""
import csv
import hashlib
import json
import re
import sqlite3
import zipfile
from collections import defaultdict

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_result_set_select_node import run_node
from tests.unit.test_runs import _v3_store
from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core import questions as qs
from wider.core.backends import update_provider
from wider.core.models import ProviderConfig
from wider.core.project import Project
from wider.core.provenance import sha256_text
from wider.core.store import SCHEMA_VERSION
from wider.providers import fake as fakemod
from wider.sample.labels import label
from wider.screen import engine as engmod
from wider.screen import result_sets as rs
from wider.screen.engine import _run_id, backend_change, screen
from wider.screen.protocol import run_protocol, second_screen
from wider.ui.app import create_app

runner = CliRunner()


@pytest.fixture
def counted(_project_fixture, monkeypatch):  # noqa: F811
    """The synthetic 40-record project (development and probability samples drawn) with the fake backend `fake`;
    the counter holds the number of model calls made."""
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    return _project_fixture, counter


def cli(p, *args, **kw):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "300"}, **kw)


def client(p):
    return TestClient(create_app(Project.find(p.root)))


def reload(p):
    return Project.find(p.root)


def set_model(p, model, name="fake"):
    next(x for x in p.config.providers if x.name == name).model = model
    p.save()


def set_rows(p, protocol="main"):
    return [dict(r) for r in p.store.query("SELECT * FROM screen_result_sets WHERE protocol=? ORDER BY result_set",
                                           (protocol,))]


def decisions_of(p, key):
    return {r["record_id"]: r["decision"] for r in p.store.query(
        "SELECT record_id, decision FROM screen_decisions WHERE protocol=?", (key,))}


def n_runs(p):
    return p.store.scalar("SELECT COUNT(*) FROM runs WHERE kind IN ('screen', 'second_screen')")


# ====================================================================================================
# Result sets, part A (design Decision 23, recorded decisions 25-34): paid reruns only on request, operational edits
# ====================================================================================================
DEV = "sample:development"


def _jobs(p):
    return p.store.scalar("SELECT COUNT(*) FROM jobs")


def _add_backend(p, name, model):
    p.config.providers.append(ProviderConfig(name=name, kind="fake", model=model))
    p.save()


def _protocol(p, *args):
    return cli(p, "screen", "protocol", *args)


def _set_json(p, protocol="main"):
    r = cli(p, "screen", "sets", "list", protocol, "--json")
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


def _post(p, scope=DEV, **kw):
    return client(p).post("/api/jobs/screen", json={"scope": scope, "mode": "protocol", "protocol": "main", **kw})


def test_changed_settings_need_request(counted):  # noqa: F811
    """Scenario "A changed model alone runs nothing": no call, no set; CLI names the field, cost and --new-set (exit
    0); the route answers 409 new_set_needed before a job; the dialog offers a new set and Cancel."""
    p, c = counted
    set_model(p, "m1")
    assert _protocol(p, DEV, "--name", "main").exit_code == 0
    set_model(p, "m2")
    n, runs = c["calls"], n_runs(p)
    r = _post(p)
    assert r.status_code == 409, r.text
    body = r.json()
    ns = body["new_set_needed"]
    assert ns["changed"] == ["model"] and ns["set"] == 2 and ns["estimate"]["all"]["calls"] > 0
    assert _jobs(p) == 0
    html = run_node(f"process.stdout.write(JSON.stringify(Backends.newSetHtml({json.dumps(ns)}, "
                    f"{json.dumps(body['detail'])})));")
    assert "Screen again as a new result set" in html and 'data-act="cancel">Cancel<' in html
    out = _protocol(p, DEV, "--name", "main")
    assert "differ in: model" in out.output and "Estimated cost:" in out.output and "--new-set" in out.output
    assert c["calls"] == n and n_runs(p) == runs and len(set_rows(p)) == 1
    assert out.exit_code == 0, out.output


def test_price_edit_keeps_set(counted):  # noqa: F811
    """Scenario "Operational edits keep the set": prices, timeout and concurrency edits make no call and no set and
    leave set 1's logged costs; an extra.body, port or CLI argv edit is a method change (refused, new set needed)."""
    p, c = counted
    update_provider(p, "fake", base_url="http://127.0.0.1:8001/v1", command=["tool", "-p", "{prompt}"],
                    price_input_per_mtok=1.0, price_output_per_mtok=2.0)
    p = reload(p)
    first = run_protocol(p, DEV, "main")
    cost = p.store.scalar("SELECT SUM(cost_usd) FROM llm_usage")
    assert cost and cost > 0
    update_provider(p, "fake", price_input_per_mtok=9.0, price_output_per_mtok=30.0, timeout_s=17, max_concurrency=1)
    p = reload(p)
    n, runs = c["calls"], n_runs(p)
    again = run_protocol(p, DEV, "main")
    assert again["set_action"] == "rederive" and again["result_set"] == 1 and again["runs"] == first["runs"]
    assert c["calls"] == n and n_runs(p) == runs and len(set_rows(p)) == 1
    assert p.store.scalar("SELECT SUM(cost_usd) FROM llm_usage") == pytest.approx(cost)
    assert _post(p).status_code == 200 and _jobs(p) == 1   # nothing to refuse: the rederive job starts
    for change, field in ((dict(extra={"body": {"top_p": 0.5}}), "extra.body"),
                          (dict(base_url="http://127.0.0.1:8002/v1"), "base_url"),
                          (dict(command=["tool", "-p", "{prompt}", "--fast"]), "command")):
        p = reload(p)
        old = p.provider("fake").model_dump()
        update_provider(p, "fake", **change)
        p = reload(p)
        with pytest.raises(rs.NewSetNeeded) as e:
            run_protocol(p, DEV, "main")
        # a first `extra` key is named `extra` (the identity had none), a later one `extra.body`
        assert any(field.startswith(x) for x in e.value.plan["changed"]), (field, e.value.plan["changed"])
        assert c["calls"] == n and len(set_rows(p)) == 1
        p.config.providers = [ProviderConfig(**old) if x.name == "fake" else x for x in p.config.providers]
        p.save()


def test_rerun_other_model_new_set(counted):  # noqa: F811
    """Scenario "A new model makes a new set": set 1 (m1, active) and set 2 (m2, "model m2", screening) with counts,
    runs, author and time; set 1's decisions unchanged; recall and the export still read set 1."""
    p, c = counted
    set_model(p, "m1")
    _add_backend(p, "b", "m2")
    assert _protocol(p, "corpus", "--name", "main").exit_code == 0
    before = decisions_of(p, "main")
    r = _protocol(p, "corpus", "--name", "main", "--provider", "b", "--new-set", "--set-label", "model m2")
    assert r.exit_code == 0, r.output
    s1, s2 = _set_json(p)
    assert (s1["result_set"], s1["models"], s1["active"]) == (1, ["m1"], 1)
    assert (s2["result_set"], s2["models"], s2["active"], s2["label"], s2["source"]) == (2, ["m2"], 0, "model m2",
                                                                                         "screening")
    for s in (s1, s2):
        assert s["include"] + s["maybe"] + s["exclude"] == s["n"] == 40
        assert s["run_ids"] and s["created_by"] and s["created_at"]
    assert all(x.endswith(".s2") for x in s2["run_ids"]) and all(x.endswith(".s1") for x in s1["run_ids"])
    assert decisions_of(p, "main") == before
    exp = cli(p, "screen", "export", "main")
    assert exp.exit_code == 0, exp.output
    path = p.root / "exports" / "screening" / "main_decisions.csv"
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 40 and {r["result_set"] for r in rows} == {"1"}
    sample = p.store.one("SELECT name FROM samples WHERE kind='probability'")["name"]
    _label_all(p, sample)                              # recall waits for every final label
    rc = cli(p, "recall", "main", "--sample", sample, "--bootstrap", "20")
    assert rc.exit_code == 0, rc.output
    assert "protocol main set 1" in " ".join(rc.output.split())


def test_identical_settings_points_to_set(counted):  # noqa: F811
    """Scenario "A set identical to an earlier one": back on m1 after set 2 on m2, a run without --new-set makes no
    call and no set and names set 1, `wider screen sets use main 1` and --new-set."""
    p, c = counted
    set_model(p, "m1")
    assert _protocol(p, DEV, "--name", "main").exit_code == 0
    set_model(p, "m2")
    assert _protocol(p, DEV, "--name", "main", "--new-set").exit_code == 0
    set_model(p, "m1")
    n, runs = c["calls"], n_runs(p)
    r = _protocol(p, DEV, "--name", "main")
    out = " ".join(r.output.split())
    assert c["calls"] == n and n_runs(p) == runs and len(set_rows(p)) == 2
    assert "--new-set" in out
    assert "set 1" in out and "wider screen sets use main 1" in out
    assert r.exit_code == 0


def test_limited_set_continues(counted):  # noqa: F811
    """Design rule 2: a set built with --limit is unfinished whatever the limit, and the next run continues it (no
    new set); once every record of the scope is decided the next run re-derives without a call."""
    p, c = counted
    ids = rs._scope_ids(p, DEV)
    first = run_protocol(p, DEV, "main", limit=3)
    assert first["set_action"] == "create" and first["n"] == 3
    (row,) = rs.sets(p, "main")
    assert row["finished"] is False
    again = run_protocol(p, DEV, "main", limit=3)   # "finished" ignores --limit: still unfinished, continued
    assert again["set_action"] == "continue" and again["result_set"] == 1 and again["runs"] == first["runs"]
    rest = run_protocol(p, DEV, "main")
    assert rest["set_action"] == "continue" and rest["runs"] == first["runs"] and rest["n"] == len(ids)
    assert len(set_rows(p)) == 1 and rs.sets(p, "main")[0]["finished"] is True
    n = c["calls"]
    assert run_protocol(p, DEV, "main")["set_action"] == "rederive" and c["calls"] == n


def test_new_set_keeps_protocol_list(counted):  # noqa: F811
    """Scenario "A second set screens in its own runs, and the protocol list is unchanged": `wider screen protocols`,
    /api/screen and the report's default protocol show `main` once, never `main#2`."""
    from wider.recall.outputs import _default_protocol

    p, _c = counted
    set_model(p, "m1")
    assert _protocol(p, DEV, "--name", "main").exit_code == 0
    set_model(p, "m2")
    assert _protocol(p, DEV, "--name", "main", "--new-set").exit_code == 0
    assert [r["result_set"] for r in set_rows(p)] == [1, 2] and decisions_of(p, "main#2")
    r = cli(p, "screen", "protocols")
    assert r.exit_code == 0 and "main#2" not in r.output
    assert [ln.split()[1] for ln in r.output.splitlines() if "main" in ln] == ["main"]
    api = client(p).get("/api/screen").json()["protocols"]
    assert [x["protocol"] for x in api] == ["main"] and "main#2" not in json.dumps(api)
    d = _default_protocol(p, None)
    assert d["protocol"] == "main" and d["key"] == "main" and d["result_set"] == 1


def test_new_scope_needs_request(counted):  # noqa: F811
    """Scenario "From the development sample to the corpus": with unchanged settings a corpus run makes no call; the
    CLI says main has no set for the corpus and names how many of set 1's decisions could be reused."""
    p, c = counted
    assert _protocol(p, DEV, "--name", "main").exit_code == 0
    reusable = len(decisions_of(p, "main"))
    n, runs = c["calls"], n_runs(p)
    r = _protocol(p, "corpus", "--name", "main")
    out = " ".join(r.output.split())
    assert c["calls"] == n and n_runs(p) == runs and len(set_rows(p)) == 1
    assert "main has no set for corpus" in out and "set 1 screens sample:development (development)" in out
    assert f"--new-set --reuse-from 1 (reuse {reusable} decisions)" in out and "--new-set --no-reuse" in out
    ns = _post(p, scope="corpus").json()["new_set_needed"]
    assert ns["reusable"]["set"] == 1 and ns["reusable"]["count"] == reusable and ns["to_screen"] == 40 - reusable
    assert _jobs(p) == 0 and set_rows(p)[0]["role"] == "development"


def test_continue_marks_set_mixed(counted):  # noqa: F811
    """Scenario "A continued set after a model change is marked mixed": set 1 keeps its first settings, is listed as
    mixed, and a later run with those settings is not told "same settings as set 1"."""
    p, c = counted
    set_model(p, "m1")
    run_protocol(p, DEV, "main", limit=3)
    settings = set_rows(p)[0]["settings"]
    set_model(p, "m2")
    r = _protocol(p, DEV, "--name", "main", "--backend-change", "continue")
    assert r.exit_code == 0, r.output
    (row,) = set_rows(p)
    assert row["settings"] == settings and json.loads(settings)["backend"]["model"] == "m1"
    (s1,) = _set_json(p)
    assert s1["mixed"] == 1 and s1["finished"] is True and s1["models"] == ["m1", "m2"]
    set_model(p, "m1")
    r = _protocol(p, DEV, "--name", "main", "--new-set")
    assert r.exit_code == 0, r.output
    assert "same settings as set 1" not in r.output and "same_as" not in r.output
    assert [x["result_set"] for x in set_rows(p)] == [1, 2]


def test_plain_run_already_done_parity(counted):  # noqa: F811
    """Scenario "A plain run already done": after a model edit the CLI prints "already done under model m1" (exit 0)
    and the route answers 200 already_done with the run id and m1; no job, no call."""
    p, c = counted
    set_model(p, "m1")
    first = screen(p, DEV, pass_name="A")
    set_model(p, "m2")
    n, runs = c["calls"], n_runs(p)
    r = cli(p, "screen", "run", DEV)
    assert r.exit_code == 0, r.output
    assert "already done under model m1" in r.output
    res = client(p).post("/api/jobs/screen", json={"scope": DEV, "pass_name": "A"})
    assert res.status_code == 200, res.text
    assert res.json()["already_done"] == {"run_id": first["run_id"], "model": "m1"} and "job_id" not in res.json()
    assert c["calls"] == n and n_runs(p) == runs and _jobs(p) == 0


def test_backend_change_case_b_same_set(counted):  # noqa: F811
    """Decision 16 case (b) for protocols: an unfinished run under another backend name is offered only for the same
    result set; a new set on that backend starts its own runs and leaves set 1's run as it was."""
    p, c = counted
    first = run_protocol(p, DEV, "main", limit=3)
    run_a = first["runs"][0]
    _add_backend(p, "b", "m2")
    ch = backend_change(p, "screen", DEV, "A", None, provider_name="b", protocol="main", result_set=1)
    assert ch["case"] == "b" and ch["run_id"] == run_a and ch["done"] == 3 and ch["to"]["provider"] == "b"
    assert backend_change(p, "screen", DEV, "A", None, provider_name="b", protocol="main", result_set=2) is None
    assert backend_change(p, "screen", DEV, "A", None, provider_name="b", protocol="other", result_set=1) is None
    assert backend_change(p, "screen", DEV, "A", None, provider_name="b") is None   # a plain run: not a protocol run
    calls_a = p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (run_a,))
    second = run_protocol(p, DEV, "main", provider_name="b", new_set=True)
    assert second["result_set"] == 2 and all(r.endswith("__main.s2") and "__b__" in r for r in second["runs"])
    assert "backend_change" not in second
    assert p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (run_a,)) == calls_a
    assert not p.store.scalar("SELECT COUNT(*) FROM run_backend_changes")


_BASE = {"name": "x", "kind": "openai_compat", "model": "m", "base_url": "http://127.0.0.1:8000/v1",
         "api_key_env": "WIDER_TEST_KEY", "command": ["tool", "-p", "{prompt}"], "temperature": 0.0, "seed": 1,
         "max_concurrency": 4, "timeout_s": 300, "price_input_per_mtok": 1.0, "price_output_per_mtok": 2.0,
         "price_cached_input_per_mtok": 0.1, "price_cache_write_per_mtok": 1.5, "preset": "p",
         "extra": {"headers": {"Authorization": "{env:WIDER_TEST_KEY}", "X-Team": "a"}, "env": {"HOME_X": "{env:A}"}}}
_OPERATIONAL = [("name", "y"), ("price_input_per_mtok", 3.0), ("price_output_per_mtok", 5.0),
                ("price_cached_input_per_mtok", 0.2), ("price_cache_write_per_mtok", 2.0), ("timeout_s", 9),
                ("max_concurrency", 1), ("api_key_env", "OTHER_KEY"), ("preset", "q"),
                ("extra.headers.Authorization", "{env:OTHER_KEY}"), ("label", "lbl"), ("version", "2.0.1"),
                ("executable", "/opt/tool/bin/tool")]
_METHOD = [("kind", "ollama"), ("model", "m2"), ("base_url", "http://127.0.0.1:8001/v1"),
           ("base_url", "https://127.0.0.1:8000/v1"), ("base_url", "http://127.0.0.1:8000/v2"),
           ("base_url", "http://10.0.0.2:8000/v1"), ("command", ["tool", "-p", "{prompt}", "--x"]),
           ("temperature", 0.3), ("seed", 2), ("repairs", 2), ("extra.body", {"top_p": 0.5}),
           ("extra.options", {"num_ctx": 8}),
           ("extra.strict", True), ("extra.deployment", "d2"), ("extra.api_version", "2025-01"),
           ("extra.strict_schema", True), ("extra.json_mode", True), ("extra.think", False),
           ("extra.omit_params", ["seed"]), ("extra.max_tokens_param", "max_completion_tokens"),
           ("extra.max_output_tokens", 4000), ("extra.anything_new", 1), ("extra.env.HOME_X", "{env:B}"),
           ("extra.headers.X-Team", "b"), ("extra.headers.X-New", "c"), ("system_channel", "argv")]


def _with(field, value):
    d = json.loads(json.dumps(_BASE))
    if field == "system_channel":
        d["kind"] = "cli_agent"
    node, *path = field.split(".")
    if not path:
        d[node] = value
        return d
    cur = d.setdefault(node, {})
    for k in path[:-1]:
        cur = cur.setdefault(k, {})
    cur[path[-1]] = value
    return d


@pytest.mark.parametrize("field,value", _OPERATIONAL + _METHOD,
                         ids=[f"{'op' if i < len(_OPERATIONAL) else 'method'}-{f}-{i}"
                              for i, (f, _v) in enumerate(_OPERATIONAL + _METHOD)])
def test_method_identity_drops_only_operational(field, value):
    """Decision 23 "Method identity": an operational input (prices, timeout, concurrency, key variable, {env:} header
    value, preset, label, CLI version and path) leaves it equal; every method input changes it."""
    assert set(ProviderConfig.model_fields) <= rs.OPERATIONAL_FIELDS | rs.METHOD_FIELDS, \
        "a ProviderConfig field is in neither the operational nor the method list"
    base = _with("system_channel", "file") if field == "system_channel" else _BASE
    same = rs.method_identity(base) == rs.method_identity(_with(field, value))
    assert same is ((field, value) in _OPERATIONAL), (field, rs.method_identity(_with(field, value)))


def test_protocols_order_over_sets(counted):  # noqa: F811
    """Scenario "Latest protocol over result sets": once `zeta` gains set 2 screened after `alpha`, `wider report`
    without --protocol uses `zeta` with its active set (set 1)."""
    from wider.recall.outputs import _default_protocol

    p, _c = counted
    set_model(p, "m1")
    run_protocol(p, DEV, "zeta")
    run_protocol(p, DEV, "alpha")
    assert _default_protocol(p, None)["protocol"] == "alpha"
    set_model(p, "m2")
    run_protocol(p, DEV, "zeta", new_set=True)
    r = cli(p, "report")
    assert r.exit_code == 0, r.output
    rep = json.loads((p.root / "exports" / "report.json").read_text(encoding="utf-8"))
    blob = json.dumps(rep)
    assert '"zeta"' in blob and rs.active_key(p, "zeta") == "zeta"
    d = _default_protocol(p, None)
    assert (d["protocol"], d["key"], d["result_set"]) == ("zeta", "zeta", 1)


# ====================================================================================================
# Result sets, part B (task 9.18): second-screen links, split answers per set, imports and derived sets, development
# ====================================================================================================
_ORIG_INIT = fakemod.FakeProvider.__init__


def _ok(r):
    assert r.exit_code == 0, r.output
    return r


def _label_all(p, sample="validation", reviewer="rev1"):
    """Every sampled record of ``sample`` labelled in the blind queue (include when the title is about voice), so
    recall on it can be revealed (label-completion-and-sealing D1)."""
    for r in p.store.query("SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                           "JOIN records m ON m.record_id=sr.record_id WHERE s.name=?", (sample,)):
        label(p, sample, r["record_id"], reviewer, "include" if "voice" in r["title"] else "exclude")


def _n_recall_results(p):
    return p.store.scalar("SELECT COUNT(*) FROM stage_results WHERE stage='recall'")


def _set_records(p, key):
    """Distinct records the backend was asked about in the runs of set ``key``."""
    ids = rs.run_ids(rs.row_by_key(p, key))
    q = ",".join("?" * len(ids))
    return {r["record_id"] for r in p.store.query(
        f"SELECT DISTINCT record_id FROM screen_calls WHERE run_id IN ({q}) AND COALESCE(provider,'')<>'researcher'",
        ids)}


def _maybe_backend(monkeypatch, c):
    """The counted fake backend, but every read of a voice record is maybe (so the second screen has work)."""
    base = _responder(c)

    def respond(req):
        out = base(req)
        return {**out, "decision": "maybe"} if out["decision"] == "include" else out
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None, _o=_ORIG_INIT: _o(self, cfg, respond))


def _two_sets_and_second(p, c, monkeypatch):
    """`main` set 1 (default model) and set 2 (m2) on the development sample, with maybes; `main_second` set 1 from
    `main` set 1."""
    _maybe_backend(monkeypatch, c)
    _ok(cli(p, "screen", "protocol", DEV))
    set_model(p, "m2")
    _ok(cli(p, "screen", "protocol", DEV, "--new-set", "--set-label", "model m2"))
    _ok(cli(p, "screen", "second", "main", "--from-set", "1"))


def test_second_set_links_source(counted, monkeypatch):  # noqa: F811
    """Scenario "Second-screen sets keep their source" (llm-screening)."""
    p, c = counted
    _two_sets_and_second(p, c, monkeypatch)
    row = set_rows(p, "main_second")[0]
    assert (row["source_protocol"], row["source_set"], row["source"]) == ("main", 1, "second")
    out = _ok(cli(p, "screen", "export", "main_second"))
    path = p.root / "exports" / "screening" / "main_second_decisions.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows and {r["source_set"] for r in rows} == {"main#1"}, out.output
    _ok(cli(p, "screen", "sets", "use", "main", "2"))
    assert rs.active_set(p, "main")["result_set"] == 2
    after = set_rows(p, "main_second")[0]
    assert (after["source_protocol"], after["source_set"]) == ("main", 1)
    before = _n_recall_results(p)
    rec = cli(p, "recall", "main_second")
    assert rec.exit_code == 1 and "out of step" in rec.output and "main set 1" in rec.output
    assert _n_recall_results(p) == before
    _ok(cli(p, "screen", "sets", "use", "main_second", "1", "--with-source"))
    assert rs.active_set(p, "main")["result_set"] == 1 and rs.active_set(p, "main_second")["result_set"] == 1
    _label_all(p)
    _ok(cli(p, "recall", "main_second"))


def test_incompatible_combination_refused(counted, monkeypatch):  # noqa: F811
    """Scenarios "Unknown set and out-of-step second screen" (recall-and-outputs) and "Second-screen sets keep their
    source" (llm-screening): refusals print the message and the fix, exit 1, record no stage result; the API 404s."""
    p, c = counted
    _two_sets_and_second(p, c, monkeypatch)
    _ok(cli(p, "screen", "sets", "use", "main", "2"))
    before = _n_recall_results(p)
    nine = cli(p, "recall", "main", "--set", "9")
    step = cli(p, "recall", "main_second")
    assert nine.exit_code == 1 and step.exit_code == 1
    assert "Traceback" not in nine.output + step.output
    assert "out of step" in step.output and "main set 1" in step.output
    assert _n_recall_results(p) == before
    with client(p) as cl:
        a = cl.get("/api/recall", params={"protocol": "main", "result_set": 9})
        b = cl.get("/api/recall", params={"protocol": "main_second"})
    assert b.status_code == 404 and "out of step" in b.json()["detail"]
    assert _n_recall_results(reload(p)) == before
    # the specified wording (failing today)
    assert "wider screen sets use main_second 1 --with-source" in " ".join(step.output.split())
    assert "no result set 9 of 'main'" in nine.output
    assert a.status_code == 404 and "no result set 9 of 'main'" in a.json()["detail"]


def test_second_rerun_needs_request(counted, monkeypatch):  # noqa: F811
    """Scenario "A second rerun with another model needs a request" (llm-screening)."""
    p, c = counted
    _maybe_backend(monkeypatch, c)
    _ok(cli(p, "screen", "protocol", DEV))
    _ok(cli(p, "screen", "second", "main"))
    assert "maybe" in decisions_of(p, "main").values() and c["calls"] > 0
    s1 = set_rows(p, "main_second")[0]
    d1 = decisions_of(p, "main_second")
    set_model(p, "m2")
    calls = c["calls"]
    r = cli(p, "screen", "second", "main")
    assert c["calls"] == calls and len(set_rows(p, "main_second")) == 1
    assert "--new-set" in r.output and "model" in r.output
    _ok(cli(p, "screen", "second", "main", "--new-set"))
    assert c["calls"] > calls
    rows = set_rows(p, "main_second")
    assert [x["result_set"] for x in rows] == [1, 2]
    assert (rows[1]["source_protocol"], rows[1]["source_set"]) == ("main", 1) == (s1["source_protocol"], s1["source_set"])
    assert rows[0] == s1 and decisions_of(p, "main_second") == d1
    assert rows[1]["active"] == 0 and rows[0]["active"] == 1


def _split_backend(monkeypatch):
    """Every record gets include, maybe, exclude in its three reads (per record, cycling across sets)."""
    seen = defaultdict(int)
    order = ["include", "maybe", "exclude"]
    base = _responder({"calls": 0})

    def respond(req):
        d = order[seen[req.user] % 3]
        seen[req.user] += 1
        return {**base(req), "decision": d}
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None, _o=_ORIG_INIT: _o(self, cfg, respond))
    monkeypatch.setattr(engmod, "apply_rule", lambda parsed, text, spec, thin=False, quote_min_chars=8:
                        {"raw": parsed["decision"], "final": parsed["decision"], "flags": []})


def _splits(p, key):
    return {q["record_id"]: q for q in qs.list_questions(p) if q["kind"] == "screen_split"
            and q["context"]["protocol"] == key}


def _votes(p, key, rid):
    return json.loads(p.store.scalar("SELECT votes FROM screen_decisions WHERE protocol=? AND record_id=?", (key, rid)))


def test_split_answer_stays_in_its_set(counted, monkeypatch):  # noqa: F811
    """Scenario "A split answer stays in its set" (doubt-handling)."""
    p, _c = counted
    _split_backend(monkeypatch)
    _ok(cli(p, "screen", "protocol", DEV))
    set_model(p, "m2")
    _ok(cli(p, "screen", "protocol", DEV, "--new-set"))
    q2 = _splits(p, "main#2")
    assert q2 and set(q2) == set(_splits(p, "main"))
    rid, q = next(iter(q2.items()))
    assert "earlier" not in q["context"]   # set 1 was never answered
    before = decisions_of(p, "main")
    _ok(cli(p, "questions", "answer", str(q["question_id"]), "include", "--by", "ana"))
    assert decisions_of(p, "main#2")[rid] == "include" and "researcher:include" in _votes(p, "main#2", rid)
    assert decisions_of(p, "main") == before and before[rid] == "maybe"
    assert rid in _splits(p, "main")   # set 1's own question stays open


def test_reuse_earlier_answer(counted, monkeypatch):  # noqa: F811
    """Scenario "Reusing an earlier answer" (doubt-handling) and "An earlier split answer is offered, never applied"
    (llm-screening): CLI on one record, the Questions page's API call on another (same synthetic project)."""
    p, _c = counted
    _split_backend(monkeypatch)
    _ok(cli(p, "screen", "protocol", DEV))
    s1q = _splits(p, "main")
    r_cli, r_ui, r_none = sorted(s1q)[:3]
    for rid in (r_cli, r_ui):
        qs.answer(p, s1q[rid]["question_id"], "exclude", by="ana")
    set1 = {rid: (decisions_of(p, "main")[rid], _votes(p, "main", rid)) for rid in (r_cli, r_ui)}
    set_model(p, "m2")
    _ok(cli(p, "screen", "protocol", DEV, "--new-set"))
    q2 = _splits(p, "main#2")
    # offered, never applied
    assert decisions_of(p, "main#2")[r_cli] == "maybe" and decisions_of(p, "main#2")[r_ui] == "maybe"
    qc = q2[r_cli]
    assert qc["context"]["earlier"]["set"] == 1 and qc["context"]["earlier"]["choice"] == "exclude"
    assert any(o["value"] == "reuse" and o["label"].startswith("Reuse this answer") for o in qc["options"])
    # CLI
    _ok(cli(p, "questions", "answer", str(qc["question_id"]), "reuse", "--by", "bob"))
    assert decisions_of(p, "main#2")[r_cli] == "exclude"
    assert f"researcher:reused:set1:{s1q[r_cli]['question_id']}" in _votes(p, "main#2", r_cli)
    h = p.store.one("SELECT author, summary FROM history WHERE kind='answer' ORDER BY change_id DESC LIMIT 1")
    assert h["author"] == "bob" and "reuse" in h["summary"]
    # risk-checker R3: the earlier answer is credited to the person who gave it (ana), not to bob
    reason = p.store.scalar("SELECT reason FROM screen_decisions WHERE protocol=? AND record_id=?", ("main#2", r_cli))
    assert "researcher (bob) reused the answer ana gave in set 1" in reason and "their answer" not in reason
    # the Questions page button sends choice "reuse"
    with client(p) as cl:
        res = cl.post(f"/api/questions/{q2[r_ui]['question_id']}/answer",
                      json={"choice": "reuse", "text": "", "by": "cara"})
        assert res.status_code == 200, res.text
        assert decisions_of(p, "main#2")[r_ui] == "exclude"
        assert f"researcher:reused:set1:{s1q[r_ui]['question_id']}" in _votes(p, "main#2", r_ui)
        assert p.store.scalar("SELECT author FROM history WHERE kind='answer' ORDER BY change_id DESC LIMIT 1") == "cara"
        # reuse without an earlier answer is an invalid answer
        bad = cl.post(f"/api/questions/{q2[r_none]['question_id']}/answer", json={"choice": "reuse", "by": "cara"})
        assert bad.status_code == 400 and "answer must be one of" in bad.json()["detail"]
    r = cli(p, "questions", "answer", str(q2[r_none]["question_id"]), "reuse", "--by", "bob")
    assert r.exit_code == 1 and "answer must be one of" in r.output
    assert decisions_of(p, "main#2")[r_none] == "maybe"
    assert {rid: (decisions_of(p, "main")[rid], _votes(p, "main", rid)) for rid in (r_cli, r_ui)} == set1
    # the question shows the earlier answer in the specified words (failing today)
    assert "Your earlier answer in set 1: exclude" in qc["prompt"]



def _import_file(p, tmp, leave_out=4):
    """A synthetic decisions file for every record but the last ``leave_out`` (voice titles include, others exclude)."""
    recs = p.store.query("SELECT record_id, title, doi FROM records ORDER BY record_id")
    kept = recs[:len(recs) - leave_out]
    path = tmp / "decisions.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["title", "doi", "final_decision"])
        for r in kept:
            w.writerow([r["title"], r["doi"], "include" if "voice" in r["title"] else "exclude"])
    return path, [r["record_id"] for r in recs[len(recs) - leave_out:]], len(recs)


def _incomplete_import(p, tmp_path):
    _ok(cli(p, "screen", "protocol", DEV))
    path, missing, n = _import_file(p, tmp_path)
    _ok(cli(p, "screen", "import-decisions", "main", str(path), "--others-unscreened"))
    row = set_rows(p)[1]
    assert (row["result_set"], row["source"], row["scope"]) == (2, "import", "corpus")
    assert len(decisions_of(p, "main#2")) == n - len(missing)
    return path, missing, n


def test_import_set_not_continued(counted, tmp_path):  # noqa: F811
    """Scenario "An incomplete import is never continued" (llm-screening); 36 of 40 records imported here (the
    scenario's 6 of 10)."""
    p, c = counted
    path, missing, n = _incomplete_import(p, tmp_path)
    before, calls = decisions_of(p, "main#2"), c["calls"]
    r = cli(p, "screen", "protocol")
    text = " ".join(r.output.split())
    assert c["calls"] == calls and len(set_rows(p)) == 2 and decisions_of(p, "main#2") == before
    assert "set 2 is an import" in text and f"{len(missing)} of {n} records have no decision" in text
    assert f"reuse {n - len(missing)} decisions" in text and f"to screen: {len(missing)}" in text
    assert "Estimated cost" in text
    assert "--new-set --reuse-from 2" in text and "--new-set --no-reuse" in text
    with client(p) as cl:
        res = cl.post("/api/jobs/screen", json={"mode": "protocol", "scope": "corpus", "protocol": "main"})
    assert res.status_code == 409, res.text
    ns = res.json()["new_set_needed"]
    assert ns["to_screen"] == len(missing) and ns["reusable"]["count"] == n - len(missing) and ns["reusable"]["set"] == 2
    assert c["calls"] == calls and not reload(p).store.scalar("SELECT COUNT(*) FROM jobs")
    # the default --others-exclude: every other record is exclude and the import counts as finished
    _ok(cli(p, "screen", "import-decisions", "main", str(path)))
    d3 = decisions_of(p, "main#3")
    assert len(d3) == n and all(d3[m] == "exclude" for m in missing)
    assert rs.finished(p, rs.set_row(p, "main", 3)) is True
    assert rs.finished(p, rs.set_row(p, "main", 2)) is False
    assert r.exit_code == 0, r.output


def test_import_gaps_into_derived_set(counted, tmp_path):  # noqa: F811
    """Scenario "Screening the gaps of an import" (llm-screening)."""
    p, c = counted
    _path, missing, n = _incomplete_import(p, tmp_path)
    s2, calls = decisions_of(p, "main#2"), c["calls"]
    _ok(cli(p, "screen", "protocol", "--new-set", "--reuse-from", "2"))
    rows = set_rows(p)
    assert len(rows) == 3 and rows[2]["source"] == "screening" and rows[2]["active"] == 0
    assert _set_records(p, "main#3") == set(missing)
    assert c["calls"] - calls == 2 * len(missing)   # passes A and B on the 4 records, no third read needed
    assert decisions_of(p, "main#2") == s2
    got = {r["record_id"]: dict(r) for r in p.store.query(
        "SELECT record_id, decision, origin, vote_calls FROM screen_decisions WHERE protocol='main#3'")}
    assert len(got) == n
    run3 = set(rs.run_ids(rs.row_by_key(p, "main#3")))
    for rid, d in got.items():
        if rid in missing:
            assert d["origin"] is None
            vc = json.loads(d["vote_calls"])
            assert vc and {r["run_id"] for r in p.store.query(
                f"SELECT run_id FROM screen_calls WHERE call_id IN ({','.join('?' * len(vc))})", vc)} <= run3
        else:
            o = json.loads(d["origin"])
            assert (o["kind"], o["set"]) == ("import", 2) and d["decision"] == s2[rid]
    out = _ok(cli(p, "screen", "export", "main", "--set", "3"))
    f = p.root / "exports" / "screening" / "main.s3_decisions.csv"
    assert f.exists(), out.output
    ex = list(csv.DictReader(f.open(encoding="utf-8")))
    by = {int(r["record_id"]): r for r in ex}
    assert len(by) == n and {r["result_set"] for r in ex} == {"3"}
    assert all((by[m]["origin"], by[m]["origin_set"]) == ("", "") for m in missing)
    assert all((r["origin"], r["origin_set"]) == ("import", "2") for k, r in by.items() if k not in missing)


def test_import_refuses_hash_in_name(counted, tmp_path):  # noqa: F811
    """Scenario "A protocol name with # is refused on import" (llm-screening), first part."""
    p, _c = counted
    path, _missing, _n = _import_file(p, tmp_path, leave_out=0)
    r = cli(p, "screen", "import-decisions", "main#2", str(path))
    assert r.exit_code == 1
    assert not p.store.scalar("SELECT COUNT(*) FROM screen_result_sets")
    assert not p.store.scalar("SELECT COUNT(*) FROM screen_decisions")
    assert "a protocol name is 1-80 letters" in " ".join(r.output.split())
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_derived_set_reuses_and_screens_rest(counted):  # noqa: F811
    """Scenario "From the development sample to the corpus" (llm-screening)."""
    p, c = counted
    _ok(cli(p, "screen", "protocol", DEV))
    s1, row1, calls = decisions_of(p, "main"), set_rows(p)[0], c["calls"]
    r = cli(p, "screen", "protocol", "corpus", "--name", "main")
    text = " ".join(r.output.split())
    assert c["calls"] == calls and len(set_rows(p)) == 1
    assert "main has no set for corpus" in text and f"reuse {len(s1)} decisions" in text
    _ok(cli(p, "screen", "protocol", "corpus", "--name", "main", "--new-set", "--reuse-from", "1"))
    rows = set_rows(p)
    assert (rows[1]["role"], rows[1]["scope"]) == ("corpus", "corpus") and rows[0]["role"] == "development"
    n = p.store.scalar("SELECT COUNT(*) FROM records")
    assert _set_records(p, "main#2") == {x["record_id"] for x in p.store.query("SELECT record_id FROM records")} - set(s1)
    d2 = {r["record_id"]: dict(r) for r in p.store.query(
        "SELECT record_id, decision, origin FROM screen_decisions WHERE protocol='main#2'")}
    assert len(d2) == n
    for rid, dec in s1.items():
        o = json.loads(d2[rid]["origin"])
        assert (o["kind"], o["set"], d2[rid]["decision"]) == ("reused", 1, dec)
    assert decisions_of(p, "main") == s1 and set_rows(p)[0] == row1


def test_reuse_refused_on_method_change(counted):  # noqa: F811
    """Scenario "Reuse refused after a method change" (llm-screening)."""
    p, c = counted
    _ok(cli(p, "screen", "protocol", DEV))
    set_model(p, "m2")
    calls, runs = c["calls"], p.store.scalar("SELECT COUNT(*) FROM runs")
    r = cli(p, "screen", "protocol", "corpus", "--name", "main", "--new-set", "--reuse-from", "1")
    text = " ".join(r.output.split())
    assert c["calls"] == calls and len(set_rows(p)) == 1 and p.store.scalar("SELECT COUNT(*) FROM runs") == runs
    assert "the method changed (model)" in text
    assert "earlier model decisions are not reused when the method changed" in text


def test_development_set_labelled(counted):  # noqa: F811
    """Scenario "A development set is labelled" (llm-screening)."""
    p, _c = counted
    _ok(cli(p, "screen", "protocol", DEV))
    assert rs.active_set(p, "main")["role"] == "development"
    note = "development set: not an independent validation"
    _label_all(p)
    assert note in " ".join(_ok(cli(p, "recall", "main")).output.split())
    assert note in " ".join(_ok(cli(p, "screen", "models", "--protocol", "main")).output.split())
    js = json.loads(_ok(cli(p, "screen", "models", "--protocol", "main", "--json")).output)
    assert js["scope"]["role_note"] == note


# ====================================================================================================
# Result sets, part C: choosing, comparing and reading sets (use, compare, routes, exports, readers, selects, recall
# ====================================================================================================
def _two_sets(p, counter):
    """Set 1 (model `fake`, active) and set 2 (model `m2`, label "model m2", three includes turned maybe) of `main`
    over the validation sample, which reviewer r1 labels (include when the title is about voice)."""
    run_protocol(p, "sample:validation", "main")
    set_model(p, "m2")
    counter["flip"] = True
    run_protocol(p, "sample:validation", "main", new_set=True, set_label="model m2")
    counter["flip"] = False
    for r in p.store.query("SELECT sr.record_id, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                           "JOIN records m ON m.record_id=sr.record_id WHERE s.name='validation'"):
        label(p, "validation", r["record_id"], "r1", "include" if "voice" in r["title"] else "exclude")
    assert decisions_of(p, "main") != decisions_of(p, "main#2")
    return p


def _active(p, protocol="main"):
    return [r["result_set"] for r in set_rows(p, protocol) if r["active"]]


def _csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _json(res):
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


# ----------------------------------------------------------------------------- use and compare
def test_use_and_compare(counted):  # noqa: F811
    """llm-screening "Choosing and comparing sets": `sets use main 2` switches (journalled, undoable, no call); the
    export uses set 2; `sets compare main 1 2 --json` gives the 3x3 table, the agreement and the differing records."""
    p, counter = counted
    _two_sets(p, counter)
    calls, runs = counter["calls"], n_runs(p)
    res = cli(p, "screen", "sets", "use", "main", "2", "--author", "Ana")
    assert res.exit_code == 0 and "main uses set 2" in res.output
    p = reload(p)
    assert _active(p) == [2]
    from wider.core.journal import changes, undo
    ch = changes(p, kind="result_set")[0]
    assert ch["author"] == "Ana" and "main uses set 2" in ch["summary"] and ch["undoable"]
    assert counter["calls"] == calls and n_runs(p) == runs

    res = cli(p, "screen", "export", "main")
    assert res.exit_code == 0
    dec = _csv(p.path("exports", "screening", "main.s2_decisions.csv"))
    ev = _csv(p.path("exports", "screening", "main.s2_evaluations.csv"))
    assert {r["result_set"] for r in dec} == {"2"} and {r["result_set_label"] for r in dec} == {"model m2"}
    assert {r["decided_by_models"] for r in dec} == {"m2"}
    assert {r["run_id"] for r in ev} == set(rs.run_ids(rs.set_row(p, "main", 2))) and {r["model"] for r in ev} == {"m2"}
    assert all(r["at"] for r in ev) and {r["result_set"] for r in ev} == {"2"}

    c = _json(cli(p, "screen", "sets", "compare", "main", "1", "2", "--json"))
    d1, d2 = decisions_of(p, "main"), decisions_of(p, "main#2")
    assert set(c["table"]) == {"include", "maybe", "exclude"} and all(set(v) == set(c["table"]) for v in c["table"].values())
    for x in c["table"]:
        for y in c["table"]:
            assert c["table"][x][y] == sum(1 for r in d1 if d1[r] == x and d2[r] == y)
    diff = sorted(r for r in d1 if d1[r] != d2[r])
    assert diff and [d["record_id"] for d in c["differ"]] == diff
    titles = {r["record_id"]: r["title"] for r in p.store.query("SELECT record_id, title FROM records")}
    assert all(d["title"] == titles[d["record_id"]] and set(d) == {"record_id", "a", "b", "title"} for d in c["differ"])
    assert c["n"] == len(d1) and c["agreement"] == round((len(d1) - len(diff)) / len(d1), 4)
    assert _active(reload(p)) == [2] and counter["calls"] == calls   # comparing changes nothing

    undo(reload(p), ch["change_id"], author="Ana")
    assert _active(reload(p)) == [1]


def test_sets_routes_match_cli(counted, monkeypatch):  # noqa: F811
    """llm-screening "Choosing and comparing sets": the "Result sets" card's routes give what the CLI gives (list,
    use, label, compare with and without `models_only`), and "Use for analysis" on an out-of-step second-screen set is
    refused until `with_source` ("Use both")."""
    p, counter = counted
    _two_sets(p, counter)
    c = client(p)
    assert c.get("/api/screen/sets", params={"protocol": "main"}).json()["sets"] == _json(
        cli(p, "screen", "sets", "list", "main", "--json"))
    for mo in (False, True):
        api = c.get("/api/screen/sets/main/compare", params={"a": 1, "b": 2, "models_only": mo}).json()
        args = ["--models-only"] if mo else []
        assert api == _json(cli(p, "screen", "sets", "compare", "main", "1", "2", *args, "--json"))
        assert api["models_only"] is mo
    calls = counter["calls"]
    r = c.post("/api/screen/sets/main/2/use")
    assert r.status_code == 200 and r.json()["result_set"] == 2 and _active(reload(p)) == [2]
    assert cli(p, "screen", "sets", "use", "main", "1").exit_code == 0 and _active(reload(p)) == [1]
    r = c.patch("/api/screen/sets/main/2", json={"label": "second model"})
    assert r.status_code == 200 and r.json()["label"] == "second model"
    assert cli(p, "screen", "sets", "label", "main", "2", "other model").exit_code == 0
    assert c.get("/api/screen/sets", params={"protocol": "main"}).json()["sets"][1]["label"] == "other model"
    assert c.post("/api/screen/sets/main/9/use").status_code == 404
    assert cli(p, "screen", "sets", "use", "main", "9").exit_code == 1
    assert counter["calls"] == calls

    # a second screen built from set 2, then main back on set 1: "Use for analysis" is refused, "Use both" works
    assert cli(p, "screen", "sets", "use", "main", "2").exit_code == 0
    from wider.screen.protocol import second_screen
    second_screen(reload(p), "main")
    assert cli(p, "screen", "sets", "use", "main", "1").exit_code == 0
    msg = "set 1 was built from main set 2; main uses set 1. Use main set 2 first, or add --with-source to switch both"
    r = c.post("/api/screen/sets/main_second/1/use")
    assert r.status_code == 400 and r.json()["detail"] == msg
    res = cli(p, "screen", "sets", "use", "main_second", "1")
    assert res.exit_code == 1 and msg in " ".join(res.output.split())
    r = c.post("/api/screen/sets/main_second/1/use", params={"with_source": True})
    assert r.status_code == 200 and _active(reload(p)) == [2] and _active(reload(p), "main_second") == [1]


# ----------------------------------------------------------------------------- exports carry the set
def _export_scenario(p, counter, which):
    _two_sets(p, counter)
    if which == "screen_export":
        assert cli(p, "screen", "export", "main", "--set", "2").exit_code == 0
        run2 = set(rs.run_ids(rs.set_row(p, "main", 2)))
        for name in ("main.s2_decisions.csv", "main.s2_evaluations.csv"):
            rows = _csv(p.path("exports", "screening", name))
            assert rows and all(r["result_set"] == "2" and r["result_set_label"] == "model m2" for r in rows)
            # every row names the run id and the model
            assert all(r.get("run_id") in run2 if "run_id" in r else set(r["run_ids"].split(";")) <= run2
                       for r in rows), name
            assert all("m2" in (r.get("model") or r.get("decided_by_models") or "") for r in rows), name
        assert cli(p, "report", "--protocol", "main", "--bundle").exit_code == 0
        b = p.path("exports", "share_bundle")
        rows = _csv(b / "screen_decisions.csv")
        assert {(r["result_set"], r["active"]) for r in rows if r["protocol"] == "main"} == {("1", "yes"), ("2", "")}
        sets_ = _csv(b / "result_sets.csv")
        assert [(r["result_set"], r["label"], r["active"]) for r in sets_] == [("1", "", "yes"), ("2", "model m2", "")]
        assert all(r["settings_hash"] and r["scope"] == "sample:validation" for r in sets_)
    elif which == "report":
        assert cli(p, "report", "--protocol", "main", "--sample", "validation").exit_code == 0
        md, js = p.path("exports", "report.md"), p.path("exports", "report.json")
        before = (md.read_text(), js.read_text())
        rep = json.loads(before[1])["recall"]
        assert rep["result_set"] == 1 and rep["result_set_key"] == "main"
        last = json.loads(p.store.get_meta("last_report"))
        assert last["protocol"] == "main" and last["result_set"] == 1
        assert cli(p, "screen", "sets", "use", "main", "2").exit_code == 0
        assert (md.read_text(), js.read_text()) == before
        # the methods line names set 1 with its models
        line = next(x for x in before[0].splitlines() if x.startswith("## Recall"))
        assert "main" in line and "fake" in line, line
    elif which == "references":
        assert cli(p, "export", "references", "--protocol", "main", "--set", "2").exit_code == 0
        inc2 = sorted(r for r, d in decisions_of(p, "main#2").items() if d == "include")
        ris = p.path("exports", "references", "main.s2_include.ris").read_text()
        assert ris.count("N1  - WIDER main set 2: include") == len(inc2)
        rows = _csv(p.path("exports", "references", "main.s2_include.csv"))
        assert sorted(int(r["record_id"]) for r in rows) == inc2
        assert all(r.get("result_set") == "2" for r in rows), list(rows[0])
    elif which == "package":
        assert cli(p, "screen", "sets", "use", "main", "2").exit_code == 0
        res = cli(p, "export", "package", "--protocol", "main")
        assert res.exit_code == 0, res.output
        z = sorted(p.path("exports").glob("replication_package_*.zip"))[-1]
        with zipfile.ZipFile(z) as zf:
            man = json.loads(zf.read("manifest.json"))
            rep = json.loads(zf.read("share_bundle/report.json"))
        assert [s["key"] for s in man["result_sets"] if s["protocol"] == "main"] == ["main#2"]
        assert rep["recall"]["result_set_key"] == "main#2"
        z.unlink()
        assert cli(p, "export", "package", "--protocol", "main", "--set", "1").exit_code == 0
        z = sorted(p.path("exports").glob("replication_package_*.zip"))[-1]
        with zipfile.ZipFile(z) as zf:
            man = json.loads(zf.read("manifest.json"))
            rep = json.loads(zf.read("share_bundle/report.json"))
        assert [s["key"] for s in man["result_sets"] if s["protocol"] == "main"] == ["main"]
        assert rep["recall"]["result_set_key"] == "main" and _active(reload(p)) == [2]
        # the Recall page's select sends the same set
        r = client(p).post("/api/outputs/package", params={"protocol": "main", "result_set": 1})
        assert r.status_code == 200 and [s["key"] for s in r.json()["result_sets"]] == ["main"]
        assert _active(reload(p)) == [2]


@pytest.mark.parametrize("which", [
    "screen_export",
    "report",
    "references",
    "package",
])
def test_exports_carry_set(counted, which):  # noqa: F811
    """llm-screening "Exports name the set"; recall-and-outputs "Report tied to its set", "Export of another set" and
    "Package names the sets"."""
    p, counter = counted
    _export_scenario(p, counter, which)


# ----------------------------------------------------------------------------- readers use the active set
def _reader(p, counter, which):
    _two_sets(p, counter)
    if which == "recall":
        from wider.recall.estimate import estimate
        e1, e2 = estimate(p, "validation", "main", B=50), estimate(p, "validation", "main", B=50, result_set=2)
        assert (e1["result_set_key"], e2["result_set_key"]) == ("main", "main#2")
        assert e1["variants"]["strict"]["recall"] != e2["variants"]["strict"]["recall"]
        assert "set 1" in cli(p, "recall", "main", "-B", "50").output
        out = cli(p, "recall", "main", "-B", "50", "--set", "2").output
        assert 'set 2 "model m2"' in out
        api = client(p).get("/api/recall", params={"protocol": "main", "result_set": 2, "B": 50}).json()
        assert api["result_set"] == 2 and api["variants"] == json.loads(json.dumps(e2["variants"]))
        assert cli(p, "screen", "sets", "use", "main", "2").exit_code == 0
        assert estimate(reload(p), "validation", "main", B=50)["result_set_key"] == "main#2"
    elif which == "recall_names_set":   # the scenario's exact surfaces
        s2 = rs.sets(p, "main")[1]
        out = _json(cli(p, "recall", "main", "--set", "2", "--json", "-B", "50"))
        assert out["result_set"] == 2 and out["result_set_label"] == "model m2" and out["result_set_role"] == "validation"
        assert out.get("models") == ["m2"] and out.get("run_ids") == s2["run_ids"] and out.get("date")
        txt = cli(p, "recall", "main", "-B", "50").output
        assert "fake" in txt and all(r in txt for r in rs.sets(p, "main")[0]["run_ids"])
        api = client(p).get("/api/recall", params={"protocol": "main", "set": 2, "B": 50}).json()
        assert api["result_set"] == 2 and api["variants"] == out["variants"]
    elif which == "adjacent":
        from wider.recall.adjacent import exclusions_by_literature
        p.store.execute("UPDATE screen_decisions SET labels=? WHERE protocol='main'", (json.dumps({"adjacent_literature": "a1"}),))
        p.store.execute("UPDATE screen_decisions SET labels=? WHERE protocol='main#2'", (json.dumps({"adjacent_literature": "a2"}),))
        want = lambda k: {d: sum(1 for x in decisions_of(p, k).values() if x == d) for d in set(decisions_of(p, k).values())}  # noqa: E731
        assert exclusions_by_literature(p) == {"main": {"a1": want("main")}}
        rs.use(p, "main", 2)
        assert exclusions_by_literature(p) == {"main": {"a2": want("main#2")}}
    elif which == "export":
        from wider.screen.export import export_protocol
        assert export_protocol(p, "main")["key"] == "main"
        rs.use(p, "main", 2)
        assert export_protocol(p, "main")["key"] == "main#2" and export_protocol(p, "main", 1)["key"] == "main"
    elif which == "second":
        from wider.screen.protocol import second_screen
        rs.use(p, "main", 2)
        out = second_screen(p, "main")
        row = rs.set_row(p, "main_second", 1)
        assert out["source"] == "main#2" and (row["source_protocol"], row["source_set"]) == ("main", 2)
        assert out["maybes_reread"] == sum(1 for d in decisions_of(p, "main#2").values() if d == "maybe")
    elif which == "dedup":
        from wider.dedup.engine import run as dedup_run
        before = (decisions_of(p, "main"), decisions_of(p, "main#2"))
        dedup_run(p)
        assert (decisions_of(p, "main"), decisions_of(p, "main#2")) == before
    elif which == "set_options":
        assert _json(cli(p, "screen", "models", "--protocol", "main", "--set", "2", "--json"))["scope"]["key"] == "main#2"
        assert cli(p, "report", "--protocol", "main", "--set", "2").exit_code == 0
        assert json.loads(p.path("exports", "report.json").read_text())["recall"]["result_set_key"] == "main#2"
        assert cli(p, "export", "references", "--protocol", "main", "--set", "2").exit_code == 0
        assert p.path("exports", "references", "main.s2_include.ris").exists()
        assert cli(p, "export", "package", "--protocol", "main", "--set", "2").exit_code == 0
        z = sorted(p.path("exports").glob("replication_package_*.zip"))[-1]
        assert json.loads(zipfile.ZipFile(z).read("manifest.json"))["result_sets"][0]["key"] == "main#2"
        assert _active(reload(p)) == [1]
    elif which == "unknown_set":   # recall-and-outputs.md:133: "no result set <n>", no traceback, exit 1
        for args in (["recall", "main"], ["report", "--protocol", "main"], ["export", "references", "--protocol", "main"],
                     ["export", "package", "--protocol", "main"]):
            res = cli(p, *args, "--set", "7")
            assert res.exit_code == 1 and "Traceback" not in res.output and "no result set 7" in res.output, args
        assert _active(reload(p)) == [1]


@pytest.mark.parametrize("which", [
    "recall",
    "recall_names_set",
    "adjacent", "export", "second", "dedup", "set_options",
    "unknown_set",
])
def test_readers_use_active_set(counted, which):  # noqa: F811
    """recall-and-outputs "Recall names the set it read" and design Decision 23's reader list: recall,
    recall/adjacent.py, export, second screen and dedup read the active set; recall, report, references and package
    accept --set N."""
    p, counter = counted
    _reader(p, counter, which)


# ----------------------------------------------------------------------------- result-set selects
def test_set_select_keeps_active(counted):  # noqa: F811
    """llm-screening "Looking at another set without switching" and recall-and-outputs "Choosing a set on the Recall
    page": set 2 chosen for Models used, Export CSV and the recall estimate reads set 2, and set 1 stays active."""
    p, counter = counted
    _two_sets(p, counter)
    calls, c = counter["calls"], client(p)
    sets_ = c.get("/api/screen/sets", params={"protocol": "main"}).json()["sets"]
    s2 = sets_[1]
    assert (s2["protocol"], s2["result_set"], s2["label"], s2["role"], s2["models"]) == ("main", 2, "model m2", "validation", ["m2"])
    assert s2["run_ids"] and all(r.endswith("__main.s2") for r in s2["run_ids"]) and not s2["active"]
    api = c.get("/api/screen/models", params={"protocol": "main", "result_set": 2}).json()
    assert api == _json(cli(p, "screen", "models", "--protocol", "main", "--set", "2", "--json"))
    assert api["scope"]["result_set"] == 2 and [m["model"] for m in api["models"]] == ["m2"]
    r = c.post("/api/screen/export", params={"protocol": "main", "result_set": 2})
    assert r.status_code == 200 and r.json()["result_set"] == 2
    assert {x["result_set"] for x in _csv(p.root / r.json()["decisions"])} == {"2"}
    e = c.get("/api/recall", params={"protocol": "main", "result_set": 2, "B": 50}).json()
    assert e["result_set"] == 2 and e["result_set_label"] == "model m2"
    assert [s["result_set"] for s in c.get("/api/screen/sets", params={"protocol": "main"}).json()["sets"]
            if s["active"]] == [1]
    assert counter["calls"] == calls and _active(reload(p)) == [1]


def test_recall_compare_sets(counted):  # noqa: F811
    """recall-and-outputs "Choosing a set on the Recall page": "Compare with set" 1 shows both estimates side by side
    as `wider recall main --set 2 --compare 1` does; the active set is unchanged."""
    p, counter = counted
    _two_sets(p, counter)
    from wider.recall.estimate import estimate
    e1, e2 = estimate(p, "validation", "main", B=50, result_set=1), estimate(p, "validation", "main", B=50, result_set=2)
    api = client(p).get("/api/recall", params={"protocol": "main", "result_set": 2, "compare": 1, "B": 50}).json()
    assert api["result_set"] == 2 and api["compare"]["result_set"] == 1 and api["compare"]["result_set_key"] == "main"
    for k, v in api["variants"].items():
        assert v["recall"] == e2["variants"][k]["recall"]
        assert api["compare"]["variants"][k] == {key: e1["variants"][k][key] for key in  # the interval too (recall-interval-honest)
                                                    ("recall", "n_missed", "interval", "interval_reason")}
    assert e1["variants"]["strict"]["recall"] != e2["variants"]["strict"]["recall"]
    res = cli(p, "recall", "main", "--set", "2", "--compare", "1", "-B", "50")
    assert res.exit_code == 0
    head = next(x for x in res.output.splitlines() if "set 2 recall" in x)
    assert "set 1 recall" in head
    row = next(x for x in res.output.splitlines() if x.strip("│┃ ").startswith("strict") and "set" not in x
               and f"{e1['variants']['strict']['recall']:.3f}" in x)
    assert f"{e2['variants']['strict']['recall']:.3f}" in row
    from wider.recall.uncertainty import interval_text
    assert interval_text(e1["variants"]["strict"]) in row and interval_text(e2["variants"]["strict"]) in row
    assert client(p).get("/api/recall", params={"protocol": "main", "result_set": 2, "compare": 9}).status_code in (400, 404)
    assert _active(reload(p)) == [1]


# ----------------------------------------------------------------------------- dedup covers every set
def test_dedup_covers_all_sets(tmp_path):
    """Design Decision 23: dedup rewrites record ids in every set key, so its merges, its collision check and its
    downstream counts cover non-active sets too."""
    import sqlite3

    from tests.unit.test_dedup import _project_with_imports
    from wider.dedup.engine import DOWNSTREAM_TABLES, _downstream_counts, decide, queue_rows
    from wider.dedup.engine import run as dedup_run
    from wider.screen.decisions import set_decision

    assert {"screen_decisions", "screen_calls", "screen_call_reuse"} <= set(DOWNSTREAM_TABLES)
    p = _project_with_imports(tmp_path)
    dedup_run(p)
    q = queue_rows(p)[0]
    rec = lambda raw: p.store.scalar("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id "  # noqa: E731
                                     "WHERE c.raw_id=?", (raw,))
    a, b = rec(q["raw_a"]), rec(q["raw_b"])
    other = p.store.scalar("SELECT record_id FROM records WHERE record_id NOT IN (?, ?) LIMIT 1", (a, b))
    rs.new_set(p, "main", "screening")
    rs.new_set(p, "main", "screening")
    assert _active(p) == [1]
    set_decision(p, "main", other, "include")
    set_decision(p, "main#2", b, "exclude")   # only the non-active set knows record b
    with sqlite3.connect(p.store.path) as c:
        assert _downstream_counts(c, b) == {"screen_decisions": 1}
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    s = dedup_run(p)["summary"]
    assert s["unique_records_merged_into_existing"] == 1
    survivor = rec(q["raw_a"])
    assert decisions_of(p, "main#2") == {survivor: "exclude"} and decisions_of(p, "main") == {other: "include"}

    # split them again, decide the new record in set 2 as well: merging collides inside set 2, so the survivor's
    # decision stays and the other's is set aside (owner bug 1, 1 Nov 2026)
    decide(p, q["raw_a"], q["raw_b"], "not_duplicate", by="tester")
    dedup_run(p)
    new_b = rec(q["raw_b"])
    assert new_b != survivor
    set_decision(p, "main#2", new_b, "include")
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    assert dedup_run(p)["set_aside"]
    assert decisions_of(p, "main#2") == {survivor: "exclude"}
    assert p.store.scalar("SELECT COUNT(*) FROM merge_set_aside WHERE from_table='screen_decisions'") == 1


# ----------------------------------------------------------------------------- `sets list` is a subcommand
def test_sets_list_is_a_subcommand(counted):  # noqa: F811
    """Task 9.17: `wider screen sets list PROTOCOL [--json]` is a subcommand, so a protocol named `use` or `label` is
    listed, never read as the `use` or `label` command."""
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.screen.decisions import set_decision

    p, _counter = counted
    for name in ("use", "label"):
        set_decision(p, name, 1, "include")
    run = lambda *a: CliRunner().invoke(app, [*a, "--dir", str(p.root)], env={"COLUMNS": "300"})  # noqa: E731
    for name in ("use", "label"):
        res = run("screen", "sets", "list", name, "--json")
        assert res.exit_code == 0, res.output
        rows = json.loads(res.output)
        assert [(r["protocol"], r["result_set"], r["include"], r["active"]) for r in rows] == [(name, 1, 1, 1)]
        res = run("screen", "sets", "list", name)
        assert res.exit_code == 0 and "has no result sets" not in res.output
    assert json.loads(run("screen", "sets", "list", "main", "--json").output) == []
    res = run("screen", "sets", "use")   # the command `use`, missing its arguments: not a listing
    assert res.exit_code == 2 and "Missing argument" in res.output
    assert run("screen", "sets", "main").exit_code == 2   # no implicit `list`
    assert p.store.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE active=1 AND protocol IN ('use','label')") == 2


# ====================================================================================================
# Result sets, part D: run ids per protocol and set, the legacy shared run, and evaluation reuse (design Decision 23,
# ====================================================================================================


def runs_of(p, key):
    return rs.run_ids(rs.row_by_key(p, key))


def calls_of(p, run_id):
    return [dict(r) for r in p.store.query("SELECT * FROM screen_calls WHERE run_id=? ORDER BY call_id", (run_id,))]


def reuse_rows(p, run_ids):
    q = ",".join("?" * len(run_ids))
    return [dict(r) for r in p.store.query(f"SELECT * FROM screen_call_reuse WHERE run_id IN ({q})", run_ids)]


def n_dev(p):
    return p.store.scalar("SELECT COUNT(*) FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                          "WHERE s.name='development'")


def fake_cfg(p):
    return next(x for x in p.config.providers if x.name == "fake")


def test_protocol_run_ids_carry_set(counted):  # noqa: F811
    """Scenario "A second set screens in its own runs": set 1's runs end in __main.s1, set 2's in __main.s2."""
    p, counter = counted
    run_protocol(p, DEV, "main")
    s1 = runs_of(p, "main")
    assert s1 and all(r.endswith("__main.s1") for r in s1)
    before = {r: calls_of(p, r) for r in s1}
    calls = counter["calls"]
    set_model(p, "m2")
    out = run_protocol(p, DEV, "main", new_set=True)
    assert out["result_set"] == 2
    s2 = runs_of(p, "main#2")
    assert s2 and all(r.endswith("__main.s2") for r in s2) and not set(s1) & set(s2)
    # set 1's runs are not resumed: set 2 screened every record itself, set 1's calls are untouched
    assert counter["calls"] - calls >= 2 * n_dev(p)
    assert {r: calls_of(p, r) for r in s1} == before
    assert {r["run_id"] for r in p.store.query("SELECT DISTINCT run_id FROM screen_calls")} == set(s1) | set(s2)


def test_run_ids_of_hash_and_dash_differ(counted):  # noqa: F811
    """`maybe:main#2` and `maybe:main-2` give different run ids (`#` written as `.`); plain runs keep their ids."""
    p, _ = counted
    a = _run_id(p, "second_screen", "S1", 1, "fake", "maybe:main#2", "main_second", 1)
    b = _run_id(p, "second_screen", "S1", 1, "fake", "maybe:main-2", "main_second", 1)
    assert a != b
    assert a == "second_screen__maybe-main.2__p1__fake__S1__main_second.s1"
    assert b == "second_screen__maybe-main-2__p1__fake__S1__main_second.s1"
    assert _run_id(p, "screen", "A", 1, "fake", "maybe:main") == "screen__maybe-main__p1__fake__A"
    # a real second screen of set 2 runs under the `.` form
    p.config.protocol.screening.second_screen = True
    p.save()
    run_protocol(p, DEV, "main")
    set_model(p, "m2")
    run_protocol(p, DEV, "main", new_set=True)
    p.store.execute("UPDATE screen_decisions SET decision='maybe' WHERE protocol='main#2' AND record_id IN "
                    "(SELECT record_id FROM screen_decisions WHERE protocol='main#2' LIMIT 2)")
    out = second_screen(p, "main", from_set=2)
    assert out["runs"] and all("__maybe-main.2__" in r and r.endswith("__main_second.s1") for r in out["runs"])


def test_two_protocols_own_run_ids(counted):  # noqa: F811
    """Scenario "Two protocols never share a run": each protocol has its own runs and alt makes its own calls."""
    p, counter = counted
    run_protocol(p, DEV, "main")
    c_main = counter["calls"]
    main_runs = runs_of(p, "main")
    main_calls = {r: calls_of(p, r) for r in main_runs}
    run_protocol(p, DEV, "alt")
    alt_runs = runs_of(p, "alt")
    assert all(r.endswith("__main.s1") for r in main_runs) and all(r.endswith("__alt.s1") for r in alt_runs)
    assert [r.removesuffix("__main.s1") for r in main_runs] == [r.removesuffix("__alt.s1") for r in alt_runs]
    assert counter["calls"] - c_main == c_main   # alt screened every record itself, nothing resumed from main
    assert {r: calls_of(p, r) for r in main_runs} == main_calls
    assert not reuse_rows(p, alt_runs)


def test_long_scopes_get_distinct_run_ids(counted):  # noqa: F811
    """Two `ids:` scopes over 40 characters sharing their first 40 get different run ids; a plain run keeps its id."""
    p, counter = counted
    ids = list(range(1, 21))
    s1, s2 = "ids:" + ",".join(map(str, ids)), "ids:" + ",".join(map(str, ids + [21]))
    assert len(s1) > 40 and s1.replace(":", "-").replace(",", "_")[:40] == s2.replace(":", "-").replace(",", "_")[:40]
    a = _run_id(p, "screen", "A", 1, "fake", s1, "main", 1)
    b = _run_id(p, "screen", "A", 1, "fake", s2, "main", 1)
    assert a != b
    safe = s1.replace(":", "-").replace(",", "_")
    assert a == f"screen__{safe[:31]}~{sha256_text(s1)[:8]}__p1__fake__A__main.s1"
    # real protocol runs over the two scopes do not share a run
    p.config.protocol.screening.passes = 1
    p.save()
    ra = run_protocol(p, s1, "one")["runs"]
    rb = run_protocol(p, s2, "two")["runs"]
    assert ra == [_run_id(p, "screen", "A", 1, "fake", s1, "one", 1)] and "~" in ra[0]
    assert rb == [_run_id(p, "screen", "A", 1, "fake", s2, "two", 1)]
    assert ra[0].removesuffix("__one.s1") != rb[0].removesuffix("__two.s1")
    assert counter["calls"] == 41
    # a plain `ids:` run over 40 characters keeps its old id (cut at 40, no hash)
    plain = screen(p, s1)["run_id"]
    assert plain == f"screen__{safe[:40]}__p1__fake__A"
    assert plain == _run_id(p, "screen", "A", 1, "fake", s1)


def _legacy_shared(p, limit):
    """Plain passes A and B over part of the development sample, used by protocols main and alt as a v3 store held
    them: decisions, set-1 rows without scope or runs, and the stage results naming the runs."""
    ra = screen(p, DEV, pass_name="A", limit=limit)["run_id"]
    rb = screen(p, DEV, pass_name="B", limit=limit, seed_offset=1)["run_id"]
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM screen_calls WHERE run_id=?", (ra,))]
    st = p.store
    for proto in ("main", "alt"):
        for rid in ids:
            st.execute("INSERT INTO screen_decisions(protocol,record_id,decision,decided_at) VALUES(?,?,?,?)",
                       (proto, rid, "exclude", "2026-01-01"))
        st.execute("INSERT OR IGNORE INTO screen_result_sets(protocol,result_set,key,source,created_at,created_by,mixed,"
                   "active) VALUES(?,1,?,'screening','2026-01-01','migration',0,1)", (proto, proto))
        st.execute("INSERT INTO stage_results(stage,action,ran_at,result) VALUES('screen','protocol','2026-01-01',?)",
                   (json.dumps({"headline": f"Protocol {proto}: {len(ids)} records",
                                "rows": [{"run": ra}, {"run": rb}]}),))
    st.execute("DELETE FROM meta WHERE key=?", (rs.MIGRATED_META,))
    return ra, rb, ids


def test_legacy_shared_run_frozen(counted):  # noqa: F811
    """Scenario "A run shared by two protocols before this version": listed as shared in both, never resumed, and
    the new records of main go into a run ending __main.s1."""
    p, counter = counted
    ra, rb, ids = _legacy_shared(p, 4)
    p = reload(p)
    for proto in ("main", "alt"):
        s = rs.sets(p, proto)[0]
        assert set(s["shared_legacy"]) == {ra, rb}, proto
        r = cli(p, "screen", "sets", "list", proto, "--json")
        assert r.exit_code == 0, r.output
        assert set(json.loads(r.output)[0]["shared_legacy"]) == {ra, rb}
    old = {r: calls_of(p, r) for r in (ra, rb)}
    calls = counter["calls"]
    out = run_protocol(p, DEV, "main")
    assert out["result_set"] == 1
    new = [r for r in out["runs"] if r not in (ra, rb)]
    assert new and all(r.endswith("__main.s1") for r in new)
    assert {r: calls_of(p, r) for r in (ra, rb)} == old   # the shared runs are not resumed
    left = n_dev(p) - len(ids)
    assert counter["calls"] - calls >= 2 * left
    screened = {c["record_id"] for r in new for c in calls_of(p, r)}
    assert screened and not screened & set(ids)


def _main_then(p, counter, proto, **kw):
    """Run protocol ``proto`` with --reuse-evaluations; returns (new calls made, reuse rows of its runs)."""
    before = counter["calls"]
    out = run_protocol(p, DEV, proto, reuse_evaluations=True, **kw)
    return counter["calls"] - before, reuse_rows(p, out["runs"])


def test_eval_reuse_needs_identical_inputs(counted):  # noqa: F811
    """Scenario "Any changed input prevents reuse": model, temperature (edit or run override), codebook,
    record text, endpoint, port, extra.body, deployment, a schema option or argv: nothing is reused."""
    from wider.screen.codebook import build, save_prompt_artifact

    p, counter = counted
    p.config.protocol.screening.passes = 1
    p.save()
    n = n_dev(p)
    run_protocol(p, DEV, "main")
    cfg = fake_cfg(p)
    snap = cfg.model_dump()
    i = 0

    def case(label, edit, **kw):
        nonlocal i, cfg
        i += 1
        edit(cfg)
        p.save()
        made, reused = _main_then(p, counter, f"alt{i}", **kw)
        assert (made, reused) == (n, []), label
        for k, v in snap.items():
            setattr(cfg, k, v)
        p.save()

    case("model", lambda c: setattr(c, "model", "m2"))
    case("temperature", lambda c: setattr(c, "temperature", 0.7))
    case("run --temperature", lambda c: None, temperature=0.9)
    case("endpoint", lambda c: setattr(c, "base_url", "http://localhost:9001/v1"))
    case("extra.body", lambda c: setattr(c, "extra", {"body": {"top_p": 0.5}}))
    case("extra.deployment", lambda c: setattr(c, "extra", {"deployment": "d2"}))
    case("schema option", lambda c: setattr(c, "extra", {"strict_schema": False}))
    case("argv", lambda c: setattr(c, "command", ["fake-agent", "--flag"]))
    # the port alone (same host)
    cfg.base_url = "http://localhost:9001/v1"
    p.save()
    run_protocol(p, DEV, "port_a")
    case("port", lambda c: setattr(c, "base_url", "http://localhost:9002/v1"))
    # record text
    p.store.execute("UPDATE records SET title=title || ' (corrected)'")
    case("record text", lambda c: None)
    # a changed codebook (a new prompt version with new text)
    from wider.construct import artifacts
    cb = artifacts.load(p, "codebook")[1]
    cb = {**cb, "question": cb["question"] + " (reworded)"}
    artifacts.save_new_version(p, "codebook", cb, origin="manual")
    save_prompt_artifact(p, build(p))
    case("codebook", lambda c: None)
    # a second-screen pass never reuses a first-screen call (same backend, same records)
    p.config.protocol.screening.second_screen = True
    p.save()
    run_protocol(p, DEV, "fs")
    p.store.execute("UPDATE screen_decisions SET decision='maybe' WHERE protocol='fs'")
    before = counter["calls"]
    out = second_screen(p, "fs", reuse_evaluations=True)
    assert counter["calls"] - before >= 2 * n and not reuse_rows(p, out["runs"])
    # control: with nothing changed, every evaluation is reused
    assert _main_then(p, counter, "same")[0] == 0


def test_eval_reuse_needs_identical_inputs_prompt_version(counted):  # noqa: F811
    """Scenario "Any changed input prevents reuse", the prompt-version case: a new prompt version reuses nothing."""
    from wider.screen.codebook import build, load_prompt, save_prompt_artifact

    p, counter = counted
    p.config.protocol.screening.passes = 1
    p.save()
    run_protocol(p, DEV, "main")
    v = load_prompt(p, None)[0]["version"]
    save_prompt_artifact(p, build(p))
    assert load_prompt(p, None)[0]["version"] == v + 1
    assert _main_then(p, counter, "alt") == (n_dev(p), [])


def _evaluations(p, protocol):
    r = cli(p, "screen", "export", protocol)
    assert r.exit_code == 0, r.output
    path = re.search(r"evaluations: (\S+\.csv)", r.output.replace("\n", "")).group(1)
    with open(p.root / path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_reused_eval_immutable_and_marked(counted):  # noqa: F811
    """Scenario "Identical inputs are reused on request": the CLI names the counts before any call, no backend call is
    made, main's calls are unchanged and alt's evaluations export marks them reused with main's run."""
    p, counter = counted
    run_protocol(p, DEV, "main")
    main_runs = runs_of(p, "main")
    before = {r: calls_of(p, r) for r in main_runs}
    calls = counter["calls"]
    n = n_dev(p)
    r = cli(p, "screen", "protocol", DEV, "--name", "alt", "--reuse-evaluations")
    assert r.exit_code == 0, r.output
    assert counter["calls"] == calls
    p = reload(p)
    assert {r_: calls_of(p, r_) for r_ in main_runs} == before   # run id, model and time unchanged
    rows = _evaluations(p, "alt")
    by_pass = {x.split("__")[-2]: x for x in main_runs}
    assert rows and all(x["evaluation"] == "reused" for x in rows)
    assert all(x["source_run_id"] == by_pass[x["pass"]] for x in rows)
    assert all(x["model"] == "fake" for x in rows)
    assert {x["call_id"] for x in rows} <= {str(c["call_id"]) for cs in before.values() for c in cs}
    assert all(x["evaluation"] == "new" and not x["source_run_id"] for x in _evaluations(p, "main"))
    head = re.split(r"\n\d+/\d+ ", r.output)[0]   # what is printed before the first progress line
    assert re.search(rf"\b{n}\b records? in scope", head), head
    assert re.search(rf"\b{2 * n}\b (identical )?evaluations? (can be )?reus", head), head
    assert re.search(r"\b0\b new calls?", head) and "Estimated cost: $0.0000–$0.0000 for 0 calls" in head, head


def test_legacy_calls_never_reused(counted):  # noqa: F811
    """Scenario "Any changed input prevents reuse": calls stored before this version (no eval_key) are never reused."""
    p, counter = counted
    p.config.protocol.screening.passes = 1
    p.save()
    run_protocol(p, DEV, "main")
    p.store.execute("UPDATE screen_calls SET eval_key=NULL")
    assert _main_then(p, counter, "alt") == (n_dev(p), [])


def test_passes_never_share_evaluation(counted):  # noqa: F811
    """Scenario "Any changed input prevents reuse": pass B never reuses a pass A call (plain runs and protocol runs)."""
    p, counter = counted
    n = n_dev(p)
    a = screen(p, DEV, pass_name="A")
    calls = counter["calls"]
    b = screen(p, DEV, pass_name="B", reuse_evaluations=True)
    assert counter["calls"] - calls == n and not reuse_rows(p, [b["run_id"]]) and a["run_id"] != b["run_id"]
    # protocol alt reuses main's calls only within the same pass
    run_protocol(p, DEV, "main")
    calls = counter["calls"]
    out = run_protocol(p, DEV, "alt", reuse_evaluations=True)
    rows = reuse_rows(p, out["runs"])
    assert counter["calls"] == calls and len(rows) == 2 * n
    src = {c["call_id"]: c["pass"] for c in p.store.query("SELECT call_id, pass FROM screen_calls")}
    assert all(src[x["source_call_id"]] == x["pass"] for x in rows)


def test_reused_eval_cost_counted_once(counted):  # noqa: F811
    """Scenario "Identical inputs are reused on request": `wider costs` counts each reused evaluation once, under
    main's run."""
    p, counter = counted
    cfg = fake_cfg(p)
    cfg.price_input_per_mtok, cfg.price_output_per_mtok = 1000.0, 2000.0
    p.save()
    run_protocol(p, DEV, "main")
    main_runs = runs_of(p, "main")
    before = cli(p, "costs", "--by", "run")
    run_protocol(p, DEV, "alt", reuse_evaluations=True)
    alt_runs = runs_of(p, "alt")
    assert reuse_rows(p, alt_runs)
    after = cli(p, "costs", "--by", "run")
    assert after.exit_code == 0, after.output
    assert after.output == before.output
    total = re.search(r"project total: ([\d,]+) calls, [\d,]+ tokens, \$([\d.]+)", after.output)
    assert int(total.group(1).replace(",", "")) == counter["calls"] and float(total.group(2)) > 0
    assert all(r in after.output for r in main_runs) and not any(r in after.output for r in alt_runs)
    logged = {r["run_ref"]: r["n"] for r in p.store.query("SELECT run_ref, COUNT(*) n FROM llm_usage GROUP BY run_ref")}
    assert sum(logged.get(r, 0) for r in main_runs) == counter["calls"]
    assert not any(r in logged for r in alt_runs)


# ====================================================================================================
# Result sets, part E: migration of a version-3 store by scope (Store.migrate + result_sets.migrate_legacy) and the
# ====================================================================================================
V3_COLS = "protocol, record_id, decision, votes, cell, labels, reason, decided_at"
V4_COLUMNS = {"llm_usage": ["cached_tokens", "cache_write_tokens"],
              "screen_calls": ["backend_hash", "provider", "model", "model_reported", "eval_key"],
              "screen_decisions": ["vote_calls", "decided_by", "origin"]}


def _as_v3(p, edit=None):
    """Close the project and rewrite its store as version 3 wrote it: no result-set tables or flags, no v4 columns.
    ``edit(con)`` changes the v3 content first (merging or renaming protocols, removing run links)."""
    p.close()
    con = sqlite3.connect(p.store_path)
    if edit:
        edit(con)
    for t in ("screen_result_sets", "screen_call_reuse", "backend_snapshots", "run_backend_changes"):
        con.execute(f"DROP TABLE {t}")
    for table, cols in V4_COLUMNS.items():
        for c in cols:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {c}")
    con.execute("DELETE FROM meta WHERE key IN ('result_sets_migrated', 'result_sets_migration_notes')")
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.commit()
    con.close()


def _rows(p, key):
    return [tuple(r) for r in p.store.query(f"SELECT {V3_COLS} FROM screen_decisions WHERE protocol=? ORDER BY record_id",
                                            (key,))]


def _history(p):
    return [r["summary"] for r in p.store.query("SELECT summary FROM history WHERE kind='result_set' ORDER BY change_id")]


def _run_files(p):
    """Every file under the run folders, with its bytes' hash."""
    return {str(f.relative_to(p.root)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted((p.root / "runs").rglob("*")) if f.is_file()}


def test_migrate_v4_result_sets(counted):  # noqa: F811
    """Older store: a v3 store with protocol main run once on the development sample gets one active set 1 with the
    same decisions, scope sample:development and its role, without a model call."""
    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    before = _rows(p, "main")
    assert before
    _as_v3(p)
    counter["calls"] = 0
    p = reload(p)
    assert p.store.get_meta("schema_version") == str(SCHEMA_VERSION)
    rows = set_rows(p)
    assert len(rows) == 1
    s = rows[0]
    assert (s["result_set"], s["key"], s["active"], s["source"]) == (1, "main", 1, "screening")
    assert (s["scope"], s["role"], s["mixed"]) == ("sample:development", "development", 0)
    assert _rows(p, "main") == before
    assert counter["calls"] == 0


def test_migrate_single_scope(counted):  # noqa: F811
    """Older store, single scope on the probability sample: set 1 keeps its decisions and runs, role validation, the
    migration is noted in the history, and `wider screen sets list main --json` shows it; no model call."""
    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:validation").exit_code == 0
    before = _rows(p, "main")
    runs = [r["run_id"] for r in p.store.query("SELECT run_id FROM runs ORDER BY run_id")]
    _as_v3(p)
    counter["calls"] = 0
    p = reload(p)
    s = set_rows(p)[0]
    assert (s["scope"], s["role"], s["active"]) == ("sample:validation", "validation", 1)
    assert sorted(x["run_id"] for x in json.loads(s["runs"])) == runs
    assert _rows(p, "main") == before
    assert any("main: set 1 kept as it was; scope sample:validation" in h for h in _history(p))
    p.close()
    r = cli(p, "screen", "sets", "list", "main", "--json")
    assert r.exit_code == 0, r.output
    lst = json.loads(r.output)
    assert len(lst) == 1 and lst[0]["active"] == 1 and lst[0]["scope"] == "sample:validation"
    assert lst[0]["n"] == len(before) and sorted(lst[0]["run_ids"]) == runs
    assert counter["calls"] == 0


def test_migrate_v4_renames_hash_key(counted):  # noqa: F811
    """A protocol name with # (older store): key main#2 becomes main-h2 in the decisions, the set row, the stage
    result headline, meta last_result:screen and last_report, and the questions naming it; the rename is in the
    history, and run ids, run folders and run_spec.json stay byte-identical."""
    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    runs_before = [tuple(r) for r in p.store.query("SELECT * FROM runs ORDER BY run_id")]
    files_before = _run_files(p)
    assert any(f.endswith("run_spec.json") for f in files_before)
    n_dec = len(_rows(p, "main"))
    rid = p.store.scalar("SELECT MIN(record_id) FROM screen_decisions WHERE protocol='main'")

    def to_hash_key(con):
        con.execute("UPDATE screen_decisions SET protocol='main#2' WHERE protocol='main'")
        for rid_, res in con.execute("SELECT result_id, result FROM stage_results WHERE stage='screen'").fetchall():
            con.execute("UPDATE stage_results SET result=? WHERE result_id=?",
                        (res.replace('"Protocol main:', '"Protocol main#2:'), rid_))
        last = json.loads(con.execute("SELECT value FROM meta WHERE key='last_result:screen'").fetchone()[0])
        last["headline"] = last["headline"].replace("Protocol main:", "Protocol main#2:")
        con.execute("UPDATE meta SET value=? WHERE key='last_result:screen'", (json.dumps(last),))
        con.execute("INSERT INTO meta(key, value) VALUES('last_report', ?)",
                    (json.dumps({"at": "2026-01-01T00:00:00+00:00", "protocol": "main#2"}),))
        con.execute("INSERT INTO questions(kind, stage, dedupe_key, record_id, ref, run_ref, title, prompt, options, "
                    "context, created_at) VALUES('screen_split', 'screen', ?, ?, 'main#2', 'main#2', 't', 'p', '[]', ?, "
                    "'2026-01-01T00:00:00+00:00')", (f"screen_split:main#2:{rid}", rid, json.dumps({"protocol": "main#2"})))

    _as_v3(p, to_hash_key)
    counter["calls"] = 0
    p = reload(p)
    st = p.store
    assert st.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol LIKE '%#%'") == 0
    assert len(_rows(p, "main-h2")) == n_dec
    s = set_rows(p, "main-h2")
    assert len(s) == 1 and (s[0]["key"], s[0]["result_set"], s[0]["active"]) == ("main-h2", 1, 1)
    assert s[0]["scope"] == "sample:development"   # the renamed headline still links its runs
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol LIKE '%#%' OR key LIKE '%#%'") == 0
    heads = [json.loads(r["result"])["headline"] for r in st.query(
        "SELECT result FROM stage_results WHERE stage='screen' AND action='protocol'")]
    assert heads and all(h.startswith("Protocol main-h2:") for h in heads)
    assert json.loads(st.get_meta("last_result:screen"))["headline"].startswith("Protocol main-h2:")
    assert json.loads(st.get_meta("last_report"))["protocol"] == "main-h2"
    q = st.one("SELECT dedupe_key, ref, run_ref, context FROM questions")
    assert (q["dedupe_key"], q["ref"], q["run_ref"]) == (f"screen_split:main-h2:{rid}", "main-h2", "main-h2")
    assert json.loads(q["context"])["protocol"] == "main-h2"
    assert any("renamed to 'main-h2'" in h for h in _history(p))
    assert [tuple(r) for r in st.query("SELECT * FROM runs ORDER BY run_id")] == runs_before
    assert _run_files(p) == files_before
    assert counter["calls"] == 0


def _two_scopes_v3(p):
    """A v3 store whose protocol `main` screened sample:development and then the corpus under the one key `main` (the
    corpus run overwrote the sample's rows). Returns each run's decisions, the corpus rows, run ids and usage counts."""
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    dev = decisions_of(p, "main")
    assert cli(p, "screen", "protocol", "corpus", "--name", "tmp").exit_code == 0
    corpus = decisions_of(p, "tmp")
    assert set(dev) < set(corpus) and len(corpus) == 40
    tmp_rows = [("main", *r[1:]) for r in _rows(p, "tmp")]
    run_ids = [r["run_id"] for r in p.store.query("SELECT run_id FROM runs ORDER BY started_at, run_id")]
    usage = {r[0]: r[1] for r in p.store.query("SELECT run_ref, COUNT(*) FROM llm_usage GROUP BY run_ref")}

    def merge(con):
        con.execute(f"INSERT OR REPLACE INTO screen_decisions({V3_COLS}) SELECT 'main', {V3_COLS.split(', ', 1)[1]} "
                    "FROM screen_decisions WHERE protocol='tmp'")
        con.execute("DELETE FROM screen_decisions WHERE protocol='tmp'")
        for rid_, res in con.execute("SELECT result_id, result FROM stage_results WHERE stage='screen'").fetchall():
            con.execute("UPDATE stage_results SET result=? WHERE result_id=?",
                        (res.replace('"Protocol tmp:', '"Protocol main:'), rid_))

    _as_v3(p, merge)
    return dev, corpus, tmp_rows, run_ids, usage


def test_migrate_multi_scope_splits_without_calls(counted):  # noqa: F811
    """A protocol run on two scopes (older store): set 1 keeps every row, stays active, scope mixed; sets 2
    (sample:development) and 3 (corpus) are re-derived from the stored calls with origin migrated; no model call, the
    migration is in the history, and `wider costs` counts each run once."""
    p, counter = counted
    dev, corpus, tmp_rows, run_ids, usage_before = _two_scopes_v3(p)
    assert usage_before and set(usage_before) == set(run_ids)
    counter["calls"] = 0
    p = reload(p)
    rows = set_rows(p)
    assert [r["result_set"] for r in rows] == [1, 2, 3]
    s1, s2, s3 = rows
    assert (s1["scope"], s1["active"], s1["key"]) == ("mixed", 1, "main")
    assert (s2["scope"], s2["source"], s2["active"], s2["role"]) == ("sample:development", "migrated", 0, "development")
    assert (s3["scope"], s3["source"], s3["active"], s3["role"]) == ("corpus", "migrated", 0, "corpus")
    assert _rows(p, "main") == tmp_rows
    assert decisions_of(p, s2["key"]) == dev
    assert decisions_of(p, s3["key"]) == corpus
    for key in (s2["key"], s3["key"]):
        origins = {json.loads(r["origin"])["kind"] for r in p.store.query(
            "SELECT origin FROM screen_decisions WHERE protocol=?", (key,))}
        assert origins == {"migrated"}
    assert p.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main' AND origin IS NOT NULL") == 0
    assert sorted({x["run_id"] for s in (s2, s3) for x in json.loads(s["runs"])}) == sorted(run_ids)
    assert any("set 2 for sample:development, set 3 for corpus" in h for h in _history(p))
    assert counter["calls"] == 0
    assert dict(p.store.query("SELECT run_ref, COUNT(*) FROM llm_usage GROUP BY run_ref")) == usage_before
    p.close()
    out = cli(p, "costs", "--by", "run").output
    for rid in usage_before:
        assert out.count(rid) == 1, rid


def test_migrate_unknown_scope_needs_answer(counted):  # noqa: F811
    """Unknown scope is asked for: runs of main cannot be found, so set 1 has scope unknown; `wider screen protocol
    corpus --name main` makes no call and names `wider screen sets scope main 1 SCOPE`; the route answers 409
    scope_needed."""
    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    before = _rows(p, "main")
    _as_v3(p, lambda con: con.execute("DELETE FROM stage_results WHERE stage='screen'"))
    counter["calls"] = 0
    p = reload(p)
    s = set_rows(p)[0]
    assert (s["scope"], s["active"]) == ("unknown", 1)
    assert _rows(p, "main") == before
    n_runs = p.store.scalar("SELECT COUNT(*) FROM runs")
    p.close()
    r = cli(p, "screen", "protocol", "corpus", "--name", "main")
    assert "wider screen sets scope main 1 SCOPE" in r.output, r.output
    resp = client(p).post("/api/jobs/screen", json={"scope": "corpus", "mode": "protocol", "protocol": "main",
                                                    "provider": "fake"})
    assert resp.status_code == 409, resp.text
    assert resp.json()["scope_needed"] == {"protocol": "main", "set": 1}
    assert "wider screen sets scope main 1 SCOPE" in resp.json()["detail"]
    p = reload(p)
    assert counter["calls"] == 0
    assert p.store.scalar("SELECT COUNT(*) FROM runs") == n_runs
    assert p.store.scalar("SELECT COUNT(*) FROM jobs") == 0
    assert set_rows(p)[0]["scope"] == "unknown" and _rows(p, "main") == before


def test_set_scope_checks_records(counted):  # noqa: F811
    """Unknown scope is asked for: `wider screen sets scope main 1 SCOPE` is refused when set 1 holds records outside
    that scope, and otherwise sets it and is journalled with its author."""
    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    _as_v3(p, lambda con: con.execute("DELETE FROM stage_results WHERE stage='screen'"))
    counter["calls"] = 0
    p = reload(p)
    assert set_rows(p)[0]["scope"] == "unknown"
    n_hist = p.store.scalar("SELECT COUNT(*) FROM history")
    p.close()
    r = cli(p, "screen", "sets", "scope", "main", "1", "sample:validation", "--author", "Rev Tester")
    assert r.exit_code == 1, r.output
    assert "are not in sample:validation" in r.output
    p = reload(p)
    assert set_rows(p)[0]["scope"] == "unknown" and p.store.scalar("SELECT COUNT(*) FROM history") == n_hist
    p.close()
    r = cli(p, "screen", "sets", "scope", "main", "1", "corpus", "--author", "Rev Tester")
    assert r.exit_code == 0, r.output
    assert "main set 1: scope corpus, role corpus" in r.output
    p = reload(p)
    s = set_rows(p)[0]
    assert (s["scope"], s["role"]) == ("corpus", "corpus")
    h = p.store.one("SELECT author, summary FROM history ORDER BY change_id DESC LIMIT 1")
    assert h["author"] == "Rev Tester" and h["summary"] == "result set: main set 1 scope set to corpus"
    assert counter["calls"] == 0


def test_migrate_v4_set_survives_version_reset(tmp_path, counted):  # noqa: F811
    """A second v4 migration changes nothing and does not fail; on a project, set 2 of main survives a version reset
    and a renamed `#` key is not renamed a second time."""
    from wider.core.store import Store

    db = tmp_path / "store.sqlite"
    _v3_store(db)
    st = Store(db)
    before = {t: st.columns(t) for t in ("llm_usage", "screen_calls", "screen_decisions")}
    st._migrate_v4()
    assert {t: st.columns(t) for t in before} == before
    assert st.scalar("SELECT COUNT(*) FROM llm_usage") == 1
    st.close()
    Store(db).close()

    p, counter = counted
    assert cli(p, "screen", "protocol", "sample:development").exit_code == 0
    _as_v3(p, lambda con: con.execute(f"INSERT INTO screen_decisions({V3_COLS}) SELECT 'old#2', "
                                      f"{V3_COLS.split(', ', 1)[1]} FROM screen_decisions WHERE protocol='main'"))
    p = reload(p)
    assert sorted(r["protocol"] for r in p.store.query("SELECT DISTINCT protocol FROM screen_result_sets")) == \
        ["main", "old-h2"]
    p.close()
    r = cli(p, "screen", "protocol", "corpus", "--new-set", "--set-label", "second")
    assert r.exit_code == 0, r.output
    p = reload(p)
    sets_before = set_rows(p)
    assert [s["result_set"] for s in sets_before] == [1, 2] and sets_before[1]["key"] == "main#2"
    dec_before = {k: _rows(p, k) for k in ("main", "main#2", "old-h2")}
    assert dec_before["main#2"]
    hist_before = _history(p)
    p.close()
    con = sqlite3.connect(p.store_path)   # a version reset (e.g. a rollback that kept the file)
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.commit()
    con.close()
    calls = counter["calls"]
    p = reload(p)
    assert p.store.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert set_rows(p) == sets_before
    assert set_rows(p, "main#2") == [] and set_rows(p, "old-h2-h2") == []
    assert {k: _rows(p, k) for k in dec_before} == dec_before
    assert _history(p) == hist_before
    assert counter["calls"] == calls


def test_migrate_multi_scope_in_info(counted):  # noqa: F811
    """A protocol run on two scopes: the migration is shown by `wider info`."""
    p, _ = counted
    _two_scopes_v3(p)
    reload(p).close()
    out = cli(p, "info").output
    assert "set 2 for sample:development" in out and "set 3 for corpus" in out
