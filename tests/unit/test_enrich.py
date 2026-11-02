"""Abstract enrichment on synthetic records with a MockTransport client: fill, failed vs exhausted, retry, batch size."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.unit.helpers import consent, mock_client
from wider.core import questions as qs
from wider.core.project import Project
from wider.enrich.fetch import candidates, enrich, summary
from wider.ingest.importer import add_file, import_all
from wider.ui.app import create_app

RIS = """TY  - JOUR
TI  - Thin one with doi
DO  - 10.1000/thin
PY  - 2020
AB  - short
ER  -

TY  - JOUR
TI  - No doi but a long enough title to search
PY  - 2019
ER  -

TY  - JOUR
TI  - Already fine
AB  - WORDS
ER  -
""".replace("WORDS", " ".join(["word"] * 30))


def _project(tmp_path):
    p = consent(Project.init(tmp_path / "r", platforms=["scopus"]))
    (tmp_path / "a.ris").write_text(RIS)
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    from wider.dedup.engine import run
    run(p)
    return p


def _good(request: httpx.Request):
    url = str(request.url)
    if "api.openalex.org/works/https://doi.org/10.1000/thin" in url:
        return httpx.Response(200, json={"abstract_inverted_index": {w: [i] for i, w in enumerate(["x"] * 1)}})  # too short
    if "api.crossref.org" in url:
        return httpx.Response(200, json={"message": {"abstract": " ".join(["c"] * 25)}})
    if "api.openalex.org/works?" in url:
        return httpx.Response(200, json={"results": [{"title": "No doi but a long enough title to search", "publication_year": 2019,
                                                      "doi": "https://doi.org/10.1000/found",
                                                      "abstract_inverted_index": {f"w{i}": [i] for i in range(30)}}]})
    return httpx.Response(404)


def _log(p):
    return [(r["record_id"], r["status"]) for r in p.store.query("SELECT record_id, status FROM enrichment_log ORDER BY id")]


def test_enrich_fills_and_logs(tmp_path):
    p = _project(tmp_path)
    assert len(candidates(p)) == 2
    stats = enrich(p, pause_s=0, client=mock_client(_good))
    assert stats["tried"] == 2 and stats["filled"] == 2 and stats["failed"] == 0
    assert stats["by_source"] == {"crossref": 1, "openalex_title": 1}
    s = summary(p)
    assert s["thin"] == 0 and s["pending"] == 0 and s["filled"] == 2 and s["failed"] == 0
    r = p.store.one("SELECT doi, abstract_source FROM records WHERE title LIKE 'No doi%'")
    assert r["doi"] == "10.1000/found" and r["abstract_source"] == "openalex_title"
    assert enrich(p, pause_s=0, client=mock_client())["tried"] == 0  # resumable: nothing left, nothing sent
    logged = {r["service"]: r["n_items"] for r in p.store.query("SELECT service, n_items FROM external_requests WHERE purpose='enrich'")}
    assert logged["openalex"] == 2 and logged["crossref"] == 1   # one DOI lookup + one title search; one DOI on to Crossref


@pytest.mark.parametrize("answer, note", [
    (lambda r: httpx.Response(503), "HTTP 503"),
    (lambda r: httpx.Response(429), "HTTP 429"),
    (httpx.ReadTimeout("slow"), "timeout"),
    (httpx.ConnectError("no route"), "connection error"),
    (lambda r: httpx.Response(200, content=b"<html>not json</html>"), "unreadable answer"),
])
def test_service_errors_mark_failed_without_a_question(tmp_path, monkeypatch, answer, note):
    monkeypatch.setattr("wider.enrich.sources.time.sleep", lambda *_: None)   # Semantic Scholar waits before a 429 retry
    p = _project(tmp_path)

    def handler(request):
        if isinstance(answer, BaseException):
            raise answer
        return answer(request)

    stats = enrich(p, pause_s=0, client=mock_client(handler))
    assert stats["tried"] == 2 and stats["failed"] == 2 and stats["exhausted"] == 0 and not stats.get("questions")
    assert not qs.list_questions(p)
    assert {s for _, s in _log(p)} == {"failed"}
    notes = [json.loads(r["note"]) for r in p.store.query("SELECT note FROM enrichment_log")]
    assert all(any(e["status"] == "error" and note in e["note"] for e in n) for n in notes)
    s = summary(p)
    assert s["failed"] == 2 and s["exhausted"] == 0 and s["pending"] == 0
    assert p.store.scalar("SELECT SUM(n_items) FROM external_requests WHERE purpose='enrich'") > 0   # failed requests are logged
    if note != "unreadable answer":   # an answered request with an unreadable body is not an HTTP failure
        assert p.store.scalar("SELECT SUM(n_failed) FROM external_requests") > 0
    assert enrich(p, pause_s=0, client=mock_client())["tried"] == 0   # failed records are not retried automatically


def test_404_everywhere_is_exhausted_with_a_question(tmp_path):
    p = _project(tmp_path)
    stats = enrich(p, pause_s=0, client=mock_client({}))
    assert stats["exhausted"] == 2 and stats["failed"] == 0 and stats["questions"] == 2
    assert summary(p)["exhausted"] == 2


def test_retry_covers_failed_and_exhausted_and_appends(tmp_path):
    p = _project(tmp_path)
    enrich(p, pause_s=0, limit=1, client=mock_client(lambda r: httpx.Response(503)))   # record 1: failed
    enrich(p, pause_s=0, limit=1, client=mock_client({}))                              # record 2: exhausted
    assert [s for _, s in _log(p)] == ["failed", "exhausted"]
    assert candidates(p) == [] and len(candidates(p, retry=True)) == 2
    stats = enrich(p, pause_s=0, retry=True, client=mock_client(_good))
    assert stats["tried"] == 2 and stats["filled"] == 2
    assert [s for _, s in _log(p)] == ["failed", "exhausted", "filled", "filled"]   # earlier attempts are kept
    s = summary(p)
    assert s["filled"] == 2 and s["failed"] == 0 and s["exhausted"] == 0
    assert enrich(p, pause_s=0, retry=True, client=mock_client())["tried"] == 0       # filled records are never retried


def test_batch_size(tmp_path):
    p = _project(tmp_path)
    with pytest.raises(ValueError, match="at least 1"):
        enrich(p, limit=0, client=mock_client())
    assert _log(p) == []
    assert enrich(p, pause_s=0, limit=1, client=mock_client({}))["tried"] == 1
    c = TestClient(create_app(p))
    for path in ("/api/enrich/run?limit=0", "/api/jobs/enrich?limit=0"):
        assert c.post(path).status_code == 400
    assert c.get("/api/jobs").json() == []
    assert len(_log(p)) == 1


def test_enrich_cli_limit_and_retry(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    p = _project(tmp_path)
    r = CliRunner().invoke(cli_app, ["enrich", "--limit", "0", "--dir", str(p.root)])
    assert r.exit_code == 2 and _log(p) == []
    enrich(p, pause_s=0, client=mock_client(lambda r: httpx.Response(503)))
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client(_good))
    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    r = CliRunner().invoke(cli_app, ["enrich", "--retry", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "retrying 2 records" in r.output
    assert [s for _, s in _log(p)] == ["failed", "failed", "filled", "filled"]


def _one_down(request: httpx.Request):   # OpenAlex 503 for the DOI; every other lookup (title search too) not found
    return httpx.Response(503 if "api.openalex.org/works/https://doi.org/" in str(request.url) else 404)


def _rows(p, title_like):   # one record's enrichment_log rows, oldest first
    return [(r["status"], json.loads(r["note"])) for r in p.store.query(
        "SELECT l.status, l.note FROM enrichment_log l JOIN records r USING(record_id) WHERE r.title LIKE ? ORDER BY l.id",
        (title_like,))]


def _flat(text):   # rich wraps long lines in CliRunner output
    return " ".join(text.split())


FAILED_WARNING = ("failed because a service could not be reached", "`wider enrich --retry`", '"Retry failed lookups"')


def test_one_source_down_is_failed_and_the_stage_result_names_it(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.stage_result import last_result
    p = _project(tmp_path)
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client(_one_down))
    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    r = CliRunner().invoke(cli_app, ["enrich", "--dir", str(p.root)])   # CLI twin of the Enrich page run
    assert r.exit_code == 0, r.output
    # THEN the DOI record is failed; its note: openalex error, a miss for each other source (no body stored)
    assert _rows(p, "Thin one%") == [("failed", [{"source": "openalex", "status": "error", "note": "HTTP 503"},
                                                 {"source": "crossref", "status": "miss"},
                                                 {"source": "europepmc", "status": "miss"},
                                                 {"source": "semantic_scholar", "status": "miss"}])]
    assert [s for s, _ in _rows(p, "No doi%")] == ["exhausted"]   # only not-found answers: exhausted, not failed
    assert [q["record_id"] for q in qs.list_questions(p)] == [               # a failed record raises no question
        p.store.scalar("SELECT record_id FROM records WHERE title LIKE 'No doi%'")]
    s = summary(p)
    assert s["failed"] == 1 and s["exhausted"] == 1 and s["pending"] == 0
    # stage result: N lookups failed, retry with the CLI or the page button; not the "nothing found" warning
    res = last_result(p, "enrich")
    assert res["metrics"]["tried"] == 2 and res["metrics"]["failed"] == 1 and res["metrics"]["exhausted"] == 1
    assert len(res["warnings"]) == 1 and res["warnings"][0].startswith("1 lookup failed")
    assert all(t in res["warnings"][0] for t in FAILED_WARNING)
    assert all(_flat(t) in _flat(r.output) for t in ("1 lookup failed", *FAILED_WARNING))   # the CLI prints it


def _wait(c, job_id, timeout=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
    raise AssertionError("job did not finish")


def test_smaller_batch_job_looks_up_at_most_25(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    p = consent(Project.init(tmp_path / "r", platforms=["scopus"]))
    words = ["amber", "basalt", "cedar", "delta", "ember", "fjord", "garnet", "harbor", "iris", "juniper"]
    (tmp_path / "b.ris").write_text("".join(
        f"TY  - JOUR\nTI  - {a} {b} {c} synthetic item\nDO  - 10.1000/b{i:02d}\nPY  - 2020\nER  -\n\n"
        for i, (a, b, c) in enumerate((words[i % 10], words[(i * 3 + 1) % 10], words[i // 10]) for i in range(30))))
    add_file(p, "scopus", tmp_path / "b.ris")
    import_all(p)
    from wider.dedup.engine import run
    run(p)
    assert len(candidates(p)) == 30
    calls = []
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client(lambda r: httpx.Response(503), calls))
    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    c = TestClient(create_app(p))
    r = c.post("/api/jobs/enrich?limit=25")   # what "Fetch next 25" sends
    assert r.status_code == 200
    j = _wait(c, r.json()["job_id"])
    assert j["status"] == "done", j
    assert j["label"] == "Fetching abstracts (up to 25)" and j["params"] == {"limit": 25, "retry": False}
    assert j["result"]["stats"]["tried"] == 25 and j["result"]["stats"]["failed"] == 25
    assert p.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 25 and len(candidates(p)) == 5
    assert len({str(q.url) for q in calls if "openalex" in q.url.host}) == 25   # 25 DOIs sent, the other 5 not
    assert j["result"]["summary"]["pending"] == 5 and j["result"]["summary"]["failed"] == 25
    w = j["result"]["stage_result"]["warnings"]   # stage result of the job: 25 failed, 5 still pending
    assert w[0].startswith("25 lookups failed") and all(t in w[0] for t in FAILED_WARNING)
    assert w[1].startswith("5 records are still pending")
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client({}))
    r = CliRunner().invoke(cli_app, ["enrich", "--limit", "3", "--dir", str(p.root)])   # CLI twin: --limit N
    assert r.exit_code == 0, r.output
    assert p.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 28 and len(candidates(p)) == 2


def test_retry_keeps_the_audit_log(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    p = _project(tmp_path)
    enrich(p, pause_s=0, client=mock_client(_one_down))   # DOI record failed, title-only record exhausted
    assert [s for s, _ in _rows(p, "Thin one%")] == ["failed"] and [s for s, _ in _rows(p, "No doi%")] == ["exhausted"]
    monkeypatch.setattr("wider.enrich.sources._client",   # now Crossref has the DOI record; the other is still not found
                        lambda: mock_client({"api.crossref.org/works/10.1000/thin": {"message": {"abstract": " ".join(["c"] * 25)}}}))
    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    r = CliRunner().invoke(cli_app, ["enrich", "--retry", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "retrying 2 records" in r.output
    assert [s for s, _ in _rows(p, "Thin one%")] == ["failed", "filled"]          # earlier row kept, new row added
    assert [s for s, _ in _rows(p, "No doi%")] == ["exhausted", "exhausted"]
    s = summary(p)   # each record counted once, by its latest attempt
    assert s["filled"] == 1 and s["exhausted"] == 1 and s["failed"] == 0 and s["by_source"] == {"crossref": 1}
    assert p.store.one("SELECT abstract_source FROM records WHERE title LIKE 'Thin one%'")["abstract_source"] == "crossref"


def test_records_by_state_groups_page_and_cli_agree(tmp_path):
    """Owner walkthrough M6/M7: the records by state (not looked up yet, failed, in no source) and by the source that
    found the abstract, each with its DOI and what happened; the page's API and `wider enrich --records` read the same."""
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.enrich.fetch import records_by_state
    p = _project(tmp_path)
    d = records_by_state(p, "waiting")
    assert d["counts"]["waiting"] == 2 and d["missing"] == 2 and d["found"] == 0
    assert any("No DOI, so it goes to OpenAlex by exact title" in r["what"] for r in d["rows"])
    enrich(p, pause_s=0, limit=1, client=mock_client(lambda r: httpx.Response(503)))   # the DOI record: failed
    enrich(p, pause_s=0, client=mock_client(_good))                                    # the title record: found
    d = records_by_state(p, "failed")
    assert d["counts"]["failed"] == 1 and d["counts"]["waiting"] == 0 and d["counts"]["openalex_title"] == 1
    row = d["rows"][0]
    assert row["doi"] == "10.1000/thin" and "HTTP 503" in row["what"] and "OpenAlex" in row["what"]
    found = records_by_state(p, "openalex_title")["rows"][0]
    assert found["doi"] == "10.1000/found" and "30 words" in found["what"]
    with pytest.raises(ValueError, match="unknown group"):
        records_by_state(p, "everything")
    c = TestClient(create_app(p))
    api = c.get("/api/enrich/records", params={"group": "failed"}).json()
    assert api["counts"] == d["counts"] and api["rows"] == d["rows"]
    assert c.get("/api/enrich/records", params={"group": "x"}).status_code == 400
    before = p.store.scalar("SELECT COUNT(*) FROM external_requests")
    r = CliRunner().invoke(cli_app, ["enrich", "--records", "failed", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["rows"] == d["rows"]
    r = CliRunner().invoke(cli_app, ["enrich", "--records", "failed", "--dir", str(p.root)])
    assert "Failed, can retry: showing 1 of 1" in _flat(r.output) and "HTTP 503" in _flat(r.output)
    assert p.store.scalar("SELECT COUNT(*) FROM external_requests") == before   # listing sends nothing
