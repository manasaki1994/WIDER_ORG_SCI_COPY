"""Search provenance (change 2c, phase E): seed chaining and term yield in the lookup log, the response cache, replay,
the three ledgers and PRISMA-S. Synthetic projects and mock OpenAlex clients only; nothing leaves the computer."""
import json

import httpx
from typer.testing import CliRunner

from tests.unit.helpers import mock_client
from tests.unit.test_seed_mining import _chained, _openalex
from wider.cli import app as cli_app
from wider.core import lookups


def test_new_purposes_are_logged(tmp_path, monkeypatch):
    """Scenario "Seed chaining and term yield are logged" (online-lookups): chaining with direction references
    resolves the two seed DOIs in one request and fetches each seed's references, 4 items in one entry."""
    from wider.seeds import chain

    calls: list = []
    p = _chained(tmp_path, monkeypatch, "a", calls)
    chain.chain_seeds(p, "references", client=mock_client(_openalex, calls))
    assert len(calls) == 3
    log = lookups.log(p)
    assert [(e["purpose"], e["service"], e["n_items"], e["n_failed"]) for e in log] == [
        ("seed_chain", "openalex", 4, 0)]
    assert "seed_chain" in lookups.status(p)["purposes"]
    # a term yield adds one term_yield entry of 1 item; no entry holds the term or its count
    from wider.search.term_yield import term_yield

    lookups.allow_terms(p, author="test")
    res = term_yield(p, '"alpha coupling"', client=mock_client({"api.openalex.org/works": {"meta": {"count": 4321}}}))
    assert res["count"] == 4321
    log = lookups.log(p)
    assert [(e["purpose"], e["service"], e["n_items"], e["n_failed"]) for e in log][0] == ("term_yield", "openalex", 1, 0)
    assert len(log) == 2 and "alpha" not in repr(log) and "4321" not in repr(log)

    # a reference-list run logs seed_refs (B001's DOI resolved and its list fetched, 2 items) and a Crossref match
    # logs ref_match (one title sent), each counted the way seed_chain is
    from tests.unit.test_seed_chain import _refs_openalex, _with_refs
    from wider.seeds import references

    def both(request):
        if request.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": []}})
        return _refs_openalex(request)
    calls2: list = []
    q = _with_refs(tmp_path, monkeypatch, "r", calls2, handler=both)
    references.seed_references(q, "B001", tier="crossref")
    assert sorted((e["purpose"], e["service"], e["n_items"], e["n_failed"]) for e in lookups.log(q)) == [
        ("ref_match", "crossref", 1, 0), ("seed_refs", "openalex", 2, 0)]


def test_lookup_cache(tmp_path, monkeypatch):
    """Scenarios "Second chaining run sends nothing" and "Different requests never share an answer" (search-provenance,
    2c D16): a repeated chaining run is answered from ``lookup_cache`` and logs nothing; clearing it requests again;
    two paths keep their own answers and a 429 is never stored."""
    import httpx
    from fastapi.testclient import TestClient

    from tests.unit.test_dry_run import _cli
    from wider.seeds import chain
    from wider.ui.app import create_app

    calls: list = []
    p = _chained(tmp_path, monkeypatch, "a", calls)
    first = chain.chain_seeds(p, "references", client=mock_client(_openalex, calls))
    assert len(calls) == 3 and first["from_cache"] == 0 and lookups.cache_count(p) == 3
    calls.clear()
    r = _cli(p, "benchmark", "chain", "--direction", "references")
    assert r.exit_code == 0, r.output
    assert calls == [] and "from the lookup cache, nothing was sent" in r.output
    log = _cli(p, "lookups", "log")
    assert log.exit_code == 0 and log.output.count("seed_chain") == 1   # only the first run's requests
    assert len(lookups.log(p)) == 1
    out = _cli(p, "lookups", "status").output
    assert "cached responses: 3" in out
    r = _cli(p, "lookups", "cache", "--clear")
    assert r.exit_code == 0 and "3 stored answers removed" in r.output and lookups.cache_count(p) == 0
    again = chain.chain_seeds(p, "references", client=mock_client(_openalex, calls))
    assert len(calls) == 3 and again["from_cache"] == 0 and len(lookups.log(p)) == 2
    assert "cached responses: 3" in _cli(p, "lookups", "cache").output
    # the UI twin: the count in status, DELETE empties the store
    c = TestClient(create_app(p))
    assert c.get("/api/lookups").json()["cached_responses"] == 3
    j = c.delete("/api/lookups/cache").json()
    assert j["removed"] == 3 and j["cached_responses"] == 0

    # two paths with the same parameters keep their own answers; a 429 is not stored and is requested again
    seen: list = []

    def handler(request):
        path = request.url.path
        if path.endswith("/W3"):
            return httpx.Response(429)
        return httpx.Response(200, json={"path": path})

    for _ in range(2):
        with lookups.lookup_client(p, "seed_chain", mock_client(handler, seen)) as lc:
            a = lc.get("https://api.openalex.org/works/W1", params={"select": "id", "mailto": "x@example.org"})
            b = lc.get("https://api.openalex.org/works/W2", params={"select": "id"})
            t = lc.get("https://api.openalex.org/works/W3", params={"select": "id"})
            assert a.json() == {"path": "/works/W1"} and b.json() == {"path": "/works/W2"}
            assert t.status_code == 429
    assert [r.url.path for r in seen] == ["/works/W1", "/works/W2", "/works/W3", "/works/W3"]
    assert lookups.cache_count(p) == 2
    # a purpose outside the cache list neither reads nor writes the store
    lookups.allow(p)
    with lookups.lookup_client(p, "enrich", mock_client(handler, seen)) as lc:
        lc.get("https://api.openalex.org/works/W1", params={"select": "id"})
    assert seen[-1].url.path == "/works/W1" and len(seen) == 5 and lookups.cache_count(p) == 2


def test_replay_offline(tmp_path, monkeypatch):
    """Scenarios "Replay matches, then a profile change shows" and "A round without a record" (search-provenance,
    2c D17, D0b): the record is written at a round's first dry run (on every platform, Development seeds only) and
    never changed; a replay calls no model and sends no request, writes nothing, names a changed input first and
    then the first output that differs."""
    import copy

    from tests.unit.helpers import no_requests
    from tests.unit.test_dry_run import _cli, _client, _proj, _versions
    from wider.construct import artifacts
    from wider.search import evaluate, replay
    from wider.search import render as rnd

    def no_model(*a, **k):
        raise AssertionError("a model was called")

    monkeypatch.setattr("wider.providers.registry.make_provider", no_model)
    p = _proj(tmp_path)
    _versions(p, "huddle*", "standup*")

    def snap():
        rows = [dict(r) for r in p.store.query("SELECT * FROM query_rounds ORDER BY round_no")]
        dry = [tuple(r) for r in p.store.query("SELECT * FROM dry_runs ORDER BY round_no, platform, benchmark_id")]
        runs = sorted(str(x.relative_to(p.root)) for x in (p.root / "runs").rglob("*")) if (p.root / "runs").exists() else []
        return rows, dry, runs

    # a first dry run on one platform writes a record that covers every profiled platform
    r = _cli(p, "query", "dry-run", "--version", "1", "--platform", "ebsco")
    assert r.exit_code == 0, r.output
    rec = replay.stored(p, 1)
    assert rec and rec["recorded_at"] and set(rec["inputs"]) == set(replay.INPUTS)
    assert set(rec["outputs"]["dry_run"]) == set(evaluate.default_platforms(p)) and "scopus" in rec["outputs"]["dry_run"]
    # a later dry run does not change it
    evaluate.dry_run(p, 1)
    assert replay.stored(p, 1) == rec

    # replay unchanged: no model, no request, nothing written; freezing the version first changes nothing
    artifacts.set_status(p, "synonyms", 1, "frozen")
    before = snap()
    sent = no_requests(monkeypatch)
    r = _cli(p, "query", "replay", "1")
    assert r.exit_code == 0, r.output
    assert "replay matches" in r.output and sent == []
    assert _client(p).get("/api/query/rounds/1/replay").json()["result"] == "matches"
    assert snap() == before

    # a profile's minimum stem edited: "profiles" is the changed input, the dry run the first difference
    cat = copy.deepcopy(rnd.platforms())
    cat["scopus"]["profile"]["min_stem"] = (cat["scopus"]["profile"].get("min_stem") or 3) + 5
    monkeypatch.setattr(rnd, "_PLATFORMS", cat)
    res = replay.replay(p, 1)
    assert res["result"] == "differs" and res["changed_inputs"] == ["profiles"]
    assert res["first_difference"] == "dry run (scopus)"
    out = _cli(p, "query", "replay", "1").output
    assert "profiles" in out and "first difference: dry run (scopus)" in out
    assert snap() == before and replay.stored(p, 1) == rec and sent == []
    monkeypatch.setattr(rnd, "_PLATFORMS", None)

    # a round from before this change: no record to compare, nothing written; its next dry run writes one, once
    p.store.execute("UPDATE query_rounds SET hashes_json=NULL WHERE round_no=2")
    before = snap()
    r = _cli(p, "query", "replay", "2")
    assert r.exit_code == 0 and "no record to compare" in r.output
    assert _client(p).get("/api/query/rounds/2/replay").json()["result"] == "no_record"
    assert snap() == before
    monkeypatch.undo()
    evaluate.dry_run(p, 2)
    rec2 = replay.stored(p, 2)
    assert rec2 and rec2["recorded_at"]
    evaluate.dry_run(p, 2)
    assert replay.stored(p, 2) == rec2
    assert replay.replay(p, 2)["result"] == "matches"
    # an unknown round is an error
    assert _cli(p, "query", "replay", "9").exit_code == 1
    assert _client(p).get("/api/query/rounds/9/replay").status_code == 400


def test_three_ledgers_one_view(tmp_path, monkeypatch):
    """Scenarios "One round, three ledgers" and "An older proposal run without an output hash" (search-provenance,
    2c D17): decisions by round, the model calls that shape the query with their hashes from each run's
    ``calls.json`` (never a prompt), the outside requests with the switch that allowed them; ``provenance_1.json``
    holds the same; a proposal run written before this change shows "output hash not recorded", cells are left out."""
    import json
    from datetime import UTC, datetime, timedelta

    from tests.unit.test_dry_run import _cli, _client
    from tests.unit.test_query_checking import REVIEW_SYN, _review_project
    from wider.construct import propose
    from wider.interview import ledger
    from wider.search import provenance, review

    def answer(req):
        return {"findings": []} if "findings" in json.dumps(req.schema_ or {}) else REVIEW_SYN

    p, _made = _review_project(tmp_path, monkeypatch, {"llama": answer})
    ev = {"subject": "nurse*", "why": "synthetic"}
    opts = [{"value": "keep", "label": "keep"}, {"value": "drop", "label": "drop"}]
    ledger.record(p, "term_check", "t:1", ev, opts, "keep", "keep", "chosen", "kept", "tester", 1)
    ledger.record(p, "term_check", "t:2", ev, opts, "keep", None, "dismissed", "nothing", "tester", 1)
    # one synonym proposal made from round 1 (it opens version 2), one reader twice on round 1, one chaining request
    row = propose.propose(p, "synonyms")
    assert row["version"] == 2 and all(c.get("output_sha256") for c in row["calls"])
    review.second_read(p, version=1, readers=["llama"])
    lookups.allow(p)
    with lookups.lookup_client(p, "seed_chain", client=mock_client({"api.openalex.org": {"results": []}})) as c:
        c.get("https://api.openalex.org/works", params={"filter": "doi:10.1000/syn1"})
    later = (datetime.now(UTC) + timedelta(seconds=2)).isoformat(timespec="seconds")
    p.store.execute("UPDATE query_rounds SET created_at=? WHERE round_no=2", (later,))
    ledger.record(p, "term_check", "t:3", ev, opts, "keep", "drop", "chosen", "dropped", "tester", 2)

    v = provenance.view(p, 1)
    assert [d["decision_id"] for d in v["decisions"]] == [1, 2] and [d["dismissed"] for d in v["decisions"]] == [False, True]
    assert sorted(c["stage"] for c in v["model_calls"]) == ["construct", "press_review", "press_review"]
    assert all(c["input_sha256"] and c["output_sha256"] for c in v["model_calls"])
    assert [(w["purpose"], w["switch"]) for w in v["web_calls"]] == [("seed_chain", "DOI consent (online_lookups.allowed)")]
    assert review.SYSTEM[:60] not in json.dumps(v)   # hashes only, no prompt text
    # the CLI writes provenance_1.json with the same listing; round 2's decision is not in it
    r = _cli(p, "query", "provenance", "1")
    assert r.exit_code == 0, r.output
    assert "model calls: 3" in r.output and "web calls: 1" in r.output and "[dismissed]" in r.output
    saved = json.loads((p.root / "exports" / "provenance_1.json").read_text())
    from wider.core.researchers import attribute_obj
    assert {k: saved[k] for k in v} == attribute_obj(p, v) and 3 not in [d["decision_id"] for d in saved["decisions"]]
    assert _client(p).get("/api/query/rounds/1/provenance").json() == v
    r2 = provenance.view(p, 2)
    assert [d["decision_id"] for d in r2["decisions"]] == [3] and r2["model_calls"] == [] and r2["web_calls"] == []
    assert _client(p).get("/api/query/rounds/9/provenance").status_code == 400
    assert _cli(p, "query", "provenance", "9").exit_code == 1

    # a proposal run written before this change (no output hash, no from_version) and a cells proposal in round 1
    at = p.store.one("SELECT created_at FROM query_rounds WHERE round_no=1")["created_at"]
    for name, artifact, calls in (("20260101T000000Z_synonyms", "synonyms", "- tag: all\n  input_sha256: abc123\n"),
                                  ("20260101T000001Z_cells", "cells", "- tag: all\n  input_sha256: def456\n")):
        d = p.root / "runs" / "construct" / name
        d.mkdir(parents=True)
        (d / "run_spec.json").write_text(json.dumps({"kind": "construct", "settings": {"artifact": artifact}}))
        (d / "calls.json").write_text(calls)
        p.store.execute("INSERT INTO llm_usage(at, stage, run_ref, provider, kind, model, locality, ok) "
                        "VALUES(?,?,?,?,?,?,?,1)", (at, "construct", name, "llama", "fake", "llama3.1:8b", "test"))
    v = provenance.view(p, 1)
    old = [c for c in v["model_calls"] if c["run_ref"] == "20260101T000000Z_synonyms"]
    assert len(old) == 1 and old[0]["input_sha256"] == "abc123" and old[0]["output_sha256"] is None
    assert old[0]["note"] == "output hash not recorded"
    assert not any(c["run_ref"].endswith("_cells") for c in v["model_calls"])
    assert "output hash not recorded" in _cli(p, "query", "provenance", "1").output
    # a round with no decisions says so
    p.store.execute("DELETE FROM decisions WHERE round_no=2")
    assert provenance.view(p, 2)["decisions_message"] == "no decisions recorded for this round"
    # the card: escaped text, the dismissed mark, the missing output hash, the empty-decisions message
    from tests.unit.test_dry_run import _rounds_js
    v1 = provenance.view(p, 1)
    v1["decisions"][0]["text"] = "<img src=x onerror=alert(1)>"
    out = _rounds_js(f"console.log(JSON.stringify({{a: Rounds.provHtml({json.dumps(v1)}), "
                     f"b: Rounds.provHtml({json.dumps(provenance.view(p, 2))})}}));")
    assert "<img" not in out["a"] and "&lt;img" in out["a"] and "(dismissed)" in out["a"]
    assert "output hash not recorded" in out["a"] and "Model calls (4)" in out["a"] and "Outside requests (1)" in out["a"]
    assert "no decisions recorded for this round" in out["b"]


def test_package_holds_the_refinement_records(tmp_path):
    """Scenario "Refinement records in the package" (recall-and-outputs, 2c D17): three decisions written through the
    ledger, one dismissed; the zip holds decisions.jsonl (three lines) and decisions.csv (three rows, the dismissed one
    marked), prisma_s.md and .json, a ledger next to each archived string and provenance_1.json, all hashed in
    MANIFEST.txt, and nothing from seeds/ or the lookup cache. A project with no decisions still gets both ledger
    files, with zero rows."""
    import csv
    import hashlib
    import io
    import json
    import zipfile

    from tests.unit.test_dry_run import _import_one, _proj, _versions
    from wider.interview import ledger
    from wider.recall.outputs import write_package
    from wider.search.archive import archive

    p = _proj(tmp_path, name="pkg")
    _import_one(p, tmp_path, "pkg.ris")
    _versions(p, "huddle*")
    archive(p, 1)
    ev = {"subject": "huddle*", "why": "synthetic"}
    opts = [{"value": "keep", "label": "keep"}, {"value": "drop", "label": "drop"}]
    ledger.record(p, "term_check", "t:1", ev, opts, "keep", "keep", "chosen", "kept", "tester", 1)
    ledger.record(p, "term_check", "t:2", ev, opts, "keep", "drop", "chosen", "dropped", "tester", 1)
    ledger.record(p, "term_check", "t:3", ev, opts, "keep", None, "dismissed", "nothing", "tester", 1)
    seeds = p.root / "seeds"
    seeds.mkdir(exist_ok=True)
    (seeds / "B001.pdf").write_bytes(b"%PDF-1.4 SEED BODY")
    p.store.execute("INSERT INTO lookup_cache(key, body, fetched_at) VALUES ('k', 'CACHED BODY', '2026-01-01')")

    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    names = z.namelist()
    jl = z.read("decisions.jsonl").decode().splitlines()
    assert len(jl) == 3 and [json.loads(x)["mode"] for x in jl] == ["chosen", "chosen", "dismissed"]
    rows = list(csv.DictReader(io.StringIO(z.read("decisions.csv").decode())))
    assert len(rows) == 3 and [r["mode"] for r in rows].count("dismissed") == 1
    assert {"prisma_s.md", "prisma_s.json", "provenance_1.json"} <= set(names)
    assert [d["decision_id"] for d in json.loads(z.read("provenance_1.json"))["decisions"]] == [1, 2, 3]
    strings = [n for n in names if "search_strings/" in n and n.endswith(".txt")]
    assert strings and all(s[:-4] + "_ledger.json" in names for s in strings)
    hashes = {ln.split("  ", 1)[1]: ln.split("  ", 1)[0] for ln in z.read("MANIFEST.txt").decode().splitlines()
              if "  " in ln and not ln.startswith("sha256")}
    for n in ("decisions.jsonl", "decisions.csv", "prisma_s.md", "provenance_1.json"):
        assert hashes[n] == hashlib.sha256(z.read(n)).hexdigest()
    assert not any("seeds/" in n or n.endswith(".pdf") for n in names)
    blob = b"".join(z.read(n) for n in names)
    assert b"SEED BODY" not in blob and b"CACHED BODY" not in blob
    # no decisions: both files, zero rows
    e = _proj(tmp_path, name="pkg0")
    z0 = zipfile.ZipFile(e.root / write_package(e)["path"])
    assert z0.read("decisions.jsonl") == b"" and len(z0.read("decisions.csv").decode().splitlines()) == 1


def _yield_proj(tmp_path, monkeypatch, calls, terms=("meeting*", "huddle")):
    """A project with lookups allowed (the DOI consent) and the term switch off, one synonyms version, and a mock
    OpenAlex whose works search answers 1234 in ``meta.count``, and 500 for the term "broken"."""
    import httpx

    from tests.unit.helpers import consent
    from tests.unit.test_dry_run import _proj
    from wider.construct import artifacts

    def works(request):
        if request.url.params.get("search") == "broken":
            return httpx.Response(500)
        return httpx.Response(200, json={"meta": {"count": 1234}, "results": [{"title": "not read"}]})

    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(works, calls))
    p = consent(_proj(tmp_path))
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "Meetings", "terms": [
        *terms, {"near": ["team*", "error*"], "distance": 3}]}], "logic": {"and": ["a"]}}, origin="manual")
    return p


def test_term_lookups_need_their_own_switch(tmp_path, monkeypatch):
    """Scenario "Terms wait for their own switch" (privacy-and-data-policy, 2c D16): with lookups allowed but the term
    switch off, `wider query yield` exits 2 naming `wider lookups terms allow` and the page's route answers 403 for the
    term switch, both before any request; after `wider lookups terms allow` the request carries the term and nothing
    else from the project, and is logged as term_yield."""
    from tests.unit.test_dry_run import _cli, _client

    calls: list = []
    p = _yield_proj(tmp_path, monkeypatch, calls)
    r = _cli(p, "query", "yield", "alpha coupling")
    assert r.exit_code == 2 and "wider lookups terms allow" in " ".join(r.output.split()) and calls == []
    assert "sends search terms or a query to OpenAlex" in " ".join(r.output.split())   # D27: one switch for both
    rr = _client(p).post("/api/query/yield", json={"term": "alpha coupling"})
    assert rr.status_code == 403 and rr.json()["detail"]["consent_required"]["switch"] == "terms" and calls == []
    assert lookups.log(p) == []
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "alpha coupling")
    assert r.exit_code == 0, r.output
    assert len(calls) == 1 and calls[0].url.host == "api.openalex.org" and calls[0].url.path == "/works"
    assert dict(calls[0].url.params) == {"search": "alpha coupling", "per-page": "1", "select": "id"}   # the term, nothing else
    assert [(e["purpose"], e["service"], e["n_items"]) for e in lookups.log(p)] == [("term_yield", "openalex", 1)]


def _oa_proj(tmp_path, monkeypatch, calls, count=5678):
    """Three Development seeds with DOIs whose titles the dry run hits, one without a DOI, one whose DOI is also a
    Validation paper's, and a Validation seed; no consent given. The mock OpenAlex answers the query count with
    ``count`` and a DOI filter with the first two Development seeds only."""
    import httpx

    from wider.construct import artifacts
    from wider.core.models import BenchmarkPaper
    from wider.core.project import Project

    def works(request):
        flt = request.url.params.get("filter", "")
        if flt.startswith("doi:"):
            asked = flt[4:].split(",", 1)[0].split("|")
            return httpx.Response(200, json={"meta": {"count": 2}, "results": [
                {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{d}"}
                for i, d in enumerate(asked) if d in ("10.1000/dev1", "10.1000/dev2")]})
        return httpx.Response(200, json={"meta": {"count": count}, "results": []})

    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(works, calls))
    pr = Project.init(tmp_path / "oa", platforms=["scopus"])
    pr.write_benchmark([BenchmarkPaper(benchmark_id=f"B00{i}", title=f"Team meeting study {i}", doi=f"10.1000/dev{i}",
                                       set="Development") for i in (1, 2, 3)]
                       + [BenchmarkPaper(benchmark_id="B004", title="Huddle notes", set="Development"),
                          BenchmarkPaper(benchmark_id="B005", title="Meeting twin", doi="10.1000/val1",
                                         set="Development"),
                          BenchmarkPaper(benchmark_id="V001", title="Meeting validation", doi="10.1000/val1",
                                         set="Validation")])
    artifacts.save_new_version(pr, "synonyms", {"blocks": [{"key": "a", "name": "Meetings", "terms": [
        "meeting*", "huddle"]}], "logic": {"and": ["a"]}}, origin="manual")
    return pr


def test_query_yield_and_seed_check(tmp_path, monkeypatch):
    """Scenario "Query count and seed check on OpenAlex" (online-lookups, D27): with the term switch off nothing is
    sent; with the DOI consent off the seed check sends nothing; with both on the count is shown with the ledger and
    the note that it is not comparable with a term count, the third seed is listed as "dry run hit, OpenAlex miss",
    the requests carry the query string and Development seed DOIs only (no Validation seed), and they are logged as
    query_yield and query_seed_check. The route answers the same; exactly one of TERM or --query."""
    from tests.unit.test_dry_run import _cli, _client
    from wider.search.term_yield import NOT_COMPARABLE, OA_MISS, SHARED_DOI

    calls: list = []
    p = _oa_proj(tmp_path, monkeypatch, calls)
    sent = "(meeting* OR huddle)"
    # exactly one of TERM or --query; --seeds goes with --query
    assert _cli(p, "query", "yield").exit_code == 1 and _cli(p, "query", "yield", "x", "--query").exit_code == 1
    assert _cli(p, "query", "yield", "x", "--seeds").exit_code == 1
    c = _client(p)
    assert c.post("/api/query/yield", json={}).status_code == 400
    assert c.post("/api/query/yield", json={"term": "x", "query": True}).status_code == 400
    # the term switch off: nothing is sent, by the CLI or the route
    r = _cli(p, "query", "yield", "--query", "--seeds")
    assert r.exit_code == 2 and "wider lookups terms allow" in r.output and calls == []
    rr = c.post("/api/query/yield", json={"query": True})
    assert rr.status_code == 403 and rr.json()["detail"]["consent_required"]["switch"] == "terms"
    assert rr.json()["detail"]["consent_required"]["fields"] == ["search terms or a query"] and calls == []
    # the term switch on, the DOI consent off: the seed check sends nothing at all, and says what it would send
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "--query", "--seeds")
    out = " ".join(r.output.split())
    assert r.exit_code == 2 and "wider lookups allow" in out and calls == []
    assert "DOI of each Development seed paper (never a Validation paper) with the query" in out
    rr = c.post("/api/query/yield", json={"query": True, "seeds": True})
    assert rr.status_code == 403 and "switch" not in rr.json()["detail"]["consent_required"] and calls == []
    assert lookups.log(p) == []
    # the count alone needs only the term switch
    r = _cli(p, "query", "yield", "--query")
    assert r.exit_code == 0, r.output
    assert "5678 works on OpenAlex (title_and_abstract.search.exact)" in r.output and NOT_COMPARABLE in r.output
    assert f"sent to OpenAlex (title_and_abstract.search.exact): {sent}" in r.output
    assert "lost: the query has truncation, so it is sent to title_and_abstract.search.exact" in r.output
    assert len(calls) == 1 and dict(calls[0].url.params) == {
        "filter": f"title_and_abstract.search.exact:{sent}", "per-page": "1", "select": "id"}   # no title cached
    # both on: the count comes from the cache, the seed check asks for the three Development DOIs only
    assert _cli(p, "lookups", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "--query", "--seeds")
    assert r.exit_code == 0, r.output
    assert len(calls) == 2 and dict(calls[1].url.params) == {
        "filter": f"doi:10.1000/dev1|10.1000/dev2|10.1000/dev3,title_and_abstract.search.exact:{sent}",
        "select": "id,doi", "per-page": "200"}
    assert not any("val" in str(q.url) for q in calls)   # no Validation seed, nor a DOI it shares
    lines = r.output.splitlines()
    assert "Development seeds: 2 of 5 found on OpenAlex (dry run on scopus; 3 DOIs sent, no Validation seed)" in r.output
    assert any(ln.strip().startswith("B003") and "dry run hit, OpenAlex miss" in ln for ln in lines)
    assert any(ln.strip().startswith("B001") and "dry run hit, OpenAlex hit" in ln for ln in lines)
    assert any(ln.strip().startswith("B004") and "not checked: no DOI" in ln for ln in lines)
    assert any(ln.strip().startswith("B005") and SHARED_DOI in ln for ln in lines)
    assert sorted((e["purpose"], e["service"], e["n_items"], e["n_failed"]) for e in lookups.log(p)) == [
        ("query_seed_check", "openalex", 1, 0), ("query_yield", "openalex", 1, 0)]
    # the route gives the same answer from the cache (no new request), for the version shown on the page
    res = c.post("/api/query/yield", json={"query": True, "seeds": True, "version": 1}).json()
    assert len(calls) == 2 and res["cached"] is True and res["count"] == 5678 and res["sent"] == sent
    assert {x["benchmark_id"]: (x["dry_run"], x["openalex"], x["note"]) for x in res["seeds"]["rows"]} == {
        "B001": ("hit", "hit", ""), "B002": ("hit", "hit", ""), "B003": ("hit", "miss", OA_MISS),
        "B004": ("hit", "not checked", "not checked: no DOI"), "B005": ("hit", "not checked", SHARED_DOI)}
    assert c.post("/api/query/yield", json={"query": True, "version": 9}).status_code == 400


def test_seed_check_names_what_it_sends_and_never_guesses(tmp_path, monkeypatch):
    """Risk-checker findings on D27: a DOI cell holding two DOIs is never sent (one piece a Validation paper's DOI:
    "a Validation paper has the same DOI"; two Development DOIs: "more than one DOI"); the term-switch refusal and
    prompt of the seed check name the seed DOIs too, the DOI-consent dialog says it turns on lookups for the whole
    project; an answer with more matches than it lists, or without its list, reads "not checked", never a miss."""
    import httpx

    from tests.unit.test_dry_run import _cli, _client
    from wider.core.models import BenchmarkPaper
    from wider.search.term_yield import NO_ANSWER, NOT_ONE_DOI, SHARED_DOI, query_yield

    calls: list = []
    p = _oa_proj(tmp_path, monkeypatch, calls)
    p.write_benchmark(p.read_benchmark() + [
        BenchmarkPaper(benchmark_id="B006", title="Meeting pair", doi="10.1000/dev6|10.1000/VAL1", set="Development"),
        BenchmarkPaper(benchmark_id="B007", title="Meeting pair two", doi="10.1000/dev7,10.1000/dev8",
                       set="Development")])
    # the term switch off, the DOI consent on: the refusal and the prompt name the seed DOIs, not only the query
    assert _cli(p, "lookups", "allow").exit_code == 0
    d = _client(p).post("/api/query/yield", json={"query": True, "seeds": True}).json()["detail"]
    assert d["consent_required"]["switch"] == "terms" and calls == []
    assert d["consent_required"]["fields"] == ["query", "DOI of each Development seed paper (never a Validation paper)"]
    monkeypatch.setattr("wider.cli_lookups._interactive", lambda: True)
    r = CliRunner().invoke(cli_app, ["query", "yield", "--query", "--seeds", "--dir", str(p.root)], input="n\n")
    out = " ".join(r.output.split())
    assert r.exit_code == 2 and calls == []
    assert "WIDER sends the query and the DOI of each Development seed paper (never a Validation paper) to OpenAlex" in out
    # the term switch on, the DOI consent off: the dialog says the consent is for the whole project
    assert _cli(p, "lookups", "revoke").exit_code == 0 and _cli(p, "lookups", "terms", "allow").exit_code == 0
    d = _client(p).post("/api/query/yield", json={"query": True, "seeds": True}).json()["detail"]
    assert "whole project" in d["consent_required"]["note"] and calls == []
    from tests.unit.test_backends_js_node import STATIC
    js = (STATIC / "common.js").read_text(encoding="utf-8")
    dialog = js[js.index("function askLookupConsent"):js.index("// The backend consent dialog")]
    assert "${info.note ? `<p>${esc(info.note)}</p>` : \"\"}" in dialog   # in normal text, not the muted line
    assert _cli(p, "lookups", "allow").exit_code == 0
    from wider.core.project import Project

    res = query_yield(Project.find(p.root), seeds=True)
    rows = {x["benchmark_id"]: x for x in res["seeds"]["rows"]}
    assert rows["B006"]["note"] == SHARED_DOI and rows["B007"]["note"] == NOT_ONE_DOI
    assert not any("val" in str(q.url).lower() or "dev6" in str(q.url) or "dev7" in str(q.url) for q in calls)
    # an incomplete answer (more matches than listed) and an answer without its list: not checked, never a miss
    for body in ({"meta": {"count": 3}, "results": [{"doi": "https://doi.org/10.1000/dev1"}]}, {"meta": {"count": 0}}):
        monkeypatch.setattr("wider.enrich.sources._client", lambda *a, b=body, **k: mock_client(
            lambda req, b=b: httpx.Response(200, json=b if req.url.params["filter"].startswith("doi:") else
                                            {"meta": {"count": 1}, "results": []}), []))
        from wider.core.lookups import clear_cache
        clear_cache(Project.find(p.root))
        rows = {x["benchmark_id"]: x for x in query_yield(Project.find(p.root), seeds=True)["seeds"]["rows"]}
        assert {k: (rows[k]["openalex"], rows[k]["note"]) for k in ("B001", "B003")} == {
            "B001": ("not checked", NO_ANSWER), "B003": ("not checked", NO_ANSWER)}


def test_openalex_rendering_and_ledger():
    """D27's renderer: blocks become OR lists, phrases stay quoted, NOT follows the positives, a Near group becomes
    AND, a comma is sent as a space; without truncation the stemmed field, with it the exact field; the ledger says
    each of these once."""
    from wider.search.ir import Query
    from wider.search.render.openalex import EXACT, FIELD, rendering

    q = Query.model_validate({"blocks": [
        {"key": "a", "name": "Meetings", "fields": ["title", "abstract", "keywords"], "terms": [
            "meeting", '"team huddle*"', {"near": [["alpha", "beta"], "coupling"], "distance": 3}]},
        {"key": "b", "name": "Errors", "fields": ["title"], "terms": ["error, slip", "lapse"]},
        {"key": "c", "name": "Ships", "terms": ["ship"]}], "logic": {"and": ["a", "b", {"not": "c"}]}})
    r = rendering(q)
    assert r["field"] == FIELD
    assert r["query"] == '(meeting OR "team huddle" OR ((alpha OR beta) AND coupling)) AND ("error slip" OR lapse) NOT (ship)'
    assert [x["reason"] for x in r["ledger"]] == ["near_as_and", "keywords_not_searched", "field_widened",
                                                  "phrase_truncation", "comma", "stemmed"]
    assert [x["kind"] for x in r["ledger"]] == ["lost", "lost", "wider", "lost", "lost", "wider"]
    q2 = Query.model_validate({"blocks": [{"key": "a", "terms": ["meet*", "huddle"]}], "logic": "a"})
    r2 = rendering(q2)
    assert r2["field"] == EXACT and r2["query"] == "(meet* OR huddle)" and r2["ledger"][-1]["reason"] == "no_stemming"


def test_openalex_check_on_the_page(tmp_path, monkeypatch):
    """The "OpenAlex check" on the Search strings page (D27): its buttons post {query, seeds, version} to the route
    the CLI twin uses, and the answer shows what was sent, the count with the not-comparable note, the ledger, and
    each seed with the differing one marked."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from tests.unit.test_dry_run import _client

    calls: list = []
    p = _oa_proj(tmp_path, monkeypatch, calls)
    lookups.allow_terms(p, author="test")
    lookups.allow(p, author="test")
    c = _client(p)
    d = c.get("/api/query/render").json()
    res = c.post("/api/query/yield", json={"query": True, "seeds": True, "version": d["row"]["version"]}).json()
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    box = html[html.index('id="q-oa-check"'):html.index("</details>", html.index('id="q-oa-check"'))]
    assert "OpenAlex check" in box and "wider query yield --query [--seeds]" in box and "never a Validation seed" in box
    page = (STATIC / "yield.js").read_text(encoding="utf-8")
    out = run_node(ESC + PAGE + page + f"""
replies["/api/query/yield"] = () => ({json.dumps(res)});
$("#q-oa-check").innerHTML = {json.dumps(box)};
TermYield.render({json.dumps(d)});
await click("#q-oa-check", "[data-oa-run]", (b) => b.dataset.oaRun === "seeds");
console.log(JSON.stringify({{ out: $("#q-oa-out").innerHTML, msg: $("#q-oa-msg").textContent, calls }}));
""")
    assert out["calls"] == [["/api/query/yield", "POST", {"query": True, "seeds": True, "version": d["row"]["version"]}]]
    assert "5678 works on OpenAlex (title_and_abstract.search.exact)" in out["out"] and "not comparable" in out["out"]
    assert "(meeting* OR huddle)" in out["out"] and "lost: the query has truncation" in out["out"]
    assert 'data-seed="B003"' in out["out"] and 'class="bad">dry run hit, OpenAlex miss' in out["out"]
    assert "2 of 5 found on OpenAlex" in out["out"] and out["msg"] == ""


def test_term_yield_count(tmp_path, monkeypatch):
    """Scenarios "A count, a cached count and a failure" and "Nothing is counted while the switch is off"
    (online-lookups, 2c D16): the CLI and the Yield column show "1234 works on OpenAlex" with the date; asking again
    shows it marked cached with no new request; a term answered 500 shows "not available" and is logged as failed;
    with the switch off the column says so and a Count click only reaches the consent step, sending nothing."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from tests.unit.test_dry_run import _cli, _client
    from wider.core.provenance import utcnow

    calls: list = []
    p = _yield_proj(tmp_path, monkeypatch, calls)
    today = utcnow()[:10]
    # switch off: the render route says so, and the column draws no button (the mock saw nothing)
    d = _client(p).get("/api/query/render").json()
    assert d["terms_allowed"] is False
    assert d["yield_terms"] == [{"key": "a", "name": "Meetings", "terms": [
        {"term": "meeting*", "countable": True}, {"term": "huddle", "countable": True},
        {"term": "team error", "countable": False}]}]
    page = (STATIC / "yield.js").read_text(encoding="utf-8")
    # a Count click goes to WIDER's own route, which answers 403 for the term switch before sending; api() opens the
    # consent dialog, and Cancel leaves the cell saying nothing was sent
    rr = _client(p).post("/api/query/yield", json={"term": "huddle"})
    assert rr.status_code == 403 and rr.json()["detail"]["consent_required"]["switch"] == "terms"
    off = run_node(ESC + PAGE + page + f"""
replies["/api/query/yield"] = () => {{ const e = new Error("Nothing was sent: search-term lookups are off for this project."); e.cancelled = true; throw e; }};
TermYield.render({json.dumps(d)});
const html = $("#q-yield").innerHTML;
await click("#q-yield", "[data-yield-term]", (b) => b.dataset.yieldTerm === "huddle");
const outs = [...$("#q-yield").querySelectorAll("[data-yield-out]")].map(o => [o.dataset.yieldOut, o.textContent]);
console.log(JSON.stringify({{ html, outs, calls }}));
""")
    assert off["html"].count("term lookups are off (Settings)") == 2 and "not counted (proximity)" in off["html"]
    assert off["outs"] == [["meeting*", ""], ["huddle", "Nothing was sent: search-term lookups are off for this project."]]
    assert off["calls"] == [["/api/query/yield", "POST", {"term": "huddle"}]]
    assert calls == [] and lookups.log(p) == []   # the mock OpenAlex saw nothing
    # "Allow and continue" in the dialog: the retried count shows, and no cell says the lookups are off any more
    allowed = run_node(ESC + PAGE + page + f"""
replies["/api/query/yield"] = () => ({{ line: "1234 works on OpenAlex, {today}" }});
TermYield.render({json.dumps(d)});
await click("#q-yield", "[data-yield-term]", (b) => b.dataset.yieldTerm === "huddle");
const outs = [...$("#q-yield").querySelectorAll("[data-yield-out]")].map(o => [o.dataset.yieldOut, o.textContent]);
console.log(JSON.stringify({{ html: $("#q-yield").innerHTML, outs }}));
""")
    assert "term lookups are off" not in allowed["html"]
    assert allowed["outs"] == [["meeting*", ""], ["huddle", f"1234 works on OpenAlex, {today}"]]
    # switch on: a count, then the same count from the cache, then a failure
    assert _cli(p, "lookups", "terms", "allow").exit_code == 0
    r = _cli(p, "query", "yield", "meeting*")
    assert r.exit_code == 0 and f"meeting: 1234 works on OpenAlex, {today}" in r.output, r.output
    assert "cached" not in r.output and len(calls) == 1 and calls[0].url.params["search"] == "meeting"
    # a cached count shows the date it was fetched, not today's (spec-checker round 2, F3)
    with p.store.conn as conn:
        conn.execute("UPDATE lookup_cache SET fetched_at='2026-01-02T03:04:05+00:00'")
    r = _cli(p, "query", "yield", "meeting*")
    assert r.exit_code == 0 and "1234 works on OpenAlex, 2026-01-02, cached" in r.output and len(calls) == 1
    res = _client(p).post("/api/query/yield", json={"term": "meeting*"}).json()
    assert res["line"] == "1234 works on OpenAlex, 2026-01-02, cached" and res["cached"] and len(calls) == 1
    r = _cli(p, "query", "yield", "broken")
    assert r.exit_code == 0 and "broken: not available" in r.output and len(calls) == 2
    assert [(e["purpose"], e["n_items"], e["n_failed"]) for e in lookups.log(p)] == [   # newest first
        ("term_yield", 1, 1), ("term_yield", 1, 0)]
    assert _client(p).post("/api/query/yield", json={"term": " * "}).status_code == 400 and len(calls) == 2
    # the column with the switch on: a Count button per word or phrase, the proximity group not counted
    d = _client(p).get("/api/query/render").json()
    assert d["terms_allowed"] is True
    on = run_node(ESC + PAGE + page + f"""
replies["/api/query/yield"] = (url, opts) => ({{ line: "1234 works on OpenAlex, {today}" }});
TermYield.render({json.dumps(d)});
const html = $("#q-yield").innerHTML;
await click("#q-yield", "[data-yield-term]", (b) => b.dataset.yieldTerm === "huddle");
const outs = [...$("#q-yield").querySelectorAll("[data-yield-out]")].map(o => [o.dataset.yieldOut, o.textContent]);
console.log(JSON.stringify({{ html, calls, outs }}));
""")
    assert on["html"].count("data-yield-term=") == 2 and "not counted (proximity)" in on["html"]
    assert "term lookups are off" not in on["html"]
    assert on["calls"] == [["/api/query/yield", "POST", {"term": "huddle"}]]
    assert on["outs"] == [["meeting*", ""], ["huddle", f"1234 works on OpenAlex, {today}"]]
