"""WP14 part 2: search-strings archive, PRISMA 2020 diagram, methods paragraph, reference exports, replication package."""
import csv
import html
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.providers import fake as fakemod
from wider.recall.outputs import (
    export_references,
    methods_draft,
    prisma_counts,
    prisma_svg,
    read_methods,
    save_methods,
    write_package,
    write_prisma,
)
from wider.screen.protocol import run_protocol
from wider.search.archive import archive, entries
from wider.ui.app import create_app

runner = CliRunner()
SYN = {"blocks": [{"key": "voice", "name": "voice", "terms": ["employee voice", "speak* up"]},
                  {"key": "work", "name": "work", "terms": ["workplace*", "organi?ation*"]}],
       "logic": {"and": ["voice", "work"]}}


@pytest.fixture
def screened(screen_project, monkeypatch):  # noqa: F811
    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    run_protocol(screen_project, "corpus", "main")
    return screen_project


def test_archive_on_request_and_on_freeze(screen_project):  # noqa: F811
    p = screen_project
    with pytest.raises(ValueError):
        archive(p)
    row = artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    r = archive(p)
    assert r["n_new"] == 1 and r["new"][0]["platform"] == "scopus" and r["new"][0]["synonyms_status"] == "draft"
    f = p.root / "exports" / "search_strings" / r["new"][0]["file"]
    assert f.exists() and "employee voice" in f.read_text() and "sha256" in f.read_text()
    assert archive(p)["n_new"] == 0   # unchanged string: not stored twice
    artifacts.set_status(p, "synonyms", row["version"], "approved")
    artifacts.set_status(p, "synonyms", row["version"], "frozen")   # freezing archives (same text: nothing new)
    assert len(entries(p)) == 1
    syn2 = {**SYN, "blocks": SYN["blocks"] + [{"key": "x", "name": "x", "terms": ["silence"]}], "logic": {"and": ["voice", "work", "x"]}}
    row2 = artifacts.save_new_version(p, "synonyms", syn2, origin="manual")
    artifacts.set_status(p, "synonyms", row2["version"], "frozen")
    e = entries(p)
    assert len(e) == 2 and e[-1]["reason"] == "synonyms frozen" and e[-1]["synonyms_status"] == "frozen"


def test_prisma_not_checked(screened, tmp_path):
    """Scenario "Records imported after the last deduplication run" (D32 F4): duplicates removed stay the last run's
    count, the new records are "not yet checked for duplicates", the boxes add up and the counts are provisional."""
    from wider.ingest.importer import add_file, import_all
    from wider.recall.dashboard import provisional_line

    p = screened
    before = prisma_counts(p)
    assert before["not_checked"] == 0
    (tmp_path / "late.ris").write_text("TY  - JOUR\nTI  - A late arrival\nPY  - 2021\nER  -\n"
                                       "TY  - JOUR\nTI  - Another late arrival\nPY  - 2022\nER  -\n")
    add_file(p, "scopus", tmp_path / "late.ris")
    import_all(p)
    c = prisma_counts(p)
    assert c["identified"] == before["identified"] + 2 and c["duplicates_removed"] == before["duplicates_removed"]
    assert c["not_checked"] == 2 and c["provisional"] is True
    assert c["identified"] == c["excluded_file_records"] + c["duplicates_removed"] + c["not_checked"] + c["unique"]
    assert provisional_line(c).startswith("Provisional: 2 records imported since the last deduplication run")
    write_prisma(p)
    svg = (p.root / "exports" / "prisma_2020.svg").read_text()
    assert "Not yet checked for duplicates (n = 2)" in svg
    assert json.loads((p.root / "exports" / "prisma_2020.json").read_text())["not_checked"] == 2


def test_prisma_counts_and_svg(screened):
    p = screened
    c = prisma_counts(p)
    raw = p.store.scalar("SELECT COUNT(*) FROM raw_records")
    unique = p.store.scalar("SELECT COUNT(*) FROM records")
    assert c["protocol"] == "main" and c["identified"] == raw and c["unique"] == unique
    assert c["duplicates_removed"] == raw - unique and c["screened"] == unique and c["not_screened"] == 0
    assert c["excluded"] + c["sought"] == c["screened"] and c["sought"] == c["included_ta"] + c["maybe_ta"]
    r = write_prisma(p)
    svg = (p.root / "exports" / "prisma_2020.svg").read_text()
    assert svg.startswith("<svg") and "Records identified from databases" in svg and "not yet recorded" in svg
    assert f"Records excluded (n = {c['excluded']:,})" in svg and r["path"] == "exports/prisma_2020.svg"
    assert json.loads((p.root / "exports" / "prisma_2020.json").read_text())["unique"] == unique
    with pytest.raises(ValueError):
        prisma_counts(p, "no-such-protocol")
    # other methods (citation chaining) get their own column; text is escaped
    c2 = {**c, "other": [{"key": "citation_chain", "label": "Citation <searching>", "n": 7}], "identified_other": 7}
    s2 = prisma_svg(c2, "A & B")
    assert "Citation &lt;searching&gt; (n = 7)" in s2 and "A &amp; B" in s2


def test_prisma_marks_unique_count_provisional(screened, monkeypatch):
    import wider.dedup.engine as engine
    p = screened
    real = engine.flow_counts
    done = {"to_decide": 0, "decided_since": 0, "unsure": 0}
    monkeypatch.setattr(engine, "flow_counts", lambda project, summary=None: {**real(project, summary), **done})
    summary = json.loads(p.store.get_meta("dedup_summary") or "{}")
    p.store.set_meta("dedup_summary", json.dumps({**summary, "unresolved_manual_component_pairs_retained": 0,
                                                  "uncertain_manual_component_pairs": 0}))
    assert prisma_counts(p)["provisional"] is False
    assert "Provisional" not in write_prisma(p)["svg"] and not json.loads(p.store.get_meta("last_result:recall"))["warnings"]
    p.store.set_meta("dedup_summary", json.dumps({**summary, "unresolved_manual_component_pairs_retained": 0,
                                                  "uncertain_manual_component_pairs": 1}))
    c = prisma_counts(p)
    assert c["provisional"] is True
    assert "Provisional: duplicate pairs" in write_prisma(p)["svg"]
    # the pairs as they stand say why: one Unsure pair, then decisions not applied (whole-app inspection, 1 Nov 2026)
    done.update(unsure=1)
    assert prisma_counts(p)["provisional_line"] == "Provisional: 1 duplicate pair is marked Unsure."
    done.update(decided_since=2)
    line = "Provisional: 2 decided duplicate pairs are not applied yet; the counts change when deduplication runs again."
    assert prisma_counts(p)["provisional_line"] == line
    assert "Provisional: 2 decided duplicate pairs" in write_prisma(p)["svg"]   # the diagram wraps the line
    assert json.loads((p.root / "exports" / "prisma_2020.json").read_text())["provisional"] is True
    assert json.loads(p.store.get_meta("last_result:recall"))["warnings"][0].startswith("Provisional: ")


def test_methods_draft_and_save(screened):
    p = screened
    d = methods_draft(p)
    assert "We searched Scopus" in d and "unique records" in d and "verbatim quote" in d and "protocol main" in d
    assert "protocol hash" in d
    assert read_methods(p) is None
    save_methods(p, "My own words.\n\n")
    assert read_methods(p) == "My own words.\n"


def test_reference_exports(screened):
    p = screened
    n_inc = p.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol='main' AND decision='include'")
    r = export_references(p, which="include")
    assert r["n"] == n_inc > 0 and len(r["files"]) == 3
    ris = (p.root / r["files"][0]).read_text()
    assert ris.count("TY  - JOUR") == n_inc and ris.count("ER  - ") == n_inc and "N1  - WIDER main: include" in ris
    bib = (p.root / r["files"][1]).read_text()
    keys = [ln.split("{", 1)[1].rstrip(",") for ln in bib.splitlines() if ln.startswith("@article{")]
    assert len(keys) == n_inc == len(set(keys))
    rows = list(csv.DictReader(open(p.root / r["files"][2], encoding="utf-8")))
    assert len(rows) == n_inc and set(rows[0]) >= {"record_id", "decision", "title", "doi", "abstract"}
    both = export_references(p, which="include+maybe")
    assert both["n"] >= n_inc and "include_and_maybe" in both["files"][0]
    with pytest.raises(ValueError):
        export_references(p, which="all")


def test_package_has_no_abstracts_and_a_manifest(screened):
    p = screened
    export_references(p)              # must not end up in the package
    save_methods(p, "Methods in my words.")
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    archive(p)
    r = write_package(p)
    z = zipfile.ZipFile(p.root / r["path"])
    names = z.namelist()
    assert "MANIFEST.txt" in names and "methods.md" in names and "prisma_2020.svg" in names
    assert "share_bundle/report.md" in names and "share_bundle/records.csv" in names
    assert any(n.startswith("share_bundle/search_strings/") for n in names)
    assert not any("references" in n or n.endswith(".ris") for n in names)
    header = z.read("share_bundle/records.csv").decode().splitlines()[0]
    assert "abstract," not in header and header.endswith("abstract_words")
    errs = [n for n in names if n.endswith("/errors.csv")]   # failed calls by id and pass, never the error text
    assert errs and all(z.read(n).decode().splitlines()[0] == "record_id,pass" for n in errs)
    decs = [n for n in names if n.startswith("share_bundle/runs/") and n.endswith("/decisions.csv")]
    assert decs and all("parsed" not in (h := z.read(n).decode().splitlines()[0].split(",")) and "error" not in h
                    and "title" in h for n in decs)
    assert not any(n.endswith("eval_report.md") for n in names)
    abstracts = [a for (a,) in p.store.query("SELECT abstract FROM records WHERE abstract IS NOT NULL LIMIT 5")]
    blob = b"".join(z.read(n) for n in names)
    assert not any(a.encode() in blob for a in abstracts if len(a) > 40)
    manifest = z.read("MANIFEST.txt").decode()
    assert "sha256" in manifest and "methods.md" in manifest and len(manifest.splitlines()) == r["n_files"] - 2 + 5
    # manifest.json names the result set read for each protocol (set 1, the active one)
    mj = json.loads(z.read("manifest.json"))
    assert [(x["protocol"], x["key"], x["result_set"]) for x in mj["result_sets"]] == [("main", "main", 1)]
    assert len(mj["files"]) == r["n_files"] - 2 and r["result_sets"] == mj["result_sets"]
    assert "unlock.json" not in names
    # the same project unlocked: unlock.json with exactly the nine keys, and still no abstract text in any file
    artifacts.set_status(p, "synonyms", 1, "frozen")
    artifacts.unlock(p, "synonyms", 1)
    rows = unlock_json(p)
    assert [(x["cause"], x["from_version"]) for x in rows] == [("unlock", 1)]
    z = zipfile.ZipFile(sorted((p.root / "exports").glob("replication_package_*.zip"))[-1])
    blob = b"".join(z.read(n) for n in z.namelist())
    assert "unlock.json" in z.namelist() and not any(a.encode() in blob for a in abstracts if len(a) > 40)


def test_package_refused_on_secret(screened, monkeypatch):
    """A synthetic key value in a file the package would hold: nothing is written, the error names file and kind."""
    from wider.core.sanitize import SecretFound

    p = screened
    p.config.providers[0].api_key_env = "WIDER_TEST_KEY"
    p.save()
    monkeypatch.setenv("WIDER_TEST_KEY", "synthetic-value-0123456789")
    save_methods(p, "Methods in my words. synthetic-value-0123456789")
    before = set((p.root / "exports").glob("replication_package_*.zip"))
    with pytest.raises(SecretFound) as e:
        write_package(p)
    assert "methods.md:1: value of environment variable WIDER_TEST_KEY" in str(e.value)
    assert "synthetic-value" not in str(e.value)
    assert set((p.root / "exports").glob("replication_package_*.zip")) == before
    assert not (p.root / "exports" / "share_bundle").exists()


def test_output_routes_and_download_guard(screened):
    p = screened
    c = TestClient(create_app(p))
    r = c.post("/api/outputs/prisma")
    assert r.status_code == 200 and r.json()["svg"].startswith("<svg")
    assert c.post("/api/outputs/prisma?protocol=nope").status_code == 400
    d = c.get("/api/outputs/methods/draft").json()["draft"]
    assert "We searched" in d
    assert c.post("/api/outputs/methods", json={"text": " "}).status_code == 400
    assert c.post("/api/outputs/methods", json={"text": "Mine."}).json()["file"] == "methods.md"
    assert c.get("/api/outputs/methods").json()["text"] == "Mine.\n"
    refs = c.post("/api/outputs/references?which=include").json()
    assert refs["n"] > 0
    got = c.get(f"/api/outputs/file?path={refs['files'][0]}")
    assert got.status_code == 200 and b"TY  - JOUR" in got.content
    for bad in ["project.yaml", "../project.yaml", "exports/../project.yaml", "/etc/hosts", "store.sqlite"]:
        assert c.get(f"/api/outputs/file?path={bad}").status_code == 404, bad
    pk = c.post("/api/outputs/package").json()
    assert pk["path"].endswith(".zip") and c.get(f"/api/outputs/file?path={pk['path']}").status_code == 200
    assert c.get("/api/query/archive").json() == {"entries": []}
    assert c.post("/api/query/archive").status_code == 400
    assert json.loads(p.store.get_meta("last_result:recall"))["action"] == "package"


def test_export_cli(screened):
    root = str(screened.root)
    env = {"COLUMNS": "200"}
    r = runner.invoke(cli_app, ["export", "prisma", "--dir", root], env=env)
    assert r.exit_code == 0 and "prisma_2020.svg" in r.output
    r = runner.invoke(cli_app, ["export", "methods", "--save", "--dir", root], env=env)
    assert r.exit_code == 0 and "We searched" in r.output and (screened.root / "methods.md").exists()
    r = runner.invoke(cli_app, ["export", "references", "--which", "include+maybe", "--dir", root], env=env)
    assert r.exit_code == 0 and ".bib" in r.output
    assert runner.invoke(cli_app, ["export", "references", "--which", "bad", "--dir", root], env=env).exit_code == 1
    r = runner.invoke(cli_app, ["export", "package", "--dir", root], env=env)
    assert r.exit_code == 0 and ".zip" in r.output
    assert runner.invoke(cli_app, ["query", "archive", "--dir", root], env=env).exit_code == 1   # no synonyms yet
    artifacts.save_new_version(screened, "synonyms", SYN, origin="manual")
    r = runner.invoke(cli_app, ["query", "archive", "--dir", root], env=env)
    assert r.exit_code == 0 and "1 new string(s)" in r.output
    r = runner.invoke(cli_app, ["query", "archive", "--list", "--dir", root], env=env)
    assert r.exit_code == 0 and "synonyms-v1_scopus" in r.output


def test_static_outputs(tmp_path):
    from wider.core.project import Project
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    html = c.get("/").text
    for needle in ['id="q-archive"', 'id="out-prisma"', 'id="out-methods-text"', 'id="out-refs-card"', 'id="out-package"', "/static/outputs.js"]:
        assert needle in html, needle


# ------------------------------------------------------------------ search validity (vision change 2a, task 1.7)

UNLOCK_KEYS = {"unlock_no", "cause", "from_version", "new_version", "at", "by", "final_before", "counts_as_seen",
               "seen_choice", "seen"}
TWO_RIS = ("TY  - JOUR\nTI  - Huddles in surgical teams\nPY  - 2021\nDO  - 10.1000/r1\nER  -\n"
           "TY  - JOUR\nTI  - Briefings after incidents\nPY  - 2022\nDO  - 10.1000/r2\nER  -\n")


def _flat(text: str) -> str:
    return " ".join(text.split())


def _card(p):
    """The Recall page's Search validity card: the route's reply rendered by outputs.js."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    from wider.core.project import Project

    d = TestClient(create_app(Project.load(p.root))).get("/api/outputs/validity").json()
    r = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\nreplies['/api/outputs/validity'] = {json.dumps(d)};\n"
                 f"{(STATIC / 'outputs.js').read_text(encoding='utf-8')}\nawait window.loadOutputs.loadValidity();\n"
                 + out("{html: $('#out-validity').innerHTML}"))
    return d, r["html"]


def validity_everywhere(p):
    """The validity note as each output gives it, each flattened to one line: report.md's "Search validity" section
    (CLI `wider report`), the methods draft (CLI `wider export methods`), the package's manifest.json and the Recall
    page's card (route and outputs.js). Returns (texts by output, the manifest's note, the package zip)."""
    from tests.unit.test_dry_run import _cli

    r = _cli(p, "report")
    assert r.exit_code == 0, r.output
    md = (p.root / "exports" / "report.md").read_text(encoding="utf-8")
    assert "## Search validity" in md, (r.output, md)
    md = md.split("## Search validity\n", 1)[1].split("\n## ", 1)[0]
    m = _cli(p, "export", "methods")
    assert m.exit_code == 0, m.output
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    mj = json.loads(z.read("manifest.json"))["search_validity"]
    d, card = _card(p)
    assert d["text"] == mj["text"] and d["note"]["sentence"] == mj["sentence"]
    texts = {"report": _flat(md), "methods": _flat(m.output), "manifest": " ".join(mj["text"]),
             "card": _flat(html.unescape(re.sub(r"<[^>]+>", " ", card)))}
    return texts, mj, z


def in_every_output(texts: dict, *lines: str) -> None:
    for where, text in texts.items():
        for line in lines:
            assert line in text, (where, line, text)


def unlock_json(p):
    """The package's unlock.json rows (None when the package holds none); each row has exactly UNLOCK_KEYS, its seen
    papers only {benchmark_id, doi, title_norm}, and MANIFEST.txt hashes the file."""
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    if "unlock.json" not in z.namelist():
        return None
    rows = json.loads(z.read("unlock.json"))
    assert rows and all(set(r) == UNLOCK_KEYS for r in rows)
    assert all(set(s) == {"benchmark_id", "doi", "title_norm"} for r in rows for s in r["seen"])
    assert any(line.endswith("unlock.json") for line in z.read("MANIFEST.txt").decode().splitlines())
    return rows


def package_json(p):
    """The package's unlock.json, rounds.json and limits.json, by name (each only when written); MANIFEST.txt carries
    each file's own hash."""
    import hashlib

    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    hashes = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in z.read("MANIFEST.txt").decode().splitlines()
              if "  " in line and not line.startswith("sha256")}
    names = [n for n in z.namelist() if n in ("unlock.json", "rounds.json", "limits.json")]
    assert all(hashes[n] == hashlib.sha256(z.read(n)).hexdigest() for n in names)
    return {n: json.loads(z.read(n)) for n in names}


def _bproj(tmp_path, name, papers=None):
    """B001 in Development, B002 and B003 in Validation (or ``papers``); one file imported."""
    from tests.unit.test_dry_run import _import_one
    from wider.core.models import BenchmarkPaper
    from wider.core.project import Project

    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.write_benchmark(papers or [BenchmarkPaper(benchmark_id="B001", title="Development paper", doi="10.1000/dev",
                                                set="Development")]
                      + [BenchmarkPaper(benchmark_id=f"B00{i}", title=f"Validation paper {i}", doi=f"10.1000/val{i}",
                                        set="Validation") for i in (2, 3)] * (papers is None))
    _import_one(p, tmp_path, f"{name}.ris")
    return p


def test_search_validity_in_report_methods_and_package(tmp_path):
    """Scenario "Unlocked, then a new validation paper": the report, the methods draft, manifest.json and the Recall
    page give "Search not validated: …" after an unlock with no new paper, then D3's sentence, the source and the
    exact interval once a colleague's paper is added."""
    from tests.unit.test_dry_run import _add_val, _import_one, _proj, _versions
    from wider.search import coverage

    p = _proj(tmp_path, n_val=2, name="sv")
    _import_one(p, tmp_path, "sv.ris")
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    artifacts.unlock(p, "synonyms", 1, seen_choice="count")
    artifacts.set_status(p, "synonyms", 2, "frozen")
    at = coverage.unlocks(p)[0]["at"]
    first = f"Search not validated: validation set seen on {at[:10]} (1 unlock); no paper added since."
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, first)
    assert (mj["first_unlock_at"], mj["n_unlocks"], mj["n_validation"], mj["validated"]) == (at, 1, 0, False)
    assert "held-out" not in texts["methods"] and mj["interval"] is None
    _add_val(p, "10.1000/abc")          # the record the import holds, so the query found it
    want = [f"Validation set seen on {at[:10]} (1 unlock); 1 paper added since.",
            f"v1 was unlocked on {at[:10]} into v2 (2 validation papers shown); the researcher chose to count them as "
            "seen.", "Where the validation papers came from: 1 from a colleague.",
            "Recall on the validation set: 1 of 1, 95% interval 0.03 to 1.00.",
            "1 validation paper was also found by the query."]
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, " ".join(want))
    assert all(first not in t for t in texts.values())
    assert (mj["first_unlock_at"], mj["n_unlocks"], mj["n_validation"], mj["validated"]) == (at, 1, 1, True)
    assert mj["by_source"] == {"colleague": 1} and mj["n_found_by_query"] == 1
    assert mj["interval"] == pytest.approx([0.025, 1.0])
    assert "tested once on 1 held-out validation paper." in texts["methods"]


def test_validity_note_reads_look_final_and_unlocks(tmp_path):
    """Scenario "A look before the freeze and an unlock both show": the look's date, the uploads-final date and the
    unlock's date in every output, B002 and B003 seen; with no unlock a counted look gives "(looked at before the
    freeze)"."""
    from tests.unit.test_dry_run import _versions
    from wider.core import journal
    from wider.ingest.importer import mark_final
    from wider.search import coverage

    p = _bproj(tmp_path, "lf")
    coverage.check(p, include_validation=True, seen_choice="count")
    look = journal.changes(p, "validation_peek")[0]["at"][:10]
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    final = mark_final(p)["at"][:10]
    artifacts.unlock(p, "synonyms", 1)
    unlock = coverage.unlocks(p)[0]
    assert unlock["final_before"][:10] == final
    texts, mj, z = validity_everywhere(p)
    in_every_output(texts, f"validation set seen on {unlock['at'][:10]} (1 unlock); no paper added since.",
                    f"Validation set looked at on {look} before the freeze (2 papers shown); the researcher chose to "
                    "count them as seen.", f"Uploads marked final on {final}.")
    assert coverage.validation_papers(p) == [] and mj["validated"] is False
    assert [(x["n"], x["counted"]) for x in mj["looks_before_freeze"]] == [(2, True)]
    seen = {s["benchmark_id"] for row in json.loads(z.read("unlock.json")) for s in row["seen"]}
    assert {"B002", "B003"} <= seen
    # a counted look that showed every Validation paper, then the freeze, no unlock
    q = _bproj(tmp_path, "nounlock")
    coverage.check(q, include_validation=True, seen_choice="count")
    day = journal.changes(q, "validation_peek")[0]["at"][:10]
    _versions(q, "a*")
    artifacts.set_status(q, "synonyms", 1, "frozen")
    texts, mj, z = validity_everywhere(q)
    in_every_output(texts, f"Search not validated: validation set seen on {day} (looked at before the freeze); no "
                           "paper added since.")
    assert mj["validated"] is False and mj["n_unlocks"] == 0 and "unlock.json" not in z.namelist()


def test_early_look_before_this_change_is_reported_by_date(tmp_path):
    """The AND lines of "A look before the freeze and an unlock both show": a look recorded before this change is
    reported by date only and never as validated; a look with no hidden Validation paper (none, or only papers from
    the query's results) asks nothing, records nothing and adds no note line."""
    from tests.unit.test_dry_run import _versions
    from wider.core.models import BenchmarkPaper
    from wider.search import coverage

    p = _bproj(tmp_path, "old")
    p.store.set_meta("validation_peeked_before_v5", "2026-09-01T10:00:00+00:00")
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    texts, mj, _ = validity_everywhere(p)
    want = ("Search not validated: validation set looked at on 2026-09-01 before the freeze; the papers shown were not "
            "recorded.")
    in_every_output(texts, want)
    assert mj["sentence"] == want and mj["validated"] is False and mj["peeked_before_v5"].startswith("2026-09-01")
    assert "held-out" not in texts["methods"]
    dev = BenchmarkPaper(benchmark_id="B001", title="Development paper", doi="10.1000/dev", set="Development")
    qr = BenchmarkPaper(benchmark_id="B002", title="From the results", doi="10.1000/qr", set="Validation",
                        source="query_results")
    for name, papers in (("none", [dev]), ("qr", [dev, qr])):
        q = _bproj(tmp_path, name, papers)
        res = coverage.check(q, include_validation=True)          # no seen_choice: nothing to ask
        assert not res.get("choice_needed")
        assert q.store.get_meta(coverage.SEEN_BY_LOOK) in (None, "[]")
        n = coverage.validity_note(q)
        assert n["lines"] == [] and n["peeked_before_v5"] is None and "not recorded" not in n["sentence"], name
        assert n["looks_before_freeze"] == []
    assert n["detail"] == ["1 validation paper came from the query results and is not counted."]


def test_validity_note_counts_papers_the_query_found(tmp_path):
    """Scenario "Validation papers the query also found": two prior_review papers the import holds, a third it does
    not, and a query_results paper that is not counted."""
    from tests.unit.test_dry_run import _versions
    from wider.core.models import BenchmarkPaper
    from wider.ingest.importer import add_file, import_all
    from wider.search import coverage
    from wider.search.diagnostics import clopper_pearson

    papers = [BenchmarkPaper(benchmark_id="B001", title="Development paper", doi="10.1000/dev", set="Development")]
    papers += [BenchmarkPaper(benchmark_id=f"B00{i}", title=f"Prior review paper {i}", doi=doi, set="Validation",
                              source="prior_review") for i, doi in ((2, "10.1000/r1"), (3, "10.1000/r2"), (4, "10.1000/no"))]
    papers.append(BenchmarkPaper(benchmark_id="B005", title="From the results", doi="10.1000/qr", set="Validation",
                                 source="query_results"))
    p = _bproj(tmp_path, "found", papers)
    (tmp_path / "two.ris").write_text(TWO_RIS, encoding="utf-8")
    add_file(p, "scopus", tmp_path / "two.ris")
    import_all(p)
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    lo, hi = clopper_pearson(2, 3)
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, "Where the validation papers came from: 3 from a prior review.", f"Recall on the validation set: 2 of 3, 95% interval {lo:.2f} to "
                    f"{hi:.2f}.", "2 validation papers were also found by the query.",
                    "1 validation paper came from the query results and is not counted.")
    assert (mj["n_found_by_query"], mj["n_validation"], mj["n_from_query_results"]) == (2, 3, 1)
    # while the set is hidden nothing is read from it: no count, no interval
    q = _bproj(tmp_path, "hidden", papers)
    n = coverage.validity_note(q)
    assert n["n_found_by_query"] is None and n["interval"] is None
    assert not any("found by the query" in t or "interval" in t for t in n["detail"])


def _upload_while_final(p, tmp_path, choice):
    from tests.unit.test_dry_run import RIS
    from wider.ingest.importer import add_file, mark_final

    mark_final(p)
    (tmp_path / f"{p.root.name}-new.ris").write_text(RIS.replace("10.1000/abc", "10.1000/new"), encoding="utf-8")
    add_file(p, "scopus", tmp_path / f"{p.root.name}-new.ris", seen_choice=choice)


def test_validity_note_states_the_seen_choice(tmp_path):
    """Scenario "Each choice on shown papers is stated" (Q1-Q3): a look before the freeze, an upload while final under
    the frozen query v3 and v2 frozen in place of v3, each line in the report, the methods draft, manifest.json and
    the Recall page, with "Count them as seen" and with "Keep them as validation". (The counted look alone:
    tests/unit/test_dry_run.py::test_early_look_asks_whether_to_count_as_seen.)"""
    from tests.unit.test_dry_run import _versions
    from wider.core.provenance import utcnow
    from wider.search import coverage

    day = utcnow()[:10]
    chose = {"keep": "the researcher chose to keep them as validation.", "count": "the researcher chose to count them as seen."}
    look = f"Validation set looked at on {day} before the freeze (2 papers shown); {chose['keep']}"
    upload = "Uploads reopened on {d} while v3 was the frozen query (2 validation papers shown); {c}"
    swap = "v2 was frozen on {d} in place of the frozen query v3 (2 validation papers shown); {c}"
    # every choice kept: the lines stand beside a search that still reads validated on B002 and B003
    p = _bproj(tmp_path, "keep")
    _versions(p, "a*", "b*", "c*")   # saved before the look: a save after it would revise the query (validation-reuse D2)
    coverage.check(p, include_validation=True, seen_choice="keep")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    _upload_while_final(p, tmp_path, "keep")
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, coverage.BOTH_TEXT, look, upload.format(d=day, c=chose["keep"]))
    assert mj["validated"] and mj["n_validation"] == 2 and "tested once on 2 held-out validation papers" in texts["methods"]
    assert [(x["n"], x["counted"]) for x in mj["looks_before_freeze"]] == [(2, False)]
    assert [(x["version"], x["n"], x["counted"]) for x in mj["uploads_reopened_while_frozen"]] == [(3, 2, False)]
    assert [(x["cause"], x["counts_as_seen"]) for x in unlock_json(p)] == [("uploads", 0)]
    # then v2 frozen in place of v3, counted as seen
    artifacts.set_status(p, "synonyms", 2, "frozen", seen_choice_value="count")
    texts, mj, _ = validity_everywhere(p)
    want = [f"Search not validated: validation set seen on {day} (1 unlock); no paper added since.", look,
            upload.format(d=day, c=chose["keep"]), swap.format(d=day, c=chose["count"])]
    in_every_output(texts, " ".join(want))
    assert mj["validated"] is False and [(x["version"], x["replaced"], x["counted"]) for x in mj["freezes_in_place"]] == [
        (2, 3, True)]
    assert [(x["cause"], x["counts_as_seen"]) for x in unlock_json(p)] == [("uploads", 0), ("freeze", 1)]
    # the other choice in each situation: an upload counted as seen, a freeze in place kept as validation
    q = _bproj(tmp_path, "upcount")
    _versions(q, "a*", "b*", "c*")
    artifacts.set_status(q, "synonyms", 3, "frozen")
    _upload_while_final(q, tmp_path, "count")
    texts, mj, _ = validity_everywhere(q)
    in_every_output(texts, f"Search not validated: validation set seen on {day} (1 unlock); no paper added since.",
                    upload.format(d=day, c=chose["count"]))
    assert [x["counted"] for x in mj["uploads_reopened_while_frozen"]] == [True]
    k = _bproj(tmp_path, "swapkeep")
    _versions(k, "a*", "b*", "c*")
    artifacts.set_status(k, "synonyms", 3, "frozen")
    artifacts.set_status(k, "synonyms", 2, "frozen", seen_choice_value="keep")
    texts, mj, _ = validity_everywhere(k)
    in_every_output(texts, coverage.BOTH_TEXT, swap.format(d=day, c=chose["keep"]))
    assert mj["validated"] and [x["counted"] for x in mj["freezes_in_place"]] == [False]


def test_package_holds_rounds_and_unlock(tmp_path):
    """Scenario "Rounds and the unlock record in the package": unlock.json with exactly the nine keys, rounds.json with
    every round (the pasted count, each round's lost seeds, the frozen query marked) and limits.json, all three hashed
    in MANIFEST.txt; none of them names a seed's title; no unlock.json for a project never unlocked."""
    from tests.unit.test_dry_run import _import_one, _proj, _versions
    from wider.core.researchers import Attributor
    from wider.recall.outputs import ROUND_KEYS, ROUND_PLATFORM_KEYS
    from wider.search import coverage, limits, rounds

    p = _proj(tmp_path, n_val=2, name="pk")
    _import_one(p, tmp_path, "pk.ris")
    _versions(p, "a*", "b*")
    artifacts.set_status(p, "synonyms", 2, "frozen")
    assert unlock_json(p) is None
    artifacts.unlock(p, "synonyms", 2, seen_choice="count")
    rows = unlock_json(p)
    u = coverage.unlocks(p)[0]
    assert [(r["unlock_no"], r["cause"], r["from_version"], r["new_version"], r["at"], r["by"], r["final_before"],
             r["counts_as_seen"]) for r in rows] == [(1, "unlock", 2, 3, u["at"], Attributor(p).who(u["by"]), "", 1)]
    assert {s["benchmark_id"] for s in rows[0]["seen"]} == {"B001", "V001", "V002"}
    # the rounds and limits half: two rounds before the unlock and the draft it opened, one count pasted
    rounds.set_count(p, 1, "scopus", "1,200")
    limits.set_limits(p, "scopus", dates="2010..2024", languages="en", reason="protocol limits")
    files = package_json(p)
    assert set(files) == {"unlock.json", "rounds.json", "limits.json"}
    rs = files["rounds.json"]
    assert all(tuple(r) == ROUND_KEYS and all(tuple(x) == ROUND_PLATFORM_KEYS for x in r["platforms"]) for r in rs)
    assert [(r["round"], r["status"], r["frozen_query"], r["lost"]) for r in rs] == [
        (1, "draft", False, []), (2, "frozen", False, []), (3, "draft", False, [])]   # unlocked: no frozen query
    artifacts.set_status(p, "synonyms", 3, "frozen")
    assert [r["frozen_query"] for r in package_json(p)["rounds.json"]] == [False, False, True]
    assert [(x["platform"], x["count"], x["count_source"]) for x in rs[0]["platforms"]] == [("scopus", 1200, "pasted")]
    assert files["limits.json"] == {"scopus": {"dates": "2010..2024", "languages": ["en"], "reason": "protocol limits"}}
    blob = json.dumps(files)
    assert "Development paper" not in blob and "Validation paper" not in blob and ".pdf" not in blob
    # a project with no round and no limit still states its limits (none), and writes no rounds.json
    e = _proj(tmp_path, name="pk0")
    assert package_json(e) == {"limits.json": {"scopus": {}}}


def test_gating_off_is_stated_in_the_note(tmp_path):
    """Scenario "Gating off is stated in the note" (F7): with validation_after_freeze off the note adds "Validation set
    not hidden (validation_after_freeze off)." and claims no validation. The report, the methods draft, the Recall
    page and manifest.json carry the same line, and the methods draft says nothing of a held-out test."""
    from tests.unit.test_dry_run import _import_one, _proj
    from wider.search.coverage import validity_note

    p = _proj(tmp_path, n_val=2, name="gate")
    _import_one(p, tmp_path, "gate.ris")
    p.config.protocol.identification.validation_after_freeze = False
    p.save()
    n = validity_note(p)
    assert "Validation set not hidden (validation_after_freeze off)." in n["lines"]
    assert n["validated"] is False and n["validation_after_freeze"] is False
    assert "validated" not in (n["sentence"] or "").lower().replace("not validated", "")
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, "Validation set not hidden (validation_after_freeze off).")
    assert mj["validated"] is False and mj["validation_after_freeze"] is False
    assert "held-out" not in texts["methods"] and "tested once" not in texts["methods"]


def test_prisma_s_export(tmp_path):
    """Scenario "Items filled and missing" (recall-and-outputs, 2c D17): two platforms imported, their strings archived
    with ledgers, one second-reader run and one chaining run; prisma_s.md lists both platforms with their strings and
    ledgers, names the chaining run and the review run as a model second reader, says "import date; search date not
    recorded", and "not recorded" for updates. No Validation title (counts only) and no model-written text."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    from tests.unit.test_dry_run import RIS, _cli, _proj, _syn
    from wider.core.project import Project
    from wider.ingest.importer import add_file, import_all
    from wider.recall import prisma_s
    from wider.search import review

    p = Project.init(tmp_path / "ps", platforms=["scopus", "wos"])
    p.write_benchmark(_proj(tmp_path, name="ps0").read_benchmark())
    for plat, doi in (("scopus", "10.1000/s1"), ("wos", "10.1000/w1")):
        (tmp_path / f"{plat}.ris").write_text(RIS.replace("10.1000/abc", doi), encoding="utf-8")
        add_file(p, plat, tmp_path / f"{plat}.ris")
    import_all(p)
    artifacts.save_new_version(p, "synonyms", _syn("huddle*"), origin="manual")
    archive(p, 1)
    d = p.run_dir(review.RUN_KIND, "20260101T000000Z")
    (d / "review.json").write_text(json.dumps({
        "synonyms_version": 1, "agreed": 1, "unsure": 2, "readers": [{"run": 1, "name": "llama", "model": "llama3.1:8b"}],
        "platforms": {"scopus": {"findings": [{"quote": "MODEL WROTE THIS", "note": "MODEL NOTE"}]}}}), encoding="utf-8")
    p.store.execute("INSERT INTO external_requests(at, service, purpose, n_items, n_failed) VALUES "
                    "('2026-01-02T00:00:00+00:00', 'openalex', 'seed_chain', 1, 0)")
    p.store.execute("INSERT INTO seed_chain_candidates(doi, title, direction, status) VALUES "
                    "('10.1000/c1', 'A candidate', 'backward', 'open')")

    r = prisma_s.write(p)
    md = (p.root / "exports" / "prisma_s.md").read_text(encoding="utf-8")
    items = {it["no"]: it for it in r["items"]}
    assert [it["no"] for it in r["items"]] == list(range(1, 17)) and prisma_s.CITATION in md
    assert any("Scopus" in ln for ln in items[1]["lines"]) and any("Web of Science" in ln for ln in items[1]["lines"])
    strat = " ".join(items[8]["lines"])
    assert "scopus" in strat and "wos" in strat and strat.count("_ledger.json") == 2 and "no ledger" not in strat
    assert "seed chaining run of 2026-01-02" in items[5]["lines"][0] and "1 backward" in items[5]["lines"][1]
    assert items[14]["lines"][0].startswith("a model second reader, not a human peer review: run 20260101T000000Z")
    s13 = [ln for ln in items[13]["lines"] if ln != "not recorded"]   # an item needing input starts with that line
    assert all("import date; search date not recorded" in ln for ln in s13) and len(s13) == 2
    assert items[13]["lines"][0] == "not recorded" if items[13]["status"] == "Researcher input needed" else True
    assert items[2]["lines"] == ["not recorded", "multi-database platform used: Web of Science"]   # D12
    assert items[2]["status"] == "Researcher input needed"
    for no in (3, 4, 6, 10, 12):
        assert items[no]["lines"] == ["not recorded"] and items[no]["status"] == "Researcher input needed"
    assert "## 12. Updates\n\nStatus: Researcher input needed\n\n### WIDER recorded\n\n- not recorded" in md
    assert items[15]["status"] == "Automatically documented" and items[14]["status"] == "Partially documented"
    assert all(it["researcher"]["text"] == "" for it in r["items"]) and r["message"].startswith("PRISMA-S written: ")
    assert "Development" in items[7]["lines"][0] and "Validation" in items[7]["lines"][0]
    blob = md + (p.root / "exports" / "prisma_s.json").read_text(encoding="utf-8")
    assert "Validation paper" not in blob and "Development paper" not in blob   # counts only, never a title
    assert "MODEL WROTE THIS" not in blob and "MODEL NOTE" not in blob
    # the CLI and the route write the same report; the Recall page names the route's message and files, escaped (the
    # items are shown on PRISMA reporting: owner, 30 Oct, D32 F17)
    c = _cli(p, "export", "prisma-s")
    assert c.exit_code == 0 and "12. Updates: not recorded" in c.output and "PRISMA-S written: 1 automatically documented, 5 partially documented, 10 need your input" in c.output, c.output
    j = TestClient(create_app(Project.load(p.root))).post("/api/outputs/prisma-s").json()
    assert j["items"] == r["items"] and j["path"] == "exports/prisma_s.md"
    j["items"][0]["lines"] = ["<b>x</b>"]
    h = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\n"
                 f"replies['/api/outputs/prisma-s'] = {json.dumps(j)};\n"
                 f"{(STATIC / 'outputs.js').read_text(encoding='utf-8')}\nawait window.loadOutputs.writePrismaS();\n"
                 + out("{html: $('#out-prisma-s-msg').innerHTML}"))["html"]
    assert h.startswith("PRISMA-S written: ") and "exports%2Fprisma_s.md" in h and "exports%2Fprisma_s.json" in h
    assert "data-prisma-s-item" not in h and "<b>x</b>" not in h


# ------------------------------------------------------------------ results-dashboard-and-ris: decision sets (3.1, 3.2)

def _refs_proj(tmp_path):
    from wider.core.project import Project

    p = Project.init(tmp_path / "refs", platforms=["scopus"])
    for i in range(1, 41):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year, abstract) VALUES(?,?,?,?,2020,?)",
                        (i, i, f"k{i}", f"Synthetic paper {i}", f"Synthetic abstract {i}"))
    return p


def _csv_rows(p, rel):
    with open(p.root / rel, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_reference_exclude_set(tmp_path):
    """Scenario "Exclusions": 5 exclude decisions, 4 by both passes and 1 by the third read."""
    from tests.unit.test_agreement import fill, rules
    from wider.recall.outputs import ALL_SETS

    p = _refs_proj(tmp_path)
    fill(p, "main", [("exclude", ["exclude", "exclude"], "A,B")] * 4 + [("exclude", ["exclude", "include", "exclude"], "A,C")]
         + [("include", ["include", "include"], "A,B")] * 2, rules())
    r = export_references(p, "main", which="exclude")
    assert r["n"] == 5 and r["files"][0].endswith("main_exclude.ris")
    ris = (p.root / r["files"][0]).read_text(encoding="utf-8")
    assert [ln for ln in ris.splitlines() if ln.startswith("N1")] == [f"N1  - WIDER main: exclude (record {i})" for i in range(1, 6)]
    rows = _csv_rows(p, r["files"][2])
    assert [x["decided_by"] for x in rows] == ["A,B"] * 4 + ["A,C"] and list(rows[0])[:3] == ["record_id", "decision", "decided_by"]
    assert ALL_SETS == ("include", "include+maybe", "maybe", "exclude", "agreed", "third_read", "split", "researcher")


def test_reference_sets_by_route(tmp_path):
    """Scenario "Sets by how the decision was reached": every route set holds its decisions, whatever they are;
    imported and copied rows are in none; a second screen's S1/S2 reads count as `agreed`."""
    from tests.unit.test_agreement import SYNTHETIC, fill, rules
    from wider.screen import result_sets as rs

    p = _refs_proj(tmp_path)
    fill(p, "main", SYNTHETIC + [("maybe", ["include", "maybe"], "split")], rules())   # records 1-12, 13 split
    got = {w: export_references(p, "main", which=w) for w in ("agreed", "third_read", "split", "researcher")}
    ids = {w: [int(x["record_id"]) for x in _csv_rows(p, r["files"][2])] for w, r in got.items()}
    assert ids == {"agreed": [1, 2, 3, 4, 5, 6], "third_read": [7, 8, 9], "split": [13], "researcher": [10]}
    assert not {11, 12} & {i for v in ids.values() for i in v}          # imported: in no route set
    by = {w: {x["decided_by"] for x in _csv_rows(p, r["files"][2])} for w, r in got.items()}
    assert by == {"agreed": {"A,B"}, "third_read": {"B,C"}, "split": {"split"}, "researcher": {"researcher"}}
    ris = (p.root / got["agreed"]["files"][0]).read_text(encoding="utf-8")
    assert "N1  - WIDER main: exclude (record 1)" in ris and "N1  - WIDER main: include (record 5)" in ris
    # a reused set: the copied rows are in no route set, its own reads are
    rs.new_set(p, "main", "screening", settings=rules())
    assert rs.copy_reused(p, {"reuse": {"key": "main"}}, "main#2", [1, 2, 3]) == 3
    fill(p, "main#2", [("include", ["include", "include"], "A,B")], start=20)
    r2 = export_references(p, "main", which="agreed", result_set=2)
    assert [int(x["record_id"]) for x in _csv_rows(p, r2["files"][2])] == [20] and r2["files"][0].endswith("main.s2_agreed.ris")
    # a second screen: S1 and S2 agreeing is `agreed`, S3 with one of them is `third_read`; carried rows are in none
    fill(p, "sec", [("include", ["include", "include"], "A,B")] * 2, rules(), protocol="sec")
    rs.new_set(p, "sec", "second", settings=rules(kind="second_screen"))
    fill(p, "sec#2", [("include", ["include", "include"], "carried from sec"), ("include", ["include", "include"], "S1,S2"),
                      ("include", ["include", "exclude", "include"], "S1,S3")])
    agreed = export_references(p, "sec", which="agreed", result_set=2)
    third = export_references(p, "sec", which="third_read", result_set=2)
    assert [x["record_id"] for x in _csv_rows(p, agreed["files"][2])] == ["2"]
    assert [x["record_id"] for x in _csv_rows(p, third["files"][2])] == ["3"]


def test_unresolved_marked_in_every_format(tmp_path):
    """Scenario "Unresolved record marked": a split record, one reused from a set where it was split and a second
    screen's record carried from a split in its source set are marked unresolved in RIS, BibTeX and CSV."""
    from tests.unit.test_agreement import fill, rules
    from wider.screen import result_sets as rs

    p = _refs_proj(tmp_path)
    fill(p, "main", [("maybe", ["maybe", "maybe", "include"], "A,B"), ("maybe", ["include", "maybe", "exclude"], "split"),
                     ("maybe", ["include", "maybe", "exclude"], "split", {"kind": "reused", "set": 1, "decided_by": "split"}),
                     ("include", ["include", "include"], "A,B")], rules())
    r = export_references(p, "main", which="maybe")
    ris, bib = ((p.root / f).read_text(encoding="utf-8") for f in r["files"][:2])
    assert [ln for ln in ris.splitlines() if ln.startswith("N1")] == [
        "N1  - WIDER main: maybe (record 1)", "N1  - WIDER main: maybe (unresolved: no majority) (record 2)",
        "N1  - WIDER main: maybe (unresolved: no majority) (record 3)"]
    assert bib.count("maybe (unresolved: no majority)") == 2 and "WIDER main: maybe (record 1)" in bib
    rows = _csv_rows(p, r["files"][2])
    assert [(x["status"], x["pathway"]) for x in rows] == [("final", "agreed"), ("unresolved", "split"),
                                                           ("unresolved", "carried")]
    assert [x["status"] for x in _csv_rows(p, export_references(p, "main", which="include")["files"][2])] == ["final"]
    # a second screen carrying record 2 (split in `main`) and record 1 (final) from its source set
    fill(p, "sec", [("include", ["include", "include"], "A,B")], rules(), protocol="sec", start=30)
    rs.new_set(p, "sec", "second", settings=rules(kind="second_screen"))
    fill(p, "sec#2", [("maybe", ["maybe", "maybe"], "carried from main"),
                      ("maybe", ["include", "maybe", "exclude"], "carried from main")])
    r2 = export_references(p, "sec", which="maybe", result_set=2)
    assert [x["status"] for x in _csv_rows(p, r2["files"][2])] == ["final", "unresolved"]
    assert "maybe (unresolved: no majority) (record 2)" in (p.root / r2["files"][0]).read_text(encoding="utf-8")


def test_reference_export_all(tmp_path):
    """Scenarios "All sets written" and "A set emptied since the last run"."""
    from tests.unit.test_agreement import fill, rules
    from wider.recall.outputs import ALL_SETS, export_all_references
    from wider.screen import result_sets as rs
    from wider.screen.decisions import set_decision

    p = _refs_proj(tmp_path)
    fill(p, "main", [("include", ["include", "include"], "A,B"), ("maybe", ["maybe", "maybe"], "A,B"),
                     ("exclude", ["exclude", "exclude"], "A,B"), ("exclude", ["include", "exclude", "exclude"], "B,C"),
                     ("include", ["include", "exclude", "maybe", "researcher:include"], "researcher")], rules())
    r = export_all_references(p, "main")
    assert [s["set"] for s in r["sets"]] == list(ALL_SETS) and r["index"] == "exports/references/main_index.csv"
    counts = {s["set"]: s["records"] for s in r["sets"]}
    assert counts == {"include": 2, "include+maybe": 3, "maybe": 1, "exclude": 2, "agreed": 3, "third_read": 1,
                      "split": 0, "researcher": 1}
    assert all(len(s["files"]) == 3 for s in r["sets"] if s["set"] != "split")
    idx = _csv_rows(p, r["index"])
    assert [x["set"] for x in idx] == list(ALL_SETS) and len(idx) == 8
    assert idx[6] == {"set": "split", "records": "0", "files": ""}
    assert idx[0]["files"] == "main_include.ris;main_include.bib;main_include.csv"
    ref = p.root / "exports" / "references"
    assert not list(ref.glob("main_split.*"))
    # a split record written, then answered by the researcher: its files go
    fill(p, "main", [("maybe", ["include", "maybe"], "split")], start=6)
    export_all_references(p, "main")
    assert sorted(x.name for x in ref.glob("main_split.*")) == ["main_split.bib", "main_split.csv", "main_split.ris"]
    set_decision(p, "main", 6, "include", votes=["include", "maybe", "researcher:include"], decided_by="researcher")
    r = export_all_references(p, "main")
    assert not list(ref.glob("main_split.*"))
    assert _csv_rows(p, r["index"])[6] == {"set": "split", "records": "0", "files": ""}
    # set 2 has its own index and leaves set 1's files as they were
    before = {x.name: x.read_bytes() for x in ref.glob("main_*")}
    rs.new_set(p, "main", "screening", settings=rules())
    fill(p, "main#2", [("exclude", ["exclude", "exclude"], "A,B")], start=30)
    r2 = export_all_references(p, "main", result_set=2)
    assert r2["index"] == "exports/references/main.s2_index.csv"
    assert {s["set"]: s["records"] for s in r2["sets"]}["exclude"] == 1
    assert {x.name: x.read_bytes() for x in ref.glob("main_*")} == before


def test_reference_export_all_names_kept_apart(tmp_path):
    """Protocols ``main+human`` and ``main_human`` write and remove only their own reference files."""
    from tests.unit.test_agreement import fill, rules
    from wider.recall.outputs import export_all_references

    p = _refs_proj(tmp_path)
    fill(p, "main+human", [("maybe", ["include", "maybe"], "split"), ("include", ["include", "include"], "A,B")],
         rules(), protocol="main+human")
    fill(p, "main_human", [("include", ["include", "include"], "A,B")] * 3, rules(), start=10, protocol="main_human")
    a = export_all_references(p, "main+human")
    ref = p.root / "exports" / "references"
    mine = {x.name: x.read_bytes() for x in ref.glob("main+human_*")}
    b = export_all_references(p, "main_human")
    assert a["index"] == "exports/references/main+human_index.csv" and b["index"] == "exports/references/main_human_index.csv"
    assert {x.name: x.read_bytes() for x in ref.glob("main+human_*")} == mine and "main+human_split.ris" in mine
    assert [x["record_id"] for x in _csv_rows(p, "exports/references/main_human_include.csv")] == ["10", "11", "12"]


def test_references_all_and_which_refused(tmp_path):
    """`--all` with `--which` is refused on the CLI and the route; `--all` and `all=true` write the same index."""
    from tests.unit.test_agreement import fill, rules
    from wider.core.project import Project

    p = _refs_proj(tmp_path)
    fill(p, "main", [("include", ["include", "include"], "A,B"), ("exclude", ["exclude", "exclude"], "A,B")], rules())
    root, env = str(p.root), {"COLUMNS": "200"}
    r = runner.invoke(cli_app, ["export", "references", "--all", "--which", "include", "--dir", root], env=env)
    assert r.exit_code == 1 and "--all writes every decision set; leave out --which" in r.output
    r = runner.invoke(cli_app, ["export", "references", "--all", "--dir", root], env=env)
    assert r.exit_code == 0 and "main_index.csv" in r.output and "split" in r.output, r.output
    cli_index = (p.root / "exports" / "references" / "main_index.csv").read_text(encoding="utf-8")
    r = runner.invoke(cli_app, ["export", "references", "--dir", root], env=env)       # --which defaults to include
    assert r.exit_code == 0 and "main_include.ris" in r.output
    c = TestClient(create_app(Project.load(p.root)))
    bad = c.post("/api/outputs/references?all=true&which=include&protocol=main")
    assert bad.status_code == 400 and "leave out which" in bad.json()["detail"]
    j = c.post("/api/outputs/references?all=true&protocol=main").json()
    assert j["index"] == "exports/references/main_index.csv" and len(j["sets"]) == 8
    assert (p.root / j["index"]).read_text(encoding="utf-8") == cli_index
    assert c.post("/api/outputs/references?protocol=main").json()["which"] == "include"


def test_checklist_card_and_write_every_set_node():
    """The PRISMA report card's Checklist button posts the protocol and set and names the message and both files,
    escaped; the items are on PRISMA reporting (owner, 30 Oct, D32 F17). ("Write every set" moved to the Results
    dashboard's Export records card: test_results_js_node.py.)"""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node

    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="out-refs-all"' not in page
    assert re.search(r'<button [^>]*id="out-prisma-2020"[^>]*>Checklist</button>', page)
    card = re.search(r'<div class="card" id="out-prisma-card">(.*?)</div>', page, re.S).group(1)
    assert '<a href="#reporting">PRISMA reporting</a>' in card
    for gone in ('id="out-prisma-svg"', 'id="out-prisma-s-out"', 'id="out-prisma-2020-out"', 'id="out-prisma-s-card"', 'id="out-prisma-2020-card"'):
        assert gone not in page, gone
    checklist = {"message": "PRISMA 2020 checklist written: 7 of 42 items filled", "path": "exports/prisma_2020_checklist.md",
                 "json_path": "exports/prisma_2020_checklist.json", "items": [
                     {"no": "5", "name": "Eligibility criteria", "filled": True, "lines": ["research question: <i>q</i>", "codebook v1"]},
                     {"no": "11", "name": "Study risk of bias assessment", "filled": False, "lines": ["not recorded by WIDER"]}]}
    r = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\n"
                 f"replies['/api/outputs/prisma-checklist'] = {json.dumps(checklist)};\n"
                 f"{(STATIC / 'outputs.js').read_text(encoding='utf-8')}\n"
                 "$('#rc-protocol').value = 'main'; $('#rc-set').value = '2';\n"
                 "await $('#out-prisma-2020').onclick(); await flush();\n"
                 + out("{calls, clmsg: $('#out-prisma-2020-msg').innerHTML}"))
    urls = [c[:2] for c in r["calls"]]
    assert ["/api/outputs/prisma-checklist?protocol=main&result_set=2", "POST"] in urls
    assert not any("/api/outputs/references" in u for u, _ in urls)
    cl = r["clmsg"]
    assert "7 of 42 items filled" in cl and "prisma_2020_checklist.md" in cl and "prisma_2020_checklist.json" in cl
    assert "data-prisma-2020-item" not in cl and "<i>q</i>" not in cl


# ------------------------------------------------------------------ Export records card (decision-set-exports-and-found-chart, task 4.1)

# the card's controller on fake parts: `find` hands out one element per selector; `api` answers from `replies` and,
# for a call it has no answer for, prints it and stops, so `_card` can ask the real route and run the script again
CARD = r"""
const calls = [], els = {};
const mk = () => ({ innerHTML: "", value: "", disabled: false });
const parts = { protocol: mk(), set: mk(), sample: mk(), body: mk(), find: (s) => (els[s] = els[s] || mk()) };
const api = async (path, opts) => { const m = (opts && opts.method) || "GET", r = replies[m + " " + path]; calls.push([path, m]);
  if (r === undefined) { console.log(JSON.stringify({ need: [m, path] })); process.exit(0); }
  if (r && r.error) throw new Error(r.error); return r; };
const c = Results.controller(parts, { api });
const el = (n) => els["[data-export-" + n + "]"] || mk();
const ids = () => [...el("records").innerHTML.matchAll(/data-export-record="(\d+)"/g)].map(m => Number(m[1]));
const ticked = () => [...el("records").innerHTML.matchAll(/value="(\d+)" checked/g)].map(m => Number(m[1]));
"""


def _show(expr):
    return f"console.log(JSON.stringify({expr}));"


def _records_card(p, body):
    """Run ``body`` on the Export records card of project ``p`` after the dashboard loaded. Every call the card makes
    is answered by the real route on its first use (a refusal as the error the page's api throws), so each line the
    card shows is the route's own; the calls are cached, so a POST runs once."""
    from tests.unit.test_backends_js_node import ESC, STATIC, run_node
    from wider.core.project import Project
    c = TestClient(create_app(Project.load(p.root)))
    js, replies = (STATIC / "results.js").read_text(encoding="utf-8"), {}
    while True:
        r = run_node(f"{ESC}\n{js}\nconst replies = {json.dumps(replies)};\n{CARD}\nawait c.load({{}});\n{body}")
        if "need" not in r:
            return r
        method, path = r["need"]
        res = c.request(method, path)
        replies[f"{method} {path}"] = res.json() if res.status_code == 200 else {"error": res.json()["detail"]}


def _dry(p, *args):
    """The count line `wider export selection --dry-run` prints (its first line), or its whole output, in one line."""
    from tests.unit.test_record_selection import cli
    r = cli(p, *args, "--dry-run")
    assert r.exit_code == 0, r.output
    return r.output.splitlines()[0], " ".join(r.output.split())


def _plain(h):
    return html.unescape(re.sub(r"<[^>]*>", "", h))


def test_double_click_writes_once_node(tmp_path):
    """A second press of Export, Download export package or Every set at once while a write runs sends nothing: one
    POST per button, the second press returns nothing."""
    from tests.unit.test_record_selection import _proj
    p = _proj(tmp_path)
    r = _records_card(p, "const out = {};"
                 "for (const k of ['exportNow', 'writePackage', 'writeAll']) {"
                 "  const n = calls.length, [a, b] = await Promise.all([c[k](), c[k]()]);"
                 "  out[k] = { posts: calls.slice(n).filter(x => x[1] === 'POST').length, first: !!a, second: b }; }"
                 + _show("out"))
    for k in ("exportNow", "writePackage", "writeAll"):
        assert r[k] == {"posts": 1, "first": True, "second": None}, k


def test_export_records_card_defaults_node(tmp_path):
    """Scenario "One-click default": the card sits in the reference section under the counts; its preset select offers
    the six presets with Include + Maybe selected, the filters under a closed line, RIS alone ticked, abstracts
    ticked; **Export** unchanged writes main_include_and_maybe.ris with the 5 records, as `wider export selection`."""
    from tests.unit.test_record_selection import _proj, ris_ids
    from wider.recall import dashboard as dash
    p = _proj(tmp_path)
    r = _records_card(p, "const before = el('count').innerHTML, go = el('go').disabled; await c.exportNow();"
                 + _show("{calls, before, go, msg: el('msg').innerHTML, page: parts.body.innerHTML}"))
    page = r["page"]
    sec = re.search(r'data-results-section="reference_sets">(.*?)</section>', page, re.S).group(1)
    assert "<div data-export-card><h4 id=\"export-records\">Export records</h4>" in sec
    sel = re.search(r'<select data-export-field="preset">(.*?)</select>', sec).group(1)
    assert re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', sel) == [
        ("include", "Include"), ("include+maybe", "Include + Maybe"), ("maybe", "Maybe only"), ("exclude", "Exclude"),
        ("unresolved", "Unresolved only"), ("every", "Every decision")]
    assert '<option value="include+maybe" selected>' in sel and sel.count("selected") == 1
    assert "<details data-export-filters><summary>Filter by how the decision was reached</summary>" in sec
    boxes = re.findall(r'data-export-field="([a-z_]+)" value="([^"]+)"( checked)?', sec)
    assert [(f, v) for f, v, on in boxes if on] == [("format", "ris"), ("abstracts", "true"), ("pkg_format", "ris")]
    assert {v for f, v, _ in boxes if f == "pathway"} == {"agreed", "third_read", "split", "researcher"}
    assert "<button class=\"main-act\" data-export-go>Export</button>" in sec and "<code>wider export selection" not in re.sub(
        r'<details class="cmdline">.*?</details>', "", sec)
    assert '<details class="cmdline"><summary>Command line</summary><p class="small"><code>wider export selection' in sec
    count, _ = _dry(p)
    assert _plain(r["before"]) == f"{count} · main_include_and_maybe.ris" and r["go"] is False
    assert ["/api/outputs/selection?protocol=main&preset=include%2Bmaybe&format=ris", "POST"] in r["calls"]
    assert r["calls"][-1] == ["/api/results?protocol=main&result_set=1&sample=validation", "GET"]   # documents again
    assert "main_include_and_maybe.ris" in r["msg"] and 'href="/api/outputs/file?path=exports%2Freferences%2F' in r["msg"]
    assert ris_ids(p.root / "exports" / "references" / "main_include_and_maybe.ris") == [1, 2, 3, 4, 5]
    assert sorted(x.name for x in (p.root / "exports" / "references").iterdir()) == ["main_include_and_maybe.ris"]
    assert dash.dashboard(p, "main")["reference_sets"]["sets"][1]["records"] == 5


def test_count_line_follows_choices_node(tmp_path):
    """Scenario "Count before export": the count line is re-asked when the preset, a pathway, a pattern or a format
    changes and reads as `--dry-run` does; no format ticked disables Export; an empty selection says the CLI's
    sentence; nothing is written while choosing."""
    from tests.unit.test_record_selection import _proj, ref_files
    p = _proj(tmp_path)
    steps = [("preset", "include", True), ("pathway", "third_read", True), ("pathway", "agreed", True),
             ("format", "bib", True), ("pattern", "include_exclude", True), ("pattern", "include_exclude", False),
             ("format", "ris", False), ("format", "bib", False), ("format", "csv", True), ("format", "csv", False)]
    js = "".join(f"await c.choose({json.dumps(n)}, {json.dumps(v)}, {json.dumps(on)}); "
                 "seen.push([el('count').innerHTML, el('go').disabled]);" for n, v, on in steps)
    r = _records_card(p, "const seen = [];" + js + "await c.choose('pathway', 'agreed', false); await c.choose('pathway', 'third_read', false);"
                 "await c.choose('format', 'ris', true); await c.choose('pathway', 'split', true);"
                 "seen.push([el('count').innerHTML, el('go').disabled]);" + _show("{seen, calls}"))
    want = [_dry(p, "--preset", "include")[0], _dry(p, "--preset", "include", "--pathway", "third_read")[0],
            _dry(p, "--preset", "include", "--pathway", "agreed,third_read")[0],
            _dry(p, "--preset", "include", "--pathway", "agreed,third_read", "--format", "ris,bib")[0],
            _dry(p, "--preset", "include", "--pathway", "agreed,third_read", "--pattern", "include_exclude", "--format", "ris,bib")[0],
            _dry(p, "--preset", "include", "--pathway", "agreed,third_read", "--format", "ris,bib")[0],
            _dry(p, "--preset", "include", "--pathway", "agreed,third_read", "--format", "bib")[0]]
    seen = r["seen"]
    for (line, off), w in zip(seen, want):
        assert _plain(line).startswith(w + " · main_include") and off is False, (line, w)
    assert seen[3][0].endswith("main_include_by_agreed_third_read.zip")
    assert seen[7] == ["Tick at least one format to export.", True]
    assert _plain(seen[8][0]).startswith(_dry(p, "--preset", "include", "--pathway", "agreed,third_read", "--format", "csv")[0])
    assert seen[9] == ["Tick at least one format to export.", True]
    empty = "No records match Include, unresolved disagreement; nothing was written."
    assert _plain(seen[10][0]) == empty and empty in _dry(p, "--preset", "include", "--pathway", "split")[1]
    assert all(m == "GET" for _, m in r["calls"]) and ref_files(p) == []


def test_final_only_box_node(tmp_path):
    """Scenario "Unresolved only, or unresolved left out": "Leave out unresolved records" re-lists the records without
    the unresolved one, the count line reads as `--final-only --dry-run`, and Export writes the _final file."""
    from tests.unit.test_record_selection import _proj, ris_ids
    p = _proj(tmp_path)
    r = _records_card(p, "await c.choose('final_only', 'true', true); const line = el('count').innerHTML, listed = ids();"
                 "await c.exportNow(); await c.choose('final_only', 'true', false); await c.choose('preset', 'unresolved', true);"
                 + _show("{line, listed, unres: el('count').innerHTML, calls}"))
    assert _plain(r["line"]) == _dry(p, "--final-only")[0] + " · main_include_and_maybe_final.ris"
    assert "1 unresolved left out" in r["line"] and r["listed"] == [1, 2, 3, 4]
    assert ["/api/outputs/selection?protocol=main&preset=include%2Bmaybe&final_only=true&format=ris", "POST"] in r["calls"]
    assert ris_ids(p.root / "exports" / "references" / "main_include_and_maybe_final.ris") == [1, 2, 3, 4]
    assert _plain(r["unres"]).startswith(_dry(p, "--preset", "unresolved")[0])


def test_choose_records_node(tmp_path):
    """Scenario "Records chosen one by one": the list shows the selection's records all ticked with title, year,
    decision, status and pathway; unticking 2 gives "3 of 5 records chosen" and Export writes the _picked file
    as `--records 1,2,3`; a new preset lists its records all ticked again; none ticked disables Export."""
    from tests.unit.test_record_selection import _proj, ris_ids
    p = _proj(tmp_path)
    r = _records_card(p, "const list = el('records').innerHTML, all = ticked();"
                 "await c.choose('record', '4', false); await c.choose('record', '5', false);"
                 "const line = el('count').innerHTML, q = c.selectionQuery(); await c.exportNow();"
                 "await c.choose('preset', 'include', true); const again = [ids(), ticked()];"
                 "for (const i of ids()) await c.choose('record', String(i), false);"
                 + _show("{list, all, line, q, again, none: [el('count').innerHTML, el('go').disabled], calls}"))
    assert r["all"] == [1, 2, 3, 4, 5]
    row = re.search(r'<tr data-export-record="5">(.*?)</tr>', r["list"]).group(1)
    assert [_plain(x).strip() for x in re.findall(r"<td>(.*?)</td>", row)] == \
        ["Synthetic paper 5", "2020", "maybe", "unresolved", "unresolved disagreement"]
    assert _plain(r["line"]) == _dry(p, "--records", "1,2,3")[0] + " · main_include_and_maybe_picked.ris"
    assert r["line"].startswith("3 of 5 records chosen · RIS")
    assert r["q"] == "&preset=include%2Bmaybe&records=1%2C2%2C3&format=ris"
    assert ris_ids(p.root / "exports" / "references" / "main_include_and_maybe_picked.ris") == [1, 2, 3]
    assert r["again"] == [[1, 2, 3], [1, 2, 3]]
    assert r["none"] == ["No record is ticked; tick at least one to export.", True]


def test_package_none_ticked_node(tmp_path):
    """Risk-check finding: with the selection in the package and no record ticked, the package list says so, its
    button is disabled and nothing is written; ticking one record again lists the package with that one record."""
    from tests.unit.test_record_selection import _proj, ref_files
    p = _proj(tmp_path)
    r = _records_card(p, "await c.cardLoaded(); await c.choose('add', 'selection', true);"
                 "for (const i of ids()) await c.choose('record', String(i), false);"
                 "const none = [el('package-list').innerHTML, el('package').disabled]; const wrote = await c.writePackage();"
                 "await c.choose('record', '2', true);"
                 + _show("{none, wrote, again: [el('package-list').innerHTML, el('package').disabled], calls}"))
    assert r["none"] == ["No record is ticked; tick at least one, or leave the selection out of the package.", True]
    assert r["wrote"] is None and not any(m == "POST" for _, m in r["calls"]) and ref_files(p) == []
    assert r["again"][1] is False and "main_include_and_maybe_picked.ris: 1 record" in _plain(r["again"][0])


def test_count_breakdown_names_other_route_node(tmp_path):
    """Scenario "No pathway ticked holds every route" (ui-tester finding): under the count line the card names the
    records per route and per pattern of the first two reads, as `--dry-run` prints them, other routes included; an
    empty selection shows no breakdown."""
    from tests.unit.test_record_selection import BASE, _proj
    rows = BASE + [("include", None, "imported from list.csv"),
                   ("include", ["include", "include"], "A,B", {"kind": "reused", "set": 1, "decided_by": "A,B"})]
    p = _proj(tmp_path, rows)
    r = _records_card(p, "await c.choose('preset', 'include', true); const one = el('breakdown').innerHTML;"
                 "await c.choose('pathway', 'split', true);" + _show("{one, empty: el('breakdown').innerHTML}"))
    out = _dry(p, "--preset", "include")[1]
    by, pat = [_plain(x) for x in r["one"].split("<br>")]
    assert by.startswith("By how the decision was reached:") and pat.startswith("First two reads:")
    assert by in out and pat in out and "2 by another route (imported, carried, one pass or older)" in by
    assert r["empty"] == ""


def test_abstracts_box_node(tmp_path):
    """Scenario "Abstracts left out": the box's sentence stands once, in normal text beside it; unticked, Export in
    all three formats writes the ZIP the CLI's `--no-abstracts` writes, and the package list follows the box."""
    from tests.unit.test_record_selection import _proj, _zip_members, cli
    p = _proj(tmp_path)
    r = _records_card(p, "await c.choose('format', 'bib', true); await c.choose('format', 'csv', true);"
                 "await c.choose('abstracts', 'true', false); await c.exportNow();"
                 + _show("{calls, page: parts.body.innerHTML}"))
    sentence = "Abstracts are the publishers&#39; text; leave them out if you pass these files to someone else."
    assert r["page"].count(sentence) == 1
    assert f'value="true" checked> Include abstracts</label> {sentence}</p>' in r["page"]
    assert ["/api/outputs/selection?protocol=main&preset=include%2Bmaybe&format=ris%2Cbib%2Ccsv&abstracts=false", "POST"] in r["calls"]
    assert ["/api/outputs/selection?protocol=main&format=ris&abstracts=false&package=true", "GET"] in r["calls"]
    zp = p.root / "exports" / "references" / "main_include_and_maybe.zip"
    card = _zip_members(zp)
    assert b"AB  -" not in card["main_include_and_maybe.ris"] and b"abstract =" not in card["main_include_and_maybe.bib"]
    assert cli(p, "--format", "ris,bib,csv", "--no-abstracts").exit_code == 0 and _zip_members(zp) == card


def test_package_contents_listed_node(tmp_path):
    """Scenarios "Default package" and "Customized package": before writing, the card lists the package's files and
    counts as `--package --dry-run` prints them; "Customize package" sends --add and the package formats, "the current
    selection" carries the card's filters; **Download export package** writes the ZIP with those files."""
    from tests.unit.test_record_selection import _proj, _zip_members
    p = _proj(tmp_path)
    r = _records_card(p, "const first = el('package-list').innerHTML;"
                 "await c.choose('add', 'include', true); await c.choose('add', 'exclude', true); await c.choose('pkg_format', 'csv', true);"
                 "const custom = el('package-list').innerHTML; await c.writePackage(); const msg = el('msg').innerHTML;"
                 "await c.choose('final_only', 'true', true); await c.choose('add', 'selection', true);"
                 + _show("{first, custom, msg, sel: el('package-list').innerHTML, calls}"))
    lis = lambda h: [_plain(x) for x in re.findall(r"<li>(.*?)</li>", h)]  # noqa: E731
    dry = _dry(p, "--package")[1]
    assert lis(r["first"]) and all(x in dry for x in lis(r["first"]))
    assert lis(r["first"])[0] == "main_include_and_maybe.ris: 5 records, 1 marked unresolved"
    custom = _dry(p, "--package", "--add", "include,exclude", "--format", "ris,csv")[1]
    assert len(lis(r["custom"])) > len(lis(r["first"])) and all(x in custom for x in lis(r["custom"]))
    post = "/api/outputs/selection?protocol=main&add=include%2Cexclude&format=ris%2Ccsv&package=true"
    assert [post, "POST"] in r["calls"]
    zp = sorted((p.root / "exports" / "references").glob("main_export_package_*.zip"))
    assert len(zp) == 1 and zp[0].name in r["msg"] and r["msg"].startswith("Export package, ")
    names = sorted(_zip_members(zp[0]))
    assert {x.split(":")[0] for x in lis(r["custom"])} <= set(names)
    assert ["/api/outputs/selection?protocol=main&preset=include%2Bmaybe&final_only=true&add=include%2Cexclude%2Cselection&format=ris%2Ccsv"
            "&package=true", "GET"] in r["calls"]
    assert any("_final" in x for x in lis(r["sel"]))


def test_recall_page_links_to_card_node():
    """Scenario "Counts match the exports": the Recall page holds only the line linking the Export records card; its
    old set select and "Write every set" button are gone, and outputs.js no longer writes reference files."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    card = re.search(r'<div[^>]*id="out-refs-card"[^>]*>(.*?)</div>', page, re.S).group(1)
    assert _plain(card).strip() == "Reference files are exported on the Results dashboard (Export records)."
    assert '<a href="#results">Results dashboard (Export records)</a>' in card
    for gone in ('id="out-refs-which"', 'id="out-refs-all"', 'id="out-refs"', "data-results-write-all"):
        assert gone not in page, gone
    js = (STATIC / "outputs.js").read_text(encoding="utf-8")
    assert "/api/outputs/references" not in js
    r = run_node(f"{ESC}\n{PAGE}\nglobal.location = {{hash: ''}}; window.addEventListener = () => {{}};\n{js}\n"
                 + out("Object.keys(window.loadOutputs)"))
    assert not {"exportRefs", "refsIndexHtml", "writeEverySet"} & set(r)


def test_card_reproduces_older_sets_node(tmp_path):
    """Scenario "Exclusions", last line, on the card: each older `--which` set, chosen by its preset (a route set by
    Every decision with that one pathway) in all three formats, gives the older files byte for byte, same names."""
    from tests.unit.test_record_selection import _proj, _zip_members
    p = _proj(tmp_path)
    older = {}
    for which in ("include", "include+maybe", "maybe", "exclude", "agreed", "third_read", "split", "researcher"):
        files = export_references(p, "main", which=which)["files"]
        older[which] = {f.rsplit("/", 1)[1]: (p.root / f).read_bytes() for f in files}
    r = _records_card(p, "await c.choose('format', 'bib', true); await c.choose('format', 'csv', true); const done = {};"
                 "for (const [which, [preset, paths]] of Object.entries(Results.OLDER)) {"
                 "  await c.choose('preset', preset, true); for (const x of paths) await c.choose('pathway', x, true);"
                 "  const w = await c.exportNow(); done[which] = w.file;"
                 "  for (const x of paths) await c.choose('pathway', x, false); }" + _show("done"))
    assert set(r) == set(older)
    for which, f in r.items():
        assert _zip_members(p.root / f) == older[which], which


def test_package_names_sampled_exports(tmp_path):
    """Requirement "Mark an export as a random sample" (D8): the PRISMA-S database line and the package manifest state
    the sample and its N; an unmarked file reads as before."""
    from tests.unit.test_ingest import _sampled
    from wider.ingest.importer import set_sample
    from wider.recall import prisma_s

    p = _sampled(tmp_path, n=40)

    def db_lines():
        return [ln for ln in prisma_s.build(p)["items"][0]["lines"] if "oa.ris" in ln]

    assert db_lines() == ["openalex: export oa.ris, 40 records"], db_lines()
    set_sample(p, 1, 650000, by="ana")
    assert db_lines() == ["openalex: export oa.ris, 40 records, a random sample of 650,000 results"], db_lines()
    m = json.loads(zipfile.ZipFile(p.root / write_package(p)["path"]).read("manifest.json"))
    assert [(f["file"], f["records"], f["sample_of"]) for f in m["imported_files"]] == [("oa.ris", 40, 650000)]
    assert prisma_s._totals(p, prisma_s._sources(p))[0].endswith("(oa.ris), a random sample of 650,000 results")
    from wider.recall.reporting import _db_line

    assert [_db_line(s) for s in prisma_s._sources(p)] == [
        "openalex: export oa.ris, 40 records, a random sample of 650,000 results"]
