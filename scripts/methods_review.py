#!/usr/bin/env python3
"""Keep the methods topics (``wider/guide/methods/``) in step with the code they describe (methods-explainer D6).

  python3 scripts/methods_review.py stale                      list topics whose code or defaults changed
  python3 scripts/methods_review.py stamp <topic>|--all|glossary
                                                               after updating a topic's text: new fingerprint,
                                                               status drafted, today's date (on a new Python minor
                                                               an unchanged reviewed text keeps its review)
  python3 scripts/methods_review.py approve <topic>|glossary --answer <dashboard id>
                                                               record the owner's review

A developer tool with no UI twin, like ``check_no_private_data.py``: it is not a researcher's action.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wider.guide import methods  # noqa: E402


def main(argv: list[str] | None = None, root: Path | None = None, base: Path | None = None) -> int:
    ap = argparse.ArgumentParser(prog="methods_review.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stale", help="list topics whose fingerprint no longer matches")
    st = sub.add_parser("stamp", help="store a topic's new fingerprint as drafted")
    st.add_argument("topic", nargs="?")
    st.add_argument("--all", action="store_true")
    ap_ = sub.add_parser("approve", help="record the owner's review of a topic or the glossary")
    ap_.add_argument("topic")
    ap_.add_argument("--answer", required=True, help="the dashboard question id the owner answered")
    a = ap.parse_args(argv)

    if a.cmd == "stale":
        note = methods.other_minor(root)
        if note:
            print(note)
        ids = methods.stale(root, base)
        for t in ids:
            print(t)
        if not ids:
            print("no methods topic is stale")
        return 0
    try:
        if a.cmd == "stamp":
            if a.all == bool(a.topic):
                ap.error("stamp needs one topic, glossary or --all")
            if a.all:
                done = methods.stamp_all(root, base)
            else:
                data = methods.stamp(a.topic, root, base)
                entry = data[a.topic] if a.topic == methods.GLOSSARY else data["topics"][a.topic]
                done = [(a.topic, entry["status"])]
            for t, status in done:
                print(f"stamped {t}: {status}")
        else:
            methods.approve(a.topic, a.answer, root, base)
            print(f"approved {a.topic}: reviewed (answer {a.answer})")
    except KeyError:
        print(methods.NO_TOPIC.format(topic=a.topic), file=sys.stderr)
        return 1
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
