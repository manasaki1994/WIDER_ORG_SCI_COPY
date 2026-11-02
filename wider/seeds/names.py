"""Candidate names into the query (vision change 2b, design D11): the admission rule and the name map.

A mined name enters a block only through the researcher (``wider seeds names --admit``, the card's "Add to block" or
a ``refine_name_add`` answer), and only when 2a's dry run shows it turns a development seed from miss to hit on a
platform with a profile, or with the researcher's note. Mining itself never writes a version. An admission is one
journalled ``name_admit`` change (the new draft synonyms version's row, its round and the ledger row; an undo keeps
the version's file, as for any artifact row); one that saved over
a frozen query also recorded an unlock, which is final, so its undo is refused (``journal._permanent``).

The name map groups names that denote the same construct ("X, also called Y"; ``refine_same_construct`` answers
"same construct") and flags look-alikes (answered "different construct"); no embeddings.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ..core import journal
from ..core.project import Project
from ..core.provenance import utcnow

NOT_ADMITTED = "not admitted: retrieves no development seed"


def _term(name: str) -> str:
    """The query term for a name: a phrase in quotes when it has a space (as 2a's ``add_name`` edits write it)."""
    n = " ".join(name.split())
    return f'"{n}"' if " " in n else n


def _admitted(project: Project) -> list[tuple[str, str]]:
    """``(name key, block)`` for every name admitted into a block and not undone, oldest first: by ``name_admit``
    or by a ``refine_name_add`` answer "add" (D12)."""
    out = []
    for r in project.store.query("SELECT kind, dedupe_key, chosen, evidence_json FROM decisions WHERE kind='name_admit' "
                                 "OR (kind='refine_name_add' AND chosen='add') ORDER BY decision_id"):
        if r["kind"] == "name_admit":
            out.append((r["dedupe_key"].split(":", 1)[1], r["chosen"]))
        else:
            ev = json.loads(r["evidence_json"] or "{}")
            out.append((r["dedupe_key"].split(":", 1)[1], ev.get("block")))
    return out


def admitted_names(project: Project) -> list[str]:
    """The names admitted into a block (and not undone), oldest first."""
    return list(dict.fromkeys(k for k, _ in _admitted(project)))


def _setup(project: Project, version: int | None):
    from ..construct import artifacts
    from ..search.diagnostics import _positive_blocks, shown_seeds
    from ..search.evaluate import NoProfile, Query, _abstracts, load_profile, profiled_platforms, seed_doc

    row, data = artifacts.load(project, "synonyms", version)
    if row is None:
        raise ValueError(f"no synonyms version {version}" if version is not None else
                         "no synonyms version yet: propose or save one on the Construct workbench first")
    query = Query.model_validate(data)
    seeds, _shown = shown_seeds(project)
    ab = _abstracts(project)
    docs = [seed_doc(b, "Development", ab.get(b.benchmark_id)) for s, b in seeds if s == "Development"]
    chosen = [p.key for p in project.config.platforms]
    profiles = []
    for k in [k for k in chosen if k in profiled_platforms()] or profiled_platforms():
        try:
            profiles.append(load_profile(k))
        except NoProfile:
            continue
    return row, data, query, _positive_blocks(query), docs, profiles


def gains(project: Project, name: str, block: str | None = None, version: int | None = None) -> dict[str, Any]:
    """What adding ``name`` to a block does in the dry run: ``{block, blocks: {key: [{seed, platform}]}}``, the
    development seeds each positive block would turn from miss to hit (validation seeds never drive it, D9).
    ``block`` is the one asked for, else the first block with a gain, else the first positive block."""
    from ..search.diagnostics import _norm_term, edited_data
    from ..search.evaluate import Query, evaluate, term_label

    _row, data, query, positive, docs, profiles = _setup(project, version)
    if block is not None and block not in {b.key for b in query.blocks}:
        raise KeyError(f"no block {block} in synonyms v{_row['version']} (blocks: "
                       f"{', '.join(b.key for b in query.blocks)})")
    term = _term(name)
    out: dict[str, list[dict[str, str]]] = {}
    held: dict[str, bool] = {}
    for k in ([block] if block else positive):
        blk = next(b for b in query.blocks if b.key == k)
        held[k] = _norm_term(term) in {_norm_term(term_label(t)) for t in blk.terms}
        edited = Query.model_validate(edited_data(data, {"op": "add_term", "block": k, "term": term}))
        got = []
        for pr in profiles:
            for d in docs:
                if evaluate(query, d, pr).state == "miss" and evaluate(edited, d, pr).state == "hit":
                    got.append({"seed": d.benchmark_id, "platform": pr.platform})
        out[k] = got
    pick = block or next((k for k in positive if out.get(k)), positive[0] if positive else None)
    return {"version": _row["version"], "block": pick, "blocks": out, "held": held,
            "positive": positive, "platforms": [pr.platform for pr in profiles]}


def _seeds_text(got: list[dict[str, str]]) -> str:
    return ", ".join(sorted({g["seed"] for g in got}))


def _evidence(project: Project, name: str) -> list[dict[str, Any]]:
    """The mined sources of a name as references (seed, section, the quote's character offset in the section's text
    with its white space collapsed, pattern, the quote's sha256), never the quote. The offset is None when the quote
    is not in an extracted section (a keywords field)."""
    from . import mine, pdf

    key = mine.normalise(name)
    texts: dict[str, dict[str, str]] = {}

    def offset(seed: str, section: str, quote: str) -> int | None:
        if seed not in texts:
            t = pdf.load_text(project, seed) or {"sections": []}
            texts[seed] = {x["name"]: re.sub(r"\s+", " ", x["text"]) for x in t["sections"]}
        i = texts[seed].get(section, "").find(re.sub(r"\s+", " ", quote))
        return i if i >= 0 else None

    for c in mine.candidates(project):
        if c["key"] == key:
            return [{"seed": s["seed"], "section": s["section"], "offset": offset(s["seed"], s["section"], s["quote"]),
                     "pattern": s["pattern"], "quote_sha256": hashlib.sha256(s["quote"].encode("utf-8")).hexdigest()}
                    for s in c["sources"]]
    return []


def admit(project: Project, name: str, block: str | None = None, note: str | None = None, by: str | None = None,
          version: int | None = None, confirm_revision: bool = False) -> dict[str, Any]:
    """Add ``name`` to a block of the newest synonyms version (or ``version``) as a new draft version, origin
    ``admit``. Without a gain in the dry run the researcher's ``note`` is required (``ValueError`` naming
    :data:`NOT_ADMITTED` otherwise). Returns the new version, its note, the gain and, when the save reopened a
    frozen query, ``notice`` (the unlock line)."""
    from ..core.questions import _LOCK

    with _LOCK:   # a double click admits once: the second call finds the name already a term (ui-tester F1)
        return _admit(project, name, block, note, by, version, confirm_revision)


def _admit(project: Project, name: str, block: str | None, note: str | None, by: str | None,
           version: int | None, confirm_revision: bool = False) -> dict[str, Any]:
    from ..construct import artifacts
    from . import mine

    name = " ".join((name or "").split())
    if not name:
        raise ValueError("no name given")
    note = (note or "").strip() or None
    g = gains(project, name, block, version)
    k = g["block"]
    if k is None:
        raise ValueError("the query has no block a name can join")
    if g["held"].get(k):
        raise ValueError(f'"{name}" is already a term of block {k}')
    got = g["blocks"].get(k, [])
    if not got and not note:
        raise ValueError(f'"{name}" {NOT_ADMITTED} in block {k} on {", ".join(g["platforms"]) or "no profiled platform"}'
                         f" (accept it with a note to add it anyway)")
    author = journal.current_author(by)
    why = (f"retrieves {_seeds_text(got)}" if got
           else f"accepted by {author}: {note}" if author else f"accepted with a note: {note}")  # no name: none shown
    if got and note:
        why += f"; note: {note}"
    text = f"admit {name} to block {k}: {why}"
    _row, data = artifacts.load(project, "synonyms", g["version"])
    from ..search.diagnostics import edited_data
    new_data = edited_data(data, {"op": "add_term", "block": k, "term": _term(name)})
    newest = max(r["version"] for r in artifacts.list_versions(project, "synonyms"))
    key = f"name_admit:{mine.normalise(name)}"
    unlock_before = project.store.scalar("SELECT MAX(change_id) FROM history WHERE kind='artifact_unlock'")
    with journal.recording(project, "name_admit", f'name "{name}" admitted to block {k} (synonyms v{g["version"]})',
                           author) as rec:
        rec.rows("artifacts", "kind='synonyms' AND version>?", [newest])
        rec.rows("query_rounds", "round_no>?", [newest])
        rec.rows("decisions", "kind='name_admit' AND dedupe_key=?", [key])
        stale = _stale_questions(project, name)   # an open "add this name?" would ask what is now done
        if stale:
            rec.rows("questions", f"question_id IN ({','.join('?' * len(stale))})", stale)
        row = artifacts.save_new_version(project, "synonyms", new_data, origin="admit", note=text,
                                         parent_version=g["version"], by=author, confirm_revision=confirm_revision)
        if row.get("notice"):
            cid = project.store.scalar("SELECT MAX(change_id) FROM history WHERE kind='artifact_unlock'")
            if cid != unlock_before:
                rec.extra({"t": "unlocked", "change_id": cid})
        with project.store.tx() as c:
            c.execute('INSERT INTO decisions(kind, dedupe_key, evidence_json, options_json, "default", chosen, mode, '
                      "consequence, author, at, round_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("name_admit", key, json.dumps({"name": name, "gains": got, "note": note,
                                                      "sources": _evidence(project, name)}, ensure_ascii=False),
                       json.dumps(g["positive"]), None, k, "chosen", why, author, utcnow(), row["version"]))
            for qid in stale:
                c.execute("UPDATE questions SET status='dismissed', answered_by=?, answered_at=?, applied=? "
                          "WHERE question_id=?", (author, utcnow(), f"closed: {name} admitted to block {k}", qid))
    if stale:   # like an answer or a dismissal: a question waiting for the budget opens (spec-checker F-R2-4)
        from ..interview import bank
        bank.after_answer(project)
    return {"name": name, "block": k, "from_version": g["version"], "version": row["version"], "note": row["note"],
            "gains": got, "notice": row.get("notice"), "change_id": rec.change_id, "closed_questions": stale}


def _stale_questions(project: Project, name: str) -> list[int]:
    """The open or waiting name-add questions about ``name``."""
    from . import mine

    want = mine.normalise(name)
    return [int(r["question_id"]) for r in project.store.query(
        "SELECT question_id, context FROM questions WHERE kind='refine_name_add' AND status IN ('open','waiting')")
        if mine.normalise(((json.loads(r["context"] or "{}").get("evidence") or {}).get("name")) or "") == want]


def listing(project: Project, version: int | None = None) -> dict[str, Any]:
    """The newest mining run's candidates with their admission state: ``admitted`` (block), else the gain the dry
    run shows (``retrieves B002``), ``already a term of block a`` or :data:`NOT_ADMITTED`. Without a synonyms
    version every name is listed with ``gain: None`` and a ``notice``."""
    from . import mine

    admitted = dict(_admitted(project))
    rows, notice, blocks = [], None, []
    cands = mine.candidates(project)
    try:
        _setup(project, version)
    except ValueError as e:
        notice = str(e)
    for c in cands:
        r = {"name": c["name"], "key": c["key"], "pattern": c["pattern"], "count": c["count"],
             "same_as": c["same_as"], "admitted": admitted.get(c["key"]), "block": None, "gains": [], "held": [],
             "status": None}
        if r["admitted"]:
            r["status"] = f"admitted to block {r['admitted']}"
        elif notice is None:
            g = gains(project, c["name"], None, version)
            blocks = g["positive"]
            r["block"], r["gains"] = g["block"], g["blocks"].get(g["block"], [])
            r["held"] = [k for k, v in g["held"].items() if v]
            if r["gains"]:
                r["status"] = f"retrieves {_seeds_text(r['gains'])}"
            elif r["held"]:
                r["status"] = f"already a term of block {', '.join(r['held'])}"
            else:
                r["status"] = NOT_ADMITTED
        rows.append(r)
    return {"candidates": rows, "blocks": blocks, "notice": notice}


# ------------------------------------------------------------------------------------------------- name map
def _answers(project: Project) -> list[tuple[str, str, str]]:
    """``(name, other, choice)`` from ``refine_same_construct`` decisions (``same`` | ``different``)."""
    out = []
    for r in project.store.query("SELECT evidence_json, chosen FROM decisions WHERE kind='refine_same_construct'"):
        ev = json.loads(r["evidence_json"] or "{}")
        names = ev.get("names") or []
        if len(names) == 2 and r["chosen"] in ("same", "different"):
            out.append((names[0], names[1], r["chosen"]))
    return out


def name_map(project: Project) -> dict[str, Any]:
    """``{groups: [[name, ...]], look_alikes: [{name, of}]}``: names joined by "also called" evidence or a "same
    construct" answer form one group (union of pairs); a "different construct" answer keeps the pair apart and flags
    the mined name as a look-alike of the name the seed paper introduced it with. Names are the normalised keys, sorted."""
    from . import mine

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    answers = [(mine.normalise(a), mine.normalise(b), choice) for a, b, choice in _answers(project)]
    apart = {frozenset((a, b)) for a, b, choice in answers if choice == "different"}   # the researcher's answer wins
    for c in mine.candidates(project):
        find(c["key"])
        for s in c["same_as"]:
            if frozenset((c["key"], mine.normalise(s))) not in apart:
                union(c["key"], mine.normalise(s))
    looks = []
    for a, b, choice in answers:
        if choice == "same":
            union(a, b)
        else:
            find(a), find(b)
            looks.append((a, b))
    groups: dict[str, list[str]] = {}
    for x in parent:
        groups.setdefault(find(x), []).append(x)
    look_names = {a for a, _ in looks}
    out_groups = sorted((sorted(g) for g in groups.values() if len(g) > 1 or not (set(g) & look_names)),
                        key=lambda g: (-len(g), g))
    out_groups = [g for g in out_groups if not (len(g) == 1 and g[0] in look_names)]
    return {"groups": out_groups, "look_alikes": [{"name": a, "of": b} for a, b in sorted(looks)]}


def map_lines(m: dict[str, Any]) -> list[str]:
    lines = [f"same construct: {', '.join(g)}" for g in m["groups"] if len(g) > 1]
    lines += [f"look-alike: {x['name']} (differs from {x['of']})" for x in m["look_alikes"]]
    return lines or ["no names grouped yet (wider seeds mine finds \"also called\" pairs)"]


def write_map(project: Project) -> dict[str, Any]:
    """Write ``name_map.json`` and ``name_map.svg`` into the newest mining run folder (or ``runs/seeds/mine/``)."""
    from ..search import plots
    from . import mine

    m = name_map(project)
    run = mine.latest_run(project) or mine.mine_dir(project)
    run.mkdir(parents=True, exist_ok=True)
    (run / "name_map.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
    svg = plots.name_map(m)
    (run / "name_map.svg").write_text(svg, encoding="utf-8")
    rel = str(run.relative_to(project.root))
    return {**m, "files": [f"{rel}/name_map.json", f"{rel}/name_map.svg"], "svg": svg}
