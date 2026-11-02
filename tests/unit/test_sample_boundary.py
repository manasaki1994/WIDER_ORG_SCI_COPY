"""Boundary-case pool (D8) and extending an unlocked purposive sample (D9), on a synthetic corpus whose records score
high on a cell only, on a near-miss entry only, or on both (topic-neutral word groups, fake embeddings)."""
import csv
import json
import random
import shutil

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.construct import adjacent as adj
from wider.construct import artifacts
from wider.core import journal
from wider.core.models import BenchmarkPaper, EmbeddingConfig, SamplingBand
from wider.core.project import Project
from wider.core.protocol import SamplingProtocol
from wider.sample import design as dmod
from wider.sample.design import (
    ALL_LABELLED,
    ALREADY,
    LOCKED,
    NO_BOUNDARY_ENTRIES,
    NO_ENTRY,
    NO_RANKING,
    ZERO,
    draw_development,
    draw_probability,
    extend_development,
    queue_hash,
    sampling_log,
)
from wider.sample.labels import import_sample_csv, label, lock, unlock

CELL = "alpha bravo charlie delta echo foxtrot"
NM1 = "golf hotel india juliet kilo lima"
NM2 = "mike november oscar papa quebec romeo"
AL1 = "sierra tango uniform victor whiskey xray"
AL2 = "lantern meadow nickel orchid pepper quartz"
NOISE = ["zulu yankee amber basalt", "cobalt dune ember fjord", "garnet harbor iris jasper"]
GROUPS = [("cell", 15, f"{CELL} {CELL}"), ("nm1", 12, f"{NM1} {NM1}"), ("both1", 10, f"{CELL} {NM1}"),
          ("nm2", 10, f"{NM2} {NM2}"), ("both2", 8, f"{CELL} {NM2}"), ("al1", 8, f"{AL1} {AL1}"),
          ("bothA1", 8, f"{CELL} {AL1}"), ("al2", 8, f"{AL2} {AL2}"), ("bothA2", 8, f"{CELL} {AL2}")]
runner = CliRunner()


def _build(tmp_path, near_miss=("nm1", "nm2")) -> tuple[Project, dict]:
    """The project and a title -> group map; ranked and clustered, no sample drawn."""
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.cluster import cluster
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=256)]
    p.config.default_embedding = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=10, n=None), SamplingBand(name="S2", start=11, end=None, n=8)]
    p.save()
    ris, group = [], {}
    i = 0
    for g, n, text in GROUPS + [("noise", 40, None), ("thin", 5, None)]:
        for j in range(n):
            ab = "" if g == "thin" else (text or NOISE[j % 3] + f" w{j}") + " extra words" * 10
            t = f"Paper {i} {g}"
            group[t] = g
            ris.append(f"TY  - JOUR\nTI  - {t}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {ab}\nER  -\n")
            i += 1
    (tmp_path / "a.ris").write_text("\n".join(ris))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Paper 0 cell", doi="10.1000/p0", set="Development")])
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"cell1": " ".join([CELL] * 8)}}, origin="manual")
    texts = {"nm1": NM1, "nm2": NM2}
    if near_miss:
        artifacts.save_new_version(p, "near_miss", {"near_miss": [
            {"key": k, "breaks": "e1", "decision": "exclude", "text": " ".join([texts[k]] * 8)} for k in near_miss]},
            origin="manual")
    rank(p, top_n=1)
    cluster(p, k=3, top=60)
    return p, group


def _approve(p, key):
    text = {"al1": AL1, "al2": AL2}[key]
    adj.add(p, key, description=" ".join([text] * 4), example_phrases=[text], key=key)


@pytest.fixture
def project(tmp_path):
    p, _ = _build(tmp_path)
    draw_probability(p)
    return p


def _rows(p, name):
    return {r["record_id"]: dict(r) for r in p.store.query(
        "SELECT sr.record_id, sr.pool, sr.queue_pos, m.title FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
        "JOIN records m ON m.record_id=sr.record_id WHERE s.name=?", (name,))}


def _labels(p, name):
    return {(r["record_id"], r["reviewer_id"]): r["decision"] for r in p.store.query(
        "SELECT l.record_id, l.reviewer_id, l.decision FROM labels l JOIN samples s ON s.sample_id=l.sample_id WHERE s.name=?",
        (name,))}


def _cli(p, *args, env=None):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env=env)


def _dev20(p):
    """`development` with 20 records: 4 likely includes, 4 per near-miss entry, 2 per cluster, 2 thin; no boundary pool."""
    d = draw_development(p, per_boundary=0, n_thin=2, n_tail=0)
    assert d["n"] == 20
    return d


def _label_first(p, k, reviewer="alice"):
    rows = sorted(_rows(p, "development").values(), key=lambda r: r["queue_pos"])
    for r in rows[:k]:
        label(p, "development", r["record_id"], reviewer, "exclude")
    return {r["record_id"] for r in rows[:k]}


def _copy(p, tmp_path, name):
    """A copy of the project directory (the store is closed first)."""
    p.close()
    dest = tmp_path / name
    shutil.copytree(p.root, dest)
    return Project.load(dest)


def _client(p):
    from wider.ui.app import create_app

    return TestClient(create_app(p))


def _no_record_named(out: dict, ids: set[int]):
    """The answer names pools and counts only: no record id or title anywhere in it."""
    assert set(out) <= {"name", "pool", "added", "pools", "n", "messages", "change_id", "stage_result"}, out
    assert isinstance(out["added"], int) and isinstance(out["n"], int)
    assert all(isinstance(k, str) and isinstance(v, int) for k, v in out["pools"].items())
    assert all(isinstance(m, str) and "Paper " not in m for m in out["messages"])


# ------------------------------------------------------------------ 4.1 / 4.2: the pool in the draw
def test_default_boundary_quota(tmp_path):
    sp = SamplingProtocol()
    assert (sp.per_cell, sp.pool_size, sp.per_near_miss, sp.near_miss_max_rank, sp.per_adjacent, sp.per_cluster,
            sp.n_thin, sp.n_tail, sp.per_boundary) == (4, 100, 4, 3000, 4, 2, 5, 3, 4)
    p, group = _build(tmp_path)
    _approve(p, "al1")
    d = draw_development(p)
    assert d["design"]["per_boundary"] == 4
    pools = {}
    for r in _rows(p, "development").values():
        pools[r["pool"]] = pools.get(r["pool"], 0) + 1
    assert pools["likely_include:cell1"] == 4 and pools["near_miss:nm1"] == pools["near_miss:nm2"] == 4
    assert pools["adjacent:al1"] == 4 and pools["no_abstract:thin"] == 5
    assert all(v == 2 for k, v in pools.items() if k.startswith("topical_coverage:"))
    assert pools["boundary:near_miss:nm1"] == pools["boundary:near_miss:nm2"] == pools["boundary:adjacent:al1"] == 4
    assert d["design"]["boundary"] == {"near_miss": ["nm1", "nm2"], "adjacent": ["al1"],
                                       "pools": {"boundary:near_miss:nm1": 4, "boundary:near_miss:nm2": 4,
                                                 "boundary:adjacent:al1": 4}}
    # with per_adjacent 0 there is no adjacent: pool, and the boundary:adjacent: pool still follows per_boundary
    d0 = draw_development(p, name="dev_noadj", per_adjacent=0)
    p0 = {r["pool"] for r in _rows(p, "dev_noadj").values()}
    assert d0["design"]["adjacent"] is None and not any(x.startswith("adjacent:") for x in p0)
    assert "boundary:adjacent:al1" in p0


def test_boundary_records_score_high_on_both(tmp_path):
    p, group = _build(tmp_path, near_miss=("nm1",))
    draw_development(p, per_boundary=2, near_miss_pool=4)
    rows = _rows(p, "development").values()
    b = [r for r in rows if r["pool"] == "boundary:near_miss:nm1"]
    assert len(b) == 2 and {group[r["title"]] for r in b} == {"both1"}
    ids = [r["record_id"] for r in rows]
    assert len(ids) == len(set(ids))   # one pool per record: none of the boundary records is in another pool
    assert not ({r["record_id"] for r in b} & {r["record_id"] for r in rows if not r["pool"].startswith("boundary:")})


def test_other_pools_unchanged(tmp_path):
    p, _ = _build(tmp_path)
    _approve(p, "al1")
    draw_probability(p)
    draw_development(p, name="d0", seed=7, per_boundary=0)
    draw_development(p, name="d1", seed=7)
    first = {(r["record_id"], r["pool"]) for r in _rows(p, "d0").values()}
    second = {(r["record_id"], r["pool"]) for r in _rows(p, "d1").values()}
    assert first <= second
    extra = second - first
    assert extra and all(pool.startswith("boundary:") for _, pool in extra)


def test_no_entries_no_pool(tmp_path):
    p, _ = _build(tmp_path, near_miss=())
    d = draw_development(p)
    assert d["design"]["boundary"] == NO_BOUNDARY_ENTRIES == "no near-miss or adjacent entries"
    assert not any(r["pool"].startswith("boundary:") for r in _rows(p, "development").values())
    assert sampling_log(p)["development"]["boundary"] == NO_BOUNDARY_ENTRIES
    r = _cli(p, "sample", "status", env={"COLUMNS": "200"})
    assert r.exit_code == 0 and "boundary: no near-miss or adjacent entries" in r.output, r.output
    draw_development(p, name="dev0", per_boundary=0)
    assert sampling_log(p)["dev0"]["boundary"] == "per_boundary is 0"


def test_boundary_pool_names_read(project, monkeypatch):
    """4.2a: the three-part names group under `boundary`, never under near_miss or adjacent."""
    from wider.screen import compare
    from wider.screen import evaluate as ev_mod

    labs = [{"record_id": 1, "title": "t1", "pool": "boundary:near_miss:nm1", "decision": "exclude", "reason": ""},
            {"record_id": 2, "title": "t2", "pool": "boundary:adjacent:nm1", "decision": "include", "reason": ""},
            {"record_id": 3, "title": "t3", "pool": "near_miss:nm1", "decision": "exclude", "reason": ""},
            {"record_id": 4, "title": "t4", "pool": "adjacent:al1", "decision": "exclude", "reason": ""}]
    calls = {i: {"record_id": i, "parsed": json.dumps({"summary": "s"}), "rule_decision": "include",
                 "raw_decision": "include"} for i in (1, 2, 3, 4)}
    monkeypatch.setattr(ev_mod, "sample_by_name", lambda p, n: {"kind": "purposive", "locked": 1})
    monkeypatch.setattr(ev_mod, "calls_by_record", lambda p, run_id: calls)
    monkeypatch.setattr(ev_mod, "final_labels", lambda p, s, primary=None: labs)
    ev = ev_mod.evaluate(project, "r1", "development")
    assert ev["by_pool"]["boundary"] == {"n": 2, "FALSE INCLUDE": 1, "agree": 1}
    assert ev["by_pool"]["near_miss"] == {"n": 1, "FALSE INCLUDE": 1} and ev["by_pool"]["adjacent"]["n"] == 1
    assert list(ev["by_adjacent"]) == ["al1"]        # boundary:adjacent: is not counted as drawn for a literature
    text = ev_mod.write_report(project, ev)
    assert "- boundary: n 2" in text and "| nm1 |" not in text
    assert compare._pool("boundary:adjacent:nm1") == compare._pool("boundary:near_miss:nm1") == "boundary"


# ------------------------------------------------------------------ 4.3: extending an unlocked sample
def test_extend_keeps_labels_and_queue(project):
    _dev20(project)
    labelled = _label_first(project, 12)
    before = _rows(project, "development")
    labs_before = _labels(project, "development")
    r = _cli(project, "sample", "extend", "development", "--pool", "boundary", "--json", "--author", "ana")
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    after = _rows(project, "development")
    new = set(after) - set(before)
    assert out["added"] == len(new) == 8 and out["n"] == 28 and out["messages"] == []
    assert out["pools"] == {"boundary:near_miss:nm1": 4, "boundary:near_miss:nm2": 4}
    _no_record_named(out, set(after))
    assert "change_id" not in out
    assert _labels(project, "development") == labs_before
    assert all(after[i]["queue_pos"] == before[i]["queue_pos"] for i in labelled)
    free = sorted(before[i]["queue_pos"] for i in set(before) - labelled)
    movers = sorted(after[i]["queue_pos"] for i in set(after) - labelled)
    assert movers == sorted(free + list(range(21, 29)))
    assert any(after[i]["queue_pos"] <= 20 for i in new)          # shuffled in, not appended
    assert project.store.scalar("SELECT n FROM samples WHERE name='development'") == 28
    ext = sampling_log(project)["development"]["extensions"]
    assert len(ext) == 1 and ext[0]["author"] == "ana" and ext[0]["at"] and ext[0]["n"] == 8
    ch = journal.changes(project, "sample_extend")
    assert len(ch) == 1 and ch[0]["author"] == "ana" and ch[0]["kind_label"] == "sample extended"
    assert not any(str(i) in ch[0]["summary"].split() for i in new)


def test_extend_cli_matches_api(project, tmp_path):
    _dev20(project)
    _label_first(project, 12)
    q = _copy(project, tmp_path, "copy")
    t = _copy(Project.load(project.root), tmp_path, "copy_text")
    project = Project.load(project.root)
    r = _cli(project, "sample", "extend", "development", "--json")
    assert r.exit_code == 0, r.output
    cli_out = json.loads(r.output)
    res = _client(q).post("/api/samples/development/extend", params={"pool": "boundary"})
    assert res.status_code == 200, res.text
    api = res.json()
    q = Project.load(q.root)
    assert _rows(q, "development") == _rows(Project.load(project.root), "development")
    assert {k: api[k] for k in cli_out} == cli_out and api["change_id"]
    _no_record_named(api, set(_rows(q, "development")))
    rt = _cli(t, "sample", "extend", "development")
    assert rt.exit_code == 0 and "development: 8 record(s) added, now 28: boundary:near_miss:nm1 4, " \
                                  "boundary:near_miss:nm2 4" in " ".join(rt.output.split()), rt.output


def test_extend_all_labelled_says_so(project, tmp_path):
    _dev20(project)
    _label_first(project, 20)
    q = _copy(project, tmp_path, "copy")
    j = _copy(Project.load(project.root), tmp_path, "copy_json")
    project = Project.load(project.root)
    msg = ALL_LABELLED.format(n=20)
    assert msg == "all 20 earlier records are labelled: the added records are recognisable as the boundary pool"
    r = _cli(project, "sample", "extend", "development")
    assert r.exit_code == 0 and msg in " ".join(r.output.split()), r.output
    rj = _cli(j, "sample", "extend", "development", "--json")
    assert json.loads(rj.output)["messages"] == [msg] and json.loads(rj.output)["added"] == 8
    res = _client(q).post("/api/samples/development/extend")
    assert res.status_code == 200 and res.json()["messages"] == [msg]


def test_extend_twice_adds_nothing(project):
    draw_development(project)                         # drawn with the boundary pool
    h = queue_hash(project)
    r = _cli(project, "sample", "extend", "development")
    assert r.exit_code == 0 and ALREADY.format(name="development") in r.output, r.output
    assert ALREADY.format(name="development") == "development already has the boundary pool for every entry"
    assert queue_hash(project) == h and journal.changes(project, "sample_extend") == []
    draw_development(project, name="dev2", per_boundary=0)
    assert _cli(project, "sample", "extend", "dev2").exit_code == 0
    h = queue_hash(project)
    r = _cli(project, "sample", "extend", "dev2", "--json")
    out = json.loads(r.output)
    assert r.exit_code == 0 and out["added"] == 0 and out["messages"] == [ALREADY.format(name="dev2")]
    assert queue_hash(project) == h


def test_extend_new_entry_gets_its_pool(project):
    _approve(project, "al1")
    draw_development(project)
    before = {rid: r["pool"] for rid, r in _rows(project, "development").items()}
    _approve(project, "al2")
    out = extend_development(project, "development")
    after = {rid: r["pool"] for rid, r in _rows(project, "development").items()}
    added = {rid: pool for rid, pool in after.items() if rid not in before}
    assert out["pools"] == {"boundary:adjacent:al2": 4} and set(added.values()) == {"boundary:adjacent:al2"}
    assert all(after[rid] == pool for rid, pool in before.items())     # boundary:near_miss:nm1 and the rest unchanged


def test_extend_refused_locked_or_probability(project, tmp_path):
    _dev20(project)
    lock(project, "development")
    h, nh = queue_hash(project), len(journal.changes(project))
    r = _cli(project, "sample", "extend", "development", "--pool", "boundary")
    assert r.exit_code == 1 and LOCKED.format(name="development") in " ".join(r.output.split()), r.output
    assert LOCKED.format(name="development") == "sample development is locked; wider sample unlock development first"
    r = _cli(project, "sample", "extend", "validation", "--pool", "boundary")
    assert r.exit_code == 1 and "only a purposive sample can be extended" in " ".join(r.output.split()), r.output
    c = _client(Project.load(project.root))
    res = c.post("/api/samples/development/extend", params={"pool": "boundary"})
    assert res.status_code == 409 and res.json()["detail"] == LOCKED.format(name="development")
    res = c.post("/api/samples/validation/extend")
    assert res.status_code == 400 and "only a purposive sample can be extended" in res.json()["detail"]
    assert c.post("/api/samples/nope/extend").status_code == 404
    project = Project.load(project.root)
    assert queue_hash(project) == h and len(journal.changes(project)) == nh


def test_extend_without_entries_or_consent(tmp_path, monkeypatch):
    p, _ = _build(tmp_path, near_miss=())
    draw_development(p)
    r = _cli(p, "sample", "extend", "development")
    assert r.exit_code == 0 and NO_ENTRY in r.output, r.output
    assert NO_ENTRY == "no near-miss or adjacent entry: no boundary records to add"
    p.config.protocol.sampling.per_boundary = 0
    p.save()
    r = _cli(p, "sample", "extend", "development")
    assert r.exit_code == 0 and ZERO in r.output and ZERO == "per_boundary is 0: no boundary records to add"
    # the ranking's embedding backend becomes remote, without consent: refused before any embedding call
    q, _ = _build(tmp_path / "q")
    draw_development(q, per_boundary=0)
    q.config.embeddings = [EmbeddingConfig(name="fake", kind="openai_compat", model="emb-1",
                                           base_url="https://embeddings.example.invalid/v1")]
    q.save()
    calls = []
    monkeypatch.setattr(dmod, "embed_texts", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError))
    h, nh = queue_hash(q), len(journal.changes(q))
    r = _cli(q, "sample", "extend", "development")
    assert r.exit_code == 2, r.output
    res = _client(Project.load(q.root)).post("/api/samples/development/extend")
    assert res.status_code == 403 and res.json()["detail"]["backend_consent_required"]["backend"] == "fake"
    q = Project.load(q.root)
    assert calls == [] and queue_hash(q) == h and len(journal.changes(q)) == nh


def test_extend_imported_sample(project, tmp_path, monkeypatch):
    taken = {r["record_id"] for r in project.store.query("SELECT record_id FROM sample_rows")}
    dois = [r["doi"] for r in project.store.query("SELECT record_id, doi FROM records WHERE thin=0 ORDER BY record_id")
            if r["record_id"] not in taken][:6]
    path = tmp_path / "dev2.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doi", "decision"])
        w.writerows([[d, ""] for d in dois])
    r = _cli(project, "sample", "import", "dev2", str(path), "--kind", "purposive")
    assert r.exit_code == 0, r.output
    assert _cli(project, "sample", "unlock", "dev2").exit_code == 0
    seeds = []
    real = random.Random

    def spy(seed=None):
        seeds.append(seed)
        return real(seed)

    monkeypatch.setattr(dmod.random, "Random", spy)
    r = _cli(project, "sample", "extend", "dev2", "--json")
    assert r.exit_code == 0 and json.loads(r.output)["added"] == 8, r.output
    assert seeds == [Project.load(project.root).config.seed + 13]
    # imported without a ranking: refused, nothing stored
    import_sample_csv(project, "dev3", path, kind="purposive", ranking_name="no-such-ranking")
    unlock(project, "dev3")
    h, nh = queue_hash(project), len(journal.changes(project))
    r = _cli(project, "sample", "extend", "dev3")
    msg = NO_RANKING.format(name="dev3")
    assert msg == "sample dev3 was imported without a ranking: no boundary records can be drawn"
    assert r.exit_code == 1 and msg in " ".join(r.output.split()), r.output
    res = _client(Project.load(project.root)).post("/api/samples/dev3/extend")
    assert res.status_code == 400 and res.json()["detail"] == msg
    project = Project.load(project.root)
    assert queue_hash(project) == h and len(journal.changes(project)) == nh


def test_extend_undo(project, tmp_path):
    _dev20(project)
    _label_first(project, 12)
    before = _rows(project, "development")
    labs = _labels(project, "development")
    out = extend_development(project, "development", author="ana")
    new = set(_rows(project, "development")) - set(before)
    cid = out["change_id"]
    r = _cli(project, "history", "list", env={"COLUMNS": "250"})
    assert "sample extended" in r.output, r.output
    copies = {k: _copy(Project.load(project.root), tmp_path, k) for k in ("bob", "adj", "lock", "route")}
    project = Project.load(project.root)
    r = _cli(project, "history", "undo", str(cid), "--author", "ana")
    assert r.exit_code == 0, r.output
    assert _rows(project, "development") == before and _labels(project, "development") == labs
    assert project.store.scalar("SELECT n FROM samples WHERE name='development'") == 20
    # an added record labelled by the second reviewer only, or adjudicated only: refused, nothing changes
    one = min(new)
    label(copies["bob"], "development", one, "bob", "include")
    # adjudicate() refuses a record without a disagreement; an older store can still hold such a row
    sid = copies["adj"].store.scalar("SELECT sample_id FROM samples WHERE name='development'")
    copies["adj"].store.execute("INSERT INTO adjudications(sample_id,record_id,decision,decided_by,decided_at) "
                                "VALUES(?,?,'include','carol','2026-11-01T00:00:00+00:00')", (sid, one))
    copies["adj"].store.conn.commit()
    for k in ("bob", "adj"):
        q = Project.load(copies[k].root)
        h = queue_hash(q)
        r = _cli(q, "history", "undo", str(cid), "--author", "ana")
        assert r.exit_code == 1 and "a record added by this extension is labelled" in " ".join(r.output.split()), r.output
        q = Project.load(q.root)
        assert queue_hash(q) == h and q.store.scalar("SELECT n FROM samples WHERE name='development'") == 28
    # locked: refused; after unlocking, the undo works
    q = Project.load(copies["lock"].root)
    lock(q, "development")
    r = _cli(q, "history", "undo", str(cid), "--author", "ana")
    assert r.exit_code == 1 and "locked" in r.output, r.output
    unlock(q, "development")
    r = _cli(q, "history", "undo", str(cid), "--author", "ana")
    assert r.exit_code == 0, r.output
    assert Project.load(q.root).store.scalar("SELECT n FROM samples WHERE name='development'") == 20
    # History's Undo button calls the route
    q = Project.load(copies["route"].root)
    res = _client(q).post(f"/api/history/{cid}/undo", headers={"X-Wider-Author": "ana"})
    assert res.status_code == 200, res.text
    assert _rows(Project.load(q.root), "development") == before
