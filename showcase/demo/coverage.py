#!/usr/bin/env python3
"""Which `/api/` calls of the interface the interactive demo answers (design D9).

``calls(static_dir)`` reads every `wider/ui/static/*.js`, finds each string or template literal holding `/api/`, and
returns the set of ``"METHOD path"`` keys: the path without its query, a template's `${...}` parts written `{x}`, the
method from a `method: "..."` inside the same call (GET when there is none; `Jobs.start(...)` is a POST).

``uncovered(static_dir, coverage, recorded)`` lists the keys with no answer: not recorded at any checkpoint (only
GETs are recorded, plus the paths listed under ``"record_posts"``), not simulated by a handler and not named in
``"not_in_demo"``. ``coverage`` is `showcase/demo/coverage.json`; ``recorded`` the recorded ``"METHOD path?query"``
keys of every checkpoint.

    python3 showcase/demo/coverage.py            # prints the uncovered calls (exit 1 when there are any)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
UI = REPO / "wider" / "ui"
STATIC = UI / "static"
COVERAGE = HERE / "coverage.json"
SNAPSHOT = HERE / "snapshot"


def _literals(src: str):
    """(start, end, text) of every string and template literal, a template's `${...}` parts written `{x}`."""
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'":
            j, out = i + 1, []
            while j < n and src[j] != c and src[j] != "\n":
                if src[j] == "\\":
                    out.append(src[j:j + 2])
                    j += 2
                    continue
                out.append(src[j])
                j += 1
            yield i, j + 1, "".join(out)
            i = j + 1
            continue
        if c == "`":
            j, out = _template(src, i + 1)
            yield i, j, out
            i = j
            continue
        i += 1


def _template(src: str, j: int) -> tuple[int, str]:
    """Read a template literal from just after its backtick; returns the index after the closing backtick and the
    text with each `${...}` written `{x}`."""
    out = []
    while j < len(src) and src[j] != "`":
        if src[j] == "\\":
            out.append(src[j:j + 2])
            j += 2
        elif src.startswith("${", j):
            j = _skip_expr(src, j + 2)
            out.append("{x}")
        else:
            out.append(src[j])
            j += 1
    return j + 1, "".join(out)


def _skip_expr(src: str, j: int) -> int:
    """Skip a `${...}` expression (nested braces, strings and templates); returns the index after its `}`."""
    depth = 1
    while j < len(src) and depth:
        c = src[j]
        if c in "\"'":
            k = j + 1
            while k < len(src) and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        if c == "`":
            j, _ = _template(src, j + 1)
            continue
        depth += {"{": 1, "}": -1}.get(c, 0)
        j += 1
    return j


def _call_args(src: str, end: int) -> str:
    """The rest of the call the literal ending at ``end`` is an argument of, up to its closing parenthesis."""
    depth, j = 1, end
    while j < len(src) and depth and j - end < 600:
        c = src[j]
        if c in "\"'`":
            k = j + 1
            while k < len(src) and src[k] != c:
                k += 2 if src[k] == "\\" else 1
            j = k + 1
            continue
        depth += {"(": 1, ")": -1}.get(c, 0)
        j += 1
    return src[end:j]


def normalise(text: str) -> str:
    """The path of a literal: no query, a template's trailing `{x}` that only adds a query dropped."""
    path = text.split("?", 1)[0]
    path = re.sub(r"(?<=[^/])\{x\}$", "", path)
    return path


def server_routes(ui_dir: Path = UI) -> list[tuple[str, str]]:
    """(METHOD, path) of every `/api/` route the server declares (`@app.get("/api/...")` and the like)."""
    out = []
    for f in sorted(ui_dir.glob("*.py")):
        for m in re.finditer(r"@\w+\.(get|post|put|patch|delete)\(\s*\"(/api/[^\"]*)\"", f.read_text(encoding="utf-8")):
            out.append((m.group(1).upper(), m.group(2)))
    return out


def _route_rx(path: str) -> re.Pattern:
    """A server path (`/api/records/{record_id}`, `{version:int}`) as a pattern over client paths, where a client's
    `{x}` stands for any one part."""
    body = "".join("(?:[^/]+)" if re.fullmatch(r"\{[^}]+\}", part) else
                   "".join("[^/]*" if q == "{x}" else re.escape(q) for q in re.split(r"(\{x\})", part))
                   for part in re.split(r"(\{[^}]+\})", path))
    return re.compile(rf"^{body}$")


def _matches(client: str, route: str) -> bool:
    """True when the client path (parts written `{x}` may be anything) can reach the server route."""
    c, r = client.strip("/").split("/"), route.strip("/").split("/")
    if len(c) != len(r):
        return False
    for a, b in zip(c, r):
        if a == b or re.fullmatch(r"\{[^}]+\}", b):
            continue
        if "{x}" in a and re.fullmatch("".join(".*" if q == "{x}" else re.escape(q) for q in re.split(r"(\{x\})", a)), b):
            continue
        return False
    return True


def calls(static_dir: Path = STATIC, ui_dir: Path = UI) -> dict[str, list[str]]:
    """``"METHOD path"`` -> the places (`file:line`) it is called from. The path and the method are the server's
    route; a client path that ends in `/` (a name appended later) stands for one more part. A client path the server
    has no route for is listed as a GET of the client's own path, so it is never missed."""
    routes = server_routes(ui_dir)
    out: dict[str, list[str]] = {}
    for f in sorted(static_dir.glob("*.js")):
        src = f.read_text(encoding="utf-8")
        for start, _end, text in _literals(src):
            if not text.startswith("/api/") or text == "/api/":
                continue
            path = normalise(text)
            if path.endswith("/"):
                path += "{x}"
            line = src.count("\n", 0, start) + 1
            hits = [(m, r) for m, r in routes if _matches(path, r)] or [("GET", path)]
            for m, r in hits:
                out.setdefault(f"{m} {r}", []).append(f"{f.name}:{line}")
    return out


def _key_rx(entry: str) -> re.Pattern:
    """An entry of coverage.json ("METHOD /api/a/{name}/b") as a pattern over call keys and recorded keys."""
    method, path = entry.split(" ", 1)
    body = "".join("[^/?]+" if re.fullmatch(r"\{[^}]+\}", part) else re.escape(part)
                   for part in re.split(r"(\{[^}]+\})", path))
    return re.compile(rf"^{method} {body}(?:\?.*)?$")


def uncovered(static_dir: Path, coverage: dict, recorded: set[str]) -> dict[str, list[str]]:
    """The calls with no recorded answer, no handler and no not-in-demo reason, with the places they come from."""
    named = [_key_rx(e) for e in list(coverage.get("handlers", [])) + list(coverage.get("not_in_demo", {}))]
    out = {}
    for key, where in calls(static_dir).items():
        concrete = key.replace("{x}", "x")
        if any(rx.match(concrete) or rx.match(key) for rx in named):
            continue
        rx = _key_rx(key)
        if any(rx.match(r) for r in recorded):
            continue
        out[key] = where
    return out


def recorded_keys(snapshot: Path = SNAPSHOT) -> set[str]:
    keys: set[str] = set()
    for f in sorted(snapshot.glob("checkpoint-*.json")):
        keys |= set(json.loads(f.read_text())["answers"])
    return keys


def main() -> None:
    coverage = json.loads(COVERAGE.read_text()) if COVERAGE.exists() else {}
    if "--all" in sys.argv:
        for k, where in sorted(calls().items(), key=lambda kv: kv[0].split(" ", 1)[1]):
            print(f"{k}    {', '.join(where)}")
        return
    left = uncovered(STATIC, coverage, recorded_keys())
    for k, where in sorted(left.items()):
        print(f"{k}    {', '.join(where)}")
    sys.exit(1 if left else 0)


if __name__ == "__main__":
    main()
