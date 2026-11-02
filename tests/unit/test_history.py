"""WP15: version history and undo of manual changes (labels, adjudications, duplicate decisions, artifact statuses,
notes, tags, answered questions, settings), the refusals that keep undo safe, routes, CLI and the static page."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.helpers import consent, mock_client
from tests.unit.test_questions import _fake
from tests.unit.test_records import _deduped, _sample
from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as screen_project  # noqa: F401 - pytest fixture
from wider.cli import app as cli_app
from wider.construct import artifacts
from wider.core import annotations as ann
from wider.core import journal
from wider.core import questions as qs
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.dedup.engine import decide, queue_rows
from wider.sample.labels import adjudicate, label, lock
from wider.screen import engine as engmod
from wider.screen.decisions import decisions
from wider.screen.engine import replay, screen
from wider.screen.protocol import run_protocol
from wider.ui.app import create_app

runner = CliRunner()


def _label(p, rid):
    return p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='ana'", (rid,))


def test_label_undo_redo_and_changed_since(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")
    c1 = journal.last_change_id()
    label(p, "dev", ids[0], "ana", "exclude", reason="R1")
    c2 = journal.last_change_id()
    hist = journal.changes(p)
    assert [h["change_id"] for h in hist[:2]] == [c2, c1] and hist[0]["kind"] == "label" and hist[0]["record_id"] == ids[0]
    assert "include" not in hist[0]["summary"] and "exclude" not in hist[0]["summary"]   # blind-safe summaries
    assert not hist[1]["undoable"] and "changed since" in hist[1]["why_not"]
    with pytest.raises(ValueError, match="changed since"):
        journal.undo(p, c1, other=True)
    u2 = journal.undo(p, c2, other=True)
    assert _label(p, ids[0])["decision"] == "include" and u2["kind"] == "undo"
    journal.undo(p, c1, other=True)
    assert _label(p, ids[0]) is None
    with pytest.raises(ValueError, match="already undone"):
        journal.undo(p, c1, other=True)
    redo = journal.changes(p)[0]
    assert redo["summary"].startswith("undo: ana labelled record")
    again = journal.undo(p, redo["change_id"], other=True)    # undo of the undo puts the label back
    assert _label(p, ids[0])["decision"] == "include" and again["summary"].startswith("redo: ana labelled record")
    label(p, "dev", ids[0], "ana", "include")      # same value: nothing changed, nothing recorded
    assert journal.changes(p)[0]["kind"] == "undo"


def test_a_locked_samples_labels_are_not_undone(tmp_path):
    """Undo by id removed a label from a locked sample (twentieth round, keyboard-only tester): a change made before
    the lock is not undone or redone while the sample is locked; it is corrected instead (D7)."""
    from wider.sample.labels import unlock
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    label(p, "dev", ids[0], "bo", "exclude")   # an adjudication needs a disagreement
    label(p, "dev", ids[0], "ana", "include")
    adjudicate(p, "dev", ids[0], "include", by="cara")
    lab, adj = journal.changes(p, kind="label")[0], journal.changes(p, kind="adjudication")[0]
    lock(p, "dev", by="dan")   # the lock and unlock are journalled too (manual-review D5), signed by dan
    why = "sample 'dev' was locked after this change: correct the label instead, with a reason"
    for ch in (lab, adj):
        r = c.post(f"/api/history/{ch['change_id']}/undo")
        assert r.status_code == 409 and why in r.json()["detail"]
    labelled = lambda: [x for x in journal.changes(p) if x["kind"] not in ("sample_lock", "sample_unlock")]  # noqa: E731
    assert [x["why_not"] for x in labelled()[:2]] == [why, why] and _label(p, ids[0])["decision"] == "include"
    for author in ("ana", "cara"):   # the label's author and the adjudicator (21st round)
        r = c.post(f"/api/history/undo-last?author={author}")
        assert r.status_code == 409 and r.json()["detail"].endswith(why), author
    newest = journal.changes(p)[0]   # only the lock itself stays undoable (D5)
    assert newest["kind"] == "sample_lock" and newest["why_not"] is None
    unlock(p, "dev", by="dan")
    assert c.post(f"/api/history/{lab['change_id']}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200 and _label(p, ids[0]) is None
    lock(p, "dev", by="dan")
    redo = labelled()[0]
    assert redo["why_not"] == why   # a redo is refused too while the sample is locked
    r = c.post(f"/api/history/{redo['change_id']}/undo")
    assert r.status_code == 409 and why in r.json()["detail"] and _label(p, ids[0]) is None


def test_adjudication_dedup_artifact_and_annotations(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")
    label(p, "dev", ids[0], "ben", "exclude")
    adjudicate(p, "dev", ids[0], "include", by="cara")
    assert journal.changes(p, kind="adjudication")[0]["author"] == "cara"
    journal.undo(p, other=True)
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 0
    lock(p, "dev")
    adjudicate(p, "dev", ids[0], "include", by="cara")   # a first adjudication on a locked sample is an addition (D2)
    assert p.store.scalar("SELECT COUNT(*) FROM adjudications") == 1
    # duplicate decision back to the queue
    q = queue_rows(p)[0]
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="ana")
    assert not any(r["raw_a"] == q["raw_a"] and r["raw_b"] == q["raw_b"] for r in queue_rows(p))
    journal.undo(p, other=True)
    assert any(r["raw_a"] == q["raw_a"] and r["raw_b"] == q["raw_b"] for r in queue_rows(p))
    # artifact status: approve can be undone, a freeze cannot
    row = artifacts.save_new_version(p, "anchors", {"anchors": {"a": " ".join(["Studies in which employees speak up to managers about problems at work and the reasons they do."] * 3)}}, origin="manual")
    artifacts.set_status(p, "anchors", row["version"], "approved")
    journal.undo(p, other=True)
    assert artifacts.latest(p, "anchors")["status"] == "draft"
    artifacts.set_status(p, "anchors", row["version"], "frozen")
    fz = journal.changes(p, kind="artifact_status")[0]
    assert not fz["undoable"] and "freeze is final" in fz["why_not"]
    # restore an older version as a new draft
    artifacts.save_new_version(p, "anchors", {"anchors": {"b": " ".join(["Studies in which employees stay silent about problems they see at work and the reasons for it."] * 3)}}, origin="manual")
    r = artifacts.restore_version(p, "anchors", row["version"])
    assert r["version"] == 3 and artifacts.load(p, "anchors")[1] == {"anchors": {"a": " ".join(["Studies in which employees speak up to managers about problems at work and the reasons they do."] * 3)}}
    with pytest.raises(ValueError, match="already the newest"):
        artifacts.restore_version(p, "anchors", 3)
    # notes and tags
    nid = ann.add_note(p, ids[1], "ana", "first thought")
    ann.remove_note(p, nid, "ana")
    journal.undo(p, other=True)                       # the deleted note comes back with its id
    assert [n["note_id"] for n in ann.for_record(p, ids[1])["notes"]] == [nid]
    ann.add_tag(p, ids[1], "ana", "check")
    journal.undo(p, other=True)
    assert ann.for_record(p, ids[1])["tags"] == []


def test_settings_changes_are_journaled_and_undone(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    assert journal.changes(p) == []      # creating a project is not a change
    r = runner.invoke(cli_app, ["protocol", "set", "screening.majority", "3", "--dir", str(p.root)], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 0, r.output
    ch = journal.changes(p)[0]
    assert ch["kind"] == "config" and ch["author"] == "ana" and "protocol.screening.majority: 2 → 3" in ch["summary"]
    c = TestClient(create_app(Project.load(p.root)))
    c.patch("/api/project", json={"title": "Voice review"}, headers={"X-Wider-Author": "ben"})
    top = journal.changes(p)[0]
    assert top["author"] == "ben" and "title" in top["summary"]
    assert not journal.changes(p)[1]["undoable"]   # project.yaml changed since
    assert c.post(f"/api/history/{ch['change_id']}/undo").status_code == 409
    assert c.post(f"/api/history/{top['change_id']}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200
    assert c.post(f"/api/history/{ch['change_id']}/undo", headers={"X-Wider-Author": "ana"}).status_code == 200
    q = Project.load(p.root)
    assert q.config.title == "" and q.config.protocol.screening.majority == 2


def test_answers_undo_including_screening_log_and_replay(screen_project, monkeypatch):  # noqa: F811
    p = screen_project
    good = _responder({"calls": 0})

    def respond(req):
        return "not json" if "Paper 3 " in req.user else good(req)
    _fake(monkeypatch, respond)
    res = screen(p, "corpus", pass_name="A")
    qid = qs.list_questions(p)[0]["question_id"]
    qs.answer(p, qid, "include", by="ana")
    rid = qs.get(p, qid)["record_id"]
    assert replay(p, res["run_id"])["replayed"] == res["n_records"]     # human reads replay without a model input
    row = lambda: p.store.one("SELECT ok, rule_decision FROM screen_calls WHERE run_id=? AND record_id=? AND pass='A'", (res["run_id"], rid))  # noqa: E731
    assert dict(row()) == {"ok": 1, "rule_decision": "include"}
    ch = journal.changes(p, kind="answer")[0]
    assert {"questions", "screen_calls", "runs"} <= set(ch["touches"])
    journal.undo(p, ch["change_id"], other=True)
    assert row()["ok"] == 0 and qs.get(p, qid)["status"] == "open"
    log = p.root / p.store.one("SELECT dir FROM runs WHERE run_id=?", (res["run_id"],))["dir"] / "responses.jsonl"
    assert '"human_retracted": true' in log.read_text()
    replay(p, res["run_id"])
    assert row()["ok"] == 0                                             # the retracted read stays out after a replay
    # a three-way split answer is undone back to maybe and the question reopens
    seen = {}
    order = ["include", "maybe", "exclude"]

    def split(req):
        k = req.user
        seen[k] = seen.get(k, -1) + 1
        return {**good(req), "decision": order[seen[k] % 3]}
    _fake(monkeypatch, split)
    monkeypatch.setattr(engmod, "apply_rule", lambda parsed, text, spec, thin=False, quote_min_chars=8:
                        {"raw": parsed["decision"], "final": parsed["decision"], "flags": []})
    run_protocol(p, "sample:development", "dev")
    sq = next(q for q in qs.list_questions(p) if q["kind"] == "screen_split")
    qs.answer(p, sq["question_id"], "exclude", by="ana")
    assert decisions(p, "dev")[sq["record_id"]] == "exclude"
    journal.undo(p, other=True)
    assert decisions(p, "dev")[sq["record_id"]] == "maybe" and qs.get(p, sq["question_id"])["status"] == "open"


def test_seed_answer_undo_restores_benchmark_csv(tmp_path, monkeypatch):
    from wider.construct import benchmark as bmmod
    p = Project.init(tmp_path / "r")
    p.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Voice at work", doi="10.1000/wrong", set="Development")])
    monkeypatch.setattr(bmmod, "fetch_abstract", lambda doi, min_words=20, **kw: (None, []))
    bmmod.fetch(consent(p), client=mock_client({}))
    q = qs.list_questions(p)[0]
    qs.answer(p, q["question_id"], "doi", text="10.1000/right")
    assert p.read_benchmark()[0].doi == "10.1000/right"
    journal.undo(p, other=True)
    assert p.read_benchmark()[0].doi == "10.1000/wrong"
    assert p.store.scalar("SELECT doi FROM benchmark_papers WHERE benchmark_id='B1'") == "10.1000/wrong"


def test_history_routes_blind_and_undo_last(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    c.post("/api/labels", json={"sample_id": 1, "record_id": ids[0], "reviewer": "ana", "decision": "include"})
    r = c.post("/api/labels", json={"sample_id": 1, "record_id": ids[1], "reviewer": "ben", "decision": "exclude"})
    assert r.json()["change_id"]
    tag = c.post(f"/api/records/{ids[0]}/tags", json={"author": "ana", "tag": "x"}).json()
    assert tag["change_id"]
    blind = c.get(f"/api/history?record_id={ids[1]}&blind=true&author=ana").json()
    assert blind == []                                     # ben's label stays out of ana's blind view
    assert c.get("/api/history?blind=true").status_code == 400
    full = c.get("/api/history").json()
    assert {"items"} & set(full[0]) == set()              # row contents never leave the server
    u = c.post("/api/history/undo-last?author=ben").json()
    assert "ben labelled" in u["summary"] and p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='ben'") == 0
    assert c.post("/api/history/9999/undo").status_code == 404
    row = artifacts.save_new_version(p, "anchors", {"anchors": {"y": " ".join(["Studies of upward communication from employees to managers about concerns in organisations."] * 3)}}, origin="manual")
    artifacts.save_new_version(p, "anchors", {"anchors": {"z": " ".join(["Studies of how supervisors respond when employees raise concerns or suggest changes at work."] * 3)}}, origin="manual")
    assert c.post(f"/api/construct/anchors/{row['version']}/restore").json()["version"] == 3
    assert c.post("/api/construct/anchors/3/restore").status_code == 409


def test_history_cli(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1])
    label(p, "dev", ids[0], "ana", "include")
    root, env = str(p.root), {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}   # undo without an id undoes ana's last change
    r = runner.invoke(cli_app, ["history", "list", "--dir", root], env=env)
    assert r.exit_code == 0 and "ana labelled record" in r.output and "yes" in r.output
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env=env)
    assert r.exit_code == 0 and "undo: ana labelled record" in r.output
    assert runner.invoke(cli_app, ["history", "undo", "1", "--dir", root], env=env).exit_code == 1   # already undone
    artifacts.save_new_version(p, "anchors", {"anchors": {"y": " ".join(["Studies of upward communication from employees to managers about concerns in organisations."] * 3)}}, origin="manual")
    artifacts.save_new_version(p, "anchors", {"anchors": {"z": " ".join(["Studies of how supervisors respond when employees raise concerns or suggest changes at work."] * 3)}}, origin="manual")
    r = runner.invoke(cli_app, ["construct", "restore", "anchors", "1", "--dir", root], env=env)
    assert r.exit_code == 0 and "restored as v3" in r.output



def test_unsigned_and_older_signed_changes_are_undone_by_id(tmp_path):
    """A page change without a name, and a change signed before page names existed (with the login name), are not
    anyone's "last change": undo-last leaves them alone and they are undone by their id (eighth round, spec-checker)."""
    p, ids = _deduped(tmp_path)
    c = TestClient(create_app(p))
    q1 = queue_rows(p)[0]
    assert c.post("/api/dedup/decide", json={"raw_a": q1["raw_a"], "raw_b": q1["raw_b"], "decision": "duplicate"}).status_code == 200
    unsigned = journal.changes(p)[0]
    assert unsigned["author"] == ""
    # a change journalled by an older WIDER, signed with the login name
    ann.add_tag(p, ids[0], "tester", "edge-case")
    older = journal.changes(p)[0]
    p.store.execute("UPDATE history SET author='old-login' WHERE change_id=?", (older["change_id"],))
    # a change journalled by an older version that signed page changes "ui" (twentieth round, spec-checker)
    ann.add_tag(p, ids[1], "tester", "edge-case")
    old_ui = journal.changes(p)[0]
    p.store.execute("UPDATE history SET author='ui' WHERE change_id=?", (old_ui["change_id"],))
    r = c.post("/api/history/undo-last")
    assert r.status_code == 400 and r.json()["detail"] == "undo-last needs the reviewer's name"
    r = c.post("/api/history/undo-last?author=tester")
    assert r.status_code == 409 and "nothing to undo by tester" in r.json()["detail"]
    assert [x["author"] for x in journal.changes(p)[:3]] == ["ui", "old-login", ""]   # older names are kept as they were
    # an older login name counts as another name (manual-review D7): no undoer name is refused, nothing changes
    r = c.post(f"/api/history/{older['change_id']}/undo")
    assert r.status_code == 400 and r.json()["detail"] == "give your name to undo old-login's change"
    assert journal.changes(p)[0]["change_id"] == old_ui["change_id"]
    for ch in (old_ui, unsigned):   # unsigned and "ui" changes are undone by id without asking
        assert c.post(f"/api/history/{ch['change_id']}/undo").status_code == 200
    u = c.post(f"/api/history/{older['change_id']}/undo?other=true", headers={"X-Wider-Author": "ana"})
    assert u.status_code == 200 and u.json()["author"] == "ana"
    assert queue_rows(p)[0]["raw_a"] == q1["raw_a"] and not p.store.scalar("SELECT COUNT(*) FROM record_tags")


def test_undo_of_another_persons_change_asks_first(tmp_path):
    """manual-review D7: "bo" changed a setting; "ana" undoing it by id is refused naming bo (CLI exit 1, route 409
    with the author, nothing changes); with --other or other=true it is restored and the undo is signed "ana"."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    root, bo, ana = str(p.root), {"WIDER_REVIEWER": "bo"}, {"WIDER_REVIEWER": "ana"}
    assert runner.invoke(cli_app, ["protocol", "set", "screening.majority", "3", "--dir", root], env=bo).exit_code == 0
    cid = journal.changes(p)[0]["change_id"]
    want = f"change {cid} was made by bo; add --other to undo it as ana"
    r = runner.invoke(cli_app, ["history", "undo", str(cid), "--dir", root], env=ana)
    assert r.exit_code == 1 and want in " ".join(r.output.split())
    c = TestClient(create_app(Project.load(p.root)))
    r = c.post(f"/api/history/{cid}/undo", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 409 and r.json() == {"detail": want, "author": "bo"}
    assert Project.load(p.root).config.protocol.screening.majority == 3 and journal.changes(p)[0]["change_id"] == cid
    r = runner.invoke(cli_app, ["history", "undo", str(cid), "--other", "--dir", root], env=ana)
    assert r.exit_code == 0, r.output
    assert Project.load(p.root).config.protocol.screening.majority == 2
    assert journal.changes(p)[0]["author"] == "ana" and journal.changes(p)[0]["kind"] == "undo"
    # the page's dialog sends other=true: a redo of ana's undo by bo is asked the same way
    undo_id = journal.changes(p)[0]["change_id"]
    r = c.post(f"/api/history/{undo_id}/undo", headers={"X-Wider-Author": "bo"})
    assert r.status_code == 409 and r.json()["author"] == "ana"
    r = c.post(f"/api/history/{undo_id}/undo?other=true", headers={"X-Wider-Author": "bo"})
    assert r.status_code == 200 and r.json()["author"] == "bo"
    assert Project.load(p.root).config.protocol.screening.majority == 3


def test_another_reviewers_label_is_undone_after_asking(tmp_path, monkeypatch):
    """manual-review D7: "ana" labelled a record; "bo" undoing that label is refused first (CLI exit 1, HTTP 409) and the
    label stays; with --other ana's label is removed and History shows the undo by "bo"."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    monkeypatch.setenv("WIDER_REVIEWER", "ana")
    label(p, "dev", ids[0], "ana", "include")
    cid = journal.changes(p, kind="label")[0]["change_id"]
    monkeypatch.setenv("WIDER_REVIEWER", "bo")
    r = runner.invoke(cli_app, ["history", "undo", str(cid), "--dir", str(p.root)])
    assert r.exit_code == 1 and "was made by ana; add --other to undo it as bo" in " ".join(r.output.split())
    r = TestClient(create_app(p)).post(f"/api/history/{cid}/undo", headers={"X-Wider-Author": "bo"})
    assert r.status_code == 409 and r.json()["author"] == "ana" and _label(p, ids[0])["decision"] == "include"
    r = runner.invoke(cli_app, ["history", "undo", str(cid), "--other", "--dir", str(p.root)])
    assert r.exit_code == 0, r.output
    assert _label(p, ids[0]) is None
    newest = journal.changes(p)[0]
    assert newest["author"] == "bo" and newest["kind"] == "undo" and journal.changes(p, kind="label")[0]["author"] == "ana"

def test_undo_last_goes_back_through_one_reviewers_changes(tmp_path):
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    label(p, "dev", ids[0], "ana", "include")                     # A
    label(p, "dev", ids[1], "ana", "exclude", reason="R1")        # B
    label(p, "dev", ids[0], "ben", "exclude", reason="R1")        # ben's, between and after
    assert c.post("/api/history/undo-last").status_code == 400   # whose change? no name, nothing undone
    assert c.post("/api/history/undo-last?author=%20").status_code == 400
    u = c.post("/api/history/undo-last?author=ana").json()
    assert u["summary"].startswith("undo: ana labelled record") and _label(p, ids[1]) is None and _label(p, ids[0])
    c.post("/api/history/undo-last?author=ana")                  # the undo itself is skipped: further back to A
    assert _label(p, ids[0]) is None
    r = c.post("/api/history/undo-last?author=ana")
    assert r.status_code == 409 and "nothing to undo by ana" in r.json()["detail"]
    assert p.store.scalar("SELECT COUNT(*) FROM labels WHERE reviewer_id='ben'") == 1   # ben's label untouched
    assert c.post("/api/history/undo-last?author=ben").status_code == 200
    with pytest.raises(ValueError, match="needs the reviewer's name"):
        journal.undo_last(p, " ")


def test_undo_last_names_a_near_match_and_reads_the_header(tmp_path):
    """manual-review D7: names that differ only in case or spacing are never merged; undo-last as "Ana" finds nothing
    and names 'ana', and the CLI says the same; without ?author= the page's name header signs and chooses."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    label(p, "dev", ids[0], "ana", "include")
    r = c.post("/api/history/undo-last?author=Ana")
    assert r.status_code == 409 and r.json()["detail"] == "nothing to undo by Ana; there are changes by 'ana'"
    with pytest.raises(ValueError, match="there are changes by 'ana'"):
        journal.undo_last(p, "  ANA ")
    out = runner.invoke(cli_app, ["history", "undo", "--dir", str(p.root)], env={"WIDER_REVIEWER": "Ana"})
    assert out.exit_code == 1 and "there are changes by 'ana'" in " ".join(out.output.split())
    assert _label(p, ids[0])["decision"] == "include"   # nothing was undone
    assert c.post("/api/history/undo-last?author=Bo").json()["detail"] == "nothing to undo by Bo"
    r = c.post("/api/history/undo-last", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200 and _label(p, ids[0]) is None and journal.changes(p)[0]["author"] == "ana"


def test_history_list_refuses_an_unknown_kind(tmp_path):
    """manual-review 6.3: `history list --kind` and the route refuse a kind nobody records, naming the known ones;
    `history kinds` and GET /api/history/kinds list the same choices, review decisions and sample locks among them."""
    p, ids = _deduped(tmp_path)
    r = runner.invoke(cli_app, ["history", "list", "--kind", "labels", "--dir", str(p.root)])
    text = " ".join(r.output.split())
    assert r.exit_code == 1 and "unknown kind 'labels'; known kinds:" in text and "review_decision" in text
    c = TestClient(create_app(p))
    bad = c.get("/api/history?kind=labels")
    assert bad.status_code == 400 and bad.json()["detail"].startswith("unknown kind 'labels'; known kinds: adjudication")
    ks = c.get("/api/history/kinds").json()
    assert ks == journal.kinds() and {"kind": "sample_lock", "label": "sample locked"} in ks
    assert {k["kind"] for k in ks} == set(journal.KIND_LABELS) | {"uploads_reopened"}
    out = runner.invoke(cli_app, ["history", "kinds"]).output
    assert "review_decision\treview decision" in out and "uploads_reopened\tuploads reopened" in out
    assert runner.invoke(cli_app, ["history", "list", "--kind", "uploads_reopened", "--dir", str(p.root)]).exit_code == 0


def test_unknown_change_id_and_a_hand_edited_setting(tmp_path):
    """manual-review "Undo edge cases are stated": an unknown id is "no change <id>" (CLI exit 1, route 404); a title
    change undone after project.yaml was edited by hand is refused and the hand edit stays."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    root = str(p.root)
    r = runner.invoke(cli_app, ["history", "undo", "999999", "--dir", root])
    assert r.exit_code == 1 and "no change 999999" in r.output
    c = TestClient(create_app(Project.load(p.root)))
    assert c.post("/api/history/999999/undo", headers={"X-Wider-Author": "ana"}).json() == {"detail": "no change 999999"}
    assert c.post("/api/history/999999/undo", headers={"X-Wider-Author": "ana"}).status_code == 404
    assert c.patch("/api/project", json={"title": "Voice review"}, headers={"X-Wider-Author": "ana"}).status_code == 200
    ch = journal.changes(p)[0]
    f = p.root / "project.yaml"
    f.write_text(f.read_text(encoding="utf-8").replace("Voice review", "Edited by hand"), encoding="utf-8")
    r = runner.invoke(cli_app, ["history", "undo", str(ch["change_id"]), "--dir", root], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 1 and "project.yaml changed since; undo the later change first" in " ".join(r.output.split())
    assert "Edited by hand" in f.read_text(encoding="utf-8") and journal.changes(p)[0]["change_id"] == ch["change_id"]


def test_undo_redo_keeps_the_first_label(tmp_path):
    """manual-review "Undo edge cases are stated": ana then bo label a record differently; ana's label undone and
    redone keeps its place, so the final labels still name ana's label as the first reviewer's."""
    from wider.sample.labels import final_labels
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")
    label(p, "dev", ids[0], "bo", "exclude", reason="R1")
    first = next(x for x in final_labels(p, "dev") if x["record_id"] == ids[0])
    assert first["source"] == "ana" and first["decision"] == "include"
    ana = next(c for c in journal.changes(p, kind="label") if "ana" in c["summary"])
    u = journal.undo(p, ana["change_id"], author="ana")
    assert next(x for x in final_labels(p, "dev") if x["record_id"] == ids[0])["source"] == "bo"
    journal.undo(p, u["change_id"], author="ana")   # redo
    again = next(x for x in final_labels(p, "dev") if x["record_id"] == ids[0])
    assert again["source"] == "ana" and again["decision"] == "include" and again["open_disagreement"]


def test_undo_last_cli_and_a_blocking_change(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    root = str(p.root)
    ana, ben = {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}, {"COLUMNS": "200", "WIDER_REVIEWER": "ben"}
    for v in ("3", "1"):
        assert runner.invoke(cli_app, ["protocol", "set", "screening.majority", v, "--dir", root], env=ana).exit_code == 0
    assert runner.invoke(cli_app, ["history", "undo", "--dir", root], env=ana).exit_code == 0
    assert Project.load(p.root).config.protocol.screening.majority == 3
    r = runner.invoke(cli_app, ["history", "undo", "--author", "ana", "--dir", root], env=ben)   # --author wins
    assert r.exit_code == 0 and Project.load(p.root).config.protocol.screening.majority == 2
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env=ana)
    assert r.exit_code == 1 and "nothing to undo by ana" in r.output
    # ana's change cannot go back while ben's later change to the same file stands: undo stops and names it
    runner.invoke(cli_app, ["protocol", "set", "screening.majority", "3", "--dir", root], env=ana)
    runner.invoke(cli_app, ["protocol", "set", "screening.majority", "1", "--dir", root], env=ben)
    bid = journal.changes(p)[0]["change_id"]
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env=ana)
    assert r.exit_code == 1 and f"blocked by change {bid} by ben" in " ".join(r.output.split())
    assert Project.load(p.root).config.protocol.screening.majority == 1
    c = TestClient(create_app(Project.load(p.root)))
    r = c.post("/api/history/undo-last?author=ana")
    assert r.status_code == 409 and f"blocked by change {bid} by ben" in r.json()["detail"]


def test_repeated_cli_undo_restores_two_settings(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    root, env = str(p.root), {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}
    assert runner.invoke(cli_app, ["protocol", "set", "screening.majority", "3", "--dir", root], env=env).exit_code == 0
    assert runner.invoke(cli_app, ["protocol", "set", "corpus.thin_words", "30", "--dir", root], env=env).exit_code == 0
    assert runner.invoke(cli_app, ["history", "undo", "--dir", root], env=env).exit_code == 0
    pr = Project.load(p.root).config.protocol
    assert (pr.corpus.thin_words, pr.screening.majority) == (20, 3)   # the newer change went first
    assert runner.invoke(cli_app, ["history", "undo", "--dir", root], env=env).exit_code == 0
    pr = Project.load(p.root).config.protocol
    assert (pr.corpus.thin_words, pr.screening.majority) == (20, 2)


def test_cli_undo_takes_only_the_named_reviewers_change(tmp_path):
    """Scenario: The CLI undoes only the current reviewer's change (and whose change it is without --author)."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:3])
    root = str(p.root)
    _ben = lambda rid: p.store.one("SELECT decision FROM labels WHERE record_id=? AND reviewer_id='ben'", (rid,))  # noqa: E731
    label(p, "dev", ids[0], "ana", "include")
    ana_change = journal.last_change_id()
    label(p, "dev", ids[1], "ben", "exclude", reason="R1")       # ben's label is the newest change
    ben_change = journal.last_change_id()
    # WHEN `wider history undo --author ana` runs (here from a shell whose $WIDER_REVIEWER is ben: --author wins)
    r = runner.invoke(cli_app, ["history", "undo", "--author", "ana", "--dir", root], env={"COLUMNS": "200", "WIDER_REVIEWER": "ben"})
    assert r.exit_code == 0 and "undo: ana labelled record" in r.output
    # THEN ana's label is undone ...
    assert _label(p, ids[0]) is None
    by_id = {c["change_id"]: c for c in journal.changes(p)}
    undo_id = by_id[ana_change]["undone_by"]
    assert undo_id and f"change {undo_id}:" in r.output and by_id[undo_id]["record_id"] == ids[0]
    # ... and ben's label stays: the row, and his change is not marked undone
    assert dict(_ben(ids[1])) == {"decision": "exclude"} and by_id[ben_change]["undone_by"] is None
    assert p.store.scalar("SELECT COUNT(*) FROM labels") == 1
    # AND without --author the CLI uses $WIDER_REVIEWER ...
    label(p, "dev", ids[2], "ana", "include")
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env={"COLUMNS": "200", "WIDER_REVIEWER": "ben", "USER": "ana"})
    assert r.exit_code == 0 and "undo: ben labelled record" in r.output
    assert _ben(ids[1]) is None and _label(p, ids[2])["decision"] == "include"
    # ... and never the login name ($USER): without a name it is refused and nothing is undone
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env={"COLUMNS": "200", "WIDER_REVIEWER": None, "USER": "ana"})
    assert r.exit_code == 1 and "whose change? give --author or set $WIDER_REVIEWER" in r.output
    assert _label(p, ids[2])["decision"] == "include"
    r = runner.invoke(cli_app, ["history", "redo", "--dir", root], env={"COLUMNS": "200", "WIDER_REVIEWER": None, "USER": "ben"})
    assert r.exit_code == 1 and "whose change? give --author or set $WIDER_REVIEWER" in r.output
    assert _ben(ids[1]) is None
    r = runner.invoke(cli_app, ["history", "undo", "--dir", root], env={"COLUMNS": "200", "WIDER_REVIEWER": "carla"})
    assert r.exit_code == 1 and "nothing to undo by carla" in r.output


def test_dismiss_is_journalled_and_undone(tmp_path):
    p = Project.init(tmp_path / "r")
    qid = qs.raise_question(p, "seed_unresolved", "B9", "Seed", "No abstract.", ref="B9")
    qs.dismiss(p, qid, by="ana")
    ch = journal.changes(p)[0]
    assert ch["kind"] == "dismiss" and ch["kind_label"] == "dismissed question" and ch["author"] == "ana"
    journal.undo_last(p, "ana")
    q = qs.get(p, qid)
    assert q["status"] == "open" and q["answered_by"] is None and q["answered_at"] is None
    with pytest.raises(ValueError, match="already dismissed"):
        qs.dismiss(p, qs.dismiss(p, qid, by="ana")["question_id"], by="ana")
    assert len(journal.changes(p, kind="dismiss")) == 2   # the refused dismissal recorded nothing


def test_static_history(tmp_path):
    c = TestClient(create_app(Project.init(tmp_path / "r")))
    html = c.get("/").text
    for needle in ['id="page-history"', 'id="hist-table"', 'id="art-restore"', "/static/history.js", 'data-key="history"']:
        assert needle in html, needle
    js = c.get("/static/app.js").text
    assert "X-Wider-Author" in js
    hj = c.get("/static/history.js").text
    assert "undo-last" in hj and "offerUndo" in hj and 'e.key.toLowerCase() !== "z"' in hj


def test_settings_summary_names_the_edit_not_an_older_file_layout(tmp_path):
    # a project.yaml written in the older layout (top-level bands, as the synthetic demo does) is moved into
    # protocol.sampling on its first save; the history row must name the real edit, not "bands → unset"
    import yaml
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    f = p.root / "project.yaml"
    cfg = yaml.safe_load(f.read_text(encoding="utf-8"))
    cfg.pop("protocol", None)
    cfg["bands"] = [{"name": "S1", "start": 1, "end": 8, "n": None}, {"name": "S2", "start": 9, "end": None, "n": 6}]
    f.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    r = runner.invoke(cli_app, ["protocol", "set", "screening.passes", "1", "--dir", str(p.root)], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 0, r.output
    summary = journal.changes(p)[0]["summary"]
    assert summary == "protocol.screening.passes: 2 → 1", summary
    c = TestClient(create_app(Project.load(p.root)))
    c.patch("/api/project", json={"title": "Voice review"}, headers={"X-Wider-Author": "ben"})
    assert journal.changes(p)[0]["summary"] == "title: '' → 'Voice review'"
    assert Project.load(p.root).config.protocol.sampling.bands[0].end == 8


def test_a_name_in_any_script_is_recorded_as_typed(tmp_path):
    # a header holds Latin-1 only, so the page percent-encodes the name and the server decodes it
    from urllib.parse import quote
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    c = TestClient(create_app(Project.load(p.root)))
    for i, name in enumerate(["Łucja", "مانا", "ana"]):
        r = c.patch("/api/project", json={"title": f"Voice review {i}"}, headers={"X-Wider-Author": quote(name)})
        assert r.status_code == 200
        assert journal.changes(p)[0]["author"] == name
    # no header: no name (never the computer's user name)
    c.patch("/api/project", json={"title": "Voice review 9"})
    assert journal.changes(p)[0]["author"] == ""
    # a hand-made header with broken percent-encoding is no name; control characters are dropped (tenth round)
    c.patch("/api/project", json={"title": "Voice review 10"}, headers={"X-Wider-Author": "%E0%A4%A"})
    assert journal.changes(p)[0]["author"] == ""
    c.patch("/api/project", json={"title": "Voice review 11"}, headers={"X-Wider-Author": "a%00b%0A"})
    assert journal.changes(p)[0]["author"] == "ab"


def test_a_long_name_is_kept_to_80_characters_everywhere(tmp_path):
    # the name in the request body and the header are both cut, so the history matches the stored note and tag
    from urllib.parse import quote
    p, ids = _deduped(tmp_path)
    c = TestClient(create_app(p))
    long_name = "reviewer " + "x" * 96
    hdr = {"X-Wider-Author": quote(long_name)}
    assert c.post(f"/api/records/{ids[0]}/tags", json={"author": long_name, "tag": "check"}, headers=hdr).status_code == 200
    assert c.post(f"/api/records/{ids[0]}/notes", json={"author": long_name, "text": "a note"}, headers=hdr).status_code == 200
    assert [ch["author"] for ch in journal.changes(p)[:2]] == [long_name[:80]] * 2
    rec = ann.for_record(p, ids[0])
    assert rec["notes"][0]["author"] == rec["tags"][0]["author"] == long_name[:80]
    # a duplicate-pair decision: the pair row and its history entry carry the same 80 characters
    a, b = [r["raw_id"] for r in p.store.query("SELECT raw_id FROM raw_records ORDER BY raw_id LIMIT 2")]
    assert c.post("/api/dedup/decide", json={"raw_a": a, "raw_b": b, "decision": "uncertain", "by": long_name},
                  headers=hdr).status_code == 200
    assert journal.changes(p)[0]["author"] == long_name[:80]
    assert p.store.scalar("SELECT decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b)) == long_name[:80]
    # the drawer's blind history and "undo my last change" send the whole name; the routes cut it the same way, so
    # the reviewer's own changes are found (they once showed "no changes yet"; seventh round, keyboard-only tester)
    mine = c.get("/api/history", params={"record_id": ids[0], "blind": "true", "author": long_name}).json()
    assert {ch["author"] for ch in mine} == {long_name[:80]} and len(mine) >= 2
    assert len(c.get("/api/history", params={"author": "  " + long_name}).json()) == 3
    newest = journal.changes(p)[0]   # the pair decision, the reviewer's last change
    r = c.post("/api/history/undo-last", params={"author": long_name})
    assert r.status_code == 200, r.text
    after = {ch["change_id"]: ch for ch in journal.changes(p)}
    assert after[newest["change_id"]]["undone_by"] and newest["kind"] == "dedup_decision"
    assert p.store.scalar("SELECT decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b)) != long_name[:80]
    assert [ch["undone_by"] for ch in journal.changes(p, record_id=ids[0])] == [None, None]   # the note and tag stay


def test_a_pair_decision_is_signed_with_the_history_author(tmp_path, monkeypatch):
    # without a name in the call, the pair row takes the name its history entry gets (header on the page,
    # $WIDER_REVIEWER or $USER on the command line), never an empty one beside a signed history entry
    from urllib.parse import quote
    p, _ = _deduped(tmp_path)
    a, b, c3 = [r["raw_id"] for r in p.store.query("SELECT raw_id FROM raw_records ORDER BY raw_id LIMIT 3")]
    monkeypatch.delenv("WIDER_REVIEWER", raising=False)
    monkeypatch.setenv("USER", "cli-reviewer")
    decide(p, a, b, "uncertain")
    assert journal.changes(p)[0]["author"] == "cli-reviewer"
    assert p.store.scalar("SELECT decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b)) == "cli-reviewer"
    c = TestClient(create_app(p))
    assert c.post("/api/dedup/decide", json={"raw_a": a, "raw_b": c3, "decision": "not_duplicate"},
                  headers={"X-Wider-Author": quote("Łucja")}).status_code == 200
    assert journal.changes(p)[0]["author"] == "Łucja"
    lo, hi = min(a, c3), max(a, c3)
    assert p.store.scalar("SELECT decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (lo, hi)) == "Łucja"


def test_a_pair_decision_on_the_command_line_is_signed_with_user(tmp_path):
    # `wider dedup decide` without --by: the pair table and the history entry carry the same name, taken from $USER
    p, _ = _deduped(tmp_path)
    a, b = [r["raw_id"] for r in p.store.query("SELECT raw_id FROM raw_records ORDER BY raw_id LIMIT 2")]
    res = CliRunner().invoke(cli_app, ["dedup", "decide", str(a), str(b), "duplicate", "--dir", str(p.root)],
                             env={"USER": "cli-reviewer", "WIDER_REVIEWER": ""})
    assert res.exit_code == 0, res.output
    p2 = Project.load(p.root)
    assert p2.store.scalar("SELECT decided_by FROM dedup_pairs WHERE raw_a=? AND raw_b=?", (a, b)) == "cli-reviewer"
    assert journal.changes(p2)[0]["author"] == "cli-reviewer"


def test_same_label_a_second_later_is_no_change(tmp_path, monkeypatch):
    """A label saved again with the same value records nothing even when the clock has passed a second (CI failed
    test_label_undo_redo_and_changed_since on a slower runner), and it does not block undoing the first save."""
    import wider.sample.labels as labels_mod
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1])
    monkeypatch.setattr(labels_mod, "utcnow", lambda: "2026-01-01T00:00:00+00:00")
    label(p, "dev", ids[0], "ana", "include")
    c1 = journal.last_change_id()
    monkeypatch.setattr(labels_mod, "utcnow", lambda: "2026-01-01T00:00:05+00:00")
    label(p, "dev", ids[0], "ana", "include")
    hist = journal.changes(p)
    assert hist[0]["change_id"] == c1 and hist[0]["undoable"], hist[0]
    journal.undo(p, c1, other=True)
    assert _label(p, ids[0]) is None


def test_unlock_is_final_and_refinement_changes_undo(tmp_path):
    """Query refinement (2a, D2): an unlock is listed as "query unlocked", is never undone (page and CLI) and is
    filtered by its kind; the freeze it reopened stays on record. A file kept out of deduplication undoes like any
    change. The refinement half (rounds, accepted edits) comes with phase B."""
    p = Project.init(tmp_path / "r")
    p.write_benchmark([BenchmarkPaper(benchmark_id="V1", title="A validation paper", set="Validation")])
    syn = {"blocks": [{"key": "a", "name": "Meetings", "terms": ["meeting*"]}], "logic": {"and": ["a"]}}
    artifacts.save_new_version(p, "synonyms", syn, origin="manual")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    artifacts.unlock(p, "synonyms", 1, by="ana", seen_choice="count")
    ch = journal.changes(p, "artifact_unlock")
    assert [(c["summary"], c["kind_label"], c["author"], c["undoable"], c["why_not"]) for c in ch] == [
        ("query unlocked: v1 copied into v2 (counted as seen)", "query unlocked", "ana", False, "an unlock is final")]
    c = TestClient(create_app(p))
    assert c.post(f"/api/history/{ch[0]['change_id']}/undo").status_code == 409
    r = CliRunner().invoke(cli_app, ["history", "undo", str(ch[0]["change_id"]), "--dir", str(p.root)])
    assert r.exit_code == 1 and "an unlock is final" in r.output, r.output
    assert len(artifacts.list_versions(p, "synonyms")) == 2 and len(journal.changes(p, "artifact_unlock")) == 1
    r = CliRunner().invoke(cli_app, ["history", "list", "--kind", "artifact_unlock", "--dir", str(p.root)],
                           env={"COLUMNS": "200"})
    assert r.exit_code == 0 and "query unlocked: v1 copied into v2 (counted as seen)" in r.output, r.output
    hist = c.get("/api/history", params={"kind": "artifact_unlock"}).json()
    assert [h["summary"] for h in hist] == ["query unlocked: v1 copied into v2 (counted as seen)"]
    assert journal.changes(p, "artifact_status")[0]["summary"] == "synonyms v1: draft → frozen"
    # a file kept out of deduplication (D5) is an ordinary change: undone on the page, the file enters again
    from tests.unit.test_ingest import _two_files
    from wider.ingest.importer import list_sources, set_included

    q = _two_files(tmp_path, "files")
    cid = set_included(q, 2, False, "ana")["change_id"]
    entry = next(h for h in journal.changes(q, "source_included") if h["change_id"] == cid)
    assert (entry["summary"], entry["kind_label"], entry["undoable"]) == (
        "file 2 (b.ris) kept out of deduplication", "file in deduplication", True)
    c = TestClient(create_app(q))
    assert c.post(f"/api/history/{cid}/undo?other=true", headers={"X-Wider-Author": "tester"}).status_code == 200
    assert [s["included"] for s in list_sources(Project.load(q.root))["sources"]] == [True, True]
    # and from the CLI
    cid = set_included(q, "b.ris", False, "ana")["change_id"]
    r = CliRunner().invoke(cli_app, ["history", "undo", str(cid), "--dir", str(q.root)], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 0, r.output
    assert [s["included"] for s in list_sources(Project.load(q.root))["sources"]] == [True, True]
    # a cluster mark (D9) is undone from the CLI; the History page's "Show" select lists "cluster marked"
    from tests.unit.test_diagnostics import ONE, ROWS, _imported
    from wider.search import diagnostics

    m = _imported(tmp_path, "marks", ONE, [BenchmarkPaper(benchmark_id="B001", title="x", set="Development")], ROWS)
    cid = diagnostics.mark_cluster(m, "C1", "noise", by="ana")["change_id"]
    entry = next(h for h in journal.changes(m, "cluster_mark") if h["change_id"] == cid)
    assert (entry["kind_label"], entry["author"], entry["undoable"]) == ("cluster marked", "ana", True)
    r = CliRunner().invoke(cli_app, ["history", "undo", str(cid), "--dir", str(m.root)], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 0, r.output
    assert m.store.query("SELECT COUNT(*) AS n FROM cluster_marks")[0]["n"] == 0
    # platform limits (D10) undo from the CLI like any change
    from wider.search import limits

    cid = limits.set_limits(m, "scopus", dates="2010..2024", by="ana")["change_id"]
    entry = next(h for h in journal.changes(m, "platform_limits") if h["change_id"] == cid)
    assert (entry["kind_label"], entry["author"], entry["undoable"]) == ("platform limits", "ana", True)
    r = CliRunner().invoke(cli_app, ["history", "undo", str(cid), "--dir", str(m.root)], env={"WIDER_REVIEWER": "ana"})
    assert r.exit_code == 0, r.output
    assert limits.limits(Project.load(m.root))["scopus"] == {}
    show = [k["label"] for k in journal.kinds()]   # the Show list is built from these (manual-review 6.3)
    for kind in ("query unlocked", "uploads reopened", "cluster marked", "platform limits"):
        assert kind in show
    # rounds (2a phase B, D8): a typed count and an acceptance undo like any change; the earlier round is accepted again
    from wider.search import rounds

    w = Project.init(tmp_path / "rounds", platforms=["scopus"])
    w.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Team meetings", set="Development")])
    for terms in (["meeting*"], ["meeting*", "huddle*"]):
        artifacts.save_new_version(w, "synonyms", {**syn, "blocks": [dict(syn["blocks"][0], terms=terms)]}, origin="manual")
    c = TestClient(create_app(w))
    cid = c.post("/api/query/rounds/2/count", json={"platform": "scopus", "n": "1,840"}).json()["change_id"]
    rounds.accept(w, 1, by="ana")
    acc = c.post("/api/query/rounds/2/accept").json()["change_id"]
    hist = c.get("/api/history", params={"kind": "round_accept"}).json()
    assert [(h["summary"], h["kind_label"], h["undoable"]) for h in hist] == [
        ("round v2 accepted", "round accepted", True), ("round v1 accepted", "round accepted", True)]
    assert c.post(f"/api/history/{acc}/undo").status_code == 200
    assert rounds.accepted_round(Project.load(w.root)) == 1
    assert next(r for r in c.get("/api/query/rounds").json()["rounds"] if r["round"] == 1)["mark"] == "accepted"
    r = CliRunner().invoke(cli_app, ["history", "undo", str(cid), "--dir", str(w.root)])
    assert r.exit_code == 0, r.output
    row = next(r for r in c.get("/api/query/rounds").json()["rounds"] if r["round"] == 2)
    assert row["platforms"] == [] or row["platforms"][0]["count"] is None
    assert journal.changes(w, "round_count")[0]["kind_label"] == "round count"
    shown = journal.kinds()   # the History page's Show list is built from these
    assert {"kind": "round_accept", "label": "round accepted"} in shown and {"kind": "round_count", "label": "round count"} in shown


def _final_with_shown(tmp_path, name="r"):
    """One file imported, Validation papers B002 and B003 (none retrieved), the uploads marked final on the page."""
    from tests.unit.test_stage_pages import RIS

    p = Project.init(tmp_path / name, platforms=["scopus"])
    (tmp_path / f"{name}.ris").write_text(RIS)
    runner.invoke(cli_app, ["import", "add", "scopus", str(tmp_path / f"{name}.ris"), "--dir", str(p.root)])
    runner.invoke(cli_app, ["import", "run", "--dir", str(p.root)])
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="x", doi="10.1000/abc", set="Development"),
                       BenchmarkPaper(benchmark_id="B002", title="Validation paper two", doi="10.1000/v2", set="Validation"),
                       BenchmarkPaper(benchmark_id="B003", title="Validation paper three", doi="10.1000/v3", set="Validation")])
    c = TestClient(create_app(Project.load(p.root)))
    r = c.post("/api/import/final")
    assert r.status_code == 200, r.text
    return p, c, r.json()["change_id"], r.json()["at"]


SEEN_NOTICE = artifacts.outcome_line("count", 2)


def test_undo_of_final_mark_records_what_was_seen(tmp_path):
    from wider.search.coverage import unlocks, validation_papers

    p, c, cid, at = _final_with_shown(tmp_path)
    entry = next(h for h in c.get("/api/history").json() if h["change_id"] == cid)
    assert entry["undoable"] and entry["undo_choice"] is True and entry["undo_notice"].endswith(artifacts.CHOICE_TAIL)
    r = c.post(f"/api/history/{cid}/undo?seen_choice=count")
    assert r.status_code == 200 and r.json()["notice"] == SEEN_NOTICE, r.text
    q = Project.load(p.root)
    u = unlocks(q)
    assert [(x["cause"], x["counts_as_seen"], x["final_before"]) for x in u] == [("uploads", 1, at)]
    assert {s["benchmark_id"] for s in u[0]["seen"]} == {"B001", "B002", "B003"}
    ch = journal.changes(q, "uploads_reopened")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("uploads reopened: final mark undone (counted as seen)", False)]
    assert q.store.get_meta("uploads_final_at") is None and validation_papers(q) == []
    # the command line twin prints the same notice
    p2, _, cid2, _ = _final_with_shown(tmp_path, "cli")
    out = runner.invoke(cli_app, ["history", "undo", str(cid2), "--count-seen", "--dir", str(p2.root)], env={"COLUMNS": "300"})
    assert out.exit_code == 0 and SEEN_NOTICE in " ".join(out.output.split()), out.output
    assert [x["cause"] for x in unlocks(Project.load(p2.root))] == ["uploads"]
    from tests.unit.test_outputs import unlock_json
    assert [(x["cause"], x["counts_as_seen"], x["final_before"]) for x in unlock_json(q)] == [("uploads", 1, at)]


def test_undo_of_final_mark_under_frozen_query_asks(tmp_path):
    from tests.unit.test_stage_pages import _frozen_and_final
    from wider.ingest.importer import final_at
    from wider.search.coverage import unlocks, validation_papers

    p = _frozen_and_final(tmp_path)
    c = TestClient(create_app(Project.load(p.root)))
    cid = journal.changes(p, "uploads_final")[0]["change_id"]
    day = final_at(p)[:10]
    ask = (f"The uploads were marked final on {day} and the frozen query v3 stands; undoing the mark reopens them; "
           f"2 validation papers have been shown. {artifacts.CHOICE_TAIL}")
    entry = next(h for h in c.get("/api/history").json() if h["change_id"] == cid)
    assert entry["undo_choice"] is True and entry["undo_notice"] == ask
    # without a choice nothing is undone: the route answers 400, the command exits 2; the mark stays
    me = {"X-Wider-Author": entry["author"]}   # the page signs as the mark's author: asked about the papers only
    r = c.post(f"/api/history/{cid}/undo", headers=me)
    assert r.status_code == 400 and r.json()["detail"] == ask
    d = ["--dir", str(p.root)]
    out = runner.invoke(cli_app, ["history", "undo", str(cid), *d], env={"COLUMNS": "300", "WIDER_REVIEWER": entry["author"]})
    flat = " ".join(out.output.split())
    assert out.exit_code == 2 and ask in flat and artifacts.CHOOSE_LINE in flat, out.output
    out = runner.invoke(cli_app, ["history", "undo", str(cid), "--count-seen", "--keep-validation", *d])
    assert out.exit_code == 2 and artifacts.CHOOSE_LINE in " ".join(out.output.split())
    q = Project.load(p.root)
    assert final_at(q) and unlocks(q) == [] and journal.changes(q, "undo") == []
    # --count-seen
    out = runner.invoke(cli_app, ["history", "undo", str(cid), "--count-seen", *d], env={"COLUMNS": "300", "WIDER_REVIEWER": entry["author"]})
    assert out.exit_code == 0, out.output
    assert ("the frozen query v3 still stands. Counted as seen: the 2 validation papers shown so far no longer "
            "validate the search.") in " ".join(out.output.split())
    q = Project.load(p.root)
    assert [(x["cause"], x["counts_as_seen"]) for x in unlocks(q)] == [("uploads", 1)]
    assert journal.changes(q, "uploads_reopened")[0]["summary"] == "uploads reopened: final mark undone (counted as seen)"
    assert validation_papers(q) == []
    from tests.unit.test_dry_run import _labels
    from wider.search.coverage import check
    assert {k for k, v in _labels(check(q)).items() if v == "used"} == {"B002", "B003"}   # "Validation, used"
    # Keep them as validation (the History page)
    k = _frozen_and_final(tmp_path, "k")
    ck = TestClient(create_app(Project.load(k.root)))
    kid = journal.changes(k, "uploads_final")[0]["change_id"]
    r = ck.post(f"/api/history/{kid}/undo?seen_choice=keep",
                headers={"X-Wider-Author": journal.changes(k, "uploads_final")[0]["author"]})
    assert r.status_code == 200, r.text
    assert r.json()["notice"] == ("the frozen query v3 still stands. Kept as validation: the 2 validation papers shown "
                                  "so far still count; every output states this choice.")
    q = Project.load(k.root)
    assert [(x["cause"], x["counts_as_seen"]) for x in unlocks(q)] == [("uploads", 0)]
    assert journal.changes(q, "uploads_reopened")[0]["summary"] == "uploads reopened: final mark undone (kept as validation)"
    assert {b.benchmark_id for b in validation_papers(q)} == {"B002", "B003"}
    # a bad value is refused, nothing undone
    m = _frozen_and_final(tmp_path, "m")
    mid = journal.changes(m, "uploads_final")[0]["change_id"]
    r = TestClient(create_app(Project.load(m.root))).post(f"/api/history/{mid}/undo?seen_choice=maybe")
    assert r.status_code == 400 and unlocks(Project.load(m.root)) == [] and final_at(Project.load(m.root))


def test_admissions_and_stream_labels_undo(tmp_path):
    """2b: an admitted name (with a note) and a stream label are journalled and undone; the History "Show" select
    lists both kinds."""
    from tests.fixtures.seeds.make_synthetic_pdf import HEADER, make_pdf
    from tests.unit.test_seed_mining import STREAM_REFS, _with_query
    from wider.seeds import names, pdf, streams

    p = _with_query(tmp_path)
    pdf.add(p, "B002", make_pdf(tmp_path / "refs.pdf", pages=([HEADER, "References", *STREAM_REFS],)))
    pdf.extract_all(p, "B002")
    env = {"COLUMNS": "200"}
    # the CLI twins sign with --by (risk-checker R3)
    r = runner.invoke(cli_app, ["seeds", "names", "--admit", "phase binding", "--block", "a", "--note", "used in field B",
                                "--by", "ana", "--dir", str(p.root)], env=env)
    assert r.exit_code == 0 and "synonyms v2 saved" in r.output, r.output
    r = runner.invoke(cli_app, ["seeds", "streams", "--label", "Journal of Synthetic Signals", "psychology", "--by", "bo",
                                "--dir", str(p.root)], env=env)
    assert r.exit_code == 0, r.output
    assert [v["by"] for v in streams.labels(p).values()] == ["bo"]
    out = runner.invoke(cli_app, ["history", "list", "--kind", "name_admit", "--dir", str(p.root)], env=env).output
    assert 'name "phase binding" admitted to block a (synonyms v1)' in out, out
    out = runner.invoke(cli_app, ["history", "list", "--kind", "stream_label", "--dir", str(p.root)], env=env).output
    assert "journal of synthetic signals" in out.lower() and "phase binding" not in out, out
    lab = journal.changes(p, "stream_label")[0]
    assert lab["kind_label"] == "stream labelled" and lab["undoable"] and lab["author"] == "bo"
    adm = journal.changes(p, "name_admit")[0]
    assert adm["kind_label"] == "name admitted" and adm["undoable"] and adm["author"] == "ana"
    for cid in (lab["change_id"], adm["change_id"]):
        res = runner.invoke(cli_app, ["history", "undo", str(cid), "--other", "--author", "di", "--dir", str(p.root)])
        assert res.exit_code == 0, res.output
    p = Project.load(p.root)
    vs = runner.invoke(cli_app, ["construct", "list", "--dir", str(p.root)], env=env).output
    syn = [line for line in vs.splitlines() if "synonyms" in line]
    assert len(syn) == 1 and " v1 " in syn[0] and "admit" not in syn[0], vs
    assert (p.artifact_dir("synonyms") / "v2.yaml").exists()   # its file stays
    assert streams.labels(p) == {} and names.admitted_names(p) == []
    shown = journal.kinds()   # the History page's Show list is built from these
    assert {"kind": "name_admit", "label": "name admitted"} in shown
    assert {"kind": "stream_label", "label": "stream labelled"} in shown


# ------------------------------------------------------------------ redo-last (accessibility-and-naming D4)

def test_redo_last_walks_back_through_undos(tmp_path):
    """Two changes by ana, both undone (the newer first): redo-last puts back the one undone last (the older change),
    then the other; a third call says "nothing to redo by ana" and re-undoes nothing. The CLI twin does the same."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    label(p, "dev", ids[0], "ana", "include")                     # change 4 in the plan's words
    label(p, "dev", ids[1], "ana", "exclude", reason="R1")        # change 5
    for _ in range(2):
        c.post("/api/history/undo-last?author=ana")
    assert _label(p, ids[0]) is None and _label(p, ids[1]) is None
    r = c.post("/api/history/redo-last?author=ana")
    assert r.status_code == 200 and r.json()["summary"].startswith("redo: ana labelled record")
    assert _label(p, ids[0])["decision"] == "include" and _label(p, ids[1]) is None   # the older change first
    out = runner.invoke(cli_app, ["history", "redo", "--author", "ana", "--dir", str(p.root)])
    assert out.exit_code == 0 and "redo: ana labelled record" in out.output
    assert _label(p, ids[1])["decision"] == "exclude"
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    r = c.post("/api/history/redo-last?author=ana")
    assert r.status_code == 409 and r.json()["detail"] == "nothing to redo by ana"
    assert p.store.scalar("SELECT COUNT(*) FROM history") == n and _label(p, ids[0]) and _label(p, ids[1])


def test_redo_stops_after_a_new_change(tmp_path):
    """An undo older than the name's newest ordinary change is not redone: after a new label, redo-last finds nothing
    and the undone label stays undone."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    label(p, "dev", ids[0], "ana", "include")
    journal.undo_last(p, "ana")
    label(p, "dev", ids[1], "ana", "exclude", reason="R1")
    with pytest.raises(ValueError, match="nothing to redo by ana"):
        journal.redo_last(p, "ana")
    assert _label(p, ids[0]) is None and _label(p, ids[1])["decision"] == "exclude"


def test_redo_last_needs_a_name(tmp_path):
    """Without a name the route answers 400 and nothing changes; the page's name header signs instead of ?author=."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:2])
    c = TestClient(create_app(p))
    label(p, "dev", ids[0], "ana", "include")
    journal.undo_last(p, "ana")
    n = p.store.scalar("SELECT COUNT(*) FROM history")
    r = c.post("/api/history/redo-last")
    assert r.status_code == 400 and r.json()["detail"] == "redo-last needs the reviewer's name"
    assert c.post("/api/history/redo-last?author=%20").status_code == 400
    out = runner.invoke(cli_app, ["history", "redo", "--dir", str(p.root)], env={"COLUMNS": "200", "WIDER_REVIEWER": "", "USER": ""})
    assert out.exit_code == 1 and "whose change? give --author or set $WIDER_REVIEWER" in out.output
    assert _label(p, ids[0]) is None and p.store.scalar("SELECT COUNT(*) FROM history") == n
    with pytest.raises(ValueError, match="redo-last needs the reviewer's name"):
        journal.redo_last(p, " ")
    r = c.post("/api/history/redo-last", headers={"X-Wider-Author": "ana"})
    assert r.status_code == 200 and _label(p, ids[0])["decision"] == "include" and journal.changes(p)[0]["author"] == "ana"


def test_redo_last_refused_after_a_later_change(tmp_path):
    """ana's undone setting cannot come back while ben's later change to the same file stands: the route answers 409
    naming ben's change, the CLI exits 1 with the same words, and the setting stays as ben set it."""
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    root, ana, ben = str(p.root), {"COLUMNS": "200", "WIDER_REVIEWER": "ana"}, {"COLUMNS": "200", "WIDER_REVIEWER": "ben"}
    runner.invoke(cli_app, ["protocol", "set", "screening.majority", "3", "--dir", root], env=ana)
    assert runner.invoke(cli_app, ["history", "undo", "--dir", root], env=ana).exit_code == 0
    runner.invoke(cli_app, ["protocol", "set", "screening.majority", "1", "--dir", root], env=ben)
    bid = journal.changes(p)[0]["change_id"]
    r = runner.invoke(cli_app, ["history", "redo", "--dir", root], env=ana)
    assert r.exit_code == 1 and f"blocked by change {bid} by ben" in " ".join(r.output.split())
    r = TestClient(create_app(Project.load(p.root))).post("/api/history/redo-last?author=ana")
    assert r.status_code == 409 and "cannot be redone" in r.json()["detail"] and f"blocked by change {bid} by ben" in r.json()["detail"]
    assert Project.load(p.root).config.protocol.screening.majority == 1


def test_blind_history_hides_the_sample_of_a_labelled_record(tmp_path):
    """Owner answer 37e6 (b): while Blind is on, History says "in a sample", so a blind reader cannot tell
    development from validation records; the stored entry keeps its words (page, API and `wider history list --blind`)."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1], name="development")
    _sample(p, ids[1:2], name="validation")
    label(p, "development", ids[0], "ana", "include")
    label(p, "validation", ids[1], "ben", "exclude")
    label(p, "validation", ids[1], "di", "include")   # an adjudication needs a disagreement
    adjudicate(p, "validation", ids[1], "include", by="cy")
    shown = [c["summary"] for c in journal.changes(p, hide_samples=True)]
    assert f"ana labelled record {ids[0]} in a sample" in shown and f"ben labelled record {ids[1]} in a sample" in shown
    assert not any("development" in s or "validation" in s for s in shown)
    assert any(f"record {ids[1]} in a sample: include" in s for s in shown)
    assert f"ana labelled record {ids[0]} in sample development" in [c["summary"] for c in journal.changes(p)]
    c = TestClient(create_app(p))
    assert not any("validation" in r["summary"] for r in c.get("/api/history?hide_samples=true").json())
    assert any("validation" in r["summary"] for r in c.get("/api/history").json())
    assert not any("development" in r["summary"] for r in c.get("/api/history?blind=true&author=ana").json())
    root, env = str(p.root), {"COLUMNS": "200"}
    r = runner.invoke(cli_app, ["history", "list", "--blind", "--dir", root], env=env)
    assert r.exit_code == 0 and "in a sample" in r.output and "validation" not in r.output and "development" not in r.output
    r = runner.invoke(cli_app, ["history", "list", "--dir", root], env=env)
    assert "in sample validation" in r.output
    # a locked sample: the earlier label's "cannot undo" reason and a correction name no sample either
    lock(p, "validation", by="cy")
    label(p, "validation", ids[1], "ben", "include", correction_reason="misread the abstract")
    rows = journal.changes(p, hide_samples=True)
    words = " ".join(f"{c['summary']} {c['why_not'] or ''}" for c in rows if c.get("record_id") is not None)
    assert f"ben corrected record {ids[1]} in a sample" in words and "a sample was locked after this change" in words
    assert "validation" not in words and "development" not in words
    assert "'validation' was locked" in " ".join(c["why_not"] or "" for c in journal.changes(p))
    # undo and redo answers while Blind is on (page and CLI)
    u = c.post("/api/history/undo-last?author=ana&hide_samples=true").json()
    assert u["summary"].startswith(f"undo: ana labelled record {ids[0]} in a sample") and "development" not in u["summary"]
    r = runner.invoke(cli_app, ["history", "redo", "--author", "ana", "--blind", "--dir", root], env=env)
    assert r.exit_code == 0 and "in a sample" in r.output and "development" not in r.output
    r = runner.invoke(cli_app, ["history", "undo", "--author", "ana", "--blind", "--dir", root], env=env)
    assert r.exit_code == 0 and "in a sample" in r.output and "development" not in r.output


def test_blind_refusal_names_no_sample(tmp_path):
    """risk-checker R2: while Blind is on, an undo or redo that is refused names no sample either (the locked
    sample's label and the change it blocks), on the page (409 detail) and with `--blind` in the CLI."""
    p, ids = _deduped(tmp_path)
    _sample(p, ids[:1], name="development")
    _sample(p, ids[1:2], name="validation")
    label(p, "development", ids[0], "ana", "include")
    label(p, "validation", ids[1], "ben", "exclude")
    lock(p, "validation", by="cy")
    root, env = str(p.root), {"COLUMNS": "300"}
    c = TestClient(create_app(p))
    r = c.post("/api/history/undo-last?author=ben&hide_samples=true")
    assert r.status_code == 409 and "cannot be undone" in r.json()["detail"] and "validation" not in r.json()["detail"]
    assert "validation" in c.post("/api/history/undo-last?author=ben").json()["detail"]   # Blind off: the words stay
    cid = r.json()["detail"].split("last change ")[1].split(" ")[0]
    r = c.post(f"/api/history/{cid}/undo?hide_samples=true", headers={"X-Wider-Author": "ben"})
    assert r.status_code == 409 and "validation" not in r.json()["detail"]
    out = runner.invoke(cli_app, ["history", "undo", "--author", "ben", "--blind", "--dir", root], env=env)
    assert out.exit_code == 1 and "cannot be undone" in out.output and "validation" not in out.output
    out = runner.invoke(cli_app, ["history", "undo", "--author", "ben", "--dir", root], env=env)
    assert "validation" in out.output
