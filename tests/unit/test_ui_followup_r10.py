"""Fixes from the tenth round of C1 checks (bad-input ui-tester): undo-last without a name points at Change in the
top bar, Run is disabled while its request runs, the tag message names the first-character rule, the dedup heading
counts every decision since the last run, Undo buttons have names, the cell filter lists every cell, and the CLI
history table wraps long names instead of cutting them."""
from pathlib import Path

import pytest
import rich
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_rank import _ranked
from tests.unit.test_records import _deduped
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import annotations as ann
from wider.dedup.engine import queue_rows
from wider.rank.score import explorer, rank
from wider.ui.app import create_app

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_undo_last_without_a_name_points_at_change():
    js, html = read("history.js"), read("index.html")
    assert "prompt(" not in js                       # a native prompt() does nothing where the browser blocks it
    assert 'id="hist-name"' not in html and "function askName" not in js   # one name, set in the top bar (D33)
    need = js[js.index("function needName"):js.index("async function undo")]
    # on the History page the message is the shared line with a button that opens the name dialog
    assert 'currentPage === "history"' in need and 'q("#hist-msg").innerHTML = WiderName.needHtml("")' in need
    assert "set it with Change in the top bar. Nothing was" in need
    body = js[js.index("async function undo"):js.index("let lastOffer")]
    assert 'if (!id && !who) { needName("Undo"); return null; }' in body
    common = read("common.js")
    assert "data-set-name>Set your name</button>" in common


def test_screening_run_is_disabled_while_its_request_runs():
    js = read("app.js")
    body = js[js.index("function screenBody()"):js.index("// ---------------- recall + report page")]
    assert body.index("btn.disabled = true") < body.index('Jobs.start("/api/jobs/screen"')
    assert 'finally { btn.disabled = !scPicked; }' in body


def test_undo_buttons_have_names():
    assert 'aria-label="Undo change ${c.change_id}: ${h(c.summary)}"' in read("history.js")
    # the pair drawer has no name field of its own any more (D33): it signs with the top bar's name
    assert "Your name, so decisions can be attributed" not in read("drawer.js")


def test_tag_message_names_the_first_character_rule(tmp_path):
    p, ids = _deduped(tmp_path)
    for bad in ("_lead", ".x"):   # a leading "-" is trimmed, so "-x" is the tag "x"
        with pytest.raises(ValueError, match="starting with a letter or digit"):
            ann.add_tag(p, ids[0], "ana", bad)


def test_dedup_heading_counts_every_decision_since_the_last_run(tmp_path):
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    before = c.get("/api/dedup").json()
    assert before["decided_since_run"] == 0
    q = queue_rows(p)[0]
    assert c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "not_duplicate", "by": "ana"}).status_code == 200
    after = c.get("/api/dedup").json()   # the page reloaded: the decision is no longer a row marked on it
    assert after["decided_since_run"] == 1 and after["pending"] == before["pending"] - 1
    assert after["flow"]["decided_since"] == 1 and after["flow"]["decided"] == before["flow"]["decided"] + 1
    assert c.post("/api/dedup/run").status_code == 200
    again = c.get("/api/dedup").json()
    assert again["decided_since_run"] == 0 and again["flow"]["decided_since"] == 0   # a run applies them
    # since A1 the list heading is built in dedup.js from the flow counts the server sends
    assert "`${f.to_decide} open, ${f.decided} decided`" in read("dedup.js")


def test_cell_filter_lists_every_cell_of_the_anchors(tmp_path):
    p = _ranked(tmp_path, 12, 2, 5)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    d = explorer(p)
    assert list(d["best_cell_top3000"]) == ["voice_cell", "ocean_cell"]   # anchor order, also a cell best for no record
    assert sum(d["best_cell_top3000"].values()) == 5
    assert d["top_pool"] == 5   # the label names the pool the counts come from, never more than the ranking holds
    js = read("app.js")
    assert "in top ${esc(d.top_pool)}" in js and "in top 3000" not in js


def test_explorer_rows_show_every_cell_score():
    js = read("app.js")
    row = js[js.index('$("#rk-table").innerHTML'):js.index('$("#rk-ranking").onchange')]
    assert "<th>cell scores (best first)</th>" in row and "cellScores(r.cell_scores)" in row
    fn = js[js.index("function cellScores"):js.index("async function loadExplorer")]
    # the two best by name, the others in the "+N more" hover text, all best first (UX review F24; rendered in
    # test_plain_words_ui.py::test_the_cell_score_column_names_the_two_best_cells)
    assert "sort((a, b) => b[1] - a[1])" in fn and "<b>" in fn and "esc(rest.map(one)" in fn


def test_the_drawer_shows_its_keyboard_focus():
    css = read("style.css")
    assert ".drawer:focus:not(:focus-visible) { outline:none; }" in css and ".drawer:focus { outline:none; }" not in css


def test_cli_history_wraps_long_names(tmp_path, monkeypatch):
    # rich keeps one console with the width it first saw: a fresh one reads COLUMNS=80, and the old one comes back after
    monkeypatch.setattr(rich, "_console", None)
    p, ids = _deduped(tmp_path)
    name = "Reviewer" + "x" * 50
    ann.add_note(p, ids[0], name, "a note")
    r = CliRunner().invoke(cli_app, ["history", "list", "--dir", str(p.root)], env={"COLUMNS": "80"})
    assert r.exit_code == 0 and "…" not in r.output and max(map(len, r.output.splitlines())) <= 80
    who = "".join(line.split("│")[3].strip() for line in r.output.splitlines() if line.count("│") > 3)
    assert name in who   # the "who" column folds the name over several lines
