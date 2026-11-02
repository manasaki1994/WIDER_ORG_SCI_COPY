"""Guided workflow: stage states, `wider status` / `wider next`, and CLI help coverage."""
import click
from typer.main import get_command
from typer.testing import CliRunner

from tests.unit.test_screen import project as screened  # noqa: F401 - pytest fixture
from wider.cli import app
from wider.construct.benchmark import sync
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.core.stages import next_stage, stage_states

runner = CliRunner()


def _states(project):
    try:
        return {s["key"]: s for s in stage_states(project)}, next_stage(project)
    finally:
        project.store.close()


def test_fresh_project_setup_ready_rest_blocked(tmp_path):
    """Scenario "Fresh project": setup is ready, every other available stage blocked, and the recommended step is the
    Model for query creation page because no construct model is set (ui-redesign D8)."""
    p = Project.init(tmp_path / "r", platforms=["ebsco"])
    st, nxt = _states(p)
    assert st["setup"]["state"] == "ready"
    assert nxt["key"] == "model" and nxt["command"] == "providers" and nxt["title"] == "Model for query creation"
    assert st["construct"]["state"] == "blocked" and "setup" in st["construct"]["missing"]
    core = [k for k, s in st.items() if s["state"] != "extension"]
    assert all(st[k]["state"] == "blocked" for k in core if k != "setup")
    assert {k for k, s in st.items() if s["state"] == "extension"} >= {"fulltext", "extract", "synthesis"}


def test_setup_done_after_question_and_seed_paper(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["ebsco"], questions=["What leads employees to speak up?"])
    p.config.stage_providers["construct"] = "fake"   # a construct model is set
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    sync(p)
    st, nxt = _states(p)
    assert st["setup"]["state"] == "done"
    assert st["construct"]["state"] == "ready" and nxt["key"] == "construct"
    assert st["query"]["state"] == "blocked"


def test_next_and_status_commands(tmp_path):
    """Scenario "Next after setup": `wider providers` first (no construct model), then `wider ui`, then after a seed
    paper `wider construct`, which `wider status` names too."""
    r = runner.invoke(app, ["init", str(tmp_path / "rev"), "-q", "Q1?", "-p", "ebsco"])
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(app, ["next", "--dir", str(tmp_path / "rev")])
    assert r.exit_code == 0 and r.stdout.strip() == "wider providers", r.stdout
    r = runner.invoke(app, ["next", "--dir", str(tmp_path / "rev"), "--why"])
    assert "Model for query creation" in r.stdout
    p = Project.load(tmp_path / "rev")
    p.config.stage_providers["construct"] = "fake"
    p.save()
    p.store.close()
    r = runner.invoke(app, ["next", "--dir", str(tmp_path / "rev")])
    assert r.exit_code == 0 and r.stdout.strip() == "wider ui", r.stdout
    p = Project.load(tmp_path / "rev")
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    sync(p)
    p.store.close()
    r = runner.invoke(app, ["next", "--dir", str(tmp_path / "rev"), "--why"])
    assert r.exit_code == 0 and "wider construct" in r.stdout, r.stdout
    r = runner.invoke(app, ["status", "--dir", str(tmp_path / "rev")])
    assert r.exit_code == 0 and "next:" in r.stdout and "construct" in r.stdout, r.stdout


def test_every_cli_option_has_help():
    missing = []

    def walk(cmd, path):
        for prm in cmd.params:
            if isinstance(prm, click.Option) and not prm.help:
                missing.append(f"{path} {prm.opts}")
        if isinstance(cmd, click.Group):
            for name, sub in cmd.commands.items():
                walk(sub, f"{path} {name}")

    walk(get_command(app), "wider")
    assert not missing, missing


def test_registry_refuses_unknown_section(tmp_path):
    """An available stage must name a section of `sections:`; an extension stage takes none."""
    import pytest

    from wider.core.stages import load_sections, load_stages

    head = "sections:\n- {number: 1, title: First}\nstages:\n"
    ext = "- {key: later, title: Later, status: extension}\n"
    ok = tmp_path / "ok.yaml"
    ok.write_text(head + "- {key: one, title: One, status: available, section: 1}\n" + ext, encoding="utf-8")
    assert [s["key"] for s in load_stages(ok)] == ["one", "later"]
    assert load_sections(ok) == [{"number": 1, "title": "First"}]
    for name, line in (("missing", "- {key: nosec, title: No section, status: available}\n"),
                       ("unknown", "- {key: badsec, title: Bad section, status: available, section: 7}\n")):
        bad = tmp_path / f"{name}.yaml"
        bad.write_text(head + line + ext, encoding="utf-8")
        with pytest.raises(ValueError, match="nosec|badsec"):
            load_stages(bad)
    # the shipped registry: ten available stages under the four sections, the three extension stages under none
    stages = load_stages()
    assert [s["section"] for s in stages if s["status"] == "available"] == [1, 1, 1, 1, 2, 2, 3, 3, 4, 4]
    assert all("section" not in s for s in stages if s["status"] == "extension")
    assert [s["title"] for s in load_sections()] == ["Literature search", "Data cleaning",
                                                     "Model tuning and validation", "Screening and outputs"]


def test_dedup_done_but_needs_you_while_pairs_wait(tmp_path):
    """design-pass-a1-dedup D4, Q5: Deduplication with pairs waiting stays done and gets a "needs you" text."""
    from tests.unit.test_dedup import RIS_A2, RIS_B2, _project_with_imports
    from wider.dedup.engine import decide, queue_rows, run

    ris_a3 = "\nTY  - JOUR\nAU  - Third, Author\nTI  - A third loosely worded heading\nPY  - 2016\nT2  - Journal E\nER  -\n"
    ris_b3 = "\nTY  - JOUR\nAU  - Fourth, Author\nTI  - A third loosely worded heading\nPY  - 2016\nT2  - Journal F\nER  -\n"
    p = _project_with_imports(tmp_path, RIS_A2 + ris_a3, RIS_B2 + ris_b3)
    run(p)
    q = queue_rows(p)
    assert len(q) == 3
    st = {s["key"]: s for s in stage_states(p)}
    assert st["dedup"]["state"] == "done" and st["dedup"]["needs_you"] == "3 pairs to decide"
    after = [s for s in st.values() if "dedup" in s["requires"]]
    assert after and all(s["state"] != "blocked" for s in after)
    assert all(s["needs_you"] is None for k, s in st.items() if k != "dedup")
    res = runner.invoke(app, ["status", "--dir", str(p.root)], env={"COLUMNS": "250"})
    assert res.exit_code == 0 and "done, needs you: 3 pairs to decide" in " ".join(res.output.split())

    decide(p, q[0]["raw_a"], q[0]["raw_b"], "duplicate", by="tester")
    decide(p, q[1]["raw_a"], q[1]["raw_b"], "not_duplicate", by="tester")
    assert {s["key"]: s for s in stage_states(p)}["dedup"]["needs_you"] == "1 pair to decide"
    decide(p, q[2]["raw_a"], q[2]["raw_b"], "uncertain", by="tester")
    st = {s["key"]: s for s in stage_states(p)}
    assert st["dedup"]["state"] == "done" and st["dedup"]["needs_you"] is None
    res = runner.invoke(app, ["status", "--dir", str(p.root)], env={"COLUMNS": "250"})
    assert res.exit_code == 0 and "needs you" not in res.output
    p.store.close()


# ---------------------------------------------------------------------------------------- needs review (ui-redesign D5)
def _rank_state(p):
    return next(s for s in stage_states(p) if s["key"] == "rank")


def test_rank_needs_review_after_new_abstracts(screened):  # noqa: F811
    """Scenario "Ranking made before new abstracts": Ranking stays done with the needs-review words, nothing else
    changes state, and `wider status` prints the same line."""
    from tests.unit.test_rank_versions import add_abstracts

    p = screened
    before = {s["key"]: s["state"] for s in stage_states(p)}
    assert _rank_state(p)["needs_review"] is None
    add_abstracts(p)
    s = _rank_state(p)
    assert s["state"] == "done" and s["needs_review"] == "the records or their abstracts changed since this ranking"
    assert [i["version"] for i in s["review"]] == [1] and s["review"][0]["samples"] == ["validation", "development"]
    assert {x["key"]: x["state"] for x in stage_states(p)} == before
    n = p.store.scalar("SELECT COUNT(*) FROM rankings")
    p.close()
    r = runner.invoke(app, ["status", "--dir", str(p.root)])
    assert r.exit_code == 0
    assert "done, test model, needs review: the records or their abstracts changed since this ranking" in " ".join(r.output.split())
    q = Project.load(p.root)
    assert q.store.scalar("SELECT COUNT(*) FROM rankings") == n   # nothing reran
    q.close()


def test_stale_embeddings_keep_review(screened):  # noqa: F811
    """Scenario "Ranked from outdated embeddings": ranking again from the stored embeddings reuses the version (same
    input hash) and still needs review."""
    from tests.unit.test_rank_versions import add_abstracts
    from wider.rank.score import rank

    p = screened
    add_abstracts(p)
    r = rank(p, top_n=1)
    assert r["reused"] and r["version"] == 1
    assert _rank_state(p)["needs_review"] == "the records or their abstracts changed since this ranking"


def test_rank_not_marked_without_a_change(screened):  # noqa: F811
    """Scenario "Not marked without a real change": embedding again from the same texts, a concept draft, a failed
    lookup and a deduplication rerun leave it unmarked; new approved anchors, another embedding model,
    corpus.thin_words and corpus.max_words each mark it."""
    from wider.construct import artifacts
    from wider.core.models import EmbeddingConfig
    from wider.dedup.engine import run as dedup_run
    from wider.rank.corpus import embed_corpus

    p = screened
    embed_corpus(p)
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "a", "terms": ["speaking up"]}],
                                               "logic": {"and": ["a"]}}, origin="manual")
    p.store.execute("INSERT INTO enrichment_log(record_id,source,status,words,fetched_at) "
                    "VALUES(1,'crossref','failed',0,'2026-10-27T00:00:00+00:00')")
    dedup_run(p)
    assert _rank_state(p)["needs_review"] is None
    # each of these marks it, and undoing it clears the mark again
    _, data = artifacts.load(p, "anchors")
    row = artifacts.save_new_version(p, "anchors", {"anchors": {**data["anchors"], "other_cell": " ".join(["ocean plankton temperature bloom"] * 10)}},
                                     origin="manual")
    artifacts.set_status(p, "anchors", row["version"], "approved")
    assert "other anchors are approved (version 2)" in _rank_state(p)["needs_review"]
    artifacts.set_status(p, "anchors", row["version"], "draft")
    assert _rank_state(p)["needs_review"] is None
    p.config.embeddings.append(EmbeddingConfig(name="fake2", kind="fake", model="fake-other", dim=64))
    p.config.default_embedding = "fake2"
    p.save()
    assert "embedding model changed" in _rank_state(p)["needs_review"]
    p.config.default_embedding = "fake"
    p.save()
    assert _rank_state(p)["needs_review"] is None
    for key, value in (("corpus.thin_words", 3), ("corpus.max_words", 100)):
        old = p.config.protocol
        p.config.protocol = p.config.protocol.set(key, value)
        p.save()
        assert key in _rank_state(p)["needs_review"], key
        p.config.protocol = old
        p.save()
        assert _rank_state(p)["needs_review"] is None


def test_two_samples_two_versions(screened):  # noqa: F811
    """Scenario "Two samples on two ranking versions": the development sample on version 1 and the validation sample
    on version 2 both need review after the records change, each named with its sample."""
    from tests.unit.test_rank_versions import add_abstracts
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank
    from wider.sample.design import draw_probability

    p = screened
    add_abstracts(p, 2)
    embed_corpus(p)
    rank(p, top_n=1)
    p.store.execute("DELETE FROM sample_rows WHERE sample_id=(SELECT sample_id FROM samples WHERE name='validation')")
    p.store.execute("DELETE FROM samples WHERE name='validation'")
    draw_probability(p)
    assert [i["version"] for i in _rank_state(p)["review"]] == [1]   # version 2 matches the current texts
    add_abstracts(p, 1)
    s = _rank_state(p)
    assert [(i["version"], i["samples"]) for i in s["review"]] == [(1, ["development"]), (2, ["validation"])]
    assert s["needs_review"].startswith("version 1 (development): the records")
    assert "version 2 (validation): the records" in s["needs_review"]


# ------------------------------------------------------------------------- the recommended stage (ui-redesign D8, D14)
def test_next_model_and_open_dev_labels(screened):  # noqa: F811
    """Scenario "No construct model, and open development labels", its first half: without a construct model `wider
    next` prints `wider providers`, and /api/stages names the same step (the Overview's Continue reads it). The
    second half (`wider sample status` while the development sample is incomplete) is pinned in
    test_screen_dev_gate.py::test_next_stage_is_labelling_while_incomplete."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    p = Project.init(screened.root.parent / "fresh", platforms=["scopus"], questions=["What leads employees to speak up?"])
    assert next_stage(p)["key"] == "model"
    assert TestClient(create_app(p)).get("/api/stages").json()["next"] == "model"
    p.close()
    r = runner.invoke(app, ["next", "--dir", str(p.root)])
    assert r.stdout.strip() == "wider providers"


def test_next_skips_model_once_past_query_creation(screened):  # noqa: F811
    """Scenario "No construct model in a project past query creation" (audit F8, 29 Oct 2026): once a stage from
    Search results onward is done, a missing construct model no longer sends Continue back to the Model for query
    creation page; `wider next` and /api/stages name the first ready stage instead, and every stage keeps its state."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    p = screened
    p.config.stage_providers.pop("construct", None)
    p.save()
    states = stage_states(p)
    assert {s["key"]: s["state"] for s in states}["rank"] == "done"   # every stage keeps its own state
    nxt = next_stage(p)
    assert nxt is None or nxt["key"] != "model"
    api = TestClient(create_app(p)).get("/api/stages").json()["next"]
    assert api == (nxt["key"] if nxt else None)
    p.close()
    r = runner.invoke(app, ["next", "--dir", str(p.root)])
    assert r.stdout.strip() != "wider providers"


def _records_first(tmp_path):
    from tests.unit.test_stage_pages import RIS
    from wider.construct import artifacts
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus"], questions=["What leads employees to speak up?"])
    p.config.stage_providers["construct"] = "fake"
    p.save()
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    sync(p)
    (tmp_path / "a.ris").write_text(RIS)
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    return p, artifacts


def test_next_passes_over_query_with_records(tmp_path):
    """Scenario "Records imported before a search is locked": with the question, a seed paper and approved concepts,
    records imported and no locked search, `wider next` names Deduplication, Search strings stays not completed, and
    /api/stages names the same step."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    p, artifacts = _records_first(tmp_path)
    # before the concepts are approved, Concepts and synonyms comes first: never passed over
    assert next_stage(p)["key"] == "construct"
    from tests.unit.test_construct import CELLS
    for kind, data in (("cells", CELLS), ("synonyms", {"blocks": [{"key": "voice", "name": "voice",
                                                                    "terms": ["employee voice"]}],
                                                        "logic": {"and": ["voice"]}})):
        row = artifacts.save_new_version(p, kind, data, origin="manual")
        artifacts.set_status(p, kind, row["version"], "approved")
    st = {s["key"]: s for s in stage_states(p)}
    assert st["construct"]["state"] == "done" and st["query"]["state"] == "ready" and st["import"]["state"] == "done"
    assert next_stage(p)["key"] == "dedup"
    assert TestClient(create_app(p)).get("/api/stages").json()["next"] == "dedup"
    assert {s["key"]: s for s in stage_states(p)}["query"]["state"] == "ready"   # open, optional, not completed
    p.close()
    r = runner.invoke(app, ["next", "--dir", str(p.root)])
    assert r.stdout.strip() == "wider dedup run"


def test_route_sentence_from_state(tmp_path):
    """Scenario "Starting from records" (its CLI half): the route sentence from state says writing search strings is
    optional, that the Review plan and Concepts and synonyms are still needed for ranking and screening and
    that seed paper coverage is checked once the uploads are marked final; it never calls Concepts and synonyms
    optional, and `wider status` prints it."""
    from wider.core.stages import route_sentence

    p, _ = _records_first(tmp_path)
    s = route_sentence(p)
    assert "writing search strings is optional" in s
    assert "Review plan and Concepts and synonyms are still needed" in s
    assert "ranking reads the anchor sentences and screening the codebook" in s
    assert "Seed paper coverage is checked once the uploads are marked final" in s
    assert "Concepts and synonyms is optional" not in s and "Concepts and synonyms are optional" not in s
    p.close()
    r = runner.invoke(app, ["status", "--dir", str(p.root)])
    assert s in " ".join(r.stdout.split())
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    q = Project.load(p.root)
    assert TestClient(create_app(q)).get("/api/stages").json()["route"] == s   # the Overview reads the same
    q.close()
    fresh = Project.init(tmp_path / "fresh", platforms=["scopus"])
    assert "optional" not in route_sentence(fresh)
    fresh.close()


def test_used_by_lists(tmp_path):
    """Scenario "The used_by lists": enrich lists rank and screen, every name is a stage key, and an unknown key is
    refused by name."""
    import yaml

    from wider.core.stages import STAGES_FILE, load_stages

    stages = {s["key"]: s for s in load_stages()}
    assert {"rank", "screen"} <= set(stages["enrich"]["used_by"])
    assert all(k in stages for s in stages.values() for k in s["used_by"])
    data = yaml.safe_load(STAGES_FILE.read_text(encoding="utf-8"))
    next(s for s in data["stages"] if s["key"] == "enrich")["used_by"].append("nosuch")
    bad = tmp_path / "stages.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="stage enrich: unknown stage nosuch in used_by"):
        load_stages(bad)


def test_status_lists_needs_and_review(screened):  # noqa: F811
    """Scenario "Overview of a project with work waiting" (CLI half): the summary the Overview reads and `wider status`
    name the same items (Ranking needs review), the open questions with their count and the review packages still out
    with their count only, and the same next step."""
    from tests.unit.test_rank_versions import add_abstracts
    from wider.core import questions as qs
    from wider.core.summary import project_summary

    p = screened
    add_abstracts(p)
    qs.raise_question(p, "seed_unresolved", "B9", "Seed", "No abstract.", ref="B9")
    p.store.execute("INSERT INTO review_packages (package_id, reviewer, mode, kind, samples, n_items, file, made_at)"
                    " VALUES ('pk1', 'ana', 'blind', 'labels', '[]', 4, 'x.zip', '2026-10-29T00:00:00')")
    p.store.execute("INSERT INTO review_packages (package_id, reviewer, mode, kind, samples, n_items, file, made_at,"
                    " imported_at) VALUES ('pk2', 'ben', 'blind', 'labels', '[]', 4, 'y.zip', '2026-10-29T00:00:00',"
                    " '2026-10-29T01:00:00')")
    p.store.conn.commit()
    s = project_summary(p)
    assert ("rank", "needs_review") in [(x["stage"], x["kind"]) for x in s["needs_you"]]
    assert s["open_questions"] == 1 and s["packages_out"] == 1
    assert s["questions"] == p.config.questions and s["route"]
    nxt = s["next"]
    p.close()
    r = runner.invoke(app, ["status", "--dir", str(p.root)], env={"COLUMNS": "250"})
    out = " ".join(r.output.split())
    assert r.exit_code == 0
    assert "needs you: Ranking: needs review: the records or their abstracts changed since this ranking" in out
    assert "open questions: 1 (wider questions)" in out and "review packages out: 1" in out
    assert "ana" not in out.split("review packages out")[1][:40]   # the count only
    assert f"next: wider {nxt['command']}" in out


# ------------------------------------------------------------------------------- D32: UX review fixes F1-F3
def _dedup_with_pairs(tmp_path):
    from tests.unit.test_dedup import RIS_A2, RIS_B2, _project_with_imports
    from wider.dedup.engine import run

    p = _project_with_imports(tmp_path, RIS_A2, RIS_B2)
    p.config.questions = ["What leads employees to speak up?"]   # the Review plan done, so it comes before nothing
    p.save()
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    sync(p)
    for kind in ("cells", "synonyms"):   # Concepts and synonyms approved: every stage before Deduplication is done
        p.store.execute("INSERT INTO artifacts (kind, version, path, sha256, status, origin, created_at) "
                        "VALUES (?, 1, 'x', 'h', 'approved', 'manual', '2026-10-30')", (kind,))
    p.store.conn.commit()
    run(p)
    return p


def test_next_open_work_dedup(tmp_path):
    """Scenario "Pairs to decide come before an empty later step" (D32 F1): a done Deduplication with pairs waiting is
    the recommended step even though a later stage is ready; `wider next` and /api/stages name it."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    p = _dedup_with_pairs(tmp_path)
    st = {s["key"]: s for s in stage_states(p)}
    assert st["dedup"]["state"] == "done" and "to decide" in st["dedup"]["needs_you"]
    assert any(s["state"] == "ready" for k, s in st.items() if k != "dedup")
    assert next_stage(p)["key"] == "dedup"
    assert TestClient(create_app(p)).get("/api/stages").json()["next"] == "dedup"
    p.close()
    r = runner.invoke(app, ["next", "--dir", str(p.root), "--why"])
    assert r.stdout.splitlines()[0] == "wider dedup run" and "needs you: " in r.stdout


def test_next_unchecked_imports(tmp_path):
    """Scenario "Records imported after the last run" (D32 F1): once the pairs are decided, records imported since the
    run make Deduplication need you with "N new records not checked for duplicates" and keep it the next step."""
    from wider.dedup.engine import decide, queue_rows
    from wider.ingest.importer import add_file, import_all

    p = _dedup_with_pairs(tmp_path)
    for row in queue_rows(p):
        decide(p, row["raw_a"], row["raw_b"], "not_duplicate", by="tester")
    assert {s["key"]: s for s in stage_states(p)}["dedup"]["needs_you"] is None
    assert next_stage(p)["key"] != "dedup"
    (tmp_path / "c.ris").write_text("TY  - JOUR\nTI  - A late arrival\nPY  - 2021\nER  -\n"
                                    "TY  - JOUR\nTI  - Another late arrival\nPY  - 2022\nER  -\n")
    add_file(p, "scopus", tmp_path / "c.ris")
    import_all(p)
    from wider.ingest.importer import set_included
    for r in p.store.query("SELECT source_id FROM sources WHERE included=0"):   # off topic: starts kept out (D32 F8)
        set_included(p, r["source_id"], True)
    dd = {s["key"]: s for s in stage_states(p)}["dedup"]
    assert dd["needs_you"] == "2 new records not checked for duplicates"
    assert next_stage(p)["key"] == "dedup"
    p.close()


def test_done_with_work_left_box(tmp_path):
    """Scenario "Done with work left" (D32 F2): the box of a done stage with work left starts "Not finished: it needs
    you: ..." and never says "Completed"; without work left it says "Completed"."""
    from wider.guide import can_do

    p = _dedup_with_pairs(tmp_path)
    states = stage_states(p)
    text = can_do(p, "dedup", states, next_stage(p, states))
    assert text.startswith("Not finished: it needs you: 2 pairs to decide.") and "Completed" not in text
    imp = can_do(p, "import", states, next_stage(p, states))
    assert imp.startswith("Completed")
    p.close()


def test_setup_needs_prior_review(tmp_path):
    """Scenario "Prior reviews not done" (D32 F2): a done Review plan whose Prior reviews card is not done needs you
    with "Prior reviews to do"; the recommended step is still Concepts and synonyms (`wider construct`)."""
    p = Project.init(tmp_path / "r", platforms=["ebsco"], questions=["What leads employees to speak up?"])
    p.config.stage_providers["construct"] = "fake"
    p.save()
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development")])
    sync(p)
    st = {s["key"]: s for s in stage_states(p)}
    assert st["setup"]["state"] == "done" and "Prior reviews to do" in st["setup"]["needs_you"]
    assert next_stage(p)["key"] == "construct"
    p.close()
    r = runner.invoke(app, ["next", "--dir", str(p.root)])
    assert r.stdout.splitlines()[0] == "wider construct list"


def test_recall_needs_estimate(screened):  # noqa: F811
    """D32 F2: Recall and report needs you while its last report has labels missing or a choice open, and that work
    moves the recommended step; a report without a sample or protocol is not such work."""
    import json

    from wider.core.stages import _open_work

    p = screened
    p.store.set_meta("last_report", json.dumps({"at": "2026-10-30", "sample": None, "protocol": None}))
    rc = {s["key"]: s for s in stage_states(p)}["recall"]
    assert rc["state"] == "done" and rc["needs_you"] is None and not _open_work(rc)
    p.store.set_meta("last_report", json.dumps({"at": "2026-10-30", "labels_incomplete": True}))
    rc = {s["key"]: s for s in stage_states(p)}["recall"]
    assert rc["needs_you"] == "labels missing for the recall estimate" and _open_work(rc)
    p.store.set_meta("last_report", json.dumps({"at": "2026-10-30", "choice_needed": True}))
    assert {s["key"]: s for s in stage_states(p)}["recall"]["needs_you"] == "a choice before the recall estimate"
    p.close()


def test_test_model_flag(screened):  # noqa: F811
    """Scenario "Results from the test model" (D32 F3): Ranking on a test embedding and Screening and Recall from test
    runs carry `test_model`; `wider status` adds "test model" to their state and prints the red line once; the box
    of such a page carries the same line."""
    import json

    from wider.core.stages import TEST_MODEL_LINE
    from wider.guide import can_do

    p = screened
    p.store.execute("INSERT INTO runs (run_id, kind, stamp, spec_hash, provider, model, status, started_at, dir) "
                    "VALUES ('r1', 'screen', 's', 'h', 'fake', 'fake', 'done', '2026-10-30', 'runs/r1')")
    p.store.conn.commit()
    p.store.set_meta("last_report", json.dumps({"at": "2026-10-30"}))
    st = {s["key"]: s for s in stage_states(p)}
    assert st["rank"]["test_model"] is True
    assert st["recall"]["test_model"] is True
    assert all(not s["test_model"] for k, s in st.items() if k not in ("rank", "screen", "recall"))
    states = list(st.values())
    assert TEST_MODEL_LINE in can_do(p, "rank", states, None)
    p.close()
    r = runner.invoke(app, ["status", "--dir", str(p.root)], env={"COLUMNS": "250"})
    out = " ".join(r.output.split())
    assert "done, test model" in out and out.count(TEST_MODEL_LINE) == 1
