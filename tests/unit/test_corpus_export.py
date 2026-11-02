"""ui-redesign D4 (owner, round 4): the plain corpus export, every unique record with its bibliographic data only, in
RIS, BibTeX and CSV, from `wider export references --corpus` and the export area of Recall and report. It works while
development labels are open."""
import csv
import re

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import project, project_open  # noqa: F401 - pytest fixtures
from wider.cli import app as cli_app
from wider.ui.app import create_app

runner = CliRunner()
JUDGED = re.compile(r"include|exclude|maybe|decision|evaluat|model|screen|WIDER", re.I)


def _files(p):
    out = p.root / "exports" / "references"
    return {ext: out / f"corpus.{ext}" for ext in ("ris", "bib", "csv")}


def _check(p, n):
    f = _files(p)
    ris = f["ris"].read_text(encoding="utf-8")
    assert ris.count("TY  - JOUR") == n and ris.count("ER  - ") == n
    assert "TI  - Paper 0 about" in ris and "DO  - 10.1000/p0" in ris and "PY  - 2020" in ris and "AB  - " in ris
    assert "DB  - Scopus" in ris
    assert "N1  - " not in ris
    bib = f["bib"].read_text(encoding="utf-8")
    assert bib.count("@article{") == n and "note = " not in bib and "doi = {10.1000/p0}" in bib
    with open(f["csv"], newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == n
    assert list(rows[0]) == ["record_id", "title", "authors", "year", "journal", "doi", "url", "abstract",
                             "source_databases"]
    assert rows[0]["source_databases"] == "Scopus"
    for path in f.values():   # no field or note naming a decision, an evaluation or a model
        for line in path.read_text(encoding="utf-8").splitlines():
            head = line.split("=")[0] if path.suffix == ".bib" else line[:6]
            assert not JUDGED.search(head), (path.name, line)
    assert not JUDGED.search(",".join(rows[0]))


def test_plain_export_while_labelling_open(project_open):  # noqa: F811
    """Scenario "Export while labelling is open": the CLI and the page's export write the same three files with every
    unique record and its bibliographic fields, while the development sample has open labels."""
    p = project_open
    n = p.store.scalar("SELECT COUNT(*) FROM records")
    dev = p.store.scalar("SELECT sample_id FROM samples WHERE name='development'")
    assert dev and not p.store.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=?", (dev,))   # labels open
    r = runner.invoke(cli_app, ["export", "references", "--corpus", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert f"{n} records" in r.output and "exports/references/corpus.ris" in r.output
    _check(p, n)
    first = {k: v.read_bytes() for k, v in _files(p).items()}
    for v in _files(p).values():
        v.unlink()
    c = TestClient(create_app(p))
    j = c.post("/api/outputs/corpus").json()
    assert j["n"] == n and sorted(j["files"]) == sorted(f"exports/references/corpus.{e}" for e in ("bib", "csv", "ris"))
    _check(p, n)
    assert {k: v.read_bytes() for k, v in _files(p).items()} == first   # page and CLI write the same files
    got = c.get("/api/outputs/file", params={"path": "exports/references/corpus.ris"})
    assert got.status_code == 200 and got.text.count("TY  - JOUR") == n


def test_corpus_refuses_which_and_all(project):  # noqa: F811
    """`--corpus` is its own export: with `--which` or `--all` it is refused and writes nothing."""
    p = project
    for extra in (["--which", "include"], ["--all"]):
        r = runner.invoke(cli_app, ["export", "references", "--corpus", *extra, "--dir", str(p.root)])
        assert r.exit_code == 1 and "--corpus" in r.output
    assert not _files(p)["ris"].exists()


def test_corpus_export_card_node():
    """The export area of Recall and report has the plain corpus export, which calls POST /api/outputs/corpus and
    links the three files; the card says it carries no decision."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, out, run_node
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    card = re.search(r'<div[^>]*id="out-corpus-card"[^>]*>(.*?)</div>\s*</div>', page, re.S).group(1)
    assert "Plain corpus export" in card and 'id="out-corpus"' in card and "no screening decision" in card
    js = (STATIC / "outputs.js").read_text(encoding="utf-8")
    r = run_node(f"""{ESC}
{PAGE}
global.location = {{hash: ''}}; window.addEventListener = () => {{}};
replies["/api/outputs/corpus"] = {{n: 3, files: ["exports/references/corpus.ris", "exports/references/corpus.bib",
                                                "exports/references/corpus.csv"]}};
{js}
await $("#out-corpus").onclick(); await flush();
{out('{calls: calls.map((c) => c.slice(0, 2)), msg: $("#out-corpus-msg").innerHTML}')}
""")
    assert r["calls"] == [["/api/outputs/corpus", "POST"]]
    assert "3 records" in r["msg"] and "corpus.ris" in r["msg"] and "corpus.csv" in r["msg"]
