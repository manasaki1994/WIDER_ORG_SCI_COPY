"""Ranking's seed paper check card (owner walkthrough R2): the big figure is how many development seed papers rank in
the top 10%, a green edge (ok) when all do and a rose edge (warn) when some fall below; it informs and blocks nothing."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "wider/ui/static/app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")


def card(v):
    s = APP.read_text(encoding="utf-8")
    fn = s[s.index("function rkSeedCardHtml"):s.index("function pctTable")]
    js = ("const esc = (x) => String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;');\n" + fn +
          f"\nprocess.stdout.write(rkSeedCardHtml({json.dumps(v)}));")
    return subprocess.run([NODE, "-"], input=js, text=True, capture_output=True, check=True).stdout


def test_all_seed_papers_in_the_top_share_is_ok():
    h = card({"n": 500, "summary": {"development": {"recall_at_pct": [{"pct": 5, "rank": 25, "k": 9, "n": 12},
                                                                       {"pct": 10, "rank": 50, "k": 12, "n": 12}]},
                                    "validation": {"recall_at_pct": [{"pct": 10, "rank": 50, "k": 3, "n": 4}]}}})
    assert 'data-rk-seed="ok"' in h and "<b>12 of 12</b> development seed papers rank in the top 10%" in h
    assert "check your anchor sentences" not in h
    assert "<dt>validation seed papers in the top 10%</dt><dd>3 of 4</dd>" in h


def test_some_below_is_warn_and_says_what_to_check():
    h = card({"summary": {"development": {"recall_at_pct": [{"pct": 10, "rank": 50, "k": 10, "n": 12}]}}})
    assert 'data-rk-seed="warn"' in h and "<b>10 of 12</b>" in h and "check your anchor sentences" in h
    assert "<button" not in h


def test_no_seed_papers_gives_no_card():
    assert card({"summary": {}}) == ""
    assert card({"summary": {"development": {"recall_at_pct": [{"pct": 10, "rank": 50, "k": 0, "n": 0}]}}}) == ""


def test_set_names_as_the_api_spells_them():
    """/api/rank/validate names the sets "Development" and "Validation": the card still leads with development and
    writes the names in lower case mid-sentence."""
    h = card({"summary": {"Validation": {"recall_at_pct": [{"pct": 10, "rank": 6, "k": 1, "n": 2}]},
                          "Development": {"recall_at_pct": [{"pct": 10, "rank": 6, "k": 0, "n": 4}]}}})
    assert "<b>0 of 4</b> development seed papers rank in the top 10%" in h
    assert "<dt>validation seed papers in the top 10%</dt><dd>1 of 2</dd>" in h
