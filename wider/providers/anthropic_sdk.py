"""Anthropic Messages API over httpx (no SDK needed). Structured output via a forced tool call."""
from __future__ import annotations

import json

import httpx

from ..core.usage import normalised
from .base import ChatRequest, Provider, ProviderError, check_status, unreachable

API_BASE = "https://api.anthropic.com"
VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    kind = "anthropic"

    def supports_native_schema(self) -> bool:
        return True

    async def _call(self, req: ChatRequest):
        headers = self.headers()
        temp = self.cfg.temperature if req.temperature is None else req.temperature
        body: dict = {"model": self.cfg.model, "max_tokens": req.max_tokens, "temperature": temp,
                      "system": req.system, "messages": [{"role": "user", "content": req.user}]}
        if req.schema_ is not None:
            body["tools"] = [{"name": "answer", "description": "Return the structured answer.",
                              "input_schema": req.schema_}]
            body["tool_choice"] = {"type": "tool", "name": "answer"}
        body.update(self.cfg.extra.get("body", {}))
        url = self.base() + "/v1/messages"
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_s, follow_redirects=False) as client:
                r = await client.post(url, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise unreachable(self.base(), e) from None
        check_status(r)
        data = r.json()
        text = ""
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and req.schema_ is not None:
                text = json.dumps(block.get("input", {}), ensure_ascii=False)
                break
            if block.get("type") == "text":
                text += block.get("text", "")
        return text, usage_anthropic(data.get("usage")), {"model": data.get("model"), "id": data.get("id"),
                                                         "stop_reason": data.get("stop_reason")}

    def base(self) -> str:
        return (self.cfg.base_url or API_BASE).rstrip("/")

    def headers(self) -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise ProviderError(f"environment variable {self.cfg.api_key_env or 'ANTHROPIC_API_KEY'} is not set")
        return {"x-api-key": key, "anthropic-version": VERSION, "content-type": "application/json"}

    async def list_models(self) -> list[str]:
        headers = self.headers()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                r = await client.get(self.base() + "/v1/models", headers=headers, params={"limit": 1000})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise unreachable(self.base(), e) from None
        check_status(r)
        return [str(m["id"]) for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")]


def usage_anthropic(u: dict | None) -> dict:
    """Anthropic ``input_tokens`` leaves cache reads and writes out; they are reported apart."""
    u = u or {}
    return normalised(input_uncached=u.get("input_tokens"), input_cached=u.get("cache_read_input_tokens"),
                      input_cache_write=u.get("cache_creation_input_tokens"), output=u.get("output_tokens"), raw=u)
