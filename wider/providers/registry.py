from __future__ import annotations

from ..core.models import ProviderConfig
from .base import Provider

PROBE_SCHEMA = {
    "type": "object",
    "properties": {"status": {"type": "string", "enum": ["ok"]},
                   "sum": {"type": "integer"},
                   "model_note": {"type": "string"}},
    "required": ["status", "sum"],
    "additionalProperties": False,
}
PROBE_SYSTEM = "You are a test endpoint. Answer with JSON only."
PROBE_USER = 'Set status to "ok", set sum to 17 + 25, and put a few words about yourself in model_note.'


def make_provider(cfg: ProviderConfig, locality: tuple[str, str] | None = None) -> Provider:
    """The adapter for ``cfg``; ``locality`` is the effective ``(locality, source)`` (core/detect.py), stored on the
    provider so its usage rows and costs follow it."""
    p = _make(cfg)
    if locality is not None:
        p.locality, p.locality_source = locality
    return p


def research_provider(project, cfg: ProviderConfig, operation: str = "research") -> Provider:
    """The adapter for a run that sends project or record text (screening, construct and adjacent proposals). Raises
    ``BackendConsentRequired`` before anything is sent when a network, cloud or unknown backend has no consent for
    ``operation`` (Ask WIDER passes ``assistant``), and ProviderError ("cannot reach ...") for a loopback Ollama that
    does not answer."""
    from ..core.backend_consent import require_backend_consent
    from .base import ProviderError

    info = require_backend_consent(project, cfg, operation)
    if info["unreachable"]:
        from urllib.parse import urlsplit
        u = urlsplit(cfg.base_url or "http://127.0.0.1:11434")
        raise ProviderError(f"cannot reach {u.hostname}:{u.port or 11434} (is Ollama running?)")
    return make_provider(cfg, (info["locality"], info["source"]))


def backend_identity(cfg: ProviderConfig) -> dict:
    """The run snapshot a backend would record (``Provider.identity()``), for a preflight. It builds the adapter only to
    read its identity: no model call, nothing leaves the computer (a CLI backend runs its ``--version`` locally), so it
    needs no consent."""
    return make_provider(cfg).identity()


def _make(cfg: ProviderConfig) -> Provider:
    k = cfg.kind
    if k == "openai_compat":
        from .openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(cfg)
    if k == "ollama":
        from .ollama import OllamaProvider
        return OllamaProvider(cfg)
    if k == "anthropic":
        from .anthropic_sdk import AnthropicProvider
        return AnthropicProvider(cfg)
    if k == "cli_agent":
        from .cli_agent import CliAgentProvider
        return CliAgentProvider(cfg)
    if k == "fake":
        from .fake import FakeProvider
        return FakeProvider(cfg)
    if k == "typesafe":
        from .typesafe import TypesafeProvider
        return TypesafeProvider(cfg)
    if k == "litellm":
        from .litellm_adapter import LiteLLMProvider
        return LiteLLMProvider(cfg)
    raise ValueError(f"unknown provider kind {k!r}")


def generates_text(cfg: ProviderConfig) -> bool:
    """False for a scoring-only backend (kind ``typesafe``, or a preset marked ``generates_text: false``)."""
    if cfg.kind == "typesafe":
        return False
    if getattr(cfg, "preset", None):
        from .presets import preset
        try:
            return preset(cfg.preset).generates_text
        except ValueError:
            return True
    return True


def require_text(cfg: ProviderConfig) -> ProviderConfig:
    """``cfg`` when it can write text; a ValueError for Jev (construct and adjacent proposals need text)."""
    if not generates_text(cfg):
        from .typesafe import NO_TEXT
        raise ValueError(NO_TEXT)
    return cfg


JEV_PROBE_STATES = ("17 + 25 = 42", "17 + 25 = 41")
JEV_PROBE_SCHEMA = {"type": "object", "properties": {"correct": {"type": "boolean"}}, "required": ["correct"]}
JEV_PROBE_SYSTEM = "Is this sum correct?"


async def _probe_scoring(cfg: ProviderConfig, p: Provider, base: dict) -> dict:
    """Two calls: the correct sum must score above 0.5 and the wrong one below."""
    from .base import ChatRequest

    results, attempts, elapsed, usage = [], 0, 0.0, {}
    for state in JEV_PROBE_STATES:
        r = await p.complete(ChatRequest(system=JEV_PROBE_SYSTEM, user=state, schema=JEV_PROBE_SCHEMA), retries=2)
        attempts += r.attempts
        elapsed += r.elapsed_s
        usage = r.usage or usage
        if not r.ok:
            return {**base, "ok": False, "attempts": attempts, "elapsed_s": round(elapsed, 2), "error": r.error,
                    "note": None, "parsed": None, "usage": usage, "identity": p.identity()}
        results.append((r.meta.get("probabilities") or {}).get("correct"))
    ok = all(isinstance(x, (int, float)) for x in results) and results[0] > 0.5 and results[1] < 0.5
    return {**base, "ok": ok, "attempts": attempts, "elapsed_s": round(elapsed, 2),
            "error": None if ok else f"wrong answer: {JEV_PROBE_STATES[0]} scored {results[0]}, "
                                     f"{JEV_PROBE_STATES[1]} scored {results[1]}",
            "note": None, "parsed": {"probabilities": results}, "usage": usage, "identity": p.identity()}


async def probe(cfg: ProviderConfig, *, locality: tuple[str, str] | None = None) -> dict:
    """One tiny structured call; returns a summary dict suitable for the CLI and UI. A backend problem (unknown kind,
    missing value, unreachable endpoint) is a failed probe with its message, never an exception. Callers check consent
    first (``core/backend_consent.py``, operation ``probe``)."""
    from .base import ChatRequest, ProviderError

    base = {"name": cfg.name, "kind": cfg.kind, "model": cfg.model}
    try:
        p = make_provider(cfg, locality)
        if not p.generates_text:
            return await _probe_scoring(cfg, p, base)
        req = ChatRequest(system=PROBE_SYSTEM, user=PROBE_USER, schema=PROBE_SCHEMA, max_tokens=200)
        r = await p.complete(req, retries=2)
    except (ProviderError, ValueError) as e:
        return {**base, "ok": False, "attempts": 0, "elapsed_s": 0.0, "error": str(e), "note": None, "parsed": None,
                "usage": {}, "identity": None}
    ok = r.ok and isinstance(r.parsed, dict) and r.parsed.get("sum") == 42
    return {**base, "ok": ok, "attempts": r.attempts,
            "elapsed_s": round(r.elapsed_s, 2), "error": r.error if not r.ok else (None if ok else f"wrong answer: {r.parsed}"),
            "note": (r.parsed or {}).get("model_note") if isinstance(r.parsed, dict) else None,
            "parsed": r.parsed, "usage": r.usage, "identity": p.identity()}
