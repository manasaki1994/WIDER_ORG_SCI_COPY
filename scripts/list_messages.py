#!/usr/bin/env python3
"""List every message WIDER shows to a researcher, where it is seen and which word-list rules it breaks.

Spec: `openspec/specs/message-inventory/spec.md`. Reads only WIDER's own source files under `wider/` and the word
list in `docs/guide-text.md`; opens no project, contacts nothing, writes only the file named by `--out`.

    .venv/bin/python scripts/list_messages.py [--out FILE] [--summary] [--root DIR]
"""

from __future__ import annotations

import argparse
import ast
import bisect
import html
import inspect
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # the hint is printed by main()
    yaml = None

KINDS = ("UI text", "UI dialog", "toast", "API error", "result text", "CLI output", "CLI help", "stage text",
         "catalogue text")
EVERY, CALLER, UNKNOWN, CLI = "*every", "*caller", "*unknown", "*cli"
SPECIAL_TITLES = {EVERY: "every page", CALLER: "any page that calls this", UNKNOWN: "page unknown", CLI: "CLI only"}
FIXED_PAGES = {"home": "Your projects", "model": "Model for query creation", "questions": "Questions",
               "notes": "Notes and tags", "history": "History", "help": "Help", "settings": "Settings"}
HEADER = "# Messages WIDER shows (scripts/list_messages.py; the timestamp flag is a heuristic)"
MAX_TEXT = 200


@dataclass
class Message:
    text: str
    file: str  # relative to the root
    line: int
    kind: str
    pages: set
    checks: list = field(default_factory=list)  # start of the source form; the named line holds one of them
    exprs: list = field(default_factory=list)  # (last name, source) of each interpolated part
    flags: list = field(default_factory=list)
    route: str | None = None  # a route's path; its pages come from the scripts that call it
    in_fold: bool = False  # inside a closed "Command line" fold (`details.cmdline`, `cmdFold(...)`)


class Inventory(list):
    """The messages, plus what the count table needs besides them."""

    unresolved = 0
    titles: dict = {}
    order: list = []


class NotACheckout(Exception):
    pass


def fold(s: str) -> str:
    return " ".join(s.split())


def has_letter(text: str) -> bool:
    return bool(re.search(r"[A-Za-z]", re.sub(r"\{[^{}]*\}", "", text)))


def prose(text: str) -> bool:
    """A letter outside `{...}`, and words with a space between them or a closing `.`, `?` or `!`."""
    t = text.strip()
    return has_letter(t) and (bool(re.search(r"\S\s+\S", t)) or t.endswith((".", "?", "!")))


def check_of(raw: str, cut: str) -> str:
    """The first 20 characters of a source form, cut at the first template part (decision 9)."""
    first = raw.split(cut)[0] if cut in raw else raw
    if not fold(first.split("\n")[0]):
        first = raw
    return fold(first.split("\n")[0])[:20]


class Lines:
    def __init__(self, src: str):
        self.starts = [0] + [m.end() for m in re.finditer("\n", src)]

    def of(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


# ---------------------------------------------------------------- word list and flags

def read_word_list(path: Path):
    """The terms and plain forms of the table under "## Word list"; None when there is no table."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## Word list")), None)
    if start is None:
        return None
    rows, in_table = [], False
    for ln in lines[start + 1:]:
        if ln.strip().startswith("|"):
            in_table = True
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 2:
                rows.append(cells)
        elif in_table:
            break
    rows = [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r if c)]
    if not rows:
        return None

    def forms(cell):
        prev = None
        while prev != cell:
            prev, cell = cell, re.sub(r"\([^()]*\)", "", cell)
        out = []
        for part in cell.split(","):
            p = part.strip().strip("`\"'").casefold()
            if p and p not in out:
                out.append(p)
        return out

    terms, plain = [], []
    for r in rows:
        terms += [t for t in forms(r[0]) if t not in terms]
        plain += [p for p in forms(r[1]) if p not in plain]
    return terms, plain


def whole(word: str) -> str:
    return r"(?<!\w)" + re.escape(word) + r"(?!\w)"


def flags_of(m: Message, terms, plain, when_formats: set) -> list:
    flags = []
    t = re.sub(r"\{[^{}]*\}", " ", m.text).casefold()
    for p in sorted(plain, key=len, reverse=True):
        t = re.sub(whole(p), " ", t)
    flags += [f"word list: {term}" for term in terms if re.search(whole(term), t)]
    shown = re.sub(r"\{[^{}]*\}", " ", m.text)
    if re.search(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b", shown):
        flags.append("snake_case")
    if re.search(r"\bwider (\{|[a-z][a-z-]+)", m.text) or re.search(r"`[^`]+`", m.text):
        flags.append("command")
    stamp = bool(re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d", m.text))
    for last, src in m.exprs:   # a time name (`at`, `*_at`, `as_of`, `created`) shown raw or cut to its date or minute
        if last in ("at", "as_of", "created") or last.endswith("_at"):
            if re.match(r"\s*!?\s*[\w.]+\s*\?", src) and src.count(last) == 1:
                continue   # only tested (`c.finished_at ? "" : " (not finished)"`), never shown
            formatted = ("fmtWhen(" in src or "toLocale" in src or "strftime" in src or ":%" in src
                         or (m.file in when_formats and re.search(r"\bwhen\(", src)))
            cut = re.search(r"\.slice\(\s*0\s*,\s*1[06]\s*\)", src) and "fmtWhen(" not in src
            stamp = stamp or not formatted or bool(cut)
    if stamp:
        flags.append("timestamp")
    if m.in_fold:
        flags.append("cmdline fold")
    return flags


# ---------------------------------------------------------------- JS: a small tokenizer (decision 2)

REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "yield",
               "await"}
CALLEE_RE = re.compile(r"([A-Za-z_$][\w$]*(?:\s*\??\.\s*[A-Za-z_$][\w$]*)*)\s*$")
CONST_RE = re.compile(r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*$")


@dataclass
class Literal:
    start: int
    end: int = 0
    parts: list = field(default_factory=list)  # ("t", raw, offset) or ("e", expr, offset)
    frames: list = field(default_factory=list)
    top: str = ""


class JSScan:
    """Walks a script once and yields its string and template literals with the brackets around them."""

    def __init__(self, src: str):
        self.s, self.i, self.n = src, 0, len(src)
        self.frames: list = []
        self.lits: list = []
        self.top = ""

    def scan(self) -> list:
        self.code(False)
        return self.lits

    def regex_ok(self) -> bool:
        s, j = self.s, self.i - 1
        while j >= 0 and s[j] in " \t\r\n":
            j -= 1
        if j < 0:
            return True
        c = s[j]
        if c in "(,=:!&|?[;{}+-*%<>~^":
            return True
        if c.isalnum() or c in "_$":
            m = re.search(r"[\w$]+$", s[max(0, j - 20):j + 1])
            return bool(m) and m.group() in REGEX_WORDS
        return False

    def skip_regex(self):
        s, i, in_class = self.s, self.i + 1, False
        while i < self.n:
            c = s[i]
            if c == "\\":
                i += 2
                continue
            if c == "\n":
                break
            if in_class:
                in_class = c != "]"
            elif c == "[":
                in_class = True
            elif c == "/":
                i += 1
                break
            i += 1
        self.i = i

    def new_literal(self) -> Literal:
        lit = Literal(self.i, frames=[tuple(f) for f in self.frames], top=self.top)
        self.lits.append(lit)
        return lit

    def string(self):
        s, q = self.s, self.s[self.i]
        lit = self.new_literal()
        i = self.i + 1
        while i < self.n and s[i] != q and s[i] != "\n":
            i += 2 if s[i] == "\\" else 1
        lit.parts = [("t", s[self.i + 1:i], self.i + 1)]
        self.i = lit.end = min(i + 1, self.n)

    def template(self):
        s = self.s
        lit = self.new_literal()
        self.i += 1
        seg = self.i
        while self.i < self.n:
            c = s[self.i]
            if c == "\\":
                self.i += 2
                continue
            if c == "`":
                lit.parts.append(("t", s[seg:self.i], seg))
                self.i += 1
                break
            if s.startswith("${", self.i):
                lit.parts.append(("t", s[seg:self.i], seg))
                self.i += 2
                ex = self.i
                self.frames.append(["${", None, 0])
                self.code(True)
                self.frames.pop()
                lit.parts.append(("e", s[ex:self.i], ex))
                self.i += 1
                seg = self.i
                continue
            self.i += 1
        lit.end = self.i

    def code(self, in_expr: bool):
        s, base = self.s, len(self.frames)
        while self.i < self.n:
            c = s[self.i]
            if c in " \t\r\n":
                self.i += 1
                continue
            if not self.frames and (self.i == 0 or s[self.i - 1] == "\n") and (c.isalpha() or c in "_$("):
                end = s.find("\n", self.i)
                self.top = s[self.i:end if end >= 0 else self.n]
            if s.startswith("//", self.i):
                end = s.find("\n", self.i)
                self.i = end if end >= 0 else self.n
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                self.i = end + 2 if end >= 0 else self.n
            elif c == "/":
                if self.regex_ok():
                    self.skip_regex()
                else:
                    self.i += 1
            elif c in "\"'":
                self.string()
            elif c == "`":
                self.template()
            elif c == "(":
                m = CALLEE_RE.search(s, max(0, self.i - 120), self.i)
                self.frames.append(["(", re.sub(r"\s|\?", "", m.group(1)) if m else None, 0])
                self.i += 1
            elif c == "{":
                m = CONST_RE.search(s, max(0, self.i - 120), self.i)
                self.frames.append(["{", m.group(1) if m else None, 0])
                self.i += 1
            elif c == "[":
                self.frames.append(["[", None, 0])
                self.i += 1
            elif c in ")]}":
                if len(self.frames) <= base:
                    if in_expr and c == "}":
                        return
                else:
                    self.frames.pop()
                self.i += 1
            elif c == ",":
                if self.frames and self.frames[-1][0] == "(":
                    self.frames[-1][2] += 1
                self.i += 1
            else:
                m = re.compile(r"[\w$]+").match(s, self.i)
                self.i = m.end() if m else self.i + 1


JS_KEYWORDS = {"true", "false", "null", "undefined", "this", "new", "typeof", "return", "await", "async",
               "function", "const", "let", "var", "of", "in", "instanceof", "void", "if", "else"}


def js_lastname(expr: str) -> str:
    chars = list(expr)
    for lit in JSScan(expr).scan():
        chars[lit.start:lit.end] = " " * (lit.end - lit.start)
    t = "".join(chars)
    names = [m.group() for m in re.finditer(r"(?<![\w$])[A-Za-z_$][\w$]*", t)
             if m.group() not in JS_KEYWORDS and not re.match(r"\s*\(", t[m.end():])]
    return names[-1] if names else "..."


ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def decode_js(raw: str, off: int):
    """The characters a JS literal part stands for, each with the source offset it came from."""
    i, out = 0, []
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            n, at = raw[i + 1], off + i
            if n == "\n":
                i += 2
                continue
            try:
                if n == "u" and raw[i + 2:i + 3] == "{":
                    j = raw.index("}", i)
                    ch, i = chr(int(raw[i + 3:j], 16)), j + 1
                elif n == "u":
                    ch, i = chr(int(raw[i + 2:i + 6], 16)), i + 6
                elif n == "x":
                    ch, i = chr(int(raw[i + 2:i + 4], 16)), i + 4
                else:
                    ch, i = ESCAPES.get(n, n), i + 2
            except ValueError:
                ch, i = n, i + 2
            if "\udc00" <= ch <= "\udfff" and out and "\ud800" <= out[-1][0] <= "\udbff":  # a pair, as \uD83D\uDE00
                hi, at = out.pop()
                ch = chr(0x10000 + ((ord(hi) - 0xD800) << 10) + ord(ch) - 0xDC00)
            out.append((ch, at))
        else:
            out.append((c, off + i))
            i += 1
    return [("\ufffd" if "\ud800" <= ch <= "\udfff" else ch, at) for ch, at in out]  # a lone half


SKIP_CALLS = {"querySelector", "querySelectorAll", "closest", "getElementById", "matches", "addEventListener",
              "removeEventListener", "getAttribute", "hasAttribute", "removeAttribute", "toggleAttribute", "$", "$$",
              "createElement", "includes", "startsWith", "endsWith", "indexOf", "lastIndexOf", "split", "join",
              "replace", "replaceAll", "match", "test", "getItem", "setItem", "removeItem", "add", "remove",
              "toggle", "contains", "localeCompare", "padStart", "padEnd", "dispatchEvent", "Event", "CustomEvent",
              "RegExp", "getPropertyValue", "insertAdjacentHTML_position"}
SKIP_OBJECTS = ("console", "classList", "localStorage", "sessionStorage", "dataset", "history", "location")
TAG_RE = re.compile(r"<(/?)([A-Za-z][\w-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
ATTR_RE = re.compile(r"(?<![\w-])(title|placeholder|aria-label|alt)\s*=\s*(\"([^\"]*)\"|'([^']*)')")
SENTINEL = re.compile("\x00(\\d+)\x00")
MAP_NAME = re.compile(r"(LABEL|WORD|CHIP|TIP)$")

# Pages of each script: (before the first marker, {start of the marker's words: pages}); "*" matches any marker.
PART_MAP = {
    "app.js": ({EVERY}, {"stage results": {EVERY}, "construct": {"construct"}, "query": {"query"},
                         "import + coverage": {"import"}, "dedup": {"dedup"}, "enrichment": {"enrich"},
                         "ranking": {"rank"}, "sampling + labelling": {"sample"}, "screening": {"screen"},
                         "recall + report": {"recall"}}),
    "shell.js": ({EVERY}, {"status words": {EVERY}, "sidebar": {EVERY}, "top bar": {EVERY}, "explain this step": {EVERY},
                           "home page": {"home"}, "help page": {"help"}, "settings page": {"settings"}}),
    "setup.js": ({"setup"}, {"seed papers": {"setup"}, "databases": {"setup"}, "prior reviews": {"setup"},
                             "advanced: review protocol": {"settings"}}),
    "outputs.js": ({"recall"}, {"search-strings archive": {"query"}, "recall page outputs": {"recall"}}),
    "pickers.js": ({"model", "rank", "screen"}, {"llm picker": {"screen"}, "embedding picker": {"rank"}}),
    "model.js": ({"model"}, {"the backend form": {"model", "settings"}, "the page": {"model"}}),
    "backends.js": ({"setup", "settings", "screen", "recall"}, {"backend setup": {"setup", "settings", "model"}}),
    "questions.js": ({"questions"}, {"questions page": {"questions"}, "ask mode dialog": {EVERY}}),
    "drawer.js": ({EVERY}, {"*": {EVERY}}),
    "records.js": ({EVERY}, {"*": {EVERY}}),
    "dedup.js": ({"dedup"}, {"*": {"dedup"}}),
    "common.js": ({EVERY}, {"*": {EVERY}}),
    "jobs.js": ({EVERY}, {"*": {EVERY}}),
    "history.js": ({"history", EVERY}, {}),
    "import.js": ({"import"}, {}),
    "adjacent.js": ({"construct"}, {}),
}
FUNC_MAP = {
    "app.js": dict.fromkeys(("loadProject", "refreshInterview", "interviewSteps", "renderInterview",
                             "renderSummary"), {"setup"}),
    "adjacent.js": {"loadAdjacentCases": {"screen"}},
}
MARKER_RE = re.compile(r"^\s*//\s*-{16,}\s*([A-Za-z].*?)[\s-]*$")


def part_pages(name: str, src: str):
    """A function from a line number to the pages of the script part holding it."""
    if name not in PART_MAP:
        return lambda line: {UNKNOWN}
    before, parts = PART_MAP[name]
    marks = []
    for no, ln in enumerate(src.splitlines(), 1):
        m = MARKER_RE.match(ln)
        if m:
            words = m.group(1).casefold()
            hits = [k for k in parts if k == "*" or words.startswith(k)]
            best = max(hits, key=lambda k: (k != "*", len(k)), default=None)
            marks.append((no, parts[best] if best else {UNKNOWN}))
    lines = [no for no, _ in marks]

    def pages(line):
        k = bisect.bisect_left(lines, line) - 1
        return set(marks[k][1]) if k >= 0 else set(before)
    return pages


def override_pages(name: str, top: str, ids: dict):
    m = re.match(r"(?:async\s+)?function\s*\*?\s*([\w$]+)", top)
    if m and m.group(1) in FUNC_MAP.get(name, {}):
        return set(FUNC_MAP[name][m.group(1)])
    m = re.match(r"\$\(\s*[\"'`]#([\w-]+)[\"'`]\s*\)\s*\.\s*(on\w+|addEventListener)", top)
    if m and m.group(1) in ids:
        return {ids[m.group(1)]}
    if re.match(r"loadStages\(|function\s+boot\b", top):
        return {EVERY}
    return None


def read_js(root: Path, rel: str, ids: dict, out: list, calls: list):
    src = (root / rel).read_text(encoding="utf-8")
    name = Path(rel).name
    lines, pages_at = Lines(src), part_pages(name, src)
    for lit in JSScan(src).scan():
        chars, pos, exprs = [], [], []
        for typ, val, off in lit.parts:
            if typ == "t":
                for ch, at in decode_js(val, off):
                    chars.append(ch)
                    pos.append(at)
            else:
                token = f"\x00{len(exprs)}\x00"
                exprs.append((js_lastname(val), val))
                chars += token
                pos += [off] * len(token)
        plain = "".join(chars)
        line = lines.of(lit.start)
        pages = override_pages(name, lit.top, ids) or pages_at(line)
        if plain.startswith("/api/"):
            path = re.split(r"[\x00{?]", plain, maxsplit=1)[0].rstrip("/")
            calls.append((path, pages))
            continue
        if plain.lstrip().startswith(("#", ".", "http")) or re.match(r"[?&][\w.-]+=", plain):
            continue  # a selector, a link or a query piece of a route
        frames = lit.frames
        top = frames[-1] if frames else None
        if top and top[0] == "(" and top[1]:
            parts = top[1].split(".")
            last = parts[-1]
            if (last in SKIP_CALLS or any(p in SKIP_OBJECTS for p in parts[:-1])
                    or (last == "setAttribute" and top[2] == 0)
                    or (last in ("get", "has", "delete") and parts[:-1] and top[2] == 0)):
                continue
        before = src[max(0, lit.start - 80):lit.start]
        after = src[lit.end:lit.end + 8]
        if (re.search(r"[=!]=\s*$", before) or re.match(r"\s*[=!]==?", after)
                or re.search(r"\bcase\s*$", before)):
            continue
        if re.search(r"\.(className|id|href|src|type|htmlFor|name|rel|target|method)\s*=\s*$", before):
            continue
        kind, forced = "UI text", False
        for f in reversed(frames):
            if f[0] == "{":
                break
            if f[0] == "(" and f[1]:
                head = f[1].split(".")
                if head[-1] == "toast":
                    kind, forced = ("toast", True) if f[2] == 0 else ("UI text", False)
                    break
                # the in-page dialog helpers (accessibility-and-naming D1) are collected as the browser boxes were
                if head[-1] in ("confirm", "alert", "prompt", "askConfirm", "askFields", "askChoice") and head[:-1] in ([], ["window"]):
                    kind, forced = ("UI dialog", True) if f[2] == 0 else ("UI text", False)
                    break
                if head == ["flash"]:  # flash(el, msg, bad): the status line next to a form
                    forced = f[2] == 1
                    break

        in_fold = any(f[0] == "(" and f[1] == "cmdFold" for f in frames)   # a card's command (C6 D5)

        def emit(start, end, text_kind, keep):
            seg = plain[start:end]
            k = len(seg) - len(seg.lstrip())
            if start + k >= len(pos):
                return
            text = fold(html.unescape(SENTINEL.sub(lambda mm: "{" + exprs[int(mm.group(1))][0] + "}", seg)))
            if not has_letter(text) or not (keep or prose(text)):
                return
            at = pos[start + k]
            used = [exprs[int(i)] for i in SENTINEL.findall(seg)]
            out.append(Message(text, rel, lines.of(at), text_kind, set(pages),
                               checks=[check_of(src[at:at + 300], "${")], exprs=used, in_fold=in_fold))

        if TAG_RE.search(plain):
            last_end, skip = 0, None
            for m in TAG_RE.finditer(plain):
                if skip is None:
                    emit(last_end, m.start(), kind, True)
                closing, tag = m.group(1), m.group(2).lower()
                if skip is None and not closing and tag in ("script", "style", "svg") and not m.group(3).endswith("/"):
                    skip = tag
                elif skip == tag and closing:
                    skip = None
                if skip is None:
                    for a in ATTR_RE.finditer(m.group(3)):
                        g = 3 if a.group(3) is not None else 4
                        emit(m.start(3) + a.start(g), m.start(3) + a.end(g), "UI text", True)
                last_end = m.end()
            if skip is None:
                emit(last_end, len(plain), kind, True)
            continue
        keep = forced
        if re.search(r"\b(?:textContent|innerText|title|placeholder|alt|ariaLabel)\s*\+?=\s*$", before):
            keep = True
        if re.search(r"\b(?:label|title|placeholder|text)\s*:\s*$", before):
            keep = True
        if top and top[0] == "{" and top[1] and MAP_NAME.search(top[1]) and re.search(r":\s*$", before):
            keep = True
        ctx = []
        for f in reversed(frames):
            ctx.append(f[0])
            if f[0] == "{":
                break
        if "${" in ctx and re.search(r"[?:|]\s*$", before):
            keep = True
        emit(0, len(plain), kind, keep)


# ---------------------------------------------------------------- index.html (decision 3)

class HTMLReader:
    ATTRS = ("title", "placeholder", "aria-label", "alt")

    def __init__(self, rel: str, src: str):
        from html.parser import HTMLParser

        reader = self

        class P(HTMLParser):
            def handle_starttag(self, tag, attrs):
                reader.start(self, tag, attrs, closed=False)

            def handle_startendtag(self, tag, attrs):
                reader.start(self, tag, attrs, closed=True)

            def handle_endtag(self, tag):
                reader.end(tag)

            def handle_data(self, data):
                reader.data(self.getpos(), data)

            def handle_entityref(self, name):
                reader.data(self.getpos(), f"&{name};")

            def handle_charref(self, name):
                reader.data(self.getpos(), f"&#{name};")

            def handle_comment(self, data):
                reader.flush()

        self.rel, self.src = rel, src
        self.sections: list = []
        self.details: list = []  # one entry per open <details>: True for a "Command line" fold
        self.skip = 0
        self.buf: list = []
        self.ids: dict = {}
        self.out: list = []
        p = P(convert_charrefs=False)
        p.feed(src)
        p.close()
        self.flush()

    def page(self):
        return self.sections[-1] if self.sections else EVERY

    def add(self, text, line, raw):
        text = fold(html.unescape(text))
        if has_letter(text):
            self.out.append(Message(text, self.rel, line, "UI text", {self.page()}, checks=[check_of(raw, "${")],
                                    in_fold=any(self.details)))

    def flush(self):
        if self.buf:
            (line, _), raw = self.buf[0][0], "".join(r for _, r in self.buf)
            k = len(raw) - len(raw.lstrip())
            if not self.skip:
                self.add(raw, line + raw[:k].count("\n"), raw[k:])
        self.buf = []

    def data(self, pos, raw):
        self.buf.append((pos, raw))

    def start(self, parser, tag, attrs, closed):
        self.flush()
        if tag in ("script", "style", "svg") and not closed:
            self.skip += 1
        a = dict(attrs)
        if tag == "section" and not closed:
            sid = a.get("id") or ""
            self.sections.append(sid[5:] if sid.startswith("page-") else self.page())
        if tag == "details" and not closed:
            self.details.append("cmdline" in (a.get("class") or "").split())
        if a.get("id"):
            self.ids[a["id"]] = self.page()
        if self.skip:
            return
        line, text = parser.getpos()[0], parser.get_starttag_text() or ""
        for name in self.ATTRS:
            value = a.get(name)
            m = re.search(r"(?<![\w-])" + re.escape(name) + r"\s*=\s*[\"']?", text)
            if value and m:
                self.add(value, line + text[:m.end()].count("\n"), text[m.end():])

    def end(self, tag):
        self.flush()
        if tag in ("script", "style", "svg") and self.skip:
            self.skip -= 1
        if tag == "section" and self.sections:
            self.sections.pop()
        if tag == "details" and self.details:
            self.details.pop()


# ---------------------------------------------------------------- Python (decision 1)

NAMED_FILES = {"wider/search/lint.py": {"query"}, "wider/ingest/sanity.py": {"import"},
               "wider/providers/presets.py": {"model", "setup", "settings"},
               "wider/screen/result_sets.py": {"screen", "recall"}, "wider/prior/chain.py": {"setup"}}
SUMMARIES = "wider/core/stage_summaries.py"
SUMMARY_MAP = {"import": {"import"}, "upload": {"import"}, "dedup": {"dedup"}, "enrich": {"enrich"},
               "embed": {"rank"}, "rank": {"rank"}, "samples": {"sample"}, "screen": {"screen"},
               "recall": {"recall"}, "report": {"recall"}, "bundle": {"recall"},
               "artifact": {"construct", "query"}, "chain": {"setup"}}
SKIP_MODULES = {"logging", "log", "logger", "_log", "LOG", "re", "os", "json", "sys", "subprocess", "shutil",
                "sqlite3", "warnings", "hashlib", "importlib", "platform", "shlex", "datetime"}
SKIP_FUNCS = {"open", "Path", "getattr", "setattr", "hasattr", "isinstance", "PurePath", "compile", "bytes",
              "TypeVar", "NewType", "namedtuple"}
SKIP_METHODS = {"split", "rsplit", "replace", "strip", "lstrip", "rstrip", "join", "encode", "decode", "execute",
                "executemany", "executescript", "strftime", "strptime", "fromisoformat", "compile", "match",
                "search", "sub", "fullmatch", "findall", "finditer", "glob", "rglob", "read_text", "write_text",
                "startswith", "endswith", "partition", "rpartition", "count", "find", "index", "with_suffix",
                "joinpath", "debug", "info", "warning", "exception", "add_argument", "removeprefix",
                "removesuffix", "translate", "maketrans", "format_map"}
KEY_METHODS = {"get", "pop", "setdefault", "getenv", "__getitem__"}
SQL_RE = re.compile(r"\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH|PRAGMA|BEGIN|COMMIT|REPLACE)\b")
RICH_RE = re.compile(r"\[/?(?:(?:bold|dim|italic|underline|strike|reverse|blink|red|green|yellow|blue|magenta|"
                     r"cyan|white|black|grey\d*|gray\d*|bright_\w+|link|not|on)\b[^\[\]]*)?\]")


def dotted(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = dotted(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return ""


def py_names(n) -> list:
    if isinstance(n, ast.Name):
        return [n.id]
    if isinstance(n, ast.Attribute):
        return py_names(n.value) + [n.attr]
    if isinstance(n, ast.Subscript):
        out = py_names(n.value)
        if isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
            out.append(n.slice.value)
        return out
    if isinstance(n, ast.Call):
        out = py_names(n.func.value) if isinstance(n.func, ast.Attribute) else []
        for a in n.args:
            out += py_names(a)
        for k in n.keywords:
            out += py_names(k.value)
        return out
    out = []
    for ch in ast.iter_child_nodes(n):
        out += py_names(ch)
    return out


def py_lastname(n) -> str:
    names = py_names(n)
    return names[-1] if names else "..."


def is_str(n) -> bool:
    return isinstance(n, ast.Constant) and isinstance(n.value, str)


def joined(n: ast.JoinedStr):
    text, exprs = [], []
    for v in n.values:
        if is_str(v):
            text.append(v.value)
        elif isinstance(v, ast.FormattedValue):
            last = py_lastname(v.value)
            spec = ast.unparse(v.format_spec) if v.format_spec is not None else ""
            exprs.append((last, ast.unparse(v.value) + (":%" if "%" in spec else "")))
            text.append("{" + last + "}")
    return "".join(text), exprs


def add_leaves(n) -> list:
    if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
        return add_leaves(n.left) + add_leaves(n.right)
    return [n]


def py_texts(e) -> list:
    """(text, node giving the line, interpolated parts) for each message an expression stands for."""
    if is_str(e):
        return [(e.value, e, [])]
    if isinstance(e, ast.JoinedStr):
        t, ex = joined(e)
        return [(t, e, ex)]
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add):
        leaves = add_leaves(e)
        if not any(is_str(x) or isinstance(x, ast.JoinedStr) for x in leaves):
            return []
        text, exprs = [], []
        for x in leaves:
            sub = py_texts(x) if isinstance(x, (ast.Constant, ast.JoinedStr, ast.BoolOp, ast.IfExp)) else []
            if sub:
                text.append(sub[0][0])
                exprs += sub[0][2]
            else:
                last = py_lastname(x)
                exprs.append((last, ast.unparse(x)))
                text.append("{" + last + "}")
        return [("".join(text), leaves[0], exprs)]
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Mod) and (is_str(e.left)
                                                                   or isinstance(e.left, ast.JoinedStr)):
        return py_texts(e.left)
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "format":
        return py_texts(e.func.value)
    if isinstance(e, ast.BoolOp):
        return [t for v in e.values for t in py_texts(v)]
    if isinstance(e, ast.IfExp):
        return py_texts(e.body) + py_texts(e.orelse)
    return []


def route_of(dec) -> str | None:
    if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
            and dec.func.attr in ("get", "post", "put", "delete", "patch") and dec.args and is_str(dec.args[0])
            and dec.args[0].value.startswith("/api")):
        return re.split(r"[{?]", dec.args[0].value, maxsplit=1)[0].rstrip("/") or "/"
    return None


class PyReader:
    def __init__(self, root: Path, rel: str, out: list):
        self.rel, self.out = rel, out
        self.src = (root / rel).read_text(encoding="utf-8")
        self.cli = bool(re.fullmatch(r"wider/cli[^/]*\.py", rel))
        self.prose_file = (rel in NAMED_FILES or re.fullmatch(r"wider/(ui|core)/[^/]+\.py", rel) is not None)
        try:
            tree = ast.parse(self.src)
        except SyntaxError:
            return
        self.parent, self.route, self.summ = {}, {}, {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                self.parent[id(ch)] = node
        for node in ast.walk(tree):  # outer functions first, so an inner one overwrites
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                path = next((p for p in map(route_of, node.decorator_list) if p), None)
                for n in ast.walk(node):
                    if path:
                        self.route[id(n)] = path
                    if rel == SUMMARIES and node.name.startswith("summarize_") and node in tree.body:
                        self.summ[id(n)] = node.name[len("summarize_"):]
        self.consumed: set = set()
        self.pass_constructs(tree)
        if self.prose_file:
            self.pass_prose(tree)

    def pages(self, node, raise_=False):
        if self.cli:
            return {CLI}, None
        if self.rel in NAMED_FILES:
            return set(NAMED_FILES[self.rel]), None
        if id(node) in self.route:
            return set(), self.route[id(node)]
        if self.rel == SUMMARIES and self.summ.get(id(node)) in SUMMARY_MAP:
            return set(SUMMARY_MAP[self.summ[id(node)]]), None
        if self.rel == "wider/core/summary.py":
            return {"home"}, None
        if self.rel == "wider/ui/jobs.py":
            return {EVERY}, None
        if raise_ and not self.rel.startswith("wider/ui/"):
            return {CALLER}, None
        return {UNKNOWN}, None

    def consume(self, node):
        for n in ast.walk(node):
            self.consumed.add(id(n))

    def emit(self, expr, kind, pages=None, raise_=False, markup=False, keep=True, at=None):
        if expr is None:
            return
        for text, node, exprs in py_texts(expr):
            if markup:
                text = RICH_RE.sub("", text)
            text = fold(text)
            if not has_letter(text) or not (keep or prose(text)):
                continue
            pg, route = (set(pages), None) if pages is not None else self.pages(at or node, raise_)
            seg = ast.get_source_segment(self.src, node) or ""
            self.out.append(Message(text, self.rel, node.lineno, kind, pg, checks=[check_of(seg, "{")],
                                    exprs=exprs, route=route))
        self.consume(expr)

    def pass_constructs(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
                if dotted(call.func).split(".")[-1] == "HTTPException":
                    continue
                msg = call.args[0] if call.args else next(
                    (k.value for k in call.keywords if k.arg in ("message", "detail", "msg")), None)
                self.emit(msg, "CLI output" if self.cli else "API error", raise_=True, markup=self.cli,
                          pages={CLI} if self.cli else None)
                self.consume(call)
            if not isinstance(node, ast.Call):
                if (self.cli and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and any(dotted(d).split(".")[-1] in ("command", "callback") for d in node.decorator_list)):
                    doc = node.body[0] if node.body else None
                    if isinstance(doc, ast.Expr) and is_str(doc.value):
                        text = fold(inspect.cleandoc(doc.value.value))
                        if has_letter(text):
                            seg = ast.get_source_segment(self.src, doc.value) or ""
                            self.out.append(Message(text, self.rel, doc.value.lineno, "CLI help", {CLI},
                                                    checks=[check_of(seg, "{")]))
                        self.consume(doc)
                if (self.rel == SUMMARIES and id(node) in self.summ and isinstance(node, (ast.Assign, ast.AugAssign))):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for t in targets:
                        if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                                and t.value.id == "metrics"):
                            self.emit(t.slice, "result text")
                if self.rel == SUMMARIES and id(node) in self.summ and isinstance(node, ast.Dict):
                    for k in node.keys:
                        if k is not None:
                            self.emit(k, "result text")
                continue
            name = dotted(node.func)
            last = name.split(".")[-1]
            if last == "HTTPException":
                msg = node.args[1] if len(node.args) > 1 else next(
                    (k.value for k in node.keywords if k.arg == "detail"), None)
                self.emit(msg, "API error")
                self.consume(node)
            elif last == "raise_question":
                kw = {k.arg: k.value for k in node.keywords}
                pages = {CLI} if self.cli else {"questions"}
                self.emit(kw.get("title", node.args[3] if len(node.args) > 3 else None), "result text", pages)
                self.emit(kw.get("prompt", node.args[4] if len(node.args) > 4 else None), "result text", pages)
                self.consume(node)
            elif last == "progress" and (isinstance(node.func, ast.Name) or name.endswith(".progress")):
                for a in node.args[2:]:
                    self.emit(a, "result text", {CLI} if self.cli else {EVERY})
                for a in node.args:
                    self.consume(a)
            elif self.cli:
                obj = name.rsplit(".", 1)[0] if "." in name else ""
                if last in ("print", "rprint", "echo", "secho") and (not obj or last != "print"
                                                                     or obj.endswith("console")):
                    for a in node.args:
                        self.emit(a, "CLI output", {CLI}, markup=True)
                elif last in ("confirm", "prompt") and obj.endswith("typer") and node.args:
                    self.emit(node.args[0], "CLI output", {CLI}, markup=True)
                elif last in ("add_column", "Column") and node.args:
                    self.emit(node.args[0], "CLI output", {CLI}, markup=True)
                elif last == "Table":
                    for a in node.args:
                        self.emit(a, "CLI output", {CLI}, markup=True)
                    for k in node.keywords:
                        if k.arg == "title":
                            self.emit(k.value, "CLI output", {CLI}, markup=True)
                for k in node.keywords:
                    if k.arg == "help":
                        self.emit(k.value, "CLI help", {CLI}, markup=True)

    def skipped(self, node) -> bool:
        p = self.parent.get(id(node))
        if isinstance(p, ast.Expr):
            return True
        if isinstance(p, ast.Dict) and any(k is node for k in p.keys):
            return True
        if isinstance(p, ast.Subscript) and p.slice is node:
            return True
        if isinstance(p, ast.Compare) or (isinstance(p, (ast.Tuple, ast.List, ast.Set))
                                          and isinstance(self.parent.get(id(p)), ast.Compare)):
            return True
        if isinstance(p, ast.Attribute) and p.attr != "format":
            return True
        call, arg = (self.parent.get(id(p)), p) if isinstance(p, ast.keyword) else (p, node)
        if isinstance(call, ast.Call) and call.func is not arg:
            name = dotted(call.func)
            head, last = name.split(".")[:-1], name.split(".")[-1]
            if any(h in SKIP_MODULES for h in head) or (not head and last in SKIP_FUNCS):
                return True
            if head and last in SKIP_METHODS:
                return True
            if last in KEY_METHODS and call.args and call.args[0] is arg:
                return True
        return False

    def pass_prose(self, tree):
        for node in ast.walk(tree):
            if id(node) in self.consumed:
                continue
            cand = (is_str(node) or isinstance(node, ast.JoinedStr)
                    or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)
                        and not (isinstance(self.parent.get(id(node)), ast.BinOp)
                                 and isinstance(self.parent[id(node)].op, ast.Add))
                        and any(is_str(x) or isinstance(x, ast.JoinedStr) for x in add_leaves(node)))
                    or (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and is_str(node.left)))
            if not cand:
                continue
            if self.skipped(node) or any(SQL_RE.match(t) for t, _, _ in py_texts(node)):
                self.consume(node)
                continue
            self.emit(node, "result text", keep=False)


# ---------------------------------------------------------------- YAML: stages and catalogues

def ymap(node) -> dict:
    return {k.value: v for k, v in node.value} if isinstance(node, yaml.MappingNode) else {}


def yaml_message(out, rel, key, node, kind, pages, prefix=""):
    if isinstance(node, yaml.ScalarNode):
        value = str(node.value)
        text, first = prefix + value, value
    elif isinstance(node, yaml.SequenceNode):
        items = [str(x.value) for x in node.value if isinstance(x, yaml.ScalarNode)]
        text, first = ", ".join(items), items[0] if items else ""
    else:
        return
    text = fold(text)
    if has_letter(text):
        out.append(Message(text, rel, node.start_mark.line + 1, kind, set(pages),
                           checks=[key, fold(first.split("\n")[0])[:20]]))


def read_stages(root: Path, out: list) -> list:
    rel = "wider/stages.yaml"
    doc = ymap(yaml.compose((root / rel).read_text(encoding="utf-8")))
    stages = []
    for sec in getattr(doc.get("sections"), "value", []) or []:
        title = ymap(sec).get("title")
        if title is not None:
            yaml_message(out, rel, "title", title, "stage text", {EVERY})
    for st in getattr(doc.get("stages"), "value", []) or []:
        m = ymap(st)
        key = str(getattr(m.get("key"), "value", ""))
        ext = str(getattr(m.get("status"), "value", "")) == "extension"
        pages = {"help"} if ext else {key, "help"}
        if not ext and key:
            stages.append((key, str(getattr(m.get("title"), "value", key))))
        for f in ("title", "short", "description", "why", "command", "produces"):
            if f in m:
                yaml_message(out, rel, f, m[f], "stage text", pages, prefix="wider " if f == "command" else "")
    return stages


def read_catalogues(root: Path, out: list):
    rel = "wider/providers/presets.yaml"
    if (root / rel).exists():
        doc = ymap(yaml.compose((root / rel).read_text(encoding="utf-8")))
        pages = {"model", "setup", "settings"}
        for entry in getattr(doc.get("presets"), "value", []) or []:
            m = ymap(entry)
            for f in ("label", "vendor", "billing"):
                if f in m:
                    yaml_message(out, rel, f, m[f], "catalogue text", pages)
            hint = ymap(ymap(m.get("defaults")).get("extra")).get("auth_hint")
            if hint is not None:
                yaml_message(out, rel, "auth_hint", hint, "catalogue text", pages)
            note = ymap(m.get("telemetry")).get("note")
            if note is not None:
                yaml_message(out, rel, "note", note, "catalogue text", pages)
    rel = "wider/search/platforms.yaml"
    if (root / rel).exists():
        doc = ymap(yaml.compose((root / rel).read_text(encoding="utf-8")))
        for _, entry in getattr(doc.get("platforms"), "value", []) or []:
            m = ymap(entry)
            for f in ("label", "description", "coverage", "cap_note", "paste"):
                if f in m:
                    yaml_message(out, rel, f, m[f], "catalogue text", {"query"} if f == "paste" else {"setup"})


# ---------------------------------------------------------------- collecting

def rel_of(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def collect(root) -> Inventory:
    root = Path(root)
    if not (root / "wider/stages.yaml").is_file() or not (root / "wider/ui/static/index.html").is_file():
        raise NotACheckout("no wider/stages.yaml or wider/ui/static/index.html")
    words = read_word_list(root / "docs/guide-text.md")
    if words is None:
        raise NotACheckout("docs/guide-text.md has no word-list table")
    raw: list = []
    stages = read_stages(root, raw)
    read_catalogues(root, raw)
    index = HTMLReader("wider/ui/static/index.html", (root / "wider/ui/static/index.html").read_text(encoding="utf-8"))
    raw += index.out
    calls: list = []
    static = root / "wider/ui/static"
    when_formats = set()
    for p in sorted(static.glob("*.js")):
        rel = rel_of(root, p)
        if re.search(r"const\s+when\s*=.*(toLocale|fmtWhen\()", p.read_text(encoding="utf-8")):
            when_formats.add(rel)
        read_js(root, rel, index.ids, raw, calls)
    for p in sorted((root / "wider").rglob("*.py")):
        PyReader(root, rel_of(root, p), raw)

    def matches(a, b):
        short, long_ = sorted((a, b), key=len)
        return long_ == short or long_.startswith(short + "/")

    for m in raw:
        if m.route is not None:
            m.pages = set().union(*[pg for path, pg in calls if path != "/api" and matches(path, m.route)]) \
                or {UNKNOWN}
    merged: dict = {}
    for m in raw:
        key = (m.file, m.line, m.kind, m.text)
        if key in merged:
            merged[key].pages |= m.pages
            merged[key].exprs += [e for e in m.exprs if e not in merged[key].exprs]
            merged[key].checks += [c for c in m.checks if c not in merged[key].checks]
        else:
            merged[key] = m
    inv = Inventory(sorted(merged.values(), key=lambda m: (m.file, m.line, m.kind, m.text)))
    terms, plain = words
    for m in inv:
        m.flags = flags_of(m, terms, plain, when_formats)
    inv.unresolved = sum(1 for path, _ in calls if path == "/api")
    inv.titles = {**FIXED_PAGES, **dict(stages), **SPECIAL_TITLES}
    keys = {k for m in inv for k in m.pages}
    known = ["home", "model"] + [k for k, _ in stages] + ["questions", "notes", "history", "help", "settings"]
    specials = list(SPECIAL_TITLES)
    inv.order = known + sorted(keys - set(known) - set(specials)) + specials
    return inv


# ---------------------------------------------------------------- output (decision 6)

def render(inv: Inventory, summary: bool = False) -> str:
    rows = [("total", "messages", len(inv))]
    rows += [("kind", k, sum(1 for m in inv if m.kind == k)) for k in KINDS]
    for key in inv.order:
        n = sum(1 for m in inv if key in m.pages)
        if n:
            rows.append(("page", inv.titles.get(key, key), n))
    flags = sorted({f for m in inv for f in m.flags})
    rows += [("flag", f, sum(1 for m in inv if f in m.flags)) for f in flags]
    rows.append(("UI", "seen in the UI with a flag", sum(
        1 for m in inv if m.flags and m.kind not in ("CLI output", "CLI help") and m.pages != {CLI})))
    rows.append(("routes", "route calls unresolved", inv.unresolved))
    out = [HEADER, "", "| Group | Name | Messages |", "|---|---|---|"]
    out += [f"| {g} | {n} | {c} |" for g, n, c in rows]
    out.append("")
    if summary:
        return "\n".join(out)
    for key in inv.order:
        msgs = [m for m in inv if key in m.pages]
        if not msgs:
            continue
        out += [f"## {inv.titles.get(key, key)}", ""]
        for m in msgs:
            text = m.text if len(m.text) <= MAX_TEXT else m.text[:MAX_TEXT - 1] + "…"
            tail = f" · {', '.join(m.flags)}" if m.flags else ""
            out.append(f'- "{text}" — `{m.file}:{m.line}` · {m.kind}{tail}')
        out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List every message WIDER shows, where it is seen and its flags.")
    ap.add_argument("--out", help="write the list to this file instead of standard output")
    ap.add_argument("--summary", action="store_true", help="write the count table only")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent),
                    help="the WIDER checkout to read (default: the folder above scripts/)")
    a = ap.parse_args(argv)
    if yaml is None:
        print("list_messages: PyYAML is missing; run it with .venv/bin/python scripts/list_messages.py",
              file=sys.stderr)
        return 2
    try:
        inv = collect(Path(a.root))
    except NotACheckout as e:
        print(f"list_messages: {a.root} is not a WIDER checkout ({e})", file=sys.stderr)
        return 2
    data = render(inv, a.summary).encode("utf-8", errors="replace")  # encoded before the file is opened
    if a.out:
        Path(a.out).write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
