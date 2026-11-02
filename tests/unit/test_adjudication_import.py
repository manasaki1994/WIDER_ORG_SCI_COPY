"""An outside adjudicator's decisions for some records of a set, imported into a new set (partial-adjudication-import).

Synthetic, topic-neutral records only ("Paper N about ..."); the names Ana, Bea and Jo are made up."""
import csv
import io
import json
from pathlib import Path

import pytest

from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401
from wider.core import journal
from wider.providers import fake as fakemod
from wider.screen import result_sets as rs
from wider.screen.adjudication import NEED_NAME, import_adjudication
from wider.screen.protocol import run_protocol
from wider.screen.review import Refusal

ROWS_OF = ("protocol", "result_set", "record_id", "title", "doi", "decision", "adjudication", "adjudicator", "note")


@pytest.fixture
def screened(_project_fixture, monkeypatch):  # noqa: F811
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    run_protocol(_project_fixture, "sample:development", "main")
    return _project_fixture


def _decisions(p, key="main"):
    return {r["record_id"]: dict(r) for r in p.store.query("SELECT * FROM screen_decisions WHERE protocol=? "
                                                          "ORDER BY record_id", (key,))}


def _export(p, result_set=None):
    """The Results export of every decision of the set, as CSV rows."""
    from wider.recall.selection import export_selection

    res = export_selection(p, "main", result_set, preset="every", formats=["csv"])
    with open(p.root / res["file"], encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _csv(rows, extra=()) -> bytes:
    cols = list(rows[0]) if rows else list(ROWS_OF)
    cols += [c for c in ("adjudication", *extra) if c not in cols]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    return buf.getvalue().encode("utf-8")


def _fill(rows, **by_id):
    """Set the ``adjudication`` cell of the rows of records ``r<id>=decision``."""
    want = {int(k[1:]): v for k, v in by_id.items()}
    for r in rows:
        r["adjudication"] = want.get(int(r["record_id"]), "")
    return rows


def _state(p):
    st = p.store
    return (st.query("SELECT * FROM screen_result_sets ORDER BY key"),
            st.query("SELECT * FROM screen_decisions ORDER BY protocol, record_id"),
            st.query("SELECT * FROM researchers ORDER BY 1"),
            st.query("SELECT change_id FROM history"))


def _flip(d):
    return "exclude" if d == "include" else "include"


def test_partial_import_keeps_the_rest(screened):
    p, st = screened, screened.store
    before = _decisions(p)
    assert len(before) == 7
    ids = sorted(before)
    a, b, c = ids[0], ids[3], ids[4]
    # one record's set 1 decision was carried from another set
    st.execute("UPDATE screen_decisions SET origin=?, decided_by=? WHERE protocol='main' AND record_id=?",
               (json.dumps({"kind": "reused", "from": "other"}), "carried from other", c))
    before = _decisions(p)
    rows = _fill(_export(p), **{f"r{a}": "include", f"r{b}": "include", f"r{c}": "exclude"})
    res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    assert res["written"] and res["result_set"] == 2 and res["base_set"] == 1
    s2 = st.one("SELECT * FROM screen_result_sets WHERE key='main#2'")
    assert s2["source"] == "adjudication" and s2["active"] == 1 and json.loads(s2["derived_from"])["set"] == 1
    assert st.scalar("SELECT active FROM screen_result_sets WHERE key='main'") == 0
    after = _decisions(p, "main#2")
    from wider.screen.agreement import route_of
    for rid, dec in ((a, "include"), (b, "include"), (c, "exclude")):
        d = after[rid]
        assert d["decision"] == dec and d["decided_by"] == "researcher"
        assert json.loads(d["votes"])[-1] == f"researcher:{dec}"
        assert d["reason"] == "researcher (Ana) decided as adjudicator"
        o = json.loads(d["origin"])
        assert o["kind"] == "adjudication" and o["before_review"] == before[rid]["decision"]
        assert o["adjudication"]["base_set"] == 1 and o["adjudication"]["by"] == "Ana"
        assert route_of(d["decided_by"], o, json.loads(d["votes"]), d["decision"], 2) == "researcher"
    assert json.loads(after[c]["origin"])["adjudication"]["base_origin"]["kind"] == "reused"
    cols = ("decision", "votes", "cell", "labels", "reason", "decided_by", "origin")
    for rid in set(ids) - {a, b, c}:
        assert {k: after[rid][k] for k in cols} == {k: before[rid][k] for k in cols}
    assert _decisions(p) == before   # set 1 unchanged
    cnt = res["counts"]
    assert cnt["imported"] == 3 and cnt["changed"] + cnt["confirmed"] == 3 and cnt["blank"] == 4
    assert cnt["changed"] == sum(before[r]["decision"] != d for r, d in ((a, "include"), (b, "include"), (c, "exclude")))
    assert (f"Set 2 of main is now active (adjudicated from set 1: {cnt['changed']} changed, {cnt['confirmed']} "
            "confirmed). Set 1 is unchanged; wider screen sets use main 1 makes it active again.") in res["lines"]


def _bad_rows(p):
    """A file with one usable row and one row for each reason a row is left out (D4)."""
    st = p.store
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    proto = dict(rows[0], adjudication="")
    add = []
    add.append(dict(proto, record_id=str(ids[1]), adjudication="perhaps"))
    add.append(dict(proto, record_id="", title="A title nobody wrote", doi="10.9999/none", adjudication="include"))
    add.append(dict(proto, record_id=str(ids[2]), title="Another project's paper", adjudication="include"))
    add.append(dict(proto, record_id=str(ids[0]), title=rows[0]["title"], adjudication=dec[ids[0]]["decision"]))
    t3 = st.scalar("SELECT title FROM records WHERE record_id=?", (ids[3],))
    add.append(dict(proto, record_id="", doi="", title=t3 + " x", adjudication="include"))
    return rows + add, ids


def test_rows_left_out_are_listed(screened):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = screened
    rows, ids = _bad_rows(p)
    data = _csv(rows)
    res = import_adjudication(p, "main", data, "adj.csv", adjudicator="Ana", dry_run=True)
    by = {x["reason"]: x for x in res["left_out"]}
    assert set(by) >= {"unreadable", "unmatched", "title_differs", "duplicate", "similar_title"}
    assert by["duplicate"]["conflict"] and by["duplicate"]["record_id"] == ids[0]
    assert by["similar_title"]["score"] and by["similar_title"]["file_title"].endswith(" x")
    assert all(x["text"].startswith(f"row {x['row']}: ") for x in res["left_out"])
    assert res["counts"]["imported"] == 1
    f = p.root / "adj.csv"
    f.write_bytes(data)
    out = CliRunner().invoke(cli_app, ["screen", "import-adjudication", "main", str(f), "--adjudicator", "Ana",
                                       "--dir", str(p.root)])
    assert out.exit_code == 0, out.output
    for x in res["left_out"]:
        assert x["text"] in out.output
    assert _decisions(p, "main#2")[ids[1]] == {**_decisions(p)[ids[1]], "protocol": "main#2"}


def test_hidden_and_failed_reads_are_left_out(screened):
    p, st = screened, screened.store
    from wider.screen import adjudication as adj

    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{i}": _flip(dec[i]["decision"]) for i in ids[:4]})
    real_state, real_hidden = adj._state, adj._hidden

    def state(project, t):
        s, d = real_state(project, t)
        s[ids[1]] = dict(s[ids[1]], failed=True, question_id=41)
        s[ids[2]] = dict(s[ids[2]], failed_answered=True, failed_by="Bea")
        return s, d

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(adj, "_state", state)
    mp.setattr(adj, "_hidden", lambda project: real_hidden(project) | {ids[3]})
    try:
        res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", dry_run=True)
        assert st.scalar("SELECT COUNT(*) FROM screen_result_sets") == 1
        real = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    finally:
        mp.undo()
    by = {x["reason"]: x for x in res["left_out"]}
    assert by["failed_read"]["record_id"] == ids[1] and "wider questions answer 41" in by["failed_read"]["text"]
    assert by["failed_answered"]["record_id"] == ids[2] and "Bea" in by["failed_answered"]["text"]
    assert by["hidden"]["record_id"] == ids[3] and "a review decision waits until then" in by["hidden"]["text"]
    assert res["counts"]["imported"] == 1 and real["left_out"] == res["left_out"]
    new = _decisions(p, real["key"])
    for i in ids[1:4]:   # left out: the new set holds set 1's decision unchanged
        assert {k: new[i][k] for k in ("decision", "votes", "reason")} == \
            {k: dec[i][k] for k in ("decision", "votes", "reason")}


def test_dry_run_writes_nothing(screened):
    p = screened
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    was = _state(p)
    dry = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", dry_run=True)
    assert _state(p) == was and not dry["written"]
    assert dry["lines"][0].startswith("Dry run, nothing written: adj.csv: 1 rows used (1 changed, 0 confirmed)")
    real = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    assert real["counts"] == dry["counts"] and real["left_out"] == dry["left_out"]


def test_route_matches_cli(screened, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    p = screened
    rows, _ = _bad_rows(p)
    data = _csv(rows)
    f = p.root / "adj.csv"
    f.write_bytes(data)
    c = TestClient(create_app(p))
    files = {"file": ("adj.csv", data, "text/csv")}
    r = c.post("/api/screen/adjudication", data={"protocol": "main", "adjudicator": "Ana", "dry_run": "true",
                                                  "result_set": "1"}, files=files, headers={"X-Wider-Author": "Bea"})
    assert r.status_code == 200, r.text
    dry = r.json()
    out = CliRunner().invoke(cli_app, ["screen", "import-adjudication", "main", str(f), "--adjudicator", "Ana",
                                       "--dry-run", "--dir", str(p.root)])
    assert out.exit_code == 0, out.output
    for line in dry["lines"]:
        assert line in out.output
    assert p.store.scalar("SELECT COUNT(*) FROM screen_result_sets") == 1
    r = c.post("/api/screen/adjudication", data={"protocol": "main", "adjudicator": "Ana", "result_set": "1"},
               files=files, headers={"X-Wider-Author": "Bea"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["written"] and body["counts"] == dry["counts"] and body["left_out"] == dry["left_out"]
    h = journal.changes(p, kind="adjudication_import")
    assert len(h) == 1 and h[0]["author"] == "Bea"
    # the uploaded file is never saved, and its name is not stored
    assert [x for x in p.root.rglob("adj*.csv")] == [f]
    assert not p.store.query("SELECT 1 FROM screen_result_sets WHERE import_file IS NOT NULL")


def test_refusals_change_nothing(screened):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.ui.app import create_app

    p, st = screened, screened.store
    dec = _decisions(p)
    ids = sorted(dec)
    good = _fill(_export(p), **{f"r{ids[0]}": "include"})
    no_col = [{k: v for k, v in r.items() if k != "adjudication"} for r in good]
    blank = _fill(_export(p))
    cases = [(b"\xff\xfe\x00bad", "not a CSV file WIDER can read", 1, {}),
             (_csv(no_col).replace(b",adjudication", b""), "column adjudication not found", 1, {}),
             (_csv(blank), "has a usable decision", 1, {}),
             (_csv(good), NEED_NAME, 2, {"adjudicator": None}),
             (_csv(good), "Researcher", 2, {"adjudicator": "Researcher 1"})]
    was = _state(p)
    c = TestClient(create_app(p))
    for data, words, code, kw in cases:
        name = kw.get("adjudicator", "Ana")
        with pytest.raises(Refusal) as e:
            import_adjudication(p, "main", data, "adj.csv", adjudicator=name)
        assert words in str(e.value.args[0]) and e.value.code == code
        f = p.root / "adj.csv"
        f.write_bytes(data)
        args = ["screen", "import-adjudication", "main", str(f), "--dir", str(p.root)]
        if name:
            args += ["--adjudicator", name]
        out = CliRunner().invoke(cli_app, args)
        assert out.exit_code == code and words in " ".join(out.output.split()), out.output
        form = {"protocol": "main", **({"adjudicator": name} if name else {})}
        r = c.post("/api/screen/adjudication", data=form, files={"file": ("adj.csv", data, "text/csv")})
        assert r.status_code == 400 and words in r.text
        assert _state(p) == was
    # an unfinished set: one decision removed
    st.execute("DELETE FROM screen_decisions WHERE protocol='main' AND record_id=?", (ids[-1],))
    was = _state(p)
    with pytest.raises(Refusal, match="set 1 of main is not finished") as e:
        import_adjudication(p, "main", _csv(good), "adj.csv", adjudicator="Ana")
    assert e.value.code == 1 and _state(p) == was


def test_file_from_another_set_is_said(screened):
    p = screened
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    res = import_adjudication(p, "main", _csv(_fill(rows, **{f"r{ids[1]}": "maybe"})), "adj2.csv", adjudicator="Ana")
    assert res["base_set"] == 2 and res["result_set"] == 3
    line = ("This file was exported from set 1 of main; it was imported into set 2 (the active set). Records whose "
            "decision changed between the sets keep the adjudicator's decision.")
    assert res["other_set_line"] == line and res["lines"].count(line) == 1


def test_adjudicator_in_shared_files(screened, monkeypatch, tmp_path):
    import re
    import zipfile

    from wider.core import researchers
    from wider.recall.report import share_bundle
    from wider.recall.selection import write_audit
    from wider.screen.export import export_protocol

    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    p = screened
    researchers.set_attribution(p, "pseudonyms")
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"]), f"r{ids[1]}": "maybe"})
    for r in rows:
        r["adjudicator"] = "Jo" if int(r["record_id"]) == ids[1] else ""
    res = import_adjudication(p, "main", _csv(rows, extra=("adjudicator",)), "adj_Jo.csv", adjudicator="Ana")
    assert res["written"] and res["adjudicators"] == ["Ana", "Jo"]
    out = export_protocol(p, "main")
    assert out["decisions"] == "exports/screening/main.s2_decisions.csv"
    with open(p.root / out["decisions"], encoding="utf-8") as f:
        reasons = {int(r["record_id"]): r["reason"] for r in csv.DictReader(f)}
    who = {e["name"]: e for e in researchers.entries(p)}
    assert reasons[ids[0]] == f"researcher ({who['Ana']['pseudonym']}) decided as adjudicator"
    assert reasons[ids[1]] == f"researcher ({who['Jo']['pseudonym']}) decided as adjudicator"
    assert who["Ana"]["status"] == "declared" and who["Jo"]["status"] == "inferred"
    assert who["Jo"]["source"] == "from an adjudication file"
    audit = write_audit(p, "main")
    with open(p.root / audit["file"] if "file" in audit else p.root / "exports" / "references" / "main.s2_audit.csv",
              encoding="utf-8") as f:
        arows = {int(r["record_id"]): r for r in csv.DictReader(f)}
    assert arows[ids[0]]["decided_by"] == "researcher"
    bundle = share_bundle(p, tmp_path / "bundle")
    files = [x for x in [*(p.root / "exports").rglob("*"), *Path(bundle).rglob("*")] if x.is_file()]
    textual = (".csv", ".md", ".json", ".txt", ".yaml", ".xml", ".rels", ".bib", ".ris", ".html", ".svg")
    for x in files:
        if zipfile.is_zipfile(x):   # a ZIP, a .docx: the text parts, not the pictures
            with zipfile.ZipFile(x) as z:
                texts = [z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(textual)]
        else:
            texts = [x.read_text(encoding="utf-8", errors="replace")] if x.suffix in textual else []
        assert x.suffix in textual or zipfile.is_zipfile(x) or x.suffix in (".png", ".pdf"), x
        for t in texts:
            assert not re.search(r"\bAna\b|\bJo\b|adj_Jo", t), x


def test_adjudication_set_not_reused(screened):
    p = screened
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    s2 = rs.active_set(p, "main")
    assert s2["result_set"] == 2
    ids_all = rs._scope_ids(p, "sample:development")
    assert (rs.reusable(p, "main", s2["settings_hash"], ids_all) or {}).get("set") != 2
    assert rs.reusable(p, "main", s2["settings_hash"], ids_all, reuse_from=2) is None
    with pytest.raises(ValueError, match="set 2 of main holds an adjudicator's decisions, not model calls; reuse set 1"):
        rs.plan_rerun(p, "main", "sample:development", s2["settings"], new_set=True, reuse_from=2)
    # a run that re-derives the active set keeps every adjudicated decision
    adjudicated = _decisions(p, "main#2")[ids[0]]
    run_protocol(p, "sample:development", "main")
    assert _decisions(p, "main#2")[ids[0]] == adjudicated


def test_second_screen_and_sample_are_told(screened):
    from fastapi.testclient import TestClient

    from wider.screen import review
    from wider.ui.app import create_app

    p, st = screened, screened.store
    dec = _decisions(p)
    ids = sorted(dec)
    st.execute("INSERT INTO screen_result_sets(protocol, result_set, key, label, source, created_at, active, "
               "source_protocol, source_set) VALUES ('second', 1, 'second', '', 'screened', 'x', 1, 'main', 1)")
    have = {r["record_id"] for r in st.query("SELECT record_id FROM sample_rows WHERE sample_id=1")}
    for i, rid in enumerate(ids[:2]):   # two adjudicated records belong to the validation sample
        if rid not in have:
            st.execute("INSERT INTO sample_rows(sample_id, record_id, queue_pos) VALUES (1, ?, ?)", (rid, 900 + i))
    before = review.reviewed_with_llm_reads(p, 1)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"]), f"r{ids[1]}": dec[ids[1]]["decision"],
                                f"r{ids[2]}": _flip(dec[ids[2]]["decision"])})
    res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Ana")
    assert res["out_of_step"] == ["second"]
    assert any(line.startswith("Second screen second was built on set 1 of main, which is no longer active")
               for line in res["lines"])
    # both adjudicated records of the sample count, the changed and the confirmed one (D15)
    n = review.reviewed_with_llm_reads(p, 1)
    assert n == before + 2
    name = st.scalar("SELECT name FROM samples WHERE sample_id=1")
    body = TestClient(create_app(p)).get("/api/samples").json()["samples"][name]
    assert body["reviewed_with_llm_reads"] == n and body["reviewed_line"] == review.reviewed_line(n)
    # undone, it no longer counts
    journal.undo(p, res["change_id"], author="Ana")
    assert review.reviewed_with_llm_reads(p, 1) == before


def test_history_undo_and_redo(screened):
    p, st = screened, screened.store
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    s1 = st.query("SELECT * FROM screen_result_sets ORDER BY key")
    res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
    h = journal.changes(p, kind="adjudication_import")
    assert len(h) == 1 and h[0]["author"] == "Bea" and h[0]["undoable"]
    assert h[0]["summary"] == (f"adjudicator decisions imported into main set 2 (file sha256 {res['sha256'][:12]}): "
                               "1 changed, 0 confirmed")
    assert {"kind": "adjudication_import", "label": "adjudicator decisions imported"} in journal.kinds()
    two = (st.query("SELECT * FROM screen_result_sets ORDER BY key"), _decisions(p, "main#2"))
    u = journal.undo(p, h[0]["change_id"], author="Bea")
    assert st.query("SELECT * FROM screen_result_sets ORDER BY key") == s1 and not _decisions(p, "main#2")
    journal.undo(p, u["change_id"], author="Bea")   # redo
    assert (st.query("SELECT * FROM screen_result_sets ORDER BY key"), _decisions(p, "main#2")) == two
    u2 = journal.undo_last(p, "Bea")   # undoes the redo
    assert u2 and not _decisions(p, "main#2")
    assert rs.next_number(p, "main") == 3


def test_undo_refused_once_used(screened):
    p, st = screened, screened.store
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})

    def fresh():
        res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
        return res["change_id"], res["key"], res["result_set"]

    def refused(cid, words):
        was = _state(p)
        with pytest.raises(ValueError, match=words):
            journal.undo(p, cid, author="Bea")
        assert _state(p) == was

    # a review decision in the new set
    from wider.screen import review
    cid, key, n = fresh()
    review.decide(p, "main", n, ids[1], _flip(dec[ids[1]]["decision"]), by="Ana")
    refused(cid, "screen_decisions changed since; undo the later change first")
    # a quality-control sample drawn from it
    cid, key, n = fresh()
    st.execute("INSERT INTO review_qc(draw_id, protocol, set_key, record_id, seed, n, pool_n, pool_sha256, by, at) "
               "VALUES (99, 'main', ?, ?, 1, 1, 1, 'x', 'Ana', '2026-10-30')", (key, ids[0]))
    refused(cid, f"a quality-control sample was drawn from set {n}")
    # recall seen on a validation sample
    cid, key, n = fresh()
    st.execute("INSERT INTO validation_looks(sample_id, result_set_key, first_at, last_at) VALUES (1, ?, 'x', 'x')",
               (key,))
    refused(cid, f"recall of set {n} was seen on a validation sample; that look stays on record")
    # a second screen built on it
    cid, key, n = fresh()
    st.execute("INSERT INTO screen_result_sets(protocol, result_set, key, label, source, created_at, active, "
               "source_protocol, source_set) VALUES ('second', 1, 'second', '', 'screened', 'x', 1, 'main', ?)", (n,))
    refused(cid, rf"a second screen was built on set {n} \(second\)")


def test_earlier_switch_names_the_adjudication(screened):
    p = screened
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
    rs.use(p, "main", 1, author="Ana")
    switch = journal.changes(p)[0]
    res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
    assert res["result_set"] == 3
    with pytest.raises(ValueError) as e:
        journal.undo_last(p, "Ana")
    msg = str(e.value)
    assert "screen_result_sets changed since; undo the later change first" in msg, msg
    assert "adjudicator decisions imported into main set 3" in msg and switch["change_id"]
    assert "imported decisions stay on record" not in msg


def test_failed_import_leaves_nothing(screened, monkeypatch):
    p, st = screened, screened.store
    from wider.screen import adjudication as adj

    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    was = _state(p)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(adj, "set_decision", boom)
    with pytest.raises(RuntimeError):
        import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana")
    assert _state(p) == was
    assert not st.query("SELECT 1 FROM history WHERE kind='adjudication_import'")
    assert rs.active_set(p, "main")["result_set"] == 1


def test_set_added_meanwhile_refuses(screened, monkeypatch):
    """Risk-check R1: a set added after the count and before the write is never filled with the import's decisions."""
    p, st = screened, screened.store
    dec = _decisions(p)
    ids = sorted(dec)
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    real, fired = journal.recording, []

    def rec(*a, **k):
        if not fired:
            fired.append(1)
            rs.new_set(p, "main", "screening", label="other process")
        return real(*a, **k)
    monkeypatch.setattr(journal, "recording", rec)
    with pytest.raises(Refusal, match="another set of main was added while the file was read; nothing was imported"):
        import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
    assert st.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE source='adjudication'") == 0
    assert _decisions(p) == dec and rs.active_set(p, "main")["result_set"] == 1


def test_open_question_left_in_the_base_set_is_said(screened):
    """Risk-check R2: an open question of a record the file does not decide is named, with where its answer goes."""
    from wider.core.questions import raise_question
    from wider.screen.decisions import set_decision

    p = screened
    dec = _decisions(p)
    ids = sorted(dec)
    rid = ids[3]
    set_decision(p, "main", rid, "maybe", votes=["include", "exclude", "maybe"], cell=dec[rid]["cell"],
                 decided_by="split")
    raise_question(p, "screen_split", f"main:{rid}", "t", "three reads disagree",
                   context={"protocol": "main", "votes": ["include", "exclude", "maybe"], "cell": dec[rid]["cell"],
                            "vote_calls": []}, record_id=rid, run_ref="main")
    rows = _fill(_export(p), **{f"r{ids[0]}": _flip(dec[ids[0]]["decision"])})
    res = import_adjudication(p, "main", _csv(rows), "adj.csv", adjudicator="Ana", author="Bea")
    assert res["open_questions_left"] == [rid]
    assert f"Record {rid} of set 1 still has an open question the file does not decide; an answer changes set 1 " \
           "only, so set 2 keeps maybe." in "\n".join(res["lines"])
