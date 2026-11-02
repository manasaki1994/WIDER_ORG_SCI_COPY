"""Prior reviews (wider/prior): local parsing, storage, coverage, the policy gate, mocked chaining, the excerpt
switch. No network: every OpenAlex call goes through a fake client. Titles are synthetic and topic-neutral."""
from __future__ import annotations

import builtins
import re

import pytest
from fastapi.testclient import TestClient

from wider.construct import seed
from wider.construct.propose import build_prompt
from wider.core.project import Project
from wider.dedup.engine import run as dedup_run
from wider.ingest.importer import import_all
from wider.prior import chain, coverage, refs_pdf, reviews
from wider.prior.reviews import PolicyError
from wider.ui.app import create_app

CSV = ("Title,Abstract,DOI,Year,Authors,Journal\n"
       "Employee voice in teams,Speaking up at work.,10.1000/aaa,2019,Doe J,J Mgmt\n"
       "Silence at work and its costs,Staying quiet.,10.1000/bbb,2020,Roe R,J Org\n"
       "Unrelated weather study,Clouds.,10.1000/zzz,2018,Sky S,J Met\n")
RIS = "TY  - JOUR\nTI  - Employee voice in teams\nDO  - 10.1000/aaa\nPY  - 2019\nER  - \n\nTY  - JOUR\nTI  - Issue selling upward\nPY  - 2017\nER  - \n"
REFS_NUMBERED = """Some body text.

References

[1] Doe, J. (2019). Employee voice in teams. Journal of Management, 45(2), 1-20. https://doi.org/10.1000/aaa
[2] Roe, R. (2020). Silence at work and its costs. Journal of Organization, 3, 5-9.
[3] Sky, S. (2018). Unrelated weather study. Journal of Meteorology, 1, 1-2.

Appendix A
Table 1. Something else.
"""
REFS_AUTHOR_YEAR = """References

Doe, J. (2019). Employee voice in teams. Journal of Management, 45(2), 1-20.
Roe, R., & Poe, P. (2020). Silence at work and its costs. Journal of Organization, 3, 5-9.
"""


@pytest.fixture
def project(tmp_path):
    return Project.init(tmp_path / "r", platforms=["scopus"])


def _import_csv(project, platform="scopus", text=CSV, name="a.csv"):
    d = project.path("inputs", platform)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return import_all(project)


# ----------------------------------------------------------------- parsing (local)
def test_reference_text_numbered_and_author_year():
    rows, warnings = refs_pdf.parse_reference_text(REFS_NUMBERED)
    assert [r["title"] for r in rows] == ["Employee voice in teams", "Silence at work and its costs", "Unrelated weather study"]
    assert rows[0]["doi"] == "10.1000/aaa" and rows[0]["year"] == 2019 and rows[0]["confidence"] == "doi"
    assert rows[1]["doi"] is None and rows[1]["confidence"] == "parsed" and rows[1]["raw_ref"].startswith("Roe")
    assert "Appendix" not in " ".join(r["raw_ref"] for r in rows)
    rows2, _ = refs_pdf.parse_reference_text(REFS_AUTHOR_YEAR)
    assert [r["title"] for r in rows2] == ["Employee voice in teams", "Silence at work and its costs"]
    assert rows2[1]["first_author"] and rows2[1]["year"] == 2020


def test_reference_section_without_heading_warns():
    section, warnings = refs_pdf.reference_section("intro text\n" * 20 + "Doe, J. (2019). A title. J, 1.\n")
    assert warnings and "Doe" in section


def test_pdf_needs_optional_extra(tmp_path, monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("pypdf"):
            raise ImportError("no pypdf")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="wider\\[pdf\\]"):
        refs_pdf.extract_text(f)


def test_rows_from_text_modes():
    rows, _ = reviews.rows_from_text("10.1000/aaa\nSilence at work and its costs\n"
                                     "Doe, J. (2019). Employee voice in teams. Journal of Management, 45, 1-20.\n", "")
    assert rows[0]["doi"] == "10.1000/aaa" and not rows[0]["title"]
    assert rows[1]["title"] == "Silence at work and its costs" and rows[1]["doi"] is None
    assert rows[2]["title"] == "Employee voice in teams" and rows[2]["year"] == 2019
    ris_rows, _ = reviews.rows_from_text(RIS, "x.ris")
    assert [r["title"] for r in ris_rows] == ["Employee voice in teams", "Issue selling upward"]


def test_rows_from_file_formats(tmp_path):
    (tmp_path / "a.csv").write_text(CSV, encoding="utf-8")
    kind, rows, _ = reviews.rows_from_file(tmp_path / "a.csv")
    assert kind == "list" and len(rows) == 3 and rows[0]["doi"] == "10.1000/aaa"
    (tmp_path / "b.bib").write_text("@article{k1,\n title={Employee voice in teams},\n doi={10.1000/aaa},\n year={2019}\n}\n", encoding="utf-8")
    kind, rows, _ = reviews.rows_from_file(tmp_path / "b.bib")
    assert kind == "list" and rows[0]["title"] == "Employee voice in teams"
    (tmp_path / "c.txt").write_text("10.1000/aaa\nSome title here\n", encoding="utf-8")
    kind, rows, _ = reviews.rows_from_file(tmp_path / "c.txt")
    assert kind == "text" and len(rows) == 2
    with pytest.raises(ValueError):
        reviews.rows_from_file(tmp_path / "d.docx")


# ----------------------------------------------------------------- storage
def test_add_list_remove_and_gitignore(project, tmp_path):
    src = tmp_path / "prior.csv"
    src.write_text(CSV, encoding="utf-8")
    rv = reviews.add_file(project, src, label="Review A")
    assert rv["n_papers"] == 3 and rv["kind"] == "list" and rv["label"] == "Review A"
    copied = project.path("prior_reviews", rv["file_name"])
    assert copied.exists() and (project.path("prior_reviews", ".gitignore")).read_text().startswith("*")
    with pytest.raises(ValueError, match="already added"):
        reviews.add_file(project, src, label="again")
    rv2 = reviews.add_text(project, "10.1000/aaa\n10.1000/AAA\nEmployee voice in teams\nEmployee Voice in Teams.\nNew title only\n", "pasted")
    assert rv2["n_papers"] == 3, "dedupe by DOI then by normalised title within a review"
    lst = reviews.list_reviews(project)
    assert [r["label"] for r in lst] == ["Review A", "pasted"] and lst[0]["n_doi"] == 3
    s = reviews.summary(project)
    assert s["n_reviews"] == 2 and s["n_papers"] == 6 and s["policy"]["allow_openalex_lookup"] is False
    assert len(reviews.papers(project, rv["review_id"])) == 3
    reviews.remove(project, rv["review_id"])
    assert not copied.exists() and len(reviews.list_reviews(project)) == 1
    assert project.store.scalar("SELECT COUNT(*) FROM prior_review_papers") == 3
    with pytest.raises(KeyError):
        reviews.remove(project, 999)


def test_policy_round_trip(project):
    pol = reviews.set_policy(project, allow_openalex_lookup=True, none_known=None, max_citing_per_paper=10)
    assert pol["allow_openalex_lookup"] is True and pol["max_citing_per_paper"] == 10 and pol["allow_llm_excerpts"] is False
    assert Project.load(project.root).config.prior_reviews.allow_openalex_lookup is True
    with pytest.raises(ValueError):
        reviews.set_policy(project, max_citing_per_paper=0)


# ----------------------------------------------------------------- coverage (local)
def test_coverage_found_and_missed(project):
    with pytest.raises(ValueError):
        coverage.check(project)
    _import_csv(project)
    reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\nA missing paper on upward communication\n", "Prior A")
    cov = coverage.check(project, "platforms")
    assert cov["scopes"] == ["platform:scopus"] and cov["n_papers"] == 3 and cov["found"] == 2
    r = cov["reviews"][0]
    assert r["found"] == 2 and r["n"] == 3 and [m["title"] for m in r["missed"]] == ["A missing paper on upward communication"]
    assert project.store.scalar("SELECT COUNT(*) FROM prior_review_matches") == 3
    with pytest.raises(ValueError):
        coverage.check(project, "corpus")
    with pytest.raises(ValueError):
        coverage.check(project, "nope")
    dedup_run(project)
    assert coverage.check(project, "corpus")["reviews"][0]["found"] == 2


# ----------------------------------------------------------------- the policy gate and chaining (fake client)
class _Resp:
    def __init__(self, d, code=200):
        self._d, self.status_code = d, code

    def json(self):
        return self._d


class FakeClient:
    """Answers DOI batches, title searches and cited_by/cites filters; records every call."""

    def __init__(self):
        self.calls: list[dict] = []

    def get(self, url, params=None):
        self.calls.append(dict(params or {}))
        flt = (params or {}).get("filter", "")
        if flt.startswith("doi:"):
            return _Resp({"results": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/aaa",
                                       "title": "Employee voice in teams", "publication_year": 2019}]})
        if "search" in (params or {}):
            if "silence" in params["search"].lower():
                return _Resp({"results": [{"id": "https://openalex.org/W2", "title": "Silence at work and its costs", "publication_year": 2020}]})
            return _Resp({"results": [{"id": "https://openalex.org/W9", "title": "Totally different thing", "publication_year": 1999}]})
        if flt.startswith(("cited_by:", "cites:")):
            wid = flt.split(":")[1]
            return _Resp({"meta": {"next_cursor": None}, "results": [
                {"id": f"https://openalex.org/{wid}00", "doi": f"https://doi.org/10.1000/{wid.lower()}00", "title": f"Ref of {wid}",
                 "publication_year": 2015, "authorships": [{"author": {"display_name": "Ann Author"}}],
                 "primary_location": {"source": {"display_name": "J Ref"}}, "abstract_inverted_index": {"Hello": [0], "world": [1]}},
                {"id": "https://openalex.org/Wshared", "doi": "https://doi.org/10.1000/zzz", "title": "Unrelated weather study",
                 "publication_year": 2018, "authorships": [], "primary_location": None, "abstract_inverted_index": None}]})
        return _Resp({}, 404)

    def close(self):
        pass


@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network client created while the lookup switch is off")
    monkeypatch.setattr("wider.enrich.sources._client", boom)
    monkeypatch.setattr("wider.prior.chain.time.sleep", lambda *_: None)


def test_gate_blocks_before_any_network(project, no_network):
    reviews.add_text(project, "10.1000/aaa\n", "Prior A")
    for fn in (chain.resolve, chain.chain, chain.chain_and_export):
        with pytest.raises(PolicyError):
            fn(project)


def test_resolve_chain_export_import_dedup(project, no_network, monkeypatch):
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    _import_csv(project)
    reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\nA missing paper on upward communication\n", "Prior A")
    reviews.set_policy(project, allow_openalex_lookup=True)
    project = Project.load(project.root)
    fc = FakeClient()
    res = chain.resolve(project, client=fc)
    assert res == {"attempted": 3, "resolved": 2, "unresolved": 1, "total": 3, "warnings": []}
    assert fc.calls[0]["filter"].startswith("doi:10.1000/aaa") and "mailto" not in fc.calls[0]
    assert sorted(r["openalex_id"] for r in project.store.query("SELECT openalex_id FROM prior_review_papers WHERE openalex_id IS NOT NULL")) == ["W1", "W2"]
    assert chain.resolve(project, client=fc)["attempted"] == 1, "resolved papers are not looked up again"
    out = chain.chain_and_export(project, client=fc)
    assert out["papers_chained"] == 2 and out["n_backward"] == 4 and out["n_forward"] == 4 and out["n_exported"] == 3
    assert out["file"].startswith("inputs/citation_chain/chain_") and out["stage_result"]["next_stage"] == "import"
    assert any("not resolved" in w for w in out["warnings"])
    text = project.path(out["file"]).read_text(encoding="utf-8")
    assert text.splitlines()[0] == "Title,Abstract,DOI,Year,Authors,Journal,ID,Chain Source,Chain Direction"
    assert "Prior A#1; Prior A#2" in text and "backward+forward" in text
    assert chain.export_chain(project) == {"file": None, "n_exported": 0}
    assert chain.chain(project, client=fc)["papers_chained"] == 0, "chained papers are not fetched again"
    imported = {(r["platform"], r["status"], r["n"]) for r in import_all(project)}
    assert ("citation_chain", "imported", 3) in imported
    cov = coverage.check(project, "platforms")
    assert set(cov["scopes"]) == {"platform:scopus", "platform:citation_chain"}
    dedup_run(project)
    assert project.store.scalar("SELECT COUNT(*) FROM records") == 5, "the shared DOI merged into one record"


def test_export_skips_works_already_in_an_earlier_chain_file(project, no_network):
    reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\nA missing paper on upward communication\n", "Prior A")
    reviews.set_policy(project, allow_openalex_lookup=True)
    project = Project.load(project.root)
    chain.resolve(project, client=FakeClient())
    first = chain.chain_and_export(project, client=FakeClient())
    assert first["n_exported"] == 3
    # a later chaining run reaches one new work and one work already exported (via another prior-review paper)
    project.store.executemany("INSERT INTO citation_chain(paper_id, direction, openalex_id, title, fetched_at) VALUES (3,'backward',?,?,'2026')",
                              [("Wshared", "Unrelated weather study"), ("Wnew", "A new neighbour")])
    out = chain.export_chain(project)
    assert out["n_exported"] == 1
    text = project.path(out["file"]).read_text(encoding="utf-8")
    assert "Wnew" in text and "Wshared" not in text
    assert project.store.scalar("SELECT COUNT(*) FROM citation_chain WHERE exported_at IS NULL") == 0, "the skipped row is marked too"
    project.store.execute("INSERT INTO citation_chain(paper_id, direction, openalex_id, fetched_at) VALUES (3,'forward','Wshared','2026')")
    assert chain.export_chain(project) == {"file": None, "n_exported": 0}
    assert project.store.scalar("SELECT COUNT(*) FROM citation_chain WHERE exported_at IS NULL") == 0


def test_resolve_limit_text_duplicate_and_fuzzy_coverage(project, no_network, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app

    runner, d = CliRunner(), ["--dir", str(project.root)]
    reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\nA missing paper on upward communication\n", "Prior A")
    with pytest.raises(ValueError, match=r"^this text was already added as review #1 \(Prior A\)$"):
        reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\nA missing paper on upward communication\n", "again")
    reviews.set_policy(project, allow_openalex_lookup=True)
    monkeypatch.setattr("wider.enrich.sources._client", FakeClient)  # lookup_client creates its client there
    r = runner.invoke(app, ["prior-reviews", "resolve", "--limit", "1", *d])
    assert r.exit_code == 0, r.output
    assert "1 resolved, 2 unresolved of 3 papers" in r.output
    assert project.store.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE resolved_at IS NULL") == 2
    c = TestClient(create_app(project))
    assert c.post("/api/prior-reviews/resolve?limit=1").json()["attempted"] == 1
    assert project.store.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE resolved_at IS NULL") == 1
    _import_csv(project)
    r = runner.invoke(app, ["prior-reviews", "coverage", *d])
    assert r.exit_code == 0 and "to confirm" not in r.output, "exact matches only: no fuzzy line"
    reviews.add_text(project, "Silence at work and its cost\n", "Prior B")
    r = runner.invoke(app, ["prior-reviews", "coverage", *d])
    assert r.exit_code == 0 and "1 fuzzy match(es) to confirm" in r.output


def test_forward_cap_warns(project, no_network):
    reviews.add_text(project, "10.1000/aaa\n", "Prior A")
    reviews.set_policy(project, allow_openalex_lookup=True, max_citing_per_paper=1)
    project = Project.load(project.root)
    out = chain.chain(project, direction="forward", client=FakeClient())
    assert out["n_forward"] == 0, "unresolved papers are skipped"
    chain.resolve(project, client=FakeClient())
    out = chain.chain(project, direction="forward", client=FakeClient())
    assert out["n_forward"] == 1 and not any("cap" in w for w in out["warnings"]), "no next cursor: nothing was cut"
    with pytest.raises(ValueError):
        chain.chain(project, direction="sideways", client=FakeClient())


# ----------------------------------------------------------------- excerpt switch
def test_prompt_titles_only_with_excerpt_switch(project):
    seed.add_rows(project, [{"title": "Employee voice in teams", "doi": "10.1000/aaa", "year": 2019}], "Development")
    reviews.add_text(project, "Silence at work and its costs\nIssue selling upward\n", "Prior A")
    prompt, ctx = build_prompt(project, "cells")
    assert ctx["prior_review_titles_n"] == 0 and "Titles of studies included in prior reviews" not in prompt
    reviews.set_policy(project, allow_llm_excerpts=True)
    prompt, ctx = build_prompt(Project.load(project.root), "cells")
    assert ctx["prior_review_titles_n"] == 2 and "Issue selling upward" in prompt


# ----------------------------------------------------------------- routes
def test_prior_review_routes(project, no_network, tmp_path):
    c = TestClient(create_app(project))
    assert 'data-task="prior"' in c.get("/").text
    js = c.get("/static/setup.js").text
    assert "never uploaded or sent to any model or service" in js
    assert 'id="prior-allow-lookup"' in js and 'id="prior-allow-excerpts"' in js
    # Setup's "Chain citations" asks for the direction, as on the Import page
    assert 'id="prior-setup-chain-dir"' in js and "/api/prior-reviews/chain?direction=" in js
    d = c.get("/api/prior-reviews").json()
    assert d["n_reviews"] == 0 and d["policy"]["allow_openalex_lookup"] is False
    r = c.post("/api/prior-reviews/text", json={"text": "10.1000/aaa\nSilence at work and its costs\n", "label": "Prior A"})
    assert r.status_code == 200 and r.json()["n_papers"] == 2
    rid = r.json()["review_id"]
    r = c.post("/api/prior-reviews/upload", files={"file": ("prior.csv", CSV.encode(), "text/csv")}, data={"label": "Upload"})
    assert r.status_code == 200 and r.json()["n_papers"] == 3
    assert c.post("/api/prior-reviews/upload", files={"file": ("x.docx", b"zz", "application/octet-stream")}, data={"label": "bad"}).status_code == 400
    assert c.post("/api/prior-reviews/upload", files={"file": ("prior2.csv", CSV.encode(), "text/csv")}, data={"label": "dup"}).status_code == 400
    assert len(c.get(f"/api/prior-reviews/{rid}/papers").json()["papers"]) == 2
    assert c.get("/api/prior-reviews/999/papers").status_code == 404
    assert c.post("/api/prior-reviews/resolve").status_code == 403
    assert c.post("/api/prior-reviews/chain").status_code == 403
    assert c.get("/api/prior-reviews/coverage").status_code == 400, "nothing imported yet"
    _import_csv(project)
    cov = c.get("/api/prior-reviews/coverage?scope=platforms").json()
    assert cov["found"] == 2 + 3
    r = c.patch("/api/project", json={"prior_reviews": {"allow_llm_excerpts": True, "none_known": True}})
    assert r.status_code == 200
    pol = c.get("/api/project").json()["prior_reviews"]["policy"]
    assert pol["allow_llm_excerpts"] is True and pol["none_known"] is True and pol["allow_openalex_lookup"] is False
    assert c.patch("/api/project", json={"prior_reviews": {"max_citing_per_paper": 0}}).status_code == 400
    assert c.post("/api/prior-reviews/policy", json={"allow_openalex_lookup": True}).json()["allow_openalex_lookup"] is True
    assert c.delete(f"/api/prior-reviews/{rid}").status_code == 200
    assert c.get("/api/prior-reviews").json()["n_reviews"] == 1


def test_lookup_adds_the_contact_address_only_when_set(project, no_network, monkeypatch):
    reviews.add_text(project, "10.1000/aaa\n", "Prior A")
    reviews.set_policy(project, allow_openalex_lookup=True)
    project = Project.load(project.root)
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    fc = FakeClient()
    chain.resolve(project, client=fc)
    assert fc.calls and all("mailto" not in c for c in fc.calls)
    monkeypatch.setenv("WIDER_MAILTO", "someone@example.org")
    project.store.execute("UPDATE prior_review_papers SET openalex_id = NULL, resolved_at = NULL")
    fc = FakeClient()
    chain.resolve(project, client=fc)
    assert fc.calls and all(c.get("mailto") == "someone@example.org" for c in fc.calls)


def _chain_filters(fc):
    return [c["filter"].split(":")[0] for c in fc.calls if c.get("filter", "").startswith(("cited_by:", "cites:"))]


def test_setup_chain_direction_references_only(project, no_network, monkeypatch):
    """Scenario "References only from setup": the Setup step sends ?direction=backward; the default is both."""
    from typer.testing import CliRunner

    from wider.cli import app

    reviews.add_text(project, "10.1000/aaa\nSilence at work and its costs\n", "Prior A")
    reviews.set_policy(project, allow_openalex_lookup=True)
    fc = FakeClient()
    monkeypatch.setattr("wider.enrich.sources._client", lambda: fc)   # lookup_client creates its client there
    c = TestClient(create_app(project))
    assert c.post("/api/prior-reviews/resolve").json()["resolved"] == 2
    fc.calls.clear()
    d = c.post("/api/prior-reviews/chain?direction=backward").json()
    # only backward chaining ran (references: cited_by), no citing works were fetched (cites)
    assert _chain_filters(fc) == ["cited_by", "cited_by"]
    assert d["n_backward"] == 4 and d["n_forward"] == 0 and d["papers_chained"] == 2
    assert {r["direction"] for r in project.store.query("SELECT direction FROM citation_chain")} == {"backward"}
    assert project.store.scalar("SELECT COUNT(*) FROM prior_review_papers WHERE chained_forward_at IS NOT NULL") == 0
    assert "backward+forward" not in project.path(d["file"]).read_text(encoding="utf-8")
    # the select's default is "references and citing works" (both), and the route defaults to both too
    js = c.get("/static/setup.js").text
    sel = js[js.index('<select id="prior-setup-chain-dir"'):]
    sel = sel[:sel.index("</select>")]
    options = re.findall(r'<option value="(\w+)"([^>]*)>([^<]+)</option>', sel)
    assert options[0][0] == "both" and options[0][2] == "references and citing works"
    assert not any("selected" in attrs for _, attrs, _ in options)
    fc.calls.clear()
    d = c.post("/api/prior-reviews/chain").json()   # no direction sent: the forward half that is still missing runs
    assert _chain_filters(fc) == ["cites", "cites"] and d["n_forward"] == 4 and d["n_backward"] == 0
    # the CLI twin: --direction backward fetches no citing works, and its default is both
    p2 = Project.init(project.root.parent / "r2", platforms=["scopus"])
    reviews.add_text(p2, "10.1000/aaa\n", "Prior A")
    reviews.set_policy(p2, allow_openalex_lookup=True)
    runner, d2 = CliRunner(), ["--dir", str(p2.root)]
    assert runner.invoke(app, ["prior-reviews", "resolve", *d2]).exit_code == 0
    fc.calls.clear()
    r = runner.invoke(app, ["prior-reviews", "chain", "--direction", "backward", *d2])
    assert r.exit_code == 0, r.output
    assert _chain_filters(fc) == ["cited_by"]
    reviews.add_text(p2, "Silence at work and its costs\n", "Prior B")   # a new paper, chained in neither direction
    assert runner.invoke(app, ["prior-reviews", "resolve", *d2]).exit_code == 0
    fc.calls.clear()
    assert runner.invoke(app, ["prior-reviews", "chain", *d2]).exit_code == 0
    assert _chain_filters(fc) == ["cited_by", "cites", "cites"], "the default runs both directions"
