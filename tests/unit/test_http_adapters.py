"""HTTP adapters (openai_compat, ollama, anthropic): request URL, headers and body on an httpx MockTransport. Key values
are synthetic strings set with monkeypatch.setenv; nothing leaves the test."""
import asyncio
import json

import httpx
import pytest

from wider.core.models import ProviderConfig
from wider.providers.base import ChatRequest, ProviderError
from wider.providers.presets import config_from_preset
from wider.providers.registry import make_provider

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
FAKE_KEY = "synthetic-test-value"


def _openai_answer(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"model": "m", "choices": [{"message": {"content": '{"ok": true}'},
                                                                "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                               "prompt_tokens_details": {"cached_tokens": 4}}})


def mock(monkeypatch, module: str, handler) -> list[httpx.Request]:
    """Route every AsyncClient the adapter module opens through ``handler``; returns the captured requests."""
    seen: list[httpx.Request] = []
    real = httpx.AsyncClient

    def route(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)

    monkeypatch.setattr(f"wider.providers.{module}.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(route), **kw))
    return seen


def call(cfg, **kw):
    return asyncio.run(make_provider(cfg).complete(ChatRequest(system="s", user="u", schema_=SCHEMA, **kw), retries=2))


def test_azure_deployment_request(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    cfg = config_from_preset("azure-openai", model="m", base_url="https://res.openai.azure.com/openai",
                             extra={"deployment": "dep1", "api_version": "2024-10-21"})
    r = call(cfg)
    assert r.ok
    req = seen[0]
    assert req.url.path == "/openai/deployments/dep1/chat/completions"
    assert req.url.params["api-version"] == "2024-10-21"
    assert req.headers["api-key"] == FAKE_KEY and "authorization" not in req.headers
    assert "AZURE_OPENAI_API_KEY" in cfg.model_dump_json() and FAKE_KEY not in cfg.model_dump_json()
    # the v1 base URL needs no deployment: the plain path is used
    seen.clear()
    call(config_from_preset("azure-openai", model="m", base_url="https://res.openai.azure.com/openai/v1"))
    assert seen[0].url.path == "/openai/v1/chat/completions" and "api-version" not in seen[0].url.params


def test_reasoning_model_params(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    cfg = config_from_preset("openai", model="m", seed=7,
                             extra={"omit_params": ["temperature"], "max_tokens_param": "max_completion_tokens"})
    assert call(cfg).ok
    body = json.loads(seen[0].content)
    assert "temperature" not in body and "max_tokens" not in body and body["max_completion_tokens"] > 0
    assert body["seed"] == 7
    seen.clear()
    call(config_from_preset("openai", model="m", seed=7, extra={"send_seed": False}))
    assert "seed" not in json.loads(seen[0].content)


def test_gemini_preset_request(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    r = call(config_from_preset("gemini", model="m"))
    assert r.ok and r.usage["input_cached"] == 4 and r.usage["input_uncached"] == 6
    req = seen[0]
    assert req.url.host == "generativelanguage.googleapis.com"
    assert req.url.path == "/v1beta/openai/chat/completions"
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"


def test_auth_none_sends_no_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    cfg = ProviderConfig(name="a", kind="openai_compat", model="m", base_url="https://gw.example.org/v1",
                         api_key_env="OPENAI_API_KEY", extra={"auth": "none"})
    assert call(cfg).ok
    h = seen[0].headers
    assert "authorization" not in h and "api-key" not in h and FAKE_KEY not in str(h.raw)


def test_missing_key_on_cloud_endpoint_sends_nothing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    r = call(config_from_preset("openai", model="m"))
    assert not r.ok and r.attempts == 1 and "OPENAI_API_KEY is not set" in r.error
    assert seen == []
    # a local server needs no key
    assert call(ProviderConfig(name="l", kind="openai_compat", model="m", base_url="http://127.0.0.1:1234/v1",
                               api_key_env="OPENAI_API_KEY")).ok
    assert len(seen) == 1


@pytest.mark.parametrize("module,cfg", [
    ("openai_compat", ProviderConfig(name="l", kind="openai_compat", model="m", base_url="http://127.0.0.1:1234/v1")),
    ("ollama", ProviderConfig(name="o", kind="ollama", model="m")),
])
def test_transport_errors_are_retryable(monkeypatch, module, cfg):
    real_sleep = asyncio.sleep
    monkeypatch.setattr("wider.providers.base.asyncio.sleep", lambda s: real_sleep(0))
    n = {"i": 0}

    def flaky(req):
        n["i"] += 1
        if n["i"] < 0:
            raise httpx.ConnectError("refused")
        if n["i"] == 1:
            raise httpx.ConnectError("refused")
        if n["i"] == 2:
            raise httpx.ReadTimeout("slow")
        return (_openai_answer(req) if module == "openai_compat" else
                httpx.Response(200, json={"model": "m", "message": {"content": '{"ok": true}'}}))

    mock(monkeypatch, module, flaky)
    r = asyncio.run(make_provider(cfg).complete(ChatRequest(system="s", user="u", schema_=SCHEMA), retries=3))
    assert r.ok and r.attempts == 3
    n["i"] = -10   # never answers within the tries: a plain message naming the endpoint
    r = asyncio.run(make_provider(cfg).complete(ChatRequest(system="s", user="u", schema_=SCHEMA), retries=2))
    assert not r.ok and "cannot reach" in r.error and "127.0.0.1" in r.error


def test_ollama_request_shape(monkeypatch):
    seen = mock(monkeypatch, "ollama", lambda req: httpx.Response(200, json={
        "model": "m", "message": {"content": '{"ok": true}'}, "prompt_eval_count": 9, "eval_count": 3}))
    cfg = ProviderConfig(name="o", kind="ollama", model="m", seed=1, extra={"think": False})
    r = call(cfg)
    assert r.ok and r.usage["input_uncached"] == 9 and r.usage["output"] == 3
    req = seen[0]
    assert str(req.url) == "http://127.0.0.1:11434/api/chat"
    body = json.loads(req.content)
    assert body["format"] == "json" and body["think"] is False and body["stream"] is False
    assert body["options"]["seed"] == 1 and body["messages"][1] == {"role": "user", "content": "u"}


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, "anthropic_sdk", lambda req: httpx.Response(200, json={
        "model": "m", "content": [{"type": "tool_use", "input": {"ok": True}}],
        "usage": {"input_tokens": 5, "cache_read_input_tokens": 7, "cache_creation_input_tokens": 2,
                  "output_tokens": 4}}))
    r = call(config_from_preset("anthropic", model="m"))
    assert r.ok and r.parsed == {"ok": True}
    assert (r.usage["input_uncached"], r.usage["input_cached"], r.usage["input_cache_write"]) == (5, 7, 2)
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == FAKE_KEY and req.headers["anthropic-version"]
    body = json.loads(req.content)
    assert body["tool_choice"] == {"type": "tool", "name": "answer"} and body["tools"][0]["input_schema"] == SCHEMA
    # no key: nothing is sent
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    seen.clear()
    r = call(config_from_preset("anthropic", model="m"))
    assert not r.ok and "ANTHROPIC_API_KEY is not set" in r.error and seen == []


def test_env_header_resolved_and_missing(monkeypatch):
    monkeypatch.setenv("GW_TOKEN", FAKE_KEY)
    seen = mock(monkeypatch, "openai_compat", _openai_answer)
    cfg = ProviderConfig(name="g", kind="openai_compat", model="m", base_url="https://gw.example.org/v1",
                         extra={"auth": "none", "headers": {"X-Gateway-Token": "{env:GW_TOKEN}", "X-Team": "lab"}})
    assert call(cfg).ok
    assert seen[0].headers["x-gateway-token"] == FAKE_KEY and seen[0].headers["x-team"] == "lab"
    assert "{env:GW_TOKEN}" in cfg.model_dump_json() and FAKE_KEY not in cfg.model_dump_json()
    monkeypatch.delenv("GW_TOKEN")
    seen.clear()
    r = call(cfg)
    assert not r.ok and r.attempts == 1 and "GW_TOKEN" in r.error and seen == []
    # a plain value in a sensitive header is refused before anything is sent
    bad = cfg.model_copy(update={"extra": {"auth": "none", "headers": {"X-Gateway-Token": "plain"}}})
    with pytest.raises(ProviderError, match="sensitive"):
        make_provider(bad).headers()


@pytest.mark.parametrize("module,cfg,env", [
    ("openai_compat", ProviderConfig(name="g", kind="openai_compat", model="m", base_url="https://gw.example.org/v1",
                                     api_key_env="OPENAI_API_KEY"), "OPENAI_API_KEY"),
    ("ollama", ProviderConfig(name="o", kind="ollama", model="m"), None),
    ("anthropic_sdk", ProviderConfig(name="a", kind="anthropic", model="m", api_key_env="ANTHROPIC_API_KEY"),
     "ANTHROPIC_API_KEY"),
])
def test_redirect_not_followed(monkeypatch, module, cfg, env):
    if env:
        monkeypatch.setenv(env, FAKE_KEY)
    seen = mock(monkeypatch, module, lambda req: httpx.Response(
        307, headers={"location": "https://elsewhere.example.net/v1/chat/completions"}))
    r = call(cfg)
    assert not r.ok and r.attempts == 1
    assert "redirected to elsewhere.example.net" in r.error and "base URL" in r.error
    assert len(seen) == 1 and all(q.url.host != "elsewhere.example.net" for q in seen)
