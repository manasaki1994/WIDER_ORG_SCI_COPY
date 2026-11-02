"""The card "Names in shared files" (researchers.js), the mode line beside the bundle and package buttons, and the
"(inferred, not confirmed)" marker in history.js and drawer.js, under Node on the stand-in page (researcher-attribution
D8). Synthetic names only."""
import json
import re

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from wider.core.researchers import MODE_LINES, NOT_VERIFIED, SOURCE_WORDS

ENTRIES = [
    {"name": "Ana Silva", "pseudonym": "Researcher 1", "status": "declared", "source": SOURCE_WORDS["page"],
     "source_key": "page", "first_seen": "2026-10-20T09:00:00", "joined_into": None},
    {"name": "ben", "pseudonym": "Researcher 2", "status": "declared", "source": SOURCE_WORDS["option"],
     "source_key": "option", "first_seen": "2026-10-20T09:01:00", "joined_into": None},
    {"name": "login-x", "pseudonym": "Researcher 3", "status": "inferred", "source": SOURCE_WORDS["USER"],
     "source_key": "USER", "first_seen": "2026-10-20T09:02:00", "joined_into": None},
]


def _state(mode="pseudonyms", entries=ENTRIES):
    return {"researchers": entries, "mode": mode, "mode_line": MODE_LINES[mode], "not_verified": NOT_VERIFIED}


def _card(body: str, state=None) -> dict:
    script = ESC + PAGE + f"""
replies["/api/researchers"] = {json.dumps(state or _state())};
replies["/api/researchers/attribution"] = (url, o) => ({{ message: "set", ...{json.dumps(_state("names"))} }});
replies["/api/researchers/confirm"] = {{ message: "confirmed", ...{json.dumps(_state())} }};
replies["/api/researchers/join"] = {{ message: "joined", ...{json.dumps(_state())} }};
let after = 0; global.afterAction = () => {{ after++; }};
{(STATIC / "researchers.js").read_text(encoding="utf-8")}
await Researchers.load(); await flush();
{body}
"""
    return run_node(script)


DUMP = """console.log(JSON.stringify({ modes: $("#rs-modes").innerHTML, table: $("#rs-table").innerHTML,
  explain: $("#rs-explain").textContent, verify: $("#rs-verify").textContent, msg: $("#rs-msg").innerHTML,
  rp: $("#rp-mode").textContent, pkg: $("#out-package-mode").textContent, after,
  posts: calls.filter(c => c[1] === "POST").map(c => [c[0], c[2]]) }));"""


def test_card_lists_researchers():
    """Every entry with its pseudonym, status and source in D2's words; Confirm only for the inferred entry."""
    out = _card(DUMP)
    t = out["table"]
    for e in ENTRIES:
        assert f"<td>{e['name']}</td><td>{e['pseudonym']}</td><td>{e['status']}</td>" in t
    assert "typed on the page" in t and "given with --by, --author or --reviewer" in t
    assert "login name of this computer" in t
    assert re.findall(r'data-confirm="([^"]*)"', t) == ["login-x"]
    assert out["posts"] == []


def test_card_says_identity_is_not_verified():
    out = _card(DUMP)
    assert out["verify"].startswith(NOT_VERIFIED + " Your name is the one shown beside \"Working as\" in the top bar.")
    assert "Confirm makes a name WIDER inferred a declared one" in out["verify"] and "Same researcher as" in out["verify"]


def test_card_explanation_once_in_black():
    """Three radios with their text beside them, the chosen one checked; the explanation once, as plain text (the
    element is styled in normal black text in index.html), naming each point the spec lists."""
    out = _card(DUMP)
    radios = re.findall(r'<label class="choice"><input type="radio" name="rs-mode" value="(\w+)"[^>]*> ([^<]+)</label>', out["modes"])
    assert radios == [("pseudonyms", "Pseudonyms"), ("names", "Real names"), ("none", "No names")]
    assert 'value="pseudonyms" data-mode="pseudonyms" checked' in out["modes"]
    e = out["explain"]
    for part in ("Researcher 1, Researcher 2", "Real names: names a researcher typed or confirmed are written as recorded",
                 "No names: no name and no pseudonym", "stays on this computer", "are never written as names",
                 "Sharing files written in different modes lets readers match pseudonyms to names."):
        assert e.count(part) == 1, part
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    tag = re.search(r'<p[^>]*id="rs-explain"[^>]*>', html).group(0)
    assert "muted" not in tag and "small" not in tag   # normal size, black (important text is never grey)
    assert html.count('id="rs-explain"') == 1


def test_mode_choice_posts_and_redraws():
    out = _card("""const r = $("#rs-modes").querySelectorAll("[data-mode]")[1]; await r.onchange(); await flush();
""" + DUMP)
    assert out["posts"] == [["/api/researchers/attribution", {"mode": "names"}]]
    assert out["rp"] == out["pkg"] == MODE_LINES["names"] and out["msg"] == "set" and out["after"] == 1


def test_confirm_sends_the_name_in_the_body():
    out = _card("""await $("#rs-table").querySelectorAll("[data-confirm]")[0].onclick(); await flush();
""" + DUMP)
    assert out["posts"] == [["/api/researchers/confirm", {"name": "login-x"}]]
    assert all("login-x" not in u for u, _ in out["posts"])


def test_join_select_sends_into():
    """"Same researcher as" lists the other open entries; choosing one posts {name, into}; a refusal shows the
    server's reason in red and redraws the list."""
    out = _card("""const s = $("#rs-table").querySelectorAll("[data-join]")[2];
s.value = "Ana Silva"; await s.onchange(); await flush();
""" + DUMP)
    assert out["posts"] == [["/api/researchers/join", {"name": "login-x", "into": "Ana Silva"}]]
    sel = re.search(r'<select data-join="login-x"[^>]*>(.*?)</select>', out["table"]).group(1)
    assert re.findall(r'<option value="([^"]+)">', sel) == ["Ana Silva", "ben"]
    joined = [dict(ENTRIES[0]), dict(ENTRIES[2], pseudonym="Researcher 1", status="declared", joined_into="Ana Silva")]
    out = _card(DUMP, _state(entries=joined))
    assert "joined into Ana Silva" in out["table"] and 'data-join="login-x"' not in out["table"]
    out = _card("""replies["/api/researchers/join"] = () => { throw new Error("cannot join a name into itself"); };
const s = $("#rs-table").querySelectorAll("[data-join]")[0]; s.value = "ben"; await s.onchange(); await flush();
""" + DUMP)
    assert out["msg"] == '<span class="bad">cannot join a name into itself</span>' and out["after"] == 0


def test_mode_line_beside_buttons():
    """The line beside "Write share bundle" and beside the package button reads the mode on load."""
    out = _card(DUMP)
    assert out["rp"] == out["pkg"] == "Names in shared files: pseudonyms (Researcher 1, Researcher 2 ...)"
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'id="rp-bundle"[^\n]*id="rp-mode"', html) and re.search(r'id="out-package"[^\n]*id="out-package-mode"', html)
    assert '<script src="/static/researchers.js"></script>' in html


def test_history_marks_inferred():
    """History and the drawer's history: "login-x (inferred, not confirmed)", "Ana Silva" without a marker."""
    rows = [{"change_id": 1, "at": "2026-10-20T09:00:00", "author": "Ana Silva", "author_inferred": False,
             "kind_label": "k", "summary": "s1"},
            {"change_id": 2, "at": "2026-10-20T09:01:00", "author": "login-x", "author_inferred": True,
             "kind_label": "k", "summary": "s2"}]
    script = ESC + PAGE + f"""
global.localStorage = {{ getItem: () => "" }}; global.fmtWhen = (t) => t;
replies["/api/history"] = {json.dumps(rows)};
{(STATIC / "history.js").read_text(encoding="utf-8")}
await window.loadHistory();
console.log(JSON.stringify({{ table: $("#hist-table").innerHTML }}));
"""
    t = run_node(script)["table"]
    assert "<td>login-x (inferred, not confirmed)</td>" in t and "<td>Ana Silva</td>" in t
    js = (STATIC / "drawer.js").read_text(encoding="utf-8")
    assert re.search(r'c\.author \|\| ""\)\}\$\{c\.author_inferred \? " \(inferred, not confirmed\)" : ""\}', js)


def test_message_cleared_when_the_list_changed_elsewhere():
    """The message of a Confirm stays while the list is as the Confirm left it (the page's own redraw), and goes once
    the list changed elsewhere (an undo on the History page) and the card loads again."""
    out = _card("""await $("#rs-table").querySelectorAll("[data-confirm]")[0].onclick(); await flush();
await Researchers.load(); await flush();
const kept = $("#rs-msg").innerHTML;
replies["/api/researchers"] = { ...replies["/api/researchers"], mode: "none" };
await Researchers.load(); await flush();
console.log(JSON.stringify({ kept, after: $("#rs-msg").innerHTML }));""")
    assert out == {"kept": "confirmed", "after": ""}
