"""Consent before contacting a network, cloud or unknown backend (design Decision 17, recorded decisions 14, 20, 22).

Built like :mod:`wider.core.lookups` but stored apart (``project.yaml`` ``backend_consent`` for LLM backends,
``embedding_consent`` for embedding backends), so one consent never stands in for another. Consent is kept per backend
name and operation:

- LLM backends: ``models`` (the model list: the key header or the CLI's sign-in, no project data), ``probe`` (the fixed
  synthetic question), ``research`` (protocol and codebook text, and one record's title and abstract per call),
  ``passages`` (passages of seed PDFs for name mining, never the file; asked once per backend, vision change 2b) and
  ``assistant`` (Ask WIDER: the question, WIDER's documents, named settings and status words, no record);
- embedding backends: ``probe`` (the fixed synthetic sentences), ``research`` (record titles and abstracts, anchor,
  construct and sampling texts) and ``assistant`` (the question and WIDER's own documents, no project data).

:func:`allow_once` is the one-question allowance of Ask WIDER (guide-assistant D8): it stores nothing and holds only
while the backend's fingerprint is the one the researcher was shown.

Each consent carries a fingerprint of what it was given for (kind, endpoint, where it runs, key variable name, auth,
header names, text version; for embeddings also the model and the fields sent). A changed fingerprint counts as no
consent, and the refusal names what changed. Local and test backends need none; neither does a loopback Ollama that
does not answer (nothing can leave the computer; the call fails with "cannot reach").
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

from .journal import current_author
from .models import BackendConsent
from .project import Project
from .provenance import utcnow

CONSENT_TEXT_VERSION = 1
OPERATIONS = ("models", "probe", "research", "passages", "assistant")
EMBED_OPERATIONS = ("probe", "research", "assistant")
NEEDS_CONSENT = ("cloud", "network", "unknown")
EMBED_FIELDS = ["title", "abstract"]

SENT = {
    "models": "the request for its model list, with the API key header (or the CLI's sign-in); no project data",
    "probe": "the fixed test question (17 + 25) with the API key header; no project data",
    "research": "the protocol and codebook text and, per call, one record's title and abstract",
    "passages": "passages of your seed PDFs, never the file: one extracted passage of at most the configured size "
                "per call, with the researcher's interview decisions, to find names for the query",
    "assistant": "your question, this page's guide, matching sections of WIDER's documents, your project's settings "
                 "for those topics, the project's status (no record) and your earlier questions",
}
EMBED_SENT = {
    "probe": "three fixed test sentences; no project data",
    "research": "record titles and abstracts, and the anchor, construct and sampling texts that are ranked",
    "assistant": "your question, and WIDER's own documents to build the index, again after they change; no project data",
}
OPERATION_LABELS = {"models": "list its models", "probe": "test it", "research": "send research data",
                    "passages": "send passages of seed PDFs", "assistant": "answer questions in Ask WIDER"}
PART_LABELS = {"kind": "the backend kind", "endpoint": "the endpoint", "locality": "where it runs",
               "api_key_env": "the key variable", "auth": "the auth method", "headers": "the header names",
               "text_version": "what WIDER sends", "model": "the model", "fields": "the fields sent"}
_DEFAULT_BASE = {"openai_compat": "https://api.openai.com/v1", "openai": "https://api.openai.com/v1",
                 "anthropic": "https://api.anthropic.com", "ollama": "http://127.0.0.1:11434",
                 "typesafe": "https://api.typesafe.ai"}


def _ops(embedding: bool) -> tuple[str, ...]:
    return EMBED_OPERATIONS if embedding else OPERATIONS


def _check_op(operation: str, embedding: bool) -> None:
    if operation not in _ops(embedding):
        raise ValueError(f"unknown operation {operation!r}; expected one of {', '.join(_ops(embedding))}")


def _url(cfg) -> str | None:
    return getattr(cfg, "base_url", None) or _DEFAULT_BASE.get(cfg.kind)


def _endpoint(cfg, loc: str) -> str:
    """The endpoint in the fingerprint: scheme, host and port of the URL; vendor and tool for a CLI backend."""
    from .detect import destination, ollama_cloud_model

    if cfg.kind == "cli_agent":
        cmd = list(getattr(cfg, "command", None) or [])
        tool = cmd[0].rsplit("/", 1)[-1] if cmd else "?"
        return f"{destination(cfg, loc)} [{tool}]"
    url = _url(cfg)
    if not url:
        return f"unknown ({cfg.kind})"
    u = urlsplit(url)
    host = u.hostname or "unknown"
    ep = f"{u.scheme or '?'}://{host}" + (f":{u.port}" if u.port else "")
    if cfg.kind == "ollama" and ollama_cloud_model(cfg, loc):
        ep += " -> ollama.com"
    return ep


def assess(cfg, embedding: bool = False) -> dict[str, Any]:
    """Where ``cfg`` sends its calls and whether contacting it needs consent: ``{locality, source, destination,
    needs, unreachable, parts, fingerprint, warning, host}``. Reads a loopback Ollama's tag list (uncached); nothing
    else is contacted."""
    from .detect import destination, effective_locality, ollama_answers, tags_base
    from .summary import embedding_locality, host_class

    if cfg.kind == "ollama":
        loc, src, dest = effective_locality(cfg, fresh=True)
    elif embedding:
        loc, src = embedding_locality(cfg.kind, getattr(cfg, "base_url", None)), "endpoint"
        dest = destination(cfg, loc)
    else:
        loc, src, dest = effective_locality(cfg)
    unreachable = False
    # "nothing answers, so nothing can leave" holds only for the address the tag list was read from (127.0.0.1, or
    # localhost read there); another loopback name was never asked, so its unknown routing needs consent
    if (cfg.kind == "ollama" and src == "not_established" and host_class(_url(cfg)) == "loopback"
            and tags_base(_url(cfg)) and not ollama_answers(cfg)):
        unreachable = True
    return _info(cfg, embedding, loc, src, dest, unreachable)


def assess_stored(project: Project, cfg, embedding: bool = False) -> dict[str, Any]:
    """:func:`assess` without any request: the locality of the backend row (``backends.backend_place``), so an Ollama
    LLM backend never checked is ``unknown`` with source ``not_checked``. For the pages that only show consent."""
    from .backends import backend_place

    place = backend_place(project, cfg, embedding)
    return _info(cfg, embedding, place["locality"], place["locality_source"], place["destination"], False)


def _info(cfg, embedding: bool, loc: str, src: str, dest: str, unreachable: bool) -> dict[str, Any]:
    from .summary import LOCALITY_LABELS

    needs = loc in NEEDS_CONSENT and not unreachable
    extra = getattr(cfg, "extra", None) or {}
    parts: dict[str, Any] = {
        "kind": cfg.kind, "endpoint": _endpoint(cfg, loc), "locality": loc,
        "api_key_env": getattr(cfg, "api_key_env", None) or "",
        "auth": str(extra.get("auth") or ""),
        "headers": sorted(str(h).lower() for h in (extra.get("headers") or {})),
        "text_version": CONSENT_TEXT_VERSION,
    }
    if embedding:
        parts["model"] = cfg.model or ""
        parts["fields"] = list(EMBED_FIELDS)
    fp = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
    url = _url(cfg) if cfg.kind != "cli_agent" else None
    host = urlsplit(url).hostname if url else None
    warning = (f"research data would travel unencrypted (http) to {host}"
               if needs and url and urlsplit(url).scheme == "http" else None)
    if loc == "network":
        dest = f"{LOCALITY_LABELS['network']}: {dest}"
    return {"locality": loc, "source": src, "destination": dest, "needs": needs, "unreachable": unreachable,
            "parts": parts, "fingerprint": fp, "warning": warning, "host": host}


def _store(project: Project, embedding: bool) -> dict[str, BackendConsent]:
    return project.config.embedding_consent if embedding else project.config.backend_consent


def _changed(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out = []
    for k, v in new.items():
        if k in old and old[k] != v:
            a, b = old[k], v
            if isinstance(a, list) or isinstance(b, list):
                a, b = ", ".join(map(str, a or [])) or "none", ", ".join(map(str, b or [])) or "none"
            out.append(f"{PART_LABELS.get(k, k)} changed from {a or 'none'} to {b or 'none'}")
    return out


class BackendConsentRequired(PermissionError):
    """The backend has no consent for this operation (or its consent is for an earlier destination); nothing was
    sent."""

    def __init__(self, name: str, operation: str, info: dict[str, Any], embedding: bool = False,
                 changed: list[str] | None = None):
        self.name, self.operation, self.embedding = name, operation, embedding
        self.destination, self.locality, self.fingerprint = info["destination"], info["locality"], info["fingerprint"]
        self.sent = (EMBED_SENT if embedding else SENT)[operation]
        self.warning, self.changed = info.get("warning"), list(changed or [])
        what = "embedding backend" if embedding else "backend"
        group = "embed" if embedding else "providers"
        msg = (f"The {what} {name} needs your consent to {OPERATION_LABELS[operation]}, so nothing was sent. "
               f"Destination: {self.destination}. WIDER would send {self.sent}.")
        if self.changed:
            msg += f" Consent was given before {'; '.join(self.changed)}."
        if self.warning:
            msg += f" Warning: {self.warning}."
        msg += f" Allow it with `wider {group} allow {name} --for {operation}` or in the dialog."
        super().__init__(msg)

    def payload(self) -> dict[str, Any]:
        return {"message": str(self), "backend_consent_required": {
            "backend": self.name, "kind": "embedding" if self.embedding else "llm", "operation": self.operation,
            "label": OPERATION_LABELS[self.operation],
            "destination": self.destination, "locality": self.locality, "sent": self.sent,
            "warning": self.warning, "changed": self.changed, "fingerprint": self.fingerprint}}


def _valid(rec: BackendConsent | None, fp: str) -> list[str]:
    return list(rec.operations) if rec is not None and rec.fingerprint == fp else []


# the allowances of the current question: (kind, backend, operation, fingerprint); a context variable, so a job
# thread or request sees only its own
_ONCE: ContextVar[frozenset[tuple[str, str, str, str]]] = ContextVar("wider_consent_once", default=frozenset())


@contextmanager
def allow_once(cfg, operation: str, fingerprint: str | None = None, embedding: bool = False) -> Iterator[None]:
    """Inside the block, ``cfg`` may be contacted for ``operation`` without stored consent while :func:`assess` still
    gives ``fingerprint`` (the one the dialog showed; ``None``: the current one, for `--allow-once`). Nothing is
    stored, so the next question asks again; a backend whose address or place changed meanwhile is refused."""
    _check_op(operation, embedding)
    fp = fingerprint or assess(cfg, embedding)["fingerprint"]
    token = _ONCE.set(_ONCE.get() | {("embedding" if embedding else "llm", cfg.name, operation, fp)})
    try:
        yield
    finally:
        _ONCE.reset(token)


def parse_once(header: str | None) -> list[tuple[str, str, str]]:
    """The allowances of an ``X-Wider-Consent-Once`` header, ``<kind>:<backend>:<operation>:<fingerprint>`` separated
    by commas, as ``(kind, backend, fingerprint)`` for the operation ``assistant`` only; anything else is ignored."""
    out = []
    for item in str(header or "").split(","):
        kind, _, rest = item.strip().partition(":")
        name, _, tail = rest.rpartition(":")
        name, _, op = name.rpartition(":")
        if kind in ("llm", "embedding") and name and op == "assistant" and tail:
            out.append((kind, name, tail))
    return out


def require_backend_consent(project: Project, cfg, operation: str, embedding: bool = False) -> dict[str, Any]:
    """Return the assessment when ``cfg`` may be contacted for ``operation``; raise :class:`BackendConsentRequired`
    otherwise, before anything is sent."""
    _check_op(operation, embedding)
    info = assess(cfg, embedding)
    if not info["needs"]:
        return info
    if ("embedding" if embedding else "llm", cfg.name, operation, info["fingerprint"]) in _ONCE.get():
        return info
    rec = _store(project, embedding).get(cfg.name)
    if operation in _valid(rec, info["fingerprint"]):
        return info
    changed = _changed(rec.parts, info["parts"]) if rec is not None and rec.fingerprint != info["fingerprint"] else []
    raise BackendConsentRequired(cfg.name, operation, info, embedding, changed)


def require_research(project: Project, provider_name: str | None, stage: str) -> None:
    """The early check of a run's route, before a job is created: the named LLM backend, else the stage's. An
    unknown backend is left to the run to report."""
    try:
        cfg = project.provider(provider_name) if provider_name else project.provider_for(stage)
    except (KeyError, TypeError):
        return
    require_backend_consent(project, cfg, "research")


def require_embedding_research(project: Project, name: str | None) -> None:
    """The same for an embedding backend (``name`` None: the project default)."""
    try:
        cfg = project.embedding(name)
    except (KeyError, TypeError):
        return
    require_backend_consent(project, cfg, "research", embedding=True)


def ranking_embedding(project: Project, ranking: str | None) -> str | None:
    """The embedding set a ranking was scored with (None without a ranking)."""
    from ..rank.versions import resolve

    st = project.store
    row = st.one("SELECT set_name FROM rankings WHERE name=?", (resolve(project, ranking),))
    return row["set_name"] if row else None


def ablate_embeddings(project: Project) -> list[str]:
    """The embedding sets an ablation ranks with (each embeds the anchors)."""
    return [r["set_name"] for r in project.store.query("SELECT set_name FROM embedding_sets")]


def _cfg(project: Project, name: str, embedding: bool):
    return project.embedding(name) if embedding else project.provider(name)


def allow_backend(project: Project, name: str, operations: list[str] | tuple[str, ...], embedding: bool = False,
                  author: str | None = None) -> dict[str, Any]:
    """Store consent for ``operations`` under the backend's current fingerprint (journalled through ``save``). A
    consent for an earlier fingerprint is replaced, not extended."""
    ops = list(dict.fromkeys(operations))
    if not ops:
        raise ValueError(f"name an operation: {', '.join(_ops(embedding))}")
    for op in ops:
        _check_op(op, embedding)
    cfg = _cfg(project, name, embedding)
    info = assess(cfg, embedding)
    if not info["needs"]:
        raise ValueError(f"{name} sends nothing off this computer ({info['destination']}); it needs no consent")
    store = _store(project, embedding)
    rec = store.get(name)
    keep = _valid(rec, info["fingerprint"])
    merged = keep + [op for op in ops if op not in keep]
    if rec is not None and keep and merged == keep:
        return status_of(project, cfg, embedding)
    store[name] = BackendConsent(fingerprint=info["fingerprint"], operations=merged, parts=info["parts"],
                                 allowed_at=utcnow(), allowed_by=current_author(author) or None)
    project.save()
    from .backends import record_probe

    record_probe(project, "embedding" if embedding else "llm", cfg, None, info["locality"])   # not a check
    return status_of(project, cfg, embedding)


def revoke_backend(project: Project, name: str, operations: list[str] | tuple[str, ...] | None = None,
                   embedding: bool = False) -> None:
    """Withdraw consent for ``operations`` (all when none are named). Nothing happens when there is none."""
    for op in operations or ():
        _check_op(op, embedding)
    store = _store(project, embedding)
    rec = store.get(name)
    if rec is None:
        return
    if operations:
        rest = [op for op in rec.operations if op not in operations]
        if rest == rec.operations:
            return
        if rest:
            rec.operations = rest
        else:
            del store[name]
    else:
        del store[name]
    project.save()


def _loose(parts: dict[str, Any]) -> dict[str, Any]:
    """The fingerprint parts known without a request: all but where it runs, and the endpoint without the cloud
    suffix an Ollama check adds."""
    out = {k: v for k, v in parts.items() if k != "locality"}
    out["endpoint"] = str(out.get("endpoint") or "").removesuffix(" -> ollama.com")
    return out


def status_of(project: Project, cfg, embedding: bool = False, probe: bool = True) -> dict[str, Any]:
    """Consent line of one backend. ``probe=False`` (the pages that only show it) makes no request: the locality is
    the backend row's, and a consent whose locality cannot be told without a check (an Ollama LLM backend never
    checked; any Ollama embedding) is compared on the parts known without one and listed as not verified."""
    from .summary import LOCALITY_LABELS

    info = assess(cfg, embedding) if probe else assess_stored(project, cfg, embedding)
    rec = _store(project, embedding).get(cfg.name)
    unchecked = not probe and info["source"] == "not_checked"
    needs: bool | None = None if unchecked else info["needs"]
    ops: list[str] = []
    verified, stale, changed = True, False, []
    if rec is not None:
        if rec.fingerprint == info["fingerprint"]:
            ops = list(rec.operations)
        elif not probe and (unchecked or (embedding and cfg.kind == "ollama")) \
                and _loose(rec.parts) == _loose(info["parts"]):
            ops, verified = list(rec.operations), False
            needs = needs or rec.parts.get("locality") in NEEDS_CONSENT
        else:
            stale, changed = True, _changed(rec.parts, info["parts"])
    label = LOCALITY_LABELS.get(info["locality"], info["locality"])
    if unchecked:
        label = "unknown, press Check"
    return {"name": cfg.name, "kind": cfg.kind, "embedding": embedding, "locality": info["locality"],
            "locality_label": label, "destination": info["destination"], "needs_consent": needs,
            "warning": info["warning"], "operations": ops if needs else [], "verified": verified,
            "stale": stale, "changed": changed,
            "allowed_at": rec.allowed_at if rec else None, "allowed_by": rec.allowed_by if rec else None,
            "sent": dict(EMBED_SENT if embedding else SENT)}


def status(project: Project, name: str | None = None, embedding: bool = False,
           probe: bool = True) -> list[dict[str, Any]]:
    """One row per configured backend (or just ``name``), plus consent entries whose backend no longer exists.
    ``probe=False``: no request (see :func:`status_of`)."""
    cfgs = project.config.embeddings if embedding else project.config.providers
    if name is not None:
        return [status_of(project, _cfg(project, name, embedding), embedding, probe)]
    rows = [status_of(project, c, embedding, probe) for c in cfgs]
    known = {c.name for c in cfgs}
    for n, rec in _store(project, embedding).items():
        if n not in known:
            rows.append({"name": n, "kind": rec.parts.get("kind"), "embedding": embedding, "locality": None,
                         "locality_label": None, "verified": True, "destination": rec.parts.get("endpoint"), "needs_consent": None, "warning": None,
                         "operations": list(rec.operations), "stale": True, "changed": ["the backend was removed"],
                         "allowed_at": rec.allowed_at, "allowed_by": rec.allowed_by,
                         "sent": dict(EMBED_SENT if embedding else SENT)})
    return rows
