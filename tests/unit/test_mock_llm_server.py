"""scripts/mock_llm_server.py: the local mock the ui-tester agents point a backend at (synthetic answers only)."""
import asyncio
import importlib.util
import json
import threading
import urllib.request
from pathlib import Path

from wider.core.models import ProviderConfig
from wider.providers.registry import PROBE_SCHEMA, PROBE_USER, probe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mock_llm_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("mock_llm_server", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mock_server_answers():
    mod = _load()
    srv = mod.serve(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        models = json.load(urllib.request.urlopen(f"{base}/v1/models", timeout=5))
        assert [m["id"] for m in models["data"]] == mod.MODELS
        tags = json.load(urllib.request.urlopen(f"{base}/api/tags", timeout=5))
        assert [m["name"] for m in tags["models"]] == mod.MODELS
        body = json.dumps({"model": "mock-small", "messages": [{"role": "user", "content": PROBE_USER}],
                           "response_format": {"type": "json_schema",
                                               "json_schema": {"name": "probe", "schema": PROBE_SCHEMA}}}).encode()
        req = urllib.request.Request(f"{base}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        ans = json.load(urllib.request.urlopen(req, timeout=5))
        parsed = json.loads(ans["choices"][0]["message"]["content"])
        assert parsed["status"] == "ok" and parsed["sum"] == 42
        assert ans["usage"]["prompt_tokens"] > 0
        assert srv.server_address[0] == "127.0.0.1"
        # WIDER's own probe through the OpenAI-compatible adapter passes against it (no key, loopback only)
        cfg = ProviderConfig(name="mock", kind="openai_compat", base_url=f"{base}/v1", model="mock-small")
        r = asyncio.run(probe(cfg, locality=("local", "test")))
        assert r["ok"], r
    finally:
        srv.shutdown()
        srv.server_close()


def test_mock_server_assistant_answer(tmp_path, monkeypatch):
    """guide-assistant 9.1 (D20): an Ask WIDER request sent to the mock through the OpenAI-compatible adapter gets the
    same synthetic answer as FakeProvider's, with and without "test markup"; other schemas are still filled."""
    from tests.unit.test_assistant_engine import DEDUP_Q, _proj
    from wider.assistant import ask as A
    from wider.providers import fake
    from wider.providers.openai_compat import OpenAICompatProvider

    mod = _load()
    seen = []
    orig = fake._assistant_answer

    def capture(req):
        seen.append(req)
        return orig(req)
    monkeypatch.setattr(fake, "_assistant_answer", capture)
    p = _proj(tmp_path)
    for q in (DEDUP_Q, DEDUP_Q + " test markup"):
        A.ask(p, "dedup", q, "ana")
    srv = mod.serve(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = ProviderConfig(name="mock", kind="openai_compat", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                             model="mock-small")
        for req in seen:
            body = json.dumps(OpenAICompatProvider(cfg).body(req)).encode()
            http = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions", data=body,
                                          headers={"Content-Type": "application/json"})
            ans = json.loads(json.load(urllib.request.urlopen(http, timeout=5))["choices"][0]["message"]["content"])
            assert ans == orig(req)
            assert ans["sources"] and f"[{ans['sources'][0]}]" in ans["answer"]
            assert ans["show"][-1]["button"] == "Merge all" and len(ans["show"]) == 2
        assert "<a href" in mod.assistant_answer(seen[1].system, seen[1].user)["answer"]
        assert "<a href" not in mod.assistant_answer(seen[0].system, seen[0].user)["answer"]
    finally:
        srv.shutdown()
        srv.server_close()
