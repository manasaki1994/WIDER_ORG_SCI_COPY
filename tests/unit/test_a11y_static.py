"""Static checks of accessibility-and-naming (C6) over the page scripts."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"


def test_no_browser_confirm_prompt_or_alert():
    """No page script calls the browser's confirm, prompt or alert: every question is an in-page dialog
    (askConfirm, askFields, askChoice) a screen reader and the keyboard can use."""
    pat = re.compile(r"(^|[^a-zA-Z_.$])(window\.)?(confirm|prompt|alert)\s*\(")
    found = [f"{f.name}:{i}: {line.strip()}" for f in sorted(STATIC.glob("*.js"))
             for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
             if pat.search(line) and not line.lstrip().startswith("//")]
    assert found == []


def test_no_command_in_page_text():
    """No text a page writes names `wider <command>` outside a closed "Command line" fold, the Explain panel's and
    Help's "For scripts:" lines, or a sentence the engine writes and the CLI prints too (the Compare refusal)."""
    import importlib.util
    import sys
    script = STATIC.parents[2] / "scripts" / "list_messages.py"
    spec = importlib.util.spec_from_file_location("list_messages", script)
    lm = importlib.util.module_from_spec(spec)
    sys.modules["list_messages"] = lm  # dataclasses look the module up by name
    spec.loader.exec_module(lm)
    src = {f.name: f.read_text(encoding="utf-8").splitlines() for f in [*STATIC.glob("*.js"), STATIC / "index.html"]}
    allowed = ("For scripts:", "const scriptsLine", "const NOT_LOCKED = ")   # Explain builder, Help's card, compare.js
    found, folded = [], set()
    for m in lm.collect(STATIC.parents[2]):
        if not m.file.startswith("wider/ui/static/") or not re.search(r"\bwider (\{|[a-z][a-z-]+)", m.text):
            continue
        if "cmdline fold" in m.flags:
            folded.add(m.text)
            continue
        line = src[Path(m.file).name][m.line - 1]
        if not any(a in line for a in allowed):
            found.append(f"{m.file}:{m.line}: {m.text}")
    assert found == []
    # the cards the task names keep their command, now in the fold (the Yield card's as in the spec scenario)
    assert {'wider query yield "<term>"', "wider init", "wider query review", "wider protocol explain",
            "wider check-backends", "wider export selection", "wider query edits", "wider status"} <= folded
    ix = (STATIC / "index.html").read_text(encoding="utf-8")
    yld = ix[ix.index('<details id="q-yield-wrap">'):ix.index('<div id="q-oa-check">')]
    assert "wider" not in re.sub(r'<details class="cmdline">.*?</details>', "", yld)
    assert '<details class="cmdline"><summary>Command line</summary><p class="small"><code>wider query yield' in yld
    assert '<details class="cmdline" open' not in ix   # closed


def test_rows_keep_a_margin_for_toasts():
    """Focusable table rows (queues, the Screening review list) keep a bottom scroll margin as high as the toasts."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    m = re.search(r"^([^{}\n]*tr\[data-idx\][^{}\n]*)\{\s*scroll-margin-bottom:\s*var\(--toast-h", css, re.M)
    assert m and "[data-rv-row]" in m.group(1)
    assert 'setProperty("--toast-h"' in (STATIC / "jobs.js").read_text(encoding="utf-8")


def unnamed_controls() -> list[str]:
    """The inputs, selects, textareas and buttons of index.html without a name for screen readers: no text, no
    aria-label or aria-labelledby, no title, no wrapping <label> and no <label for>."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.controls, self.label_for, self.stack, self.open_labels = [], set(), [], 0

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "label":
                self.open_labels += 1
                if a.get("for"):
                    self.label_for.add(a["for"])
            if tag in ("input", "select", "textarea", "button") and a.get("type") != "hidden":
                c = {"tag": tag, "a": a, "line": self.getpos()[0], "text": "", "in_label": self.open_labels > 0}
                self.controls.append(c)
                if tag in ("button", "textarea", "select"):
                    self.stack.append(c)

        def handle_endtag(self, tag):
            if tag == "label":
                self.open_labels = max(0, self.open_labels - 1)
            if tag in ("button", "textarea", "select") and self.stack and self.stack[-1]["tag"] == tag:
                self.stack.pop()

        def handle_data(self, data):
            for c in self.stack:
                if c["tag"] == "button":
                    c["text"] += data

    p = P()
    p.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    out = []
    for c in p.controls:
        a = c["a"]
        named = (c["text"].strip() or a.get("aria-label", "").strip() or a.get("aria-labelledby") or a.get("title")
                 or c["in_label"] or (a.get("id") and a["id"] in p.label_for) or a.get("type") in ("submit", "reset") and a.get("value"))
        if not named:
            out.append(f"{c['line']}: <{c['tag']} {' '.join(f'{k}={v!r}' for k, v in a.items())}>")
    return out


def test_every_static_control_has_a_name():
    """Every control written in index.html has a name (text, aria-label, a label), and every select on the Recall
    page has a visible <label> whose words are not overridden by a different aria-label."""
    assert unnamed_controls() == []
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for sid in ("rc-protocol", "rc-set", "rc-sample", "rc-compare"):
        m = re.search(r'<label class="inline">([^<]+)<select id="' + sid + r'"([^>]*)>', html)
        assert m, sid
        assert "aria-label" not in m.group(2), sid   # the visible caption is the name


def test_prisma_and_coverage_svgs_are_named_with_unique_ids(tmp_path):
    """Scenario "Dashboard chart, coverage plot and PRISMA diagram", the files the CLI writes: `wider export prisma`
    writes a PRISMA SVG with role="img" and a name starting "PRISMA 2020 flow diagram"; two different coverage plots (as
    `wider query dry-run` draws them) are named pictures and share no id."""
    from typer.testing import CliRunner

    from tests.unit.test_validation_reuse import _frozen
    from wider.cli import app as cli_app
    from wider.search.coverage_plot import coverage_bars

    p = _frozen(tmp_path, "prisma")
    r = CliRunner().invoke(cli_app, ["export", "prisma", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    svg = (p.root / "exports" / "prisma_2020.svg").read_text(encoding="utf-8")
    assert re.match(r'<svg [^>]*role="img" aria-labelledby="prisma-title"', svg)
    assert re.search(r'<title id="prisma-title">PRISMA 2020 flow diagram', svg)
    run = {"version": 1, "platforms": {"ebsco": {"recall": {"hits": 2, "uncertain": 1, "misses": 1, "total": 4}}}}
    other = {"version": 2, "platforms": {"ebsco": {"recall": {"hits": 3, "uncertain": 0, "misses": 1, "total": 4}}}}
    a, b = coverage_bars(run), coverage_bars(other)
    assert coverage_bars(run) == a   # the same result draws the same SVG, so a stored dry run equals its recomputation
    for s in (a, b):
        assert re.match(r'<svg [^>]*role="img" aria-labelledby="(\S+) (\S+)"', s)
        assert re.search(r"Dry run coverage per database, synonyms v[12]</title>", s)
    ids = lambda s: set(re.findall(r'\bid="([^"]+)"', s))  # noqa: E731
    assert ids(a) and not ids(a) & ids(b)
    labelled = re.search(r'aria-labelledby="(\S+) (\S+)"', a).groups()
    assert set(labelled) <= ids(a)


def test_long_titles_wrap_at_phone_width():
    """accessibility-and-naming 5.1: below 640 px a long Home project title and the agreement tables' cells wrap
    (overflow-wrap:anywhere) and cards scroll sideways instead of widening the page; the sidebar is hidden behind
    the Menu button."""
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    i = css.rindex("@media (max-width: 639px)")
    block = css[i:css.index("\n}", i)]
    wrap = re.search(r"^\s*([^{}\n]+)\{\s*overflow-wrap:\s*anywhere", block, re.M)
    assert wrap and {".project-card h2", ".rec-table th", ".rec-table td"} <= {s.strip() for s in wrap.group(1).split(",")}
    assert re.search(r"^\s*\.card\s*\{\s*overflow-x:\s*auto", block, re.M)
    assert re.search(r"^\s*\.sidebar\s*\{\s*display:\s*none", block, re.M)
    assert re.search(r"body\.menu-open \.sidebar\s*\{[^}]*display:\s*flex", block)
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<button[^>]*id="menu-btn"[^>]*aria-controls="sidebar"[^>]*aria-expanded="false"', html)


def test_style_css_braces_balance():
    """Every block of style.css is closed (a stray brace silently drops the rules after it)."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "style.css").read_text(encoding="utf-8"), flags=re.S)
    depth = 0
    for ch in css:
        depth += {"{": 1, "}": -1}.get(ch, 0)
        assert depth >= 0
    assert depth == 0


def test_construct_editor_before_save():
    """accessibility-and-naming 5.2: on Concepts and synonyms, Tab reaches the artifact editor before Save."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    panel = html[html.index('id="art-panel"'):html.index('id="art-diff-wrap"')]
    assert panel.index('id="art-text"') < panel.index('id="art-save"')
    assert panel.index('id="art-approve"') < panel.index('id="art-text"')


def test_one_word_for_one_thing():
    """accessibility-and-naming 7.2: the pages, Help and the CLI help use the glossary's word (docs/names.md): the
    labelling queue (not "Screening queue"), `wider history undo` (not `wider undo`), "Seed papers" heading the
    Concepts and synonyms card, and Help's keys name "unsure" for duplicate pairs."""
    from typer.testing import CliRunner

    from wider.cli import app
    pages = {p.name: p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.suffix in (".js", ".html")}
    for name, text in pages.items():
        assert "Screening queue" not in text and "Benchmark papers" not in text and "wider undo" not in text, name
    assert '<span class="step-no">4a</span> Labelling queue</h2>' in pages["index.html"] and "<h2>Seed papers <button" in pages["index.html"]
    assert "Seed papers ground every proposal." in pages["index.html"]
    assert "duplicate pairs: duplicates, keep A / duplicates, keep B / not duplicates / unsure" in pages["shell.js"]
    setup = (STATIC.parents[1] / "cli_setup.py").read_text(encoding="utf-8")
    assert "wider history undo" in setup and "wider undo" not in setup
    out = CliRunner().invoke(app, ["history", "--help"], env={"COLUMNS": "200"})
    assert out.exit_code == 0 and "undo" in out.output, out.output
    out = CliRunner().invoke(app, ["--help"], env={"COLUMNS": "200"})
    assert "wider undo" not in out.output


def test_no_page_skips_a_heading_level():
    """Within each page of index.html the headings never jump more than one level down (h1 to h3 on Search strings
    and Review did, ui-redesign 5.6 leftovers)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    found = []
    pages = re.split(r'<section id="(page-[\w-]+)"', html)[1:]   # name, body, name, body, ...
    assert len(pages) >= 2 * 15
    for name, body in zip(pages[::2], pages[1::2]):
        last = 0
        for lv in (int(x) for x in re.findall(r"<h([1-6])\b", body)):
            if last and lv > last + 1:
                found.append(f"{name}: h{last} then h{lv}")
            last = lv
    assert found == []
