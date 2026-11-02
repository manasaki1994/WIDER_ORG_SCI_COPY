import json

import pytest
import yaml
from fastapi.testclient import TestClient

from wider.construct import artifacts
from wider.construct.benchmark import sync
from wider.construct.propose import ProposalError, build_prompt, propose
from wider.core.models import BenchmarkPaper, ProviderConfig
from wider.core.project import Project
from wider.search.ir import Query, count_terms, term_words
from wider.ui.app import create_app

CELLS = {"construct": "Employee voice and silence",
         "dimensions": [{"name": "form", "values": [{"key": "voice", "label": "Voice", "definition": "speaking up"},
                                                    {"key": "silence", "label": "Silence", "definition": "withholding"}]},
                        {"name": "focus", "values": [{"key": "antecedent", "label": "A", "definition": "a"},
                                                     {"key": "consequence", "label": "C", "definition": "c"}]}],
         "cells": [{"key": "voice_antecedent", "labels": {"form": "voice", "focus": "antecedent"}, "question": "q1"},
                   {"key": "silence_consequence", "labels": {"form": "silence", "focus": "consequence"}, "question": "q2"}]}

SYN = {"blocks": [{"key": "voice", "name": "Voice", "terms": ["voice*", '"speaking up"', "dissent"]},
                  {"key": "actor", "name": "Actor", "terms": ["employee*", {"near": ["staff", "member*"], "distance": 5},
                                                              {"near": ["work*", ["team*", "unit*"]], "distance": 5}]}],
       "logic": {"and": ["voice", "actor"]}}


def _project(tmp_path):
    p = Project.init(tmp_path / "r", questions=["What leads employees to speak up or stay silent at work?"])
    p.write_benchmark([
        BenchmarkPaper(benchmark_id="B1", title="Paper one", doi="10.1000/one", set="Development", abstract=" ".join(["w"] * 30)),
        BenchmarkPaper(benchmark_id="B2", title="Paper two", set="Validation", abstract="secret validation abstract " * 10),
    ])
    return p


def test_versioning_status_and_diff(tmp_path):
    p = _project(tmp_path)
    r1 = artifacts.save_new_version(p, "cells", CELLS, origin="llm", note="first")
    assert r1["version"] == 1 and r1["status"] == "draft" and (p.root / r1["path"]).exists()
    edited = json.loads(json.dumps(CELLS))
    edited["cells"][0]["question"] = "changed"
    # the page's save goes through save_edit, which decides the origin
    r = TestClient(create_app(Project.load(p.root))).post("/api/construct/cells/save", json={"text": yaml.safe_dump(edited)})
    assert r.status_code == 200, r.text
    r2 = r.json()
    assert (r2["version"], r2["path"], r2["status"], r2["origin"], r2["parent_version"]) == (
        2, "artifacts/cells/v2.yaml", "draft", "manual", 1)
    assert (p.root / "artifacts/cells/v2.yaml").exists()
    vs = artifacts.list_versions(p, "cells")
    assert [(v["version"], v["origin"]) for v in vs] == [(1, "llm"), (2, "manual")]
    assert all(len(v["sha256"]) == 64 for v in vs) and vs[0]["sha256"] != vs[1]["sha256"]
    assert "-  question: q1" in artifacts.diff(p, "cells", 1, 2)
    # the CLI list names the sha256 prefix and the parent, like the page's version list
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    res = CliRunner().invoke(cli_app, ["construct", "list", "--dir", str(p.root)], env={"COLUMNS": "200"})
    assert res.exit_code == 0, res.output
    head = next(ln for ln in res.output.splitlines() if "origin" in ln and "sha256" in ln)
    names = [c.strip() for c in head.split("┃")[1:-1]]
    rows = [dict(zip(names, [c.strip() for c in ln.split("│")[1:-1]])) for ln in res.output.splitlines()
            if ln.startswith("│") and "cells" in ln]
    assert [(r["version"], r["origin"], r["from"], r["sha256"]) for r in rows] == [
        ("v1", "llm", "", vs[0]["sha256"][:12]), ("v2", "manual", "v1", vs[1]["sha256"][:12])]
    # the page's version list, run under Node on the same versions: origin, "from v1" and the sha256 prefix
    from tests.unit.test_drawer_keys_node import STATIC, run_node
    line = next(ln for ln in (STATIC / "app.js").read_text(encoding="utf-8").splitlines() if '$("#art-version").innerHTML' in ln)
    out = run_node("const el = {}; const $ = () => el; const esc = (s) => String(s);\nconst vs = "
                   + json.dumps([dict(v) for v in vs], default=str) + ";\n" + line
                   + "\nconsole.log(JSON.stringify(el.innerHTML.match(/>[^<]+</g).map((s) => s.slice(1, -1))));")
    assert out == [f"v2 · draft · manual · from v1 · sha256 {vs[1]['sha256'][:12]}",
                   f"v1 · draft · llm · sha256 {vs[0]['sha256'][:12]}"]
    artifacts.set_status(p, "cells", 2, "approved")
    assert artifacts.latest(p, "cells", status="approved")["version"] == 2
    artifacts.set_status(p, "cells", 2, "frozen")
    with pytest.raises(ValueError):
        artifacts.set_status(p, "cells", 2, "draft")
    # on-disk edit after save is refused for status changes
    path = p.root / r1["path"]
    path.write_text(path.read_text() + "\n# tampered\n")
    with pytest.raises(ValueError):
        artifacts.set_status(p, "cells", 1, "approved")


def test_two_saves_at_once_get_their_own_versions(tmp_path, monkeypatch):
    """A double click on "save as new version" once sent two saves that both picked the same next version; the second
    failed with a database error (seventh round, bad-input tester)."""
    p = _project(tmp_path)
    monkeypatch.setattr(artifacts, "next_version", lambda d, suffix: 1)  # both saves see the same next version
    r1 = artifacts.save_new_version(p, "synonyms", SYN, origin="import")
    r2 = artifacts.save_new_version(p, "synonyms", SYN, origin="import")
    assert (r1["version"], r2["version"]) == (1, 2)
    assert sorted(x.name for x in p.artifact_dir("synonyms").glob("v*.yaml")) == ["v1.yaml", "v2.yaml"]
    assert [r["version"] for r in artifacts.list_versions(p, "synonyms")] in ([1, 2], [2, 1])


def test_validation_rejects_bad_artifacts(tmp_path):
    p = _project(tmp_path)
    with pytest.raises(ValueError):
        artifacts.save_new_version(p, "cells", {"construct": "x"}, origin="manual")
    bad = dict(SYN, logic={"and": ["voice", "nope"]})
    with pytest.raises(ValueError):
        artifacts.save_new_version(p, "synonyms", bad, origin="manual")
    with pytest.raises(ValueError):
        artifacts.save_new_version(p, "anchors", {"anchors": {"c": "too short"}}, origin="manual")
    row = artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    assert row["version"] == 1
    assert artifacts.list_versions(p, "cells") == [] and artifacts.list_versions(p, "anchors") == []
    # the page's save answers 400 with the validation text and stores nothing
    c = TestClient(create_app(Project.load(p.root)))
    for kind, body, needle in [("cells", {"construct": "x"}, "'dimensions' is a required property"),
                               ("anchors", {"anchors": {"c": "too short"}}, "'too short' is too short"),
                               ("synonyms", bad, "unknown block 'nope'")]:
        r = c.post(f"/api/construct/{kind}/save", json={"text": yaml.safe_dump(body)})
        assert r.status_code == 400 and f"{kind} does not validate" in r.json()["detail"] and needle in r.json()["detail"], r.text
    assert artifacts.list_versions(p, "cells") == [] and artifacts.list_versions(p, "anchors") == []
    assert [v["version"] for v in artifacts.list_versions(p, "synonyms")] == [1]


def test_query_ir_helpers():
    q = Query.model_validate(SYN)
    assert count_terms(q) == {"voice": 3, "actor": 3}
    assert term_words(q.block("actor").terms[2]) == ["work", "team", "unit"]
    assert term_words('"follow-up"') == ["follow-up"]


def test_benchmark_sync_and_prompt_hides_validation(tmp_path):
    p = _project(tmp_path)
    rows = sync(p)
    assert [r["benchmark_id"] for r in rows] == ["B1", "B2"]
    prompt, info = build_prompt(p, "cells")
    assert "Paper one" in prompt and "secret validation abstract" not in prompt and "Paper two" not in prompt
    assert info["benchmark_n"] == 1
    with pytest.raises(ProposalError):
        build_prompt(p, "anchors")  # needs cells first


def test_propose_with_fake_provider(tmp_path):
    p = _project(tmp_path)
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.save()
    from wider.providers import fake as fakemod

    anchor = " ".join(["This study examines what leads employees to speak up to their supervisors."] * 12)

    def responder(req):
        if "cells" in req.schema_["properties"]:
            return CELLS
        if "anchors" in req.schema_["properties"]:
            key = "voice_antecedent" if "`voice_antecedent`" in req.system else "silence_consequence"
            return {"style": "aims only", "anchors": {key: anchor}}
        return {}

    orig = fakemod.FakeProvider.__init__

    def patched(self, cfg, responder_=None):
        orig(self, cfg, responder)

    fakemod.FakeProvider.__init__ = patched
    try:
        row = propose(p, "cells", provider_name="fake")
        assert row["version"] == 1 and row["origin"] == "llm"
        artifacts.set_status(p, "cells", 1, "approved")
        row = propose(p, "anchors", provider_name="fake")
        _, data = artifacts.load(p, "anchors", row["version"])
        assert set(data["anchors"]) == {"voice_antecedent", "silence_consequence"}
        assert len(row["calls"]) == 2
        assert (p.root / "runs" / "construct").exists()
    finally:
        fakemod.FakeProvider.__init__ = orig


def test_propose_refuses_kinds_it_cannot_propose(tmp_path):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from wider.cli import app
    from wider.ui.app import create_app

    p = _project(tmp_path)
    runner, d = CliRunner(), ["--dir", str(p.root)]
    r = runner.invoke(app, ["construct", "propose", "adjacent", *d])
    assert r.exit_code == 2 and "wider construct adjacent propose" in " ".join(r.output.split())
    r = runner.invoke(app, ["construct", "propose", "nope", "--show-prompt", *d])
    assert r.exit_code == 2 and "cells, codebook, synonyms, anchors, near_miss" in " ".join(r.output.split())
    assert not (p.root / "runs" / "construct").exists()
    with pytest.raises(ProposalError, match="edited and saved"):
        build_prompt(p, "prompts")
    c = TestClient(create_app(p))
    assert c.post("/api/construct/prompts/propose").status_code == 400
    assert c.get("/api/construct/prompts/prompt").status_code == 400
    assert "Paper one" in c.get("/api/construct/cells/prompt").json()["prompt"]  # not swallowed by /{kind}/{version}
    r = c.post("/api/jobs/propose/adjacent")
    assert r.status_code == 400 and "construct adjacent propose" in r.json()["detail"]
    assert not (p.root / "runs" / "construct").exists()


def test_import_yaml_file(tmp_path):
    p = _project(tmp_path)
    f = tmp_path / "cells.yaml"
    f.write_text(yaml.safe_dump(CELLS))
    row = artifacts.import_file(p, "cells", f)
    assert (row["origin"], row["status"], row["version"]) == ("import", "draft", 1)
    # the CLI twin, and a .json file read the same way
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    j = tmp_path / "cells.json"
    j.write_text(json.dumps(CELLS))
    for path, version in [(f, 2), (j, 3)]:
        out = CliRunner().invoke(cli_app, ["construct", "save", "cells", str(path), "--dir", str(p.root)])
        assert out.exit_code == 0 and f"cells v{version}" in out.output, out.output
    rows = artifacts.list_versions(p, "cells")
    assert [(r["version"], r["origin"], r["status"]) for r in rows] == [(v, "import", "draft") for v in (1, 2, 3)]
    assert artifacts.load(p, "cells", 3)[1] == artifacts.load(p, "cells", 2)[1] == CELLS
