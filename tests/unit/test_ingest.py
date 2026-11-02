import json
import re
from pathlib import Path

from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.ingest.importer import add_file, import_all, set_sample, summary
from wider.ingest.ris import parse_ris
from wider.ingest.standardize import standardize
from wider.search.coverage import backfill_benchmark_abstracts, check

RIS_SCOPUS = """TY  - JOUR
AU  - Doe, Jane
AU  - Roe, Rick
TI  - Team meetings after errors
PY  - 2020
T2  - Journal of Things
DO  - 10.1000/abc
AB  - First line of the abstract
      continues here with more words to pass twenty words in total for the thin check ok.
KW  - blame
KW  - meetings
ER  -

TY  - JOUR
TI  - A second record without a DOI
PY  - 2019
AB  - Short.
ER  -
"""

RIS_PROQUEST = """TY  - Scholarly Journals
T1  - Team Meetings After Errors.
AU  - Doe, Jane
JF  - Journal of Things
Y1  - 2020-01-01
DO  - https://doi.org/10.1000/ABC
AB  - Same paper from ProQuest
UR  - https://www.proquest.com/docview/1
ER  -
"""


def test_parse_and_standardize():
    recs = parse_ris(RIS_SCOPUS)
    assert len(recs) == 2 and recs[0]["AU"] == ["Doe, Jane", "Roe, Rick"]
    r = standardize(recs[0], "scopus")
    assert r["doi"] == "10.1000/abc" and r["year"] == 2020 and r["first_author"] == "doe"
    assert r["abstract"].startswith("First line of the abstract continues")
    assert r["keywords"] == "blame; meetings" and r["journal"] == "Journal of Things"
    r2 = standardize(parse_ris(RIS_PROQUEST)[0], "proquest")
    assert r2["doi"] == "10.1000/abc" and r2["title"] == "Team Meetings After Errors" and r2["year"] == 2020
    assert r2["title_norm"] == r["title_norm"]


def test_import_once_and_coverage(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus", "proquest"])
    (tmp_path / "s.ris").write_text(RIS_SCOPUS)
    (tmp_path / "q.ris").write_text(RIS_PROQUEST)
    add_file(p, "scopus", tmp_path / "s.ris")
    add_file(p, "proquest", tmp_path / "q.ris")
    res = import_all(p)
    assert [r["status"] for r in res] == ["imported", "imported"]
    assert import_all(p)[0]["status"] == "already"
    assert {r["platform"]: r["records"] for r in summary(p)} == {"proquest": 1, "scopus": 2}
    p.write_benchmark([
        BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/abc", set="Development"),
        BenchmarkPaper(benchmark_id="B2", title="A second record without a DOI", set="Development"),
        BenchmarkPaper(benchmark_id="B3", title="Not retrieved anywhere at all", set="Validation"),
    ])
    cov = check(p)
    assert cov["validation_included"] is False
    sc = cov["scopes"]["platform:scopus"]["Development"]
    assert sc["found"] == 2 and {r["benchmark_id"]: r["method"] for r in sc["rows"]} == {"B1": "doi", "B2": "title"}
    assert cov["scopes"]["platform:proquest"]["Development"]["found"] == 1
    cov = check(p, include_validation=True, seen_choice="keep")   # an early look asks first (D3, Q1)
    assert cov["scopes"]["platform:scopus"]["Validation"]["found"] == 0
    # uploads marked final unlock the Validation set for coverage and the sanity check alike, with no peek
    from wider.ingest.importer import mark_final
    from wider.ingest.sanity import benchmark_list
    assert [b[0] for b in benchmark_list(p)] == ["B1", "B2"]
    peeks = p.store.get_meta("validation_peeks")
    mark_final(p)
    assert check(p)["validation_included"] is True and [b[0] for b in benchmark_list(p)] == ["B1", "B2", "B3"]
    assert p.store.get_meta("validation_peeks") == peeks
    from wider.construct.benchmark import sync
    sync(p)
    assert backfill_benchmark_abstracts(p, min_words=3) == 1  # B1 from the longer scopus abstract
    row = p.store.one("SELECT abstract_source, abstract_words FROM benchmark_papers WHERE benchmark_id='B1'")
    assert row["abstract_source"] == "corpus" and row["abstract_words"] > 10


def _covered(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus", "proquest"])
    (tmp_path / "s.ris").write_text(RIS_SCOPUS)
    add_file(p, "scopus", tmp_path / "s.ris")
    import_all(p)
    p.write_benchmark([
        BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/abc", set="Development"),
        BenchmarkPaper(benchmark_id="B3", title="Not retrieved anywhere at all", set="Validation"),
    ])
    return p


def test_validation_gating_follows_the_protocol_and_a_peek_is_logged(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core import journal
    from wider.ui.app import create_app

    p = _covered(tmp_path)
    p.config.protocol.identification.validation_after_freeze = False   # gating off: both sets, no peek
    p.save()
    cov = check(Project.load(p.root))
    assert cov["validation_included"] and not cov["validation_peeked"]
    assert set(cov["scopes"]["platform:scopus"]) == {"Development", "Validation"}
    assert p.store.get_meta("validation_peeked_at") is None and journal.changes(p, kind="validation_peek") == []
    # the CLI and the API the Import page calls report both sets, with no hidden-set note and no peek
    r = CliRunner().invoke(cli_app, ["coverage", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "platform:scopus\n  Development: 1 of 1, 95% interval 0.03 to 1.00\n  Validation: 0 of 1" in r.output
    assert "hidden until" not in r.output and "before the query was frozen" not in r.output
    d = TestClient(create_app(Project.load(p.root))).get("/api/coverage").json()
    assert d["validation_included"] is True and d["validation_peeked"] is False   # page note: both sets, not hidden
    assert {s: (v["found"], v["n"]) for s, v in d["scopes"]["platform:scopus"].items()} == \
        {"Development": (1, 1), "Validation": (0, 1)}
    assert p.store.get_meta("validation_peeked_at") is None and journal.changes(p, kind="validation_peek") == []
    p.config.protocol.identification.validation_after_freeze = True
    p.save()
    c = TestClient(create_app(Project.load(p.root)))
    d = c.get("/api/coverage").json()
    assert not d["validation_included"] and not d["validation_peeked"]
    # an early look asks first whether the papers shown count as seen (D3, Q1): nothing shown or recorded without it
    d = c.get("/api/coverage?validation=true").json()
    assert d["choice_needed"] and "The choice is final." in d["notice"] and d["scopes"] == {}
    assert p.store.get_meta("validation_peeked_at") is None and journal.changes(p, kind="validation_peek") == []
    d = c.get("/api/coverage?validation=true&seen_choice=keep").json()
    assert d["validation_included"] and d["validation_peeked"] and d["notice"].startswith("validation set shown before")
    assert p.store.get_meta("validation_peeked_at")
    ch = journal.changes(p)[0]
    assert ch["kind"] == "validation_peek" and not ch["undoable"]
    r = CliRunner().invoke(cli_app, ["coverage", "--validation", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert r.exit_code == 2 and "Choose one: --count-seen or --keep-validation. Nothing was done." in r.output
    r = CliRunner().invoke(cli_app, ["coverage", "--validation", "--keep-validation", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0 and "logged in the history" in r.output
    assert len(journal.changes(p, kind="validation_peek")) == 2


def test_a_changed_match_loses_its_confirmation(tmp_path):
    from wider.construct.benchmark import sync

    p = _covered(tmp_path)
    sync(p)
    check(p)
    conf = lambda: dict(p.store.one("SELECT method, confirmed FROM benchmark_matches WHERE benchmark_id='B1'"))  # noqa: E731
    assert conf() == {"method": "doi", "confirmed": 1}
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Team meeting after the errors", set="Development")])
    sync(p)
    check(p)
    assert conf() == {"method": "fuzzy", "confirmed": 0}   # the DOI match's confirmation does not carry over
    assert backfill_benchmark_abstracts(p, min_words=3) == 0


def test_hidden_validation_set_is_noted_until_the_freeze(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.construct import artifacts
    from wider.ui.app import create_app

    p = _covered(tmp_path)   # gating on (the default), nothing frozen
    note = ("validation set hidden until the synonyms artifact is frozen or the uploads are marked final "
            "(wider construct freeze synonyms N, or wider import final)")
    cov = lambda: CliRunner().invoke(cli_app, ["coverage", "--dir", str(p.root)], env={"COLUMNS": "200"})  # noqa: E731
    r = cov()
    assert r.exit_code == 0 and note in r.output
    assert "platform:scopus\n  Development: 1 of 1, 95% interval 0.03 to 1.00\n" in r.output and "Validation" not in r.output
    api = lambda: TestClient(create_app(Project.load(p.root))).get("/api/coverage").json()  # noqa: E731
    d = api()   # the Import page picks its note from these two flags (app.js showCoverage)
    assert (d["validation_included"], d["validation_peeked"], d["validation_unlocked"]) == (False, False, False)
    assert set(d["scopes"]["platform:scopus"]) == {"Development"}
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["meeting*"]}],
                                               "logic": {"and": ["a"]}}, origin="manual", status="frozen")
    r = cov()
    assert r.exit_code == 0 and "hidden until" not in r.output
    assert "platform:scopus\n  Development: 1 of 1, 95% interval 0.03 to 1.00\n  Validation: 0 of 1" in r.output
    d = api()
    assert (d["validation_included"], d["validation_peeked"], d["validation_unlocked"]) == (True, False, True)
    assert set(d["scopes"]["platform:scopus"]) == {"Development", "Validation"}
    assert p.store.get_meta("validation_peeked_at") is None
    # the page's wording for those flags
    js = (Path(__file__).resolve().parents[2] / "wider" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert ('d.validation_included ? "Development and validation sets." '
            ': "Validation set hidden until the query is frozen or the uploads are marked final."') in js


def test_a_fuzzy_match_that_becomes_a_doi_match_is_confirmed(tmp_path):
    from wider.construct.benchmark import sync

    p = _covered(tmp_path)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Team meeting after the errors", set="Development")])
    sync(p)
    check(p)
    conf = lambda: dict(p.store.one(  # noqa: E731
        "SELECT method, record_ref, confirmed FROM benchmark_matches WHERE benchmark_id='B1' AND scope='platform:scopus'"))
    first = conf()
    assert first["method"] == "fuzzy" and first["confirmed"] == 0 and first["record_ref"] is not None
    assert backfill_benchmark_abstracts(p, min_words=3) == 0   # an unconfirmed match lends no abstract
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Team meeting after the errors", doi="10.1000/abc",
                                      set="Development")])
    sync(p)
    check(p)
    assert conf() == {"method": "doi", "record_ref": first["record_ref"], "confirmed": 1}   # same record, now confirmed
    assert p.store.one("SELECT COUNT(*) AS n FROM benchmark_matches WHERE benchmark_id='B1'")["n"] == 1
    assert backfill_benchmark_abstracts(p, min_words=3) == 1


# ----------------------------------------------------------------------------- files kept out of deduplication (D5)
WORDS = ("alder birch cedar dahlia elm fern gorse hazel iris juniper kale lilac maple nettle oak poppy quince rowan "
         "sage thyme ulmus violet willow yarrow zinnia acorn bramble clover daisy ember").split()


def _ris(ids) -> str:
    """Synthetic records with no title word in common; the same id gives the same record (an exact duplicate)."""
    out = []
    for i in ids:
        w = WORDS[3 * i % 30], WORDS[(3 * i + 1) % 30], WORDS[(3 * i + 2) % 30]
        out.append(f"TY  - JOUR\nAU  - Author{i}, A\nTI  - {w[0]} {w[1]} {w[2]}\nPY  - {2000 + i}\nT2  - Journal {i}\n"
                   f"DO  - 10.1000/x{i}\nER  -\n")
    return "\n".join(out)


def _two_files(tmp_path, name="r"):
    """Two Scopus files: 10 records (record 0 twice) and 4 records (records 1 and 3 again, and 9 only here)."""
    d = tmp_path / f"{name}_in"
    d.mkdir()
    p = Project.init(tmp_path / name, platforms=["scopus"])
    (d / "a.ris").write_text(_ris([0, 1, 2, 3, 4, 5, 6, 7, 8, 0]))
    (d / "b.ris").write_text(_ris([1, 3, 9, 9]))
    add_file(p, "scopus", d / "a.ris")
    add_file(p, "scopus", d / "b.ris")
    import_all(p)
    return p


def _cli(*args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    return CliRunner().invoke(cli_app, [str(a) for a in args], env={"COLUMNS": "250"})


def test_files_can_be_kept_out_of_dedup(tmp_path):
    import pytest
    from fastapi.testclient import TestClient

    from wider.core import journal
    from wider.dedup.engine import raw_count, run
    from wider.ingest.importer import list_sources, set_included
    from wider.ui.app import create_app

    p = _two_files(tmp_path)
    root = p.root
    assert [(r["source_id"], r["file"], r["included"]) for r in list_sources(p)["sources"]] == [
        (1, "a.ris", True), (2, "b.ris", True)]
    p.close()
    # excluded by its id (CLI): the listing says "no" under "enters dedup", one undoable source_included change
    r = _cli("import", "exclude", "2", "--dir", root)
    assert r.exit_code == 0 and "file 2 (b.ris) is kept out of deduplication" in r.output, r.output
    r = _cli("import", "list", "--dir", root)
    assert "enters dedup" in r.output and [ln.split("│")[-3].strip() for ln in r.output.splitlines() if "a.ris" in ln or "b.ris" in ln] == ["yes", "no"]
    p = Project.load(root)
    ch = journal.changes(p, "source_included")
    assert [(c["summary"], c["kind_label"], c["undoable"]) for c in ch] == [
        ("file 2 (b.ris) kept out of deduplication", "file in deduplication", True)]
    assert set_included(p, "2", False)["already"] is True and len(journal.changes(p, "source_included")) == 1
    # deduplication reads only the other file: raw_count and load_records agree, and record 9 (only in b.ris) is absent
    assert raw_count(p) == 10
    run(p, write_exports=False)
    titles = {r["title"] for r in p.store.query("SELECT title FROM records")}
    assert len(titles) == 9 and "clover daisy ember" not in titles   # record 9
    # undo reverses it, then a run brings record 9 in
    journal.undo(p, ch[0]["change_id"], author="ana", other=True)
    assert list_sources(p)["sources"][1]["included"] is True and raw_count(p) == 14
    run(p, write_exports=False)
    only_b = p.store.scalar("SELECT record_id FROM records WHERE title = 'clover daisy ember'")
    assert only_b is not None
    # a screening decision on the record only b.ris brought: excluding the file is refused, naming the table
    p.store.execute("INSERT INTO screen_decisions(protocol,record_id,decision,decided_at) VALUES('p1',?,'include','t')",
                    (only_b,))
    with pytest.raises(ValueError, match=r"later stages hold rows on records only this file brought \(1 screening decisions\)"):
        set_included(p, 2, False)
    c = TestClient(create_app(p))
    res = c.post("/api/import/sources/2/included", json={"included": False})
    assert res.status_code == 409 and "1 screening decisions" in res.json()["detail"]
    assert c.post("/api/import/sources/99/included", json={"included": False}).status_code == 404
    p.close()
    r = _cli("import", "exclude", "b.ris", "--dir", root)
    assert r.exit_code == 1 and "1 screening decisions" in r.output and "Nothing was changed" in r.output
    p = Project.load(root)
    assert list_sources(p)["sources"][1]["included"] is True and len(journal.changes(p, "source_included")) == 1
    # a.ris holds no record alone that a later stage uses: the page's switch passes and is journalled the same way
    res = TestClient(create_app(p)).post("/api/import/sources/1/included", json={"included": False})
    assert res.status_code == 200 and res.json()["included"] is False and res.json()["change_id"]
    assert journal.changes(p, "source_included")[0]["summary"] == "file 1 (a.ris) kept out of deduplication"


def test_include_by_name_only_when_unique(tmp_path):
    from fastapi.testclient import TestClient

    from tests.unit.test_backends_js_node import ESC, PAGE, out, run_node
    from tests.unit.test_backends_js_node import STATIC as STATIC_DIR
    from tests.unit.test_query_roles import APP_JS, js_function
    from wider.core import journal
    from wider.ui.app import create_app

    # two exports both called scopus.csv (a saved name is never reused within a platform folder, so the second sits
    # under wos/): synthetic Scopus CSV rows, the second file one row shorter
    src = (Path(__file__).parents[1] / "fixtures" / "imports" / "scopus.csv").read_text(encoding="utf-8")
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    root, name = p.root, "scopus.csv"
    for sub, text in (("a", src), ("b", "".join(src.splitlines(keepends=True)[:-1]))):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / name).write_text(text, encoding="utf-8")
    add_file(p, "scopus", tmp_path / "a" / name)
    add_file(p, "wos", tmp_path / "b" / name)
    import_all(p)
    p.close()
    r = _cli("import", "exclude", name, "--dir", root)
    assert r.exit_code == 1, r.output
    flat = " ".join(r.output.split())
    assert f"2 imported files are called {name}" in flat and "id 1: scopus, 6 records" in flat and "id 2: wos, 5 records" in flat
    assert "Give the file's id instead." in flat
    p = Project.load(root)
    assert journal.changes(p, "source_included") == [] and p.store.scalar("SELECT MIN(included) FROM sources") == 1
    # a file saved under inputs/ but not imported: the page lists it without a checkbox
    (p.path("inputs", "scopus") / "late.ris").write_text(_ris([12]))
    d = TestClient(create_app(p)).get("/api/import").json()
    p.close()
    rows = {f["file"]: f for f in d["files"]}
    assert rows["late.ris"] == {"platform": "scopus", "file": "late.ris", "imported": False}
    assert sorted((f["source_id"], f["included"]) for f in d["files"] if f["imported"]) == [(1, True), (2, True)]
    next(f for f in d["files"] if f.get("source_id") == 2)["included"] = False
    fn = js_function(APP_JS, "async function loadImport()")
    posted = {"source_id": 2, "file": name, "included": True, "already": False, "change_id": 5}
    res = run_node(f"{ESC}\n{PAGE}\nreplies['/api/import'] = {json.dumps(d)};\n"
                   f"replies['/api/import/sources/2/included'] = {json.dumps(posted)};\n{fn}\n"
                   # import.js draws the files table (filesTable, ui-redesign D32 F8)
                   f"{(STATIC_DIR / 'import.js').read_text(encoding='utf-8')}\nwindow.filesTable = filesTable;\nawait loadImport();\n"
                   "const before = $('#imp-files').innerHTML;\n"
                   "const boxes = [...$('#imp-files').querySelectorAll('[data-source]')];\n"
                   "boxes[1].checked = true; await boxes[1].onchange(); await flush();\n"
                   + out("{ before, ids: boxes.map((b) => b.dataset.source), calls, msg: $('#imp-msg').textContent }"))
    trs = re.findall(r"<tr>.*?</tr>", res["before"])
    assert "<th>Enters deduplication</th>" in trs[0]
    by_file = {re.search(r"<td>[^<]*</td><td>([^<]*)</td>", t).group(1): t for t in trs[1:] if "file(s)" not in t}
    assert "checkbox" not in by_file["late.ris"]
    assert sum("checkbox" in t for t in trs) == 2 and res["ids"] == ["1", "2"]
    assert 'data-source="1" checked' in res["before"] and 'data-source="2" aria-label' in res["before"]
    assert ["/api/import/sources/2/included", "POST", {"included": True}] in res["calls"]
    assert res["msg"].startswith(f"File 2 ({name}) enters deduplication")


def _svg_text(svg):
    # the diagram wraps long lines over several text elements; join them back
    return " ".join(t.strip() for t in re.findall(r">([^<>]+)<", svg) if t.strip())


def test_excluded_file_shown_as_not_used(tmp_path):
    import zipfile

    from wider.dedup.engine import prisma_rows, run
    from wider.ingest.importer import set_included
    from wider.recall.outputs import prisma_counts, write_package, write_prisma

    # nothing excluded: the rows, the diagram and the manifest are those of today, with every file included
    q = _two_files(tmp_path, "plain")
    run(q, write_exports=False)
    assert [r[0] for r in prisma_rows(q)] == ["Scopus records retrieved", "Total records before deduplication",
                                              "Duplicate records removed before screening",
                                              "Records remaining for title and abstract screening"]
    c = write_prisma(q)
    assert c["excluded_file_records"] == 0 and c["duplicates_removed"] == c["identified"] - c["unique"] == 4
    assert "file excluded" not in c["svg"]
    z = zipfile.ZipFile(q.root / write_package(q)["path"])
    m = json.loads(z.read("manifest.json"))
    assert [(f["file"], f["records"], f["included"]) for f in m["imported_files"]] == [("a.ris", 10, True), ("b.ris", 4, True)]
    q.close()
    # Scopus files of 10 and 4 records, the second excluded, then deduplication
    p = _two_files(tmp_path)
    set_included(p, 2, False, "ana")
    run(p, write_exports=False)
    rows = prisma_rows(p)
    assert rows == [["Scopus records retrieved", 14], ["Records not used (file excluded)", 4],
                    ["Total records before deduplication", 10], ["Duplicate records removed before screening", 1],
                    ["Records remaining for title and abstract screening", 9]]
    assert rows[0][1] - rows[1][1] - rows[3][1] == rows[4][1]
    r = _cli("dedup", "prisma", "--dir", p.root)
    assert r.exit_code == 0 and "Records not used (file excluded)" in r.output and "│ 4 " in r.output
    # the diagram: removed before screening as a file excluded, never as duplicates
    c = prisma_counts(p)
    assert (c["identified"], c["excluded_file_records"], c["duplicates_removed"], c["unique"]) == (14, 4, 1, 9)
    svg = _svg_text(write_prisma(p)["svg"])
    assert "Records removed before screening: file excluded (n = 4)" in svg and "Duplicate records removed (n = 1)" in svg
    r = _cli("export", "prisma", "--dir", p.root)
    assert r.exit_code == 0 and "14 identified, 1 duplicates removed" in r.output, r.output
    # the package lists both files, the second not included
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    m = json.loads(z.read("manifest.json"))
    assert [(f["source_id"], f["file"], f["records"], f["included"]) for f in m["imported_files"]] == [
        (1, "a.ris", 10, True), (2, "b.ris", 4, False)]
    assert "Records removed before screening: file excluded (n = 4)" in _svg_text(z.read("prisma_2020.svg").decode())


def _sampled(tmp_path, name="s", n=1249):
    """A project whose only file is a synthetic OpenAlex export of ``n`` records (file 1)."""
    d = tmp_path / f"{name}_in"
    d.mkdir()
    p = Project.init(tmp_path / name, platforms=["scopus"])
    (d / "oa.ris").write_text(_ris(range(n)))
    add_file(p, "openalex", d / "oa.ris")
    import_all(p)
    return p


def test_mark_export_as_sample(tmp_path):
    """Scenario "Mark a sample on the CLI and on the page": the mark from the CLI and from the route both show as
    sample_of in the listings, History holds one "file marked as a sample" change that undo reverses, clearing is
    another change, and the PRISMA rows still count the records imported."""
    from fastapi.testclient import TestClient

    from wider.core import journal
    from wider.dedup.engine import prisma_rows
    from wider.ingest.importer import list_sources
    from wider.ui.app import create_app

    p = _sampled(tmp_path)
    root = p.root
    before = prisma_rows(p)
    assert ["openalex records retrieved", 1249] in before and list_sources(p)["sources"][0]["sample_of"] is None
    p.close()
    r = _cli("import", "sample", "1", "--of", "650000", "--dir", root)
    assert r.exit_code == 0 and "file 1 (oa.ris) marked a random sample of 650,000 results" in r.output, r.output
    r = _cli("import", "list", "--json", "--dir", root)
    assert json.loads(r.output)["sources"][0]["sample_of"] == 650000
    r = _cli("import", "list", "--dir", root)
    assert "sample of" in r.output
    assert [ln.split("│")[-2].strip() for ln in r.output.splitlines() if "oa.ris" in ln] == ["650000"]
    p = Project.load(root)
    c = TestClient(create_app(p))
    assert c.get("/api/import/sources").json()["sources"][0]["sample_of"] == 650000
    assert [f["sample_of"] for f in c.get("/api/import").json()["files"]] == [650000]
    assert prisma_rows(p) == before
    ch = journal.changes(p, "source_sample")
    assert [(x["summary"], x["kind_label"], x["undoable"]) for x in ch] == [
        ("file 1 (oa.ris) marked a random sample of 650,000 results", "file marked as a sample", True)]
    journal.undo(p, ch[0]["change_id"], author="ana", other=True)
    assert list_sources(p)["sources"][0]["sample_of"] is None
    p.close()
    # the same file in a second project, marked through the page's route, then cleared with --clear
    q = _sampled(tmp_path, "t")
    c = TestClient(create_app(q))
    res = c.post("/api/import/sources/1/sample", json={"of": 650000})
    assert res.status_code == 200 and res.json()["sample_of"] == 650000 and res.json()["change_id"]
    assert c.get("/api/import/sources").json()["sources"][0]["sample_of"] == 650000
    assert c.post("/api/import/sources/1/sample", json={"of": 650000}).json()["already"] is True
    qroot = q.root
    q.close()
    r = _cli("import", "sample", "oa.ris", "--clear", "--dir", qroot)
    assert r.exit_code == 0 and "file 1 (oa.ris) marked a complete export" in r.output, r.output
    q = Project.load(qroot)
    assert list_sources(q)["sources"][0]["sample_of"] is None
    assert [x["summary"] for x in journal.changes(q, "source_sample")] == [
        "file 1 (oa.ris) marked a complete export", "file 1 (oa.ris) marked a random sample of 650,000 results"]
    q.close()


def test_sample_mark_refusals(tmp_path):
    """Scenario "A sample that is not smaller than its result is refused": N not above the records, 0, a shared name,
    an unknown id and a same-N sample holding N records or more are refused on the CLI (exit 1) and the route (400 or
    404), and nothing changes."""
    from fastapi.testclient import TestClient

    from wider.core import journal
    from wider.ui.app import create_app

    p = _sampled(tmp_path)
    root = p.root
    p.close()
    r = _cli("import", "sample", "1", "--of", "1249", "--dir", root)
    assert r.exit_code == 1 and "holds 1,249 records" in " ".join(r.output.split()), r.output
    assert "Nothing was changed" in " ".join(r.output.split())
    assert _cli("import", "sample", "99", "--of", "5000", "--dir", root).exit_code == 1
    assert _cli("import", "sample", "1", "--dir", root).exit_code == 1   # neither --of nor --clear
    p = Project.load(root)
    c = TestClient(create_app(p))
    res = c.post("/api/import/sources/1/sample", json={"of": 0})
    assert res.status_code == 400 and "Nothing was changed" in res.json()["detail"]
    res = c.post("/api/import/sources/1/sample", json={"of": 1249})
    assert res.status_code == 400 and "holds 1,249 records" in res.json()["detail"]
    assert c.post("/api/import/sources/99/sample", json={"of": 5000}).status_code == 404
    # a name two imported files share: the CLI lists both with their ids
    d = tmp_path / "dup"
    d.mkdir()
    (d / "oa.ris").write_text(_ris(range(2000, 2600)))
    add_file(p, "wos", d / "oa.ris")
    (d / "x").mkdir()
    (d / "x" / "third.ris").write_text(_ris(range(3000, 4000)))
    add_file(p, "openalex", d / "x" / "third.ris")
    (d / "x" / "second.ris").write_text(_ris(range(5000, 5600)))
    add_file(p, "openalex", d / "x" / "second.ris")
    import_all(p)
    ids = {r["file_name"]: r["source_id"] for r in p.store.query("SELECT source_id, file_name FROM sources")}
    p.close()
    r = _cli("import", "sample", "oa.ris", "--of", "9000", "--dir", root)
    flat = " ".join(r.output.split())
    assert r.exit_code == 1 and "2 imported files are called oa.ris" in flat and "id 1: openalex" in flat, flat
    # same-N pooling: 600 records marked a sample of 1,500; a third file of 1,000 with the same N would make 1,600
    assert _cli("import", "sample", str(ids["second.ris"]), "--of", "1500", "--dir", root).exit_code == 0
    r = _cli("import", "sample", str(ids["third.ris"]), "--of", "1500", "--dir", root)
    flat = " ".join(r.output.split())
    assert r.exit_code == 1 and "would hold 1,600 records" in flat, flat
    p = Project.load(root)
    marks = {r["file_name"]: r["sample_of"] for r in p.store.query("SELECT file_name, sample_of FROM sources")}
    assert marks == {"oa.ris": None, "third.ris": None, "second.ris": 1500}, marks
    assert len(journal.changes(p, "source_sample")) == 1
    # a number past any database is refused as a bad N (400), not as an unknown id
    res = TestClient(create_app(p)).post(f"/api/import/sources/{ids['third.ris']}/sample", json={"of": 10 ** 20})
    assert res.status_code == 400 and "more than any database holds" in res.json()["detail"], res.json()
    # undo cannot bring back a mark set_sample would refuse: clear second, mark third 1,500, undo the clear
    cleared = set_sample(p, ids["second.ris"], None)["change_id"]
    set_sample(p, ids["third.ris"], 1500)
    ok, why = journal._check(p, dict(p.store.one("SELECT * FROM history WHERE change_id=?", (cleared,))),
                             json.loads(p.store.scalar("SELECT items FROM history WHERE change_id=?", (cleared,))))
    assert not ok and "would hold 1,600 records" in why, why
    p.close()


def test_schema_12_migration_reads_files_as_complete(tmp_path):
    """Task 3.1: a project made before schema 12 (no sources.sample_of) opens with every file a complete export."""
    from wider.core.store import SCHEMA_VERSION
    from wider.ingest.importer import list_sources

    p = _two_files(tmp_path)
    root = p.root
    conn = p.store.conn
    conn.execute("ALTER TABLE sources DROP COLUMN sample_of")
    conn.execute("UPDATE meta SET value='11' WHERE key='schema_version'")
    conn.commit()
    p.close()
    p = Project.load(root)
    assert SCHEMA_VERSION == 12 and p.store.get_meta("schema_version") == "12"
    assert [r["sample_of"] for r in list_sources(p)["sources"]] == [None, None]
    p.close()
