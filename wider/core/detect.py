"""Find backends on this computer, list a backend's models, and resolve where a backend's calls go.

Detection (design Decision 5) runs only when asked (``wider providers detect`` or the "Find backends on this computer"
button). It looks up executables (PATH, then the preset's bundle globs), runs ``--version``, reads which key variables
are set (names only), checks that credential files exist (never opens them) and sends short GETs to 127.0.0.1 only.
No vendor status command runs and nothing leaves the computer.

Ollama routing (Decision 19): a model reached through a loopback Ollama may still run on ollama.com. The local
``/api/tags`` list says which; nothing is ever sent to the cloud to find out.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .summary import host_class, provider_locality

LOOPBACK = "127.0.0.1"
HTTP_TIMEOUT_S = 0.5
VERSION_TIMEOUT_S = 2.0
ROUTING_CACHE_S = 60.0

HttpGet = Callable[[str], "tuple[int, Any] | None"]


def http_get(url: str, timeout: float = HTTP_TIMEOUT_S) -> tuple[int, Any] | None:
    """GET a loopback URL: ``(status, json or None)``, or None when nothing answers. Any host other than 127.0.0.1 is
    refused, so detection can only ever contact this computer."""
    if urlsplit(url).hostname != LOOPBACK:
        raise ValueError(f"detection only contacts {LOOPBACK}, not {urlsplit(url).hostname}")
    import httpx

    try:
        r = httpx.get(url, timeout=timeout, follow_redirects=False)
    except (httpx.TimeoutException, httpx.TransportError):
        return None
    try:
        data = r.json()
    except ValueError:
        data = None
    return r.status_code, data


def run_version(argv: list[str]) -> str | None:
    """First line of ``<exe> --version`` (2 s), or None."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=VERSION_TIMEOUT_S, stdin=subprocess.DEVNULL,
                             cwd=os.path.expanduser("~"))
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or out.stderr or "").strip()
    return text.splitlines()[0][:120] if text else None


def tilde(path: str | None, home: str | None = None) -> str | None:
    """``path`` with the home folder shown as ``~``."""
    if not path:
        return path
    home = (home or os.path.expanduser("~")).rstrip("/")
    if path == home:
        return "~"
    return "~" + path[len(home):] if path.startswith(home + "/") else path


# ----------------------------------------------------------------- Ollama routing and destination
_ROUTING_CACHE: dict[str, tuple[float, Any]] = {}


def tags_base(base: str) -> str | None:
    """The address the Ollama tag list is read from: ``localhost`` is read at 127.0.0.1 (the only host detection
    contacts); any other loopback name (127.0.0.2, ::1, *.localhost) is not read, so its routing stays unknown."""
    parts = urlsplit(base)
    host = parts.hostname or ""
    if host == LOOPBACK:
        return base
    if host == "localhost":
        netloc = LOOPBACK + (f":{parts.port}" if parts.port else "")
        return parts._replace(netloc=netloc).geturl()
    return None


def _ollama_tags(base: str, get: HttpGet, fresh: bool) -> Any:
    base = tags_base(base) or base     # an unreadable address is refused by http_get, so the tags stay None
    now = time.monotonic()
    hit = _ROUTING_CACHE.get(base)
    if hit and not fresh and now - hit[0] < ROUTING_CACHE_S:
        return hit[1]
    try:
        r = get(base.rstrip("/") + "/api/tags")
    except ValueError:
        r = None
    tags = r[1] if r and r[0] == 200 and isinstance(r[1], dict) else None
    _ROUTING_CACHE[base] = (now, tags)
    return tags


def ollama_routing(cfg, get: HttpGet | None = None, fresh: bool = False) -> tuple[str, str]:
    """``(locality, source)`` of an Ollama model: ``cloud`` when the local tag list marks it remote or its name ends in
    ``-cloud``, ``local`` when listed without remote fields, else ``unknown`` (``not_established``). Off loopback only
    the name is used (nothing is contacted)."""
    model = cfg.model or ""
    base = (cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
    if host_class(base) != "loopback":
        return ("cloud", "name_suffix") if model.endswith("-cloud") else ("unknown", "not_established")
    tags = _ollama_tags(base, get or http_get, fresh)
    entry = None
    for m in (tags or {}).get("models") or []:
        if isinstance(m, dict) and model in (m.get("name"), m.get("model")):
            entry = m
            break
    if entry is not None and (entry.get("remote_host") or entry.get("remote_model")):
        return "cloud", "ollama_tags"
    if model.endswith("-cloud"):
        return "cloud", "name_suffix"
    if entry is not None:
        return "local", "ollama_tags"
    return "unknown", "not_established"


def ollama_answers(cfg, get: HttpGet | None = None) -> bool:
    """Whether the loopback Ollama answered its tag list (read without the cache)."""
    base = (cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
    return _ollama_tags(base, get or http_get, fresh=True) is not None


def _preset(cfg):
    if not getattr(cfg, "preset", None):
        return None
    from ..providers.presets import preset
    try:
        return preset(cfg.preset)
    except ValueError:
        return None


def ollama_cloud_model(cfg, locality: str | None) -> bool:
    """Whether an Ollama backend's calls run on ollama.com: a loopback Ollama whose routing says ``cloud`` (its tag list
    or a ``-cloud`` name), or a model named ``*-cloud`` on any other host. A host off this computer is ``cloud`` by
    its address alone, which says nothing about ollama.com."""
    if locality != "cloud":
        return False
    base = cfg.base_url or "http://127.0.0.1:11434"
    return host_class(base) == "loopback" or (cfg.model or "").endswith("-cloud")


def destination(cfg, locality: str | None = None) -> str:
    """Where the backend's calls go, in words: "this computer", a host, "<vendor> (via <tool>)" or "unknown …"."""
    kind = cfg.kind
    if kind == "fake":
        return "nowhere (test backend)"
    loc = locality or provider_locality(kind, cfg.base_url)
    if kind == "cli_agent":
        p = _preset(cfg)
        if p and p.vendor:
            return f"{p.vendor} (via {p.label.removesuffix(' CLI')})"
        return "unknown (custom CLI)"
    if kind == "ollama":
        base = cfg.base_url or "http://127.0.0.1:11434"
        host = urlsplit(base).hostname or ""
        if ollama_cloud_model(cfg, loc):
            return "ollama.com (via Ollama)" if host_class(base) == "loopback" else f"ollama.com (via Ollama at {host})"
        if loc == "cloud":    # an Ollama on a remote host runs the model there
            return f"{host} (via Ollama)"
        if loc == "unknown":
            return "unknown (via Ollama)"
        if loc == "network":
            return f"{host} (another computer on your network: data leaves this computer)"
        return "this computer"
    if loc in ("local", "test"):
        return "this computer"
    if kind == "anthropic":
        return urlsplit(cfg.base_url or "https://api.anthropic.com").hostname or "api.anthropic.com"
    host = urlsplit(cfg.base_url or "https://api.openai.com/v1").hostname or "unknown"
    if loc == "network":
        return f"{host} (another computer on your network: data leaves this computer)"
    return host


def effective_locality(cfg, get: HttpGet | None = None, fresh: bool = False) -> tuple[str, str, str]:
    """``(locality, source, destination)``: Ollama by its routing, every other backend by its endpoint."""
    if cfg.kind == "ollama":
        loc, src = ollama_routing(cfg, get, fresh)
        if host_class(cfg.base_url or "http://127.0.0.1:11434") != "loopback" and loc != "cloud":
            loc = provider_locality(cfg.kind, cfg.base_url, routing=(loc, src))
    else:
        loc, src = provider_locality(cfg.kind, cfg.base_url), "endpoint"
    return loc, src, destination(cfg, loc)


# ----------------------------------------------------------------- detection
def _row(p, **kw) -> dict:
    from ..providers.presets import preset_row

    card = preset_row(p)
    row = {"preset": p.id, "family": p.family, "label": p.label, "found": False, "version": None, "where": None,
           "signed_in": "unknown", "key_env": p.defaults.api_key_env, "key_set": None, "server": None, "models": [],
           "locality": None, "destination": None, "billing": p.billing, "checked": card["runs"],
           "experimental": p.experimental, "warning": card["warning"], "telemetry": card["telemetry"]}
    row.update(kw)
    return row


def detect(env: dict | None = None, which: Callable[[str], str | None] = shutil.which,
           run_version: Callable[[list[str]], str | None] | None = None, http_get: HttpGet | None = None,
           exists: Callable[[str], bool] = os.path.exists, home: str | Path | None = None,
           bundle_root: str | None = None, globber=None) -> list[dict]:
    """One row per LLM preset. Runs nothing that sends data off the computer."""
    import glob as _glob

    from ..core.models import ProviderConfig
    from ..providers.cli_agent import find_executable
    from ..providers.presets import config_from_preset, presets

    env = os.environ if env is None else env
    run_version = run_version or globals()["run_version"]
    http_get = http_get or globals()["http_get"]
    home = str(home or os.path.expanduser("~"))
    globber = globber or _glob.glob

    def expand(path: str) -> str:
        return home + path[1:] if path.startswith("~") else path

    rows: list[dict] = []
    for p in presets():
        key_envs = list(p.detect.get("env") or ([p.defaults.api_key_env] if p.defaults.api_key_env else []))
        keys_set = [k for k in key_envs if env.get(k)]
        if p.family == "cli":
            binary = p.detect.get("binary")
            exe = find_executable(binary, list(p.detect.get("bundle_globs") or []), which=which, home=home,
                                  root=bundle_root, globber=globber) if binary else None
            version = run_version([exe, *(p.detect.get("version_args") or ["--version"])]) if exe else None
            version = version.replace(home, "~") if version else version
            creds = [c for c in p.detect.get("credential_files") or [] if exists(expand(c))]
            signed = "yes" if (keys_set or creds) else "unknown"
            cfg = ProviderConfig(name=p.id, kind=p.kind, model="", command=p.defaults.command, preset=p.id)
            rows.append(_row(p, found=bool(exe), version=version, where=tilde(exe, home), signed_in=signed if exe else "unknown",
                             key_env=key_envs[0] if key_envs else None, key_set=bool(keys_set) if key_envs else None,
                             models=list(p.models), locality="cloud", destination=destination(cfg, "cloud")))
        elif p.family == "cloud":
            cfg = config_from_preset(p.id, model="detect", base_url=p.defaults.base_url or "https://example.invalid")
            rows.append(_row(p, found=bool(keys_set), key_env=p.defaults.api_key_env, key_set=bool(keys_set),
                             signed_in="yes" if keys_set else "no", models=list(p.models),
                             locality=provider_locality(cfg.kind, cfg.base_url), destination=destination(cfg)))
        else:
            rows.append(_detect_local(p, http_get))
    return rows


def _detect_local(p, get: HttpGet) -> dict:
    from ..providers.presets import config_from_preset

    found, server, models, status = False, None, [], None
    for port in p.detect.get("ports") or []:
        url = f"http://{LOOPBACK}:{port['port']}{port['path']}"
        try:
            r = get(url)
        except ValueError:
            r = None
        if r is None:
            continue
        code, data = r
        if code == 200:
            found, server = True, f"{LOOPBACK}:{port['port']}"
            if p.kind == "ollama":
                models = [m.get("name") for m in (data or {}).get("models") or [] if isinstance(m, dict)]
            else:
                models = [m.get("id") for m in (data or {}).get("data") or [] if isinstance(m, dict)]
            models = [m for m in models if m]
            break
        if p.detect.get("health"):
            h = get(f"http://{LOOPBACK}:{port['port']}{p.detect['health']}")
            if h and h[0] == 503:
                server, status = f"{LOOPBACK}:{port['port']}", "loading"
                break
    base = f"http://{server}" + ("/v1" if p.kind == "openai_compat" else "") if server else p.defaults.base_url
    cfg = config_from_preset(p.id, base_url=base)
    model_rows = []
    for m in models:
        if p.kind == "ollama":
            loc = "cloud" if m.endswith("-cloud") else "local"
        else:
            loc = provider_locality(cfg.kind, cfg.base_url)
        model_rows.append({"id": m, "locality": loc,
                           "destination": "ollama.com (via Ollama)" if loc == "cloud" else "this computer"})
    return _row(p, found=found, server=server, status=status, models=models, model_rows=model_rows,
                locality="local", destination="this computer", signed_in="unknown", key_set=None)


# ----------------------------------------------------------------- model lists
class NeedsSavedBackend(ValueError):
    """A model list of an unsaved backend that would need consent: nothing is sent; save it, then allow it."""


def list_models(cfg=None, preset_id: str | None = None, base_url: str | None = None, project=None,
                kind: str | None = None) -> list[str]:
    """The models a backend offers. Local servers and cloud APIs are asked (cloud only on an explicit action, with the
    key header only); CLI and Jev presets return the catalogue list. Raises ValueError ("cannot reach …", the
    missing-key message); a missing key sends nothing. A network, cloud or unknown backend needs ``models`` consent
    in ``project`` (raises ``BackendConsentRequired``); an unsaved preset that would need it is refused, since
    consent is kept per saved backend. ``kind`` with ``base_url`` asks an unsaved backend filled in by hand."""
    import asyncio

    from ..providers.base import ProviderError
    from ..providers.presets import config_from_preset, preset
    from ..providers.registry import make_provider
    from .backend_consent import assess, require_backend_consent

    if cfg is None and kind and not preset_id:
        from .models import ProviderConfig
        if kind in ("cli_agent", "typesafe", "fake"):
            raise ValueError(f"backend kind {kind} has no server to ask; type the model name")
        cfg = ProviderConfig(name=f"unsaved {kind}", kind=kind, model="list", base_url=base_url or None)
    if cfg is None:
        if not preset_id:
            raise ValueError("name a backend, a preset or a kind")
        p = preset(preset_id)
        over = {"base_url": base_url} if base_url else {}
        if "command" in p.needs:
            return list(p.models)
        cfg = config_from_preset(preset_id, model=p.defaults.model or "list", **over)
    if cfg.kind in ("cli_agent", "typesafe", "fake"):
        p = _preset(cfg)
        return list(p.models) if p else ([cfg.model] if cfg.model else [])
    if cfg.api_key_env and not os.environ.get(cfg.api_key_env):
        raise ValueError(f"environment variable {cfg.api_key_env} is not set; nothing was sent")
    if project is not None and any(x.name == cfg.name for x in project.config.providers):
        require_backend_consent(project, cfg, "models")
    elif assess(cfg)["needs"]:
        raise NeedsSavedBackend(f"{cfg.name}: save the backend first, then allow it to list models; nothing was sent")
    prov = make_provider(cfg)
    if not hasattr(prov, "list_models"):
        raise ValueError(f"backend kind {cfg.kind} cannot list its models; type the model name")
    try:
        return list(asyncio.run(prov.list_models()))
    except ProviderError as e:
        raise ValueError(str(e)) from None
