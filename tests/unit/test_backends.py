"""LLM / embedding backend chooser: add, remove, default and per-stage overrides round-trip through ProjectConfig."""
import pytest

from wider.core import backends
from wider.core.project import Project


def test_provider_add_remove_default_roundtrip(tmp_path):
    p = Project.init(tmp_path / "r")
    n0 = len(p.config.providers)
    cfg, _ = backends.add_provider(p, "local", kind="openai_compat", model="m-1", base_url="http://127.0.0.1:1234/v1",
                               api_key_env="LOCAL_KEY", set_default=True)
    assert cfg.name == "local" and cfg.api_key_env == "LOCAL_KEY"
    p2 = Project.load(tmp_path / "r")
    assert len(p2.config.providers) == n0 + 1 and p2.config.default_provider == "local"
    assert p2.provider("local").base_url == "http://127.0.0.1:1234/v1"
    # editing keeps the name unique and updates in place
    backends.add_provider(p2, "local", kind="openai_compat", model="m-2")
    p3 = Project.load(tmp_path / "r")
    assert len(p3.config.providers) == n0 + 1 and p3.provider("local").model == "m-2"
    backends.remove_provider(p3, "local")
    p4 = Project.load(tmp_path / "r")
    assert all(pr.name != "local" for pr in p4.config.providers)
    assert p4.config.default_provider != "local" and p4.config.default_provider  # fell back to another provider
    with pytest.raises(KeyError):
        backends.remove_provider(p4, "local")


def test_provider_rows_never_expose_key_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_KEY_VAR", "secret-value")
    p = Project.init(tmp_path / "r")
    backends.add_provider(p, "x", kind="openai_compat", model="m", api_key_env="SOME_KEY_VAR")
    row = next(r for r in backends.provider_rows(p) if r["name"] == "x")
    assert row["key_set"] is True and row["api_key_env"] == "SOME_KEY_VAR"
    assert "secret-value" not in str(row) and "secret-value" not in (tmp_path / "r" / "project.yaml").read_text()


def test_cli_agent_needs_command_and_parses_json(tmp_path):
    p = Project.init(tmp_path / "r")
    with pytest.raises(ValueError):
        backends.add_provider(p, "agent", kind="cli_agent")
    cfg, _ = backends.add_provider(p, "agent", kind="cli_agent", command='["tool", "-p", "--model", "{model}", "{system_file}"]')
    assert cfg.command == ["tool", "-p", "--model", "{model}", "{system_file}"]
    # only the placeholders the adapter replaces; an old {prompt} would reach the tool literally
    with pytest.raises(ValueError, match=r"\{prompt\}.*\{model\}, \{system_file\}, \{system_toml\}, \{schema_file\}, \{system\}, \{user\}"):
        backends.add_provider(p, "old", kind="cli_agent", command='["tool", "-p", "{prompt}"]')
    assert backends.add_provider(p, "json", kind="cli_agent", command=["tool", '{"a": 1}', "{user}"])[0].command[1] == '{"a": 1}'
    with pytest.raises(ValueError):
        backends.add_provider(p, "bad", kind="unknown_kind")


def test_stage_override_and_provider_for(tmp_path):
    p = Project.init(tmp_path / "r")
    backends.add_provider(p, "a", kind="openai_compat", model="a-model", set_default=True)
    backends.add_provider(p, "b", kind="openai_compat", model="b-model")
    backends.set_stage_provider(p, "screen", "b")
    p2 = Project.load(tmp_path / "r")
    assert p2.config.stage_providers == {"screen": "b"}
    assert p2.provider_for("screen").model == "b-model"
    assert p2.provider_for("construct").model == "a-model"
    with pytest.raises(ValueError):
        backends.set_stage_provider(p2, "nosuchstage", "b")
    with pytest.raises(KeyError):
        backends.set_stage_provider(p2, "screen", "zzz")
    backends.remove_provider(p2, "b")
    p3 = Project.load(tmp_path / "r")
    assert "screen" not in p3.config.stage_providers
    assert p3.provider_for("screen").model == "a-model"
    backends.set_stage_provider(p3, "construct", None)
    assert Project.load(tmp_path / "r").config.stage_providers == {}


def test_embedding_add_remove_default(tmp_path):
    p = Project.init(tmp_path / "r")
    cfg, _ = backends.add_embedding(p, "small", kind="sentence_transformers", model="org/small-model", set_default=True)
    assert cfg.kind == "sentence_transformers"
    p2 = Project.load(tmp_path / "r")
    assert p2.config.default_embedding == "small"
    row = next(r for r in backends.embedding_rows(p2) if r["name"] == "small")
    assert row["downloaded"] in (True, False, None) and "size_mb" in row
    backends.add_embedding(p2, "api", kind="openai_compat", model="emb", base_url="http://127.0.0.1:1/v1",
                           api_key_env="EMB_KEY", dim=256)
    assert Project.load(tmp_path / "r").embedding("api").dim == 256
    backends.set_default_embedding(p2, "api")
    backends.remove_embedding(p2, "small")
    p3 = Project.load(tmp_path / "r")
    assert p3.config.default_embedding == "api" and all(e.name != "small" for e in p3.config.embeddings)
    with pytest.raises(KeyError):
        backends.set_default_embedding(p3, "small")


def _add(tmp_path, extra):
    p = Project.init(tmp_path / "r", exist_ok=True)
    try:
        return backends.add_provider(p, "g", "openai_compat", model="m", base_url="https://gw.example.org/v1",
                                     extra=extra)[0]
    finally:
        p.close()


def test_sensitive_header_needs_env(tmp_path):
    for name in ("X-Gateway-Token", "X-Secret-Id", "Cookie", "X-Auth-Team"):
        with pytest.raises(ValueError, match=f"header {name} is sensitive") as e:
            _add(tmp_path, {"headers": {name: "plain-value"}})
        assert "plain-value" not in str(e.value)
    with pytest.raises(ValueError, match="X-Org is sensitive"):
        _add(tmp_path, {"headers": {"X-Org": "lab"}, "sensitive_headers": ["x-org"]})
    cfg = _add(tmp_path, {"headers": {"X-Gateway-Token": "{env:GW_TOKEN}", "X-Team": "lab"}})
    assert cfg.extra["headers"]["X-Gateway-Token"] == "{env:GW_TOKEN}"


def test_max_output_tokens_must_be_whole_number(tmp_path):
    # a junk limit was saved and then ignored (or refused every run); it is refused when the backend is saved
    for bad in ("abc", -5, 0, 2.5, True):
        with pytest.raises(ValueError, match="extra.max_output_tokens must be a whole number"):
            _add(tmp_path, {"max_output_tokens": bad})
    assert "gw.example.org" not in (tmp_path / "r" / "project.yaml").read_text()  # nothing was saved
    assert _add(tmp_path, {"max_output_tokens": 4096}).extra["max_output_tokens"] == 4096


def test_extra_env_key_refused(tmp_path):
    p = Project.init(tmp_path / "r")
    cmd = ["mycli", "--model", "{model}"]
    for name in ("MY_API_KEY", "SERVICE_TOKEN", "DB_PASSWORD", "AWS_SECRET", "GCP_CREDENTIALS"):
        with pytest.raises(ValueError, match=f"extra.env.{name}") as e:
            backends.add_provider(p, "c", "cli_agent", command=cmd, extra={"env": {name: "abc123"}})
        assert "abc123" not in str(e.value)
    cfg, _ = backends.add_provider(p, "c", "cli_agent", command=cmd,
                                   extra={"env": {"MY_API_KEY": "{env:MY_API_KEY}", "NO_COLOR": "1"}})
    assert cfg.extra["env"]["MY_API_KEY"] == "{env:MY_API_KEY}"
    p.close()


def test_extra_auth_headers_refused(tmp_path):
    for name in ("Authorization", "api-key", "X-API-Key"):
        with pytest.raises(ValueError, match="extra.auth to none"):
            _add(tmp_path, {"headers": {name: "{env:GW_TOKEN}"}})
    cfg = _add(tmp_path, {"auth": "none", "headers": {"Authorization": "{env:GW_TOKEN}"}})
    assert cfg.extra["headers"]["Authorization"] == "{env:GW_TOKEN}"
    with pytest.raises(ValueError, match="sensitive"):   # auth: none still needs a reference, not a value
        _add(tmp_path, {"auth": "none", "headers": {"Authorization": "Bearer abc"}})


def test_extra_secret_values_refused(tmp_path):
    long_run = "A1b2" * 10
    cases = [({"body": {"api_token": "sk-" + "x" * 12}}, "extra.body.api_token"),
             ({"body": {"nested": [{"v": "Bearer abc"}]}}, "extra.body.nested[0].v"),
             ({"note": long_run}, "extra.note"),
             ({"headers": {"X-Team": "AIza" + "q" * 20}}, "extra.headers.X-Team"),
             ({"options": {"k": "ghp_" + "z" * 20}}, "extra.options.k")]
    for extra, path in cases:
        with pytest.raises(ValueError, match=path.replace("[", r"\[").replace("]", r"\]")) as e:
            _add(tmp_path, extra)
        value = str(e.value)
        assert "sk-x" not in value and long_run not in value and "AIza" not in value and "ghp_" not in value
    ok = _add(tmp_path, {"body": {"reasoning_effort": "low"}, "deployment": "dep1",
                         "note": "https://example.org/a-very-long-path-segment-that-is-not-a-key-0123456789"})
    assert ok.extra["deployment"] == "dep1"


def test_update_provider_keeps_fields(tmp_path):
    p = Project.init(tmp_path / "r")
    backends.add_provider(p, "a", kind="openai_compat", model="m-1", base_url="http://127.0.0.1:1234/v1",
                          api_key_env="LOCAL_KEY", timeout_s=900, temperature=0.2, max_concurrency=2,
                          price_input_per_mtok=1.5, price_output_per_mtok=3.0,
                          extra={"think": False, "omit_params": ["seed"]})
    backends.update_provider(p, "a", model="m-2", extra={"think": None, "send_seed": False})
    a = Project.load(tmp_path / "r").provider("a")
    assert a.model == "m-2" and a.base_url == "http://127.0.0.1:1234/v1" and a.api_key_env == "LOCAL_KEY"
    assert (a.timeout_s, a.temperature, a.max_concurrency) == (900, 0.2, 2)
    assert (a.price_input_per_mtok, a.price_output_per_mtok) == (1.5, 3.0)
    assert a.extra == {"omit_params": ["seed"], "send_seed": False}   # merged key by key, None removed a key
    backends.update_provider(p, "a", api_key_env="")
    assert Project.load(tmp_path / "r").provider("a").api_key_env is None
    # add with an existing name still replaces and names what it reset
    cfg, reset = backends.add_provider(p, "a", kind="openai_compat", model="m-3")
    assert cfg.timeout_s == 300 and {"timeout_s", "temperature", "max_concurrency", "base_url",
                                     "price_input_per_mtok"} <= set(reset)
    assert "model" not in reset


def test_update_provider_validates(tmp_path):
    p = Project.init(tmp_path / "r")
    backends.add_provider(p, "a", kind="openai_compat", model="m", timeout_s=600)
    before = (tmp_path / "r" / "project.yaml").read_text()
    bad = [dict(name="b"), dict(kind="ollama"), dict(timeout_s=0), dict(max_concurrency=-1),
           dict(price_input_per_mtok=-1.0), dict(preset="no-such-preset"), dict(extra={"output": "yaml"}),
           dict(extra={"auth": "basic"}), dict(extra={"system_channel": "stdin"}),
           dict(extra={"headers": {"Authorization": "{env:X}"}}), dict(extra={"env": {"MY_TOKEN": "abc"}}),
           dict(colour="red")]
    for change in bad:
        with pytest.raises(ValueError):
            backends.update_provider(p, "a", **change)
    with pytest.raises(ValueError, match="placeholder"):
        backends.add_provider(p, "c", kind="cli_agent", command=["tool", "{user}"])[0]
        backends.update_provider(p, "c", command=["tool", "{prompt}"])
    backends.remove_provider(p, "c")
    assert (tmp_path / "r" / "project.yaml").read_text() == before   # a refusal saved nothing
    with pytest.raises(KeyError):
        backends.update_provider(p, "missing", model="m")


def test_update_embedding_keeps_fields(tmp_path):
    p = Project.init(tmp_path / "r")
    backends.add_embedding(p, "e", kind="openai_compat", model="emb", base_url="http://127.0.0.1:1/v1",
                           api_key_env="EMB_KEY", dim=256, batch_size=16, price_input_per_mtok=0.02)
    backends.update_embedding(p, "e", model="emb-2")
    e = Project.load(tmp_path / "r").embedding("e")
    assert (e.model, e.dim, e.batch_size, e.price_input_per_mtok, e.base_url, e.api_key_env) == (
        "emb-2", 256, 16, 0.02, "http://127.0.0.1:1/v1", "EMB_KEY")
    before = (tmp_path / "r" / "project.yaml").read_text()
    for change in (dict(kind="ollama"), dict(name="f"), dict(batch_size=0), dict(dim=-3),
                   dict(price_input_per_mtok=-1.0), dict(model="")):
        with pytest.raises(ValueError):
            backends.update_embedding(p, "e", **change)
    assert (tmp_path / "r" / "project.yaml").read_text() == before
    _, reset = backends.add_embedding(p, "e", kind="openai_compat", model="emb")
    assert {"dim", "batch_size", "price_input_per_mtok", "base_url", "api_key_env"} <= set(reset)


def test_add_from_preset(tmp_path):
    p = Project.init(tmp_path / "r")
    assert "ollama" in [x.name for x in p.config.providers]
    cfg, reset = backends.add_provider(p, preset="ollama", model="m")
    assert cfg.name == "ollama-2" and cfg.kind == "ollama" and cfg.preset == "ollama" and reset == []
    cfg, _ = backends.add_provider(p, preset="ollama", model="m")
    assert cfg.name == "ollama-3"
    cfg, _ = backends.add_provider(p, "mine", preset="ollama", model="m", timeout_s=120)
    assert cfg.name == "mine" and cfg.timeout_s == 120
    with pytest.raises(ValueError, match="choose a model"):
        backends.add_provider(p, "o", preset="openai")
    with pytest.raises(ValueError, match="unknown preset"):
        backends.add_provider(p, "x", preset="no-such-preset")
    assert "x" not in [x.name for x in Project.load(tmp_path / "r").config.providers]
    assert backends.parse_extra_pairs(["think=false", "deployment=dep1", "omit_params=[\"seed\"]", "auth="]) == {
        "think": False, "deployment": "dep1", "omit_params": ["seed"], "auth": None}
    with pytest.raises(ValueError, match="key=value"):
        backends.parse_extra_pairs(["think"])


def test_backend_name_with_slash_is_refused(tmp_path):
    """ui-tester F1: a name with '/' was saved but its Edit, Probe and Remove answered 404."""
    from wider.core.backends import add_embedding, add_provider
    from wider.core.project import Project

    p = Project.init(tmp_path / "r")
    for bad in ("a/b", "<b>x</b>", "a\\b", "..", "a\nb"):
        with pytest.raises(ValueError, match="backend name"):
            add_provider(p, bad, kind="fake")
        with pytest.raises(ValueError, match="backend name"):
            add_embedding(p, bad, kind="fake", model="m")
    assert not any(x.name in ("a/b", "<b>x</b>") for x in Project.load(p.root).config.providers)
    add_provider(p, "lab-model_2.1", kind="fake")
    assert "lab-model_2.1" in [x.name for x in Project.load(p.root).config.providers]


def test_key_value_as_api_key_env_is_refused(tmp_path):
    """risk-checker R1: a key value given as --api-key-env was saved to project.yaml, consent parts and the journal."""
    from wider.core.backends import add_embedding, add_provider, update_embedding, update_provider
    from wider.core.project import Project

    p = Project.init(tmp_path / "r")
    keys = ("sk-proj-SYNTHETICabcdefghijklmnopqrstuvwxyz0123", "Xq7Lm2Pz9Rt4Vw8Yb3Nc6Hd1Kf5Gj0Ts",
            "AIzaSyntheticSyntheticSynthetic", "gsk_synthetic", "has space", "1ABC")
    before = (p.root / "project.yaml").read_text()
    for k in keys:
        for call in (lambda: add_provider(p, "o1", preset="openai", model="m", api_key_env=k),
                     lambda: add_provider(p, "o2", kind="openai_compat", model="m", api_key_env=k),
                     lambda: update_provider(p, "openai", api_key_env=k),
                     lambda: add_embedding(p, "e1", kind="openai", model="m", api_key_env=k),
                     lambda: update_embedding(p, "oai-emb" if any(e.name == "oai-emb" for e in p.config.embeddings)
                                              else p.config.embeddings[0].name, api_key_env=k)):
            with pytest.raises(ValueError, match="api_key_env") as e:
                call()
            assert k not in str(e.value)
    after = (p.root / "project.yaml").read_text()
    assert after == before and not any(k in after for k in keys)
    # ordinary variable names, long ones included, are accepted
    for ok in ("OPENAI_API_KEY", "my_lab_key", "AZURE_OPENAI_API_KEY_FOR_RESEARCH_LAB_2"):
        cfg, _ = add_provider(p, "o3", kind="openai_compat", model="m", api_key_env=ok)
        assert cfg.api_key_env == ok


def _ollama_project(root):
    p = Project.init(root)
    backends.add_provider(p, "o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434")
    return Project.load(root)


def test_rows_make_no_request(tmp_path, monkeypatch):
    from tests.unit.helpers import no_requests

    p = _ollama_project(tmp_path / "r")
    seen = no_requests(monkeypatch)
    row = next(r for r in backends.provider_rows(p) if r["name"] == "o")
    assert row["locality"] == "unknown" and row["locality_source"] == "not_checked"
    assert row["locality_label"] == "unknown, press Check" and row["last_check"] is None
    assert all("locality" in r and "destination" in r and "last_check" in r for r in backends.embedding_rows(p))
    assert seen == []


def test_failed_check_of_unknown_ollama_reads_last_check_failed(tmp_path):
    """A failed check cannot tell where an Ollama model runs; its label says so instead of "press Check", until the
    model or address changes (M37)."""
    p = _ollama_project(tmp_path / "r")
    backends.record_probe(p, "llm", p.provider("o"), False, "unknown", "HTTP 404: model not found")
    row = next(r for r in backends.provider_rows(p) if r["name"] == "o")
    assert row["locality"] == "unknown" and row["locality_label"] == "unknown, last check failed"
    assert row["last_check"]["ok"] is False
    backends.update_provider(p, "o", model="qwen3:14b")
    row = next(r for r in backends.provider_rows(Project.load(tmp_path / "r")) if r["name"] == "o")
    assert row["locality_label"] == "unknown, press Check"


def _ollama_answers_sum(monkeypatch, model="qwen3:8b"):
    """Ollama on 127.0.0.1 lists ``model`` (no remote fields, so it runs here) and answers the probe question."""
    import httpx

    from wider.core import detect

    monkeypatch.setattr(detect, "http_get",
                        lambda url, timeout=detect.HTTP_TIMEOUT_S: (200, {"models": [{"name": model, "model": model}]}))
    detect._ROUTING_CACHE.clear()
    real = httpx.AsyncClient
    monkeypatch.setattr("wider.providers.ollama.httpx.AsyncClient", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"model": model, "message": {"content": '{"status": "ok", "sum": 42}'},
                                              "prompt_eval_count": 9, "eval_count": 3})), **kw))


def test_probe_records_last_check(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    root = tmp_path / "r"
    _ollama_project(root)
    _ollama_answers_sum(monkeypatch)
    runner = CliRunner()
    r = runner.invoke(cli_app, ["providers", "probe", "o", "--dir", str(root)])
    assert r.exit_code == 0, r.output
    row = next(x for x in backends.provider_rows(Project.load(root)) if x["name"] == "o")
    assert row["locality"] == "local" and row["locality_source"] == "last_check"
    assert row["last_check"]["ok"] is True and row["last_check"]["at"]
    first = row["last_check"]["at"]
    # the same through the page's Check button
    r = TestClient(create_app(Project.load(root))).post("/api/providers/o/probe")
    assert r.status_code == 200, r.text
    row = next(x for x in backends.provider_rows(Project.load(root)) if x["name"] == "o")
    assert row["locality"] == "local" and row["last_check"]["ok"] is True and row["last_check"]["at"] >= first
    # a new model is a backend never checked
    r = runner.invoke(cli_app, ["providers", "edit", "o", "--model", "qwen3:14b", "--dir", str(root)])
    assert r.exit_code == 0, r.output
    row = next(x for x in backends.provider_rows(Project.load(root)) if x["name"] == "o")
    assert row["locality_label"] == "unknown, press Check"
