"""Import formats (CSV / TSV / XLSX / BibTeX), column profiles, schema v3 and export sanity checks.

All fixtures under tests/fixtures/imports/ are synthetic (a fictitious topic: remote work and team productivity).
"""
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app
from wider.construct import artifacts
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.core.store import SCHEMA_VERSION, Store
from wider.ingest.bibtex_reader import parse_bibtex, parse_bibtex_builtin
from wider.ingest.csv_reader import read_table, sniff_delimiter
from wider.ingest.importer import add_file, import_all, input_files, summary
from wider.ingest.readers import detect_format, read_file, read_standard, sniff_text_format
from wider.ingest.sanity import check_all, check_file, export_cap, term_pattern, vocabulary_overlap
from wider.ingest.standardize import detect_profile, profile_for, standardize
from wider.ui.app import create_app

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "imports"
runner = CliRunner(env={"COLUMNS": "200"})  # wide terminal so rich tables do not fold file names

SYN = {"blocks": [{"key": "work", "name": "Remote work", "terms": ["remote work", "telework*", "hybrid"]},
                  {"key": "outcome", "name": "Outcome", "terms": ["productiv*", "collaboration", "output"]}],
       "logic": {"and": ["work", "outcome"]}}

BENCH = [
    BenchmarkPaper(benchmark_id="B1", title="Remote work and team productivity in distributed software teams",
                   doi="10.1000/rw.001", set="Development"),
    BenchmarkPaper(benchmark_id="B2", title="Hybrid schedules and collaboration quality", set="Development"),
    BenchmarkPaper(benchmark_id="B3", title="Something kept for validation only", set="Validation"),
]


def _project(tmp_path, platforms=("proquest", "ebsco", "scopus", "wos")) -> Project:
    p = Project.init(tmp_path / "r", platforms=list(platforms))
    p.write_benchmark(BENCH)
    return p


def _bib(tmp_path) -> Path:
    """The BibTeX fixture is stored as .txt (the privacy guard refuses .bib); copy it to a real .bib."""
    dst = tmp_path / "scopus.bib"
    shutil.copy(FIX / "scopus.bib.txt", dst)
    return dst


# ----- readers -----
def test_csv_sniffing_and_bom():
    assert sniff_delimiter("a,b,c\n1,2,3\n") == ","
    assert sniff_delimiter("a;b;c\n1;2;3\n") == ";"
    assert sniff_delimiter("a\tb\tc\n1\t2\t3\n") == "\t"
    headers, rows = read_table(FIX / "ebsco.csv")
    assert headers[0] == "Article Title" and len(rows) == 4  # BOM stripped, semicolons sniffed
    assert rows[0]["DOI"] == ["10.1000/rw.002"]
    headers, rows = read_table(FIX / "proquest.csv")
    assert headers[0] == "Title" and len(rows) == 4
    headers, rows = read_table(FIX / "scopus.csv")
    assert "DOI" not in rows[4] and rows[4]["Title"]  # empty cells are dropped


def test_format_detection(tmp_path):
    assert detect_format(FIX / "proquest.csv") == "csv"
    assert detect_format(FIX / "wos.txt") == "tsv"
    assert detect_format(FIX / "scopus.bib.txt") == "bib"
    assert detect_format(_bib(tmp_path)) == "bib"
    assert sniff_text_format("TY  - JOUR\nTI  - x\nER  -\n") == "ris"
    assert sniff_text_format("@article{k,\n title={x}\n}\n") == "bib"
    assert sniff_text_format("Title,Abstract\nx,y\n") == "csv"
    (tmp_path / "x.ris").write_text("TY  - JOUR\nTI  - Only title\nER  -\n")
    assert detect_format(tmp_path / "x.ris") == "ris"
    with pytest.raises(ValueError):
        read_file(tmp_path / "x.ris", fmt="docx")


def test_builtin_bibtex_parser_handles_braces_quotes_strings_and_concat():
    text = (FIX / "scopus.bib.txt").read_text(encoding="utf-8")
    recs = parse_bibtex_builtin(text)
    assert len(recs) == 4  # @comment and @string are not entries
    r = recs[0]
    assert r["ENTRYTYPE"] == ["article"] and r["ID"] == ["rw1"]
    assert r["title"] == ["Remote work and team productivity in distributed software teams"]  # inner braces removed
    assert r["author"] == ["Lee, Ana", "Kim, Bo"] and r["doi"] == ["10.1000/rw.001"]
    r4 = recs[3]
    assert r4["year"] == ["2020"] and r4["doi"] == ["10.1000/rw.004"]
    assert r4["journal"] == ["Journal of Work Studies (special issue)"]  # @string + '#' concatenation
    assert parse_bibtex(text, prefer_builtin=True) == recs
    # malformed tail does not lose earlier entries
    assert len(parse_bibtex_builtin(text + "\n@article{broken,\n title={unclosed")) >= 4


def test_xlsx_round_trip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Publication Type", "Authors", "Article Title", "Source Title", "Abstract", "DOI", "Publication Year",
               "UT (Unique WOS ID)"])
    ws.append(["J", "Lee, Ana; Kim, Bo", "Remote work and team productivity in distributed software teams",
               "Journal of Work Studies", "We study remote work and team productivity.", "10.1000/rw.001", 2021,
               "WOS:1"])
    ws.append(["J", "Novak, Ivo", "Hybrid schedules and collaboration quality", "Organization Quarterly", None,
               None, 2022, "WOS:2"])
    f = tmp_path / "wos.xlsx"
    wb.save(f)
    fmt, headers, recs = read_file(f)
    assert fmt == "xlsx" and "Article Title" in headers and len(recs) == 2
    assert recs[0]["Publication Year"] == ["2021"] and "Abstract" not in recs[1]
    assert detect_profile(headers, "xlsx") == ("wos", "xlsx")
    meta, rows = read_standard(f, "wos")
    assert meta["profile"] == "wos_xlsx" and rows[0]["year"] == 2021 and rows[0]["first_author"] == "lee"
    assert rows[0]["native_id"] == "WOS:1"


def test_xlsx_missing_dependency_message(tmp_path, monkeypatch):
    import builtins

    from wider.ingest.xlsx_reader import MissingDependency, read_xlsx_table

    real_import = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("openpyxl"):
            raise ImportError("no openpyxl")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)
    f = tmp_path / "x.xlsx"
    f.write_bytes(b"PK\x03\x04")
    with pytest.raises(MissingDependency, match="wider\\[import\\]"):
        read_xlsx_table(f)


# ----- profiles and standardisation -----
def test_detect_profile_per_platform():
    assert detect_profile(["Title", "Abstract", "StoreId", "year"], "csv") == ("proquest", "csv")
    assert detect_profile(["Article Title", "Abstract", "Accession Number"], "csv") == ("ebsco", "csv")
    assert detect_profile(["Authors", "Title", "Source title", "EID"], "csv") == ("scopus", "csv")
    assert detect_profile(["PT", "AU", "TI", "AB", "UT"], "tsv") == ("wos", "tsv")
    assert detect_profile(["title", "author", "year"], "bib") == ("any", "bib")
    assert detect_profile(["Something", "Else"], "csv") == (None, None)
    assert profile_for(["Title", "Year"], "csv")["name"] == "generic"
    assert profile_for(["title", "author"], "bib")["name"] == "bibtex"


@pytest.mark.parametrize("name,platform,profile,n,doi0", [
    ("proquest.csv", "proquest", "proquest_csv", 4, "10.1000/rw.001"),
    ("ebsco.csv", "ebsco", "ebsco_csv", 4, "10.1000/rw.002"),
    ("scopus.csv", "scopus", "scopus_csv", 6, "10.1000/rw.001"),
    ("wos.txt", "wos", "wos_tsv", 4, "10.1000/rw.003"),
])
def test_read_standard_tabular(name, platform, profile, n, doi0):
    meta, rows = read_standard(FIX / name, platform)
    assert meta["profile"] == profile and meta["detected_platform"] == platform and meta["mismatch"] is False
    assert len(rows) == n and rows[0]["doi"] == doi0
    assert all(r["title"] and r["year"] and r["first_author"] for r in rows)
    assert rows[0]["abstract"] and rows[0]["journal"]


def test_read_standard_bibtex_and_mismatch(tmp_path):
    meta, rows = read_standard(_bib(tmp_path), "scopus")
    assert meta["format"] == "bib" and meta["profile"] == "bibtex" and meta["mismatch"] is False
    assert len(rows) == 4 and rows[0]["doi"] == "10.1000/rw.001" and rows[0]["first_author"] == "lee"
    assert rows[0]["authors"].startswith("Lee, Ana") and rows[3]["year"] == 2020
    # a scopus-shaped CSV filed under another platform is flagged, not refused
    meta, rows = read_standard(FIX / "scopus.csv", "ebsco")
    assert meta["detected_platform"] == "scopus" and meta["mismatch"] is True and len(rows) == 6


def test_standardize_column_record_pages_and_multi_values():
    prof = profile_for(["Authors", "Title", "Source title", "EID"], "csv")
    rec = {"Authors": ["Lee, Ana; Kim, Bo"], "Title": ["A title."], "Source title": ["J"], "EID": ["e1"],
           "Page start": ["10"], "Page end": ["20"], "Author Keywords": ["alpha; beta"], "Year": ["2020"],
           "Link": ["https://example.org/x?doi=10.1000/zz.1"]}
    r = standardize(rec, "scopus", prof)
    assert r["first_author"] == "lee" and r["pages"] == "10-20" and r["keywords"] == "alpha; beta"
    assert r["title"] == "A title" and r["year"] == 2020 and r["doi"] == "10.1000/zz.1"


# ----- importer and store -----
def test_import_all_mixed_formats_records_format(tmp_path):
    p = _project(tmp_path)
    add_file(p, "proquest", FIX / "proquest.csv")
    add_file(p, "ebsco", FIX / "ebsco.csv")
    add_file(p, "wos", FIX / "wos.txt")
    add_file(p, "scopus", _bib(tmp_path))
    # legacy layout keeps working
    legacy = p.path("inputs", "ris", "scopus")
    legacy.mkdir(parents=True)
    (legacy / "old.ris").write_text("TY  - JOUR\nTI  - Office redesign and employee wellbeing\nPY  - 2019\n"
                                    "DO  - 10.1000/rw.006\nER  -\n")
    assert sorted(pl for pl, _ in input_files(p)) == ["ebsco", "proquest", "scopus", "scopus", "wos"]
    res = import_all(p)
    assert {r["status"] for r in res} == {"imported"}
    fmts = {r["file_name"]: (r["format"], r["profile"]) for r in p.store.query("SELECT * FROM sources")}
    assert fmts["proquest.csv"] == ("csv", "proquest_csv") and fmts["wos.txt"] == ("tsv", "wos_tsv")
    assert fmts["scopus.bib"] == ("bib", "bibtex") and fmts["old.ris"] == ("ris", "ris:scopus")
    assert {r["platform"]: r["records"] for r in summary(p)} == {"ebsco": 4, "proquest": 4, "scopus": 5, "wos": 4}
    assert all(r["status"] == "already" for r in import_all(p))
    (p.path("inputs", "wos") / "broken.xlsx").write_bytes(b"not a workbook")
    res = import_all(p)
    assert [r["status"] for r in res if r["file"] == "broken.xlsx"] == ["error"]


def test_store_migrates_v2_sources_to_v3(tmp_path):
    db = tmp_path / "store.sqlite"
    Store(db).close()
    con = sqlite3.connect(db)
    con.executescript("""
    DROP TABLE sources;
    CREATE TABLE sources (
      source_id INTEGER PRIMARY KEY, platform TEXT NOT NULL, file_name TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
      n_records INTEGER NOT NULL, imported_at TEXT NOT NULL);
    INSERT INTO sources(platform,file_name,sha256,n_records,imported_at) VALUES ('scopus','a.ris','h1',3,'now');
    UPDATE meta SET value='2' WHERE key='schema_version';
    """)
    con.commit()
    con.close()
    st = Store(db)
    assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert {"format", "profile"} <= set(st.columns("sources"))
    assert st.one("SELECT format FROM sources WHERE sha256='h1'")["format"] == "ris"
    st.close()


# ----- sanity checks -----
def test_term_pattern_and_vocabulary_overlap():
    rows = [{"title": "Remote work and productivity", "abstract": ""},
            {"title": "Teleworking parents", "abstract": "collaboration quality"},
            {"title": "Sourdough bread", "abstract": "fermentation"}]
    vocab = {"version": 1, "status": "approved", "note": None,
             "blocks": {"work": {"role": "required", "patterns": [term_pattern("remote work"), term_pattern("telework*")]},
                        "out": {"role": "required", "patterns": [term_pattern("productiv*"), term_pattern("collaboration")]}}}
    ov = vocabulary_overlap(rows, vocab)
    assert ov["any"] == 0.667 and ov["blocks"] == {"work": 0.667, "out": 0.667} and ov["n"] == 3
    assert vocabulary_overlap(rows, None) is None
    assert term_pattern("") is None and term_pattern('"hybrid schedule"').search("hybrid schedule")


def test_export_cap_from_platforms_yaml():
    assert export_cap("proquest", "csv") == 100 and export_cap("scopus", "csv") == 2000
    assert export_cap("ebsco", "csv") is None and export_cap("unknown", "csv") is None


def test_check_all_verdicts(tmp_path):
    p = _project(tmp_path)
    add_file(p, "proquest", FIX / "proquest.csv")
    add_file(p, "proquest", FIX / "proquest_capped.csv")
    add_file(p, "scopus", FIX / "off_topic.csv")
    add_file(p, "scopus", FIX / "empty.csv")
    add_file(p, "ebsco", FIX / "ebsco.csv")
    shutil.copy(FIX / "ebsco.csv", p.path("inputs", "ebsco", "ebsco_again.csv"))
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="approved")
    rep = check_all(p)
    assert rep["benchmark_n"] == 2 and rep["synonyms"]["status"] == "approved"  # validation set hidden
    v = {(r["platform"], r["file"]): r for r in rep["files"]}
    pq = v[("proquest", "proquest.csv")]
    assert pq["verdict"] == "ok" and pq["benchmark_hits"] == 2 and pq["benchmark_found"] == ["B1", "B2"]
    assert pq["vocab_overlap"] == 1.0 and pq["imported"] is False
    cap = v[("proquest", "proquest_capped.csv")]
    assert cap["verdict"] == "capped" and cap["n"] == 100 and "export cap" in cap["message"]
    off = v[("scopus", "off_topic.csv")]
    assert off["verdict"] == "off_topic" and off["benchmark_hits"] == 0 and off["vocab_overlap"] == 0.0
    assert "probably is not the result of your query" in off["message"]
    assert v[("scopus", "empty.csv")]["verdict"] == "empty"
    assert v[("ebsco", "ebsco.csv")]["verdict"] == "ok"
    dup = v[("ebsco", "ebsco_again.csv")]
    assert dup["verdict"] == "duplicate" and dup["duplicate_of"] == "ebsco/ebsco.csv"
    assert rep["counts"] == {"ok": 2, "capped": 1, "off_topic": 1, "empty": 1, "duplicate": 1}
    import_all(p)
    assert check_all(p)["files"][0]["imported"] is True


def test_flagged_files_start_out_of_deduplication(tmp_path):
    """ui-redesign D32 F8 (owner 30 Oct): an off-topic or empty file is imported unticked, with a notice; `wider
    import run` says the same; `wider import include` puts it back."""
    p = _project(tmp_path)
    add_file(p, "proquest", FIX / "proquest.csv")
    add_file(p, "scopus", FIX / "off_topic.csv")
    add_file(p, "scopus", FIX / "empty.csv")
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="approved")
    notices: list[str] = []
    res = {r["file"]: r for r in import_all(p, notices=notices)}
    assert res["off_topic.csv"]["kept_out"] is True and res["proquest.csv"]["kept_out"] is False
    inc = {r["file_name"]: r["included"] for r in p.store.query("SELECT file_name, included FROM sources")}
    assert inc["off_topic.csv"] == 0 and inc["proquest.csv"] == 1
    assert any(n.startswith("off_topic.csv is kept out of deduplication until you include it: ") for n in notices)
    assert not any(n.startswith("proquest.csv") for n in notices)
    if "empty.csv" in inc:   # an empty file that still reads (header only) is kept out too
        assert inc["empty.csv"] == 0
    p.close()
    d = str(p.root)
    sid = next(r["source_id"] for r in res.values() if r["file"] == "off_topic.csv")
    r = runner.invoke(app, ["import", "include", str(sid), "--dir", d])
    assert r.exit_code == 0, r.output


def test_cli_import_run_names_a_kept_out_file(tmp_path):
    p = _project(tmp_path)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="approved")
    add_file(p, "scopus", FIX / "off_topic.csv")
    p.close()
    r = runner.invoke(app, ["import", "run", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "off_topic.csv is kept out of deduplication until you include it" in " ".join(r.output.split())


def test_check_file_without_benchmark_or_synonyms_is_graceful(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    r = check_file(p, "scopus", FIX / "off_topic.csv")
    assert r["verdict"] == "ok" and r["benchmark_hits"] is None and r["vocab_overlap"] is None
    assert "no seed papers and no synonyms yet: topic checks skipped" in r["message"], r["message"]
    # threshold is honoured: cap warning can be switched off
    p.config.protocol.imports.warn_on_export_cap = False
    p.save()
    assert check_file(p, "proquest", FIX / "proquest_capped.csv")["verdict"] == "ok"


def test_import_protocol_is_explained():
    from wider.core.protocol import ReviewProtocol

    rows = {r["path"]: r for r in ReviewProtocol().explain()}
    assert rows["imports.min_benchmark_hits"]["value"] == 1 and rows["imports.min_vocabulary_overlap"]["value"] == 0.5
    assert rows["imports.warn_on_export_cap"]["description"]


# ----- CLI and UI -----
def test_cli_import_add_check_run(tmp_path):
    p = _project(tmp_path)
    p.close()
    d = str(p.root)
    r = runner.invoke(app, ["import", "add", "proquest", str(FIX / "proquest.csv"), "--dir", d])
    assert r.exit_code == 0, r.output
    assert "proquest.csv" in r.output and "ok" in r.output
    r = runner.invoke(app, ["import", "add", "scopus", str(FIX / "off_topic.csv"), "--dir", d])
    assert r.exit_code == 0 and "off_topic" in r.output
    r = runner.invoke(app, ["import", "check", "--dir", d])
    assert r.exit_code == 0 and "off_topic.csv" in r.output and "proquest.csv" in r.output
    r = runner.invoke(app, ["import", "run", "--dir", d])
    assert r.exit_code == 0, r.output
    assert "4 records (csv)" in r.output and "off_topic" in r.output
    r = runner.invoke(app, ["import", "--dir", d])  # bare command still imports
    assert r.exit_code == 0 and "already" in r.output


def test_ui_import_check_and_upload(tmp_path):
    p = _project(tmp_path)
    c = TestClient(create_app(p))
    assert c.get("/api/import/check").json()["n_files"] == 0
    with open(FIX / "proquest.csv", "rb") as f:
        r = c.post("/api/import/upload", data={"platform": "proquest"}, files={"file": ("proquest.csv", f, "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["verdict"] == "ok" and r.json()["file"] == "proquest.csv"
    assert (p.path("inputs", "proquest", "proquest.csv")).exists()
    r = c.post("/api/import/upload", data={"platform": "Bad Key"}, files={"file": ("x.csv", b"Title\n", "text/csv")})
    assert r.status_code == 400
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("x.docx", b"zz", "text/plain")})
    assert r.status_code == 400
    d = c.get("/api/import/check").json()
    assert d["n_files"] == 1 and d["files"][0]["verdict"] == "ok"
    assert c.get("/api/import").json()["root"].endswith("inputs")


def _wos_txt(path: Path, n: int) -> Path:
    head = (FIX / "wos.txt").read_text(encoding="utf-8").splitlines()[0]
    rows = [f"J\tAuthor, A{i}\tA synthetic record number {i}\tJournal of Examples\tword\tAn abstract.\t10.1000/x.{i}\t2020\tWOS:{i}\tArticle"
            for i in range(n)]
    path.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    return path


def test_txt_export_uses_the_txt_cap(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["wos"])
    full = check_file(p, "wos", _wos_txt(tmp_path / "full.txt", 500))
    assert full["format"] == "tsv" and full["n"] == 500 and full["verdict"] == "capped" and full["cap"] == 500
    assert check_file(p, "wos", _wos_txt(tmp_path / "short.txt", 499))["verdict"] == "ok"


def _ris_txt(path: Path, n: int) -> Path:
    """A RIS export saved with a .txt suffix (as some platforms name it)."""
    recs = [f"TY  - JOUR\nTI  - A synthetic record number {i}\nPY  - 2020\nDO  - 10.1000/r.{i}\nER  -\n" for i in range(n)]
    path.write_text("\n".join(recs), encoding="utf-8")
    return path


def test_txt_export_without_a_txt_cap_uses_the_sniffed_format_cap(tmp_path):
    # EBSCO's catalogue entry has a ris cap (200) but no txt cap: a .txt sniffed as RIS uses the ris cap
    assert export_cap("ebsco", "ris", ".txt") == 200 and export_cap("ebsco", "tsv", ".txt") is None
    p = Project.init(tmp_path / "r", platforms=["ebsco"])
    full = check_file(p, "ebsco", _ris_txt(tmp_path / "full.txt", 200))
    assert full["format"] == "ris" and full["n"] == 200 and full["cap"] == 200 and full["verdict"] == "capped"
    assert full["message"] == ("200 records is exactly the ebsco ris export cap; "
                               "the export was probably truncated to one batch")
    short = check_file(p, "ebsco", _ris_txt(tmp_path / "short.txt", 199))
    assert short["format"] == "ris" and short["cap"] == 200 and short["verdict"] == "ok"
    # the Import page's sanity card (GET /api/import/check) reports the same cap
    add_file(p, "ebsco", tmp_path / "full.txt")
    d = TestClient(create_app(p)).get("/api/import/check").json()
    assert [(r["file"], r["format"], r["cap"], r["verdict"]) for r in d["files"]] == [("full.txt", "ris", 200, "capped")]


def test_import_page_marks_files_by_content(tmp_path):
    p = _project(tmp_path)
    add_file(p, "proquest", FIX / "proquest.csv")
    import_all(p)
    c = TestClient(create_app(p))
    src = p.path("inputs", "proquest", "proquest.csv")
    src.rename(src.with_name("renamed.csv"))           # same bytes, new name: still imported
    assert [(f["file"], f["imported"]) for f in c.get("/api/import").json()["files"]] == [("renamed.csv", True)]
    src.with_name("renamed.csv").write_bytes((FIX / "ebsco.csv").read_bytes())   # same name, new bytes: not imported
    assert c.get("/api/import").json()["files"][0]["imported"] is False


def test_upload_never_overwrites(tmp_path):
    p = _project(tmp_path)
    c = TestClient(create_app(p))
    first = (FIX / "proquest.csv").read_bytes()

    def up(name, data):
        return c.post("/api/import/upload", data={"platform": "proquest"}, files={"file": (name, data, "text/csv")}).json()

    assert up("proquest.csv", first)["saved"] is True
    d = up("copy.csv", first)                              # identical bytes under any name: reported, not saved
    assert d["verdict"] == "duplicate" and d["saved"] is False and "identical to proquest/proquest.csv" in d["message"]
    d = up("proquest.csv", (FIX / "proquest_capped.csv").read_bytes())   # a taken name: saved under a free one
    assert d["saved"] and d["renamed"] and d["file"] == "proquest-2.csv"
    assert sorted(f.name for f in p.path("inputs", "proquest").iterdir()) == ["proquest-2.csv", "proquest.csv"]
    assert p.path("inputs", "proquest", "proquest.csv").read_bytes() == first
    assert not any(p.path("inputs", ".upload").iterdir())


def test_cli_import_add_is_all_or_nothing_and_never_overwrites(tmp_path):
    p = _project(tmp_path)
    p.close()
    d = str(p.root)
    r = runner.invoke(app, ["import", "add", "proquest", str(FIX / "proquest.csv"), str(tmp_path / "missing.csv"), "--dir", d])
    assert r.exit_code == 2 and "missing.csv does not exist" in r.output and "nothing copied" in r.output
    assert not p.path("inputs", "proquest").exists() or not any(p.path("inputs", "proquest").iterdir())
    r = runner.invoke(app, ["import", "add", "proquest", str(FIX / "proquest.csv"), str(FIX / "proquest.csv"), "--dir", d])
    assert r.exit_code == 0 and "not copied" in r.output and "duplicate" in r.output
    assert "synonyms: none yet" in r.output
    other = tmp_path / "proquest.csv"
    other.write_bytes((FIX / "proquest_capped.csv").read_bytes())
    artifacts.save_new_version(Project.load(p.root), "synonyms", SYN, origin="manual", status="approved")
    r = runner.invoke(app, ["import", "add", "proquest", str(other), "--dir", d])
    assert r.exit_code == 0 and "inputs/proquest/proquest-2.csv" in r.output
    assert "synonyms: v1 (approved)" in r.output and "benchmark papers checked: 2" in r.output
    assert p.path("inputs", "proquest", "proquest.csv").read_bytes() == (FIX / "proquest.csv").read_bytes()


def test_check_uses_the_latest_draft_synonyms(tmp_path):
    """No approved or frozen synonyms version, only drafts: the vocabulary comes from the newest draft and the file is
    warned "no approved synonyms artifact; using the latest draft"; an approved version replaces the draft and the
    warning."""
    p = _project(tmp_path)
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "x", "name": "X", "terms": ["unrelated phrase"]}],
                                               "logic": {"and": ["x"]}},
                               origin="manual", status="draft")
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="draft")
    r = check_file(p, "proquest", FIX / "proquest.csv")
    assert r["vocab_overlap"] == 1.0   # the newest draft (SYN), not the older one
    assert "no approved synonyms artifact; using the latest draft" in r["warnings"]
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual", status="approved")
    r = check_file(p, "proquest", FIX / "proquest.csv")
    assert r["vocab_overlap"] == 1.0 and "no approved synonyms artifact; using the latest draft" not in r["warnings"]
