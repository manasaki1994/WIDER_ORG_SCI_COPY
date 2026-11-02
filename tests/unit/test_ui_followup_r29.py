"""Tests asked for by the twenty-ninth round of C1 checks: a blind labelling queue shown for one name does not
stay when another name is set (the page reloads, ui-redesign D33), a number too large to read is refused through the page
route, and `wider rank show` lists the cell scores best first, as the explorer page does."""
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_drawer_keys_node import STATIC, run_node
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.ui.app import create_app

RECORDS = (STATIC / "records.js").read_text(encoding="utf-8")
HISTORY = (STATIC / "history.js").read_text(encoding="utf-8")


def test_switching_to_another_name_reloads_so_no_queue_stays():
    # ui-redesign D33: the page has no name field; Change in the top bar is the only switch, and another name reloads
    # the page, so a blind queue shown for ana never stays on screen under bo (the same name changes nothing)
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
    out = run_node(QUEUE_PAGE + queue_name("ana") + RECORDS + r"""
apiReply = (p) => p.startsWith("/api/labels/queue") ? [{ record_id: 1, title: "t", decision: "", locked: false }] : {};
await $("#lb-load").onclick(); await flush(); await flush();
const shown = $("#lb-table").innerHTML.includes("<tr data-idx");
const same = WiderName.set(" ana "), sameReloads = location.reloads;
WiderName.set("bo");
console.log(JSON.stringify({ shown, same, sameReloads, reloads: location.reloads }));
""")
    assert out == {"shown": True, "same": False, "sameReloads": 0, "reloads": 1}
    # decisions and notes in the drawer are signed with the name the queue was loaded for, never a later one
    assert "lb.owner = rev;" in RECORDS and "reviewer: owner, decision: d" in RECORDS
    assert "onAnnotate: async () => { lb.ann = await summary(owner, true); }" in RECORDS
    assert 'reviewer: reviewer(), decision' not in RECORDS
    assert "hist-name" not in HISTORY and "lbForgetOther" not in RECORDS


def test_a_number_too_large_to_read_is_refused_through_the_page(tmp_path):
    p = Project.init(tmp_path / "r")
    before = (p.root / "project.yaml").read_text(encoding="utf-8")
    r = TestClient(create_app(p)).post("/api/protocol/set", json={"path": "screening.max_tokens", "value": 1e23})
    assert r.status_code == 400
    assert r.json()["detail"] == "screening.max_tokens was not changed: the number is too large (got 1e+23)"
    assert (p.root / "project.yaml").read_text(encoding="utf-8") == before


def test_rank_show_lists_the_cell_scores_best_first(tmp_path):
    from tests.unit.test_ui_followup_r16 import _two_cells
    from wider.rank.score import explorer
    p = _two_cells(tmp_path)
    rows = explorer(p, limit=12)["rows"]
    r = CliRunner().invoke(cli_app, ["rank", "show", "--limit", "12", "--dir", str(p.root)], env={"COLUMNS": "250"})
    assert r.exit_code == 0, r.output
    for row in rows:   # each row's scores, best first, as the page sorts them
        best_first = sorted(row["cell_scores"].items(), key=lambda x: -x[1])
        assert " ".join(f"{c} {s:.2f}" for c, s in best_first) in r.output
    assert any(list(row["cell_scores"]) != [c for c, _ in sorted(row["cell_scores"].items(), key=lambda x: -x[1])]
               for row in rows)   # some row is not already in best-first order, so the sort is checked
