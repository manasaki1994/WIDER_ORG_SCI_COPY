"""The tool gate shared by every run that sends research text to a CLI agent (change 2c, D15): seed mining's model
pass and the PRESS second reader. A ``cli_agent`` backend may be used only when its command holds every argv run that
disables its tool's tools (its built-in presets carry them); an edited command or an unknown tool is refused."""
from __future__ import annotations

import re

# the argv runs that disable an agent CLI's tools (its built-in presets carry them, 2b D11)
TOOL_OFF_FLAGS: dict[str, list[tuple[str, ...]]] = {
    "claude": [("--tools", ""), ("--strict-mcp-config",)],
    "codex": [("--sandbox", "read-only"), ("-c", "features.shell_tool=false"), ("-c", 'web_search="disabled"')],
    "gemini": [("--approval-mode", "plan"), ("-e", "none")],
}

# flags that hand tools back to the agent whatever else the command says (risk check of 2c, R4)
TOOL_ON_FLAGS: dict[str, set[str]] = {
    "claude": {"--mcp-config", "--allowedTools", "--allowed-tools", "--dangerously-skip-permissions"},
    "codex": {"--dangerously-bypass-approvals-and-sandbox", "--full-auto"},
    "gemini": {"-y", "--yolo", "--allowed-tools", "--allowed-mcp-server-names"},
}
EMPTY = '""'
_ALIAS = {"codex": {"-s": "--sandbox", "--config": "-c"}, "gemini": {"--extensions": "-e"}}


def _exe(argv: list[str]) -> str:
    return re.sub(r"\.exe$", "", (argv[0] if argv else "").replace("\\", "/").rsplit("/", 1)[-1])


def _norm(exe: str, argv: list[str]) -> list[str]:
    """``--flag=value`` split in two and short aliases spelled one way, so an override cannot hide in another form."""
    out: list[str] = []
    for a in argv:
        if a.startswith("--") and "=" in a:
            out += a.split("=", 1)
        else:
            out.append(a)
    alias = _ALIAS.get(exe, {})
    return [alias.get(a, a) for a in out]


def _undone(argv: list[str], run: tuple[str, ...]) -> bool:
    """Whether the command also gives ``run``'s flag another value (later flags win in these CLIs). For codex's
    ``-c key=value`` only the same key counts."""
    if len(run) != 2:
        return False
    flag, val = run
    key = val.split("=", 1)[0].strip() if flag == "-c" else None
    for i, a in enumerate(argv[:-1]):
        if a != flag:
            continue
        v = argv[i + 1]
        if key is not None and v.split("=", 1)[0].strip() != key:
            continue
        if v.replace(" ", "") != val.replace(" ", ""):
            return True
    return False


def _has_run(argv: list[str], run: tuple[str, ...]) -> bool:
    n = len(run)
    return any(tuple(argv[i:i + n]) == run for i in range(len(argv) - n + 1))


def missing_tool_flags(command: list[str] | None) -> tuple[str, list[str]]:
    """``(executable, flags missing)`` for a CLI backend's command; ``flags missing`` is ``["(unknown tool)"]`` for an
    executable without known tool-disabling flags."""
    argv = [str(a) for a in (command or [])]
    exe = _exe(argv)
    runs = TOOL_OFF_FLAGS.get(exe)
    if runs is None:
        return exe or "(no command)", ["(unknown tool)"]
    argv = _norm(exe, argv)
    return exe, [" ".join(f'"{a}"' if a == "" else a for a in r) for r in runs if not _has_run(argv, r)]


def tool_on_flags(command: list[str] | None) -> list[str]:
    """The flags in a CLI backend's command that turn its tools back on: a known tool-on flag, or a tool-off flag
    given again with another value."""
    argv = [str(a) for a in (command or [])]
    exe = _exe(argv)
    argv = _norm(exe, argv)
    out = sorted({a for a in argv if a in TOOL_ON_FLAGS.get(exe, set())})
    return out + [f"{r[0]} with another value than {r[1] or EMPTY}" for r in TOOL_OFF_FLAGS.get(exe, [])
                  if _undone(argv, r)]


def tool_gate(cfg) -> str | None:
    """Why ``cfg`` may not be given research text, or ``None``: only a ``cli_agent`` whose command lacks its tool's
    tool-off flags (or runs a tool WIDER knows no such flags for) is refused. The command is read, not the preset
    label. The callers add what follows from it (mining skips, the second reader stops)."""
    if cfg.kind != "cli_agent":
        return None
    exe, missing = missing_tool_flags(cfg.command)
    if missing == ["(unknown tool)"]:
        return (f"the backend {cfg.name} runs {exe}, for which WIDER knows no command-line flags that disable the "
                "agent's tools")
    if missing:
        return f"the command of the backend {cfg.name} lacks {', '.join(missing)}, which disable the agent's tools"
    on = tool_on_flags(cfg.command)
    if on:
        return f"the command of the backend {cfg.name} turns the agent's tools back on ({'; '.join(on)})"
    return None
