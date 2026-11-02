import numpy as np

from wider.core.models import EmbeddingConfig
from wider.embeddings import make_embedder
from wider.embeddings.cache import EmbeddingCache
from wider.embeddings.registry import probe


def test_fake_embedder_normalised_and_deterministic(tmp_path):
    e = make_embedder(EmbeddingConfig(name="f", kind="fake", model="fake", dim=32))
    m = e.embed(["team meeting manager", "team meeting manager error", "plankton ocean"])
    assert m.shape == (3, 32) and m.dtype == np.float32
    assert np.allclose(np.linalg.norm(m, axis=1), 1.0)
    assert m[0] @ m[1] > m[0] @ m[2]
    m2 = e.embed(["team meeting manager"])
    assert np.allclose(m[0], m2[0])


def test_cache_roundtrip_and_truncation(tmp_path):
    cache = tmp_path / "c.sqlite"
    e = make_embedder(EmbeddingConfig(name="f", kind="fake", model="fake", dim=16, max_words=2), cache)
    a = e.embed(["one two three four", "x y"])
    assert e.cache.count() == 2
    b = e.embed(["one two three four"])  # served from cache
    assert np.allclose(a[0], b[0])
    c = e.embed(["one two"])  # truncation to 2 words makes this the same text
    assert np.allclose(a[0], c[0])
    assert e.embed([]).shape[0] == 0


def test_probe_fake():
    r = probe(EmbeddingConfig(name="f", kind="fake", model="fake", dim=64))
    assert r["ok"] and r["dim"] == 64 and r["sanity"]


def test_cache_count_by_model(tmp_path):
    c = EmbeddingCache(tmp_path / "c.sqlite")
    c.put_many("m1", ["a"], np.ones((1, 4), dtype=np.float32))
    assert c.count("m1") == 1 and c.count("m2") == 0
    assert c.get_many("m1", ["a", "b"])[1] is None


def test_cached_local_model_loads_offline_unless_download_requested(monkeypatch):
    """A cached sentence-transformers model is loaded with local_files_only; only the download path may go online."""
    import sys
    import types

    import numpy as np

    from wider.core.models import EmbeddingConfig
    from wider.embeddings.sentence_transformers import SentenceTransformersEmbedder

    seen = []

    class FakeST:
        def __init__(self, key, **kw):
            seen.append(kw.get("local_files_only"))

        def encode(self, texts, **kw):
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.setattr(SentenceTransformersEmbedder, "_models", {})
    monkeypatch.setattr(SentenceTransformersEmbedder, "is_downloaded", lambda self: True)
    cfg = EmbeddingConfig(name="t", kind="sentence_transformers", model="org/cached-model")
    SentenceTransformersEmbedder(cfg).embed(["a"])
    assert seen == [True]
    SentenceTransformersEmbedder._models.clear()
    e = SentenceTransformersEmbedder(cfg)
    e.allow_download = True
    e.embed(["a"])
    assert seen == [True, False]


def test_local_model_halves_batch_on_out_of_memory(monkeypatch):
    import sys
    import types

    import numpy as np

    from wider.core.models import EmbeddingConfig
    from wider.embeddings.sentence_transformers import SentenceTransformersEmbedder

    sizes = []

    class FakeST:
        def __init__(self, key, **kw):
            pass

        def encode(self, texts, batch_size=64, **kw):
            sizes.append(batch_size)
            if batch_size > 4:
                raise RuntimeError("MPS backend out of memory (MPS allocated: 17.01 GiB)")
            return np.ones((len(texts), 4), dtype=np.float32)

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=FakeST))
    monkeypatch.setattr(SentenceTransformersEmbedder, "_models", {})
    monkeypatch.setattr(SentenceTransformersEmbedder, "is_downloaded", lambda self: True)
    e = SentenceTransformersEmbedder(EmbeddingConfig(name="t", kind="sentence_transformers", model="org/m", batch_size=16))
    assert e.embed(["a", "b"]).shape == (2, 4)
    assert sizes == [16, 8, 4]
    e.embed(["c"])
    assert sizes[-1] == 4  # the reduced size sticks for the rest of the run


# ------------------------------------------------------------------ C2 section 7: retries, usage, sanity
import httpx  # noqa: E402
import pytest  # noqa: E402

from wider.embeddings.base import EmbeddingError  # noqa: E402


class _Answers:
    """A fake ``httpx.post`` for the OpenAI-compatible embedder: plays ``plan`` (a status or an exception per call),
    then answers 200 with one vector per text and ``usage.prompt_tokens`` = number of words."""

    def __init__(self, plan=()):
        self.plan, self.calls = list(plan), 0

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        req = httpx.Request("POST", url)
        step = self.plan.pop(0) if self.plan else 200
        if isinstance(step, Exception):
            raise step
        if step != 200:
            return httpx.Response(step, text="synthetic error", request=req)
        texts = json["input"]
        data = [{"index": i, "embedding": [1.0, float(len(t) % 7), 0.5]} for i, t in enumerate(texts)]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": sum(len(t.split()) for t in texts)}},
                              request=req)


def _api_cfg(**kw):
    return EmbeddingConfig(name="api", kind="openai_compat", model="m", base_url="http://127.0.0.1:9/v1", **kw)


def test_embedding_retry_and_usage(tmp_path, monkeypatch):
    from wider.core.project import Project
    from wider.core.usage import track

    sleeps = []
    monkeypatch.setattr("wider.embeddings.base._sleep", sleeps.append)
    fake = _Answers([httpx.ReadTimeout("slow"), 503])
    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", fake)
    p = Project.init(tmp_path / "r")
    with track(p, "rank"):
        m = make_embedder(_api_cfg(price_input_per_mtok=1.0)).embed(["one two", "three"])
    assert m.shape == (2, 3) and fake.calls == 3 and len(sleeps) == 2
    rows = p.store.query("SELECT ok, prompt_tokens, completion_tokens, cost_usd, kind, stage FROM llm_usage ORDER BY usage_id")
    assert [r["ok"] for r in rows] == [0, 0, 1]
    assert rows[-1]["prompt_tokens"] == 3 and rows[-1]["completion_tokens"] == 0
    assert rows[-1]["cost_usd"] == pytest.approx(3 / 1_000_000) and rows[-1]["kind"] == "embedding:openai_compat"
    assert {r["stage"] for r in rows} == {"rank"}
    # three failures in a row: the error is raised after the third try
    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", _Answers([500, 500, 500, 200]))
    with pytest.raises(EmbeddingError):
        make_embedder(_api_cfg()).embed(["x"])


def test_embedding_auth_error_not_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr("wider.embeddings.base._sleep", sleeps.append)
    fake = _Answers([401])
    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", fake)
    with pytest.raises(EmbeddingError) as e:
        make_embedder(_api_cfg()).embed(["x"])
    assert fake.calls == 1 and not sleeps and "401" in str(e.value) and not e.value.retryable


def test_probe_fails_sanity(monkeypatch):
    from wider.embeddings.registry import PROBE_TEXTS, SANITY_ERROR

    def post(url, json=None, headers=None, timeout=None):
        # the related pair points apart, the unrelated sentence points the same way as the first
        vec = {PROBE_TEXTS[0]: [1.0, 0.0], PROBE_TEXTS[1]: [0.0, 1.0]}
        data = [{"index": i, "embedding": vec.get(t, [1.0, 0.0])} for i, t in enumerate(json["input"])]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 1}},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", post)
    r = probe(_api_cfg())
    assert r["ok"] is False and r["sanity"] is False and r["error"] == SANITY_ERROR and r["dim"] == 2


def test_corpus_embeddings_logged_under_rank(tmp_path, monkeypatch):
    from tests.unit.test_rank import _ris
    from wider.core.project import Project
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus

    fake = _Answers()
    monkeypatch.setattr("wider.embeddings.openai_compat.httpx.post", fake)
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [_api_cfg()]
    p.config.default_embedding = "api"
    p.save()
    (tmp_path / "a.ris").write_text(_ris(6))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    res = embed_corpus(p)
    rows = p.store.query("SELECT stage, prompt_tokens, completion_tokens FROM llm_usage")
    assert rows and {r["stage"] for r in rows} == {"rank"} and all(r["completion_tokens"] == 0 for r in rows)
    assert sum(r["prompt_tokens"] for r in rows) > 0
    # again for the same texts, with the saved matrix gone: every text comes from the embedding cache, no new row
    p.path("embeddings", f"{res['set_name']}.npy").unlink()
    p.store.execute("UPDATE embedding_sets SET text_hash='changed'")
    before, calls = len(rows), fake.calls
    embed_corpus(p)
    assert p.store.scalar("SELECT COUNT(*) FROM llm_usage") == before and fake.calls == calls
