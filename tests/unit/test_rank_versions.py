"""ui-redesign D5: ranking versions. A re-rank adds a version and never changes an earlier one, an identical rerun
reuses it, samples keep the version they were drawn from, new work uses the newest version, an ablation leaves the
chosen ranking alone, and a v9 store opens with every link intact."""
import json
import sqlite3

from typer.testing import CliRunner

from tests.unit.test_screen import project, project_open  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.rank import versions as rv
from wider.rank.cluster import cluster
from wider.rank.corpus import embed_corpus
from wider.rank.score import rank
from wider.sample.design import draw_development, draw_probability

runner = CliRunner()


def _rows(p, rid):
    return [tuple(r) for r in p.store.query("SELECT record_id, rank, score, best_cell, cell_scores FROM ranking_rows "
                                            "WHERE ranking_id=? ORDER BY record_id", (rid,))]


def _sample_ids(p):
    return {r["name"]: r["ranking_id"] for r in p.store.query("SELECT name, ranking_id FROM samples")}


def add_abstracts(p, n=3):
    """n records get an abstract from Missing abstracts (here written directly)."""
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM records ORDER BY record_id DESC LIMIT ?", (n,))]
    for i in ids:
        p.store.execute("UPDATE records SET abstract=abstract || ' a longer abstract from a lookup about speaking up' "
                        "WHERE record_id=?", (i,))
    return ids


def test_rerank_adds_a_version(project):  # noqa: F811
    """Scenario "A re-rank adds a version": version 2 is stored and becomes the default, version 1's rows are
    unchanged, both samples still read version 1, and `wider rank run` says they keep it without asking."""
    p = project
    v1 = p.store.one("SELECT * FROM rankings WHERE name=?", (p.store.get_meta("default_ranking"),))
    before = _rows(p, v1["ranking_id"])
    assert set(_sample_ids(p).values()) == {v1["ranking_id"]}
    assert (v1["base_name"], v1["version"], v1["current_run"]) == (v1["name"], 1, 1) and v1["input_hash"]
    add_abstracts(p)
    embed_corpus(p)
    p.close()
    r = runner.invoke(cli_app, ["rank", "run", "--top-n", "1", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "Samples keep the version they were drawn from: validation and development keep version 1." in out
    p = Project.load(p.root)
    name2 = f"{v1['name']}@v2"
    assert p.store.get_meta("default_ranking") == name2
    v2 = p.store.one("SELECT * FROM rankings WHERE name=?", (name2,))
    assert (v2["base_name"], v2["version"], v2["current_run"]) == (v1["name"], 2, 1)
    assert _rows(p, v1["ranking_id"]) == before
    assert set(_sample_ids(p).values()) == {v1["ranking_id"]}
    vs = rv.versions(p, v1["name"])
    assert [v["version"] for v in vs] == [1, 2] and vs[0]["samples"] == ["validation", "development"]
    from wider.rank.score import explorer
    ex = explorer(p)   # the Ranking page: one ranking to choose, its two versions in the history
    assert ex["bases"] == [v1["name"]] and [v["version"] for v in ex["versions"]] == [1, 2]
    assert ex["ranking"]["name"] == name2
    p.close()


def test_rerank_without_samples(project_open):  # noqa: F811
    """Scenario "A re-rank nothing depends on": without samples a changed input adds a version and no note; an
    identical rerun reuses it and says so."""
    p = project_open
    p.store.execute("DELETE FROM sample_rows")
    p.store.execute("DELETE FROM samples")
    first = p.store.get_meta("default_ranking")
    add_abstracts(p)
    embed_corpus(p)
    r = rank(p, top_n=1)
    assert r["version"] == 2 and not r["reused"] and r["samples_keep"] == []
    n = p.store.scalar("SELECT COUNT(*) FROM rankings")
    again = rank(p, top_n=1)
    assert again["reused"] and again["name"] == r["name"] and p.store.scalar("SELECT COUNT(*) FROM rankings") == n
    head = json.loads(p.store.get_meta("last_result:rank"))["headline"]
    assert "version 2 was reused and nothing was added" in head
    assert p.store.get_meta("default_ranking") == f"{first}@v2"


def test_new_work_newest_old_samples_theirs(project):  # noqa: F811
    """Scenario "New work uses the newest version, old samples keep theirs": after the re-rank a new draw and a fresh
    validation sample use version 2, while the old validation sample's recall frame and the methods draft are as
    before."""
    from wider.recall.curve import _band_spans
    from wider.recall.outputs import methods_draft

    p = project
    v1 = p.store.get_meta("default_ranking")
    spans = _band_spans(p, "validation")
    methods = methods_draft(p)
    add_abstracts(p)
    embed_corpus(p)
    rank(p, top_n=1)
    assert _band_spans(p, "validation") == spans
    assert methods_draft(p) == methods
    v2id = p.store.scalar("SELECT ranking_id FROM rankings WHERE name=?", (f"{v1}@v2",))
    d = draw_development(p, name="dev2", cluster_max_rank=20, near_miss_max_rank=20)
    draw_probability(p, name="validation2", fresh_from="validation")
    assert _sample_ids(p)["dev2"] == v2id and _sample_ids(p)["validation2"] == v2id
    assert d["design"]["ranking"] == f"{v1}@v2"
    assert _band_spans(p, "validation") == spans
    # a base name given for new work is its newest version; NAME@v1 names the first
    assert rv.resolve(p, v1) == f"{v1}@v2" and rv.resolve(p, f"{v1}@v1") == v1


def test_new_draw_keeps_clustering(project):  # noqa: F811
    """Scenario "A new draw keeps the clustering": a clustering made on version 1 is used by a draw on version 2,
    which says so."""
    p = project
    v1 = p.store.get_meta("default_ranking")
    cluster(p, k=2, top=20)
    add_abstracts(p)
    embed_corpus(p)
    rank(p, top_n=1)
    d = draw_development(p, name="dev2", cluster_max_rank=20, near_miss_max_rank=20)
    assert d["design"]["clusters"] == 2
    assert any(x.startswith("topical_coverage:") for x in [r["pool"] for r in p.store.query(
        "SELECT pool FROM sample_rows WHERE sample_id=?", (d["sample_id"],))])
    assert "made on version 1" in d["design"]["clustering_note"] and "uses version 2" in d["design"]["clustering_note"]
    assert d["messages"] == [d["design"]["clustering_note"]]
    assert not p.store.get_meta(f"clusters:{v1}@v2:2")


def test_ablation_leaves_default(project):  # noqa: F811
    """Scenario "An ablation leaves the chosen ranking alone": unchanged inputs reuse the version; after
    corpus.thin_words changed the result goes under an ablation-only name; the default, the ranking new draws use and
    the number of versions are unchanged both times."""
    from wider.rank.ablate import ablation

    p = project
    v1 = p.store.get_meta("default_ranking")
    ablation(p)
    assert p.store.get_meta("default_ranking") == v1 and len(rv.versions(p, v1)) == 1
    p.config.protocol = p.config.protocol.set("corpus.thin_words", 3)
    p.save()
    res = ablation(p)
    assert p.store.get_meta("default_ranking") == v1 and len(rv.versions(p, v1)) == 1
    assert rv.resolve(p, None) == v1 and rv.resolve(p, v1) == v1
    names = {r["ranking"] for r in res["rows"]}
    assert f"{v1}~ablation" in names
    row = p.store.one("SELECT * FROM rankings WHERE name=?", (f"{v1}~ablation",))
    assert row["current_run"] == 0 and row["base_name"] == f"{v1}~ablation"


def _make_v9(root):
    """Turn a store into one an older version wrote: no v10 columns, no counter triggers, schema 9."""
    st = sqlite3.connect(root / "store.sqlite", isolation_level=None)
    for t in ("records_changes_ins", "records_changes_upd", "records_changes_del"):
        st.execute(f"DROP TRIGGER IF EXISTS {t}")
    for c in ("base_name", "version", "input_hash", "current_run", "embedding_model"):
        st.execute(f"ALTER TABLE rankings DROP COLUMN {c}")
    st.execute("DELETE FROM meta WHERE key LIKE 'rank_inputs:%' OR key='records_changes'")
    st.execute("UPDATE meta SET value='9' WHERE key='schema_version'")
    st.close()


def test_v9_store_opens_with_links_intact(project_open):  # noqa: F811
    """A v9 store with rankings, ranking rows, samples and labels opens (from Home too) with every link intact: the
    ranking is version 1 of its own name with its embedding model, has no input hash and is never marked; opening it
    twice changes nothing; a re-rank then adds version 2."""
    from fastapi.testclient import TestClient

    from wider.core.stages import stage_states
    from wider.core.store import SCHEMA_VERSION
    from wider.sample.labels import label
    from wider.ui.app import create_app

    p = project_open
    dev = p.store.scalar("SELECT sample_id FROM samples WHERE name='development'")
    rid = p.store.scalar("SELECT record_id FROM sample_rows WHERE sample_id=? LIMIT 1", (dev,))
    label(p, "development", rid, "ana", "include")
    name = p.store.get_meta("default_ranking")
    rows = _rows(p, p.store.scalar("SELECT ranking_id FROM rankings WHERE name=?", (name,)))
    p.close()
    _make_v9(p.root)
    other = Project.init(p.root.parent / "other", platforms=["scopus"])
    c = TestClient(create_app(other))
    assert c.post("/api/projects/open", json={"path": str(p.root)}).status_code == 200   # opened from Home
    other.close()
    for _ in range(2):
        q = Project.load(p.root)
        st = q.store
        assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
        r = st.one("SELECT * FROM rankings WHERE name=?", (name,))
        assert (r["base_name"], r["version"], r["current_run"], r["embedding_model"], r["input_hash"]) == \
            (name, 1, 1, "fake", None)
        assert _rows(q, r["ranking_id"]) == rows
        assert set(_sample_ids(q).values()) == {r["ranking_id"]}
        assert st.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=?", (dev,)) == 1
        q.close()
    q = Project.load(p.root)
    add_abstracts(q)
    assert int(q.store.get_meta("records_changes")) >= 3   # the counter triggers are back
    assert next(s for s in stage_states(q) if s["key"] == "rank")["needs_review"] is None   # no hash: never marked
    embed_corpus(q)
    r2 = rank(q, top_n=1)
    assert r2["version"] == 2 and r2["name"] == f"{name}@v2"
    q.close()
