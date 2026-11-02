"""reviewer-roles-and-blinding, group 7: blinded review in the report, the methods draft and the share bundle, and
the recall sentence while a validation package is out (recall-and-outputs). Synthetic records and reviewers only."""
from __future__ import annotations

import csv
import json

import pytest

from tests.unit.test_package import _cli, _codebook, _export, _project, _write
from wider.recall.outputs import methods_draft
from wider.recall.report import build_report, share_bundle, to_markdown
from wider.sample import labels as lb
from wider.sample import package as pk

LIMITS = pk.TEXTS["limits"]


def _two_packages(tmp_path):
    """R3's package P (second, validation, 28 items) made 2 and imported 9 October; R2's package Q (independent,
    development, its 12 earlier labels re-reviewed)."""
    p = _project(tmp_path)
    for rid in range(1, 13):
        lb.label(p, "dev", rid, "R2", "exclude")
    P = pk.make(p, "R3", ["validation"], "second")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=28), "p.json"))
    Q = pk.make(p, "R2", ["dev"], "independent", earlier="re-review")
    pk.import_file(p, _write(tmp_path, _export(p, Q["package_id"], n=Q["n_items"]), "q.json"))
    p.store.execute("UPDATE review_packages SET made_at='2026-10-02T09:00:00+00:00', imported_at='2026-10-09T09:00:00+00:00' "
                    "WHERE package_id=?", (P["package_id"],))
    return p, P, Q


def test_report_methods_bundle_section(tmp_path):
    p, P, Q = _two_packages(tmp_path)
    rep = build_report(p)
    md = to_markdown(rep)
    line_p = ("R3: second reviewer on 28 validation records (criteria version 3), blind in a review package, 2 to 9 October "
              "2026; labels used for agreement and final labels")
    line_q = ("R2 labelled 12 records again; a repeat review does not restore independence when the reviewer has seen "
              "earlier judgments")
    assert md.count("## Blinded independent review") == 1
    assert md.count(line_p) == 1 and md.count(line_q) == 1 and md.count(LIMITS) == 1
    assert "blind queue in WIDER (History visible)" in md
    assert "with repeats" in md and "without" in md
    assert pk.ONE_REVIEWER not in md
    att_md = methods_draft(p)
    # the methods draft is a shared file: reviewers as exports.attribution says (pseudonyms by default)
    from wider.core.researchers import Attributor
    who = Attributor(p).who
    assert att_md.count("Blinded independent review: ") == 1
    assert line_p.replace("R3", who("R3")) in att_md and line_q.replace("R2", who("R2")) in att_md and LIMITS in att_md
    dest = share_bundle(p, tmp_path / "bundle")
    pkgs = json.loads((dest / "review_packages.json").read_text())
    assert {x["package_id"] for x in pkgs} == {P["package_id"], Q["package_id"]}
    text = (dest / "review_packages.json").read_text()
    for it in pk.items(p, P["package_id"]) + pk.items(p, Q["package_id"]):
        assert it["token"] not in text
    x = next(x for x in pkgs if x["package_id"] == P["package_id"])
    assert (x["mode"], x["sample_kind"], x["items"], x["codebook_version"], x["labels"]) == \
        ("second", "validation", 28, 3, {"blind": 28, "repeat": 0})
    assert x["reviewer"] == who("R3")
    for name in ("R1", "R2", "R3"):   # no name anywhere in the file, keys included (ui-tester F1)
        assert f'"{name}' not in text and f":{name}\"" not in text and f":{name}" not in text
    assert set(x["agreement"]) == {f"validation:{who('R1')}"}
    assert "R3" not in (dest / "report.md").read_text()   # report.md names reviewers by the attribution


def test_labels_csv_columns(tmp_path):
    p, P, Q = _two_packages(tmp_path)
    lb.label(p, "validation", p.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' LIMIT 1"), "R4",
             "include", queue_mode="second")
    dest = share_bundle(p, tmp_path / "bundle")
    with open(dest / "labels.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0])[-4:] == ["made_in", "blinded", "left_out", "abstract_version"] and "tags" not in rows[0]
    by = {}
    for r in rows:
        by.setdefault((r["made_in"], r["blinded"]), 0)
        by[(r["made_in"], r["blinded"])] += 1
    assert by[(P["package_id"], "yes")] == 28 and by[(Q["package_id"], "repeat")] == 12
    assert by[("blind queue", "queue")] == 1 and by[("wider", "no")] >= 200
    assert {r["left_out"] for r in rows if r["made_in"]} == {"no"}
    # an exclude choice marks the labels it leaves out
    q = _project(tmp_path, "q")
    for rid in range(1, 5):
        lb.label(q, "dev", rid, "R2", "include")
    pk.make(q, "R2", ["dev"], "independent", earlier="exclude")
    d2 = share_bundle(q, tmp_path / "b2")
    with open(d2 / "labels.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert sum(r["left_out"] == "yes" for r in rows) == 4


def test_abstract_differences_reported(tmp_path):
    from wider.enrich import abstracts
    p = _project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    its = pk.items(p, P["package_id"])
    for it in its[:3]:   # R3 read an enriched abstract of three records, R1 the imported one
        p.store.execute("UPDATE review_package_items SET abstract_version=2 WHERE token=?", (it["token"],))
        abstracts.add_version(p.store, it["record_id"], " ".join(["w"] * 80), "openalex")
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], n=28)))
    md = to_markdown(build_report(p))
    assert md.count("3 records were labelled by R1 and R3 on different abstract versions") == 1
    dest = share_bundle(p, tmp_path / "bundle")
    assert "abstract_version" in (dest / "labels.csv").read_text().splitlines()[0]


def test_no_section_without_package(tmp_path):
    p = _project(tmp_path)
    lb.label(p, "validation", p.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' LIMIT 1"), "R2", "include",
             made_in="unblinded queue")
    md, m = to_markdown(build_report(p)), methods_draft(p)
    assert "Blinded independent review" not in md and "Blinded independent review" not in m
    assert pk.ONE_REVIEWER not in md and pk.ONE_REVIEWER not in m
    dest = share_bundle(p, tmp_path / "bundle")
    assert not (dest / "review_packages.json").exists()
    with open(dest / "labels.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["made_in"]]
    assert {(r["made_in"], r["blinded"], r["left_out"]) for r in rows} == {("wider", "no", "no"), ("unblinded queue", "no", "no")}


def test_one_reviewer_said_once(tmp_path):
    p = _project(tmp_path)
    md, m = to_markdown(build_report(p)), methods_draft(p)
    assert md.count(pk.ONE_REVIEWER) == 1 and m.count(pk.ONE_REVIEWER) == 1
    assert "Blinded independent review" not in md and LIMITS not in md
    pk.make(p, "R3", ["validation"], "second")
    md = to_markdown(build_report(p))
    assert pk.ONE_REVIEWER not in md and "Blinded independent review" in md


def _recall_project(tmp_path):
    from tests.unit.test_validation_suggest import _drawn
    p = _drawn(tmp_path)
    _codebook(p, "frozen")
    return p


def test_recall_says_package_out_once(tmp_path, monkeypatch):
    """Under label-completion-and-sealing D4 an open validation package keeps the sample incomplete: recall is refused
    with "R3's review package on validation is not finished" said once (estimate, CLI, API, report), no look recorded,
    while any item is undecided; then the refusal counts the records R1 and R3 disagree on until they are
    adjudicated. A withdrawn package and a development package never bring the sentence."""
    from fastapi.testclient import TestClient

    from wider.rank import score
    from wider.recall.estimate import estimate
    from wider.recall.validation import LabelsIncomplete
    from wider.ui.app import create_app

    def no_ranking_check(project):   # the synthetic ranking has no embedding set behind it
        raise ValueError("no ranking check")
    monkeypatch.setattr(score, "validate", no_ranking_check)
    sentence = "R3's review package on validation is not finished"

    def refusal(project):
        with pytest.raises(LabelsIncomplete) as e:
            estimate(project, "validation", "main", B=50, record_result=False)
        return e.value.notice

    def looks(project):
        return project.store.scalar("SELECT COUNT(*) FROM validation_looks")

    p = _recall_project(tmp_path)
    P = pk.make(p, "R3", ["validation"], "second")
    its = pk.items(p, P["package_id"])
    r1 = {r[0]: r[1] for r in p.store.query("SELECT record_id, decision FROM labels WHERE reviewer_id='r1'")}
    flip = {"include": "exclude", "exclude": "include", "maybe": "exclude"}
    assert refusal(p).count(sentence) == 1 and looks(p) == 0
    out = _cli(p, "recall", "main", "--sample", "validation", "-B", "50")
    assert out.exit_code == 1, out.output
    assert " ".join(out.output.split()).count(sentence) == 1
    api = TestClient(create_app(p)).get("/api/recall?protocol=main&sample=validation&B=50").json()
    assert api["labels_incomplete"] is True and api["notice"].count(sentence) == 1
    assert to_markdown(build_report(p, protocol="main", sample="validation", B=50)).count(sentence) == 1
    assert looks(p) == 0
    # the first half imported, R3 differing from R1 on one record: still refused while items are undecided
    half = len(its) // 2
    first = {i: (flip[r1[its[i]["record_id"]]] if i == 0 else r1[its[i]["record_id"]]) for i in range(half)}
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], first)))
    assert refusal(p).count(sentence) == 1 and looks(p) == 0
    # the last items imported (one more disagreement): the sentence is gone, the open disagreements are counted
    rest = {i: (flip[r1[its[i]["record_id"]]] if i == half else r1[its[i]["record_id"]]) for i in range(half, len(its))}
    pk.import_file(p, _write(tmp_path, _export(p, P["package_id"], rest, seq=2), "dec2.json"))
    notice = refusal(p)
    assert sentence not in notice and "2 records have labels that disagree and are not adjudicated" in notice
    assert looks(p) == 0
    for i in (0, half):
        lb.adjudicate(p, "validation", its[i]["record_id"], r1[its[i]["record_id"]], by="Owner")
    r = estimate(p, "validation", "main", B=50, record_result=False)
    assert not any(sentence in x or "is out and not imported" in x or "disagree" in x for x in r["lines"])
    # a withdrawn package and a development package never bring the sentence
    q = _recall_project(tmp_path / "q")
    Q = pk.make(q, "R3", ["validation"], "second")
    pk.withdraw(q, Q["package_id"], "not sent")
    assert not any(sentence in x for x in estimate(q, "validation", "main", B=50, record_result=False)["lines"])
    d = _recall_project(tmp_path / "d")
    ids = [r[0] for r in d.store.query("SELECT record_id FROM records ORDER BY record_id LIMIT 3")]
    d.store.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('dev','purposive','{}',3,'t')")
    sd = d.store.scalar("SELECT sample_id FROM samples WHERE name='dev'")
    d.store.executemany("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)", [(sd, i, i) for i in ids])
    pk.make(d, "R3", ["dev"], "independent")
    assert not any(sentence in x for x in estimate(d, "validation", "main", B=50, record_result=False)["lines"])


def test_one_reviewer_not_said_with_a_second_in_wider(tmp_path):
    """A second reviewer who labelled in WIDER (no package): neither the report nor the methods draft says one
    reviewer; nor does the methods draft once a package exists."""
    p = _project(tmp_path)
    rid = p.store.scalar("SELECT record_id FROM labels WHERE reviewer_id='R1' LIMIT 1")
    lb.label(p, "validation", rid, "R2", "include")
    assert pk.ONE_REVIEWER not in to_markdown(build_report(p)) and pk.ONE_REVIEWER not in methods_draft(p)
    q = _project(tmp_path, "q")
    pk.make(q, "R3", ["validation"], "second")
    assert pk.ONE_REVIEWER not in methods_draft(q)


def test_withdraw_reason_name_hidden_in_report(tmp_path):
    """risk-checker R1: a researcher's name typed into a withdraw reason is written by the attribution setting in
    report.md and report.json, like the History row; and agreement is given with and without repeats."""
    from wider.recall.report import write_report
    p, P, Q = _two_packages(tmp_path)
    lb.add_reviewer(p, "Carla Diaz")
    from wider.core import researchers
    researchers.register(p, "Carla Diaz", "option")
    pk.withdraw(p, P["package_id"], "Carla Diaz left the project", by="Owner Person")
    md, js = write_report(p, record_result=False)
    assert "Carla" not in md.read_text() and "Carla" not in js.read_text()
    assert "Owner Person" not in md.read_text()
    a = json.loads(js.read_text())["blinded_review"]["packages"][0]["agreement"]
    assert all(set(v) == {"with_repeats", "without_repeats"} for v in a.values()) and a
