"""researcher-attribution, section 4: names in shared files follow ``exports.attribution`` (D5), the name scan
before sharing (D6) and no author in model prompts (D9). Synthetic, topic-neutral names and records only."""
import csv
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_recall import _corpus, _sample_of
from wider.cli import app as cli_app
from wider.construct.artifacts import record_unlock
from wider.core import journal
from wider.core import researchers as rs
from wider.core.project import Project
from wider.core.sanitize import NameFound
from wider.interview import ledger
from wider.recall.outputs import methods_draft, save_methods, write_package
from wider.recall.report import build_report, share_bundle, write_report
from wider.sample.labels import label
from wider.ui.app import create_app

runner = CliRunner()
ANA, LI, LOGIN, ENV, CARA = "Ana Silva", "Li", "login-x", "rev-env", "Cara Tomé"
LONG = "Maximiliana Konstantinopoulou-Wertheimsson"   # longer than 40 characters: config_diff cuts it
MODES = ("pseudonyms", "names", "none")
BIB_FILES = ("records.csv", "benchmark.csv", "result_sets.csv")   # titles, journals, authors (D6)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setattr(rs.getpass, "getuser", lambda: LOGIN)
    return monkeypatch


def _cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "200"})


def _mode(p, mode):
    rs.set_attribution(p, mode)
    return Project.load(p.root)


def _built(tmp_path, env):
    """Declared "Ana Silva" (Researcher 1) and "Li" (2), inferred "login-x" (3, from $USER), declared LONG (4, the
    term switch, named only inside a configuration change), declared "Cara Tomé" (5, a configuration comparison
    only), an unnamed change, an unlock and a ledger row."""
    p = _corpus(tmp_path, n=12)          # the band setting: no author recorded
    ids = _sample_of(p, tmp_path)
    for rid in ids:
        label(p, "validation", rid, ANA, "include", reason="clear fit")
        label(p, "validation", rid, LI, "include" if rid in ids[:4] else "exclude")
    env.setenv("USER", LOGIN)
    p.config.exclude_notes = "a synthetic exclusion note"
    p.save()
    env.delenv("USER")
    record_unlock(p, "uploads", None, None, by=ANA, detail="a file added")
    from wider.core.lookups import allow_terms
    allow_terms(p, author=LONG)
    with journal.recording(p, "note", "a ledger row", author=LI) as rec:
        did = ledger.record(p, "term", "k1", {"note": f"{LI} kept it"}, ["keep", "drop"], "keep", "keep", "chosen",
                            f"{LI} kept the term", LI, None)
        rec.rows("decisions", "decision_id=?", [did])
    rs.register(p, CARA, "option")
    p.store.execute("INSERT INTO screen_comparisons(name,sample,configs,repeats,record_ids,author,created_at) "
                    "VALUES(?,?,?,?,?,?,?)", ("cmp1", "validation", "[]", 1, "[]", CARA, "2026-01-01T00:00:00"))
    return p, ids


def _zip_texts(p, r) -> dict[str, str]:
    z = zipfile.ZipFile(p.root / r["path"])
    return {n: z.read(n).decode("utf-8", "replace") for n in z.namelist()}


def _outside_bib(files: dict[str, str]) -> dict[str, str]:
    return {n: t for n, t in files.items() if not n.endswith(BIB_FILES)}


def test_attributor_modes(tmp_path, env):
    """who/text/hidden in each mode: declared and inferred entries, a joined entry, "no author recorded", "not
    shown", the import placeholder and system authors."""
    p = Project.init(tmp_path / "p", platforms=["scopus"])
    rs.register(p, ANA, "option")
    rs.register(p, LOGIN, "USER")
    rs.register(p, "A. Silva", "option")
    rs.join(p, "A. Silva", ANA, by=ANA)
    a = rs.Attributor(p, "pseudonyms")
    assert [a.who(n) for n in (ANA, LOGIN, "A. Silva", "", None, "import", "migration", "who")] == [
        "Researcher 1", "Researcher 2", "Researcher 1", "no author recorded", "no author recorded", "imported labels",
        "migration", "an unlisted researcher"]
    assert a.hidden() == {ANA: "Researcher 1", LOGIN: "Researcher 2", "A. Silva": "Researcher 1"}
    assert a.text(f"set by {ANA} and A. Silva") == "set by Researcher 1 and Researcher 1"
    n = rs.Attributor(p, "names")
    assert [n.who(x) for x in (ANA, LOGIN, "A. Silva")] == [ANA, "Researcher 2", ANA]
    assert set(n.hidden()) == {LOGIN, "A. Silva"} and n.text(f"by {ANA}") == f"by {ANA}"
    z = rs.Attributor(p, "none")
    assert [z.who(x) for x in (ANA, LOGIN, "")] == ["not shown", "not shown", "no author recorded"]
    assert z.text(f"by {ANA}") == "by a researcher"
    assert a.text("by Li", own=["Li"]) == "by Li"          # an unlisted short name stays
    rs.register(p, LI, "option")
    a = rs.Attributor(p, "pseudonyms")
    assert a.text("by Li", own=["Li"]) == "by Researcher 4" and a.text("by Li") == "by Li"   # 2 characters: own only


def test_bundle_labels_follow_the_setting(tmp_path, env):
    """labels.csv: pseudonyms, declared names, or a ``reader`` column numbering each sample's readers 1, 2."""
    p, ids = _built(tmp_path, env)
    got = {}
    for mode in MODES:
        p = _mode(p, mode)
        b = share_bundle(p)
        rows = list(csv.DictReader(io.StringIO((b / "labels.csv").read_text())))
        col = "reader" if mode == "none" else "reviewer"
        assert col in rows[0] and ("reviewer" not in rows[0] or mode != "none")
        got[mode] = sorted({r[col] for r in rows if r["sample"] == "validation" and r[col]})
    imp = "imported labels"   # the sample's imported decisions are no reader
    assert got == {"pseudonyms": ["Researcher 1", "Researcher 2", imp], "names": [ANA, LI, imp], "none": ["1", "2", imp]}


def test_interventions_under_each_mode(tmp_path, env):
    """Authors per mode, "no author recorded" for the unnamed change, the same dates, kinds and counts; a config
    summary holding a name cut to 40 characters carries no part of the name; "Li" (2 characters) as an author."""
    p, _ = _built(tmp_path, env)
    seen = {}
    for mode in MODES:
        p = _mode(p, mode)
        md, js = write_report(p)
        rep = json.loads(js.read_text())
        iv = rep["interventions"]
        seen[mode] = iv
        authors = {e["author"] for e in iv["entries"]}
        text = md.read_text()
        assert rep["attribution"] == mode
        assert "no author recorded" in authors
        assert LOGIN not in text and (LONG[:40] in text) == (mode == "names")   # declared: shown, cut, in names
        if mode == "pseudonyms":
            assert {"Researcher 1", "Researcher 3"} <= authors and ANA not in text
            assert "- label: 12 entries; Researcher 1, Researcher 2; " in text
        elif mode == "names":
            assert {ANA, "Researcher 3"} <= authors
            assert f"- label: 12 entries; {ANA}, {LI}; " in text
        else:
            assert authors <= {"not shown", "no author recorded"} and not re.search(r"Researcher \d", text) and ANA not in text
            assert "- label: 12 entries; not shown; " in text
    # each switch of the setting is itself a recorded change; the entries before them are the same in every mode
    shape = {m: [(e["at"], e["kind"], e["undone"]) for e in iv["entries"] if "exports.attribution" not in e["summary"]]
             for m, iv in seen.items()}
    counts = {m: {k["kind"]: k["count"] for k in iv["by_kind"]} for m, iv in seen.items()}
    assert shape["pseudonyms"] == shape["names"] == shape["none"]
    assert {k: v for k, v in counts["pseudonyms"].items() if k != "config"} == {
        k: v for k, v in counts["none"].items() if k != "config"}
    cut = next(e for e in seen["pseudonyms"]["entries"] if "terms_allowed_by" in e["summary"])
    assert "Researcher 4" in cut["summary"] and "Maximiliana" not in cut["summary"]


def _agreement(rows, col):
    by = {}
    for r in rows:
        if r[col] and r[col] not in ("imported labels", "import", "original"):
            by.setdefault(r["record_id"], {})[r[col]] = r["decision"]
    pairs = [tuple(v.values()) for v in by.values() if len(v) == 2]
    return sum(a == b for a, b in pairs), len(pairs)


def test_none_mode(tmp_path, env):
    """Mode none: no name and no "Researcher n" in any bundle file outside bibliographic text; the agreement of the two
    readers computed from labels.csv equals the one from the stored labels; REPRODUCE.md states the mode."""
    p, ids = _built(tmp_path, env)
    p = _mode(p, "none")
    b = share_bundle(p)
    files = {str(f.relative_to(b)): f.read_text("utf-8", "replace") for f in b.rglob("*") if f.is_file()}
    for name, t in _outside_bib(files).items():
        for n in (ANA, LOGIN, CARA, LONG, "Researcher 1", "Researcher 2"):
            assert n not in t, (name, n)
    assert rs.REPRODUCE_LINES["none"] in files["REPRODUCE.md"]
    rows = list(csv.DictReader(io.StringIO(files["labels.csv"])))
    stored = [{"record_id": str(r["record_id"]), "reviewer": r["reviewer_id"], "decision": r["decision"]}
              for r in p.store.query("SELECT record_id, reviewer_id, decision FROM labels")]
    assert _agreement(rows, "reader") == _agreement(stored, "reviewer") == (4, 6)


def test_name_keyed_counts_survive_none_mode(tmp_path, env):
    """progress.reviewers: one count per reader in mode none (a list), the joined names' counts added; keyed by
    pseudonym otherwise."""
    p, ids = _built(tmp_path, env)
    rs.register(p, "A. Silva", "option")
    label(p, "validation", ids[0], "A. Silva", "exclude")   # a second record by the same person under another name
    rs.join(p, "A. Silva", ANA, by=ANA)
    p = _mode(p, "none")
    rep = rs.attribute_report(p, build_report(p))
    rv = rep["samples"]["validation"]["progress"]["reviewers"]
    assert isinstance(rv, list) and sorted(x["done"] for x in rv) == [6, 6, 7]   # with the imported labels
    p = _mode(p, "pseudonyms")
    rv = rs.attribute_report(p, build_report(p))["samples"]["validation"]["progress"]["reviewers"]
    assert rv["Researcher 1"]["done"] == 7 and rv["Researcher 2"]["done"] == 6


def test_pseudonyms_across_the_package(tmp_path, env):
    """Mode pseudonyms: no researcher name in any package file outside bibliographic text; the comparison (its
    author's only change), the unlock and the ledger write pseudonyms; manifest.json and REPRODUCE.md state the mode."""
    p, _ = _built(tmp_path, env)
    r = write_package(p)
    files = _zip_texts(p, r)
    for name, t in _outside_bib(files).items():
        for n in (ANA, LOGIN, CARA, LONG, LONG[:40]):
            assert n not in t, (name, n)
    assert json.loads(files["manifest.json"])["attribution"] == "pseudonyms" and r["attribution"] == "pseudonyms"
    assert rs.REPRODUCE_LINES["pseudonyms"] in files["share_bundle/REPRODUCE.md"]
    assert json.loads(files["comparisons.json"])[0]["author"] == "Researcher 5"
    assert json.loads(files["unlock.json"])[0]["by"] == "Researcher 1"
    led = [json.loads(x) for x in files["decisions.jsonl"].splitlines()]
    assert led[0]["author"] == "Researcher 2" and "Researcher 2 kept the term" in led[0]["consequence"]
    assert "Researcher 2 kept it" in json.dumps(led[0]["evidence"])
    assert "no author recorded" in files["share_bundle/report.md"]


def test_names_mode_hides_inferred(tmp_path, env):
    """Mode names: declared names as recorded, the inferred login name as its pseudonym."""
    p, _ = _built(tmp_path, env)
    p = _mode(p, "names")
    files = _zip_texts(p, write_package(p))
    assert json.loads(files["unlock.json"])[0]["by"] == ANA
    assert json.loads(files["comparisons.json"])[0]["author"] == CARA
    assert all(LOGIN not in t for t in _outside_bib(files).values())
    assert "| Researcher 3 |" in files["share_bundle/report.md"]


def test_ledger_authors_follow_the_setting(tmp_path, env):
    """The package's ledger follows the mode ("Li", 2 characters, as its own author); runs/query keeps the name."""
    p, _ = _built(tmp_path, env)
    want = {"pseudonyms": "Researcher 2", "names": LI, "none": "not shown"}
    for mode in MODES:
        p = _mode(p, mode)
        jl, cs = ledger.export_texts(p, rs.Attributor(p))
        row = json.loads(jl.splitlines()[0])
        assert row["author"] == want[mode], mode
        assert row["consequence"].startswith("a researcher" if mode == "none" else want[mode])
        assert next(csv.DictReader(io.StringIO(cs)))["author"] == want[mode]
    ledger.export(p)
    kept = (p.root / "runs" / "query" / "decisions.jsonl").read_text()
    assert f'"author": "{LI}"' in kept


def test_no_mapping_in_shared_files(tmp_path, env):
    """Mode names: no package file holds both a hidden name and its pseudonym, and none lists the researchers."""
    p, _ = _built(tmp_path, env)
    p = _mode(p, "names")
    files = _zip_texts(p, write_package(p))
    for name, t in files.items():
        assert not (LOGIN in t and "Researcher 3" in t), name
        assert "first_seen" not in t and "joined_into" not in t, name


def test_history_unchanged_by_exports(tmp_path, env):
    """History and the researchers table read the same before and after a package in each mode, apart from stage
    results the writes record."""
    p, _ = _built(tmp_path, env)

    def snap(q):
        h = [tuple(r) for r in q.store.query("SELECT at, author, kind, summary FROM history WHERE kind != 'stage_result'"
                                             " ORDER BY change_id")]
        return h, [tuple(r) for r in q.store.query("SELECT * FROM researchers ORDER BY number")]
    for mode in MODES:
        p = _mode(p, mode)
        before = snap(p)
        write_package(p)
        assert snap(Project.load(p.root))[1] == before[1]
        after = snap(Project.load(p.root))[0]
        assert after[:len(before[0])] == before[0] and all(a[1] in (None, "") or a[2] == "config" for a in after[len(before[0]):]
                                                           ) or after == before[0], mode


def test_methods_draft_follows_the_setting(tmp_path, env):
    """The methods draft carries no researcher name in pseudonyms mode; History keeps the name. The validation sentence
    "The researcher (...)" itself is pinned per mode in test_validation_looks.py::test_not_informed_disclosed."""
    p, _ = _built(tmp_path, env)
    p = _mode(p, "pseudonyms")
    d = methods_draft(p)
    assert ANA not in d and LI not in d
    assert any(r["author"] == ANA for r in p.store.query("SELECT author FROM history"))


def test_name_left_in_a_file_refuses(tmp_path, env):
    """A name the researcher typed into methods.md refuses the package (CLI exit 1, route 400) with the file, line and
    pseudonym, and no partial package remains; a secret is reported first; a hidden name WIDER left in unlock.json is
    reported as a WIDER bug."""
    p, _ = _built(tmp_path, env)
    save_methods(p, "First line.\nSecond line.\nScreened by Ana Silva.\n")
    msg = "methods.md line 3: the name of Researcher 1; edit the file"
    with pytest.raises(NameFound) as e:
        write_package(p)
    assert msg in str(e.value) and ANA not in str(e.value)
    assert not list((p.root / "exports").glob("replication_package_*"))
    r = _cli(p, "export", "package")
    assert r.exit_code == 1 and msg in " ".join(r.output.split())
    assert r.output.startswith(rs.MODE_LINES["pseudonyms"])   # the mode is stated before writing, refused or not
    c = TestClient(create_app(p))
    res = c.post("/api/outputs/package")
    assert res.status_code == 400 and msg in res.json()["detail"]
    assert not list((p.root / "exports").glob("replication_package_*"))
    # a secret in the same file is reported first (the secret scan runs before the name scan)
    save_methods(p, "First line.\nkey sk-ant-api03-" + "A" * 40 + "\nScreened by Ana Silva.\n")
    with pytest.raises(Exception) as e:
        write_package(p)
    assert not isinstance(e.value, NameFound)
    # a hidden name only in unlock.json's by: WIDER left it, so the message asks for a report
    save_methods(p, "Methods in my words.\n")
    orig = rs.Attributor.obj

    def leaky(self, obj, *a, **k):
        out = orig(self, obj, *a, **k)
        if isinstance(out, list) and out and isinstance(out[0], dict) and "seen" in out[0]:
            out[0]["by"] = ANA
        return out
    env.setattr(rs.Attributor, "obj", leaky)
    with pytest.raises(NameFound) as e:
        write_package(p)
    assert "unlock.json" in str(e.value) and "WIDER bug" in str(e.value)
    assert not list((p.root / "exports").glob("replication_package_*"))


def test_login_name_is_never_written(tmp_path, env):
    """$USER "login-x" and $WIDER_REVIEWER "rev-env" that never signed: a title holding "login-x" is bibliographic and
    allowed, neither value occurs elsewhere in any mode; a login "will" that signed nothing does not refuse a report
    holding the word; "login-x" signed but unconfirmed stays hidden in mode names."""
    p = _corpus(tmp_path, n=12)
    p.store.execute("UPDATE records SET title=? WHERE record_id=(SELECT MIN(record_id) FROM records)",
                    (f"A synthetic title about {LOGIN} things",))
    ids = _sample_of(p, tmp_path)
    label(p, "validation", ids[0], ANA, "include", reason="it will fit")
    env.setenv("USER", LOGIN)
    env.setenv("WIDER_REVIEWER", ENV)
    for mode in MODES:
        p = _mode(p, mode)
        files = _zip_texts(p, write_package(p))
        for name, t in _outside_bib(files).items():
            assert LOGIN not in t and ENV not in t, (name, mode)
    env.setattr(rs.getpass, "getuser", lambda: "will")
    env.setenv("USER", "will")
    write_package(p)
    env.setenv("USER", LOGIN)
    env.delenv("WIDER_REVIEWER")
    p.config.exclude_notes = "signed by the login name"
    p.save()
    assert rs.is_inferred(p, LOGIN)
    p = _mode(p, "names")
    files = _zip_texts(p, write_package(p))
    assert all(LOGIN not in t for t in _outside_bib(files).values())


@pytest.mark.parametrize("mode", MODES)
def test_privacy_rules_hold_in_every_mode(tmp_path, env, mode):
    """The acceptance test of the privacy rules: every file of the bundle and the unzipped package holds no hidden
    name, no "login-x" and no "rev-env" outside the bibliographic files."""
    p, _ = _built(tmp_path, env)
    env.setenv("WIDER_REVIEWER", ENV)
    env.setenv("USER", LOGIN)
    p = _mode(p, mode)
    hidden = set(rs.Attributor(p).hidden()) | {LOGIN, ENV}
    files = _zip_texts(p, write_package(p))
    b = p.root / "exports" / "share_bundle"
    files |= {f"bundle/{f.relative_to(b)}": f.read_text("utf-8", "replace") for f in b.rglob("*") if f.is_file()}
    for name, t in _outside_bib(files).items():
        for n in hidden:
            if len(n) >= 3:
                assert n not in t, (mode, name, n)


def test_prompt_texts_carry_no_author(tmp_path, env):
    """The ledger lines sent to a model carry no author; ``wider decisions list`` still shows it."""
    p, _ = _built(tmp_path, env)
    texts = ledger.prompt_texts(p)
    rows = ledger.rows(p)
    assert texts == [ledger.text(d, author=False) for d in rows] and not any("accepted by" in t for t in texts)
    assert all(LI not in t.replace(f"{LI} kept it", "") for t in texts)   # only the researcher's own note names Li
    assert any(LI in ledger.text(d) for d in rows)
    r = _cli(p, "decisions", "list")
    assert r.exit_code == 0 and LI in r.output


def test_cli_states_the_mode_first(tmp_path, env):
    """`wider report --bundle` and `wider export package` print the mode line before anything they write."""
    p, _ = _built(tmp_path, env)
    for mode in ("pseudonyms", "names", "none"):
        p = _mode(p, mode)
        for args in (("report", "--bundle"), ("export", "package")):
            r = _cli(p, *args)
            assert r.exit_code == 0, r.output
            first = " ".join(r.output.split("wrote")[0].split())
            assert first.startswith(" ".join(rs.MODE_LINES[mode].split())), (mode, args, r.output[:200])


def test_join_line_names_no_one(tmp_path, env):
    """The History line of a join and of its undo names both sides; in shared files it is written without either name,
    so no file links a pseudonym to the name it was joined into (ui-tester, bad-input user F1)."""
    p, _ = _built(tmp_path, env)
    r = _cli(p, "researchers", "join", LOGIN, "--into", ANA, "--by", LI)
    assert r.exit_code == 0, r.output
    jid = journal.changes(p, kind="researcher_join")[0]["change_id"]
    journal.undo(p, jid, author=LI)
    assert any(f"researcher {LOGIN} joined into {ANA}" in c["summary"] for c in journal.changes(p))
    for mode in MODES:
        p = _mode(p, mode)
        report = (share_bundle(p) / "report.md").read_text()
        files = _zip_texts(p, write_package(p))
        for name, t in [("report.md", report), *files.items()]:
            assert "joined into" not in t, (mode, name)
            assert LOGIN not in t, (mode, name)
        assert rs.JOIN_SHARED in report, mode


def test_short_confirmed_name_not_written(tmp_path, env):
    """The History line of a confirm names a short inferred name ("Bo"); shared files write it as the pseudonym, or,
    with no names, without one; with real names Bo is declared by the confirm and written (risk-checker R3)."""
    p, _ = _built(tmp_path, env)
    rs.register(p, "Bo", "USER")
    assert _cli(p, "researchers", "confirm", "Bo", "--by", ANA).exit_code == 0
    for mode in ("pseudonyms", "none"):
        p = _mode(p, mode)
        report = (share_bundle(p) / "report.md").read_text()
        assert not re.search(r"\bBo\b", report), mode
        want = "a researcher name confirmed" if mode == "none" else "confirmed"
        assert want in report, mode
    p = _mode(p, "pseudonyms")
    assert re.search(r"researcher Researcher \d+ confirmed", (share_bundle(p) / "report.md").read_text())
    p = _mode(p, "names")
    assert "researcher Bo confirmed" in (share_bundle(p) / "report.md").read_text()


def test_older_pseudonym_shaped_author_is_not_refused(tmp_path, env):
    """A project from before this version whose History holds the author "Researcher 9" still writes its shared files
    in every mode: the name scan does not look for a name the pseudonyms already read as (risk-checker R2)."""
    p, _ = _built(tmp_path, env)
    p.store.execute("INSERT INTO history(at,author,kind,summary,items) VALUES(?,?,?,?,?)",
                    ("2026-01-01T00:00:09", "Researcher 9", "note", "s", "[]"))
    rs.register(p, "Researcher 9", "older version")
    for mode in MODES:
        p = _mode(p, mode)
        assert (share_bundle(p) / "report.md").exists(), mode
        write_package(p)


def test_redo_chain_bundle_in_names_mode(tmp_path, env):
    """A redo that makes a join chain (login-x into Ana Silva, Ana Silva into Li) still writes the names-mode bundle,
    with neither joined name in it (risk-checker R4)."""
    p, _ = _built(tmp_path, env)
    assert _cli(p, "researchers", "join", LOGIN, "--into", ANA, "--by", LI).exit_code == 0
    journal.undo(p, journal.changes(p, kind="researcher_join")[0]["change_id"], author=LI)
    assert _cli(p, "researchers", "join", ANA, "--into", LI, "--by", LI).exit_code == 0
    journal.undo(p, journal.changes(p, kind="undo")[0]["change_id"], author=LI)
    p = _mode(p, "names")
    report = (share_bundle(p) / "report.md").read_text()
    assert LOGIN not in report and ANA not in report


def test_sample_unlock_author_follows_the_setting(tmp_path, env):
    """A sample unlock signed by the short name "Li": the methods sentence and sample_unlocks.json write the
    pseudonym, or no author with no names (merge with manual-review-workbench D5)."""
    from wider.sample.labels import lock, unlock
    p, _ = _built(tmp_path, env)
    lock(p, "validation", by=LI)
    unlock(p, "validation", by=LI, yes=True)
    for mode in MODES:
        p = _mode(p, mode)
        draft = methods_draft(p)
        line = re.search(r"The validation sample was unlocked on [^.;]*", draft).group(0)
        assert not re.search(r"\bLi\b", line) or mode == "names", (mode, line)
        assert (" by " in line) == (mode != "none"), (mode, line)
        files = _zip_texts(p, write_package(p))
        body = next(t for n, t in files.items() if n.endswith("sample_unlocks.json"))
        assert ('"Li"' in body) == (mode == "names"), (mode, body)
