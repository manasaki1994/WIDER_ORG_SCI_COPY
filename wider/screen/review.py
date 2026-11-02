"""The Screening review page's engine (manual-review D1-D4, D11-D13): which records of a result set need a person and
why, one record's reads beside the LLM outcome and the final decision, a review decision written in place and
journalled, the criteria the set was screened with, quality-control samples of settled records, the pending count
for the Screening page and Home, and a sample's count of records reviewed with the LLM's reads. The CLI
(``wider review``) and the page's routes call these functions."""
from __future__ import annotations

import hashlib
import json
import random
import re
from typing import Any

from ..core.project import Project
from ..core.provenance import utcnow
from . import result_sets as rs
from .agreement import ROUTE_LABELS, _origin, _votes, reads, route_of, set_rules
from .devgate import HIDDEN

SENTENCE = ("This page shows the AI model's reads of each record. If you still label a validation sample, label it first: "
            "these reads could sway your labels.")
NOTHING = "no screening decisions yet"
NO_READS = "no pass reads were stored for this record (imported or older project)"
QC_SENTENCE = "A quality-control sample checks the AI model's settled decisions; it is not a recall estimate."
FAILED_ROW = "failed read: answer it on the Questions page"
SHOWS = ("flagged", "majority", "qc", "all")
DECISIONS = ("include", "exclude", "maybe")
PASS_NAMES = {"A": "pass A", "B": "pass B", "C": "third read"}
ANSWERED_READ = "researcher (answered failed read)"


class Refusal(ValueError):
    """A refused review action: ``status`` is the route's HTTP status, ``code`` the CLI's exit code."""

    def __init__(self, message: str, status: int = 400, code: int = 1):
        super().__init__(message)
        self.status, self.code = status, code


# ------------------------------------------------------------------------------------------------- the target
def default_target(project: Project) -> dict[str, Any] | None:
    """``{protocol, set}`` the page, ``wider review list`` and the pending count start on (D1): the protocol of the
    round the Screening card shows, else "main", else the first protocol with decisions; its active set."""
    from .decisions import protocols
    from .progress import chosen_corpus

    chosen = chosen_corpus(project)
    names = [p["protocol"] for p in protocols(project)]
    if chosen:
        protocol = chosen["protocol"]
    elif "main" in names:
        protocol = "main"
    elif names:
        protocol = names[0]
    else:
        return None
    act = rs.active_set(project, protocol)
    return {"protocol": protocol, "set": int(act["result_set"]) if act else 1}


def _target(project: Project, protocol: str | None, result_set: int | None) -> dict[str, Any] | None:
    """``{protocol, set, key, row}`` for the given protocol and set (defaults: :func:`default_target`), None when
    nothing was screened. An unknown set raises KeyError "no result set N of 'P'"."""
    if protocol is None:
        t = default_target(project)
        if t is None:
            return None
        protocol = t["protocol"]
        result_set = result_set if result_set is not None else t["set"]
    key = rs.active_key(project, protocol, result_set)
    row = rs.row_by_key(project, key)
    n = int(row["result_set"]) if row else int(result_set or 1)
    return {"protocol": protocol, "set": n, "key": key, "row": row or {}}


def set_label(t: dict[str, Any]) -> str:
    return f"{t['protocol']} set {t['set']}"


# ------------------------------------------------------------------------------------------------- the rows
def _decision_rows(project: Project, key: str) -> dict[int, dict]:
    return {r["record_id"]: dict(r) for r in project.store.query(
        "SELECT d.*, r.title FROM screen_decisions d LEFT JOIN records r ON r.record_id=d.record_id WHERE d.protocol=? "
        "ORDER BY d.record_id", (key,))}


def _failed_reads(project: Project, key: str, row: dict, decisions: dict[int, dict]) -> dict[int, dict]:
    """``{record_id: {state, question_id, by, pass, run_id}}`` of the set's runs (D2): ``open`` for a pass that failed
    and is not read since, ``answered`` for a failed read a researcher answered that the next run has not written."""
    runs = rs.run_ids(row)
    if not runs:
        return {}
    st, q = project.store, ",".join("?" * len(runs))
    out: dict[int, dict] = {}
    ok = {(r["record_id"], r["pass"]) for r in st.query(
        f"SELECT record_id, pass FROM screen_calls WHERE ok=1 AND run_id IN ({q})", runs)}
    for r in st.query(f"SELECT record_id, pass, run_id FROM screen_calls WHERE ok=0 AND run_id IN ({q}) "
                      "ORDER BY record_id, pass", runs):
        if (r["record_id"], r["pass"]) in ok or r["record_id"] in out:
            continue
        qrow = st.one("SELECT question_id FROM questions WHERE kind='screen_failed' AND record_id=? AND run_ref=? "
                      "ORDER BY question_id DESC", (r["record_id"], r["run_id"]))
        out[r["record_id"]] = {"state": "open", "question_id": qrow["question_id"] if qrow else None, "by": None,
                               "pass": r["pass"], "run_id": r["run_id"]}
    for a in st.query(f"SELECT question_id, record_id, run_ref, answer, answered_by, answered_at, context FROM questions "
                      f"WHERE kind='screen_failed' AND status='answered' AND run_ref IN ({q}) ORDER BY question_id", runs):
        rid = a["record_id"]
        if rid in out or (json.loads(a["answer"] or "{}").get("choice") == "retry"):
            continue
        ctx = json.loads(a["context"] or "{}")
        call = st.one("SELECT call_id FROM screen_calls WHERE run_id=? AND record_id=? AND pass=? AND provider='researcher'",
                      (a["run_ref"], rid, ctx.get("pass")))
        if call is None:
            continue
        d = decisions.get(rid)
        if d is not None:
            calls = _votes(d["vote_calls"])
            written = call["call_id"] in calls if calls else (d["decided_at"] or "") >= (a["answered_at"] or "")
            if written:
                continue
        out[rid] = {"state": "answered", "question_id": a["question_id"], "by": a["answered_by"] or "unnamed",
                    "pass": ctx.get("pass"), "run_id": a["run_ref"]}
    return out


def _open_splits(project: Project, key: str) -> dict[int, int]:
    return {r["record_id"]: r["question_id"] for r in project.store.query(
        "SELECT question_id, record_id FROM questions WHERE kind='screen_split' AND status='open' AND run_ref=?", (key,))}


def qc_records(project: Project, key: str) -> set[int]:
    return {r["record_id"] for r in project.store.query("SELECT record_id FROM review_qc WHERE set_key=?", (key,))}


def answered_wait(by: str) -> str:
    return f"failed read answered by {by}; the next screening run of this set writes its decision"


def earlier_outcome(row: dict) -> str:
    """The decision before any person reviewed the row (D3): the outcome of the passes' reads without the researcher's
    vote, or, for a row without pass reads, its ``before_review`` (else its decision)."""
    if reads(_votes(row.get("votes"))):
        return rs._model_final({"votes": row.get("votes") if isinstance(row.get("votes"), str) else
                                json.dumps(row.get("votes")), "decision": row["decision"]})
    return (_origin(row.get("origin")) or {}).get("before_review") or row["decision"]


def _reviewer(reason: str | None) -> str | None:
    m = re.match(r"researcher \((.+?)\) (?:decided|reused)", reason or "")
    return m.group(1) if m else None


def _row(rid: int, d: dict | None, title: str | None, majority: int, failed: dict | None, split_q: int | None,
         qc: bool) -> dict[str, Any]:
    route = route_of(d["decided_by"], _origin(d["origin"]), _votes(d["votes"]), d["decision"], majority) if d else None
    decided = bool(d) and d["decided_by"] == "researcher"
    if failed and failed["state"] == "open":
        why = "a pass failed to read it"
    elif failed:
        why = answered_wait(failed["by"])
    elif split_q is not None:
        why = "the three reads disagree"
    elif qc:
        why = f"quality-control sample: the AI model settled it as {earlier_outcome(d)}"
    elif decided:
        why = ROUTE_LABELS["researcher"]
    elif d["decision"] == "maybe":
        why = "the final decision is maybe"
    elif route == "third_read":
        why = "settled by the third read"
    else:
        why = ROUTE_LABELS.get(route, route)
    return {"record_id": rid, "title": title or (d or {}).get("title") or f"record {rid}",
            "decision": d["decision"] if d else None, "route": route, "route_label": ROUTE_LABELS.get(route) if route else None,
            "why": why, "decided": decided, "reviewer": _reviewer(d["reason"]) if decided else None,
            "failed": bool(failed and failed["state"] == "open"), "failed_answered": bool(failed and failed["state"] == "answered"),
            "failed_by": failed["by"] if failed else None, "question_id": (failed or {}).get("question_id") or split_q,
            "split_question": split_q is not None, "qc": qc,
            "decidable": not failed, "pass_reads": bool(d and reads(_votes(d["votes"]))),
            "needs_person": not decided and bool(failed or split_q is not None or qc or (d and d["decision"] == "maybe"))}


def _state(project: Project, t: dict[str, Any]) -> tuple[dict[int, dict], dict[int, dict]]:
    """Every row of the set (``all``) and the decision rows behind them."""
    key = t["key"]
    decisions = _decision_rows(project, key)
    failed = _failed_reads(project, key, t["row"], decisions)
    splits, qc = _open_splits(project, key), qc_records(project, key)
    majority = set_rules(project, key)["majority"]
    titles = {}
    missing = [rid for rid in failed if rid not in decisions]
    if missing:
        titles = {r["record_id"]: r["title"] for r in project.store.query(
            f"SELECT record_id, title FROM records WHERE record_id IN ({','.join('?' * len(missing))})", missing)}
    rows = {}
    for rid in sorted(set(decisions) | set(failed)):
        d = decisions.get(rid)
        rows[rid] = _row(rid, d, titles.get(rid), majority, failed.get(rid), splits.get(rid), rid in qc)
    return rows, decisions


# Records of a validation (probability) sample not locked yet (owner question 28b0, not answered yet): "show" lists
# them with the LLM's reads and counts afterwards how many a person decided after seeing them (option a, the
# recommendation; reviewed_line); "hide" also lists them without reads until the sample is locked (option b).
OPEN_SAMPLE_READS = "show"
OPEN_SAMPLE_HIDDEN = "Model decisions on {n} records of a sample not locked yet are hidden until it is locked"


def _open_sample_ids(project: Project) -> set[int]:
    if OPEN_SAMPLE_READS != "hide":
        return set()
    return {r[0] for r in project.store.query(
        "SELECT r.record_id FROM sample_rows r JOIN samples s ON s.sample_id=r.sample_id "
        "WHERE s.kind!='purposive' AND s.locked=0")}


def _hidden(project: Project) -> set[int]:
    """Records of an incomplete purposive sample (ui-redesign D4), and with OPEN_SAMPLE_READS "hide" those of an
    unlocked validation sample: left out of the list, shown without reads."""
    from .devgate import hidden_ids

    return hidden_ids(project) | _open_sample_ids(project)


def _hidden_text(project: Project, n: int) -> str:
    return OPEN_SAMPLE_HIDDEN.format(n=n) if OPEN_SAMPLE_READS == "hide" and _open_sample_ids(project) else \
        HIDDEN.format(n=n)


def _hidden_view(project: Project, t: dict[str, Any], rid: int, n: int) -> dict[str, Any]:
    """A hidden record in the drawer and `wider review show`: title and abstract only, no read, outcome or route."""
    rec = dict(project.store.one("SELECT title, abstract, year, journal FROM records WHERE record_id=?", (rid,)) or {})
    return {"record_id": rid, "protocol": t["protocol"], "set": t["set"], "key": t["key"],
            "title": rec.get("title") or f"record {rid}", "abstract": rec.get("abstract"), "year": rec.get("year"),
            "venue": rec.get("journal"), "why": None, "decision": None, "route": None, "route_label": None,
            "decided": False, "needs_person": False, "failed": False, "failed_answered": False, "question_id": None,
            "split_question": None, "qc": False, "pass_reads": False, "reads": [], "no_reads": None,
            "llm_outcome": None, "outcome_label": None, "final": None, "final_line": None, "origin": None,
            "hidden": _hidden_text(project, n)}


def _listed(r: dict[str, Any], show: str) -> bool:
    flagged = (r["failed"] or r["failed_answered"] or r["split_question"] or r["qc"] or r["decided"]
               or r["decision"] == "maybe")
    if show == "flagged":
        return flagged
    if show == "majority":
        return flagged or r["route"] == "third_read"
    if show == "qc":
        return r["qc"]
    return True


def flagged(project: Project, protocol: str | None = None, result_set: int | None = None,
            show: str = "flagged") -> dict[str, Any]:
    """The review list (D2): ``{protocol, set, key, show, rows, listed, undecided, nothing, qc_drawn}``; ``nothing``
    when the project has no screening decisions. One row per record; a failed read wins over its maybe."""
    if show not in SHOWS:
        raise Refusal(f"show must be one of {', '.join(SHOWS)}", 400, 2)
    t = _target(project, protocol, result_set)
    if t is None:
        return {"protocol": None, "set": None, "key": None, "show": show, "rows": [], "listed": 0, "undecided": 0,
                "nothing": True, "qc_drawn": False, "n_hidden": 0, "hidden": None}
    rows, _d = _state(project, t)
    hid = _hidden(project) & set(rows)
    out = [r for r in rows.values() if _listed(r, show) and r["record_id"] not in hid]
    return {"protocol": t["protocol"], "set": t["set"], "key": t["key"], "show": show, "rows": out, "listed": len(out),
            "undecided": sum(r["needs_person"] for r in out), "n_hidden": len(hid),
            "hidden": _hidden_text(project, len(hid)) if hid else None,
            "nothing": not rows and not project.store.scalar("SELECT COUNT(*) FROM screen_decisions"),
            "qc_drawn": bool(qc_records(project, t["key"]))}


def count_line(res: dict[str, Any]) -> str:
    if res["show"] in ("flagged", "qc"):
        return f"{res['listed']} records need a person, {res['undecided']} undecided"
    return f"{res['listed']} records listed, {res['undecided']} undecided"


# ------------------------------------------------------------------------------------------------- one record
def _not_in_set(rid: int, t: dict[str, Any]) -> Refusal:
    return Refusal(f"record {rid} is not in set {set_label(t)}", 404, 1)


def _reads_of(project: Project, d: dict | None, failed: dict | None) -> list[dict[str, Any]]:
    out = []
    if d:
        votes, calls = reads(_votes(d["votes"])), _votes(d["vote_calls"])
        passes = ["A", "B", "C"]
        for i, v in enumerate(votes):
            c = project.store.one("SELECT pass, provider, model, model_reported, parsed FROM screen_calls WHERE call_id=?",
                                  (calls[i],)) if i < len(calls) else None
            p = (c["pass"] if c else None) or (passes[i] if i < 3 else str(i + 1))
            parsed = json.loads(c["parsed"] or "{}") if c else {}
            human = bool(c and c["provider"] == "researcher")
            reason = parsed.get("note") if human else parsed.get("summary")
            excl = parsed.get("exclusion_reason")
            out.append({"pass": p, "label": PASS_NAMES.get(p, f"pass {p}"), "decision": v,
                        "reason": reason or None, "exclusion_reason": excl if excl not in (None, "", "none") else None,
                        "model": ANSWERED_READ if human else ((c["model_reported"] or c["model"]) if c else None),
                        "by_researcher": human})
    if failed and failed["state"] == "open":
        out.append({"pass": failed["pass"], "label": PASS_NAMES.get(failed["pass"], f"pass {failed['pass']}"),
                    "decision": None, "reason": None, "exclusion_reason": None, "model": None, "by_researcher": False,
                    "failed": True})
    return out


def record_view(project: Project, protocol: str | None, result_set: int | None, record_id: int) -> dict[str, Any]:
    """One listed record (D2, D3): title, abstract, each pass's read, the LLM outcome and the final decision with who
    reached it. No sample label, band, rank, score or sample membership."""
    t = _target(project, protocol, result_set)
    if t is None:
        raise Refusal(NOTHING, 404, 1)
    rows, decisions = _state(project, t)
    rid = int(record_id)
    if rid not in rows:
        raise _not_in_set(rid, t)
    hid = _hidden(project) & set(rows)
    if rid in hid:
        return _hidden_view(project, t, rid, len(hid))
    r, d = rows[rid], decisions.get(rid)
    rec = project.store.one("SELECT title, abstract, year, journal FROM records WHERE record_id=?", (rid,)) or {}
    failed = None
    if r["failed"] or r["failed_answered"]:
        failed = {"state": "open" if r["failed"] else "answered", "by": r["failed_by"],
                  "pass": None, "question_id": r["question_id"]}
        if r["failed"]:
            fr = _failed_reads(project, t["key"], t["row"], decisions).get(rid) or {}
            failed["pass"] = fr.get("pass")
    rd = _reads_of(project, d, failed)
    view = {**r, "protocol": t["protocol"], "set": t["set"], "key": t["key"], "title": (rec["title"] if rec else None)
            or r["title"], "abstract": rec["abstract"] if rec else None, "year": rec["year"] if rec else None,
            "venue": rec["journal"] if rec else None, "reads": rd, "no_reads": None if r["pass_reads"] else NO_READS,
            "llm_outcome": None, "outcome_label": None, "final": d["decision"] if d else None, "final_line": None,
            "origin": _origin(d["origin"]) if d else None, "hidden": None}
    if d:
        view["llm_outcome"] = earlier_outcome(d)
        if not r["pass_reads"]:
            view["outcome_label"] = "Earlier decision (imported)"
        elif any(x.get("by_researcher") for x in rd):
            view["outcome_label"] = "Outcome of the passes' reads (one given by a researcher)"
        else:
            view["outcome_label"] = "AI model outcome"
        who = f"researcher {r['reviewer']}" if r["decided"] and r["reviewer"] else (
            ROUTE_LABELS["researcher"] if r["decided"] else r["route_label"])
        view["final_line"] = f"Final: {d['decision']} ({who})"
    if r["failed"]:
        view["failed_line"] = FAILED_ROW
    elif r["failed_answered"]:
        view["failed_line"] = answered_wait(r["failed_by"])
    return view


def view_lines(v: dict[str, Any]) -> list[str]:
    """``wider review show`` as text (the drawer's twin)."""
    out = [f"record {v['record_id']}: {v['title']}"] + ([f"why: {v['why']}"] if v.get("why") else [])
    if v.get("abstract"):
        out.append(f"abstract: {v['abstract']}")
    if v.get("hidden"):
        return out + [v["hidden"] + "."]
    for x in v["reads"]:
        if x.get("failed"):
            out.append(f"{x['label']}: failed")
            continue
        extra = "".join(f"; {k}: {x[k]}" for k in ("exclusion_reason",) if x.get(k))
        out.append(f"{x['label']}: {x['decision']}" + (f" ({x['model']})" if x.get("model") else "")
                   + (f": {x['reason']}" if x.get("reason") else "") + extra)
    if v["no_reads"]:
        out.append(v["no_reads"])
    if v.get("failed_line"):
        out.append(v["failed_line"] + ("" if v["failed_answered"] else
                                       f" (wider questions answer {v['question_id']})" if v.get("question_id") else ""))
    if v["llm_outcome"] is not None:
        out.append(f"{v['outcome_label']}: {v['llm_outcome']}")
    if v["final_line"]:
        out.append(v["final_line"])
    return out


# ------------------------------------------------------------------------------------------------- deciding
def _need_name(by: str | None) -> str:
    who = (by or "").strip()
    if not who:
        raise Refusal("a reviewer name is needed", 400, 2)
    return who


def decide(project: Project, protocol: str | None, result_set: int | None, record_id: int, decision: str,
           by: str | None, reason: str | None = None) -> dict[str, Any]:
    """A review decision (D3): through the open split question when there is one, refused for a failed read,
    otherwise written in place and journalled as ``review_decision``. Returns the record view and ``change_id``."""
    from ..core import journal
    from ..core.questions import answer
    from .decisions import set_decision

    who = _need_name(by)
    if decision not in DECISIONS:
        raise Refusal("decision must be include, exclude or maybe", 400, 2)
    t = _target(project, protocol, result_set)
    if t is None:
        raise Refusal(NOTHING, 404, 1)
    rows, decisions = _state(project, t)
    rid = int(record_id)
    if rid not in rows:
        raise _not_in_set(rid, t)
    hid = _hidden(project) & set(rows)
    if rid in hid:
        raise Refusal(f"record {rid}: {_hidden_text(project, len(hid))}; a review decision waits until then", 409, 1)
    r = rows[rid]
    if r["failed"]:
        qid = f" {r['question_id']}" if r["question_id"] else " ID"
        raise Refusal(f"record {rid} has a failed read; answer its question on the Questions page "
                      f"(wider questions answer{qid})", 409, 1)
    if r["failed_answered"]:
        raise Refusal(f"record {rid}: {answered_wait(r['failed_by'])}", 409, 1)
    note = (reason or "").strip()
    if r["split_question"]:
        answer(project, r["question_id"], decision, note, who)
    else:
        d = decisions[rid]
        votes = reads(_votes(d["votes"])) + [f"researcher:{decision}"]
        origin = _origin(d["origin"])
        if not reads(_votes(d["votes"])) and "before_review" not in (origin or {}):
            origin = {**(origin or {}), "before_review": d["decision"]}
        labels = json.loads(d["labels"]) if d.get("labels") else None
        summary = f"review decision on record {rid}: {decision} (was {d['decision']})"
        with journal.recording(project, "review_decision", summary, author=who, record_id=rid) as rec:
            rec.rows("screen_decisions", "protocol=? AND record_id=?", (t["key"], rid))
            set_decision(project, t["key"], rid, decision, votes=votes, cell=d["cell"], labels=labels,
                         reason=f"researcher ({who}) decided after screening" + (f": {note}" if note else ""),
                         decided_by="researcher", origin=origin)
    out = record_view(project, t["protocol"], t["set"], rid)
    out["change_id"] = journal.last_change_id()
    return out


# ------------------------------------------------------------------------------------------------- criteria
def criteria(project: Project, protocol: str | None = None, result_set: int | None = None) -> dict[str, Any]:
    """The criteria the set was screened with (D11a): the codebook version behind the set's prompt and every section
    ``codebook.build`` puts in the prompt, in its order, empty sections left out."""
    from ..construct import artifacts
    from ..construct.adjacent import approved_literatures

    t = _target(project, protocol, result_set)
    runs = rs.run_ids(t["row"]) if t else []
    st = project.store
    pvs = sorted({r["prompt_version"] for r in st.query(
        f"SELECT prompt_version FROM runs WHERE run_id IN ({','.join('?' * len(runs))})", runs)
        if r["prompt_version"] is not None}) if runs else []
    versions, cells_v = set(), {}
    for pv in pvs:
        _row_, data = artifacts.load(project, "prompts", pv)
        if data and data.get("codebook_version") is not None:
            versions.add(int(data["codebook_version"]))
            cells_v[int(data["codebook_version"])] = data.get("cells_version")
    versions = sorted(versions)
    if versions:
        version = versions[-1]
        note = (f"codebook version {version}, used by this set" if len(versions) == 1 else
                f"this set's runs used codebook versions {' and '.join(map(str, versions))}; showing version {version}")
        crow, cb = artifacts.load(project, "codebook", version)
        _c, cells = artifacts.load(project, "cells", cells_v.get(version))
        known = True
    else:
        crow, cb = artifacts.load(project, "codebook")
        _c, cells = artifacts.load(project, "cells")
        version = crow["version"] if crow else None
        note = (f"this set's codebook version is not recorded; showing the current codebook (version {version})"
                if crow else "no codebook yet")
        known = False
    cb, cells = cb or {}, cells or {}
    thin = cb.get("thin_abstract_rule") or (
        f"A record with no abstract or an abstract of fewer than about {project.config.protocol.corpus.thin_words} words "
        "is \"maybe\", unless the title alone makes an include obvious, or the title alone shows the record is plainly "
        "not a study or plainly off topic (then exclude).") if cb else None
    sections = [
        ("question", "Question", cb.get("question")),
        ("target_description", "Target description", cb.get("target_description")),
        ("elements", "Elements", [f"{e.get('name') or e.get('id')}: {e.get('rule') or ''}".strip(": ")
                                  for e in cb.get("elements") or []]),
        ("cells", "Cells", [f"{c.get('key')}: {c.get('question') or ''}".strip(": ") for c in cells.get("cells") or []]),
        ("checks", "Checks", [f"{c.get('question') or c.get('key')}" + (f" (if yes: {c['if_yes']})" if c.get("if_yes") else "")
                              for c in cb.get("checks") or []]),
        ("edge_cases", "Edge cases", [f"{e.get('case') or e.get('key')}" + (f" (default: {e['default']})" if e.get("default") else "")
                                      for e in cb.get("edge_cases") or []]),
        ("exclusion_reasons", "Exclusion reasons", [f"{x.get('code')}: {x.get('text') or ''}".strip(": ")
                                                    for x in cb.get("exclusion_reasons") or []]),
        ("not_a_criterion", "Not a criterion", [str(x) if not isinstance(x, dict) else
                                                (x.get("text") or x.get("name") or json.dumps(x)) for x in cells.get("not_a_criterion") or []]),
        ("adjacent_literatures", "Adjacent literatures", [
            f"{e.get('name') or e.get('key')}" for e in (approved_literatures(project) if cb else [])]
            or [str(x) if not isinstance(x, dict) else (x.get("name") or x.get("key") or "") for x in cb.get("adjacent_literatures") or []]),
        ("thin_abstract_rule", "Thin abstracts", thin),
    ]
    out = [{"key": k, "title": title, "text": v if isinstance(v, str) else None, "items": v if isinstance(v, list) else None}
           for k, title, v in sections if v]
    return {"protocol": t["protocol"] if t else None, "set": t["set"] if t else None, "version": version,
            "known": known, "versions": versions, "note": note, "sections": out}


def criteria_lines(c: dict[str, Any]) -> list[str]:
    out = [c["note"]]
    for s in c["sections"]:
        if s["text"]:
            out.append(f"{s['title']}: {s['text']}")
        else:
            out.append(f"{s['title']}:")
            out.extend(f"  - {x}" for x in s["items"])
    return out


# ------------------------------------------------------------------------------------------------- quality control
def qc_pool(project: Project, t: dict[str, Any]) -> list[int]:
    """The records a quality-control draw may take (D11): settled include or exclude, not decided by a person, in no
    labelling sample, not listed under ``flagged`` and not in an earlier draw of the set; sorted by record id."""
    rows, decisions = _state(project, t)
    listed = {rid for rid, r in rows.items() if _listed(r, "flagged")}
    sampled = {r["record_id"] for r in project.store.query("SELECT DISTINCT record_id FROM sample_rows")}
    drawn = qc_records(project, t["key"])
    return sorted(rid for rid, d in decisions.items() if d["decision"] in ("include", "exclude")
                  and d["decided_by"] != "researcher" and rid not in sampled and rid not in listed and rid not in drawn)


def pool_sha256(pool: list[int]) -> str:
    return hashlib.sha256(",".join(map(str, sorted(pool))).encode()).hexdigest()


def qc_draw(project: Project, protocol: str | None, result_set: int | None, n: int, seed: int | None,
            by: str | None) -> dict[str, Any]:
    """Draw a seeded quality-control sample of ``n`` settled records (D11), journalled as ``review_qc_draw``."""
    from ..core import journal

    who = _need_name(by)
    if n is None or int(n) < 1:
        raise Refusal("the sample size must be at least 1", 400, 1)
    n = int(n)
    t = _target(project, protocol, result_set)
    if t is None:
        raise Refusal(NOTHING, 400, 1)
    pool = qc_pool(project, t)
    if n > len(pool):
        raise Refusal(f"only {len(pool)} settled records can be drawn", 400, 1)
    if seed is None:
        seed = random.SystemRandom().randrange(1, 2 ** 31)
    seed = int(seed)
    drawn = sorted(random.Random(seed).sample(pool, n))
    st = project.store
    draw_id = int(st.scalar("SELECT COALESCE(MAX(draw_id), 0) FROM review_qc") or 0) + 1
    sha, at = pool_sha256(pool), utcnow()
    with journal.recording(project, "review_qc_draw", f"quality-control sample of {n} records (seed {seed})",
                           author=who) as rec:
        rec.rows("review_qc", "draw_id=?", (draw_id,))
        for rid in drawn:
            st.execute("INSERT INTO review_qc(draw_id,protocol,set_key,record_id,seed,n,pool_n,pool_sha256,by,at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (draw_id, t["protocol"], t["key"], rid, seed, n, len(pool), sha, who, at))
    return {"draw_id": draw_id, "protocol": t["protocol"], "set": t["set"], "records": drawn, "seed": seed, "n": n,
            "pool_n": len(pool), "pool_sha256": sha, "change_id": journal.last_change_id()}


def qc_draws(project: Project, key: str) -> list[dict[str, Any]]:
    """The set's draws that stand (an undone draw has no rows): ``{draw_id, n, pool_n, seed, by, at, records}``."""
    out: dict[int, dict] = {}
    for r in project.store.query("SELECT * FROM review_qc WHERE set_key=? ORDER BY draw_id, record_id", (key,)):
        d = out.setdefault(r["draw_id"], {"draw_id": r["draw_id"], "n": r["n"], "pool_n": r["pool_n"], "seed": r["seed"],
                                          "by": r["by"], "at": r["at"], "pool_sha256": r["pool_sha256"], "records": []})
        d["records"].append(r["record_id"])
    return list(out.values())


# ------------------------------------------------------------------------------------------------- reporting
def methods_sentences(project: Project, key: str) -> list[str]:
    """The methods draft's review sentences for set ``key`` (D4); empty when no person decided a record."""
    st = project.store
    rows = {r["record_id"]: dict(r) for r in st.query(
        "SELECT record_id, decision, votes, origin, decided_by FROM screen_decisions WHERE protocol=?", (key,))}
    out = []
    mine = [d for d in rows.values() if d["decided_by"] == "researcher"]
    if mine:
        changed = sum(d["decision"] != earlier_outcome(d) for d in mine)
        out.append(f"A researcher decided {len(mine)} record{'s' if len(mine) != 1 else ''} after screening; {changed} "
                   f"of them changed the LLM outcome.")
    for dr in qc_draws(project, key):
        ds = [rows[rid] for rid in dr["records"] if rid in rows and rows[rid]["decided_by"] == "researcher"]
        changed = sum(d["decision"] != earlier_outcome(d) for d in ds)
        out.append(f"A quality-control sample of {dr['n']} of {dr['pool_n']} records the LLM settled was drawn at random "
                   f"(seed {dr['seed']}); a researcher checked {len(ds)} and changed {changed}.")
    return out


def pending(project: Project) -> dict[str, Any] | None:
    """``{count, protocol, set, line, href}`` of the undecided records of the default review list (D12), None when
    nothing was screened."""
    t = default_target(project)
    if t is None:
        return None
    res = flagged(project, t["protocol"], t["set"])
    n = res["undecided"]
    return {"count": n, "protocol": t["protocol"], "set": t["set"],
            "line": f"{n} record{'s' if n != 1 else ''} wait{'s' if n == 1 else ''} for a person" if n else None,
            "href": f"#review?protocol={t['protocol']}&set={t['set']}"}


def pending_line(p: dict[str, Any] | None) -> str | None:
    """The CLI's line (``wider screen status``, ``wider info``), None when no record waits."""
    if not p or not p["count"]:
        return None
    return f"{p['line']} (Screening review, wider review list)"


def reviewed_with_llm_reads(project: Project, sample_id: int) -> int:
    """How many records of the sample a person decided on the review page, by answering a split question or in an
    imported adjudication, with those changes not undone (D13; partial-adjudication-import D15)."""
    from ..core.journal import undone_now

    ids = {r["record_id"] for r in project.store.query("SELECT record_id FROM sample_rows WHERE sample_id=?", (sample_id,))}
    if not ids:
        return 0
    seen = set()
    q = ",".join("?" * len(ids))
    for r in project.store.query(f"SELECT * FROM history WHERE kind IN ('review_decision','answer') AND record_id IN ({q})",
                                 sorted(ids)):
        if r["record_id"] in seen or undone_now(project, dict(r)):
            continue
        if r["kind"] == "answer":
            items = json.loads(r["items"])
            kinds = {row.get("kind") for it in items if it.get("table") == "questions" for row in (it.get("before") or [])}
            if "screen_split" not in kinds:
                continue
        seen.add(r["record_id"])
    # an adjudicator's decisions imported for some records (partial-adjudication-import D15): the records it changed
    for r in project.store.query("SELECT * FROM history WHERE kind='adjudication_import'"):
        if undone_now(project, dict(r)):
            continue
        for it in json.loads(r["items"]):
            if it.get("t") == "rows" and it.get("table") == "screen_decisions":
                seen |= {row["record_id"] for row in it.get("after") or []
                         if row.get("record_id") in ids and (_origin(row.get("origin")) or {}).get("kind") == "adjudication"}
    return len(seen)


def list_lines(res: dict[str, Any]) -> list[str]:
    """``wider review list`` after its first line (the page's list)."""
    if res["nothing"]:
        return [NOTHING]
    out = [f"{set_label(res)}: {count_line(res)}"]
    if res["qc_drawn"]:
        out.append(QC_SENTENCE)
    if res.get("hidden"):
        out.append(res["hidden"] + ".")
    for r in res["rows"]:
        state = "decided" if r["decided"] else "undecided" if r["needs_person"] else "settled"
        dec = r["decision"] or "-"
        line = f"  {r['record_id']:>6}  {dec:<8} {state:<10} {r['why']}  {r['title'][:70]}"
        if r["failed"]:
            line += f"  [{FAILED_ROW}" + (f": wider questions answer {r['question_id']}]" if r["question_id"] else "]")
        out.append(line)
    return out


def reviewed_of_sample(project: Project, name: str) -> int:
    """:func:`reviewed_with_llm_reads` of the sample called ``name`` (0 when there is none)."""
    sid = project.store.scalar("SELECT sample_id FROM samples WHERE name=?", (name,))
    return reviewed_with_llm_reads(project, sid) if sid is not None else 0


def reviewed_line(n: int) -> str | None:
    """The sample row's sentence (D13), None when no record of it was decided after seeing the reads."""
    if not n:
        return None
    return (f"{n} records of this sample were decided by a person who saw the AI model's reads" if n != 1 else
            "1 record of this sample was decided by a person who saw the AI model's reads")
