"""Setup-page routes (wider/ui/routes_setup.py) through the FastAPI TestClient; no network."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_screen import project  # noqa: F401  (the screened synthetic project fixture)
from wider.construct import seed
from wider.core.project import Project
from wider.ui.app import create_app

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"


@pytest.fixture
def client(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["ebsco", "scopus"])
    return TestClient(create_app(p))


def test_provider_routes(client, monkeypatch):
    monkeypatch.delenv("MY_KEY", raising=False)
    d = client.get("/api/providers").json()
    assert d["kinds"] == ["openai_compat", "ollama", "anthropic", "cli_agent", "litellm", "typesafe"] and d["stages"] == ["construct", "screen", "review"]
    r = client.post("/api/providers", json={"name": "local", "kind": "openai_compat", "model": "m", "base_url": "http://127.0.0.1:1/v1",
                                            "api_key_env": "MY_KEY", "set_default": True})
    assert r.status_code == 200 and r.json()["api_key_env"] == "MY_KEY"
    d = client.get("/api/providers").json()
    row = next(x for x in d["providers"] if x["name"] == "local")
    assert d["default"] == "local" and row["key_set"] is False and row["default"] is True
    assert client.post("/api/providers", json={"name": "agent", "kind": "cli_agent"}).status_code == 400
    r = client.post("/api/providers", json={"name": "agent", "kind": "cli_agent", "command": '["tool", "{prompt}"]'})
    assert r.status_code == 400 and "{prompt}" in r.json()["detail"]
    assert client.post("/api/providers", json={"name": "agent", "kind": "cli_agent", "command": '["tool", "--model", "{model}"]'}).status_code == 200
    assert client.post("/api/providers/stage", json={"stage": "screen", "name": "agent"}).json()["stage_providers"] == {"screen": "agent"}
    assert client.post("/api/providers/stage", json={"stage": "screen", "name": "zzz"}).status_code == 404
    assert client.post("/api/providers/default", json={"name": "agent"}).json()["default"] == "agent"
    assert client.get("/api/project").json()["config"]["stage_providers"] == {"screen": "agent"}
    r = client.delete("/api/providers/agent")
    assert r.status_code == 200 and r.json()["default"] != "agent"
    assert client.get("/api/providers").json()["stage_providers"] == {}
    assert client.delete("/api/providers/agent").status_code == 404


def test_embedding_routes(client):
    r = client.post("/api/embeddings", json={"name": "api-emb", "kind": "openai_compat", "model": "e", "dim": 128,
                                             "base_url": "http://127.0.0.1:1/v1", "api_key_env": "E_KEY", "set_default": True})
    assert r.status_code == 200 and r.json()["dim"] == 128
    d = client.get("/api/embeddings").json()
    assert d["default"] == "api-emb" and "sentence_transformers" in d["kinds"]
    local = next(x for x in d["embeddings"] if x["kind"] == "sentence_transformers")
    assert "downloaded" in local and "size_mb" in local
    assert client.post("/api/embeddings", json={"name": "x", "kind": "nope"}).status_code == 400
    assert client.post("/api/embeddings/default", json={"name": "nope"}).status_code == 404
    assert client.post("/api/embeddings/nope/download").status_code == 404
    assert client.delete("/api/embeddings/api-emb").status_code == 200
    assert client.get("/api/embeddings").json()["default"] != "api-emb"


def test_seed_routes(client, monkeypatch):
    monkeypatch.setattr(seed, "openalex_work", lambda doi, c: {"source": "openalex", "title": "Found Title", "year": 2001, "doi": doi,
                                                               "abstract": " ".join(["w"] * 60), "first_author": "Someone"}
                        if doi == "10.1000/s.1" else None)
    monkeypatch.setattr(seed, "crossref_work", lambda doi, c: None)
    monkeypatch.setattr(seed, "openalex_search", lambda title, c: [])
    monkeypatch.setattr("wider.enrich.sources._client", lambda: __import__("types").SimpleNamespace(close=lambda: None))
    d = client.get("/api/benchmark/seed").json()
    assert d["rows"] == [] and d["dev_share"] == 0.57 and "development" in d["rule"].lower()
    r = client.post("/api/benchmark/add", json={"text": "10.1000/s.1\n10.1000/s.2\nA Title Only\n", "set": "Validation"})
    assert r.status_code == 200 and [a["benchmark_id"] for a in r.json()["added"]] == ["B001", "B002", "B003"]
    assert all(a["set"] == "Validation" for a in r.json()["added"])
    r = client.post("/api/benchmark/add", json={"text": "title,doi\nFrom File,10.1000/s.3\n", "filename": "seeds.csv"})
    assert [a["title"] for a in r.json()["added"]] == ["From File"]
    denied = client.post("/api/benchmark/resolve")   # online lookups are off by default: nothing is sent
    assert denied.status_code == 403 and denied.json()["detail"]["consent_required"]["purpose"] == "seed_resolve"
    assert client.post("/api/lookups/allow").json()["allowed"] is True
    r = client.post("/api/benchmark/resolve").json()
    assert r["resolved"] == 1 and r["unresolved"] == 3
    rows = {x["benchmark_id"]: x for x in r["rows"]}
    assert rows["B001"]["title"] == "Found Title" and rows["B001"]["resolved"] == "resolved"
    assert client.post("/api/benchmark/assign", json={"benchmark_id": "B001", "set": "Development"}).json()["set"] == "Development"
    assert client.post("/api/benchmark/assign", json={"benchmark_id": "B999", "set": "Development"}).status_code == 404
    s = client.post("/api/benchmark/split", json={"dev_share": 0.5}).json()
    assert s["n"] == 4 and len(s["development"]) == 2 and "applied" not in s
    s = client.post("/api/benchmark/split", json={"dev_share": 0.5, "apply": True}).json()
    assert s["applied"] is True
    sets = {x["benchmark_id"]: x["set_name"] for x in client.get("/api/benchmark/seed").json()["rows"]}
    assert [k for k, v in sets.items() if v == "Development"] == s["development"]
    assert client.post("/api/benchmark/split", json={"dev_share": 2}).status_code == 400
    assert client.delete("/api/benchmark/B004").status_code == 200
    assert client.delete("/api/benchmark/B004").status_code == 404
    assert len(client.get("/api/benchmark/seed").json()["rows"]) == 3


def test_platform_routes_and_render(client):
    d = client.get("/api/platforms").json()
    assert [c["key"] for c in d["chosen"]] == ["ebsco", "scopus"] and d["chosen"][0]["role"] == "development"
    assert {c["key"] for c in d["catalogue"]} >= {"ebsco", "scopus", "wos", "pubmed"} and "ebsco" in d["families"]
    assert client.post("/api/platforms/add", json={"key": "mine"}).status_code == 400
    r = client.post("/api/platforms/add", json={"key": "mine", "label": "My host", "syntax": "scopus"})
    assert r.status_code == 200 and r.json()["chosen"][-1]["custom"] is True
    r = client.post("/api/platforms/order", json={"keys": ["mine", "scopus"]})
    assert [c["key"] for c in r.json()["chosen"]] == ["mine", "scopus", "ebsco"]
    assert client.post("/api/platforms/order", json={"keys": ["nope"]}).status_code == 400
    r = client.post("/api/platforms/role", json={"key": "scopus", "role": "development"})
    assert [(c["key"], c["role"]) for c in r.json()["chosen"]] == [("mine", "validation"), ("scopus", "development"), ("ebsco", "validation")]
    assert client.post("/api/platforms/role", json={"key": "scopus", "role": "primary"}).status_code == 400
    # the query platforms listing exposes the custom entry; rendering covers only chosen platforms
    qp = client.get("/api/query/platforms").json()
    assert qp["mine"]["custom"] is True and qp["mine"]["style"] == "scopus"
    from wider.construct import artifacts

    p = Project.load(client.app.state.project.root)
    artifacts.save_new_version(p, "synonyms", {"blocks": [{"key": "a", "name": "a", "terms": ["alpha", "beta gamma"]}], "logic": "a"},
                               origin="test", note="t")
    p.close()
    d = client.get("/api/query/render").json()
    assert set(d["strings"]) == {"mine", "scopus", "ebsco"} and "TITLE-ABS" in d["strings"]["mine"]
    assert [c["key"] for c in d["platforms"]] == ["mine", "scopus", "ebsco"]
    assert client.delete("/api/platforms/mine").status_code == 200
    assert client.delete("/api/platforms/mine").status_code == 404
    assert [c["key"] for c in client.get("/api/platforms").json()["chosen"]] == ["scopus", "ebsco"]


def test_protocol_routes(client):
    d = client.get("/api/protocol").json()
    assert d["changed"] == 0 and d["hash"] and any(s["key"] == "identification" for s in d["sections"])
    row = next(r for r in d["rows"] if r["path"] == "identification.development_share")
    assert row["value"] == 0.57 and row["default"] == 0.57 and row["changed"] is False and row["description"]
    r = client.post("/api/protocol/set", json={"path": "identification.development_share", "value": 0.5})
    assert r.status_code == 200 and r.json()["changed"] == 1 and r.json()["hash"] != d["hash"]
    row = next(x for x in r.json()["rows"] if x["path"] == "identification.development_share")
    assert row["value"] == 0.5 and row["changed"] is True
    assert next(s for s in r.json()["sections"] if s["key"] == "identification")["changed"] == 1
    assert client.post("/api/protocol/set", json={"path": "identification.development_share", "value": 7}).status_code == 400
    assert client.post("/api/protocol/set", json={"path": "nope.nope", "value": 1}).status_code == 400
    assert client.get("/api/project").json()["config"]["protocol"]["identification"]["development_share"] == 0.5
    r = client.post("/api/protocol/reset", json={"path": "identification.development_share"})
    assert r.json()["changed"] == 0 and r.json()["hash"] == d["hash"]
    client.post("/api/protocol/set", json={"path": "corpus.thin_words", "value": 10})
    assert client.post("/api/protocol/reset", json={}).json()["changed"] == 0


def test_databases_step_names_dev_platform_not_listed_first(tmp_path):
    """Scenario: the platforms are wos then scopus and scopus was made the development platform."""
    from tests.unit.test_query_roles import setup_page

    client = TestClient(create_app(Project.init(tmp_path / "r", platforms=["wos", "scopus"])))
    r = client.post("/api/platforms/role", json={"key": "scopus", "role": "development"})
    assert [(c["key"], c["role"]) for c in r.json()["chosen"]] == [("wos", "validation"), ("scopus", "development")]
    assert [(x["key"], x["role"]) for x in client.get("/api/project").json()["config"]["platforms"]] == [
        ("wos", "validation"), ("scopus", "development")]
    steps = setup_page(client)["steps"]
    # THEN the databases step reads "2 platforms, development on scopus"
    assert next(s for s in steps if s["key"] == "databases") == {
        "key": "databases", "done": True, "text": "2 platforms, development on scopus", "state": "done"}


def test_databases_step_no_platform_chosen(tmp_path):
    """Scenario: no platform is chosen (the last one removed)."""
    from tests.unit.test_query_roles import setup_page

    client = TestClient(create_app(Project.init(tmp_path / "r", platforms=["scopus"])))
    assert client.delete("/api/platforms/scopus").status_code == 200
    assert client.get("/api/platforms").json()["chosen"] == []
    assert client.get("/api/project").json()["config"]["platforms"] == []
    page = setup_page(client)
    # THEN the databases step reads "no databases chosen" and is not done
    assert next(s for s in page["steps"] if s["key"] == "databases") == {
        "key": "databases", "done": False, "text": "no databases chosen", "state": "todo"}
    assert page["steps"][-1] == {"key": "summary", "done": False, "text": "complete the tasks above"}
    assert '<tr><th scope="row" class="kv">databases</th><td>—</td></tr>' in page["summary"]


def test_cli_agent_form_names_only_supported_placeholders():
    """Scenario: Example command in the form (model.js renders the backend form for the model page and Settings)."""
    import json
    import re
    from pathlib import Path

    import wider.ui
    from wider.core.backends import CLI_AGENT_PLACEHOLDERS, _check_placeholders, _parse_command

    js = (Path(wider.ui.__file__).parent / "static" / "model.js").read_text(encoding="utf-8")
    m = re.search(r'<label data-for="cli_agent">([^<]*)\s*<input name="command" placeholder=\'([^\']*)\'></label>', js)
    assert m, "the cli_agent command field is missing from model.js"
    label, placeholder = m.groups()
    assert js.count('name="command"') == 1  # one form, used by the model page and Settings
    # THEN the label names {model}, {system_file}, {system_toml}, {schema_file}, {system} and {user} ...
    assert CLI_AGENT_PLACEHOLDERS == ("{model}", "{system_file}", "{system_toml}", "{schema_file}", "{system}", "{user}")
    assert "{model}, {system_file}, {system_toml}, {schema_file}, {system} and {user} are replaced" in label
    assert "{prompt}" not in label
    assert "the user message goes to standard input" in label
    # ... the placeholder example uses only those (and is a command the backend accepts) ...
    tokens = re.findall(r"\{[A-Za-z_]+\}", placeholder)
    assert tokens == ["{model}", "{system_file}"] and set(tokens) <= set(CLI_AGENT_PLACEHOLDERS)
    argv = json.loads(placeholder)
    assert _parse_command(placeholder) == argv
    _check_placeholders(argv)
    # ... and neither contains {prompt}
    assert "{prompt}" not in label and "{prompt}" not in placeholder and "{prompt}" not in js


def _cloud_a(tmp_path, monkeypatch):
    from wider.core.models import ProviderConfig

    monkeypatch.setenv("A_KEY", "synthetic-not-a-key")
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.providers.append(ProviderConfig(name="a", kind="openai_compat", model="m",
                                             base_url="https://llm.example.invalid/v1", api_key_env="A_KEY"))
    p.save()
    return p


def test_models_route_consent_403(tmp_path, monkeypatch):
    import httpx

    sent = []
    real = httpx.AsyncClient

    def answer(request):
        sent.append(request)
        return httpx.Response(200, json={"data": [{"id": "m1"}]})
    monkeypatch.setattr("wider.providers.openai_compat.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    p = _cloud_a(tmp_path, monkeypatch)
    client = TestClient(create_app(p))
    r = client.get("/api/providers/a/models")
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert "nothing was sent" in d["message"]
    c = d["backend_consent_required"]
    assert (c["backend"], c["kind"], c["operation"], c["destination"]) == ("a", "llm", "models", "llm.example.invalid")
    assert "no project data" in c["sent"] and sent == []
    # an unsaved cloud preset cannot hold consent: refused, nothing sent
    r = client.post("/api/providers/models", json={"preset": "openai"})
    assert r.status_code in (400, 502) and sent == []
    assert client.post("/api/providers/a/consent", json={"operations": ["models"]}).status_code == 200
    r = client.get("/api/providers/a/models")
    assert r.status_code == 200 and "m1" in r.text and len(sent) == 1


def test_consent_route_matches_cli(tmp_path, monkeypatch):
    import json

    from typer.testing import CliRunner

    from wider.cli import app as cli_app

    p = _cloud_a(tmp_path, monkeypatch)
    client = TestClient(create_app(p))
    run = CliRunner().invoke

    def same():
        cli_rows = json.loads(run(cli_app, ["providers", "consent", "--json", "--dir", str(p.root)]).output)
        assert client.get("/api/providers/consent").json() == cli_rows
        return {r["name"]: r for r in cli_rows}

    assert same()["a"]["operations"] == [] and same()["a"]["needs_consent"] is True
    assert client.post("/api/providers/a/consent", json={"operations": ["models", "probe"]}).status_code == 200
    assert same()["a"]["operations"] == ["models", "probe"]
    assert run(cli_app, ["providers", "revoke", "a", "--for", "probe", "--dir", str(p.root)]).exit_code == 0
    assert same()["a"]["operations"] == ["models"]
    assert client.delete("/api/providers/a/consent").status_code == 200
    assert same()["a"]["operations"] == []
    assert client.post("/api/providers/a/consent", json={"operations": ["nope"]}).status_code == 400
    assert client.post("/api/providers/zz/consent", json={"operations": ["models"]}).status_code == 404
    # the same for an embedding model (`wider embed consent|allow|revoke`)
    from wider.core.models import EmbeddingConfig

    q = Project.find(p.root)
    q.config.embeddings.append(EmbeddingConfig(name="e", kind="openai_compat", model="em", dim=8,
                                               base_url="https://emb.example.invalid/v1", api_key_env="A_KEY"))
    q.save()
    q.close()

    def same_e():
        cli_rows = json.loads(run(cli_app, ["embed", "consent", "--json", "--dir", str(p.root)]).output)
        assert client.get("/api/embeddings/consent").json() == cli_rows
        return {r["name"]: r for r in cli_rows}

    assert same_e()["e"]["operations"] == [] and same_e()["e"]["needs_consent"] is True
    assert run(cli_app, ["embed", "allow", "e", "--for", "probe", "--dir", str(p.root)]).exit_code == 0
    assert same_e()["e"]["operations"] == ["probe"]
    assert client.delete("/api/embeddings/e/consent").status_code == 200
    assert same_e()["e"]["operations"] == []


def _cli(*args):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    return CliRunner().invoke(cli_app, [str(a) for a in args])


def test_detect_route_matches_cli(client, tmp_path, monkeypatch):
    """Scenarios: Cards on the models step (found state matches `wider providers detect`); Detection names keys only."""
    import json

    from tests.unit.test_detect import FAKE_KEY, exe, server
    from wider.core import detect as D

    gemini = exe(tmp_path / "bin" / "gemini")
    home = tmp_path / "home"
    cred = home / ".gemini" / "oauth_creds.json"
    cred.parent.mkdir(parents=True)
    cred.write_text(json.dumps({"token": FAKE_KEY}))
    real = D.detect
    monkeypatch.setattr(D, "detect", lambda: real(env={"OPENAI_API_KEY": FAKE_KEY}, which={"gemini": gemini}.get,
                                                  run_version=lambda argv: "1.0", http_get=server({}),
                                                  exists=lambda p: p == str(cred), home=str(home),
                                                  bundle_root=str(tmp_path / "root")))
    r = _cli("providers", "detect", "--json")
    assert r.exit_code == 0, r.output
    route = client.get("/api/backends/detect")
    assert route.json()["rows"] == json.loads(r.output)
    by = {x["preset"]: x for x in route.json()["rows"]}
    assert by["gemini-cli"]["found"] and by["gemini-cli"]["signed_in"] == "yes" and by["openai"]["key_set"]
    assert FAKE_KEY not in r.output and FAKE_KEY not in route.text


def test_provider_from_preset_refusals(client):
    """Scenario: a preset fills the form; refusals answer 400 and change nothing."""
    before = client.get("/api/providers").json()["providers"]
    r = client.post("/api/providers", json={"preset": "lm-studio", "model": "local-m"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["name"], d["preset"], d["base_url"]) == ("lm-studio", "lm-studio", "http://127.0.0.1:1234/v1")
    assert client.post("/api/providers", json={"preset": "lm-studio", "model": "m2"}).json()["name"] == "lm-studio-2"
    r = client.post("/api/providers", json={"preset": "nope"})
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    r = client.post("/api/providers", json={"preset": "lm-studio", "kind": "anthropic", "name": "x"})
    assert r.status_code == 400 and "not anthropic" in r.json()["detail"]
    r = client.post("/api/providers", json={"name": "x", "kind": "openai_compat", "model": "m",
                                            "extra": {"auth": "sometimes"}})
    assert r.status_code == 400 and "auth" in r.json()["detail"]
    names = [x["name"] for x in client.get("/api/providers").json()["providers"]]
    assert names == [x["name"] for x in before] + ["lm-studio", "lm-studio-2"]


def test_post_existing_provider_needs_replace(client):
    """Scenario: saving under an existing name asks first, naming the fields a replace resets."""
    client.post("/api/providers", json={"name": "loc", "model": "m", "base_url": "http://127.0.0.1:9/v1",
                                        "timeout_s": 77, "price_input_per_mtok": 1.5})
    r = client.post("/api/providers", json={"name": "loc", "model": "m2", "base_url": "http://127.0.0.1:9/v1"})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["error"] == "provider_exists" and {"timeout_s", "price_input_per_mtok"} <= set(d["reset_fields"])
    row = next(x for x in client.get("/api/providers").json()["providers"] if x["name"] == "loc")
    assert row["model"] == "m"   # nothing saved
    r = client.post("/api/providers", json={"name": "loc", "model": "m2", "base_url": "http://127.0.0.1:9/v1",
                                            "replace": True})
    assert r.status_code == 200 and r.json()["timeout_s"] != 77 and "timeout_s" in r.json()["reset_fields"]
    # embeddings behave the same
    assert client.post("/api/embeddings", json={"name": "minilm", "kind": "sentence_transformers",
                                                "model": "x"}).status_code == 409


def test_patch_provider_keeps_fields(client):
    """Scenario: Edit keeps the fields it does not change, as `wider providers edit` does."""
    client.post("/api/providers", json={"name": "loc", "model": "m", "base_url": "http://127.0.0.1:9/v1",
                                        "timeout_s": 77, "extra": {"max_output_tokens": 500, "think": False}})
    r = client.patch("/api/providers/loc", json={"model": "m2", "extra": {"think": ""}, "price_cached_input_per_mtok": 0.1})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["model"], d["timeout_s"], d["base_url"]) == ("m2", 77, "http://127.0.0.1:9/v1")
    assert d["extra"] == {"max_output_tokens": 500} and d["price_cached_input_per_mtok"] == 0.1
    assert client.patch("/api/providers/zz", json={"model": "x"}).status_code == 404
    assert client.patch("/api/providers/loc", json={"timeout_s": -1}).status_code == 400
    assert client.patch("/api/providers/loc", json={"kind": "ollama"}).status_code in (200, 422)  # ignored field
    assert next(x for x in client.get("/api/providers").json()["providers"] if x["name"] == "loc")["kind"] == "openai_compat"


def test_patch_embedding_keeps_fields(client):
    r = client.patch("/api/embeddings/minilm", json={"batch_size": 8})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["batch_size"] == 8 and d["model"] and d["kind"] == "sentence_transformers"
    assert client.patch("/api/embeddings/zz", json={"dim": 3}).status_code == 404
    assert client.patch("/api/embeddings/minilm", json={"dim": -3}).status_code == 400


def test_embed_edit_cli_matches_route(tmp_path):
    """`wider embed edit` and `PATCH /api/embeddings/{name}` change the same fields and keep the rest."""
    a = Project.init(tmp_path / "a", platforms=["scopus"])
    b = Project.init(tmp_path / "b", platforms=["scopus"])
    r = _cli("embed", "edit", "minilm", "--batch-size", "8", "--price-in", "0.5", "--dir", a.root)
    assert r.exit_code == 0, r.output
    assert TestClient(create_app(b)).patch("/api/embeddings/minilm",
                                           json={"batch_size": 8, "price_input_per_mtok": 0.5}).status_code == 200
    ea, eb = Project.find(a.root).embedding("minilm"), Project.find(b.root).embedding("minilm")
    assert ea.model_dump() == eb.model_dump() and ea.batch_size == 8 and ea.model
    assert _cli("embed", "edit", "zz", "--dim", "3", "--dir", a.root).exit_code == 1


def test_models_route_unreachable(client, monkeypatch):
    """Scenario: Unreachable server: the route answers 502 with "cannot reach" and the base URL."""
    import httpx

    from tests.unit.test_detect import mock_models

    def refuse(req):
        raise httpx.ConnectError("refused")

    mock_models(monkeypatch, "openai_compat", refuse)
    client.post("/api/providers", json={"name": "loc", "model": "m", "base_url": "http://127.0.0.1:9/v1"})
    r = client.get("/api/providers/loc/models")
    assert r.status_code == 502 and "cannot reach http://127.0.0.1:9/v1" in r.json()["detail"]
    r = client.post("/api/providers/models", json={"preset": "lm-studio"})
    assert r.status_code == 502 and "cannot reach http://127.0.0.1:1234/v1" in r.json()["detail"]


def test_probe_route_unreachable(client, monkeypatch):
    """Scenario: Unreachable backend: the UI probe answers with the message, not with a server error."""
    import asyncio

    import httpx

    real_sleep = asyncio.sleep
    monkeypatch.setattr("wider.providers.base.asyncio.sleep", lambda s: real_sleep(0))
    real = httpx.AsyncClient

    def refuse(req):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("wider.providers.openai_compat.httpx.AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(refuse), **kw))
    client.post("/api/providers", json={"name": "loc", "model": "m", "base_url": "http://127.0.0.1:9/v1"})
    r = client.post("/api/providers/loc/probe")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is False and d["error"].startswith("cannot reach") and d["attempts"] == 2
    # anything the probe does not turn into a failed probe is a 502 with its message, never a bare 500
    monkeypatch.setattr("wider.providers.registry.probe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.post("/api/providers/loc/probe")
    assert r.status_code == 502 and "boom" in r.json()["detail"]


def test_estimate_route_matches_cli(project):  # noqa: F811
    """Scenario: Range in the run form: `POST /api/estimate` gives the lines `--estimate-only` prints."""
    p = project
    fake = p.provider("fake")
    fake.price_input_per_mtok, fake.price_output_per_mtok = 2.0, 8.0
    p.save()
    client = TestClient(create_app(p))
    for body, args in [({"stage": "screen", "scope": "sample:development", "limit": 3},
                        ["screen", "run", "sample:development", "--limit", "3"]),
                       ({"stage": "embed"}, ["embed", "corpus"])]:
        r = client.post("/api/estimate", json=body)
        assert r.status_code == 200, (body, r.text)
        out = _cli(*args, "--estimate-only", "--dir", p.root)
        assert out.exit_code == 0, out.output
        assert " ".join(out.output.split()) == " ".join(" ".join(r.json()["lines"]).split())
    # a refusal is the same message in both: no Development papers for a construct proposal
    r = client.post("/api/estimate", json={"stage": "construct", "kind": "cells"})
    out = _cli("construct", "propose", "cells", "--estimate-only", "--dir", p.root)
    assert r.status_code == 400 and out.exit_code == 1 and r.json()["detail"] in " ".join(out.output.split())
    d = client.post("/api/estimate", json={"stage": "screen", "scope": "sample:development", "limit": 3}).json()
    assert d["priced"] and d["calls"] == 3 and d["max_usd"] >= d["min_usd"] > 0
    assert client.post("/api/estimate", json={"stage": "nope"}).status_code == 400
    assert client.post("/api/estimate", json={"stage": "screen", "scope": "sample:development", "provider": "zz"}).status_code == 400


def test_page_estimate_makes_no_request(project, monkeypatch):  # noqa: F811
    """Scenario: Opening a page contacts nothing. The Concepts and Screening pages POST /api/estimate when they
    open; with a loopback Ollama the estimate takes the backend row's locality instead of reading its tag list."""
    from tests.unit.helpers import no_requests
    from wider.core.backends import record_probe
    from wider.core.models import ProviderConfig

    p = project
    p.config.providers.append(ProviderConfig(name="o", kind="ollama", model="m", base_url="http://127.0.0.1:11434"))
    p.save()
    seen = no_requests(monkeypatch)
    client = TestClient(create_app(Project.load(p.root)))
    body = {"stage": "screen", "scope": "sample:development", "limit": 3, "provider": "o"}
    d = client.post("/api/estimate", json=body).json()
    assert d["destination"] == "unknown (via Ollama)" and d["locality"] == "unknown", d
    assert client.get("/api/providers").json()["providers"][-1]["locality_label"] == "unknown, press Check"
    # after a check found the model on this computer, the estimate says so, still with no request
    pp = Project.load(p.root)
    record_probe(pp, "llm", pp.provider("o"), True, "local")
    pp.close()
    d = client.post("/api/estimate", json=body).json()
    assert d["destination"] == "this computer" and d["locality"] == "local", d
    assert seen == []


def test_experimental_route_needs_ack(client):
    """Scenario: Adding needs an acknowledgement (HTTP 400 naming the limitations; project.yaml unchanged)."""
    before = client.get("/api/providers").json()["providers"]
    r = client.post("/api/providers", json={"preset": "antigravity", "name": "ag", "model": "m"})
    assert r.status_code == 400 and "acknowledgement" in r.json()["detail"]
    assert client.get("/api/providers").json()["providers"] == before
    r = client.post("/api/providers", json={"preset": "antigravity", "name": "ag", "model": "m",
                                            "accept_experimental": True})
    assert r.status_code == 200, r.text
    assert any(x["name"] == "ag" for x in client.get("/api/providers").json()["providers"])


def _two_backends(root):
    """A synthetic project with two loopback backends (nothing is contacted); `a` is the default."""
    from wider.core.backends import add_provider

    p = Project.init(root, platforms=["scopus"])
    add_provider(p, name="a", kind="ollama", model="m-a", base_url="http://127.0.0.1:11434", set_default=True)
    add_provider(p, name="b", kind="ollama", model="m-b", base_url="http://127.0.0.1:11434")
    return p


def test_model_choice_matches_cli(tmp_path):
    """Scenario: choosing a card on the model page = `wider providers stage construct NAME` + `wider providers
    set-default NAME`."""
    ui, cli = _two_backends(tmp_path / "ui"), _two_backends(tmp_path / "cli")
    c = TestClient(create_app(ui))
    assert c.post("/api/providers/stage", json={"stage": "construct", "name": "b"}).status_code == 200
    assert c.post("/api/providers/default", json={"name": "b"}).status_code == 200
    for args in (("providers", "stage", "construct", "b"), ("providers", "set-default", "b")):
        r = _cli(*args, "--dir", cli.root)
        assert r.exit_code == 0, r.output
    a, b = Project.load(ui.root).config, Project.load(cli.root).config
    assert a.stage_providers == b.stage_providers == {"construct": "b"}
    assert a.default_provider == b.default_provider == "b"
    rows = {x["name"]: x for x in c.get("/api/providers").json()["providers"]}
    assert rows["b"]["default"] is True and rows["b"]["stages"] == ["construct"]


def test_model_choice_undo_and_remove(tmp_path):
    """The choice is two History entries, each undone in turn back to the old project.yaml; removing the chosen
    backend unsets the construct backend, so the page shows the guided steps again."""
    from wider.core import journal

    p = _two_backends(tmp_path / "r")
    before = (p.root / "project.yaml").read_text(encoding="utf-8")
    n0 = len(journal.changes(p))
    c = TestClient(create_app(p))
    c.post("/api/providers/stage", json={"stage": "construct", "name": "b"}, headers={"X-Wider-Author": "ana"})
    c.post("/api/providers/default", json={"name": "b"}, headers={"X-Wider-Author": "ana"})
    ch = journal.changes(p)
    assert len(ch) == n0 + 2 and all(x["kind"] == "config" and x["author"] == "ana" for x in ch[:2])
    assert "construct" in ch[1]["summary"] and "default" in ch[0]["summary"]
    for x in ch[:2]:   # newest first
        assert c.post(f"/api/history/{x['change_id']}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200
    assert (p.root / "project.yaml").read_text(encoding="utf-8") == before
    assert c.get("/api/providers").json()["stage_providers"] == {}
    # choose again, then remove the chosen backend: no construct backend is left
    c.post("/api/providers/stage", json={"stage": "construct", "name": "b"})
    c.post("/api/providers/default", json={"name": "b"})
    assert c.delete("/api/providers/b").status_code == 200
    d = c.get("/api/providers").json()
    first = d["providers"][0]["name"]   # "the default and stage overrides fall back to the first remaining one"
    assert "construct" not in d["stage_providers"] and d["default"] == first != "b"


def test_setup_steps_without_models_or_protocol(client, monkeypatch):
    """Scenarios: completion rules per task, no development seed paper, and the summary done without models: the Review
    plan has five task cards (no backends, embedding or protocol task; the last one optional) and it is ready although
    the default embedding model is not downloaded."""
    from tests.unit.test_query_roles import setup_page

    monkeypatch.setattr("wider.core.backends.is_downloaded", lambda cfg: False)
    assert client.patch("/api/project", json={"questions": ["Q?"], "platforms": ["scopus"]}).status_code == 200
    client.post("/api/benchmark/add", json={"text": "A Title Only\nAnother Title\n", "set": "Validation"})
    steps = {s["key"]: s for s in setup_page(client)["steps"]}
    assert list(steps) == ["question", "seeds", "prior", "databases", "more", "summary"]
    assert steps["question"]["done"] and steps["databases"]["done"] and not steps["prior"]["done"]
    assert not steps["seeds"]["done"] and "assign some to the development set" in steps["seeds"]["text"]
    assert not steps["summary"]["done"]
    assert client.post("/api/benchmark/assign", json={"benchmark_id": "B001", "set": "Development"}).status_code == 200
    assert client.patch("/api/project", json={"prior_reviews": {"none_known": True}}).status_code == 200
    steps = {s["key"]: s for s in setup_page(client)["steps"]}
    assert all(s["done"] for k, s in steps.items() if k != "more") and steps["summary"]["text"] == "ready for the construct stage"
    assert steps["more"]["state"] == "optional"
    emb = client.get("/api/embeddings").json()
    assert next(e for e in emb["embeddings"] if e["name"] == emb["default"])["downloaded"] is False
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    setup = html[html.index('id="page-setup"'):html.index('id="page-setup"') + html[html.index('id="page-setup"'):].index("</section>")]
    assert 'data-step="backends"' not in html and 'data-step="protocol"' not in html and 'id="setup-protocol"' not in html
    assert [s for s in ("question", "seeds", "prior", "databases", "more") if f'data-task="{s}"' not in setup] == []


def _protocol_card(client, body: str) -> dict:
    """setup.js's Advanced protocol card (`loadProtocol`) on the stand-in page, drawn from the project's protocol."""
    from tests.unit.test_backends_js_node import ESC, PAGE, run_node

    replies = {"/api/protocol": client.get("/api/protocol").json(), "/api/protocol/set": {}, "/api/protocol/reset": {}}
    setup = (STATIC / "setup.js").read_text(encoding="utf-8")
    row = ("const row = (p) => { const h = $('#protocol-editor').innerHTML, i = h.indexOf(`data-proto-set=\"${p}\"`);"
           " return h.slice(h.lastIndexOf('<tr', i), h.indexOf('</tr>', i)); };"
           "const head = () => { const h = $('#protocol-editor').innerHTML; return h.slice(h.indexOf('<summary>'), h.indexOf('</summary>')); };")
    return run_node(f"{ESC}\n{PAGE}\nObject.assign(replies, {json.dumps(replies)});\n{setup}\n{row}\n"
                    f"await window.loadProtocol(); await flush();\n{body}")


def _changed_settings(root) -> list[str]:
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    r = CliRunner().invoke(cli_app, ["protocol", "show", "--changed", "--dir", str(root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    return [p for p in ("identification.development_share",) if p in r.output]


def test_protocol_card_on_settings(client):
    """Scenario: the protocol card on Settings. Set 0.5 on the card, then Reset there: the card marks the setting
    changed and then unchanged, as `wider protocol show` does after each step."""
    path = "identification.development_share"
    root = client.app.state.project.root
    out = lambda e: f"console.log(JSON.stringify({e}));"   # noqa: E731
    first = _protocol_card(client, f"""
const before = row('{path}'), headBefore = head();
const f = $('#proto-identification-development_share'); f.value = '0.5'; f.dataset = {{ type: 'number' }};
await click('#protocol-editor', '[data-proto-set]', (b) => b.dataset.protoSet === '{path}');
""" + out("{before, headBefore, posts: calls.filter(c => c[1] === 'POST')}"))
    assert 'class=""' in first["before"] and "Reset" not in first["before"] and "all defaults" in first["headBefore"]
    assert first["posts"] == [["/api/protocol/set", "POST", {"path": path, "value": 0.5}]]
    assert client.post("/api/protocol/set", json=first["posts"][0][2]).status_code == 200
    assert _changed_settings(root) == [path]
    second = _protocol_card(client, f"""
const changed = row('{path}'), headChanged = head();
await click('#protocol-editor', '[data-proto-reset]', (b) => b.dataset.protoReset === '{path}');
""" + out("{changed, headChanged, posts: calls.filter(c => c[1] === 'POST')}"))
    assert 'class="changed"' in second["changed"] and ">changed</span>" in second["changed"]
    assert "1 setting changed from the defaults" in second["headChanged"]
    assert second["posts"] == [["/api/protocol/reset", "POST", {"path": path}]]
    assert client.post("/api/protocol/reset", json=second["posts"][0][2]).status_code == 200
    assert _changed_settings(root) == []
    third = _protocol_card(client, out(f"{{after: row('{path}'), head: head()}}"))
    assert 'class=""' in third["after"] and "all defaults" in third["head"]


SETTINGS_ACTIONS = (("PATCH", "/api/providers/a", {"price_input_per_mtok": 0.5}),
                    ("POST", "/api/providers/stage", {"stage": "screen", "name": "a"}),
                    ("DELETE", "/api/providers/b", None))
SETTINGS_CLI = (("providers", "edit", "a", "--price-in", "0.5"), ("providers", "stage", "screen", "a"), ("providers", "remove", "b"))


def backends_after_cli(root) -> dict:
    """The CLI twin of the Settings backend actions, on a fresh `_two_backends` project at ``root``."""
    p = _two_backends(root)
    for args in SETTINGS_CLI:
        r = _cli(*args, "--dir", p.root)
        assert r.exit_code == 0, r.output
    return backend_config(p.root)


def backend_config(root) -> dict:
    cfg = Project.load(root).config
    return {"providers": [(x.name, x.model, x.price_input_per_mtok) for x in cfg.providers],
            "stage_providers": cfg.stage_providers, "default": cfg.default_provider}


def test_settings_backend_actions_match_cli(tmp_path):
    """Scenario: managing a backend on Settings. Edit `a` with an input price of 0.5, `screen` set to `a` and `b`
    removed give the same project.yaml backends as `wider providers edit a --price-in 0.5`, `wider providers stage
    screen a` and `wider providers remove b`."""
    ui = _two_backends(tmp_path / "ui")
    c = TestClient(create_app(ui))
    for method, url, body in SETTINGS_ACTIONS:
        r = c.request(method, url, json=body) if body is not None else c.request(method, url)
        assert r.status_code == 200, r.text
    got = backend_config(ui.root)
    assert got == backends_after_cli(tmp_path / "cli")
    assert ("a", "m-a", 0.5) in got["providers"] and "b" not in [x[0] for x in got["providers"]]
    assert got["stage_providers"] == {"screen": "a"} and got["default"] == "a"
