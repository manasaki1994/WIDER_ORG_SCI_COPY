"""Which `/api/` calls the interactive demo answers (openspec change interactive-demo, design D9)."""
import importlib.util
import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("demo_coverage", REPO / "showcase" / "demo" / "coverage.py")
cov = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cov)


def _static_copy(tmp_path):
    static = tmp_path / "static"
    shutil.copytree(cov.STATIC, static, ignore=shutil.ignore_patterns("*.woff2", "*.png", "*.svg"))
    return static


def test_coverage_lists_uncovered_paths(tmp_path):
    static = _static_copy(tmp_path)
    (static / "planted.js").write_text('call("/api/new/thing");\n', encoding="utf-8")
    left = cov.uncovered(static, {}, set())
    assert left["GET /api/new/thing"] == ["planted.js:1"]
    # a path that has an answer or a reason is not listed
    assert "GET /api/new/thing" not in cov.uncovered(static, {"not_in_demo": {"GET /api/new/thing": "why"}}, set())
    assert "GET /api/new/thing" not in cov.uncovered(static, {}, {"GET /api/new/thing?a=1"})


def test_coverage_is_keyed_by_method(tmp_path):
    static = _static_copy(tmp_path)
    left = cov.uncovered(static, {}, {"GET /api/outputs/methods"})
    assert "GET /api/outputs/methods" not in left
    assert "POST /api/outputs/methods" in left
    assert any(w.startswith("outputs.js:") for w in left["POST /api/outputs/methods"])


def test_every_call_of_the_app_is_listed():
    """Each client call maps to a server route (or is kept as its own GET); none is lost."""
    found = cov.calls()
    assert "GET /api/home" in found and "POST /api/outputs/methods" in found
    routes = {f"{m} {p}" for m, p in cov.server_routes()}
    assert routes, "the server declares its routes with @app.<method>(\"/api/...\")"
    stray = [k for k in found if k not in routes]
    assert stray == [], f"client calls with no server route: {stray}"


def test_exports_name_why_they_are_not_in_the_demo():
    """The replication package and the other exports say what WIDER would do instead."""
    reasons = json.loads((REPO / "showcase" / "demo" / "coverage.json").read_text())["not_in_demo"]
    for key in ("POST /api/outputs/package", "POST /api/outputs/references", "POST /api/report/bundle"):
        assert reasons[key] == "This export is not part of the demo. In WIDER it writes a file into your project folder."
