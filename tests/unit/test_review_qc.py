"""Quality-control draws of records the LLM settled (manual-review D11). Synthetic, topic-neutral records."""
import json

import pytest
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.screen import result_sets as rs
from wider.screen import review as rv
from wider.screen.decisions import set_decision

RULES = {"kind": "screen", "rules": {"passes": 2, "majority": 2, "blind_third_read": True}}


@pytest.fixture
def proj(tmp_path):
    """40 records the LLM settled (both passes agreed), 6 of them in the development sample and 2 decided by a
    researcher."""
    p = Project.init(tmp_path / "q", platforms=["scopus"])
    rs.new_set(p, "main", "screening", settings=RULES)
    p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(1,'dev','purposive','{}',6,'x')")
    for i in range(1, 41):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
        dec = "include" if i % 4 == 0 else "exclude"
        if i in (39, 40):
            set_decision(p, "main", i, "include", votes=[dec, dec, "researcher:include"], decided_by="researcher",
                         reason="researcher (ana) decided after screening")
        else:
            set_decision(p, "main", i, dec, votes=[dec, dec], decided_by="A,B")
    for i in range(1, 7):
        p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,?,?)", (i, i))
    return p


def cli(p, *args):
    return CliRunner().invoke(cli_app, ["review", *args, "--dir", str(p.root)])


def test_qc_draw_seeded_and_leaves_out_sampled_and_decided(proj):
    t = rv._target(proj, None, None)
    pool = rv.qc_pool(proj, t)
    assert pool == list(range(7, 39)) and len(pool) == 32
    out = cli(proj, "qc", "draw", "--n", "5", "--seed", "7", "--by", "ana", "--json")
    assert out.exit_code == 0, out.output
    d = json.loads(out.output)
    assert len(d["records"]) == 5 and set(d["records"]) <= set(pool) and d["pool_n"] == 32
    import random
    assert d["records"] == sorted(random.Random(7).sample(pool, 5))   # the same 5 for the same seed and set
    rows = rv.flagged(proj, show="qc")["rows"]
    assert [r["record_id"] for r in rows] == d["records"]
    assert all(r["why"] == f"quality-control sample: the AI model settled it as {r['decision']}" and not r["decided"]
               for r in rows)
    assert rv.flagged(proj, show="qc")["undecided"] == 5
    h = proj.store.query("SELECT * FROM history WHERE kind='review_qc_draw'")
    assert len(h) == 1 and h[0]["author"] == "ana" and h[0]["summary"] == "quality-control sample of 5 records (seed 7)"
    dr = rv.qc_draws(proj, "main")[0]
    assert dr["pool_n"] == 32 and dr["pool_sha256"] == rv.pool_sha256(pool)
    lst = cli(proj, "list", "--show", "qc")
    assert rv.QC_SENTENCE in lst.output
    # a second draw leaves out the first one's records
    assert not set(rv.qc_pool(proj, t)) & set(d["records"])


def test_qc_draw_undo_and_refusal(proj):
    d = rv.qc_draw(proj, None, None, 5, 7, "ana")
    journal.undo(proj, d["change_id"], author="ana")
    assert rv.flagged(proj, show="qc")["rows"] == []
    d = rv.qc_draw(proj, None, None, 5, 7, "ana")
    v = rv.decide(proj, None, None, d["records"][0], "include", "bo")
    why = "a record of this quality-control sample has a review decision; undo that decision first"
    before = proj.store.scalar("SELECT COUNT(*) FROM review_qc")
    with pytest.raises(ValueError, match=why):
        journal.undo(proj, d["change_id"], author="ana")
    assert proj.store.scalar("SELECT COUNT(*) FROM review_qc") == before == 5
    # `wider undo` (undo_last) stops at the draw instead of stepping over it to an older change of ana's
    with pytest.raises(ValueError, match=why):
        journal.undo_last(proj, "ana")
    assert proj.store.scalar("SELECT COUNT(*) FROM review_qc") == 5
    journal.undo(proj, v["change_id"], author="bo")
    journal.undo(proj, d["change_id"], author="ana")   # the decision undone: the draw's undo succeeds
    assert rv.flagged(proj, show="qc")["rows"] == []


def test_qc_draw_refusals(proj):
    for args, msg in ((["--n", "0", "--by", "ana"], "the sample size must be at least 1"),
                      (["--n", "50", "--by", "ana"], "only 32 settled records can be drawn")):
        out = cli(proj, "qc", "draw", *args)
        assert out.exit_code != 0 and msg in out.output
    with pytest.raises(rv.Refusal, match="a reviewer name is needed"):
        rv.qc_draw(proj, None, None, 5, 7, "")
    assert proj.store.scalar("SELECT COUNT(*) FROM review_qc") == 0


def test_qc_sample_in_report_and_methods(proj):
    from wider.recall.report import interventions

    d = rv.qc_draw(proj, None, None, 5, 7, "ana")
    r0, r1 = d["records"][:2]
    flip = "exclude" if proj.store.scalar("SELECT decision FROM screen_decisions WHERE record_id=?", (r0,)) == "include" \
        else "include"
    rv.decide(proj, None, None, r0, flip, "ana")
    keep = proj.store.scalar("SELECT decision FROM screen_decisions WHERE record_id=?", (r1,))
    rv.decide(proj, None, None, r1, keep, "ana")
    assert ("A quality-control sample of 5 of 32 records the LLM settled was drawn at random (seed 7); a researcher "
            "checked 2 and changed 1.") in rv.methods_sentences(proj, "main")
    e = [x for x in interventions(proj)["entries"] if x["kind"] == "review_qc_draw"]
    assert len(e) == 1 and e[0]["kind_label"] == "quality-control sample"


def test_qc_draw_routes(proj):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    c = TestClient(create_app(proj))
    for body, head, msg in (({"n": 0}, {"x-wider-author": "ana"}, "the sample size must be at least 1"),
                            ({}, {"x-wider-author": "ana"}, "the sample size must be at least 1"),
                            ({"n": 50}, {"x-wider-author": "ana"}, "only 32 settled records can be drawn"),
                            ({"n": 5, "seed": 7}, {}, "a reviewer name is needed")):
        r = c.post("/api/review/qc", json=body, headers=head)
        assert r.status_code == 400 and r.json()["detail"] == msg, r.text
    assert proj.store.scalar("SELECT COUNT(*) FROM review_qc") == 0
    r = c.post("/api/review/qc", json={"n": 5, "seed": 7}, headers={"x-wider-author": "ana"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["n"] == 5 and d["pool_n"] == 32 and d["change_id"]
    assert proj.store.scalar("SELECT author FROM history WHERE change_id=?", (d["change_id"],)) == "ana"
    assert [x["record_id"] for x in c.get("/api/review", params={"show": "qc"}).json()["rows"]] == d["records"]
