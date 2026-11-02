"""Whole-app inspection of 1 Nov 2026, search and query part: multi-word terms are quoted in every search string,
the syntax check warns at freeze time without blocking, a hand-written outline when a proposal fails, the screening
step that names a missing codebook, imports with no recorded synonyms version, the project default for query
creation, the import check's rounding and labels, the per-format export caps, and Missing abstracts with nothing to
fetch. Synthetic projects only."""
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core.project import Project
from wider.screen.progress import screen_progress
from wider.search import diagnostics, rounds
from wider.search.ir import Query, as_phrase, needs_quotes
from wider.search.lint import lint
from wider.search.render.boolean import render_ebsco, render_scopus
from wider.ui.app import create_app

Q = {"blocks": [{"key": "a", "name": "Meetings", "terms": ["team huddle", '"stand up"', "meeting*"]}], "logic": "a"}


def test_multi_word_terms_are_quoted_everywhere():
    """An unquoted multi-word term is searched as one phrase in every renderer, and the check says so as a warning."""
    assert needs_quotes("team huddle") and not needs_quotes('"team huddle"') and not needs_quotes("huddle")
    assert as_phrase("team huddle") and as_phrase('"stand up"') and not as_phrase("meeting*")
    q = Query.model_validate(Q)
    for out in (render_scopus(q, pretty=False), render_ebsco(q, pretty=False)):
        assert '"team huddle"' in out and '"stand up"' in out, out
    found = [f for f in lint(q) if f["term"] == "team huddle"]
    assert [f["level"] for f in found] == ["warning"] and "quotes it as one phrase" in found[0]["message"]


def _proj(tmp_path, terms=("meeting*",)):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": list(terms)}],
                                               "logic": "a"}, origin="manual")
    return Project.load(p.root)


def test_freeze_warns_about_syntax_errors_and_freezes(tmp_path):
    """Before the freeze the notice names the errors and says it can go ahead; the freeze goes ahead and says so."""
    p = _proj(tmp_path, terms=("me*", "huddle"))
    before = artifacts.freeze_notice(p, "synonyms", 1)
    assert before.startswith("Warning: the syntax check finds 1 error in v1") and "freeze anyway" in before
    row = artifacts.set_status(p, "synonyms", 1, "frozen")
    assert row["status"] == "frozen" and "v1 was frozen with it" in row["notice"]
    clean = _proj(tmp_path / "c")
    assert artifacts.freeze_notice(clean, "synonyms", 1) is None
    assert artifacts.set_status(clean, "synonyms", 1, "frozen")["notice"] is None


def test_outline_on_the_page_and_the_command_line(tmp_path):
    """After a failed proposal the researcher gets a text to fill in by hand; the CLI prints the same text."""
    p = _proj(tmp_path)
    text = artifacts.outline("codebook")
    assert text.startswith("# codebook: fill in every empty value")
    c = TestClient(create_app(p))
    assert c.get("/api/construct/codebook/outline").json() == {"text": text}
    assert c.get("/api/construct/nonsense/outline").status_code == 404
    r = CliRunner().invoke(cli_app, ["construct", "outline", "codebook"])
    assert r.exit_code == 0 and r.output == text
    assert CliRunner().invoke(cli_app, ["construct", "outline", "nonsense"]).exit_code == 1


def test_screening_step_names_the_missing_codebook(tmp_path):
    p = _proj(tmp_path)
    step = next(s for s in screen_progress(p)["steps"] if s["key"] == "prompt")
    assert step["state"] == "not_started" and "There is no codebook yet" in step["text"]


def test_imports_without_a_version_are_explained(tmp_path):
    """Files imported before any synonyms version existed carry no version: the diagnosis, the budget and the rounds
    table say so instead of claiming nothing was imported."""
    from tests.unit.test_diagnostics import _export
    from wider.ingest.importer import add_file, import_all

    q = Project.init(tmp_path / "r", platforms=["scopus"])
    add_file(q, "scopus", _export(tmp_path / "x.csv", [("A made-up title", "An invented abstract.", "10.1/x")]))
    import_all(q)
    artifacts.save_new_version(q, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["meeting*"]}],
                                               "logic": "a"}, origin="manual")
    p = Project.load(q.root)
    assert diagnostics.unrecorded_imports(p) == 1
    try:
        diagnostics.what_did_what(p)
        raise AssertionError("expected a DiagnoseError")
    except diagnostics.DiagnoseError as e:
        assert "imported before any synonyms version existed" in str(e)
    assert "1 file imported with no version" in diagnostics.budget(p, 1)["text"]
    data = rounds.rounds(p)
    assert data["no_export"].startswith("no export recorded under this version (1 file")


def test_stages_name_the_project_default(tmp_path):
    p = _proj(tmp_path)
    d = TestClient(create_app(p)).get("/api/stages").json()
    assert d["construct_backend"] is None
    want = p.config.default_provider if any(x.name == p.config.default_provider for x in p.config.providers) else None
    assert d["construct_default"] == want


def test_import_check_rounds_half_up_and_names_one_low_check():
    """20.5% shows as 21% (not 20%), and an ok file with one check under its threshold reads "ok, one check low"."""
    from wider.ingest.sanity import THRESHOLD_RULE, pct, verdict_label
    assert pct(0.205) == 21 and pct(0.2) == 20 and pct(0.994) == 99
    assert verdict_label({"verdict": "ok", "low": True}) == "ok, one check low"
    assert verdict_label({"verdict": "ok", "low": False}) == "ok"
    assert verdict_label({"verdict": "off_topic"}) == "off topic"
    assert "misses both" in THRESHOLD_RULE


def test_export_caps_name_every_format():
    """Scopus-like caps show both numbers, the tightest first; equal caps show one number."""
    from wider.search.catalogue import cap_text
    assert cap_text({"ris": 20000, "csv": 2000, "bib": 20000}) == "2,000 per CSV export; 20,000 per RIS or BIB export"
    assert cap_text({"ris": 1000, "csv": 1000}) == "1,000"
    assert cap_text({}) == ""


def test_missing_abstracts_with_nothing_to_fetch_is_completed(tmp_path):
    """Every record has a full abstract: Missing abstracts reads completed (never "not started, recommended next")
    and its box says nothing was looked up because there is nothing to fetch."""
    from wider.core.stages import next_stage, stage_states
    from wider.dedup.engine import run
    from wider.guide import can_do
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    words = " ".join(["word"] * 40)
    (tmp_path / "a.ris").write_text(f"TY  - JOUR\nTI  - A full record\nAB  - {words}\nER  -\n")
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    run(p)
    states = stage_states(p)
    by = {s["key"]: s for s in states}
    assert by["enrich"]["state"] == "done"
    assert (next_stage(p, states) or {}).get("key") != "enrich"
    text = can_do(p, "enrich", states, next_stage(p, states))
    assert text.startswith("Completed") and "nothing to fetch" in text, text
