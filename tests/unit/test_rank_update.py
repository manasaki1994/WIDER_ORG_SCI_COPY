"""ui-redesign D5 (owner, round 6): "Update ranking" (`wider rank update`, POST /api/jobs/rank-update) checks whether
the embeddings match the current texts, says what re-embedding sends, where and at what cost, asks consent unless
given, re-embeds only the changed texts and ranks; a failure adds no version; the older embeddings stay available and
keep "needs review"."""
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_rank_versions import add_abstracts
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core.backend_consent import allow_backend, revoke_backend
from wider.core.models import EmbeddingConfig, ProviderConfig
from wider.core.project import Project
from wider.core.stages import stage_states
from wider.embeddings.fake import FakeEmbedder
from wider.rank import corpus as corpus_mod
from wider.rank.corpus import embed_corpus
from wider.rank.score import rank
from wider.sample.design import draw_development, draw_probability
from wider.ui.app import create_app

runner = CliRunner()
SENT: list[str] = []


class CountingEmbedder(FakeEmbedder):
    """The fake vectors behind a remote configuration; counts every text sent, fails when ``FAIL`` is set."""
    FAIL = False

    def _embed_batch(self, texts):
        if CountingEmbedder.FAIL:
            raise RuntimeError("the embedding backend answered 503")
        SENT.extend(texts)
        return super()._embed_batch(texts)


@pytest.fixture
def remote(tmp_path, monkeypatch):
    from wider.core.models import SamplingBand
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all

    SENT.clear()
    CountingEmbedder.FAIL = False
    monkeypatch.setattr(corpus_mod, "make_embedder", lambda cfg, cache_path=None: CountingEmbedder(
        cfg, __import__("wider.embeddings.cache", fromlist=["EmbeddingCache"]).EmbeddingCache(cache_path)))
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="remote", kind="openai_compat", model="emb-x", dim=64,
                                           base_url="https://emb.example.test/v1", api_key_env="EMB_KEY",
                                           price_input_per_mtok=0.02)]
    p.config.default_embedding = "remote"
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.config.default_provider = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=6, n=None), SamplingBand(name="S2", start=7, end=None, n=6)]
    p.save()
    ris = []
    for i in range(30):
        topic = "employee voice speaking up concerns supervisors" if i % 3 == 0 else "ocean plankton temperature bloom"
        ris.append(f"TY  - JOUR\nTI  - Paper {i} about {topic}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - {topic} "
                   f"{'extra words ' * 10}\nER  -\n")
    (tmp_path / "a.ris").write_text("\n".join(ris))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    allow_backend(p, "remote", ["research"], embedding=True)
    embed_corpus(p)
    anchor = " ".join(["employee voice speaking up concerns supervisors managers upward communication"] * 4)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": anchor}}, origin="manual")
    rank(p, top_n=1)
    draw_probability(p)
    draw_development(p, cluster_max_rank=20, near_miss_max_rank=20)
    SENT.clear()
    return p


def _versions(p):
    return [(r["name"], r["version"]) for r in p.store.query("SELECT name, version FROM rankings ORDER BY ranking_id")]


def _rank_review(p):
    return next(s for s in stage_states(p) if s["key"] == "rank")["needs_review"]


def test_update_reembeds_after_consent(remote):
    """Scenario "Abstracts changed": the page first says the 3 changed texts go to the backend, where and at what
    cost, and asks consent unless given; on Allow it embeds those 3 (the rest from the cache) and adds version 2,
    which does not need review; on Cancel nothing is sent; `--yes` confirms the cost note only."""
    p = remote
    add_abstracts(p)
    revoke_backend(p, "remote", embedding=True)
    c = TestClient(create_app(p))
    j = c.post("/api/jobs/rank-update").json()
    assert j["confirm_needed"] and j["changed"] == 3 and "job_id" not in j
    assert "sends 3 changed texts" in j["notice"] and "emb.example.test" in j["notice"]
    assert "Estimated cost" in j["notice"] and "WIDER asks before sending" in j["notice"]
    assert "validation and development keep version 1" in j["keep_note"]
    # Cancel: the page asks no further; nothing is sent and no version is added
    assert SENT == [] and len(_versions(p)) == 1
    # the CLI: --yes confirms the cost note, the missing consent still refuses before sending
    p.close()
    r = runner.invoke(cli_app, ["rank", "update", "--yes", "--dir", str(p.root)])
    assert r.exit_code != 0 and "needs your consent" in " ".join(r.output.split())
    r = runner.invoke(cli_app, ["rank", "update", "--dir", str(p.root)])   # no --yes, no terminal: nothing sent
    assert r.exit_code == 1 and "rerun with --yes" in " ".join(r.output.split())
    p = Project.load(p.root)
    assert SENT == [] and len(_versions(p)) == 1
    # Allow: the confirmed route asks consent first (403, no job), then embeds the 3 texts and adds version 2
    c = TestClient(create_app(p))
    refused = c.post("/api/jobs/rank-update?confirm=true")
    assert refused.status_code == 403 and SENT == []
    allow_backend(p, "remote", ["research"], embedding=True)
    p.close()
    r = runner.invoke(cli_app, ["rank", "update", "--yes", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert len(SENT) == 3
    p = Project.load(p.root)
    assert [v for _, v in _versions(p)] == [1, 2]
    assert p.store.get_meta("default_ranking").endswith("@v2")
    review = next(s for s in stage_states(p) if s["key"] == "rank")["review"]
    assert [i["version"] for i in review] == [1]   # the samples' version still needs review; version 2 does not
    p.close()


def test_update_route_starts_a_job_when_embeddings_match(remote):
    """With embeddings that match the texts, "Update ranking" starts the ranking job at once (nothing to send)."""
    p = remote
    c = TestClient(create_app(p))
    j = c.post("/api/jobs/rank-update").json()
    assert "job_id" in j and not j.get("confirm_needed")


def test_older_embeddings_and_failure(remote):
    """Scenario "Older embeddings and failures": --use-older-embeddings reuses or adds a version that still needs
    review and says so; a failing embedding adds no version and changes nothing, and says what failed."""
    p = remote
    add_abstracts(p)
    before = _versions(p)
    p.close()
    r = runner.invoke(cli_app, ["rank", "update", "--use-older-embeddings", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    assert "older embeddings" in out and "reused" in out
    p = Project.load(p.root)
    assert _versions(p) == before and SENT == []
    assert _rank_review(p) == "the records or their abstracts changed since this ranking"
    # a changed setting with the older embeddings adds a version, which still needs review
    p.config.protocol = p.config.protocol.set("corpus.thin_words", 3)
    p.save()
    p.close()
    r = runner.invoke(cli_app, ["rank", "update", "--use-older-embeddings", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    p = Project.load(p.root)
    assert [v for _, v in _versions(p)] == [1, 2]
    review = next(s for s in stage_states(p) if s["key"] == "rank")["review"]
    assert [i["version"] for i in review] == [1]   # the samples' version
    from wider.rank.versions import current_parts, input_hash
    v2 = p.store.one("SELECT * FROM rankings WHERE version=2")
    assert v2["input_hash"] != input_hash(current_parts(p, v2["anchors_version"]))   # made from older embeddings
    samples = [tuple(x) for x in p.store.query("SELECT name, ranking_id FROM samples ORDER BY sample_id")]
    CountingEmbedder.FAIL = True
    p.close()
    r = runner.invoke(cli_app, ["rank", "update", "--yes", "--dir", str(p.root)])
    assert r.exit_code == 1
    out = " ".join(r.output.split())
    assert "503" in out and "no version was added" in out
    p = Project.load(p.root)
    assert [v for _, v in _versions(p)] == [1, 2]
    assert [tuple(x) for x in p.store.query("SELECT name, ranking_id FROM samples ORDER BY sample_id")] == samples
    p.close()


def test_update_keeps_the_chosen_top_n(remote):
    """Spec-checker F1: "Update ranking" ranks with the chosen ranking's settings; after `rank run --top-n` chose
    another top-N, the update keeps that ranking (reuse or a new version of it), never the protocol default."""
    from wider.construct import artifacts

    p = remote
    _, data = artifacts.load(p, "anchors")
    row = artifacts.save_new_version(p, "anchors", {"anchors": {**data["anchors"], "other_cell": " ".join(
        ["ocean plankton temperature bloom"] * 10)}}, origin="manual")
    p.close()
    r = runner.invoke(cli_app, ["rank", "run", "--anchors", str(row["version"]), "--top-n", "1", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    chosen = q.store.get_meta("default_ranking")
    assert chosen.endswith("__top1")
    q.close()
    r = runner.invoke(cli_app, ["rank", "update", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    assert q.store.get_meta("default_ranking") == chosen
    q.close()


def _wait(c, job_id, timeout=20):
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_update_route_cancel_and_job_failure(remote):
    """Spec-checker F5: on the page, Cancel after the cost note sends nothing (the route without ``confirm`` never
    starts a job, however often it is asked); a confirmed job whose embedding fails ends "failed" with what failed,
    adds no version, keeps the default ranking and leaves every sample on its version."""
    p = remote
    add_abstracts(p)
    before = _versions(p)
    default = p.store.get_meta("default_ranking")
    samples = [tuple(x) for x in p.store.query("SELECT name, ranking_id FROM samples ORDER BY sample_id")]
    c = TestClient(create_app(p))
    for _ in range(2):   # the cost note asked twice, cancelled both times
        j = c.post("/api/jobs/rank-update").json()
        assert j["confirm_needed"] and "job_id" not in j
    assert c.get("/api/jobs").json() == [] and SENT == []
    CountingEmbedder.FAIL = True
    j = c.post("/api/jobs/rank-update?confirm=true").json()
    assert "job_id" in j, j
    done = _wait(c, j["job_id"])
    assert done["status"] == "failed" and "503" in done["error"]
    assert _versions(p) == before and p.store.get_meta("default_ranking") == default
    assert [tuple(x) for x in p.store.query("SELECT name, ranking_id FROM samples ORDER BY sample_id")] == samples
    assert _rank_review(p) == "the records or their abstracts changed since this ranking"
