"""C3 unify-stage-pages: what the pages read when they open (no request to any backend)."""
import json
import zipfile

from fastapi.testclient import TestClient

from tests.unit.helpers import no_requests
from tests.unit.test_pass_backends import pp  # noqa: F401  (a fixture)
from tests.unit.test_screen_compare import _template, api, cli, fresh  # noqa: F401  (_template is a fixture)
from wider.core.models import EmbeddingConfig, ProviderConfig
from wider.core.project import Project
from wider.core.recent import RecentProjects
from wider.ui.app import create_app


def _ollama_project(root, title):
    p = Project.init(root, platforms=["scopus"], title=title)
    p.config.providers.append(ProviderConfig(name="o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434"))
    p.config.embeddings.append(EmbeddingConfig(name="oe", kind="ollama", model="nomic-embed-text",
                                               base_url="http://127.0.0.1:11434"))
    p.config.default_provider = "o"
    p.save()
    p.close()
    return root


def test_page_open_routes_make_no_request(tmp_path, monkeypatch):
    a = _ollama_project(tmp_path / "a", "Alpha")
    b = _ollama_project(tmp_path / "b", "Beta")
    recent = tmp_path / "wider_home" / "recent_projects.json"
    RecentProjects(recent).add(b)
    seen = no_requests(monkeypatch)
    c = TestClient(create_app(Project.load(a), recent_file=recent))
    for path in ("/api/screen", "/api/providers/consent", "/api/embeddings/consent", "/api/project/summary",
                 "/api/providers", "/api/embeddings", "/api/stages", "/api/home"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text)
    assert {p["title"] for p in c.get("/api/home").json()["projects"]} == {"Alpha", "Beta"}
    llm = next(r for r in c.get("/api/providers/consent").json() if r["name"] == "o")
    assert llm["locality_label"] == "unknown, press Check"
    assert seen == []


# ----- uploads marked final (2.2) and the hidden-set notes (2.3) -----
RIS = """TY  - JOUR
TI  - Team meetings after errors
PY  - 2020
DO  - 10.1000/abc
AB  - A synthetic abstract about meetings.
ER  -
"""
RIS_2 = RIS.replace("10.1000/abc", "10.1000/def").replace("Team meetings", "Shift handovers")


def _cli(*args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    return CliRunner().invoke(cli_app, list(args), env={"COLUMNS": "200"})


def _imported(tmp_path):
    from wider.core.models import BenchmarkPaper
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    (tmp_path / "s.ris").write_text(RIS)
    add_file(p, "scopus", tmp_path / "s.ris")
    import_all(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/abc", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="Not retrieved anywhere", set="Validation")])
    return p


def _n_final(p):
    return p.store.scalar("SELECT COUNT(*) FROM history WHERE kind='uploads_final'")


def test_uploads_final_mark_matches_cli(tmp_path):
    from wider.core import journal

    p = _imported(tmp_path)
    d = ["--dir", str(p.root)]
    c = TestClient(create_app(p))
    assert c.get("/api/coverage").json()["validation_included"] is False
    # the page marks the uploads final
    r = c.post("/api/import/final")
    assert r.status_code == 200, r.text
    at, cid = r.json()["at"], r.json()["change_id"]
    assert at and cid and r.json()["already"] is False
    hist = c.get("/api/history?kind=uploads_final").json()
    assert [h["change_id"] for h in hist] == [cid] and hist[0]["kind_label"] == "uploads marked final"
    out = _cli("history", "list", "--kind", "uploads_final", *d)
    assert out.exit_code == 0 and "uploads marked final" in out.output
    # the Validation set is reported now, with no peek logged
    cov = c.get("/api/coverage").json()
    assert cov["validation_included"] and not cov["validation_peeked"]
    assert set(cov["scopes"]["platform:scopus"]) == {"Development", "Validation"}
    out = _cli("coverage", *d)
    assert out.exit_code == 0 and "Validation: 0 of 1" in out.output and "hidden until" not in out.output
    assert p.store.get_meta("validation_peeked_at") is None
    # marked twice: the existing time, no new History entry (page and command)
    r2 = c.post("/api/import/final")
    assert r2.status_code == 200 and r2.json()["at"] == at and r2.json()["already"] is True
    out = _cli("import", "final", *d)
    assert out.exit_code == 0 and "already marked final at" in out.output and at in out.output
    assert _n_final(p) == 1
    # undo: uploads are accepted again and the Validation set is hidden again
    out = _cli("history", "undo", str(cid), "--count-seen", *d)
    assert out.exit_code == 0, out.output
    p = Project.load(p.root)
    assert p.store.get_meta("uploads_final_at") is None
    assert c.get("/api/coverage").json()["validation_included"] is False
    (tmp_path / "t.ris").write_text(RIS_2)
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("t.ris", RIS_2.encode())})
    assert r.status_code == 200, r.text
    # the command line marks it the same way, naming the undo
    assert c.post("/api/import/run").status_code == 200
    out = _cli("import", "final", *d)
    assert out.exit_code == 0 and "uploads marked final at" in out.output and "wider history undo" in out.output
    p = Project.load(p.root)
    assert len(journal.changes(p, kind="uploads_final")) == 2
    # a mark made while the Validation set was empty: its undo records nothing, a later upload shows no notice
    from wider.core.models import BenchmarkPaper
    from wider.search.coverage import unlocks
    e = Project.init(tmp_path / "empty", platforms=["scopus"])
    (tmp_path / "e.ris").write_text(RIS)
    _cli("import", "add", "scopus", str(tmp_path / "e.ris"), "--dir", str(e.root))
    _cli("import", "run", "--dir", str(e.root))
    e.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/abc", set="Development")])
    ce = TestClient(create_app(Project.load(e.root)))
    mark = ce.post("/api/import/final").json()["change_id"]
    r = ce.post(f"/api/history/{mark}/undo")
    assert r.status_code == 200 and r.json()["notice"] is None, r.text
    r = ce.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("t.ris", RIS_2.encode())})
    assert r.status_code == 200 and r.json()["notice"] is None, r.text
    assert unlocks(Project.load(e.root)) == [] and ce.get("/api/coverage").json()["validation_included"] is False


def test_uploads_final_refusals(tmp_path):
    from wider.ingest.importer import add_file

    # nothing imported yet
    p = Project.init(tmp_path / "e", platforms=["scopus"])
    c = TestClient(create_app(p))
    r = c.post("/api/import/final")
    assert r.status_code == 409 and "no files imported yet" in r.json()["detail"]
    out = _cli("import", "final", "--dir", str(p.root))
    assert out.exit_code == 1 and "no files imported yet" in out.output
    assert _n_final(p) == 0
    # a file saved but not imported
    p = _imported(tmp_path)
    c = TestClient(create_app(p))
    (tmp_path / "t.ris").write_text(RIS_2)
    add_file(p, "scopus", tmp_path / "t.ris")
    r = c.post("/api/import/final")
    detail = r.json()["detail"]
    assert r.status_code == 409 and "1 file is saved but not imported yet" in detail
    assert "scopus/t.ris" in detail and "Import the uploaded files first" in detail
    out = _cli("import", "final", "--dir", str(p.root))
    assert out.exit_code == 1 and "1 file is saved but not imported yet" in out.output and "scopus/t.ris" in out.output
    assert _n_final(p) == 0 and c.get("/api/coverage").json()["validation_included"] is False
    # a file that cannot be read is named with its parse error
    (p.path("inputs", "scopus") / "t.ris").unlink()
    (p.path("inputs", "scopus") / "broken.xlsx").write_bytes(b"not a spreadsheet")
    r = c.post("/api/import/final")
    detail = r.json()["detail"]
    assert r.status_code == 409 and "scopus/broken.xlsx cannot be read" in detail
    assert "Fix or delete it in inputs/scopus/, then try again" in detail
    out = _cli("import", "final", "--dir", str(p.root))
    assert out.exit_code == 1 and "broken.xlsx cannot be read" in out.output
    assert _n_final(p) == 0


def test_redo_of_final_mark_rechecks_and_names_the_redo(tmp_path, monkeypatch):
    from wider.ingest.importer import add_file, import_all

    p = _imported(tmp_path)
    d = ["--dir", str(p.root)]
    c = TestClient(create_app(p))
    cid = c.post("/api/import/final").json()["change_id"]
    undo_id = c.post(f"/api/history/{cid}/undo?seen_choice=count").json()["change_id"]
    # a file saved after the undo stops the redo (page and command), nothing changes
    (tmp_path / "t.ris").write_text(RIS_2)
    add_file(p, "scopus", tmp_path / "t.ris")
    r = c.post(f"/api/history/{undo_id}/undo")
    assert r.status_code == 409 and "cannot be marked final again" in r.json()["detail"]
    assert "1 file is saved but not imported yet: scopus/t.ris" in r.json()["detail"]
    out = _cli("history", "undo", str(undo_id), *d)
    assert out.exit_code == 1 and "1 file is saved but not imported yet" in out.output
    p = Project.load(p.root)
    assert p.store.get_meta("uploads_final_at") is None
    assert c.get("/api/coverage").json()["validation_included"] is False
    # the undo (the set was shown, no version frozen) recorded an unlock: B2 now counts as seen
    from wider.ingest.importer import final_at
    from wider.search.coverage import latest_unlock_no, unlocks, validation_papers
    q = Project.load(p.root)
    first_mark = unlocks(q)[0]["final_before"]
    assert [(x["cause"], x["counts_as_seen"]) for x in unlocks(q)] == [("uploads", 1)] and validation_papers(q) == []
    # once imported the redo goes through; it stores the current latest unlock number, so the set opens again
    import_all(p)
    monkeypatch.setattr("wider.core.journal.utcnow", lambda: "2031-01-02T03:04:05+00:00")   # the redo's own time
    out = _cli("history", "undo", str(undo_id), *d)
    assert out.exit_code == 0, out.output
    q = Project.load(p.root)
    assert q.store.get_meta("uploads_final_unlock_no") == str(latest_unlock_no(q)) == "1"
    assert c.get("/api/coverage").json()["validation_included"] is True
    # a later upload records an unlock whose notice and row name the redo's time, not the first mark's
    redo_at = final_at(q)
    assert redo_at == "2031-01-02T03:04:05+00:00" != first_mark
    r = c.post("/api/import/upload", data={"platform": "scopus"},
               files={"file": ("u.ris", RIS.replace("10.1000/abc", "10.1000/ghi").encode())})
    assert r.status_code == 200, r.text
    assert r.json()["notice"].startswith(f"Uploads reopened: u.ris was added after they were marked final on {redo_at[:10]}.")
    assert unlocks(Project.load(p.root))[-1]["final_before"] == redo_at


def journal_last(root):
    p = Project.load(root)
    try:
        return p.store.scalar("SELECT MAX(change_id) FROM history")
    finally:
        p.close()


def _three_shown(tmp_path):
    """Imported, three Validation papers (none retrieved), the uploads marked final: the set is shown."""
    from wider.core.models import BenchmarkPaper
    from wider.ingest.importer import mark_final

    p = _imported(tmp_path)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/abc", set="Development")]
                      + [BenchmarkPaper(benchmark_id=f"B{i}", title=f"Validation paper {i}", doi=f"10.1000/v{i}",
                                        set="Validation") for i in (2, 3, 4)])
    mark_final(p)
    return p


def _unlocks(p):
    from wider.search.coverage import unlocks
    return unlocks(Project.load(p.root))


def _fake_chain(monkeypatch, saves=True):
    """Citation chaining with no request: ``chain`` finds one work and ``export_chain`` saves (or not) one file."""
    from wider.prior import chain

    def fake_chain(project, direction="both", limit=None, client=None):
        return {"n_new": 1 if saves else 0}

    def fake_export(project):
        if not saves:
            return {"file": None, "n_exported": 0}
        d = project.path("inputs", "citation_chain")
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"chain_{len(list(d.iterdir())) + 1}.ris"
        f.write_text(RIS.replace("10.1000/abc", f"10.1000/c{f.stem}"))
        return {"file": str(f), "n_exported": 1}
    monkeypatch.setattr(chain, "chain", fake_chain)
    monkeypatch.setattr(chain, "export_chain", fake_export)


def test_upload_while_final_reopens_the_search(tmp_path, monkeypatch):
    from wider.construct import artifacts
    from wider.core import journal
    from wider.ingest.importer import final_at, mark_final

    p = _three_shown(tmp_path)
    d = ["--dir", str(p.root)]
    c = TestClient(create_app(p))
    day = final_at(p)[:10]
    n_sources = p.store.scalar("SELECT COUNT(*) FROM sources")
    n_records = p.store.scalar("SELECT COUNT(*) FROM raw_records")
    # no frozen query: the choice is asked all the same (unlock-seen-choice D3); without it nothing is taken
    plan = c.get("/api/import/sources?file=t.ris").json()
    assert plan["upload_choice"] is True and plan["upload_notice"].endswith(artifacts.CHOICE_TAIL)
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("t.ris", RIS_2.encode())})
    assert r.status_code == 400 and p.store.scalar("SELECT COUNT(*) FROM sources") == n_sources
    # the page takes the file with the choice and shows the notice
    r = c.post("/api/import/upload", data={"platform": "scopus", "seen_choice": "count"},
               files={"file": ("t.ris", RIS_2.encode())})
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == (f"Uploads reopened: t.ris was added after they were marked final on {day}. Earlier "
                                  f"imports and decisions are kept. {artifacts.outcome_line('count', 3)}")
    u = _unlocks(p)
    assert [(x["cause"], x["counts_as_seen"], x["final_before"][:10]) for x in u] == [("uploads", 1, day)]
    assert {s["benchmark_id"] for s in u[0]["seen"]} == {"B1", "B2", "B3", "B4"}
    q = Project.load(p.root)
    ch = journal.changes(q, "uploads_reopened")
    assert [x["summary"] for x in ch] == ["uploads reopened: t.ris added (counted as seen)"] and not ch[0]["undoable"]
    assert len(journal.changes(q, "uploads_final")) == 1   # the earlier mark stays listed
    out = _cli("import", "list", *d)
    assert out.exit_code == 0 and "uploads not marked final" in out.output
    # earlier imports and records are unchanged; the new file is imported beside them
    # the one-record file matches no seed paper, so it starts out of deduplication (D32 F8): the only notice
    assert c.post("/api/import/run").json()["notice"].startswith("t.ris is kept out of deduplication until you include it")
    assert q.store.scalar("SELECT COUNT(*) FROM sources") == n_sources + 1
    assert q.store.scalar("SELECT COUNT(*) FROM raw_records") == n_records + 1
    # an identical file while final records nothing: the same bytes under another name, page and command line
    mark_final(Project.load(p.root))
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("copy.ris", RIS.encode())})
    assert r.status_code == 200 and r.json()["notice"] is None, r.text
    (tmp_path / "copy2.ris").write_text(RIS)
    out = _cli("import", "add", "scopus", str(tmp_path / "copy2.ris"), *d)
    assert out.exit_code == 0 and "Uploads reopened" not in out.output, out.output
    out = _cli("import", "run", *d)   # nothing waiting
    assert out.exit_code == 0 and "Uploads reopened" not in out.output and len(_unlocks(p)) == 1
    # the command line: `import add` takes a new file with the notice (exit 0)
    day = final_at(Project.load(p.root))[:10]
    (tmp_path / "u.ris").write_text(RIS.replace("10.1000/abc", "10.1000/ghi"))
    out = _cli("import", "add", "scopus", str(tmp_path / "u.ris"), *d)
    assert out.exit_code == 0, out.output
    assert f"Uploads reopened: u.ris was added after they were marked final on {day}." in " ".join(out.output.split())
    assert [x["cause"] for x in _unlocks(p)] == ["uploads", "uploads"]
    # `import run` with a file saved straight into inputs/ names it
    assert _cli("import", "run", *d).exit_code == 0
    mark_final(Project.load(p.root))
    (p.path("inputs", "scopus") / "w.ris").write_text(RIS.replace("10.1000/abc", "10.1000/jkl"))
    out = _cli("import", "run", *d)
    assert out.exit_code == 0 and "Uploads reopened: w.ris was added" in " ".join(out.output.split()), out.output
    assert len(_unlocks(p)) == 3
    # citation chaining: a run that saves a file reopens the uploads (page and command), one that saves none does not
    mark_final(Project.load(p.root))
    _fake_chain(monkeypatch, saves=False)
    r = c.post("/api/prior-reviews/chain")
    assert r.status_code == 200 and r.json()["notice"] is None and len(_unlocks(p)) == 3, r.text
    _fake_chain(monkeypatch)
    assert c.get("/api/prior-reviews").json()["chain_choice"] is False
    r = c.post("/api/prior-reviews/chain")
    assert r.status_code == 200, r.text
    assert r.json()["notice"].startswith("Uploads reopened: chain_1.ris was added after they were marked final on")
    assert len(_unlocks(p)) == 4
    c.post("/api/import/run")
    mark_final(Project.load(p.root))
    out = _cli("prior-reviews", "chain", *d)
    assert out.exit_code == 0 and "Uploads reopened: chain_2.ris was added" in " ".join(out.output.split()), out.output
    assert [x["cause"] for x in _unlocks(p)] == ["uploads"] * 5
    from tests.unit.test_outputs import unlock_json
    assert [(x["unlock_no"], x["cause"], x["from_version"]) for x in unlock_json(Project.load(p.root))] == [
        (n, "uploads", None) for n in range(1, 6)]


def _frozen_and_final(tmp_path, name="r"):
    """Synonyms v3 the frozen query, Validation papers B002 and B003 shown, the uploads marked final."""
    from wider.construct import artifacts
    from wider.core.models import BenchmarkPaper
    from wider.ingest.importer import add_file, import_all, mark_final

    p = Project.init(tmp_path / name, platforms=["scopus"])
    (tmp_path / f"{name}.ris").write_text(RIS)
    add_file(p, "scopus", tmp_path / f"{name}.ris")
    import_all(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/abc", set="Development"),
                       BenchmarkPaper(benchmark_id="B002", title="Validation paper two", doi="10.1000/v2", set="Validation"),
                       BenchmarkPaper(benchmark_id="B003", title="Validation paper three", doi="10.1000/v3", set="Validation")])
    for t in ("a*", "b*", "c*"):
        artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["meeting*", t]}],
                                                   "logic": {"and": ["a"]}}, origin="manual")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    mark_final(p)
    return p


def test_upload_while_final_with_frozen_query_asks(tmp_path, monkeypatch):
    from wider.construct.artifacts import CHOICE_TAIL, CHOOSE_LINE
    from wider.core import journal
    from wider.ingest.importer import final_at
    from wider.search.coverage import validation_papers

    p = _frozen_and_final(tmp_path)
    d = ["--dir", str(p.root)]
    c = TestClient(create_app(p))
    day = final_at(p)[:10]
    ask = (f"The uploads were marked final on {day} and the frozen query v3 stands; adding new.ris reopens them; "
           f"2 validation papers have been shown. {CHOICE_TAIL}")
    plan = c.get("/api/import/sources?file=new.ris").json()
    assert plan["upload_choice"] is True and plan["upload_notice"] == ask
    # without a choice nothing is taken: the route answers 400 with the notice, the command exits 2
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("new.ris", RIS_2.encode())})
    assert r.status_code == 400 and r.json()["detail"] == ask
    (tmp_path / "new.ris").write_text(RIS_2)
    out = _cli("import", "add", "scopus", str(tmp_path / "new.ris"), *d)
    flat = " ".join(out.output.split())
    assert out.exit_code == 2 and ask in flat and CHOOSE_LINE in flat, out.output
    out = _cli("import", "add", "scopus", str(tmp_path / "new.ris"), "--count-seen", "--keep-validation", *d)
    assert out.exit_code == 2 and CHOOSE_LINE in " ".join(out.output.split())
    assert sorted(f.name for f in p.path("inputs", "scopus").iterdir()) == ["r.ris"] and _unlocks(p) == []
    upload_dir = p.path("inputs", ".upload")
    assert not upload_dir.exists() or list(upload_dir.iterdir()) == []
    # chaining asks the same before any request; the command exits 2 and nothing runs
    _fake_chain(monkeypatch)
    pr = c.get("/api/prior-reviews").json()
    assert pr["chain_choice"] is True and "adding the chaining file reopens them" in pr["chain_notice"]
    r = c.post("/api/prior-reviews/chain")
    assert r.status_code == 400 and r.json()["detail"] == pr["chain_notice"]
    out = _cli("prior-reviews", "chain", *d)
    assert out.exit_code == 2 and CHOOSE_LINE in " ".join(out.output.split())
    assert not p.path("inputs", "citation_chain").exists() and _unlocks(p) == []
    # Count them as seen (the page)
    r = c.post("/api/import/upload", data={"platform": "scopus", "seen_choice": "count"},
               files={"file": ("new.ris", RIS_2.encode())})
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == (f"Uploads reopened: new.ris was added after they were marked final on {day}. Earlier "
                                  "imports and decisions are kept; the frozen query v3 still stands. Counted as seen: "
                                  "the 2 validation papers shown so far no longer validate the search.")
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(p)] == [("uploads", 1)]
    q = Project.load(p.root)
    assert journal.changes(q, "uploads_reopened")[0]["summary"] == "uploads reopened: new.ris added (counted as seen)"
    assert validation_papers(q) == []
    from tests.unit.test_dry_run import _labels
    from wider.search.coverage import check
    assert {k for k, v in _labels(check(q)).items() if v == "used"} == {"B002", "B003"}   # "Validation, used"

    # Keep them as validation (the command line)
    k = _frozen_and_final(tmp_path, "k")
    day = final_at(k)[:10]
    out = _cli("import", "add", "scopus", str(tmp_path / "new.ris"), "--keep-validation", "--dir", str(k.root))
    assert out.exit_code == 0, out.output
    assert (f"Uploads reopened: new.ris was added after they were marked final on {day}. Earlier imports and "
            "decisions are kept; the frozen query v3 still stands. Kept as validation: the 2 validation papers shown "
            "so far still count; every output states this choice.") in " ".join(out.output.split())
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(k)] == [("uploads", 0)]
    q = Project.load(k.root)
    assert journal.changes(q, "uploads_reopened")[0]["summary"] == "uploads reopened: new.ris added (kept as validation)"
    assert "uploads not marked final" in _cli("import", "list", "--dir", str(k.root)).output
    assert {b.benchmark_id for b in validation_papers(q)} == {"B002", "B003"}
    # a later save of v4 records an unlock with cause save, after which B002 and B003 are seen
    from wider.construct import artifacts
    artifacts.save_new_version(q, "synonyms", {"blocks": [{"key": "a", "name": "A", "terms": ["d*"]}],
                                               "logic": {"and": ["a"]}}, origin="manual", seen_choice="count")
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(k)] == [("uploads", 0), ("save", 1)]
    assert validation_papers(Project.load(k.root)) == []
    # chaining with a choice runs (page)
    ch = _frozen_and_final(tmp_path, "ch")
    r = TestClient(create_app(ch)).post("/api/prior-reviews/chain?seen_choice=keep")
    assert r.status_code == 200, r.text
    assert "Kept as validation: the 2 validation papers" in r.json()["notice"]
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(ch)] == [("uploads", 0)]


def test_uploads_with_no_paper_shown_count_nothing(tmp_path):
    from wider.core.models import BenchmarkPaper
    from wider.ingest.importer import final_at

    p = _frozen_and_final(tmp_path)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/abc", set="Development")])
    c = TestClient(create_app(p))
    day = final_at(p)[:10]
    assert c.get("/api/import/sources?file=new.ris").json()["upload_choice"] is False
    r = c.post("/api/import/upload", data={"platform": "scopus"}, files={"file": ("new.ris", RIS_2.encode())})
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == (f"Uploads reopened: new.ris was added after they were marked final on {day}. "
                                  "Earlier imports and decisions are kept; the frozen query v3 still stands.")
    u = _unlocks(p)
    assert [(x["cause"], x["counts_as_seen"]) for x in u] == [("uploads", 0)]
    assert "uploads not marked final" in _cli("import", "list", "--dir", str(p.root)).output
    from wider.search.coverage import validity_note
    assert validity_note(Project.load(p.root))["lines"] == []   # no choice was asked, so no note line


def test_hidden_set_notes(tmp_path):
    from pathlib import Path

    p = _imported(tmp_path)
    out = _cli("coverage", "--dir", str(p.root))
    assert ("validation set hidden until the synonyms artifact is frozen or the uploads are marked final "
            "(wider construct freeze synonyms N, or wider import final)") in out.output
    static = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
    js = (static / "app.js").read_text()
    assert '"Validation set hidden until the query is frozen or the uploads are marked final."' in js
    assert '"Development and validation sets."' in js
    assert "validation set hidden until the query is frozen or the uploads are marked final" in js  # Ranking heading
    html = (static / "index.html").read_text()
    assert "stays hidden until the query is frozen or the uploads are marked final" in html
    assert 'title="Before the query is frozen or the uploads are marked final, looking at the validation set' in html
    _cli("import", "final", "--dir", str(p.root))
    out = _cli("coverage", "--dir", str(p.root))
    assert "hidden until" not in out.output and "Validation: 0 of 1" in out.output


def test_rank_validate_after_uploads_final(tmp_path):
    from wider.construct import artifacts
    from wider.core.models import BenchmarkPaper
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=16)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / "a.ris").write_text("\n".join(
        f"TY  - JOUR\nTI  - Synthetic paper {i} on topic {i % 3}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - words {i}\nER  -\n"
        for i in range(6)))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="x", doi="10.1000/p3", set="Validation")])
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"a": "synthetic paper on topic one " * 12, "b": "synthetic paper on topic two " * 12}}, origin="manual")
    rank(p, top_n=1)
    out = _cli("rank", "validate", "--dir", str(p.root))
    assert out.exit_code == 0 and "B1" in out.output and "B2" not in out.output, out.output
    _cli("import", "final", "--dir", str(p.root))
    assert Project.load(p.root).store.get_meta("uploads_final_unlock_no") == "0"   # no unlock row yet
    out = _cli("rank", "validate", "--dir", str(p.root))
    assert out.exit_code == 0 and "B2" in out.output and "Validation" in out.output, out.output


def _ranked_fake(tmp_path):
    from wider.construct import artifacts
    from wider.core.models import BenchmarkPaper
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=16)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / "a.ris").write_text("\n".join(
        f"TY  - JOUR\nTI  - Synthetic paper {i} on topic {i % 3}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - words {i}\nER  -\n"
        for i in range(6)))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="x", doi="10.1000/p3", set="Validation")])
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"a": "synthetic paper on topic one " * 12,
                                                          "b": "synthetic paper on topic two " * 12}}, origin="manual")
    return p


def test_rank_validate_early_peek_is_warned_and_recorded(tmp_path):
    """Scenario "Early peek at the validation set" (MODIFIED "Validation against the benchmark", round 7, F1): asking
    for the validation set before the freeze first asks whether the papers shown count as seen; without the choice
    the CLI exits 2 and the route answers ``choice_needed`` with nothing recorded; each branch prints its outcome line,
    records the papers with ``counted`` true or false and one ``validation_peek`` per look; once the set is unlocked
    nothing more is recorded."""
    import json

    from wider.core import journal
    from wider.rank.score import rank
    from wider.search.coverage import SEEN_BY_LOOK

    p = _ranked_fake(tmp_path)
    rank(p, top_n=1)
    d = ["--dir", str(p.root)]
    warn = "validation set shown before the query was frozen; this look is logged in the history"

    def nothing_recorded():
        q = Project.load(p.root)
        assert q.store.get_meta("validation_peeked_at") is None and q.store.get_meta(SEEN_BY_LOOK) is None
        assert journal.changes(q, kind="validation_peek") == []

    # without --validation: hidden, no warning, nothing recorded
    out = _cli("rank", "validate", *d)
    assert out.exit_code == 0 and "B2" not in out.output and warn not in out.output, out.output
    nothing_recorded()
    # --validation without a choice (and with both): the asking notice, nothing shown, exit 2
    for extra in ([], ["--count-seen", "--keep-validation"]):
        out = _cli("rank", "validate", "--validation", *extra, *d)
        flat = " ".join(out.output.split())
        assert out.exit_code == 2 and "B2" not in out.output, out.output
        assert "Choose one: --count-seen or --keep-validation. Nothing was done." in flat
        if not extra:
            assert "Validation set requested before the query was frozen; it holds 1 paper." in flat, flat
    nothing_recorded()
    c = TestClient(create_app(Project.load(p.root)))
    v = c.get("/api/rank/validate?validation=true").json()
    assert v["choice_needed"] is True and v["rows"] == [] and "The choice is final." in v["notice"]
    assert c.get("/api/rank/validate?validation=true&seen_choice=maybe").status_code == 400
    nothing_recorded()
    # the CLI look kept as validation: the paper shown, the notice and its outcome line, counted false
    out = _cli("rank", "validate", "--validation", "--keep-validation", *d)
    flat = " ".join(out.output.split())
    assert out.exit_code == 0 and "B2" in out.output, out.output
    assert (f"{warn}. Kept as validation: the 1 validation paper shown still counts; every output states this "
            "choice.") in flat
    p = Project.load(p.root)
    assert p.store.get_meta("validation_peeked_at") and p.store.get_meta("validation_peeks") == "1"
    ch = journal.changes(p, kind="validation_peek")
    assert len(ch) == 1 and not ch[0]["undoable"]
    assert ch[0]["summary"] == "validation set looked at before the freeze: kept as validation"
    looks = json.loads(p.store.get_meta(SEEN_BY_LOOK))
    assert [(e["benchmark_id"], e["unlock_no"], e["counted"]) for e in looks] == [("B2", 0, False)]
    # the API look counted as seen: the outcome line, counted true, a second permanent entry
    c = TestClient(create_app(p))
    v = c.get("/api/rank/validate").json()
    assert v["validation_peeked"] is False and v["validation_included"] is False
    v = c.get("/api/rank/validate?validation=true&seen_choice=count").json()
    assert v["validation_peeked"] is True and v["validation_included"] is True and "Validation" in v["summary"]
    assert v["notice"] == f"{warn}. Counted as seen: the 1 validation paper shown no longer validates the search."
    assert p.store.get_meta("validation_peeks") == "2"
    ch = journal.changes(p, kind="validation_peek")
    assert len(ch) == 2 and ch[0]["summary"] == "validation set looked at before the freeze: counted as seen"
    looks = json.loads(p.store.get_meta(SEEN_BY_LOOK))
    assert [(e["benchmark_id"], e["unlock_no"], e["counted"]) for e in looks] == [("B2", 0, False), ("B2", 0, True)]
    # once unlocked: the set is shown with no warning and no new record
    assert _cli("import", "final", *d).exit_code == 0
    p = Project.load(p.root)
    peeked_at = p.store.get_meta("validation_peeked_at")
    out = _cli("rank", "validate", "--validation", *d)
    assert out.exit_code == 0 and "B2" in out.output and warn not in " ".join(out.output.split()), out.output
    v = TestClient(create_app(Project.load(p.root))).get("/api/rank/validate?validation=true").json()
    assert v["validation_peeked"] is False and v["validation_included"] is True
    p = Project.load(p.root)
    assert p.store.get_meta("validation_peeked_at") == peeked_at and p.store.get_meta("validation_peeks") == "2"
    assert len(journal.changes(p, kind="validation_peek")) == 2


def _ablation_project(root):
    """`_ranked_fake` with two validation papers (B2, B3, both in the corpus) and a chosen ranking."""
    from wider.core.models import BenchmarkPaper
    from wider.rank.score import rank

    root.mkdir()
    p = _ranked_fake(root)
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="x", doi="10.1000/p0", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="y", doi="10.1000/p3", set="Validation"),
                       BenchmarkPaper(benchmark_id="B3", title="z", doi="10.1000/p4", set="Validation")])
    rank(p, top_n=1)
    return p


ABL_ASKING = ("Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no "
              "longer validate the search; only papers added to Validation later can. Keep them as validation: they "
              "still count, and every output states that they were shown. The choice is final.")
ABL_WARN = "validation set shown before the query was frozen; this look is logged in the history"


def test_ablation_before_freeze_is_a_logged_look(tmp_path):
    """Scenario "Before the freeze" (rewrite of test_ablation_refuses_locked_validation; D3, Q1, F5): without a choice
    `wider rank ablate --validation` exits 2 and the route answers ``choice_needed``, nothing recorded or ranked; with
    the choice (CLI --count-seen, route seen_choice=keep) the validation columns, the notice and its outcome line, one
    permanent ``validation_peek`` change, the validity note's line, the default ranking and the rank stage result
    unchanged, and after a freeze the papers counted as seen or kept; after ``wider import final`` no look at all."""
    import json

    from tests.unit.test_dry_run import _labels, _versions
    from wider.construct import artifacts
    from wider.core import journal
    from wider.core.stage_result import last_result
    from wider.search.coverage import SEEN_BY_LOOK, check, validity_note

    for choice in ("count", "keep"):
        p = _ablation_project(tmp_path / choice)
        _versions(p, "a*", "b*")   # saved before the look: a save after it would revise the query (validation-reuse D2)
        d = ["--dir", str(p.root)]
        chosen, result = p.store.get_meta("default_ranking"), last_result(p, "rank")
        n_rankings = p.store.scalar("SELECT COUNT(*) FROM rankings")

        def nothing_recorded():
            q = Project.load(p.root)
            assert q.store.get_meta("validation_peeked_at") is None and q.store.get_meta(SEEN_BY_LOOK) is None
            assert journal.changes(q, kind="validation_peek") == []
            assert q.store.scalar("SELECT COUNT(*) FROM rankings") == n_rankings

        for extra in ([], ["--count-seen", "--keep-validation"]):
            out = _cli("rank", "ablate", "--validation", *extra, *d)
            flat = " ".join(out.output.split())
            assert out.exit_code == 2 and "Choose one: --count-seen or --keep-validation. Nothing was done." in flat, flat
            assert (ABL_ASKING in flat) == (not extra), flat
        c = TestClient(create_app(Project.load(p.root)))
        r = c.get("/api/rank/ablate?validation=true").json()
        assert r["choice_needed"] is True and r["notice"] == ABL_ASKING and r["rows"] == []
        assert c.get("/api/rank/ablate?validation=true&seen_choice=maybe").status_code == 400
        nothing_recorded()
        outcome = ("Counted as seen: the 2 validation papers shown no longer validate the search." if choice == "count"
                   else "Kept as validation: the 2 validation papers shown still count; every output states this choice.")
        if choice == "count":   # the CLI look
            out = _cli("rank", "ablate", "--validation", "--count-seen", *d)
            flat = " ".join(out.output.split())
            assert out.exit_code == 0 and f"{ABL_WARN}. {outcome}" in flat and "validation" in flat, flat
            assert "2/2" in flat
        else:   # the route look
            r = c.get("/api/rank/ablate?validation=true&seen_choice=keep").json()
            assert r["validation_peeked"] is True and r["notice"] == f"{ABL_WARN}. {outcome}"
            assert {row["validation found"] for row in r["rows"]} == {"2/2"}
        q = Project.load(p.root)
        assert q.store.get_meta("default_ranking") == chosen and last_result(q, "rank") == result
        ch = journal.changes(q, kind="validation_peek")
        assert len(ch) == 1 and not ch[0]["undoable"]
        assert ch[0]["summary"] == ("validation set looked at before the freeze: "
                                    + ("counted as seen" if choice == "count" else "kept as validation"))
        looks = json.loads(q.store.get_meta(SEEN_BY_LOOK))
        assert [(e["benchmark_id"], e["unlock_no"], e["counted"]) for e in looks] == [
            ("B2", 0, choice == "count"), ("B3", 0, choice == "count")]
        day = looks[0]["at"][:10]
        chose = "count them as seen" if choice == "count" else "keep them as validation"
        assert (f"Validation set looked at on {day} before the freeze (2 papers shown); the researcher chose to "
                f"{chose}.") in validity_note(q)["lines"]
        # after a freeze: counted papers are listed under "Validation, used", kept ones stay in the set
        artifacts.set_status(q, "synonyms", 2, "frozen")
        want = "used" if choice == "count" else "Validation"
        assert _labels(check(Project.load(p.root))) == {"B1": "Development", "B2": want, "B3": want}
    # after `wider import final` the set is open: the validation columns, no look notice, no validation_peek entry
    p = _ablation_project(tmp_path / "final")
    d = ["--dir", str(p.root)]
    assert _cli("import", "final", *d).exit_code == 0
    out = _cli("rank", "ablate", "--validation", *d)
    flat = " ".join(out.output.split())
    assert out.exit_code == 0 and "2/2" in flat and "this look is logged" not in flat, flat
    assert journal.changes(Project.load(p.root), kind="validation_peek") == []
    # the help text says it is a logged look
    help_ablate = " ".join(_cli("rank", "ablate", "--help").output.replace("│", " ").split())
    assert "this is a look, logged in the history" in help_ablate
    for cmd in (("coverage", "--help"), ("rank", "validate", "--help")):
        text = " ".join(_cli(*cmd).output.replace("│", " ").split())
        assert "even if the query is not frozen and the uploads are not marked final" in text, cmd
    # with no consent for a cloud embedding backend the choice is still asked first, then the consent request
    p = _ablation_project(tmp_path / "cloud")
    cfg = p.config.embeddings[0]
    p.config.embeddings = [EmbeddingConfig(name=cfg.name, kind="openai_compat", model="m",
                                           base_url="https://embeddings.example.invalid/v1", api_key_env="X_KEY")]
    p.save()
    c = TestClient(create_app(Project.load(p.root)))
    assert c.get("/api/rank/ablate?validation=true").json()["choice_needed"] is True
    assert c.get("/api/rank/ablate?validation=true&seen_choice=count").status_code == 403
    assert c.get("/api/rank/ablate").status_code == 403
    assert journal.changes(Project.load(p.root), kind="validation_peek") == []


def test_ablation_while_reopened_counts_as_seen(tmp_path):
    """The AND line of "Before the freeze" for a look while the query is reopened (F6; unlock-seen-choice D7): the
    choice is asked (exit 2 without it), then the "...while the query reopened..." notice with its outcome, the papers shown appended with unlock_no 1 and counted, one
    ``validation_peek`` change, and the re-freeze lists them under "Validation, used"."""
    import json

    from tests.unit.test_dry_run import _add_val, _labels, _versions
    from wider.construct import artifacts
    from wider.core import journal
    from wider.search.coverage import LOOK_REOPENED, SEEN_BY_LOOK, check, look_outcome

    p = _ablation_project(tmp_path / "r")
    d = ["--dir", str(p.root)]
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    new = _add_val(p, "10.1000/p5")   # entered after the unlock: the one paper the set holds now
    out = _cli("rank", "ablate", "--validation", *d)
    assert out.exit_code == 2 and "Choose one" in " ".join(out.output.split()), out.output
    out = _cli("rank", "ablate", "--validation", "--count-seen", *d)
    flat = " ".join(out.output.split())
    assert out.exit_code == 0 and "Choose one" not in flat, flat
    assert f"{LOOK_REOPENED.format(what='query is')}. {look_outcome('count', 1)}" in flat
    q = Project.load(p.root)
    looks = json.loads(q.store.get_meta(SEEN_BY_LOOK))
    assert [(e["benchmark_id"], e["unlock_no"], e["counted"]) for e in looks] == [(new, 1, True)]
    ch = journal.changes(q, kind="validation_peek")
    assert len(ch) == 1 and ch[0]["summary"] == "validation set shown while the query is reopened: counted as seen"
    artifacts.set_status(q, "synonyms", 4, "frozen")
    assert _labels(check(Project.load(p.root)))[new] == "used"


def _fake_seed_lookup(monkeypatch):
    """OpenAlex 'knows' every synthetic DOI except 10.1000/s.miss; nothing leaves the computer."""
    import types

    from wider.construct import seed
    monkeypatch.setattr(seed, "openalex_work", lambda doi, c: None if doi == "10.1000/s.miss" else {
        "source": "openalex", "title": f"Synthetic title {doi}", "year": 2001, "doi": doi,
        "abstract": " ".join(["w"] * 60), "first_author": "Someone"})
    monkeypatch.setattr(seed, "crossref_work", lambda doi, c: None)
    monkeypatch.setattr(seed, "openalex_search", lambda title, c: [])
    monkeypatch.setattr("wider.enrich.sources._client", lambda: types.SimpleNamespace(close=lambda: None))


def _seeded(root):
    from wider.construct import seed

    p = Project.init(root)
    seed.add_by_dois(p, ["10.1000/s.1", "10.1000/s.2", "10.1000/s.3"])
    assert _cli("lookups", "allow", "--dir", str(p.root)).exit_code == 0
    assert _cli("benchmark", "resolve", "--dir", str(p.root)).exit_code == 0
    seed.add_by_dois(p, ["10.1000/s.4", "10.1000/s.miss"])
    return p


def test_resolve_summary_counts_looked_up(tmp_path, monkeypatch):
    _fake_seed_lookup(monkeypatch)
    want = "1 resolved, 1 unresolved of 2 looked up; 3 already resolved"
    a = _seeded(tmp_path / "a")
    out = _cli("benchmark", "resolve", "--dir", str(a.root))
    assert out.exit_code == 0 and want in out.output, out.output
    b = _seeded(tmp_path / "b")
    d = TestClient(create_app(b)).post("/api/benchmark/resolve").json()
    assert d["summary"] == want and (d["resolved"], d["unresolved"], d["looked_up"], d["already"]) == (1, 1, 2, 3)
    # with --all (all=true) every paper is looked up again
    out = _cli("benchmark", "resolve", "--all", "--dir", str(a.root))
    assert "4 resolved, 1 unresolved of 5 looked up; 0 already resolved" in out.output, out.output
    d = TestClient(create_app(b)).post("/api/benchmark/resolve", params={"all": True}).json()
    assert d["summary"] == "4 resolved, 1 unresolved of 5 looked up; 0 already resolved"


def test_platforms_add_help_lists_catalogue():
    from wider.search.catalogue import catalogue

    text = " ".join(_cli("query", "platforms", "add", "--help").output.replace("│", " ").split())
    keys = [c["key"] for c in catalogue()]
    assert f"Catalogue key ({', '.join(keys)}) or a new key." in text


# ----- the Ranking embedding picker (4.4) -----
def test_embedding_choice_matches_cli(tmp_path, monkeypatch):
    """Choosing an embedding card (`POST /api/embeddings/default`) = `wider embed set-default NAME`: the same default,
    the same rows in `wider embed list --json`, one History entry, and nothing contacted."""
    import json

    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core import journal

    ui, cli = (Project.load(_ollama_project(tmp_path / n, "T")) for n in ("ui", "cli"))
    seen = no_requests(monkeypatch)
    n0 = len(journal.changes(ui))
    c = TestClient(create_app(ui))
    assert c.post("/api/embeddings/default", json={"name": "oe"}).status_code == 200
    r = CliRunner().invoke(cli_app, ["embed", "set-default", "oe", "--dir", str(cli.root)])
    assert r.exit_code == 0, r.output
    assert Project.load(ui.root).config.default_embedding == Project.load(cli.root).config.default_embedding == "oe"
    page = {e["name"]: e["default"] for e in c.get("/api/embeddings").json()["embeddings"]}
    listed = CliRunner().invoke(cli_app, ["embed", "list", "--json", "--dir", str(cli.root)])
    assert listed.exit_code == 0, listed.output
    assert page == {e["name"]: e["default"] for e in json.loads(listed.output)} and page["oe"] is True
    assert len(journal.changes(ui)) == n0 + 1 and journal.changes(ui)[0]["kind"] == "config"
    assert seen == []


def test_page_ablation_with_validation(tmp_path):
    """Ranking page, Ablation (scenarios "Checkbox while the validation set is locked" and "Validation set on the
    page"): while the validation set is hidden the "Include the validation set" checkbox stays enabled and says that
    ticking it is a logged look; the ticked box asks first and Cancel runs nothing, then the choice runs it with the
    look notice; once the uploads are marked final the ticked box sends `validation=true` with no step, and the table
    has the same readable headers as `wider rank ablate --validation`, with no underscore in any of them."""
    import re

    from tests.unit.test_backends_js_node import STATIC
    from tests.unit.test_shell_js_node import _stages_reply, app_page

    p = _ranked_fake(tmp_path)
    d = ["--dir", str(p.root)]

    def page(body, choice=None):
        c = TestClient(create_app(Project.load(p.root)))
        replies = {"/api/stages": _stages_reply({"setup": "done", "rank": "ready"}), "/api/project": c.get("/api/project").json(),
                   "/api/construct": c.get("/api/construct").json(), "/api/rank": c.get("/api/rank").json(),
                   "/api/stages/rank/results": []}
        # the real route answers the URLs the page builds; the one with the choice (which records the look) only for
        # the run whose dialog answers it
        urls = ["/api/rank/ablate?validation=true"] + ([f"/api/rank/ablate?validation=true&seen_choice={choice}"] if choice else [])
        reply = {u: c.get(u).json() for u in urls}
        return app_page(f"const rows = {json.dumps(reply)};"
                        "replies['/api/rank/ablate'] = (u) => rows[u];"
                        "global.askChoice = async (html) => { asked.push(html); return ask ? { act: ask } : null; };"
                        + (STATIC / "refine.js").read_text(encoding="utf-8") + "await flush();" + body, replies)

    out_js = ("console.log(JSON.stringify({disabled: $('#rk-ablate-val').disabled, why: $('#rk-ablate-val-why').textContent,"
              " whyHidden: $('#rk-ablate-val-why').hidden, asked: calls.filter(c => c[0].startsWith('/api/rank/ablate')).map(c => c[0]),"
              " dialogs: asked.length, table: $('#rk-ablation').innerHTML}));")
    run = "$('#rk-ablate-val').checked = true; await $('#rk-ablate').onclick(); await flush();"
    why = ("Include the validation set: it is hidden until the query is frozen or the uploads are final; including it "
           "now is logged, and you choose whether the papers shown count as seen")
    cancel = page("const asked = []; const ask = null;" + run + out_js)
    assert cancel["disabled"] is False and cancel["whyHidden"] is False and cancel["why"] == why
    assert cancel["asked"] == ["/api/rank/ablate?validation=true"] and cancel["dialogs"] == 1 and cancel["table"] == ""
    assert Project.load(p.root).store.get_meta("validation_peeks") is None   # Cancel ran nothing
    kept = page("const asked = []; const ask = 'keep';" + run + out_js, "keep")   # kept, so B2 still validates below
    assert kept["asked"] == ["/api/rank/ablate?validation=true", "/api/rank/ablate?validation=true&seen_choice=keep"]
    assert (f"{ABL_WARN}. Kept as validation: the 1 validation paper shown still counts; every output states this "
            "choice.") in kept["table"] and "validation found" in kept["table"]

    assert _cli("import", "final", *d).exit_code == 0
    opened = page("const asked = []; const ask = null;" + run + out_js)
    assert opened["disabled"] is False and opened["whyHidden"] is True
    assert opened["asked"] == ["/api/rank/ablate?validation=true"] and opened["dialogs"] == 0
    heads = re.findall(r"<th>(.*?)</th>", opened["table"])
    assert "validation found" in heads and "development median rank" in heads
    assert not any("_" in h for h in heads), heads
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    cli = CliRunner().invoke(cli_app, ["rank", "ablate", "--validation", *d], env={"COLUMNS": "400"})
    assert cli.exit_code == 0, cli.output
    lines = [x.split("┃")[1:-1] for x in cli.output.splitlines() if x.startswith("┃")]   # a long header wraps
    assert [" ".join(c[i].strip() for c in lines if c[i].strip()) for i in range(len(lines[0]))] == heads


# ----- the Screening page's model picker (7.1) -----
def test_screen_run_uses_the_picked_backend(tmp_path, monkeypatch):
    """The card picked on the Screening page goes to the run as `provider` and screens that run only: the run records
    backend c as `wider screen run --provider c` does, and `project.yaml` is byte-for-byte unchanged."""
    from tests.unit.test_screen import _responder
    from tests.unit.test_screen import project as screen_project
    from wider.providers import fake as fakemod

    p = screen_project.__wrapped__(tmp_path)
    p.config.providers = [ProviderConfig(name=n, kind="fake", model=f"fake-{n}") for n in ("a", "b", "c")]
    p.config.default_provider = "a"
    p.config.stage_providers = {"screen": "b"}
    p.save()
    root = p.root
    p.close()
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder({"calls": 0})))
    before = (root / "project.yaml").read_bytes()
    c = TestClient(create_app(Project.load(root)))
    r = c.post("/api/screen/run", json={"mode": "pass", "scope": "sample:development", "pass_name": "A", "provider": "c"})
    assert r.status_code == 200, r.text
    out = _cli("screen", "run", "sample:development", "--pass", "B", "--provider", "c", "--dir", str(root))
    assert out.exit_code == 0, out.output
    q = Project.load(root)
    runs = {r["run_id"]: (r["provider"], r["model"]) for r in q.store.query("SELECT run_id, provider, model FROM runs")}
    q.close()
    assert len(runs) == 2 and set(runs.values()) == {("c", "fake-c")}, runs
    assert all("__c__" in k for k in runs)
    assert (root / "project.yaml").read_bytes() == before


def test_limit_wording_page_and_help():
    """The Screening page's record limit and the `--limit` help of `wider screen run` and `wider screen protocol` say
    the same: the limit caps new model calls, and finished passes still decide every record."""
    import re
    from pathlib import Path

    from wider.cli import LIMIT_HELP

    html = (Path(__file__).parents[2] / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    page = re.search(r'<p id="sc-limit-why"[^>]*>([^<]*)</p>', html).group(1)
    assert 'aria-describedby="sc-limit-why"' in html.split('id="sc-limit"')[1].split(">")[0]
    flat = lambda s: re.sub(r"[\s│|]+", " ", s).strip()  # noqa: E731
    assert flat(page) == flat(LIMIT_HELP)
    assert "caps the new model calls" in page and "still decides every record, without new calls" in page
    for cmd in ("run", "protocol"):
        out = _cli("screen", cmd, "--help")
        assert out.exit_code == 0 and flat(LIMIT_HELP) in flat(out.output), out.output


# ----- query version per upload (8.1) and the uploads listing (8.2) -----
SYN = {"blocks": [{"key": "a", "name": "A", "terms": ["meeting*"]}], "logic": {"and": ["a"]}}


def test_import_records_the_frozen_query_version(tmp_path):
    """Each imported file records the version it was searched with (2a, D8): the frozen query, else the newest
    version of any status; before any version the store holds NULL and the listing says "not recorded"."""
    from wider.construct import artifacts
    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    (tmp_path / "a.ris").write_text(RIS)
    (tmp_path / "b.ris").write_text(RIS_2)
    (tmp_path / "c.ris").write_text(RIS.replace("10.1000/abc", "10.1000/ghi"))
    (tmp_path / "d.ris").write_text(RIS.replace("10.1000/abc", "10.1000/jkl"))
    add_file(p, "scopus", tmp_path / "d.ris")                                                        # no version yet
    import_all(p)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")                                  # v1, a draft
    add_file(p, "scopus", tmp_path / "c.ris")
    import_all(p)
    artifacts.save_new_version(p, "synonyms", {**SYN, "blocks": [dict(SYN["blocks"][0], terms=["meeting*", "team*"])]}, origin="manual", status="frozen")
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    row = artifacts.unlock(p, "synonyms", 2, by="ana")                                               # v3, unlocked from v2
    artifacts.set_status(p, "synonyms", row["version"], "frozen")
    add_file(p, "scopus", tmp_path / "b.ris")
    root = p.root
    p.close()
    out = _cli("import", "run", "--dir", str(root))
    assert out.exit_code == 0, out.output
    assert "recorded under v3 (the frozen query)" in out.output
    p = Project.load(root)
    got = {r["file_name"]: r["query_version"] for r in p.store.query("SELECT file_name, query_version FROM sources")}
    assert got == {"d.ris": None, "c.ris": 1, "a.ris": 2, "b.ris": 3}
    p.close()
    out = _cli("import", "list", "--dir", str(root))
    assert out.exit_code == 0, out.output
    # columns: id, platform, file, records, imported at, query version, enters dedup (task 1.6 added the id and the last)
    cells = {c[3]: c for c in ([x.strip() for x in ln.split("│")] for ln in out.output.splitlines() if ".ris" in ln)}
    assert "not recorded" in cells["d.ris"][6] and cells["c.ris"][6] == "1" and cells["a.ris"][6] == "2"
    assert cells["b.ris"][6] == "3" and {c[7] for c in cells.values()} == {"yes"}


def test_import_list_matches_the_page(tmp_path):
    """`wider import list --json` and `GET /api/import/sources` give the same rows and final mark, oldest first, and
    change nothing; with nothing imported the command says so and the route lists nothing."""
    import sqlite3

    from wider.ingest.importer import add_file, import_all

    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    root = p.root
    empty = _cli("import", "list", "--dir", str(root))
    assert empty.exit_code == 0 and "no files imported yet" in empty.output and "not marked final" in empty.output
    assert TestClient(create_app(Project.load(root))).get("/api/import/sources").json() == {
        "sources": [], "final_at": None, "upload_choice": False, "upload_notice": None}
    (tmp_path / "a.ris").write_text(RIS)
    (tmp_path / "b.ris").write_text(RIS_2)
    add_file(p, "scopus", tmp_path / "a.ris")
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    p.close()
    db = next(root.rglob("*.sqlite*"), None) or next(root.rglob("*.db"))

    def dump():
        con = sqlite3.connect(db)
        try:
            return list(con.iterdump())
        finally:
            con.close()
    before, yaml_before = dump(), (root / "project.yaml").read_bytes()
    cli = _cli("import", "list", "--json", "--dir", str(root))
    assert cli.exit_code == 0, cli.output
    api = TestClient(create_app(Project.load(root))).get("/api/import/sources").json()
    # the page's confirm-step fields (D21) are the route's own; the command asks through `import add` instead
    assert (api.pop("upload_choice"), api.pop("upload_notice")) == (False, None)
    assert json.loads(cli.output) == api
    assert [(r["platform"], r["file"], r["records"], r["query_version"]) for r in api["sources"]] == \
        [("scopus", "a.ris", 1, None), ("wos", "b.ris", 1, None)]
    assert all(r["imported_at"] for r in api["sources"]) and api["final_at"] is None
    assert dump() == before and (root / "project.yaml").read_bytes() == yaml_before


def test_s2_key_named_never_shown(tmp_path, monkeypatch):
    """The Missing abstracts page and `wider enrich --status` say whether S2_API_KEY is set, never its value."""
    p = _imported(tmp_path)
    root, c = str(p.root), TestClient(create_app(p))
    secret = "synthetic-s2-secret-123"
    seen = []
    for value in (secret, None):
        if value:
            monkeypatch.setenv("S2_API_KEY", value)
        else:
            monkeypatch.delenv("S2_API_KEY", raising=False)
        page = c.get("/api/enrich")
        cli = _cli("enrich", "--status", "--dir", root)
        assert page.status_code == 200 and cli.exit_code == 0, cli.output
        assert secret not in page.text and secret not in cli.output
        seen.append((page.json()["s2_key"], page.json()["s2_key_note"], " ".join(cli.output.split())))
        if not value:   # D32 F10: the page's words name the button, never a command
            words = page.json()["s2_key_page"]
            assert words.startswith("No Semantic Scholar API key (S2_API_KEY) is set.") and "wider " not in words
            assert "Retry failed lookups" in words
    (on, on_note, on_cli), (off, off_note, off_cli) = seen
    assert (on, on_note) == ("set", "S2_API_KEY set") and "S2_API_KEY set" in on_cli
    assert off == "not set" and off_note.startswith("S2_API_KEY not set; lookups work without it")
    assert "wider enrich --retry" in off_note and off_note in off_cli
    assert "S2_API_KEY" in " ".join(_cli("enrich", "--help").output.split())


def test_gscholar_notes_name_left_out_blocks(tmp_path):
    """The Google Scholar card and `wider query render gscholar` name the blocks the reduced string leaves out, and say
    when no string can be built and why."""
    from wider.construct import artifacts

    near = {"near": ["team*", "huddle*"], "distance": 3}
    cases = {
        "named": {"blocks": [{"key": "a", "terms": ["meeting*"]}, {"key": "b", "terms": ["error*"]},
                             {"key": "c", "terms": [near]}, {"key": "d", "terms": ["animal*"]}],
                  "logic": {"and": ["a", "b", "c", {"not": "d"}]}},
        "excluded": {"blocks": [{"key": "d", "terms": ["animal*"]}], "logic": {"not": "d"}},
        "optional": {"blocks": [{"key": "o", "role": "optional", "terms": ["meeting*"]}], "logic": "o"},
    }
    seen = {}
    for name, syn in cases.items():
        p = Project.init(tmp_path / name, platforms=["gscholar"])
        syn = {**syn, "blocks": [{"name": b["key"].upper(), **b} for b in syn["blocks"]]}
        artifacts.save_new_version(p, "synonyms", syn, origin="manual")
        card = TestClient(create_app(p)).get("/api/query/render").json()["notes"]["gscholar"]
        cli = _cli("query", "render", "gscholar", "--dir", str(p.root))
        assert cli.exit_code == 0, cli.output
        header = " ".join(ln[2:] for ln in cli.output.splitlines() if ln.startswith("# "))
        assert card in header
        seen[name] = card
    assert "Left out: c (proximity only), d (excluded)." in seen["named"] and "No Google Scholar" not in seen["named"]
    assert seen["excluded"].startswith("No Google Scholar string can be built from this query: only excluded blocks.")
    assert seen["optional"] == "No Google Scholar string can be built from this query: no required block."
    from pathlib import Path

    js = (Path(__file__).parents[2] / "wider" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    assert "esc(gsNotes[k] || \"\")" in js


def test_report_and_bundle_without_decisions(tmp_path):
    """With records but no screening decisions, Build report and Write share bundle answer 200 and write the same
    files as `wider report --bundle` on a copy of the project; the page keeps both buttons enabled."""
    import re
    import shutil

    p = _imported(tmp_path)
    p.close()
    shutil.copytree(tmp_path / "r", tmp_path / "copy")
    p = Project.load(tmp_path / "r")
    c = TestClient(create_app(p))
    built, bundled = c.post("/api/report"), c.post("/api/report/bundle")
    assert built.status_code == 200 and bundled.status_code == 200, (built.text, bundled.text)
    cli = _cli("report", "--bundle", "--dir", str(tmp_path / "copy"))
    assert cli.exit_code == 0, cli.output
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")

    def files(root):
        ex = root / "exports"
        out = {}
        for f in sorted(ex.rglob("*")):
            if f.is_file():
                t = (zipfile.ZipFile(f).read("word/document.xml").decode() if f.suffix == ".docx"   # the zip's dates differ
                     else f.read_text(encoding="utf-8")).replace(str(root.resolve()), "<root>").replace(str(root), "<root>")
                out[str(f.relative_to(ex))] = stamp.sub("<time>", t)
        return out

    page, cmd = files(tmp_path / "r"), files(tmp_path / "copy")
    assert "report.md" in page and any(k.startswith("share_bundle") for k in page)
    assert sorted(page) == sorted(cmd)
    assert {k: v for k, v in page.items() if page[k] != cmd[k]} == {}
    from tests.unit.test_shell_js_node import _stages_reply, app_page

    js = app_page("global.resultSetSelect = () => {}; global.resultSetOptions = () => [];"   # common.js, not loaded here
                  "$('#rc-compare').options = []; await loadRecall(); const before = [$('#rp-build').disabled, $('#rp-bundle').disabled];"
                  "await $('#rp-build').onclick(); await $('#rp-bundle').onclick(); await flush();"
                  "console.log(JSON.stringify({before, after: [$('#rp-build').disabled, $('#rp-bundle').disabled],"
                  " proto: $('#rc-protocol').innerHTML, posts: calls.filter(c => c[1] === 'POST').map(c => c[0])}));",
                  {"/api/stages": _stages_reply({"setup": "done", "recall": "ready"}), "/api/screen": {"protocols": []},
                   "/api/samples": {"samples": {}}, "/api/report": built.json(), "/api/report/bundle": bundled.json()})
    assert js["before"] == [False, False] and js["after"] == [False, False]
    assert "no protocol decisions yet" in js["proto"]
    assert js["posts"] == ["/api/report", "/api/report/bundle"]


def test_validate_without_a_ranking(tmp_path):
    """Validation before any ranking exists: the command line and the page route give the same plain message (no
    traceback, no quotes around it) and exit 1 / answer 400."""
    p = Project.init(tmp_path / "n", platforms=["scopus"])
    p.close()
    out = _cli("rank", "validate", "--dir", str(tmp_path / "n"))
    assert out.exit_code == 1 and out.output.strip() == "no ranking; run wider rank run first"
    r = TestClient(create_app(Project.load(tmp_path / "n"))).get("/api/rank/validate")
    assert r.status_code == 400 and r.json()["detail"] == "no ranking; run wider rank run first"


# ----- the Screening page's pass pickers (screening-config-comparison 5.5) -----


def _pass_providers(p, skip=frozenset()) -> tuple[dict, set]:
    """{pass: providers} of the calls of runs not in ``skip``, and every run id."""
    q = fresh(p)
    rows = q.store.query("SELECT run_id, pass, provider FROM screen_calls")
    q.close()
    out: dict[str, set] = {}
    for x in rows:
        if x["run_id"] not in skip:
            out.setdefault(x["pass"], set()).add(x["provider"])
    return out, {x["run_id"] for x in rows}


def test_screen_protocol_uses_picked_pass_backends(pp):  # noqa: F811
    """The pass pickers' picks go to `POST /api/screen/run` (mode protocol) as provider_b / provider_c and screen that
    run only, as `wider screen protocol --provider-b --provider-c` does: pass A's calls are on a, pass B's on b, the
    judge's (when the passes disagree) on c, and `project.yaml` is byte-for-byte unchanged."""
    p, _ = pp
    before = (p.root / "project.yaml").read_bytes()
    r = api(p).post("/api/screen/run", json={"mode": "protocol", "scope": "corpus", "protocol": "pg",
                                              "provider": "a", "provider_b": "b", "provider_c": "c"})
    assert r.status_code == 200, r.text
    page, seen = _pass_providers(p)
    assert page == {"A": {"a"}, "B": {"b"}, "C": {"c"}}, page
    out = cli(p, "screen", "protocol", "corpus", "--name", "cl", "--provider", "a",
              "--provider-b", "b", "--provider-c", "c")
    assert out.exit_code == 0, out.output
    assert _pass_providers(p, seen)[0] == page
    assert (p.root / "project.yaml").read_bytes() == before
