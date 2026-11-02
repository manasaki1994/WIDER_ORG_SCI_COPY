"""The relevant-found series stored with a recall estimate (results-dashboard-and-ris, design D5). Synthetic,
topic-neutral records only (the 600-record ranking of test_validation_suggest)."""
import json

from tests.unit.test_validation_plan import Band, _ranked
from tests.unit.test_validation_suggest import _drawn
from wider.recall import curve
from wider.recall.estimate import estimate
from wider.sample.design import draw_probability
from wider.sample.labels import label
from wider.screen.decisions import set_decision


def _sampled(p):
    return [r["record_id"] for r in p.store.query("SELECT record_id FROM sample_rows r JOIN samples s ON "
                                                 "s.sample_id=r.sample_id WHERE s.name='validation' ORDER BY queue_pos")]


def _purposive(p, name, ids):
    """A purposive sample of ``ids``, labelled include by r1."""
    p.store.execute("INSERT INTO samples(name, kind, design, n, created_at) VALUES(?, 'purposive', '{}', ?, "
                    "'2026-01-01T00:00:00+00:00')", (name, len(ids)))
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name=?", (name,))
    for i, rid in enumerate(ids):
        p.store.execute("INSERT INTO sample_rows(sample_id, record_id, queue_pos, pool) VALUES(?,?,?,'synthetic')",
                        (sid, rid, i))
        label(p, name, rid, "r1", "include")


def _bands(tmp_path, relevant=(5, 2, 0), missed=(0, 0, 0), unlabelled=(0, 0, 0)):
    """The spec's sample (design D9): 400 ranked records in bands of 50, 150 and 200 (ranks 1-50, 51-200, 201-400),
    10 sampled from each. In band i the first ``relevant[i]`` sampled records are relevant, the first ``missed[i]`` of
    them excluded by screening, and the last ``unlabelled[i]`` sampled records left unlabelled; every record screened."""
    p = _ranked(tmp_path, n=400, bands=[Band(name="S1", start=1, end=50, n=10), Band(name="S2", start=51, end=200, n=10),
                                        Band(name="S3", start=201, end=None, n=10)])
    draw_probability(p)
    by: dict = {}
    for r in p.store.query("SELECT record_id, band FROM sample_rows ORDER BY queue_pos"):
        by.setdefault(r["band"], []).append(r["record_id"])
    rel, miss, labelled = set(), set(), []
    for i, b in enumerate(("S1", "S2", "S3")):
        ids = by[b]
        rel |= set(ids[:relevant[i]])
        miss |= set(ids[:missed[i]])
        labelled += ids[:len(ids) - unlabelled[i]]
    for r in range(1, 401):
        set_decision(p, "main", r, "include" if r in rel and r not in miss else "exclude")
    for rid in labelled:
        label(p, "validation", rid, "r1", "include" if rid in rel else "exclude")
    return p


def test_series_stored_and_matches_estimate(tmp_path):
    p = _drawn(tmp_path)
    est = estimate(p, "validation", "main", B=50)
    rec = curve.stored(p, "validation")
    lines = json.loads(p.store.get_meta("last_validation_lines:validation"))
    # stored with the lines' set key and time, the primary variant, and one point per band end (open band at n_ranked)
    assert rec["result_set_key"] == lines["result_set_key"] == "main" and rec["at"] == lines["at"]
    assert rec["variant"] == p.config.protocol.recall.primary_variant and rec["bands"] is True
    assert [pt[0] for pt in rec["points"]] == [100, 300, 600]
    rel = [pt[1] for pt in rec["points"]]
    kept = [pt[2] for pt in rec["points"]]
    assert rel == sorted(rel) and kept == sorted(kept) and all(k <= r for k, r in zip(kept, rel, strict=True))
    # the last point's kept/relevant is the primary point estimate
    v = est["variants"][rec["variant"]]
    assert abs(kept[-1] / rel[-1] - v["recall"]) < 1e-6
    assert abs(rel[-1] - v["weighted_relevant"]) < 1e-5 and abs(kept[-1] - v["weighted_kept"]) < 1e-5
    assert rec["checksum"] == curve.checksum(p, "validation", "main", rec["primary"])
    # no record id, title or single sampled rank in the stored record
    raw = p.store.get_meta("last_recall_curve:validation")
    assert set(rec) == {"result_set_key", "at", "variant", "primary", "bands", "checksum", "points", "level",
                        "has_bounds", "band_details", "ends", "population"}
    assert "Synthetic" not in raw and "record_id" not in raw


def test_checksum_follows_labels_and_decisions(tmp_path):
    p = _drawn(tmp_path)
    before = curve.checksum(p, "validation", "main")
    ids = set(_sampled(p))
    rid = next(iter(sorted(ids)))
    old = p.store.one("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=?", (rid,))["decision"]
    # a decision on a record outside the sample leaves it equal
    outside = next(r for r in range(1, 601) if r not in ids)
    o_old = p.store.one("SELECT decision FROM screen_decisions WHERE protocol='main' AND record_id=?",
                        (outside,))["decision"]
    set_decision(p, "main", outside, "maybe" if o_old != "maybe" else "include")
    assert curve.checksum(p, "validation", "main") == before
    # a decision on a sampled record changes it, and putting it back restores it
    set_decision(p, "main", rid, "maybe" if old != "maybe" else "exclude")
    assert curve.checksum(p, "validation", "main") != before
    set_decision(p, "main", rid, old)
    assert curve.checksum(p, "validation", "main") == before
    # so does a sampled record's final label
    lab = p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='r1'", (rid,))["decision"]
    label(p, "validation", rid, "r1", "exclude" if lab == "include" else "include")
    assert curve.checksum(p, "validation", "main") != before
    label(p, "validation", rid, "r1", lab)
    assert curve.checksum(p, "validation", "main") == before


def test_no_series_for_purposive(tmp_path):
    p = _drawn(tmp_path)
    _purposive(p, "development", range(1, 11))
    estimate(p, "development", "main", B=50)
    assert curve.stored(p, "development") is None
    assert p.store.get_meta("last_validation_lines:development") is None
    # a sample without score bands gives a record with no points (series() direct)
    p.store.execute("UPDATE samples SET design=? WHERE name='validation'", (json.dumps({"kind": "imported"}),))
    s = curve.series(p, "validation", "main")
    assert s["bands"] is False and s["points"] == []


def test_band_details_and_bounds_stored(tmp_path):
    """Design D9: each band's ranks, records, sampled, labelled and relevant observed, its weighted relevant and kept,
    and each band end's cumulative relevant, kept and missed with the summed exact cell bounds."""
    p = _bands(tmp_path, relevant=(5, 2, 0), missed=(1, 0, 0))
    est = estimate(p, "validation", "main", B=50)
    rec = curve.stored(p, "validation")
    b = est["bounds"]
    assert rec["has_bounds"] is True and rec["level"] == b["level"]
    bands = rec["band_details"]
    assert [(d["first"], d["last"]) for d in bands] == [(1, 50), (51, 200), (201, 400)]
    assert [(d["records"], d["sampled"], d["labelled"]) for d in bands] == [(50, 10, 10), (150, 10, 10), (200, 10, 10)]
    assert [d["relevant"] for d in bands] == [5, 2, 0]
    assert [(d["rel"], d["kept"]) for d in bands] == [(25, 20), (30, 30), (0, 0)]   # N/t times the relevant observed
    # the weighted counts add up to the points, and each end's bounds are the sums of the cells up to it
    rel = kep = 0.0
    seen = []
    for d, end, pt in zip(bands, rec["ends"], rec["points"], strict=True):
        rel, kep = rel + d["rel"], kep + d["kept"]
        assert end["end"] == pt[0] == d["last"] and abs(end["rel"] - pt[1]) < 1e-6 and abs(end["kept"] - pt[2]) < 1e-6
        assert abs(end["rel"] - rel) < 1e-6 and abs(end["missed"] - (rel - kep)) < 1e-6
        mine = [c for c in b["cells"] if c["band"] == d["band"]]
        seen += mine
        assert (d["rel_low"], d["rel_high"]) == (sum(c["low"] for c in mine), sum(c["high"] for c in mine))
        for part, sel in (("rel", seen), ("kept", [c for c in seen if c["decision"] == "kept"]),
                          ("missed", [c for c in seen if c["decision"] != "kept"])):
            assert (end[f"{part}_low"], end[f"{part}_high"]) == (sum(c["low"] for c in sel), sum(c["high"] for c in sel))
    assert rec["ends"][-1]["rel_high"] > rec["ends"][-1]["rel"]          # the zero-relevant band still adds to the range
    assert "record_id" not in json.dumps(rec)
    # a series read with one labeller's labels stores no bounds (they were computed from the final labels)
    other = curve.store(p, "validation", "main", rec["at"], primary="r1", bounds=b)
    assert other["has_bounds"] is False and [d["relevant"] for d in other["band_details"]] == [5, 2, 0]
    assert "rel_low" not in other["ends"][0] and "rel_low" not in other["band_details"][0]
