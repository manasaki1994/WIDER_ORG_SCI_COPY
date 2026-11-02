"""Consent before contacting a network, cloud or unknown backend (design Decision 17; core/backend_consent.py). Every
backend here is synthetic and every request goes to a MockTransport that records it, so "nothing was sent" is checked
by an empty record list."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_lookups import carry  # noqa: F401  (fixture: a synthetic project with two records)
from wider.cli import app as cli_app
from wider.core import backend_consent as bc
from wider.core.models import EmbeddingConfig, ProviderConfig
from wider.core.project import Project
from wider.ui.app import create_app

runner = CliRunner()
REAL_ASYNC_CLIENT = httpx.AsyncClient   # captured here: the tests patch the httpx module attribute itself
REAL_POST = httpx.post
LLM_BASE = "https://llm.example.invalid/v1"
EMB_BASE = "https://emb.example.invalid/v1"


def _llm_transport(monkeypatch, sent: list[httpx.Request]) -> None:
    """An OpenAI-compatible endpoint: GET /models lists two models, POST /chat/completions answers from the schema."""
    from wider.providers.fake import _example_from_schema

    def answer(request: httpx.Request):
        sent.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "m1"}, {"id": "m2"}]})
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        out = {"status": "ok", "sum": 42} if "sum" in schema.get("properties", {}) else _example_from_schema(schema)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(out)}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "m1"})

    monkeypatch.setattr("wider.providers.openai_compat.httpx.AsyncClient",
                        lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(answer), **kw))


def _emb_transport(monkeypatch, sent: list[list[str]]) -> None:
    """An OpenAI-compatible embedding endpoint: records each input list; the first two probe sentences come out close."""
    def post(url, json=None, headers=None, timeout=None):
        texts = list(json["input"])
        sent.append(texts)
        from wider.embeddings.registry import PROBE_TEXTS  # by text: the embedder reorders a batch by length
        vecs = [[1.0, 0.0, 0.0] if t == PROBE_TEXTS[0] else [0.9, 0.1, 0.0] if t == PROBE_TEXTS[1] else [0.0, 0.0, 1.0]
                for t in texts]
        return httpx.Response(200, json={"data": [{"index": i, "embedding": v} for i, v in enumerate(vecs)],
                                         "usage": {"prompt_tokens": 3}}, request=httpx.Request("POST", url))

    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", post)


@pytest.fixture
def proj(carry, monkeypatch):  # noqa: F811
    """`carry` plus cloud LLM backend `a` (the default) and cloud embedding backend `e`, all without consent."""
    monkeypatch.setenv("A_KEY", "synthetic-not-a-key")
    carry.config.providers.append(ProviderConfig(name="a", kind="openai_compat", model="m1", base_url=LLM_BASE,
                                                 api_key_env="A_KEY"))
    carry.config.embeddings.append(EmbeddingConfig(name="e", kind="openai_compat", model="emb-1", base_url=EMB_BASE))
    carry.config.default_provider = "a"
    carry.save()
    return carry


@pytest.fixture
def sent(monkeypatch):
    out: list[httpx.Request] = []
    _llm_transport(monkeypatch, out)
    return out


@pytest.fixture
def embedded(monkeypatch):
    out: list[list[str]] = []
    _emb_transport(monkeypatch, out)
    return out


def cli(p: Project, *args: str, input: str | None = None):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], input=input)


def _reload(p: Project) -> Project:
    return Project.load(p.root)


def _runs(p: Project) -> int:
    return _reload(p).store.scalar("SELECT COUNT(*) FROM runs")


def _set(p: Project, name: str, **changes) -> None:
    q = _reload(p)
    for c in q.config.providers + q.config.embeddings:
        if c.name == name:
            for k, v in changes.items():
                setattr(c, k, v)
    q.save()


# ----------------------------------------------------------------- LLM backends
def test_models_need_consent(proj, sent):
    r = cli(proj, "providers", "models", "a")
    assert r.exit_code == 2, r.output
    out = " ".join(r.output.split())
    assert "llm.example.invalid" in out and "model list" in out and "nothing was sent" in out
    assert "wider providers allow a --for models" in out
    assert sent == [] and _reload(proj).config.backend_consent == {}


def test_consent_per_operation(proj, sent):
    assert cli(proj, "providers", "allow", "a", "--for", "models").exit_code == 0
    for _ in range(2):
        r = cli(proj, "providers", "models", "a")
        assert r.exit_code == 0 and "m1" in r.output and "m2" in r.output, r.output
    assert [q.method for q in sent] == ["GET", "GET"]
    assert all(q.headers["authorization"] == "Bearer synthetic-not-a-key" for q in sent)
    r = cli(proj, "providers", "probe", "a")
    assert r.exit_code == 2 and "--for probe" in " ".join(r.output.split())
    assert len(sent) == 2   # the probe sent nothing
    assert _reload(proj).config.backend_consent["a"].operations == ["models"]


RESEARCH_CLI = [
    ("screen", "run", "corpus"),
    ("screen", "protocol"),
    ("screen", "second"),
    ("construct", "propose", "codebook"),
    ("construct", "adjacent", "propose"),
    ("construct", "adjacent", "find"),
]
RESEARCH_ROUTES = [
    ("/api/screen/run", {"scope": "corpus"}),
    ("/api/jobs/screen", {"scope": "corpus"}),
    ("/api/jobs/screen", {"scope": "corpus", "mode": "protocol"}),
    ("/api/jobs/propose/codebook", None),
    ("/api/adjacent/propose", None),
    ("/api/adjacent/find", {}),
]


def test_research_needs_consent(proj, sent):
    bc.allow_backend(proj, "a", ["models", "probe"])
    n_runs = _runs(proj)
    for args in RESEARCH_CLI:
        r = cli(proj, *args)
        assert r.exit_code == 2, (args, r.output)
        assert "wider providers allow a --for research" in " ".join(r.output.split())
    assert sent == [] and _runs(proj) == n_runs
    # the engines refuse too, whoever calls them
    from wider.construct.adjacent import propose_from_question
    with pytest.raises(bc.BackendConsentRequired):
        propose_from_question(_reload(proj))
    assert sent == []
    # allowed: the same run starts and the call goes out
    assert cli(proj, "providers", "allow", "a", "--for", "research").exit_code == 0
    from wider.core.models import BenchmarkPaper
    proj.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="A synthetic seed title on shared calendars",
                                         doi="10.1000/synth.s1", set="Development")])
    r = cli(proj, "construct", "propose", "codebook")
    assert r.exit_code != 2, r.output
    assert sent and {q.method for q in sent} == {"POST"}


@pytest.mark.parametrize("surface", [("cli", a) for a in RESEARCH_CLI] + [("route", r) for r in RESEARCH_ROUTES],
                         ids=lambda s: " ".join(s[1]) if s[0] == "cli" else f"{s[1][0]} {s[1][1] or ''}")
def test_research_route_consent_403(proj, sent, surface):
    bc.allow_backend(proj, "a", ["models", "probe"])
    n_runs = _runs(proj)
    how, what = surface
    if how == "cli":
        r = cli(proj, *what)
        assert r.exit_code == 2, r.output
    else:
        client = TestClient(create_app(_reload(proj)))
        path, body = what
        r = client.post(path, json=body) if body is not None else client.post(path)
        assert r.status_code == 403, r.text
        d = r.json()["detail"]["backend_consent_required"]
        assert d["backend"] == "a" and d["kind"] == "llm" and d["operation"] == "research"
        assert d["destination"] == "llm.example.invalid" and d["locality"] == "cloud"
        assert "title and abstract" in d["sent"]
        assert client.get("/api/jobs").json() in ([], {"jobs": []}) or not client.get("/api/jobs").json().get("jobs")
    assert sent == [] and _runs(proj) == n_runs


def test_fingerprint_change_renews(proj, sent):
    bc.allow_backend(proj, "a", ["models", "probe", "research"])
    _set(proj, "a", base_url="https://other.example.invalid/v1")
    p = _reload(proj)
    with pytest.raises(bc.BackendConsentRequired) as ei:
        bc.require_backend_consent(p, p.provider("a"), "research")
    msg = str(ei.value)
    assert "llm.example.invalid" in msg and "other.example.invalid" in msg and "changed" in msg
    r = cli(proj, "providers", "models", "a")
    assert r.exit_code == 2 and "other.example.invalid" in r.output
    assert sent == []
    # consent anew replaces the old record rather than extending it
    row = bc.allow_backend(p, "a", ["models"])
    assert row["operations"] == ["models"] and not row["stale"]


def test_network_needs_consent(proj, sent):
    _set(proj, "a", base_url="http://192.168.1.20:8000/v1", api_key_env=None)
    p = _reload(proj)
    with pytest.raises(bc.BackendConsentRequired) as ei:
        bc.require_backend_consent(p, p.provider("a"), "research")
    e = ei.value
    assert e.locality == "network"
    for words in ("Local network", "192.168.1.20", "data leaves this computer", "unencrypted (http)"):
        assert words in str(e), words
    pl = e.payload()["backend_consent_required"]
    assert "Local network" in pl["destination"] and "unencrypted" in pl["warning"]
    r = cli(proj, "screen", "run", "corpus")
    assert r.exit_code == 2 and "Local network" in " ".join(r.output.split())
    bc.allow_backend(p, "a", ["research"])
    bc.require_backend_consent(_reload(proj), p.provider("a"), "research")
    _set(proj, "a", base_url="http://192.168.1.21:8000/v1")
    p = _reload(proj)
    with pytest.raises(bc.BackendConsentRequired) as ei:
        bc.require_backend_consent(p, p.provider("a"), "research")
    assert "192.168.1.20" in str(ei.value) and "192.168.1.21" in str(ei.value)
    assert sent == []


def test_revoke_and_decline(proj, sent, monkeypatch):
    from wider.providers.registry import research_provider

    lookups_before = _reload(proj).config.online_lookups.model_dump()
    # declining in a terminal stores nothing and sends nothing
    monkeypatch.setattr("wider.cli_backend_consent._interactive", lambda: True)
    r = cli(proj, "providers", "models", "a", input="n\n")
    assert r.exit_code == 2 and "nothing was sent" in r.output
    assert _reload(proj).config.backend_consent == {} and sent == []
    # yes stores consent for that operation only, then the list is fetched
    r = cli(proj, "providers", "models", "a", input="y\n")
    assert r.exit_code == 0 and "m1" in r.output, r.output
    assert _reload(proj).config.backend_consent["a"].operations == ["models"] and len(sent) == 1
    monkeypatch.setattr("wider.cli_backend_consent._interactive", lambda: False)
    # revoked: nothing more is sent
    bc.allow_backend(_reload(proj), "a", ["probe", "research"])
    assert cli(proj, "providers", "revoke", "a").exit_code == 0
    p = _reload(proj)
    assert p.config.backend_consent == {}
    for args in (("providers", "models", "a"), ("providers", "probe", "a"), ("screen", "run", "corpus")):
        assert cli(proj, *args).exit_code == 2
    assert len(sent) == 1
    # everything else is unchanged: lookups consent, the test backend, labelling data
    assert p.config.online_lookups.model_dump() == lookups_before
    p.config.providers.append(ProviderConfig(name="f", kind="fake", model="fake"))
    assert research_provider(p, p.provider("f")) is not None
    rows = json.loads(cli(proj, "providers", "consent", "--json").output)
    assert next(x for x in rows if x["name"] == "a")["operations"] == []


def _local_ollama(monkeypatch, listed: bool = True) -> ProviderConfig:
    from wider.core import detect

    tags = {"models": [{"name": "qwen3:8b", "model": "qwen3:8b"}]} if listed else None
    monkeypatch.setattr(detect, "http_get", lambda url, timeout=detect.HTTP_TIMEOUT_S: (200, tags) if tags else None)
    return ProviderConfig(name="o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434")


def test_local_needs_none(proj, monkeypatch):
    from wider.providers.registry import research_provider

    o = _local_ollama(monkeypatch)
    proj.config.providers += [o, ProviderConfig(name="f", kind="fake", model="fake")]
    proj.save()
    yaml_before = (proj.root / "project.yaml").read_text()
    p = _reload(proj)
    for name in ("o", "f"):
        cfg = p.provider(name)
        for op in bc.OPERATIONS:
            assert bc.require_backend_consent(p, cfg, op)["needs"] is False
        assert research_provider(p, cfg) is not None
        with pytest.raises(ValueError, match="needs no consent"):
            bc.allow_backend(p, name, ["research"])
    assert (proj.root / "project.yaml").read_text() == yaml_before
    assert _reload(proj).config.backend_consent == {}
    # a CLI backend lists its catalogue models without asking and without running the CLI
    import asyncio
    import subprocess

    def no_run(*a, **k):
        raise AssertionError("the CLI was run")

    monkeypatch.setattr(subprocess, "run", no_run)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_run)
    cc = next(x for x in _reload(proj).config.providers if x.kind == "cli_agent")
    r = cli(proj, "providers", "models", cc.name)
    assert r.exit_code == 0 and r.output.strip(), r.output
    assert _reload(proj).config.backend_consent == {}


def test_unreachable_loopback_no_consent(proj, monkeypatch):
    from wider.providers.base import ProviderError
    from wider.providers.registry import research_provider

    o = _local_ollama(monkeypatch, listed=False)   # nothing answers on 127.0.0.1:11434
    proj.config.providers.append(o)
    proj.save()
    p = _reload(proj)
    info = bc.assess(p.provider("o"))
    assert info["unreachable"] and not info["needs"]
    with pytest.raises(ProviderError, match=r"cannot reach 127\.0\.0\.1:11434"):
        research_provider(p, p.provider("o"))

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr("wider.providers.ollama.httpx.AsyncClient",
                        lambda **kw: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(refuse), **kw))
    r = cli(proj, "providers", "probe", "o")
    out = " ".join(r.output.split())
    assert r.exit_code == 1 and "127.0.0.1:11434" in out and "consent" not in out, r.output
    assert _reload(proj).config.backend_consent == {}


# Only these functions may build an adapter: each checks consent first (Decision 17) or contacts nothing.
GATES = {
    ("providers/registry.py", "research_provider"), ("providers/registry.py", "probe"),
    ("embeddings/registry.py", "probe"), ("core/detect.py", "list_models"),
    ("rank/corpus.py", "embed_corpus"), ("rank/corpus.py", "embed_texts"),
    ("core/backends.py", "is_downloaded"), ("core/backends.py", "download_embedding"),
    ("providers/registry.py", "backend_identity"),   # identity only (the backend-change preflight): no call
    ("assistant/retrieve.py", "_loaded"),   # Ask WIDER: only with the config meaning_backend cleared (consent there)
}


def _builder_calls() -> set[tuple[str, str]]:
    root = Path(__file__).resolve().parents[2] / "wider"
    found = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(root).as_posix()

        def walk(node, fn):
            for child in ast.iter_child_nodes(node):
                name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
                if isinstance(child, ast.Call):
                    f = child.func
                    called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                    if called in ("make_provider", "make_embedder"):
                        found.add((rel, fn))
                walk(child, name)

        walk(tree, "<module>")
    return found


def test_every_llm_caller_checks_consent():
    calls = _builder_calls()
    assert calls - GATES == set(), "a new caller builds an adapter without the consent gate"
    assert {("providers/registry.py", "research_provider"), ("rank/corpus.py", "embed_corpus")} <= calls
    # the gate functions check consent before they build
    import inspect

    from wider.embeddings import registry as er
    from wider.providers import registry as pr
    from wider.rank import corpus

    src = inspect.getsource(pr.research_provider)
    assert src.index("require_backend_consent(") < src.index("make_provider(")
    for fn in (er.probe, corpus.embed_corpus, corpus.embed_texts):
        src = inspect.getsource(fn)
        assert src.index("require_backend_consent(") < src.index("make_embedder("), fn.__name__
    from wider.assistant import retrieve as ar
    src = inspect.getsource(ar.retrieve)
    assert "require_backend_consent(" in inspect.getsource(ar.meaning_backend)
    assert src.index("meaning_backend(") < src.index("meaning_rank(")
    # the engines go through the gate
    root = Path(__file__).resolve().parents[2] / "wider"
    for rel in ("screen/engine.py", "construct/propose.py", "construct/adjacent.py", "rank/adjacent.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "research_provider(" in text and "make_provider(" not in text, rel


def test_probe_callers_check_consent(proj, sent, monkeypatch):
    import asyncio
    import inspect

    from wider.core.backend_check import check_backends
    from wider.providers.registry import probe

    # only the LLM row is checked here: skip the embedding probe as on CI, so a downloaded local model is not loaded
    monkeypatch.setattr("wider.core.backends.is_downloaded", lambda cfg: False)

    assert inspect.signature(probe).parameters["locality"].kind is inspect.Parameter.KEYWORD_ONLY
    r = cli(proj, "providers", "probe", "a")
    assert r.exit_code == 2 and "--for probe" in " ".join(r.output.split())
    client = TestClient(create_app(_reload(proj)))
    r = client.post("/api/providers/a/probe")
    assert r.status_code == 403 and r.json()["detail"]["backend_consent_required"]["operation"] == "probe"
    out = asyncio.run(check_backends(_reload(proj)))
    row = next(x for x in out["results"] if x["role"] == "llm")
    assert row["needs_consent"] and not row["ok"]
    assert "llm.example.invalid" in row["error"] and "wider providers allow a --for probe" in row["error"]
    assert sent == []
    bc.allow_backend(_reload(proj), "a", ["probe"])
    r = cli(proj, "providers", "probe", "a")
    assert r.exit_code == 0, r.output
    assert len(sent) == 1 and sent[0].method == "POST"


# ----------------------------------------------------------------- embedding backends
def test_embed_research_needs_consent(proj, embedded):
    bc.allow_backend(proj, "e", ["probe"], embedding=True)
    r = cli(proj, "embed", "corpus", "--embedding", "e")
    assert r.exit_code == 2, r.output
    assert "wider embed allow e --for research" in " ".join(r.output.split())
    client = TestClient(create_app(_reload(proj)))
    r = client.post("/api/jobs/embed", params={"embedding": "e"})
    assert r.status_code == 403, r.text
    d = r.json()["detail"]["backend_consent_required"]
    assert d["kind"] == "embedding" and d["operation"] == "research" and d["backend"] == "e"
    assert not client.get("/api/jobs").json() or not client.get("/api/jobs").json().get("jobs", [1])
    assert embedded == []
    assert cli(proj, "embed", "allow", "e", "--for", "research").exit_code == 0
    r = cli(proj, "embed", "corpus", "--embedding", "e")
    assert r.exit_code == 0, r.output
    assert embedded and any("quillwort marker one" in t for batch in embedded for t in batch)


def test_embed_probe_vs_research(proj, embedded):
    from wider.embeddings.registry import PROBE_TEXTS

    assert cli(proj, "embed", "allow", "e", "--for", "probe").exit_code == 0
    r = cli(proj, "embed", "probe", "e")
    assert r.exit_code == 0, r.output
    assert len(embedded) == 1 and sorted(embedded[0]) == sorted(PROBE_TEXTS)   # the fixed sentences only
    r = cli(proj, "rank", "run", "--embedding", "e")
    assert r.exit_code == 2 and "--for research" in " ".join(r.output.split()), r.output
    assert len(embedded) == 1
    # LLM consent is kept apart: an LLM backend named e has none from this
    p = _reload(proj)
    p.config.providers.append(ProviderConfig(name="e", kind="openai_compat", model="m", base_url=EMB_BASE))
    with pytest.raises(bc.BackendConsentRequired):
        bc.require_backend_consent(p, p.provider("e"), "probe")
    assert "e" not in p.config.backend_consent


def _with_sets(p: Project) -> None:
    """An embedding set and a default ranking made with `e` (rows only; nothing is embedded)."""
    st = p.store
    st.execute("INSERT INTO embedding_sets(set_name, model, kind, dim, n, text_hash, file, created_at) "
               "VALUES ('e', 'emb-1', 'openai_compat', 3, 2, 'h', 'e.npy', '2026-10-13')")
    st.execute("INSERT INTO rankings(name, set_name, anchors_version, top_n, n, md5, created_at) "
               "VALUES ('r1', 'e', 1, 10, 2, 'h', '2026-10-13')")
    st.set_meta("default_ranking", "r1")


EMBED_SURFACES = [
    ("route", "post", "/api/jobs/embed", {"embedding": "e"}),
    ("route", "post", "/api/rank/embed", {"embedding": "e"}),
    ("route", "post", "/api/rank/run", {"embedding": "e"}),
    ("route", "get", "/api/rank/ablate", {}),
    ("route", "post", "/api/samples/draw", {"which": "development"}),
    ("cli", "embed", "corpus", "--embedding", "e"),
    ("cli", "rank", "run", "--embedding", "e"),
    ("cli", "rank", "ablate"),
    ("cli", "sample", "draw", "development"),
]


@pytest.mark.parametrize("surface", EMBED_SURFACES, ids=lambda s: " ".join(map(str, s[1:3])))
def test_embed_renew_and_local(proj, embedded, surface):
    bc.allow_backend(proj, "e", ["probe", "research"], embedding=True)
    _with_sets(proj)
    _set(proj, "e", model="emb-2")
    if surface[0] == "cli":
        r = cli(proj, *surface[1:])
        assert r.exit_code == 2, r.output
        out = " ".join(r.output.split())
    else:
        _, method, path, params = surface
        client = TestClient(create_app(_reload(proj)))
        r = getattr(client, method)(path, params=params)
        assert r.status_code == 403, r.text
        d = r.json()["detail"]["backend_consent_required"]
        assert d["kind"] == "embedding" and d["backend"] == "e"
        out = " ".join(d["changed"])
    assert "the model changed from emb-1 to emb-2" in out
    assert embedded == []
    # a local sentence-transformers model asks nothing and gets no consent entry
    p = _reload(proj)
    st = EmbeddingConfig(name="st", kind="sentence_transformers", model="all-MiniLM-L6-v2")
    p.config.embeddings.append(st)
    assert bc.require_backend_consent(p, st, "research", embedding=True)["needs"] is False
    with pytest.raises(ValueError, match="needs no consent"):
        bc.allow_backend(p, "st", ["research"], embedding=True)
    assert "st" not in p.config.embedding_consent


def test_rank_validate_never_asks(proj, embedded):
    r = cli(proj, "rank", "validate")
    assert r.exit_code != 2 and "consent" not in r.output, r.output
    r = TestClient(create_app(_reload(proj))).get("/api/rank/validate")
    assert r.status_code != 403 and "backend_consent_required" not in r.text
    assert embedded == []


# ----------------------------------------------------------------- status without a request (C3)
def _cloud_ollama(monkeypatch) -> ProviderConfig:
    """Ollama on 127.0.0.1 whose tag list marks the model remote (it runs on ollama.com)."""
    from wider.core import detect

    tags = {"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "remote_host": "https://ollama.com:443"}]}
    monkeypatch.setattr(detect, "http_get", lambda url, timeout=detect.HTTP_TIMEOUT_S: (200, tags))
    detect._ROUTING_CACHE.clear()
    return ProviderConfig(name="o", kind="ollama", model="qwen3:8b", base_url="http://127.0.0.1:11434")


def test_status_without_probe_keeps_consent_unverified(proj, monkeypatch):
    from tests.unit.helpers import no_requests
    from wider.core.backends import probe_key

    proj.config.providers.append(_cloud_ollama(monkeypatch))
    proj.save()
    row = bc.allow_backend(_reload(proj), "o", ["probe", "research"])
    assert row["locality"] == "cloud" and row["operations"] == ["probe", "research"]
    seen = no_requests(monkeypatch)
    # the locality assessed at consent is kept: listed as given
    s = bc.status(_reload(proj), "o", probe=False)[0]
    assert s["operations"] == ["probe", "research"] and not s["stale"] and s["needs_consent"] is True
    # a consent given before localities were kept: no check yet, so listed but not verified
    p = _reload(proj)
    p.store.execute("DELETE FROM meta WHERE key=?", (probe_key("llm", "o"),))
    s = bc.status(_reload(proj), "o", probe=False)[0]
    assert s["operations"] == ["probe", "research"] and s["verified"] is False and not s["stale"]
    assert s["locality_label"] == "unknown, press Check" and s["needs_consent"] is True
    r = TestClient(create_app(_reload(proj))).get("/api/providers/consent")
    o = next(x for x in r.json() if x["name"] == "o")
    assert o["verified"] is False and o["operations"] == ["probe", "research"]
    assert seen == []


def test_status_without_probe_asks_for_check(proj, monkeypatch):
    from tests.unit.helpers import no_requests

    proj.config.providers.append(_local_ollama(monkeypatch))
    proj.save()
    seen = no_requests(monkeypatch)
    s = bc.status(_reload(proj), "o", probe=False)[0]
    assert s["locality"] == "unknown" and s["locality_label"] == "unknown, press Check"
    assert s["needs_consent"] is not True and s["operations"] == [] and not s["stale"]
    assert seen == []


def test_embedding_consent_kept_without_probe(proj, monkeypatch):
    from tests.unit.helpers import no_requests

    proj.config.embeddings.append(EmbeddingConfig(name="oe", kind="ollama", model="nomic-embed-text",
                                                  base_url="http://10.0.0.5:11434"))
    proj.save()
    row = bc.allow_backend(_reload(proj), "oe", ["probe"], embedding=True)   # assessed with the fresh check
    assert row["needs_consent"] is True and row["operations"] == ["probe"]
    seen = no_requests(monkeypatch)
    s = bc.status(_reload(proj), "oe", embedding=True, probe=False)[0]
    assert s["operations"] == ["probe"] and not s["stale"] and s["needs_consent"] is True
    r = TestClient(create_app(_reload(proj))).get("/api/embeddings/consent")
    assert next(x for x in r.json() if x["name"] == "oe")["operations"] == ["probe"]
    assert seen == []


# ----------------------------------------------------------------- Ask WIDER (guide-assistant D8)
def test_assistant_needs_own_consent(proj, sent):
    """A backend allowed for research only still needs consent for `assistant`: the refusal names what the
    assistant sends and carries the fingerprint the dialog sends back; `wider providers allow a --for assistant`
    stores it and later checks pass. (The CLI exit 2 and the route's 403 are pinned in test_cli_assistant and
    test_assistant_routes, with the commands.)"""
    bc.allow_backend(proj, "a", ["research"])
    p = _reload(proj)
    with pytest.raises(bc.BackendConsentRequired) as ei:
        bc.require_backend_consent(p, p.provider("a"), "assistant")
    d = ei.value.payload()["backend_consent_required"]
    assert d["operation"] == "assistant" and d["sent"] == bc.SENT["assistant"]
    assert d["fingerprint"] == bc.assess(p.provider("a"))["fingerprint"]
    assert "answer questions in Ask WIDER" in str(ei.value) and "--for assistant" in str(ei.value)
    assert sent == []
    r = cli(proj, "providers", "allow", "a", "--for", "assistant")
    assert r.exit_code == 0, r.output
    p = _reload(proj)
    assert p.config.backend_consent["a"].operations == ["research", "assistant"]
    for _ in range(2):
        assert bc.require_backend_consent(p, p.provider("a"), "assistant")["needs"] is True
    # the embedding backend has its own `assistant` operation and texts
    with pytest.raises(bc.BackendConsentRequired) as ei:
        bc.require_backend_consent(p, p.embedding("e"), "assistant", embedding=True)
    assert ei.value.sent == bc.EMBED_SENT["assistant"] and "no project data" in ei.value.sent
    help_text = " ".join(cli(proj, "providers", "allow", "--help").output.split())
    assert "assistant" in help_text and "assistant" in " ".join(cli(proj, "embed", "revoke", "--help").output.split())


def test_revoke_assistant_keeps_other_operations(proj, sent):
    bc.allow_backend(proj, "a", ["research", "assistant"])
    assert cli(proj, "providers", "revoke", "a", "--for", "assistant").exit_code == 0
    p = _reload(proj)
    assert p.config.backend_consent["a"].operations == ["research"]
    with pytest.raises(bc.BackendConsentRequired):
        bc.require_backend_consent(p, p.provider("a"), "assistant")
    assert bc.require_backend_consent(p, p.provider("a"), "research")["needs"] is True


def test_allow_once_checks_fingerprint(proj, sent):
    """The one-question allowance holds inside its block only, for the fingerprint the dialog showed, stores nothing,
    and is refused once the backend's address changed after the dialog."""
    p = _reload(proj)
    cfg = p.provider("a")
    shown = bc.assess(cfg)["fingerprint"]
    yaml_before = (proj.root / "project.yaml").read_text()
    with bc.allow_once(cfg, "assistant", shown):
        assert bc.require_backend_consent(p, cfg, "assistant")["needs"] is True
        with pytest.raises(bc.BackendConsentRequired):   # the allowance is for `assistant` only
            bc.require_backend_consent(p, cfg, "research")
    with pytest.raises(bc.BackendConsentRequired):       # and for this one question only
        bc.require_backend_consent(p, cfg, "assistant")
    assert (proj.root / "project.yaml").read_text() == yaml_before
    _set(proj, "a", base_url="https://other.example.invalid/v1")
    p = _reload(proj)
    with bc.allow_once(p.provider("a"), "assistant", shown), pytest.raises(bc.BackendConsentRequired):
        bc.require_backend_consent(p, p.provider("a"), "assistant")
    with bc.allow_once(p.provider("a"), "assistant"):    # --allow-once: the current fingerprint
        assert bc.require_backend_consent(p, p.provider("a"), "assistant")["needs"] is True
    assert bc.parse_once(f"llm:my:odd:name:assistant:{shown}, embedding:e:research:x, junk") == [("llm", "my:odd:name", shown)]
    assert sent == []
