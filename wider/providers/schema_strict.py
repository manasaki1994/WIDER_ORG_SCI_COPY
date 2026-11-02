"""Strict JSON schemas for CLIs that demand them (Codex ``--output-schema``; design Decision 4).

``to_strict`` makes every property required, lets an optional property be ``null`` instead, and closes every object
(``additionalProperties: false``). ``drop_null_optionals`` removes, after parsing, the keys that were optional in the
original schema and came back ``null``, so the answer validates against the original schema.
"""
from __future__ import annotations

import copy
from typing import Any


def _nullable(schema: dict) -> dict:
    s = dict(schema)
    if "enum" in s and None not in s["enum"]:
        s["enum"] = [*s["enum"], None]
    t = s.get("type")
    if isinstance(t, str) and t != "null":
        s["type"] = [t, "null"]
    elif isinstance(t, list) and "null" not in t:
        s["type"] = [*t, "null"]
    elif t is None and "enum" not in s and "const" not in s:
        return {"anyOf": [s, {"type": "null"}]}
    return s


def to_strict(schema: dict) -> dict:
    """A copy of ``schema`` in the strict form; the original is not changed."""
    return _strict(copy.deepcopy(schema))


def _strict(s: Any) -> Any:
    if not isinstance(s, dict):
        return s
    if s.get("type") == "object" or "properties" in s:
        props = s.get("properties") or {}
        required = set(s.get("required") or [])
        out = {}
        for k, v in props.items():
            v = _strict(v)
            out[k] = v if k in required else _nullable(v)
        s["properties"] = out
        s["required"] = list(props)
        s["additionalProperties"] = False
    if isinstance(s.get("items"), dict):
        s["items"] = _strict(s["items"])
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(s.get(key), list):
            s[key] = [_strict(x) for x in s[key]]
    return s


def drop_null_optionals(obj: Any, schema: dict) -> Any:
    """``obj`` without the keys that are optional in ``schema`` (the original, not the strict copy) and are null."""
    if isinstance(obj, dict) and isinstance(schema, dict):
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        out = {}
        for k, v in obj.items():
            if v is None and k in props and k not in required:
                continue
            out[k] = drop_null_optionals(v, props.get(k, {}))
        return out
    if isinstance(obj, list) and isinstance(schema, dict) and isinstance(schema.get("items"), dict):
        return [drop_null_optionals(x, schema["items"]) for x in obj]
    return obj
