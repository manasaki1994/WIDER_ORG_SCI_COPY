"""OpenAI-compatible chat completions: OpenAI, Azure OpenAI, Gemini's compat endpoint, OpenRouter, Mistral, Groq,
Together, vLLM, LM Studio, llama.cpp.

``extra`` options (design Decision 3): ``auth`` (``bearer`` default, ``api-key`` for Azure, ``none``), ``api_version``
(``?api-version=``), ``deployment`` (Azure legacy path), ``omit_params`` (body keys not to send, e.g. ``temperature``
for reasoning models; ``send_seed: false`` still works), ``max_tokens_param`` (e.g. ``max_completion_tokens``),
``headers`` (sensitive ones only as ``{env:NAME}``), ``json_mode``, ``strict``, ``body``.
"""
from __future__ import annotations

import httpx

from ..core.usage import normalised
from .base import ChatRequest, Provider, ProviderError, check_status, schema_instruction, unreachable
from .secrets import AUTH_HEADERS, env_ref, is_sensitive_header, resolve_env_refs

DEFAULT_BASE = "https://api.openai.com/v1"


class OpenAICompatProvider(Provider):
    kind = "openai_compat"

    def supports_native_schema(self) -> bool:
        return self.cfg.extra.get("json_mode", "schema") in ("schema", "object")

    def base(self) -> str:
        return (self.cfg.base_url or DEFAULT_BASE).rstrip("/")

    def url(self, path: str) -> tuple[str, dict]:
        """The request URL and query parameters for ``path`` (``chat/completions`` or ``models``)."""
        dep = self.cfg.extra.get("deployment")
        url = f"{self.base()}/deployments/{dep}/{path}" if dep and path == "chat/completions" else f"{self.base()}/{path}"
        params = {"api-version": str(self.cfg.extra["api_version"])} if self.cfg.extra.get("api_version") else {}
        return url, params

    def headers(self) -> dict[str, str]:
        """Auth and extra headers. Raises a non-retryable ProviderError (nothing is sent) when a needed key or
        ``{env:NAME}`` variable is missing, or a sensitive header holds a plain value."""
        from ..core.summary import provider_locality

        extra = self.cfg.extra
        auth = extra.get("auth", "bearer")
        h = {"Content-Type": "application/json"}
        extra_headers = extra.get("headers") or {}
        for name, value in extra_headers.items():
            if is_sensitive_header(str(name), extra) and not env_ref(value):
                raise ProviderError(f"header {name} is sensitive: store its value in an environment variable and "
                                    "write {env:NAME}")
            if str(name).lower() in AUTH_HEADERS and auth != "none":
                raise ProviderError(f"header {name} is set from api_key_env; set extra.auth to none to send it yourself")
        try:
            h.update(resolve_env_refs(extra_headers, "header"))
        except KeyError as e:
            raise ProviderError(e.args[0]) from None
        if auth == "none":
            return h
        key = self.api_key()
        if key:
            if auth == "api-key":
                h["api-key"] = key
            else:
                h["Authorization"] = f"Bearer {key}"
        elif self.cfg.api_key_env and provider_locality(self.cfg.kind, self.base()) != "local":
            raise ProviderError(f"environment variable {self.cfg.api_key_env} is not set")
        return h

    def body(self, req: ChatRequest) -> dict:
        extra = self.cfg.extra
        mode = extra.get("json_mode", "schema")
        system = req.system
        body: dict = {"model": self.cfg.model, extra.get("max_tokens_param") or "max_tokens": req.max_tokens}
        body["temperature"] = self.cfg.temperature if req.temperature is None else req.temperature
        seed = self.cfg.seed if req.seed is None else req.seed
        if seed is not None and extra.get("send_seed", True):
            body["seed"] = seed
        if req.schema_ is not None:
            if mode == "schema":
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "answer", "schema": req.schema_, "strict": bool(extra.get("strict", False))}}
            elif mode == "object":
                body["response_format"] = {"type": "json_object"}
                system += schema_instruction(req.schema_)
            else:
                system += schema_instruction(req.schema_)
        body["messages"] = [{"role": "system", "content": system}, {"role": "user", "content": req.user}]
        for k in extra.get("omit_params") or []:
            body.pop(k, None)
        body.update(extra.get("body", {}))
        return body

    async def _call(self, req: ChatRequest):
        headers = self.headers()
        url, params = self.url("chat/completions")
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_s, follow_redirects=False) as client:
                r = await client.post(url, params=params, json=self.body(req), headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise unreachable(self.base(), e) from None
        check_status(r)
        data = r.json()
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
            if not text and msg.get("refusal"):
                raise ProviderError(f"refusal: {msg['refusal'][:200]}")
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected response shape: {e}") from None
        return text, usage_openai(data.get("usage")), {"model": data.get("model"), "id": data.get("id"),
                                                      "finish_reason": data["choices"][0].get("finish_reason")}

    async def list_models(self) -> list[str]:
        url, params = self.url("models")
        headers = self.headers()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                r = await client.get(url, params=params, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise unreachable(self.base(), e) from None
        check_status(r)
        data = r.json()
        rows = data.get("data") if isinstance(data, dict) else data
        return sorted(str(m.get("id")) for m in rows or [] if isinstance(m, dict) and m.get("id"))


def usage_openai(u: dict | None) -> dict:
    """OpenAI-shaped usage: ``prompt_tokens`` counts cached tokens, ``prompt_tokens_details.cached_tokens`` names them."""
    u = u or {}
    prompt = u.get("prompt_tokens")
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens")
    unc = None if prompt is None else int(prompt) - int(cached or 0)
    return normalised(input_uncached=unc, input_cached=cached, output=u.get("completion_tokens"), raw=u)
