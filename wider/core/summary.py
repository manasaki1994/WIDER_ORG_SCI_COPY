"""One-screen project summary shared by the home page (project card, info panel) and `wider info`.

Everything here is read from ``project.yaml`` and the store; nothing is fetched and nothing leaves the machine.
Costs come from the usage log (``core/usage.py``): the cost a backend reported, else its prices; token counts read
the normalised per-call usage through ``core/usage.py:tokens_of``.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from .project import Project

BACKEND_CHECK_META = "backend_check"
NETWORK_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa")
LOCALITY_LABELS = {"local": "this computer", "cloud": "remote", "network": "Local network", "unknown": "unknown",
                   "test": "test"}


def host_class(url: str | None) -> str:
    """'loopback', 'network', 'remote' or 'unknown', from the URL alone (no name is resolved)."""
    import ipaddress

    if not url:
        return "unknown"
    try:
        host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower().rstrip(".")
    except ValueError:
        return "unknown"
    if not host:
        return "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_unspecified:
            return "unknown"
        if ip.is_loopback:
            return "loopback"
        if ip.is_private or ip.is_link_local:
            return "network"
        return "remote"
    if host == "localhost" or host.endswith(".localhost"):
        return "loopback"
    if host.endswith(NETWORK_SUFFIXES) or "." not in host:
        return "network"
    return "remote"


def _host_is_local(url: str | None) -> bool:
    return host_class(url) == "loopback"


_CLASS_LOCALITY = {"loopback": "local", "network": "network", "remote": "cloud", "unknown": "unknown"}


def provider_locality(kind: str, base_url: str | None = None, routing=None, command=None) -> str:
    """Where a backend's text goes, from the actual endpoint (never from a preset's family):
    'local' (this computer), 'network' (another computer on the local network), 'cloud' (a remote service),
    'unknown' (not established; treated like cloud for consent and cost) or 'test' (fake backend).

    ``routing`` is the Ollama routing result for the model (``core/detect.py:ollama_routing``), a locality or a
    ``(locality, source)`` pair; ``None`` means not resolved, so a loopback Ollama is 'unknown'."""
    if kind == "fake":
        return "test"
    r = routing[0] if isinstance(routing, tuple) else routing
    if kind == "ollama":
        hc = host_class(base_url or "http://127.0.0.1:11434")
        if hc == "loopback":
            return r if r in ("local", "cloud", "unknown") else "unknown"
        if r == "cloud":
            return "cloud"
        return _CLASS_LOCALITY[hc]
    if kind in ("openai_compat", "litellm", "typesafe"):
        return _CLASS_LOCALITY[host_class(base_url)]
    return "cloud"  # anthropic, cli_agent (the agent CLI calls its vendor's API)


def embedding_locality(kind: str, base_url: str | None = None) -> str:
    if kind == "fake":
        return "test"
    if kind == "sentence_transformers":
        return "local"
    if kind == "ollama":
        return _CLASS_LOCALITY[host_class(base_url or "http://127.0.0.1:11434")]
    if kind == "openai_compat":
        return _CLASS_LOCALITY[host_class(base_url)]
    return "cloud"  # openai


def _backend(project: Project, cfgs, name: str | None, embedding: bool, chk) -> dict[str, Any] | None:
    """The row of the chosen backend, placed as the backend listings place it (no request, not even to 127.0.0.1)."""
    from .backends import backend_place

    for c in cfgs:
        if c.name == name:
            return {"name": c.name, "kind": c.kind, "model": c.model,
                    **backend_place(project, c, embedding=embedding, chk=chk)}
    return None


def _tokens(st) -> dict[str, int]:
    """Sum of prompt and completion tokens over every stored screening call (normalised or legacy usage)."""
    from .usage import tokens_of
    out = {"prompt": 0, "completion": 0, "calls": 0}
    if "screen_calls" not in st.tables():
        return out
    for r in st.query("SELECT usage FROM screen_calls"):
        out["calls"] += 1
        try:
            u = json.loads(r["usage"] or "{}") or {}
        except (TypeError, json.JSONDecodeError):
            continue
        t = tokens_of(u if isinstance(u, dict) else {})
        out["prompt"] += t["prompt"]
        out["completion"] += t["completion"]
    return out


def _cost(project: Project) -> dict[str, Any]:
    """Project total over every logged model call (and screening calls stored before the log existed)."""
    from .usage import project_totals

    t = project_totals(project)
    return {"usd": t["cost_usd"], "known": t["cost_known"], "unpriced_calls": t["unpriced_calls"], "calls": t["calls"],
            "tokens": t["tokens"], "locality": t["locality"], "cached_tokens": t["cached_tokens"],
            "logged_usd": t["logged_cost_usd"], "legacy_calls": t["legacy_calls"], "legacy_usd": t["legacy_cost_usd"],
            "legacy_label": t["legacy_label"]}


def _legacy_text(c: dict[str, Any]) -> str:
    if not c.get("legacy_calls"):
        return ""
    return (f" (logged ${c['logged_usd']:,.2f}; ${c['legacy_usd']:,.2f} for {c['legacy_calls']:,} older screening calls, "
            f"{c['legacy_label']})")


def cost_text(c: dict[str, Any] | None) -> str:
    from .usage import cache_text

    if not c or not c.get("calls"):
        return "no model calls yet"
    usd = f"${c['usd']:,.2f}" + _legacy_text(c)
    if c["known"]:
        return usd + (" (local backends only)" if c.get("locality") == "local" and c["usd"] == 0 else "") + f"; {cache_text(c)}"
    return (f"{usd} so far, plus {c['unpriced_calls']:,} calls without prices (set the backend's prices on Settings); "
            f"{cache_text(c)}")


def _seed_coverage(project: Project) -> dict[str, Any]:
    """Seed papers found: in the deduplicated corpus when it has been checked, else in any platform export. The
    total counts every seed paper; ``development`` counts the development set of D3 and ``used`` the used validation
    papers apart (validation-reuse D1)."""
    from ..search.coverage import development_papers, used_papers

    st = project.store
    n = st.scalar("SELECT COUNT(*) FROM benchmark_papers") or 0
    corpus = st.scalar("SELECT COUNT(*) FROM benchmark_matches WHERE scope='corpus'") or 0
    if corpus:
        where, scope = "scope='corpus'", "corpus"
    elif st.scalar("SELECT COUNT(DISTINCT benchmark_id) FROM benchmark_matches"):
        where, scope = "scope LIKE 'platform:%'", "platforms"
    else:
        return {"n": n, "found": None, "scope": None}
    found = {r["benchmark_id"] for r in st.query(f"SELECT DISTINCT benchmark_id FROM benchmark_matches WHERE {where} AND method!='none'")}
    papers = project.read_benchmark()
    dev = {b.benchmark_id for b in development_papers(project, papers)}
    used = {b.benchmark_id for b in used_papers(project, papers)}
    return {"n": n, "found": len(found), "scope": scope, "development": {"n": len(dev), "found": len(found & dev)},
            **({"used": {"n": len(used), "found": len(found & used)}} if used else {})}


def _llm_uses(project: Project, chk) -> list[dict[str, Any]]:
    """Each distinct LLM backend in use, with its uses (the default, then each stage override), as the backend check
    lists them; a name with no backend is left out."""
    from .backends import STAGE_KEYS, stage_use

    c = project.config
    uses: dict[str, list[str]] = {}
    if c.default_provider:
        uses[c.default_provider] = ["default"]
    for stage in STAGE_KEYS:
        name = c.stage_providers.get(stage)
        if name and stage_use(stage) not in uses.get(name, []):   # review_2 is the review row's second reader
            uses.setdefault(name, []).append(stage_use(stage))
    rows = []
    for name, used_for in uses.items():
        row = _backend(project, c.providers, name, False, chk)
        if row:
            rows.append({**row, "uses": used_for})
    return rows


def backend_check(project: Project) -> dict[str, Any] | None:
    raw = project.store.get_meta(BACKEND_CHECK_META)
    return json.loads(raw) if raw else None


def needs_items(states: list[dict[str, Any]]) -> list[dict[str, str]]:
    """What needs the researcher (ui-redesign D11): each stage's needs-you and needs-review item, in stage order."""
    out = []
    for s in states:
        if s.get("needs_you"):
            out.append({"stage": s["key"], "title": s["title"], "kind": "needs_you", "text": s["needs_you"]})
        if s.get("needs_review"):
            out.append({"stage": s["key"], "title": s["title"], "kind": "needs_review", "text": s["needs_review"]})
    return out


def waiting_counts(project: Project) -> dict[str, int]:
    """Open questions and review packages still out (made, neither imported nor withdrawn): counts only, so the
    Overview and `wider status` never name a reviewer or show what a package holds (ui-redesign, Project Overview)."""
    from . import questions as qs

    out = project.store.scalar("SELECT COUNT(*) FROM review_packages WHERE imported_at IS NULL AND withdrawn_at IS NULL")
    return {"open_questions": qs.counts(project).get("open", 0), "packages_out": out or 0}


def waiting_lines(w: dict[str, int]) -> list[str]:
    """The `wider status` lines of :func:`waiting_counts`, empty when nothing waits."""
    return ([f"open questions: {w['open_questions']} (wider questions)"] if w["open_questions"] else []) + (
        [f"review packages out: {w['packages_out']}"] if w["packages_out"] else [])


def need_you_text(n: int) -> str:
    """"1 needs you", "3 need you", or "Nothing waits for you"."""
    return "Nothing waits for you" if not n else f"{n} {'needs' if n == 1 else 'need'} you"


def project_summary(project: Project) -> dict[str, Any]:
    """Title, the next step and what needs the researcher (no stage count, ui-redesign D11), counts, seed coverage,
    backends (where they run, from their last check), tokens and the last run."""
    from .lookups import allowed as lookups_allowed
    from .stage_result import all_last_results
    from .stages import next_stage, route_sentence, stage_states

    c = project.config
    st = project.store
    states = stage_states(project)
    nxt = next_stage(project, states)
    needs = needs_items(states)
    results = all_last_results(project)
    last = max(results.values(), key=lambda r: r.get("ran_at") or "", default=None)
    counts = st.counts()
    chk = backend_check(project)
    prov = _backend(project, c.providers, c.default_provider, False, chk)
    emb = _backend(project, c.embeddings, c.default_embedding, True, chk)
    return {
        "name": c.name,
        "title": c.title or c.name,
        "root": str(project.root),
        "next": {"key": nxt["key"], "title": nxt["title"], "command": nxt["command"]} if nxt else None,
        "needs_you": needs,
        "need_you_text": need_you_text(len(needs)),
        **waiting_counts(project),
        "questions": list(c.questions),
        "route": route_sentence(project, states),
        # the Overview's Project summary: how many reviewers (no names), the search platforms, lookups on or off
        "reviewers": st.scalar("SELECT COUNT(*) FROM reviewers") or 0,
        "platforms": [pl.label or pl.key for pl in c.platforms],
        "lookups_allowed": lookups_allowed(project),
        "records": counts.get("records", 0),
        "raw_records": counts.get("raw_records", 0),
        "records_provisional": _records_provisional(project),
        "seed": _seed_coverage(project),
        "backends": {"llm": prov, "embedding": emb, "llm_uses": _llm_uses(project, chk)},
        "tokens": _tokens(st),
        "cost": _cost(project),
        "last_run": {"stage": last["stage"], "headline": last.get("headline"), "ran_at": last.get("ran_at")} if last else None,
        "backend_check": chk,
        "migration": _migration_notes(project),
        "pending_review": _pending_review(project),
        "dev_gate": _dev_gate(project),
    }


def _dev_gate(project: Project) -> dict[str, Any] | None:
    """Why screening runs on development records wait, for a project upgraded to D4 (ui-redesign)."""
    from ..screen.devgate import gate_item

    try:
        return gate_item(project)
    except Exception:   # a damaged sample must not break the home page
        return None


def _pending_review(project: Project) -> dict[str, Any] | None:
    """The records of the Screening review list that wait for a person (manual-review D12)."""
    from ..screen.review import pending

    try:
        return pending(project)
    except Exception:   # a damaged set must not break the home page
        return None


def _migration_notes(project: Project) -> list[str]:
    """What the result-set migration did to the stored protocols (llm-screening, "A protocol run on two scopes")."""
    from ..screen.result_sets import migration_notes

    return migration_notes(project)


def _records_provisional(project: Project) -> str | None:
    from ..recall.outputs import records_provisional

    return records_provisional(project)


def summary_lines(s: dict[str, Any]) -> list[tuple[str, str]]:
    """Label/value pairs for the CLI; the home page renders the same dict."""
    seed = s["seed"]
    dev = seed.get("development")
    seed_txt = (f"{seed['found']}/{seed['n']} found ({seed['scope']})"
                + (f"; development {dev['found']}/{dev['n']}" if dev else "") if seed["found"] is not None
                else f"{seed['n']} seed papers, coverage not checked yet")
    def be(b):
        return f"{b['name']} ({b['model']}, {b.get('locality_label') or b['locality']})" if b else "none chosen"
    tok = s["tokens"]
    nxt = s["next"]
    needs = s.get("needs_you") or []
    lines = [
        ("project", f"{s['title']}  [{s['root']}]"),
        ("next step", f"{nxt['title']} (wider {nxt['command']})" if nxt else "every step is done"),
        ("needs you", (s.get("need_you_text") or need_you_text(len(needs)))
                      + "".join(f"; {x['title']}: {x['text']}" for x in needs)),
        ("records", f"{s['records']:,} unique of {s['raw_records']:,} imported"
                    + (f". {s['records_provisional']}" if s.get("records_provisional") else "")),
        ("seed papers", seed_txt),
        ("AI model", "; ".join(f"{be(b)} for {', '.join(b['uses'])}" for b in s["backends"].get("llm_uses") or [])
                        or be(s["backends"]["llm"])),
        ("embedding model", be(s["backends"]["embedding"])),
        ("tokens", f"{tok['prompt'] + tok['completion']:,} over {tok['calls']:,} screening calls" if tok["calls"] else "no model calls stored yet"),
        ("cost", cost_text(s.get("cost"))),
    ]
    lr = s["last_run"]
    lines.append(("last run", f"{lr['stage']}: {lr['headline']} ({lr['ran_at']})" if lr else "nothing run yet"))
    chk = s["backend_check"]
    if chk:
        oks = ", ".join(f"{'LLM' if r.get('role') == 'llm' else 'embeddings'} {r['name']} {'ok' if r.get('ok') else 'failed'}"
                        for r in chk.get("results", []))
        lines.append(("backend check", f"{oks} ({chk.get('at')})"))
    else:
        lines.append(("backend check", "not run yet (wider check-backends)"))
    from ..screen.review import pending_line

    if pending_line(s.get("pending_review")):
        lines.append(("screening review", pending_line(s["pending_review"])))
    for note in s.get("migration") or []:
        lines.append(("result sets", note))
    return lines


__all__ = ["waiting_counts", "waiting_lines", "project_summary", "summary_lines", "provider_locality", "embedding_locality", "backend_check",
           "BACKEND_CHECK_META"]
