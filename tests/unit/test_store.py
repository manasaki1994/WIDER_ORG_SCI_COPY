from wider.core.store import SCHEMA_VERSION, Store


def test_store_creates_schema_and_meta(tmp_path):
    s = Store(tmp_path / "store.sqlite")
    tables = s.tables()
    for t in ["sources", "raw_records", "dedup_pairs", "records", "rankings", "samples", "labels",
              "runs", "screen_calls", "screen_decisions", "artifacts", "benchmark_matches"]:
        assert t in tables
    assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert s.scalar("PRAGMA journal_mode") == "wal"
    s.close()


def test_store_transaction_rollback(tmp_path):
    s = Store(tmp_path / "store.sqlite")
    s.execute("INSERT INTO reviewers(reviewer_id,name,created_at) VALUES('a','A','now')")
    try:
        with s.tx() as c:
            c.execute("INSERT INTO reviewers(reviewer_id,name,created_at) VALUES('b','B','now')")
            raise RuntimeError("abort")
    except RuntimeError:
        pass
    assert s.scalar("SELECT COUNT(*) FROM reviewers") == 1
    assert s.counts()["reviewers"] == 1
    s.close()


# ----- schema 5 (query-refinement-loop, task 1.1) -----

V4_HEADER = "benchmark_id,title,doi,set,year,first_author,note"


def _cli(*args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    return CliRunner().invoke(cli_app, list(args), env={"COLUMNS": "200"})


def _as_v4(p):
    """Close the project and rewrite its store and benchmark.csv as version 4 wrote them: no v5 tables or columns."""
    import csv
    import sqlite3

    from wider.core.store import V5_COLUMNS, V5_TABLES, V6_TABLES, V12_COLUMNS
    p.close()
    con = sqlite3.connect(p.store_path)
    for name in [ln.split()[5] for ln in (V5_TABLES + V6_TABLES).splitlines() if ln.startswith("CREATE TABLE")]:
        con.execute(f"DROP TABLE {name}")
    for table, cols in (*V5_COLUMNS.items(), *V12_COLUMNS.items()):
        for c in cols:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {c.split()[0]}")
    con.execute("DELETE FROM meta WHERE key='benchmark_csv_v5'")
    con.execute("UPDATE meta SET value='4' WHERE key='schema_version'")
    con.commit()
    con.close()
    f = p.root / "benchmark.csv"
    rows = list(csv.DictReader(f.open(encoding="utf-8")))
    with f.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(V4_HEADER.split(","))
        for r in rows:
            w.writerow([r[k] for k in V4_HEADER.split(",")])


def _two_files_two_papers(tmp_path):
    from tests.unit.test_stage_pages import RIS, RIS_2
    from wider.core.models import BenchmarkPaper
    from wider.core.project import Project
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    (tmp_path / "a.ris").write_text(RIS)
    (tmp_path / "b.ris").write_text(RIS_2)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Team meetings", doi="10.1000/abc", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="Not retrieved anywhere", set="Validation")])
    return p


def _table_info(store):
    return {t: [tuple(r) for r in store.query(f"PRAGMA table_info({t})")] for t in store.tables()}


def test_migrate_v5_keeps_rows(tmp_path):
    """Scenario "An existing project opens unchanged": a v4 store with two files and two seed papers opens at schema
    5 with every file included, both papers from the researcher and stamped with the migration time, the Development
    paper marked, every new table empty, the same columns as a new store, and the same listings as before."""
    from wider.core.project import Project
    from wider.core.store import V5_TABLES

    p = _two_files_two_papers(tmp_path)
    root = p.root
    p.store.set_meta("validation_peeked_at", "2026-10-01T10:00:00Z")
    p.close()
    outs = {cmd: _cli(*cmd.split(), "--dir", str(root)) for cmd in ("import list", "benchmark list", "coverage")}
    assert all(o.exit_code == 0 for o in outs.values()), {k: o.output for k, o in outs.items()}
    _as_v4(Project.load(root))
    p = Project.load(root)
    st = p.store
    assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert [r["included"] for r in st.query("SELECT included FROM sources ORDER BY source_id")] == [1, 1]
    p.close()
    after = {cmd: _cli(*cmd.split(), "--dir", str(root)) for cmd in outs}
    # the same listings; coverage adds only D3's line for the look logged before v5, which has no paper list
    legacy = ("Search not validated: validation set looked at on 2026-10-01 before the freeze; the papers shown were "
              "not recorded.\n")
    assert legacy in after["coverage"].output
    after_out = {k: o.output.replace(legacy, "") for k, o in after.items()}
    assert after_out == {k: o.output for k, o in outs.items()}
    p = Project.load(root)
    st = p.store
    rows = {r["benchmark_id"]: dict(r) for r in st.query("SELECT * FROM benchmark_papers")}
    now = st.get_meta("benchmark_csv_v5").removeprefix("done ")
    assert now and rows["B1"]["source"] == rows["B2"]["source"] == "researcher"
    assert rows["B1"]["added_at"] == rows["B2"]["added_at"] == now
    assert rows["B2"]["validation_since"] == now and not rows["B1"]["validation_since"]
    assert (rows["B1"]["was_development"], rows["B2"]["was_development"]) == (1, 0)
    assert rows["B1"]["validation_since_unlock"] == rows["B2"]["validation_since_unlock"] == 0
    new_tables = [ln.split()[5] for ln in V5_TABLES.splitlines() if ln.startswith("CREATE TABLE")]
    assert len(new_tables) == 12 and all(st.scalar(f"SELECT COUNT(*) FROM {t}") == 0 for t in new_tables)
    # the early look logged before v5 is kept under the key that gives the "not recorded" line
    assert st.get_meta("validation_peeked_before_v5") == "2026-10-01T10:00:00Z"
    fresh = Project.init(tmp_path / "fresh", platforms=["scopus"])
    assert _table_info(fresh.store) == _table_info(st)
    assert fresh.store.get_meta("validation_peeked_before_v5") is None


def test_migrate_v5_rewrites_benchmark_csv(tmp_path):
    """benchmark.csv is backed up as benchmark.v4.csv and rewritten with BENCHMARK_HEADER and the migration's values,
    which sync keeps; a csv edited by hand without the new columns loads every paper as seen and once in Development."""
    import csv

    from wider.construct.benchmark import sync
    from wider.core.project import BENCHMARK_HEADER, Project

    p = _two_files_two_papers(tmp_path)
    root = p.root
    _as_v4(p)
    v4_text = (root / "benchmark.csv").read_text(encoding="utf-8")
    p = Project.load(root)
    p.store  # opening migrates the store, then the csv
    assert (root / "benchmark.v4.csv").read_text(encoding="utf-8") == v4_text
    rows = list(csv.reader((root / "benchmark.csv").open(encoding="utf-8")))
    assert rows[0] == BENCHMARK_HEADER
    now = p.store.get_meta("benchmark_csv_v5").removeprefix("done ")
    by_id = {r[0]: dict(zip(BENCHMARK_HEADER, r, strict=True)) for r in rows[1:]}
    assert by_id["B1"]["added_at"] == by_id["B2"]["added_at"] == now
    assert (by_id["B1"]["was_development"], by_id["B2"]["was_development"]) == ("1", "0")
    assert by_id["B2"]["validation_since"] == now and by_id["B1"]["validation_since"] == ""
    assert by_id["B1"]["source"] == "researcher" and by_id["B2"]["validation_since_unlock"] == "0"
    synced = {r["benchmark_id"]: r for r in sync(p)}
    assert synced["B2"]["validation_since"] == now and synced["B1"]["was_development"] == 1
    # opening again changes nothing (the rewrite runs once)
    p.close()
    before = (root / "benchmark.csv").read_bytes()
    Project.load(root).store
    assert (root / "benchmark.csv").read_bytes() == before
    # a file edited by hand with the old header: conservative defaults
    (root / "benchmark.csv").write_text(V4_HEADER + "\nB3,Hand added,,Validation,,,\n", encoding="utf-8")
    (paper,) = Project.load(root).read_benchmark()
    assert (paper.validation_since_unlock, paper.was_development, paper.source) == (0, True, "researcher")


def test_migrate_v5_backfills_rounds(tmp_path):
    """Scenario "An existing project's versions appear as rounds": v1 draft and v2 frozen become rounds 1 and 2,
    round 2 accepted by its freeze, round 1 not accepted, no dry run stored; the frozen version is numbered 1."""
    from tests.unit.test_stage_pages import SYN
    from wider.construct import artifacts
    from wider.core.project import Project

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    artifacts.save_new_version(p, "synonyms", {**SYN, "blocks": [dict(SYN["blocks"][0], terms=["meeting*", "team*"])]},
                               origin="manual", status="frozen")
    root = p.root
    _as_v4(p)
    st = Project.load(root).store
    rounds = {r["round_no"]: dict(r) for r in st.query("SELECT * FROM query_rounds")}
    assert sorted(rounds) == [1, 2]
    assert (rounds[2]["accepted"], rounds[2]["accepted_how"]) == (1, "freeze")
    assert rounds[1]["accepted"] == 0 and rounds[1]["accepted_how"] is None
    created = {r["version"]: r["created_at"] for r in st.query("SELECT version, created_at FROM artifacts")}
    assert all(rounds[v]["created_at"] == created[v] for v in rounds)
    assert st.scalar("SELECT COUNT(*) FROM dry_runs") == 0
    assert [tuple(r) for r in st.query("SELECT version, frozen_no FROM artifacts WHERE kind='synonyms' ORDER BY version")] \
        == [(1, None), (2, 1)]


def test_migrate_v5_keeps_done_stage_with_draft_after_freeze(tmp_path):
    """Scenario "A project with a draft after a freeze": a v4 store with v1 frozen and v2 a draft saved after it opens
    with the query stage done and coverage showing both sets, because v1 is still the frozen query."""
    from tests.unit.test_stage_pages import SYN
    from wider.construct import artifacts
    from wider.core.project import Project
    from wider.core.stages import stage_states
    from wider.search.coverage import validation_unlocked

    p = _two_files_two_papers(tmp_path)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="frozen")
    artifacts.save_new_version(p, "synonyms", {**SYN, "blocks": [dict(SYN["blocks"][0], terms=["meeting*", "team*"])]},
                               origin="manual", seen_choice="count")
    root = p.root
    _as_v4(p)
    p = Project.load(root)
    assert {s["key"]: s["state"] for s in stage_states(p)}["query"] == "done"
    assert validation_unlocked(p)
    p.close()
    out = _cli("coverage", "--dir", str(root))
    assert out.exit_code == 0 and "hidden until" not in out.output and "Validation: 0 of 1" in out.output
    # the save of v3 goes through and records an unlock naming v1, the frozen query (F5)
    p = Project.load(root)
    row = artifacts.save_new_version(p, "synonyms", {**SYN, "blocks": [dict(SYN["blocks"][0], terms=["meeting*", "huddle*"])]},
                                     origin="manual", seen_choice="count")
    assert row["version"] == 3 and row["notice"].startswith("v1 is frozen. Saving v3 reopens the query")
    assert [(u["cause"], u["from_version"], u["new_version"], u["frozen_no"]) for u in
            p.store.query("SELECT * FROM query_unlocks")] == [("save", 1, 3, 1)]
    p.close()


# ----- schema 6 (papers-beyond-the-query, task 1.3) -----

def test_migrate_v6_keeps_candidates(tmp_path):
    """D28: a schema 5 store with two chaining candidates opens at schema 6; both rows keep their values, came from
    OpenAlex in round 1, the new tables are empty, the columns match a new store, and opening again changes nothing."""
    import sqlite3

    from wider.core.project import Project
    from wider.core.store import V6_COLUMNS, V6_TABLES

    p = Project.init(tmp_path / "p", platforms=["scopus"])
    for i, (doi, title) in enumerate([("10.1/a", "Alpha paper"), ("10.1/b", "Beta paper")], 1):
        p.store.execute("INSERT INTO seed_chain_candidates(candidate_id,doi,title,title_norm,year,direction,"
                        "linking_seeds_json,n_linking,status,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (i, doi, title, title.lower(), 2020 + i, "backward", '["B1"]', 1, "new", "2026-10-01"))
    root, path = p.root, p.store_path
    p.close()
    con = sqlite3.connect(path)
    for name in [ln.split()[5] for ln in V6_TABLES.splitlines() if ln.startswith("CREATE TABLE")]:
        con.execute(f"DROP TABLE {name}")
    for table, cols in V6_COLUMNS.items():
        for c in cols:
            con.execute(f"ALTER TABLE {table} DROP COLUMN {c.split()[0]}")
    con.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    con.commit()
    con.close()

    p = Project.load(root)
    st = p.store
    assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
    rows = [dict(r) for r in st.query("SELECT * FROM seed_chain_candidates ORDER BY candidate_id")]
    assert [(r["doi"], r["title"], r["year"], r["direction"], r["n_linking"], r["status"]) for r in rows] == [
        ("10.1/a", "Alpha paper", 2021, "backward", 1, "new"), ("10.1/b", "Beta paper", 2022, "backward", 1, "new")]
    assert all(r["origins_json"] == '["openalex"]' and r["chain_round"] == 1 for r in rows)
    assert st.scalar("SELECT COUNT(*) FROM seed_references") == 0
    assert st.scalar("SELECT COUNT(*) FROM candidate_imports") == 0
    fresh = Project.init(tmp_path / "fresh", platforms=["scopus"])
    assert _table_info(fresh.store) == _table_info(st)
    before = (rows, _table_info(st))
    st._migrate_v6()
    after = ([dict(r) for r in st.query("SELECT * FROM seed_chain_candidates ORDER BY candidate_id")], _table_info(st))
    assert after == before


def test_migrate_v11_adds_seen_choice(tmp_path):
    """unlock-seen-choice D2: a v10 store opens with ``query_unlocks.seen_choice``; its old unlock rows read NULL
    ("not asked") and keep ``counts_as_seen``; running the step again is harmless and the table matches a fresh one."""
    import sqlite3

    from wider.core.project import Project

    p = Project.init(tmp_path / "p", platforms=["scopus"])
    p.store.execute("INSERT INTO query_unlocks(unlock_no,cause,from_version,new_version,at,counts_as_seen,n_shown) "
                    "VALUES(1,'unlock',1,2,'2026-10-01',1,2)")
    root, path = p.root, p.store_path
    p.close()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE query_unlocks DROP COLUMN seen_choice")
    con.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    con.commit()
    con.close()

    p = Project.load(root)
    st = p.store
    assert st.get_meta("schema_version") == str(SCHEMA_VERSION) == "12"
    row = dict(st.one("SELECT * FROM query_unlocks WHERE unlock_no=1"))
    assert (row["seen_choice"], row["counts_as_seen"], row["n_shown"]) == (None, 1, 2)
    fresh = Project.init(tmp_path / "fresh", platforms=["scopus"])
    assert _table_info(fresh.store) == _table_info(st)
    st._migrate_v11()
    assert _table_info(fresh.store) == _table_info(st)
    p.close()
    fresh.close()


def test_comparison_tables_on_any_store(tmp_path):
    """screening-config-comparison D1: the two comparison tables come with every open. A schema-6 store made before
    this change (no comparison tables) opens with them and keeps its rows; opening twice is harmless; a store whose
    version older code wrote back (5) still has and uses them; SCHEMA_VERSION stays 6."""
    import sqlite3

    from wider.core.store import COMPARISON_TABLES

    assert SCHEMA_VERSION >= 7
    path = tmp_path / "store.sqlite"
    s = Store(path)
    s.execute("INSERT INTO meta(key,value) VALUES('probe','kept')")
    s.close()
    con = sqlite3.connect(path)
    for name in [ln.split()[5] for ln in COMPARISON_TABLES.splitlines() if ln.startswith("CREATE TABLE")]:
        con.execute(f"DROP TABLE {name}")
    con.commit()
    con.close()
    s = Store(path)   # made before this change
    assert {"screen_comparisons", "screen_comparison_runs"} <= set(s.tables())
    assert s.get_meta("probe") == "kept" and s.get_meta("schema_version") == str(SCHEMA_VERSION)
    s.execute("INSERT INTO screen_comparisons(name,sample,configs,repeats,created_at) VALUES('cmp1','development','[]',3,'t')")
    s.execute("INSERT INTO screen_comparison_runs VALUES(1,1,1,'run1')")
    s.close()
    s = Store(path)   # opening again
    assert s.scalar("SELECT COUNT(*) FROM screen_comparisons") == 1
    assert s.scalar("SELECT COUNT(*) FROM screen_comparison_runs") == 1
    s.set_meta("schema_version", "5")   # what older code writes back
    s.close()
    s = Store(path)
    assert s.scalar("SELECT name FROM screen_comparisons") == "cmp1" and s.get_meta("probe") == "kept"
    s.execute("INSERT INTO screen_comparisons(name,sample,configs,repeats,created_at) VALUES('cmp2','development','[]',1,'t')")
    assert s.scalar("SELECT COUNT(*) FROM screen_comparisons") == 2
    assert s.get_meta("schema_version") == str(SCHEMA_VERSION)
    s.close()
