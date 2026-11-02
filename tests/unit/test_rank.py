import json
import statistics

import numpy as np
import pytest

from wider.construct import artifacts
from wider.core.models import BenchmarkPaper, EmbeddingConfig
from wider.core.project import Project
from wider.dedup.engine import run as dedup_run
from wider.ingest.importer import add_file, import_all
from wider.rank.ablate import ablate
from wider.rank.cluster import cluster
from wider.rank.corpus import clean_keywords, embed_corpus, record_text
from wider.rank.score import explorer, rank, score_matrix, validate


def test_strip_boilerplate():
    from wider.rank.corpus import strip_boilerplate
    t = "Findings show X. (PsycInfo Database Record (c) 2026 APA, all rights reserved) Copyright 2020 Elsevier Ltd. All rights reserved."
    assert strip_boilerplate(t) == "Findings show X."


def test_keywords_and_text():
    assert clean_keywords("Adult; blame; Blame; meetings") == "blame; meetings"
    assert record_text("T", "A b", "x; human", 3) == "T A b"


def test_score_matrix_top_n():
    E = np.array([[1, 0], [0, 1]], dtype=float)
    A = np.array([[1, 0], [0.6, 0.8], [0, 1]], dtype=float)
    S, sc = score_matrix(E, A, 2)
    assert S.shape == (2, 3) and np.isclose(sc[0], (1 + 0.6) / 2) and np.isclose(sc[1], (1 + 0.8) / 2)


def _ris(n):
    out = []
    for i in range(n):
        topic = "employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent" if i % 3 == 0 else "ocean plankton temperature bloom"
        out.append(f"TY  - JOUR\nTI  - Paper {i} about {topic}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {topic} " * 1 + f"{' extra words ' * 10}\nER  -\n")
    return "\n".join(out)


def test_rank_validate_ablate_cluster(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / "a.ris").write_text(_ris(30))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="x", doi="10.1000/p3", set="Validation")])
    res = embed_corpus(p)
    assert res["n"] == 30 and res["dim"] == 64 and not res["cached"]
    assert embed_corpus(p)["cached"]
    anchor = " ".join(["employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent"] * 4)
    other = " ".join(["Plankton blooms respond to ocean temperature changes."] * 6)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": anchor, "ocean_cell": other}}, origin="manual")
    r = rank(p, top_n=1)
    assert r["n"] == 30 and r["cells"] == ["voice_cell", "ocean_cell"]
    # top-N outside 1..cells is refused, not silently replaced by the default or written as a new ranking
    n_rankings = p.store.scalar("SELECT COUNT(*) FROM rankings")
    for bad in (0, -3, 3, 99999999):
        with pytest.raises(ValueError, match="1 to 2"):
            rank(p, top_n=bad)
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    rr = TestClient(create_app(p)).post("/api/rank/run?top_n=0")
    assert rr.status_code == 400 and "1 to 2" in rr.json()["detail"]
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    out = CliRunner().invoke(cli_app, ["rank", "run", "--top-n", "0", "--dir", str(p.root)])
    assert out.exit_code == 1 and "1 to 2" in out.output and "Traceback" not in out.output
    assert p.store.scalar("SELECT COUNT(*) FROM rankings") == n_rankings
    v = validate(p)
    assert v["validation_included"] is False and len(v["rows"]) == 1
    assert v["rows"][0]["found"] and v["rows"][0]["rank"] <= 10 and v["rows"][0]["best_cell"] == "voice_cell"
    v2 = validate(p, include_validation=True, seen_choice="keep")   # an early look asks first (D3, Q1)
    assert len(v2["rows"]) == 2 and "Validation" in v2["summary"]
    # the median rank under each compared N comes from the stored cell scores: top-1 is the ranking itself (run with
    # top_n=1), top-2 is the mean of both cells (spec-checker C3 round 2)
    v4 = validate(p, top_n_compare=(1, 2), include_validation=True, seen_choice="keep")
    stored = p.store.query("SELECT record_id, cell_scores FROM ranking_rows")
    mean = {x["record_id"]: sum(json.loads(x["cell_scores"]).values()) / 2 for x in stored}
    by_mean = {rid: i + 1 for i, rid in enumerate(sorted(mean, key=lambda r: -mean[r]))}
    found = [x for x in v4["rows"] if x["found"]]
    assert len(found) == 2 and all(x["rank_top1"] == x["rank"] and x["rank_top2"] == by_mean[x["record_id"]] for x in found)
    assert v4["top_n_compare"] == {n: statistics.median(x[f"rank_top{n}"] for x in found) for n in (1, 2)}
    ex = explorer(p, limit=5)
    assert len(ex["rows"]) == 5 and ex["rows"][0]["rank"] == 1 and "voice_cell" in ex["rows"][0]["cell_scores"]
    ab = ablate(p, top_ns=(1, 2))
    assert len(ab) == 2 and all("dev_median_rank" in a for a in ab)
    # a number in every row, never a missing value (thirtieth round, spec-checker)
    assert all(isinstance(a["dev_median_rank"], (int, float)) and a["dev_median_rank"] >= 1 for a in ab)
    # a comparison above the number of cells is said, not silently capped (eighth round, spec-checker)
    v3 = validate(p, top_n_compare=(1, 3))
    assert v3["notes"] == ["top-3 is more than the 2 cells of these anchors, so it is compared as top-2"]
    assert validate(p, top_n_compare=(1, 2))["notes"] == []
    capped = ablate(p, anchors_versions=[1], top_ns=(1, 3, 99))
    assert [(a["top_n"], a["note"]) for a in capped] == [(1, ""), (2, "top-3, 99 capped at the 2 cells")]
    p.config.protocol.ranking.top_n_compare = [1, 3]
    p.save()
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    out = CliRunner().invoke(cli_app, ["rank", "validate", "--dir", str(p.root)])
    assert out.exit_code == 0 and "compared as top-2" in " ".join(out.output.split())
    assert "top3" in out.output and "top2" not in out.output   # one column per compared value, none missing
    from pydantic import ValidationError

    from wider.core.protocol import RankingProtocol
    with pytest.raises(ValidationError):
        RankingProtocol(top_n_compare=[0])
    cl = cluster(p, k=2, top=30)
    assert cl["k"] == 2 and sum(c["n"] for c in cl["clusters"]) == 30 and -1 <= cl["split_half_ari"] <= 1
    # the protocol default (2) is capped at the cells of a one-cell anchors set instead of refused
    one = artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": anchor}}, origin="manual")
    r1 = rank(p, anchors_version=one["version"])
    assert r1["top_n"] == 1 and r1["name"].endswith("__top1")
    with pytest.raises(ValueError, match="1 to 1"):
        rank(p, anchors_version=one["version"], top_n=2)


def _ranked(tmp_path, n, cluster_k, cluster_top):
    """A project of ``n`` synthetic records ranked against one anchor, with the given protocol cluster settings."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.config.protocol.ranking.cluster_k = cluster_k
    p.config.protocol.ranking.cluster_top = cluster_top
    p.save()
    (tmp_path / "a.ris").write_text(_ris(n))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20}}, origin="manual")
    assert rank(p, top_n=1)["n"] == n
    return p


def _ranked_ids(p, top):
    name = p.store.get_meta("default_ranking")
    rid = p.store.one("SELECT ranking_id FROM rankings WHERE name=?", (name,))["ranking_id"]
    return name, {r["record_id"] for r in p.store.query("SELECT record_id FROM ranking_rows WHERE ranking_id=? AND rank<=?", (rid, top))}


def test_static_cluster_heading_names_records_clustered_and_k(tmp_path):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    js = TestClient(create_app(Project.init(tmp_path / "r"))).get("/static/app.js").text
    # the heading is built from the route's n_clustered and k, never a fixed depth
    assert "Clusters of the top ${esc(c.n_clustered ?? c.top)} (k=${esc(c.k)}, " in js and "Clusters of the top 3000" not in js


def test_cluster_route_uses_protocol_settings(tmp_path):
    """Scenario: Protocol with smaller cluster settings (cluster_k 4, cluster_top 500), on the route and the CLI."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    p = _ranked(tmp_path, 520, cluster_k=4, cluster_top=500)
    name, top500 = _ranked_ids(p, 500)
    assert len(top500) == 500
    c = TestClient(create_app(p))
    r = c.get("/api/rank/cluster").json()
    # THEN 4 clusters are returned over the top 500 records
    assert r["k"] == 4 and len(r["clusters"]) == 4 and [x["cluster"] for x in r["clusters"]] == [0, 1, 2, 3]
    assert r["top"] == 500 and sum(x["n"] for x in r["clusters"]) == 500
    assert all(x["medoid"]["rank"] <= 500 for x in r["clusters"] if x["medoid"])
    full = cluster(p)   # the same defaults as the route: the members are exactly the records ranked 1..500
    assert set(full["labels"]) == top500 and set(full["labels"].values()) == {0, 1, 2, 3}
    assert [x["n"] for x in full["clusters"]] == [x["n"] for x in r["clusters"]]
    # AND the heading reads "Clusters of the top 500 (k=4, ...)": it is built from these two fields
    assert r["n_clustered"] == 500 and r["k"] == 4
    r = c.get("/api/rank/cluster", params={"k": 2, "top": 55}).json()  # explicit values still win
    assert r["k"] == 2 and len(r["clusters"]) == 2 and r["n_clustered"] == 55 == sum(x["n"] for x in r["clusters"])
    # CLI twin: `wider rank cluster` without options clusters with the same protocol settings
    p.store.execute("DELETE FROM meta WHERE key LIKE 'clusters:%'")
    res = CliRunner().invoke(cli_app, ["rank", "cluster", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output
    stored = json.loads(p.store.get_meta(f"clusters:{name}:4"))
    assert stored["k"] == 4 and stored["n_clustered"] == 500 and set(map(int, stored["labels"])) == top500
    assert p.store.get_meta(f"clusters:{name}:8") is None   # not the built-in default k


def test_cluster_ranking_shorter_than_clustering_depth(tmp_path):
    """Scenario: Ranking shorter than the clustering depth (120 records, cluster_top 3000)."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    p = _ranked(tmp_path, 120, cluster_k=3, cluster_top=3000)
    _, ids = _ranked_ids(p, 3000)
    assert len(ids) == 120
    r = TestClient(create_app(p)).get("/api/rank/cluster").json()
    # THEN the heading names 120 records (it shows n_clustered, which wins over top)
    assert r["top"] == 3000 and r["n_clustered"] == 120
    # AND the cluster sizes sum to 120
    assert len(r["clusters"]) == 3 and sum(x["n"] for x in r["clusters"]) == 120
    assert set(cluster(p)["labels"]) == ids


def test_two_rank_runs_at_once_both_succeed(tmp_path):
    # a double click on Rank sends two requests together; the second waits for the first instead of failing
    import threading
    p = _ranked(tmp_path, 30, 3, 50)
    errors, done = [], []
    for _ in range(5):
        gate = threading.Barrier(2)

        def run():
            q = Project.load(p.root)
            gate.wait()
            try:
                done.append(rank(q, top_n=1)["n"])
            except Exception as e:  # noqa: BLE001 - collected and asserted below
                errors.append(repr(e))
            finally:
                q.close()
        ts = [threading.Thread(target=run) for _ in range(2)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    assert errors == [] and done == [30] * 10
    assert p.store.scalar("SELECT COUNT(*) FROM rankings") == 1
    assert p.store.scalar("SELECT COUNT(*) FROM ranking_rows") == 30
    # the rank stage result names the one ranking, with every record ranked
    from wider.core.stage_result import last_result
    res = last_result(p, "rank")
    name = p.store.scalar("SELECT name FROM rankings")
    assert res["ok"] and res["stage"] == "rank" and name in res["headline"]
    assert p.store.get_meta("default_ranking") == name


def test_validate_percentiles_per_set(tmp_path):
    """validation-sample-planner D3: benchmark papers within the top 1/5/10/25/50% per set, alike in the engine, the
    CLI (--json and table) and the route; the hidden Validation set adds no percentiles and records no look."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    p = Project.init(tmp_path / "pc", platforms=["scopus"])
    st = p.store
    n = 2000
    st.executemany("INSERT INTO records(record_id, cluster_id, key, title, doi, abstract_words, thin) VALUES(?,?,?,?,?,?,0)",
                   [(i, i, f"k{i}", f"Record {i}", f"10.1/r{i}", 200) for i in range(1, n + 1)])
    st.execute("INSERT INTO rankings(ranking_id,name,set_name,anchors_version,top_n,n,md5,created_at) "
               "VALUES(1,'r1','fake',1,1,?,'x','2026-01-01')", (n,))
    st.executemany("INSERT INTO ranking_rows(ranking_id,record_id,rank,score,best_cell,cell_scores) VALUES(1,?,?,?,'c',?)",
                   [(i, i, 1 - i / n, json.dumps({"c": 1 - i / n})) for i in range(1, n + 1)])
    st.set_meta("default_ranking", "r1")
    dev = [5, 12, 30, 80, 150, 190, 400, 700, 1200, 1900]
    val = [3, 50, 260, 990]
    p.write_benchmark([BenchmarkPaper(benchmark_id=f"D{r}", title=f"Record {r}", doi=f"10.1/r{r}", set="Development") for r in dev]
                      + [BenchmarkPaper(benchmark_id=f"V{r}", title=f"Record {r}", doi=f"10.1/r{r}", set="Validation") for r in val])
    hidden = validate(p)
    assert list(hidden["summary"]) == ["Development"] and not hidden["validation_peeked"]
    assert [h["k"] for h in hidden["summary"]["Development"]["recall_at_pct"]] == [2, 4, 6, 7, 8]
    assert [h["rank"] for h in hidden["summary"]["Development"]["recall_at_pct"]] == [20, 100, 200, 500, 1000]
    assert st.scalar("SELECT COUNT(*) FROM history") == 0
    v = validate(p, include_validation=True, seen_choice="keep")
    assert [(h["k"], h["n"]) for h in v["summary"]["Validation"]["recall_at_pct"]] == [(1, 4), (2, 4), (2, 4), (3, 4), (4, 4)]
    out = CliRunner().invoke(cli_app, ["rank", "validate", "--json", "--dir", str(p.root)])
    assert out.exit_code == 0, out.output
    assert json.loads(out.output)["summary"]["Development"]["recall_at_pct"] == hidden["summary"]["Development"]["recall_at_pct"]
    table = CliRunner().invoke(cli_app, ["rank", "validate", "--dir", str(p.root)], terminal_width=200)
    assert "top 1% (rank 20)" in table.output and "8 of 10" in table.output
    api = TestClient(create_app(p)).get("/api/rank/validate").json()
    assert api["summary"]["Development"]["recall_at_pct"] == hidden["summary"]["Development"]["recall_at_pct"]
