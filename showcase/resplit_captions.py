"""Re-split a captions track that record.py wrote before captions were split (tester 1 Nov 2026), without filming
again: every caption keeps its time span and is cut into cues of at most two short lines (record.caption_cues), a
tour card's title gets the full stop the caption needs (scenes.titled), and ``--card STEP`` replaces a card's
caption with the text the tour shows now (for a card whose numbers changed after filming). The chapters track is
not touched. Prints each caption's reading speed in characters per second.

    python3 showcase/resplit_captions.py showcase/site/wider-showcase.captions.vtt --card recall-interval
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / "demo")]

import build_demo  # noqa: E402
import record  # noqa: E402
from scenes import titled  # noqa: E402


def read_vtt(text: str) -> list[tuple[float, float, str]]:
    """The captions of a WebVTT text as (start, stop, text), pieces "n-1", "n-2", ... of one caption joined again."""
    def secs(t: str) -> float:
        return sum(float(x) * f for x, f in zip(t.strip().split(":"), (3600, 60, 1)))
    out: list[list] = []
    last = None
    for block in text.strip().split("\n\n")[1:]:
        cue, timing, *lines = block.splitlines()
        start, stop = (secs(t) for t in timing.split("-->"))
        head, _, part = cue.partition("-")
        body = " ".join(x.strip() for x in lines)
        if part and part != "1" and last == head:
            out[-1][1] = stop
            out[-1][2] += " " + body
        else:
            out.append([start, stop, body])
        last = head
    return [tuple(x) for x in out]


def card_caption(step: dict) -> str:
    return " ".join([titled(step["title"])] + step["body"])


def resplit(text: str, steps: list[dict], cards: list[str] = ()) -> str:
    """``text`` (a captions track) with each caption split, the titles of ``steps`` given their full stop and the
    captions of the steps named in ``cards`` replaced by their current card text."""
    by_title = sorted(steps, key=lambda s: -len(s["title"]))
    lines = ["WEBVTT", ""]
    for n, (start, stop, body) in enumerate(read_vtt(text), 1):
        step = next((s for s in by_title if body.startswith(s["title"] + " ")
                     or body.startswith(titled(s["title"]) + " ")), None)
        if step and step["id"] in cards:
            body = card_caption(step)
        elif step and not body.startswith(titled(step["title"])):
            body = titled(step["title"]) + body[len(step["title"]):]
        lines += record.caption_cues(n, start, stop, body)
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("vtt", type=Path)
    ap.add_argument("--card", action="append", default=[], help="a tour step id whose caption takes today's card text")
    a = ap.parse_args()
    steps = build_demo.tour_data()["steps"]
    unknown = sorted(set(a.card) - {s["id"] for s in steps})
    if unknown:
        sys.exit(f"no tour step {', '.join(unknown)}")
    new = resplit(a.vtt.read_text(encoding="utf-8"), steps, a.card)
    a.vtt.write_text(new, encoding="utf-8")
    for n, cps in record.caption_speeds(new):
        print(f"caption {n}: {cps} characters per second" + ("  (fast)" if cps > 20 else ""))


if __name__ == "__main__":
    main()
