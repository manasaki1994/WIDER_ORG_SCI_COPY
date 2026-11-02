"""Building the interactive demo's static folder (openspec change interactive-demo, design D1, D2, D9, D10).

The tests pass a fixed ``user`` so the login name of the computer that runs them plays no part.
"""
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("demo_build", REPO / "showcase" / "demo" / "build_demo.py")
build_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_demo)

USER = "nobody-in-particular"


def _build(tmp_path, **kw):
    kw.setdefault("check_coverage", False)
    kw.setdefault("user", USER)
    return build_demo.build(tmp_path / "demo", **kw)


def _layer(tmp_path, extra: str = "") -> Path:
    """A copy of the demo layer, with ``extra`` added to demo.js."""
    d = tmp_path / "layer"
    d.mkdir()
    for f in build_demo.LAYER:
        src = build_demo.HERE / f
        if src.exists():
            (d / f).write_bytes(src.read_bytes())
    (d / "demo.js").write_text((d / "demo.js").read_text(encoding="utf-8") + extra, encoding="utf-8")
    return d


def test_build_refuses_uncovered_path(tmp_path):
    coverage = tmp_path / "coverage.json"
    coverage.write_text("{}", encoding="utf-8")
    with pytest.raises(build_demo.BuildError, match=r"calls with no answer in the demo:[\s\S]*POST /api/labels    records.js"):
        _build(tmp_path, coverage_path=coverage, check_coverage=True)
    assert not (tmp_path / "demo").exists()


def test_build_refuses_a_listed_handler_that_does_not_exist(tmp_path):
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"handlers": ["POST /api/labels", "POST /api/made-up"]}), encoding="utf-8")
    with pytest.raises(build_demo.BuildError, match=r"does not have:\n  POST /api/made-up$"):
        _build(tmp_path, coverage_path=coverage, check_coverage=True)
    assert not (tmp_path / "demo").exists()


def test_csp_and_no_outside_address(tmp_path):
    out = _build(tmp_path)
    html = (out / "index.html").read_text(encoding="utf-8")
    head = html.split("<head>", 1)[1]
    assert head.lstrip().startswith('<meta http-equiv="Content-Security-Policy"')
    assert "connect-src 'none'" in build_demo.CSP and "form-action 'none'" in build_demo.CSP
    assert "<title>Demo — WIDER</title>" in html
    assert "/static/" not in html
    order = [html.index(f'src="/demo/{f}"') for f in ("snapshot.js", "demo.js", "actions.js", "screening.js",
                                                              "common.js", "shell.js", "terms.js", "tour.js")]
    assert order == sorted(order)
    # every script of the layer is loaded, not only copied
    for f in build_demo.LAYER:
        if f.endswith(".js"):
            assert f'src="/demo/{f}"' in html, f
    assert html.index("/demo/style.css") < html.index("/demo/demo.css")
    for f in sorted(out.rglob("*")):
        if f.suffix in {".html", ".js", ".css"}:
            assert build_demo.outside_addresses(f.read_text(encoding="utf-8")) == [], f.name


def test_terms_data_follows_the_papers_and_blocks():
    """The terms panel counts on the papers of papers.yaml with their reference decisions, and starts from the
    blocks of synonyms.yaml (design D6)."""
    import yaml
    papers = yaml.safe_load((build_demo.HERE / "papers.yaml").read_text(encoding="utf-8"))["papers"]
    data = build_demo.terms_data()
    assert [(r["id"], r["title"], r["decision"]) for r in data["records"]] == [
        (p["id"], p["title"], p["decision"]) for p in papers]
    assert all(r["abstract"] for r in data["records"])
    assert len(data["blocks"]) == 2 and all(b["name"] and b["terms"] for b in data["blocks"])
    assert '"terms":' in build_demo.snapshot_js(build_demo.SNAPSHOT, {}, USER)


@pytest.mark.parametrize("planted, kind", [
    ('<img src="https://example.org/x.png">', "a src attribute"),
    ("body { background: url(//example.org/x.png); }", "a CSS url()"),
    ("@import 'x.css';", "a CSS @import"),
    ('fetch("https://example.org/api")', "a fetch of an outside address"),
])
def test_outside_loads_are_found(planted, kind):
    assert kind in build_demo.outside_addresses(planted)
    assert build_demo.outside_addresses('<img src="/demo/x.png"> url(data:image/png;base64,AA)') == []


def test_static_files_copied_unchanged(tmp_path):
    out = _build(tmp_path)
    for f in sorted(build_demo.STATIC.rglob("*")):
        if f.is_file() and f.name not in {"index.html", ".DS_Store"}:
            rel = f.relative_to(build_demo.STATIC)
            assert (out / rel).read_bytes() == f.read_bytes(), rel
    for f in build_demo.REQUIRED_LAYER + ("snapshot.js",):
        assert (out / f).is_file()


def test_bundle_has_no_paths_names_or_keys(tmp_path):
    out = _build(tmp_path)
    snap = (out / "snapshot.js").read_text(encoding="utf-8")
    assert snap.startswith("window.DEMO_SNAPSHOT = ")
    assert not re.search(r"/Users/(?!you\b)|/private/var|/var/folders", snap)
    assert USER not in snap
    files = {f.relative_to(out).as_posix(): f.read_bytes() for f in out.rglob("*") if f.is_file()}
    assert build_demo.scan(files, USER) == []


def test_build_refuses_key_like_string(tmp_path):
    layer = _layer(tmp_path, '\n// "sk-' + "x" * 30 + '"\n')
    with pytest.raises(build_demo.BuildError, match=r"demo\.js: a key-like string"):
        _build(tmp_path, layer_dir=layer)
    assert not (tmp_path / "demo").exists()


def test_build_refuses_login_name_and_outside_load(tmp_path):
    layer = _layer(tmp_path, f'\n// made by {USER}\nfetch("https://example.org/x");\n')
    with pytest.raises(build_demo.BuildError) as e:
        _build(tmp_path, layer_dir=layer)
    assert "demo.js: the login name" in str(e.value)
    assert "demo.js: a fetch of an outside address" in str(e.value)


def test_app_files_pass_the_scan():
    files = {f.relative_to(build_demo.STATIC).as_posix(): f.read_bytes()
             for f in build_demo.STATIC.rglob("*") if f.is_file()}
    assert build_demo.scan(files, USER) == []


def test_build_is_deterministic(tmp_path):
    a = _build(tmp_path / "a")
    b = _build(tmp_path / "b")
    fa = {f.relative_to(a).as_posix(): f.read_bytes() for f in a.rglob("*") if f.is_file()}
    fb = {f.relative_to(b).as_posix(): f.read_bytes() for f in b.rglob("*") if f.is_file()}
    assert fa == fb


def test_a_rebuild_replaces_the_old_folder(tmp_path):
    out = _build(tmp_path)
    (out / "stale.js").write_text("old", encoding="utf-8")
    _build(tmp_path)
    assert not (out / "stale.js").exists()
    assert not list(tmp_path.glob(".demo-build-*"))


SHOWCASE = REPO / "showcase" / "site" / "index.html"


def test_showcase_page_video_text():
    """The video has a captions track and a text version drawn from it, and the page links to the demo (D8)."""
    page = SHOWCASE.read_text(encoding="utf-8")
    assert '<track kind="captions" src="wider-showcase.captions.vtt"' in page
    assert "Read the video as text" in page and 'id="video-text"' in page
    assert 'href="demo/"' in page
    video = re.search(r"<video[^>]*>", page).group(0)
    assert "autoplay" not in video and 'poster="poster.jpg"' in video and "controls" in video
    # the text version stays hidden until its cues have loaded
    assert re.search(r'<details class="as-text" hidden>', page)
    # the caption says what the video is: the offline demo, invented data, a replayed recorded run
    caption = " ".join(re.search(r"<figcaption>(.*?)</figcaption>", page, re.S).group(1).split())
    assert "offline demo" in caption and "invented for this demo" in caption
    assert "recorded run of OpenAI's gpt-4o-mini" in caption and "replayed in the browser" in caption
    assert "live project" not in page and "the one in the video" not in page


def test_showcase_page_video_chapters():
    """The chapters track is drawn as buttons that seek the video, since browsers do not reliably show it (D8)."""
    page = SHOWCASE.read_text(encoding="utf-8")
    assert '<track kind="chapters" src="wider-showcase.chapters.vtt"' in page
    assert 'aria-label="Video chapters" hidden' in page and 'id="video-chapters"' in page
    assert "video.currentTime = c.startTime" in page
    assert "four-minute" not in page
