"""First-launch backend check: probe the default LLM backend, each stage-override backend and the default embedding
model.

Reuses the probes behind `wider providers probe` and `wider embed probe`; they send only fixed synthetic test
sentences, never project data. A local embedding model whose weights are not downloaded is reported, not
downloaded. The result is kept in the store's meta table so the home page can show when it last ran.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .backends import STAGE_KEYS, stage_use
from .detect import effective_locality
from .provenance import utcnow
from .summary import BACKEND_CHECK_META, embedding_locality

if TYPE_CHECKING:
    from .project import Project


async def check_backends(project: Project) -> dict[str, Any]:
    from ..embeddings.registry import probe as probe_embedding
    from ..providers.registry import probe as probe_provider
    from .backend_consent import BackendConsentRequired, require_backend_consent
    from .backends import is_downloaded

    c = project.config
    results: list[dict[str, Any]] = []
    # the default and each stage override, each distinct backend once with every use it has
    uses: dict[str, list[str]] = {}
    if c.default_provider:
        uses[c.default_provider] = ["default"]
    for stage in STAGE_KEYS:
        name = c.stage_providers.get(stage)
        if name and stage_use(stage) not in uses.get(name, []):   # review_2 is the review row's second reader
            uses.setdefault(name, []).append(stage_use(stage))
    if not c.default_provider:
        results.append({"role": "llm", "name": None, "uses": ["default"], "ok": False,
                        "error": "no default LLM backend chosen"})
    for name, used_for in uses.items():
        try:
            cfg = project.provider(name)
        except (KeyError, TypeError):
            results.append({"role": "llm", "name": name, "uses": used_for, "ok": False,
                            "error": f"backend {name!r} is not in project.yaml"})
            continue
        loc = effective_locality(cfg)
        row = {"role": "llm", "name": cfg.name, "uses": used_for, "kind": cfg.kind, "model": cfg.model,
               "locality": loc[0], "destination": loc[2]}
        try:
            info = require_backend_consent(project, cfg, "probe")
        except BackendConsentRequired as e:
            # nothing is sent without consent; the row says why it was not checked
            results.append({**row, "ok": False, "skipped": True, "needs_consent": True,
                            "error": f"not checked: needs your consent to contact {e.destination} "
                                     f"(wider providers allow {cfg.name} --for probe)"})
            continue
        try:
            r = await probe_provider(cfg, locality=(info["locality"], info["source"]))
        except Exception as ex:  # report, do not crash the page
            r = {"name": cfg.name, "kind": cfg.kind, "model": cfg.model, "ok": False, "error": str(ex)[:400]}
        results.append({**row, "ok": bool(r.get("ok")), "elapsed_s": r.get("elapsed_s"), "error": r.get("error")})
    try:
        ecfg = project.embedding(c.default_embedding)
    except (KeyError, TypeError):
        results.append({"role": "embedding", "name": c.default_embedding, "ok": False, "error": "no default embedding model chosen"})
    else:
        row = {"role": "embedding", "name": ecfg.name, "kind": ecfg.kind, "model": ecfg.model,
               "locality": embedding_locality(ecfg.kind, ecfg.base_url)}
        if is_downloaded(ecfg) is False:
            row.update(ok=False, skipped=True, error="model weights are not downloaded (wider embed download)")
        else:
            try:
                r = probe_embedding(ecfg, project.path("embeddings", "cache.sqlite"), project=project)
            except BackendConsentRequired as e:
                r = {"ok": False, "error": f"not checked: needs your consent to contact {e.destination} "
                                          f"(wider embed allow {ecfg.name} --for probe)"}
                row.update(skipped=True, needs_consent=True)
            row.update(ok=bool(r.get("ok")), elapsed_s=r.get("elapsed_s"), error=r.get("error"))
        results.append(row)
    out = {"at": utcnow(), "ok": all(r["ok"] for r in results), "results": results}
    project.store.set_meta(BACKEND_CHECK_META, json.dumps(out))
    return out
