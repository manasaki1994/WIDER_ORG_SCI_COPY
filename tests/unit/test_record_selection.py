"""Record exports chosen on the Export records card: presets, pathways, first-two-read patterns, final only, chosen ids,
the count before export, formats, abstracts, the audit CSV and the export package (decision-set-exports-and-found-chart,
design D1-D8). Each scenario runs the function, the CLI (``wider export selection``) and the route. Synthetic,
topic-neutral records only."""
import csv
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_agreement import fill, rules
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.recall.outputs import export_references
from wider.recall.selection import (
    AUDIT_COLUMNS,
    export_selection,
    select_records,
    write_audit,
    write_records_package,
)
from wider.ui.app import create_app

runner = CliRunner()

# records 1-10 of set `main`: 3 include, 2 maybe (record 5 unresolved), 5 exclude (record 9 by the researcher)
BASE = [("include", ["include", "include"], "A,B"),
        ("include", ["include", "exclude", "include"], "A,C"),            # third read; reads include / exclude
        ("include", ["include", "include"], "A,B"),
        ("maybe", ["maybe", "maybe"], "A,B"),
        ("maybe", ["include", "maybe", "exclude"], "split"),             # unresolved; reads include / maybe
        ("exclude", ["exclude", "exclude"], "A,B"),
        ("exclude", ["exclude", "exclude"], "A,B"),
        ("exclude", ["exclude", "exclude"], "A,B"),
        ("exclude", ["maybe", "exclude", "include", "researcher:exclude"], "researcher"),   # reads maybe / exclude
        ("exclude", ["exclude", "exclude"], "A,B")]


def _proj(tmp_path, rows=BASE, n=20):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = Project.init(tmp_path / "sel", platforms=["scopus"])
    for i in range(1, n + 1):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, authors, year, journal, doi, url, abstract) "
                        "VALUES(?,?,?,?,?,2020,?,?,?,?)",
                        (i, i, f"k{i}", f"Synthetic paper {i}", "Doe, Ann; Roe, Ben", "Synthetic Journal",
                         f"10.5555/syn.{i}", f"https://example.org/{i}", f"Synthetic abstract {i}"))
    fill(p, "main", rows, rules())
    return p


def cli(p, *args):
    return runner.invoke(cli_app, ["export", "selection", *args, "--dir", str(p.root)])


def api(p, method="get", **params):
    c = TestClient(create_app(Project.load(p.root)))
    return getattr(c, method)("/api/outputs/selection", params=params)


def ris_ids(path):
    return [int(ln.rsplit("record ", 1)[1].rstrip(")")) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.startswith("N1")]


def ref_files(p):
    d = p.root / "exports" / "references"
    return sorted(x.name for x in d.iterdir()) if d.exists() else []


def stage_count(p):
    return p.store.one("SELECT COUNT(*) n FROM stage_results")["n"]


def snapshot(p):
    return [[tuple(r) for r in p.store.query(f"SELECT * FROM {t} ORDER BY 1, 2")]
            for t in ("screen_decisions", "screen_result_sets", "questions")]


# ----------------------------------------------------------------------------------------------- presets and filters
def test_default_include_and_maybe_ris(tmp_path):
    """Scenario "One-click default"."""
    p = _proj(tmp_path)
    old = export_references(p, "main", which="include+maybe")
    old_bytes = (p.root / old["files"][0]).read_bytes()
    for f in ref_files(p):
        (p.root / "exports" / "references" / f).unlink()
    before = stage_count(p)
    r = cli(p)
    assert r.exit_code == 0, r.output
    assert ref_files(p) == ["main_include_and_maybe.ris"]
    path = p.root / "exports" / "references" / "main_include_and_maybe.ris"
    assert ris_ids(path) == [1, 2, 3, 4, 5] and stage_count(p) == before + 1
    text = path.read_text(encoding="utf-8")
    for tag in ("TI  - Synthetic paper 1", "AU  - Doe, Ann", "PY  - 2020", "JO  - Synthetic Journal",
                "DO  - 10.5555/syn.1", "UR  - https://example.org/1", "AB  - Synthetic abstract 1",
                "N1  - WIDER main: include (record 1)", "N1  - WIDER main: maybe (record 4)"):
        assert tag in text
    assert path.read_bytes() == old_bytes
    j = api(p, "post").json()
    assert j["written"] and j["file"] == "exports/references/main_include_and_maybe.ris" and j["n"] == 5


def test_export_changes_no_decision(tmp_path):
    """Scenario "One-click default", last line: the store is the same after every kind of export."""
    p = _proj(tmp_path)
    before = snapshot(p)
    export_selection(p, "main")
    export_selection(p, "main", preset="every", formats="ris,bib,csv")
    write_audit(p, "main")
    write_records_package(p, "main", add="include,exclude,unresolved")
    assert api(p, "get").status_code == 200 and api(p, "post", preset="exclude").status_code == 200
    assert snapshot(p) == before


def test_preset_and_pathway_combined(tmp_path):
    """Scenario "Preset and pathway combined"."""
    p = _proj(tmp_path)
    r = cli(p, "--preset", "exclude", "--pathway", "researcher")
    assert r.exit_code == 0, r.output
    d = p.root / "exports" / "references"
    assert ris_ids(d / "main_exclude_by_researcher.ris") == [9]
    assert api(p, "post", preset="include", pathway="third_read").json()["file"].endswith("main_include_by_third_read.ris")
    assert ris_ids(d / "main_include_by_third_read.ris") == [2]
    a = export_selection(p, "main", preset="include", pathways="third_read,agreed")
    b = select_records(p, "main", "include", ["agreed", "third_read"])
    assert a["file"].endswith("main_include_by_agreed_third_read.ris") and a["n"] == b["n"] == 3
    assert ris_ids(d / "main_include_by_agreed_third_read.ris") == [1, 2, 3]


def test_no_pathway_holds_every_route(tmp_path):
    """Scenario "No pathway ticked holds every route"."""
    rows = BASE + [("include", None, "imported from list.csv"),
                   ("include", ["include", "include"], "A,B", {"kind": "reused", "set": 1, "decided_by": "A,B"}),
                   ("maybe", ["include", "maybe", "exclude"], "split", {"kind": "reused", "set": 1, "decided_by": "split"})]
    p = _proj(tmp_path, rows)
    s = select_records(p, "main", "include")
    assert [r["record_id"] for r in s["rows"]] == [1, 2, 3, 11, 12] and s["other_route"] == 2
    r = cli(p, "--preset", "include", "--dry-run")
    assert "2 by another route (imported, carried, one pass or older)" in r.output
    assert api(p, preset="include").json()["pathway_line"] in r.output
    ticked = select_records(p, "main", "include+maybe", ["agreed", "third_read", "split", "researcher"])
    assert [r["record_id"] for r in ticked["rows"]] == [1, 2, 3, 4, 5, 13]   # 11, 12 left out; 13 carried unresolved in


def test_pattern_of_first_two_reads(tmp_path):
    """Scenario "Disagreement pattern of the first two reads"."""
    p = _proj(tmp_path, BASE + [("include", None, "imported from list.csv")])
    r = cli(p, "--preset", "every", "--pattern", "include_exclude")
    assert r.exit_code == 0, r.output
    assert ris_ids(p.root / "exports" / "references" / "main_every_reads_include_exclude.ris") == [2]
    ids = lambda s: [x["record_id"] for x in s["rows"]]   # noqa: E731
    assert ids(select_records(p, "main", "every", patterns="include_maybe,maybe_exclude")) == [5, 9]
    assert ids(select_records(p, "main", "every", patterns="agreed")) == [1, 3, 4, 6, 7, 8, 10]   # 11 has no reads
    assert ids(select_records(p, "main", "every", ["split"], ["include_maybe", "maybe_exclude"])) == [5]
    assert api(p, preset="every", pattern="maybe_exclude").json()["n"] == 1


def test_unresolved_only_and_final_only(tmp_path):
    """Scenario "Unresolved only, or unresolved left out"."""
    rows = list(BASE)
    rows[3] = ("maybe", ["include", "maybe", "exclude"], "split", {"kind": "reused", "set": 1, "decided_by": "split"})
    p = _proj(tmp_path, rows)
    r = cli(p, "--preset", "unresolved")
    assert r.exit_code == 0, r.output
    d = p.root / "exports" / "references"
    assert ris_ids(d / "main_unresolved.ris") == [4, 5]
    assert (d / "main_unresolved.ris").read_text(encoding="utf-8").count("maybe (unresolved: no majority)") == 2
    r = cli(p, "--final-only")
    assert ris_ids(d / "main_include_and_maybe_final.ris") == [1, 2, 3]
    assert "3 records (2 unresolved left out) · RIS" in r.output
    assert api(p, final_only=True).json()["count_line"] == "3 records (2 unresolved left out) · RIS"
    split = select_records(p, "main", "include+maybe", ["split"])
    assert split["n"] == select_records(p, "main")["unresolved"] == 2
    q = _proj(tmp_path / "b", rows + [("include", None, "imported from list.csv"),
                                       ("maybe", ["maybe", "maybe"], "A,B", {"kind": "reused", "set": 1})])
    assert [x["record_id"] for x in select_records(q, "main", final_only=True)["rows"]] == [1, 2, 3, 11, 12]


def test_records_chosen_one_by_one(tmp_path):
    """Scenario "Records chosen one by one"."""
    p = _proj(tmp_path)
    g = api(p).json()
    assert [x["record_id"] for x in g["records"]] == [1, 2, 3, 4, 5]
    assert set(g["records"][0]) == {"record_id", "title", "year", "decision", "status", "pathway"}
    r = cli(p, "--records", "1,2,3")
    assert r.exit_code == 0, r.output
    assert ris_ids(p.root / "exports" / "references" / "main_include_and_maybe_picked.ris") == [1, 2, 3]
    assert "3 of 5 records chosen · RIS" in r.output
    assert api(p, records="1,2,3").json()["count_line"] == "3 of 5 records chosen · RIS"
    assert export_selection(p, "main", records="1,2,3,4,5")["file"].endswith("main_include_and_maybe.ris")  # all = not picked
    before = ref_files(p)
    r = cli(p, "--records", "1,7")
    assert r.exit_code == 1 and "record 7 is not in this selection" in r.output and ref_files(p) == before
    assert api(p, "post", records="1,7").status_code == 400 and ref_files(p) == before


def test_empty_selection_and_bad_choices(tmp_path):
    """Scenario "Empty selection and bad choices"."""
    p = _proj(tmp_path)
    r = cli(p, "--preset", "include", "--pathway", "split")
    assert r.exit_code == 0 and "No records match Include, unresolved disagreement; nothing was written." in r.output
    assert ref_files(p) == []
    d = p.root / "exports" / "references"
    d.mkdir(parents=True)
    (d / "main_include_by_split.ris").write_text("earlier\n", encoding="utf-8")
    msg = api(p, "post", preset="include", pathway="split").json()["message"]
    assert msg.startswith("No records match Include, unresolved disagreement; nothing was written. The earlier file "
                          "main_include_by_split.ris (") and msg.endswith(") no longer matches this selection.")
    assert (d / "main_include_by_split.ris").read_text(encoding="utf-8") == "earlier\n"
    for args, allowed in ((["--preset", "all"], "every"), (["--pathway", "import"], "researcher"),
                          (["--pattern", "include_include"], "maybe_exclude"), (["--format", "docx"], "csv"),
                          (["--format", ""], "ris")):
        r = cli(p, *args)
        assert r.exit_code == 1 and allowed in r.output, (args, r.output)
    for opt in (["--preset", "include"], ["--pathway", "agreed"], ["--pattern", "agreed"], ["--final-only"],
                ["--records", "1"], ["--format", "bib"], ["--no-abstracts"]):
        r = cli(p, "--audit", *opt)
        assert r.exit_code == 1 and "--audit covers the whole set" in r.output and opt[0] in r.output
    assert ref_files(p) == ["main_include_by_split.ris"]
    assert api(p, "post", preset="all").status_code == 400


# ----------------------------------------------------------------------------------------------- count, formats, abstracts
def test_dry_run_counts_and_writes_nothing(tmp_path):
    """Scenario "Count before export"."""
    p = _proj(tmp_path)
    before = stage_count(p)
    r = cli(p, "--dry-run")
    assert r.exit_code == 0, r.output
    assert "5 records (of which 1 unresolved: no majority) · RIS" in r.output
    assert "By how the decision was reached: 3 both configurations agreed; 1 resolved by the third model; " \
           "1 unresolved disagreement" in r.output
    assert "First two reads: 1 include / exclude; 1 include / maybe; 3 both reads the same" in r.output
    assert "main_include_and_maybe.ris" in r.output and ref_files(p) == [] and stage_count(p) == before
    g = api(p).json()
    assert g["count_line"] == "5 records (of which 1 unresolved: no majority) · RIS" and not g["written"]
    assert api(p, format="ris,bib").json()["count_line"].endswith("· RIS + BibTeX in one ZIP")
    assert ref_files(p) == [] and stage_count(p) == before
    assert api(p, "post").json()["n"] == 5 == len(ris_ids(p.root / "exports" / "references" / "main_include_and_maybe.ris"))


def test_repeated_options_add_up(tmp_path):
    """A repeated --format, --pathway, --pattern or --add counts every value, as the comma-separated form does (7.6)."""
    p = _proj(tmp_path)
    rep = cli(p, "--format", "ris", "--format", "csv", "--pathway", "agreed", "--pathway", "third_read",
              "--pattern", "agreed", "--pattern", "include_maybe", "--dry-run")
    comma = cli(p, "--format", "ris,csv", "--pathway", "agreed,third_read", "--pattern", "agreed,include_maybe",
                "--dry-run")
    assert rep.exit_code == comma.exit_code == 0, rep.output
    assert rep.output == comma.output and "3 records · RIS + CSV in one ZIP" in rep.output
    assert "_by_agreed_third_read_reads_include_maybe_agreed.zip" in rep.output.replace("\n", "")
    assert ref_files(p) == []
    rep = cli(p, "--package", "--add", "include", "--add", "exclude", "--format", "ris", "--dry-run")
    comma = cli(p, "--package", "--add", "include,exclude", "--format", "ris", "--dry-run")
    assert rep.exit_code == comma.exit_code == 0, rep.output
    stamp = re.compile(r"\d{8}T\d{6}")                 # the package name carries the time of the run
    assert stamp.sub("T", rep.output) == stamp.sub("T", comma.output) and ref_files(p) == []
    assert "main_include.ris: 3 records" in rep.output and "main_exclude.ris: 5 records" in rep.output


def _zip_members(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_formats_and_zip(tmp_path):
    """Scenario "Several formats in one ZIP"."""
    p = _proj(tmp_path)
    r = cli(p, "--format", "csv,bib,ris")
    assert r.exit_code == 0, r.output
    assert ref_files(p) == ["main_include_and_maybe.zip"]
    m = _zip_members(p.root / "exports" / "references" / "main_include_and_maybe.zip")
    assert sorted(m) == ["main_include_and_maybe.bib", "main_include_and_maybe.csv", "main_include_and_maybe.ris"]
    rows = list(csv.DictReader(io.StringIO(m["main_include_and_maybe.csv"].decode("utf-8"))))
    assert list(rows[0]) == ["record_id", "decision", "decided_by", "title", "authors", "year", "journal", "doi", "url",
                             "abstract", "protocol", "result_set", "status", "pathway"]
    assert [int(x["record_id"]) for x in rows] == [1, 2, 3, 4, 5]
    assert [x["status"] for x in rows] == ["final"] * 4 + ["unresolved"]
    bib, ris = m["main_include_and_maybe.bib"].decode(), m["main_include_and_maybe.ris"].decode()
    for x in rows:
        assert f"record {x['record_id']})" in bib and f"record {x['record_id']})" in ris
        assert x["title"] in bib and x["title"] in ris and x["doi"] in bib and x["doi"] in ris
    assert "Doe, Ann and Roe, Ben" in bib and bib.count("year = {2020}") == 5
    r = cli(p, "--format", "bib")
    assert r.exit_code == 0 and "main_include_and_maybe.bib" in ref_files(p)
    assert ref_files(p) == ["main_include_and_maybe.bib", "main_include_and_maybe.zip"]
    assert api(p, "post", format="").status_code == 400


def test_no_abstracts(tmp_path):
    """Scenario "Abstracts left out"."""
    p = _proj(tmp_path)
    with_ab = export_selection(p, "main", formats="ris,bib,csv")
    m1 = _zip_members(p.root / with_ab["file"])
    assert with_ab["abstracts_sentence"] == ("Abstracts are the publishers' text; leave them out if you pass these "
                                             "files to someone else.")
    r = cli(p, "--format", "ris,bib,csv", "--no-abstracts")
    assert r.exit_code == 0, r.output
    m2 = _zip_members(p.root / with_ab["file"])
    ris, bib = m2["main_include_and_maybe.ris"].decode(), m2["main_include_and_maybe.bib"].decode()
    assert "AB  -" not in ris and "abstract =" not in bib
    assert [ln for ln in m1["main_include_and_maybe.ris"].decode().splitlines() if not ln.startswith("AB")] == ris.splitlines()
    rows = list(csv.DictReader(io.StringIO(m2["main_include_and_maybe.csv"].decode())))
    assert all(x["abstract"] == "" for x in rows) and rows[0]["title"] == "Synthetic paper 1"
    assert api(p, "post", format="ris", abstracts=False).json()["abstracts"] is False


def test_older_export_parity(tmp_path):
    """Scenario "Exclusions", last line: every older set, in all three formats, byte for byte and with the same names."""
    p = _proj(tmp_path)
    old = {"include": ("include", None), "include+maybe": ("include+maybe", None), "maybe": ("maybe", None),
           "exclude": ("exclude", None), "agreed": ("every", "agreed"), "third_read": ("every", "third_read"),
           "split": ("every", "split"), "researcher": ("every", "researcher")}
    for which, (preset, pathway) in old.items():
        files = export_references(p, "main", which=which)["files"]
        want = {f.rsplit("/", 1)[1]: (p.root / f).read_bytes() for f in files}
        new = export_selection(p, "main", preset=preset, pathways=pathway, formats="ris,bib,csv")
        assert _zip_members(p.root / new["file"]) == want, which


# ----------------------------------------------------------------------------------------------- audit CSV
def _audit_proj(tmp_path, monkeypatch, user="synth_user"):
    """13 records in scope: 10 decided as BASE (record 9 by the researcher, record 5 unresolved, record 2's second read
    supplied by the researcher for a failed call), 11 and 12 with failed calls, 13 with no call."""
    monkeypatch.setenv("USER", user)
    p = _proj(tmp_path)
    st = p.store
    st.execute("INSERT INTO runs(run_id, kind, stamp, spec_hash, provider, model, status, started_at, dir) "
               "VALUES('r1','screen','s','h','fake','model-a','done','2026-01-01','runs/r1')")
    st.execute("UPDATE screen_result_sets SET runs=?, scope=?, created_by=? WHERE key='main'",
               (json.dumps(["r1"]), "ids:" + ",".join(map(str, range(1, 14))), user))
    calls = {}
    for rid, (_final, votes, _by) in enumerate(BASE, 1):
        ids = []
        for i, v in enumerate([v for v in votes if not v.startswith("researcher:")]):
            human = rid == 2 and i == 1
            cur = st.execute("INSERT INTO screen_calls(run_id, record_id, pass, input_sha256, ok, rule_decision, parsed, at, "
                             "provider, model) VALUES('r1',?,?,'x',1,?,?,'t',?,?)",
                             (rid, "ABC"[i], v, json.dumps({"by": user} if human else {}),
                              "researcher" if human else "fake", None if human else f"model-{'abc'[i]}"))
            ids.append(cur.lastrowid)
        calls[rid] = ids
        st.execute("UPDATE screen_decisions SET vote_calls=? WHERE protocol='main' AND record_id=?", (json.dumps(ids), rid))
    st.execute("UPDATE screen_decisions SET reason=? WHERE protocol='main' AND record_id=9",
               (f"researcher ({user}) decided after three disagreeing reads: methods unclear",))
    for rid in (11, 12):
        st.execute("INSERT INTO screen_calls(run_id, record_id, pass, input_sha256, ok, at, provider, model) "
                   "VALUES('r1',?,'A','x',0,'t','fake','model-a')", (rid,))
    st.execute("INSERT INTO questions(kind, stage, dedupe_key, record_id, title, prompt, options, context, status, answer, "
               "answered_by, created_at) VALUES('screen_split','screen','d9',9,'t','p','{}',?,'answered',?,?,'t')",
               (json.dumps({"protocol": "main"}), json.dumps({"choice": "exclude", "text": "methods unclear"}), user))
    return p


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_audit_rows(tmp_path, monkeypatch):
    """Scenario "Audit rows"."""
    p = _audit_proj(tmp_path, monkeypatch)
    before = stage_count(p)
    r = cli(p, "--audit")
    assert r.exit_code == 0, r.output
    assert stage_count(p) == before + 1
    rows = _read_csv(p.root / "exports" / "references" / "main_audit.csv")
    assert len(rows) == 13 and tuple(rows[0]) == AUDIT_COLUMNS
    by = {int(x["record_id"]): x for x in rows}
    r9 = by[9]
    assert (r9["pass_1_read"], r9["pass_2_read"], r9["pass_3_read"]) == ("maybe", "exclude", "include")
    assert (r9["pathway"], r9["pattern"], r9["researcher_decision"], r9["researcher_note"]) == (
        "researcher", "maybe_exclude", "exclude", "methods unclear")
    assert (by[5]["status"], by[5]["final_decision"], by[5]["pathway"]) == ("unresolved", "maybe", "split")
    for rid, call in ((11, "failed"), (12, "failed"), (13, "none")):
        assert (by[rid]["status"], by[rid]["final_decision"], by[rid]["screen_call"]) == ("not screened", "", call)
    assert by[2]["pass_2_model"] == "researcher" and by[2]["pass_1_model"] == "model-a"
    assert by[1]["majority"] == "2" and by[1]["third_read"] == "blind" and by[1]["pathway_label"]
    text = (p.root / "exports" / "references" / "main_audit.csv").read_text(encoding="utf-8")
    assert "Synthetic abstract" not in text
    j = api(p, "post", audit=True).json()
    assert j["n"] == 13 and j["file"] == "exports/references/main_audit.csv"


def test_audit_second_screen_passes(tmp_path):
    """Scenario "Audit rows", last line: a second screen's passes S1, S2, S3 fill pass_1 to pass_3."""
    p = _proj(tmp_path)
    st = p.store
    st.execute("INSERT INTO runs(run_id, kind, stamp, spec_hash, model, status, started_at, dir) "
               "VALUES('r2','screen','s','h','m','done','t','runs/r2')")
    ids = [st.execute("INSERT INTO screen_calls(run_id, record_id, pass, input_sha256, ok, at, provider, model) "
                      "VALUES('r2',2,?,'x',1,'t','fake',?)", (ps, f"model-{ps}")).lastrowid for ps in ("S1", "S2", "S3")]
    st.execute("UPDATE screen_decisions SET vote_calls=? WHERE protocol='main' AND record_id=2", (json.dumps(ids),))
    rows = {int(x["record_id"]): x for x in _read_csv(p.root / write_audit(p, "main")["file"])}
    assert [rows[2][f"pass_{i}_model"] for i in (1, 2, 3)] == ["model-S1", "model-S2", "model-S3"]


def test_no_researcher_name(tmp_path, monkeypatch):
    """Scenario "No researcher name in the audit or package"."""
    p = _audit_proj(tmp_path, monkeypatch)
    audit = (p.root / write_audit(p, "main")["file"]).read_text(encoding="utf-8")
    assert "synth_user" not in audit and "methods unclear" in audit
    pkg = write_records_package(p, "main", add="include,maybe,exclude,unresolved,selection", formats="ris,bib,csv")
    for name, data in _zip_members(p.root / pkg["file"]).items():
        assert b"synth_user" not in data, name
    sel = export_selection(p, "main", preset="every", formats="ris,bib,csv")
    for name, data in _zip_members(p.root / sel["file"]).items():
        assert b"synth_user" not in data, name


def test_audit_not_in_replication_package(tmp_path, monkeypatch):
    """Scenario "Not duplicated in the replication package"."""
    p = _audit_proj(tmp_path, monkeypatch)
    write_audit(p, "main")
    r = runner.invoke(cli_app, ["export", "package", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    pkgs = sorted((p.root / "exports").glob("*package*.zip"))
    assert pkgs
    for name, data in _zip_members(pkgs[-1]).items():
        assert not name.endswith((".ris", "_audit.csv")) and b"Synthetic abstract" not in data, name


# ----------------------------------------------------------------------------------------------- export package
def _manifest_ok(members):
    import hashlib
    lines = [ln for ln in members["MANIFEST.txt"].decode().splitlines() if "  " in ln and not ln.startswith("sha256")]
    listed = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in lines}
    assert listed == {n: hashlib.sha256(b).hexdigest() for n, b in members.items() if n != "MANIFEST.txt"}


def test_default_package(tmp_path):
    """Scenario "Default package"."""
    p = _proj(tmp_path)
    before = stage_count(p)
    r = cli(p, "--package", "--dry-run")
    assert r.exit_code == 0, r.output
    assert "main_include_and_maybe.ris: 5 records, 1 marked unresolved" in r.output and "main_audit.csv" in r.output
    assert ref_files(p) == [] and stage_count(p) == before
    assert api(p, package=True).json()["lines"] == [ln for ln in r.output.splitlines() if ": " in ln and "would" not in ln]
    r = cli(p, "--package")
    assert r.exit_code == 0, r.output
    (z,) = [f for f in ref_files(p) if f.startswith("main_export_package_")]
    m = _zip_members(p.root / "exports" / "references" / z)
    assert sorted(m) == ["MANIFEST.txt", "main_audit.csv", "main_include_and_maybe.ris", "summary.md"]
    assert m["main_include_and_maybe.ris"].decode().count("N1  -") == 5
    assert m["main_include_and_maybe.ris"].decode().count("unresolved: no majority") == 1
    _manifest_ok(m)
    s = m["summary.md"].decode()
    for part in ("include: 3", "maybe: 2 (of which 1 unresolved: no majority)", "exclude: 5",
                 "both configurations agreed: 7", "resolved by the third model: 1", "decided by a researcher: 1",
                 "majority: 2 of 3", "protocol main", "Abstracts: included", "stays on this computer"):
        assert part in s, part
    assert stage_count(p) == before + 1


def test_customized_package(tmp_path):
    """Scenario "Customized package"."""
    p = _proj(tmp_path)
    r = cli(p, "--package", "--add", "include,exclude", "--format", "ris,csv")
    assert r.exit_code == 0, r.output
    (z,) = [f for f in ref_files(p) if f.startswith("main_export_package_")]
    m = _zip_members(p.root / "exports" / "references" / z)
    assert sorted(m) == ["MANIFEST.txt", "main_audit.csv", "main_exclude.csv", "main_exclude.ris", "main_include.csv",
                         "main_include.ris", "main_include_and_maybe.csv", "main_include_and_maybe.ris", "summary.md"]
    assert m["main_include.ris"].decode().count("N1  -") == 3 and m["main_exclude.ris"].decode().count("N1  -") == 5
    _manifest_ok(m)
    j = write_records_package(p, "main", add="selection", selection={"preset": "exclude", "records": "6,7"})
    assert "main_exclude_picked.ris" in [f["name"] for f in j["files"]]
    r = cli(p, "--package", "--records", "1,2")
    assert r.exit_code == 1 and "--add selection" in r.output
    assert api(p, "post", package=True, preset="include").status_code == 400
    cli(p, "--package")
    assert len([f for f in ref_files(p) if f.startswith("main_export_package_")]) == 3


def test_package_empty_part_writes_no_file(tmp_path):
    """ui-tester finding (spec silent): a package part that holds no record is listed as such and writes no empty
    file that would look like an export; the message counts only the files written."""
    from wider.recall.selection import PACKAGE_EMPTY
    p = _proj(tmp_path)
    j = write_records_package(p, "main", add="selection", formats="ris,csv",
                              selection={"preset": "include", "pathways": "researcher"})
    empty = [f for f in j["files"] if f["label"] == PACKAGE_EMPTY]
    assert [f["name"] for f in empty] == ["main_include_by_researcher.ris", "main_include_by_researcher.csv"]
    assert all(f["records"] == 0 for f in empty)
    m = _zip_members(p.root / j["file"])
    assert "main_include_by_researcher.ris" not in m and "main_include_by_researcher.csv" not in m
    assert sorted(m) == ["MANIFEST.txt", "main_audit.csv", "main_include_and_maybe.csv", "main_include_and_maybe.ris",
                         "summary.md"]
    assert j["message"].endswith(f"({len(m)} files)")
    assert f"main_include_by_researcher.ris: {PACKAGE_EMPTY}" in m["summary.md"].decode()


@pytest.mark.parametrize("bad", ["x", "1,a"])
def test_records_must_be_ids(tmp_path, bad):
    p = _proj(tmp_path)
    r = cli(p, "--records", bad)
    assert r.exit_code == 1 and "record ids" in r.output
