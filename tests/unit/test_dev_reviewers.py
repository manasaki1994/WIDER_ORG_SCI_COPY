"""Reviewers for the development sample (ui-redesign D4, task 3.0): `sampling.development_reviewers` (one or two),
stored, set on the page and with `wider protocol set`, journalled, named in the methods draft; completeness of a
purposive sample under it, and the open review package rule for development packages. Synthetic records; reviewers
ana, bo and cy; nothing calls a model or the network."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.core.project import Project
from wider.core.protocol import ReviewProtocol
from wider.recall.outputs import methods_draft
from wider.sample import labels as lb
from wider.ui.app import create_app

runner = CliRunner()
PATH = "sampling.development_reviewers"
STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")


def _project(root, n_dev: int = 4) -> Project:
    """``development`` (purposive, records 1..n_dev) and ``validation`` (probability, the next 3), neither locked."""
    p = Project.init(root, questions=["q"])
    for i in range(1, n_dev + 4):
        p.store.execute("INSERT INTO records(record_id, cluster_id, key, title, year) VALUES(?,?,?,?,2020)",
                        (i, i, f"k{i}", f"Synthetic paper {i}"))
    for sid, name, kind, ids in ((1, "development", "purposive", range(1, n_dev + 1)),
                                 (2, "validation", "probability", range(n_dev + 1, n_dev + 4))):
        p.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(?,?,?,'{}',?,'x')",
                        (sid, name, kind, len(ids)))
        for pos, rid in enumerate(ids):
            p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,?)", (sid, rid, pos))
    return p


def samples_settings_html(payload: dict) -> str:
    """The settings card of Samples and labelling drawn by settings_card.js under Node with ``payload`` as
    ``GET /api/protocol``."""
    if not NODE:
        pytest.skip("node is not installed")
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    common = common[:common.index("let lastControl")]          # esc and cmdFold
    card = (STATIC / "settings_card.js").read_text(encoding="utf-8")
    script = ('const els = {}; global.window = global; global.document = { querySelector(s) { return (els[s] ||= '
              '{ innerHTML: "", querySelector() { return null; } }); } };\n'
              f"global.api = async () => ({json.dumps(payload)});\n{common}\n{card}\n"
              'SettingsCard.draw("#sm-settings", "samples").then(() => console.log(els["#sm-settings"].innerHTML))'
              '.catch(e => { console.error(e); process.exit(1); });')
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return res.stdout


def _all(p, who: str, decision: str = "include", ids=range(1, 5)) -> None:
    for rid in ids:
        lb.label(p, "development", rid, who, decision)


def test_setting_page_and_cli(tmp_path):
    a, b = _project(tmp_path / "a"), _project(tmp_path / "b")
    _all(a, "ana")
    r = runner.invoke(cli_app, ["protocol", "set", PATH, "2", "--dir", str(a.root)], env={"COLUMNS": "300"})
    assert r.exit_code == 0, r.output
    out = " ".join(r.output.split())
    # the effect names the purposive sample the setting leaves incomplete and the output that would be hidden
    assert "development (4 of 4 records without a final label)" in out and "hidden" in out
    c = TestClient(create_app(b))
    page = c.get("/api/protocol").json()
    row = next(x for x in page["rows"] if x["path"] == PATH)
    assert row["value"] == 1 and row["default"] == 1
    assert "Reviewers for the development sample" in row["description"]
    assert "agreement or adjudication" in row["description"]
    assert PATH in page["effects"]                        # shown before saving, for the other value
    assert page["effects"][PATH].startswith("If you change it to 2: With two reviewers")   # not read as the value set
    d = c.post("/api/protocol/set", json={"path": PATH, "value": 2}, headers={"X-Wider-Author": "bo"}).json()
    assert "With two reviewers" in d["effects"][PATH] or "With one reviewer" in d["effects"][PATH]
    pa, pb = Project.load(a.root), Project.load(b.root)
    assert pa.config.protocol.sampling.development_reviewers == pb.config.protocol.sampling.development_reviewers == 2
    ha = TestClient(create_app(pa)).get("/api/history").json()
    hb = c.get("/api/history").json()
    assert PATH in ha[0]["summary"] and PATH in hb[0]["summary"]
    assert ha[0]["author"] and hb[0]["author"] == "bo"   # signed by its author
    text = methods_draft(pb)
    assert "The development sample was labelled by two reviewers, with disagreements adjudicated." in text
    assert "labelled by two reviewers" not in methods_draft(_project(tmp_path / "c"))
    # the page shows it with its explanation in normal text: the settings card on Samples and labelling
    html = samples_settings_html(page)
    field = html[html.index(f'data-ps-field="{PATH}"'):]
    assert f'data-ps-set="{PATH}"' in field and "<p>" + row["description"].split(".")[0] in field
    assert "muted" not in field and 'class="small"' not in field.split("data-ps-set")[0]
    assert '<p data-ps-effect="sampling.development_reviewers">If you change it to 2: With two reviewers' in field
    with pytest.raises(ValueError):
        ReviewProtocol().set(PATH, 3)


def test_three_reviewers(tmp_path):
    p = _project(tmp_path / "p")
    p.config.protocol = p.config.protocol.set(PATH, 2)
    p.save()
    _all(p, "ana")
    _all(p, "bo")
    lb.label(p, "development", 1, "cy", "exclude")      # cy disagrees on record 1
    c = lb.completeness(p, "development")
    assert not c["complete"] and c["missing"] == [1] and c["open"] == [1]
    lb.adjudicate(p, "development", 1, "include", by="ana")
    assert lb.completeness(p, "development")["complete"]


def test_default_one(tmp_path):
    p = _project(tmp_path / "p")
    assert p.config.protocol.sampling.development_reviewers == 1
    assert Project.load(p.root).config.protocol.sampling.development_reviewers == 1
    _all(p, "ana", ids=range(1, 4))
    c = lb.completeness(p, "development")
    assert not c["complete"] and c["missing"] == [4] and c["no_label"] == [4]
    lb.label(p, "development", 4, "ana", "exclude")
    assert lb.completeness(p, "development")["complete"]
    # with two required, one reviewer's labels are never final; the validation sample keeps its own rule
    p.config.protocol = p.config.protocol.set(PATH, 2)
    p.save()
    c = lb.completeness(p, "development")
    assert not c["complete"] and len(c["missing"]) == 4
    assert any("fewer than 2 reviewers" in line for line in c["lines"])
    assert lb.completeness(p, "validation")["rule"].endswith("keeps the sample incomplete.")


def test_development_package_out_keeps_the_sample_incomplete(tmp_path):
    from tests.unit.test_label_completion import _decisions_as_r1
    from tests.unit.test_package import _project as _pkg_project
    from tests.unit.test_package import _write
    from wider.sample import package as pk

    p = _pkg_project(tmp_path, "o", n_dev=4, n_pos=10, n_exc=10)
    for rid in range(1, 5):
        lb.label(p, "dev", rid, "R1", "exclude")
    assert lb.completeness(p, "dev")["complete"]
    P = pk.make(p, "R2", ["dev"], "independent")
    c = lb.completeness(p, "dev")
    assert not c["complete"] and c["packages_out"] == ["R2"]
    assert "an open review package keeps the sample incomplete" in c["rule"]
    half = max(P["n_items"] // 2, 1)
    pk.import_file(p, _write(tmp_path, _decisions_as_r1(p, P["package_id"], half)))
    assert lb.completeness(p, "dev")["packages_out"] == ["R2"]   # a partly imported package keeps it open
    pk.withdraw(p, P["package_id"], "the reviewer left", by="Owner")
    assert lb.completeness(p, "dev")["complete"]
