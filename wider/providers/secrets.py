"""Keys only by environment variable name (Decision 18): sensitive headers and CLI environment entries hold
``{env:NAME}`` references, resolved only when a request is built; every other ``extra`` value is scanned for text that
looks like a key. Messages name the path, never the value."""
from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Any

ENV_REF = re.compile(r"^\{env:([A-Za-z_][A-Za-z0-9_]*)\}$")
SENSITIVE_HEADER_WORDS = ("key", "token", "auth", "secret", "cookie")
SENSITIVE_ENV_WORDS = ("key", "token", "secret", "password", "credential")
AUTH_HEADERS = ("authorization", "api-key", "x-api-key")
KEY_PREFIXES = ("sk-", "sk-ant-", "AIza", "xai-", "gsk_", "hf_", "ghp_", "github_pat_")
_LONG_RUN = re.compile(r"[A-Za-z0-9_-]{32,}")


class SecretError(ValueError):
    """A key-like value where only an ``{env:NAME}`` reference is allowed."""


def is_sensitive_header(name: str, extra: dict | None = None) -> bool:
    low = name.lower()
    listed = [str(h).lower() for h in (extra or {}).get("sensitive_headers") or []]
    return any(w in low for w in SENSITIVE_HEADER_WORDS) or low in listed


def is_sensitive_env(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in SENSITIVE_ENV_WORDS)


def env_ref(value: Any) -> str | None:
    """The variable name of an ``{env:NAME}`` reference, else None."""
    m = ENV_REF.match(value) if isinstance(value, str) else None
    return m.group(1) if m else None


def looks_like_key(value: str) -> bool:
    if env_ref(value):
        return False
    v = value.strip()
    if v.lower().startswith("bearer "):
        return True
    if any(v.startswith(p) for p in KEY_PREFIXES):
        return True
    if v.startswith(("http://", "https://")):   # URLs pass (a long path segment is not a key)
        return False
    return bool(_LONG_RUN.search(v))


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def check_key_env_name(value: str | None) -> str | None:
    """``api_key_env`` must name an environment variable, never hold the key. Returns the stripped name (None when
    empty); raises SecretError without echoing the value."""
    v = (value or "").strip()
    if not v:
        return None
    if not _ENV_NAME.match(v):
        raise SecretError("api_key_env must be the name of an environment variable (letters, digits and '_', not "
                          "starting with a digit), not the key itself; nothing was saved")
    mixed = any(c.islower() for c in v) and any(c.isupper() for c in v) and any(c.isdigit() for c in v)
    if any(v.startswith(p) for p in KEY_PREFIXES) or (len(v) >= 20 and mixed):
        raise SecretError("api_key_env looks like a key value, not the name of an environment variable; put the key "
                          "in an environment variable and give its name (for example OPENAI_API_KEY); nothing was saved")
    return v


def _walk(obj: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        yield path, obj


def check_extra_secrets(extra: dict | None) -> None:
    """Refuse plain values in sensitive headers and sensitive CLI env entries, auth headers unless ``auth: none``, and
    key-like text anywhere else in ``extra``. Raises SecretError naming the path."""
    extra = extra or {}
    headers = extra.get("headers") or {}
    if not isinstance(headers, dict):
        raise SecretError("extra.headers must be a mapping of header names to values")
    for name, value in headers.items():
        if str(name).lower() in AUTH_HEADERS and extra.get("auth", "bearer") != "none":
            raise SecretError(f"header {name} is set from api_key_env; to send it yourself set extra.auth to none "
                              "and write {env:NAME}")
        if is_sensitive_header(str(name), extra) and not env_ref(value):
            raise SecretError(f"header {name} is sensitive: store its value in an environment variable and write "
                              "{env:NAME}")
    env = extra.get("env") or {}
    if not isinstance(env, dict):
        raise SecretError("extra.env must be a mapping of variable names to values")
    for name, value in env.items():
        if is_sensitive_env(str(name)) and not env_ref(value):
            raise SecretError(f"extra.env.{name} looks like a key variable: write {{env:NAME}} to pass a value from "
                              "your environment, never the value itself")
    for path, value in _walk({k: v for k, v in extra.items() if k not in ("headers", "env")}, "extra"):
        if looks_like_key(value):
            raise SecretError(f"{path} looks like a key: store it in an environment variable and write {{env:NAME}}")
    for name, value in headers.items():
        if not is_sensitive_header(str(name), extra) and isinstance(value, str) and looks_like_key(value):
            raise SecretError(f"extra.headers.{name} looks like a key: store it in an environment variable and write "
                              "{env:NAME}")


def resolve_env_refs(mapping: dict, what: str, environ=os.environ) -> dict[str, str]:
    """Values with ``{env:NAME}`` replaced by the variable's value; a missing or empty variable raises KeyError with
    a message naming the entry and the variable (never a value)."""
    out: dict[str, str] = {}
    for name, value in (mapping or {}).items():
        var = env_ref(value)
        if var is None:
            out[str(name)] = str(value)
            continue
        got = environ.get(var)
        if not got:
            raise KeyError(f"{what} {name} needs environment variable {var}, which is not set")
        out[str(name)] = got
    return out
