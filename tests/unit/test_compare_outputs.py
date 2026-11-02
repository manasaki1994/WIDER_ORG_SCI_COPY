"""Configuration comparisons and the model of each pass in the outputs (change screening-config-comparison, group 6,
design D15): the report's section, the methods draft's sentences, `comparisons.json` in the replication package and
`manifest.json` `models_per_pass`; counts and settings only, never a record. Synthetic topic-neutral records and fake
backends only."""
import json
import re
import zipfile

import pytest

from tests.unit.test_pass_backends import pp  # noqa: F401  (fixture)
from tests.unit.test_screen_compare import (  # noqa: F401  (_template, cproject and locked are fixtures)
    _template,
    api,
    cli,
    cproject,
    dev_rows,
    fresh,
    locked,
    per_backend,
    use_responder,
)
from wider.recall.outputs import methods_draft
from wider.screen import compare

TWO = ("2 configuration comparisons were run on the purposive development sample; their figures describe that "
       "sample, not the corpus.")


class Stop(Exception):
    pass


def stop_after(n):
    """An ``on_run`` callback that stops the comparison before its run number ``n + 1``."""
    seen = []

    def on_run(c, k, rid):
        if len(seen) == n:
            raise Stop()
        seen.append(rid)
    return on_run


def package(p) -> zipfile.ZipFile:
    r = cli(p, "export", "package")
    assert r.exit_code == 0, r.output
    path = sorted((p.root / "exports").glob("replication_package_*.zip"))[-1]
    return zipfile.ZipFile(path)


def report_md(p) -> str:
    r = cli(p, "report")
    assert r.exit_code == 0, r.output
    return (p.root / "exports" / "report.md").read_text(encoding="utf-8")


def section(md: str, title: str) -> str:
    body = md[md.index(f"## {title}"):]
    nxt = body.find("\n## ", 3)
    return body if nxt < 0 else body[:nxt]


def test_comparisons_in_report_methods_package(locked, monkeypatch):  # noqa: F811
    """Scenario "Two comparisons listed": cmp1 (2 configurations) finished, cmp2 (3 configurations) stopped after 2;
    the report lists both with cmp2 "not finished", the methods draft has the sentence, and the zip's comparisons.json
    holds both comparisons' settings and counts, hashed in MANIFEST.txt, with no title, abstract or response; the
    share bundle carries no comparison.csv and no decisions.csv of a comparison run."""
    p, counter = locked
    use_responder(monkeypatch, counter, per_backend(misses={"fake_b": {18}}))
    compare.run(p, "cmp1", "development", ["fake", "fake_b"], repeats=1)
    with pytest.raises(Stop):
        compare.run(p, "cmp2", "development", ["fake", "fake_b", "fake@0.5"], repeats=1, on_run=stop_after(2))
    q = fresh(p)
    assert q.store.scalar("SELECT finished_at FROM screen_comparisons WHERE name='cmp2'") is None
    titles = [r["title"] for r in dev_rows(q)]

    md = report_md(p)
    sec = section(md, "Configuration comparisons")
    assert "Comparison cmp1 on development: 2 configurations" in sec
    assert "Comparison cmp2 on development (not finished): 3 configurations" in sec
    assert "cmp1 on development (not finished)" not in sec
    assert re.search(r"- fake_b \(fake_b, model fake-m2, .*\): misses 1–1, false includes 0–0", sec), sec
    assert "- fake@0.5 (fake, model fake-m1, prompt v1, temperature 0.5): not run\n" in sec   # no repeat done
    assert not [t for t in titles if t in md], "report.md names a record"
    rj = json.loads((p.root / "exports" / "report.json").read_text(encoding="utf-8"))
    assert rj["comparisons"] == compare.report_lines(fresh(p))

    draft = methods_draft(fresh(p))
    assert TWO in draft
    assert api(p).get("/api/outputs/methods/draft").json()["draft"] == draft
    r = cli(p, "export", "methods")
    assert r.exit_code == 0 and TWO in " ".join(r.output.split())

    z = package(p)
    names = z.namelist()
    raw = z.read("comparisons.json").decode("utf-8")
    cj = json.loads(raw)
    assert [c["name"] for c in cj] == ["cmp1", "cmp2"]
    assert [c["finished"] for c in cj] == [True, False]
    c1, c2 = cj
    assert c1["sample"] == "development" and c1["repeats"] == 1 and c1["labelled"] == len(titles)
    assert [x["label"] for x in c1["configurations"]] == ["fake", "fake_b"]
    assert c1["configurations"][1]["misses"] == [1, 1] and c1["configurations"][1]["model"] == "fake-m2"
    assert [len(x["run_ids"]) for x in c2["configurations"]] == [1, 1, 0]
    assert all(x["run_ids"] == [r["run_id"] for r in y["runs"]]
               for x, y in zip(c1["configurations"], compare.results(fresh(p), "cmp1")["configs"]))
    for t in titles:
        assert t not in raw
    assert "abstract" not in raw.lower() and "response" not in raw.lower() and "record_id" not in raw
    assert "topic alpha" not in raw and "topic beta" not in raw
    manifest = z.read("MANIFEST.txt").decode("utf-8")
    import hashlib
    assert f"{hashlib.sha256(z.read('comparisons.json')).hexdigest()}  comparisons.json" in manifest
    mj = json.loads(z.read("manifest.json"))
    assert "comparisons.json" in {f["file"] for f in mj["files"]}
    assert not [n for n in names if n.endswith("comparison.csv")]
    tagged = [n for n in names if "__cmp." in n]
    assert tagged, "the share bundle copies the comparison run folders"
    assert not [n for n in tagged if n.endswith(("decisions.csv", "errors.csv"))]
    for n in tagged:
        if n.endswith((".json", ".md", ".jsonl", ".csv", ".txt")):
            body = z.read(n).decode("utf-8", "replace")
            assert not [t for t in titles if t in body], n


def test_no_comparison(cproject):  # noqa: F811
    """Scenario "No comparison": the report says so, the methods draft has no comparison sentence and the package
    has no comparisons.json."""
    p, _ = cproject
    sec = section(report_md(p), "Configuration comparisons")
    assert "No configuration comparison was run." in sec
    assert "configuration comparison" not in methods_draft(fresh(p))
    assert "comparisons.json" not in package(p).namelist()


def test_model_per_pass_named(pp):  # noqa: F811
    """Scenario "The model per pass": pass A on a, B on b and the judge on c are named with their pass in the methods
    draft and in manifest.json `models_per_pass`; every pass on a gives one model for all passes, and a second
    screen names its reads S1, S2 and S3."""
    p, counter = pp
    r = cli(p, "screen", "protocol", "corpus", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    three = "Pass A used a-m, pass B used b-m and the judge used c-m."
    assert three in methods_draft(fresh(p), "main")
    assert "use the same model" not in methods_draft(fresh(p), "main")
    mj = json.loads(package(p).read("manifest.json"))
    assert mj["models_per_pass"]["main"] == {"A": ["a-m"], "B": ["b-m"], "C": ["c-m"]}

    r = cli(p, "screen", "protocol", "corpus", "--name", "one")
    assert r.exit_code == 0, r.output
    assert "Every pass (pass A and pass B) used a-m." in methods_draft(fresh(p), "one")

    counter["maybe"] = (3, 9)
    assert cli(p, "screen", "protocol", "corpus", "--name", "src").exit_code == 0
    counter["maybe"] = ()
    r = cli(p, "screen", "second", "src", "--provider-b", "b", "--provider-c", "c")
    assert r.exit_code == 0, r.output
    # the set keeps the source's pass A and B votes for the records that were not maybes
    assert ("Pass A, pass B and read S1 used a-m, read S2 used b-m and read S3 used c-m."
            in methods_draft(fresh(p), "src_second"))
    mj = json.loads(package(p).read("manifest.json"))
    assert mj["models_per_pass"]["src_second"] == {"A": ["a-m"], "B": ["a-m"], "S1": ["a-m"], "S2": ["b-m"], "S3": ["c-m"]}


def test_methods_sentence_number(monkeypatch):
    """One comparison reads "its figures"; comparisons on two samples read "those samples"."""
    from wider.screen import compare

    one = [{"sample": "development"}]
    monkeypatch.setattr(compare, "list_comparisons", lambda project: one)
    assert compare.methods_sentence(None) == ("1 configuration comparison was run on the purposive development "
                                              "sample; its figures describe that sample, not the corpus.")
    two = [{"sample": "development"}, {"sample": "dev2"}]
    monkeypatch.setattr(compare, "list_comparisons", lambda project: two)
    assert compare.methods_sentence(None) == ("2 configuration comparisons were run on the purposive samples "
                                              "development and dev2; their figures describe those samples, not the "
                                              "corpus.")
