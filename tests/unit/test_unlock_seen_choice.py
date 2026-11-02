"""The researcher decides on every unlock case whether the validation seed papers already shown still count (change
unlock-seen-choice): an unlock, a save after a freeze or while the uploads are final, a file added while they are
final, an undo of the final mark. Synthetic, topic-neutral data only; nothing leaves the computer."""
import json

import pytest
import yaml

from tests.unit.test_dry_run import (
    _bench,
    _changes,
    _cli,
    _client,
    _import_one,
    _labels,
    _proj,
    _snapshot,
    _syn,
    _unlocks,
    _versions,
)
from tests.unit.test_validation_reuse import PAPERS, SYN
from wider.construct import artifacts
from wider.core.project import Project
from wider.search import coverage, diagnostics

TAIL = artifacts.CHOICE_TAIL
KEPT2 = "Kept as validation: the 2 validation papers shown so far still count; every output states this choice."
COUNTED2 = "Counted as seen: the 2 validation papers shown so far no longer validate the search."


def _flat(r) -> str:
    return " ".join(r.output.split())


def _frozen(tmp_path, name, n_val=2, n=3):
    """Synonyms v1..vn saved, vn frozen, V001..V00n_val in Validation, one file imported."""
    p = _proj(tmp_path, n_val=n_val, name=name)
    _import_one(p, tmp_path, f"{name}.ris")
    _versions(p, *[f"t{i}*" for i in range(n)])
    artifacts.set_status(p, "synonyms", n, "frozen")
    return p


def _rows(p):
    return [(x["cause"], x["counts_as_seen"], x["seen_choice"]) for x in _unlocks(Project.load(p.root))]


# ------------------------------------------------------------------------------------------- engine guard (D1-D4)
def test_nothing_shown_nothing_asked(tmp_path):
    """Scenario "Nothing shown, nothing asked": an empty validation set asks nothing and the row counts as before."""
    p = _frozen(tmp_path, "empty", n_val=0, n=1)
    assert not artifacts.unlock_choice(p, 1)
    r = _cli(p, "construct", "unlock", "synonyms", "1")
    assert r.exit_code == 0 and "Unlocking v1 reopens the query; no validation paper has been seen yet." in _flat(r), r.output
    assert _rows(p) == [("unlock", 1, None)]
    artifacts.set_status(Project.load(p.root), "synonyms", 2, "frozen")
    q = Project.load(p.root)
    assert not artifacts.save_choice(q)
    f = tmp_path / "e.yaml"
    f.write_text(yaml.safe_dump(_syn("e*")), encoding="utf-8")
    r = _cli(p, "construct", "save", "synonyms", str(f))
    assert r.exit_code == 0 and "v2 is frozen. Saving v3 reopens the query; no validation paper has been seen yet." in _flat(r), r.output
    assert [c for c, *_ in _rows(p)] == ["unlock", "save"] and _rows(p)[1][2] is None


def test_guard_raises_seen_choice_needed(tmp_path):
    """The engine refuses to reopen without the choice (SeenChoiceNeeded, nothing written); a choice lets it through."""
    p = _frozen(tmp_path, "guard")
    before = _snapshot(p)
    with pytest.raises(artifacts.SeenChoiceNeeded):
        artifacts.record_unlock(p, "unlock", 3, 4)
    with pytest.raises(artifacts.SeenChoiceNeeded) as e:
        artifacts.save_new_version(p, "synonyms", _syn("x*"), origin="manual")
    assert e.value.notice == f"v3 is frozen. Saving v4 reopens the query; 2 validation papers have been shown. {TAIL}"
    with pytest.raises(artifacts.SeenChoiceNeeded):
        artifacts.unlock(p, "synonyms", 3)
    assert _snapshot(p) == before and len(artifacts.list_versions(p, "synonyms")) == 3
    with artifacts.save_seen_choice("keep"):
        row = artifacts.save_new_version(p, "synonyms", _syn("x*"), origin="manual")
    assert row["version"] == 4 and _rows(p) == [("save", 0, "keep")]
    with pytest.raises(ValueError, match="seen_choice must be count or keep"):
        artifacts.seen_choice("maybe")


# ---------------------------------------------------------------------------------------------- the unlock (D3, D5)
def test_unlock_asks_first(tmp_path):
    """Scenarios "Unlock asks first", "Kept as validation" and "Counted as seen" on the CLI."""
    p = _frozen(tmp_path, "ask")
    want = f"Unlocking v3 reopens the query; 2 validation papers have been shown. {TAIL}"
    before = _snapshot(p)
    r = _cli(p, "construct", "unlock", "synonyms", "3")
    assert r.exit_code == 2 and want in _flat(r) and artifacts.CHOOSE_LINE in _flat(r), r.output
    r = _cli(p, "construct", "unlock", "synonyms", "3", "--count-seen", "--keep-validation")
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r), r.output
    assert _snapshot(p) == before and len(artifacts.list_versions(p, "synonyms")) == 3
    r = _cli(p, "construct", "unlock", "synonyms", "3", "--keep-validation")
    assert r.exit_code == 0 and KEPT2 in _flat(r) and TAIL not in _flat(r), r.output
    q = Project.load(p.root)
    assert artifacts.list_versions(q, "synonyms")[3]["status"] == "draft"
    assert _rows(p) == [("unlock", 0, "keep")]
    ch = _changes(q, "artifact_unlock")
    assert ch[0]["summary"] == "query unlocked: v3 copied into v4 (kept as validation)" and ch[0]["why_not"]
    artifacts.set_status(q, "synonyms", 4, "frozen")
    labels = _labels(coverage.check(Project.load(p.root)))
    assert labels["V001"] == labels["V002"] == "Validation", labels
    c = _frozen(tmp_path, "count")
    r = _cli(c, "construct", "unlock", "synonyms", "3", "--count-seen")
    assert r.exit_code == 0 and COUNTED2 in _flat(r), r.output
    assert _rows(c) == [("unlock", 1, "count")]
    assert _changes(Project.load(c.root), "artifact_unlock")[0]["summary"].endswith("(counted as seen)")
    artifacts.set_status(Project.load(c.root), "synonyms", 4, "frozen")
    labels = _labels(coverage.check(Project.load(c.root)))
    assert labels["V001"] == labels["V002"] == "used", labels


def test_unlock_page_asks_first(tmp_path):
    """Scenario "Unlock asks first" on the page: the GET names the choice, a POST without it changes nothing, Cancel
    sends nothing, Keep sends seen_choice."""
    from tests.unit.test_construct_js_node import run_refine

    p = _frozen(tmp_path, "page")
    want = f"Unlocking v3 reopens the query; 2 validation papers have been shown. {TAIL}"
    d = _client(p).get("/api/construct/synonyms").json()
    assert d["unlock_choice"]["3"] is True and d["unlock_notice"]["3"] == want
    before = _snapshot(p)
    r = _client(p).post("/api/construct/unlock", json={"kind": "synonyms", "version": 3})
    assert r.status_code == 400 and r.json()["detail"] == want
    r = _client(p).post("/api/construct/unlock", json={"kind": "synonyms", "version": 3, "seen_choice": "maybe"})
    assert r.status_code == 400
    assert _snapshot(p) == before
    out = run_refine(d, "cancel", "await Refine.unlock(3);")
    assert out["posts"] == [] and 'data-act="count"' in out["html"] and 'data-act="keep"' in out["html"]
    assert 'data-act="cancel"' in out["html"]
    out = run_refine(d, "keep", "await Refine.unlock(3);")
    assert [b for u, *b in out["posts"] if u == "/api/construct/unlock"] == [
        [{"kind": "synonyms", "version": 3, "seen_choice": "keep"}]]
    r = _client(p).post("/api/construct/unlock", json={"kind": "synonyms", "version": 3, "seen_choice": "keep"})
    assert r.status_code == 200 and KEPT2 in r.json()["notice"], r.text
    assert _rows(p) == [("unlock", 0, "keep")]


# ------------------------------------------------------------------------------------ saves after a freeze (D4, D5)
def _bench_frozen(tmp_path, name):
    p = _bench(tmp_path, PAPERS, name=name)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    _import_one(p, tmp_path, f"{name}.ris")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    return Project.load(p.root)


def _fake_provider(p, monkeypatch, built):
    from wider.core.models import ProviderConfig
    from wider.providers import fake as fakemod

    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.save()
    orig = fakemod.FakeProvider.__init__

    def init(self, cfg, responder_=None):
        built.append(1)
        orig(self, cfg, lambda req: _syn("propos*"))
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", init)


SAVE_ASK = f"v1 is frozen. Saving v2 reopens the query; 2 validation papers have been shown. {TAIL}"


def test_save_after_freeze_asks_first(tmp_path, monkeypatch):
    """Scenario "A save after a freeze asks first" on every CLI path that saves a synonyms version: without a flag exit
    2 with the notice and nothing written (the model is not called); with one the row records the choice."""
    f = tmp_path / "v2.yaml"
    f.write_text(yaml.safe_dump(SYN | {"notes": "edited"}), encoding="utf-8")
    (tmp_path / "q.txt").write_text("TI (telework) AND TI (learning)", encoding="utf-8")

    def edit_args(p):
        e = next(e for e in diagnostics.suggested_edits(p)["edits"] if e["label"] == 'add "virtual work" to block a')
        return ["query", "edit", "apply", e["id"]]
    paths = [("save", lambda p: ["construct", "save", "synonyms", str(f)]),
             ("import", lambda p: ["query", "import", str(tmp_path / "q.txt")]),
             ("propose", lambda p: ["construct", "propose", "synonyms", "--provider", "fake"]),
             ("edit", edit_args),
             ("admit", lambda p: ["seeds", "names", "--admit", "virtual work", "--block", "a", "--note", "a synthetic note"])]
    for i, (name, args) in enumerate(paths):
        flag, counts = ("--keep-validation", 0) if i % 2 else ("--count-seen", 1)
        p = _bench_frozen(tmp_path, f"s{name}")
        built = []
        if name == "propose":
            _fake_provider(p, monkeypatch, built)
        a = args(p)
        before = _snapshot(p)
        r = _cli(p, *a)
        assert r.exit_code == 2 and SAVE_ASK in _flat(r) and artifacts.CHOOSE_LINE in _flat(r), (name, r.output)
        assert _snapshot(p) == before and built == [], name
        r = _cli(p, *a, flag)
        assert r.exit_code == 0, (name, r.output)
        assert (KEPT2 if counts == 0 else COUNTED2) in _flat(r), (name, r.output)
        assert _rows(p) == [("save", counts, "keep" if counts == 0 else "count")], name
    # restore: v1 draft, v2 frozen, v1 brought back as v3
    p = _bench(tmp_path, PAPERS, name="srestore")
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    artifacts.save_new_version(p, "synonyms", SYN | {"notes": "two"}, origin="manual")
    artifacts.set_status(p, "synonyms", 2, "frozen")
    before = _snapshot(p)
    r = _cli(p, "construct", "restore", "synonyms", "1")
    want = f"v2 is frozen. Saving v3 reopens the query; 2 validation papers have been shown. {TAIL}"
    assert r.exit_code == 2 and want in _flat(r) and artifacts.CHOOSE_LINE in _flat(r), r.output
    assert _snapshot(p) == before
    assert _client(p).post("/api/construct/synonyms/1/restore").json()["detail"] == want
    r = _client(p).post("/api/construct/synonyms/1/restore?seen_choice=count")
    assert r.status_code == 200 and COUNTED2 in r.json()["notice"], r.text
    assert _rows(p) == [("save", 1, "count")]


def test_answer_after_freeze_asks_first(tmp_path):
    """A question answer that saves a synonyms version asks like every other save."""
    from tests.unit.test_dry_run import _add_val
    from tests.unit.test_interview import _interview, _one

    p = _interview(tmp_path, "ans")
    _add_val(p, "10.1000/val-a")
    _add_val(p, "10.1000/val-b")
    artifacts.set_status(p, "synonyms", 1, "frozen")
    q = _one(Project.load(p.root), "refine_name_add")
    before = _snapshot(p)
    r = _cli(p, "questions", "answer", str(q["question_id"]), "add")
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r) and "v1 is frozen. Saving v2" in _flat(r), r.output
    assert _snapshot(p) == before
    r = _cli(p, "questions", "answer", str(q["question_id"]), "add", "--keep-validation")
    assert r.exit_code == 0 and "Kept as validation" in _flat(r), r.output
    assert [(c, s) for c, _n, s in _rows(p)] == [("save", "keep")]


def test_save_page_asks_first(tmp_path, monkeypatch):
    """The page twin: GET names the choice; each saving route answers 400 with the notice without seen_choice and
    saves with it; the job route refuses before the job starts."""
    p = _bench_frozen(tmp_path, "spage")
    d = _client(p).get("/api/construct/synonyms").json()
    assert d["save_choice"] is True and d["save_notice"] == SAVE_ASK
    edit = next(e for e in diagnostics.suggested_edits(p)["edits"] if e["label"] == 'add "virtual work" to block a')
    built = []
    _fake_provider(p, monkeypatch, built)
    c = _client(p)
    before = _snapshot(p)
    calls = [("post", "/api/construct/synonyms/save", {"text": yaml.safe_dump(SYN | {"notes": "x"})}),
             ("post", "/api/query/import", {"text": "TI (telework) AND TI (learning)"}),
             ("post", "/api/query/edit", {"id": edit["id"], "version": 1}),
             ("post", "/api/seeds/names/admit", {"name": "virtual work", "block": "a", "note": "a synthetic note"}),
             ("post", "/api/jobs/propose/synonyms?provider=fake", None)]
    for _m, url, body in calls:
        r = c.post(url, json=body) if body is not None else c.post(url)
        assert r.status_code == 400 and r.json()["detail"] == SAVE_ASK, (url, r.status_code, r.text)
    assert _snapshot(p) == before and built == []
    r = c.post("/api/construct/synonyms/save", json={"text": yaml.safe_dump(SYN | {"notes": "x"}), "seen_choice": "keep"})
    assert r.status_code == 200 and KEPT2 in r.json()["notice"], r.text
    assert _rows(p) == [("save", 0, "keep")]


def test_confirm_save_returns_the_choice(tmp_path):
    """refine.js confirmSave gives the choice ("count" | "keep") when the save asks, false on Cancel."""
    from tests.unit.test_construct_js_node import run_refine

    p = _bench_frozen(tmp_path, "js")
    d = _client(p).get("/api/construct/synonyms").json()
    out = run_refine(d, "keep", 'calls.push(["/result", "POST", await Refine.confirmSave("synonyms")]);')
    assert ["/result", "keep"] in out["posts"], out
    out = run_refine(d, "cancel", 'calls.push(["/result", "POST", await Refine.confirmSave("synonyms")]);')
    assert ["/result", False] in out["posts"], out


# ------------------------------------------------------------------------------------ uploads final (D3) and undo
def test_save_while_final_asks_first(tmp_path):
    """Scenario "A save while the uploads are final asks first"."""
    from wider.ingest.importer import mark_final

    p = _proj(tmp_path, n_val=3, name="final")
    _import_one(p, tmp_path, "f.ris")
    mark_final(p)
    want = f"The uploads are final. Saving v1 reopens them; 3 validation papers have been shown. {TAIL}"
    f = tmp_path / "f.yaml"
    f.write_text(yaml.safe_dump(_syn("f*")), encoding="utf-8")
    before = _snapshot(p)
    r = _cli(p, "construct", "save", "synonyms", str(f))
    assert r.exit_code == 2 and want in _flat(r), r.output
    assert _snapshot(p) == before
    r = _cli(p, "construct", "save", "synonyms", str(f), "--keep-validation")
    assert r.exit_code == 0 and "Kept as validation: the 3 validation papers" in _flat(r), r.output
    q = Project.load(p.root)
    assert _rows(p) == [("save", 0, "keep")] and not coverage.validation_unlocked(q)


def test_undo_last_of_final_mark_asks(tmp_path):
    """Undoing the uploads-final mark without an id (CLI `history undo`, route undo-last) asks like an undo by id."""
    from wider.ingest.importer import mark_final

    p = _proj(tmp_path, n_val=2, name="ul")
    _import_one(p, tmp_path, "ul.ris")
    mark_final(p, author="ana")
    client = _client(p)
    before = _snapshot(p)
    r = _cli(p, "history", "undo", "--author", "ana")
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r) and TAIL in _flat(r), r.output
    r = client.post("/api/history/undo-last?author=ana")
    assert r.status_code == 400 and TAIL in r.json()["detail"], r.text
    assert _snapshot(p) == before
    r = client.post("/api/history/undo-last?author=ana&seen_choice=keep")
    assert r.status_code == 200, r.text
    assert [(c, s) for c, _n, s in _rows(p)] == [("uploads", "keep")]


# ------------------------------------------------------------------------------------- every output states it (D9)
def test_every_output_states_each_unlock_choice(tmp_path):
    """Scenario "Every output states the choice": unlock.json, the reports, the manifest; old rows add no line."""
    from tests.unit.test_outputs import in_every_output, unlock_json, validity_everywhere

    p = _frozen(tmp_path, "out")
    artifacts.unlock(p, "synonyms", 3, seen_choice="keep")
    artifacts.set_status(Project.load(p.root), "synonyms", 4, "frozen")
    with artifacts.save_seen_choice("keep"):
        artifacts.save_new_version(Project.load(p.root), "synonyms", _syn("z*"), origin="manual")
    q = Project.load(p.root)
    artifacts.set_status(q, "synonyms", 5, "frozen")
    q = Project.load(p.root)
    day = _unlocks(q)[0]["at"][:10]
    texts, note, _zip = validity_everywhere(q)
    in_every_output(texts,
                    f"v3 was unlocked on {day} into v4 (2 validation papers shown); the researcher chose to keep them "
                    "as validation.",
                    f"v5 was saved on {day} after the frozen query v4 (2 validation papers shown); the researcher chose "
                    "to keep them as validation.")
    assert [(x["cause"], x["counts_as_seen"], x["seen_choice"]) for x in unlock_json(q)] == [
        ("unlock", 0, "keep"), ("save", 0, "keep")]
    assert note.get("unlocks_kept") == 2 and [c["seen_choice"] for c in note["choices"]] == ["keep", "keep"], note


# ------------------------------------------------------------------------------------- looks and the unlock (D7, D8)
def test_kept_look_while_reopened_then_revised(tmp_path):
    """Scenario "Peek after an unlock" with the choice: a look while the query is reopened asks; kept, the paper stays
    in Validation, and a later revision records it as used (D10 option a: the validation-reuse rule)."""
    from tests.unit.test_dry_run import _add_val, _reopened

    p = _reopened(tmp_path, "rl")
    b7 = _add_val(p, "10.1000/b7")
    want = f"Validation set requested while the query is reopened; it holds 1 paper. {TAIL}"
    before = _snapshot(p)
    r = _cli(p, "coverage", "--validation")
    assert r.exit_code == 2 and want in _flat(r) and artifacts.CHOOSE_LINE in _flat(r), r.output
    assert _snapshot(p) == before
    r = _cli(p, "coverage", "--validation", "--keep-validation")
    assert r.exit_code == 0, r.output
    assert "validation set shown while the query is reopened; this look is logged in the history" in _flat(r), r.output
    assert "Kept as validation" in _flat(r), r.output
    q = Project.load(p.root)
    e = [x for x in coverage._look_entries(q) if x["benchmark_id"] == b7]
    assert [(x["choice"], x["after_unlock"], x["counted"]) for x in e] == [("keep", 1, False)]
    assert [x["benchmark_id"] for x in coverage.kept_unused_looks(q)] == [b7]
    f = tmp_path / "rl.yaml"
    f.write_text(yaml.safe_dump(_syn("rl*")), encoding="utf-8")
    r = _cli(p, "construct", "save", "synonyms", str(f))
    assert r.exit_code == 2 and artifacts.REVISION_CONFIRM in _flat(r), r.output
    r = _cli(p, "construct", "save", "synonyms", str(f), "--confirm")
    assert r.exit_code == 0, r.output
    assert len(_changes(Project.load(p.root), "validation_used")) == 1


def test_kept_unlock_keeps_the_set(tmp_path):
    """D8: looks before an unlock were settled by that unlock's choice; a save after a kept unlock records nothing as
    used and asks no --confirm."""
    from tests.unit.test_validation_reuse import _looked

    p = _looked(tmp_path, "ku")                    # B002, B003 kept at a look before any freeze
    artifacts.set_status(Project.load(p.root), "synonyms", 3, "frozen")
    r = _cli(p, "construct", "unlock", "synonyms", "3", "--keep-validation")
    assert r.exit_code == 0 and KEPT2 in _flat(r), r.output
    q = Project.load(p.root)
    assert coverage.kept_unused_looks(q) == []
    f = tmp_path / "ku.yaml"
    f.write_text(yaml.safe_dump(SYN | {"notes": "after"}), encoding="utf-8")
    r = _cli(p, "construct", "save", "synonyms", str(f))
    assert r.exit_code == 0 and artifacts.REVISION_CONFIRM not in _flat(r), r.output
    assert _changes(Project.load(p.root), "validation_used") == []
    artifacts.set_status(Project.load(p.root), "synonyms", 5, "frozen")
    labels = _labels(coverage.check(Project.load(p.root)))
    assert labels["B002"] == labels["B003"] == "Validation", labels


def test_old_look_after_keep_unlock_stays_live(tmp_path):
    """D8 for entries written before this change (no after_unlock): judged by time against the latest unlock row."""
    p = _proj(tmp_path, n_val=2, name="old")
    _import_one(p, tmp_path, "old.ris")
    _versions(p, "a*", "b*", "c*")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="keep")
    q = Project.load(p.root)
    old = [{"benchmark_id": "V001", "doi": "10.1000/val1", "title_norm": "", "unlock_no": 0, "counted": False,
            "at": "2000-01-01T00:00:00Z", "look": 1, "kind": "hiding_off"},
           {"benchmark_id": "V002", "doi": "10.1000/val2", "title_norm": "", "unlock_no": 0, "counted": False,
            "at": "9999-01-01T00:00:00Z", "look": 2, "kind": "hiding_off"}]
    q.store.set_meta(coverage.SEEN_BY_LOOK, json.dumps(old))
    assert [e["benchmark_id"] for e in coverage.kept_unused_looks(q)] == ["V002"]


def test_question_answer_page_sends_the_choice(tmp_path):
    """questions.js passes the choice confirmSave returns (count | keep) as seen_choice; Cancel sends nothing."""
    from tests.unit.test_interview import _interview, _one, _page

    p = _interview(tmp_path, "qp")
    listing = {"questions": [_one(p, "refine_noise_term")], "counts": {"open": 1}, "mode": "queue"}
    for ret, want in (('"keep"', [{"seen_choice": "keep"}]), ("false", [])):
        res = _page({"/api/questions": listing, "/api/decisions": {"decisions": [], "lines": []},
                     "/api/questions/summary": {"open": 1, "mode": "queue", "latest_open": 1}},
                    f"window.Refine = {{ confirmSave: async () => {ret} }};\n"
                    "await window.loadQuestions(); await flush();\n"
                    "[...card('#q-open').querySelectorAll('[data-choice]')].find(b => b.dataset.choice === 'drop').onclick();\n"
                    "await flush();\n"
                    "console.log(JSON.stringify(calls.filter(c => c[0].endsWith('/answer')).map(c => c[2])));")
        assert [{k: v for k, v in b.items() if k == "seen_choice"} for b in res] == want, res


# ------------------------------------------------------- no frozen query: uploads and the final mark (D3, spec-checker)
def test_upload_while_final_cli_asks_and_keeps(tmp_path):
    """Scenario "A file added while the uploads are final" with no frozen query, on the command line: `import add` and
    `import run` exit 2 without a flag and copy or import nothing; `--keep-validation` takes the file, the notice says
    "Uploads reopened ..." with the kept outcome, and the row records keep (the papers still count)."""
    from tests.unit.test_stage_pages import RIS, _three_shown
    from tests.unit.test_stage_pages import _cli as cli
    from wider.ingest.importer import final_at, mark_final

    p = _three_shown(tmp_path)
    d = ["--dir", str(p.root)]
    day = final_at(p)[:10]
    n_sources = p.store.scalar("SELECT COUNT(*) FROM sources")
    (tmp_path / "k.ris").write_text(RIS.replace("10.1000/abc", "10.1000/kkk"))
    r = cli("import", "add", "scopus", str(tmp_path / "k.ris"), *d)
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r) and TAIL in _flat(r), r.output
    assert not (p.path("inputs", "scopus") / "k.ris").exists() and _unlocks(Project.load(p.root)) == []
    r = cli("import", "add", "scopus", str(tmp_path / "k.ris"), "--keep-validation", *d)
    assert r.exit_code == 0, r.output
    assert (f"Uploads reopened: k.ris was added after they were marked final on {day}. Earlier imports and decisions "
            f"are kept. {artifacts.outcome_line('keep', 3)}") in _flat(r), r.output
    assert _rows(p) == [("uploads", 0, "keep")]
    q = Project.load(p.root)
    assert {b.benchmark_id for b in coverage.validation_papers(q)} == {"B2", "B3", "B4"}
    assert cli("import", "run", *d).exit_code == 0
    # a file saved straight into inputs/ while final: `import run` asks, imports nothing, then keeps
    mark_final(Project.load(p.root))
    n_sources = Project.load(p.root).store.scalar("SELECT COUNT(*) FROM sources")
    (p.path("inputs", "scopus") / "w.ris").write_text(RIS.replace("10.1000/abc", "10.1000/www"))
    r = cli("import", "run", *d)
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r), r.output
    assert Project.load(p.root).store.scalar("SELECT COUNT(*) FROM sources") == n_sources and len(_rows(p)) == 1
    r = cli("import", "run", "--keep-validation", *d)
    assert r.exit_code == 0 and "Uploads reopened: w.ris was added" in _flat(r), r.output
    assert artifacts.outcome_line("keep", 3) in _flat(r), r.output
    assert _rows(p) == [("uploads", 0, "keep"), ("uploads", 0, "keep")]
    assert Project.load(p.root).store.scalar("SELECT COUNT(*) FROM sources") == n_sources + 1


def test_undo_of_final_mark_cli_asks_and_keeps(tmp_path):
    """Scenario "Undo of the final mark" with no frozen query, on the command line: `history undo <id>` exits 2 without
    a flag and undoes nothing; `--keep-validation` undoes it with the kept notice and the papers still count."""
    from tests.unit.test_history import _final_with_shown, runner
    from wider.cli import app as cli_app
    from wider.core import journal

    p, _c, cid, at = _final_with_shown(tmp_path)
    d = ["--dir", str(p.root)]
    before = _snapshot(p)
    r = runner.invoke(cli_app, ["history", "undo", str(cid), *d], env={"COLUMNS": "300"})
    assert r.exit_code == 2 and artifacts.CHOOSE_LINE in _flat(r) and TAIL in _flat(r), r.output
    assert _snapshot(p) == before and Project.load(p.root).store.get_meta("uploads_final_at") == at
    r = runner.invoke(cli_app, ["history", "undo", str(cid), "--keep-validation", *d], env={"COLUMNS": "300"})
    assert r.exit_code == 0 and artifacts.outcome_line("keep", 2) in _flat(r), r.output
    q = Project.load(p.root)
    assert _rows(p) == [("uploads", 0, "keep")] and q.store.get_meta("uploads_final_at") is None
    assert journal.changes(q, "uploads_reopened")[0]["summary"] == "uploads reopened: final mark undone (kept as validation)"
    assert {b.benchmark_id for b in coverage.validation_papers(q)} == {"B002", "B003"}


def test_look_pages_ask_while_reopened(tmp_path):
    """Scenario "Peek after an unlock" on the pages: the coverage and ablation routes answer
    choice_needed with the reopened notice and write nothing; with keep the look is logged as kept (not counted)."""
    from tests.unit.test_dry_run import _add_val, _reopened

    p = _reopened(tmp_path, "pg")
    b = _add_val(p, "10.1000/pg7")
    client = _client(p)
    want = f"Validation set requested while the query is reopened; it holds 1 paper. {TAIL}"
    before = _snapshot(p)
    for url in ("/api/coverage?validation=true", "/api/rank/ablate?validation=true"):   # neither needs a ranking to ask
        r = client.get(url)
        assert r.status_code == 200 and r.json()["choice_needed"] is True, (url, r.text)
        assert r.json()["notice"] == want and r.json()["validation_included"] is False, (url, r.json())
    assert _snapshot(p) == before and coverage._look_entries(Project.load(p.root)) == []
    r = client.get("/api/coverage?validation=true&seen_choice=keep")
    assert r.status_code == 200 and not r.json().get("choice_needed"), r.text
    e = [x for x in coverage._look_entries(Project.load(p.root)) if x["benchmark_id"] == b]
    assert [(x["choice"], x["after_unlock"], x["counted"]) for x in e] == [("keep", 1, False)]


@pytest.mark.parametrize("how", ["unlock", "freeze"])
def test_hiding_off_after_kept_unlock_stays_kept(tmp_path, how):
    """Risk-checker R1: the papers shown while hiding was off, after a kept unlock or a kept freeze in place of the
    frozen query, are kept as validation (not counted); every output says so, never "count as seen"."""
    p = _frozen(tmp_path, f"h{how}")
    r = (_cli(p, "construct", "unlock", "synonyms", "3", "--keep-validation") if how == "unlock" else
         _cli(p, "construct", "freeze", "synonyms", "2", "--keep-validation"))
    assert r.exit_code == 0, r.output
    for v in ("false", "true"):
        r = _cli(p, "protocol", "set", "identification.validation_after_freeze", v)
        assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    assert [e["counted"] for e in coverage._look_entries(q)] == [False, False]
    assert len(coverage.validation_papers(q)) == 2
    note = coverage.validity_note(q)
    text = " ".join(coverage.validity_lines(note))
    assert "count as seen" not in text and "counts as seen" not in text, text
    assert "(2 papers shown); the researcher chose to keep them as validation." in text, text
    assert note["looks_after_unlock"] == [], note
