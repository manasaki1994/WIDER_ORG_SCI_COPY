"""Locking and unlocking a sample (manual-review D5): both signed History changes; a lock undoable until a recall
result on the sample is seen, an unlock final and warned about when that result was seen. Synthetic data only."""
import json

import pytest
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core import journal
from wider.core.project import Project
from wider.recall.validation import record_look
from wider.sample.labels import label, lock, unlock

SEEN = "The recall estimate on validation is no longer blind: its result was seen before this unlock."


@pytest.fixture
def proj(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    for i in range(1, 7):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    for sid, name, kind in ((1, "dev", "purposive"), (2, "validation", "probability")):
        p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(?,?,?,'{}',3,'x')",
                        (sid, name, kind))
        for pos, rid in enumerate((1, 2, 3) if sid == 1 else (4, 5, 6)):
            p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)", (sid, rid, pos))
    return p


def _cli(p, *args):
    return CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)])


def _look(p):
    """A recall result on "validation" recorded as seen (the estimate records the look)."""
    record_look(p, "validation", "main", [{"record_id": 4, "human": "include"}])


def _locked(p, name):
    return p.store.scalar("SELECT locked FROM samples WHERE name=?", (name,))


def test_lock_is_journalled_and_undoable_until_recall_is_seen(proj):
    tok = journal.AUTHOR.set("ana")   # the page's author header
    try:
        lock(proj, "dev")
    finally:
        journal.AUTHOR.reset(tok)
    ch = journal.changes(proj, kind="sample_lock")
    assert len(ch) == 1 and ch[0]["author"] == "ana" and "dev" in ch[0]["summary"]
    assert ch[0]["kind_label"] == "sample locked" and ch[0]["undoable"]
    label(proj, "dev", 1, "ana", "include")            # a first label on a locked sample is an addition (D2)
    journal.undo(proj, ch[0]["change_id"], author="ana")
    assert _locked(proj, "dev") == 0
    label(proj, "dev", 2, "ana", "include")            # open again
    # a recall result seen after the lock: its undo is refused
    lock(proj, "validation", by="ana")
    _look(proj)
    v = journal.changes(proj, kind="sample_lock")[0]
    msg = "recall on validation was seen after the lock; unlock it instead, which is recorded"
    assert not v["undoable"] and v["why_not"] == msg
    with pytest.raises(ValueError, match="unlock it instead"):
        journal.undo(proj, v["change_id"], author="ana")
    assert _locked(proj, "validation") == 1


def test_a_lock_from_an_older_version_is_unlocked_not_undone(proj):
    proj.store.execute("UPDATE samples SET locked=1 WHERE name='dev'")   # locked by an older version: no History entry
    assert not journal.changes(proj, kind="sample_lock")
    r = _cli(proj, "sample", "unlock", "dev", "--by", "ana")
    assert r.exit_code == 0 and "unlocked dev" in r.output
    ch = journal.changes(proj, kind="sample_unlock")
    assert len(ch) == 1 and ch[0]["author"] == "ana" and not ch[0]["undoable"]
    # an older project whose recall looks were taken before looks were recorded: "may have been seen"
    proj.store.execute("UPDATE samples SET locked=1 WHERE name='validation'")
    proj.store.set_meta("recall_looks_before_v7", "2026-01-01T00:00:00+00:00")
    r = _cli(proj, "sample", "unlock", "validation", "--by", "ana")
    assert r.exit_code == 2 and "its result may have been seen before this unlock" in r.output
    assert _locked(proj, "validation") == 1
    assert _cli(proj, "sample", "unlock", "validation", "--by", "ana", "--yes").exit_code == 0
    assert _locked(proj, "validation") == 0


def test_unlock_on_the_command_line_is_journalled_and_final(proj):
    assert _cli(proj, "sample", "lock", "dev", "--by", "ana").exit_code == 0
    r = _cli(proj, "sample", "unlock", "dev", "--by", "ana")
    assert r.exit_code == 0 and "unlocked dev" in r.output and _locked(proj, "dev") == 0
    ch = journal.changes(proj, kind="sample_unlock")
    assert len(ch) == 1 and ch[0]["author"] == "ana" and ch[0]["kind_label"] == "sample unlocked"
    assert ch[0]["why_not"] == "an unlock is final" and ch[0]["touches"] == ["samples"]
    with pytest.raises(ValueError, match="an unlock is final"):
        journal.undo(proj, ch[0]["change_id"], author="ana")


def test_lock_unknown_sample(proj):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    before = proj.store.scalar("SELECT COUNT(*) FROM history")
    for cmd in ("lock", "unlock"):
        r = _cli(proj, "sample", cmd, "nosuch")
        assert r.exit_code == 1 and "no sample 'nosuch'" in r.output
    c = TestClient(create_app(proj))
    for q in ("", "?unlock=true"):
        r = c.post(f"/api/samples/nosuch/lock{q}", headers={"X-Wider-Author": "ana"})
        assert r.status_code == 404 and r.json()["detail"] == "no sample 'nosuch'"
    assert proj.store.scalar("SELECT COUNT(*) FROM history") == before
    assert proj.store.scalar("SELECT COUNT(*) FROM samples WHERE locked=1") == 0


def test_unlock_after_recall_warns_and_needs_yes(proj):
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    lock(proj, "validation", by="ana")
    _look(proj)
    r = _cli(proj, "sample", "unlock", "validation", "--by", "ana")
    assert r.exit_code == 2 and SEEN in r.output and _locked(proj, "validation") == 1
    assert not journal.changes(proj, kind="sample_unlock")
    c = TestClient(create_app(proj))
    assert c.get("/api/samples").json()["samples"]["validation"]["unlock_warning"] == SEEN
    r = c.post("/api/samples/validation/lock?unlock=true", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 409 and r.json()["detail"] == SEEN and _locked(proj, "validation") == 1
    r = c.post("/api/samples/validation/lock?unlock=true&yes=true", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200 and r.json()["unlocked"] and _locked(proj, "validation") == 0
    ch = journal.changes(proj, kind="sample_unlock")
    assert len(ch) == 1 and ch[0]["author"] == "ana"
    items = json.loads(proj.store.scalar("SELECT items FROM history WHERE change_id=?", (ch[0]["change_id"],)))
    assert {"t": "recall_seen", "sample": "validation", "value": "yes"} in items
    # the CLI's --yes unlocks too
    lock(proj, "validation", by="ana")
    r = _cli(proj, "sample", "unlock", "validation", "--by", "ana", "--yes")
    assert r.exit_code == 0 and SEEN in r.output and "unlocked validation" in r.output


def test_sample_unlock_in_methods_and_package(proj, tmp_path):
    import zipfile

    from wider.recall.outputs import methods_draft, write_package

    def package(p):
        z = zipfile.ZipFile(p.root / write_package(p)["path"])
        return {n: z.read(n) for n in z.namelist()}

    # a project without an unlock: neither the sentence nor the file
    assert "was unlocked on" not in methods_draft(proj)
    assert not [n for n in package(proj) if n.endswith("sample_unlocks.json")]
    lock(proj, "validation", by="ana")
    _look(proj)
    assert unlock(proj, "validation", by="ana", yes=True)["unlocked"]
    at = proj.store.scalar("SELECT at FROM history WHERE kind='sample_unlock'")
    from wider.core.researchers import Attributor  # shared files name the author by exports.attribution
    ana = Attributor(proj).who("ana")
    assert ana.startswith("Researcher ")
    assert f"The validation sample was unlocked on {at[:10]} by {ana} after its recall result had been seen." \
        in methods_draft(proj)
    files = package(proj)
    name = next(n for n in files if n.endswith("sample_unlocks.json"))
    rows = json.loads(files[name])
    assert rows == [{"sample": "validation", "at": at, "by": ana, "recall_seen": "yes"}]
    body = files[name].decode()
    assert "record_id" not in body and "Synthetic paper" not in body and "include" not in body


def test_page_lock_without_a_name_is_refused(proj):
    """risk-checker R2: a lock or unlock from the page with no reviewer name signed History with '' (the methods
    sentence read "by  "); the route now answers 400 and changes nothing."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    c = TestClient(create_app(proj))
    before = proj.store.scalar("SELECT COUNT(*) FROM history")
    for q in ("", "?unlock=true"):
        r = c.post(f"/api/samples/validation/lock{q}")
        assert r.status_code == 400 and r.json()["detail"].startswith("a reviewer name is needed")
    assert proj.store.scalar("SELECT COUNT(*) FROM history") == before and _locked(proj, "validation") == 0
    r = c.post("/api/samples/validation/lock", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200 and journal.changes(proj, kind="sample_lock")[0]["author"] == "ana"


def test_an_old_lock_is_not_undone_after_unlock_and_relock(proj):
    """risk-checker R4: ana locks, bo unlocks and locks again; undoing ana's lock would have cancelled bo's lock (the
    check compared the samples row only). It is refused; bo's lock can still be undone."""
    lock(proj, "dev", by="ana")
    unlock(proj, "dev", by="bo")
    lock(proj, "dev", by="bo")
    old, new = sorted(journal.changes(proj, kind="sample_lock"), key=lambda c: c["change_id"])
    msg = "dev was unlocked after this lock; the later lock is the one to undo"
    assert not old["undoable"] and old["why_not"] == msg and new["undoable"]
    with pytest.raises(ValueError, match="the later lock is the one to undo"):
        journal.undo(proj, old["change_id"], author="ana")
    assert _locked(proj, "dev") == 1
    journal.undo(proj, new["change_id"], author="bo")
    assert _locked(proj, "dev") == 0
