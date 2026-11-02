"""The development sample is complete before any model output on it (ui-redesign D4, task 3.1): screening runs that
include records of an incomplete purposive sample (sample, `ids:`, `maybe:` and corpus scopes), their development
evaluation, a comparison and a replay are refused (exit 1, API 409 with the counts) before the sending notice, consent,
a job, a run folder or a call; preparation is not. Synthetic topic-neutral records and the counting fake provider
of test_screen_compare; nothing calls a real model or the network."""
from __future__ import annotations

import json
import re

import pytest

from tests.unit.test_screen_compare import (  # noqa: F401  (the fixtures are used by name)
    _template,
    api,
    cli,
    cproject,
    dev_rows,
    fresh,
    label_all,
)
from wider.sample import labels as lb
from wider.screen import compare
from wider.screen.devgate import PACKAGE_OUT, DevSampleIncomplete
from wider.screen.engine import screen

DEV = "sample:development"


def _state(p) -> tuple[int, int, int, str]:
    """Runs, calls, comparisons and project.yaml: none may change when a request is refused."""
    q = fresh(p).store
    return (q.scalar("SELECT COUNT(*) FROM runs"), q.scalar("SELECT COUNT(*) FROM screen_calls"),
            q.scalar("SELECT COUNT(*) FROM screen_comparisons"), (p.root / "project.yaml").read_text())


def _outside(p) -> int:
    """A record in no purposive sample."""
    dev = {r["record_id"] for r in dev_rows(p)}
    return next(i for (i,) in fresh(p).store.query("SELECT record_id FROM records ORDER BY record_id") if i not in dev)


def _refused_everywhere(p, counter, sentence: str) -> None:
    before = _state(p)
    for args in (["screen", "run", DEV], ["screen", "run", "corpus"], ["screen", "protocol", "corpus"],
                 ["screen", "protocol", DEV]):
        r = cli(p, *args)
        out = " ".join(r.output.split())
        assert r.exit_code == 1 and sentence in out, (args, r.output)
        assert "will be sent" not in out and "consent" not in out.lower(), args   # no sending notice, no consent step
    c = api(p)
    for body in ({"scope": DEV}, {"scope": "corpus", "mode": "protocol"}):
        for url in ("/api/screen/run", "/api/jobs/screen"):
            r = c.post(url, json=body)
            assert r.status_code == 409, (url, body, r.text)
            d = r.json()
            assert sentence in d["detail"] and d["dev_incomplete"]["missing"] >= 0 and d["dev_incomplete"]["samples"]
    with pytest.raises(DevSampleIncomplete):   # the second check, for any caller that skips the first
        screen(fresh(p), DEV)
    assert counter["calls"] == 0 and _state(p) == before
    assert c.get("/api/jobs").status_code in (200, 404)


def test_refused_while_labels_open(cproject):  # noqa: F811
    p, counter = cproject
    rows = dev_rows(p)
    label_all(p, skip={rows[0]["record_id"], rows[2]["record_id"]})
    _refused_everywhere(p, counter, "the development sample has 2 records without a final label; label them all first")
    d = api(p).post("/api/screen/run", json={"scope": DEV}).json()
    assert d["dev_incomplete"] == {"samples": [{"sample": "development", "missing": 2, "packages_out": []}], "missing": 2}
    # an ids: scope with one development record is refused too; one without any runs
    r = cli(p, "screen", "run", f"ids:{rows[0]['record_id']}")
    assert r.exit_code == 1 and "label them all first" in r.output
    r = cli(p, "screen", "run", f"ids:{_outside(p)}")
    assert r.exit_code == 0, r.output
    assert counter["calls"] == 1


def test_two_reviewer_protocol(cproject):  # noqa: F811
    p, counter = cproject
    p.config.protocol = p.config.protocol.set("sampling.development_reviewers", 2)
    p.save()
    label_all(p)                                   # alice alone: no label is final with two required
    n = len(dev_rows(p))
    r = cli(p, "screen", "run", DEV)
    out = " ".join(r.output.split())
    assert r.exit_code == 1
    assert f"the development sample has {n} records without a final label ({n} lack a second reviewer's label)" in out
    label_all(p, reviewer="bo")
    rid = dev_rows(p)[0]["record_id"]
    lb.label(p, "development", rid, "bo", "maybe")  # bo disagrees on one record
    r = cli(p, "screen", "run", DEV)
    assert r.exit_code == 1 and "1 record without a final label (1 disagreement waits for adjudication)" in " ".join(r.output.split())
    lb.adjudicate(p, "development", rid, "maybe", by="alice")
    assert cli(p, "screen", "run", DEV).exit_code == 0
    assert counter["calls"] == n


def test_open_package_closes_the_gate(cproject):  # noqa: F811
    from wider.construct import artifacts
    from wider.sample import package as pk

    p, counter = cproject
    artifacts.set_status(p, "codebook", artifacts.latest(p, "codebook")["version"], "approved")
    label_all(p)
    P = pk.make(p, "bo", ["development"], "independent")
    r = cli(p, "screen", "run", DEV)
    assert r.exit_code == 1 and PACKAGE_OUT in r.output
    d = api(p).post("/api/screen/run", json={"scope": DEV}).json()
    assert d["dev_incomplete"]["samples"][0]["packages_out"] == ["bo"]
    assert counter["calls"] == 0
    pk.withdraw(p, P["package_id"], "the reviewer left", by="alice")
    assert cli(p, "screen", "run", DEV).exit_code == 0 and counter["calls"] > 0


def test_preparation_not_refused(cproject, monkeypatch):  # noqa: F811
    """Estimates, choosing models, model checks (`wider check-backends`, its page twin) and the comparison's estimate
    are not refused; nothing is called (the probe is replaced, so no backend is contacted)."""
    from tests.unit.test_shell import _probe_ok

    p, counter = cproject
    _probe_ok(monkeypatch)
    r = cli(p, "check-backends")
    assert r.exit_code == 0 and "ok" in r.output and "development" not in r.output, r.output
    res = api(p).post("/api/backends/check")
    assert res.status_code == 200 and res.json()["ok"], res.text
    r = cli(p, "screen", "run", DEV, "--estimate-only")
    assert r.exit_code == 0, r.output
    assert cli(p, "screen", "protocol", "corpus", "--estimate-only").exit_code == 0
    c = api(p)
    assert c.post("/api/screen/compare/estimate", json={"name": "cmp1", "configs": ["fake"], "repeats": 1}).status_code == 200
    assert cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "1", "--estimate-only").exit_code == 0
    assert c.get("/api/providers").status_code == 200               # the model cards to choose from
    assert counter["calls"] == 0


def test_allowed_when_all_labelled(cproject):  # noqa: F811
    p, counter = cproject
    label_all(p)
    n = len(dev_rows(p))
    r = cli(p, "screen", "run", DEV)
    assert r.exit_code == 0, r.output
    assert counter["calls"] == n


def test_evaluate_refused(cproject):  # noqa: F811
    p, counter = cproject
    label_all(p)
    cli(p, "screen", "run", DEV)
    run_id = fresh(p).store.scalar("SELECT run_id FROM runs")
    # a label withdrawn after the run (undo) reopens the sample
    rid = dev_rows(p)[0]["record_id"]
    fresh(p).store.execute("DELETE FROM labels WHERE record_id=?", (rid,))
    r = cli(p, "screen", "evaluate", run_id)
    assert r.exit_code == 1 and "1 record without a final label" in r.output
    for url in (f"/api/screen/evaluate/{run_id}", f"/api/adjacent/cases/{run_id}"):
        res = api(p).get(url)
        assert res.status_code == 409 and res.json()["dev_incomplete"]["missing"] == 1, url
    calls, runs = counter["calls"], fresh(p).store.scalar("SELECT COUNT(*) FROM runs")
    r = cli(p, "screen", "replay", run_id)
    out = " ".join(r.output.split())
    assert r.exit_code == 1 and "1 record without a final label; label them all first" in out
    # the replay is refused before anything is called or stored
    assert counter["calls"] == calls and fresh(p).store.scalar("SELECT COUNT(*) FROM runs") == runs


def test_compare_refused(cproject):  # noqa: F811
    """A comparison on a locked sample with records left is refused after the lock check (an unlocked sample keeps
    its own message); nothing is stored or called."""
    p, counter = cproject
    rows = dev_rows(p)
    label_all(p, skip={rows[1]["record_id"]})
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "1")
    assert r.exit_code == 1 and "is not locked" in r.output
    lb.lock(p, "development")
    before = _state(p)
    r = cli(p, "screen", "compare", "run", "cmp1", "--config", "fake", "--repeats", "1")
    assert r.exit_code == 1 and "1 record without a final label" in r.output
    c = api(p)
    for url in ("/api/screen/compare", "/api/jobs/compare"):
        res = c.post(url, json={"name": "cmp1", "configs": ["fake"], "repeats": 1})
        assert res.status_code == 409 and res.json()["dev_incomplete"]["missing"] == 1, url
    with pytest.raises(DevSampleIncomplete):
        compare.run(fresh(p), "cmp1", None, ["fake"], repeats=1)
    assert counter["calls"] == 0 and _state(p) == before


def test_next_stage_is_labelling_while_incomplete(cproject):  # noqa: F811
    """Task 3.7: the recommended step is Samples and labelling in place of a later stage (screening) while the
    development sample is incomplete; an earlier ready stage keeps its place; once complete it moves on."""
    from wider.core.stages import next_stage, stage_states

    p, _ = cproject
    p.config.stage_providers["construct"] = "fake"
    p.save()
    rows = dev_rows(p)
    label_all(p, skip={rows[0]["record_id"]})

    def states(**over):
        # D32: open work of a done stage (pairs to decide) would take the next step; this test is about labelling
        return [{**s, "state": over.get(s["key"], "done" if s["state"] in ("ready", "blocked") else s["state"]),
                 "needs_you": None} for s in stage_states(fresh(p))]

    assert next_stage(fresh(p), states(screen="ready"))["key"] == "sample"
    assert next_stage(fresh(p), states(screen="ready", enrich="ready"))["key"] == "enrich"
    assert next_stage(fresh(p), states())["key"] == "sample"     # nothing ready: labelling still waits
    # scenario "No construct model, and open development labels", second half: `wider next` prints the command of
    # Samples and labelling (every stage before Screening done)
    import wider.core.stages as stages_mod
    ready = states(screen="ready")
    real = stages_mod.stage_states
    stages_mod.stage_states = lambda project, *a, **k: ready
    try:
        r = cli(p, "next")
    finally:
        stages_mod.stage_states = real
    assert r.exit_code == 0 and r.output.strip() == "wider sample status", r.output
    label_all(p)
    assert next_stage(fresh(p), states(screen="ready"))["key"] == "screen"
    assert next_stage(fresh(p), states()) is None


def _corpus_round(p) -> None:
    """A finished screening round on the whole corpus (its run folder names the scope); no call is stored."""
    import json

    d = p.root / "runs" / "screen_x__corpus"
    d.mkdir(parents=True)
    (d / "run_spec.json").write_text(json.dumps({"settings": {"scope": "corpus"}}))
    p.store.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) "
                    "VALUES('screen_x__corpus','screen','x','h','done','2026-01-01T00:00:00+00:00',?)",
                    (str(d.relative_to(p.root)),))


def test_extend_after_screening_warns(tmp_path):
    """Task 3.4: once a corpus round exists, extending the development sample first says that the added records
    hide development model output and refuse screening runs on them until labelled, and asks to confirm: nothing
    changes on Cancel, `--yes` confirms, the API answers 409 with the same sentence without `confirm: true`. With no
    corpus round nothing is asked."""
    from tests.unit.test_sample_boundary import _build, _client, _copy, _dev20, _label_first
    from tests.unit.test_sample_boundary import _cli as bcli
    from wider.core.project import Project
    from wider.sample.design import EXTEND_HIDES, ExtendNeedsConfirm, draw_probability, extend_development

    p, _ = _build(tmp_path)
    draw_probability(p)
    _dev20(p)
    _label_first(p, 12)
    root = p.root
    plain = _copy(p, tmp_path, "plain")                      # no corpus round: no question
    r = bcli(plain, "sample", "extend", "development")
    assert r.exit_code == 0 and "8 record(s) added" in r.output and "Until the" not in r.output, r.output
    p = Project.load(root)
    _corpus_round(p)
    api_copy = _copy(p, tmp_path, "api")
    p = Project.load(root)
    sentence = EXTEND_HIDES.format(n=8, s="s", are="are")
    assert sentence == ("Until the 8 added records are labelled, development model output is hidden and screening "
                        "runs on those records are refused.")

    def rows(q):
        return fresh(q).store.scalar("SELECT COUNT(*) FROM sample_rows")

    before = rows(p)
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    r = CliRunner().invoke(cli_app, ["sample", "extend", "development", "--dir", str(root)], input="n\n")   # Cancel
    out = " ".join(r.output.split())
    assert r.exit_code == 1 and isinstance(r.exception, (SystemExit, type(None))), r.exception   # no crash
    assert sentence in out and "Nothing was changed." in out and "8 record(s) added" not in out, r.output
    assert rows(p) == before
    with pytest.raises(ExtendNeedsConfirm) as e:
        extend_development(fresh(p), "development")
    assert str(e.value) == sentence and rows(p) == before
    c = _client(fresh(p))
    res = c.post("/api/samples/development/extend", json={"pool": "boundary"})
    assert res.status_code == 409 and res.json()["detail"] == sentence and res.json()["confirm_needed"] is True
    assert rows(p) == before
    r = bcli(fresh(p), "sample", "extend", "development", "--yes")
    assert r.exit_code == 0 and "8 record(s) added" in r.output, r.output
    assert rows(p) == before + 8
    res = _client(api_copy).post("/api/samples/development/extend", json={"pool": "boundary", "confirm": True})
    assert res.status_code == 200 and res.json()["added"] == 8


def test_locked_incomplete_sample_can_be_completed(cproject):  # noqa: F811
    """A development sample locked with labels left (allowed before this change): runs are refused, and the queue of
    "Continue labelling" (one reviewer, the sample's unlabelled records) offers the missing records on the locked
    sample, where a first label needs no reason (D22); then runs proceed."""
    p, counter = cproject
    rows = dev_rows(p)
    left = {rows[i]["record_id"] for i in (0, 3, 5)}
    label_all(p, skip=left)
    lb.lock(p, "development")
    r = cli(p, "screen", "run", DEV)
    assert r.exit_code == 1 and "3 records without a final label" in r.output
    q = api(p).get("/api/labels/queue", params={"reviewer": "alice", "samples": "development", "unlabelled": True})
    assert q.status_code == 200, q.text
    items = q.json()["items"] if isinstance(q.json(), dict) else q.json()
    assert len(items) == 3
    for rid in left:
        lb.label(p, "development", rid, "alice", "exclude", reason="a")
    assert cli(p, "screen", "run", DEV).exit_code == 0 and counter["calls"] == len(rows)


# ------------------------------------------------------------------------- 3.3 earlier output hidden, exports wait
def _early(p):
    """A project that ran the model before the sample became incomplete: the development sample labelled by alice, a
    development run, a comparison and protocol `main` on the corpus; then the protocol asks two reviewers, so every
    development record lacks a final label (the route a project upgraded to D4 takes). Returns the dev run id."""
    label_all(p)
    lb.lock(p, "development")
    run = screen(fresh(p), DEV)["run_id"]
    compare.run(fresh(p), "cmp1", None, ["fake"], repeats=1)
    from wider.screen.protocol import run_protocol

    run_protocol(fresh(p), "corpus", "main")
    from wider.screen.export import export_protocol

    export_protocol(fresh(p), "main")                      # an earlier export, written while the sample was complete
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 2)
    q.save()
    return run


def test_exports_and_totals(cproject):  # noqa: F811
    from wider.recall.outputs import export_all_references, export_corpus, export_references, write_package
    from wider.recall.report import share_bundle
    from wider.recall.selection import run_selection
    from wider.screen.devgate import export_wait
    from wider.screen.export import export_protocol

    p, _ = cproject
    run = _early(p)
    n = len(dev_rows(p))
    wait = export_wait(n)
    q = fresh(p)
    files = {f for f in (p.root / "exports").rglob("*") if f.is_file()}
    for fn in (lambda: export_references(q, "main"), lambda: export_all_references(q, "main"),
               lambda: run_selection(q, "main", dry_run=False), lambda: export_protocol(q, "main"),
               lambda: write_package(q, "main"), lambda: share_bundle(q, protocol="main")):
        with pytest.raises(DevSampleIncomplete, match=wait):
            fn()
    for args in (["export", "references", "--protocol", "main"], ["export", "references", "--all", "--protocol", "main"],
                 ["export", "selection", "--protocol", "main"], ["screen", "export", "main"],
                 ["export", "package", "--protocol", "main"], ["report", "--bundle"]):
        r = cli(p, *args)
        assert r.exit_code == 1 and wait in " ".join(r.output.split()), (args, r.output)
    c = api(p)
    for method, url in (("post", "/api/outputs/references?protocol=main"),
                        ("post", "/api/outputs/references?protocol=main&all=true"),
                        ("post", "/api/outputs/selection?protocol=main"), ("post", "/api/screen/export?protocol=main"),
                        ("post", "/api/outputs/package?protocol=main"), ("post", "/api/report/bundle?protocol=main")):
        res = getattr(c, method)(url)
        assert res.status_code == 409 and wait in res.json()["detail"], (url, res.text)
    assert {f for f in (p.root / "exports").rglob("*") if f.is_file() and "report" not in f.name} == \
        {f for f in files if "report" not in f.name}          # nothing written (the report itself is allowed)
    # the Results page's counts, asked on load, answer the same sentence quietly (no 409 in the console)
    for url in ("/api/outputs/selection?protocol=main&format=ris",
                "/api/outputs/selection?protocol=main&format=ris&package=true&add=selection"):
        res = c.get(url)
        assert res.status_code == 200 and res.json()["ready"] is False and wait in res.json()["message"], (url, res.text)
    res = c.get("/api/outputs/file", params={"path": "exports/screening/main_decisions.csv"})
    assert res.status_code == 409 and wait in res.json()["detail"]
    res = c.get(f"/api/screen/calls/{run}")
    calls = res.json()
    dev = {r["record_id"] for r in dev_rows(p)}
    assert not dev & {x["record_id"] for x in calls}       # no development record's reads
    assert res.headers.get("X-Hidden", "").startswith("Model decisions on development records are hidden")  # no count
    out = export_corpus(fresh(p))                           # the plain corpus export writes every unique record
    assert out["n"] == fresh(p).store.scalar("SELECT COUNT(*) FROM records")
    assert c.get("/api/outputs/file", params={"path": "exports/references/corpus.csv"}).status_code == 200
    for rid in sorted(dev):                                 # complete: everything is back
        lb.label(fresh(p), "development", rid, "bob",
                 next(lb_["decision"] for lb_ in lb.final_labels(fresh(p), "development") if lb_["record_id"] == rid)
                 or "exclude", reason="a")
    assert export_references(fresh(p), "main")["n"] >= 0
    assert api(p).get("/api/outputs/file", params={"path": "exports/screening/main_decisions.csv"}).status_code == 200


def test_earlier_output_hidden_not_deleted(cproject):  # noqa: F811
    from wider.screen.devgate import HIDDEN

    p, _ = cproject
    _early(p)
    HIDDEN_SOME = HIDDEN.format(n=len(dev_rows(fresh(p))))   # the count of the sample's records is named
    assert HIDDEN_SOME.startswith(f"Model decisions on {len(dev_rows(fresh(p)))} development records")
    stored = fresh(p).store.scalar("SELECT COUNT(*) FROM screen_calls")
    res = compare.results(fresh(p), "cmp1")
    assert res["hidden"] == HIDDEN_SOME and all(not c.get("misses_text") for c in res["configs"])
    r = cli(p, "screen", "compare", "show", "cmp1")
    out = " ".join(r.output.split())
    assert r.exit_code == 0 and HIDDEN_SOME in out and "misses" not in out, r.output
    j = api(p).get("/api/screen/compare/cmp1").json()
    assert j["hidden"] == HIDDEN_SOME and "misses" not in str(j["configs"])
    assert fresh(p).store.scalar("SELECT COUNT(*) FROM screen_calls") == stored     # nothing deleted
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    res = compare.results(fresh(p), "cmp1")
    assert not res.get("hidden") and res["configs"][0]["misses_text"]


def test_report_hides_comparisons(cproject):  # noqa: F811
    import json

    from wider.recall.report import write_report
    from wider.screen.devgate import REPORT_HIDDEN

    p, _ = cproject
    _early(p)
    md, js = write_report(fresh(p), protocol="main")
    text = md.read_text()
    assert REPORT_HIDDEN in text and "misses" not in text.split("## Configuration comparisons", 1)[1].split("##", 1)[0]
    rep = json.loads(js.read_text())
    assert rep["comparisons_hidden"] is True and rep["comparisons"] == [REPORT_HIDDEN]
    assert rep["screening"]["protocols"]                    # totals unchanged
    assert api(p).get("/api/outputs/file", params={"path": "exports/report.md"}).status_code == 200


def test_upgraded_project_told(cproject):  # noqa: F811
    """Scenario "An upgraded project is told" (its CLI and API part; the Overview rendering is
    test_shell_js_node.py::test_overview_dev_gate_item): a project with model decisions on development records that
    are now without a final label gets the Needs attention item saying runs wait and why, in `wider status` and the
    project summary; a project with open labels and no model output on them, or with a complete sample, gets none."""
    from wider.core.summary import project_summary
    from wider.screen.devgate import GATE_TEXT

    p, _ = cproject
    assert project_summary(fresh(p))["dev_gate"] is None              # labels open, no model output yet
    _early(p)
    item = project_summary(fresh(p))["dev_gate"]
    assert item == {"stage": "sample", "title": "Samples and labelling", "kind": "dev_gate", "text": GATE_TEXT}
    assert "wait until the development sample is complete" in GATE_TEXT and "hidden" in GATE_TEXT
    assert not any(ch.isdigit() for ch in GATE_TEXT)                  # no development count (blinding)
    assert api(p).get("/api/project/summary").json()["dev_gate"] == item
    out = " ".join(cli(p, "status").output.split())
    assert f"needs you: Samples and labelling: {GATE_TEXT}" in out
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    assert project_summary(fresh(p))["dev_gate"] is None


def test_status_unchanged_by_a_development_label(cproject):  # noqa: F811
    """`wider status` names no development count (W4): its whole output, the store line included, is the same before
    and after one more development label while the sample stays open; complete, the label count is back."""
    p, _ = cproject
    rows = dev_rows(p)
    label_all(p, skip={rows[0]["record_id"], rows[1]["record_id"], rows[2]["record_id"]})
    before = cli(p, "status").output
    lb.label(fresh(p), "development", rows[0]["record_id"], "alice", "exclude")
    assert cli(p, "status").output == before
    assert not re.search(r"store: .*\blabels \d", before)
    label_all(p)
    assert re.search(r"store: .*\blabels \d", cli(p, "status").output)


def test_samples_not_completed_while_labels_open(cproject):  # noqa: F811
    """Samples and labelling is not called completed while a development sample is open (tester finding; the spec is
    silent): the stage keeps ``done`` (the samples are drawn) with ``needs_you`` "development labels open", no count,
    in the stage list (sidebar "needs attention") and in `wider status`; complete, the words go."""
    from wider.core.stages import stage_states

    p, _ = cproject
    rows = dev_rows(p)
    label_all(p, skip={rows[0]["record_id"]})
    s = next(s for s in stage_states(fresh(p)) if s["key"] == "sample")
    assert s["state"] == "done" and s["needs_you"] == "development labels open"
    out = " ".join(cli(p, "status").output.split())
    assert "done, needs you: development labels open" in out
    assert "needs you: Samples and labelling: development labels open" in out and "needs you: needs you" not in out \
        and ": needs you: development" not in out   # the Needs attention line says "needs you" once
    label_all(p)
    s = next(s for s in stage_states(fresh(p)) if s["key"] == "sample")
    assert s["state"] == "done" and s["needs_you"] is None


def test_exports_without_model_decisions_go_ahead(tmp_path):
    """Only an export with model decisions waits: a project with an open development sample and no screening decision
    (stored or imported) writes its replication package, and an earlier file under exports/ is served."""
    from tests.unit.test_dev_reviewers import _project
    from wider.recall.outputs import write_package
    from wider.screen.devgate import check_export, file_waits, open_samples

    p = _project(tmp_path / "p")
    assert open_samples(p) and not p.store.scalar("SELECT COUNT(*) FROM screen_decisions")
    check_export(p)                                    # no refusal
    assert write_package(p)                            # written, not refused
    assert not file_waits(p, "exports/screening/main_decisions.csv")


def test_round_counts_hidden(cproject):  # noqa: F811
    """A round on development records names no include/maybe/exclude counts while the sample is incomplete: the
    Screening round card (/api/screen), `wider screen status` and the progress rounds say the decisions are hidden;
    complete, the counts are back. A round on no development record keeps its counts."""
    from wider.screen.devgate import HIDDEN_SOME
    from wider.screen.progress import rounds

    p, _ = cproject
    run = _early(p)
    r = next(x for x in rounds(fresh(p)) if x["run_id"] == run)
    assert r["hidden"] == HIDDEN_SOME and r["include"] is None and r["maybe"] is None and r["exclude"] is None
    assert not r["unusual"]
    j = next(x for x in api(p).get("/api/screen").json()["runs"] if x["run_id"] == run)
    assert j["hidden"] == HIDDEN_SOME and j["inc"] is None and j["may"] is None and j["exc"] is None
    out = " ".join(cli(p, "screen", "status").output.split())
    assert "Latest round" in out and HIDDEN_SOME in out and " include," not in out, out   # the corpus round too
    for rid in sorted({x["record_id"] for x in dev_rows(p)}):
        lb.label(fresh(p), "development", rid, "bob",
                 next(x["decision"] for x in lb.final_labels(fresh(p), "development") if x["record_id"] == rid)
                 or "exclude", reason="a")
    r = next(x for x in rounds(fresh(p)) if x["run_id"] == run)
    assert not r.get("hidden") and r["include"] is not None
    j = next(x for x in api(p).get("/api/screen").json()["runs"] if x["run_id"] == run)
    assert not j.get("hidden") and j["inc"] is not None


def test_round_card_shows_hidden_sentence():
    """The page's round card shows the hidden sentence in place of the counts."""
    import json
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    src = (Path(__file__).resolve().parents[2] / "wider" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    start = src.index("function scRoundHtml(r)")
    body = src[start:src.index("\n}\n", start) + 3]
    r = {"name": "Development sample", "status": "done", "ok": 4, "n": 4, "n_items": 4, "inc": None, "may": None,
         "exc": None, "hidden": "Model decisions on development records are hidden until every record has a final label"}
    script = ("const esc = s => String(s ?? ''); const scKindBadges = () => ''; const scInt = n => String(n);"
              " const SC_RUN_STATUS = {}; const usageChip = () => ''; const usageText = () => ''; const fmtWhen = s => String(s ?? '');\n"
              f"{body}\nconsole.log(scRoundHtml({json.dumps(r)}));")
    res = subprocess.run([node, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    assert r["hidden"] in res.stdout and "Include · maybe · exclude" not in res.stdout


def test_review_hides_development_records(cproject):  # noqa: F811
    """Screening review (D4): while the development sample is incomplete its records leave the review list with a
    sentence saying how many and why; `wider review show` and the drawer route give no read, outcome or final
    decision; a review decision on one is refused. Complete, they are back."""
    from wider.screen import review
    from wider.screen.devgate import HIDDEN

    p, _ = cproject
    _early(p)
    dev = {r["record_id"] for r in dev_rows(p)}
    res = review.flagged(fresh(p), "main", show="all")
    assert not dev & {r["record_id"] for r in res["rows"]}
    k = res["n_hidden"]
    assert k == len(dev) and res["hidden"] == HIDDEN.format(n=k)
    out = " ".join(cli(p, "review", "list", "main", "--show", "all").output.split())
    assert HIDDEN.format(n=k) in out
    rid = min(dev)
    v = review.record_view(fresh(p), "main", None, rid)
    assert v["hidden"] == HIDDEN.format(n=k) and v["reads"] == [] and v["final"] is None and v["llm_outcome"] is None
    assert v["decision"] is None and v["route"] is None
    j = api(p).get(f"/api/review/{rid}", params={"protocol": "main"}).json()
    assert j["hidden"] and not j["reads"] and j["final"] is None and j["decision"] is None
    r = cli(p, "review", "show", str(rid), "main")
    o = " ".join(r.output.split())
    assert HIDDEN.format(n=k) in o and "Final:" not in o and "pass A" not in o, r.output
    with pytest.raises(review.Refusal, match="hidden"):
        review.decide(fresh(p), "main", None, rid, "include", by="alice")
    assert api(p).post(f"/api/review/{rid}", params={"protocol": "main"},
                       json={"decision": "include"}, headers={"X-Wider-Author": "alice"}).status_code == 409
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    res = review.flagged(fresh(p), "main", show="all")
    assert dev <= {r["record_id"] for r in res["rows"]} and not res["hidden"]
    assert review.record_view(fresh(p), "main", None, rid)["reads"]


def test_review_page_hides_development_records(cproject):  # noqa: F811
    """review.js under Node with the real route replies: the list says how many development records are hidden; a
    list loaded before the sample became incomplete opens a hidden record with its title and the sentence only: no
    read, outcome, final decision or decision buttons."""
    from tests.unit.test_review_js_node import page, text
    from wider.screen.devgate import HIDDEN

    p, _ = cproject
    _early(p)
    c = api(p)
    lst = c.get("/api/review", params={"protocol": "main", "show": "all"}).json()
    rid = min(r["record_id"] for r in dev_rows(p))
    stale = {**lst, "rows": [{**lst["rows"][0], "record_id": rid}] + lst["rows"]}
    replies = {"/api/review": stale, "/api/review/criteria": c.get("/api/review/criteria").json(),
               f"/api/review/{rid}": c.get(f"/api/review/{rid}", params={"protocol": "main"}).json()}
    out = page("""await Review.open(0, true); await flush();
      const hid = $("#rv-hidden");
      console.log(JSON.stringify({ hidden: hid.textContent, shown: !hid.hidden, drawer: $("#rv-drawer").innerHTML }));""",
               replies, store={"wider.reviewer": "ana"})
    n = lst["n_hidden"]
    assert out["shown"] and out["hidden"] == HIDDEN.format(n=n) + "."
    d = text(out["drawer"])
    assert HIDDEN.format(n=n) in d and "data-rv-decide" not in out["drawer"]
    assert "Final" not in d and "pass A" not in d and "AI model outcome" not in d


def test_questions_hide_model_reads(cproject):  # noqa: F811
    """A screening question about a development record holds the model's reads (D4): while the sample is incomplete
    it leaves the Questions list, its counts and the dialog's latest id; the page and `wider questions list` say why
    (with the development count, not how many questions); answering or dismissing it is refused. Complete, it is
    back. A question about another record stays."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from wider.core import questions as qs
    from wider.screen.devgate import HIDDEN

    p, _ = cproject
    _early(p)
    dev = sorted(r["record_id"] for r in dev_rows(p))
    other = p.store.scalar(f"SELECT MIN(record_id) FROM records WHERE record_id NOT IN ({','.join('?' * len(dev))})",
                           tuple(dev))
    q = fresh(p)
    hid = qs.raise_question(q, "screen_split", "main:dev", "Synthetic dev", "The three reads disagree (include, maybe, "
                            "exclude)", context={"votes": ["include", "maybe", "exclude"]}, record_id=dev[0], run_ref="main")
    keep = qs.raise_question(q, "screen_split", "main:other", "Synthetic other", "The three reads disagree",
                             context={}, record_id=other, run_ref="main")
    line = HIDDEN.format(n=len(dev)) + "; their screening questions wait until then"
    q = fresh(p)
    assert [x["question_id"] for x in qs.list_questions(q)] == [keep] and qs.counts(q)["open"] == 1
    assert qs.hidden_line(q) == line
    for fn in (lambda: qs.answer(fresh(p), hid, "include", by="ana"), lambda: qs.dismiss(fresh(p), hid, by="ana")):
        with pytest.raises(ValueError, match="hidden"):
            fn()
    c = api(p)
    j = c.get("/api/questions").json()
    assert [x["question_id"] for x in j["questions"]] == [keep] and j["hidden"] == line and j["counts"]["open"] == 1
    assert c.get("/api/questions/summary").json() == {**c.get("/api/questions/summary").json(), "open": 1,
                                                      "latest_open": keep}
    assert c.post(f"/api/questions/{hid}/answer", json={"choice": "include", "by": "ana"}).status_code == 400
    out = " ".join(cli(p, "questions", "list").output.split())
    assert line + "." in out and "Synthetic dev" not in out and "Synthetic other" in out
    # the page: the sentence in normal text, no card of the hidden question
    js = (STATIC / "questions.js").read_text(encoding="utf-8")
    res = run_node(f"""{PAGE}\n{ESC}\nglobal.setInterval = () => 0; global.fmtWhen = (x) => String(x); document.querySelectorAll = () => [];
      replies["/api/questions"] = {json.dumps(j)}; replies["/api/decisions"] = {{ decisions: [], lines: [] }};
      replies["/api/questions/summary"] = {{ open: 1 }};
      {js}\nawait loadQuestions(); await flush();
      console.log(JSON.stringify({{ open: $("#q-open").innerHTML }})); process.exit(0);""")
    assert f'<p class="card" id="q-hidden" role="note">{line}.</p>' in res["open"]
    assert f'data-qid="{hid}"' not in res["open"] and f'data-qid="{keep}"' in res["open"]
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    q = fresh(p)
    assert {x["question_id"] for x in qs.list_questions(q)} == {hid, keep} and qs.hidden_line(q) is None


def test_result_set_counts_leave_out_development_records(cproject):  # noqa: F811
    """The result sets (`wider screen sets list`, GET /api/screen/sets and the sets card): while the development
    sample is incomplete the include/maybe/exclude counts leave its records out and say how many; complete, they
    count them again."""
    from tests.unit.test_backends_js_node import backends
    from wider.screen import result_sets as rs
    from wider.screen.devgate import HIDDEN

    p, _ = cproject
    _early(p)
    dev = {r["record_id"] for r in dev_rows(p)}
    q = fresh(p)
    key = rs.active_key(q, "main")
    total = q.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (key,))
    k = q.store.scalar(f"SELECT COUNT(*) FROM screen_decisions WHERE protocol=? AND record_id IN "
                       f"({','.join('?' * len(dev))})", (key, *dev))
    assert k
    s = next(x for x in rs.sets(q, "main") if x["key"] == key)
    assert s["n"] == total - k and s["include"] + s["maybe"] + s["exclude"] == total - k
    assert s["hidden"] == HIDDEN.format(n=k)
    j = api(p).get("/api/screen/sets", params={"protocol": "main"}).json()
    assert next(x for x in j["sets"] if x["key"] == key)["hidden"] == s["hidden"]
    out = " ".join(cli(p, "screen", "sets", "list", "main").output.split())
    assert f"{s['hidden']}; the counts leave them out." in out
    html = backends(f'console.log(JSON.stringify({{ h: Backends.setsCardHtml("main", {json.dumps(j["sets"], default=str)}) }}));')["h"]
    assert f'<p role="note">{s["hidden"]}; the counts leave them out.</p>' in html
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    s = next(x for x in rs.sets(fresh(p), "main") if x["key"] == key)
    assert s["hidden"] is None, s
    assert s["n"] == total, (s, fresh(p).store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (key,)))


def test_counts_leave_out_development_records(cproject):  # noqa: F811
    """The models summary (page, CLI, API), a run's progress counts and the agreement table leave the hidden records'
    decisions out and say so without a count; the routes (PRISMA's counts) stay; back once the sample is complete."""
    from wider.screen.agreement import markdown, pass_agreement
    from wider.screen.devgate import LEFT_OUT
    from wider.screen.models_summary import by_model

    p, _ = cproject
    run = _early(p)
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)
    q.save()
    full_m, full_a = by_model(fresh(p), protocol="main"), pass_agreement(fresh(p), "main")
    full_r = api(p).get(f"/api/screen/progress/{run}").json()
    assert full_m.get("hidden") is None and full_a.get("hidden") is None and full_r.get("hidden") is None
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 2)
    q.save()

    def decided(s):
        return sum(m["include"] + m["maybe"] + m["exclude"] for m in s["models"])

    m = by_model(fresh(p), protocol="main")
    assert m["hidden"] == LEFT_OUT and decided(m) < decided(full_m)
    assert not any(ch.isdigit() for ch in LEFT_OUT)                  # no count named
    assert api(p).get("/api/screen/models", params={"protocol": "main"}).json()["hidden"] == LEFT_OUT
    out = " ".join(cli(p, "screen", "models", "--protocol", "main").output.split())
    assert LEFT_OUT in out
    from tests.unit.test_backends_js_node import backends

    html = backends(f"console.log(JSON.stringify(Backends.modelsCardHtml({json.dumps(m, default=str)})));")
    assert f'<p role="note" data-models-hidden>{LEFT_OUT}.</p>' in html     # the models card, in normal text
    r = api(p).get(f"/api/screen/progress/{run}").json()             # the development run: every call is hidden
    assert r["hidden"] == LEFT_OUT and not (r["inc"] or r["may"] or r["exc"]) and r["n"] == full_r["n"]
    a = pass_agreement(fresh(p), "main")
    assert a["hidden"] == LEFT_OUT and a["n"] < full_a["n"]
    assert a["routes"] == full_a["routes"]                            # how decisions were reached: PRISMA's counts
    assert LEFT_OUT in " ".join(markdown(a))


def test_imported_decisions_hidden(tmp_path):
    """Decisions imported with `wider screen import-decisions` (origin import) are allowed; on records of an
    incomplete purposive sample they are hidden (record view, result-set counts) and their exports wait."""
    from tests.unit.test_dev_reviewers import _project
    from wider.screen import result_sets as rs
    from wider.screen import review
    from wider.screen.devgate import HIDDEN, check_export, gate_item

    p = _project(tmp_path / "p")
    f = tmp_path / "decisions.csv"
    f.write_text("title,final_decision\n" + "".join(f"Synthetic paper {i},include\n" for i in (1, 2, 5)))
    r = cli(p, "screen", "import-decisions", "main", str(f), "--author", "ana")
    assert r.exit_code == 0, r.output                                 # allowed
    q = fresh(p)
    assert q.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol LIKE 'main%'") == 7
    v = review.record_view(q, "main", None, 1)
    assert v["hidden"] == HIDDEN.format(n=4) and v["decision"] is None
    s = rs.sets(fresh(p), "main")[0]
    assert s["hidden"] == HIDDEN.format(n=4)
    assert s["include"] == 1 and s["n"] == 3                        # record 5 only; 1 and 2 are left out
    with pytest.raises(DevSampleIncomplete):
        check_export(fresh(p))                                        # their exports wait
    assert gate_item(fresh(p))                                        # the Needs attention item names the gate
    for rid in range(1, 5):
        lb.label(fresh(p), "development", rid, "ana", "exclude")
    assert review.record_view(fresh(p), "main", None, 1)["hidden"] is None
    check_export(fresh(p))


def test_output_list_marks_waiting_files(cproject):  # noqa: F811
    """An earlier file with model decisions is marked as waiting in the Results output list (page and `wider
    results`), with no link; files without model decisions keep theirs."""
    from tests.unit.test_results_js_node import html_of, text
    from wider.recall import dashboard as dash
    from wider.screen.devgate import FILE_WAITS

    p, _ = cproject
    _early(p)
    (p.root / "exports" / "report.md").write_text("# report\n")
    (p.root / "exports" / "report.json").write_text("{}")             # comparisons not hidden: it waits
    (p.root / "exports" / "prisma_s.md").write_text("PRISMA-S\n")      # no model decisions
    d = dash.dashboard(fresh(p), "main")
    docs = {x["path"]: x for x in d["documents"]}
    assert docs["exports/report.md"]["waits"] is True and docs["exports/prisma_s.md"]["waits"] is False
    out = " ".join(cli(p, "results", "main").output.split())
    assert f"Report: exports/report.md, {FILE_WAITS}" in out
    html = html_of(d)
    row = html[html.index('data-doc="exports/report.md"'):].split("</tr>")[0]
    assert FILE_WAITS in text(row) and "/api/outputs/file" not in row and "muted" not in row
    assert "/api/outputs/file" in html[html.index('data-doc="exports/prisma_s.md"'):].split("</tr>")[0]


def test_set_comparison_leaves_out_development_records(tmp_path):
    """Comparing two result sets (page, CLI, API) leaves the hidden records out of the table and the list of records
    that differ, and says so; back once the sample is complete."""
    from tests.unit.test_backends_js_node import backends
    from tests.unit.test_dev_reviewers import _project
    from wider.screen import result_sets as rs
    from wider.screen.devgate import LEFT_OUT_LIST

    p = _project(tmp_path / "p")
    for name, dec in (("a.csv", "include"), ("b.csv", "maybe")):
        f = tmp_path / name
        f.write_text("title,final_decision\n" + "".join(f"Synthetic paper {i},{dec}\n" for i in (1, 5)))
        assert cli(p, "screen", "import-decisions", "main", str(f), "--author", "ana").exit_code == 0
    c = rs.compare(fresh(p), "main", 1, 2)
    assert c["hidden"] == LEFT_OUT_LIST and [d["record_id"] for d in c["differ"]] == [5] and c["n"] == 3
    j = api(p).get("/api/screen/sets/main/compare", params={"a": 1, "b": 2}).json()
    assert j["hidden"] == LEFT_OUT_LIST and 1 not in [d["record_id"] for d in j["differ"]]
    out = " ".join(cli(p, "screen", "sets", "compare", "main", "1", "2").output.split())
    assert LEFT_OUT_LIST in out and "Synthetic paper 1 " not in out + " "
    html = backends(f"console.log(JSON.stringify(Backends.compareHtml({json.dumps(c)})));")
    assert f'<p role="note" data-compare-hidden>{LEFT_OUT_LIST}.</p>' in html
    for rid in range(1, 5):
        lb.label(fresh(p), "development", rid, "ana", "exclude")
    c = rs.compare(fresh(p), "main", 1, 2)
    assert c["hidden"] is None and [d["record_id"] for d in c["differ"]] == [1, 5]


def test_blind_drawer_same_for_dev_and_validation(cproject):  # noqa: F811
    """The blind labelling drawer of a development record, whose model output is hidden, looks like the drawer of a
    validation record: the same fields from the queue, no model block, no line saying something is hidden (D4)."""
    from tests.unit.test_drawer_keys_node import run_node
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
    from wider.ui.app import STATIC

    p, _ = cproject
    _early(p)
    c = api(p)
    dev_q = c.get("/api/labels/queue", params={"reviewer": "bo", "samples": "development"}).json()
    val_q = c.get("/api/labels/queue", params={"reviewer": "bo", "samples": "validation"}).json()
    assert dev_q and val_q
    assert {tuple(sorted(r)) for r in dev_q} == {tuple(sorted(r)) for r in val_q}   # the same fields
    for r in dev_q + val_q:
        assert not {"parsed", "rule_decision", "raw_decision", "hidden", "sample", "band", "rank"} & set(r), r
    # the record route the drawer reads has no model output either
    rec = c.get(f"/api/records/{dev_q[0]['record_id']}").json()
    assert not {"parsed", "rule_decision", "decision", "hidden"} & set(rec["record"])
    rows = [dev_q[0], val_q[0]]
    out = run_node(QUEUE_PAGE + queue_name("bo") + (STATIC / "records.js").read_text(encoding="utf-8") + f"""
const rows = {json.dumps(rows)};
apiReply = (p) => p.startsWith("/api/labels/queue") ? rows.map((r) => ({{ ...r }})) : {{}};
$("#lb-load").onclick(); await flush(); await flush();
const cfg = opened[0];
const u = {{ recordBlock: (r, quotes) => "<h2>T</h2>" + JSON.stringify(quotes), h: esc, call: (p) => api(p) }};
const html = [];
for (const it of cfg.items) html.push(await cfg.render(it, u));
console.log(JSON.stringify({{ html, label: cfg.label, blind: cfg.blind }}));
""")
    assert out["blind"] is True and "blind" in out["label"]
    dev_html, val_html = out["html"]
    assert dev_html == val_html                             # nothing tells a development record apart
    assert "<h2>T</h2>[]" in dev_html                       # no model quotes highlighted
    for word in ("Model", "model", "hidden", "development"):
        assert word not in dev_html, word


# Every module that reads model decisions (screen_calls, screen_decisions), and what it does while the development
# sample is open (D4): "hide" leaves the development records out and says so; "refuse" refuses the whole request;
# "total" reads them only into totals, estimates or PRISMA counts that name no record and no model decision of one;
# "internal" is storage, the run itself or the gate. A new reader fails this test until it is placed here.
READERS = {
    "wider/core/questions.py": "hide", "wider/core/summary.py": "hide", "wider/recall/report.py": "hide",
    "wider/screen/agreement.py": "hide", "wider/screen/models_summary.py": "hide", "wider/screen/progress.py": "hide",
    "wider/screen/result_sets.py": "hide", "wider/screen/review.py": "hide", "wider/ui/app.py": "hide",
    "wider/screen/adjudication.py": "hide",
    "wider/recall/outputs.py": "refuse", "wider/recall/selection.py": "refuse", "wider/screen/compare.py": "refuse",
    "wider/screen/export.py": "refuse",
    "wider/core/estimate.py": "total", "wider/core/usage.py": "total", "wider/recall/adjacent.py": "total",
    "wider/recall/estimate.py": "total", "wider/recall/independent.py": "total", "wider/screen/decisions.py": "total",
    "wider/sample/package.py": "total",
    "wider/core/store.py": "internal", "wider/dedup/engine.py": "internal", "wider/screen/devgate.py": "internal", "wider/screen/engine.py": "internal",
    "wider/screen/protocol.py": "internal",
}


def test_every_reader_of_model_decisions_is_placed():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pat = re.compile(r"(FROM|JOIN)\s+screen_(calls|decisions)\b")
    found = {f.relative_to(root).as_posix() for f in (root / "wider").rglob("*.py")
             if pat.search(f.read_text(encoding="utf-8"))}
    assert found == set(READERS), (sorted(found - set(READERS)), sorted(set(READERS) - found))
    for rel, how in READERS.items():                       # a hiding or refusing reader asks the gate
        if how in ("hide", "refuse"):
            assert "devgate" in (root / rel).read_text(encoding="utf-8"), rel


def test_open_validation_sample_switch(cproject, monkeypatch):  # noqa: F811
    """Owner question 28b0 (not answered): by default a record of an unlocked validation sample is listed on Screening
    review with its reads (option a, counted afterwards); OPEN_SAMPLE_READS "hide" lists it without reads (option b)."""
    from wider.screen import review

    p, _ = cproject
    _early(p)
    q = fresh(p)
    q.config.protocol = q.config.protocol.set("sampling.development_reviewers", 1)   # the development sample complete
    q.save()
    val = {r[0] for r in fresh(p).store.query(
        "SELECT r.record_id FROM sample_rows r JOIN samples s USING(sample_id) WHERE s.name='validation'")}
    assert review.OPEN_SAMPLE_READS == "show"
    res = review.flagged(fresh(p), "main", show="all")
    shown = val & {r["record_id"] for r in res["rows"]}
    assert shown and not res["hidden"]
    rid = min(shown)
    assert review.record_view(fresh(p), "main", None, rid)["reads"]
    monkeypatch.setattr(review, "OPEN_SAMPLE_READS", "hide")
    res = review.flagged(fresh(p), "main", show="all")
    assert not val & {r["record_id"] for r in res["rows"]}
    assert res["hidden"].endswith("records of a sample not locked yet are hidden until it is locked")
    v = review.record_view(fresh(p), "main", None, rid)
    assert not v["reads"] and v["hidden"]
