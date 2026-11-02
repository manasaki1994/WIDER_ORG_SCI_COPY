"""The Review plan page under Node with the fake DOM (review-plan-and-question-window D1-D4): the task cards from
GET /api/plan, the focused task pages at #setup?task=<key>, the unknown task, mining on "more" only, the consent block
on the pages that send, and the router re-showing the plan."""
from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from tests.unit.test_query_roles import js_function, setup_page
from wider.core.project import Project
from wider.ui.app import create_app

APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
SETUP_JS = (STATIC / "setup.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")

TASKS = [
    {"key": "question", "title": "Research question", "optional": False, "state": "done", "line": "1 question saved",
     "questions": 0, "done": True},
    {"key": "seeds", "title": "Seed papers", "optional": False, "state": "waiting",
     "line": "3 seed papers: 2 development, 1 validation; 1 not found", "questions": 1, "done": True},
    {"key": "prior", "title": "Prior reviews", "optional": False, "state": "todo",
     "line": "add a prior review or say you know of none", "questions": 0, "done": False},
    {"key": "databases", "title": "Databases", "optional": False, "state": "done",
     "line": "1 platform, development on scopus", "questions": 0, "done": True},
    {"key": "more", "title": "Find more seed papers", "optional": True, "state": "optional", "line": "nothing waiting",
     "questions": 0, "done": False},
]
PLAN = {"tasks": TASKS, "ready": False, "summary": "complete the tasks above"}
SEED = {"rows": [], "rule": "", "sources": ["manual"], "dev_share": 0.6, "seed": 1}


def _page(hash_: str, body: str, plan: dict = PLAN) -> dict:
    """setup.js on the fake page at ``hash_``; ``renderSetupCards`` has run; ``body`` prints one JSON object."""
    replies = {"/api/plan": plan, "/api/benchmark/seed": SEED, "/api/benchmark/candidates": {"candidates": []},
               "/api/prior": {"reviews": [], "policy": {}}, "/api/platforms": {"chosen": [], "catalogue": []}}
    return run_node(f"""{ESC}
{PAGE}
global.location = {{ hash: {json.dumps(hash_)} }};
global.history = {{ replaceState(_, __, h) {{ location.hash = h; }} }};
const lookups = [];
global.renderLookupsCard = (el) => {{ lookups.push(el._name); }};
Object.assign(replies, {json.dumps(replies)});
{SETUP_JS}
await window.renderSetupCards(); await flush();
{body}""")


def test_review_plan_cards():
    """Scenario "The Review plan shows five task cards": each card links to its task page and shows the icon and the
    word of its state, its line and the count of open questions; the summary line follows; no task page is shown."""
    out = _page("#setup", """console.log(JSON.stringify({cards: $("#plan-cards").innerHTML,
      line: $("#plan-summary-line").innerHTML, plan: $("#plan-view").hidden,
      tasks: ["question", "seeds", "prior", "databases", "more"].map(k => $(`#task-${k}`).hidden)}));""")
    cards = re.findall(r'<li class="plan-card s-(\w+)" data-plan-task="(\w+)"><a href="([^"]+)">', out["cards"])
    assert cards == [("done", "question", "#setup?task=question"), ("waiting", "seeds", "#setup?task=seeds"),
                     ("todo", "prior", "#setup?task=prior"), ("done", "databases", "#setup?task=databases"),
                     ("optional", "more", "#setup?task=more")]
    seeds = out["cards"][out["cards"].index('data-plan-task="seeds"'):out["cards"].index('data-plan-task="prior"')]
    assert "Waiting for you" in seeds and "!" in seeds and "1 question from WIDER" in seeds and "1 not found" in seeds
    assert "Optional" in out["cards"] and "To do" in out["cards"] and "✓" in out["cards"]
    assert out["line"] == "Complete the tasks above." and out["plan"] is False and all(out["tasks"])


def test_review_plan_ready_line():
    ready = {**PLAN, "ready": True, "summary": "ready for the construct stage"}
    out = _page("#setup", 'console.log(JSON.stringify({line: $("#plan-summary-line").innerHTML}));', ready)
    assert out["line"] == "✓ Ready for the construct stage."


def test_task_addresses_and_back_next():
    """Scenario "A task opens on its own page": #setup?task=seeds shows Seed papers only, its window title names the
    task, "Next task" names Prior reviews; the last task has no Next; every task page leads back to the plan."""
    out = _page("#setup?task=seeds", """console.log(JSON.stringify({plan: $("#plan-view").hidden,
      tasks: ["question", "seeds", "prior", "databases", "more"].map(k => $(`#task-${k}`).hidden),
      next: $('[data-next="seeds"]').innerHTML, title: window.pageTitle}));""")
    assert out["plan"] is True and out["tasks"] == [True, False, True, True, True]
    assert out["next"] == 'Next task: <a href="#setup?task=prior">Prior reviews</a>'
    assert out["title"] == "Seed papers – Review plan – WIDER"
    last = _page("#setup?task=more", 'console.log(JSON.stringify({next: $(\'[data-next="more"]\').innerHTML}));')
    assert last["next"] == ""
    pages = re.findall(r'<div class="task-page" id="task-(\w+)" data-task="\1" hidden>(.*?)\n    </div>\n', INDEX, re.S)
    assert [k for k, _ in pages] == ["question", "seeds", "prior", "databases", "more"]
    for k, html in pages:
        assert '<a href="#setup">‹ Review plan</a>' in html, k
        assert ('data-next="' in html) == (k != "more"), k


def test_unknown_task_opens_plan():
    """Scenario "An unknown task address opens the plan": the address becomes #setup and the cards are shown."""
    out = _page("#setup?task=nonsense", """console.log(JSON.stringify({hash: location.hash, plan: $("#plan-view").hidden,
      seeds: $("#task-seeds").hidden, cards: $("#plan-cards").innerHTML.length}));""")
    assert out == {"hash": "#setup", "plan": False, "seeds": True, "cards": out["cards"]} and out["cards"] > 0


def test_seeds_page_has_no_mining():
    """Scenario "Seed papers without mining": the Seed papers task draws the list and the add forms, never the reference
    lists or the citation candidates; those are on Find more seed papers."""
    out = _page("#setup?task=seeds", 'console.log(JSON.stringify({html: $("#setup-seed").innerHTML, urls: urls()}));')
    assert "seed-chain" not in out["html"] and "seed-refs" not in out["html"]
    assert not any(u.startswith("/api/benchmark/candidates") for u in out["urls"])
    more = _page("#setup?task=more", 'console.log(JSON.stringify({urls: urls(), chain: $("#seed-chain").innerHTML}));')
    assert any(u.startswith("/api/benchmark/candidates") for u in more["urls"])
    task_more = INDEX[INDEX.index('id="task-more"'):]
    task_seeds = INDEX[INDEX.index('id="task-seeds"'):INDEX.index('id="task-prior"')]
    assert 'id="seed-chain"' in task_more and 'id="seed-refs"' in task_more and 'id="seed-chain"' not in task_seeds


def test_consent_on_sending_pages():
    """The online-lookup consent block is drawn on both pages that send DOIs or titles out (Seed papers and Find more
    seed papers), and not on the plan itself."""
    assert _page("#setup?task=seeds", "console.log(JSON.stringify({l: lookups}));")["l"] == ["#seed-lookups"]
    assert _page("#setup?task=more", "console.log(JSON.stringify({l: lookups}));")["l"] == ["#more-lookups"]
    assert _page("#setup", "console.log(JSON.stringify({l: lookups}));")["l"] == []


def _show_page(hashes: list[str]) -> dict:
    """app.js's showPage and hashchange listener with the rest stubbed; each hash in turn fires hashchange."""
    listener = re.search(r'^window\.addEventListener\("hashchange", (\(\) => \{.*?\})\);', APP_JS, re.M).group(1)
    return run_node(f"""
const PAGES = {{ home: "page-home", setup: "page-setup", sample: "page-sample" }};
let methodsSub = "", currentPage = "home", pagesShown = 0, lastHash = null;
const marked = [], shown = [];
global.location = {{ hash: "#home" }};
global.history = {{ replaceState(_, __, h) {{ location.hash = h; }} }};
global.window = global;
global.document = {{ querySelectorAll: () => [], querySelector: () => null }};
const $ = () => null;
global.markCurrentPage = (k) => marked.push(k);
const showLoadError = () => {{}}, pageLoader = () => null, renderStageResults = () => {{}};
{js_function(APP_JS, "function showPage(")}
const realShow = showPage;
showPage = (k) => {{ shown.push(location.hash); return realShow(k); }};
const onHash = {listener};
for (const h of {json.dumps(hashes)}) {{ location.hash = h; onHash(); }}
console.log(JSON.stringify({{ marked, shown }}));""")


def test_sidebar_current_on_task_pages():
    """Scenario "The sidebar marks Review plan on every task page": a task address marks the setup page current."""
    out = _show_page(["#setup", "#setup?task=seeds", "#setup?task=more"])
    assert out["marked"] == ["setup", "setup", "setup"]


def test_back_to_plan_redraws():
    """D2: from a task page back to #setup the page is shown again (the cards are redrawn), although the page key is
    the same; the same address twice is shown once."""
    out = _show_page(["#setup?task=seeds", "#setup", "#setup", "#setup?task=prior"])
    assert out["shown"] == ["#setup?task=seeds", "#setup", "#setup?task=prior"]


def test_plan_summary_folded(tmp_path):
    """Scenario "Plan summary folded": the Plan summary is a closed fold at the bottom of the plan, under the summary
    line; opened, renderSummary has drawn the store counts and the protocol hash into it."""
    plan_view = INDEX[INDEX.index('id="plan-view"'):INDEX.index('id="task-question"')]
    fold = re.search(r'<details class="plan-summary" id="plan-summary-fold">(.*?)</details>', plan_view, re.S)
    assert fold and "open" not in fold.group(0)[:60] and 'id="setup-summary"' in fold.group(1)
    assert plan_view.index('id="plan-cards"') < plan_view.index('id="plan-summary-line"') < fold.start()
    client = TestClient(create_app(Project.init(tmp_path / "r", questions=["Q?"], platforms=["scopus"])))
    summary = setup_page(client)["summary"]
    assert 'class="kv">store</th>' in summary and f"hash <code>{client.get('/api/protocol').json()['hash']}</code>" in summary
