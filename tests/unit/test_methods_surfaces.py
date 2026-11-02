"""The methods document's CLI, API and guide links (methods-explainer D8, tasks 3.1, 3.2, 3.4), on the fixture topics
or the shipped ones; no project data is read."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.cli_notebook import guide_lines
from wider.core.project import Project
from wider.guide import guide, pages
from wider.guide import methods as m
from wider.ui.app import create_app

from .test_methods_current import FIXTURE, shipped_corpus


def _cli(*args):
    res = CliRunner().invoke(cli_app, list(args))
    return res.exit_code, res.output


def test_cli_methods_outside_project(tmp_path, monkeypatch):
    """`wider guide methods` in a folder that is not a WIDER project lists every topic and exits 0."""
    monkeypatch.chdir(tmp_path)
    code, out = _cli("guide", "methods")
    assert code == 0
    for t in m.topics():   # each topic with its review status on the next line
        assert f"{t['id']}  {t['title']} — {t['context']}\n  {m.review_line(t['review'])}\n" in out
    assert f"\n  {m.review_line(m.glossary_review())}" in out.split("Glossary (wider guide methods glossary)")[1]


def test_cli_methods_topic_json_matches_loader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tid = m.topics()[0]["id"]
    code, out = _cli("guide", "methods", tid, "--json")
    assert code == 0 and json.loads(out) == m.topic(tid)
    code, out = _cli("guide", "methods", tid)
    t = m.topic(tid)
    assert code == 0 and out.splitlines()[:3] == [t["title"], t["context"], m.review_line(t["review"])]
    assert all(layer["heading"] in out for layer in t["layers"])


def test_cli_methods_glossary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = _cli("guide", "methods", "glossary")
    assert code == 0 and out.startswith("Glossary\n")
    assert "recall (also: sensitivity; share of relevant papers found)" in out
    code, out = _cli("guide", "methods", "glossary", "--json")
    assert code == 0 and json.loads(out) == m.glossary()


def test_bad_topic_file_cli_and_route(tmp_path, monkeypatch):
    """guide-assistant D22: a topic file saved with its headings out of order. `wider guide methods` and GET
    /api/methods list the others with the problem sentence above them (exit 0); the topic itself exits 1 and answers
    404 with that sentence. The server reads the fix with no restart."""
    import shutil
    root = tmp_path / "methods"
    shutil.copytree(FIXTURE, root)
    monkeypatch.setattr(m, "HERE", root)
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(tmp_path / "state"))
    assert client.get("/api/methods").json()["problems"] == []
    beta = root / "beta.md"
    beta.write_text(beta.read_text(encoding="utf-8").replace("## In short", "## For now"), encoding="utf-8")
    [(file, problem)] = m.problems()
    line = m.problem_line(file, problem)
    code, out = _cli("guide", "methods")
    assert code == 0 and out.splitlines()[0] == line and "alpha  " in out and "beta  " not in out
    code, out = _cli("guide", "methods", "--json")
    assert code == 0 and json.loads(out)["problems"] == [{"file": "beta.md", "text": line}]
    code, out = _cli("guide", "methods", "beta")
    assert code == 1 and out.strip() == line
    idx = client.get("/api/methods").json()
    assert idx["problems"] == [{"file": "beta.md", "text": line}] and [t["id"] for t in idx["topics"]] == ["alpha"]
    r = client.get("/api/methods/beta")
    assert r.status_code == 404 and r.json()["detail"] == line
    beta.write_text(beta.read_text(encoding="utf-8").replace("## For now", "## In short"), encoding="utf-8")
    assert client.get("/api/methods/beta").status_code == 200 and client.get("/api/methods").json()["problems"] == []


def test_cli_methods_unknown_topic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out = _cli("guide", "methods", "no-such-topic")
    assert code == 1 and out.strip() == "there is no such methods topic: no-such-topic"
    code, out = _cli("guide", "dedup", "ranking")
    assert code == 2 and "only wider guide methods takes a topic" in out


def test_methods_not_a_guide_page():
    """`methods` is never a guide page, so `wider guide methods` always means the methods document."""
    assert "methods" not in pages() and m.GLOSSARY not in pages()


def test_review_line():
    assert m.review_line({"status": "reviewed", "date": "2026-11-02"}) == "Reviewed by the author on 2026-11-02."
    assert m.review_line({"status": "drafted", "date": "2026-11-02"}) == "Drafted; not yet reviewed by the author."
    assert m.review_line({}) == "Drafted; not yet reviewed by the author."


def test_api_methods_same_as_cli(tmp_path, monkeypatch):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    try:
        client = TestClient(create_app(p))
        monkeypatch.chdir(tmp_path)
        assert client.get("/api/methods").json() == json.loads(_cli("guide", "methods", "--json")[1])
        tid = m.topics()[-1]["id"]
        assert client.get(f"/api/methods/{tid}").json() == json.loads(_cli("guide", "methods", tid, "--json")[1])
        r = client.get("/api/methods/no-such-topic")
        assert r.status_code == 404 and r.json()["detail"] == "there is no such methods topic: no-such-topic"
    finally:
        p.close()


def test_api_methods_without_project(tmp_path, monkeypatch):
    """The routes never open the project: they answer even when the project cannot be read."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    client = TestClient(create_app(p))
    p.close()
    monkeypatch.setattr(Project, "find", lambda *a, **k: (_ for _ in ()).throw(AssertionError("project opened")))
    assert client.get("/api/methods").status_code == 200
    assert client.get(f"/api/methods/{m.topics()[0]['id']}").status_code == 200


def test_guide_lists_methods_topics():
    """`for_page` names the topics whose pages include the page (fixture topics)."""
    assert [t["id"] for t in m.for_page("sample", FIXTURE)] == ["alpha", "beta"]
    assert [t["id"] for t in m.for_page("recall", FIXTURE)] == ["alpha"]
    assert m.for_page("dedup", FIXTURE) == []


@shipped_corpus
def test_guide_lists_shipped_sample_topic(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    try:
        assert "stratified-sampling" in guide(p, "sample")["methods"]
    finally:
        p.close()


def test_guide_text_prints_methods_line():
    g = {"can_do": "x", "notices": [], "title": "T", "parts": [], "methods": ["stratified-sampling"]}
    title = m.topic("stratified-sampling")["title"]
    assert guide_lines(g)[-1] == f"Why WIDER works this way: {title} (stratified-sampling)"
    assert not any("Why WIDER" in x for x in guide_lines({**g, "methods": []}))


@shipped_corpus
def test_cli_guide_sample_names_methods(tmp_path):
    """`wider guide sample --json` lists stratified-sampling under methods; the text ends with the methods line."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.close()
    code, out = _cli("guide", "sample", "--json", "--dir", str(tmp_path / "r"))
    assert code == 0 and "stratified-sampling" in json.loads(out)["methods"]
    code, out = _cli("guide", "sample", "--dir", str(tmp_path / "r"))
    title = m.topic("stratified-sampling")["title"]
    last = out.strip().splitlines()[-1]
    assert code == 0 and last.startswith("Why WIDER works this way: ") and f"{title} (stratified-sampling)" in last


def test_help_links_methods():
    """The Help page holds a link to #methods; help.md names the page and the command."""
    text = (Path(m.__file__).parent.parent / "pages" / "help.md").read_text(encoding="utf-8")
    assert "#methods" in text and "Methods: why WIDER works this way" in text
    from .a11y_dom import section
    assert '<a href="#methods">Methods: why WIDER works this way</a>' in section("page-help")
