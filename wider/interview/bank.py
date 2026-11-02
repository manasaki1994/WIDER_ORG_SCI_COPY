"""The question bank of the refinement interview (vision change 2b, D12): ten ``refine_*`` kinds in the questions
store, raised only when their evidence exists after a dry run, a mining run, a diagnosis or an import.

:func:`trigger` reads the newest synonyms version's evidence (the candidate names and their dry-run gains, 2a's
suggested edits with their computed consequences, the diagnosis of the imported records, the indexing verdicts and
the record budget), builds one dedupe key per question from the thing it is about, and raises each question with 3
to 5 options, a default and each option's consequence in numbers. A key that is open, waiting or already in the
decision ledger (answered, accepted or dismissed) is never asked again (F12).

Budgets: at most :data:`OPEN_BUDGET` refinement questions are open at once and at most :data:`BLOCK_BUDGET` are
asked about one construct block in one interview round; the others wait (status ``waiting``, listed as "waiting for
the budget") and :func:`release` opens them, oldest first, as answers and dismissals free the budget.

The stop rule (:func:`after_answer`): when the last three answers since the round began moved development recall
and the records by less than 2% each, one ``refine_round_stop`` question is raised and nothing else is asked until
the next dry run or import (meta ``refine_paused``). Nothing here sends anything anywhere.
"""
from __future__ import annotations

import json
import math
from typing import Any

from ..core import questions
from ..core.project import Project

KINDS = questions.REFINE_KINDS   # registered in questions.KINDS (stage ``query``; the options come with each question)
OPEN_BUDGET = 5        # refinement questions open at once
BLOCK_BUDGET = 10      # asked about one construct block in one interview round
STOP_MOVE = 0.02       # the stop rule: recall and records each moved by less than this
STOP_ANSWERS = 3       # over this many answers in a row
BOUNDARY_MIN = 5       # a cluster this large, with no seed and no mark, is a boundary-record question
ROUND_KEY, PAUSE_KEY = "refine_round", "refine_paused"
CLEARING_EVENTS = ("dry_run", "import")   # a new dry run or import ends "stop asking for now"
WAITING = "waiting for the budget"


def interview_round(project: Project) -> int:
    return int(project.store.get_meta(ROUND_KEY) or 1)


def paused(project: Project) -> bool:
    return project.store.get_meta(PAUSE_KEY) == "1"


def _opt(value: str, label: str, consequence: str, recall: float = 0.0, records: float | None = 0.0,
         writes: bool = False, default: bool = False) -> dict[str, Any]:
    o: dict[str, Any] = {"value": value, "label": label, "consequence": consequence,
                         "effect": {"recall": round(recall, 4), "records": None if records is None else round(records, 4)}}
    if writes:
        o["writes"] = True
    if default:
        o["default"] = True
    return o


def _item(kind: str, key: str, title: str, prompt: str, evidence: dict, options: list[dict], block: str | None,
          changes: dict | None = None, **extra) -> dict[str, Any]:
    default = next(o["value"] for o in options if o.get("default"))
    return {"kind": kind, "key": key, "title": title, "prompt": prompt, "options": options, "block": block,
            "context": {"evidence": evidence, "default": default, "changes": changes or {}, "block": block, **extra}}


# ------------------------------------------------------------------------------------------- the evidence
class _Evidence:
    """What the finders read, computed once per trigger (each part only when the project has it)."""

    def __init__(self, project: Project):
        from ..construct import artifacts
        from ..search import diagnostics
        from ..search.evaluate import evaluate
        from ..seeds import names

        self.project = project
        self.row, self.data = artifacts.load(project, "synonyms")
        self.version = self.row["version"]
        _r, _d, self.query, self.positive, self.docs, self.profiles = names._setup(project, self.version)
        self.n_dev = max(1, len(self.docs))
        self.base = {pr.platform: {d.benchmark_id: evaluate(self.query, d, pr) for d in self.docs}
                     for pr in self.profiles}
        try:
            self.names = names.listing(project, self.version)["candidates"]
        except ValueError:
            self.names = []
        try:
            self.edits = diagnostics.suggested_edits(project, self.version)
        except ValueError:
            self.edits = {"edits": [], "budget": diagnostics.budget(project, self.version), "platforms": []}
        self.budget = self.edits["budget"]
        try:
            self.diag = diagnostics.what_did_what(project, self.version) if self.budget["records"] else None
        except ValueError:
            self.diag = None

    def recall(self, deltas: dict[str, int]) -> float:
        """Seeds gained (or lost) as a share of the development seeds, over the platforms."""
        return sum(deltas.values()) / (self.n_dev * max(1, len(deltas)))

    def records(self, removed: dict[str, int]) -> float | None:
        total = self.budget["records"]
        if not total:
            return None
        return -sum(removed.values()) / total


def _edit_option(ev: _Evidence, value: str, e: dict, default: bool = False) -> dict[str, Any]:
    recs = None if e["kind"] in ("add_name", "add_field") else ev.records(e["records_removed"])
    return _opt(value, e["label"][0].upper() + e["label"][1:], e["consequence"], ev.recall(e["deltas"]), recs,
                writes=True, default=default)


def _name_add(ev: _Evidence) -> list[dict]:
    from ..seeds import names

    out = []
    for c in ev.names:
        if c["admitted"] or not c["gains"] or not c["block"]:
            continue
        got = names._seeds_text(c["gains"])
        plats = sorted({g["platform"] for g in c["gains"]})
        share = len({g["seed"] for g in c["gains"]}) / ev.n_dev
        cons = f"retrieves {got} on {', '.join(plats)}; records added: not known until the next import"
        opts = [_opt("add", f"Add {c['name']} to block {c['block']}", cons, share, None, writes=True, default=True),
                _opt("keep", "Keep the query as it is", f"{got} stays a miss, records unchanged"),
                _opt("reject", "Not a name for this construct", f"{got} stays a miss; the name is not asked again")]
        evidence = {"subject": f"name {c['name']}", "name": c["name"], "block": c["block"],
                    "why": f"mined from the seed papers; {got}",
                    "gains": c["gains"], "refs": names._evidence(ev.project, c["name"])}
        out.append(_item("refine_name_add", c["key"], f'Add "{c["name"]}" to block {c["block"]}?',
                         f'The seed papers name "{c["name"]}"; added to block {c["block"]} it {cons}.', evidence,
                         opts, c["block"], {"add": {"op": "add_term", "block": c["block"],
                                                    "term": names._term(c["name"])}}, name=c["name"]))
    return out


def _same_construct(ev: _Evidence) -> list[dict]:
    from ..seeds import mine, names

    out, seen = [], set()
    for c in ev.names:
        for other in c["same_as"]:
            a, b = c["key"], mine.normalise(other)   # a: the mined name, b: the name it was introduced with
            if a == b or tuple(sorted((a, b))) in seen:
                continue
            seen.add(tuple(sorted((a, b))))
            opts = [_opt("same", "The same construct", f"{a} and {b} form one group of the name map"),
                    _opt("different", "Different constructs", f"{a} is flagged as a look-alike of {b}"),
                    _opt("leave", "Leave it", "the name map is unchanged", default=True)]
            out.append(_item("refine_same_construct", "|".join(sorted((a, b))), f'Are "{a}" and "{b}" the same construct?',
                             f'A seed paper introduces "{a}" and "{b}" as the same thing ("also called").',
                             {"subject": f"names {a} and {b}", "names": [a, b], "why": "introduced as the same thing",
                              "seeds": sorted({x["seed"] for x in names._evidence(ev.project, c["name"])})},
                             opts, None))
    return out


def _keep_leave(what: str) -> list[dict]:
    return [_opt("keep", "Keep it as it is", f"{what} unchanged"),
            _opt("leave", "Leave it for now", "nothing changes; the question is not asked again")]


def _noise_term(ev: _Evidence) -> list[dict]:
    out = []
    for e in ev.edits["edits"]:
        if e["kind"] != "drop_term":
            continue
        ch = e["change"]
        opts = [_edit_option(ev, "drop", e, default=True)] + _keep_leave(f"{ch['term']} stays in block {ch['block']}, records")
        opts[0]["label"] = f"Drop {ch['term']} from block {ch['block']}"
        out.append(_item("refine_noise_term", f"{ch['block']}:{ch['term']}",
                         f"Drop the noise term {ch['term']} from block {ch['block']}?",
                         f"{e['source']}; none of them holds a term a seed matches. Dropping it: {e['consequence']}.",
                         {"subject": f"term {ch['term']} in block {ch['block']}", "term": ch["term"],
                          "why": e["source"]}, opts, ch["block"], {"drop": ch}))
    return out


def _variant(ev: _Evidence) -> list[dict]:
    out = []
    for e in ev.edits["edits"]:
        ch = e["change"]
        lost = any(v["lost"] for v in e["seeds"].values())
        gained = any(v["gained"] for v in e["seeds"].values())
        if e["kind"] != "change_stem" or lost or not (gained or sum(e["records_removed"].values())):
            continue
        opts = [_edit_option(ev, "change", e, default=True)] + _keep_leave(f"{ch['term']} in block {ch['block']}")
        out.append(_item("refine_variant", f"{ch['block']}:{ch['term']}:{ch['old']}:{ch['new']}",
                         f"Change {ch['old']} to {ch['new']} in {ch['block']}: {ch['term']}?",
                         f"The truncation report ({e['source'].split(': ', 1)[-1]}): {e['consequence']}.",
                         {"subject": f"stem {ch['old']} in {ch['block']}: {ch['term']}", "term": ch["term"],
                          "why": e["source"]}, opts, ch["block"], {"change": ch}))
    return out


def _context_and(ev: _Evidence) -> list[dict]:
    out = []
    for e in ev.edits["edits"]:
        ch = e["change"]
        if e["kind"] != "add_name" or len(ev.positive) < 2 or ch["block"] == ev.positive[0]:
            continue
        opts = [_edit_option(ev, "add", e, default=True)] + _keep_leave(f"block {ch['block']}")
        out.append(_item("refine_context_and", f"{ch['block']}:{ch['term']}",
                         f"Add {ch['term']} to the context block {ch['block']}?",
                         f"A missed seed fails block {ch['block']}, which is joined by AND ({e['source']}): "
                         f"{e['consequence']}.",
                         {"subject": f"term {ch['term']} for block {ch['block']}", "term": ch["term"],
                          "why": e["source"]}, opts, ch["block"], {"add": ch}))
    return out


def _seed_not_retrieved(ev: _Evidence) -> list[dict]:
    from ..search import indexing

    held = indexing.verdicts(ev.project)
    out = []
    for plat, res in ev.base.items():
        for bid, r in res.items():
            if r.state != "miss" or (bid, plat) in held:
                continue
            adds = [e for e in ev.edits["edits"] if e["kind"] == "add_name" and bid in e["seeds"].get(plat, {}).get("gained", [])][:2]
            opts = [_edit_option(ev, f"add{i}", e, default=i == 1) for i, e in enumerate(adds, 1)]
            opts += [_opt("not_indexed", f"{bid} is not in {plat}", f"recorded as not indexed; recall on {plat} "
                          "is read against the seeds it holds"),
                     _opt("indexed", f"{bid} is in {plat} but not retrieved", "recorded as indexed but missed"),
                     _opt("leave", "Leave it for now", f"{bid} stays a miss on {plat}", default=not adds)]
            block = adds[0]["change"]["block"] if adds else (r.failed_blocks[0] if r.failed_blocks else None)
            out.append(_item("refine_seed_not_retrieved", f"{bid}:{plat}", f"{bid} is not retrieved on {plat}",
                             f"The dry run of synonyms v{ev.version} misses {bid} on {plat} (fails block "
                             f"{', '.join(r.failed_blocks) or '-'})." + (f" Best edit: {adds[0]['label']}, "
                                                                        f"{adds[0]['consequence']}." if adds else ""),
                             {"subject": f"seed {bid} on {plat}", "seed": bid, "platform": plat,
                              "why": f"a miss in the dry run of v{ev.version}"}, opts, block,
                             {f"add{i}": e["change"] for i, e in enumerate(adds, 1)}, seed=bid, platform=plat))
    return out


def _boundary_record(ev: _Evidence) -> list[dict]:
    from ..search.diagnostics import NO_TERM

    out = []
    for plat, pl in (ev.diag or {}).get("platforms", {}).items():
        if not pl.get("profile"):
            continue
        noise = {f"{x['block']}: {x['term']}" for x in pl["noise"]}
        for c in pl["clusters"]:
            if c["mark"] or c["seeds"] or c["size"] < BOUNDARY_MIN or c["label"] == NO_TERM:
                continue
            if not set(c["terms"]) - noise:
                continue
            share = c["size"] / max(1, pl["records"])
            size = c.get("text") or f"{c['size']} records"   # "about 52,000 records (100 in the sample)" (D6)
            opts = [_opt("relevant", "Relevant records", f"its terms ({c['label']}) are not reported as noise"),
                    _opt("noise", "Noise", f"{size} ({100 * share:.1f}%) count as noise in the next "
                         "diagnosis; no screening decision is made"),
                    _opt("leave", "Leave it", "no mark", default=True)]
            out.append(_item("refine_boundary_record", f"{plat}:v{ev.diag['version']}:{c['signature']}",
                             f"{size} on {plat} with {c['label']}: relevant?",
                             f"Cluster {c['id']} ({c['label']}) holds {size} and no seed; samples: "
                             + "; ".join(c["samples"]),
                             {"subject": f"cluster {c['label']} on {plat}", "why": f"{size}, no seed"},
                             opts, None, platform=plat, signature=c["signature"], diag_version=ev.diag["version"]))
    return out


def _limit(ev: _Evidence) -> list[dict]:
    if not ev.budget["over"]:
        return []
    years = [b.year for b in ev.project.read_benchmark() if b.year and b.set == "Development"]
    if not years:
        return []
    y = min(years)
    out = []
    from ..search.diagnostics import about, sample_weights
    sw = sample_weights(ev.project, ev.version) if ev.budget.get("estimated") else {}
    for p in ev.project.config.platforms:
        if (p.limits or {}).get("dates") or p.key not in ev.budget["per_platform"]:
            continue

        wts = (sw.get(p.key) or {}).get("weights", {}) if (sw.get(p.key) or {}).get("sampled") else {}

        def older(year: int, plat: str = p.key) -> float:
            # each file's count times its weight: a random sample's records stand for N/K records each (D7)
            rows = ev.project.store.query(
                "SELECT s.source_id, COUNT(*) AS n FROM raw_records r JOIN sources s ON s.source_id=r.source_id "
                "WHERE s.platform=? AND s.query_version=? AND s.included=1 AND r.year IS NOT NULL AND r.year<? "
                "GROUP BY s.source_id", (plat, ev.version, year))
            return sum(int(r["n"]) * wts.get(r["source_id"], 1.0) for r in rows)
        n0, n5 = older(y), older(y - 5)
        if not n0:
            continue
        total = ev.budget["records"]

        def shown(x: float) -> str:
            return f"about {about(x)}" if wts else str(int(x))
        opts = [_opt("from_seed", f"Limit {p.key} to {y}..", f"-{shown(n0)} records on {p.key} (published before "
                     f"{y}); seeds unchanged", 0.0, -n0 / total),
                _opt("from_seed_5", f"Limit {p.key} to {y - 5}..", f"-{shown(n5)} records on {p.key} (published "
                     f"before {y - 5}); seeds unchanged", 0.0, -n5 / total),
                _opt("keep", "Keep no date limit", f"{ev.budget['text']}", default=True)]
        out.append(_item("refine_limit", p.key, f"Limit {p.key} by date?",
                         f"The imported records are over the budget ({ev.budget['text']}); {shown(n0)} on {p.key} are older "
                         f"than the earliest development seed ({y}).",
                         {"subject": f"date limit on {p.key}", "why": ev.budget["text"]}, opts, None,
                         platform=p.key, years={"from_seed": y, "from_seed_5": y - 5}))
    return out


def _screening_budget(ev: _Evidence) -> list[dict]:
    b = ev.budget
    if not b["over"]:
        return []
    up = int(math.ceil(b["records"] / 500.0) * 500)
    if b.get("estimated"):   # an estimate from a random sample says so (D7); raising is still offered
        from ..search.diagnostics import about
        opts = [_opt("raise", f"Raise the budget to about {about(up)}",
                     f"about {about(b['records'])} records (estimate) are within a budget of {up}"),
                _opt("keep", f"Keep the budget of {b['budget']}",
                     f"about {about(b['over'])} records over (estimate); narrow the query", default=True),
                _opt("leave", "Leave it for now", "nothing changes")]
        title = f"about {about(b['records'])} records: over the budget of {b['budget']} (estimate from a random sample)"
    else:
        opts = [_opt("raise", f"Raise the budget to {up}", f"{b['records']} records are within a budget of {up}"),
                _opt("keep", f"Keep the budget of {b['budget']}", f"{b['over']} records over; narrow the query",
                     default=True),
                _opt("leave", "Leave it for now", "nothing changes")]
        title = f"{b['records']} records: over the budget of {b['budget']}"
    return [_item("refine_screening_budget", f"v{ev.version}", title,
                  f"Synonyms v{ev.version}: {b['text']}.", {"subject": f"record budget at v{ev.version}",
                                                              "why": b["text"]}, opts, None, raise_to=up)]


FINDERS = (_name_add, _same_construct, _noise_term, _variant, _context_and, _seed_not_retrieved, _boundary_record,
           _limit, _screening_budget)


# ------------------------------------------------------------------------------------------- raising
def _known(project: Project, key: str) -> bool:
    st = project.store
    return bool(st.scalar("SELECT COUNT(*) FROM questions WHERE dedupe_key=? AND status IN ('open','waiting')", (key,))
                or st.scalar("SELECT COUNT(*) FROM decisions WHERE dedupe_key=?", (key,)))


def _open_count(project: Project) -> int:
    marks = ",".join("?" * len(KINDS))
    return int(project.store.scalar(f"SELECT COUNT(*) FROM questions WHERE status='open' AND kind IN ({marks})", KINDS))


def _asked_for_block(project: Project, block: str, rnd: int) -> int:
    marks = ",".join("?" * len(KINDS))
    n = 0
    for r in project.store.query(f"SELECT context FROM questions WHERE status!='waiting' AND kind IN ({marks})", KINDS):
        ctx = json.loads(r["context"] or "{}")
        n += ctx.get("block") == block and ctx.get("interview_round") == rnd
    return n


def _fits(project: Project, block: str | None, rnd: int) -> bool:
    return _open_count(project) < OPEN_BUDGET and (block is None or _asked_for_block(project, block, rnd) < BLOCK_BUDGET)


def _raise(project: Project, it: dict, rnd: int, version: int) -> tuple[int | None, bool]:
    """Raise one question, open when the budgets allow, else waiting; ``(id, opened)``."""
    ctx = {**it["context"], "version": version, "interview_round": rnd}
    opened = _fits(project, it["block"], rnd)
    qid = questions.raise_question(project, it["kind"], it["key"], it["title"], it["prompt"], ctx,
                                   ref=ctx.get("seed") or ctx.get("name"), extra_options=it["options"])
    if qid is not None and not opened:
        project.store.execute("UPDATE questions SET status='waiting' WHERE question_id=?", (qid,))
    return qid, opened


def _held_reason(project: Project, kind: str, ctx: dict, held: set[str]) -> str | None:
    """Why a question rests only on seeds that are no longer development seeds (``None``: it does not). Answering
    it would tune the query to a validation paper (blinding; risk-checker R1)."""
    if not held:
        return None
    ev = ctx.get("evidence") or {}
    if kind == "refine_same_construct":   # the seeds that introduced the pair, kept when it was asked (not the
        seeds = set(ev.get("seeds") or [])  # newest run: a run on one seed holds no other seed's pairs; round 4 N1)
    elif kind == "refine_seed_not_retrieved":
        seeds = {ctx.get("seed") or ev.get("seed")} - {None}
    elif kind == "refine_name_add":
        found = {r["seed"] for r in ev.get("refs") or []}
        gains = {g["seed"] for g in ev.get("gains") or []}
        seeds = found if found and found <= held else gains
    else:
        return None
    if not seeds or not seeds <= held:
        return None
    return f"closed: its evidence is now only validation seed(s) {', '.join(sorted(seeds))}"


def close_stale(project: Project, rec=None, only: int | None = None, held_only: bool = False) -> list[int]:
    """Close the open or waiting questions that no longer hold: asked about an older synonyms version whose every
    edit the newest version already holds or no longer takes (an "add X" where X is now a term; ui-tester F1), or
    resting only on seeds moved out of Development (risk-checker R1). Status ``dismissed`` with the reason in
    ``applied``, no ledger row, so the key may be asked again from the newest evidence. ``rec``: the journal
    recording of the answer that caused it, so undoing the answer reopens them (risk-checker R2); ``only``: check
    just this question; ``held_only``: only the moved-seed check (an answer about an older version still runs and
    says what it saves)."""
    from ..construct import artifacts
    from ..core.provenance import utcnow
    from . import apply

    if "questions" not in project.store.tables():
        return []
    row, data = artifacts.load(project, "synonyms")
    held = {b.benchmark_id for b in project.read_benchmark() if b.set != "Development"}
    closed = []
    for r in project.store.query("SELECT question_id, kind, context FROM questions WHERE status IN ('open','waiting') "
                                 f"AND kind IN ({','.join('?' * len(KINDS))})", KINDS):
        if only is not None and r["question_id"] != only:
            continue
        ctx = json.loads(r["context"] or "{}")
        why = _held_reason(project, r["kind"], ctx, held)
        changes = list((ctx.get("changes") or {}).values())
        if why is None and not held_only and row is not None and changes and ctx.get("version") not in (None, row["version"]):
            live = 0
            for ch in changes:
                try:
                    apply._check(data, ch)
                    live += 1
                except ValueError:
                    pass
            if not live:
                why = f"closed: synonyms v{row['version']} already holds this edit (asked about v{ctx['version']})"
        if why is None:
            continue
        if rec is not None:
            rec.rows("questions", "question_id=?", (r["question_id"],))
        project.store.execute("UPDATE questions SET status='dismissed', answered_at=?, applied=? WHERE question_id=?",
                              (utcnow(), why, r["question_id"]))
        closed.append(int(r["question_id"]))
    return closed


def trigger(project: Project, event: str | None = None) -> dict[str, Any]:
    """Raise the refinement questions the evidence calls for; ``{raised: [ids], waiting: [ids], paused, notice}``.
    ``event`` ``dry_run`` or ``import`` ends a pause set by "stop asking for now"."""
    out: dict[str, Any] = {"raised": [], "waiting": [], "paused": False, "notice": None}
    st = project.store
    if "decisions" not in st.tables() or not st.scalar("SELECT COUNT(*) FROM artifacts WHERE kind='synonyms'"):
        return out
    close_stale(project)
    if event in CLEARING_EVENTS and paused(project):
        st.set_meta(PAUSE_KEY, "0")
    if paused(project):
        out["paused"] = True
        out["notice"] = "refinement questions paused (\"stop asking for now\") until the next dry run or import"
        return out
    if not project.read_benchmark():
        return out
    try:
        ev = _Evidence(project)
    except ValueError as e:   # no profile, no synonyms version: nothing to ask about
        out["notice"] = str(e)
        return out
    rnd = interview_round(project)
    for finder in FINDERS:
        for it in finder(ev):
            if not 3 <= len(it["options"]) <= 5 or _known(project, f"{it['kind']}:{it['key']}"):
                continue
            qid, opened = _raise(project, it, rnd, ev.version)
            if qid is not None:
                out["raised" if opened else "waiting"].append(qid)
    return out


def safe_trigger(project: Project, event: str | None = None) -> dict[str, Any]:
    """:func:`trigger` for the hooks (dry run, mining, diagnosis, import): a failure becomes a notice, never a
    broken run."""
    try:
        return trigger(project, event)
    except Exception as e:  # noqa: BLE001 - the interview never breaks the step that called it
        return {"raised": [], "waiting": [], "paused": False, "notice": f"refinement questions not raised: {e}"}


def notice(res: dict[str, Any]) -> str | None:
    """One line for the step that triggered: how many questions were raised and wait, or why none were."""
    n, w = len(res.get("raised") or []), len(res.get("waiting") or [])
    if n or w:
        return (f"{n} refinement question{'s' if n != 1 else ''} raised" + (f", {w} {WAITING}" if w else "")
                + " (the Questions page, `wider questions list`)")
    return res.get("notice") if (res.get("notice") or "").startswith("refinement questions not raised") else None


def release(project: Project) -> list[int]:
    """Open waiting questions, oldest first, while the budgets allow; returns their ids."""
    opened = []
    marks = ",".join("?" * len(KINDS))
    rnd = interview_round(project)
    for r in project.store.query(f"SELECT question_id, context FROM questions WHERE status='waiting' AND kind IN "
                                 f"({marks}) ORDER BY question_id", KINDS):
        ctx = json.loads(r["context"] or "{}")
        if _open_count(project) >= OPEN_BUDGET:
            break
        if not _fits(project, ctx.get("block"), rnd):
            continue
        ctx["interview_round"] = rnd
        project.store.execute("UPDATE questions SET status='open', context=? WHERE question_id=?",
                              (json.dumps(ctx, default=str), r["question_id"]))
        opened.append(r["question_id"])
    return opened


def waiting(project: Project) -> int:
    return int(project.store.scalar("SELECT COUNT(*) FROM questions WHERE status='waiting'"))


# ------------------------------------------------------------------------------------------- the stop rule
def _moves(project: Project) -> list[dict]:
    """The effects of the answers given since the newest round-stop decision, oldest first."""
    rs = project.store.query("SELECT decision_id, kind, chosen, mode, options_json FROM decisions WHERE kind LIKE "
                             "'refine_%' ORDER BY decision_id")
    out = []
    for r in rs:
        if r["kind"] == "refine_round_stop":
            out = []
            continue
        if r["mode"] == "dismissed":
            continue
        opt = next((o for o in json.loads(r["options_json"] or "[]") if o.get("value") == r["chosen"]), {})
        out.append(opt.get("effect") or {})
    return out


def stop_due(project: Project) -> bool:
    moves = _moves(project)[-STOP_ANSWERS:]
    return len(moves) == STOP_ANSWERS and all(abs(m.get("recall") or 0) < STOP_MOVE and abs(m.get("records") or 0)
                                              < STOP_MOVE for m in moves)


def _round_stop(project: Project) -> int | None:
    rnd = interview_round(project)
    key = f"refine_round_stop:round{rnd}"
    if _known(project, key):
        return None
    opts = [_opt("freeze", "Freeze this round", "opens the Freeze step; the freeze is its own action"),
            _opt("more", "One more round", "the interview asks again from the current evidence", default=True),
            _opt("stop", "Stop asking for now", "no refinement question until the next dry run or import")]
    why = f"the last {STOP_ANSWERS} answers moved recall and records by less than {int(STOP_MOVE * 100)}% each"
    item = _item("refine_round_stop", f"round{rnd}", f"Round {rnd} has settled: freeze it?",
                 f"In interview round {rnd}, {why}.", {"subject": f"interview round {rnd}", "why": why}, opts, None)
    from ..construct import artifacts
    row, _ = artifacts.load(project, "synonyms")
    qid, _opened = _raise(project, {**item, "block": None}, rnd, row["version"] if row else None)
    if qid is not None:   # it may wait for the budget; it is answered like any other question
        project.store.execute("UPDATE questions SET status='open' WHERE question_id=?", (qid,))
        project.store.set_meta(PAUSE_KEY, "1")
    return qid


def after_answer(project: Project) -> dict[str, Any]:
    """After an answer or a dismissal: raise the round-stop question when due, else open waiting questions."""
    close_stale(project)   # an answer that saved a version may have done what another open question asks
    qid = _round_stop(project) if stop_due(project) else None
    return {"round_stop": qid, "opened": [] if paused(project) else release(project)}
