"""Cost ranges before a run and the projection while it runs (wider/core/estimate.py, design Decision 15)."""
import pytest

from tests.unit.test_screen import (
    _responder,
    project,  # noqa: F401  (the screened synthetic project fixture)
)
from wider.core import detect
from wider.core.estimate import cost_range, estimate_screen, projection, range_text
from wider.core.models import ProviderConfig
from wider.core.project import Project
from wider.providers import fake as fakemod


def test_range_priced_backend(tmp_path):
    p = Project.init(tmp_path / "r")
    cfg = ProviderConfig(name="fake", kind="fake", model="fake", price_input_per_mtok=3.0, price_cached_input_per_mtok=0.3,
                         price_cache_write_per_mtok=3.75, price_output_per_mtok=15.0)
    calls = [("S" * 4000, "U" * 400)] * 3
    r = cost_range(p, cfg, "screen", calls, max_tokens=100)
    assert r["priced"] and r["calls"] == 3 and 0 < r["min_usd"] < r["max_usd"]
    assert r["input_tokens"] == 3 * 1100 and r["output_tokens"] == 300
    # maximum: every token at the input price; minimum: one cache write, two cached reads
    assert r["max_usd"] == pytest.approx((3300 * 3.0 + 300 * 15.0) / 1e6)
    assert r["min_usd"] == pytest.approx((300 * 3.0 + 1000 * 3.75 + 2000 * 0.3 + 300 * 15.0) / 1e6)
    assert r["destination"] == "nowhere (test backend)"
    text = "\n".join(range_text(r))
    assert "Estimated cost: $" in text and "for 3 calls to nowhere (test backend)" in text
    assert "characters ÷ 4" in text and "max_tokens per call (100)" in text
    assert "cached after the first call" in text and "maximum: no caching" in text
    # caching that saves nothing (a write premium, no cheaper cached price) never lifts the minimum above the maximum
    dear = cfg.model_copy(update={"price_cached_input_per_mtok": None})
    r = cost_range(p, dear, "screen", calls, max_tokens=100)
    assert r["min_usd"] == r["max_usd"]
    p.close()


def test_range_uses_observed_usage(project, monkeypatch):  # noqa: F811
    from wider.screen.engine import screen

    counter = {"calls": 0}
    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, _responder(counter)))
    before = estimate_screen(project, "pass", scope="sample:development")
    assert any("characters ÷ 4" in a for a in before["assumptions"])
    assert any("max_tokens per call" in a for a in before["assumptions"])
    screen(project, "sample:development", pass_name="A")
    after = estimate_screen(project, "pass", scope="sample:development")
    assert any(a.startswith("input tokens from earlier calls of this backend") for a in after["assumptions"])
    assert any(a.startswith("output tokens: median of") for a in after["assumptions"])
    assert after["calls"] == before["calls"] > 0
    # the fake backend counts words, so the observed counts are far below characters ÷ 4 and max_tokens
    assert after["input_tokens"] < before["input_tokens"] and after["output_tokens"] < before["output_tokens"]
    # a protocol counts two reads of each record
    assert estimate_screen(project, "protocol", scope="sample:development")["calls"] == 2 * after["calls"]


def test_range_local_and_unpriced(tmp_path, monkeypatch):
    p = Project.init(tmp_path / "r")
    monkeypatch.setattr(detect, "http_get", lambda url, timeout=detect.HTTP_TIMEOUT_S:
                        (200, {"models": [{"name": "m"}]}) if url.endswith("/api/tags") else None)
    local = ProviderConfig(name="o", kind="ollama", model="m", base_url="http://127.0.0.1:11434")
    r = cost_range(p, local, "screen", [("sys", "user text")] * 2, max_tokens=50)
    assert r["priced"] and r["min_usd"] == r["max_usd"] == 0 and r["destination"] == "this computer"
    assert any("no cost" in a for a in r["assumptions"])
    remote = ProviderConfig(name="c", kind="openai_compat", model="m", base_url="https://api.example.org/v1")
    r = cost_range(p, remote, "screen", [("sys", "user text")], max_tokens=50)
    assert not r["priced"] and r["min_usd"] is None and "api.example.org" in r["destination"]
    assert "not priced" in range_text(r)[0] and r["input_tokens"] > 0
    p.close()


def test_projection_bounds():
    pre = {"priced": True, "calls": 10, "min_usd": 0.01, "max_usd": 0.05}
    usage = {"calls": 4, "cost_usd": 0.012, "cost_known": True}
    pr = projection(usage, 6, pre)
    assert pr["cost_so_far_usd"] == pytest.approx(0.012)
    assert pr["projected_min_usd"] == pytest.approx(0.012 + 6 * 0.001)
    assert pr["projected_max_usd"] == pytest.approx(0.012 + 6 * 0.005)
    assert projection({"calls": 4, "cost_usd": None, "cost_known": False}, 6, pre) is None
    assert projection({"calls": 0}, 6, pre) is None


def test_cached_price_alone_is_not_priced():
    """A cloud backend with only a cached-input price is "not priced", never $0 (its input and output have no price)."""
    from wider.core.usage import call_cost
    cfg = ProviderConfig(name="c", kind="openai_compat", model="m", base_url="https://api.openai.com/v1",
                         price_cached_input_per_mtok=0.5)
    assert call_cost(cfg, {"prompt_tokens": 1000, "completion_tokens": 100}) is None
    local = ProviderConfig(name="l", kind="openai_compat", model="m", base_url="http://127.0.0.1:1234/v1",
                           price_cached_input_per_mtok=0.5)
    assert call_cost(local, {"prompt_tokens": 1000, "completion_tokens": 100}) == 0.0
