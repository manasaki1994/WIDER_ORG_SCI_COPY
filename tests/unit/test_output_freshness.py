"""Reporting fixes of the whole-app inspection (1 Nov 2026): output files written before the review's data last changed
are marked "may be out of date" (never rewritten), one provisional sentence for every PRISMA output, the recall
headline, and manuscript text without CLI commands or garbled fragments. Synthetic, topic-neutral data only."""
import os

import pytest

from tests.unit.test_agreement import fill, proj, rules  # noqa: F401 - pytest fixture
from tests.unit.test_results_dashboard import COUNTED, _after, api, cli
from wider.recall import dashboard as dash
from wider.recall import outputs, reporting
from wider.recall import validation as val

OLD = 1_600_000_000   # 2020-09-13, before any change the tests record


def _change(p, kind="label", at="2026-11-01T10:00:00+00:00"):
    p.store.execute("INSERT INTO history(at, author, kind, summary, items) VALUES(?, 'Tester A', ?, 'x', '[]')",
                    (at, kind))


def _write(p, rel, when=OLD):
    f = p.root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("x", encoding="utf-8")
    os.utime(f, (when, when))
    return f


def test_documents_marked_out_of_date(proj):  # noqa: F811
    fill(proj, "main", COUNTED, rules())
    _write(proj, "exports/report.md")
    _write(proj, "exports/prisma_2020.svg")
    _change(proj)
    d = dash.dashboard(proj, "main")
    docs = {x["path"]: x for x in d["documents"]}
    for path, cmd in (("exports/report.md", "wider report"), ("exports/prisma_2020.svg", "wider export prisma")):
        assert docs[path]["stale"] and docs[path]["stale_text"] == f"may be out of date; write it again with {cmd}"
    assert not any(x["stale"] for x in d["documents"] if not x["exists"])
    assert d["documents_stale_line"] == dash.STALE_LINE
    lines = _after(cli(proj, "results", "main").output.splitlines(), "Documents")
    assert lines.count(dash.STALE_LINE) == 1   # the reason said once, a short mark per file
    assert any(x.startswith("Report: exports/report.md, ") and x.endswith(
        "; may be out of date; write it again with wider report") for x in lines)
    assert api(proj).get("/api/results", params={"protocol": "main"}).json()["documents_stale_line"] == dash.STALE_LINE
    # written again after the change: no mark, and no line once nothing is marked
    _write(proj, "exports/report.md", when=OLD * 2)
    _write(proj, "exports/prisma_2020.svg", when=OLD * 2)
    d = dash.dashboard(proj, "main")
    assert not any(x["stale"] for x in d["documents"]) and d["documents_stale_line"] is None
    assert dash.STALE_LINE not in cli(proj, "results", "main").output


def test_last_data_change_leaves_out_notes_and_file_runs(proj):  # noqa: F811
    assert dash.last_data_change(proj) is None
    _change(proj, "label", "2026-11-01T09:00:00+00:00")
    _change(proj, "note", "2026-11-01T11:00:00+00:00")
    _change(proj, "undo", "2026-11-01T09:30:00+00:00")
    for action, at in (("prisma", "2026-11-01T12:00:00+00:00"), ("report", "2026-11-01T12:00:00+00:00"),
                       ("estimate", "2026-11-01T09:45:00+00:00")):
        proj.store.execute("INSERT INTO stage_results(stage, action, ran_at, result) VALUES('recall', ?, ?, '{}')",
                           (action, at))
    assert dash.last_data_change(proj) == "2026-11-01T09:45:00+00:00"


def test_reporting_files_marked_on_the_flow(tmp_path):
    from tests.unit.test_reporting import _imported
    p = _imported(tmp_path)
    assert reporting.flow_state(p)["exports_stale"] is None
    _write(p, "exports/prisma_reporting/prisma_2020_checklist.md")
    _change(p)
    st = reporting.flow_state(p)
    assert st["exports_stale"].startswith("The PRISMA reporting files in exports/prisma_reporting/ were written before")
    assert st["exports_stale"] not in st["checks"]   # the exported flow JSON never carries it
    out = cli(p, "report", "prisma", "flow").output
    assert "may be out of date" in " ".join(out.split())
    _write(p, "exports/prisma_reporting/prisma_2020_checklist.md", when=OLD * 2)
    assert reporting.flow_state(p)["exports_stale"] is None


@pytest.mark.parametrize("flow, line", [
    ({"to_decide": 2, "decided_since": 0, "unsure": 0}, "Provisional: 2 duplicate pairs still wait for a manual decision."),
    ({"to_decide": 1, "decided_since": 0, "unsure": 0}, "Provisional: 1 duplicate pair still waits for a manual decision."),
    ({"to_decide": 0, "decided_since": 3, "unsure": 0},
     "Provisional: 3 decided duplicate pairs are not applied yet; the counts change when deduplication runs again."),
    ({"to_decide": 0, "decided_since": 0, "unsure": 1}, "Provisional: 1 duplicate pair is marked Unsure."),
    ({"to_decide": 0, "decided_since": 0, "unsure": 0}, None)])
def test_provisional_line_says_what_waits(proj, monkeypatch, flow, line):  # noqa: F811
    import wider.dedup.engine as engine
    monkeypatch.setattr(engine, "flow_counts", lambda project, summary=None: flow)
    assert outputs.provisional_line(proj, {"unique_records": 5}, 0) == line
    assert outputs.provisional_line(proj, {}, 0) is None
    assert outputs.provisional_line(proj, {"unique_records": 5}, 4) == (
        "Provisional: 4 records imported since the last deduplication run are not checked for duplicates yet.")


def test_flow_json_carries_the_provisional_line(proj, monkeypatch):  # noqa: F811
    from wider.recall import flow_layout
    fill(proj, "main", COUNTED, rules())
    c = outputs.prisma_counts(proj, "main")
    c["provisional_line"] = "Provisional: 3 decided duplicate pairs are not applied yet."
    layout = flow_layout.flow_boxes(c, {}, [], None)
    js = flow_layout.flow_json(layout, [], "main", 1)
    assert js["provisional"] == js["notes"][0] == c["provisional_line"]
    assert c["provisional_line"] in flow_layout.flow_svg(layout)


def test_recall_headline():
    assert dash._recall_headline({}) is None
    assert dash._recall_headline({"recall": 0.9}) == "Recall: 90.0%"
    assert dash._recall_headline({"recall": 0.9, "variant": "ratio", "bounds": {"low": 0.71, "high": 0.98, "level": 0.95}}) \
        == "Recall (ratio): 90.0%; exact bounds valid at every look (95%): 71.0% to 98.0%"


def test_manuscript_words():
    assert "wider" not in val.NO_TARGET and val.NO_TARGET == "No recall target was set in advance."
    from wider.search.coverage import BOTH_TEXT
    assert BOTH_TEXT.endswith("a development set and a validation set.")


def test_old_no_target_line_reads_as_new():
    assert val.OLD_NO_TARGET != val.NO_TARGET and "wider validation target" in val.OLD_NO_TARGET
