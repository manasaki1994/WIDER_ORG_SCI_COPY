"""Guide text shipped with WIDER (design-pass-a1-dedup D11, ui-redesign D6): one Markdown file per page under
``pages/``, and the six answers every page inside a project gives, in this order: Where you are, What to do, Why it
matters, What you need, Done when, What comes next, then Watch out where written. "Where you are" and "What comes next"
come from the stage registry and the project's state; the other four are the shipped Markdown, in the page's button
words. Before them comes "What you can do here now" (ui-redesign D14), and the notices of the named states the project
is in replace the general text on the panel, as before. Only stage and page names and counts are shown, never a
record. No model and no online service."""
from __future__ import annotations

import re
from importlib import resources
from typing import TYPE_CHECKING, Any

from ..core.stages import TEST_MODEL_LINE

if TYPE_CHECKING:
    from ..core.project import Project

HEADINGS = ("Where you are", "What to do", "Why it matters", "What you need", "Done when", "What comes next")
WRITTEN = ("What to do", "Why it matters", "What you need", "Done when")   # from the Markdown, all required
WATCH_OUT = "Watch out"                                                     # from the Markdown, where written
ROLE = "Page role"                                                          # pages outside the stages: their role
CAN_DO = "What you can do here now"
_ITEM = re.compile(r"^(?:-|\d+\.)\s+(.*)$")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
NO_PAGE = "there is no such page: {page}"
# bold names that are emphasis or a concept, not something on the page (guide-assistant D13): no "Show" link
NOT_SHOWN: frozenset[str] = frozenset({
    "Next task",          # setup: the words before the link to the next task, whose text is that task's title
})
# bold names that are a heading on the page, not a control: the guide's "Show" points at the heading, the assistant
# names no such "button" (guide-assistant M123, 31 Oct: "Needs attention" was offered as a button)
NOT_BUTTONS: frozenset[str] = frozenset({
    "Needs attention", "Start screening", "Where screening stands", "Keyboard", "Status marks",
    "Page names and commands",
})


def pages() -> list[str]:
    """The pages that have shipped guide text."""
    return sorted(p.name[:-3] for p in resources.files(__package__).joinpath("pages").iterdir() if p.name.endswith(".md"))


def _parts(page: str) -> tuple[str, list[dict]]:
    """The page's title and its parts with the items as written (``**`` kept)."""
    if page not in pages():
        raise KeyError(page)
    title, parts = "", []
    for line in resources.files(__package__).joinpath("pages", f"{page}.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            parts.append({"heading": line[3:].strip(), "items": []})
        elif (m := _ITEM.match(line)) and parts:
            parts[-1]["items"].append(m[1].strip())
        elif line.strip() and parts and parts[-1]["items"]:   # a wrapped line continues the item above
            parts[-1]["items"][-1] += " " + line.strip()
    return title, parts


def buttons(page: str) -> dict[str, list[str]]:
    """Per written part, the ``**bold**`` names in its items, in order and once each (guide-assistant D13);
    ``KeyError`` for a page without text."""
    out = {}
    for p in _parts(page)[1]:
        names = [m for i in p["items"] for m in _BOLD.findall(i)]
        out[p["heading"]] = list(dict.fromkeys(n.strip() for n in names))
    return out


def button_registry() -> dict[str, list[str]]:
    """Page → its bold names that are controls (not in ``NOT_SHOWN`` or ``NOT_BUTTONS``): what the assistant may offer
    to show."""
    return {pg: list(dict.fromkeys(n for names in buttons(pg).values() for n in names
                                   if n not in NOT_SHOWN and n not in NOT_BUTTONS))
            for pg in pages()}


def guide_text(page: str) -> dict:
    """``{page, title, role, parts: [{heading, items}]}`` read from ``pages/<page>.md`` (the written parts only);
    ``KeyError`` for a page without text. Items keep their words; ``**`` around a button name is dropped."""
    title, parts = _parts(page)
    for p in parts:
        p["items"] = [i.replace("**", "") for i in p["items"]]
    role = next((" ".join(p["items"]) for p in parts if p["heading"] == ROLE), None)
    return {"page": page, "title": title, "role": role, "parts": [p for p in parts if p["heading"] != ROLE]}


# ------------------------------------------------------------------------------------------- generated answers
def _state_words(s: dict[str, Any], recommended: str | None) -> str:
    """The sidebar's words for the stage (shell.js stateText): "needs attention: ..." when done with work for you,
    the reason after "not started" when it waits for work elsewhere. Review plan, Concepts and Search strings need
    you in any state while a question about them is open (review-plan-and-question-window D13)."""
    if s.get("needs_you") and s["state"] != "extension":
        return f"needs attention: {s['needs_you']}"
    if s["state"] == "done":
        return "completed"
    out = "not started, work here next" if s["state"] == "ready" and s["key"] == recommended else "not started"
    return f"{out}: {s['waits']}" if s.get("waits") else out


def can_do(project: Project, page: str, states: list[dict[str, Any]], recommended: dict[str, Any] | None) -> str:
    """"What you can do here now" (ui-redesign D14), from the stage's state: work here; completed but changeable,
    naming the later steps that read its result; frozen and read only, with Unlock and what it means; not yet, with
    what it needs. The page always stays open. Pages outside the stages say they can be used at any time."""
    by_key = {s["key"]: s for s in states}
    rec = recommended["key"] if recommended else None
    if page == "model":
        name = project.config.stage_providers.get("construct")
        if name:
            return f"A model for query creation is set ({name}). You can change it here at any time; nothing reruns."
        default = project.config.default_provider
        if any(p.name == default for p in project.config.providers):
            return (f"Choose the model for query creation here: it is the recommended next step. Until you do, the "
                    f"project default ({default}) writes the proposals.")
        return "Choose the model for query creation here: it is the recommended next step."
    s = by_key.get(page)
    if s is None or s.get("status") == "extension":
        return "This page is outside the stages: you can use it at any time."
    titles = {x["key"]: x["title"] for x in states}
    extra = ""
    if s.get("needs_you"):
        extra += f" It needs you: {s['needs_you']}."
    if s.get("needs_review"):
        extra += " It needs review: the inputs changed since it was made (see Needs attention)."
    waits = f" It waits: {s['waits']}." if s.get("waits") else ""
    if page == "enrich" and s["state"] == "done" and not project.store.scalar("SELECT COUNT(*) FROM enrichment_log"):
        extra += " Nothing was looked up: no record has a missing or thin abstract, so there is nothing to fetch."
    if page == "query" and s["state"] == "done":
        return ("Frozen: the search strings can be read and copied; later steps use this search. Unlock to change it "
                "(on Concepts and synonyms): unlocking asks whether the validation papers already shown count as seen; "
                "the ones not shown stay validation. Your answer is recorded in History, the methods text and the "
                "package." + extra)
    if s["state"] == "done":
        used = [titles[k] for k in s.get("used_by") or [] if k in titles]
        reads = f" Changing this reruns nothing; these steps read its result: {', '.join(used)}." if used else ""
        test = f" {TEST_MODEL_LINE}" if s.get("test_model") else ""
        if s.get("needs_you"):   # D32 F2: the box agrees with the chip; never "Completed" while work is left
            rest = extra.replace(f" It needs you: {s['needs_you']}.", "")
            return f"Not finished: it needs you: {s['needs_you']}.{test}{reads}{rest}"
        return f"Completed, and you can still change it.{test}{reads}{extra}"
    imported = by_key.get("import", {}).get("state") == "done"
    if page == "import" and s["state"] == "blocked" and set(s["missing"]) <= {"query"}:
        # a records start (ui-redesign D8): Search results is open before any search string is written
        return ("You can import the records you already have now: importing them before writing search strings is "
                "fine, and writing search strings then becomes optional.")
    if s["state"] == "blocked":
        need = ", ".join(titles.get(k, k) for k in s["missing"])
        out = f"Not yet: this step needs {need} first. You can read the page meanwhile.{waits}"
        return out + (f" {_STRATEGY} {_RECORDS_FIRST}" if page == "query" else "")
    if page == "query" and imported:
        return ("You can work here, but you do not have to: records are already imported, so writing search strings "
                "is optional. Recording the outside search here keeps it reproducible: import the string as a draft (Import a "
                "string; wider query import).")
    if s.get("waits"):   # demo-chapters-and-video D9: a step that waits is not "Ready", nor the step to work on
        out = f"Everything this step needs is done, but it waits for this: {s['waits']}."
    else:
        # "not started" as the chip says, never "Ready" beside a "not started" chip (owner inspection, 1 Nov 2026)
        out = ("Not started yet: everything this step needs is done, and it is the recommended next step." if page == rec
               else "Not started yet: everything this step needs is done, so you can start here.")
    return out + (f" {_STRATEGY} {_RECORDS_FIRST}" if page == "query" else "")


# the strategy and records starts (ui-redesign D8): nothing about the start is stored, so Search strings always says it
_STRATEGY = ("If you have a search strategy already, import the string as a draft (Import a string; wider query "
             "import): the Review plan and Concepts and synonyms are still needed.")
_RECORDS_FIRST = "If you already have the records, importing them on Search results before writing search strings is fine."


def where_you_are(page: str, g: dict, states: list[dict[str, Any]], recommended: dict[str, Any] | None) -> str:
    from ..core.stages import load_sections

    s = next((x for x in states if x["key"] == page), None)
    if s is None or s.get("status") == "extension":
        role = f": {g['role']}" if g.get("role") else ""
        return f"{g['title']} is a project page outside the stages{role}"
    sections = {x["number"]: x["title"] for x in load_sections()}
    in_section = [x for x in states if x.get("section") == s["section"]]
    i = in_section.index(s) + 1
    return (f"Section {s['section']}, {sections.get(s['section'], '')}: {s['title']}, step {i} of {len(in_section)} in "
            f"this section ({_state_words(s, recommended['key'] if recommended else None)}).")


def what_comes_next(page: str, states: list[dict[str, Any]], recommended: dict[str, Any] | None) -> str:
    core = [x for x in states if x.get("status") != "extension"]
    s = next((x for x in core if x["key"] == page), None)
    if s is None:   # a page outside the stages goes back to the recommended step
        return f"Back to {recommended['title']}." if recommended else "Every step is done: back to Recall and report."
    i = core.index(s)
    nxt = core[i + 1] if i + 1 < len(core) else None
    out = (f"Continue to {nxt['title']}." if nxt else
           "This is the last step: the report and the exports are here and on the Results dashboard.")
    if recommended and recommended["key"] not in (page, nxt["key"] if nxt else None):
        out += f" The recommended step now is {recommended['title']}."
    return out


def guide(project: Project, page: str) -> dict:
    """The page's guide for this project: ``{page, title, can_do, parts (the six answers, then Watch out where
    written, then the page's own further parts; each ``{heading, items, buttons}``), notices, methods}``; ``KeyError`` for an unknown page. Page, API and CLI show this one dict."""
    from ..core.stages import next_stage, stage_states

    g = guide_text(page)
    states = stage_states(project)
    rec = next_stage(project, states)
    written = {p["heading"]: p["items"] for p in g["parts"]}
    parts = [{"heading": "Where you are", "items": [where_you_are(page, g, states, rec)]}]
    parts += [{"heading": h, "items": written.get(h, [])} for h in WRITTEN[:2]]
    parts += [{"heading": h, "items": written.get(h, [])} for h in WRITTEN[2:]]
    parts.append({"heading": "What comes next", "items": [what_comes_next(page, states, rec)]})
    if written.get(WATCH_OUT):
        parts.append({"heading": WATCH_OUT, "items": written[WATCH_OUT]})
    # a page's own further parts, as written (the help page's "Ask WIDER", guide-assistant 9.2)
    parts += [{"heading": h, "items": items} for h, items in written.items() if h not in (*WRITTEN, WATCH_OUT)]
    names = buttons(page)
    for p in parts:   # the panel's "Show" links: one list per answer, controls only (D13)
        p["buttons"] = [n for n in names.get(p["heading"], []) if n not in NOT_SHOWN]
    return {"page": page, "title": g["title"], "can_do": can_do(project, page, states, rec), "parts": parts,
            "notices": notices(project, page), "methods": [t["id"] for t in _methods().for_page(page)]}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def notices(project: Project, page: str) -> list[str]:
    """The notices of the named states the project is in, with its own numbers (Deduplication only)."""
    if page != "dedup":
        return []
    from ..dedup.engine import flow_counts, last_queue

    st = project.store
    flow = flow_counts(project)
    if not flow["has_run"]:
        return []
    out = []
    n = flow["to_decide"]
    if n:
        out.append(f"{_plural(n, 'pair waits', 'pairs wait')} for your decision. Decide them, then press Apply "
                   "decisions and check again.")
    # files whose records the last run has not clustered yet: exact also for an import in the run's own second
    r = st.one("SELECT COUNT(DISTINCT r.source_id) n FROM raw_records r JOIN sources s ON s.source_id=r.source_id "
               "WHERE r.raw_id NOT IN (SELECT raw_id FROM dedup_clusters)")
    files = int(r["n"]) if r else 0
    if files:
        out.append(f"You added {_plural(files, 'file', 'files')} since the last run. Press Apply decisions and check again.")
    if not flow["removed"] and not last_queue(project) and not files:
        out.append("No duplicates were found. Check that every database's export is uploaded.")
    return out


def _methods():
    """The methods document (imported late: it reads protocol defaults on first use)."""
    from . import methods

    return methods
