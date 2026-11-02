"""Seed papers (benchmark papers) reported apart from the estimate, the used sample's line and the scope line
(validation-sample-planner D13). Synthetic projects only."""
from __future__ import annotations

from tests.unit.test_recall import _corpus
from tests.unit.test_validation_plan import _ranked
from wider.core.models import BenchmarkPaper
from wider.recall import validation as va
from wider.recall.estimate import estimate
from wider.sample import labels as lb
from wider.sample.design import draw_probability
from wider.screen.decisions import set_decision


def _bench(tmp_path, after_freeze):
    p = _corpus(tmp_path)
    p.config.protocol.identification.validation_after_freeze = after_freeze
    p.save()
    recs = {r["doi"]: r for r in p.store.query("SELECT record_id, title, doi FROM records")}
    papers = [BenchmarkPaper(benchmark_id=f"D{i}", title=recs[f"10.1000/r{i}"]["title"], doi=f"10.1000/r{i}")
              for i in range(12)]
    papers += [BenchmarkPaper(benchmark_id=f"V{i}", title=recs[f"10.1000/r{i}"]["title"], doi=f"10.1000/r{i}",
                              set="Validation") for i in range(20, 28)]
    p.write_benchmark(papers)
    for i in range(12):
        set_decision(p, "main", recs[f"10.1000/r{i}"]["record_id"], "include" if i < 10 else ("maybe" if i == 10 else "exclude"))
    for i in range(20, 28):
        set_decision(p, "main", recs[f"10.1000/r{i}"]["record_id"], "include" if i < 26 else "exclude")
    return p


def test_benchmark_apart_lines(tmp_path):
    p = _bench(tmp_path, after_freeze=False)
    out = va.benchmark_apart(p, "main")
    dev, val = out["lines"]
    assert dev.startswith("Development seed papers: 11 of 12 kept (10 include, 1 maybe, 1 excluded), 95% interval ")
    assert dev.endswith(". Known papers, not a random sample; not pooled into the estimate.")
    assert val.startswith("Validation seed papers: 6 of 8 kept (6 include, 0 maybe, 2 excluded), 95% interval ")
    d = out["sets"][0]
    assert (d["set"], d["n"], d["kept"], d["include"], d["maybe"], d["exclude"], d["unscreened"]) == (
        "Development", 12, 11, 10, 1, 1, 0)
    assert 0 < d["interval"][0] < 11 / 12 < d["interval"][1] <= 1


def test_benchmark_apart_hidden_set(tmp_path):
    p = _bench(tmp_path, after_freeze=True)
    meta = [tuple(r) for r in p.store.query("SELECT key, value FROM meta ORDER BY key")]
    out = va.benchmark_apart(p, "main")
    assert out["lines"][1] == "Validation seed papers: hidden until the query is frozen"
    assert out["sets"][1] == {"set": "Validation", "hidden": True}
    assert "6 of 8" not in " ".join(out["lines"])
    assert [tuple(r) for r in p.store.query("SELECT key, value FROM meta ORDER BY key")] == meta   # no look


def _drawn(tmp_path):
    p = _ranked(tmp_path)
    ids = [r["record_id"] for r in p.store.query("SELECT record_id FROM ranking_rows ORDER BY rank")]
    thin = ids[100:140]
    p.store.executemany("UPDATE records SET thin=1 WHERE record_id=?", [(i,) for i in thin])
    bench = ids[200:220]
    p.store.executemany("UPDATE records SET doi=?, title=? WHERE record_id=?",
                        [(f"10.9/b{i}", f"Benchmark paper {i} on a distinct topic", i) for i in bench])
    p.write_benchmark([BenchmarkPaper(benchmark_id=f"B{i}", title=f"Benchmark paper {i} on a distinct topic",
                                      doi=f"10.9/b{i}") for i in bench])
    p.store.execute("UPDATE ranking_rows SET cell_scores='{\"c\": 0.5}'")   # the report's ranking section reads them
    draw_probability(p)
    return p


def _label(p, sample, n_include=10):
    rows = [r["record_id"] for r in p.store.query(
        "SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id WHERE s.name=? "
        "ORDER BY queue_pos", (sample,))]
    for k, rid in enumerate(rows[:20]):
        lb.label(p, sample, rid, "R1", "include" if k < n_include else "exclude", None if k < n_include else "a")
        set_decision(p, "main", rid, "include" if k < 6 else ("maybe" if k == 6 else "exclude"))
    return rows


SCOPE = ("This estimate covers 1940 ranked records (not the 40 records with too short an abstract, or the 20 "
         "seed papers), screened on title and abstract only; it says nothing about full-text screening.")


def test_scope_line_everywhere(tmp_path):
    p = _drawn(tmp_path)
    assert va.scope_line(p, "validation") == SCOPE
    rows = _label(p, "validation")
    for rid in rows[20:]:   # recall is estimated only on a complete sample (label-completion-and-sealing D4)
        lb.label(p, "validation", rid, "R1", "exclude", "a")
    e = estimate(p, "validation", "main", B=20)
    assert e["lines"][-1] == SCOPE and e["lines"].count(SCOPE) == 1 and e["scope_line"] == SCOPE
    from wider.recall.outputs import methods_draft
    from wider.recall.report import build_report, to_markdown
    assert to_markdown(build_report(p, B=20)).count(SCOPE) == 1 and SCOPE in methods_draft(p)


def test_used_sample_reported_apart(tmp_path):
    p = _drawn(tmp_path)
    _label(p, "validation")
    draw_probability(p, name="validation2", fresh_from="validation")
    n = p.store.scalar("SELECT n FROM samples WHERE name='validation'")
    assert va.used_sample_lines(p, "validation2", "main") == [
        f"The {n} records of validation (used) are not in this estimate: 10 relevant, 7 kept by this set. "
        "Not independent; not pooled."]
    assert "(used)" in va.scope_line(p, "validation2")
