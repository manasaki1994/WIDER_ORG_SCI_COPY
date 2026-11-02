"""Provider interface: one isolated chat call in, one validated structured answer out."""
from __future__ import annotations

import asyncio
import os
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ..core.models import ProviderConfig
from ..core.provenance import sha256_text
from .structured import extract_json, validate_json


class ChatRequest(BaseModel):
    system: str
    user: str
    schema_: dict | None = Field(default=None, alias="schema")
    temperature: float | None = None
    seed: int | None = None
    max_tokens: int = 2048
    context_tokens: int | None = None   # the prompt's budget (Ask WIDER, guide-assistant D10); an Ollama window fits it
    model_config = {"populate_by_name": True}

    @property
    def input_sha256(self) -> str:
        return sha256_text(self.system + "\n\x1e\n" + self.user)


class ChatResponse(BaseModel):
    ok: bool
    text: str = ""
    parsed: Any = None
    usage: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)
    error: str | None = None
    attempts: int = 1
    input_sha256: str = ""
    elapsed_s: float = 0.0
    repaired: bool = False     # a repair round ran; input_sha256 is still the original request's
    repairs: int = 0           # how many repair rounds ran (at most ``ProviderConfig.repairs``, default one)


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def unreachable(base: str, e: Exception) -> ProviderError:
    """A timeout or transport error (httpx) as a retryable ProviderError naming the endpoint."""
    detail = str(e) or type(e).__name__
    return ProviderError(f"cannot reach {base}: {detail}", retryable=True)


def check_status(r) -> None:
    """Shared HTTP status rules: a redirect is never followed (keys and sensitive headers stay with the configured
    host), 429/5xx are retryable, other 4xx are not."""
    if 300 <= r.status_code < 400:
        from urllib.parse import urlsplit
        loc = r.headers.get("location", "")
        host = urlsplit(loc).hostname or loc or "another address"
        raise ProviderError(f"the endpoint redirected to {host}; set the base URL to the final address",
                            status=r.status_code)
    if r.status_code in (429, 529) or r.status_code >= 500:
        raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}", retryable=True, status=r.status_code)
    if r.status_code >= 400:
        raise ProviderError(f"HTTP {r.status_code}: {r.text[:300]}", status=r.status_code)


_PLACEHOLDERS_ONLY = re.compile(r"^(\{[A-Za-z_][A-Za-z0-9_]*(:[A-Za-z_][A-Za-z0-9_]*)?\}\s*)+$")
NOT_RECORDED = "(value not recorded)"


def identity_extra(extra: Any, home: str, path: tuple = ()) -> Any:
    """``extra`` for the run snapshot: strings with the home folder as ``~``; an ``extra.env`` value is kept only when
    it is placeholders alone (``{system_file}``, ``{env:NAME}``), otherwise only its name is recorded."""
    from ..core.detect import tilde

    if isinstance(extra, dict):
        out = {}
        for k, v in extra.items():
            if path == ("env",) and not (isinstance(v, str) and _PLACEHOLDERS_ONLY.match(v.strip() or "-")):
                out[k] = NOT_RECORDED
            else:
                out[k] = identity_extra(v, home, (*path, k))
        return out
    if isinstance(extra, list):
        return [identity_extra(v, home, path) for v in extra]
    if isinstance(extra, str):
        return tilde(extra, home) if extra.startswith(home) else extra.replace(home + "/", "~/")
    return extra


class Provider(ABC):
    """Subclasses implement ``_call`` (one raw call). ``complete`` adds retries, extraction, validation, repair."""

    kind = "base"
    generates_text = True      # False for a scoring-only backend (Jev): text stages refuse it

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.sem = asyncio.Semaphore(max(1, cfg.max_concurrency))
        # effective (locality, source) set by make_provider (Ollama routing); None: the endpoint rule decides
        self.locality: str | None = None
        self.locality_source: str | None = None

    # ----- identity -----
    def identity(self) -> dict:
        """The effective backend snapshot of a run (design Decision 8): every setting (``api_key_env`` as a name),
        ``extra`` as written (sensitive headers are ``{env:NAME}``; ``extra.env`` values only when placeholders),
        the home folder as ``~``, locality, its source, destination, and the preset's experimental marking."""
        from ..core.detect import _preset, destination, tilde
        from ..core.summary import provider_locality

        home = os.path.expanduser("~")
        d = self.cfg.model_dump(exclude={"extra", "command"})
        d["command"] = [tilde(a, home) for a in self.cfg.command] if self.cfg.command else self.cfg.command
        d["extra"] = identity_extra(self.cfg.extra, home)
        loc = self.locality or provider_locality(self.cfg.kind, self.cfg.base_url)
        d["locality"] = loc
        d["locality_source"] = self.locality_source or "endpoint"
        d["destination"] = destination(self.cfg, loc)
        pre = _preset(self.cfg)
        d["experimental"] = bool(pre and pre.experimental)
        d["limitations"] = pre.all_limitations() if pre else []
        return d

    def api_key(self) -> str | None:
        if not self.cfg.api_key_env:
            return None
        return os.environ.get(self.cfg.api_key_env) or None

    def supports_native_schema(self) -> bool:
        return False

    # ----- one raw call -----
    @abstractmethod
    async def _call(self, req: ChatRequest) -> tuple[str, dict, dict]:
        """Return (text, usage, meta). Raise ProviderError on failure."""

    # ----- the loop every stage uses -----
    async def complete(self, req: ChatRequest, retries: int = 3, repair: bool = True) -> ChatResponse:
        """One request with retries and one repair round; every outcome is logged for tokens and costs when the
        calling engine opened a usage scope (wider.core.usage.track)."""
        res = await self._complete(req, retries, repair)
        from ..core.usage import record_call
        record_call(self.cfg, res.usage, res.ok, locality=self.locality)
        return res

    async def _complete(self, req: ChatRequest, retries: int, repair: bool) -> ChatResponse:
        t0 = time.monotonic()
        attempts = 0
        last_err: str | None = None
        text, usage, meta = "", {}, {}
        sha = req.input_sha256          # the record's input; a repair note never changes it
        repairs = 0
        max_repairs = self.cfg.repairs or 1

        def done(ok: bool, **kw) -> ChatResponse:
            return ChatResponse(ok=ok, attempts=attempts, input_sha256=sha, elapsed_s=time.monotonic() - t0,
                                repaired=repairs > 0, repairs=repairs, **kw)

        async with self.sem:
            for i in range(retries):
                attempts += 1
                try:
                    text, usage, meta = await self._call(req)
                except ProviderError as e:
                    last_err = str(e)
                    if not e.retryable or i == retries - 1:
                        return done(False, error=last_err)
                    await asyncio.sleep(min(30, 2 ** i + random.random()))
                    continue
                except (TimeoutError, OSError) as e:
                    last_err = f"{type(e).__name__}: {e}"
                    if i == retries - 1:
                        return done(False, error=last_err)
                    await asyncio.sleep(min(30, 2 ** i + random.random()))
                    continue
                if req.schema_ is None:
                    return done(True, text=text, parsed=None, usage=usage, meta=meta)
                parsed, err = extract_json(text)
                if parsed is not None:
                    verr = validate_json(parsed, req.schema_)
                    if verr is None:
                        return done(True, text=text, parsed=parsed, usage=usage, meta=meta)
                    err = verr
                last_err = f"invalid structured output: {err}"
                if not repair or repairs >= max_repairs:
                    break
                repairs += 1
                req = req.model_copy(update={"user": req.user + "\n\n" + _repair_note(err, text)})
        return done(False, text=text, usage=usage, meta=meta, error=last_err)

    def complete_sync(self, req: ChatRequest, **kw) -> ChatResponse:
        return asyncio.run(self.complete(req, **kw))


def _repair_note(err: str, previous: str) -> str:
    prev = previous.strip()
    if len(prev) > 1500:
        prev = prev[:1500] + " ..."
    return ("Your previous reply did not match the required JSON schema "
            f"({err}). Previous reply:\n{prev}\n\nReturn only one valid JSON object with exactly the required fields.")


def schema_instruction(schema: dict | None) -> str:
    """Appended to the system prompt for backends without native structured output."""
    if not schema:
        return ""
    import json
    return ("\n\nReturn only one JSON object, no prose and no code fence, matching this JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))
