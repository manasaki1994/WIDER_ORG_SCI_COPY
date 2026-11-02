import csv

import pytest

from wider.construct import artifacts
from wider.core.models import BenchmarkPaper, EmbeddingConfig, SamplingBand
from wider.core.project import Project
from wider.dedup.engine import run as dedup_run
from wider.ingest.importer import add_file, import_all
from wider.rank.cluster import cluster
from wider.rank.corpus import embed_corpus
from wider.rank.score import rank
from wider.sample.design import draw_development, draw_probability, sampling_log, thin_census
from wider.sample.labels import (
    adjudicate,
    agreement,
    export_csv,
    final_labels,
    import_csv,
    label,
    lock,
    progress,
    queue,
)


def _ris(n):
    out = []
    for i in range(n):
        topic = "employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent" if i % 3 == 0 else "ocean plankton temperature bloom"
        ab = "" if i % 17 == 0 else f"{topic} {'extra words ' * 10}"
        out.append(f"TY  - JOUR\nTI  - Paper {i} about {topic}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {ab}\nER  -\n")
    return "\n".join(out)


@pytest.fixture
def project(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=10, n=None), SamplingBand(name="S2", start=11, end=40, n=8),
                      SamplingBand(name="S3", start=41, end=None, n=6)]
    p.save()
    (tmp_path / "a.ris").write_text(_ris(120))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development")])
    embed_corpus(p)
    anchor = " ".join(["employee voice silence speaking up concerns suggestions supervisors managers upward communication dissent"] * 4)
    other = " ".join(["Plankton blooms respond to ocean temperature changes."] * 6)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": anchor, "ocean_cell": other}}, origin="manual")
    artifacts.save_new_version(p, "near_miss", {"near_miss": [
        {"key": "customer_voice", "breaks": "e1", "decision": "exclude", "text": " ".join(["Studies of customers who complain about a service and the firm's recovery response."] * 4)}]},
        origin="manual")
    rank(p, top_n=1)
    cluster(p, k=3, top=60)
    return p


def test_probability_sample_design(project):
    r = draw_probability(project)
    bands = {b["band"]: b for b in r["design"]["bands"]}
    assert bands["S1"]["n_h"] == bands["S1"]["N_h"] <= 10  # census minus thin and benchmark
    assert bands["S2"]["n_h"] == 8 and bands["S3"]["n_h"] == 6
    assert abs(bands["S2"]["weight"] - bands["S2"]["N_h"] / 8) < 1e-9
    rows = project.store.query("SELECT * FROM sample_rows")
    assert len(rows) == r["n"] and len({x["record_id"] for x in rows}) == r["n"]
    assert all(x["weight"] >= 1 for x in rows)
    # thin and benchmark excluded
    thin = {x["record_id"] for x in project.store.query("SELECT record_id FROM records WHERE thin=1")}
    assert not ({x["record_id"] for x in rows} & thin)
    # reproducible with the same seed
    again = draw_probability(project)
    assert [x["record_id"] for x in project.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")] and again["n"] == r["n"]
    assert len(thin_census(project)) == len(thin)


def test_redraw_keeps_the_sample_id_and_cli_unlock(project):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    first = draw_probability(project)
    again = draw_probability(project, seed=99)        # an unlabelled sample is redrawn in place
    row = project.store.one("SELECT sample_id, seed, n FROM samples WHERE name=?", (first["name"],))
    assert again["sample_id"] == first["sample_id"] == row["sample_id"] and row["seed"] == 99
    assert project.store.scalar("SELECT COUNT(*) FROM samples") == 1
    assert project.store.scalar("SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (row["sample_id"],)) == again["n"]
    runner, root = CliRunner(), str(project.root)
    r = runner.invoke(cli_app, ["sample", "lock", first["name"], "--dir", root])
    assert r.exit_code == 0 and project.store.scalar("SELECT locked FROM samples") == 1
    with pytest.raises(ValueError, match="locked"):
        draw_probability(project)
    r = runner.invoke(cli_app, ["sample", "unlock", first["name"], "--dir", root])
    assert r.exit_code == 0 and f"unlocked {first['name']}" in r.output
    assert project.store.scalar("SELECT locked FROM samples") == 0
    assert runner.invoke(cli_app, ["sample", "unlock", "nope", "--dir", root]).exit_code == 1


def test_development_sample_and_labels(project, tmp_path):
    draw_probability(project)
    d = draw_development(project, cluster_max_rank=60, near_miss_max_rank=60)
    pools = [x["pool"].split(":")[0] for x in project.store.query("SELECT pool FROM sample_rows WHERE sample_id=?", (d["sample_id"],))]
    assert {"likely_include", "near_miss", "topical_coverage", "no_abstract", "clearly_irrelevant"} <= set(pools)
    val_ids = {x["record_id"] for x in project.store.query("SELECT record_id FROM sample_rows WHERE sample_id=(SELECT sample_id FROM samples WHERE name='validation')")}
    dev_ids = {x["record_id"] for x in project.store.query("SELECT record_id FROM sample_rows WHERE sample_id=?", (d["sample_id"],))}
    assert not (val_ids & dev_ids)
    assert set(sampling_log(project)) == {"validation", "development"}
    # blinded queue hides band/pool/rank
    q = queue(project, "alice")
    assert q and "band" not in q[0] and "rank" not in q[0] and "pool" not in q[0] and q[0]["decision"] is None
    # two reviewers, blind to each other
    first = q[0]
    sname = "validation" if first["record_id"] in val_ids else "development"
    label(project, sname, first["record_id"], "alice", "include", labels={"cell": "voice_cell"})
    label(project, sname, first["record_id"], "bob", "exclude", reason="a")
    qa = queue(project, "alice", [sname])
    qb = queue(project, "bob", [sname])
    assert next(x for x in qa if x["record_id"] == first["record_id"])["decision"] == "include"
    assert next(x for x in qb if x["record_id"] == first["record_id"])["decision"] == "exclude"
    ag = agreement(project, sname, "alice", "bob")
    assert ag["n"] == 1 and len(ag["disagreements"]) == 1
    adjudicate(project, sname, first["record_id"], "maybe", by="chair")
    fl = final_labels(project, sname)
    assert next(x for x in fl if x["record_id"] == first["record_id"])["decision"] == "maybe"
    assert progress(project, sname)["reviewers"]["alice"]["done"] == 1
    lock(project, sname)
    # a lock keeps labels open: a change needs a reason for the correction, a new reviewer's label is an addition (D2)
    with pytest.raises(ValueError, match="needs a reason for the correction"):
        label(project, sname, first["record_id"], "alice", "exclude")
    label(project, sname, first["record_id"], "alice", "exclude", correction_reason="misread the abstract")
    label(project, sname, first["record_id"], "carol", "include")
    assert {x["reviewer_id"]: x["decision"] for x in project.store.query(
        "SELECT reviewer_id, decision FROM labels WHERE record_id=?", (first["record_id"],))} == {
        "alice": "exclude", "bob": "exclude", "carol": "include"}


def test_csv_round_trip_and_rayyan(project, tmp_path):
    draw_probability(project)
    out = tmp_path / "q.csv"
    n = export_csv(project, "validation", out)
    rows = list(csv.DictReader(open(out)))
    assert len(rows) == n and "band" not in rows[0]
    for r in rows[:3]:
        r["decision"] = "include"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    res = import_csv(project, "validation", out, reviewer_id="carol")
    assert res["imported"] == 3 and res["no_decision"] == n - 3
    # Rayyan-style export: decisions in the notes, matched by DOI
    ray = tmp_path / "rayyan.csv"
    with open(ray, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "doi", "notes"])
        rec = project.store.one("SELECT m.title, m.doi FROM sample_rows sr JOIN records m ON m.record_id=sr.record_id LIMIT 1")
        w.writerow([rec["title"], rec["doi"], 'RAYYAN-INCLUSION: {"Dana"=>"Excluded"} | RAYYAN-LABELS: cell:voice_cell | RAYYAN-EXCLUSION-REASONS: a no voice measured'])
    res = import_csv(project, "validation", ray)
    assert res["imported"] == 1
    lab = project.store.one("SELECT * FROM labels WHERE reviewer_id='Dana'")
    assert lab["decision"] == "exclude" and "voice_cell" in lab["labels"] and lab["reason"].startswith("a ")


def test_import_sample_csv(project, tmp_path):
    from wider.sample.labels import import_sample_csv

    recs = project.store.query("SELECT record_id, title, doi FROM records LIMIT 4")
    f = tmp_path / "val.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "doi", "band", "N_h", "n_h", "weight", "decision", "rayyan_labels"])
        w.writerow([recs[0]["title"], recs[0]["doi"], "S1", 84, 84, 1.0, "Included", "cell:voice_cell"])
        w.writerow([recs[1]["title"], "", "S2", 196, 70, 2.8, "Excluded", ""])
        w.writerow(["no such record anywhere", "", "S2", 196, 70, 2.8, "Maybe", ""])
    res = import_sample_csv(project, "validation_original", f)
    assert res["matched"] == 2 and res["unmatched"] == 1 and res["labelled"] == 2
    rows = project.store.query("SELECT * FROM sample_rows WHERE sample_id=?", (res["sample_id"],))
    assert {r["band"] for r in rows} == {"S1", "S2"} and {r["weight"] for r in rows} == {1.0, 2.8}
    fl = final_labels(project, "validation_original")
    assert [x["decision"] for x in fl] == ["include", "exclude"] and fl[0]["labels"] == {"cell": "voice_cell"}
    assert project.store.one("SELECT locked FROM samples WHERE name='validation_original'")["locked"] == 1
