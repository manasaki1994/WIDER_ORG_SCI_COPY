"""The Review plan's task cards (review-plan-and-question-window D3): computed from project.yaml, the benchmark and
the store, never stored, so re-answering a task is always possible. The route `GET /api/plan` and `wider plan` both
read `tasks()`; the done rules and lines are the setup interview's (formerly `interviewSteps` in app.js)."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .project import Project

# the state words of a card ("Status shown by word and icon"); "Waiting for you" as on the Deduplication chip
STATE_WORDS = {"done": "Done", "todo": "To do", "waiting": "Waiting for you", "optional": "Optional"}
STATE_ICONS = {"done": "✓", "todo": "○", "waiting": "!", "optional": "–"}
READY = "ready for the construct stage"
NOT_READY = "complete the tasks above"


def _s(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _seed_line(project: Project, unresolved: int) -> str:
    from ..search.coverage import (
        NOT_COUNTED,
        development_papers,
        query_results_papers,
        used_papers,
        validation_papers,
    )

    bm = project.read_benchmark()
    if not bm:
        return "no seed papers yet"
    dev, val = len(development_papers(project, bm)), len(validation_papers(project, bm))
    used, apart = len(used_papers(project, bm)), len(query_results_papers(project, bm))
    line = (f"{_s(len(bm), 'seed paper')}: {dev} development, {val} validation"
            + (f", {used} used validation" if used else "") + (f"; {apart} {NOT_COUNTED}" if apart else "")
            + ("" if dev else " (assign some to the development set)"))
    return line + (f"; {unresolved} not found" if unresolved else "")


def _prior(project: Project) -> tuple[bool, str]:
    from ..prior.reviews import summary

    try:
        pr = summary(project)
    except Exception:   # noqa: BLE001 - the card says so instead of failing the page
        return False, "prior reviews not readable"
    pol = pr["policy"] or {}
    if pr["n_reviews"]:
        return True, (f"{_s(pr['n_reviews'], 'prior review')}, {pr['n_papers']} papers"
                      + ("; OpenAlex lookup on" if pol.get("allow_openalex_lookup") else "; local only"))
    if pol.get("none_known"):
        return True, "no prior review known"
    return False, "add a prior review or say you know of none"


def _databases(project: Project) -> tuple[bool, str]:
    from ..search.catalogue import project_platforms

    plats = project_platforms(project)
    if not plats:
        return False, "no databases chosen"
    dev = next((p for p in plats if p["role"] == "development"), plats[0])
    return True, f"{_s(len(plats), 'platform')}, development on {dev['key']}"


def _candidates(project: Project) -> int:
    from ..seeds import chain

    return sum(1 for c in chain.candidates(project) if c["status"] == "new")


def tasks(project: Project) -> list[dict[str, Any]]:
    """The five cards in order, ``{key, title, optional, state, line, questions, done}``; an open question about a task
    sets it to ``waiting``. Validation seed papers are counted, never named."""
    from .questions import list_questions

    open_qs: dict[str, list[dict]] = {}
    for q in list_questions(project, "open", limit=10_000, page="setup"):
        task = q["page"].partition("task=")[2] or "question"
        open_qs.setdefault(task, []).append(q)
    questions = [x for x in project.config.questions if x.strip()]
    seed_done = False
    try:
        from ..search.coverage import development_papers

        seed_done = bool(development_papers(project, project.read_benchmark()))
    except Exception:   # noqa: BLE001
        pass
    unresolved = sum(1 for q in open_qs.get("seeds", []) if q["kind"] == "seed_unresolved")
    prior_done, prior_line = _prior(project)
    db_done, db_line = _databases(project)
    n_cand = _candidates(project)
    cards = [
        ("question", "Research question", False, bool(questions),
         _s(len(questions), "question") + " saved" if questions else "no research question yet"),
        ("seeds", "Seed papers", False, seed_done, _seed_line(project, unresolved)),
        ("prior", "Prior reviews", False, prior_done, prior_line),
        ("databases", "Databases", False, db_done, db_line),
        ("more", "Find more seed papers", True, False,
         f"{_s(n_cand, 'candidate')} to look at" if n_cand else "nothing waiting"),
    ]
    out = []
    for key, title, optional, done, line in cards:
        n = len(open_qs.get(key, []))
        state = "waiting" if n else "optional" if optional else "done" if done else "todo"
        out.append({"key": key, "title": title, "optional": optional, "state": state, "line": line, "questions": n,
                    "done": done})
    return out


def ready(cards: list[dict[str, Any]]) -> bool:
    """Ready for the construct stage when the four required tasks are done by their rules; an open question shows on
    the card but does not undo a done task."""
    return all(c["done"] for c in cards if not c["optional"])


def summary_line(cards: list[dict[str, Any]]) -> str:
    return READY if ready(cards) else NOT_READY


def card_line(c: dict[str, Any]) -> str:
    """``wider plan``'s line: "Done · Databases · 2 platforms, development on scopus"."""
    return f"{STATE_WORDS[c['state']]} · {c['title']} · {c['line']}"
