#!/usr/bin/env python3
"""A local mock LLM server for testing the setup page: synthetic answers only, standard library only.

    python3 scripts/mock_llm_server.py 8794

Listens on 127.0.0.1 only and answers:
- `GET /v1/models` (OpenAI-compatible model list) and `GET /api/tags` (Ollama model list): two synthetic models;
- `POST /v1/chat/completions`: a JSON answer filled from the request's JSON schema (first enum value, `sum` = 42 so the
  probe passes, empty strings and lists otherwise), with synthetic token counts. An Ask WIDER question (its answer
  schema) gets the same synthetic answer as FakeProvider's: the first sentence of the first section sent with its id
  in brackets, one known button of the page and "Merge all", one draft (guide-assistant D20).
It reads no file, stores nothing and never contacts another host. Used by the `ui-tester` agents (ports 8794, 8795).
"""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = ["mock-small", "mock-large"]


def fill(schema: dict, name: str = "") -> object:
    """A synthetic value that matches `schema`."""
    if "enum" in schema:
        return schema["enum"][0]
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object":
        return {k: fill(v, k) for k, v in (schema.get("properties") or {}).items()}
    if t == "array":
        return []
    if t == "integer":
        return 42 if name == "sum" else int(schema.get("minimum", 0))
    if t == "number":
        return float(schema.get("minimum", 0))
    if t == "boolean":
        return False
    if t == "null":
        return None
    return "synthetic mock answer" if name == "model_note" else ""


ASSISTANT_KEYS = {"answer", "sources", "show", "drafts"}


def assistant_answer(system: str, user: str) -> dict:
    """Ask WIDER's synthetic answer, the same as `wider/providers/fake.py:_assistant_answer` (kept in step by
    `test_mock_server_assistant_answer`); this script imports nothing of WIDER."""
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
    m = re.search(r'The buttons you may name in "show" are only these, on page (\S+): (.*)\.$', system)
    if m:
        page = m.group(1)
        buttons = [] if m.group(2) == "none" else [b.strip() for b in m.group(2).split(", ")]
    text = f"{sentence} [{sid}]" if sid else sentence
    if "test markup" in question:
        text += ' <a href="x">link</a> ![p](https://example.org/x.png)'
    show = ([{"page": page, "button": buttons[0]}] if buttons else []) + [{"page": page, "button": "Merge all"}]
    return {"answer": text, "sources": [sid] if sid else [], "show": show,
            "drafts": [{"for": "methods section", "text": f"We followed WIDER's documents: {sentence}"}]}


def _messages(body: dict, role: str) -> str:
    return "\n\n".join(str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == role)


def answer(body: dict) -> dict:
    fmt = body.get("response_format") or {}
    schema = (fmt.get("json_schema") or {}).get("schema")
    if schema and set(schema.get("properties") or {}) == ASSISTANT_KEYS:
        content = assistant_answer(_messages(body, "system"), _messages(body, "user"))
    else:
        content = fill(schema) if schema else {"status": "ok", "sum": 42}
    return {
        "id": "mock-1", "object": "chat.completion", "model": body.get("model") or MODELS[0],
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in MODELS]})
        elif path == "/api/tags":
            self._send(200, {"models": [{"name": m, "model": m} for m in MODELS]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._send(400, {"error": "body is not JSON"})
            return
        if path == "/v1/chat/completions":
            self._send(200, answer(body))
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *args) -> None:  # quiet
        pass


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8794
    print(f"mock LLM server on http://127.0.0.1:{port} (synthetic answers)", flush=True)
    serve(port).serve_forever()
