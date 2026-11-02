"""reviewer-roles-and-blinding, groups 2 and 6: every abstract a record has had is kept as a numbered version, and each
model call and label names the version it read (abstract-enrichment, "Every abstract version is kept"). Synthetic
records only; lookups go to a MockTransport client."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tests.unit.helpers import consent, mock_client
from tests.unit.test_dedup import _project_with_imports, queue_rows
from tests.unit.test_screen import _fake
from tests.unit.test_screen import project as screened  # noqa: F401 - the fixture
from wider.cli import app as cli_app
from wider.core import questions as qs
from wider.core.project import Project
from wider.core.store import SCHEMA_VERSION
from wider.dedup.engine import decide, run
from wider.enrich import abstracts
from wider.enrich.fetch import enrich
from wider.sample.labels import label
from wider.screen.engine import screen
from wider.ui.app import create_app

runner = CliRunner()
TWELVE = "one two three four five six seven eight nine ten eleven twelve"


def _cli(p, *args):
    return runner.invoke(cli_app, [*args, "--dir", str(p.root)], env={"COLUMNS": "200"})


def _openalex_240(request):
    if "api.openalex.org/works/https://doi.org/" in str(request.url):
        import httpx
        return httpx.Response(200, json={"abstract_inverted_index": {f"w{i}": [i] for i in range(240)}})
    import httpx
    return httpx.Response(404)


def test_versions_kept_on_fill(screened, monkeypatch):  # noqa: F811
    """Imported with 12 words, screened, filled from OpenAlex with 240 words, labelled: version 1 (imported) and
    version 2 (OpenAlex); the call names 1 and the label 2; the current abstract is the filled one."""
    p = screened
    rid = p.store.scalar("SELECT sr.record_id FROM sample_rows sr JOIN samples s ON s.sample_id=sr.sample_id "
                         "WHERE s.name='development' ORDER BY sr.queue_pos LIMIT 1")
    # this record came in with a 12-word abstract (the import's text, version 1)
    p.store.execute("UPDATE records SET abstract=?, abstract_words=12, thin=1 WHERE record_id=?", (TWELVE, rid))
    p.store.execute("UPDATE record_abstracts SET abstract=?, words=12 WHERE record_id=? AND version=1", (TWELVE, rid))
    _fake(monkeypatch, {"calls": 0})
    res = screen(p, "sample:development", pass_name="A")
    assert p.store.scalar("SELECT abstract_version FROM screen_calls WHERE run_id=? AND record_id=?", (res["run_id"], rid)) == 1
    consent(p)
    enrich(p, pause_s=0, client=mock_client(_openalex_240))
    label(p, "development", rid, "R1", "include")
    out = _cli(p, "enrich", "--versions", str(rid), "--json")
    assert out.exit_code == 0, out.output
    got = json.loads(out.output)
    assert [(v["version"], v["source_word"], v["words"]) for v in got["versions"]] == [(1, "imported", 12), (2, "OpenAlex", 240)]
    assert got["versions"][0]["at"] and got["versions"][1]["at"] and got["current"] == 2
    assert p.store.scalar("SELECT abstract_version FROM labels WHERE record_id=? AND reviewer_id='R1'", (rid,)) == 2
    cur = p.store.one("SELECT abstract, abstract_words, abstract_source FROM records WHERE record_id=?", (rid,))
    assert cur["abstract_words"] == 240 and cur["abstract_source"] == "openalex"
    assert got["versions"][1]["abstract"] == cur["abstract"]
    c = TestClient(create_app(p))
    assert c.get(f"/api/records/{rid}/abstracts").json() == got
    text = _cli(p, "enrich", "--versions", str(rid)).output
    assert "Abstract versions of record" in text and "1  imported, 12 words" in text and "2  OpenAlex, 240 words" in text


def test_versions_kept_on_paste(tmp_path):
    """An abstract pasted as an answer adds a version "pasted by the researcher"; undoing the answer takes it away."""
    p = _project_with_imports(tmp_path)
    run(p)
    rid = p.store.scalar("SELECT record_id FROM records WHERE title='Unique paper one'")
    qid = qs.raise_question(p, "enrich_unresolved", str(rid), "Unique paper one", "No source had an abstract.",
                            record_id=rid)
    qs.answer(p, qid, "abstract", "An abstract the researcher pasted from the full text of this paper.", by="ana")
    vs = abstracts.versions(p.store, rid)
    assert [(v["version"], v["source_word"]) for v in vs] == [(1, "imported"), (2, "pasted by the researcher")]
    assert vs[-1]["abstract"] == p.store.scalar("SELECT abstract FROM records WHERE record_id=?", (rid,))
    from wider.core import journal
    journal.undo(p, journal.last_change_id(), author="ana")
    assert [v["version"] for v in abstracts.versions(p.store, rid)] == [1]


def test_versions_kept_on_dedup_rerun(tmp_path):
    """A re-run that brings a longer export abstract adds a version "export"; a merge moves the merged-away
    record's versions after the survivor's, renumbers the labels and calls moved with them, and the survivor's latest
    version is its current abstract."""
    p = _project_with_imports(tmp_path)
    run(p)
    one = p.store.scalar("SELECT record_id FROM records WHERE title='Unique paper one'")
    assert [v["source"] for v in abstracts.versions(p.store, one)] == ["imported"]
    # the next export of file a carries a longer abstract for "Unique paper one"
    raw = p.store.scalar("SELECT raw_id FROM raw_records WHERE title='Unique paper one' LIMIT 1")
    longer = "A much longer abstract from a later export of this file that has many more words than before."
    p.store.execute("UPDATE raw_records SET abstract=? WHERE raw_id=?", (longer, raw))
    q = queue_rows(p)[0]
    rec = lambda raw_id: p.store.scalar("SELECT m.record_id FROM records m JOIN dedup_clusters c "   # noqa: E731
                                        "ON c.cluster_id=m.cluster_id WHERE c.raw_id=?", (raw_id,))
    a_rec, b_rec = rec(q["raw_a"]), rec(q["raw_b"])
    # b gets a second version (pasted), a label and a model call on version 1 of its own
    abstracts.add_version(p.store, b_rec, "A pasted abstract for the merged-away record.", "researcher")
    p.store.execute("UPDATE records SET abstract=?, abstract_source='researcher' WHERE record_id=?",
                    ("A pasted abstract for the merged-away record.", b_rec))
    p.store.execute("INSERT INTO samples(name,kind,design,n,created_at) VALUES('dev','purposive','{}',1,'t')")
    sid = p.store.scalar("SELECT sample_id FROM samples WHERE name='dev'")
    p.store.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(?,?,1)", (sid, b_rec))
    label(p, "dev", b_rec, "R1", "include")
    p.store.execute("UPDATE labels SET abstract_version=1 WHERE record_id=?", (b_rec,))
    p.store.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) VALUES('r1','screen','s','h','done','t','d')")
    p.store.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at,abstract_version) "
                    "VALUES('r1',?,'A','x',1,'t',2)", (b_rec,))
    n_a = len(abstracts.versions(p.store, a_rec))
    decide(p, q["raw_a"], q["raw_b"], "duplicate", by="tester")
    run(p)
    assert [v["source"] for v in abstracts.versions(p.store, one)] == ["imported", "export"]
    assert abstracts.versions(p.store, one)[-1]["abstract"] == longer
    survivor = a_rec if p.store.scalar("SELECT 1 FROM records WHERE record_id=?", (a_rec,)) else b_rec
    assert survivor == a_rec and not p.store.scalar("SELECT 1 FROM records WHERE record_id=?", (b_rec,))
    vs = abstracts.versions(p.store, a_rec)
    assert [v["version"] for v in vs][:n_a + 2] == list(range(1, n_a + 3))
    assert vs[n_a + 1]["source"] == "researcher"           # b's version 2 is now a's version n_a + 2
    assert p.store.scalar("SELECT abstract_version FROM labels WHERE reviewer_id='R1'") == n_a + 1
    assert p.store.scalar("SELECT abstract_version FROM screen_calls WHERE run_id='r1'") == n_a + 2
    assert vs[-1]["abstract"] == (p.store.scalar("SELECT abstract FROM records WHERE record_id=?", (a_rec,)) or "")


def test_unknown_record(tmp_path, monkeypatch):
    """An unknown id: "no record 99999", exit 1, 404; --versions never asks consent or builds an HTTP client."""
    p = _project_with_imports(tmp_path)
    run(p)
    import wider.cli as cli_mod
    import wider.core.lookups as lk
    monkeypatch.setattr(cli_mod, "ensure_consent", lambda *a, **k: pytest.fail("consent asked"))
    monkeypatch.setattr(lk, "lookup_client", lambda *a, **k: pytest.fail("HTTP client made"))
    out = _cli(p, "enrich", "--versions", "99999")
    assert out.exit_code == 1 and "no record 99999" in out.output
    rid = p.store.scalar("SELECT MIN(record_id) FROM records")
    assert _cli(p, "enrich", "--versions", str(rid)).exit_code == 0
    assert TestClient(create_app(p)).get("/api/records/99999/abstracts").status_code == 404


def _make_v8(root):
    """Turn a store into the state a v8 project had: no v9 tables or columns, schema version 8."""
    p = Project.load(root)
    st = p.store
    for t in ("record_abstracts", "review_packages", "review_package_items"):
        st.execute(f"DROP TABLE {t}")
    for t, cols in (("labels", ["made_in", "abstract_version", "tags"]), ("screen_calls", ["abstract_version"]),
                    ("label_revisions", ["cause", "package_id"])):
        for c in cols:
            st.execute(f"ALTER TABLE {t} DROP COLUMN {c}")
    st.set_meta("schema_version", "8")
    p.close()


def test_upgrade_builds_versions(tmp_path):
    """A v8 project with a record filled from Crossref and one never enriched: version 1 from the imported abstract
    and version 2 dated by the fill; earlier calls and labels get the version in force at their time; a label saved
    from the second-reviewer queue becomes ``blind queue``; opening it again adds nothing."""
    p = _project_with_imports(tmp_path)
    run(p)
    st = p.store
    filled = st.scalar("SELECT record_id FROM records WHERE title='Unique paper one'")
    plain = st.scalar("SELECT record_id FROM records WHERE doi='10.1000/abc'")
    imported = st.scalar("SELECT abstract FROM records WHERE record_id=?", (filled,)) or ""
    st.execute("UPDATE records SET abstract=?, abstract_words=9, abstract_source='crossref' WHERE record_id=?",
               ("A Crossref abstract with nine words in it ok.", filled))
    st.execute("INSERT INTO enrichment_log(record_id,source,status,words,fetched_at) "
               "VALUES(?,'crossref','filled',9,'2026-03-01T00:00:00+00:00')", (filled,))
    st.execute("INSERT INTO samples(sample_id,name,kind,design,n,created_at) VALUES(7,'validation','probability','{}',2,'t')")
    st.execute("INSERT INTO sample_rows(sample_id,record_id,queue_pos) VALUES(7,?,1),(7,?,2)", (filled, plain))
    st.execute("INSERT INTO reviewers(reviewer_id,name,created_at) VALUES('R1','R1','t'),('R2','R2','t')")
    st.execute("INSERT INTO runs(run_id,kind,stamp,spec_hash,status,started_at,dir) VALUES('r1','screen','s','h','done','t','d')")
    p.close()
    _make_v8(p.root)
    st = sqlite3.connect(p.root / "store.sqlite", isolation_level=None)   # rows an older version wrote, no upgrade yet
    for rid, at in ((filled, "2026-02-01T00:00:00+00:00"), (plain, "2026-02-01T00:00:00+00:00")):
        st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at) VALUES('r1',?,'A','x',1,?)", (rid, at))
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at) VALUES('r1',?,'B','x',1,"
               "'2026-04-01T00:00:00+00:00')", (filled,))
    st.execute("INSERT INTO screen_calls(run_id,record_id,pass,input_sha256,ok,at) VALUES('r1',?,'C','x',1,'')", (filled,))
    st.execute("INSERT INTO labels(sample_id,record_id,reviewer_id,decision,updated_at) VALUES(7,?,'R1','include',"
               "'2026-01-15T00:00:00+00:00'),(7,?,'R2','exclude','2026-03-15T00:00:00+00:00'),"
               "(7,?,'R2','include','2026-03-15T00:00:00+00:00')", (filled, filled, plain))
    st.execute("INSERT INTO queue_items(token,sample_id,record_id,mode,reviewer,created_at) VALUES('tk',7,?,'second','R2','t')",
               (plain,))
    st.execute("INSERT INTO history(at,author,kind,summary,items) VALUES('2026-03-15','R2','label',"
               "'R2 saved a second-reviewer label',?)", (json.dumps([{"t": "rows", "table": "labels",
                                                                      "where": "x", "params": [7, plain, "R2"]}]),))
    st.close()
    for _ in range(2):
        p = Project.load(p.root)
        st = p.store
        assert st.get_meta("schema_version") == str(SCHEMA_VERSION)
        vf = abstracts.versions(st, filled)
        assert [(v["version"], v["source"], v["abstract"]) for v in vf] == [
            (1, "imported", imported), (2, "crossref", "A Crossref abstract with nine words in it ok.")]
        assert vf[1]["at"] == "2026-03-01T00:00:00+00:00"
        assert [v["version"] for v in abstracts.versions(st, plain)] == [1]
        calls = {(r["record_id"], r["pass"]): r["abstract_version"] for r in st.query("SELECT * FROM screen_calls")}
        assert calls == {(filled, "A"): 1, (plain, "A"): 1, (filled, "B"): 2, (filled, "C"): None}   # no time: none
        assert abstracts.describe(st, filled, None) == "abstract version not recorded"
        labs = {(r["record_id"], r["reviewer_id"]): (r["abstract_version"], r["made_in"]) for r in st.query("SELECT * FROM labels")}
        assert labs == {(filled, "R1"): (1, "wider"), (filled, "R2"): (2, "wider"), (plain, "R2"): (1, "blind queue")}
        assert st.scalar("SELECT COUNT(*) FROM record_abstracts") == st.scalar("SELECT COUNT(*) FROM records") + 1
        assert st.columns("label_revisions")[-2:] == ["cause", "package_id"]
        p.close()



def test_drawer_lists_versions_node(tmp_path):
    """The drawer shows "Abstract versions: 2" and, opened, the same two lines as `wider enrich --versions`."""
    from tests.unit.a11y_dom import run_dom
    p = _project_with_imports(tmp_path)
    run(p)
    rid = p.store.scalar("SELECT record_id FROM records WHERE title='Unique paper one'")
    p.store.execute("UPDATE record_abstracts SET words=12, at='2026-01-02T00:00:00+00:00' WHERE record_id=?", (rid,))
    abstracts.add_version(p.store, rid, " ".join(["w"] * 240), "openalex", at="2026-03-04T00:00:00+00:00")
    p.store.execute("UPDATE records SET abstract=? WHERE record_id=?", (" ".join(["w"] * 240), rid))
    rec = TestClient(create_app(p)).get(f"/api/records/{rid}").json()["record"]
    out = run_dom(f"""
const rec = {json.dumps(rec)};
Drawer.open({{ label: "t", items: [rec], index: 0, recordId: () => null, render: async (r, u) => u.recordBlock(r, []) }});
await flush(); await flush();
const d = document.querySelector(".dw-versions");
out({{ line: d.querySelector("summary").textContent, items: [...d.querySelectorAll("li")].map((li) => li.textContent),
      when: [fmtWhen("2026-01-02T00:00:00+00:00"), fmtWhen("2026-03-04T00:00:00+00:00")] }});
""", html=_drawer_html(), scripts=("common.js", "drawer.js"))
    assert out["line"] == "Abstract versions: 2"
    a, b = out["when"]   # the one time format of every page (fmtWhen)
    assert out["items"] == [f"1  imported, 12 words, {a}", f"2  OpenAlex, 240 words, {b}  (current)"]
    cli = json.loads(_cli(p, "enrich", "--versions", str(rid), "--json").output)
    assert [(v["version"], v["source_word"], v["words"], v["at"][:10]) for v in cli["versions"]] == \
        [(1, "imported", 12, "2026-01-02"), (2, "OpenAlex", 240, "2026-03-04")]


def _drawer_html() -> str:
    from tests.unit.a11y_dom import static
    html = static("index.html")
    return html[html.index('<aside id="drawer"'):html.index("</aside>", html.index('<aside id="drawer"')) + len("</aside>")]
