"""Stage registry resolver: which stages are done, ready or blocked for a project."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .project import Project

STAGES_FILE = Path(__file__).resolve().parent.parent / "stages.yaml"


def load_sections(path: Path = STAGES_FILE) -> list[dict[str, Any]]:
    """The sections of the procedure, in order: ``{"number", "title"}`` from the registry's ``sections:`` list."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [{"number": int(s["number"]), "title": str(s["title"])} for s in data.get("sections", [])]


def load_stages(path: Path = STAGES_FILE) -> list[dict[str, Any]]:
    """The stages in order. An available stage must name a section of ``sections:``; an extension stage takes none."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {int(s["number"]) for s in data.get("sections", [])}
    out = []
    for st in data.get("stages", []):
        st = dict(st)
        if st.get("status") != "extension" and st.get("section") not in known:
            raise ValueError(f"stage {st.get('key')!r} has no known section (got {st.get('section')!r}; "
                             f"sections: {sorted(known)})")
        st.setdefault("requires", [])
        st.setdefault("produces", [])
        st.setdefault("done_when", [])
        st.setdefault("description", "")
        st.setdefault("why", "")
        st.setdefault("short", st.get("title", st["key"]))
        st.setdefault("commands", [st["command"]] if st.get("command") else [])
        st["used_by"] = list(st.get("used_by") or [])
        out.append(st)
    keys = {st["key"] for st in out}
    for st in out:   # a hand-written list: every name must be a stage (ui-redesign D14)
        for k in st["used_by"]:
            if k not in keys:
                raise ValueError(f"stage {st['key']}: unknown stage {k} in used_by")
    return out


def _check(project: Project, chk: dict[str, Any]) -> bool:
    kind, name = chk.get("check"), chk.get("name")
    st = project.store
    if kind == "table":
        if name not in st.tables():
            return False
        return (st.scalar(f"SELECT COUNT(*) FROM {name}") or 0) >= int(chk.get("min", 1))
    if kind == "artifact":
        if chk.get("frozen_query"):   # the latest freeze, not reopened by an unlock or a save (the query stage only)
            from ..construct.artifacts import frozen_query
            return frozen_query(project, name) is not None
        statuses = {"approved": ("approved", "frozen"), "frozen": ("frozen",)}.get(chk.get("status", "approved"), ("draft", "approved", "frozen"))
        q = ",".join("?" * len(statuses))
        return (st.scalar(f"SELECT COUNT(*) FROM artifacts WHERE kind=? AND status IN ({q})", (name, *statuses)) or 0) > 0
    if kind == "meta":
        return st.get_meta(name) is not None
    if kind == "enrich_settled":   # Missing abstracts: looked up, or nothing left to look up (never "not started")
        if "enrichment_log" in st.tables() and (st.scalar("SELECT COUNT(*) FROM enrichment_log") or 0) > 0:
            return True
        if "records" not in st.tables() or not st.scalar("SELECT COUNT(*) FROM records"):
            return False
        from ..enrich.fetch import candidates
        return not candidates(project)
    if kind == "config":
        v = getattr(project.config, name, None)
        return bool(v)
    raise ValueError(f"unknown stage check {kind!r}")


def _dedup_needs_you(project: Project) -> str | None:
    """Deduplication still has work for you while queue pairs wait (the condition of ``queue_rows(pending_only=True)``)."""
    st = project.store
    if "dedup_pairs" not in st.tables():
        return None
    n = st.scalar("SELECT COUNT(*) FROM dedup_pairs p JOIN raw_records a ON a.raw_id=p.raw_a "
                  "JOIN raw_records b ON b.raw_id=p.raw_b WHERE p.decision='queued'") or 0
    parts = [f"{n} pair{'' if n == 1 else 's'} to decide"] if n else []
    new = _unchecked_imports(project)
    if new:
        parts.append(f"{new} new record{'' if new == 1 else 's'} not checked for duplicates")
    return ", ".join(parts) or None


def _unchecked_imports(project: Project) -> int:
    """Records in included files imported since the last deduplication run (D32 F1); 0 with no run."""
    import json

    from ..dedup.engine import raw_count
    summary = json.loads(project.store.get_meta("dedup_summary") or "{}")
    if not summary:
        return 0
    return max(raw_count(project) - int(summary.get("total_records_imported") or 0), 0)


# A stage that is done can still have work for you (design-pass-a1-dedup D4); its state and done_when are unchanged.
def _screen_needs_you(project: Project) -> str | None:
    """Screening still has a required step open (screening-page-tasks D4): unlabelled validation records, or the whole
    corpus not yet screened by a real model."""
    from ..screen.progress import needs_you
    return needs_you(project)


def _sample_needs_you(project: Project) -> str | None:
    """Samples and labelling is not called completed while a development sample lacks final labels (D4); no count, so
    the words do not move between labels (blinding)."""
    from ..screen.devgate import open_samples
    return "development labels open" if open_samples(project) else None


def _questions_for(stage: str):
    """Open questions whose page belongs to ``stage`` (review-plan-and-question-window D13): "2 questions for you"."""
    def needs(project: Project) -> str | None:
        from .questions import in_stage, list_questions

        n = sum(1 for q in list_questions(project, "open", limit=10_000) if in_stage(q, stage))
        return f"{n} question{'' if n == 1 else 's'} for you" if n else None
    return needs


def _plan_needs_you(project: Project) -> str | None:
    """The Review plan's required cards not done yet (D32 F2): "Prior reviews to do". Only once the stage is done, so
    a fresh project's plan is not a list of everything."""
    from .plan import tasks
    left = [c["title"] for c in tasks(project) if not c["optional"] and not c["done"]]
    return ", ".join(f"{t} to do" for t in left) or None


def _recall_needs_you(project: Project) -> str | None:
    """Recall and report has no estimate yet for a reason you can clear (D32 F2): labels missing or a choice open in
    its last report. A report without a sample or protocol is a note on the page, not work here (it would hold the
    recommended step on a stage that cannot move)."""
    import json
    rep = json.loads(project.store.get_meta("last_report") or "{}")
    if rep.get("labels_incomplete"):
        return "labels missing for the recall estimate"
    if rep.get("choice_needed"):
        return "a choice before the recall estimate"
    return None


NEEDS_YOU = {"dedup": _dedup_needs_you, "sample": _sample_needs_you, "screen": _screen_needs_you,
             "recall": _recall_needs_you,
             "setup": _questions_for("setup"), "construct": _questions_for("construct"), "query": _questions_for("query")}
# These count in any state, not only once the stage is done (D13)
NEEDS_YOU_ANY_STATE = ("setup", "construct", "query")
# Added to the stage's needs-you once it is done (D32 F2)
NEEDS_YOU_DONE = {"setup": _plan_needs_you}
# A done stage whose needs-you moves the recommended step (D32 F1): work the researcher can clear on that page
MOVES_NEXT = ("dedup", "screen", "recall")


def _screen_waits(project: Project) -> str | None:
    """Screening not done yet waits while a development sample lacks final labels (D4): the same words as
    :func:`_sample_needs_you`, no count."""
    return _sample_needs_you(project)


# A stage that is not done can wait for work elsewhere; its state is unchanged (ui-redesign 4.4)
WAITS = {"screen": _screen_waits}


def _rank_review(project: Project) -> tuple[str | None, list[dict[str, Any]]]:
    """Ranking needs review (ui-redesign D5): its words and the versions behind them."""
    from ..rank.versions import review_items, review_text
    items = review_items(project)
    return review_text(items), items


NEEDS_REVIEW = {"rank": _rank_review}

# The red line on a stage whose results come from the test model only (D32 F3), on the page and in `wider status`
TEST_MODEL_LINE = "These results come from the test model, not from a real one: do not use them for your review."


def _test_model(project: Project) -> dict[str, bool]:
    """Stages whose results come only from the test model (D32 F3): Ranking when every ranking's embedding set is the
    test kind, Screening when every screening run is a test run, Recall and report when Screening is."""
    st = project.store
    out = {"rank": False, "screen": False, "recall": False}
    try:
        if "rankings" in st.tables() and "embedding_sets" in st.tables():
            kinds = [r["kind"] for r in st.query("SELECT e.kind FROM rankings r LEFT JOIN embedding_sets e "
                                                 "ON e.set_name=r.set_name")]
            out["rank"] = bool(kinds) and all(k == "fake" for k in kinds)
        if "runs" in st.tables():
            from ..screen.progress import _test_runs
            tests = _test_runs(project)
            ids = [r["run_id"] for r in st.query("SELECT run_id FROM runs WHERE kind='screen'")]
            out["screen"] = out["recall"] = bool(ids) and all(tests.get(i) for i in ids)
    except Exception:   # noqa: BLE001  a damaged table must not break the stage list
        pass
    return out


def state_text(s: dict[str, Any]) -> str:
    """The state as `wider status` prints it: "done, needs you: 3 pairs to decide" when the stage has work for you,
    "done, needs review: ..." when its inputs changed since (ui-redesign D5)."""
    out = s["state"]
    if s.get("test_model"):
        out += ", test model"
    if s.get("needs_you"):
        out += f", needs you: {s['needs_you']}"
    if s.get("needs_review"):
        out += f", needs review: {s['needs_review']}"
    if s.get("waits"):
        out += f", waits: {s['waits']}"
    return out


def stage_states(project: Project) -> list[dict[str, Any]]:
    """Each stage with ``state`` in {done, ready, blocked, extension} and the unmet requirements.
    Each stage also has ``needs_you``: a short text when the stage is done but still has work for you (in any state
    for the Literature search stages with open questions), else None, and
    ``needs_review`` with ``review`` (the items behind it): a done stage whose inputs changed since (Ranking only,
    ui-redesign D5), and ``waits``: a stage not done yet that waits for work elsewhere (Screening while development
    labels are open, 4.4). None of them changes the state."""
    stages = load_stages()
    test = _test_model(project)
    done: dict[str, bool] = {}
    out = []
    for st in stages:
        if st.get("status") == "extension":
            done[st["key"]] = False
            out.append({**st, "state": "extension", "missing": [], "needs_you": None, "needs_review": None,
                        "review": [], "waits": None, "test_model": False})
            continue
        is_done = bool(st["done_when"]) and all(_check(project, c) for c in st["done_when"])
        done[st["key"]] = is_done
        missing = [r for r in st["requires"] if not done.get(r, False)]
        state = "done" if is_done else ("ready" if not missing else "blocked")
        needs = (NEEDS_YOU[st["key"]](project) if st["key"] in NEEDS_YOU
                 and (state == "done" or (st["key"] in NEEDS_YOU_ANY_STATE and state != "extension")) else None)
        if state == "done" and st["key"] in NEEDS_YOU_DONE:
            needs = ", ".join(x for x in (needs, NEEDS_YOU_DONE[st["key"]](project)) if x) or None
        review, items = NEEDS_REVIEW[st["key"]](project) if state == "done" and st["key"] in NEEDS_REVIEW else (None, [])
        waits = WAITS[st["key"]](project) if state != "done" and st["key"] in WAITS else None
        out.append({**st, "state": state, "missing": missing, "needs_you": needs, "needs_review": review,
                    "review": items, "waits": waits, "test_model": state == "done" and test.get(st["key"], False)})
    return out


# The Model for query creation page (`wider providers`): not a stage, but the recommended step while no construct
# model is set (ui-redesign D8); every stage keeps its own state
MODEL_PAGE = {"key": "model", "title": "Model for query creation", "command": "providers", "page": True,
              "state": "ready", "missing": [], "needs_you": None, "needs_review": None, "review": [], "waits": None,
              "test_model": False, "description": "Choose the language model that proposes the concepts, synonyms, anchor sentences and "
                             "codebook from your question and seed papers. Where it runs and what it is sent is "
                             "shown before you choose.",
              "why": "Query creation needs a model, and its choice decides where your question and seed paper abstracts "
                     "are sent."}


def _passes_over_query(project: Project, states: dict[str, dict[str, Any]]) -> bool:
    """Records are imported and no search is frozen: Search strings is optional and passed over (ui-redesign D8)."""
    return states.get("import", {}).get("state") == "done" and states.get("query", {}).get("state") != "done"


def _past_query_creation(states: list[dict[str, Any]]) -> bool:
    """A stage from Search results (``import``) onward is done."""
    keys = [s["key"] for s in states]
    start = keys.index("import") if "import" in keys else len(keys)
    return any(s["state"] == "done" for s in states[start:])


def _open_work(st: dict[str, Any]) -> bool:
    """A done stage with work you can clear on its page (D32 F1); a running screening is not such work."""
    return (st["state"] == "done" and st["key"] in MOVES_NEXT and bool(st.get("needs_you"))
            and "running" not in (st.get("needs_you") or ""))


def next_stage(project: Project, states: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """The recommended step, shared by `wider next`, `wider status`, Continue, the sidebar and the footer (ui-redesign
    D8): the Model for query creation page (key ``model``, command ``providers``) while no construct model is set and
    no stage from Search results onward is done (a project past query creation is not sent back to it; audit F8);
    otherwise the first ready stage or done stage with open work (D32 F1), passing over Search strings while records are imported and no search is frozen
    (it stays ready and optional), and Samples and labelling in place of a later stage while a development sample
    is incomplete (D4: screening waits for its labels). None when no stage is ready."""
    states = states if states is not None else stage_states(project)
    if not project.config.stage_providers.get("construct") and not _past_query_creation(states):
        return dict(MODEL_PAGE)
    by_key = {s["key"]: s for s in states}
    skip_query = _passes_over_query(project, by_key)
    first = next((st for st in states if (st["state"] == "ready" and not (skip_query and st["key"] == "query"))
                  or _open_work(st)), None)
    keys = [s["key"] for s in states]
    if "sample" in by_key and (first is None or keys.index(first["key"]) > keys.index("sample")):
        from ..screen.devgate import open_samples

        if open_samples(project):
            return by_key["sample"]
    return first


def route_sentence(project: Project, states: list[dict[str, Any]] | None = None) -> str:
    """The route from the project's state (ui-redesign D8), as the Overview and `wider status` say it. Concepts and
    synonyms is never called optional: ranking needs its anchor sentences and screening its codebook."""
    states = states if states is not None else stage_states(project)
    by_key = {s["key"]: s for s in states}
    if _passes_over_query(project, by_key):
        return ("You started from records you already retrieved: writing search strings is optional (record the "
                "outside search for reproducibility if you can). The Review plan and Concepts and synonyms "
                "are still needed: ranking reads the anchor sentences and screening the codebook. Seed paper coverage "
                "is checked once the uploads are marked final.")
    if by_key.get("query", {}).get("state") == "done":
        return ("The search strings are frozen; the records come from the searches they record. Seed paper coverage "
                "is checked once the query is frozen or the uploads are marked final.")
    return ("The steps run in order: question and seed papers, concepts and synonyms, search strings, then the search "
            "results you bring back. If you already have a search strategy or records, start from them: Search "
            "strings or Search results.")
