"""reporting.js runs under Node (no browser): the PRISMA reporting page draws the dicts of the /api/reporting routes
(the ones `wider report prisma ... --json` prints) with the CLI's blocks and sentences, sends what `edit`, `na`,
`clear`, `flow` and `export` take, and offers Undo after each change (prisma-reporting-assistant 7.1-7.3).
Synthetic, topic-neutral data only."""
import json
import re

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from tests.unit.test_prisma_2020_checklist import checked  # noqa: F401 - pytest fixture
from tests.unit.test_screen import project as _project_fixture  # noqa: F401 - pytest fixture

REPORTING = (STATIC / "reporting.js").read_text(encoding="utf-8")
ROOT = STATIC.parents[2]

CHOICES = {"protocols": ["main", "other"], "sets": [{"result_set": 1, "label": "", "active": True},
                                                   {"result_set": 2, "label": "second", "active": False}]}


def row(no, section, topic, status, **kw):
    return {"checklist": kw.pop("checklist", "prisma_2020"), "no": no, "section": section, "topic": topic,
            "status": status, "wider_status": kw.pop("wider_status", status), "not_applicable": False,
            "your_text": "none", "your_text_label": "none", "gaps_left": 0, "saved_for": None, "to_check": False,
            "where": None, **kw}


ROWS = [row("1", "Title", "Title", "Researcher input needed"),
        row("6", "Methods", "Information sources", "Partially documented"),
        row("7", "Methods", "Search strategy", "Automatically documented", your_text="saved", your_text_label="saved"),
        row("8", "Methods", "Selection process", "Partially documented", your_text="outdated",
            your_text_label="outdated", to_check=True),
        row("12", "Methods", "Effect measures", "Not applicable", wider_status="Researcher input needed",
            not_applicable=True)]
ROWS_S = [row("8", "Search methods", "Search strategies", "Automatically documented", checklist="prisma_s"),
          row("13", "Search methods", "Search filters", "Researcher input needed", checklist="prisma_s")]


def listing(cl="prisma_2020", rows=None):
    return {"checklist": cl, "title": {"prisma_2020": "PRISMA 2020", "prisma_s": "PRISMA-S"}[cl],
            "relate": f"How {cl} relates to the other checklist.", "rows": rows if rows is not None else
            (ROWS if cl == "prisma_2020" else ROWS_S), "protocol": "main", "result_set": 1, "choices": CHOICES}


def yours(**kw):
    return {"your_text": "none", "your_text_label": "none", "gaps_left": 0, "saved_for": None, "text": "",
            "covers": [], "where": None, "not_applicable": None, "from_draft": False, "from_item": None,
            "versions": 0, "outdated": None, **kw}


def item(no="7", cl="prisma_2020", draft=None, **kw):
    return {"checklist": cl, "no": no, "section": "Methods", "topic": "Search strategy",
            "official": "Present the full search strategies.", "source": "Page et al. 2021",
            "asks": "", "elements": [{"key": "strings", "text": "the full strings"},
                                     {"key": "limits", "text": "the limits used"}],
            "outside_sentence": None,
            "evidence": [{"text": "Scopus string v3", "source": "query round 3"}],
            "missing": [{"key": "limits", "text": "the limits used", "say": "name the limits", "covered_by_you": False}],
            "wider_status": "Partially documented", "status": "Partially documented", "has_evidence": None,
            "related": [{"checklist": "prisma_s", "no": "8", "topic": "Search strategies",
                         "status": "Automatically documented", "first_line": "All strings are in the appendix."}],
            "related_heading": "Related PRISMA-S items", "yours": kw.pop("yours", yours()), "draft": draft, **kw}


CTRL = r"""
const calls = [], undo = [];
const mk = () => ({ innerHTML: "", value: "", disabled: false, focus() { this.focused = true; } });
const naBtn = mk();
const parts = { protocol: mk(), set: mk(), body: mk(), find: (sel) => sel === "[data-rp-na-go]" ? naBtn : null };
const replies = {};
const api = async (path, opts) => {
  const m = (opts && opts.method) || "GET";
  calls.push([m, path, opts && opts.body ? JSON.parse(opts.body) : null]);
  const key = path.split("?")[0], r = replies[m + " " + key] !== undefined ? replies[m + " " + key] : replies[key];
  if (r === undefined) throw new Error("no reply for " + m + " " + key);
  if (typeof r === "string") throw new Error(r);
  const out = JSON.parse(JSON.stringify(r)), q = new URLSearchParams(path.split("?")[1] || "");
  if (m === "GET" && "result_set" in out) {   // a listing shows the protocol and set it was asked for
    if (q.get("protocol")) out.protocol = q.get("protocol");
    if (q.get("set")) out.result_set = Number(q.get("set"));
  }
  return out;
};
const c = Reporting.controller(parts, { api, offerUndo: (id, t) => undo.push([id, t]) });
const html = () => parts.body.innerHTML;
const posts = () => calls.filter(x => x[0] === "POST").map(x => [x[1].split("?")[0], x[2]]);
"""


def run(setup: dict, body: str) -> dict:
    reps = "\n".join(f"replies[{json.dumps(k)}] = {json.dumps(v)};" for k, v in setup.items())
    return run_node(f"{ESC}\n{REPORTING}\n{CTRL}\n{reps}\n{body}")


def text(h: str) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", h))
    return t.replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()


BASE = {"/api/reporting/prisma_2020": listing(), "/api/reporting/prisma_s": listing("prisma_s"),
        "/api/reporting/prisma_2020/7": item(), "/api/reporting/prisma_s/8": item(
            "8", "prisma_s", related=[{"checklist": "prisma_2020", "no": "7", "topic": "Search strategy",
                                       "status": "Automatically documented", "first_line": ""}],
            yours=yours(text="All strings are in the appendix.", your_text="saved", your_text_label="saved"))}


def test_item_panel_blocks(checked):  # noqa: F811
    """Item 7 from the real routes: the blocks in the CLI's order (PRISMA asks, WIDER recorded, Still missing, the
    related items, Your text), each evidence and missing line of `show 7`, in normal text."""
    from tests.unit.test_reporting import _api, _cli

    client = _api(checked)
    setup = {"/api/reporting/prisma_2020": client.get("/api/reporting/prisma_2020").json(),
             "/api/reporting/prisma_2020/7": client.get("/api/reporting/prisma_2020/7").json()}
    v = setup["/api/reporting/prisma_2020/7"]
    out = run(setup, "await c.load(); await c.open('7'); console.log(JSON.stringify({ h: html() }));")
    h = out["h"]
    heads = ["PRISMA asks", "WIDER recorded", "Still missing", v["related_heading"], "Your text"]
    pos = [h.index(f">{x}</h4>") for x in heads]
    assert pos == sorted(pos)
    page = text(h)
    cli = " ".join(_cli(checked, "report", "prisma", "show", "7").output.split())
    assert f"PRISMA 2020 item 7: {v['topic']} ({v['section']})" in page and f"Status: {v['status']}" in page
    for e in v["evidence"]:
        assert f"{e['text']} ({e['source']})" in page and e["text"] in cli
    for m in v["missing"]:
        assert f"{m['text']}: {m['say']}" in page
    for x in v["related"]:
        assert f'data-rp-follow="prisma_s:{x["no"]}"' in h
    assert "muted" not in h


def test_status_filter_counts():
    """The filter buttons count the tab's rows per status and "To check"; a filter shows only its rows, under the
    official section headings."""
    out = run(BASE, """await c.load(); const all = html(); c.filter("Partially documented");
      console.log(JSON.stringify({ all, part: html() }));""")
    a = text(out["all"])
    for label, n in [("All", 5), ("Automatically documented", 1), ("Partially documented", 2),
                     ("Researcher input needed", 1), ("Not applicable", 1), ("To check", 1)]:
        assert f"{label} {n}" in a
    assert 'data-rp-filter="Partially documented" aria-pressed="true"' in out["part"]
    rows = re.findall(r'data-rp-row="(\w+)"', out["part"])
    assert rows == ["6", "8"]
    assert '<th colspan="4" scope="colgroup">Methods</th>' in out["part"] and ">Title</th>" not in out["part"]
    # a status the researcher set shows WIDER's beside it
    assert "Not applicable (WIDER: Researcher input needed)" in text(out["all"])


def test_save_sends_edit_fields():
    """Save sends what changed, with the shown protocol and set: text, covers and where; only where when only that
    changed; nothing (and "Nothing changed.") when nothing did."""
    setup = {**BASE, "/api/reporting/prisma_2020/7": item(yours=yours(text="old", covers=["strings"], where="p. 2")),
             "POST /api/reporting/prisma_2020/7/text": {"changed": True, "change_id": 41}}
    out = run(setup, """await c.load(); await c.open('7');
      c.edit("text", "new text"); c.edit("covers", "limits", true); c.edit("where", "p. 6"); await c.save();
      const first = posts().length;
      await c.save();  // after the reload the form holds the saved values again: nothing changed
      const nothing = html();
      c.edit("where", "p. 7"); await c.save();
      console.log(JSON.stringify({ posts: posts(), first, nothing }));""")
    assert out["posts"][0] == ["/api/reporting/prisma_2020/7/text",
                               {"protocol": "main", "set": 1, "text": "new text", "covers": ["strings", "limits"],
                                "where": "p. 6"}]
    assert out["first"] == 1 and "Nothing changed." in out["nothing"]
    assert out["posts"][1] == ["/api/reporting/prisma_2020/7/text", {"protocol": "main", "set": 1, "where": "p. 7"}]


def test_na_needs_reason():
    """"Not applicable" waits for a reason: disabled while the reason is empty, nothing sent; with a reason it sends
    it."""
    setup = {**BASE, "POST /api/reporting/prisma_2020/7/na": {"changed": True, "change_id": 5}}
    out = run(setup, """await c.load(); await c.open('7'); const before = html();
      const none = await c.na(); const n0 = posts().length;
      c.edit("reason", "  "); const blank = naBtn.disabled;
      c.edit("reason", "no meta-analysis"); const ok = naBtn.disabled; await c.na();
      console.log(JSON.stringify({ before, none, n0, blank, ok, posts: posts() }));""")
    assert "data-rp-na-go disabled" in out["before"]
    assert out["none"] is None and out["n0"] == 0 and out["blank"] is True and out["ok"] is False
    assert out["posts"] == [["/api/reporting/prisma_2020/7/na", {"reason": "no meta-analysis"}]]



def test_focus_after_actions_and_undo_drops_line():
    """Keyboard: after a button redraws the panel the focus goes to the control that was pressed or took its place
    (Not applicable -> Remove the mark -> Reason; Clear my text -> Save), the export result takes the focus, and a
    load from outside (an Undo, a select) drops the last action's line."""
    setup = {**BASE, "POST /api/reporting/prisma_2020/7/na": {"changed": True, "change_id": 5},
             "POST /api/reporting/prisma_2020/7/text": {"changed": True, "change_id": 6},
             "POST /api/reporting/prisma_2020/7/clear": {"changed": True, "change_id": 7},
             "POST /api/reporting/export": {"message": "written", "files": [], "kept": []}}
    out = run(setup, """const focused = [];
      parts.find = (sel) => { const n = mk(); n.focus = () => focused.push(sel); return n; };
      await c.load(); await c.open('7'); focused.length = 0;
      c.edit("reason", "no meta-analysis"); await c.na(); const na = focused.slice(); focused.length = 0;
      await c.naClear(); const naClear = focused.slice(); focused.length = 0;
      await c.clearText(); const clear = focused.slice(); focused.length = 0;
      c.edit("text", "new text"); await c.save(); const msg = c.state().msg; focused.length = 0;
      await c.exportNow(); const exp = focused.slice();
      await c.load(); const after = c.state().msg;
      console.log(JSON.stringify({ na, naClear, clear, msg, exp, after }));""")
    assert out["na"] == ["[data-rp-na-clear]"] and out["naClear"] == ['[data-rp-field="reason"]']
    assert out["clear"] == ["[data-rp-save]"] and out["exp"] == ["[data-rp-export-msg]"]
    assert out["msg"].endswith("text saved") and out["after"] == ""

def test_tabs_and_item_7_link():
    """Three tabs (role tab, the shown one selected), each checklist tab with its sentence on how the checklists
    relate; item 7's related PRISMA-S item opens on the PRISMA-S tab with the protocol and set kept."""
    out = run(BASE, """await c.load(); await c.open('7'); const h7 = html();
      await c.follow("prisma_s", "8");
      console.log(JSON.stringify({ h7, hs: html(), gets: calls.filter(x => x[0] === "GET").map(x => x[1]),
        tab: c.state().tab }));""")
    h7 = out["h7"]
    assert re.findall(r'role="tab" id="rp-tabbtn-(\w+)" data-rp-tab="\w+" aria-selected="(\w+)"', h7) == [
        ("prisma_2020", "true"), ("prisma_s", "false"), ("flow", "false")]
    assert "How prisma_2020 relates to the other checklist." in h7
    assert 'data-rp-follow="prisma_s:8"' in h7 and "PRISMA-S item 8: Search strategies (Automatically documented)" in text(h7)
    assert out["tab"] == "prisma_s" and 'data-rp-tab="prisma_s" aria-selected="true"' in out["hs"]
    assert "How prisma_s relates to the other checklist." in out["hs"]
    assert out["gets"][-2:] == ["/api/reporting/prisma_s?protocol=main&set=1", "/api/reporting/prisma_s/8?protocol=main&set=1"]
    assert "PRISMA-S item 8: Search strategy (Methods)" in text(out["hs"])


def test_undo_offer_after_save():
    """A save offers Undo with its change id and what was done; a save the engine found unchanged offers none."""
    setup = {**BASE, "POST /api/reporting/prisma_2020/7/text": {"changed": True, "change_id": 77}}
    out = run(setup, """await c.load(); await c.open('7'); c.edit("text", "a"); await c.save(); const h = html();
      replies["POST /api/reporting/prisma_2020/7/text"] = { changed: false };
      c.edit("text", "b"); await c.save();
      console.log(JSON.stringify({ undo, h, h2: html() }));""")
    assert out["undo"] == [[77, "PRISMA 2020 item 7: text saved"]]
    assert "PRISMA 2020 item 7: text saved" in out["h"] and "Nothing changed." in out["h2"]


def test_reporting_hash_opens_page():
    """#reporting is a page: its section with the selects and the owner sentence in black, the script after
    results.js, its PAGES id and loader in app.js; the wiring sets loadReporting and the top bar's "Export
    checklists", and loading asks the checklist route."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = html[html.index('<section id="page-reporting"'):]
    sec = sec[:sec.index("</section>")]
    assert '<h1>PRISMA reporting</h1>' in sec and 'id="rp-protocol"' in sec and 'id="rp-set"' in sec
    assert ('<p>An owner view: it shows what WIDER recorded, including screening results. A blind second reviewer '
            'labels in the queue on Samples and labelling only.</p>') in sec and "muted" not in sec
    assert html.index('src="/static/results.js"') < html.index('src="/static/reporting.js"') < html.index('src="/static/shell.js"')
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'reporting: "page-reporting"' in app and "reporting: window.loadReporting" in app
    script = ESC + r"""
const els = {}, mk = (id) => (els[id] = els[id] || { id, innerHTML: "", value: "", listeners: {},
  addEventListener(k, f) { this.listeners[k] = f; }, querySelector: () => null });
global.window = {}; const sets = [];
global.document = { querySelector: (s) => mk(s.slice(1)) };
const calls = [];
global.api = async (path) => { calls.push(path); throw new Error("nothing imported yet"); };
""" + REPORTING + r"""
// shell.js loads after reporting.js, so Topbar exists only once the page opens
global.Topbar = { set: (k, conf) => sets.push([k, conf.main.label]) }; window.Topbar = Topbar;
await window.loadReporting();
console.log(JSON.stringify({ calls, sets, body: els["rp-body"].innerHTML }));
"""
    out = run_node(script)
    assert out["calls"] == ["/api/reporting/prisma_2020"] and out["sets"] == [["reporting", "Export checklists"]]
    assert "<p data-rp-error>nothing imported yet</p>" in out["body"]


def test_use_draft_and_keep_send_edit_fields():
    """"Use the draft" sends from_draft and "Keep my text" (shown only for outdated text) sends keep, each with the
    protocol and set."""
    od = {"reason": None, "saved": [{"text": "Scopus string v2", "source": "query round 2"}],
          "current": [{"text": "Scopus string v3", "source": "query round 3"}], "inconsistencies": ["2 strings, not 3"]}
    setup = {**BASE, "/api/reporting/prisma_2020/7": item(draft={"text": "WIDER's draft [limits]", "gaps": 1},
                                                          yours=yours(text="mine", outdated=od)),
             "POST /api/reporting/prisma_2020/7/text": {"changed": True, "change_id": 3}}
    out = run(setup, """await c.load(); await c.open('7'); const h = html(); await c.useDraft(); await c.keep();
      console.log(JSON.stringify({ h, posts: posts() }));""")
    h = out["h"]
    assert "data-rp-keep" in h and "data-rp-draft" in h
    red = [text(x) for x in re.findall(r'<p class="warn-line"[^>]*>([^<]*)</p>', h)]
    assert red == ["Your text is outdated: WIDER's evidence for this item changed since you saved it.",
                   "2 strings, not 3"]
    assert out["posts"] == [["/api/reporting/prisma_2020/7/text", {"protocol": "main", "set": 1, "from_draft": True}],
                            ["/api/reporting/prisma_2020/7/text", {"protocol": "main", "set": 1, "keep": True}]]


def test_na_clear_button():
    """A marked item shows its reason and "Remove the not-applicable mark", which sends clear."""
    setup = {**BASE, "/api/reporting/prisma_2020/7": item(yours=yours(not_applicable={"reason": "no synthesis"})),
             "POST /api/reporting/prisma_2020/7/na": {"changed": True, "change_id": 9}}
    out = run(setup, """await c.load(); await c.open('7'); const h = html(); await c.naClear();
      console.log(JSON.stringify({ h, posts: posts(), undo }));""")
    assert "Not applicable: no synthesis" in text(out["h"]) and "data-rp-na-clear" in out["h"]
    assert "data-rp-na-go" not in out["h"]
    assert out["posts"] == [["/api/reporting/prisma_2020/7/na", {"clear": True}]]
    assert out["undo"] == [[9, "PRISMA 2020 item 7: not-applicable mark removed"]]


def test_no_draft_hides_use_draft():
    out = run(BASE, """await c.load(); await c.open('7'); const r = await c.useDraft();
      console.log(JSON.stringify({ h: html(), r, posts: posts() }));""")
    assert "data-rp-draft" not in out["h"] and "Draft from WIDER" not in out["h"]
    assert out["r"] is None and out["posts"] == [] and "data-rp-keep" not in out["h"]


def test_related_links_switch_tab():
    """From PRISMA-S item 8 back to PRISMA 2020 item 7: the tab switches, the item opens, the protocol and set stay;
    the shared filter goes back to all, also when it would not hide the item (ui-tester F1)."""
    out = run(BASE, """await c.load({ protocol: "other" }); c.filter("Researcher input needed"); await c.tab("prisma_s");
      await c.follow("prisma_s", "8"); await c.follow("prisma_2020", "7");
      console.log(JSON.stringify({ st: c.state(), h: html(), gets: calls.map(x => x[1]) }));""")
    st = out["st"]
    assert st["tab"] == "prisma_2020" and st["open"] == {"checklist": "prisma_2020", "no": "7"} and st["filter"] == "all"
    assert out["gets"][0] == "/api/reporting/prisma_2020?protocol=other"
    assert out["gets"][-1] == "/api/reporting/prisma_2020/7?protocol=other&set=1"
    assert 'data-rp-row="7" class="on"' in out["h"]
    keep = run(BASE, """await c.load(); c.filter("Automatically documented"); await c.follow("prisma_2020", "7");
      console.log(JSON.stringify({ st: c.state(), h: html() }));""")
    assert keep["st"]["filter"] == "all" and 'data-rp-filter="all" aria-pressed="true"' in keep["h"]


def test_to_check_filter():
    """"To check" shows the items to look at (here the outdated one), its "Your text" cell in red."""
    out = run(BASE, """await c.load(); c.filter("To check"); console.log(JSON.stringify({ h: html() }));""")
    assert re.findall(r'data-rp-row="(\w+)"', out["h"]) == ["8"]
    assert '<td class="warn-line">outdated</td>' in out["h"]


def test_start_from_related_text():
    """"Start from this text" fills the box with the related item's text and saves nothing; Save then sends
    from_item; after an edit of the box it sends the text instead."""
    setup = {**BASE, "POST /api/reporting/prisma_2020/7/text": {"changed": True, "change_id": 1}}
    out = run(setup, """await c.load(); await c.open('7'); await c.startFrom("prisma_s", "8"); const h = html();
      const n = posts().length; await c.save();
      await c.startFrom("prisma_s", "8"); c.edit("text", "All strings are in appendix B."); await c.save();
      console.log(JSON.stringify({ h, n, posts: posts() }));""")
    assert out["n"] == 0 and ">All strings are in the appendix.</textarea>" in out["h"]
    assert "Nothing is saved until you press Save." in out["h"]
    assert out["posts"] == [["/api/reporting/prisma_2020/7/text", {"protocol": "main", "set": 1, "from_item": "prisma-s:8"}],
                            ["/api/reporting/prisma_2020/7/text", {"protocol": "main", "set": 1,
                                                                   "text": "All strings are in appendix B."}]]


def test_history_show_offers_report_text():
    """The History page's Show list, built from the engine's kinds, offers "PRISMA reporting text"."""
    from wider.core import journal

    kinds = journal.kinds()
    assert {"kind": "report_text", "label": "PRISMA reporting text"} in kinds
    script = ESC + PAGE + f"""
global.localStorage = {{ getItem: () => "" }}; global.fmtWhen = (t) => t;
replies["/api/history/kinds"] = {json.dumps(kinds)};
replies["/api/history?limit=200"] = [];
{(STATIC / "history.js").read_text(encoding="utf-8")}
await window.loadHistory();
console.log(JSON.stringify({{ html: $("#hist-kind").innerHTML }}));
"""
    assert '<option value="report_text">PRISMA reporting text</option>' in run_node(script)["html"]


FLOW = {"protocol": "main", "result_set": 1, "message": None,
        "boxes": [{"name": "identified_databases", "label": "Records identified from databases", "n": 40,
                   "source": "wider", "text": "40"},
                  {"name": "registers", "label": "Records identified from registers", "n": 2, "source": "researcher",
                   "text": "2"},
                  {"name": "studies", "label": "Studies included", "n": None, "source": "missing", "text": ""}],
        "entered": {"registers": 2}, "excluded": [{"reason": "Wrong design", "n": 3}],
        "checks": ["Reports excluded (3) exceed reports assessed (2).", "Studies included is missing."],
        "outdated": None, "unreadable": False, "other_sets": None, "saved_at": "t", "svg": "<svg><text>flow</text></svg>"}


def test_flow_tab_checks_red():
    """The flow tab: the preview, a number field for each box WIDER does not record, † for the researcher's, and each
    check line red, once."""
    setup = {**BASE, "/api/reporting/flow": FLOW}
    out = run(setup, """await c.tab("flow"); console.log(JSON.stringify({ h: html() }));""")
    h = out["h"]
    assert "<svg><text>flow</text></svg>" in h and 'data-rp-count="registers"' in h and 'data-rp-count="studies"' in h
    assert 'data-rp-count="identified_databases"' not in h and "† entered by the researcher" in h
    for line in FLOW["checks"]:
        assert h.count(f'<p class="warn-line" data-rp-check>{line}</p>') == 1


def test_flow_tab_sends_counts():
    """Save counts sends every field (an empty one as a removal) and the reason rows, with protocol and set; Undo is
    offered; a refusal shows the route's message."""
    setup = {**BASE, "/api/reporting/flow": FLOW,
             "POST /api/reporting/flow": {"changed": True, "change_id": 12, "flow": FLOW}}
    out = run(setup, """await c.tab("flow"); c.edit("count", ["registers", ""]); c.edit("count", ["studies", "4"]);
      c.edit("ex", ["1:reason", "No full text"]); c.edit("ex", ["1:n", "1"]); await c.saveFlow();
      replies["POST /api/reporting/flow"] = "studies: not a whole number"; await c.saveFlow();
      console.log(JSON.stringify({ posts: posts(), undo, h: html() }));""")
    assert out["posts"][0] == ["/api/reporting/flow", {
        "protocol": "main", "set": 1,
        "counts": {"registers": "", "automation_removed": "", "not_retrieved": "", "studies": "4", "reports": ""},
        "excluded": [{"reason": "Wrong design", "n": "3"}, {"reason": "No full text", "n": "1"}]}]
    assert out["undo"] == [[12, "PRISMA 2020 flow: counts saved"]]
    assert '<span class="bad" role="alert">studies: not a whole number</span>' in out["h"]


def test_flow_tab_other_set_notice():
    """Counts entered for other sets are named in a black line; unreadable counts in red."""
    setup = {**BASE, "/api/reporting/flow": {**FLOW, "checks": [], "other_sets": "Counts were entered for main, set 2.",
                                             "unreadable": True}}
    out = run(setup, """await c.tab("flow"); console.log(JSON.stringify({ h: html() }));""")
    assert "<p data-rp-other-sets>Counts were entered for main, set 2.</p>" in out["h"]
    assert '<p class="warn-line">could not read your flow counts for this set</p>' in out["h"]


def test_flow_tab_outdated_new_box():
    """WIDER's counts changed since the save: each changed box is listed, and a box new since then (a platform imported
    later) reads "not in the diagram" on the saved side, not an empty value."""
    od = {"line": "WIDER's counts changed since you saved these counts: check them.",
          "changed": [{"box": "identified_databases", "label": "Records identified from databases", "saved": 40, "now": 44},
                      {"box": "database:ebsco", "label": "Records identified from EBSCOhost", "saved": None, "now": 4}]}
    setup = {**BASE, "/api/reporting/flow": {**FLOW, "outdated": od}}
    out = run(setup, """await c.tab("flow"); console.log(JSON.stringify({ h: html() }));""")
    assert "<li>Records identified from databases: 40 when you saved, 44 now</li>" in out["h"]
    assert "<li>Records identified from EBSCOhost: not in the diagram when you saved, 4 now</li>" in out["h"]


def test_export_lists_word_files(checked):  # noqa: F811
    """Export checklists lists every file the real route wrote, each a download link to /api/outputs/file."""
    from urllib.parse import quote

    from tests.unit.test_reporting import _api

    res = _api(checked).post("/api/reporting/export", json={}).json()
    setup = {**BASE, "POST /api/reporting/export": res}
    out = run(setup, """await c.load(); await c.exportNow(); console.log(JSON.stringify({ h: html(), posts: posts() }));""")
    h = out["h"]
    assert out["posts"] == [["/api/reporting/export", {"protocol": "main", "set": 1}]]
    assert all(f.startswith("exports/") for f in res["files"]) and len(res["files"]) > 4
    for f in res["files"]:
        assert f'<li><a href="/api/outputs/file?path={quote(f, safe="")}" download>{f[len("exports/"):]}</a></li>' in h
    assert any(f.endswith("prisma_2020_checklist.docx") for f in res["files"])


def test_export_message_shown():
    """The export's message is shown; a refusal shows the route's words."""
    setup = {**BASE, "POST /api/reporting/export": {"message": "PRISMA reporting exports written: 2 files",
                                                    "files": ["exports/prisma_reporting/a.md"], "kept": []}}
    out = run(setup, """await c.load(); await c.exportNow(); const h = html();
      replies["POST /api/reporting/export"] = "nothing imported yet"; await c.exportNow();
      console.log(JSON.stringify({ h, h2: html() }));""")
    assert '<p data-rp-export-msg tabindex="-1">PRISMA reporting exports written: 2 files</p>' in out["h"]
    assert '<span class="bad" role="alert">nothing imported yet</span>' in out["h2"]
