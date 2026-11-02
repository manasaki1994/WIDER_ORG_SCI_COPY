"""The Results dashboard (results-dashboard-and-ris, design D1-D9): `wider results`, `GET /api/results` and
`dashboard()`. Synthetic, topic-neutral records only; no model server is started (nothing here calls one)."""
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.test_agreement import fill, proj, rules  # noqa: F401 - pytest fixture
from tests.unit.test_recall_curve import _bands, _purposive, _sampled
from tests.unit.test_screen_compare import (  # noqa: F401 - _template, cproject and locked are fixtures
    _template,
    cproject,
    fresh,
    locked,
    per_backend,
    use_responder,
)
from tests.unit.test_validation_suggest import _drawn
from wider.cli import app as cli_app
from wider.core.project import Project
from wider.recall import curve
from wider.recall import dashboard as dash
from wider.recall.estimate import estimate
from wider.recall.outputs import export_all_references, prisma_counts
from wider.recall.validation import sealed_lines, status
from wider.sample.labels import label
from wider.screen import compare
from wider.screen import result_sets as rs
from wider.screen.decisions import set_decision
from wider.ui.app import create_app

runner = CliRunner()
KEYS = ("heading", "agreement", "comparisons", "recall", "curve", "counts", "reference_sets", "documents")
SECTIONS = ["Agreement between the passes", "Configuration comparisons (development sample)", "Recall",
            "Relevant found along the ranking", "PRISMA counts", "Reference files", "Documents"]


def cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)])


def api(p):
    return TestClient(create_app(Project.load(p.root)))


def _models(p, key, rid, models=(("A", "a"), ("B", "b"), ("C", "c"))):
    """Calls of each pass on record ``rid`` of set ``key``, one model per pass, as the screening run records them."""
    p.store.execute("INSERT INTO runs(run_id, kind, stamp, spec_hash, provider, model, status, started_at, dir) "
                    "VALUES('run1', 'screen', 's', 'h', 'fake', 'x', 'done', '2026-01-01', 'runs/run1')")
    ids = []
    for ps, m in models:
        p.store.execute("INSERT INTO screen_calls(run_id, record_id, pass, input_sha256, ok, rule_decision, at, model) "
                        "VALUES('run1', ?, ?, 'x', 1, 'include', '2026-01-01', ?)", (rid, ps, m))
        ids.append(p.store.scalar("SELECT MAX(call_id) FROM screen_calls"))
    p.store.execute("UPDATE screen_decisions SET vote_calls=? WHERE protocol=? AND record_id=?", (json.dumps(ids), key, rid))


def _set2(p, n=600):
    """Set 2 of ``main`` (not active): the first 100 records included."""
    rs.new_set(p, "main", "screening", settings=rules())
    for r in range(1, n + 1):
        set_decision(p, "main#2", r, "include" if r <= 100 else "exclude", votes=["exclude", "exclude"], decided_by="A,B")


def _quiet(p):
    """What a look, a stage result or a History entry would change."""
    return (p.store.scalar("SELECT COALESCE(SUM(n), 0) FROM validation_looks"),
            p.store.scalar("SELECT SUM(n) FROM validation_looks"), p.store.scalar("SELECT COUNT(*) FROM stage_results"),
            p.store.scalar("SELECT COUNT(*) FROM history"))


def _after(lines, head):
    return lines[lines.index(head) + 1:]


def test_dashboard_sections_cli_and_json(tmp_path):
    p = _drawn(tmp_path)
    _set2(p)
    _models(p, "main", 1)
    r = cli(p, "results", "main")
    assert r.exit_code == 0, r.output
    lines = r.output.splitlines()
    assert lines[0] == "main (set 1)" and lines[1] == "Pass A: a; pass B: b; third read: c"
    at = [lines.index(s) for s in SECTIONS]
    assert at == sorted(at)
    j = cli(p, "results", "main", "--json")
    assert j.exit_code == 0, j.output
    d = json.loads(j.output)
    assert all(k in d for k in KEYS)
    assert d["heading"]["title"] == "main (set 1)" and d["heading"]["models"] == "Pass A: a; pass B: b; third read: c"
    assert [d["headings"][k] for k in KEYS[1:]] == SECTIONS
    g = api(p).get("/api/results", params={"protocol": "main"})
    assert g.status_code == 200 and g.json() == d
    assert dash.dashboard_lines(dash.dashboard(p, "main")) == lines


def test_other_set_leaves_active_set(tmp_path):
    p = _drawn(tmp_path)
    _set2(p)
    d = dash.dashboard(p, "main", 2)
    assert d["heading"]["title"] == "main (set 2)" and d["result_set_key"] == "main#2"
    assert d["agreement"]["key"] == "main#2" and d["agreement"]["n"] == 600
    assert d["counts"]["result_set"] == 2 and d["counts"]["included_ta"] == 100 and d["counts"]["excluded"] == 500
    ref = {s["set"]: s["records"] for s in d["reference_sets"]["sets"]}
    assert ref["include"] == 100 and ref["exclude"] == 500 and ref["agreed"] == 500
    assert d["choices"]["sets"] == [{"result_set": 1, "label": None, "active": True},
                                    {"result_set": 2, "label": None, "active": False}]
    r = cli(p, "results", "main", "--set", "2")
    assert r.exit_code == 0 and r.output.splitlines()[0] == "main (set 2)"
    g = api(p).get("/api/results", params={"protocol": "main", "result_set": 2, "sample": "validation"})
    assert g.status_code == 200 and g.json()["heading"]["title"] == "main (set 2)"
    assert g.json()["sample"] == "validation"
    assert rs.active_set(fresh(p), "main")["result_set"] == 1          # choosing a set never activates it


def test_nothing_screened(tmp_path):
    p = Project.init(tmp_path / "e", platforms=["scopus"])
    r = cli(p, "results")
    assert r.exit_code == 1 and "no screening decisions yet" in r.output
    g = api(p).get("/api/results")
    assert g.status_code == 400 and g.json()["detail"] == "no screening decisions yet"
    with pytest.raises(ValueError, match="no screening decisions yet"):
        dash.dashboard(p)


def test_comparisons_apart(locked, monkeypatch):  # noqa: F811
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {18}}))
    compare.run(p, "cmp1", "development", ["fake", "fake_b"], repeats=1)
    q = fresh(p)
    rid = q.store.scalar("SELECT MIN(record_id) FROM records")
    set_decision(q, "main", rid, "include")
    d = dash.dashboard(q, "main")
    assert d["comparisons"]["lines"] == compare.report_lines(q)
    assert any(x.startswith("Comparison cmp1 on development: 2 configurations") for x in d["comparisons"]["lines"])
    lines = cli(q, "results", "main").output.splitlines()
    body = _after(lines, "Configuration comparisons (development sample)")
    assert body[:len(d["comparisons"]["lines"])] == d["comparisons"]["lines"]
    assert body[len(d["comparisons"]["lines"])] == "" and lines.index("Configuration comparisons (development "
                                                                       "sample)") > lines.index("Agreement between the passes")


def test_no_comparison_line(tmp_path):
    p = _drawn(tmp_path)
    assert dash.dashboard(p, "main")["comparisons"]["lines"] == ["No configuration comparison was run."]
    lines = cli(p, "results", "main").output.splitlines()
    assert _after(lines, "Configuration comparisons (development sample)")[0] == "No configuration comparison was run."


def test_recall_no_target_said_once(tmp_path):
    """ui-tester finding: without a recall target the status line and the stored outcome both said NO_TARGET."""
    from wider.recall.validation import NO_TARGET

    p = _drawn(tmp_path)
    p.config.protocol.recall.target = None
    p.save()
    estimate(p, "validation", "main", B=50)
    assert NO_TARGET in json.loads(p.store.get_meta("last_validation_lines:validation"))["outcome"]["lines"]
    assert dash.dashboard(p, "main")["recall"]["lines"].count(NO_TARGET) == 1
    assert status(p, "validation")["lines"].count(NO_TARGET) == 1


def test_recall_lines_without_look(tmp_path):
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    before = _quiet(p)
    d = dash.dashboard(p, "main")
    r = cli(p, "results", "main")
    assert r.exit_code == 0
    assert api(p).get("/api/results", params={"protocol": "main"}).status_code == 200
    assert _quiet(fresh(p)) == before                                   # no look, stage result or History entry
    at = json.loads(p.store.get_meta("last_validation_lines:validation"))["at"]
    st = status(p, "validation")
    assert d["recall"]["state"] == "current" and d["recall"]["as_of"] == at and d["recall"]["lines"] == st["lines"]
    body = _after(r.output.splitlines(), "Recall")
    head = d["recall"]["headline"]   # the stored estimate leads, in one line (inspection 1 Nov 2026)
    assert head.startswith("Recall (lenient): ") and "exact bounds valid at every look (95%)" in head
    assert body[0] == head and body[1] == f"as of {at}" and body[2:2 + len(st["lines"])] == st["lines"]
    vs = cli(p, "validation", "status")
    assert vs.exit_code == 0 and vs.output.splitlines()[:len(st["lines"])] == st["lines"]   # same lines, same order
    stored = json.loads(p.store.get_meta("last_validation_lines:validation"))["outcome"]
    assert stored["outcome"] == "cannot_tell" and d["recall"]["warning_lines"] == []   # inconclusive: not a warning


def test_recall_none_yet(tmp_path):
    p = _drawn(tmp_path)
    d = dash.dashboard(p, "main")
    assert d["recall"]["sentence"] == ("No recall estimate on validation yet. Estimate recall on the Recall and report "
                                       "page (wider recall main).")
    assert d["recall"]["lines"] == [] and d["curve"]["points"] == [] and d["curve"]["state"] == "none"
    assert dash.dashboard(p, "main", sample="nosuch")["recall"]["sentence"] == "No sample nosuch."
    _purposive(p, "development", range(1, 6))
    assert dash.dashboard(p, "main", sample="development")["recall"]["sentence"] == (
        "Recall is estimated on probability samples only; development is purposive.")
    _set2(p)
    p.store.execute("INSERT INTO samples(name, kind, design, n, created_at) VALUES('validation2', 'probability', '{}', "
                    "0, '2026-01-01T00:00:00+00:00')")
    assert dash.dashboard(p, "main", 2, "validation2")["recall"]["sentence"] == (   # its design names no bands
        "validation2 has no score bands, so the dashboard keeps no recall lines for it; its recall is shown on the "
        "Recall and report page (wider recall main --set 2 --sample validation2).")
    out = cli(p, "results", "main", "--sample", "nosuch").output.splitlines()
    assert _after(out, "Recall")[0] == "No sample nosuch."


def test_recall_other_set(tmp_path):
    p = _drawn(tmp_path)
    _set2(p)
    estimate(p, "validation", "main", B=50)
    d = dash.dashboard(p, "main", 2)
    assert d["recall"]["sentence"] == (
        "The last recall estimate on validation is for main (set 1), not set 2. Estimate recall for set 2 on the "
        "Recall and report page (wider recall main --set 2); this records a look on set 2.")
    assert d["recall"]["lines"] == [] and d["recall"]["as_of"] is None
    assert d["curve"]["points"] == [] and d["curve"]["state"] == "none"
    body = _after(cli(p, "results", "main", "--set", "2").output.splitlines(), "Recall")
    assert body[0] == d["recall"]["sentence"] and body[1] == ""


def test_curve_table_matches_estimate(tmp_path):
    p = _drawn(tmp_path)
    est = estimate(p, "validation", "main", B=50)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    assert c["state"] == "current" and c["label"] == "Estimate from the validation sample; no stopping rule."
    assert c["legend"] == ["Estimated relevant", "Estimated relevant kept by screening", "Estimated missed by screening"]
    assert c["points"] == curve.stored(p, "validation")["points"] and [x[0] for x in c["points"]] == [100, 300, 600]
    assert c["notes"][0] == ("Points are at band ends; the lines only join them, since the sample says nothing about "
                             "where in a band the relevant records sit.")
    assert c["title"] == "Estimated relevant records along the ranking"
    assert c["axes"] == {"x": "Position in the ranking (1 = highest score)", "y": "Cumulative estimated relevant records"}
    last = c["ends"][-1]
    assert [e["end"] for e in c["ends"]] == [100, 300, 600]
    assert abs(last["kept"] / last["rel"] - est["variants"][c["variant"]]["recall"]) < 1e-6
    assert abs(last["missed"] - (last["rel"] - last["kept"])) < 1e-6
    assert c["desc"] == (f"Up to rank 600: {dash._num(last['rel'])} estimated relevant, {dash._num(last['kept'])} kept "
                         f"by screening, {dash._num(last['missed'])} missed. The table below gives the same numbers.")
    # the CLI prints the table rows the page shows, the last row's cumulative values those of the last point
    body = _after(cli(p, "results", "main").output.splitlines(), "Relevant found along the ranking")
    assert body[0] == c["label"] and body[1].startswith("observed: ranks | records") and "|| estimated: " in body[1]
    assert body[2:2 + len(c["rows"])] == [" | ".join(dash.table_cells(r)) for r in c["rows"]]
    assert c["rows"][-1]["cum"] == {"rel": last["rel"], "kept": last["kept"], "missed": last["missed"]}


def test_curve_without_bounds(tmp_path):
    """A series read with a primary labeller is not what the exact bounds read: no ranges, the sentence says so."""
    p = _drawn(tmp_path)
    r = cli(p, "recall", "main", "--primary", "r1", "-B", "20")
    assert r.exit_code == 0, r.output
    c = dash.dashboard(p, "main")["curve"]
    assert c["state"] == "current" and c["has_bounds"] is False and c["range_legend"] is None and c["method"] is None
    assert c["notes"][1] == "No ranges: the chart's labels are not those of the exact bounds, so it shows point estimates only."
    assert all("rel_low" not in e for e in c["ends"]) and all(r["rel_low"] is None for r in c["rows"])
    assert all("range" not in r["tooltip"] for r in c["rows"])
    assert c["notes"][1] in cli(p, "results", "main").output.splitlines()
    # with the final labels the ranges are there, at the stored level
    estimate(p, "validation", "main", B=20)
    c = dash.dashboard(p, "main")["curve"]
    assert c["has_bounds"] and c["range_legend"] == "Exact range (valid at every look)" and c["level"] == 0.95
    assert c["notes"][1] == ("Ranges: exact bounds valid at every look (95%), summed over the bands up to each end; "
                             "conservative.")
    assert c["method"]["summary"] == "How the estimate and the range are made"


def test_curve_old_series(tmp_path):
    """A series stored before band details: the three lines, no ranges, no tooltips, and the sentence; not recomputed."""
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=20)
    rec = curve.stored(p, "validation")
    old = {k: rec[k] for k in ("result_set_key", "at", "variant", "primary", "bands", "checksum", "points")}
    p.store.set_meta("last_recall_curve:validation", json.dumps(old))
    before = _quiet(p)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    assert c["state"] == "current" and c["old_series"] and c["points"] == rec["points"] and not c["has_bounds"]
    assert [(e["end"], e["rel"], e["kept"], e["missed"]) for e in c["ends"]] == [
        (e, r, k, round(r - k, 6)) for e, r, k in rec["points"]]
    assert all(r.get("tooltip") is None for r in c["rows"])
    sentence = ("Ranges and band details are stored with newer estimates; estimate recall again to show them (this "
                "records a look).")
    assert sentence in c["notes"]
    body = _after(cli(p, "results", "main").output.splitlines(), "Relevant found along the ranking")
    assert body[1] == "band end | relevant | kept | missed" and sentence in body
    assert body[2:5] == [" | ".join(dash.table_cells(r)) for r in c["rows"]]
    assert _quiet(p) == before and curve.stored(p, "validation") == old


def _tip(c, ranks):
    return next(r for r in c["rows"] if r["ranks"] == ranks)


def test_band_table_cli_matches_page(tmp_path):
    p = _bands(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    c = d["curve"]
    row = _tip(c, "51-200")
    assert (row["records"], row["sampled"], row["labelled"], row["relevant"], row["few"]) == (150, 10, 10, 2, True)
    e = c["ends"][1]
    rng = lambda lo, hi: f"range {dash._num(lo)}-{dash._num(hi)}"   # noqa: E731
    want = (f"Ranks 51-200: 150 records, 10 sampled, 10 labelled, 2 relevant observed (few relevant observed). "
            f"Estimated in this band: 30 relevant ({rng(row['rel_low'], row['rel_high'])}), 30 kept, 0 missed. "
            f"Up to rank 200: {dash._num(e['rel'])} relevant ({rng(e['rel_low'], e['rel_high'])}), "
            f"{dash._num(e['kept'])} kept ({rng(e['kept_low'], e['kept_high'])}), "
            f"{dash._num(e['missed'])} missed ({rng(e['missed_low'], e['missed_high'])}).")
    assert row["tooltip"].startswith(want)
    assert dash.table_cells(row)[:8] == ["51-200", "150", "10", "10", "2 (few relevant observed)",
                                         f"30 ({rng(row['rel_low'], row['rel_high'])})", "30", "0"]
    assert _tip(c, "1-50")["few"] is False and "(few relevant observed)" not in _tip(c, "1-50")["tooltip"].split(".")[0]
    out = cli(p, "results", "main").output.splitlines()
    body = _after(out, "Relevant found along the ranking")
    assert body[1] == ("observed: ranks | records | sampled | labelled | relevant || estimated: relevant (band) | "
                       "kept (band) | missed (band) | relevant up to end | kept up to end | missed up to end")
    assert body[2:5] == [" | ".join(dash.table_cells(r)) for r in c["rows"]]
    j = api(p).get("/api/results", params={"protocol": "main"}).json()["curve"]
    assert j["rows"] == json.loads(json.dumps(c["rows"]))


def _store_partly(p):
    """The stored lines and series of an estimate on a partly labelled sample, as an estimate stored them before
    recall waited for every final label (label-completion-and-sealing 4.4): ``curve.store`` on the labelled rows."""
    at = "2026-01-01T00:00:00+00:00"
    p.store.set_meta("last_validation_lines:validation", json.dumps(
        {"result_set_key": "main", "at": at, "look": 1, "outcome": {}, "lines": []}))
    curve.store(p, "validation", "main", at)


def test_curve_band_without_labels(tmp_path):
    p = _bands(tmp_path, unlabelled=(0, 0, 10))
    _store_partly(p)
    c = dash.dashboard(p, "main")["curve"]
    assert c["state"] == "current" and [e["end"] for e in c["ends"]] == [50, 200] and [x[0] for x in c["points"]] == [50, 200]
    row = _tip(c, "201-400")
    assert row["estimated"] is False and row["note"] == "no labelled record: not estimated" and row["tooltip"] is None
    sentence = ("Ranks 201-400 hold no labelled record, so nothing is estimated from rank 201 on; label that band's "
                "sampled records and estimate recall again.")
    assert c["stop"]["sentence"] == sentence
    body = _after(cli(p, "results", "main").output.splitlines(), "Relevant found along the ranking")
    assert sentence in body and "201-400 | 200 | 10 | 0 | 0 (few relevant observed) | no labelled record: not estimated" in body


def test_curve_band_partly_labelled(tmp_path):
    p = _bands(tmp_path, unlabelled=(0, 5, 0))
    _store_partly(p)
    c = dash.dashboard(p, "main")["curve"]
    assert [e["end"] for e in c["ends"]] == [50]
    row = _tip(c, "51-200")
    assert (row["sampled"], row["labelled"]) == (10, 5) and row["note"] == "5 of 10 sampled unlabelled: not estimated"
    sentence = ("Ranks 51-200: 5 of 10 sampled records are not labelled yet, so nothing is estimated from rank 51 on; "
                "label them and estimate recall again.")
    assert c["stop"]["sentence"] == sentence
    assert (c["stop"]["link"], c["stop"]["href"]) == ("Label them on Samples and labelling", "#sample")
    assert c["stop"]["commands"] == ["wider label export validation <file.csv>", "wider label import validation <file.csv>"]
    body = _after(cli(p, "results", "main").output.splitlines(), "Relevant found along the ranking")
    i = body.index(sentence)
    assert body[i + 1:i + 3] == ["Label them: wider label export validation <file.csv>",
                                 "Label them: wider label import validation <file.csv>"]
    assert "51-200 | 150 | 10 | 5 | 2 (few relevant observed) | 5 of 10 sampled unlabelled: not estimated" in body
    # the first band not fully labelled: no line at all, only the table and the sentence
    p2 = _bands(tmp_path / "b", unlabelled=(3, 0, 0))
    _store_partly(p2)
    c2 = dash.dashboard(p2, "main")["curve"]
    assert c2["ends"] == [] and c2["points"] == [] and c2["stop"]["sentence"].startswith("Ranks 1-50: 3 of 10")
    assert c2["desc"] is None and len(c2["rows"]) == 3


def test_point_below_within_above_range(tmp_path):
    """Three cases (D14): a point above its range (the sample's kept share of a band far above the frame's), inside
    and below; the point is shown as computed, never moved into the range; the stored checksum is kept."""
    p = _bands(tmp_path)                               # all relevant kept; few kept records in the frame
    estimate(p, "validation", "main", B=20)
    c = dash.dashboard(p, "main")["curve"]
    e = c["ends"][0]
    assert e["kept"] > e["kept_high"] and _tip(c, "1-50")["outside"] and c["outside"]
    assert _tip(c, "1-50")["tooltip"].endswith(dash.CURVE_OUTSIDE) and dash.CURVE_OUTSIDE in dash.table_cells(_tip(c, "1-50"))
    assert c["ends"] == curve.stored(p, "validation")["ends"]          # drawn where computed
    rec = curve.stored(p, "validation")
    for case, (lo, hi) in (("inside", (0, 10 ** 6)), ("below", (10 ** 5, 10 ** 6))):
        ends = [{**x, **{f"{q}_low": lo for q in ("rel", "kept", "missed")},
                 **{f"{q}_high": hi for q in ("rel", "kept", "missed")}} for x in rec["ends"]]
        p.store.set_meta("last_recall_curve:validation", json.dumps({**rec, "ends": ends}))
        c = dash.dashboard(p, "main")["curve"]
        assert c["state"] == "current", case
        assert all(r["outside"] is (case == "below") for r in c["rows"]), case
        assert c["outside"] is (case == "below") and [x["rel"] for x in c["ends"]] == [x["rel"] for x in rec["ends"]]
        lines = cli(p, "results", "main").output.splitlines()
        assert (dash.CURVE_OUTSIDE in lines) is (case == "below")
    # low prevalence: one relevant in the sample, a zero-relevant band; the flags follow the stored numbers
    q = _bands(tmp_path / "low", relevant=(1, 0, 0))
    estimate(q, "validation", "main", B=20)
    c = dash.dashboard(q, "main")["curve"]
    assert [r["outside"] for r in c["rows"]] == [dash._outside(x) for x in c["ends"]]
    assert c["rows"][2]["relevant"] == 0 and c["rows"][2]["estimated"]


def test_curve_band_no_relevant_keeps_range(tmp_path):
    p = _bands(tmp_path)                               # ranks 201-400: 200 records, 10 labelled, none relevant
    estimate(p, "validation", "main", B=20)
    c = dash.dashboard(p, "main")["curve"]
    row = _tip(c, "201-400")
    assert (row["records"], row["labelled"], row["relevant"], row["rel"], row["estimated"]) == (200, 10, 0, 0, True)
    assert "0 relevant observed (few relevant observed)" in row["tooltip"] and row["rel_high"] > 0
    assert c["ends"][2]["rel"] == c["ends"][1]["rel"] and c["ends"][2]["rel_high"] > c["ends"][2]["rel"]
    assert dash.table_cells(row)[4] == "0 (few relevant observed)"


def test_band_records_in_another_sample(tmp_path):
    """A sample drawn after this one takes records of a band: the estimate and the range both count them as records
    of the band not labelled here (the design's N_h, recall-final-check-fixes), and every surface says so."""
    p = _bands(tmp_path)
    rk = {r["record_id"]: r["rank"] for r in p.store.query("SELECT record_id, rank FROM ranking_rows")}
    taken = [rid for rid, k in sorted(rk.items(), key=lambda x: x[1]) if 51 <= k <= 200
             and rid not in set(_sampled(p))][:20]
    _purposive(p, "development", taken)
    estimate(p, "validation", "main", B=20)
    c = dash.dashboard(p, "main")["curve"]
    row = _tip(c, "51-200")
    other = dash.CURVE_IN_OTHER.format(n="20")
    assert (row["records"], row["in_other"], row["old_population"]) == (150, 20, False)
    assert other in row["tooltip"] and dash.CURVE_IN_OTHER_OLD.format(n="20") not in row["tooltip"]
    assert dash.table_cells(row)[-1] == other
    assert _tip(c, "1-50")["in_other"] is None and other not in _tip(c, "1-50")["tooltip"]
    # a new series never blames the other sample for a point outside its range
    assert c["outside_text"] == dash.CURVE_OUTSIDE and not any("range leaves them out" in n for n in c["notes"])
    out = cli(p, "results", "main").output
    assert other in out
    # the series still names no record
    assert "record_id" not in p.store.get_meta("last_recall_curve:validation")


def test_estimate_again_matches_cli(tmp_path):
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=20)
    rid = _sampled(p)[0]
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include",
          correction_reason="synthetic correction")
    looks = p.store.scalar("SELECT SUM(n) FROM validation_looks")
    c = dash.dashboard(p, "main")["curve"]
    assert c["state"] == "stale" and c["again_label"] == "Estimate recall again"
    assert c["estimate_again"] == {"protocol": "main", "sample": "validation", "result_set": 1}
    client = api(p)
    for _ in range(2):                                 # opening the page never estimates
        assert client.get("/api/results", params={"protocol": "main"}).status_code == 200
    assert p.store.scalar("SELECT SUM(n) FROM validation_looks") == looks
    body = _after(cli(p, "results", "main").output.splitlines(), "Relevant found along the ranking")
    assert body[:2] == [c["sentence"], "Estimate recall again: wider recall main --sample validation"]
    g = client.get("/api/recall", params=c["estimate_again"])
    assert g.status_code == 200
    assert fresh(p).store.scalar("SELECT SUM(n) FROM validation_looks") == looks + 1
    c2 = dash.dashboard(fresh(p), "main")["curve"]
    assert c2["state"] == "current" and c2["estimate_again"] is None
    r = cli(p, "recall", "main", "--sample", "validation", "--json", "-B", "20")
    assert r.exit_code == 0
    assert fresh(p).store.scalar("SELECT SUM(n) FROM validation_looks") == looks + 2
    v = p.config.protocol.recall.primary_variant
    assert abs(json.loads(r.output)["variants"][v]["recall"] - g.json()["variants"][v]["recall"]) < 1e-9
    # another set than the active one is named in the request and the command
    _set2(p)
    estimate(p, "validation", "main", B=20, result_set=2, choice="not_informed")
    label(p, "validation", rid, "r1", lab, correction_reason="synthetic correction undone")
    c3 = dash.dashboard(p, "main", 2)["curve"]
    assert c3["estimate_again"] == {"protocol": "main", "sample": "validation", "result_set": 2}
    assert c3["again_command"] == "wider recall main --set 2 --sample validation"


def test_curve_stale(tmp_path):
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    d0 = dash.dashboard(p, "main")
    at = d0["recall"]["as_of"]
    ids = _sampled(p)
    rid = ids[0]
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include",
          correction_reason="synthetic correction")
    d = dash.dashboard(p, "main")
    stale = f"The labels or screening decisions changed since this estimate ({at}); estimate recall again to update it."
    assert d["recall"]["sentence"] == stale
    # the stored lines are kept; the correction of a sealed record adds the change line and the stale line (D6)
    n0 = len(d0["recall"]["lines"])
    assert d["recall"]["lines"][:n0] == d0["recall"]["lines"]
    assert d["recall"]["lines"][n0:] == sealed_lines(p, "validation", at) and len(sealed_lines(p, "validation", at)) == 2
    assert d["curve"]["state"] == "stale" and d["curve"]["points"] == []
    assert d["curve"]["sentence"] == ("The labels or screening decisions changed since the last recall estimate; "
                                      "estimate recall again to redraw the chart.")
    out = cli(p, "results", "main").output.splitlines()
    assert _after(out, "Recall")[:3] == [stale, d["recall"]["headline"], f"as of {at}"]
    assert _after(out, "Relevant found along the ranking")[:3] == [
        d["curve"]["sentence"], "Estimate recall again: wider recall main --sample validation", ""]
    label(p, "validation", rid, "r1", lab, correction_reason="synthetic correction undone")   # undone: both sentences
    d = dash.dashboard(p, "main")                                       # go, the chart returns
    assert d["recall"]["sentence"] is None and d["curve"]["state"] == "current" and d["curve"]["points"]
    assert d["recall"]["lines"] == d0["recall"]["lines"]
    # the shown set's decision on a sampled record
    old = p.store.one("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=?", (rid,))["decision"]
    set_decision(p, "main", rid, "maybe" if old != "maybe" else "exclude")
    assert dash.dashboard(p, "main")["curve"]["state"] == "stale"
    set_decision(p, "main", rid, old)
    # a decision on a record outside the sample changes neither the lines nor the chart
    outside = next(r for r in range(1, 601) if r not in set(ids))
    o = p.store.one("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=?", (outside,))["decision"]
    set_decision(p, "main", outside, "maybe" if o != "maybe" else "include")
    d = dash.dashboard(p, "main")
    assert d["recall"]["sentence"] is None and d["recall"]["lines"] == d0["recall"]["lines"]
    assert d["curve"] == d0["curve"]


def test_curve_missing(tmp_path):
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    # an estimate stored before series were stored: lines without a time, no series
    meta = json.loads(p.store.get_meta("last_validation_lines:validation"))
    meta.pop("at")
    p.store.set_meta("last_validation_lines:validation", json.dumps(meta))
    p.store.execute("DELETE FROM meta WHERE key='last_recall_curve:validation'")
    d = dash.dashboard(p, "main")
    assert d["recall"]["state"] == "current" and d["recall"]["lines"] == status(p, "validation")["lines"]
    assert d["recall"]["as_of"] is None and d["recall"]["sentence"] is None
    assert d["curve"]["sentence"] == "No chart is stored with this estimate; estimate recall again to draw it."
    body = _after(cli(p, "results", "main").output.splitlines(), "Recall")
    assert body[0] == d["recall"]["headline"]
    assert body[1] == status(p, "validation")["lines"][0]               # no "as of" without a time
    # a sample without score bands: the record has no points
    estimate(p, "validation", "main", B=50)
    rec = curve.stored(p, "validation")
    p.store.set_meta("last_recall_curve:validation", json.dumps({**rec, "bands": False, "points": []}))
    d = dash.dashboard(p, "main")
    assert d["curve"]["sentence"] == "This sample has no score bands, so no chart is drawn." and d["curve"]["points"] == []
    assert d["recall"]["lines"]


def test_curve_no_relevant(tmp_path):
    """No relevant record in the labelled sample: no point estimate, so no flat chart at zero; both sections say so."""
    p = _drawn(tmp_path)
    for rid in _sampled(p):
        if p.store.one("SELECT 1 FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,)):
            label(p, "validation", rid, "r1", "exclude")
    estimate(p, "validation", "main", B=50)
    assert curve.stored(p, "validation")["points"][-1][1] == 0
    d = dash.dashboard(p, "main")
    assert d["recall"]["state"] == "current" and d["recall"]["lines"]
    assert d["recall"]["sentence"] == "Recall could not be estimated: the labelled sample holds no relevant validation papers."
    assert d["curve"]["state"] == "no_relevant" and d["curve"]["points"] == []
    assert d["curve"]["sentence"] == "The labelled sample holds no relevant record, so no chart is drawn."
    out = cli(p, "results", "main").output.splitlines()
    assert _after(out, "Recall")[0] == d["recall"]["sentence"]
    assert _after(out, "Relevant found along the ranking")[0] == d["curve"]["sentence"]


COUNTED = ([("include", ["include", "include"], "A,B")] * 2 + [("exclude", ["exclude", "exclude"], "A,B")] * 4
           + [("include", ["include", "exclude", "include"], "A,C"), ("maybe", ["maybe", "exclude", "maybe"], "A,C"),
              ("exclude", ["include", "exclude", "exclude"], "B,C"),
              ("maybe", ["include", "maybe", "exclude", "researcher:maybe"], "researcher")])


def test_counts_match_exports(proj):  # noqa: F811
    fill(proj, "main", COUNTED, rules())
    d = dash.dashboard(proj, "main")
    # what `wider export prisma` writes, plus the page's provisional line (D32 F4)
    drop = lambda c: {k: v for k, v in c.items() if k != "provisional_line"}   # noqa: E731
    assert drop(d["counts"]) == drop(prisma_counts(proj, "main"))
    assert (d["counts"]["screened"], d["counts"]["included_ta"], d["counts"]["maybe_ta"]) == (10, 3, 2)
    sets = d["reference_sets"]["sets"]
    assert [(s["label"], s["records"]) for s in sets] == [
        ("Include", 3), ("Include + Maybe", 5), ("Maybe only", 2), ("Exclude", 5), ("both configurations agreed", 6),
        ("resolved by the third model", 3), ("unresolved disagreement", 0), ("decided by a researcher", 1)]
    assert sets[1]["unresolved"] == 0
    # each count is what `wider export selection --dry-run` gives for that preset or pathway
    for s in sets:
        args = ["--preset", s["set"]] if s["kind"] == "preset" else ["--preset", "every", "--pathway", s["set"]]
        r = cli(proj, "export", "selection", "--protocol", "main", *args, "--dry-run")
        assert r.exit_code == 0, r.output
        assert r.output.startswith(f"{s['records']:,} record"), (s, r.output)
    lines = _after(cli(proj, "results", "main").output.splitlines(), "Reference files")
    assert lines[:9] == ["Include: 3", "Include + Maybe: 5 (of which 0 unresolved)", "Maybe only: 2", "Exclude: 5",
                         "both configurations agreed: 6", "resolved by the third model: 3",
                         "unresolved disagreement: 0", "decided by a researcher: 1", d["reference_sets"]["pattern_line"]]
    assert "Export records: wider export selection --protocol main" in lines
    assert "Every set at once: wider export references --all --protocol main" in lines
    allr = {s["set"]: s["records"] for s in export_all_references(proj, "main")["sets"]}
    assert {s["set"]: s["records"] for s in sets} == {k: allr[k] for k in (s["set"] for s in sets)}


def test_documents_list(proj):  # noqa: F811
    fill(proj, "main", COUNTED, rules())
    ex = proj.path("exports")
    ex.mkdir(parents=True, exist_ok=True)
    (ex / "report.md").write_text("# Report\n", encoding="utf-8")
    (ex / "prisma_2020.svg").write_text("<svg/>", encoding="utf-8")
    d = dash.dashboard(proj, "main")
    docs = {x["path"]: x for x in d["documents"]}
    for path in ("exports/report.md", "exports/prisma_2020.svg"):
        assert docs[path]["exists"] and docs[path]["at"] and docs[path]["link"] and docs[path]["command"] is None
    cl = docs["exports/prisma_2020_checklist.md"]
    assert not cl["exists"] and cl["command"] == "wider export prisma-checklist" and not cl["link"]
    assert sum(1 for x in d["documents"] if x["exists"]) == 2
    lines = _after(cli(proj, "results", "main").output.splitlines(), "Documents")
    assert "PRISMA 2020 checklist: not written yet (wider export prisma-checklist)" in lines
    assert any(x.startswith("Report: exports/report.md, ") for x in lines)
    c = api(proj)
    for path in ("exports/report.md", "exports/prisma_2020.svg"):
        assert c.get("/api/outputs/file", params={"path": path}).status_code == 200
    # the PRISMA reporting files (prisma-reporting-assistant): not written yet, then listed with time and link; a
    # .docx missing because python-docx cannot be imported says so instead of naming the export again
    rep = [x for x in d["documents"] if x["path"].startswith("exports/prisma_reporting/")]
    assert [x["path"].rsplit("/", 1)[-1] for x in rep] == [
        "prisma_2020_checklist.md", "prisma_2020_checklist.docx", "prisma_s_checklist.docx",
        "methods_documentation.docx", "prisma_2020_flow.docx"]
    assert all(not x["exists"] and x["command"] == "wider report prisma export" for x in rep)
    assert "PRISMA reporting checklist: not written yet (wider report prisma export)" in lines
    assert "PRISMA 2020 checklist (Word): not written yet (wider report prisma export)" in lines
    (ex / "prisma_reporting").mkdir()
    for x in rep[:4]:
        (proj.root / x["path"]).write_bytes(b"x")
    rep2 = {x["path"]: x for x in dash.dashboard(proj, "main")["documents"]}
    for x in rep[:4]:
        y = rep2[x["path"]]
        assert y["exists"] and y["at"] and y["link"] and y["command"] is None
        assert c.get("/api/outputs/file", params={"path": x["path"]}).status_code == 200
    import importlib.util
    real = importlib.util.find_spec
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "docx" else real(name, *a))
        flow = {x["path"]: x for x in dash.dashboard(proj, "main")["documents"]}[rep[4]["path"]]
        assert not flow["exists"] and flow["command"] is None
        assert flow["missing"] == "not written: python-docx is not installed"
        assert ("PRISMA 2020 flow diagram (Word): not written: python-docx is not installed"
                in _after(cli(proj, "results", "main").output.splitlines(), "Documents"))
    for x in rep[:4]:
        (proj.root / x["path"]).unlink()
    # reference files: only this set's set files and index; another protocol whose name starts "main_" is left out
    refs = ex / "references"
    refs.mkdir()
    for name in ("main_include.ris", "main_include_and_maybe.csv", "main_index.csv", "main_v2_include.ris",
                 "main.s2_include.ris"):
        (refs / name).write_text("x", encoding="utf-8")
    got = {x["path"].rsplit("/", 1)[-1]: x["label"] for x in dash.dashboard(proj, "main")["documents"]
           if x["path"].startswith("exports/references/")}
    assert got == {"main_include.ris": "Reference file", "main_include_and_maybe.csv": "Reference file",
                   "main_index.csv": "Reference index"}
    # the Export records card's files: selections, a multi-format ZIP, the audit table and packages
    for name in ("main_include_and_maybe.ris", "main_include_by_third_read.zip", "main_audit.csv",
                 "main_export_package_20261025T101500.zip", "main_export_package_20261025T101500_2.zip",
                 "main_v2_audit.csv", "main_v2_export_package_20261025T101500.zip"):
        (refs / name).write_text("x", encoding="utf-8")
    docs = {x["path"].rsplit("/", 1)[-1]: x for x in dash.dashboard(proj, "main")["documents"]
            if x["path"].startswith("exports/references/")}
    assert {k: v["label"] for k, v in docs.items()} == {
        "main_include.ris": "Reference file", "main_include_and_maybe.csv": "Reference file",
        "main_index.csv": "Reference index", "main_include_and_maybe.ris": "Reference file",
        "main_include_by_third_read.zip": "Reference file", "main_audit.csv": "Audit table",
        "main_export_package_20261025T101500.zip": "Export package",
        "main_export_package_20261025T101500_2.zip": "Export package"}
    assert all(v["exists"] and v["at"] and v["link"] for v in docs.values())
    for name in docs:
        assert c.get("/api/outputs/file", params={"path": f"exports/references/{name}"}).status_code == 200
    # none written: the not-written line names `wider export selection`
    for f in refs.iterdir():
        f.unlink()
    lines = _after(cli(proj, "results", "main").output.splitlines(), "Documents")
    assert "Reference files of main (set 1): not written yet (wider export selection --protocol main)" in lines


def test_recall_imported_sample(tmp_path):
    """An imported sample has no score bands: an estimate on it stores no lines, so the dashboard says why instead of
    "No recall estimate yet", and the chart section says there are no bands."""
    from tests.unit.test_recall import _labelled
    p = _labelled(tmp_path)
    estimate(p, "validation", "main", B=20)
    d = dash.dashboard(p, "main")
    assert d["recall"]["state"] == "no_bands" and d["recall"]["sentence"] == (
        "validation has no score bands, so the dashboard keeps no recall lines for it; its recall is shown on the "
        "Recall and report page (wider recall main).")
    assert d["curve"]["state"] == "no_bands" and d["curve"]["sentence"] == "This sample has no score bands, so no chart is drawn."
    out = cli(p, "results", "main").output.splitlines()
    assert _after(out, "Recall")[0] == d["recall"]["sentence"]
    assert _after(out, "Relevant found along the ranking")[0] == d["curve"]["sentence"]


def _packages(p):
    """reviewer-roles-and-blinding: R3's package P imported (28 labels, 3 of them repeats), R4's 10 blind-queue labels,
    R5's 4 labels made in WIDER outside a blind queue, R6's 2 imported labels (label-completion-and-sealing D11) and
    R2's package Q on validation not imported, written as the store keeps them."""
    st = p.store
    sid = st.scalar("SELECT sample_id FROM samples WHERE name='validation'")
    ids = [r[0] for r in st.query("SELECT record_id FROM sample_rows WHERE sample_id=? ORDER BY queue_pos", (sid,))]
    samples = json.dumps([{"sample_id": sid, "name": "validation", "n": len(ids)}])
    for pid, who, imported in (("p-20261002-aaaaaa", "R3", "2026-10-09T09:00:00+00:00"), ("p-20261025-bbbbbb", "R2", None)):
        st.execute("INSERT INTO review_packages(package_id,reviewer,mode,kind,samples,codebook_version,frozen,n_items,file,"
                   "made_at,imported_at) VALUES(?,?,'second','validation',?,3,1,28,'x','2026-10-02T09:00:00+00:00',?)",
                   (pid, who, samples, imported))
    for i, rid in enumerate(ids[:28]):
        st.execute("INSERT INTO review_package_items(package_id,token,sample_id,record_id,repeat) VALUES(?,?,?,?,?)",
                   ("p-20261002-aaaaaa", f"t{i}", sid, rid, int(i < 3)))
    for who, made, recs in (("R3", "p-20261002-aaaaaa", ids[:28]), ("R4", "blind queue", ids[28:38]), ("R5", "unblinded queue", ids[38:42]),
                            ("R6", "file import", ids[42:44])):
        st.execute("INSERT OR IGNORE INTO reviewers(reviewer_id,name,created_at) VALUES(?,?,'t')", (who, who))
        st.executemany("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at,made_in) "
                       "VALUES(?,?,?,'include','t',?)", [(sid, r, who, made) for r in recs])
    return st.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='r1'")


def test_blinded_review_line(tmp_path):
    p = _drawn(tmp_path)
    assert "blinded_review" not in dash.dashboard(p, "main")
    assert not any(x.startswith("Validation labels:") for x in dash.dashboard_lines(dash.dashboard(p, "main")))
    n = _packages(p)
    line = (f"Validation labels: 25 made blind in a review package, 3 repeated in a review package, {10 + n} in the blind queue "
            f"in WIDER, 4 made in WIDER, 2 imported. R2's review package is out and not imported yet.")
    r = cli(p, "results", "main")
    assert r.exit_code == 0 and r.output.count(line) == 1 and r.output.splitlines()[0] == line
    d = json.loads(cli(p, "results", "main", "--json").output)
    assert d["blinded_review"] == {"line": line, "made_blind": 25, "repeated": 3, "blind_queue": 10 + n, "made_in_wider": 4,
                                   "imported": 2, "not_imported": ["R2"]}



def test_inconclusive_not_a_warning(tmp_path):
    """recall-certification-workload D2: an inconclusive outcome gives no warning lines; a not-met one does."""
    p = _drawn(tmp_path)
    estimate(p, "validation", "main", B=50)
    d = dash.dashboard(p, "main")
    assert json.loads(p.store.get_meta("last_validation_lines:validation"))["outcome"]["outcome"] == "cannot_tell"
    assert d["recall"]["warning_lines"] == [] and any(x.startswith("Inconclusive for the target") for x in d["recall"]["lines"])
    meta = json.loads(p.store.get_meta("last_validation_lines:validation"))
    meta["outcome"] = {"outcome": "not_met", "lines": ["Target 0.50 not met: at look 1, ..., lie below it."]}
    p.store.set_meta("last_validation_lines:validation", json.dumps(meta))
    assert dash.dashboard(p, "main")["recall"]["warning_lines"] == ["Target 0.50 not met: at look 1, ..., lie below it."]
