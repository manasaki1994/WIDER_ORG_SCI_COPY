"""The guide shipped with WIDER and its notices (design-pass-a1-dedup D11), on synthetic records only."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wider.cli import app as cli_app
from wider.cli_notebook import guide_lines
from wider.core.project import Project
from wider.dedup.engine import decide, run
from wider.guide import HEADINGS, WRITTEN, guide, guide_text, notices, pages
from wider.ui.app import create_app

from .test_notebook import _import_more, _project, _r
from .test_screen import project_open  # noqa: F401 - pytest fixture
from .test_screen_compare import _template, cproject  # noqa: F401 - pytest fixtures

# twelve made-up titles with no word in common, each exported by both databases with a different journal: the run
# queues twelve pairs and nothing else
TITLES = ["Amber lantern quietly crossing", "Birch meadow under drizzle", "Cedar xylophone tuning method",
          "Delta pottery kiln temperature", "Ember violin bow rosin", "Fjord ferry timetable puzzle",
          "Grove beekeeping honey yield", "Heron sketchbook charcoal shading", "Iris bicycle chain grease",
          "Juniper telescope mirror polish", "Kestrel origami crane folding", "Lotus snowboard wax layers"]
TWELVE_A = "".join(_r("Lee, Kim", t, 2015, f"Annals {chr(65 + i)}") for i, t in enumerate(TITLES))
TWELVE_B = "".join(_r("Lee, Kim", t, 2015, f"Review {chr(65 + i)}") for i, t in enumerate(TITLES))
NO_DUP_A = _r("Ito, Sam", "Hand washing on night wards", 2011, "Journal H")
NO_DUP_B = _r("Moss, Eve", "Rota planning for small clinics", 2019, "Journal K")


def _cli(p, *args):
    res = CliRunner().invoke(cli_app, [*args, "--dir", str(p.root)])
    return res.exit_code, res.output


def test_written_parts_in_the_button_words():
    """The dedup Markdown holds the four written answers and Watch out; its steps name the page's buttons."""
    assert "dedup" in pages()
    g = guide_text("dedup")
    assert g["title"] == "Deduplication"
    assert [x["heading"] for x in g["parts"]] == [*WRITTEN, "Watch out"]
    steps = " ".join(g["parts"][0]["items"])
    for button in ("Run deduplication", "Duplicates, keep A", "Duplicates, keep B", "Not duplicates", "Unsure",
                   "Apply decisions and check again"):
        assert button in steps
    assert all(x["items"] for x in g["parts"]) and "**" not in json.dumps(g)


def test_pairs_waiting(tmp_path):
    p = _project(tmp_path, TWELVE_A, TWELVE_B)
    run(p)
    assert notices(p, "dedup") == ["12 pairs wait for your decision. Decide them, then press Apply decisions and "
                                   "check again."]
    decide(p, 1, 13, "duplicate", by="ana")
    assert notices(p, "dedup")[0].startswith("11 pairs wait")


def test_new_files_since_the_run(tmp_path):
    p = _project(tmp_path, NO_DUP_A, NO_DUP_B)
    run(p)
    _import_more(p, tmp_path, _r("Ward, Jo", "Shift length and errors", 2012, "Journal L"), "c.ris")
    _import_more(p, tmp_path, _r("Bell, Al", "Quiet rooms for staff", 2013, "Journal M"), "d.ris")
    assert notices(p, "dedup") == ["You added 2 files since the last run. Press Apply decisions and check again."]


def test_no_duplicates_found(tmp_path):
    p = _project(tmp_path, NO_DUP_A, NO_DUP_B)
    assert notices(p, "dedup") == []   # before the first run no state applies
    run(p)
    assert notices(p, "dedup") == ["No duplicates were found. Check that every database's export is uploaded."]


def test_no_state_applies(tmp_path):
    p = _project(tmp_path, TWELVE_A, TWELVE_B)
    run(p)
    for i in range(12):
        decide(p, 1 + i, 13 + i, "duplicate", by="ana")
    run(p)
    assert notices(p, "dedup") == []
    assert notices(p, "rank") == []


def _six(g):
    heads = [x["heading"] for x in g["parts"]]
    return heads[:6] == list(HEADINGS) and all(x["items"] for x in g["parts"][:6])


def test_same_text_on_both_surfaces(tmp_path):
    """The panel's route, `wider guide dedup --json` and the plain CLI show one guide: what you can do here now first,
    then the notices, then the six answers in order."""
    p = _project(tmp_path, TWELVE_A, TWELVE_B)
    run(p)
    page = TestClient(create_app(p)).get("/api/guide/dedup").json()
    code, out = _cli(p, "guide", "dedup", "--json")
    assert code == 0 and json.loads(out) == page == guide(p, "dedup")
    assert _six(page) and page["parts"][-1]["heading"] == "Watch out"
    code, out = _cli(p, "guide", "dedup")
    assert code == 0 and out.splitlines() == guide_lines(page)
    assert out.splitlines()[0] == f"What you can do here now: {page['can_do']}"
    assert page["notices"][0] in out.splitlines()[2]
    for part in page["parts"]:
        assert part["heading"] in out and all(item in out for item in part["items"])
    assert [ln for ln in out.splitlines() if ln in HEADINGS] == list(HEADINGS)


def test_six_answers_same_on_both_surfaces(tmp_path):
    """Scenario "Same text on both surfaces": `wider guide enrich` and the Missing abstracts panel's route show the six
    headings in order with the same sentences, and "Where you are" names section 2 and the step."""
    p = _project(tmp_path)
    page = TestClient(create_app(p)).get("/api/guide/enrich").json()
    code, out = _cli(p, "guide", "enrich", "--json")
    assert code == 0 and json.loads(out) == page and _six(page)
    where = page["parts"][0]["items"][0]
    assert where.startswith("Section 2, Data cleaning: Missing abstracts")
    code, out = _cli(p, "guide", "enrich")
    assert out.splitlines() == guide_lines(page)


def _page_keys():
    """The pages of the first table of docs/names.md, Home, Methods and Ask WIDER left out (Methods is the document
    the guide links to, not a page with a guide: methods-explainer D8; Ask WIDER is about another page and shows no
    guide panel: guide-assistant D23, its help is the Help guide's "Ask WIDER" section)."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "docs" / "names.md").read_text(encoding="utf-8")
    table = text.split("## Pages", 1)[1].split("Extension stages", 1)[0]
    keys = re.findall(r"^\| [^|]+ \| `#[\w-]+` \| `([\w-]+)` \|", table, re.M)
    return [k for k in keys if k not in ("home", "methods", "ask")]


def test_every_page_has_a_guide(tmp_path):
    """Scenario "Every page has a guide" (and "A page without guide text"): every page of the first table of
    docs/names.md but Home prints the six headings, and none says no guide is written."""
    p = _project(tmp_path)
    keys = _page_keys()
    assert "rank" in keys and len(keys) >= 18
    for key in keys:
        code, out = _cli(p, "guide", key)
        assert code == 0, (key, out)
        assert "no guide is written" not in out
        assert [ln for ln in out.splitlines() if ln in HEADINGS] == list(HEADINGS), key
        assert out.splitlines()[0].startswith("What you can do here now: "), key


def test_button_names_and_no_record_data(project_open):  # noqa: F811
    """Scenario "Button names and no record data": the dedup steps name the page's buttons; the sample guide, while
    the development sample has open labels, holds no record title, label, model decision or sample membership."""
    p = project_open
    code, out = _cli(p, "guide", "dedup")
    what = out.split("What to do", 1)[1].split("Why it matters", 1)[0]
    for button in ("Run deduplication", "Duplicates, keep A", "Duplicates, keep B", "Not duplicates", "Unsure"):
        assert button in what
    dev = p.store.scalar("SELECT sample_id FROM samples WHERE name='development'")
    assert p.store.scalar("SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (dev,)) > \
        p.store.scalar("SELECT COUNT(*) FROM labels WHERE sample_id=?", (dev,))   # labels open
    code, out = _cli(p, "guide", "sample", "--json")
    assert code == 0
    text = json.dumps(json.loads(out))
    for r in p.store.query("SELECT r.title, r.doi, r.record_id FROM records r"):
        assert r["title"] not in text and r["doi"] not in text
    n_dev = p.store.scalar("SELECT COUNT(*) FROM sample_rows WHERE sample_id=?", (dev,))
    for word in ("record_id", "decision", f"{n_dev} development", f"{n_dev} records", "development labels:"):
        assert word not in text, word


def test_page_outside_the_stages(tmp_path):
    """Scenario "A page outside the stages": with Missing abstracts next, `wider guide history` says History is a
    project page outside the stages and "What comes next" says "Back to Missing abstracts"."""
    from wider.core.stages import next_stage

    from .test_guided import _records_first

    p, artifacts = _records_first(tmp_path)
    from .test_construct import CELLS
    for kind, data in (("cells", CELLS), ("synonyms", {"blocks": [{"key": "voice", "name": "voice",
                                                                    "terms": ["employee voice"]}],
                                                        "logic": {"and": ["voice"]}})):
        row = artifacts.save_new_version(p, kind, data, origin="manual")
        artifacts.set_status(p, kind, row["version"], "approved")
    from wider.ingest.importer import set_included
    set_included(p, 1, True)   # the test file matches no seed paper, so it starts kept out (D32 F8)
    run(p)
    assert next_stage(p)["key"] == "enrich"
    g = guide(p, "history")
    parts = {x["heading"]: x["items"] for x in g["parts"]}
    assert parts["Where you are"][0].startswith("History is a project page outside the stages")
    assert parts["What comes next"] == ["Back to Missing abstracts."]
    code, out = _cli(p, "guide", "history")
    assert code == 0 and "Back to Missing abstracts." in out


def test_can_do_states(tmp_path):
    """"What you can do here now" from the stage's state: not yet with what it needs, work here, and completed naming
    the steps that read its result."""
    p = _project(tmp_path)
    assert guide(p, "rank")["can_do"].startswith("Not yet: this step needs Deduplication first")
    run(p)
    # D32 F2: a done stage with pairs left says what it needs, never "Completed"
    assert guide(p, "dedup")["can_do"].startswith("Not finished: it needs you: 4 pairs to decide. Changing this reruns "
                                                  "nothing; these steps read its result: Missing abstracts, Ranking")
    # audit F6: not "You can work here now" under the heading "What you can do here now"
    assert guide(p, "enrich")["can_do"].startswith("Not started yet: everything this step needs is done")
    assert guide(p, "model")["can_do"].startswith("Choose the model for query creation here")
    # scenarios "A locked search" and "A completed step names what uses it", from the stage states
    from wider.core.stages import stage_states
    from wider.guide import can_do

    states = [dict(s, state="done") if s["key"] in ("query", "enrich") else s for s in stage_states(p)]
    locked = can_do(p, "query", states, None)
    assert locked.startswith("Frozen: the search strings can be read and copied;")
    assert "Unlock to change" in locked and "unlocking asks whether the validation papers already shown count as seen" in locked
    assert "recorded in History, the methods text and the package" in locked
    assert can_do(p, "enrich", states, None) == ("Completed, and you can still change it. Changing this reruns nothing; "
                                                 "these steps read its result: Ranking, Screening. Nothing was looked up: no record has a "
                                                     "missing or thin abstract, so there is nothing to fetch.")


def test_can_do_start_routes(tmp_path):
    """Scenarios "Starting from a search strategy", "A records start before any import" and "Documenting an outside
    search": before any import, Search strings offers importing the string as a draft and says Question and seed
    papers and Concepts and synonyms are still needed, and both Search strings and Search results say importing the
    records first is fine (Search results is never "Not yet" for want of search strings); with records imported and
    no locked search, Search strings is optional and suggests recording the outside search."""
    from wider.core.stages import stage_states
    from wider.guide import can_do

    p = Project.init(tmp_path / "fresh", platforms=["scopus"])
    states = stage_states(p)
    for st in (states, [dict(s, state="ready", missing=[]) if s["key"] == "query" else s for s in states]):
        q = can_do(p, "query", st, None)
        assert "import the string as a draft (Import a string; wider query import)" in q, q
        assert "Review plan and Concepts and synonyms are still needed" in q
        assert "importing them on Search results before writing search strings is fine" in q
    imp = can_do(p, "import", states, None)
    assert not imp.startswith("Not yet") and "importing them before writing search strings is fine" in imp
    done = [dict(s, state="done") if s["key"] == "import" else dict(s, state="ready", missing=[]) if s["key"] == "query"
            else s for s in states]
    q = can_do(p, "query", done, None)
    assert "writing search strings is optional" in q and "Recording the outside search here keeps it reproducible" in q
    assert "import the string as a draft" in q
    p.close()


def test_unknown_page(tmp_path):
    """Scenario "Unknown page": the CLI says there is no such page and exits 1; the route answers 404."""
    p = _project(tmp_path)
    code, out = _cli(p, "guide", "nosuchpage")
    assert code == 1 and "there is no such page: nosuchpage" in out
    r = TestClient(create_app(p)).get("/api/guide/nosuchpage")
    assert r.status_code == 404 and r.json()["detail"] == "there is no such page: nosuchpage"


def test_no_dev_count_in_guide(cproject):  # noqa: F811
    """Scenario "No development count beside the queue" (the guide's part, ui-redesign 4.4): while the development
    sample has open labels, the Samples and labelling and Screening guides (page and `wider guide`) say "development
    labels open" with no number, and neither changes when a development record and then a validation record are
    labelled; the Samples guide's "Done when" names the complete development labels."""
    from tests.unit.test_screen_compare import dev_rows, fresh, label_all
    from wider.sample.labels import label

    p, _ = cproject
    rows = dev_rows(p)
    label_all(p, skip={r["record_id"] for r in rows[:3]})

    def seen():
        q = fresh(p)
        out = {page: guide(q, page) for page in ("sample", "screen")}
        cli = {page: _cli(q, "guide", page)[1] for page in ("sample", "screen")}
        return out, cli

    before, cli_before = seen()
    for page in ("sample", "screen"):
        text = json.dumps(before[page])
        assert "development labels open" in text, page
        assert "development labels open" in " ".join(cli_before[page].split()), page
        assert "left" not in before[page]["can_do"] and not any(ch.isdigit() for ch in before[page]["can_do"]), page
    done_when = next(x["items"] for x in before["sample"]["parts"] if x["heading"] == "Done when")
    assert any("every development record has a final label" in s for s in done_when)
    label(fresh(p), "development", rows[0]["record_id"], "alice", "exclude", reason="a")
    val = dev_rows(p, "validation")[0]["record_id"]
    label(fresh(p), "validation", val, "alice", "exclude", reason="a")
    assert seen() == (before, cli_before)


def test_plan_guide_names_the_tasks(tmp_path):
    """review-plan-and-question-window D14: `wider guide setup` is the Review plan's guide; its steps name the five
    task cards and the page's buttons, in the six answers."""
    p = _project(tmp_path)
    code, out = _cli(p, "guide", "setup")
    assert code == 0 and [ln for ln in out.splitlines() if ln in HEADINGS] == list(HEADINGS)
    assert guide_text("setup")["title"] == "Review plan"
    what = out.split("What to do", 1)[1].split("Why it matters", 1)[0]
    for name in ("Research question", "Seed papers", "Prior reviews", "Databases", "Find more seed papers",
                 "Next task", "‹ Review plan", "Waiting for you"):
        assert name in what, name
    assert "Question and seed " + "papers" not in out   # the old name, split so a grep finds none


def test_same_guide_on_task_page():
    """A task page (#setup?task=…) is the setup page to the shell: it marks setup current, and that loads the guide
    of "setup"; there is no guide per task."""
    from tests.unit.test_review_plan_js_node import _show_page

    assert _show_page(["#setup?task=seeds", "#setup?task=more"])["marked"] == ["setup", "setup"]
    shell = (Path(__file__).resolve().parents[2] / "wider/ui/static/shell.js").read_text(encoding="utf-8")
    mark = shell[shell.index("window.markCurrentPage = function (key)"):]
    assert "loadGuide(key);" in mark[:mark.index("\n  };")]
    assert not any(k.startswith("setup?") for k in pages())


def test_guide_pressed_labels_exist():
    """D32 F9: every button the guide says to press ("press **X**") is a button or button-styled link of the pages,
    so a first-time user never looks for a label that is not there; <name> stands for a model's name."""
    import pathlib
    import re

    static = pathlib.Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(static.glob("*.js")) + [static / "index.html"])
    labels = re.findall(r"<button[^>]*>([^<]+)", src) + re.findall(r'<a class="btn[^"]*"[^>]*>([^<]+)', src)
    labels += [s for line in re.findall(r"label:[^\n]*", src) for s in re.findall(r'"([^"]+)"', line)]
    labels += re.findall(r'el\("button", "[^"]*", "([^"]+)"\)', src)      # built in script: Ask WIDER's "Ask"
    labels += re.findall(r'mk\("([^"]+)", ', src)                          # Ask WIDER's "Didn't help"
    labels += [s for lab in labels for s in re.findall(r'"([^"]+)"', lab)]    # ${on ? "Chosen" : "Choose"}
    labels = [re.sub(r"\$\{[^}]*\}", "<name>", lab).strip() for lab in labels]
    pages = pathlib.Path(__file__).resolve().parents[2] / "wider" / "guide" / "pages"
    missing = []
    for md in sorted(pages.glob("*.md")):
        for b in re.findall(r"[Pp]ress (?:its |the )?\*\*(.+?)\*\*", md.read_text(encoding="utf-8")):
            pat = ".+".join(re.escape(x) for x in b.split("<name>"))
            if not any(re.fullmatch(pat + "…?", lab) for lab in labels):
                missing.append(f"{md.name}: {b}")
    assert not missing, missing


def test_guide_buttons_listed(tmp_path):
    """Scenario "Show a button on this page" (guide-assistant D13): the dedup guide lists "Run deduplication" in the
    `buttons` of the answer that names it, on the route and in `wider guide dedup --json`; the methods links stay."""
    from wider.guide import buttons

    assert "Run deduplication" in buttons("dedup")["What to do"]
    p = _project(tmp_path)
    page = TestClient(create_app(p)).get("/api/guide/dedup").json()
    code, out = _cli(p, "guide", "dedup", "--json")
    assert code == 0 and json.loads(out) == page
    todo = next(x for x in page["parts"] if x["heading"] == "What to do")
    assert "Run deduplication" in todo["buttons"]
    assert all(isinstance(x["buttons"], list) for x in page["parts"])
    assert next(x for x in page["parts"] if x["heading"] == "Where you are")["buttons"] == []
    assert "methods" in page


def test_guide_button_names_exist():
    """Scenario "Every named control exists": every bold name of every shipped guide page is the text or accessible
    name of a button, link, heading, legend or label in the pages' HTML or scripts (or a string the scripts render as
    one), or is in NOT_SHOWN; NOT_SHOWN names get no "Show" link. <name> stands for a model's name."""
    import re

    from wider.guide import NOT_SHOWN, button_registry, buttons

    static = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(static.glob("*.js")) + [static / "index.html"])
    tags = r"(?:button|a|h[1-6]|legend|label|summary)"
    names = [re.sub(r"<[^>]+>", "", t) for t in re.findall(rf"<{tags}\b[^>]*>(.*?)</{tags}>", src, re.S)]
    names += re.findall(r'aria-label="([^"]+)"', src)
    names += [s for line in re.findall(r"label:[^\n]*", src) for s in re.findall(r'"([^"]+)"', line)]
    names += re.findall(r'"([^"\n]{2,60})"', src) + re.findall(r"`([^`\n]{2,60})`", src)
    names = {re.sub(r"\$\{[^}]*\}", "<name>", re.sub(r"\s+", " ", n)).strip() for n in names}
    missing = []
    for page in pages():
        for heading, found in buttons(page).items():
            for b in found:
                pat = ".+".join(re.escape(x) for x in b.split("<name>"))
                if b not in NOT_SHOWN and not any(re.fullmatch(pat + "…?", n) for n in names):
                    missing.append(f"{page}/{heading}: {b}")
    assert not missing, missing
    registry = button_registry()
    assert not any(set(v) & NOT_SHOWN for v in registry.values())
    assert set(registry) == set(pages())


def test_assistant_buttons_are_controls():
    """M123 (31 Oct): the names the assistant may show (button_registry) are controls, not headings: none is found on
    the pages only as a heading or legend (Setup's task cards are links named by their title), every name in
    NOT_BUTTONS is such a heading, and every label a page's main top-bar button can show is known for that page
    (both of Deduplication's: "Run deduplication", then "Apply decisions and check again")."""
    import re

    from wider.guide import NOT_BUTTONS, button_registry, buttons

    static = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
    js = "\n".join(p.read_text(encoding="utf-8") for p in sorted(static.glob("*.js")))
    src = js + "\n" + (static / "index.html").read_text(encoding="utf-8")

    def inner(tags):
        return {re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", t)).strip()
                for t in re.findall(rf"<{tags}\b[^>]*>(.*?)</{tags}>", src, re.S)}
    controls = inner(r"(?:button|a|summary|label)") | set(re.findall(r'aria-label="([^"]+)"', src))
    controls |= {s for line in re.findall(r"label:[^\n]*", src) for s in re.findall(r'"([^"]+)"', line)}
    setup = (static / "setup.js").read_text(encoding="utf-8")
    controls |= set(re.findall(r'"([^"]+)"', re.search(r"const TITLES = \{([^}]*)\}", setup)[1]))   # Setup's card links
    headings = inner(r"(?:h[1-6]|legend)")
    registry = button_registry()
    bold = {n for pg in pages() for names in buttons(pg).values() for n in names}
    assert not [(pg, n) for pg, names in registry.items() for n in names if n in headings and n not in controls]
    assert NOT_BUTTONS <= bold and NOT_BUTTONS <= headings and not NOT_BUTTONS & controls
    assert "Needs attention" not in registry["overview"]
    mains = {}
    for pg, body in re.findall(r'Topbar\.set\("(\w+)",\s*\{(.*?)\}\s*\)', js, re.S):
        m = re.search(r"main:\s*\{\s*label:(.*?)onclick", body, re.S)
        if m:
            mains[pg] = re.findall(r'"([^"]+)"', m[1])
    assert set(mains["dedup"]) == {"Run deduplication", "Apply decisions and check again"}
    assert not [(pg, n) for pg, names in mains.items() for n in names if n not in registry.get(pg, [])]


def test_help_page_ask_wider(tmp_path):
    """guide-assistant 9.2: `wider guide help` prints the "Ask WIDER" part after the six answers: what it reads and
    never reads, that it never acts, where the conversation is kept, how to forget it and "Didn't help"; the route
    shows the same part, with its button names."""
    p = _project(tmp_path)
    code, out = _cli(p, "guide", "help")
    assert code == 0
    part = out.split("\nAsk WIDER\n", 1)[1]
    for words in ("never reads a record", "It never acts", "assistant/", "Forget this conversation",
                  "wider assistant forget", "Didn't help", "wider assistant history --unanswered"):
        assert words in " ".join(part.split()), words
    page = TestClient(create_app(p)).get("/api/guide/help").json()
    ask = page["parts"][-1]
    assert ask["heading"] == "Ask WIDER" and ask["buttons"] == ["Ask WIDER", "Ask", "Forget this conversation", "Didn't help"]   # the top-bar button first (D23)
    assert [x["heading"] for x in page["parts"]][:6] == list(HEADINGS)


def test_waiting_step_is_not_ready(cproject):  # noqa: F811
    """Scenarios "Screening waits for labels" and "Ready when nothing waits" (demo-chapters-and-video D9): while 6
    development records lack a final label, Screening's "What you can do here now" (page route, `can_do` and `wider guide`) says everything it needs is
    done but it waits for this, with no count, no "Ready" and no recommended-step clause; once the labels are complete
    the box no longer says it waits."""
    from tests.unit.test_screen_compare import dev_rows, fresh, label_all
    from wider.core.stages import stage_states
    from wider.guide import can_do

    p, _ = cproject
    rows = dev_rows(p)
    assert len(rows) >= 6
    label_all(p, skip={r["record_id"] for r in rows[:6]})
    q = fresh(p)
    states = stage_states(q)
    screen = next(s for s in states if s["key"] == "screen")
    assert screen["state"] == "ready" and screen["waits"] == "development labels open"
    want = "Everything this step needs is done, but it waits for this: development labels open."
    assert can_do(q, "screen", states, {"key": "screen"}) == want
    r = TestClient(create_app(q)).get("/api/guide/screen")
    assert r.status_code == 200 and r.json()["can_do"] == want
    assert "Ready" not in r.json()["can_do"] and "recommended" not in r.json()["can_do"]
    code, out = _cli(q, "guide", "screen")
    assert code == 0 and want in " ".join(out.split()) and "Ready:" not in out
    label_all(q)
    after = guide(fresh(p), "screen")["can_do"]
    assert after.startswith("Not started yet: everything this step needs is done") and "waits" not in after
