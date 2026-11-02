from wider.core.text import (
    clean_doi,
    clean_title,
    first_author_surname,
    is_thin,
    norm_text,
    norm_title,
    truncate_words,
    uk_us_variants,
    word_count,
    year_of,
)


def test_clean_doi_variants():
    assert clean_doi("https://doi.org/10.1037/APL0000123") == "10.1037/apl0000123"
    assert clean_doi("doi: 10.1016/j.obhdp.2020.01.002.") == "10.1016/j.obhdp.2020.01.002"
    assert clean_doi("DOI:10.5465/amj.2019.0001)") == "10.5465/amj.2019.0001"
    assert clean_doi("not a doi") is None
    assert clean_doi("") is None
    assert clean_doi(None) is None


def test_norm_text_ascii_and_spacing():
    assert norm_text("  Manager’s  Meetings: An Experiment! ") == "manager s meetings an experiment"
    assert norm_text("Über-Grösse") == "uber grosse"
    assert norm_text("<i>Title</i> here") == "title here"


def test_clean_title_and_norm_title():
    assert clean_title("A study.  ") == "A study"
    assert norm_title("The Voice Gap in Teams.") == "the voice gap in teams"


def test_word_count_and_thin():
    assert word_count("one two  three") == 3
    assert word_count("") == 0
    assert word_count(None) == 0
    assert is_thin("short abstract") is True
    assert is_thin(" ".join(["w"] * 20)) is False


def test_truncate_words():
    assert truncate_words("a b c d", 2) == "a b"
    assert truncate_words("a b", 5) == "a b"


def test_uk_us_variants():
    v = uk_us_variants("misbehaviour")
    assert v[0] == "misbehaviour" and "misbehavior" in v
    assert uk_us_variants("Organizational") == ["Organizational", "Organisational"]
    assert uk_us_variants("fraud") == ["fraud"]


def test_first_author_and_year():
    assert first_author_surname(["Eagly, Alice H.", "Karau, S."]) == "eagly"
    assert first_author_surname("Rudman, L.; Glick, P.") == "rudman"
    assert first_author_surname([]) == ""
    assert year_of("2019///") == 2019
    assert year_of("n.d.") is None
