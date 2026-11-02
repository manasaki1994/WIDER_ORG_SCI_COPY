"""Questions the tool raises when it is in doubt, and how the researcher's answers are used.

Engines call :func:`raise_question` at the points where they would otherwise guess or silently skip:

* ``screen_failed``  a screening answer that could not be parsed after the retries (the record has no decision in
  that pass). Answer include / maybe / exclude, or retry. A decision is written as that pass's read, marked as the
  researcher's, so the protocol majority uses it; retry leaves it for the next run of the pass.
* ``screen_split``   the three reads of the protocol disagree completely (include, maybe, exclude); the protocol
  records maybe. Answer include / maybe / exclude: the protocol decision becomes the researcher's. The question and
  its answer belong to one result set; when another set of the protocol with the same codebook and rules already has
  an answer for the record, the question shows it and offers ``reuse``.
* ``enrich_unresolved``  no source had an abstract for a record's DOI. Answer with a corrected DOI (the record is
  looked up again on the next enrichment run), with the abstract itself, or leave it.
* ``seed_unresolved``  a seed paper's DOI found no abstract. Answer with a corrected DOI (written to benchmark.csv),
  or leave it.
* ``proposal_failed``  the model could not produce a usable construct proposal. Answer with a clarification; every
  later proposal prompt shows it under "Clarifications from the researcher".
* ``refine_*``  the ten questions of the refinement interview (vision change 2b, :mod:`wider.interview.bank`),
  raised from the evidence of a dry run, a mining run, a diagnosis or an import, within a budget (the others wait,
  status ``waiting``). Each answer, accepted default and dismissal is a row of the decision ledger
  (:mod:`wider.interview.ledger`), written inside the same journalled change; the answer is applied on an existing
  path (:mod:`wider.interview.apply`). The decisions are the clarifications of scope ``query``.

The project chooses whether questions wait on the Questions page (``doubts: queue``) or open in a dialog as they
arrive while the run continues (``doubts: ask``). Nothing here sends anything anywhere.
"""
from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Any

from .provenance import utcnow

if TYPE_CHECKING:
    from .project import Project

DECISION_OPTIONS = [{"value": "include", "label": "Include", "consequence": "The record counts as included in this pass."},
                    {"value": "maybe", "label": "Maybe", "consequence": "The record counts as maybe in this pass."},
                    {"value": "exclude", "label": "Exclude", "consequence": "The record counts as excluded in this pass."}]
SPLIT_OPTIONS = [{"value": "include", "label": "Include", "consequence": "The protocol decision for this record becomes include, as yours."},
                 {"value": "maybe", "label": "Maybe", "consequence": "The protocol decision for this record becomes maybe, as yours."},
                 {"value": "exclude", "label": "Exclude", "consequence": "The protocol decision for this record becomes exclude, as yours."}]
# Each kind names the page its evidence is on (review-plan-and-question-window D6). The consequences are fixed text
# (D5); `default` marks the suggested answer (D8). The screening kinds mark none: a suggestion would lean a decision
# that counts as a read.
KINDS = {
    "screen_failed": {"stage": "screen", "page": "screen", "options": DECISION_OPTIONS + [
        {"value": "retry", "label": "Retry on the next run",
         "consequence": "The record has no read in this pass until the next run asks the model again."}]},
    "screen_split": {"stage": "screen", "page": "screen", "options": SPLIT_OPTIONS},
    "enrich_unresolved": {"stage": "enrich", "page": "enrich", "options": [
        {"value": "doi", "label": "Correct the DOI", "input": "doi", "default": True,
         "consequence": "The DOI is saved and the next enrichment run looks the record up again."},
        {"value": "abstract", "label": "Paste the abstract", "input": "text",
         "consequence": "Your text becomes the record's abstract, marked as yours."},
        {"value": "leave", "label": "Leave it", "consequence": "The record stays without an abstract."}]},
    "seed_unresolved": {"stage": "setup", "page": "setup?task=seeds", "options": [
        {"value": "doi", "label": "Correct the DOI", "input": "doi", "default": True,
         "consequence": "The DOI is saved in benchmark.csv; fetch the seed abstracts again to use it."},
        {"value": "leave", "label": "Leave it", "consequence": "Seed coverage is counted without this paper."}]},
    "proposal_failed": {"stage": "construct", "page": "construct", "options": [
        {"value": "clarify", "label": "Add a clarification", "input": "text", "default": True,
         "consequence": "Every later construct proposal prompt shows your clarification."},
        {"value": "leave", "label": "Leave it", "consequence": "The next proposal is made without a clarification."}]},
}
REFINE_KINDS = ("refine_name_add", "refine_same_construct", "refine_noise_term", "refine_variant", "refine_context_and",
                "refine_seed_not_retrieved", "refine_boundary_record", "refine_limit", "refine_screening_budget",
                "refine_round_stop")
KINDS.update({k: {"stage": "query", "page": "query", "options": []} for k in REFINE_KINDS})   # options come with each question
ORIGINAL_KINDS = ("screen_failed", "screen_split", "enrich_unresolved", "seed_unresolved", "proposal_failed")


def raise_question(project: Project, kind: str, dedupe_key: str, title: str, prompt: str, context: dict | None = None,
                   record_id: int | None = None, ref: str | None = None, run_ref: str | None = None,
                   extra_options: list[dict] | None = None) -> int | None:
    """Store a question unless the same one is still open; returns its id (None when it was already open)."""
    from .usage import JOB_ID

    spec = KINDS[kind]
    st = project.store
    key = f"{kind}:{dedupe_key}"
    if st.scalar("SELECT COUNT(*) FROM questions WHERE dedupe_key=? AND status='open'", (key,)):
        return None
    cur = st.execute(
        "INSERT INTO questions(kind,stage,dedupe_key,run_ref,job_id,record_id,ref,title,prompt,options,context,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (kind, spec["stage"], key, run_ref, JOB_ID.get(), record_id, ref, title[:300], prompt[:2000],
         json.dumps(spec["options"] + (extra_options or [])), json.dumps(context or {}, default=str), utcnow()))
    return int(cur.lastrowid)


def _row(r) -> dict[str, Any]:
    d = dict(r)
    for k in ("options", "context", "answer"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    spec = KINDS.get(d["kind"], {})
    if d["kind"] in ORIGINAL_KINDS and d["options"]:
        # options are written at raise time: a question stored before the consequences existed gets them by value
        fixed = {o["value"]: o for o in spec["options"]}
        d["options"] = [{**{k: v for k, v in fixed.get(o["value"], {}).items() if k in ("consequence", "default")}, **o}
                        for o in d["options"]]
    d["page"] = spec.get("page", d.get("stage"))
    d["anchor"] = d.get("ref") if d["kind"] == "seed_unresolved" else d.get("record_id")
    return d


def on_page(q: dict, page: str) -> bool:
    """A question belongs to ``page`` when it is the question's page; ``setup`` holds every ``setup?task=…`` too
    (the Review plan shows all setup questions), except a seed question about a validation seed paper: the plan names
    no validation paper, so that one is asked on the Seed papers task only."""
    if page == "setup" and q["page"].startswith("setup?"):
        return not (q["kind"] == "seed_unresolved" and (q.get("context") or {}).get("set") == "Validation")
    return q["page"] == page


def in_stage(q: dict, stage: str) -> bool:
    """A question counts for the stage its page belongs to (``setup?task=…`` for setup), whatever the page shows."""
    return q["page"].split("?", 1)[0] == stage


SCREEN_KINDS = ("screen_failed", "screen_split")


def _hidden_records(project: Project) -> set[int]:
    """Records of an incomplete development sample (ui-redesign D4): a screening question about one holds the
    model's reads, so it is left out of the list and counts and cannot be answered until the sample is complete."""
    from ..screen.devgate import hidden_ids

    return hidden_ids(project)


def _blind(project: Project, q) -> bool:
    return q["kind"] in SCREEN_KINDS and q["record_id"] is not None and q["record_id"] in _hidden_records(project)


def hidden_line(project: Project) -> str | None:
    """The sentence the Questions page and `wider questions list` show while screening questions are left out; it
    names the development records' count, never how many questions wait (blinding)."""
    from ..screen.devgate import HIDDEN

    ids = _hidden_records(project)
    if not ids:
        return None
    q = ",".join("?" * len(ids))
    k = ",".join("?" * len(SCREEN_KINDS))
    if not project.store.scalar(f"SELECT 1 FROM questions WHERE kind IN ({k}) AND record_id IN ({q}) LIMIT 1",
                                (*SCREEN_KINDS, *ids)):
        return None
    return HIDDEN.format(n=len(ids)) + "; their screening questions wait until then"


def list_questions(project: Project, status: str | None = "open", limit: int = 200,
                   page: str | None = None) -> list[dict[str, Any]]:
    hid = _hidden_records(project)
    if status:
        rows = project.store.query("SELECT * FROM questions WHERE status=? ORDER BY question_id", (status,))
    else:
        rows = project.store.query("SELECT * FROM questions ORDER BY question_id DESC")
    out = [_row(r) for r in rows if not (r["kind"] in SCREEN_KINDS and r["record_id"] in hid)]   # hidden first
    if page:
        out = [q for q in out if on_page(q, page)]
    return out[:limit]


def counts(project: Project) -> dict[str, int]:
    hid = _hidden_records(project)
    out: dict[str, int] = {}
    for r in project.store.query("SELECT status, kind, record_id FROM questions"):
        if not (r["kind"] in SCREEN_KINDS and r["record_id"] in hid):
            out[r["status"]] = out.get(r["status"], 0) + 1
    return out


def get(project: Project, question_id: int) -> dict[str, Any]:
    r = project.store.one("SELECT * FROM questions WHERE question_id=?", (question_id,))
    if r is None:
        raise KeyError(f"no question {question_id}")
    return _row(r)


# One answer or dismissal at a time: two requests from a double click would both find the question open and write
# two ledger and journal rows (ui-tester F1); the second now finds it answered and is refused.
_LOCK = threading.RLock()


def dismiss(project: Project, question_id: int, by: str = "") -> dict[str, Any]:
    with _LOCK:
        return _dismiss(project, question_id, by)


def _dismiss(project: Project, question_id: int, by: str) -> dict[str, Any]:
    q = _fresh(project, question_id)
    _open(q)
    from .journal import recording
    with recording(project, "dismiss", f"dismissed '{q['title'][:60]}' ({q['kind'].replace('_', ' ')})", author=by,
                   record_id=q["record_id"]) as rec:
        rec.rows("questions", "question_id=?", (question_id,))
        if q["kind"] in REFINE_KINDS:
            rec.rows("decisions", "dedupe_key=?", [q["dedupe_key"]])
            _ledger(project, q, None, "", by)
        project.store.execute("UPDATE questions SET status='dismissed', answered_by=?, answered_at=? WHERE question_id=?",
                              (by, utcnow(), question_id))
        _close_stale(project, rec, q)
    _after(project, q)
    return get(project, question_id)


def _fresh(project: Project, question_id: int) -> dict[str, Any]:
    """The question, closed first when it rests only on seeds moved out of Development, so answering it is refused
    with the reason (blinding; risk-checker R1)."""
    q = get(project, question_id)
    if _blind(project, q):
        raise ValueError(f"question {question_id}: {hidden_line(project)}")
    if q["kind"] in REFINE_KINDS and q["status"] in ("open", "waiting"):
        from ..interview import bank
        if bank.close_stale(project, only=question_id, held_only=True):
            q = get(project, question_id)
    return q


def _close_stale(project: Project, rec, q: dict) -> None:
    """Inside the answer's recording, so undoing the answer reopens what it closed (risk-checker R2)."""
    if q["kind"] in REFINE_KINDS:
        from ..interview import bank
        bank.close_stale(project, rec)


def _open(q: dict) -> None:
    if q["status"] == "dismissed" and (q.get("applied") or "").startswith("closed:"):
        raise ValueError(f"question {q['question_id']} is {q['applied']}")
    if q["status"] == "waiting":
        raise ValueError(f"question {q['question_id']} is waiting for the budget: answer an open one first")
    if q["status"] != "open":
        raise ValueError(f"question {q['question_id']} is already {q['status']}")


def _ledger(project: Project, q: dict, choice: str | None, text: str, by: str) -> None:
    """The decision-ledger row of a refinement answer (``choice`` None: a dismissal), inside the caller's change."""
    from ..construct import artifacts
    from ..interview import ledger
    from .journal import current_author

    ctx = q["context"] or {}
    default = ctx.get("default")
    mode = "dismissed" if choice is None else ("default" if choice == default else "chosen")
    opt = next((o for o in q["options"] if o["value"] == choice), {})
    evidence = {**(ctx.get("evidence") or {}), **({"note": text} if text else {})}
    row = artifacts.latest(project, "synonyms")
    ledger.record(project, q["kind"], q["dedupe_key"], evidence, q["options"], default, choice, mode,
                  opt.get("consequence") or ("dismissed" if choice is None else ""), current_author(by),
                  row["version"] if row else None)


def _after(project: Project, q: dict) -> None:
    if q["kind"] in REFINE_KINDS:
        from ..interview import bank
        bank.after_answer(project)


def answer(project: Project, question_id: int, choice: str, text: str = "", by: str = "",
           confirm_revision: bool = False) -> dict[str, Any]:
    """Record the answer and apply it at once; returns the question with ``applied`` describing what changed. An
    answer that saves a synonyms version after a look that kept papers as validation needs ``confirm_revision``
    (:class:`~wider.construct.artifacts.RevisionNeedsConfirm`, nothing recorded; D2 of validation-reuse)."""
    from ..construct.artifacts import revision_confirmed

    with _LOCK, revision_confirmed(confirm_revision):
        return _answer(project, question_id, choice, text, by)


def _answer(project: Project, question_id: int, choice: str, text: str, by: str) -> dict[str, Any]:
    q = _fresh(project, question_id)
    _open(q)
    allowed = {o["value"]: o for o in q["options"]}
    if choice not in allowed:
        raise ValueError(f"answer must be one of {', '.join(allowed)}")
    if allowed[choice].get("input") and not (text or "").strip():
        raise ValueError(f"'{allowed[choice]['label']}' needs a value")
    from .journal import recording
    with recording(project, "answer", f"answered '{q['title'][:60]}' ({q['kind'].replace('_', ' ')}): {choice}", author=by,
                   record_id=q["record_id"]) as rec:
        rec.rows("questions", "question_id=?", (question_id,))
        _watch(project, rec, q)
        applied = APPLY[q["kind"]](project, q, choice, (text or "").strip(), by)
        if q["kind"] in REFINE_KINDS:
            _ledger(project, q, choice, (text or "").strip(), by)
        project.store.execute("UPDATE questions SET status='answered', answer=?, answered_by=?, applied=?, answered_at=? WHERE question_id=?",
                              (json.dumps({"choice": choice, "text": text or ""}), by, applied, utcnow(), question_id))
        _close_stale(project, rec, q)
    _after(project, q)
    return get(project, question_id)


def _watch(project: Project, rec, q: dict) -> None:
    """Everything an answer of this kind can change, so the answer can be undone."""
    ctx, rid = q["context"] or {}, q["record_id"]
    if q["kind"] == "screen_failed":
        rec.rows("screen_calls", "run_id=? AND record_id=? AND pass=?", (q["run_ref"], rid, ctx.get("pass")))
        rec.rows("runs", "run_id=?", (q["run_ref"],))
    elif q["kind"] == "screen_split":
        rec.rows("screen_decisions", "protocol=? AND record_id=?", (ctx.get("protocol"), rid))
    elif q["kind"] == "enrich_unresolved":
        rec.rows("records", "record_id=?", (rid,))
        rec.rows("enrichment_log", "record_id=?", (rid,))
        rec.rows("record_abstracts", "record_id=?", (rid,))   # an undo takes the pasted version away with the abstract
    elif q["kind"] == "seed_unresolved":
        rec.file("benchmark.csv")
        rec.rows("benchmark_papers", "benchmark_id=?", (q["ref"],))
    elif q["kind"] == "proposal_failed":
        rec.rows("clarifications", "question_id=?", (q["question_id"],))
    elif q["kind"] in REFINE_KINDS:
        from ..interview import apply
        apply.watch(project, rec, q)


# ------------------------------------------------------------------------------------------- applying answers
def _apply_screen_failed(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    if choice == "retry":
        return "left for the next run of this pass, which asks the model again"
    from . import jsonl
    from .provenance import sha256_text
    ctx = q["context"] or {}
    run_id, pass_name, rid = q["run_ref"], ctx.get("pass"), q["record_id"]
    parsed = {"decision": choice, "human": True, "by": by or "researcher", "question_id": q["question_id"],
              **({"note": text} if text else {})}
    st = project.store
    # the researcher's read: provider "researcher", no model, no backend (never reused as a model evaluation)
    from ..enrich.abstracts import current_version
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,raw_decision,rule_decision,parsed,usage,attempts,error,at,"
               "provider,model,backend_hash,model_reported,eval_key,abstract_version) "
               "VALUES(?,?,?,?,1,?,?,?,?,0,NULL,?,'researcher',NULL,NULL,NULL,NULL,?) "
               "ON CONFLICT(run_id,record_id,pass) DO UPDATE SET ok=1, raw_decision=excluded.raw_decision, "
               "rule_decision=excluded.rule_decision, parsed=excluded.parsed, error=NULL, at=excluded.at, provider='researcher', "
               "model=NULL, backend_hash=NULL, model_reported=NULL, eval_key=NULL, abstract_version=excluded.abstract_version",
               (run_id, rid, pass_name, sha256_text(f"human:{q['question_id']}"), choice, choice, json.dumps(parsed), "{}", utcnow(),
                current_version(st, rid)))
    run = st.one("SELECT dir FROM runs WHERE run_id=?", (run_id,))
    if run:   # the run's response log gets the human read too, so a replay keeps it (and an undo retracts it)
        log = project.root / run["dir"] / "responses.jsonl"
        jsonl.append(log, {"record_id": rid, "pass": pass_name, "ok": True, "human": True, "question_id": q["question_id"],
                           "parsed": parsed, "rule": {"raw": choice, "final": choice, "flags": ["human"]}, "at": utcnow()})
        from .journal import side_effect
        side_effect({"t": "jsonl_human", "path": str(log.relative_to(project.root)), "record_id": rid, "pass": pass_name,
                     "question_id": q["question_id"]})
        st.execute("UPDATE runs SET n_ok=(SELECT COUNT(*) FROM screen_calls WHERE run_id=? AND ok=1) WHERE run_id=?", (run_id, run_id))
    return f"recorded as the {pass_name} read of record {rid} in run {run_id} (marked as the researcher's)"


def _apply_screen_split(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    """Write the researcher's decision into the result set the question belongs to (its context's set key)."""
    from ..screen.decisions import set_decision
    ctx = q["context"] or {}
    key = ctx["protocol"]
    if choice == "reuse":
        earlier = ctx.get("earlier")
        if not earlier:
            raise ValueError("there is no earlier answer to reuse for this question")
        choice = earlier["choice"]
        vote = f"researcher:reused:set{earlier['set']}:{earlier['question_id']}"
        # credit the earlier answer to whoever gave it (read from the question itself, not assumed)
        row = project.store.one("SELECT answered_by FROM questions WHERE question_id=?", (earlier["question_id"],))
        gave = (row["answered_by"] if row else None) or earlier.get("by") or "unnamed"
        whose = "their answer" if gave == (by or "unnamed") else f"the answer {gave} gave"
        reason = (f"researcher ({by or 'unnamed'}) reused {whose} in set {earlier['set']} (question "
                  f"{earlier['question_id']}, same codebook and rules)")
    else:
        vote = f"researcher:{choice}"
        reason = f"researcher ({by or 'unnamed'}) decided after three disagreeing reads" + (f": {text}" if text else "")
    votes = list(ctx.get("votes") or []) + [vote]
    set_decision(project, key, q["record_id"], choice, votes=votes, cell=ctx.get("cell"), reason=reason,
                 vote_calls=ctx.get("vote_calls"), decided_by="researcher")
    return f"protocol {key} decision for record {q['record_id']} set to {choice}"


def earlier_split_answer(project: Project, key: str, record_id: int) -> dict[str, Any] | None:
    """The researcher's answer to the same record's split in another result set of the protocol whose codebook and
    rules equal this set's (recorded decision 27), or None."""
    from ..screen import result_sets as rs

    protocol, n = rs.split_key(key)
    here = rs.row_by_key(project, key) or {}
    hs = here.get("settings") if isinstance(here.get("settings"), dict) else {}
    for r in project.store.query("SELECT question_id, context, answer, answered_by FROM questions WHERE kind='screen_split' AND "
                                 "status='answered' AND record_id=? ORDER BY question_id DESC", (record_id,)):
        ctx = json.loads(r["context"]) if r["context"] else {}
        other = ctx.get("protocol")
        if not other or other == key or rs.split_key(other)[0] != protocol:
            continue
        ans = json.loads(r["answer"]) if r["answer"] else {}
        if ans.get("choice") not in ("include", "maybe", "exclude"):
            continue
        row = rs.row_by_key(project, other) or {}
        os_ = row.get("settings") if isinstance(row.get("settings"), dict) else {}
        if not hs or not os_ or hs.get("codebook") != os_.get("codebook") or hs.get("rules") != os_.get("rules"):
            continue
        return {"set": rs.split_key(other)[1], "key": other, "choice": ans["choice"], "question_id": r["question_id"],
                "by": r["answered_by"]}
    return None


def _norm_doi(text: str) -> str:
    from .text import clean_doi
    d = clean_doi(text)
    if not d or not d.startswith("10."):
        raise ValueError("that does not look like a DOI (it starts with 10.)")
    return d


def _apply_enrich(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    st, rid = project.store, q["record_id"]
    if choice == "leave":
        return "left as it is"
    if choice == "doi":
        doi = _norm_doi(text)
        st.execute("UPDATE records SET doi=? WHERE record_id=?", (doi, rid))
        # the log is append-only: a 'reset' row keeps the exhausted attempt and makes the record a candidate again
        st.execute("INSERT INTO enrichment_log(record_id,source,status,words,fetched_at,note) VALUES(?,?,?,?,?,?)",
                   (rid, "researcher", "reset", None, utcnow(),
                    json.dumps({"doi_corrected": doi, "question_id": q["question_id"], "by": by})))
        return f"DOI of record {rid} set to {doi}; the next enrichment run looks it up again"
    from .text import word_count
    words = word_count(text)
    thin = int(words < project.config.protocol.corpus.thin_words)
    st.execute("UPDATE records SET abstract=?, abstract_words=?, thin=?, abstract_source='researcher' WHERE record_id=?",
               (text, words, thin, rid))
    from ..enrich.abstracts import add_version
    add_version(st, rid, text, "researcher")
    st.execute("INSERT INTO enrichment_log(record_id,source,status,words,fetched_at,note) VALUES(?,?,?,?,?,?)",
               (rid, "researcher", "filled", words, utcnow(), json.dumps({"question_id": q["question_id"], "by": by})))
    return f"abstract of record {rid} set from your answer ({words} words)"


def _apply_seed(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    if choice == "leave":
        return "left as it is"
    doi = _norm_doi(text)
    papers = project.read_benchmark()
    hit = False
    for b in papers:
        if b.benchmark_id == q["ref"]:
            b.doi, hit = doi, True
    if not hit:
        raise ValueError(f"seed paper {q['ref']} is no longer in benchmark.csv")
    project.write_benchmark(papers)
    from ..construct.benchmark import sync
    sync(project)
    return f"DOI of seed paper {q['ref']} set to {doi} in benchmark.csv; fetch the seed abstracts again to use it"


def _apply_proposal(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    if choice == "leave":
        return "left as it is"
    project.store.execute("INSERT INTO clarifications(scope,text,author,question_id,created_at) VALUES('construct',?,?,?,?)",
                          (text, by, q["question_id"], utcnow()))
    return "saved as a clarification; every later construct proposal prompt shows it"


def _apply_refine(project: Project, q: dict, choice: str, text: str, by: str) -> str:
    from ..interview import apply
    return apply.APPLY[q["kind"]](project, q, choice, text, by)


APPLY = {"screen_failed": _apply_screen_failed, "screen_split": _apply_screen_split, "enrich_unresolved": _apply_enrich,
         "seed_unresolved": _apply_seed, "proposal_failed": _apply_proposal, **dict.fromkeys(REFINE_KINDS, _apply_refine)}


def clarifications(project: Project, scope: str = "construct", limit: int | None = None) -> list[str]:
    """The active clarifications of a scope, oldest first; scope ``query`` holds the decision ledger's texts (the
    newest ``limit``, default 30)."""
    if scope == "query":
        from ..interview import ledger
        return ledger.prompt_texts(project, limit or ledger.PROMPT_LIMIT)
    if "clarifications" not in project.store.tables():
        return []
    rows = [r["text"] for r in project.store.query("SELECT text FROM clarifications WHERE scope=? AND active=1 ORDER BY clarification_id", (scope,))]
    return rows[-limit:] if limit else rows


def set_mode(project: Project, mode: str) -> str:
    if mode not in ("queue", "ask"):
        raise ValueError("mode must be queue or ask")
    project.config.doubts = mode
    project.save()
    return mode
