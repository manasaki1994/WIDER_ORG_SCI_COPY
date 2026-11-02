"""Agentic CLI adapter (wider.providers.cli_agent) against fake executables: small Python scripts written per test into
tmp_path and put on PATH. They print synthetic Codex JSONL, Gemini JSON, Antigravity JSON or a Claude envelope, report
their argv, stdin, folder and environment, exit with a chosen code, or hang. Nothing real is started."""
import asyncio
import json
import os
import sys
import time
import tomllib

import pytest

from wider.core.models import ProviderConfig
from wider.providers.base import ChatRequest
from wider.providers.cli_agent import _substitute, toml_basic_string
from wider.providers.presets import config_from_preset
from wider.providers.registry import make_provider

REAL_SLEEP = asyncio.sleep   # the fixture stubs asyncio.sleep for retry backoff
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}}, "required": ["ok"]}

FAKE = r'''import json, os, sys, time
mode = os.environ.get("FAKE_MODE", "text")
stdin = sys.stdin.read() if os.environ.get("FAKE_READ_STDIN", "1") == "1" else ""
report = os.environ.get("FAKE_REPORT")
if report:
    files = sorted(os.listdir(os.getcwd()))
    contents = {{f: open(f, encoding="utf-8").read() for f in files}}
    env = {{k: os.environ.get(k) for k in ("GEMINI_SYSTEM_MD", "FAKE_SETTINGS", "NO_COLOR")}}
    with open(report, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin, "files": files, "contents": contents,
                             "env": env, "pid": os.getpid()}}) + "\n")
counter = os.environ.get("FAKE_COUNTER")
if counter:
    with open(counter, "a") as fh:
        fh.write("x")
answer = os.environ.get("FAKE_ANSWER", '{{"ok": true}}')
if mode == "codex":
    print(json.dumps({{"type": "thread.started", "thread_id": "t1"}}))
    print(json.dumps({{"type": "item.completed", "item": {{"type": "reasoning", "text": "thinking"}}}}))
    print(json.dumps({{"type": "item.completed", "item": {{"type": "agent_message", "text": answer}}}}))
    print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 120, "cached_input_tokens": 100,
                                                          "output_tokens": 7}}}}))
elif mode == "codex_failed":
    print(json.dumps({{"type": "turn.failed", "error": {{"message": "model overloaded"}}}}))
elif mode == "gemini":
    print(json.dumps({{"response": answer, "stats": {{"models": {{"gemini-x": {{"tokens": {{
        "prompt": 50, "cached": 20, "candidates": 5, "thoughts": 3}}}}, "gemini-y": {{"tokens": {{
        "prompt": 10, "cached": 0, "candidates": 1, "thoughts": 0}}}}}}}}}}))
elif mode == "agy":
    print(json.dumps({{"status": "SUCCESS", "structured_output": json.loads(answer), "model": "agy-m",
                       "input_tokens": 30, "output_tokens": 4, "thinking_tokens": 2, "cache_read_tokens": 10}}))
elif mode == "claude":
    print(json.dumps({{"result": answer, "is_error": False, "total_cost_usd": 0.01, "session_id": "s",
                       "usage": {{"input_tokens": 3, "cache_read_input_tokens": 9, "output_tokens": 2}},
                       "modelUsage": {{"claude-x": {{}}}}}}))
elif mode == "exit":
    sys.stderr.write(os.environ.get("FAKE_STDERR", "failed") + "\n")
    sys.exit(int(os.environ["FAKE_CODE"]))
elif mode == "hang":
    time.sleep(60)
else:
    print(answer)
'''


@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """Write the fake as each named executable on a fresh PATH folder; returns the report path."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = tmp_path / "fake_cli.py"
    script.write_text(FAKE.format())
    # a sh launcher: the interpreter path may hold spaces, which a #! line cannot
    for name in ("codex", "gemini", "agy", "claude", "mycli"):
        exe = bindir / name
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    report = tmp_path / "report.jsonl"
    monkeypatch.setenv("FAKE_REPORT", str(report))
    monkeypatch.setattr("wider.providers.base.asyncio.sleep", lambda s: REAL_SLEEP(0))
    return report


def reports(path):
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def run(cfg, schema=SCHEMA, system="SYS", user="USER", retries=3):
    return asyncio.run(make_provider(cfg).complete(ChatRequest(system=system, user=user, schema_=schema),
                                                   retries=retries))


def test_codex_jsonl_parser(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "codex")
    r = run(config_from_preset("codex", model="m"))
    assert r.ok and r.parsed == {"ok": True}
    monkeypatch.setenv("FAKE_MODE", "codex_failed")
    r = run(config_from_preset("codex", model="m"), retries=1)
    assert not r.ok and "model overloaded" in r.error


def test_codex_usage_reading(fake_cli, monkeypatch):
    """Our reading of turn.completed.usage (field names UNVERIFIED, open question 3): input includes cached."""
    monkeypatch.setenv("FAKE_MODE", "codex")
    r = run(config_from_preset("codex", model="m"))
    assert (r.usage["input_uncached"], r.usage["input_cached"], r.usage["output"]) == (20, 100, 7)
    assert r.usage["raw"]["input_tokens"] == 120


def test_gemini_json_parser(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "gemini")
    r = run(config_from_preset("gemini-cli", model="m"))
    assert r.ok and r.parsed == {"ok": True}
    assert (r.usage["input_uncached"], r.usage["input_cached"], r.usage["output"]) == (40, 20, 9)
    assert r.meta["models"] == ["gemini-x", "gemini-y"]
    rep = reports(fake_cli)[-1]
    # GEMINI_SYSTEM_MD points at the system file in the call folder; the user message is on stdin
    assert rep["env"]["GEMINI_SYSTEM_MD"].endswith("system_prompt.md") and rep["stdin"] == "USER"
    assert rep["argv"][-2:] == ["-p", "Answer the request above."]


def test_agy_json_parser(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "agy")
    r = run(config_from_preset("antigravity", model="m"))
    assert r.ok and r.parsed == {"ok": True}
    assert (r.usage["input_uncached"], r.usage["input_cached"], r.usage["output"]) == (30, 10, 6)
    rep = reports(fake_cli)[-1]
    assert rep["stdin"] == "" and rep["argv"][1].startswith("SYS") and rep["argv"][1].endswith("USER")


def test_codex_developer_instructions_round_trip(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "codex")
    system = 'Rules: "quote" \\ back\nline two\ttab\x01 {user} {model}'
    r = run(config_from_preset("codex", model="m"), system=system)
    assert r.ok
    rep = reports(fake_cli)[-1]
    tok = next(a for a in rep["argv"] if a.startswith("developer_instructions="))
    value = tomllib.loads("v = " + tok.split("=", 1)[1])["v"]
    assert value.startswith(system)            # the schema instruction follows the system text
    assert rep["stdin"] == "USER"


def test_no_schema_drops_schema_flag(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "codex")
    run(config_from_preset("codex", model="m"), schema=None)
    rep = reports(fake_cli)[-1]
    assert "--output-schema" not in rep["argv"] and not any("schema" in a for a in rep["argv"])
    assert "schema.json" not in rep["files"]
    # a joined token is dropped alone
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m", command=["mycli", "--schema={schema_file}", "-x"])
    run(cfg, schema=None)
    assert reports(fake_cli)[-1]["argv"] == ["-x"]


def test_schema_file_and_env_placeholders(fake_cli, monkeypatch):
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m",
                         command=["mycli", "--schema", "{schema_file}", "--model", "{model}"],
                         extra={"env": {"GEMINI_SYSTEM_MD": "{system_file}", "NO_COLOR": "1"}, "strict_schema": True})
    r = run(cfg)
    assert r.ok
    rep = reports(fake_cli)[-1]
    schema_arg = rep["argv"][1]
    assert schema_arg.endswith("schema.json") and rep["argv"][3] == "m"
    strict = json.loads(rep["contents"]["schema.json"])
    assert strict["required"] == ["ok", "note"] and strict["additionalProperties"] is False
    assert rep["env"]["GEMINI_SYSTEM_MD"].endswith("system_prompt.md") and rep["env"]["NO_COLOR"] == "1"
    assert rep["contents"]["system_prompt.md"].startswith("SYS")


def test_call_folder_holds_only_prompt_schema_and_settings(fake_cli, monkeypatch):
    """The call folder holds the system prompt, the schema and, only for a checked switch, the settings file."""
    cfg = config_from_preset("gemini-cli", model="m")
    monkeypatch.setenv("FAKE_MODE", "gemini")
    run(cfg)
    assert reports(fake_cli)[-1]["files"] == ["schema.json", "system_prompt.md"]
    checked = cfg.model_copy(update={"extra": {**cfg.extra, "settings_file": {
        "name": "settings.json", "env": "FAKE_SETTINGS", "content": {"privacy": {"usageStatisticsEnabled": False}}}}})
    run(checked)
    rep = reports(fake_cli)[-1]
    assert rep["files"] == ["schema.json", "settings.json", "system_prompt.md"]
    assert rep["env"]["FAKE_SETTINGS"].endswith("settings.json")
    assert json.loads(rep["contents"]["settings.json"]) == {"privacy": {"usageStatisticsEnabled": False}}


def test_not_signed_in_is_not_retried(fake_cli, monkeypatch, tmp_path):
    counter = tmp_path / "count"
    monkeypatch.setenv("FAKE_COUNTER", str(counter))
    monkeypatch.setenv("FAKE_MODE", "exit")
    monkeypatch.setenv("FAKE_CODE", "41")
    r = run(config_from_preset("gemini-cli", model="m"))
    assert not r.ok and r.attempts == 1 and "Gemini CLI is not signed in" in r.error
    assert counter.read_text() == "x"
    counter.unlink()
    monkeypatch.setenv("FAKE_CODE", "1")
    monkeypatch.setenv("FAKE_STDERR", "Error: authentication required")
    r = run(config_from_preset("antigravity", model="m"))
    assert not r.ok and r.attempts == 1 and "Antigravity CLI is not signed in" in r.error
    assert counter.read_text() == "x"


def test_gemini_exit_codes(fake_cli, monkeypatch, tmp_path):
    """Exit 42 (input error) is not retried; exit 53 (turn limit) runs once more, then fails (open question 14)."""
    counter = tmp_path / "count"
    monkeypatch.setenv("FAKE_COUNTER", str(counter))
    monkeypatch.setenv("FAKE_MODE", "exit")
    monkeypatch.setenv("FAKE_CODE", "42")
    r = run(config_from_preset("gemini-cli", model="m"))
    assert not r.ok and r.attempts == 1 and "exit 42" in r.error and counter.read_text() == "x"
    counter.unlink()
    monkeypatch.setenv("FAKE_CODE", "53")
    r = run(config_from_preset("gemini-cli", model="m"))
    assert not r.ok and r.attempts == 1 and "exit 53 twice" in r.error and counter.read_text() == "xx"


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def test_timeout_kills_child(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m", command=["mycli"], timeout_s=1)
    t0 = time.monotonic()
    r = run(cfg, retries=1)
    assert not r.ok and "did not answer within 1 s" in r.error and time.monotonic() - t0 < 10
    assert _gone(reports(fake_cli)[-1]["pid"])


def test_cancel_kills_child(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hang")
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m", command=["mycli"], timeout_s=60)

    async def go():
        task = asyncio.create_task(make_provider(cfg).complete(ChatRequest(system="s", user="u"), retries=1))
        for _ in range(200):
            if reports(fake_cli):
                break
            await REAL_SLEEP(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert _gone(reports(fake_cli)[-1]["pid"])


def test_substitute_does_not_rescan_values(fake_cli, monkeypatch):
    subs = {"{system}": "say {user} and {model}", "{user}": "RECORD", "{model}": "m1"}
    assert _substitute("{system}|{user}|{model}", subs) == "say {user} and {model}|RECORD|m1"
    cfg = ProviderConfig(name="c", kind="cli_agent", model="m1", command=["mycli", "{system}", "--model", "{model}"])
    run(cfg, schema=None, system="keep {user} and {model} as written")
    argv = reports(fake_cli)[-1]["argv"]
    assert argv[0] == "keep {user} and {model} as written" and argv[2] == "m1"


def test_claude_envelope_usage(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "claude")
    r = run(config_from_preset("claude-code"))
    assert r.ok and r.usage["input_cached"] == 9 and r.usage["total_cost_usd"] == 0.01
    assert r.meta["models"] == ["claude-x"]


def test_toml_basic_string_escapes_controls():
    text = 'a"b\\c\nd\x00e\x1ff\x7fg\th'
    assert tomllib.loads("v = " + toml_basic_string(text))["v"] == text
