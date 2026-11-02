"""Find backends on this computer and list a backend's models (wider.core.detect, design Decision 5). Everything is
injected or mocked: fake executables in tmp_path, a fake loopback server through ``http_get``, httpx MockTransport for
model lists, synthetic env values. Nothing real is contacted."""
import builtins
import json

import httpx
import pytest

from wider.core import detect as D
from wider.core.detect import http_get as REAL_HTTP_GET  # conftest stubs detect.http_get; keep the real one
from wider.core.models import ProviderConfig
from wider.providers.presets import config_from_preset, preset

FAKE_KEY = "synthetic-test-value"
REAL_CLIENT = httpx.AsyncClient   # one module object: a second patch must not wrap the first


def rows_by_id(rows):
    return {r["preset"]: r for r in rows}


def server(answers):
    """A fake loopback server: ``answers`` maps "port/path" to (status, json); everything else is closed."""
    seen = []

    def get(url):
        seen.append(url)
        assert url.startswith("http://127.0.0.1:"), url
        return answers.get(url.removeprefix("http://127.0.0.1:"))
    get.seen = seen
    return get


def exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(0o755)
    return str(path)


def run_detect(tmp_path, *, which=None, env=None, get=None, exists=None, root=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return D.detect(env=env or {}, which=which or (lambda n: None), run_version=lambda argv: f"{argv[0]} 1.2.3",
                    http_get=get or server({}), exists=exists or (lambda p: False), home=str(home),
                    bundle_root=str(root or tmp_path / "root"))


def test_detect_cli_and_local_server(tmp_path):
    claude = exe(tmp_path / "bin" / "claude")
    get = server({"11434/api/tags": (200, {"models": [{"name": "qwen3:8b"}, {"name": "gpt-oss:120b-cloud"}]}),
                  "1234/v1/models": (200, {"data": [{"id": "local-m"}]})})
    rows = rows_by_id(run_detect(tmp_path, which=lambda n: claude if n == "claude" else None, get=get))
    c = rows["claude-code"]
    assert c["found"] and c["where"] == claude and c["version"].endswith("1.2.3") and c["family"] == "cli"
    assert not rows["codex"]["found"] and rows["codex"]["where"] is None
    o = rows["ollama"]
    assert o["found"] and o["server"] == "127.0.0.1:11434" and o["models"] == ["qwen3:8b", "gpt-oss:120b-cloud"]
    assert [(m["id"], m["locality"]) for m in o["model_rows"]] == [("qwen3:8b", "local"), ("gpt-oss:120b-cloud", "cloud")]
    assert o["model_rows"][1]["destination"] == "ollama.com (via Ollama)"
    assert rows["lm-studio"]["found"] and rows["lm-studio"]["models"] == ["local-m"]
    assert not rows["vllm"]["found"] and rows["vllm"]["destination"] == "this computer"


def test_detect_codex_in_app_bundle(tmp_path):
    root, home = tmp_path / "root", tmp_path / "home"
    app = exe(root / "Applications/Codex.app/Contents/Resources/codex-cli/bin/codex")
    exe(home / ".vscode/extensions/openai.chatgpt-99.0.0/bin/macos-aarch64/codex")
    rows = rows_by_id(run_detect(tmp_path, root=root))
    assert rows["codex"]["found"] and rows["codex"]["where"] == app       # Codex.app wins over the higher extension
    # without Codex.app, the highest extension version is taken
    (root / "Applications/Codex.app/Contents/Resources/codex-cli/bin/codex").unlink()
    exe(home / ".vscode/extensions/openai.chatgpt-9.0.0/bin/macos-aarch64/codex")
    rows = rows_by_id(run_detect(tmp_path, root=root))
    assert rows["codex"]["where"] == "~/.vscode/extensions/openai.chatgpt-99.0.0/bin/macos-aarch64/codex"


def test_detect_reports_keys_and_sign_in_by_name_only(tmp_path, monkeypatch):
    codex = exe(tmp_path / "bin" / "codex")
    gemini = exe(tmp_path / "bin" / "gemini")
    home = tmp_path / "home"
    cred = home / ".gemini" / "oauth_creds.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"token": FAKE_KEY}))
    opened = []
    real_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    checked = []

    def exists(p):
        checked.append(p)
        return p == str(cred)

    paths = {"codex": codex, "gemini": gemini}
    rows = run_detect(tmp_path, which=paths.get, env={"OPENAI_API_KEY": FAKE_KEY}, exists=exists)
    by = rows_by_id(rows)
    assert by["codex"]["signed_in"] == "yes" and by["codex"]["key_env"] == "OPENAI_API_KEY" and by["codex"]["key_set"]
    assert by["gemini-cli"]["signed_in"] == "yes" and by["gemini-cli"]["key_set"] is False
    assert by["openai"]["key_set"] and by["openai"]["found"] and by["anthropic"]["signed_in"] == "no"
    assert str(cred) in checked and not any(".gemini" in f or ".codex" in f for f in opened)
    assert FAKE_KEY not in json.dumps(rows)


def test_detect_hides_home_and_times_out(tmp_path, monkeypatch):
    home = tmp_path / "home"
    claude = exe(home / "Library/Application Support/Claude/claude-code/2.0.1/arm64/claude.app/Contents/MacOS/claude")
    exe(home / "Library/Application Support/Claude/claude-code/2.0.0/arm64/claude.app/Contents/MacOS/claude")
    rows = run_detect(tmp_path)
    c = rows_by_id(rows)["claude-code"]
    assert c["found"] and c["where"].startswith("~/Library/") and "2.0.1" in c["where"]
    assert str(home) not in json.dumps(rows) and claude.endswith(c["where"][1:])
    # loopback reads are bounded by the short timeout and a silent port counts as closed
    seen = {}

    def fake_get(url, timeout, follow_redirects):
        seen.update(timeout=timeout, follow=follow_redirects)
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert REAL_HTTP_GET("http://127.0.0.1:11434/api/tags") is None
    assert seen == {"timeout": D.HTTP_TIMEOUT_S, "follow": False} and D.HTTP_TIMEOUT_S <= 0.5


def test_detect_contacts_only_loopback(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="only contacts 127.0.0.1"):
        REAL_HTTP_GET("http://example.org/api/tags")
    with pytest.raises(ValueError):
        REAL_HTTP_GET("http://localhost.example.net:11434/api/tags")
    get = server({})
    run_detect(tmp_path, get=get, env={"OPENAI_API_KEY": FAKE_KEY, "ANTHROPIC_API_KEY": FAKE_KEY})
    assert get.seen and all(u.startswith("http://127.0.0.1:") for u in get.seen)
    ports = {u.split(":")[2].split("/")[0] for u in get.seen}
    assert {"11434", "1234", "8000", "9931", "8080"} <= ports
    # no cloud request is made: httpx.AsyncClient is never opened during detection
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: pytest.fail("detection opened an HTTP client"))
    run_detect(tmp_path, env={"OPENAI_API_KEY": FAKE_KEY})


def mock_models(monkeypatch, module, handler):
    seen = []

    def route(req):
        seen.append(req)
        return handler(req)

    monkeypatch.setattr(f"wider.providers.{module}.httpx.AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(route), **kw))
    return seen


def test_list_models_local_openai_compat(monkeypatch):
    seen = mock_models(monkeypatch, "openai_compat",
                       lambda req: httpx.Response(200, json={"data": [{"id": "b"}, {"id": "a"}]}))
    assert D.list_models(preset_id="lm-studio") == ["a", "b"]
    assert str(seen[0].url) == "http://127.0.0.1:1234/v1/models" and "authorization" not in seen[0].headers
    seen.clear()
    assert D.list_models(preset_id="vllm", base_url="http://127.0.0.1:8001/v1") == ["a", "b"]
    assert seen[0].url.port == 8001
    # Ollama through its tag list; CLI presets return the catalogue
    mock_models(monkeypatch, "ollama", lambda req: httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]}))
    assert D.list_models(ProviderConfig(name="o", kind="ollama", model="m")) == ["qwen3:8b"]
    assert D.list_models(config_from_preset("claude-code")) == list(preset("claude-code").models)


def test_list_models_anthropic_needs_key(monkeypatch, tmp_path):
    from wider.core.backend_consent import allow_backend
    from wider.core.project import Project

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = mock_models(monkeypatch, "anthropic_sdk", lambda req: httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is not set"):
        D.list_models(preset_id="anthropic")
    assert seen == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    seen = mock_models(monkeypatch, "anthropic_sdk", lambda req: httpx.Response(200, json={"data": [{"id": "claude-x"}]}))
    # an unsaved cloud preset is refused (consent is kept per saved backend); saved and allowed, it is asked
    with pytest.raises(D.NeedsSavedBackend, match="save the backend first"):
        D.list_models(preset_id="anthropic")
    assert seen == []
    p = Project.init(tmp_path / "r")
    p.config.providers.append(config_from_preset("anthropic", name="anthropic", model="m"))
    p.save()
    allow_backend(p, "anthropic", ["models"])
    assert D.list_models(p.provider("anthropic"), project=p) == ["claude-x"]
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = mock_models(monkeypatch, "openai_compat", lambda req: httpx.Response(200, json={"data": []}))
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        D.list_models(preset_id="openai")
    assert seen == []


def test_list_models_unreachable(monkeypatch):
    def refuse(req):
        raise httpx.ConnectError("refused")

    mock_models(monkeypatch, "openai_compat", refuse)
    with pytest.raises(ValueError, match="cannot reach http://127.0.0.1:1234/v1"):
        D.list_models(preset_id="lm-studio")
    mock_models(monkeypatch, "ollama", refuse)
    with pytest.raises(ValueError, match="cannot reach Ollama"):
        D.list_models(ProviderConfig(name="o", kind="ollama", model="m"))


def test_detect_llama_cpp_loading(tmp_path):
    get = server({"9931/v1/models": (503, {"error": "loading"}), "9931/health": (503, {"status": "loading model"})})
    row = rows_by_id(run_detect(tmp_path, get=get))["llama-cpp"]
    assert not row["found"] and row["status"] == "loading" and row["server"] == "127.0.0.1:9931"
    get = server({"8080/v1/models": (200, {"data": [{"id": "gguf-m"}]})})
    row = rows_by_id(run_detect(tmp_path, get=get))["llama-cpp"]
    assert row["found"] and row["server"] == "127.0.0.1:8080" and row["models"] == ["gguf-m"]


def test_ollama_routing():
    def tags(models):
        return lambda url: (200, {"models": models}) if url.endswith("/api/tags") else None

    loop = ProviderConfig(name="o", kind="ollama", model="m3:7b", base_url="http://127.0.0.1:11434")
    assert D.ollama_routing(loop, tags([{"name": "m3:7b"}]), fresh=True) == ("local", "ollama_tags")
    assert D.ollama_routing(loop, tags([{"name": "m3:7b", "remote_host": "https://ollama.com:443"}]),
                            fresh=True) == ("cloud", "ollama_tags")
    cloud_name = loop.model_copy(update={"model": "m3:7b-cloud"})
    assert D.ollama_routing(cloud_name, tags([]), fresh=True) == ("cloud", "name_suffix")
    # a `*-cloud` model on another computer of the network is cloud by its name; nothing is contacted off loopback
    def never(url):
        raise AssertionError("only loopback is contacted")
    lan = cloud_name.model_copy(update={"base_url": "http://192.168.1.20:11434"})
    assert D.ollama_routing(lan, never) == ("cloud", "name_suffix")
    assert D.effective_locality(lan, never)[0] == "cloud"
    lan_plain = loop.model_copy(update={"base_url": "http://192.168.1.20:11434"})
    assert D.effective_locality(lan_plain, never)[0] == "network"


def test_ollama_unreachable_unknown():
    cfg = ProviderConfig(name="o", kind="ollama", model="m3:7b", base_url="http://127.0.0.1:11434")
    assert D.ollama_routing(cfg, lambda url: None, fresh=True) == ("unknown", "not_established")
    loc, src, dest = D.effective_locality(cfg, lambda url: None, fresh=True)
    assert (loc, src, dest) == ("unknown", "not_established", "unknown (via Ollama)")
    from wider.core.usage import call_cost
    assert call_cost(cfg, {"input_uncached": 10, "output": 1}, loc) is None   # unknown is never free


def test_list_models_by_kind_for_a_hand_filled_form(monkeypatch, tmp_path):
    """ui-tester F8: 'Load models' on a form filled in by hand sent the kind as a preset id ('unknown preset')."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.project import Project
    from wider.ui.app import create_app

    seen = mock_models(monkeypatch, "openai_compat", lambda req: httpx.Response(200, json={"data": [{"id": "m2"}, {"id": "m1"}]}))
    assert D.list_models(kind="openai_compat", base_url="http://127.0.0.1:8001/v1") == ["m1", "m2"]
    assert seen[0].url.port == 8001 and "authorization" not in seen[0].headers
    with pytest.raises(ValueError, match="no server to ask"):
        D.list_models(kind="cli_agent")
    # a cloud address filled in by hand is not asked before it is saved and allowed
    seen.clear()
    with pytest.raises(D.NeedsSavedBackend):
        D.list_models(kind="openai_compat", base_url="https://gw.example.org/v1")
    assert seen == []
    r = CliRunner().invoke(cli_app, ["providers", "models", "--kind", "openai_compat",
                                     "--base-url", "http://127.0.0.1:8001/v1"])
    assert r.exit_code == 0 and r.output.split() == ["m1", "m2"], r.output
    assert "give --preset or --kind" in CliRunner().invoke(cli_app, ["providers", "models"]).output
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    r = c.post("/api/providers/models", json={"kind": "openai_compat", "base_url": "http://127.0.0.1:8001/v1"})
    assert r.status_code == 200 and r.json()["models"] == ["m1", "m2"], r.text


def test_ollama_localhost_routing_is_read_and_needs_consent(monkeypatch):
    """risk-checker R2: Ollama at `localhost` was never asked for its tags (only 127.0.0.1 is contacted), counted as
    unreachable and probed without consent although it routed to ollama.com."""
    from wider.core.backend_consent import assess

    asked = []

    def get(url, timeout=D.HTTP_TIMEOUT_S):
        asked.append(url)
        if not url.startswith("http://127.0.0.1:18431/"):
            raise ValueError("only 127.0.0.1")
        return 200, {"models": [{"name": "bigmodel:latest", "remote_host": "https://ollama.com:443"}]}

    monkeypatch.setattr(D, "http_get", get)
    for base in ("http://localhost:18431", "http://127.0.0.1:18431"):
        cfg = ProviderConfig(name="ol", kind="ollama", model="bigmodel:latest", base_url=base)
        a = assess(cfg)
        assert a["locality"] == "cloud" and a["needs"] and not a["unreachable"], (base, a)
    assert asked and all(u == "http://127.0.0.1:18431/api/tags" for u in asked)
    # another loopback name is not read: its routing stays unknown, and it is not exempted as unreachable
    asked.clear()
    a = assess(ProviderConfig(name="o6", kind="ollama", model="bigmodel:latest", base_url="http://[::1]:18431"))
    assert a["needs"] and not a["unreachable"]
    assert D.tags_base("http://localhost:18431/x") == "http://127.0.0.1:18431/x" and D.tags_base("http://[::1]:1") is None
