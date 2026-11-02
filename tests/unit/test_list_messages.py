"""The message list (`scripts/list_messages.py`): spec `message-inventory`.

A synthetic tree with topic-neutral text stands in for the repository; one smoke test reads the real checkout.
"""

import importlib.util
import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "list_messages.py"


def load():
    spec = importlib.util.spec_from_file_location("list_messages", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["list_messages"] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def lm():
    return load()


FILES = {
    "wider/stages.yaml": """\
sections:
  - number: 1
    title: Literature search
stages:
  - key: setup
    title: Review plan
    section: 1
    status: built
  - key: construct
    title: Concepts and synonyms
    section: 1
    status: built
  - key: query
    title: Search strings
    short: Strings
    section: 1
    status: built
    command: query build
    description: >
      Builds one search string
      for each database.
    produces:
      - strings.txt
      - report.md
  - key: dedup
    title: Deduplication
    section: 1
    status: built
  - key: rank
    title: Ranking
    section: 1
    status: built
  - key: screen
    title: Screening
    section: 1
    status: built
  - key: later
    title: Later step
    section: 1
    status: extension
""",
    "wider/ui/static/index.html": """\
<!doctype html>
<html><head><style>.page { margin: 0; }</style></head>
<body>
<nav><button id="go-home" title="Open the projects list">Projects</button></nav>
<section id="page-setup" class="page" hidden>
  <button id="save">Save the question</button>
</section>
<section id="page-dedup" class="page" hidden>
  <label for="dd">Pairs to check</label>
  <input id="dd" placeholder="type a number">
</section>
<script src="app.js"></script>
</body></html>
""",
    "wider/ui/static/app.js": """\
// WIDER app (synthetic)
function showLoadError(where, msg) { toast(`Could not start: ${msg}`); }

// ---------------- construct page ----------------
function drawConcepts() {
  toast("Concepts are saved");
}
async function loadProject() {
  toast("Project question loaded");
}
$("#save").onclick = async () => {
  toast("Question saved");
};
loadStages().catch(e => showLoadError("boot", `Could not load the stage list: ${e.message}`)).then(() => {
  route();
});

// ---------------- dedup page ----------------
function drawPairs(n) {
  // the "Clear all pairs now" button
  document.querySelector("section ul li");
  toast("Pairs are merged");
  if (confirm(`Merge ${n} pairs? This keeps one record of each.`)) go();
}

// ---------------- ranking page ----------------
function rankNow(m) {
  Jobs.start(`/api/rank/run?mode=${m}`, "Ranking the records");
  api("/api/rank/check").then(() => toast("Ranking is checked"));
}

// ---------------- odd new part ----------------
function oddThing() {
  toast("This part has no page yet");
}
""",
    "wider/ui/static/pickers.js": """\
// pickers shared by several pages
const Pickers = (() => {
  function chip(x) {
    return `<button class="small" data-id="${x.id}">Remove</button>`;
  }
  // ---------------- LLM picker (Screening page) ----------------
  function llm() { return "Pick a screening model."; }
  return { chip, llm };
})();
""",
    "wider/ui/static/shell.js": """\
const Shell = (() => {
  const STATE_WORD = { done: "done", ready: "ready", blocked: "blocked", extension: "later" };
  function stateLine(s) {
    return `<span>${s.n ? "steps left" : "nothing"}</span>`;
  }
  return { stateLine };
})();
""",
    "wider/ui/static/jobs.js": """\
function toast(msg, tone) { /* show it */ }
function done() {
  toast("The job has finished.");
}
""",
    "wider/ui/static/common.js": """\
const api = async (path, opts = {}) => fetch(path, opts);
function consent(x) {
  return api(`/api/${x}/y`);
}
""",
    "wider/ui/static/records.js": """\
function flagTexts(s) {
  toast("Open dedup again");
  toast("Freeze the query development set");
  toast("The list is frozen");
  toast("Deduplication is done");
  toast("stage exact_normalized_title");
  toast("use `wider enrich --retry`");
  toast("checked 2026-10-15T07:51:38+00:00");
  toast(`last checked ${s.checked_at}`);
  toast(`last checked ${fmtWhen(s.checked_at)}`);
}
""",
    "wider/ui/static/extra.js": """\
function extra() {
  toast("This extra script is new.");
}
""",
    "wider/ui/routes_rank.py": """\
from fastapi import HTTPException


def register(app):
    @app.post("/api/rank/run")
    def rank_run(body: dict):
        if not body:
            raise HTTPException(400, "Choose a ranking model first.")
        return {"ok": True}

    @app.get("/api/rank/check")
    def rank_check():
        raise HTTPException(status_code=409, detail="The ranking is not ready yet.")
""",
    "wider/screen/engine.py": """\
from wider.core.questions import raise_question


def split(project, rid, title, x, n, progress):
    if n < 0:
        raise ValueError("The record list is empty.")
    raise_question(project, "screen_split", f"k:{rid}", title or f"record {rid}",
                   f"The two reads of record {rid}" + (" again" if x else "") + " differ.")
    progress(1, 2, "checking the record list")
""",
    "wider/core/stage_summaries.py": """\
import logging


def label_for(key):
    \"\"\"Return the label shown for a key.\"\"\"
    names = {"pairs left to check today": key}
    logging.info("summary built for the stage")
    return names


def summarize_dedup(stage, results):
    uniq = 3
    head = f"{uniq:,} unique records kept."
    rows = [{"rule": k, "records removed": v} for k, v in results]
    return dict(head=head, metrics={"duplicates merged": 2}, rows=rows)
""",
    "wider/search/lint.py": """\
def lint(term):
    if " " in term:
        return "multi-word term is not quoted"
    return ""
""",
    "wider/cli.py": """\
import logging

import typer
from rich import print as rprint

app = typer.Typer(help="Work on a review from the terminal.")


def check_name(value: str):
    if not value:
        raise typer.BadParameter("Give a project name.")
    return value


@app.command()
def status(name: str = typer.Option("", help="Name of the project to show.")):
    \"\"\"Show where the project stands.\"\"\"
    rprint("[bold]Project status[/bold] is shown below.")
    if typer.confirm("Open the project folder now?"):
        logging.info("opening the folder for the user")
""",
    "wider/providers/presets.yaml": """\
presets:
  - id: test-cli
    label: Test CLI model
    vendor: Example vendor
    defaults:
      extra:
        auth_hint: "Test CLI is not signed in: run `testcli login` in a terminal"
""",
    "wider/search/platforms.yaml": """\
platforms:
  demo:
    label: Demo database
    coverage: Papers from 1990 onward
    paste: >-
      Paste the string into the
      advanced search box.
""",
    "docs/guide-text.md": """\
# Guide text

## Word list (first entries)

| In WIDER today | In plain words |
|---|---|
| dedup, deduplication | deduplication (author, 17 Oct: keep, it only removes duplicates) |
| freeze, frozen | lock, locked |
| development set | query development set (explained in the guide) |
| run | screening round |

More text after the table.
""",
}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "checkout"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def line_of(root, rel, snippet, nth=1):
    seen = 0
    for i, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
        if snippet in line:
            seen += 1
            if seen == nth:
                return i
    raise AssertionError(f"{snippet!r} not in {rel}")


def find(msgs, text, exact=True):
    hits = [m for m in msgs if (m.text == text if exact else text in m.text)]
    return hits


def one(msgs, text, exact=True):
    hits = find(msgs, text, exact)
    assert len(hits) == 1, (text, [(m.text, m.file, m.line) for m in hits])
    return hits[0]


def run(lm, root, *args):
    return lm.main(["--root", str(root), *args])


def test_not_a_wider_checkout(tmp_path, capsys, monkeypatch):
    lm = load()
    out = tmp_path / "list.md"
    assert lm.main(["--root", str(tmp_path), "--out", str(out)]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "not a WIDER checkout" in err[0]
    assert not out.exists()
    # a checkout whose guide text has no word-list table is refused too
    root = tmp_path / "no-table"
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text if rel != "docs/guide-text.md" else "# Guide text\n", encoding="utf-8")
    assert lm.main(["--root", str(root), "--out", str(out)]) == 2
    assert len(capsys.readouterr().err.strip().splitlines()) == 1
    assert not out.exists()
    # without PyYAML the tool stops with a one-line hint
    monkeypatch.setitem(sys.modules, "yaml", None)
    lm2 = load()
    assert lm2.main(["--root", str(root), "--out", str(out)]) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1 and "PyYAML" in err[0]
    assert not out.exists()


def test_ui_messages_of_each_kind(lm, tree):
    msgs = lm.collect(tree)
    button = one(msgs, "Projects")
    assert (button.kind, button.file, button.line) == ("UI text", "wider/ui/static/index.html", 4)
    t = one(msgs, "The job has finished.")
    assert (t.kind, t.file, t.line) == ("toast", "wider/ui/static/jobs.js", 3)
    c = one(msgs, "Merge {n} pairs? This keeps one record of each.")
    assert (c.kind, c.file) == ("UI dialog", "wider/ui/static/app.js")
    assert c.line == line_of(tree, "wider/ui/static/app.js", "confirm(")
    r = one(msgs, "Remove")
    assert (r.kind, r.file, r.line) == ("UI text", "wider/ui/static/pickers.js", 4)
    w = one(msgs, "later")
    assert (w.kind, w.file, w.line) == ("UI text", "wider/ui/static/shell.js", 2)
    assert one(msgs, "nothing").line == 4  # a one-word branch of a ternary inside a template
    # the comment and the selector are not messages
    assert not find(msgs, "Clear all pairs now", exact=False)
    assert not find(msgs, "section ul li", exact=False)
    assert not find(msgs, "show it", exact=False)


def test_api_and_result_messages(lm, tree):
    msgs = lm.collect(tree)
    routes = "wider/ui/routes_rank.py"
    h = one(msgs, "The ranking is not ready yet.")
    assert (h.kind, h.file, h.line) == ("API error", routes, line_of(tree, routes, "status_code=409"))
    eng = "wider/screen/engine.py"
    e = one(msgs, "The record list is empty.")
    assert (e.kind, e.file, e.line) == ("API error", eng, line_of(tree, eng, "ValueError"))
    ss = "wider/core/stage_summaries.py"
    head = one(msgs, "{uniq} unique records kept.")
    assert (head.kind, head.file, head.line) == ("result text", ss, line_of(tree, ss, "unique records kept"))
    m = one(msgs, "duplicates merged")
    assert (m.kind, m.file, m.line) == ("result text", ss, line_of(tree, ss, "duplicates merged"))
    col = one(msgs, "records removed")
    assert (col.kind, col.file, col.line) == ("result text", ss, line_of(tree, ss, "records removed"))
    title = one(msgs, "record {rid}")
    assert (title.kind, title.file, title.line) == ("result text", eng, line_of(tree, eng, "raise_question("))
    prompt = one(msgs, "The two reads of record {rid} again differ.")
    assert (prompt.kind, prompt.file, prompt.line) == ("result text", eng, line_of(tree, eng, "The two reads"))
    assert not find(msgs, " again") and not find(msgs, " differ.")  # the + parts are not listed again
    p = one(msgs, "checking the record list")
    assert (p.kind, p.file, p.line) == ("result text", eng, line_of(tree, eng, "progress(1, 2"))
    lint = one(msgs, "multi-word term is not quoted")
    assert (lint.kind, lint.file, lint.line) == ("result text", "wider/search/lint.py", 3)
    # the docstring, the prose dict key and the logging call are not messages
    for text in ("Return the label shown for a key.", "pairs left to check today", "summary built for the stage"):
        assert not find(msgs, text), text


def test_cli_and_stage_messages(lm, tree):
    msgs = lm.collect(tree)
    cli = "wider/cli.py"
    for text, kind, snippet in (
        ("Project status is shown below.", "CLI output", "rprint("),
        ("Give a project name.", "CLI output", "BadParameter"),
        ("Open the project folder now?", "CLI output", "typer.confirm"),
        ("Name of the project to show.", "CLI help", "help=\"Name"),
        ("Show where the project stands.", "CLI help", "Show where"),
    ):
        m = one(msgs, text)
        assert (m.kind, m.file, m.line, m.pages) == (kind, cli, line_of(tree, cli, snippet), {lm.CLI}), text
    assert not find(msgs, "opening the folder for the user")
    st = "wider/stages.yaml"
    d = one(msgs, "Builds one search string for each database.")
    assert (d.kind, d.file, d.line) == ("stage text", st, line_of(tree, st, "description:"))
    c = one(msgs, "wider query build")
    assert (c.kind, c.line) == ("stage text", line_of(tree, st, "command: query build"))
    p = one(msgs, "strings.txt, report.md")
    assert p.kind == "stage text"
    assert p.line in (line_of(tree, st, "produces:"), line_of(tree, st, "- strings.txt"))
    assert d.pages == c.pages == p.pages == {"query", "help"}
    assert one(msgs, "Later step").pages == {"help"}
    assert one(msgs, "Literature search").pages == {lm.EVERY}


def test_catalogue_texts(lm, tree):
    msgs = lm.collect(tree)
    pr = "wider/providers/presets.yaml"
    hint = one(msgs, "Test CLI is not signed in", exact=False)
    assert (hint.kind, hint.file, hint.line) == ("catalogue text", pr, line_of(tree, pr, "auth_hint"))
    assert hint.pages == {"model", "setup", "settings"}
    assert "command" in hint.flags
    pl = "wider/search/platforms.yaml"
    cov = one(msgs, "Papers from 1990 onward")
    assert (cov.kind, cov.file, cov.line, cov.pages) == ("catalogue text", pl, line_of(tree, pl, "coverage"),
                                                         {"setup"})
    paste = one(msgs, "Paste the string into the advanced search box.")
    assert (paste.kind, paste.line, paste.pages) == ("catalogue text", line_of(tree, pl, "paste"), {"query"})
    assert one(msgs, "Test CLI model").pages == {"model", "setup", "settings"}


def test_page_from_section_part_and_function(lm, tree):
    msgs = lm.collect(tree)
    assert one(msgs, "Pairs to check").pages == {"dedup"}
    assert one(msgs, "type a number").pages == {"dedup"}
    assert one(msgs, "Pairs are merged").pages == {"dedup"}
    assert one(msgs, "{uniq} unique records kept.").pages == {"dedup"}
    assert one(msgs, "Project question loaded").pages == {"setup"}
    assert one(msgs, "Question saved").pages == {"setup"}  # the $("#save") handler takes the page of #save
    assert one(msgs, "Concepts are saved").pages == {"construct"}
    assert one(msgs, "Remove").pages == {"model", "rank", "screen"}
    assert one(msgs, "Pick a screening model.").pages == {"screen"}
    assert one(msgs, "The job has finished.").pages == {lm.EVERY}
    assert one(msgs, "Could not load the stage list: {message}").pages == {lm.EVERY}
    assert one(msgs, "Projects").pages == {lm.EVERY}


def test_route_engine_and_unknown_pages(lm, tree):
    msgs = lm.collect(tree)
    assert one(msgs, "Choose a ranking model first.").pages == {"rank"}
    assert one(msgs, "The ranking is not ready yet.").pages == {"rank"}
    assert one(msgs, "The record list is empty.").pages == {lm.CALLER}
    assert one(msgs, "This extra script is new.").pages == {lm.UNKNOWN}
    assert one(msgs, "This part has no page yet").pages == {lm.UNKNOWN}  # a marker the map does not know
    assert one(msgs, "multi-word term is not quoted").pages == {"query"}
    assert one(msgs, "checking the record list").pages == {lm.EVERY}
    assert one(msgs, "record {rid}").pages == {"questions"}


def test_word_list_terms(lm, tree):
    msgs = lm.collect(tree)
    assert one(msgs, "Open dedup again").flags == ["word list: dedup"]
    assert one(msgs, "Freeze the query development set").flags == ["word list: freeze"]
    assert one(msgs, "The list is frozen").flags == ["word list: frozen"]
    assert one(msgs, "Deduplication is done").flags == []


def test_new_word_list_row(lm, tree):
    guide = tree / "docs/guide-text.md"
    guide.write_text(guide.read_text().replace("| run | screening round |",
                                               "| run | screening round |\n| widget | gadget |"))
    js = tree / "wider/ui/static/extra.js"
    js.write_text(js.read_text() + 'function more() { toast("Open the widget"); }\n')
    msgs = lm.collect(tree)
    assert one(msgs, "Open the widget").flags == ["word list: widget"]


def test_escaped_pair_is_one_character_and_out_is_kept_on_failure(lm, tree, monkeypatch):
    js = tree / "wider/ui/static/extra.js"
    js.write_text(js.read_text() + 'function f() { toast("Saved \\uD83D\\uDE00 and \\uD83D alone"); }\n')
    out = tree.parent / "list.md"
    assert run(lm, tree, "--out", str(out)) == 0
    assert "Saved \U0001F600 and � alone" in out.read_text(encoding="utf-8")
    out.write_text("earlier list", encoding="utf-8")
    monkeypatch.setattr(lm, "render", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        run(lm, tree, "--out", str(out))
    assert out.read_text(encoding="utf-8") == "earlier list"


def test_name_command_and_timestamp(lm, tree):
    msgs = lm.collect(tree)
    assert one(msgs, "stage exact_normalized_title").flags == ["snake_case"]
    assert one(msgs, "use `wider enrich --retry`").flags == ["command"]
    assert one(msgs, "checked 2026-10-15T07:51:38+00:00").flags == ["timestamp"]
    raw, formatted = sorted(find(msgs, "last checked {checked_at}"), key=lambda m: m.line)
    assert raw.flags == ["timestamp"]
    assert formatted.flags == []


def test_counts_order_and_stable_output(lm, tree, capsys):
    assert run(lm, tree, "--summary") == 0
    summary = capsys.readouterr().out
    a, b = tree.parent / "a.md", tree.parent / "b.md"
    assert run(lm, tree, "--out", str(a)) == 0
    assert run(lm, tree, "--out", str(b)) == 0
    text = a.read_text(encoding="utf-8")
    assert text == b.read_text(encoding="utf-8")
    assert text.startswith(summary)
    head = summary.splitlines()
    assert head[0].startswith("# ") and "heuristic" in head[0]
    rows = [r for r in head if r.startswith("| ")]
    assert rows and all(r.startswith("|") for r in head[1:] if r.strip())  # the table alone after the header
    rest = text[len(summary):].splitlines()
    sections = [r[3:] for r in rest if r.startswith("## ")]
    order = ["Your projects", "Model for query creation", "Review plan", "Concepts and synonyms",
             "Search strings", "Deduplication", "Ranking", "Screening", "Questions", "Help", "Settings",
             "every page", "any page that calls this", "page unknown", "CLI only"]
    assert [s for s in order if s in sections] == sections
    lines = [r for r in rest if r.startswith('- "')]
    page_rows = [r for r in rows if r.startswith("| page |")]
    assert sum(int(r.rstrip(" |").rsplit("|", 1)[1]) for r in page_rows) == len(lines)
    total = next(r for r in rows if r.startswith("| total |"))
    distinct = {ln.split(" — ")[0] + ln.split(" — ")[1].split(" · ")[0] + ln.split(" · ")[1] for ln in lines}
    assert int(total.rstrip(" |").rsplit("|", 1)[1]) == len(distinct) == len(lm.collect(tree))


def test_no_absolute_paths_and_no_new_file(lm, tree, monkeypatch, capsys):
    def no_network(*a, **k):
        raise OSError("network use in a test")
    monkeypatch.setattr(socket, "socket", no_network)
    before = sorted(p for p in tree.rglob("*"))
    assert run(lm, tree) == 0
    out = capsys.readouterr().out
    assert str(tree) not in out and str(tree.resolve()) not in out and "/Users/" not in out
    assert "`wider/ui/static/app.js:" in out
    assert sorted(p for p in tree.rglob("*")) == before


def fold(s):
    return " ".join(s.split())


def test_real_repository_lines(lm):
    msgs = lm.collect(REPO)
    kinds = {m.kind for m in msgs}
    assert kinds == set(lm.KINDS)
    cache = {}
    bad = []
    for m in msgs:
        lines = cache.setdefault(m.file, (REPO / m.file).read_text(encoding="utf-8").splitlines())
        line = fold(lines[m.line - 1])
        if not any(fold(c) in line for c in m.checks):
            bad.append((m.file, m.line, m.checks, m.text[:60]))
    assert not bad, bad[:20]
    # one-word status texts given to flash(el, msg) are listed (setup.js "saved", "resolving…")
    setup = "wider/ui/static/setup.js"
    assert {"saved", "resolving…"} <= {m.text for m in msgs if m.file == setup}
    # a query piece of a route (`"?sample=" + ...` in adjacent.js) is not a message
    assert not [m.text for m in msgs if m.file.endswith(".js") and m.text.startswith(("?", "&"))]

    def at(rel, snippet):
        return line_of(REPO, rel, snippet)

    app = "wider/ui/static/app.js"
    # the Freeze confirm moved to refine.js as an askChoice dialog (query refinement 2a): listed once, at its line
    assert not [m for m in msgs if m.file == app and m.text.startswith("Freeze")]
    refine = "wider/ui/static/refine.js"
    freeze = [m for m in msgs if m.file == refine and m.text == "Freeze {kind} v{version}? It can no longer change status."]
    assert [m.line for m in freeze] == [at(refine, "It can no longer change status.")]
    assert [m for m in msgs if m.file == app and m.line == at(app, "· produces") and "produces" in m.text]
    lk = "wider/cli_lookups.py"
    assert [m for m in msgs if m.file == lk and m.line == at(lk, "typer.confirm(") and m.kind == "CLI output"]
    ss = "wider/core/stage_summaries.py"
    assert [m for m in msgs if m.file == ss and "unique records from" in m.text and "dedup" in m.pages]
    assert [m for m in msgs if m.file == ss and m.text == "unique records" and m.kind == "result text"]
    assert [m for m in msgs if m.file == ss and m.text == "records removed" and m.line == at(ss, '"records removed"')]
    pro = "wider/screen/protocol.py"
    assert [m for m in msgs if m.file == pro and "disagree" in m.text and m.pages == {"questions"}]
    assert [m for m in msgs if "claude login" in m.text and m.kind == "catalogue text" and "command" in m.flags]
    assert [m for m in msgs if m.file == app and m.text.startswith("Could not load the stage list")
            and m.pages == {lm.EVERY}]
    assert [m for m in msgs if m.file == "wider/search/lint.py" and "multi-word term is not quoted" in m.text
            and m.pages == {"query"}]


def test_dialog_texts_are_collected(lm):
    """The in-page dialog helpers replace confirm() and prompt() (accessibility-and-naming D1): their questions and
    titles are still listed as "UI dialog", at their own line."""
    msgs = lm.collect(REPO)
    jobs, app = "wider/ui/static/jobs.js", "wider/ui/static/app.js"
    cancel = [m for m in msgs if m.file == jobs and m.text.startswith("Cancel this run?")]
    assert [(m.kind, m.line) for m in cancel] == [("UI dialog", line_of(REPO, jobs, "askConfirm(\"Cancel this run?"))]
    label = [m for m in msgs if m.file == app and m.text == "Label of {proto} set {n}"]
    assert [m.kind for m in label] == ["UI dialog"]


def test_ui_shows_no_raw_timestamp(lm, tree):
    """accessibility-and-naming 4.4 (D7): the timestamp flag also catches a time named `at` and a time cut to its date
    or minute with slice(0, 10|16), unless it goes through fmtWhen; then no message of the real page scripts has it."""
    (tree / "wider/ui/static/times.js").write_text("""\
box.innerHTML = `<li>called ${esc(w.at)} for ${esc(w.service)}</li>`;
box.innerHTML = `<li>dismissed on ${esc((c.at || "").slice(0, 10))} by ${esc(c.by)}</li>`;
box.innerHTML = `<li>archived ${h(e.archived_at.slice(0, 16).replace("T", " "))} as ${h(e.label)}</li>`;
box.innerHTML = `<li>imported on ${esc(fmtWhen(i.at))} by ${esc(i.by)}</li>`;
""", encoding="utf-8")
    msgs = [m for m in lm.collect(tree) if m.file == "wider/ui/static/times.js"]
    flagged = {m.text.split(" ")[0]: "timestamp" in m.flags for m in msgs}
    assert flagged == {"called": True, "dismissed": True, "archived": True, "imported": False}
    raw = [(m.file, m.line, m.text[:60]) for m in lm.collect(REPO)
           if m.file.startswith("wider/ui/static/") and "timestamp" in m.flags]
    assert not raw, raw


# The page words that may stand alone: platform roles, and texts a main spec quotes word for word (to be reworded in a
# later change, with its spec deltas). Keyed by the text, so a new unqualified message still fails.
DEV_VAL_EXCEPTIONS = {
    "(development)": "platform role (the development platform)",
    "development": "platform role badge on a search-string card",
    "Validation, used": "chip-like group name, defined in the glossary; quoted by five main specs",
    "in Validation, but was in Development: counted with Development": "chip title, quoted by import-and-coverage",
    "Validation checks": "Recall heading over the validation sample, quoted by recall-and-outputs",
    "Stop validation here": "Recall validation sample button, quoted by recall-and-outputs",
    "used validation (not independent)": "the page adds 'seeds found' after it",
    "Include the validation set": "Ranking check box, quoted by anchor-ranking",
    "Development evaluation": "Screening card heading, quoted by llm-screening and record-drawer",
    "Keep them as validation": "seed-paper dialog button, quoted by four main specs",
    "Move to Development?": "seed-paper dialog title, quoted by import-and-coverage",
    "Move to Development": "seed-paper dialog button, quoted by import-and-coverage",
    "the development share must be a number from 0 to 1, e.g. 0.6": "seed-paper split error, quoted by project-setup",
    "Development ({length}):": "seed-paper split preview, quoted by three main specs",
    "Validation ({length}):": "seed-paper split preview, quoted by three main specs",
    "3 Model tuning and validation": "the step's own name (stages.yaml sections), grouping the Settings data box",
}


def test_development_and_validation_are_qualified(lm, tree):
    """accessibility-and-naming 7.2: a page message holding the whole word "development" or "validation" also says
    which (seed papers, query, sample, screening or platform, in the text or on its source line), or is one of the
    named exceptions; a synthetic tree shows the check catches a bare one."""
    import re
    word = re.compile(r"(?<!\w)(development|validation)(?!\w)", re.I)
    which = re.compile(r"(?<!\w)(seed papers?|seeds?|query|sample|screening|platform)(?!\w)", re.I)

    def bare(root):
        out, lines = [], {}
        for m in lm.collect(root):
            if not m.kind.startswith("UI") or not word.search(m.text) or which.search(m.text):
                continue
            src = lines.setdefault(m.file, (Path(root) / m.file).read_text(encoding="utf-8").splitlines())
            if not which.search(src[m.line - 1]) and m.text not in DEV_VAL_EXCEPTIONS:
                out.append((m.file, m.line, m.text))
        return out

    (tree / "wider/ui/static/words.js").write_text("""\
box.innerHTML = `<p>Show the validation set?</p>`;
box.innerHTML = `<p>Show the validation seed papers?</p>`;
""", encoding="utf-8")
    assert [t for f, _, t in bare(tree) if f.endswith("words.js")] == ["Show the validation set?"]
    assert not bare(REPO), bare(REPO)
