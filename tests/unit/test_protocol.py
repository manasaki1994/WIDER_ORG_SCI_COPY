import sqlite3

import pytest
import yaml

from wider.core.models import ProjectConfig
from wider.core.project import PROJECT_FILE, Project
from wider.core.protocol import DEFAULT_BANDS, ReviewProtocol
from wider.core.stages import load_stages, next_stage, stage_states
from wider.core.store import SCHEMA_VERSION, Store


def test_defaults_are_the_original_review_values():
    p = ReviewProtocol()
    assert p.identification.jw_benchmark == 0.88
    assert p.dedup.jw_candidate == 0.85 and p.dedup.jw_auto_merge == 0.97
    assert p.corpus.thin_words == 20 and p.corpus.max_words == 3000
    assert p.ranking.top_n_cells == 2 and p.ranking.recall_at == [100, 250, 1000, 3000]
    assert [b.n for b in p.sampling.bands] == [None, 70, 60, 46, 40]
    assert p.sampling.bands == DEFAULT_BANDS
    assert p.screening.passes == 2 and p.screening.majority == 2 and p.screening.quote_min_chars == 8
    assert p.recall.bootstrap_b == 10000 and p.recall.primary_variant == "lenient"
    assert p.enrichment.sources == ["openalex", "crossref", "europepmc", "semantic_scholar"]


def test_every_field_is_explained_and_unchanged_by_default():
    rows = ReviewProtocol().explain()
    assert len(rows) >= 35
    assert all(r["description"] for r in rows), [r["path"] for r in rows if not r["description"]]
    assert not any(r["changed"] for r in rows)
    md = ReviewProtocol().markdown()
    assert "## sampling" in md and "`thin_words`" in md


def test_set_get_hash_and_validation():
    p = ReviewProtocol()
    h = p.hash()
    q = p.set("corpus.thin_words", "25")
    assert q.corpus.thin_words == 25 and q.get("corpus.thin_words") == 25
    assert q.hash() != h and p.hash() == h
    assert [r["path"] for r in q.explain() if r["changed"]] == ["corpus.thin_words"]
    q2 = p.set("ranking.recall_at", "[50, 100]")
    assert q2.ranking.recall_at == [50, 100]
    with pytest.raises(KeyError):
        p.set("corpus.nope", 1)
    with pytest.raises(ValueError):
        p.set("corpus.thin_words", "-3")
    with pytest.raises(ValueError):  # non-contiguous bands
        p.set("sampling.bands", [{"name": "S1", "start": 1, "end": 10, "n": None}, {"name": "S2", "start": 12, "end": None, "n": 5}])
    with pytest.raises(ValueError):  # majority larger than the reads
        p.set("screening.majority", 4)


def test_unknown_protocol_path_is_refused_and_yaml_unchanged(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.ui.app import create_app

    for bad in ("corpus.nope", "nope.thin_words", "thin_words", ""):
        for fn in (lambda b: ReviewProtocol().get(b), lambda b: ReviewProtocol().set(b, 1)):
            with pytest.raises(KeyError, match="section.key"):
                fn(bad)
    p = Project.init(tmp_path / "r", questions=["q"])
    p.config.protocol = p.config.protocol.set("corpus.thin_words", 25)
    p.save()
    before = (tmp_path / "r" / PROJECT_FILE).read_text()
    runner, d = CliRunner(), ["--dir", str(tmp_path / "r")]
    for args in (["reset", "thin_words"], ["reset", "corpus.nope"], ["set", "thin_words", "5"]):
        r = runner.invoke(app, ["protocol", *args, *d])
        assert r.exit_code == 1 and "wider protocol show" in " ".join(r.output.split()) and "Traceback" not in r.output, args
    assert (tmp_path / "r" / PROJECT_FILE).read_text() == before
    c = TestClient(create_app(p))
    r = c.post("/api/protocol/reset", json={"path": "corpus.nope"})
    assert r.status_code == 400 and "section.key" in r.json()["detail"]
    assert (tmp_path / "r" / PROJECT_FILE).read_text() == before
    assert runner.invoke(app, ["protocol", "reset", "corpus.thin_words", *d]).exit_code == 0
    assert Project.load(tmp_path / "r").config.protocol.corpus.thin_words == 20


def test_legacy_project_yaml_is_migrated():
    cfg = ProjectConfig.model_validate({"name": "old", "bands": [{"name": "S1", "start": 1, "end": 10, "n": None},
                                                                  {"name": "S2", "start": 11, "end": None, "n": 5}],
                                        "thresholds": {"thin_words": 30, "jw_dedup": 0.9}})
    assert [b.n for b in cfg.protocol.sampling.bands] == [None, 5]
    assert cfg.protocol.corpus.thin_words == 30 and cfg.protocol.dedup.jw_candidate == 0.9
    assert cfg.thresholds.thin_words == 30 and cfg.thresholds.jw_dedup == 0.9  # compatibility view
    dumped = cfg.model_dump()
    assert "bands" not in dumped and "thresholds" not in dumped and "protocol" in dumped


def test_project_round_trip_keeps_protocol(tmp_path):
    p = Project.init(tmp_path / "r", questions=["q"])
    p.config.protocol = p.config.protocol.set("sampling.per_cell", 6)
    p.save()
    p.close()
    data = yaml.safe_load((tmp_path / "r" / PROJECT_FILE).read_text())
    assert data["protocol"]["sampling"]["per_cell"] == 6
    q = Project.load(tmp_path / "r")
    assert q.config.protocol.sampling.per_cell == 6
    q.close()


def test_store_migrates_v1_decisions_to_labels(tmp_path):
    db = tmp_path / "store.sqlite"
    st = Store(db)  # current schema
    st.close()
    con = sqlite3.connect(db)
    con.executescript("""
    DROP TABLE screen_decisions;
    CREATE TABLE screen_decisions (
      protocol TEXT NOT NULL, record_id INTEGER NOT NULL,
      decision TEXT NOT NULL, votes TEXT, cell TEXT, responder TEXT, role_variant TEXT, reason TEXT,
      decided_at TEXT NOT NULL, PRIMARY KEY (protocol, record_id));
    INSERT INTO screen_decisions VALUES ('p', 1, 'include', NULL, 'c1', 'peer', 'lead', NULL, 'now');
    UPDATE meta SET value='1' WHERE key='schema_version';
    """)
    con.commit()
    con.close()
    st = Store(db)
    assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert "labels" in st.columns("screen_decisions")
    row = st.one("SELECT labels FROM screen_decisions WHERE record_id=1")
    assert row["labels"] == '{"responder": "peer", "role_variant": "lead"}'
    st.close()


def test_stage_registry_and_states(tmp_path):
    stages = load_stages()
    keys = [s["key"] for s in stages]
    assert keys[:3] == ["setup", "construct", "query"] and "recall" in keys
    for s in stages:
        if s["status"] == "available":
            assert s["description"] and s["why"] and s["done_when"], s["key"]
        for r in s["requires"]:
            assert r in keys
    p = Project.init(tmp_path / "r")
    states = {s["key"]: s["state"] for s in stage_states(p)}
    assert states["setup"] == "ready" and states["construct"] == "blocked" and states["fulltext"] == "extension"
    assert next_stage(p)["key"] == "model"   # no construct model yet (ui-redesign D8)
    p.config.stage_providers["construct"] = "fake"
    assert next_stage(p)["key"] == "setup"
    p.config.questions = ["q"]
    p.save()
    p.store.execute("INSERT INTO benchmark_papers(benchmark_id,title,set_name) VALUES('B1','t','Development')")
    states = {s["key"]: s["state"] for s in stage_states(p)}
    assert states["setup"] == "done" and states["construct"] == "ready" and states["query"] == "blocked"
    assert next_stage(p)["key"] == "construct"
    p.close()
