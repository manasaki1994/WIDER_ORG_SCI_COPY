"""Deterministic fake provider for tests and dry runs."""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .base import ChatRequest, Provider


class FakeProvider(Provider):
    kind = "fake"

    def __init__(self, cfg, responder: Callable[[ChatRequest], Any] | None = None):
        super().__init__(cfg)
        self.responder = responder
        self.calls: list[ChatRequest] = []

    def supports_native_schema(self) -> bool:
        return True

    async def _call(self, req: ChatRequest):
        self.calls.append(req)
        delay = float((self.cfg.extra or {}).get("delay_s") or 0)   # demos: make a fake run take visible time
        if delay > 0:
            import asyncio
            await asyncio.sleep(delay)
        if self.responder is not None:
            out = self.responder(req)
        elif _assistant(req.schema_):
            out = _assistant_answer(req)
        elif req.schema_ is not None:
            out = _example_from_schema(req.schema_)
        else:
            out = "ok"
        text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
        from ..core.usage import normalised
        return text, normalised(input_uncached=len(req.user.split()), output=len(text.split())), {"fake": True}


def _example_from_schema(schema: dict) -> Any:
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    if t == "object":
        # `sum` is 42 so the backend probe (17 + 25) passes on a fake project
        return {k: 42 if k == "sum" and (v or {}).get("type") == "integer" else _example_from_schema(v)
                for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return []
    if t == "integer":
        return schema.get("minimum", 0)
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return False
    return ""


def _assistant(schema: dict | None) -> bool:
    from ..assistant.ask import is_assistant_schema
    return is_assistant_schema(schema)


def _assistant_answer(req: ChatRequest) -> dict:
    """Ask WIDER's synthetic answer (guide-assistant D20): the first sentence of the first section sent and its id in
    brackets, one known button of the page and "Merge all" (unknown), one draft; literal markup only when the question
    says "test markup"."""
    import re

    user = req.user
    question = user.rsplit("The researcher's question:\n", 1)[-1].split("\n\nYour previous reply", 1)[0]
    sid, sentence = "", "WIDER's documents say nothing more."
    if "Sections of WIDER's documents:\n\n" in user:
        block = user.split("Sections of WIDER's documents:\n\n", 1)[1]
        m = re.match(r"\[([^\]]+)\][^\n]*\n[^\n]*\n(.*?)(?:\n\n|$)", block, re.S)
        if m:
            sid = m.group(1)
            text = re.sub(r"^\s*-\s*", "", m.group(2).strip()).replace("\n", " ")
            sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    page, buttons = "", []
    m = re.search(r'The buttons you may name in "show" are only these, on page (\S+): (.*)\.$', req.system)
    if m:
        page = m.group(1)
        buttons = [] if m.group(2) == "none" else [b.strip() for b in m.group(2).split(", ")]
    answer = f"{sentence} [{sid}]" if sid else sentence
    if "test markup" in question:
        answer += ' <a href="x">link</a> ![p](https://example.org/x.png)'
    show = ([{"page": page, "button": buttons[0]}] if buttons else []) + [{"page": page, "button": "Merge all"}]
    return {"answer": answer, "sources": [sid] if sid else [], "show": show,
            "drafts": [{"for": "methods section", "text": f"We followed WIDER's documents: {sentence}"}]}
