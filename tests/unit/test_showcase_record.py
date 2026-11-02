"""The showcase video's recorder and script (openspec change demo-chapters-and-video, design D8): the scenes follow
the demo's chapters, the chapter and caption tracks are written from the takes, and the static mode serves only the
site folder."""
from __future__ import annotations

import http.server
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "showcase"))
import record  # noqa: E402
import scenes  # noqa: E402

TOUR = yaml.safe_load((REPO / "showcase" / "demo" / "tour.yaml").read_text(encoding="utf-8"))


def test_demo_scenes_follow_chapters():
    chapters = [c for c in TOUR["chapters"] if any(s["stage"] == c["stage"] for s in TOUR["steps"])]
    plan = scenes.plan(TOUR)
    assert [p["title"] for p in plan] == [c["title"] for c in chapters]
    kept = {sid for p in plan for sid in p["steps"]}
    by_id = {s["id"]: s for s in TOUR["steps"]}
    # every core step and every action step is in the video; only show-only extra steps may be left out
    for s in TOUR["steps"]:
        if s.get("core") or s.get("action"):
            assert s["id"] in kept, s["id"]
    for p in plan:
        stage_steps = [s["id"] for s in TOUR["steps"] if s["stage"] == p["stage"]]
        assert p["steps"] == [sid for sid in stage_steps if sid in p["steps"]]   # in the tour's order
        assert p["steps"], p["title"]
        # a chapter left with extra steps fits the limit; one over it holds core and action steps only
        if p["seconds"] > scenes.LIMIT:
            assert all(by_id[sid].get("core") or by_id[sid].get("action") for sid in p["steps"][1:]), p["title"]
    # the pacing formula, over the title and first paragraph only
    assert scenes.hold("x" * 15) == 2.0 and scenes.hold("x" * 150) == pytest.approx(10.5)
    total = sum(p["seconds"] for p in plan) / 60
    assert 4.5 <= total <= 7.0, total
    # the scene list: the result first, a scene per chapter, the end card; the opening is filmed after the chapters
    names = [n for n, _ in scenes.SCENES]
    assert names[1] == "opening" and names[-1] == "ending" and len(names) == len(plan) + 3
    assert scenes.FILM_ORDER.index("opening") > scenes.FILM_ORDER.index(f"ch{len(plan):02d}")
    assert sorted(scenes.FILM_ORDER) == sorted(names)


def test_chapter_tracks_written(tmp_path):
    fps = record.FPS
    takes = [(0, []),                                                      # a scene with no frame
             (fps * 10, [("chapter", 0, "The result first"), ("caption", 0, "Where the review ends")]),
             (fps * 20, [("chapter", 0, "1. Getting started"), ("caption", fps * 2, "Chapter 1 --> start")])]
    tracks = record.vtt_tracks(takes)
    ch = tracks["chapters"].splitlines()
    assert ch[0] == "WEBVTT"
    assert "00:00:00.000 --> 00:00:10.000" in ch and "The result first" in ch
    assert "00:00:10.000 --> 00:00:30.000" in ch and "1. Getting started" in ch
    cap = tracks["captions"]
    assert "00:00:00.000 --> 00:00:12.000" in cap and "00:00:12.000 --> 00:00:30.000" in cap
    assert "Chapter 1 → start" in cap          # a cue's text never holds the arrow that ends its timing line
    assert record.vtt_time(3725.5) == "01:02:05.500"

    # written next to the video from the takes on disk
    rec = record.Recorder(tmp_path, "http://localhost:1/")
    rec.begin_take("a")
    rec.manifest = [("frame", "frames/a/00001.png", fps * 3)]
    rec.cue("chapter", "One")
    rec.manifest.append(("frame", "frames/a/00002.png", fps * 2))
    rec.end_take()
    paths = rec.write_tracks(["a", "missing"], tmp_path)
    assert sorted(p.name for p in paths) == ["wider-showcase.captions.vtt", "wider-showcase.chapters.vtt"]
    assert "00:00:03.000 --> 00:00:05.000\nOne" in (tmp_path / "wider-showcase.chapters.vtt").read_text()


LONG = ("Where the two rounds differ. Two different comparisons. Within one round, each record is read twice (reads A "
        "and B); where the two reads disagree, a third read decides. In the first round that happened for 3 of 74 records.")


def test_a_long_caption_is_split_into_short_cues_over_its_own_time():
    """Tester 1 Nov 2026: a whole tour card was one caption. Each cue has at most two lines of at most 42 characters,
    the pieces share the caption's time by length and are numbered n-1, n-2, ...; no word is lost; a short caption
    stays one cue under its plain number."""
    fps = record.FPS
    takes = [(fps * 30, [("caption", 0, "Short one."), ("caption", fps * 10, LONG)])]
    blocks = record.vtt_tracks(takes)["captions"].strip().split("\n\n")[1:]
    ids = [b.splitlines()[0] for b in blocks]
    assert ids[0] == "1" and ids[1:] == [f"2-{k}" for k in range(1, len(ids))] and len(ids) > 3
    for b in blocks:
        text = b.splitlines()[2:]
        assert 1 <= len(text) <= record.CAPTION_LINES and all(len(x) <= record.CAPTION_CHARS for x in text)
    times = [b.splitlines()[1].split(" --> ") for b in blocks[1:]]
    assert times[0][0] == "00:00:10.000" and times[-1][1] == "00:00:30.000"
    assert all(a[1] == b[0] for a, b in zip(times, times[1:]))     # back to back, no gap and no overlap
    assert " ".join(" ".join(b.splitlines()[2:]) for b in blocks[1:]).split() == LONG.split()
    # a short sentence that fills half a line ends its cue: the title is a cue of its own
    assert blocks[1].splitlines()[2:] == ["Where the two rounds differ."]
    assert record.caption_speeds(record.vtt_tracks(takes)["captions"])[0] == ("1", 1.0)


def test_card_caption_titles_end_with_a_full_stop():
    assert scenes.titled("Where the two rounds differ") == "Where the two rounds differ."
    assert scenes.titled("Ready?") == "Ready?" and scenes.titled("") == ""


def test_resplit_keeps_spans_fixes_titles_and_replaces_a_named_card():
    sys.path.insert(0, str(REPO / "showcase" / "demo"))
    import resplit_captions
    old = ("WEBVTT\n\n1\n00:00:00.000 --> 00:00:10.000\nWhere the two rounds differ " + LONG.split(". ", 1)[1] +
           "\n\n2\n00:00:10.000 --> 00:00:20.000\nAn estimate with its uncertainty For the first round 100% to 100%.\n\n"
           "3\n00:00:20.000 --> 00:00:21.000\nDone.\n")
    steps = [{"id": "screen-compare", "title": "Where the two rounds differ", "body": ["x"]},
             {"id": "recall-interval", "title": "An estimate with its uncertainty", "body": ["From 54% to 100%."]}]
    new = resplit_captions.resplit(old, steps, ["recall-interval"])
    back = resplit_captions.read_vtt(new)
    assert [(a, b) for a, b, _ in back] == [(0.0, 10.0), (10.0, 20.0), (20.0, 21.0)]
    assert back[0][2].startswith("Where the two rounds differ. Two different comparisons.")
    assert back[1][2] == "An estimate with its uncertainty. From 54% to 100%."
    assert resplit_captions.resplit(new, steps, ["recall-interval"]) == new      # running it again changes nothing


@pytest.fixture()
def site_server(tmp_path):
    site = tmp_path / "site"
    (site / "demo").mkdir(parents=True)
    (site / "demo" / "index.html").write_text("<p>demo</p>")
    (site / "fonts").mkdir()
    (site / "fonts" / "a.woff2").write_bytes(b"font")
    (tmp_path / "secret.txt").write_text("outside")
    old = (record._Proxy.site, record._Proxy.app, record._Proxy.stage)
    record._Proxy.site, record._Proxy.app, record._Proxy.stage = site, "http://127.0.0.1:9/", b"<p>stage</p>"
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), record._Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    record._Proxy.site, record._Proxy.app, record._Proxy.stage = old


def _get(url, method="GET"):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method=method, data=b"x" if method == "POST" else None),
                                   timeout=10)
        return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


def test_static_mode_serves_only_the_site(site_server):
    assert _get(site_server + "/__stage__.html")[1] == b"<p>stage</p>"
    status, body, kind = _get(site_server + "/demo/")
    assert status == 200 and body == b"<p>demo</p>" and kind.startswith("text/html")
    assert _get(site_server + "/fonts/a.woff2")[:2] == (200, b"font")
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/demo/%2e%2e/%2e%2e/secret.txt", "/nothing.js",
                 "/%2fetc%2fpasswd"):
        assert _get(site_server + path)[0] == 404, path
    # nothing is written and nothing is forwarded to an app
    assert _get(site_server + "/demo/index.html", "POST")[0] == 405
