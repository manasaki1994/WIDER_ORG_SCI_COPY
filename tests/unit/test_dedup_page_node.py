"""The Deduplication page's dedup.js runs under Node on a small stand-in (no browser). Match words and marked
differences are pure functions with the same word list as ``engine.match_word`` (the shared cases in
``tests/fixtures/match_words.json``); record text in a marked difference stays text. The page itself (flow chart,
list, docked pair view, notebook, guide, main button) runs on canned answers of the page's own routes, with the
shell's status map, drawer.js and history.js loaded as the page loads them. Synthetic data only."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_drawer_keys_node import WIDER_NAME
from wider.core.protocol import ReviewProtocol

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "wider" / "ui" / "static"
FIXTURE = ROOT / "tests" / "fixtures" / "match_words.json"
NODE = shutil.which("node")


def common_esc() -> str:
    """The page's own ``esc`` and ``cmdFold`` from common.js, so the test escapes as the page does."""
    js = (STATIC / "common.js").read_text(encoding="utf-8")
    from tests.unit.test_backends_js_node import fmt_when_js
    pick = lambda start: next(line for line in js.splitlines() if line.startswith(start))   # noqa: E731
    return pick("const esc =") + "\n" + pick("const cmdFold =") + "\n" + fmt_when_js()


def run_node(body: str) -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    page = f"global.window = global;\n{common_esc()}\n{(STATIC / 'dedup.js').read_text(encoding='utf-8')}"
    r = subprocess.run([NODE, "-"], input=f"{page}\n{body}", capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_match_words_are_the_shared_cases():
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out = run_node(f"const cases = {json.dumps(cases)};\n"
                   "console.log(JSON.stringify(cases.map((c) => DedupView.matchWord(c.a, c.b))));")
    assert out == [c["word"] for c in cases]
    assert {c["word"] for c in cases} == {"same", "spelling", "nearly", "differs", "only_a", "only_b", "missing"}


def test_nearly_is_a_word_set_jaccard_of_at_least_0_8():
    # 8 shared of 10 words is 0.8 (nearly); 7 shared of 10 is 0.7 (differs)
    eight = "a1 a2 a3 a4 a5 a6 a7 a8"
    out = run_node(f"""console.log(JSON.stringify([
      DedupView.matchWord("{eight} x9 x10", "{eight}"),
      DedupView.matchWord("a1 a2 a3 a4 a5 a6 a7 x8 x9 x10", "a1 a2 a3 a4 a5 a6 a7"),
      DedupView.matchWord("Résumé of Ångström", "resume of angstrom")]));""")
    assert out == ["nearly", "differs", "spelling"]


def _unsame(html):
    """The text with the green "in both records" marks (P10) taken off, so the pink ones can be compared."""
    return re.sub(r'<mark class="same">(.*?)</mark>', r"\1", html)


def test_marked_differences_mark_only_the_missing_words():
    out = run_node("""const m = DedupView.markDiff("Work stress in nurses", "Work Stress in hospital nurses.");
console.log(JSON.stringify(m));""")
    # P10: a run of shared words is one light-green mark, a word one record lacks a light-pink one
    assert out["a"] == '<mark class="same">Work stress in nurses</mark>'
    assert out["b"] == '<mark class="same">Work Stress in</mark> <mark class="diff">hospital</mark> <mark class="same">nurses.</mark>'


def test_a_script_title_stays_text():
    out = run_node("""const m = DedupView.markDiff('<script>alert(1)</script> stress', 'stress & "strain"');
console.log(JSON.stringify(m));""")
    assert "<script>" not in out["a"] and "&lt;script&gt;alert(1)&lt;/script&gt;" in out["a"]
    assert out["a"].startswith('<mark class="diff">&lt;script&gt;')
    # a lone "&" has no word to miss, so only "strain" is marked
    assert _unsame(out["b"]) == 'stress &amp; <mark class="diff">&quot;strain&quot;</mark>'


ABS = "nurses on night shifts reported more work stress than day staff in the survey of four large city hospitals {} {}"


def test_scenario_match_words():
    a, b = ABS.format("during", "winter"), ABS.format("across", "summer")
    out = run_node(f"""const V = DedupView, a = {json.dumps(a)}, b = {json.dumps(b)};
console.log(JSON.stringify({{ title: V.matchWord("Work stress in nurses", "Work Stress in Nurses."),
  doi: V.matchWord("10.1000/x1", ""), volume: V.matchWord("", null), abstract: V.matchWord(a, b), marks: V.markDiff(a, b) }}));""")
    assert [out["title"], out["doi"], out["volume"], out["abstract"]] == ["spelling", "only_a", "missing", "nearly"]
    for side, two in (("a", ("during", "winter")), ("b", ("across", "summer"))):
        html = _unsame(out["marks"][side])
        assert html.count('<mark class="diff">') == 2
        assert all(f'<mark class="diff">{w}</mark>' in html for w in two)
        # every other word is plain text
        assert html.replace(f'<mark class="diff">{two[0]}</mark>', two[0]).replace(
            f'<mark class="diff">{two[1]}</mark>', two[1]) == ABS.format(*two)
    # the mark is drawn with an underline as well as a background
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    rule = css[css.index("mark.diff {"):].split("}")[0]
    assert "underline" in rule and "background" in rule


def test_scenario_marking_does_not_change_the_text():
    out = run_node("""const m = DedupView.markDiff("stress <b>and</b> strain &amp; burnout", "stress <b>and</b> strain &amp; fatigue");
console.log(JSON.stringify(m));""")
    assert _unsame(out["a"]) == 'stress &lt;b&gt;and&lt;/b&gt; strain &amp;amp; <mark class="diff">burnout</mark>'
    assert _unsame(out["b"]) == 'stress &lt;b&gt;and&lt;/b&gt; strain &amp;amp; <mark class="diff">fatigue</mark>'


# ---------------------------------------------------------------------------------------------------------- the page
# just enough of a document: elements by selector (made on first use), focus, keydown listeners, timers held back,
# api() answering from the canned replies R by path
PAGE = r"""
const els = {}, listeners = {}, timers = [], calls = [];
function mk(sel, tag) {
  return { sel, tagName: tag || "DIV", textContent: "", innerHTML: "", hidden: false, disabled: false, checked: false, className: "",
    dataset: {}, value: "", attrs: {}, setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    appendChild(x) { x.parentNode = this; }, addEventListener() {}, scrollIntoView() {}, querySelectorAll() { return []; },
    insertAdjacentHTML(where, html) { this.innerHTML = where === "afterbegin" ? html + this.innerHTML : this.innerHTML + html; },
    querySelector(s) { return document.querySelector(s); }, closest(s) { return document.querySelector(s); },
    contains(x) { return !!x && x !== document.body; },
    focus() { document.activeElement = this; },
    blur() { if (document.activeElement === this) document.activeElement = document.body; },
    classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } } };
}
global.document = { body: mk("body", "BODY"), querySelector(s) { return (els[s] ||= mk(s)); },
  addEventListener(type, fn) { (listeners[type] ||= []).push(fn); }, contains() { return true; } };
for (const s of ["#dw-body", "#dw-notes", "#dw-decide", "#toasts"]) { const e = mk(s); e.contains = (x) => !!x && x.within === s; els[s] = e; }
for (const [s, tag] of [["#dd-search", "INPUT"], ["#dd-openfirst", "INPUT"]]) els[s] = mk(s, tag);
els["#dd-openfirst"].checked = true;
document.activeElement = document.body;
global.window = global; global.addEventListener = () => {}; global.dispatchEvent = () => {};
global.CustomEvent = class { constructor(t) { this.type = t; } };
global.location = { hash: "#dedup" };
const ls = {};
global.localStorage = { getItem: (k) => (k in ls ? ls[k] : null), setItem: (k, v) => { ls[k] = String(v); }, removeItem: (k) => { delete ls[k]; } };
global.setTimeout = (fn, ms) => { const t = [fn, ms]; timers.push(t); return t; };
global.clearTimeout = (t) => { if (t) t[0] = () => {}; };
global.api = async (url, opts) => {
  const method = (opts && opts.method) || "GET", body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push([method, url, body]);
  const h = R[url] !== undefined ? R[url] : R[url.split("?")[0]];
  if (h === undefined) throw new Error("no canned answer for " + url);
  const v = typeof h === "function" ? await h(url, body) : h;
  return JSON.parse(JSON.stringify(v));
};
let tb = null, stagesRefreshed = 0;
global.Topbar = { set(k, c) { tb = c; }, redraw() {} };
global.refreshStages = async () => { stagesRefreshed++; };
global.afterAction = () => DedupView.load();
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise((r) => setImmediate(r)); };
const key = (k, mod) => ({ key: k, metaKey: false, ctrlKey: !!mod, shiftKey: false, altKey: false, prevented: false, preventDefault() { this.prevented = true; } });
// the docked pair takes its keys only while the focus is in the list or the pair (WCAG 2.1.4): a press starts there
const press = async (k, mod) => { if (document.activeElement === document.body) document.querySelector("#dd-list").focus();
  for (const fn of listeners.keydown || []) fn(key(k, mod)); await settle(); };
const pause = async () => { timers.splice(0).forEach(([fn]) => fn()); await settle(); };
const html = (s) => document.querySelector(s).innerHTML;
const text = (s) => document.querySelector(s).textContent;
// a click on a panel button: the target finds the one marked element it was asked for
const target = (attr, value) => ({ closest: (s) => (s === `[${attr}]` ? { dataset: { [attr.replace("data-", "")]: value }, disabled: false } : null) });
let opened = null;
""" + WIDER_NAME


def page_scripts() -> str:
    common = common_esc()
    shell = (STATIC / "shell.js").read_text(encoding="utf-8")
    status = shell[shell.index("  const ICON ="):shell.index("  // ------------------------------------------------------------------ sidebar")]
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    cards = app[app.index("// ---------------- stage results"):app.index("function renderStageResult")]
    drawer = (STATIC / "drawer.js").read_text(encoding="utf-8")
    history = (STATIC / "history.js").read_text(encoding="utf-8")
    dedup = (STATIC / "dedup.js").read_text(encoding="utf-8")
    card = (STATIC / "settings_card.js").read_text(encoding="utf-8")
    # the row opener drawer.js binds to the list, kept so a test can open row i as a click does
    hook = "const _bind = Drawer.bindRows; Drawer.bindRows = (t, fn) => { opened = fn; return _bind(t, fn); };"
    return "\n".join([common, "const h = esc;", status, "let LAST_RESULTS = {}; const STAGES = [];", cards, drawer, hook, history, card, dedup])


MSG_OPEN = "3 pairs need your decision. The record count stays provisional until every pair is decided."
MSG_DONE = "Every pair is decided. Press Apply decisions and check again to update the counts."


def flow(**kw) -> dict:
    f = dict(has_run=True, imported=20, imported_since_run=0, files=2, removed=5, removed_manual=0, removed_auto=5,
             split_recorded=True, left=15, provisional=True, to_decide=3, decided=0, unsure=0, decided_since=0,
             message=MSG_OPEN)
    f.update(kw)
    return f


def pair(n: int, status: str = "open", by: str | None = None, source=None, **kw) -> dict:
    p = {"pair_id": n, "raw_a": n, "raw_b": n + 10, "stage": "C", "rule": "title", "score": 0.9,
         "title_a": f"Synthetic paper {n} on work stress", "title_b": f"Synthetic paper {n} on work stress.",
         "authors_a": "Doe J", "authors_b": "Doe J", "year_a": 2020, "year_b": 2020, "journal_a": "J Synth",
         "journal_b": "J Synth", "doi_a": f"10.1000/s{n}", "doi_b": "", "abstract_a": "a b c", "abstract_b": "a b d",
         "platform_a": "pubmed", "platform_b": "scopus", "status": status, "signed_by": by, "source": source,
         "reason_words": "Same title"}
    p.update(kw)
    return p


GUIDE = {"page": "dedup", "title": "Deduplication", "notices": [],
         "parts": [{"heading": "What this step is for", "items": ["WIDER finds records imported twice. It merges sure ones."]},
                   {"heading": "What to do now", "items": ["Press Run deduplication.", "Decide each pair."]},
                   {"heading": "Done when", "items": ["No pair is open."]},
                   {"heading": "Watch out", "items": ["Unsure keeps the count provisional."]}]}
RUN = {"headline": "Deduplication: 15 unique records", "action": "dedup run", "ran_at": "2026-10-01T10:00:00",
       "metrics": {"unique_records": 15}, "warnings": ["3 pairs wait for a decision"], "outputs": ["09_prisma_counts.csv"]}


def replies(f: dict | None = None, queue: list | None = None, **extra) -> dict:
    f = f or flow()
    r = {"/api/dedup": {"summary": {"unique_records": f["left"]}, "run_at": "2026-10-01T10:00:00", "prisma": {},
                        "queue": queue if queue is not None else [pair(i) for i in range(1, f["to_decide"] + 1)],
                        "pending": f["to_decide"], "decided_since_run": f["decided_since"], "flow": f},
         "/api/notebook/dedup": [{"at": "2026-10-01T10:00:00", "who": "WIDER", "kind": "run", "text": "Ran deduplication.",
                                  "undone": False, "change_id": None, "result": RUN}],
         "/api/guide/dedup": GUIDE,
         "/api/import/sources": {"sources": [], "final_at": None},
         "/api/dedup/merged": {"rows": [], "total": 0, "approximate": False},
         "/api/dedup/records": {"rows": [], "total": 0},
         "/api/protocol": {"rows": ReviewProtocol().explain(), "sections": [], "hash": "abc", "changed": 0,
                           "effects": {"corpus.thin_words": "Changing the thin-abstract words reruns nothing."}}}
    r.update(extra)
    return r


def run_page(body: str, r: dict, name: str | None = "ana") -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    setup = f"const R = {json.dumps(r)};\n" + (f"ls['wider.reviewer'] = {json.dumps(name)};\n" if name else "")
    script = f"{PAGE}\n{setup}\n{page_scripts()}\n(async () => {{\nawait DedupView.load(); await settle();\n{body}\n}})()" \
             ".catch(e => { console.error(e && e.stack || e); process.exit(1); });"
    res = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def plain(s: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", s).split())


def heading(h: str) -> str:
    """The list heading's words: the box's name and its count line (the count pill and "Shown by the chart" left out)."""
    return plain(re.sub(r'<span class="dd-list-n">.*?</span>', "", h)).replace("Shown by the chart · ", "")


def boxes(flow_html: str) -> dict:
    """Each flow-chart box: its words as read aloud (marks hidden from screen readers left out), pressed and disabled."""
    out = {}
    for m in re.finditer(r'<button[^>]*data-box="(\w+)"[^>]*>(.*?)</button>', flow_html, re.S):
        tag = m.group(0)[:m.group(0).index(">")]
        said = re.sub(r'<span[^>]*aria-hidden="true"[^>]*>.*?</span>', "", m.group(2), flags=re.S)
        out[m.group(1)] = {"text": plain(said), "pressed": 'aria-pressed="true"' in tag, "disabled": " disabled" in tag}
    return out


def test_counts_after_a_run_with_open_pairs():
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow"), msg: text("#dd-msg"), chip: html("#dd-chip"), '
                   'head: plain(html("#dd-list-head")) }));'.replace("plain(", "("), replies())
    b = boxes(out["flow"])
    assert b["imported"]["text"] == "Records imported 20 from 2 database exports"
    assert b["removed"]["text"] == "Duplicates removed 5 (5 by WIDER, 0 by your decisions)"
    assert b["left"]["text"] == "Records left 15 provisional"
    assert b["queue"]["text"] == "Pairs to decide ✓ Showing 3 waiting for you" and b["queue"]["pressed"]
    assert out["msg"] == MSG_OPEN
    # the page chip: "Needs you" (owner, 29 Oct) with an icon hidden from screen readers, in the rose wash (st-wait)
    assert 'class="st-chip st-wait"' in out["chip"] and 'aria-hidden="true"' in out["chip"] and "Needs you" in out["chip"]
    assert heading(out["head"]) == "Pairs to decide 3 open, 0 decided"
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "These counts go into the PRISMA flow diagram of your report." in index
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert ".st-wait" in css and "var(--rose-bg)" in css[css.index(".st-wait"):].split("}")[0]


def test_message_after_deciding_every_pair_and_the_heading_follow_a_decision():
    # the last open pair is decided in the pair view: the message, the chart, the heading and the Notebook count change
    # without a reload, the stored warnings stay under Details, the list keeps its rows and the sidebar is refreshed
    f1, f2 = flow(to_decide=1, message="1 pair needs your decision."), flow(to_decide=0, decided=1, decided_since=1, message=MSG_DONE)
    r = replies(f1)
    out = run_page(r"""
const before = { details: html("#dd-result"), table: html("#dd-runtable"), count: tb.notebook.count(), list: html("#dd-list") };
R["/api/dedup/decide"] = { ok: true, change_id: 41 };
R["/api/dedup"] = Object.assign({}, R["/api/dedup"], { flow: F2, queue: [Object.assign({}, R["/api/dedup"].queue[0], { status: "manual_same", signed_by: "ana" })] });
R["/api/notebook/dedup"] = [{ at: "2026-10-01T11:00:00", who: "ana", kind: "decision", text: "Pair 1 and 11 marked duplicate.", undone: false, change_id: 41 }].concat(R["/api/notebook/dedup"]);
stagesRefreshed = 0;
await press("1");
console.log(JSON.stringify({ before, msg: text("#dd-msg"), flow: html("#dd-flow"), head: html("#dd-list-head"),
  details: html("#dd-result"), table: html("#dd-runtable"), count: tb.notebook.count(), stages: stagesRefreshed,
  status: html('tr[data-key="1-11"] .dd-status'), sent: calls.filter((c) => c[0] === "POST") }));""".replace("F2", json.dumps(f2)), r)
    # key 1 is "Duplicates, keep A" (P8): a duplicate that keeps raw record A
    assert out["sent"] == [["POST", "/api/dedup/decide", {"raw_a": 1, "raw_b": 11, "decision": "duplicate", "by": "ana", "keep": 1}]]
    assert out["msg"] == MSG_DONE
    assert boxes(out["flow"])["queue"]["text"].startswith("Pairs to decide ✓ Showing 0")
    assert heading(out["head"]) == "Pairs to decide 0 open, 1 decided"
    assert out["before"]["count"] == 1 and out["count"] == 2 and out["stages"] >= 1
    # the row's status cell reads the decision; the warnings of the run are still under Details
    assert "Duplicate (you)" in out["status"]
    assert "3 pairs wait for a decision" in out["details"] and out["details"] == out["before"]["details"]
    assert out["table"] == out["before"]["table"]


def test_a_run_from_before_the_split_was_stored():
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow") }));', replies(flow(split_recorded=False, removed_auto=None, removed_manual=None)))
    assert boxes(out["flow"])["removed"]["text"] == "Duplicates removed 5 split not recorded"


def test_files_imported_after_the_run():
    msg = "2 records were imported since the last run. Press Apply decisions and check again to include them."
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow"), msg: text("#dd-msg") }));',
                   replies(flow(imported_since_run=2, to_decide=0, message=msg)))
    assert boxes(out["flow"])["imported"]["text"] == "Records imported 20 from 2 database exports +2 more imported since, not checked for duplicates yet"
    assert out["msg"] == msg


def test_before_the_first_run_only_records_imported_has_a_number():
    f = flow(has_run=False, removed=0, left=0, to_decide=0, provisional=False, message="Run deduplication to find records imported twice.")
    src = {"sources": [{"platform": "pubmed", "file": "synthetic_a.ris", "records": 12, "imported_at": "2026-10-01T09:00:00", "query_version": 2},
                       {"platform": "scopus", "file": "synthetic_b.ris", "records": 8, "imported_at": "2026-10-01T09:05:00", "query_version": None}],
           "final_at": None}
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow"), msg: text("#dd-msg"), main: tb.main.label(), '
                   'head: html("#dd-list-head"), list: html("#dd-list"), cont: document.querySelector("#dd-continue").hidden }));',
                   replies(f, queue=[], **{"/api/import/sources": src}))
    b = boxes(out["flow"])
    assert b["imported"]["pressed"] and b["imported"]["text"].startswith("Records imported ✓ Showing 20")
    for k in ("removed", "left", "queue"):
        assert b[k]["disabled"] and " – " in f" {b[k]['text']} " and not b[k]["pressed"]
    assert "Run deduplication" in out["msg"] and out["main"] == "Run deduplication" and out["cont"] is True
    assert heading(out["head"]) == "Records imported 2 files that deduplication reads"


def test_dedup_uploads_card_is_the_records_imported_list():
    # two synthetic exports, the second imported before query versions were recorded: read only, no button or checkbox
    src = {"sources": [{"platform": "pubmed", "file": "synthetic_a.ris", "records": 12, "imported_at": "2026-10-01T09:00:00", "query_version": 2},
                       {"platform": "scopus", "file": "synthetic_b.ris", "records": 8, "imported_at": "2026-10-01T09:05:00", "query_version": None}],
           "final_at": None}
    src["sources"][0]["file"] = "a<b>.ris"
    out = run_page('DedupView.pressBox("imported"); await settle();\nconsole.log(JSON.stringify({ list: html("#dd-list"), flow: html("#dd-flow"), '
                   'when: fmtWhen("2026-10-01T09:00:00") }));', replies(**{"/api/import/sources": src}))
    rows = re.findall(r"<tr><td>.*?</tr>", out["list"])
    assert len(rows) == 2 and "v2" in rows[0] and "not recorded" in rows[1]
    assert "a&lt;b&gt;.ris" in rows[0] and out["when"] in rows[0] and "T09:00" not in rows[0]
    assert "<button" not in out["list"] and "checkbox" not in out["list"]
    assert boxes(out["flow"])["imported"]["text"].startswith("Records imported ✓ Showing 20 from 2 database exports")
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'<a class="btn-quiet[^"]*"[^>]*href="#import"[^>]*>Add or change uploads</a>', index)


def test_pressing_a_box_and_opening_on_the_right_box():
    merged = {"rows": [{"a": 1, "b": 2, "manual": False, "by": None, "at": "2026-10-01T10:00:00", "reason": "Same DOI",
                        "text": "Removed by WIDER on 1 Oct 2026: Same DOI", "title_a": "T", "title_b": "T"}], "total": 5, "approximate": False}
    out = run_page(r"""
DedupView.pressBox("removed"); await settle();
console.log(JSON.stringify({ flow: html("#dd-flow"), head: html("#dd-list-head"), focus: document.activeElement.sel }));""",
                   replies(**{"/api/dedup/merged": merged}))
    b = boxes(out["flow"])
    assert b["removed"]["pressed"] and "✓ Showing" in b["removed"]["text"]
    assert [k for k, v in b.items() if v["pressed"]] == ["removed"] and sum("Showing" in v["text"] for v in b.values()) == 1
    assert heading(out["head"]).startswith("Duplicates removed 5 merged pairs")
    assert out["focus"] == '[data-box="removed"]'   # the redrawn box keeps the focus
    # no open pair after a run: the page opens on Records left
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow") }));', replies(flow(to_decide=0, decided=3), queue=[]))
    assert [k for k, v in boxes(out["flow"]).items() if v["pressed"]] == ["left"]


def test_an_unsure_pair():
    msg = "15 records left, provisional while 1 pair is Unsure."
    out = run_page(r"""
DedupView.pressBox("queue"); await settle();
console.log(JSON.stringify({ flow: html("#dd-flow"), head: html("#dd-list-head"), list: html("#dd-list"), msg: text("#dd-msg") }));""",
                   replies(flow(to_decide=0, decided=1, unsure=1, message=msg), queue=[pair(1, "manual_uncertain", "ana")]))
    b = boxes(out["flow"])
    assert b["queue"]["text"].startswith("Pairs to decide ✓ Showing 0 1 unsure") and b["left"]["text"] == "Records left 15 provisional"
    assert heading(out["head"]) == "Pairs to decide 0 open, 1 decided"
    assert "Unsure (you)" in out["list"] and out["msg"] == msg


def test_decided_pairs_stay_in_the_list_with_their_signers():
    # open, decided by ana (the current name) and by ben, one decided before the last run: all four rows stay
    q = [pair(1), pair(2, "manual_same", "ana"), pair(3, "manual_uncertain", "ben"), pair(4, "manual_different", "ana")]
    out = run_page('console.log(JSON.stringify({ list: html("#dd-list"), head: html("#dd-list-head") }));',
                   replies(flow(to_decide=1, decided=3), queue=q))
    cells = [plain(c) for c in re.findall(r'<span class="dd-status">(.*?)</span></div></td>', out["list"], re.S)]
    assert cells == ["Open", "Duplicate (you)", "Unsure (ben)", "Not duplicate (you)"]
    assert out["list"].count('aria-hidden="true"') == 4   # each status with its icon
    assert heading(out["head"]) == "Pairs to decide 1 open, 3 decided"


def test_a_decision_copied_from_another_pair():
    q = [pair(3, "manual_same", "ana", [3, 7], raw_b=9)]
    out = run_page('DedupView.pressBox("queue"); await settle();\nconsole.log(JSON.stringify({ list: html("#dd-list") }));',
                   replies(flow(to_decide=0, decided=1), queue=q), name="ben")
    assert "Duplicate (copied from raw records 3 and 7, ana)" in out["list"]


def test_open_first_and_search_by_title_words():
    q = [pair(1, "manual_same", "ana"), pair(2), pair(3, title_a="Night shifts and sleep", title_b="Night shifts and sleep")]
    out = run_page(r"""
const order = () => [...html("#dd-list").matchAll(/data-key="([^"]+)"/g)].map((m) => m[1]);
const r = { first: order() };
document.querySelector("#dd-openfirst").checked = false; document.querySelector("#dd-openfirst").onchange(); await settle();
r.asQueued = order();
document.querySelector("#dd-search").value = "night SHIFTS"; document.querySelector("#dd-search").oninput(); await settle();
r.search = order();
console.log(JSON.stringify(r));""", replies(flow(to_decide=2, decided=1), queue=q))
    assert out["first"] == ["2-12", "3-13", "1-11"] and out["asQueued"] == ["1-11", "2-12", "3-13"] and out["search"] == ["3-13"]


def test_every_status_has_a_word_and_an_icon():
    q = [pair(1), pair(2, "manual_same", "ana"), pair(3, "manual_different", "ben"), pair(4, "manual_uncertain", "ben")]
    out = run_page(r"""
const keys = ["open", "wait", "dup", "notdup", "unsure", "wider", "done", "needs", "blocked"];
console.log(JSON.stringify({ list: html("#dd-list"), chips: keys.map((k) => [k, Status.chip(k), Status.word(k), Status.icon(k)]),
  removed: DedupView.rowsHtml("removed", [{ a: 1, b: 2, manual: false, reason: "Same DOI" }], "ana") }));""",
                   replies(flow(to_decide=1, decided=3), queue=q))
    rows = re.findall(r"<tr .*?</tr>", out["list"], re.S) + re.findall(r"<tr .*?</tr>", out["removed"], re.S)
    assert len(rows) == 5
    for row in rows:   # every list-row mark: a word and an icon hidden from screen readers
        chip = re.search(r'<span class="st-chip[^"]*">(.*?)</span></span>', row, re.S).group(0)
        assert '<svg class="st-icon"' in chip and 'aria-hidden="true"' in chip and plain(chip)
    words, icons = set(), set()
    for k, chip, word, icon in out["chips"]:
        assert 'aria-hidden="true"' in chip and word and plain(chip) == word and icon
        words.add(word)
        icons.add(icon)
    assert len(words) == len(out["chips"]) and len(icons) == len(out["chips"])   # no two share a word or an icon


def test_the_pair_view_header_buttons_and_signing_name():
    q = [pair(i) for i in range(1, 13)]
    out = run_page(r"""
opened(1); await settle();
const r = { body: html("#dw-body"), decide: html("#dw-decide"), notesHidden: document.querySelector(".dw-keys-notes").hidden,
  decideHidden: document.querySelector(".dw-keys-decide").hidden, esc: text(".dw-keys-esc"),
  prev: document.querySelector("#dw-prev").disabled, next: document.querySelector("#dw-next").disabled };
R["/api/dedup/decide"] = { ok: true, change_id: 9 };
await press("4");
r.sent = calls.filter((c) => c[0] === "POST"); r.status = html('tr[data-key="2-12"] .dd-status'); r.msg = text("#dw-dec-msg");
console.log(JSON.stringify(r));""", replies(flow(to_decide=12, message="12 pairs need your decision."), queue=q))
    body = plain(out["body"])
    assert body.startswith("Pair 2 of 12 Matched on: Same title Open") and "Matched on: Same title" in body
    # keyboard-only persona F3: the docked drawer (aria-labelledby="dw-title") is named by the pair's first title
    assert 'aria-label="Synthetic paper 2 on work stress, pair 2 of 12"' in out["body"]
    assert "Are these the same paper?" in body and "Deciding as ana" in body
    assert "Spelling differs" in body and "Only in A" in body   # each field with its match word
    assert out["prev"] is False and out["next"] is False
    # P8: four big buttons, key first, each with its colour (keep A pink, keep B purple, Not duplicates green,
    # Unsure yellow); no note or tag keys for a pair
    assert re.findall(r'data-tone="(\w+)"[^>]*data-dec="\d"[^>]*><kbd>(\d)</kbd> ([^<]+)</button>', out["decide"]) == [
        ("a", "1", "Duplicates, keep A"), ("b", "2", "Duplicates, keep B"), ("not", "3", "Not duplicates"), ("unsure", "4", "Unsure")]
    assert re.findall(r'aria-pressed="(\w+)"', out["decide"]) == ["false"] * 4   # an open pair: none chosen yet
    assert out["notesHidden"] is True and out["decideHidden"] is False and out["esc"] == "back to the list"
    assert out["sent"] == [["POST", "/api/dedup/decide", {"raw_a": 2, "raw_b": 12, "decision": "uncertain", "by": "ana", "keep": None}]]
    assert "Unsure (you)" in out["status"] and 'aria-hidden="true"' in out["status"] and out["msg"] == "saved: unsure"



def test_a_decided_pair_presses_its_kept_record():
    # P8: a duplicate that kept B presses "Duplicates, keep B"; one decided without a kept record (before P8, or by
    # the CLI without --keep) presses neither keep button
    q = [pair(1, "manual_same", "ana", diagnostics={"keep": 11}), pair(2, "manual_same", "ana", diagnostics={})]
    out = run_page(r"""
opened(0); await settle(); const one = html("#dw-decide");
opened(1); await settle();
console.log(JSON.stringify({ one, two: html("#dw-decide") }));""", replies(flow(to_decide=2), queue=q))
    assert re.findall(r'aria-pressed="(\w+)"', out["one"]) == ["false", "true", "false", "false"]
    assert re.findall(r'aria-pressed="(\w+)"', out["two"]) == ["false"] * 4

def test_duplicate_pair_view_signs_with_the_stored_name():
    # ui-redesign D33: the pair view signs with the name WIDER works as and has no Change link or name field of its own
    q = [pair(i) for i in range(1, 4)]
    out = run_page(r"""
opened(0); await settle();
const r = { body: html("#dw-body") };
R["/api/dedup/decide"] = { ok: true, change_id: 9 };
await press("1");
r.sent = calls.filter((c) => c[0] === "POST").map((c) => c[2].by);
console.log(JSON.stringify(r));""", replies(flow(to_decide=3), queue=q))
    assert "Deciding as <b>ana</b>" in out["body"]
    for gone in ("Change", "data-change-name", "dw-author", "Your name", "<input"):
        assert gone not in out["body"], gone
    assert out["sent"] == ["ana"]


def test_duplicate_pair_view_without_a_name_points_at_change():
    # no name: the view says so with a Set your name button; a decision throws needName, posts nothing and the drawer
    # shows WiderName.NEED; once a first name is set (no reload) the next key signs with it
    out = run_page(r"""
let cfg = null; const _open = Drawer.open; Drawer.open = (c) => { cfg = c; return _open(c); };
opened(0); await settle();
const r = { body: html("#dw-body"), notes: document.querySelector("#dw-notes").hidden };
let thrown = null;
try { await cfg.decisions[0].fn(cfg.items[0]); } catch (e) { thrown = { needName: !!e.needName, msg: e.message }; }
r.thrown = thrown;
R["/api/dedup/decide"] = { ok: true, change_id: 7 };
await press("1");
r.noName = { calls: calls.filter((c) => c[0] === "POST").length, msg: html("#dw-dec-msg") };
WiderName.set("ana");
await press("1");
r.sent = calls.filter((c) => c[0] === "POST");
console.log(JSON.stringify(r));""", replies(flow(to_decide=2)), name=None)
    body = plain(out["body"])
    assert "No name yet" in body and "data-set-name>Set your name</button>" in out["body"]
    assert "Deciding as" not in body and "dw-author" not in out["body"] and out["notes"] is True
    assert out["thrown"] == {"needName": True, "msg": "enter your name first: use Change beside “Working as” in the top bar"}
    # no decision is sent without a name (no prompt()); the drawer points at Change with a Set your name button
    assert out["noName"]["calls"] == 0
    assert "enter your name first: use Change beside" in out["noName"]["msg"] and "data-set-name" in out["noName"]["msg"]
    assert out["sent"] == [["POST", "/api/dedup/decide", {"raw_a": 1, "raw_b": 11, "decision": "duplicate", "by": "ana", "keep": 1}]]
    js = (STATIC / "dedup.js").read_text(encoding="utf-8")
    assert "prompt(" not in js


def test_a_merged_pair_is_read_only():
    merged = {"rows": [
        {"a": 1, "b": 2, "manual": False, "by": None, "at": "2026-10-01T10:00:00", "reason": "Same DOI",
         "text": "Removed by WIDER on 1 Oct 2026: Same DOI", "title_a": "T1", "title_b": "T1"},
        {"a": 3, "b": 9, "manual": True, "by": "ana", "at": "2026-10-02T10:00:00", "reason": "your decision",
         "text": "Removed by your decision (copied from raw records 3 and 7, ana, 2 Oct 2026)", "title_a": "T2", "title_b": "T2"}],
        "total": 2, "approximate": True}
    out = run_page(r"""
DedupView.pressBox("removed"); await settle();
const r = { approx: [document.querySelector("#dd-approx").hidden, text("#dd-approx")], list: html("#dd-list") };
opened(1); await settle();
r.body = html("#dw-body"); r.decide = html("#dw-decide"); r.keysHidden = document.querySelector(".dw-keys-decide").hidden;
await press("1"); await press("3");
r.sent = calls.filter((c) => c[0] === "POST");
console.log(JSON.stringify(r));""", replies(**{"/api/dedup/merged": merged}))
    assert out["approx"] == [False, "(approximate: this run was made before WIDER kept its list; run deduplication again for an exact list)"]
    rows = re.findall(r"<tr .*?</tr>", out["list"], re.S)
    assert len(rows) == 2 and "Same DOI" in rows[0] and "Removed by your decision" in rows[1] and 'class="dd-you">You' in rows[1]
    assert "Removed by your decision (copied from raw records 3 and 7, ana, 2 Oct 2026)" in out["body"]
    assert "Removed pair 2 of 2" in out["body"] and "Read only." in out["body"]
    assert "data-dec" not in out["decide"] and out["keysHidden"] is True and out["sent"] == []


def test_records_left_list():
    recs = {"rows": [{"record_id": 1, "title": "Merged synthetic paper", "authors": "Doe J", "year": 2020, "journal": "J", "n_raw": 2, "authors_short": "Doe"},
                     {"record_id": 2, "title": "Single synthetic paper", "authors": "Roe K", "year": 2021, "journal": "J", "n_raw": 1, "authors_short": "Roe"}],
            "total": 14}
    out = run_page('console.log(JSON.stringify({ list: html("#dd-list"), head: html("#dd-list-head"), more: [document.querySelector("#dd-more").hidden, text("#dd-more")], shown: text("#dd-shown"), body: html("#dw-body") }));',
                   replies(flow(to_decide=0, decided=3), queue=[], **{"/api/dedup/records": recs}))
    assert "from 2 imported records" in out["list"] and "from 1 imported record<" in out["list"]
    assert heading(out["head"]) == "Records left 14 records"
    assert out["more"] == [False, "Show 12 more"] and out["shown"] == "2 of 14 shown"
    assert "Read only." in out["body"] and "made from 2 imported records" in out["body"]


NOTEBOOK = [
    {"at": "2026-10-03T10:00:00", "who": "ana", "kind": "undo", "text": "Undid: pair 2 and 12 marked duplicate.", "undone": False, "change_id": 13},
    {"at": "2026-10-02T12:00:00", "who": "WIDER", "kind": "run", "text": "Ran deduplication: 14 records left.", "undone": False, "change_id": None,
     "result": dict(RUN, headline="Deduplication: 14 unique records")},
    {"at": "2026-10-02T11:00:00", "who": "ana", "kind": "decision", "text": "Pair 2 and 12 marked duplicate.", "undone": True, "change_id": 12},
    {"at": "2026-10-02T10:00:00", "who": "ana", "kind": "decision", "text": "Pair 1 and 11 marked duplicate.", "undone": False, "change_id": 11},
    {"at": "2026-10-01T10:00:00", "who": "WIDER", "kind": "run", "text": "Ran deduplication.", "undone": False, "change_id": None, "result": dict(RUN, action="")}]


def test_the_notebook_opens_undoes_redoes_and_shows_an_earlier_result():
    out = run_page(r"""
const r = { closed: [tb.notebook.expanded(), document.querySelector("#dd-notebook").hidden], count: tb.notebook.count() };
DedupView.togglePanel("notebook");
r.open = [tb.notebook.expanded(), document.querySelector("#dd-notebook").hidden, document.activeElement.sel];
r.panel = html("#dd-notebook");
R["/api/history/11/undo"] = { ok: true, change_id: 14, summary: "undo: pair 1 and 11 (change 11)" };
await DedupView.notebookClick(target("data-undo", "11")); await settle();
r.undoFocus = document.activeElement.sel;
R["/api/history/13/undo"] = () => { throw new Error("change 13 can no longer be undone: the pair was changed by a later run"); };
await DedupView.notebookClick(target("data-undo", "13")); await settle();
r.refusal = text("#dd-nb-msg");
await DedupView.notebookClick(target("data-show", "4")); await settle();
r.shown = html("#dd-notebook"); r.showFocus = document.activeElement.sel;
await DedupView.notebookClick(target("data-latest", "")); await settle();
r.back = html("#dd-notebook"); r.latestFocus = document.activeElement.sel;
r.sent = calls.filter((c) => c[0] === "POST").map((c) => c[1]);
document.querySelector("#dd-notebook").onkeydown({ key: "Escape", preventDefault() {}, stopPropagation() {} });   // Esc inside the panel
r.closedAgain = [tb.notebook.expanded(), document.querySelector("#dd-notebook").hidden, document.activeElement.sel];
console.log(JSON.stringify(r));""", replies(**{"/api/notebook/dedup": NOTEBOOK}))
    assert out["closed"] == [False, True] and out["count"] == 5
    # opening moves the focus into the panel, and Undo, Show and Latest keep it there (not on the page body)
    assert out["open"] == [True, False, "#dd-notebook"]
    assert out["undoFocus"] == out["showFocus"] == out["latestFocus"] == "#dd-notebook"
    entries = re.findall(r'<li class="dd-nb-entry">(.*?)</li>', out["panel"], re.S)
    assert len(entries) == 5
    assert "Redo" in entries[0] and 'class="dd-you">You' in entries[0]
    assert "WIDER" in plain(entries[1]) and "Show this result" in entries[1]
    assert "(undone)" in entries[2] and "data-undo" not in entries[2]
    assert "data-undo=\"11\"" in entries[3] and ">Undo<" in entries[3]
    # undo and redo go through the journal's undo route; a refusal is shown in the journal's words
    assert out["sent"] == ["/api/history/11/undo", "/api/history/13/undo"]
    assert out["refusal"] == "change 13 can no longer be undone: the pair was changed by a later run"
    assert "Earlier result" in out["shown"] and "Deduplication: 15 unique records" in out["shown"] and "Back to the latest result" in out["shown"]
    assert "Earlier result" not in out["back"] and "· ·" not in out["shown"]   # a result without an action has one dot
    assert out["closedAgain"] == [False, True, "#tb-notebook-btn"]



def test_arriving_keeps_the_panel_through_a_refresh_and_leaving_closes_it():
    # the router loads the page before this page's hashchange listener runs: arriving must not mark the page as fresh,
    # or the next refresh (after an Undo in the notebook) would close the panel and drop the focus to the page body
    out = run_page(r"""
const r = {};
location.hash = "#dedup"; DedupView.hashLeft();   // arrived on the page
DedupView.togglePanel("notebook"); await DedupView.load(); await settle();
r.afterRefresh = [document.querySelector("#dd-notebook").hidden, DedupView.state.panel];
location.hash = "#enrich"; DedupView.hashLeft(); location.hash = "#dedup"; DedupView.hashLeft();
await DedupView.load(); await settle();
r.reopened = [document.querySelector("#dd-notebook").hidden, DedupView.state.panel];
console.log(JSON.stringify(r));""", replies(**{"/api/notebook/dedup": NOTEBOOK}))
    assert out["afterRefresh"] == [False, "notebook"]
    assert out["reopened"] == [True, None]


def test_the_guide_is_the_shared_panel():
    """Owner, 29 Oct 2026 (ui-redesign D28): Deduplication has no guide panel of its own; Guide in the top bar is the
    shared one (the page sets none), the lead still comes from the guide, and every refresh after the first draw asks
    the shared guide again, so its notices count the pairs still waiting. The Notebook panel stays the page's own."""
    g = dict(GUIDE, notices=["3 pairs wait for you: decide them in Pairs to decide."])
    out = run_page(r"""
const r = { lead: text("#dd-lead"), guide: tb.guide || null, reloads: 0 };
window.Guide = { reload() { r.reloads += 1; } };
await DedupView.load(); await settle(); r.afterOne = r.reloads;
DedupView.togglePanel("notebook"); r.notebook = [document.querySelector("#dd-notebook").hidden, DedupView.state.panel];
console.log(JSON.stringify(r));""", replies(**{"/api/guide/dedup": g}))
    assert out["lead"] == "WIDER finds records imported twice."
    assert out["guide"] is None   # the page sets no Guide: the shared one is drawn
    assert out["afterOne"] == 1
    assert out["notebook"] == [False, "notebook"]
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="dd-guide"' not in index and 'id="dd-notebook" aria-label="Step notebook" tabindex="-1"' in index


def test_the_main_button_runs_and_shows_errors_beside_the_chart():
    f2 = flow(to_decide=0, decided=3, removed=6, removed_manual=1, left=14, provisional=False, message="Done.")
    out = run_page(r"""
const r = { label: tb.main.label() };
let shown = null; global.showStageResult = (x) => { shown = x; };
R["/api/dedup/run"] = () => { throw new Error("500 Internal Server Error: the run failed"); };
await DedupView.run(); await settle();
r.err = [document.querySelector("#dd-error").hidden, text("#dd-error"), text("#dd-msg")];
R["/api/dedup/run"] = { stage_result: { headline: "done" } };
R["/api/dedup"] = Object.assign({}, R["/api/dedup"], { flow: F2, queue: [] });
await DedupView.run(); await settle();
r.after = [document.querySelector("#dd-error").hidden, html("#dd-flow"), text("#dd-msg"), shown, html("#dd-list-head")];
r.stay = [location.hash, document.querySelector("#dd-continue").hidden, html("#dd-result")];
console.log(JSON.stringify(r));""".replace("F2", json.dumps(f2)), replies())
    assert out["label"] == "Apply decisions and check again"
    assert out["err"] == [False, "500 Internal Server Error: the run failed", MSG_OPEN]
    hidden, fl, msg, shown, head = out["after"]
    assert hidden is True and msg == "Done." and shown == {"headline": "done"}
    b = boxes(fl)
    assert b["removed"]["text"] == "Duplicates removed 6 (5 by WIDER, 1 by your decisions)" and b["left"]["text"].startswith("Records left 14 final")
    assert heading(head) == "Pairs to decide 0 open, 3 decided"   # the box the researcher had stays pressed
    # "Continue after a run": the page stays, the quiet link shows, Details holds the run's tiles and warnings
    assert out["stay"][0] == "#dedup" and out["stay"][1] is False
    assert "3 pairs wait for a decision" in out["stay"][2] and "09_prisma_counts.csv" in out["stay"][2]
    # the page's other buttons are quiet: only the top bar's main button has the main-action look
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = index[index.index('<section id="page-dedup"'):index.index("</section>", index.index('<section id="page-dedup"'))]
    for b_tag in re.findall(r"<(?:button|a)\b[^>]*>", sec):
        if "<button" in b_tag or "btn" in b_tag:
            assert "btn-quiet" in b_tag or "icon-btn" in b_tag, b_tag
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.dd-page button\.continue\s*\{[^}]*background:var\(--card\)", css)


def test_ctrl_z_after_a_decision_keeps_the_same_pair_open():
    q = [pair(1), pair(2), pair(3)]
    out = run_page(r"""
opened(1); await settle();
R["/api/dedup/decide"] = { ok: true, change_id: 21 };
await press("1");
const decided = html('tr[data-key="2-12"] .dd-status');
R["/api/history/undo-last"] = { ok: true, change_id: 22, summary: "undo: pair 2 and 12 (change 21)" };
await press("z", true); await pause();
console.log(JSON.stringify({ decided, sent: calls.filter((c) => c[0] === "POST").map((c) => c[1]), open: Drawer.isOpen(), docked: Drawer.isDocked(),
  current: Drawer.current().raw_a, body: plain(html("#dw-body")), sel: DedupView.state.sel }));""".replace("plain(", "("), replies(queue=q))
    assert "Duplicate (you)" in out["decided"]
    assert out["sent"] == ["/api/dedup/decide", "/api/history/undo-last?author=ana"]
    # the page reloads from the server (the pair is open again there) and the same pair stays selected and docked
    assert out["open"] and out["docked"] and out["current"] == 2 and out["sel"] == "2-12"
    assert plain(out["body"]).startswith("Pair 2 of 3 Matched on: Same title Open")


def test_no_run_selector_and_no_earlier_stage_line_on_dedup():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    start = index.index('<section id="page-dedup"')
    sec = index[start:index.index("</section>", start)]
    assert 'data-runs="dedup"' not in sec and 'data-prev="dedup"' not in sec and 'data-result="dedup"' not in sec
    assert '<a href="#enrich" id="dd-continue" hidden>Continue to Missing abstracts</a>' in sec and "<details" in sec


def test_keys_on_the_docked_view_and_the_selection_after_the_refresh():
    q = [pair(1), pair(2), pair(3)]
    out = run_page(r"""
R["/api/dedup/decide"] = { ok: true, change_id: 31 };
await press("1");
const r = { first: calls.filter((c) => c[0] === "POST").length };
await press("1");   // during the pause: already saved, nothing more is sent
r.second = calls.filter((c) => c[0] === "POST").length; r.msg = text("#dw-dec-msg");
await pause();
r.afterPause = [Drawer.current().raw_a, DedupView.state.sel];
await DedupView.load(); await settle();   // the page's refresh from the server after an action
r.afterLoad = [Drawer.isDocked(), Drawer.current().raw_a, DedupView.state.sel, html("#dw-body")];
console.log(JSON.stringify(r));""".replace("plain(", "("), replies(queue=q))
    assert out["first"] == 1 and out["second"] == 1 and "nothing more was decided" in out["msg"]
    assert out["afterPause"] == [2, "2-12"]
    assert out["afterLoad"][:3] == [True, 2, "2-12"] and plain(out["afterLoad"][3]).startswith("Pair 2 of 3")


def test_the_pair_history_under_the_pair_view_marks_wider_and_you():
    """The opened pair asks for its own history by its raw ids; the run's entry reads "WIDER", ana's own decision
    "You" while the name is ana, and ben's decision of another pair is not asked for or shown."""
    q = [pair(1), pair(2)]
    hist = [{"at": "2026-10-01T10:00:00", "who": "WIDER", "kind": "run", "text": "Left for you to decide: same title.",
             "undone": False, "change_id": None},
            {"at": "2026-10-02T09:00:00", "who": "ana", "kind": "decision", "text": "Duplicate.", "undone": False,
             "change_id": 5}]
    out = run_page(r"""
opened(0); await settle();
console.log(JSON.stringify({ list: html("#dd-hist-list"), hidden: document.querySelector("#dd-hist").hidden,
  asked: calls.filter((c) => String(c[1]).startsWith("/api/notebook/dedup?")).map((c) => c[1]) }));""",
                   replies(flow(to_decide=2), queue=q, **{"/api/notebook/dedup?a=1&b=11": hist}))
    # the page opens the first pair on load and again on the click: each time only this pair's history is asked for
    assert set(out["asked"]) == {"/api/notebook/dedup?a=1&b=11"} and out["hidden"] is False
    text = plain(out["list"])
    assert "WIDER" in text and "Left for you to decide: same title." in text and "You" in text and "Duplicate." in text
    assert "ben" not in text and text.index("WIDER") < text.index("You")


def test_flow_shows_file_excluded_only_above_zero():
    # Scopus files of 10 and 4 records, the second kept out: the box counts the included file's records (D5, F6)
    src = {"sources": [{"source_id": 1, "platform": "scopus", "file": "a.ris", "records": 10, "imported_at": "2026-10-01T09:00:00",
                        "query_version": 1, "included": True},
                       {"source_id": 2, "platform": "scopus", "file": "b.ris", "records": 4, "imported_at": "2026-10-01T09:05:00",
                        "query_version": 1, "included": False}], "final_at": None}
    f = flow(imported=10, files=1, excluded_file_records=4, removed=1, removed_auto=1, left=9, to_decide=0,
             provisional=False, message=MSG_DONE)
    out = run_page('DedupView.pressBox("imported"); await settle();\nconsole.log(JSON.stringify({ flow: html("#dd-flow"), '
                   'list: html("#dd-list"), head: html("#dd-list-head") }));', replies(f, **{"/api/import/sources": src}))
    assert boxes(out["flow"])["imported"]["text"] == "Records imported ✓ Showing 10 from 1 database export file excluded: 4"
    rows = re.findall(r"<tr><td>.*?</tr>", out["list"])
    assert "(file excluded: not used)" in rows[1] and "file excluded" not in rows[0]
    # nothing excluded: no such line
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow") }));', replies(flow(excluded_file_records=0)))
    assert "file excluded" not in boxes(out["flow"])["imported"]["text"]
    out = run_page('console.log(JSON.stringify({ flow: html("#dd-flow") }));', replies())
    assert boxes(out["flow"])["imported"]["text"] == "Records imported 20 from 2 database exports"


def test_every_setting_visible():
    """Every dedup.* field on the page in a named section with its explanation and default; no "Advanced" fold."""
    out = run_page("console.log(JSON.stringify({ s: html('#dd-settings') }));", replies())
    s, words = out["s"], plain(out["s"])
    for name in ("Fuzzy title thresholds", "DOI conflicts", "Short titles"):
        assert f"<h3>{name}</h3>" in s
    rows = [r for r in ReviewProtocol().explain() if r["section"] == "dedup"]
    assert rows
    for r in rows:
        assert f'data-ps-set="{r["path"]}"' in s and r["description"] in words and f"default: {r['default']}" in words, r["path"]
    assert "Other settings" not in s and "thin_words" not in s and "Advanced" not in s
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    sec = page[page.index('<section id="page-dedup"'):page.index('<section id="page-enrich"')]
    assert 'id="dd-settings"' in sec and not re.search(r"<summary>[^<]*Advanced", sec)


def test_list_numbers_are_the_pair_counter_numbers():
    """D32 F22: the list numbers each row by its place in the shown list, so row N opens as "Pair N of M", also when
    Open first moves a decided pair to the end."""
    q = [pair(1, "manual_same", "ana"), pair(2), pair(3)]
    out = run_page(r"""
const nums = () => [...html("#dd-list").matchAll(/data-key="([^"]+)"><td class="num">(\d+)</g)].map((m) => [m[1], m[2]]);
const r = { nums: nums() };
opened(2); await settle();
r.body = plain(html("#dw-body"));
console.log(JSON.stringify(r));""".replace("plain(", "("), replies(flow(to_decide=2, decided=1), queue=q))
    assert out["nums"] == [["2-12", "1"], ["3-13", "2"], ["1-11", "3"]]
    assert plain(out["body"]).startswith("Pair 3 of 3")
