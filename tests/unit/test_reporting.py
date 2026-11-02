"""PRISMA reporting assistant (prisma-reporting-assistant, D1-D4): the item catalogue, evidence lines with the
elements they cover, statuses computed from evidence only and what is still missing. Synthetic records only."""
import csv
import io
import json
import re
import zipfile

import pytest

from tests.unit.test_outputs import SYN
from tests.unit.test_prisma_2020_checklist import checked  # noqa: F401 - pytest fixture
from tests.unit.test_screen import project as _project_fixture  # noqa: F401 - pytest fixture
from wider.construct import artifacts
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.ingest.importer import add_file, import_all
from wider.recall import reporting as r
from wider.search.archive import archive


def _ris(path, n, word):
    path.write_text("".join(f"TY  - JOUR\nTI  - Synthetic {word} paper {i}\nPY  - 2020\nER  -\n" for i in range(n)))
    return path


def _statuses(p):
    ctx = r.context(p)
    out = {}
    for cl in r.CHECKLISTS:
        st = {it["no"]: r.view(p, cl, it["no"], ctx)["wider_status"] for it in r.catalogue()[cl]}
        out[cl] = {s: sorted((k for k, v in st.items() if v == s), key=lambda n: (int(n.rstrip("abcdef")), n))
                   for s in (r.AUTO, r.PART, r.INPUT)}
    return out


def test_catalogue_complete():
    cat = r.catalogue()
    nos = [it["no"] for it in cat["prisma_2020"]]
    assert len(nos) == 42 and len(set(nos)) == 42 and nos[0] == "1" and nos[-1] == "27" and "16a" in nos
    assert [it["no"] for it in cat["prisma_s"]] == [str(i) for i in range(1, 17)]
    for cl in r.CHECKLISTS:
        for it in cat[cl]:
            assert it["elements"] and all(set(e) <= {"key", "text", "outside_wider"} and e["text"] for e in it["elements"])
            assert len({e["key"] for e in it["elements"]}) == len(it["elements"])
            assert it["asks"].endswith(".")
    assert r.item("prisma-2020", "7")["elements"] == [{"key": "strategies", "text": "search strategies for every source"},
                                                      {"key": "filters_limits", "text": "filters and limits"}]
    stages = {"9", "10", "11", "12", "13", "14", "15", "17", "18", "19", "20", "21", "22", "25", "26"}
    outside = {it["no"] for it in cat["prisma_2020"] if it["outside_wider"]}
    assert outside == {n for n in nos if re.sub("[a-z]$", "", n) in stages} | {"23a", "23b", "23d", "24a"}
    assert not any(it["outside_wider"] for it in cat["prisma_s"])
    assert [e["key"] for e in r.item("prisma_2020", "8")["elements"] if e.get("outside_wider")] == ["reviewers_per_report"]
    assert "full-text" in r.NOTES["prisma_2020"]["16a"](None, {})["counts"][0]
    with_evidence = {"4", "5", "6", "7", "8", "16a", "23c", "24b", "27"}
    assert set(r.EVIDENCE["prisma_2020"]) == with_evidence and not with_evidence & outside
    assert set(r.EVIDENCE["prisma_s"]) == {"1", "2", "5", "7", "8", "9", "11", "13", "14", "15", "16"}
    with pytest.raises(ValueError, match="unknown item '99' in PRISMA 2020"):
        r.item("prisma-2020", "99")
    with pytest.raises(ValueError, match="unknown checklist"):
        r.item("prisma-2021", "1")


def test_related_both_ways():
    for it in r.catalogue()["prisma_2020"]:
        for s in it["related"]:
            assert it["no"] in r.item("prisma_s", s)["related"]
    assert r.item("prisma_s", "8")["related"] == ["7"] and r.item("prisma_s", "15")["related"] == ["16a"]
    assert r.item("prisma_s", "6")["related"] == ["6"]


def test_context_lines_cover_nothing():
    it = r.item("prisma_2020", "6")
    ctx_only = [r.line("Scopus: imported 2026-01-01 (import date; search date not recorded)", "imports")]
    assert r.status(it, ctx_only) == r.INPUT
    assert all(m["say"] == r.NOT_RECORDED for m in r.missing(None, "prisma_2020", it, ctx_only, {"sources": []}))
    part = ctx_only + [r.line("Scopus: export a.ris, 1,204 records", "imports", {"databases": "full"})]
    assert r.status(it, part) == r.PART and part[1]["numbers"] == ["1204"]


def test_context_lines_24b_24c(checked):  # noqa: F811
    """24b: the settings hash is a context line, not a review protocol, so the item needs input; 24c: nothing recorded,
    and History's settings changes are not offered as an amendment record."""
    for q in (2, 3):
        checked.config.protocol.screening.majority = q
        checked.save()
    assert checked.store.scalar("SELECT COUNT(*) FROM history") >= 2
    ctx = r.context(checked)
    b, c = r.view(checked, "prisma_2020", "24b", ctx), r.view(checked, "prisma_2020", "24c", ctx)
    assert [e["text"] for e in b["evidence"]] == [ln["text"] for ln in r.evidence(checked, "prisma_2020", "24b", ctx)]
    assert len(b["evidence"]) == 1 and b["evidence"][0]["text"].startswith(
        "WIDER screening settings (not a review protocol): hash ")
    assert b["wider_status"] == r.INPUT and c["wider_status"] == r.INPUT and c["evidence"] == []
    doc = {it["no"]: it for it in r.checklist_doc(checked, "prisma_2020", ctx)["items"]}
    assert doc["24c"]["lines"] == ["not recorded by WIDER"] and doc["24c"]["status"] == r.INPUT
    assert not any("history" in ln.lower() for ln in doc["24c"]["lines"] + doc["24b"]["lines"])


def test_statuses_from_evidence_only():
    it = r.item("prisma_2020", "4")
    assert r.status(it, []) == r.INPUT
    assert r.status(it, [r.line("research question: q", "project settings", {"question": "full"})]) == r.AUTO
    two = r.item("prisma_s", "16")
    lines = [r.line("x", "s", {"dedup_process": "full", "software": "full"}), r.line("y", "s", {"dedup_process": "part"})]
    assert r.coverage(two, lines) == {"dedup_process": "part", "software": "full"} and r.status(two, lines) == r.PART


def test_outside_wider_item(checked):  # noqa: F811
    ctx = r.context(checked)
    v = r.view(checked, "prisma-2020", "17", ctx)
    assert v["outside_wider"] and v["outside_sentence"] == r.OUTSIDE and v["evidence"] == []
    assert v["wider_status"] == r.INPUT and all(m["outside_wider"] for m in v["missing"])


def test_item_7_blocks(checked):  # noqa: F811
    v = r.view(checked, "prisma_2020", "7", r.context(checked))
    says = {m["key"]: m["say"] for m in v["missing"]}
    assert v["wider_status"] == r.INPUT and v["evidence"] == []
    assert says["strategies"] == r.NOT_RECORDED
    artifacts.save_new_version(checked, "synonyms", SYN, origin="manual")
    archive(checked)
    v = r.view(checked, "prisma_2020", "7", r.context(checked))
    texts = [ln["text"] for ln in v["evidence"]]
    assert v["wider_status"] == r.PART and all(ln["covers"] == {"strategies": "part"} for ln in v["evidence"])
    assert any(t.startswith("Scopus (synonyms v") and "search_strings/" in t for t in texts)
    say = {m["key"]: m["say"] for m in v["missing"]}["strategies"]
    assert say.startswith(r.PART_SAY) and "not confirmed as run as archived" in say


def test_demo_items_on_fixture(checked):  # noqa: F811
    ctx = r.context(checked)
    st = {(cl, it["no"]): r.view(checked, cl, it["no"], ctx)["wider_status"]
          for cl in r.CHECKLISTS for it in r.catalogue()[cl]}
    assert st[("prisma_2020", "4")] == r.PART   # the question is part of the objectives (whole-app inspection)
    assert st[("prisma_2020", "6")] == r.PART          # Scopus and WoS: dates are context, other sources not covered
    assert st[("prisma_s", "15")] == r.AUTO
    v8 = r.view(checked, "prisma_2020", "8", ctx)
    assert v8["coverage"]["automation"] == "full" and v8["coverage"]["method"] == "full"
    assert v8["coverage"]["independence"] == "part"    # every pass ran on the fake model
    assert st[("prisma_2020", "1")] == r.INPUT


def test_context_before_import(tmp_path):
    from wider.core.project import Project

    with pytest.raises(ValueError, match="nothing imported yet"):
        r.context(Project.init(tmp_path / "blank", platforms=["scopus"]))


def test_demo_statuses(checked):  # noqa: F811
    """The fixture shaped like the demo (archived strings, one benchmark paper) gives the item table's demo column."""
    artifacts.save_new_version(checked, "synonyms", SYN, origin="manual")
    archive(checked)
    checked.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Synthetic seed paper", set="Development")])
    st = _statuses(checked)
    assert st["prisma_2020"].get(r.AUTO, []) == [] and st["prisma_2020"][r.PART] == ["4", "5", "6", "7", "8", "16a", "23c"]
    assert len(st["prisma_2020"][r.INPUT]) == 35
    assert st["prisma_s"][r.AUTO] == ["15"] and st["prisma_s"][r.PART] == ["1", "7", "8", "16"]
    assert len(st["prisma_s"][r.INPUT]) == 11


def test_prisma_s_citation_imports_and_prior_reviews(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["citation_chain"])
    add_file(p, "citation_chain", _ris(tmp_path / "c.ris", 2, "chain"))
    import_all(p)
    p.store.execute("INSERT INTO prior_reviews(label, kind, file_name, sha256, n_papers, added_at, note) "
                    "VALUES ('Synthetic prior review', 'review', 'pr.ris', 'x', 2, '2026-01-01T00:00:00', '')")
    ctx = r.context(p)
    v5, v11 = r.view(p, "prisma_s", "5", ctx), r.view(p, "prisma_s", "11", ctx)
    assert v5["wider_status"] == r.PART and v5["evidence"][0]["text"] == "citation searching: export c.ris, 2 records"
    assert v11["wider_status"] == r.INPUT
    assert v11["evidence"][0]["text"].startswith("prior review: Synthetic prior review") and v11["evidence"][0]["covers"] == {}

    p.config.platforms.append(type(p.config.platforms[0])(key="scopus", label="Scopus"))
    p.save()
    add_file(p, "scopus", _ris(tmp_path / "a.ris", 4, "alpha"))
    import_all(p)
    ctx = r.context(p)
    s1 = [ln["text"] for ln in r.view(p, "prisma_s", "1", ctx)["evidence"]]
    assert s1 == ["Scopus: export a.ris, 4 records"]
    p6 = r.view(p, "prisma_2020", "6", ctx)["evidence"]
    assert [ln["text"] for ln in p6 if "databases" in ln["covers"]] == ["Scopus: export a.ris, 4 records"]
    assert [ln["text"] for ln in p6 if "other_sources" in ln["covers"]] == ["citation searching: export c.ris, 2 records"]


def test_prisma_s_multi_database_platform(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus", "ebsco"])
    add_file(p, "scopus", _ris(tmp_path / "a.ris", 4, "alpha"))
    add_file(p, "ebsco", _ris(tmp_path / "e.ris", 3, "eb"))
    import_all(p)
    ctx = r.context(p)
    v1 = r.view(p, "prisma_s", "1", ctx)
    assert {ln["text"]: ln["covers"] for ln in v1["evidence"]} == {
        "EBSCOhost: export e.ris, 3 records": {"database_and_platform": "part"},
        "Scopus: export a.ris, 4 records": {"database_and_platform": "full"}}
    assert v1["wider_status"] == r.PART
    assert "which databases on EBSCOhost: not recorded by WIDER" in v1["missing"][0]["say"]
    v2 = r.view(p, "prisma_s", "2", ctx)
    assert [ln["text"] for ln in v2["evidence"]] == ["multi-database platform used: EBSCOhost"]
    assert all("PsycINFO" not in ln["text"] for v in (v1, v2) for ln in v["evidence"])


GOLDEN = __import__("pathlib").Path(__file__).parents[1] / "fixtures" / "methods_draft_golden.txt"


def test_methods_draft_unchanged(checked):  # noqa: F811
    """D9: splitting ``methods_draft`` into sentence helpers leaves its paragraph as it was (dates normalised)."""
    import os

    from wider.recall.outputs import methods_draft

    artifacts.save_new_version(checked, "synonyms", SYN, origin="manual")
    archive(checked)
    checked.write_benchmark([BenchmarkPaper(benchmark_id="B1", title="Synthetic seed paper", set="Development")])
    text = re.sub(r"\d{4}-\d{2}-\d{2}", "DATE", methods_draft(checked))
    if os.environ.get("WIDER_WRITE_GOLDEN"):
        GOLDEN.write_text(text + "\n", encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8").rstrip("\n")


# ------------------------------------------------------------------------------------------------ your text (D5-D8)
def _imported(tmp_path, n=4, platforms=("scopus",)):
    p = Project.init(tmp_path / "r", platforms=list(platforms))
    add_file(p, "scopus", _ris(tmp_path / "a.ris", n, "alpha"))
    import_all(p)
    return p


def _summaries(p):
    from wider.core import journal

    return [c["summary"] for c in journal.changes(p, kind="report_text")]


def _records(p):
    """Every table but ``history`` and ``researchers`` (the journal's own: who changed what), and ``project.yaml``."""
    tables = [t["name"] for t in p.store.query("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                                "'sqlite_%' AND name NOT IN ('history', 'researchers') ORDER BY name")]
    return ({t: [dict(x) for x in p.store.query(f"SELECT * FROM {t} ORDER BY rowid")] for t in tables},
            (p.root / "project.yaml").read_bytes())


def _state(p, cl, no, **kw):
    ctx = r.context(p, **kw)
    return r.text_state(p, cl, no, ctx, r.evidence(p, cl, no, ctx))


def test_save_leaves_records_unchanged(tmp_path):
    p = _imported(tmp_path)
    before = _records(p)
    r.save_text(p, "prisma_2020", "8", "First text.", by="rev1")
    r.save_text(p, "prisma_2020", "8", "Second text.", by="rev1")
    r.clear(p, "prisma_2020", "8", by="rev1")
    assert _records(p) == before
    rec = r.read_text(p, "prisma_2020", "8")
    assert rec["text"] == "" and [v["text"] for v in rec["versions"]] == ["Second text.", "First text."]
    assert all(v["saved_by"] == "rev1" and v["saved_at"] for v in rec["versions"])
    assert _summaries(p) == ["PRISMA 2020 item 8: text cleared", "PRISMA 2020 item 8: text saved",
                             "PRISMA 2020 item 8: text saved"]
    # the page's twin: the same engine through the routes, signed by the Reviewer box
    c = _api(p)
    assert c.post("/api/reporting/prisma-2020/9/text", json={"text": "Page text."}).json()["changed"]
    assert c.post("/api/reporting/prisma-2020/9/text", json={"text": "Page text."}).json() == {
        "changed": False, "change_id": None}
    assert c.post("/api/reporting/prisma-2020/9/clear").status_code == 200
    assert _records(p) == before
    rec = r.read_text(p, "prisma_2020", "9")
    assert rec["text"] == "" and [(v["text"], v["saved_by"]) for v in rec["versions"]] == [("Page text.", "rev1")]
    assert _summaries(p)[:2] == ["PRISMA 2020 item 9: text cleared", "PRISMA 2020 item 9: text saved"]
    item = c.get("/api/reporting/prisma-2020/9").json()
    assert item["no"] == "9" and "draft" in item and item["yours"]["text"] == ""
    for body, msg in (({}, "give text, from_draft, from_item, covers, where or keep"),
                      ({"text": "x", "from_draft": True}, "give one of text, from_draft and from_item"),
                      ({"text": "x", "covers": ["nonsense"]}, "unknown element 'nonsense'")):
        res = c.post("/api/reporting/prisma-2020/9/text", json=body)
        assert res.status_code == 400 and msg in res.json()["detail"], (body, res.text)
    assert c.get("/api/reporting/prisma-2020/99").status_code == 400
    assert _records(p) == before


def test_same_save_records_nothing(tmp_path):
    p = _imported(tmp_path)
    first = r.save_text(p, "prisma_2020", "7", "Text.", covers=["strategies"], where="p. 4", by="rev1")
    again = r.save_text(p, "prisma_2020", "7", "Text.", covers=["strategies"], where="p. 4", by="rev1")
    assert first["changed"] and not again["changed"] and again["change_id"] is None
    assert not r.save_text(p, "prisma_2020", "7", by="rev1")["changed"]          # nothing given: nothing changes
    assert not r.save_text(p, "prisma_2020", "12", "  ", by="rev1")["changed"]   # an empty text is no text
    assert not r.clear(p, "prisma_2020", "12", by="rev1")["changed"]
    assert len(_summaries(p)) == 1


def test_where_only_save(tmp_path):
    p = _imported(tmp_path)
    r.save_text(p, "prisma_2020", "1", where="p. 3", by="rev1")
    assert r.read_text(p, "prisma_2020", "1")["where"] == "p. 3"
    st = _state(p, "prisma_2020", "1")
    assert st["your_text"] == "none" and st["where"] == "p. 3"
    assert _summaries(p) == ["PRISMA 2020 item 1: location saved"]


def test_versions_capped(tmp_path):
    p = _imported(tmp_path)
    for i in range(22):
        r.save_text(p, "prisma_2020", "8", f"Text {i}.", by="rev1")
    v = r.read_text(p, "prisma_2020", "8")["versions"]
    assert len(v) == r.MAX_VERSIONS == 20 and v[0]["text"] == "Text 20." and v[-1]["text"] == "Text 1."


def test_clear_keeps_versions(tmp_path):
    p = _imported(tmp_path)
    for i in range(22):
        r.save_text(p, "prisma_2020", "8", f"Text {i}.", by="rev1")
    r.mark_not_applicable(p, "prisma_2020", "8", reason="synthetic reason", by="rev1")
    r.clear(p, "prisma_2020", "8", by="rev1")
    rec = r.read_text(p, "prisma_2020", "8")
    assert rec["text"] == "" and rec["not_applicable"] == {"reason": "synthetic reason"}
    assert len(rec["versions"]) == 20 and rec["versions"][0]["text"] == "Text 21." and rec["versions"][-1]["text"] == "Text 2."


def test_unreadable_item_file(tmp_path):
    p = _imported(tmp_path)
    f = p.root / "reporting" / "prisma_2020" / "9.json"
    f.parent.mkdir(parents=True)
    f.write_text("not json {", encoding="utf-8")
    ctx = r.context(p)
    v = r.show(p, "prisma_2020", "9", ctx)
    assert v["yours"]["your_text"] == "unreadable" and v["yours"]["your_text_label"] == r.UNREADABLE
    assert v["status"] == v["wider_status"] == r.INPUT
    row = next(x for x in r.status_rows(p, "prisma_2020", to_check=True) if x["no"] == "9")
    assert row["your_text"] == "unreadable"
    assert f.read_text(encoding="utf-8") == "not json {"
    r.save_text(p, "prisma_2020", "9", "Readable now.", by="rev1")
    assert r.read_text(p, "prisma_2020", "9")["text"] == "Readable now."


def test_text_from_file_and_draft(tmp_path):
    p = _imported(tmp_path)
    (tmp_path / "text.md").write_text("Text from a file.\n", encoding="utf-8")
    r.save_text(p, "prisma_2020", "6", (tmp_path / "text.md").read_text(encoding="utf-8"), by="rev1")
    assert r.read_text(p, "prisma_2020", "6")["text"] == "Text from a file."
    r.save_text(p, "prisma_2020", "6", from_draft=True, by="rev1")
    rec = r.read_text(p, "prisma_2020", "6")
    assert rec["text"] == r.draft(p, "prisma_2020", "6", r.context(p))["text"] and rec["from_draft"] is True
    assert rec["versions"][0]["text"] == "Text from a file."


def test_unknown_item_or_element(tmp_path):
    p = _imported(tmp_path)
    with pytest.raises(ValueError, match="unknown item '99' in PRISMA 2020"):
        r.save_text(p, "prisma_2020", "99", "x", by="rev1")
    with pytest.raises(ValueError, match="unknown element 'nonsense' in PRISMA 2020 item 7"):
        r.save_text(p, "prisma_2020", "7", "x", covers=["nonsense"], by="rev1")
    with pytest.raises(ValueError, match="^no draft for this item$"):
        r.save_text(p, "prisma_2020", "9", from_draft=True, by="rev1")
    assert r.draft(p, "prisma_2020", "9", r.context(p)) is None
    assert not (p.root / "reporting").exists() and _summaries(p) == []


def test_your_text_covers_missing(checked):  # noqa: F811
    ctx = r.context(checked)
    assert [m["key"] for m in r.view(checked, "prisma_2020", "5", ctx)["missing"]] == ["synthesis_groups"]
    r.save_text(checked, "prisma_2020", "5", "Studies were grouped by design.", covers=["synthesis_groups"], by="rev1")
    v = r.show(checked, "prisma_2020", "5", r.context(checked))
    assert v["yours"]["your_text_label"] == "written, covers what was missing" and v["status"] == r.PART
    assert [m["covered_by_you"] for m in v["missing"]] == [True]
    r.save_text(checked, "prisma_2020", "5", covers=[], by="rev1")
    assert r.show(checked, "prisma_2020", "5", r.context(checked))["yours"]["your_text_label"] == "written"


def test_not_applicable_mark_and_clear(tmp_path):
    p = _imported(tmp_path)
    with pytest.raises(ValueError, match="^a reason is required$"):
        r.mark_not_applicable(p, "prisma_2020", "12", reason="  ", by="rev1")
    r.mark_not_applicable(p, "prisma_2020", "12", reason="no quantitative synthesis", by="rev1")
    v = r.show(p, "prisma_2020", "12", r.context(p))
    assert v["status"] == r.NA and v["yours"]["not_applicable"] == {"reason": "no quantitative synthesis"}
    assert v["wider_status"] == r.INPUT and v["has_evidence"] is None
    r.mark_not_applicable(p, "prisma_2020", "12", remove=True, by="rev1")
    assert r.show(p, "prisma_2020", "12", r.context(p))["status"] == r.INPUT
    assert _summaries(p) == ["PRISMA 2020 item 12: not-applicable mark removed",
                             "PRISMA 2020 item 12: marked not applicable"]
    # the page's twin: "Not applicable" with its reason, then "Remove the not-applicable mark" ({clear: true})
    c = _api(p)
    res = c.post("/api/reporting/prisma-2020/13a/na", json={"reason": " "})
    assert res.status_code == 400 and res.json()["detail"] == "a reason is required"
    assert c.post("/api/reporting/prisma-2020/13a/na", json={"reason": "no synthesis"}).json()["changed"]
    assert c.get("/api/reporting/prisma-2020/13a").json()["status"] == r.NA
    assert {x["no"]: x["status"] for x in c.get("/api/reporting/prisma-2020").json()["rows"]}["13a"] == r.NA
    assert c.post("/api/reporting/prisma-2020/13a/na", json={"clear": True}).json()["changed"]
    assert r.show(p, "prisma_2020", "13a", r.context(p))["status"] == r.INPUT
    assert _summaries(p)[:2] == ["PRISMA 2020 item 13a: not-applicable mark removed",
                                 "PRISMA 2020 item 13a: marked not applicable"]


def test_not_applicable_with_evidence(tmp_path):
    p = _imported(tmp_path)
    r.mark_not_applicable(p, "prisma_2020", "16a", reason="synthetic reason", by="rev1")
    v = r.show(p, "prisma_2020", "16a", r.context(p))
    assert v["status"] == r.NA and v["has_evidence"] == r.HAS_EVIDENCE == "WIDER recorded evidence for this item"
    assert v["evidence"] and v["wider_status"] == r.PART


def test_undo_restores_previous_text(tmp_path):
    from wider.core import journal

    p = _imported(tmp_path)
    first = r.save_text(p, "prisma_2020", "8", "First.", by="rev1")
    second = r.save_text(p, "prisma_2020", "8", "Second.", by="rev1")
    journal.undo(p, second["change_id"], author="rev1")
    assert r.read_text(p, "prisma_2020", "8")["text"] == "First."
    journal.undo(p, first["change_id"], author="rev1")
    assert r.read_text(p, "prisma_2020", "8") is None


def test_report_text_history_and_undo(tmp_path):
    from wider.core import journal

    p = _imported(tmp_path)
    assert {"kind": "report_text", "label": "PRISMA reporting text"} in [
        {k: x[k] for k in ("kind", "label")} for x in journal.kinds()]
    before = _records(p)
    r.save_text(p, "prisma_2020", "9", "First text.", by="rev1")
    second = r.save_text(p, "prisma_2020", "9", "Second text.", by="rev1")
    rows = journal.changes(p, kind="report_text")
    assert [x["summary"] for x in rows] == ["PRISMA 2020 item 9: text saved"] * 2 and rows[0]["author"] == "rev1"
    assert not any("First text" in str(x) or "Second text" in str(x) for x in rows)   # the summary never holds the text
    journal.undo(p, second["change_id"], author="rev1")
    assert r.read_text(p, "prisma_2020", "9")["text"] == "First text."
    assert _records(p) == before


def test_edit_from_item(tmp_path):
    p = _imported(tmp_path)
    r.save_text(p, "prisma_s", "13", "Searched on 1 May 2026.", by="rev1")
    r.save_text(p, "prisma_2020", "6", from_item="prisma-s:13", by="rev1")
    rec = r.read_text(p, "prisma_2020", "6")
    assert rec["text"] == "Searched on 1 May 2026." and rec["from_item"] == "prisma-s:13"
    with pytest.raises(ValueError, match="^not a related item$"):
        r.save_text(p, "prisma_2020", "6", from_item="prisma-s:15", by="rev1")
    with pytest.raises(ValueError, match="^PRISMA-S item 1 has no text$"):
        r.save_text(p, "prisma_2020", "6", from_item="prisma-s:1", by="rev1")


def test_gaps_left_to_check(tmp_path):
    p = _imported(tmp_path)
    r.save_text(p, "prisma_2020", "6", from_draft=True, by="rev1")
    d = r.draft(p, "prisma_2020", "6", r.context(p))
    row = next(x for x in r.status_rows(p, "prisma_2020") if x["no"] == "6")
    assert d["gaps"] >= 2 and row["gaps_left"] == d["gaps"] and row["to_check"] and row["your_text"] == "saved"
    r.save_text(p, "prisma_2020", "6", "We searched Scopus on 1 May 2026.", by="rev1")
    row = next(x for x in r.status_rows(p, "prisma_2020") if x["no"] == "6")
    assert row["gaps_left"] == 0 and not row["to_check"]


def test_new_import_outdates_item_6(tmp_path):
    p = _imported(tmp_path)
    r.save_text(p, "prisma_2020", "6", "We searched Scopus.", by="rev1")
    r.save_text(p, "prisma_2020", "24a", "Not registered.", by="rev1")
    p.config.platforms.append(type(p.config.platforms[0])(key="wos", label="Web of Science"))
    p.save()
    add_file(p, "wos", _ris(tmp_path / "b.ris", 3, "beta"))
    import_all(p)
    s6 = _state(p, "prisma_2020", "6")
    assert s6["your_text"] == "outdated" and s6["to_check"] and s6["text"] == "We searched Scopus."
    assert any("a.ris" in x["text"] for x in s6["outdated"]["saved"])
    assert any("b.ris" in x["text"] for x in s6["outdated"]["current"])
    assert not any("b.ris" in x["text"] for x in s6["outdated"]["saved"])
    assert _state(p, "prisma_2020", "24a")["your_text"] == "saved"
    n = len(_summaries(p))
    r.save_text(p, "prisma_2020", "6", keep=True, by="rev1")
    assert _state(p, "prisma_2020", "6")["your_text"] == "saved"
    assert _summaries(p)[0] == "PRISMA 2020 item 6: text kept for the current evidence" and len(_summaries(p)) == n + 1
    assert r.read_text(p, "prisma_2020", "6")["text"] == "We searched Scopus."


def test_location_only_save_keeps_outdated(tmp_path):
    # risk-checker R1: a location or covers save (or the page re-sending the same text) is not a Keep
    p = _imported(tmp_path)
    r.save_text(p, "prisma_2020", "6", "We searched Scopus.", by="rev1")
    r.save_text(p, "prisma_2020", "5", "Records from the main set.", by="rev1")
    saved5 = r.read_text(p, "prisma_2020", "5")
    p.config.platforms.append(type(p.config.platforms[0])(key="wos", label="Web of Science"))
    p.save()
    add_file(p, "wos", _ris(tmp_path / "b.ris", 3, "beta"))
    import_all(p)
    snap = r.read_text(p, "prisma_2020", "6")["evidence_snapshot"]
    r.save_text(p, "prisma_2020", "6", where="p. 4", by="rev1")
    assert _summaries(p)[0] == "PRISMA 2020 item 6: location saved"
    r.save_text(p, "prisma_2020", "6", "We searched Scopus.", covers=["dates"], by="rev1")
    s6 = _state(p, "prisma_2020", "6")
    assert s6["your_text"] == "outdated" and s6["to_check"]
    assert r.read_text(p, "prisma_2020", "6")["evidence_snapshot"] == snap
    r.save_text(p, "prisma_2020", "5", where="p. 3", by="rev1")
    now5 = r.read_text(p, "prisma_2020", "5")
    assert (now5["protocol"], now5["result_set"], now5["evidence_hash"]) == (
        saved5["protocol"], saved5["result_set"], saved5["evidence_hash"])
    r.save_text(p, "prisma_2020", "6", keep=True, by="rev1")
    assert _state(p, "prisma_2020", "6")["your_text"] == "saved"


def test_rebuild_does_not_outdate(tmp_path, monkeypatch):
    p = _imported(tmp_path)
    (p.root / "exports").mkdir(exist_ok=True)
    (p.root / "exports" / "replication_package_20260101-000000.zip").write_bytes(b"synthetic")
    for no in ("6", "16a", "27"):
        r.save_text(p, "prisma_2020", no, f"Text for item {no}.", by="rev1")
    for cl in r.CHECKLISTS:
        r.save_text(p, cl, "15" if cl == "prisma_s" else "4", "Text.", by="rev1")
    first = r.status_rows(p, "prisma_2020") + r.status_rows(p, "prisma_s")
    monkeypatch.setattr(r, "_now", lambda: "2027-01-01T00:00:00Z")
    (p.root / "exports" / "replication_package_20270101-000000.zip").write_bytes(b"synthetic, built again")
    second = r.status_rows(p, "prisma_2020") + r.status_rows(p, "prisma_s")
    assert first == second and not any(x["your_text"] == "outdated" for x in second)
    assert sum(x["your_text"] == "saved" for x in second) == 5


def test_number_inconsistency(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus", "wos"])
    add_file(p, "scopus", _ris(tmp_path / "a.ris", 12, "alpha"))
    add_file(p, "wos", _ris(tmp_path / "b.ris", 10, "beta"))
    import_all(p)
    r.save_text(p, "prisma_s", "15", "Scopus gave 12 records; in total 22 records were imported.", by="rev1")
    add_file(p, "wos", _ris(tmp_path / "c.ris", 11, "gamma"))
    import_all(p)
    st = _state(p, "prisma_s", "15")
    assert st["your_text"] == "outdated"
    assert st["outdated"]["inconsistencies"] == [
        "Your text says 22; WIDER's evidence for this item no longer contains 22 (now: 33)"]


def test_text_saved_before_screening(_project_fixture):  # noqa: F811
    from wider.screen.protocol import run_protocol

    p = _project_fixture
    r.save_text(p, "prisma_2020", "16a", "Counts before screening.", by="rev1")
    r.save_text(p, "prisma_s", "3", "No online resources were browsed.", by="rev1")
    for cl, no in (("prisma_2020", "16a"), ("prisma_s", "3")):
        rec = r.read_text(p, cl, no)
        assert (rec["protocol"], rec["result_set"]) == (None, None) and _state(p, cl, no)["saved_for"] is None
    run_protocol(p, "corpus", "main")
    assert _state(p, "prisma_2020", "16a")["your_text"] == "outdated"
    assert _state(p, "prisma_s", "3")["your_text"] == "saved"
    r.save_text(p, "prisma_2020", "16a", keep=True, by="rev1")
    rec = r.read_text(p, "prisma_2020", "16a")
    assert (rec["protocol"], rec["result_set"]) == ("main", 1)
    assert r.read_text(p, "prisma_s", "3")["protocol"] is None


def test_status_nothing_imported(tmp_path):
    p = Project.init(tmp_path / "blank", platforms=["scopus"])
    for call in (lambda: r.status_rows(p, "prisma_2020"), lambda: r.context(p),
                 lambda: r.save_text(p, "prisma_2020", "6", "x", by="rev1"),
                 lambda: r.mark_not_applicable(p, "prisma_2020", "6", reason="x", by="rev1"),
                 lambda: r.clear(p, "prisma_2020", "6", by="rev1")):
        with pytest.raises(ValueError, match="^nothing imported yet"):
            call()
    assert not (p.root / "reporting").exists()


def test_status_filters(tmp_path):
    p = _imported(tmp_path)
    r.save_text(p, "prisma_2020", "6", "We searched Scopus.", by="rev1")
    r.save_text(p, "prisma_2020", "9", "Two reviewers extracted data.", covers=["process", "reviewers_and_process"]
                if False else [e["key"] for e in r.item("prisma_2020", "9")["elements"]], by="rev1")
    p.config.platforms.append(type(p.config.platforms[0])(key="wos", label="Web of Science"))
    p.save()
    add_file(p, "wos", _ris(tmp_path / "b.ris", 3, "beta"))
    import_all(p)
    rows = r.status_rows(p, "prisma_2020", to_check=True)
    assert [x["no"] for x in rows] == ["6"] and rows[0]["your_text"] == "outdated" and rows[0]["gaps_left"] == 0
    row9 = next(x for x in r.status_rows(p, "prisma_2020") if x["no"] == "9")
    assert row9["your_text_label"] == "written, covers what was missing"
    inp = r.status_rows(p, "prisma_2020", only=r.INPUT)
    assert inp and all(x["status"] == r.INPUT for x in inp) and "9" in [x["no"] for x in inp]
    with pytest.raises(ValueError, match="unknown status 'Done'"):
        r.status_rows(p, "prisma_2020", only="Done")
    rows_cli = json.loads(_cli(p, "report", "prisma", "status", "--to-check", "--json").output)
    assert rows_cli == r.status_rows(p, "prisma_2020", to_check=True)
    inp_cli = json.loads(_cli(p, "report", "prisma", "status", "--status", r.INPUT, "--json").output)
    assert inp_cli == inp
    bad = _cli(p, "report", "prisma", "status", "--status", "Done")
    assert bad.exit_code == 1 and "unknown status 'Done'" in bad.output


def test_status_lists_both_checklists(tmp_path):
    p = _imported(tmp_path)
    rows = {cl: r.status_rows(p, cl) for cl in r.CHECKLISTS}
    assert [x["no"] for x in rows["prisma_2020"]] == [it["no"] for it in r.catalogue()["prisma_2020"]]
    assert [x["no"] for x in rows["prisma_s"]] == [str(i) for i in range(1, 17)]
    assert all(set(x) >= {"status", "wider_status", "your_text", "gaps_left", "saved_for", "to_check"}
               for cl in rows for x in rows[cl])
    _status_cli(p)


def _cli(p, *args):
    from typer.testing import CliRunner

    from wider.cli import app

    return CliRunner().invoke(app, [*args, "--dir", str(p.root)], env={"COLUMNS": "200"})


def _api(p, who="rev1"):
    """The page's client (routes_reporting, D14), signing as ``who`` through the Reviewer header."""
    from fastapi.testclient import TestClient

    from wider.ui.app import create_app

    return TestClient(create_app(Project.load(p.root)), headers={"x-wider-author": who})


def _status_cli(p):
    for cl, flag, n in (("prisma_2020", "prisma-2020", 42), ("prisma_s", "prisma-s", 16)):
        out = _cli(p, "report", "prisma", "status", "--checklist", flag, "--json")
        assert out.exit_code == 0, out.output
        rows = json.loads(out.output)
        assert len(rows) == n and rows == r.status_rows(p, cl)
        text = _cli(p, "report", "prisma", "status", "--checklist", flag)
        assert text.exit_code == 0 and f"{r.TITLES[cl]}: {n} items" in text.output
        assert all(it["topic"] in text.output for it in r.catalogue()[cl])


def test_cli_edit_na_clear_and_errors(tmp_path):
    p = _imported(tmp_path)
    (tmp_path / "text.md").write_text("Text from a file.\n", encoding="utf-8")
    assert _cli(p, "report", "prisma", "edit", "6", "--file", str(tmp_path / "text.md"), "--by", "rev1").exit_code == 0
    assert r.read_text(p, "prisma_2020", "6")["text"] == "Text from a file."
    out = _cli(p, "report", "prisma", "edit", "6", "--from-draft", "--by", "rev1")
    assert out.exit_code == 0 and "undo: wider history undo" in out.output
    assert r.read_text(p, "prisma_2020", "6")["from_draft"] is True
    assert _cli(p, "report", "prisma", "edit", "1", "--where", "p. 3", "--by", "rev1").exit_code == 0
    assert r.read_text(p, "prisma_2020", "1")["where"] == "p. 3" and _summaries(p)[0] == "PRISMA 2020 item 1: location saved"
    for args, msg in ((("edit", "99", "--text", "x"), "unknown item '99' in PRISMA 2020"),
                      (("edit", "7", "--text", "x", "--covers", "nonsense"), "unknown element 'nonsense'"),
                      (("edit", "9", "--from-draft"), "no draft for this item"),
                      (("edit", "9"), "give --text"),
                      (("edit", "9", "--text", "x", "--from-draft"), "give one of"),
                      (("na", "12"), "a reason is required"),
                      (("edit", "6", "--from-item", "prisma-s:15"), "not a related item"),
                      (("edit", "6", "--from-item", "prisma-s:1"), "PRISMA-S item 1 has no text")):
        out = _cli(p, "report", "prisma", *args, "--by", "rev1")
        assert out.exit_code == 1 and msg in out.output, (args, out.output)
    assert _cli(p, "report", "prisma", "na", "12", "--reason", "no quantitative synthesis", "--by", "rev1").exit_code == 0
    assert r.show(p, "prisma_2020", "12", r.context(p))["status"] == r.NA
    assert _cli(p, "report", "prisma", "na", "12", "--clear", "--by", "rev1").exit_code == 0
    assert r.show(p, "prisma_2020", "12", r.context(p))["status"] == r.INPUT
    assert _cli(p, "report", "prisma", "clear", "6", "--by", "rev1").exit_code == 0
    assert r.read_text(p, "prisma_2020", "6")["text"] == ""


def test_show_cli_blocks_and_json(checked):  # noqa: F811
    artifacts.save_new_version(checked, "synonyms", SYN, origin="manual")
    archive(checked)
    out = _cli(checked, "report", "prisma", "show", "7")
    assert out.exit_code == 0, out.output
    text = " ".join(out.output.split())
    for block in ("PRISMA asks", "WIDER recorded", "Still missing", "PRISMA-S reports this in detail:"):
        assert block in text
    assert "PRISMA-S item 8:" in text and "search strings archive" in text
    js = json.loads(_cli(checked, "report", "prisma", "show", "10", "--checklist", "prisma-s", "--json").output)
    assert js["related_heading"] == "Part of PRISMA 2020 item 7:"
    assert [(x["checklist"], x["no"]) for x in js["related"]] == [("prisma_2020", "7")]
    assert set(js["related"][0]) >= {"topic", "status"}
    v = r.show(checked, "prisma_2020", "7", r.context(checked))
    js7 = json.loads(_cli(checked, "report", "prisma", "show", "7", "--json").output)
    assert all(js7[k] == v[k] for k in ("official", "section", "elements", "evidence", "missing"))


def test_report_group_keeps_old_invocation(checked):  # noqa: F811
    out = _cli(checked, "report", "--protocol", "main", "--set", "1")
    assert out.exit_code == 0, out.output
    assert (checked.root / "exports" / "report.md").exists() and (checked.root / "exports" / "report.json").exists()
    from typer.testing import CliRunner

    from wider.cli import app

    h = " ".join(CliRunner().invoke(app, ["report", "prisma", "--help"], env={"COLUMNS": "200"}).output.split())
    for cmd in ("status", "show", "edit", "na", "clear", "flow", "export"):
        assert f" {cmd} " in h, cmd


def test_reporting_folder_gitignored(tmp_path):
    p = _imported(tmp_path)
    gi = p.root / ".gitignore"
    assert "reporting/" in gi.read_text(encoding="utf-8").splitlines()
    gi.write_text("inputs/\nexports/\n", encoding="utf-8")          # a project made before this change
    r.save_text(p, "prisma_2020", "1", "Title text.", by="rev1")
    r.save_text(p, "prisma_2020", "2", "Abstract text.", by="rev1")
    assert gi.read_text(encoding="utf-8") == "inputs/\nexports/\nreporting/\n"


# ------------------------------------------------------------------------------------------------ drafts (D9)
def test_item_6_draft_no_model(tmp_path, monkeypatch):
    import wider.providers as providers

    def no_backend(*a, **k):
        raise AssertionError("a backend was created")

    for name in dir(providers):
        if name.startswith(("make", "get_provider", "provider_for")):
            monkeypatch.setattr(providers, name, no_backend)
    p = Project.init(tmp_path / "r", platforms=["scopus", "ebsco"])
    add_file(p, "scopus", _ris(tmp_path / "a.ris", 4, "alpha"))
    add_file(p, "ebsco", _ris(tmp_path / "e.ris", 3, "eb"))
    import_all(p)
    ctx = r.context(p)
    d = r.draft(p, "prisma_2020", "6", ctx)
    day = ctx["sources"][0]["imported_at"][:10]
    assert (f"We searched Scopus; the export was imported into WIDER on {day} [search date: not recorded by WIDER]."
            in d["text"])
    assert "[databases searched on EBSCOhost: not recorded by WIDER]" in d["text"]
    assert "PsycINFO" not in d["text"] and "We searched EBSCOhost and Scopus" not in d["text"]
    assert d["gaps"] == d["text"].count("[")


def test_viewing_records_no_look(checked):  # noqa: F811
    before = _records(checked), checked.store.scalar("SELECT COUNT(*) FROM history")
    ctx = r.context(checked)
    texts = {no: r.draft(checked, "prisma_2020", no, ctx)["text"] for no in ("8", "23c")}
    r.status_rows(checked, "prisma_2020", ctx=ctx)
    assert (_records(checked), checked.store.scalar("SELECT COUNT(*) FROM history")) == before
    assert ctx["recall"] is None
    assert all("[recall estimate: not recorded by WIDER]" in t for t in texts.values())


def test_viewing_records_no_look_with_stored_estimate(tmp_path):
    """With a stored estimate, item 8's draft quotes its outcome lines (bounds, level, method) as stored, and the draft,
    the status and `show 8` record no look and no History entry."""
    from tests.unit.test_results_dashboard import _quiet
    from tests.unit.test_validation_suggest import _drawn
    from wider.recall.estimate import estimate
    from wider.recall.validation import status

    p = _drawn(tmp_path)
    p.store.execute("INSERT INTO sources(platform, file_name, sha256, n_records, imported_at) "   # the fixture's
                    "VALUES ('scopus', 'a.ris', 'x', 600, '2026-01-01T00:00:00Z')")               # records' export
    estimate(p, "validation", "main", B=50)
    before = _quiet(p)
    ctx = r.context(p, "main")
    text = r.draft(p, "prisma_2020", "8", ctx)["text"]
    r.status_rows(p, "prisma_2020", "main", ctx=ctx)
    r.show(p, "prisma_2020", "8", ctx)
    assert _quiet(Project.find(p.root)) == before
    stored = [ln for ln in status(p, "validation")["lines"] if not ln.startswith(("Label the", "Estimate recall"))]
    assert ctx["recall"]["lines"] == stored and stored
    assert all(ln in text for ln in stored) and r.RECALL_GAP not in text
    assert any("%" in ln and "confidence" in ln for ln in stored)        # the bounds and the level, as stored


def test_item_8_draft_reads_set_rules(checked):  # noqa: F811
    from wider.recall.outputs import screening_sentences

    ctx = r.context(checked)
    text = r.draft(checked, "prisma_2020", "8", ctx)["text"]
    assert "with a blind third read on disagreement; decisions were combined by a 2-of-3 majority." in text
    rule = screening_sentences(checked, ctx["counts"])[0]          # methods_draft's wording of the rules (D9)
    assert rule.split(". ")[1] not in text and "Two independent reads" not in text
    assert "rev1" not in text                                     # no labeller name in a draft
    checked.config.protocol.screening.blind_third_read = False    # the current rule changes, the set's does not
    checked.config.protocol.screening.majority = 3
    checked.save()
    assert r.draft(checked, "prisma_2020", "8", r.context(checked))["text"] == text


# ------------------------------------------------------------------------------------------------ flow counts (D21)
def _scenario_counts(monkeypatch):
    """The scenario's numbers: 120 records from databases, 20 duplicates removed, 100 screened, 15 reports sought."""
    import wider.recall.outputs as outputs

    real = outputs.prisma_counts

    def fake(*a, **k):
        c = real(*a, **k)
        return {**c, "databases": [{"key": "scopus", "label": "Scopus", "n": 120}], "identified_databases": 120,
                "identified_other": 0, "duplicates_removed": 20, "excluded_file_records": 0, "screened": 100,
                "not_screened": 0, "sought": 15}

    monkeypatch.setattr(outputs, "prisma_counts", fake)


ASSESSED_LINE = "Reports assessed (13) minus reports excluded (7) is 6, but reports of included studies is 8."
IDENT_LINE = ("Records identified (130) minus records removed (20) is 110, but WIDER holds 100 records after removal, "
              "screened or not yet screened (registers and automation-tool records are not in WIDER).")


def own(checks):
    """The flow's own checks, without the provisional line on the duplicates (outputs.provisional_line)."""
    return [c for c in checks if not c.startswith("Provisional: ")]


def test_flow_counts_checks(checked, monkeypatch):  # noqa: F811
    _scenario_counts(monkeypatch)
    r.save_flow(checked, counts={"not_retrieved": 2, "reports": 8}, excluded=[("wrong design", 4), ("no outcome", 3)],
                by="rev1")
    st = r.flow_state(checked)
    assert own(st["checks"]) == [ASSESSED_LINE]
    box = {b["name"]: b for b in st["boxes"]}
    assert (box["assessed"]["n"], box["assessed"]["source"]) == (13, "derived")
    assert (box["reports_excluded"]["n"], box["registers"]["text"]) == (7, r.INPUT_NEEDED)
    assert box["screened"]["source"] == "wider" and box["reports"]["source"] == "researcher"
    r.save_flow(checked, counts={"registers": 10}, by="rev1")
    assert own(r.flow_state(checked)["checks"]) == [IDENT_LINE, ASSESSED_LINE]
    r.save_flow(checked, counts={"not_retrieved": 16, "studies": 9}, by="rev1")
    checks = own(r.flow_state(checked)["checks"])
    assert "Reports not retrieved (16) exceed reports sought for retrieval (15)." in checks
    assert "More studies than reports of included studies." in checks
    assert r.flow_state(checked)["outdated"] is None
    # the CLI and the page's routes: the same boxes and checks after the same saves
    out = _cli(checked, "report", "prisma", "flow", "--count", "studies=", "--count", "not_retrieved=2", "--by", "rev1")
    assert out.exit_code == 0 and "flow counts saved" in out.output, out.output
    assert ASSESSED_LINE in " ".join(out.output.split()) and "† entered by the researcher" in out.output
    c = _api(checked)
    res = c.post("/api/reporting/flow", json={"counts": {"registers": ""}, "excluded": [{"reason": "x", "n": 1}]})
    assert res.status_code == 200 and res.json()["changed"]
    st = r.flow_state(checked)
    page = c.get("/api/reporting/flow").json()
    assert res.json()["flow"] == page and page.pop("svg").startswith("<svg")
    assert page == json.loads(_cli(checked, "report", "prisma", "flow", "--json").output)
    assert own(st["checks"]) == ["Reports assessed (13) minus reports excluded (1) is 12, but reports of included studies is 8."]


def test_flow_save_and_undo(checked):  # noqa: F811
    from wider.core import journal

    before = _records(checked)
    first = r.save_flow(checked, counts={"reports": 8, "studies": 6}, by="rev1")
    second = r.save_flow(checked, counts={"reports": 9}, by="rev1")
    assert _records(checked) == before
    rows = journal.changes(checked, kind="report_text")
    assert [x["summary"] for x in rows] == ["PRISMA 2020 flow, main set 1: counts saved"] * 2
    assert r.save_flow(checked, counts={"reports": 9}, by="rev1") == {"changed": False, "change_id": None}
    journal.undo(checked, second["change_id"], author="rev1")
    assert r.flow_state(checked)["entered"] == {"studies": 6, "reports": 8}
    cleared = r.clear_flow(checked, by="rev1")
    assert cleared["changed"] and r.flow_state(checked)["entered"] == {}
    assert journal.changes(checked, kind="report_text")[0]["summary"] == "PRISMA 2020 flow, main set 1: counts cleared"
    assert not r.clear_flow(checked, by="rev1")["changed"]
    journal.undo(checked, cleared["change_id"], author="rev1")
    assert r.flow_state(checked)["entered"] == {"studies": 6, "reports": 8}
    assert first["changed"] and _records(checked) == before


def test_flow_outdated(checked, tmp_path):  # noqa: F811
    r.save_flow(checked, counts={"reports": 8}, by="rev1")
    checked.config.platforms[0].label = "Relabelled database"
    checked.save()
    checked.store.execute("UPDATE screen_result_sets SET label='renamed set' WHERE protocol='main'")
    assert r.flow_state(checked)["outdated"] is None
    add_file(checked, "scopus", _ris(tmp_path / "more.ris", 5, "extra"))
    import_all(checked)
    st = r.flow_state(checked)
    assert st["outdated"]["line"] == r.FLOW_OUTDATED and st["checks"][-1] == r.FLOW_OUTDATED
    changed = {x["box"]: x for x in st["outdated"]["changed"]}
    assert changed["identified_databases"]["now"] == changed["identified_databases"]["saved"] + 5


def test_flow_counts_per_set(checked):  # noqa: F811
    from wider.screen.protocol import run_protocol

    r.save_flow(checked, counts={"reports": 8}, excluded=[("wrong design", 2)], by="rev1")
    run_protocol(checked, "corpus", "main", new_set=True)
    st = r.flow_state(checked, "main", 2)
    assert st["result_set"] == 2 and st["entered"] == {} and st["excluded"] == []
    assert st["other_sets"] == "Counts were entered for main, set 1."
    assert st["outdated"] is None and r.FLOW_OUTDATED not in st["checks"]
    assert {b["name"]: b["text"] for b in st["boxes"]}["reports"] == r.INPUT_NEEDED
    assert r.flow_state(checked, "main", 1)["entered"] == {"reports": 8}
    assert r.flow_state(checked, "main", 1)["other_sets"] is None


def test_flow_bad_input(checked):  # noqa: F811
    before = _records(checked)
    for kw, msg in (({"counts": {"reports": -1}}, r.BAD_COUNT), ({"counts": {"reports": 2.5}}, r.BAD_COUNT),
                    ({"counts": {"reports": "2.5"}}, r.BAD_COUNT), ({"counts": {"reports": True}}, r.BAD_COUNT),
                    ({"excluded": [("wrong design", None)]}, r.BAD_ROW), ({"excluded": [("", 3)]}, r.BAD_ROW),
                    ({"excluded": [("wrong design", -2)]}, r.BAD_COUNT),
                    ({"counts": {"screened": 5}}, "Records screened is recorded by WIDER and cannot be entered"),
                    ({"counts": {"excluded": 5}}, "Records excluded is recorded by WIDER and cannot be entered"),
                    ({"counts": {"foo": 5}}, "unknown count foo; one of registers, automation_removed, not_retrieved, "
                                             "studies, reports")):
        with pytest.raises(ValueError) as e:
            r.save_flow(checked, by="rev1", **kw)
        assert str(e.value) == msg
    # the CLI exits 1 and the route answers 400 with the same words; nothing written by either
    c = _api(checked)
    for args, body, msg in ((("--count", "reports=-1"), {"counts": {"reports": -1}}, r.BAD_COUNT),
                            (("--count", "reports=2.5"), {"counts": {"reports": 2.5}}, r.BAD_COUNT),
                            (("--excluded", "wrong design="), {"excluded": [{"reason": "wrong design", "n": ""}]},
                             r.BAD_ROW),
                            (("--count", "screened=5"), {"counts": {"screened": 5}},
                             "Records screened is recorded by WIDER and cannot be entered"),
                            (("--count", "foo=5"), {"counts": {"foo": 5}}, "unknown count foo")):
        out = _cli(checked, "report", "prisma", "flow", *args, "--by", "rev1")
        assert out.exit_code == 1 and msg in " ".join(out.output.split()), (args, out.output)
        res = c.post("/api/reporting/flow", json=body)
        assert res.status_code == 400 and res.json()["detail"].startswith(msg), (body, res.text)
    assert _records(checked) == before and not (checked.root / "reporting").exists()


def test_flow_merge_and_remove(checked):  # noqa: F811
    r.save_flow(checked, counts={"not_retrieved": 2, "reports": 8, "studies": 6}, excluded=[("wrong design", 7)],
                by="rev1")
    r.save_flow(checked, counts={"registers": 10}, by="rev1")
    st = r.flow_state(checked)
    assert st["entered"] == {"registers": 10, "not_retrieved": 2, "studies": 6, "reports": 8}
    assert st["excluded"] == [{"reason": "wrong design", "n": 7}]
    r.save_flow(checked, counts={"registers": ""}, by="rev1")
    assert "registers" not in r.flow_state(checked)["entered"]
    r.save_flow(checked, excluded=[], by="rev1")
    st = r.flow_state(checked)
    assert st["excluded"] == [] and st["entered"] == {"not_retrieved": 2, "studies": 6, "reports": 8}
    # the page sends every field, an empty one as a removal: the same as naming every field
    r.save_flow(checked, counts={k: ("" if k != "reports" else "8") for k in r.ENTERED}, excluded=[("", "")],
                by="rev1")
    assert r.flow_state(checked)["entered"] == {"reports": 8}
    # the CLI merges and removes the same way ("NAME=" removes, --excluded none empties) ...
    assert _cli(checked, "report", "prisma", "flow", "--count", "studies=6", "--excluded", "wrong design=7",
                "--by", "rev1").exit_code == 0
    assert _cli(checked, "report", "prisma", "flow", "--count", "reports=", "--by", "rev1").exit_code == 0
    st = r.flow_state(checked)
    assert st["entered"] == {"studies": 6} and st["excluded"] == [{"reason": "wrong design", "n": 7}]
    assert _cli(checked, "report", "prisma", "flow", "--excluded", "none", "--by", "rev1").exit_code == 0
    assert r.flow_state(checked)["excluded"] == []
    # ... and so does the page, which sends every field; "Clear my counts" removes the file
    c = _api(checked)
    body = {"counts": {k: ("" if k != "registers" else 4) for k in r.ENTERED}, "excluded": [{"reason": "", "n": ""}]}
    assert c.post("/api/reporting/flow", json=body).json()["flow"]["entered"] == {"registers": 4}
    cleared = c.post("/api/reporting/flow/clear", json={}).json()
    assert cleared["changed"] and cleared["flow"]["entered"] == {}
    assert _summaries(checked)[0] == "PRISMA 2020 flow, main set 1: counts cleared"


def test_flow_before_import_or_screening(tmp_path, _project_fixture):  # noqa: F811
    blank = Project.init(tmp_path / "blank", platforms=["scopus"])
    for call in (lambda: r.flow_state(blank), lambda: r.save_flow(blank, counts={"reports": 1}, by="rev1"),
                 lambda: r.clear_flow(blank, by="rev1")):
        with pytest.raises(ValueError, match="^nothing imported yet"):
            call()
    p = _project_fixture
    st = r.flow_state(p)
    assert st["message"] == "no screening yet" and st["result_set"] is None
    assert all(c.startswith("Provisional: ") for c in st["checks"])   # only why the counts may still change
    assert {b["name"]: b["source"] for b in st["boxes"]}["identified_databases"] == "wider"
    with pytest.raises(ValueError, match="^no screening yet$"):
        r.save_flow(p, counts={"reports": 1}, by="rev1")
    # the CLI and the routes refuse with the same words
    for proj, msg in ((blank, "nothing imported yet"), (p, "no screening yet")):
        out = _cli(proj, "report", "prisma", "flow", "--count", "reports=1", "--by", "rev1")
        assert out.exit_code == 1 and msg in out.output, out.output
        res = _api(proj).post("/api/reporting/flow", json={"counts": {"reports": 1}})
        assert res.status_code == 400 and res.json()["detail"].startswith(msg)
    assert _api(blank).get("/api/reporting/flow").status_code == 400
    assert _api(p).get("/api/reporting/flow").json()["message"] == "no screening yet"
    assert json.loads(_cli(p, "report", "prisma", "flow", "--json").output)["message"] == "no screening yet"
    assert not (p.root / "reporting").exists() and not (blank.root / "reporting").exists()


def test_flow_route_not_a_checklist(checked):  # noqa: F811
    """`/api/reporting/flow` is the flow diagram, never a checklist called "flow" (D21); viewing writes nothing."""
    before = _records(checked)
    files = sorted(x for x in checked.root.rglob("*") if x.is_file() and "store.sqlite" not in x.name)
    c = _api(checked)
    st = c.get("/api/reporting/flow").json()
    assert {"boxes", "checks", "svg"} <= set(st) and st["protocol"] == "main" and st["result_set"] == 1
    assert c.get("/api/reporting/flowchart").status_code == 400
    cl = c.get("/api/reporting/prisma-s").json()
    assert cl["checklist"] == "prisma_s" and cl["protocol"] == "main" and cl["result_set"] == 1
    assert "main" in cl["choices"]["protocols"] and cl["choices"]["sets"]
    assert _records(checked) == before and not (checked.root / "reporting").exists()
    assert sorted(x for x in checked.root.rglob("*") if x.is_file() and "store.sqlite" not in x.name) == files


def test_items_official_wording():
    """Task 1.3: every item carries the published wording, copied unchanged, and the file names both sources."""
    from pathlib import Path

    cat = r.catalogue()
    assert all(it["official"].strip() for cl in cat for it in cat[cl])
    assert r.item("prisma_2020", "1")["official"] == "Identify the report as a systematic review."
    assert r.item("prisma_s", "13")["official"] == "For each search strategy, provide the date when the last search occurred."
    assert all(it["official"] != it["asks"] for cl in cat for it in cat[cl])
    head = (Path(r.__file__).parent / "prisma_items.yaml").read_text(encoding="utf-8").split("\nprisma_2020:")[0]
    for s in ("doi:10.1136/bmj.n71", "doi:10.1186/s13643-020-01542-z", "CC BY 4.0", "`related` is WIDER's mapping"):
        assert s in head


# ------------------------------------------------------------------------------------------------ exports (D10, D25)
def _export_texts(p):
    d = p.root / "exports" / r.EXPORT_DIR
    return {f.name: (f.read_bytes() if f.suffix == ".docx" else f.read_text(encoding="utf-8")) for f in sorted(d.iterdir())}


def _file_text(f):
    """A file's text; a .docx gives the text of every XML part, its runs joined."""
    if f.suffix != ".docx":
        return f.read_text(encoding="utf-8", errors="replace")
    with zipfile.ZipFile(f) as z:
        xml = " ".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xml"))
    return re.sub(r"<[^>]+>", "", xml)


def _exports_hold(p, word):
    return [str(f.relative_to(p.root)) for f in (p.root / "exports").rglob("*") if f.is_file()
            and f.suffix != ".zip" and word in _file_text(f)]


def test_export_files_and_labels(checked):  # noqa: F811
    r.save_text(checked, "prisma_2020", "8", "Two reviewers screened every record.", by="rev1")
    r.mark_not_applicable(checked, "prisma_2020", "12", reason="no effect measures were synthesised", by="rev1")
    svg = checked.root / "exports" / "prisma_2020.svg"
    if svg.exists():
        svg.unlink()
    out = r.export(checked)
    texts = _export_texts(checked)
    assert set(texts) == {"prisma_2020_checklist.md", "prisma_2020_checklist.csv", "prisma_2020_checklist.json",
                          "prisma_s_checklist.md", "prisma_s_checklist.csv", "prisma_s_checklist.json",
                          "methods_text.md", "prisma_2020_flow.svg", "prisma_2020_flow.json", "prisma_2020_flow.docx",
                          "prisma_2020_checklist.docx", "prisma_s_checklist.docx", "methods_documentation.docx",
                          ".written.json"}
    assert svg.exists() and "exports/prisma_2020.svg" in out["files"] and "prisma_2020.svg" not in texts
    assert out["message"].startswith("PRISMA reporting exports written: 14 files in exports/prisma_reporting/")
    md = texts["prisma_2020_checklist.md"]
    i8 = md.index("### 8. ")
    block = md[i8:md.index("### 9. ")]
    assert block.index("#### WIDER recorded") < block.index("#### Researcher text") < block.index(
        "Two reviewers screened every record.")
    assert "Not applicable: no effect measures were synthesised" in md[md.index("### 12. "):md.index("### 13a. ")]
    rows = {x["item"]: x for x in csv.DictReader(io.StringIO(texts["prisma_2020_checklist.csv"]))}
    assert tuple(next(csv.reader(io.StringIO(texts["prisma_2020_checklist.csv"])))) == r.CSV_COLUMNS
    assert rows["8"]["researcher text"] == "Two reviewers screened every record." and rows["8"]["WIDER evidence"]
    assert rows["12"]["not applicable reason"] == "no effect measures were synthesised"
    assert rows["8"]["saved for"] == "written for main, set 1"
    js = json.loads(texts["prisma_2020_checklist.json"])
    assert js["written_at"] and [it["no"] for it in js["items"]][:3] == ["1", "2", "3"]
    mt = texts["methods_text.md"]
    b8 = mt[mt.index("## PRISMA 2020 item 8: "):mt.index("## PRISMA 2020 item 9: ")]
    b6 = mt[mt.index("## PRISMA 2020 item 6: "):mt.index("## PRISMA 2020 item 7: ")]
    assert "Two reviewers screened every record." in b8 and r.WIDER_DRAFT not in b8
    assert f"*{r.WIDER_DRAFT}*" in b6
    assert "Not applicable: no effect measures were synthesised" in mt[mt.index("## PRISMA 2020 item 12: "):]
    assert "## PRISMA-S item 16: " in mt and "## PRISMA 2020 item 1: " not in mt


def test_show_7_points_to_prisma_s(checked):  # noqa: F811
    """PRISMA 2020 item 7 (the full search strategies) shows the related PRISMA-S items with their topic, status and
    text, under its heading; the page's link to PRISMA-S item 8 opens that item (test_reporting_js_node)."""
    ctx = r.context(checked)
    v = r.show(checked, "prisma_2020", "7", ctx)
    assert [x["no"] for x in v["related"]] == r.item("prisma_2020", "7")["related"]
    assert all(x["checklist"] == "prisma_s" for x in v["related"]) and "PRISMA-S" in v["related_heading"]
    s8 = next(x for x in v["related"] if x["no"] == "8")
    assert s8["topic"] == r.item("prisma_s", "8")["topic"] and s8["status"] in r.STATUSES


def test_export_cli_and_route_same(checked, tmp_path):  # noqa: F811
    """`wider report prisma export` and POST /api/reporting/export write the same files with the same content."""
    import shutil

    r.save_text(checked, "prisma_2020", "8", "Two reviewers screened every record.", by="rev1")
    checked.close()
    shutil.copytree(checked.root, tmp_path / "copy")
    copy = Project.load(tmp_path / "copy")
    out = _cli(copy, "report", "prisma", "export")
    assert out.exit_code == 0, out.output
    res = _api(checked).post("/api/reporting/export", json={})
    assert res.status_code == 200, res.text
    js = res.json()
    assert js["message"] in " ".join(out.output.split())
    assert all(f in out.output for f in js["files"])
    stamp = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?")

    def texts(root):
        d = root / "exports" / r.EXPORT_DIR
        return {f.name: stamp.sub("<time>", _file_text(f).replace(str(root), "<root>")) for f in sorted(d.iterdir())}

    page, cli = texts(checked.root), texts(copy.root)
    assert sorted(page) == sorted(cli) and "prisma_2020_checklist.docx" in page
    # .written.json holds the hashes of the Word files, which carry their write time: the same files are named
    assert {k for k in page if page[k] != cli[k]} <= {r.WRITTEN}
    assert json.loads(page[r.WRITTEN]).keys() == json.loads(cli[r.WRITTEN]).keys()
    assert _api(checked).post("/api/reporting/export", json={"protocol": "nope"}).status_code == 400


def test_export_nothing_imported(tmp_path):
    p = Project.init(tmp_path / "blank", platforms=["scopus"])
    with pytest.raises(ValueError, match="^nothing imported yet"):
        r.export(p)
    assert not (p.root / "exports" / r.EXPORT_DIR).exists() and not (p.root / "exports" / "prisma_2020.svg").exists()


def test_switching_set_does_not_outdate(checked):  # noqa: F811
    from wider.screen.protocol import run_protocol

    r.save_text(checked, "prisma_2020", "16a", "Counts for set 1.", by="rev1")
    run_protocol(checked, "corpus", "main", new_set=True)
    rows = {x["no"]: x for x in r.status_rows(checked, "prisma_2020", protocol="main", result_set=2)}
    assert rows["16a"]["your_text"] != "outdated" and rows["16a"]["saved_for"] == "main, set 1"
    r.export(checked, protocol="main", result_set=2)
    csv_rows = {x["item"]: x for x in csv.DictReader(io.StringIO(
        (checked.root / "exports" / r.EXPORT_DIR / "prisma_2020_checklist.csv").read_text(encoding="utf-8")))}
    assert csv_rows["16a"]["saved for"] == "written for main, set 1"


def test_saver_name_not_exported(checked):  # noqa: F811
    from wider.core import journal
    from wider.recall.outputs import write_package
    from wider.recall.report import share_bundle
    from wider.sample.labels import label

    rid = checked.store.scalar("SELECT record_id FROM sample_rows m JOIN samples s USING(sample_id) "
                               "WHERE s.name='validation' ORDER BY record_id DESC LIMIT 1")
    label(checked, "validation", rid, "lab-two", "include")
    first = r.save_text(checked, "prisma_2020", "8", "Text for item 8.", by="rev-one")
    r.save_text(checked, "prisma_s", "9", "Text for PRISMA-S item 9.", by="rev-one")
    journal.undo(checked, first["change_id"], author="rev-one")
    r.export(checked)
    share_bundle(checked)
    res = write_package(checked)
    assert _exports_hold(checked, "rev-one") == []
    z = zipfile.ZipFile(checked.root / res["path"])
    assert not [n for n in z.namelist() if b"rev-one" in z.read(n)]
    assert any(n.endswith("prisma_reporting/prisma_s_checklist.csv") for n in z.namelist())
    lines8 = next(it for it in json.loads((checked.root / "exports" / r.EXPORT_DIR / "prisma_2020_checklist.json")
                                          .read_text(encoding="utf-8"))["items"] if it["no"] == "8")["lines"]
    assert not any("lab-two" in x for x in lines8) and any(re.search(r"Researcher \d", x) for x in lines8)
    assert "rev-one" in (checked.root / "reporting" / "prisma_s" / "9.json").read_text(encoding="utf-8")
    assert {c["author"] for c in journal.changes(checked, kind="report_text")} == {"rev-one"}


def test_hidden_name_in_text_refuses_package(checked):  # noqa: F811
    from wider.core.sanitize import NameFound
    from wider.recall.outputs import write_package
    from wider.recall.report import share_bundle

    r.save_text(checked, "prisma_s", "9", "Peer review was done by rev1.", by="rev-one")
    r.export(checked)
    assert "Peer review was done by rev1." in _export_texts(checked)["prisma_s_checklist.csv"]
    assert "rev1" in (checked.root / "reporting" / "prisma_s" / "9.json").read_text(encoding="utf-8")
    for build in (lambda: share_bundle(checked), lambda: write_package(checked)):
        with pytest.raises(NameFound) as e:
            build()
        msg = str(e.value)
        assert re.search(r"PRISMA-S item 9: the name of Researcher \d+; edit your text", msg) and "rev1" not in msg
        assert "WIDER bug" not in msg
    assert not list((checked.root / "exports").glob("replication_package_*"))


def test_prisma_s_before_import(tmp_path):
    from wider.recall import prisma_s
    from wider.recall.outputs import write_package

    p = Project.init(tmp_path / "blank", platforms=["scopus"])
    res = prisma_s.write(p)
    md = (p.root / "exports" / "prisma_s.md").read_text(encoding="utf-8")
    items = json.loads((p.root / "exports" / "prisma_s.json").read_text(encoding="utf-8"))["items"]
    assert len(items) == 16 and all(it["status"] == r.INPUT and it["lines"] == ["nothing imported yet"] for it in items)
    assert md.count("Status: Researcher input needed") == 16 and res["message"].startswith("PRISMA-S written:")
    assert write_package(p)["path"]


def test_prisma_s_researcher_fields_name_scanned(checked, tmp_path):  # noqa: F811
    from wider.core.researchers import Attributor
    from wider.core.sanitize import NameFound, scan_with
    from wider.recall import prisma_s
    from wider.recall.report import share_bundle

    r.save_text(checked, "prisma_s", "9", "Checked with rev1.", by="rev-one")
    with pytest.raises(NameFound) as e:
        share_bundle(checked)
    assert "prisma_s.md line" in str(e.value) and "PRISMA-S item 9: " in str(e.value)
    # the archived search strategies (PRISMA-S item 8's WIDER lines) are bibliographic: a name there is not refused
    r.clear(checked, "prisma_s", "9", by="rev-one")
    rep = prisma_s.build(checked)
    it8 = next(it for it in rep["items"] if str(it["no"]) == "8")
    it8["lines"].append("TITLE-ABS-KEY(rev1 AND widget)")
    md = prisma_s.markdown(rep, "Synthetic")
    assert "TITLE-ABS-KEY(rev1 AND widget)" in md
    assert scan_with([("prisma_s.md", md), ("prisma_s.json", json.dumps(rep))], Attributor(checked)) == []


def test_report_text_not_an_intervention(tmp_path):
    from wider.core import journal
    from wider.core.annotations import add_note
    from wider.dedup.engine import run as dedup
    from wider.recall.report import interventions, write_report

    p = _imported(tmp_path)
    dedup(p)
    r.save_text(p, "prisma_2020", "8", "Text for item 8.", by="rev1")
    nine = r.save_text(p, "prisma_2020", "9", "Text for item 9.", by="rev1")
    journal.undo(p, nine["change_id"], author="rev1")
    add_note(p, p.store.scalar("SELECT MIN(record_id) FROM records"), "rev1", "A synthetic note.")
    iv = interventions(p)
    assert [k["kind"] for k in iv["by_kind"]] == ["note"] and iv["by_kind"][0]["count"] == 1
    assert not any("PRISMA" in e["summary"] for e in iv["entries"])
    md, js = write_report(p)
    sec = md.read_text(encoding="utf-8").split("## Researcher interventions", 1)[1]
    assert "PRISMA reporting text" not in sec and "item 9" not in sec and "- note: 1 entry" in sec
    assert [c["summary"] for c in journal.changes(p, kind="report_text")].count("PRISMA 2020 item 9: text saved") == 1


def test_export_writes_flow_files(checked, tmp_path):  # noqa: F811
    from wider.recall.outputs import prisma_counts

    r.save_flow(checked, counts={"not_retrieved": 1}, by="rev1")
    out = r.export(checked)
    assert {"exports/prisma_reporting/prisma_2020_flow.svg", "exports/prisma_reporting/prisma_2020_flow.json"} <= set(
        out["files"]) and "prisma_2020_flow.svg" in out["message"] and "prisma_2020_flow.json" in out["message"]
    d = checked.root / "exports" / r.EXPORT_DIR
    js = json.loads((d / "prisma_2020_flow.json").read_text(encoding="utf-8"))
    counts = {c["name"]: c for b in js["boxes"] for c in b["counts"]}
    c = prisma_counts(checked)
    assert counts["screened"]["n"] == c["screened"] and counts["screened"]["source"] == "wider"
    assert counts["not_retrieved"] == {"name": "not_retrieved", "label": "Reports not retrieved", "n": 1,
                                       "source": "researcher"}
    assert counts["assessed"]["n"] == c["sought"] - 1 and counts["assessed"]["source"] == "derived"
    if "excluded_models" in counts:
        assert sum(counts[k]["n"] for k in counts if k.startswith("excluded_")) == c["excluded"]
    assert "rev1" not in (d / "prisma_2020_flow.json").read_text(encoding="utf-8")
    assert (d / "prisma_2020_flow.svg").read_text(encoding="utf-8").startswith("<svg ")
    # the Word copy (D20) holds the same counts as the json, and no reviewer id
    import zipfile
    xml = zipfile.ZipFile(d / "prisma_2020_flow.docx").read("word/document.xml").decode()
    assert "prisma_2020_flow.docx" in out["message"] and "rev1" not in xml
    assert f"Records screened (n = {c['screened']:,})" in xml or f"Records screened (n = {c['screened']})" in xml
    pk = tmp_path / "pk"
    files = r.export(checked, folder=pk, packaged=True)["files"]
    assert (pk / "prisma_2020_flow.svg").exists() and (pk / "prisma_2020_flow.json").exists()
    assert (pk / "prisma_2020_flow.docx").exists()
    assert str(pk / "prisma_2020_flow.svg") in files


def test_package_carries_reporting_exports(checked):  # noqa: F811
    from wider.recall.outputs import write_package

    def diagrams():
        return checked.store.scalar("SELECT COUNT(*) FROM stage_results WHERE stage='recall' AND action='prisma'")

    n = diagrams()
    res = write_package(checked)
    assert diagrams() == n + 1
    z = zipfile.ZipFile(checked.root / res["path"])
    names = z.namelist()
    sub = sorted(x.split("/", 1)[1] for x in names if x.startswith(f"{r.EXPORT_DIR}/"))
    assert sub == sorted(["prisma_2020_checklist.csv", "prisma_s_checklist.csv", "methods_text.md",
                          "prisma_2020_checklist.docx", "prisma_s_checklist.docx", "methods_documentation.docx",
                          "prisma_2020_flow.docx", "prisma_2020_flow.svg", "prisma_2020_flow.json"])
    for root_file in ("prisma_2020_checklist.md", "prisma_2020_checklist.json", "prisma_s.md", "prisma_2020.svg"):
        assert names.count(root_file) == 1 and f"{r.EXPORT_DIR}/{root_file}" not in names
    manifest = z.read("MANIFEST.txt").decode("utf-8")
    assert all(f"  {r.EXPORT_DIR}/{x}" in manifest for x in sub)
    assert not (checked.root / "exports" / r.EXPORT_DIR / ".written.json").exists()
    write_package(checked)
    assert diagrams() == n + 2


def test_cli_flow_outdated_new_box(capsys):
    """The CLI twin of the flow tab's outdated list: a box new since the save reads "not in the diagram", not None."""
    from wider.cli_reporting import _print_flow

    _print_flow({"message": None, "protocol": "main", "result_set": 1, "boxes": [], "excluded": [], "other_sets": None,
                 "unreadable": False, "checks": [], "outdated": {"line": "x", "changed": [
                     {"box": "database:ebsco", "label": "Records identified from EBSCOhost", "saved": None, "now": 4}]}})
    out = capsys.readouterr().out
    assert "Records identified from EBSCOhost: not in the diagram when you saved, 4 now" in out and "None" not in out
