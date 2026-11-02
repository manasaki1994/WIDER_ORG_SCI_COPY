"""review.js runs under Node (no browser): the Screening review page draws the replies of `/api/review*` (the same
dicts `wider review list|show|criteria --json` print) with the engine's sentences; keys 1/2/3 and j/k, the toast's
Undo, the failed-read link, the signing line, the Criteria panel and the quality-control dialog. The replies come
from the synthetic scenario of test_review through the real routes, so the page and the CLI read one dict."""
import json
import re

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from tests.unit.test_review import client, proj, scenario  # noqa: F401 - pytest fixture
from wider.core import journal
from wider.core.questions import answer
from wider.screen import review as rv
from wider.screen.decisions import set_decision

REVIEW = (STATIC / "review.js").read_text(encoding="utf-8")
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
COMMON = (STATIC / "common.js").read_text(encoding="utf-8")
NAME = COMMON[COMMON.index("// --- WiderName ---"):COMMON.index("// --- end WiderName ---")]   # the one name (D33)

SETUP = r"""
let currentPage = "review";
global.location = { hash: __HASH__, reloads: 0, reload() { this.reloads++; } };
const docEvents = {};   // document events: the page listens for "widername" when a first name is set
document.addEventListener = (t, f) => { (docEvents[t] ||= []).push(f); };
document.dispatchEvent = (e) => { (docEvents[e.type] || []).forEach((f) => f(e)); };
global.CustomEvent = class { constructor(t, o) { this.type = t; this.detail = o && o.detail; } };
global.history = { replaceState(a, b, h) { location.hash = h; } };
const LS = __STORE__;
global.localStorage = { getItem: (k) => (k in LS ? LS[k] : null), setItem: (k, v) => { LS[k] = String(v); },
  removeItem: (k) => { delete LS[k]; } };
const undos = []; global.offerUndo = (id, text) => undos.push([id, text]);
document.querySelector("#rv-qc-dialog").hidden = true;   // as index.html has it
Object.assign(replies, __REPLIES__);
const posts = () => calls.filter((c) => c[1] === "POST");
const rowIds = () => [...String($("#rv-list").innerHTML).matchAll(/<tr data-rv-row="\d+"[^>]*>\s*<td>(\d+)<\/td>/g)].map((m) => Number(m[1]));
const key = async (k, target) => { await Review.onKey({ key: k, target: target || { tagName: "TR" } }); await flush(); };
"""


def page(body: str, replies: dict, *, pre: str = "", hash_: str = "#review", store: dict | None = None) -> dict:
    """review.js on the stand-in page with ``replies``; ``pre`` runs before the page loads, ``body`` after."""
    setup = (SETUP.replace("__HASH__", json.dumps(hash_))
             .replace("__STORE__", json.dumps({"wider.reviewer": "ana"} if store is None else store))
             .replace("__REPLIES__", json.dumps(replies)))
    return run_node(f"{PAGE}\n{ESC}\n{setup}\n{pre}\n{NAME}\n{REVIEW}\nawait loadScreeningReview(); await flush();\n{body}")


def text(html: str) -> str:
    html = re.sub(r"</?(b|span|a|kbd)\b[^>]*>", "", html)   # inline tags join their text, as the browser shows it
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html)).replace("&#39;", "'").replace("&amp;", "&").replace(
        "&quot;", '"').strip()


def replies_of(p, *records, show: str = "flagged") -> dict:
    """The routes' replies for the default set: the list (with ``show``), the criteria and the given records."""
    c = client(p)
    out = {"/api/review": c.get("/api/review", params={"show": show}).json(),
           "/api/review/criteria": c.get("/api/review/criteria").json()}
    for rid in records:
        out[f"/api/review/{rid}"] = c.get(f"/api/review/{rid}").json()
    return out


def section() -> str:
    a = INDEX.index('<section id="page-review"')
    return INDEX[a:INDEX.index("</section>", a)]


# ------------------------------------------------------------------------------------------------- 3.2
def test_review_list_and_filter(proj):  # noqa: F811
    """The list holds the route's rows (those of `wider review list --json`); Show "all" asks for them again."""
    scenario(proj)
    r = replies_of(proj, 4, 5)
    r_all = client(proj).get("/api/review", params={"show": "all"}).json()
    out = page(f"""
      const first = rowIds(), count = $("#rv-count").textContent;
      replies["/api/review"] = {json.dumps(r_all)};
      $("#rv-show").value = "all"; await $("#rv-show").onchange(); await flush();
      console.log(JSON.stringify({{ first, count, all: rowIds(), url: urls().filter((u) => u.startsWith("/api/review?")).pop() }}));""", r)
    assert out["first"] == [x["record_id"] for x in rv.flagged(proj)["rows"]] == [4, 5, 6, 8, 9]
    assert out["count"] == "5 records need a person, 4 undecided"
    assert out["all"] == list(range(1, 10))
    assert out["url"] == "/api/review?protocol=main&set=1&show=all"


def test_review_lists_all_when_none_flagged(proj):  # noqa: F811
    """Nothing needs a person but the set has records: the page lists every record (show "all") and says why, rather
    than an empty list (owner walkthrough, 31 Oct 2026); the first request still asks for the flagged ones."""
    scenario(proj)
    c = client(proj)
    flagged = {**c.get("/api/review").json(), "rows": [], "count_line": "0 records need a person, 0 undecided"}
    r_all = c.get("/api/review", params={"show": "all"}).json()
    r = {"/api/review/criteria": c.get("/api/review/criteria").json(), "/api/review/4": c.get("/api/review/4").json()}
    pre = f"""replies["/api/review"] = (u) => u.includes("show=all") ? {json.dumps(r_all)} : {json.dumps(flagged)};"""
    out = page('console.log(JSON.stringify({ rows: rowIds(), count: $("#rv-count").textContent, show: Review.state().show, '
               'first: urls().find((u) => u.startsWith("/api/review?")) }));', r, pre=pre)
    assert out["rows"] == list(range(1, 10)) and out["show"] == "all"
    assert out["first"].endswith("show=flagged")
    assert out["count"] == f"No record needs a person, so every record of this set is listed: {r_all['count_line']}."


def test_review_undo_line_names_last_decision(proj):  # noqa: F811
    """After a decision the record panel's undo line names it with an Undo last button; before any, it says decisions
    are saved as chosen. The decided record's button for its final decision is marked pressed."""
    scenario(proj)
    r = replies_of(proj, 4, 5, 6, 8)
    view5 = r["/api/review/5"]
    pre = f"""replies["/api/review/5"] = (url, opts) => (opts && opts.method === "POST")
      ? {{ ...{json.dumps(view5)}, change_id: 77, final_line: "Final: exclude (researcher ana)" }} : {json.dumps(view5)};"""
    out = page("""const before = $("#rv-drawer").innerHTML;
      await Review.open(1, true); await flush(); await key("2");
      const after = $("#rv-drawer").innerHTML;
      await Review.open(3, true); await flush();
      console.log(JSON.stringify({ before, after, eight: $("#rv-drawer").innerHTML }));""", r, pre=pre)
    assert "Undo last appears here" in text(out["before"]) and "data-rv-undo" not in out["before"]
    assert "Record 5: Final: exclude (researcher ana). Undo last" in text(out["after"]) and "data-rv-undo" in out["after"]
    assert re.search(r'data-rv-decide="include" aria-pressed="true"', out["eight"])   # record 8: decided include by ana

def test_review_empty(proj):  # noqa: F811
    """Nothing screened: one sentence naming the Screening step, with its link; no list."""
    r = {"/api/review": client(proj).get("/api/review").json()}
    out = page('console.log(JSON.stringify({ empty: $("#rv-empty").innerHTML, hidden: $("#rv-empty").hidden, '
               'body: $("#rv-body").hidden, rows: rowIds() }));', r)
    assert not out["hidden"] and out["body"] and out["rows"] == []
    assert text(out["empty"]) == "no screening decisions yet: run the Screening step first."
    assert '<a href="#screen">Screening</a>' in out["empty"]


def test_review_drawer_shows_reads_only(proj):  # noqa: F811
    """Record 3: the three reads with their reasons and models, the final line; no band, rank, score or sample."""
    scenario(proj)
    r = replies_of(proj, 3, 4, show="majority")
    out = page('await Review.open(0, true); await flush(); console.log(JSON.stringify({ d: $("#rv-drawer").innerHTML }));', r)
    t = text(out["d"])
    assert "pass A: include (synthetic-model): fits" in t
    assert "pass B: exclude (synthetic-model): no study" in t
    assert "third read: exclude (synthetic-model): no study" in t
    assert "Final: exclude (settled by the third read)" in t
    for word in ("band", "rank", "score", "sample", "weight"):
        assert word not in t.lower()


def test_review_drawer_without_pass_reads(proj):  # noqa: F811
    """Record 7 was imported: no reads, the stored-reads sentence and its earlier decision."""
    scenario(proj)
    r = replies_of(proj, 1, 7, show="all")
    out = page('await Review.open(6, true); await flush(); console.log(JSON.stringify({ d: $("#rv-drawer").innerHTML }));', r)
    t = text(out["d"])
    assert rv.NO_READS in t and "Earlier decision (imported): exclude" in t and "rv-reads" not in out["d"]


def test_review_keys_move_and_decide(proj):  # noqa: F811
    """j opens the second record, 1 decides it include and the next undecided opens; 2 and 3 send exclude and maybe;
    a digit typed in the Note field decides nothing."""
    scenario(proj)
    r = replies_of(proj, 4, 5, 6)
    view5 = r["/api/review/5"]
    pre = f"""replies["/api/review/5"] = (url, opts) => (opts && opts.method === "POST")
      ? {{ ...{json.dumps(view5)}, change_id: 77, final_line: "Final: " + JSON.parse(opts.body).decision }} : {json.dumps(view5)};"""
    out = page("""
      const opened = Review.state().view.record_id;
      await key("j"); const second = Review.state().view.record_id;
      await key("1", { tagName: "INPUT" }); const typed = posts().length;
      await key("1"); const after = Review.state().view.record_id;
      await key("k"); await key("2"); await key("k"); await key("3");
      console.log(JSON.stringify({ opened, second, typed, after, posts: posts().map((c) => [c[0], c[2]]) }));""", r, pre=pre)
    assert out["opened"] == 4 and out["second"] == 5 and out["typed"] == 0
    assert out["after"] == 6   # the next undecided record
    assert out["posts"] == [["/api/review/5?protocol=main&set=1", {"decision": d, "reason": ""}]
                            for d in ("include", "exclude", "maybe")]


def test_review_keys_only_in_the_list_or_the_record_and_never_twice_while_saving(proj):  # noqa: F811
    """WCAG 2.1.4 (fixes of 1 Nov 2026): 1/2/3 and j/k do nothing while the focus is elsewhere on the page; a key
    pressed while a decision is still being sent decides nothing and says so with a count, in the status line."""
    scenario(proj)
    r = replies_of(proj, 4, 5, 6)
    view4 = r["/api/review/4"]
    pre = f"""replies["/api/review/4"] = (url, opts) => (opts && opts.method === "POST")
      ? {{ ...{json.dumps(view4)}, change_id: 78, final_line: "Final: include" }} : {json.dumps(view4)};"""
    out = page("""
      $("#rv-list").contains = (x) => !!(x && x.inList); $("#rv-drawer").contains = (x) => !!(x && x.inDrawer);
      await key("1", { tagName: "BUTTON" }); await key("j", { tagName: "A" });
      const outside = [posts().length, Review.state().view.record_id];
      const row = { tagName: "TR", inList: true };
      const first = Review.onKey({ key: "1", target: row }), second = Review.onKey({ key: "2", target: { tagName: "BUTTON", inDrawer: true } });
      await second; const msg = $("#rv-msg").textContent; await first; await flush();
      console.log(JSON.stringify({ outside, msg, posts: posts().map((c) => c[2].decision) }));""", r, pre=pre)
    assert out["outside"] == [0, 4]
    assert out["posts"] == ["include"]
    assert out["msg"] == "the last decision is still being saved: nothing more was decided (1 key press ignored)"
    assert 'aria-describedby="row-hint"' in REVIEW and 'id="row-hint"' in INDEX
    assert "Keys while the focus is in the list or the record:" in section()


def test_review_toast_undo(proj):  # noqa: F811
    """After a decision the toast offers Undo of that change; undoing it makes record 5 maybe and undecided again."""
    scenario(proj)
    r = replies_of(proj, 4, 5, 6)
    c = client(proj)
    res = c.post("/api/review/5", json={"decision": "exclude", "reason": ""}, headers={"x-wider-author": "ana"}).json()
    after = c.get("/api/review").json()
    pre = f"""let posted = false; const before = replies["/api/review"];
      replies["/api/review"] = () => posted ? {json.dumps(after)} : before;
      const v5 = replies["/api/review/5"];
      replies["/api/review/5"] = (url, opts) => {{ if (opts && opts.method === "POST") {{ posted = true; return {json.dumps(res)}; }} return v5; }};"""
    out = page('await Review.open(1, true); await flush(); await key("2"); console.log(JSON.stringify({ undos }));', r, pre=pre)
    assert out["undos"] == [[res["change_id"], "Record 5: Final: exclude (researcher ana)"]]
    journal.undo(proj, res["change_id"], other=True)
    assert rv.record_view(proj, None, None, 5)["final"] == "maybe" and rv.flagged(proj)["undecided"] == 4


def test_review_page_says_it_shows_llm_reads(proj):  # noqa: F811
    """The sentence once above the list, in a plain paragraph (normal black text, no muted or small class)."""
    scenario(proj)
    out = page('console.log(JSON.stringify({ s: $("#rv-sentence").textContent }));', replies_of(proj, 4))
    assert out["s"] == rv.SENTENCE
    s = section()
    assert s.count('id="rv-sentence"') == 1 and s.index('id="rv-sentence"') < s.index('id="rv-list"')
    tag = re.search(r'<p id="rv-sentence"[^>]*>', s).group(0)
    assert "muted" not in tag and "small" not in tag


def test_failed_read_links_to_questions(proj):  # noqa: F811
    """Record 6's failed read: the sentence with a link to #questions, no decision buttons, and 1 decides nothing."""
    scenario(proj)
    r = replies_of(proj, 4, 6)
    out = page('await Review.open(2, true); await flush(); await key("1"); '
               'console.log(JSON.stringify({ d: $("#rv-drawer").innerHTML, posts: posts().length }));', r)
    assert rv.FAILED_ROW in text(out["d"]) and 'href="#questions"' in out["d"]
    assert "data-rv-decide" not in out["d"] and out["posts"] == 0


def test_review_protocol_and_set_selects(proj):  # noqa: F811
    """The address's protocol and set are asked for; the selects list the protocols and sets with captions, and
    choosing set 2 asks for it."""
    scenario(proj)
    r = replies_of(proj, 4)
    r["/api/review"]["sets"] = [{"set": 1, "label": "set 1", "active": False}, {"set": 2, "label": "set 2", "active": True}]
    out = page("""
      const first = urls()[0], sets = $("#rv-set").innerHTML, protos = $("#rv-protocol").innerHTML;
      $("#rv-set").value = "2"; await $("#rv-set").onchange(); await flush();
      console.log(JSON.stringify({ first, sets, protos, url: urls().filter((u) => u.startsWith("/api/review?")).pop(), hash: location.hash }));""",
               r, hash_="#review?protocol=main&set=1")
    assert out["first"] == "/api/review?protocol=main&set=1&show=flagged"
    assert text(out["sets"]) == "set 1 set 2 (active)" and text(out["protos"]) == "main"
    assert out["url"] == "/api/review?protocol=main&set=2&show=flagged" and out["hash"] == "#review"
    s = section()
    assert re.search(r'<label class="inline">Protocol <select id="rv-protocol"', s)
    assert re.search(r'<label class="inline">Result set <select id="rv-set"', s)


def test_review_page_says_who_signs(proj):  # noqa: F811
    """With a name: "Signing as ana". Without: the no-name sentence that points at Change in the top bar (WiderName.NEED)
    with a Set your name button, and buttons, keys and the quality-control dialog send nothing; the dialog has no name
    field."""
    scenario(proj)
    r = replies_of(proj, 4, 5)
    body = """
      const btns = [...$("#rv-drawer").querySelectorAll("[data-rv-decide]")];
      await btns[0].onclick(); await flush(); await key("2");
      $("#rv-qc-n").value = "1"; await $("#rv-qc-go").onclick(); await flush();
      console.log(JSON.stringify({ s: $("#rv-signing").innerHTML, posts: posts().length, msg: $("#rv-msg").innerHTML,
                                   qc: $("#rv-qc-msg").innerHTML }));"""
    named = page(body.replace("const btns", "await Review.open(1, true); await flush(); const btns"), r,
                 pre='replies["/api/review/qc"] = { change_id: 1, n: 1, pool_n: 3, seed: 5 };')
    assert text(named["s"]) == "Signing as ana" and named["posts"] >= 1
    blank = page(body, r, store={})
    need = ("no name yet: decisions need your name. enter your name first: use Change beside “Working as” in the top bar."
            " Set your name")
    assert text(blank["s"]) == need and blank["posts"] == 0
    assert text(blank["msg"]) == text(blank["qc"]) == need
    for html in (blank["s"], blank["msg"], blank["qc"]):
        assert 'class="rv-noname"' in html and "data-set-name>Set your name</button>" in html
    dialog = section()[section().index('id="rv-qc-dialog"'):]
    dialog = dialog[:dialog.index("</div>\n      <details")]
    assert re.findall(r"<input id=\"([\w-]+)\"", dialog) == ["rv-qc-n", "rv-qc-seed"]
    # a first name set after the page loaded (the top bar's dialog): the signing line follows at once, no reload
    later = page("""WiderName.set("bo"); await flush();
      console.log(JSON.stringify({ s: $("#rv-signing").innerHTML, reloads: location.reloads }));""", r, store={})
    assert text(later["s"]) == "Signing as bo" and later["reloads"] == 0
    # risk-checker R5: switching to another name reloads the page, so nothing stays signed under the earlier name
    switched = page("""await Review.open(1, true); await flush(); WiderName.set("bo"); await flush();
      console.log(JSON.stringify({ reloads: location.reloads, stored: LS["wider.reviewer"] }));""", r)
    assert switched == {"reloads": 1, "stored": "bo"}


# ------------------------------------------------------------------------------------------------- 3.4
def test_review_rows_and_drawer_say_why(proj):  # noqa: F811
    """Each row and the drawer carry the route's `why` sentence (the one `--show majority --json` holds)."""
    scenario(proj)
    r = replies_of(proj, 3, 4, show="majority")
    out = page('await Review.open(0, true); await flush(); '
               'console.log(JSON.stringify({ l: $("#rv-list").innerHTML, d: $("#rv-drawer").innerHTML }));', r)
    whys = re.findall(r'<td class="rv-why">([^<]*)</td>', out["l"])
    assert whys == [x["why"] for x in rv.flagged(proj, show="majority")["rows"]]
    assert "settled by the third read" in whys and "the three reads disagree" in whys
    assert re.search(r'<p class="rv-why" id="rv-drawer-why">settled by the third read</p>', out["d"])


def test_review_drawer_llm_outcome_and_final(proj):  # noqa: F811
    """Record 8, decided by ana after three disagreeing reads: "AI model outcome: maybe" beside "Final: include
    (researcher ana)"."""
    scenario(proj)
    r = replies_of(proj, 4, 8)
    out = page('await Review.open(3, true); await flush(); console.log(JSON.stringify({ d: $("#rv-drawer").innerHTML }));', r)
    t = text(out["d"])
    assert "AI model outcome: maybe · Final: include (researcher ana)" in t


def test_review_drawer_answered_failed_read(proj):  # noqa: F811
    """Record 6's failed read answered by bo: before the next run the drawer says it waits and has no buttons; after,
    pass B is labelled "researcher (answered failed read)" with the outcome label naming it."""
    q = scenario(proj)
    answer(proj, q["f6"], "exclude", "checked by hand", "bo")
    waiting = replies_of(proj, 4, 6)
    call = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-B' AND record_id=6")
    first = proj.store.scalar("SELECT call_id FROM screen_calls WHERE run_id='main-A' AND record_id=6")
    set_decision(proj, "main", 6, "exclude", votes=["exclude", "exclude"], decided_by="A,B", vote_calls=[first, call])
    done = replies_of(proj, 1, 6, show="all")
    body = 'await Review.open(__I__, true); await flush(); console.log(JSON.stringify({ d: $("#rv-drawer").innerHTML }));'
    w = page(body.replace("__I__", "2"), waiting)
    assert "failed read answered by bo; the next screening run of this set writes its decision" in text(w["d"])
    assert "data-rv-decide" not in w["d"]
    d = page(body.replace("__I__", "5"), done)
    t = text(d["d"])
    assert f"pass B: exclude ({rv.ANSWERED_READ})" in t
    assert "Outcome of the passes' reads (one given by a researcher): exclude" in t


def test_review_criteria_panel(proj):  # noqa: F811
    """One folded Criteria panel above the list with the route's note and sections; its open state is kept per
    browser and stays as left while records change."""
    from tests.unit.test_review import CODEBOOK
    from wider.construct import artifacts

    artifacts.save_new_version(proj, "codebook", CODEBOOK, origin="manual")
    scenario(proj)
    r = replies_of(proj, 4, 5)
    out = page("""
      const closed = $("#rv-criteria").open;
      $("#rv-criteria").open = true; listeners["#rv-criteria:toggle"].forEach((f) => f());
      await key("j"); await key("k");
      console.log(JSON.stringify({ closed, open: $("#rv-criteria").open, ls: LS["wider.review.criteria"], body: $("#rv-criteria-body").innerHTML }));""", r)
    assert out["closed"] is False and out["open"] is True and out["ls"] == "open"
    c = r["/api/review/criteria"]
    assert text(out["body"]).startswith(c["note"])
    assert re.findall(r'data-rv-crit="([\w]+)"', out["body"]) == [s["key"] for s in c["sections"]]
    assert "Widgets: the paper studies widgets" in text(out["body"])
    again = page('console.log(JSON.stringify({ open: $("#rv-criteria").open }));', r,
                 store={"wider.reviewer": "ana", "wider.review.criteria": "open"})
    assert again["open"] is True
    s = section()
    assert s.count('id="rv-criteria"') == 1 and s.index('id="rv-criteria"') < s.index('id="rv-list"')
    assert "<details id=\"rv-criteria\"" in s and "<details id=\"rv-criteria\" open" not in s
    # localStorage that throws (a private window) leaves the panel folded and the page working
    broken = page('console.log(JSON.stringify({ open: $("#rv-criteria").open, rows: rowIds() }));', r,
                  pre='global.localStorage = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); } };')
    assert broken["open"] is False and broken["rows"] == [4, 5, 6, 8, 9]


def test_review_qc_dialog_and_sentence(proj):  # noqa: F811
    """The dialog posts size and seed, signs with the page's name (the author header), and the page then lists the
    drawn records with the not-a-recall-estimate sentence; the route draws what `wider review qc draw` draws."""
    scenario(proj)
    r = replies_of(proj, 4)
    c = client(proj)
    res = c.post("/api/review/qc", json={"n": 1, "seed": 7}, headers={"x-wider-author": "ana"}).json()
    qc_list = c.get("/api/review", params={"show": "qc"}).json()
    rid = res["records"][0]
    qc_view = c.get(f"/api/review/{rid}").json()
    pre = f"""replies["/api/review/qc"] = {json.dumps(res)};
      const plain = replies["/api/review"]; replies["/api/review"] = (url) => url.includes("show=qc") ? {json.dumps(qc_list)} : plain;
      replies["/api/review/{rid}"] = {json.dumps(qc_view)};"""
    out = page("""
      const shown = $("#rv-qc-sentence").hidden;
      await $("#rv-qc-btn").onclick(); const open = !$("#rv-qc-dialog").hidden;
      $("#rv-qc-n").value = "1"; $("#rv-qc-seed").value = "7"; await $("#rv-qc-go").onclick(); await flush();
      console.log(JSON.stringify({ shown, open, closed: $("#rv-qc-dialog").hidden, body: posts()[0][2], url: posts()[0][0],
        sentence: $("#rv-qc-sentence").textContent, hidden: $("#rv-qc-sentence").hidden, rows: rowIds(), undos }));""", r, pre=pre)
    assert out["shown"] is True and out["open"] and out["closed"]
    assert out["body"] == {"n": 1, "seed": 7} and out["url"] == "/api/review/qc?protocol=main&set=1"
    assert out["sentence"] == rv.QC_SENTENCE and out["hidden"] is False
    assert out["rows"] == res["records"]
    assert out["undos"] == [[res["change_id"], f"Drew 1 of {res['pool_n']} settled records (seed 7)."]]
    # keyboard-only tester F1: Esc closes the dialog (focus back on its button); Enter in Size draws; 1 decides nothing
    keys = page("""
      await $("#rv-qc-btn").onclick(); await key("1", { tagName: "INPUT", id: "rv-qc-n" });
      await key("Escape", { tagName: "INPUT", id: "rv-qc-n" }); const escClosed = $("#rv-qc-dialog").hidden;
      await $("#rv-qc-btn").onclick(); $("#rv-qc-n").value = "1"; $("#rv-qc-seed").value = "7";
      await key("Enter", { tagName: "INPUT", id: "rv-qc-seed" });
      console.log(JSON.stringify({ esc: escClosed, posts: posts().map((p) => p[0]), closed: $("#rv-qc-dialog").hidden }));""", r, pre=pre)
    assert keys["esc"] is True and keys["posts"] == ["/api/review/qc?protocol=main&set=1"] and keys["closed"] is True
    ch = proj.store.one("SELECT kind, author FROM history WHERE change_id=?", (res["change_id"],))
    assert (ch["kind"], ch["author"]) == ("review_qc_draw", "ana")
    tag = re.search(r'<p id="rv-qc-sentence"[^>]*>', section()).group(0)
    assert "muted" not in tag and "small" not in tag


def test_review_command_fold():
    """A closed "Command line" fold at the end of the page lists the CLI twins."""
    s = section()
    a = s.index('<details id="rv-cmd"')
    assert s[a:].startswith('<details id="rv-cmd" class="card rv-cmd cmdline"><summary>Command line</summary>')
    assert "<details" not in s[a + 1:] and s.rstrip().endswith("</details>")
    for cmd in ("wider review list", "wider review show", "wider review decide", "wider review criteria",
                "wider review qc draw"):
        assert cmd in s[a:]


def test_pending_reviews_link_on_screening_and_home(proj, tmp_path):  # noqa: F811
    """D12: the Screening card says "4 records wait for a person" with a link to the review list of that protocol and
    set, its maybes step links "Screening review page" to the same address, and the Home page's open project card
    shows the same line and link; none of them when no record waits. The dict is `review.pending` of the scenario."""
    import subprocess

    from tests.unit import test_screen_page_node as sp
    from tests.unit import test_shell_js_node as sh
    from wider.screen.progress import screen_progress

    scenario(proj)
    prog = json.loads(json.dumps(screen_progress(proj)))
    pend = prog["pending_review"]
    assert pend["line"] == "4 records wait for a person" and pend["href"] == "#review?protocol=main&set=1"
    link = '<a href="#review?protocol=main&amp;set=1">'
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    code = src[src.index("let scData = null"):src.index("async function loadScreen()")]

    def card(p):
        script = (f"{sp.PAGE}\n{code}\nrenderScreenOverview({json.dumps({'runs': [], 'progress': p})});\n"
                  "console.log(JSON.stringify($('#sc-overview').innerHTML));")
        out = subprocess.run([sp.NODE, "-"], input=script, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    # the scenario screened no whole corpus, so its maybes step is "not started": give it the step the engine builds
    # when maybes are left (progress.py, the maybes step's needs_attention branch)
    from wider.screen.progress import _step

    maybes = _step("maybes", "Maybes read again or decided (optional)", False, "needs_attention",
                   "2 records of protocol main are maybe. You may read them again (Start screening, records: maybes) "
                   "or decide them yourself on the Screening review page.", "2 maybes", tab="review")
    assert "decide them yourself on the Screening review page." in (STATIC.parents[1] / "screen" / "progress.py").read_text()
    prog["steps"] = [maybes if s["key"] == "maybes" else s for s in prog["steps"]]
    html = card(prog)
    assert f'<p class="sc-pending">{link}4 records wait for a person</a></p>' in html
    assert f"decide them yourself on the {link}Screening review page</a>." in html
    html = card(prog | {"pending_review": pend | {"count": 0, "line": None}})
    assert "sc-pending" not in html and "#review?" not in html

    cur, _ = sh._summary(tmp_path / "home", current=True)
    other, _ = sh._summary(tmp_path / "other")

    def home(projects):
        out = sh.shell_page(f"{sh.HOME} console.log(JSON.stringify($('#home-projects').innerHTML));", sh.stages({}),
                            None, {"/api/home": {"projects": projects, "missing": [], "recent_persisted": True},
                                   "/api/stages": {"stages": [], "next": None}})
        return out

    html = home([cur | {"pending_review": pend}, other | {"pending_review": pend}])
    first, second = html.split("<article")[1:]
    assert f'<p class="pc-pending">{link}4 records wait for a person</a></p>' in first
    assert "4 records wait for a person" in first and "4 records wait" not in second   # the open project only
    assert "pc-pending" not in home([cur | {"pending_review": None}])
