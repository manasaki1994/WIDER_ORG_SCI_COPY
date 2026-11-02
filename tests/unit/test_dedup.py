import json

import pytest

from wider.core.project import Project
from wider.dedup.diagnostics import (
    Rec,
    auto_duplicate_rule,
    auto_not_duplicate_rule,
    diagnose,
    jaro_winkler,
    token_cosine,
)
from wider.dedup.engine import decide, import_decisions_csv, prisma_rows, queue_rows, run
from wider.ingest.importer import add_file, import_all


def rec(rid, title, doi=None, author="doe", year=2020, journal="journal of things", vol="1", issue="2", page="10",
        fam="journal_article", f="a.ris", dbid=None, plat="scopus"):
    return Rec(rid, title, doi, author, year, journal, vol, issue, page, None, fam, f, dbid, plat)


def test_similarities():
    assert jaro_winkler("abc", "abc") == 1.0
    assert 0.9 < jaro_winkler("team meetings", "team meeting") < 1.0
    assert token_cosine("a b c", "a b d") > 0.6 and token_cosine("", "x") == 0.0


def test_rules_exact_title_and_fuzzy():
    a, b = rec(1, "team meetings after errors"), rec(2, "team meetings after errors")
    d = diagnose(a, b, "exact_normalized_title")
    assert d["full_citation_match"] and auto_duplicate_rule(d) == "exact_title_full_citation"
    b2 = rec(2, "team meetings after errors", doi="10.1/x", author="roe", journal="other", vol="9", page="99", year=2015)
    d2 = diagnose(rec(1, "team meetings after errors", doi="10.1/y"), b2, "exact_normalized_title")
    assert auto_duplicate_rule(d2) is None
    assert auto_not_duplicate_rule(d2) == "publication_years_over_one_apart_with_citation_conflicts"
    d3 = diagnose(rec(1, "team meetings after errors"), rec(2, "team meeting after error", doi=None), "fuzzy_title")
    assert d3["title_similarity"] >= 0.97 and auto_duplicate_rule(d3) == "high_similarity_full_citation"
    d3b = diagnose(rec(1, "team meetings after errors"), rec(2, "team meeting after error", page="11"), "fuzzy_title")
    assert not d3b["full_citation_match"] and auto_duplicate_rule(d3b) == "fuzzy_title_same_year_two_locators"
    d4 = diagnose(rec(1, "team meetings after errors"), rec(2, "team meetings after errors", fam="conference"), "exact_normalized_title")
    assert d4["publication_family_conflict"] and auto_duplicate_rule(d4) is None


RIS_A = """TY  - JOUR
AU  - Doe, Jane
TI  - Team meetings after errors
PY  - 2020
T2  - Journal of Things
VL  - 1
IS  - 2
SP  - 10
EP  - 20
DO  - 10.1000/abc
AB  - Abstract one with enough words to count as an abstract here ok ok ok ok ok ok ok ok ok ok.
ER  -

TY  - JOUR
AU  - Smith, Ann
TI  - Unique paper one
PY  - 2018
ER  -

TY  - JOUR
AU  - Who, Guess
TI  - A rather ambiguous title about something
PY  - 2019
T2  - Journal A
ER  -
"""
RIS_B = """TY  - JOUR
AU  - Doe, J.
TI  - Team Meetings After Errors.
PY  - 2020
JF  - Journal of Things
VL  - 1
IS  - 2
SP  - 10
AB  - Longer abstract from the second platform with many more words than the first one has so it wins the field.
ER  -

TY  - JOUR
AU  - Else, Someone
TI  - A rather ambiguous title about something
PY  - 2019
T2  - Journal B
ER  -

TY  - JOUR
AU  - Doe, Jane
TI  - Team meetings after errors
PY  - 2020
DO  - https://doi.org/10.1000/ABC
ER  -
"""


def test_engine_end_to_end(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    (tmp_path / "a.ris").write_text(RIS_A)
    (tmp_path / "b.ris").write_text(RIS_B)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    res = run(p)
    s = res["summary"]
    assert s["total_records_imported"] == 6
    # the three "blame" records collapse (DOI + exact title full citation); the ambiguous pair goes to the queue
    assert s["unique_records"] == 4 and s["unresolved_manual_component_pairs_retained"] == 1
    q = queue_rows(p)
    assert len(q) == 1 and q[0]["stage"] == "exact_normalized_title"
    master = p.store.one("SELECT * FROM records WHERE doi='10.1000/abc'")
    assert master["n_sources"] == 3 and master["abstract"].startswith("Longer abstract") and master["platforms"] == "scopus;wos"
    decide(p, q[0]["raw_a"], q[0]["raw_b"], "duplicate", by="tester")
    s2 = run(p)["summary"]
    assert s2["unique_records"] == 3 and s2["unresolved_manual_component_pairs_retained"] == 0
    assert s2["manually_confirmed_duplicate_pairs"] == 1
    rows = prisma_rows(p)
    assert rows[-1][0] == "Records remaining for title and abstract screening" and rows[-1][1] == 3
    assert (p.path("exports", "dedup", "05_manual_duplicate_review.csv")).exists()
    # decisions CSV round-trip (this tool's export format, read as written)
    csv_path = p.path("exports", "dedup", "05_manual_duplicate_review.csv")
    text = csv_path.read_text().replace(",duplicate\n", ",not_duplicate\n")
    csv_path.write_text(text)
    assert import_decisions_csv(p, csv_path)["not_duplicate"] == 1
    assert run(p)["summary"]["unique_records"] == 4


def _project_with_imports(tmp_path, extra_a="", extra_b=""):
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    (tmp_path / "a.ris").write_text(RIS_A + extra_a)
    (tmp_path / "b.ris").write_text(RIS_B + extra_b)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    return p


# a second same-title pair from different journals, so two pairs wait in the queue
RIS_A2 = "\nTY  - JOUR\nAU  - First, Author\nTI  - Another vague heading on a topic\nPY  - 2017\nT2  - Journal C\nER  -\n"
RIS_B2 = "\nTY  - JOUR\nAU  - Second, Author\nTI  - Another vague heading on a topic\nPY  - 2017\nT2  - Journal D\nER  -\n"


def _review_csv(p, decisions: dict):
    """Fill the exported review file: {title_a: decision} (a missing title stays blank)."""
    import csv

    path = p.path("exports", "dedup", "05_manual_duplicate_review.csv")
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["manual_decision"] = decisions.get(r["title_a"], "")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def test_import_own_review_file_round_trip(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = _project_with_imports(tmp_path, RIS_A2, RIS_B2)
    assert run(p)["summary"]["unresolved_manual_component_pairs_retained"] == 2
    path = _review_csv(p, {"A rather ambiguous title about something": "duplicate"})
    assert import_decisions_csv(p, path) == {"duplicate": 1, "blank": 1}
    s = run(p)["summary"]
    assert s["manually_confirmed_duplicate_pairs"] == 1 and s["unresolved_manual_component_pairs_retained"] == 1
    assert len(queue_rows(p)) == 1 and queue_rows(p)[0]["title_a"] == "Another vague heading on a topic"

    # an unknown header is refused before anything is stored, naming the expected columns
    bad = tmp_path / "bad.csv"
    bad.write_text("first,second,verdict\n1,2,duplicate\n")
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    with pytest.raises(ValueError, match="raw_a, raw_b and manual_decision"):
        import_decisions_csv(p, bad)
    res = CliRunner().invoke(cli_app, ["dedup", "import-decisions", str(bad), "--dir", str(p.root)])
    assert res.exit_code == 1 and "manual_decision" in res.output
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n

    # a row whose raw ids exist here but belong to other titles (another project's file) is not applied
    q = queue_rows(p)[0]
    other = tmp_path / "other.csv"
    other.write_text("raw_a,raw_b,title_a,title_b,platform_a,platform_b,manual_decision\n"
                     f"{q['raw_a']},{q['raw_b']},Some unrelated title,Another unrelated title,scopus,wos,duplicate\n")
    assert import_decisions_csv(p, other) == {"unmatched": 1}
    assert len(queue_rows(p)) == 1


def test_decide_validates_before_recording(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    p = _project_with_imports(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    before = (p.store.scalar("SELECT COUNT(*) FROM history"), p.store.query("SELECT raw_a, raw_b, decision FROM dedup_pairs ORDER BY raw_a, raw_b"))
    with pytest.raises(ValueError, match=r"decision must be duplicate, not_duplicate or unsure$"):
        decide(p, q["raw_a"], q["raw_b"], "dupe")
    with pytest.raises(ValueError, match="raw record 9999 does not exist"):
        decide(p, q["raw_a"], 9999, "duplicate")
    with pytest.raises(ValueError, match="duplicate of itself"):
        decide(p, q["raw_a"], q["raw_a"], "duplicate")
    cli = CliRunner()
    assert cli.invoke(cli_app, ["dedup", "decide", str(q["raw_a"]), str(q["raw_a"]), "duplicate", "--dir", str(p.root)]).exit_code == 1
    bad = cli.invoke(cli_app, ["dedup", "decide", str(q["raw_a"]), str(q["raw_b"]), "duplicat", "--dir", str(p.root)])
    assert bad.exit_code == 1 and "decision must be duplicate, not_duplicate or unsure" in " ".join(bad.output.split())
    assert "Traceback" not in bad.output
    c = TestClient(create_app(p))
    r = c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": 9999, "decision": "duplicate"})
    assert r.status_code == 400 and "raw record 9999" in r.json()["detail"]
    r = c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "duplicat", "by": "ana"})
    assert r.status_code == 400 and "decision must be duplicate" in r.json()["detail"]
    after = (p.store.scalar("SELECT COUNT(*) FROM history"), p.store.query("SELECT raw_a, raw_b, decision FROM dedup_pairs ORDER BY raw_a, raw_b"))
    assert [dict(x) for x in after[1]] == [dict(x) for x in before[1]] and after[0] == before[0]
    # the dedup page counts the whole queue once
    d = c.get("/api/dedup").json()
    assert d["pending"] == len(queue_rows(p)) == len(d["queue"]) == 1


def test_prisma_remaining_is_provisional_while_pairs_are_open(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.stage_summaries import summarize_dedup

    p = _project_with_imports(tmp_path)
    out = run(p)
    labels = [r[0] for r in prisma_rows(p)]
    assert labels[-1] == "Provisional records remaining after automated deduplication"
    assert "Total records before deduplication" in labels and "Duplicate records removed before screening" in labels
    assert labels.index("Total records before deduplication") > 0   # the rows per platform come first
    assert any("provisional" in w for w in summarize_dedup(out).warnings)
    q = queue_rows(p)[0]
    decide(p, q["raw_a"], q["raw_b"], "uncertain", by="tester")
    out = run(p)
    assert out["summary"]["unresolved_manual_component_pairs_retained"] == 0 and out["summary"]["uncertain_manual_component_pairs"] == 1
    assert prisma_rows(p)[-1][0] == "Provisional records remaining after automated deduplication"
    assert "Provisional records remaining" in p.path("exports", "dedup", "09_prisma_counts.csv").read_text()
    assert any("uncertain" in w for w in summarize_dedup(out).warnings) and any("provisional" in w for w in summarize_dedup(out).warnings)
    res = CliRunner().invoke(cli_app, ["dedup", "prisma", "--dir", str(p.root)])
    assert res.exit_code == 0 and "Provisional" in res.output
    decide(p, q["raw_a"], q["raw_b"], "not_duplicate", by="tester")
    out = run(p)
    assert prisma_rows(p)[-1] == ["Records remaining for title and abstract screening", 4]
    assert "Provisional" not in p.path("exports", "dedup", "09_prisma_counts.csv").read_text()
    assert not any("provisional" in w for w in summarize_dedup(out).warnings)


def test_rerun_keeps_record_ids_and_remaps_merged_masters(tmp_path):
    from wider.screen.decisions import decisions, set_decision

    p = _project_with_imports(tmp_path)
    first = run(p)["summary"]
    assert first["unique_records_new"] == 4 and first["unique_records_kept_from_previous_run"] == 0
    before = {r["cluster_id"]: (r["record_id"], r["key"]) for r in p.store.query("SELECT record_id, cluster_id, key FROM records")}
    master = p.store.one("SELECT record_id FROM records WHERE doi='10.1000/abc'")["record_id"]
    set_decision(p, "main", master, "include")
    p.store.execute("INSERT INTO enrichment_log(record_id,source,status,fetched_at) VALUES(?,?,?,?)", (master, "x", "miss", "t"))

    # plain re-run: same ids and keys, the decision still attached, counts say nothing changed
    s2 = run(p)["summary"]
    assert s2["unique_records"] == 4 and s2["unique_records_kept_from_previous_run"] == 4 and s2["unique_records_new"] == 0
    after = {r["cluster_id"]: (r["record_id"], r["key"]) for r in p.store.query("SELECT record_id, cluster_id, key FROM records")}
    assert after == before
    assert decisions(p, "main") == {master: "include"}

    # the two "ambiguous title" masters merge after a manual decision: the decision on the losing master moves
    q = queue_rows(p)[0]
    a_rec = p.store.one("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id WHERE c.raw_id=?", (q["raw_a"],))["record_id"]
    b_rec = p.store.one("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id WHERE c.raw_id=?", (q["raw_b"],))["record_id"]
    assert a_rec != b_rec
    set_decision(p, "main", b_rec, "exclude")
    p.store.execute("INSERT INTO enrichment_log(record_id,source,status,fetched_at) VALUES(?,?,?,?)", (b_rec, "y", "miss", "t"))
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    s3 = run(p)["summary"]
    assert s3["unique_records"] == 3 and s3["unique_records_merged_into_existing"] == 1 and s3["unique_records_kept_from_previous_run"] == 3
    assert p.store.scalar("SELECT COUNT(*) FROM records WHERE record_id=?", (b_rec,)) == 0
    assert decisions(p, "main") == {master: "include", a_rec: "exclude"}
    assert p.store.scalar("SELECT record_id FROM enrichment_log WHERE source='x'") == master
    assert p.store.scalar("SELECT record_id FROM enrichment_log WHERE source='y'") == a_rec   # the loser's log moves too

    # splitting them again (not_duplicate) keeps the surviving id and gives the split-off cluster a fresh id
    decide(p, q["raw_a"], q["raw_b"], "not_duplicate", by="tester")
    s4 = run(p)["summary"]
    assert s4["unique_records"] == 4 and s4["unique_records_new"] == 1
    ids = {r["record_id"] for r in p.store.query("SELECT record_id FROM records")}
    assert a_rec in ids and master in ids and b_rec not in ids
    assert decisions(p, "main") == {master: "include", a_rec: "exclude"}

    # both masters screened under the same protocol: the merge goes ahead, the survivor's decision is kept and the
    # other's is set aside (kept on record, named in History, never undone by History)
    new_rec = max(ids)
    assert new_rec not in {i for i, _ in after.values()}
    set_decision(p, "main", new_rec, "include")
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    res = run(p)
    assert p.store.scalar("SELECT COUNT(*) FROM records") == 3
    assert decisions(p, "main") == {master: "include", a_rec: "exclude"}
    aside = p.store.query("SELECT from_table, record_id, kept_record_id, row FROM merge_set_aside")
    assert [(r["from_table"], r["record_id"], r["kept_record_id"]) for r in aside] == [("screen_decisions", new_rec, a_rec)]
    assert json.loads(aside[0]["row"])["decision"] == "include"
    [line] = res["set_aside"]
    assert f"record {a_rec}'s screening is kept" in line and f"In main the two had different screening decisions; record {a_rec}'s is kept." in line
    assert "include" not in line and "exclude" not in line   # no decision named: one may be a hidden model decision
    assert "screen_" not in line
    h = p.store.one("SELECT kind, summary FROM history WHERE kind='dedup_set_aside'")
    assert h["summary"] == line


def test_merge_of_two_screened_records_sets_the_others_calls_aside(tmp_path):
    """Owner bug 1 (1 Nov 2026): 'Duplicates, keep A' on two records screened in the same run blocked every later
    deduplication run. Now the kept record's calls and decision stay, the other's are set aside, nothing is lost."""
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.screen.decisions import decisions, set_decision

    p = _project_with_imports(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    rec = lambda raw: p.store.scalar("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id "  # noqa: E731
                                     "WHERE c.raw_id=?", (raw,))
    a, b = rec(q["raw_a"]), rec(q["raw_b"])
    p.store.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) VALUES('r1','screen','s','h','done','t','d')")
    for r, d in ((a, "include"), (b, "exclude")):
        p.store.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,raw_decision,at) "
                        "VALUES('r1',?,'A','x',1,?,'t')", (r, d))
        set_decision(p, "main", r, d)
    b_call = p.store.scalar("SELECT call_id FROM screen_calls WHERE record_id=?", (b,))
    p.store.execute("INSERT INTO screen_call_reuse(run_id,record_id,pass,source_call_id,reused_at) VALUES('r2',?,'A',?,'t')",
                    (b, b_call))
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester", keep=q["raw_a"])
    p.close()
    out = CliRunner().invoke(app, ["dedup", "run", "--dir", str(tmp_path / "r")])
    assert out.exit_code == 0, out.output
    assert "screening is kept" in out.output and "screen_calls" not in out.output
    p = Project.find(tmp_path / "r")
    survivor = rec(q["raw_a"])
    assert decisions(p, "main")[survivor] == ("include" if survivor == a else "exclude")
    assert p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE record_id NOT IN (SELECT record_id FROM records)") == 0
    assert p.store.scalar("SELECT COUNT(*) FROM screen_call_reuse WHERE record_id NOT IN (SELECT record_id FROM records)") == 0
    tables = sorted(r["from_table"] for r in p.store.query("SELECT from_table FROM merge_set_aside"))
    assert tables == ["screen_call_reuse", "screen_calls", "screen_decisions"]
    from wider.core import journal
    ch = p.store.one("SELECT * FROM history WHERE kind='dedup_set_aside'")
    assert ch and journal.KIND_LABELS["dedup_set_aside"]
    with pytest.raises(Exception, match="stay on record"):
        journal.undo(p, ch["change_id"], author="tester")
    # a second run changes nothing more
    assert run(p)["set_aside"] == []


def test_labels_clash_is_refused_in_words(tmp_path):
    from wider.dedup.engine import DedupRerunError

    p = _project_with_imports(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    rec = lambda raw: p.store.scalar("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id "  # noqa: E731
                                     "WHERE c.raw_id=?", (raw,))
    p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(1,'dev','purposive','x',2,'t')")
    for i, r in enumerate((rec(q["raw_a"]), rec(q["raw_b"]))):
        p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,?,?)", (r, i))
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    with pytest.raises(DedupRerunError) as refused:
        run(p)
    msg = str(refused.value)
    assert "sample places in the same sample" in msg and "Not duplicates" in msg and "_rows" not in msg


def test_rerun_keeps_enriched_abstract(tmp_path):
    p = _project_with_imports(tmp_path)
    run(p)
    rid = p.store.one("SELECT record_id FROM records WHERE title='Unique paper one'")["record_id"]
    p.store.execute("UPDATE records SET abstract=?, abstract_words=12, thin=0, abstract_source='openalex' WHERE record_id=?",
                    ("An abstract fetched later from a lookup service with twelve words in it ok.", rid))
    run(p)
    r = p.store.one("SELECT abstract_source, abstract_words, thin, record_id FROM records WHERE title='Unique paper one'")
    assert r["record_id"] == rid and r["abstract_source"] == "openalex" and r["abstract_words"] == 12
    assert r["thin"] == int(12 < p.config.thresholds.thin_words)


def test_a_merge_after_ranking_keeps_the_survivors_ranking_row(tmp_path):
    p = _project_with_imports(tmp_path)
    run(p)
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM records ORDER BY record_id")]
    p.store.execute("INSERT INTO rankings(ranking_id,name,set_name,anchors_version,top_n,n,md5,created_at) "
                    "VALUES(1,'r1','s',1,2,?,'m','t')", (len(ids),))
    for i, rid in enumerate(ids, 1):
        p.store.execute("INSERT INTO ranking_rows(ranking_id,record_id,rank,score) VALUES(1,?,?,?)", (rid, i, 1.0 / i))
    q = queue_rows(p)[0]
    master_of = "SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id WHERE c.raw_id=?"
    a_rec, b_rec = (p.store.one(master_of, (q[k],))["record_id"] for k in ("raw_a", "raw_b"))
    survivor_rank = p.store.scalar("SELECT rank FROM ranking_rows WHERE record_id=?", (a_rec,))
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    assert run(p)["summary"]["unique_records"] == len(ids) - 1   # no "both already have rows in ranking_rows" refusal
    rows = {r["record_id"]: r["rank"] for r in p.store.query("SELECT record_id, rank FROM ranking_rows WHERE ranking_id=1")}
    assert b_rec not in rows and rows[a_rec] == survivor_rank and len(rows) == len(ids) - 1


# ----------------------------------------------------------------------------- the run's own queue (design-pass-a1-dedup D6)
def _ris(au, ti, py, jo, doi=None, vl=None):
    cite = f"VL  - {vl}\nIS  - 1\nSP  - 5\n" if vl else ""
    return f"\nTY  - JOUR\nAU  - {au}\nTI  - {ti}\nPY  - {py}\nT2  - {jo}\n{cite}" + (f"DO  - {doi}\n" if doi else "") + "ER  -\n"


TITLE_P = "Effects of team meetings on error reporting in hospitals"
TITLE_Q = "Effects of team meeting on error reporting in hospital"


def _fuzzy_project(tmp_path, vl=None):
    """RIS_A/RIS_B plus a fuzzy-title pair (raw records 4 and 8) that waits in the queue."""
    return _project_with_imports(tmp_path, extra_a=_ris("Lee, Kim", TITLE_P, 2015, "Journal X", vl=vl),
                                 extra_b=_ris("Lee, Kim", TITLE_Q, 2015, "Journal Y", "10.1000/q"))


def _import_more(p, tmp_path, text, name="c.ris"):
    (tmp_path / name).write_text(text)
    add_file(p, "scopus", tmp_path / name)
    import_all(p)


def _entry(p, a, b):
    from wider.dedup.engine import last_queue
    return next((e for e in last_queue(p) if (e["a"], e["b"]) == (a, b)), None)


def test_run_stores_its_queue_and_merges(tmp_path):
    from wider.dedup.engine import last_queue, run_queue

    p = _fuzzy_project(tmp_path)
    s = run(p)["summary"]
    v = run_queue(p)
    assert {(e["a"], e["b"]) for e in v["queue"]} == {(3, 6), (4, 8)}
    assert all(e["decision"] is None and e["by"] is None and e["source"] is None for e in v["queue"])
    assert _entry(p, 4, 8)["reason"] == "fuzzy_title"
    assert len(v["merged"]) == s["duplicate_records_removed"] and all(e["by"] == "WIDER" for e in v["merged"])
    assert all(e["at"] == p.store.get_meta("dedup_run_at") for e in v["merged"])
    decide(p, 4, 8, "duplicate", by="ana")
    s = run(p)["summary"]
    e = _entry(p, 4, 8)
    assert e["decision"] == "manual_same" and e["by"] == "ana" and e["source"] is None and e["at"]
    manual = [m for m in run_queue(p)["merged"] if m["by"] != "WIDER"]
    assert manual == [{"a": 4, "b": 8, "match_method": "fuzzy_title_manual_confirmed", "source": None, "by": "ana", "at": e["at"]}]
    assert len(run_queue(p)["merged"]) == s["duplicate_records_removed"]
    assert last_queue(p) == run_queue(p)["queue"]


def test_decide_takes_unsure(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = _project_with_imports(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    cli = CliRunner()
    r = cli.invoke(cli_app, ["dedup", "decide", str(q["raw_a"]), str(q["raw_b"]), "unsure", "--by", "ana", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    row = p.store.one("SELECT decision, decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (q["raw_a"], q["raw_b"]))
    assert (row["decision"], row["decided_by"]) == ("manual_uncertain", "ana")
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="ana")
    decide(p, q["raw_a"], q["raw_b"], "uncertain", by="ana")   # the older word stores the same value
    r = cli.invoke(cli_app, ["dedup", "decide", str(q["raw_a"]), str(q["raw_b"]), "uncertain", "--by", "ana", "--dir", str(p.root)])
    assert r.exit_code == 0 and f"recorded unsure for {q['raw_a']}/{q['raw_b']}" in r.output, r.output
    row = p.store.one("SELECT decision, decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (q["raw_a"], q["raw_b"]))
    assert (row["decision"], row["decided_by"]) == ("manual_uncertain", "ana")
    helptext = " ".join(cli.invoke(cli_app, ["dedup", "decide", "--help"], env={"COLUMNS": "200"}).output.split())
    assert "duplicate | not_duplicate | unsure" in helptext


def test_stale_decided_pair_is_not_in_the_last_queue(tmp_path):
    """A pair decided by hand keeps its "queue" flag after an import links its two records automatically; the run's
    own list leaves it out, the flag (the fallback for older runs) does not."""
    from wider.dedup.engine import last_queue

    p = _fuzzy_project(tmp_path, vl="3")
    run(p)
    decide(p, 4, 8, "not_duplicate", by="ana")
    run(p)
    # the same title as 4 with its full citation and the DOI of 8: both merge automatically with the new record
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal X", "10.1000/q", vl="3"))
    run(p)
    import json
    row = p.store.one("SELECT decision, diagnostics FROM dedup_pairs WHERE raw_a=4 AND raw_b=8")
    assert row["decision"] == "manual_different" and json.loads(row["diagnostics"]).get("queue") is True   # stale flag
    assert _entry(p, 4, 8) is None and {(e["a"], e["b"]) for e in last_queue(p)} == {(3, 6)}
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_queue'")   # a run made before WIDER kept the list
    assert (4, 8) in {(e["a"], e["b"]) for e in last_queue(p)}


def test_copied_decision_keeps_its_signer(tmp_path):
    """ana decides 4 and 8; an import makes 4 and 9 the representative, which gets ana's decision as a copy. Its
    signer stays named after two more runs; an undo of ana's source decision is then refused and changes nothing."""
    from wider.core.journal import changes, undo
    from wider.dedup.engine import run_queue

    p = _fuzzy_project(tmp_path)
    run(p)
    decide(p, 4, 8, "duplicate", by="ana")
    run(p)
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal Y", "10.1000/q"))
    run(p)
    e = _entry(p, 4, 9)
    assert e["decision"] == "manual_same" and e["source"] == [4, 8] and e["by"] == "ana" and e["at"]
    assert p.store.one("SELECT decided_by FROM dedup_pairs WHERE raw_a=4 AND raw_b=9")["decided_by"] is None
    for _ in range(2):
        run(p)
        assert _entry(p, 4, 9) == e
    assert any(m["source"] == [4, 8] and m["by"] == "ana" for m in run_queue(p)["merged"])
    ch = changes(p, kind="dedup_decision")[0]
    before = (p.store.query("SELECT * FROM dedup_pairs ORDER BY raw_a, raw_b"), p.store.get_meta("dedup_run_queue"))
    with pytest.raises(ValueError, match="dedup_pairs changed since"):
        undo(p, ch["change_id"], other=True)
    assert (p.store.query("SELECT * FROM dedup_pairs ORDER BY raw_a, raw_b"), p.store.get_meta("dedup_run_queue")) == before


def test_several_rows_of_one_cluster_pair_take_the_signer_of_the_applied_decision(tmp_path):
    """Two stored rows bridge the same two clusters with different decisions and the applied one has no signer: the
    decision applied is the one the run picks today; the signer comes only from a row with that decision."""
    p = _fuzzy_project(tmp_path)
    run(p)
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal Y", "10.1000/q"))
    run(p)   # 8 and 9 merge (DOI); the representative of {4} and {8, 9} is 4 and 9
    assert _entry(p, 4, 9) is not None
    # 4-8 holds an unsigned Duplicate (as a copy leaves it), 4-7 is not a candidate pair: ben's Not duplicate
    p.store.execute("UPDATE dedup_pairs SET decision='manual_same', decided_by=NULL, decided_at='2026-10-01T00:00:00Z' "
                    "WHERE raw_a=4 AND raw_b=8")
    run(p)
    e = _entry(p, 4, 9)
    assert e["decision"] == "manual_same" and e["source"] == [4, 8] and e["by"] is None   # signer not recorded
    decide(p, 4, 9, "duplicate", by="ana")   # not a bridge any more once decided on the representative itself
    p.store.execute("DELETE FROM dedup_pairs WHERE raw_a=4 AND raw_b=9")
    p.store.execute("UPDATE meta SET value='{}' WHERE key='dedup_run_queue'")
    # now a signed row with the other decision and an unsigned one with the decision the run applies
    p.store.execute("INSERT INTO dedup_pairs(raw_a,raw_b,stage,rule,score,diagnostics,decision,decided_by,decided_at) "
                    "VALUES(4,9,'manual','',NULL,'{}','manual_different','ben','2026-10-02T00:00:00Z')")
    p.store.execute("UPDATE dedup_pairs SET decision='manual_same' WHERE raw_a=4 AND raw_b=8")
    run(p)
    e = _entry(p, 4, 9)
    # the representative's own row applies (today's rule), so ben signs his own Not duplicate
    assert e["decision"] == "manual_different" and e["by"] == "ben" and e["source"] is None


def test_refused_run_leaves_the_run_queue_unchanged(tmp_path):
    from wider.dedup.engine import DedupRerunError

    p = _project_with_imports(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    rec = lambda raw: p.store.one("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id "  # noqa: E731
                                  "WHERE c.raw_id=?", (raw,))["record_id"]
    p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(1,'dev','purposive','x',2,'t')")
    for i, raw in enumerate((q["raw_a"], q["raw_b"])):   # both in one sample: a clash a person must settle
        p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,?,?)", (rec(raw), i))
    before = p.store.get_meta("dedup_run_queue")
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="ana")
    with pytest.raises(DedupRerunError):
        run(p)
    assert p.store.get_meta("dedup_run_queue") == before


# ----------------------------------------------------------------------------- flow chart, merged pairs, records left (D5-D7)
SAME = ["Shared leadership in volunteer fire brigades", "Rota fairness and nurse turnover", "Feedback timing in call centres",
        "Onboarding rituals in software firms", "Peer mentoring among junior lawyers"]
FUZZY = [("Effects of team meetings on error reporting in hospitals", "Effects of team meeting on error reporting in hospital"),
         ("Night shifts and the quality of handover notes", "Night shift and the quality of handover note"),
         ("Supervisor praise and the reporting of small mistakes", "Supervisor praises and the reporting of small mistake")]
SOLO = ["Cooperative bakeries and member voting", "Seasonal staffing in ski resorts", "Library volunteers and task choice",
        "Bus drivers and route planning meetings"]
OPEN = [(6, 18), (7, 19), (8, 20)]   # the three queued pairs of _twenty()


def _twenty(tmp_path):
    """20 synthetic records: 5 DOI duplicates WIDER removes on its own, 3 fuzzy-title pairs it queues, 4 alone."""
    a = "".join(_ris("Smith, Ann", s, 2018, "Journal M", f"10.1000/s{i}") for i, s in enumerate(SAME))
    a += "".join(_ris("Lee, Kim", x, 2015, "Journal X") for x, _ in FUZZY)
    a += "".join(_ris(f"Solo, {i}", s, 2011, "Journal S") for i, s in enumerate(SOLO))
    b = "".join(_ris("Smith, A.", s, 2018, "Journal M", f"10.1000/s{i}") for i, s in enumerate(SAME))
    b += "".join(_ris("Lee, Kim", y, 2015, "Journal Y", f"10.1000/q{i}") for i, (_, y) in enumerate(FUZZY))
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    (tmp_path / "a.ris").write_text(a)
    (tmp_path / "b.ris").write_text(b)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    return p


def _prisma_removed(p):
    from wider.dedup.engine import prisma_rows
    return dict((k, v) for k, v in prisma_rows(p))["Duplicate records removed before screening"]


def test_reason_words():
    from wider.dedup.diagnostics import METHOD_PRIORITY
    from wider.dedup.engine import REASON_WORDS, reason_words
    assert reason_words("exact_doi") == "Same DOI" and reason_words("exact_normalized_title") == "Same title"
    assert reason_words("fuzzy_title") == "Similar title"
    assert set(METHOD_PRIORITY) <= set(REASON_WORDS)   # every merge method has words


def test_flow_after_a_run_with_open_pairs(tmp_path):
    from wider.dedup.engine import flow_counts, merged_pairs, unique_records

    p = _twenty(tmp_path)
    before = flow_counts(p)
    assert before["has_run"] is False and before["imported"] == 20 and before["removed"] is None and before["left"] is None
    assert before["message"] == "No deduplication run yet. Press Run deduplication to find the duplicates."
    s = run(p)["summary"]
    f = flow_counts(p)
    assert (f["imported"], f["removed"], f["removed_auto"], f["removed_manual"], f["left"], f["to_decide"]) == (20, 5, 5, 0, 15, 3)
    assert f["provisional"] is True and f["split_recorded"] is True and f["files"] == 2 and f["imported_since_run"] == 0
    assert f["message"] == "3 pairs need your decision. The record count stays provisional until every pair is decided."
    auto = sum(v for k, v in s.items() if k.startswith("duplicate_records_removed_by_") and not k.endswith("_manual_confirmed"))
    assert auto == f["removed_auto"]
    m = merged_pairs(p)
    assert m["approximate"] is False and m["total"] == 5 == s["duplicate_records_removed"]
    assert all(r["reason"] == "Same DOI" and r["text"].startswith("Removed by WIDER on ") and r["text"].endswith(": Same DOI")
               for r in m["rows"])
    assert len(unique_records(p)) == s["unique_records"] == 15
    assert len(merged_pairs(p, limit=2)["rows"]) == 2 and merged_pairs(p, limit=2)["total"] == 5


def test_merge_by_your_decision_is_listed_with_the_run_time_signer(tmp_path):
    from wider.core.journal import changes, undo
    from wider.dedup.engine import flow_counts, merged_pairs, unique_records

    p = _twenty(tmp_path)
    run(p)
    decide(p, 6, 18, "duplicate", by="ana")
    s = run(p)["summary"]
    f = flow_counts(p)
    assert (f["removed"], f["removed_auto"], f["removed_manual"], f["left"]) == (6, 5, 1, 14)
    assert f["removed_auto"] + f["removed_manual"] == _prisma_removed(p) == s["duplicate_records_removed"]
    rows = merged_pairs(p)["rows"]
    mine = [r for r in rows if r["manual"]]
    assert len(rows) == 6 and len(mine) == 1 and (mine[0]["a"], mine[0]["b"]) == (6, 18)
    assert mine[0]["text"] == f"Removed by your decision (ana, {mine[0]['at'][:10]})" and mine[0]["by"] == "ana"
    one = [r for r in unique_records(p) if r["n_raw"] == 2]
    assert len(one) == 6 and len(unique_records(p)) == 14
    # after the run: a pair decided now is not listed; changing or undoing ana's decision changes nothing until a rerun
    decide(p, 7, 19, "duplicate", by="ben")
    decide(p, 6, 18, "not_duplicate", by="ben")
    rows2 = merged_pairs(p)["rows"]
    assert rows2 == rows
    undo(p, changes(p, kind="dedup_decision")[0]["change_id"], other=True)   # ben's Not duplicate
    undo(p, changes(p, kind="dedup_decision")[2]["change_id"], other=True)   # ana's Duplicate
    assert merged_pairs(p)["rows"] == rows


def test_transitive_manual_triple_lists_two_rows(tmp_path):
    from wider.dedup.engine import merged_pairs

    t = "Coaching circles and the reporting of near misses"
    extra_a = _ris("Lee, Kim", t, 2015, "Journal X")
    extra_b = _ris("Lee, Kim", t.replace("circles", "circle"), 2015, "Journal Y", "10.1000/t1") + \
        _ris("Lee, Kim", t.replace("misses", "miss"), 2015, "Journal Z", "10.1000/t2")
    p = _project_with_imports(tmp_path, extra_a, extra_b)
    run(p)
    trio = sorted({(q["raw_a"], q["raw_b"]) for q in queue_rows(p)} - {(3, 6)})
    assert len(trio) == 3, trio
    for a, b in trio:   # every pair of the three, the third one closing the triangle
        decide(p, a, b, "duplicate", by="ana")
    s = run(p)["summary"]
    rows = merged_pairs(p)["rows"]
    assert len([r for r in rows if r["manual"]]) == 2 and len(rows) == s["duplicate_records_removed"]


def test_not_duplicate_by_csv_then_merged_automatically_is_removed_by_wider(tmp_path):
    from wider.dedup.engine import merged_pairs

    p = _twenty(tmp_path)
    run(p)
    f = tmp_path / "decisions.csv"
    f.write_text("raw_a,raw_b,manual_decision\n1,13,not_duplicate\n")   # a pair WIDER merges by its DOI
    assert import_decisions_csv(p, f) == {"not_duplicate": 1}
    s = run(p)["summary"]
    rows = merged_pairs(p)["rows"]
    r = next(r for r in rows if (r["a"], r["b"]) == (1, 13))
    assert r["by"] == "WIDER" and r["text"].startswith("Removed by WIDER") and len(rows) == s["duplicate_records_removed"] == 5


def test_merged_pairs_of_an_older_run_are_replayed(tmp_path):
    from wider.dedup.engine import merged_pairs

    p = _twenty(tmp_path)
    run(p)
    decide(p, 6, 18, "duplicate", by="ana")
    run(p)
    exact = merged_pairs(p)
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_queue'")
    old = merged_pairs(p)
    assert old["approximate"] is True and exact["approximate"] is False
    key = lambda r: (r["a"], r["b"], r["match_method"], r["by"])   # noqa: E731
    assert sorted(map(key, old["rows"])) == sorted(map(key, exact["rows"]))


def test_copied_duplicate_is_listed_as_a_copy(tmp_path):
    from wider.dedup.engine import merged_pairs

    p = _fuzzy_project(tmp_path)
    run(p)
    decide(p, 4, 8, "duplicate", by="ana")
    run(p)
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal Y", "10.1000/q"))
    run(p)
    r = next(r for r in merged_pairs(p)["rows"] if r["manual"])
    assert (r["a"], r["b"]) == (4, 9)
    assert r["text"] == f"Removed by your decision (copied from raw records 4 and 8, ana, {r['at'][:10]})"


def test_flow_messages(tmp_path):
    from wider.dedup.engine import flow_counts, flow_message

    p = _twenty(tmp_path)
    run(p)
    for a, b in OPEN:
        decide(p, a, b, "not_duplicate", by="ana")
    assert flow_counts(p)["message"] == "Every pair is decided. Press Apply decisions and check again to update the counts."
    run(p)
    assert flow_counts(p)["message"] == "Deduplication is done: 15 records left."
    _import_more(p, tmp_path, _ris("New, One", "Garden allotments and neighbour trust", 2020, "Journal G")
                 + _ris("New, Two", "Ferry crews and weather briefings", 2021, "Journal F"))
    f = flow_counts(p)
    assert (f["imported"], f["imported_since_run"]) == (20, 2)
    assert f["message"] == "2 records were imported since the last run. Press Apply decisions and check again to include them."
    run(p)
    decide(p, 6, 18, "uncertain", by="ana")
    run(p)
    f = flow_counts(p)
    assert (f["to_decide"], f["decided"], f["unsure"], f["provisional"]) == (0, 3, 1, True)
    assert f["message"] == f"{f['left']} records left, provisional while 1 pair is Unsure."
    base = dict(f, unsure=2)
    assert flow_message(base).endswith("provisional while 2 pairs are Unsure.")
    assert flow_message(dict(f, to_decide=1)).startswith("1 pair needs your decision.")
    assert flow_message(dict(f, provisional=False, imported_since_run=1)).startswith("1 record was imported")


def test_flow_of_a_summary_without_the_split(tmp_path):
    import json

    from wider.dedup.engine import flow_counts, removed_line

    p = _twenty(tmp_path)
    s = run(p)["summary"]
    assert removed_line(flow_counts(p)) == "5 duplicates removed (5 by WIDER, 0 by your decisions)"
    old = {k: v for k, v in s.items() if not k.startswith("duplicate_records_removed_by_")}
    p.store.set_meta("dedup_summary", json.dumps(old))
    f = flow_counts(p)
    assert f["split_recorded"] is False and f["removed"] == 5 and f["removed_auto"] is None
    assert removed_line(f) == "5 duplicates removed (split not recorded)"


def test_flow_of_a_run_that_removed_nothing_reads_zero_by_each(tmp_path):
    """A run with no duplicates stores no per-method counts; the box must not say "split not recorded" (M43)."""
    import json

    from wider.dedup.engine import flow_counts, removed_line

    p = _twenty(tmp_path)
    s = run(p)["summary"]
    zero = {**{k: v for k, v in s.items() if not k.startswith("duplicate_records_removed_by_")}, "duplicate_records_removed": 0}
    p.store.set_meta("dedup_summary", json.dumps(zero))
    f = flow_counts(p)
    assert f["split_recorded"] is True and f["removed_auto"] == 0 and f["removed_manual"] == 0
    assert removed_line(f) == "0 duplicates removed (0 by WIDER, 0 by your decisions)"


# ----------------------------------------------------------------------------- CLI twins (D7)
def _cli(p, *args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    res = CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)])
    return res.exit_code, res.output


def test_cli_queue_all_prisma_merged_and_records(tmp_path):
    import json

    from wider.dedup.engine import merged_pairs, unique_records

    p = _twenty(tmp_path)
    run(p)
    code, out = _cli(p, "dedup", "queue")
    assert code == 0 and out.splitlines()[0] == "3 pairs need your decision. The record count stays provisional until every pair is decided."
    decide(p, 6, 18, "duplicate", by="ana")
    decide(p, 7, 19, "uncertain", by="ben")
    code, out = _cli(p, "dedup", "queue", "--all")
    lines = out.splitlines()
    assert code == 0 and lines[1] == "1 open, 2 decided"
    assert lines[2].startswith("6 18  Duplicate (ana)  (Similar title)") and lines[3].startswith("7 19  Unsure (ben)")
    assert lines[4].startswith("8 20  Open") and len(lines) == 5   # --all is every pair, with no cut
    code, out = _cli(p, "dedup", "queue", "--all", "--limit", "1")
    assert out.splitlines()[-1] == "... 2 more pairs not shown (--limit 1)"   # a cut list says so
    code, out = _cli(p, "dedup", "queue", "--limit", "0")   # without --all: the one open pair
    assert "... 1 more pair not shown (--limit 0, or --all for every pair)" in out
    run(p)   # a pair decided before the last run and queued again by it keeps its decision
    code, out = _cli(p, "dedup", "queue", "--all")
    assert "6 18  Duplicate (ana)" in out and "7 19  Unsure (ben)" in out and "8 20  Open" in out
    code, out = _cli(p, "dedup", "prisma")
    assert code == 0 and "6 duplicates removed (5 by WIDER, 1 by your decisions)" in out and "imported since" not in out
    code, out = _cli(p, "dedup", "merged")
    rows = merged_pairs(p)["rows"]
    assert code == 0 and out.splitlines()[0] == "6 merged pairs" and "approximate" not in out
    assert [ln.split("  ")[1] for ln in out.splitlines()[1:]] == [r["text"] for r in rows]
    assert any(ln.startswith("6 18  Removed by your decision (ana, ") for ln in out.splitlines())
    code, out = _cli(p, "dedup", "merged", "--json", "--limit", "2")
    got = json.loads(out)
    assert got["total"] == 6 and len(got["rows"]) == 2 and got["approximate"] is False
    code, out = _cli(p, "dedup", "records")
    recs = unique_records(p)
    assert code == 0 and out.splitlines()[0] == "14 records left" and len(out.splitlines()) == 1 + len(recs) == 15
    assert sum("from 2 imported records" in ln for ln in out.splitlines()) == 6
    assert sum(ln.endswith("from 1 imported record") for ln in out.splitlines()) == 8
    code, out = _cli(p, "dedup", "records", "--json", "--limit", "3")
    assert json.loads(out)["total"] == 14 and [r["record_id"] for r in json.loads(out)["rows"]] == [r["record_id"] for r in recs[:3]]
    _import_more(p, tmp_path, _ris("New, One", "Garden allotments and neighbour trust", 2020, "Journal G")
                 + _ris("New, Two", "Ferry crews and weather briefings", 2021, "Journal F"))
    code, out = _cli(p, "dedup", "prisma")
    assert "+2 more imported since, not checked for duplicates yet" in out


def test_cli_merged_of_an_older_run_and_summary_without_split(tmp_path):
    import json

    p = _twenty(tmp_path)
    s = run(p)["summary"]
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_queue'")
    code, out = _cli(p, "dedup", "merged")
    assert code == 0 and out.splitlines()[0] == ("(approximate: this run was made before WIDER kept its list; run "
                                                 "deduplication again for an exact list)")
    assert json.loads(_cli(p, "dedup", "merged", "--json")[1])["approximate"] is True
    p.store.set_meta("dedup_summary", json.dumps({k: v for k, v in s.items() if not k.startswith("duplicate_records_removed_by_")}))
    assert "5 duplicates removed (split not recorded)" in _cli(p, "dedup", "prisma")[1]


def test_cli_queue_all_shows_a_copy_and_undo_of_its_source_is_refused(tmp_path):
    from wider.core.journal import changes

    p = _fuzzy_project(tmp_path)
    run(p)
    decide(p, 4, 8, "duplicate", by="ana")
    run(p)
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal Y", "10.1000/q"))
    run(p)
    code, out = _cli(p, "dedup", "queue", "--all")
    assert code == 0 and any(ln.startswith("4 9  Duplicate (copied from raw records 4 and 8, ana)") for ln in out.splitlines())
    before = p.store.query("SELECT * FROM dedup_pairs ORDER BY raw_a, raw_b")
    ch = changes(p, kind="dedup_decision")[0]
    code, out = _cli(p, "history", "undo", str(ch["change_id"]))
    assert code == 1 and "dedup_pairs changed since; undo the later change first" in " ".join(out.split())
    assert p.store.query("SELECT * FROM dedup_pairs ORDER BY raw_a, raw_b") == before


def test_decide_summary_format_is_pinned(tmp_path):
    """The pair history (D10) finds a pair's decisions by this summary; the format must not drift."""
    from wider.core.journal import changes

    p = _project_with_imports(tmp_path)
    run(p)
    decide(p, 6, 3, "not_duplicate", by="ana")
    assert changes(p, kind="dedup_decision")[0]["summary"] == "raw records 3 and 6: not duplicate"


# ----------------------------------------------------------------------------- routes (D6, D7)
def _client(p):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    return TestClient(create_app(p))


def test_route_flow_matches_prisma_and_the_lists_match_the_functions(tmp_path):
    from wider.dedup.engine import is_provisional, last_queue, merged_pairs, unique_records

    p = _twenty(tmp_path)
    c = _client(p)
    d = c.get("/api/dedup").json()
    assert d["flow"]["has_run"] is False and d["flow"]["imported"] == 20 and d["queue"] == []
    run(p)
    decide(p, 6, 18, "duplicate", by="ana")
    run(p)
    decide(p, 7, 19, "uncertain", by="ben")
    run(p)
    _import_more(p, tmp_path, _ris("New, One", "Garden allotments and neighbour trust", 2020, "Journal G")
                 + _ris("New, Two", "Ferry crews and weather briefings", 2021, "Journal F"))
    d = c.get("/api/dedup").json()
    f, prisma = d["flow"], dict((k, v) for k, v in d["prisma"])
    assert f["imported"] == prisma["Total records before deduplication"] == 20 and f["imported_since_run"] == 2
    assert f["removed"] == prisma["Duplicate records removed before screening"] == f["removed_auto"] + f["removed_manual"] == 6
    assert f["left"] == prisma["Provisional records remaining after automated deduplication"] == 14
    assert f["provisional"] is is_provisional(d["summary"]) is True and f["unsure"] == 1 and f["to_decide"] == 1
    assert f["removed_line"] == "6 duplicates removed (5 by WIDER, 1 by your decisions)"
    assert [(r["raw_a"], r["raw_b"]) for r in d["queue"]] == [(e["a"], e["b"]) for e in last_queue(p)] == OPEN
    assert [r["text"] for r in d["queue"]] == ["Duplicate (ana)", "Unsure (ben)", "Open"] and d["pending"] == 1
    m = c.get("/api/dedup/merged?limit=3").json()
    assert m == merged_pairs(p, limit=3) and m["total"] == 6 and len(m["rows"]) == 3
    r = c.get("/api/dedup/records?limit=500").json()
    assert r["rows"] == unique_records(p, limit=500) and r["total"] == 14 == len(r["rows"])


def test_route_queue_leaves_out_a_stale_decided_pair(tmp_path):
    p = _fuzzy_project(tmp_path, vl="3")
    run(p)
    decide(p, 4, 8, "not_duplicate", by="ana")
    run(p)
    _import_more(p, tmp_path, _ris("Lee, Kim", TITLE_P, 2015, "Journal X", "10.1000/q", vl="3"))
    run(p)
    c = _client(p)
    assert [(r["raw_a"], r["raw_b"]) for r in c.get("/api/dedup").json()["queue"]] == [(3, 6)]
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_queue'")   # an older run: the flag is used
    assert (4, 8) in [(r["raw_a"], r["raw_b"]) for r in c.get("/api/dedup").json()["queue"]]


def test_decide_keep_names_the_record_the_merged_record_shows(tmp_path):
    """"Duplicates, keep A/B" (P8): the kept raw record's title, journal and year stand for the merged record after
    the run, the record id stays, a re-run keeps the choice, and the CLI twin takes --keep A|B."""
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = _twenty(tmp_path)
    run(p)
    q = queue_rows(p)[0]
    a, b = q["raw_a"], q["raw_b"]
    with pytest.raises(ValueError, match="only be named for a duplicate"):
        decide(p, a, b, "not_duplicate", by="ana", keep=a)
    with pytest.raises(ValueError, match="must be raw record"):
        decide(p, a, b, "duplicate", by="ana", keep=999)
    for kept in (a, b):
        decide(p, a, b, "duplicate", by="ana", keep=kept)
        run(p)
        raw = p.store.one("SELECT title, journal, year FROM raw_records WHERE raw_id=?", (kept,))
        rec_ = p.store.one("SELECT r.record_id, r.title, r.journal, r.year FROM records r JOIN dedup_clusters c "
                           "ON c.cluster_id=r.cluster_id WHERE c.raw_id=?", (kept,))
        assert (rec_["title"], rec_["journal"], rec_["year"]) == (raw["title"], raw["journal"], raw["year"])
        if kept == a:
            first_id = rec_["record_id"]
        else:
            assert rec_["record_id"] == first_id   # the record id follows the anchor, not the kept record
    run(p)   # a re-run keeps the choice in the pair row
    assert p.store.one("SELECT json_extract(diagnostics,'$.keep') k FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b))["k"] == b
    assert [x for x in queue_rows(p, pending_only=False) if (x["raw_a"], x["raw_b"]) == (a, b)][0]["diagnostics"]["keep"] == b
    cli = CliRunner()
    r = cli.invoke(cli_app, ["dedup", "decide", str(a), str(b), "duplicate", "--keep", "A", "--by", "ana", "--dir", str(p.root)])
    assert r.exit_code == 0 and f"keeping raw record {a}" in r.output, r.output
    from wider.dedup.engine import decided_since_run
    assert decided_since_run(p) == 1   # a changed kept record counts as a new decision until the run
    r = cli.invoke(cli_app, ["dedup", "decide", str(a), str(b), "duplicate", "--keep", "C", "--dir", str(p.root)])
    assert r.exit_code == 1 and "--keep must be A or B" in r.output
    decide(p, a, b, "not_duplicate", by="ana")   # another decision drops the kept record
    assert p.store.one("SELECT json_extract(diagnostics,'$.keep') k FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b))["k"] is None
    from wider.core.journal import changes
    assert any(c["summary"] == f"raw records {a} and {b}: duplicate, keep raw record {a}" for c in changes(p, kind="dedup_decision"))
    c = _client(p)
    assert c.post("/api/dedup/decide", json={"raw_a": a, "raw_b": b, "decision": "duplicate", "by": "ana", "keep": b}).status_code == 200
    assert p.store.one("SELECT json_extract(diagnostics,'$.keep') k FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b))["k"] == b
