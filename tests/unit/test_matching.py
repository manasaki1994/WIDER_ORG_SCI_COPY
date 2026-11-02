from wider.core.matching import CorpusRecord, jaro_winkler, locate_benchmark, quote_verified, token_jaccard


def test_jaro_winkler_bounds():
    assert jaro_winkler("Team meetings", "Team meetings") == 1.0
    assert 0.8 < jaro_winkler("Team meetings", "Team & meeting") < 1.0
    assert jaro_winkler("abc", "xyz") < 0.5


def test_token_jaccard():
    assert token_jaccard("a b c", "a b d") == 0.5
    assert token_jaccard("", "") == 1.0
    assert token_jaccard("a", "") == 0.0


def test_locate_benchmark_doi_title_fuzzy_none():
    corpus = [
        CorpusRecord("r1", "Meetings for Teams That Err", doi="10.1000/abc"),
        CorpusRecord("r2", "Who speaks up: voice and silence at work"),
        CorpusRecord("r3", "Who speaks up? Voice and silence at work"),
        CorpusRecord("r4", "Completely unrelated title about plankton"),
    ]
    bm = [
        ("B1", "different title", "https://doi.org/10.1000/ABC"),
        ("B2", "Who Speaks Up: Voice and Silence at Work.", None),
        ("B3", "Who speaks up - voice and silence in work", None),
        ("B4", "Plankton blooms and ocean temperature in 2020", None),
    ]
    m = {x.benchmark_id: x for x in locate_benchmark(bm, corpus)}
    assert (m["B1"].method, m["B1"].record_id) == ("doi", "r1")
    assert (m["B2"].method, m["B2"].record_id) == ("title", "r2")
    assert m["B3"].method == "fuzzy" and m["B3"].needs_confirmation and m["B3"].record_id in {"r2", "r3"}
    assert m["B4"].method == "none" and m["B4"].record_id is None


def test_quote_verified():
    text = "Title: Voice and teams. Abstract: Junior employees spoke up less often than senior employees about identical errors."
    assert quote_verified("Junior employees spoke up less", text)
    assert quote_verified("spoke up less ... identical errors", text)
    assert not quote_verified("The abstract does not mention voice", text)
    assert not quote_verified("spoke", text)  # shorter than the minimum fragment
    assert not quote_verified("", text)
    assert not quote_verified(None, text)
