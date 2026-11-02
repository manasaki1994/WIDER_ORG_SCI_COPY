"""The Methods page (methods.js) and the guide panel's "Why WIDER works this way:" links (shell.js), under Node on the
small DOM of ``a11y_dom`` (methods-explainer D8, tasks 3.3 and 3.4). The page is fed the shipped topics through the
same dicts the API returns, or a synthetic topic for the literal-markup case; no project, no network."""
from __future__ import annotations

import json

from tests.unit.a11y_dom import run_dom, section
from tests.unit.test_shell_js_node import shell_page, stages
from wider.guide import methods as m

TOPIC = "stratified-sampling"


def _index() -> dict:
    """What GET /api/methods answers (wider/ui/routes_notebook.py)."""
    return {"topics": m.topics(), "glossary": m.glossary(), "glossary_review": m.glossary_review()}


def _page(body: str, sub: str = "", topics: dict | None = None, index: dict | None = None) -> dict:
    """Methods.load(sub) on the page's own section, with the API answering from ``index`` and ``topics``."""
    replies = {"/api/methods": index or _index()}
    for tid, t in (topics or {t["id"]: m.topic(t["id"]) for t in m.topics()}).items():
        replies[f"/api/methods/{tid}"] = t
    return run_dom(f"""
      Object.assign(replies, {json.dumps(replies)});
      const view = $("#methods-view"), list = $("#methods-list"), status = $("#methods-status");
      await Methods.load({json.dumps(sub)}); await flush();
      {body}
    """, html=section("page-methods"), scripts=("common.js", "methods.js"))


DUMP = """out({ h2: [...view.querySelectorAll("h2")].map(e => e.textContent), h3: [...view.querySelectorAll("h3")].map(e => [e.id, e.textContent]),
  p: [...view.querySelectorAll("p")].map(e => e.textContent), text: view.textContent,
  list: [...list.querySelectorAll("a")].map(a => [a.getAttribute("href"), a.textContent, a.getAttribute("aria-current")]),
  status: status.textContent, statusHidden: status.hidden,
  scrolled: [...view.querySelectorAll("[id]")].filter(e => e.scrolled.length).map(e => e.id),
  focus: document.activeElement && document.activeElement.id });"""


def test_methods_page_renders_layers():
    """#methods/stratified-sampling: the topic's title, context, review line and its three layers, each heading with
    its anchor as id; the list names every topic with its context and marks the open one; nothing is scrolled."""
    t = m.topic(TOPIC)
    out = _page(DUMP, TOPIC)
    assert out["h2"] == [t["title"]]
    assert out["p"][:2] == [t["context"], m.review_line(t["review"])]
    assert out["h3"] == [[f"methods/{TOPIC}/{x['layer']}", x["heading"]] for x in t["layers"]]
    assert [h for _id, h in out["h3"]] == ["In short", "Why", "For auditors"]
    for layer in t["layers"]:   # every line of each layer reaches the page (code spans lose only their backticks)
        for line in filter(None, (x.strip() for x in layer["text"].split("\n"))):
            assert line.removeprefix("- ").replace("`", "") in out["text"]
    hrefs = [a[0] for a in out["list"]]
    assert hrefs == [f"#methods/{x['id']}" for x in m.topics()] + ["#methods/glossary"]
    assert [a for a in out["list"] if a[2] == "page"] == [[f"#methods/{TOPIC}", t["title"], "page"]]
    assert out["statusHidden"] is True and out["scrolled"] == []


SYN_INDEX = {"topics": [{"id": "alpha", "title": "Alpha", "context": "The alpha context.", "settings": [], "artifacts": [],
                         "pages": [], "review": {"status": "drafted", "date": "2026-10-29"}}],
             "glossary": [{"term": "recall", "slug": "recall", "synonyms": ["sensitivity"], "definition": "The share."}],
             "glossary_review": {"status": "drafted", "date": "2026-10-29"}}
SYN_TOPIC = dict(SYN_INDEX["topics"][0], layers=[
    {"layer": "short", "heading": "In short", "text": "One <b>bold</b> word and [a link](https://example.org).\n"
                                                      "<script>x()</script> continues the paragraph.\n\nSecond paragraph."},
    {"layer": "why", "heading": "Why", "text": "- item with `<i>code</i>`\n  that wraps\n- second <a href=\"#x\">item</a>"},
    {"layer": "auditors", "heading": "For auditors", "text": "Plain."}])


def test_methods_page_literal_markup():
    """Text holding tags or a Markdown link is shown as written: no element is made from it; only paragraphs, list
    items and code spans are formatted."""
    out = _page("""out({ tags: [...view.querySelectorAll("*")].map(e => e.tagName), text: view.textContent,
      ps: [...view.querySelectorAll("p")].map(e => e.textContent), lis: [...view.querySelectorAll("li")].map(e => e.textContent),
      code: [...view.querySelectorAll("code")].map(e => e.textContent) });""",
                "alpha", {"alpha": SYN_TOPIC}, SYN_INDEX)
    assert set(out["tags"]) == {"H2", "H3", "P", "UL", "LI", "CODE"}
    assert ("One <b>bold</b> word and [a link](https://example.org). <script>x()</script> continues the paragraph."
            in out["ps"])
    assert "Second paragraph." in out["ps"]
    assert out["lis"] == ["item with <i>code</i> that wraps", 'second <a href="#x">item</a>']
    assert out["code"] == ["<i>code</i>"]


def test_methods_page_unknown_topic():
    """#methods/no-such-topic: the CLI's sentence above the topic list, the list as on #methods, no topic open."""
    out = _page(DUMP, "no-such-topic")
    assert out["status"] == "there is no such methods topic: no-such-topic" and out["statusHidden"] is False
    assert out["h2"] == [] and len(out["list"]) == len(m.topics()) + 1
    assert all(a[2] is None for a in out["list"])
    plain = _page(DUMP, "")
    assert plain["status"] == "" and plain["statusHidden"] is True and plain["h2"] == []


def test_methods_list_shows_review_status():
    """The topic list shows each title's review status under its context, the glossary's too."""
    idx = dict(SYN_INDEX, topics=[dict(SYN_INDEX["topics"][0], review={"status": "reviewed", "date": "2026-11-02"})])
    out = _page("""out([...list.querySelectorAll("li")].map(li => [...li.querySelectorAll("p")].map(p => p.textContent)));""",
                index=idx)
    assert out == [["The alpha context.", "Reviewed by the author on 2026-11-02."],
                   ["The words WIDER uses, with the other names they go by.", "Drafted; not yet reviewed by the author."]]


def test_edited_review_line():
    """guide-assistant D22: a reviewed text edited since shows the third line on the page, in the CLI's words; a file
    that cannot be read is named above the list, and opening it shows that sentence instead of "no such topic"."""
    edited = {"status": "drafted", "date": "2026-10-30", "edited": True}
    line = "WIDER's documents: beta.md cannot be read (x); it is left out until it is fixed."
    idx = dict(SYN_INDEX, topics=[dict(SYN_INDEX["topics"][0], review=edited)], problems=[{"file": "beta.md", "text": line}])
    out = _page("""out({ list: [...list.querySelectorAll("li")].map(li => [...li.querySelectorAll("p")].map(p => p.textContent)),
                         problems: [...list.querySelectorAll(".methods-problem")].map(p => p.textContent),
                         first: list.firstElementChild.className, status: status.textContent });""", "beta", index=idx)
    assert out["list"][0] == ["The alpha context.", m.review_line(edited)]
    assert m.review_line(edited) == "Edited since the author's review on 2026-10-30; not yet reviewed again."
    assert out["problems"] == [line] and out["first"] == "methods-problem" and out["status"] == line
    out = _page("""out(view.querySelector(".methods-review").textContent);""", "alpha",
                {"alpha": dict(SYN_TOPIC, review=edited)}, idx)
    assert out == m.review_line(edited)
    js = run_dom("""out(Methods.reviewLine({ status: "drafted", date: "2026-10-30", edited: true }));""",
                 scripts=("common.js", "methods.js"))
    assert js == m.review_line(edited)


def test_methods_review_line():
    """Under each title, in the words of `wider guide methods`: reviewed with its date, or drafted; the glossary too."""
    reviewed = dict(SYN_TOPIC, review={"status": "reviewed", "date": "2026-11-02"})
    out = _page(DUMP, "alpha", {"alpha": reviewed}, SYN_INDEX)
    assert out["p"][1] == "Reviewed by the author on 2026-11-02."
    out = _page(DUMP, "alpha", {"alpha": SYN_TOPIC}, SYN_INDEX)
    assert out["p"][1] == "Drafted; not yet reviewed by the author."
    out = _page(DUMP, "glossary", index=dict(SYN_INDEX, glossary_review={"status": "reviewed", "date": "2026-11-03"}))
    assert out["h2"] == ["Glossary"] and out["p"][0] == "Reviewed by the author on 2026-11-03."
    js = run_dom("""out([Methods.reviewLine({ status: "reviewed", date: "2026-11-02" }), Methods.reviewLine({ status: "reviewed" }),
                         Methods.reviewLine(null)]);""", scripts=("common.js", "methods.js"))
    assert js == [m.review_line({"status": "reviewed", "date": "2026-11-02"}), m.review_line({"status": "reviewed"}),
                  m.review_line({})]


def test_methods_layer_anchor_scrolls():
    """#methods/recall-estimate/auditors opens recall-estimate scrolled to its "For auditors" heading, which takes the
    focus; an unknown layer opens the topic at its top."""
    out = _page(DUMP, "recall-estimate/auditors")
    assert out["h2"] == [m.topic("recall-estimate")["title"]]
    assert out["scrolled"] == ["methods/recall-estimate/auditors"] and out["focus"] == "methods/recall-estimate/auditors"
    assert _page(DUMP, "recall-estimate/nope")["scrolled"] == []


def test_methods_glossary_anchor_scrolls():
    """#methods/glossary/recall shows the glossary as a definition list, scrolled to "recall" with its other names."""
    out = _page(DUMP, "glossary/recall")
    assert out["h2"] == ["Glossary"]
    assert out["scrolled"] == ["methods/glossary/recall"] and out["focus"] == "methods/glossary/recall"
    g = run_dom(f"""
      Object.assign(replies, {json.dumps({"/api/methods": _index()})});
      await Methods.load("glossary"); await flush();
      const dts = [...$("#methods-view").querySelectorAll("dl dt")];
      out({{ terms: dts.map(e => [e.id, e.textContent]), dds: dts.map(e => e.nextElementSibling.textContent) }});
    """, html=section("page-methods"), scripts=("common.js", "methods.js"))
    gl = m.glossary()
    assert g["terms"] == [[f"methods/glossary/{e['slug']}", e["term"]] for e in gl]
    for e, dd in zip(gl, g["dds"]):   # each term's other names, then its definition
        also = f"Also called: {'; '.join(e['synonyms'])}." if e["synonyms"] else ""
        assert dd.startswith(also) and " ".join(e["definition"].replace("`", "").split()) in " ".join(dd.split())
    recall = next(e for e in gl if e["slug"] == "recall")
    assert "sensitivity" in recall["synonyms"]


GUIDE = {"page": "sample", "title": "Samples and labelling", "can_do": "Label the sample.", "notices": [],
         "parts": [{"heading": "What to do", "items": ["Draw a sample."]}], "methods": [TOPIC]}


def test_methods_has_no_guide_panel():
    """The Methods page has no guide panel of its own: shell.js never asks /api/guide/methods, the panel stays hidden
    and the top bar shows no Guide button (one would do nothing here)."""
    rows = stages({"setup": "done"})
    body = r"""
global.innerWidth = 1440; markCurrentPage("methods"); await flush();
console.log(JSON.stringify({ hidden: $("#guide-panel").hidden, guideBtn: String($("#tb-guide").innerHTML), asked: urls().filter(u => u.startsWith("/api/guide")),
  fixed: window.FIXED_PAGES.find(p => p.key === "methods") }));"""
    out = shell_page(body, rows, "setup", {})
    assert out["hidden"] is True and out["asked"] == [] and out["guideBtn"] == ""
    assert out["fixed"] == {"key": "methods", "title": "Methods", "commands": ["guide methods"]}


def test_guide_panel_methods_links():
    """The Samples and labelling guide ends with "Why WIDER works this way:" and a link titled from /api/methods that
    opens #methods/stratified-sampling; /api/methods is asked for once."""
    from tests.unit.a11y_dom import static

    rows = stages({"setup": "done", "construct": "done", "query": "done", "import": "done", "dedup": "done",
                   "rank": "done", "sample": "ready"})
    body = static("methods.js") + r"""
global.innerWidth = 1440; markCurrentPage("sample"); await flush(); await flush();
markCurrentPage("rank"); await flush(); markCurrentPage("sample"); await flush(); await flush();
const html = String($("#guide-body").innerHTML);   // the guide's text; Ask WIDER sits below it (guide-assistant D18)
console.log(JSON.stringify({ line: (html.match(/<p class="guide-methods">[\s\S]*?<\/p>/) || [""])[0],
  asked: urls().filter(u => u === "/api/methods").length }));"""
    rank = dict(GUIDE, page="rank", title="Ranking", methods=[])
    out = shell_page(body, rows, "sample", {"/api/guide/sample": GUIDE, "/api/guide/rank": rank, "/api/methods": _index()})
    title = m.topic(TOPIC)["title"]
    assert out["line"] == (f'<p class="guide-methods">Why WIDER works this way: '
                           f'<a href="#methods/{TOPIC}">{title}</a></p>')
    assert out["asked"] == 1


def test_methods_topic_on_phone_scrolls_to_title():
    """On a phone the topic list sits above the topic, so #methods/<topic> brings the topic's title into view and
    gives it the focus; on a wide screen the topic opens without scrolling (test above)."""
    out = run_dom(f"""
      Object.assign(replies, {json.dumps({"/api/methods": _index(), f"/api/methods/{TOPIC}": m.topic(TOPIC)})});
      window.matchMedia = (q) => ({{ matches: q === "(max-width: 640px)" }});
      await Methods.load({json.dumps(TOPIC)}); await flush();
      const view = $("#methods-view");
      out({{ scrolled: [...view.querySelectorAll("[id]")].filter(e => e.scrolled.length).map(e => e.id),
            focus: document.activeElement && document.activeElement.id }});
    """, html=section("page-methods"), scripts=("common.js", "methods.js"))
    assert out == {"scrolled": [f"methods/{TOPIC}"], "focus": f"methods/{TOPIC}"}
