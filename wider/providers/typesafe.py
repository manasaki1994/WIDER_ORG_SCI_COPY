"""Jev (TypeSafe AI, kind ``typesafe``): a scoring model that answers questions about a text but writes none (design
Decision 13). Screening only.

The schema's booleans become ``noul`` questions (answer: probability above 0.5), string enums ``choice`` questions and
small integer ranges (2 to 10 levels) ``score`` questions. Every other property (free text such as ``summary`` or a
quote) is not asked: it is filled with ``""`` and listed in ``meta.unanswered``. One request per record carries all
questions: ``POST {base}/v1/systemone`` with ``{model, state, questions}``, Bearer key from ``api_key_env``.

The answer field names are UNVERIFIED (open question 8): the reader takes ``answers`` (or ``results``) keyed by
question id, each an object with ``answer`` and optional ``probability``, ``confidence`` and ``probabilities``.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

from ..core.usage import normalised
from .base import ChatRequest, Provider, ProviderError, check_status, unreachable

API_BASE = "https://api.typesafe.ai"
PATH = "/v1/systemone"
NOT_ANSWERABLE = "Jev cannot answer this schema"
NO_TEXT = "Jev cannot write text; choose a generative backend"


def questions_from_schema(schema: dict | None, system: str = "") -> tuple[dict, list[str]]:
    """``(questions, unanswerable)``: the Jev questions keyed by property name, and the properties not asked."""
    questions: dict[str, dict] = {}
    unanswerable: list[str] = []
    for name, prop in ((schema or {}).get("properties") or {}).items():
        prop = prop if isinstance(prop, dict) else {}
        t = prop.get("type")
        instructions = (system + "\n\n" if system else "") + f"Answer only: {name}"
        if t == "boolean":
            questions[name] = {"type": "noul", "instructions": instructions}
        elif t == "string" and isinstance(prop.get("enum"), list) and prop["enum"]:
            values = [str(v) for v in prop["enum"]]
            questions[name] = {"type": "choice", "instructions": instructions, "criteria": {v: v for v in values}}
        elif t == "integer" and _levels(prop):
            lo, hi = _levels(prop)
            questions[name] = {"type": "score", "instructions": instructions,
                               "criteria": {str(v): str(v) for v in range(lo, hi + 1)}}
        else:
            unanswerable.append(name)
    return questions, unanswerable


def _levels(prop: dict) -> tuple[int, int] | None:
    lo, hi = prop.get("minimum"), prop.get("maximum")
    if isinstance(lo, int) and isinstance(hi, int) and 2 <= hi - lo + 1 <= 10:
        return lo, hi
    return None


class TypesafeProvider(Provider):
    kind = "typesafe"
    generates_text = False

    def base(self) -> str:
        return (self.cfg.base_url or API_BASE).rstrip("/")

    def headers(self) -> dict[str, str]:
        key = self.api_key()
        if not key:
            raise ProviderError(f"environment variable {self.cfg.api_key_env or 'TYPESAFE_API_KEY'} is not set")
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def _call(self, req: ChatRequest):
        questions, unanswered = questions_from_schema(req.schema_, req.system)
        if not questions:
            raise ProviderError(NOT_ANSWERABLE)
        headers = self.headers()
        body = {"model": self.cfg.model, "state": req.user, "questions": questions}
        body.update(self.cfg.extra.get("body", {}))
        try:
            async with httpx.AsyncClient(timeout=self.cfg.timeout_s, follow_redirects=False) as client:
                r = await client.post(self.base() + PATH, json=body, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise unreachable(self.base(), e) from None
        check_status(r)
        data = r.json()
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            answers = data.get("results") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            raise ProviderError(f"unexpected response shape: no answers in {str(data)[:200]}")
        obj: dict[str, Any] = {}
        probs: dict[str, Any] = {}
        for name, q in questions.items():
            if name not in answers:
                raise ProviderError(f"unexpected response shape: no answer for {name}")
            value, p = _read(answers[name], q)
            obj[name] = value
            probs[name] = p
        for name in unanswered:
            obj[name] = ""
        u = data.get("usage") or {}
        usage = normalised(input_uncached=u.get("input_tokens"), output=u.get("output_tokens"), raw=u)
        meta = {"model": data.get("model"), "unanswered": unanswered, "probabilities": probs}
        return json.dumps(obj, ensure_ascii=False), usage, meta


def _read(entry: Any, q: dict) -> tuple[Any, Any]:
    """One answer as ``(value, probability or probabilities)``."""
    e = entry if isinstance(entry, dict) else {"answer": entry}
    dist = e.get("probabilities") if isinstance(e.get("probabilities"), dict) else None
    if q["type"] == "noul":
        p = e.get("probability")
        if p is None and dist:
            p = next((dist[k] for k in ("yes", "true", "True", "1") if k in dist), None)
        if p is None and isinstance(e.get("answer"), (int, float)) and not isinstance(e.get("answer"), bool):
            p = e["answer"]
        if p is None:
            p = 1.0 if e.get("answer") in (True, "yes", "true") else 0.0
        return float(p) > 0.5, float(p)
    options = list(q["criteria"])
    value = e.get("answer")
    if str(value) not in options and dist:
        value = max(dist, key=lambda k: dist[k])
    if str(value) not in options:
        raise ProviderError(f"unexpected answer {value!r}; expected one of {', '.join(options)}")
    value = int(value) if q["type"] == "score" else str(value)
    return value, dist if dist is not None else e.get("confidence")
