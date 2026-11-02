"""researcher-attribution, sections 2 and 3: the researcher list (declared and inferred names, pseudonyms by first
appearance), how a name was given, confirm and join, and the setting ``exports.attribution``. Synthetic names only."""
import csv
import json

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_records import _deduped, _sample
from wider.cli import app as cli_app
from wider.core import annotations as ann
from wider.core import journal
from wider.core import questions as qs
from wider.core import researchers as rs
from wider.core.project import Project
from wider.sample.labels import label
from wider.ui.app import create_app

runner = CliRunner()


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    monkeypatch.delenv("USER", raising=False)
    return monkeypatch


def _by_name(p):
    return {e["name"]: e for e in rs.entries(p)}


def _cli(p, *args, env=None):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "200", **(env or {})})


def test_older_project_becomes_inferred(tmp_path, no_env):
    """A project made before the list: every name already in History (by first change) and in the author columns
    becomes an inferred entry with the source "recorded before this version"; opening it again adds nothing."""
    p = Project.init(tmp_path / "p", platforms=["scopus"])
    st = p.store
    for at, who in [("2026-01-01T00:00:01", "ui"), ("2026-01-01T00:00:02", "login-x"), ("2026-01-01T00:00:03", "ui"),
                    ("2026-01-01T00:00:04", "migration"), ("2026-01-01T00:00:05", "")]:
        st.execute("INSERT INTO history(at,author,kind,summary,items) VALUES(?,?,?,?,?)", (at, who, "note", "s", "[]"))
    st.execute("INSERT INTO reviewers(reviewer_id,name,created_at) VALUES(?,?,?)", ("Ana Silva", "Ana Silva", "t"))
    st.execute("DROP TABLE researchers")
    st.set_meta("schema_version", "7")
    p.close()
    for _ in range(2):
        p = Project.load(tmp_path / "p")
        got = [(e["name"], e["pseudonym"], e["status"], e["source_key"]) for e in rs.entries(p)]
        assert got == [("ui", "Researcher 1", "inferred", "older version"),
                       ("login-x", "Researcher 2", "inferred", "older version"),
                       ("Ana Silva", "Researcher 3", "inferred", "older version")]
        assert rs.entries(p)[0]["source"] == "recorded before this version"
        p.close()


def test_pseudonyms_by_first_appearance(tmp_path, no_env):
    """Names given with an option are declared; numbers follow first appearance; the page's GET equals
    `wider researchers list --json`; a page request signs with source "page"."""
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "ben", "first")
    ann.add_note(p, ids[0], "Ana Silva", "second")
    ann.add_note(p, ids[1], "ben", "third")
    e = _by_name(p)
    assert [(n, e[n]["pseudonym"], e[n]["status"]) for n in e] == [("ben", "Researcher 1", "declared"),
                                                                      ("Ana Silva", "Researcher 2", "declared")]
    assert e["ben"]["source"] == "given with --by, --author or --reviewer"
    assert not any(c["author_inferred"] for c in journal.changes(p))
    c = TestClient(create_app(p))
    r = c.post(f"/api/records/{ids[2]}/notes", json={"text": "fourth", "author": "cleo"}, headers={"X-Wider-Author": "cleo"})
    assert r.status_code == 200, r.text
    got = c.get("/api/researchers").json()
    assert got["mode"] == "pseudonyms" and got["mode_line"] == rs.MODE_LINES["pseudonyms"]
    assert got["not_verified"] == rs.NOT_VERIFIED
    assert (got["researchers"][2]["name"], got["researchers"][2]["source"]) == ("cleo", "typed on the page")
    out = _cli(p, "researchers", "list", "--json")
    assert out.exit_code == 0 and json.loads(out.output) == got["researchers"]
    out = _cli(p, "researchers", "list").output
    assert out.splitlines()[0] == rs.MODE_LINES["pseudonyms"] and rs.NOT_VERIFIED in out and "Researcher 3" in out


def test_pseudonym_never_reused(tmp_path, no_env):
    """Undoing the only change of a name keeps its entry and number; the next new name takes the next number."""
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "ben", "a")
    ann.add_note(p, ids[0], "Ana Silva", "b")
    journal.undo(p, journal.changes(p, kind="note")[-1]["change_id"], author="ben")
    ann.add_note(p, ids[0], "cleo", "c")
    e = _by_name(p)
    assert (e["ben"]["pseudonym"], e["cleo"]["pseudonym"]) == ("Researcher 1", "Researcher 3")


def test_unnamed_change_adds_no_entry(tmp_path, no_env):
    """A change with no name (no option, no page name, no environment) and the system or placeholder authors add no
    entry; History says no author was recorded."""
    p, ids = _deduped(tmp_path)
    rs.set_attribution(p, "names")   # a settings change signed with no name at all
    assert journal.changes(p, kind="config")[0]["author"] == ""
    for name in ("migration", "import", "original", "  "):
        rs.register(p, name, "option")
    assert rs.entries(p) == []
    assert rs.Attributor(p).who("") == "no author recorded"


def test_environment_names_are_inferred(tmp_path, no_env):
    """`$WIDER_REVIEWER` and `$USER` sign inferred entries with their own source words; a later --author with the
    same name upgrades the entry to declared, never back."""
    p, ids = _deduped(tmp_path)
    r = _cli(p, "notes", "add", str(ids[0]), "from env", env={"WIDER_REVIEWER": "rev-env"})
    assert r.exit_code == 0, r.output
    no_env.setenv("USER", "login-x")
    r = _cli(p, "notes", "add", str(ids[0]), "from login")
    assert r.exit_code == 0, r.output
    e = _by_name(p)
    assert (e["rev-env"]["status"], e["rev-env"]["source"]) == ("inferred", "from $WIDER_REVIEWER")
    assert (e["login-x"]["status"], e["login-x"]["source"]) == ("inferred", "login name of this computer")
    assert [c["author_inferred"] for c in journal.changes(p, kind="note")] == [True, True]
    assert _cli(p, "notes", "add", str(ids[1]), "typed", "--author", "rev-env").exit_code == 0
    assert _by_name(p)["rev-env"]["status"] == "declared"
    assert _cli(p, "notes", "add", str(ids[1]), "env again", env={"WIDER_REVIEWER": "rev-env"}).exit_code == 0
    assert _by_name(p)["rev-env"]["status"] == "declared"


def test_imported_reviewers_are_inferred(tmp_path, no_env):
    """Reviewer ids read from an imported label file are inferred ("from an imported label file"); a reviewer given
    with --reviewer is declared; the placeholders `import` and `original` add no entry."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:3], name="validation")
    recs = [dict(r) for r in p.store.query("SELECT title, doi FROM records ORDER BY record_id")]
    ray = tmp_path / "rayyan.csv"
    with open(ray, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "doi", "notes"])
        w.writerow([recs[0]["title"], recs[0]["doi"], 'RAYYAN-INCLUSION: {"ben"=>"Excluded"}'])
    plain = tmp_path / "plain.csv"
    with open(plain, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["title", "doi", "decision"])
        w.writerow([recs[1]["title"], recs[1]["doi"], "include"])
    r = _cli(p, "label", "import", "validation", str(ray))
    assert r.exit_code == 0, r.output
    assert _cli(p, "label", "import", "validation", str(plain)).exit_code == 0
    e = _by_name(p)
    assert list(e) == ["ben"] and (e["ben"]["status"], e["ben"]["source"]) == ("inferred", "from an imported label file")
    assert _cli(p, "label", "import", "validation", str(plain), "--reviewer", "cleo").exit_code == 0
    assert _by_name(p)["cleo"]["status"] == "declared"


def test_unjournalled_authors_are_registered(tmp_path, no_env):
    """Writers that store an author without a History change register it through `sign`, with the same source rules."""
    p, _ = _deduped(tmp_path)
    assert rs.sign(p, "Ana Silva") == "Ana Silva"
    no_env.setenv("USER", "login-x")
    assert rs.sign(p) == "login-x"
    no_env.delenv("USER")
    assert rs.sign(p) is None
    e = _by_name(p)
    assert (e["Ana Silva"]["status"], e["login-x"]["status"]) == ("declared", "inferred")
    assert len(e) == 2


def test_review_package_source_is_declared(tmp_path, no_env):
    p, _ = _deduped(tmp_path)
    rs.register(p, "rev-b", "imported")
    assert _by_name(p)["rev-b"]["status"] == "inferred"
    rs.register(p, "rev-b", "review package")
    assert (_by_name(p)["rev-b"]["status"], _by_name(p)["rev-b"]["source_key"]) == ("declared", "review package")
    rs.register(p, "rev-c", "review package")
    assert _by_name(p)["rev-c"]["source"] == "from a review package"


def test_cli_fallbacks_are_inferred(tmp_path, no_env):
    """Without --by every path signs with `$USER` as an inferred name: notes and questions (their own fallback is
    gone), an adjudication, a duplicate decision and a pasted round count."""
    from tests.unit.test_dry_run import _import_one, _proj, _versions
    from wider.dedup.engine import decide, queue_rows
    from wider.search import rounds

    no_env.setenv("USER", "login-x")
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ben", "include")
    label(p, "dev", ids[0], "cleo", "exclude")
    assert _cli(p, "notes", "add", str(ids[0]), "n").exit_code == 0
    qid = qs.raise_question(p, "enrich_unresolved", "x1", "A record", "No abstract.", record_id=ids[0],
                            context={"doi": None})
    assert _cli(p, "questions", "answer", str(qid), "leave").exit_code == 0
    assert _cli(p, "label", "adjudicate", "dev", str(ids[0]), "include").exit_code == 0
    q = queue_rows(p)[0]
    decide(p, q["raw_a"], q["raw_b"], "duplicate")
    for kind in ("note", "answer", "adjudication", "dedup_decision"):
        ch = journal.changes(p, kind=kind)
        assert ch and (ch[0]["author"], ch[0]["author_inferred"]) == ("login-x", True), (kind, ch)
    assert _by_name(p)["login-x"]["status"] == "inferred"
    p2 = _proj(tmp_path, name="r2")
    _import_one(p2, tmp_path, "r2.ris")
    _versions(p2, "a*")
    rounds.set_count(p2, 1, "scopus", "1,200")
    ch = journal.changes(p2, kind="round_count")[0]
    assert (ch["author"], ch["author_inferred"]) == ("login-x", True)
    assert _by_name(p2)["login-x"]["status"] == "inferred"


def test_history_marks_inferred(tmp_path, no_env):
    p, ids = _deduped(tmp_path)
    no_env.setenv("USER", "login-x")
    assert _cli(p, "notes", "add", str(ids[0]), "inferred one").exit_code == 0
    ann.add_note(p, ids[0], "ben", "declared one")
    out = _cli(p, "history", "list").output
    lines = [x for x in out.splitlines() if "login-x" in x or "ben" in x]
    assert any("login-x" in x and "(inferred, not confirmed)" in x for x in lines), out
    assert not any("ben" in x and "inferred" in x for x in lines), out
    got = TestClient(create_app(p)).get("/api/history").json()
    rows = got["changes"] if isinstance(got, dict) else got
    assert {(c["author"], c["author_inferred"]) for c in rows if c["kind"] == "note"} == {("login-x", True),
                                                                                         ("ben", False)}


def test_confirm_and_undo(tmp_path, no_env):
    """Confirm makes an inferred name declared as one History change; undo makes it inferred again; the CLI and the
    route call the same function."""
    p, ids = _deduped(tmp_path)
    no_env.setenv("USER", "login-x")
    assert _cli(p, "notes", "add", str(ids[0]), "x").exit_code == 0
    no_env.delenv("USER")
    r = _cli(p, "researchers", "confirm", "login-x", "--by", "Ana Silva")
    assert r.exit_code == 0 and "login-x is declared (Researcher 1)" in r.output, r.output
    ch = journal.changes(p, kind="researcher_confirm")[0]
    assert ch["kind_label"] and ch["undoable"] and ch["author"] == "Ana Silva"
    assert _by_name(p)["login-x"]["status"] == "declared"
    journal.undo(p, ch["change_id"], author="Ana Silva")
    assert _by_name(p)["login-x"]["status"] == "inferred"
    c = TestClient(create_app(p))
    assert c.post("/api/researchers/confirm", json={"name": "login-x"}).status_code == 200
    assert _by_name(p)["login-x"]["status"] == "declared"
    r = c.post("/api/researchers/confirm", json={"name": "login-x"})
    assert r.status_code == 400 and "already declared" in r.text
    r = _cli(p, "researchers", "confirm", "nobody")
    assert r.exit_code == 1 and "no researcher named" in r.output


def test_join_two_names(tmp_path, no_env):
    """Join: the joined name shares the target's pseudonym and status, in the list and in shared files; undo splits
    them again."""
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "Ana Silva", "x")
    no_env.setenv("USER", "login-x")
    assert _cli(p, "notes", "add", str(ids[0]), "y").exit_code == 0
    r = _cli(p, "researchers", "join", "login-x", "--into", "Ana Silva", "--by", "Ana Silva")
    assert r.exit_code == 0 and "login-x is now Researcher 1, the same researcher as Ana Silva" in r.output, r.output
    e = _by_name(p)
    assert (e["login-x"]["pseudonym"], e["login-x"]["status"], e["login-x"]["joined_into"]) == (
        "Researcher 1", "declared", "Ana Silva")
    a = rs.Attributor(p, "names")
    assert a.who("login-x") == "Ana Silva" and rs.Attributor(p).who("login-x") == "Researcher 1"
    journal.undo(p, journal.changes(p, kind="researcher_join")[0]["change_id"], author="Ana Silva")
    e = _by_name(p)
    assert (e["login-x"]["pseudonym"], e["login-x"]["status"], e["login-x"]["joined_into"]) == (
        "Researcher 2", "inferred", None)


def test_join_refusals(tmp_path, no_env):
    p, ids = _deduped(tmp_path)
    for who in ("Ana Silva", "ben", "cleo"):
        ann.add_note(p, ids[0], who, "x")
    cases = [("ben", "ben", "cannot be joined into itself"), ("ben", "nobody", "no researcher named"),
             ("nobody", "ben", "no researcher named")]
    for name, into, why in cases:
        r = _cli(p, "researchers", "join", name, "--into", into)
        assert r.exit_code == 1 and why in r.output, (name, into, r.output)
    assert _cli(p, "researchers", "join", "ben", "--into", "Ana Silva").exit_code == 0
    c = TestClient(create_app(p))
    for name, into, why in [("cleo", "ben", "is itself joined into"), ("ben", "cleo", "is already joined into"),
                            ("Ana Silva", "cleo", "is joined into 'Ana Silva'")]:
        r = c.post("/api/researchers/join", json={"name": name, "into": into})
        assert r.status_code == 400 and why in r.json()["detail"], (name, into, r.text)
        assert why in _cli(p, "researchers", "join", name, "--into", into).output
    assert len(journal.changes(p, kind="researcher_join")) == 1
    r = _cli(p, "researchers", "confirm", "ben")
    assert r.exit_code == 1 and "is joined into" in r.output


def test_attribution_default_and_change(tmp_path, no_env):
    """Default pseudonyms; the CLI and the route save the mode as a journalled settings change that undo reverts; an
    unknown mode is refused before project.yaml is touched."""
    p, _ = _deduped(tmp_path)
    assert rs.mode_of(p) == "pseudonyms"
    r = _cli(p, "researchers", "attribution", "names", env={"WIDER_REVIEWER": "ben"})
    assert r.exit_code == 0 and rs.MODE_LINES["names"] in r.output, r.output
    assert Project.load(p.root).config.exports.attribution == "names"
    ch = journal.changes(p, kind="config")[0]
    assert ch["undoable"]
    journal.undo(p, ch["change_id"], author="ben")
    assert Project.load(p.root).config.exports.attribution == "pseudonyms"
    before = (p.root / "project.yaml").read_text()
    r = _cli(p, "researchers", "attribution", "everyone")
    assert r.exit_code == 1 and "unknown mode" in r.output
    c = TestClient(create_app(p))
    assert c.post("/api/researchers/attribution", json={"mode": "everyone"}).status_code == 400
    assert (p.root / "project.yaml").read_text() == before
    r = c.post("/api/researchers/attribution", json={"mode": "none"})
    assert r.status_code == 200 and c.get("/api/researchers").json()["mode"] == "none"
    assert yaml.safe_load((p.root / "project.yaml").read_text())["exports"]["attribution"] == "none"


def test_pseudonym_like_name_refused(tmp_path, no_env):
    """A name of the form "Researcher n" is refused before anything changes, from the CLI and from the page: shared
    files could not tell it from a pseudonym (ui-tester, bad-input user F2)."""
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "Ana Silva", "x")
    no_env.setenv("USER", "login-x")
    assert _cli(p, "notes", "add", str(ids[0]), "y").exit_code == 0
    before = (len(rs.entries(p)), len(journal.changes(p)))
    for name in ("Researcher 2", "researcher 7"):
        r = _cli(p, "notes", "add", str(ids[0]), "z", "--author", name)
        assert r.exit_code == 1 and "has the form of the pseudonyms" in r.output, r.output
    r = TestClient(create_app(p)).post("/api/researchers/confirm", json={"name": "login-x"},
                                       headers={"X-Wider-Author": "Researcher 2"})
    assert r.status_code == 400 and "give another name" in r.json()["detail"]
    assert (len(rs.entries(p)), len(journal.changes(p))) == before
    assert _by_name(p)["login-x"]["status"] == "inferred"
    assert _cli(p, "notes", "add", str(ids[0]), "z", "--author", "Researcher Two").exit_code == 0


def test_control_characters_dropped_from_cli_names(tmp_path, no_env):
    """A tab or newline in --author is dropped, as the page header drops it (ui-tester F4)."""
    p, ids = _deduped(tmp_path)
    assert _cli(p, "notes", "add", str(ids[0]), "x", "--author", "tab\tname\n").exit_code == 0
    assert "tabname" in _by_name(p) and journal.changes(p)[0]["author"] == "tabname"


def test_join_stays_undoable_after_the_name_signs(tmp_path, no_env):
    """A joined inferred name that later signs a change with --by is not made declared outside the journal, so the
    join can still be undone (risk-checker R1)."""
    p, ids = _deduped(tmp_path)
    ann.add_note(p, ids[0], "Ana Silva", "x")
    no_env.setenv("USER", "login-x")
    assert _cli(p, "notes", "add", str(ids[0]), "y").exit_code == 0
    no_env.delenv("USER")
    assert _cli(p, "researchers", "join", "login-x", "--into", "Ana Silva", "--by", "Ana Silva").exit_code == 0
    assert _cli(p, "notes", "add", str(ids[0]), "z", "--author", "login-x").exit_code == 0
    journal.undo(p, journal.changes(p, kind="researcher_join")[0]["change_id"], author="Ana Silva")
    e = _by_name(p)["login-x"]
    assert (e["pseudonym"], e["status"], e["joined_into"]) == ("Researcher 2", "inferred", None)


def test_pseudonym_like_name_refused_everywhere(tmp_path, no_env):
    """A settings save from the page, an undo and label imports under a name of the form "Researcher n" are refused
    before anything changes; a Rayyan file naming such a reviewer imports no row at all (risk-checker R2, R5)."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    ann.add_note(p, ids[0], "Ana Silva", "x")
    before = (p.root / "project.yaml").read_text()
    r = TestClient(create_app(p)).post("/api/researchers/attribution", json={"mode": "none"},
                                       headers={"X-Wider-Author": "Researcher 2"})
    assert r.status_code == 400 and "has the form of the pseudonyms" in r.json()["detail"]
    assert (p.root / "project.yaml").read_text() == before
    with pytest.raises(ValueError, match="has the form of the pseudonyms"):
        journal.undo(p, journal.changes(p)[0]["change_id"], author="researcher 3")
    assert not journal.changes(p)[0].get("undone")
    with pytest.raises(ValueError, match="has the form of the pseudonyms"):
        label(p, "dev", ids[0], "Researcher 4", "include")
    titles = [p.store.scalar("SELECT title FROM records WHERE record_id=?", (i,)) for i in ids[:2]]
    f = tmp_path / "rayyan.csv"
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "notes"])
        w.writerow([titles[0], 'RAYYAN-INCLUSION: {"Dana"=>"Included"}'])
        w.writerow([titles[1], 'RAYYAN-INCLUSION: {"Researcher 5"=>"Excluded"}'])
    r = _cli(p, "label", "import", "dev", str(f))
    assert r.exit_code != 0 and "nothing was imported" in r.output, r.output
    assert p.store.scalar("SELECT COUNT(*) FROM labels") == 0
    assert "Researcher 5" not in _by_name(p) and "Dana" not in _by_name(p)


def test_redo_chain_resolves_to_one_entry(tmp_path, no_env):
    """Redoing an older join after its target was joined elsewhere makes a chain; it resolves to the last entry
    (risk-checker R4)."""
    p, ids = _deduped(tmp_path)
    for who in ("Ana Silva", "ben", "cleo"):
        ann.add_note(p, ids[0], who, "x")
    assert _cli(p, "researchers", "join", "cleo", "--into", "Ana Silva", "--by", "ben").exit_code == 0
    first = journal.changes(p, kind="researcher_join")[0]["change_id"]
    journal.undo(p, first, author="ben")
    assert _cli(p, "researchers", "join", "Ana Silva", "--into", "ben", "--by", "ben").exit_code == 0
    redo = journal.changes(p, kind="undo")[0]["change_id"]
    journal.undo(p, redo, author="ben")
    assert _by_name(p)["cleo"]["joined_into"] == "Ana Silva"
    assert rs.resolve(p, "cleo")["name"] == "ben"
    for mode in ("pseudonyms", "names"):
        assert rs.Attributor(p, mode).who("cleo") == rs.Attributor(p, mode).who("ben")
