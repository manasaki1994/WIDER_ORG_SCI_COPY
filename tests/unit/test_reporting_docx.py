"""The flow diagram of the reporting exports (prisma-reporting-assistant D19) and its Word twins (D17, D20)."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from wider.recall import flow_layout as fl

SVG = "{http://www.w3.org/2000/svg}"
ALLOWED = {"svg", "defs", "marker", "rect", "path", "text"}


def _counts(**kw):
    c = {"protocol": "main", "result_set": 1, "result_set_key": "main", "set_name": "main, set 1",
         "databases": [{"key": "ebsco", "label": "EBSCOhost (Business Source, PsycINFO, ERIC, ...)", "n": 40},
                       {"key": "scopus", "label": "Scopus", "n": 60}],
         "other": [{"key": "citations", "label": "Citation searching", "n": 5}],
         "identified_databases": 100, "identified_other": 5, "duplicates_removed": 20, "excluded_file_records": 3,
         "screened": 82, "not_screened": 0, "excluded": 70, "maybe_ta": 2, "sought": 12}
    return {**c, **kw}


def _elements(svg):
    root = ET.fromstring(svg)
    return root, [(e.tag.removeprefix(SVG), e) for e in root.iter()]


def _all_counts(layout):
    return {c["name"]: (c["n"], c["source"]) for b in layout["boxes"] for c in b["counts"]}


def test_flow_svg_in_skill_subset():
    entered = {"not_retrieved": 2, "studies": 6, "reports": 7}
    excluded = [{"reason": "wrong population", "n": 3}]
    split = {"models": 61, "researcher": 9}
    layout = fl.flow_boxes(_counts(), entered, excluded, split)
    svg = fl.flow_svg(layout)
    root, els = _elements(svg)
    assert root.get("width") == "680" and root.get("font-size") == "13"
    assert {t for t, _ in els} <= ALLOWED and "tspan" not in svg and "transform" not in svg
    for t, e in els:
        for k in ("fill", "stroke"):
            assert e.get(k) in (None, "none") or re.fullmatch(r"#[0-9a-f]{3}([0-9a-f]{3})?", e.get(k))
    in_marker = {id(x) for _, m in els if _ == "marker" for x in m.iter()}
    lines = [e for t, e in els if t == "path" and id(e) not in in_marker]
    assert lines and all(re.fullmatch(r"M \d+ \d+ [HV] \d+", e.get("d")) and e.get("marker-end") for e in lines)
    # every count from prisma_2020.json (the counts) or the entered counts, with its source
    got = _all_counts(layout)
    assert got["identified_databases"] == (100, "wider") and got["screened"] == (82, "wider")
    assert got["excluded"] == (70, "wider") and got["sought"] == (12, "wider")
    assert got["not_retrieved"] == (2, "researcher") and got["studies"] == (6, "researcher")
    assert got["assessed"] == (10, "derived") and got["reports_excluded"] == (3, "researcher")
    assert got["registers"] == (None, "missing") and got["automation_removed"] == (None, "missing")
    # the split sums to the excluded count
    assert got["excluded_models"][0] + got["excluded_researcher"][0] == got["excluded"][0]
    joined = " | ".join(ln for b in layout["boxes"] for ln in b["lines"])
    assert [e.text for t, e in els if t == "text"] == [h["text"] for h in layout["heads"]] + [
        ln for b in layout["boxes"] for ln in b["text"]] + layout["note_lines"]
    assert "Reports assessed for eligibility (n = 10†)" in joined and "Studies included in review (n = 6†)" in joined
    assert "by the models (n = 61); by a researcher (n = 9)" in joined
    assert fl.NOTE_DERIVED in " ".join(layout["notes"]) and fl.NOTE_MODELS in layout["notes"]
    # "researcher input needed" boxes dashed, filled boxes not
    by = {b["id"]: b for b in layout["boxes"]}
    assert by["removed"]["dashed"] and by["identified"]["dashed"] and not by["included"]["dashed"]
    rects = [e for t, e in els if t == "rect"]
    assert sum(bool(e.get("stroke-dasharray")) for e in rects) == sum(b["dashed"] for b in layout["boxes"])
    # a multi_database platform by its short name, never its catalogue label
    js = json.dumps(fl.flow_json(layout, [], "main", 1))
    assert "PsycINFO" not in svg and "PsycINFO" not in js and f"EBSCOhost (n = 40); {fl.MULTI_NOTE}" in joined
    assert json.loads(js)["boxes"][0]["counts"]
    # the notes sit under the diagram; text lines inside a box step evenly by 1.0 to 1.6 em
    for b in layout["boxes"]:
        ys = [float(e.get("y")) for t, e in els if t == "text" and b["x"] < float(e.get("x")) < b["x"] + b["w"]
              and b["y"] < float(e.get("y")) < b["y"] + b["h"]]
        assert len(ys) == len(b["text"]) and len({y2 - y1 for y1, y2 in zip(ys, ys[1:])}) <= 1
    assert 13 <= fl.STEP <= 1.6 * 13
    note_y = layout["note_y"]
    assert all(b["y"] + b["h"] <= note_y for b in layout["boxes"])


def test_flow_missing_and_not_screened():
    layout = fl.flow_boxes(_counts(protocol=None, result_set=None, result_set_key=None), {})
    got = _all_counts(layout)
    assert got["screened"] == (None, "wider") and got["assessed"] == (None, "missing")
    joined = " | ".join(ln for b in layout["boxes"] for ln in b["lines"])
    assert f"Records screened (n = {fl.NO_SCREENING})" in joined
    assert f"Studies included in review (n = {fl.INPUT_NEEDED})" in joined
    assert [b["id"] for b in layout["boxes"] if b["source"] == "missing"] == ["not_retrieved", "assessed",
                                                                            "reports_excluded", "included"]
    assert fl.DAGGER not in joined and layout["notes"] == [fl.NOTE_MODELS]


def test_flow_docx_counts_match(tmp_path):
    import zipfile

    import docx

    from wider.recall import reporting_docx as rd
    from wider.recall.docx_shapes import NS

    counts = _counts()
    entered = {"not_retrieved": 2, "studies": 6, "reports": 7}
    excluded = [{"reason": "wrong population", "n": 3}]
    layout = fl.flow_boxes(counts, entered, excluded, {"models": 61, "researcher": 9})
    path = rd.flow_docx(layout, tmp_path / "prisma_2020_flow.docx")
    d = docx.Document(str(path))
    assert d.core_properties.title == rd.FLOW_TITLE and d.core_properties.author == "WIDER"
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    groups = root.findall(f".//{{{NS['wpg']}}}wgp")
    assert len(groups) == 1
    shapes = groups[0].findall(f"{{{NS['wps']}}}wsp")
    texts = ["".join(t.text or "" for t in s.iter(f"{{{NS['w']}}}t")) for s in shapes]
    boxes = [t for t in texts if "n = " in t]
    assert len(boxes) == sum(any("n = " in ln for ln in b["lines"]) for b in layout["boxes"])
    wider = {v for k, v in counts.items() if isinstance(v, int)} | {x["n"] for x in counts["databases"] + counts["other"]}
    allowed = wider | set(entered.values()) | {x["n"] for x in excluded} | {61, 9} | {counts["sought"] - 2}
    found = [int(m.replace(",", "")) for t in boxes for m in re.findall(r"n = ([\d,]+)", t)]
    assert found and set(found) <= allowed
    assert "Reports assessed for eligibility (n = 10†)" in texts
    assert sum(t == "" for t in texts) == len(layout["arrows"])
    assert "PsycINFO" not in zipfile.ZipFile(path).read("word/document.xml").decode()
    body = [p.text for p in d.paragraphs]
    assert fl.NOTE_MODELS in body and any(p.startswith("Flow diagram template: Page MJ") and "CC BY 4.0" in p
                                          for p in body)


# ------------------------------------------------------------------------------------------------ checklists (D17, D18)
from tests.unit.test_outputs import SYN  # noqa: E402
from tests.unit.test_prisma_2020_checklist import checked  # noqa: E402, F401 - pytest fixture
from tests.unit.test_screen import project as _project_fixture  # noqa: E402, F401 - pytest fixture

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _out(p):
    from wider.recall import reporting as r

    return p.root / "exports" / r.EXPORT_DIR


def _cell_highlighted(cell):
    return any(run.font.highlight_color is not None for par in cell.paragraphs for run in par.runs)


def test_checklist_docx_official(checked):  # noqa: F811
    import docx

    from wider.recall import reporting as r

    r.save_text(checked, "prisma_2020", "8", "Two reviewers screened every record.", where="Methods, p. 6", by="rev1")
    r.mark_not_applicable(checked, "prisma_2020", "12", reason="no effect measures were synthesised", by="rev1")
    r.export(checked)
    cat = r.catalogue()
    for cl, name, cols, sections in (
            ("prisma_2020", "prisma_2020_checklist.docx",
             ["Section and Topic", "Item #", "Checklist item", "Location where item is reported"],
             ["TITLE", "ABSTRACT", "INTRODUCTION", "METHODS", "RESULTS", "DISCUSSION", "OTHER INFORMATION"]),
            ("prisma_s", "prisma_s_checklist.docx",
             ["Section/topic", "Item #", "Checklist item", "Location where item is reported"],
             ["INFORMATION SOURCES AND METHODS", "SEARCH STRATEGIES", "PEER REVIEW", "MANAGING RECORDS"])):
        d = docx.Document(str(_out(checked) / name))
        assert d.core_properties.title == f"{r.TITLES[cl]} checklist" and d.core_properties.author == "WIDER"
        assert d.core_properties.last_modified_by == "WIDER"
        sec = d.sections[0]
        assert (round(sec.page_width.cm, 1), round(sec.page_height.cm, 1)) == (21.0, 29.7)
        assert {round(m.cm, 2) for m in (sec.left_margin, sec.right_margin, sec.top_margin, sec.bottom_margin)} == {2.5}
        (t,) = d.tables
        assert t.style.name == "PRISMA checklist" and t.style.font.name == "Arial" and t.style.font.size.pt == 9
        assert t.style.base_style.name == "Table Grid"
        assert [c.text for c in t.rows[0].cells] == cols
        assert t.rows[0]._tr.trPr.find(f"{W}tblHeader") is not None
        assert not any(run.font.name or run.font.size or run.font.bold for row in t.rows for c in row.cells
                       for par in c.paragraphs for run in par.runs)
        section_rows = [row for row in t.rows[1:] if len({id(c._tc) for c in row.cells}) == 1]
        assert [row.cells[0].text for row in section_rows] == sections
        assert all(row.cells[0]._tc.tcPr.find(f"{W}shd").get(f"{W}fill") == "D9D9D9" for row in section_rows)
        items = {row.cells[1].text: row for row in t.rows[1:] if len({id(c._tc) for c in row.cells}) == 4}
        assert list(items) == [it["no"] for it in cat[cl]]
        assert all(items[it["no"]].cells[2].text == it["official"] for it in cat[cl])
        text = "\n".join(c.text for row in t.rows for c in row.cells)
        for word in (r.AUTO, r.PART, "rev1", "✓"):
            assert word not in text
        assert not any(c.text in r.STATUSES for row in t.rows for c in row.cells)
        body = [p.text for p in d.paragraphs]
        assert any("CC BY 4.0" in p and r.SOURCES[cl].split()[0] in p for p in body)
        if cl == "prisma_2020":
            assert items["8"].cells[3].text == "Methods, p. 6" and not _cell_highlighted(items["8"].cells[3])
            assert items["12"].cells[3].text == "Not applicable: no effect measures were synthesised"
            assert items["16a"].cells[3].text == "Researcher input needed: page or section"
            assert _cell_highlighted(items["16a"].cells[3])
            assert items["13b"].cells[0].text == "" and items["13a"].cells[0].text == "Synthesis methods"


def test_methods_docx_content(checked):  # noqa: F811
    import docx

    from wider.construct import artifacts
    from wider.recall import reporting as r
    from wider.search.archive import archive, archive_dir, entries

    artifacts.save_new_version(checked, "synonyms", SYN, origin="manual")
    archive(checked)
    r.save_text(checked, "prisma_s", "9", "No limits were applied.", by="rev1")
    r.mark_not_applicable(checked, "prisma_2020", "12", reason="no effect measures were synthesised", by="rev1")
    r.export(checked)
    d = docx.Document(str(_out(checked) / "methods_documentation.docx"))
    assert d.core_properties.title == "Methods documentation" and d.core_properties.author == "WIDER"
    h1 = [p.text for p in d.paragraphs if p.style.name == "Heading 1"]
    assert h1[1:] == ["Methods text", "Search strategies", "Reporting status"]
    paras = d.paragraphs
    texts = [p.text for p in paras]
    i9 = texts.index("PRISMA-S item 9: Limits and restrictions") if "PRISMA-S item 9: Limits and restrictions" in texts \
        else next(i for i, t in enumerate(texts) if t.startswith("PRISMA-S item 9"))
    assert texts[i9 + 1] == "Researcher text" and texts[i9 + 2] == "No limits were applied."
    i12 = next(i for i, t in enumerate(texts) if t.startswith("PRISMA 2020 item 12"))
    assert texts[i12 + 1] == "Not applicable: no effect measures were synthesised"
    i8 = next(i for i, t in enumerate(texts) if t.startswith("PRISMA 2020 item 8"))
    assert texts[i8 + 1] == r.WIDER_DRAFT
    gaps = [run.text for p in paras for run in p.runs if run.font.highlight_color is not None]
    assert gaps and all(re.fullmatch(r"\[[^\[\]]+\]", g) for g in gaps)
    assert all(re.search(r"\[[^\[\]]*(not recorded by WIDER|researcher input needed)\]", g) for g in gaps)
    assert "[search date: not recorded by WIDER]" in gaps
    mono = "\n".join(p.text for p in paras if p.style.name == "Search string")
    for e in entries(checked):
        string = "\n".join((archive_dir(checked) / e["file"]).read_text(encoding="utf-8").rstrip("\n").split("\n")[3:])
        assert string in mono
    assert not any(run.font.name for p in paras for run in p.runs)
    status_tables = d.tables
    assert len(status_tables) == 2
    assert [c.text for c in status_tables[0].rows[0].cells] == ["Item", "WIDER status", "Your text", "Still missing"]
    rows = {row.cells[0].text: row for row in status_tables[0].rows[1:]}
    assert rows["12"].cells[1].text == r.NA and rows["4"].cells[1].text == r.PART
    footer = d.sections[0].footer.paragraphs[0].text
    assert footer.startswith("Prepared with WIDER ") and footer.endswith("drafts are not reviewed unless marked.")
    whole = "\n".join(texts)
    assert "rev1" not in whole


def test_export_keeps_edited_docx(checked, monkeypatch):  # noqa: F811
    from wider.recall import reporting as r

    out = _out(checked)
    first = r.export(checked)
    assert first["kept"] == [] and (out / ".written.json").exists()
    assert set(json.loads((out / ".written.json").read_text())) == {
        "prisma_2020_flow.svg", "prisma_2020_checklist.docx", "prisma_s_checklist.docx",
        "methods_documentation.docx", "prisma_2020_flow.docx"}
    again = r.export(checked)
    assert again["kept"] == []            # nothing edited: overwritten, nothing kept
    m = out / "methods_documentation.docx"
    m.write_bytes(m.read_bytes() + b"edited in Word")
    svg = out / "prisma_2020_flow.svg"
    svg.write_text(svg.read_text(encoding="utf-8").replace("Records", "Records!"), encoding="utf-8")
    res = r.export(checked)
    assert len(res["kept"]) == 2 and all(" (edited 20" in k for k in res["kept"])
    stamp = re.search(r"\(edited (\d{4}-\d\d-\d\d \d{6})\)", res["kept"][0]).group(1)
    assert {k.split(" (edited")[0] for k in res["kept"]} == {"methods_documentation", "prisma_2020_flow"}
    kept_m = out / f"methods_documentation (edited {stamp}).docx"
    assert kept_m.read_bytes().endswith(b"edited in Word") and "your edited copies kept as" in res["message"]
    assert not m.read_bytes().endswith(b"edited in Word")
    # the same second again: the clash gets " (2)"
    import datetime as dt

    class Same(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.strptime(stamp, "%Y-%m-%d %H%M%S")

    monkeypatch.setattr(dt, "datetime", Same)
    m.write_bytes(m.read_bytes() + b"edited again")
    res2 = r.export(checked)
    assert res2["kept"] == [f"methods_documentation (edited {stamp}) (2).docx"]
    # no .written.json: every existing file counts as edited and is kept
    (out / ".written.json").unlink()
    res3 = r.export(checked)
    assert len(res3["kept"]) == 5 and all(" (edited " in k for k in res3["kept"])


def test_locked_docx_writes_nothing(checked, monkeypatch):  # noqa: F811
    import builtins

    import pytest

    from wider.recall import reporting as r

    out = _out(checked)
    r.export(checked)
    before = {f.name: f.stat().st_mtime_ns for f in out.iterdir()}
    svg = checked.root / "exports" / "prisma_2020.svg"
    svg_before = svg.stat().st_mtime_ns
    n_results = checked.store.scalar("SELECT COUNT(*) FROM stage_results")
    real = builtins.open

    def locked(file, mode="r", *a, **kw):
        if str(file).endswith("prisma_s_checklist.docx") and mode == "r+b":
            raise PermissionError(13, "locked")
        return real(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", locked)
    with pytest.raises(ValueError, match=r"^close prisma_s_checklist.docx in Word and export again$"):
        r.export(checked)
    monkeypatch.setattr(builtins, "open", real)
    assert {f.name: f.stat().st_mtime_ns for f in out.iterdir()} == before
    assert svg.stat().st_mtime_ns == svg_before
    assert checked.store.scalar("SELECT COUNT(*) FROM stage_results") == n_results


def test_export_without_python_docx(checked, monkeypatch):  # noqa: F811
    import sys

    from wider.recall import reporting as r

    monkeypatch.setitem(sys.modules, "docx", None)
    res = r.export(checked)
    names = {f.name for f in _out(checked).iterdir()}
    assert not any(n.endswith(".docx") for n in names)
    assert {"prisma_2020_checklist.md", "prisma_s_checklist.csv", "methods_text.md", "prisma_2020_flow.svg"} <= names
    assert res["message"].endswith("; Word files not written: python-docx is not installed (pip install -e .).")
    assert json.loads((_out(checked) / ".written.json").read_text()) == {"prisma_2020_flow.svg": r._sha(
        _out(checked) / "prisma_2020_flow.svg")}


def _split_run(paragraph, text, at):
    """Write ``text`` into ``paragraph`` as two runs split at ``at`` (what Word does after an edit)."""
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)
    paragraph.add_run(text[:at])
    paragraph.add_run(text[at:])


def test_docx_is_secret_scanned(tmp_path, monkeypatch):
    import io

    from docx import Document

    from wider.core.sanitize import docx_text, scan_entries, scan_tree

    key = "sk-ant-" + "A1b2C3d4" * 4
    doc = Document()
    doc.add_paragraph("Methods & search")
    _split_run(doc.add_paragraph(), f"The key {key} was used.", 12)
    doc.core_properties.comments = "Bearer " + "x" * 30
    buf = io.BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    assert key not in data.decode("latin-1")            # deflated and split: a byte search would miss it
    text = docx_text(data).splitlines()
    assert text[:2] == ["Methods & search", f"The key {key} was used."]
    found = scan_entries([("prisma_reporting/methods_documentation.docx", data)])
    assert ("prisma_reporting/methods_documentation.docx", 2, "anthropic key") in {(f.file, f.line, f.kind) for f in found}
    assert any(f.kind == "bearer token" for f in found)
    (tmp_path / "methods_documentation.docx").write_bytes(data)
    assert any(f.kind == "anthropic key" for f in scan_tree(tmp_path))


def test_docx_is_name_scanned(checked):  # noqa: F811
    import io

    import pytest
    from docx import Document

    from wider.core.researchers import Attributor
    from wider.core.sanitize import NameFound, scan_with
    from wider.recall import reporting as r
    from wider.recall.outputs import write_package

    r.save_text(checked, "prisma_2020", "9", where="Methods, screening paragraph", by="rev-one")
    r.export(checked)
    doc = Document(str(_out(checked) / "prisma_2020_checklist.docx"))
    row = next(rw for rw in doc.tables[0].rows if rw.cells[1].text == "9")
    _split_run(row.cells[3].paragraphs[0], "Methods, checked by rev1", 22)
    buf = io.BytesIO()
    doc.save(buf)
    found = scan_with([("prisma_reporting/prisma_2020_checklist.docx", buf.getvalue())], Attributor(checked))
    assert len(found) == 1 and re.fullmatch(r"PRISMA 2020 item 9: the name of Researcher \d+; edit your text",
                                            found[0].kind)
    # the researcher's own text with the name refuses the package (the Word files are scanned, not only the csv)
    r.save_text(checked, "prisma_2020", "9", where="Methods, checked by rev1", by="rev-one")
    with pytest.raises(NameFound) as e:
        write_package(checked)
    assert "prisma_reporting/prisma_2020_checklist.docx line" in str(e.value) and "rev1" not in str(e.value)
    assert "WIDER bug" not in str(e.value)
    assert not list((checked.root / "exports").glob("replication_package_*"))


def test_search_term_equal_to_hidden_name(checked):  # noqa: F811
    import csv

    from docx import Document

    from wider.core.provenance import sha256_text, utcnow
    from wider.core.researchers import Attributor
    from wider.core.sanitize import scan_with
    from wider.recall import reporting as r
    from wider.search.archive import FIELDS, archive_dir

    platform = checked.store.scalar("SELECT platform FROM sources ORDER BY source_id LIMIT 1")
    sdir = archive_dir(checked)
    sdir.mkdir(parents=True, exist_ok=True)
    string = "TITLE-ABS-KEY(rev1 AND widget)"
    (sdir / "s.txt").write_text(f"# a\n# b\n# c\n{string}\n", encoding="utf-8")
    with open(sdir / "archive.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerow({"archived_at": utcnow(), "platform": platform, "label": platform, "synonyms_version": 1,
                    "synonyms_status": "frozen", "sha256": sha256_text(string), "file": "s.txt", "reason": "test"})
    r.export(checked)
    path = _out(checked) / "methods_documentation.docx"
    texts = [p.text for p in Document(str(path)).paragraphs]
    assert "TITLE-ABS-KEY(rev1 AND widget)" in texts
    assert scan_with([("prisma_reporting/methods_documentation.docx", path.read_bytes())], Attributor(checked)) == []
    assert scan_with([("other.docx", path.read_bytes())], Attributor(checked))   # only the strategies are exempt
