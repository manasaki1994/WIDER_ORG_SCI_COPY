"""Balanced-JSON extraction and schema validation for model output."""
from __future__ import annotations

import json
import re
from typing import Any

import jsonschema

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> tuple[Any, str | None]:
    """Find the first balanced JSON object/array in ``text``. Returns (value, error)."""
    if text is None:
        return None, "empty response"
    s = text.strip()
    if not s:
        return None, "empty response"
    try:
        return json.loads(s), None
    except json.JSONDecodeError:
        pass
    m = _FENCE.search(s)
    if m:
        try:
            return json.loads(m.group(1)), None
        except json.JSONDecodeError:
            pass
    # balanced scan from each opening brace
    for start, ch in enumerate(s):
        if ch not in "{[":
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1]), None
                    except json.JSONDecodeError:
                        break
    return None, "no JSON object found"


def validate_json(value: Any, schema: dict) -> str | None:
    try:
        jsonschema.validate(value, schema)
        return None
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path) or "<root>"
        return f"{path}: {e.message}"[:300]
