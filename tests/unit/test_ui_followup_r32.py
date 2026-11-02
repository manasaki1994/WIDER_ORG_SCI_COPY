"""Tests asked for by the thirty-second round of C1 checks: `wider history list --author` with a whole long name finds
that reviewer's changes as the page does, `wider history undo` with no name known is refused and undoes nothing, a
second screen quotes a bad protocol name as typed, `wider rank show` prints the page's corpus line, a second Esc closes
the drawer, every message area is a status area and the icon rail keeps each stage's name."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_drawer_keys_node import STATIC
from tests.unit.test_records import _deduped
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.core import journal
from wider.ui.app import create_app


def _rows(output: str) -> list[list[str]]:
    return [[c.strip() for c in line.split("│")[1:-1]] for line in output.splitlines() if line.startswith("│")]


def test_history_list_with_a_whole_long_name_finds_that_reviewer_s_changes(tmp_path):
    p, ids = _deduped(tmp_path)
    long_name = "reviewer " + "x" * 96
    c = TestClient(create_app(p))
    for path, body in ((f"/api/records/{ids[0]}/tags", {"tag": "check"}), (f"/api/records/{ids[0]}/notes", {"text": "a note"})):
        assert c.post(path, json={"author": long_name, **body}).status_code == 200
    page = c.get("/api/history", params={"author": long_name}).json()
    runner = CliRunner()
    found = []
    for name in (long_name, "  " + long_name, long_name[:80]):
        r = runner.invoke(cli_app, ["history", "list", "--author", name, "--dir", str(p.root)], terminal_width=400)
        assert r.exit_code == 0 and "no changes recorded yet" not in r.output, r.output
        found.append(sorted(int(row[0]) for row in _rows(r.output) if row and row[0].isdigit()))
    assert len(page) == 2 and found == [sorted(ch["change_id"] for ch in page)] * 3
    assert journal.changes(p, author="   ") == journal.changes(p)   # a blank filter is no filter, as before


def test_cli_undo_last_with_no_name_known_is_refused(tmp_path, monkeypatch):
    p, ids = _deduped(tmp_path)
    c = TestClient(create_app(p))
    assert c.post(f"/api/records/{ids[0]}/notes", json={"author": "ana", "text": "a note"}).status_code == 200
    before = [(ch["change_id"], ch["undone_by"]) for ch in journal.changes(p)]
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    monkeypatch.setenv("USER", "")
    for extra in ([], ["--author", "  "]):
        r = CliRunner().invoke(cli_app, ["history", "undo", *extra, "--dir", str(p.root)])
        assert r.exit_code == 1 and "whose change? give --author or set $WIDER_REVIEWER" in r.output, r.output
    assert [(ch["change_id"], ch["undone_by"]) for ch in journal.changes(p)] == before


# ---- ranking and screening: the second screen's name, the corpus line of `wider rank show`, the ablate help


def test_a_second_screen_quotes_a_bad_protocol_name_as_typed_and_says_why_a_long_one_is_refused(screen_project, monkeypatch):  # noqa: F811
    from tests.unit.test_screen import _fake
    from wider.screen.protocol import run_protocol, second_protocol_name, second_screen
    p = screen_project
    counter = {"calls": 0, "flip": True}
    _fake(monkeypatch, counter)
    run_protocol(p, "corpus", "main")
    n_runs, calls = p.store.scalar("SELECT COUNT(*) FROM runs"), counter["calls"]
    c = TestClient(create_app(p))
    bad, long = "<b>x</b>", "a" * 74
    for name, words in ((bad, "(got '<b>x</b>')"), (long, "at most 73 characters here (got 74)")):
        with pytest.raises(ValueError) as e:
            second_screen(p, name)
        assert words in str(e.value)
        r = CliRunner().invoke(cli_app, ["screen", "second", name, "--dir", str(p.root)])
        assert r.exit_code == 1 and words in " ".join(r.output.split()) and "Traceback" not in r.output, r.output
        for route in ("/api/jobs/screen", "/api/screen/run"):
            r = c.post(route, json={"scope": "corpus", "mode": "second", "protocol": name})
            assert r.status_code == 400 and words in r.json()["detail"], (route, r.text)
    assert counter["calls"] == calls and p.store.scalar("SELECT COUNT(*) FROM runs") == n_runs   # nothing was screened
    assert second_protocol_name("a" * 73) == "a" * 73 + "_second"   # 73 is the longest name that fits in 80


def test_rank_show_prints_the_corpus_line_of_the_page_and_ablate_help_says_beside(tmp_path):
    from tests.unit.test_rank import _ranked
    from wider.rank.score import corpus_overview
    p = _ranked(tmp_path, 120, cluster_k=3, cluster_top=3000)
    ov = corpus_overview(p)
    line = f"{ov['records']} unique records ({ov['thin']} thin) · embedding sets: fake 64d"
    r = CliRunner().invoke(cli_app, ["rank", "show", "--limit", "3", "--dir", str(p.root)], terminal_width=200)
    assert r.exit_code == 0 and r.output.splitlines()[0] == line, r.output
    assert ov["records"] == 120
    r = CliRunner().invoke(cli_app, ["rank", "ablate", "--help"], terminal_width=200)
    assert "Also report the validation set beside the development set" in " ".join(r.output.replace("│", " ").split())


# ---- drawer and shell: Esc closes the drawer, message areas are status areas, the rail keeps the stage names


def test_a_second_esc_closes_the_drawer():
    from tests.unit.test_drawer_keys_node import run_drawer
    out = run_drawer(r"""
const note = mk("#dw-note", "TEXTAREA"); note.focus();
listeners.keydown(key("Escape")); const first = [document.activeElement.sel, Drawer.isOpen()];
listeners.keydown(key("Escape")); const second = Drawer.isOpen();
console.log(JSON.stringify({ first, second }));""")
    assert out == {"first": ["#drawer", True], "second": False}


def test_every_message_area_is_a_status_area():
    from tests.unit.test_ui_followup_r30 import MSG_AREA
    found = [(f.name, m.group(0)) for f in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.js"))
             for m in MSG_AREA.finditer(f.read_text(encoding="utf-8"))]
    assert len(found) >= 20 and [x for x in found if 'role="status"' not in x[1]] == []


def test_the_icon_rail_keeps_each_stage_s_name_in_its_label():
    """Each stage link keeps its short label (the name read out in the icon rail); the whole shell.js runs on the
    stand-in page."""
    from tests.unit.test_shell_js_node import LINK, shell_page, stages
    core = [r for r in stages({}) if r["state"] != "extension"]
    out = shell_page(f"{LINK} renderShell(); console.log(JSON.stringify(links().map(x => [x.key, x.label])));",
                     core, None)
    assert out[1:] == [[s["key"], s["short"]] for s in core] and all(s["short"] for s in core)
    assert dict(out)["dedup"] == "Deduplication" and out[0] == ["model", "Model for query creation"]


# ---- spec-checker, 32nd round (second report): the acceptance halves the tests above left to the name function or the CLI


def test_a_protocol_of_73_letters_is_second_screened_from_the_engine_the_cli_and_both_page_routes(screen_project, monkeypatch):  # noqa: F811
    from tests.unit.test_screen import _fake
    from wider.screen.protocol import run_protocol
    p, name = screen_project, "a" * 73
    _fake(monkeypatch, {"calls": 0, "flip": True})
    run_protocol(p, "corpus", name)
    r = CliRunner().invoke(cli_app, ["screen", "second", name, "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    second = p.store.scalar("SELECT COUNT(*) FROM screen_decisions WHERE protocol=?", (name + "_second",))
    assert second
    c = TestClient(create_app(p))
    for route in ("/api/screen/run", "/api/jobs/screen"):
        r = c.post(route, json={"scope": "corpus", "mode": "second", "protocol": name})
        assert r.status_code == 200, (route, r.text)


def test_the_page_s_split_follows_the_project_s_development_share(tmp_path):
    from wider.construct import seed
    from wider.core.project import Project
    p = Project.init(tmp_path / "r")
    seed.add_by_dois(p, [f"10.1000/synth.{i:03d}" for i in range(1, 11)])
    setup_js = (STATIC / "setup.js").read_text(encoding="utf-8")
    # the page's share field is filled from the project, and Suggest a split sends that field's value
    assert 'id="seed-share" type="number" step="0.01" min="0" max="1" value="${d.dev_share}"' in setup_js
    assert 'post("/api/benchmark/split", { dev_share: Number(raw) })' in setup_js
    for share, n_dev in ((0.3, 3), (0.8, 8)):
        p.config.protocol.identification.development_share = share
        p.save()
        c = TestClient(create_app(Project.find(p.root)))
        shown = c.get("/api/benchmark/seed").json()["dev_share"]
        assert shown == share
        split = c.post("/api/benchmark/split", json={"dev_share": shown}).json()
        assert (len(split["development"]), len(split["validation"]), split["dev_share"]) == (n_dev, 10 - n_dev, share)
