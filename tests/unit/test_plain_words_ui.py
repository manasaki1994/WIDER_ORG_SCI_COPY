"""Plain words in the page text (ui-redesign D32, UX review 30 Oct, finding F11; PRODUCT.md "Plain words": no file
names or commands in UI text).

The visible text of index.html and every prose string the page scripts render is checked against a list of internal
words (backend, artifact, YAML and .yaml/.md file names, $WIDER variables, command-line options, fake, ledger, oracle,
linter, sha256, absolute paths). Exempt: text inside a "Command line" disclosure (<details class="cmdline">, the CLI
twin of a page), <script>/<style>, tag names and non-visible attributes (ids, classes, data-*), comments, and strings
with no space (keys, ids, API paths). A word that must stay is on ALLOWED below, with the reason.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"

INTERNAL = re.compile(
    r"backend|artifact|\byaml\b|\.yaml\b|\.md\b|\$WIDER|(?:^|\s)--[a-z]|\bfake\b|ledger|\boracle\b|\blinter\b|sha256"
    r"|/Users/|~/|\b[A-Z]:\\[A-Za-z]|Setup page",
    re.I)

# (file or "*", a regex the flagged text must match, reason). Keep it short; every entry needs a reason.
ALLOWED = [
    # Home's Info table names its rows as `wider info` prints them (CLI/UI parity, test_shell_js_node).
    ("shell.js", r"^backend check$", "the Info row matches `wider info` (parity)"),
    # The construct version list says what `wider construct list` prints (CLI/UI parity, test_construct.py).
    ("app.js", r"sha256", "the version list matches the CLI's version list (parity)"),
    # A CLI line a page shows inside its "Command line" disclosure, built in the script and set into a <code> there.
    ("*", r"^\s*wider [a-z]", "the text of a Command line disclosure (CLI twin), which keeps its commands"),
]

VISIBLE_ATTRS = ("title", "placeholder", "aria-label", "alt", "aria-description")
VOID = {"input", "br", "img", "meta", "link", "hr", "source", "col", "wbr", "area", "base", "embed", "track"}


class _Visible(HTMLParser):
    """The visible text pieces of an HTML fragment, skipping script/style and Command line disclosures."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skip, self.pieces = [], 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        sk = tag in ("script", "style", "template") or (
            tag == "details" and "cmdline" in (a.get("class") or "").split())
        if not self.skip:
            for k in VISIBLE_ATTRS:
                if a.get(k):
                    self.pieces.append((self.getpos()[0], a[k]))
        if tag not in VOID:
            self.stack.append((tag, sk))
            self.skip += sk

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self.stack:
            self.skip -= self.stack.pop()[1]

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        # close up to the matching tag (tolerates fragments that open a tag in another string)
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                for _, sk in self.stack[i:]:
                    self.skip -= sk
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.pieces.append((self.getpos()[0], data))


def visible_pieces(html):
    p = _Visible()
    p.feed(html)
    p.close()
    return p.pieces


_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {""}


def js_strings(src):
    """Every string and template literal of a script as (line, text), comments skipped; a template's ${...} parts
    become a space and their own literals are listed separately."""
    out = []

    def scan(i, stop_at_brace):
        depth = 0
        prev = ""
        n = len(src)
        while i < n:
            c = src[i]
            if c in " \t\r\n":
                i += 1
                continue
            if src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "'\"":
                j, buf = i + 1, []
                while j < n and src[j] != c:
                    if src[j] == "\\":
                        buf.append(src[j:j + 2])
                        j += 2
                        continue
                    if src[j] == "\n":
                        break
                    buf.append(src[j])
                    j += 1
                out.append((src.count("\n", 0, i) + 1, "".join(buf)))
                i, prev = j + 1, "x"
                continue
            if c == "`":
                j, buf, line = i + 1, [], src.count("\n", 0, i) + 1
                while j < n and src[j] != "`":
                    if src[j] == "\\":
                        buf.append(src[j:j + 2])
                        j += 2
                        continue
                    if src.startswith("${", j):
                        buf.append(" ")
                        j = scan(j + 2, True)
                        continue
                    buf.append(src[j])
                    j += 1
                out.append((line, "".join(buf)))
                i, prev = j + 1, "x"
                continue
            if c == "/" and prev in _REGEX_PREV:
                j, in_class = i + 1, False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        break
                    j += 1
                i, prev = j + 1, "x"
                continue
            if stop_at_brace:
                if c == "{":
                    depth += 1
                elif c == "}":
                    if depth == 0:
                        return i + 1
                    depth -= 1
            if c.isalnum() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                word = src[i:j]
                # after a keyword such as return or typeof a "/" starts a regex
                prev = "" if word in ("return", "typeof", "case", "in", "of", "else", "void") else "x"
                i = j
                continue
            prev = c if c not in ")]" else "x"
            i += 1
        return i

    scan(0, False)
    return out


def _allowed(fname, text):
    return any(f in (fname, "*") and re.search(rx, text) for f, rx, _ in ALLOWED)


def flagged():
    hits = []
    for line, text in visible_pieces((STATIC / "index.html").read_text()):
        if INTERNAL.search(text) and not _allowed("index.html", text):
            hits.append(f"index.html:{line}: {' '.join(text.split())[:140]}")
    for f in sorted(STATIC.glob("*.js")):
        for line, s in js_strings(f.read_text()):
            if re.search(r"<[a-z]", s):                     # an HTML fragment: its visible text only
                texts = [t for _, t in visible_pieces(s)]
            elif re.search(r"\S\s+\S", s.strip()) and not re.match(r"\s*[#.\[]", s):
                texts = [s]                                  # prose; no space: a key, id or path; [#.[]: a selector
            else:
                continue
            for t in texts:
                if INTERNAL.search(t) and not _allowed(f.name, t):
                    hits.append(f"{f.name}:{line}: {' '.join(t.split())[:140]}")
    return hits


def test_page_text_uses_plain_words():
    hits = flagged()
    assert not hits, "internal words in page text (PRODUCT.md, plain words):\n" + "\n".join(hits)


@pytest.mark.parametrize("snippet,bad", [
    ('<p>Check backends</p>', True),
    ('<p>Saved in project.yaml</p>', True),
    ('<button title="sha256 of the file">x</button>', True),
    ('<details class="cmdline"><summary>Command line</summary><code>wider x --json</code></details>', False),
    ('<p data-backend="a" id="backend-x">Model</p>', False),
])
def test_the_check_sees_visible_text_only(snippet, bad):
    assert any(INTERNAL.search(t) for _, t in visible_pieces(snippet)) is bad


def test_js_strings_skip_comments_and_split_templates():
    src = 'const a = "one backend"; // a backend in a comment\n/* fake */ const b = `x ${f("inner text")} y`;'
    got = [s for _, s in js_strings(src)]
    assert got == ["one backend", "inner text", "x   y"]


# ---- other D32 review findings on the same pages: F13 (older results), F24 (cell scores) ----

def _results_js(body: str, data: dict) -> dict:
    from tests.unit.test_query_roles import APP_JS, js_function, run_js
    fns = "".join(js_function(APP_JS, f"function {n}(") for n in (
        "metricTiles", "rowsTable", "resultCardHtml", "renderStageResult", "renderPrevResult", "showStageResult"))
    prelude = r"""
const fmtNum = String, metricLabel = (k) => k, fmtWhen = (t) => "when " + t, usageChip = () => "", usageText = () => "";
const boxes = {};
const box = (sel) => (boxes[sel] ||= { hidden: true, innerHTML: "", dataset: {}, querySelector: () => ({}) });
global.document = { querySelector: (s) => box(s),
  querySelectorAll: (s) => s.includes("prev") ? [Object.assign(box('.stage-result.prev[data-prev="rank"]'), { dataset: { prev: "rank" } })] : [] };
const SHOWN_THIS_VISIT = new Set();
let STAGES = DATA.stages, LAST_RESULTS = DATA.last;
"""
    return run_js(prelude + fns + body, data)


def test_a_result_card_shows_only_for_this_visits_action():
    """F13: on opening a page, an earlier visit's result is one line with "Show the result", not a card, and the next
    page has no strip for it; after an action in this visit its card shows, the counts sit under "Details", and the
    next page's strip names it."""
    data = {"stages": [{"key": "dedup", "title": "Deduplication"}, {"key": "rank", "title": "Ranking"}],
            "last": {"dedup": {"stage": "dedup", "headline": "14 pairs found", "ran_at": "t1", "metrics": {"pairs": 14}}}}
    out = _results_js(r"""
const r = {};
renderStageResult("dedup"); renderPrevResult("rank");
r.before = { card: box('.stage-result[data-result="dedup"]').innerHTML, prevHidden: box('.stage-result.prev[data-prev="rank"]').hidden };
showStageResult({ stage: "dedup", headline: "15 pairs found", ran_at: "t2", metrics: { pairs: 15 } });
r.after = { card: box('.stage-result[data-result="dedup"]').innerHTML, prev: box('.stage-result.prev[data-prev="rank"]').innerHTML };
console.log(JSON.stringify(r));""", data)
    b, a = out["before"], out["after"]
    assert "Last run when t1" in b["card"] and "Show the result" in b["card"] and "14 pairs found" not in b["card"]
    assert b["prevHidden"] is True
    assert "15 pairs found" in a["card"] and "result-card" in a["card"]
    assert a["card"].index("<details>") < a["card"].index('class="metrics"')   # the tiles only under Details
    assert "15 pairs found" in a["prev"]


def test_the_cell_score_column_names_the_two_best_cells():
    """F24: the two best cells by name, best first, the others in the hover text of "+N more"."""
    from tests.unit.test_query_roles import APP_JS, js_function, run_js
    out = run_js(js_function(APP_JS, "function cellScores(") + """
console.log(JSON.stringify({ many: cellScores(DATA.many), two: cellScores(DATA.two) }));""",
                 {"many": {"voice_antecedent": 0.363, "silence_outcome": 0.5, "a_b": 0.1, "c": 0.2},
                  "two": {"x_y": 0.2, "z": 0.3}})
    assert out["many"].startswith("<b>silence outcome 0.500</b><br>voice antecedent 0.363<br>")
    # the hidden names are also read out, not only in the hover text (inspection 1 Nov, item 20)
    assert 'title="c 0.200; a b 0.100">+2 more<span class="sr-only">: c 0.200; a b 0.100</span></span>' in out["many"]
    assert out["two"] == "<b>z 0.300</b><br>x y 0.200"
