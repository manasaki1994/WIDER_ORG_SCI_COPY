"""WP13 record work: notes and tags (store, blind filtering, search, dedup merge), drawer routes, two-reviewer
comparison, `wider notes`, and the static drawer."""
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_dedup import _project_with_imports
from wider.cli import app as cli_app
from wider.core import annotations as ann
from wider.dedup.engine import DedupRerunError, decide, queue_rows, run
from wider.sample.labels import label
from wider.ui.app import create_app

runner = CliRunner()


def _deduped(tmp_path):
    p = _project_with_imports(tmp_path)
    run(p)
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM records ORDER BY record_id")]
    return p, ids


def _sample(p, ids, name="dev"):
    cur = p.store.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES(?,?,?,?,?)", (name, "purposive", "{}", len(ids), "t"))
    sid = cur.lastrowid
    for i, rid in enumerate(ids):
        p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos,band,rank) VALUES(?,?,?,?,?)", (sid, rid, i, "S1", i + 1))
    return sid


def test_notes_tags_blind_and_search(tmp_path):
    p, ids = _deduped(tmp_path)
    r1, r2 = ids[0], ids[1]
    n1 = ann.add_note(p, r1, "ana", "Looks like the adjacent participation literature")
    ann.add_note(p, r1, "ben", "Include: clear upward voice")
    assert ann.add_tag(p, r1, "ana", "  Edge Case ") == "edge-case"
    ann.add_tag(p, r1, "ana", "edge-case")            # idempotent per author
    ann.add_tag(p, r1, "ben", "edge-case")
    ann.add_tag(p, r2, "ben", "check-doi")
    with pytest.raises(ValueError):
        ann.add_tag(p, r1, "ana", "no/slashes")
    # letters of any script are letters
    assert ann.add_tag(p, r2, "ana", "Ünïcødé 漢字") == "ünïcødé-漢字"
    for bad in ["_lead", "a" * 41, "🙂"]:
        with pytest.raises(ValueError):
            ann.add_tag(p, r2, "ana", bad)
    ann.remove_tag(p, r2, "ünïcødé-漢字", author="ana")
    with pytest.raises(ValueError):
        ann.add_note(p, r1, "ana", "   ")
    with pytest.raises(ValueError):
        ann.add_note(p, r1, "", "who wrote this")
    with pytest.raises(KeyError):
        ann.add_note(p, 99999, "ana", "no such record")
    # blind: only the reader's own notes and tags
    blind = ann.for_record(p, r1, author="ana", blind=True)
    assert [n["author"] for n in blind["notes"]] == ["ana"] and {t["author"] for t in blind["tags"]} == {"ana"}
    open_ = ann.for_record(p, r1)
    assert len(open_["notes"]) == 2 and len(open_["tags"]) == 2
    summ = ann.summary(p, author="ana", blind=True)
    assert set(summ) == {r1} and summ[r1]["n_notes"] == 1 and "participation" in summ[r1]["text"]
    assert ann.summary(p)[r1]["tags"] == ["edge-case"]
    # search by words, tag and author
    assert [r["record_id"] for r in ann.search(p, q="upward voice")] == [r1]
    assert [r["record_id"] for r in ann.search(p, tag="check-doi")] == [r2]
    assert [r["record_id"] for r in ann.search(p, author="ana")] == [r1]
    assert ann.all_tags(p) == [{"tag": "check-doi", "n": 1}, {"tag": "edge-case", "n": 1}]
    # only the author removes their own note
    assert not ann.remove_note(p, n1, author="ben") and ann.remove_note(p, n1, author="ana")
    assert ann.remove_tag(p, r1, "edge-case", author="ana") == 1 and len(ann.for_record(p, r1)["tags"]) == 1


def test_dedup_merge_moves_notes_and_tags_and_refuses_orphans(tmp_path):
    p, _ = _deduped(tmp_path)
    q = queue_rows(p)[0]
    rec_of = lambda raw: p.store.one("SELECT m.record_id FROM records m JOIN dedup_clusters c ON c.cluster_id=m.cluster_id WHERE c.raw_id=?", (raw,))["record_id"]  # noqa: E731
    a_rec, b_rec = rec_of(q["raw_a"]), rec_of(q["raw_b"])
    ann.add_note(p, b_rec, "ana", "note on the record that will merge away")
    ann.add_tag(p, a_rec, "ana", "same")
    ann.add_tag(p, b_rec, "ana", "same")      # same tag, same author on both: kept once
    ann.add_tag(p, b_rec, "ben", "other")
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    run(p)
    survivor = rec_of(q["raw_a"])
    assert p.store.scalar("SELECT COUNT(*) FROM records WHERE record_id=?", (b_rec,)) == 0
    a = ann.for_record(p, survivor)
    assert [n["text"] for n in a["notes"]] == ["note on the record that will merge away"]
    assert sorted((t["tag"], t["author"]) for t in a["tags"]) == [("other", "ben"), ("same", "ana")]
    assert p.store.scalar("SELECT COUNT(*) FROM record_tags WHERE record_id=?", (b_rec,)) == 0
    # a record that disappears entirely while it carries a note: the re-run is refused, nothing changes
    one = p.store.one("SELECT record_id, cluster_id FROM records WHERE title='Unique paper one'")
    ann.add_note(p, one["record_id"], "ana", "keep me")
    raw_ids = [r["raw_id"] for r in p.store.query("SELECT raw_id FROM dedup_clusters WHERE cluster_id=?", (one["cluster_id"],))]
    p.store.execute("PRAGMA foreign_keys=OFF")
    for rid in raw_ids:
        p.store.execute("DELETE FROM dedup_clusters WHERE raw_id=?", (rid,))
        p.store.execute("DELETE FROM raw_records WHERE raw_id=?", (rid,))
    p.store.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(DedupRerunError, match=r"\(1 notes\)"):
        run(p)
    assert ann.for_record(p, one["record_id"])["notes"][0]["text"] == "keep me"


def test_record_routes_blind_and_compare(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:3])
    for rid, (x, y) in zip(ids[:3], [("include", "exclude"), ("maybe", "maybe"), ("exclude", "include")], strict=True):
        label(p, "dev", rid, "ana", x, reason="R1" if x == "exclude" else "", note="n-" + x)
        label(p, "dev", rid, "ben", y)
    c = TestClient(create_app(p))
    r = c.get(f"/api/records/{ids[0]}").json()
    assert r["record"]["title"] and not ({"rank", "score", "band", "cell", "sample"} & set(r["record"]))
    assert c.get("/api/records/99999").status_code == 404
    assert c.post(f"/api/records/{ids[0]}/notes", json={"author": "ana", "text": "mine"}).status_code == 200
    assert c.post(f"/api/records/{ids[0]}/notes", json={"author": "ben", "text": "his"}).status_code == 200
    assert c.post(f"/api/records/{ids[0]}/notes", json={"author": "ana", "text": " "}).status_code == 400
    assert c.post(f"/api/records/{ids[0]}/tags", json={"author": "ana", "tag": "Hard Case"}).json()["tag"] == "hard-case"
    assert c.post(f"/api/records/{ids[0]}/tags", json={"author": "ana", "tag": "bad/tag"}).status_code == 400
    assert c.post("/api/records/99999/tags", json={"author": "ana", "tag": "x"}).status_code == 404
    blind = c.get(f"/api/records/{ids[0]}?author=ana&blind=true").json()
    assert [n["text"] for n in blind["notes"]] == ["mine"]
    full = c.get(f"/api/records/{ids[0]}").json()
    assert len(full["notes"]) == 2
    note_ben = [n for n in full["notes"] if n["author"] == "ben"][0]["note_id"]
    assert c.delete(f"/api/records/{ids[0]}/notes/{note_ben}?author=ana").status_code == 404
    assert c.delete(f"/api/records/{ids[0]}/notes/{note_ben}?author=ben").status_code == 200
    assert c.delete(f"/api/records/{ids[0]}/tags/hard-case?author=ben").status_code == 404
    summ = c.get("/api/annotations/summary?author=ana&blind=true").json()
    assert summ[str(ids[0])]["tags"] == ["hard-case"]
    found = c.get("/api/annotations?tag=hard-case").json()
    assert [x["record_id"] for x in found["rows"]] == [ids[0]] and found["tags"][0]["tag"] == "hard-case"
    assert c.delete(f"/api/records/{ids[0]}/tags/hard-case?author=ana").status_code == 200
    # adjudication view: both labels side by side, disagreements only unless all=true
    cmp = c.get("/api/labels/compare?sample=dev&a=ana&b=ben").json()
    assert [x["record_id"] for x in cmp["rows"]] == sorted([ids[0], ids[2]])
    row = [x for x in cmp["rows"] if x["record_id"] == ids[2]][0]
    assert {k: row["a"][k] for k in ("decision", "reason", "note")} == {"decision": "exclude", "reason": "R1", "note": "n-exclude"}
    assert row["b"]["decision"] == "include"   # each side also says how it was made (reviewer-roles-and-blinding D9)
    assert len(c.get("/api/labels/compare?sample=dev&a=ana&b=ben&all=true").json()["rows"]) == 3
    c.post("/api/labels/adjudicate", json={"sample": "dev", "record_id": ids[2], "decision": "include", "by": "cara"})
    row = [x for x in c.get("/api/labels/compare?sample=dev&a=ana&b=ben").json()["rows"] if x["record_id"] == ids[2]][0]
    assert row["adjudicated"]["decision"] == "include" and row["adjudicated"]["decided_by"] == "cara"
    assert c.get("/api/labels/compare?sample=nope&a=ana&b=ben").status_code == 404
    # the dedup queue carries both abstracts for the side-by-side drawer
    q = c.get("/api/dedup").json()["queue"]
    assert q and {"abstract_a", "abstract_b"} <= set(q[0])


def test_notes_cli(tmp_path, monkeypatch):
    p, ids = _deduped(tmp_path)
    root = str(p.root)
    p.close()
    monkeypatch.setenv("WIDER_REVIEWER", "ana")
    monkeypatch.setenv("COLUMNS", "200")
    r = runner.invoke(cli_app, ["notes", "add", str(ids[0]), "A borderline case", "--dir", root])
    assert r.exit_code == 0 and "note 1 added" in r.output
    r = runner.invoke(cli_app, ["notes", "tag", str(ids[0]), "Borderline", "recheck", "--dir", root])
    assert r.exit_code == 0 and "borderline, recheck" in r.output
    # two spellings of one tag are echoed once ("Re check" and "re-check" are both "re-check")
    r = runner.invoke(cli_app, ["notes", "tag", str(ids[0]), "Re check", "re-check", "--dir", root])
    assert r.exit_code == 0 and r.output.count("re-check") == 1
    r = runner.invoke(cli_app, ["notes", "list", "--tag", "recheck", "--dir", root])
    assert r.exit_code == 0 and "A borderline case" in r.output and "recheck (1)" in r.output
    assert "no notes or tags match" in runner.invoke(cli_app, ["notes", "list", "--search", "nothing-like-this", "--dir", root]).output
    assert runner.invoke(cli_app, ["notes", "add", "99999", "x", "--dir", root]).exit_code == 1
    assert runner.invoke(cli_app, ["notes", "untag", str(ids[0]), "recheck", "--dir", root]).exit_code == 0
    assert runner.invoke(cli_app, ["notes", "remove", "1", "--author", "ben", "--dir", root]).exit_code == 1
    assert runner.invoke(cli_app, ["notes", "remove", "1", "--dir", root]).exit_code == 0


def test_removal_needs_the_author_and_blind_covers_every_response(tmp_path):
    p, ids = _deduped(tmp_path)
    r1, r2 = ids[0], ids[1]
    c = TestClient(create_app(p))
    for who in ("ana", "ben"):
        c.post(f"/api/records/{r1}/tags", json={"author": who, "tag": "recheck"})
    n_ben = c.post(f"/api/records/{r1}/notes", json={"author": "ben", "text": "his view of the topic"}).json()["notes"][0]["note_id"]
    c.post(f"/api/records/{r2}/notes", json={"author": "ben", "text": "another view of the topic"})
    # removal: a name is required, and only one's own goes
    assert c.delete(f"/api/records/{r1}/tags/recheck").status_code == 400
    assert c.delete(f"/api/records/{r1}/notes/{n_ben}").status_code == 400
    assert c.delete(f"/api/records/{r1}/tags/recheck?author=ben").status_code == 200
    assert [t["author"] for t in ann.for_record(p, r1)["tags"]] == ["ana"]
    assert c.delete(f"/api/records/{r1}/notes/{n_ben}?author=ana").status_code == 404
    assert len(ann.for_record(p, r1)["notes"]) == 1
    with pytest.raises(ValueError):
        ann.remove_tag(p, r1, "recheck", author=" ")
    # blind: the note POST response, the search and the tag counts show only the reader's own
    d = c.post(f"/api/records/{r1}/notes?blind=true", json={"author": "ana", "text": "my view of the topic"}).json()
    assert [n["author"] for n in d["notes"]] == ["ana"] and {t["author"] for t in d["tags"]} == {"ana"}
    c.post(f"/api/records/{r2}/tags", json={"author": "ben", "tag": "recheck"})
    s = c.get("/api/annotations?q=view&author=ana&blind=true").json()
    assert [x["record_id"] for x in s["rows"]] == [r1] and s["tags"] == [{"tag": "recheck", "n": 1}]
    s = c.get("/api/annotations?q=view").json()
    assert len(s["rows"]) == 2 and s["tags"] == [{"tag": "recheck", "n": 2}]
    for url in ("/api/annotations?blind=true", "/api/annotations/summary?blind=true", f"/api/records/{r1}?blind=true",
                f"/api/records/{r1}/notes?blind=true"):
        r = c.post(url, json={"author": "", "text": "x"}) if url.endswith("notes?blind=true") else c.get(url)
        assert r.status_code == 400, url


def test_remove_cli_without_any_name_and_adjudication_errors(tmp_path, monkeypatch):
    from wider.sample.labels import adjudicate

    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")   # record ids[0]: a disagreement; ids[1]: both agree
    label(p, "dev", ids[0], "bo", "exclude")
    label(p, "dev", ids[1], "ana", "include")
    label(p, "dev", ids[1], "bo", "include")
    nid = ann.add_note(p, ids[0], "ana", "a thought")
    root = str(p.root)
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    monkeypatch.setenv("USER", "")
    assert runner.invoke(cli_app, ["notes", "remove", str(nid), "--dir", root]).exit_code == 1
    assert runner.invoke(cli_app, ["notes", "untag", str(ids[0]), "x", "--dir", root]).exit_code == 1
    assert len(ann.for_record(p, ids[0])["notes"]) == 1
    # adjudication: always attributed; a bad decision or an unknown sample is an error, not a crash
    r = runner.invoke(cli_app, ["label", "adjudicate", "dev", str(ids[0]), "include", "--dir", root])
    assert r.exit_code == 1 and "name" in r.output
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 0
    monkeypatch.setenv("WIDER_REVIEWER", "dana")
    r = runner.invoke(cli_app, ["label", "adjudicate", "dev", str(ids[0]), "include", "--dir", root])
    assert r.exit_code == 0, r.output
    assert p.store.one("SELECT decided_by FROM adjudications")["decided_by"] == "dana"
    r = runner.invoke(cli_app, ["label", "adjudicate", "dev", str(ids[0]), "perhaps", "--dir", root])
    assert r.exit_code == 1 and "decision must be one of" in r.output
    r = runner.invoke(cli_app, ["label", "adjudicate", "nope", str(ids[0]), "include", "--dir", root])
    assert r.exit_code == 1 and "no sample 'nope'" in r.output
    # a record outside the sample, or one whose labels agree, has nothing to adjudicate: refused, nothing stored
    r = runner.invoke(cli_app, ["label", "adjudicate", "dev", "99999", "include", "--dir", root])
    assert r.exit_code == 1 and "record 99999 is not in sample dev" in " ".join(r.output.split()), r.output
    r = runner.invoke(cli_app, ["label", "adjudicate", "dev", str(ids[1]), "include", "--dir", root])
    assert r.exit_code == 1 and "has no disagreement to adjudicate (all 2 labels say include)" in " ".join(r.output.split())
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 1
    with pytest.raises(ValueError, match="name"):
        adjudicate(p, "dev", ids[0], "include", by=" ")
    c = TestClient(create_app(p))
    body = {"sample": "dev", "record_id": ids[0], "decision": "include", "by": "dana"}
    assert c.post("/api/labels/adjudicate", json={**body, "by": ""}).status_code == 400
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 1   # the refused posts stored nothing
    assert c.post("/api/labels/adjudicate", json={**body, "decision": "perhaps"}).status_code == 400
    r = c.post("/api/labels/adjudicate", json={**body, "sample": "nope"})
    assert r.status_code == 404 and r.json()["detail"] == "no sample 'nope'"
    r = c.post("/api/labels/adjudicate", json={**body, "record_id": 88888})
    assert r.status_code == 400 and r.json()["detail"] == "record 88888 is not in sample dev; nothing was adjudicated"
    r = c.post("/api/labels/adjudicate", json={**body, "record_id": ids[1]})
    assert r.status_code == 400 and "no disagreement to adjudicate" in r.json()["detail"]
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 1
    # lock and unlock of an unknown sample are errors too
    for cmd in ("lock", "unlock"):
        r = runner.invoke(cli_app, ["sample", cmd, "nope", "--dir", root])
        assert r.exit_code == 1 and "no sample 'nope'" in r.output, cmd
    assert c.post("/api/samples/nope/lock", headers={"X-Wider-Author": "ana"}).status_code == 404


def test_static_drawer(tmp_path):
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    html = c.get("/").text
    for needle in ['id="drawer"', 'id="dw-decide"', 'id="dw-notes"', 'id="dw-close"', "/static/drawer.js", "/static/records.js",
                   'id="lb-table"', 'id="lb-search"', 'id="dd-list"', 'id="dd-pair"', 'id="in-search"', 'id="ev-filter"', 'id="an-table"']:
        assert needle in html, needle
    assert html.index("/static/drawer.js") < html.index("/static/records.js") < html.index("/static/dedup.js") < html.index("/static/shell.js")
    # the Notes and tags search is its own page (#notes, a utility link), not a card on Home
    notes = html[html.index('id="page-notes"'):html.index("</section>", html.index('id="page-notes"'))]
    assert 'id="an-table"' in notes and 'id="an-search"' in notes and "<h1>Notes and tags</h1>" in notes
    home = html[html.index('id="page-home"'):html.index("</section>", html.index('id="page-home"'))]
    assert 'id="an-table"' not in home and 'href="#notes" data-key="notes"' in html
    app_js = c.get("/static/app.js").text
    assert 'notes: "page-notes"' in app_js and "notes: window.loadNotesSearch" in app_js
    js = c.get("/static/drawer.js").text
    for key in ['k === "j"', 'k === "k"', 'k === "n"', 'k === "t"', '"Escape"', "blind=", "<mark>"]:
        assert key in js, key
    rec = c.get("/static/records.js").text
    assert "blind: true" in rec and "/api/labels/compare" in rec and "/api/dedup/decide" not in rec
    assert "/api/dedup/decide" in c.get("/static/dedup.js").text   # the pair decisions live on the Deduplication page
    assert "showLabelCard" not in c.get("/static/app.js").text


def test_queue_marks_locked_samples(tmp_path):
    from wider.sample.labels import lock, queue

    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")
    label(p, "dev", ids[1], "ana", "exclude")
    assert {r["locked"] for r in queue(p, "ana")} == {False}
    lock(p, "dev")
    rows = queue(p, "ana")
    assert {r["locked"] for r in rows} == {True} and len(rows) == 2   # the key stays for the page; nothing dropped
    assert not ({"band", "pool", "rank", "sample"} & set(rows[0]))   # still blind
    # a lock keeps labels open: a change needs a reason (label-completion-and-sealing D2)
    with pytest.raises(ValueError, match="needs a reason for the correction"):
        label(p, "dev", ids[0], "ana", "maybe")



def test_blind_queue_mixing_locked_and_open_samples_leaves_the_locked_records_out(tmp_path):
    """A merged blind queue once showed a locked sample's records with the read-only chip next to the open sample's,
    which tells the reviewer how each record was drawn (eighth round, spec-checker)."""
    from wider.sample.labels import lock, queue

    p, ids = _deduped(tmp_path)
    dev = _sample(p, ids[:2], "dev")
    probe = _sample(p, ids[2:4], "probe")
    lock(p, "dev")
    from wider.sample.labels import resolve_item

    rows = queue(p, "ana")
    assert {resolve_item(p, r["item"])["sample_id"] for r in rows} == {probe}
    assert {r["record_id"] for r in rows} == set(ids[2:4]) and not any("locked" in r for r in rows)   # risk-checker R2
    # the page posts labels with the opaque item each row carries (D6)
    c = TestClient(create_app(p))
    q = c.get("/api/labels/queue", params={"reviewer": "ana"}).json()
    assert {resolve_item(p, r["item"])["sample_id"] for r in q} == {probe} and not any("locked" in r for r in q)
    assert not any("sample_id" in r for r in q)
    # an unblinded queue (adjudication) still lists both, the locked one read-only
    both = queue(p, "ana", blind=False)
    assert {(r["sample_id"], r["locked"]) for r in both} == {(dev, True), (probe, False)}


def test_blind_queue_rows_carry_items_not_sample_ids(tmp_path):
    """manual-review D6: a blind queue row carries an opaque item and no sample id; the same record keeps its item,
    each reviewer has their own, and an unblinded row keeps its sample id and no item."""
    from wider.sample.labels import queue, resolve_item

    p, ids = _deduped(tmp_path)
    sid = _sample(p, ids[:3])
    c = TestClient(create_app(p))
    rows = c.get("/api/labels/queue", params={"reviewer": "ana"}).json()
    assert len(rows) == 3 and all("sample_id" not in r and r["item"] for r in rows)
    assert not ({"band", "pool", "rank", "sample"} & set(rows[0]))
    it = resolve_item(p, rows[0]["item"])
    assert (it["sample_id"], it["record_id"], it["mode"], it["reviewer"]) == (sid, rows[0]["record_id"], "blind", "ana")
    again = c.get("/api/labels/queue", params={"reviewer": "ana"}).json()
    assert [r["item"] for r in again] == [r["item"] for r in rows]
    ben = c.get("/api/labels/queue", params={"reviewer": "ben"}).json()
    assert not ({r["item"] for r in ben} & {r["item"] for r in rows})
    plain = queue(p, "ana", blind=False)
    assert {r["sample_id"] for r in plain} == {sid} and not any("item" in r for r in plain)
    # a save by item labels the record under the item's own reviewer; another name is refused
    r = c.post("/api/labels", json={"item": rows[0]["item"], "reviewer": "ana", "decision": "include"})
    assert r.status_code == 200
    assert p.store.scalar("SELECT decision FROM labels WHERE sample_id=? AND record_id=? AND reviewer_id='ana'",
                          (sid, rows[0]["record_id"])) == "include"
    r = c.post("/api/labels", json={"item": rows[1]["item"], "reviewer": "ben", "decision": "include"})
    assert r.status_code == 400 and "another reviewer" in r.json()["detail"]


def test_blind_label_stays_in_the_drawer_history(tmp_path):
    """manual-review D6: a blind save keeps its record id in the journal, so the drawer's history of the record lists
    it, and its summary names no sample."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2], "secret-sample-name")
    c = TestClient(create_app(p))
    row = c.get("/api/labels/queue", params={"reviewer": "ana"}).json()[0]
    cid = c.post("/api/labels", json={"item": row["item"], "reviewer": "ana", "decision": "exclude"},
                 headers={"X-Wider-Author": "ana"}).json()["change_id"]
    h = dict(p.store.one("SELECT * FROM history WHERE change_id=?", (cid,)))
    assert h["record_id"] == row["record_id"]
    assert h["summary"] == f"ana labelled record {row['record_id']}" and "secret-sample-name" not in h["summary"]
    hist = c.get(f"/api/history?record_id={row['record_id']}&blind=true&author=ana").json()
    items = hist["changes"] if isinstance(hist, dict) else hist
    assert cid in [x["change_id"] for x in items]

def test_page_changes_are_signed_with_the_page_name_never_the_login_name(tmp_path, monkeypatch):
    """A change made from the page carries the reviewer name the page sent; with no name it is unsigned, never the
    server's login name ($USER), which would leak the account name into a shared project and break undo-last."""
    monkeypatch.setenv("USER", "os-login-name")
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    p, _ = _deduped(tmp_path)
    q = queue_rows(p)[0]
    c = TestClient(create_app(p))
    r = c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "uncertain"})
    assert r.status_code == 200
    r = c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "duplicate"},
               headers={"X-Wider-Author": "tester"})
    assert r.status_code == 200
    authors = [x["author"] for x in p.store.query("SELECT author FROM history ORDER BY change_id")]
    assert authors == ["", "tester"]
    rows = lambda sql: json.dumps([dict(r) for r in p.store.query(sql)], default=str)   # noqa: E731
    assert '"decided_by": "tester"' in rows("SELECT decided_by FROM dedup_pairs")   # the rows are really read
    assert "os-login-name" not in rows("SELECT * FROM history")      # every column: author, summary, before, after
    assert "os-login-name" not in rows("SELECT * FROM dedup_pairs")
    u = c.post("/api/history/undo-last?author=tester")
    assert u.status_code == 200 and "duplicate" in u.json()["summary"]
    # the CLI still falls back to $WIDER_REVIEWER, then $USER (one person at a terminal)
    from wider.core.journal import current_author
    assert current_author() == "os-login-name"


def test_jobs_keep_the_page_name(tmp_path):
    """A background job started from the page journals under the page's name, not the server's login name."""
    import time

    from wider.core.journal import AUTHOR, current_author
    from wider.ui.jobs import JobRunner

    p, _ = _deduped(tmp_path)
    seen = {}
    token = AUTHOR.set("tester")
    try:
        jm = JobRunner()
        jid = jm.start(p.root, "t", "dedup", "check", {}, lambda project, progress: seen.setdefault("who", current_author()))
    finally:
        AUTHOR.reset(token)
    for _ in range(100):
        if "who" in seen:
            break
        time.sleep(0.05)
    assert seen.get("who") == "tester", jid
    # no name on the page: the job's changes stay unsigned, never the server's login name
    import os
    seen.clear()
    token = AUTHOR.set("")
    try:
        jm.start(p.root, "t", "dedup", "check", {}, lambda project, progress: seen.setdefault("who", current_author()))
    finally:
        AUTHOR.reset(token)
    for _ in range(100):
        if "who" in seen:
            break
        time.sleep(0.05)
    assert seen.get("who") == "" and os.environ.get("USER", "x") != seen.get("who")


def test_a_job_started_from_the_page_signs_its_changes_with_the_page_name(tmp_path, monkeypatch):
    """Through the route: the page's header reaches the background job, and the change the job journals carries it."""
    import time

    import wider.construct.propose as propose
    from wider.core import journal

    monkeypatch.setenv("USER", "os-login-name")
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    p, _ = _deduped(tmp_path)

    async def fake_propose(project, kind, provider_name=None, **kw):   # a job step that changes a setting
        project.config.title = f"Set by a job {len(journal.changes(project))}"
        project.save()
        return {"kind": kind, "version": 1, "origin": "llm"}

    monkeypatch.setattr(propose, "propose_async", fake_propose)
    c = TestClient(create_app(p))
    for header, who in [({"X-Wider-Author": "tester"}, "tester"), ({}, "")]:
        r = c.post("/api/jobs/propose/cells", headers=header)
        assert r.status_code == 200, r.text
        job = r.json()["job_id"]
        for _ in range(200):
            if c.get(f"/api/jobs/{job}").json()["status"] in ("done", "failed"):
                break
            time.sleep(0.05)
        assert c.get(f"/api/jobs/{job}").json()["status"] == "done"
        top = journal.changes(p)[0]
        assert top["kind"] == "config" and "title" in top["summary"] and top["author"] == who


def test_a_long_name_sees_its_own_notes_in_blind_mode(tmp_path):
    # names are cut to 80 characters when saved; the blind filter cuts the same way, so the reviewer still sees them
    p, ids = _deduped(tmp_path)
    long = "<i>r</i>" + "x" * 100 + "END"
    ann.add_note(p, ids[0], long, "mine")
    ann.add_tag(p, ids[0], long, "edge-case")
    ann.add_note(p, ids[0], "ben", "not mine")
    mine = ann.for_record(p, ids[0], author=long, blind=True)
    assert [n["text"] for n in mine["notes"]] == ["mine"] and [t["tag"] for t in mine["tags"]] == ["edge-case"]
    assert mine["notes"][0]["author"] == mine["tags"][0]["author"] == long[:80]   # both signed with the cut name
    c = TestClient(create_app(p))
    r = c.get(f"/api/records/{ids[0]}", params={"author": long, "blind": "true"}).json()
    assert [n["text"] for n in r["notes"]] == ["mine"]


def test_static_dedup_decision_asks_for_the_name(tmp_path):
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    rec, dd = c.get("/static/records.js").text, c.get("/static/dedup.js").text
    # the name is the one WIDER works as (ui-redesign D33), never a browser prompt(), which some browsers block with a
    # raw error
    for js in (rec, dd):
        assert 'by: Drawer.author() || ""' not in js and "reviewerName" not in js and "prompt(" not in js
    # inside the dedup decision: no name -> throw needName with WiderName.NEED before /api/dedup/decide is called; the
    # drawer then shows WiderName.needHtml (the line pointing at Change, with a Set your name button)
    fn = dd[dd.index("async function decide(r, v, label)"):]
    fn = fn[:fn.index("\n  }\n")]
    assert "const by = me();" in fn and "dw-author" not in dd   # the name WIDER works as (test_dedup_page_node)
    guard = fn.index("if (!by)")
    branch = fn[guard:fn.index("\n", guard)]
    assert "throw Object.assign(new Error(WiderName.NEED), { needName: true })" in branch
    assert fn.index("const by = ") < guard < fn.index('api("/api/dedup/decide"')
    drawer = c.get("/static/drawer.js").text
    assert "catch (e)" in drawer and "e.needName ?" in drawer and drawer.count("WiderName.needHtml(") >= 2
    assert "window.reviewerName" not in c.get("/static/history.js").text


def test_label_export_marks_a_locked_sample_read_only(tmp_path):
    """manual-review D6: `wider label export` writes every row with a read_only column (yes for a locked sample);
    an import of that export into an open sample saves the same decisions as the file without the column."""
    import csv

    from wider.sample.labels import export_csv, import_csv, lock

    p, ids = _deduped(tmp_path)
    _sample(p, ids[:3], "dev")
    _sample(p, ids[:3], "copy1")
    _sample(p, ids[:3], "copy2")
    label(p, "dev", ids[0], "ana", "include")
    lock(p, "dev")
    out = tmp_path / "dev.csv"
    r = runner.invoke(cli_app, ["label", "export", "dev", str(out), "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    rows = list(csv.DictReader(open(out, encoding="utf-8")))
    assert len(rows) == 3 and {x["read_only"] for x in rows} == {"no"}   # always "no" now (label-completion D8)
    assert not ({"band", "pool", "rank", "sample"} & set(rows[0]))
    export_csv(p, "copy1", tmp_path / "open.csv")
    assert {x["read_only"] for x in csv.DictReader(open(tmp_path / "open.csv", encoding="utf-8"))} == {"no"}
    # fill decisions, then import with and without the column into two open samples
    for i, x in enumerate(rows):
        x["decision"] = ("include", "exclude", "maybe")[i]
    with open(tmp_path / "with.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(tmp_path / "without.csv", "w", newline="", encoding="utf-8") as f:
        cols = [c for c in rows[0] if c != "read_only"]
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    a = import_csv(p, "copy1", tmp_path / "with.csv", reviewer_id="ben")
    b = import_csv(p, "copy2", tmp_path / "without.csv", reviewer_id="ben")
    assert a == b
    got = lambda n: [tuple(x) for x in p.store.query(   # noqa: E731
        "SELECT l.record_id, l.decision FROM labels l JOIN samples s ON s.sample_id=l.sample_id WHERE s.name=? "
        "ORDER BY l.record_id", (n,))]
    assert got("copy1") == got("copy2") and len(got("copy1")) == 3
    # into the locked sample, new labels are additions; changing them again needs a reason (label-completion D2)
    assert import_csv(p, "dev", tmp_path / "with.csv", reviewer_id="ben")["imported"] == 3
    for x in rows:
        x["decision"] = "exclude"
    with open(tmp_path / "changed.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with pytest.raises(ValueError, match="2 rows change a saved label on the locked sample dev"):
        import_csv(p, "dev", tmp_path / "changed.csv", reviewer_id="ben", overwrite=True)


def test_label_after_lock_in_another_tab_names_no_sample(tmp_path):
    """manual-review D6: a blind item whose sample was locked after the queue was shown is refused with the closed
    sentence, naming no sample, and nothing is saved; an unblinded post still names the sample."""
    from wider.sample.labels import lock

    p, ids = _deduped(tmp_path)
    sid = _sample(p, ids[:2], "secret-sample-name")
    c = TestClient(create_app(p))
    row = c.get("/api/labels/queue", params={"reviewer": "ana"}).json()[0]
    r = c.post("/api/labels", json={"item": row["item"], "reviewer": "ana", "decision": "include"})
    assert r.status_code == 200
    lock(p, "secret-sample-name")
    # label-completion-and-sealing D2: a lock keeps labels open; a change of the saved label needs a reason, and the
    # blind refusal names no sample; nothing is saved
    before = p.store.scalar("SELECT COUNT(*) FROM history")
    r = c.post("/api/labels", json={"item": row["item"], "reviewer": "ana", "decision": "exclude"})
    assert r.status_code == 409 and r.json() == {
        "detail": "a change to a saved label here needs a reason for the correction", "correction_needed": True}
    assert "secret" not in r.text
    assert p.store.scalar("SELECT decision FROM labels") == "include"
    assert p.store.scalar("SELECT COUNT(*) FROM history") == before
    r = c.post("/api/labels", json={"sample_id": sid, "record_id": row["record_id"], "reviewer": "ana", "decision": "exclude"})
    assert r.status_code == 409 and r.json()["detail"] == (
        "sample 'secret-sample-name' is locked: a change to a saved label needs a reason for the correction")


def test_recheck_queue_only_for_the_signing_name(tmp_path):
    """manual-review D10: the page's re-check and second-reviewer queues are for the name it signs with (the author
    header); another name's re-check queue is refused and shows none of its labels; both queues stay blind; the
    command line still names the queue with --reviewer."""
    from tests.unit.test_validation_checks import _assessed, _blind
    p, _ = _assessed(tmp_path)   # R1 labelled the validation sample; its criteria changed, so R1 has a re-check queue
    c = TestClient(create_app(p))
    r = c.get("/api/labels/queue", params={"mode": "recheck", "reviewer": "R1"}, headers={"X-Wider-Author": "R2"})
    assert r.status_code == 400 and r.json() == {"detail": "this queue is for the name you sign with"}
    assert c.get("/api/labels/queue", params={"mode": "recheck", "reviewer": "R1"}).status_code == 400   # no name signs
    sec = c.get("/api/labels/queue", params={"mode": "second", "reviewer": "R2", "blind": "false"},
                headers={"X-Wider-Author": "R2"})
    assert sec.status_code == 200 and sec.json()["items"]
    _blind(sec.json()["items"])
    own = c.get("/api/labels/queue", params={"mode": "recheck", "reviewer": "R1"}, headers={"X-Wider-Author": "R1"})
    assert own.status_code == 200 and own.json()["items"]
    _blind(own.json()["items"])
    out = CliRunner().invoke(cli_app, ["label", "recheck", "--reviewer", "R1", "--json", "--dir", str(p.root)])
    assert out.exit_code == 0 and json.loads(out.output) == own.json()


def test_label_queue_get_registers_no_reviewer(tmp_path):
    """A GET writes no reviewer (inspection finding 8): the queue checks its mode first and the name is registered by
    the first saved label (label() calls add_reviewer), as before."""
    from wider.sample.labels import label, reviewers

    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2], "probe")
    c = TestClient(create_app(p))
    assert c.get("/api/labels/queue", params={"reviewer": "zoe", "mode": "bogus"}).status_code == 400
    assert c.get("/api/labels/queue", params={"reviewer": "zoe"}).status_code == 200
    assert "zoe" not in {r["reviewer_id"] for r in reviewers(p)}
    label(p, "probe", ids[0], "zoe", "include")
    assert "zoe" in {r["reviewer_id"] for r in reviewers(p)}
