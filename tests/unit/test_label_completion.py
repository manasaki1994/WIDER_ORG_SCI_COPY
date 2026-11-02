"""label-completion-and-sealing: the three new tables, label provenance and independence, and the completeness of a
sample (design D2, D3, D11). Synthetic, topic-neutral records; reviewers R1, R2 and R3; nothing calls a model or the
network."""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_package import ME, _export, _write
from tests.unit.test_package import _project as _pkg_project
from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.core.store import SCHEMA_VERSION, Store
from wider.sample import labels as lb
from wider.sample import package as pk
from wider.ui.app import create_app

runner = CliRunner()
NEW_TABLES = ("label_corrections", "label_seals", "label_independence")


@pytest.fixture
def proj(tmp_path):
    """``dev`` (purposive, records 1-3) and ``validation`` (probability, records 4-6), neither locked."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    for i in range(1, 10):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    for sid, name, kind in ((1, "dev", "purposive"), (2, "validation", "probability")):
        p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(?,?,?,'{}',3,'x')",
                        (sid, name, kind))
        for pos, rid in enumerate((1, 2, 3) if sid == 1 else (4, 5, 6)):
            p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)", (sid, rid, pos))
    return p


def _cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env=ME)


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# ----- 2.1 storage -----

def test_new_and_upgraded_store_have_the_tables(tmp_path):
    s = Store(tmp_path / "a.sqlite")
    assert set(NEW_TABLES) <= _tables(s.conn)
    assert int(s.get_meta("schema_version")) == SCHEMA_VERSION == 12
    for t in NEW_TABLES:   # an older file, made before this change, opened again
        s.conn.execute(f"DROP TABLE {t}")
    s.conn.close()
    assert not set(NEW_TABLES) & _tables(sqlite3.connect(tmp_path / "a.sqlite"))
    s = Store(tmp_path / "a.sqlite")
    assert set(NEW_TABLES) <= _tables(s.conn)
    assert int(s.get_meta("schema_version")) == SCHEMA_VERSION   # no version step
    s.conn.close()


def test_dedup_merge_moves_seals(proj):
    from wider.dedup.engine import _remap_downstream
    st = proj.store
    st.execute("INSERT INTO label_seals(sample_id,record_id,sealed_at,first_change_id,last_change_id,decision) "
               "VALUES(2,4,'t',1,1,'include')")
    st.execute("INSERT INTO label_corrections(sample_id,record_id,target,why,author,at,locked,sealed) "
               "VALUES(2,4,'label','typo','R1','t',1,1)")
    st.execute("INSERT INTO label_independence(sample_id,record_id,reviewer_id,basis,by,at) "
               "VALUES(2,4,'R1','screened before the model existed','R1','t')")
    with st.tx() as c:
        _remap_downstream(c, 4, 9)
    for t in NEW_TABLES:
        assert st.scalar(f"SELECT record_id FROM {t}") == 9, t


# ----- 2.1b provenance (D11) -----
BASIS = "screened in 2024, before the ranking model was built"


def _made_in(p, sample, rid, who):
    return p.store.scalar("SELECT l.made_in FROM labels l JOIN samples s ON s.sample_id=l.sample_id "
                          "WHERE s.name=? AND l.record_id=? AND l.reviewer_id=?", (sample, rid, who))


def _ind(p, sample, rid, who):
    r = p.store.one("SELECT l.* FROM labels l JOIN samples s ON s.sample_id=l.sample_id "
                    "WHERE s.name=? AND l.record_id=? AND l.reviewer_id=?", (sample, rid, who))
    return lb._independent(p, dict(r))


def _client(p, author="Owner"):
    return TestClient(create_app(p), headers={"X-Wider-Author": author})


def test_provenance_of_each_label(proj, tmp_path):
    c = _client(proj)
    # the unblinded queue posts a sample and record; the blind queue posts an item
    assert c.post("/api/labels", json={"sample_id": 2, "record_id": 4, "reviewer": "R1",
                                       "decision": "include"}).status_code == 200
    tok = next(r["item"] for r in lb.queue(proj, "R2", ["validation"]) if r["item"])
    r = c.post("/api/labels", json={"item": tok, "reviewer": "R2", "decision": "exclude"})
    assert r.status_code == 200, r.text
    rid2 = lb.resolve_item(proj, tok)["record_id"]
    f = tmp_path / "file.csv"
    f.write_text("record_id,decision\n5,include\n", encoding="utf-8")
    lb.import_csv(proj, "validation", f, reviewer_id="R3")
    g = tmp_path / "sample.csv"
    g.write_text("title,decision,band,weight\nSynthetic paper 7,include,S1,2\nSynthetic paper 8,exclude,S1,2\n",
                 encoding="utf-8")
    lb.import_sample_csv(proj, "elsewhere", g, reviewer_id="R4")
    assert _made_in(proj, "validation", 4, "R1") == "unblinded queue"
    assert _made_in(proj, "validation", rid2, "R2") == "blind queue"
    assert _made_in(proj, "validation", 5, "R3") == "file import"
    assert _made_in(proj, "elsewhere", 7, "R4") == "sample import"
    assert not _ind(proj, "validation", 4, "R1") and _ind(proj, "validation", rid2, "R2")
    assert not _ind(proj, "validation", 5, "R3") and not _ind(proj, "elsewhere", 7, "R4")
    pv = lb.provenance(proj, "validation")
    assert pv["by_made_in"] == {"blind queue": 1, "file import": 1, "unblinded queue": 1}
    assert (pv["independent"], pv["not_independent"], pv["declared"]) == (1, 2, 0)
    # a package label (not a repeat) is independent at once
    p = _pkg_project(tmp_path, "pk", n_pos=4, n_exc=4)
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=P["n_items"])))
    rid = pk.items(p, P["package_id"])[0]["record_id"]
    assert _made_in(p, "validation", rid, "R3") == P["package_id"] and _ind(p, "validation", rid, "R3")


def test_older_labels_keep_their_provenance(proj):
    st = proj.store
    lb.add_reviewer(proj, "R1")
    for rid, m in ((4, None), (5, "blind queue"), (6, "p-older")):
        st.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at" + (",made_in" if m else "")
                   + ") VALUES(2,?,?,?,?" + (",?" if m else "") + ")",
                   (rid, "R1", "include", "t", *([m] if m else [])))
    assert _made_in(proj, "validation", 4, "R1") == "wider"   # the column's default: saved before provenance
    assert not _ind(proj, "validation", 4, "R1")
    assert _ind(proj, "validation", 5, "R1")   # second-reviewer and re-check labels: independent at once
    assert _ind(proj, "validation", 6, "R1")   # a package label with no item recorded as a repeat
    assert lb.provenance(proj, "validation")["by_made_in"] == {"blind queue": 1, "review package": 1, "wider": 1}


def test_declaring_earlier_labels_independent(proj, tmp_path):
    st = proj.store
    lb.add_reviewer(proj, "R1")
    st.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at) VALUES(2,4,'R1','include','t')")
    lb.label(proj, "validation", 5, "R1", "exclude", made_in="unblinded queue")
    lb.label(proj, "validation", 6, "R1", "exclude")   # blind queue: needs nothing
    r = _cli(proj, "label", "provenance", "validation", "--reviewer", "R1", "--independent", BASIS, "--by", "Owner")
    assert r.exit_code == 0, r.output
    assert "1 label of R1 on validation now count as independent" in " ".join(r.output.split())
    assert "1 label was made in the unblinded queue" in " ".join(r.output.split())
    assert _ind(proj, "validation", 4, "R1") and not _ind(proj, "validation", 5, "R1")
    ch = journal.changes(proj, kind="label_provenance")
    assert len(ch) == 1 and ch[0]["author"] == "Owner" and ch[0]["undoable"]
    assert ch[0]["kind_label"] == "labels declared independent"
    assert tuple(st.one("SELECT basis, by FROM label_independence WHERE record_id=4")) == (BASIS, "Owner")
    journal.undo(proj, ch[0]["change_id"], author="Owner")
    assert not _ind(proj, "validation", 4, "R1")
    # the page's twin gives the same result; an empty basis is refused
    c = _client(proj)
    assert c.post("/api/labels/independence", json={"sample": "validation", "reviewer": "R1",
                                                    "basis": "  "}).status_code == 400
    j = c.post("/api/labels/independence", json={"sample": "validation", "reviewer": "R1", "basis": BASIS})
    assert j.status_code == 200 and j.json()["declared"] == 1 and j.json()["change_id"]
    assert st.scalar("SELECT by FROM label_independence WHERE record_id=4") == "Owner"
    # a later save of the label removes its basis in the same change, and Undo brings both back
    lb.label(proj, "validation", 4, "R1", "exclude", made_in="unblinded queue")
    assert not st.scalar("SELECT COUNT(*) FROM label_independence") and not _ind(proj, "validation", 4, "R1")
    journal.undo(proj, journal.changes(proj, kind="label")[0]["change_id"], author="R1")
    assert _ind(proj, "validation", 4, "R1") and _made_in(proj, "validation", 4, "R1") == "wider"
    # --independent on the imports
    f = tmp_path / "file.csv"
    f.write_text("record_id,decision\n5,include\n", encoding="utf-8")
    assert _cli(proj, "label", "import", "validation", str(f), "--reviewer", "R3", "--independent", BASIS).exit_code == 0
    assert _made_in(proj, "validation", 5, "R3") == "file import" and _ind(proj, "validation", 5, "R3")
    g = tmp_path / "sample.csv"
    g.write_text("title,decision,band,weight\nSynthetic paper 7,include,S1,2\n", encoding="utf-8")
    r = _cli(proj, "sample", "import", "elsewhere", str(g), "--reviewer", "R4", "--independent", BASIS, "--by", "Owner")
    assert r.exit_code == 0, r.output
    assert _ind(proj, "elsewhere", 7, "R4")


def test_label_callers_name_made_in():
    """Every call of label() and _save_label() under wider/ names its made_in value (D10)."""
    root = Path(__file__).resolve().parents[2] / "wider"
    bad = []
    for f in root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                fn = node.func   # a bare name, or the labels module by its usual names (streams.label is another)
                name = (fn.attr if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                        and fn.value.id in ("lb", "labels") else fn.id if isinstance(fn, ast.Name) else None)
                if name in ("label", "_save_label") and not any(k.arg == "made_in" for k in node.keywords):
                    bad.append(f"{f.relative_to(root)}:{node.lineno}")
    assert not bad, bad


def test_a_later_blind_save_never_raises_independence(proj):
    st = proj.store
    lb.label(proj, "validation", 4, "R1", "include", made_in="unblinded queue")
    lb.label(proj, "validation", 4, "R1", "exclude", made_in="blind queue")
    assert _made_in(proj, "validation", 4, "R1") == "unblinded queue"
    lb.add_reviewer(proj, "R1")
    st.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at) VALUES(2,5,'R1','include','t')")
    lb.label(proj, "validation", 5, "R1", "exclude", made_in="blind queue")
    assert _made_in(proj, "validation", 5, "R1") == "wider"
    # an independent label saved again outside a blind queue is no longer independent
    lb.label(proj, "validation", 6, "R1", "include")
    lb.label(proj, "validation", 6, "R1", "exclude", made_in="unblinded queue")
    assert _made_in(proj, "validation", 6, "R1") == "unblinded queue"
    # another reviewer's blind label makes the record independent
    lb.label(proj, "validation", 4, "R2", "include")
    assert _ind(proj, "validation", 4, "R2")


def test_repeat_package_label_keeps_independence(tmp_path):
    p = _pkg_project(tmp_path, n_pos=6, n_exc=6)
    ids = [r[0] for r in p.store.query("SELECT record_id FROM labels WHERE reviewer_id='R1' ORDER BY record_id LIMIT 4")]
    lb.add_reviewer(p, "R2")
    for rid in ids[:2]:   # saved before provenance was recorded
        p.store.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at,made_in) "
                        "SELECT sample_id,?,'R2','exclude','2026-10-01T09:00:00+00:00','wider' FROM samples "
                        "WHERE name='validation'", (rid,))
    for rid in ids[2:]:   # made in a blind queue
        lb.label(p, "validation", rid, "R2", "exclude")
    P = pk.make(p, "R2", ["validation"], "second", earlier="re-review")
    pid = P["package_id"]
    reps = {it["record_id"] for it in pk.items(p, pid) if it["repeat"]}
    assert set(ids) <= reps
    pk.import_file(p, _write(tmp_path, _export(p, pid, n=P["n_items"])))
    assert all(_made_in(p, "validation", r, "R2") == pid for r in ids)
    assert not _ind(p, "validation", ids[0], "R2")   # a repeat review does not restore independence
    assert _ind(p, "validation", ids[2], "R2")       # it keeps the independence the replaced label had
    # a blind save, or a re-check save, over a repeat label that is not independent writes the replaced value
    lb.label(p, "validation", ids[0], "R2", "maybe")
    assert _made_in(p, "validation", ids[0], "R2") == "wider" and not _ind(p, "validation", ids[0], "R2")
    lb.label(p, "validation", ids[2], "R2", "maybe")
    assert _made_in(p, "validation", ids[2], "R2") == "blind queue"
    # a basis stated before the package import counts for the replaced label
    q = _pkg_project(tmp_path, "q", n_pos=4, n_exc=4)
    rid = q.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' ORDER BY record_id")
    lb.add_reviewer(q, "R2")
    q.store.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at,made_in) "
                    "SELECT sample_id,?,'R2','exclude','2026-10-01T09:00:00+00:00','wider' FROM samples "
                    "WHERE name='validation'", (rid,))
    lb.declare_independent(q, "validation", "R2", BASIS, by="Owner")
    Q = pk.make(q, "R2", ["validation"], "second", earlier="re-review")
    pk.import_file(q, _write(tmp_path, _export(q, Q["package_id"], n=Q["n_items"]), "q.json"))
    assert not q.store.scalar("SELECT COUNT(*) FROM label_independence")   # the save dropped the basis
    assert _ind(q, "validation", rid, "R2")


# ----- 2.2 / 2.3 completeness (D3) -----
NOT_IND_LINE = ("1 record has no independent label (made in an unblinded queue, imported, or saved before provenance "
                "was recorded)")


def _twenty(tmp_path):
    """``validation`` with 20 records as in the spec scenario "What is left": 14 labelled by R1 in the blind queue,
    the 15th only in the unblinded queue, the 16th include by R1 and exclude by R2, the 17th the same but
    adjudicated, 3 unlabelled. Returns the project and the records in queue order."""
    p = _pkg_project(tmp_path, "c", n_dev=3, n_pos=10, n_exc=10, label_val=False)
    v = [r[0] for r in p.store.query("SELECT record_id FROM sample_rows sr JOIN samples s USING(sample_id) "
                                     "WHERE s.name='validation' ORDER BY queue_pos")]
    for rid in v[:14]:
        lb.label(p, "validation", rid, "R1", "exclude")
    lb.label(p, "validation", v[14], "R1", "exclude", made_in="unblinded queue")
    for rid in v[15:17]:
        lb.label(p, "validation", rid, "R1", "include")
        lb.label(p, "validation", rid, "R2", "exclude")
    lb.adjudicate(p, "validation", v[16], "exclude", by="Owner")
    return p, v


def test_completeness_contract(tmp_path):
    p, v = _twenty(tmp_path)
    c = lb.completeness(p, "validation")
    assert {"sample", "kind", "complete", "total", "final", "missing", "open", "sealed", "packages_out", "no_label",
            "not_independent", "rule", "lines"} <= set(c) and "disagreements" not in c
    assert (c["sample"], c["kind"], c["complete"], c["total"], c["final"]) == ("validation", "probability", False, 20, 15)
    assert c["missing"] == [v[14], v[15], *v[17:]] and c["no_label"] == v[17:]
    assert c["not_independent"] == [v[14]] and c["open"] == [v[15]] and c["sealed"] == [] and c["packages_out"] == []
    assert c["lines"] == ["3 of 20 records have no label", NOT_IND_LINE,
                          "1 record has labels that disagree and is not adjudicated"]
    assert c["rule"].startswith("A record is final when")
    for rid in v[17:]:
        lb.label(p, "validation", rid, "R1", "exclude")
    lb.label(p, "validation", v[14], "R2", "exclude")
    lb.adjudicate(p, "validation", v[15], "include", by="Owner")
    c = lb.completeness(p, "validation")
    assert c["complete"] and c["lines"] == [] and c["final"] == 20
    # a purposive sample counts any label: the unblinded one too
    p.store.execute("UPDATE samples SET kind='purposive' WHERE name='validation'")
    lb.label(p, "validation", v[0], "R3", "exclude", made_in="unblinded queue")
    assert lb.completeness(p, "validation")["complete"]
    p.store.execute("UPDATE samples SET kind='probability' WHERE name='validation'")
    # a sealed record is final through its reference, whatever a later label says
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    p.store.execute("INSERT INTO label_seals(sample_id,record_id,first_look,last_look,first_change_id,last_change_id,"
                    "sealed_at,decision,source,sealed_decision) VALUES(?,?,1,1,0,0,'t','exclude','R1','exclude')",
                    (sid, v[1]))
    lb.label(p, "validation", v[1], "R4", "include")
    c = lb.completeness(p, "validation")
    assert c["complete"] and c["sealed"] == [v[1]] and c["open"] == []
    # a sample with no record
    p.store.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('empty','probability','{}',0,'t')")
    e = lb.completeness(p, "empty")
    assert not e["complete"] and e["total"] == 0 and e["lines"] == ["the sample has no records"]
    with pytest.raises(KeyError):
        lb.completeness(p, "nope")


def test_completeness_min_reviewers(tmp_path):
    p, v = _twenty(tmp_path)
    c = lb.completeness(p, "validation", min_reviewers=2)
    assert v[0] in c["missing"] and v[16] not in c["missing"]   # one label is not enough; an adjudication is
    lb.label(p, "validation", v[0], "R2", "exclude")
    c = lb.completeness(p, "validation", min_reviewers=2)
    assert v[0] not in c["missing"] and "14 records have labels of fewer than 2 reviewers and are not adjudicated" in c["lines"]
    assert "agree" in c["rule"] and "at least 2 reviewers" in c["rule"]
    assert lb.completeness(p, "dev", min_reviewers=2)["no_label"] == lb.completeness(p, "dev")["no_label"]


def _decisions_as_r1(p, pid, n):
    """A decisions file deciding the first ``n`` items as R1 labelled them, so no disagreement comes in."""
    its = pk.items(p, pid)
    dec = {i: p.store.scalar("SELECT decision FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id='R1'",
                             (it["sample_id"], it["record_id"])) for i, it in enumerate(its[:n])}
    return _export(p, pid, dec)


def test_completeness_validation_package_out(tmp_path):
    p = _pkg_project(tmp_path, "o", n_dev=4, n_pos=10, n_exc=10)
    lb.declare_independent(p, "validation", "R1", BASIS, by="Owner")
    for rid in range(1, 5):
        lb.label(p, "dev", rid, "R1", "exclude")
    assert lb.completeness(p, "validation")["complete"]
    P = pk.make(p, "R3", ["validation"], "second")
    pk.make(p, "R2", ["dev"], "independent")
    c = lb.completeness(p, "validation")
    assert not c["complete"] and c["packages_out"] == ["R3"] and c["missing"] == []
    assert c["lines"] == ["R3's review package on validation is not finished"]
    assert lb.completeness(p, "dev")["packages_out"] == ["R2"]   # development packages too since ui-redesign 3.0
    half = P["n_items"] // 2
    pk.import_file(p, _write(tmp_path, _decisions_as_r1(p, P["package_id"], half)))
    n_r3 = p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='R3'")
    assert n_r3 == half and lb.completeness(p, "validation")["packages_out"] == ["R3"]
    pk.withdraw(p, P["package_id"], "the reviewer left", by="Owner")
    c = lb.completeness(p, "validation")
    assert c["complete"] and c["packages_out"] == [] and c["final"] == c["total"]
    assert p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='R3'") == n_r3   # the withdraw keeps them
    # a later import that decides every item also finishes a package
    Q = pk.make(p, "R4", ["validation"], "second")
    assert lb.completeness(p, "validation")["packages_out"] == ["R4"]
    pk.import_file(p, _write(tmp_path, _decisions_as_r1(p, Q["package_id"], Q["n_items"]), "q.json"))
    assert lb.completeness(p, "validation")["complete"]


def test_completeness_in_sample_status_and_api(tmp_path):
    p, v = _twenty(tmp_path)
    want = {"complete": False, "total": 20, "final": 15, "n_missing": 5, "n_no_label": 3, "n_not_independent": 1,
            "n_open": 1, "n_sealed": 0, "packages_out": []}
    r = _cli(p, "sample", "status", "--json")
    assert r.exit_code == 0, r.output
    cli = json.loads(r.output)["samples"]
    api = _client(p).get("/api/samples").json()["samples"]
    for out in (cli, api):
        for name in ("dev", "validation"):   # every sample kind
            assert {"completeness", "provenance"} <= set(out[name])
            assert not {"missing", "no_label", "not_independent", "open", "sealed"} & set(out[name]["completeness"])
        assert {k: out["validation"]["completeness"][k] for k in want} == want
        assert out["validation"]["provenance"]["by_made_in"] == {"blind queue": 18, "unblinded queue": 1}
    assert cli["validation"]["completeness"] == api["validation"]["completeness"]
    text = _cli(p, "sample", "status").output
    assert "3 of 20 records have no label" in text


# ----- 3.1 corrections on locked samples (D1, D2) -----

def _lab(p, sample, rid, who):
    r = p.store.one("SELECT l.decision, l.reason FROM labels l JOIN samples s ON s.sample_id=l.sample_id "
                    "WHERE s.name=? AND l.record_id=? AND l.reviewer_id=?", (sample, rid, who))
    return tuple(r) if r else None


def _last(p):
    return journal.changes(p)[0]


def test_change_on_a_locked_sample_needs_a_correction_reason(proj):
    lb.label(proj, "dev", 1, "R1", "include", made_in="unblinded queue")
    lb.lock(proj, "dev")
    n = len(journal.changes(proj))
    with pytest.raises(lb.CorrectionNeeded,
                       match="^sample 'dev' is locked: a change to a saved label needs a reason for the correction$"):
        lb.label(proj, "dev", 1, "R1", "exclude", made_in="unblinded queue")
    with pytest.raises(lb.CorrectionNeeded):
        lb.label(proj, "dev", 1, "R1", "exclude", made_in="unblinded queue", correction_reason="   ")
    with pytest.raises(ValueError, match="at most 500 characters"):
        lb.label(proj, "dev", 1, "R1", "exclude", made_in="unblinded queue", correction_reason="x" * 501)
    assert _lab(proj, "dev", 1, "R1") == ("include", "") and len(journal.changes(proj)) == n
    assert not lb.corrections(proj, "dev")
    # additions stay open while locked: a new record, a new reviewer
    lb.label(proj, "dev", 2, "R1", "maybe", made_in="unblinded queue")
    lb.label(proj, "dev", 1, "R2", "exclude", made_in="unblinded queue")
    assert _last(proj)["kind"] == "label"
    lb.label(proj, "dev", 1, "R1", "exclude", reason="a", made_in="unblinded queue", correction_reason="x" * 500)
    assert _lab(proj, "dev", 1, "R1") == ("exclude", "a")
    (c,) = lb.corrections(proj, "dev")
    assert (c["target"], c["reviewer"], c["old_decision"], c["new_decision"], c["new_reason"], c["locked"],
            c["sealed"], c["author"]) == ("label", "R1", "include", "exclude", "a", 1, 0, "R1")
    assert c["why"] == "x" * 500 and c["old_by"] == "R1"


def test_identical_save_writes_nothing(proj):
    lb.label(proj, "dev", 1, "R1", "include", reason="r", labels={"a": "1"}, note="n", made_in="unblinded queue")
    lb.lock(proj, "dev")
    n = len(journal.changes(proj))
    lb.label(proj, "dev", 1, "R1", "include", reason="r", labels={"a": "1"}, note="n", made_in="file import")
    assert len(journal.changes(proj)) == n and _made_in(proj, "dev", 1, "R1") == "unblinded queue"
    for change in ({"reason": "s"}, {"labels": {"a": "2"}}, {"note": "m"}):
        args = {"reason": "r", "labels": {"a": "1"}, "note": "n"} | change
        with pytest.raises(lb.CorrectionNeeded):
            lb.label(proj, "dev", 1, "R1", "include", made_in="unblinded queue", **args)
    # unlocked, an equal save behaves as before: it rewrites made_in (F16)
    lb.label(proj, "validation", 4, "R1", "include", made_in="unblinded queue")
    lb.label(proj, "validation", 4, "R1", "include", made_in="file import")
    assert _made_in(proj, "validation", 4, "R1") == "file import"


def test_adjudication_on_a_locked_sample(proj):
    lb.label(proj, "dev", 1, "R1", "include", made_in="unblinded queue")
    lb.label(proj, "dev", 1, "R2", "exclude", made_in="unblinded queue")
    lb.lock(proj, "dev")
    lb.adjudicate(proj, "dev", 1, "include", reason="fits", by="R3")   # a first adjudication is an addition
    assert _last(proj)["kind"] == "adjudication"
    lb.adjudicate(proj, "dev", 1, "include", reason="fits", by="R3")   # equal: nothing
    with pytest.raises(lb.CorrectionNeeded, match="sample 'dev' is locked"):
        lb.adjudicate(proj, "dev", 1, "exclude", reason="no", by="R3")
    lb.adjudicate(proj, "dev", 1, "exclude", reason="no", by="R3", correction_reason="wrong criterion applied")
    ch = _last(proj)
    assert ch["kind"] == "label_correction" and ch["summary"] == "R3 corrected the adjudication of record 1 in sample dev"
    (c,) = lb.corrections(proj, "dev")
    assert (c["target"], c["reviewer"], c["old_decision"], c["new_decision"], c["old_by"]) == (
        "adjudication", None, "include", "exclude", "R3")
    assert proj.store.scalar("SELECT decision FROM adjudications WHERE record_id=1") == "exclude"


def test_blind_correction_refusal_names_no_sample(proj):
    item = next(r["item"] for r in lb.queue(proj, "R1", ["dev"]) if r["record_id"] == 1)
    lb.label_item(proj, item, "R1", "include")
    lb.lock(proj, "dev")
    item = next(r["item"] for r in lb.queue(proj, "R1", ["dev"]) if r["record_id"] == 1)
    with pytest.raises(lb.CorrectionNeeded) as e:
        lb.label_item(proj, item, "R1", "exclude")
    assert str(e.value) == lb.BLIND_CORRECTION and "dev" not in str(e.value) and "locked" not in str(e.value)
    lb.label_item(proj, item, "R1", "exclude", correction_reason="clicked the wrong button")
    ch = _last(proj)
    assert (ch["kind"], ch["summary"], ch["record_id"]) == ("label_correction", "R1 corrected a label", None)
    assert lb.corrections(proj, "dev")[0]["record_id"] == 1   # the table keeps the record


def test_correction_in_history_and_show_list(proj):
    lb.label(proj, "dev", 1, "R1", "include", made_in="unblinded queue")
    lb.lock(proj, "dev")
    lb.label(proj, "dev", 1, "R1", "exclude", made_in="unblinded queue", correction_reason="typo")
    ch = _last(proj)
    assert ch["kind_label"] == "label correction" and ch["summary"] == "R1 corrected record 1 in sample dev"
    assert ch["author"] == "R1" and ch["record_id"] == 1
    assert "while_locked" not in ch["touches"] and "blind" not in ch["touches"]
    assert {"labels", "label_corrections"} <= set(ch["touches"])
    assert "typo" not in json.dumps(ch) and "exclude" not in ch["summary"]
    assert journal.KIND_LABELS["label_correction"] == "label correction"
    r = _client(proj).get("/api/history").json()
    rows = r["changes"] if isinstance(r, dict) else r
    assert any(x["kind"] == "label_correction" and x["kind_label"] == "label correction" for x in rows)


def test_redraw_and_extension_still_refused_when_locked(tmp_path):
    from wider.sample import design
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    for i in range(1, 6):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at,locked) "
                    "VALUES(1,'dev','purposive','{}',2,'x',1)")
    with pytest.raises(ValueError, match="locked"):
        design.extend_development(p, "dev")
    with pytest.raises(ValueError, match="locked"):
        design._store_sample(p, "dev", "purposive", None, [], 1, {})   # a redraw under the same name


def test_correction_cli_and_route_same(proj, tmp_path):
    for rid in (1, 2):
        lb.label(proj, "dev", rid, "R1", "include", made_in="unblinded queue")
    lb.label(proj, "dev", 1, "R2", "exclude", made_in="unblinded queue")
    lb.adjudicate(proj, "dev", 1, "include", by="R3")
    item = next(r["item"] for r in lb.queue(proj, "R1", ["dev"]) if r["record_id"] == 2)
    lb.lock(proj, "dev")
    c = _client(proj, "R1")
    # the page: 409 with correction_needed, nothing written; then the same save with a reason
    r = c.post("/api/labels", json={"sample_id": 1, "record_id": 1, "reviewer": "R1", "decision": "exclude"})
    assert r.status_code == 409 and r.json() == {
        "detail": "sample 'dev' is locked: a change to a saved label needs a reason for the correction",
        "correction_needed": True}
    r = c.post("/api/labels", json={"item": item, "reviewer": "R1", "decision": "maybe"})
    assert r.status_code == 409 and r.json()["detail"] == lb.BLIND_CORRECTION
    r = c.post("/api/labels/adjudicate", json={"sample": "dev", "record_id": 1, "decision": "maybe", "by": "R3"})
    assert r.status_code == 409 and r.json()["correction_needed"] is True
    assert not lb.corrections(proj, "dev")
    assert c.post("/api/labels", json={"sample_id": 1, "record_id": 1, "reviewer": "R1", "decision": "exclude",
                                       "correction_reason": "page"}).status_code == 200
    assert c.post("/api/labels", json={"item": item, "reviewer": "R1", "decision": "maybe",
                                       "correction_reason": "page"}).status_code == 200
    assert c.post("/api/labels/adjudicate", json={"sample": "dev", "record_id": 1, "decision": "maybe", "by": "R3",
                                                  "correction_reason": "page"}).status_code == 200
    page = [(x["target"], x["record_id"], x["reviewer"], x["new_decision"]) for x in lb.corrections(proj, "dev")]
    assert page == [("label", 1, "R1", "exclude"), ("label", 2, "R1", "maybe"), ("adjudication", 1, None, "maybe")]
    # the command line: the same refusals (exit 1, nothing saved from the file) and the same rows with a reason
    f = tmp_path / "labels.csv"
    f.write_text("record_id,decision\n1,include\n2,include\n3,exclude\n", encoding="utf-8")
    r = _cli(proj, "label", "import", "dev", str(f), "--reviewer", "R1", "--overwrite")
    assert r.exit_code == 1 and "2 rows change a saved label on the locked sample dev; give --correction-reason" in \
        " ".join(r.output.split())
    assert _lab(proj, "dev", 3, "R1") is None   # the addition in the same file waits too
    q = tmp_path / "queue.csv"
    q.write_text(f"item,decision\n{item},exclude\n", encoding="utf-8")
    r = _cli(proj, "label", "import", "--queue", str(q))
    assert r.exit_code == 1 and "1 row changes a saved label here; give --correction-reason" in " ".join(r.output.split())
    assert "dev" not in r.output
    r = _cli(proj, "label", "adjudicate", "dev", "1", "exclude", "--by", "R3")
    assert r.exit_code == 1 and "needs a reason for the correction" in r.output
    assert len(lb.corrections(proj, "dev")) == 3
    assert _cli(proj, "label", "import", "dev", str(f), "--reviewer", "R1", "--overwrite",
                "--correction-reason", "cli").exit_code == 0
    assert _cli(proj, "label", "import", "--queue", str(q), "--correction-reason", "cli").exit_code == 0
    assert _cli(proj, "label", "adjudicate", "dev", "1", "exclude", "--by", "R3",
                "--correction-reason", "cli").exit_code == 0
    cli = [(x["target"], x["record_id"], x["reviewer"], x["new_decision"], x["why"]) for x in lb.corrections(proj, "dev")[3:]]
    assert cli == [("label", 1, "R1", "include", "cli"), ("label", 2, "R1", "include", "cli"),
                   ("label", 2, "R1", "exclude", "cli"), ("adjudication", 1, None, "exclude", "cli")]
    assert _lab(proj, "dev", 3, "R1") == ("exclude", "")
    assert [x["kind"] for x in journal.changes(proj)[:5]].count("label_correction") == 4   # record 3: an addition


# ----- 3.3 the corrections listed and counted (D2) -----

def _corrected(p):
    lb.label(p, "dev", 1, "R1", "include", labels={"a": "1"}, made_in="unblinded queue")
    lb.label(p, "dev", 1, "R2", "exclude", made_in="unblinded queue")
    lb.adjudicate(p, "dev", 1, "include", by="R3")
    lb.lock(p, "dev")
    lb.label(p, "dev", 1, "R1", "exclude", reason="off topic", labels={"a": "2"}, made_in="unblinded queue",
             correction_reason="ticked the wrong box")
    lb.adjudicate(p, "dev", 1, "exclude", by="R3", correction_reason="follows the corrected label")


def test_revisions_list_corrections(proj):
    _corrected(proj)
    r = _cli(proj, "label", "revisions", "dev")
    out = " ".join(r.output.split())
    assert r.exit_code == 0 and "no re-checked labels" in out
    assert ("record 1 (R1): include to exclude; correction; reason: off topic; ticks {\"a\": \"1\"} to {\"a\": \"2\"}; "
            "why: ticked the wrong box; by R1 at") in out
    assert "record 1 (adjudication): include to exclude; correction; why: follows the corrected label; by R3" in out
    j = json.loads(_cli(proj, "label", "revisions", "dev", "--json").output)
    assert j["revisions"] == [] and [c["target"] for c in j["corrections"]] == ["label", "adjudication"]
    assert j["corrections"][0]["why"] == "ticked the wrong box"


def test_corrections_on_the_sample_card(proj):
    from tests.unit.test_sample_extend_node import page
    _corrected(proj)
    api = _client(proj).get("/api/samples").json()["samples"]
    cli = json.loads(_cli(proj, "sample", "status", "--json").output)["samples"]
    assert api["dev"]["corrections"] == cli["dev"]["corrections"] == 2
    assert api["validation"]["corrections"] == cli["validation"]["corrections"] == 0
    # the page never carries a correction's decision or reason (a blind second reviewer may open it)
    blob = json.dumps(api)
    assert "ticked the wrong box" not in blob and "follows the corrected label" not in blob
    out = page("""
const res = { two: SampleExtend.correctionsHtml("dev", 2), one: SampleExtend.correctionsHtml("dev", 1),
              none: SampleExtend.correctionsHtml("dev", 0) };
process.stdout.write(JSON.stringify(res));""")
    r = out if isinstance(out, dict) else json.loads(out)
    # the count in page text, the command that lists them in the card's closed "Command line" fold
    assert r["two"].startswith("<p data-corrections>2 corrected labels</p><details")
    assert "<code>wider label revisions dev</code>" in r["two"]
    assert r["one"].startswith("<p data-corrections>1 corrected label</p>") and r["none"] == ""


def test_corrections_counted_in_the_report(proj):
    from wider.recall.report import COUNTED_ONLY, interventions
    _corrected(proj)
    assert {"label_correction", "label_provenance"} <= COUNTED_ONLY
    iv = interventions(proj)
    k = {x["kind"]: x for x in iv["by_kind"]}
    assert k["label_correction"]["count"] == 2 and k["label_correction"]["label"] == "label correction"
    assert not [e for e in iv["entries"] if e["kind"] in ("label_correction", "label_provenance")]


# ----- 3.4 queues (D8) -----

def _seal(p, sample_id, rid, decision="include"):
    p.store.execute("INSERT INTO label_seals(sample_id,record_id,sealed_at,first_change_id,last_change_id,decision) "
                    "VALUES(?,?,'t',1,1,?)", (sample_id, rid, decision))


def test_blind_queue_leaves_sealed_records_out(proj):
    _seal(proj, 2, 4)
    lb.label(proj, "validation", 5, "R1", "include", made_in="unblinded queue")
    _seal(proj, 2, 5)
    # merged blind queue: dev (open) and validation's record 6 (not sealed) stay; sealed 4 and 5 are left out
    rows, left = lb.queue_left_out(proj, "R1")
    assert {r["record_id"] for r in rows} == {1, 2, 3, 6} and left == 1   # record 5 is labelled: not counted
    r = _client(proj, "R1").get("/api/labels/queue", params={"reviewer": "R1"})
    assert r.status_code == 200 and r.headers["X-Left-Out"] == "1"
    assert {x["record_id"] for x in r.json()} == {1, 2, 3, 6} and "validation" not in r.text
    # a queue of one sample, and the unblinded queue, list them
    assert {r["record_id"] for r in lb.queue(proj, "R1", ["validation"])} == {4, 5, 6}
    assert {r["record_id"] for r in lb.queue(proj, "R1", blind=False)} == {1, 2, 3, 4, 5, 6}
    r = _client(proj, "R1").get("/api/labels/queue", params={"reviewer": "R1", "samples": "validation"})
    assert "X-Left-Out" not in r.headers
    # a locked sample beside an open one is left out of the merged queue as before
    proj.store.execute("DELETE FROM label_seals")
    lb.lock(proj, "dev")
    rows, left = lb.queue_left_out(proj, "R1")
    assert {r["record_id"] for r in rows} == {4, 5, 6} and left == 3
    assert {r["record_id"] for r in lb.queue(proj, "R1", ["dev"])} == {1, 2, 3}


def test_recheck_and_second_queues_serve_locked_samples(proj):
    proj.store.execute("UPDATE samples SET kind='probability' WHERE name='dev'")   # two probability samples
    for rid in (4, 5):
        lb.label(proj, "validation", rid, "R1", "include", made_in="unblinded queue")
    lb.label(proj, "validation", 6, "R1", "exclude", made_in="unblinded queue")
    open_q = lb.second_reviewer_queue(proj, "R2", "validation")
    lb.lock(proj, "validation")
    q = lb.second_reviewer_queue(proj, "R2", "validation")
    assert q["n"] == open_q["n"] == 3 and not any("locked" in x for x in q["lines"])
    lb.label_item(proj, q["items"][0]["item"], "R2", "maybe")   # an addition: no reason
    assert _last(proj)["kind"] == "label" and not lb.corrections(proj, "validation")
    assert not any("locked" in x for x in lb.recheck_queue(proj, "R1")["lines"])


# ----- 3.6 undo on a locked sample (D7) -----

def _undo_why(p, change_id):
    return next(c for c in journal.changes(p) if c["change_id"] == change_id)["why_not"]


def test_undo_before_lock_refused_after_lock_allowed(proj):
    lb.label(proj, "dev", 1, "R1", "include")
    before = _last(proj)["change_id"]
    lb.lock(proj, "dev", by="Owner")
    lb.label(proj, "dev", 2, "R1", "exclude", reason="a")              # an addition while locked
    added = _last(proj)["change_id"]
    lb.label(proj, "dev", 1, "R1", "exclude", reason="a", correction_reason="read again")   # a correction
    corrected = _last(proj)["change_id"]
    why = "sample 'dev' was locked after this change: correct the label instead, with a reason"
    assert _undo_why(proj, before) == why
    with pytest.raises(ValueError, match="correct the label instead"):
        journal.undo(proj, before, author="R1")
    assert _undo_why(proj, corrected) is None and _undo_why(proj, added) is None
    journal.undo(proj, corrected, author="R1")
    assert _lab(proj, "dev", 1, "R1")[0] == "include"
    assert not proj.store.scalar("SELECT COUNT(*) FROM label_corrections")   # the undo removes the correction row too
    journal.undo(proj, added, author="R1")
    assert _lab(proj, "dev", 2, "R1") is None
    # a later lock: the save made under the earlier lock is from before the current one
    lb.label(proj, "dev", 3, "R1", "include")
    under_first = _last(proj)["change_id"]
    lb.unlock(proj, "dev", by="Owner")
    lb.lock(proj, "dev", by="Owner")
    assert _undo_why(proj, under_first) == why
    # a sample imported locked (no lock in History): a label added afterwards undoes as usual
    proj.store.execute("UPDATE samples SET locked=1 WHERE name='validation'")
    lb.label(proj, "validation", 4, "R1", "include")
    journal.undo(proj, _last(proj)["change_id"], author="R1")
    assert _lab(proj, "validation", 4, "R1") is None


def test_undo_then_redo_on_a_locked_sample(proj):
    lb.lock(proj, "dev", by="Owner")
    lb.label(proj, "dev", 1, "R1", "include")
    cid = _last(proj)["change_id"]
    u = journal.undo(proj, cid, author="R1")["change_id"]
    assert _lab(proj, "dev", 1, "R1") is None
    redo = journal.undo(proj, u, author="R1")["change_id"]   # redo: the original change was made while locked
    assert _lab(proj, "dev", 1, "R1")[0] == "include"
    lb.unlock(proj, "dev", by="Owner")
    lb.lock(proj, "dev", by="Owner")      # locked again after it: the redo is now from before the lock
    assert "was locked after this change" in _undo_why(proj, redo)


def test_blind_undo_refusal_names_no_sample(proj):
    lb.label(proj, "dev", 1, "R1", "include", queue_mode="blind")
    cid = _last(proj)["change_id"]
    lb.lock(proj, "dev", by="Owner")
    assert _undo_why(proj, cid) == journal.BLIND_LOCKED


# ----- 3.5 packages on a locked sample (D2, D8, F3) -----

def _dev_r1(tmp_path, n=10):
    p = _pkg_project(tmp_path, label_val=False)
    for rid in range(1, n + 1):
        lb.label(p, "dev", rid, "R1", "include")
    return p


def _r2(p):
    return {r[0]: r[1] for r in p.store.query(
        "SELECT l.record_id, l.decision FROM labels l JOIN samples s ON s.sample_id=l.sample_id "
        "WHERE s.name='dev' AND l.reviewer_id='R2'")}


def test_package_on_a_locked_sample(tmp_path):
    p = _dev_r1(tmp_path)
    lb.lock(p, "dev", by="Owner")
    out = _cli(p, "blinding", "package", "R2", "--sample", "dev", "--mode", "independent", "--out", str(tmp_path / "r2.html"))
    assert out.exit_code == 0 and "is locked" not in out.output, out.output
    assert pk.packages(p)[0]["n_items"] == 20
    r = _client(p).post("/api/blinding/package", json={"reviewer": "R3", "samples": ["dev"], "mode": "independent"})
    assert r.status_code == 200 and "is locked" not in r.text, r.text
    # a package made while open, imported after the lock: no reason asked, one undoable change
    q = _dev_r1(tmp_path / "q")
    pid = pk.make(q, "R2", ["dev"], "independent")["package_id"]
    lb.lock(q, "dev", by="Owner")
    res = pk.import_file(q, _write(tmp_path, _export(q, pid, n=20)))
    assert res["imported"] == 20 and len(_r2(q)) == 20
    items = json.loads(q.store.scalar("SELECT items FROM history WHERE change_id=?", (res["change_id"],)))
    assert {"t": "while_locked"} in items
    journal.undo(q, res["change_id"], author="Owner", other=True)
    assert not _r2(q)


def test_later_export_on_a_locked_sample_is_recorded(tmp_path):
    p = _dev_r1(tmp_path)
    pid = pk.make(p, "R2", ["dev"], "independent")["package_id"]
    order = [it["record_id"] for it in pk.items(p, pid)]
    pk.import_file(p, _write(tmp_path, _export(p, pid, n=20, seq=1), "a.json"))
    second = _export(p, pid, n=20, seq=2)
    second["items"][order.index(4)]["decision"] = "exclude"
    pk.import_file(p, _write(tmp_path, second, "b.json"))
    assert not p.store.scalar("SELECT COUNT(*) FROM label_revisions WHERE cause='package update'")   # still open
    lb.lock(p, "dev", by="Owner")
    third = _export(p, pid, n=20, seq=3)   # record 4 back to include, record 5 now exclude, the rest unchanged
    third["items"][order.index(5)]["decision"] = "exclude"
    pk.import_file(p, _write(tmp_path, third, "c.json"))
    assert _r2(p)[4] == "include" and _r2(p)[5] == "exclude"
    revs = json.loads(_cli(p, "label", "revisions", "dev", "--json").output)["revisions"]
    upd = sorted((r["record_id"], r["old_decision"], r["new_decision"], r["package_id"])
                 for r in revs if r["cause"] == "package update")
    assert upd == [(4, "exclude", "include", pid), (5, "include", "exclude", pid)]


# ----------------------------------------------------------------- 4.1 recall revealed only on a complete sample (D4)
NOTICE_HEAD = "Recall on validation is revealed once every sampled record has a final label. Not yet: "
NOTICE_TAIL = ". Nothing was estimated or recorded."


def _drawn_open(tmp_path, monkeypatch, unlabelled=3):
    """The 120-record validation sample of ``_drawn``: ``unlabelled`` records without a label and one more where R2
    disagrees with r1. Returns the project and the records in queue order."""
    from tests.unit.test_package import _codebook
    from tests.unit.test_validation_suggest import _drawn
    from wider.rank import score

    def no_ranking_check(project):   # the synthetic ranking has no embedding set behind it
        raise ValueError("no ranking check")
    monkeypatch.setattr(score, "validate", no_ranking_check)
    p = _drawn(tmp_path, unlabelled=unlabelled)
    _codebook(p, "frozen")
    v = [r[0] for r in p.store.query("SELECT record_id FROM sample_rows ORDER BY queue_pos")]
    d = p.store.scalar("SELECT decision FROM labels WHERE record_id=?", (v[unlabelled],))
    lb.label(p, "validation", v[unlabelled], "R2", "exclude" if d == "include" else "include")
    return p, v


def _flat(text):
    return " ".join(text.split())


def _snapshot(p):
    """Row counts of every table but the journal's, and the meta rows: what a refused estimate must leave alone."""
    names = [r[0] for r in p.store.query("SELECT name FROM sqlite_master WHERE type='table'")]
    counts = {n: p.store.scalar(f'SELECT COUNT(*) FROM "{n}"') for n in names}
    return counts, [tuple(r) for r in p.store.query("SELECT * FROM meta ORDER BY 1")]


def test_recall_refused_until_complete(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate
    from wider.recall.validation import LabelsIncomplete

    p, v = _drawn_open(tmp_path, monkeypatch)
    notice = (NOTICE_HEAD + "3 of 120 records have no label; 1 record has labels that disagree and is not adjudicated"
              + NOTICE_TAIL)
    with pytest.raises(LabelsIncomplete) as e:
        estimate(p, "validation", "main", B=20)
    assert e.value.notice == notice and not isinstance(e.value, ValueError)
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "20")
    assert out.exit_code == 1 and _flat(out.output) == notice
    js = _cli(p, "recall", "main", "--sample", "validation", "-B", "20", "--json")
    assert js.exit_code == 1 and json.loads(js.output)["notice"] == notice
    r = _client(p).get("/api/recall", params={"protocol": "main", "B": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["labels_incomplete"] is True and body["notice"] == notice and "variants" not in body
    c = body["completeness"]
    assert (c["complete"], c["total"], c["n_no_label"], c["n_open"]) == (False, 120, 3, 1) and "missing" not in c
    assert json.loads(js.output)["completeness"] == c
    # once the 3 are labelled and the disagreement adjudicated, the same command gives look 1
    for rid in v[:3]:
        lb.label(p, "validation", rid, "r1", "exclude")
    lb.adjudicate(p, "validation", v[3], "include", by="Owner")
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == 0
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "20")
    assert out.exit_code == 0, out.output
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == 1


def test_recall_refusal_records_nothing(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate
    from wider.recall.validation import LabelsIncomplete
    from wider.sample.design import extend_probability

    p, v = _drawn_open(tmp_path, monkeypatch, unlabelled=0)
    lb.adjudicate(p, "validation", v[0], "include", by="Owner")
    assert _cli(p, "recall", "main", "--sample", "validation", "-B", "20").exit_code == 0   # look 1
    status = _flat(_cli(p, "validation", "status").output)
    p.store.execute("UPDATE samples SET locked=0 WHERE name='validation'")
    extend_probability(p, "validation", {"S2": 10}, author="ana")   # ten new records, none labelled
    before = _snapshot(p)
    with pytest.raises(LabelsIncomplete):
        estimate(p, "validation", "main", B=20, choice="used")
    for how in (lambda: _cli(p, "recall", "main", "--sample", "validation", "-B", "20"),
                lambda: _client(p).get("/api/recall", params={"protocol": "main", "B": 20, "choice": "used"})):
        how()
    after = _snapshot(p)
    assert after == before   # no look, choice, stage result, stored line, series or seal
    assert _flat(_cli(p, "validation", "status").output) == status


def test_recall_refusal_cli_api_report(tmp_path, monkeypatch):
    from wider.recall.outputs import methods_draft
    from wider.recall.report import share_bundle

    p, _ = _drawn_open(tmp_path, monkeypatch)
    notice = (NOTICE_HEAD + "3 of 120 records have no label; 1 record has labels that disagree and is not adjudicated"
              + NOTICE_TAIL)
    for args in (("report",), ("report", "--protocol", "main", "--sample", "validation")):
        out = _cli(p, *args)
        assert out.exit_code == 0, out.output
        md = (p.root / "exports" / "report.md").read_text(encoding="utf-8")
        assert f"## Recall (main vs validation)\n\n{notice}\n" in md and "| variant | recall |" not in md
        rep = json.loads((p.root / "exports" / "report.json").read_text(encoding="utf-8"))
        assert rep["recall"]["labels_incomplete"] is True and rep["recall"]["notice"] == notice
    assert json.loads(p.store.get_meta("last_report"))["labels_incomplete"] is True
    dest = share_bundle(p, tmp_path / "bundle")
    assert notice in (dest / "report.md").read_text(encoding="utf-8")
    m = methods_draft(p)
    assert notice in m and "band-weighted recall" not in m
    assert p.store.scalar("SELECT COUNT(*) FROM validation_looks") == 0


def test_recall_refusal_names_both_declaration_paths(tmp_path, monkeypatch):
    p, v = _drawn_open(tmp_path, monkeypatch, unlabelled=0)
    lb.adjudicate(p, "validation", v[0], "include", by="Owner")
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    # 20 labels by r1 saved before provenance was recorded, and one made in the unblinded queue
    p.store.execute(f"UPDATE labels SET made_in='wider' WHERE sample_id=? AND reviewer_id='r1' AND record_id IN "
                    f"({','.join('?' * 20)})", (sid, *v[1:21]))
    p.store.execute("UPDATE labels SET made_in='unblinded queue' WHERE sample_id=? AND reviewer_id='r1' AND record_id=?",
                    (sid, v[21]))
    notice = (NOTICE_HEAD + "21 records have no independent label " + lb.NOT_INDEPENDENT + "; 20 labels by r1 have no "
              "stated independence: declare it on the sample card (Declare labels independent) or with wider label "
              "provenance validation --reviewer r1 --independent BASIS" + NOTICE_TAIL)
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "20")
    assert out.exit_code == 1 and _flat(out.output) == notice
    assert _client(p).get("/api/recall", params={"protocol": "main", "B": 20}).json()["notice"] == notice
    # only unblinded-queue labels: the record-count part, no declaration hint
    p.store.execute("UPDATE labels SET made_in='blind queue' WHERE sample_id=? AND made_in='wider'", (sid,))
    r = _client(p).get("/api/recall", params={"protocol": "main", "B": 20}).json()
    assert r["notice"] == NOTICE_HEAD + "1 record has no independent label " + lb.NOT_INDEPENDENT + NOTICE_TAIL
    assert "declare" not in r["notice"]


# ----- 4.2 seals (D5, D1) -----

def _complete(tmp_path, monkeypatch):
    """The 120-record validation sample, every record final (the disagreement on the first record adjudicated)."""
    p, v = _drawn_open(tmp_path, monkeypatch, unlabelled=0)
    lb.adjudicate(p, "validation", v[0], "include", by="Owner")
    return p, v, p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")


def _seals(p, sid):
    return {r["record_id"]: dict(r) for r in p.store.query("SELECT * FROM label_seals WHERE sample_id=?", (sid,))}


def _r1_record(p, v, decision="include"):
    """A record whose only label is r1's ``decision``, so r1's label is its reference."""
    return next(rid for rid in v[1:] if [tuple(r) for r in p.store.query(
        "SELECT reviewer_id, decision FROM labels WHERE record_id=?", (rid,))] == [("r1", decision)])


def test_look_seals_its_records(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate

    p, v, sid = _complete(tmp_path, monkeypatch)
    assert _seals(p, sid) == {}
    finals = {r["record_id"]: r for r in lb.final_labels(p, "validation")}
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "20")
    assert out.exit_code == 0, out.output
    seals = _seals(p, sid)
    assert set(seals) == set(v) and len(seals) == 120
    top = p.store.scalar("SELECT MAX(change_id) FROM history")
    for rid, s in seals.items():
        assert (s["first_look"], s["last_look"], s["first_change_id"], s["last_change_id"]) == (1, 1, top, top)
        assert s["sealed_at"] and s["decision"] == s["sealed_decision"] == finals[rid]["decision"]
        assert s["source"] == finals[rid]["source"] and s["corrected_at"] is None
    assert seals[v[0]]["source"] == "adjudication"
    assert all(r["sealed"] for r in lb.final_labels(p, "validation"))
    # a supplementary label and its undo, and an unlock, leave every seal in place
    lb.label(p, "validation", v[5], "R3", "maybe")
    journal.undo(p, author="R3")
    p.store.execute("UPDATE samples SET locked=0 WHERE sample_id=?", (sid,))
    assert set(_seals(p, sid)) == set(v)
    # a second look with the same labels takes the same index and moves only the last change id
    estimate(p, "validation", "main", B=20)
    again = _seals(p, sid)
    assert {s["first_change_id"] for s in again.values()} == {top}
    assert {s["last_look"] for s in again.values()} == {1}
    assert {s["last_change_id"] for s in again.values()} == {p.store.scalar("SELECT MAX(change_id) FROM history")}


def test_second_reviewer_label_on_sealed_record_is_an_addition(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate

    p, v, sid = _complete(tmp_path, monkeypatch)
    first = estimate(p, "validation", "main", B=20)
    rid = _r1_record(p, v)
    lb.label(p, "validation", rid, "R2", "exclude")   # no reason: R2 had no label here
    lab = p.store.one("SELECT * FROM labels WHERE record_id=? AND reviewer_id='R2'", (rid,))
    assert lab["decision"] == "exclude" and lab["after_look"] == 1 and lab["updated_at"]
    fin = next(r for r in lb.final_labels(p, "validation") if r["record_id"] == rid)
    assert (fin["decision"], fin["sealed"], fin["open_disagreement"], fin["source"]) == ("include", True, False, "r1")
    assert lb.completeness(p, "validation")["complete"] is True
    second = estimate(p, "validation", "main", B=20)
    assert second["variants"]["strict"]["recall"] == first["variants"]["strict"]["recall"]
    assert second["look"]["index"] == first["look"]["index"] == 1
    # an adjudication on a sealed record corrects the reference: it needs a reason
    out = _cli(p, "label", "adjudicate", "validation", str(rid), "exclude", "--by", "dana")
    assert out.exit_code == 1 and "reason" in out.output
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications WHERE record_id=?", (rid,)) == 0
    with pytest.raises(lb.CorrectionNeeded):
        lb.adjudicate(p, "validation", rid, "exclude", by="dana")
    out = _cli(p, "label", "adjudicate", "validation", str(rid), "exclude", "--by", "dana",
               "--correction-reason", "data entry slip")
    assert out.exit_code == 0, out.output
    s = _seals(p, sid)[rid]
    assert (s["decision"], s["source"], s["corrected_by"], s["sealed_decision"]) == ("exclude", "adjudication", "dana",
                                                                                       "include")
    assert s["corrected_at"]
    assert estimate(p, "validation", "main", B=20)["look"]["index"] == 2
    # the reference's own reviewer corrects it with a reason, and the reference follows; another reviewer's change
    # to their own label never moves it
    other = _r1_record(p, [x for x in v if x != rid])
    with pytest.raises(lb.CorrectionNeeded):
        lb.label(p, "validation", other, "r1", "exclude")
    lb.label(p, "validation", other, "R2", "include")
    lb.label(p, "validation", other, "R2", "exclude", correction_reason="my slip")
    assert _seals(p, sid)[other]["decision"] == "include"
    lb.label(p, "validation", other, "r1", "exclude", correction_reason="data entry slip")
    s = _seals(p, sid)[other]
    assert (s["decision"], s["source"], s["corrected_by"], s["sealed_decision"]) == ("exclude", "r1", "r1", "include")
    journal.undo(p, author="r1")   # the undo of the correction brings the reference back
    assert _seals(p, sid)[other]["decision"] == "include" and _seals(p, sid)[other]["corrected_at"] is None


def test_old_project_counts_as_sealed(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate
    from wider.sample.design import extend_probability

    # "maybe": looks were taken before they were recorded; the labels saved by then are sealed at the first call
    p, v = _drawn_open(tmp_path, monkeypatch, unlabelled=3)
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    p.store.set_meta("recall_looks_before_v7", "2999-01-01T00:00:00+00:00")
    rid = _r1_record(p, v[4:])
    with pytest.raises(lb.CorrectionNeeded):
        lb.label(p, "validation", rid, "r1", "exclude")
    seals = _seals(p, sid)
    assert set(seals) == set(v[4:]) and {s["first_look"] for s in seals.values()} == {None}
    for x in v[:3]:   # first saved after the cut-off: not sealed until a look uses them
        lb.label(p, "validation", x, "r1", "exclude")
    lb.adjudicate(p, "validation", v[3], "include", by="Owner")   # an open disagreement then: not sealed, no reason
    assert set(_seals(p, sid)) == set(v[4:])
    estimate(p, "validation", "main", B=20)
    assert set(_seals(p, sid)) == set(v)
    # a sample drawn after the upgrade is not sealed by the cut-off
    p.store.execute("UPDATE samples SET created_at='2999-06-01T00:00:00+00:00' WHERE sample_id=?", (sid,))
    p.store.execute("DELETE FROM label_seals")
    p.store.execute("DELETE FROM validation_looks")
    assert lb.sealed_records(p, lb.sample_by_name(p, "validation")) == {}

    # "yes": two recorded looks with an extension between them, from before seals were written
    q, w, qid = _complete(tmp_path / "yes", monkeypatch)
    estimate(q, "validation", "main", B=20)
    q.store.execute("UPDATE samples SET locked=0 WHERE sample_id=?", (qid,))
    extend_probability(q, "validation", {"S2": 10}, author="ana")
    new = [r[0] for r in q.store.query("SELECT record_id FROM sample_rows WHERE sample_id=? AND record_id NOT IN "
                                       f"({','.join('?' * len(w))})", (qid, *w))]
    for x in new:
        lb.label(q, "validation", x, "r1", "exclude")
    estimate(q, "validation", "main", B=20, choice="used")
    first_look = q.store.scalar("SELECT MIN(change_id) FROM history WHERE kind='sample_look'")
    top = q.store.scalar("SELECT MAX(change_id) FROM history")
    q.store.execute("DELETE FROM label_seals")   # as an older project left it
    seals = lb.sealed_records(q, lb.sample_by_name(q, "validation"))
    assert set(seals) == set(w) | set(new) and len(new) == 10
    assert {(s["first_change_id"], s["last_change_id"], s["first_look"]) for s in seals.values()} == {(first_look, top, 1)}
    rid = _r1_record(q, w)
    assert lb.sealed_change_line(q, "validation") is None   # no label changed after the older looks
    before_look = q.store.scalar("SELECT MAX(change_id) FROM history WHERE kind IN ('label', 'label_correction') "
                                 "AND change_id<?", (first_look,))
    assert "was sealed after this change" in _undo_why(q, before_look)
    with pytest.raises(lb.CorrectionNeeded):
        lb.label(q, "validation", rid, "r1", "exclude")
    lb.label(q, "validation", rid, "r1", "exclude", correction_reason="data entry slip")
    line = _change_line(1, min(s["sealed_at"] for s in seals.values())[:10])
    assert estimate(q, "validation", "main", B=20, choice="used")["lines"].count(line) == 1
    assert _flat(_cli(q, "validation", "status").output).count(line) == 1


def test_sample_import_keeps_work_added_in_wider(proj, tmp_path):
    f = tmp_path / "s.csv"
    titles = [r["title"] for r in proj.store.query("SELECT title FROM records ORDER BY record_id LIMIT 2")]
    f.write_text("title,decision,band,weight\n" + "".join(f'"{t}",Included,S1,1.0\n' for t in titles), encoding="utf-8")
    assert _cli(proj, "sample", "import", "ext", str(f), "--independent", "labelled blind in Rayyan").exit_code == 0
    s = lb.sample_by_name(proj, "ext")
    assert proj.store.scalar("SELECT COUNT(*) FROM label_independence WHERE sample_id=?", (s["sample_id"],)) == 2
    # nothing was added: a re-import without --independent replaces it and keeps no stated independence
    assert _cli(proj, "sample", "import", "ext", str(f)).exit_code == 0
    s = lb.sample_by_name(proj, "ext")
    assert proj.store.scalar("SELECT COUNT(*) FROM label_independence") == 0
    assert lb.provenance(proj, "ext")["independent"] == 0
    rid = proj.store.scalar("SELECT MIN(record_id) FROM sample_rows WHERE sample_id=?", (s["sample_id"],))
    refusal = "ext has labels or results added in WIDER since it was imported; import the file under another name"

    def refused():
        before = [tuple(r) for t in ("samples", "labels", "adjudications", "label_corrections")
                  for r in proj.store.query(f"SELECT * FROM {t} ORDER BY 1")]
        out = _cli(proj, "sample", "import", "ext", str(f))
        assert out.exit_code == 1 and _flat(out.output) == refusal
        assert before == [tuple(r) for t in ("samples", "labels", "adjudications", "label_corrections")
                          for r in proj.store.query(f"SELECT * FROM {t} ORDER BY 1")]

    lb.label(proj, "ext", rid, "R2", "exclude")
    refused()
    journal.undo(proj, author="R2")   # the label is gone, its History stays: still work added in WIDER
    refused()
    # seals and stated independence go with a replacing import
    lb.import_sample_csv(proj, "ext2", f)
    s2 = lb.sample_by_name(proj, "ext2")
    _seal(proj, s2["sample_id"], rid)
    proj.store.execute("INSERT INTO label_corrections(sample_id,record_id,target,why,author,at,locked,sealed) "
                       "VALUES(?,?,'label','w','a','t',1,1)", (s2["sample_id"], rid))
    with pytest.raises(ValueError):   # a correction row is work added in WIDER
        lb.import_sample_csv(proj, "ext2", f)
    proj.store.execute("DELETE FROM label_corrections WHERE sample_id=?", (s2["sample_id"],))
    lb.import_sample_csv(proj, "ext2", f)
    assert proj.store.scalar("SELECT COUNT(*) FROM label_seals WHERE sample_id=?", (s2["sample_id"],)) == 0


# ----- 4.2b undo of a change to a sealed record (D7) -----

def test_undo_before_seal_refused(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate

    p, v, sid = _complete(tmp_path, monkeypatch)
    seven, eight, nine = (_r1_record(p, v), *[x for x in v[1:] if x != _r1_record(p, v)][:2])
    lb.label(p, "validation", seven, "R3", "include")          # saved before the first look
    before = _last(p)["change_id"]
    agree = p.store.scalar("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (nine,))
    lb.label(p, "validation", nine, "R3", agree, queue_mode="blind")   # from the blind queue, before the look
    blind = _last(p)["change_id"]
    estimate(p, "validation", "main", B=20)
    why = f"record {seven} of validation was sealed after this change: correct the label instead, with a reason"
    assert _undo_why(p, before) == why
    with pytest.raises(ValueError, match="was sealed after this change"):
        journal.undo(p, before, author="R3")
    assert p.store.scalar("SELECT COUNT(*) FROM labels WHERE record_id=? AND reviewer_id='R3'", (seven,)) == 1
    assert _undo_why(p, blind) == journal.BLIND_LOCKED
    assert "validation" not in journal.BLIND_LOCKED and "sample" not in journal.BLIND_LOCKED
    # a correction after the look undoes as usual until recall is estimated again
    lb.label(p, "validation", eight, "R3", "maybe")
    lb.label(p, "validation", eight, "R3", "exclude", correction_reason="read again")
    corrected = _last(p)["change_id"]
    assert _undo_why(p, corrected) is None
    journal.undo(p, corrected, author="R3")
    redo = _last(p)["change_id"]
    journal.undo(p, redo, author="R3")                       # redo it, then estimate again
    estimate(p, "validation", "main", B=20)
    assert _undo_why(p, corrected) == why.replace(str(seven), str(eight))
    # a record both locked and sealed: the seal's text is shown
    lb.lock(p, "validation", by="Owner")
    assert _undo_why(p, before) == why


# ----- 4.3 the change line on sealed records (D6) -----

def _change_line(n, day):
    rec = "1 record of validation had its labels" if n == 1 else f"{n} records of validation had their labels"
    return (f"{rec} changed after its recall was first revealed on {day}; results revealed before then used the "
            "earlier labels (wider label revisions validation).")


def test_sealed_change_needs_reason_and_is_flagged(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate

    p, v, sid = _complete(tmp_path, monkeypatch)
    first = estimate(p, "validation", "main", B=20)
    day = min(s["sealed_at"] for s in _seals(p, sid).values())[:10]
    assert lb.sealed_change_line(p, "validation") is None
    assert not any("changed after its recall" in x for x in first["lines"])
    rid = _r1_record(p, v)
    with pytest.raises(lb.CorrectionNeeded):
        lb.label(p, "validation", rid, "r1", "exclude")
    lb.label(p, "validation", rid, "r1", "exclude", correction_reason="data entry slip")
    cor = p.store.one("SELECT * FROM label_corrections WHERE sample_id=? AND record_id=?", (sid, rid))
    assert cor["sealed"] == 1 and cor["why"] == "data entry slip"
    s = _seals(p, sid)[rid]
    assert (s["decision"], s["corrected_by"]) == ("exclude", "r1")
    line = _change_line(1, day)
    assert lb.sealed_change_line(p, "validation") == line
    assert str(rid) not in line and "exclude" not in line and "slip" not in line
    # an unlock and an undo of another change keep the seals and the line
    lb.label(p, "validation", v[5], "R3", "maybe")
    journal.undo(p, author="R3")
    p.store.execute("UPDATE samples SET locked=0 WHERE sample_id=?", (sid,))
    assert len(_seals(p, sid)) == 120 and lb.sealed_change_line(p, "validation") == line
    # before the next estimate the status says the stored result is older than the correction
    stale = "1 reference label was corrected since. Estimate recall again."
    st = _flat(_cli(p, "validation", "status").output)
    assert st.count(line) == 1 and st.count(stale) == 1 and "This result is from " in st
    second = estimate(p, "validation", "main", B=20)
    assert second["lines"].count(line) == 1
    st = _flat(_cli(p, "validation", "status").output)
    assert st.count(line) == 1 and stale not in st
    api = _client(p).get("/api/validation/status").json()
    assert api["lines"].count(line) == 1
    # a supplementary label adds nothing; an undone correction leaves no count
    lb.label(p, "validation", rid, "R2", "include")
    assert lb.sealed_change_line(p, "validation") == line
    other = _r1_record(p, [x for x in v if x != rid])
    lb.label(p, "validation", other, "r1", "exclude", correction_reason="slip")
    assert lb.sealed_change_line(p, "validation") == _change_line(2, day)
    journal.undo(p, author="r1")
    assert lb.sealed_change_line(p, "validation") == line


def test_sealed_flag_in_status_report_methods(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate
    from wider.recall.outputs import methods_draft

    p, v, sid = _complete(tmp_path, monkeypatch)
    estimate(p, "validation", "main", B=20)
    day = min(s["sealed_at"] for s in _seals(p, sid).values())[:10]
    lb.label(p, "validation", _r1_record(p, v), "r1", "exclude", correction_reason="data entry slip")
    line = _change_line(1, day)
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "20")
    assert out.exit_code == 0 and _flat(out.output).count(line) == 1
    out = _cli(p, "report", "--protocol", "main", "--sample", "validation")
    assert out.exit_code == 0, out.output
    md = _flat((p.root / "exports" / "report.md").read_text(encoding="utf-8"))
    assert md.count(line) == 1
    rep = json.loads((p.root / "exports" / "report.json").read_text(encoding="utf-8"))
    assert line in rep["recall"]["validation_checks"]["lines"]
    assert _flat(methods_draft(p)).count(line) == 1


# ----- 4.6 the expected recall from the stored look (D4, Q11) -----

def test_expected_recall_from_the_stored_look(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate
    from wider.sample import plan as pl

    p, v, sid = _complete(tmp_path, monkeypatch)
    rates = "S1=0.5,S2=0.1,S3=0.01"

    def expected(**kw):
        looks = p.store.scalar("SELECT COUNT(*) FROM validation_look_states")
        seals = p.store.scalar("SELECT COUNT(*) FROM label_seals")
        out = pl.plan(p, budget=150, rates=rates, sims=20, **kw)
        assert p.store.scalar("SELECT COUNT(*) FROM validation_look_states") == looks   # a plan records no look
        assert p.store.scalar("SELECT COUNT(*) FROM label_seals") == seals
        return out["expected_recall"], out["expected_recall_source"]

    assert expected() == (0.55, "the target plus 0.05")   # complete, never estimated: no new estimate
    r = estimate(p, "validation", "main", B=20)
    stored = r["variants"][r["primary_variant"]]["recall"]
    assert expected() == (stored, "the primary recall stored with look 1")
    assert expected(expected_recall=0.7) == (0.7, "your expected recall")   # the given value wins
    # a look stored before this change holds no recall
    meta = json.loads(p.store.get_meta("last_validation_lines:validation"))
    p.store.set_meta("last_validation_lines:validation", json.dumps({k: x for k, x in meta.items() if k != "recall"}))
    assert expected() == (0.55, "the target plus 0.05")
    p.store.set_meta("last_validation_lines:validation", json.dumps(meta))
    # changed labels: the stored recall no longer stands
    lb.label(p, "validation", _r1_record(p, v), "r1", "exclude", correction_reason="data entry slip")
    assert expected() == (0.55, "the target plus 0.05")


def test_bundle_carries_reference_and_corrections(tmp_path, monkeypatch):
    import csv

    from wider.core import researchers as rs
    from wider.recall.estimate import estimate
    from wider.recall.report import share_bundle

    # an incomplete sample is never revealed, so nothing is sealed and neither file is written
    (tmp_path / "open").mkdir()
    q, _ = _drawn_open(tmp_path / "open", monkeypatch)
    plain = share_bundle(q, tmp_path / "plain")
    assert not (plain / "reference_labels.csv").exists() and not (plain / "label_corrections.csv").exists()
    p, v, sid = _complete(tmp_path, monkeypatch)
    rs.register(p, "dana", "option")
    rs.set_attribution(p, "pseudonyms")
    estimate(p, "validation", "main", B=20)
    lb.adjudicate(p, "validation", v[0], "exclude", by="dana", correction_reason="hidden slip text")
    out = estimate(p, "validation", "main", B=20)
    b = share_bundle(p, tmp_path / "b")

    def rows(name):
        with open(b / name, encoding="utf-8") as f:
            return list(csv.DictReader(f))

    ref = rows("reference_labels.csv")
    assert len(ref) == 120 and {r["record_id"] for r in ref} == {str(x) for x in v}
    r0 = next(r for r in ref if r["record_id"] == str(v[0]))
    assert (r0["decision"], r0["source"], r0["corrected"]) == ("exclude", "adjudication", "yes")
    assert {r["corrected"] for r in ref if r is not r0} == {"no"}
    cor = rows("label_corrections.csv")
    assert len(cor) == 1
    c = cor[0]
    assert (c["target"], c["old_decision"], c["new_decision"]) == ("adjudication", "include", "exclude")
    assert c["by"].startswith("Researcher ") and "dana" not in c["by"]
    for name in ("reference_labels.csv", "label_corrections.csv"):
        text = (b / name).read_text(encoding="utf-8")
        assert "hidden slip text" not in text and "dana" not in text
    # the recall from the reference labels and the active screening decisions is the reported one
    weight = {r["record_id"]: float(r["weight"]) for r in rows("labels.csv") if r["sample"] == "validation"}
    screen = {r["record_id"]: r["decision"] for r in rows("screen_decisions.csv") if r["active"] == "yes"}
    rel = [r for r in ref if r["decision"] == "include"]
    num = sum(weight[r["record_id"]] for r in rel if screen.get(r["record_id"]) == "include")
    den = sum(weight[r["record_id"]] for r in rel)
    assert num / den == pytest.approx(out["variants"]["strict"]["recall"])


def test_package_re_review_on_sealed_record(tmp_path, monkeypatch):
    from wider.recall.estimate import estimate

    p, v, sid = _complete(tmp_path, monkeypatch)
    first = estimate(p, "validation", "main", B=20)
    rid = _r1_record(p, v)
    pid = pk.make(p, "r1", ["validation"], "independent", earlier="re-review")["package_id"]
    order = [it["record_id"] for it in pk.items(p, pid)]
    assert rid in order
    earlier = {r[0]: r[1] for r in p.store.query(
        "SELECT record_id, decision FROM labels WHERE sample_id=? AND reviewer_id='r1'", (sid,))}
    data = _export(p, pid, {i: ("exclude" if r == rid else earlier[r]) for i, r in enumerate(order)})
    res = pk.import_file(p, _write(tmp_path, data))
    assert res["imported"] == len(order)
    assert p.store.scalar("SELECT decision FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id='r1'",
                          (sid, rid)) == "exclude"
    rev = p.store.one("SELECT * FROM label_revisions WHERE record_id=? AND sample_id=?", (rid, sid))
    assert (rev["cause"], rev["old_decision"], rev["new_decision"]) == ("package re-review", "include", "exclude")
    assert p.store.scalar("SELECT kind FROM history WHERE change_id=?", (res["change_id"],)) == "package_import"
    # the reference stays include, the record stays final, no change line, the same recall
    assert _seals(p, sid)[rid]["decision"] == "include" and _seals(p, sid)[rid]["corrected_at"] is None
    fin = {r["record_id"]: r for r in lb.final_labels(p, "validation")}[rid]
    assert fin["decision"] == "include" and not fin["open_disagreement"]
    assert lb.completeness(p, "validation")["complete"]
    assert lb.sealed_change_line(p, "validation") is None
    second = estimate(p, "validation", "main", B=20)
    assert second["variants"]["strict"]["recall"] == first["variants"]["strict"]["recall"]
    # only a correction with a reason changes the reference
    with pytest.raises(lb.CorrectionNeeded):
        lb.adjudicate(p, "validation", rid, "exclude", by="Owner")
    lb.adjudicate(p, "validation", rid, "exclude", by="Owner", correction_reason="the re-review was right")
    assert _seals(p, sid)[rid]["decision"] == "exclude"
    # M105: a first adjudication corrects the sealed reference, not an adjudication, and the import stays
    ch = journal.changes(p)[0]
    assert (ch["kind"], ch["summary"]) == (
        "label_correction", f"Owner adjudicated record {rid} in sample validation, correcting its sealed reference")
    with pytest.raises(ValueError, match="cannot be undone"):
        journal.undo(p, res["change_id"], author="Owner", other=True)


# ----------------------------------------------------------------- 5.1 development flags (D6)
from tests.unit.test_screen_compare import _template, cproject, dev_rows, label_all  # noqa: E402, F401


def _dev_run(cproject):  # noqa: F811
    """A run and comparison ``cmp1`` on the locked development sample, both dated the day before every label change
    that follows (a save in the run's last second counts as before it)."""
    from wider.screen import compare
    from wider.screen.engine import screen

    p, _ = cproject
    label_all(p)
    lb.lock(p, "development", by="Owner")
    run = screen(p, "sample:development", pass_name="A")["run_id"]
    compare.run(p, "cmp1", None, ["fake"], repeats=1)
    p.store.execute("UPDATE history SET at='2026-01-01T00:00:00+00:00'")
    p.store.execute("UPDATE runs SET finished_at='2026-01-02T00:00:00+00:00'")
    p.store.execute("UPDATE screen_comparisons SET finished_at='2026-01-02T00:00:00+00:00'")
    return p, run, [r["record_id"] for r in dev_rows(p)]


def _changes(p, ids):
    """alice corrects one record with a reason; bob adds a second, agreeing label on another (a disagreement would
    leave the development sample incomplete and refuse the evaluation, D4)."""
    old = p.store.scalar("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='alice'", (ids[0],))
    lb.label(p, "development", ids[0], "alice", "maybe" if old != "maybe" else "include", correction_reason="slip")
    same = p.store.scalar("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='alice'", (ids[1],))
    lb.label(p, "development", ids[1], "bob", same)
    return p.store.scalar("SELECT MAX(change_id) FROM history")


def _cmp_notes(p):
    out = _cli(p, "screen", "compare", "show", "cmp1")
    assert out.exit_code == 0, out.output
    return _flat(out.output), _client(p).get("/api/screen/compare/cmp1").json()["notes"]


def test_evaluation_flags_later_label_changes(cproject):  # noqa: F811
    from wider.screen.evaluate import NOT_LOCKED, evaluate

    p, run, ids = _dev_run(cproject)
    assert not any("labels changed after" in x for x in evaluate(p, run)["lines"])
    top = _changes(p, ids)
    line = (f"2 records of development had their labels changed after run {run} ran (2026-01-02); "
            "this evaluation uses the current labels.")
    assert evaluate(p, run)["lines"].count(line) == 1
    out = _cli(p, "screen", "evaluate", run)
    assert out.exit_code == 0 and _flat(out.output).count(line) == 1, out.output
    assert _client(p).get(f"/api/screen/evaluate/{run}").json()["lines"].count(line) == 1
    # the run and its calls are kept; after both changes are undone the line is gone
    assert p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=?", (run,))
    for cid in (top, top - 1):
        journal.undo(p, cid, author="Owner", other=True)
    assert not any("labels changed after" in x for x in evaluate(p, run)["lines"])
    # on an unlocked sample the line drops its tail beside NOT_LOCKED
    lb.unlock(p, "development", by="Owner", yes=True)
    lb.label(p, "development", ids[0], "alice", "maybe" if p.store.scalar(
        "SELECT decision FROM labels WHERE record_id=? AND reviewer_id='alice'", (ids[0],)) != "maybe" else "include")
    lines = evaluate(p, run)["lines"]
    assert NOT_LOCKED in lines
    assert f"1 record of development had its labels changed after run {run} ran (2026-01-02)." in lines


def test_comparison_flags_later_label_changes(cproject):  # noqa: F811
    from wider.screen import compare

    p, run, ids = _dev_run(cproject)
    assert not any("labels changed after" in x for x in _cmp_notes(p)[1])
    top = _changes(p, ids)
    line = ("2 records of development had their labels changed after this comparison ran (2026-01-02); "
            "its figures use the current labels.")
    cli_text, notes = _cmp_notes(p)
    assert cli_text.count(line) == 1 and notes.count(line) == 1
    assert p.store.scalar("SELECT COUNT(*) FROM screen_comparisons WHERE name='cmp1'") == 1
    for cid in (top, top - 1):
        journal.undo(p, cid, author="Owner", other=True)
    assert not any("labels changed after" in x for x in _cmp_notes(p)[1])
    # unlocked: the unlocked sentence and the line without its tail
    lb.unlock(p, "development", by="Owner", yes=True)
    lb.label(p, "development", ids[0], "alice", "maybe" if p.store.scalar(
        "SELECT decision FROM labels WHERE record_id=? AND reviewer_id='alice'", (ids[0],)) != "maybe" else "include")
    notes = _cmp_notes(p)[1]
    assert compare.UNLOCKED_NOW.format(name="development") in notes
    assert "1 record of development had its labels changed after this comparison ran (2026-01-02)." in notes


# ----- risk-checker findings (28 Oct 2026) -----

def test_blind_save_from_before_this_change_names_no_sample_once_sealed(proj):
    """R1: a blind save made by the version before this change has no ``blind`` History item; once the record is
    sealed, its undo refusal still names no sample."""
    lb.label(proj, "validation", 4, "R1", "include", queue_mode="blind")
    cid = _last(proj)["change_id"]
    items = json.loads(proj.store.scalar("SELECT items FROM history WHERE change_id=?", (cid,)))
    proj.store.execute("UPDATE history SET items=? WHERE change_id=?",
                       (json.dumps([i for i in items if i.get("t") != "blind"]), cid))
    proj.store.execute("INSERT INTO label_seals(sample_id,record_id,sealed_at,first_change_id,last_change_id,decision,"
                       "sealed_decision,source) VALUES(2,4,'t',?,?,'include','include','R1')", (cid, cid))
    why = next(c for c in journal.changes(proj) if c["change_id"] == cid)["why_not"]
    assert why and "validation" not in why and "sample" not in why
    # an unblinded save still names its sample and record
    lb.label(proj, "validation", 5, "R1", "include", made_in="unblinded queue")
    cid5 = _last(proj)["change_id"]
    proj.store.execute("INSERT INTO label_seals(sample_id,record_id,sealed_at,first_change_id,last_change_id,decision,"
                       "sealed_decision,source) VALUES(2,5,'t',?,?,'include','include','R1')", (cid5, cid5))
    why5 = next(c for c in journal.changes(proj) if c["change_id"] == cid5)["why_not"]
    assert "record 5 of validation was sealed" in why5


def test_blind_queue_over_several_samples_carries_no_locked_flag(proj):
    """R2: when every row of a merged blind queue is guarded, none is left out, and no row tells by a ``locked``
    flag which sample it came from; a one-sample queue and an unblinded queue keep the flag."""
    for rid in (4, 5, 6):
        _seal(proj, 2, rid)
    lb.lock(proj, "dev", by="Owner")
    rows, left = lb.queue_left_out(proj, "R1")
    assert left == 0 and len(rows) == 6 and all("locked" not in r for r in rows)
    api = _client(proj, "R1").get("/api/labels/queue", params={"reviewer": "R1"}).json()
    assert len(api) == 6 and all("locked" not in r for r in api)
    one, _ = lb.queue_left_out(proj, "R1", ["dev"])
    assert all(r["locked"] for r in one)
    unblind, _ = lb.queue_left_out(proj, "R1", blind=False)
    assert {r["locked"] for r in unblind} == {True, False}


def test_sample_import_refused_once_a_package_was_made(tmp_path):
    """R3: a review package made for an imported sample is work added in WIDER, so a replacing import is refused and
    the package's items still point at the sample's records."""
    p = _pkg_project(tmp_path, label_val=False)
    rows = [(f"Record {i} title", "Excluded" if i % 3 else "Included", "S1", 2.0) for i in range(21, 41)]
    (tmp_path / "a.csv").write_text("title,decision,band,weight\n" + "".join(f"{t},{d},{b},{w}\n" for t, d, b, w in rows))
    lb.import_sample_csv(p, "ext", tmp_path / "a.csv")
    pk.make(p, "R3", ["ext"], "second")
    before = [tuple(r) for r in p.store.query("SELECT * FROM labels ORDER BY sample_id, record_id, reviewer_id")]
    (tmp_path / "b.csv").write_text("title,decision,band,weight\n" + "".join(f"{t},Excluded,{b},{w}\n" for t, _d, b, w in rows))
    with pytest.raises(ValueError, match="added in WIDER since it was imported"):
        lb.import_sample_csv(p, "ext", tmp_path / "b.csv")
    assert [tuple(r) for r in p.store.query("SELECT * FROM labels ORDER BY sample_id, record_id, reviewer_id")] == before
    r = _cli(p, "sample", "import", "ext", str(tmp_path / "b.csv"))
    assert r.exit_code == 1 and "added in WIDER since it was imported" in r.output


def test_too_long_correction_reason_imports_nothing(proj, tmp_path):
    """R4: `wider label import --overwrite --correction-reason` with a reason over 500 characters is refused before
    any row is saved, as the missing reason is; the queue import too."""
    lb.label(proj, "dev", 1, "R1", "include")
    lb.lock(proj, "dev", by="Owner")
    f = tmp_path / "c.csv"
    f.write_text("record_id,decision\n2,include\n3,include\n1,exclude\n")
    n = len(journal.changes(proj))
    r = _cli(proj, "label", "import", "dev", str(f), "--reviewer", "R1", "--overwrite", "--correction-reason", "x" * 501)
    assert r.exit_code == 1 and "a correction reason is at most 500 characters" in r.output
    assert proj.store.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=1 AND reviewer_id='R1'") == 1
    assert len(journal.changes(proj)) == n
    # the queue import: one new label and one change, the reason too long
    q = lb.queue(proj, "R1", ["dev"])
    item = {r["record_id"]: r["item"] for r in q}
    g = tmp_path / "q.csv"
    g.write_text(f"item,decision\n{item[2]},include\n{item[1]},exclude\n")
    with pytest.raises(ValueError, match="at most 500 characters"):
        lb.import_queue_csv(proj, g, correction_reason="y" * 501)
    assert proj.store.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=1 AND reviewer_id='R1'") == 1
