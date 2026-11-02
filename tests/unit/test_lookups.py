"""Online lookups: consent model, gate and local request log (wider.core.lookups). No network: MockTransport only."""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.helpers import consent, failing_client, mock_client
from wider.cli import app as cli_app
from wider.core import lookups
from wider.core.models import BenchmarkPaper, EmbeddingConfig, ProviderConfig
from wider.core.project import Project
from wider.ui.app import create_app

runner = CliRunner()
STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    return Project.init(tmp_path / "r")


def test_new_project_has_consent_off(project):
    assert project.config.online_lookups.allowed is False
    assert lookups.status(project)["allowed"] is False


def test_old_project_yaml_loads_with_consent_off(project):
    f = project.root / "project.yaml"
    data = yaml.safe_load(f.read_text())
    data.pop("online_lookups", None)
    f.write_text(yaml.safe_dump(data))
    p = Project.load(project.root)
    assert p.config.online_lookups.allowed is False
    assert p.config.model_dump(exclude={"online_lookups"}) == project.config.model_dump(exclude={"online_lookups"})


def test_allow_and_revoke_are_stored_and_journalled(project):
    lookups.allow(project, author="ana")
    p = Project.load(project.root)
    assert p.config.online_lookups.allowed and p.config.online_lookups.allowed_at and p.config.online_lookups.allowed_by == "ana"
    kinds = [r["kind"] for r in project.store.conn.execute("SELECT kind FROM history")]
    assert kinds  # settings changes are journalled
    lookups.revoke(project)
    assert Project.load(project.root).config.online_lookups.allowed is False


def test_require_consent_names_services_and_fields(project, monkeypatch):
    with pytest.raises(lookups.ConsentRequired) as e:
        lookups.require_consent(project, "enrich")
    assert isinstance(e.value, PermissionError)
    assert e.value.services == ["OpenAlex", "Crossref", "Europe PMC", "Semantic Scholar"]
    assert e.value.fields == ["DOI", "title"]
    assert "wider lookups allow" in str(e.value)
    assert e.value.payload()["consent_required"]["purpose"] == "enrich"
    monkeypatch.setenv("WIDER_MAILTO", "someone@example.org")
    with pytest.raises(lookups.ConsentRequired) as e:
        lookups.require_consent(project, "seed_resolve")
    assert e.value.services == ["OpenAlex", "Crossref"]
    assert "contact address (WIDER_MAILTO)" in e.value.fields
    with pytest.raises(ValueError):
        lookups.require_consent(project, "nonsense")


def test_prior_purposes_do_not_need_this_consent(project):
    lookups.require_consent(project, "prior_resolve")  # their own switch is checked by prior.reviews.require_lookup
    lookups.allow(project)
    assert project.config.prior_reviews.allow_openalex_lookup is False
    assert lookups.status(project)["prior_review_lookup"] is False


def test_lookup_client_without_consent_sends_nothing(project):
    calls: list = []
    with pytest.raises(lookups.ConsentRequired):
        with lookups.lookup_client(project, "enrich", client=failing_client(calls)) as c:
            c.get("https://api.openalex.org/works/doi:10.1000/x")
    assert calls == []
    assert lookups.log(project) == []


def test_lookup_client_counts_items_per_service_and_never_stores_bodies(project):
    consent(project)
    secret = "An abstract text returned by the service"
    client = mock_client({"api.openalex.org": {"abstract": secret}, "api.crossref.org": 503})
    with lookups.lookup_client(project, "enrich", client=client) as c:
        for doi in ("10.1000/a", "10.1000/b", "10.1000/c"):
            c.get(f"https://api.openalex.org/works/doi:{doi}", params={"mailto": "x"})
        c.get("https://api.openalex.org/works/doi:10.1000/a")  # the same paper again counts once
        c.get("https://api.crossref.org/works/10.1000/c")
    rows = {r["service"]: r for r in lookups.log(project)}
    assert rows["openalex"]["n_items"] == 3 and rows["openalex"]["n_failed"] == 0
    assert rows["crossref"]["n_items"] == 1 and rows["crossref"]["n_failed"] == 1
    assert {r["purpose"] for r in rows.values()} == {"enrich"}
    dump = " ".join(str(tuple(r)) for r in project.store.conn.execute("SELECT * FROM external_requests"))
    assert secret not in dump and "10.1000" not in dump and "http" not in dump


def test_batched_doi_filter_counts_each_doi(project):
    with lookups.lookup_client(project, "prior_resolve", client=mock_client({"openalex": {"results": []}})) as c:
        c.get("https://api.openalex.org/works", params={"filter": "doi:10.1/a|10.1/b|10.1/c", "per-page": 50})
    (row,) = lookups.log(project)
    assert (row["service"], row["purpose"], row["n_items"]) == ("openalex", "prior_resolve", 3)


def test_failed_run_is_logged_too(project):
    consent(project)

    def boom(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(httpx.ConnectError):
        with lookups.lookup_client(project, "seed_resolve", client=mock_client(boom)) as c:
            c.get("https://api.openalex.org/works/doi:10.1/a")
    (row,) = lookups.log(project)
    assert row["n_items"] == 1 and row["n_failed"] == 1


def test_nested_lookup_client_logs_once(project):
    consent(project)
    with lookups.lookup_client(project, "seed_abstracts", client=mock_client({"openalex": {}})) as outer:
        with lookups.lookup_client(project, "seed_abstracts", client=outer) as inner:
            inner.get("https://api.openalex.org/works/doi:10.1/a")
    assert len(lookups.log(project)) == 1


# ----------------------------------------------------------------- the gate on every lookup surface (spec scenarios)
THIN_RIS = [
    "TY  - JOUR\nTI  - First synthetic record with a short abstract\nDO  - 10.1000/synth.a\nAB  - short\nER  -\n",
    "TY  - JOUR\nTI  - Second synthetic record with a short abstract\nDO  - 10.1000/synth.b\nAB  - brief\nER  -\n",
    "TY  - JOUR\nTI  - Third synthetic record with a short abstract\nDO  - 10.1000/synth.c\nER  -\n",
]


def _thin(project, tmp_path, n=3):
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all

    (tmp_path / "t.ris").write_text("\n".join(THIN_RIS[:n]))
    add_file(project, "scopus", tmp_path / "t.ris")
    import_all(project)
    dedup_run(project)
    return project


@pytest.fixture
def thin(tmp_path, monkeypatch):
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    return _thin(Project.init(tmp_path / "r", platforms=["scopus"]), tmp_path, n=2)


@pytest.fixture
def no_client(monkeypatch):
    """Fail the test when any lookup creates a network client."""
    def boom(*a, **k):
        raise AssertionError("a network client was created without consent")
    monkeypatch.setattr("wider.enrich.sources._client", boom)


def _cli(p, *args, input=None):
    r = runner.invoke(cli_app, [*args, "--dir", str(p.root)], input=input)
    return r, " ".join(r.output.split())   # rich wraps long lines


def _abstracts(p):
    return [(r["record_id"], r["abstract"]) for r in p.store.query("SELECT record_id, abstract FROM records ORDER BY record_id")]


def test_enrichment_without_consent_sends_and_changes_nothing(thin, no_client):
    before = _abstracts(thin)
    r, out = _cli(thin, "enrich")
    assert r.exit_code == 2
    assert "wider lookups allow" in out and "OpenAlex" in out and "Semantic Scholar" in out and "DOI" in out
    assert _abstracts(thin) == before
    assert thin.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 0 and lookups.log(thin) == []
    from wider.enrich.fetch import enrich
    with pytest.raises(lookups.ConsentRequired):
        enrich(thin, client=failing_client())
    assert thin.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 0


def test_local_commands_are_not_affected(thin, no_client):
    assert _cli(thin, "enrich", "--status")[0].exit_code == 0
    assert _cli(thin, "benchmark", "list")[0].exit_code == 0


def test_terminal_prompt_accept_stores_consent_and_continues(thin, monkeypatch):
    monkeypatch.setattr("wider.cli_lookups._interactive", lambda: True)
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client({}))
    r, out = _cli(thin, "enrich", input="y\n")
    assert r.exit_code == 0, r.output
    for name in ("OpenAlex", "Crossref", "Europe PMC", "Semantic Scholar", "DOI", "title", "2 papers"):
        assert name in out
    assert Project.load(thin.root).config.online_lookups.allowed is True
    assert thin.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 2


@pytest.mark.parametrize("answer", ["n\n", "\n"])
def test_terminal_prompt_decline_sends_nothing(thin, monkeypatch, no_client, answer):
    monkeypatch.setattr("wider.cli_lookups._interactive", lambda: True)
    r, out = _cli(thin, "enrich", input=answer)
    assert r.exit_code == 2 and "nothing was sent" in out
    assert Project.load(thin.root).config.online_lookups.allowed is False
    assert thin.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 0
    assert "Allow online lookups" in _cli(thin, "enrich", input="n\n")[1]   # asks again next time


def test_seed_resolve_and_fetch_in_a_script_exit_2(project, no_client):
    project.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="Synthetic seed paper", doi="10.1000/synth.001",
                                            set="Development")])
    csv_before = (project.root / "benchmark.csv").read_bytes()
    r, out = _cli(project, "benchmark", "resolve")
    assert r.exit_code == 2
    for name in ("OpenAlex", "Crossref", "DOI", "title", "wider lookups allow"):
        assert name in out
    assert (project.root / "benchmark.csv").read_bytes() == csv_before
    r, out = _cli(project, "benchmark", "fetch")
    assert r.exit_code == 2 and "wider lookups allow" in out
    assert (project.root / "benchmark.csv").read_bytes() == csv_before and lookups.log(project) == []


def test_add_with_resolve_in_a_script_adds_nothing(project, no_client):
    r, out = _cli(project, "benchmark", "add", "--doi", "10.1000/synth.001", "--resolve")
    assert r.exit_code == 2
    assert Project.load(project.root).read_benchmark() == []
    assert "without --resolve" in out and "wider benchmark resolve" in out
    r, _ = _cli(project, "benchmark", "add", "--doi", "10.1000/synth.001")   # without --resolve it is added
    assert r.exit_code == 0 and len(Project.load(project.root).read_benchmark()) == 1


def test_routes_without_consent_answer_403_and_start_no_job(thin, no_client):
    thin.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="Synthetic seed paper", doi="10.1000/synth.001",
                                         set="Development")])
    c = TestClient(create_app(thin))
    for path in ("/api/benchmark/resolve", "/api/benchmark/fetch", "/api/enrich/run", "/api/jobs/enrich",
                 "/api/jobs/enrich?retry=true"):   # "Retry failed lookups" asks first, like Fetch
        r = c.post(path)
        assert r.status_code == 403, path
        d = r.json()["detail"]
        assert "online lookups" in d["message"].lower() and "wider lookups allow" in d["message"]
        assert d["consent_required"]["services"] and d["consent_required"]["fields"][:2] == ["DOI", "title"]
    assert c.get("/api/jobs").json() == []
    assert lookups.log(thin) == [] and thin.store.scalar("SELECT COUNT(*) FROM enrichment_log") == 0


def test_allow_from_the_dialog_then_the_job_starts(thin, monkeypatch):
    monkeypatch.setattr("wider.enrich.sources._client", lambda: mock_client({}))
    c = TestClient(create_app(thin))
    assert c.post("/api/jobs/enrich").status_code == 403
    s = c.post("/api/lookups/allow").json()   # what "Allow and continue" sends
    assert s["allowed"] is True and s["allowed_at"]
    assert Project.load(thin.root).config.online_lookups.allowed is True
    r = c.post("/api/jobs/enrich")
    assert r.status_code == 200 and r.json()["job_id"]
    got = c.get("/api/lookups").json()
    assert got["allowed"] is True and got["allowed_at"] == s["allowed_at"]


def test_lookups_commands_allow_status_revoke(thin, no_client):
    r, out = _cli(thin, "lookups", "status")
    assert r.exit_code == 0 and "online lookups: off" in out and "OpenAlex" in out
    assert _cli(thin, "lookups", "allow")[0].exit_code == 0
    r, out = _cli(thin, "lookups", "status")
    at = Project.load(thin.root).config.online_lookups.allowed_at
    assert r.exit_code == 0 and "online lookups: on" in out and str(at) in out
    assert _cli(thin, "lookups", "revoke")[0].exit_code == 0
    assert "online lookups: off" in _cli(thin, "lookups", "status")[1]
    assert _cli(thin, "enrich")[0].exit_code == 2   # asks (refuses in a script) again


def test_consent_not_stored_is_reported_and_stays_off(thin, monkeypatch):
    """When project.yaml cannot be written, Allow fails with one plain message in the terminal (exit 1) and in the
    page (HTTP 500 with the same text), and consent stays off. (A read-only project.yaml is not enough to fail: the
    save writes a new file and renames it, so the test makes that write fail.)"""
    def denied(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("wider.core.project.write_atomic", denied)
    want = "the consent was not stored: project.yaml could not be written (Permission denied)"
    for args in (("lookups", "allow"), ("lookups", "terms", "allow")):
        r, out = _cli(thin, *args)
        assert r.exit_code == 1 and want in out, args
    c = TestClient(create_app(thin))
    r = c.post("/api/lookups/allow")
    assert r.status_code == 500 and r.json()["detail"] == want
    assert Project.load(thin.root).config.online_lookups.allowed is False


def test_lookups_log_empty_and_limited(project):
    r, out = _cli(project, "lookups", "log")
    assert r.exit_code == 0 and "no outside request has been logged" in out
    consent(project)
    for svc in ("api.openalex.org", "api.crossref.org"):
        with lookups.lookup_client(project, "seed_resolve", client=mock_client({svc: {}})) as c:
            c.get(f"https://{svc}/works/10.1000/synth.x")
    r, out = _cli(project, "lookups", "log", "--limit", "1")
    assert r.exit_code == 0 and "Crossref" in out and "OpenAlex" not in out
    c = TestClient(create_app(project))
    assert len(c.get("/api/lookups/log?limit=1").json()) == 1
    assert c.get("/api/lookups/log?limit=0").status_code == 400


def test_settings_switch_and_cli_share_one_setting(project):
    c = TestClient(create_app(project))
    assert c.get("/api/lookups").json()["allowed"] is False
    assert c.post("/api/lookups/allow").json()["allowed"] is True
    assert "online lookups: on" in _cli(project, "lookups", "status")[1]
    assert c.get("/api/lookups").json()["allowed"] is True   # the Setup card reads the same route
    assert c.post("/api/lookups/revoke").json()["allowed"] is False
    assert "online lookups: off" in _cli(project, "lookups", "status")[1]


def test_enrichment_run_is_logged_per_service(tmp_path, monkeypatch):
    monkeypatch.delenv("WIDER_MAILTO", raising=False)
    p = consent(_thin(Project.init(tmp_path / "r", platforms=["scopus"]), tmp_path))
    from wider.enrich.fetch import enrich
    long = {f"w{i}": [i] for i in range(40)}

    def handler(request):
        url = str(request.url)
        if "api.openalex.org" in url:
            return httpx.Response(404) if "synth.c" in url else httpx.Response(200, json={"abstract_inverted_index": long})
        if "api.crossref.org" in url:
            return httpx.Response(200, json={"message": {"abstract": "returned text " * 20}})
        return httpx.Response(404)

    stats = enrich(p, pause_s=0, client=mock_client(handler))
    assert stats["filled"] == 3
    rows = {r["service"]: r for r in lookups.log(p) if r["purpose"] == "enrich"}
    assert set(rows) == {"openalex", "crossref"}
    assert rows["openalex"]["n_items"] == 3 and rows["crossref"]["n_items"] == 1
    dump = " ".join(str(tuple(r)) for r in p.store.conn.execute("SELECT * FROM external_requests"))
    assert "returned text" not in dump and "w1" not in dump


def test_prior_review_lookups_logged_and_keep_their_own_switch(project, monkeypatch):
    from wider.prior import chain, reviews
    monkeypatch.setattr("wider.prior.chain.time.sleep", lambda *_: None)
    reviews.add_text(project, "10.1000/synth.p1\n10.1000/synth.p2\n", "Prior S")
    lookups.allow(project)   # consent does not open prior-review lookups
    with pytest.raises(reviews.PolicyError):
        chain.resolve(project, client=failing_client())
    out = _cli(project, "lookups", "status")[1]
    assert "prior-review lookup switch (separate): off" in out and "wider prior-reviews policy" in out
    lookups.revoke(project)
    reviews.set_policy(project, allow_openalex_lookup=True)   # the prior switch alone is enough for prior lookups
    res = chain.resolve(project, client=mock_client({"api.openalex.org": {"results": []}}))
    assert res["attempted"] == 2
    row = next(r for r in lookups.log(project) if r["purpose"] == "prior_resolve")
    assert row["service"] == "openalex" and row["n_items"] >= 2
    assert Project.load(project.root).config.online_lookups.allowed is False
    assert _cli(project, "enrich")[0].exit_code == 2   # ... and does not give consent


def test_consent_dialog_script_loads_first():
    js = (STATIC / "common.js").read_text()
    assert "consent_required" in js and "Allow and continue" in js and "Cancel" in js and "renderLookupsCard" in js
    html = (STATIC / "index.html").read_text()
    scripts = re.findall(r'<script[^>]*src="([^"]+)"', html)
    assert scripts[0] == "/static/common.js"
    assert 'id="settings-lookups"' in html
    assert "renderLookupsCard" in (STATIC / "shell.js").read_text()
    assert "renderLookupsCard" in (STATIC / "setup.js").read_text()


# ----------------------------------------------------------------- what an external call carries (privacy spec)
CARRY_RIS = (
    "TY  - JOUR\nTI  - First synthetic record on shared calendars\nDO  - 10.1000/synth.a\nAB  - quillwort marker one\n"
    "AU  - Zeta, Q.\nJO  - Journal of Synthetic Things\nPY  - 2020\nER  -\n\n"
    "TY  - JOUR\nTI  - Second synthetic record on shared calendars\nAB  - quillwort marker two\n"
    "AU  - Ypsilon, R.\nJO  - Journal of Synthetic Things\nPY  - 2021\nER  -\n")
CARRY_TITLES = ["First synthetic record on shared calendars", "Second synthetic record on shared calendars"]
CARRY_ABSTRACTS = ["quillwort marker one", "quillwort marker two"]
NOT_SENT = ("quillwort", "Zeta", "Ypsilon", "Journal of Synthetic Things", "2020", "2021")   # abstract, authors, journal, year
CONTACT = "someone@example.org"


@pytest.fixture
def carry(tmp_path, monkeypatch):
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all

    monkeypatch.setattr("wider.enrich.fetch.time.sleep", lambda *_: None)
    p = Project.init(tmp_path / "r", platforms=["scopus"], questions=["Which synthetic records describe shared calendars?"])
    (tmp_path / "c.ris").write_text(CARRY_RIS)
    add_file(p, "scopus", tmp_path / "c.ris")
    import_all(p)
    dedup_run(p)
    return p


@pytest.fixture
def offline_lookups(monkeypatch):
    """Every lookup client is the real one (``wider.enrich.sources._client``: its headers, timeout, redirects) on a
    MockTransport that answers 404 and records each request."""
    sent: list[httpx.Request] = []
    real = httpx.Client

    def route(request):
        sent.append(request)
        return httpx.Response(404)

    monkeypatch.setattr("wider.enrich.sources.httpx.Client", lambda **kw: real(transport=httpx.MockTransport(route), **kw))
    return sent


def test_lookups_carry_dois_or_titles_and_a_fixed_user_agent(carry, offline_lookups, monkeypatch):
    from urllib.parse import unquote_plus

    from wider.construct import seed
    from wider.enrich.fetch import enrich
    from wider.enrich.sources import USER_AGENT

    consent(carry)
    seed.add_by_dois(carry, ["10.1000/synth.s1"])
    seed.add_by_titles(carry, ["A synthetic seed title on shared calendars"])
    named = {"10.1000/synth.a", "10.1000/synth.s1", *CARRY_TITLES, "A synthetic seed title on shared calendars"}
    for mailto in (None, CONTACT):
        if mailto:
            monkeypatch.setenv("WIDER_MAILTO", mailto)
        else:
            monkeypatch.delenv("WIDER_MAILTO", raising=False)
        offline_lookups.clear()
        enrich(carry, pause_s=0, retry=True)
        seed.resolve(carry, only_unresolved=False)
        hosts = {q.url.host for q in offline_lookups}
        assert hosts == {"api.openalex.org", "api.crossref.org", "www.ebi.ac.uk", "api.semanticscholar.org"}
        for q in offline_lookups:
            url = unquote_plus(str(q.url))
            assert q.method == "GET" and q.content == b"", url
            # the tool is named by the fixed User-Agent; the contact address only when WIDER_MAILTO is set
            assert q.headers["user-agent"] == USER_AGENT + (f" mailto:{mailto}" if mailto else ""), url
            if mailto is None:
                assert "mailto" not in url and CONTACT not in url
            elif q.url.host in ("api.openalex.org", "api.crossref.org"):
                assert q.url.params["mailto"] == mailto
            # each request names exactly one paper, by DOI or title, and nothing else about it
            assert sum(x in url for x in named) == 1, url
            assert not any(x in url for x in NOT_SENT), url
        # the paper without a DOI was looked up by its title, on OpenAlex only
        by_title = [q for q in offline_lookups if CARRY_TITLES[1] in unquote_plus(str(q.url))]
        assert [(q.url.host, q.url.params["search"]) for q in by_title] == [("api.openalex.org", CARRY_TITLES[1])]


def _cloud_llm(monkeypatch, bodies: list[dict]):
    """An OpenAI-compatible endpoint on a MockTransport: records each request body and answers from the schema."""
    import json

    from wider.providers.fake import _example_from_schema

    real = httpx.AsyncClient

    def answer(request):
        body = json.loads(request.content)
        bodies.append(body)
        schema = body["response_format"]["json_schema"]["schema"]
        out = {"status": "ok", "sum": 42} if "sum" in schema.get("properties", {}) else _example_from_schema(schema)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "m"})

    monkeypatch.setattr("wider.providers.openai_compat.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    return ProviderConfig(name="cloud", kind="openai_compat", model="m", base_url="https://llm.example.invalid/v1")


def test_screening_call_carries_one_records_title_and_abstract(carry, monkeypatch):
    from wider.construct import artifacts
    from wider.core.summary import provider_locality
    from wider.screen.codebook import build, load_prompt, save_prompt_artifact
    from wider.screen.engine import screen

    bodies: list[dict] = []
    cfg = _cloud_llm(monkeypatch, bodies)
    assert provider_locality(cfg.kind, cfg.base_url) == "cloud"
    carry.config.providers.append(cfg)
    carry.save()
    from wider.core.backend_consent import allow_backend
    allow_backend(carry, "cloud", ["research"])   # a cloud backend is contacted only with consent
    artifacts.save_new_version(carry, "codebook", {
        "question": "Which synthetic records describe a shared object?",
        "target_description": "Synthetic records that describe a shared object.",
        "elements": [{"id": "e1", "name": "Shared object named", "rule": "A shared object is named."},
                     {"id": "e2", "name": "More than one user", "rule": "More than one user is named."}],
        "exclusion_reasons": [{"code": "a", "text": "no shared object", "element": "e1"}]}, origin="manual")
    save_prompt_artifact(carry, build(carry))
    prompt = load_prompt(carry)[1]["prompt"]
    res = screen(carry, "corpus", provider_name="cloud")
    assert res["n_records"] == 2 and bodies
    users = set()
    for b in bodies:
        assert [m["role"] for m in b["messages"]] == ["system", "user"]
        system, user = b["messages"][0]["content"], b["messages"][1]["content"]
        assert system == prompt and not any(t in system for t in CARRY_TITLES)
        # the prompt with exactly one record's title and abstract, and nothing else about the record
        i = [t in user for t in CARRY_TITLES].index(True)
        assert user == f"Title: {CARRY_TITLES[i]}\n\nAbstract: {CARRY_ABSTRACTS[i]}"
        assert CARRY_TITLES[1 - i] not in user and CARRY_ABSTRACTS[1 - i] not in user
        dump = str(b)
        assert not any(x in dump for x in ("10.1000/synth.a", "Zeta", "Ypsilon", "Journal of Synthetic Things"))
        users.add(user)
    assert len(users) == 2


def test_backend_check_sends_fixed_sentences_and_no_project_data(carry, monkeypatch):
    import asyncio

    from wider.core.backend_check import check_backends
    from wider.embeddings import fake as fake_emb
    from wider.embeddings.registry import PROBE_TEXTS
    from wider.providers.registry import PROBE_SYSTEM, PROBE_USER

    bodies: list[dict] = []
    carry.config.providers.append(_cloud_llm(monkeypatch, bodies))
    carry.config.embeddings.append(EmbeddingConfig(name="fake", kind="fake", model="fake", dim=16))
    carry.config.default_provider, carry.config.default_embedding = "cloud", "fake"
    carry.save()
    from wider.core.backend_consent import allow_backend
    allow_backend(carry, "cloud", ["probe"])   # the check probes a cloud backend only with consent
    carry.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="A synthetic seed title on shared calendars",
                                          doi="10.1000/synth.s1", set="Development")])
    embedded: list[list[str]] = []
    orig = fake_emb.FakeEmbedder.embed
    monkeypatch.setattr(fake_emb.FakeEmbedder, "embed",
                        lambda self, texts, progress=None: embedded.append(list(texts)) or orig(self, texts, progress))
    out = asyncio.run(check_backends(Project.load(carry.root)))
    assert out["ok"], out
    # the LLM probe: the fixed test sentences only
    assert len(bodies) == 1
    assert bodies[0]["messages"] == [{"role": "system", "content": PROBE_SYSTEM}, {"role": "user", "content": PROBE_USER}]
    # the embedding probe: the fixed test sentences only
    assert embedded == [PROBE_TEXTS]
    project_text = [*CARRY_TITLES, *CARRY_ABSTRACTS, "10.1000/synth", "shared calendars", "Zeta", "Ypsilon",
                    "Which synthetic records", "A synthetic seed title"]
    dump = str(bodies) + str(embedded)
    assert not any(x in dump for x in project_text)


def _card_js(status: dict, toggle: str | None = None) -> dict:
    """renderLookupsCard from common.js on a stand-in card: the HTML it renders and the POSTs a switch change sends."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = (STATIC / "common.js").read_text(encoding="utf-8")
    a = js.index("async function renderLookupsCard")
    fn = js[a:js.index("\n}\n", a) + 3]
    fold = next(ln for ln in js.splitlines() if ln.startswith("const cmdFold = "))
    script = f"""
const esc = (s) => String(s == null ? '' : s);
const fmtWhen = (t) => String(t || '');
{fold}
const sent = []; const status = {json.dumps(status)};
async function api(path, opts) {{ if (opts) sent.push(path); return {{ ...status, log: [] }}; }}
const els = {{}};
const card = {{ innerHTML: "", querySelector: (sel) => (els[sel] ||= {{ checked: false, disabled: false }}) }};
{fn}
(async () => {{
  await renderLookupsCard(card);
  const html = card.innerHTML;
  const t = {json.dumps(toggle)};
  if (t) {{ const sw = els[t]; sw.checked = !sw.checked; await sw.onchange(); }}
  console.log(JSON.stringify({{ html, sent }}));
}})();"""
    r = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_term_switch_is_separate(project):
    """Scenario "Two switches" (2c D16, owner's answer to D0c Q1): the term switch is off by default, separate from the
    DOI consent both ways, and a term lookup needs it alone."""
    c = TestClient(create_app(project))
    _cli(project, "lookups", "allow")
    out = _cli(project, "lookups", "status")[1]
    assert "online lookups: on" in out and "search-term lookups (separate): off" in out
    assert "wider lookups terms allow" in out
    with pytest.raises(lookups.TermsRequired) as e:
        lookups.require_consent(Project.find(project.root), "term_yield")
    assert "sends search terms or a query to OpenAlex; turn on with `wider lookups terms allow`" in str(e.value)
    assert e.value.payload()["consent_required"] == {"purpose": "term_yield", "services": ["OpenAlex"],
                                                     "fields": ["search terms or a query"], "switch": "terms"}
    sent = []
    with pytest.raises(lookups.TermsRequired):   # nothing is sent: the gate comes before the client
        with lookups.lookup_client(Project.find(project.root), "term_yield",
                                   mock_client(lambda r: sent.append(r) or httpx.Response(200, json={}))):
            pass
    assert sent == [] and lookups.log(Project.find(project.root)) == []
    # turned on: the card's data shows both on; the CLI and the route store one setting
    r, out = _cli(project, "lookups", "terms", "allow")
    assert r.exit_code == 0 and "search-term lookups allowed" in out
    s = c.get("/api/lookups").json()
    assert s["allowed"] is True and s["terms_allowed"] is True and s["terms_allowed_at"]
    card = _card_js(s)
    assert "search terms allowed" in card["html"] and "data-terms-switch checked" in card["html"]
    assert "data-lookups-switch checked" in card["html"]
    # revoking the DOI consent leaves the term switch on, and a term lookup still passes on it alone
    _cli(project, "lookups", "revoke")
    out = _cli(project, "lookups", "status")[1]
    assert "online lookups: off" in out and "search-term lookups (separate): on" in out
    lookups.require_consent(Project.find(project.root), "term_yield")
    with pytest.raises(lookups.ConsentRequired):
        lookups.require_consent(Project.find(project.root), "seed_chain")
    # and the other way: the term switch off leaves the DOI consent as it is
    _cli(project, "lookups", "allow")
    assert c.post("/api/lookups/terms/revoke").json()["terms_allowed"] is False
    out = _cli(project, "lookups", "status")[1]
    assert "online lookups: on" in out and "search-term lookups (separate): off" in out
    # the CLI twin: `wider lookups terms revoke` leaves the DOI consent on too (spec-checker round 2, F2)
    _cli(project, "lookups", "terms", "allow")
    assert "search-term lookups: off" in _cli(project, "lookups", "terms", "revoke")[1]
    out = _cli(project, "lookups", "status")[1]
    assert "online lookups: on" in out and "search-term lookups (separate): off" in out
    # each switch on the card posts to its own route
    off = {**s, "terms_allowed": False}
    assert _card_js(off, "[data-terms-switch]")["sent"] == ["/api/lookups/terms/allow"]
    assert _card_js(off, "[data-lookups-switch]")["sent"] == ["/api/lookups/allow"]   # stand-in starts unchecked
    assert "no search term or query is sent" in _card_js(off)["html"]
