"""Query refinement (vision change 2a): the frozen query, unlocks, and the researcher's choice when another version
is frozen (D2, D21). Synthetic projects only; nothing leaves the computer."""
import json
import os

import rich
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_outputs import in_every_output, package_json, unlock_json, validity_everywhere
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import journal
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.core.stages import stage_states
from wider.search import coverage
from wider.ui.app import create_app

SYN = {"blocks": [{"key": "a", "name": "Meetings", "terms": ["meeting*", "huddle*"]}], "logic": {"and": ["a"]}}
RIS = "TY  - JOUR\nTI  - Team meetings after errors\nPY  - 2020\nDO  - 10.1000/abc\nAB  - A synthetic abstract.\nER  -\n"


def _syn(term: str) -> dict:
    return {"blocks": [{"key": "a", "name": "Meetings", "terms": ["meeting*", term]}], "logic": {"and": ["a"]}}


def _proj(tmp_path, n_val: int = 2, name: str = "r") -> Project:
    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="Development paper", doi="10.1000/dev", set="Development")]
                      + [BenchmarkPaper(benchmark_id=f"V{i:03d}", title=f"Validation paper {i}", doi=f"10.1000/val{i}",
                                        set="Validation") for i in range(1, n_val + 1)])
    return p


def _versions(p, *terms):
    for t in terms:
        artifacts.save_new_version(p, "synonyms", _syn(t), origin="manual")


# undo, redo and the review-package commands never sign with the login name ($USER); the person at these tests is
# the one whose login name signed the engine calls (current_author's inferred fallback)
ME = {"WIDER_REVIEWER": os.environ.get("USER") or ""}


def _cli(p, *args):
    # rich's shared console takes its width from COLUMNS once, when the first test in the run prints; set it here
    con = rich.get_console()
    old, con.width = con._width, 1000
    try:
        return CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "1000", **ME})
    finally:
        con._width = old


def _invoke_wide(args, env):
    # the same wide console as _cli, for commands that take no project; under xdist COLUMNS alone does not reach it
    con = rich.get_console()
    old, con.width = con._width, 1000
    try:
        return CliRunner().invoke(cli_app, args, env=env)
    finally:
        con._width = old


def _client(p):
    return TestClient(create_app(Project.load(p.root)))


def _unlocks(p):
    return coverage.unlocks(p)


def _changes(p, kind=None):
    return journal.changes(p, kind)


def _state(p, key):
    return next(s["state"] for s in stage_states(p) if s["key"] == key)


def _import_one(p, tmp_path, name="s.ris"):
    from wider.ingest.importer import add_file, import_all

    (tmp_path / name).write_text(RIS, encoding="utf-8")
    add_file(p, "scopus", tmp_path / name)
    import_all(p)


def test_save_after_freeze_reopens_the_query(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "huddle*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    head = "v1 is frozen. Saving v2 reopens the query; 2 validation papers have been shown."
    want = f"{head} {artifacts.CHOICE_TAIL}"
    c = _client(p)
    assert c.get("/api/construct/synonyms").json()["save_notice"] == want   # the page's confirm step shows it first
    f = tmp_path / "v2.yaml"
    f.write_text(yaml.safe_dump(_syn("standup*")), encoding="utf-8")
    r = _cli(p, "construct", "save", "synonyms", str(f), "--count-seen")
    assert r.exit_code == 0, r.output
    assert head in r.output and artifacts.outcome_line("count", 2) in r.output and "synonyms v2" in r.output
    u = _unlocks(p)
    assert [(x["cause"], x["from_version"], x["new_version"], x["frozen_no"], x["counts_as_seen"]) for x in u] == [
        ("save", 1, 2, 1, 1)]
    assert {s["benchmark_id"] for s in u[0]["seen"]} == {"B001", "V001", "V002"}
    assert set(u[0]["seen"][0]) == {"benchmark_id", "doi", "title_norm", "set"}
    ch = _changes(p, "artifact_unlock")
    assert [x["summary"] for x in ch] == ["query reopened: v2 saved after v1 was frozen (counted as seen)"]
    assert ch[0]["kind_label"] == "query unlocked" and not ch[0]["undoable"] and ch[0]["why_not"] == "an unlock is final"
    assert artifacts.frozen_query(p) is None and coverage.validation_papers(p) == []
    # a second save (the page) adds no row and no notice
    r = c.post("/api/construct/synonyms/save", json={"text": yaml.safe_dump(_syn("briefing*"))})
    assert r.status_code == 200 and r.json()["notice"] is None
    assert len(_unlocks(p)) == 1 and c.get("/api/construct/synonyms").json()["save_notice"] is None
    assert [(x["cause"], x["from_version"], x["new_version"], x["counts_as_seen"]) for x in unlock_json(p)] == [
        ("save", 1, 2, 1)]
    # every path into a new version records the save while a frozen query stands: restore, query import, propose
    from wider.providers import fake as fakemod

    def refreeze():
        v = artifacts.latest(p, "synonyms")["version"]
        artifacts.set_status(p, "synonyms", v, "frozen")
        return v

    v = refreeze()
    r = c.post("/api/construct/synonyms/1/restore")
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == f"v{v} is frozen. Saving v{r.json()['version']} reopens the query; no validation paper has been seen yet."
    v = refreeze()
    r = _cli(p, "construct", "restore", "synonyms", "1")
    assert r.exit_code == 0 and f"v{v} is frozen. Saving v{v + 1} reopens the query" in r.output
    v = refreeze()
    r = c.post("/api/query/import", json={"text": "TI (meeting*)"})
    assert r.status_code == 200, r.text
    assert r.json()["notice"].startswith(f"v{v} is frozen. Saving v{v + 1} reopens the query")
    v = refreeze()
    (tmp_path / "q.txt").write_text("TI (huddle*)", encoding="utf-8")
    r = _cli(p, "query", "import", str(tmp_path / "q.txt"))
    assert r.exit_code == 0 and f"v{v} is frozen. Saving v{v + 1} reopens the query" in r.output, r.output
    v = refreeze()
    from wider.construct.propose import propose
    from wider.core.models import ProviderConfig

    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.save()
    orig = fakemod.FakeProvider.__init__
    fakemod.FakeProvider.__init__ = lambda self, cfg, responder_=None: orig(self, cfg, lambda req: _syn("propos*"))
    try:
        row = propose(p, "synonyms", provider_name="fake")
        assert row["notice"].startswith(f"v{v} is frozen. Saving v{row['version']} reopens the query")
        v = refreeze()
        r = _cli(p, "construct", "propose", "synonyms", "--provider", "fake")
        assert r.exit_code == 0 and f"v{v} is frozen. Saving v{v + 1} reopens the query" in r.output, r.output
        v = refreeze()
        r = _client(p).post("/api/construct/synonyms/propose?provider=fake")
        assert r.status_code == 200, r.text
        assert r.json()["notice"].startswith(f"v{v} is frozen. Saving v{v + 1} reopens the query")
    finally:
        fakemod.FakeProvider.__init__ = orig
    assert [x["cause"] for x in _unlocks(p)] == ["save"] * 8
    assert len(_changes(p, "artifact_unlock")) == 8


def _nowarn(text: str) -> str:
    """A freeze notice without its syntax-check warning (these fixtures' one-letter stems are lint errors)."""
    return "\n".join(x for x in text.split("\n") if not x.startswith("Warning:"))


def test_freeze_an_older_version_names_the_frozen_query(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "a*", "b*", "c*")
    want = "v2 is now the frozen query; draft v3 is kept and is not the searched query."
    c = _client(p)
    d = c.get("/api/construct/synonyms").json()
    assert _nowarn(d["freeze_notice"]["2"]) == want and d["freeze_choice"]["2"] is False
    # the syntax check's errors are said first, in words, and do not block the freeze (inspection 1 Nov)
    assert d["freeze_notice"]["2"].startswith("Warning: the syntax check finds 1 error in v2")
    r = c.post("/api/construct/synonyms/2/status/frozen")
    assert r.status_code == 200 and _nowarn(r.json()["notice"]) == want
    assert r.json()["notice"].split("\n")[-1].endswith("v2 was frozen with it, and the search strings carry it as "
                                                       "written; to fix it, save a new version in the editor and "
                                                       "freeze that.")
    assert artifacts.frozen_query(p)["version"] == 2 and _state(p, "query") == "done"
    assert coverage.validation_unlocked(p) and len(coverage.validation_papers(p)) == 2
    assert _changes(p, "artifact_status")[0]["summary"] == "synonyms v2: draft → frozen"
    assert _unlocks(p) == []
    n = len(_changes(p))
    r = _cli(p, "construct", "freeze", "synonyms", "2")
    assert r.exit_code == 0 and "v2 is already the frozen query" in r.output and "is now frozen" not in r.output
    assert len(_changes(p)) == n and _unlocks(p) == []
    assert "2" not in c.get("/api/construct/synonyms").json()["freeze_notice"]   # the frozen query has no Freeze step
    # the package's rounds.json marks v2, not the newest v3, as the frozen query
    assert [(r["round"], r["frozen_query"]) for r in package_json(p)["rounds.json"]] == [(1, False), (2, True), (3, False)]
    # the CLI twin on another project prints the same clause
    q = _proj(tmp_path, name="q")
    _versions(q, "a*", "b*", "c*")
    r = _cli(q, "construct", "freeze", "synonyms", "2")
    assert r.exit_code == 0 and want in r.output


ASK = ("v3 is the frozen query. Freezing v2 instead changes the searched query; 14 validation papers have been shown. "
       "Count them as seen: they no longer validate the search; only papers added to Validation later can. Keep them "
       "as validation: they still count, and every output states that they were shown. The choice is final.")


def _frozen_v3(tmp_path, name):
    p = _proj(tmp_path, n_val=14, name=name)
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    return p


def test_methods_names_the_version_frozen_last(tmp_path):
    """The searched query is the one frozen last: v2 frozen after v3 is named, not v3 (ui-tester new-researcher F23)."""
    from wider.recall.outputs import methods_draft

    p = _frozen_v3(tmp_path, "methods")
    _waiting(p, tmp_path, "a.ris", RIS)
    assert _cli(p, "import", "run").exit_code == 0
    assert "(synonym blocks version 3, frozen)" in methods_draft(Project.load(p.root))
    assert _cli(p, "construct", "freeze", "synonyms", "2", "--count-seen").exit_code == 0
    assert "(synonym blocks version 2, frozen)" in methods_draft(Project.load(p.root))


def test_freeze_another_version_asks(tmp_path):
    p = _frozen_v3(tmp_path, "count")
    c = _client(p)
    d = c.get("/api/construct/synonyms").json()
    assert d["freeze_choice"] == {"1": True, "2": True}
    assert _nowarn(d["freeze_notice"]["2"]).split("\n") == [ASK, "v2 is now the frozen query; v3 stays frozen and is not the searched query."]
    # without a choice nothing is frozen: the route answers 400 with the notice, the CLI exits 2
    r = c.post("/api/construct/synonyms/2/status/frozen")
    assert r.status_code == 400 and r.json()["detail"] == ASK
    r = _cli(p, "construct", "freeze", "synonyms", "2")
    assert r.exit_code == 2 and ASK in r.output and artifacts.CHOOSE_LINE in r.output
    r = _cli(p, "construct", "freeze", "synonyms", "2", "--count-seen", "--keep-validation")
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in r.output
    assert artifacts.list_versions(p, "synonyms")[1]["status"] == "draft" and _unlocks(p) == []
    assert artifacts.frozen_query(p)["version"] == 3
    # Count them as seen
    r = _cli(p, "construct", "freeze", "synonyms", "2", "--count-seen")
    assert r.exit_code == 0, r.output
    assert "Counted as seen: the 14 validation papers shown so far no longer validate the search." in r.output
    assert r.output.index("Counted as seen") < r.output.index("v2 is now the frozen query; v3 stays frozen")
    u = _unlocks(p)
    assert [(x["cause"], x["from_version"], x["new_version"], x["frozen_no"], x["counts_as_seen"]) for x in u] == [
        ("freeze", 3, 2, 1, 1)]
    assert artifacts.frozen_query(p)["version"] == 2 and artifacts.frozen_query(p)["frozen_no"] == 2
    ch = _changes(p, "artifact_unlock")
    assert ch[0]["summary"] == "query reopened: v2 frozen in place of v3 (counted as seen)" and not ch[0]["undoable"]
    assert coverage.validation_papers(p) == []
    assert [(x["cause"], x["from_version"], x["new_version"], x["counts_as_seen"]) for x in unlock_json(p)] == [
        ("freeze", 3, 2, 1)]
    # re-freezing v3 with the set empty asks nothing and records no row; the flags are ignored
    r = _cli(p, "construct", "freeze", "synonyms", "3", "--keep-validation")
    assert r.exit_code == 0 and "Kept as validation" not in r.output and len(_unlocks(p)) == 1
    assert artifacts.frozen_query(p)["version"] == 3

    # Keep them as validation (the page)
    k = _frozen_v3(tmp_path, "keep")
    r = _client(k).post("/api/construct/synonyms/2/status/frozen?seen_choice=keep")
    assert r.status_code == 200, r.text
    assert r.json()["notice"].split("\n")[0] == ("Kept as validation: the 14 validation papers shown so far still "
                                                 "count; every output states this choice.")
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(k)] == [("freeze", 0)]
    assert _changes(k, "artifact_unlock")[0]["summary"].endswith("(kept as validation)")
    assert len(coverage.validation_papers(k)) == 14 and artifacts.frozen_query(k)["version"] == 2
    assert [(x["cause"], x["counts_as_seen"]) for x in unlock_json(k)] == [("freeze", 0)]
    # a bad value is refused, nothing done
    r = _client(k).post("/api/construct/synonyms/3/status/frozen?seen_choice=maybe")
    assert r.status_code == 400 and len(_unlocks(k)) == 1

    # with no frozen query the flags are ignored and nothing is recorded
    n = _proj(tmp_path, name="none")
    _versions(n, "a*")
    r = _cli(n, "construct", "freeze", "synonyms", "1", "--count-seen")
    assert r.exit_code == 0 and _unlocks(n) == [] and "Counted as seen" not in r.output


def test_unlock_copies_warns_and_records(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    head = "Unlocking v3 reopens the query; 2 validation papers have been shown."
    assert _client(p).get("/api/construct/synonyms").json()["unlock_notice"]["3"] == f"{head} {artifacts.CHOICE_TAIL}"
    r = _cli(p, "construct", "unlock", "synonyms", "3", "--count-seen")
    assert r.exit_code == 0 and head in r.output and "copied into v4" in r.output, r.output
    assert artifacts.outcome_line("count", 2) in r.output
    v = artifacts.list_versions(p, "synonyms")
    assert (v[2]["status"], v[3]["status"], v[3]["origin"], v[3]["note"]) == ("frozen", "draft", "unlock", "unlocked from v3")
    assert artifacts.load(p, "synonyms", 4)[1] == artifacts.load(p, "synonyms", 3)[1]
    assert [(x["cause"], x["from_version"], x["new_version"], x["frozen_no"], x["counts_as_seen"]) for x in _unlocks(p)] == [
        ("unlock", 3, 4, 1, 1)]
    ch = _changes(p, "artifact_unlock")
    assert ch[0]["summary"] == "query unlocked: v3 copied into v4 (counted as seen)" and ch[0]["why_not"] == "an unlock is final"
    r = _client(p).post(f"/api/history/{ch[0]['change_id']}/undo")
    assert r.status_code == 409 and len(_unlocks(p)) == 1
    assert artifacts.frozen_query(p) is None and _state(p, "query") != "done"
    assert not coverage.validation_unlocked(p)
    assert [(x["unlock_no"], x["cause"], x["from_version"], x["new_version"]) for x in unlock_json(p)] == [
        (1, "unlock", 3, 4)]


def test_unlock_again_and_unlock_a_draft(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    at = _unlocks(p)[0]["at"][:10]
    r = _client(p).post("/api/construct/unlock", json={"kind": "synonyms", "version": 3})
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == f"the query is already reopened (unlock 1 on {at}); v5 is a copy of v3"
    assert len(_changes(p, "artifact_unlock")) == 1 and len(_unlocks(p)) == 1
    r = _cli(p, "construct", "unlock", "synonyms", "3")
    assert r.exit_code == 0 and f"the query is already reopened (unlock 1 on {at}); v6 is a copy of v3" in r.output
    n = len(artifacts.list_versions(p, "synonyms"))
    r = _cli(p, "construct", "unlock", "synonyms", "4")
    assert r.exit_code == 1 and "v4 is a draft; edit and save it, no unlock needed" in r.output
    r = _client(p).post("/api/construct/unlock", json={"kind": "synonyms", "version": 4})
    assert r.status_code == 400 and r.json()["detail"] == "v4 is a draft; edit and save it, no unlock needed"
    assert len(artifacts.list_versions(p, "synonyms")) == n and len(_unlocks(p)) == 1
    assert [x["unlock_no"] for x in unlock_json(p)] == [1]


def test_unlock_route_and_dialog(tmp_path):
    from tests.unit.test_construct_js_node import run_refine

    p = _proj(tmp_path)
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    c = _client(p)
    r = c.post("/api/construct/unlock", json={"kind": "cells", "version": 1})
    assert r.status_code == 400 and r.json()["detail"] == "only synonyms can be unlocked"
    r = c.post("/api/construct/unlock", json={"kind": "synonyms", "version": 9})
    assert r.status_code == 400 and "v9 does not exist" in r.json()["detail"]
    r = _cli(p, "construct", "unlock", "cells", "1")
    assert r.exit_code == 1 and "only synonyms can be unlocked" in r.output
    assert _unlocks(p) == [] and len(artifacts.list_versions(p, "synonyms")) == 1
    d = c.get("/api/construct/synonyms").json()
    # the Unlock button's confirm step: the notice, Cancel focused, Cancel posts nothing; Unlock posts the route
    out = run_refine(d, "cancel", "await Refine.unlock(1);")
    assert "Unlock synonyms v1?" in out["html"] and d["unlock_notice"]["1"] in out["html"]
    assert out["posts"] == []
    out = run_refine(d, "count", "await Refine.unlock(1);")
    assert out["posts"] == [["/api/construct/unlock", {"kind": "synonyms", "version": 1, "seen_choice": "count"}]]
    r = c.post("/api/construct/unlock", json={"kind": "synonyms", "version": 1, "seen_choice": "count"})
    assert r.status_code == 200 and r.json()["version"] == 2 and len(_unlocks(p)) == 1


def test_unlock_reopens_final_uploads(tmp_path):
    from wider.ingest.importer import FINAL_KEY, final_at, mark_final

    p = _proj(tmp_path)
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    _import_one(p, tmp_path)
    mark = mark_final(p)
    assert coverage.validation_unlocked(p)
    r = _cli(p, "construct", "unlock", "synonyms", "3", "--count-seen")
    assert r.exit_code == 0, r.output
    assert final_at(p) is None and p.store.get_meta(FINAL_KEY) is None
    assert len(_unlocks(p)) == 1 and _unlocks(p)[0]["final_before"] == mark["at"]
    r = _cli(p, "import", "list")
    assert "uploads not marked final" in r.output, r.output
    assert not coverage.validation_unlocked(p)
    # the new round's export goes in without a notice and without a further unlock entry
    from wider.ingest.importer import add_file

    (tmp_path / "s2.ris").write_text(RIS, encoding="utf-8")
    add_file(p, "scopus", tmp_path / "s2.ris")
    r = _cli(p, "import", "run")
    assert r.exit_code == 0, r.output
    assert "unlock" not in r.output.lower() and "reopen" not in r.output.lower(), r.output
    assert len(_unlocks(p)) == 1
    # the undo of the earlier mark is refused: the mark changed since
    r = _client(p).post(f"/api/history/{mark['change_id']}/undo")
    assert r.status_code == 409, r.text
    r = _cli(p, "history", "undo", str(mark["change_id"]))
    assert r.exit_code == 1, r.output
    assert final_at(p) is None and len(_unlocks(p)) == 1
    # a later mark opens the set again
    mark_final(p)
    assert coverage.validation_unlocked(p)


def test_stage_check_frozen_query_only_for_query(tmp_path):
    from tests.unit.test_construct import CELLS
    from wider.core.stages import load_stages

    checks = {s["key"]: s["done_when"] for s in load_stages()}
    assert {"check": "artifact", "name": "synonyms", "status": "frozen", "frozen_query": True} in checks["query"]
    assert not any(c.get("frozen_query") for k, cs in checks.items() if k != "query" for c in cs)
    p = _proj(tmp_path)
    artifacts.save_new_version(p, "cells", CELLS, origin="manual")
    artifacts.set_status(p, "cells", 1, "approved")
    artifacts.save_new_version(p, "cells", CELLS, origin="manual")
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    assert _state(p, "query") == "done" and _state(p, "construct") == "done"
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    assert _state(p, "query") != "done" and _state(p, "construct") == "done"


def test_unlock_older_frozen_reopens_the_frozen_query(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 2, "frozen")
    artifacts.set_status(p, "synonyms", 3, "frozen", "keep")
    fn = artifacts.frozen_query(p)["frozen_no"]
    want = "v3 is the frozen query; unlocking v2 reopens it and copies v2 into v4; 2 validation papers have been shown."
    assert _client(p).get("/api/construct/synonyms").json()["unlock_notice"]["2"] == f"{want} {artifacts.CHOICE_TAIL}"
    r = _cli(p, "construct", "unlock", "synonyms", "2", "--keep-validation")
    assert r.exit_code == 0 and want in r.output and artifacts.outcome_line("keep", 2) in r.output, r.output
    u = _unlocks(p)[-1]
    assert (u["cause"], u["from_version"], u["new_version"], u["frozen_no"]) == ("unlock", 2, 4, fn)
    assert artifacts.frozen_query(p) is None
    assert not _changes(p, "artifact_unlock")[0]["undoable"]
    assert (unlock_json(p)[-1]["cause"], unlock_json(p)[-1]["from_version"]) == ("unlock", 2)
    # the route twin on another project
    q = _proj(tmp_path, name="q")
    _versions(q, "a*", "b*", "c*")
    artifacts.set_status(q, "synonyms", 2, "frozen")
    artifacts.set_status(q, "synonyms", 3, "frozen", "count")
    r = _client(q).post("/api/construct/unlock", json={"kind": "synonyms", "version": 2})
    assert r.status_code == 200 and r.json()["notice"] == "v3 is the frozen query; unlocking v2 reopens it and copies v2 into v4"
    assert artifacts.frozen_query(q) is None   # v3 was frozen counting the papers as seen: the set is empty, nothing asked


def test_save_while_uploads_final_records_what_was_seen(tmp_path):
    from wider.ingest.importer import final_at, mark_final

    p = _proj(tmp_path, n_val=3)
    _import_one(p, tmp_path)
    mark = mark_final(p)
    assert coverage.validation_unlocked(p) and artifacts.frozen_query(p) is None
    head = "The uploads are final. Saving v1 reopens them; 3 validation papers have been shown."
    assert _client(p).get("/api/construct/synonyms").json()["save_notice"] == f"{head} {artifacts.CHOICE_TAIL}"
    f = tmp_path / "v1.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    r = _cli(p, "construct", "save", "synonyms", str(f), "--count-seen")
    assert r.exit_code == 0 and head in r.output and artifacts.outcome_line("count", 3) in r.output, r.output
    u = _unlocks(p)
    assert [(x["cause"], x["frozen_no"], x["final_before"], x["counts_as_seen"]) for x in u] == [("save", None, mark["at"], 1)]
    assert {s["benchmark_id"] for s in u[0]["seen"]} >= {"V001", "V002", "V003"}
    assert final_at(p) is None and not coverage.validation_unlocked(p)
    ch = _changes(p, "uploads_reopened")
    assert ch[0]["kind_label"] == "uploads reopened" and not ch[0]["undoable"]
    assert _changes(p, "artifact_unlock") == []   # the "query unlocked" entry lists the query's unlocks only
    assert [(x["cause"], x["final_before"]) for x in unlock_json(p)] == [("save", mark["at"])]
    # the page twin: a project whose uploads are final, saved through the route
    q = _proj(tmp_path, n_val=1, name="q")
    _import_one(q, tmp_path, "q.ris")
    mark_final(q)
    r = _client(q).post("/api/construct/synonyms/save", json={"text": yaml.safe_dump(SYN), "seen_choice": "count"})
    assert r.status_code == 200 and r.json()["notice"] == (
        "The uploads are final. Saving v1 reopens them; 1 validation paper has been shown.\n"
        + artifacts.outcome_line("count", 1))


def test_invalid_save_records_no_unlock(tmp_path):
    p = _proj(tmp_path)
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    try:
        artifacts.save_new_version(p, "synonyms", {"blocks": "not a list"}, origin="manual")
        raise AssertionError("an invalid save was accepted")
    except ValueError:
        pass
    r = _client(p).post("/api/construct/synonyms/save", json={"text": "blocks: 3\n"})
    assert r.status_code == 400
    assert _unlocks(p) == [] and len(artifacts.list_versions(p, "synonyms")) == 1
    assert not (p.root / "artifacts/synonyms/v2.yaml").exists() and artifacts.frozen_query(p)["version"] == 1
    assert json.loads(json.dumps(_changes(p, "artifact_unlock"))) == []


def _snapshot(p):
    """Everything a refused command must leave alone: unlock rows, meta, journal entries and saved input files."""
    q = Project.load(p.root)
    try:
        return (q.store.query("SELECT * FROM query_unlocks"), q.store.query("SELECT key, value FROM meta ORDER BY key"),
                q.store.scalar("SELECT COUNT(*) FROM history"),
                sorted(str(f.relative_to(p.root)) for f in p.path("inputs").rglob("*") if f.is_file()))
    finally:
        q.close()


def test_seen_choice_missing_exits_2(tmp_path, monkeypatch):
    """Each command that would reopen the query or the uploads while a frozen query stands and validation papers were
    shown needs --count-seen or --keep-validation: without one, or with both, it prints the asking notice and
    CHOOSE_LINE, exits 2 and changes nothing (Q2). Outside that situation either flag acts as without it. The
    early look of `wider coverage --validation` and `wider rank validate --validation` asks the same (Q1)."""
    from tests.unit.test_stage_pages import RIS_2, _fake_chain, _frozen_and_final, _ranked_fake
    from wider.rank.score import rank

    p = _frozen_and_final(tmp_path)
    (tmp_path / "new.ris").write_text(RIS_2)
    _fake_chain(monkeypatch)
    mark = journal.changes(p, "uploads_final")[0]["change_id"]
    q = _proj(tmp_path, n_val=2, name="fz")
    _versions(q, "a*", "b*", "c*")
    artifacts.set_status(q, "synonyms", 3, "frozen")
    cases = [(p, ["import", "add", "scopus", str(tmp_path / "new.ris")]),
             (p, ["prior-reviews", "chain"]),
             (p, ["history", "undo", str(mark)]),
             (q, ["construct", "freeze", "synonyms", "2"])]
    look = _proj(tmp_path, n_val=2, name="look")   # nothing frozen, two hidden validation papers
    _import_one(look, tmp_path, "l.ris")
    rk = _ranked_fake(tmp_path / "rk")
    rank(rk, top_n=1)
    cases += [(look, ["coverage", "--validation"]), (rk, ["rank", "validate", "--validation"])]
    for proj, args in cases:
        before = _snapshot(proj)
        for extra in ([], ["--count-seen", "--keep-validation"]):
            r = _cli(proj, *args, *extra)
            flat = " ".join(r.output.split())
            assert r.exit_code == 2 and artifacts.CHOOSE_LINE in flat, (args, extra, r.output)
            if not extra:
                assert "Count them as seen: they no longer validate the search" in flat, (args, r.output)
            assert _snapshot(proj) == before, (args, extra)
    # `import run` with a file waiting asks the same
    (p.path("inputs", "scopus") / "w.ris").write_text(RIS_2.replace("10.1000/def", "10.1000/w"))
    before = _snapshot(p)
    r = _cli(p, "import", "run")
    assert r.exit_code == 2 and "adding w.ris reopens them" in " ".join(r.output.split()), r.output
    assert _snapshot(p) == before
    # outside the situation either flag acts as without it (exit 0)
    from wider.ingest.importer import mark_final
    e = _proj(tmp_path, n_val=0, name="empty")
    _versions(e, "a*")
    artifacts.set_status(e, "synonyms", 1, "frozen")
    _import_one(e, tmp_path, "e.ris")
    mark_final(e)
    r = _cli(e, "import", "add", "scopus", str(tmp_path / "new.ris"), "--count-seen")
    assert r.exit_code == 0 and "Counted as seen" not in r.output, r.output
    # an empty validation set under a frozen query: the uploads row with counts_as_seen 0
    assert [(x["cause"], x["counts_as_seen"]) for x in _unlocks(e)] == [("uploads", 0)]
    n = _proj(tmp_path, name="none")
    _import_one(n, tmp_path, "n.ris")
    r = _cli(n, "import", "add", "scopus", str(tmp_path / "new.ris"), "--keep-validation")
    assert r.exit_code == 0 and _unlocks(n) == [], r.output
    # an early look with the set unlocked, or with no hidden paper, asks nothing: either flag acts as without it
    r = _cli(e, "coverage", "--validation", "--count-seen")
    assert r.exit_code == 0 and "Counted as seen" not in r.output and "logged in the history" not in r.output, r.output
    z = _proj(tmp_path, n_val=0, name="nolook")
    r = _cli(z, "coverage", "--validation", "--keep-validation")
    assert r.exit_code == 0 and "Kept as validation" not in r.output and "logged in the history" in r.output, r.output


# ------------------------------------------------------------------ coverage after an unlock (task 1.5, D3)
def _reopened(tmp_path, name, n_val=2):
    """Synonyms v3 frozen and unlocked into draft v4 with V001..V00n in Validation; one file imported."""
    p = _proj(tmp_path, n_val=n_val, name=name)
    _import_one(p, tmp_path, f"{name}.ris")
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    return p


def _add_val(p, doi, source="colleague"):
    from wider.construct import seed

    return seed.add_by_dois(p, [doi], set_name="Validation", source=source)[0].benchmark_id


def _labels(res):
    """{benchmark_id: set label} of the first scope; a used Validation paper is "used"."""
    sets = next(iter(res["scopes"].values()))
    return {r["benchmark_id"]: ("used" if s == coverage.USED else s) for s, v in sets.items() for r in v["rows"]}


def _looks(p):
    return [(e["benchmark_id"], e["unlock_no"], e["counted"]) for e in json.loads(p.store.get_meta(coverage.SEEN_BY_LOOK) or "[]")]


def test_coverage_after_unlock(tmp_path):
    p = _reopened(tmp_path, "add")
    at = _unlocks(p)[0]["at"][:10]
    new = _add_val(p, "10.1000/new1")
    artifacts.set_status(p, "synonyms", 4, "frozen")
    res = coverage.check(p)
    assert res["validation_included"]
    assert _labels(res) == {"B001": "Development", "V001": "used", "V002": "used", new: "Validation"}
    assert sorted(res["used_validation"]) == ["V001", "V002"]
    sets = next(iter(res["scopes"].values()))
    assert (sets["Development"]["n"], sets["Validation"]["n"], sets[coverage.USED]["n"]) == (1, 1, 2)
    want = f"Validation set seen on {at} (1 unlock); 1 paper added since."
    assert res["note"]["sentence"] == want and res["note"]["validated"]
    r = _cli(p, "coverage")
    assert r.exit_code == 0 and want in r.output and r.output.count("Val (used)") == 2, r.output
    # no paper added since
    q = _reopened(tmp_path, "none")
    artifacts.set_status(q, "synonyms", 4, "frozen")
    note = coverage.check(q)["note"]
    assert note["sentence"] == (f"Search not validated: validation set seen on {_unlocks(q)[0]['at'][:10]} (1 unlock); "
                                "no paper added since.")
    assert not note["validated"] and note["n_unlocks"] == 1


def test_unlock_order_by_sequence_in_the_same_second(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    from wider.ingest.importer import mark_final

    class Held(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    monkeypatch.setattr("wider.core.provenance.datetime", Held)
    p = _reopened(tmp_path, "sec")
    b8 = _add_val(p, "10.1000/b8")
    mark_final(p)
    res = coverage.check(p)
    assert res["validation_included"] and _labels(res)[b8] == "Validation"
    assert [b.benchmark_id for b in coverage.validation_papers(p)] == [b8]
    # a second unlock in the same second: B008 entered before it
    artifacts.set_status(p, "synonyms", 4, "frozen")
    artifacts.unlock(p, "synonyms", 4, seen_choice="count")
    assert len({u["at"] for u in _unlocks(p)}) == 1 and len(_unlocks(p)) == 2
    res = coverage.check(p)
    assert not res["validation_included"] and b8 in res["used_validation"]
    assert coverage.validation_papers(p) == []


def test_two_unlocks_use_the_latest(tmp_path):
    p = _reopened(tmp_path, "two")
    first = _unlocks(p)[0]["at"][:10]
    b5 = _add_val(p, "10.1000/b5")
    artifacts.set_status(p, "synonyms", 4, "frozen")
    artifacts.unlock(p, "synonyms", 4, seen_choice="count")
    artifacts.set_status(p, "synonyms", 5, "frozen")
    res = coverage.check(p)
    assert _labels(res)[b5] == "used" and b5 in res["used_validation"]
    assert res["note"]["sentence"] == f"Search not validated: validation set seen on {first} (2 unlocks); no paper added since."


LOOK_Q = coverage.LOOK_REOPENED.format(what="query is") + ". " + coverage.look_outcome("count", 1)


def test_peek_after_unlock_is_not_validated(tmp_path):
    from wider.core.provenance import utcnow

    p = _reopened(tmp_path, "peek")
    b7 = _add_val(p, "10.1000/b7")
    r = _cli(p, "coverage", "--validation", "--count-seen")
    assert r.exit_code == 0 and LOOK_Q in " ".join(r.output.split()), r.output
    ch = _changes(p, "validation_peek")
    assert [(c["summary"], c["undoable"]) for c in ch] == [("validation set shown while the query is reopened: counted as seen", False)]
    assert _looks(p) == [(b7, 1, True)]
    # the page twin
    q = _reopened(tmp_path, "page")
    q7 = _add_val(q, "10.1000/b7")
    d = _client(q).get("/api/coverage?validation=true&seen_choice=count").json()
    assert d["notice"] == LOOK_Q and d["validation_peeked"] and not d.get("choice_needed")
    assert [r["benchmark_id"] for r in next(iter(d["scopes"].values()))["Validation"]["rows"]] == [q7]
    assert len(_changes(q, "validation_peek")) == 1
    # after v4 is frozen: D3's sentence, then the look line
    artifacts.set_status(p, "synonyms", 4, "frozen")
    res = coverage.check(p)
    at, today = _unlocks(p)[0]["at"][:10], utcnow()[:10]
    want = [f"Search not validated: validation set seen on {at} (1 unlock); no paper added since.",
            f"v3 was unlocked on {at} into v4 (2 validation papers shown); the researcher chose to count them as seen.",
            f"Looked at on {today} while the query was reopened (1 validation paper shown); the researcher chose to "
            "count them as seen."]
    assert [res["note"]["sentence"], *res["note"]["lines"]] == want and b7 in res["used_validation"]
    r = _cli(p, "coverage")
    assert all(w in r.output for w in want), r.output
    # the report, the methods draft, manifest.json and the Recall page carry the same lines
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, *want)
    assert [x["n"] for x in mj["looks_after_unlock"]] == [1]


def test_moved_and_re_added_papers_are_seen(tmp_path):
    from wider.construct import seed

    p = _reopened(tmp_path, "moved")
    b6 = seed.add_by_dois(p, ["10.1000/b6"], set_name="Development")[0].benchmark_id
    r = _cli(p, "benchmark", "set", b6, "Validation")
    want6 = seed.WAS_DEV_NOTICE.format(id=b6)
    assert r.exit_code == 0 and want6 in " ".join(r.output.split()), r.output
    seed.remove(p, "V001")
    r = _cli(p, "benchmark", "add", "--doi", "10.1000/val1", "--set", "Validation")
    assert r.exit_code == 0, r.output
    again = next(b.benchmark_id for b in Project.load(p.root).read_benchmark() if b.doi == "10.1000/val1")
    assert seed.USED_NOTICE.format(id=again) in " ".join(r.output.split()), r.output
    artifacts.set_status(p, "synonyms", 4, "frozen")
    res = coverage.check(p)
    # B006 was in Development at the unlock and never shown: counted with Development; the re-added paper is used
    assert _labels(res)[b6] == "Development" and again in res["used_validation"] and b6 not in res["used_validation"]
    assert coverage.validation_papers(p) == []
    # the seed-papers step shows the same notices beside each paper
    q = _reopened(tmp_path, "page")
    c = _client(q)
    d = c.post("/api/benchmark/add", json={"text": "10.1000/q6", "set": "Development", "source": "researcher"}).json()
    q6 = d["added"][0]["benchmark_id"]
    r = c.post("/api/benchmark/assign", json={"benchmark_id": q6, "set": "Validation"})
    assert r.status_code == 200 and r.json()["notices"] == [seed.WAS_DEV_NOTICE.format(id=q6)]
    seed.remove(q, "V002")
    d = c.post("/api/benchmark/add", json={"text": "10.1000/val2", "set": "Validation", "source": "researcher"}).json()
    q2 = d["added"][0]["benchmark_id"]
    assert d["notices"] == {q2: [seed.USED_NOTICE.format(id=q2)]}


def test_freezing_synonyms_reports_validation(tmp_path):
    """Scenario "Freezing synonyms": the stage result names v3 and says validation is now reported, the strings are
    archived, and coverage on the CLI and the route reports the Validation set beside the Development set."""
    from wider.core.stage_result import last_result
    from wider.search.archive import entries

    p = _proj(tmp_path, name="fz")
    _import_one(p, tmp_path, "fz.ris")
    _versions(p, "a*", "b*", "c*")
    assert set(coverage.check(p)["scopes"]["platform:scopus"]) == {"Development"}
    r = _cli(p, "construct", "freeze", "synonyms", "3")
    assert r.exit_code == 0, r.output
    assert last_result(p, "query")["headline"].startswith(
        "synonyms v3 is now frozen. The validation seed papers are now reported in coverage.")
    assert entries(p) and _state(p, "query") == "done"
    assert set(coverage.check(p)["scopes"]["platform:scopus"]) == {"Development", "Validation"}
    d = _client(p).get("/api/coverage").json()
    assert set(d["scopes"]["platform:scopus"]) == {"Development", "Validation"} and d["validation_included"]
    assert "Validation:" in _cli(p, "coverage").output


def test_freeze_after_unlock(tmp_path):
    from wider.core.stage_result import last_result

    p = _reopened(tmp_path, "fa")
    new = _add_val(p, "10.1000/fa")
    r = _cli(p, "construct", "freeze", "synonyms", "4")
    assert r.exit_code == 0, r.output
    assert _state(p, "query") == "done"
    assert last_result(p, "query")["headline"] == ("synonyms v4 is now frozen. The validation seed papers are now reported "
                                                    "in coverage. v4 was accepted implicitly by the freeze.")
    res = coverage.check(p)
    assert sorted(res["used_validation"]) == ["V001", "V002"] and _labels(res)[new] == "Validation"
    # freezing v3 again while the query is still reopened: a new freeze number, the stage done, no new unlock row
    q = _reopened(tmp_path, "fb")
    fn = artifacts.latest_frozen(q)["frozen_no"]
    r = _cli(q, "construct", "freeze", "synonyms", "3")
    assert r.exit_code == 0, r.output
    fq = artifacts.frozen_query(q)
    assert fq["version"] == 3 and fq["frozen_no"] > fn and _state(q, "query") == "done" and len(_unlocks(q)) == 1
    # once v4 is frozen, freezing v3 again asks first, then records an unlock with cause freeze holding the choice
    fn = artifacts.frozen_query(p)["frozen_no"]
    r = _cli(p, "construct", "freeze", "synonyms", "3")
    assert r.exit_code == 2 and "v4 is the frozen query. Freezing v3 instead changes the searched query;" in " ".join(
        r.output.split()), r.output
    assert len(_unlocks(p)) == 1
    r = _cli(p, "construct", "freeze", "synonyms", "3", "--keep-validation")
    assert r.exit_code == 0, r.output
    fq = artifacts.frozen_query(p)
    assert fq["version"] == 3 and fq["frozen_no"] > fn
    assert [(u["cause"], u["counts_as_seen"]) for u in _unlocks(p)] == [("unlock", 1), ("freeze", 0)]


def test_look_adds_its_papers_to_seen_by_look(tmp_path):
    from tests.unit.test_stage_pages import RIS_2
    from wider.ingest.importer import add_file, mark_final

    p = _reopened(tmp_path, "look")
    b7 = _add_val(p, "10.1000/b7")
    coverage.check(p, include_validation=True, seen_choice="count")
    assert _looks(p) == [(b7, 1, True)]   # exactly the papers shown, with the current seen count
    ch = p.store.query("SELECT items FROM history WHERE kind='validation_peek'")
    assert len(ch) == 1 and coverage.SEEN_BY_LOOK in ch[0]["items"]   # the change watches the key
    # no version ever frozen and the uploads reopened: "while the uploads are reopened"
    u = _proj(tmp_path, n_val=1, name="up")
    _import_one(u, tmp_path, "up.ris")
    mark_final(u)
    (tmp_path / "up2.ris").write_text(RIS_2, encoding="utf-8")
    add_file(u, "scopus", tmp_path / "up2.ris", seen_choice="count")
    assert [x["cause"] for x in _unlocks(u)] == ["uploads"]
    v = _add_val(u, "10.1000/upv")
    res = coverage.check(u, include_validation=True, seen_choice="count")
    assert res["notice"] == LOOK_Q.replace("query is", "uploads are") and _looks(u) == [(v, 1, True)]
    assert _changes(u, "validation_peek")[0]["summary"] == "validation set shown while the uploads are reopened: counted as seen"
    assert res["note"]["lines"][-1].endswith("while the uploads were reopened (1 validation paper shown); the researcher chose to count "
                                          "them as seen.")


def test_new_validation_hidden_until_refreeze(tmp_path):
    from wider.ingest.importer import mark_final

    p = _reopened(tmp_path, "hid")
    # hidden after the unlock although v3 stays frozen
    assert artifacts.list_versions(p, "synonyms")[2]["status"] == "frozen"
    res = coverage.check(p)
    assert not res["validation_included"] and sorted(res["used_validation"]) == ["V001", "V002"]
    assert res["note"]["sentence"] == coverage.HIDDEN_NOTE and not res["note"]["validated"]
    r = _cli(p, "coverage")
    assert r.exit_code == 0 and "validation set hidden until the synonyms artifact is frozen" in r.output, r.output
    new = _add_val(p, "10.1000/hid")
    res = coverage.check(p)
    assert not res["validation_included"] and new not in _labels(res)
    # the dry-run half comes with task 2.2
    artifacts.set_status(p, "synonyms", 4, "frozen")
    res = coverage.check(p)
    assert res["validation_included"] and _labels(res)[new] == "Validation"
    assert res["note"]["sentence"] != coverage.HIDDEN_NOTE
    # a new uploads-final mark after the unlock opens it too; an old mark does not
    q = _proj(tmp_path, name="old")
    _import_one(q, tmp_path, "old.ris")
    _versions(q, "a*", "b*", "c*")
    mark_final(q)
    artifacts.set_status(q, "synonyms", 3, "frozen")
    artifacts.unlock(q, "synonyms", 3, seen_choice="count")   # clears the mark
    qn = _add_val(q, "10.1000/qn")
    assert not coverage.check(q)["validation_included"]
    mark_final(q)
    res = coverage.check(q)
    assert res["validation_included"] and _labels(res)[qn] == "Validation"


def test_early_look_asks_whether_to_count_as_seen(tmp_path):
    p = _proj(tmp_path, n_val=2, name="cnt")
    _import_one(p, tmp_path, "cnt.ris")
    c = _client(p)
    ask = ("Validation set requested before the query was frozen; it holds 2 papers. Count them as seen: they no "
           "longer validate the search; only papers added to Validation later can. Keep them as validation: they still "
           "count, and every output states that they were shown. The choice is final.")
    d = c.get("/api/coverage?validation=true").json()
    assert d["choice_needed"] and d["notice"] == ask and d["scopes"] == {}
    assert p.store.get_meta("validation_peeked_at") is None and _changes(p, "validation_peek") == []
    r = _cli(p, "coverage", "--validation")
    assert r.exit_code == 2 and ask in " ".join(r.output.split()), r.output
    d = c.get("/api/coverage?validation=true&seen_choice=count").json()
    out = ("validation set shown before the query was frozen; this look is logged in the history. Counted as seen: the 2 "
           "validation papers shown no longer validate the search.")
    assert d["notice"] == out and len(next(iter(d["scopes"].values()))["Validation"]["rows"]) == 2
    assert p.store.get_meta("validation_peeked_at") and _looks(p) == [("V001", 0, True), ("V002", 0, True)]
    ch = _changes(p, "validation_peek")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("validation set looked at before the freeze: counted as seen", False)]
    # the CLI twin on another project
    q = _proj(tmp_path, n_val=2, name="cli")
    _import_one(q, tmp_path, "cli.ris")
    r = _cli(q, "coverage", "--validation", "--count-seen")
    assert r.exit_code == 0 and out in " ".join(r.output.split()), r.output
    # after the freeze the counted papers are development papers and the note names the choice
    _versions(p, "a*")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    res = coverage.check(p)
    assert sorted(res["used_validation"]) == ["V001", "V002"] and coverage.validation_papers(p) == []
    day = ch[0]["at"][:10]
    line = (f"Validation set looked at on {day} before the freeze (2 papers shown); the researcher chose to count them "
            "as seen.")
    assert line in res["note"]["lines"]
    # the report's validity note, the methods draft, manifest.json and the Recall page carry the same line
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, line)
    assert [(x["n"], x["counted"]) for x in mj["looks_before_freeze"]] == [(2, True)]


def test_early_look_kept_as_validation(tmp_path):
    from tests.unit.test_pickers_js_node import run_coverage_card

    p = _proj(tmp_path, n_val=2, name="keep")
    _import_one(p, tmp_path, "keep.ris")
    _versions(p, "a*", "b*", "c*")   # saved before the look: a save after it would revise the query (validation-reuse D2)
    r = _cli(p, "coverage", "--validation", "--keep-validation")
    out = ("validation set shown before the query was frozen; this look is logged in the history. Kept as validation: "
           "the 2 validation papers shown still count; every output states this choice.")
    assert r.exit_code == 0 and out in " ".join(r.output.split()), r.output
    assert p.store.get_meta("validation_peeked_at") and _looks(p) == [("V001", 0, False), ("V002", 0, False)]
    ch = _changes(p, "validation_peek")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("validation set looked at before the freeze: kept as validation", False)]
    artifacts.set_status(p, "synonyms", 3, "frozen")
    res = coverage.check(p)
    assert res["used_validation"] == [] and [b.benchmark_id for b in coverage.validation_papers(p)] == ["V001", "V002"]
    line = (f"Validation set looked at on {ch[0]['at'][:10]} before the freeze (2 papers shown); the researcher chose to "
            "keep them as validation.")
    assert line in res["note"]["lines"]
    texts, mj, _ = validity_everywhere(p)
    in_every_output(texts, line)
    assert [(x["n"], x["counted"]) for x in mj["looks_before_freeze"]] == [(2, False)] and mj["validated"]
    # the card's "Keep them as validation" sends seen_choice=keep
    q = _proj(tmp_path, n_val=2, name="card")
    _import_one(q, tmp_path, "card.ris")
    got = run_coverage_card(q, "keep")
    assert got["gets"][-1].endswith("&seen_choice=keep") and got["note"].startswith(out[0].upper() + out[1:])
    assert _looks(q) == [("V001", 0, False), ("V002", 0, False)]


def _ranked(tmp_path, name, papers):
    """A project with eight synthetic records (DOIs 10.1000/p0..p7), the given seed papers, a fake embedding and a
    ranking; synonyms v1-v3 saved, none frozen."""
    from wider.core.models import EmbeddingConfig
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank

    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=16)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / f"{name}.ris").write_text("\n".join(
        f"TY  - JOUR\nTI  - Synthetic paper {i} on topic {i % 3}\nPY  - 2020\nDO  - 10.1000/p{i}\nAB  - words {i}\nER  -\n"
        for i in range(8)))
    add_file(p, "scopus", tmp_path / f"{name}.ris")
    import_all(p)
    dedup_run(p)
    p.write_benchmark(papers)
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"a": "synthetic paper on topic one " * 12,
                                                          "b": "synthetic paper on topic two " * 12}}, origin="manual")
    rank(p, top_n=1)
    _versions(p, "a*", "b*", "c*")
    return p


def _rows(text):
    """{id: set cell} of the first table in a CLI output (rich box drawing)."""
    out = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("│┃ ").split("│")]
        if len(cells) > 2 and cells[0][:1] == "B" and cells[0][1:].isdigit():
            out[cells[0]] = cells[1]
    return out


def test_ranking_validation_after_unlock_excludes_seen_papers(tmp_path):
    """Scenario "Ranking validation after an unlock excludes seen papers" (1.5b): after an unlock with B002 and B003
    shown, B004 added to Validation and v4 frozen, every reader treats B004 as the only validation paper."""
    from wider.core.summary import project_summary
    from wider.ingest.sanity import benchmark_list
    from wider.recall.outputs import methods_draft

    p = _ranked(tmp_path, "rv", [BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/p0", set="Development"),
                                BenchmarkPaper(benchmark_id="B002", title="y", doi="10.1000/p1", set="Validation"),
                                BenchmarkPaper(benchmark_id="B003", title="z", doi="10.1000/p2", set="Validation")])
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    assert _add_val(p, "10.1000/p5") == "B004"
    artifacts.set_status(p, "synonyms", 4, "frozen")
    q = Project.load(p.root)
    d = ["--dir", str(p.root)]
    env = {"COLUMNS": "200"}
    # wider rank validate and its page twin
    out = _invoke_wide(["rank", "validate", *d], env=env)
    assert out.exit_code == 0, out.output
    assert _rows(out.output) == {"B001": "Dev", "B002": "Val (used)", "B003": "Val (used)", "B004": "Val"}
    v = TestClient(create_app(q)).get("/api/rank/validate").json()
    assert {r["benchmark_id"]: (r["set"], r.get("used", False)) for r in v["rows"]} == {
        "B001": ("Development", False), "B002": (coverage.USED, True), "B003": (coverage.USED, True),
        "B004": ("Validation", False)}
    assert [v["summary"][s]["n"] for s in ("Development", "Validation", coverage.USED)] == [1, 1, 2]
    # the Ranking page's benchmark counts and the wider status line
    bm = TestClient(create_app(q)).get("/api/project").json()["benchmark"]
    assert bm == {"n": 4, "development": 1, "validation": 1, "used": 2, "not_counted": 0}
    out = _invoke_wide(["status", *d], env=env)
    assert "benchmark: 4 papers (1 development, 1 validation, 2 used validation)" in out.output, out.output
    # the screening sanity list: the used papers are listed whether or not the validation set is
    assert [b for b, _, _ in benchmark_list(q, include_validation=False)] == ["B001", "B002", "B003"]
    assert [b for b, _, _ in benchmark_list(q)] == ["B001", "B002", "B003", "B004"]
    # the methods draft
    assert ("developed against 1 known-relevant development paper and tested once on 1 held-out validation paper."
            in methods_draft(q))
    # the Overview's seed coverage counts B002 and B003 as used validation papers apart, its total unchanged
    coverage.check(q)
    seed = project_summary(q)["seed"]
    assert seed["n"] == 4 and seed["development"]["n"] == 1 and seed["used"]["n"] == 2


def test_ranking_validation_leaves_out_query_results_papers(tmp_path):
    """Scenario "Ranking validation leaves out papers from the query results" and the AND line of "A paper from the
    query results is kept but not counted" (1.5b): B001 (source query_results) is listed under its label and counted
    in neither set; only B004 (prior_review) validates; no unlock row."""
    from tests.unit.test_backends_js_node import PAGE, run_node
    from tests.unit.test_pickers_js_node import ESC, STATIC, set_cell_js
    from tests.unit.test_pickers_js_node import out as js_out
    from tests.unit.test_query_roles import js_function

    label = coverage.NOT_COUNTED
    from wider.construct.seed import SOURCE_LABELS
    assert label == SOURCE_LABELS["query_results"] == "from the query's results: kept, not counted in validation"
    p = _ranked(tmp_path, "qr", [
        BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/p0", set="Validation", source="query_results"),
        BenchmarkPaper(benchmark_id="B002", title="y", doi="10.1000/p1", set="Development"),
        BenchmarkPaper(benchmark_id="B004", title="z", doi="10.1000/p4", set="Validation", source="prior_review")])
    artifacts.set_status(p, "synonyms", 2, "frozen")
    q = Project.load(p.root)
    d = ["--dir", str(p.root)]
    env = {"COLUMNS": "250"}
    # coverage: CLI and route
    res = coverage.check(q)
    assert {s: [r["benchmark_id"] for r in v["rows"]] for s, v in res["scopes"]["platform:scopus"].items()} == {
        "Development": ["B002"], "Validation": ["B004"], label: ["B001"]}
    out = _invoke_wide(["coverage", *d], env=env)
    assert out.exit_code == 0 and _rows(out.output) == {"B002": "Dev", "B004": "Val", "B001": label}, out.output
    # wider rank validate and ablate --validation
    out = _invoke_wide(["rank", "validate", *d], env=env)
    assert out.exit_code == 0 and _rows(out.output) == {"B002": "Dev", "B004": "Val", "B001": label}, out.output
    c = TestClient(create_app(q))
    v = c.get("/api/rank/validate").json()
    assert v["summary"]["Validation"]["n"] == 1 and {r["benchmark_id"]: r["set"] for r in v["rows"]}["B001"] == label
    out = _invoke_wide(["rank", "ablate", "--validation", *d], env=env)
    assert out.exit_code == 0 and "1/1" in out.output, out.output
    from wider.rank.ablate import ablate
    assert {r["val_found"] for r in ablate(Project.load(p.root), include_validation=True)} == {"1/1"}
    # wider status and the page counts: B001 in neither set
    out = _invoke_wide(["status", *d], env=env)
    assert f"benchmark: 3 papers (1 development, 1 validation; 1 {label})" in out.output, out.output
    assert c.get("/api/project").json()["benchmark"] == {"n": 3, "development": 1, "validation": 1, "used": 0, "not_counted": 1}
    # the card and the Ranking page draw the label in full
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    cov = c.get("/api/coverage?scope=platforms").json()
    body = f"""{set_cell_js()}{js_function(src, "async function showCoverage(")}
{js_function(src, "function pctTable(")}
{js_function(src, "function rkSeedCardHtml(")}
{js_function(src, '$("#rk-validate").onclick = async () => {')};
replies["/api/coverage"] = {json.dumps(cov)}; replies["/api/rank/validate"] = {json.dumps(v)};
await showCoverage("platforms"); await $("#rk-validate").onclick(); await flush();
""" + js_out("{card: $('#cov-tables').innerHTML, rank: $('#rk-validation').innerHTML}")
    r = run_node(f"{ESC}\n{PAGE}\n{body}")
    cell = f"<td>B001</td><td>{label.replace(chr(39), '&#39;')}</td>"   # esc() turns the apostrophe into &#39;
    assert cell in r["card"] and cell in r["rank"]
    assert coverage.unlocks(Project.load(p.root)) == []


# ---------------------------------------------------------------- phase B: the dry run (D6, tasks 2.1 and 2.2)
def _bench(tmp_path, papers, name="d", platforms=("scopus",)) -> Project:
    """A project whose seed papers are (id, set, title, abstract, keywords); abstracts go to the store, as fetched."""
    from wider.construct.benchmark import sync

    p = Project.init(tmp_path / name, platforms=list(platforms))
    p.write_benchmark([BenchmarkPaper(benchmark_id=i, title=t, set=s, doi=f"10.1000/{i.lower()}", keywords=k)
                       for i, s, t, _a, k in papers])
    sync(p)
    for i, _s, _t, a, _k in papers:
        p.store.execute("UPDATE benchmark_papers SET abstract=? WHERE benchmark_id=?", (a, i))
    return p


def _save(p, syn):
    return artifacts.save_new_version(p, "synonyms", syn, origin="manual")


def _dry_cli(p, *args):
    """The CLI dry run: (result, the dry_run.json it wrote or None)."""
    before = set((p.root / "runs" / "query").glob("*")) if (p.root / "runs" / "query").exists() else set()
    r = _cli(p, "query", "dry-run", *args)
    new = set((p.root / "runs" / "query").glob("*")) - before if (p.root / "runs" / "query").exists() else set()
    data = json.loads((next(iter(new)) / "dry_run.json").read_text(encoding="utf-8")) if new else None
    return r, data


def _dry_route(p, **body):
    return _client(p).post("/api/query/dry-run", json=body)


def _same(cli_json, route_json):
    """CLI and route give the same evaluation (the time and folder differ, and the refinement questions the first run
    raised are not raised again by the second)."""
    drop = ("at", "run_dir", "grid_files", "interview")
    assert {k: v for k, v in cli_json.items() if k not in drop} == {k: v for k, v in route_json.items() if k not in drop}


def _seed(res, platform, bid):
    return next(s for s in res["platforms"][platform]["seeds"] if s["benchmark_id"] == bid)


def test_profiles_cover_the_boolean_family(tmp_path):
    from wider.search.render import platforms

    cat = platforms()
    stems = {"ebsco": 3, "scopus": 3, "wos": 3, "proquest": None, "ovid": None, "pubmed": 4}
    for key, stem in stems.items():
        prof = cat[key]["profile"]
        assert prof["min_stem"] == stem and len(prof["proximity_range"]) == 2, key
        assert prof["proximity_counts"] in ("between", "within") and "uncertain_when" in prof
    assert "profile" not in cat["gscholar"]
    p = _bench(tmp_path, [("B001", "Development", "Team huddles", "", "")])
    _save(p, SYN)
    r = _cli(p, "query", "platforms")
    assert r.exit_code == 0, r.output
    rows = {ln.split("│")[1].strip(): ln.split("│")[-2].strip() for ln in r.output.splitlines()
            if ln.count("│") > 6 and ln.split("│")[1].strip() in cat}
    assert rows == {k: ("no" if k == "gscholar" else "yes") for k in cat}
    r, data = _dry_cli(p, "--platform", "gscholar")
    assert r.exit_code == 1 and "no profile for gscholar" in r.output and data is None
    rr = _dry_route(p, platform="gscholar")
    assert rr.status_code == 400 and rr.json()["detail"] == "no profile for gscholar"
    prof = _client(p).get("/api/query/profiles").json()
    assert {x["key"]: x["profile"] for x in prof["platforms"]} == {**{k: k != "gscholar" for k in cat}, "openalex": True}
    assert prof["default"][0] == "scopus" and "gscholar" not in prof["default"]   # the project's platform first



KEEPER = ("B001", "Development", "Keepers of the coast", "A lantern on the cliff.", "fog")


def _oa_block(per_block, key):
    b = per_block[key]
    return b.state, b.reasons


def test_openalex_profile_follows_the_renderer(tmp_path):
    """Scenario "Seeds evaluated under the OpenAlex profile": every block is read in title and abstract (no keywords),
    the stemmed field makes a word variant uncertain, a query with truncation goes to the exact field, Near is AND,
    truncation inside a phrase and a short stem are uncertain; the platform catalogue does not change."""
    from wider.search.evaluate import Query, evaluate, load_profile, seed_doc
    from wider.search.render import openalex, platforms

    syn = {"blocks": [{"key": "a", "name": "Keepers", "terms": ["keeper"], "fields": ["title", "abstract"]},
                      {"key": "b", "name": "Lights", "terms": ["lantern"], "fields": ["title"]},
                      {"key": "c", "name": "Weather", "terms": ["fog"], "fields": ["keywords"]}],
           "logic": {"and": ["a", "b", "c"]}}
    p = _bench(tmp_path, [KEEPER])
    _save(p, syn)
    r, data = _dry_cli(p, "--platform", "openalex")
    assert r.exit_code == 0, r.output
    blocks = _seed(data, "openalex", "B001")["blocks"]
    assert (blocks["a"]["state"], blocks["a"]["reasons"]) == ("uncertain", ["lemmatisation"])
    assert blocks["a"]["words"] == {"keeper": ["keepers"]}
    assert blocks["b"]["state"] == "hit"          # a title-only block is searched in title and abstract
    assert (blocks["c"]["state"], blocks["c"]["reasons"]) == ("miss", [])   # no keywords read, so no "no keywords"
    assert data["platforms"]["openalex"]["profile"]["fields"]["keywords"] == ["title", "abstract"]
    _same(data, _dry_route(p, platform="openalex").json())
    doc = seed_doc(p.read_benchmark()[0], "Development", KEEPER[3])
    # truncation sends the query to the unstemmed exact field: coas* matches coast, "keepers" is no longer keeper
    q = Query.model_validate({**syn, "blocks": [{**syn["blocks"][0], "terms": ["keeper", "coas*"]}, *syn["blocks"][1:]]})
    assert openalex.field_for(q) == openalex.EXACT
    prof = load_profile("openalex", q)
    assert "lemmatisation" not in prof.uncertain_when
    a = evaluate(q, doc, prof).per_block["a"]
    assert (a.state, a.hits, a.words) == ("hit", ["coas*"], {"coas*": ["coast"]})
    only = Query.model_validate({**syn, "blocks": [{**syn["blocks"][0], "terms": ["keeper", "zz*"]}, *syn["blocks"][1:]]})
    assert _oa_block(evaluate(only, doc, load_profile("openalex", only)).per_block, "a") == ("miss", [])
    # Near is read as AND across title and abstract; truncation inside a phrase and a short stem are uncertain
    near = Query.model_validate({"blocks": [{"key": "n", "name": "N", "terms": [{"near": ["coast", "lantern"],
                                                                                  "distance": 1}]}],
                                 "logic": {"and": ["n"]}})
    assert evaluate(near, doc, load_profile("openalex", near)).per_block["n"].state == "hit"
    keeper_doc = seed_doc(p.read_benchmark()[0], "Development", "The coast keeper lit it.")
    phrase = Query.model_validate({"blocks": [{"key": "f", "name": "F", "terms": ['"coas* keeper"']}],
                                   "logic": {"and": ["f"]}})
    assert _oa_block(evaluate(phrase, keeper_doc, load_profile("openalex", phrase)).per_block, "f") == (
        "uncertain", ["truncation inside a phrase"])
    short = Query.model_validate({"blocks": [{"key": "s", "name": "S", "terms": ["ke*"]}], "logic": {"and": ["s"]}})
    assert _oa_block(evaluate(short, doc, load_profile("openalex", short)).per_block, "s") == (
        "uncertain", ["stem under minimum"])
    # the catalogue and `wider query platforms` do not change
    assert "openalex" not in platforms()
    r = _cli(p, "query", "platforms")
    assert r.exit_code == 0 and "openalex" not in r.output.lower(), r.output


def test_dry_run_card_offers_openalex(tmp_path):
    """The Dry run card's list offers OpenAlex, not selected: the default run ("Every platform with a profile") stays
    the catalogue's profiled platforms."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    p = _bench(tmp_path, [KEEPER])
    _save(p, SYN)
    prof = _client(p).get("/api/query/profiles").json()
    assert prof["platforms"][-1] == {"key": "openalex", "label": "OpenAlex", "profile": True}
    assert "openalex" not in prof["default"]
    r, data = _dry_cli(p)
    assert r.exit_code == 0 and "openalex" not in data["platforms"], r.output
    out = run_node(ESC + PAGE + f"""Object.assign(replies, {{ "/api/query/profiles": {json.dumps(prof)} }});
$("#dr-platform").options = [];
""" + (STATIC / "dryrun.js").read_text(encoding="utf-8") + """
await DryRun.load();
console.log(JSON.stringify({ html: $("#dr-platform").innerHTML, value: $("#dr-platform").value, msg: $("#dr-msg").textContent }));
""")
    assert '<option value="openalex">OpenAlex</option>' in out["html"], out
    assert out["html"].startswith('<option value="">Every platform with a profile</option>')
    assert out["value"] == "" and out["msg"] == "", out


def test_unsearched_field_decides_nothing(tmp_path):
    """Scenario "A keywords block on OpenAlex and on Scopus": a record with no keywords that matches no term of a
    title-abstract-keywords block is a miss on OpenAlex (it reads no keywords) and uncertain "no keywords" on Scopus."""
    syn = {"blocks": [{"key": "a", "name": "Fog", "terms": ["fog"], "fields": ["title", "abstract", "keywords"]}],
           "logic": {"and": ["a"]}}
    p = _bench(tmp_path, [("B001", "Development", "Keepers of the coast", "A lantern on the cliff.", "")])
    _save(p, syn)
    r, data = _dry_cli(p, "--platform", "openalex")
    assert r.exit_code == 0, r.output
    oa = _seed(data, "openalex", "B001")
    assert oa["state"] == "miss" and oa["blocks"]["a"]["reasons"] == [] and oa["why"] == "no match in block a"
    r, data = _dry_cli(p, "--platform", "scopus")
    sc = _seed(data, "scopus", "B001")
    assert sc["state"] == "uncertain" and sc["reasons"] == ["no keywords"], sc


AB_SYN = {"blocks": [{"key": "a", "name": "Safety", "terms": ['"psychological safety"']},
                     {"key": "b", "name": "Learning", "terms": ["learn*"]}], "logic": {"and": ["a", "b"]}}


def test_hits_and_misses_follow_the_profile(tmp_path):
    p = _bench(tmp_path, [("B001", "Development", "Teams at work", "Psychological safety supports learning in teams.", ""),
                          ("B002", "Development", "Hospitals", "Psychological safety among nurses.", "")])
    _save(p, AB_SYN)
    r, data = _dry_cli(p, "--platform", "scopus")
    assert r.exit_code == 0, r.output
    assert data["version"] == 1 and list(data["platforms"]) == ["scopus"]
    assert data["platforms"]["scopus"]["profile"]["platform"] == "scopus"
    b1, b2 = _seed(data, "scopus", "B001"), _seed(data, "scopus", "B002")
    assert b1["state"] == "hit" and b1["terms"] == ['"psychological safety"', "learn*"]
    assert b2["state"] == "miss" and b2["failed_blocks"] == ["b"] and b2["why"] == "no match in block b"
    assert data["platforms"]["scopus"]["recall"]["text"] == "recall 1/2"
    out = " ".join(r.output.split())
    assert "synonyms v1 (draft)" in out and "scopus (profile: minimum stem 3)" in out and "scopus: recall 1/2" in out
    assert "no match in block b" in out and "dry_run.json" in out
    rr = _dry_route(p, platform="scopus")
    assert rr.status_code == 200, rr.text
    _same(data, rr.json())
    assert rr.json()["run_dir"].startswith("runs/query/")
    # with no platform named: every profiled platform, the project's own (scopus) first
    r, data = _dry_cli(p)
    assert r.exit_code == 0 and list(data["platforms"])[0] == "scopus" and "gscholar" not in data["platforms"]
    assert len(data["platforms"]) == 6


def test_proximity_and_fields_are_evaluated(tmp_path):
    syn = {"blocks": [{"key": "p", "name": "Team learning", "terms": [{"near": ["team*", "learn*"], "distance": 3}]},
                      {"key": "t", "name": "Huddles", "terms": ["huddle*"], "fields": ["title"]}],
           "logic": {"and": ["p", "t"]}}
    p = _bench(tmp_path, [
        ("B001", "Development", "Daily huddles", "Teams often learn from errors.", ""),                 # 1 word between
        ("B002", "Development", "Daily huddles", "Teams of nurses and doctors rarely learn.", ""),      # 5 words between
        ("B003", "Development", "Ward rounds", "Teams learn in daily huddles.", "")])                  # huddle in abstract
    _save(p, syn)
    r, data = _dry_cli(p, "--platform", "scopus")
    assert r.exit_code == 0, r.output
    blocks = {b: _seed(data, "scopus", b)["blocks"] for b in ("B001", "B002", "B003")}
    assert blocks["B001"]["p"]["state"] == "hit" and blocks["B001"]["t"]["state"] == "hit"
    assert blocks["B002"]["p"]["state"] == "miss" and _seed(data, "scopus", "B002")["failed_blocks"] == ["p"]
    assert blocks["B003"]["p"]["state"] == "hit" and blocks["B003"]["t"]["state"] == "miss"
    assert [_seed(data, "scopus", b)["state"] for b in ("B001", "B002", "B003")] == ["hit", "miss", "miss"]
    assert "team* NEAR/3 learn*" in _seed(data, "scopus", "B001")["terms"]
    _same(data, _dry_route(p, platform="scopus").json())


def test_uncertain_states_and_reasons(tmp_path):
    syn = {"blocks": [{"key": "a", "name": "Organisation", "terms": ["organization"]}], "logic": {"and": ["a"]}}
    p = _bench(tmp_path, [("B001", "Development", "Learning at work", "Every organisation learns.", ""),
                          ("B002", "Development", "A study of teams", None, ""),
                          ("B003", "Development", "The organization of wards", "Wards.", "")], platforms=("pubmed",))
    _save(p, syn)
    r, data = _dry_cli(p, "--platform", "pubmed")
    assert r.exit_code == 0, r.output
    b1, b2, b3 = (_seed(data, "pubmed", b) for b in ("B001", "B002", "B003"))
    assert b1["state"] == "uncertain" and "automatic term mapping" in b1["reasons"]
    assert b2["state"] == "uncertain" and b2["reasons"] == ["no abstract"]
    assert b3["state"] == "hit"
    rec = data["platforms"]["pubmed"]["recall"]
    assert (rec["hits"], rec["uncertain"], rec["misses"], rec["total"]) == (1, 2, 0, 3)
    out = " ".join(r.output.split())
    assert "pubmed: recall 1/3, 2 uncertain (not counted)" in out and "automatic term mapping" in out
    assert "no abstract" in out
    _same(data, _dry_route(p, platform="pubmed").json())
    # the same seed on Scopus (no term mapping in its profile): a spelling variant is not a match there
    r, data = _dry_cli(p, "--platform", "scopus")
    assert _seed(data, "scopus", "B001")["state"] == "miss"


def test_dry_run_without_seeds_names_setup(tmp_path):
    from wider.search.evaluate import NO_SEEDS

    assert NO_SEEDS == "no seed papers: add them on the Review plan (Seed papers)"
    p = Project.init(tmp_path / "e", platforms=["scopus"])
    _save(p, SYN)
    r = _cli(p, "query", "dry-run")
    assert r.exit_code == 1 and NO_SEEDS in " ".join(r.output.split())
    assert not list((p.root / "runs").glob("query/*"))
    rr = _dry_route(p)
    assert rr.status_code == 400 and rr.json()["detail"] == NO_SEEDS
    assert not list((p.root / "runs").glob("query/*"))
    # the card shows the same sentence with a link to the Seed papers task (review-plan-and-question-window D17)
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    out = run_node(ESC + PAGE + (STATIC / "dryrun.js").read_text(encoding="utf-8") + f"""
console.log(JSON.stringify({{ err: DryRun.errorHtml({json.dumps(NO_SEEDS)}), other: DryRun.errorHtml("no synonyms version 9") }}));
""")
    assert NO_SEEDS in out["err"] and 'href="#setup?task=seeds"' in out["err"] and "Open Seed papers" in out["err"]
    assert "#setup" not in out["other"]


def test_dry_run_card_shows_state_reason_and_recall():
    # the page's builder: the state column carries the reason (shown on focus), the recall line and the notices
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    res = {"version": 4, "status": "draft", "notices": ["validation seeds are shown only for the frozen query v3"],
           "seeds": [{"benchmark_id": "B001", "set": "Development"}], "run_dir": "runs/query/x",
           "platforms": {"pubmed": {"profile": {"min_stem": 4}, "recall": {"text": "recall 0/1, 1 uncertain (not counted)"},
                                    "seeds": [{"benchmark_id": "B001", "set": "Development", "state": "uncertain",
                                               "terms": ["organization"], "why": "automatic term mapping"}]}}}
    out = run_node(ESC + PAGE + (STATIC / "dryrun.js").read_text(encoding="utf-8") + f"""
console.log(JSON.stringify({{ html: DryRun.resultHtml({json.dumps(res)}, {{ pubmed: "PubMed" }}) }}));
""")
    h = out["html"]
    assert 'tabindex="0" class="dr-state unc" data-reason="automatic term mapping"' in h
    assert "PubMed: recall 0/1, 1 uncertain (not counted)" in h and "validation seeds are shown only for the frozen query v3" in h
    assert "synonyms v4 (draft)" in h and "minimum stem 4" in h and "runs/query/x/dry_run.json" in h
    # a run from the card reloads the Rounds card, which shows the run kept for its round
    out = run_node(ESC + PAGE + f"""Object.assign(replies, {{ "/api/query/dry-run": {json.dumps(res)} }});
const loads = []; globalThis.Rounds = {{ load: async () => {{ loads.push(1); }} }};
""" + (STATIC / "dryrun.js").read_text(encoding="utf-8") + """
await DryRun.run();
console.log(JSON.stringify({ loads, msg: $("#dr-msg").textContent }));
""")
    assert out == {"loads": [1], "msg": ""}, out


def test_validation_seeds_only_for_the_frozen_query(tmp_path):
    p = _bench(tmp_path, [("B001", "Development", "Huddles", "Team huddles after errors.", ""),
                          ("B004", "Validation", "Meetings", "Safety meetings in wards.", "")])
    _versions(p, "huddle*", "briefing*", "standup*", "debrief*")   # v1..v4; v4 is kept from before the freeze
    artifacts.set_status(p, "synonyms", 3, "frozen")
    notice = "validation seeds are shown only for the frozen query v3"
    r, data = _dry_cli(p, "--version", "4", "--platform", "scopus")
    assert r.exit_code == 0, r.output
    assert data["notices"] == [notice] and [s["benchmark_id"] for s in data["seeds"]] == ["B001"]
    assert notice in " ".join(r.output.split())
    _same(data, _dry_route(p, version=4, platform="scopus").json())
    r, data = _dry_cli(p, "--version", "3", "--platform", "scopus")
    assert data["notices"] == [] and [(s["benchmark_id"], s["set"]) for s in data["seeds"]] == [
        ("B001", "Development"), ("B004", "Validation")]
    assert _seed(data, "scopus", "B004")["state"] == "hit"
    _same(data, _dry_route(p, version=3, platform="scopus").json())
    # acceptance (2.6): v4 is compared with v3 on the development seed only; the frozen query's own v3 shows B004
    plan = _client(p).get("/api/query/rounds/4/accept").json()
    assert notice in plan["lines"] and not any("validation seeds on" in x for x in plan["lines"])
    assert not any("B004" in x for x in plan["lines"]) and plan["previous"] == 3
    r = _cli(p, "query", "accept", "4")
    assert r.exit_code == 0 and notice in _flat(r.output) and "B004" not in r.output
    rr = _client(p).post("/api/query/rounds/3/accept")
    assert rr.status_code == 200 and notice not in rr.json()["lines"]
    assert "validation seeds on scopus: 1/1 retrieved" in rr.json()["lines"]
    rr = _client(p).post("/api/query/rounds/4/accept")
    assert rr.status_code == 200 and notice in rr.json()["lines"] and "B004" not in json.dumps(rr.json())
    # no frozen query stands (the uploads final, no version frozen): every dry run reads the other notice
    from wider.ingest.importer import mark_final

    q = _bench(tmp_path, [("B001", "Development", "Huddles", "Team huddles.", ""),
                          ("B004", "Validation", "Meetings", "Safety meetings.", "")], name="q")
    _versions(q, "huddle*")
    _import_one(q, tmp_path)
    mark_final(q)
    none = "validation seeds are shown only for a frozen query; none stands"
    r, data = _dry_cli(q, "--platform", "scopus")
    assert data["notices"] == [none] and [s["benchmark_id"] for s in data["seeds"]] == ["B001"]
    assert _dry_route(q, platform="scopus").json()["notices"] == [none]


# ---------------------------------------------------------------- keywords (D6, task 2.3)
KW_CODES = {"ebsco": "(KW (silence))", "scopus": "TITLE-ABS-KEY(silence)", "wos": "(AK=(silence))",
            "proquest": "IF(silence)", "ovid": "(silence).kw.", "pubmed": "(silence[tiab])"}
UNVERIFIED = "keyword field code not yet verified against the platform's help page"


def _page_blocks(client):
    """loadQuery in node on the routes it calls: the block list and each card's field notes as the page shows them."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js

    urls = ["/api/query/platforms", "/api/platforms", "/api/query/render", "/api/query/render?compact=true"]
    data = {u: client.get(u).json() for u in urls}
    body = js_function(APP_JS, "async function loadQuery(") + """
const api = async (u) => { if (!(u in DATA)) throw new Error("unexpected call " + u); return DATA[u]; };
const el = () => ({ innerHTML: "", textContent: "", querySelector: () => el() });
const els = {};
const $ = (sel) => (els[sel] ||= el());
const document = { querySelectorAll: () => [] };
await loadQuery();
console.log(JSON.stringify({ blocks: $("#q-blocks").innerHTML, cards: $("#q-strings").innerHTML, status: $("#q-status").textContent }));"""
    out = run_js(body, data)
    assert out["status"].startswith("synonyms v"), out["status"]
    return out


def test_keywords_field_rendered_and_evaluated(tmp_path, monkeypatch):
    import html
    import re

    from wider.search.ir import Query
    from wider.search.render import boolean, notes_for, platforms

    six = ("ebsco", "scopus", "wos", "proquest", "ovid", "pubmed")
    p = _bench(tmp_path, [("B001", "Development", "Nurses on night shifts", "Nurses withhold concerns.", "silence; fear")],
               platforms=six)
    _save(p, {"blocks": [{"key": "c", "name": "C", "terms": ["silence"], "fields": ["keywords"]}], "logic": "c"})
    # the CLI: --blocks lists the block first, every string uses today's code and carries its notes
    r = _cli(p, "query", "render", "--blocks", "--compact")
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0] == "# blocks of synonyms v1" and lines[1] == "C: keywords · silence"
    for key, code in KW_CODES.items():
        assert code in lines, (key, r.output)
    route = _client(p).get("/api/query/render?compact=true").json()
    assert {k: route["strings"][k] for k in six} == KW_CODES
    notes = route["field_notes"]
    for key in six:
        assert any(n.endswith(UNVERIFIED) for n in notes[key]), key
        for n in notes[key]:
            assert f"# {n}" in r.output
    assert "keywords searched more widely: the code also covers title and abstract" in notes["scopus"]
    assert "[tiab] includes author keywords" in notes["pubmed"]
    assert not any("searched more widely" in n for k in six if k != "scopus" for n in notes[k])
    # the page lists the same block line as the CLI and shows the notes on each card
    page = _page_blocks(_client(p))
    assert re.findall(r">([^<]+)</li>", page["blocks"]) == ["C: keywords · silence"]
    assert UNVERIFIED in html.unescape(page["cards"]) and 'data-ledger="scopus"' in page["cards"]
    # Scopus title and keywords: TITLE-ABS-KEY, the code also covers the abstract; abstract and keywords: the title
    tk = Query(blocks=[{"key": "c", "terms": ["silence"], "fields": ["title", "keywords"]}], logic="c")
    ak = Query(blocks=[{"key": "c", "terms": ["silence"], "fields": ["abstract", "keywords"]}], logic="c")
    from wider.search.render import render
    assert render(tk, "scopus", pretty=False) == "TITLE-ABS-KEY(silence)"
    assert "keywords searched more widely: the code also covers the abstract" in notes_for(tk, "scopus")
    assert "keywords searched more widely: the code also covers the title" in notes_for(ak, "scopus")
    for q in (tk, ak, Query(blocks=[{"key": "c", "terms": ["silence"], "fields": ["title", "abstract", "keywords"]}], logic="c")):
        assert not any("searched more widely" in n for n in notes_for(q, "wos"))
    # the dry run: the seed's keywords hold C's term and its abstract does not: a hit on every platform
    res, data = _dry_cli(p)
    assert res.exit_code == 0, res.output
    for key in six:
        assert _seed(data, key, "B001")["state"] == "hit", (key, _seed(data, key, "B001"))
    # every code in the catalogue: verified false today; verified true counts only with source url and accessed
    for key in six:
        for combo, e in platforms()[key]["field_codes"].items():
            assert e.get("note"), (key, combo)
            if e.get("verified"):
                assert e.get("source", {}).get("url") and e["source"].get("accessed"), (key, combo)
    kw = dict(platforms()["ebsco"]["field_codes"]["keywords"])
    one = Query(blocks=[{"key": "c", "terms": ["silence"], "fields": ["keywords"]}], logic="c")
    monkeypatch.setitem(platforms()["ebsco"]["field_codes"], "keywords", {**kw, "verified": True})
    assert any(n.endswith(UNVERIFIED) for n in notes_for(one, "ebsco"))       # no source: still unverified
    monkeypatch.setitem(platforms()["ebsco"]["field_codes"], "keywords",
                        {**kw, "verified": True, "source": {"url": "https://example.org/help", "accessed": "2026-10-19"}})
    assert boolean.verified(platforms()["ebsco"]["field_codes"]["keywords"]) and notes_for(one, "ebsco") == []


def test_scopus_keywords_block_reads_title_and_abstract_too():
    """A keywords block is rendered for Scopus as TITLE-ABS-KEY, which searches title, abstract and keywords: the dry
    run reads it the same way, so a seed with the term only in its title is a hit on Scopus and a miss where the code
    searches keywords only; every code's `covers` equals what the profile reads for the same fields."""
    from wider.search.evaluate import SeedDoc, evaluate, load_profile
    from wider.search.ir import Query
    from wider.search.render import platforms

    q = Query(blocks=[{"key": "c", "terms": ["silence"], "fields": ["keywords"]}], logic="c")
    doc = SeedDoc("B001", title="Employee silence in hospitals", keywords="voice; hospitals")
    assert evaluate(q, doc, load_profile("scopus")).state == "hit"
    assert evaluate(q, doc, load_profile("ebsco")).state == "miss"
    for key, cat in platforms().items():
        read_as = (cat.get("profile") or {}).get("fields") or {}
        for combo, e in (cat.get("field_codes") or {}).items():
            if "covers" in e:
                assert set(e["covers"]) == {r for f in combo.split(",") for r in read_as.get(f, [f])}, (key, combo)


def _node_card(res, labels):
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    return run_node(ESC + PAGE + (STATIC / "dryrun.js").read_text(encoding="utf-8") + f"""
console.log(JSON.stringify({{ html: DryRun.resultHtml({json.dumps(res)}, {json.dumps(labels)}) }}));
""")["html"]


def test_grid_cover_and_heatmap(tmp_path):
    # four terms matching {1,2}, {2,3}, {3} and {}: greedy cover takes alpha (ties go to the term order), then beta
    p = _bench(tmp_path, [("B001", "Development", "One", "alpha", ""), ("B002", "Development", "Two", "alpha beta", ""),
                          ("B003", "Development", "Three", "beta gamma", "")])
    _save(p, {"blocks": [{"key": "a", "name": "Words", "terms": ["alpha", "beta", "gamma", "delta"]}],
              "logic": {"and": ["a"]}})
    r, data = _dry_cli(p, "--platform", "scopus", "--grid")
    assert r.exit_code == 0, r.output
    pl = data["platforms"]["scopus"]
    assert pl["grid"]["seeds"] == ["B001", "B002", "B003"]
    assert {row["term"]: row["cells"] for row in pl["grid"]["rows"]} == {
        "alpha": ["hit", "hit", ""], "beta": ["", "hit", "hit"], "gamma": ["", "", "hit"], "delta": ["", "", ""]}
    assert [c["term"] for c in pl["cover"]] == ["alpha", "beta"]
    assert pl["no_seed"] == [{"block": "a", "term": "delta"}]
    out = r.output
    assert "minimal cover: a: alpha; a: beta" in out and "matches no seed paper: a: delta" in out
    run = next(iter((p.root / "runs" / "query").glob("*")))
    assert f"written to {run.relative_to(p.root)}/grid.csv" in out and f"{run.relative_to(p.root)}/grid.svg" in out
    rows = (run / "grid.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "platform,block,term,B001,B002,B003" and "scopus,a,alpha,1,1," in rows and "scopus,a,delta,,," in rows
    svg = (run / "grid.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "<title>" in svg and "<desc>" in svg
    assert all(f">{w}<" in svg or f"a: {w}" in svg for w in ("alpha", "beta", "gamma", "delta"))
    assert all(f">{s}<" in svg for s in ("B001", "B002", "B003"))
    # the <desc> states the same cells as the text grid
    assert "scopus, a: alpha: hits B001, B002" in svg and "scopus, a: delta: hits no seed" in svg
    # the route (the card) gives the same grid and cover; the page shows the cover line, the cells and the heatmap
    rr = _dry_route(p, platform="scopus")
    assert rr.status_code == 200, rr.text
    _same(data, rr.json())
    assert rr.json()["platforms"]["scopus"]["cover"] == pl["cover"]
    h = _node_card(rr.json(), {"scopus": "Scopus"})
    assert "minimal cover: a: alpha; a: beta" in h and "matches no seed paper: a: delta" in h
    assert 'aria-label="gamma on B003: hit"' in h and 'aria-label="delta on B001: no match"' in h
    assert 'class="dr-heatmap"' in h and "<desc>" in h and "grid.svg" in h
    # without --grid the CLI prints no grid but still writes the files
    r, _ = _dry_cli(p, "--platform", "scopus")
    assert "minimal cover" not in r.output


def test_truncation_diagnostics_list_matched_words(tmp_path):
    p = _bench(tmp_path, [("B001", "Development", "Culture", "The organization and its culture.", ""),
                          ("B002", "Development", "Climate", "An organizational climate.", ""),
                          ("B003", "Development", "Food", "Organic food in canteens.", "")])
    _save(p, {"blocks": [{"key": "b", "name": "Organisations", "terms": ["organ*"]}], "logic": {"and": ["b"]}})
    r, data = _dry_cli(p, "--platform", "scopus", "--terms")
    assert r.exit_code == 0, r.output
    rep = data["platforms"]["scopus"]["terms_report"]
    assert len(rep) == 1 and rep[0]["seeds_hit"] == ["B001", "B002", "B003"]
    stem = rep[0]["stems"][0]
    assert stem["piece"] == "organ*"
    assert [(w["word"], w["seeds"]) for w in stem["words"]] == [
        ("organic", ["B003"]), ("organization", ["B001"]), ("organizational", ["B002"])]
    assert stem["variants"] == [{"pattern": "organi?ation*", "keeps": ["organization", "organizational"],
                                 "drops": ["organic"]}]
    assert stem["shorter"] == {"pattern": "orga*", "gains": [], "under_minimum": False}
    out = r.output
    assert "b: organ*: hits B001, B002, B003" in out
    assert "organ* matches organic (B003), organization (B001), organizational (B002)" in out
    assert "organi?ation* would keep organization, organizational and drop organic" in out
    run = next(iter((p.root / "runs" / "query").glob("*")))
    assert json.loads((run / "truncation.json").read_text(encoding="utf-8"))["scopus"] == rep
    # the route gives the same words, and the card's expanded term row shows the same lines
    rr = _dry_route(p, platform="scopus")
    assert rr.status_code == 200, rr.text
    _same(data, rr.json())
    h = _node_card(rr.json(), {"scopus": "Scopus"})
    assert '<details class="dr-term"><summary class="small">b: organ*: hits B001, B002, B003</summary>' in h
    assert "<li>organi?ation* would keep organization, organizational and drop organic</li>" in h
    # a stem one letter shorter that falls under the minimum is flagged (PubMed needs four letters)
    _save(p, {"blocks": [{"key": "b", "name": "Organisations", "terms": ["orga*"]}], "logic": {"and": ["b"]}})
    r, data = _dry_cli(p, "--platform", "pubmed", "--terms")
    assert data["platforms"]["pubmed"]["terms_report"][0]["stems"][0]["shorter"]["under_minimum"] is True
    assert "under the platform's minimum stem" in r.output


# --- stream B: rounds and acceptance (2.5, 2.6, 2.9) ---
SEEDS3 = [("B001", "Development", "Team huddles", "Huddles after errors.", ""),
          ("B002", "Development", "Daily huddles", "Huddles in wards.", ""),
          ("B003", "Development", "Safety briefings", "Briefings after incidents.", "")]
RIS3 = "".join(RIS.replace("10.1000/abc", f"10.1000/r{i}").replace("Team meetings", f"Meeting {i}") for i in range(3))


def _rsyn(*terms):
    return {"blocks": [{"key": "a", "name": "A", "terms": list(terms)}], "logic": {"and": ["a"]}}


def _include_all(p):
    """The toy exports match no seed paper, so they start out of deduplication (ui-redesign D32 F8): tick them in."""
    from wider.ingest.importer import set_included
    q = Project.load(p.root)
    for r in q.store.query("SELECT source_id FROM sources WHERE included=0"):
        set_included(q, r["source_id"], True)


def _rounds_proj(tmp_path, *versions, name="rd", papers=SEEDS3, platforms=("scopus",)):
    """Seeds B001-B003 (huddles, huddles, briefings) and one synonyms version per term list: v1, v2, ..."""
    p = _bench(tmp_path, papers, name=name, platforms=platforms)
    for terms in versions:
        _save(p, _rsyn(*terms))
    return p


def _waiting(p, tmp_path, name, text=RIS):
    """A file saved under inputs/scopus/, not imported yet."""
    from wider.ingest.importer import add_file

    (tmp_path / name).write_text(text, encoding="utf-8")
    add_file(p, "scopus", tmp_path / name)


def _qv(p):
    return {r["file_name"]: r["query_version"] for r in Project.load(p.root).store.query("SELECT file_name, query_version FROM sources")}


def _round(data, n):
    return next(r for r in data["rounds"] if r["round"] == n)


def _plat(data, n, k):
    return next(x for x in _round(data, n)["platforms"] if x["platform"] == k)


def _flat(out):
    return " ".join(out.split())


def _rounds_js(js, replies=None, extra=""):
    """rounds.js on the stand-in page; ``js`` runs after it and prints its JSON."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    return run_node(ESC + PAGE + f"Object.assign(replies, {json.dumps(replies or {})});\n{extra}\n"
                    + (STATIC / "rounds.js").read_text(encoding="utf-8") + js)


def test_rounds_log_and_plot(tmp_path):
    from wider.search import rounds

    e = Project.init(tmp_path / "empty", platforms=["scopus"])
    r = _cli(e, "query", "rounds")
    assert r.exit_code == 0 and "no rounds yet: save a synonyms version first" in r.output
    p = _rounds_proj(tmp_path, ("huddle*", "briefing*"), ("huddle*",))
    # a written dry run is kept per round and platform: the list shows only what was dry-run
    for v in ("1", "2"):
        assert _dry_cli(p, "--version", v, "--platform", "scopus")[0].exit_code == 0
    r = _cli(p, "query", "rounds")
    assert r.exit_code == 0, r.output
    lines = [ln.rstrip() for ln in r.output.splitlines()]
    assert lines[:4] == ["v1 (draft)", "  scopus: recall 3/3; no export yet", "v2 (draft)", "  scopus: recall 2/3; no export yet"]
    # an export imported under round 1: its count, read from the included files; round 2 stays on the recall axis
    _waiting(p, tmp_path, "a.ris", RIS3)
    assert _cli(p, "import", "run", "--round", "1").exit_code == 0
    _include_all(p)
    r = _cli(p, "query", "rounds")
    assert "  scopus: recall 3/3; 3 records" in r.output.splitlines()
    assert "v1 scopus: recall 3/3, 3 records (export)" in r.output and "plot written to exports/recall_vs_yield.svg" in r.output
    assert "v2 scopus: recall 2/3, no export yet (recall axis only)" in r.output
    svg = (p.root / "exports" / "recall_vs_yield.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and "<title>Recall versus yield by round</title>" in svg
    assert '<g class="point"><desc>v1 scopus: recall 3/3, 3 records (export)</desc>' in svg and 'fill="black"' in svg
    assert '<g class="axis-only"><desc>v2 scopus: recall 2/3, no export yet (recall axis only)</desc>' in svg
    txt = (p.root / "exports" / "recall_vs_yield.txt").read_text(encoding="utf-8").splitlines()
    assert txt == ["v1 scopus: recall 3/3, 3 records (export)", "v2 scopus: recall 2/3, no export yet (recall axis only)"]
    # the route: the same rows, lines, plot and table (reads only)
    d = _client(p).get("/api/query/rounds").json()
    assert d["lines"] == rounds.table_lines(rounds.rounds(Project.load(p.root)))
    assert d["lines"][:2] == ["v1 (draft)", "  scopus: recall 3/3; 3 records"]
    assert d["svg"] == svg and d["table"] == txt
    assert _plat(d, 1, "scopus")["count"] == 3 and _plat(d, 1, "scopus")["count_source"] == "export"
    # --refresh dry-runs every round on every profiled platform, the project's first
    r = _cli(p, "query", "rounds", "--refresh")
    assert r.exit_code == 0, r.output
    d = _client(p).get("/api/query/rounds").json()
    assert [x["platform"] for x in _round(d, 2)["platforms"]][0] == "scopus" and len(_round(d, 2)["platforms"]) == 6
    assert _client(p).post("/api/query/rounds/refresh").json() == {"refreshed": [1, 2]}
    # the page draws the plot and the rows from the route
    out = _rounds_js("console.log(JSON.stringify({ list: Rounds.listHtml(D), plot: Rounds.plotHtml(D) }));",
                     extra=f"const D = {json.dumps(d)};")
    assert "development recall" in out["list"] and "recall 3/3" in out["list"] and "3 records" in out["list"]
    assert out["plot"].startswith('<figure class="rd-plot"><svg') and "<li>v1 scopus: recall 3/3, 3 records (export)</li>" in out["plot"]


def test_pasted_count_marks_the_plot(tmp_path, monkeypatch):
    from wider.search import rounds

    p = _rounds_proj(tmp_path, ("huddle*", "briefing*"), ("huddle*",))
    for v in ("1", "2"):
        _dry_cli(p, "--version", v, "--platform", "scopus")
    calls = []
    real = rounds.set_count
    monkeypatch.setattr(rounds, "set_count", lambda *a, **k: calls.append(a[1:4]) or real(*a, **k))
    r = _cli(p, "query", "count", "2", "scopus", "1,840")
    assert r.exit_code == 0 and "round v2 on scopus: 1,840 records (pasted)" in r.output, r.output
    rr = _client(p).post("/api/query/rounds/1/count", json={"platform": "scopus", "n": "900"})
    assert rr.status_code == 200 and rr.json()["line"] == "round v1 on scopus: 900 records (pasted)"
    assert calls == [(2, "scopus", "1,840"), (1, "scopus", "900")]   # the CLI and the route call the same function
    d = _client(p).get("/api/query/rounds").json()
    assert (_plat(d, 2, "scopus")["count"], _plat(d, 2, "scopus")["count_source"]) == (1840, "pasted")
    assert _plat(d, 2, "scopus")["count_text"] == "1,840 records (pasted)"
    assert '<g class="point pasted"><desc>v2 scopus: recall 2/3, 1,840 records (pasted)</desc><circle' in d["svg"]
    assert 'fill="none"' in d["svg"] and "v2 scopus: recall 2/3, 1,840 records (pasted)" in d["table"]
    r = _cli(p, "query", "rounds")
    assert "v2 scopus: recall 2/3, 1,840 records (pasted)" in r.output and "  scopus: recall 2/3; 1,840 records (pasted)" in r.output
    assert 'class="point pasted"' in (p.root / "exports" / "recall_vs_yield.svg").read_text(encoding="utf-8")
    # refusals: nothing written
    for args, said in ((("2", "nowhere", "5"), "unknown platform 'nowhere'"),
                       (("9", "scopus", "5"), "no round 9: the synonyms versions are v1, v2"),
                       (("2", "scopus", "many"), "the record count must be a whole number"),
                       (("2", "scopus", "9" * 23), "is too large to store; check the number")):
        r = _cli(p, "query", "count", *args)
        assert r.exit_code == 1 and said in _flat(r.output), (args, r.output)
    rr = _client(p).post("/api/query/rounds/2/count", json={"platform": "scopus", "n": -3})
    assert rr.status_code == 400 and rr.json()["detail"] == "the record count must be 0 or more"
    # journalled and undoable: v1's pasted count goes
    ch = _changes(p, "round_count")
    assert [c["summary"] for c in ch] == ["round v1 scopus: 900 records (pasted)", "round v2 scopus: 1,840 records (pasted)"]
    assert ch[0]["kind_label"] == "round count" and ch[0]["undoable"] and ch[0]["change_id"] == rr.json().get("change_id", ch[0]["change_id"])
    journal.undo(p, ch[0]["change_id"])
    d = _client(p).get("/api/query/rounds").json()
    assert _plat(d, 1, "scopus")["count"] is None and _plat(d, 1, "scopus")["count_text"] == "no export yet"
    # an export imported under round 2 replaces the pasted count; typing one then is refused
    _waiting(p, tmp_path, "a.ris", RIS3)
    assert _cli(p, "import", "run", "--round", "2").exit_code == 0
    _include_all(p)
    d = _client(p).get("/api/query/rounds").json()
    assert (_plat(d, 2, "scopus")["count"], _plat(d, 2, "scopus")["count_source"]) == (3, "export")
    assert '<g class="point"><desc>v2 scopus: recall 2/3, 3 records (export)</desc>' in d["svg"] and "pasted" not in d["svg"].split("<g")[-1]
    r = _cli(p, "query", "count", "2", "scopus", "1840")
    assert r.exit_code == 1 and "round 2 has an imported scopus export of 3 records; that count stands" in _flat(r.output)
    # the page saves each typed count through the route
    out = _rounds_js("""
const inp = { value: " 1,200 ", dataset: { countPlatform: "scopus" } };
document.querySelectorAll = () => [inp];
await Rounds.saveCounts(1);
console.log(JSON.stringify({ calls, msg: $("#rd-msg").textContent }));""",
                     {"/api/query/rounds/1/count": {"line": "round v1 on scopus: 1,200 records (pasted)"}, "/api/query/rounds": d})
    assert ["/api/query/rounds/1/count", "POST", {"platform": "scopus", "n": "1,200"}] in out["calls"]
    assert out["msg"] == "round v1 on scopus: 1,200 records (pasted)"


def test_import_attaches_to_chosen_round(tmp_path):
    p = _rounds_proj(tmp_path, ("huddle*",), ("briefing*",), ("standup*",))
    _waiting(p, tmp_path, "a.ris")
    r = _cli(p, "import", "run", "--round", "1")
    assert r.exit_code == 0 and "recorded under v1 (draft)" in r.output, r.output
    _waiting(p, tmp_path, "b.ris", RIS.replace("10.1000/abc", "10.1000/b"))
    rr = _client(p).post("/api/import/run?round=2")
    assert rr.status_code == 200 and rr.json()["recorded"] == "recorded under v2 (draft)"
    assert _qv(p) == {"a.ris": 1, "b.ris": 2}
    # an unknown round is refused, naming the versions; nothing is imported
    _waiting(p, tmp_path, "c.ris", RIS3)
    r = _cli(p, "import", "run", "--round", "9")
    assert r.exit_code == 1 and "no round 9: the synonyms versions are v1, v2, v3" in _flat(r.output)
    rr = _client(p).post("/api/import/run?round=9")
    assert rr.status_code == 400 and rr.json()["detail"] == "no round 9: the synonyms versions are v1, v2, v3"
    assert "c.ris" not in _qv(p)
    # the count sums the round's included files; its source is the newest
    assert _cli(p, "import", "run", "--round", "1").exit_code == 0
    _include_all(p)
    d = _client(p).get("/api/query/rounds").json()
    src = Project.load(p.root).store.one("SELECT source_id FROM sources WHERE file_name='c.ris'")["source_id"]
    assert (_plat(d, 1, "scopus")["count"], _plat(d, 1, "scopus")["source_id"]) == (4, src)
    # the selector lists every version, newest first, the newest preselected (no frozen query)
    assert d["selector"] == {"preselect": 3, "options": [{"version": 3, "label": "v3 (draft)"}, {"version": 2, "label": "v2 (draft)"},
                                                         {"version": 1, "label": "v1 (draft)"}]}
    out = _rounds_js("""
const html = Rounds.selectorHtml(D.selector);
await Rounds.fillSelector();
const filled = $("#imp-round").innerHTML;
$("#imp-round").value = "2"; const two = Rounds.roundParam();
$("#imp-round").value = ""; const none = Rounds.roundParam();
console.log(JSON.stringify({ html, filled, two, none, empty: Rounds.selectorHtml({ options: [] }) }));""",
                     {"/api/query/rounds": d}, extra=f"const D = {json.dumps(d)};")
    assert out["html"] == ('<option value="3" selected>v3 (draft)</option><option value="2">v2 (draft)</option>'
                           '<option value="1">v1 (draft)</option>') == out["filled"]
    assert (out["two"], out["none"]) == ("round=2", "")
    assert out["empty"] == '<option value="">no synonyms version yet</option>'


def _imp_run_js(replies, round_value):
    """The upload card's Import button (app.js) on the stand-in page: the URL it posts and the message it shows."""
    from tests.unit.test_query_roles import APP_JS, js_function

    handler = js_function(APP_JS, '$("#imp-run").onclick = async () =>')
    return _rounds_js(f"""
globalThis.Refine = {{ withSeenChoice: async (t, f) => f(null) }};
globalThis.showStageResult = () => {{}}; globalThis.loadImport = async () => {{}}; globalThis.showStageError = () => {{}};
$("#imp-round").value = {json.dumps(round_value)};
{handler};
await $("#imp-run").onclick();
console.log(JSON.stringify({{ calls, msg: $("#imp-msg").textContent }}));""", replies)


def test_import_defaults_to_the_frozen_query(tmp_path):
    p = _rounds_proj(tmp_path, ("huddle*",), ("briefing*",), ("standup*",))
    artifacts.set_status(p, "synonyms", 2, "frozen")                   # v2 frozen, draft v3 kept from before
    sel = _client(p).get("/api/query/rounds").json()["selector"]
    assert sel["preselect"] == 2 and sel["options"][1] == {"version": 2, "label": "v2 (the frozen query)"}
    _waiting(p, tmp_path, "a.ris")
    r = _cli(p, "import", "run")
    assert r.exit_code == 0 and "recorded under v2 (the frozen query)" in r.output, r.output
    _waiting(p, tmp_path, "b.ris", RIS.replace("10.1000/abc", "10.1000/b"))
    rr = _client(p).post("/api/import/run")
    assert rr.json()["recorded"] == "recorded under v2 (the frozen query)"
    # the page: the selector preselects v2; Import sends it and shows the line
    reply = {"files": [], "notice": None, "recorded": "recorded under v2 (the frozen query)", "stage_result": None}
    out = _imp_run_js({"/api/import/run": reply}, "2")
    assert out["calls"][0][:2] == ["/api/import/run?round=2", "POST"] and out["msg"] == "recorded under v2 (the frozen query)"
    out = _imp_run_js({"/api/import/run": reply}, "")
    assert out["calls"][0][0] == "/api/import/run?"   # no round chosen: the server's default
    assert _qv(p) == {"a.ris": 2, "b.ris": 2}
    # unlocked into v4: no frozen query stands, the next file goes to the newest version
    row = artifacts.unlock(p, "synonyms", 2, by="ana")
    assert row["version"] == 4
    _waiting(p, tmp_path, "c.ris", RIS.replace("10.1000/abc", "10.1000/c"))
    r = _cli(p, "import", "run")
    assert r.exit_code == 0 and "recorded under v4 (draft)" in r.output, r.output
    assert _qv(p)["c.ris"] == 4
    assert _client(p).get("/api/query/rounds").json()["selector"]["preselect"] == 4


def test_acceptance_states_lost_seeds(tmp_path):
    from wider.search import rounds

    p = _rounds_proj(tmp_path, ("huddle*", "briefing*"), ("huddle*",))   # v1 3/3, v2 loses B003
    r = _cli(p, "query", "accept", "1")
    assert r.exit_code == 0 and "v1 is now the accepted round" in r.output and "loses" not in r.output
    c = _client(p)
    plan = c.get("/api/query/rounds/2/accept").json()
    assert plan["loss_lines"][:1] == ["v2 loses B003 on scopus, which v1 retrieves."] and plan["previous"] == 1
    assert {x["benchmark_id"] for x in plan["lost"]} == {"B003"} and rounds.accepted_round(Project.load(p.root)) == 1
    # the page: a confirm step with the loss; Cancel accepts nothing
    js = """
globalThis.askChoice = async (html) => { shown.push(html); return { act: ANSWER }; };
const r = await Rounds.accept(2);
console.log(JSON.stringify({ shown, calls, msg: $("#rd-msg").textContent, r, needs: Rounds.needsConfirm(PLAN) }));"""
    replies = {"/api/query/rounds/2/accept": lambda_reply(plan), "/api/query/rounds": c.get("/api/query/rounds").json()}
    out = _rounds_js(js, replies, extra=f'const shown = []; const ANSWER = "cancel"; const PLAN = {json.dumps(plan)};')
    assert out["needs"] and out["msg"] == "Nothing was accepted." and out["r"] is None
    assert "v2 loses B003 on scopus, which v1 retrieves." in out["shown"][0] and 'data-act="cancel">Cancel</button>' in out["shown"][0]
    assert [x[1] for x in out["calls"]] == ["GET"]
    out = _rounds_js(js, replies, extra=f'const shown = []; const ANSWER = "accept"; const PLAN = {json.dumps(plan)};')
    assert ["/api/query/rounds/2/accept", "POST", None] in out["calls"]
    assert out["msg"].startswith("v2 is now the accepted round; it loses B003 on scopus, ebsco") and "retrieves" not in out["msg"]
    assert rounds.accepted_round(Project.load(p.root)) == 1   # the stand-in page posts nothing for real
    # the CLI accepts it and prints the same line after
    r = _cli(p, "query", "accept", "2")
    assert r.exit_code == 0 and "v2 is now the accepted round" in r.output
    assert "v2 loses B003 on scopus, which v1 retrieves." in _flat(r.output)
    d = c.get("/api/query/rounds").json()
    assert _round(d, 2)["mark"].startswith("accepted, loses B003 on scopus") and _round(d, 1)["mark"] == "accepted earlier"
    assert d["accepted"] == 2 and {x["benchmark_id"] for x in _round(d, 2)["lost"]} == {"B003"}
    html = _rounds_js("console.log(JSON.stringify(Rounds.listHtml(D)));", extra=f"const D = {json.dumps(d)};")
    assert 'class="warn-text small" data-mark="2">accepted, loses B003' in html and 'class="muted small" data-mark="1"' in html
    assert "v2 (draft): accepted, loses B003 on scopus" in _cli(p, "query", "rounds").output
    rs = {r["round"]: r for r in package_json(p)["rounds.json"]}
    assert rs[2]["lost"] == _round(d, 2)["lost"] and {"benchmark_id": "B003", "platform": "scopus"} in rs[2]["lost"]
    assert {x["benchmark_id"] for x in rs[2]["lost"]} == {"B003"} and rs[2]["accepted"]
    assert rs[1]["lost"] == [] and not rs[1]["accepted"] and rs[1]["accepted_no"] == 1
    # the Search strings page and `wider query render` default to the accepted round, not the newest
    _save(p, _rsyn("huddle*", "briefing*", "standup*"))                  # v3, 3/3
    assert c.get("/api/query/render").json()["row"]["version"] == 2
    assert "synonyms v2 (draft) rendered for" in _cli(p, "query", "render", "scopus").output
    # journalled; undo makes v1 the accepted round again
    ch = _changes(p, "round_accept")
    assert ch[0]["summary"].startswith("round v2 accepted, loses B003 on scopus") and ch[0]["kind_label"] == "round accepted"
    assert ch[0]["undoable"]
    journal.undo(p, ch[0]["change_id"])
    assert rounds.accepted_round(Project.load(p.root)) == 1 and _round(c.get("/api/query/rounds").json(), 1)["mark"] == "accepted"
    rr = c.post("/api/query/rounds/2/accept")
    assert rr.status_code == 200 and rr.json()["accepted"] and rr.json()["change_id"]
    assert c.get("/api/query/rounds/2/accept").json()["lines"] == ["v2 is already the accepted round"]
    # v3 retrieves all three: no loss line, no confirm step
    plan3 = c.get("/api/query/rounds/3/accept").json()
    assert plan3["loss_lines"] == [] and plan3["lost"] == []
    out = _rounds_js("""
globalThis.askChoice = async () => { throw new Error("no confirm step expected"); };
await Rounds.accept(3);
console.log(JSON.stringify({ calls }));""", {"/api/query/rounds/3/accept": lambda_reply(plan3), "/api/query/rounds": d})
    assert ["/api/query/rounds/3/accept", "POST", None] in out["calls"]
    r = _cli(p, "query", "accept", "3")
    assert r.exit_code == 0 and "loses" not in r.output and _round(c.get("/api/query/rounds").json(), 3)["mark"] == "accepted"
    r = _cli(p, "query", "accept", "9")
    assert r.exit_code == 1 and "no round 9: the synonyms versions are v1, v2, v3" in _flat(r.output)


def test_second_confirm_while_one_is_open_is_a_cancel():
    """A double click on Accept, Freeze or Unlock asks once (ui-tester F3): askChoice resolves null while a dialog is open."""
    from tests.unit.test_backends_js_node import STATIC, run_node

    src = (STATIC / "common.js").read_text(encoding="utf-8")   # moved there with the in-page dialogs (accessibility-and-naming D1)
    start = src.index("function askChoice(html, opts = {}) {")
    fn = src[start:src.index("\n}\n", start) + 2]
    out = run_node("""
const open = [];
const mk = () => { const d = { className: "", innerHTML: "", open: false, listeners: {},
  setAttribute() {}, addEventListener(k, f) { this.listeners[k] = f; },
  showModal() { this.open = true; open.push(this); }, close() { this.open = false; }, remove() {},
  querySelector(q) { return { focus() {}, dataset: { act: "cancel" } }; }, querySelectorAll() { return []; } }; return d; };
globalThis.document = { body: { appendChild() {} }, activeElement: null, createElement: mk,
  querySelector(q) { return q === "dialog.choice-dialog[open]" && open.some(d => d.open) ? open.find(d => d.open) : null; } };
globalThis.lastControl = null; globalThis.focusWhenEnabled = () => {};
""" + fn + """
const first = askChoice("<p>Unlock v2?</p>");
const second = await askChoice("<p>Unlock v2?</p>");
const shownWhileOpen = open.length;
open[0].listeners.click({ target: { closest: () => ({ dataset: { act: "unlock" } }) } });
const r1 = await first;
const third = askChoice("<p>again</p>");
console.log(JSON.stringify({ second, shownWhileOpen, r1, reopened: open.length }));""")
    assert out["second"] is None and out["shownWhileOpen"] == 1
    assert out["r1"] == {"act": "unlock", "fields": {}} and out["reopened"] == 2   # once closed, the next call asks again


def lambda_reply(plan):
    """A reply the stand-in api gives a GET and a POST alike (the POST adds accepted)."""
    return {**plan, "accepted": True}


def _reseed(p, papers):
    from wider.construct.benchmark import sync

    p.write_benchmark([BenchmarkPaper(benchmark_id=i, title=t, set=s, doi=f"10.1000/{i.lower()}", keywords=k)
                       for i, s, t, _a, k in papers])
    sync(p)
    for i, _s, _t, a, _k in papers:
        p.store.execute("UPDATE benchmark_papers SET abstract=? WHERE benchmark_id=?", (a, i))


def test_acceptance_reevaluates_on_todays_seeds(tmp_path):
    # both rounds were dry-run when only the two huddle papers were seeds: v2 lost nothing then
    p = _rounds_proj(tmp_path, ("huddle*", "briefing*"), ("huddle*",), papers=SEEDS3[:2])
    for v in ("1", "2"):
        _dry_cli(p, "--version", v, "--platform", "scopus")
    assert _cli(p, "query", "accept", "1").exit_code == 0
    d = _client(p).get("/api/query/rounds").json()
    assert _plat(d, 2, "scopus")["recall_text"] == "recall 2/2" and not _plat(d, 2, "scopus")["stale"]
    # today B003 (briefings) is a seed too: acceptance evaluates both rounds again, not the stored dry runs
    _reseed(p, SEEDS3)
    d = _client(p).get("/api/query/rounds").json()
    assert _plat(d, 2, "scopus")["stale"] and _plat(d, 2, "scopus")["recall_text"] == "recall 2/2"
    assert "  scopus: recall 2/2 (seeds changed since this dry run); no export yet" in _cli(p, "query", "rounds").output
    r = _cli(p, "query", "accept", "2")
    assert r.exit_code == 0 and "v2 loses B003 on scopus, which v1 retrieves." in _flat(r.output)
    assert _round(_client(p).get("/api/query/rounds").json(), 2)["mark"].startswith("accepted, loses B003 on scopus")
    # a seed dropped today is no longer lost: v3 is compared with v2 on B001 and B002 only
    _reseed(p, SEEDS3[:2])
    _save(p, _rsyn("huddle*", "standup*"))
    assert _client(p).get("/api/query/rounds/3/accept").json()["loss_lines"] == []


def test_uncertain_seed_neither_gains_nor_loses(tmp_path):
    # PubMed maps "organization" to "organisation" automatically: B001 is uncertain there under both rounds
    papers = [("B001", "Development", "Learning at work", "Every organisation learns.", ""),
              ("B002", "Development", "The organization of wards", "Wards.", "")]
    p = _rounds_proj(tmp_path, ("organisation",), ("organization",), papers=papers, platforms=("pubmed",))
    assert _cli(p, "query", "accept", "1").exit_code == 0
    plan = _client(p).get("/api/query/rounds/2/accept").json()
    assert "uncertain: B001, B002 on pubmed (not counted as lost or found)" in plan["uncertain_lines"]
    assert not any(x["platform"] == "pubmed" for x in plan["lost"])                  # neither lost on PubMed ...
    assert "v2 loses B001 on scopus, which v1 retrieves." in plan["loss_lines"]       # ... while a miss elsewhere is
    r = _cli(p, "query", "accept", "2")
    assert r.exit_code == 0 and "uncertain: B001, B002 on pubmed (not counted as lost or found)" in _flat(r.output)
    assert "on pubmed, which" not in r.output
    # B002 is uncertain under v1 only (listed above, a hit under v2); the dry run counts B001 neither found nor missed
    _dry_cli(p, "--version", "2", "--platform", "pubmed")
    x = _plat(_client(p).get("/api/query/rounds").json(), 2, "pubmed")
    assert x["recall_text"] == "recall 1/2, 1 uncertain (not counted)" and x["uncertain"] == ["B001"]
    assert "pubmed" not in _round(_client(p).get("/api/query/rounds").json(), 2)["mark"]


def test_freeze_accepts_implicitly(tmp_path):
    from wider.core.stage_result import last_result
    from wider.search import rounds

    p = _rounds_proj(tmp_path, ("huddle*",), ("briefing*",), ("huddle*", "briefing*"))
    r = _cli(p, "construct", "freeze", "synonyms", "3")
    assert r.exit_code == 0, r.output
    assert last_result(p, "query")["headline"].endswith("v3 was accepted implicitly by the freeze.")
    d = _client(p).get("/api/query/rounds").json()
    assert d["accepted"] == 3 and _round(d, 3)["mark"] == "accepted (by freeze)" and _round(d, 3)["accepted_how"] == "freeze"
    assert "v3 (frozen, the frozen query): accepted (by freeze)" in _cli(p, "query", "rounds").output
    ch = _changes(p, "round_accept")
    assert [(c["summary"], c["undoable"]) for c in ch] == [("round v3 accepted by the freeze", True)]
    # the page's freeze: the same sentence; freezing the accepted round adds none
    q = _rounds_proj(tmp_path, ("huddle*",), ("briefing*",), name="rq")
    assert _cli(q, "query", "accept", "1").exit_code == 0
    rr = _client(q).post("/api/construct/synonyms/2/status/frozen")
    assert rr.status_code == 200 and rr.json()["stage_result"]["headline"].endswith("v2 was accepted implicitly by the freeze.")
    assert rounds.accepted_round(Project.load(q.root)) == 2
    s = _rounds_proj(tmp_path, ("huddle*",), name="rs")
    assert _cli(s, "query", "accept", "1").exit_code == 0
    rr = _client(s).post("/api/construct/synonyms/1/status/frozen")
    assert "accepted implicitly" not in rr.json()["stage_result"]["headline"]
    assert [c["summary"] for c in _changes(s, "round_accept")] == ["round v1 accepted"]


# --- stream C: coverage bars (2.7) ---

def _plot_numbers(svg: str) -> dict:
    """{platform: (hits, uncertain, misses)} read from the SVG's bar segments."""
    import xml.etree.ElementTree as ET

    ns = {"s": "http://www.w3.org/2000/svg"}
    out = {}
    for g in ET.fromstring(svg).findall("s:g", ns):
        n = {r.get("data-state"): int(r.get("data-n")) for r in g.findall("s:rect", ns)}
        out[g.get("data-platform")] = (n["hits"], n["uncertain"], n["misses"])
    return out


def test_coverage_plot_matches_table(tmp_path):
    # scenario "Plot and table agree": the dry run covers more than one platform (scopus and pubmed differ: PubMed's
    # term mapping makes a spelling variant uncertain), writes coverage_by_platform.svg, and the CLI table, the SVG
    # and the page's table list the same hit, uncertain and miss counts per platform
    import re

    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
    from wider.search.coverage_plot import SVG_NAME

    syn = {"blocks": [{"key": "a", "name": "Organisation", "terms": ["organization"]}], "logic": {"and": ["a"]}}
    p = _bench(tmp_path, [("B001", "Development", "Learning at work", "Every organisation learns.", ""),
                          ("B002", "Development", "A study of teams", None, ""),
                          ("B003", "Development", "The organization of wards", "Wards.", ""),
                          ("B004", "Development", "Night shifts", "Nurses on wards.", "")], platforms=("scopus", "pubmed"))
    _save(p, syn)
    r, data = _dry_cli(p)
    assert r.exit_code == 0, r.output
    want = {k: (pl["recall"]["hits"], pl["recall"]["uncertain"], pl["recall"]["misses"])
            for k, pl in data["platforms"].items()}
    assert len(want) >= 2 and want["pubmed"] == (1, 2, 1) and want["scopus"] == (1, 1, 2)
    # the table in the result: one row per platform, in the run's order, with the recall numbers
    assert [row["platform"] for row in data["coverage"]] == list(data["platforms"])
    assert {row["platform"]: (row["hits"], row["uncertain"], row["misses"]) for row in data["coverage"]} == want
    assert all(row["total"] == 4 for row in data["coverage"])
    # the plot in the run folder, the same SVG as in the result, with the same numbers in its bars and its <desc>
    out = " ".join(r.output.split())
    m = re.search(r"plot written to (runs/query/\S+)/" + SVG_NAME, out)
    assert m, out
    svg = (p.root / m.group(1) / SVG_NAME).read_text(encoding="utf-8")
    assert (p.root / m.group(1) / "dry_run.json").exists()
    assert svg == data["coverage_svg"] and svg.startswith("<svg") and "<title" in svg
    assert _plot_numbers(svg) == want
    assert "scopus: 1 hit, 1 uncertain, 2 miss of 4" in svg and "pubmed: 1 hit, 2 uncertain, 1 miss of 4" in svg
    # the CLI table: platform, hits, uncertain, misses, seeds
    cells = " ".join(r.output.replace("│", " ").replace("┃", " ").split())
    assert "coverage per database" in cells
    for k, (h, u, mi) in want.items():
        assert f"{k} {h} {u} {mi} 4" in cells, k
    # the route: the same evaluation, table and plot (CLI/UI parity), and its own run folder holds the plot too
    rr = _dry_route(p)
    assert rr.status_code == 200, rr.text
    _same(data, rr.json())
    assert (p.root / rr.json()["run_dir"] / SVG_NAME).read_text(encoding="utf-8") == svg
    # the Dry run card: the plot and the text table with the same numbers
    page = run_node(ESC + PAGE + (STATIC / "coverage_bars.js").read_text(encoding="utf-8")
                    + (STATIC / "dryrun.js").read_text(encoding="utf-8") + f"""
console.log(JSON.stringify({{ html: DryRun.resultHtml({json.dumps(rr.json())}, {{ scopus: "Scopus", pubmed: "PubMed" }}) }}));
""")["html"]
    assert _plot_numbers(re.search(r"<svg.*?</svg>", page, re.S).group(0)) == want
    shown = {}
    for k, row in re.findall(r'<tr data-platform="([^"]+)">(.*?)</tr>', page, re.S):
        n = dict(re.findall(r'<td data-state="(\w+)">(\d+)</td>', row))
        shown[k] = (int(n["hits"]), int(n["uncertain"]), int(n["misses"]))
    assert shown == want
    assert "Scopus" in page and f"{rr.json()['run_dir']}/{SVG_NAME}" in page
