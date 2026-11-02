"""Agentic CLIs (Claude Code, Codex, Gemini CLI, ...) driven one isolated process per call.

Generalises the review's second_screen.py: templated argv, the system prompt written to a temp file,
the user message on stdin, an empty working directory so no project memory leaks in, and a parser
per tool for the JSON envelope (design Decision 4).

``command`` placeholders: {model} {system_file} {schema_file} {system_toml} {system} {user}, substituted in one pass
(a substituted value is never rescanned), in the argv and in ``extra.env`` values. Without a schema, a
``{schema_file}`` token and the flag before it are dropped.
``extra.stdin``: "user" (default) sends the user message on stdin; "none" sends nothing.
``extra.output``: "claude_json" | "codex_jsonl" | "gemini_json" | "agy_json" | "text" | "last_line" | "json" (whole
stdout is JSON with a text field named by extra.text_field).
``extra.system_channel``: "file" (default) or "prompt" (the CLI has no system channel; the text travels in the prompt).
``extra.strict_schema``: write the schema in the strict form (``schema_strict.to_strict``).
``extra.auth_exit_codes`` / ``auth_patterns`` / ``auth_hint``: a sign-in failure, never retried.
``extra.fatal_exit_codes``: never retried. ``extra.retry_once_exit_codes``: run once more, then fail.
``extra.settings_file``: ``{name, env, content}`` written into the call folder and passed by its variable (a checked
usage-statistics switch, Decision 25).
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile

from ..core.usage import normalised
from .base import ChatRequest, Provider, ProviderError, schema_instruction
from .schema_strict import drop_null_optionals, to_strict

CLAUDE_BUNDLE_GLOBS = ["~/Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"]


def _version_key(path: str) -> list:
    """Natural sort key: the numbers in a path compare as numbers, so 1.10.0 is above 1.9.3."""
    import re
    return [(0, int(t)) if t.isdigit() else (1, t) for t in re.split(r"(\d+)", path)]


def bundle_matches(pattern: str, home: str | None = None, root: str | None = None, globber=glob.glob) -> list[str]:
    """Paths matching one bundle glob, highest version last. ``~`` is the home folder; an absolute pattern is put under
    ``root`` (tests use a temporary root instead of /Applications)."""
    home = home if home is not None else os.path.expanduser("~")
    if pattern.startswith("~"):
        pat = home + pattern[1:]
    elif root and os.path.isabs(pattern):
        pat = os.path.join(root, pattern.lstrip("/"))
    else:
        pat = pattern
    return sorted(globber(pat), key=_version_key)


def find_executable(name: str, bundle_globs: list[str] | None = None, *, which=shutil.which,
                    home: str | None = None, root: str | None = None, globber=glob.glob) -> str | None:
    """An absolute path as given (when it exists), else PATH, else the first bundle glob in the given order that matches,
    taking the highest version within that glob. Without ``bundle_globs``, ``claude`` keeps its desktop-app bundle."""
    if os.path.isabs(name):
        return name if os.path.exists(name) else None
    found = which(name)
    if found:
        return found
    if bundle_globs is None:
        bundle_globs = CLAUDE_BUNDLE_GLOBS if name == "claude" else []
    for pattern in bundle_globs:
        hits = bundle_matches(pattern, home=home, root=root, globber=globber)
        if hits:
            return hits[-1]
    return None


def preset_bundle_globs(cfg) -> list[str] | None:
    """The bundle globs of the backend's preset (None: none known)."""
    if not getattr(cfg, "preset", None):
        return None
    try:
        from .presets import preset
        return list(preset(cfg.preset).detect.get("bundle_globs") or []) or None
    except ValueError:
        return None


PLACEHOLDER_RE = re.compile(r"\{(model|system_file|schema_file|system_toml|system|user)\}")
_VERSIONS: dict[str, str] = {}


def _substitute(arg: str, subs: dict[str, str]) -> str:
    """One pass over the placeholders: a value that itself holds ``{user}`` or ``{model}`` is never replaced again
    (str.format would also choke on braces in the user's own arguments)."""
    return PLACEHOLDER_RE.sub(lambda m: subs.get(m.group(0), m.group(0)), arg)


def toml_basic_string(text: str) -> str:
    """``text`` as a TOML basic string: quotes and backslashes escaped, every control character but tab as \\uXXXX."""
    out = ['"']
    for ch in text:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\t":
            out.append(ch)
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\u{o:04X}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def drop_schema_tokens(argv: list[str]) -> list[str]:
    """The argv without ``{schema_file}``: a bare token goes with the flag before it, a joined one alone."""
    out: list[str] = []
    for tok in argv:
        if tok == "{schema_file}":
            if out and out[-1].startswith("-"):
                out.pop()
            continue
        if "{schema_file}" in tok:
            continue
        out.append(tok)
    return out


def tilde(text: str, home: str | None = None) -> str:
    home = (home or os.path.expanduser("~")).rstrip("/")
    return text.replace(home + "/", "~/") if home and isinstance(text, str) else text


def cli_version(exe: str | None) -> str:
    """First line of ``<exe> --version`` (cached per process; ``unknown`` on failure)."""
    if not exe:
        return "unknown"
    if exe not in _VERSIONS:
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5,
                               stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
            line = (r.stdout or r.stderr or "").strip().splitlines()
            _VERSIONS[exe] = line[0][:120] if r.returncode == 0 and line else "unknown"
        except (OSError, subprocess.SubprocessError):
            _VERSIONS[exe] = "unknown"
    return _VERSIONS[exe]


async def _stop(proc) -> None:
    """Kill and reap the child, so a timeout or a cancelled job leaves no process behind."""
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


class CliAgentProvider(Provider):
    kind = "cli_agent"

    def identity(self) -> dict:
        d = super().identity()
        home = os.path.expanduser("~")
        # every argv token with the home prefix as ~, also inside a token (--flag=/Users/x/...)
        d["command"] = [a.replace(home + "/", "~/") if a != home else "~" for a in self.cfg.command] \
            if self.cfg.command else self.cfg.command
        exe = find_executable(self.cfg.command[0], preset_bundle_globs(self.cfg)) if self.cfg.command else None
        d["executable"] = tilde(exe, home) if exe else None
        d["version"] = cli_version(exe)
        d["system_channel"] = self.cfg.extra.get("system_channel", "file")
        return d

    def _label(self) -> str:
        return os.path.basename(self.cfg.command[0]) if self.cfg.command else "the tool"

    async def _call(self, req: ChatRequest):
        if not self.cfg.command:
            raise ProviderError("cli_agent provider needs a command template")
        extra = self.cfg.extra
        retry_once = set(extra.get("retry_once_exit_codes") or [])
        for attempt in (1, 2):
            text, usage, meta, code = await self._run(req)
            if code in retry_once and attempt == 1:
                continue
            if code in retry_once:
                raise ProviderError(f"{self._label()} exit {code} twice: {meta.get('stderr_tail', '')[-200:]}")
            return text, usage, meta
        raise AssertionError("unreachable")

    async def _run(self, req: ChatRequest):
        extra = self.cfg.extra
        schema = req.schema_
        system = req.system + (schema_instruction(schema) if schema is not None else "")
        channel = extra.get("system_channel", "file")
        with tempfile.TemporaryDirectory() as empty_dir:
            sys_path = os.path.join(empty_dir, "system_prompt.md")
            with open(sys_path, "w", encoding="utf-8") as f:
                f.write(system)
            schema_path = os.path.join(empty_dir, "schema.json")
            if schema is not None:
                with open(schema_path, "w", encoding="utf-8") as f:
                    json.dump(to_strict(schema) if extra.get("strict_schema") else schema, f)
            subs = {"{model}": self.cfg.model, "{system_file}": sys_path, "{schema_file}": schema_path,
                    "{system_toml}": toml_basic_string(system), "{system}": system, "{user}": req.user}
            template = list(self.cfg.command) if schema is not None else drop_schema_tokens(list(self.cfg.command))
            argv = [_substitute(a, subs) for a in template]
            exe = find_executable(argv[0], preset_bundle_globs(self.cfg))
            if not exe:
                raise ProviderError(f"executable not found: {argv[0]}")
            env = dict(os.environ)
            env.update({k: _substitute(str(v), subs) for k, v in (extra.get("env") or {}).items()})
            settings = extra.get("settings_file")
            if isinstance(settings, dict) and settings.get("name") and settings.get("env"):
                spath = os.path.join(empty_dir, os.path.basename(str(settings["name"])))
                with open(spath, "w", encoding="utf-8") as f:
                    json.dump(settings.get("content") or {}, f)
                env[str(settings["env"])] = spath
            stdin_mode = extra.get("stdin", "user")
            stdin_text = req.user
            uses_system = any(t in a for a in self.cfg.command for t in ("{system}", "{system_file}", "{system_toml}"))
            if channel == "prompt" and not uses_system and "GEMINI_SYSTEM_MD" not in (extra.get("env") or {}):
                stdin_text = system + "\n\n---\n\n" + req.user
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    exe, *argv[1:], cwd=empty_dir, env=env,
                    stdin=asyncio.subprocess.PIPE if stdin_mode == "user" else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(
                    proc.communicate(stdin_text.encode("utf-8") if stdin_mode == "user" else None),
                    timeout=self.cfg.timeout_s)
            except TimeoutError:
                await _stop(proc)
                raise ProviderError(f"{self._label()} did not answer within {self.cfg.timeout_s} s",
                                    retryable=True) from None
            except asyncio.CancelledError:
                await _stop(proc)
                raise
        stdout = out.decode("utf-8", errors="replace")
        stderr = err.decode("utf-8", errors="replace")
        code = proc.returncode
        meta_base = {"returncode": code, "stderr_tail": stderr[-300:]}
        self._check_exit(code, stdout, stderr)
        if code in set(extra.get("retry_once_exit_codes") or []):
            return "", {}, meta_base, code
        if code != 0 and not stdout.strip():
            raise ProviderError(f"{self._label()} exit {code}: {stderr[-400:]}", retryable=True)
        try:
            text, usage, meta = self._parse(stdout)
        except ProviderError:
            if code != 0:
                self._check_auth_text(stdout, stderr)
            raise
        if schema is not None and extra.get("strict_schema"):
            text = _drop_nulls_in_text(text, schema)
        meta.update(meta_base)
        return text, usage, meta, code

    # ----- sign-in and exit codes -----
    def _auth_error(self) -> ProviderError:
        hint = self.cfg.extra.get("auth_hint") or f"{self._label()} is not signed in: sign in to it in a terminal"
        return ProviderError(hint)

    def _check_auth_text(self, stdout: str, stderr: str) -> None:
        pats = [str(x).lower() for x in self.cfg.extra.get("auth_patterns") or []]
        blob = (stderr + "\n" + stdout).lower()
        if pats and any(x in blob for x in pats):
            raise self._auth_error()

    def _check_exit(self, code: int, stdout: str, stderr: str) -> None:
        extra = self.cfg.extra
        if code in set(extra.get("auth_exit_codes") or []):
            raise self._auth_error()
        if code != 0:
            self._check_auth_text(stdout, stderr)
        if code in set(extra.get("fatal_exit_codes") or []):
            raise ProviderError(f"{self._label()} exit {code}: {(stderr or stdout)[-300:]}")

    # ----- parsers -----
    def _parse(self, stdout: str):
        mode = self.cfg.extra.get("output", "text")
        if mode == "claude_json":
            return _parse_claude(stdout)
        if mode == "codex_jsonl":
            return parse_codex_jsonl(stdout)
        if mode == "gemini_json":
            return parse_gemini_json(stdout)
        if mode == "agy_json":
            return parse_agy_json(stdout)
        if mode == "json":
            env = _json_object(stdout, "tool did not return JSON")
            field = self.cfg.extra.get("text_field", "result")
            return str(env.get(field, "")), normalised(raw=env.get("usage") or {}), {}
        if mode == "last_line":
            lines = [ln for ln in stdout.splitlines() if ln.strip()]
            return (lines[-1] if lines else ""), normalised(), {}
        return stdout, normalised(), {}


def _drop_nulls_in_text(text: str, schema: dict) -> str:
    from .structured import extract_json

    parsed, _ = extract_json(text)
    if parsed is None:
        return text
    return json.dumps(drop_null_optionals(parsed, schema), ensure_ascii=False)


def _json_object(stdout: str, what: str) -> dict:
    try:
        env = json.loads(stdout)
    except json.JSONDecodeError:
        raise ProviderError(what, retryable=True) from None
    if not isinstance(env, dict):
        raise ProviderError(what, retryable=True)
    return env


def _parse_claude(stdout: str):
    from .anthropic_sdk import usage_anthropic

    env = _json_object(stdout, "claude did not return a JSON envelope")
    if env.get("is_error"):
        raise ProviderError(f"claude error: {str(env.get('result'))[:300]}", retryable=True)
    usage = usage_anthropic(env.get("usage") or {})
    if env.get("total_cost_usd") is not None:
        usage["total_cost_usd"] = env.get("total_cost_usd")
    models = sorted((env.get("modelUsage") or {}).keys())
    return (env.get("result") or ""), usage, {"session_id": env.get("session_id"), "models": models,
                                             "modelUsage": env.get("modelUsage")}


def parse_codex_jsonl(stdout: str):
    """Codex ``exec --json`` events: the last completed ``agent_message`` is the answer; ``turn.completed.usage`` holds
    input (cached included), cached and output tokens (field names UNVERIFIED, open question 3)."""
    text, usage_raw, models = None, None, []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            text = (ev.get("item") or {}).get("text") or ""
        elif t == "turn.completed":
            usage_raw = ev.get("usage") or {}
        elif t == "turn.failed":
            raise ProviderError(f"codex: {((ev.get('error') or {}).get('message') or 'turn failed')[:300]}",
                                retryable=True)
        elif t == "error":
            raise ProviderError(f"codex: {str(ev.get('message') or 'error')[:300]}", retryable=True)
        if ev.get("model") and ev["model"] not in models:
            models.append(ev["model"])
    if text is None:
        raise ProviderError("codex returned no answer", retryable=True)
    u = usage_raw or {}
    prompt, cached = u.get("input_tokens"), u.get("cached_input_tokens")
    unc = None if prompt is None else int(prompt) - int(cached or 0)
    return text, normalised(input_uncached=unc, input_cached=cached, output=u.get("output_tokens"), raw=u), \
        {"models": models}


def parse_gemini_json(stdout: str):
    """Gemini CLI ``--output-format json``: ``response``, ``error``, usage summed over ``stats.models.*.tokens``."""
    env = _json_object(stdout, "gemini did not return JSON")
    if env.get("error"):
        err = env["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise ProviderError(f"gemini: {str(msg)[:300]}", retryable=True)
    models = (env.get("stats") or {}).get("models") or {}
    prompt = cached = out = 0
    seen = False
    for m in models.values():
        tok = (m or {}).get("tokens") or {}
        if tok:
            seen = True
        prompt += int(tok.get("prompt") or 0)
        cached += int(tok.get("cached") or 0)
        out += int(tok.get("candidates") or 0) + int(tok.get("thoughts") or 0)
    usage = normalised(input_uncached=prompt - cached, input_cached=cached, output=out,
                       raw=env.get("stats") or {}) if seen else normalised(raw=env.get("stats") or {})
    return str(env.get("response") or ""), usage, {"models": sorted(models)}


def parse_agy_json(stdout: str):
    """Antigravity CLI JSON: ``structured_output`` else ``response``; ``status`` other than SUCCESS is an error."""
    env = _json_object(stdout, "agy did not return JSON")
    status = env.get("status")
    if env.get("error") or (status is not None and status != "SUCCESS"):
        raise ProviderError(f"agy: {str(env.get('error') or status)[:300]}", retryable=True)
    so = env.get("structured_output")
    text = json.dumps(so, ensure_ascii=False) if so is not None else str(env.get("response") or "")
    u = env.get("usage") if isinstance(env.get("usage"), dict) else env
    usage = normalised(input_uncached=u.get("input_tokens"), input_cached=u.get("cache_read_tokens"),
                       output=(None if u.get("output_tokens") is None else
                               int(u.get("output_tokens") or 0) + int(u.get("thinking_tokens") or 0)),
                       raw={k: u.get(k) for k in ("input_tokens", "output_tokens", "thinking_tokens",
                                                  "cache_read_tokens") if k in u})
    return text, usage, {"models": [env["model"]] if env.get("model") else []}
