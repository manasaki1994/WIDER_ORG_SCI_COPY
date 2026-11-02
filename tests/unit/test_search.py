import os
from pathlib import Path

import pytest

from wider.search.ir import Query
from wider.search.lint import lint
from wider.search.parse_ebsco import Parser, parse_ebsco, tokenize
from wider.search.render import render, render_all

EBSCO = """
# a comment line
(
  (TI (voice* OR silence OR "speaking up") OR AB (voice* OR silence OR "speaking up"))
  AND
  (
    (TI (whistleblow* OR (issue N5 sell*) OR (report* N5 (irregularit* OR problem*)))
     OR AB (whistleblow* OR (issue N5 sell*) OR (report* N5 (irregularit* OR problem*))))
    OR
    (TI ("upward communication" OR dissent*) OR AB ("upward communication" OR dissent*))
  )
  AND
  (TI (antecedent* OR (career W3 outcome*)) OR AB (antecedent* OR (career W3 outcome*)))
)
"""


def test_parse_recovers_blocks_and_logic():
    q = parse_ebsco(EBSCO)
    assert [b.key for b in q.blocks] == ["voice", "whistleblow", "upward_communication", "antecedent"]
    assert q.logic == {"and": ["voice", {"or": ["whistleblow", "upward_communication"]}, "antecedent"]}
    near = q.block("whistleblow").terms[2]
    assert near.near == ["report*", ["irregularit*", "problem*"]] and near.distance == 5 and not near.ordered
    assert q.block("antecedent").terms[1].ordered is True and q.block("antecedent").terms[1].distance == 3
    assert q.block("voice").fields == ["title", "abstract"]


def test_ebsco_roundtrip_is_structurally_identical():
    q = parse_ebsco(EBSCO)
    out = render(q, "ebsco", pretty=False)
    body = "\n".join(ln for ln in EBSCO.splitlines() if not ln.lstrip().startswith("#"))
    assert Parser(tokenize(body)).parse() == Parser(tokenize(out)).parse()
    assert parse_ebsco(out).model_dump() == q.model_dump()
    assert "    )" not in out  # compact output has no indentation residue


def test_renderers_per_platform():
    q = parse_ebsco(EBSCO)
    s = render_all(q, pretty=False)
    assert s["scopus"].startswith("(TITLE-ABS(voice*") and 'W/5' in s["scopus"] and "PRE/3" in s["scopus"]
    assert "TI=(" in s["wos"] and "AB=(" in s["wos"] and "NEAR/5" in s["wos"]
    assert "TI,AB(" in s["proquest"] and "NEAR/5" in s["proquest"] and "PRE/3" in s["proquest"]
    assert ".ti,ab." in s["ovid"] and "voice$" in s["ovid"] and "ADJ5" in s["ovid"] and '"' not in s["ovid"]
    assert "voice*[tiab]" in s["pubmed"] and "(issue[tiab] AND sell*[tiab])" in s["pubmed"]
    assert '"upward communication"[tiab]' in s["pubmed"]
    assert len(s["gscholar"]) <= 256 and "*" not in s["gscholar"] and "(voice OR silence OR " in s["gscholar"]
    pretty = render(q, "ebsco")
    assert "\n" in pretty and parse_ebsco(pretty).model_dump() == q.model_dump()


def test_parser_errors():
    with pytest.raises(ValueError):
        parse_ebsco("(TI (a OR b)")
    with pytest.raises(ValueError):
        parse_ebsco("TI (a) AND")


def test_lint_catches_problems():
    q = Query.model_validate({"blocks": [
        {"key": "a", "name": "a", "terms": ["abc*", "poor performance", "behaviour*", "behaviour*", "fine"]},
        {"key": "unused", "name": "u", "terms": ["x"]},
    ], "logic": "a"})
    issues = lint(q)
    msgs = {(i["block"], i["message"].split(":")[0]) for i in issues}
    assert ("a", "short truncation stem") in msgs
    assert ("a", "multi-word term is not quoted") in msgs
    assert ("a", "duplicate term") in msgs
    assert ("a", "spelling variant missing") in msgs
    assert ("unused", "block is not used in the logic") in msgs
    assert not any(i["message"].startswith("spelling") for i in lint(Query.model_validate(
        {"blocks": [{"key": "a", "name": "a", "terms": ["behavio*"]}], "logic": "a"})))


@pytest.mark.skipif(not os.environ.get("WIDER_CASE_DATA"), reason="real data not configured")
def test_golden_ebsco_string_roundtrips():
    """The saved search string of the original review parses and renders back to the same structure."""
    src = next(Path(os.environ["WIDER_CASE_DATA"]).glob("Queries/business_source_premier_query_*.txt")).read_text()
    q = parse_ebsco(src)
    assert q.blocks and all(b.terms for b in q.blocks)
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert Parser(tokenize(body)).parse() == Parser(tokenize(render(q, "ebsco", pretty=False))).parse()


# ----------------------------------------------------------------- field codes (vision change 2a, task 2.3)
# What render/boolean.py gave before the codes moved into platforms.yaml: every style, every field combination,
# compact; and one pretty two-block string per style. The strings must never change with the move.
FIELD_CODE_STRINGS = [
    ("ebsco", "title", '(TI (voice* OR "employee voice"))'),
    ("ebsco", "abstract", '(AB (voice* OR "employee voice"))'),
    ("ebsco", "keywords", '(KW (voice* OR "employee voice"))'),
    ("ebsco", "title,abstract", '(TI (voice* OR "employee voice") OR AB (voice* OR "employee voice"))'),
    ("ebsco", "title,keywords", '(TI (voice* OR "employee voice") OR KW (voice* OR "employee voice"))'),
    ("ebsco", "abstract,keywords", '(AB (voice* OR "employee voice") OR KW (voice* OR "employee voice"))'),
    ("ebsco", "title,abstract,keywords", '(TI (voice* OR "employee voice") OR AB (voice* OR "employee voice") OR KW (voice* OR "employee voice"))'),
    ("scopus", "title", 'TITLE(voice* OR "employee voice")'),
    ("scopus", "abstract", 'ABS(voice* OR "employee voice")'),
    ("scopus", "keywords", 'TITLE-ABS-KEY(voice* OR "employee voice")'),
    ("scopus", "title,abstract", 'TITLE-ABS(voice* OR "employee voice")'),
    ("scopus", "title,keywords", 'TITLE-ABS-KEY(voice* OR "employee voice")'),
    ("scopus", "abstract,keywords", 'TITLE-ABS-KEY(voice* OR "employee voice")'),
    ("scopus", "title,abstract,keywords", 'TITLE-ABS-KEY(voice* OR "employee voice")'),
    ("wos", "title", '(TI=(voice* OR "employee voice"))'),
    ("wos", "abstract", '(AB=(voice* OR "employee voice"))'),
    ("wos", "keywords", '(AK=(voice* OR "employee voice"))'),
    ("wos", "title,abstract", '(TI=(voice* OR "employee voice") OR AB=(voice* OR "employee voice"))'),
    ("wos", "title,keywords", '(TI=(voice* OR "employee voice") OR AK=(voice* OR "employee voice"))'),
    ("wos", "abstract,keywords", '(AB=(voice* OR "employee voice") OR AK=(voice* OR "employee voice"))'),
    ("wos", "title,abstract,keywords", 'TS=(voice* OR "employee voice")'),
    ("proquest", "title", 'TI(voice* OR "employee voice")'),
    ("proquest", "abstract", 'AB(voice* OR "employee voice")'),
    ("proquest", "keywords", 'IF(voice* OR "employee voice")'),
    ("proquest", "title,abstract", 'TI,AB(voice* OR "employee voice")'),
    ("proquest", "title,keywords", 'TI,IF(voice* OR "employee voice")'),
    ("proquest", "abstract,keywords", 'AB,IF(voice* OR "employee voice")'),
    ("proquest", "title,abstract,keywords", 'TI,AB,IF(voice* OR "employee voice")'),
    ("ovid", "title", '(voice$ OR employee voice).ti.'),
    ("ovid", "abstract", '(voice$ OR employee voice).ab.'),
    ("ovid", "keywords", '(voice$ OR employee voice).kw.'),
    ("ovid", "title,abstract", '(voice$ OR employee voice).ti,ab.'),
    ("ovid", "title,keywords", '(voice$ OR employee voice).ti,kw.'),
    ("ovid", "abstract,keywords", '(voice$ OR employee voice).ab,kw.'),
    ("ovid", "title,abstract,keywords", '(voice$ OR employee voice).ti,ab,kw.'),
    ("pubmed", "title", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "abstract", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "keywords", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "title,abstract", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "title,keywords", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "abstract,keywords", '(voice*[tiab] OR "employee voice"[tiab])'),
    ("pubmed", "title,abstract,keywords", '(voice*[tiab] OR "employee voice"[tiab])'),
]
FIELD_CODE_PRETTY = {
    "ebsco": '(\n    (TI (\n        voice* OR "employee voice"\n    ) OR AB (\n        voice* OR "employee voice"\n    ) OR KW (\n        voice* OR "employee voice"\n    ))\n    AND\n    (KW (\n        silence\n    ))\n)',
    "scopus": '(\n    TITLE-ABS-KEY(\n        voice* OR "employee voice"\n    )\n    AND\n    TITLE-ABS-KEY(\n        silence\n    )\n)',
    "wos": '(\n    TS=(\n        voice* OR "employee voice"\n    )\n    AND\n    (AK=(\n        silence\n    ))\n)',
    "proquest": '(\n    TI,AB,IF(\n        voice* OR "employee voice"\n    )\n    AND\n    IF(\n        silence\n    )\n)',
    "ovid": '(\n    (\n        voice$ OR employee voice\n    ).ti,ab,kw.\n    AND\n    (\n        silence\n    ).kw.\n)',
    "pubmed": '(\n    (voice*[tiab] OR "employee voice"[tiab])\n    AND\n    (silence[tiab])\n)',
}


def test_field_codes_unchanged_for_every_style():
    assert len(FIELD_CODE_STRINGS) == 6 * 7
    for style, fields, expected in FIELD_CODE_STRINGS:
        q = Query(blocks=[{"key": "v", "terms": ["voice*", '"employee voice"'], "fields": fields.split(",")}], logic="v")
        assert render(q, style, pretty=False) == expected, (style, fields)
    two = Query(blocks=[{"key": "v", "terms": ["voice*", '"employee voice"'], "fields": ["title", "abstract", "keywords"]},
                        {"key": "s", "terms": ["silence"], "fields": ["keywords"]}], logic={"and": ["v", "s"]})
    for style, expected in FIELD_CODE_PRETTY.items():
        assert render(two, style) == expected, style
