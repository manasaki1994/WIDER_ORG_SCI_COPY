"""reviewer-roles-and-blinding: making review packages, what they hold, importing the reviewer's decisions, status,
withdrawing, earlier labels and disagreements (blinded-review spec). Synthetic, topic-neutral records; reviewers R1,
R2 and R3; nothing calls a model or the network."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import CODEBOOK
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import journal
from wider.core.project import Project
from wider.enrich import abstracts
from wider.sample import labels as lb
from wider.sample import package as pk
from wider.ui.app import create_app

runner = CliRunner()
MADE_AT = "2026-10-02T09:00:00+00:00"


def _codebook(p, status="frozen", note=""):
    v = artifacts.save_new_version(p, "codebook", {**CODEBOOK, "question": CODEBOOK["question"] + note}, origin="manual")
    if status != "draft":
        artifacts.set_status(p, "codebook", v["version"], status)
    return v["version"]


def _project(tmp_path, name="p", n_dev=20, n_pos=14, n_exc=186, label_val=True, codebook=True):
    """``dev`` (purposive, ``n_dev`` records) and ``validation`` (probability, ``n_pos`` includes and maybes and
    ``n_exc`` excludes by R1 under codebook version 3, frozen)."""
    p = Project.init(tmp_path / name, platforms=["scopus"])
    st = p.store
    n = n_dev + n_pos + n_exc
    for i in range(1, n + 1):
        ab = f"Abstract text of record {i} about a neutral topic with several words in it."
        st.execute("INSERT INTO records(record_id,cluster_id,key,title,authors,year,journal,doi,abstract,abstract_words,thin) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?,0)", (i, i, f"k{i}", f"Record {i} title", "Author, A.; Author, B.", 2020,
                                                     "Journal X", f"10.1000/r{i}", ab, 13))
        abstracts.add_version(st, i, ab, "imported", at="2026-01-02T00:00:00+00:00")
    if codebook:
        _codebook(p, "approved")
        _codebook(p, "approved", " v2")
        _codebook(p, "frozen", " v3")
    st.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('dev','purposive','{}',?,'t')", (n_dev,))
    st.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('validation','probability','{}',?,'t')",
               (n_pos + n_exc,))
    dev, val = (st.scalar("SELECT sample_id FROM samples WHERE name=?", (x,)) for x in ("dev", "validation"))
    st.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,weight,pool,rank) VALUES(?,?,?,?,?,?,?)",
                   [(dev, i, i, "S1", None, "cluster", i) for i in range(1, n_dev + 1)] +
                   [(val, i, i, "S2", 3.5, "main", i) for i in range(n_dev + 1, n + 1)])
    if label_val:
        lb.add_reviewer(p, "R1")
        st.executemany("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,reason,labels,note,updated_at,"
                       "codebook_version,criteria_frozen,after_look,made_in,abstract_version) "
                       "VALUES(?,?,?,?,?,?,?,?,3,1,0,'wider',1)",
                       [(val, i, "R1", ("include" if k % 2 else "maybe") if k < n_pos else "exclude",
                         "" if k < n_pos else "a", "{}", "", "2026-10-01T09:00:00+00:00")
                        for k, i in enumerate(range(n_dev + 1, n + 1))])
    return p


# undo, redo and the review-package commands never sign with the login name ($USER); the person at these tests is
# the one whose login name signed the engine calls (current_author's inferred fallback)
ME = {"WIDER_REVIEWER": os.environ.get("USER") or ""}


def _cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "200", **ME})


def _client(p, author="Owner"):
    return TestClient(create_app(p), headers={"X-Wider-Author": author})


def _data(path) -> dict:
    """The data block of a package file, parsed."""
    text = Path(path).read_text(encoding="utf-8")
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', text, re.S)
    return json.loads(m.group(1))


def _export(p, pkg_id, decisions: dict | None = None, seq=1, reviewer=None, n=None, at="2026-10-09T14:00:00Z") -> dict:
    """A decisions file as the page exports it: ``decisions`` maps an item index (the page's order) to a decision,
    else the first ``n`` items are decided include."""
    pkg = pk._row(p, pkg_id)
    toks = [it["token"] for it in pk.items(p, pkg_id)]
    decisions = decisions if decisions is not None else {i: "include" for i in range(n if n is not None else len(toks))}
    return {"package_id": pkg_id, "project_id": pk.project_id(p), "reviewer": reviewer or pkg["reviewer"],
            "codebook_version": pkg["codebook_version"], "export_seq": seq, "exported_at": at,
            "items": [{"item": toks[i], "decision": d, "reason": "", "ticks": {}, "tags": [], "note": "",
                       "decided_at": at} for i, d in decisions.items()]}


def _write(tmp_path, data, name="dec.json") -> Path:
    f = tmp_path / name
    f.write_text(json.dumps(data), encoding="utf-8")
    return f


# ------------------------------------------------------------------------------------------------ making (2.3)
def test_make_independent_and_second(tmp_path):
    p = _project(tmp_path)
    a = pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "r2-dev.html")
    b = pk.make(p, "R3", ["validation"], "second", out=tmp_path / "r3-val.html")
    assert a["n_items"] == 20 and b["n_items"] == 28
    d = _data(tmp_path / "r3-val.html")
    assert len(d["items"]) == 28 and len({i["item"] for i in d["items"]}) == 28
    recs = {it["record_id"] for it in pk.items(p, b["package_id"])}
    first = {r["record_id"]: r["decision"] for r in p.store.query("SELECT record_id, decision FROM labels WHERE reviewer_id='R1'")}
    assert sum(first[r] in ("include", "maybe") for r in recs) == 14 and sum(first[r] == "exclude" for r in recs) == 14
    # the excludes are the ones the queue would choose (one shared selection), the order is not the sample's
    s = lb.sample_by_name(p, "validation")
    assert set(lb._second_pairs(p, "R3", s)["pairs"]) == {(s["sample_id"], r) for r in recs}
    order = [it["record_id"] for it in pk.items(p, b["package_id"])]
    assert order != sorted(order)
    out = _cli(p, "blinding", "status", "--json")
    assert out.exit_code == 0, out.output
    st = json.loads(out.output)
    rows = {r["reviewer"]: r for r in st["packages"]}
    assert (rows["R2"]["mode"], rows["R2"]["samples"], rows["R2"]["n_items"]) == ("independent", ["dev"], 20)
    assert (rows["R3"]["mode"], rows["R3"]["samples"], rows["R3"]["n_items"], rows["R3"]["codebook_version"]) == \
        ("second", ["validation"], 28, 3)
    assert rows["R3"]["made_at"]
    kept = p.root / "review-packages" / f"{b['package_id']}.html"
    assert kept.read_bytes() == (tmp_path / "r3-val.html").read_bytes()


def test_copy_kept_and_written_again(tmp_path):
    p = _project(tmp_path)
    b = pk.make(p, "R3", ["validation"], "second", out=tmp_path / "r3-val.html")
    out = _cli(p, "blinding", "package-file", b["package_id"], "--out", str(tmp_path / "again.html"))
    assert out.exit_code == 0 and f"wrote {tmp_path / 'again.html'}" in out.output
    assert (tmp_path / "again.html").read_bytes() == (tmp_path / "r3-val.html").read_bytes()
    r = _client(p).get(f"/api/blinding/package/{b['package_id']}/file")
    assert r.status_code == 200 and r.content == (tmp_path / "r3-val.html").read_bytes()
    assert f'filename="review-package-R3-{b["package_id"]}.html"' in r.headers["content-disposition"]
    assert pk.download_name({"reviewer": "Ana María / x", "package_id": "p-1"}) == "review-package-Ana-Mar-a-x-p-1.html"


def test_leaves_computer_sentence_cli(tmp_path):
    p = _project(tmp_path)
    out = _cli(p, "blinding", "package", "R3", "--sample", "validation", "--mode", "second", "--out",
               str(tmp_path / "r3-val.html"))
    assert out.exit_code == 0, out.output
    text = ("This file leaves your computer when you send it. It holds the titles, abstracts, authors, years, journals "
            "and DOIs of 28 records, the research questions, construct definitions and criteria (codebook version 3), "
            "the project title and the reviewer name R3. It holds no decision, note, tag, model output or sample name.")
    assert out.output.count(text) == 1
    assert out.output.index(text) < out.output.index(f"wrote {tmp_path / 'r3-val.html'}")


BAD = [
    (["--sample", "dev", "--sample", "validation", "--mode", "independent"], "A package holds development or validation records, not both.", 400),
    (["--sample", "nosuch", "--mode", "independent"], "no sample 'nosuch'", 404),
    (["--sample", "dev", "--mode", "both"], "mode must be second or independent", 400),
    (["--sample", "dev", "--mode", "second"], "second mode is for validation samples; use independent for development records.", 400),
]


def test_bad_package_refused(tmp_path):
    p = _project(tmp_path)
    c = _client(p)

    def both(reviewer, args, text, code):
        before = (p.store.scalar("SELECT COUNT(*) FROM review_packages"), sorted((p.root / "review-packages").glob("*"))
                  if (p.root / "review-packages").exists() else [])
        out = _cli(p, "blinding", "package", reviewer, *args, "--out", str(tmp_path / "x.html"))
        assert out.exit_code == 1 and text in out.output.replace("\n", " "), out.output
        assert not (tmp_path / "x.html").exists()
        it = iter(args)
        samples, mode = [], None
        for a in it:
            if a == "--sample":
                samples.append(next(it))
            elif a == "--mode":
                mode = next(it)
        r = c.post("/api/blinding/package", json={"reviewer": reviewer, "samples": samples, "mode": mode})
        assert r.status_code == code and text in r.json()["detail"], r.text
        after = (p.store.scalar("SELECT COUNT(*) FROM review_packages"), sorted((p.root / "review-packages").glob("*"))
                 if (p.root / "review-packages").exists() else [])
        assert after == before

    for args, text, code in BAD:
        both("R2", args, text, code)
    pk.make(p, "R2", ["dev"], "independent")
    both("R2", ["--sample", "dev", "--mode", "independent"], "R2 already has an open package on dev; import or withdraw it first.", 409)
    both("R1", ["--sample", "validation", "--mode", "second"],
         "R1 made the first labels on validation; a second-reviewer package is for someone else.", 400)
    p.store.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('empty','purposive','{}',0,'t')")
    both("R3", ["--sample", "empty", "--mode", "independent"], "The package would hold no record.", 400)
    q = _project(tmp_path, "q", codebook=False)
    c = _client(q)
    p = q
    both("R2", ["--sample", "dev", "--mode", "independent"], "Save the criteria first: the package carries them.", 409)


def test_package_made_is_permanent(tmp_path):
    p = _project(tmp_path)
    b = pk.make(p, "R3", ["validation"], "second")
    ch = p.store.one("SELECT * FROM history WHERE kind='review_package'")
    assert ch["summary"] == "Review package made for R3 (28 records)" and ch["change_id"] == b["change_id"]
    with pytest.raises(ValueError, match=re.escape(pk.PERMANENT)):
        journal.undo(p, ch["change_id"], author=ch["author"] or None, other=True)
    out = _cli(p, "history", "undo", str(ch["change_id"]))
    assert out.exit_code != 0 and pk.PERMANENT in out.output.replace("\n", " ")
    r = _client(p, ch["author"] or "Owner").post(f"/api/history/{ch['change_id']}/undo", json={})
    assert r.status_code in (400, 409) and pk.PERMANENT in r.text
    assert pk.is_open(p, pk._row(p, b["package_id"]))
    entry = next(x for x in journal.changes(p) if x["change_id"] == ch["change_id"])
    assert entry["undoable"] is False and entry["why_not"] == pk.PERMANENT


def test_new_project_has_project_id(tmp_path):
    p = _project(tmp_path)
    assert p.store.get_meta("project_id") is None   # a store made at v9 never ran an upgrade
    b = pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "f.html")
    pid = p.store.get_meta("project_id")
    assert pid and _data(tmp_path / "f.html")["project_id"] == pid
    res = pk.import_file(p, _write(tmp_path, _export(p, b["package_id"], n=3)))
    assert res["imported"] == 3


def test_criteria_version_of_first_reviewer(tmp_path):
    p = _project(tmp_path)
    v4 = _codebook(p, "frozen", " v4")
    assert v4 == 4
    a = pk.make(p, "R3", ["validation"], "second", out=tmp_path / "a.html")
    d = _data(tmp_path / "a.html")
    assert a["codebook_version"] == 3 and d["codebook_version"] == 3
    v3 = artifacts.load(p, "codebook", 3)[1]
    assert d["questions"][0] == v3["question"] and [e["id"] for e in d["elements"]] == ["e1", "e2"]
    assert d["elements"][0]["rule"] == v3["elements"][0]["rule"]
    assert [r["code"] for r in d["exclusion_reasons"]] == ["a", "b", "e"]
    pk.withdraw(p, a["package_id"], "trying another version")
    # R1 relabels 5 records under version 4: two versions, a choice is needed
    p.store.execute("UPDATE labels SET codebook_version=4 WHERE label_id IN (SELECT label_id FROM labels "
                    "WHERE reviewer_id='R1' ORDER BY label_id LIMIT 5)")
    with pytest.raises(pk.PackageError, match=re.escape("R1's labels on validation use codebook versions 3 and 4; "
                                                        "choose one with --codebook-version.")):
        pk.make(p, "R3", ["validation"], "second")
    b = pk.make(p, "R3", ["validation"], "second", codebook_version=4, out=tmp_path / "b.html")
    assert b["codebook_version"] == 4 and _data(tmp_path / "b.html")["questions"][0].endswith(" v4")
    # no version recorded: the latest frozen one, said once
    q = _project(tmp_path, "q")
    _codebook(q, "frozen", " v4")
    q.store.execute("UPDATE labels SET codebook_version=NULL")
    c = pk.make(q, "R3", ["validation"], "second", out=tmp_path / "c.html")
    assert c["codebook_version"] == 4
    assert c["notes"].count("The first reviewer's criteria version is not recorded; the package carries version 4.") == 1
    # neither frozen nor approved: refused
    r = _project(tmp_path, "r", codebook=False)
    _codebook(r, "draft")
    r.store.execute("UPDATE labels SET codebook_version=NULL")
    with pytest.raises(pk.PackageError, match="Approve the criteria first"):
        pk.make(r, "R3", ["validation"], "second")
    # approved but not frozen: carried and marked
    _codebook(r, "approved", " ok")
    d = pk.make(r, "R3", ["validation"], "second", out=tmp_path / "d.html")
    assert _data(tmp_path / "d.html")["not_frozen"] == pk.NOT_FROZEN and not d["frozen"]


def test_development_labels_record_version(tmp_path):
    p = _project(tmp_path)
    lb.label(p, "dev", 1, "R1", "include")
    row = p.store.one("SELECT codebook_version, criteria_frozen, after_look FROM labels WHERE record_id=1")
    assert (row["codebook_version"], row["criteria_frozen"], row["after_look"]) == (3, None, None)
    assert lb.criteria_counts(p, "dev")["not_recorded"] == 1   # the counts of a purposive sample are as before
    _codebook(p, "frozen", " v4")
    a = pk.make(p, "R2", ["dev"], "independent")
    assert a["codebook_version"] == 3


def test_abstract_version_the_model_judged(tmp_path):
    """A screened on version 1 then enriched; B enriched then screened on version 2; C never screened."""
    p = _project(tmp_path, n_dev=3, n_pos=1, n_exc=1)
    st = p.store
    st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) VALUES('r1','screen','s','h','done','t','d')")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,provider,abstract_version) "
               "VALUES('r1',1,'A','x',1,'t','fake',1)")
    for rid in (1, 2):
        abstracts.add_version(st, rid, " ".join(["word"] * 240), "openalex", at="2026-03-04T00:00:00+00:00")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,provider,abstract_version) "
               "VALUES('r1',2,'A','x',1,'t','fake',2)")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,provider,abstract_version) "
               "VALUES('r1',3,'B','x',1,'t','researcher',1)")   # the researcher's own read does not count
    a = pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "f.html")
    its = {it["record_id"]: it["abstract_version"] for it in pk.items(p, a["package_id"])}
    assert its == {1: 1, 2: 2, 3: 1}
    d = {x["title"]: x for x in _data(tmp_path / "f.html")["items"]}
    assert d["Record 1 title"]["abstract_source"] == "imported" and d["Record 1 title"]["abstract_words"] == 14
    assert d["Record 2 title"]["abstract_source"] == "OpenAlex" and d["Record 2 title"]["abstract_words"] == 240
    assert d["Record 2 title"]["abstract_date"] == "2026-03-04"
    assert d["Record 3 title"]["abstract_source"] == "imported"
    assert a["notes"].count("1 item shows an older abstract than the current one, because the model judged that one.") == 1


def test_earlier_choice_needed(tmp_path):
    p = _project(tmp_path)
    s = lb.sample_by_name(p, "validation")
    ids = [r[0] for r in p.store.query("SELECT record_id FROM labels WHERE reviewer_id='R1' ORDER BY record_id")]
    for rid in ids[:8]:
        lb.label(p, "validation", rid, "R2", "include", made_in="unblinded queue")
    for rid in ids[8:12]:   # four from the blind queue
        lb.label(p, "validation", rid, "R2", "exclude", queue_mode="second")
    assert p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='R2' AND made_in='blind queue'") == 4
    msg = ("R2 already has 12 labels on these samples that were not made in a review package, 4 of them from the blind "
           "queue; choose --earlier retain, re-review or exclude.")
    out = _cli(p, "blinding", "package", "R2", "--sample", "validation", "--mode", "second", "--out", str(tmp_path / "x.html"))
    flat = " ".join(out.output.split())
    assert out.exit_code == 1 and flat.count(msg) == 1 and flat.count(pk.earlier_meanings("R2")) == 1
    pre = pk.preview(p, "R2", ["validation"], "second")
    assert pre["earlier"]["n"] == 12 and pre["earlier"]["queue"] == 4 and pre["earlier"]["message"] == msg
    assert pre["earlier"]["meanings"] == pk.earlier_meanings("R2")
    assert "--earlier" not in pre["earlier"]["page_message"]
    rr = pk.preview(p, "R2", ["validation"], "second", earlier="re-review")   # the dialog's count follows the choice
    assert rr["n_items"] == pre["n_items"] + 12 and f"of {rr['n_items']} records" in rr["leaving"]
    assert not p.store.scalar("SELECT COUNT(*) FROM review_packages") and s


def test_second_package_only_new_records(tmp_path):
    p = _project(tmp_path)
    a = pk.make(p, "R2", ["dev"], "independent")
    pk.import_file(p, _write(tmp_path, _export(p, a["package_id"], n=15)))
    with pytest.raises(pk.PackageError, match="R2 already has an open package on dev; import or withdraw it first."):
        pk.make(p, "R2", ["dev"], "independent")
    pk.import_file(p, _write(tmp_path, _export(p, a["package_id"], n=20, seq=2), "d2.json"))
    # dev is extended by 10 records
    dev = lb.sample_by_name(p, "dev")["sample_id"]
    p.store.executemany("INSERT INTO records(record_id,cluster_id,key,title) VALUES(?,?,?,?)",
                        [(1000 + i, 1000 + i, f"x{i}", f"New record {i}") for i in range(10)])
    p.store.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)",
                        [(dev, 1000 + i, 100 + i) for i in range(10)])
    b = pk.make(p, "R2", ["dev"], "independent")
    assert b["n_items"] == 10 and b["earlier"] is None
    assert {it["record_id"] for it in pk.items(p, b["package_id"])} == {1000 + i for i in range(10)}


# ------------------------------------------------------------------------------------------------ the file (2.4, 2.5)
MARK = "ZQXMARKER"


def test_marker_strings_never_in_package(tmp_path):
    """Markers in R1's reasons, notes and tags, a model's reason and confidence, a band, a sample name, an owner note
    and an adjudication never reach the file, nor do sample names, kinds, record ids, scores or other names."""
    p = _project(tmp_path)
    st = p.store
    st.execute("UPDATE labels SET reason=?, note=?, tags=? WHERE reviewer_id='R1'", (MARK + "reason", MARK + "note",
                                                                                     json.dumps([MARK + "tag"])))
    st.execute("UPDATE sample_rows SET band=?, pool=?", (MARK + "band", MARK + "pool"))
    st.execute("UPDATE samples SET name=? WHERE name='validation'", (MARK + "sample",))
    st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir,provider,model) "
               "VALUES('r1','screen','s','h','done','t','d',?,?)", (MARK + "backend", MARK + "model"))
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,provider,model,parsed,abstract_version) "
               "SELECT 'r1',record_id,'A','x',1,'t',?,?,?,1 FROM sample_rows", (MARK + "backend", MARK + "model",
                                                                                  json.dumps({"reason": MARK + "why", "confidence": MARK + "conf"})))
    st.execute("INSERT INTO record_notes(record_id,author,text,created_at) SELECT record_id,'Owner',?,'t' FROM sample_rows",
               (MARK + "ownernote",))
    st.execute("INSERT INTO adjudications(sample_id,record_id,decision,reason,decided_by,decided_at) "
               "SELECT sample_id,record_id,'include',?,'Owner','t' FROM sample_rows", (MARK + "adj",))
    st.execute("INSERT INTO record_tags(record_id,tag,author,created_at) SELECT record_id,?,'R1','t' FROM sample_rows",
               (MARK.lower() + "-tag",))
    a = pk.make(p, "R3", [MARK + "sample"], "second", out=tmp_path / "s.html")
    b = pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "i.html")
    for f, pid in ((tmp_path / "s.html", a["package_id"]), (tmp_path / "i.html", b["package_id"])):
        text = f.read_text(encoding="utf-8")
        assert MARK not in text and MARK.lower() not in text
        d = _data(f)
        for it in d["items"]:
            assert set(it) == {"item", "title", "abstract", "abstract_source", "abstract_words", "abstract_date",
                               "authors", "year", "journal", "doi"}
        assert set(d) == {"package_id", "project_id", "project_title", "reviewer", "codebook_version", "frozen",
                          "not_frozen", "questions", "constructs", "target", "elements", "exclusion_reasons",
                          "limits_text", "made_at", "items"}
        blob = json.dumps({**d, "package_id": "", "project_id": "", "items": [{**it, "item": ""} for it in d["items"]]})
        for word in ("record_id", "score", "band", "pool", "rank", "weight", "R1", "Owner", "probability", "purposive",
                     "validation", "development", "dev\"", "repeat", "sample"):
            assert word not in blob, word
        assert pk.TEXTS["reviewers"] not in text and pk.TEXTS["limits"] in text
        ids = [str(it["record_id"]) for it in pk.items(p, pid)]
        assert not any(f'"{i}"' in blob for i in ids)
    page_own = (Path(pk.__file__).parent / "package_page" / "page.js").read_text() + \
        (Path(pk.__file__).parent / "package_page" / "page.html").read_text()
    for word in ("second", "independent", "development", "validation", "probability"):
        assert word not in page_own.lower(), word


def test_page_self_contained_no_requests(tmp_path):
    p = _project(tmp_path, n_dev=3)
    st = p.store
    st.execute("UPDATE records SET title=?, abstract=? WHERE record_id=1",
               ("<script>alert(1)</script> title", "<img src=https://example.org/x> and </script> end"))
    st.execute("UPDATE record_abstracts SET abstract=? WHERE record_id=1", ("<img src=https://example.org/x> and </script> end",))
    a = pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "f.html")
    text = (tmp_path / "f.html").read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text and "</script> end" not in text
    assert _data(tmp_path / "f.html")["items"]  # still parses
    titles = {x["title"] for x in _data(tmp_path / "f.html")["items"]}
    assert "<script>alert(1)</script> title" in titles
    markup = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S)
    assert not re.search(r"<[^>]+\s(src|href|action|srcset)\s*=", markup)   # no element loads or links anything
    assert ".src" not in text and "setAttribute(\"src\"" not in text
    assert "fetch(" not in text and "XMLHttpRequest" not in text and "import(" not in text
    for href in re.findall(r'href\s*=\s*"([^"]*)"', text):
        assert href.startswith("https://doi.org/")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]*)"', text).group(1)
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp and "img-src 'none'" in csp and "form-action 'none'" in csp
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", text, re.S)]
    styles = re.findall(r"<style>(.*?)</style>", text, re.S)
    assert len(scripts) == 1 and len(styles) == 1
    h = lambda s: "sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode()   # noqa: E731
    assert f"script-src '{h(scripts[0])}'" in csp and f"style-src '{h(styles[0])}'" in csp
    js = scripts[0]
    assert 'rel: "noreferrer noopener"' in js and "https://doi.org/" in js and a
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


# ------------------------------------------------------------------------------------------------ import (4.1, 4.2)
def _copy(p, tmp_path, name):
    import shutil
    p.close()
    shutil.copytree(p.root, tmp_path / name)
    return Project.load(p.root), Project.load(tmp_path / name)


def _r3_labels(p):
    return {r["record_id"]: dict(r) for r in p.store.query("SELECT * FROM labels WHERE reviewer_id='R3'")}


def test_import_matches_and_signs(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pid = P["package_id"]
    p, q = _copy(p, tmp_path, "copy")
    data = _export(p, pid, n=20, reviewer="Ana")
    data["items"][0]["tags"] = ["check full text"]
    data["items"][1].update(decision="exclude", reason="b", ticks={"e1": "no"}, note="my note")
    f = _write(tmp_path, data)
    out = _cli(p, "blinding", "import", str(f))
    assert out.exit_code == 0, out.output
    assert out.output.count("R3: 20 of 28 decided; 20 imported.") == 1
    assert out.output.count("The file names Ana; its labels are stored as R3's, the package's reviewer.") == 1
    with f.open("rb") as fh:
        r = _client(q).post("/api/blinding/import", files={"file": ("dec.json", fh, "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["lines"][0] == "R3: 20 of 28 decided; 20 imported."
    toks = {it["token"]: it for it in pk.items(p, pid)}
    for proj in (p, q):
        labs = _r3_labels(proj)
        assert len(labs) == 20 and not proj.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='Ana'")
        for e in data["items"]:
            it = toks[e["item"]]
            lab = labs[it["record_id"]]
            assert lab["sample_id"] == it["sample_id"] and lab["decision"] == e["decision"] and lab["made_in"] == pid
            assert (lab["codebook_version"], lab["criteria_frozen"], lab["after_look"]) == (3, 1, 0)
            assert lab["abstract_version"] == it["abstract_version"] == 1
        first = labs[toks[data["items"][0]["item"]]["record_id"]]
        second = labs[toks[data["items"][1]["item"]]["record_id"]]
        assert json.loads(first["tags"]) == ["check full text"]
        assert (second["reason"], json.loads(second["labels"]), second["note"]) == ("b", {"e1": "no"}, "my note")
        ch = [c for c in journal.changes(proj) if c["kind"] == "package_import"]
        assert [c["summary"] for c in ch] == [f"R3's decisions imported from review package {pid} (20 labels)"]
    from wider.core import researchers
    assert researchers._row(p, "R3")["status"] == "declared" and researchers._row(p, "R3")["source"] == "review package"


def test_imported_label_criteria_state(tmp_path):
    """The package's codebook version and frozen state are written (not kept from an earlier label); after_look is 1
    only for a validation look before the package was made."""
    p = _project(tmp_path)
    s = lb.sample_by_name(p, "validation")
    p.store.execute("INSERT INTO validation_looks(sample_id,result_set_key,first_at,last_at,n) VALUES(?,'main',?,?,1)",
                    (s["sample_id"], "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"))
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=5)))
    assert {lab["after_look"] for lab in _r3_labels(p).values()} == {1}
    q = _project(tmp_path, "q")
    rid = q.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' AND decision='include' LIMIT 1")
    lb.label(q, "validation", rid, "R3", "maybe")
    q.store.execute("UPDATE labels SET codebook_version=1, criteria_frozen=0 WHERE reviewer_id='R3'")
    Q = pk.make(q, "R3", ["validation"], "second", earlier="re-review")
    sv = lb.sample_by_name(q, "validation")["sample_id"]
    q.store.execute("INSERT INTO validation_looks(sample_id,result_set_key,first_at,last_at,n) VALUES(?,'main',?,?,1)",
                    (sv, "2099-01-01T00:00:00+00:00", "2099-01-01T00:00:00+00:00"))   # a look after the package
    toks = [it["token"] for it in pk.items(q, Q["package_id"])]
    idx = next(i for i, it in enumerate(pk.items(q, Q["package_id"])) if it["record_id"] == rid)
    pk.import_file(q, _write(tmp_path, _export(q, Q["package_id"], {idx: "include"}), "q.json"))
    lab = _r3_labels(q)[rid]
    assert (lab["codebook_version"], lab["criteria_frozen"], lab["after_look"], lab["made_in"]) == (3, 1, 0, Q["package_id"])
    assert toks


def test_later_export_updates_older_refused(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pid = P["package_id"]
    early = _export(p, pid, n=20, seq=1, at="2026-10-09T14:00:00Z")
    late = _export(p, pid, n=28, seq=2, at="2026-10-09T16:00:00Z")
    late["items"][0]["decision"] = late["items"][1]["decision"] = "exclude"
    f1, f2 = _write(tmp_path, early, "a.json"), _write(tmp_path, late, "b.json")
    pk.import_file(p, f1)
    res = pk.import_file(p, f2)
    assert res["imported"] == 28 and len(_r3_labels(p)) == 28
    ch = p.store.one("SELECT * FROM history WHERE kind='package_import' ORDER BY change_id DESC LIMIT 1")
    items = json.loads(ch["items"])
    labs = [it for it in items if it.get("table") == "labels"]
    added = [it for it in labs if not it["before"]]
    changed = [it for it in labs if it["before"] and it["before"][0]["decision"] != it["after"][0]["decision"]]
    assert len(added) == 8 and len(changed) == 2 and all(it["before"][0]["decision"] == "include" for it in changed)
    for f in (f1,):
        out = _cli(p, "blinding", "import", str(f))
        assert out.exit_code == 1
        assert f"This file is older than the one already imported from package {pid} (exported 16:00)." in out.output
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    out = _cli(p, "blinding", "import", str(f2))
    assert out.exit_code == 0 and "Nothing new: this file was already imported." in out.output
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n
    same_seq = {**late, "items": late["items"][:5]}
    with pytest.raises(pk.PackageError, match="has the export number of one already imported, with other entries"):
        pk.import_file(p, _write(tmp_path, same_seq, "c.json"))


def test_foreign_or_bad_files(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pid = P["package_id"]
    c = _client(p)
    good = _export(p, pid, n=10)

    def refused(data, text, code):
        f = _write(tmp_path, data, "x.json")
        before = p.store.scalar("SELECT COUNT(*) FROM labels"), p.store.scalar("SELECT COUNT(*) FROM history")
        out = _cli(p, "blinding", "import", str(f))
        assert out.exit_code == 1 and text in out.output.replace("\n", " "), out.output
        with f.open("rb") as fh:
            r = c.post("/api/blinding/import", files={"file": ("x.json", fh, "application/json")})
        assert r.status_code == code and text in r.json()["detail"], r.text
        assert (p.store.scalar("SELECT COUNT(*) FROM labels"), p.store.scalar("SELECT COUNT(*) FROM history")) == before

    refused({**good, "project_id": "0000000000000000"}, pk.FOREIGN, 400)
    refused({**good, "package_id": "p-20990101-abcdef"}, pk.FOREIGN, 404)
    refused({"no": "items"} | {"items": "x"}, "This decisions file cannot be read", 400)
    bad = _export(p, pid, n=6)
    bad["items"] += [{"item": "unknown-token-1", "decision": "include"}, {"item": "unknown-token-2", "decision": "include"}]
    bad["items"][0]["decision"] = "perhaps"
    res = pk.import_file(p, _write(tmp_path, bad, "bad.json"))
    assert res["imported"] == 5
    assert res["lines"].count("2 items are not in this package and 1 decision is not include, exclude or maybe; they were "
                              "skipped.") == 1
    pk.withdraw(p, pid, "stopped")
    refused({**good, "export_seq": 10}, f"Package {pid} was withdrawn; its decisions are not imported.", 409)


def test_items_that_left_the_sample(tmp_path):
    from wider.dedup.engine import _remap_downstream
    p = _project(tmp_path, n_exc=187)   # record 221 is outside both samples: the survivor of a merge
    st = p.store
    sv = lb.sample_by_name(p, "validation")["sample_id"]
    st.execute("DELETE FROM labels WHERE record_id=221")
    st.execute("DELETE FROM sample_rows WHERE record_id=221")
    P = pk.make(p, "R3", ["validation"], "second")
    its = pk.items(p, P["package_id"])
    merged, d1, d2 = its[0]["record_id"], its[1]["record_id"], its[2]["record_id"]
    with st.tx() as c:   # a dedup re-run merges one record into another
        _remap_downstream(c, merged, 221)
        c.execute("DELETE FROM records WHERE record_id=?", (merged,))
    st.execute("DELETE FROM sample_rows WHERE sample_id=? AND record_id IN (?,?)", (sv, d1, d2))   # a redraw drops two
    res = pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=10)))
    assert res["imported"] == 8
    assert res["lines"].count("2 items left the sample since the package was made; they were skipped.") == 1
    labs = _r3_labels(p)
    assert 221 in labs and merged not in labs and d1 not in labs and d2 not in labs


def test_package_item_record_dropped_by_dedup(tmp_path):
    from tests.unit.test_dedup import _project_with_imports
    from wider.dedup.engine import _downstream_counts, decide, queue_rows, run
    p = _project_with_imports(tmp_path)
    run(p)
    st = p.store
    one = st.scalar("SELECT record_id FROM records WHERE title='Unique paper one'")
    q = queue_rows(p)[0]
    rec = lambda raw: st.scalar("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id "   # noqa: E731
                                "WHERE c.raw_id=?", (raw,))
    a_rec, b_rec = rec(q["raw_a"]), rec(q["raw_b"])
    _codebook(p, "frozen")
    st.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('dev','purposive','{}',2,'t')")
    sid = st.scalar("SELECT sample_id FROM samples WHERE name='dev'")
    st.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)", [(sid, one, 1), (sid, b_rec, 2)])
    P = pk.make(p, "R2", ["dev"], "independent")
    assert {it["record_id"] for it in pk.items(p, P["package_id"])} == {one, b_rec}
    data = _export(p, P["package_id"], n=2)
    # a redraw takes "Unique paper one" out of dev, its file no longer holds it, and the ambiguous pair merges
    st.execute("DELETE FROM sample_rows WHERE record_id=?", (one,))
    raw = st.scalar("SELECT raw_id FROM raw_records WHERE title='Unique paper one'")
    st.execute("DELETE FROM dedup_clusters WHERE raw_id=?", (raw,))
    st.execute("DELETE FROM raw_records WHERE raw_id=?", (raw,))
    with st.tx() as c:
        assert "review_package_items" not in _downstream_counts(c, one)
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    run(p)   # not refused because of the package's items
    assert not st.scalar("SELECT 1 FROM records WHERE record_id=?", (one,))
    survivor = a_rec if st.scalar("SELECT 1 FROM records WHERE record_id=?", (a_rec,)) else b_rec
    assert {it["record_id"] for it in pk.items(p, P["package_id"])} == {one, survivor}
    res = pk.import_file(p, _write(tmp_path, data))
    assert res["imported"] == 1 and res["lines"].count("1 item left the sample since the package was made; it was skipped.") == 1
    assert st.scalar("SELECT record_id FROM labels WHERE reviewer_id='R2'") == survivor


def test_import_undo_redo(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pid = P["package_id"]
    f = _write(tmp_path, _export(p, pid, n=20))
    c1 = pk.import_file(p, f)["change_id"]
    journal.undo(p, c1, author="Owner", other=True)
    assert not _r3_labels(p)
    row = next(r for r in pk.status(p)["packages"] if r["package_id"] == pid)
    assert row["imported_at"] is None and "not imported" in row["state"]
    c2 = pk.import_file(p, f)["change_id"]   # the same file imports again
    assert len(_r3_labels(p)) == 20
    u2 = journal.undo(p, c2, author="Owner", other=True)["change_id"]
    journal.undo(p, u2, author="Owner", other=True)   # redo: one change brings the 20 back
    assert len(_r3_labels(p)) == 20
    rid = next(iter(_r3_labels(p)))
    lb.label(p, "validation", rid, "R3", "exclude")
    with pytest.raises(ValueError, match="undo the later change first"):
        journal.undo(p, c2, author="Owner", other=True)


def test_import_undo_refused_while_its_adjudication_stands(tmp_path):
    """M105 hand check: an adjudication made after a package import rests on an imported label, so undoing the import
    is refused and names it (the adjudicator's decision is never taken away by the import's undo); once that
    adjudication is undone by its own change, the import undoes and leaves no adjudication behind. A first
    adjudication on a sealed record is never called "corrected" in History."""
    p = _project(tmp_path)
    pid = pk.make(p, "R3", ["validation"], "second")["package_id"]
    toks = pk.items(p, pid)
    rid = toks[0]["record_id"]
    r1 = p.store.scalar("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='R1'", (rid,))
    data = _export(p, pid, decisions={0: "include" if r1 == "exclude" else "exclude"})   # R3 disagrees with R1
    c1 = pk.import_file(p, _write(tmp_path, data))["change_id"]
    sealed = rid in lb.sealed_ids(p, dict(p.store.one("SELECT * FROM samples WHERE name='validation'")))
    lb.adjudicate(p, "validation", rid, "include", by="ana", reason="fits",
                  correction_reason="settles the disagreement" if sealed else None)
    adj = journal.changes(p)[0]
    assert "corrected the adjudication" not in adj["summary"] and f"ana adjudicated record {rid}" in adj["summary"]
    with pytest.raises(ValueError, match=f"record {rid} of sample validation was adjudicated since by ana "
                                         f"\\(change {adj['change_id']}\\).*undo that adjudication first"):
        journal.undo(p, c1, author="Owner", other=True)
    out = _cli(p, "history", "undo", str(c1), "--other", "--author", "Owner")
    assert out.exit_code != 0 and "undo that adjudication first" in " ".join(out.output.split())
    r = _client(p).post(f"/api/history/{c1}/undo", json={"other": True})
    assert r.status_code in (400, 409) and "undo that adjudication first" in r.text
    entry = next(x for x in journal.changes(p) if x["change_id"] == c1)
    assert entry["undoable"] is False and "adjudicated since by ana" in entry["why_not"]
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 1 and len(_r3_labels(p)) == 1   # nothing lost
    journal.undo(p, adj["change_id"], author="ana")
    journal.undo(p, c1, author="Owner", other=True)
    assert not _r3_labels(p) and p.store.scalar("SELECT COUNT(*) FROM adjudications") == 0
    assert p.store.scalar("SELECT COUNT(*) FROM label_corrections") == 0


def test_import_warns_other_reviewer_unfinished(tmp_path):
    p = _project(tmp_path, label_val=False)
    for rid in range(1, 13):
        lb.label(p, "dev", rid, "R1", "include")
    P = pk.make(p, "R2", ["dev"], "independent")
    assert P["n_items"] == 20
    res = pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=20)))
    line = ("R1 has not labelled 8 of these records yet. R2's labels are now visible in WIDER; for independent labels, "
            "R1 labels those first or works from a review package too.")
    assert res["lines"].count(line) == 1
    # a second-reviewer package holds the first reviewer's records: other second reviewers are owed nothing on it
    q = _project(tmp_path, "q")
    rid = q.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' AND decision='include' LIMIT 1")
    lb.label(q, "validation", rid, "R4", "include")
    Q = pk.make(q, "R3", ["validation"], "second")
    res = pk.import_file(q, _write(tmp_path, _export(q, Q["package_id"], n=28), "q.json"))
    assert not any("has not labelled" in x for x in res["lines"])


def test_label_changed_in_wider_loses_package_mark(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], {0: "exclude"})))
    rid = pk.items(p, P["package_id"])[0]["record_id"]
    lb.label(p, "validation", rid, "R3", "include", made_in="unblinded queue")
    lab = _r3_labels(p)[rid]
    assert lab["made_in"] == "unblinded queue" and pk.label_blinded(p, lab) == "no"
    ch = p.store.one("SELECT items FROM history WHERE kind='label' ORDER BY change_id DESC LIMIT 1")
    before = json.loads(ch["items"])[0]["before"][0]
    assert (before["made_in"], before["decision"]) == (P["package_id"], "exclude")
    q = _project(tmp_path, "q")
    rid = q.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' AND decision='include' LIMIT 1")
    lb.label(q, "validation", rid, "R4", "maybe", queue_mode="second")
    assert q.store.scalar("SELECT made_in FROM labels WHERE reviewer_id='R4'") == "blind queue"
    tok = lb._token(q, lb.sample_by_name(q, "validation")["sample_id"], rid, "second", "R4")
    lb.label_item(q, tok, "R4", "include")
    assert q.store.scalar("SELECT made_in FROM labels WHERE reviewer_id='R4'") == "blind queue"


# ------------------------------------------------------------------------------------------------ status and withdraw (4.3)
def test_status_cli_and_route_same(tmp_path):
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=28)))
    Q = pk.make(p, "R2", ["dev"], "independent")
    sv = lb.sample_by_name(p, "validation")["sample_id"]
    p.store.executemany("INSERT INTO records(record_id,cluster_id,key,title) VALUES(?,?,?,?)",
                        [(500 + i, 500 + i, f"n{i}", f"New {i}") for i in range(40)])
    p.store.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)",
                        [(sv, 500 + i, 500 + i) for i in range(40)])
    out = _cli(p, "blinding", "status", "--json")
    assert out.exit_code == 0
    cli = json.loads(out.output)
    assert cli == _client(p).get("/api/blinding/status").json()
    rows = {r["package_id"]: r for r in cli["packages"]}
    assert rows[Q["package_id"]]["state"] == f"out since {pk._date(Q['made_at'])}, not imported"
    assert rows[P["package_id"]]["lines"] == ["validation gained 40 records after this package was made; they are not in it"]
    assert rows[P["package_id"]]["state"].startswith("imported ") and "(28 of 28 decided)" in rows[P["package_id"]]["state"]
    text = _cli(p, "blinding", "status").output
    assert text.count("validation gained 40 records after this package was made; they are not in it") == 1
    assert f"out since {pk._date(Q['made_at'])}, not imported" in text


def test_withdraw_needs_reason_and_undoes(tmp_path):
    p = _project(tmp_path)
    Q = pk.make(p, "R2", ["dev"], "independent")
    qid = Q["package_id"]
    out = _cli(p, "blinding", "withdraw", qid)
    assert out.exit_code == 1 and "Give a reason." in out.output
    r = _client(p).post(f"/api/blinding/package/{qid}/withdraw", json={"reason": "  "})
    assert r.status_code == 400 and r.json()["detail"] == "Give a reason."
    out = _cli(p, "blinding", "withdraw", qid, "--reason", "R2 left the project")
    assert out.exit_code == 0
    row = next(r for r in pk.status(p)["packages"] if r["package_id"] == qid)
    assert row["withdrawn_at"] and row["withdraw_reason"] == "R2 left the project" and not row["open"]
    assert row["state"].startswith("withdrawn ") and row["state"].endswith(": R2 left the project")
    ch = p.store.one("SELECT * FROM history WHERE kind='package_withdraw'")
    assert ch["summary"] == f"Review package {qid} withdrawn: R2 left the project"
    journal.undo(p, ch["change_id"], author=ch["author"] or "Owner", other=True)
    assert pk.is_open(p, pk._row(p, qid))
    r = _client(p).post(f"/api/blinding/package/{qid}/withdraw", json={"reason": "again"})
    assert r.status_code == 200 and r.json()["withdraw_reason"] == "again"


def _r2_earlier(p, n=12):
    """R2 labels ``n`` of R1's includes and maybes in WIDER (all excludes, so they disagree with R1)."""
    ids = [r[0] for r in p.store.query("SELECT record_id FROM labels WHERE reviewer_id='R1' AND decision<>'exclude' "
                                       "ORDER BY record_id LIMIT ?", (n,))]
    for rid in ids:
        lb.label(p, "validation", rid, "R2", "exclude", reason="a", made_in="unblinded queue")
    return ids


def test_withdraw_after_import(tmp_path):
    p = _project(tmp_path)
    _r2_earlier(p)
    P = pk.make(p, "R2", ["validation"], "second", earlier="exclude")
    pid = P["package_id"]
    pk.import_file(p, _write(tmp_path, _export(p, pid, n=P["n_items"])))
    assert lb.agreement(p, "validation", "R1", "R2")["left_out"]["n"] == 12
    w = pk.withdraw(p, pid, "done early")
    n_lab = p.store.scalar("SELECT COUNT(*) FROM labels WHERE made_in=?", (pid,))
    assert n_lab == P["n_items"]
    assert lb.agreement(p, "validation", "R1", "R2")["left_out"]["n"] == 12   # still left out
    line = (f"{pid} was withdrawn after its decisions were imported; its {n_lab} labels stay, and the labels it left out "
            f"stay left out.")
    row = next(r for r in pk.status(p)["packages"] if r["package_id"] == pid)
    assert row["lines"].count(line) == 1 and _cli(p, "blinding", "status").output.count(line) == 1
    journal.undo(p, w["change_id"], author="Owner", other=True)
    assert not pk._row(p, pid)["withdrawn_at"]
    # withdrawn before any import: the excluded labels come back
    q = _project(tmp_path, "q")
    _r2_earlier(q)
    Q = pk.make(q, "R2", ["validation"], "second", earlier="exclude")
    assert lb.agreement(q, "validation", "R1", "R2")["left_out"]["n"] == 12
    pk.withdraw(q, Q["package_id"], "not sent")
    ag = lb.agreement(q, "validation", "R1", "R2")
    assert "left_out" not in ag and ag["n"] == 12


def test_texts_once_in_status(tmp_path):
    p = _project(tmp_path)
    for n in range(2):
        if n:
            pk.make(p, "R2", ["dev"], "independent")
            pk.make(p, "R3", ["validation"], "second")
        out = _cli(p, "blinding", "status").output
        flat = " ".join(out.split())
        assert flat.count(pk.TEXTS["reviewers"]) == 1 and flat.count(pk.TEXTS["limits"]) == 1
        st = pk.status(p)
        assert st["texts"].count(pk.TEXTS["reviewers"]) == 1 and st["texts"].count(pk.TEXTS["limits"]) == 1
        assert len(st["packages"]) == 2 * n


# ------------------------------------------------------------------------------------------------ earlier labels (4.4, 4.5)
def test_earlier_retain_rereview_exclude(tmp_path):
    from wider.recall.validation import review_lines
    base = _project(tmp_path)
    ids = _r2_earlier(base)
    import shutil
    base.close()
    copies = {}
    for choice in pk.EARLIER:
        shutil.copytree(base.root, tmp_path / choice)
        copies[choice] = Project.load(tmp_path / choice)
    # retain
    p = copies["retain"]
    P = pk.make(p, "R2", ["validation"], "second", earlier="retain")
    assert not set(ids) & {it["record_id"] for it in pk.items(p, P["package_id"])}
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=P["n_items"]), "r.json"))
    kept = {r["record_id"]: dict(r) for r in p.store.query("SELECT * FROM labels WHERE reviewer_id='R2'")}
    assert all(kept[r]["made_in"] == "unblinded queue" and pk.label_blinded(p, kept[r]) == "no" for r in ids)
    assert "left_out" not in lb.agreement(p, "validation", "R1", "R2")
    # re-review
    p = copies["re-review"]
    P = pk.make(p, "R2", ["validation"], "second", earlier="re-review")
    rep = {it["record_id"] for it in pk.items(p, P["package_id"]) if it["repeat"]}
    assert rep == set(ids)
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=P["n_items"]), "rr.json"))
    now = {r["record_id"]: dict(r) for r in p.store.query("SELECT * FROM labels WHERE reviewer_id='R2'")}
    assert all(now[r]["decision"] == "include" and now[r]["made_in"] == P["package_id"] for r in ids)
    assert all(pk.label_blinded(p, now[r]) == "repeat" for r in ids)
    out = _cli(p, "label", "revisions", "validation")
    assert out.output.count(f"re-reviewed in review package {P['package_id']}") == 12
    revs = lb.revisions(p, "validation")
    assert {r["cause"] for r in revs} == {"package re-review"} and all(r["old_decision"] == "exclude" for r in revs)
    assert not any("re-checked" in x for x in review_lines(p, "validation"))
    # exclude
    p = copies["exclude"]
    P = pk.make(p, "R2", ["validation"], "second", earlier="exclude")
    pid = P["package_id"]
    assert not set(ids) & {it["record_id"] for it in pk.items(p, pid)}
    pk.import_file(p, _write(tmp_path, _export(p, pid, n=P["n_items"]), "x.json"))
    ag = lb.agreement(p, "validation", "R1", "R2")
    line = f"12 labels left out by review package {pid}"
    assert ag["left_out"]["line"] == line and ag["n"] == P["n_items"]
    assert not set(ids) & {d["record_id"] for d in ag["disagreements"]}
    out = _cli(p, "label", "agreement", "validation", "R1", "R2")
    assert out.output.count(line) == 1
    from wider.recall.outputs import methods_draft
    assert methods_draft(p).count(line) == 1
    final = {r["record_id"]: r for r in lb.final_labels(p, "validation")}
    assert all(final[r]["open_disagreement"] for r in ids)   # still used for final labels
    d = pk.disagreements(p, "validation", "R1", "R2")
    marked = {r["record_id"]: r["b"]["left_out"] for r in d["rows"]}
    assert all(marked[r] for r in ids)
    r = _client(p).get("/api/labels/final", params={"sample": "validation"})
    assert r.status_code == 200
    for proj in copies.values():
        proj.close()


def test_package_items_count_as_second_reviewer(tmp_path):
    from wider.recall.validation import review_lines, second_reviewer_coverage, validation_checks
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    sv = lb.sample_by_name(p, "validation")["sample_id"]
    assert second_reviewer_coverage(p, sv) == {"n": 28, "done": 0}
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=28)))
    assert validation_checks(p, "validation")["second_reviewer"] == {"n": 28, "done": 28}
    out = _cli(p, "validation", "status", "validation", "--json")
    assert json.loads(out.output)["second_reviewer"] == {"n": 28, "done": 28}
    assert "14 of 14 have a second reviewer (queue enriched with 14 excludes)" in review_lines(p, "validation")


def test_validation_package_labels_never_tune(tmp_path):
    from wider.screen.evaluate import evaluate
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=28)))
    p.store.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) VALUES('r1','screen','s','h','done','t','d')")
    with pytest.raises(ValueError, match="probability"):
        evaluate(p, "r1", "validation")
    from wider.screen import compare
    with pytest.raises(ValueError, match="probability sample"):
        compare.check_sample(p, "validation")
    c = _client(p)
    r = c.post("/api/screen/compare/estimate", json={"name": "c1", "sample": "validation", "configs": ["fake"]})
    assert r.status_code == 400 and "probability sample" in r.json()["detail"]
    r = c.get("/api/screen/evaluate/r1", params={"sample": "validation"})
    assert r.status_code == 400 and "probability sample" in r.json()["detail"]


def test_no_package_changes_nothing(tmp_path):
    """A project that never makes a package: the same pages, labels.csv with the new columns empty of package values,
    no blinded-review line anywhere, and on the card only the two texts."""
    from wider.recall import report
    p = _project(tmp_path)
    c = _client(p)
    st = c.get("/api/blinding/status").json()
    assert st == {"texts": [pk.TEXTS["reviewers"], pk.TEXTS["limits"]], "packages": []}
    assert pk.dashboard_line(p) is None and pk.validation_out(p) == []
    assert pk.left_out(p) == {} and "left_out" not in lb.agreement(p, "validation", "R1", "R2")
    rid = p.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' LIMIT 1")
    lb.label(p, "validation", rid, "R2", "include", made_in="unblinded queue")
    assert p.store.scalar("SELECT made_in FROM labels WHERE reviewer_id='R2'") == "unblinded queue"
    assert "Blinded independent review" not in report.to_markdown(report.build_report(p))
    for page in ("/api/samples", "/api/labels/agreement?sample=validation&a=R1&b=R2"):
        assert c.get(page).status_code == 200
    assert not (p.root / "review-packages").exists()


# ------------------------------------------------------------------------------------------------ disagreements (5.1)
def test_disagreements_cli_and_route_same(tmp_path):
    """R1 and R3 disagree on 4 records of validation, R3's labels from package P, one pair on different abstract
    versions: the CLI and the reviewer table's route list the same 4 with each side's details and no model field;
    resolving one stores an adjudication and leaves both labels."""
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    its = pk.items(p, P["package_id"])
    first = {r["record_id"]: r["decision"] for r in p.store.query("SELECT record_id, decision FROM labels WHERE reviewer_id='R1'")}
    other = {"include": "exclude", "maybe": "exclude", "exclude": "include"}
    decisions = {i: (other[first[it["record_id"]]] if i < 4 else first[it["record_id"]]) for i, it in enumerate(its)}
    data = _export(p, P["package_id"], decisions)
    data["items"][0].update(reason="b", ticks={"e1": "no"}, tags=["check"], note="R3 note")
    from wider.enrich import abstracts
    rid0 = its[0]["record_id"]
    abstracts.add_version(p.store, rid0, " ".join(["w"] * 240), "openalex")
    p.store.execute("UPDATE review_package_items SET abstract_version=2 WHERE token=?", (its[0]["token"],))
    p.store.execute("UPDATE labels SET abstract_version=1 WHERE reviewer_id='R1'")
    pk.import_file(p, _write(tmp_path, data))
    out = _cli(p, "blinding", "disagreements", "validation", "--a", "R1", "--b", "R3", "--json")
    assert out.exit_code == 0, out.output
    cli = json.loads(out.output)
    route = _client(p).get("/api/labels/compare", params={"sample": "validation", "a": "R1", "b": "R3"}).json()
    assert cli == route and len(cli["rows"]) == 4
    assert {r["record_id"] for r in cli["rows"]} == {it["record_id"] for it in its[:4]}
    row = next(r for r in cli["rows"] if r["record_id"] == rid0)
    assert (row["b"]["reason"], row["b"]["ticks"], row["b"]["tags"], row["b"]["note"]) == ("b", {"e1": "no"}, ["check"], "R3 note")
    assert (row["b"]["made_in"], row["b"]["blinded"], row["a"]["made_in"], row["a"]["blinded"]) == \
        (P["package_id"], "yes", "wider", "no")
    assert row["different_abstracts"] == "R1 and R3 read different abstracts (imported, 14 words; OpenAlex, 240 words)"
    assert sum(1 for r in cli["rows"] if r["different_abstracts"]) == 1
    blob = json.dumps(cli)
    for word in ("model", "confidence", "rule_decision", "parsed", "provider"):
        assert word not in blob
    text = _cli(p, "blinding", "disagreements", "validation", "--a", "R1", "--b", "R3").output
    assert "4 records where R1 and R3 differ on validation" in text and row["different_abstracts"] in text
    every = json.loads(_cli(p, "blinding", "disagreements", "validation", "--a", "R1", "--b", "R3", "--all", "--json").output)
    assert len(every["rows"]) == 28 and every == _client(p).get(
        "/api/labels/compare", params={"sample": "validation", "a": "R1", "b": "R3", "all": "true"}).json()
    before = {r["reviewer_id"]: dict(r) for r in p.store.query("SELECT * FROM labels WHERE record_id=?", (rid0,))}
    res = _cli(p, "label", "adjudicate", "validation", str(rid0), "include", "--by", "Owner", "--reason", "full text read")
    assert res.exit_code == 0, res.output
    after = {r["reviewer_id"]: dict(r) for r in p.store.query("SELECT * FROM labels WHERE record_id=?", (rid0,))}
    assert after == before
    final = {r["record_id"]: r for r in lb.final_labels(p, "validation")}
    assert final[rid0]["decision"] == "include" and final[rid0]["source"] == "adjudication"
    adj = p.store.one("SELECT reason, decided_by FROM adjudications WHERE record_id=?", (rid0,))
    assert (adj["reason"], adj["decided_by"]) == ("full text read", "Owner")


# ------------------------------------------------------------------------------------------------ risk-checker (10.5)
def test_import_keeps_label_changed_in_wider(tmp_path):
    """R2: a label the reviewer made or changed in WIDER after the package is never overwritten by an import, and the
    import says so once; the package's own labels still update from a later export."""
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    pid = P["package_id"]
    its = pk.items(p, pid)
    pk.import_file(p, _write(tmp_path, _export(p, pid, {0: "include", 1: "exclude"}, seq=1), "a.json"))
    rid0 = its[0]["record_id"]
    lb.label(p, "validation", rid0, "R3", "exclude", reason="a", note="changed after talking to R3",
             made_in="unblinded queue")
    rid2 = its[2]["record_id"]
    lb.label(p, "validation", rid2, "R3", "maybe", note="my careful WIDER note", made_in="unblinded queue")   # made in WIDER, never imported
    res = pk.import_file(p, _write(tmp_path, _export(p, pid, {0: "include", 1: "maybe", 2: "include"}, seq=2), "b.json"))
    labs = _r3_labels(p)
    assert (labs[rid0]["decision"], labs[rid0]["note"], labs[rid0]["made_in"]) == ("exclude", "changed after talking to R3", "unblinded queue")
    assert (labs[rid2]["decision"], labs[rid2]["note"], labs[rid2]["made_in"]) == ("maybe", "my careful WIDER note", "unblinded queue")
    assert labs[its[1]["record_id"]]["decision"] == "maybe" and labs[its[1]["record_id"]]["made_in"] == pid
    assert sum("made or changed in WIDER after the package was made" in x for x in res["lines"]) == 1
    assert res["skipped"]["kept_wider"] == 2


def test_package_changes_need_a_name_on_the_page(tmp_path):
    """R3: making, importing and withdrawing from the page are signed; the CLI takes --by."""
    p = _project(tmp_path)
    c = TestClient(create_app(p))   # no X-Wider-Author header
    r = c.post("/api/blinding/package", json={"reviewer": "R3", "samples": ["validation"], "mode": "second"})
    assert r.status_code == 400 and "name is needed" in r.json()["detail"]
    assert not p.store.scalar("SELECT COUNT(*) FROM review_packages")
    out = _cli(p, "blinding", "package", "R3", "--sample", "validation", "--mode", "second", "--out",
               str(tmp_path / "x.html"), "--by", "Owner")
    assert out.exit_code == 0, out.output
    assert p.store.scalar("SELECT author FROM history WHERE kind='review_package'") == "Owner"
    pid = pk.status(p)["packages"][0]["package_id"]
    f = _write(tmp_path, _export(p, pid, n=2))
    with f.open("rb") as fh:
        assert c.post("/api/blinding/import", files={"file": ("d.json", fh, "application/json")}).status_code == 400
    assert c.post(f"/api/blinding/package/{pid}/withdraw", json={"reason": "x"}).status_code == 400
    assert _cli(p, "blinding", "import", str(f), "--by", "Owner").exit_code == 0
    assert _cli(p, "blinding", "withdraw", pid, "--reason", "done", "--by", "Owner").exit_code == 0
    assert {r[0] for r in p.store.query("SELECT author FROM history WHERE kind LIKE 'package_%'")} == {"Owner"}
