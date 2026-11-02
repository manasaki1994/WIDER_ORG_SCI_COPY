"""Shared outputs without home paths or credentials (Decision 21). Synthetic credentials only."""
from pathlib import Path

import yaml

from wider.core.models import BackendConsent, ProviderConfig
from wider.core.project import Project
from wider.core.sanitize import (
    Finding,
    SecretFound,
    env_names,
    portable_path,
    sanitized_project_yaml,
    scan_for_secrets,
)


def test_sanitized_project_yaml(tmp_path):
    p = Project.init(tmp_path / "r")
    home = str(Path.home())
    p.config.providers.append(ProviderConfig(name="cli", kind="cli_agent", model="m",
                                             command=[f"{home}/bin/agent", "-p", "--model", "{model}"],
                                             api_key_env="SOME_KEY", extra={"env": {"TOKEN": "{env:OTHER_KEY}"}}))
    p.config.backend_consent["cli"] = BackendConsent(operations=["research"], fingerprint="f", allowed_at="2026-10-13T00:00:00Z",
                                                     allowed_by="alice")
    text, dropped = sanitized_project_yaml(p.config, p.root)
    data = yaml.safe_load(text)
    cli = next(x for x in data["providers"] if x["name"] == "cli")
    # every setting kept; the home folder as ~; references stay references; who consented is dropped and listed
    assert cli["command"][0] == "~/bin/agent" and cli["command"][1:] == ["-p", "--model", "{model}"]
    assert cli["api_key_env"] == "SOME_KEY" and cli["extra"]["env"]["TOKEN"] == "{env:OTHER_KEY}"
    assert data["backend_consent"]["cli"]["operations"] == ["research"] and "allowed_by" not in data["backend_consent"]["cli"]
    assert dropped == ["backend_consent.cli.allowed_by"]
    assert home not in text and "alice" not in text
    assert env_names(p.config) >= {"SOME_KEY", "OTHER_KEY"}


def test_portable_path(tmp_path):
    home = str(Path.home())
    assert portable_path(f"{home}/x/y") == "~/x/y" and portable_path(home) == "~"
    assert portable_path(str(tmp_path / "a" / "b"), tmp_path) == "a/b"
    assert portable_path("/opt/tools/agent") == "<path>/agent" and portable_path("relative/x") == "relative/x"


def test_secret_scan_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDER_SYNTH_KEY", "synthetic-secret-value-42")
    monkeypatch.setenv("WIDER_SHORT", "short")
    d = tmp_path / "out"
    d.mkdir()
    (d / "a.csv").write_text("id,text\n1,fine\n2,has synthetic-secret-value-42 inside\n")
    (d / "b.json").write_text('{"k": "sk-ant-' + "A" * 24 + '"}\n')
    (d / "c.md").write_text(f"path {Path.home()}/projects\nshort is ok\n")
    (d / "d.bin").write_bytes(b"\x00\xff")
    found = scan_for_secrets(sorted(d.iterdir()), d, {"WIDER_SYNTH_KEY", "WIDER_SHORT", "WIDER_UNSET"})
    assert [str(f) for f in found] == ["a.csv:3: value of environment variable WIDER_SYNTH_KEY",
                                       "b.json:1: anthropic key", "b.json:1: openai-style key",
                                       "c.md:1: home-folder path"]
    e = SecretFound(found)
    assert "a.csv:3" in str(e) and "synthetic-secret" not in str(e) and "AAAA" not in str(e)
    assert scan_for_secrets([d / "a.csv"], d, set()) == [] and all(isinstance(f, Finding) for f in found)
