"""Seed papers' full texts (vision change 2b): the PDF per seed, extraction, name mining, admission, the name map and
the name-by-venue table for streams. Synthetic PDFs built in ``tmp_path``; nothing leaves the computer."""
import json
import zipfile

from fastapi.testclient import TestClient

from tests.fixtures.seeds.make_synthetic_pdf import PAGE2, make_pdf
from tests.unit.test_dry_run import _cli
from wider.core.models import BenchmarkPaper
from wider.core.project import Project
from wider.recall.outputs import write_package
from wider.recall.report import share_bundle
from wider.seeds import pdf
from wider.ui.app import create_app

EXTRACTED_LINE = "synchronisation across trials"


def _proj(tmp_path, name="r") -> Project:
    p = Project.init(tmp_path / name, platforms=["scopus"])
    p.write_benchmark([BenchmarkPaper(benchmark_id="B001", title="Alpha coupling in pairs", doi="10.1000/a1",
                                      set="Development"),
                       BenchmarkPaper(benchmark_id="B002", title="Beta drift over time", doi="10.1000/a2",
                                      set="Development"),
                       BenchmarkPaper(benchmark_id="V001", title="Validation paper", doi="10.1000/v1",
                                      set="Validation")])
    return p


def _outputs_hold_nothing_of_seeds(p) -> None:
    """Neither the share bundle, the package nor anything under exports/ holds a seeds/ file or extracted text."""
    bundle = share_bundle(p)
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    names = z.namelist()
    assert not [n for n in names if n.startswith("seeds/") or "/seeds/" in n or n.endswith(".pdf")], names
    # no 40-character run of an extracted text and no mined quote from a seed paper (spec-checker F3)
    from wider.seeds import mine
    runs = []
    for t in (p.root / "runs" / "seeds").glob("*/text.txt"):
        text = " ".join(t.read_text(encoding="utf-8").split())
        runs += [text[i:i + 40] for i in range(0, max(1, len(text) - 40), 20)]
    runs += [" ".join(s["quote"].split()) for c in mine.candidates(p) for s in c["sources"] if s["seed"] != "retrieved"]
    runs = [r for r in runs if len(r) >= 20]
    assert len(runs) > 10

    def clean(body: str, where) -> None:
        body = " ".join(body.split())
        assert not [r for r in runs if r in body], where

    for n in names:
        clean(z.read(n).decode("utf-8", "replace"), n)
    for f in [*bundle.rglob("*"), *p.path("exports").rglob("*")]:
        if f.is_file():
            assert f.suffix != ".pdf" and "seeds" not in f.relative_to(p.root).parts[1:], f
            if f.suffix != ".zip":
                clean(f.read_text(encoding="utf-8", errors="replace"), f)


def test_seed_pdf_add_list_remove_and_never_exported(tmp_path):
    p = _proj(tmp_path)
    good = make_pdf(tmp_path / "in" / "paper one.pdf")
    bad = tmp_path / "in" / "notes.txt"
    bad.write_text("not a pdf", encoding="utf-8")
    r = _cli(p, "benchmark", "pdf", "add", "B001", str(good))
    assert r.exit_code == 0, r.output
    assert "seeds/B001.pdf kept" in r.output
    r = _cli(p, "benchmark", "pdf", "add", "B002", str(bad))
    assert r.exit_code == 1 and "notes.txt is not a PDF" in r.output
    assert not (p.root / "seeds" / "B002.pdf").exists()
    rows = {x["benchmark_id"]: x for x in pdf.list_files(p)}
    from wider.core.provenance import sha256_file
    assert rows["B001"]["has_pdf"] and rows["B001"]["sha256"] == sha256_file(good)
    assert not rows["B002"]["has_pdf"] and rows["B002"]["sha256"] is None
    r = _cli(p, "benchmark", "pdf", "list")
    assert f"B001  seeds/B001.pdf  sha256 {sha256_file(good)}" in r.output and "B002  no PDF" in r.output
    assert (p.root / "seeds" / ".gitignore").read_text() == "*\n!.gitignore\n"
    assert "seeds/" not in (p.root / ".gitignore").read_text()   # the project's own .gitignore is left unchanged
    # a second PDF replaces the first: one file, one hash
    other = make_pdf(tmp_path / "in" / "other.pdf", pages=(PAGE2,))
    pdf.add(p, "B001", other)
    rows_b1 = p.store.query("SELECT sha256 FROM seed_files WHERE benchmark_id='B001'")
    assert [r["sha256"] for r in rows_b1] == [sha256_file(other)] and len(list((p.root / "seeds").glob("B001*"))) == 1
    pdf.add(p, "B001", good)
    # the page's slot: the same engine functions through the routes
    c = TestClient(create_app(Project.load(p.root)))
    up = c.post("/api/seeds/files/B002", files={"file": ("notes.txt", b"not a pdf", "text/plain")})
    assert up.status_code == 400 and "is not a PDF" in up.json()["detail"]
    assert not list((p.root / "seeds").glob(".upload*"))
    assert c.post("/api/seeds/files/B009", files={"file": ("x.pdf", good.read_bytes(), "application/pdf")}).status_code == 404
    up = c.post("/api/seeds/files/V001", files={"file": ("x.pdf", good.read_bytes(), "application/pdf")})
    assert up.status_code == 200 and up.json()["file"] == "seeds/V001.pdf"
    assert [f["benchmark_id"] for f in c.get("/api/seeds/files").json()["files"] if f["has_pdf"]] == ["B001", "V001"]
    assert c.delete("/api/seeds/files/V001").status_code == 200
    assert not (p.root / "seeds" / "V001.pdf").exists()
    assert c.delete("/api/seeds/files/V001").status_code == 404
    # extracted text sits in runs/seeds/; neither it nor the PDF reaches the bundle, the package or exports/
    pdf.extract_all(p)
    assert EXTRACTED_LINE in (p.root / "runs" / "seeds" / "B001" / "text.txt").read_text()
    _outputs_hold_nothing_of_seeds(p)
    r = _cli(p, "benchmark", "pdf", "remove", "B001")
    assert r.exit_code == 0 and not (p.root / "seeds" / "B001.pdf").exists()
    assert not (p.root / "runs" / "seeds" / "B001").exists() and not pdf.with_pdf(p)
    assert good.exists()   # the researcher's own file is untouched


def test_extract_and_postprocess(tmp_path):
    p = _proj(tmp_path)
    pdf.add(p, "B001", make_pdf(tmp_path / "s.pdf"))
    r = _cli(p, "seeds", "extract")
    assert r.exit_code == 0, r.output
    assert "B001: 2 pages" in r.output and "3 references" in r.output and "runs/seeds/B001/" in r.output
    text = (p.root / "runs" / "seeds" / "B001" / "text.txt").read_text()
    assert text.count("synchronisation") == 1 and "synchro-" not in text
    assert text.count("Synthetic Signals Review 2026") == 1
    sections = json.loads((p.root / "runs" / "seeds" / "B001" / "sections.json").read_text())
    assert [s["name"] for s in sections] == ["front", "abstract", "introduction", "discussion", "references"]
    refs = json.loads((p.root / "runs" / "seeds" / "B001" / "references.json").read_text())
    assert [(x["title"], x["year"]) for x in refs] == [("Alpha coupling in paired signals", 2019),
                                                         ("Alpha coupling and beta drift", 2020),
                                                         ("Phase binding at scale", 2021)]
    # the route runs the same function
    c = TestClient(create_app(Project.load(p.root)))
    res = c.post("/api/seeds/extract", json={"benchmark_id": "B001"}).json()
    assert res["seeds"][0]["references"] == 3 and res["seeds"][0]["pages"] == 2


def test_missing_extra_is_named(tmp_path, monkeypatch):
    import sys

    p = _proj(tmp_path)
    pdf.add(p, "B001", make_pdf(tmp_path / "s.pdf"))
    monkeypatch.setitem(sys.modules, "pypdf", None)   # pypdf is installed everywhere; the import is made to fail
    r = _cli(p, "seeds", "extract")
    assert r.exit_code == 1 and 'pip install "wider[pdf]"' in r.output
    assert not (p.root / "runs" / "seeds").exists()


def test_deterministic_patterns(tmp_path):
    from wider.seeds.mine import mine_deterministic

    sections = {"B001": [{"name": "front", "text": "Keywords: alpha coupling; beta drift"},
                         {"name": "introduction", "text": 'Alpha coupling, also called phase binding, links two '
                          'signals. The "phase binding" view treats\ncoupling as shared timing; "phase binding" '
                          'recurs.'},
                         {"name": "references", "text": "Abel, A. (2019). Also called nothing. J. 1, 1-2."}]}
    titles = ["Phase binding in neural pairs", "Neural pairs and phase binding", "Unrelated title"]
    by = {c.key: c for c in mine_deterministic(sections, titles)}
    assert {"alpha coupling", "beta drift", "phase binding"} <= set(by)
    pb = by["phase binding"]
    assert pb.pattern == "also called" and pb.count == 3 and pb.same_as == ["alpha coupling"]
    assert [s.quote for s in pb.sources] == ["Alpha coupling, also called phase binding, links two signals.",
                                             'The "phase binding" view treats coupling as shared timing;',
                                             '"phase binding" recurs.']
    assert by["alpha coupling"].pattern == "keywords" and by["alpha coupling"].sources[0].quote.startswith("Keywords:")
    assert by["neural pairs"].pattern == "title co-occurrence" and by["neural pairs"].count == 2
    assert "nothing" not in by   # the reference list is not mined for names
    # the CLI twin over a project: extracted text plus a keywords field, no model asked
    p = _proj(tmp_path)
    pdf.add(p, "B001", make_pdf(tmp_path / "s.pdf"))
    pdf.extract_all(p)
    r = _cli(p, "seeds", "mine", "--no-model")
    assert r.exit_code == 0, r.output
    assert "phase binding  [also called] ×3  same as: alpha coupling" in r.output and "runs/seeds/mine/" in r.output
    c = TestClient(create_app(Project.load(p.root)))
    names = [x["key"] for x in c.get("/api/seeds/candidates").json()["candidates"]]
    assert names[:3] == ["phase binding", "alpha coupling", "beta drift"]
    assert c.post("/api/seeds/mine", json={"model": False}).json()["deterministic"] == len(names)


STREAM_REFS = [
    "Abel, A. (2019). Alpha coupling in paired signals. Journal of Synthetic Signals, 12(3), 1-10.",
    "Berg, B. (2020). Alpha coupling and beta drift. Journal of Synthetic Signals, 13(1), 11-20.",
    "Cole, C. (2020). Alpha coupling at rest. Journal of Synthetic Signals, 13(2), 21-30.",
    "Dahl, D. (2021). Measuring alpha coupling. Journal of Synthetic Signals, 14(1), 31-40.",
    "Eck, E. (2021). Alpha coupling in devices. Proceedings on Phase Studies, 4, 41-50.",
    "Fox, F. (2022). Phase binding at scale. Proceedings on Phase Studies, 5, 51-60.",
]


def test_other_introducing_patterns():
    """"also known as", "known as", "termed", "referred to as" and "so-called" give the name without the words that
    follow it, and a "same as" partner only where the sentence names one (spec-checker F9)."""
    from wider.seeds import mine

    text = ("Voice, also known as speaking up, matters. Silence is termed withholding behaviour here. The effect is "
            "referred to as upward dissent. The so-called quiet quitting trend grew. This is known as the issue "
            "selling frame.")
    got = {c.name: (c.pattern, c.same_as) for c in mine.mine_deterministic({"S": [{"name": "introduction",
                                                                                   "text": text}]})}
    assert got == {"speaking up": ("also known as", ["voice"]), "withholding behaviour": ("termed", ["silence"]),
                   "upward dissent": ("referred to as", []), "quiet quitting trend": ("so-called", []),
                   "issue selling frame": ("known as", [])}
    # "than" belongs to the name (spec-checker F-R2-5)
    got = [c.name for c in mine.mine_deterministic({"S": [{"name": "introduction", "text":
           "The bias is also called the better than average effect in the literature."}]})]
    assert "better than average effect" in got, got


def test_name_by_venue_table(tmp_path):
    from wider.core import journal
    from wider.seeds import mine, streams

    assert streams.venue(STREAM_REFS[0], "Alpha coupling in paired signals") == "Journal of Synthetic Signals"
    assert streams.venue(STREAM_REFS[5], "Phase binding at scale") == "Proceedings on Phase Studies"
    assert streams.venue("Gray, G. (2023). A title. 10.1000/x", "Not in the entry") == streams.UNKNOWN
    p = _proj(tmp_path)
    from tests.fixtures.seeds.make_synthetic_pdf import HEADER, PAGE1
    # two seed texts, three references each
    pdf.add(p, "B001", make_pdf(tmp_path / "s1.pdf", pages=(PAGE1, [HEADER, "References", *STREAM_REFS[:3]])))
    pdf.add(p, "B002", make_pdf(tmp_path / "s2.pdf", pages=([HEADER, "Abstract", "Beta drift."],
                                                             [HEADER, "References", *STREAM_REFS[3:]])))
    pdf.extract_all(p)
    mine.mine(p, model=False)
    t = streams.table(p)
    jss, pps = "journal of synthetic signals", "proceedings on phase studies"
    assert [v["venue_norm"] for v in t["venues"]] == [jss, pps]
    rows = {n["name"]: n["counts"] for n in t["names"]}
    assert rows["alpha coupling"] == {jss: 4, pps: 1} and rows["phase binding"] == {jss: 0, pps: 1}
    # the CLI twin labels a venue; the label is journalled and undone
    r = _cli(p, "seeds", "streams", "--label", "Proceedings on Phase Studies", "engineering")
    assert r.exit_code == 0, r.output
    assert "Proceedings on Phase Studies  (2 references)  stream: engineering" in r.output
    assert "alpha coupling: Journal of Synthetic Signals 4, Proceedings on Phase Studies 1" in r.output
    p = Project.load(p.root)
    assert streams.labels(p)[pps]["label"] == "engineering"
    ch = journal.changes(p)[0]
    assert ch["kind"] == "stream_label" and ch["kind_label"] == "stream labelled"
    journal.undo(p, ch["change_id"], author="ana", other=True)
    assert streams.labels(p) == {}
    assert _cli(p, "seeds", "streams", "--label", "No Such Venue", "x").exit_code == 1
    # the page's twin, then the package holds the table and the label
    c = TestClient(create_app(Project.load(p.root)))
    assert c.post("/api/seeds/streams/label", json={"venue": pps, "stream": "engineering", "author": "ana"}).status_code == 200
    got = c.get("/api/seeds/streams").json()
    assert got["venues"][1]["label"] == "engineering" and got["names"][0]["name"] == "alpha coupling"
    # the card's Streams table from the route's own reply, and its Save label
    out = _card("""
      const html = $("#sn-streams").innerHTML;
      $("#sn-venue").value = "journal of synthetic signals"; $("#sn-stream").value = " psychology ";
      await $("#sn-label-save").onclick(); await flush();
      console.log(JSON.stringify({html, calls, msg: $("#sn-msg").textContent}));
    """, {"/api/seeds/names": c.get("/api/seeds/names").json(), "/api/seeds/streams": got,
          "/api/seeds/streams/label": {"venue_norm": jss, "label": "psychology"}})
    assert f'<th data-venue="{pps}">Proceedings on Phase Studies<br>' in out["html"]
    assert "stream: engineering · 2 references" in out["html"] and "no stream label · 4 references" in out["html"]
    assert "<td>alpha coupling</td><td>4</td><td>1</td>" in out["html"]
    assert [x for x in out["calls"] if x[0] == "/api/seeds/streams/label"] == [
        ["/api/seeds/streams/label", "POST", {"venue": jss, "stream": "psychology"}]]
    assert out["msg"] == f'{jss}: stream "psychology"'
    p = Project.load(p.root)
    z = zipfile.ZipFile(p.root / write_package(p)["path"])
    arc = [n for n in z.namelist() if n.endswith("streams.json")]
    assert arc, z.namelist()
    st = json.loads(z.read(arc[0]))
    assert st["labels"][pps]["label"] == "engineering" and {n["name"] for n in st["names"]} >= {"alpha coupling"}
    assert "links two signals" not in z.read(arc[0]).decode()


def _fake(responder=None, **kw):
    from wider.core.models import ProviderConfig
    from wider.providers.fake import FakeProvider
    return FakeProvider(ProviderConfig(name="fake", kind="fake", **kw), responder)


def _mined(tmp_path, name="r"):
    p = _proj(tmp_path, name)
    pdf.add(p, "B001", make_pdf(tmp_path / f"{name}.pdf"))
    pdf.extract_all(p)
    return p


def test_model_candidates_need_a_verbatim_quote(tmp_path):
    from wider.seeds import mine

    p = _mined(tmp_path)

    def answer(req):
        if "also called phase binding" in req.user:
            return {"candidates": [
                {"name": "shared timing", "kind": "construct", "quote": "treats coupling as shared timing",
                 "reasoning": "named in the passage"},
                {"name": "neural entrainment", "kind": "construct", "quote": "neural entrainment of pairs",
                 "reasoning": "not in the passage"}]}
        return {"candidates": []}
    fake = _fake(answer)
    r = mine.mine(p, provider=fake)
    assert r["model"]["ran"] and "1 dropped: quote not found" in r["model"]["message"]
    by = {c["key"]: c for c in r["candidates"]}
    assert by["shared timing"]["origin"] == "model" and by["shared timing"]["pattern"] == "model"
    assert by["shared timing"]["sources"][0]["passage"].startswith("B001:introduction:")
    assert "neural entrainment" not in by
    run = p.root / r["folder"]
    calls = json.loads((run / "calls.json").read_text())
    assert calls and all(len(c["input_sha256"]) == 64 and c["passage_sha256"] for c in calls)
    blob = (run / "calls.json").read_text() + (run / "run.json").read_text()
    assert "shared timing" not in blob and EXTRACTED_LINE not in blob
    assert not list(run.rglob("*.pdf"))
    # the reference list is never sent
    assert not any("Journal of Synthetic Signals" in q.user for q in fake.calls)


def test_call_rules(tmp_path):
    from wider.seeds import mine

    p = _mined(tmp_path)
    for repairs, calls_on_bad in ((None, 2), (2, 3)):
        fake = _fake(lambda req: "not json" if "Beta drift remains open" in req.user else {"candidates": []},
                     repairs=repairs)
        r = mine.mine(p, provider=fake)
        recs = json.loads((p.root / r["folder"] / "calls.json").read_text())
        bad = [c for c in recs if not c["ok"]]
        assert len(bad) == 1 and bad[0]["attempts"] == calls_on_bad <= 3 and bad[0]["repairs"] == calls_on_bad - 1
        assert bad[0]["input_sha256"] and bad[0]["error"].startswith("invalid structured output")
        assert all(c["ok"] for c in recs if c is not bad[0]) and len(recs) > 1
        assert "1 passage(s) failed" in r["model"]["message"]
        assert all(q.temperature == 0 and q.seed == 1 for q in fake.calls)
        assert all(c["seed"].startswith("B") and c["sampling_seed"] == 1 for c in recs)   # paper id and sampling seed
        assert sum("Beta drift remains open" in q.user for q in fake.calls) == calls_on_bad
    # the usage log holds counts and the model, no text
    cols = [r["name"] for r in p.store.query("PRAGMA table_info(llm_usage)")]
    assert not [c for c in cols if "text" in c or "prompt" == c]
    assert p.store.scalar("SELECT COUNT(*) FROM llm_usage WHERE stage='seed_mining'") >= 3


def test_cli_backend_flags_in_run_record(tmp_path):
    from wider.providers.presets import config_from_preset
    from wider.providers.registry import make_provider
    from wider.seeds import mine

    p = _mined(tmp_path)
    p.config.seed_pdfs.passages_to_cloud = False   # the cloud presets are then skipped before any consent (3.5)
    flags = {"claude-code": ["--tools", "", "--strict-mcp-config"],
             "codex": ["features.shell_tool=false", 'web_search="disabled"', "--sandbox", "read-only"],
             "gemini-cli": ["-e", "none", "--approval-mode", "plan"]}
    for pid, want in flags.items():
        cfg = config_from_preset(pid, name=pid, model="m")
        r = mine.mine(p, provider=make_provider(cfg, ("cloud", "endpoint")))
        snap = json.loads((p.root / r["folder"] / "run.json").read_text())["model"]["provider"]
        cmd = snap["command"]
        assert all(w in cmd for w in want), (pid, cmd)
        assert snap["kind"] == "cli_agent" and not r["model"]["ran"] and "passages_to_cloud" in r["model"]["message"]
        assert not (p.root / r["folder"] / "calls.json").exists()
    for pid in ("antigravity", "custom-cli"):
        cfg = config_from_preset(pid, name=pid, model="m", **({"command": ["mytool"]} if pid == "custom-cli" else {}))
        r = mine.mine(p, provider=make_provider(cfg, ("local", "endpoint")))
        assert not r["model"]["ran"] and "knows no command-line flags" in r["model"]["message"], r["model"]
        assert r["deterministic"] and r["candidates"]   # the pattern pass still runs
    # the command is read, not the preset label: an edited claude-code command that turns tools back on gets nothing
    cfg = config_from_preset("claude-code", name="edited", model="m")
    i = cfg.command.index("--tools")
    cfg = cfg.model_copy(update={"command": cfg.command[:i] + cfg.command[i + 2:]})
    r = mine.mine(p, provider=make_provider(cfg, ("local", "endpoint")))
    assert not r["model"]["ran"] and 'lacks --tools ""' in r["model"]["message"], r["model"]
    assert mine.missing_tool_flags(config_from_preset("codex", name="c", model="m").command) == ("codex", [])


def test_passages_switch_gates_cloud_calls(tmp_path, monkeypatch):
    """`seed_pdfs.passages_to_cloud` (the privacy rule's named exception, 3.0/3.5): on, a backend that is not local
    gets one passage of at most the set size per call, after its own `passages` consent asked once; off, it gets
    nothing and only the pattern pass runs. A local backend gets passages either way."""
    from tests.unit.test_backend_consent import LLM_BASE, _llm_transport
    from wider.core import backend_consent as bc
    from wider.core.models import ProviderConfig
    from wider.seeds import mine, policy

    p = _mined(tmp_path)
    assert p.config.seed_pdfs.passages_to_cloud is True and p.config.seed_pdfs.passage_tokens == 1500
    with __import__("pytest").raises(ValueError, match="between 100 and 8,000"):
        policy.set_policy(p, passage_tokens=50)
    policy.set_policy(p, passage_tokens=300)
    assert Project.load(p.root).config.seed_pdfs.passage_tokens == 300   # saved to project.yaml
    fake = _fake(lambda req: {"candidates": []})
    fake.locality = "cloud"
    r = mine.mine(p, provider=fake)
    assert r["model"]["ran"] and len(fake.calls) >= 3
    assert all(q.user.count("Passage (") == 1 and len(q.user) <= 300 * 4 + 40 for q in fake.calls)
    cols = [c["name"] for c in p.store.query("PRAGMA table_info(llm_usage)")]
    assert not [c for c in cols if "text" in c or c == "prompt"]
    # off: no call to a backend that is not local, the message names the switch; the pattern pass still runs
    policy.set_policy(p, passages_to_cloud=False)
    for loc in ("cloud", "unknown", "network"):
        fake = _fake(lambda req: {"candidates": []})
        fake.locality = loc
        r = mine.mine(p, provider=fake)
        assert not fake.calls and not r["model"]["ran"] and r["candidates"], loc
        assert "seed_pdfs.passages_to_cloud is off" in r["model"]["message"] and "wider seeds policy" in r["model"]["message"]
    fake = _fake(lambda req: {"candidates": []})
    fake.locality = "local"
    assert mine.mine(p, provider=fake)["model"]["ran"] and fake.calls
    out = _cli(p, "lookups", "status")
    assert "seed-paper passages to a backend that is not local (separate): off" in out.output, out.output
    # a real cloud backend without consent: nothing is sent; research is asked first, then passages
    policy.set_policy(p, passages_to_cloud=True)
    monkeypatch.setenv("A_KEY", "synthetic-not-a-key")
    sent: list = []
    _llm_transport(monkeypatch, sent)
    p.config.providers.append(ProviderConfig(name="a", kind="openai_compat", model="m1", base_url=LLM_BASE,
                                             api_key_env="A_KEY"))
    p.config.default_provider = "a"
    p.save()
    assert [op for _, op in mine.needs_consent(p)] == ["research", "passages"]
    out = _cli(p, "seeds", "mine")
    assert out.exit_code == 2 and "send research data" in out.output and not sent, out.output
    client = TestClient(create_app(Project.load(p.root)))
    got = client.post("/api/seeds/mine", json={"model": True})
    assert got.status_code == 403 and got.json()["detail"]["backend_consent_required"]["operation"] == "research"
    bc.allow_backend(p, "a", ["research"])
    assert [op for _, op in mine.needs_consent(p)] == ["passages"]
    out = _cli(p, "seeds", "mine")
    assert out.exit_code == 2 and "send passages of seed PDFs" in out.output and not sent, out.output
    got = TestClient(create_app(Project.load(p.root))).post("/api/seeds/mine", json={"model": True})
    assert got.status_code == 403 and got.json()["detail"]["backend_consent_required"]["operation"] == "passages"
    assert "never the file" in got.json()["detail"]["backend_consent_required"]["sent"]
    # allowed once, remembered: the next runs ask nothing and each request holds one passage
    client = TestClient(create_app(Project.load(p.root)))
    assert client.post("/api/providers/a/consent", json={"operations": ["passages"]}).status_code == 200
    assert mine.needs_consent(Project.load(p.root)) == []
    out = _cli(p, "seeds", "mine")
    assert out.exit_code == 0 and sent, out.output
    chats = [json.loads(x.content) for x in sent if x.method == "POST"]
    assert chats and all(sum(m["content"].count("Passage (") for m in c["messages"]) == 1 for c in chats)
    pol = client.get("/api/seeds/policy").json()
    assert pol["passages_allowed"] == ["a"] and pol["backend"] == {
        "name": "a", "locality": "cloud", "locality_label": pol["backend"]["locality_label"], "needs_switch": True,
        "passages_allowed": True, "state": "allowed for passages"}
    out = _cli(p, "seeds", "policy")
    assert out.exit_code == 0 and "allowed for passages: a" in out.output and "300 tokens" in out.output, out.output
    # Withdraw (Settings) is `wider providers revoke a --for passages`: the next model pass asks again
    assert client.delete("/api/providers/a/consent?operation=passages").status_code == 200
    assert [op for _, op in mine.needs_consent(Project.load(p.root))] == ["passages"]
    out = _cli(p, "seeds", "policy", "passages-to-cloud", "off")
    assert out.exit_code == 0 and Project.load(p.root).config.seed_pdfs.passages_to_cloud is False
    assert "gets no passage while the switch is off" in out.output, out.output
    assert mine.needs_consent(Project.load(p.root)) == []   # a skipped pass never asks
    assert client.post("/api/seeds/policy", json={"passages_to_cloud": True, "passage_tokens": 600}).json()[
        "passage_tokens"] == 600
    assert client.post("/api/seeds/policy", json={"passage_tokens": 9000}).status_code == 400
    bad = _cli(p, "seeds", "policy", "passages-to-cloud", "maybe")
    assert bad.exit_code != 0


def test_seed_policy_card():
    """Settings, Seed papers: the switch, what it lets out, the size, and Withdraw per allowed backend."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    pol = {"passages_to_cloud": True, "passage_tokens": 1500, "lets_out": "extracted passages of seed PDFs <b>",
           "backend": {"name": "a", "locality": "cloud", "locality_label": "cloud service", "needs_switch": True,
                       "passages_allowed": True, "state": "allowed for passages"}, "passages_allowed": ["a"]}
    js = (STATIC / "seeds.js").read_text(encoding="utf-8")
    out = run_node(ESC + PAGE + js + f"\nObject.assign(replies, {{'/api/seeds/policy': {json.dumps(pol)}}});\n" + """
      const card = $("#settings-seeds");
      await SeedNames.renderPolicy(card); await flush();
      const html = card.innerHTML;
      card.querySelector("[data-passages-switch]").checked = false;
      await card.querySelector("[data-passages-switch]").onchange(); await flush();
      card.querySelector("[data-passage-tokens]").value = "800";
      await card.querySelector("[data-passage-save]").onclick(); await flush();
      await click("#settings-seeds", "[data-passages-revoke]", b => b.dataset.passagesRevoke === "a");
      console.log(JSON.stringify({html, calls}));
    """)
    html = out["html"]
    assert "<h2>Seed papers</h2>" in html and "&lt;b&gt;" in html and "The PDF itself never leaves" in html
    assert "a (cloud service): allowed for passages." in html and 'data-passages-revoke="a"' in html and "wider seeds policy" in html
    sent = [c for c in out["calls"] if c[1] in ("POST", "DELETE")]
    assert sent == [["/api/seeds/policy", "POST", {"passages_to_cloud": False}],
                    ["/api/seeds/policy", "POST", {"passage_tokens": 800}],
                    ["/api/providers/a/consent?operation=passages", "DELETE", None]], sent


def test_pdf_never_leaves(tmp_path):
    import importlib.util
    import pathlib

    from tests.fixtures.seeds.make_synthetic_pdf import HEADER, PAGE1
    from wider.seeds import mine

    p = _proj(tmp_path)   # an introduction of about 9,000 characters: more than one passage (spec-checker F2)
    filler = [HEADER] + [f"Signal pair {i} keeps a steady alpha coupling over a long synthetic window." for i in range(40)]
    pdf.add(p, "B001", make_pdf(tmp_path / "long.pdf", (PAGE1, filler, filler, filler, PAGE2)))
    pdf.extract_all(p)
    intro = next(s["text"] for s in pdf.load_text(p, "B001")["sections"] if s["name"] == "introduction")
    assert len(intro) > 1500 * 4 * 1.4
    fake = _fake(lambda req: {"candidates": []})
    mine.mine(p, provider=fake)
    intro_calls = [q for q in fake.calls if "Signal pair" in q.user]
    assert len(intro_calls) >= 2, len(intro_calls)
    assert all(len(q.user) <= 1500 * 4 + 40 for q in fake.calls), [len(q.user) for q in fake.calls]
    assert not any(q.user.startswith("%PDF") for q in fake.calls)
    usage = p.store.query("SELECT * FROM llm_usage WHERE stage='seed_mining'")
    assert usage and all(u["run_ref"].startswith("seeds/mine/") for u in usage)
    _outputs_hold_nothing_of_seeds(p)
    # the privacy guard refuses a seed PDF however it is named
    guard = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check_no_private_data.py"
    spec = importlib.util.spec_from_file_location("guard", guard)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    staged = p.root / "seeds" / "x.pdf"
    staged.write_bytes((p.root / "seeds" / "B001.pdf").read_bytes())
    assert g.main([str(staged)]) != 0


def test_validation_seeds_are_not_mined(tmp_path):
    """A validation paper's PDF may be kept, but its text, keywords and reference list stay out of mining and the
    streams table, and the run says which papers it left out (blinding; spec-checker F7)."""
    from wider.construct import seed
    from wider.seeds import mine, streams

    p = _mined(tmp_path)
    own = ["Introduction", "Gamma lattice, also called kappa weave, binds synthetic nodes in a ring.", "References",
           "Gale, G. (2021). Alpha coupling held out. Bulletin of Held Papers, 2, 1-9."]
    pdf.add(p, "V001", make_pdf(tmp_path / "v.pdf", pages=(own,)))
    pdf.extract_all(p)
    seed.set_keywords(p, "V001", "omega braid; sigma knot")
    fake = _fake(lambda req: {"candidates": []})
    r = mine.mine(p, provider=fake)
    assert r["seeds"] == ["B001"] and r["validation_skipped"] == ["V001"]
    assert "V001: validation paper(s), not mined" in r["validation_notice"]
    assert fake.calls and not [q for q in fake.calls if "Gamma lattice" in q.user or "omega braid" in q.user]
    assert {s["seed"] for c in r["candidates"] for s in c["sources"]} == {"B001"}
    keys = {c["key"] for c in r["candidates"]}
    assert not keys & {"kappa weave", "gamma lattice", "omega braid", "sigma knot"}, keys
    st = streams.table(p)   # B001's three, not V001's (spec-checker round 3 F2)
    assert st["references"] == 3 and not [v for v in st["venues"] if "held" in v["venue_norm"]], st["venues"]
    out = _cli(p, "seeds", "mine", "--no-model")
    assert out.exit_code == 0 and "V001: validation paper(s), not mined" in out.output
    one = _cli(p, "seeds", "mine", "--benchmark", "V001", "--no-model")
    assert one.exit_code == 1 and "Move a paper to Development" in one.output
    got = TestClient(create_app(p)).post("/api/seeds/mine", json={"model": False}).json()
    assert got["validation_skipped"] == ["V001"]
    # the page says it too (the Mine button's message)
    page = _card("""
      await $("#sn-mine-plain").onclick(); await flush();
      console.log(JSON.stringify({msg: $("#sn-msg").textContent}));
    """, {"/api/seeds/names": CARD_NAMES, "/api/seeds/streams": {"references": 0, "venues": [], "names": []},
          "/api/seeds/mine": got})
    assert "V001: validation paper(s), not mined" in page["msg"] and "wider benchmark set ID Development" in page["msg"]
    # a seed moved to Validation after mining drops out at once: its names are not listed, asked about or admitted
    assert {"phase binding"} <= {c["key"] for c in mine.candidates(p)}
    assert _cli(p, "benchmark", "set", "B001", "Validation").exit_code == 0
    q = Project.load(p.root)
    assert mine.candidates(q) == []
    from wider.seeds import names
    assert names.listing(q)["candidates"] == []
    # every seed with a text is a validation paper: the refusal names them, not "add a PDF"
    out = _cli(q, "seeds", "mine", "--no-model")
    assert out.exit_code == 1 and "B001, V001: validation paper(s), not mined" in out.output, out.output
    assert "add a PDF" not in out.output


SYN = {"blocks": [{"key": "a", "name": "Coupling", "terms": ['"alpha coupling"']}], "logic": {"and": ["a"]}}


def _with_query(tmp_path, name="r"):
    """B001 ("Alpha coupling in pairs") is a hit of v1, B002 ("Beta drift over time") a miss; names mined."""
    from wider.construct import artifacts
    from wider.seeds import mine

    p = _mined(tmp_path, name)
    from wider.construct.benchmark import sync

    sync(p)
    for i in ("B001", "B002"):
        p.store.execute("UPDATE benchmark_papers SET abstract=? WHERE benchmark_id=?", ("A synthetic abstract.", i))
    mine.mine(p, model=False)
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    return p


def test_admission_rule(tmp_path):
    import pytest

    from wider.construct import artifacts
    from wider.core import journal
    from wider.seeds import names

    p = _with_query(tmp_path)
    lst = {c["key"]: c for c in names.listing(p)["candidates"]}
    assert lst["beta drift"]["status"] == "retrieves B002" and lst["beta drift"]["block"] == "a"
    assert lst["phase binding"]["status"] == names.NOT_ADMITTED
    assert lst["alpha coupling"]["status"] == "already a term of block a" and lst["alpha coupling"]["held"] == ["a"]
    assert len(artifacts.list_versions(p, "synonyms")) == 1   # mining and listing never write a version
    # no gain and no note: refused, nothing saved
    with pytest.raises(ValueError, match="not admitted: retrieves no development seed in block a"):
        names.admit(p, "phase binding")
    assert len(artifacts.list_versions(p, "synonyms")) == 1
    # the CLI twin admits a name with a gain
    r = _cli(p, "seeds", "names")
    assert "beta drift: retrieves B002 (block a)" in r.output and f"phase binding: {names.NOT_ADMITTED}" in r.output
    r = _cli(p, "seeds", "names", "--admit", "beta drift", "--count-seen")
    assert r.exit_code == 0, r.output
    assert "synonyms v2 saved: admit beta drift to block a: retrieves B002" in r.output
    p = Project.load(p.root)
    row, data = artifacts.load(p, "synonyms")
    assert row["origin"] == "admit" and row["parent_version"] == 1 and row["status"] == "draft"
    assert data["blocks"][0]["terms"] == ['"alpha coupling"', '"beta drift"']
    assert names.admitted_names(p) == ["beta drift"]
    d = p.store.one("SELECT * FROM decisions WHERE kind='name_admit'")
    assert d["chosen"] == "a" and d["round_no"] == 2 and d["mode"] == "chosen"
    ev = json.loads(d["evidence_json"])
    assert ev["gains"] == [{"seed": "B002", "platform": "scopus"}]
    assert ev["sources"] and all(set(s) == {"seed", "section", "offset", "pattern", "quote_sha256"} for s in ev["sources"])
    assert "Keywords" not in d["evidence_json"]   # a reference to the quote, never its text
    assert _cli(p, "seeds", "names", "--admit", "beta drift").exit_code == 1   # already a term of block a
    # with a note, a name without a gain is accepted; the page's twin
    c = TestClient(create_app(Project.load(p.root)))
    bad = c.post("/api/seeds/names/admit", json={"name": "phase binding", "block": "a"})
    assert bad.status_code == 400 and names.NOT_ADMITTED in bad.json()["detail"]
    assert c.post("/api/seeds/names/admit", json={"name": "x", "block": "zz"}).status_code == 404
    ok = c.post("/api/seeds/names/admit", json={"name": "phase binding", "block": "a", "note": "used in field B",
                                               "author": "ana"}).json()
    assert ok["version"] == 3 and ok["note"] == "admit phase binding to block a: accepted by ana: used in field B"
    got = c.get("/api/seeds/names").json()
    assert {x["key"]: x["status"] for x in got["candidates"]}["phase binding"] == "admitted to block a"
    # an admission is one journalled change: undo removes the version, its round and the ledger row
    p = Project.load(p.root)
    ch = journal.changes(p)[0]
    assert ch["kind"] == "name_admit" and ch["kind_label"] == "name admitted" and ch["author"] == "ana"
    journal.undo(p, ch["change_id"], author="ana")
    assert [r["version"] for r in artifacts.list_versions(p, "synonyms")] == [1, 2]
    assert (p.artifact_dir("synonyms") / "v3.yaml").exists()   # its file stays
    assert not p.store.query("SELECT 1 FROM query_rounds WHERE round_no=3")
    assert names.admitted_names(p) == ["beta drift"]


def test_admission_while_frozen(tmp_path):
    from wider.construct import artifacts
    from wider.core import journal

    p = _with_query(tmp_path)
    artifacts.set_status(p, "synonyms", 1, "frozen")
    r = _cli(p, "seeds", "names", "--admit", "beta drift", "--count-seen")
    assert r.exit_code == 0, r.output
    assert "v1 is frozen. Saving v2 reopens the query" in r.output
    p = Project.load(p.root)
    adm = next(c for c in journal.changes(p) if c["kind"] == "name_admit")
    unl = next(c for c in journal.changes(p) if c["kind"] == "artifact_unlock")
    import pytest
    with pytest.raises(ValueError, match=f"it unlocked the frozen query \\(change {unl['change_id']}\\); an unlock is final"):
        journal.undo(p, adm["change_id"], author="ana")
    assert len(artifacts.list_versions(p, "synonyms")) == 2   # nothing undone


def test_name_map_from_evidence_and_answers(tmp_path):
    from wider.seeds import names

    p = _with_query(tmp_path)
    with p.store.tx() as c:
        c.execute("INSERT INTO decisions(kind, evidence_json, chosen) VALUES(?,?,?)",
                  ("refine_same_construct", json.dumps({"names": ["Beta drift", "alpha coupling"]}), "different"))
        c.execute("INSERT INTO decisions(kind, evidence_json, chosen) VALUES(?,?,?)",
                  ("refine_same_construct", json.dumps({"names": ["neural pairs", "phase binding"]}), "same"))
    m = names.name_map(p)
    assert ["alpha coupling", "neural pairs", "phase binding"] in m["groups"]
    assert m["look_alikes"] == [{"name": "beta drift", "of": "alpha coupling"}]
    assert not any("beta drift" in g for g in m["groups"])
    r = _cli(p, "seeds", "names", "--map")
    assert r.exit_code == 0, r.output
    assert "same construct: alpha coupling, neural pairs, phase binding" in r.output
    assert "look-alike: beta drift (differs from alpha coupling)" in r.output
    run = next((p.root / "runs" / "seeds" / "mine").iterdir())
    svg = (run / "name_map.svg").read_text(encoding="utf-8")
    assert "<title>Name map of the seed papers</title>" in svg and "Look-alike: beta drift" in svg
    assert json.loads((run / "name_map.json").read_text())["look_alikes"] == m["look_alikes"]
    c = TestClient(create_app(Project.load(p.root)))
    assert c.get("/api/seeds/names").json()["map"] == m


def _card(body: str, replies: dict) -> dict:
    """seeds.js (with refine.js for the confirm step) on the stand-in page; ``body`` runs after SeedNames.load()."""
    from tests.unit.test_backends_js_node import ESC, PAGE, STATIC, run_node

    js = "\n".join((STATIC / f).read_text(encoding="utf-8") for f in ("refine.js", "seeds.js"))
    return run_node(ESC + PAGE + js + f"\nObject.assign(replies, {json.dumps(replies)});\n"
                    "SeedNames.wire(); await SeedNames.load(); await flush();\n" + body)


CARD_NAMES = {"candidates": [
    {"name": "beta drift", "key": "beta drift", "pattern": "keywords", "count": 1, "same_as": [], "admitted": None,
     "block": "a", "gains": [{"seed": "B002", "platform": "scopus"}], "status": "retrieves B002"},
    {"name": "phase binding", "key": "phase binding", "pattern": "also called", "count": 3,
     "same_as": ["alpha coupling"], "admitted": None, "block": "a", "gains": [],
     "status": "not admitted: retrieves no development seed"},
    {"name": "alpha coupling", "key": "alpha coupling", "pattern": "keywords", "count": 1, "same_as": [],
     "admitted": None, "block": "a", "gains": [], "held": ["a"], "status": "already a term of block a"}],
    "blocks": ["a"], "notice": None, "map": {"groups": [["alpha coupling", "phase binding"]], "look_alikes": []}}


def test_names_card_add_to_block_confirm_step():
    frozen = "v1 is frozen. Saving v2 reopens the query: the 2 validation papers seen so far no longer count as fresh validation."
    admitted = {"name": "beta drift", "block": "a", "version": 2, "notice": frozen,
                "note": "admit beta drift to block a: retrieves B002"}
    replies = {"/api/seeds/names": CARD_NAMES, "/api/seeds/streams": {"references": 0, "venues": [], "names": []},
               "/api/construct/synonyms": {"save_notice": frozen}, "/api/seeds/names/admit": admitted}
    out = _card("""
      $("#sn-block").value = "a";
      await click("#sn-names", "[data-admit]", b => b.dataset.admit === "beta drift");   // askChoice -> null: Cancel
      const cancelled = calls.filter(c => c[0] === "/api/seeds/names/admit").length, msg1 = $("#sn-msg").textContent;
      let asked = null;
      global.askChoice = async (html) => { asked = html; return { act: "go" }; };
      $("#sn-note").value = "seen in field B";
      await click("#sn-names", "[data-admit]", b => b.dataset.admit === "beta drift");
      const buttons = [...$("#sn-names").querySelectorAll("[data-admit]")].map(b => b.dataset.admit);
      console.log(JSON.stringify({cancelled, msg1, asked, calls, buttons, msg2: $("#sn-msg").textContent}));
    """, replies)
    assert out["buttons"] == ["beta drift", "phase binding"]   # a name the block already holds has no button
    assert out["cancelled"] == 0 and out["msg1"] == "Nothing saved."
    assert frozen in out["asked"]
    post = [c for c in out["calls"] if c[0] == "/api/seeds/names/admit"]
    assert post == [["/api/seeds/names/admit", "POST", {"name": "beta drift", "block": "a", "note": "seen in field B"}]]
    assert out["msg2"] == frozen + " synonyms v2 saved: admit beta drift to block a: retrieves B002"


def test_names_card_double_click_adds_once():
    """Two clicks on "add to block" send one admission (ui-tester F1)."""
    admitted = {"name": "beta drift", "block": "a", "version": 2, "notice": None, "note": "admit beta drift"}
    replies = {"/api/seeds/names": CARD_NAMES, "/api/seeds/streams": {"references": 0, "venues": [], "names": []},
               "/api/construct/synonyms": {}, "/api/seeds/names/admit": admitted}
    out = _card("""
      $("#sn-block").value = "a";
      global.askChoice = async () => ({ act: "go" });
      const b = [...$("#sn-names").querySelectorAll("[data-admit]")].find(x => x.dataset.admit === "beta drift");
      b.onclick(); b.onclick(); await flush();
      console.log(JSON.stringify({posts: calls.filter(c => c[0] === "/api/seeds/names/admit").length}));
    """, replies)
    assert out == {"posts": 1}



def test_names_card_asks_for_the_note_before_the_confirm_step():
    """A name that retrieves no seed in its block needs a note: the card says so before the frozen-query confirm."""
    replies = {"/api/seeds/names": CARD_NAMES, "/api/seeds/streams": {"references": 0, "venues": [], "names": []},
               "/api/construct/synonyms": {"save_notice": "v1 is frozen."}, "/api/seeds/names/admit": {}}
    out = _card("""
      $("#sn-block").value = "a";
      let asked = 0;
      global.askChoice = async () => { asked += 1; return { act: "go" }; };
      await click("#sn-names", "[data-admit]", b => b.dataset.admit === "phase binding");
      console.log(JSON.stringify({asked, calls, msg: $("#sn-msg").textContent}));
    """, replies)
    assert out["asked"] == 0 and not [c for c in out["calls"] if c[0] == "/api/seeds/names/admit"]
    assert out["msg"] == ('"phase binding" retrieves no development seed in block a: write a note to add it anyway. '
                          "Nothing saved.")

def test_admitted_name_is_a_suggested_edit_source(tmp_path):
    """2a's suggested edits offer a name admitted from the seed papers for another block a missed seed fails."""
    from wider.construct import artifacts
    from wider.search.diagnostics import suggested_edits
    from wider.seeds import names

    p = _with_query(tmp_path)
    two = {"blocks": [{"key": "a", "name": "Coupling", "terms": ['"alpha coupling"']},
                      {"key": "b", "name": "Pairs", "terms": ["pairs"]}], "logic": {"and": ["a", "b"]}}
    artifacts.save_new_version(p, "synonyms", two, origin="manual")
    r = names.admit(p, "beta drift", "a", note="named in the seed's keywords")
    assert r["gains"] == []   # B002 still fails block b
    got = [e for e in suggested_edits(p)["edits"] if e["source"] == "a name admitted from the seed papers"]
    assert [e["change"] for e in got] == [{"op": "add_term", "block": "b", "term": '"beta drift"'}]


# ----------------------------------------------------------------- candidates from the seed papers (2c, D16)
def _openalex(request):
    """Mock OpenAlex for seed chaining: B001 -> W1, B002 -> W2; W1 cites X (imported) and Y, W2 cites Y, Z cites W1."""
    import httpx

    flt = request.url.params.get("filter", "")
    work = {"X": ("10.1000/abc", "Team meetings after errors", 2020), "Y": ("10.1000/y1", "Why pairs drift", 2019),
            "Z": ("10.1000/z1", "Coupling revisited", 2023)}

    def w(k):
        doi, title, year = work[k]
        return {"id": f"https://openalex.org/{k}", "doi": f"https://doi.org/{doi}", "title": title,
                "publication_year": year}

    body = {"doi:10.1000/a1|10.1000/a2": [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/a1"},
                                          {"id": "https://openalex.org/W2", "doi": "https://doi.org/10.1000/a2"}],
            "cited_by:W1": [w("X"), w("Y")], "cited_by:W2": [w("Y")], "cites:W1": [w("Z")], "cites:W2": []}
    if flt not in body:
        return httpx.Response(404)
    return httpx.Response(200, json={"results": body[flt], "meta": {"next_cursor": None}})


def _chained(tmp_path, monkeypatch, name, calls):
    from tests.unit.helpers import consent, mock_client
    from tests.unit.test_seed import _imported_proj

    monkeypatch.setattr("wider.prior.chain.time.sleep", lambda *_: None)
    monkeypatch.setattr("wider.enrich.sources._client", lambda *a, **k: mock_client(_openalex, calls))
    (tmp_path / name).mkdir()
    p = consent(_imported_proj(_proj(tmp_path / name), tmp_path / name))
    return p


def test_seed_chain_candidates(tmp_path, monkeypatch):
    """Scenarios "Chaining with a mock client" and "Dismissing a candidate" (construct-workbench, 2c D16)."""
    from wider.seeds import chain

    calls: list = []
    p = _chained(tmp_path, monkeypatch, "a", calls)
    before = (p.root / "benchmark.csv").read_text(encoding="utf-8")
    r = _cli(p, "benchmark", "chain")
    assert r.exit_code == 0, r.output
    assert "1 Validation seed not chained" in r.output
    assert "retrieved by the query" in r.output
    sent = " ".join(str(c.url) for c in calls)
    assert calls and "10.1000%2Fv1" not in sent and "10.1000/v1" not in sent   # the Validation seed is in no request
    assert (p.root / "benchmark.csv").read_text(encoding="utf-8") == before
    rows = {c["title"]: c for c in chain.candidates(p)}
    assert set(rows) == {"Team meetings after errors", "Why pairs drift", "Coupling revisited"}
    assert rows["Why pairs drift"]["linking_seeds"] == ["B001", "B002"]
    assert rows["Coupling revisited"]["direction"] == "cited_by"
    assert [k for k, c in rows.items() if c["retrieved"]] == ["Team meetings after errors"]
    assert {c["status"] for c in rows.values()} == {"new"}
    log = p.store.query("SELECT purpose, service, n_items FROM external_requests")
    assert [(x["purpose"], x["service"]) for x in log] == [("seed_chain", "openalex")]
    assert log[0]["n_items"] == 6   # 2 DOIs resolved, 2 reference lists, 2 citing lists

    # adding the marked one to Validation: source seed_chain:B001 and a warning naming the imported record
    x, y, z = (rows[t]["candidate_id"] for t in ("Team meetings after errors", "Why pairs drift", "Coupling revisited"))
    r = _cli(p, "benchmark", "add", "--from-candidate", str(x), "--set", "Validation")
    assert r.exit_code == 0, r.output
    raw_id = rows["Team meetings after errors"]["retrieved_raw_id"]
    assert "seed_chain:B001" in r.output and f"matches an imported record (s.ris, record {raw_id}" in r.output
    added = {b.benchmark_id: b for b in p.read_benchmark()}
    new_id = next(k for k, b in added.items() if b.doi == "10.1000/abc")
    assert (added[new_id].set, added[new_id].source) == ("Validation", "seed_chain:B001")
    # while the set is hidden, the list no longer tells that the query finds this Validation paper
    listed = {c["candidate_id"]: c for c in chain.candidates(p)}
    assert listed[x]["status"] == "added" and listed[x]["benchmark_id"] == new_id and not listed[x]["retrieved"]
    # adding another (the route, the Setup list's Add) writes it with no warning
    client = TestClient(create_app(Project.load(p.root)))
    res = client.post(f"/api/benchmark/candidates/{z}/add", json={"set": "Validation"})
    assert res.status_code == 200 and res.json()["notices"] == {}
    assert res.json()["added"]["source"] == "seed_chain:B001"
    assert client.post(f"/api/benchmark/candidates/{z}/add", json={"set": "Development"}).status_code == 409
    assert client.post(f"/api/benchmark/candidates/{z}/dismiss").status_code == 409
    assert client.post("/api/benchmark/candidates/99/dismiss").status_code == 404

    # dismissing: kept with status, who and when, listed last and marked "dismissed"
    r = _cli(p, "benchmark", "candidates", "--dismiss", str(y))
    assert r.exit_code == 0 and f"candidate {y} dismissed by" in r.output
    out = r.output.splitlines()
    assert "dismissed" in [ln for ln in out if "Why pairs drift" in ln][0]
    listed = chain.candidates(p)
    assert listed[-1]["candidate_id"] == y and listed[-1]["status"] == "dismissed"
    assert listed[-1]["by"] and listed[-1]["at"]
    # a second run keeps every status and adds nothing new
    r = _cli(p, "benchmark", "chain")
    assert r.exit_code == 0 and "0 new candidate(s)" in r.output
    assert [c["status"] for c in chain.candidates(p)] == [c["status"] for c in listed]

    # the same dismissal on the Setup list gives the same row
    q = _chained(tmp_path, monkeypatch, "b", [])
    assert _cli(q, "benchmark", "chain").exit_code == 0
    cq = TestClient(create_app(Project.load(q.root)))
    yq = next(c["candidate_id"] for c in cq.get("/api/benchmark/candidates").json()["candidates"]
              if c["title"] == "Why pairs drift")
    via_route = [c for c in cq.post(f"/api/benchmark/candidates/{yq}/dismiss",
                                    headers={"x-wider-author": "Reviewer%20A"}).json()["candidates"]
                 if c["candidate_id"] == yq][0]
    via_cli = [c for c in chain.candidates(Project.load(p.root)) if c["candidate_id"] == y][0]
    assert via_route["by"] == "Reviewer A" and via_route["at"]   # the name the page sent, never the server's login
    via_route["by"] = via_cli["by"]
    keep = ("title", "doi", "status", "linking_seeds", "direction", "n_linking", "by")
    assert {k: via_route[k] for k in keep} == {k: via_cli[k] for k in keep}

    # without consent nothing is sent: exit 2 on the CLI, 403 on the route
    from wider.core import lookups
    lookups.revoke(q)
    sent_before = len(calls)
    assert _cli(q, "benchmark", "chain").exit_code == 2
    assert TestClient(create_app(Project.load(q.root))).post("/api/benchmark/chain", json={}).status_code == 403
    assert len(calls) == sent_before


def test_candidates_list_on_the_setup_page():
    """The Setup seed step's "Candidates from seed papers": the retrieved mark, Add to either set and Dismiss on a new
    candidate, no buttons on an added one, Dismiss gone on a dismissed one; Find candidates sends the direction and
    shows the run's messages; each button posts to its route."""
    from tests.unit.test_seed import _seed_page

    cands = [{"candidate_id": 1, "title": "Team meetings after errors", "doi": "10.1000/abc", "year": 2020,
              "direction": "references", "linking_seeds": ["B001"], "status": "new", "retrieved": True},
             {"candidate_id": 2, "title": "Coupling revisited", "doi": "10.1000/z1", "year": 2023,
              "direction": "cited_by", "linking_seeds": ["B001"], "status": "added", "benchmark_id": "B004",
              "retrieved": False},
             {"candidate_id": 3, "title": "Why pairs drift", "doi": "10.1000/y1", "year": 2019, "direction": "both",
              "linking_seeds": ["B001", "B002"], "status": "dismissed", "by": "Reviewer A",
              "at": "2026-10-20T10:00:00Z", "retrieved": False}]
    body = """
      Object.assign(replies, {"/api/benchmark/candidates": {candidates: CANDS},
        "/api/benchmark/chain": {seeds_chained: 2, found: 3, new: 0, messages: ["1 Validation seed not chained"]},
        "/api/benchmark/candidates/1/add": {added: {benchmark_id: "B005", set: "Validation"},
                                            notices: {B005: ["B005 matches an imported record"]}, rows: []},
        "/api/benchmark/candidates/1/dismiss": {candidates: CANDS}});
      await window.loadMore(); await flush();
      const html = $("#seed-chain").innerHTML;
      $("#chain-dir").value = "references";
      await click("#seed-chain", "[data-cand-dismiss]");
      const dismissMsg = $("#chain-msg").innerHTML;
      await click("#seed-chain", "[data-cand-add]", b => b.dataset.set === "Validation");
      const addMsg = $("#chain-msg").innerHTML;
      await $("#chain-run").onclick(); await flush();
      console.log(JSON.stringify({html, calls, dismissMsg, addMsg, chainMsg: $("#chain-msg").innerHTML}));
    """.replace("CANDS", __import__("json").dumps(cands))
    out = _seed_page([], body)
    html = out["html"]
    rows = {c["title"]: html[html.index(c["title"]):html.index("</tr>", html.index(c["title"]))] for c in cands}
    assert "retrieved by the query" in rows["Team meetings after errors"]
    assert "retrieved by the query" not in rows["Coupling revisited"] + rows["Why pairs drift"]
    assert rows["Team meetings after errors"].count("data-cand-add") == 2 and "data-cand-dismiss" in rows[
        "Team meetings after errors"]
    assert "added as B004" in rows["Coupling revisited"] and "data-cand" not in rows["Coupling revisited"]
    assert "dismissed" in rows["Why pairs drift"] and "Reviewer A" in rows["Why pairs drift"]
    assert "data-cand-dismiss" not in rows["Why pairs drift"] and "data-cand-add" in rows["Why pairs drift"]
    posts = [c for c in out["calls"] if c[1] == "POST"]
    assert posts == [["/api/benchmark/candidates/1/dismiss", "POST", {}],
                     ["/api/benchmark/candidates/1/add", "POST", {"set": "Validation"}],
                     ["/api/benchmark/chain", "POST", {"direction": "references"}]]
    assert out["dismissMsg"] == "candidate 1 dismissed (kept, listed last)"
    assert out["addMsg"] == "B005 added to Validation from candidate 1; see the note beside B005"
    assert out["chainMsg"] == "2 development seed(s) chained: 3 works, 0 new; 1 Validation seed not chained"


def test_chain_candidate_follows_its_seed_paper(tmp_path, monkeypatch):
    """Risk check of 2c (R1, R2): a candidate that became a hidden Validation seed by another route shows no
    "retrieved by the query" mark after a later import; removing the seed paper an added candidate became makes it a
    candidate again, so it never points at a paper that reuses the id."""
    from tests.unit.helpers import mock_client
    from wider.construct import seed
    from wider.ingest.importer import add_file, import_all
    from wider.seeds import chain

    calls = []
    p = _chained(tmp_path, monkeypatch, "r1", calls)
    chain.chain_seeds(p, "both", client=mock_client(_openalex, calls))
    y = next(c for c in chain.candidates(p) if c["title"] == "Why pairs drift")
    assert _cli(p, "benchmark", "add", "--doi", "10.1000/y1", "--set", "Validation").exit_code == 0
    (tmp_path / "y.ris").write_text("TY  - JOUR\nTI  - Why pairs drift\nPY  - 2019\nDO  - 10.1000/y1\nER  -\n",
                                    encoding="utf-8")
    add_file(p, "scopus", tmp_path / "y.ris")
    import_all(p)
    row = next(c for c in chain.candidates(p) if c["candidate_id"] == y["candidate_id"])
    assert not row["retrieved"]
    line = next(ln for ln in _cli(p, "benchmark", "candidates").output.splitlines() if "10.1000/y1" in ln)
    assert "retrieved" not in line, line

    q = _chained(tmp_path, monkeypatch, "r2", calls)
    chain.chain_seeds(q, "both", client=mock_client(_openalex, calls))
    cid = next(c for c in chain.candidates(q) if c["title"] == "Why pairs drift")["candidate_id"]
    added, _ = chain.add(q, cid, "Development")
    seed.remove(q, added.benchmark_id)
    row = next(c for c in chain.candidates(q) if c["candidate_id"] == cid)
    assert row["status"] == "new" and row["benchmark_id"] is None
    assert _cli(q, "benchmark", "candidates", "--dismiss", str(cid)).exit_code == 0


def test_tool_gate_refuses_flags_that_undo_tool_off():
    """Risk check of 2c (R4): a command that keeps every tool-off flag but turns tools back on later (an MCP config,
    the same flag again with another value, a yolo mode, an alias or ``--flag=value``) is refused; the presets pass."""
    from wider.providers.gate import tool_gate
    from wider.providers.presets import config_from_preset

    for pid, extra in (("claude-code", ["--mcp-config", "fetch.json"]), ("claude-code", ["--tools", "Bash"]),
                       ("codex", ["-c", 'web_search="live"']), ("codex", ["-s", "danger-full-access"]),
                       ("codex", ["--full-auto"]), ("gemini-cli", ["--approval-mode=yolo"]),
                       ("gemini-cli", ["-y"]), ("gemini-cli", ["--extensions", "fetch"])):
        cfg = config_from_preset(pid, name=pid, model="m")
        assert tool_gate(cfg) is None, pid
        why = tool_gate(cfg.model_copy(update={"command": cfg.command + extra}))
        assert why and "turns the agent's tools back on" in why, (pid, extra, why)

