"""Names from connected papers the query misses (papers-beyond-the-query, design D24).

Chaining candidates linked to several seeds are likely relevant, so a word their titles and keywords share that the
query misses may be a name the query lacks. :func:`missed_names` runs the dry run (``evaluate``) on each candidate
with ``n_linking >= min_links`` that is new or added to Development; dismissed candidates and any whose paper is a
Validation seed are left out and counted (Validation never drives an edit, as in ``diagnostics.py``). For the
candidates the query misses, word n-grams (1 to 3 words after ``norm_text``, :data:`STOPWORDS` trimmed) of the title
and each keyword that no block holds and no ``near_miss`` entry's key names are kept when two or more missed
candidates hold them, ranked by that count, then by summed ``n_linking``. Each name carries its dry-run consequence
in the block its candidates fail: "turns N connected candidates into hits, +k development seeds".

Nothing is written here. A name enters the query only through ``names.admit`` (the researcher's "Add to block" or
``wider seeds names --admit``) with the note :func:`admit_note` gives. The optional model pass (``model=True``) asks
the stage ``construct`` backend, one call per missed candidate, which phrases of its title, abstract and keywords
name the construct of the block it fails; a phrase not in that candidate's text is dropped and counted (D22). Only
public metadata goes to the model, so the backend's ``research`` consent is asked, not ``passages``.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..core.project import Project
from ..core.text import clean_doi, norm_text, norm_title

STOPWORDS = frozenset(
    "a about across after among an and are as at be between by can do does during for from has have how in into is "
    "it its not of on or over than that the their these this those through to toward towards under upon use using "
    "versus via vs was we were what when which while who why with within without".split())
MAX_NAMES = 30
USAGE_STAGE = "missed_names"   # the llm_usage stage of the model pass (`wider costs`); backend: construct
TEMPLATE = Path(__file__).parent / "templates" / "missed_names.md"
SCHEMA = {"type": "object", "properties": {"names": {"type": "array", "items": {
    "type": "object", "properties": {"block": {"type": "string"}, "phrase": {"type": "string"},
                                     "candidate_id": {"type": "string"}},
    "required": ["block", "phrase", "candidate_id"]}}}, "required": ["names"]}
NOT_JUDGED = "connected, not judged"


def _grams(text: str) -> set[str]:
    """Word n-grams of 1 to 3 words; none starts or ends with a stopword, none is a number or a one- or two-letter
    word."""
    toks = norm_text(text).split()
    out = set()
    for n in (1, 2, 3):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in STOPWORDS or g[-1] in STOPWORDS or all(t.isdigit() for t in g) or (n == 1 and len(g[0]) < 3):
                continue
            out.add(" ".join(g))
    return out


def _in(phrase: str, text: str) -> bool:
    """``phrase`` occurs in ``text`` as whole words, after ``norm_text`` on both (the verbatim check of D22)."""
    p = norm_text(phrase)
    return bool(p) and f" {p} " in f" {norm_text(text)} "


def admit_note(candidate_ids: list[int], note: str | None = None) -> str:
    """The note an admission from connected papers carries: "from connected candidates C3, C7" and the researcher's
    words."""
    base = "from connected candidates " + ", ".join(f"C{i}" for i in candidate_ids)
    return f"{base}; {note.strip()}" if note and note.strip() else base


def _candidates(project: Project, min_links: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The candidates to evaluate and the counts of those left out (``validation``, ``dismissed``, ``few_links``)."""
    from . import chain

    sets: dict[str, str] = {}
    by_doi, by_title = {}, {}
    for b in project.read_benchmark():
        sets[b.benchmark_id] = b.set
        if b.doi:
            by_doi[clean_doi(b.doi)] = b.benchmark_id
        if b.title:
            by_title[norm_title(b.title)] = b.benchmark_id
    left = {"validation": 0, "dismissed": 0, "few_links": 0}
    out = []
    for row in chain.candidates(project):
        ids = {i for i in (row["benchmark_id"], by_doi.get(row["doi"]) if row["doi"] else None,
                           by_title.get(row["title_norm"]) if row["title_norm"] else None) if i}
        if any(sets.get(i) == "Validation" for i in ids):
            left["validation"] += 1   # before the dismissed count: a Validation paper is never named, in any group
        elif row["status"] == "dismissed":
            left["dismissed"] += 1
        elif (row["n_linking"] or 0) < min_links:
            left["few_links"] += 1
        elif row["status"] == "added" and not any(sets.get(i) == "Development" for i in ids):
            continue   # added, and the seed paper has since left the benchmark
        else:
            out.append(row)
    return out, left


def _doc(row: dict[str, Any]):
    from ..search.evaluate import SeedDoc

    return SeedDoc(f"C{row['candidate_id']}", "candidate", row["title"] or "", row["abstract"] or "",
                   "; ".join(row["keywords"] or []))


def _near_miss_keys(project: Project) -> set[str]:
    from ..construct import artifacts

    try:
        _row, data = artifacts.load(project, "near_miss", None)
    except (KeyError, ValueError):
        return set()
    return {norm_text(str(e.get("key", "")).replace("_", " ")) for e in ((data or {}).get("near_miss") or [])
            if isinstance(e, dict)}


def _held(phrase: str, query, profile) -> bool:
    """A block already holds ``phrase``: some block retrieves a record that is only this phrase."""
    from ..search.evaluate import SeedDoc, evaluate_block

    probe = SeedDoc("probe", "probe", phrase, phrase, phrase)
    return any(evaluate_block(b, probe, profile).state != "miss" for b in query.blocks)


def missed_names(project: Project, version: int | None = None, platform: str | None = None, min_links: int = 2,
                 model: bool = False, provider=None) -> dict[str, Any]:
    """The names from connected candidates the query misses, with their consequence on ``platform`` (default: the
    first profiled platform of the project). ``model`` adds the model pass; ``provider`` is a ready adapter (tests).
    Raises ``ValueError`` without a synonyms version or for a platform without a profile."""
    from ..search.diagnostics import edited_data
    from ..search.evaluate import Query, evaluate
    from .names import _setup, _term

    if min_links < 1:
        raise ValueError("the minimum number of linking seeds (--min-links) must be 1 or more")
    row, data, query, positive, dev_docs, profiles = _setup(project, version)
    if platform is not None:
        profiles = [pr for pr in profiles if pr.platform == platform]
        if not profiles:
            raise ValueError(f"no profile for platform {platform}")
    if not profiles:
        raise ValueError("no platform with a profile: the dry run cannot evaluate the candidates")
    pr = profiles[0]
    rows, left = _candidates(project, min_links)
    by_id = {f"C{r['candidate_id']}": r for r in rows}
    missed: dict[str, Any] = {}
    uncertain = 0
    for r in rows:
        res = evaluate(query, _doc(r), pr)
        if res.state == "miss":
            missed[f"C{r['candidate_id']}"] = res
        uncertain += res.state == "uncertain"

    # the deterministic pass: n-grams of titles and keywords shared by two or more missed candidates
    holders: dict[str, set[str]] = {}
    for cid in missed:
        r = by_id[cid]
        grams = _grams(r["title"] or "")
        for kw in r["keywords"] or []:
            grams |= _grams(kw)
        for g in grams:
            holders.setdefault(g, set()).add(cid)
    skip = _near_miss_keys(project)
    shared = {g: c for g, c in holders.items() if len(c) >= 2 and g not in skip}
    shared = {g: c for g, c in shared.items()   # a shorter name inside a longer one with the same candidates goes
              if not any(h != g and c2 == c and f" {g} " in f" {h} " for h, c2 in shared.items())}
    found: dict[str, dict[str, Any]] = {g: {"name": g, "holders": c, "origin": ["text"], "model_blocks": {}}
                                        for g, c in shared.items()}

    run = None
    if model:
        run = _model_pass(project, query, missed, by_id, provider)
        for n in run.pop("names", []):
            k = norm_text(n["phrase"])
            f = found.setdefault(k, {"name": k, "holders": set(), "origin": [], "model_blocks": {}})
            f["holders"].add(n["candidate_id"])
            f["model_blocks"][n["candidate_id"]] = n["block"]
            if "llm" not in f["origin"]:
                f["origin"].append("llm")

    names = []
    for f in found.values():
        if _held(f["name"], query, pr):
            continue
        fails: Counter = Counter()   # the blocks its candidates fail (the model's block for a model name)
        for c in f["holders"]:
            mb = f["model_blocks"].get(c)
            fails.update([mb] if mb else missed[c].failed_blocks)
        if not fails:
            continue
        order = sorted(fails, key=lambda k: (-fails[k], positive.index(k) if k in positive else len(positive)))
        per = []
        for k in order:
            edited = Query.model_validate(edited_data(data, {"op": "add_term", "block": k, "term": _term(f["name"])}))
            turns = [c for c in missed if evaluate(edited, _doc(by_id[c]), pr).state == "hit"]
            gains = [d.benchmark_id for d in dev_docs
                     if evaluate(query, d, pr).state == "miss" and evaluate(edited, d, pr).state == "hit"]
            per.append({"block": k, "turns": turns, "gains": gains})
        best = max(per, key=lambda x: len(x["turns"]))   # max keeps the first of equals: the most failed block
        cands = sorted(f["holders"], key=lambda c: int(c[1:]))
        quoted = [{"candidate_id": c, "title": by_id[c]["title"], "status": by_id[c]["status"],
                   "n_linking": by_id[c]["n_linking"], "benchmark_id": by_id[c]["benchmark_id"]} for c in cands]
        not_judged = any(q["status"] == "new" for q in quoted)
        line = f"turns {len(best['turns'])} connected candidates into hits, +{len(best['gains'])} development seeds"
        names.append({"name": f["name"], "origin": f["origin"], "block": best["block"], "turns": best["turns"],
                      "gains": best["gains"], "blocks": per, "candidates": quoted, "n": len(cands),
                      "links": sum(q["n_linking"] or 0 for q in quoted), "not_judged": not_judged,
                      "line": line + (f"; {NOT_JUDGED}" if not_judged else ""),
                      "note": admit_note([int(c[1:]) for c in cands])})
    names.sort(key=lambda x: (-x["n"], -x["links"], x["name"]))
    # none of the project's platforms has a dry-run profile: the candidates are judged with another platform's
    # profile, which the heading must say (it read "on ebsco" in a project searching other platforms; inspection
    # 1 Nov 2026)
    from ..search.catalogue import platform_labels
    label = platform_labels().get(pr.platform, pr.platform)
    borrowed = pr.platform not in [p.key for p in project.config.platforms]
    out = {"version": row["version"], "platform": pr.platform, "min_links": min_links, "evaluated": len(rows),
           "missed": len(missed), "uncertain": uncertain, "left_out": left, "names": names[:MAX_NAMES], "more": max(0, len(names) - MAX_NAMES),
           "model": run, "borrowed_profile": borrowed,
           "where": (f"judged with the {label} dry-run profile, since none of your platforms has one" if borrowed
                     else f"on {label}")}
    return out


def needs_consent(project: Project, provider=None) -> list[tuple[Any, str]]:
    """The consent the model pass still lacks: ``[(cfg, "research")]`` or empty (also when the pass is skipped)."""
    from ..core import backend_consent as bc
    from ..providers.gate import tool_gate
    from . import mine

    try:
        cfg, _loc = mine.target(project, provider)
    except (KeyError, TypeError):
        return []
    if tool_gate(cfg):
        return []
    try:
        bc.require_backend_consent(project, cfg, "research")
    except bc.BackendConsentRequired:
        return [(cfg, "research")]
    return []


def _block_text(query, k: str) -> str:
    from ..search.evaluate import term_label

    b = next(b for b in query.blocks if b.key == k)
    return f"block {k}" + (f" ({b.name})" if b.name else "") + ": " + ", ".join(term_label(t) for t in b.terms)


def _model_pass(project: Project, query, missed: dict[str, Any], by_id: dict[str, dict], provider) -> dict[str, Any]:
    """One call per missed candidate; a phrase is kept only when it is in that candidate's title, abstract or
    keywords. ``calls`` holds hashes, attempts and repairs, never text."""
    import asyncio

    from ..core import backend_consent as bc
    from ..core.provenance import sha256_text
    from ..core.usage import track
    from ..providers.gate import tool_gate
    from . import mine

    if not missed:
        return {"ran": False, "message": "the model pass had nothing to read: the query misses no connected candidate"}
    try:
        cfg, _loc = mine.target(project, provider)
    except (KeyError, TypeError):
        return {"ran": False, "message": "no backend for the stage construct, so the model pass did not run: "
                                          "the names below come from the text alone"}
    why = tool_gate(cfg)
    if why:
        return {"ran": False, "message": f"the model pass is skipped: {why}, so it gets no text"}
    if provider is None:
        from ..providers.registry import research_provider
        provider = research_provider(project, cfg)
    bc.require_backend_consent(project, cfg, "research")
    system = TEMPLATE.read_text(encoding="utf-8")
    seed = cfg.seed if cfg.seed is not None else 1
    items = []
    for cid, res in missed.items():
        r = by_id[cid]
        text = "\n".join(x for x in (r["title"] or "", r["abstract"] or "", "; ".join(r["keywords"] or [])) if x)
        blocks = "\n".join(_block_text(query, k) for k in res.failed_blocks)
        user = (f"Candidate {cid}\nTitle: {r['title'] or ''}\nAbstract: {r['abstract'] or ''}\n"
                f"Keywords: {'; '.join(r['keywords'] or [])}\n\nThe query block(s) this paper fails:\n{blocks}")
        items.append((cid, text, list(res.failed_blocks), mine.rules_request(system, user, SCHEMA, seed)))

    async def run_all():
        return await asyncio.gather(*(provider.complete(req, retries=mine.CALLS_PER_ITEM) for *_x, req in items))

    with track(project, USAGE_STAGE):
        results = asyncio.run(run_all())
    names, calls, dropped, failed = [], [], 0, 0
    for (cid, text, fails, req), res in zip(items, results):
        rec = {"candidate_id": cid, "input_sha256": res.input_sha256 or req.input_sha256,
               "output_sha256": sha256_text(res.text) if res.text else None, "attempts": res.attempts,
               "repairs": res.repairs, "ok": res.ok, "kept": 0, "dropped": 0}
        if not res.ok:
            failed += 1
        else:
            for n in (res.parsed or {}).get("names", []):
                phrase = (n.get("phrase") or "").strip()
                if not _in(phrase, text):
                    dropped += 1
                    rec["dropped"] += 1
                    continue
                rec["kept"] += 1
                names.append({"phrase": phrase, "candidate_id": cid,
                              "block": n.get("block") if n.get("block") in fails else fails[0]})
        calls.append(rec)
    parts = [f"{len(names)} kept"]
    if dropped:
        parts.append(f"{dropped} dropped: not in the candidate's text")
    if failed:
        parts.append(f"{failed} candidate(s) failed after {mine.CALLS_PER_ITEM} calls at most")
    return {"ran": True, "message": "model pass: " + ", ".join(parts), "kept": len(names), "dropped": dropped,
            "failed": failed, "calls": calls, "names": names, "provider": provider.identity(),
            "backend_kind": cfg.kind}


def lines(d: dict[str, Any]) -> list[str]:
    """The CLI's text (and the page's ``lines``)."""
    lo = d["left_out"]
    out = [f"synonyms v{d['version']} {d.get('where') or 'on ' + d['platform']}: {d['evaluated']} connected candidate(s) linked to "
           f"{d['min_links']}+ seeds evaluated, {d['missed']} missed by the query; left out: {lo['validation']} "
           f"Validation, {lo['dismissed']} dismissed, {lo['few_links']} with fewer links"]
    if d["uncertain"]:
        out.append(f"{d['uncertain']} candidate(s) uncertain in the dry run (the profile cannot decide, often no "
                   "abstract) are not used")
    if d.get("model"):
        out.append(d["model"]["message"])
    if not d["names"]:
        out.append("no name: no word is shared by two or more missed candidates and missing from every block")
    for n in d["names"]:
        src = "" if n["origin"] == ["text"] else f" [{', '.join(n['origin'])}]"
        out.append(f"{n['name']}{src} → block {n['block']}: {n['line']}")
        for q in n["candidates"]:
            out.append(f"  {q['candidate_id']} ({q['status']}, {q['n_linking']} seeds): “{q['title']}”")
        out.append(f"  add with: wider seeds names --admit \"{n['name']}\" --block {n['block']} --note "
                   f"\"{n['note']}; <your reason>\"")
    if d["more"]:
        out.append(f"{d['more']} more name(s) not shown")
    out.append("Nothing changes until you add a name; the dry run's consequence is not a judgement of the papers.")
    return out
