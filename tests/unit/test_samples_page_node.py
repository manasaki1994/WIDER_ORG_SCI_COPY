"""The "Add records to bands" fields on the Samples page (wider/ui/static/sample_extend.js) under Node, no browser
(change validation-sample-planner, D6): only on an unlocked probability sample with bands; it posts the positive
whole numbers per band and shows the server's lines, in normal text, marked bad when refused."""
import json
import re

from tests.unit.test_review import proj  # noqa: F401 - pytest fixture
from tests.unit.test_sample_extend_node import page

GREY = re.compile(r'class="[^"]*\b(muted|small)\b')


def test_add_records_to_bands():
    out = page("""
const s = { kind: "probability", locked: false, n: 180, bands: [{ band: "S1" }, { band: "S2" }] };
const res = {};
res.open = SampleExtend.bandsHtml("validation", s);
res.locked = SampleExtend.bandsHtml("validation", { ...s, locked: true });
res.purposive = SampleExtend.bandsHtml("development", { ...s, kind: "purposive" });
(async () => {
  await SampleExtend.addBands("validation", { S1: "", S2: "0" }, deps);
  res.none = SampleExtend.bandsHtml("validation", s); res.sentNone = sent.length;
  replies["/api/validation/extend"] = { lines: ["validation: 20 records added (S2 20); 200 in all."] };
  await SampleExtend.addBands("validation", { S1: "x", S2: " 20 " }, deps);
  res.sent = sent; res.ok = SampleExtend.bandsHtml("validation", s);
  replies["/api/validation/extend"] = { error: "S2 has only 3 frame records left", status: 400 };
  await SampleExtend.addBands("validation", { S2: "5" }, deps);
  res.bad = SampleExtend.bandsHtml("validation", s);
  process.stdout.write(JSON.stringify(res));
})();""")
    r = out if isinstance(out, dict) else json.loads(out)
    assert "Add records to bands" in r["open"] and 'data-add-band="S1"' in r["open"] and 'data-add-band="S2"' in r["open"]
    assert r["locked"] == "" and r["purposive"] == ""
    assert r["sentNone"] == 0 and "at least one band" in r["none"]
    assert r["sent"][:1] == [{"url": "/api/validation/extend", "method": "POST",
                          "body": {"sample": "validation", "bands": {"S2": 20}}}]
    assert "20 records added" in r["ok"] and 'class="bad"' not in r["ok"]
    assert "only 3 frame records left" in r["bad"] and 'class="bad"' in r["bad"]
    for k in ("open", "ok", "bad"):
        assert not GREY.search(r[k])


def test_fresh_sample_button():
    out = page("""
const samples = { validation: { kind: "probability", in_use: true }, development: { kind: "purposive" },
                  validation2: { kind: "probability" } };
const res = {};
res.html = SampleExtend.freshHtml(samples);
res.none = SampleExtend.freshHtml({ development: { kind: "purposive" } });
res.name = SampleExtend.freshName("validation", Object.keys(samples));
res.url = SampleExtend.freshUrl("validation", " validation3 ");
process.stdout.write(JSON.stringify(res));""")
    r = out if isinstance(out, dict) else json.loads(out)
    assert "Fresh sample instead of the used one" in r["html"]
    assert '<option value="validation" selected>' in r["html"] and 'value="validation2"' in r["html"]
    assert 'value="development"' not in r["html"] and r["none"] == ""
    assert r["name"] == "validation3" and 'data-fresh-name value="validation3"' in r["html"]
    assert r["url"] == "/api/samples/draw?which=probability&name=validation3&fresh_from=validation"
    assert "leaves out the used sample" in r["html"] and not GREY.search(r["html"])


def test_criteria_counts_on_card():
    out = page("""
const res = {};
res.html = SampleExtend.criteriaHtml({ lines: ["40 labels: 10 before the criteria were frozen, 30 after", "<b>x</b>"] });
res.none = SampleExtend.criteriaHtml(undefined) + SampleExtend.criteriaHtml({ lines: [] });
process.stdout.write(JSON.stringify(res));""")
    r = out if isinstance(out, dict) else json.loads(out)
    assert "<p>40 labels: 10 before the criteria were frozen, 30 after</p>" in r["html"]
    assert "&lt;b&gt;" in r["html"] and r["none"] == "" and not GREY.search(r["html"])


def test_criteria_section_on_card():
    out = page("""
const r = { early: 20, to_version: 3, recommendation: "re-check the 11 affected labels",
            lines: ["other changes: 0; elements changed: 1; reasons changed: 1", "Recommended: re-check the 11 affected labels"] };
const res = {};
res.html = SampleExtend.criteriaChangeHtml("validation", r, [{ record_id: 5, reviewer: "R1", old_decision: "include",
  old_codebook_version: 2, new_decision: "exclude", new_codebook_version: 3 }]);
res.none = SampleExtend.criteriaChangeHtml("validation", { early: 0, to_version: 3 }, []);
res.noPick = SampleExtend.criteriaBody("validation", null, "x");
res.keep = SampleExtend.criteriaBody("validation", "none", " wording only ");
res.aff = SampleExtend.criteriaBody("validation", "affected", "");
process.stdout.write(JSON.stringify(res));""")
    r = out if isinstance(out, dict) else json.loads(out)
    h = r["html"]
    assert "Criteria changed after labelling" in h and h.count("Recommended: re-check the 11 affected labels") == 1
    assert h.count('type="radio"') == 3 and "checked" not in h and not GREY.search(h)
    assert "Revised labels" in h and "include under v2 to exclude under v3" in h
    assert r["none"] == "" and r["noPick"] is None
    assert r["keep"] == {"sample": "validation", "substantive": "no", "scope": None, "note": "wording only"}
    assert r["aff"] == {"sample": "validation", "substantive": "yes", "scope": "affected", "note": None}



def test_sample_row_counts_reviewed_records(proj):  # noqa: F811
    """manual-review D13: a sample's row on the Samples and labelling page says how many of its records a person
    decided after seeing the LLM's reads, in the engine's sentence (`wider sample status` prints the same), in normal
    text; no line when none was. The reply comes from the real `GET /api/samples` on the scenario of test_review."""
    from fastapi.testclient import TestClient

    from tests.unit.test_backends_js_node import ESC, STATIC, run_node
    from tests.unit.test_review import scenario
    from wider.screen import review as rv
    from wider.ui.app import create_app

    scenario(proj)
    proj.store.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) "
                       "VALUES(1,'probability','probability','{}',4,'x')")
    for rid in (3, 4, 5, 6):
        proj.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(1,?,?)", (rid, rid))
    c = TestClient(create_app(proj))
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    code = src[src.index("let lbCodebook = null"):src.index('// "Add boundary cases"')]

    def row(reply):
        return run_node(f"""{ESC}
const els = {{}}; const $ = (s) => els[s] || (els[s] = {{ innerHTML: "", value: "", disabled: false }});
const fillSelect = () => true; global.localStorage = {{ getItem: () => null }};
let smShown = {{}}; const lockButtonText = () => {{}};   // the Lock button (tested below)
const api = async (url) => {{ if (url === "/api/samples") return {json.dumps(reply)}; throw new Error("no " + url); }};
{code}
await loadSamples(); process.stdout.write(JSON.stringify($("#sm-samples").innerHTML));""")

    none = c.get("/api/samples").json()
    assert none["samples"]["probability"]["reviewed_line"] is None
    assert "sm-reviewed" not in row(none)
    rv.decide(proj, None, None, 5, "include", "bo")
    rv.decide(proj, None, None, 4, "exclude", "bo")              # the split question
    reply = c.get("/api/samples").json()
    line = "2 records of this sample were decided by a person who saw the AI model's reads"
    assert reply["samples"]["probability"]["reviewed_line"] == line
    assert '<p class="sm-reviewed">2 records of this sample were decided by a person who saw the AI model&#39;s reads' in row(reply)


def test_unlock_dialog_warns_when_recall_was_seen():
    """manual-review D5: Lock and Unlock ask in the page's own dialog (no `confirm()`); for a locked sample whose recall
    result was seen the Unlock dialog shows the warning in normal text, Cancel sends nothing, and "Unlock" sends the
    unlock with `yes` (twin of `wider sample unlock NAME --yes`)."""
    from tests.unit.test_backends_js_node import ESC, STATIC, run_node
    from tests.unit.test_ui_followup_r31 import NAME_BLOCK

    src = (STATIC / "app.js").read_text(encoding="utf-8")
    a = src.index("// Lock and unlock (D5")
    code = src[a:src.index("await loadSamples();\n};\n", a) + len("await loadSamples();\n};\n")]
    assert "confirm(" not in code
    warn = "The recall estimate on validation is no longer blind: its result was seen before this unlock."
    out = run_node(f"""{ESC}
const els = {{}}; const $ = (s) => els[s] || (els[s] = {{ textContent: "", value: "", addEventListener() {{}} }});
const sent = [], asked = []; let answer = null;
const api = async (url, opts) => {{ sent.push([url, opts && opts.method]); return {{ ok: true }}; }};
const askChoice = async (html) => {{ asked.push(html); return answer; }};
const loadSamples = async () => {{}};
let stored = null; global.localStorage = {{ getItem: () => stored, setItem: (k, v) => {{ stored = v; }}, removeItem() {{ stored = null; }} }};
global.document = {{}}; global.window = global; global.location = {{ reload() {{}} }};
{NAME_BLOCK}
{code}
smShown = {{ validation: {{ locked: true, unlock_warning: {json.dumps(warn)} }}, dev: {{ locked: false, unlock_warning: null }} }};
$("#ag-sample").value = "validation"; lockButtonText();
const label = $("#ag-lock").textContent;
answer = {{ act: "unlock", fields: {{}} }}; await $("#ag-lock").onclick();   // no name yet: no dialog, nothing sent
const noName = {{ asked: asked.length, sent: sent.length, msg: $("#ag-summary").innerHTML }};
WiderName.set("ana"); answer = null;
await $("#ag-lock").onclick();                     // Cancel
const afterCancel = sent.length;
answer = {{ act: "unlock", fields: {{}} }}; await $("#ag-lock").onclick();
$("#ag-sample").value = "dev"; lockButtonText(); const devLabel = $("#ag-lock").textContent;
answer = {{ act: "lock", fields: {{}} }}; await $("#ag-lock").onclick();
process.stdout.write(JSON.stringify({{ label, devLabel, asked, afterCancel, sent, noName, msg: $("#ag-summary").textContent }}));""")
    r = out
    assert r["noName"]["asked"] == 0 and r["noName"]["sent"] == 0
    msg = re.sub(r"\s+", " ", r["noName"]["msg"])   # the refusal points at Change in the top bar, with its button
    assert msg.startswith("no name yet: a lock or unlock is signed with your name. enter your name first: use Change "
                          "beside “Working as” in the top bar.")
    assert msg.endswith("<button type=\"button\" class=\"secondary small\" data-set-name>Set your name</button>")
    assert r["label"] == "Unlock sample" and r["devLabel"] == "Lock sample"
    assert f'<p class="dlg-warning">{warn}</p>' in r["asked"][0] and "The unlock is final" in r["asked"][0]
    assert 'data-act="cancel"' in r["asked"][0]
    assert r["afterCancel"] == 0
    assert r["sent"][0] == ["/api/samples/validation/lock?unlock=true&yes=true", "POST"]
    assert "no longer blind" not in r["asked"][2] and "Lock dev" in r["asked"][2]
    # M17: the lock fixes the records; labels are still accepted, a change is a correction
    assert "No further labels" not in r["asked"][2] and "Missing labels and adjudications are still accepted" in r["asked"][2]
    assert "can no longer be redrawn or extended" in r["asked"][2] and "accepted again" not in r["asked"][0]
    assert r["sent"][1] == ["/api/samples/dev/lock", "POST"] and r["msg"] == "locked dev (in History)"


QUEUE_ROWS = r"""
const rowsOf = (html) => (html.match(/<tr data-idx[\s\S]*?<\/tr>/g) || []);
const body = { querySelectorAll: () => [], querySelector: () => ({ value: "" }) };
"""


def test_corrected_labels_list():
    """sampling-and-labelling (D2): the card says once how many labels of a sample were corrected, with the command
    that lists them, in normal text, on any kind of sample; no decision or reason is on the page; none, no line."""
    out = page("""
process.stdout.write(JSON.stringify({ two: SampleExtend.correctionsHtml("dev", 2),
  one: SampleExtend.correctionsHtml("validation", 1), none: SampleExtend.correctionsHtml("dev", 0) }));""")
    assert out["two"].startswith("<p data-corrections>2 corrected labels</p>")   # the command sits in the page's fold
    assert "<summary>Command line</summary>" in out["two"] and "wider label revisions dev" in out["two"]
    assert out["one"].startswith("<p data-corrections>1 corrected label</p>") and out["none"] == ""
    assert not GREY.search(out["two"].split("<details")[0])   # the count line is normal text; the fold is the common one


def test_declare_independent_dialog():
    """recall-and-outputs / sampling-and-labelling (D9, D11, owner Q8): a probability sample with labels whose
    independence is not stated shows "R1: 20 labels without an established independence" and "Declare labels
    independent"; the dialog asks the reviewer (filled when there is one) and the basis, posts
    /api/labels/independence and says the route's lines; Cancel or an empty basis posts nothing."""
    out = page("""
const s = { kind: "probability", provenance: { undeclared: { R1: 20 } } };
const res = {};
res.card = SampleExtend.independenceHtml("validation", s);
res.purposive = SampleExtend.independenceHtml("dev", { ...s, kind: "purposive" });
res.none = SampleExtend.independenceHtml("validation", { kind: "probability", provenance: { undeclared: {} } });
const said = []; let answer = null; const asked = [];
const d = { ...deps, say: (t) => said.push(t), askFields: async (title, fields, action) => { asked.push([title, fields, action]); return answer; } };
(async () => {
  await SampleExtend.declareIndependent("validation", s, d);                    // Cancel
  answer = { reviewer: "R1", basis: "  " }; await SampleExtend.declareIndependent("validation", s, d);
  res.sentEmpty = sent.length;
  replies["/api/labels/independence"] = { lines: ["20 labels of R1 on validation now count as independent"] };
  answer = { reviewer: "R1", basis: "labelled before any model ran" };
  await SampleExtend.declareIndependent("validation", s, d);
  res.sent = sent; res.said = said; res.asked = asked[0]; res.reloads = reloads;
  process.stdout.write(JSON.stringify(res));
})();""")
    assert "<p>R1: 20 labels without an established independence</p>" in out["card"]
    assert out["card"].count("Declare labels independent") == 1 and 'data-declare-independent="validation"' in out["card"]
    assert not GREY.search(out["card"]) and out["purposive"] == "" and out["none"] == ""
    title, fields, action = out["asked"]
    assert title == "Declare labels independent" and action == "Declare"
    assert [f["key"] for f in fields] == ["reviewer", "basis"] and fields[0]["value"] == "R1"
    assert out["sentEmpty"] == 0
    assert out["said"][:2] == ["nothing was saved", "nothing was saved: a declaration needs a reviewer and a basis"]
    assert out["sent"] == [{"url": "/api/labels/independence", "method": "POST",
                            "body": {"sample": "validation", "reviewer": "R1", "basis": "labelled before any model ran"}}]
    assert out["said"][2] == "20 labels of R1 on validation now count as independent" and out["reloads"] == 1


def test_locked_sample_queue_takes_labels():
    """sampling-and-labelling "A locked sample's queue takes missing labels and corrections": the queue's sample select
    lists a locked sample, marked; choosing it asks for that sample's queue, blind (no band, pool, rank or sample on
    the page), no row read-only, the "not labelled yet" filter lists the unlabelled records, and the drawer has no
    read-only rule; with the blind switch off the rows show sample, band and rank."""
    from tests.unit.test_backends_js_node import run_node
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, RECORDS, queue_name

    out = run_node(QUEUE_PAGE + queue_name("ana") + RECORDS + QUEUE_ROWS + r"""
const res = {};
window.lbSampleOptions({ dev: { locked: true }, probe: { locked: false } });
res.options = $("#lb-sample").innerHTML;
const blindRows = [{ record_id: 1, item: "t1", title: "first", decision: "include", locked: true },
                   { record_id: 2, item: "t2", title: "second", decision: "", locked: true }];
const plainRows = [{ record_id: 1, sample_id: 1, sample: "dev", band: "S1", rank: 4, title: "first", decision: "include", locked: true }];
apiReply = (p) => p.startsWith("/api/labels/queue") ? (p.includes("blind=false") ? plainRows : blindRows).map((r) => ({ ...r })) : {};
$("#lb-filter").value = "unlabelled"; $("#lb-sample").value = "dev";
$("#lb-load").onclick(); await flush(); await flush();
res.asked = calls[0][0];
res.rows = rowsOf($("#lb-table").innerHTML);
const cfg = opened[opened.length - 1];
res.readOnly = "readOnly" in cfg; res.items = cfg.items.map((r) => r.record_id);
$("#lb-filter").value = "all"; $("#lb-filter").onchange(); res.all = rowsOf($("#lb-table").innerHTML);
$("#lb-blind").checked = false; calls.length = 0; $("#lb-blind").onchange(); await flush(); await flush();
res.plainAsked = calls[0][0]; res.plain = rowsOf($("#lb-table").innerHTML);
console.log(JSON.stringify(res));""")
    assert '<option value="dev">dev (locked)</option>' in out["options"] and ">probe</option>" in out["options"]
    assert out["asked"] == "/api/labels/queue?reviewer=ana&samples=dev"
    assert len(out["rows"]) == 1 and "second" in out["rows"][0]     # "not labelled yet" lists the unlabelled one
    assert out["items"] == [2] and out["readOnly"] is False
    assert len(out["all"]) == 2 and "include" in out["all"][0]     # her own label stays shown
    assert not any("read-only" in r for r in out["all"])
    assert not any(w in "".join(out["all"]) for w in ("S1", "rank", "dev"))
    assert out["plainAsked"] == "/api/labels/queue?reviewer=ana&samples=dev&blind=false"
    assert "dev · S1 · rank 4" in out["plain"][0]


def test_left_out_records_said_once():
    """sampling-and-labelling "Sealed records and an open sample": the route's X-Left-Out count (read through
    api(path, {withHeaders: true})) gives one line under the queue that names no sample; without the header the
    line is not there."""
    from tests.unit.test_backends_js_node import run_node
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, RECORDS, queue_name

    out = run_node(QUEUE_PAGE + queue_name("ana") + RECORDS + QUEUE_ROWS + r"""
let left = "5";
const rows = [{ record_id: 3, item: "t3", title: "third", decision: "", locked: false }];
const asked = [];
apiReply = (path) => path.startsWith("/api/labels/queue")
  ? { body: rows.map((r) => ({ ...r })), headers: { get: (k) => k === "X-Left-Out" ? left : null } } : {};
const res = {};
$("#lb-filter").value = "all";
$("#lb-load").onclick(); await flush(); await flush();
res.lines = $("#lb-lines").innerHTML; res.rows = rowsOf($("#lb-table").innerHTML).length;
$("#lb-filter").onchange(); res.again = $("#lb-lines").innerHTML;
left = null; $("#lb-load").onclick(); await flush(); await flush();
res.none = $("#lb-lines").innerHTML;
res.asked = calls.filter(([p]) => p.startsWith("/api/labels/queue")).length;
console.log(JSON.stringify(res));""")
    line = ("5 records not labelled yet are in a queue of their own: choose a sample in the sample select")
    assert out["lines"] == f"<p>{line}</p>" and out["again"] == out["lines"] and out["rows"] == 1
    assert out["lines"].count("not labelled yet") == 1 and not GREY.search(out["lines"])
    assert out["none"] == "" and out["asked"] == 2


def test_name_change_offers_ask_again():
    """guide-assistant (ui-tester F1): a queue with records hides Ask WIDER; switching to another name (Change in the
    top bar, ui-redesign D33) reloads the page, so no queue stays open and Ask WIDER is offered again instead of
    staying on "not offered while labelling"."""
    from tests.unit.test_backends_js_node import run_node
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, RECORDS, queue_name

    out = run_node(QUEUE_PAGE + queue_name("ana") + RECORDS + QUEUE_ROWS + r"""
let redraws = 0; const open = [];
window.askBoxRedraw = () => { redraws += 1; open.push(window.queueOpen()); };
globalThis.currentPage = "sample";
const rows = [{ record_id: 3, item: "t3", title: "third", decision: "", locked: false }];
apiReply = (p) => p.startsWith("/api/labels/queue") ? rows.map((r) => ({ ...r })) : {};
$("#lb-filter").value = "all";
$("#lb-load").onclick(); await flush(); await flush();
const loaded = redraws;
WiderName.set("bo");
console.log(JSON.stringify({ loaded, open, reloads: location.reloads }));""")
    assert out["loaded"] >= 1 and out["open"][out["loaded"] - 1] is True
    assert out["reloads"] == 1


def test_labelling_page_says_who_signs(proj, monkeypatch):  # noqa: F811
    """manual-review D10: the Labelling queue says which name signs, in normal text (no muted class), and once when
    the server's WIDER_REVIEWER differs; with no name it says labels need one, with a Set your name button that opens
    the one name dialog (ui-redesign D33). The reply is the real
    `GET /api/samples`, read by app.js's loadSamples with records.js on the page."""
    from fastapi.testclient import TestClient

    from tests.unit.test_backends_js_node import STATIC, run_node
    from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
    from wider.ui.app import create_app

    src = (STATIC / "app.js").read_text(encoding="utf-8")
    code = src[src.index("let lbCodebook = null"):src.index('// "Add boundary cases"')]
    rec = (STATIC / "records.js").read_text(encoding="utf-8")

    def page_says(env, name):
        if env is None:
            monkeypatch.delenv("WIDER_REVIEWER", raising=False)
        else:
            monkeypatch.setenv("WIDER_REVIEWER", env)
        reply = TestClient(create_app(proj)).get("/api/samples").json()
        return run_node(QUEUE_PAGE + queue_name(name) + rec + f"""
const fillSelect = () => true; let smShown = {{}}; const lockButtonText = () => {{}};
apiReply = (p) => p === "/api/samples" ? {json.dumps(reply)} : {{}};
{code}
await loadSamples();
const first = $("#lb-signing").innerHTML;
window.lbSigning();   // drawn again (a first name set from the dialog): the command line's name is kept
console.log(JSON.stringify({{ first, again: $("#lb-signing").innerHTML, cls: $("#lb-signing").className || "" }}));""")

    text = lambda s: re.sub(r"<[^>]+>", "", s)  # noqa: E731
    out = page_says("bo", "ana")
    assert text(out["first"]) == "Signing as ana. The command line signs as bo." and out["again"] == out["first"]
    assert "muted" not in out["cls"] and "muted" not in out["first"]
    assert text(out["first"]).count("Signing as") == 1 and text(out["first"]).count("signs as bo") == 1
    for env in (None, "ana", " ana "):
        assert text(page_says(env, "ana")["first"]) == "Signing as ana."
    blank = page_says("bo", "")["first"]
    assert text(blank) == "no name yet: labels need your name. Set your name"
    assert '<button type="button" class="secondary small" data-set-name>Set your name</button>' in blank


def _sample_section() -> str:
    from tests.unit.test_backends_js_node import STATIC

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    a = html.index('<section id="page-sample"')
    return html[a:html.index("</section>", a)]


def test_sample_steps_order_and_one_filled_button():
    """ui-redesign D32 F6: the Samples page reads 1 recall target and rule, 2 plan, 3 draw, 4 label (queue, then the
    review package), and exactly one button is filled: the first step not done; the others are outline buttons."""
    import json as _json

    from tests.unit.test_backends_js_node import ESC, STATIC, run_node

    sec = _sample_section()
    heads = re.findall(r'<span class="step-no">(\d[ab]?)</span> ([^<]+)</h2>', sec)
    assert [n for n, _ in heads] == ["1", "2", "3", "4a", "4b"]   # owner inspection: not two sections numbered 4
    assert heads[0][1].startswith("Recall target") and heads[1][1].startswith("Plan the validation sample")
    assert heads[2][1].startswith("Draw samples") and "labelling queue" in heads[3][1].lower() and "review package" in heads[4][1]
    steps = re.findall(r'<button[^>]*data-sm-step="(\d)"', sec)
    assert steps == ["1", "2", "3", "4"]
    # every id the page code and other tests find is still there
    for i in ("val-card", "val-plan", "val-table", "val-msg", "val-target-msg", "val-depth", "sm-draw", "sm-msg",
              "sm-fresh", "sm-samples", "sm-settings", "lb-card", "lb-load", "bp-card", "bp-make", "bp-reviewers",
              "bp-limits", "ag-sample", "ag-lock"):
        assert f'id="{i}"' in sec, i
    # the recall target and the plan stay in #val-card, whose listeners read both
    val = sec[sec.index('id="val-card"'):sec.index('id="sm-draw-card"')]
    assert 'data-val-target="target"' in val and 'data-val-act="plan"' in val
    # as written, no button of the page is filled (an unclassed <button>): the page script picks the one
    assert [b for b in re.findall(r"<button(?![^>]*class=)[^>]*>", sec)] == []
    code = (STATIC / "validation.js").read_text(encoding="utf-8")
    out = run_node(ESC + code + """
const mk = (n) => ({ dataset: { smStep: String(n) }, className: "secondary" });
const states = { none: {}, target: { target: 0.9 }, planned: { target: 0.9, planned: true },
  drawnNoPlan: { target: 0.9, drawn: true, probability: true }, drawn: { target: 0.9, planned: true, drawn: true },
  drawnNoTarget: { planned: true, drawn: true } };
const r = {};
for (const [k, s] of Object.entries(states)) {
  const bs = [1, 2, 3, 4].map(mk); const step = Validation.nextStep(s); Validation.markSteps(bs, step);
  r[k] = { step, filled: bs.filter(b => b.className === "main-act").map(b => b.dataset.smStep), outline: bs.filter(b => b.className === "secondary").length };
}
console.log(JSON.stringify(r));""")
    want = {"none": 1, "target": 2, "planned": 3, "drawnNoPlan": 4, "drawn": 4, "drawnNoTarget": 1}
    for k, step in want.items():
        assert out[k]["step"] == step and out[k]["filled"] == [str(step)] and out[k]["outline"] == 3, (k, _json.dumps(out[k]))


def test_sample_steps_follow_a_saved_target():
    """After Save target (and after Use this allocation) the controller asks the page to mark its steps again."""
    from tests.unit.test_backends_js_node import ESC, STATIC, run_node

    code = (STATIC / "validation.js").read_text(encoding="utf-8")
    out = run_node(ESC + code + """
let marks = 0;
const el = () => ({ innerHTML: "", textContent: "", hidden: true });
const api = async (path) => path === "/api/validation/target" ? { status: "Target 0.9 set." } : { applied: { lines: ["ok"] } };
const c = Validation.controller({ table: el(), msg: el(), depth: el(), targetMsg: el() }, { api, afterStep: async () => { marks++; } });
await c.saveTarget(); await c.use("neyman");
console.log(JSON.stringify({ marks }));""")
    assert out == {"marks": 2}
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "await markSampleSteps(d.samples);" in app and 'document.querySelectorAll("#page-sample [data-sm-step]")' in app
