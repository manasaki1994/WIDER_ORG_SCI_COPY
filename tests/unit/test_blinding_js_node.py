"""reviewer-roles-and-blinding, groups 5 and 6: the "Review packages" card (blinding.js) on the small DOM with the real
in-page dialog of common.js, and the reviewer-versus-reviewer table of records.js on its stand-in page. Synthetic
names only; requests are answered from ``replies``."""
from __future__ import annotations

import json
import re

from tests.unit.a11y_dom import STATIC, run_dom, section
from tests.unit.test_drawer_keys_node import run_node
from tests.unit.test_ui_followup_r31 import QUEUE_PAGE, queue_name
from wider.sample import package as pk

LEAVING3 = pk.leaving_text(28, 3, "R3")
STATUS = {"texts": [pk.TEXTS["reviewers"], pk.TEXTS["limits"]], "packages": [
    {"package_id": "p-20261002-aaaaaa", "reviewer": "R3", "mode": "second", "kind": "validation", "samples": ["validation"],
     "n_items": 28, "codebook_version": 3, "frozen": True, "earlier": None, "made_at": "2026-10-02T09:00:00+00:00",
     "imported_at": "2026-10-09T09:00:00+00:00", "withdrawn_at": None, "withdraw_reason": None, "decided": 28, "labels": 28,
     "open": False, "state": "imported 9 Oct (28 of 28 decided)",
     "lines": ["validation gained 40 records after this package was made; they are not in it"],
     "download_name": "review-package-R3-p-20261002-aaaaaa.html"},
    {"package_id": "p-20261025-bbbbbb", "reviewer": "R2", "mode": "independent", "kind": "development", "samples": ["dev"],
     "n_items": 20, "codebook_version": 3, "frozen": True, "earlier": None, "made_at": "2026-10-25T09:00:00+00:00",
     "imported_at": None, "withdrawn_at": None, "withdraw_reason": None, "decided": 0, "labels": 0, "open": True,
     "state": "out since 25 Oct, not imported", "lines": [], "download_name": "review-package-R2-p-20261025-bbbbbb.html"}]}
PREVIEW = {"reviewer": "R3", "n_items": 28, "kind": "validation", "mode": "second", "codebook_version": 3, "frozen": True,
           "versions": [], "leaving": LEAVING3, "leaving_by_version": {}, "earlier": None, "notes": []}
GREY = re.compile(r'class="[^"]*\b(muted|small)\b')

PRE = r"""
global.FormData = class { constructor() { this.parts = []; } append(k, v) { this.parts.push([k, v]); } };
global.offerUndo = (id, t) => { (global.undos ||= []).push([id, t]); };
"""


def card(body: str, status=None, preview=None) -> dict:
    pre = PRE + f"""
replies["/api/blinding/status"] = {json.dumps(status or STATUS)};
const PRE = {json.dumps(preview or PREVIEW)};
// the dialog asks again when a choice changes: a re-review adds 12 records, another version changes the sentence
replies["/api/blinding/package/preview"] = (url, o) => {{ const b = JSON.parse(o.body), r = JSON.parse(JSON.stringify(PRE));
  if (b.earlier === "re-review") r.n_items += 12;
  if (b.codebook_version && r.leaving_by_version[String(b.codebook_version)]) r.leaving = r.leaving_by_version[String(b.codebook_version)];
  else if (b.codebook_version) r.leaving = r.leaving.replace("(codebook version 3)", `(codebook version ${{b.codebook_version}})`);
  if (b.earlier === "re-review") r.leaving = r.leaving.replace("of 28 records", "of 40 records");
  return r; }};
replies["/api/blinding/package"] = {{ package_id: "p-20261026-cccccc", reviewer: "R3", n_items: 28, leaving: {json.dumps(LEAVING3)},
  notes: [], download_name: "review-package-R3-p-20261026-cccccc.html", change_id: 5, codebook_version: 3 }};
"""
    return run_dom(f"await Blinding.load({{ dev: {{}}, validation: {{}} }}); await flush();\n{body}",
                   html=section("page-sample"), scripts=("common.js", "blinding.js"), pre=pre)


FILL = """$("#bp-reviewer").value = "R3"; $('[data-bp-sample="validation"]').checked = true; $("#bp-mode").value = "second";"""


def test_texts_once_on_card():
    for st in (STATUS, {**STATUS, "packages": []}):
        out = card("out({ text: $('#bp-card').textContent, rev: $('#bp-reviewers').outerHTML, lim: $('#bp-limits').outerHTML });",
                   status=st)
        assert out["text"].count(pk.TEXTS["reviewers"]) == 1 and out["text"].count(pk.TEXTS["limits"]) == 1
        assert out["lim"] == f"<p id=\"bp-limits\">{pk.TEXTS['limits']}</p>"   # no grey colour, no smaller size
        assert not GREY.search(out["rev"]) and not GREY.search(out["lim"])


def test_package_list():
    out = card("out({ list: $('#bp-list').textContent, html: $('#bp-list').innerHTML });")
    assert "out since 25 Oct, not imported" in out["list"]
    assert out["list"].count("validation gained 40 records after this package was made; they are not in it") == 1
    assert "p-20261002-aaaaaa" in out["list"] and "R2" in out["list"] and "independent" in out["list"]
    assert 'href="/api/blinding/package/p-20261002-aaaaaa/file" download="review-package-R3-p-20261002-aaaaaa.html"' in out["html"]
    assert out["html"].count('data-withdraw="') == 2


def test_dialog_says_file_leaves_computer():
    out = card(FILL + """
$("#bp-make").click(); await flush(); await flush();
const dlg = document.querySelector("dialog.choice-dialog");
const text = dlg.textContent, leaving = $("#bp-leaving").outerHTML;
const order = [...dlg.querySelectorAll("p, button")].map((e) => e.id || e.textContent);
dlg.querySelector('[data-act="ok"]').click(); await flush(); await flush(); await flush();
const clicks = [];
out({ text, leaving, order, posts: posts(), msg: $("#bp-msg").textContent });
""")
    assert out["text"].count(LEAVING3) == 1 and out["leaving"] == f'<p id="bp-leaving">{LEAVING3}</p>'
    assert out["order"].index("bp-leaving") < out["order"].index("Download")
    posted = [c for c in out["posts"] if c[0] == "/api/blinding/package"]
    assert posted[0][2] == {"reviewer": "R3", "mode": "second", "samples": ["validation"], "earlier": None,
                            "codebook_version": None}
    assert out["msg"].startswith("Review package p-20261026-cccccc for R3: 28 records.")


def test_download_link_names_file():
    out = card(FILL + """
const made = []; const orig = document.createElement;
document.createElement = (t) => { const e = orig(t); if (t === "a") { e.click = () => made.push([e.getAttribute("href"), e.getAttribute("download")]); } return e; };
$("#bp-make").click(); await flush(); await flush();
document.querySelector('dialog.choice-dialog [data-act="ok"]').click(); await flush(); await flush(); await flush();
out({ made });
""")
    assert out["made"] == [["/api/blinding/package/p-20261026-cccccc/file", "review-package-R3-p-20261026-cccccc.html"]]


def test_dialog_asks_earlier_choice():
    prior = [{"made_in": "wider"}] * 8 + [{"made_in": "blind queue"}] * 4
    pre = {**PREVIEW, "earlier": {"n": 12, "queue": 4, "message": pk.earlier_message("R2", prior),
                                  "page_message": pk.earlier_message("R2", prior, page=True),
                                  "meanings": pk.earlier_meanings("R2"), "choice": None}}
    out = card(FILL + """
$("#bp-make").click(); await flush(); await flush();
const dlg = document.querySelector("dialog.choice-dialog");
const text = dlg.textContent, okBefore = dlg.querySelector('[data-act="ok"]').disabled;
$("#bp-earlier").value = "re-review"; $("#bp-earlier").dispatchEvent(new Event("change")); await flush(); await flush();
const title = dlg.querySelector("h3").textContent, leaving = $("#bp-leaving").textContent;
const okAfter = dlg.querySelector('[data-act="ok"]').disabled;
dlg.querySelector('[data-act="ok"]').click(); await flush(); await flush();
out({ text, okBefore, okAfter, title, leaving, posts: posts() });
""", preview=pre)
    assert out["text"].count(pre["earlier"]["page_message"]) == 1 and out["text"].count(pk.earlier_meanings("R2")) == 1
    assert "4 of them from the blind queue" in out["text"] and "--earlier" not in out["text"]
    assert out["okBefore"] is True and out["okAfter"] is False   # Download waits for the choice
    assert out["title"] == "Review package for R3: 40 records" and "of 40 records" in out["leaving"]   # follows it
    posted = [c for c in out["posts"] if c[0] == "/api/blinding/package"][0][2]
    assert posted["earlier"] == "re-review"


def test_dialog_asks_codebook_version():
    pre = {**PREVIEW, "codebook_version": None, "versions": [3, 4], "leaving": None,
           "leaving_by_version": {"3": LEAVING3, "4": pk.leaving_text(28, 4, "R3")},
           "notes": ["R1's labels on validation use codebook versions 3 and 4; choose one with --codebook-version."]}
    out = card(FILL + """
$("#bp-make").click(); await flush(); await flush();
const opts = [...$("#bp-version").querySelectorAll("option")].map((o) => o.value);
const first = $("#bp-leaving").textContent;
$("#bp-version").value = "4"; $("#bp-version").dispatchEvent(new Event("change")); await flush(); await flush();
const second = $("#bp-leaving").textContent, text = document.querySelector("dialog.choice-dialog").textContent;
document.querySelector('dialog.choice-dialog [data-act="ok"]').click(); await flush(); await flush();
out({ opts, first, second, text, posts: posts() });
""", preview=pre)
    assert out["opts"] == ["3", "4"] and out["first"] == LEAVING3 and out["second"] == pk.leaving_text(28, 4, "R3")
    assert "--codebook-version" not in out["text"]
    assert [c for c in out["posts"] if c[0] == "/api/blinding/package"][0][2]["codebook_version"] == 4


def test_import_posts_file():
    out = card("""
replies["/api/blinding/import"] = { lines: ["R3: 20 of 28 decided; 20 imported."], change_id: 8 };
const input = $("#bp-import"); input.files = [{ name: "wider-decisions-R3-p.json" }];
input.dispatchEvent(new Event("change")); await flush(); await flush(); await flush();
const c = calls.find((x) => x[0] === "/api/blinding/import");
out({ method: c[1], form: c[2] && c[2].parts ? c[2].parts.map((p) => [p[0], p[1].name]) : String(c[2]), msg: $("#bp-msg").textContent, undos });
""")
    assert out["method"] == "POST" and out["msg"] == "R3: 20 of 28 decided; 20 imported." and out["undos"] == [[8, "Decisions imported."]]


def test_withdraw_asks_reason():
    out = card("""
replies["/api/blinding/package/p-20261025-bbbbbb/withdraw"] = (url, o) => { const b = JSON.parse(o.body);
  if (!b.reason) { const e = new Error("Give a reason."); e.status = 400; return e; }
  return { package_id: "p-20261025-bbbbbb", withdraw_reason: b.reason, change_id: 6 }; };
$('[data-withdraw="p-20261025-bbbbbb"]').click(); await flush();
let dlg = document.querySelector("dialog.choice-dialog"); const title = dlg.textContent;
dlg.querySelector('[data-act="ok"]').click(); await flush(); await flush();
const empty = $("#bp-msg").textContent;
$('[data-withdraw="p-20261025-bbbbbb"]').click(); await flush();
dlg = document.querySelector("dialog.choice-dialog");
dlg.querySelector('[data-field="reason"]').value = "R2 left the project";
dlg.querySelector('[data-act="ok"]').click(); await flush(); await flush();
out({ title, empty, msg: $("#bp-msg").textContent, undos });
""")
    assert "Withdraw review package p-20261025-bbbbbb" in out["title"] and "Reason" in out["title"]
    assert out["empty"] == "Give a reason."
    assert out["msg"] == "Review package p-20261025-bbbbbb withdrawn: R2 left the project"
    assert out["undos"] == [[6, "Package withdrawn."]]


# ------------------------------------------------------------------------------------------------ records.js (5.2)
SIDE = {"decision": "include", "reason": "", "ticks": {"e1": "yes"}, "tags": ["check full text"], "note": "a note",
        "made_in": "p-20261002-aaaaaa", "blinded": "yes", "left_out": False, "abstract_version": 2,
        "abstract": "OpenAlex, 240 words"}
COMPARE = {"sample": "validation", "a": "R1", "b": "R3", "locked": False, "rows": [
    {"record_id": 41, "title": "Record 41 title", "agree": False, "adjudicated": None,
     "a": {**SIDE, "decision": "exclude", "reason": "a", "ticks": {}, "tags": [], "note": "", "made_in": "wider",
           "blinded": "no", "left_out": True, "abstract_version": 1, "abstract": "imported, 80 words"},
     "b": SIDE, "different_abstracts": "R1 and R3 read different abstracts (imported, 80 words; OpenAlex, 240 words)"}]}


def table(body: str, agreement: dict) -> dict:
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    return run_node(QUEUE_PAGE + queue_name("Owner") + rec + f"""
const AG = {json.dumps(agreement)}, CMP = {json.dumps(COMPARE)};
apiReply = (p) => p.startsWith("/api/labels/agreement") ? AG : p.startsWith("/api/labels/compare") ? CMP
  : p.startsWith("/api/records/") ? {{ record: {{ title: "Record 41 title", abstract: "x" }} }} : p.startsWith("/api/labels/adjudicate") ? {{ ok: true, change_id: 3 }} : {{}};
$("#ag-sample").value = "validation"; $("#ag-a").value = "R1"; $("#ag-b").value = "R3";
await $("#ag-run").onclick(); await flush(); await flush();
{body}""")


AGREE = {"n": 28, "agreement": 0.857, "kappa": 0.7, "disagreements": [{"record_id": 41}],
         "left_out": {"n": 12, "packages": ["p-q"], "line": "12 labels left out by review package p-q"}}


def test_agreement_summary_says_left_out():
    out = table('console.log(JSON.stringify({ s: $("#ag-summary").textContent }));', AGREE)
    assert out["s"].count("12 labels left out by review package p-q") == 1 and out["s"].startswith("Both labelled 28 records")
    out = table('console.log(JSON.stringify({ s: $("#ag-summary").textContent }));',
                {k: v for k, v in AGREE.items() if k != "left_out"})
    assert "left out" not in out["s"]


def test_reviewer_table_shows_details_no_model():
    out = table("""
const cfg = opened[opened.length - 1] || null;
const html = $("#ag-disagreements").innerHTML;
let drawer = "";
const u = { call: (p) => api(p), recordBlock: (r) => `<h2>${r.title}</h2>`, h: esc };
global.Drawer.author = () => "Owner";
calls.length = 0;
console.log(JSON.stringify({ html, calls }));""", AGREE)
    html = out["html"]
    assert "blind, review package" in html and "made in WIDER" in html and "left out of agreement" in html
    assert "R1 and R3 read different abstracts (imported, 80 words; OpenAlex, 240 words)" in html
    # the drawer: both sides with their details, the abstract line, a reason sent with the resolution, no model field
    rec = (STATIC / "records.js").read_text(encoding="utf-8")
    res = run_node(QUEUE_PAGE + queue_name("Owner") + rec + f"""
const CMP = {json.dumps(COMPARE)};
apiReply = (p) => p.startsWith("/api/labels/agreement") ? {json.dumps(AGREE)} : p.startsWith("/api/labels/compare") ? CMP
  : p.startsWith("/api/records/") ? {{ record: {{ title: "Record 41 title", abstract: "x" }} }} : {{ ok: true, change_id: 3 }};
global.call = global.api;
$("#ag-sample").value = "validation"; $("#ag-a").value = "R1"; $("#ag-b").value = "R3";
await $("#ag-run").onclick(); await flush(); await flush();
global.Drawer.bindRows = (t, fn) => {{ global.openRow = fn; }};
$("#ag-run").onclick && await $("#ag-run").onclick(); await flush(); await flush();
global.Drawer.author = () => "Owner";  // set before opening: the save is signed by the name shown when opened
openRow(0);
const cfg = opened[opened.length - 1];
const u = {{ call: (p) => api(p), recordBlock: (r) => "<h2>" + r.title + "</h2>", h: esc }};
const body = await cfg.render(cfg.items[0], u);
els["#ag-adj-reason"] = {{ value: "both read the full text" }};
await cfg.decisions[0].fn(cfg.items[0]);
console.log(JSON.stringify({{ body, posted: calls.filter((c) => c[0] === "/api/labels/adjudicate").map((c) => c[1]) }}));""")
    b = res["body"]
    for s in ("criteria: e1 yes", "tags: check full text", "a note", "abstract: OpenAlex, 240 words",
              "abstract: imported, 80 words", "blind, review package", "Reason for the resolution"):
        assert s in b, s
    assert res["posted"] == [{"sample": "validation", "record_id": 41, "decision": "include", "by": "Owner",
                              "reason": "both read the full text"}]
    payload = json.dumps(COMPARE)
    for word in ("model", "confidence", "rule_decision", "parsed", "provider"):
        assert word not in payload
