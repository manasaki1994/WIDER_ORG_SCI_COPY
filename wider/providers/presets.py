"""Backend preset catalogue (presets.yaml): what WIDER offers, how to find it and its defaults.

Adding from a preset copies every value into the project's backend; ``ProviderConfig.preset`` is only a label, so a
later catalogue change never alters a saved backend. The family is a grouping for the cards only: locality and cost
are always computed from the actual endpoint (``core/summary.py``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from ..core.models import EmbeddingConfig, ProviderConfig

Family = Literal["cli", "cloud", "local"]


class PresetDefaults(BaseModel):
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    command: list[str] | None = None
    extra: dict = Field(default_factory=dict)
    timeout_s: int | None = None
    max_concurrency: int | None = None


class Telemetry(BaseModel):
    switch: list[str] | dict | None = None  # argv tokens or settings; applied only when checked
    checked: bool = False
    note: str


class Checked(BaseModel):
    on: str
    by: Literal["docs", "run"]
    source: str = ""


class Preset(BaseModel):
    id: str
    family: Family
    kind: Literal["openai_compat", "ollama", "anthropic", "cli_agent", "litellm", "typesafe"]
    label: str
    vendor: str = ""
    defaults: PresetDefaults = Field(default_factory=PresetDefaults)
    models: list[str] = Field(default_factory=list)
    detect: dict = Field(default_factory=dict)
    billing: str = ""
    checked: Checked
    needs: list[str] = Field(default_factory=list)
    generates_text: bool = True
    experimental: bool = False
    limitations: list[str] = Field(default_factory=list)
    telemetry: Telemetry | None = None

    def runs_text(self) -> str:
        """Functional check, kept apart from the experimental warning (Decision 20)."""
        if self.checked.by == "run":
            return f"Runs: run on this computer on {self.checked.on}"
        return "Runs: checked against the docs, not yet run on this computer"

    def all_limitations(self) -> list[str]:
        out = list(self.limitations)
        if self.telemetry is not None and not self.telemetry.checked and "vendor_telemetry" not in out:
            out.append("vendor_telemetry")
        return out


class EmbeddingDefaults(BaseModel):
    model: str = ""
    base_url: str | None = None
    api_key_env: str | None = None
    dim: int | None = None
    batch_size: int | None = None


class EmbeddingPreset(BaseModel):
    id: str
    family: Family
    kind: Literal["openai", "openai_compat", "ollama", "sentence_transformers"]
    label: str
    vendor: str = ""
    defaults: EmbeddingDefaults = Field(default_factory=EmbeddingDefaults)
    needs: list[str] = Field(default_factory=list)
    billing: str = ""


EXPERIMENTAL_WARNING = {
    "antigravity": ("Experimental: prompts and record text are visible to other programs on this computer during "
                    "each call (process arguments), and whether it follows system instructions is unverified. "
                    "Not recommended for confidential data."),
    "gemini-cli": ("Experimental: isolation not verified. Your personal Gemini settings, GEMINI.md, MCP servers or "
                   "extensions may take part in each call."),
}

_CATALOGUE: tuple[dict[str, Preset], dict[str, EmbeddingPreset]] | None = None
CATALOGUE_FILE = Path(__file__).with_name("presets.yaml")


def load_catalogue(text: str) -> tuple[dict[str, Preset], dict[str, EmbeddingPreset]]:
    """Validate a catalogue text; a broken entry raises ValueError naming its id."""
    data = yaml.safe_load(text) or {}
    out: dict[str, Preset] = {}
    for raw in data.get("presets") or []:
        pid = (raw or {}).get("id", "?")
        try:
            p = Preset.model_validate(raw)
        except ValidationError as e:
            raise ValueError(f"preset {pid!r} in the backend catalogue is invalid: {e}") from None
        if p.id in out:
            raise ValueError(f"preset {pid!r} appears twice in the backend catalogue")
        out[p.id] = p
    emb: dict[str, EmbeddingPreset] = {}
    for raw in data.get("embeddings") or []:
        pid = (raw or {}).get("id", "?")
        try:
            e = EmbeddingPreset.model_validate(raw)
        except ValidationError as err:
            raise ValueError(f"embedding preset {pid!r} in the backend catalogue is invalid: {err}") from None
        emb[e.id] = e
    return out, emb


def _catalogue() -> tuple[dict[str, Preset], dict[str, EmbeddingPreset]]:
    global _CATALOGUE
    if _CATALOGUE is None:
        _CATALOGUE = load_catalogue(CATALOGUE_FILE.read_text(encoding="utf-8"))
    return _CATALOGUE


def _family_order(p: Preset) -> tuple:
    return ({"cli": 0, "cloud": 1, "local": 2}[p.family], p.experimental)


def presets() -> list[Preset]:
    """Every LLM preset, grouped CLI / cloud / local, experimental ones last in their group."""
    items = list(_catalogue()[0].values())
    return sorted(items, key=_family_order)  # stable: catalogue order within a group


def preset(pid: str) -> Preset:
    cat = _catalogue()[0]
    if pid not in cat:
        raise ValueError(f"unknown preset {pid!r}; choose one of {', '.join(cat)}")
    return cat[pid]


def embedding_presets() -> list[EmbeddingPreset]:
    return list(_catalogue()[1].values())


def embedding_preset(pid: str) -> EmbeddingPreset:
    cat = _catalogue()[1]
    if pid not in cat:
        raise ValueError(f"unknown embedding preset {pid!r}; choose one of {', '.join(cat)}")
    return cat[pid]


def _command_with_telemetry(p: Preset) -> list[str] | None:
    cmd = list(p.defaults.command) if p.defaults.command is not None else None
    t = p.telemetry
    if cmd is not None and t is not None and t.checked and isinstance(t.switch, list):
        cmd = cmd[:-1] + list(t.switch) + cmd[-1:] if cmd and cmd[-1] == "-" else cmd + list(t.switch)
    return cmd


def config_from_preset(pid: str, name: str | None = None, model: str | None = None, **overrides) -> ProviderConfig:
    """A full backend from a preset; ``overrides`` replace any field (``extra`` keys merge over the preset's)."""
    p = preset(pid)
    d = p.defaults
    model = model if model else d.model
    fields: dict = {
        "name": name or p.id,
        "kind": p.kind,
        "model": model or "",
        "base_url": d.base_url,
        "api_key_env": d.api_key_env,
        "command": _command_with_telemetry(p),
        "extra": dict(d.extra),
        "preset": p.id,
    }
    t = p.telemetry
    if t is not None and t.checked and isinstance(t.switch, dict):   # a settings-file switch (Decision 25)
        fields["extra"]["settings_file"] = dict(t.switch)
    if d.timeout_s is not None:
        fields["timeout_s"] = d.timeout_s
    if d.max_concurrency is not None:
        fields["max_concurrency"] = d.max_concurrency
    extra_over = overrides.pop("extra", None) or {}
    for k, v in overrides.items():
        if v is not None:
            fields[k] = v
    for k, v in extra_over.items():
        if v is None or v == "":
            fields["extra"].pop(k, None)
        else:
            fields["extra"][k] = v
    for need in p.needs:
        if not fields.get(need):
            label = {"base_url": "a base URL (--base-url)", "command": "a command (--command)",
                     "model": "a model (--model)"}.get(need, need)
            raise ValueError(f"preset {p.id!r} needs {label}")
    if not fields["model"] and p.family in ("cli", "cloud"):
        raise ValueError(f"choose a model (`wider providers models --preset {p.id}`)")
    return ProviderConfig(**fields)


def embedding_config_from_preset(pid: str, name: str | None = None, **overrides) -> EmbeddingConfig:
    p = embedding_preset(pid)
    d = p.defaults
    fields: dict = {"name": name or p.id, "kind": p.kind, "model": d.model, "base_url": d.base_url,
                    "api_key_env": d.api_key_env, "dim": d.dim, "preset": p.id}
    if d.batch_size is not None:
        fields["batch_size"] = d.batch_size
    for k, v in overrides.items():
        if v is not None:
            fields[k] = v
    for need in p.needs:
        if not fields.get(need):
            raise ValueError(f"embedding preset {p.id!r} needs a {need.replace('_', ' ')}")
    if not fields["model"]:
        raise ValueError(f"embedding preset {p.id!r} needs a model (--model)")
    return EmbeddingConfig(**fields)


def preset_row(p: Preset) -> dict:
    """The card / `providers presets --json` view of a preset."""
    return {
        "id": p.id, "family": p.family, "kind": p.kind, "label": p.label, "vendor": p.vendor,
        "default_model": p.defaults.model, "models": list(p.models), "base_url": p.defaults.base_url,
        "api_key_env": p.defaults.api_key_env, "command": _command_with_telemetry(p),
        "extra": dict(p.defaults.extra), "timeout_s": p.defaults.timeout_s,
        "billing": p.billing, "checked": p.checked.model_dump(), "runs": p.runs_text(), "needs": list(p.needs),
        "generates_text": p.generates_text, "experimental": p.experimental,
        "limitations": p.all_limitations(), "warning": EXPERIMENTAL_WARNING.get(p.id, "") if p.experimental else "",
        "telemetry": p.telemetry.note if p.telemetry else "",
    }
