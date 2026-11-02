"""CLI setup commands (wider/cli_setup.py): providers/embed add|remove|set-default, benchmark add|split|set, query platforms."""
import pytest
from typer.testing import CliRunner

from tests.unit.test_screen import project  # noqa: F401  (the screened synthetic project fixture)
from wider.cli import app
from wider.core.project import Project

runner = CliRunner()


def _run(*args):
    import rich

    con = rich.get_console()   # a wide console: the platforms table must not be cut at the terminal's width
    old, con.width = con._width, 1000
    try:
        r = runner.invoke(app, list(args), env={"COLUMNS": "1000"})
    finally:
        con._width = old
    assert r.exit_code == 0, r.output
    return r.output


def test_providers_and_embed_commands(tmp_path):
    root = tmp_path / "r"
    Project.init(root)
    d = str(root)
    out = _run("providers", "add", "--name", "mine", "--kind", "openai_compat", "--model", "m", "--base-url", "http://127.0.0.1:1/v1",
               "--api-key-env", "MY_KEY", "--set-default", "--dir", d)
    assert "provider mine" in out and "default" in out
    assert "cli_agent provider needs a command" in runner.invoke(app, ["providers", "add", "--name", "a", "--kind", "cli_agent", "--dir", d]).output
    bad = runner.invoke(app, ["providers", "add", "--name", "a", "--kind", "cli_agent", "--command", '["tool", "{prompt}"]', "--dir", d])
    assert bad.exit_code == 1 and "{prompt}" in bad.output and "{system_file}" in bad.output
    _run("providers", "add", "--name", "a", "--kind", "cli_agent", "--command", '["tool", "--model", "{model}"]', "--dir", d)
    _run("providers", "stage", "screen", "a", "--dir", d)
    p = Project.load(root)
    assert p.config.default_provider == "mine" and p.config.stage_providers == {"screen": "a"}
    assert p.provider("a").command == ["tool", "--model", "{model}"]
    assert "MY_KEY MISSING" in _run("providers", "list", "--dir", d) or "MY_KEY" in _run("providers", "list", "--dir", d)
    _run("providers", "set-default", "a", "--dir", d)
    _run("providers", "remove", "a", "--dir", d)
    p = Project.load(root)
    assert p.config.default_provider != "a" and p.config.stage_providers == {}
    assert runner.invoke(app, ["providers", "remove", "a", "--dir", d]).exit_code == 1

    _run("embed", "add", "--name", "e1", "--kind", "openai_compat", "--model", "emb", "--dim", "64", "--base-url", "http://127.0.0.1:1/v1",
         "--api-key-env", "E_KEY", "--set-default", "--dir", d)
    p = Project.load(root)
    assert p.config.default_embedding == "e1" and p.embedding("e1").dim == 64
    _run("embed", "set-default", "bge-m3", "--dir", d)
    _run("embed", "remove", "e1", "--dir", d)
    assert all(e.name != "e1" for e in Project.load(root).config.embeddings)
    assert runner.invoke(app, ["embed", "download", "nope", "--dir", d]).exit_code == 1


def test_benchmark_commands(tmp_path):
    root = tmp_path / "r"
    Project.init(root)
    d = str(root)
    f = tmp_path / "seeds.csv"
    f.write_text("title,doi\nFile Paper,10.1000/f.1\n", encoding="utf-8")
    out = _run("benchmark", "add", "--doi", "10.1000/a.1", "--doi", "10.1000/a.2", "--title", "Some Title", "--file", str(f),
               "--set", "Validation", "--dir", d)
    assert "B001" in out and "B004" in out
    papers = Project.load(root).read_benchmark()
    assert [b.benchmark_id for b in papers] == ["B001", "B002", "B003", "B004"] and all(b.set == "Validation" for b in papers)
    assert runner.invoke(app, ["benchmark", "add", "--dir", d]).exit_code == 1
    _run("benchmark", "set", "B002", "Development", "--dir", d)
    assert {b.benchmark_id: b.set for b in Project.load(root).read_benchmark()}["B002"] == "Development"
    out = _run("benchmark", "split", "--dev-share", "0.5", "--dir", d)
    assert "2 development / 2 validation" in out and "applied" not in out
    sets = {b.set for b in Project.load(root).read_benchmark()}
    assert sets == {"Development", "Validation"}  # not applied: B002 dev, rest validation
    _run("benchmark", "split", "--dev-share", "0.5", "--apply", "--dir", d)
    sets = [b.set for b in Project.load(root).read_benchmark()]
    assert sets.count("Development") == 2
    _run("benchmark", "remove", "B004", "--dir", d)
    assert len(Project.load(root).read_benchmark()) == 3
    assert runner.invoke(app, ["benchmark", "split", "--dev-share", "3", "--dir", d]).exit_code == 1


def test_query_platforms_commands(tmp_path):
    root = tmp_path / "r"
    Project.init(root, platforms=["ebsco", "scopus"])
    d = str(root)
    out = _run("query", "platforms", "--dir", d)
    assert "ebsco" in out and "pubmed" in out and "development" in out
    _run("query", "platforms", "add", "wos", "--dir", d)
    assert runner.invoke(app, ["query", "platforms", "add", "mine", "--dir", d]).exit_code == 1
    _run("query", "platforms", "add", "mine", "--label", "My host", "--syntax", "ebsco", "--dir", d)
    assert "wos > mine" in _run("query", "platforms", "order", "wos", "mine", "--dir", d)
    _run("query", "platforms", "role", "scopus", "development", "--dir", d)
    p = Project.load(root)
    assert [(x.key, x.role) for x in p.config.platforms] == [("wos", "validation"), ("mine", "validation"), ("ebsco", "validation"), ("scopus", "development")]
    assert p.config.platforms[1].syntax == "ebsco" and p.config.platforms[1].label == "My host"
    assert "(custom)" in _run("query", "platforms", "--dir", d)
    _run("query", "platforms", "remove", "wos", "--dir", d)
    assert [x.key for x in Project.load(root).config.platforms] == ["mine", "ebsco", "scopus"]
    assert runner.invoke(app, ["query", "platforms", "remove", "wos", "--dir", d]).exit_code == 1


def test_providers_add_help_command_example_has_only_supported_placeholders(tmp_path):
    """Scenario: Example command in the help (`wider providers add --help`)."""
    import json
    import re

    from wider.core.backends import CLI_AGENT_PLACEHOLDERS, _check_placeholders

    r = runner.invoke(app, ["providers", "add", "--help"], env={"COLUMNS": "400"})
    assert r.exit_code == 0, r.output
    text = " ".join(r.output.replace("│", " ").split())
    m = re.search(r"--command <str> cli_agent only: argv template as a JSON list, e\.g\. '(\[.*?\])'\. "
                  r"Placeholders: ([^;]*); the user message goes to standard input\.", text)
    assert m, text
    example, listed = m.groups()
    # THEN the --command help shows an example without {prompt}
    assert "{prompt}" not in text
    argv = json.loads(example)
    assert argv[:2] == ["claude", "-p"] and "{model}" in argv and "{system_file}" in argv
    _check_placeholders(argv)  # every placeholder in the example is one the adapter replaces
    assert listed == ", ".join(CLI_AGENT_PLACEHOLDERS) == "{model}, {system_file}, {system_toml}, {schema_file}, {system}, {user}"
    # the example as printed is a command `providers add` accepts
    root = tmp_path / "r"
    Project.init(root)
    _run("providers", "add", "--name", "agent", "--kind", "cli_agent", "--command", example, "--dir", str(root))
    assert Project.load(root).provider("agent").command == argv


def test_query_platforms_lists_dev_role_not_listed_first(tmp_path):
    """CLI twin of the databases step: wos then scopus, scopus made the development platform."""
    from tests.unit.test_query_roles import _cli_roles

    root = tmp_path / "r"
    Project.init(root, platforms=["wos", "scopus"])
    assert "scopus: development" in _run("query", "platforms", "role", "scopus", "development", "--dir", str(root))
    assert _cli_roles(root) == {"wos": "validation", "scopus": "development"}
    # no platform chosen: no row carries a role
    _run("query", "platforms", "remove", "wos", "--dir", str(root))
    _run("query", "platforms", "remove", "scopus", "--dir", str(root))
    assert Project.load(root).config.platforms == [] and _cli_roles(root) == {}


@pytest.fixture
def priced(project, monkeypatch):  # noqa: F811
    """The screened synthetic project with its fake backend priced and answering like a screener."""
    from tests.unit.test_screen import _responder
    from wider.core.backends import update_provider
    from wider.core.models import BenchmarkPaper
    from wider.providers import fake as fakemod

    project.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="A synthetic paper on employee voice",
                                            abstract="Employees speak up to supervisors.", set="Development")])
    update_provider(project, "fake", price_input_per_mtok=1000.0, price_output_per_mtok=2000.0)
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, _responder({"calls": 0})))
    return project


def _usage_rows(p):
    return p.store.scalar("SELECT COUNT(*) FROM llm_usage")


def test_estimate_only_makes_no_call(priced):
    p, d = priced, str(priced.root)
    n0 = _usage_rows(p)
    runs0 = p.store.scalar("SELECT COUNT(*) FROM runs")
    for args in (["screen", "run", "sample:development"], ["screen", "protocol", "sample:development"],
                 ["screen", "second"], ["construct", "propose", "codebook"], ["embed", "corpus"]):
        out = _run(*args, "--estimate-only", "--dir", d)
        assert "Estimated cost:" in out and "assumption:" in out, (args, out)
    assert _usage_rows(p) == n0 and p.store.scalar("SELECT COUNT(*) FROM runs") == runs0
    assert "nowhere (test backend)" in _run("screen", "run", "sample:development", "--estimate-only", "--dir", d)


def test_screen_progress_shows_projection(priced):
    p, d = priced, str(priced.root)
    out = _run("screen", "run", "sample:development", "--pass", "P", "--dir", d)
    assert out.index("Estimated cost:") < out.index("cost so far $")
    lines = [ln for ln in out.splitlines() if "cost so far" in ln]
    assert lines and all("· projected $" in ln for ln in lines)
    n = p.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE run_id=(SELECT run_id FROM runs ORDER BY rowid DESC LIMIT 1)")
    assert lines[-1].startswith(f"{n}/{n} ")
    # at the end nothing is left, so the projection equals the cost so far
    last = lines[-1].split("cost so far ")[1]
    so_far, proj = last.split(" · projected ")
    assert proj == f"{so_far}–{so_far}"


# ----------------------------------------------------------------- presets, edit, consent (llm-backend-choice §10)
def _new(tmp_path):
    root = tmp_path / "r"
    Project.init(root)
    return root, str(root)


def _flat(r):
    return " ".join(r.output.replace("│", " ").split())


def test_providers_presets_command():
    import json

    rows = json.loads(_run("providers", "presets", "--json"))
    ids = [r["id"] for r in rows]
    assert {"codex", "claude-code", "openai", "ollama", "lm-studio", "antigravity"} <= set(ids)
    fams = [r["family"] for r in rows]
    assert fams == sorted(fams, key=["cli", "cloud", "local"].index)   # grouped CLI / cloud / local
    table = runner.invoke(app, ["providers", "presets"], env={"COLUMNS": "300"}).output
    assert "codex" in table and "(experimental)" in table


def test_providers_add_from_preset(tmp_path):
    root, d = _new(tmp_path)
    out = _run("providers", "add", "--preset", "codex", "--model", "m1", "--dir", d)
    assert "provider codex" in out
    c = Project.load(root).provider("codex")
    assert c.kind == "cli_agent" and c.preset == "codex" and c.model == "m1"
    assert c.extra["output"] == "codex_jsonl" and c.extra["strict_schema"] is True
    assert "{schema_file}" in c.command and any("developer_instructions={system_toml}" in a for a in c.command)
    # a preset value may be overridden
    _run("providers", "add", "--preset", "lm-studio", "--name", "lms", "--base-url", "http://127.0.0.1:9999/v1",
         "--extra", "think=false", "--timeout", "120", "--dir", d)
    lms = Project.load(root).provider("lms")
    assert lms.base_url == "http://127.0.0.1:9999/v1" and lms.extra["think"] is False and lms.timeout_s == 120


def test_providers_list_destination(tmp_path):
    root, d = _new(tmp_path)
    _run("providers", "add", "--preset", "codex", "--model", "m1", "--dir", d)
    _run("providers", "add", "--name", "mycli", "--kind", "cli_agent", "--command", '["mycli","--model","{model}"]',
         "--model", "x", "--dir", d)
    _run("providers", "add", "--name", "lms", "--kind", "openai_compat", "--base-url", "http://127.0.0.1:1234/v1",
         "--model", "x", "--dir", d)
    r = runner.invoke(app, ["providers", "list", "--dir", d], env={"COLUMNS": "400"})
    lines = [ln for ln in r.output.splitlines() if ln.startswith("│")]
    dest = {ln.split("│")[1].strip(): ln.split("│")[6].strip() for ln in lines}
    assert dest["lms"] == "this computer"
    assert dest["ollama"] == "unknown (via Ollama)"  # tests cannot reach Ollama, so its routing is not established
    assert dest["openai"] == "api.openai.com"
    assert dest["codex"] == "OpenAI (via Codex)"
    assert dest["mycli"] == "unknown (custom CLI)"


def test_preset_refusals(tmp_path):
    root, d = _new(tmp_path)
    before = (root / "project.yaml").read_text()
    r = runner.invoke(app, ["providers", "add", "--preset", "azure-openai", "--model", "m1", "--dir", d])
    assert r.exit_code == 1 and "needs a base URL" in _flat(r)
    r = runner.invoke(app, ["providers", "add", "--preset", "nope", "--dir", d])
    assert r.exit_code == 1 and "unknown preset 'nope'" in _flat(r) and "codex" in _flat(r)
    r = runner.invoke(app, ["providers", "add", "--preset", "openai", "--kind", "ollama", "--model", "m", "--dir", d])
    assert r.exit_code == 1 and "not ollama" in _flat(r)
    assert (root / "project.yaml").read_text() == before


def test_cloud_preset_needs_model(tmp_path):
    root, d = _new(tmp_path)
    before = (root / "project.yaml").read_text()
    r = runner.invoke(app, ["providers", "add", "--preset", "openai", "--name", "o2", "--dir", d])
    assert r.exit_code == 1 and "choose a model" in _flat(r) and "wider providers models --preset openai" in _flat(r)
    assert (root / "project.yaml").read_text() == before


def test_providers_add_replace_warns(tmp_path):
    root, d = _new(tmp_path)
    _run("providers", "add", "--name", "a", "--model", "m1", "--base-url", "http://127.0.0.1:1/v1",
         "--timeout", "900", "--price-in", "1", "--dir", d)
    r = runner.invoke(app, ["providers", "add", "--name", "a", "--model", "m2", "--dir", d], env={"COLUMNS": "400"})
    out = _flat(r)
    assert r.exit_code == 0 and "replaced the existing a" in out
    assert "timeout_s" in out and "price_input_per_mtok" in out and "wider providers edit a" in out
    a = Project.load(root).provider("a")
    assert a.model == "m2" and a.timeout_s == 300 and a.price_input_per_mtok is None


def test_preset_default_name_takes_free_suffix(tmp_path):
    root, d = _new(tmp_path)
    old = Project.load(root).provider("ollama").model_dump()
    r = runner.invoke(app, ["providers", "add", "--preset", "ollama", "--model", "m1", "--dir", d], env={"COLUMNS": "400"})
    out = _flat(r)
    assert r.exit_code == 0 and "added as ollama-2" in out and "wider providers edit ollama" in out
    p = Project.load(root)
    assert p.provider("ollama-2").model == "m1" and p.provider("ollama").model_dump() == old


def test_providers_edit(tmp_path):
    root, d = _new(tmp_path)
    _run("providers", "add", "--name", "a", "--model", "m1", "--base-url", "http://127.0.0.1:1/v1", "--timeout", "900",
         "--concurrency", "2", "--price-in", "1", "--extra", "think=false", "--dir", d)
    out = _run("providers", "edit", "a", "--model", "m2", "--extra", "json_mode=object", "--dir", d)
    assert "updated" in out
    a = Project.load(root).provider("a")
    assert a.model == "m2" and a.extra == {"think": False, "json_mode": "object"}
    assert a.timeout_s == 900 and a.max_concurrency == 2 and a.price_input_per_mtok == 1
    _run("providers", "edit", "a", "--extra", "think=", "--price-cached-in", "0.5", "--dir", d)
    a = Project.load(root).provider("a")
    assert "think" not in a.extra and a.price_cached_input_per_mtok == 0.5
    r = runner.invoke(app, ["providers", "edit", "nope", "--model", "m2", "--dir", d])
    assert r.exit_code == 1 and "no provider" in r.output
    before = (root / "project.yaml").read_text()
    r = runner.invoke(app, ["providers", "edit", "a", "--timeout", "0", "--dir", d])
    assert r.exit_code == 1 and "timeout" in r.output and (root / "project.yaml").read_text() == before


def test_providers_models_local_preset(monkeypatch):
    import httpx

    from tests.unit.test_detect import mock_models

    seen = mock_models(monkeypatch, "openai_compat",
                       lambda req: httpx.Response(200, json={"data": [{"id": "m2"}, {"id": "m1"}]}))
    out = _run("providers", "models", "--preset", "lm-studio")
    assert out.split() == ["m1", "m2"] and seen[0].url.host == "127.0.0.1"


def test_models_cloud_needs_key(monkeypatch):
    import httpx

    from tests.unit.test_detect import mock_models

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    seen = mock_models(monkeypatch, "openai_compat", lambda req: httpx.Response(200, json={"data": []}))
    r = runner.invoke(app, ["providers", "models", "--preset", "openai"])
    assert r.exit_code == 1 and "OPENAI_API_KEY is not set" in _flat(r) and seen == []


def test_embed_add_from_preset(tmp_path):
    root, d = _new(tmp_path)
    _run("embed", "add", "--preset", "gemini-embedding", "--dir", d)
    e = Project.load(root).embedding("gemini-embedding")
    assert e.kind == "openai_compat" and e.model == "gemini-embedding-001" and e.api_key_env == "GEMINI_API_KEY"
    assert e.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    out = _flat(runner.invoke(app, ["embed", "list", "--dir", d], env={"COLUMNS": "400"}))
    assert "gemini-embedding" in out and "remote" in out.lower()
    _run("embed", "add", "--preset", "ollama-embed", "--name", "oe", "--batch-size", "8", "--price-in", "0", "--dir", d)
    assert Project.load(root).embedding("oe").batch_size == 8


def test_experimental_needs_ack(tmp_path):
    root, d = _new(tmp_path)
    before = (root / "project.yaml").read_text()
    r = runner.invoke(app, ["providers", "add", "--preset", "antigravity", "--name", "ag", "--model", "m", "--dir", d],
                      env={"COLUMNS": "400"})
    out = _flat(r)
    assert r.exit_code == 1 and "experimental" in out and "process" in out and "--accept-experimental" in out
    assert (root / "project.yaml").read_text() == before
    _run("providers", "add", "--preset", "antigravity", "--name", "ag", "--model", "m", "--accept-experimental", "--dir", d)
    p = Project.load(root)
    assert p.provider("ag").preset == "antigravity"
    from wider.core import journal

    ch = journal.changes(p, kind="config")[0]
    assert ch["author"] and "ag" in ch["summary"]


def test_providers_consent_commands(tmp_path):
    import json

    root, d = _new(tmp_path)
    _run("providers", "add", "--name", "a", "--model", "m", "--base-url", "https://llm.example.invalid/v1",
         "--api-key-env", "WIDER_TEST_UNSET_KEY", "--dir", d)
    rows = {r["name"]: r for r in json.loads(_run("providers", "consent", "--json", "--dir", d))}
    assert rows["a"]["needs_consent"] is True and rows["a"]["operations"] == []
    assert rows["ollama"]["needs_consent"] is not True and rows["ollama"]["locality_label"] == "unknown, press Check"
    _run("providers", "allow", "a", "--for", "models", "--for", "probe", "--dir", d)
    assert json.loads(_run("providers", "consent", "a", "--json", "--dir", d))[0]["operations"] == ["models", "probe"]
    _run("providers", "revoke", "a", "--for", "probe", "--dir", d)
    assert json.loads(_run("providers", "consent", "a", "--json", "--dir", d))[0]["operations"] == ["models"]
    _run("providers", "revoke", "a", "--dir", d)
    assert json.loads(_run("providers", "consent", "a", "--json", "--dir", d))[0]["operations"] == []
    bad = runner.invoke(app, ["providers", "allow", "a", "--for", "bogus", "--dir", d])
    assert bad.exit_code == 1
    # embeddings: probe and research only
    _run("embed", "allow", "openai-large-1024", "--for", "research", "--dir", d)
    e = json.loads(_run("embed", "consent", "openai-large-1024", "--json", "--dir", d))[0]
    assert e["operations"] == ["research"]
    assert runner.invoke(app, ["embed", "allow", "openai-large-1024", "--for", "models", "--dir", d]).exit_code == 1
    _run("embed", "revoke", "openai-large-1024", "--dir", d)
    assert json.loads(_run("embed", "consent", "openai-large-1024", "--json", "--dir", d))[0]["operations"] == []


def test_no_default_chosen_is_reported(tmp_path):
    """No default chosen: the backend check says so for the LLM and the embedding model, and sends nothing."""
    import asyncio

    from wider.core.backend_check import check_backends

    root, _ = _new(tmp_path)
    p = Project.load(root)
    p.config.default_provider = ""
    p.config.default_embedding = ""
    p.save()
    out = asyncio.run(check_backends(Project.load(root)))
    errors = {r["role"]: r["error"] for r in out["results"] if not r["ok"]}
    assert errors["llm"] == "no default LLM backend chosen"
    assert errors["embedding"] == "no default embedding model chosen"
    assert all("elapsed_s" not in r for r in out["results"])  # nothing was probed


def test_list_json_matches_rows(tmp_path, monkeypatch):
    import json

    from fastapi.testclient import TestClient

    from tests.unit.helpers import no_requests
    from wider.ui.app import create_app

    monkeypatch.setenv("SYNTH_A_KEY", "synthetic-key-value")
    root = tmp_path / "r"
    Project.init(root)
    d = str(root)
    _run("providers", "add", "--name", "a", "--kind", "openai_compat", "--model", "m1", "--base-url",
         "https://api.openai.com/v1", "--api-key-env", "SYNTH_A_KEY", "--dir", d)
    _run("providers", "add", "--name", "o", "--kind", "ollama", "--model", "qwen3:8b", "--base-url",
         "http://127.0.0.1:11434", "--dir", d)
    _run("embed", "add", "--name", "e", "--kind", "openai_compat", "--model", "emb-1", "--base-url",
         "https://emb.example.invalid/v1", "--dir", d)
    no_requests(monkeypatch)
    llm = json.loads(_run("providers", "list", "--json", "--dir", d))
    emb = json.loads(_run("embed", "list", "--json", "--dir", d))
    client = TestClient(create_app(Project.load(root)))
    assert llm == client.get("/api/providers").json()["providers"]
    assert emb == client.get("/api/embeddings").json()["embeddings"]
    a = next(r for r in llm if r["name"] == "a")
    assert a["locality"] == "cloud" and "api.openai.com" in a["destination"] and a["key_set"] is True
    assert next(r for r in llm if r["name"] == "o")["locality_label"] == "unknown, press Check"
    assert next(r for r in emb if r["name"] == "e")["locality"] == "cloud"
    assert "synthetic-key-value" not in json.dumps(llm) + json.dumps(emb)
