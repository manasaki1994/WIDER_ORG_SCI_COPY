"""Ollama native chat API (``/api/chat``) with JSON-schema ``format`` for structured output."""
from __future__ import annotations

import httpx

from ..core.usage import normalised
from .base import ChatRequest, Provider, ProviderError, check_status, schema_instruction


class OllamaProvider(Provider):
    kind = "ollama"

    def supports_native_schema(self) -> bool:
        return True

    async def _call(self, req: ChatRequest):
        base = (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        temp = self.cfg.temperature if req.temperature is None else req.temperature
        seed = self.cfg.seed if req.seed is None else req.seed
        options = {"temperature": temp, "num_predict": req.max_tokens}
        if seed is not None:
            options["seed"] = seed
        if req.context_tokens:   # Ollama's default window would cut a longer prompt silently; the backend's own wins
            options["num_ctx"] = req.context_tokens + req.max_tokens
        options.update(self.cfg.extra.get("options", {}))
        system = req.system
        body: dict = {"model": self.cfg.model, "stream": False, "options": options,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": req.user}]}
        if "think" in self.cfg.extra:
            body["think"] = bool(self.cfg.extra["think"])
        if req.schema_ is not None:
            # "json" (default) = free JSON, validated client-side; "schema" = grammar-constrained decoding,
            # exact but very slow for large schemas on small local models
            body["format"] = req.schema_ if self.cfg.extra.get("json_mode") == "schema" else "json"
            body["messages"][0]["content"] = system + schema_instruction(req.schema_)
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_s, follow_redirects=False) as client:
                r = await client.post(f"{base}/api/chat", json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"cannot reach Ollama at {base}: {str(e) or type(e).__name__}", retryable=True) from None
        check_status(r)
        data = r.json()
        text = (data.get("message") or {}).get("content") or ""
        usage = normalised(input_uncached=data.get("prompt_eval_count"), output=data.get("eval_count"),
                           raw={k: data.get(k) for k in ("prompt_eval_count", "eval_count", "total_duration")})
        return text, usage, {"model": data.get("model"), "done_reason": data.get("done_reason")}

    async def list_models(self) -> list[str]:
        base = (self.cfg.base_url or "http://127.0.0.1:11434").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                r = await client.get(f"{base}/api/tags")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ProviderError(f"cannot reach Ollama at {base}: {str(e) or type(e).__name__}", retryable=True) from None
        check_status(r)
        return [m["name"] for m in r.json().get("models", [])]
