"""Page fixes found by the C1 checks (fix-defects-followup): consent dialog focus, skip link, read-only chip in the
labelling queue, forgetting a cleared name, the name the drawer signs with, readable Enrich source counts.

Static checks on the page code run everywhere; the Enrich count formatter also runs under Node when it is installed."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "wider" / "ui" / "static"
NODE = shutil.which("node")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_consent_dialog_focuses_cancel_and_returns_focus():
    js = read("common.js")
    body = js[js.index("function askLookupConsent"):]
    body = body[:body.index("\n}\n")]
    # Cancel gets the focus when the dialog opens, never "Allow and continue"
    assert 'querySelector("[data-act=cancel]").focus()' in body
    assert 'querySelector("[data-act=allow]").focus()' not in body
    # the control that opened the dialog is remembered before showModal and focused again after it closes (once it is
    # enabled again, when its request disabled it: test_drawer_keys_node.py runs that)
    assert body.index("const active = document.activeElement") < body.index("dlg.showModal()")
    close = body.index("dlg.close(); dlg.remove();")
    assert body.index("focusWhenEnabled(opener)") > close
    # Esc (the dialog's cancel event) is the same as Cancel: nothing is allowed, and the consent POST only runs on Allow
    assert 'addEventListener("cancel"' in body and "finish(false)" in body
    fin = body[body.index("const finish = async (allow)"):]
    fin = fin[:fin.index("dlg.close()")]
    assert fin.index("if (allow) {") < fin.index('"/api/lookups/allow"') and fin.count('method: "POST"') == 1
    # the only other request is the read-back of the stored state after a failed Allow (risk-checker R1), a GET
    assert fin.count("fetch(") == 2 and 'fetch("/api/lookups")' in fin


def test_skip_link_keeps_the_page():
    html, js = read("index.html"), read("app.js")
    assert 'class="skip-link"' in html and 'href="#main"' in html
    # <main> takes the focus only with tabindex="-1" (without it focus() would do nothing)
    assert '<main id="main" tabindex="-1">' in html
    line = next(ln for ln in js.splitlines() if ".skip-link" in ln)
    # the link's default (setting location.hash to #main, an unknown page that would open Home) is prevented
    assert "preventDefault()" in line and '$("#main").focus()' in line


def test_labelling_queue_has_no_read_only_chip():
    js = read("records.js")
    lb = js[js.index("function lbRender"):js.index("adjudication (reviewer versus reviewer)")]
    # label-completion-and-sealing D2: a locked record takes labels and corrections, so no row is read-only
    assert "read-only" not in lb and "readOnly" not in lb and "CLOSED" not in js
    assert '(f === "all" || (f === "labelled" ? !!r.decision : !r.decision))' in lb
    # the queue is asked with its headers, so X-Left-Out (D8) reaches the page
    assert "call(queueUrl(mode, rev), { withHeaders: true })" in js and 'res.headers.get("X-Left-Out")' in js


def test_clearing_the_name_forgets_it():
    # ui-redesign D33: the one name lives in common.js's WiderName; saving it empty removes the key, and the Labelling
    # page has no name field (nor a store of its own) that could keep an old name
    js, rec = read("common.js"), read("records.js")
    assert "if (n) localStorage.setItem(KEY, n); else localStorage.removeItem(KEY);" in js
    assert "lb-reviewer" not in rec and "wider.reviewer" not in rec


def test_construct_version_list_names_sha_parent_and_announces_a_refusal():
    js, html = read("app.js"), read("index.html")
    opts = next(ln for ln in js.splitlines() if '$("#art-version").innerHTML' in ln)
    status = next(ln for ln in js.splitlines() if '$("#art-status").textContent = `${artKind} v' in ln)
    for line in (opts, status):
        assert " · from v${" in line and "sha256 ${" in line and ".slice(0, 12)" in line
    assert "${esc(v.origin)}" in opts and "${d.row.origin}" in status
    assert '<p id="art-status" class="muted small" role="status">' in html
    # a refused save or approval is one alert in the error style (accessibility-and-naming 4.5)
    assert '<p id="art-refusal" class="bad" role="alert" hidden></p>' in html
    assert 'catch (e) { artRefusal("Not saved: " + e.message); }' in js and 'artRefusal("Not approved: " + e.message)' in js


def test_the_drawer_signs_with_the_queue_name_or_the_one_name():
    js = read("drawer.js")
    body = js[js.index("function author()"):]
    body = body[:body.index("\n  }\n")]
    # a queue loaded for one name signs with that name; otherwise the one name WIDER works as (D33): no field of its own
    assert body.index("cfg.author") < body.index("WiderName.get()")
    assert "#lb-reviewer" not in body and "#dw-author" not in body and "localStorage" not in body


def test_enrich_source_counts_are_readable_not_json():
    js = read("app.js")
    enrich = js[js.index("function renderEnrich"):]
    enrich = enrich[:enrich.index("\n}\n")]
    assert "JSON.stringify" not in enrich
    assert '["filled by source", counts(d.by_source)]' in enrich and '["abstract origin", counts(d.by_source_all)]' in enrich


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_enrich_counts_formatter_under_node(tmp_path):
    js = read("app.js")
    line = next(ln for ln in js.splitlines() if ln.startswith("const counts = "))
    cases = [{"openalex": 3, "export": 62}, {}, None]
    script = tmp_path / "counts.js"
    script.write_text(line + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(counts)));\n", encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=20, check=True).stdout
    assert json.loads(out) == ["openalex 3, export 62", "none yet", "none yet"]


def test_rank_page_top_n_empty_uses_the_default_and_a_fraction_is_refused():
    js = read("app.js")
    run = js[js.index('$("#rk-run").onclick'):]
    run = run[:run.index("\n};\n")]
    # an empty field sends no top_n (the server then uses the protocol default); never `top_n=` with nothing
    assert '${topn ? "&top_n=" + encodeURIComponent(topn) : ""}' in run and "&top_n=${" not in run
    # a fraction or text is refused on the page with a visible message, before any request
    check = run.index("!/^-?\\d+$/.test(topn)")
    assert check < run.index("await api(") and 'showStageError("rank", `top-N must be a whole number' in run
    # a whole number, even 0 or -3, is sent so the server names the allowed range; the button is disabled while the
    # request runs, so a double click sends one request
    assert "btn.disabled = true" in run and "finally { btn.disabled = false;" in run
    assert run.index("btn.disabled = true") < run.index("await api(")
    # the refusal branch ends with `return`, so no request follows it
    branch = run[check:run.index("\n", check)]
    assert branch.rstrip().endswith("return; }")
    # the field is free text and starts empty (a number input would turn "e" into "" and rank silently with the default);
    # the page never fills it with the uncapped protocol default, it only names the default in the placeholder
    html = read("index.html")
    tag = html[html.index('id="rk-topn"') - 10:]
    tag = tag[:tag.index(">")]
    assert 'type="text"' in tag and "value=" not in tag
    assert '$("#rk-topn").value =' not in js and '$("#rk-topn").placeholder = `default ' in js


def test_screen_page_sends_the_record_limit_as_typed():
    js = read("app.js")
    run = js[js.index("function screenBody()"):js.index("// ---------------- recall + report page")]
    # 0 or -5 goes to the server, which refuses it; only an empty field means every record (never `+value || null`)
    assert '+$("#sc-limit").value || null' not in run and 'limit: lim === "" ? null : lim,' in run
    check = run.index("!/^-?\\d+$/.test(lim)")
    assert check < run.index("Jobs.start(") and run[check:run.index("\n", check)].rstrip().endswith("return null; }")
    # the refusal shows a message on the page (no request follows it)
    assert 'showStageError("screen", `the record limit must be a whole number' in run[check:run.index("\n", check)]
    html = read("index.html")
    tag = html[html.index('id="sc-limit"') - 10:]
    assert 'type="text"' in tag[:tag.index(">")]


def test_drawer_keeps_the_focus_after_moving_to_another_record():
    js = read("drawer.js")
    mv = js[js.index("function move(step)"):]
    mv = mv[:mv.index("\n  }\n")]
    # j/k, Prev/Next and the advance after a decision all go through move(); the old item's controls are replaced, so
    # once the render is done the new title gets the focus when it left the drawer (never the page body)
    assert "render().then(() => settleFocus(false))" in mv and "el.focus({ preventScroll: true })" in mv
    settle = js[js.index("function settleFocus(always)"):]
    settle = settle[:settle.index("\n  }\n")]
    assert "!el.contains(a)" in settle and "a.disabled" in settle
    assert 'q("#dw-title")' in settle and ".focus({ preventScroll: true })" in settle
    dec = js[js.index("async function decide(i)"):]
    assert "advanceTimer = setTimeout(() => { advanceTimer = null; move(1); }, 200)" in dec[:dec.index("\n  }\n")]


def test_rank_and_screen_controls_have_names_and_the_content_shows_its_focus():
    html = read("index.html")
    for sel in ["rk-anchors", "rk-ranking", "rk-cell", "sc-prompt", "sc-scope", "sc-mode",
                "sc-pass", "sc-protocol", "rk-prev", "rk-next"]:
        i = html.index(f'id="{sel}"')
        tag = html[html.rindex("<", 0, i):html.index(">", i)]
        assert 'aria-label="' in tag, sel
    # the skip link moves the focus to <main>; a keyboard user sees where it went
    css = read("style.css")
    assert "main:focus { outline:none; }" not in css and "main:focus:not(:focus-visible)" in css
    keys = read("shell.js")
    assert '["Enter", "open the record of the focused table row in the drawer"]' in keys and "cancel a dialog" in keys


def test_a_long_name_can_remove_its_own_notes_and_the_pair_drawer_hint_has_no_note_keys():
    js = read("drawer.js")
    notes = js[js.index("async function renderNotes"):]
    notes = notes[:notes.index("\n  }\n")]
    # the server keeps the first 80 characters (code points, not UTF-16 units) of a name, so "mine" is compared with those
    assert 'const mine = Array.from(me || "").slice(0, 80).join("")' in notes
    assert "t.author === mine" in notes and "n.author === mine" in notes and "author === me " not in notes
    # the duplicate-pair drawer (no notes) hides the n and t keys from its hint
    assert '<span class="dw-keys-notes"><kbd>n</kbd> note · <kbd>t</kbd> tag · </span>' in read("index.html")
    assert 'q(".dw-keys-notes").hidden = c.notes === false' in js
    # the Deduplication page's docked pair view opens it that way; its Esc hint reads "back to the list"
    assert 'dock: "#dd-pair"' in read("dedup.js") and "notes: false" in read("dedup.js")
    assert '"back to the list"' in js


def test_a_drawer_for_reading_only_hides_the_decision_keys():
    # the screening views open the drawer without decisions: its hint does not offer 1/2/3
    assert '<span class="dw-keys-decide"><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> decide · </span>' in read("index.html")
    assert 'q(".dw-keys-decide").hidden = !c.decisions' in read("drawer.js")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_screen_page_keeps_the_chosen_records_after_a_run(tmp_path):
    """The page reloads after each run; the scope once fell back to the first sample, so the next Run screened other
    records (seventh round, keyboard-only tester)."""
    js = read("app.js")
    body = js[js.index("async function loadScreen()"):]
    body = body[:body.index("\n}\n") + 3]
    script = tmp_path / "screen.js"
    script.write_text("""
const els = {};
function mk() {
  const e = { options: [], _v: "", disabled: false, querySelector: () => null };   // no development-gate refusal shown
  Object.defineProperty(e, "innerHTML", { set(h) {
    e.options = [...h.matchAll(/<option value="([^"]*)"/g)].map(m => ({ value: m[1] }));
    e._v = e.options.length ? e.options[0].value : ""; } });
  Object.defineProperty(e, "value", { get() { return e._v; }, set(v) {
    e._v = e.options.some(o => o.value === v) ? v : ""; } });
  return e;
}
const $ = (s) => els[s] || (els[s] = mk());
const esc = (x) => String(x);
const fillSelect = () => true, usageChip = () => "", usageText = () => "";
let scPicked = null; const screenRunLine = () => {}, Pickers = { llmPicker: async () => ({ chosen: "fake", row: null }) };
const Backends = { mixedBadge: () => "", expBadge: () => "" };
let scData = null; const renderScreenOverview = () => {}, renderScreenHistory = () => {}, screenRounds = () => [], roundLabel = () => "";
const loadModelsCard = async () => {}, loadSetsCard = async () => {}, loadFromSets = async () => {}, screenEstimate = () => {};
const cmpCard = { load: async () => {} }, loadPassPickers = async () => {};
let scRows = [];
const data = { prompts: [{ version: 1, status: "approved" }, { version: 2, status: "draft" }],
  samples: [{ name: "validation", n: 21 }, { name: "development", n: 35 }], protocols: [], runs: [] };
const api = async (u) => u === "/api/screen" ? data : { config: {} };
""" + body + """
(async () => {
  await loadScreen();
  const first = [$("#sc-scope").value, $("#sc-prompt").value];
  $("#sc-scope").value = "sample:development"; $("#sc-prompt").value = "1";
  await loadScreen();
  const kept = [$("#sc-scope").value, $("#sc-prompt").value];
  $("#sc-scope").value = "corpus"; data.samples = [];
  await loadScreen();
  console.log(JSON.stringify({ first, kept, corpus: $("#sc-scope").value }));
})();
""", encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=20, check=True).stdout
    assert json.loads(out) == {"first": ["sample:validation", "2"], "kept": ["sample:development", "1"], "corpus": "corpus"}


def test_saving_a_pasted_search_string_sends_one_request():
    js = read("app.js")
    save = js[js.index('$("#q-import-save").onclick'):]
    save = save[:save.index("\n};\n")]
    # the button is disabled before the request and enabled again afterwards, so a double click saves one version
    assert save.index("btn.disabled = true") < save.index("await api(") and "finally { btn.disabled = false; }" in save
    # an empty paste is refused on the page, before any request
    empty = save.index("if (!text.trim())")
    assert empty < save.index("btn.disabled = true") and save[empty:save.index("\n", empty)].rstrip().endswith("return; }")



def test_save_as_new_version_sends_one_request():
    """A double click on the construct editor's "Save as new version" sent two saves (eighth round, spec-checker)."""
    js = read("app.js")
    save = js[js.index('$("#art-save").onclick'):]
    save = save[:save.index("\n};\n")]
    assert save.index("if (btn.disabled) return;") < save.index("btn.disabled = true") < save.index("await api(")
    assert "finally { btn.disabled = false; }" in save


def test_rank_validation_shows_one_column_per_compared_top_n_and_its_notes():
    """With ranking.top_n_compare = [1, 3] the table read rank_top2, which does not exist (eighth round)."""
    js = read("app.js")
    body = js[js.index('$("#rk-validate").onclick'):]
    body = body[:body.index("\n};\n")]
    assert "rank_top1" not in body and "rank_top2" not in body and "rank_top3" not in body
    assert "const ns = Object.keys(v.top_n_compare" in body and 'r["rank_top" + n]' in body
    assert 'colspan="${ns.length + 2}"' in body and "(v.notes || []).map(" in body

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_fetch_pressed_again_while_starting_starts_one_lookup(tmp_path):
    """Pressing "Fetch next 100" again while its consent dialog was open stacked a second dialog (seventh round,
    keyboard-only tester)."""
    js = read("app.js")
    body = js[js.index("let enStarting = false;"):]
    body = body[:body.index("\n}\n") + 3]
    script = tmp_path / "enrich.js"
    script.write_text("""
const els = { "#en-limit": { value: "100" }, "#en-msg": { textContent: "" } };
const $ = (s) => els[s];
const enLimit = () => 100, enBatch = 100, enRecords = () => {}, renderEnrich = () => {}, showStageError = () => {};
let calls = 0, release;
const Jobs = { start: () => { calls++; return new Promise((r) => { release = r; }); } };
""" + body + """
(async () => {
  const first = startEnrich(false); startEnrich(false); startEnrich(true);
  const during = calls; release({}); await first;
  const again = startEnrich(false); release({}); await again;
  console.log(JSON.stringify({ during, after: calls }));
})();
""", encoding="utf-8")
    out = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=20, check=True).stdout
    assert json.loads(out) == {"during": 1, "after": 2}


def test_rank_keeps_the_keyboard_focus_and_enter_in_top_n_runs_it():
    js = read("app.js")
    run = js[js.index('$("#rk-run").onclick'):]
    run = run[:run.index("\n};\n")]
    # the focus is noted before the button is disabled, and given back to it when it fell to the page
    assert run.index("const hadFocus = document.activeElement === btn") < run.index("btn.disabled = true")
    fin = run[run.index("finally {"):]
    assert "if (hadFocus && (!document.activeElement || document.activeElement === document.body)) btn.focus();" in fin
    assert '$("#rk-topn").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); $("#rk-run").click(); } };' in js


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_consent_dialogs_say_each_fact_once():
    """The author's hand check M27 (23 Oct 2026): the backend consent dialog showed the CLI's long message (destination,
    what is sent, warning and a CLI command) and then the same facts again, so the decision was hard to see."""
    js = read("common.js")
    lookup = js[js.index("function askLookupConsent"):js.index("// The backend consent dialog")]
    assert "info.message" not in lookup
    builder = js[js.index("function backendConsentHtml("):js.index("// Ask for backend consent:")]
    info = {"backend": "lan-test", "kind": "llm", "operation": "probe", "locality": "network",
            "destination": "Local network: 192.168.1.20 (another computer on your network: data leaves this computer)",
            "sent": "the fixed test question (17 + 25) with the API key header; no project data",
            "warning": "research data would travel unencrypted (http) to 192.168.1.20", "changed": []}
    script = ("const esc = s => String(s);\n" + builder
              + f"\nprocess.stdout.write(backendConsentHtml({json.dumps(info)}));")
    html = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, check=True).stdout
    assert html.count("192.168.1.20") == 2                       # once where it sends to, once in the warning
    assert html.count("unencrypted") == 1 and "wider providers allow" not in html
    assert '<p class="consent-warn" role="note"><b>Warning:</b> research data' in html
    assert html.index("Sends to:") < html.index("Warning:") < html.index("What is sent:")


def test_const_modules_are_not_checked_on_window():
    """A page module declared as a top-level ``const Name = (() => ...)()`` is a global binding but never a property of
    ``window`` in a browser, so ``window.Name ? ... : stub`` always takes the stub: the compare card and the "Add
    boundary cases" button were never drawn (found in the screening-config-comparison walkthrough). Check such a module
    with ``typeof Name !== "undefined"``. A module its file puts on ``window`` (``window.Name = Name;``, as common.js does
    with ``WiderName``) is a property, so ``window.Name`` is right for it."""
    static = STATIC
    srcs = [f.read_text(encoding="utf-8") for f in static.glob("*.js")]
    exported = {m for src in srcs for m in re.findall(r"^window\.([A-Z][A-Za-z]*) = \1;", src, re.M)}
    names = {m for src in srcs for m in re.findall(r"^const ([A-Z][A-Za-z]*) = \(\(\) =>", src, re.M)} - exported
    assert {"Compare", "SampleExtend"} <= names and "WiderName" in exported
    bad = [(f.name, n) for f in static.glob("*.js") for n in names
           if re.search(rf"window\.{n}\b", f.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_screen_page_sections_each_have_their_own_card():
    # hand check M31: "Models used" sat in the run card right under the runs table, with no separation
    html = read("index.html")
    page = html[html.index('<section id="page-screen"'):]
    page = page[:page.index("</section>")]
    cards = re.split(r'<(?:div|details) class="card', page)[1:]
    for heading in ("Start screening", "Models used", "Protocol decisions"):
        holding = [c for c in cards if f"<h2>{heading}" in c]
        assert len(holding) == 1, heading
        assert sum(c.count("<h2>") for c in holding) == 1, heading
    assert ".tab-panel > .card { margin-bottom:16px; }" in read("style.css")
    # every run setting carries a visible name, and each section says what it is for in normal-size text
    run = next(c for c in cards if "<h2>Start screening" in c)
    for field in ("sc-prompt", "sc-scope", "sc-mode", "sc-pass", "sc-protocol", "sc-limit"):
        assert re.search(rf'<label class="inline">[A-Z][a-z ]+<(select|input) id="{field}"', run), field
    for card in cards:
        if "<h2>" in card and 'id="sc-models"' not in card[:40]:
            assert 'class="section-intro"' in card or "<h2>Models used" in card, card[:80]
    assert 'class="muted small">Export CSV writes' not in page


def test_screen_page_is_split_into_three_task_tabs():
    # author, 25 Oct 2026 (screening-page-tasks D7): three researcher tasks across the top, the summary card above
    # them, the screening history inside Set up and run, and no stage-result card of its own
    html = read("index.html")
    page = html[html.index('<section id="page-screen"'):]
    page = page[:page.index("</section>")]
    tabs = re.findall(r'role="tab" id="sc-tabbtn-(\w+)" data-tab="\1" aria-controls="sc-tab-\1">([^<]+)</button>', page)
    assert tabs == [("setup", "1 Set up and run"), ("review", "2 Decisions and result sets"), ("quality", "3 Check quality")]
    panels = {k: page[page.index(f'data-panel="{k}"'):] for k, _ in tabs}
    for k, (nxt, _) in zip([k for k, _ in tabs], tabs[1:] + [(None, None)]):
        if nxt:
            panels[k] = panels[k][:panels[k].index(f'data-panel="{nxt}"')]
    where = {"sc-models": "setup", "sc-run": "setup", "sc-runs": "setup", "sc-rounds-card": "setup",
             "sc-protocols": "review", "in-table": "review", "ss-table": "review", "mu-out": "review",
             "ev-table": "quality", "adj-cases": "quality", "sc-passes": "setup", "cmp-card": "quality"}
    for el, k in where.items():
        assert [p for p, body in panels.items() if f'id="{el}"' in body] == [k], el
    for k in panels:  # each tab names its panel, and every panel starts hidden until the script picks one
        assert f'id="sc-tab-{k}" aria-labelledby="sc-tabbtn-{k}" hidden>' in page
    assert page.index('id="sc-overview"') < page.index('role="tablist"')
    assert '<details class="card sc-history" id="sc-rounds-card">' in panels["setup"]
    assert 'data-result="screen"' not in page and 'class="stage-result' not in page
    js = read("app.js")
    assert 'document.querySelectorAll(".page-tabs").forEach(initPageTabs);' in js
    assert "ArrowRight" in js and "sessionStorage" in js
