"""Shared test helpers: online-lookup consent and offline HTTP clients (nothing here touches the network)."""
from __future__ import annotations

from collections.abc import Callable

import httpx

from wider.core import lookups
from wider.core.project import Project


def consent(project: Project) -> Project:
    """Allow online lookups for a test project (as `wider lookups allow` would)."""
    lookups.allow(project, author="test")
    return project


def mock_client(handler: Callable[[httpx.Request], httpx.Response] | dict[str, object] | None = None,
                calls: list[httpx.Request] | None = None) -> httpx.Client:
    """An ``httpx.Client`` on a ``MockTransport``. ``handler`` is a function of the request, or a dict mapping a URL
    substring to a JSON body (or an ``int`` status, or an exception instance to raise); unmatched requests get 404.
    Each request is appended to ``calls`` when given. Without a handler every request fails the test."""

    def route(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if handler is None:
            raise AssertionError(f"unexpected outside request to {request.url.host}")
        if callable(handler):
            return handler(request)
        url = str(request.url)
        for key, out in handler.items():
            if key in url:
                if isinstance(out, BaseException):
                    raise out
                if isinstance(out, int):
                    return httpx.Response(out)
                return httpx.Response(200, json=out)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(route))


def failing_client(calls: list[httpx.Request] | None = None) -> httpx.Client:
    """A client that fails the test on any request (for checks that nothing is sent)."""
    return mock_client(None, calls)


def no_requests(monkeypatch, calls: list[str] | None = None) -> list[str]:
    """Fail on any outgoing request, 127.0.0.1 included: the detection helper, both httpx transports, urllib and raw
    sockets. The in-process TestClient transport is not touched. Returns the list of attempted targets."""
    import socket
    import urllib.request

    from wider.core import detect

    seen = [] if calls is None else calls

    def refuse(what):
        def _f(*a, **k):
            seen.append(what)
            raise AssertionError(f"a request was made ({what})")
        return _f

    monkeypatch.setattr(detect, "http_get", refuse("detect.http_get"))
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse("httpx"))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse("httpx async"))
    monkeypatch.setattr(urllib.request, "urlopen", refuse("urllib"))
    monkeypatch.setattr(socket.socket, "connect", refuse("socket"))
    detect._ROUTING_CACHE.clear()
    return seen
