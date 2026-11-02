"""Configuration comparison on the development sample (change screening-config-comparison, design D1-D7): stored
comparisons with their own tagged runs, figures from the final labels, consistency over repeats, no winner and no
corpus estimate. Synthetic topic-neutral records, the fake embedding backend and the fake provider only."""
import re

import pytest

from wider.construct import artifacts
from wider.core.models import EmbeddingConfig, ProviderConfig, SamplingBand
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.sample.design import draw_development, draw_probability
from wider.sample.labels import label, sample_by_name
from wider.screen.codebook import build, save_prompt_artifact
from wider.screen.engine import screen
from wider.screen.evaluate import evaluate

CODEBOOK = {
    "question": "Which records report topic alpha?",
    "target_description": "Records whose abstract reports a measurement of topic alpha.",
    "elements": [
        {"id": "e1", "name": "Topic alpha measured", "rule": "The abstract reports topic alpha."},
        {"id": "e2", "name": "Outcome reported", "rule": "The abstract reports an outcome."},
    ],
    "exclusion_reasons": [{"code": "a", "text": "no topic alpha", "element": "e1"}, {"code": "b", "text": "no outcome", "element": "e2"}],
    "checks": [{"key": "secondary", "question": "Is topic alpha only a secondary outcome?", "if_yes": "maybe"}],
    "edge_cases": [{"key": "overview", "case": "Overviews of topic alpha", "default": "maybe"}],
    "labels": [],
}
ALPHA = "topic alpha signal measured outcome reported"
BETA = "topic beta signal measured outcome reported"
DEV = "sample:development"


def record_no(req) -> int:
    """The record number in the synthetic title ("Record 07 on ...")."""
    return int(re.search(r"Record (\d+)", req.user).group(1))


def decide(req, decision=None):
    """A schema-shaped answer: include for topic alpha, exclude otherwise, unless ``decision`` is given."""
    hit = "topic alpha" in req.user
    dec = decision or ("include" if hit else "exclude")
    return {"summary": "s", "e1": "present" if dec != "exclude" else "absent",
            "e1_quote": "topic alpha signal measured" if hit else "topic beta signal measured",
            "e2": "present", "e2_quote": "outcome reported", "secondary": False,
            "target_fit": "core" if dec != "exclude" else "unrelated", "edge_case": "none", "decision": dec,
            "exclusion_reason": "none" if dec != "exclude" else "a", "missing_for_maybe": ""}


def default_responder(counter, cfg):
    """Topic alpha is included, except records numbered 0 mod 6 (alpha records the model misses)."""
    def respond(req):
        counter["calls"] += 1
        counter.setdefault("by_model", {}).setdefault(cfg.model, 0)
        counter["by_model"][cfg.model] += 1
        if record_no(req) % 6 == 0:
            return decide(req, "exclude")
        return decide(req)
    return respond


_FAKE_INIT = fakemod.FakeProvider.__init__


def use_responder(monkeypatch, counter, factory=default_responder):
    """Every FakeProvider answers through ``factory(counter, cfg)``, so a test can decide per backend, record, seed
    (repeat) and temperature."""
    orig = _FAKE_INIT
    monkeypatch.setattr(fakemod.FakeProvider, "__init__",
                        lambda self, cfg, responder_=None: orig(self, cfg, factory(counter, cfg)))


def build_project(root) -> Project:
    """40 synthetic records (every third on topic alpha), ranked and with both samples drawn; one fake backend `fake`
    with seed 0, so a repeat's seed offset reaches the responder unchanged."""
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(root / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake-m1", seed=0))
    p.config.default_provider = "fake"
    p.config.bands = [SamplingBand(name="S1", start=1, end=6, n=None), SamplingBand(name="S2", start=7, end=None, n=6)]
    p.save()
    ris = []
    for i in range(40):
        topic = "alpha" if i % 3 == 0 else "beta"
        body = ALPHA if topic == "alpha" else BETA
        ris.append(f"TY  - JOUR\nTI  - Record {i:02d} on topic {topic}\nPY  - 2020\nDO  - 10.1000/r{i}\n"
                   f"AB  - {body} {'filler words ' * 10}\nER  -\n")
    (root / "a.ris").write_text("\n".join(ris))
    add_file(p, "scopus", root / "a.ris")
    import_all(p)
    dedup_run(p)
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"alpha_cell": " ".join([ALPHA] * 8)}}, origin="manual")
    rank(p, top_n=1)
    artifacts.save_new_version(p, "codebook", CODEBOOK, origin="manual")
    save_prompt_artifact(p, build(p))
    draw_probability(p)
    draw_development(p, cluster_max_rank=20, near_miss_max_rank=20)
    return p


def dev_rows(p, sample="development"):
    s = sample_by_name(p, sample)
    return [dict(r) for r in p.store.query(
        "SELECT sr.record_id, sr.pool, r.title FROM sample_rows sr JOIN records r ON r.record_id=sr.record_id "
        "WHERE sr.sample_id=? ORDER BY sr.record_id", (s["sample_id"],))]


def label_all(p, sample="development", reviewer="alice", skip=()):
    """The true topic as the reviewer's label: include for topic alpha, exclude otherwise."""
    for r in dev_rows(p, sample):
        if r["record_id"] in skip:
            continue
        label(p, sample, r["record_id"], reviewer, "include" if "topic alpha" in r["title"] else "exclude",
              reason="" if "topic alpha" in r["title"] else "a")


@pytest.fixture(scope="session")
def _template(tmp_path_factory):
    """Built once per test session (no model call is made while building); each test works on a copy."""
    root = tmp_path_factory.mktemp("compare_template")
    build_project(root)
    return root / "r"


def copy_project(template, tmp_path) -> Project:
    import shutil
    shutil.copytree(template, tmp_path / "r")
    return Project.find(tmp_path / "r")


@pytest.fixture
def cproject(_template, tmp_path, monkeypatch):
    """A copy of the synthetic project with the default responder; returns (project, counter)."""
    counter = {"calls": 0}
    use_responder(monkeypatch, counter)
    return copy_project(_template, tmp_path), counter


# ---------------------------------------------------------------------------------------------- 1.1 scoring (D2)
def test_evaluate_counts_reused_evaluations(cproject):
    """Scenario "A run with reused evaluations": a run whose evaluations are all reused reports the same misses as
    the run it reused from."""
    p, counter = cproject
    label_all(p)
    first = screen(p, DEV, pass_name="A")
    calls = counter["calls"]
    ids = ",".join(str(r["record_id"]) for r in dev_rows(p))
    second = screen(p, f"ids:{ids}", pass_name="A", reuse_evaluations=True)
    assert counter["calls"] == calls, "every evaluation should be reused"
    assert second["run_id"] != first["run_id"]
    a, b = evaluate(p, first["run_id"]), evaluate(p, second["run_id"])
    assert a["misses"] > 0 and a["n"] > 0
    for k in ("n", "misses", "false_includes", "false_maybes", "agree", "must_not_miss"):
        assert a[k] == b[k], k
    assert [r["record_id"] for r in a["disagreements"]] == [r["record_id"] for r in b["disagreements"]]


# ---------------------------------------------------------------------------------------------- 1.2 run tag (D1)
def test_run_id_tag(tmp_path):
    """A tagged run id differs per repeat and configuration; an untagged one is byte-identical to the ids runs always
    had (plain and protocol)."""
    from wider.screen.engine import _run_id, comparison_tag, is_comparison_run

    p = None
    assert _run_id(p, "screen", "A", 1, "fake", "sample:development") == "screen__sample-development__p1__fake__A"
    assert (_run_id(p, "screen", "B", 2, "fake", "sample:development", "main", 2)
            == "screen__sample-development__p2__fake__B__main.s2")
    long_ids = "ids:" + ",".join(str(i) for i in range(1, 30))
    assert _run_id(p, "screen", "A", 1, "fake", long_ids) == "screen__" + long_ids.replace(":", "-").replace(",", "_")[:40] + "__p1__fake__A"
    tagged = {_run_id(p, "screen", "A", 1, "fake", long_ids, tag=comparison_tag("cmp1", i, k)) for i in (1, 2) for k in (1, 2, 3)}
    assert len(tagged) == 6 and all(is_comparison_run(t) for t in tagged)
    assert _run_id(p, "screen", "A", 1, "fake", long_ids, tag=comparison_tag("cmp1", 2, 3)).endswith("__p1__fake__A__cmp.cmp1.c2.r3")
    other = "ids:" + ",".join(str(i) for i in range(1, 31))
    assert (_run_id(p, "screen", "A", 1, "fake", long_ids, tag="cmp.x.c1.r1")
            != _run_id(p, "screen", "A", 1, "fake", other, tag="cmp.x.c1.r1"))
    assert not is_comparison_run("screen__sample-development__p1__fake__A__main.s1")


# ---------------------------------------------------------------------------------------------- helpers for group 2
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from wider.cli import app as cli_app  # noqa: E402
from wider.sample.labels import lock, unlock  # noqa: E402
from wider.screen import compare  # noqa: E402
from wider.screen.devgate import HIDDEN  # noqa: E402
from wider.ui.app import create_app  # noqa: E402

runner = CliRunner()


def cli(p, *args, input=None):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], input=input)


def api(p):
    return TestClient(create_app(Project.load(p.root)))


def fresh(p) -> Project:
    return Project.load(p.root)


def add_backend(p, name, **kw):
    kw.setdefault("kind", "fake")
    kw.setdefault("model", f"{name}-m")
    if kw["kind"] == "fake":
        kw.setdefault("seed", 0)
    p.config.providers.append(ProviderConfig(name=name, **kw))
    p.save()


def n_comparisons(p) -> int:
    return fresh(p).store.scalar("SELECT COUNT(*) FROM screen_comparisons")


def n_tagged_runs(p) -> int:
    return fresh(p).store.scalar("SELECT COUNT(*) FROM runs WHERE run_id LIKE '%__cmp.%'")


@pytest.fixture
def locked(cproject):
    """The synthetic project with a second fake backend `fake_b` (model fake-m2), the development sample labelled by
    alice and locked; returns (project, counter)."""
    p, counter = cproject
    add_backend(p, "fake_b", model="fake-m2")
    label_all(p)
    lock(p, "development")
    return p, counter


def recording_responder(counter, cfg):
    """The default decisions, and every request kept as (backend, record number, seed, temperature)."""
    base = default_responder(counter, cfg)

    def respond(req):
        counter.setdefault("reqs", []).append((cfg.name, record_no(req), req.seed, req.temperature))
        return base(req)
    return respond


# ---------------------------------------------------------------------------------------------- 2.1 refusals (D5-D7)
def test_parse_config_forms():
    assert compare.parse_config("a") == {"label": "a", "provider": "a", "prompt_version": None, "temperature": None,
                                         "temperature_text": None}
    c = compare.parse_config("a:p2@0.7")
    assert (c["provider"], c["prompt_version"], c["temperature"]) == ("a", 2, 0.7)
    assert compare.parse_config("fake_b@0")["temperature"] == 0.0
    for bad in ("", "a:2", "a@x", "a b", "a:p2:p3", "@0.7"):
        with pytest.raises(ValueError, match="for example a:p2@0.7"):
            compare.parse_config(bad)


def test_prompt_and_temperature_configurations(locked, monkeypatch):
    """Scenario "Prompt version and temperature as configurations": one backend with two prompt versions and two
    temperatures gives rows that name them; each repeat reaches the backend with its seed offset and temperature."""
    p, counter = locked
    use_responder(monkeypatch, counter, recording_responder)
    save_prompt_artifact(p, build(p), note="second version")
    res = compare.run(p, "cmp1", None, ["fake:p1", "fake:p2@0.7"], repeats=2)
    rows = res["configs"]
    assert [(r["label"], r["prompt_version"], r["temperature"]) for r in rows] == [("fake:p1", 1, 0.0),
                                                                                  ("fake:p2@0.7", 2, 0.7)]
    assert rows[1]["temperature_text"] == "0.7" and rows[0]["temperature_text"] == "0"
    n = len(dev_rows(p))
    seen = counter["reqs"]
    assert len(seen) == 2 * 2 * n
    # without @T the request leaves the temperature to the backend's own setting (0 here), as a plain run does
    assert {(s, t) for _b, _r, s, t in seen} == {(0, None), (0, 0.7), (1, None), (1, 0.7)}
    specs = [fresh(p).store.scalar("SELECT prompt_version FROM runs WHERE run_id=?", (r["run_id"],))
             for row in rows for r in row["runs"]]
    assert specs == [1, 1, 2, 2]
    for bad, msg in ((["fake:p9"], "prompt version 9 does not exist"), (["nope"], "backend nope is not configured"),
                     (["fake", "fake"], "configuration fake is given twice")):
        with pytest.raises(ValueError, match=msg):
            compare.run(p, "cmp2", None, bad)
    assert n_comparisons(p) == 1


def test_probability_sample_refused(locked):
    """Scenario "The probability sample is refused": CLI exit 1, API 400, the estimate too; nothing stored, no call."""
    p, counter = locked
    lock(p, "validation")
    msg = "'validation' is the probability sample; a comparison there would select the model on the recall set"
    r = cli(p, "screen", "compare", "run", "c1", "--sample", "validation", "--config", "fake")
    assert r.exit_code == 1 and msg in " ".join(r.output.split())
    r = cli(p, "screen", "compare", "run", "c1", "--sample", "validation", "--config", "fake", "--estimate-only")
    assert r.exit_code == 1
    c = api(p)
    body = {"name": "c1", "sample": "validation", "configs": ["fake"]}
    for path in ("/api/screen/compare", "/api/screen/compare/estimate", "/api/jobs/compare"):
        resp = c.post(path, json=body)
        assert resp.status_code == 400 and resp.json()["detail"] == msg, (path, resp.text)
    assert n_comparisons(p) == 0 and counter["calls"] == 0 and n_tagged_runs(p) == 0


def test_unlocked_sample_refused(cproject):
    """Scenario "An unlocked sample is refused" (D7): CLI exit 1, both POST routes 409, nothing stored, no call; the
    estimate still answers; after the lock the same command runs; an unlock between two runs refuses the second."""
    p, counter = cproject
    label_all(p)
    msg = "sample development is not locked; lock it before comparing: wider sample lock development"
    args = ("screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "2")
    r = cli(p, *args)
    assert r.exit_code == 1 and msg in " ".join(r.output.split()), r.output
    c = api(p)
    body = {"name": "cmp1", "configs": ["fake"], "repeats": 2}
    for path in ("/api/screen/compare", "/api/jobs/compare"):
        resp = c.post(path, json=body)
        assert resp.status_code == 409 and resp.json()["detail"] == msg, (path, resp.text)
    assert c.get("/api/jobs").json() in ([], {"jobs": []})
    assert n_comparisons(p) == 0 and counter["calls"] == 0 and n_tagged_runs(p) == 0
    r = cli(p, *args, "--estimate-only")
    n = len(dev_rows(p))
    assert r.exit_code == 0 and f"Comparison cmp1: {n} records, 1 configuration × 2 repeats = 2 runs" in r.output
    assert c.post("/api/screen/compare/estimate", json=body).status_code == 200
    assert n_comparisons(p) == 0 and counter["calls"] == 0
    r = cli(p, "sample", "lock", "development")
    assert r.exit_code == 0, r.output
    r = cli(p, *args)
    assert r.exit_code == 0, r.output
    assert n_comparisons(p) == 1 and counter["calls"] == 2 * len(dev_rows(p))
    calls = counter["calls"]
    unlock(p, "development")
    r = cli(p, *args)
    assert r.exit_code == 1 and msg in " ".join(r.output.split())
    assert api(p).post("/api/screen/compare", json=body).status_code == 409
    lock(p, "development")
    r = cli(p, *args)
    assert r.exit_code == 0 and counter["calls"] == calls, "a finished comparison makes no call when run again"


def test_compare_needs_sample_and_config(cproject):
    """No development sample yet, and no --config: exit 1 and 400, nothing stored."""
    p, counter = cproject
    r = cli(p, "screen", "compare", "run", "c1")
    assert r.exit_code == 1 and "at least one --config is needed" in r.output
    resp = api(p).post("/api/screen/compare", json={"name": "c1", "configs": []})
    assert resp.status_code == 400 and resp.json()["detail"] == "at least one --config is needed"
    r = cli(p, "screen", "compare", "run", "c1", "--sample", "pilot", "--config", "fake")
    assert r.exit_code == 1 and "sample pilot does not exist; draw it with wider sample draw pilot" in r.output
    resp = api(p).post("/api/screen/compare/estimate", json={"name": "c1", "sample": "pilot", "configs": ["fake"]})
    assert resp.status_code == 400 and "sample pilot does not exist" in resp.json()["detail"]
    for bad in ("0", "11"):
        r = cli(p, "screen", "compare", "run", "c1", "--config", "fake", "--repeats", bad)
        assert r.exit_code == 1 and "repeats is a whole number from 1 to 10" in r.output
    assert n_comparisons(p) == 0 and counter["calls"] == 0


def test_comparison_name_without_double_underscore(locked):
    """A comparison name may not contain '__' (the run-id separator) and follows the protocol-name rule."""
    p, counter = locked
    r = cli(p, "screen", "compare", "run", "a__b", "--config", "fake")
    assert r.exit_code == 1 and "a comparison name may not contain '__'" in r.output
    resp = api(p).post("/api/screen/compare", json={"name": "a__b", "configs": ["fake"]})
    assert resp.status_code == 400 and resp.json()["detail"] == "a comparison name may not contain '__'"
    for bad in ("a.b", "a b", "<x>"):
        resp = api(p).post("/api/screen/compare", json={"name": bad, "configs": ["fake"]})
        assert resp.status_code == 400 and "protocol name" in resp.json()["detail"]
    assert n_comparisons(p) == 0 and counter["calls"] == 0


def test_rerun_resumes_and_list_is_fixed(locked):
    """Scenario "Running a comparison again": the same name and list resumes (no call for finished runs, only the
    missing records of an unfinished one); another list, repeat count or sample under the name is refused (409)."""
    p, counter = locked
    n = len(dev_rows(p))
    first = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    assert counter["calls"] == 2 * 2 * n
    again = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    assert counter["calls"] == 2 * 2 * n
    assert [r["misses"] for r in again["configs"]] == [r["misses"] for r in first["configs"]]
    run_id = first["configs"][1]["runs"][1]["run_id"]
    st = fresh(p).store
    st.execute("UPDATE runs SET status='interrupted' WHERE run_id=?", (run_id,))
    st.execute("DELETE FROM screen_calls WHERE run_id=? AND record_id IN (SELECT record_id FROM screen_calls "
               "WHERE run_id=? ORDER BY record_id LIMIT 2)", (run_id, run_id))
    compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    assert counter["calls"] == 2 * 2 * n + 2, "only the two missing records of the unfinished run are screened"
    assert fresh(p).store.scalar("SELECT status FROM runs WHERE run_id=?", (run_id,)) == "done"
    msg = "comparison cmp1 already exists with configurations fake, fake_b, 2 repeats on development"
    for configs, repeats in ((["fake"], 2), (["fake_b", "fake"], 2), (["fake", "fake_b"], 3)):
        with pytest.raises(compare.Conflict, match=msg):
            compare.run(p, "cmp1", None, configs, repeats=repeats)
        resp = api(p).post("/api/screen/compare", json={"name": "cmp1", "configs": configs, "repeats": repeats})
        assert resp.status_code == 409 and resp.json()["detail"].startswith(msg)
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake")
    assert r.exit_code == 1 and "give another list a new name" in " ".join(r.output.split())
    assert n_comparisons(p) == 1 and counter["calls"] == 2 * 2 * n + 2


def test_resume_refused_after_backend_change(locked):
    """Scenario "Resuming after a backend changed or was removed": an unfinished comparison whose backend's method
    identity changed, or whose backend is gone, is refused with 409 and no call; the figures stay readable."""
    p, counter = locked
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    calls = counter["calls"]
    fresh(p).store.execute("UPDATE runs SET status='interrupted' WHERE run_id=?", (res["configs"][1]["runs"][1]["run_id"],))
    p.config.providers[-1].model = "fake-m3"
    p.save()
    msg = "backend fake_b changed since cmp1 first ran (model fake-m2, now fake-m3); start a new comparison"
    resp = api(p).post("/api/screen/compare", json={"name": "cmp1", "configs": ["fake", "fake_b"], "repeats": 2})
    assert resp.status_code == 409 and resp.json()["detail"] == msg
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--config", "fake_b", "--repeats", "2")
    assert r.exit_code == 1 and msg in " ".join(r.output.split())
    p.config.providers = [c for c in p.config.providers if c.name != "fake_b"]
    p.save()
    with pytest.raises(compare.Conflict, match="backend fake_b is no longer configured; start a new comparison"):
        compare.run(fresh(p), "cmp1", None, ["fake", "fake_b"], repeats=2)
    # both at once, as the scenario words it: one backend changed and the other removed
    next(c for c in p.config.providers if c.name == "fake").model = "fake-m9"
    p.save()
    msg = ("backend fake changed since cmp1 first ran (model fake-m1, now fake-m9); backend fake_b is no longer "
           "configured; start a new comparison")
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--config", "fake_b", "--repeats", "2")
    assert r.exit_code == 1 and msg in " ".join(r.output.split())
    resp = api(p).post("/api/screen/compare", json={"name": "cmp1", "configs": ["fake", "fake_b"], "repeats": 2})
    assert resp.status_code == 409 and resp.json()["detail"] == msg
    assert counter["calls"] == calls
    assert api(p).get("/api/screen/compare/cmp1").status_code == 200
    shown = cli(p, "screen", "compare", "show", "cmp1", "--json")
    assert shown.exit_code == 0, shown.output
    runs = {(row["label"], r["repeat"]): r["status"] for row in json.loads(shown.output)["configs"] for r in row["runs"]}
    assert runs == {("fake", 1): "done", ("fake", 2): "done", ("fake_b", 1): "done", ("fake_b", 2): "interrupted"}


def test_compare_remote_needs_consent(locked, monkeypatch):
    """A cloud backend without consent: the CLI without a terminal exits 2 (``ensure_backend_consent``), the API
    answers 403 ``backend_consent_required``; nothing stored, no job, no call."""
    p, counter = locked
    monkeypatch.setenv("R_KEY", "synthetic-not-a-key")
    add_backend(p, "remote", kind="openai_compat", model="m1", base_url="https://llm.example.invalid/v1",
                api_key_env="R_KEY")
    r = cli(p, "screen", "compare", "run", "c1", "--config", "fake", "--config", "remote")
    assert r.exit_code == 2, r.output
    c = api(p)
    for path in ("/api/screen/compare", "/api/jobs/compare"):
        resp = c.post(path, json={"name": "c1", "configs": ["fake", "remote"]})
        assert resp.status_code == 403, resp.text
        d = resp.json()["detail"]["backend_consent_required"]
        assert d["backend"] == "remote" and d["operation"] == "research"
    assert c.get("/api/jobs").json() in ([], {"jobs": []})
    assert n_comparisons(p) == 0 and counter["calls"] == 0 and n_tagged_runs(p) == 0


@pytest.mark.parametrize("kw", [
    {"kind": "cli_agent", "command": ["agent", "{model}"]},
    {"kind": "typesafe"},
    {"kind": "openai_compat", "extra": {"omit_params": ["temperature"]}},
    {"kind": "openai_compat", "extra": {"body": {"temperature": 1}}},
    {"kind": "anthropic", "extra": {"body": {"temperature": 1}}},
    {"kind": "litellm", "extra": {"body": {"temperature": 1}}},
    {"kind": "ollama", "extra": {"options": {"temperature": 1}}},
], ids=["cli_agent", "typesafe", "omit_params", "openai_body", "anthropic_body", "litellm_body", "ollama_options"])
def test_temperature_not_sent(locked, kw):
    """Scenario "A backend that does not send a temperature": `@T` is refused through ``backends.sends_temperature``;
    without it the stored configuration says "temperature not sent by this backend"."""
    from wider.core.backends import sends_temperature

    p, counter = locked
    add_backend(p, "nt", model="m", **kw)
    assert sends_temperature(p.provider("nt")) is False
    with pytest.raises(ValueError, match="backend nt does not send a temperature; compare it without @0.70"):
        compare.estimate(p, "c1", None, ["fake", "nt@0.70"], probe=False)
    e = compare.estimate(p, "c1", None, ["fake", "nt"], probe=False)
    nt = e["configs"][1]
    assert nt["temperature_sent"] is False and nt["temperature"] is None
    assert compare.temperature_text(nt) == "temperature not sent by this backend"
    assert n_comparisons(p) == 0 and counter["calls"] == 0


def test_temperature_sent_by_plain_backends():
    from wider.core.backends import sends_temperature

    for kind in ("openai_compat", "ollama", "anthropic", "litellm", "fake"):
        assert sends_temperature(ProviderConfig(name="x", kind=kind, model="m")) is True


# ---------------------------------------------------------------------------------------------- 2.2 runs (D1-D3)
import json  # noqa: E402


def per_backend(fails=None, misses=None, flips=None):
    """A responder factory: the true topic for every record, except ``misses`` {backend: {record number}} answered
    exclude, ``flips`` {backend: {record number: {seed: decision}}} answered per seed, and ``fails`` {backend:
    {(record number, seed)}} answered with text that is not JSON while ``counter["fail"]`` is true."""
    misses, flips, fails = misses or {}, flips or {}, fails or {}

    def factory(counter, cfg):
        def respond(req):
            counter["calls"] += 1
            counter.setdefault("by_model", {}).setdefault(cfg.model, 0)
            counter["by_model"][cfg.model] += 1
            no = record_no(req)
            if counter.get("fail", True) and (no, req.seed) in fails.get(cfg.name, ()):
                counter["failed"] = counter.get("failed", 0) + 1
                return "this is not JSON"
            if no in flips.get(cfg.name, {}):
                return decide(req, flips[cfg.name][no][req.seed])
            if no in misses.get(cfg.name, ()):
                return decide(req, "exclude")
            return decide(req)
        return respond
    return factory


def run_ids_of(res) -> list[str]:
    return [r["run_id"] for row in res["configs"] for r in row["runs"]]


def record_ids_in(obj) -> set[int]:
    """Every value under a ``record_id`` key anywhere in a JSON-like object."""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "record_id" and isinstance(v, int):
                out.add(v)
            else:
                out |= record_ids_in(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= record_ids_in(v)
    return out


ALPHA_DEV = 18   # record number of the one topic-alpha record in the development sample (record id 19)
BETA_DEV = 4     # record number of a topic-beta record in the development sample (record id 5)


def test_two_configurations_rows(locked, monkeypatch):
    """Scenario "Two configurations on the development sample": `fake` agrees with every label, `fake_b` excludes
    the one included record; 4 runs; rows in the order given with every figure; no configuration marked best."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    r = cli(p, "screen", "compare", "run", "cmp1", "--sample", "development", "--config", "fake", "--config",
            "fake_b", "--repeats", "2", "--json")
    assert r.exit_code == 0, r.output
    res = json.loads(r.output)
    assert n_tagged_runs(p) == 4 and len(run_ids_of(res)) == 4
    a, b = res["configs"]
    assert (a["label"], b["label"]) == ("fake", "fake_b")
    assert a["misses"] == [0, 0] and b["misses"] == [1, 1] and b["misses_text"] == "1–1"
    n = len(dev_rows(p))
    for row in (a, b):
        assert row["repeats_done"] == 2 and row["scored"] == [n, n]
        for k in ("false_includes", "false_maybes", "agree", "prompt_tokens", "completion_tokens", "cost"):
            assert row[k] is not None, k
        assert row["prompt_tokens"] > 0
    assert a["agree"] == [n, n] and b["agree"] == [n - 1, n - 1]
    shown = cli(p, "screen", "compare", "show", "cmp1")
    assert shown.exit_code == 0
    for text in (r.output, shown.output):
        assert not re.search(r"\b(best|winner|recommended)\b", text, re.I)
    assert shown.output.index("fake: backend fake") < shown.output.index("fake_b: backend fake_b")


def test_comparison_names_unadjudicated_labels(locked, monkeypatch):
    """manual-review D9 under ui-redesign D4: bo disagrees with alice on 2 records, none adjudicated: the comparison
    is refused with the count of disagreements; after adjudicating both it runs and names no unadjudicated label."""
    from wider.sample.labels import adjudicate
    p, counter = locked
    rows = dev_rows(p)[:2]
    unlock(p, "development")
    for r in rows:
        alpha = "topic alpha" in r["title"]
        label(p, "development", r["record_id"], "bo", "exclude" if alpha else "include", reason="a" if alpha else "")
    lock(p, "development")
    # a disagreement leaves the record without a final label: the comparison is refused until it is adjudicated
    # (ui-redesign D4); nothing is stored or called
    from wider.screen.devgate import DevSampleIncomplete
    with pytest.raises(DevSampleIncomplete, match="2 disagreements wait for adjudication"):
        compare.run(p, "cmp1", None, ["fake"], repeats=1)
    assert n_comparisons(p) == 0 and counter["calls"] == 0
    unlock(p, "development")
    for r in rows:
        adjudicate(p, "development", r["record_id"], "include", by="cara")
    lock(p, "development")
    compare.run(p, "cmp1", None, ["fake"], repeats=1)
    shown = cli(p, "screen", "compare", "show", "cmp1")
    assert shown.exit_code == 0 and "not adjudicated" not in shown.output
    assert not any("not adjudicated" in n for n in api(p).get("/api/screen/compare/cmp1").json()["notes"])


def test_compare_cli_matches_api(locked, monkeypatch):
    """`wider screen compare show cmp1 --json` and `GET /api/screen/compare/cmp1` return the same figures; `list`
    agrees with `GET /api/screen/compare`."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    r = cli(p, "screen", "compare", "show", "cmp1", "--json")
    assert r.exit_code == 0, r.output
    resp = api(p).get("/api/screen/compare/cmp1")
    assert resp.status_code == 200
    assert json.loads(r.output) == resp.json()
    r = cli(p, "screen", "compare", "list", "--json")
    assert json.loads(r.output) == api(p).get("/api/screen/compare").json()["comparisons"]
    assert api(p).get("/api/screen/compare/nope").status_code == 404
    r = cli(p, "screen", "compare", "show", "nope")
    assert r.exit_code == 1


def test_comparison_reuses_identical_evaluations(locked, monkeypatch):
    """Scenario "A new comparison reuses identical evaluations on request": with --reuse-evaluations, configuration
    `fake` of cmp3 makes no call (its evaluations equal cmp1's and are marked reused); only `fake_c` is screened."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend())
    add_backend(p, "fake_c", model="fake-m3")
    n = len(dev_rows(p))
    compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    before = dict(counter["by_model"])
    e = compare.estimate(p, "cmp3", None, ["fake", "fake_c"], 2, reuse_evaluations=True, probe=False)
    assert e["reusable"] == 2 * n and e["new_calls"] == 2 * n
    res = compare.run(p, "cmp3", None, ["fake", "fake_c"], repeats=2, reuse_evaluations=True)
    assert counter["by_model"]["fake-m1"] == before["fake-m1"], "configuration fake makes no call"
    assert counter["by_model"]["fake-m3"] == 2 * n
    st = fresh(p).store
    for run in res["configs"][0]["runs"]:
        assert st.scalar("SELECT COUNT(*) FROM screen_call_reuse WHERE run_id=?", (run["run_id"],)) == n
    for run in res["configs"][1]["runs"]:
        assert st.scalar("SELECT COUNT(*) FROM screen_call_reuse WHERE run_id=?", (run["run_id"],)) == 0
    assert [r["misses"] for r in res["configs"]] == [[0, 0], [0, 0]]


def test_failed_call_counted_not_asked(locked, monkeypatch):
    """Scenario "A failed call in a comparison" and "No question for a comparison run": `fake_b` fails on one record
    in repeat 1; the row says "1 failed call", repeat 1 leaves the record out, no question is raised, the next run
    calls the model for that record only, and the Screening stage gets one result naming cmp1."""
    from wider.core.stage_result import history, last_result

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(fails={"fake_b": {(BETA_DEV, 0)}}))
    n = len(dev_rows(p))
    hist = len(history(p, "screen"))
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    b = res["configs"][1]
    assert b["failed"] == 1 and b["failed_text"] == "1 failed call"
    assert [r["scored"] for r in b["runs"]] == [n - 1, n]
    assert b["runs"][0]["status"] == "done_with_errors"
    assert res["configs"][0]["failed_text"] == "0 failed calls"
    st = fresh(p).store
    assert st.scalar("SELECT COUNT(*) FROM questions WHERE kind='screen_failed'") == 0
    assert len(history(fresh(p), "screen")) == hist + 1
    last = last_result(fresh(p), "screen")
    assert "cmp1" in last["headline"] and any("1 failed call" in w for w in last["warnings"])
    assert "1 failed call" in cli(p, "screen", "compare", "show", "cmp1").output
    counter["fail"] = False
    calls = counter["calls"]
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    assert counter["calls"] == calls + 1, "only the failed record is screened again"
    b = res["configs"][1]
    assert b["failed"] == 0 and [r["scored"] for r in b["runs"]] == [n, n]
    assert st.scalar("SELECT COUNT(*) FROM questions WHERE kind='screen_failed'") == 0


def test_comparison_run_folder(locked, monkeypatch):
    """Scenario "Comparison run folder": run_spec.json (with the comparison), prompt.md, schema.json and
    responses.jsonl, no decisions.csv or errors.csv, also with a failed call; `wider screen replay RUN_ID` writes none
    and says "comparison run: no decisions.csv"."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(fails={"fake": {(BETA_DEV, 0)}}))
    res = compare.run(p, "cmp1", None, ["fake"], repeats=1)
    rid = res["configs"][0]["runs"][0]["run_id"]
    assert res["configs"][0]["failed"] == 1
    d = p.root / fresh(p).store.scalar("SELECT dir FROM runs WHERE run_id=?", (rid,))
    names = {f.name for f in d.iterdir()}
    assert {"run_spec.json", "prompt.md", "schema.json", "responses.jsonl"} <= names
    assert "decisions.csv" not in names and "errors.csv" not in names
    spec = json.loads((d / "run_spec.json").read_text())
    assert spec["settings"]["comparison"]["name"] == "cmp1" and spec["settings"]["comparison"]["repeat"] == 1
    r = cli(p, "screen", "replay", rid)
    assert r.exit_code == 0, r.output
    assert "comparison run: no decisions.csv" in r.output
    assert not (d / "decisions.csv").exists() and not (d / "errors.csv").exists()


def test_comparison_run_ids(locked, monkeypatch):
    """Scenario "Comparison runs get their own ids": the 4 ids end in __cmp.cmp1.c1.r1 ... c2.r2, and a plain run and
    a protocol run on the same backend, prompt, pass and records get their own ids and make their own calls."""
    from wider.screen.protocol import run_protocol

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend())
    n = len(dev_rows(p))
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    ids = run_ids_of(res)
    assert [i.split("__")[-1] for i in ids] == ["cmp.cmp1.c1.r1", "cmp.cmp1.c1.r2", "cmp.cmp1.c2.r1",
                                                  "cmp.cmp1.c2.r2"]
    scope = compare._scope(sorted(r["record_id"] for r in dev_rows(p)))
    assert all(i.startswith(f"screen__{scope.replace(':', '-').replace(',', '_')[:40]}") for i in ids)
    calls = counter["calls"]
    plain = screen(p, scope, provider_name="fake")
    assert plain["run_id"] not in ids and counter["calls"] == calls + n and "backend_change" not in plain
    calls = counter["calls"]
    run_protocol(p, scope, "main", provider_name="fake")
    proto = [r["run_id"] for r in fresh(p).store.query("SELECT run_id FROM runs WHERE run_id LIKE '%__main.s1'")]
    assert proto and not set(proto) & set(ids) and counter["calls"] > calls


def test_tagged_runs_outside_screen_readers(locked, monkeypatch):
    """D1: export, `wider screen models --protocol`, the result set, the recall estimate and the backend-change check
    leave a comparison's runs out; the Screening stage result names the comparison; `wider costs --by run` counts it."""
    from wider.core.stage_result import last_result
    from wider.recall.estimate import estimate as recall_estimate
    from wider.screen.export import export_protocol
    from wider.screen.models_summary import by_model
    from wider.screen.protocol import run_protocol
    from wider.screen.result_sets import run_ids, set_row

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend())
    run_protocol(p, "corpus", "main", provider_name="fake")
    label_all(p, "validation")

    def readers():
        q = fresh(p)
        ex = export_protocol(q, "main")
        texts = {k: (q.root / ex[k]).read_text() for k in ("decisions", "evaluations")}
        r = cli(p, "screen", "models", "--protocol", "main", "--json")
        assert r.exit_code == 0, r.output
        rec = recall_estimate(q, "validation", "main", record_result=False, seed=1)
        return texts, json.loads(r.output), run_ids(set_row(q, "main", 1)), {k: rec.get(k) for k in ("recall", "n")}

    before = readers()
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    tagged = run_ids_of(res)
    after = readers()
    assert after == before
    blob = json.dumps(after, default=str)
    assert not any(t in blob for t in tagged) and "__cmp." not in blob
    assert "__cmp." not in json.dumps(by_model(fresh(p), protocol="main"), default=str)
    assert "Comparison cmp1" in last_result(fresh(p), "screen")["headline"]
    n = len(dev_rows(p))
    st = fresh(p).store
    assert {r["run_ref"]: r["n"] for r in st.query("SELECT run_ref, COUNT(*) n FROM llm_usage WHERE run_ref LIKE "
                                                    "'%__cmp.%' GROUP BY run_ref")} == {t: n for t in tagged}
    r = runner.invoke(cli_app, ["costs", "--by", "run", "--dir", str(p.root)], env={"COLUMNS": "400"})
    assert r.exit_code == 0, r.output
    for t in tagged:
        assert re.search(rf"{re.escape(t)}\s*│\s*{n}\b", r.output), t
    # the backend-change check of a plain run on the comparison's records ignores the comparison's other backend
    scope = compare._scope(sorted(r["record_id"] for r in dev_rows(p)))
    out = screen(p, scope, provider_name="fake")
    assert "backend_change" not in out


def test_comparison_labels_match_evaluate(locked, monkeypatch):
    """D2: per repeat the comparison's misses, false includes, false maybes and agreement equal `wider screen
    evaluate` on the same run, against the same final labels."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    for row in res["configs"]:
        for k, run in enumerate(row["runs"]):
            ev = evaluate(fresh(p), run["run_id"])
            for key in ("misses", "false_includes", "false_maybes", "agree"):
                assert ev[key] == run[key], (row["label"], key)
            assert ev["n"] == run["scored"]
    r = cli(p, "screen", "evaluate", res["configs"][1]["runs"][0]["run_id"])
    assert r.exit_code == 0, r.output


def test_protocol_reuses_comparison_evaluations(locked, monkeypatch):
    """Scenario "Comparison evaluations reused by a protocol run": without --reuse-evaluations the records are
    screened afresh; with it `wider screen run` and pass A of `wider screen protocol` make no call for them and their
    reuse provenance names the comparison run."""
    from wider.screen.engine import run_calls
    from wider.screen.protocol import run_protocol
    from wider.screen.result_sets import run_ids, set_row

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend())
    n = len(dev_rows(p))
    res = compare.run(p, "cmp1", None, ["fake"], repeats=1)
    cmp_run = res["configs"][0]["runs"][0]["run_id"]
    calls = counter["calls"]
    afresh = screen(p, DEV, provider_name="fake")
    assert counter["calls"] == calls + n
    assert {c["evaluation"] for c in run_calls(fresh(p), afresh["run_id"])} == {"new"}
    calls = counter["calls"]
    ids = ",".join(str(i) for i in sorted((r["record_id"] for r in dev_rows(p)), reverse=True))
    r = cli(p, "screen", "run", f"ids:{ids}", "--provider", "fake", "--reuse-evaluations")
    assert r.exit_code == 0, r.output
    assert counter["calls"] == calls
    rid = fresh(p).store.scalar("SELECT run_id FROM runs WHERE run_id LIKE 'screen__ids-%' AND run_id NOT LIKE "
                                "'%__cmp.%' ORDER BY started_at DESC LIMIT 1")
    rows = run_calls(fresh(p), rid)
    assert len(rows) == n and {c["evaluation"] for c in rows} == {"reused"}
    assert {c["source_run_id"] for c in rows} == {cmp_run}
    run_protocol(p, DEV, "main", provider_name="fake", reuse_evaluations=True)
    pass_a = [i for i in run_ids(set_row(fresh(p), "main", 1)) if "__A__" in i]
    assert len(pass_a) == 1
    rows = run_calls(fresh(p), pass_a[0])
    assert len(rows) == n and {c["evaluation"] for c in rows} == {"reused"}
    assert {c["source_run_id"] for c in rows} == {cmp_run}


# ---------------------------------------------------------------------------------------------- 2.3 results (D2-D4, D7)
def test_unlabelled_records_refused(cproject):
    """Scenario "Records without a final label" (ui-redesign D4): a locked sample with records left is refused (exit 1,
    API 409 with the counts) before anything is stored or called; once every record has a final label the figures say
    "Y of Y records have a final label"."""
    from wider.screen.devgate import DevSampleIncomplete

    p, counter = cproject
    rows = dev_rows(p)
    n = len(rows)
    skip = {rows[1]["record_id"], rows[3]["record_id"]}
    label_all(p, skip=skip)
    lock(p, "development")
    with pytest.raises(DevSampleIncomplete, match="2 records without a final label"):
        compare.run(p, "cmp1", None, ["fake"], repeats=1)
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "1")
    assert r.exit_code == 1 and "label them all first" in r.output
    res = api(p).post("/api/screen/compare", json={"name": "cmp1", "configs": ["fake"], "repeats": 1})
    assert res.status_code == 409 and res.json()["dev_incomplete"]["missing"] == 2
    assert counter["calls"] == 0 and n_comparisons(p) == 0 and n_tagged_runs(p) == 0
    for rid in skip:   # the sample is locked: labelling it is still allowed (D22)
        label(p, "development", rid, "alice", "exclude", reason="a")
    res = compare.run(fresh(p), "cmp1", None, ["fake"], repeats=1)
    row = res["configs"][0]
    assert counter["calls"] == n and row["screened"] == [n, n] and row["scored"] == [n, n]
    assert row["labelled"] == n and row["unlabelled"] == 0
    text = f"{n} of {n} records have a final label"
    assert res["labelled_text"] == text
    assert text in cli(p, "screen", "compare", "show", "cmp1").output
    assert api(p).get("/api/screen/compare/cmp1").json()["labelled_text"] == text


def test_consistency_lists_changed_records(locked, monkeypatch):
    """Scenario "One record changes between repeats": `fake` answers one record include in repeat 1 and exclude in
    repeat 2; consistency is "n-1 of n records (..%)" and the record is listed with "include, exclude" in the CLI and
    the API."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(flips={"fake": {BETA_DEV: {0: "include", 1: "exclude"}}}))
    n = len(dev_rows(p))
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    cons = res["configs"][0]["consistency"]
    assert cons["measured"] and cons["text"] == f"{n - 1} of {n} records ({round(100 * (n - 1) / n)}%)"
    assert [(c["record_id"], c["decisions"], c["decisions_text"]) for c in cons["changed"]] == [
        (5, ["include", "exclude"], "include, exclude")]
    other = res["configs"][1]["consistency"]
    assert other["text"] == f"{n} of {n} records (100%)" and other["changed"] == []
    out = cli(p, "screen", "compare", "show", "cmp1").output
    assert f"consistency {n - 1} of {n} records" in out
    assert "changed: record 5 Record 04 on topic beta: include, exclude" in out
    got = api(p).get("/api/screen/compare/cmp1").json()["configs"][0]["consistency"]
    assert got["changed"][0]["decisions_text"] == "include, exclude" and got["text"] == cons["text"]


def test_consistency_notes(locked, monkeypatch):
    """Scenario "One repeat, and temperature 0": one repeat says consistency was not measured; temperature 0 with 3
    repeats carries the temperature-0 note; a backend that sends no temperature gets no temperature-0 note."""
    import wider.core.backends as backends

    p, counter = locked
    one = compare.run(p, "one", None, ["fake"], repeats=1)["configs"][0]["consistency"]
    assert one["measured"] is False and one["notes"] == ["consistency not measured (one repeat)"]
    zero = compare.run(p, "zero", None, ["fake_b@0"], repeats=3)["configs"][0]
    assert zero["temperature_text"] == "0" and zero["consistency"]["measured"]
    assert zero["consistency"]["notes"] == ["temperature 0: tests only whether the backend gives the same answer twice"]
    out = cli(p, "screen", "compare", "show", "zero").output
    assert out.count("temperature 0: tests only whether the backend gives the same answer twice") == 1
    assert "consistency not measured (one repeat)" in cli(p, "screen", "compare", "show", "one").output
    monkeypatch.setattr(backends, "sends_temperature", lambda cfg: cfg.name != "fake_b")
    nt = compare.run(p, "nt", None, ["fake_b"], repeats=2)["configs"][0]
    assert nt["temperature_text"] == "temperature not sent by this backend" and nt["consistency"]["notes"] == []


def area_project(locked, monkeypatch):
    """The locked project with the ranking clustered to rank 20 (two areas; the other development records not
    clustered) and `fake_b` missing the one included record; returns (project, counter, areas)."""
    from wider.rank.cluster import cluster
    from wider.sample.design import cluster_labels

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    cluster(p, k=2, top=20)
    areas = cluster_labels(fresh(p), fresh(p).store.get_meta("default_ranking"))
    return p, counter, areas


def test_breakdown_by_pool_and_area(locked, monkeypatch):
    """Scenario "A miss in one research area": `fake_b` misses the included record of one area: 1 miss there, 0 in
    the other, a "not clustered" row with its n; the per-pool table gives the miss under the record's pool; the CLI
    `show --by area|pool` prints the same rows."""
    p, counter, areas = area_project(locked, monkeypatch)
    rows = {r["record_id"]: r for r in dev_rows(p)}
    miss_id = 19
    assert miss_id in areas, "the included record lies in a clustered area"
    in_sample = {rid: areas.get(rid, "not clustered") for rid in rows}
    assert len(set(in_sample.values()) - {"not clustered"}) == 2 and "not clustered" in in_sample.values()
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    assert res["areas_message"] is None
    b = res["configs"][1]
    by_area = {a["key"]: a for a in b["by_area"]}
    assert list(by_area)[-1] == "not clustered"
    for key, a in by_area.items():
        members = [rid for rid, k in in_sample.items() if k == key]
        assert a["n"] == len(members) and a["records"] == len(members)
        assert a["misses"] == ([1, 1] if key == areas[miss_id] else [0, 0]), key
        assert (a["terms"] != []) == (key != "not clustered")
    assert by_area["not clustered"]["n"] == sum(1 for k in in_sample.values() if k == "not clustered")
    pool = rows[miss_id]["pool"].split(":")[0]
    for prow in b["by_pool"]:
        members = [rid for rid, r in rows.items() if r["pool"].split(":")[0] == prow["key"]]
        assert prow["n"] == len(members)
        assert prow["misses"] == ([1, 1] if prow["key"] == pool else [0, 0])
    assert all(a["misses"] == [0, 0] for a in res["configs"][0]["by_area"])
    out = cli(p, "screen", "compare", "show", "cmp1", "--by", "area").output
    assert f"area {areas[miss_id]} (" in out and "area not clustered: n " in out
    assert re.search(rf"area {areas[miss_id]} \([^)]*\): n \d+, misses 1–1", out)
    out = cli(p, "screen", "compare", "show", "cmp1", "--by", "pool").output
    assert re.search(rf"pool {pool}: n \d+, misses 1–1", out)
    r = cli(p, "screen", "compare", "show", "cmp1", "--by", "band")
    assert r.exit_code == 1 and "--by is pool or area" in r.output
    assert api(p).get("/api/screen/compare/cmp1").json()["configs"][1]["by_area"] == b["by_area"]


def test_breakdown_without_clusters(locked):
    """Scenario "No clusters": a ranking never clustered gives the no-clusters message and the per-pool table; a
    sample with no ranking (an imported one) says so."""
    p, counter = locked
    res = compare.run(p, "cmp1", None, ["fake"], repeats=1)
    assert res["areas_message"] == "no research areas: the ranking has no clusters (wider rank cluster)"
    assert "by_area" not in res["configs"][0] and res["configs"][0]["by_pool"]
    out = cli(p, "screen", "compare", "show", "cmp1", "--by", "area").output
    assert "no research areas: the ranking has no clusters (wider rank cluster)" in out
    assert "pool likely_include" in cli(p, "screen", "compare", "show", "cmp1", "--by", "pool").output
    fresh(p).store.execute("UPDATE samples SET ranking_id=NULL WHERE name='development'")
    res = api(p).get("/api/screen/compare/cmp1").json()
    assert res["areas_message"] == "no research areas: the sample has no ranking"
    assert "no research areas: the sample has no ranking" in cli(p, "screen", "compare", "show", "cmp1", "--by",
                                                                 "area").output


def test_records_added_after_comparison(locked):
    """Scenario "Records added to the sample after a comparison" (lock, compare, unlock, `wider sample extend`): the
    count is said, the figures still cover the comparison's records, and neither `show --json` nor the API names an
    added record."""
    p, counter = locked
    n = len(dev_rows(p))
    first = compare.run(p, "cmp1", None, ["fake"], repeats=2)
    unlock(p, "development")
    before = {r["record_id"] for r in dev_rows(fresh(p))}
    artifacts.save_new_version(fresh(p), "near_miss", {"near_miss": [
        {"key": "nm_beta", "breaks": "e1", "decision": "exclude", "text": " ".join([BETA] * 8)}]}, origin="manual")
    r = cli(p, "sample", "extend", "development", "--pool", "boundary", "--json")
    assert r.exit_code == 0 and json.loads(r.output)["added"] == 4, r.output
    added = sorted({r["record_id"] for r in dev_rows(fresh(p))} - before)
    assert len(added) == 4
    msg = "4 records were added to development after this comparison; run a new comparison to include them"
    # the added records have no label yet, so the figures are hidden until they do (ui-redesign D4)
    hid = compare.results(fresh(p), "cmp1")
    assert hid["hidden"] == HIDDEN.format(n=len(dev_rows(fresh(p))))
    # the label count follows the development sample's completeness, as on Screening: the 4 added records are open
    assert hid["labelled_text"] == f"4 of {len(dev_rows(fresh(p)))} records without a final label"
    label_all(fresh(p), skip=set(before))
    res = compare.results(fresh(p), "cmp1")
    assert msg in res["notes"] and res["added"] == 4 and res["records"] == n
    assert res["configs"][0]["misses"] == first["configs"][0]["misses"]
    assert res["configs"][0]["screened"] == [n, n]
    assert msg in cli(p, "screen", "compare", "show", "cmp1").output
    for got in (json.loads(cli(p, "screen", "compare", "show", "cmp1", "--json").output),
                api(p).get("/api/screen/compare/cmp1").json()):
        assert not record_ids_in(got) & set(added)
        assert msg in got["notes"]


def test_show_after_unlock(locked):
    """Scenario "Labels changed after an unlock": the figures follow the current final labels and say once that the
    sample is unlocked; after the lock the line is gone and the figures stay."""
    p, counter = locked
    res = compare.run(p, "cmp1", None, ["fake"], repeats=1)
    assert res["configs"][0]["misses"] == [1, 1] and res["unlocked"] is False
    line = "sample development is unlocked: these figures use its current labels"
    assert line not in cli(p, "screen", "compare", "show", "cmp1").output
    # the labelling and the comparison moved to the past, so the change below is after the comparison even within the
    # same second (timestamps have whole seconds; label-completion-and-sealing D6)
    p.store.execute("UPDATE history SET at='2026-01-01T00:00:00+00:00'")
    p.store.execute("UPDATE screen_comparisons SET finished_at='2026-01-01T00:00:01+00:00'")
    unlock(p, "development")
    label(p, "development", 19, "alice", "exclude", reason="a")
    res = compare.results(fresh(p), "cmp1")
    changed = "1 record of development had its labels changed after this comparison ran (2026-01-01)."
    assert res["configs"][0]["misses"] == [0, 0] and res["notes"] == [line, changed]
    out = cli(p, "screen", "compare", "show", "cmp1").output
    assert out.count(line) == 1
    assert api(p).get("/api/screen/compare/cmp1").json()["notes"] == [line, changed]
    lock(p, "development")
    res = compare.results(fresh(p), "cmp1")
    assert res["notes"] == [changed[:-1] + "; its figures use the current labels."]
    assert res["configs"][0]["misses"] == [0, 0]
    assert line not in cli(p, "screen", "compare", "show", "cmp1").output


# ---------------------------------------------------------------------------------------------- 2.4 redraw (D2)
def test_records_left_after_redraw(cproject):
    """Scenario "A sample redrawn after a comparison": a locked, labelled sample compared, then unlocked and
    redrawn: only the comparison's records still in the sample count, and the output says how many left."""
    p, counter = cproject
    label_all(p)   # D4: a comparison needs every record labelled
    lock(p, "development")
    ids = [r["record_id"] for r in dev_rows(p)]
    compare.run(p, "cmp1", None, ["fake"], repeats=2)
    unlock(p, "development")
    with p.store.tx() as c:   # the labels undone, so the sample may be drawn again
        c.execute("DELETE FROM labels")
    draw_development(p, seed=5, cluster_max_rank=20, near_miss_max_rank=20)   # keeps 3 of the 7 records
    label_all(p)   # the redrawn records too, so the figures are shown (D4)
    kept = [i for i in ids if i in {r["record_id"] for r in dev_rows(p)}]
    gone = len(ids) - len(kept)
    assert gone > 0 and kept, "the redraw keeps some records and drops others"
    res = compare.results(fresh(p), "cmp1")
    word = "record" if gone == 1 else "records"
    verb = "is" if gone == 1 else "are"
    msg = f"{gone} {word} of this comparison {verb} no longer in the sample"
    assert msg in res["notes"] and res["records_in_sample"] == len(kept)
    row = res["configs"][0]
    assert row["screened"] == [len(kept), len(kept)] and row["consistency"]["base"] == len(kept)
    assert msg in cli(p, "screen", "compare", "show", "cmp1").output


# ---------------------------------------------------------------------------------------------- 2.5 comparison.csv (D15)
def test_comparison_csv(locked, monkeypatch):
    """runs/compare_<name>/comparison.csv: per record title, pool, area, final label and each configuration's
    decision per repeat; written in the project folder, outside every run folder."""
    import csv

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    res = compare.run(p, "cmp1", None, ["fake", "fake_b"], repeats=2)
    path = p.root / res["csv"]
    assert path == p.root / "runs" / "compare_cmp1" / "comparison.csv" and path.exists()
    dirs = [p.root / d for (d,) in fresh(p).store.query("SELECT dir FROM runs")]
    assert not any(path.is_relative_to(d) for d in dirs)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert list(rows[0]) == ["record_id", "title", "pool", "area", "final_label", "fake r1", "fake r2", "fake_b r1",
                             "fake_b r2"]
    dev = {r["record_id"]: r for r in dev_rows(p)}
    assert sorted(int(r["record_id"]) for r in rows) == sorted(dev)
    by_id = {int(r["record_id"]): r for r in rows}
    assert by_id[19]["final_label"] == "include" and by_id[19]["fake r1"] == "include"
    assert by_id[19]["fake_b r1"] == by_id[19]["fake_b r2"] == "exclude"
    assert by_id[19]["title"] == dev[19]["title"] and by_id[19]["pool"] == dev[19]["pool"]
    assert by_id[19]["area"] == ""   # the ranking has no clusters


# ---------------------------------------------------------------------------------------------- 2.6 CLI (D16)
def test_estimate_only_parity(locked):
    """Scenario "Estimate only": CLI --estimate-only --json and POST /api/screen/compare/estimate give the same
    records, runs and new calls with each backend's locality and cost range; no call, no comparison listed."""
    p, counter = locked
    n = len(dev_rows(p))
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--config", "fake_b", "--repeats", "3",
            "--estimate-only", "--json")
    assert r.exit_code == 0, r.output
    c = json.loads(r.output)
    a = api(p).post("/api/screen/compare/estimate", json={"name": "cmp1", "configs": ["fake", "fake_b"],
                                                          "repeats": 3}).json()
    for got in (c, a):
        assert (got["records"], got["runs"], got["new_calls"]) == (n, 6, 6 * n)
        assert [b["backend"] for b in got["backends"]] == ["fake", "fake_b"]
        assert all(b["locality"] and b["lines"] for b in got["backends"])
    assert {k: c[k] for k in ("records", "runs", "new_calls", "backends", "lines")} == {
        k: a[k] for k in ("records", "runs", "new_calls", "backends", "lines")}
    text = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--config", "fake_b", "--repeats", "3",
               "--estimate-only").output
    assert f"Comparison cmp1: {n} records, 2 configurations × 3 repeats = 6 runs, {6 * n} new calls" in text
    assert counter["calls"] == 0 and n_comparisons(p) == 0
    assert "No comparison yet" in cli(p, "screen", "compare", "list").output
    assert json.loads(cli(p, "screen", "compare", "list", "--json").output) == []


def test_compare_run_prints_estimate_and_runs(locked):
    """`wider screen compare run` prints the estimate, each run as it starts, the figures and the CSV path."""
    p, counter = locked
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "2")
    assert r.exit_code == 0, r.output
    out = r.output
    assert out.index("Comparison cmp1: ") < out.index("fake repeat 1: ") < out.index("fake repeat 2: ")
    assert "These figures describe the development sample (records picked by hand), not the corpus." in out
    assert "per-record decisions: runs/compare_cmp1/comparison.csv" in out
    rows = json.loads(cli(p, "screen", "compare", "list", "--json").output)
    assert [(x["name"], x["configs"], x["repeats"]) for x in rows] == [("cmp1", ["fake"], 2)]
    assert "cmp1" in cli(p, "screen", "compare", "list").output


# ---------------------------------------------------------------------------------------------- group 8 follow-up
def test_compare_routes_run_to_completion(locked, monkeypatch):
    """POST /api/screen/compare answers the figures `compare show --json` gives; POST /api/jobs/compare runs as a job
    to done and stores the same rows as the CLI."""
    import time

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {ALPHA_DEV}}))
    c = api(p)
    resp = c.post("/api/screen/compare", json={"name": "direct", "configs": ["fake", "fake_b"], "repeats": 2})
    assert resp.status_code == 200, resp.text
    shown = json.loads(cli(p, "screen", "compare", "show", "direct", "--json").output)
    assert resp.json()["configs"] == shown["configs"] and shown["configs"][1]["misses"] == [1, 1]
    r = c.post("/api/jobs/compare", json={"name": "viajob", "configs": ["fake", "fake_b"], "repeats": 2})
    assert r.status_code == 200, r.text
    job = r.json()["job_id"]
    for _ in range(400):
        j = c.get(f"/api/jobs/{job}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    got = c.get("/api/screen/compare/viajob").json()
    assert [(x["label"], x["misses"], x["repeats_done"]) for x in got["configs"]] == [
        ("fake", [0, 0], 2), ("fake_b", [1, 1], 2)]
    assert n_tagged_runs(p) == 8


def test_temperature_not_sent_refused_by_cli_and_route(locked):
    """`@T` on a backend that sends no temperature: the CLI exits 1 and the route answers 400, with the same
    sentence; nothing stored."""
    p, counter = locked
    add_backend(p, "nt", model="m", kind="cli_agent", command=["agent", "{model}"])
    msg = "backend nt does not send a temperature; compare it without @0.70"
    r = cli(p, "screen", "compare", "run", "c1", "--config", "fake", "--config", "nt@0.70")
    assert r.exit_code == 1 and msg in r.output
    for path in ("/api/screen/compare/estimate", "/api/screen/compare", "/api/jobs/compare"):
        resp = api(p).post(path, json={"name": "c1", "configs": ["fake", "nt@0.70"]})
        assert resp.status_code == 400 and msg in resp.text, (path, resp.text)
    assert n_comparisons(p) == 0 and counter["calls"] == 0


def test_no_final_label_says_nothing_scored(cproject):
    """No record of the sample has a final label: since ui-redesign D4 the comparison is refused (it would score
    nothing), in the CLI and the API, and nothing is stored or called."""
    from wider.screen.devgate import DevSampleIncomplete
    p, counter = cproject
    lock(p, "development")
    n = len(dev_rows(p))
    with pytest.raises(DevSampleIncomplete, match=f"{n} records without a final label"):
        compare.run(p, "nolab", None, ["fake"], repeats=1)
    r = cli(p, "screen", "compare", "run", "nolab", "--config", "fake", "--repeats", "1")
    assert r.exit_code == 1 and "label them all first" in r.output
    assert api(p).post("/api/screen/compare", json={"name": "nolab", "configs": ["fake"], "repeats": 1}).status_code == 409
    assert n_comparisons(p) == 0 and counter["calls"] == 0


def test_show_configuration_not_run(locked):
    """A configuration with no finished run reads "not run" in `compare show` and carries no temperature-0 note;
    the estimate says "1 run" for one configuration and one repeat."""
    p, counter = locked
    compare.run(p, "part", None, ["fake@0.5", "fake_b@0"], repeats=2)
    st = fresh(p).store
    cid = st.scalar("SELECT comparison_id FROM screen_comparisons WHERE name='part'")
    st.execute("DELETE FROM screen_comparison_runs WHERE comparison_id=? AND config_index=2", (cid,))
    res = compare.results(fresh(p), "part")
    assert res["configs"][1]["repeats_done"] == 0 and res["configs"][1]["consistency"]["notes"] == []
    out = cli(p, "screen", "compare", "show", "part").output
    assert "  not run" in out and "temperature 0: tests only" not in out
    assert out.count("range over 2 repeats") == 1
    e = compare.estimate(p, "single", None, ["fake"], repeats=1, probe=False)
    assert "1 configuration × 1 repeat = 1 run, " in compare.estimate_lines(e)[0]


def test_reused_evaluation_of_a_twin_record_stays_with_its_record(locked, monkeypatch):
    """Two records with the same title and abstract share an evaluation key, so a reused evaluation may come from the
    twin's call: it still counts for the record it was reused for (ui-tester F1: 24 of 29 records were scored)."""
    from wider.screen.evaluate import calls_by_record

    p, counter = locked
    use_responder(monkeypatch, counter, per_backend())
    rows = dev_rows(p)
    a, b = rows[0]["record_id"], rows[1]["record_id"]
    st = fresh(p).store
    st.execute("UPDATE records SET title=(SELECT title FROM records WHERE record_id=?), "
               "abstract=(SELECT abstract FROM records WHERE record_id=?) WHERE record_id=?", (a, a, b))
    n = len(rows)
    compare.run(p, "cmp1", None, ["fake"], repeats=1)
    res = compare.run(p, "cmp3", None, ["fake"], repeats=1, reuse_evaluations=True)
    row = res["configs"][0]
    assert row["screened"] == [n, n] and row["scored"] == [n, n]
    run_id = row["runs"][0]["run_id"]
    assert fresh(p).store.scalar("SELECT COUNT(*) FROM screen_call_reuse WHERE run_id=?", (run_id,)) == n
    assert set(calls_by_record(fresh(p), run_id)) == {r["record_id"] for r in rows}
