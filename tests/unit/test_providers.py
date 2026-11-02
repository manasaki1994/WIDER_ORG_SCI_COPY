import json

import pytest

from wider.core.models import ProviderConfig
from wider.providers import ChatRequest, make_provider
from wider.providers.fake import FakeProvider
from wider.providers.registry import probe
from wider.providers.structured import extract_json, validate_json

SCHEMA = {"type": "object", "properties": {"decision": {"type": "string", "enum": ["include", "exclude", "maybe"]},
                                           "n": {"type": "integer"}}, "required": ["decision", "n"]}


def test_extract_json_variants():
    assert extract_json('{"a": 1}')[0] == {"a": 1}
    assert extract_json('Sure! ```json\n{"a": 1}\n```')[0] == {"a": 1}
    assert extract_json('text before {"a": {"b": "}"}} after')[0] == {"a": {"b": "}"}}
    assert extract_json('[1, 2]')[0] == [1, 2]
    assert extract_json("no json here")[0] is None
    assert extract_json("")[1] == "empty response"


def test_validate_json_reports_path():
    assert validate_json({"decision": "include", "n": 1}, SCHEMA) is None
    err = validate_json({"decision": "nope", "n": 1}, SCHEMA)
    assert err and err.startswith("decision:")


def test_fake_provider_fills_schema_and_hashes_input():
    p = make_provider(ProviderConfig(name="f", kind="fake"))
    r = p.complete_sync(ChatRequest(system="s", user="u", schema=SCHEMA))
    assert r.ok and r.parsed == {"decision": "include", "n": 0}
    assert r.input_sha256 == ChatRequest(system="s", user="u").input_sha256
    assert r.attempts == 1


def test_repair_retry_on_invalid_output():
    replies = iter(['{"decision": "wrong", "n": 1}', '{"decision": "maybe", "n": 2}'])
    p = FakeProvider(ProviderConfig(name="f", kind="fake"), responder=lambda req: next(replies))
    r = p.complete_sync(ChatRequest(system="s", user="u", schema=SCHEMA))
    assert r.ok and r.parsed["decision"] == "maybe" and r.attempts == 2
    assert "did not match" in p.calls[1].user


def test_gives_up_after_repair():
    p = FakeProvider(ProviderConfig(name="f", kind="fake"), responder=lambda req: "nonsense")
    r = p.complete_sync(ChatRequest(system="s", user="u", schema=SCHEMA))
    assert not r.ok and "invalid structured output" in r.error and r.attempts == 2


def test_repair_after_transport_retry(monkeypatch):
    """A transport retry does not use up the repair round: retry, then invalid output, then one repair."""
    import asyncio

    from wider.providers.base import ProviderError

    real_sleep = asyncio.sleep
    monkeypatch.setattr("wider.providers.base.asyncio.sleep", lambda s: real_sleep(0))
    steps = iter([ProviderError("cannot reach x", retryable=True), '{"decision": "wrong", "n": 1}',
                  '{"decision": "maybe", "n": 2}'])

    def respond(req):
        step = next(steps)
        if isinstance(step, Exception):
            raise step
        return step

    p = FakeProvider(ProviderConfig(name="f", kind="fake"), responder=respond)
    r = p.complete_sync(ChatRequest(system="s", user="u", schema=SCHEMA))
    assert r.ok and r.parsed == {"decision": "maybe", "n": 2} and r.attempts == 3 and r.repaired


def test_repaired_call_keeps_input_hash():
    replies = iter(['{"decision": "wrong", "n": 1}', '{"decision": "maybe", "n": 2}'])
    p = FakeProvider(ProviderConfig(name="f", kind="fake"), responder=lambda req: next(replies))
    req = ChatRequest(system="s", user="u", schema=SCHEMA)
    r = p.complete_sync(req)
    assert r.ok and r.repaired and r.input_sha256 == req.input_sha256
    assert p.calls[1].input_sha256 != req.input_sha256   # the repair round sent a longer message
    plain = make_provider(ProviderConfig(name="f", kind="fake")).complete_sync(req)
    assert not plain.repaired and plain.input_sha256 == req.input_sha256


def test_probe_unreachable_backend(monkeypatch):
    """An unreachable endpoint or a config the adapter refuses gives a failed probe with a plain message, not a crash."""
    import asyncio

    import httpx

    real_sleep = asyncio.sleep
    monkeypatch.setattr("wider.providers.base.asyncio.sleep", lambda s: real_sleep(0))
    real = httpx.AsyncClient

    def refuse(req):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("wider.providers.openai_compat.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(refuse), **kw))
    res = asyncio.run(probe(ProviderConfig(name="l", kind="openai_compat", model="m",
                                           base_url="http://127.0.0.1:1234/v1")))
    assert res["ok"] is False and "cannot reach http://127.0.0.1:1234/v1" in res["error"]
    monkeypatch.delenv("NO_SUCH_TEST_KEY", raising=False)
    res = asyncio.run(probe(ProviderConfig(name="g", kind="openai_compat", model="m",
                                           base_url="https://gw.example.org/v1", api_key_env="NO_SUCH_TEST_KEY")))
    assert res["ok"] is False and "NO_SUCH_TEST_KEY is not set" in res["error"]


def test_probe_with_fake_provider():
    import asyncio

    cfg = ProviderConfig(name="f", kind="fake")
    res = asyncio.run(probe(cfg))
    assert res["ok"] is True and res["error"] is None  # fake answers sum=42, so a fresh synthetic project is green
    assert res["parsed"]["sum"] == 42


def test_identity_has_no_secrets(monkeypatch):
    monkeypatch.setenv("TESTKEY", "sk-secret")
    p = make_provider(ProviderConfig(name="o", kind="openai_compat", model="m", api_key_env="TESTKEY"))
    assert "sk-secret" not in json.dumps(p.identity())
    assert p.api_key() == "sk-secret"


def test_cli_agent_runs_a_process():
    import sys

    cfg = ProviderConfig(name="c", kind="cli_agent", model="m", timeout_s=20,
                         command=[sys.executable, "-c", "import sys,json;print(json.dumps({'decision':'include','n':3}))"],
                         extra={"stdin": "user", "output": "text"})
    p = make_provider(cfg)
    r = p.complete_sync(ChatRequest(system="s", user="u", schema=SCHEMA))
    assert r.ok and r.parsed == {"decision": "include", "n": 3}
    assert p.identity()["executable"]


def test_cli_agent_substitutes_placeholders():
    import sys

    # echo back argv and stdin: {model} {system_file} {system} {user} are replaced, the user message also arrives on stdin
    script = ("import sys,json;a=sys.argv[1:];print(json.dumps({'model':a[0],'sysfile':open(a[1]).read(),"
              "'system':a[2],'user':a[3],'stdin':sys.stdin.read()}))")
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m-9", timeout_s=20,
                         command=[sys.executable, "-c", script, "{model}", "{system_file}", "{system}", "{user}"],
                         extra={"output": "text"})
    r = make_provider(cfg).complete_sync(ChatRequest(system="sys text", user="user text", schema=None))
    assert r.ok, r.error
    got = json.loads(r.text)
    assert got["model"] == "m-9" and got["user"] == "user text" and got["stdin"] == "user text"
    assert got["system"] == "sys text" == got["sysfile"]


def test_unknown_kind():
    with pytest.raises(ValueError):
        make_provider(ProviderConfig.model_construct(name="x", kind="nope"))
