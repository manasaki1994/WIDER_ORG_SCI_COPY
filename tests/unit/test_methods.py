"""The methods document's loader and format (methods-explainer D1–D4, D7): fixture topics for the loader and every
failure path, the shipped corpus for the format (``shipped_corpus``)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from types import UnionType
from typing import Literal, Union, get_args, get_origin

import pytest

from wider.construct.artifacts import KINDS
from wider.core.protocol import ReviewProtocol, iter_fields
from wider.guide import methods as m
from wider.guide import pages

FIXTURE = Path(__file__).parent.parent / "fixtures" / "methods"
SHIPPED_READY = True   # task 2.8 switches the shipped-corpus tests on
shipped_corpus = pytest.mark.skipif(not SHIPPED_READY, reason="shipped methods topics are switched on by task 2.8")
REQUIRED = ("overview", "query-construction", "development-validation-split", "deduplication", "ranking",
            "development-sample", "stratified-sampling", "recall-estimate", "recall-target", "llm-screening",
            "labels-and-agreement", "blinding", "consent-and-privacy", "provenance-and-reporting")
FORMULA = set("=<>≤≥±Σ√^$\\")
LIMITS = {"short": 120, "why": 250, "auditors": 200}
GLOSSARY_WORDS = 60


# ----------------------------------------------------------------------------------------------- the checks
def _numeric_choice_flag() -> set[str]:
    """Protocol keys whose type is int, float, bool or a Literal, each optionally ``| None`` (D1)."""
    out = set()
    for section, key, info in iter_fields():
        ann = info.annotation
        args = [a for a in get_args(ann) if a is not type(None)] if get_origin(ann) in (Union, UnionType) else [ann]
        if len(args) == 1 and (args[0] in (int, float, bool) or get_origin(args[0]) is Literal):
            out.add(f"{section}.{key}")
    return out


def _words(text: str) -> int:
    return len(text.split())


def _sentences(text: str) -> int:
    body = text.strip()
    return len([s for s in re.split(r"(?<=[.?!])\s+", body) if s.strip()])


def format_problems(root: Path) -> list[str]:
    """Every format problem of a methods folder, each naming the topic or term."""
    out: list[str] = [f"{file}: {problem}" for file, problem in m.problems(root)]   # files left out (D22)
    try:
        ts = m.topics(root)
    except ValueError as e:
        return [*out, str(e)]
    allowed, kinds, page_names = _numeric_choice_flag(), set(KINDS), set(pages())
    for t in ts:
        tid = t["id"]
        meta = m.raw_meta(tid, root)
        if tuple(meta) != m.KEYS:
            out.append(f"{tid}: front matter keys must be exactly {', '.join(m.KEYS)} (found {', '.join(meta)})")
        if tid == m.GLOSSARY:
            out.append(f"{tid}: glossary is reserved")
        ctx = str(meta.get("context", ""))
        if not ctx.endswith(".") or _sentences(ctx) != 1 or _words(ctx) > 40:
            out.append(f"{tid}: context must be one sentence ending in a full stop, at most 40 words")
        for s in t["settings"]:
            if s not in allowed:
                out.append(f"{tid}: setting {s} is not a numeric, choice or flag protocol key")
        for a in t["artifacts"]:
            if a not in kinds:
                out.append(f"{tid}: artifact {a} is not a kind in KINDS")
        if not t["pages"]:
            out.append(f"{tid}: pages must name at least one guide page")
        for p in t["pages"]:
            if p not in page_names:
                out.append(f"{tid}: page {p} has no shipped guide text")
        for layer in m.topic(tid, root)["layers"]:
            if layer["layer"] == "short" and FORMULA & set(layer["text"]):
                out.append(f"{tid}: In short holds a formula character ({''.join(sorted(FORMULA & set(layer['text'])))})")
            if _words(layer["text"]) > LIMITS[layer["layer"]]:
                out.append(f"{tid}: {layer['heading']} has {_words(layer['text'])} words, "
                           f"more than {LIMITS[layer['layer']]}")
    seen: dict[str, str] = {}
    has_line = m.glossary_has_synonym_line(root)
    for e in m.glossary(root):
        if not has_line[e["slug"]]:
            out.append(f"glossary {e['term']}: no synonyms: line")
        if not 1 <= _sentences(e["definition"]) <= 3:
            out.append(f"glossary {e['term']}: the definition must have one to three sentences")
        if _words(e["definition"]) > GLOSSARY_WORDS:
            out.append(f"glossary {e['term']}: the definition has more than {GLOSSARY_WORDS} words")
        if FORMULA & set(e["definition"]):
            out.append(f"glossary {e['term']}: the definition holds a formula character")
        for name in [e["term"], *e["synonyms"]]:
            k = name.lower()
            if k in seen and seen[k] != e["slug"]:
                out.append(f"glossary: {name!r} appears in {seen[k]} and {e['slug']}")
            seen.setdefault(k, e["slug"])
    slugs = [e["slug"] for e in m.glossary(root)]
    for s in {s for s in slugs if slugs.count(s) > 1}:
        out.append(f"glossary: the slug {s} is used twice")
    return out


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "methods"
    shutil.copytree(FIXTURE, root)
    return root


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


# ------------------------------------------------------------------------------------------ loader (task 1.1)
def test_sections_three_per_topic():
    secs = m.sections(FIXTURE)
    per = {t: [s.layer for s in secs if s.topic == t] for t in ("alpha", "beta")}
    assert per == {"alpha": ["short", "why", "auditors"], "beta": ["short", "why", "auditors"]}
    assert len([s for s in secs if s.kind == "glossary"]) == len(m.glossary(FIXTURE)) == 3
    assert len({s.id for s in secs}) == len(secs)


def test_section_ids_and_anchors():
    by_id = {s.id: s for s in m.sections(FIXTURE)}
    s = by_id["methods/alpha/auditors"]
    assert (s.kind, s.topic, s.layer, s.anchor) == ("methods", "alpha", "auditors", "#methods/alpha/auditors")
    assert s.title == "Alpha topic" and s.context.startswith("A synthetic topic")
    assert s.settings == ("recall.ci_level", "identification.validation_after_freeze") and s.pages == ("sample", "recall")
    g = by_id["glossary/recall"]
    assert (g.kind, g.topic, g.layer, g.title, g.anchor) == ("glossary", None, None, "recall", "#methods/glossary/recall")
    assert g.text.startswith("Also called: sensitivity; share of relevant papers found. The share")


def test_defaults_filled_from_protocol():
    p = ReviewProtocol()
    t = m.topic("alpha", FIXTURE)
    short, why = t["layers"][0]["text"], t["layers"][1]["text"]
    assert f"The confidence level is {p.recall.ci_level}." in short
    assert "a flag that is on" in why and "a target not set" in why   # True -> on, None -> not set
    assert not any("{default:" in s.text or "{default:" in s.context for s in m.sections(FIXTURE))


def test_unknown_default_names_topic(tmp_path):
    root = _copy(tmp_path)
    _edit(root / "beta.md", "Plain words.", "Plain {default:recall.no_such_key} words.")
    assert [s.topic for s in m.sections(root) if s.kind == "methods"] == ["alpha"] * 3   # beta is left out (D22)
    [(file, problem)] = m.problems(root)
    assert file == "beta.md" and re.search(r"beta.*recall\.no_such_key", problem)


def test_glossary_section_has_synonyms():
    g = {e["slug"]: e for e in m.glossary(FIXTURE)}
    assert g["band"]["term"] == "band" and g["band"]["synonyms"] == ["stratum", "score band"]
    assert g["local-and-cloud-backend"]["synonyms"] == []
    by_id = {s.id: s for s in m.sections(FIXTURE)}
    assert by_id["glossary/local-and-cloud-backend"].text == "Where the model runs."   # no "Also called" without synonyms
    assert by_id["glossary/band"].text.startswith("Also called: stratum; score band.")


def test_glossary_slugs_unique(tmp_path):
    assert [e["slug"] for e in m.glossary(FIXTURE)] == ["recall", "band", "local-and-cloud-backend"]
    assert m.slug("Local and cloud backend!") == ("local-and-cloud-backend", "Local and cloud backend!", None)
    root = _copy(tmp_path)
    _edit(root / "glossary.md", "## local and cloud backend", "## band")
    assert any("slug band is used twice" in p for p in format_problems(root))


def test_topics_order_review_and_unknown():
    ts = m.topics(FIXTURE)
    assert [t["id"] for t in ts] == ["alpha", "beta"]
    assert ts[0]["review"] == {"status": "reviewed", "date": "2026-10-30"}
    assert set(ts[0]) == {"id", "title", "context", "settings", "artifacts", "pages", "review"}
    assert [x["heading"] for x in m.topic("beta", FIXTURE)["layers"]] == ["In short", "Why", "For auditors"]
    assert m.glossary_review(FIXTURE) == {"status": "drafted", "date": "2026-10-30"}
    assert m.for_page("sample", FIXTURE) == [{"id": "alpha", "title": "Alpha topic"}, {"id": "beta", "title": "Beta topic"}]
    with pytest.raises(KeyError):
        m.topic("no-such-topic", FIXTURE)


# ------------------------------------------------------------- edits read at once (guide-assistant D22, task 3.6)
def _set_review(root: Path, **entries) -> None:
    import yaml
    data = yaml.safe_load((root / "review.yaml").read_text(encoding="utf-8"))
    for key, entry in entries.items():
        if key == m.GLOSSARY:
            data[m.GLOSSARY] = entry
        else:
            data["topics"][key] = entry
    (root / "review.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def test_loader_sees_edit_without_restart(tmp_path):
    """A sentence replaced in a topic file is in the next call's text and sections; no cache needs clearing."""
    root = _copy(tmp_path)
    assert "Plain words." in m.topic("beta", root)["layers"][0]["text"]
    key = m.corpus_key(root)
    _edit(root / "beta.md", "Plain words.", "Edited words.")
    assert m.corpus_key(root) != key
    assert "Edited words." in m.topic("beta", root)["layers"][0]["text"]
    assert any("Edited words." in s.text for s in m.sections(root) if s.topic == "beta")
    assert "Edited words." in "\n".join(m.methods_lines("beta", root))


def test_edited_reviewed_topic_reads_drafted(tmp_path):
    """A reviewed topic whose file then changes reads drafted with its review date and edited: true; a stamped,
    never-reviewed topic gets no edited key when it is edited."""
    root = _copy(tmp_path)
    _set_review(root, beta={"status": "drafted", "date": "2026-10-30", "text": m.text_hash("beta", root)})
    assert m.topic("alpha", root)["review"] == {"status": "reviewed", "date": "2026-10-30"}
    _edit(root / "alpha.md", "The confidence level", "The stated confidence level")
    _edit(root / "beta.md", "Plain words.", "Edited words.")
    review = m.topic("alpha", root)["review"]
    assert review == {"status": "drafted", "date": "2026-10-30", "edited": True}
    assert m.review_line(review) == "Edited since the author's review on 2026-10-30; not yet reviewed again."
    assert m.topic("beta", root)["review"] == {"status": "drafted", "date": "2026-10-30"}
    assert m.review_line(review) in m.methods_lines("alpha", root)


def test_edited_glossary_reads_drafted(tmp_path):
    """The glossary reviewed, then a synonym added: drafted, the date and edited: true; stamped only: no edited."""
    root = _copy(tmp_path)
    _set_review(root, glossary={"status": "drafted", "date": "2026-10-30", "text": m.text_hash(m.GLOSSARY, root)})
    _edit(root / "glossary.md", "synonyms: score band", "synonyms: score band; layer")
    assert m.glossary_review(root) == {"status": "drafted", "date": "2026-10-30"}
    _set_review(root, glossary={"status": "reviewed", "date": "2026-10-31", "text": m.text_hash(m.GLOSSARY, root)})
    assert m.glossary_review(root) == {"status": "reviewed", "date": "2026-10-31"}
    _edit(root / "glossary.md", "synonyms: score band; layer", "synonyms: score band; layer; tier")
    assert m.glossary_review(root) == {"status": "drafted", "date": "2026-10-31", "edited": True}
    assert "tier" in {e["slug"]: e for e in m.glossary(root)}["band"]["synonyms"]


def test_bad_topic_file_left_out_and_named(tmp_path):
    """A topic saved with its headings out of order is left out, the other topics and the glossary still load, and
    problems() names the file; the format check still fails on it, so the gate catches it."""
    root = _copy(tmp_path)
    _edit(root / "beta.md", "## In short", "## For now")
    assert [t["id"] for t in m.topics(root)] == ["alpha"]
    assert {s.topic for s in m.sections(root) if s.kind == "methods"} == {"alpha"}
    assert [e["slug"] for e in m.glossary(root)] == ["recall", "band", "local-and-cloud-backend"]
    [(file, problem)] = m.problems(root)
    assert file == "beta.md" and "beta" in problem
    line = m.problem_line(file, problem)
    assert line == f"WIDER's documents: beta.md cannot be read ({problem}); it is left out until it is fixed."
    assert m.methods_lines(None, root)[0] == line and m.bad_topic("beta", root) == line
    assert m.bad_topic("alpha", root) is None
    with pytest.raises(KeyError):
        m.topic("beta", root)
    assert any(p.startswith("beta.md: ") for p in format_problems(root))
    _edit(root / "beta.md", "## For now", "## In short")   # fixed: back at once
    assert m.problems(root) == [] and [t["id"] for t in m.topics(root)] == ["alpha", "beta"]
    _edit(root / "glossary.md", "## band (stratum)", "## !!")       # a glossary entry with no term is left out and named
    assert [e["slug"] for e in m.glossary(root)] == ["recall", "local-and-cloud-backend"]
    assert m.problems(root) == [("glossary.md", "the heading '!!' names no term")]


# ------------------------------------------------------------------------------------------ format (task 1.4)
def test_fixture_has_no_format_problem():
    assert format_problems(FIXTURE) == []


@pytest.mark.parametrize(("fname", "old", "new", "needle"), [
    ("beta.md", "pages: [sample]\n", "pages: [sample]\nextra: 1\n", "front matter keys must be exactly"),
    ("beta.md", "linked from the sample page only.", "linked. From the sample page", "context must be one sentence"),
    ("beta.md", "settings: []", "settings: [sampling.bands]", "setting sampling.bands is not a numeric"),
    ("beta.md", "settings: []", "settings: [recall.no_such_key]", "setting recall.no_such_key"),
    ("beta.md", "artifacts: []", "artifacts: [pictures]", "artifact pictures is not a kind"),
    ("beta.md", "pages: [sample]", "pages: []", "pages must name at least one"),
    ("beta.md", "pages: [sample]", "pages: [methods]", "page methods has no shipped guide text"),
    ("beta.md", "Plain words.", "Recall = kept / relevant.", "In short holds a formula character"),
    ("beta.md", "Plain words.", "word " * 121, "In short has 121 words, more than 120"),
    ("beta.md", "More words.", "word " * 251, "Why has 251 words, more than 250"),
    ("beta.md", "The code is", "word " * 200 + "The code is", "For auditors has 204 words, more than 200"),
    ("glossary.md", "synonyms: score band\n", "", "glossary band: no synonyms: line"),
    ("glossary.md", "Where the model runs.", "One. Two. Three. Four.", "one to three sentences"),
    ("glossary.md", "Where the model runs.", "word " * 61, "more than 60 words"),
    ("glossary.md", "synonyms: score band", "synonyms: sensitivity", "'sensitivity' appears in recall and band"),
    ("glossary.md", "Where the model runs.", "It runs where x > 1.", "formula character"),
])
def test_format_failure_paths(tmp_path, fname, old, new, needle):
    root = _copy(tmp_path)
    _edit(root / fname, old, new)
    probs = format_problems(root)
    assert any(needle in p for p in probs), probs


@pytest.mark.parametrize(("old", "new", "needle"), [
    ("## Why\n", "## Reasons\n", "headings must be In short, Why, For auditors"),
    ("## Why\n\nMore words.\n", "", "headings must be In short, Why, For auditors"),
    ("## Why\n\nMore words.\n", "## Why\n\n", "the layer Why has no text"),
    ("id: beta", "id: gamma", "its id must equal the file name"),
    ("---\nid: beta", "id: beta", "no front matter"),
])
def test_structure_errors_name_topic(tmp_path, old, new, needle):
    root = _copy(tmp_path)
    _edit(root / "beta.md", old, new)
    probs = format_problems(root)
    assert any("beta" in p and needle in p for p in probs), probs


def test_topic_named_glossary_is_refused():
    """A topic's id equals its file name, and ``glossary.md`` is the glossary, so no topic can be called glossary."""
    assert (FIXTURE / "glossary.md").exists()
    assert m.GLOSSARY not in [t["id"] for t in m.topics(FIXTURE)]


def test_numeric_choice_flag_keys():
    keys = _numeric_choice_flag()
    assert {"recall.ci_level", "recall.target", "recall.primary_variant", "sampling.plan_seconds",
            "identification.validation_after_freeze"} <= keys
    assert "sampling.bands" not in keys


# --------------------------------------------------------------------------------------- the shipped corpus
@shipped_corpus
def test_shipped_format():
    assert format_problems(m.HERE) == []


@shipped_corpus
def test_shipped_required_topics():
    ids = [t["id"] for t in m.topics()]
    assert m.GLOSSARY not in ids
    assert set(REQUIRED) <= set(ids)
    assert ids[:len(m.TOPIC_ORDER)] == list(m.TOPIC_ORDER)


@shipped_corpus
def test_shipped_sections_and_recall_entry():
    by_id = {s.id: s for s in m.sections()}
    assert by_id["methods/recall-estimate/auditors"].anchor == "#methods/recall-estimate/auditors"
    g = by_id["glossary/recall"]
    assert g.title == "recall" and g.anchor == "#methods/glossary/recall"
    assert g.text.startswith("Also called:") and "sensitivity" in g.text
    recall = next(e for e in m.glossary() if e["slug"] == "recall")
    assert {"sensitivity", "share of relevant papers found"} <= set(recall["synonyms"])
    assert 1 <= _sentences(recall["definition"]) <= 3
    assert len(by_id) == 3 * len(m.topics()) + len(m.glossary())
    assert not any("{default:" in s.text for s in by_id.values())
