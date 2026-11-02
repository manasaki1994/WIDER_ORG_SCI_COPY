#!/usr/bin/env python3
"""Generate a small synthetic project for checking the Rounds card (Search strings page) and the upload card's round
selector (Import page) by hand. Built with the same helpers as the rounds tests in tests/unit/test_dry_run.py:

- three invented seed papers: B001 and B002 about huddles, B003 about briefings, all in the development set;
- synonyms v1 (huddle*, briefing*) retrieves all three; v2 (huddle*) loses B003;
- both versions dry-run on scopus; an export of 3 invented records imported under v1; v1 accepted;
- one more file (b.ris) waiting under inputs/scopus/, to import under a round chosen on the Import page.

Usage (from the repo root; keep WIDER_HOME away from your own settings):

    WIDER_HOME=/tmp/rounds-home python scripts/make_rounds_example.py /tmp/rounds
    WIDER_HOME=/tmp/rounds-home python -m wider ui --dir /tmp/rounds/rounds-ui --no-open --port 8782

Then open the Search strings page: type a count for v2, accept v2 (a confirm step names B003 as lost), Cancel, and so
on. Everything is invented; nothing is sent anywhere. The output folder is deleted and rebuilt on every run.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # the test helpers live in tests/unit

from tests.unit.test_dry_run import RIS, RIS3, _cli, _dry_cli, _rounds_proj, _waiting  # noqa: E402


def main(base: Path):
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True)
    p = _rounds_proj(base, ("huddle*", "briefing*"), ("huddle*",), name="rounds-ui")
    for v in ("1", "2"):
        r, _ = _dry_cli(p, "--version", v, "--platform", "scopus")
        assert r.exit_code == 0, r.output
    _waiting(p, base, "a.ris", RIS3)
    for args in (("import", "run", "--round", "1"), ("query", "accept", "1")):
        r = _cli(p, *args)
        assert r.exit_code == 0, r.output
    _waiting(p, base, "b.ris", RIS.replace("10.1000/abc", "10.1000/zz"))
    return p


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: make_rounds_example.py OUTPUT_DIR")
    p = main(Path(sys.argv[1]).resolve())
    print(f"project: {p.root}")
    print(_cli(p, "query", "rounds").output)
