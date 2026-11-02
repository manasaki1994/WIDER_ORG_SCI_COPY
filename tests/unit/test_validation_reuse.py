"""Reusing the validation set (change validation-reuse): used validation papers stay in Validation, a revision after a
kept look records them as used, moving a shown paper to Development warns and is reported. Synthetic, topic-neutral
seed papers only; nothing leaves the computer."""
import yaml

from tests.unit.test_dry_run import _bench, _changes, _cli, _client, _import_one, _labels
from tests.unit.test_outputs import in_every_output, validity_everywhere
from wider.construct import artifacts
from wider.core import journal
from wider.core.project import Project
from wider.search import coverage, diagnostics

SYN = {"blocks": [{"key": "a", "name": "Remote", "terms": ["telework"]},
                  {"key": "b", "name": "Learning", "terms": ["learning"]}], "logic": {"and": ["a", "b"]}}
PAPERS = [("B001", "Development", "Telework and team learning", "Telework teams that learn.", ""),
          ("B004", "Development", "Distributed teams learning from errors", "Virtual work groups learning from errors.",
           "virtual work; learning"),
          ("B002", "Validation", "Validation paper two", "A synthetic abstract.", ""),
          ("B003", "Validation", "Validation paper three", "Another synthetic abstract.", "")]


def _revision_notice(day: str, v: int) -> str:
    return (f"You looked at the validation set on {day} and kept 2 papers as validation. Saving v{v} revises the query "
            "after that look: the 2 papers stay in Validation, recorded as used; recall on them will not be independent.")


def _looked(tmp_path, name: str, choice: str = "--keep-validation") -> Project:
    """Synonyms v1-v3 saved, one file imported, then a look at the hidden set (B002, B003) before any freeze."""
    p = _bench(tmp_path, PAPERS, name=name)
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    _import_one(p, tmp_path, f"{name}.ris")
    r = _cli(p, "coverage", "--validation", choice)
    assert r.exit_code == 0, r.output
    return p


def _flat(r) -> str:
    return " ".join(r.output.split())


def _versions(p) -> list[int]:
    return [x["version"] for x in artifacts.list_versions(Project.load(p.root), "synonyms")]


def test_save_after_kept_look_records_used(tmp_path, monkeypatch):
    """Scenarios "Saving after a kept look" and "Nothing more to record" (D2)."""
    from wider.core.models import ProviderConfig
    from wider.providers import fake as fakemod

    p = _looked(tmp_path, "keep")
    day = _changes(p, "validation_peek")[0]["at"][:10]
    want = _revision_notice(day, 4)
    # the page's confirm step reads the notice first; the CLI stops with it and saves nothing
    assert _client(p).get("/api/construct/synonyms").json()["save_notice"] == want
    f = tmp_path / "v4.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    stop = f"{want} {artifacts.REVISION_CONFIRM}"
    r = _cli(p, "construct", "save", "synonyms", str(f))
    assert r.exit_code == 2 and stop in _flat(r), r.output
    edit = next(e for e in diagnostics.suggested_edits(p)["edits"] if e["label"] == 'add "virtual work" to block a')
    r = _cli(p, "query", "edit", "apply", edit["id"])
    assert r.exit_code == 2 and stop in _flat(r), r.output
    (tmp_path / "q.txt").write_text("TI (telework) AND TI (learning)", encoding="utf-8")
    r = _cli(p, "query", "import", str(tmp_path / "q.txt"))
    assert r.exit_code == 2 and stop in _flat(r), r.output
    r = _cli(p, "construct", "restore", "synonyms", "1")
    assert r.exit_code == 2 and stop in _flat(r), r.output
    # propose stops before any model call: the provider is never built
    p.config.providers.append(ProviderConfig(name="fake", kind="fake", model="fake"))
    p.save()
    built = []
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, *a, **k: built.append(1))
    r = _cli(p, "construct", "propose", "synonyms", "--provider", "fake")
    assert r.exit_code == 2 and stop in _flat(r) and built == [], r.output
    q = Project.load(p.root)
    assert _versions(q) == [1, 2, 3] and _changes(q, "validation_used") == []
    assert all(not e.get("used_at") for e in coverage._look_entries(q))
    # with --confirm v4 is saved and the papers are recorded as used, in one final change
    r = _cli(p, "construct", "save", "synonyms", str(f), "--confirm")
    assert r.exit_code == 0 and "synonyms v4 saved" in r.output, r.output
    ch = _changes(Project.load(p.root), "validation_used")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("validation papers recorded as used: v4 saved after a look",
                                                             False)]
    assert _client(p).post(f"/api/history/{ch[0]['change_id']}/undo").status_code == 409
    assert _cli(p, "history", "undo", str(ch[0]["change_id"])).exit_code == 1
    # nothing more to record: no notice, v5 saves without --confirm, still one validation_used change
    assert _client(p).get("/api/construct/synonyms").json()["save_notice"] is None
    r = _cli(p, "query", "edit", "apply", edit["id"])
    assert r.exit_code == 0 and "saved draft synonyms v5" in r.output, r.output
    assert len(_changes(Project.load(p.root), "validation_used")) == 1
    # after a freeze the papers are listed as used and every output says the search is not validated
    artifacts.set_status(Project.load(p.root), "synonyms", 4, "frozen")
    q = Project.load(p.root)
    assert _labels(coverage.check(q)) == {"B001": "Development", "B004": "Development", "B002": "used", "B003": "used"}
    texts, mj, _ = validity_everywhere(q)
    in_every_output(texts, f"Search not validated: validation set seen on {day} (looked at before the freeze); no paper "
                    "added since.",
                    f"Validation set looked at on {day} before the freeze (2 papers shown); the researcher chose to keep "
                    "them as validation.",
                    f"The query was revised on {day} (v4 saved) after that look: the 2 papers shown are recorded as used.")
    assert mj["validated"] is False and [(x["version"], x["n"]) for x in mj["revisions_after_look"]] == [(4, 2)]


def test_revision_notice_on_the_page_and_admit(tmp_path):
    """The page's Save and Import (they pass the confirmation after the confirm step) record the papers as used; a
    name admitted after a kept look needs --confirm and its change cannot be undone; a counted look or a look with no
    validation paper gives no notice."""
    from wider.core.provenance import utcnow

    day = utcnow()[:10]
    p = _looked(tmp_path, "page")
    r = _client(p).post("/api/construct/synonyms/save", json={"text": yaml.safe_dump(SYN), "note": "edited"})
    assert r.status_code == 200 and r.json()["version"] == 4, r.text
    assert [x["summary"] for x in _changes(Project.load(p.root), "validation_used")] == [
        "validation papers recorded as used: v4 saved after a look"]
    q = _looked(tmp_path, "imp")
    r = _client(q).post("/api/query/import", json={"text": "TI (telework) AND TI (learning)"})
    assert r.status_code == 200, r.text
    assert len(_changes(Project.load(q.root), "validation_used")) == 1
    # a name admitted after the look: exit 2 without --confirm, then a name_admit change that cannot be undone
    a = _looked(tmp_path, "admit")
    args = ("seeds", "names", "--admit", "virtual work", "--block", "a", "--note", "a synthetic note")
    r = _cli(a, *args)
    assert r.exit_code == 2 and f"{_revision_notice(day, 4)} {artifacts.REVISION_CONFIRM}" in _flat(r), r.output
    assert _versions(a) == [1, 2, 3]
    r = _cli(a, *args, "--confirm")
    assert r.exit_code == 0 and "synonyms v4 saved" in r.output, r.output
    adm = journal.changes(Project.load(a.root), "name_admit")
    assert len(adm) == 1 and not adm[0]["undoable"], adm
    assert _client(a).post(f"/api/history/{adm[0]['change_id']}/undo").status_code == 409
    assert _cli(a, "history", "undo", str(adm[0]["change_id"])).exit_code == 1
    # a counted look leaves nothing to record: no notice, the save needs no --confirm
    c = _looked(tmp_path, "count", "--count-seen")
    assert artifacts.save_notice(c) is None and artifacts.revision_notice(c) is None
    artifacts.save_new_version(c, "synonyms", SYN, origin="manual")
    assert _changes(Project.load(c.root), "validation_used") == []
    # a look with no hidden validation paper: nothing shown, nothing to record
    n = _bench(tmp_path, PAPERS[:2], name="none")
    artifacts.save_new_version(n, "synonyms", SYN, origin="manual")
    _import_one(n, tmp_path, "none.ris")
    _cli(n, "coverage", "--validation", "--keep-validation")
    assert artifacts.revision_notice(Project.load(n.root)) is None


def _frozen(tmp_path, name: str, ris: str | None = None) -> Project:
    """Synonyms v1-v3 saved, one file imported, v3 frozen: the validation set (B002, B003) is shown."""
    p = _bench(tmp_path, PAPERS, name=name)
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    if ris is None:
        _import_one(p, tmp_path, f"{name}.ris")
    else:
        from wider.ingest.importer import add_file, import_all
        (tmp_path / f"{name}.ris").write_text(ris, encoding="utf-8")
        add_file(p, "scopus", tmp_path / f"{name}.ris")
        import_all(p)
    artifacts.set_status(p, "synonyms", 3, "frozen")
    return p


def _sets(p) -> dict[str, str]:
    return {b.benchmark_id: b.set for b in Project.load(p.root).read_benchmark()}


def _today() -> str:
    from wider.core.provenance import utcnow
    return utcnow()[:10]


MOVE_B002 = ("B002 has been shown as a validation paper. Moving it to Development makes it a former validation paper: "
             "the report, the methods draft and the package list it, and the move cannot be undone.")


def test_move_of_shown_paper_warns(tmp_path):
    """Scenario "The move warns first": exit 2 with the notice, the route answers confirm_needed; nothing changes."""
    p = _frozen(tmp_path, "warn")
    r = _cli(p, "benchmark", "set", "B002", "Development")
    assert r.exit_code == 2 and f"{MOVE_B002} Add --confirm to move it. Nothing was done." in _flat(r), r.output
    r = _client(p).post("/api/benchmark/assign", json={"benchmark_id": "B002", "set": "Development"})
    assert r.status_code == 200 and r.json() == {"confirm_needed": True, "notice": MOVE_B002}, r.text
    q = Project.load(p.root)
    assert _sets(q)["B002"] == "Validation" and coverage.moved(q) == []
    assert _changes(q, "validation_moved") == [] and _changes(q, "benchmark_set") == []


def test_confirmed_move_is_final_and_reported(tmp_path):
    """Scenario "A confirmed move is final and reported": one final validation_moved change, "Dev (former val)" and the
    note's line; the page's confirmed move (confirm true) adds the second paper to the same day's line."""
    p = _frozen(tmp_path, "final")
    r = _cli(p, "benchmark", "set", "B002", "Development", "--confirm")
    assert r.exit_code == 0 and "B002: Development" in r.output, r.output
    ch = _changes(Project.load(p.root), "validation_moved")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("validation paper moved to Development: B002", False)]
    r = _client(p).post(f"/api/history/{ch[0]['change_id']}/undo")
    assert r.status_code == 409 and "a move of a shown validation paper is final" in r.text, r.text
    assert _cli(p, "history", "undo", str(ch[0]["change_id"])).exit_code == 1
    assert _sets(p)["B002"] == "Development"
    q = Project.load(p.root)
    res = coverage.check(q)
    rows = {r["benchmark_id"]: r for v in next(iter(res["scopes"].values())).values() for r in v["rows"]}
    assert _labels(res)["B002"] == "Development" and rows["B002"].get("former") is True
    assert "Dev (former val)" in _flat(_cli(p, "coverage"))
    day = _today()
    assert (f"1 validation paper that had been shown was moved to Development on {day} (former validation paper: "
            "B002).") in coverage.validity_note(q)["lines"]
    r = _client(p).post("/api/benchmark/assign", json={"benchmark_id": "B003", "set": "Development", "confirm": True})
    assert r.status_code == 200 and r.json()["set"] == "Development", r.text
    note = coverage.validity_note(Project.load(p.root))
    assert (f"2 validation papers that had been shown were moved to Development on {day} (former validation papers: "
            "B002, B003).") in note["lines"]
    assert [m["benchmark_id"] for m in note["moved"]] == ["B002", "B003"]


def test_unshown_move_is_undoable(tmp_path):
    """Scenario "A paper never shown moves freely", with the Development paper moved to Validation and back after an
    unlock (the set hidden again)."""
    p = _bench(tmp_path, PAPERS, name="free")
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    r = _cli(p, "benchmark", "set", "B003", "Development")
    assert r.exit_code == 0 and "shown" not in r.output, r.output
    ch = _changes(Project.load(p.root), "benchmark_set")
    assert [(x["summary"], x["undoable"]) for x in ch] == [("seed paper set changed: B003 Validation → Development", True)]
    assert not any("moved to Development" in x for x in coverage.validity_note(Project.load(p.root))["lines"])
    assert _cli(p, "history", "undo", str(ch[0]["change_id"])).exit_code == 0
    assert _sets(p)["B003"] == "Validation"
    # after an unlock: B001 (in the unlock's list as a Development paper) to Validation and back, no warning
    v = artifacts.save_new_version(p, "synonyms", SYN, origin="manual")["version"]
    artifacts.set_status(p, "synonyms", v, "frozen")
    artifacts.unlock(p, "synonyms", v, seen_choice="count")
    for target in ("Validation", "Development"):
        r = _cli(p, "benchmark", "set", "B001", target)
        assert r.exit_code == 0, r.output
    q = Project.load(p.root)
    assert [x["summary"] for x in _changes(q, "benchmark_set")][:2] == [
        "seed paper set changed: B001 Validation → Development", "seed paper set changed: B001 Development → Validation"]
    assert _changes(q, "validation_moved") == [] and coverage.moved(q) == []


def test_former_paper_moved_back_is_used(tmp_path):
    """Scenario "A former validation paper moved back": no warning, "Validation, used", the notice beside it."""
    from wider.construct import seed

    p = _frozen(tmp_path, "back")
    assert _cli(p, "benchmark", "set", "B002", "Development", "--confirm").exit_code == 0
    r = _cli(p, "benchmark", "set", "B002", "Validation")
    notice = seed.USED_NOTICE.format(id="B002")
    assert r.exit_code == 0 and notice in _flat(r), r.output
    q = Project.load(p.root)
    assert _labels(coverage.check(q))["B002"] == "used"
    rows = {x["benchmark_id"]: x for x in _client(p).get("/api/benchmark/seed").json()["rows"]}
    assert notice in rows["B002"]["notices"]
    # moved again it is still a shown paper: the move warns, and move-used asks too (risk-checker R1)
    r = _cli(p, "benchmark", "set", "B002", "Development")
    assert r.exit_code == 2 and "Add --confirm to move it. Nothing was done." in _flat(r), r.output
    r = _cli(p, "benchmark", "move-used")
    assert r.exit_code == 2 and "Nothing was done." in _flat(r), r.output
    assert _client(p).post("/api/benchmark/move-used", json={}).json()["confirm_needed"] is True
    assert _cli(p, "benchmark", "move-used", "--confirm").exit_code == 0
    ch = _changes(Project.load(p.root), "validation_moved")
    assert len(ch) == 2 and not any(x["undoable"] for x in ch), ch


def test_split_moving_shown_papers_needs_confirm(tmp_path):
    """Scenario "An applied split moves shown papers": exit 2 naming both, confirm_needed on the route, one final change
    with --confirm; a split moving no shown paper is one undoable benchmark_set change."""
    p = _frozen(tmp_path, "split")
    r = _cli(p, "benchmark", "split", "--dev-share", "1", "--apply")
    out = _flat(r)
    assert r.exit_code == 2 and "B002 and B003 have been shown as validation papers" in out, r.output
    assert "Add --confirm to move them. Nothing was done." in out
    r = _client(p).post("/api/benchmark/split", json={"dev_share": 1, "apply": True})
    assert r.status_code == 200 and r.json()["confirm_needed"] is True and "applied" not in r.json(), r.text
    assert _sets(p) == {"B001": "Development", "B004": "Development", "B002": "Validation", "B003": "Validation"}
    r = _cli(p, "benchmark", "split", "--dev-share", "1", "--apply", "--confirm")
    assert r.exit_code == 0, r.output
    ch = _changes(Project.load(p.root), "validation_moved")
    assert len(ch) == 1 and not ch[0]["undoable"], ch
    assert ch[0]["summary"].startswith("validation papers moved to Development: B002, B003; seed paper sets changed: ")
    sets = _sets(p)  # a split keeps one paper in Validation: a Development paper takes its place
    assert sets["B002"] == sets["B003"] == "Development" and list(sets.values()).count("Validation") == 1
    # nothing shown yet: an applied split is one undoable change
    n = _bench(tmp_path, PAPERS, name="split-free")
    r = _client(n).post("/api/benchmark/split", json={"dev_share": 1, "apply": True})
    assert r.status_code == 200 and r.json()["applied"] is True, r.text
    ch = _changes(Project.load(n.root), "benchmark_set")
    assert len(ch) == 1 and ch[0]["undoable"] and _changes(Project.load(n.root), "validation_moved") == []


def test_undo_of_set_change_after_shown_is_refused(tmp_path):
    """Scenario "Undoing a set change of a paper shown since": the undo, and the redo, refused after the freeze."""
    want = "B001 has been shown as a validation paper since; move it with wider benchmark set B001 Development --confirm"
    p = _bench(tmp_path, PAPERS, name="undo")
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    assert _cli(p, "benchmark", "set", "B001", "Validation").exit_code == 0
    ch = _changes(Project.load(p.root), "benchmark_set")[0]
    artifacts.set_status(Project.load(p.root), "synonyms", 3, "frozen")
    r = _client(p).post(f"/api/history/{ch['change_id']}/undo")
    assert r.status_code == 409 and want in r.text, r.text
    r = _cli(p, "history", "undo", str(ch["change_id"]))
    assert r.exit_code == 1 and want in _flat(r), r.output
    assert _sets(p)["B001"] == "Validation"
    # the redo: B003's move to Development undone before the freeze, its redo after it refused
    q = _bench(tmp_path, PAPERS, name="redo")
    for i in range(3):
        artifacts.save_new_version(q, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    assert _cli(q, "benchmark", "set", "B003", "Development").exit_code == 0
    assert _cli(q, "history", "undo", str(_changes(Project.load(q.root), "benchmark_set")[0]["change_id"])).exit_code == 0
    assert _sets(q)["B003"] == "Validation"
    artifacts.set_status(Project.load(q.root), "synonyms", 3, "frozen")
    u = _changes(Project.load(q.root), "undo")[0]
    r = _cli(q, "history", "undo", str(u["change_id"]))
    assert r.exit_code == 1 and want.replace("B001", "B003") in _flat(r), r.output
    assert _sets(q)["B003"] == "Validation"


def test_set_changes_in_history(tmp_path):
    """Scenario "Set changes listed and undone": benchmark_set undone; validation_used and validation_moved final; the
    three kinds in `wider history list --kind` and the History "Show" select."""

    p = _looked(tmp_path, "hist")
    assert _cli(p, "benchmark", "add", "--doi", "10.1000/b005", "--set", "Validation", "--source", "colleague").exit_code == 0
    csv_before = (p.root / "benchmark.csv").read_text(encoding="utf-8")
    assert _cli(p, "benchmark", "set", "B005", "Development").exit_code == 0
    ch = _changes(Project.load(p.root), "benchmark_set")[0]
    assert ch["undoable"] and _cli(p, "history", "undo", str(ch["change_id"])).exit_code == 0
    assert _sets(p)["B005"] == "Validation" and (p.root / "benchmark.csv").read_text(encoding="utf-8") == csv_before
    r = _cli(p, "history", "list", "--kind", "benchmark_set")
    assert r.exit_code == 0 and "seed paper set changed" in r.output, r.output
    f = tmp_path / "v4.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    assert _cli(p, "construct", "save", "synonyms", str(f), "--confirm").exit_code == 0
    assert _cli(p, "benchmark", "set", "B002", "Development", "--confirm").exit_code == 0
    for kind, label in [("validation_used", "validation papers recorded as used"),
                        ("validation_moved", "validation paper moved to Development")]:
        r = _cli(p, "history", "list", "--kind", kind)
        assert r.exit_code == 0 and label in r.output, r.output
        c = _changes(Project.load(p.root), kind)[0]
        assert _client(p).post(f"/api/history/{c['change_id']}/undo").status_code == 409
        assert _cli(p, "history", "undo", str(c["change_id"])).exit_code == 1
    assert _sets(p)["B002"] == "Development"
    shown = journal.kinds()   # the History page's Show list is built from these (manual-review 6.3)
    for kind in ("benchmark_set", "validation_used", "validation_moved"):
        assert {"kind": kind, "label": journal.KIND_LABELS[kind]} in shown


def test_move_used_moves_all_and_invites(tmp_path):
    """Scenarios "Moving the used papers" and "No used paper"."""
    from wider.construct import seed

    p = _looked(tmp_path, "used")
    f = tmp_path / "v4.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    assert _cli(p, "construct", "save", "synonyms", str(f), "--confirm").exit_code == 0
    seed_page = _client(p).get("/api/benchmark/seed").json()
    assert seed_page["n_used"] == 2 and seed_page["invite"] is None
    r = _cli(p, "benchmark", "move-used")
    assert r.exit_code == 2 and ("Move the 2 used validation papers (B002, B003) to Development? They become former "
                                 "validation papers: the report, the methods draft and the package list them, and the "
                                 "move cannot be undone. Add --confirm to move them. Nothing was done.") in _flat(r), r.output
    r = _client(p).post("/api/benchmark/move-used", json={})
    assert r.status_code == 200 and r.json()["confirm_needed"] is True, r.text
    assert _sets(p)["B002"] == "Validation"
    r = _cli(p, "benchmark", "move-used", "--confirm")
    assert r.exit_code == 0 and seed.ADD_INVITE in _flat(r), r.output
    q = Project.load(p.root)
    assert [x["summary"] for x in _changes(q, "validation_moved")] == [
        "validation papers moved to Development: B002, B003"]
    assert coverage.former_ids(q) == {"B002", "B003"} and coverage.used_papers(q) == []
    seed_page = _client(p).get("/api/benchmark/seed").json()
    assert seed_page["n_used"] == 0 and seed_page["invite"] == seed.ADD_INVITE
    assert _cli(p, "benchmark", "add", "--doi", "10.1000/b007", "--set", "Validation",
                "--source", "prior_review").exit_code == 0
    assert _client(p).get("/api/benchmark/seed").json()["invite"] is None
    # no used paper: exit 1, 400, no button (n_used 0)
    r = _cli(p, "benchmark", "move-used")
    assert r.exit_code == 1 and "no used validation paper" in r.output, r.output
    assert _client(p).post("/api/benchmark/move-used", json={"confirm": True}).status_code == 400


def test_note_names_used_and_former_papers(tmp_path):
    """Scenarios "Used papers in the note" and "Former validation papers in the note"."""
    found_b002 = ("TY  - JOUR\nTI  - Validation paper two\nPY  - 2020\nDO  - 10.1000/b002\nAB  - A synthetic abstract."
                  "\nER  -\n")
    p = _looked(tmp_path, "note-used")
    from wider.ingest.importer import add_file, import_all
    (tmp_path / "b002.ris").write_text(found_b002, encoding="utf-8")
    add_file(p, "scopus", tmp_path / "b002.ris")
    import_all(p)
    f = tmp_path / "v4.yaml"
    f.write_text(yaml.safe_dump(SYN), encoding="utf-8")
    assert _cli(p, "construct", "save", "synonyms", str(f), "--confirm").exit_code == 0
    artifacts.set_status(Project.load(p.root), "synonyms", 4, "frozen")
    texts, mj, _ = validity_everywhere(Project.load(p.root))
    in_every_output(texts, "Recall on the 2 used validation papers (not independent): 1 of 2.")
    assert mj["used_validation"] == {"ids": ["B002", "B003"], "n": 2, "found": 1}
    # former validation papers: shown in the frozen set, moved, the query found neither; B007 added since
    q = _frozen(tmp_path, "note-former")
    assert _cli(q, "benchmark", "set", "B002", "Development", "--confirm").exit_code == 0
    assert _cli(q, "benchmark", "set", "B003", "Development", "--confirm").exit_code == 0
    assert _cli(q, "benchmark", "add", "--doi", "10.1000/b007", "--set", "Validation",
                "--source", "prior_review").exit_code == 0
    texts, mj, _ = validity_everywhere(Project.load(q.root))
    in_every_output(texts, f"2 validation papers that had been shown were moved to Development on {_today()} (former "
                    "validation papers: B002, B003).", "Former validation papers: 0 of 2 found by the query.")
    assert mj["validated"] is True and mj["n_validation"] == 1
    assert [(x["benchmark_id"], x["found_by_query"], bool(x["at"]), "by" in x) for x in mj["former_validation"]] == [
        ("B002", False, True, True), ("B003", False, True, True)]


def _unlocked(tmp_path, name: str, papers=PAPERS) -> Project:
    """v3 frozen with B002 and B003 in Validation, then unlocked into draft v4: B002 and B003 are used papers."""
    p = _bench(tmp_path, papers, name=name)
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    _import_one(p, tmp_path, f"{name}.ris")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    artifacts.unlock(p, "synonyms", 3, seen_choice="count")
    return Project.load(p.root)


THREE = [PAPERS[0], PAPERS[2], PAPERS[3]]   # B001 the only Development paper; B002, B003 in Validation


def test_used_papers_are_reported_apart(tmp_path):
    """Scenario "Used papers in coverage and status" (D1)."""
    from wider.ui.app import STATIC

    p = _unlocked(tmp_path, "apart", THREE)
    assert _cli(p, "benchmark", "add", "--doi", "10.1000/b007", "--set", "Validation",
                "--source", "colleague").exit_code == 0
    new = next(b.benchmark_id for b in Project.load(p.root).read_benchmark() if b.doi == "10.1000/b007")
    artifacts.set_status(Project.load(p.root), "synonyms", 4, "frozen")
    for res in (coverage.check(Project.load(p.root)), _client(p).get("/api/coverage").json()):
        sets = next(iter(res["scopes"].values()))
        assert [r["benchmark_id"] for r in sets["Validation"]["rows"]] == [new]
        used = sets[coverage.USED]
        assert [r["benchmark_id"] for r in used["rows"]] == ["B002", "B003"] and all(r["used"] for r in used["rows"])
        assert used["n"] == 2 and used["found"] == 0 and len(used["interval"]) == 2
        assert res["used_validation"] == ["B002", "B003"]
    r = _cli(p, "coverage")
    rows = [ln for ln in r.output.splitlines() if "B002" in ln or "B003" in ln]
    assert r.exit_code == 0 and len(rows) == 2 and all("Val (used)" in ln for ln in rows), r.output
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '"Val (used)"' in js and coverage.USED_TITLE in js
    r = _cli(p, "status")
    assert "benchmark: 4 papers (1 development, 1 validation, 2 used validation)" in _flat(r), r.output
    assert _client(p).get("/api/project").json()["benchmark"]["used"] == 2


def test_used_papers_are_not_development_seeds(tmp_path):
    """Scenario "Used papers are not development seeds" (D1): the dry run, the Overview and the hidden set."""
    from tests.unit.test_dry_run import _dry_cli, _dry_route
    from wider.core.summary import project_summary

    p = _unlocked(tmp_path, "seeds", THREE)
    r, data = _dry_cli(p, "--platform", "scopus")
    assert r.exit_code == 0 and data is not None, r.output
    route = _dry_route(p, platform="scopus")
    assert route.status_code == 200, route.text
    for res in (data, route.json()):
        assert [s["benchmark_id"] for s in res["platforms"]["scopus"]["seeds"]] == ["B001"]
    looks = len(_changes(Project.load(p.root), "validation_peek"))
    assert not coverage.validation_unlocked(Project.load(p.root))
    for res in (coverage.check(Project.load(p.root)), _client(p).get("/api/coverage").json()):
        sets = next(iter(res["scopes"].values()))
        assert "Validation" not in sets
        assert [r["benchmark_id"] for r in sets[coverage.USED]["rows"]] == ["B002", "B003"]
    assert len(_changes(Project.load(p.root), "validation_peek")) == looks
    seed = project_summary(Project.load(p.root))["seed"]   # the Overview's seed coverage, after the check
    assert seed["development"]["n"] == 1 and seed["used"]["n"] == 2


def test_used_papers_left_out_of_ablation(tmp_path):
    """Scenario "Ablation with the validation set", its used-paper line: B4 (in Validation at a counting unlock, in the
    corpus) is in neither column, and `wider rank validate` lists it under "Validation, used"."""
    from tests.unit.test_dry_run import _cli as cli
    from tests.unit.test_rank import _ris
    from wider.core.models import BenchmarkPaper, EmbeddingConfig
    from wider.dedup.engine import run as dedup_run
    from wider.ingest.importer import add_file, import_all
    from wider.rank.ablate import ablate
    from wider.rank.corpus import embed_corpus
    from wider.rank.score import rank, validate

    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)]
    p.config.default_embedding = "fake"
    p.save()
    (tmp_path / "a.ris").write_text(_ris(30))
    add_file(p, "scopus", tmp_path / "a.ris")
    import_all(p)
    dedup_run(p)
    dev = BenchmarkPaper(benchmark_id="B1", title="Paper one", doi="10.1000/p0", set="Development")
    b4 = BenchmarkPaper(benchmark_id="B4", title="Used paper four", doi="10.1000/p5", set="Validation")
    p.write_benchmark([dev, b4])
    syn = {"blocks": [{"key": "a", "name": "A", "terms": ["voice"]}], "logic": {"and": ["a"]}}
    artifacts.save_new_version(p, "synonyms", syn, origin="manual", status="frozen")
    artifacts.unlock(p, "synonyms", 1, seen_choice="count")   # B4 was shown: a used paper from here on
    p.write_benchmark([dev, b4,
                       BenchmarkPaper(benchmark_id="B2", title="Paper two", doi="10.1000/p3", set="Validation",
                                      validation_since_unlock=1),
                       BenchmarkPaper(benchmark_id="B3", title="Paper three", doi="10.1000/none", set="Validation",
                                      validation_since_unlock=1)])   # added after the unlock, as `benchmark add` does
    embed_corpus(p)
    artifacts.save_new_version(p, "anchors", {"anchors": {"voice_cell": "employee voice silence speaking up " * 10}},
                               origin="manual")
    assert [b.benchmark_id for b in coverage.used_papers(p)] == ["B4"]
    rows = ablate(p, top_ns=(1,), include_validation=True)
    assert rows[0]["dev_found"] == "1/1" and rows[0]["val_found"] == "1/2"   # B4 is in neither column
    rank(p, top_n=1)
    v = validate(p, include_validation=True)
    assert {r["benchmark_id"]: r["set"] for r in v["rows"]}["B4"] == coverage.USED
    assert "Validation, used" in str(v["summary"])
    r = cli(p, "rank", "validate")
    line = next(ln for ln in r.output.splitlines() if "B4" in ln)
    assert r.exit_code == 0 and "Val (used)" in line and "Validation, used" in _flat(r), r.output


def test_hiding_turned_back_on_records_papers_as_seen(tmp_path):
    """Owner answer q-20261020-120242-7ae2 (risk-checker R2): papers shown while validation hiding was off are recorded
    as seen, kept as validation, when it is turned back on (CLI set and reset, page set and reset); a revision then
    records them as used and moving one warns. Turning it on while it is already on records nothing."""
    key = "identification.validation_after_freeze"
    summary = coverage.HIDING_ON_SUMMARY.format(n=2)
    p = _bench(tmp_path, PAPERS, name="hide")
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN | {"notes": f"draft {i + 1}"}, origin="manual")
    assert _cli(p, "protocol", "set", key, "true").exit_code == 0   # already on: nothing to record
    assert _cli(p, "protocol", "set", key, "false").exit_code == 0
    assert _changes(Project.load(p.root), "validation_peek") == []
    r = _cli(p, "protocol", "set", key, "true")
    assert r.exit_code == 0 and f"{summary}; logged in the history" in _flat(r), r.output
    q = Project.load(p.root)
    ch = _changes(q, "validation_peek")
    assert len(ch) == 1 and ch[0]["summary"] == summary and not ch[0]["undoable"], ch
    looks = coverage._look_entries(q)
    assert sorted(e["benchmark_id"] for e in looks) == ["B002", "B003"]
    assert not any(e["counted"] for e in looks) and {e["kind"] for e in looks} == {coverage.HIDING_OFF}
    assert all(coverage.has_been_shown(q, b) for b in q.read_benchmark() if b.set == "Validation")
    # a revision records them as used; moving one warns
    assert _client(p).get("/api/construct/synonyms").json()["save_notice"].endswith(
        "the 2 papers stay in Validation, recorded as used; recall on them will not be independent.")
    r = _cli(p, "benchmark", "set", "B002", "Development")
    assert r.exit_code == 2 and "Add --confirm to move it. Nothing was done." in _flat(r), r.output
    # the CLI reset and the page's set and reset record the same
    assert _cli(p, "protocol", "set", key, "false").exit_code == 0
    r = _cli(p, "protocol", "reset", key)
    assert r.exit_code == 0 and f"{summary}; logged in the history" in _flat(r), r.output
    c = _client(p)
    assert c.post("/api/protocol/set", json={"path": key, "value": False}).json()["notice"] == ""
    assert c.post("/api/protocol/set", json={"path": key, "value": True}).json()["notice"] == \
        f"{summary}; logged in the history"
    c.post("/api/protocol/set", json={"path": key, "value": False})
    assert c.post("/api/protocol/reset", json={}).json()["notice"] == f"{summary}; logged in the history"
    assert len(_changes(Project.load(p.root), "validation_peek")) == 4
    # the Settings page shows the notice under the protocol table after Set, Reset and Reset everything
    from tests.unit.test_backends_js_node import STATIC

    setup = (STATIC / "setup.js").read_text(encoding="utf-8")
    assert 'flash(q("#proto-msg"), `${path} set` + (r && r.notice ? `; ${r.notice}` : ""));' in setup
    assert setup.count('if (r && r.notice) flash(q("#proto-msg"), r.notice);') == 2
