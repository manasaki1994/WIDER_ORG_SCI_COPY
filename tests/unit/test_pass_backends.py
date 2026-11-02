"""A backend per pass (change screening-config-comparison, group 5, design D10-D14): pass B and the judge, and the
second screen's S2 and S3, run on their own backend; their settings, cost, consent, mixed marks and shared-model
sentences follow. Synthetic topic-neutral records and fake backends only."""
import json

import pytest

from tests.unit.test_screen_compare import (  # noqa: F401  (_template is a fixture)
    _template,
    add_backend,
    api,
    cli,
    copy_project,
    decide,
    fresh,
    label_all,
    record_no,
    use_responder,
)
from wider.screen.protocol import run_protocol, second_screen

FLIP_B = (3, 9)   # alpha records pass B's model excludes: the passes disagree on these two of the corpus


def pass_responder(counter, cfg):
    """Include topic alpha; the model ``b-m`` excludes records 3 and 9; every model answers maybe for the record
    numbers in ``counter["maybe"]``. Calls are counted per model."""
    def respond(req):
        counter["calls"] += 1
        counter.setdefault("by_model", {}).setdefault(cfg.model, 0)
        counter["by_model"][cfg.model] += 1
        n = record_no(req)
        if n in counter.get("maybe", ()):
            return decide(req, "maybe")
        if cfg.model.startswith("b-m") and n in FLIP_B:
            return decide(req, "exclude")
        return decide(req)
    return respond


@pytest.fixture
def pp(_template, tmp_path, monkeypatch):  # noqa: F811
    """The synthetic project with fake backends a, b, c (models a-m, b-m, c-m), a the default, and its development
    sample labelled (runs that include development records wait for it, ui-redesign D4); (project, counter)."""
    counter = {"calls": 0}
    use_responder(monkeypatch, counter, pass_responder)
    p = copy_project(_template, tmp_path)
    for n in ("a", "b", "c"):
        add_backend(p, n)
    p.config.default_provider = "a"
    p.save()
    label_all(p)
    return p, counter


def calls_by_pass(p) -> dict[str, set]:
    out: dict[str, set] = {}
    for r in fresh(p).store.query("SELECT pass, model FROM screen_calls"):
        out.setdefault(r["pass"], set()).add(r["model"])
    return out


def set_settings(p, protocol="main", n=1) -> dict:
    return json.loads(fresh(p).store.scalar(
        "SELECT settings FROM screen_result_sets WHERE protocol=? AND result_set=?", (protocol, n)))


def n_sets(p, protocol="main") -> int:
    return fresh(p).store.scalar("SELECT COUNT(*) FROM screen_result_sets WHERE protocol=?", (protocol,))


def models_json(p, *args) -> dict:
    r = cli(p, "screen", "models", *args, "--json")
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


def passes_of(summary) -> dict[str, list]:
    return {m["model"]: m["passes"] for m in summary["models"]}


# ---------------------------------------------------------------------------------------------- 5.1 stage keys
def test_provider_row_names_pass(pp):
    """`backends.provider_rows` lists a backend set only for `screen_b` or `screen_judge` as "screen (pass B)" or
    "screen (judge)"."""
    from wider.core.backends import provider_rows

    p, _ = pp
    p.config.stage_providers = {"screen_b": "b", "screen_judge": "c"}
    p.save()
    rows = {r["name"]: r for r in provider_rows(fresh(p))}
    flat = lambda r: json.dumps(r)  # noqa: E731
    assert "screen (pass B)" in flat(rows["b"]) and "screen (judge)" not in flat(rows["b"])
    assert "screen (judge)" in flat(rows["c"]) and "screen (pass B)" not in flat(rows["c"])


def test_stage_keys_for_passes(pp):
    """Scenario "Pass B and judge keys"."""
    from wider.screen.protocol import pass_backends

    p, _ = pp
    r = cli(p, "providers", "stage", "screen_b", "b")
    assert r.exit_code == 0, r.output
    before = (p.root / "project.yaml").read_bytes()
    r = cli(p, "providers", "stage", "screen_judge", "nosuch")
    assert r.exit_code == 1 and "Traceback" not in r.output
    assert (p.root / "project.yaml").read_bytes() == before
    q = fresh(p)
    allb = pass_backends(q)
    assert (allb["A"].name, allb["B"].name, allb["C"].name) == ("a", "b", "a")
    r = cli(p, "providers", "stage", "screen_b")
    assert r.exit_code == 0, r.output
    assert "screen_b" not in fresh(p).config.stage_providers
    # a screen_b naming a removed backend follows pass A
    q = fresh(p)
    q.config.stage_providers = {"screen_b": "gone"}
    q.save()
    assert pass_backends(fresh(p))["B"].name == "a"
    help_ = cli(p, "providers", "stage", "--help").output
    for key in ("construct", "screen", "screen_b", "screen_judge"):
        assert key in help_


def test_backend_check_names_pass_uses(pp):
    """The backend check and the home summary list a stage override for pass B or the judge by its pass."""
    import asyncio

    from wider.core.backend_check import check_backends
    from wider.core.summary import _llm_uses

    p, _ = pp
    p.config.stage_providers = {"screen_b": "b", "screen_judge": "c"}
    p.save()
    q = fresh(p)
    chk = asyncio.run(check_backends(q))
    llm = {r["name"]: r["uses"] for r in chk["results"] if r["role"] == "llm"}
    assert llm["a"] == ["default"] and llm["b"] == ["screen (pass B)"] and llm["c"] == ["screen (judge)"]
    assert {r["name"]: r["uses"] for r in _llm_uses(q, None)} == llm


# ---------------------------------------------------------------------------------------------- 5.2 passes
def test_three_backends(pp):
    """Scenario "Three backends"."""
    p, counter = pp
    r = cli(p, "screen", "protocol", "corpus", "--provider", "a", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    assert calls_by_pass(p) == {"A": {"a-m"}, "B": {"b-m"}, "C": {"c-m"}}
    q = fresh(p)
    assert q.store.scalar("SELECT COUNT(*) FROM screen_calls WHERE pass='C'") == len(FLIP_B)
    runs = {r["run_id"]: r["model"] for r in q.store.query("SELECT run_id, model FROM runs WHERE kind='screen'")}
    for pass_, name in (("A", "a"), ("B", "b"), ("C", "c")):
        rid = next(k for k in runs if f"__{pass_}__" in k + "__")
        assert f"__{name}__{pass_}" in rid and runs[rid] == f"{name}-m", runs
    s = models_json(p, "--protocol", "main")
    assert passes_of(s) == {"a-m": ["A"], "b-m": ["B"], "c-m": ["C"]} and s["mixed"] is False
    from wider.screen.models_summary import by_model
    assert api(p).get("/api/screen/models?protocol=main").json()["models"] == by_model(fresh(p), protocol="main")["models"]


def test_stage_keys_default_per_pass(pp):
    """Scenario "Stage keys as defaults"."""
    p, _ = pp
    for key, name in (("screen_b", "b"), ("screen_judge", "c")):
        assert cli(p, "providers", "stage", key, name).exit_code == 0
    before = (p.root / "project.yaml").read_bytes()
    r = cli(p, "screen", "protocol", "corpus")
    assert r.exit_code == 0, r.output
    assert calls_by_pass(p) == {"A": {"a-m"}, "B": {"b-m"}, "C": {"c-m"}}
    r = cli(p, "screen", "protocol", "corpus", "--name", "other", "--provider-b", "a")
    assert r.exit_code == 0, r.output
    q = fresh(p)
    other = {r["pass"] for r in q.store.query(
        "SELECT DISTINCT c.pass FROM screen_calls c JOIN runs r ON r.run_id=c.run_id "
        "WHERE r.run_id LIKE '%other%' AND c.model='a-m'")}
    assert other == {"A", "B"}, other   # pass B on a for that run; its passes agree, so no third read
    assert (p.root / "project.yaml").read_bytes() == before


def test_second_screen_per_pass(pp):
    """Scenario "Second screen with per-pass backends"."""
    p, counter = pp
    counter["maybe"] = (0, 6, 12)
    assert cli(p, "screen", "protocol", "corpus").exit_code == 0
    counter["maybe"] = ()
    pre = fresh(p).store.scalar("SELECT MAX(call_id) FROM screen_calls")
    r = cli(p, "screen", "second", "main", "--provider", "a", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    new = {}
    for row in fresh(p).store.query("SELECT pass, model FROM screen_calls WHERE call_id>?", (pre,)):
        new.setdefault(row["pass"], set()).add(row["model"])
    assert new["S1"] == {"a-m"} and new["S2"] == {"b-m"}, new
    st = set_settings(p, "main_second")
    assert st["backends"]["S2"]["model"] == "b-m" and st["backends"]["S3"]["model"] == "c-m"
    s = models_json(p, "--protocol", "main_second")
    assert "S1" in passes_of(s)["a-m"] and "S2" in passes_of(s)["b-m"]


def test_second_screen_third_read_on_judge(pp):
    """S3 of the second screen runs on the judge's backend when S1 and S2 disagree."""
    p, counter = pp
    counter["maybe"] = (3, 9)
    assert cli(p, "screen", "protocol", "corpus").exit_code == 0
    counter["maybe"] = ()
    pre = fresh(p).store.scalar("SELECT MAX(call_id) FROM screen_calls")
    r = cli(p, "screen", "second", "main", "--provider", "a", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    new = {}
    for row in fresh(p).store.query("SELECT pass, model FROM screen_calls WHERE call_id>?", (pre,)):
        new.setdefault(row["pass"], set()).add(row["model"])
    assert new == {"S1": {"a-m"}, "S2": {"b-m"}, "S3": {"c-m"}}, new
    assert passes_of(models_json(p, "--protocol", "main_second"))["c-m"] == ["S3"]


SHARED = "Passes A and B use the same model (a-m, temperature 0): their readings are not independent."


def test_shared_model_named(pp):
    """Scenario "A and B on one model": the CLI before the run, the run result and the page view (the methods draft:
    `test_methods_names_shared_model`)."""
    from wider.screen.protocol import pass_view

    p, _ = pp
    r = cli(p, "screen", "protocol", "corpus", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    assert SHARED in " ".join(r.output.split())
    assert calls_by_pass(p) == {"A": {"a-m"}, "B": {"a-m"}}   # the run went ahead (the passes agree: no third read)
    assert pass_view(fresh(p), "protocol", provider_c="c")["shared"] == [SHARED]
    assert api(p).get("/api/screen/passes?mode=protocol&provider_c=c").json()["shared"] == [SHARED]
    out = run_protocol(fresh(p), "corpus", "x", provider_c="c")
    assert out["shared_models"] == [SHARED] and out["pass_backends"] == {"A": "a", "B": "a", "C": "c"}
    view = pass_view(fresh(p), "second", provider_b="b")
    assert view["shared"] == ["Passes S1 and S3 use the same model (a-m, temperature 0): their readings are not "
                              "independent."]


@pytest.mark.parametrize("setting,flag,line", [
    ("passes", "--provider-b", "pass B does not run in this protocol (screening.passes: 1); --provider-b is not used"),
    ("passes", "--provider-c", "the judge does not run in this protocol (screening.passes: 1); --provider-c is not used"),
    ("blind_third_read", "--provider-c",
     "the judge does not run in this protocol (screening.blind_third_read: false); --provider-c is not used"),
])
def test_unused_pass_backend_noted(pp, setting, flag, line):
    """Scenario "A per-pass backend for a pass that does not run": noted, not refused, in the CLI, the run result
    and the page view (which disables that picker); the second screen uses both flags and prints no note."""
    from wider.screen.protocol import pass_view

    p, counter = pp
    sc = p.config.protocol.screening
    if setting == "passes":
        sc.passes = 1
    else:
        sc.blind_third_read = False
    p.save()
    name = "b" if flag == "--provider-b" else "c"
    counter["maybe"] = (0, 6)
    r = cli(p, "screen", "protocol", "corpus", flag, name)
    assert r.exit_code == 0, r.output
    assert line in " ".join(r.output.split())
    assert name + "-m" not in set().union(*calls_by_pass(p).values())
    out = run_protocol(fresh(p), "corpus", "x", **{"provider_" + flag[-1]: name})
    assert out["notes"] == [line]
    pick = "B" if flag == "--provider-b" else "C"
    view = api(p).get(f"/api/screen/passes?mode=protocol&provider_{flag[-1]}={name}").json()
    assert view["notes"] == [line] and view["pass_off"][pick] == line.split(";")[0]
    assert pass_view(fresh(p), "protocol", **{"provider_" + flag[-1]: name})["notes"] == [line]
    counter["maybe"] = ()
    r = cli(p, "screen", "second", "main", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    assert "is not used" not in r.output and "does not run" not in r.output
    assert "b-m" in {m for ms in calls_by_pass(p).values() for m in ms}


# ---------------------------------------------------------------------------------------------- 5.2a cost, consent
def test_cost_and_consent_per_pass(pp, monkeypatch):
    """Scenario "Cost and consent for every pass's backend": the estimate prices pass B at r's rates and pass A as
    local; the run, the job route and the synchronous route stop before any call for the unconsented pass-B backend."""
    from wider.core.estimate import estimate_screen

    p, counter = pp
    monkeypatch.setenv("R_KEY", "synthetic-not-a-key")
    add_backend(p, "r", kind="openai_compat", model="r-m", base_url="https://llm.example.invalid/v1",
                api_key_env="R_KEY", price_input_per_mtok=1.0, price_output_per_mtok=4.0)
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "r", "--estimate-only")
    assert r.exit_code == 0, r.output
    flat = " ".join(r.output.split())
    assert "pass A on a: local, no charge" in flat and "pass B on r: $" in flat, flat
    e = estimate_screen(fresh(p), "protocol", scope="corpus", provider_b="r", probe=False)
    per = {tuple(x["passes"]): x for x in e["per_pass"]}
    assert per[("B",)]["priced"] and per[("B",)]["max_usd"] > 0 and per[("B",)]["backend"] == "r"
    assert per[("A",)]["locality"] in ("local", "test") and not per[("A",)]["max_usd"]
    assert e["max_usd"] == per[("B",)]["max_usd"]
    body = {"mode": "protocol", "scope": "corpus", "provider_b": "r"}
    assert api(p).post("/api/estimate", json={"stage": "screen", **body}).json()["per_pass"] == e["per_pass"]
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "r")
    assert r.exit_code == 2 and "r" in r.output, r.output
    c = api(p)
    for path in ("/api/jobs/screen", "/api/screen/run"):
        resp = c.post(path, json=body)
        assert resp.status_code == 403, resp.text
        d = resp.json()["detail"]["backend_consent_required"]
        assert d["backend"] == "r" and d["operation"] == "research"
    assert counter["calls"] == 0
    q = fresh(p)
    assert q.store.scalar("SELECT COUNT(*) FROM runs WHERE kind='screen'") == 0
    assert q.store.scalar("SELECT COUNT(*) FROM jobs") == 0


def test_pass_b_change_caught_by_preflight(pp):
    """A finished set meeting a new pass-B backend: the shared preflight answers `new_set_needed` before any call."""
    from wider.screen.preflight import preflight_screen, refusal_body

    p, counter = pp
    run_protocol(p, "corpus", "main")
    calls = counter["calls"]
    with pytest.raises(ValueError) as e:
        preflight_screen(fresh(p), "protocol", scope="corpus", protocol="main", provider_b="b")
    rb = refusal_body(e.value)
    assert "new_set_needed" in rb and "backends.B.model" in rb["new_set_needed"]["changed"]
    assert counter["calls"] == calls


# ---------------------------------------------------------------------------------------------- 5.3 settings
def test_old_set_keeps_settings(pp):
    """Scenario "An older set needs no new set": a set with every pass on A records no `backends`, and the same run
    again makes no call and no set."""
    p, counter = pp
    r = cli(p, "screen", "protocol", "corpus")
    assert r.exit_code == 0, r.output
    before = set_settings(p)
    assert "backends" not in before
    calls = counter["calls"]
    r = cli(p, "screen", "protocol", "corpus")
    assert r.exit_code == 0, r.output
    assert counter["calls"] == calls and n_sets(p) == 1 and set_settings(p) == before
    # the explicit A backend for B and the judge is still "every pass on A"
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "a", "--provider-c", "a")
    assert r.exit_code == 0, r.output
    assert counter["calls"] == calls and n_sets(p) == 1


def test_pass_backend_change_needs_new_set(pp):
    """Scenario "A new pass-B backend needs a new set"."""
    p, counter = pp
    assert cli(p, "screen", "protocol", "corpus").exit_code == 0
    calls = counter["calls"]
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "b")
    assert r.exit_code == 0, r.output
    flat = " ".join(r.output.split())
    assert "backends.B.model" in flat and "--new-set" in flat, flat
    assert counter["calls"] == calls and n_sets(p) == 1
    resp = api(p).post("/api/screen/run", json={"mode": "protocol", "scope": "corpus", "provider_b": "b"})
    assert resp.status_code == 409 and "backends.B.model" in resp.json()["new_set_needed"]["changed"]
    resp = api(p).post("/api/jobs/screen", json={"mode": "protocol", "scope": "corpus", "provider_b": "b"})
    assert resp.status_code == 409 and "backends.B.model" in resp.json()["new_set_needed"]["changed"]
    assert counter["calls"] == calls
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "b", "--new-set")
    assert r.exit_code == 0, r.output
    assert n_sets(p) == 2 and set_settings(p, n=2)["backends"]["B"]["model"] == "b-m"
    assert "backends" not in set_settings(p, n=1)


# ---------------------------------------------------------------------------------------------- 5.4 mixed
def test_pass_backend_not_mixed(pp):
    """Scenario "A pass-B backend chosen on purpose is not mixed"; a pass-B run continued on another model after a
    confirmed backend change is."""
    from wider.screen.models_summary import by_model

    p, counter = pp
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "b")
    assert r.exit_code == 0, r.output
    s = models_json(p, "--protocol", "main")
    assert passes_of(s) == {"a-m": ["A", "C"], "b-m": ["B"]} and s["mixed"] is False
    assert api(p).get("/api/screen/models?protocol=main").json()["mixed"] is False
    # pass B of another set stops part-way, its backend changes model, and the run continues after confirming
    run_protocol(fresh(p), "corpus", "later", provider_b="b", limit=5)
    q = fresh(p)
    next(c for c in q.config.providers if c.name == "b").model = "b-m3"
    q.save()
    run_protocol(fresh(p), "corpus", "later", provider_b="b", backend_change="continue")
    s = by_model(fresh(p), protocol="later")
    assert {"b-m", "b-m3"} <= set(passes_of(s)) and s["mixed"] is True
    assert models_json(p, "--protocol", "later")["mixed"] is True
    # a set with no recorded settings keeps the old rule: more than one model is mixed
    q = fresh(p)
    q.store.execute("UPDATE screen_result_sets SET settings=NULL WHERE protocol='main'")
    assert by_model(fresh(p), protocol="main")["mixed"] is True


def _csv(p, path):
    import csv
    return list(csv.DictReader((p.root / path).open(encoding="utf-8")) if not str(path).startswith("/")
                else csv.DictReader(open(path, encoding="utf-8")))


def test_export_pass_backend_not_mixed(pp):
    """Scenario "Per-pass backends in the export": A on a, B on b (chosen), C on a: record 3 has models a-m;b-m and
    is not marked mixed."""
    from wider.screen.export import export_protocol

    p, _ = pp
    run_protocol(p, "corpus", "main", provider_b="b")
    out = export_protocol(fresh(p), "main")
    rows = _csv(p, out["decisions"])
    ids = {r["record_id"]: r for r in rows}
    rid = str(fresh(p).store.scalar("SELECT record_id FROM records WHERE title LIKE 'Record 03 %'"))
    assert ids[rid]["final_decision"] == "include" and ids[rid]["decided_by_models"] == "a-m;b-m", ids[rid]
    assert not any(r["mixed_models"] for r in rows), {r["mixed_models"] for r in rows}
    r = cli(p, "screen", "export", "main")
    assert r.exit_code == 0, r.output
    assert _csv(p, out["decisions"]) == rows


def test_carried_votes_judged_by_source_set(pp):
    """A second screen's carried decisions keep their pass-B votes on b: judged by the source set's settings (where
    B is b), they are not mixed."""
    from wider.screen.export import export_protocol

    p, counter = pp
    counter["maybe"] = (0, 6)
    run_protocol(p, "corpus", "main", provider_b="b")
    counter["maybe"] = ()
    second_screen(fresh(p), "main", provider_b="b")
    out = export_protocol(fresh(p), "main_second")
    rows = _csv(p, out["decisions"])
    carried = [r for r in rows if r["decided_by"].startswith("carried")]
    assert carried and any("b-m" in r["decided_by_models"] for r in carried)
    assert not any(r["mixed_models"] for r in rows), [(r["record_id"], r["models"]) for r in rows if r["mixed_models"]]


def test_methods_names_shared_model(pp):
    """Scenario "A and B on one model", the methods draft: passes A and B on a, the judge on c."""
    from wider.recall.outputs import methods_draft

    p, counter = pp
    counter["maybe"] = (3,)   # a disagreement only on record 3 would need b; here both passes agree, so no judge
    r = cli(p, "screen", "protocol", "corpus", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    draft = methods_draft(fresh(p), "main")
    assert SHARED in draft
    assert "Every pass (pass A and pass B) used a-m." in draft


def test_shared_sentence_grammar():
    """The shared-model sentence reads "Pass A and the judge" when one lettered pass shares with the judge."""
    from wider.screen.protocol import _shared_sentence

    tail = " use the same model (m, temperature 0): their readings are not independent."
    assert _shared_sentence(["A", "B"], "m", 0) == "Passes A and B" + tail
    assert _shared_sentence(["A", "the judge"], "m", 0) == "Pass A and the judge" + tail
    assert _shared_sentence(["A", "B", "the judge"], "m", 0) == "Passes A, B and the judge" + tail
    assert _shared_sentence(["S1", "S3"], "m", 0) == "Passes S1 and S3" + tail
