"""Strict-schema transform for CLIs that demand it (wider.providers.schema_strict, design Decision 4)."""
import jsonschema

from wider.providers.schema_strict import drop_null_optionals, to_strict

SCHEMA = {"type": "object", "required": ["id", "items"], "properties": {
    "id": {"type": "string"},
    "note": {"type": "string"},
    "level": {"enum": ["a", "b"]},
    "any": {"description": "untyped"},
    "items": {"type": "array", "items": {"type": "object", "required": ["k"], "properties": {
        "k": {"type": "integer"}, "why": {"type": "string"}}}},
}}


def test_strict_transform_and_null_drop():
    before = repr(SCHEMA)
    strict = to_strict(SCHEMA)
    assert repr(SCHEMA) == before                                   # the original is untouched
    assert strict["required"] == ["id", "note", "level", "any", "items"]
    assert strict["additionalProperties"] is False
    assert strict["properties"]["id"]["type"] == "string"           # required stays non-null
    assert strict["properties"]["note"]["type"] == ["string", "null"]
    assert strict["properties"]["level"]["enum"] == ["a", "b", None]
    assert strict["properties"]["any"] == {"anyOf": [{"description": "untyped"}, {"type": "null"}]}
    inner = strict["properties"]["items"]["items"]
    assert inner["required"] == ["k", "why"] and inner["additionalProperties"] is False
    assert inner["properties"]["why"]["type"] == ["string", "null"]
    answer = {"id": "x", "note": None, "level": None, "any": None, "items": [{"k": 1, "why": None}]}
    jsonschema.validate(answer, strict)
    cleaned = drop_null_optionals(answer, SCHEMA)
    assert cleaned == {"id": "x", "items": [{"k": 1}]}
    jsonschema.validate(cleaned, SCHEMA)
    # a null in a required field is kept (and fails the original schema, as it should)
    assert drop_null_optionals({"id": None, "items": []}, SCHEMA) == {"id": None, "items": []}
