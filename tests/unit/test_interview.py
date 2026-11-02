"""Vision change 2b, D12: the refinement interview. Questions follow the evidence (a dry run, mining, a diagnosis, an
import), within a budget, on the same Questions page; answers apply on existing paths and undo; the decision ledger
and its prompts; the stop rule. Synthetic data only."""
import json

from fastapi.testclient import TestClient

from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node
from tests.unit.test_diagnostics import SAMPLE_ROWS, _cli, _export, _mark, _oa
from tests.unit.test_seed_mining import _mined
from wider.construct import artifacts
from wider.construct.benchmark import sync
from wider.core import journal
from wider.core import questions as qs
from wider.core.project import Project
from wider.interview import bank, ledger
from wider.ui.app import create_app

# block a: the hit's name and a noise term; B002 ("Beta drift over time") is a miss that the mined "beta drift" retrieves
SYN = {"blocks": [{"key": "a", "name": "Coupling", "terms": ['"alpha coupling"', "gamma*"]}], "logic": {"and": ["a"]}}
NOISE = [("Alpha coupling in pairs", "Alpha coupling studied.", "10.1000/a1")] + [
    (f"Gamma rays {i}", "Gamma emission of invented stars.", None) for i in range(6)]


def _interview(tmp_path, name="r", syn=SYN, rows=NOISE):
    """Seeds mined, synonyms v1 saved, a Scopus export with six noise records imported against it."""
    from wider.ingest.importer import add_file, import_all
    from wider.seeds import mine

    p = _mined(tmp_path, name)
    sync(p)
    for i in ("B001", "B002"):
        p.store.execute("UPDATE benchmark_papers SET abstract=? WHERE benchmark_id=?", ("A synthetic abstract.", i))
    mine.mine(p, model=False)
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    add_file(p, "scopus", _export(tmp_path / f"{name}.csv", rows))
    import_all(p)
    return Project.load(p.root)


def _open(p, kind=None):
    rows = [q for q in qs.list_questions(p, "open") if q["kind"].startswith("refine_")]
    return [q for q in rows if q["kind"] == kind] if kind else rows


def _one(p, kind):
    got = _open(p, kind)
    assert len(got) == 1, [q["dedupe_key"] for q in _open(p)]
    return got[0]


def _default(q):
    return next(o for o in q["options"] if o.get("default"))


def test_questions_follow_the_evidence(tmp_path):
    from wider.search import evaluate

    p = Project.init(tmp_path / "bare", platforms=["scopus"])
    assert bank.trigger(p) == {"raised": [], "waiting": [], "paused": False, "notice": None}   # no evidence, nothing
    p = _interview(tmp_path)
    res = evaluate.dry_run(p)
    assert res["platforms"]["scopus"]["seeds"][1]["state"] == "miss"
    assert res["interview"]["raised"] == []   # the import already raised them; the dry run asks nothing twice
    kinds = [q["kind"] for q in _open(p)]
    for kind in ("refine_seed_not_retrieved", "refine_name_add", "refine_noise_term"):
        assert kinds.count(kind) == 1, kinds
        q = _one(p, kind)
        assert q["stage"] == "query" and q["context"]["evidence"]["why"] and 3 <= len(q["options"]) <= 5
        cons = _default(q)["consequence"]
        assert "seed" in cons or "B002" in cons, cons
        assert "records" in cons, cons
    assert not {"refine_variant", "refine_limit", "refine_screening_budget"} & set(kinds)
    assert _one(p, "refine_seed_not_retrieved")["dedupe_key"] == "refine_seed_not_retrieved:B002:scopus"
    noise = _one(p, "refine_noise_term")
    assert noise["context"]["evidence"]["term"] == "gamma*" and "-6 records" in _default(noise)["consequence"]
    assert _default(noise)["effect"]["records"] < 0 and _default(noise)["writes"]


def _fresh(p, syn=None):
    """The open questions out of the way; ``syn`` saved as the newest version; the interview asked again."""
    for q in _open(p):
        qs.dismiss(p, q["question_id"], by="ana")
    if syn:
        artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    bank.trigger(p, "dry_run")
    return Project.load(p.root)


def test_more_finders_follow_the_evidence(tmp_path):
    """Variant, context-AND, boundary record, date limit and screening budget, each from real (synthetic) evidence
    (spec-checker F5): a dry run, a diagnosis of an import, the budget and the seeds' years."""
    # a stem one letter too long: drift* also matches B002
    p = _fresh(_interview(tmp_path / "v"), {"blocks": [{"key": "a", "name": "C", "terms": ['"alpha coupling"', "drifts*"]}],
                                            "logic": {"and": ["a"]}})
    v = _one(p, "refine_variant")
    assert v["dedupe_key"] == "refine_variant:a:drifts*:drifts*:drift*" and _default(v)["value"] == "change"
    assert _default(v)["consequence"].startswith("+1 seed on scopus") and _default(v)["writes"]
    qs.answer(p, v["question_id"], "change", by="ana")
    assert artifacts.load(p, "synonyms")[1]["blocks"][0]["terms"][1] == "drift*"
    # B002 fails the AND-joined context block b
    p = _fresh(_interview(tmp_path / "c"), {"blocks": [{"key": "a", "name": "C", "terms": ['"alpha coupling"', '"beta drift"']},
                                                       {"key": "b", "name": "P", "terms": ["pairs*"]}],
                                            "logic": {"and": ["a", "b"]}})
    ctx = _open(p, "refine_context_and")
    assert ctx and all(q["dedupe_key"].startswith("refine_context_and:b:") for q in ctx)
    assert all(q["context"]["block"] == "b" and "+1 seed on scopus" in _default(q)["consequence"] for q in ctx)
    # five records sharing a term no seed holds, not reported as noise: a boundary cluster
    syn = {"blocks": [{"key": "a", "name": "C", "terms": ['"alpha coupling"', "gamma*", "delta*"]}], "logic": {"and": ["a"]}}
    rows = NOISE + [(f"Alpha coupling and delta waves {i}", "Alpha coupling of delta waves.", None) for i in range(5)]
    p = _interview(tmp_path / "b", syn=syn, rows=rows)
    b = _one(p, "refine_boundary_record")
    assert b["dedupe_key"] == 'refine_boundary_record:scopus:v1:a: "alpha coupling"; a: delta*'
    assert _default(b)["value"] == "leave" and "5 records" in b["title"]
    assert not [q for q in _open(p, "refine_boundary_record") if "gamma" in q["dedupe_key"]]   # gamma* is noise already
    # over the budget, with records older than the earliest development seed
    p = _interview(tmp_path / "l")
    bm = p.read_benchmark()
    for x in bm:
        x.year = 2015
    p.write_benchmark(bm)
    sync(p)
    p.config.protocol.identification.record_budget = 3
    p.save()
    p.store.execute("UPDATE raw_records SET year=1990 WHERE title LIKE 'Gamma rays%'")
    p = _fresh(Project.load(p.root))
    lim, bud = _one(p, "refine_limit"), _one(p, "refine_screening_budget")
    assert lim["dedupe_key"] == "refine_limit:scopus" and _default(lim)["value"] == "keep"
    assert [o["consequence"] for o in lim["options"]][0].startswith("-6 records on scopus (published before 2015)")
    assert "7 records, 4 over the budget of 3" in bud["prompt"] and bud["context"]["raise_to"] == 500
    qs.answer(p, lim["question_id"], "from_seed", by="ana")
    assert (Project.load(p.root).config.platforms[0].limits or {}).get("dates")


def _items(n, block="a", records=0.0, prefix="n"):
    """``n`` invented name-add questions for one block, with options that write nothing."""
    def finder(ev):
        return [bank._item("refine_name_add", f"{prefix}{i}", f"Add name {i}?", "Invented.", {"subject": f"name {i}",
                           "why": "invented"}, [bank._opt("keep", "Keep", "nothing", 0.0, records, default=True),
                                                bank._opt("reject", "Reject", "nothing"),
                                                bank._opt("leave", "Leave", "nothing")], block)
                for i in range(n)]
    return finder


def test_budget_and_dedupe(tmp_path, monkeypatch):
    assert (bank.OPEN_BUDGET, bank.BLOCK_BUDGET) == (5, 10)
    p = _interview(tmp_path)
    for q in _open(p):   # the real questions out of the way: the budget below is the invented ones'
        qs.dismiss(p, q["question_id"], by="ana")
    assert _open(p) == [] and bank.waiting(p) == 0
    monkeypatch.setattr(bank, "FINDERS", (_items(7, records=0.05),))   # each answer moves records: no stop rule
    r = bank.trigger(p)
    assert len(r["raised"]) == 5 and len(r["waiting"]) == 2
    assert qs.counts(p)["waiting"] == 2
    out = _cli(p, "questions", "list").output
    assert "2 waiting for the budget" in out and "keep (default): nothing" in out
    try:
        qs.answer(p, r["waiting"][0], "keep", by="ana")
        raise AssertionError("a waiting question was answered")
    except ValueError as e:
        assert "waiting for the budget" in str(e)
    qs.answer(p, r["raised"][0], "keep", by="ana")
    assert len(_open(p)) == 5 and bank.waiting(p) == 1 and qs.get(p, r["waiting"][0])["status"] == "open"
    assert bank.trigger(p)["raised"] == [] and bank.waiting(p) == 1   # the same keys: nothing new
    qs.dismiss(p, r["raised"][1], by="ana")
    assert bank.waiting(p) == 0 and len(_open(p)) == 5
    assert bank.trigger(p) == {"raised": [], "waiting": [], "paused": False, "notice": None}   # answered, dismissed
    # the block cap: once a block has had its share of questions in a round, the rest wait though fewer are open
    for q in _open(p):
        qs.dismiss(p, q["question_id"], by="ana")
    monkeypatch.setattr(bank, "BLOCK_BUDGET", 2)
    monkeypatch.setattr(bank, "FINDERS", (_items(4, block="z", records=0.05, prefix="m"),))
    r2 = bank.trigger(p)
    assert len(r2["raised"]) == 2 and len(r2["waiting"]) == 2 and len(_open(p)) == 2
    monkeypatch.undo()
    # a second mining run with the same names opens nothing new (their keys are in the ledger)
    from wider.seeds import mine
    real = _interview(tmp_path, "s")
    for q in _open(real):
        qs.dismiss(real, q["question_id"], by="ana")
    again = mine.mine(real, model=False)
    assert again["interview"]["raised"] == [] and again["interview"]["waiting"] == [] and _open(real) == []


def _page(replies: dict, body: str) -> dict:
    """questions.js on the stand-in page; timers do not run (the page's refresh loop)."""
    code = (STATIC / "questions.js").read_text(encoding="utf-8")
    pre = ("global.setTimeout = () => 0; global.clearTimeout = () => {}; global.fmtWhen = (s) => s || '';\n"
           "window.Drawer = { author: () => 'ana' };\n"
           "document.querySelectorAll = () => new NodeListLike([]);\n"
           "let shown = 0; $('#ask-dialog').open = false; $('#ask-dialog').showModal = () => { shown++; };\n"
           # the stand-in finds [data-x] only: a question card is an <article data-qid> holding its own buttons
           "const _fa = findAllArray; findAllArray = (parent, sel) => { if (sel !== '.question') return _fa(parent, sel);\n"
           "  const parts = String(parent.innerHTML || '').split('<article').slice(1), kids = _fa(parent, '[data-qid]');\n"
           "  kids.forEach((k, i) => { k.innerHTML = parts[i]; }); return kids; };\n"
           "const card = (box, i = 0) => [...$(box).querySelectorAll('.question')][i];\n")
    return run_node(f"{ESC}\n{PAGE}\n{pre}Object.assign(replies, {json.dumps(replies)});\n{code}\nawait flush();\n{body}")


def test_refinement_questions_use_the_questions_page(tmp_path):
    p = _interview(tmp_path)
    q = _one(p, "refine_noise_term")
    out = _cli(p, "questions", "list").output
    row = next(ln for ln in out.splitlines() if ln.strip("│ ").startswith(str(q["question_id"])))
    assert "query" in row and "drop*" in row
    assert f"{q['question_id']} refine_noise_term (why: " in out and "drop (default): no seed gained or lost" in out
    c = TestClient(create_app(p))
    got = next(x for x in c.get("/api/questions").json()["questions"] if x["question_id"] == q["question_id"])
    assert got["stage"] == "query" and got["context"]["evidence"]["term"] == "gamma*"
    assert got["context"]["default"] == "drop"
    r = c.post(f"/api/questions/{q['question_id']}/answer", json={"choice": "nope", "by": "ana"})
    assert r.status_code == 400 and "answer must be one of drop, keep, leave" in r.json()["detail"]
    assert qs.get(p, q["question_id"])["status"] == "open" and ledger.rows(p) == []
    # the page and the ask dialog render the same card: why, each consequence, the default
    listing = {"questions": [got], "counts": {"open": 1, "waiting": 2}, "mode": "ask"}
    res = _page({"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
                 "/api/questions/summary": {"open": 1, "mode": "ask", "latest_open": q["question_id"]}},
                "await window.loadQuestions(); await flush();\n"
                "console.log(JSON.stringify({page: $('#q-open').innerHTML, ask: $('#ask-body').innerHTML, shown}));")
    for html in (res["page"], res["ask"]):
        assert "Why: " in html and "(the default)" in html and "-6 records on scopus" in html, html
        assert 'data-choice="drop"' in html and 'data-writes="1"' in html
    assert res["shown"] == 1 and "2 waiting for the budget" in res["page"]


def test_admitting_a_name_closes_its_open_question(tmp_path):
    for how in ("cli", "api"):
        p = _interview(tmp_path / how)
        q = _one(p, "refine_name_add")
        name, block = q["context"]["evidence"]["name"], q["context"]["block"]
        if how == "cli":
            out = _cli(p, "seeds", "names", "--admit", name, "--block", block)
            assert out.exit_code == 0 and f"closed the open question(s) {q['question_id']} about this name" in out.output
        else:
            r = TestClient(create_app(p)).post("/api/seeds/names/admit", json={"name": name, "block": block, "author": "ana"})
            assert r.status_code == 200 and r.json()["closed_questions"] == [q["question_id"]]
        got = qs.get(p, q["question_id"])
        assert got["status"] == "dismissed" and got["applied"] == f"closed: {name} admitted to block {block}"
        assert [d["kind"] for d in ledger.rows(p)] == ["name_admit"]   # the admission is the decision, not a dismissal
        journal.undo(p, journal.changes(p, kind="name_admit")[0]["change_id"], author="ana", other=True)
        assert qs.get(p, q["question_id"])["status"] == "open" and ledger.rows(p) == []


def test_admission_releases_a_waiting_question(tmp_path, monkeypatch):
    """Closing a name-add question by admission frees the budget like an answer: the oldest waiting question opens
    (spec-checker F-R2-4); the card says which question was closed (F-R2-6)."""
    from tests.unit.test_seed_mining import CARD_NAMES, _card
    from wider.interview import bank

    p = _interview(tmp_path)
    q = _one(p, "refine_name_add")
    other = next(x for x in _open(p) if x["question_id"] != q["question_id"])
    p.store.execute("UPDATE questions SET status='waiting' WHERE question_id=?", (other["question_id"],))
    monkeypatch.setattr(bank, "OPEN_BUDGET", len(_open(p)))
    assert bank.release(p) == []   # the budget is full
    name, block = q["context"]["evidence"]["name"], q["context"]["block"]
    out = _cli(p, "seeds", "names", "--admit", name, "--block", block)
    assert out.exit_code == 0, out.output
    assert qs.get(p, other["question_id"])["status"] == "open"
    admitted = {"name": name, "block": block, "version": 2, "notice": None, "note": f"admit {name} to block {block}",
                "closed_questions": [q["question_id"]]}
    page = _card("""
      $("#sn-block").value = "a"; $("#sn-note").value = "seen in field B";
      await click("#sn-names", "[data-admit]", b => b.dataset.admit === "beta drift");
      console.log(JSON.stringify({msg: $("#sn-msg").textContent}));
    """, {"/api/seeds/names": CARD_NAMES, "/api/seeds/streams": {"references": 0, "venues": [], "names": []},
          "/api/construct/synonyms": {}, "/api/seeds/names/admit": admitted})
    assert page["msg"].endswith(f"; closed the open question(s) {q['question_id']} about this name"), page["msg"]


def test_answers_apply_and_undo(tmp_path):
    from wider.search import indexing

    p = _interview(tmp_path)
    c = TestClient(create_app(p))
    q = _one(p, "refine_name_add")
    done = qs.answer(p, q["question_id"], "add", by="ana")
    assert done["applied"].startswith("synonyms v2 saved: answer to question")
    _row, data = artifacts.load(p, "synonyms")
    assert '"beta drift"' in data["blocks"][0]["terms"] and _row["origin"] == "answer"
    assert ledger.rows(p)[-1]["mode"] == "default"
    assert "v2" in _cli(p, "construct", "list").output
    cid = journal.changes(p, kind="answer")[0]["change_id"]
    journal.undo(p, cid, author="ana")
    assert "v2" not in _cli(p, "construct", "list").output and (p.root / "artifacts").exists()
    assert qs.get(p, q["question_id"])["status"] == "open"
    assert [d for d in ledger.rows(p) if d["dedupe_key"] == q["dedupe_key"]] == []
    ledger.export(p)
    assert q["dedupe_key"] not in (p.root / "runs" / "query" / "decisions.jsonl").read_text(encoding="utf-8")
    assert c.get("/api/decisions").json()["decisions"] == []

    def apply_and_undo(qid, choice, check):
        n = len(journal.changes(p))
        qs.answer(p, qid, choice, by="ana")
        assert len(journal.changes(p)) == n + 1   # one answer, one history row
        check(True)
        journal.undo(p, journal.changes(p, kind="answer")[0]["change_id"], author="ana")
        check(False)
        assert qs.get(p, qid)["status"] == "open"

    def terms():
        return artifacts.load(p, "synonyms")[1]["blocks"][0]["terms"]

    noise = _one(p, "refine_noise_term")
    apply_and_undo(noise["question_id"], "drop", lambda on: (assert_(("gamma*" in terms()) != on)))
    v = artifacts.latest(p, "synonyms")["version"]
    hand = [("refine_variant", "a:gamma*:gamma*:gammas", {"change": {"op": "replace_piece", "block": "a",
                                                                        "term": "gamma*", "old": "gamma*",
                                                                        "new": "gammas"}}, "change", "gammas"),
            ("refine_context_and", "a:beta*", {"add": {"op": "add_term", "block": "a", "term": "beta*"}}, "add",
             "beta*")]
    for kind, key, changes, choice, term in hand:
        it = bank._item(kind, key, f"{kind}?", "Invented.", {"subject": key, "why": "invented"},
                        [bank._opt(choice, choice, "invented", writes=True, default=True), *bank._keep_leave("x")],
                        "a", changes)
        qid, _ = bank._raise(p, it, 1, v)
        p.store.execute("UPDATE questions SET status='open' WHERE question_id=?", (qid,))   # past the budget here
        apply_and_undo(qid, choice, lambda on, t=term: assert_((t in terms()) == on))
    seed = _one(p, "refine_seed_not_retrieved")
    add1 = seed["context"]["changes"]["add1"]["term"]   # 2a's best edit for the miss
    apply_and_undo(seed["question_id"], "add1", lambda on: assert_((add1 in terms()) == on))
    apply_and_undo(seed["question_id"], "not_indexed",
                   lambda on: assert_((("B002", "scopus") in indexing.verdicts(p)) == on))
    lim = bank._item("refine_limit", "scopus", "Limit scopus?", "Invented.", {"subject": "date limit", "why": "x"},
                     [bank._opt("from_seed", "Limit to 2015..", "-1"), bank._opt("from_seed_5", "Limit", "-2"),
                      bank._opt("keep", "Keep", "x", default=True)], None, platform="scopus",
                     years={"from_seed": 2015, "from_seed_5": 2010})
    lid, _ = bank._raise(p, lim, 1, v)
    p.store.execute("UPDATE questions SET status='open' WHERE question_id=?", (lid,))

    def has_limit(on):
        plat = next(x for x in Project.load(p.root).config.platforms if x.key == "scopus")
        assert bool((plat.limits or {}).get("dates")) == on, plat.limits
    apply_and_undo(lid, "from_seed", has_limit)


def assert_(ok):
    assert ok


def test_stale_questions_write_nothing_and_close(tmp_path):
    """A question asked about v1 whose edit a later version already holds (saved on the workbench, then frozen):
    answering it saves nothing and unlocks nothing, and the other open questions asking the same edit close with
    the reason (ui-tester F1)."""
    p = _interview(tmp_path)
    seed, name_q = _one(p, "refine_seed_not_retrieved"), _one(p, "refine_name_add")
    term = seed["context"]["changes"]["add1"]["term"]
    held = [c["term"] for c in [*seed["context"]["changes"].values(), name_q["context"]["changes"]["add"]]]
    _row, data = artifacts.load(p, "synonyms")
    data["blocks"][0]["terms"] += list(dict.fromkeys(held))
    artifacts.save_new_version(p, "synonyms", data, origin="manual")
    artifacts.set_status(p, "synonyms", 2, "frozen")
    r = _cli(p, "questions", "answer", str(seed["question_id"]), "add1")
    assert r.exit_code == 0, r.output
    assert f"{term} is already in block a of synonyms v2: nothing saved" in r.output and "reopens" not in r.output
    assert artifacts.latest(p, "synonyms")["version"] == 2 and artifacts.latest(p, "synonyms")["status"] == "frozen"
    got = qs.get(p, name_q["question_id"])
    assert got["status"] == "dismissed" and got["applied"] == "closed: synonyms v2 already holds this edit (asked about v1)"
    assert name_q["dedupe_key"] not in {d["dedupe_key"] for d in ledger.rows(p)}   # may be asked again, fresh
    noise = _one(p, "refine_noise_term")   # an edit v2 still takes stays open
    assert bank.close_stale(p) == [] and qs.get(p, noise["question_id"])["status"] == "open"
    # undoing the answer reopens what it closed (risk-checker R2)
    journal.undo(p, journal.changes(p, kind="answer")[0]["change_id"], author="ana", other=True)
    assert qs.get(p, name_q["question_id"])["status"] == "open" and qs.get(p, seed["question_id"])["status"] == "open"


def test_questions_from_a_seed_moved_to_validation_close(tmp_path):
    """Questions raised from B001's mined names close when B001 moves to Validation, with the reason, and cannot be
    answered; a question about another seed stays; moving B001 back lets them be asked again (risk-checker R1)."""
    p = _interview(tmp_path)
    name_q, pair_q = _one(p, "refine_name_add"), _one(p, "refine_same_construct")
    other = _one(p, "refine_seed_not_retrieved")   # about B002
    assert {r["seed"] for r in name_q["context"]["evidence"]["refs"]} == {"B001"}
    # a run on another seed alone is not a move: the pair question from B001 stays (round 4 N1)
    from wider.construct import seed
    from wider.seeds import mine
    seed.set_keywords(p, "B002", "omega braid; sigma knot")
    mine.mine(p, benchmark_id="B002", model=False)
    assert qs.get(p, pair_q["question_id"])["status"] == "open"
    p.store.execute("UPDATE questions SET status='waiting' WHERE question_id=?", (pair_q["question_id"],))
    r = _cli(p, "benchmark", "set", "B001", "Validation")
    assert r.exit_code == 0, r.output
    p = Project.load(p.root)
    got = qs.get(p, name_q["question_id"])
    assert got["status"] == "dismissed" and got["applied"] == "closed: its evidence is now only validation seed(s) B001"
    got = qs.get(p, pair_q["question_id"])   # a waiting question closes too (round 4 N2)
    assert got["status"] == "dismissed" and got["applied"] == "closed: its evidence is now only validation seed(s) B001"
    assert qs.get(p, other["question_id"])["status"] == "open"
    r = _cli(p, "questions", "answer", str(name_q["question_id"]), "add", "--by", "ana")
    assert r.exit_code != 0 and "closed: its evidence is now only validation seed(s) B001" in r.output
    assert artifacts.latest(p, "synonyms")["version"] == 1 and not ledger.rows(p)
    bank.trigger(p, "dry_run")
    assert not [q for q in _open(p) if q["kind"] in ("refine_name_add", "refine_same_construct")]
    # benchmark.csv edited by hand: nothing closed the question yet, the answer checks first
    _cli(p, "benchmark", "set", "B001", "Development")
    p = Project.load(p.root)
    mine.mine(p, model=False)   # the newest run is B002's alone: mine all seeds again
    bank.trigger(p, "dry_run")
    again = _one(p, "refine_name_add")
    papers = p.read_benchmark()
    next(b for b in papers if b.benchmark_id == "B001").set = "Validation"
    p.write_benchmark(papers)
    with __import__("pytest").raises(ValueError, match="validation seed"):
        qs.answer(p, again["question_id"], "add", by="ana")


def test_answer_that_unlocked_cannot_be_undone(tmp_path):
    import pytest

    p = _interview(tmp_path)
    artifacts.set_status(p, "synonyms", 1, "frozen")
    q = _one(p, "refine_name_add")
    r = _cli(p, "questions", "answer", str(q["question_id"]), "add", "--count-seen")
    assert r.exit_code == 0, r.output
    assert "v1 is frozen. Saving v2 reopens the query" in r.output and "synonyms v2 saved" in r.output
    cid = journal.changes(p, kind="answer")[0]["change_id"]
    with pytest.raises(ValueError, match="unlocked the frozen query"):
        journal.undo(p, cid, author="ana")
    assert artifacts.latest(p, "synonyms")["version"] == 2
    # the page: a writing answer goes through the same confirm step as any save; Cancel writes nothing
    listing = {"questions": [_one(p, "refine_noise_term")], "counts": {"open": 1}, "mode": "queue"}
    for ok, posts in ((False, 0), (True, 1)):
        res = _page({"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
                     "/api/questions/summary": {"open": 1, "mode": "queue", "latest_open": 1}},
                    f"let asked = 0; window.Refine = {{ confirmSave: async () => {{ asked++; return {str(ok).lower()}; }} }};\n"
                    "await window.loadQuestions(); await flush();\n"
                    "[...card('#q-open').querySelectorAll('[data-choice]')].find(b => b.dataset.choice === 'drop').onclick();\n"
                    "await flush();\n"
                    "console.log(JSON.stringify({asked, posts: calls.filter(c => c[0].endsWith('/answer')).length}));")
        assert res == {"asked": 1, "posts": posts}



def test_a_double_click_answers_once(tmp_path):
    """Two clicks on a card send one answer (ui-tester F1); two requests at once save one answer, one ledger row
    and one journal change, the other is refused."""
    import threading

    p = _interview(tmp_path)
    q = _one(p, "refine_noise_term")
    listing = {"questions": [q], "counts": {"open": 1}, "mode": "queue"}
    replies = {"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
               "/api/questions/summary": {"open": 1, "mode": "queue"}}
    for sel, url in (("[data-choice]", "/answer"), ("[data-dismiss]", "/dismiss")):
        res = _page(replies, "await window.loadQuestions(); await flush();\n"
                    f"const b = '{sel}' === '[data-dismiss]' ? $('[data-dismiss]') : card('#q-open').querySelectorAll('{sel}')[0]; b.onclick(); b.onclick(); await flush();\n"
                    f"console.log(JSON.stringify({{posts: calls.filter(c => c[0].endsWith('{url}')).length}}));")
        assert res == {"posts": 1}, sel
    out, errors = [], []

    def go():
        try:
            out.append(qs.answer(Project.load(p.root), q["question_id"], "keep", by="ana"))   # a project per request thread
        except ValueError as e:
            errors.append(str(e))
    ts = [threading.Thread(target=go) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(out) == 1 and errors == [f"question {q['question_id']} is already answered"]
    assert len(ledger.rows(p)) == 1 and len(journal.changes(p, kind="answer")) == 1

def test_ledger_modes_and_export(tmp_path):
    p = _interview(tmp_path)
    qs.answer(p, _one(p, "refine_noise_term")["question_id"], "keep", "the stars are a real topic", by="ana")
    qs.answer(p, _one(p, "refine_name_add")["question_id"], "add", by="ben")
    qs.dismiss(p, _one(p, "refine_same_construct")["question_id"], by="ana")
    rows = ledger.rows(p)
    assert [(d["kind"], d["mode"], d["author"]) for d in rows] == [
        ("refine_noise_term", "chosen", "ana"), ("refine_name_add", "default", "ben"),
        ("refine_same_construct", "dismissed", "ana")]
    assert all(d["evidence"] and d["round_no"] for d in rows)
    r = _cli(p, "decisions", "export")
    assert r.exit_code == 0 and "3 decisions" in r.output
    lines = (p.root / "runs" / "query" / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["mode"] for x in lines] == ["chosen", "default", "dismissed"]
    assert (p.root / "runs" / "query" / "decisions.csv").read_text(encoding="utf-8").startswith("decision_id,kind")
    refs = rows[1]["evidence"]["refs"]
    assert refs and all(set(x) == {"seed", "section", "offset", "pattern", "quote_sha256"} for x in refs)
    assert all(x["offset"] is None or isinstance(x["offset"], int) for x in refs)
    from wider.seeds import mine
    quotes = [s["quote"] for c in mine.candidates(p) if c["key"] == "beta drift" for s in c["sources"]]
    texts = "\n".join(qs.clarifications(p, "query")) + "\n".join(lines)
    assert quotes and not [x for x in quotes if x in texts]
    assert "the stars are a real topic" in qs.clarifications(p, "query")[0]
    assert "beta drift" in qs.clarifications(p, "query")[1]
    assert len(qs.clarifications(p, "query")) == 2   # a dismissal is not a clarification
    out = _cli(p, "decisions", "list", "--round", str(rows[0]["round_no"])).output
    assert " · noise term · answered by ana: " in out
    assert _cli(p, "decisions", "list", "--round", "99").output.strip() == "no decisions yet"
    assert TestClient(create_app(p)).get("/api/decisions").json()["lines"] == ledger.lines(rows)


def test_different_answer_splits_an_also_called_pair(tmp_path):
    """A "different" answer on a pair joined by "also called" evidence keeps the two apart in the name map and
    flags the mined name (not the name the seed paper introduced it with) as the look-alike (spec-checker F1)."""
    from wider.seeds import mine, names
    p = _interview(tmp_path)
    q = _one(p, "refine_same_construct")
    mined, ante = q["context"]["evidence"]["names"]
    assert ante in next(c for c in mine.candidates(p) if c["key"] == mined)["same_as"]
    assert any({mined, ante} <= set(g) for g in names.name_map(p)["groups"])   # evidence joins them first
    qs.answer(p, q["question_id"], "different", by="ana")
    m = names.name_map(p)
    assert not any({mined, ante} <= set(g) for g in m["groups"])
    assert m["look_alikes"] == [{"name": mined, "of": ante}]


def test_decisions_enter_prompts(tmp_path):
    from wider.construct.propose import build_prompt
    from wider.seeds import mine

    p = _interview(tmp_path)
    assert "Decisions from the researcher" not in build_prompt(p, "synonyms")[0]
    q = _one(p, "refine_noise_term")
    qs.answer(p, q["question_id"], "drop", by="ana")
    prompt, ctx = build_prompt(p, "synonyms")
    part = prompt.split("# Decisions from the researcher", 1)[1]
    assert "term gamma* in block a" in part and "noise on scopus: 6 records" in part and "Drop gamma* from block a" in part
    assert ctx["decisions_n"] == 1
    assert "Decisions from the researcher" not in build_prompt(p, "cells")[0]
    assert "gamma*" in mine.system_prompt(p) and "Decisions from the researcher" in mine.system_prompt(p)
    # the screening prompt never reads the query decisions: no screening module asks for them
    import pathlib
    screen = pathlib.Path(__file__).resolve().parents[2] / "wider" / "screen"
    assert not [f.name for f in screen.glob("*.py") if "clarifications(" in f.read_text(encoding="utf-8")]
    # the newest 30, oldest first
    for i in range(32):
        p.store.execute("INSERT INTO decisions(kind, dedupe_key, evidence_json, options_json, mode, consequence, "
                        "author, at) VALUES('refine_name_add', ?, ?, '[]', 'chosen', '', 'ana', '')",
                        (f"k{i}", json.dumps({"subject": f"name n{i}"})))
    got = qs.clarifications(p, "query")
    assert len(got) == 30 and got[0].startswith("name n2:") and got[-1].startswith("name n31:")


def _round_stop(p, monkeypatch, prefix="n") -> dict:
    for q in _open(p):
        qs.dismiss(p, q["question_id"], by="ana")
    monkeypatch.setattr(bank, "FINDERS", (_items(3, records=-0.01, prefix=prefix),))
    bank.trigger(p)
    for q in _open(p):
        qs.answer(p, q["question_id"], "keep", by="ana")
    return _one(p, "refine_round_stop")


def test_freeze_and_more_answers(tmp_path, monkeypatch):
    p = _interview(tmp_path)
    stop = _round_stop(p, monkeypatch)
    rnd = bank.interview_round(p)
    c = TestClient(create_app(p))
    r = c.post(f"/api/questions/{stop['question_id']}/answer", json={"choice": "freeze", "text": "good enough", "by": "ana"})
    assert r.status_code == 200, r.text
    assert "nothing was frozen by this answer" in r.json()["applied"] and "wider construct freeze synonyms" in r.json()["applied"]
    assert bank.interview_round(p) == rnd + 1 and not bank.paused(p) and artifacts.latest_frozen(p) is None
    assert ledger.rows(p)[-1]["evidence"]["note"] == "good enough" and "note: good enough" in ledger.rows(p)[-1]["text"]
    stop = _round_stop(p, monkeypatch, "m")
    out = _cli(p, "questions", "answer", str(stop["question_id"]), "more", "--by", "ana")
    assert out.exit_code == 0 and f"interview round {rnd + 2} started" in out.output
    assert bank.interview_round(p) == rnd + 2 and artifacts.latest_frozen(p) is None
    # the page: the note box goes with the answer; "freeze" then opens the Construct workbench
    stop = _round_stop(p, monkeypatch, "o")
    got = next(x for x in c.get("/api/questions").json()["questions"] if x["question_id"] == stop["question_id"])
    listing = {"questions": [got], "counts": {"open": 1}, "mode": "queue"}
    res = _page({"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
                 "/api/questions/summary": {"open": 1, "mode": "queue"},
                 f"/api/questions/{stop['question_id']}/answer": {"applied": "interview round 3 closed", "change_id": 1}},
                "global.location = { hash: '#questions' }; global.setTimeout = (f) => { f(); return 0; };\n"
                "await window.loadQuestions(); await flush();\n"
                "const html = $('#q-open').innerHTML; $('[data-note]').value = 'enough for now';\n"
                "await card('#q-open').querySelectorAll('[data-choice]')[0].onclick(); await flush();\n"
                "console.log(JSON.stringify({html, hash: location.hash, msg: $('.q-msg').innerHTML,\n"
                "  sent: calls.filter(c => c[1] === 'POST').map(c => c[2])}));")
    assert 'data-kind="refine_round_stop"' in res["html"] and "data-note" in res["html"]
    assert res["sent"] == [{"choice": "freeze", "text": "enough for now", "by": "ana"}]
    assert res["hash"] == "construct" and "Opening the Construct workbench" in res["msg"]



def test_questions_page_without_a_name_sends_nothing(tmp_path):
    """risk-checker R1 (D33): an answer or a dismissal is signed; with no name WIDER works as, nothing is posted and the
    card points at Change beside "Working as"."""
    p = _interview(tmp_path)
    q = _one(p, "refine_noise_term")
    got = next(x for x in TestClient(create_app(p)).get("/api/questions").json()["questions"]
               if x["question_id"] == q["question_id"])
    listing = {"questions": [got], "counts": {"open": 1}, "mode": "queue"}
    for act in ("[data-choice]", "[data-dismiss]"):
        res = _page({"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
                     "/api/questions/summary": {"open": 1, "mode": "queue"}},
                    "window.Drawer = { author: () => '' };\n"
                    "await window.loadQuestions(); await flush();\n"
                    f"const b = '{act}' === '[data-dismiss]' ? $('[data-dismiss]') : card('#q-open').querySelectorAll('{act}')[0];\n"
                    "await b.onclick(); await flush();\n"
                    "console.log(JSON.stringify({msg: $('.q-msg').innerHTML,\n"
                    "  sent: calls.filter(c => c[1] === 'POST').map(c => c[2])}));")
        assert res["sent"] == [], act
        assert "Answers are signed with your name:" in res["msg"] and "enter your name first" in res["msg"], act

def test_stop_rule(tmp_path, monkeypatch):
    p = _interview(tmp_path)
    for q in _open(p):
        qs.dismiss(p, q["question_id"], by="ana")
    monkeypatch.setattr(bank, "FINDERS", (_items(3, records=-0.01),))
    bank.trigger(p)
    for q in _open(p):   # three answers: recall 0, records 1% each
        qs.answer(p, q["question_id"], "keep", by="ana")
    stop = _one(p, "refine_round_stop")
    assert [o["value"] for o in stop["options"]] == ["freeze", "more", "stop"] and len(_open(p)) == 1
    monkeypatch.setattr(bank, "FINDERS", (_items(2, prefix="later"),))
    assert bank.trigger(p, "mine")["paused"] and len(_open(p)) == 1   # nothing else until the next dry run
    done = qs.answer(p, stop["question_id"], "stop", by="ana")
    assert "until the next dry run or import" in done["applied"]
    assert bank.trigger(p, "diagnose")["raised"] == []
    r = bank.trigger(p, "dry_run")
    assert len(r["raised"]) == 2 and not r["paused"]
    assert _cli(p, "questions", "list").exit_code == 0


def test_answer_after_kept_look_needs_confirm(tmp_path):
    """Scenario "Saving after a kept look" for `wider questions answer` (validation-reuse D2): an answer that saves a
    version after a look that kept papers as validation stops with the notice and saves nothing; with --confirm it
    saves and records the papers as used, in one final change."""
    from wider.construct import seed

    p = _interview(tmp_path)
    seed.add_by_dois(p, ["10.1000/val.9"], set_name="Validation", source="colleague")
    r = _cli(p, "coverage", "--validation", "--keep-validation")
    assert r.exit_code == 0, r.output
    q = _one(Project.load(p.root), "refine_name_add")
    r = _cli(p, "questions", "answer", str(q["question_id"]), "add", "--count-seen")
    flat = " ".join(r.output.split())
    assert r.exit_code == 2 and "recorded as used" in flat and f"{artifacts.REVISION_CONFIRM}" in flat, r.output
    p = Project.load(p.root)
    assert artifacts.latest(p, "synonyms")["version"] == 1 and journal.changes(p, kind="validation_used") == []
    assert _one(p, "refine_name_add")["question_id"] == q["question_id"]   # still open
    r = _cli(p, "questions", "answer", str(q["question_id"]), "add", "--confirm")
    assert r.exit_code == 0 and "synonyms v2 saved" in r.output, r.output
    p = Project.load(p.root)
    assert [(c["summary"], c["undoable"]) for c in journal.changes(p, kind="validation_used")] == [
        ("validation papers recorded as used: v2 saved after a look", False)]


def test_budget_question_names_the_sample(tmp_path):
    """Scenario "The interview's budget question names the sample": on the 1,249 / 650,000 project the budget question
    is an estimate and its why-text is the budget sentence naming both numbers."""
    p, (sid,) = _oa(tmp_path, "s", [("openalex", SAMPLE_ROWS)], budget=5000)
    p = _mark(p, sid, 650000)
    bank.trigger(p, "dry_run")
    q = next(q for q in qs.list_questions(Project.load(p.root)) if q["kind"] == "refine_screening_budget")
    assert q["title"] == "about 650,000 records: over the budget of 5000 (estimate from a random sample)", q["title"]
    assert q["context"]["evidence"]["why"] == ("about 650,000 records, 645,000 over the budget of 5000 (estimate: openalex is a "
                                   "random sample of 1,249 of 650,000 results)")
    assert [o["label"] for o in q["options"]][:2] == ["Raise the budget to about 650,000", "Keep the budget of 5000"]
