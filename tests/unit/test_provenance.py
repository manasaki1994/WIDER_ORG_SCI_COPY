import json

import pytest

from tests.unit.test_screen import (
    _responder,
    project,  # noqa: F401  (the screened synthetic project fixture)
)
from wider.core.backends import update_provider
from wider.core.jobs import Checkpoint, batched
from wider.core.jsonl import append, read_all
from wider.core.provenance import RunSpec, next_version, sha256_json, sha256_text
from wider.providers import cli_agent
from wider.providers import fake as fakemod
from wider.providers.base import NOT_RECORDED
from wider.providers.cli_agent import CliAgentProvider
from wider.providers.presets import config_from_preset
from wider.providers.registry import make_provider


def test_hashes_are_stable():
    assert sha256_text("a") == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"
    assert sha256_json({"b": 1, "a": 2}) == sha256_json({"a": 2, "b": 1})


def test_runspec_hash_ignores_time(tmp_path):
    a = RunSpec(kind="screen", inputs={"prompt": "x"}, settings={"t": 0})
    b = RunSpec(kind="screen", inputs={"prompt": "x"}, settings={"t": 0})
    assert a.spec_hash() == b.spec_hash()
    p = a.write(tmp_path / "run")
    assert p.exists() and "spec_hash" in p.read_text()


def test_next_version(tmp_path):
    assert next_version(tmp_path, ".yaml") == 1
    (tmp_path / "v1.yaml").write_text("")
    (tmp_path / "v3.yaml").write_text("")
    assert next_version(tmp_path, ".yaml") == 4


def test_jsonl_tolerates_truncated_line(tmp_path):
    f = tmp_path / "r.jsonl"
    append(f, {"k": 1})
    append(f, {"k": 2})
    f.write_text(f.read_text() + '{"k": 3, "trunc')
    assert [r["k"] for r in read_all(f)] == [1, 2]


def test_checkpoint_resume(tmp_path):
    c = Checkpoint(tmp_path / "ck.json")
    c.mark("a")
    c.save()
    c2 = Checkpoint(tmp_path / "ck.json")
    assert c2.pending(["a", "b"], key=str) == ["b"]
    assert list(batched([1, 2, 3], 2)) == [[1, 2], [3]]


# ----- backend snapshot of a run (llm-backend-choice, design Decision 8) -----

def test_cli_identity_hides_home_and_records_version(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "bin").mkdir(parents=True)
    exe = home / "bin" / "fakeclaude"
    exe.write_text("#!/bin/sh\necho 9.9.9\n")
    exe.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli_agent, "_VERSIONS", {})
    cfg = config_from_preset("claude-code", name="a", model="m")
    cfg = cfg.model_copy(update={"command": [str(exe), *cfg.command[1:]]})
    ident = CliAgentProvider(cfg).identity()
    assert ident["preset"] == "claude-code" and ident["version"] == "9.9.9"
    assert ident["command"][0] == "~/bin/fakeclaude" and ident["executable"] == "~/bin/fakeclaude"
    assert ident["destination"] and ident["system_channel"] == "file"
    spec = RunSpec(kind="screen", inputs={}, settings={}, provider=ident)
    text = spec.write(tmp_path / "run").read_text()
    assert str(home) not in text and "9.9.9" in text


def test_extra_in_identity_and_hash(monkeypatch):
    from wider.core.models import ProviderConfig

    def spec(extra):
        cfg = ProviderConfig(name="a", kind="openai_compat", model="m", base_url="http://127.0.0.1:9/v1", extra=extra)
        return RunSpec(kind="screen", inputs={}, settings={}, provider=make_provider(cfg).identity())

    a, b = spec({"json_mode": "json"}), spec({"json_mode": "schema"})
    assert a.spec_hash() != b.spec_hash()
    ident = spec({"headers": {"X-Key": "{env:SOME_HEADER_VAR}", "X-Team": "plain"}}).provider
    assert ident["extra"]["headers"] == {"X-Key": "{env:SOME_HEADER_VAR}", "X-Team": "plain"}
    cli = config_from_preset("gemini-cli", name="g", model="m")
    cli = cli.model_copy(update={"extra": {**cli.extra, "env": {"GEMINI_SYSTEM_MD": "{system_file}", "OTHER": "abc"}}})
    env = CliAgentProvider(cli).identity()["extra"]["env"]
    assert env == {"GEMINI_SYSTEM_MD": "{system_file}", "OTHER": NOT_RECORDED}


@pytest.fixture
def screened(project):  # noqa: F811
    return project


def _screen_fake(p, monkeypatch):
    from wider.screen.engine import screen

    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    res = screen(p, "sample:development", pass_name="A")
    run_dir = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"]
    return json.loads((run_dir / "run_spec.json").read_text()), run_dir


def test_snapshot_survives_later_edit(screened, monkeypatch):
    p = screened
    update_provider(p, "fake", timeout_s=900)
    before, run_dir = _screen_fake(p, monkeypatch)
    assert before["provider"]["timeout_s"] == 900
    update_provider(p, "fake", timeout_s=60)
    after = json.loads((run_dir / "run_spec.json").read_text())
    assert after["provider"]["timeout_s"] == 900 and after["spec_hash"] == before["spec_hash"]
    assert p.provider("fake").timeout_s == 60


def test_key_value_never_recorded(screened, monkeypatch):
    p = screened
    from wider.recall.report import share_bundle

    secret, header_secret = "sk-synthetic-0000-secret", "hdr-synthetic-1111-secret"
    monkeypatch.setenv("SOME_KEY_VAR", secret)
    monkeypatch.setenv("SOME_HEADER_VAR", header_secret)
    update_provider(p, "fake", api_key_env="SOME_KEY_VAR", extra={"headers": {"X-Key": "{env:SOME_HEADER_VAR}"}})
    spec, run_dir = _screen_fake(p, monkeypatch)
    assert spec["provider"]["api_key_env"] == "SOME_KEY_VAR"
    assert spec["provider"]["extra"]["headers"]["X-Key"] == "{env:SOME_HEADER_VAR}"
    bundle = share_bundle(p)
    texts = [(p.root / "project.yaml").read_text(), (run_dir / "run_spec.json").read_text(),
             (run_dir / "decisions.csv").read_text(),
             json.dumps([dict(r) for r in p.store.query("SELECT * FROM llm_usage")])]
    texts += [f.read_text(errors="ignore") for f in bundle.rglob("*") if f.is_file()]
    for t in texts:
        assert secret not in t and header_secret not in t


def test_experimental_marked(screened, monkeypatch):
    """A run on a backend whose preset is experimental carries "experimental backend: not validated" on the run row,
    the result card, the models summary (CLI, API), decisions.csv, the evaluations export, the bundle and the
    methods line (Decision 20)."""
    import csv

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.stage_result import history
    from wider.recall.outputs import methods_draft
    from wider.recall.report import share_bundle
    from wider.screen.engine import EXPERIMENTAL_NOTE
    from wider.screen.export import export_protocol
    from wider.screen.models_summary import by_model
    from wider.screen.protocol import run_protocol
    from wider.ui.app import create_app

    p = screened
    update_provider(p, "fake", preset="gemini-cli")   # an experimental preset; the calls still go to the fake backend
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder({"calls": 0})))
    out = run_protocol(p, "sample:development", "main")
    assert out["experimental"] == EXPERIMENTAL_NOTE
    card = history(p, "screen")[0]
    assert EXPERIMENTAL_NOTE in json.dumps(card)
    c = TestClient(create_app(p))
    runs = c.get("/api/screen").json()["runs"]
    assert runs and all(r["experimental"] == EXPERIMENTAL_NOTE for r in runs)
    s = by_model(p, protocol="main")
    assert s["experimental"] and s["note"] == EXPERIMENTAL_NOTE and s["models"][0]["note"] == EXPERIMENTAL_NOTE
    assert c.get("/api/screen/models", params={"protocol": "main"}).json()["note"] == EXPERIMENTAL_NOTE
    d = str(p.root)
    assert "(experimental)" in CliRunner().invoke(cli_app, ["screen", "models", "--protocol", "main", "--dir", d],
                                                  env={"COLUMNS": "300"}).output
    assert EXPERIMENTAL_NOTE in CliRunner().invoke(cli_app, ["screen", "runs", "--dir", d],
                                                   env={"COLUMNS": "300"}).output
    run_dir = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (out["runs"][0],))["dir"]
    with open(run_dir / "decisions.csv", newline="", encoding="utf-8") as f:
        assert {r["experimental"] for r in csv.DictReader(f)} == {EXPERIMENTAL_NOTE}
    ex = export_protocol(p, "main")
    with open(p.root / ex["evaluations"], newline="", encoding="utf-8") as f:
        assert {r["experimental"] for r in csv.DictReader(f)} == {EXPERIMENTAL_NOTE}
    b = share_bundle(p)
    with open(b / "screen_decisions.csv", newline="", encoding="utf-8") as f:
        assert {r["experimental"] for r in csv.DictReader(f)} == {EXPERIMENTAL_NOTE}
    assert EXPERIMENTAL_NOTE in methods_draft(p, "main")
