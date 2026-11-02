"""The recall interval for unequal band weights (change recall-interval-honest, design D2-D4, D10). Every sample here is
simulated in the test: rows {band, w, human, screen} as ``estimate()`` builds them. The reference numbers of the first
test come from R ``survey::svyciprop(method = "beta")`` on the same rows."""
import sys
from pathlib import Path

import pytest

from wider.recall.uncertainty import (
    FEW_DF,
    METHOD_SENTENCE,
    NO_RELEVANT,
    beta_interval,
    empty_bands_reason,
    empty_sampled_bands,
    interval_text,
    interval_words,
    method_text,
    recall_uncertainty,
    stratified_uncertainty,
    uncertainty_lines,
)

INC = ("include",)


def row(band, w, human, screen):
    return {"band": band, "w": w, "human": human, "screen": screen}


def kept_rows(spec, missed=()):
    """Relevant papers per (band, weight, count), all kept unless listed in ``missed`` as (band, count), plus ten
    irrelevant records per band so every sampled band has more than one labelled record."""
    rows = []
    for band, w, n in spec:
        miss = dict(missed).get(band, 0)
        rows += [row(band, w, "include", "exclude" if i < miss else "include") for i in range(n)]
        rows += [row(band, w, "exclude", "exclude") for _ in range(10)]
    return rows


def test_matches_survey_beta_interval():
    # the two-band sample of "Hand-computed estimate on a two-band sample", strict variant
    two = [row("S1", 1, "include", "include"), row("S1", 1, "include", "maybe"), row("S1", 1, "maybe", "include"),
           row("S1", 1, "exclude", "exclude"), row("S2", 10, "include", "include"), row("S2", 10, "include", "exclude"),
           row("S2", 10, "exclude", "exclude"), row("S2", 10, "exclude", "exclude")]
    u = stratified_uncertainty(two, INC, INC)
    assert u["recall"] == pytest.approx(0.5)
    assert u["interval"] == pytest.approx((0.0007475140875, 0.9992524859125), abs=5e-6)
    assert u["method"] == "korn_graubard" and u["df"] == 2 and u["n_effective"] == pytest.approx(1.103, abs=1e-3)
    # three bands: a census, weight 10 and weight 100
    rel = [1, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0]
    kep = [1, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    bands = ["S1"] * 6 + ["S2"] * 8 + ["S3"] * 6
    w = {"S1": 1, "S2": 10, "S3": 100}
    three = [row(b, w[b], "include" if a else "exclude", "include" if k else "exclude") for b, a, k in zip(bands, rel, kep)]
    u = stratified_uncertainty(three, INC, INC)
    assert u["recall"] == pytest.approx(0.5256410256)
    assert u["interval"] == pytest.approx((0.015955554846739516, 0.9898269351134104), abs=5e-6)
    assert u["df"] == 6 and u["n_effective"] == pytest.approx(2.013, abs=1e-3)


def test_perfect_recall_equal_and_unequal_weights():
    equal = stratified_uncertainty(kept_rows([("S1", 5, 10)]), INC, INC)
    unequal = stratified_uncertainty(kept_rows([("S1", 1, 4), ("S2", 10, 3), ("S3", 50, 3)]), INC, INC)
    assert equal["recall"] == 1 and unequal["recall"] == 1
    assert interval_text(equal) == "0.69–1.00" and equal["interval"][0] == pytest.approx(0.69150, abs=1e-5)
    assert interval_text(unequal) == "0.43–1.00" and unequal["interval"][0] == pytest.approx(0.42728, abs=1e-5)
    assert unequal["method"] == "kish" and unequal["n_effective"] == pytest.approx(4.338, abs=1e-3)
    for u in (equal, unequal):
        assert u["interval"][1] == 1.0 and u["interval"][0] < 1.0   # never the single point 1.00 to 1.00


def test_zero_recall():
    u = stratified_uncertainty(kept_rows([("S1", 8, 10)], missed=[("S1", 10)]), INC, INC)
    assert u["recall"] == 0 and interval_text(u) == "0.00–0.31"
    assert u["interval"][0] == 0.0 and u["interval"][1] == pytest.approx(0.30850, abs=1e-5)
    assert uncertainty_lines(u)[-1] == "The screening kept none of the relevant validation papers."


def test_level_090():
    rows = kept_rows([("S1", 5, 10)])
    u = stratified_uncertainty(rows, INC, INC, level=0.9)
    assert u["interval"][0] == pytest.approx(0.74113, abs=1e-5)
    assert u["interval"][0] > stratified_uncertainty(rows, INC, INC)["interval"][0]
    assert uncertainty_lines(u)[1] == "90% interval: 0.74 to 1.00."


def test_one_record_band_has_no_interval():
    rows = ([row("S1", 1, "include", "include") for _ in range(3)]
            + [row("S2", 10, "include", "include"), row("S2", 10, "exclude", "exclude")]
            + [row("S3", 50, "include", "exclude")])
    u = stratified_uncertainty(rows, INC, INC)
    assert 0 < u["recall"] < 1 and u["interval"] is None and u["method"] is None
    assert interval_text(u) == "could not be reliably estimated"
    assert uncertainty_lines(u)[1] == ("Uncertainty could not be reliably estimated: band S3 holds a relevant paper "
                                       "but only one labelled record.")
    # one relevant paper per band leaves no degrees of freedom
    flat = [row("S2", 10, "include", "include"), row("S2", 10, "exclude", "exclude"),
            row("S3", 50, "include", "exclude"), row("S3", 50, "exclude", "exclude")]
    assert stratified_uncertainty(flat, INC, INC)["reason"] == FEW_DF


def test_no_relevant_paper():
    u = stratified_uncertainty([row("S1", 5, "exclude", "include"), row("S1", 5, "maybe", "exclude")], INC, INC)
    assert u["recall"] is None and u["interval"] is None and u["reason"] == NO_RELEVANT and u["n_relevant"] == 0
    assert interval_text(u) == "–"
    assert uncertainty_lines(u) == ["Recall could not be estimated: the labelled sample holds no relevant validation papers."]


def test_seven_of_seven_lines():
    u = stratified_uncertainty(kept_rows([("S1", 1, 4), ("S2", 10, 2), ("S3", 50, 1)]), INC, INC)
    assert uncertainty_lines(u) == [
        "Based on 7 relevant validation papers.",
        "95% interval: 0.16 to 1.00.",
        "That is few: one paper more or fewer missed would change the estimate a lot. Labelling more of the sample "
        "gives a firmer estimate.",
        "No relevant validation paper was missed. This does not mean the screening keeps every relevant paper: a "
        "recall as low as 0.16 fits what was seen."]
    assert method_text(u) == "Kish effective size, effective n 2.03"


def test_one_relevant_paper_singular_and_wide():
    u = stratified_uncertainty(kept_rows([("S1", 5, 1)]), INC, INC)
    lines = uncertainty_lines(u)
    assert lines[0] == "Based on 1 relevant validation paper."
    assert u["interval"][0] == pytest.approx(0.025, abs=1e-9) and lines[1] == "95% interval: 0.03 to 1.00."
    assert lines[2].startswith("That is few")
    # the few note follows the threshold the caller gives; 20 or more papers get none
    assert not any(x.startswith("That is few") for x in uncertainty_lines(u, few_below=1))
    many = stratified_uncertainty(kept_rows([("S1", 5, 20)]), INC, INC)
    assert not any(x.startswith("That is few") for x in uncertainty_lines(many))


def test_one_miss_never_raises_the_lower_bound():
    """For fixed weights the lower bound with a missed paper is never above the bound with none missed (the Kish cap,
    design D2): with the raw count as cap, one missed weight-1 paper gave 0.68 against 0.43 with none missed."""
    spec = [("S1", 1, 4), ("S2", 10, 3), ("S3", 50, 3)]
    none = stratified_uncertainty(kept_rows(spec), INC, INC)["interval"][0]
    for band in ("S1", "S2", "S3"):
        for k in (1, 2):
            u = stratified_uncertainty(kept_rows(spec, missed=[(band, k)]), INC, INC)
            assert u["interval"] is not None and u["interval"][0] <= none + 1e-12, (band, k, u["interval"])
    assert stratified_uncertainty(kept_rows(spec, missed=[("S1", 1)]), INC, INC)["interval"][0] == pytest.approx(
        0.421, abs=1e-3)


def test_lines_for_another_noun_and_subject():
    """The search side's known papers (papers-beyond-the-query) reuse the lines with their own words."""
    u = recall_uncertainty(5, 5)
    lines = uncertainty_lines(u, noun="known paper", subject="the query", keeps="finds", kept="found", remedy=None)
    assert lines[0] == "Based on 5 known papers."
    assert lines[2] == "That is few: one paper more or fewer missed would change the estimate a lot."
    assert lines[3] == ("No known paper was missed. This does not mean the query finds every relevant paper: a recall "
                        "as low as 0.48 fits what was seen.")
    assert uncertainty_lines(recall_uncertainty(0, 3), noun="known paper", subject="the query", kept="found")[-1] == (
        "The query found none of the known papers.")
    assert interval_words(u) == "95% interval 0.48 to 1.00"
    assert interval_words({**u, "interval": None}) == "uncertainty could not be reliably estimated"
    assert interval_words(recall_uncertainty(0, 0)) == "–"


def test_census_only_sample_is_exact():
    """Every record of every band labelled: the recall of these records is exact, as the exact bounds of the verdict
    say (inspection 1 Nov 2026: a 0.59-1.00 interval stood beside exact bounds 1.00 to 1.00); an unweighted count
    keeps its Clopper-Pearson interval."""
    rows = kept_rows([("S1", 1, 7)], missed=[("S1", 4)])
    u = stratified_uncertainty(rows, INC, INC)
    assert u["recall"] == pytest.approx(3 / 7) and u["method"] == "census"
    assert u["interval"] == pytest.approx((3 / 7, 3 / 7)) and interval_text(u) == "exact (census)"
    assert recall_uncertainty(3, 7)["interval"] == pytest.approx(beta_interval(3, 7))
    assert recall_uncertainty(3, 7)["method"] == "clopper_pearson"


def test_clopper_pearson_unchanged():
    """The search side's exact interval keeps its numbers (D7): the values before this change."""
    from wider.search.diagnostics import clopper_pearson, recall_text

    assert clopper_pearson(10, 10) == pytest.approx((0.6915028922, 1.0), abs=1e-9)
    assert clopper_pearson(0, 5) == pytest.approx((0.0, 0.5218237501), abs=1e-9)
    assert clopper_pearson(3, 7) == pytest.approx((0.0989882784, 0.8159484324), abs=1e-9)
    assert clopper_pearson(0, 0) is None
    assert recall_text(10, 10) == "10 of 10, 95% interval 0.69 to 1.00"
    with pytest.raises(ValueError):
        clopper_pearson(4, 3)


def _sim():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import recall_interval_sim  # scripts/recall_interval_sim.py, the study recorded in design D10
    return recall_interval_sim


@pytest.mark.parametrize("scenario", ["default", "rare_thin", "near_perfect", "perfect", "zero", "near_zero", "unequal_extreme"])
def test_coverage_on_simulated_samples(scenario):
    """Design D10: 300 simulated stratified samples per scenario (rare relevant papers, weights up to 500, observed
    recall 0 or 1 in most replicates): wherever the shipped interval is shown it holds the true recall in at least 95%
    of the samples and is never a point."""
    out = _sim().run(scenario, reps=300, seed=7)["band"]
    assert out["shown"] > 0 and out["coverage"] >= 0.95 and out["points"] == 0


def test_hidden_misses_get_no_interval_instead_of_a_wrong_one():
    """Design D10: when a sampled band shows no relevant paper while hiding missed ones, an interval would hold the true
    recall in under a quarter of the samples; the shipped rule gives none there and keeps 95% coverage where it does."""
    out = _sim().run("hidden_misses", reps=200, seed=7)
    assert out["none"]["coverage"] < 0.3 and out["none"]["shown"] == 1.0
    assert out["band"]["shown"] < 0.4 and out["band"]["coverage"] >= 0.95


def test_sampled_band_without_relevant_paper_gives_no_interval():
    """Design D10: a sampled band (weight not 1) whose labelled records hold no relevant paper hides what cannot be
    estimated, so there is no interval, with the band's size and rate in the reason; a census band without a relevant
    paper changes nothing; two such bands are both named."""
    rows = kept_rows([("S1", 1, 3)]) + [row("S2", 10, "exclude", "exclude") for _ in range(4)]
    assert empty_sampled_bands(rows, INC) == [("S2", 4, 10)]
    u = stratified_uncertainty(rows, INC, INC)
    assert u["recall"] == 1.0 and u["interval"] is None
    assert u["reason"] == "band S2 shows no relevant paper in its 4 labelled records (1 in 10 sampled), so what it hides cannot be estimated"
    lines = uncertainty_lines(u, noun="relevant validation paper", subject="the screening", keeps="keeps", kept="kept",
                              remedy="Labelling more of the sample")
    assert ("Uncertainty could not be reliably estimated: band S2 shows no relevant paper in its 4 labelled records "
            "(1 in 10 sampled), so what it hides cannot be estimated.") in lines
    assert not any("interval" in ln for ln in lines)
    # the same band fully read (weight 1) is a census band: nothing hidden, interval given
    census = kept_rows([("S1", 1, 3)]) + [row("S2", 1, "exclude", "exclude") for _ in range(4)]
    assert stratified_uncertainty(census, INC, INC)["interval"] is not None
    # one labelled record and two empty bands
    two = kept_rows([("S1", 1, 3)]) + [row("S2", 10, "exclude", "exclude")] + [row("S3", 50.5, "exclude", "exclude") for _ in range(2)]
    assert empty_bands_reason(empty_sampled_bands(two, INC)) == (
        "band S2 shows no relevant paper in its 1 labelled record (1 in 10 sampled); band S3 shows no relevant paper "
        "in its 2 labelled records (1 in 50.5 sampled), so what they hide cannot be estimated")
    # the band rule can be switched off (the simulation study compares with and without it)
    assert stratified_uncertainty(rows, INC, INC, band_rule=False)["interval"] is not None


def test_method_sentence_is_honest():
    """Owner answer a-20261024-094201-8c08: the interval is approximate, never called exact or validated."""
    assert "approximate" in METHOD_SENTENCE and "errs on the wide side" in METHOD_SENTENCE
    assert "exact" not in METHOD_SENTENCE.lower() and "validated" not in METHOD_SENTENCE.lower()
