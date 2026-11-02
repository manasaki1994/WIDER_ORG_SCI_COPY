"""Fixes from the fourteenth round of C1 checks: the explorer's cell pool on a ranking smaller than the pool,
undo-last without a name off the History page, a screening limit that is not a whole number, the skip link used on
Home, undo-last after Redo, an empty question list, a development share the page cannot read, the History name after
it was cleared elsewhere, explorer paging at the ends, and names and announcements for a few controls."""
import json

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_history import _label
from tests.unit.test_query_roles import APP_JS, STATIC, run_js
from tests.unit.test_rank import _ranked
from tests.unit.test_records import _deduped, _sample
from tests.unit.test_screen import _fake
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from tests.unit.test_ui_followup_r11 import _undo_harness
from wider.construct import artifacts
from wider.core import journal
from wider.core.project import Project
from wider.rank.score import explorer, rank
from wider.sample.labels import label
from wider.ui.app import create_app


def test_cell_pool_is_never_more_than_the_ranking_holds(tmp_path):
    p = _ranked(tmp_path, 6, 2, 50)   # a pool of 50 on a ranking of 6 records
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    d = explorer(p)
    assert d["top_pool"] == 6 and sum(d["best_cell_top3000"].values()) == 6


def test_undo_last_without_a_name_off_the_history_page_only_says_so():
    out = _undo_harness({"stored": "", "id": None, "reply": None, "offPage": True})
    assert out["calls"] == [] and out["out"] is None and out["focus"] is None and out["msg"] == ""
    assert out["toasts"] == [{"t": "Undo needs your name: set it with Change in the top bar. Nothing was undone.",
                              "tone": "danger", "label": "Set your name"}]   # a refusal is an alert (22nd round)


def test_a_screening_limit_that_is_not_a_whole_number_is_refused_in_plain_words(screen_project, monkeypatch):  # noqa: F811
    project = screen_project
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    c = TestClient(create_app(project))
    for bad in (2.5, "abc", "2.5", "3 records", True, False):   # JSON true is not the number 1 (31st round)
        for route in ("/api/jobs/screen", "/api/screen/run"):
            r = c.post(route, json={"scope": "corpus", "limit": bad, "pass_name": "Z"})
            assert r.status_code == 400 and "the record limit must be a whole number" in r.json()["detail"], (bad, route, r.text)
            assert "leave it empty to screen every record" in r.json()["detail"]
    for route in ("/api/jobs/screen", "/api/screen/run"):   # both routes (32nd round)
        r = c.post(route, json={"scope": "corpus", "limit": "0"})
        assert r.status_code == 400 and "the record limit must be at least 1" in r.json()["detail"], route
        # the page sends the digits as typed: a 23-digit limit is quoted as typed, never as 1e+23
        r = c.post(route, json={"scope": "corpus", "limit": "99999999999999999999999"})
        assert r.status_code == 400 and "at most 1,000,000,000 (got 99999999999999999999999)" in r.json()["detail"], route
    assert counter["calls"] == 0 and project.store.scalar("SELECT COUNT(*) FROM runs") == 0
    assert project.store.scalar("SELECT COUNT(*) FROM jobs") == 0   # refused before a job is started
    assert c.post("/api/jobs/screen", json={"scope": "corpus", "limit": " ", "pass_name": "Z"}).status_code == 200   # empty: every record
    params = json.loads(project.store.scalar("SELECT params FROM jobs WHERE kind='screen'"))
    assert params["limit"] is None   # no limit: every record of the scope


def test_skip_link_on_home_keeps_home():
    # Home keeps #home in the address; the link's own target (#main) would otherwise replace it
    line = next(ln for ln in APP_JS.splitlines() if ".skip-link" in ln)
    out = run_js("""
const location = { hash: "#home" }; let handler = null; const focused = [];
const document = { querySelector: (s) => (s === ".skip-link" ? { addEventListener: (t, fn) => { handler = fn; } } : null) };
const $ = (s) => ({ focus: () => focused.push(s) });
""" + line + """
const ev = { prevented: false, preventDefault() { this.prevented = true; } };
handler(ev);
console.log(JSON.stringify({ prevented: ev.prevented, hash: location.hash, focused }));""", {})
    assert out == {"prevented": True, "hash": "#home", "focused": ["#main"]}


def test_undo_last_after_redo_undoes_the_change_again(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1])
    label(p, "dev", ids[0], "ana", "include")
    u = journal.undo_last(p, "ana")
    journal.undo(p, u["change_id"], author="ana")   # the page's Redo
    assert _label(p, ids[0])["decision"] == "include"
    first = next(c for c in journal.changes(p) if c["kind"] != "undo")
    assert first["undone_by"] is None and first["undoable"]   # in effect again, so History offers Undo
    again = journal.undo_last(p, "ana")
    assert _label(p, ids[0]) is None and again["undo_of"] == first["change_id"]
    with pytest.raises(ValueError, match="nothing to undo by ana"):
        journal.undo_last(p, "ana")


def test_a_project_needs_at_least_one_question(tmp_path):
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    before = Project.load(p.root).config.questions
    r = c.patch("/api/project", json={"questions": ["  ", ""]}, headers={"X-Wider-Author": "ana"})
    assert r.status_code == 400 and "at least one research question" in r.json()["detail"]
    assert Project.load(p.root).config.questions == before   # nothing was saved


def test_init_drops_blank_questions(tmp_path):
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    runner = CliRunner()
    r = runner.invoke(cli_app, ["init", str(tmp_path / "blank"), "-q", "", "-q", "  "], env={"COLUMNS": "200"})
    assert r.exit_code == 1 and "a project needs at least one research question; nothing was saved" in r.output
    assert not (tmp_path / "blank").exists()
    r = runner.invoke(cli_app, ["init", str(tmp_path / "one"), "-q", " ", "-q", " Q? "], env={"COLUMNS": "200"})
    assert r.exit_code == 0 and Project.load(tmp_path / "one").config.questions == ["Q?"]


def test_a_development_share_out_of_range_is_said_in_plain_words(tmp_path):
    p, _ = _deduped(tmp_path)
    r = TestClient(create_app(p)).post("/api/benchmark/split", json={"dev_share": -1})
    assert r.status_code == 400 and "the development share must be a number from 0 to 1, e.g. 0.6" in r.json()["detail"]
    assert r.json()["detail"].endswith("(got -1)")   # the share as typed, not "-1.0" (21st round)


def test_seed_split_reads_the_share_before_sending():
    js = (STATIC / "setup.js").read_text(encoding="utf-8")
    body = js[js.index('const sb = q("#seed-split-box"), raw'):js.index('lastSplit = await post("/api/benchmark/split"')]
    assert "sb.hidden = true" in body and "lastSplit = null" in body and "Number.isFinite(Number(raw))" in body
    assert "if (!raw || !Number.isFinite(Number(raw)))" in body and "e.g. 0.6" in body   # an empty field is refused too


def test_history_shows_the_remembered_name_after_it_was_cleared_elsewhere():
    # one name (D33): History has no field of its own; a name cleared with Change leaves "Undo my last change"
    # sending nothing, and the page says where to set it
    out = _undo_harness({"stored": "", "id": None, "reply": {"change_id": 1, "summary": "x"}})
    assert out["calls"] == [] and out["out"] is None
    assert out["msg"].startswith("enter your name first: use Change beside “Working as” in the top bar")
    assert 'id="hist-name"' not in (STATIC / "index.html").read_text(encoding="utf-8")


def test_page_controls_named_and_bounded():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="art-text" rows="28" spellcheck="false" aria-label=' in html
    assert '$("#rk-prev").disabled = rkOffset === 0;' in APP_JS
    assert '$("#rk-next").disabled = !d.ranking || rkOffset + d.rows.length >= d.n_rows;' in APP_JS
    assert '<div class="stage-error" role="alert">' in APP_JS
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert 'data-act="cancel" autofocus>Cancel' in common   # the dialog never passes over "Allow and continue"


def test_blank_questions_beside_a_real_one_are_dropped_on_the_page(tmp_path):
    # spec-checker, 28th round: the page drops blanks like `wider init -q` does
    p, _ = _deduped(tmp_path)
    c = TestClient(create_app(p))
    r = c.patch("/api/project", json={"questions": ["  ", " Q? ", ""]}, headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200, r.text
    assert Project.load(p.root).config.questions == ["Q?"]
