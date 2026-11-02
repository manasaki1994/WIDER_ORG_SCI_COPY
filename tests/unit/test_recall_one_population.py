"""One population for the recall estimate, its bounds and its verdict (recall-final-check-fixes D1-D5, D9): a band
counts its frame as it was at the draw or last extension, so records another sample takes afterwards stay in the
band as records not labelled here. Synthetic, topic-neutral records only."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from tests.unit.test_validation_plan import Band, _ranked
from wider.cli import app as cli_app
from wider.core import journal
from wider.recall import dashboard as dash
from wider.recall.estimate import estimate
from wider.recall.validation import POPULATION_STALE, exact_cells, status, validation_checks
from wider.sample import plan as pl
from wider.sample.design import (
    FRAME_EXCLUDED,
    draw_probability,
    extend_probability,
    frame_excluded,
    sampling_log,
)
from wider.sample.labels import label
from wider.screen.decisions import set_decision

runner = CliRunner()


def _rel(r):
    return r <= 12 or r % 5 == 0


def _project(tmp_path, name="p"):
    """60 ranked records, bands S1 (ranks 1-20, n 10) and S2 (ranks 21-60, n 10), target 0.75; every record screened,
    two relevant records of S2 excluded."""
    p = _ranked(tmp_path, n=60, bands=[Band(name="S1", start=1, end=20, n=10), Band(name="S2", start=21, end=None, n=10)],
                name=name)
    p.config.protocol = p.config.protocol.set("recall.target", 0.75)
    p.save()
    for r in range(1, 61):
        set_decision(p, "main", r, "include" if _rel(r) and r not in (25, 35) else "exclude")
    return p


def _purposive(p, ids, name="development", at="2099-01-01T00:00:00+00:00"):
    p.store.execute("INSERT INTO samples(name, kind, design, n, created_at) VALUES(?, 'purposive', '{}', ?, ?)",
                    (name, len(ids), at))
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name=?", (name,))
    p.store.executemany("INSERT INTO sample_rows(sample_id, record_id, queue_pos, pool) VALUES(?,?,?,'synthetic')",
                        [(sid, rid, i) for i, rid in enumerate(ids)])


def _val(p):
    return [r["record_id"] for r in p.store.query(
        "SELECT record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='validation' "
        "ORDER BY queue_pos")]


def _label_all(p):
    for rid in _val(p):
        label(p, "validation", rid, "r1", "include" if _rel(rid) else "exclude")


def _s2(p):
    return [c for c in exact_cells(p, "validation", "main") if c["band"] == "S2"]


def _after(tmp_path, name="p"):
    """validation drawn, then a development sample takes the 30 other records of S2, then every record labelled."""
    p = _project(tmp_path, name)
    draw_probability(p)
    others = [r for r in range(21, 61) if r not in set(_val(p))]
    _purposive(p, others)
    _label_all(p)
    return p, others


def test_development_drawn_after_validation(tmp_path):
    p, others = _after(tmp_path)
    assert len(others) == 30
    cells = _s2(p)
    assert sum(c["N"] for c in cells) == 40 and sum(c["t"] for c in cells) == 10
    assert all(c["t"] < c["N"] for c in cells)                       # neither cell is a census
    out = estimate(p, "validation", "main", B=50)
    v = out["variants"][p.config.protocol.recall.primary_variant]
    low, high = out["bounds"]["low"], out["bounds"]["high"]
    assert low < 0.75 < high and out["outcome"]["outcome"] != "met"
    # the HT estimate is the design's: weights N_h/n_h stored at the draw, unchanged by the fix
    w = {"S1": 20 / 10, "S2": 40 / 10}
    rows = {r["record_id"]: r["band"] for r in p.store.query(
        "SELECT record_id, band FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name='validation'")}
    rel = sum(w[b] for r, b in rows.items() if _rel(r))
    kept = sum(w[b] for r, b in rows.items() if _rel(r) and r not in (25, 35))
    assert abs(v["recall"] - kept / rel) < 1e-9
    line = ("30 records of the development sample were drawn after validation; its estimate and bounds count them as "
            "records of their bands that validation did not label.")
    assert line in out["lines"]
    assert not any("are not covered" in x for x in out["lines"])
    # with S2 counted as its 10 labelled records (the old cells), the lower bound would be higher
    narrow = _project(tmp_path, "q")
    draw_probability(narrow)
    _purposive(narrow, [r for r in range(21, 61) if r not in set(_val(narrow))], at="2000-01-01T00:00:00+00:00")
    narrow.store.execute("UPDATE samples SET design=json_set(design, '$.frame_excluded', json(?)) WHERE name='validation'",
                         (json.dumps([r for r in range(21, 61) if r not in set(_val(narrow))]),))
    _label_all(narrow)
    assert sum(c["N"] for c in _s2(narrow)) == 10
    old = estimate(narrow, "validation", "main", B=50)
    assert old["bounds"]["low"] > low
    # the suggestion never counts the 30 records as left to label, and says why the bounds may not close
    sc = pl.sample_cells(p, "validation", "main")
    s2 = next(b for b in sc["bands"] if b["name"] == "S2")
    assert s2["in_other"] == 30 and not set(others) & {i for c in s2["cells"].values() for i in c["left"]}
    s = pl.suggest(p, "validation", "main")
    assert pl.IN_OTHER_OPEN.format(n="30", sample="validation") in s["lines"]
    assert ("30 records of these bands are in another sample and cannot be labelled here, so the bounds may not close; "
            "a fresh sample (wider sample draw probability --name NEW --fresh-from validation) can settle it.") in s["lines"]
    assert not s.get("census")


def test_development_drawn_before_validation(tmp_path):
    p = _project(tmp_path)
    dev = list(range(21, 31))
    _purposive(p, dev, at="2000-01-01T00:00:00+00:00")
    draw_probability(p)
    d = json.loads(p.store.scalar("SELECT design FROM samples WHERE name='validation'"))
    assert sorted(d[FRAME_EXCLUDED]) == dev
    _label_all(p)
    cells = _s2(p)
    assert sum(c["N"] for c in cells) == 30
    assert not set(dev) & set(_val(p))
    out = estimate(p, "validation", "main", B=50)
    assert "10 records of the development sample are not covered." in out["lines"]
    assert not any("were drawn after validation" in x for x in out["lines"])


def test_extension_resets_the_population(tmp_path):
    p = _project(tmp_path)
    draw_probability(p)
    free = [r for r in range(21, 61) if r not in set(_val(p))]
    dev = free[:20]
    _purposive(p, dev)
    out = extend_probability(p, "validation", {"S2": 5}, author="ana")
    assert out["added"] == 5 and not set(dev) & set(_val(p))         # nothing drawn from the development sample
    assert frame_excluded(p, "validation") == set(dev)
    _label_all(p)
    assert sum(c["N"] for c in _s2(p)) == 20 == out["weights"]["S2"]["N_h"]
    # a development sample drawn after the extension counts in the cells as records not labelled here
    left = [r for r in free[20:] if r not in set(_val(p))]
    assert len(left) == 5
    _purposive(p, left[:3], name="dev2", at="2099-02-01T00:00:00+00:00")
    assert sum(c["N"] for c in _s2(p)) == 20
    sc = pl.sample_cells(p, "validation", "main")
    assert next(b for b in sc["bands"] if b["name"] == "S2")["in_other"] == 3


def test_older_sample_fallback(tmp_path):
    p = _project(tmp_path)
    _purposive(p, list(range(21, 31)), name="early", at="2000-01-01T00:00:00+00:00")
    draw_probability(p)
    later = [r for r in range(31, 61) if r not in set(_val(p))][:5]
    _purposive(p, later, name="late")
    # a sample stored before this change: no list of left-out records
    p.store.execute("UPDATE samples SET design=json_remove(design, '$.frame_excluded') WHERE name='validation'")
    before = p.store.scalar("SELECT design FROM samples WHERE name='validation'")
    assert frame_excluded(p, "validation") == set(range(21, 31))
    _label_all(p)
    cells = _s2(p)
    assert sum(c["N"] for c in cells) == 30                           # the earlier sample's 10 left out, the later 5 kept
    sc = pl.sample_cells(p, "validation", "main")
    assert next(b for b in sc["bands"] if b["name"] == "S2")["in_other"] == 5
    assert p.store.scalar("SELECT design FROM samples WHERE name='validation'") == before   # nothing written


def test_left_out_list_not_shown(tmp_path):
    p = _project(tmp_path)
    _purposive(p, list(range(21, 31)), at="2000-01-01T00:00:00+00:00")
    out = draw_probability(p)
    assert out["development_excluded"] == 10 and FRAME_EXCLUDED not in json.dumps(out)
    log = sampling_log(p)
    assert log["validation"]["development_excluded"] == 10 and FRAME_EXCLUDED not in json.dumps(log)
    res = runner.invoke(cli_app, ["sample", "status", "--json", "--dir", str(p.root)])
    assert res.exit_code == 0 and FRAME_EXCLUDED not in res.output
    # an extension after a later development sample changes the stored list; undoing it restores the earlier one
    later = [r for r in range(31, 61) if r not in set(_val(p))][:5]
    _purposive(p, later, name="late")
    ext = extend_probability(p, "validation", {"S2": 2}, author="ana")
    assert FRAME_EXCLUDED not in json.dumps(ext)
    assert frame_excluded(p, "validation") == set(range(21, 31)) | set(later)
    journal.undo(p, ext["change_id"], author="ana")
    assert frame_excluded(p, "validation") == set(range(21, 31))


def test_stored_result_before_the_fix(tmp_path):
    p, _ = _after(tmp_path)
    estimate(p, "validation", "main", B=50)
    looks = p.store.scalar("SELECT COUNT(*) FROM validation_look_states")
    stale = POPULATION_STALE.format(sample="validation")
    assert stale == ("The stored recall result of validation was worked out before records drawn into another sample "
                     "after it were counted in its bands; estimate recall again (the same labels add no look).")
    assert stale not in status(p, "validation")["lines"]
    # as a result stored before this change: no population marker in the lines or the chart series
    for key in ("last_validation_lines:validation", "last_recall_curve:validation"):
        raw = p.store.get_meta(key)
        if raw:
            d = json.loads(raw)
            d.pop("population", None)
            p.store.set_meta(key, json.dumps(d))
    assert stale in status(p, "validation")["lines"]
    rec = json.loads(p.store.get_meta("last_validation_lines:validation"))
    assert stale in validation_checks(p, "validation", rec)["lines"]
    c = dash.dashboard(p, "main")["curve"]
    assert stale in c["notes"]
    row = next(r for r in c["rows"] if r.get("in_other"))
    assert row["old_population"] and dash.CURVE_IN_OTHER_OLD.format(n="30") in row["tooltip"]
    res = runner.invoke(cli_app, ["validation", "status", "--dir", str(p.root)])
    assert res.exit_code == 0 and stale in " ".join(res.output.split())
    # estimating again with the same labels clears it and adds no look
    estimate(p, "validation", "main", B=50)
    assert stale not in status(p, "validation")["lines"] and stale not in dash.dashboard(p, "main")["curve"]["notes"]
    assert p.store.scalar("SELECT COUNT(*) FROM validation_look_states") == looks
