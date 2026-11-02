"""PRISMA 2020 checklist (results-dashboard-and-ris, design D7): items filled from the store by a fixed map, the rest
"not recorded by WIDER"; the CLI and the route write the same files; the replication package carries its own copy.
Synthetic, topic-neutral records only (the fake backend screens them)."""
import hashlib
import json
import re
import zipfile

import pytest

from tests.unit.test_result_sets import cli, client
from tests.unit.test_screen import _responder
from tests.unit.test_screen import project as _project_fixture  # noqa: F401 - pytest fixture
from wider.core.models import Platform
from wider.core.project import Project
from wider.providers import fake as fakemod
from wider.recall import prisma_2020, reporting
from wider.recall.outputs import prisma_counts, write_package
from wider.sample.labels import label
from wider.screen.protocol import run_protocol


@pytest.fixture
def checked(_project_fixture, tmp_path, monkeypatch):  # noqa: F811
    """Two platforms imported, `main` screened with passes A, B and the judge (every seventh call reads an included
    record as maybe, so the passes disagree on some), `validation` partly labelled."""
    from wider.ingest.importer import add_file, import_all

    counter = {"calls": 0}
    base = _responder(counter)

    def respond(req):
        out = base(req)
        return {**out, "decision": "maybe"} if counter["calls"] % 7 == 0 and out["decision"] == "include" else out

    orig = fakemod.FakeProvider.__init__
    monkeypatch.setattr(fakemod.FakeProvider, "__init__", lambda self, cfg, responder_=None: orig(self, cfg, respond))
    p = _project_fixture
    p.config.questions = ["How do synthetic widgets relate to synthetic outcomes?"]
    p.config.platforms.append(Platform(key="wos", label="Web of Science"))
    p.save()
    (tmp_path / "b.ris").write_text("".join(f"TY  - JOUR\nTI  - Extra synthetic paper {i}\nPY  - 2021\nER  -\n"
                                            for i in range(3)))
    add_file(p, "wos", tmp_path / "b.ris")
    import_all(p)
    run_protocol(p, "corpus", "main")
    rid = p.store.scalar("SELECT record_id FROM sample_rows m JOIN samples s USING(sample_id) WHERE s.name='validation' "
                         "ORDER BY record_id LIMIT 1")
    label(p, "validation", rid, "rev1", "exclude")
    return Project.find(p.root)


def items(d):
    return {it["no"]: it for it in d["items"]}


def test_items_filled_and_not_recorded(checked):
    p = checked
    pkg = write_package(p)["path"]
    r = prisma_2020.write(p)
    it = items(r)
    assert len(prisma_2020.ITEMS) == len(it) == 42 and r["path"] == "exports/prisma_2020_checklist.md"
    # 5: the research question and the codebook version the protocol screened with
    assert "research question: How do synthetic widgets relate to synthetic outcomes?" in it["5"]["lines"]
    assert any(ln.startswith("codebook v1 ") and "used by protocol main" in ln for ln in it["5"]["lines"])
    # 6: both platforms and their import dates
    six = "\n".join(it["6"]["lines"])
    assert "Scopus" in six and "Web of Science" in six and six.count("import date; search date not recorded") == 2
    # 7: PRISMA-S item 8 (full strategies) is linked through the related-items block, not an evidence line
    assert "8" in reporting.item("prisma_2020", "7")["related"] and not any("prisma_s.md" in ln for ln in it["7"]["lines"])
    # 8: each pass's model, the third read, the majority, records without a majority, the sample with its size
    eight = it["8"]["lines"]
    assert {"pass A: fake", "pass B: fake", "pass C: fake"} <= set(eight)
    assert "two independent reads; a blind third read on disagreement; majority 2 of 3; codebook-based decisions" in eight
    assert any(ln.startswith("records without a majority stay at maybe for the researcher's review: ") for ln in eight)
    n = p.store.scalar("SELECT n FROM samples WHERE name='validation'")
    from wider.core.researchers import Attributor
    assert f"probability sample validation: {n} records, labelled by {Attributor(p).who('rev1')}" in eight   # D5
    # 16a: the counts of prisma_2020.json for the set
    c = prisma_counts(p)
    sixteen = "\n".join(it["16a"]["lines"])
    for k in ("identified", "unique", "screened", "excluded", "sought"):
        assert f"{c[k]:,}" in sixteen
    assert f"records excluded: {c['excluded']:,}; included: {c['included_ta']:,}; maybe: {c['maybe_ta']:,}" in sixteen
    # 24a-24c, 11, 16b; 27 names the package and its manifest
    for no in ("24a", "24c", "11", "16b", "1", "13f", "23d"):
        assert it[no]["lines"] == ["not recorded by WIDER"] and not it[no]["filled"]
    assert it["24b"]["lines"][0] == "not recorded by WIDER"
    assert re.fullmatch(r"WIDER screening settings \(not a review protocol\): hash [0-9a-f]{12}", it["24b"]["lines"][1])
    assert it["27"]["lines"] == [f"replication package {pkg} (MANIFEST.txt, manifest.json)"]
    md = (p.root / r["path"]).read_text()
    assert "## Other information" in md and "### 16a. Study selection" in md and "doi:10.1136/bmj.n71" in md
    st = {no: x["status"] for no, x in it.items()}
    assert {no for no in ("5", "6", "7", "8", "16a", "27") if st[no] == "Partially documented"} == {"5", "6", "8", "16a", "27"}
    assert st["7"] == st["11"] == "Researcher input needed"   # no search string archived in this project
    assert all(x["researcher"]["text"] == "" for x in it.values())
    assert r["message"] == "PRISMA 2020 checklist written: " + ", ".join(
        f"{sum(v == s for v in st.values())} {w}" for s, w in (
            ("Automatically documented", "automatically documented"), ("Partially documented", "partially documented"),
            ("Researcher input needed", "need your input"), ("Not applicable", "not applicable")))
    js = json.loads((p.root / r["json_path"]).read_text())
    assert js["items"] == r["items"] and js["protocol"] == "main"


def test_nothing_imported(tmp_path):
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    with pytest.raises(ValueError, match="^nothing imported yet"):
        prisma_2020.write(p)
    assert not list(p.path("exports").glob("prisma_2020_checklist.*"))
    r = cli(p, "export", "prisma-checklist")
    assert r.exit_code == 1 and "nothing imported yet" in r.output
    res = client(p).post("/api/outputs/prisma-checklist")
    assert res.status_code == 400 and res.json()["detail"].startswith("nothing imported yet")
    assert not list(p.path("exports").glob("prisma_2020_checklist.*"))


def _strip_time(path):
    d = json.loads(path.read_text())
    d.pop("written_at")
    return d


def test_cli_and_route_write_same_files(checked):
    p = checked
    r = cli(p, "export", "prisma-checklist", "--protocol", "main")
    assert r.exit_code == 0, r.output
    assert "16a. Study selection: records identified" in r.output and "wrote exports/prisma_2020_checklist.md" in r.output
    md, js = p.root / "exports/prisma_2020_checklist.md", p.root / "exports/prisma_2020_checklist.json"
    md1, js1 = md.read_text(), _strip_time(js)
    res = client(p).post("/api/outputs/prisma-checklist?protocol=main")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["path"] == "exports/prisma_2020_checklist.md" and body["json_path"] == "exports/prisma_2020_checklist.json"
    assert md.read_text() == md1 and _strip_time(js) == js1
    assert items(body)["8"]["lines"] == js1["items"][7]["lines"]
    r = cli(p, "export", "prisma-checklist", "--protocol", "nope")
    assert r.exit_code == 1


def test_checklist_in_package(checked, tmp_path):
    p = checked
    standalone = prisma_2020.write(p)
    before = (p.root / standalone["path"]).read_text()
    assert ("### 27. Availability of data, code and other materials\n\nStatus: Researcher input needed\n\n"
            "#### WIDER recorded\n\n- not recorded by WIDER") in before
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    names = z.namelist()
    assert {"prisma_2020_checklist.md", "prisma_2020_checklist.json"} <= set(names)
    packed = z.read("prisma_2020_checklist.md").decode()
    assert "- this replication package (MANIFEST.txt, manifest.json)" in packed
    assert (p.root / standalone["path"]).read_text() == before            # exports/ keeps its own item 27
    manifest = z.read("MANIFEST.txt").decode()
    for n in ("prisma_2020_checklist.md", "prisma_2020_checklist.json"):
        assert f"{hashlib.sha256(z.read(n)).hexdigest()}  {n}" in manifest
    assert "prisma_2020_checklist.md" in {f["file"] for f in json.loads(z.read("manifest.json"))["files"]}
    blob = b"".join(z.read(n) for n in names)
    abstracts = [a for (a,) in p.store.query("SELECT abstract FROM records WHERE abstract IS NOT NULL")]
    assert abstracts and not any(a.encode() in blob for a in abstracts if len(a) > 40)
    # a package built before any import still builds, without the checklist
    e = Project.init(tmp_path / "empty", platforms=["scopus"])
    z0 = zipfile.ZipFile(e.root / write_package(e)["path"])
    assert not any(n.startswith("prisma_2020_checklist") for n in z0.namelist())


def test_researcher_text_in_checklist(checked):
    """WIDER's lines and the researcher's text stay apart (D10): item 9 keeps "not recorded by WIDER" under WIDER
    recorded and the text under Researcher text; item 12 reads Not applicable with the reason; no researcher name."""
    p = checked
    reporting.save_text(p, "prisma_2020", "9", "Two reviewers screened each record independently.", by="rev1")
    reporting.mark_not_applicable(p, "prisma_2020", "12", "No outcomes are synthesised in this scoping review.",
                                  by="rev1")
    r = prisma_2020.write(p)
    md = (p.root / r["path"]).read_text(encoding="utf-8")
    nine = md.split("### 9. ", 1)[1].split("\n### ", 1)[0]
    wider_part, researcher_part = nine.split("#### Researcher text", 1)
    assert "#### WIDER recorded" in wider_part and "- not recorded by WIDER" in wider_part
    assert "Two reviewers screened" not in wider_part and "Two reviewers screened each record independently." \
        in researcher_part
    twelve = md.split("### 12. ", 1)[1].split("\n### ", 1)[0]
    assert "Not applicable: No outcomes are synthesised in this scoping review." in twelve
    assert "Status: Not applicable" in twelve
    js = json.loads((p.root / r["json_path"]).read_text(encoding="utf-8"))
    it = items(js)
    assert it["9"]["lines"][0] == "not recorded by WIDER"
    assert it["9"]["researcher"]["text"] == "Two reviewers screened each record independently."
    assert it["12"]["researcher"]["not_applicable"] == "No outcomes are synthesised in this scoping review."
    assert it["12"]["status"] == "Not applicable"
    from wider.core.researchers import Attributor
    name = Attributor(p).who("rev1")
    blob = json.dumps(it["9"]["researcher"]) + json.dumps(it["12"]["researcher"])
    assert "rev1" not in blob and name not in blob
