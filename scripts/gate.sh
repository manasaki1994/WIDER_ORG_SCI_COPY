#!/bin/sh
# The gate before a commit: unit tests, ruff, the privacy guard and openspec validation, in one short summary.
# Usage: sh scripts/gate.sh            (10 to 18 minutes; prints the last lines of each check)
cd "$(dirname "$0")/.." || exit 1
# in parallel when pytest-xdist is installed (the tests share no port or file; see tests/conftest.py), else serially
PAR=$(.venv/bin/python -c "import xdist" 2>/dev/null && echo "-n auto")
.venv/bin/python -m pytest -q -p no:warnings $PAR 2>&1 | tail -3
.venv/bin/ruff check wider tests 2>&1 | tail -3
python3 scripts/check_no_private_data.py >/dev/null 2>&1; echo "privacy $?"
openspec validate --all --strict 2>&1 | tail -1
