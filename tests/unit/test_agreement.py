"""Agreement between a result set's passes and the route of each decision (results-dashboard-and-ris, design D2).
Synthetic, topic-neutral records only; decisions are written directly or by the fake backend."""
import json

import pytest

from tests.unit.test_result_sets import DEV, _two_scopes_v3, counted, reload  # noqa: F401 - pytest fixture
from tests.unit.test_screen import project as _project_fixture  # noqa: F401 - pytest fixture
from wider.core.project import Project
from wider.screen import agreement as ag
from wider.screen import result_sets as rs
from wider.screen.decisions import set_decision
from wider.screen.protocol import run_protocol


def rules(passes=2, majority=2, third=True, kind="screen"):
    return {"kind": kind, "rules": {"passes": passes, "majority": majority, "blind_third_read": third}}


@pytest.fixture
def proj(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    for i in range(1, 41):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    return p


def fill(p, key, rows, settings=None, start=1, protocol="main"):
    """Write ``rows`` of (final, votes, decided_by[, origin]) as set ``key``; returns the record ids used."""
    if rs.row_by_key(p, key) is None:
        rs.new_set(p, protocol, "second" if settings and settings.get("kind") == "second_screen" else "screening",
                   settings=settings)
    ids = []
    for i, row in enumerate(rows, start):
        final, votes, by, *origin = row
        set_decision(p, key, i, final, votes=votes, decided_by=by, origin=origin[0] if origin else None)
        ids.append(i)
    return ids


def routes(a):
    return {r["route"]: r["count"] for r in a["routes"]}


SYNTHETIC = ([("exclude", ["exclude", "exclude"], "A,B")] * 4 + [("include", ["include", "include"], "A,B")] * 2
             + [("exclude", ["include", "exclude", "exclude"], "B,C")] * 2
             + [("exclude", ["maybe", "exclude", "exclude"], "B,C"),
                ("include", ["include", "maybe", "exclude", "researcher:include"], "researcher"),
                ("include", None, "imported from list.csv"), ("exclude", None, "imported from list.csv")])


def test_table_percent_kappa_routes(proj):
    fill(proj, "main", SYNTHETIC, rules())
    a = ag.pass_agreement(proj, "main")
    t = a["table"]
    assert (t["exclude"]["exclude"], t["include"]["include"], t["include"]["exclude"], t["maybe"]["exclude"],
            t["include"]["maybe"]) == (4, 2, 2, 1, 1)
    assert a["n"] == 10 and a["agreement"] == "6 of 10 (60%)" and a["kappa_text"] == "0.34" and a["kappa"] == 0.34
    assert a["columns"] == ["pass A", "pass B"] and a["sentence"] is None
    assert routes(a) == {"agreed": 6, "third_read": 3, "split": 0, "researcher": 1, "import": 2, "carried": 0,
                         "not_recorded": 0, "one_pass": 0, "other": 0}
    labels = {r["route"]: r["label"] for r in a["routes"]}
    assert labels["third_read"] == "settled by the third read" and labels["split"] == "no majority (left at maybe)"
    md = "\n".join(ag.markdown(a))
    assert "6 of 10 (60%)" in md and "0.34" in md and "both passes agreed: 6" in md and "imported: 2" in md


def test_kappa_not_defined(proj):
    fill(proj, "main", [("exclude", ["exclude", "exclude"], "A,B")] * 5, rules())
    a = ag.pass_agreement(proj, "main")
    assert a["agreement"] == "5 of 5 (100%)" and a["kappa"] is None
    assert a["kappa_reason"] == "kappa not defined: both passes gave only one decision"
    assert "; kappa not defined: both passes gave only one decision" in "\n".join(ag.markdown(a))
    assert "Cohen's kappa: kappa" not in "\n".join(ag.markdown(a))   # ui-tester: said once
    fill(proj, "other", [("include", None, "imported from list.csv")] * 3, rules(), protocol="other")
    b = ag.pass_agreement(proj, "other")
    assert b["n"] == 0 and b["agreement"] is None and b["sentence"] == "No record was read by both passes."
    assert routes(b)["import"] == 3


def test_second_screen_reads(proj):
    fill(proj, "main", [("include", ["include", "include"], "A,B")] * 8 + [("maybe", ["maybe", "exclude"], "A")] * 4,
         rules())
    rs.new_set(proj, "main", "second", settings=rules(kind="second_screen"))
    carried = [("include", ["include", "include"], "carried from main")] * 8
    reread = [("include", ["include", "include"], "S1,S2"), ("include", ["include", "exclude", "include"], "S1,S3"),
              ("maybe", ["maybe", "maybe"], "S1,S2"), ("maybe", ["exclude", "maybe", "maybe"], "S2,S3")]
    fill(proj, "main#2", carried + reread)
    a = ag.pass_agreement(proj, "main#2")
    assert a["second_screen"] and a["columns"] == ["read S1", "read S2"]
    assert a["n"] == 4 and a["agreement"] == "2 of 4 (50%)"
    assert a["table"]["include"]["exclude"] == 1 and a["table"]["exclude"]["maybe"] == 1
    r = routes(a)
    assert (r["carried"], r["agreed"], r["third_read"]) == (8, 2, 2)
    assert "read S1 \\ read S2" in "\n".join(ag.markdown(a))


def test_copied_and_older_decisions(proj):
    fill(proj, "main", [("include", ["include", "maybe", "include"], "A,C")] * 5, rules())
    rs.new_set(proj, "main", "screening", settings=rules())
    assert rs.copy_reused(proj, {"reuse": {"key": "main"}}, "main#2", [1, 2, 3, 4, 5]) == 5
    fill(proj, "main#2", [("exclude", ["include", "exclude"], None)] * 3, start=6)              # older rows
    fill(proj, "main#2", [("exclude", ["exclude", "exclude"], "A,B", {"kind": "migrated", "set": 1})] * 4, start=9)
    a = ag.pass_agreement(proj, "main#2")
    r = routes(a)
    assert (r["carried"], r["not_recorded"], r["agreed"]) == (5, 3, 4)
    assert a["n"] == 4 and a["table"]["exclude"]["exclude"] == 4 and a["table"]["include"]["maybe"] == 0
    assert sum(r.values()) == 12


def test_third_read_off_and_majority_three(proj):
    fill(proj, "main", [("maybe", ["include", "maybe"], "B")], rules(third=False))
    fill(proj, "strict", [("maybe", ["maybe", "include", "maybe"], "A,C")], rules(majority=3), protocol="strict")
    assert routes(ag.pass_agreement(proj, "main"))["split"] == 1
    assert routes(ag.pass_agreement(proj, "strict"))["split"] == 1
    assert ag.decision_routes(proj, "main") == {1: "split"} and ag.decision_routes(proj, "strict") == {1: "split"}


def test_routes_use_set_rules(proj):
    fill(proj, "main", [("maybe", ["maybe", "include", "maybe"], "A,C")], rules(majority=2))
    assert routes(ag.pass_agreement(proj, "main"))["third_read"] == 1
    proj.config.protocol.screening.majority = 3
    proj.save()
    p = Project.find(proj.root)
    assert routes(ag.pass_agreement(p, "main"))["third_read"] == 1      # the rules the set was screened with
    fill(p, "bare", [("maybe", ["maybe", "include", "maybe"], "A,C")], protocol="bare")
    assert routes(ag.pass_agreement(p, "bare"))["split"] == 1           # no settings: the protocol's current rule
    fill(p, "sec", [("maybe", ["include", "include"], "split")], rules(majority=3, kind="second_screen"), protocol="sec")
    a = ag.pass_agreement(p, "sec")
    assert routes(a)["split"] == 1 and a["columns"] == ["read S1", "read S2"]


def test_one_pass(proj):
    fill(proj, "main", [("include", ["include"], "A")] * 3 + [("exclude", ["exclude"], "A")] * 2, rules(passes=1))
    a = ag.pass_agreement(proj, "main")
    assert a["one_pass"] and a["sentence"] == "This set was screened with one pass; there is no agreement to report."
    assert routes(a)["one_pass"] == 5 and a["agreement"] is None
    assert ag.markdown(a)[0] == a["sentence"]


def test_unplanned_route_counted_as_other(proj):
    fill(proj, "main", [("include", ["include", "exclude"], "A,B"), ("exclude", ["exclude", "exclude"], "A,B")], rules())
    a = ag.pass_agreement(proj, "main")
    r = routes(a)
    assert r["other"] == 1 and r["agreed"] == 1 and sum(r.values()) == 2
    other = next(x for x in a["routes"] if x["route"] == "other")
    assert other["values"] == {"A,B": 1}
    assert "other: 1 (A,B 1)" in ag.route_lines(a)


def _passes_in_order(p, key):
    n = 0
    for r in p.store.query("SELECT votes, vote_calls FROM screen_decisions WHERE protocol=? AND vote_calls IS NOT NULL",
                           (key,)):
        calls = json.loads(r["vote_calls"])
        got = [p.store.one("SELECT pass, rule_decision FROM screen_calls WHERE call_id=?", (c,)) for c in calls]
        assert [g["pass"] for g in got] == ["A", "B", "C"][:len(got)]
        assert ag.reads(json.loads(r["votes"])) == [g["rule_decision"] for g in got]
        n += 1
    return n


def test_votes_in_pass_order(counted):  # noqa: F811
    p, counter = counted
    counter["flip"] = True
    run_protocol(p, DEV, "fresh")
    assert _passes_in_order(p, "fresh") > 0
    _two_scopes_v3(p)
    p = reload(p)
    migrated = [r["key"] for r in p.store.query("SELECT key FROM screen_result_sets WHERE source='migrated'")]
    assert migrated
    for key in migrated:
        assert _passes_in_order(p, key) > 0
