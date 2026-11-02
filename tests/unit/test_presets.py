"""Backend preset catalogue (Decision 1, 20, 25): loading, locality, keys by name, experimental, telemetry."""
from __future__ import annotations

import pytest
import yaml

from wider.core.summary import provider_locality
from wider.providers import presets as P

ALL_IDS = {
    "cli": ["claude-code", "codex", "gemini-cli", "antigravity", "custom-cli"],
    "cloud": ["openai", "anthropic", "openrouter", "gemini", "azure-openai", "mistral", "groq", "together", "jev"],
    "local": ["ollama", "lm-studio", "vllm", "llama-cpp"],
}
NEEDS = {"base_url": "https://res.example.test/openai/v1/", "command": ["mytool", "{model}"], "model": "m1"}


def _cfg(p):
    over = {k: NEEDS[k] for k in p.needs}
    model = over.pop("model", None) or p.defaults.model or "m1"
    return P.config_from_preset(p.id, model=model, **over)


def test_catalogue_lists_every_preset():
    rows = P.presets()
    assert len(rows) == 18
    for fam, ids in ALL_IDS.items():
        assert sorted(p.id for p in rows if p.family == fam) == sorted(ids)
    # grouped CLI / cloud / local
    fams = [p.family for p in rows]
    assert fams == sorted(fams, key=["cli", "cloud", "local"].index)
    for pid in ("gemini-cli", "antigravity"):
        assert P.preset_row(P.preset(pid))["runs"] == "Runs: checked against the docs, not yet run on this computer"
    assert {e.id for e in P.embedding_presets()} >= {"openai-3-small", "openai-3-large", "gemini-embedding",
                                                     "ollama-embed", "lm-studio-embed", "bge-m3", "minilm"}
    # the claude-code default backend is built from its preset, argv unchanged
    from wider.core.project import DEFAULT_PROVIDERS
    cc = next(c for c in DEFAULT_PROVIDERS if c.name == "claude-code")
    assert cc.command == ["claude", "-p", "--model", "{model}", "--system-prompt-file", "{system_file}",
                          "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                          "--no-session-persistence", "--output-format", "json"]
    assert cc.preset == "claude-code" and cc.extra["output"] == "claude_json"
    # cloud and CLI presets other than claude-code set no default model
    for p in rows:
        if p.family in ("cli", "cloud") and p.id != "claude-code":
            assert not p.defaults.model, p.id


def test_preset_locality_matches_kind_and_url():
    for p in P.presets():
        cfg = _cfg(p)
        routing = ("local", "ollama_tags") if p.id == "ollama" else None
        loc = provider_locality(cfg.kind, cfg.base_url, routing=routing)
        assert loc == ("local" if p.family == "local" else "cloud"), p.id
    oll = _cfg(P.preset("ollama"))
    assert provider_locality(oll.kind, oll.base_url) == "unknown"


def test_preset_never_sets_locality():
    # a "local" preset pointed at a remote or network host follows the host, not its family
    cfg = P.config_from_preset("lm-studio", base_url="https://models.example.test/v1")
    assert provider_locality(cfg.kind, cfg.base_url) == "cloud"
    cfg = P.config_from_preset("vllm", base_url="http://192.168.1.20:8000/v1")
    assert provider_locality(cfg.kind, cfg.base_url) == "network"
    # and a "cloud" preset pointed at loopback is local
    cfg = P.config_from_preset("openai", model="m1", base_url="http://127.0.0.1:9999/v1")
    assert provider_locality(cfg.kind, cfg.base_url) == "local"
    assert "locality" not in P.Preset.model_fields


def test_presets_name_keys_only():
    text = P.CATALOGUE_FILE.read_text(encoding="utf-8")
    assert "sk-" not in text and "Bearer " not in text
    for p in P.presets():
        cfg = _cfg(p)
        if cfg.api_key_env:
            assert cfg.api_key_env.isupper() and " " not in cfg.api_key_env, p.id
        for env in p.detect.get("env", []):
            assert env.replace("_", "").isalnum() and env.isupper(), p.id
    names = {p.id: p.defaults.api_key_env for p in P.presets()}
    assert names["openai"] == "OPENAI_API_KEY" and names["gemini"] == "GEMINI_API_KEY"
    assert names["jev"] == "TYPESAFE_API_KEY"


def test_invalid_preset_is_refused():
    good = {"id": "x", "family": "cloud", "kind": "openai_compat", "label": "X",
            "checked": {"on": "2026-10-11", "by": "docs"}}
    P.load_catalogue(yaml.safe_dump({"presets": [good]}))
    no_kind = {k: v for k, v in good.items() if k != "kind"} | {"id": "nokind"}
    with pytest.raises(ValueError, match="'nokind'"):
        P.load_catalogue(yaml.safe_dump({"presets": [no_kind]}))
    with pytest.raises(ValueError, match="'badfam'"):
        P.load_catalogue(yaml.safe_dump({"presets": [good | {"id": "badfam", "family": "moon"}]}))
    with pytest.raises(ValueError, match="unknown preset 'nope'"):
        P.preset("nope")


def test_experimental_presets():
    exp = {p.id for p in P.presets() if p.experimental}
    assert exp == {"antigravity", "gemini-cli"}
    assert P.preset("gemini-cli").limitations == ["isolation_unverified"]
    assert P.preset("antigravity").limitations == ["argv_exposure", "instructions_in_prompt",
                                                    "instructions_unverified"]
    cli = [p.id for p in P.presets() if p.family == "cli"]
    assert set(cli[-2:]) == exp  # last in their group
    from wider.core.project import DEFAULT_PROVIDERS
    assert not {c.preset for c in DEFAULT_PROVIDERS} & exp  # never a default
    row = P.preset_row(P.preset("antigravity"))
    assert row["runs"].startswith("Runs: ") and "Experimental" not in row["runs"]
    assert row["warning"].startswith("Experimental: prompts and record text are visible")
    assert "isolation not verified" in P.preset_row(P.preset("gemini-cli"))["warning"]
    assert P.preset_row(P.preset("codex"))["warning"] == ""


def test_cli_presets_disclose_telemetry():
    for p in P.presets():
        if p.family != "cli":
            continue
        assert p.telemetry is not None, p.id
        note = p.telemetry.note
        assert "may send its own usage data" in note, p.id
        cmd = P.config_from_preset(p.id, model="m1", command=NEEDS["command"] if "command" in p.needs else None).command
        sw = p.telemetry.switch
        if p.telemetry.checked:
            assert isinstance(sw, list) and all(t in cmd for t in sw), p.id
        else:
            assert "vendor_telemetry" in p.all_limitations(), p.id
            assert "turned off" in note or "cannot turn" in note, p.id
            if isinstance(sw, list):
                assert not any(t in (p.defaults.command or []) for t in sw), p.id
    # a switch is applied only when checked
    raw = {"id": "t", "family": "cli", "kind": "cli_agent", "label": "T", "checked": {"on": "d", "by": "docs"},
           "defaults": {"command": ["t", "{model}", "-"]},
           "telemetry": {"switch": ["-c", "analytics=false"], "checked": False, "note": "n"}}
    cat, _ = P.load_catalogue(yaml.safe_dump({"presets": [raw]}))
    assert "analytics=false" not in P._command_with_telemetry(cat["t"])
    cat["t"].telemetry.checked = True
    assert P._command_with_telemetry(cat["t"]) == ["t", "{model}", "-c", "analytics=false", "-"]


def test_detect_cards_carry_telemetry_note():
    """CLI presets disclose the tool's own usage data: the detection rows behind the cards carry the note."""
    from wider.core.detect import detect

    rows = {r["preset"]: r for r in detect(env={}, which=lambda _: None)}
    for pid in ("codex", "gemini-cli", "antigravity"):
        assert "may send its own usage data" in rows[pid]["telemetry"], pid
    assert "turns it off" in rows["codex"]["telemetry"]
