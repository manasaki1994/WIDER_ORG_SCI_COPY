"""The Screening review page's engine and CLI (manual-review D1-D4, D11a, D12, D13). Synthetic, topic-neutral records;
decisions, runs, calls and questions are written directly; nothing calls a model."""
import json

import pytest
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.core.questions import answer, raise_question
from wider.screen import result_sets as rs
from wider.screen import review as rv
from wider.screen.decisions import set_decision


@pytest.fixture
def proj(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    for i in range(1, 41):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    return p


RULES = {"kind": "screen", "rules": {"passes": 2, "majority": 2, "blind_third_read": True}}


def run(p, key, run_id, pass_, calls, prompt_version=None):
    """A stored run of one pass with ``calls`` of (record_id, ok, decision, summary); returns {record_id: call_id}."""
    (p.root / "runs" / run_id).mkdir(parents=True, exist_ok=True)
    p.store.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,provider,model,prompt_version,status,n_items,n_ok,"
                    "started_at,dir) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, "screen", "s", "h", "fake", "synthetic-model", prompt_version, "done", len(calls),
                     sum(c[1] for c in calls), "2026-01-01T00:00:00", f"runs/{run_id}"))
    out = {}
    for rid, ok, dec, summary in calls:
        cur = p.store.execute(
            "INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,rule_decision,parsed,error,at,provider,model) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, rid, pass_, "x", ok, dec if ok else None,
             json.dumps({"decision": dec, "summary": summary}) if ok else None, None if ok else "timeout",
             "2026-01-01T00:00:01", "fake", "synthetic-model"))
        out[rid] = cur.lastrowid
    rs.add_runs(p, key, [(run_id, pass_)])
    return out


def scenario(p, key="main"):
    """Nine records of set ``key``: agreed exclude, agreed include, settled by the third read, an open split, an
    agreed maybe, a failed pass with its maybe, an imported decision, one decided by a researcher and a failed read
    without a decision row. Returns the question ids."""
    if rs.row_by_key(p, key) is None:
        rs.new_set(p, key.split("#")[0], "screening", settings=RULES)
    a = run(p, key, f"{key}-A", "A", [(1, 1, "exclude", "off topic"), (2, 1, "include", "fits"),
                                      (3, 1, "include", "fits"), (4, 1, "include", "fits"), (5, 1, "maybe", "unclear"),
                                      (6, 1, "exclude", "off topic"), (8, 1, "include", "fits"), (9, 0, None, None)])
    b = run(p, key, f"{key}-B", "B", [(1, 1, "exclude", "off topic"), (2, 1, "include", "fits"),
                                      (3, 1, "exclude", "no study"), (4, 1, "exclude", "no study"), (5, 1, "maybe", "thin"),
                                      (6, 0, None, None), (8, 1, "maybe", "unclear"), (9, 1, "exclude", "off topic")])
    c = run(p, key, f"{key}-C", "C", [(3, 1, "exclude", "no study"), (4, 1, "maybe", "unclear"),
                                      (8, 1, "exclude", "no study")])
    rows = {1: ("exclude", ["exclude", "exclude"], "A,B", [a[1], b[1]]),
            2: ("include", ["include", "include"], "A,B", [a[2], b[2]]),
            3: ("exclude", ["include", "exclude", "exclude"], "B,C", [a[3], b[3], c[3]]),
            4: ("maybe", ["include", "exclude", "maybe"], "split", [a[4], b[4], c[4]]),
            5: ("maybe", ["maybe", "maybe"], "A,B", [a[5], b[5]]),
            6: ("maybe", ["exclude"], "A", [a[6]]),
            7: ("exclude", None, "imported from list.csv", None),
            8: ("include", ["include", "maybe", "exclude", "researcher:include"], "researcher", [a[8], b[8], c[8]])}
    for rid, (final, votes, by, calls) in rows.items():
        set_decision(p, key, rid, final, votes=votes, decided_by=by, vote_calls=calls,
                     reason="researcher (ana) decided after three disagreeing reads" if by == "researcher" else None)
    split = raise_question(p, "screen_split", f"{key}:4", "Synthetic paper 4", "three reads disagree",
                           context={"protocol": key, "votes": rows[4][1], "vote_calls": rows[4][3]}, record_id=4,
                           run_ref=key)
    f6 = raise_question(p, "screen_failed", f"{key}-B:6:B", "Synthetic paper 6", "failed", context={"pass": "B",
                        "error": "timeout"}, record_id=6, run_ref=f"{key}-B")
    f9 = raise_question(p, "screen_failed", f"{key}-A:9:A", "Synthetic paper 9", "failed", context={"pass": "A",
                        "error": "timeout"}, record_id=9, run_ref=f"{key}-A")
    return {"split": split, "f6": f6, "f9": f9}


def ids(res):
    return [r["record_id"] for r in res["rows"]]


def cli(p, *args):
    return CliRunner().invoke(cli_app, ["review", *args, "--dir", str(p.root)])


# ------------------------------------------------------------------------------------------------- 2.1 the list
def test_review_list_flagged_majority_all(proj):
    scenario(proj)
    f = rv.flagged(proj)
    assert (f["protocol"], f["set"]) == ("main", 1)
    assert ids(f) == [4, 5, 6, 8, 9] and f["listed"] == 5 and f["undecided"] == 4
    assert rv.count_line(f) == "5 records need a person, 4 undecided"
    assert ids(rv.flagged(proj, show="majority")) == [3, 4, 5, 6, 8, 9]
    assert ids(rv.flagged(proj, show="all")) == list(range(1, 10))
    assert ids(rv.flagged(proj, show="qc")) == []
    by = {r["record_id"]: r for r in rv.flagged(proj, show="all")["rows"]}
    assert by[9]["failed"] and by[9]["decision"] is None and not by[9]["decidable"]
    assert by[1]["route_label"] == "both passes agreed" and by[7]["route"] == "import"
    with pytest.raises(rv.Refusal):
        rv.flagged(proj, show="everything")
    out = cli(proj, "list", "--json")
    assert out.exit_code == 0, out.output
    assert [r["record_id"] for r in json.loads(out.output)["rows"]] == [4, 5, 6, 8, 9]
    out = cli(proj, "list", "--show", "all")
    assert out.exit_code == 0 and "9 records listed, 4 undecided" in out.output
    assert "settled" in out.output.splitlines()[2]   # record 1: the passes settled it, nothing waits


def test_review_list_without_decisions(proj):
    f = rv.flagged(proj)
    assert f["nothing"] and f["rows"] == [] and rv.default_target(proj) is None
    out = cli(proj, "list")
    assert out.exit_code == 0 and rv.NOTHING in out.output
    assert rv.pending(proj) is None


def test_review_list_first_line_says_it_shows_llm_reads(proj):
    scenario(proj)
    out = cli(proj, "list")
    assert out.output.splitlines()[0] == rv.SENTENCE
    out = cli(proj, "list")   # also when nothing is listed for another protocol
    assert rv.SENTENCE in out.output


def test_review_list_default_protocol_and_set(proj):
    fill_set(proj, "other", 3)
    assert rv.default_target(proj) == {"protocol": "other", "set": 1}   # the only protocol with decisions
    scenario(proj)
    assert rv.default_target(proj)["protocol"] == "main"                # "main" before the others
    rs.new_set(proj, "main", "screening", settings=RULES)
    rs.use(proj, "main", 2)
    set_decision(proj, "main#2", 1, "maybe", votes=["maybe", "maybe"], decided_by="A,B")
    assert rv.default_target(proj) == {"protocol": "main", "set": 2}    # the active set
    assert ids(rv.flagged(proj)) == [1] and ids(rv.flagged(proj, "main", 1)) == [4, 5, 6, 8, 9]
    # a whole-corpus round on the Screening card wins
    fill_set(proj, "probe", 40, scope="corpus")
    assert rv.default_target(proj)["protocol"] == "probe"
    with pytest.raises(KeyError):
        rv.flagged(proj, "main", 7)


def fill_set(p, protocol, n, scope=None):
    rs.new_set(p, protocol, "screening", settings=RULES, scope=scope)
    for i in range(1, n + 1):
        set_decision(p, protocol, i, "maybe" if i == 1 else "exclude", votes=["exclude", "exclude"], decided_by="A,B")


def test_failed_read_and_its_maybe_are_one_row(proj):
    q = scenario(proj)
    rows = [r for r in rv.flagged(proj)["rows"] if r["record_id"] == 6]
    assert len(rows) == 1 and rows[0]["failed"] and rows[0]["why"] == "a pass failed to read it"
    assert rows[0]["question_id"] == q["f6"] and rows[0]["decision"] == "maybe"
    out = cli(proj, "list")
    assert f"wider questions answer {q['f6']}" in " ".join(out.output.split())   # rich wraps at the terminal width


def test_answered_failed_read_waits_for_the_next_run(proj):
    q = scenario(proj)
    answer(proj, q["f6"], "exclude", "", "bo")
    r = next(r for r in rv.flagged(proj)["rows"] if r["record_id"] == 6)
    assert r["failed_answered"] and not r["failed"] and not r["decided"]
    assert r["why"] == "failed read answered by bo; the next screening run of this set writes its decision"
    assert rv.flagged(proj)["undecided"] == 4
    with pytest.raises(rv.Refusal) as e:
        rv.decide(proj, None, None, 6, "include", "ana")
    assert e.value.status == 409 and "failed read answered by bo" in str(e.value)
    # the next run writes the decision with the researcher's call: the row leaves the list
    call = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-B' AND record_id=6")
    first = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-A' AND record_id=6")
    set_decision(proj, "main", 6, "exclude", votes=["exclude", "exclude"], decided_by="A,B", vote_calls=[first, call])
    assert 6 not in ids(rv.flagged(proj))


def test_answered_failed_read_is_labelled(proj):
    q = scenario(proj)
    answer(proj, q["f6"], "exclude", "checked by hand", "bo")
    call = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-B' AND record_id=6")
    first = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-A' AND record_id=6")
    set_decision(proj, "main", 6, "exclude", votes=["exclude", "exclude"], decided_by="A,B", vote_calls=[first, call])
    v = rv.record_view(proj, None, None, 6)
    assert [x["model"] for x in v["reads"]] == ["synthetic-model", rv.ANSWERED_READ]
    assert v["outcome_label"] == "Outcome of the passes' reads (one given by a researcher)"


# ------------------------------------------------------------------------------------------------- 2.1 one record
def test_review_show_reads_and_no_sample_information(proj):
    from wider.sample.labels import label
    scenario(proj)
    # record 3 is also in a labelling sample with a label: neither shows on the review page (blinding)
    proj.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) "
                       "VALUES(1,'secretdev','purposive','{}',1,'x')")
    proj.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,3,1)")
    label(proj, "secretdev", 3, "zed", "maybe", reason="labelled in the sample")
    proj.store.execute("UPDATE records SET abstract='A synthetic abstract.' WHERE record_id=3")
    v = rv.record_view(proj, None, None, 3)
    assert [(x["label"], x["decision"], x["reason"]) for x in v["reads"]] == [
        ("pass A", "include", "fits"), ("pass B", "exclude", "no study"), ("third read", "exclude", "no study")]
    assert v["abstract"] == "A synthetic abstract." and v["no_reads"] is None
    assert v["final_line"] == "Final: exclude (settled by the third read)"
    text = json.dumps(v)
    for word in ("band", "rank", "sample_id", "pool", "weight", "score"):
        assert f'"{word}' not in text
    for word in ("secretdev", "zed", "labelled in the sample"):
        assert word not in text
    out = cli(proj, "show", "3")
    assert out.exit_code == 0 and "third read: exclude (synthetic-model): no study" in out.output
    assert "secretdev" not in out.output and "zed" not in out.output
    assert "band" not in out.output and "sample" not in out.output.replace(rv.SENTENCE, "")
    with pytest.raises(rv.Refusal) as e:
        rv.record_view(proj, None, None, 33)
    assert e.value.status == 404 and str(e.value) == "record 33 is not in set main set 1"
    assert cli(proj, "show", "33").exit_code == 1


def test_review_show_without_pass_reads(proj):
    scenario(proj)
    v = rv.record_view(proj, None, None, 7)
    assert v["reads"] == [] and v["no_reads"] == rv.NO_READS
    assert v["outcome_label"] == "Earlier decision (imported)" and v["llm_outcome"] == "exclude"
    assert rv.NO_READS in cli(proj, "show", "7").output


def test_review_show_llm_outcome_and_final(proj):
    scenario(proj)
    v = rv.record_view(proj, None, None, 8)   # three reads disagree: the LLM outcome is maybe
    assert v["llm_outcome"] == "maybe" and v["outcome_label"] == "AI model outcome"
    assert v["final"] == "include" and v["final_line"] == "Final: include (researcher ana)"
    before = [x["decision"] for x in rv.record_view(proj, None, None, 5)["reads"]]
    rv.decide(proj, None, None, 5, "exclude", "bo")
    v = rv.record_view(proj, None, None, 5)
    assert [x["decision"] for x in v["reads"]] == before == ["maybe", "maybe"]
    assert v["llm_outcome"] == "maybe" and v["final_line"] == "Final: exclude (researcher bo)"
    v = rv.record_view(proj, None, None, 4)
    assert v["final_line"] == "Final: maybe (no majority (left at maybe))"


def test_review_rows_say_why(proj):
    scenario(proj)
    why = {r["record_id"]: r["why"] for r in rv.flagged(proj, show="all")["rows"]}
    assert why == {1: "both passes agreed", 2: "both passes agreed", 3: "settled by the third read",
                   4: "the three reads disagree", 5: "the final decision is maybe", 6: "a pass failed to read it",
                   7: "imported", 8: "decided by the researcher", 9: "a pass failed to read it"}
    assert rv.record_view(proj, None, None, 4)["why"] == "the three reads disagree"
    data = json.loads(cli(proj, "list", "--json", "--show", "all").output)
    assert {r["record_id"]: r["why"] for r in data["rows"]} == why


# ------------------------------------------------------------------------------------------------- 2.2 deciding
def test_review_decide_in_place_and_through_the_question(proj):
    q = scenario(proj)
    v = rv.decide(proj, None, None, 5, "include", "bo", "read the full text")
    row = proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=5")
    assert row["decision"] == "include" and row["decided_by"] == "researcher"
    assert json.loads(row["votes"]) == ["maybe", "maybe", "researcher:include"]
    assert row["reason"] == "researcher (bo) decided after screening: read the full text"
    ch = proj.store.one("SELECT * FROM history WHERE change_id=?", (v["change_id"],))
    assert (ch["kind"], ch["author"], ch["record_id"]) == ("review_decision", "bo", 5)
    assert ch["summary"] == "review decision on record 5: include (was maybe)"
    assert journal.KIND_LABELS["review_decision"] == "review decision"
    rv.decide(proj, None, None, 5, "exclude", "bo")   # one researcher vote: the earlier one replaced
    votes = json.loads(proj.store.scalar("SELECT votes FROM screen_decisions WHERE protocol='main' AND record_id=5"))
    assert votes == ["maybe", "maybe", "researcher:exclude"]
    # the open split goes through its question
    rv.decide(proj, None, None, 4, "exclude", "ana", "no data")
    assert proj.store.one("SELECT status, answered_by FROM questions WHERE question_id=?", (q["split"],))["status"] == "answered"
    assert proj.store.scalar("SELECT kind FROM history ORDER BY change_id DESC LIMIT 1") == "answer"
    assert rv.record_view(proj, None, None, 4)["final_line"] == "Final: exclude (researcher ana)"
    out = cli(proj, "decide", "2", "maybe", "--by", "cy", "--reason", "unsure")
    assert out.exit_code == 0, out.output
    assert "Final: maybe (researcher cy)" in out.output


def test_review_decide_refusals(proj):
    scenario(proj)
    for args, msg, status, code in [
            ((5, "include", ""), "a reviewer name is needed", 400, 2),
            ((5, "keep", "bo"), "decision must be include, exclude or maybe", 400, 2),
            ((33, "include", "bo"), "record 33 is not in set main set 1", 404, 1)]:
        with pytest.raises(rv.Refusal) as e:
            rv.decide(proj, None, None, *args)
        assert (str(e.value), e.value.status, e.value.code) == (msg, status, code)
    assert proj.store.scalar("SELECT COUNT(*) FROM history WHERE kind='review_decision'") == 0
    assert cli(proj, "decide", "5", "keep", "--by", "bo").exit_code == 2
    assert cli(proj, "decide", "33", "include", "--by", "bo").exit_code == 1


def test_failed_read_is_linked_not_decided(proj):
    q = scenario(proj)
    with pytest.raises(rv.Refusal) as e:
        rv.decide(proj, None, None, 6, "include", "bo")
    assert e.value.status == 409 and str(e.value) == (
        f"record 6 has a failed read; answer its question on the Questions page (wider questions answer {q['f6']})")
    assert proj.store.scalar("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=6") == "maybe"
    out = cli(proj, "decide", "9", "exclude", "--by", "bo")
    assert out.exit_code == 1 and f"wider questions answer {q['f9']}" in " ".join(out.output.split())


def test_review_decision_keeps_and_restores_the_origin(proj):
    scenario(proj)
    origin = {"kind": "reused", "from_set": 1, "protocol": "main"}
    set_decision(proj, "main", 5, "maybe", votes=["maybe", "maybe"], decided_by="A,B", origin=origin)
    v = rv.decide(proj, None, None, 5, "include", "bo")
    assert json.loads(proj.store.scalar("SELECT origin FROM screen_decisions WHERE record_id=5")) == origin
    journal.undo(proj, v["change_id"], author="bo")
    row = proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=5")
    assert json.loads(row["origin"]) == origin and row["decision"] == "maybe" and row["decided_by"] == "A,B"


def test_review_decision_undo_restores_the_llm_outcome(proj):
    scenario(proj)
    before = dict(proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=5"))
    v = rv.decide(proj, None, None, 5, "exclude", "bo")
    journal.undo(proj, v["change_id"], author="bo")
    after = dict(proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=5"))
    assert after == before
    assert 5 in ids(rv.flagged(proj)) and not rv.record_view(proj, None, None, 5)["decided"]


def test_review_decision_undo_refused_after_a_later_decision(proj):
    scenario(proj)
    first = rv.decide(proj, None, None, 5, "exclude", "ana")
    rv.decide(proj, None, None, 5, "include", "bo")
    with pytest.raises(ValueError, match="changed since; undo the later change first"):
        journal.undo(proj, first["change_id"], author="ana")
    row = proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=5")
    assert row["decision"] == "include" and json.loads(row["votes"]) == ["maybe", "maybe", "researcher:include"]
    # a screening rerun keeps the decision: the run re-checks for a researcher vote (protocol.py, D3)


def test_imported_row_keeps_its_earlier_decision(proj):
    scenario(proj)
    v = rv.decide(proj, None, None, 7, "include", "bo")
    row = proj.store.one("SELECT * FROM screen_decisions WHERE protocol='main' AND record_id=7")
    assert json.loads(row["origin"])["before_review"] == "exclude"
    assert v["llm_outcome"] == "exclude" and v["outcome_label"] == "Earlier decision (imported)"
    w = rv.decide(proj, None, None, 7, "maybe", "ana")   # first decision only: the earlier decision stays "exclude"
    assert json.loads(proj.store.scalar("SELECT origin FROM screen_decisions WHERE record_id=7"))["before_review"] == "exclude"
    view = rv.record_view(proj, None, None, 7)
    assert view["route"] == "researcher" and view["final_line"] == "Final: maybe (researcher ana)"
    journal.undo(proj, w["change_id"], author="ana")
    journal.undo(proj, v["change_id"], author="bo")
    assert proj.store.scalar("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=7") == "exclude"


# ------------------------------------------------------------------------------------------------- 2.7 criteria
CODEBOOK = {"question": "Does the synthetic paper report a study of widgets?", "target_description": "Widget studies.",
            "elements": [{"id": "e1", "name": "Widgets", "rule": "the paper studies widgets"},
                         {"id": "e2", "name": "Gadgets", "rule": "the paper studies gadgets"}],
            "checks": [{"key": "c1", "question": "Is it a study?", "if_yes": "go on"}],
            "edge_cases": [], "exclusion_reasons": [{"code": "not_study", "text": "not a study"}]}


def test_review_criteria_of_the_set_s_codebook(proj):
    from wider.construct import artifacts

    artifacts.save_new_version(proj, "codebook", CODEBOOK, origin="manual")
    artifacts.save_new_version(proj, "codebook", {**CODEBOOK, "question": "A later question?"}, origin="manual")
    artifacts.save_new_version(proj, "prompts", {"codebook_version": 1, "prompt": "Synthetic prompt. " * 20, "schema": {},
                                                    "elements": ["e1", "e2"]}, origin="manual")
    rs.new_set(proj, "main", "screening", settings=RULES)
    run(proj, "main", "r1", "A", [(1, 1, "exclude", "x")], prompt_version=1)
    set_decision(proj, "main", 1, "exclude", votes=["exclude"], decided_by="A")
    c = rv.criteria(proj)
    assert c["version"] == 1 and c["note"] == "codebook version 1, used by this set" and c["known"]
    keys = [s["key"] for s in c["sections"]]
    assert keys[:3] == ["question", "target_description", "elements"] and "edge_cases" not in keys
    assert c["sections"][0]["text"] == "Does the synthetic paper report a study of widgets?"
    assert "thin_abstract_rule" in keys
    out = cli(proj, "criteria")
    assert out.exit_code == 0 and "codebook version 1, used by this set" in out.output
    assert "Widgets: the paper studies widgets" in out.output and "A later question?" not in out.output


def test_review_criteria_version_unknown(proj):
    from wider.construct import artifacts

    artifacts.save_new_version(proj, "codebook", CODEBOOK, origin="manual")
    scenario(proj)   # runs without a prompt version
    c = rv.criteria(proj)
    assert not c["known"] and c["note"] == ("this set's codebook version is not recorded; showing the current "
                                            "codebook (version 1)")


# ------------------------------------------------------------------------------------------------- 2.9, 2.10
def test_pending_reviews_in_screen_status_and_info(proj):
    from wider.core.summary import project_summary, summary_lines
    from wider.screen.progress import screen_progress, status_lines

    scenario(proj)
    p = rv.pending(proj)
    assert p["count"] == 4 and p["href"] == "#review?protocol=main&set=1"
    line = "4 records wait for a person (Screening review, wider review list)"
    assert line in status_lines(screen_progress(proj))
    s = project_summary(proj)
    assert s["pending_review"]["count"] == 4 and ("screening review", line) in summary_lines(s)
    for rid in (4, 5):
        rv.decide(proj, None, None, rid, "exclude", "bo")
    assert rv.pending(proj)["count"] == 2


def test_sample_counts_records_reviewed_with_llm_reads(proj):
    q = scenario(proj)
    proj.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(1,'probability','probability','{}',4,'x')")
    for rid in (3, 4, 5, 6):
        proj.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,?,?)", (rid, rid))
    assert rv.reviewed_with_llm_reads(proj, 1) == 0
    v = rv.decide(proj, None, None, 5, "include", "bo")
    rv.decide(proj, None, None, 4, "exclude", "bo")              # the split question
    answer(proj, q["f6"], "exclude", "", "bo")                  # a failed read: not counted
    assert rv.reviewed_with_llm_reads(proj, 1) == 2
    st = CliRunner().invoke(cli_app, ["sample", "status", "--dir", str(proj.root)])
    assert st.exit_code == 0, st.output
    assert "2 records of this sample were decided by a person who saw the AI model's reads" in st.output
    journal.undo(proj, v["change_id"], author="bo")
    assert rv.reviewed_with_llm_reads(proj, 1) == 1
    st = CliRunner().invoke(cli_app, ["sample", "status", "--json", "--dir", str(proj.root)])
    assert json.loads(st.output)["samples"]["probability"]["reviewed_with_llm_reads"] == 1
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    c = TestClient(create_app(proj))
    assert c.get("/api/samples").json()["samples"]["probability"]["reviewed_with_llm_reads"] == 1
    q = c.get("/api/labels/queue", params={"samples": "probability", "reviewer": "bo"})
    assert q.status_code == 200, q.text
    assert "reviewed_with_llm_reads" not in q.text


# ------------------------------------------------------------------------------------------------- 2.4 report
def test_review_decisions_in_report_and_methods(proj):
    from wider.recall.outputs import methods_draft
    from wider.recall.report import interventions

    scenario(proj)
    assert rv.methods_sentences(proj, "main") == ["A researcher decided 1 record after screening; 1 of them changed "
                                                  "the LLM outcome."]   # record 8: maybe by the reads, include by ana
    v = rv.decide(proj, None, None, 5, "maybe", "bo")      # unchanged against the LLM outcome
    rv.decide(proj, None, None, 1, "include", "bo")        # changed
    line = "A researcher decided 3 records after screening; 2 of them changed the LLM outcome."
    assert rv.methods_sentences(proj, "main") == [line]
    assert line in methods_draft(proj)
    e = [x for x in interventions(proj)["entries"] if x["kind"] == "review_decision"]
    assert [x["summary"] for x in e] == ["review decision on record 5: maybe (was maybe)",
                                         "review decision on record 1: include (was exclude)"]
    assert e[0]["kind_label"] == "review decision" and e[0]["author"] == "bo"
    journal.undo(proj, v["change_id"], author="bo")
    assert rv.methods_sentences(proj, "main") == ["A researcher decided 2 records after screening; 2 of them changed "
                                                  "the LLM outcome."]


# ------------------------------------------------------------------------------------------------- 3.1 the routes
def client(p):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app
    return TestClient(create_app(p))


def test_review_routes_list_record_summary(proj):
    scenario(proj)
    c = client(proj)
    r = c.get("/api/review")
    assert r.status_code == 200, r.text
    j = r.json()
    assert [x["record_id"] for x in j["rows"]] == [4, 5, 6, 8, 9] == ids(rv.flagged(proj))
    assert j["count_line"] == "5 records need a person, 4 undecided" and j["sentence"] == rv.SENTENCE
    assert j["protocols"] == ["main"] and j["sets"] == [{"set": 1, "label": "set 1", "active": True}]
    assert len(c.get("/api/review", params={"show": "all"}).json()["rows"]) == 9
    assert c.get("/api/review", params={"show": "everything"}).status_code == 400
    assert c.get("/api/review", params={"protocol": "main", "set": 4}).status_code == 404
    v = c.get("/api/review/5").json()
    assert v == json.loads(json.dumps(rv.record_view(proj, None, None, 5), default=str))
    assert c.get("/api/review/33").status_code == 404
    s = c.get("/api/review/summary").json()["pending_review"]
    assert s["count"] == 4 and s["href"] == "#review?protocol=main&set=1"


def test_review_routes_decide_and_refusals(proj):
    scenario(proj)
    c = client(proj)
    for rid, body, head, status, msg in [
            (5, {"decision": "include"}, {}, 400, "a reviewer name is needed"),
            (5, {"decision": "keep"}, {"x-wider-author": "bo"}, 400, "decision must be include, exclude or maybe"),
            (33, {"decision": "include"}, {"x-wider-author": "bo"}, 404, "record 33 is not in set main set 1"),
            (6, {"decision": "include"}, {"x-wider-author": "bo"}, 409, "record 6 has a failed read")]:
        r = c.post(f"/api/review/{rid}", json=body, headers=head)
        assert r.status_code == status and msg in r.json()["detail"], r.text
    assert proj.store.scalar("SELECT COUNT(*) FROM history WHERE kind='review_decision'") == 0
    r = c.post("/api/review/5", json={"decision": "include", "reason": "full text"}, headers={"x-wider-author": "bo"})
    assert r.status_code == 200, r.text
    ch = proj.store.one("SELECT kind, author FROM history WHERE change_id=?", (r.json()["change_id"],))
    assert (ch["kind"], ch["author"]) == ("review_decision", "bo")   # signed by the page's name, not $USER


def test_review_routes_criteria(proj):
    scenario(proj)
    r = client(proj).get("/api/review/criteria")
    assert r.status_code == 200, r.text
    assert r.json() == json.loads(json.dumps(rv.criteria(proj), default=str))


# ------------------------------------------------------------------------------------------------- risk-checker R1
from tests.unit.test_result_sets import (  # noqa: E402,F401
    DEV,
    _maybe_backend,
    _ok,
    _project_fixture,
    counted,
    decisions_of,
    reload,
)


def test_second_screen_rerun_keeps_a_review_decision(counted, monkeypatch):  # noqa: F811
    """A re-run of the second screen left a review decision made in its set as it was only for the first protocol run;
    the second screen overwrote it (risk-checker R1). Now both leave a researcher's decision, vote and note alone."""
    from tests.unit.test_result_sets import cli as rs_cli
    p, c = counted
    _maybe_backend(monkeypatch, c)
    p.config.protocol.screening.second_screen = True
    p.save()
    _ok(rs_cli(p, "screen", "protocol", DEV))
    _ok(rs_cli(p, "screen", "second", "main"))
    p = reload(p)
    src = decisions_of(p, "main")
    rid = next(r for r in decisions_of(p, "main_second") if src.get(r) == "maybe")
    rv.decide(p, "main_second", None, rid, "exclude", "ana", "my call")
    before = dict(p.store.one("SELECT * FROM screen_decisions WHERE protocol='main_second' AND record_id=?", (rid,)))
    _ok(rs_cli(p, "screen", "second", "main"))
    after = dict(reload(p).store.one("SELECT * FROM screen_decisions WHERE protocol='main_second' AND record_id=?", (rid,)))
    assert after == before and after["decided_by"] == "researcher" and "my call" in after["reason"]


def test_cli_without_by_never_signs_with_the_login_name(proj, monkeypatch):
    """risk-checker R3: `wider review decide` and `qc draw` without --by fell back to $USER and signed with the
    computer's login name; they now refuse ("a reviewer name is needed", exit 2) unless $WIDER_REVIEWER names one."""
    scenario(proj)
    monkeypatch.setenv("USER", "loginname")
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    for args in (("decide", "5", "include"), ("qc", "draw", "--n", "1")):
        out = cli(proj, *args)
        assert out.exit_code == 2 and "a reviewer name is needed" in out.output, out.output
    assert proj.store.scalar("SELECT COUNT(*) FROM history WHERE author='loginname'") == 0
    monkeypatch.setenv("WIDER_REVIEWER", "ana")
    out = cli(proj, "decide", "5", "include")
    assert out.exit_code == 0 and "Final: include (researcher ana)" in " ".join(out.output.split()), out.output
