"""Query generation page and Setup checklist: the development platform is named by its role, and the character count
on each card is the length of the string Copy puts on the clipboard (Google Scholar within its 256-character limit).

The page code itself runs under Node (skipped when Node is not installed) on JSON from the real API routes; the
static checks on the page text run everywhere."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import wider.ui
from wider.cli import app
from wider.construct import artifacts
from wider.core.project import Project
from wider.search.ir import Block, Query
from wider.search.render import platforms
from wider.search.render.reduced import BUDGET, render_gscholar
from wider.ui.app import create_app

STATIC = Path(wider.ui.__file__).parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def js_function(src: str, head: str) -> str:
    """The source of the top-level function that starts with ``head``, up to its matching closing brace."""
    i = src.index(head)
    depth = 0
    for k in range(src.index("{", i), len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise AssertionError(f"no end for {head}")


def _prelude() -> str:
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    esc = re.search(r"^const esc = .*;$", common, re.M).group(0)
    fold = re.search(r"^const cmdFold = .*;$", common, re.M).group(0)  # the cards' "Command line" fold
    dev = re.search(r"^const devPlatform = .*;$", APP_JS, re.M).group(0)
    return f"{esc}\n{fold}\n{dev}\n"


def run_js(body: str, data: dict) -> dict:
    """Run page code under Node with ``DATA`` bound to ``data``; the script prints one JSON object."""
    if not NODE:
        pytest.skip("node is not installed")
    script = f"(async () => {{\nconst DATA = {json.dumps(data)};\n{_prelude()}{body}\n}})()" \
             ".catch(e => { console.error(e && e.stack || e); process.exit(1); });"
    r = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def setup_page(client: TestClient) -> dict:
    """The Review plan's task cards (GET /api/plan, as setup.js draws them: key, done, line as text) plus a "summary"
    step for the plan's ready line, and the folded summary table (renderSummary) from /api/project."""
    plan = client.get("/api/plan").json()
    steps = [{"key": t["key"], "done": t["done"], "text": t["line"], "state": t["state"]} for t in plan["tasks"]]
    steps.append({"key": "summary", "done": plan["ready"], "text": plan["summary"]})
    data = {"d": client.get("/api/project").json(), "proto": client.get("/api/protocol").json()}
    body = (js_function(APP_JS, "function renderSummary(") + """
const els = {};
const $ = (sel) => (els[sel] ||= { innerHTML: "" });
renderSummary(DATA.d, DATA.proto);
console.log(JSON.stringify({ summary: $("#setup-summary").innerHTML }));""")
    return {"steps": steps, **run_js(body, data)}


def query_page(client: TestClient) -> dict:
    """loadQuery on the routes it calls; every Copy button is clicked and the clipboard writes are returned."""
    urls = ["/api/query/platforms", "/api/platforms", "/api/query/render", "/api/query/render?compact=true"]
    data = {u: client.get(u).json() for u in urls}
    body = js_function(APP_JS, "async function loadQuery(") + """
const api = async (u) => { if (!(u in DATA)) throw new Error("unexpected call " + u); return DATA[u]; };
const el = () => ({ innerHTML: "", textContent: "", querySelector: () => el() });
const els = {};
const $ = (sel) => (els[sel] ||= el());
const clip = [];
const navigator = { clipboard: { writeText: async (t) => { clip.push(t); } } };
const buttons = Object.keys(DATA["/api/query/render"].strings).map(k => ({ dataset: { copy: k }, textContent: "Copy" }));
const document = { querySelectorAll: (s) => s === "[data-copy]" ? buttons : [] };
const setTimeout = () => {};
await loadQuery();
for (const b of buttons) await b.onclick();
console.log(JSON.stringify({ dev: $("#q-dev").innerHTML, status: $("#q-status").textContent,
  cards: $("#q-strings").innerHTML, clip: buttons.map((b, i) => [b.dataset.copy, clip[i]]) }));"""
    out = run_js(body, data)
    assert out["status"].startswith("synonyms v"), out["status"]  # the page did not fall into its error branch
    cards = {}
    for html in out["cards"].split('<div class="card">')[1:]:
        key = re.search(r'data-copy="([^"]+)"', html).group(1)
        cards[key] = {"badge": html.count('<span class="badge">development</span>'),
                      "title": re.search(r"<h2>(.*?) <", html).group(1),
                      "count": int(re.search(r"(\d+) characters when pasted", html).group(1))}
    out["cards"], out["clip"] = cards, dict(out["clip"])
    out["order"] = list(cards)
    return out


def _project(tmp_path, keys, query: Query | None = None) -> TestClient:
    p = Project.init(tmp_path / "r", platforms=keys)
    q = query or Query(blocks=[Block(key="a", terms=["alpha", "beta gamma"]), Block(key="b", terms=["delta"])],
                       logic={"and": ["a", "b"]})
    artifacts.save_new_version(p, "synonyms", q.model_dump(mode="json"), origin="test", note="t")
    p.close()
    return TestClient(create_app(Project.load(tmp_path / "r")))


def _cli_roles(root) -> dict[str, str]:
    """``wider query platforms``: the role column of each chosen platform's row."""
    r = CliRunner().invoke(app, ["query", "platforms", "--dir", str(root)], env={"COLUMNS": "250"})
    assert r.exit_code == 0, r.output
    roles = {}
    for line in r.output.splitlines():
        cells = [c.strip() for c in line.strip("│┃ ").split("│")]
        if len(cells) >= 3 and cells[2] in ("development", "validation"):
            roles[cells[0]] = cells[2]
    return roles


# ------------------------------------------------- the development platform is named by its role
def test_static_page_names_dev_platform_by_role():
    # one resolver: the platform whose role is development, the first in search order only when none has it
    assert 'const devPlatform = (plats) => plats.find(p => p.role === "development") || plats[0];' in APP_JS
    # every place that names the development platform goes through it, never through platforms[0]
    # the Review plan's Databases card names it from GET /api/plan (wider/core/plan.py:_databases, same rule)
    assert "const dev = devPlatform(c.platforms || []);" in APP_JS
    assert 'const dev = devPlatform((await api("/api/platforms")).chosen || []);' in APP_JS
    assert "Development platform: <b>${esc(dev.label || dev.key)}</b>" in APP_JS
    assert "${dev && dev.key === k ? ' <span class=\"badge\">development</span>' : \"\"}" in APP_JS
    assert not re.search(r"platforms\[0\]|plats\[0\]\.key|chosen\[0\]", APP_JS.replace("|| plats[0];", ""))
    # the page says that the Validation set of seed papers, not any platform, stays hidden until the freeze
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "the Validation set of seed papers (not any platform) stays hidden until the query is frozen" in html


def test_dev_platform_set_out_of_order(tmp_path):
    """Scenario: platforms ebsco, scopus, wos in that order and scopus set as development."""
    client = _project(tmp_path, ["ebsco", "scopus", "wos"])
    r = client.post("/api/platforms/role", json={"key": "scopus", "role": "development"})
    assert [(c["key"], c["role"]) for c in r.json()["chosen"]] == [
        ("ebsco", "validation"), ("scopus", "development"), ("wos", "validation")]
    # THEN the Query generation page names Scopus as the development platform ...
    page = query_page(client)
    assert page["dev"].startswith(f"Development platform: <b>{platforms()['scopus']['label']}</b>. ")
    assert page["dev"] == ("Development platform: <b>Scopus</b>. The query is built and checked there; "
                           "the other platforms run the frozen query.")
    # ... and marks its card (and only its card) as development, the cards keeping the search order
    assert page["order"] == ["ebsco", "scopus", "wos"]
    assert {k: c["badge"] for k, c in page["cards"].items()} == {"ebsco": 0, "scopus": 1, "wos": 0}
    # AND the Review plan's Databases card and the summary name scopus, not ebsco
    setup = setup_page(client)
    step = next(s for s in setup["steps"] if s["key"] == "databases")
    assert step == {"key": "databases", "done": True, "text": "3 platforms, development on scopus", "state": "done"}
    assert '<tr><th scope="row" class="kv">databases</th><td>ebsco, scopus (development), wos</td></tr>' in setup["summary"]
    assert "ebsco (development)" not in setup["summary"]
    # CLI twin: `wider query platforms` lists each role
    assert _cli_roles(tmp_path / "r") == {"ebsco": "validation", "scopus": "development", "wos": "validation"}


def test_no_role_set_names_first_platform(tmp_path):
    """Scenario: no platform has the development role (as in a hand-edited project.yaml)."""
    client = _project(tmp_path, ["wos", "scopus", "ebsco"])
    p = Project.load(tmp_path / "r")
    for pl in p.config.platforms:
        pl.role = "validation"
    p.save()
    assert [x.role for x in Project.load(tmp_path / "r").config.platforms] == ["validation"] * 3
    assert client.get("/api/project").json()["config"]["platforms"][0]["role"] == "validation"
    # THEN the first platform in search order is named as the development platform, everywhere
    assert [(c["key"], c["role"]) for c in client.get("/api/platforms").json()["chosen"]] == [
        ("wos", "development"), ("scopus", "validation"), ("ebsco", "validation")]
    page = query_page(client)
    assert page["dev"].startswith(f"Development platform: <b>{platforms()['wos']['label']}</b>. ")
    assert {k: c["badge"] for k, c in page["cards"].items()} == {"wos": 1, "scopus": 0, "ebsco": 0}
    setup = setup_page(client)
    assert next(s for s in setup["steps"] if s["key"] == "databases")["text"] == "3 platforms, development on wos"
    assert '<tr><th scope="row" class="kv">databases</th><td>wos (development), scopus, ebsco</td></tr>' in setup["summary"]
    assert _cli_roles(tmp_path / "r") == {"wos": "development", "scopus": "validation", "ebsco": "validation"}


# ------------------------------------------------- character count = copied string; Google Scholar within 256
def _long_query(n_blocks=3, n_terms=30) -> Query:
    blocks = [Block(key=f"b{i}", terms=[f"term{i}x{j} word{j}" for j in range(n_terms)]) for i in range(n_blocks)]
    return Query(blocks=blocks, logic={"and": [b.key for b in blocks]})


def test_static_count_and_copy_use_the_compact_string():
    q = js_function(APP_JS, "async function loadQuery(")
    assert 'api("/api/query/render?compact=true")' in q
    assert "${(compact.strings[k] ?? s).length} characters when pasted" in q
    assert "navigator.clipboard.writeText(compact.strings[b.dataset.copy] ?? d.strings[b.dataset.copy])" in q


def test_gscholar_card_within_limit_and_equal_to_copied(tmp_path):
    """Scenario: the Google Scholar card's count is at most 256, the same length as the copied string."""
    client = _project(tmp_path, ["scopus", "gscholar"], _long_query())
    compact = client.get("/api/query/render?compact=true").json()["strings"]
    assert 0 < len(compact["gscholar"]) <= 256 and "\n" not in compact["gscholar"]
    page = query_page(client)
    card = page["cards"]["gscholar"]
    assert card["title"] == platforms()["gscholar"]["label"]
    assert card["count"] <= 256
    assert page["clip"]["gscholar"] == compact["gscholar"] and card["count"] == len(page["clip"]["gscholar"])
    # every card's count is the length of exactly what Copy put on the clipboard (the indented Scopus string too)
    assert "\n" in client.get("/api/query/render").json()["strings"]["scopus"]
    for k, c in page["cards"].items():
        assert c["count"] == len(page["clip"][k]) == len(compact[k]), k
    # CLI twin: `wider query render --compact --out DIR` reports the length of the string it writes
    out = tmp_path / "out"
    r = CliRunner().invoke(app, ["query", "render", "gscholar", "--compact", "--out", str(out),
                                 "--dir", str(tmp_path / "r")], env={"COLUMNS": "250"})
    assert r.exit_code == 0, r.output
    written = (out / "gscholar.txt").read_text(encoding="utf-8").splitlines()[-1]
    assert written == compact["gscholar"]
    assert f"({len(written)} chars)" in " ".join(r.output.split())


@pytest.mark.parametrize("n_blocks,words", [(3, 2), (9, 4), (12, 4), (1, 60)])
def test_gscholar_string_never_exceeds_budget(n_blocks, words):
    """Even when one term per block is still too long (many blocks, or one very long phrase)."""
    blocks = [Block(key=f"b{i}", terms=[" ".join(f"w{i}x{j}y{k}" for k in range(words)) for j in range(5)])
              for i in range(n_blocks)]
    q = Query(blocks=blocks, logic={"and": [b.key for b in blocks]})
    s = render_gscholar(q)
    assert BUDGET == 256 and 0 < len(s) <= 256, len(s)
    assert s == render_gscholar(q, pretty=False)  # one form only: the card shows what is pasted
    assert s.count("(") == s.count(")") and s.count('"') % 2 == 0
    assert s.startswith('("w0x0y0') or s.startswith("(w0x0y0")
    if n_blocks >= 9:  # too many blocks for one term each: the last blocks are left out, the first kept
        assert f"w{n_blocks - 1}x" not in s and "w1x0" in s
    if n_blocks == 1:  # one block whose one term is too long keeps that term's leading words
        assert s.startswith('"w0x0y0 w0x0y1') or s.startswith('("w0x0y0 w0x0y1')
    # the paste note shown with the string (page card and `wider query render gscholar`) says the last blocks may go
    from wider.search.catalogue import catalogue
    paste = " ".join(next(r for r in catalogue() if r["key"] == "gscholar")["paste"].split())
    assert "when even one term per block is too long, the last blocks are left out" in paste


def test_gscholar_keeps_truncated_terms_without_the_star():
    """A block's truncated first term stays first (without `*`); nine short-word blocks all fit, none left out."""
    words = ["silence", "employ", "fear", "outcome", "leader", "safety", "channel", "survey", "climate"]
    blocks = [Block(key=f"b{i}", terms=[f"{w}*", f"organizational {w} behaviour phrase", f"{w}ing*"])
              for i, w in enumerate(words)]
    q = Query(blocks=blocks, logic={"and": [b.key for b in blocks]})
    s = render_gscholar(q)
    assert 0 < len(s) <= BUDGET and "*" not in s
    assert s.startswith("(silence")
    for w in words:  # every block keeps at least its first term, and that term comes first in its group
        assert f"({w} OR " in s or f"({w})" in s, w


def test_gscholar_keeps_every_alternative_of_an_or_of_plain_blocks():
    """A string without field codes imports one block per term; the Scholar string once kept only the first of each OR
    ("(voice) (employee) (manag)") though every term fit (tenth round)."""
    from wider.search.parse_ebsco import parse_ebsco
    q = parse_ebsco('(voice* OR silence* OR "speak* up" OR whistleblow*) AND (employee* OR worker* OR staff) '
                    'AND (manag* OR supervis* OR leader*)')
    s = render_gscholar(q)
    assert s == '(voice OR silence OR "speak up" OR whistleblow) (employee OR worker OR staff) (manag OR supervis OR leader)', s


def test_gscholar_fills_the_string_up_to_the_limit():
    """A fixed share of terms per block once left a 185-character string although every term fit (seventh round)."""
    blocks = [Block(key=f"b{i}", terms=[f"t{i}word{j}" for j in range(5)]) for i in range(4)]
    q = Query(blocks=blocks, logic={"and": [b.key for b in blocks]})
    s = render_gscholar(q)
    # all 20 terms fit: all are kept, in their blocks' order
    assert len(s) <= BUDGET and s == " ".join("(" + " OR ".join(f"t{i}word{j}" for j in range(5)) + ")" for i in range(4)), s
    # with more terms than fit, the longest block is trimmed first and the string ends near the limit
    blocks = [Block(key="b0", terms=[f"long{j}" for j in range(80)]), Block(key="b1", terms=["a1", "a2", "a3"])]
    q = Query(blocks=blocks, logic={"and": ["b0", "b1"]})
    s = render_gscholar(q)
    assert BUDGET - 12 < len(s) <= BUDGET and "(a1 OR a2 OR a3)" in s, (len(s), s)
    paste = " ".join(next(r for r in __import__("wider.search.catalogue", fromlist=["catalogue"]).catalogue()
                          if r["key"] == "gscholar")["paste"].split())
    assert "keeps as many terms of each required block as fit, trimming the longest block first" in paste
