"""Optional LiteLLM adapter (``pip install wider[litellm]``): 100+ providers through one call."""
from __future__ import annotations

from .base import ChatRequest, Provider, ProviderError, schema_instruction


class LiteLLMProvider(Provider):
    kind = "litellm"

    async def _call(self, req: ChatRequest):
        try:
            import litellm
        except ImportError:
            raise ProviderError("litellm is not installed; pip install 'wider[litellm]'")
        temp = self.cfg.temperature if req.temperature is None else req.temperature
        system = req.system + (schema_instruction(req.schema_) if req.schema_ is not None else "")
        kw = dict(model=self.cfg.model, temperature=temp, max_tokens=req.max_tokens,
                  messages=[{"role": "system", "content": system}, {"role": "user", "content": req.user}])
        if self.cfg.base_url:
            kw["api_base"] = self.cfg.base_url
        if req.schema_ is not None:
            kw["response_format"] = {"type": "json_object"}
        kw.update(self.cfg.extra.get("body", {}))
        try:
            resp = await litellm.acompletion(**kw)
        except Exception as e:  # litellm raises many types; treat rate limits/5xx as retryable
            retry = any(s in str(e).lower() for s in ("rate", "429", "500", "502", "503", "timeout"))
            raise ProviderError(str(e)[:300], retryable=retry)
        text = resp.choices[0].message.content or ""
        usage = dict(resp.usage) if getattr(resp, "usage", None) else {}
        return text, usage, {"model": getattr(resp, "model", None)}
