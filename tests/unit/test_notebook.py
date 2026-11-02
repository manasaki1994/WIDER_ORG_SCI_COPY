"""The step notebook and a pair's own history (design-pass-a1-dedup D9, D10), on synthetic records only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wider.core.journal import undo
from wider.core.notebook import step_notebook
from wider.core.project import Project
from wider.dedup.engine import decide, match_word, run
from wider.ingest.importer import add_file, import_all

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "match_words.json"


def _r(au, ti, py, jo, doi=None, sp=None, vl=None):
    s = f"\nTY  - JOUR\nAU  - {au}\nTI  - {ti}\nPY  - {py}\nT2  - {jo}\n"
    if vl:
        s += f"VL  - {vl}\nIS  - 1\n"
    if sp:
        s += f"SP  - {sp}\n"
    if doi:
        s += f"DO  - {doi}\n"
    return s + "ER  -\n"


# raw ids: 1-4 from a.ris, 5-8 from b.ris; the run queues (1,5) and (2,6) for the same title, journals differing,
# (3,7) for the same DOI with a year gap of 3, and (4,8) for the same title with different pages
A = (_r("Park, Min", "Night shift handover and patient falls", 2016, "Journal C")
     + _r("Ortiz, Lia", "Another vague heading on a topic", 2017, "Journal E")
     + _r("Chen, Bo", "Checklists in the operating theatre", 2010, "Journal of Safety", "10.1000/d1")
     + _r("Ruiz, Ana", "Peer feedback among junior doctors", 2014, "Journal of Care", sp="5"))
B = (_r("Park, Min", "Night shift handover and patient falls", 2016, "Journal D")
     + _r("Ortiz, Lia", "Another vague heading on a topic", 2017, "Journal F")
     + _r("Chen, Bo", "Checklists in the operating theatre", 2013, "Journal of Safety.", "10.1000/d1")
     + _r("Ruiz, Ana", "Peer feedback among junior doctors", 2014, "Journal of Care.", sp="50"))


def _project(tmp_path, a=A, b=B):
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    (tmp_path / "a.ris").write_text(a)
    (tmp_path / "b.ris").write_text(b)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    return p


def _import_more(p, tmp_path, text, name="c.ris"):
    (tmp_path / name).write_text(text)
    add_file(p, "scopus", tmp_path / name)
    import_all(p)


def _at(p, table, key, value, when):
    """Give one stored row a time of its own: the steps of a test run in the same second."""
    col, idcol = ("ran_at", "result_id") if table == "stage_results" else ("at", "change_id")
    p.store.execute(f"UPDATE {table} SET {col}=? WHERE {idcol}=?", (when, value if key is None else key))


def _last_result_id(p):
    return p.store.scalar("SELECT MAX(result_id) FROM stage_results WHERE stage='dedup'")


def _last_change_id(p):
    return p.store.scalar("SELECT MAX(change_id) FROM history")


def _kept(entries):
    return [e["text"] for e in entries if e["who"] == "WIDER"]


def _two_runs(tmp_path):
    """Run, ana decides (1,5) and (2,6), run again, ana undoes the (2,6) decision; each step a minute apart."""
    p = _project(tmp_path)
    run(p)
    _at(p, "stage_results", None, _last_result_id(p), "2026-10-01T10:00:00+00:00")
    decide(p, 1, 5, "duplicate", by="ana")
    _at(p, "history", None, _last_change_id(p), "2026-10-01T10:01:00+00:00")
    decide(p, 2, 6, "not_duplicate", by="ana")
    _at(p, "history", None, _last_change_id(p), "2026-10-01T10:02:00+00:00")
    run(p)
    _at(p, "stage_results", None, _last_result_id(p), "2026-10-01T10:03:00+00:00")
    p.store.set_meta("dedup_run_at", "2026-10-01T10:03:00+00:00")
    undo(p, _last_change_id(p), author="ana")
    _at(p, "history", None, _last_change_id(p), "2026-10-01T10:04:00+00:00")
    return p


# ----------------------------------------------------------------------------- match words (D9)
def test_match_words_of_the_shared_table():
    for case in json.loads(FIXTURE.read_text(encoding="utf-8")):
        assert match_word(case["a"], case["b"]) == case["word"], case


# ----------------------------------------------------------------------------- the full notebook
def test_runs_and_decisions_in_one_list(tmp_path):
    p = _two_runs(tmp_path)
    nb = step_notebook(p, "dedup")
    assert [(e["kind"], e["who"]) for e in nb] == [("undo", "ana"), ("run", "WIDER"), ("decision", "ana"),
                                                   ("decision", "ana"), ("run", "WIDER")]
    assert nb[0]["text"] == f"Undo: raw records 2 and 6: not duplicate (change {nb[2]['change_id']})"
    assert nb[2]["text"] == "Raw records 2 and 6: Not duplicate" and nb[2]["undone"] is True
    assert nb[3]["text"] == "Raw records 1 and 5: Duplicate" and nb[3]["undone"] is False
    assert nb[1]["text"].startswith("Deduplication ran: ") and "duplicates removed" in nb[1]["text"]
    assert [e["at"][:16] for e in nb] == ["2026-10-01T10:04", "2026-10-01T10:03", "2026-10-01T10:02",
                                          "2026-10-01T10:01", "2026-10-01T10:00"]
    assert all(set(e) >= {"at", "who", "kind", "text", "undone", "change_id", "result_id"} for e in nb)


def test_a_run_entry_carries_its_stored_result(tmp_path):
    p = _two_runs(tmp_path)
    first = step_notebook(p, "dedup")[-1]
    stored = json.loads(p.store.scalar("SELECT result FROM stage_results WHERE result_id=?", (first["result_id"],)))
    assert first["result"]["headline"] == stored["headline"] and first["result"]["metrics"] == stored["metrics"]
    assert first["result"]["outputs"] == ["exports/dedup/"] and first["change_id"] is None


def test_undo_then_redo_leaves_the_decision_in_effect(tmp_path):
    p = _two_runs(tmp_path)
    undo_id = _last_change_id(p)
    undo(p, undo_id, author="ana")   # the redo
    nb = step_notebook(p, "dedup")
    decision = next(e for e in nb if e["text"] == "Raw records 2 and 6: Not duplicate")
    assert decision["undone"] is False
    assert nb[0]["kind"] == "redo" and next(e for e in nb if e["change_id"] == undo_id)["undone"] is True


def test_no_notebook_for_another_step(tmp_path):
    p = _project(tmp_path)
    with pytest.raises(KeyError):
        step_notebook(p, "rank")


# ----------------------------------------------------------------------------- a pair's own history
def test_pair_history_in_either_order_and_without_other_pairs(tmp_path):
    p = _project(tmp_path)
    run(p)
    p.store.set_meta("dedup_run_at", "2026-10-01T10:00:00+00:00")
    decide(p, 3, 7, "uncertain", by="ana")
    _at(p, "history", None, _last_change_id(p), "2026-10-01T10:01:00+00:00")
    decide(p, 2, 6, "duplicate", by="ben")
    one, other = step_notebook(p, "dedup", (3, 7)), step_notebook(p, "dedup", (7, 3))
    assert one == other
    assert [(e["who"], e["text"]) for e in one] == [("ana", "Raw records 3 and 7: Unsure"),
                                                    ("WIDER", "Left for you to decide: same DOI, but the year differs")]
    assert all(e["who"] != "ben" for e in one)


def test_kept_reasons_name_the_first_differing_field(tmp_path):
    p = _project(tmp_path)
    run(p)
    assert _kept(step_notebook(p, "dedup", (1, 5))) == ["Left for you to decide: same title, but the journal differs"]
    # journals differ only in punctuation (spelling), volumes both empty (missing): the year is named
    assert _kept(step_notebook(p, "dedup", (3, 7))) == ["Left for you to decide: same DOI, but the year differs"]
    # no DOI on either record, journals differ in punctuation, volumes empty: the pages are named
    assert _kept(step_notebook(p, "dedup", (4, 8))) == ["Left for you to decide: same title, but the pages differ"]


def test_a_pair_decided_duplicate_and_rerun_shows_both_wider_entries(tmp_path):
    p = _project(tmp_path)
    run(p)
    decide(p, 1, 5, "duplicate", by="ana")
    run(p)
    texts = _kept(step_notebook(p, "dedup", (1, 5)))
    assert sorted(texts) == ["Left for you to decide: same title, but the journal differs", "Removed by your decision"]


def test_a_pair_a_linking_import_took_out_of_the_queue_is_not_kept(tmp_path):
    ti_p = "Effects of team meetings on error reporting in hospitals"
    ti_q = "Effects of team meeting on error reporting in hospital"
    p = _project(tmp_path, a=_r("Lee, Kim", ti_p, 2015, "Journal X", vl="3", sp="5"),
                 b=_r("Lee, Kim", ti_q, 2015, "Journal Y", "10.1000/q"))
    run(p)
    assert _kept(step_notebook(p, "dedup", (1, 2)))[0].startswith("Left for you to decide: similar title")
    decide(p, 1, 2, "not_duplicate", by="ana")
    run(p)
    # the same title as 1 with its full citation and the DOI of 2: both merge automatically with the new record
    _import_more(p, tmp_path, _r("Lee, Kim", ti_p, 2015, "Journal X", "10.1000/q", vl="3", sp="5"))
    run(p)
    hist = step_notebook(p, "dedup", (1, 2))
    assert not any(t.startswith("Left for you to decide") for t in _kept(hist))
    assert [e["who"] for e in hist if e["kind"] == "decision"] == ["ana"]


def test_pair_history_of_a_run_made_before_the_queue_was_kept(tmp_path):
    p = _project(tmp_path)
    run(p)
    p.store.execute("DELETE FROM meta WHERE key='dedup_run_queue'")
    assert _kept(step_notebook(p, "dedup", (1, 5))) == ["Left for you to decide: same title, but the journal differs"]


# ----------------------------------------------------------------------------- route and CLI twins (task 5.2)
def _cli(p, *args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    res = CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)])
    return res.exit_code, res.output


def _client(p):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    return TestClient(create_app(p))


def test_route_and_cli_give_the_same_entries(tmp_path):
    from wider.core.notebook import entry_line

    p = _two_runs(tmp_path)
    c = _client(p)
    page = c.get("/api/notebook/dedup").json()
    code, out = _cli(p, "notebook", "dedup", "--json")
    assert code == 0 and json.loads(out) == page == json.loads(json.dumps(step_notebook(p, "dedup"), default=str))
    assert len(page) == 5 and page[-1]["result"]["headline"]
    code, out = _cli(p, "notebook", "dedup")
    assert code == 0 and out.splitlines() == [entry_line(e) for e in page]
    assert "(undone)" in out.splitlines()[2] and out.splitlines()[0].split("  ")[1] == "ana"
    pair = c.get("/api/notebook/dedup", params={"a": 6, "b": 2}).json()
    code, out = _cli(p, "notebook", "dedup", "--pair", "6", "2", "--json")
    assert code == 0 and json.loads(out) == pair and [e["kind"] for e in pair] == ["undo", "run", "decision"]
    code, out = _cli(p, "notebook", "dedup", "--limit", "2")
    assert code == 0 and len(out.splitlines()) == 2
    assert len(c.get("/api/notebook/dedup", params={"limit": 2}).json()) == 2


def test_no_notebook_route_for_another_step_and_a_pair_needs_both_ids(tmp_path):
    p = _project(tmp_path)
    c = _client(p)
    assert c.get("/api/notebook/rank").status_code == 404
    assert c.get("/api/notebook/dedup", params={"a": 1}).status_code == 400


def test_undo_after_a_plain_rerun(tmp_path):
    """A rerun with no new import writes the same row: the undo goes through, from the page and from the CLI."""
    p = _project(tmp_path)
    run(p)
    decide(p, 1, 5, "duplicate", by="ana")
    first = _last_change_id(p)
    decide(p, 2, 6, "not_duplicate", by="ana")
    second = _last_change_id(p)
    run(p)
    r = _client(p).post(f"/api/history/{first}/undo", headers={"x-wider-author": "ana"})
    assert r.status_code == 200 and r.json()["undo_of"] == first
    code, out = _cli(p, "history", "undo", str(second), "--author", "ana")
    assert code == 0, out
    nb = {e["change_id"]: e for e in step_notebook(p, "dedup")}
    assert nb[first]["undone"] and nb[second]["undone"]
    assert p.store.scalar("SELECT decision FROM dedup_pairs WHERE raw_a=1 AND raw_b=5") == "queued"


def test_undo_refused_after_the_pair_changed(tmp_path):
    """ana decides a pair with a record imported since the run; the next run rewrites that pair's row (its stage
    and diagnostics), so the journal refuses the undo, in the same words from the page and the CLI."""
    p = _project(tmp_path)
    run(p)
    _import_more(p, tmp_path, _r("Park, Min", "Night shift handover and patient falls", 2016, "Journal G"))
    decide(p, 1, 9, "not_duplicate", by="ana")
    cid = _last_change_id(p)
    run(p)
    row = dict(p.store.one("SELECT * FROM dedup_pairs WHERE raw_a=1 AND raw_b=9"))
    assert row["stage"] == "exact_normalized_title"   # the rerun rewrote the row the decision stored
    n_changes = p.store.scalar("SELECT COUNT(*) FROM history")
    r = _client(p).post(f"/api/history/{cid}/undo", headers={"x-wider-author": "ana"})
    code, out = _cli(p, "history", "undo", str(cid), "--author", "ana")
    assert r.status_code == 409 and code == 1
    assert "dedup_pairs changed since" in r.json()["detail"] and r.json()["detail"] in " ".join(out.split())
    assert dict(p.store.one("SELECT * FROM dedup_pairs WHERE raw_a=1 AND raw_b=9")) == row
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n_changes
    assert next(e for e in step_notebook(p, "dedup") if e["change_id"] == cid)["undone"] is False
