"""Where screening stands (design screening-page-tasks D1-D6): the steps, the next step, readable round names and
notes on unusual results. Read-only: nothing here writes to the store or contacts a backend.

The step list never names a label or a model decision of one record (blinding): only counts."""
from __future__ import annotations

import json
from typing import Any

from ..core.project import Project

STATES = ("completed", "in_progress", "needs_attention", "not_started")
WORDS = {"completed": "completed", "in_progress": "in progress", "needs_attention": "needs attention",
         "not_started": "not started"}
KINDS = {"development": "development sample", "validation": "validation sample", "sample": "other sample",
         "corpus": "whole corpus", "maybes": "maybes", "records": "chosen records", "other": "other"}
UNUSUAL_MIN = 5   # below this, all-excluded or all-included rounds are expected by chance (D6)
TEST_NOTE = ("These decisions come from the test model, which makes up its answers so WIDER can be tried out: "
             "they are not a screening result.")


# ----------------------------------------------------------------------------- rounds
def _sample_names(project: Project) -> tuple[str, str]:
    s = project.config.protocol.sampling
    return s.probability_sample, s.development_sample


def scope_kind(project: Project, scope: str | None) -> str:
    val, dev = _sample_names(project)
    if not scope:
        return "other"
    if scope == "corpus":
        return "corpus"
    if scope.startswith("sample:"):
        name = scope.split(":", 1)[1]
        return "validation" if name == val else "development" if name == dev else "sample"
    if scope.startswith("maybe:"):
        return "maybes"
    if scope.startswith("ids:"):
        return "records"
    return "other"


def _records_words(kind: str, scope: str | None) -> tuple[str, str]:
    """(title, phrase): ("Validation sample", "the validation sample")."""
    arg = (scope or "").split(":", 1)[1] if scope and ":" in scope else ""
    return {"development": ("Development sample", "the development sample"),
            "validation": ("Validation sample", "the validation sample"),
            "sample": (f"Sample {arg}", f"sample {arg}"),
            "corpus": ("Whole corpus", "the whole corpus"),
            "maybes": (f"Maybes of {arg}", f"the maybes of {arg}"),
            "records": ("Chosen records", "chosen records")}.get(kind, ("Screening round", "a screening round"))


def _run_sets(project: Project) -> dict[str, tuple[str, int]]:
    """run id -> (protocol, set number) from the result sets' run lists."""
    from .result_sets import _row, run_ids

    out: dict[str, tuple[str, int]] = {}
    if "screen_result_sets" not in project.store.tables():
        return out
    for r in project.store.query("SELECT * FROM screen_result_sets ORDER BY protocol, result_set"):
        d = _row(r)
        for rid in run_ids(d):
            out.setdefault(rid, (d["protocol"], int(d["result_set"])))
    return out


def _test_runs(project: Project) -> dict[str, bool]:
    """run id -> True when every backend that answered in it is the test backend (from the run snapshots, else the
    configured provider of the run)."""
    st = project.store
    fake = {p.name for p in project.config.providers if p.kind == "fake"}
    seen: dict[str, list[bool]] = {}
    if "backend_snapshots" in st.tables():
        for r in st.query("SELECT DISTINCT c.run_id, b.snapshot FROM screen_calls c JOIN backend_snapshots b "
                          "ON b.backend_hash=c.backend_hash WHERE COALESCE(c.provider,'')<>'researcher'"):
            try:
                snap = json.loads(r["snapshot"] or "{}")
            except ValueError:
                snap = {}
            seen.setdefault(r["run_id"], []).append(snap.get("kind") == "fake" or snap.get("locality") == "test")
    out = {}
    for r in st.query("SELECT run_id, provider FROM runs"):
        vals = seen.get(r["run_id"])
        out[r["run_id"]] = all(vals) if vals else r["provider"] in fake
    return out


def unusual(n: int, include: int, maybe: int, exclude: int, test: bool = False) -> str | None:
    """A plain note on a round or set whose decisions look unusual (D6); ``None`` when nothing is unusual."""
    notes = [TEST_NOTE] if test and n else []
    if n >= UNUSUAL_MIN:
        if exclude == n:
            notes.append(f"Every one of the {n} records was excluded. That is rarely a real finding: check that the "
                         "prompt's inclusion criteria fit your question, read some of the model's answers under Records "
                         "of one round, and check that the backend answered properly.")
        elif include == n:
            notes.append(f"Every one of the {n} records was included. Check that the prompt's exclusion criteria are "
                         "clear, read some of the model's answers under Records of one round, and check the backend.")
        elif maybe > n / 2:
            notes.append(f"More than half of the {n} records are maybe ({maybe}). The prompt may need sharper reasons "
                         "to exclude; read some of the model's answers under Records of one round.")
    return " ".join(notes) or None


def rounds(project: Project) -> list[dict[str, Any]]:
    """Every screening round (run), oldest first, with ``name``, ``kind``, ``test``, ``protocol``, ``set``,
    ``pass``, counts and ``unusual``."""
    from .result_sets import _pass_of, _run_scope

    st = project.store
    if "runs" not in st.tables():
        return []
    calls = {r["run_id"]: dict(r) for r in st.query(
        "SELECT run_id, COUNT(*) n, SUM(ok) ok, SUM(rule_decision='include') inc, SUM(rule_decision='maybe') may, "
        "SUM(rule_decision='exclude') exc FROM screen_calls GROUP BY run_id")}
    sets_of, tests = _run_sets(project), _test_runs(project)
    from .devgate import HIDDEN_SOME, hidden_runs

    hide = hidden_runs(project)
    out = []
    for r in st.query("SELECT * FROM runs WHERE kind='screen' ORDER BY started_at"):
        r = dict(r)
        rid = r["run_id"]
        scope = _run_scope(project, rid)
        kind = scope_kind(project, scope)
        title, phrase = _records_words(kind, scope)
        proto, n_set = sets_of.get(rid, (None, None))
        pass_name = _pass_of(project, rid)
        parts = [title]
        if proto and kind != "maybes":
            parts.append(f"protocol {proto}")
        if n_set and n_set >= 2:
            parts.append(f"set {n_set}")
        parts += [f"pass {pass_name}", f"prompt v{r['prompt_version']}" if r.get("prompt_version") else None]
        c = calls.get(rid, {})
        ok, inc, may, exc = (int(c.get(k) or 0) for k in ("ok", "inc", "may", "exc"))
        test = bool(tests.get(rid))
        out.append({"run_id": rid, "name": " · ".join(p for p in parts if p), "phrase": phrase, "kind": kind,
                    "kind_label": KINDS[kind], "test": test, "scope": scope, "protocol": proto, "set": n_set,
                    "pass": pass_name, "prompt_version": r.get("prompt_version"), "status": r["status"],
                    "n_items": r.get("n_items") or 0, "ok": ok, "failed": int(c.get("n") or 0) - ok,
                    "include": inc, "maybe": may, "exclude": exc, "unusual": unusual(ok, inc, may, exc, test),
                    **({"include": None, "maybe": None, "exclude": None, "unusual": None, "hidden": HIDDEN_SOME}
                       if rid in hide else {}),
                    "started_at": r["started_at"], "finished_at": r.get("finished_at")})
    return out


def run_name(project: Project, run_id: str) -> dict[str, Any] | None:
    return next((r for r in rounds(project) if r["run_id"] == run_id), None)


# ----------------------------------------------------------------------------- steps
def _step(key: str, title: str, required: bool, state: str, text: str, short: str, page: str = "screen",
          tab: str | None = "setup") -> dict[str, Any]:
    return {"key": key, "title": title, "required": required, "state": state, "word": WORDS[state], "text": text,
            "short": short, "page": page, "tab": tab if page == "screen" else None}


def not_final_text(c: dict[str, Any]) -> str:
    """Why a fully labelled validation sample is not final yet (``c`` from ``completeness``), and where to fix it."""
    fixes = []
    if c["not_independent"]:
        fixes.append("declare the labels independent on the sample's card (Declare labels independent)")
    if c["open"] or c["few"]:
        fixes.append("adjudicate the disagreements in Second reviewer and adjudication")
    if c["packages_out"]:
        fixes.append("import or close the review package")
    return ("Every record has a label, but recall cannot be estimated yet: " + "; ".join(c["lines"])
            + (". On the Samples and labelling page, " + " and ".join(fixes) if fixes else "") + ".")


def _labels_step(project: Project) -> dict[str, Any]:
    from ..sample.labels import completeness, final_labels

    val, _ = _sample_names(project)
    title = "Your labels on the validation sample"
    has = "samples" in project.store.tables() and project.store.scalar("SELECT COUNT(*) FROM samples WHERE name=?", (val,))
    if not has:
        return _step("labels", title, True, "not_started", "Draw the validation sample on the Samples and labelling "
                     "page; recall is measured against your labels on it.", "no validation sample", "sample")
    labs = final_labels(project, val)
    n, k = len(labs), sum(1 for r in labs if r["decision"])
    if n and k == n:
        c = completeness(project, val)   # what Estimate recall asks (inspection 1 Nov 2026): no dead end after "Estimate"
        if c["complete"]:
            return _step("labels", title, True, "completed", f"All {n} records have your final label.",
                         f"validation labels: {k} of {n}", "sample")
        return _step("labels", title, True, "needs_attention", not_final_text(c), "validation labels not final",
                     "sample")
    state = "in_progress" if k else "not_started"
    return _step("labels", title, True, state, f"{k} of {n} records have your final label. Label the rest on the "
                 "Samples and labelling page; recall is measured against these labels.",
                 f"validation labels: {k} of {n}", "sample")


def _dev_labels_step(project: Project) -> dict[str, Any] | None:
    """Every purposive sample fully labelled under the review protocol (ui-redesign D4); None without one. Its short
    text has no number (the stage list shows it); the text gives the labels left, never a label of a record."""
    from ..sample.labels import completeness
    from .devgate import _purposive

    names = _purposive(project)
    if not names:
        return None
    title = "Development sample fully labelled"
    cs = [completeness(project, n) for n in names]
    opened = [c for c in cs if not c["complete"] and c["total"]]   # an empty sample has nothing to label
    if not opened:
        return _step("dev_labels", title, True, "completed", "Every development record has a final label.",
                     "development sample labelled", "sample")
    left = sum(len(c["missing"]) for c in opened)
    out = [f"{c['sample']} has {c['packages_out'][0]}'s review package still out" for c in opened if c["packages_out"]]
    text = (f"{left:,} left: every development record needs a final label before the model sees it. Until then "
            "screening runs on them are refused and earlier model output on them is hidden."
            + (" " + "; ".join(out) + "." if out else ""))
    started = any(len(c["missing"]) < c["total"] for c in opened)
    return _step("dev_labels", title, True, "in_progress" if started else "not_started", text,
                 "development labels open", "sample")


def _corpus_sets(project: Project) -> list[dict[str, Any]]:
    """The active sets of protocols whose scope is the whole corpus, latest protocol last, with counts and ``test``."""
    from .decisions import protocols
    from .result_sets import row_by_key, run_ids

    tests = _test_runs(project)
    out = []
    for p in protocols(project):
        row = row_by_key(project, p["key"]) or {}
        if row.get("scope") != "corpus":
            continue
        ids = run_ids(row)
        out.append({**p, "include": p["include"] or 0, "maybe": p["maybe"] or 0, "exclude": p["exclude"] or 0,
                    "source": row.get("source"), "test": bool(ids) and all(tests.get(i) for i in ids)})
    return out


def chosen_corpus(project: Project) -> dict[str, Any] | None:
    """The whole-corpus set the Screening card shows (real over test, full over partial, latest last), or None."""
    st = project.store
    n_records = st.scalar("SELECT COUNT(*) FROM records") if "records" in st.tables() else 0
    corpus = _corpus_sets(project)
    full = [c for c in corpus if c["n"] >= n_records]
    real = [c for c in full if not c["test"]]
    return (real or full or corpus or [None])[-1]


def screen_progress(project: Project) -> dict[str, Any]:
    """``{steps, next, also, protocol, rounds}`` (design D2, D3)."""
    from ..construct import artifacts

    st = project.store
    rs = rounds(project)
    steps = []
    has_prompt = bool(artifacts.list_versions(project, "prompts"))
    has_codebook = bool(artifacts.list_versions(project, "codebook"))
    if has_prompt:
        steps.append(_step("prompt", "Screening prompt", True, "completed",
                           "A screening prompt is built from your concepts and criteria.", "no screening prompt"))
    elif not has_codebook:   # the prompt is built from the codebook: say where to write it first
        steps.append(_step("prompt", "Screening prompt", True, "not_started",
                           "There is no codebook yet, and the screening prompt is built from it. Write one by hand or "
                           "ask the model for one on the Concepts and synonyms page (codebook tab), then build the prompt here.",
                           "no codebook yet (Concepts and synonyms page)"))
    else:
        steps.append(_step("prompt", "Screening prompt", True, "not_started",
                           "Build the screening prompt from your concepts and criteria (Start screening).",
                           "no screening prompt"))

    dev_step = _dev_labels_step(project)
    dev_open = bool(dev_step) and dev_step["state"] != "completed"
    if dev_step:
        steps.append(dev_step)
    _, dev = _sample_names(project)
    dev_rounds = [r for r in rs if r["kind"] == "development"]
    title = "Try the model on the development sample (optional)"
    if dev_open:
        steps.append(_step("development", title, False, "not_started",
                           ("Earlier results on the development sample are hidden until labelling is complete. "
                            if dev_rounds else "") + "Optional: once every development record is labelled, try the "
                           "model on it to tune the prompt.", "development sample: after labelling"))
    elif any(r["status"] == "running" for r in dev_rounds):
        steps.append(_step("development", title, False, "in_progress", "A round on the development sample is running.",
                           "development round running"))
    elif any(not r["test"] and r["ok"] for r in dev_rounds):
        steps.append(_step("development", title, False, "completed", "The model was tried on the development sample; "
                           "Check quality shows how it did against your labels.", "development sample tried",
                           tab="quality"))
    elif dev_rounds:
        steps.append(_step("development", title, False, "needs_attention", "Only the test model ran on the development "
                           "sample. Try a real model to see how the prompt works before screening the whole corpus.",
                           "development sample: test model only"))
    else:
        steps.append(_step("development", title, False, "not_started", "Optional: try the model on the development "
                           "sample to tune the prompt before screening the whole corpus.", "development sample not tried"))

    steps.append(_labels_step(project))

    n_records = st.scalar("SELECT COUNT(*) FROM records") if "records" in st.tables() else 0
    title = "Whole corpus screened by a protocol"
    chosen = chosen_corpus(project)
    running = [r for r in rs if r["kind"] == "corpus" and r["status"] == "running"]
    if running:
        step = _step("corpus", title, True, "in_progress", "Screening the whole corpus is running; the bar at the top "
                     "shows how far it is.", "whole corpus screening running")
    elif chosen is None:
        step = _step("corpus", title, True, "not_started", "Once the prompt is final, screen the whole corpus with a "
                     "protocol (two passes and a blind third read where they disagree).", "whole corpus not screened yet")
    elif chosen["n"] < n_records:
        step = _step("corpus", title, True, "in_progress", f"Protocol {chosen['protocol']}: {chosen['n']:,} of "
                     f"{n_records:,} records decided. Start the same protocol again to finish the rest.",
                     f"whole corpus: {chosen['n']:,} of {n_records:,} decided")
    elif chosen["test"]:
        step = _step("corpus", title, True, "needs_attention", f"Protocol {chosen['protocol']} was screened with the "
                     "test model only: its decisions are not a screening result. Screen the whole corpus again with a "
                     "real model.", "whole corpus screened by the test model only")
    elif chosen["exclude"] == chosen["n"] or chosen["include"] == chosen["n"]:
        word = "excluded" if chosen["exclude"] == chosen["n"] else "included"
        step = _step("corpus", title, True, "needs_attention", f"Protocol {chosen['protocol']} {word} every record. "
                     + (unusual(chosen["n"], chosen["include"], chosen["maybe"], chosen["exclude"]) or ""),
                     f"every corpus record {word}", tab="review")
    elif chosen["maybe"] > chosen["n"] / 2:
        step = _step("corpus", title, True, "needs_attention", f"Protocol {chosen['protocol']}: "
                     + (unusual(chosen["n"], chosen["include"], chosen["maybe"], chosen["exclude"]) or ""),
                     f"{chosen['maybe']:,} of {chosen['n']:,} corpus records are maybe", tab="review")
    else:
        step = _step("corpus", title, True, "completed", f"Protocol {chosen['protocol']}: all {chosen['n']:,} records "
                     f"decided ({chosen['include']:,} include, {chosen['maybe']:,} maybe, {chosen['exclude']:,} "
                     "exclude).", "whole corpus screened", tab="review")
    if dev_open and step["state"] != "completed" and "running" not in step["short"] and step["tab"] != "review":
        step["text"] = "After the development sample is fully labelled: " + step["text"]   # no run the gate refuses
    steps.append(step)

    title = "Maybes read again or decided (optional)"
    if any(r["kind"] == "maybes" and r["status"] == "running" for r in rs):
        steps.append(_step("maybes", title, False, "in_progress", "A second screen of the maybes is running.",
                           "maybes being read again"))
    elif chosen is None:
        steps.append(_step("maybes", title, False, "not_started", "After the whole corpus is screened.", "no maybes yet"))
    elif chosen["maybe"] and dev_open:
        steps.append(_step("maybes", title, False, "needs_attention", f"{chosen['maybe']:,} records of protocol "
                           f"{chosen['protocol']} are maybe. Once the development sample is fully labelled you may read "
                           "them again, or decide them yourself on the Screening review page.",
                           f"{chosen['maybe']:,} maybes", tab="review"))
    elif chosen["maybe"]:
        steps.append(_step("maybes", title, False, "needs_attention", f"{chosen['maybe']:,} records of protocol "
                           f"{chosen['protocol']} are maybe. You may read them again (Start screening, records: "
                           "maybes) or decide them yourself on the Screening review page.",
                           f"{chosen['maybe']:,} maybes", tab="review"))
    else:
        steps.append(_step("maybes", title, False, "completed", "No maybes are left.", "no maybes left", tab="review"))

    open_req = next((s for s in steps if s["required"] and s["state"] != "completed"), None)
    if open_req is None:
        nxt = {"page": "recall", "tab": None, "label": "Estimate recall",
               "text": "The required screening steps are completed. Estimate recall against your validation labels on "
                       "the Recall and report page."}
    else:
        nxt = {"page": open_req["page"], "tab": open_req["tab"], "label": _next_label(open_req),
               "text": open_req["text"], "step": open_req["key"]}
    also = [s["text"] for s in steps if not s["required"] and s["state"] == "needs_attention"]
    return {"steps": steps, "next": nxt, "also": also, "required_done": open_req is None,
            "protocol": chosen["protocol"] if chosen else None, "rounds": rs, "pending_review": _pending(project)}


def _pending(project: Project) -> dict[str, Any] | None:
    from .review import pending

    try:
        return pending(project)
    except Exception:   # a damaged set must not break the Screening card
        return None


def _next_label(step: dict[str, Any]) -> str:
    if step["key"] == "prompt":
        return "Build the screening prompt"
    if step["key"] == "labels":
        return "Make the validation labels final" if step["state"] == "needs_attention" else "Label the validation sample"
    if step["key"] == "dev_labels":
        return "Continue labelling"
    if step["state"] == "in_progress" and "running" in step["short"]:
        return "See the running screening"
    if step["state"] == "in_progress":
        return "Finish screening the whole corpus"
    if step["tab"] == "review":
        return "Check the corpus decisions"
    if step["state"] == "needs_attention":
        return "Screen the whole corpus with a real model"
    return "Screen the whole corpus"


def needs_you(project: Project) -> str | None:
    """The Screening stage's ``needs_you`` (D4): the first open required step's short text."""
    try:
        prog = screen_progress(project)
    except Exception:   # a damaged run folder must not break the stage list
        return None
    s = next((s for s in prog["steps"] if s["required"] and s["state"] != "completed"), None)
    return s["short"] if s else None


def status_lines(prog: dict[str, Any]) -> list[str]:
    """`wider screen status` (the summary card's twin)."""
    out = ["Where screening stands:"]
    for s in prog["steps"]:
        out.append(f"  [{s['word']}] {s['title']}: {s['text']}")
    if prog["rounds"]:
        r = prog["rounds"][-1]
        out.append(f"Latest round: {r['name']}{' (test model)' if r['test'] else ''}: {r['ok']} of {r['n_items']} "
                   f"records, {r['failed']} failed; " + (f"{r['hidden']}." if r.get("hidden") else
                                                          f"{r['include']} include, {r['maybe']} maybe, {r['exclude']} exclude."))
        if r["unusual"]:
            out.append(f"  note: {r['unusual']}")
    for a in prog["also"]:
        out.append(f"You may also: {a}")
    from .review import pending_line

    line = pending_line(prog.get("pending_review"))
    if line:
        out.append(line)
    n = prog["next"]
    out.append(f"next: {n['label']} ({'Recall and report' if n['page'] == 'recall' else n['page']}): {n['text']}")
    return out
