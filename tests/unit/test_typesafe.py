"""Jev (TypeSafe AI, kind ``typesafe``) as a screening-only backend (design Decision 13). httpx MockTransport, a
synthetic TYPESAFE_API_KEY; nothing leaves the test. The answer shape read here is UNVERIFIED (open question 8)."""
import asyncio
import json

import httpx
import pytest
from typer.testing import CliRunner

from wider.cli import app
from wider.core import backends
from wider.core.project import Project
from wider.providers.base import ChatRequest
from wider.providers.presets import config_from_preset
from wider.providers.registry import make_provider, probe
from wider.providers.typesafe import questions_from_schema

FAKE_KEY = "synthetic-test-value"
REAL_CLIENT = httpx.AsyncClient
SCHEMA = {"type": "object", "required": ["decision", "c1", "summary"], "properties": {
    "decision": {"type": "string", "enum": ["include", "maybe", "exclude"]},
    "c1": {"type": "boolean"},
    "summary": {"type": "string"}}}


def mock(monkeypatch, handler):
    seen = []

    def route(req):
        seen.append(req)
        return handler(req)

    monkeypatch.setattr("wider.providers.typesafe.httpx.AsyncClient",
                        lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(route), **kw))
    return seen


def jev():
    return config_from_preset("jev", model="jev-latest")


def test_questions_from_schema_and_answer(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, lambda req: httpx.Response(200, json={
        "model": "jev-1.13.0", "usage": {"input_tokens": 40, "output_tokens": 2},
        "answers": {"decision": {"answer": "include", "probabilities": {"include": 0.7, "maybe": 0.2, "exclude": 0.1}},
                    "c1": {"answer": "yes", "probability": 0.8}}}))
    r = asyncio.run(make_provider(jev()).complete(ChatRequest(system="Screen it.", user="A record.", schema=SCHEMA)))
    assert r.ok and r.parsed == {"decision": "include", "c1": True, "summary": ""}
    assert r.meta["unanswered"] == ["summary"]
    assert r.meta["probabilities"]["c1"] == 0.8 and r.meta["probabilities"]["decision"]["include"] == 0.7
    assert r.usage["input_uncached"] == 40 and r.usage["output"] == 2
    req = seen[0]
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
    assert req.headers["authorization"] == f"Bearer {FAKE_KEY}"
    body = json.loads(req.content)
    assert body["model"] == "jev-latest" and body["state"] == "A record."
    assert {k: q["type"] for k, q in body["questions"].items()} == {"decision": "choice", "c1": "noul"}
    assert body["questions"]["decision"]["criteria"] == {"include": "include", "maybe": "maybe", "exclude": "exclude"}
    assert body["questions"]["c1"]["instructions"].endswith("Answer only: c1")
    # a small integer range is a score question; a wide one is not asked
    q, un = questions_from_schema({"properties": {"s": {"type": "integer", "minimum": 1, "maximum": 5},
                                                  "w": {"type": "integer", "minimum": 0, "maximum": 100}}})
    assert q["s"]["type"] == "score" and list(q["s"]["criteria"]) == ["1", "2", "3", "4", "5"] and un == ["w"]


def test_unanswerable_schema(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, lambda req: httpx.Response(200, json={"answers": {}}))
    only_text = {"type": "object", "properties": {"summary": {"type": "string"}, "quote": {"type": "string"}}}
    r = asyncio.run(make_provider(jev()).complete(ChatRequest(system="s", user="u", schema=only_text), retries=3))
    assert not r.ok and r.attempts == 1 and r.error == "Jev cannot answer this schema" and seen == []
    # no key: nothing is sent
    monkeypatch.delenv("TYPESAFE_API_KEY")
    r = asyncio.run(make_provider(jev()).complete(ChatRequest(system="s", user="u", schema=SCHEMA)))
    assert not r.ok and "TYPESAFE_API_KEY is not set" in r.error and seen == []


def test_text_stages_refuse_jev(tmp_path, monkeypatch):
    from wider.construct.adjacent import propose_from_question_async
    from wider.construct.propose import propose
    from wider.rank.adjacent import find_candidates_async

    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    seen = mock(monkeypatch, lambda req: httpx.Response(200, json={"answers": {}}))
    p = Project.init(tmp_path / "r")
    backends.add_provider(p, "jev", kind="typesafe", model="jev-latest", base_url="https://api.typesafe.ai",
                          api_key_env="TYPESAFE_API_KEY", preset="jev")
    msg = "Jev cannot write text; choose a generative backend"
    with pytest.raises(ValueError, match=msg):
        propose(p, "codebook", provider_name="jev")
    with pytest.raises(ValueError, match=msg):
        asyncio.run(propose_from_question_async(p, provider_name="jev"))
    with pytest.raises(ValueError, match=msg):
        asyncio.run(find_candidates_async(p, provider_name="jev"))
    with pytest.raises(ValueError, match=msg):
        backends.set_stage_provider(p, "construct", "jev")
    assert "construct" not in Project.load(tmp_path / "r").config.stage_providers
    backends.set_stage_provider(p, "screen", "jev")          # screening may use it
    assert Project.load(tmp_path / "r").config.stage_providers["screen"] == "jev"
    p.close()
    res = CliRunner().invoke(app, ["construct", "adjacent", "propose", "--provider", "jev", "--dir", str(tmp_path / "r")])
    assert res.exit_code == 1 and msg in " ".join(res.output.split())
    res = CliRunner().invoke(app, ["construct", "adjacent", "find", "--provider", "jev", "--dir", str(tmp_path / "r")])
    assert res.exit_code == 1 and msg in " ".join(res.output.split())
    assert seen == []


def test_jev_probe(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", FAKE_KEY)
    scores = {"17 + 25 = 42": 0.9, "17 + 25 = 41": 0.1}

    def answer(req):
        body = json.loads(req.content)
        assert list(body["questions"]) == ["correct"] and body["questions"]["correct"]["type"] == "noul"
        p = scores[body["state"]]
        return httpx.Response(200, json={"answers": {"correct": {"answer": p > 0.5, "probability": p}}})

    seen = mock(monkeypatch, answer)
    r = asyncio.run(probe(jev()))
    assert r["ok"] and r["error"] is None and len(seen) == 2
    scores["17 + 25 = 41"] = 0.9
    r = asyncio.run(probe(jev()))
    assert not r["ok"] and r["error"].startswith("wrong answer")
