"""Fixes from the eleventh round of C1 checks: the query page of a project without synonyms, the home banner after
CLI work, NOT blocks in the Google Scholar string, a top-N that is not a whole number, the explorer's first page,
undo-last without a name, Redo after undo-last, and the page never signing with $WIDER_REVIEWER."""
import json

from fastapi.testclient import TestClient

from tests.unit.test_history import _label
from tests.unit.test_query_roles import APP_JS, STATIC, js_function, run_js
from tests.unit.test_rank import _ranked
from tests.unit.test_records import _deduped, _sample
from wider.construct import artifacts
from wider.core import journal
from wider.core.project import Project
from wider.dedup.engine import queue_rows
from wider.rank.score import explorer, rank
from wider.sample.labels import label
from wider.search.ir import Block, Query
from wider.search.render import platforms
from wider.search.render.reduced import render_gscholar
from wider.ui.app import create_app

HISTORY_JS = (STATIC / "history.js").read_text(encoding="utf-8")


def test_query_page_of_a_project_without_synonyms(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "r", platforms=["scopus"])))
    r = c.get("/api/query/render")
    assert r.status_code == 200 and r.json() == {"row": None, "message": "no synonyms artifact yet"}
    assert c.get("/api/query/render?version=3").status_code == 404   # a version asked for by number still must exist
    data = {u: c.get(u).json() for u in ("/api/query/platforms", "/api/platforms", "/api/query/render")}
    body = js_function(APP_JS, "async function loadQuery(") + """
const api = async (u) => { if (!(u in DATA)) throw new Error("unexpected call " + u); return DATA[u]; };
const el = () => ({ innerHTML: "x", textContent: "", querySelector: () => el() });
const els = {};
const $ = (sel) => (els[sel] ||= el());
await loadQuery();
console.log(JSON.stringify({ status: $("#q-status").textContent, cards: $("#q-strings").innerHTML }));"""
    out = run_js(body, data)   # no compact call: the empty project asks for nothing more
    assert out == {"status": "no synonyms artifact yet", "cards": ""}


def test_home_refreshes_the_stage_marks_before_its_cards():
    js = (STATIC / "shell.js").read_text(encoding="utf-8")
    body = js[js.index("window.loadHome = async function"):js.index('q("#home-projects").innerHTML = d.projects')]
    assert body.index("refreshStages()") < body.index('call("/api/home")')


def test_gscholar_leaves_out_a_not_block():
    q = Query(blocks=[Block(key="a", terms=["voice"]), Block(key="b", terms=["employee"]),
                      Block(key="c", terms=["plankton"])],
              logic={"and": ["a", "b", {"not": "c"}]})
    s = render_gscholar(q)
    assert "plankton" not in s and "voice" in s and "employee" in s
    q.logic = {"or": [{"and": ["a", "b"]}, {"and": ["c", {"not": "a"}]}]}
    assert render_gscholar(q) == "(voice) (employee)"   # an OR of AND paths keeps the first path
    assert "Excluded (NOT) blocks are left out" in " ".join(platforms()["gscholar"]["paste"].split())


def test_rank_top_n_must_be_a_whole_number(tmp_path):
    p = _ranked(tmp_path, 6, 2, 50)   # the route reloads project.yaml, which allows no pool under 50
    c = TestClient(create_app(p))
    n = p.store.scalar("SELECT COUNT(*) FROM rankings")
    for bad in ("abc", "2.5", ""):
        r = c.post(f"/api/rank/run?top_n={bad}")
        assert r.status_code == 400 and "whole number" in r.json()["detail"], (bad, r.text)
    assert p.store.scalar("SELECT COUNT(*) FROM rankings") == n   # nothing ran
    assert c.post("/api/rank/run?top_n=1").status_code == 200


def test_explorer_first_page_starts_at_rank_one_with_every_cell(tmp_path):
    p = _ranked(tmp_path, 8, 2, 5)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    rows = explorer(p)["rows"]
    assert rows[0]["rank"] == 1 and [r["rank"] for r in rows] == sorted(r["rank"] for r in rows)
    assert all(set(r["cell_scores"]) == {"voice_cell", "ocean_cell"} for r in rows)


def test_cell_filter_counts_a_cell_best_for_no_record_as_zero(tmp_path):
    p = _ranked(tmp_path, 12, 2, 5)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence " * 20,
                                                           "ocean_cell": "plankton blooms ocean temperature " * 20}}, origin="manual")
    rank(p, top_n=1)
    p.store.execute("UPDATE ranking_rows SET best_cell='voice_cell'")   # every record closest to the first cell
    d = explorer(p)
    assert d["best_cell_top3000"] == {"voice_cell": 5, "ocean_cell": 0}


def _undo_harness(data: dict) -> dict:
    """history.js ``undo``, ``needName`` and ``me`` with a recording ``call``, a stub ``WiderName`` holding the stored
    name (D33: no field of its own on the page); DATA gives that name, the reply and (offPage) whether another page
    than History is open."""
    lines = HISTORY_JS.splitlines()
    me_line = "\n".join(x for x in lines if x.strip().startswith(("const me = ", "const setName = ", "const blindOn = ", "const blindQ = ")))
    body = (js_function(HISTORY_JS, "async function undo(") + js_function(HISTORY_JS, "function needName(")
            + js_function(HISTORY_JS, "async function withOther(") + js_function(HISTORY_JS, "function offer(")) + """
let lastOffer = null;   // offer() keeps one Undone/Redone toast (C6 10.2 F4); the stub toast returns nothing to remove
const calls = [], toasts = [], els = {};
let plans = {}; const window = {};   // no entry asks the seen choice (2a) and no Refine: the plain undo and redo paths
const document = { activeElement: null };   // undo() notes the focused control (C6 D2); nothing has it here
const currentPage = DATA.offPage ? "records" : "history";
let asked = 0;
const WiderName = { get: () => DATA.stored, ask() { asked++; },
  needHtml: (lead) => lead + "enter your name first: use Change beside “Working as” in the top bar. <button data-set-name>Set your name</button>" };
els["#hist-msg"] = { textContent: "", innerHTML: "" };
els["#lb-blind"] = { checked: false };   // Blind off: no hide_samples on the undo and redo posts (owner answer 37e6)
const q = (s) => els[s] || null;
""" + me_line + """
const call = async (u, o) => { calls.push([u, o && o.method]); return DATA.reply; };
const toast = (t, tone, action) => { toasts.push({ t, tone, label: action && action.label, action }); };
const refreshAll = () => {};
const out = await undo(DATA.id);
const first = toasts[0] || {};
if (first.action && DATA.follow) await first.action.fn();
console.log(JSON.stringify({ out, calls, msg: els["#hist-msg"].innerHTML, focus: document.activeElement, asked,
  toasts: toasts.map(x => ({ t: x.t, tone: x.tone, label: x.label })) }));"""
    return run_js(body, data)


def test_undo_last_without_a_name_sends_nothing():
    out = _undo_harness({"stored": "", "id": None, "reply": None})
    assert out["calls"] == [] and out["out"] is None
    # on the History page: the shared line pointing at Change, with a button that opens the name dialog
    assert out["msg"].startswith("enter your name first: use Change beside") and "data-set-name" in out["msg"]
    t = out["toasts"][0]
    assert "Nothing was undone." in t["t"] and t["tone"] == "danger" and t["label"] == "Set your name"


def test_undo_last_sends_the_stored_name():
    reply = {"change_id": 7, "summary": "label: include"}
    out = _undo_harness({"stored": "ana", "id": None, "reply": reply})
    assert out["calls"][0] == ["/api/history/undo-last?author=ana", "POST"]
    assert out["msg"] == "" and out["asked"] == 0


def test_redo_after_undo_last(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1])
    label(p, "dev", ids[0], "ana", "include")
    u = journal.undo_last(p, "ana")
    assert _label(p, ids[0]) is None
    again = journal.undo(p, u["change_id"], other=True)   # the page's Redo undoes the undo by its id
    assert again["summary"].startswith("redo: ") and _label(p, ids[0])["decision"] == "include"
    reply = {"change_id": u["change_id"], "summary": u["summary"]}
    out = _undo_harness({"stored": "ana", "id": None, "reply": reply, "follow": True})
    assert out["calls"][0] == ["/api/history/undo-last?author=ana", "POST"]
    assert out["toasts"][0]["label"] == "Redo" and out["toasts"][0]["t"].startswith("Undone: ")
    assert out["calls"][1] == [f"/api/history/{u['change_id']}/undo", "POST"]


def test_page_changes_ignore_wider_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDER_REVIEWER", "env-reviewer")
    monkeypatch.setenv("USER", "os-login")
    p, _ = _deduped(tmp_path)
    q = queue_rows(p)[0]
    c = TestClient(create_app(p))
    assert c.post("/api/dedup/decide", json={"raw_a": q["raw_a"], "raw_b": q["raw_b"], "decision": "uncertain"}).status_code == 200
    assert [x["author"] for x in p.store.query("SELECT author FROM history")] == [""]
    dump = json.dumps([dict(x) for x in p.store.query("SELECT * FROM history")])
    assert "env-reviewer" not in dump and "os-login" not in dump
    assert journal.current_author() == "env-reviewer"   # the CLI, one person at a terminal, still uses it
