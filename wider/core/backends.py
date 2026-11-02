"""Manage the LLM and embedding backends of a project without editing project.yaml by hand.

API keys are never stored: a backend names the environment variable (``api_key_env``) that holds its key.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from ..providers.secrets import check_key_env_name
from .models import EmbeddingConfig, ProviderConfig
from .project import Project

PROVIDER_KINDS = ("openai_compat", "ollama", "anthropic", "cli_agent", "litellm", "typesafe")
EMBEDDING_KINDS = ("openai", "openai_compat", "ollama", "sentence_transformers")
STAGE_KEYS = ("construct", "screen", "screen_b", "screen_judge", "review", "review_2")
# the stages as rows (Settings, the summary, the backend check): `review_2` is the review row's second reader (2c D0c Q3),
# `screen_b` and `screen_judge` the screen row's pass B and judge (configuration comparison D10)
STAGE_ROWS = ("construct", "screen", "review")
_ROW_OF = {"review_2": "review", "screen_b": "screen", "screen_judge": "screen"}
_USE_OF = {"review_2": "review", "screen_b": "screen (pass B)", "screen_judge": "screen (judge)"}


def stage_row(stage: str) -> str:
    """The row a stage key is shown in: `review_2` in `review`, `screen_b` and `screen_judge` in `screen`, every other
    key in its own."""
    return _ROW_OF.get(stage, stage)


def stage_use(stage: str) -> str:
    """How a stage override's use is named in a backend's uses (the summary, the backend check, a backend row's
    stages): `review_2` as `review`, `screen_b` as "screen (pass B)", `screen_judge` as "screen (judge)"."""
    return _USE_OF.get(stage, stage)


def sends_temperature(cfg) -> bool:
    """Whether a run's temperature reaches the model (configuration comparison D1): not for a ``cli_agent`` or
    ``typesafe`` backend, nor when ``extra.omit_params`` drops it or ``extra.body`` (``extra.options`` for ollama)
    sets it, since those override the run's value after it is set."""
    if cfg.kind in ("cli_agent", "typesafe"):
        return False
    extra = cfg.extra or {}
    if "temperature" in (extra.get("omit_params") or []):
        return False
    if "temperature" in (extra.get("body") or {}):
        return False
    return not (cfg.kind == "ollama" and "temperature" in (extra.get("options") or {}))


# the placeholders providers/cli_agent.py replaces in a cli_agent command; the user message goes to stdin by default
CLI_AGENT_PLACEHOLDERS = ("{model}", "{system_file}", "{system_toml}", "{schema_file}", "{system}", "{user}")

# rough download sizes of local sentence-transformers models, shown before the researcher confirms a download
MODEL_SIZES_MB = {
    "BAAI/bge-m3": 2270,
    "BAAI/bge-base-en-v1.5": 440,
    "BAAI/bge-large-en-v1.5": 1340,
    "BAAI/bge-small-en-v1.5": 130,
    "sentence-transformers/all-MiniLM-L6-v2": 90,
    "sentence-transformers/all-mpnet-base-v2": 440,
    "intfloat/e5-large-v2": 1340,
    "intfloat/multilingual-e5-large": 2240,
}


def _parse_command(command: Any) -> list[str] | None:
    if command is None or command == "":
        return None
    if isinstance(command, list):
        return [str(x) for x in command]
    s = str(command).strip()
    if s.startswith("["):
        parsed = json.loads(s)
        if not isinstance(parsed, list):
            raise ValueError("command must be a JSON list of argv strings")
        return [str(x) for x in parsed]
    import shlex

    return shlex.split(s)


def _check_placeholders(argv: list[str]) -> None:
    """Reject ``{name}`` tokens the cli_agent adapter would pass through literally (such as an old ``{prompt}``)."""
    unknown = sorted({t for a in argv for t in re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", a)} - set(CLI_AGENT_PLACEHOLDERS))
    if unknown:
        raise ValueError(f"unknown placeholder(s) {', '.join(unknown)} in the command; a cli_agent command can use "
                         f"{', '.join(CLI_AGENT_PLACEHOLDERS)}, and the user message is sent on standard input")


# the values the adapters understand; anything else would be ignored silently, so it is refused when saved
EXTRA_CHOICES = {
    "output": ("text", "json", "last_line", "claude_json", "codex_jsonl", "gemini_json", "agy_json"),
    "auth": ("bearer", "api-key", "none"),
    "system_channel": ("file", "prompt"),
}


def _check_name(name: str) -> None:
    """A new backend name is used in UI paths (``/api/providers/{name}``) and CLI arguments: no slash, backslash or
    control character, so a saved backend can always be edited, probed and removed."""
    bad = [c for c in name if c in "/\\" or ord(c) < 32 or ord(c) == 127]
    if bad:
        raise ValueError(f"backend name {name!r} may not contain {'/' if '/' in bad else repr(bad[0])}; "
                         "use letters, digits, '-', '_' or '.'")
    if name in (".", ".."):
        raise ValueError(f"backend name {name!r} is not allowed; use letters, digits, '-', '_' or '.'")


def _check_extra(extra: dict | None) -> None:
    """Keys only by name (Decision 18): sensitive headers and CLI env entries only as ``{env:NAME}``, auth headers only
    with ``auth: none``, and no key-like text elsewhere in ``extra``; ``output``, ``auth`` and ``system_channel`` only
    with a value the adapters know; ``max_output_tokens`` a whole number of at least 1. Raises ValueError naming the path."""
    from ..providers.secrets import check_extra_secrets

    if extra is None:
        return
    if not isinstance(extra, dict):
        raise ValueError("extra must be a mapping")
    check_extra_secrets(extra)
    for key, allowed in EXTRA_CHOICES.items():
        if key in extra and extra[key] not in allowed:
            raise ValueError(f"unknown extra.{key} value {extra[key]!r}; choose one of {', '.join(allowed)}")
    mot = extra.get("max_output_tokens")
    if mot not in (None, ""):
        try:
            ok = not isinstance(mot, bool) and int(str(mot)) >= 1
        except ValueError:
            ok = False
        if not ok:
            raise ValueError(f"extra.max_output_tokens must be a whole number of at least 1, got {mot!r}")


def parse_extra_pairs(pairs: list[str] | None) -> dict:
    """``--extra k=v`` options as a dict: the value as JSON when it parses, as text otherwise; an empty value (``k=``)
    becomes None, which an edit reads as "remove this key"."""
    out: dict = {}
    for pair in pairs or []:
        key, sep, value = str(pair).partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--extra takes key=value, got {pair!r}")
        if value == "":
            out[key] = None
            continue
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


def merge_extra(old: dict, changes: dict | None) -> dict:
    """``old`` with ``changes`` applied key by key; a None or empty-string value removes the key."""
    merged = dict(old or {})
    for k, v in (changes or {}).items():
        if v is None or v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged


def reset_fields(old: Any, new: Any) -> list[str]:
    """The fields a replace changed back to their default: set in ``old``, default in ``new``. ``extra`` is listed
    when ``new`` lost keys ``old`` had."""
    out = []
    for f, info in type(new).model_fields.items():
        if f == "name":
            continue
        a, b = getattr(old, f, None), getattr(new, f, None)
        if f == "extra":
            if set(a or {}) - set(b or {}):
                out.append(f)
            continue
        default = info.default_factory() if info.default_factory else info.default
        if a != b and b == default and a != default:
            out.append(f)
    return out


def free_name(project: Project, base: str, embedding: bool = False) -> str:
    """``base``, or the first free ``base-2``, ``base-3``, … when a backend already has that name."""
    taken = {c.name for c in (project.config.embeddings if embedding else project.config.providers)}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _validate_provider(cfg: ProviderConfig) -> None:
    """The checks every saved provider passes: command and placeholders, ``extra``, positive numbers, a known preset."""
    if cfg.kind == "cli_agent":
        if not cfg.command:
            raise ValueError("a cli_agent provider needs a command (argv template, JSON list)")
        _check_placeholders(cfg.command)
    _check_extra(cfg.extra)
    if cfg.timeout_s <= 0:
        raise ValueError("timeout must be more than 0 seconds")
    if cfg.max_concurrency <= 0:
        raise ValueError("concurrency must be at least 1")
    if cfg.temperature < 0:
        raise ValueError("temperature cannot be negative")
    if cfg.preset:
        from ..providers.presets import preset

        p = preset(cfg.preset)   # raises ValueError for an unknown preset
        if not cfg.model and p.family in ("cli", "cloud"):
            raise ValueError(f"choose a model (`wider providers models --preset {p.id}`)")


def _build(model_cls, data: dict):
    from pydantic import ValidationError

    try:
        return model_cls(**data)
    except ValidationError as e:
        err = e.errors()[0]
        where = ".".join(str(x) for x in err.get("loc", ()))
        raise ValueError(f"{where}: {err.get('msg', 'invalid value')}") from None


# ----------------------------------------------------------------- last checks and locality (no request)
OLLAMA_BASE = "http://127.0.0.1:11434"
CHECK_HINT = "press Check"
CHECK_FAILED = "last check failed"


def probe_key(role: str, name: str) -> str:
    """Store meta key holding one backend's last card check (role ``llm`` or ``embedding``)."""
    return f"backend_probe:{role}:{name}"


def _address(cfg) -> str:
    from .backend_consent import _url

    return (_url(cfg) or "").rstrip("/")


def stored_probe(project: Project, role: str, name: str) -> dict[str, Any] | None:
    raw = project.store.get_meta(probe_key(role, name))
    try:
        return json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return None


def record_probe(project: Project, role: str, cfg, ok: bool | None, locality: str | None,
                 error: str | None = None) -> None:
    """Keep the result of a card Check or ``wider providers|embed probe`` (``ok`` true or false). ``ok`` None records
    only the locality a consent was given for (``allow_backend``): that is not a check, so an earlier check of the same
    model and address keeps its time and result."""
    from .provenance import utcnow

    entry = {"at": utcnow(), "ok": ok, "locality": locality, "model": cfg.model, "address": _address(cfg),
             "error": (error or None) and str(error)[:400]}
    old = stored_probe(project, role, cfg.name)
    if ok is None and old and old.get("ok") is not None and old.get("model") == cfg.model \
            and old.get("address") == entry["address"]:
        entry.update(at=old.get("at"), ok=old.get("ok"), error=old.get("error"))
    project.store.set_meta(probe_key(role, cfg.name), json.dumps(entry))


def last_check(project: Project, role: str, name: str, chk: dict | None = None) -> dict[str, Any] | None:
    """The newer of the backend's last card check and its row in the last ``wider check-backends``, or None."""
    from .summary import backend_check

    found = []
    e = stored_probe(project, role, name)
    if e and e.get("ok") is not None and e.get("at"):
        found.append({"at": e["at"], "ok": bool(e["ok"]), "error": e.get("error"), "via": "probe"})
    chk = backend_check(project) if chk is None else chk
    for r in (chk or {}).get("results", []):
        if r.get("role") == role and r.get("name") == name and chk.get("at"):
            found.append({"at": chk["at"], "ok": bool(r.get("ok")), "error": r.get("error"), "via": "check"})
    return max(found, key=lambda x: x["at"]) if found else None


def llm_locality(project: Project, cfg, chk: dict | None = None) -> tuple[str, str]:
    """``(locality, source)`` of an LLM backend without any request. A loopback Ollama model takes the locality of
    its last check while its model and address are unchanged (``not_checked`` otherwise, shown as "unknown, press
    Check"); every other backend is placed by its endpoint, as before."""
    from .detect import effective_locality
    from .summary import backend_check, host_class

    if cfg.kind != "ollama" or host_class(cfg.base_url or OLLAMA_BASE) != "loopback":
        loc, src, _dest = effective_locality(cfg)   # off loopback Ollama is placed by name: nothing is contacted
        return loc, src
    if (cfg.model or "").endswith("-cloud"):
        return "cloud", "name_suffix"
    e = stored_probe(project, "llm", cfg.name)
    if e and e.get("locality") and e.get("model") == cfg.model and e.get("address") == _address(cfg):
        return e["locality"], "last_check"
    if _address(cfg) == OLLAMA_BASE:   # check-backends rows hold no address: only for the default one
        chk = backend_check(project) if chk is None else chk
        for r in (chk or {}).get("results", []):
            if r.get("role") == "llm" and r.get("name") == cfg.name and r.get("model") == cfg.model and r.get("locality"):
                return r["locality"], "backend_check"
    return "unknown", "not_checked"


def backend_place(project: Project, cfg, embedding: bool = False, chk: dict | None = None) -> dict[str, Any]:
    """``locality``, ``locality_source``, ``locality_label``, ``destination`` and ``last_check`` of one backend, from
    ``project.yaml`` and the store only."""
    from .detect import destination
    from .summary import LOCALITY_LABELS, backend_check, embedding_locality

    chk = backend_check(project) if chk is None else chk
    if embedding:
        loc, src = embedding_locality(cfg.kind, getattr(cfg, "base_url", None)), "endpoint"
    else:
        loc, src = llm_locality(project, cfg, chk)
    label = LOCALITY_LABELS.get(loc, loc)
    if not embedding and cfg.kind == "ollama" and loc == "unknown":
        # a failed check of this model and address could not tell where it runs; "press Check" reads odd right after
        e = stored_probe(project, "llm", cfg.name)
        failed = bool(e and e.get("ok") is False and e.get("model") == cfg.model and e.get("address") == _address(cfg))
        label = f"unknown, {CHECK_FAILED if failed else CHECK_HINT}"
    return {"locality": loc, "locality_source": src, "locality_label": label, "destination": destination(cfg, loc),
            "last_check": last_check(project, "embedding" if embedding else "llm", cfg.name, chk)}


# ----------------------------------------------------------------- providers
def provider_rows(project: Project) -> list[dict]:
    """Providers as plain dicts with ``key_set`` (whether the named environment variable is set), ``default``, the
    stages they serve and :func:`backend_place`. Nothing is contacted."""
    from .summary import backend_check

    chk = backend_check(project)
    out = []
    for pr in project.config.providers:
        d = pr.model_dump(mode="json")
        d["key_set"] = bool(pr.api_key_env and os.environ.get(pr.api_key_env))
        d["default"] = pr.name == project.config.default_provider
        d["stages"] = list(dict.fromkeys(stage_use(s) for s, n in project.config.stage_providers.items() if n == pr.name))
        d.update(backend_place(project, pr, chk=chk))
        out.append(d)
    return out


def add_provider(project: Project, name: str | None = None, kind: str | None = None, model: str = "",
                 base_url: str | None = None, api_key_env: str | None = None, command: Any = None,
                 set_default: bool = False, replace: bool = True, preset: str | None = None,
                 accept_experimental: bool = False, dry_run: bool = False,
                 **extra_fields: Any) -> tuple[ProviderConfig, list[str]]:
    """Add a provider, or with ``replace`` replace one of the same name (an edit that merges is ``update_provider``).
    With ``preset`` every unset field comes from the catalogue and the name defaults to the preset id or the first free
    ``<id>-N``. An experimental preset needs ``accept_experimental`` (its limitations are named otherwise). Returns the
    saved config and the fields the replace reset (``reset_fields``), so callers can warn; ``dry_run`` checks and
    returns them without saving."""
    name = (name or "").strip()
    if preset:
        from ..providers.presets import config_from_preset
        from ..providers.presets import preset as find_preset

        p = find_preset(preset)
        if kind and kind != p.kind:
            raise ValueError(f"preset {preset!r} is a {p.kind} backend, not {kind}")
        if p.experimental and not accept_experimental:
            from ..providers.presets import EXPERIMENTAL_WARNING

            why = EXPERIMENTAL_WARNING.get(p.id) or f"Experimental: {', '.join(p.all_limitations())}."
            raise ValueError(f"{p.label}: {why} Add it only with the acknowledgement (--accept-experimental, or tick "
                             "the box in the form)")
        name = name or free_name(project, p.id)
        cfg = config_from_preset(preset, name=name, model=model or None, base_url=base_url or None,
                                 api_key_env=check_key_env_name(api_key_env), command=_parse_command(command),
                                 **extra_fields)
    else:
        kind = kind or "openai_compat"
        if kind not in PROVIDER_KINDS + ("fake",):
            raise ValueError(f"unknown provider kind {kind!r}; choose one of {', '.join(PROVIDER_KINDS)}")
        if not name:
            raise ValueError("a provider needs a name")
        if kind == "cli_agent" and not command:
            raise ValueError("a cli_agent provider needs a command (argv template, JSON list)")
        _check_extra(extra_fields.get("extra"))
        cfg = _build(ProviderConfig, dict(name=name, kind=kind, model=model or "", base_url=base_url or None,
                                          api_key_env=check_key_env_name(api_key_env),
                                          command=_parse_command(command),
                                          **{k: v for k, v in extra_fields.items() if v is not None}))
    existing = [i for i, p in enumerate(project.config.providers) if p.name == cfg.name]
    if not existing:
        _check_name(cfg.name)
    reset: list[str] = []
    if existing:
        if not replace:
            raise ValueError(f"provider {cfg.name!r} already exists")
        old = project.config.providers[existing[0]]
        if not cfg.extra and old.extra and not preset:  # keep provider-specific extras (e.g. think=False)
            cfg.extra = dict(old.extra)
        reset = reset_fields(old, cfg)
    _validate_provider(cfg)
    if dry_run:
        return cfg, reset
    if existing:
        project.config.providers[existing[0]] = cfg
    else:
        project.config.providers.append(cfg)
    if set_default or project.config.default_provider is None:
        project.config.default_provider = cfg.name
    project.save()
    return cfg, reset


PROVIDER_EDITABLE = ("model", "base_url", "api_key_env", "command", "temperature", "seed", "max_concurrency",
                     "timeout_s", "price_input_per_mtok", "price_output_per_mtok", "price_cached_input_per_mtok",
                     "price_cache_write_per_mtok", "preset", "extra")


def update_provider(project: Project, name: str, /, **changes: Any) -> ProviderConfig:
    """Change some fields of a provider and keep the rest. ``None`` means unchanged; ``extra`` merges key by key (an
    empty value removes the key); ``base_url`` and ``api_key_env`` set to ``""`` are cleared. ``name`` and ``kind``
    cannot change. The merged result is validated before anything is saved."""
    for fixed in ("name", "kind"):
        if changes.get(fixed) is not None:
            raise ValueError(f"the {fixed} of a backend cannot be changed; add a new backend instead")
        changes.pop(fixed, None)
    unknown = sorted(set(changes) - set(PROVIDER_EDITABLE))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}")
    old = project.provider(name)   # KeyError when unknown
    data = old.model_dump()
    for k, v in changes.items():
        if v is None:
            continue
        if k == "extra":
            if not isinstance(v, dict):
                raise ValueError("extra must be a mapping")
            data["extra"] = merge_extra(old.extra, v)
        elif k == "command":
            data["command"] = _parse_command(v)
        elif k == "api_key_env":
            data[k] = check_key_env_name(str(v))
        elif k in ("base_url", "preset"):
            data[k] = str(v).strip() or None
        else:
            data[k] = v
    cfg = _build(ProviderConfig, data)
    _validate_provider(cfg)
    if project.config.stage_providers.get("construct") == name:
        from ..providers.registry import require_text

        require_text(cfg)
    i = next(i for i, p in enumerate(project.config.providers) if p.name == name)
    project.config.providers[i] = cfg
    project.save()
    return cfg


def remove_provider(project: Project, name: str) -> None:
    before = len(project.config.providers)
    project.config.providers = [p for p in project.config.providers if p.name != name]
    if len(project.config.providers) == before:
        raise KeyError(f"provider {name!r} not in project.yaml")
    if project.config.default_provider == name:
        project.config.default_provider = project.config.providers[0].name if project.config.providers else None
    project.config.stage_providers = {s: n for s, n in project.config.stage_providers.items() if n != name}
    project.save()


def set_default_provider(project: Project, name: str) -> None:
    project.provider(name)  # raises KeyError when unknown
    project.config.default_provider = name
    project.save()


def set_stage_provider(project: Project, stage: str, name: str | None) -> None:
    """Use ``name`` for one stage (one of :data:`STAGE_KEYS`); ``None`` removes the override."""
    stage = stage.strip()
    if stage not in STAGE_KEYS:
        raise ValueError(f"unknown stage {stage!r}; choose one of {', '.join(STAGE_KEYS)}")
    if name:
        cfg = project.provider(name)
        if stage in ("construct", "review", "review_2"):   # these stages need a backend that writes text
            from ..providers.registry import require_text
            require_text(cfg)
        project.config.stage_providers[stage] = name
    else:
        project.config.stage_providers.pop(stage, None)
    project.save()


def set_review_readers(project: Project, names: list[str]) -> None:
    """The ``review`` stage's readers (2c D0c Q3): one name sets ``review`` and clears ``review_2``, two set both,
    none clears both. Every name is checked before anything is saved."""
    names = [n for n in names if n]
    if len(names) > 2:
        raise ValueError(f"the review stage takes at most two readers, got {len(names)}")
    from ..providers.registry import require_text
    for n in names:
        require_text(project.provider(n))
    for stage, n in zip(("review", "review_2"), names + [None, None]):
        if n:
            project.config.stage_providers[stage] = n
        else:
            project.config.stage_providers.pop(stage, None)
    project.save()


# ----------------------------------------------------------------- embeddings
def embedding_rows(project: Project) -> list[dict]:
    from .summary import backend_check

    chk = backend_check(project)
    out = []
    for e in project.config.embeddings:
        d = e.model_dump(mode="json")
        d["key_set"] = bool(e.api_key_env and os.environ.get(e.api_key_env))
        d["default"] = e.name == project.config.default_embedding
        d.update(backend_place(project, e, embedding=True, chk=chk))
        d["downloaded"] = None
        d["size_mb"] = None
        if e.kind == "sentence_transformers":
            d["size_mb"] = MODEL_SIZES_MB.get(e.model)
            d["downloaded"] = is_downloaded(e)
        out.append(d)
    return out


def is_downloaded(cfg: EmbeddingConfig) -> bool | None:
    """Whether a local model's weights are in the Hugging Face cache (None when it cannot be told)."""
    if cfg.kind != "sentence_transformers":
        return None
    try:
        from ..embeddings.registry import make_embedder

        emb = make_embedder(cfg)
        return bool(emb.is_downloaded()) if hasattr(emb, "is_downloaded") else None
    except Exception:  # missing optional dependency: report unknown, never crash the setup page
        return None


def _validate_embedding(cfg: EmbeddingConfig) -> None:
    if not cfg.model:
        raise ValueError("an embedding needs a model name")
    if cfg.dim is not None and cfg.dim <= 0:
        raise ValueError("dim must be more than 0")
    if cfg.batch_size <= 0:
        raise ValueError("batch size must be at least 1")
    if cfg.preset:
        from ..providers.presets import embedding_preset

        embedding_preset(cfg.preset)   # raises ValueError for an unknown preset


def add_embedding(project: Project, name: str | None = None, kind: str | None = None, model: str = "",
                  dim: int | None = None, base_url: str | None = None, api_key_env: str | None = None,
                  set_default: bool = False, replace: bool = True, preset: str | None = None,
                  price_input_per_mtok: float | None = None, batch_size: int | None = None,
                  dry_run: bool = False) -> tuple[EmbeddingConfig, list[str]]:
    """Add an embedding model, or with ``replace`` replace one of the same name; ``update_embedding`` merges. Returns
    the saved config and the fields the replace reset."""
    name = (name or "").strip()
    over = dict(model=model or None, dim=dim or None, base_url=base_url or None,
                api_key_env=check_key_env_name(api_key_env), price_input_per_mtok=price_input_per_mtok,
                batch_size=batch_size)
    if preset:
        from ..providers.presets import embedding_config_from_preset
        from ..providers.presets import embedding_preset as find_preset

        p = find_preset(preset)
        if kind and kind != p.kind:
            raise ValueError(f"embedding preset {preset!r} is a {p.kind} model, not {kind}")
        name = name or free_name(project, p.id, embedding=True)
        cfg = embedding_config_from_preset(preset, name=name, **over)
    else:
        kind = kind or "sentence_transformers"
        if kind not in EMBEDDING_KINDS + ("fake",):
            raise ValueError(f"unknown embedding kind {kind!r}; choose one of {', '.join(EMBEDDING_KINDS)}")
        if not name:
            raise ValueError("an embedding needs a name")
        if not model:
            raise ValueError("an embedding needs a model name")
        cfg = _build(EmbeddingConfig, dict(name=name, kind=kind, **{k: v for k, v in over.items() if v is not None}))
    _validate_embedding(cfg)
    existing = [i for i, e in enumerate(project.config.embeddings) if e.name == cfg.name]
    if not existing:
        _check_name(cfg.name)
    reset: list[str] = []
    if existing:
        if not replace:
            raise ValueError(f"embedding {cfg.name!r} already exists")
        reset = reset_fields(project.config.embeddings[existing[0]], cfg)
    if dry_run:
        return cfg, reset
    if existing:
        project.config.embeddings[existing[0]] = cfg
    else:
        project.config.embeddings.append(cfg)
    if set_default or project.config.default_embedding is None:
        project.config.default_embedding = cfg.name
    project.save()
    return cfg, reset


EMBEDDING_EDITABLE = ("model", "dim", "batch_size", "price_input_per_mtok", "base_url", "api_key_env")


def update_embedding(project: Project, name: str, /, **changes: Any) -> EmbeddingConfig:
    """Change some fields of an embedding model and keep the rest (``None`` means unchanged; ``base_url`` and
    ``api_key_env`` set to ``""`` are cleared). ``name`` and ``kind`` cannot change; nothing is saved on a refusal."""
    for fixed in ("name", "kind"):
        if changes.get(fixed) is not None:
            raise ValueError(f"the {fixed} of an embedding model cannot be changed; add a new one instead")
        changes.pop(fixed, None)
    unknown = sorted(set(changes) - set(EMBEDDING_EDITABLE))
    if unknown:
        raise ValueError(f"unknown field(s) {', '.join(unknown)}")
    old = project.embedding(name)   # KeyError when unknown
    data = old.model_dump()
    for k, v in changes.items():
        if v is None:
            continue
        if k == "api_key_env":
            data[k] = check_key_env_name(str(v))
        else:
            data[k] = (str(v).strip() or None) if k == "base_url" else v
    cfg = _build(EmbeddingConfig, data)
    _validate_embedding(cfg)
    i = next(i for i, e in enumerate(project.config.embeddings) if e.name == name)
    project.config.embeddings[i] = cfg
    project.save()
    return cfg


def remove_embedding(project: Project, name: str) -> None:
    before = len(project.config.embeddings)
    project.config.embeddings = [e for e in project.config.embeddings if e.name != name]
    if len(project.config.embeddings) == before:
        raise KeyError(f"embedding {name!r} not in project.yaml")
    if project.config.default_embedding == name:
        project.config.default_embedding = project.config.embeddings[0].name if project.config.embeddings else None
    project.save()


def set_default_embedding(project: Project, name: str) -> None:
    project.embedding(name)
    project.config.default_embedding = name
    project.save()


def download_embedding(project: Project, name: str) -> dict:
    """Load a local model once so its weights land in the cache. Only ever called on an explicit request."""
    import time

    cfg = project.embedding(name)
    if cfg.kind != "sentence_transformers":
        return {"name": name, "ok": True, "note": "remote model, nothing to download"}
    from ..embeddings.registry import make_embedder

    t0 = time.monotonic()
    try:
        emb = make_embedder(cfg)
        emb.allow_download = True
        vec = emb.embed(["download check"])
    except Exception as e:  # report to the UI, do not crash the server
        return {"name": name, "ok": False, "error": str(e)[:400]}
    return {"name": name, "ok": True, "elapsed_s": round(time.monotonic() - t0, 1), "downloaded": True,
            "dim": int(vec.shape[1])}
