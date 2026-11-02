"""Query checking (change 2c, capability query-checking): the translation ledger, the platform-aware linter, the
string check, the parsers for the Boolean family, the property tests and the PRESS second reader."""
from __future__ import annotations

import html
import json
import random
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_dry_run import _bench, _cli, _client, _page_blocks, _save
from wider.core.project import Project
from wider.search.ir import Query
from wider.search.translation import ledger, ledger_lines, nothing_lost
from wider.ui.app import create_app

SIX = ("ebsco", "scopus", "wos", "proquest", "ovid", "pubmed")
UNVERIFIED = "keyword field code not yet verified against the platform's help page"


def _rows(rows, kind=None, reason=None):
    return [r for r in rows if (kind is None or r["kind"] == kind) and (reason is None or r["reason"] == reason)]


def _texts(rows, kind=None, reason=None):
    return [r["text"] for r in _rows(rows, kind, reason)]


# ------------------------------------------------------------------ 4.1 translation ledger

def test_translation_ledger(tmp_path):
    """Scenario "PubMed proximity and a keywords field"."""
    syn = {"blocks": [{"key": "turnover", "name": "Turnover", "terms": [{"near": ["employee", "turnover"], "distance": 5}]},
                      {"key": "silence", "name": "Silence", "terms": ["silence", "voice"], "fields": ["keywords"]}],
           "logic": {"and": ["turnover", "silence"]}}
    q = Query.model_validate(syn)
    pub, sco, ebs = ledger(q, "pubmed"), ledger(q, "scopus"), ledger(q, "ebsco")
    assert any(t.startswith("proximity rendered as AND") for t in _texts(pub, "lost", "near_as_and")), pub
    assert "[tiab] includes author keywords" in _texts(pub, "unverified", "render_note"), pub
    assert "keywords searched more widely: the code also covers title and abstract" in _texts(sco, "wider", "keywords_wider")
    assert nothing_lost(ebs) and ledger_lines(ebs)[0] == "nothing lost", ebs
    assert f"KW: {UNVERIFIED}" in _texts(ebs, "unverified", "field_code_unverified"), ebs
    assert not nothing_lost(pub) and not nothing_lost(sco)
    # every row has a kind, a reason and a text; each row once
    for k in SIX:
        rows = ledger(q, k)
        assert all(set(r) == {"kind", "reason", "text"} and r["kind"] in ("lost", "wider", "unverified") for r in rows)
        assert len({json.dumps(r) for r in rows}) == len(rows)
    # the same ledger on every surface: `wider query ledger`, `query render --ledger`, the route and the page
    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=SIX)
    _save(p, syn)
    js = _cli(p, "query", "ledger", "--json")
    assert js.exit_code == 0, js.output
    assert json.loads(js.output) == {k: ledger(q, k) for k in SIX}
    one = _cli(p, "query", "ledger", "--platform", "ebsco")
    assert one.exit_code == 0 and one.output.splitlines()[:2] == ["# ebsco", "nothing lost"], one.output
    r = _cli(p, "query", "render", "pubmed", "--ledger", "--compact")
    assert r.exit_code == 0, r.output
    for ln in ledger_lines(pub):
        assert f"# ledger: {ln}" in r.output
    route = _client(p).get("/api/query/render").json()
    assert route["ledgers"] == {k: ledger(q, k) for k in SIX}
    cards = html.unescape(_page_blocks(_client(p))["cards"])
    ebsco_card = re.search(r'data-ledger="ebsco"[^>]*>(.*?)</ul>', cards).group(1)
    assert ebsco_card.startswith("<li>nothing lost</li>") and f"KW: {UNVERIFIED}" in ebsco_card
    pub_card = re.search(r'data-ledger="pubmed"[^>]*>(.*?)</ul>', cards).group(1)
    assert "nothing lost" not in pub_card and 'data-reason="near_as_and"' in pub_card
    p.close()


def test_ledger_lists_silent_changes():
    """Scenario "Silent changes are listed"."""
    q = Query.model_validate({
        "blocks": [{"key": "demand", "terms": [{"near": ["job", "demand"], "distance": 5, "ordered": True}]},
                   {"key": "org", "terms": ["organi*", '"job demand"'], "fields": ["title"]}],
        "logic": {"and": ["demand", "org"]}})
    led = {k: ledger(q, k) for k in SIX}
    for k in ("wos", "ovid"):
        assert _rows(led[k], "lost", "ordered_as_unordered"), (k, led[k])
    for k in ("ebsco", "scopus", "proquest"):   # ordered proximity exists there
        assert not _rows(led[k], reason="ordered_as_unordered"), k
    assert _texts(led["ovid"], "lost", "proximity_counting") == ["ADJ5 allows 4 words between; the query asked for 5"]
    assert any("title" in t and "title and abstract" in t for t in _texts(led["pubmed"], "wider", "field_widened"))
    assert any("at most five characters" in t and "organi*" in t for t in _texts(led["proquest"], "lost", "truncation_cap"))
    assert any("variants of the phrase" in t and '"job demand"' in t for t in _texts(led["scopus"], "wider", "loose_phrase"))
    assert any("lemmatisation off" in t and "organi*" in t for t in _texts(led["wos"], "lost", "variants_off"))
    assert nothing_lost(led["ebsco"])
    # PubMed: proximity as AND only once, no ordered row on top; a phrase with * loses it
    assert _rows(led["pubmed"], "lost", "near_as_and") and not _rows(led["pubmed"], reason="ordered_as_unordered")
    pt = Query.model_validate({"blocks": [{"key": "a", "terms": ['"job demand*"']}], "logic": "a"})
    assert _rows(ledger(pt, "pubmed"), "lost", "phrase_truncation") and not _rows(ledger(pt, "ebsco"), reason="phrase_truncation")
    # Ovid: an operator word inside an unquoted phrase
    ow = Query.model_validate({"blocks": [{"key": "a", "terms": ['"health and safety"']}], "logic": "a"})
    assert _rows(ledger(ow, "ovid"), "lost", "phrase_operator_word") and not _rows(ledger(ow, "ebsco"), reason="phrase_operator_word")
    # PubMed: a block with every field is still listed ([tiab] for all of them); a title+abstract block is not
    ta = Query.model_validate({"blocks": [{"key": "a", "terms": ["x"]}], "logic": "a"})
    assert not _rows(ledger(ta, "pubmed"), reason="field_widened")
    # Google Scholar: blocks left out
    gs = ledger(q, "gscholar")
    assert any("demand" in t for t in _texts(gs, "lost", "block_left_out")), gs


def test_ledger_next_to_archived_string(tmp_path, monkeypatch):
    """Scenario "Ledger next to the archived string"."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    from wider.search import archive as arch
    from wider.search.render import platforms

    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=("ebsco",))
    _save(p, {"blocks": [{"key": "a", "name": "A", "terms": ["silence"], "fields": ["keywords"]}], "logic": "a"})
    sdir = arch.archive_dir(p)
    # an older string, archived before ledgers: its file and index row, no ledger next to it
    first = arch.archive(p, 1)
    old = first["new"][0]["file"]
    (sdir / arch.ledger_name(old)).unlink()
    old_name = "2026-01-01_synonyms-v1_ebsco.txt"
    (sdir / old).rename(sdir / old_name)
    idx = (sdir / "archive.csv").read_text(encoding="utf-8").replace(old, old_name)
    (sdir / "archive.csv").write_text(idx, encoding="utf-8")
    # a new version archived today, then the syntax changes and the same version is archived again the same day
    _save(p, {"blocks": [{"key": "a", "name": "A", "terms": ["silence", "voice"], "fields": ["keywords"]}], "logic": "a"})
    a = arch.archive(p, 2)
    kw = dict(platforms()["ebsco"]["field_codes"]["keywords"])
    monkeypatch.setitem(platforms()["ebsco"]["field_codes"], "keywords", {**kw, "code": "SU"})
    b = arch.archive(p, 2)
    names = [a["new"][0]["file"], b["new"][0]["file"]]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d_synonyms-v2_ebsco_[0-9a-f]{8}\.txt", names[1]), names
    for name, code in zip(names, ("KW", "SU")):
        led = json.loads((sdir / arch.ledger_name(name)).read_text(encoding="utf-8"))
        assert led["platform"] == "ebsco" and led["synonyms_version"] == 2
        assert f"{code}: {UNVERIFIED}" in _texts(led["rows"]), led
    ents = {e["file"]: e["ledger"] for e in arch.entries(p)}
    assert ents == {old_name: None, names[0]: arch.ledger_name(names[0]), names[1]: arch.ledger_name(names[1])}
    # the CLI list says "no ledger" for the older string; the page says it in plain words (UX review F11)
    lst = _cli(p, "query", "archive", "--list")
    assert lst.exit_code == 0 and "no ledger (archived before ledgers)" in lst.output, lst.output
    d = TestClient(create_app(Project.load(p.root))).get("/api/query/archive").json()
    r = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\n"
                 f"replies['/api/query/archive'] = {json.dumps(d)};\n"
                 f"{(STATIC / 'outputs.js').read_text(encoding='utf-8')}\nawait window.loadOutputs.loadArchive();\n"
                 + out("{html: $('#q-archive-table').innerHTML}"))
    rows = re.findall(r"<tr>(.*?)</tr>", r["html"], re.S)
    old_row = next(x for x in rows if old_name in x)
    assert "none (archived before translation notes were kept)" in old_row
    for name in names:
        assert arch.ledger_name(name) in next(x for x in rows if name + '"' in x or name + "<" in x)
    # the package carries the ledgers in search_strings/
    import zipfile

    from wider.recall.outputs import write_package
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    for name in names:   # the share bundle holds the archive (else the package's own search_strings/)
        assert any(n.endswith(f"search_strings/{arch.ledger_name(name)}") for n in z.namelist()), z.namelist()
    p.close()


# ------------------------------------------------------------------ 4.2 platform-aware linter

def test_platform_aware_lint_rules(tmp_path):
    """Scenario "Platform rules"."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js
    from wider.search.lint import lint

    syn = {"blocks": [{"key": "work", "name": "Work", "terms": ["wor*", "worker", "*", "self-*"]},
                      {"key": "near", "name": "Near", "terms": [{"near": ["job", "demand"], "distance": 5}]}],
           "logic": {"and": ["work", "near"]}}
    q = Query.model_validate(syn)
    # the saved schema keeps distances in 1-15, inside every range; a parsed string can hold more before it is saved
    far = Query.model_validate({**syn, "blocks": [syn["blocks"][0], {"key": "near", "name": "Near", "terms": [
        {"near": ["job", "demand"], "distance": 150}]}]})

    def msgs(platform, term=None, query=q):
        return [i["message"] for i in lint(query, platform) if term is None or i["term"] == term]

    pub = lint(q, "pubmed")
    assert msgs("pubmed", "wor*") == ["short truncation stem: under PubMed's minimum of four letters"]
    assert msgs("pubmed", "worker") == ["term covered by wor*: it adds nothing"]
    assert any(m.startswith("wildcard after a hyphen") for m in msgs("pubmed", "self-*"))
    star = [i for i in pub if i["term"] == "*"]
    assert len(star) == 1 and star[0]["level"] == "error", star
    assert star[0]["message"].startswith("truncation stem of two letters or fewer floods the search")
    assert "standalone wildcard" in star[0]["message"]
    # EBSCOhost's minimum is three: no stem warning for wor*, the other findings stay
    assert msgs("ebsco", "wor*") == [] and msgs("ebsco", "worker") == ["term covered by wor*: it adds nothing"]
    # proximity range: 150 is outside Ovid's 1-99 and the strictest, inside EBSCOhost's 1-255 and Scopus's open range;
    # PubMed has no range check (its ledger lists the AND); the "loose" warning over 10 stays everywhere
    assert any("outside Ovid's range (1-99)" in m for m in msgs("ovid", query=far))
    assert any("outside" in m for m in msgs(None, query=far))
    for k in ("ebsco", "scopus", "pubmed"):
        assert not any("outside" in m for m in msgs(k, query=far)), k
        assert "proximity distance 150 is loose" in msgs(k, query=far)
    assert not any("outside" in m or "loose" in m for k in (None, *SIX) for m in msgs(k))
    # scenario "The platform sets the limits": wor* and a proximity of 120
    lim = Query.model_validate({"blocks": [{"key": "a", "terms": ["wor*", {"near": ["job", "demand"], "distance": 120}]}],
                                "logic": "a"})

    def kinds(platform):
        ms = msgs(platform, query=lim)
        assert "proximity distance 120 is loose" in ms, (platform, ms)
        return (any(m.startswith("short truncation stem") for m in ms), any("outside" in m for m in ms))
    assert kinds("ebsco") == (False, False)
    assert kinds("ovid") == (False, True)
    assert kinds("pubmed") == (True, False)
    assert kinds(None) == (True, True)
    assert "proximity distance 120 is outside the strictest platform's range (1-99)" in msgs(None, query=lim)
    # without a platform: the strictest minimum (four), worded as before
    assert msgs(None, "wor*") == ["short truncation stem: check what it expands to"]
    # the same findings on the CLI, the route and the panel; the saved schema refuses a lone * (two characters at
    # least), so the saved query leaves it out and the * rule guards a query not yet saved (a parsed import)
    saved = {**syn, "blocks": [{**syn["blocks"][0], "terms": ["wor*", "worker", "self-*"]}, syn["blocks"][1]]}
    q = Query.model_validate(saved)
    pub = lint(q, "pubmed")
    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=SIX)
    _save(p, saved)
    r = _cli(p, "query", "lint", "--platform", "pubmed")
    assert r.exit_code == 0, r.output   # warnings only
    assert "PubMed's minimum of four" in " ".join(r.output.split())
    e = _cli(p, "query", "lint", "--platform", "ebsco")
    assert "minimum" not in e.output
    bad = _cli(p, "query", "lint", "--platform", "nowhere")
    assert bad.exit_code == 1 and "unknown platform" in bad.output
    c = _client(p)
    assert c.get("/api/query/render?platform=pubmed").json()["lint"] == pub
    assert c.get("/api/query/render?platform=").json()["lint"] == lint(q)
    assert c.get("/api/query/render?platform=nowhere").status_code == 400
    urls = ["/api/query/platforms", "/api/platforms", "/api/query/render", "/api/query/render?compact=true",
            "/api/query/render?platform=pubmed"]
    data = {u: c.get(u).json() for u in urls}
    body = js_function(APP_JS, "async function loadQuery(") + """
const api = async (u) => { if (!(u in DATA)) throw new Error("unexpected call " + u); return DATA[u]; };
const el = () => ({ innerHTML: "", textContent: "", value: "", querySelector: () => el() });
const els = {};
const $ = (sel) => (els[sel] ||= el());
const document = { querySelectorAll: () => [] };
await loadQuery();
const first = $("#q-lint").innerHTML, opts = $("#q-lint-platform").innerHTML;
$("#q-lint-platform").value = "pubmed";
await $("#q-lint-platform").onchange();
console.log(JSON.stringify({ first, opts, after: $("#q-lint").innerHTML }));"""
    out = run_js(body, data)
    assert 'value="pubmed"' in out["opts"] and "strictest of all platforms" in out["opts"]
    assert "minimum of four" not in html.unescape(out["first"])
    assert "PubMed&#39;s minimum of four letters" in out["after"] or "PubMed's minimum of four letters" in html.unescape(out["after"])
    p.close()



def test_clean_query_lint(tmp_path):
    """Scenario "A clean query": the CLI prints "no issues" and exits 0; the panel summary reads "Syntax check: 0 issues"."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js
    from wider.search.lint import lint

    clean = {"blocks": [{"key": "work", "name": "Work", "terms": ["worker", "employee"]},
                        {"key": "voice", "name": "Voice", "terms": ["silence", "dissent"]}],
             "logic": {"and": ["work", "voice"]}}
    assert lint(Query.model_validate(clean)) == []
    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=SIX)
    _save(p, clean)
    r = _cli(p, "query", "lint")
    assert r.exit_code == 0 and "no issues" in r.output, r.output
    c = _client(p)
    urls = ["/api/query/platforms", "/api/platforms", "/api/query/render", "/api/query/render?compact=true"]
    data = {u: c.get(u).json() for u in urls}
    assert data["/api/query/render"]["lint"] == []
    body = js_function(APP_JS, "async function loadQuery(") + """
const api = async (u) => { if (!(u in DATA)) throw new Error("unexpected call " + u); return DATA[u]; };
const els = {};
const el = (k) => (els[k] ||= { innerHTML: "", textContent: "", value: "", querySelector: (q) => el(k + " " + q) });
const $ = (sel) => el(sel);
const document = { querySelectorAll: () => [] };
await loadQuery();
console.log(JSON.stringify({ summary: el("#q-lint-wrap summary").textContent, rows: $("#q-lint").innerHTML }));"""
    out = run_js(body, data)
    assert out["summary"] == "Syntax check: 0 issues" and out["rows"] == ""
    p.close()


# ------------------------------------------------------------------ 4.3 string check and parsers

WORDS = ["voice", "silence", "employee", "worker", "nurse", "burnout", "stress", "strain", "demand", "turnover",
         "leader", "climate", "safety", "speaking", "upward", "dissent", "retention", "engagement", "justice", "trust",
         "support", "conflict", "team", "manager", "career", "outcome", "report", "concern", "issue", "problem"]
FIELD_SETS = [["title", "abstract"], ["title"], ["abstract"], ["title", "abstract", "keywords"], ["keywords"],
              ["title", "keywords"]]


def synthetic_query(rng, proximity=True) -> Query:
    """A random query: one to four blocks of plain words, truncated stems, phrases (some truncated) and proximity
    (single words or an OR list, ordered or not), random fields, and a random AND/OR/NOT tree over the blocks."""
    pool = WORDS[:]
    rng.shuffle(pool)

    def term():
        r, w = rng.random(), pool.pop()
        if r < 0.25:
            return w[:rng.randint(4, len(w))] + "*"
        if r < 0.45:
            return f'"{w} {pool.pop()}{"*" if rng.random() < .3 else ""}"'
        if r < 0.6 and proximity:
            b = [pool.pop(), pool.pop()] if rng.random() < .5 else pool.pop()
            return {"near": [w, b], "distance": rng.randint(1, 15), "ordered": rng.random() < .4}
        return w

    blocks = [{"key": f"k{i}", "name": f"K{i}", "terms": [term() for _ in range(rng.randint(1, 3))],
               "fields": rng.choice(FIELD_SETS)} for i in range(rng.randint(1, 4))]

    def tree(ks):
        if len(ks) == 1:
            return ks[0]
        cut, op = rng.randint(1, len(ks) - 1), rng.choice(["and", "or"])
        left, right = tree(ks[:cut]), tree(ks[cut:])
        if op == "and" and rng.random() < .2 and isinstance(right, str):
            right = {"not": right}
        kids = []
        for x in (left, right):
            kids += x[op] if isinstance(x, dict) and op in x else [x]
        return {op: kids}
    return Query.model_validate({"blocks": blocks, "logic": tree([b["key"] for b in blocks])})


def synthetic_queries(proximity=True) -> list[Query]:
    rng = random.Random(7)
    return [synthetic_query(rng, proximity) for _ in range(50)]


def _stdin_cli(p, text, *args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    return CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)], input=text, env={"COLUMNS": "1000"})


def test_round_trip_property(tmp_path):
    """Scenario "Round trip on synthetic queries": rendered, then parsed, every query comes back as its canonical form
    for that platform, pretty and compact; PubMed's queries have no proximity (its ledger lists the AND)."""
    from wider.search.parse_boolean import canonical, parse
    from wider.search.render import render

    with_near, without = synthetic_queries(), synthetic_queries(proximity=False)
    assert sum(isinstance(t, dict) or hasattr(t, "near") for q in with_near for b in q.blocks for t in b.terms) > 20
    for platform in SIX:
        for i, q in enumerate(without if platform == "pubmed" else with_near):
            want = canonical(q, platform).model_dump()
            for pretty in (True, False):
                s = render(q, platform, pretty=pretty)
                assert parse(s, platform).model_dump() == want, (platform, i, pretty, s)
    p = Project.init(tmp_path / "rt")
    r = _stdin_cli(p, "(a AND (b OR c", "query", "parse", "--platform", "pubmed", "-")
    assert r.exit_code == 1, r.output
    out = " ".join(r.output.split())
    assert "could not parse: missing ): the ( at position 0 is never closed" in out
    assert "the ( at position 7 is never closed" in out
    p.close()


def test_preview_says_when_save_would_refuse(tmp_path):
    """ui-tester findings (bad input, 20 Oct 2026): a string that parses but does not validate as synonyms (a one-letter
    term, a lone *) says so on the preview, CLI and route alike; a missing file is one line, not a traceback."""
    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=SIX)
    c = _client(p)
    for text, term in (("(a AND b OR c)", "is too short"), ("*", "is too short")):
        f = tmp_path / "s.txt"
        f.write_text(text, encoding="utf-8")
        r = _cli(p, "query", "parse", "--platform", "pubmed", str(f))
        flat = " ".join(r.output.split())
        assert r.exit_code == 0 and "Save would refuse it: synonyms does not validate" in flat and term in flat, flat
        j = c.post("/api/query/parse", json={"text": text, "platform": "pubmed"}).json()
        assert j["save_refusal"].startswith("Save would refuse it") and term in j["save_refusal"]
    ok = c.post("/api/query/parse", json={"text": "(voice AND silence OR dissent)", "platform": "pubmed"}).json()
    assert ok["save_refusal"] is None
    assert p.store.scalar("SELECT COUNT(*) FROM artifacts WHERE kind='synonyms'") == 0
    r = _cli(p, "query", "parse", str(tmp_path / "missing.txt"))
    assert r.exit_code == 1 and "could not read" in r.output and "Traceback" not in r.output, r.output


def test_string_check(tmp_path):
    """Scenario "Checking an imported string": PubMed curly quotes, a lower-case or, a nested [tiab], each with its
    position, and the string parsed; an import of a mixed string warns and saves, an unbalanced one saves nothing."""
    from wider.construct import artifacts
    from wider.search.parse_boolean import parse
    from wider.search.string_check import check_string

    s = '“voice”[tiab] or (silence[tiab] OR dissent[tiab])[tiab]'
    found = check_string(s, "pubmed")
    assert {"level": "warning", "pos": 0, "message": 'curly quote “ read as a straight one (")'} in found
    assert {"level": "warning", "pos": 6, "message": 'curly quote ” read as a straight one (")'} in found
    assert {"level": "warning", "pos": 14, "message": "lower-case operator 'or' read as OR"} in found
    nested = [f for f in found if f["message"].startswith("field tag [tiab] nested in a field group ([tiab])")]
    assert [f["pos"] for f in nested] == [s.index("[tiab] OR"), s.index("[tiab])")], found
    assert all(f["level"] == "warning" for f in found)
    # Web of Science: the check names the NEAR over a grouped AND, and the refusal carries it
    from wider.search.string_check import checked_parse
    wos = "TS=(voice NEAR/3 (silence AND dissent))"
    assert [f["message"] for f in check_string(wos, "wos")] == [
        "NEAR/3 over a grouped AND: Web of Science does not allow AND inside a proximity group"]
    with pytest.raises(ValueError, match="does not allow AND inside a proximity group"):
        checked_parse(wos, "wos")
    q = parse(s, "pubmed")
    assert q.model_dump(mode="json")["logic"] == {"or": ["voice", "silence"]}
    assert [b.terms for b in q.blocks] == [['"voice"'], ["silence", "dissent"]]
    # the check and the parse are the same on the CLI (no project needed beyond the folder)
    p = Project.init(tmp_path / "sc")
    r = _stdin_cli(p, s, "query", "parse", "--platform", "pubmed", "--json", "-")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["findings"] == found
    # import: a mixed string warns how it was read, saves and exits 0
    mixed, unbalanced = tmp_path / "mixed.txt", tmp_path / "unbalanced.txt"
    mixed.write_text("(voice AND silence OR dissent)", encoding="utf-8")
    unbalanced.write_text("(voice AND (silence OR dissent)", encoding="utf-8")
    r = _cli(p, "query", "import", str(mixed))
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "warning at position 19: AND and OR mixed without parentheses: read as (voice AND silence) OR dissent" in out
    assert "synonyms v1" in out
    saved, content = artifacts.load(p, "synonyms")
    assert content["logic"] == {"or": [{"and": ["voice", "silence"]}, "dissent"]}
    # an unbalanced string: exit 1, the missing parenthesis named with its position, nothing saved
    r = _cli(p, "query", "import", "--platform", "ebsco", str(unbalanced))
    assert r.exit_code == 1
    assert "could not parse: missing ): the ( at position 0 is never closed" in " ".join(r.output.split())
    assert artifacts.latest(p, "synonyms")["version"] == saved["version"]
    assert _cli(p, "query", "import", "--platform", "nowhere", str(mixed)).exit_code == 1
    # parsed but refused by the synonyms schema (distance 1-15): a message, not a traceback, and nothing saved
    far = tmp_path / "far.txt"
    far.write_text("TI (voice N50 silence) OR AB (voice N50 silence)", encoding="utf-8")
    r = _cli(p, "query", "import", str(far))
    assert r.exit_code == 1 and "could not save: synonyms does not validate" in " ".join(r.output.split()), r.output
    assert artifacts.latest(p, "synonyms")["version"] == saved["version"]
    p.close()


def test_parse_and_import_routes(tmp_path):
    """POST /api/query/parse writes nothing; POST /api/query/import with a platform saves, with the findings."""
    from wider.construct import artifacts

    p = Project.init(tmp_path / "routes")
    c = _client(p)
    s = '“voice”[tiab] or silence[tiab]'
    r = c.post("/api/query/parse", json={"text": s, "platform": "pubmed"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["pos"] for f in body["findings"]] == [0, 6, 14]
    # an OR of terms with the same tag is one block, as PubMed renders a block
    assert [b["terms"] for b in body["blocks"]] == [['"voice"', "silence"]] and body["logic"] == "voice"
    assert artifacts.latest(p, "synonyms") is None
    bad = c.post("/api/query/parse", json={"text": "(a AND (b OR c", "platform": "pubmed"})
    assert bad.status_code == 400 and bad.json()["detail"].startswith("could not parse: missing ): the ( at position 0")
    assert c.post("/api/query/parse", json={"text": "voice", "platform": "nowhere"}).status_code == 400
    r = c.post("/api/query/import", json={"text": s, "platform": "pubmed", "note": "from a paper"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and len(r.json()["findings"]) == 3
    cur, content = artifacts.load(p, "synonyms")
    assert cur["version"] == 1 and content["blocks"][0]["terms"] == ['"voice"', "silence"]
    assert c.post("/api/query/import", json={"text": "(voice AND (silence OR dissent)"}).status_code == 400
    far = c.post("/api/query/import", json={"text": "TI (voice N50 silence)", "platform": "ebsco"})
    assert far.status_code == 400 and far.json()["detail"].startswith("could not save: synonyms does not validate")
    assert artifacts.latest(p, "synonyms")["version"] == 1
    p.close()


def test_import_block_on_the_page(tmp_path):
    """The "Import a string" block: the platform select, Preview (findings and blocks, nothing saved) and Save (the
    chosen platform sent, the findings listed)."""
    from tests.unit.test_query_roles import APP_JS, run_js

    page = (Path(__file__).parents[2] / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    block = page[page.index('<div id="q-import" hidden>'):page.index('<div id="q-blocks-wrap">')]
    assert ">Import a string</button>" in page
    assert all(f'<option value="{k}">' in block for k in SIX)
    assert 'id="q-import-preview"' in block and 'id="q-import-findings"' in block

    def handler(head):
        body = APP_JS[APP_JS.index(head):]
        return body[:body.index("\n};\n") + 3]
    p = Project.init(tmp_path / "page")
    c = _client(p)
    s = '“voice”[tiab] or silence[tiab]'
    data = {"parse": c.post("/api/query/parse", json={"text": s, "platform": "pubmed"}).json()}
    out = run_js("""
const el = () => ({ innerHTML: "", textContent: "", value: "", disabled: false });
const els = {}, asked = [];
const $ = (sel) => (els[sel] ||= el());
const confirmSave = async () => true, loadQuery = async () => {};
const api = async (u, opts) => { asked.push([u, JSON.parse(opts.body)]);
  if (u === "/api/query/parse") return DATA.parse;
  return { version: 4, findings: DATA.parse.findings }; };
""" + handler('$("#q-import-preview").onclick') + handler('$("#q-import-save").onclick') + """
$("#q-import-text").value = """ + json.dumps(s) + """; $("#q-import-platform").value = "pubmed";
await $("#q-import-preview").onclick();
const preview = [$("#q-import-findings").innerHTML, $("#q-import-msg").textContent];
await $("#q-import-save").onclick();
console.log(JSON.stringify({ asked, preview, saved: [$("#q-import-findings").innerHTML, $("#q-import-msg").textContent] }));""",
                 data)
    assert out["asked"] == [["/api/query/parse", {"text": s, "platform": "pubmed"}],
                            ["/api/query/import", {"text": s, "platform": "pubmed"}]]
    shown = html.unescape(out["preview"][0])
    assert "warning at position 14: lower-case operator 'or' read as OR" in shown
    assert 'voice: title, abstract · "voice"; silence' in shown
    assert out["preview"][1] == "read as 1 block, nothing saved · 3 warnings"
    assert "curly quote" in html.unescape(out["saved"][0]) and out["saved"][1] == "saved synonyms v4"
    p.close()


# ------------------------------------------------------------------ 4.4 rendering properties

def test_rendering_properties():
    """Scenario "Rendered strings are lint-clean": over the synthetic queries, every string balances its parentheses,
    writes operators in capitals, quotes every multi-word term (Ovid's phrases are unquoted by its syntax) and keeps
    proximity inside the platform's range; a lint-clean query renders with no error-level string check."""
    from wider.search.lint import limits_for, lint
    from wider.search.parse_boolean import dialect, tokenize
    from wider.search.render import render
    from wider.search.string_check import check_string

    with_near, without = synthetic_queries(), synthetic_queries(proximity=False)
    clean = 0
    for platform in SIX:
        d, rng = dialect(platform), limits_for(platform)["range"]
        for i, q in enumerate(without if platform == "pubmed" else with_near):
            for pretty in (True, False):
                s = render(q, platform, pretty=pretty)
                toks = tokenize(s, d)
                depth = 0
                for t in toks:
                    depth += {"lp": 1, "rp": -1}.get(t.kind, 0)
                    assert depth >= 0, (platform, i, s)
                assert depth == 0, (platform, i, s)
                words = [t for t in toks if t.kind == "word"]
                for t in words:
                    if d.is_operator(t.text):
                        assert t.text == t.text.upper(), (platform, s)
                    if (near := d.prox_match(t.text)) and rng:
                        assert rng[0] <= near[0] <= (rng[1] or near[0]), (platform, s)   # None: no upper limit
                if platform != "ovid":   # two operand words side by side are an unquoted multi-word term
                    for a, b in zip(toks, toks[1:]):
                        assert not (a.kind == b.kind == "word" and not d.is_operator(a.text)
                                    and not d.is_operator(b.text)), (platform, i, a, b, s)
                if not any(f["level"] == "error" for f in lint(q, platform)):
                    clean += 1
                    errors = [f for f in check_string(s, platform) if f["level"] == "error"]
                    assert errors == [], (platform, i, s, errors)
    assert clean >= 250, clean   # most synthetic queries are lint-clean, so the last check is not vacuous


# ------------------------------------------------------------------ 4.5 PRESS second reader

REVIEW_SYN = {"blocks": [{"key": "silence", "name": "Silence", "terms": ["silence", '"employee voice"']},
                         {"key": "nurse", "name": "Nurses", "terms": ["nurse*", "nurse*"]}],
              "logic": {"and": ["silence", "nurse"]}}
A = {"item": "text_words", "quote": '"employee voice"', "note": "add speaking up", "severity": "revision"}


def _review_project(tmp_path, monkeypatch, responders, models=(("llama", "llama3.1:8b"), ("qwen", "qwen2.5:7b"))):
    """Synthetic project on Scopus with fake readers in project.yaml; every adapter built for a reader (the CLI, the
    route and the engine alike) answers with that reader's responder."""
    from wider.core.models import ProviderConfig
    from wider.providers import registry
    from wider.providers.fake import FakeProvider

    p = _bench(tmp_path, [("B001", "Development", "Nurses", "Nurses stay silent.", "silence")], platforms=("scopus",))
    _save(p, REVIEW_SYN)
    for name, model in models:
        p.config.providers.append(ProviderConfig(name=name, kind="fake", model=model))
    p.config.default_provider = models[0][0]
    p.save()
    made: dict[str, list] = {}

    def _make(cfg):
        fp = FakeProvider(cfg, responders.get(cfg.name))
        made.setdefault(cfg.name, []).append(fp)
        return fp
    monkeypatch.setattr(registry, "_make", _make)
    return p, made


def test_press_second_reader(tmp_path, monkeypatch):
    """Scenario "Two readers, agreed and unsure findings"."""
    from wider.core.backends import provider_rows
    from wider.search import review

    responders = {
        "llama": lambda req: {"findings": [A, {"item": "spelling_syntax", "quote": "nurse*", "note": "twice",
                                               "severity": "suggestion"}]},
        "qwen": lambda req: {"findings": [{**A, "quote": "employee voice", "note": "speaking up is missing"},
                                          {"item": "text_words", "quote": "burnout", "note": "add burnout",
                                           "severity": "suggestion"}]}}
    p, made = _review_project(tmp_path, monkeypatch, responders)
    # Settings twin: `wider providers stage review llama qwen`; one "review" row, review_2 its second select
    r = _cli(p, "providers", "stage", "review", "llama", "qwen")
    assert r.exit_code == 0 and "review: llama, qwen" in r.output, r.output
    p = Project.load(p.root)
    assert p.config.stage_providers == {"review": "llama", "review_2": "qwen"}
    rows = {d["name"]: d["stages"] for d in provider_rows(p)}
    assert (rows["llama"], rows["qwen"]) == (["review"], ["review"])
    c = _client(p)
    assert c.get("/api/providers").json()["stages"] == ["construct", "screen", "review"]
    versions = p.store.scalar("SELECT COUNT(*) FROM artifacts WHERE kind='synonyms'")
    strings = c.get("/api/query/render").json()["strings"]
    # before the run: the readers, their families and where they run; nothing is sent
    pre = c.get("/api/query/review").json()
    assert [(x["name"], x["family"], x["locality"]) for x in pre["readers"]] == [("llama", "llama", "test"),
                                                                               ("qwen", "qwen", "test")]
    assert pre["notices"] == [] and pre["source"] == "review stage" and pre["last"] is None and not made
    # the CLI run
    out = _cli(p, "query", "review")
    assert out.exit_code == 0, out.output
    text = out.output
    assert "second reader: 1 agreed, 1 unsure, 1 dropped: quote not found" in text, text
    assert "reader 1: llama (llama3.1:8b, family llama, test)" in text and "reader 2: qwen (qwen2.5:7b, family qwen, test)" in text
    assert '[agreed] text_words (revision, llama/llama3.1:8b#1, qwen/qwen2.5:7b#2): ""employee voice""' in text, text
    assert "[unsure] spelling_syntax (suggestion, llama/llama3.1:8b#1)" in text
    assert "not a human peer review" in text and "burnout" not in text
    rv = review.last_review(p)
    sc = rv["platforms"]["scopus"]
    assert [(f["item"], f["agreement"], f["backend"]) for f in sc["findings"]] == [
        ("text_words", "agreed", "llama"), ("spelling_syntax", "unsure", "llama")]
    assert sc["dropped"] == 1 and [x["family"] for x in rv["readers"]] == ["llama", "qwen"]
    stored = p.store.query("SELECT reader, item, quote, at FROM review_findings ORDER BY finding_id")
    assert [(s["reader"], s["item"]) for s in stored] == [("llama/llama3.1:8b#1", "text_words"),
                                                         ("llama/llama3.1:8b#1", "spelling_syntax"),
                                                         ("qwen/qwen2.5:7b#2", "text_words")]
    assert {s["at"] for s in stored} == {rv["at"]}
    # findings change nothing: the same synonyms versions and strings
    assert p.store.scalar("SELECT COUNT(*) FROM artifacts WHERE kind='synonyms'") == versions
    assert _client(p).get("/api/query/render").json()["strings"] == strings
    # the route twin gives the same review
    j = _client(p).post("/api/query/review", json={}).json()
    assert (j["agreed"], j["unsure"], j["dropped"]) == (1, 1, 1) and j["message"] == rv["message"]
    # one reader, or two with the same model: it reads twice, "same model twice"
    one = _cli(p, "query", "review", "--reader", "llama")
    assert one.exit_code == 0 and "same model twice" in one.output, one.output
    assert "reader 2: llama (llama3.1:8b" in one.output
    assert review.resolve_readers(p, ["llama", "llama"])["notices"][0].startswith("same model twice")
    # two different models of one family: "same family twice"
    from wider.core.models import ProviderConfig
    p.config.providers.append(ProviderConfig(name="big", kind="fake", model="meta-llama/Llama-3.1-70B"))
    p.save()
    fam = review.resolve_readers(p, ["llama", "big"])
    assert fam["families"] == ["llama", "llama"] and fam["notices"][0].startswith("same family twice")
    # the stage's other forms: one name clears review_2; no name clears both and the construct backend reads twice
    assert _client(p).post("/api/providers/stage", json={"stage": "review_2", "name": "qwen"}).status_code == 200
    _cli(p, "providers", "stage", "review", "qwen")
    assert Project.load(p.root).config.stage_providers == {"review": "qwen"}
    r = _cli(p, "providers", "stage", "review")
    assert r.exit_code == 0 and "the construct backend (reads twice)" in r.output
    q = Project.load(p.root)
    assert "review" not in q.config.stage_providers
    d = review.resolve_readers(q)
    assert [c.name for c in d["configs"]] == ["llama", "llama"] and d["source"] == "construct backend"
    assert d["notices"][0].startswith("same model twice")
    # a removed reader is named; a second name on another stage is refused
    q.config.stage_providers["review"] = "gone"
    with pytest.raises(ValueError, match="'gone' is not in project.yaml"):
        review.resolve_readers(q)
    assert _cli(p, "providers", "stage", "screen", "llama", "qwen").exit_code == 1
    p.close()


def test_second_reader_prompt(tmp_path, monkeypatch):
    """Scenario "What the reader is given"."""
    from wider.interview.ledger import record
    from wider.search import review

    p, made = _review_project(tmp_path, monkeypatch, {}, models=(("llama", "llama3.1:8b"),))
    record(p, "scope", "q:speak", {"subject": "speaking up stays out", "why": "a different construct"}, ["in", "out"],
           "in", "out", "chosen", "kept out of the silence block", "author", None)
    record(p, "scope", "q:mute", {"subject": "selective mutism", "why": "clinical"}, ["in", "out"], "in", None,
           "dismissed", "", "author", None)
    out = review.second_read(p)
    calls = made["llama"][0].calls
    assert len(calls) == 2 and calls[0].seed != calls[1].seed   # one reader twice: the second run takes the next seed
    user, system = calls[0].user, calls[0].system
    string = _client(p).get("/api/query/render").json()["strings"]["scopus"]
    assert string in user and '"key": "silence"' in user and '"employee voice"' in user   # the string and the tree
    assert "- wider: quotes also find variants of the phrase" in user                         # the ledger row
    assert "warning (block nurse): duplicate term" in user                                    # the lint warning
    assert "subject_headings: Subject headings" in user and "text_words: Text word searching" in user
    assert "speaking up stays out" in user and "selective mutism" not in user                 # dismissed: left out
    assert "Nurses stay silent" not in user and "Passage" not in user and "PRESS" in system   # no seed passage
    d = p.root / out["run_dir"]
    rows = json.loads((d / "calls.json").read_text(encoding="utf-8"))
    assert len(rows) == 2 and all(r["input_sha256"] and r["output_sha256"] and r["attempts"] == 1 for r in rows)
    assert out["run_ref"] == f"query/review/{out['at']}" and (d / "review.json").exists()
    usage = p.store.query("SELECT * FROM llm_usage WHERE stage='press_review'")
    assert len(usage) == 2 and {u["run_ref"] for u in usage} == {out["run_ref"]}
    assert not [k for k in usage[0].keys() if "text" in k or k == "prompt"]
    p.close()


def test_second_reader_refuses_agent_with_tools(tmp_path, monkeypatch):
    """Scenario "A CLI agent with tools is not used as a reader"."""
    from wider.providers.presets import config_from_preset
    from wider.search import review

    p, made = _review_project(tmp_path, monkeypatch, {})
    cfg = config_from_preset("claude-code", name="agent", model="m")
    i = cfg.command.index("--tools")
    p.config.providers.append(cfg.model_copy(update={"command": cfg.command[:i] + cfg.command[i + 2:]}))
    p.config.providers.append(config_from_preset("custom-cli", name="mytool", model="m", command=["mytool"]))
    p.save()
    r = _cli(p, "query", "review", "--reader", "agent")
    assert r.exit_code == 2 and 'lacks --tools ""' in r.output and "nothing is sent" in r.output, r.output
    r = _cli(p, "query", "review", "--reader", "llama", "--reader", "mytool")
    assert r.exit_code == 2 and "knows no command-line flags" in r.output, r.output
    c = _client(p)
    j = c.post("/api/query/review", json={"readers": ["agent"]})
    assert j.status_code == 409 and "lacks" in j.json()["detail"]
    with pytest.raises(review.ReaderRefused):
        review.second_read(p, readers=["agent"])
    pre = c.get("/api/query/review", params={"readers": ["agent"]}).json()
    assert pre["readers"][0]["refused"].startswith("the reader is refused")
    assert not made and not (p.root / "runs" / "query" / "review").exists()   # nothing built, nothing sent or written
    assert p.store.scalar("SELECT COUNT(*) FROM llm_usage WHERE stage='press_review'") == 0
    p.close()



def test_failed_reader_is_not_a_review(tmp_path, monkeypatch):
    """ui-tester findings (bad input, 20 Oct 2026): each reader call names its own backend in the provenance view, a
    failed call says so, and a platform whose calls all failed reads "no answer", not "no findings", on the CLI, the
    card and in PRISMA-S item 14."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js
    from wider.providers.base import ProviderError
    from wider.recall import prisma_s
    from wider.search import provenance, review

    def down(req):
        raise ProviderError("HTTP 404")
    p, _ = _review_project(tmp_path, monkeypatch, {"llama": lambda req: {"findings": []}, "qwen": down})
    out = _cli(p, "query", "review", "--reader", "llama", "--reader", "qwen")
    assert out.exit_code == 0, out.output
    assert "no findings from the reader that answered; 1 call(s) failed" in out.output, out.output
    calls = provenance.view(p, 1)["model_calls"]
    rv = [c for c in calls if c["stage"] == "press_review"]
    assert [(c["provider"], c["model"], c["failed"]) for c in rv] == [("llama", "llama3.1:8b", False),
                                                                      ("qwen", "qwen2.5:7b", True)]
    assert rv[1]["note"].startswith("failed: ") and "HTTP 404" in rv[1]["note"]
    text = "\n".join(provenance.lines(provenance.view(p, 1)))
    assert "(qwen, qwen2.5:7b): input" in text and "output failed: " in text
    # both readers down: no answer, and PRISMA-S says the run is incomplete
    p2, _ = _review_project(tmp_path / "b", monkeypatch, {"llama": down, "qwen": down})
    out = _cli(p2, "query", "review")
    assert "no answer: every call failed, so this is not a review" in out.output and "no findings\n" not in out.output
    # PRISMA-S item 14's lines (before any import the checklist itself says "nothing imported yet")
    assert "2 call(s) failed, so the run is incomplete" in prisma_s._peer_review(p2)[0]
    pl = review.last_review(p2)["platforms"]["scopus"]
    body = js_function(APP_JS, "function reviewEmpty(") + """
console.log(JSON.stringify({ all: reviewEmpty(DATA.all), some: reviewEmpty(DATA.some), none: reviewEmpty(DATA.none) }));"""
    js = run_js(body, {"all": pl, "some": {**pl, "failed": 1}, "none": {**pl, "failed": 0}})
    assert js == {"all": review.empty_text(pl), "some": review.empty_text({**pl, "failed": 1}), "none": "no findings"}



def test_review_runs_in_one_second_keep_their_records(tmp_path, monkeypatch):
    """Risk check of 2c (R3): two second-reader runs started in the same second write two run folders, so neither
    review.json nor calls.json is overwritten and PRISMA-S lists both runs."""
    import wider.core.provenance as prov
    from wider.recall import prisma_s
    from wider.search import review

    monkeypatch.setattr(prov, "stamp", lambda: "20261020T120000Z")
    p, _ = _review_project(tmp_path, monkeypatch, {"llama": lambda req: {"findings": [A]},
                                                     "qwen": lambda req: {"findings": []}})
    r1 = review.second_read(p, version=1, readers=["llama"])
    r2 = review.second_read(p, version=1, readers=["qwen"])
    assert r1["run_dir"] != r2["run_dir"] and r2["run_dir"].endswith("-2")
    for r, name in ((r1, "llama"), (r2, "qwen")):
        saved = json.loads((p.root / r["run_dir"] / "review.json").read_text(encoding="utf-8"))
        assert {x["name"] for x in saved["readers"]} == {name}
        assert (p.root / r["run_dir"] / "calls.json").exists()
    assert review.last_review(p)["run_dir"] == r2["run_dir"]
    assert len(prisma_s._peer_review(p)) == 2   # PRISMA-S item 14's lines

def test_second_reader_card_on_the_page(tmp_path, monkeypatch):
    """The "Second reader" card on the Query page: the reader selects, the readers line (family, where it runs, a
    refusal disables the run), the run posts {platform, readers}, a second consent dialog is retried, and the findings
    show agreed and unsure chips and the dropped count."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js

    page = (Path(__file__).parents[2] / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    card = page[page.index('id="q-review-card"'):]
    card = card[:card.index("</section>") if "</section>" in card else 3000]
    assert all(f'id="{i}"' in card for i in ("q-review-run", "q-review-r1", "q-review-r2", "q-review-platform",
                                              "q-review-readers", "q-review-out")) and "wider query review" in card
    responders = {"llama": lambda req: {"findings": [A]},
                  "qwen": lambda req: {"findings": [{**A, "quote": "employee voice"},
                                                    {"item": "limits_filters", "quote": "burnout", "note": "x",
                                                     "severity": "suggestion"}]}}
    p, _ = _review_project(tmp_path, monkeypatch, responders)
    c = _client(p)
    data = {"provs": c.get("/api/providers").json(),
            "pre": c.get("/api/query/review", params={"readers": ["llama", "qwen"]}).json(),
            "run": c.post("/api/query/review", json={"readers": ["llama", "qwen"]}).json()}
    out = run_js("""
const el = () => ({ innerHTML: "", textContent: "", value: "", disabled: false });
const els = {}, asked = [];
const $ = (sel) => (els[sel] ||= el());
let consent = 2;
const api = async (u, opts) => { asked.push([u, opts && opts.body ? JSON.parse(opts.body) : null]);
  if (u === "/api/providers") return DATA.provs;
  if (u.startsWith("/api/query/review") && !opts) return DATA.pre;
  if (consent-- > 0) { const e = new Error("consent"); e.status = 403; e.detail = { backend_consent_required: true }; throw e; }
  return DATA.run; };
""" + js_function(APP_JS, "function reviewFindingsHtml(") + "\n" + js_function(APP_JS, "async function loadReview(") + """
$("#q-review-r1"); $("#q-review-r2").value = "qwen";
await loadReview(["scopus"]);
$("#q-review-r1").value = "llama"; $("#q-review-platform").value = "scopus";
await $("#q-review-r1").onchange();
const readers = $("#q-review-readers").innerHTML, before = $("#q-review-run").disabled;
await $("#q-review-run").onclick({ currentTarget: $("#q-review-run") });
console.log(JSON.stringify({ asked, readers, before, r1: $("#q-review-r1").innerHTML, msg: $("#q-review-msg").textContent,
  out: $("#q-review-out").innerHTML }));""", data)
    posts = [a for a in out["asked"] if a[1]]
    assert posts == [["/api/query/review", {"platform": "scopus", "readers": ["llama", "qwen"]}]] * 3   # 2 dialogs, then the run
    assert "from Settings (review row)" in out["r1"] and not out["before"]
    shown = html.unescape(out["readers"])
    assert "reader 1: <b>llama</b> (llama3.1:8b, family llama)" in shown and "reader 2: <b>qwen</b>" in shown
    assert out["msg"] == data["run"]["message"] == "second reader: 1 agreed, 0 unsure, 1 dropped: quote not found"
    got = html.unescape(out["out"])
    assert 'data-agreement="agreed"' in got and '<span class="chip ok">agreed</span>' in got
    assert "1 dropped: quote not found" in got and "not a human peer review" in got
    # a refused reader: the line says why and the run button is disabled
    from wider.core.models import ProviderConfig
    p.config.providers.append(ProviderConfig(name="tool", kind="cli_agent", model="m", command=["mytool"]))
    p.save()
    data["pre"] = _client(p).get("/api/query/review", params={"readers": ["tool"]}).json()
    out = run_js("""
const el = () => ({ innerHTML: "", textContent: "", value: "", disabled: false });
const els = {};
const $ = (sel) => (els[sel] ||= el());
const api = async (u) => u === "/api/providers" ? DATA.provs : DATA.pre;
""" + js_function(APP_JS, "function reviewFindingsHtml(") + "\n" + js_function(APP_JS, "async function loadReview(") + """
$("#q-review-r1");
await loadReview(["scopus"]);
console.log(JSON.stringify({ readers: $("#q-review-readers").innerHTML, disabled: $("#q-review-run").disabled }));""", data)
    assert out["disabled"] and "knows no command-line flags" in html.unescape(out["readers"])
    p.close()
