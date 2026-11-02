"""Plain CLI output in every test run: Typer forces a colour terminal when GITHUB_ACTIONS, FORCE_COLOR or PY_COLORS is
set (GitHub Actions sets the first), and Rich colours on FORCE_COLOR, which puts escape codes into the text the tests
read."""
import os

for name in ("FORCE_COLOR", "PY_COLORS"):
    os.environ.pop(name, None)
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
os.environ.pop("OPENALEX_API_KEY", None)   # a key in the developer's shell would be sent by every mocked lookup

import typer.rich_utils  # noqa: E402

typer.rich_utils.FORCE_TERMINAL = False   # in case Typer was imported before this file

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _wide_console():
    """CLI tables are read whole: rich keeps one shared console with the width it first saw, which under pytest-xdist
    (no terminal) is 80 columns and depends on which test printed first, so every test starts from a wide one."""
    import rich

    rich.get_console().width = 1000


@pytest.fixture(autouse=True)
def _no_loopback_servers(monkeypatch):
    """Tests never reach a real server on this computer: the detection and Ollama-routing GET answers nothing unless a
    test injects its own, and the routing cache starts empty."""
    from wider.core import detect

    monkeypatch.setattr(detect, "http_get", lambda url, timeout=detect.HTTP_TIMEOUT_S: None)
    detect._ROUTING_CACHE.clear()


@pytest.fixture(autouse=True)
def _fresh_inferred():
    """Names a test took from the environment are not carried into the next test (the contextvar lives in the test
    process's main context; a CLI command or a request resets it itself)."""
    from wider.core import journal

    token = journal.INFERRED.set(None)
    yield
    journal.INFERRED.reset(token)
