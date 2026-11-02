"""Ask WIDER's sections and retrieval (guide-assistant D2-D6, D22; tasks 3.1-3.5, 3.7). Synthetic projects only, the
fake embedder for meaning, and no network: a remote embedding backend is never contacted because consent is missing."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from tests.unit.test_methods import FIXTURE, format_problems
from wider.assistant import retrieve as r
from wider.assistant.sections import all_sections, docs_key, guide_sections
from wider.core.models import EmbeddingConfig
from wider.core.project import Project
from wider.guide import methods as m
from wider.guide import pages

FAKE = EmbeddingConfig(name="fake", kind="fake", model="fake", dim=64)


def _project(tmp_path: Path, emb: EmbeddingConfig | None = FAKE) -> Project:
    p = Project.init(tmp_path / "r", platforms=["scopus"])
    p.config.embeddings = [emb] if emb else []
    p.config.default_embedding = emb.name if emb else None
    p.save()
    return p


def _ids(res) -> list[str]:
    return [s.id for s in res.sections]


# ------------------------------------------------------------------------------------------------ 3.1 contract
@pytest.mark.parametrize("root", [FIXTURE, m.HERE], ids=["fixture", "shipped"])
def test_methods_contract(root):
    """The methods folder keeps methods-explainer's contract (D2): front matter keys, the three layers, settings that
    are numbers, choices or flags, artifact kinds and pages that exist, and the section ids and anchors."""
    assert format_problems(root) == []
    for t in m.topics(root):
        assert [lay["layer"] for lay in m.topic(t["id"], root)["layers"]] == ["short", "why", "auditors"]
    for s in m.sections(root):
        if s.kind == "methods":
            assert s.id == f"methods/{s.topic}/{s.layer}" and s.anchor == f"#{s.id}"
        else:
            assert s.kind == "glossary" and s.id.startswith("glossary/") and s.anchor == f"#methods/{s.id}"


# ------------------------------------------------------------------------------------------------ 3.2 sections
def test_sections_ids_stable():
    """Ids are unique, read the same twice, and follow D3: ``guide/<page>/<part>`` for each page's parts."""
    a, b = all_sections(FIXTURE), all_sections(FIXTURE)
    assert [s.id for s in a] == [s.id for s in b]
    assert len({s.id for s in a}) == len(a)
    g = guide_sections()
    assert {s.pages[0] for s in g} == set(pages())
    for s in g:
        page = s.pages[0]
        assert s.id.startswith(f"guide/{page}/") and s.anchor == f"#{page}"
        assert s.context.startswith("WIDER guide, page ")
    assert docs_key(a) == docs_key(list(reversed(b)))


def test_sections_layers_separate():
    """Each layer of a topic is its own section, so a plain and an auditor question can match different layers."""
    by_id = {s.id: s for s in all_sections(FIXTURE)}
    layers = [by_id[f"methods/alpha/{lay}"] for lay in ("short", "why", "auditors")]
    assert len({s.text for s in layers}) == 3
    assert "estimate.py" in layers[2].text and "estimate.py" not in layers[0].text


def test_sections_read_no_project_file(tmp_path, monkeypatch):
    """Building the sections opens only files under ``wider/``, never one in the project folder."""
    p = _project(tmp_path)
    opened: list[str] = []
    real_open, real_read = Path.open, Path.read_text

    def spy_open(self, *a, **k):
        opened.append(str(self))
        return real_open(self, *a, **k)

    def spy_read(self, *a, **k):
        opened.append(str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    m._load.cache_clear()
    m._glossary.cache_clear()
    all_sections()
    root = str(Path(m.__file__).resolve().parents[2])
    assert opened and all(str(Path(f).resolve()).startswith(root) for f in opened)
    assert not any(str(p.root) in f for f in opened)


# ------------------------------------------------------------------------------------------------ 3.3 keywords
def test_stemmer_meets_forms():
    assert len({r.stem(w) for w in ("sampled", "samples", "sampling", "sample")}) == 1
    assert r.stem("labelled") == r.stem("label") and r.stem("studies") == "study" and r.stem("bias") == "bias"


def _sec(i: str, text: str):
    from wider.guide.methods import Section
    return Section(id=i, kind="methods", topic=i, layer="short", title=i, context="", text=text, settings=(),
                   artifacts=(), pages=(), anchor="")


def test_bm25_ranks_exact_term_first():
    """The section that names the question's exact term ranks above sections that only share common words, and a
    glossary entry named by the question comes first among the sent sections."""
    secs = [_sec("a", "The sample is drawn from the ranked list of records and checked by hand."),
            _sec("b", "The Horvitz-Thompson estimator weights each sampled record by its inverse inclusion chance."),
            _sec("c", "Records in the sample are read and labelled, then the sample is closed.")]
    got = r.keyword_rank(secs, "Which estimator is used for the sample?")
    assert got[0][0].id == "b"
    assert r.keyword_rank(all_sections(FIXTURE), "What is a band?")[0][0].id == "glossary/band"
    assert _ids(r.retrieve("What is deduplication?"))[0] == "glossary/deduplication"


def test_synonym_finds_recall(tmp_path):
    """With no embedding backend, "sensitivity" finds the recall entry and the recall-estimate topic through the
    glossary synonym, and the retrieval is keywords alone."""
    p = _project(tmp_path, emb=None)
    res = r.retrieve("How is sensitivity estimated?", project=p)
    assert res.matched and res.retrieval == "keywords" and res.notes == []
    assert "glossary/recall" in _ids(res)
    assert any(i.startswith("methods/recall-estimate/") for i in _ids(res))
    assert r.stem("recall") in r.query_terms("sensitivity", r.synonym_groups(all_sections()))


@pytest.mark.parametrize("q", ["What is the capital of France?", "How do I bake a cake?", "What is the weather today?"])
def test_no_section_for_unrelated_question(q, tmp_path, monkeypatch):
    """An unrelated question finds no section, with and without the fake embedder, and nothing is embedded."""
    monkeypatch.setattr(r, "_loaded", lambda cfg: pytest.fail("embedded an unmatched question"))
    for emb in (None, FAKE):
        res = r.retrieve(q, project=_project(tmp_path / (emb.name if emb else "none"), emb=emb))
        assert res.sections == [] and not res.matched


# ------------------------------------------------------------------------------------------------ 3.4 meaning
def _count_embeds(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = r._loaded

    def counted(cfg):
        emb = real(cfg)
        if getattr(emb, "_counted", False):
            return emb
        emb._counted = True
        orig = emb.embed

        def embed(texts, progress=None):
            calls.append(len(texts))
            return orig(texts, progress)

        emb.embed = embed
        return emb

    monkeypatch.setattr(r, "_loaded", counted)
    return calls


def test_index_built_once_and_reused(tmp_path, monkeypatch):
    p = _project(tmp_path)
    calls = _count_embeds(monkeypatch)
    res = r.retrieve("How are duplicates merged?", project=p)
    assert res.retrieval == "keywords and meaning"
    files = sorted((p.root / "assistant" / "index").iterdir())
    assert [f.suffix for f in files] == [".json", ".npy"] and files[0].name.startswith("fake-")
    n_sections = len(all_sections())
    assert calls == [n_sections, 1]
    r.retrieve("How are duplicates merged?", project=p)
    assert calls == [n_sections, 1, 1]           # the index is read from its file; only the question is embedded



@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_index_owner_only_and_cut_file_rebuilt(tmp_path):
    """The index files are readable by the owner only (0600, no temporary file left); a .npy cut short by a crash is
    built again instead of failing the question."""
    import numpy as np

    p = _project(tmp_path)
    r.retrieve("How are duplicates merged?", project=p)
    files = sorted((p.root / "assistant" / "index").iterdir())
    assert [f.suffix for f in files] == [".json", ".npy"]
    assert all(stat.S_IMODE(os.stat(f).st_mode) == 0o600 for f in files)
    npy = files[1]
    npy.write_bytes(npy.read_bytes()[:40])
    res = r.retrieve("How are duplicates merged?", project=p)
    assert res.retrieval == "keywords and meaning" and r.NOTE_FAILED not in res.notes
    assert np.load(npy).shape[0] == len(all_sections()) and stat.S_IMODE(os.stat(npy).st_mode) == 0o600

def _copy_methods(tmp_path: Path) -> Path:
    root = tmp_path / "methods"
    shutil.copytree(m.HERE, root, ignore=shutil.ignore_patterns("*.py", "__pycache__"))
    return root


def test_index_rebuilt_when_text_changes(tmp_path):
    p = _project(tmp_path)
    root = _copy_methods(tmp_path)
    r.retrieve("How are duplicates merged?", project=p, methods_root=root)
    first = sorted(f.name for f in (p.root / "assistant" / "index").iterdir())
    f = root / "deduplication.md"
    f.write_text(f.read_text(encoding="utf-8").replace("## In short\n", "## In short\n\nAn added sentence.\n"),
                 encoding="utf-8")
    r.retrieve("How are duplicates merged?", project=p, methods_root=root)
    second = sorted(f.name for f in (p.root / "assistant" / "index").iterdir())
    assert len(second) == 2 and set(first).isdisjoint(second)       # a new index, the old files removed


def test_index_holds_shipped_text_only(tmp_path, monkeypatch):
    """The texts embedded are the question and the shipped sections, nothing from the project."""
    p = _project(tmp_path)
    p.config.questions = ["Does street tree canopy cool city pavements?"]
    p.save()
    seen: list[str] = []
    real = r._loaded

    def spy(cfg):
        emb = real(cfg)
        orig = emb.embed
        emb.embed = lambda texts, progress=None: (seen.extend(texts), orig(texts, progress))[1]
        return emb

    monkeypatch.setattr(r, "_loaded", spy)
    q = "How are duplicates merged?"
    r.retrieve(q, project=p)
    shipped = {f"{s.context}\n{s.title}\n{s.text}" for s in all_sections()}
    assert set(seen) == shipped | {q}
    assert not any("canopy" in t for t in seen)


def test_rrf_merges_both(tmp_path, monkeypatch):
    """A section only the meaning list ranks high is lifted into the sent sections by reciprocal rank fusion."""
    p = _project(tmp_path)
    sections = all_sections(FIXTURE)
    target = next(i for i, s in enumerate(sections) if s.id == "methods/beta/why")
    monkeypatch.setattr(r, "meaning_rank", lambda *a: [target])
    kw = [s.id for s, _ in r.keyword_rank(sections, "What is recall?")]
    res = r.retrieve("What is recall?", project=p, sections=sections)
    assert res.retrieval == "keywords and meaning"
    assert "methods/beta/why" in _ids(res)
    assert "methods/beta/why" not in kw[:r.MAX_SECTIONS]


def test_meaning_never_passes_the_keyword_gate(tmp_path, monkeypatch):
    """Even a meaning list that ranks every section cannot turn a keyword no-match into a match."""
    p = _project(tmp_path)
    monkeypatch.setattr(r, "meaning_rank", lambda project, cfg, sections, q, key: list(range(len(sections))))
    res = r.retrieve("What is the capital of France?", project=p)
    assert res.sections == [] and not res.matched


def test_cloud_embedding_without_consent_falls_back(tmp_path, monkeypatch):
    """A remote embedding backend without consent for ``assistant``: keywords alone, the consent note and the payload
    the "Allow <backend> for Ask WIDER…" button opens; nothing is sent."""
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("contacted the embedding service"))
    p = _project(tmp_path, EmbeddingConfig(name="cloud-emb", kind="openai_compat", model="emb-1",
                                           base_url="https://emb.example.invalid/v1"))
    res = r.retrieve("How is sensitivity estimated?", project=p)
    assert res.retrieval == "keywords" and res.notes == [r.NOTE_CONSENT]
    need = res.consent["backend_consent_required"]
    assert need["operation"] == "assistant" and need["backend"] == "cloud-emb" and need["kind"] == "embedding"
    assert "glossary/recall" in _ids(res)
    assert not (p.root / "assistant" / "index").exists()


def test_embedding_not_downloaded_falls_back_without_download(tmp_path, monkeypatch):
    """A sentence-transformers default whose weights are not here: keywords alone with the note; no embedder is made,
    so nothing can be downloaded."""
    import wider.core.backends as backends
    import wider.embeddings.registry as registry

    monkeypatch.setattr(backends, "is_downloaded", lambda cfg: False)
    monkeypatch.setattr(registry, "make_embedder", lambda *a, **k: pytest.fail("made an embedder"))
    p = _project(tmp_path, EmbeddingConfig(name="minilm", kind="sentence_transformers",
                                           model="sentence-transformers/all-MiniLM-L6-v2"))
    res = r.retrieve("How is sensitivity estimated?", project=p)
    assert res.retrieval == "keywords" and res.notes == [r.NOTE_NOT_DOWNLOADED] and res.consent is None
    assert res.sections


def test_failing_embedder_falls_back(tmp_path, monkeypatch):
    p = _project(tmp_path)

    def boom(*a):
        raise RuntimeError("down")

    monkeypatch.setattr(r, "meaning_rank", boom)
    res = r.retrieve("How are duplicates merged?", project=p)
    assert res.retrieval == "keywords" and res.notes == [r.NOTE_FAILED] and res.sections


# ------------------------------------------------------------------------------------------------ 3.5 depth
def test_depth_simpler_layers():
    res = r.retrieve("How are duplicates merged?", depth="simpler")
    ids = _ids(res)
    assert "methods/deduplication/short" in ids
    assert not any(i.endswith(("/why", "/auditors")) for i in ids)
    assert all(i.startswith(("glossary/", "methods/")) for i in ids)


def test_depth_detail_layers():
    res = r.retrieve("How are duplicates merged?", depth="detail")
    ids = _ids(res)
    assert ids[:2] == ["methods/deduplication/why", "methods/deduplication/auditors"]
    assert all(i.endswith(("/why", "/auditors")) for i in ids)


def test_plain_and_technical_question_pick_layers():
    """The spec's two questions: the plain one gets the glossary entry (or the In short layer) on top; the technical
    one's first methods section is the recall topic's For auditors layer (glossary entries are sent first). A synonym
    question finds the glossary entry it names."""
    plain = _ids(r.retrieve("What does recall mean?"))
    assert plain[0] in ("glossary/recall", "methods/recall-estimate/short")
    tech = _ids(r.retrieve("Which variance estimator gives the recall interval?"))
    assert [i for i in tech if i.startswith("methods/")][0] == "methods/recall-estimate/auditors"
    assert _ids(r.retrieve("What does stratum mean?"))[0] == "glossary/band"


# ------------------------------------------------------------------------------------------------ 3.7 documents key
def test_edited_synonym_found_and_index_rebuilt(tmp_path):
    """A word in no section finds nothing; added as a synonym of "recall" in a copy of the methods folder, the next
    question finds the recall sections, and the fake embedder's index file is replaced."""
    p = _project(tmp_path)
    root = _copy_methods(tmp_path)
    q = "What is findability?"
    before = all_sections(root)
    assert not any("findability" in f"{s.title} {s.context} {s.text}".lower() for s in before)
    assert r.retrieve(q, project=p, methods_root=root).sections == []
    r.retrieve("How is recall estimated?", project=p, methods_root=root)
    old = sorted(f.name for f in (p.root / "assistant" / "index").iterdir())
    g = root / "glossary.md"
    g.write_text(g.read_text(encoding="utf-8").replace("synonyms: sensitivity;", "synonyms: findability; sensitivity;"),
                 encoding="utf-8")
    after = all_sections(root)
    assert docs_key(after) != docs_key(before)
    assert "glossary/recall" in _ids(r.retrieve(q, project=p, methods_root=root))
    kw = [s.id for s, _ in r.keyword_rank(after, "How is findability estimated?")[:r.MAX_SECTIONS]]
    assert kw[0] == "glossary/recall" and any(i.startswith("methods/recall-estimate/") for i in kw)
    new = sorted(f.name for f in (p.root / "assistant" / "index").iterdir())
    assert len(new) == 2 and set(old).isdisjoint(new)


@pytest.mark.parametrize("meaning", [True, False], ids=["with-meaning", "keywords"])
def test_named_topic_and_entries_stay_first(meaning, tmp_path, monkeypatch):
    """M123 (31 Oct): a question that names a methods topic (its id's words in a row: "recall estimate") gets that
    topic and the glossary entries it names, even when the meaning list ranks other topics first; the hand check's
    question ended in a typed quote."""
    p = _project(tmp_path) if meaning else _project(tmp_path, emb=None)
    sections = all_sections()
    others = [i for i, s in enumerate(sections) if s.topic in ("recall-target", "stratified-sampling")]
    monkeypatch.setattr(r, "meaning_rank", lambda *a: others)
    res = r.retrieve("How is the recall estimate computed from the weighted validation sample?”", project=p,
                     sections=sections)
    assert res.retrieval == ("keywords and meaning" if meaning else "keywords")
    ids = _ids(res)
    assert ids[0] == "glossary/validation-sample"
    assert [i for i in ids if i.startswith("methods/")] == ["methods/recall-estimate/auditors",
                                                             "methods/recall-estimate/why"]


def test_check_retrieval_through_fused_path_with_fake_embedder(tmp_path):
    """M123 lead (31 Oct): the hand-check project's fake embedder made the answer "keywords and meaning", and the fused
    list pushed needed sections out. The shipped question set run as Ask WIDER ranks with the fake embedder passes as
    it does by keywords, and a needed section of every covered question is among the sections sent."""
    from wider.assistant import evaluate as ev

    p = _project(tmp_path)
    kw, fused = ev.check(), ev.check(project=p)
    assert kw["ok"] and fused["ok"], (kw["groups"], fused["groups"])
    assert {g: v["share"] for g, v in fused["groups"].items()} == {g: v["share"] for g, v in kw["groups"].items()}
    sections = all_sections()
    unsent = [q["question"] for q in ev.questions() if q["needs"] and not any(
        s.id in q["needs"] for s in r.retrieve(q["question"], project=p, sections=sections).sections)]
    assert unsent == []


def test_best_keyword_section_never_pushed_out_by_meaning(tmp_path, monkeypatch):
    """The best keyword section (after the named entries and topic) is sent even when the meaning list ranks it last."""
    p = _project(tmp_path)
    sections = all_sections()
    q = "How do I add a note or a tag to a record?"
    best = r.keyword_match(sections, q)[0][0].id
    monkeypatch.setattr(r, "meaning_rank", lambda *a: [i for i, s in enumerate(sections) if s.id != best][:40])
    res = r.retrieve(q, project=p, sections=sections)
    assert res.retrieval == "keywords and meaning" and best in _ids(res)


@pytest.mark.parametrize("question, page, want", [
    ("what should i do now", "model", ["guide/model/what-to-do", "guide/overview/what-to-do"]),
    ("What do I do next?", "dedup", ["guide/dedup/what-to-do", "guide/overview/what-to-do"]),
    ("What do I do first in WIDER?", None, ["guide/overview/what-to-do", "methods/overview/short"]),
    ("Where do I begin?", "general", ["guide/overview/what-to-do", "methods/overview/short"]),
])
def test_what_next_question_gets_the_pages_what_to_do(question, page, want):
    """M124 (31 Oct): "what should i do now" on the Model page has only stop words, so keywords cannot match it; a bare
    what-to-do-next question is answered from the page's "What to do" and the Overview's (in general, the Overview's
    and the methods overview), sent first. A question that asks something more is ranked as before."""
    sections = all_sections()
    res = r.retrieve(question, sections=sections, page=page, meaning=False)
    assert res.matched and _ids(res)[:len(want)] == want
    assert r.what_next(sections, "What do I do first with the recall estimate?", "recall") == []
    assert not r.retrieve("what is the weather now", sections=sections, page="model", meaning=False).matched
