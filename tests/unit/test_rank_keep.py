"""ui-redesign D5: keep a ranking that needs review (`wider rank keep`, POST /api/rank/keep). A signed, undoable History
change of kind `ranking_kept`; it clears "needs review" until the inputs change again and is named in the methods
draft. Nothing is rerun."""
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_rank_versions import add_abstracts
from tests.unit.test_screen import project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.core.stages import stage_states
from wider.ui.app import create_app

runner = CliRunner()


def _review(p):
    return next(s for s in stage_states(p) if s["key"] == "rank")["needs_review"]


def test_keep_and_undo(project):  # noqa: F811
    """Scenario "Keep and undo" and "A kept ranking by kind": after `wider rank keep` as ana, status shows no
    "needs review", History lists the change of kind ranking_kept signed by ana (in the Show list too), the page's
    route agrees; undo brings the mark back, and a later change to the records marks Ranking again."""
    p = project
    add_abstracts(p)
    assert _review(p)
    n = p.store.scalar("SELECT COUNT(*) FROM rankings")
    p.close()
    r = runner.invoke(cli_app, ["rank", "keep", "--by", "ana", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli_app, ["status", "--dir", str(p.root)])
    assert "needs review" not in r.output
    r = runner.invoke(cli_app, ["history", "list", "--kind", "ranking_kept", "--dir", str(p.root)])
    assert r.exit_code == 0 and "Kept the ranking after the records changed" in r.output and "ana" in r.output
    p = Project.load(p.root)
    assert {"kind": "ranking_kept", "label": "ranking kept"} in journal.kinds()
    ch = journal.changes(p, kind="ranking_kept")
    assert len(ch) == 1 and ch[0]["author"] == "ana" and ch[0]["undoable"]
    c = TestClient(create_app(p))
    stages = {s["key"]: s for s in c.get("/api/stages").json()["stages"]}
    assert stages["rank"]["needs_review"] is None   # the page reads the same state
    assert p.store.scalar("SELECT COUNT(*) FROM rankings") == n   # nothing reran
    journal.undo_last(p, "ana")
    assert _review(p) == "the records or their abstracts changed since this ranking"
    j = c.post("/api/rank/keep", json={}).json()   # the page's button keeps it again
    assert j["version"] == 1 and j["change_id"]
    assert _review(p) is None
    add_abstracts(p, 1)   # a later change to the records marks it again
    assert _review(p) == "the records or their abstracts changed since this ranking"
    p.close()


def test_keep_one_of_two(project):  # noqa: F811
    """Scenario "Keeping one of two versions": with versions 1 and 2 both needing review, `wider rank keep` exits 1
    naming both and their samples; `--version 1` keeps version 1 only, as the page's button on that version does."""
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank
    from wider.sample.design import draw_probability

    p = project
    add_abstracts(p, 2)
    embed_corpus(p)
    rank(p, top_n=1)
    p.store.execute("DELETE FROM sample_rows WHERE sample_id=(SELECT sample_id FROM samples WHERE name='validation')")
    p.store.execute("DELETE FROM samples WHERE name='validation'")
    draw_probability(p)
    add_abstracts(p, 1)
    p.close()
    r = runner.invoke(cli_app, ["rank", "keep", "--dir", str(p.root)])
    out = " ".join(r.output.split())
    assert r.exit_code == 1 and "version 1 (development)" in out and "version 2 (validation)" in out
    p = Project.load(p.root)
    assert not journal.changes(p, kind="ranking_kept")
    p.close()
    r = runner.invoke(cli_app, ["rank", "keep", "--version", "1", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    p = Project.load(p.root)
    items = next(s for s in stage_states(p) if s["key"] == "rank")["review"]
    assert [i["version"] for i in items] == [2]
    c = TestClient(create_app(p))
    assert c.post("/api/rank/keep", json={"version": 1}).status_code == 409   # version 1 no longer needs review
    assert c.post("/api/rank/keep", json={"version": 2}).status_code == 200
    assert _review(p) is None
    p.close()


def test_nothing_to_keep(project):  # noqa: F811
    """Scenario "Nothing to keep": exit 1 with "the ranking does not need review", nothing recorded."""
    p = project
    p.close()
    r = runner.invoke(cli_app, ["rank", "keep", "--dir", str(p.root)])
    assert r.exit_code == 1 and "the ranking does not need review" in r.output
    p = Project.load(p.root)
    assert not journal.changes(p, kind="ranking_kept")
    assert TestClient(create_app(p)).post("/api/rank/keep", json={}).status_code == 409
    p.close()


def test_methods_sentence(project):  # noqa: F811
    """Scenario "A kept ranking in the methods": the draft names the keep with its date and who kept it, written by
    the attribution setting; without a keep, no such sentence."""
    from wider.core.researchers import set_attribution
    from wider.rank.versions import keep
    from wider.recall.outputs import methods_draft

    p = project
    assert "The ranking was kept" not in methods_draft(p)
    add_abstracts(p)
    keep(p, author="ana")
    day = datetime.now(UTC)   # the History time is UTC, so the sentence gives the UTC day
    text = methods_draft(p)
    assert "The ranking was kept after the records or their abstracts changed (kept on " in text
    from wider.core.researchers import pseudonym
    assert f"by {pseudonym(p, 'ana')})" in text and "ana" not in text   # the default setting writes pseudonyms
    set_attribution(p, "names")
    assert "by ana)" in methods_draft(p)
    assert f"(kept on {day.day} {day:%b %Y} by " in text
    journal.undo_last(p, "ana")
    assert "The ranking was kept" not in methods_draft(p)


def test_page_and_cli_show_versions_and_review(project):  # noqa: F811
    """Scenarios "A re-rank adds a version" (the note before the run, "2 versions") and "A labelled development sample
    offers Keep only": GET /api/rank gives each sample of a version that needs review with its kind and whether it
    has labels, the note before a re-rank and the version history; `wider rank show` prints the same."""
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = project
    add_abstracts(p)
    c = TestClient(create_app(p))
    d = c.get("/api/rank").json()
    assert d["keep_before"] == "Samples keep the version they were drawn from: validation and development keep version 1."
    (item,) = d["review"]
    assert item["version"] == 1 and item["reason"] == "the records or their abstracts changed since this ranking"
    by = {s["name"]: s for s in item["samples"]}
    assert by["development"] == {"name": "development", "kind": "purposive", "labelled": True}
    assert by["validation"] == {"name": "validation", "kind": "probability", "labelled": False}
    assert len(d["versions"]) == 1 and d["bases"] == [d["ranking"]["base_name"]]
    r = runner.invoke(cli_app, ["rank", "show", "--limit", "1", "--dir", str(p.root)], env={"COLUMNS": "300"})
    out = " ".join(r.output.split())
    assert r.exit_code == 0, r.output
    assert "needs review: version 1 (validation, development): the records or their abstracts changed" in out
    assert "versions" not in out.split("needs review")[0]   # one version: no history line
    assert d["keep_before"] in out
    embed_corpus(p)
    rank(p, top_n=1)
    d = c.get("/api/rank").json()
    assert [v["version"] for v in d["versions"]] == [1, 2] and d["bases"] == [d["ranking"]["base_name"]]
    r = runner.invoke(cli_app, ["rank", "show", "--limit", "1", "--dir", str(p.root)], env={"COLUMNS": "300"})
    out = " ".join(r.output.split())
    assert "2 versions: version 1 (validation, development); version 2 (newest)" in out
    p.close()
