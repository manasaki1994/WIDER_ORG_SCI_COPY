"""WP12 shell: project summary (`wider info`), backend check, recent projects, home routes, static shell."""
import json
import re

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_screen import project as screened  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.construct.benchmark import sync
from wider.core.models import BenchmarkPaper, EmbeddingConfig, ProviderConfig
from wider.core.project import Project
from wider.core.recent import RecentProjects
from wider.core.summary import embedding_locality, project_summary, provider_locality, summary_lines
from wider.ui.app import create_app

runner = CliRunner()


def _fake_project(root, **kw):
    p = Project.init(root, platforms=["scopus"], **kw)
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.config.embeddings.append(EmbeddingConfig(name="fake", kind="fake", model="fake", dim=16))
    p.config.default_provider, p.config.default_embedding = "fake", "fake"
    p.save()
    return Project.load(root)


def test_locality_rules():
    # a loopback Ollama is classed by the routing result for its model (Decision 19); unresolved is unknown
    assert provider_locality("ollama", "http://127.0.0.1:11434", routing=("local", "ollama_tags")) == "local"
    assert provider_locality("ollama", "http://127.0.0.1:11434", routing=("cloud", "name_suffix")) == "cloud"
    assert provider_locality("ollama", "http://127.0.0.1:11434") == "unknown"
    assert provider_locality("ollama", "https://gpu.example.org") == "cloud"
    assert provider_locality("ollama", "http://gpu.local:11434") == "network"
    assert provider_locality("ollama", "http://gpu.local:11434", routing="cloud") == "cloud"
    assert provider_locality("openai_compat", "http://localhost:8000/v1") == "local"
    assert provider_locality("openai_compat", "http://192.168.1.20:8000/v1") == "network"
    assert provider_locality("openai_compat", "http://0.0.0.0:8000/v1") == "unknown"
    assert provider_locality("openai_compat", "https://api.openai.com/v1") == "cloud"
    assert provider_locality("typesafe", "https://api.typesafe.ai") == "cloud"
    assert provider_locality("anthropic") == "cloud" and provider_locality("cli_agent") == "cloud"
    assert provider_locality("fake") == "test"
    assert embedding_locality("sentence_transformers") == "local"
    assert embedding_locality("openai") == "cloud"
    assert embedding_locality("ollama", None) == "local"
    assert embedding_locality("openai_compat", "http://10.0.0.5:1234/v1") == "network"


def test_host_class(monkeypatch):
    import socket

    def no_dns(*a, **k):
        raise AssertionError("host_class must not resolve names")
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    monkeypatch.setattr(socket, "gethostbyname", no_dns)
    from wider.core.summary import host_class
    for url in ("http://127.0.0.1:1", "http://127.8.9.1", "http://localhost:1234", "http://a.localhost", "http://[::1]:8"):
        assert host_class(url) == "loopback", url
    for url in ("http://10.1.2.3", "http://172.16.0.9", "http://192.168.1.20:8000/v1", "http://169.254.1.1",
                "http://[fd00::1]", "http://[fe80::1]", "http://gpu.local", "http://box.lan", "http://x.internal",
                "http://nas.home.arpa", "http://gpu:8000"):
        assert host_class(url) == "network", url
    for url in ("http://0.0.0.0:8000", "http://[::]:1", "", None, "http://"):
        assert host_class(url) == "unknown", url
    for url in ("https://api.openai.com/v1", "http://8.8.8.8", "https://models.example.test"):
        assert host_class(url) == "remote", url


def test_summary_fresh_project(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"], title="Voice review")
    s = project_summary(p)   # no request: the default Ollama model was never checked
    lines = dict(summary_lines(s))
    p.close()
    assert s["backends"]["llm"]["locality"] == "unknown" and s["backends"]["llm"]["locality_source"] == "not_checked"
    assert s["backends"]["llm"]["destination"] == "unknown (via Ollama)"
    assert s["backends"]["embedding"]["locality"] == "local"
    # ui-redesign D11: the next step and what needs you, no stage count
    assert lines["next step"] == "Model for query creation (wider providers)" and lines["needs you"] == \
        "Nothing waits for you"
    assert not any("stages done" in v or re.search(r"\d+ of \d+ stage", v) for v in lines.values())
    assert not {"n_done", "n_stages", "stage_position", "segments"} & set(s)   # nor in the dict the pages read
    assert s["title"] == "Voice review" and s["next"]["key"] == "model" and s["needs_you"] == []
    assert s["records"] == 0 and s["seed"] == {"n": 0, "found": None, "scope": None}
    assert s["tokens"]["calls"] == 0 and s["last_run"] is None and s["backend_check"] is None
    assert s["cost"]["calls"] == 0 and s["cost"]["known"] and s["cost"]["usd"] == 0


def test_summary_makes_no_request(tmp_path, monkeypatch):
    from tests.unit.helpers import no_requests

    root = tmp_path / "r"
    p = Project.init(root, platforms=["scopus"])
    p.config.providers.append(ProviderConfig(name="o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434"))
    p.config.default_provider = "o"
    p.save()
    p.close()
    seen = no_requests(monkeypatch)
    q = Project.load(root)
    s = project_summary(q)
    q.close()
    assert s["backends"]["llm"]["locality_label"] == "unknown, press Check"
    r = runner.invoke(cli_app, ["info", "--dir", str(root)], env={"COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "unknown, press Check" in " ".join(r.output.split())
    assert seen == []


def test_summary_after_setup_tokens_and_coverage(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"], questions=["What leads employees to speak up?"])
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", set="Development"),
                       BenchmarkPaper(benchmark_id="B2", title="Silence in teams", set="Validation")])
    sync(p)
    p.config.stage_providers["construct"] = "fake"   # a construct model is set: the next step is a stage
    st = p.store
    st.execute("PRAGMA foreign_keys=OFF")
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,usage,at) VALUES ('r',1,'A','x',1,?, 't')",
               (json.dumps({"prompt_tokens": 100, "completion_tokens": 20}),))
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,usage,at) VALUES ('r',2,'A','y',1,?, 't')",
               (json.dumps({"input_tokens": 50, "output_tokens": 5}),))
    st.execute("INSERT INTO benchmark_matches(benchmark_id,scope,method,score,checked_at) VALUES ('B1','platform:scopus','doi',1,'t')")
    st.execute("INSERT INTO benchmark_matches(benchmark_id,scope,method,score,checked_at) VALUES ('B2','platform:scopus','none',0,'t')")
    s = project_summary(p)
    p.close()
    assert s["next"]["key"] == "construct" and s["need_you_text"] == "1 needs you"   # D32: Prior reviews to do
    assert s["tokens"] == {"prompt": 150, "completion": 25, "calls": 2}
    assert s["seed"] == {"n": 2, "found": 1, "scope": "platforms", "development": {"n": 1, "found": 1}}   # D3 sets (2a)


def _probe_ok(monkeypatch):
    async def ok(cfg, **kw):
        return {"name": cfg.name, "ok": True, "elapsed_s": 0.0, "error": None}
    monkeypatch.setattr("wider.providers.registry.probe", ok)


def test_info_and_check_backends_cli(tmp_path, monkeypatch):
    root = tmp_path / "r"
    _fake_project(root, title="T")
    r = runner.invoke(cli_app, ["info", "--dir", str(root)])
    assert r.exit_code == 0, r.output
    assert "Model for query creation" in r.output and "fake" in r.output and "not run yet" in r.output
    assert "stages done" not in r.output
    # a backend that answers the probe question wrongly: reported as failed, exit code 1
    async def wrong(cfg, **kw):
        return {"name": cfg.name, "ok": False, "elapsed_s": 0.0, "error": "wrong answer: {'sum': 41}"}
    monkeypatch.setattr("wider.providers.registry.probe", wrong)
    r = runner.invoke(cli_app, ["check-backends", "--dir", str(root)])
    assert r.exit_code == 1 and "wrong answer" in r.output
    assert "LLM fake failed" in runner.invoke(cli_app, ["info", "--dir", str(root)]).output
    _probe_ok(monkeypatch)
    r = runner.invoke(cli_app, ["check-backends", "--dir", str(root)])
    assert r.exit_code == 0, r.output
    assert r.output.count("ok") >= 2
    r = runner.invoke(cli_app, ["info", "--dir", str(root)])
    assert "LLM fake ok" in r.output


def test_check_backends_skips_missing_weights(tmp_path, monkeypatch):
    root = tmp_path / "r"
    p = _fake_project(root)
    p.config.default_embedding = "bge-m3"
    p.save()
    from wider.embeddings import sentence_transformers as st_mod
    monkeypatch.setattr(st_mod.SentenceTransformersEmbedder, "is_downloaded", lambda self: False)
    r = runner.invoke(cli_app, ["check-backends", "--dir", str(root)])
    assert r.exit_code == 1 and "not downloaded" in r.output


def test_recent_projects_file_and_memory(tmp_path):
    f = tmp_path / "home" / "recent_projects.json"
    rp = RecentProjects(f)
    rp.add(tmp_path / "a")
    rp.add(tmp_path / "b")
    rp.add(tmp_path / "a")
    assert [d["path"] for d in rp.list()] == [str((tmp_path / "a").resolve()), str((tmp_path / "b").resolve())]
    data = json.loads(f.read_text())
    assert set(data[0]) == {"path", "opened_at"}  # paths and times only
    rp.remove(tmp_path / "b")
    assert len(RecentProjects(f).list()) == 1
    mem = RecentProjects(None)
    mem.add(tmp_path / "x")
    assert len(mem.list()) == 1 and not any(tmp_path.glob("**/recent_projects.json.tmp"))


def test_home_routes_switch_create_forget(tmp_path):
    a = _fake_project(tmp_path / "a", title="Alpha")
    b = _fake_project(tmp_path / "b", title="Beta")
    recent = tmp_path / "wider_home" / "recent_projects.json"
    c = TestClient(create_app(a, recent_file=recent))
    home = c.get("/api/home").json()
    assert [p["title"] for p in home["projects"]] == ["Alpha"] and home["projects"][0]["current"]
    assert home["recent_persisted"] is True
    assert c.post("/api/projects/open", json={"path": "relative/path"}).status_code == 400
    assert c.post("/api/projects/open", json={"path": str(tmp_path / "nope")}).status_code == 404
    r = c.post("/api/projects/open", json={"path": str(b.root)})
    assert r.status_code == 200 and r.json()["title"] == "Beta"
    assert c.get("/api/project").json()["config"]["title"] == "Beta"  # every route now reads the new folder
    home = c.get("/api/home").json()
    assert [p["title"] for p in home["projects"]] == ["Beta", "Alpha"] and home["projects"][0]["current"]
    # create: refused on an existing project, a missing parent and a non-empty folder
    assert c.post("/api/projects/create", json={"path": str(a.root)}).status_code == 409
    assert c.post("/api/projects/create", json={"path": str(tmp_path / "x" / "y" / "z")}).status_code == 400
    (tmp_path / "busy").mkdir()
    (tmp_path / "busy" / "note.txt").write_text("keep")
    assert c.post("/api/projects/create", json={"path": str(tmp_path / "busy")}).status_code == 400
    r = c.post("/api/projects/create", json={"path": str(tmp_path / "new"), "title": "Gamma",
                                             "questions": ["Q1?", " "], "platforms": ["wos", " Scopus "]})
    assert r.status_code == 200, r.text
    cfg = c.get("/api/project").json()["config"]
    assert cfg["title"] == "Gamma" and cfg["questions"] == ["Q1?"] and [x["key"] for x in cfg["platforms"]] == ["wos", "scopus"]
    assert (tmp_path / "new" / "project.yaml").exists()
    # forget: never the open project, and the folder stays
    assert c.post("/api/projects/forget", json={"path": str(tmp_path / "new")}).status_code == 400
    assert c.post("/api/projects/forget", json={"path": str(a.root)}).status_code == 200
    assert (a.root / "project.yaml").exists()
    assert "Alpha" not in [p["title"] for p in c.get("/api/home").json()["projects"]]
    # a deleted folder is hidden and counted
    import shutil
    shutil.rmtree(b.root)
    home = c.get("/api/home").json()
    assert str(b.root) in home["missing"]
    # the open project's folder deleted: Home still answers, counts it as missing and lists the others
    r = c.post("/api/projects/create", json={"path": str(tmp_path / "gone")})
    assert r.status_code == 200
    shutil.rmtree(tmp_path / "gone")
    r = c.get("/api/home")
    assert r.status_code == 200, r.text
    home = r.json()
    assert str((tmp_path / "gone").resolve()) in home["missing"] and [p["title"] for p in home["projects"]] == ["Gamma"]
    assert not any(p["current"] for p in home["projects"])


def test_home_card_running_job(tmp_path):
    """Scenario "Progress line on the card" (its route half): a job still running in this server is on its project's
    card by label and stage; a job whose server stopped is interrupted and not listed."""
    a = _fake_project(tmp_path / "a", title="Alpha")
    app = create_app(a)
    c = TestClient(app)
    a.store.execute("INSERT INTO jobs(job_id,kind,stage,label,params,status,n_done,n_total,n_start,phase_at,started_at,updated_at) "
                    "VALUES('j1','dedup','dedup','Duplicate check','{}','running',0,NULL,0,'2026-10-29T00:00:00+00:00','2026-10-29T00:00:00+00:00','2026-10-29T00:00:00+00:00')")
    app.state.jobs.is_alive = lambda job_id: True
    assert c.get("/api/home").json()["projects"][0]["running"] == [{"label": "Duplicate check", "stage": "dedup"}]
    app.state.jobs.is_alive = lambda job_id: False
    assert c.get("/api/home").json()["projects"][0]["running"] == []


def test_backend_check_route_and_summary(tmp_path, monkeypatch):
    _probe_ok(monkeypatch)
    a = _fake_project(tmp_path / "a")
    c = TestClient(create_app(a))
    assert c.get("/api/home").json()["recent_persisted"] is False
    r = c.post("/api/backends/check").json()
    assert r["ok"] and {x["role"] for x in r["results"]} == {"llm", "embedding"}
    s = c.get("/api/project/summary").json()
    assert s["backend_check"]["ok"] and s["backends"]["llm"]["locality"] == "test"


def test_static_shell(tmp_path):
    a = Project.init(tmp_path / "a")
    c = TestClient(create_app(a))
    html = c.get("/").text
    for needle in ['id="sidebar"', 'id="sidebar-toggle"', 'aria-expanded="true"', 'id="page-home"', 'id="page-help"',
                   'id="page-settings"', 'id="page-model"', 'id="i-model"', 'id="topbar"', 'id="tb-crumb"',
                   'id="tb-notebook"', 'id="tb-guide"', 'id="tb-main"', 'class="skip-link"', "/static/shell.js",
                   'id="i-home"', 'id="nav-foot"', 'class="nav-project-box"']:
        assert needle in html, needle
    assert 'id="stepper"' not in html and "renderStepper" not in c.get("/static/shell.js").text  # the top bar replaces it
    # every stage page has a title row with a status chip and an Explain button, explanation hidden until asked
    for key in ["setup", "construct", "query", "import", "dedup", "enrich", "rank", "sample", "screen", "recall"]:
        # the Deduplication chip is dedup.js's own (it adds the waiting count), the others are app.js's state chips
        assert (f'data-chip="{key}"' if key != "dedup" else 'id="dd-chip"') in html and f'data-explain="{key}"' in html
        assert f'id="why-{key}" data-why="{key}" hidden' in html
        assert f'id="i-{key}"' in html  # an icon per stage
    js = c.get("/static/shell.js").text
    assert "wider.sidebar" in js and "aria-current" in js and "/api/home" in js and "/api/backends/check" in js
    app_js = c.get("/static/app.js").text
    assert app_js.count('|| "home"') == 1 and "startPage()" in app_js
    # an unknown or stale hash opens Home and rewrites the address; a failed load leaves a visible banner
    assert 'history.replaceState(null, "", "#home")' in app_js and "pageLoadFailed" in app_js
    assert 'id="load-error"' in html and 'role="alert"' in html and "Could not load the stage list" in app_js
    # the Help keys match the keys the drawer and shell handle
    for key in ['"j / k"', '"1 / 2 / 3"', '["n", ', '["t", ', '"Esc"', "Ctrl+Z / Cmd+Z", '"Tab, Shift+Tab"', "Skip to content"]:
        assert key in js, key
    assert '"n / p"' not in js
    # duplicate pairs have four keys (dedup.js): 1 keep A, 2 keep B, 3 not duplicates, 4 unsure
    assert '"1 / 2 / 3 / 4", "duplicate pairs: duplicates, keep A / duplicates, keep B / not duplicates / unsure"' in js
    assert "duplicate / not duplicate / unsure" not in js
    assert "close the record drawer" in js and "back to the list on the Deduplication page" in js
    # the Query count is the compact string that Copy puts on the clipboard; the development platform is named by role
    assert "characters when pasted" in app_js and "render?compact=true" in app_js
    assert 'p.role === "development"' in app_js and 'id="q-dev"' in html
    assert "The first platform is the development platform" not in html


def test_no_page_says_explain_this_stage():
    """Every stage page names the button "Explain this step", a quiet button; no page, Help included, keeps "stage"."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    static = root / "wider" / "ui" / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    assert html.count(">Explain this step</button>") == 10
    assert html.count('class="btn-quiet small-btn explain-btn"') == 10
    for f in [static / "index.html", *static.glob("*.js"), root / "docs" / "guide.md"]:
        assert "Explain this stage" not in f.read_text(encoding="utf-8"), f.name


def test_check_backends_includes_stage_overrides(tmp_path, monkeypatch):
    root = tmp_path / "r"
    p = _fake_project(root)
    p.config.providers += [ProviderConfig(name="a", kind="fake", model="fake"),
                           ProviderConfig(name="b", kind="fake", model="fake")]
    p.config.default_provider = "a"
    p.config.stage_providers = {"construct": "a", "screen": "b"}
    p.save()
    probed = []

    async def probe(cfg, **kw):
        probed.append(cfg.name)
        ok = cfg.name != "b"
        return {"name": cfg.name, "ok": ok, "elapsed_s": 0.0, "error": None if ok else "wrong answer: 41"}
    monkeypatch.setattr("wider.providers.registry.probe", probe)
    r = runner.invoke(cli_app, ["check-backends", "--dir", str(root)])
    assert r.exit_code == 1, r.output
    assert probed == ["a", "b"]                  # each distinct backend once
    assert "llm (default, construct) a" in r.output and "llm (screen) b" in r.output
    out = json.loads(Project.load(root).store.get_meta("backend_check"))
    llm = [x for x in out["results"] if x["role"] == "llm"]
    assert [(x["name"], x["uses"]) for x in llm] == [("a", ["default", "construct"]), ("b", ["screen"])]


def test_check_backends_needs_consent(tmp_path, monkeypatch):
    root = tmp_path / "r"
    p = _fake_project(root)
    p.config.providers.append(ProviderConfig(name="a", kind="openai_compat", model="m",
                                             base_url="https://llm.example.invalid/v1"))
    p.config.default_provider = "a"
    p.save()
    probed = []

    async def probe(cfg, **kw):
        probed.append(cfg.name)
        return {"name": cfg.name, "ok": True}
    monkeypatch.setattr("wider.providers.registry.probe", probe)
    r = runner.invoke(cli_app, ["check-backends", "--dir", str(root)])
    out = " ".join(r.output.split())
    assert r.exit_code == 1, r.output
    assert probed == []                          # nothing was sent to a
    assert "not checked: needs your consent to contact llm.example.invalid" in out
    assert "wider providers allow a --for probe" in out
    row = next(x for x in json.loads(Project.load(root).store.get_meta("backend_check"))["results"] if x["name"] == "a")
    assert row["needs_consent"] and not row["ok"]


def test_info_tokens_count_normalised_usage(tmp_path):
    """`wider info` sums screening tokens from rows stored before C2 (vendor keys) and normalised rows alike."""
    p = _fake_project(tmp_path / "r")
    st = p.store
    st.execute("PRAGMA foreign_keys=OFF")
    for i, usage in enumerate([{"input_tokens": 10, "output_tokens": 5},
                               {"input_uncached": 20, "input_cached": 30, "input_cache_write": None, "output": 7,
                                "raw": {}}]):
        st.execute("INSERT INTO screen_calls(run_id, record_id, pass, input_sha256, ok, usage, at) "
                   "VALUES('r', ?, 'A', 'x', 1, ?, 't')", (i + 1, json.dumps(usage)))
    tok = project_summary(p)["tokens"]
    assert tok == {"prompt": 60, "completion": 12, "calls": 2}
    r = runner.invoke(cli_app, ["info", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert "72 over 2 screening calls" in r.output


def test_ollama_destination_names_its_host(tmp_path, monkeypatch):
    """An Ollama off this computer names its host; only a cloud-tagged model names ollama.com (author's decision,
    15 Oct 2026). The same words reach the Settings and picker rows (GET /api/providers and /api/embeddings) and
    `wider providers list --json`, with no request."""
    from tests.unit.helpers import no_requests
    from wider.core.backend_consent import _endpoint
    from wider.core.detect import destination, effective_locality

    def oll(model, base):
        return ProviderConfig(name="o", kind="ollama", model=model, base_url=base)

    loop, lan, far = "http://127.0.0.1:11434", "http://192.168.1.20:11434", "http://ollama-host.example.invalid:11434"
    # loopback: by its routing (the tag list or the name)
    assert destination(oll("m", loop), "local") == "this computer"
    assert destination(oll("m", loop), "cloud") == "ollama.com (via Ollama)"
    assert destination(oll("m", loop), "unknown") == "unknown (via Ollama)"
    seen = no_requests(monkeypatch)
    # a network or remote host, with no request: the host, or ollama.com for a cloud-tagged model
    assert effective_locality(oll("m", lan))[2].startswith("192.168.1.20 (another computer on your network")
    assert effective_locality(oll("m", far)) == ("cloud", "not_established", "ollama-host.example.invalid (via Ollama)")
    assert effective_locality(oll("m-cloud", far))[2] == "ollama.com (via Ollama at ollama-host.example.invalid)"
    assert effective_locality(oll("m-cloud", lan))[2] == "ollama.com (via Ollama at 192.168.1.20)"
    assert destination(oll("m-cloud", loop), "cloud") == "ollama.com (via Ollama)"
    # the consent fingerprint's endpoint adds ollama.com only for a cloud model
    assert _endpoint(oll("m", far), "cloud") == "http://ollama-host.example.invalid:11434"
    assert _endpoint(oll("m-cloud", far), "cloud") == "http://ollama-host.example.invalid:11434 -> ollama.com"
    # the rows Settings and the pickers show, and the CLI's
    p = _fake_project(tmp_path / "r")
    p.config.providers = [ProviderConfig(name="far", kind="ollama", model="m", base_url=far),
                          ProviderConfig(name="farcloud", kind="ollama", model="m-cloud", base_url=far),
                          ProviderConfig(name="lan", kind="ollama", model="m", base_url=lan),
                          ProviderConfig(name="loopcloud", kind="ollama", model="m-cloud", base_url=loop)]
    p.config.embeddings = [EmbeddingConfig(name="efar", kind="ollama", model="e", base_url=far, dim=8)]
    p.save()
    want = {"far": "ollama-host.example.invalid (via Ollama)",
            "farcloud": "ollama.com (via Ollama at ollama-host.example.invalid)",
            "lan": "192.168.1.20 (another computer on your network: data leaves this computer)",
            "loopcloud": "ollama.com (via Ollama)"}
    c = TestClient(create_app(Project.load(p.root)))
    assert {r["name"]: r["destination"] for r in c.get("/api/providers").json()["providers"]} == want
    assert {r["name"]: r["destination"] for r in c.get("/api/embeddings").json()["embeddings"]} == \
        {"efar": "ollama-host.example.invalid (via Ollama)"}
    r = CliRunner().invoke(cli_app, ["providers", "list", "--json", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert {x["name"]: x["destination"] for x in json.loads(r.output)} == want
    assert seen == []


def test_destinations():
    from wider.core.detect import destination

    oll = ProviderConfig(name="o", kind="ollama", model="m", base_url="http://127.0.0.1:11434")
    assert destination(oll, "local") == "this computer"
    assert destination(oll, "cloud") == "ollama.com (via Ollama)"
    assert destination(oll, "unknown") == "unknown (via Ollama)"
    assert destination(oll) == "unknown (via Ollama)"   # routing not established
    lan = ProviderConfig(name="l", kind="openai_compat", model="m", base_url="http://192.168.1.20:8000/v1")
    assert destination(lan).startswith("192.168.1.20 (another computer on your network")
    assert destination(ProviderConfig(name="c", kind="openai_compat", model="m",
                                      base_url="https://api.example.org/v1")) == "api.example.org"
    assert destination(ProviderConfig(name="a", kind="anthropic", model="m")) == "api.anthropic.com"
    assert destination(ProviderConfig(name="f", kind="fake", model="f")) == "nowhere (test backend)"
    assert destination(ProviderConfig(name="x", kind="cli_agent", command=["x"])) == "unknown (custom CLI)"
    cc = ProviderConfig(name="cc", kind="cli_agent", preset="claude-code", command=["claude"])
    assert destination(cc).endswith("(via Claude Code)")


def test_page_names_follow_the_registry(tmp_path):
    """`GET /api/stages` passes each stage's title (the page name), its section, the four sections, the project title
    and a project key that names the folder without its path."""
    from wider.core.stages import load_stages

    p = _fake_project(tmp_path / "pages", title="A synthetic title")
    c = TestClient(create_app(p))
    d = c.get("/api/stages").json()
    reg = {s["key"]: s for s in load_stages()}
    for s in d["stages"]:
        assert s["title"] == reg[s["key"]]["title"]
        assert s["short"] == s["title"] or s["state"] == "extension"
        assert s.get("section") == reg[s["key"]].get("section")
    assert [s["title"] for s in d["stages"] if s["state"] != "extension"][:4] == [
        "Review plan", "Concepts and synonyms", "Search strings", "Search results"]
    assert [s["number"] for s in d["sections"]] == [1, 2, 3, 4]
    assert d["title"] == "A synthetic title"
    key = d["project_key"]
    assert len(key) == 16 and all(ch in "0123456789abcdef" for ch in key)
    root = str(p.root.resolve())
    assert key not in root and all(part not in key for part in p.root.resolve().parts if len(part) > 2)
    assert c.get("/api/stages").json()["project_key"] == key  # stable for the same folder
    assert d["construct_backend"] is None   # no model for query creation yet: the project opens on the model page
    p.config.stage_providers["construct"] = "fake"
    p.save()
    assert c.get("/api/stages").json()["construct_backend"] == "fake"


def test_coverage_heading_has_no_controls():
    """The "Seed paper coverage" heading holds only its name; the validation checkbox is labelled in the toolbar under it."""
    import re
    from pathlib import Path

    html = (Path(__file__).parents[2] / "wider" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    m = re.search(r"<h2>(Seed paper coverage[^<]*)</h2>\s*<div class=\"row toolbar\">(.*?)</div>", html, re.S)
    assert m and m.group(1).strip() == "Seed paper coverage"
    assert re.search(r'<label[^>]*>\s*<input type="checkbox" id="cov-val">\s*include validation set</label>', m.group(2))


def test_no_online_font_or_script():
    """Nothing the page loads comes from the internet (design-pass-a1-dedup D3): no http(s) address in a
    <link href>, <script src>, @import or @font-face src. The SVG namespace and the doi.org links stay allowed."""
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
    online = re.compile(r"""(?:<link\b[^>]*\bhref|<script\b[^>]*\bsrc)\s*=\s*["']?\s*(?:https?:)?//""", re.I)
    css_online = re.compile(r"""@import\s+(?:url\()?\s*["']?\s*(?:https?:)?//|url\(\s*["']?\s*(?:https?:)?//""", re.I)
    html = (static / "index.html").read_text(encoding="utf-8")
    assert not online.search(html)
    css = (static / "style.css").read_text(encoding="utf-8")
    assert not css_online.search(css)
    for face in re.findall(r"@font-face\s*\{[^}]*\}", css):
        assert "http" not in face and 'url("fonts/' in face
    assert len(re.findall(r"@font-face", css)) == 4 and '"Public Sans", system-ui' in css
    for js in static.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert not online.search(text), js.name
        assert not re.search(r"""\.src\s*=\s*["'](?:https?:)?//""", text), js.name
    # the package ships the folder (the wheel check of task 1.2 listed the files once)
    pyproject = (static.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"ui/static/fonts/*"' in pyproject
    assert sorted(f.name for f in (static / "fonts").iterdir()) == [
        "OFL.txt", "PublicSans-Bold.woff2", "PublicSans-Medium.woff2", "PublicSans-Regular.woff2", "PublicSans-SemiBold.woff2"]


def test_font_served_locally(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "a")))
    for w in ("Regular", "Medium", "SemiBold", "Bold"):
        r = c.get(f"/static/fonts/PublicSans-{w}.woff2")
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2"
        assert r.content[:4] == b"wOF2"
    assert "SIL Open Font License" in c.get("/static/fonts/OFL.txt").text


def test_summary_needs_you_without_stage_count(screened):  # noqa: F811
    """ui-redesign D11: the summary and `wider info` show the next step and what needs the researcher (needs-you and
    needs-review items), never a stage count."""
    from tests.unit.test_rank_versions import add_abstracts

    p = screened
    p.config.stage_providers["construct"] = "fake"
    p.save()
    before = len(project_summary(p)["needs_you"])
    add_abstracts(p)
    s = project_summary(p)
    assert ("rank", "needs_review") in [(x["stage"], x["kind"]) for x in s["needs_you"]]
    n = before + 1
    assert len(s["needs_you"]) == n and s["need_you_text"] == f"{n} {'needs' if n == 1 else 'need'} you"
    p.close()
    r = runner.invoke(cli_app, ["info", "--dir", str(p.root)])
    out = " ".join(r.output.split())
    assert s["need_you_text"] in out and "Ranking: the records or their abstracts changed since this ranking" in out
    assert "stages done" not in out
