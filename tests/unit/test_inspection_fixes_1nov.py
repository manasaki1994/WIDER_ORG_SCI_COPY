"""Fixes from the owner's inspection of 1 Nov 2026 (owner-log f-20261101-103058-0a8b): a census is exact, an empty
development sample says so and blocks nothing, a recall target may be typed as a percentage, seed sources read as
words, and a fully labelled validation sample that is not final points to its fix. Synthetic data only."""
from __future__ import annotations

import pytest

from wider.recall import validation as va
from wider.recall.uncertainty import interval_text, stratified_uncertainty, uncertainty_lines
from wider.sample.design import EMPTY_DEVELOPMENT, draw_development, draw_probability
from wider.screen import progress
from wider.screen.devgate import open_samples
from wider.search.coverage import source_counts

from .test_adjacent import project  # noqa: F401 - pytest fixture

INC = ("include",)


def _rows(w):
    return ([{"band": b, "w": w, "human": "include", "screen": "include" if i else "exclude"} for b in ("S1", "S2")
             for i in range(4)] + [{"band": "S3", "w": w, "human": "exclude", "screen": "exclude"} for _ in range(5)])


def test_census_is_exact_with_no_interval():
    u = stratified_uncertainty(_rows(1.0), INC, INC)
    assert u["method"] == "census" and u["interval"] == (pytest.approx(6 / 8), pytest.approx(6 / 8))
    assert interval_text(u) == "exact (census)"
    text = " ".join(uncertainty_lines(u))
    assert "no sampling uncertainty" in text and "interval" in text
    sampled = stratified_uncertainty(_rows(3.66667), INC, INC)   # S3 sampled with no relevant paper: no interval
    assert sampled["method"] != "census" and sampled["interval"] is None
    assert "1 in 3.7 sampled" in sampled["reason"]


def test_target_as_percentage():
    assert va.as_fraction(95) == pytest.approx(0.95) and va.as_fraction(0.95) == 0.95 and va.as_fraction(None) is None
    assert va.as_fraction(1) == 1   # 1 stays ambiguous and is refused by set_target


def test_set_target_percentage(project):  # noqa: F811
    va.set_target(project, 95, author="Tester")
    assert project.config.protocol.recall.target == pytest.approx(0.95)
    with pytest.raises(va.TargetError, match="percentage above 1 and below 100"):
        va.set_target(project, 150, author="Tester")


def test_empty_development_sample_warns_and_blocks_nothing(project):  # noqa: F811
    draw_probability(project)
    d = draw_development(project, name="dev-empty", per_cell=0, per_near_miss=0, per_cluster=0, n_thin=0, n_tail=0,
                         per_adjacent=0, per_boundary=0)
    assert d["n"] == 0 and d["messages"][0] == EMPTY_DEVELOPMENT
    assert "optional" in EMPTY_DEVELOPMENT and "draw the development sample first" in EMPTY_DEVELOPMENT
    assert not [c for c in open_samples(project) if c["sample"] == "dev-empty"]


def test_source_words():
    assert source_counts({"researcher": 2}) == "2 added by the researcher"
    assert source_counts({"colleague": (0, 1), "seed_chain": (4, 5)}) == ("0 of 1 from a colleague, 4 of 5 found "
                                                                          "through the seeds' citations")


def test_not_final_text_points_to_the_fix():
    c = {"not_independent": 3, "open": 7, "few": 0, "packages_out": 0,
         "lines": ["7 records have labels that disagree"]}
    t = progress.not_final_text(c)
    assert t.startswith("Every record has a label, but recall cannot be estimated yet: 7 records")
    assert "Declare labels independent" in t and "Second reviewer and adjudication" in t


# ---------------------------------------------------------------- final check (1 Nov 2026): band list rules
def test_rank_bands_must_run_to_the_end_and_draw_at_least_one():
    from pydantic import ValidationError

    from wider.core.protocol import ReviewProtocol

    base = ReviewProtocol().model_dump()
    cut = dict(base)
    cut["sampling"] = dict(base["sampling"], bands=[{"name": "S1", "start": 1, "end": 100, "n": None},
                                                     {"name": "S2", "start": 101, "end": 3000, "n": 40}])
    with pytest.raises(ValidationError, match="last band must run to the end of the ranking"):
        ReviewProtocol.model_validate(cut)
    zero = dict(base)
    zero["sampling"] = dict(base["sampling"], bands=[{"name": "S1", "start": 1, "end": None, "n": 0}])
    with pytest.raises(ValidationError):
        ReviewProtocol.model_validate(zero)


def test_stale_window_cannot_overwrite_project_yaml(project):  # noqa: F811
    from wider.core.project import FileChangedSince, Project
    a, b = Project.find(project.root), Project.find(project.root)
    a.config.include_notes = "synthetic note A"
    a.save()
    b.config.exclude_notes = "synthetic note B"
    with pytest.raises(FileChangedSince, match="nothing was saved"):
        b.save()
    assert Project.find(project.root).config.include_notes == "synthetic note A"
    c = Project.find(project.root)   # a fresh read saves
    c.config.exclude_notes = "synthetic note C"
    c.save()


def test_stale_window_cannot_overwrite_benchmark(project):  # noqa: F811
    from wider.core.project import FileChangedSince, Project
    a, b = Project.find(project.root), Project.find(project.root)
    papers = a.read_benchmark()
    b.read_benchmark()
    a.write_benchmark(papers)
    (project.root / "benchmark.csv").write_text((project.root / "benchmark.csv").read_text() + "\n")
    with pytest.raises(FileChangedSince):
        b.write_benchmark(papers)


def test_missing_store_with_artifacts_is_refused_not_recreated(project):  # noqa: F811
    from typer.testing import CliRunner

    from wider.cli import app as cli_app
    from wider.core.project import Project, ProjectBroken
    p = Project.find(project.root)
    _ = p.store
    (project.root / "artifacts").mkdir(exist_ok=True)
    (project.root / "artifacts" / "synthetic.txt").write_text("x")
    p.store.close() if hasattr(p.store, "close") else None
    (project.root / "store.sqlite").unlink()
    with pytest.raises(ProjectBroken):
        _ = Project.find(project.root).store
    assert not (project.root / "store.sqlite").exists()
    res = CliRunner().invoke(cli_app, ["status", "--dir", str(project.root)])
    assert res.exit_code == 1 and "Traceback" not in res.output


@pytest.mark.parametrize("text, words", [("", "is empty"), ("hello\nnot a ris file\n", "no RIS record")])
def test_empty_or_non_ris_file_is_not_an_export(tmp_path, text, words):
    from wider.ingest.readers import NotAnExport, read_standard
    f = tmp_path / "synthetic.ris"
    f.write_text(text)
    with pytest.raises(NotAnExport, match=words):
        read_standard(f, "scopus")


def test_cli_label_add_is_the_twin_of_post_labels(tmp_path):
    from typer.testing import CliRunner

    from tests.unit.test_recall import _corpus, _sample_of
    from wider.cli import app as cli_app
    p = _corpus(tmp_path, n=12)
    ids = _sample_of(p, tmp_path)
    d = str(p.root)
    res = CliRunner().invoke(cli_app, ["label", "add", "validation", str(ids[0]), "include", "--reviewer", "rev1",
                                       "--dir", d])
    assert res.exit_code == 0, res.output
    row = p.store.one("SELECT l.decision, l.made_in FROM labels l JOIN samples s USING(sample_id) "
                      "WHERE s.name='validation' AND l.record_id=? AND l.reviewer_id='rev1'", (ids[0],))
    assert row["decision"] == "include" and row["made_in"] == "unblinded queue"
    res = CliRunner().invoke(cli_app, ["label", "add", "validation", str(ids[0]), "unsure", "--reviewer", "rev1",
                                       "--dir", d])
    assert res.exit_code == 1 and "Traceback" not in res.output
