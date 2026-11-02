"""shell.js runs under Node on a stand-in (no browser): the backend-check line names each backend's uses, so a
stage-override backend shows as "LLM b (screen)"; the sidebar is grouped under the registry sections with the state
words and tooltips of the spec, the page headings follow the registry and the later stages are hidden. The whole
shell.js runs on the stand-in page of ``test_backends_js_node`` (elements made on demand, requests answered from
``replies``)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_backends_js_node import ESC, PAGE
from tests.unit.test_backends_js_node import run_node as run_async
from tests.unit.test_screen_compare import _template, cproject  # noqa: F401  (pytest fixtures)

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
ROOT = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")


def shell_helpers() -> str:
    """``locChip`` through ``infoRows`` from shell.js, the part that renders the project summary."""
    js = (STATIC / "shell.js").read_text(encoding="utf-8")
    return js[js.index("  const locChip"):js.index("  function projectCard")]


def run_node(body: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    stubs = ("const esc = (s) => String(s == null ? '' : s); const h = esc; const LOC_WORD = {};"
             "const stageTitle = (s) => s; const fmtWhen = (t) => t;")
    script = f"{stubs}\n{shell_helpers()}\n{body}"
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_backend_check_shows_uses():
    summary = {"seed": {"n": 0}, "tokens": {}, "cost": None, "last_run": None, "root": "/r", "records": 0,
               "raw_records": 0, "backends": {"llm": None, "embedding": None},
               "backend_check": {"at": "t", "ok": False, "results": [
                   {"role": "llm", "name": "a", "uses": ["default", "construct"], "ok": True},
                   {"role": "llm", "name": "b", "uses": ["screen"], "ok": False, "error": "wrong answer"},
                   {"role": "embedding", "name": "e", "ok": True}]}}
    out = run_node(f"console.log(JSON.stringify({{html: infoRows({json.dumps(summary)})}}));")
    html = out["html"]
    assert "LLM a (default, construct) <span" in html and "LLM b (screen) <span" in html
    assert "embeddings e <span" in html


# ------------------------------------------------------------------ the whole shell.js on the stand-in page

APP = (STATIC / "app.js").read_text(encoding="utf-8")
PAGES_LINE = next(line for line in APP.splitlines() if line.startswith("const PAGES = "))

SHELL_GLOBALS = PAGES_LINE + r"""
let STAGES = [], NEXT_KEY = null, LAST_RESULTS = {}, SECTIONS = [], PROJECT_TITLE = "", PROJECT_KEY = null, CONSTRUCT_BACKEND = null;
let currentPage = "home";
const shown = [];
const stageTitle = (k) => { const s = STAGES.find(x => x.key === k); return s ? s.title : k; };
const oneLine = (s) => String(s || "").split(/\s+/).join(" ").trim();
const fmtWhen = (t) => t;
const storage = {};
global.localStorage = { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); } };
// the one name of common.js (ui-redesign D33): its top bar slot and once-only question are tested in test_name_once_node
global.WiderName = { NEED: "enter your name first: use Change beside “Working as” in the top bar",
  get: () => String(storage["wider.reviewer"] || "").trim(), render() {}, askOnce() {}, ask() {}, set() {}, needHtml: (l) => esc(l) };
global.innerWidth = 1200;
global.matchMedia = () => ({ matches: false, addEventListener() {} });
global.location = { hash: "", pathname: "/", reloads: 0, reload() { this.reloads++; } };
global.history = { replaceState(a, b, u) { location.hash = u && u.startsWith("#") ? u : ""; } };
global.showPage = (k) => { shown.push(k); currentPage = k; };
global.Backends = { checkBannerText: () => "", settingsBackendsHtml: () => "" };
document.querySelectorAll = (sel) => [];
"""


def stages(states: dict, missing: dict | None = None) -> list[dict]:
    """The real registry with the given states (others blocked); extension stages stay "extension"."""
    from wider.core.stages import load_stages

    out = []
    for st in load_stages():
        state = "extension" if st.get("status") == "extension" else states.get(st["key"], "blocked")
        out.append({k: st[k] for k in ("key", "title", "short", "command", "section") if k in st}
                   | {"state": state, "missing": (missing or {}).get(st["key"], []), "description": "", "why": ""})
    return out


def shell_page(body: str, stage_rows: list[dict], nxt: str | None, replies: dict | None = None,
               title: str = "Synthetic project") -> dict:
    from wider.core.stages import load_sections

    setup = (f"STAGES = {json.dumps(stage_rows)}; NEXT_KEY = {json.dumps(nxt)}; SECTIONS = {json.dumps(load_sections())};"
             f"PROJECT_TITLE = {json.dumps(title)}; Object.assign(replies, {json.dumps(replies or {})});")
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    return run_async(f"{ESC}\n{PAGE}\n{SHELL_GLOBALS}\n{setup}\n{shell}\nawait flush();\n{body}")


LINK = r"""const links = () => [...String($("#stage-links").innerHTML).matchAll(/<a class="nav-link[^"]*" href="#([\w-]+)" data-key="[^"]*" title="([^"]*)">[\s\S]*?<span class="label">([^<]*)<\/span>(?:(?:(?!<\/a>)[\s\S])*?<span class="sr-only">, ([^<]*)<\/span>)?/g)].map(m => ({ key: m[1], tip: m[2], label: m[3], word: m[4] || null }));"""


def test_sidebar_state_words():
    """Two stages ready, one done although a stage it requires is not, one blocked: only the next stage reads "not
    started, work here next", the other "not started", the done stage reads "completed" with no "(needs ...)", and only
    the blocked stage's tooltip names what it needs first (screening-page-tasks D8)."""
    rows = stages({"setup": "done", "construct": "ready", "query": "ready", "dedup": "done"},
                  {"dedup": ["import"], "enrich": ["dedup", "import"]})
    out = shell_page(f"{LINK} renderShell(); console.log(JSON.stringify(links()));", rows, "construct")
    by = {x["key"]: x for x in out}
    assert by["construct"]["word"] == "not started, work here next" and by["query"]["word"] == "not started"
    assert [x["key"] for x in out if x["word"] == "not started, work here next"] == ["construct"]
    assert by["dedup"]["word"] == "completed" and "(needs" not in by["dedup"]["tip"]
    assert by["enrich"]["word"] == "not started"
    assert by["enrich"]["tip"] == "Missing abstracts: not started (needs Deduplication, Search results first)"
    titles = {r["key"]: r["title"] for r in rows}
    core = [r["key"] for r in rows if r["state"] != "extension"]
    for key in core:   # no step number in the tooltip (owner, 30 Oct, D32 F16)
        assert by[key]["tip"].startswith(f"{titles[key]}: ")
    assert sum("(needs" in x["tip"] for x in out) == 1
    # every link shows its task's own icon and no status mark; the journey line carries the marks (owner, 29 Oct)
    html = shell_page("renderShell(); console.log(JSON.stringify($('#stage-links').innerHTML));", rows, "construct")
    assert "st-icon" not in html and 'class="dot"' not in html and "nav-needs" not in html
    assert html.count('<svg class="icon" aria-hidden="true"><use href="#i-') == html.count('class="nav-link')


def test_sidebar_sections():
    """The links sit under the four section headings in order, with the model page first in section 1."""
    rows = stages({"setup": "ready"})
    out = shell_page(f"{LINK} renderShell(); console.log(JSON.stringify({{html: $('#stage-links').innerHTML, links: links()}}));",
                     rows, "setup")
    html = out["html"]
    heads = ["1 Literature search", "2 Data cleaning", "3 Model tuning and validation", "4 Screening and outputs"]
    pos = [html.index(f'<span class="sec-t">{t}</span>') for t in heads]
    assert pos == sorted(pos)
    keys = [x["key"] for x in out["links"]]
    assert keys == ["model", "setup", "construct", "query", "import", "dedup", "enrich", "rank", "sample", "screen",
                    "recall"]
    assert out["links"][0]["label"] == "Model for query creation" and out["links"][0]["word"] is None
    sec = lambda k: next(i for i, p in enumerate(pos + [len(html)]) if html.index(f'href="#{k}"') < p) - 1  # noqa: E731
    assert [sec(k) for k in keys] == [0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3]
    assert "Model for query creation" in html[pos[0]:pos[1]] and 'data-key="model"' in html


def test_results_link_after_recall():
    """"Results dashboard" is the last link of section 4, after "Recall and report", with its icon, no state word and
    no step number; on #results the link is the current page, section 4 is
    current, and the breadcrumb reads "Results dashboard" with no step. The collapsed rail keeps its name (title and
    label)."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import")})
    links = r"""const fake = (key) => { const a = { dataset: { key }, attrs: {}, cls: new Set(),
      classList: { toggle(c, on) { on ? a.cls.add(c) : a.cls.delete(c); } },
      setAttribute(k, v) { a.attrs[k] = v; }, removeAttribute(k) { delete a.attrs[k]; } }; return a; };
    let navs = [];
    document.querySelectorAll = (sel) => sel === ".sidebar .nav-link" ? navs : [];"""
    out = shell_page(f"{links}\n{TOPBAR}\nrenderShell(); const html = $('#stage-links').innerHTML;"
                     "navs = [...String(html).matchAll(/data-key=\"([\\w-]+)\"/g)].map(m => fake(m[1]));"
                     "show('results');"
                     "console.log(JSON.stringify({html, bar: bar(), sec4: $('#nav-sec-4').className,"
                     " cur: navs.filter(a => a.attrs['aria-current'] === 'page').map(a => a.dataset.key)}));",
                     rows, "dedup", title="Practice review")
    html = out["html"]
    sec4 = html[html.index('id="nav-sec-4"'):]
    assert sec4.index('href="#recall"') < sec4.index('href="#results"') < sec4.index('href="#reporting"')
    assert sec4.rstrip().endswith("</div></div>")
    link = sec4[sec4.index('href="#results"') - 40:]
    link = link[link.index("<a "):link.index("</a>")]
    assert link.startswith('<a class="nav-link" data-key="results" href="#results" title="Results dashboard">')
    assert '<use href="#i-results"/>' in link and '<span class="label">Results dashboard</span>' in link
    assert "sr-only" not in link and "dot" not in link and "n. " not in link
    assert html.count('href="#results"') == 1 and re.findall(r'data-key="([\w-]+)"', sec4)[-3:] == ["recall", "results", "reporting"]
    assert re.findall(r'<span class="label">([^<]*)</span>', sec4) == ["Screening", "Screening review", "Recall and report",
                                                                       "Results dashboard", "PRISMA reporting"]
    assert out["cur"] == ["results"]
    assert out["bar"]["crumb"] == "Practice review Switch project / Results dashboard"
    assert out["sec4"] == "nav-section-title sec-current"



def test_reporting_link_after_results():
    """prisma-reporting-assistant 7.2: "PRISMA reporting" is the last link of section 4, right after "Results
    dashboard", with its icon, no state word and no step number; on #reporting it is the current page, section 4 is
    current, and the breadcrumb reads "PRISMA reporting" with no step."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import")})
    links = r"""const fake = (key) => { const a = { dataset: { key }, attrs: {}, cls: new Set(),
      classList: { toggle(c, on) { on ? a.cls.add(c) : a.cls.delete(c); } },
      setAttribute(k, v) { a.attrs[k] = v; }, removeAttribute(k) { delete a.attrs[k]; } }; return a; };
    let navs = [];
    document.querySelectorAll = (sel) => sel === ".sidebar .nav-link" ? navs : [];"""
    out = shell_page(f"{links}\n{TOPBAR}\nrenderShell(); const html = $('#stage-links').innerHTML;"
                     "navs = [...String(html).matchAll(/data-key=\"([\\w-]+)\"/g)].map(m => fake(m[1]));"
                     "show('reporting');"
                     "console.log(JSON.stringify({html, bar: bar(), sec4: $('#nav-sec-4').className,"
                     " cur: navs.filter(a => a.attrs['aria-current'] === 'page').map(a => a.dataset.key)}));",
                     rows, "dedup", title="Practice review")
    html = out["html"]
    sec4 = html[html.index('id="nav-sec-4"'):]
    assert sec4.index('href="#recall"') < sec4.index('href="#results"') < sec4.index('href="#reporting"')
    assert sec4.rstrip().endswith("</div></div>")
    start = sec4.rindex("<a ", 0, sec4.index('href="#reporting"'))
    link = sec4[start:sec4.index("</a>", start)]
    assert link.startswith('<a class="nav-link" data-key="reporting" href="#reporting" title="PRISMA reporting">')
    assert '<use href="#i-reporting"/>' in link and '<span class="label">PRISMA reporting</span>' in link
    assert "sr-only" not in link and "dot" not in link and "n. " not in link
    assert html.count('href="#reporting"') == 1 and re.findall(r'data-key="([\w-]+)"', sec4)[-3:] == ["recall", "results", "reporting"]
    assert re.findall(r'<span class="label">([^<]*)</span>', sec4) == ["Screening", "Screening review", "Recall and report",
                                                                       "Results dashboard", "PRISMA reporting"]
    assert out["cur"] == ["reporting"]
    assert out["bar"]["crumb"] == "Practice review Switch project / PRISMA reporting"
    assert out["sec4"] == "nav-section-title sec-current"

def test_review_link_after_screening():
    """"Screening review" follows "Screening" in section 4 and goes to #review, with its icon, no state words and no
    "n. "; its tooltip names the records that need a person (from /api/review/summary), "no screening decisions yet"
    without decisions; it is not counted as a step; on #review section 4 is current and the breadcrumb reads
    "Screening review" with no step (manual-review D1)."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import")})
    body = r"""const fake = (key) => { const a = { dataset: { key }, attrs: {}, cls: new Set(),
      classList: { toggle(c, on) { on ? a.cls.add(c) : a.cls.delete(c); } },
      setAttribute(k, v) { a.attrs[k] = v; }, removeAttribute(k) { delete a.attrs[k]; } }; return a; };
    let navs = [];
    document.querySelectorAll = (sel) => sel === ".sidebar .nav-link" ? navs : [];
    renderShell(); await flush(); const live = $('#stage-links a[data-key="review"]').title;
    renderShell(); const html = $('#stage-links').innerHTML;
    navs = [...String(html).matchAll(/data-key="([\w-]+)"/g)].map(m => fake(m[1]));
    show('review');
    console.log(JSON.stringify({ html, live, bar: bar(), sec4: $('#nav-sec-4').className,
      cur: navs.filter(a => a.attrs['aria-current'] === 'page').map(a => a.dataset.key) }));"""
    pending = {"/api/review/summary": {"pending_review": {"count": 4, "protocol": "main", "set": 1}}}
    out = shell_page(f"{TOPBAR}\n{body}", rows, "dedup", replies=pending, title="Practice review")
    html = out["html"]
    sec4 = html[html.index('id="nav-sec-4"'):]
    assert re.findall(r'data-key="([\w-]+)"', sec4)[:2] == ["screen", "review"]
    link = sec4[sec4.index('data-key="review"') - 40:]
    link = link[link.index("<a "):link.index("</a>")]
    assert link.startswith('<a class="nav-link" data-key="review" href="#review" aria-label="Screening review" title="4 records need a person">')
    assert out["live"] == "4 records need a person"
    assert '<use href="#i-review"/>' in link and '<span class="label">Screening review</span>' in link
    assert "sr-only" not in link and "dot" not in link and "n. " not in link and "state-" not in link
    assert out["cur"] == ["review"] and out["sec4"] == "nav-section-title sec-current"
    assert out["bar"]["crumb"] == "Practice review Switch project / Screening review"
    one = shell_page(f"{LINK} renderShell(); await flush(); console.log(JSON.stringify($('#stage-links a[data-key=\"review\"]').title));",
                     rows, "dedup", replies={"/api/review/summary": {"pending_review": {"count": 1}}})
    none = shell_page(f"{LINK} renderShell(); await flush(); console.log(JSON.stringify($('#stage-links a[data-key=\"review\"]').title));",
                      rows, "dedup", replies={"/api/review/summary": {"pending_review": None}})
    assert (one, none) == ("1 record needs a person", "no screening decisions yet")
    assert '<symbol id="i-review"' in (STATIC / "index.html").read_text(encoding="utf-8")


SEC = r"""const secs = () => [...String($("#stage-links").innerHTML).matchAll(/<p class="nav-section-title sec-(\w+)" id="nav-sec-(\d)">([\s\S]*?)<span class="sec-t">[^<]*<\/span><span class="sr-only">, ([^<]*)<\/span>/g)].map(m => ({ n: +m[2], cls: m[1], icon: m[3].includes("st-icon"), word: m[4] }));"""


def test_sidebar_section_marks():
    """Section 1 all done and the Deduplication page current: section 1 reads "completed" (no mark, owner 29 Oct),
    section 2 is current, sections 3 and 4 upcoming (design-pass-a1-dedup D4)."""
    sec1 = ["setup", "construct", "query", "import"]
    rows = stages({k: "done" for k in sec1} | {"dedup": "ready"})
    out = shell_page(f"{SEC} currentPage = 'dedup'; renderShell(); console.log(JSON.stringify(secs()));", rows, "dedup")
    assert [(x["n"], x["cls"], x["word"]) for x in out] == [(1, "done", "completed"), (2, "current", "current"),
                                                            (3, "upcoming", "upcoming"), (4, "upcoming", "upcoming")]
    assert not any(x["icon"] for x in out)
    # off the stage pages (Home), the section of the next stage is the current one; a section with one stage not
    # done is not done
    rows = stages({k: "done" for k in sec1[:3]} | {"import": "ready"})
    out = shell_page(f"{SEC} currentPage = 'home'; renderShell(); console.log(JSON.stringify(secs()));", rows, "import")
    assert [x["cls"] for x in out] == ["current", "upcoming", "upcoming", "upcoming"]


def test_section_with_a_done_stage_is_not_upcoming():
    """Whole-app inspection, 1 Nov 2026: a section away from the next step with a completed stage in it reads "partly
    done", never "upcoming" beside a completed Screening."""
    sec1 = ["setup", "construct", "query", "import"]
    rows = stages({k: "done" for k in sec1} | {"dedup": "done", "enrich": "ready", "screen": "done"})
    out = shell_page(f"{SEC} currentPage = 'home'; renderShell(); console.log(JSON.stringify(secs()));", rows, "enrich")
    sec_of = {r["key"]: r.get("section") for r in rows}
    by = {x["n"]: x for x in out}
    assert (by[sec_of["screen"]]["cls"], by[sec_of["screen"]]["word"]) == ("partial", "partly done")
    assert by[sec_of["enrich"]]["cls"] == "current" and by[1]["cls"] == "done"


def test_sidebar_needs_you_mark():
    """Dedup done with needs_you: the link keeps its task icon with no mark, reads "needs attention: 3 pairs to
    decide", and the footer counts one thing that needs you."""
    rows = stages({"setup": "done", "dedup": "done"})
    for r in rows:
        r["needs_you"] = "3 pairs to decide" if r["key"] == "dedup" else None
    out = shell_page(f"{LINK} renderShell(); console.log(JSON.stringify({{links: links(), html: $('#stage-links').innerHTML,"
                     f" foot: $('#nav-foot').innerHTML}}));", rows, "construct")
    by = {x["key"]: x for x in out["links"]}
    assert by["dedup"]["word"] == "needs attention: 3 pairs to decide"
    assert by["dedup"]["tip"] == "Deduplication: needs attention: 3 pairs to decide"
    link = out["html"][out["html"].index('href="#dedup"'):]
    link = link[:link.index("</a>")]
    assert '<svg class="icon" aria-hidden="true"><use href="#i-dedup"/>' in link and "st-icon" not in out["html"]
    assert out["html"].count("needs-you") == 1   # only dedup has the class
    assert by["setup"]["word"] == "completed"
    assert "1 thing needs you" in out["foot"]


def test_sidebar_in_progress_while_a_job_runs():
    """A screening job runs: the Screening link and the page chip read "in progress"; a blocked stage's chip reads
    "not started: needs <titles> first" and a done stage with work left "needs attention: <reason>"
    (screening-page-tasks D8)."""
    rows = stages({"setup": "done", "dedup": "done", "screen": "done"}, {"recall": ["screen", "sample"]})
    for r in rows:
        r["needs_you"] = "3 pairs to decide" if r["key"] == "dedup" else None
    body = (f"{LINK} global.Jobs = {{ runningStages: () => new Set(['screen']) }}; renderShell();"
            "const st = (k) => stageStatus(STAGES.find(s => s.key === k));"
            "console.log(JSON.stringify({links: links(), screen: st('screen'), recall: st('recall'), dedup: st('dedup')}));")
    out = shell_page(body, rows, "construct")
    by = {x["key"]: x for x in out["links"]}
    assert by["screen"]["word"] == "in progress" and out["screen"] == {"cls": "running", "word": "in progress",
                                                                       "text": "in progress"}
    assert out["recall"]["text"] == "not started: needs Screening, Samples and labelling first"
    assert out["dedup"]["text"] == "needs attention: 3 pairs to decide" and out["dedup"]["cls"] == "needs"


def test_sidebar_footer_needs_and_next():
    """The first four of the ten available stages done, Deduplication with 3 pairs to decide and Missing abstracts
    next: the footer reads "1 thing needs you" (a link to the Overview) and "Next: Missing abstracts", with no "steps
    done" text and no bar; two open questions show 2 on the Questions link. On the Overview only "Next:" shows;
    with nothing waiting it reads "Nothing waits for you." (ui-redesign D11)."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup")} | {"enrich": "ready"})
    for r in rows:
        r["needs_you"] = "3 pairs to decide" if r["key"] == "dedup" else None
    replies = {"/api/questions/summary": {"open": 2, "latest_open": 7, "mode": "queue"}}
    body = ("CONSTRUCT_BACKEND = 'ollama'; renderShell(); await flush(); markCurrentPage('dedup');"
            "const a = $('#nav-foot').innerHTML, ah = $('#nav-foot').hidden; markCurrentPage('overview');"
            "console.log(JSON.stringify({a, ah, ov: $('#nav-foot').innerHTML, n: $('#q-count').textContent,"
            " qh: $('#q-count').hidden}));")
    out = shell_page(body, rows, "enrich", replies=replies)
    assert out["ah"] is False
    assert re.search(r'<a class="nav-foot-needs" href="#overview"[^>]*>.*1 thing needs you', out["a"])
    assert 'Next: <a href="#enrich">Missing abstracts</a>' in out["a"]
    assert "steps done" not in out["a"] and "meter" not in out["a"] and "width:" not in out["a"]
    assert "needs you" not in out["ov"] and 'Next: <a href="#enrich">' in out["ov"]
    assert str(out["n"]) == "2" and out["qh"] is False
    # nothing waiting, no open questions: "Nothing waits for you." and the count is hidden
    for r in rows:
        r["needs_you"] = None
    out = shell_page("CONSTRUCT_BACKEND = 'ollama'; renderShell(); await flush(); markCurrentPage('dedup');"
                     "console.log(JSON.stringify({f: $('#nav-foot').innerHTML, qh: $('#q-count').hidden}));", rows,
                     "enrich", replies={"/api/questions/summary": {"open": 0, "mode": "queue"}})
    assert "Nothing waits for you." in out["f"] and out["qh"] is True


def test_footer_counts_dev_gate_item():
    """The footer counts the Overview's D4 item (an incomplete development sample with model decisions on it) as one
    thing that needs you, so its count matches the Overview's list (ui-tester finding, 29 Oct)."""
    from wider.screen.devgate import GATE_TEXT

    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup")} | {"enrich": "ready"})
    for r in rows:
        r["needs_you"] = "3 pairs to decide" if r["key"] == "dedup" else None
    item = {"stage": "sample", "title": "Samples and labelling", "kind": "dev_gate", "text": GATE_TEXT}
    body = ("CONSTRUCT_BACKEND = 'ollama'; renderShell(); markCurrentPage('dedup'); await flush();"
            "console.log(JSON.stringify({f: $('#nav-foot').innerHTML}));")
    out = shell_page(body, rows, "enrich", replies={"/api/project/summary": _overview_summary(dev_gate=item)})
    assert "2 things need you" in out["f"]
    out = shell_page(body, rows, "enrich", replies={"/api/project/summary": _overview_summary()})
    assert "1 thing needs you" in out["f"]


def test_sidebar_needs_review_mark():
    """Ranking done with needs_review: its link keeps its task icon with no mark, and its words read
    "needs review: the records or their abstracts changed since this ranking"; the page chip has the same words and
    the class state-review (ui-redesign D5)."""
    why = "the records or their abstracts changed since this ranking"
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup", "enrich", "rank")})
    for r in rows:
        r["needs_review"] = why if r["key"] == "rank" else None
    body = (f"{LINK} renderShell(); const st = stageStatus(STAGES.find(s => s.key === 'rank'));"
            "console.log(JSON.stringify({links: links(), html: $('#stage-links').innerHTML, st,"
            " foot: $('#nav-foot').innerHTML}));")
    out = shell_page(body, rows, "sample")
    by = {x["key"]: x for x in out["links"]}
    assert by["rank"]["word"] == f"needs review: {why}"
    link = out["html"][out["html"].index('href="#rank"'):]
    link = link[:link.index("</a>")]
    assert '<svg class="icon" aria-hidden="true"><use href="#i-rank"/>' in link and "st-icon" not in link
    assert out["html"].count("needs-review") == 1 and "needs-you" not in out["html"]
    assert out["st"] == {"cls": "review", "word": "needs review", "text": f"needs review: {why}"}
    assert "1 thing needs you" in out["foot"]
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".state-chip.state-review" in css


def test_page_headings_from_registry():
    """A registry title for `rank` of "Ranked records" names the Ranking page's heading, sidebar link and the top
    bar's breadcrumb."""
    rows = stages({"setup": "done", "rank": "ready"})
    for r in rows:
        if r["key"] == "rank":
            r["title"] = r["short"] = "Ranked records"
    out = shell_page(f"{LINK} renderShell(); markCurrentPage('rank');"
                     "console.log(JSON.stringify({h1: $('#page-rank .page-head h1').textContent,"
                     " setup: $('#page-setup .page-head h1').textContent, links: links(), step: $('#tb-crumb').innerHTML,"
                     " project: $('#nav-project').textContent}));", rows, "rank", title="Renamed synthetic project")
    assert out["h1"] == "Ranked records" and out["setup"] == "Review plan"
    assert next(x for x in out["links"] if x["key"] == "rank")["label"] == "Ranked records"
    assert '<span class="pg" aria-current="page">Ranked records</span>' in out["step"]
    assert out["project"] == "Renamed synthetic project"


def test_later_stages_hidden():
    """No sidebar link for the later stages (full text, extraction, synthesis); the top bar has no step number (owner,
    30 Oct, D32 F16)."""
    rows = stages({"setup": "done"})
    later = [r["key"] for r in rows if r["state"] == "extension"]
    assert set(later) >= {"fulltext", "extract", "synthesis"}
    out = shell_page(f"{LINK} renderShell(); markCurrentPage('setup');"
                     "console.log(JSON.stringify({links: links()}));", rows, "construct")
    keys = {x["key"] for x in out["links"]}
    for k in later:
        assert k not in keys
    assert 'id="tb-step"' not in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "Step ${" not in (STATIC / "shell.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------ top bar (ui-redesign D3, D6)
TOPBAR = r"""const strip = (s) => String(s).replace(/<ul[\s\S]*?<\/ul>/g, "").replace(/<[^>]*>/g, " ").split(/\s+/).join(" ").trim();
const bar = () => ({ hidden: $('#topbar').hidden, home: $('#tb-home').getAttribute('aria-current') || null,
  crumbHidden: $('#tb-crumb').hidden, crumb: strip($('#tb-crumb').innerHTML), crumbHtml: $('#tb-crumb').innerHTML,
  notebook: $('#tb-notebook').innerHTML, guide: $('#tb-guide').innerHTML, main: $('#tb-main').innerHTML, questions: !$('#tb-questions').hidden });
let nbCount = 3, clicked = [];
const DEDUP = { notebook: { label: "Notebook", count: () => nbCount, expanded: () => false, onclick: () => clicked.push("nb") },
  main: { label: () => "Apply decisions and check again", onclick: () => clicked.push("main") } };
const show = (k) => { currentPage = k; markCurrentPage(k); };
// attributes that remember, on the elements the top bar sets them on (the stand-in forgets them)
for (const sel of ["#tb-home", "#sw-btn"]) { const a = {}; $(sel).setAttribute = (k, v) => { a[k] = String(v); };
  $(sel).getAttribute = (k) => (k in a ? a[k] : null); $(sel).removeAttribute = (k) => { delete a[k]; }; }
$('#tb-home').classList = { add() {}, remove() {} };"""


def test_topbar_dedup():
    """The Deduplication page of "Practice review" after a run: "All projects", "Practice review / Deduplication" with
    "Switch project" on the title (no step number), the Notebook (with its count), the labelled Guide button with its
    state and the main button; each button calls the page's handler."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup")})
    out = shell_page(f"{TOPBAR} renderShell(); show('dedup'); Topbar.set('dedup', DEDUP);"
                     "$('#tb-main-btn').onclick(); $('#tb-notebook-btn').onclick();"
                     "console.log(JSON.stringify({bar: bar(), clicked, all: $('#tb-home').innerHTML || 'static'}));",
                     rows, "enrich", title="Practice review")
    b = out["bar"]
    assert b["hidden"] is False and b["home"] is None and b["crumbHidden"] is False
    assert b["crumb"] == "Practice review Switch project / Deduplication"
    assert 'id="sw-btn" aria-expanded="false"' in b["crumbHtml"]
    assert 'aria-label="Open project: Practice review. Switch project"' in b["crumbHtml"]
    assert '<span aria-current="page"' not in b["crumbHtml"] and 'class="pg" aria-current="page">Deduplication<' in b["crumbHtml"]
    assert 'id="tb-notebook-btn"' in b["notebook"] and "Notebook" in b["notebook"] and '<span class="tb-count">3</span>' in b["notebook"]
    assert 'aria-expanded="false"' in b["notebook"]
    # the shared Guide button (owner, 29 Oct: Deduplication uses the shared guide panel, ui-redesign D28)
    assert 'id="tb-guide-btn"' in b["guide"] and 'aria-expanded=' in b["guide"] and "</svg>Guide</button>" in b["guide"]
    assert 'class="tb-main-btn"' in b["main"] and "Apply decisions and check again" in b["main"]
    assert b["questions"] is True and out["clicked"] == ["main", "nb"]
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<a class="tb-btn" id="tb-home" href="#home" title="All projects">(<svg[\s\S]*?</svg>)'
                     r'<span class="tb-lbl">All projects</span></a>', html)


def test_topbar_fits_1024_with_both_actions():
    """Audit F1 (29 Oct 2026): at 1024 px on Deduplication, with Notebook and "Apply decisions and check again" shown,
    the project switcher overlapped "Step 5 of 10". Below 1240 px the brand and the "All projects" and "Switch project"
    words leave the bar (kept as the link's name and the button's label); the switcher shrinks inside the breadcrumb
    and the step and the actions do not shrink, so nothing is drawn over anything else."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".switch { position:relative; min-width:0; flex:0 1 auto; }" in css
    assert re.search(r"\.tb-actions, #tb-questions, #tb-guide, \.top \.tb-help, #tb-home \{ flex-shrink:0; \}", css)
    wide = css.split("@media (max-width: 1240px) {", 1)[1].split("\n}", 1)[0]
    assert ".top .brand { display:none; }" in wide and "#tb-home .tb-lbl" in wide and ".switch .swl { display:none; }" in wide


def test_topbar_page_not_through_pass():
    """The Ranking page: "All projects", the breadcrumb, "Switch project" and "Guide", no Notebook and no main
    button; non-stage pages have the breadcrumb and the Guide button."""
    rows = stages({"setup": "done"})
    out = shell_page(f"{TOPBAR} renderShell(); show('rank'); const r = bar(); show('help');"
                     "console.log(JSON.stringify({rank: r, help: bar()}));", rows, "construct", title="Practice review")
    r = out["rank"]
    assert r["crumb"] == "Practice review Switch project / Ranking"
    assert r["notebook"] == r["main"] == "" and 'id="tb-guide-btn"' in r["guide"]
    assert out["help"]["crumb"] == "Practice review Switch project / Help"
    assert 'id="tb-guide-btn"' in out["help"]["guide"]


def test_topbar_home():
    """Home: "All projects" is marked as the current page; no breadcrumb, switcher, step, Questions or Guide button."""
    rows = stages({"setup": "done"})
    out = shell_page(f"{TOPBAR} renderShell(); show('rank'); const r = bar(); show('home');"
                     "console.log(JSON.stringify({rank: r, home: bar()}));", rows, "construct", title="Practice review")
    h = out["home"]
    assert h["hidden"] is False and h["home"] == "page" and out["rank"]["home"] is None
    assert h["crumbHidden"] is True and h["crumbHtml"] == "" and "sw-btn" not in h["crumbHtml"]
    assert h["guide"] == "" and h["questions"] is False
    # with no project open the bar still stands, with All projects only
    out = shell_page(f"{TOPBAR} renderShell(); show('home'); console.log(JSON.stringify(bar()));", rows, None, title="")
    assert out["hidden"] is False and out["home"] == "page" and out["crumbHidden"] is True


def test_switch_project_menu():
    """The switcher lists the recent projects with how many items need the researcher and the next step, then "All
    projects and new review"; choosing another project opens it (POST /api/projects/open, the bare address, reload), so
    the project opens on its Overview; the open project goes to its Overview; Esc closes the menu and returns the focus
    to its button."""
    home = {"projects": [
        {"title": "A", "root": "/a", "current": True, "needs_you": [{"title": "Deduplication"}], "next": {"title": "Missing abstracts"}},
        {"title": "B", "root": "/b", "current": False, "needs_you": [], "next": {"title": "Search strings"}}],
        "missing": [], "recent_persisted": True}
    body = TOPBAR + r"""
replies["/api/projects/open"] = { ok: true };
renderShell(); show('rank'); location.hash = "#rank";
await $('#sw-btn').onclick(); await flush();
const open = { expanded: $('#sw-btn').getAttribute('aria-expanded'), hidden: $('#sw-list').hidden, items: strip($('#sw-list').innerHTML.replace(/<li class="sep"[^>]*><\/li>/, "|")),
  current: (String($('#sw-list').innerHTML).match(/<a [^>]*aria-current="true"[^>]*>([^<]*)/) || [])[1] };
// Esc on the switcher closes it and the focus goes back to its button
let stopped = false;
$('#tb-switch').onkeydown({ key: "Escape", preventDefault() {}, stopPropagation() { stopped = true; } });
const escd = { expanded: $('#sw-btn').getAttribute('aria-expanded'), hidden: $('#sw-list').hidden, back: document.activeElement === $('#sw-btn'), stopped };
// the open project: its Overview
await $('#sw-btn').onclick(); await flush();
$('#sw-list').querySelectorAll('[data-sw]')[0].onclick({ preventDefault() {} }); await flush();
const own = { hash: location.hash, shown: shown.slice(-1)[0], hidden: $('#sw-list').hidden, reloads: location.reloads };
// another project: opened, bare address, reload (it then opens on its Overview, test_open_on_overview)
await $('#sw-btn').onclick(); await flush();
await $('#sw-list').querySelectorAll('[data-sw]')[1].onclick({ preventDefault() {} }); await flush();
const other = { posted: calls.filter(c => c[0] === "/api/projects/open").map(c => c[2].path), hash: location.hash, reloads: location.reloads };
console.log(JSON.stringify({ open, esc: escd, own, other }));"""
    out = shell_page(body, stages({"setup": "done"}), "construct", {"/api/home": home}, title="A")
    assert out["open"] == {"expanded": "true", "hidden": False, "current": "A",
                           "items": "A 1 needs you · Next: Missing abstracts B Next: Search strings | All projects and new review"}
    assert out["esc"] == {"expanded": "false", "hidden": True, "back": True, "stopped": True}
    assert out["own"]["hash"] == "overview" or out["own"]["hash"] == "#overview"
    assert out["own"]["hidden"] is True and out["own"]["reloads"] == 0
    assert out["other"] == {"posted": ["/b"], "hash": "", "reloads": 1}


def test_journey_and_sidebar_list_the_same_steps():
    """Owner, 30 Oct (D32 F16): the journey line and the sidebar list the same pages in the same order: the model page,
    the stages, Screening review after Screening, and the Results dashboard and PRISMA reporting after Recall."""
    rows = stages({"setup": "done"})
    out = shell_page("CONSTRUCT_BACKEND = 'fake'; renderShell(); markCurrentPage('dedup');"
                     "console.log(JSON.stringify({side: $('#stage-links').innerHTML, line: $('#route').innerHTML}));", rows, "construct")
    line = re.findall(r'<li class="jst[^"]*"><a href="#([a-z]+)"', out["line"])
    assert line == re.findall(r'data-key="([\w-]+)"', out["side"])
    assert line[0] == "model" and line[line.index("screen") + 1] == "review"
    assert line[line.index("recall") + 1:line.index("recall") + 3] == ["results", "reporting"]


def test_topbar_slots_per_page():
    """The Dedup buttons belong to the Dedup page: after moving to Ranking there is no main or Notebook button (the
    shared Guide button stays), and back on Dedup they return; a page that sets its config while another page is shown does not draw it there."""
    rows = stages({"setup": "done"})
    out = shell_page(f"{TOPBAR} renderShell(); show('dedup'); Topbar.set('dedup', DEDUP); const d = bar(); show('rank');"
                     "const r = bar(); show('dedup'); const d2 = bar(); show('rank'); Topbar.set('dedup', DEDUP);"
                     "console.log(JSON.stringify({d, r, d2, r2: bar()}));", rows, "construct")
    assert "tb-main-btn" in out["d"]["main"] and "tb-main-btn" in out["d2"]["main"] and "Notebook" in out["d2"]["notebook"]
    for k in ("r", "r2"):
        assert out[k]["main"] == out[k]["notebook"] == "" and 'id="tb-guide-btn"' in out[k]["guide"]


def test_topbar_survives_refresh():
    """On Dedup, a decision and then the stage refresh (refreshStages redraws the shell): the main button and the
    Notebook stay, and the Notebook count is read again (3 to 4)."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup")})
    out = shell_page(f"{TOPBAR} renderShell(); show('dedup'); Topbar.set('dedup', DEDUP); nbCount = 4; renderShell();"
                     "console.log(JSON.stringify(bar()));", rows, "enrich")
    assert "Apply decisions and check again" in out["main"] and '<span class="tb-count">4</span>' in out["notebook"]


# ------------------------------------------------------------------ home page

def _summary(root, **kw) -> dict:
    """The summary of a synthetic project, as /api/home sends it (plus ``current``)."""
    from wider.core.models import ProviderConfig
    from wider.core.project import Project
    from wider.core.summary import project_summary, summary_lines

    p = Project.init(root, platforms=["scopus"], title=kw.get("title", "Synthetic review"))
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake-1"))
    p.config.providers.append(ProviderConfig(name="fake2", kind="fake", model="fake-2"))
    p.config.default_provider = "fake"
    p.config.stage_providers["screen"] = "fake2"
    p.save()
    s = project_summary(p)
    lines = dict(summary_lines(s))
    p.close()
    return json.loads(json.dumps(s)) | {"current": kw.get("current", False)}, lines


HOME = "await loadHome(); await flush();"


def test_home_card_next_step(tmp_path):
    """Scenario "Progress line on the card": 7 of 10 stages done, Missing abstracts next and Deduplication with 3 pairs
    to decide: the card reads "1 needs you" and "Next: Missing abstracts" (plain text), never "7 of 10 stages done", a
    segmented bar or "Stage 2 of 10"; a project whose duplicate check runs shows the running job and what needs the
    researcher, both; Home has no backend banner and no notes search; Remove from list is disabled on the open one."""
    cur, _ = _summary(tmp_path / "a", title="Open one", current=True)
    other, _ = _summary(tmp_path / "b", title="Other one")
    quiet, _ = _summary(tmp_path / "c", title="Quiet one")
    needs = [{"stage": "dedup", "title": "Deduplication", "kind": "needs_you", "text": "3 pairs to decide"}]
    for s in (cur, other):
        s.update(needs_you=needs, need_you_text="1 needs you", n_done=7, stage_position=8,
                 next={"key": "enrich", "title": "Missing abstracts", "command": "enrich"})
    other["running"] = [{"label": "Duplicate check", "stage": "dedup"}]
    home = {"projects": [cur, other, quiet], "missing": [], "recent_persisted": True}
    out = shell_page(f"{HOME} console.log(JSON.stringify({{html: $('#home-projects').innerHTML, urls: urls(), empty: $('#home-empty').hidden}}));",
                     stages({}), None, {"/api/home": home, "/api/stages": {"stages": [], "next": None}})
    html = out["html"]
    cards = html.split("<article")[1:]
    assert len(cards) == 3 and out["empty"] is True
    strip = lambda c: " ".join(__import__("re").sub(r"<[^>]*>", " ", c).split())  # noqa: E731
    for card in cards[:2]:
        assert "1 needs you" in strip(card) and "Next: Missing abstracts" in strip(card)
        assert '<p class="pc-next">Next: Missing abstracts</p>' in card   # plain text, not a link
        assert "stages done" not in card and "Stage " not in card and "segbar" not in card and 'class="seg' not in card
        assert ">Open<span" in card and "> Info</button>" in card
    assert "Running: Duplicate check" in strip(cards[1]) and "1 needs you" in strip(cards[1])
    assert "Running" not in cards[0]
    assert "Nothing waits for you" in strip(cards[2]) and "needs you" not in strip(cards[2])
    assert "Next: Model for query creation" in strip(cards[2])
    # D32 F19: the open project offers no Remove button at all, the others do
    assert "Remove from list" not in cards[0] and "Remove from list</button>" in cards[1]
    assert "data-forget" not in cards[0] and 'data-forget="' + other["root"] + '"' in cards[1]
    assert "disabled" not in cards[1]
    assert not any(u.startswith("/api/notes") for u in out["urls"])
    html_page = (STATIC / "index.html").read_text(encoding="utf-8")
    home_sec = html_page[html_page.index('id="page-home"'):html_page.index("</section>", html_page.index('id="page-home"'))]
    for gone in ('id="home-banner"', 'id="whats-next"', 'id="an-search"'):
        assert gone not in home_sec, gone


def test_home_demo_link():
    """Scenario "The demo link": one link "Explore the demo" with target _blank and rel noopener noreferrer to
    the demo address in docs/guide.md, next to the sentence that the demo is separate from your projects; loading Home
    makes no request to that address."""
    import re
    guide = (ROOT / "docs" / "guide.md").read_text(encoding="utf-8")
    url = re.search(r"interactive demo at <(https://[^>]+)>", guide)[1]
    html_page = (STATIC / "index.html").read_text(encoding="utf-8")
    home_sec = html_page[html_page.index('id="page-home"'):html_page.index("</section>", html_page.index('id="page-home"'))]
    links = re.findall(r"<a [^>]*>Explore the demo", home_sec)
    assert len(links) == 1 and html_page.count("Explore the demo") == 1
    assert f'href="{url}"' in links[0] and 'target="_blank"' in links[0] and 'rel="noopener noreferrer"' in links[0]
    note = home_sec[home_sec.index('id="demo-note"'):home_sec.index("</p>", home_sec.index('id="demo-note"'))]
    assert "invented data" in note and "separate from your projects" in note and "nothing from your projects is sent" in note
    home = {"projects": [], "missing": [], "recent_persisted": True}
    out = shell_page(f"{HOME} console.log(JSON.stringify(urls()));", stages({}), None,
                     {"/api/home": home, "/api/stages": {"stages": [], "next": None}})
    assert not any(url in u or "vercel" in u for u in out)


START_BODY = r"""
replies["/api/projects/create"] = { root: "/x" };
// the start cards' command folds as index.html has them (the fake DOM does not read the page; the test pins the HTML)
$("#sc-cmd-question").textContent = "wider providers"; $("#sc-cmd-strategy").textContent = "wider query import";
$("#sc-cmd-records").textContent = "wider import";
const startWith = async (k) => {
  location.hash = "#home"; location.reloads = 0;
  $("#home-new").onclick({ target: { closest: (sel) => sel === "[data-start]" ? { dataset: { start: k } } : null }, preventDefault() {} });
  const shown = { create: !$("#home-create").hidden, head: $("#hc-h").textContent, cmd: $("#hc-cmd").textContent, focus: document.activeElement === $("#hc-path") };
  $("#hc-path").value = "/x/" + k; $("#hc-title").value = ""; $("#hc-questions").value = ""; $("#hc-platforms").value = "";
  await $("#hc-create").onclick(); await flush();
  return { ...shown, hash: location.hash, reloads: location.reloads };
};"""


def test_start_from_records():
    """Scenario "Starting from records" (the page half): "Start from records" opens the create form under its start,
    and the new project opens on Search results; nothing about the start is posted; the Overview shows the route
    sentence the project's state gives."""
    route = ("You started from records you already retrieved: writing search strings is optional (record the outside "
             "search for reproducibility if you can). The Review plan and Concepts and synonyms are still needed")
    body = START_BODY + r"""
const r = { records: await startWith("records") };
r.posted = calls.filter(c => c[0] === "/api/projects/create").map(c => Object.keys(c[2]).sort());
await loadOverview(); await flush(); r.route = $("#ov-route").textContent; r.go = $("#ov-continue").innerHTML;
console.log(JSON.stringify(r));"""
    summary = {"needs_you": [], "seed": {"n": 0}, "backends": {}, "open_questions": 0, "packages_out": 0, "route": route}
    out = shell_page(body, stages({"setup": "ready", "import": "done"}), "setup", {"/api/project/summary": summary})
    assert out["records"] == {"create": True, "head": "Create a project: Records you already retrieved", "cmd": "wider import",
                              "focus": True, "hash": "#import", "reloads": 1}
    assert out["posted"] == [["path", "platforms", "questions", "title"]]   # no start field: nothing is stored
    assert out["route"] == route and "Concepts and synonyms is optional" not in out["route"]
    assert 'id="ov-go"' in out["go"]   # the Overview still offers its one Continue


def test_start_from_strategy():
    """Scenario "Starting from a search strategy": the new project opens on Search strings, whose box shows the
    guide's sentence offering the import of the string as a draft (the sentence itself is pinned in test_guide)."""
    text = ("Not yet: this step needs Concepts and synonyms first. You can read the page meanwhile. If you have a search "
            "strategy already, import the string as a draft (Import a string; wider query import): Question and seed "
            "papers and Concepts and synonyms are still needed.")
    body = START_BODY + r"""
const r = { strategy: await startWith("strategy"), question: await startWith("question") };
markCurrentPage("query"); await flush(); r.box = $("#can-do-query").innerHTML;
console.log(JSON.stringify(r));"""
    out = shell_page(body, stages({}, {"query": ["construct"]}), "setup",
                     {"/api/guide/query": {"page": "query", "title": "Search strings", "can_do": text, "notices": [], "parts": []}})
    assert out["strategy"]["hash"] == "#query" and out["strategy"]["cmd"] == "wider query import"
    assert out["question"]["hash"] == "#model" and out["question"]["cmd"] == "wider providers"
    assert text in out["box"]
    html_page = (STATIC / "index.html").read_text(encoding="utf-8")
    for k, t in (("question", "A research question"), ("strategy", "A search strategy you already have"),
                 ("records", "Records you already retrieved")):
        card = html_page[html_page.index(f'aria-labelledby="st-{k}"'):]
        card = card[:card.index("</article>")]
        assert f">{t}</h3>" in card and "You need" in card and f'data-start="{k}"' in card
        cmd = {"question": "wider providers", "strategy": "wider query import", "records": "wider import"}[k]
        assert f'<code id="sc-cmd-{k}">{cmd}</code>' in card


def test_start_existing_and_empty_home():
    """Scenario "An existing project and an empty Home": "An existing WIDER project" opens the Open project panel;
    an opened project shows its Overview (the bare address); the empty Home shows the four starts and the demo link and
    says projects will be listed here once created or opened."""
    body = r"""
const r = {};
await loadHome(); await flush();
r.empty = { note: !$("#home-empty").hidden, text: $("#home-empty").textContent, newOpen: !$("#home-new").hidden,
            expanded: $("#home-create-toggle").getAttribute ? null : null };
$("#home-new").onclick({ target: { closest: (sel) => sel === "[data-start]" ? { dataset: { start: "existing" } } : null }, preventDefault() {} });
r.existing = { open: !$("#home-open").hidden, newHidden: $("#home-new").hidden, focus: document.activeElement === $("#ho-path") };
replies["/api/projects/open"] = { ok: true };
$("#ho-path").value = "/y"; location.hash = "#home"; await $("#ho-open").onclick(); await flush();
r.opened = { hash: location.hash, reloads: location.reloads };
console.log(JSON.stringify(r));"""
    out = shell_page(body, stages({}), None, {"/api/home": {"projects": [], "missing": [], "recent_persisted": True},
                                             "/api/stages": {"stages": [], "next": None}})
    assert out["empty"]["note"] is True and out["empty"]["newOpen"] is True
    assert out["existing"] == {"open": True, "newHidden": True, "focus": True}
    assert out["opened"] == {"hash": "", "reloads": 1}   # the bare address: the project opens on its Overview
    html_page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<p id="home-empty" hidden>Projects will be listed here once created or opened.</p>' in html_page
    assert html_page.count("data-start=") == 4 and 'data-start="existing"' in html_page


def test_records_start_before_import():
    """Scenario "A records start before any import": Search results, blocked only for want of search strings, shows the
    guide's sentence that importing first is fine as a box you can work in, not "Not yet"."""
    text = ("You can import the records you already have now: importing them before writing search strings is fine, "
            "and writing search strings then becomes optional.")
    body = r"""
markCurrentPage("import"); await flush();
console.log(JSON.stringify({ cls: $("#can-do-import").className, html: $("#can-do-import").innerHTML }));"""
    out = shell_page(body, stages({}, {"import": ["query"]}), "setup",
                     {"/api/guide/import": {"page": "import", "title": "Search results", "can_do": text, "notices": [], "parts": []}})
    assert out["cls"] == "can-do can-do-work" and text in out["html"]


def test_home_info_panel(tmp_path):
    """Info on a card shows the folder, records, seed papers, each LLM backend with its uses and locality, the
    embedding model, tokens, cost, last run and last backend check, as `wider info` prints them."""
    cur, lines = _summary(tmp_path / "a", current=True)
    home = {"projects": [cur], "missing": [], "recent_persisted": True}
    out = shell_page(f"{HOME} $('#pc-info-0').hidden = true; const b = $('#home-projects').querySelectorAll('[data-info]')[0]; b.onclick();"
                     "console.log(JSON.stringify({html: $('#home-projects').innerHTML, open: !$('#pc-info-0').hidden}));",
                     stages({}), None, {"/api/home": home, "/api/stages": {"stages": [], "next": None}})
    assert out["open"]
    info = out["html"][out["html"].index('id="pc-info-0"'):]
    rows = dict((k, v) for k, v in __import__("re").findall(r"<tr><th scope=\"row\" class=\"kv\">([^<]*)</th><td>(.*?)</td></tr>", info))
    assert list(rows) == ["folder", "records", "seed papers", "AI model", "embedding model", "tokens", "cost",
                          "last run", "backend check"]
    assert cur["root"] in rows["folder"] and cur["root"] in lines["project"]
    assert rows["records"] == lines["records"]
    assert rows["seed papers"] == lines["seed papers"]
    # two LLM backends, each with its uses and where it runs, in the order wider info lists them
    assert lines["AI model"].startswith("fake (fake-1, ") and "for default; fake2 (fake-2, " in lines["AI model"]
    assert lines["AI model"].endswith("for screen")
    llm = rows["AI model"]
    assert llm.index("fake <span") < llm.index("for default") < llm.index("fake2 <span") < llm.index("for screen")
    assert llm.count('class="chip loc-') == 2
    emb = cur["backends"]["embedding"]
    assert emb["name"] in rows["embedding model"] and emb["name"] in lines["embedding model"]
    assert "no model calls stored yet" in rows["tokens"] and lines["tokens"] == "no model calls stored yet"
    assert "nothing run yet" in rows["last run"] and lines["last run"] == "nothing run yet"
    assert "not run yet" in rows["backend check"] and lines["backend check"].startswith("not run yet")
    assert "no model calls yet" in rows["cost"] and "no model calls" in lines["cost"]


# ------------------------------------------------------------------ the last page per project

def test_open_lands_on_overview(tmp_path):
    """Ranking on A, open B (its last page was Deduplication), Home, then A again: each opens on its Overview, whose
    Continue offers the recommended step and "Back to <last page>, where you stopped"; the stored keys hold no folder
    path, Home and the Overview are never stored, and an explicit #page in the address wins (ui-redesign D7)."""
    a, _ = _summary(tmp_path / "a", title="A", current=True)
    b, _ = _summary(tmp_path / "b", title="B")
    home = {"projects": [a, b], "missing": [], "recent_persisted": True}
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    start = app.index("loadStages().catch(e => showLoadError(\"boot\"")
    real = app[app.index(".then(() => {", start) + len(".then(() => {"):app.index("\n});", start)]
    assert "startPage()" in real and "showPage(key)" in real
    body = (r"""
global.showPage = (k) => { shown.push(k); currentPage = k; markCurrentPage(k); };
const boot = (pk) => { PROJECT_KEY = pk; """ + real + r"""
  return key; };""" + r"""
replies["/api/projects/open"] = { ok: true };
CONSTRUCT_BACKEND = "fake"; NEXT_KEY = "query";
storage["wider.lastPage.bbbbbbbbbbbbbbbb"] = "dedup";
PROJECT_KEY = "aaaaaaaaaaaaaaaa"; markCurrentPage("rank"); markCurrentPage("home");
location.hash = "#home"; await loadHome(); await flush();
const open = (i) => $("#home-projects").querySelectorAll("[data-open]")[i];
await open(1).onclick(); await flush();
const afterOpen = { hash: location.hash, reloads: location.reloads, posted: calls.filter(c => c[0] === "/api/projects/open").map(c => c[2].path) };
const bPage = boot("bbbbbbbbbbbbbbbb"), bHash = location.hash;
await loadOverview(); const bCont = $("#ov-continue").innerHTML;
markCurrentPage("home");
await open(0).onclick(); await flush();
const aPage = boot("aaaaaaaaaaaaaaaa");
await loadOverview(); const aCont = $("#ov-continue").innerHTML;
storage["wider.lastPage.aaaaaaaaaaaaaaaa"] = "query"; await loadOverview(); const sameCont = $("#ov-continue").innerHTML;
storage["wider.lastPage.aaaaaaaaaaaaaaaa"] = "rank";
location.hash = "#dedup"; const explicit = boot("aaaaaaaaaaaaaaaa");
PROJECT_TITLE = ""; location.hash = ""; const none = boot("aaaaaaaaaaaaaaaa");
console.log(JSON.stringify({ afterOpen, bPage, bHash, aPage, bCont, aCont, sameCont, explicit, none, storage }));""")
    summary = {"title": "A", "needs_you": [], "questions": ["Q?"], "route": "", "records": 0, "raw_records": 0,
               "seed": {"n": 0}, "backends": {}, "open_questions": 0, "packages_out": 0}
    out = shell_page(body, stages({"setup": "done", "construct": "done", "query": "ready"}), "query",
                     {"/api/home": home, "/api/stages": {"stages": [], "next": None}, "/api/project/summary": summary})
    assert out["afterOpen"] == {"hash": "", "reloads": 1, "posted": [b["root"]]}
    assert out["bPage"] == "overview" and out["aPage"] == "overview" and out["bHash"] == "#overview"
    assert "Open Search strings" in out["bCont"] and "Back to Deduplication, where you stopped" in out["bCont"]
    assert "Back to Ranking, where you stopped" in out["aCont"]
    assert "Back to" not in out["sameCont"]   # the last page is the recommended one: one button
    assert out["explicit"] == "dedup" and out["none"] == "home"
    kept = {k: v for k, v in out["storage"].items() if k.startswith("wider.lastPage.")}
    assert kept == {"wider.lastPage.aaaaaaaaaaaaaaaa": "dedup", "wider.lastPage.bbbbbbbbbbbbbbbb": "dedup"}
    for k, v in kept.items():
        assert "/" not in k and str(tmp_path) not in k and v not in ("home", "overview")


def test_overview_continue_fallbacks():
    """No model for query creation: Continue recommends the model page while `wider next` names it, and the step
    `wider next` names once past query creation (audit F8, journey line too); a stored name that is not a page, or a storage
    that throws, offers no Back button; every step done recommends Recall and report; Create opens the model page."""
    body = r"""
PROJECT_KEY = "cccccccccccccccc"; NEXT_KEY = "query";
const r = {};
const cont = async () => { await loadOverview(); return $("#ov-continue").innerHTML; };
CONSTRUCT_BACKEND = null; NEXT_KEY = "model"; r.noModel = await cont();
// past query creation with no model (audit F8): the page follows `wider next`, not its own rule
NEXT_KEY = "query"; r.pastNoModel = await cont();
markCurrentPage("dedup"); r.pastJourney = $("#route").innerHTML;
storage["wider.lastPage.cccccccccccccccc"] = "no-such-page"; CONSTRUCT_BACKEND = "fake"; r.notAPage = await cont();
NEXT_KEY = null; r.allDone = await cont();
NEXT_KEY = "query";
global.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
markCurrentPage("rank"); r.blocked = await cont();
replies["/api/projects/create"] = { root: "/x" };
$("#hc-path").value = "/x"; $("#hc-title").value = "New"; $("#hc-questions").value = ""; $("#hc-platforms").value = "";
location.hash = "#home"; await $("#hc-create").onclick(); await flush();
r.created = location.hash;
console.log(JSON.stringify(r));"""
    summary = {"needs_you": [], "seed": {"n": 0}, "backends": {}, "open_questions": 0, "packages_out": 0}
    out = shell_page(body, stages({"setup": "done", "construct": "done", "query": "ready"}), "query",
                     {"/api/project/summary": summary})
    assert "Open Model for query creation" in out["noModel"]
    assert "Open Search strings" in out["pastNoModel"] and "Model for query creation" not in out["pastNoModel"]
    assert '<li class="jst now"><a href="#query"' in out["pastJourney"] and out["pastJourney"].count("jst now") == 1
    assert '<li class="jst open"><a href="#model" title="Model: not set yet">' in out["pastJourney"]   # shown, not current
    assert "Open Search strings" in out["notAPage"] and "Back to" not in out["notAPage"]
    assert 'href="#recall" id="ov-go">' in out["allDone"]
    assert "Open Search strings" in out["blocked"] and "Back to" not in out["blocked"]
    assert out["created"] == "#model"


def _overview_summary(**kw):
    base = {"title": "Synthetic project", "questions": ["Does feedback predict voice?"],
            "route": "Write search strings, then import.", "records": 240, "raw_records": 300,
            "seed": {"n": 12, "found": 11, "scope": "corpus"}, "reviewers": 2, "platforms": ["Scopus", "PubMed"],
            "lookups_allowed": False, "open_questions": 2, "packages_out": 1,
            "backends": {"llm": {"name": "local-a", "model": "m1", "locality": "local", "locality_label": "on this computer"},
                         "embedding": {"name": "emb", "model": "e1", "locality": "unknown"},
                         "llm_uses": [{"name": "local-a", "model": "m1", "locality": "local",
                                       "locality_label": "on this computer", "uses": ["default"]},
                                      {"name": "cloud-b", "model": "m2", "locality": "unknown", "uses": ["screen"]}]},
            "needs_you": [{"stage": "dedup", "title": "Deduplication", "kind": "needs_you", "text": "3 pairs to decide"},
                          {"stage": "rank", "title": "Ranking", "kind": "needs_review",
                           "text": "made before the newer Missing abstracts result"}]}
    return base | kw


def test_overview_sections_in_order():
    """The four headings in the order Continue, Needs attention, Research journey, Project summary; Needs attention
    lists "3 pairs to decide" with a button to Deduplication, the Ranking item with "Update ranking" and "Keep this
    ranking" and its reason, a note that the samples drawn from it keep their version, the open questions with a link
    to Questions and the review packages still out as a count only; Keep posts to /api/rank/keep."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = html[html.index('<section id="page-overview"'):]
    sec = sec[:sec.index("</section>\n  <section")]
    heads = re.findall(r"<h2 id=\"ov-h-\w+\">([^<]+)</h2>", sec)
    assert heads == ["Continue", "Needs attention", "Research journey", "Project summary"]
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done",
                   "enrich": "done", "rank": "done", "sample": "ready"})
    for r in rows:
        if r["key"] == "rank":
            r["review"] = [{"version": 1, "samples": ["dev-1"]}]
    body = r"""
CONSTRUCT_BACKEND = "fake"; replies["/api/rank/keep"] = { kept: true };
global.loadStages = async () => {};
await loadOverview();
const r = { needs: $("#ov-needs").innerHTML, board: $("#ov-board").innerHTML, sum: $("#ov-summary").innerHTML };
const keep = $("#ov-needs").querySelectorAll("[data-ov-keep]");
global.rkUpdate = async (deps) => { r.updated = typeof deps.start === "function" && typeof deps.askChoice === "function"; return "Updating the ranking in the background; see the bar at the top."; };
global.Jobs = global.Jobs || { start: async () => ({}) };
await $("#ov-needs").querySelectorAll("[data-ov-update]")[0].onclick(); r.updateMsg = $("#ov-msg").textContent;
r.nKeep = keep.length; await keep[0].onclick(); await flush();
r.posted = calls.filter(c => c[0] === "/api/rank/keep").map(c => c[2]);
r.msg = $("#ov-msg").textContent;
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "sample", {"/api/project/summary": _overview_summary()})
    n = out["needs"]
    assert "<b>Deduplication</b>: 3 pairs to decide" in n and 'href="#dedup">Open Deduplication</a>' in n
    assert "<b>Ranking</b> needs review: made before the newer Missing abstracts result" in n
    assert "data-ov-update>Update ranking</button>" in n and "Keep this ranking</button>" in n
    assert out["updated"] and out["updateMsg"].startswith("Updating the ranking")   # the Ranking page's flow
    assert "(dev-1) keep their version" in n
    assert "2 open questions" in n and 'href="#questions"' in n
    assert "1 review package still out" in n and n.count("<li") == 4
    assert n.index("Deduplication") < n.index("Ranking") < n.index("open questions") < n.index("review package")
    assert out["nKeep"] == 1 and out["posted"] == [{}] and "Kept this ranking" in out["msg"]
    assert "%" not in out["board"] + out["sum"]
    # open, no step count, the same places as the sidebar (M120 walk, 31 Oct): stage states in words, the model first in
    # stage 1, the review, results and reporting pages at their sidebar places marked "open any time"
    b = out["board"]
    assert "<details" not in b and "steps and their state" not in b and 'open any time' in b
    assert re.search(r"<h3>1 [^<]+ <span class=\"ov-state\">(current|completed|upcoming)</span></h3>", b)
    keys = re.findall(r'<a href="#([a-z]+)">', b)
    assert keys[0] == "model" and keys.index("screen") + 1 == keys.index("review")
    assert keys[keys.index("recall") + 1:keys.index("recall") + 3] == ["results", "reporting"]
    assert b.count('class="ov-stage ov-any"') == 3
    for k in ("Question", "Searches", "Records", "Reviewers", "Models and where they run", "Online lookups"):
        assert f"<dt>{k}</dt>" in out["sum"]
    assert "<dt>Route</dt>" not in out["sum"]   # the route sentence stands once, under the title (#ov-route)
    assert "Scopus, PubMed" in out["sum"] and "2 reviewers" in out["sum"]


def test_overview_hides_judgments():
    """While the query is not locked and development labels are open, nothing on the Overview shows a model decision,
    a miss or false-include count, a seed paper result or another reviewer's labels: the board shows last-result
    headlines only for stages that cannot hold a judgment; seed papers are a count; each model shows where it runs,
    "unknown" until checked."""
    rows = stages({"setup": "done", "construct": "done", "query": "ready", "import": "done", "dedup": "done",
                   "enrich": "done", "rank": "done", "sample": "done", "screen": "done"})
    results = {"dedup": {"headline": "14 pairs found", "ran_at": "2026-10-01"},
               "query": {"headline": "11 of 12 seed papers found", "ran_at": "2026-10-02"},
               "rank": {"headline": "3 seed papers missed in the top 500", "ran_at": "2026-10-03"},
               "screen": {"headline": "41 included, 2 false includes", "ran_at": "2026-10-04"},
               "sample": {"headline": "ana: 20 include, 80 exclude", "ran_at": "2026-10-05"}}
    body = (f"LAST_RESULTS = {json.dumps(results)}; CONSTRUCT_BACKEND = 'fake';" + r"""
await loadOverview();
console.log(JSON.stringify({ all: $("#ov-continue").innerHTML + $("#ov-needs").innerHTML + $("#ov-board").innerHTML + $("#ov-summary").innerHTML }));""")
    out = shell_page(body, rows, "query", {"/api/project/summary": _overview_summary(needs_you=[])})
    a = out["all"]
    assert "14 pairs found" in a   # Deduplication holds no judgment
    for bad in ("seed papers found", "missed", "false include", "41 included", "ana:", "11 of 12", "found\":"):
        assert bad not in a
    assert "12 seed papers" in a and "11" not in a.split("Seed papers")[1].split("</dd>")[0]
    assert "last run 2026-10-04" in a
    assert "local-a (m1): on this computer (default)" in a and "cloud-b (m2): unknown (screen)" in a
    assert "Embedding model</dt><dd>emb (e1): unknown" in a


def test_create_keeps_the_open_projects_last_page():
    """Create from Home while project A is open: A's stored last page stays as it was (the move to the model page
    fires no hashchange on A's page), the new project opens on the model page, and Enter in the folder or title field
    creates as the button does."""
    body = r"""
// an assignment to location.hash fires hashchange, which shows (and so records) that page, as app.js does
let hash = "#home";
Object.defineProperty(location, "hash", { get: () => hash, set: (v) => { hash = v.startsWith("#") ? v : "#" + v; showPage(hash.slice(1)); markCurrentPage(hash.slice(1)); } });
global.history = { replaceState(a, b, u) { hash = u && u.startsWith("#") ? u : ""; } };
PROJECT_KEY = "aaaaaaaaaaaaaaaa"; markCurrentPage("rank"); markCurrentPage("home");
replies["/api/projects/create"] = { root: "/x" };
$("#hc-path").value = "/x"; $("#hc-title").value = "New"; $("#hc-questions").value = ""; $("#hc-platforms").value = "";
await $("#hc-create").onclick(); await flush();
const r = { kept: storage["wider.lastPage.aaaaaaaaaaaaaaaa"], hash: location.hash, reloads: location.reloads };
const pressed = [];
$("#hc-create").click = () => pressed.push("create");
$("#hc-path").onkeydown({ key: "Enter" }); $("#hc-title").onkeydown({ key: "Enter" }); $("#hc-path").onkeydown({ key: "a" });
r.pressed = pressed;
console.log(JSON.stringify(r));"""
    out = shell_page(body, stages({}), "query")
    assert out == {"kept": "rank", "hash": "#model", "reloads": 1, "pressed": ["create", "create"]}


# ------------------------------------------------------------------ toasts and metric tiles

TOAST_DOM = r"""
const timers = [];
global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
global.clearTimeout = () => {};
const box = { kids: [], appendChild(e) { this.kids.push(e); } };
els["#toasts"] = box;
document.createElement = () => {
  const parts = {};
  const t = { className: "", attrs: {}, innerHTML: "", setAttribute(k, v) { this.attrs[k] = v; },
    querySelector(sel) { return parts[sel] ||= { onclick: null }; },
    remove() { box.kids = box.kids.filter(x => x !== t); } };
  return t;
};
replies["/api/jobs"] = [];
"""


def test_newer_toast_closes_earlier_ones():
    """An error, an Undo toast, a success, then another success: the newer success, the error and the Undo toast
    remain; the Undo button still works; the Undo toast closes 20 seconds after it opened, the error never does."""
    jobs = (STATIC / "jobs.js").read_text(encoding="utf-8")
    body = r"""
const undone = [];
const texts = () => box.kids.map(t => t.innerHTML.match(/<span>([^<]*)<\/span>/)[1]);
const err = Jobs.toast("it failed", "danger");
const undo = Jobs.toast("Saved.", "success", null, { label: "Undo", fn: async () => { undone.push(1); } });
const undoTimer = timers.length - 1;
Jobs.toast("first notice", "success");
const afterFirst = texts();
Jobs.toast("second notice", "success");
const afterSecond = texts();
Jobs.toast("a warning", "warning");
const afterWarning = texts();
await undo.querySelector("[data-act]").onclick();
const afterUndoClick = texts();
// a fresh Undo toast and its timer: 20 seconds later it is gone, the error stays
const undo2 = Jobs.toast("Saved again.", "success", null, { label: "Undo", fn: async () => {} });
const t2 = timers[timers.length - 1];
Jobs.toast("third notice", "success");
const beforeTimer = texts();
t2.fn();
console.log(JSON.stringify({ afterFirst, afterSecond, afterWarning, undone, afterUndoClick, beforeTimer,
  afterTimer: texts(), ms: t2.ms, errTimers: timers.filter(t => t.ms === 20000).length,
  roles: [err.className.includes("danger") ? "alert" : "status", undo.className.includes("danger") ? "alert" : "status"] }));"""
    out = run_async(f"{ESC}\n{PAGE}\n{TOAST_DOM}\n{jobs}\nawait flush();\n{body}")
    assert out["afterFirst"] == ["it failed", "Saved.", "first notice"]
    assert out["afterSecond"] == ["it failed", "Saved.", "second notice"]
    assert out["afterWarning"] == ["it failed", "Saved.", "a warning"]
    assert out["undone"] == [1] and out["afterUndoClick"] == ["it failed", "a warning"]
    assert out["beforeTimer"] == ["it failed", "Saved again.", "third notice"]
    assert out["ms"] == 20000 and out["afterTimer"] == ["it failed", "third notice"]
    # every toast but the error has a 20-second timer: 1 undo + 3 notices + 1 undo + 1 notice
    assert out["errTimers"] == 6 and out["roles"] == ["alert", "status"]


def test_metric_tiles_agree_in_number():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    code = app[app.index("const fmtNum ="):app.index("function rowsTable(")]
    code = "\n".join(line for line in code.splitlines() if not line.startswith("const fmtWhen"))
    body = ("const k = (m) => [...metricTiles(m).matchAll(/<div class=\"k\">([^<]*)<\\/div>/g)].map(x => x[1]);"
            "console.log(JSON.stringify({one: k({protocols: 1, files_imported: 1, 'unique records': 1, status: 1,"
            " 'maybes re-read': 1, 'benchmark hits': 1}), two: k({protocols: 2, 'unique records': 2}),"
            " zero: k({protocols: 0})}));")
    out = run_node_plain(f"const esc = (s) => String(s);\n{code}\n{body}")
    assert out["one"] == ["protocol", "file imported", "unique record", "status", "maybe re-read", "benchmark hit"]
    assert out["two"] == ["protocols", "unique records"] and out["zero"] == ["protocols"]


def run_node_plain(script: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ------------------------------------------------------------------ app.js, records.js and shell.js together

def app_page(body: str, replies: dict) -> dict:
    """app.js, records.js, dedup.js and shell.js as top-level scripts, as the browser loads them (so only function declarations
    and ``window.x =`` are on ``window``), on the stand-in page; ``body`` runs after the boot."""
    if not NODE:
        pytest.skip("node is not installed")
    page = PAGE.replace("const $ = (s) => document.querySelector(s);\n", "")   # app.js brings its own
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    fill = common[common.index("function fillSelect("):]
    fill = fill[:fill.index("\n}\n") + 3]
    stubs = r"""
const storage = {}, winListeners = {};
global.localStorage = { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); }, removeItem: (k) => { delete storage[k]; } };
global.innerWidth = 1200; global.matchMedia = () => ({ matches: false, addEventListener() {} });
global.location = { hash: "#rank", pathname: "/", reload() {} };
global.history = { replaceState(a, b, u) { location.hash = u && u.startsWith("#") ? u : ""; } };
global.fetch = async () => ({}); global.scrollTo = () => {};
global.addEventListener = (t, fn) => { (winListeners[t] ||= []).push(fn); };
document.querySelectorAll = () => [];
global.Backends = { checkBannerText: () => "", settingsBackendsHtml: () => "" };
global.Pickers = { embeddingPicker: async () => ({ rows: [], chosen: null }) };   // the Ranking page's cards (pickers.js)
let rowOpen = null, drawerCfg = null;
global.Drawer = { open(c) { drawerCfg = c; }, isOpen: () => false, close() {}, bindRows(t, fn) { rowOpen = fn; }, markRow() {},
  author: () => "Reviewer A", current: () => null, isDocked: () => !!drawerCfg };
global.WiderName = { get: () => String(storage["wider.reviewer"] || "").trim(), render() {}, askOnce() {}, ask() {}, set() {},
  needHtml: (l) => esc(l) };   // the one name of common.js (D33), tested in test_name_once_node
"""
    parts = [ESC, page, stubs, f"Object.assign(replies, {json.dumps(replies)});", fill, APP,
             (STATIC / "records.js").read_text(encoding="utf-8"), (STATIC / "dedup.js").read_text(encoding="utf-8"),
             (STATIC / "shell.js").read_text(encoding="utf-8"),
             f"(async () => {{ await flush();\n{body}\n}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});"]
    r = subprocess.run([NODE, "-"], input="\n".join(parts), capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _stages_reply(states: dict, title: str = "Synthetic project") -> dict:
    from wider.core.stages import load_sections

    return {"stages": stages(states), "next": "rank", "last_results": {}, "sections": load_sections(), "title": title,
            "project_key": "abcdabcdabcdabcd", "construct_backend": "fake"}


def test_pages_redraw_after_actions():
    """An ablation lists its rankings in the ranking select and the run selector without a reload; a duplicate decision
    in the docked pair view updates the list heading at once and the sidebar follows the new stage state; a rename on the
    setup page reaches the sidebar title; a protocol screened elsewhere is in the Recall page's select when it opens."""
    states = {"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "ready", "rank": "ready"}
    replies = {
        "/api/stages": _stages_reply(states),
        "/api/project": {"config": {"embeddings": [], "protocol": {"ranking": {"top_n_cells": 2}}}},
        "/api/construct": {"anchors": []},
        "/api/dedup": {"run_at": "t0", "prisma": [], "summary": {}, "pending": 3, "decided_since_run": 0,
                       "flow": {"has_run": True, "imported": 9, "imported_since_run": 0, "files": 1, "removed": 0,
                                "removed_manual": 0, "removed_auto": 0, "split_recorded": True, "left": 9, "provisional": True,
                                "to_decide": 3, "decided": 0, "unsure": 0, "decided_since": 0, "message": "3 pairs need your decision."},
                       "queue": [{"raw_a": 1, "raw_b": 2, "stage": "fuzzy", "score": 0.9, "title_a": "Alpha", "title_b": "Alpha"},
                                 {"raw_a": 3, "raw_b": 4, "stage": "fuzzy", "score": 0.8, "title_a": "Beta", "title_b": "Beta"},
                                 {"raw_a": 5, "raw_b": 6, "stage": "fuzzy", "score": 0.7, "title_a": "Gamma", "title_b": "Gamma"}]},
        "/api/dedup/decide": {"change_id": 7},
        "/api/notebook/dedup": [], "/api/guide/dedup": {"page": "dedup", "title": "Deduplication", "parts": [], "notices": []},
        "/api/screen": {"protocols": [{"protocol": "p1"}]},
        "/api/samples": {"samples": {}},
        "/api/screen/sets": {"sets": []},
    }
    body = r"""
const r = {};
let ablated = false;
const result = (id, head) => ({ result_id: id, ran_at: "t" + id, action: "rank", headline: head });
replies["/api/rank"] = () => ({ corpus: { records: 3, thin: 0, sets: [] }, ranking: null, rows: [], n_rows: 0, best_cell_top3000: {}, top_pool: 0,
  bases: ["anchors-v1"].concat(ablated ? ["ablation-cells-only"] : []) });
replies["/api/stages/rank/results"] = () => ablated ? [result(2, "ablation stored 1 ranking"), result(1, "first ranking")] : [result(1, "first ranking")];
replies["/api/rank/ablate"] = () => { ablated = true; return { rows: [{ variant: "cells only", recall_at_250: 0.5 }], notice: null, validation_peeked: false }; };
await flush();
r.before = { ranking: $("#rk-ranking").innerHTML, runs: $('.run-select[data-runs="rank"]').innerHTML };
await $("#rk-ablate").onclick(); await flush();
r.after = { ranking: $("#rk-ranking").innerHTML, runs: $('.run-select[data-runs="rank"]').innerHTML,
            hidden: $('.run-select[data-runs="rank"]').hidden };

// the Deduplication page: a decision in the docked pair view, which stays open
const queue = replies["/api/dedup"].queue, flow0 = replies["/api/dedup"].flow;
replies["/api/dedup"] = () => { const n = calls.filter(c => c[0] === "/api/dedup/decide").length;
  return { run_at: "t0", prisma: [], summary: {}, pending: 3 - n, decided_since_run: n, queue,
           flow: Object.assign({}, flow0, { to_decide: 3 - n, decided: n, decided_since: n }) }; };
const head = () => $("#dd-list-head").innerHTML.replace(/<span class="dd-list-n">[^<]*<\/span>/, "").replace("Shown by the chart · ", "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
for (const s of ["#dd-search", "#dd-openfirst"]) $(s).closest = () => ({});   // the row of tools around them
showPage("dedup"); await flush();
r.headBefore = head();
replies["/api/stages"].stages.find(s => s.key === "dedup").state = "done";
rowOpen(0);
await drawerCfg.decisions[0].fn(drawerCfg.items[0]);
await flush();
r.headAt = head();
""" + LINK + r"""
r.dedupWord = links().find(l => l.key === "dedup").word;
r.decided = calls.filter(c => c[0] === "/api/dedup/decide").map(c => c[2].decision);

// a rename on the setup page (setup.js dispatches wider:changed after each write)
replies["/api/stages"].title = "Renamed project";
winListeners["wider:changed"].forEach(fn => fn());
await new Promise(res => setTimeout(res, 350)); await flush();
r.navTitle = $("#nav-project").textContent;

// a protocol screened elsewhere: the Recall page lists it when it opens
replies["/api/screen"] = { protocols: [{ protocol: "p1" }, { protocol: "p2-screened-elsewhere" }] };
showPage("recall"); await flush();
r.protocols = $("#rc-protocol").innerHTML;
r.globals = { refresh: typeof window.refreshStages, after: typeof window.afterAction };
console.log(JSON.stringify(r));
"""
    out = app_page(body, replies)
    assert "ablation-cells-only" not in out["before"]["ranking"] and out["before"]["runs"] == ""
    assert "ablation-cells-only" in out["after"]["ranking"] and "anchors-v1" in out["after"]["ranking"]
    assert "ablation stored 1 ranking" not in out["after"]["runs"]  # the latest is the "Latest result" option
    assert "first ranking" in out["after"]["runs"] and out["after"]["hidden"] is False
    assert out["headBefore"] == "Pairs to decide 3 open, 0 decided"
    assert out["headAt"] == "Pairs to decide 2 open, 1 decided" and out["decided"] == ["duplicate"]
    assert out["dedupWord"] == "completed"
    assert out["navTitle"] == "Renamed project"
    assert "p2-screened-elsewhere" in out["protocols"]
    assert out["globals"] == {"refresh": "function", "after": "function"}


# ------------------------------------------------------------------ Settings (loadSettings)

SETTINGS_ROUTES = ("/api/project/summary", "/api/project", "/api/providers", "/api/embeddings", "/api/providers/consent",
                   "/api/embeddings/consent", "/api/questions/summary")
# the cards of #page-settings are separate elements on the stand-in page: the page searches all of them
SETTINGS_PAGE = r"""
global.renderLookupsCard = () => {};
const CARDS = ["#settings-backends", "#settings-policy", "#settings-doubts", "#settings-computer"];
$("#page-settings").querySelectorAll = (sel) => new NodeListLike(CARDS.flatMap(c => [...$(c).querySelectorAll(sel)]));
const settingsText = () => CARDS.map(c => $(c).innerHTML).join("\n");
"""


def settings_page(body: str, replies: dict, real_backends: bool = False) -> dict:
    """shell.js's loadSettings on the stand-in page; with ``real_backends`` the real backends.js, pickers.js and
    model.js replace the ``Backends`` stub (model.js holds the backend form Settings opens in place)."""
    code = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("backends.js", "pickers.js", "model.js")) if real_backends else ""
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    return run_async(f"{ESC}\n{PAGE}\n{SHELL_GLOBALS}\n{code}\nObject.assign(replies, {json.dumps(replies)});\n{SETTINGS_PAGE}\n"
                     f"{shell}\nawait flush(); await loadSettings(); await flush();\n{body}")


def test_settings_check_result_shows_uses(tmp_path):
    """The Settings check table names each LLM backend's uses, as the info card and `wider check-backends` do."""
    from fastapi.testclient import TestClient

    from tests.unit.test_setup_routes import _two_backends
    from wider.ui.app import create_app

    c = TestClient(create_app(_two_backends(tmp_path / "r")))
    replies = {u: c.get(u).json() for u in SETTINGS_ROUTES}
    replies["/api/project/summary"]["backend_check"] = {"at": "t", "ok": False, "results": [
        {"role": "llm", "name": "a", "uses": ["default", "construct"], "ok": True},
        {"role": "llm", "name": "b", "uses": ["screen"], "ok": False, "error": "wrong answer"}]}
    html = settings_page("console.log(JSON.stringify($('#settings-backends').innerHTML));", replies)
    assert "<td>llm (default, construct)</td><td>a " in html and "<td>llm (screen)</td><td>b " in html
    assert "wrong answer" in html


def test_settings_manage_backends(tmp_path):
    """Scenario: managing a backend on Settings. Edit opens the backend form in place with its price fields, the
    `screen` select and Remove send the routes `test_setup_routes::test_settings_backend_actions_match_cli` pins;
    replayed on a project they give the same backends as the CLI twins. No Settings text, cost card or `wider info`
    line names a "setup step"."""
    from fastapi.testclient import TestClient

    from tests.unit.test_setup_routes import _cli, _two_backends, backend_config, backends_after_cli
    from wider.core.summary import cost_text
    from wider.ui.app import create_app

    ui = _two_backends(tmp_path / "ui")
    c = TestClient(create_app(ui))
    replies = {u: c.get(u).json() for u in SETTINGS_ROUTES}
    replies.update({"/api/providers/a": {"ok": True}, "/api/providers/b": {"ok": True}, "/api/providers/stage": {"ok": True},
                    "/api/providers/a/models": {"models": []}})
    body = r"""
const text = settingsText();
await click("#settings-backends", "[data-settings-edit]", (b) => b.dataset.settingsEdit === "a");
// the Settings form's ids carry "st-", so they never repeat the model page's form (inspection 1 Nov, item 18)
const form = $("#st-backend-form"), opened = { hidden: $("#settings-backend-form").hidden, title: $("#st-backend-form-title").textContent,
  name: form.name.value, model: form.model.value, price: form.price_input_per_mtok.value,
  unprefixed: ($("#settings-backend-form").innerHTML.match(/ id="backend-[^"]*"/g) || []).length,
  prefixed: ($("#settings-backend-form").innerHTML.match(/ id="st-backend-[^"]*"/g) || []).length };
form.price_input_per_mtok.value = "0.5";
await form.onsubmit({ preventDefault() {} }); await flush();
const sel = [...$("#settings-backends").querySelectorAll("[data-settings-stage]")].find(s => s.dataset.settingsStage === "screen");
sel.value = "a"; await sel.onchange(); await flush();
await click("#settings-backends", "[data-settings-remove]", (b) => b.dataset.settingsRemove === "b");
console.log(JSON.stringify({ text, opened, writes: calls.filter(c => c[1] !== "GET"), after: settingsText() }));
"""
    r = settings_page(body, replies, real_backends=True)
    assert r["opened"] == {"hidden": False, "title": "Edit a", "name": "a", "model": "m-a", "price": "", "unprefixed": 0,
                           "prefixed": 12}
    writes = r["writes"]
    assert [w[:2] for w in writes] == [["/api/providers/a", "PATCH"], ["/api/providers/stage", "POST"], ["/api/providers/b", "DELETE"]]
    assert writes[0][2]["price_input_per_mtok"] == 0.5 and writes[0][2]["model"] == "m-a"
    assert writes[1][2] == {"stage": "screen", "name": "a"}
    for url, method, b in writes:
        res = c.request(method, url, json=b) if b is not None else c.request(method, url)
        assert res.status_code == 200, res.text
    assert backend_config(ui.root) == backends_after_cli(tmp_path / "cli")
    # Settings and the cost card name no setup step; `wider info` neither
    unpriced = {"calls": 3, "usd": 0.0, "known": False, "unpriced_calls": 3, "cache_reads": 0, "cached_tokens": 0}
    cost = run_node(f"console.log(JSON.stringify(costHtml({json.dumps(unpriced)})));")
    info = _cli("info", "--dir", ui.root)
    assert info.exit_code == 0, info.output
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    settings = html[html.index('id="page-settings"'):]
    settings = settings[:settings.index("</section>")]
    for t in (r["text"], r["after"], cost, info.output, settings, cost_text(unpriced)):
        assert "setup step" not in t.lower() and "in setup" not in t.lower()
    assert "on Settings" in cost


def test_status_map_word_and_icon():
    """Every status of the shared map has a text word and an aria-hidden icon, and no two statuses look alike
    (design-pass-a1-dedup D12)."""
    rows = stages({"setup": "ready"})
    out = shell_page("console.log(JSON.stringify(Status.keys.map(k => ({key: k, word: Status.word(k), chip: Status.chip(k)}))));",
                     rows, "setup")
    keys = [x["key"] for x in out]
    assert keys == ["open", "wait", "dup", "notdup", "unsure", "wider", "done", "needs", "review", "blocked", "running", "test"]
    words = {x["key"]: x["word"] for x in out}
    assert words["wait"] == "waiting for you" and words["unsure"] == "unsure" and words["wider"] == "by WIDER"
    assert len(set(words.values())) == len(keys)
    icons = set()
    for x in out:
        chip = x["chip"]
        assert f'class="st-chip st-{x["key"]}"' in chip
        assert '<svg class="st-icon"' in chip and 'aria-hidden="true"' in chip
        assert f'<span class="st-word">{x["word"]}</span>' in chip
        icons.add(chip[chip.index("<svg"):chip.index("</svg>")])
    assert len(icons) == len(keys)


def test_sidebar_icon_rail_below_900_px_and_the_choice_is_remembered():
    """Narrower than 900 px with no remembered choice: the sidebar starts as an icon rail; the researcher's expand is
    remembered under wider.sidebar and wins over the narrow window on the next load."""
    from wider.core.stages import load_sections

    rows = stages({"setup": "done"})
    setup = f"STAGES = {json.dumps(rows)}; SECTIONS = {json.dumps(load_sections())};"
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")

    def load(pref: str | None, body: str) -> dict:
        glob = SHELL_GLOBALS.replace("global.innerWidth = 1200;", "global.innerWidth = 800;")
        glob = glob.replace("const storage = {};", f"const storage = {json.dumps({'wider.sidebar': pref} if pref else {})};")
        # a class list and attributes that remember, on the body and the toggle (the stand-in forgets them)
        glob += ("const cls = new Set(); document.body.classList = { toggle: (c, on) => { on ? cls.add(c) : cls.delete(c); },"
                 " contains: (c) => cls.has(c), add: (c) => cls.add(c), remove: (c) => cls.delete(c) };"
                 "const attrs = {}; $('#sidebar-toggle').setAttribute = (k, v) => { attrs[k] = v; };"
                 "$('#sidebar-toggle').getAttribute = (k) => attrs[k];")
        return run_async(f"{ESC}\n{PAGE}\n{glob}\n{setup}\n{shell}\nawait flush();\n{body}")

    rail = "() => document.body.classList.contains('sidebar-collapsed')"
    out = load(None, f"const c = {rail}; const before = c(); $('#sidebar-toggle').onclick();"
                     "console.log(JSON.stringify({before, after: c(), kept: storage['wider.sidebar'] || null,"
                     " label: $('#sidebar-toggle').getAttribute('aria-expanded')}));")
    assert out == {"before": True, "after": False, "kept": "open", "label": "true"}
    out = load("open", f"console.log(JSON.stringify({{rail: ({rail})()}}));")
    assert out == {"rail": False}


def test_node_scripts_go_through_stdin():
    """The page scripts are larger than Linux allows for one argument (128 KiB), so ``node -e script`` fails on the
    CI runner with "Argument list too long" while it passes on macOS: every test hands its script to ``node -``."""
    tests = Path(__file__).resolve().parent
    found = [p.name for p in tests.glob("*.py") if '[NODE, "-' + 'e"' in p.read_text(encoding="utf-8")]
    assert found == []


def test_help_lists_redo_tab_and_menu_keys():
    """The Help page's Keyboard card lists Ctrl/Cmd+Shift+Z as redo, Left/Right as moving between tabs and Esc as
    closing the Menu, and its 1 / 2 / 3 line says "unsure for duplicate pairs"."""
    from tests.unit.a11y_dom import run_dom
    from tests.unit.test_a11y_node import BODY, SHELL_PAGE_PRE

    r = run_dom("""
      $("#page-help").hidden = false; loadHelp();
      out($$("#help-keys tr").filter(tr => tr.querySelector("kbd")).map(tr => [tr.querySelector("kbd").textContent, tr.querySelector("td").textContent]));
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"), pre=SHELL_PAGE_PRE)
    keys = dict(r)
    assert "redo" in keys["Ctrl+Shift+Z / Cmd+Shift+Z"] and "tabs" in keys["Left / Right"]
    assert any(k == "Esc" and "Menu" in v for k, v in r)
    # inspection 1 Nov, item 1: Deduplication's own four keys have their own line; 1 / 2 / 3 is the drawer's
    assert "include / exclude / maybe" in keys["1 / 2 / 3"] and "duplicate" not in keys["1 / 2 / 3"]
    assert "keep A" in keys["1 / 2 / 3 / 4"] and "unsure" in keys["1 / 2 / 3 / 4"]


def test_stage_commands_in_explain_and_help():
    """accessibility-and-naming 6.2: the Explain panel ends with "For scripts:" and every command of the stage; Help's
    card "Page names and commands" gives each stage its description, why and commands, and lists every other sidebar
    page from the one FIXED_PAGES list, which also names the sidebar's fixed links."""
    from tests.unit.a11y_dom import run_dom, static
    from tests.unit.test_a11y_node import BODY, SHELL_PAGE_PRE

    app = static("app.js")
    why = app[app.index("function renderStageWhy() {"):app.index("const oneLine = ")]
    r = run_dom("""
      STAGES[0].commands = ["init", "benchmark", "protocol", "providers"];
      $("#page-help").hidden = false; loadHelp();
      const help = $$("#help-stages details").map(d => [d.querySelector("summary").textContent.replace(/\\s+/g, " ").trim(),
        [...d.querySelectorAll("code")].map(c => c.textContent), d.textContent.includes("For scripts:")]);
      const pages = $$("#help-pages tr").filter(tr => tr.querySelector("a")).map(tr => [tr.querySelector("a").textContent, tr.querySelector("a").getAttribute("href"),
        [...tr.querySelectorAll("code")].map(c => c.textContent)]);
      const side = $$("#sidebar > a.nav-link[data-key]").map(a => [a.dataset.key, a.querySelector(".label").textContent, a.title]);
      """ + why + """
      renderStageWhy();
      const explain = ["#why-setup", "#why-rank"].map(id => $(id)).map(el => { const p = [...el.querySelectorAll("p")].pop(); return [p.textContent.startsWith("For scripts:"), [...p.querySelectorAll("code")].map(c => c.textContent)]; });
      out({ heading: $("#help-stages").parentNode.querySelector("h2").textContent, help, pages, side, explain,
            fixed: window.FIXED_PAGES.map(p => p.key) });
    """, html=BODY, scripts=("common.js", "jobs.js", "backends.js", "shell.js"), pre=SHELL_PAGE_PRE)
    assert r["heading"] == "Page names and commands"
    assert r["help"][0][1] == ["wider init", "wider benchmark", "wider protocol", "wider providers"] and r["help"][0][2]
    assert r["help"][1][1] == ["wider rank"] and r["help"][1][0].startswith("2. Ranking")
    titles = {p[0] for p in r["pages"]}
    assert titles == {"Home", "Overview", "Model for query creation", "Screening review", "Results dashboard", "Questions",
                      "Ask WIDER", "Notes and tags", "History", "Help", "Settings and data", "Methods", "PRISMA reporting"}
    assert all(href == "#" + k for (_t, href, _c), k in zip(r["pages"], r["fixed"], strict=True))
    assert dict((p[0], p[2]) for p in r["pages"])["Settings and data"] == ["wider protocol", "wider providers", "wider check-backends"]
    assert r["side"] and all(label == title and (key, label) in {(k, p[0]) for k, p in zip(r["fixed"], r["pages"], strict=True)}
                             for key, label, title in r["side"])
    assert r["explain"] == [[True, ["wider init", "wider benchmark", "wider protocol", "wider providers"]], [True, ["wider rank"]]]


# ------------------------------------------------------------------ journey line (ui-redesign D12, task 4.3)

def test_journey_line_hide_show():
    """A transit line under the top bar (owner, 29 Oct 2026, option A): the four sections as segments, each step a
    station with its state icon and words for screen readers, the step `wider next` names marked as the current step
    and the open page underlined; Home has none. Hidden on Ranking, it stays hidden on the Overview with "Show
    the journey line" offered; showing it brings it back, focused on Hide, and the choice is kept as `wider.journey`."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done",
                   "enrich": "done", "rank": "done"})
    for r in rows:
        if r["key"] == "rank":
            r["needs_you"] = "3 records to check"
    body = r"""
CONSTRUCT_BACKEND = "fake"; NEXT_KEY = "rank";
$("#route-hide").setAttribute = () => {}; $("#route-show").setAttribute = () => {};
const r = {};
markCurrentPage("home"); r.home = { hidden: $("#route").hidden, show: $("#route-show").hidden };
markCurrentPage("rank"); r.line = $("#route").innerHTML; r.lineHidden = $("#route").hidden;
$("#route-hide").onclick();
r.afterHide = { hidden: $("#route").hidden, show: $("#route-show").hidden, focus: document.activeElement === $("#route-show"), kept: storage["wider.journey"] };
markCurrentPage("overview"); r.overview = { hidden: $("#route").hidden, show: $("#route-show").hidden };
$("#route-show").onclick();
r.afterShow = { hidden: $("#route").hidden, show: $("#route-show").hidden, focus: document.activeElement === $("#route-hide"), kept: storage["wider.journey"], line: $("#route").innerHTML };
CONSTRUCT_BACKEND = null; NEXT_KEY = "model"; markCurrentPage("overview"); r.noModel = $("#route").innerHTML;
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "rank")
    assert out["home"] == {"hidden": True, "show": True}
    line = out["line"]
    assert out["lineHidden"] is False
    assert [m for m in re.findall(r'<span class="ln">([^<]+)<', line)] == [
        "1 Literature search", "2 Data cleaning", "3 Model tuning and validation", "4 Screening and outputs"]
    assert '<li class="jseg sec-current" style="--n:2"><span class="ln">3 Model tuning and validation' in line
    # one grid column per stage, its share by its number of steps, so all stage titles share one row
    cols = re.search(r'<ol class="jl" style="--cols:([^"]+)">', line).group(1)
    assert cols.split(" ")[1::2] == [f"{n}fr)" for n in re.findall(r'class="jseg[^"]*" style="--n:(\d+)"', line)]
    assert cols.count("minmax(min-content,") == 4
    assert '<li class="jst now needs"><a href="#rank" aria-current="page"' in line
    assert '<span class="sr-only">, current step, needs attention: 3 records to check</span>' in line
    rank = line[line.index('href="#rank"'):line.index('href="#sample"')]
    assert '<svg class="st-icon"' in rank   # the state has its icon beside the words, never colour alone
    assert '<li class="jst open"><a href="#sample"' in line and "%" not in line
    assert line.count('aria-current="page"') == 1 and line.count("jst now") == 1
    assert out["afterHide"] == {"hidden": True, "show": False, "focus": True, "kept": "0"}
    assert out["overview"] == {"hidden": True, "show": False}
    assert out["afterShow"]["hidden"] is False and out["afterShow"]["show"] is True
    assert out["afterShow"]["focus"] is True and out["afterShow"]["kept"] == "1"
    assert '<li class="jst now needs"><a href="#rank" title=' in out["afterShow"]["line"]   # on the Overview, Ranking is not the page
    assert '<li class="jst now"><a href="#model"' in out["noModel"] and ", current step, not set yet" in out["noModel"]
    assert '<li class="jseg sec-current" style="--n:5"><span class="ln">1 Literature search' in out["noModel"]   # not done without a model


def test_no_refresh_while_queue_open():
    """While the labelling queue is open (records.js queueOpen), a saved label does not refresh the stages, so the
    sidebar words, footer, journey line and guide cannot change between two labels; the refresh is held and runs once
    the queue is left; with the queue closed a write refreshes as before (ui-redesign D4)."""
    from tests.unit.a11y_dom import run_dom

    body = r"""replies["/api/labels"] = { ok: true }; let n = 0; window.refreshStages = () => { n++; return Promise.resolve(); };
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    let open = true; window.queueOpen = () => open;
    await api("/api/labels", { method: "POST", body: "{}" }); await api("/api/labels", { method: "POST", body: "{}" });
    await wait(500); const held = n, flag = window.stageRefreshHeld === true;
    open = false; await api("/api/labels", { method: "POST", body: "{}" }); await wait(500);
    out({ held, flag, after: n });"""
    out = run_dom(body, scripts=("common.js",))
    assert out == {"held": 0, "flag": True, "after": 1}
    # leaving the queue's page runs the held refresh once; staying on it does not
    rows = stages({"setup": "done"})
    res = shell_page("let n = 0; global.refreshStages = () => { n++; }; window.stageRefreshHeld = true;"
                     "markCurrentPage('sample'); const stay = n; markCurrentPage('dedup'); markCurrentPage('rank');"
                     "console.log(JSON.stringify({stay, left: n, flag: window.stageRefreshHeld}));", rows, "construct")
    assert res == {"stay": 0, "left": 1, "flag": False}
    src = (STATIC / "records.js").read_text(encoding="utf-8")
    assert 'window.queueOpen = () => typeof currentPage !== "undefined" && currentPage === "sample" && lb.rows.length > 0;' in src


def test_topbar_more_folds_notebook_and_guide():
    """Phone top bar (owner, 29 Oct, ui-redesign D27): "More" carries the Notebook (with its count) and Guide (with its
    state), closed until pressed; the CSS shows it only up to 700 px and hides the two buttons there. Opening, Esc and
    the items were checked at 375 px in the browser (the stand-in DOM does not parse the menu)."""
    rows = stages({k: "done" for k in ("setup", "construct", "query", "import", "dedup")})
    out = shell_page(f"{TOPBAR} renderShell(); show('dedup'); Topbar.set('dedup', DEDUP);"
                     "console.log(JSON.stringify({ html: $('#tb-more').innerHTML }));", rows, "enrich", title="Practice review")
    html = out["html"]
    assert 'id="tb-more-btn" aria-expanded="false" aria-controls="tb-more-list">More <span class="tb-count">3</span>' in html
    assert '<ul id="tb-more-list" hidden>' in html
    assert 'data-more="notebook" aria-pressed="false">Notebook <span class="tb-count">3</span>' in html
    assert 'data-more="guide"' in html and "Guide</button>" in html
    # owner inspection G0: Ask WIDER and Questions fold in too, so the phone bar keeps two rows
    assert 'data-more="ask" aria-pressed="false">Ask WIDER</button>' in html
    assert 'data-more="questions">Questions</button>' in html
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "#tb-ask, #tb-questions { display:none !important; }" in css
    assert "#tb-notebook, #tb-guide { display:none !important; } #tb-more { display:inline-flex;" in css
    assert "@media (min-width: 701px) { #tb-more { display:none; } }" in css


def test_guide_panel_default_and_choice():
    """Scenarios "Open on a wide window" and "Closed on a narrow window, choice kept": with no stored choice the guide
    panel is open at 1440 px (Deduplication too: it uses the shared panel, ui-redesign D28) and closed at 900 px;
    showing it on Ranking draws the Ranking guide with the focus in the panel and keeps it open on the next page
    (`wider.guide`); Esc in the panel, or Guide pressed again, closes it and puts the focus on the Guide button; Home has no panel."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "ready", "rank": "ready"})
    guide = {"page": "rank", "title": "Ranking", "can_do": "Rank the records.", "notices": [],
             "parts": [{"heading": "Where you are", "items": ["Step 7 of 12."]},
                       {"heading": "What to do", "items": ["Press Rank records.", "Check the top records."]},
                       {"heading": "Watch out", "items": ["Ranking sends nothing."]}]}
    sample = dict(guide, page="sample", title="Labelling", notices=["2 records wait for your label."])
    body = r"""
CONSTRUCT_BACKEND = "fake";
const r = {}, btn = () => $("#tb-guide-btn"), pn = $("#guide-panel");   // the guide's text is in #guide-body, Ask WIDER below it (guide-assistant D18)
const exp = () => { const m = String($("#tb-guide").innerHTML).match(/aria-expanded="(\w+)"/); return m ? m[1] : null; };
global.innerWidth = 1440; markCurrentPage("dedup"); await flush();
r.dedup = { wanted: Guide.wanted(), shared: pn.hidden };
markCurrentPage("rank"); await flush();
r.wide = { open: Guide.isOpen(), hidden: pn.hidden, exp: exp(), html: $("#guide-body").innerHTML };
global.innerWidth = 900; markCurrentPage("rank"); await flush();
r.narrow = { open: Guide.isOpen(), hidden: pn.hidden, exp: exp(), kept: storage["wider.guide"] || null };
btn().onclick(); await flush();
r.shown = { hidden: pn.hidden, exp: exp(), focus: document.activeElement === pn, kept: storage["wider.guide"], html: $("#guide-body").innerHTML };
markCurrentPage("sample"); await flush();
r.next = { hidden: pn.hidden, html: $("#guide-body").innerHTML, box: String(($("#can-do-sample") || {}).innerHTML || "") };
let stopped = false;
pn.onkeydown({ key: "Escape", preventDefault() {}, stopPropagation() { stopped = true; } });
r.esc = { hidden: pn.hidden, exp: exp(), focus: document.activeElement === btn(), kept: storage["wider.guide"], stopped };
btn().onclick(); await flush();
btn().onclick(); await flush();
r.hide = { hidden: pn.hidden, focus: document.activeElement === btn() };
markCurrentPage("home"); r.home = { hidden: pn.hidden, btn: $("#tb-guide").innerHTML };
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "dedup", {"/api/guide/rank": guide, "/api/guide/sample": sample, "/api/guide/dedup": guide})
    assert out["dedup"] == {"wanted": True, "shared": False}   # the shared panel opens on Deduplication too
    assert out["wide"]["open"] is True and out["wide"]["hidden"] is False and out["wide"]["exp"] == "true"
    assert "<h2" in out["wide"]["html"] and "Hide guide" not in out["wide"]["html"] and "Press Rank records." in out["wide"]["html"]
    assert out["narrow"] == {"open": False, "hidden": True, "exp": "false", "kept": None}
    assert out["shown"]["hidden"] is False and out["shown"]["exp"] == "true" and out["shown"]["focus"] is True
    assert out["shown"]["kept"] == "open" and "Check the top records." in out["shown"]["html"]
    assert "Ranking sends nothing." in out["shown"]["html"]   # Watch out is shown where written
    # D30 (owner, 29 Oct): a page's notices sit in its "What you can do here now" box; the panel shows the general guide
    assert out["next"]["hidden"] is False and "Check the top records." in out["next"]["html"]
    assert "2 records wait for your label." not in out["next"]["html"] and "Show the general guide" not in out["next"]["html"]
    assert "What you can do here now:" in out["next"]["box"] and "<li>2 records wait for your label.</li>" in out["next"]["box"]
    assert out["esc"] == {"hidden": True, "exp": "false", "focus": True, "kept": "closed", "stopped": True}
    assert out["hide"] == {"hidden": True, "focus": True}
    assert out["home"]["hidden"] is True and out["home"]["btn"] == ""
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="guide-panel" aria-label="Guide and Ask WIDER" tabindex="-1" hidden' in index   # its two tabs (D23)



def test_drawer_lies_over_the_guide():
    """ui-redesign D24, guide-assistant D23: with the guide open, the record drawer takes the guide's width and the
    guide hides under it (one side panel at a time), and a wide window keeps the main area's width; Guide pressed while a drawer over the page covers the guide closes the drawer, the guide
    stays open and takes the focus; with the guide closed it closes the drawer and opens the guide. A docked drawer is
    left alone."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done", "rank": "ready"})
    guide = {"page": "rank", "title": "Ranking", "can_do": "Rank the records.", "notices": [], "parts": []}
    body = r"""
CONSTRUCT_BACKEND = "fake";
const r = {}, btn = () => $("#tb-guide-btn"), pn = $("#guide-panel");
let open = true, docked = false, closed = [];
global.Drawer = { isOpen: () => open, isDocked: () => docked, close(l) { closed.push(l); open = false; } };
global.innerWidth = 1440; markCurrentPage("rank"); await flush();
btn().onclick(); await flush();
r.covered = { closed: closed.slice(), open: Guide.isOpen(), hidden: pn.hidden, focus: document.activeElement === pn };
Guide.toggle(); await flush();   // the guide closed, then a drawer opened over the page
open = true; closed = [];
btn().onclick(); await flush();
r.closedGuide = { closed: closed.slice(), open: Guide.isOpen(), hidden: pn.hidden };
open = true; docked = true; closed = [];
btn().onclick(); await flush();
r.docked = { closed: closed.slice(), open: Guide.isOpen() };
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "rank", {"/api/guide/rank": guide})
    assert out["covered"] == {"closed": [True], "open": True, "hidden": False, "focus": True}
    assert out["closedGuide"] == {"closed": [True], "open": True, "hidden": False}
    assert out["docked"] == {"closed": [], "open": False}
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "@media (min-width: 900px) { body.guide-open { --drawer-w:340px; } body.guide-open.drawer-open .drawer { z-index:16; } }" in css
    assert "@media (min-width: 1180px) { body.guide-open.drawer-open .shell { padding-right:0; } }" in css
    assert re.search(r"body\.guide-open \.shell \{ grid-template-columns:var\(--sidebar-w\) minmax\(0, 1fr\) 340px; \}", css)

def test_panel_and_drawer_one_at_a_time():
    """guide-assistant D23 (owner, 31 Oct 2026: "one at a time"): a drawer opened over the page hides the side panel and
    gives its room back (the panel is still wanted, nothing is stored as closed); when the drawer closes the panel comes
    back on its tab; a docked drawer leaves the panel shown. drawer.js asks the panel to follow at the end of open and
    of close."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done", "rank": "ready"})
    guide = {"page": "rank", "title": "Ranking", "can_do": "Rank the records.", "notices": [], "parts": []}
    body = r"""
CONSTRUCT_BACKEND = "fake";
const cls = new Set(); document.body.classList = { toggle: (c, on) => { on ? cls.add(c) : cls.delete(c); }, contains: (c) => cls.has(c) };
const r = {}, pn = $("#guide-panel");
let open = false, docked = false;
global.Drawer = { isOpen: () => open, isDocked: () => docked, close() { open = false; Guide.sync(); } };
const st = () => ({ hidden: pn.hidden, room: cls.has("guide-open"), wanted: Guide.isOpen(), kept: storage["wider.guide"] || null,
                    exp: (String($("#tb-guide").innerHTML).match(/aria-expanded="(\w+)"/) || [])[1] || null });
global.innerWidth = 1440; markCurrentPage("rank"); await flush(); r.before = st();
open = true; Guide.sync(); await flush(); r.over = st();
Drawer.close(); await flush(); r.after = st();
open = true; docked = true; Guide.sync(); await flush(); r.docked = st();
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "rank", {"/api/guide/rank": guide})
    assert out["before"] == {"hidden": False, "room": True, "wanted": True, "kept": None, "exp": "true"}
    assert out["over"] == {"hidden": True, "room": False, "wanted": True, "kept": None, "exp": "true"}
    assert out["after"] == out["before"] and out["docked"] == out["before"]
    drawer = (STATIC / "drawer.js").read_text(encoding="utf-8")
    assert drawer.count("if (window.Guide && Guide.sync) Guide.sync();") == 2


def test_can_do_box_states():
    """Scenarios "A locked search", "A completed step names what uses it" and "A blocked stage stays open": the box
    under the title shows the guide's can_do sentence with the stage's mark; the locked search's main button is
    "Continue to Search results" and its quiet "Unlock to change…" opens Concepts and synonyms; the blocked Ranking page keeps its controls enabled."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "enrich": "done", "dedup": "ready"},
                  {"rank": ["dedup"]})
    locked = ("Locked: the search strings can be read and copied; later steps use this search. Unlock to change it (on "
              "Concepts and synonyms): the search is then no longer validated on the validation seed papers, and that "
              "is recorded in History, the methods text and the package.")
    done = "Completed, and you can still change it. Changing this reruns nothing; these steps read its result: Ranking, Screening."
    blocked = "Not yet: this step needs Deduplication first. You can read the page meanwhile."
    g = lambda page, c: {"page": page, "title": page, "can_do": c, "notices": [], "parts": []}  # noqa: E731
    body = r"""
CONSTRUCT_BACKEND = "fake";
const r = {}, box = (k) => ({ cls: $("#can-do-" + k).className, hidden: $("#can-do-" + k).hidden, html: $("#can-do-" + k).innerHTML });
markCurrentPage("query"); await flush(); r.query = box("query");
$("#art-unlock").hidden = false;
$("#can-do-query").onclick({ target: { closest: (s) => s === "[data-unlock]" ? {} : null } });
r.unlock = { hash: location.hash, focus: document.activeElement === $("#art-unlock") };
markCurrentPage("enrich"); await flush(); r.enrich = box("enrich");
$("#rk-run").disabled = false;
markCurrentPage("rank"); await flush(); r.rank = box("rank"); r.rankRun = $("#rk-run").disabled;
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "dedup", {"/api/guide/query": g("query", locked), "/api/guide/enrich": g("enrich", done),
                                           "/api/guide/rank": g("rank", blocked)})
    assert out["query"]["cls"] == "can-do can-do-locked" and out["query"]["hidden"] is False
    assert "What you can do here now:" in out["query"]["html"] and locked in out["query"]["html"]
    # D32 F5: the main (filled) action of the locked search is Continue; Unlock is a quiet button in a disclosure
    html = out["query"]["html"]
    assert '<a class="btn primary" href="#import">Continue to Search results</a>' in html
    assert '<details class="can-do-unlock"><summary>Change a locked string</summary>' in html
    assert '<button type="button" class="btn quiet small" data-unlock>Unlock to change…</button>' in html
    assert html.index("Continue to Search results") < html.index("<details") < html.index("data-unlock")
    assert html.count('class="btn primary"') == 1 and "btn small" not in html
    assert out["unlock"] == {"hash": "construct", "focus": True}
    assert out["enrich"]["cls"] == "can-do can-do-done" and done in out["enrich"]["html"] and "data-unlock" not in out["enrich"]["html"]
    assert out["rank"]["cls"] == "can-do can-do-notyet" and blocked in out["rank"]["html"]
    assert out["rankRun"] is False   # the box never disables the page
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    for key in ("model", "setup", "construct", "query", "import", "dedup", "enrich", "rank", "sample", "screen", "recall"):
        assert f'id="can-do-{key}" role="note" aria-label="What you can do here now" hidden' in index, key


def test_prev_next_links():
    """Scenario "Missing abstracts": the page ends with "Previous Deduplication" and "Continue to Ranking"; the first
    stage page shows only Continue and the last only Previous; the result card has no Continue button of its own."""
    rows = stages({"setup": "done", "dedup": "done", "enrich": "ready"})
    body = r"""
const strip = (s) => String(s).replace(/<[^>]*>/g, " ").split(/\s+/).join(" ").trim();
const r = {};
for (const k of ["enrich", "setup", "recall"]) { markCurrentPage(k); r[k] = { text: strip($('[data-pn="' + k + '"]').innerHTML), html: $('[data-pn="' + k + '"]').innerHTML, hidden: $('[data-pn="' + k + '"]').hidden }; }
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "enrich")
    assert out["enrich"]["text"] == "Previous Deduplication Continue to Ranking" and out["enrich"]["hidden"] is False
    assert 'href="#dedup"' in out["enrich"]["html"] and 'href="#rank"' in out["enrich"]["html"]
    assert out["setup"]["text"].startswith("Continue to ") and "Previous" not in out["setup"]["text"]
    assert out["recall"]["text"].startswith("Previous ") and "Continue" not in out["recall"]["text"]
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert index.count('class="pn" data-pn="') == 9 and 'data-pn="dedup"' not in index   # Deduplication keeps its own link
    assert 'data-goto="${esc(next)}"' not in APP and "Continue to ${" not in APP


def test_overview_dev_gate_item():
    """Scenario "An upgraded project is told": the Overview's Needs attention says that screening runs including
    development records wait until the sample is complete, and why, in normal text with "Continue labelling" opening
    Samples and labelling; nothing when the summary has no such item."""
    from wider.screen.devgate import GATE_TEXT

    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done",
                   "enrich": "done", "rank": "done", "sample": "done", "screen": "done"})
    body = r"""
CONSTRUCT_BACKEND = "fake";
global.loadStages = async () => {};
await loadOverview();
console.log(JSON.stringify({ needs: $("#ov-needs").innerHTML }));"""
    item = {"stage": "sample", "title": "Samples and labelling", "kind": "dev_gate", "text": GATE_TEXT}
    n = shell_page(body, rows, "sample", {"/api/project/summary": _overview_summary(dev_gate=item)})["needs"]
    li = n[:n.index("</li>") + 5]                                     # the first item
    assert f"<b>Samples and labelling</b>: {GATE_TEXT}" in n and 'href="#sample" data-goto-queue>Continue labelling</a>' in li
    assert 'class="small"' not in li and "muted" not in li
    assert n.index(GATE_TEXT) < n.index("Deduplication")             # first: it decides what can run
    plain = shell_page(body, rows, "sample", {"/api/project/summary": _overview_summary()})["needs"]
    assert GATE_TEXT not in plain and "Continue labelling" not in plain


def test_no_dev_count_beside_queue(cproject):  # noqa: F811
    """Scenario "No development count beside the queue" (ui-redesign 4.4; the held refresh is
    test_no_refresh_while_queue_open): from the real stage states of a project whose development sample has open
    labels, the sidebar words for Screening (and Samples and labelling), the footer and the journey line say
    "development labels open" with no number, and none of them changes when a development record and then a
    validation record are labelled. `wider status` says the same words (its CLI twin)."""
    from tests.unit.test_screen_compare import cli, dev_rows, fresh, label_all
    from wider.core.stages import stage_states
    from wider.sample.labels import label

    p, _ = cproject
    rows = dev_rows(p)
    label_all(p, skip={r["record_id"] for r in rows[:3]})
    body = (f"{LINK} CONSTRUCT_BACKEND = 'fake'; renderShell(); markCurrentPage('sample'); await flush();"
            "const by = Object.fromEntries(links().map(x => [x.key, x]));"
            "console.log(JSON.stringify({screen: by.screen, sample: by.sample, foot: $('#nav-foot').innerHTML,"
            " line: $('#route').innerHTML}));")

    def seen():
        q = fresh(p)
        states = json.loads(json.dumps(stage_states(q), default=str))
        # Samples and labelling is the next step once the earlier ones are done (next_stage passes a later stage
        # over for it while development labels are open); this project leaves the Review plan open
        return shell_page(body, states, "sample")

    before = seen()
    for k in ("screen", "sample"):
        assert "development labels open" in before[k]["word"] and "development labels open" in before[k]["tip"], k
        assert not any(ch.isdigit() for ch in before[k]["word"]), k
    assert 'Next: <a href="#sample">Samples and labelling</a>: development labels open' in before["foot"]
    assert "development labels open" in before["line"]
    assert not re.search(r"\d+ (left|records?|development)", before["foot"] + before["line"])
    out = " ".join(cli(p, "status").output.split())
    assert "waits: development labels open" in out and "done, needs you: development labels open" in out
    label(fresh(p), "development", rows[0]["record_id"], "alice", "exclude", reason="a")
    assert seen() == before
    label(fresh(p), "validation", dev_rows(p, "validation")[0]["record_id"], "alice", "exclude", reason="a")
    assert seen() == before


def test_project_box_keeps_its_height_in_a_short_window():
    """Audit F13 (29 Oct 2026): in a window too short for the sidebar, the sidebar's flex column squeezed the project
    box under Home to an empty pill; it does not shrink."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    rule = css.split(".nav-project-box {", 1)[1].split("}", 1)[0]
    assert "flex-shrink:0" in rule


def test_top_bar_height_is_measured():
    """Audit 5.6: the top bar wraps on a narrow window or when a page puts long buttons in it, so --top-h (which
    places the sidebar, the guide panel and the record drawer under the bar) is the bar's measured height, set each
    time the bar is drawn; a hidden bar leaves it alone."""
    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "ready"})
    body = r"""
const set = []; document.body.style = { setProperty: (k, v) => set.push([k, v]) };
$("#topbar").offsetHeight = 145; markCurrentPage("dedup"); Topbar.redraw();
const r = { tall: set[set.length - 1] };
$("#topbar").offsetHeight = 50.4; Topbar.redraw(); r.short = set[set.length - 1];
const n = set.length; $("#topbar").offsetHeight = 0; Topbar.redraw(); r.zero = set.length === n;
console.log(JSON.stringify(r));"""
    out = shell_page(body, rows, "dedup")
    assert out == {"tall": ["--top-h", "145px"], "short": ["--top-h", "50px"], "zero": True}


# ------------------------------------------------------------------ Settings sections (owner, 29 Oct 2026, option A)

def test_settings_sections_show_one_part():
    """Settings has a section list on the left; Language models is open first, and choosing a section shows only its
    cards and marks that button as current."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    secs = re.findall(r'data-settings-sec="(\w+)"', html)
    parts = re.findall(r'<div class="card" id="settings-[\w-]+" data-settings-part="(\w+)"( hidden)?', html)
    assert secs == ["models", "data", "protocol", "doubts", "computer"] and {p for p, _ in parts} == set(secs)
    assert {p for p, hid in parts if not hid} == {"models"} and 'data-settings-sec="models" aria-current="true"' in html
    code = (STATIC / "shell.js").read_text(encoding="utf-8")
    fn = code[code.index("function showSettingsPart"):code.index("qa(\"[data-settings-sec]\").forEach(b => b.onclick")]
    out = run_node_plain(f"""
const el = (d) => ({{ dataset: d, hidden: false, attrs: {{}}, setAttribute(k, v) {{ this.attrs[k] = v; }}, removeAttribute(k) {{ delete this.attrs[k]; }} }});
const btns = {json.dumps(secs)}.map(s => el({{ settingsSec: s }}));
const cards = {json.dumps([p for p, _ in parts])}.map(s => el({{ settingsPart: s }}));
const qa = (sel) => sel === "[data-settings-sec]" ? btns : cards;
{fn}
showSettingsPart("data");
console.log(JSON.stringify({{ cur: btns.filter(b => b.attrs["aria-current"] === "true").map(b => b.dataset.settingsSec),
  shown: cards.filter(c => !c.hidden).map(c => c.dataset.settingsPart) }}));""")
    assert out == {"cur": ["data"], "shown": ["data", "data", "data"]}


def test_shell_needs_you_literature_search():
    """review-plan-and-question-window D13: an open question marks Literature search stages in any state ("needs
    attention: 1 question for you" on a ready Search strings); a later stage that is not done keeps its state word."""
    rows = stages({"setup": "done", "construct": "done", "query": "ready", "dedup": "ready"})
    for r in rows:
        r["needs_you"] = {"query": "1 question for you", "setup": "2 questions for you", "dedup": "3 pairs"}.get(r["key"])
    body = ("const st = (k) => stageStatus(STAGES.find(s => s.key === k));"
            "console.log(JSON.stringify({query: st('query'), setup: st('setup'), dedup: st('dedup')}));")
    out = shell_page(body, rows, "query")
    assert out["query"] == {"cls": "needs", "word": "needs attention", "text": "needs attention: 1 question for you"}
    assert out["setup"]["text"] == "needs attention: 2 questions for you"
    assert out["dedup"]["cls"] == "ready"


def test_footer_counts_needs_you_not_done():
    """The footer counts a Literature search stage with open questions that is not done, and the next step names
    what it waits on (D13)."""
    rows = stages({"setup": "done", "construct": "ready"})
    for r in rows:
        r["needs_you"] = "1 question for you" if r["key"] == "construct" else None
    out = shell_page("renderShell(); await flush(); markCurrentPage('dedup');"
                     "console.log(JSON.stringify({f: $('#nav-foot').innerHTML}));", rows, "construct")
    assert "1 thing needs you" in out["f"]
    assert 'Next: <a href="#construct">' in out["f"] and ": 1 question for you" in out["f"]


def test_setup_links_open_task_pages():
    """D17: the Settings links into setup open the task page (seed → seeds), the prior-review switch names the
    Prior reviews task, and the dry-run card links to Seed papers."""
    from tests.unit.test_query_roles import js_function

    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    out = run_node(f"""global.location = {{ hash: "" }}; const go = () => {{ throw new Error("go"); }};
{js_function(shell, "function gotoSetupStep(")}
const seen = []; for (const s of ["prior", "seed", "databases"]) {{ gotoSetupStep(s); seen.push(location.hash); }}
console.log(JSON.stringify(seen));""")
    assert out == ["setup?task=prior", "setup?task=seeds", "setup?task=databases"]
    assert "Change in the Prior reviews task of the Review plan" in shell
    assert "Question and seed " + "papers" not in shell   # the old page name, split so a grep for it finds none
    dry = (STATIC / "dryrun.js").read_text(encoding="utf-8")
    assert 'href="#setup?task=seeds" data-dr-setup>Open Seed papers' in dry and 'location.hash = "setup?task=seeds"' in dry
