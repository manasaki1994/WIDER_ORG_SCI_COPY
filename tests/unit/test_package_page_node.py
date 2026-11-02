"""reviewer-roles-and-blinding, groups 2 and 3: the review page of a package, run under Node on the small DOM of
``a11y_dom`` from the file WIDER writes (its body, data block and inlined script). Synthetic records only; the page
makes no request (``fetch`` and ``XMLHttpRequest`` fail the run)."""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from tests.unit.a11y_dom import DOM, NODE
from tests.unit.test_package import MARK, _project
from tests.unit.test_screen import CODEBOOK
from wider.sample import package as pk

CODEBOOK_Q = CODEBOOK["question"]


def have_node() -> bool:
    return bool(NODE)


STUBS = r"""
global.downloads = [];
global.Blob = class { constructor(parts, o) { this.parts = parts; this.type = (o || {}).type; } };
global.URL = { createObjectURL: (b) => { downloads.push({ blob: b }); return "blob:x" + downloads.length; }, revokeObjectURL() {} };
const _origCreate = document.createElement;
document.createElement = (t) => { const e = _origCreate(t); if (t === "a") { const c = e.click.bind(e); e.click = () => { const d = downloads[downloads.length - 1]; if (d) d.name = e.getAttribute("download"); c(); }; } return e; };
global.files = {};
global.FileReader = class { readAsText(f) { this.result = f.text; setTimeout(() => this.onload && this.onload(), 0); } };
global.fetch = () => { throw new Error("the page made a request"); };
global.XMLHttpRequest = class { constructor() { throw new Error("the page made a request"); } };
global.isContentEditable = false;
global.visibleText = () => { const out = []; const go = (n) => { for (const c of n.childNodes) { if (c.nodeType === 3) out.push(c.data);
  else if (c.tagName !== "SCRIPT" && c.tagName !== "STYLE" && !c.hidden) go(c); } }; go(document.body); return out.join(" ").replace(/\s+/g, " "); };
global.allText = () => { const out = []; const go = (n) => { for (const c of n.childNodes) { if (c.nodeType === 3) out.push(c.data);
  else if (c.tagName !== "SCRIPT" && c.tagName !== "STYLE") go(c); } }; go(document.body); return out.join(" ").replace(/\s+/g, " "); };
global.lastExport = () => JSON.parse(downloads[downloads.length - 1].blob.parts[0]);
global.btn = (word) => [...document.querySelectorAll("#rp-item .rp-decide button")].find((b) => b.textContent.startsWith(word));
"""


def page_parts(path) -> tuple[str, str]:
    """(the body's markup with the data block, the inlined page script) of a package file."""
    text = path.read_text(encoding="utf-8")
    body = text[text.index("<body>") + len("<body>"):text.index("</body>")]
    script = re.search(r"<script>(.*)</script>", body, re.S).group(1)
    markup = body.replace(f"<script>{script}</script>", "")
    return markup, script


def run_page(path, body: str, pre: str = "") -> dict:
    if not NODE:
        pytest.skip("node is not installed")
    markup, script = page_parts(path)
    code = (f"{DOM}\n{STUBS}\ndocument.body.innerHTML = {json.dumps(markup)};\n"
            "global.out = (x) => { process.stdout.write(JSON.stringify(x), () => process.exit(0)); };\n"
            f"{pre}\n{script}\n;(async () => {{\n{body}\n}})().catch(e => {{ console.error(e && e.stack || e); process.exit(1); }});")
    r = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-3000:]
    return json.loads(r.stdout or "null")


@pytest.fixture
def made(tmp_path):
    p = _project(tmp_path)
    res = pk.make(p, "R3", ["validation"], "second", out=tmp_path / "r3.html")
    return p, res, tmp_path / "r3.html"


def test_keys_and_mouse_decide(made, tmp_path):
    p, res, f = made
    out = run_page(f, """
press("1");                                    // the first item: include
press("j");
$("#rp-reason").value = "a"; $("#rp-reason").dispatchEvent(new Event("change"));
const no = [...document.querySelectorAll('input[name="tick-e1"]')].find((r) => r.value === "no");
no.checked = true; no.dispatchEvent(new Event("change"));
document.body.focus(); press("2");
press("j");
btn("Maybe").click();
$("#rp-tags").value = "check full text"; $("#rp-tags").dispatchEvent(new Event("input"));
$("#rp-note").value = "a note"; $("#rp-note").dispatchEvent(new Event("input"));
$("#rp-export").click();
out({ progress: $("#rp-progress").textContent, file: lastExport(), name: downloads[0].name });
""")
    assert out["progress"] == "3 of 28 decided"
    f_ = out["file"]
    assert out["name"] == f"wider-decisions-R3-{res['package_id']}.json"
    assert set(f_) == {"package_id", "project_id", "reviewer", "codebook_version", "export_seq", "exported_at", "items"}
    assert (f_["package_id"], f_["project_id"], f_["reviewer"], f_["codebook_version"], f_["export_seq"]) == \
        (res["package_id"], pk.project_id(p), "R3", 3, 1)
    toks = [it["token"] for it in pk.items(p, res["package_id"])]
    got = [(e["item"], e["decision"], e["reason"], e["ticks"], e["tags"], e["note"]) for e in f_["items"]]
    assert got == [(toks[0], "include", "", {}, [], ""), (toks[1], "exclude", "a", {"e1": "no"}, [], ""),
                   (toks[2], "maybe", "", {}, ["check full text"], "a note")]
    assert all(set(e) == {"item", "decision", "reason", "ticks", "tags", "note", "decided_at"} and e["decided_at"]
               for e in f_["items"])


def test_export_holds_only_entries(made):
    p, res, f = made
    out = run_page(f, """press("3"); $("#rp-export").click(); $("#rp-export").click();
out({ a: downloads[0].blob.parts[0], b: lastExport() });""")
    assert out["b"]["export_seq"] == 2 and len(out["b"]["items"]) == 1
    text = out["a"]
    for word in ("record_id", "sample", "title", "abstract", "R1", "validation"):
        assert word not in text, word


def test_keys_ignored_in_text_fields(made):
    p, res, f = made
    out = run_page(f, """
for (const id of ["#rp-note", "#rp-tags", "#rp-reason"]) {
  $(id).focus();
  for (const k of ["1", "2", "j", "k", "3"]) press(k);
}
$("#rp-reason").value = "other"; $("#rp-reason").dispatchEvent(new Event("change"));
$("#rp-reason-other").focus(); for (const k of ["1", "j"]) press(k);
out({ progress: $("#rp-progress").textContent, title: $("#rp-item-title").textContent,
      current: $("#rp-items button[aria-current]").textContent });
""")
    assert out["progress"] == "0 of 28 decided" and out["current"].startswith("1. ")


def test_criteria_panel_and_notice(made):
    p, res, f = made
    out = run_page(f, """
const before = $("#rp-criteria").hidden;
$("#rp-criteria-toggle").click();
out({ before, after: $("#rp-criteria").hidden, expanded: $("#rp-criteria-toggle").getAttribute("aria-expanded"),
      text: visibleText(), all: allText(), criteria: $("#rp-criteria").textContent, notice: $("#rp-notice").textContent,
      storage: $("#rp-storage").hidden, limitsTag: $("#rp-limits").tagName, limitsClass: $("#rp-limits").className });
""")
    assert out["before"] is True and out["after"] is False and out["expanded"] == "true"
    notice = ("This page holds no other person's decisions and no model output. Your work stays in this browser until "
              "you export it.")
    assert out["notice"] == notice and out["all"].count(notice) == 1 and out["storage"] is True
    assert out["all"].count(pk.TEXTS["limits"]) == 1 and pk.TEXTS["reviewers"] not in out["all"]
    assert out["limitsTag"] == "P" and out["limitsClass"] == ""
    assert CODEBOOK_Q in out["criteria"] and "Voice behaviour measured" in out["criteria"]
    assert "a: no voice measured" in out["criteria"] and "Criteria (version 3)" in out["criteria"]


def test_texts_rendered_as_text(tmp_path):
    p = _project(tmp_path, n_dev=2)
    st = p.store
    title, ab = "<script>alert(1)</script> title", "<img src=https://example.org/x> and </script> end"
    st.execute("UPDATE records SET title=?, abstract=? WHERE record_id IN (1,2)", (title, ab))
    st.execute("UPDATE record_abstracts SET abstract=? WHERE record_id IN (1,2)", (ab,))
    pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "f.html")
    out = run_page(tmp_path / "f.html", """
out({ title: $("#rp-item-title").textContent, abstract: $(".rp-abstract").textContent,
      imgs: document.querySelectorAll("img").length, scripts: document.querySelectorAll("#rp script").length,
      html: $("#rp-item").innerHTML });
""")
    assert out["title"] == title and out["abstract"] == ab
    assert out["imgs"] == 0 and out["scripts"] == 0 and "<img" not in out["html"] and "&lt;img" in out["html"]


def test_source_mark_opens_provenance(tmp_path):
    from wider.enrich import abstracts
    p = _project(tmp_path, n_dev=1, n_pos=1, n_exc=1)
    abstracts.add_version(p.store, 1, " ".join(["word"] * 240), "openalex", at="2026-03-04T00:00:00+00:00")
    pk.make(p, "R2", ["dev"], "independent", out=tmp_path / "f.html")
    out = run_page(tmp_path / "f.html", """
const m = $(".rp-source"), prov = $(".rp-provenance");
const closed = prov.hidden; m.click();
out({ mark: m.textContent, closed, open: !prov.hidden, prov: prov.textContent, exp: m.getAttribute("aria-expanded") });
""")
    assert out["mark"] == "abstract: OpenAlex" and out["closed"] is True and out["open"] is True
    assert out["prov"] == "Source: OpenAlex, 240 words, retrieved 2026-03-04" and out["exp"] == "true"


def test_work_restored_after_reload(made):
    p, res, f = made
    first = run_page(f, """for (let i = 0; i < 5; i++) { press("1"); press("j"); }
out({ stored: localStorage.getItem("wider-package:" + JSON.parse($("#data").textContent).package_id) });""")
    stored = first["stored"]
    assert stored and len(json.loads(stored)["entries"]) == 5
    again = run_page(f, "out({ progress: $('#rp-progress').textContent });",
                     pre=f"localStorage.setItem({json.dumps('wider-package:' + res['package_id'])}, {json.dumps(stored)});")
    assert again["progress"] == "5 of 28 decided"


def test_storage_failure_said_once(made):
    p, res, f = made
    out = run_page(f, """press("1"); out({ all: allText(), storage: $("#rp-storage").textContent, hidden: $("#rp-storage").hidden,
progress: $("#rp-progress").textContent });""",
                   pre="global.localStorage = { getItem() { throw new Error('no'); }, setItem() { throw new Error('no'); }, removeItem() {} };")
    msg = "This browser does not keep your work in this page. Export your decisions before you close it."
    assert out["all"].count(msg) == 1 and out["hidden"] is False
    assert "Your work stays in this browser" not in out["all"] and out["progress"] == "1 of 28 decided"


def test_continue_from_file(made):
    p, res, f = made
    toks = [it["token"] for it in pk.items(p, res["package_id"])]
    earlier = {"package_id": res["package_id"], "project_id": pk.project_id(p), "reviewer": "R3", "codebook_version": 3,
               "export_seq": 4, "exported_at": "2026-10-09T14:00:00Z",
               "items": [{"item": toks[0], "decision": "exclude", "reason": "b", "ticks": {}, "tags": [], "note": "",
                          "decided_at": "x"}, {"item": toks[1], "decision": "maybe", "reason": "", "ticks": {},
                                               "tags": ["t"], "note": "n", "decided_at": "x"}]}
    other = {**earlier, "package_id": "p-20990101-ffffff"}
    out = run_page(f, f"""
const input = $("#rp-continue");
input.files = [{{ text: {json.dumps(json.dumps(other))} }}]; input.dispatchEvent(new Event("change")); await flush(); await flush();
const refused = $("#rp-msg").textContent, p0 = $("#rp-progress").textContent;
input.files = [{{ text: {json.dumps(json.dumps(earlier))} }}]; input.dispatchEvent(new Event("change")); await flush(); await flush();
const ok = $("#rp-msg").textContent;
$("#rp-export").click();
out({{ refused, p0, ok, progress: $("#rp-progress").textContent, file: lastExport() }});
""")
    assert out["refused"] == "This decisions file belongs to another package." and out["p0"] == "0 of 28 decided"
    assert out["ok"] == "Continued from the file: 2 decisions restored." and out["progress"] == "2 of 28 decided"
    assert out["file"]["export_seq"] == 5
    assert [(e["item"], e["decision"]) for e in out["file"]["items"]] == [(toks[0], "exclude"), (toks[1], "maybe")]


def test_page_text_has_no_marker(tmp_path):
    """The text the page renders (and every element's attributes) holds no marker, sample word or other name."""
    p = _project(tmp_path)
    st = p.store
    st.execute("UPDATE labels SET reason=?, note=?, tags=? WHERE reviewer_id='R1'", (MARK + "reason", MARK + "note",
                                                                                     json.dumps([MARK + "tag"])))
    st.execute("UPDATE sample_rows SET band=?, pool=?", (MARK + "band", MARK + "pool"))
    st.execute("UPDATE samples SET name=? WHERE name='validation'", (MARK + "sample",))
    pk.make(p, "R3", [MARK + "sample"], "second", out=tmp_path / "s.html")
    out = run_page(tmp_path / "s.html", """
$("#rp-criteria-toggle").click();
const texts = [];
for (let i = 0; i < 28; i++) { $$(".rp-source").forEach((b) => b.click()); texts.push(allText()); press("j"); }
out({ text: texts.join(" "), html: $("#rp").innerHTML });
""")
    blob = out["text"] + out["html"]
    assert MARK not in blob and MARK.lower() not in blob
    own = re.sub(r"Record \d+ title|Abstract text of record \d+ about a neutral topic with several words in it\.", "", out["text"])
    own = own.replace(CODEBOOK_Q, "")
    for word in ("second-reviewer", "independent", "development", "validation", "probability", "R1", "Owner"):
        assert word not in own, word
