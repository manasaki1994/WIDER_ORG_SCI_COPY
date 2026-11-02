"""Adjacent literatures: neighbouring bodies of work that the search retrieves and that could contaminate the results.

They live in the versioned ``adjacent`` artifact as a list of literatures, each with a key, a name, a description in
the register of its own abstracts, what it fails in the codebook, the cue that tells it apart from an include and a
few example phrases. Entries carry an ``origin`` (researcher or tool) and a ``status`` (proposed, approved, rejected);
only approved entries drive the later stages (marginal-case quotas in the development sample and the
``adjacent_literature`` screening label). Every change saves a new artifact version, like the other artifacts.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from ..core.project import Project
from ..core.provenance import RunSpec, sha256_json, sha256_text
from ..providers import ChatRequest, research_provider
from ..providers.registry import require_text
from . import artifacts
from .artifacts import dump_yaml
from .propose import USER_MESSAGE, ProposalError, _artifact_or_none, _env, benchmark_context

KIND = "adjacent"
FIELDS = ("key", "name", "description", "differs_from_construct", "discriminating_cue", "example_phrases", "origin", "status")
STATUSES = ("proposed", "approved", "rejected")

PROPOSAL_SCHEMA = {
    "type": "object",
    "properties": {
        "literatures": {"type": "array", "minItems": 1, "items": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "differs_from_construct": {"type": "string"},
                "discriminating_cue": {"type": "string"},
                "example_phrases": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["key", "name", "description", "differs_from_construct", "discriminating_cue", "example_phrases"]}},
    },
    "required": ["literatures"],
}


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    s = re.sub(r"_+", "_", s)
    if not s or not s[0].isalpha():
        s = "lit_" + s
    return s[:48].rstrip("_")


def normalise(entry: dict) -> dict:
    """Fill the optional fields of a literature entry and keep the field order stable."""
    e = {k: entry.get(k) for k in FIELDS}
    e["key"] = e["key"] or slug(e["name"] or "")
    e["name"] = (e["name"] or e["key"]).strip()
    e["description"] = (e["description"] or "").strip()
    e["differs_from_construct"] = (e["differs_from_construct"] or "").strip()
    e["discriminating_cue"] = (e["discriminating_cue"] or "").strip()
    e["example_phrases"] = [str(x).strip() for x in (e["example_phrases"] or []) if str(x).strip()]
    e["origin"] = e["origin"] or "researcher"
    e["status"] = e["status"] or "proposed"
    if entry.get("evidence"):
        e["evidence"] = entry["evidence"]
    return e


def load_list(project: Project) -> tuple[dict | None, list[dict]]:
    """The newest version of the artifact and its literature list (empty when there is no version yet)."""
    row, data = artifacts.load(project, KIND)
    if row is None:
        return None, []
    return row, [normalise(e) for e in (data or {}).get("literatures", [])]


def approved_literatures(project: Project) -> list[dict]:
    """Approved entries of the newest version: the only ones later stages act on."""
    _, lits = load_list(project)
    return [e for e in lits if e["status"] == "approved"]


def embedding_text(entry: dict) -> str:
    """The text embedded to find a literature's marginal cases: description plus example phrases."""
    parts = [entry.get("description") or entry.get("name") or ""] + list(entry.get("example_phrases") or [])
    return " ".join(p for p in parts if p).strip()


def _save(project: Project, lits: list[dict], origin: str, note: str) -> dict:
    keys = [e["key"] for e in lits]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate literature keys: " + ", ".join(k for k in keys if keys.count(k) > 1))
    return artifacts.save_new_version(project, KIND, {"literatures": lits}, origin=origin, note=note)


def unique_key(key: str, taken: set[str]) -> str:
    base = key
    i = 2
    while key in taken:
        key = f"{base}_{i}"
        i += 1
    return key


def add(project: Project, name: str, description: str = "", differs_from_construct: str = "", discriminating_cue: str = "",
        example_phrases: list[str] | None = None, key: str | None = None, origin: str = "researcher",
        status: str = "approved", note: str = "") -> dict:
    """Add one literature typed by the researcher (approved by default: it is their own knowledge) and save a version."""
    if not (name or "").strip():
        raise ValueError("the literature needs a name")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    _, lits = load_list(project)
    entry = normalise({"key": key, "name": name, "description": description or name, "differs_from_construct": differs_from_construct,
                       "discriminating_cue": discriminating_cue, "example_phrases": example_phrases, "origin": origin, "status": status})
    entry["key"] = unique_key(entry["key"], {e["key"] for e in lits})
    row = _save(project, lits + [entry], origin="manual", note=note or f"added {entry['key']}")
    return {**row, "entry": entry}


def update_entry(project: Project, key: str, note: str = "", **fields: Any) -> dict:
    """Edit the fields of one literature (name, description, cue, phrases, status) as a new version."""
    _, lits = load_list(project)
    idx = next((i for i, e in enumerate(lits) if e["key"] == key), None)
    if idx is None:
        raise ValueError(f"no adjacent literature {key!r}")
    allowed = {k: v for k, v in fields.items() if k in FIELDS and k != "key" and v is not None}
    if "status" in allowed and allowed["status"] not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    lits[idx] = normalise({**lits[idx], **allowed})
    row = _save(project, lits, origin="manual", note=note or f"edited {key}")
    return {**row, "entry": lits[idx]}


def set_status(project: Project, key: str, status: str, note: str = "") -> dict:
    return update_entry(project, key, status=status, note=note or f"{key} {status}")


def merge_candidates(project: Project, candidates: list[dict], origin: str = "tool", status: str = "proposed", note: str = "") -> dict:
    """Append proposed literatures that are not already listed (by key or by name); returns the version and the added keys."""
    _, lits = load_list(project)
    taken = {e["key"] for e in lits}
    names = {e["name"].strip().lower() for e in lits}
    added = []
    for c in candidates:
        e = normalise({**c, "origin": origin, "status": status})
        if not e["name"] or e["name"].lower() in names:
            continue
        e["key"] = unique_key(e["key"], taken)
        taken.add(e["key"])
        names.add(e["name"].lower())
        lits.append(e)
        added.append(e["key"])
    if not added:
        return {"version": None, "added": [], "n": len(lits)}
    row = _save(project, lits, origin=origin if origin in ("llm", "manual", "import") else "llm",
                note=note or f"{len(added)} literatures proposed by the tool")
    return {**row, "added": added, "n": len(lits)}


def seed_from_codebook(project: Project, note: str = "") -> dict:
    """Pull the codebook's ``adjacent_literatures`` into the artifact as researcher entries awaiting approval."""
    row, cb = _artifact_or_none(project, "codebook")
    if not cb:
        return {"version": None, "added": [], "n": 0, "codebook_version": None}
    cands = []
    for a in cb.get("adjacent_literatures", []) or []:
        differs = a.get("strains_element") or ""
        if a.get("decision"):
            differs = f"{differs}: {a['decision']}" if differs else a["decision"]
        if a.get("reason_code"):
            differs += f" (reason {a['reason_code']})"
        cands.append({"name": a.get("name"), "description": a.get("note") or a.get("name"), "differs_from_construct": differs})
    out = merge_candidates(project, cands, origin="researcher", status="proposed",
                           note=note or f"seeded from codebook v{row['version']}")
    out["codebook_version"] = row["version"]
    return out


def build_prompt(project: Project) -> tuple[str, dict]:
    """System prompt asking for the adjacent literatures, grounded in the question and the development abstracts."""
    cfg = project.config
    bench = benchmark_context(project)
    if not bench:
        raise ProposalError("benchmark.csv has no Development papers; add them first (wider benchmark fetch)")
    _, lits = load_list(project)
    crow, cb = _artifact_or_none(project, "codebook")
    ctx = {"questions": cfg.questions or [cfg.title or cfg.name], "include_notes": cfg.include_notes,
           "exclude_notes": cfg.exclude_notes, "benchmark": bench, "existing": lits,
           "codebook_yaml": dump_yaml({k: cb[k] for k in ("target_description", "elements") if k in cb}) if cb else ""}
    prompt = _env().get_template("adjacent.md.j2").render(**ctx)
    return prompt, {"benchmark_n": len(bench), "existing": [e["key"] for e in lits],
                    "codebook_version": crow["version"] if crow else None}


async def propose_from_question_async(project: Project, provider_name: str | None = None, note: str = "") -> dict:
    """Ask the model which adjacent literatures the search will retrieve; merge them as tool proposals."""
    pcfg = require_text(project.provider(provider_name) if provider_name else project.provider_for("construct"))
    provider = research_provider(project, pcfg)
    prompt, info = build_prompt(project)
    run_dir = project.run_dir("construct", RunSpec(kind="construct").stamp + "_adjacent")
    req = ChatRequest(system=prompt, user=USER_MESSAGE, schema=PROPOSAL_SCHEMA, max_tokens=6000)
    from ..core.usage import track
    with track(project, "construct", run_dir.name):
        r = await provider.complete(req)
    (run_dir / "prompt_all.md").write_text(prompt, encoding="utf-8")
    (run_dir / "response_all.txt").write_text(r.text or "", encoding="utf-8")
    call = {"ok": r.ok, "attempts": r.attempts, "error": r.error, "usage": r.usage, "input_sha256": r.input_sha256,
            "elapsed_s": round(r.elapsed_s, 1)}
    spec = RunSpec(kind="construct", inputs={"prompt": sha256_text(prompt), "schema": sha256_json(PROPOSAL_SCHEMA)},
                   settings={"artifact": KIND, "context": info}, provider=provider.identity(), notes=note)
    spec.write(run_dir)
    (run_dir / "calls.json").write_text(json.dumps([call], indent=1), encoding="utf-8")
    if not r.ok:
        raise ProposalError(f"adjacent proposal failed: {r.error}")
    merged = merge_candidates(project, (r.parsed or {}).get("literatures", []), origin="tool", status="proposed",
                              note=note or f"proposed by {pcfg.name}/{pcfg.model}; run {run_dir.name}")
    return {**merged, "run_dir": str(run_dir), "call": call, "proposed": len((r.parsed or {}).get("literatures", []))}


def propose_from_question(project: Project, **kw) -> dict:
    return asyncio.run(propose_from_question_async(project, **kw))


def summary(project: Project) -> dict:
    row, lits = load_list(project)
    return {"row": row, "literatures": lits, "versions": artifacts.list_versions(project, KIND),
            "approved": [e["key"] for e in lits if e["status"] == "approved"]}
