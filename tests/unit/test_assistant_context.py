"""guide-assistant group 4: what Ask WIDER sends the model (D10, D12, D22). Synthetic projects only; the retrieval runs
on keywords (``meaning=False``) or is cut to named sections, so no embedding model is loaded."""
import dataclasses
import re
import typing

import pytest

from tests.unit.test_screen_compare import _template, cproject, dev_rows  # noqa: F401 - pytest fixtures
from wider.assistant import context as cx
from wider.assistant import store
from wider.assistant.retrieve import retrieve
from wider.assistant.sections import all_sections, section_hash
from wider.core.models import BenchmarkPaper, ProviderConfig
from wider.core.project import Project


def _proj(tmp_path) -> Project:
    return Project.init(tmp_path / "r")


def _ret(project, question, keep=None):
    """The keyword retrieval of ``question``, cut to its first ``keep`` sections when given."""
    r = retrieve(question, project=project, meaning=False)
    return dataclasses.replace(r, sections=r.sections[:keep]) if keep else r


def _turn(q="Earlier question?", a="Earlier answer.", docs=None):
    return {"question": q, "answer": a, "docs": docs or {}, "page": "dedup"}


def _sent(ctx) -> str:
    return ctx.system() + "\n" + ctx.user()


# ------------------------------------------------------------------------------------------- 4.1 parts and order
def test_context_parts_and_order(tmp_path):
    """D10: rules, question, this page's guide, matching sections, settings, status, earlier turns, highest priority
    first; the best section cannot be dropped; the question comes last in the user text, the rules are the system
    text and name only this page's buttons."""
    from wider.guide import button_registry

    p = _proj(tmp_path)
    for i in range(2):
        store.append(p, "ana", _turn(f"Question {i}?", f"Answer {i}."))
    q = "How does deduplication decide two records are duplicates?"
    r = _ret(p, q)
    ctx = cx.build_context(p, "dedup", q, "ana", retrieval=r)
    names = [x.name for x in ctx.parts]
    order = ["rules", "question", "guide", "section", "settings", "status", "turn"]
    assert [n for i, n in enumerate(names) if i == 0 or names[i - 1] != n] == order
    assert [x.priority for x in ctx.parts] == sorted(x.priority for x in ctx.parts)
    secs = [x for x in ctx.parts if x.name == "section"]
    assert [x.ref for x in secs] == [s.id for s in r.sections] and not secs[0].droppable
    assert all(x.droppable for x in secs[1:])
    assert not any(x.droppable for x in ctx.parts if x.name in ("rules", "question", "guide"))
    assert ctx.user().endswith(f"The researcher's question:\n{q}")
    assert ctx.user().index("This page's guide (Deduplication)") < ctx.user().index("Sections of WIDER's documents:")
    assert ctx.system().startswith("You are Ask WIDER")
    assert ", ".join(button_registry()["dedup"]) in ctx.system()
    assert [x.ref for x in ctx.parts if x.name == "turn"] == ["1", "2"]
    assert ctx.docs() == {s.id: section_hash(s) for s in r.sections}


def test_read_line_names_parts(tmp_path):
    """Scenario "The parts named under the answer": a deduplication question on Deduplication with two earlier turns
    reads "Read: this page's guide, 2 sections, your project's dedup settings, project status, 2 earlier turns"; the
    JSON lists each part with its name and size in characters, without its text."""
    p = _proj(tmp_path)
    for i in range(2):
        store.append(p, "ana", _turn(f"Question {i}?", f"Answer {i}."))
    q = "How does deduplication decide two records are duplicates?"
    r = _ret(p, q)
    by_id = {s.id: s for s in all_sections()}
    keep = [by_id["methods/deduplication/short"], by_id["glossary/deduplication"]]
    ctx = cx.build_context(p, "dedup", q, "ana", retrieval=dataclasses.replace(r, sections=keep))
    assert ctx.read_line() == ("Read: this page's guide, 2 sections, your project's dedup settings, project status, "
                               "2 earlier turns")
    summary = ctx.summary()
    assert [x["name"] for x in summary] == [x.name for x in ctx.parts]
    for row, part in zip(summary, ctx.parts):
        assert row["chars"] == len(part.text) and "text" not in row
    assert all(part.text not in str(summary) for part in ctx.parts if len(part.text) > 20)


# ------------------------------------------------------------------------------------------- 4.2 no record data
def test_context_holds_no_record_data(cproject):  # noqa: F811
    """Scenario "No record data": with an incomplete development sample (labels, notes on labels and records), model
    decisions, a locked validation sample with a corrected label and a recall estimate, the text sent for a question on
    Samples and labelling holds no record title, abstract or DOI, no note, reason or correction reason, no sample
    count and no recall figure, and says "development labels open" without a count."""
    from wider.core.annotations import add_note
    from wider.recall.estimate import estimate
    from wider.sample.labels import label, lock
    from wider.screen.decisions import set_decision

    p, _ = cproject
    dev = dev_rows(p)
    for i, r in enumerate(dev[:-3]):
        label(p, "development", r["record_id"], "alice", "include" if "alpha" in r["title"] else "exclude",
              reason="" if "alpha" in r["title"] else f"zq dev reason {i}", note=f"zq label note {i}")
    add_note(p, dev[0]["record_id"], "alice", "zq record note about this paper")
    val = dev_rows(p, "validation")
    for r in val:
        label(p, "validation", r["record_id"], "alice", "include" if "alpha" in r["title"] else "exclude",
              reason="" if "alpha" in r["title"] else "a")
    for r in [*dev, *val]:
        set_decision(p, "main", r["record_id"], "include" if int(r["record_id"]) % 2 else "exclude")
    lock(p, "validation", by="cy")
    first = val[0]
    label(p, "validation", first["record_id"], "alice", "exclude" if "alpha" in first["title"] else "include",
          reason="a", correction_reason="zq corrected after misreading")
    est = estimate(p, "validation", "main", B=50, seed=1)
    p = Project.load(p.root)
    q = "What should I do next with the labels on this page?"
    text = _sent(cx.build_context(p, "sample", q, "alice", retrieval=_ret(p, q)))
    for rec in p.store.query("SELECT title, abstract, doi FROM records"):
        assert rec["title"] not in text and rec["doi"] not in text
        assert not rec["abstract"] or rec["abstract"][:40] not in text
    assert "zq" not in text   # notes, reasons and the correction reason
    for v in est["variants"].values():
        if v.get("recall") is not None:
            for shown in (f"{v['recall'] * 100:.1f}%", f"{v['recall'] * 100:.0f}%", f"{v['recall']:.3f}"):
                assert shown not in text, shown
    assert "Samples and labelling: needs attention: development labels open" in text
    for n in {len(dev), len(dev) - 3, 3, len(val)}:
        assert not re.search(rf"\b{n} (development|validation|records?|labels?)\b", text), n
    assert "record_id" not in text and "development labels:" not in text


# ------------------------------------------------------------------------------------------- 4.3 validation hidden
def test_context_validation_hidden_until_freeze(tmp_path):
    """Scenario "Validation papers hidden until the freeze": on Search results, before the query is frozen the context
    holds no validation paper's title, DOI or count; after the freeze no title or DOI; never a recall figure or
    "met / not met"."""
    from tests.unit.test_construct import SYN
    from wider.construct import artifacts
    from wider.search.coverage import validation_unlocked

    p = _proj(tmp_path)
    p.write_benchmark([
        BenchmarkPaper(benchmark_id="B1", title="Zqdev seed paper", doi="10.1000/zqdev", set="Development"),
        BenchmarkPaper(benchmark_id="B2", title="Zqval hidden paper one", doi="10.1000/zqval1", set="Validation"),
        BenchmarkPaper(benchmark_id="B3", title="Zqval hidden paper two", doi="10.1000/zqval2", set="Validation"),
    ])
    artifacts.save_new_version(p, "synonyms", SYN, origin="manual")
    q = "How many validation papers did my search find?"

    def sent(project):
        return cx.build_context(project, "results", q, "ana", retrieval=_ret(project, q))

    before = sent(Project.load(p.root))
    assert not validation_unlocked(Project.load(p.root))
    artifacts.set_status(p, "synonyms", 1, "frozen")
    p = Project.load(p.root)
    assert validation_unlocked(p)
    after = sent(p)
    for ctx in (before, after):
        text = _sent(ctx)
        assert "zqval" not in text.lower() and "Zqdev" not in text and "10.1000/zq" not in text
        project_text = "\n".join(x.text for x in ctx.parts if x.name in ("status", "settings", "guide"))
        assert not re.search(r"\d+(\.\d+)?%", project_text)
        assert not re.search(r"\b(not met|is met|recall estimate)\b", project_text, re.I)
    text = _sent(before)
    assert not re.search(r"\b2 (of \d+ )?(validation|seed|papers?)\b", text) and not re.search(r"\b2 of 2\b", text)


# ------------------------------------------------------------------------------------------- 4.4 settings
def test_settings_named_values_only(tmp_path):
    """Scenario "Why this recall target": with the recall target 0.95, "Why is our target 95%?" sends the recall-target
    section and `recall.target = 0.95` with its shipped description, and no protocol value the sent sections do not
    name. Every protocol key a shipped section names is a number, a fixed choice or a yes/no, never free text."""
    from wider.core.protocol import iter_fields

    p = _proj(tmp_path)
    p.config.protocol = p.config.protocol.set("recall.target", 0.95)
    p.save()
    p = Project.load(p.root)
    q = "Why is our target 95%?"
    ctx = cx.build_context(p, "recall", q, "ana", retrieval=_ret(p, q))
    assert any(s.id.startswith("methods/recall-target/") or s.id == "glossary/recall-target" for s in ctx.sections)
    part = next(x.text for x in ctx.parts if x.name == "settings")
    info = {f"{s}.{k}": f for s, k, f in iter_fields()}
    desc = " ".join(info["recall.target"].description.split())
    assert f"- recall.target = 0.95 ({desc})" in part
    named = {k for s in ctx.sections for k in s.settings}
    keys = re.findall(r"^- ([a-z_]+\.[a-z_]+) = ", part, re.M)
    assert keys and set(keys) <= named
    for key in info:
        if key not in named:
            assert f"{key} =" not in _sent(ctx), key

    def plain(ann) -> bool:
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        if typing.get_origin(ann) is typing.Literal:
            return True
        if args:
            return all(plain(a) for a in args)
        return ann in (int, float, bool) or getattr(ann, "__mro__", [None])[0].__class__.__name__ == "EnumType"

    for key in {k for s in all_sections() for k in s.settings}:
        assert key in info, key
        assert plain(info[key].annotation), (key, info[key].annotation)


def test_artifact_note_not_sent(tmp_path):
    """Scenario "Artifact versions without their notes": a search-query question matching a section that names the
    synonyms artifact says "synonyms version 3, frozen, written by hand" and holds no text of the note."""
    from tests.unit.test_construct import SYN
    from wider.construct import artifacts

    p = _proj(tmp_path)
    for i in range(3):
        artifacts.save_new_version(p, "synonyms", SYN, origin="manual", note=f"zqnote secret version {i + 1}")
    artifacts.set_status(p, "synonyms", 3, "frozen")
    p = Project.load(p.root)
    q = "How is the search query built from the synonyms?"
    ctx = cx.build_context(p, "construct", q, "ana", retrieval=_ret(p, q))
    assert any("synonyms" in s.artifacts for s in ctx.sections)
    text = _sent(ctx)
    assert "synonyms version 3, frozen, written by hand" in text
    assert "zqnote" not in text and "secret version" not in text
    assert "synonyms" in ctx.settings_topics


# ------------------------------------------------------------------------------------------- 4.5 budget
def _small(tmp_path):
    """A project whose context without turns fits 800 tokens: the Notes page and one short glossary section."""
    p = _proj(tmp_path)
    cx.set_budget(p, 800, author="ana")
    q = "What is a seed paper?"
    r = _ret(p, q)
    best = min((s for s in r.sections if s.kind == "glossary"), key=lambda s: len(s.text))
    return p, q, dataclasses.replace(r, sections=[best])


def test_budget_drops_oldest_turns_first(tmp_path):
    """Scenario "A small budget drops the oldest turns first": with the budget at 800 and six earlier turns, the
    context keeps rules, question, page guide and the best section and the newest turns that fit; the "Read:" line says
    "4 earlier turns left out to fit the model's budget"."""
    p, q, r = _small(tmp_path)
    base = cx.build_context(p, "notes", q, "ana", retrieval=r)
    room = (800 - base.tokens()) * 4
    assert room > 400, base.tokens()
    size = int(room / 2.5)   # two turns fit, three do not
    for i in range(6):
        store.append(p, "ana", _turn(f"Question {i + 1}?", f"Answer {i + 1} " + "x" * (size - 40)))
    ctx = cx.build_context(p, "notes", q, "ana", retrieval=r)
    assert ctx.budget == 800 and ctx.tokens() <= 800
    assert [x.ref for x in ctx.parts if x.name == "turn"] == ["5", "6"]
    assert {"rules", "question", "guide", "section", "status"} <= {x.name for x in ctx.parts}
    assert ctx.read_line().endswith("; 4 earlier turns left out to fit the model's budget")
    assert {"name": "turn", "dropped": 4} in ctx.summary()


def test_budget_too_long_refused(tmp_path):
    """Scenario "A question too long for the budget": when rules, question, page guide and the best section alone
    exceed the budget, nothing is built and the message names the budget."""
    p, _, r = _small(tmp_path)
    with pytest.raises(cx.TooLong) as e:
        cx.build_context(p, "notes", "Why? " * 800, "ana", retrieval=r)
    assert str(e.value) == ("This question is too long for the model's budget (800 tokens). Shorten it, or raise the "
                            "budget in Settings.")
    assert e.value.budget == 800


def test_budget_default_by_place(tmp_path, monkeypatch):
    """D10: 3,000 on this computer, for a test backend or an unknown place; 12,000 for the local network or a remote
    service; `assistant.context_tokens` wins over both."""
    p = _proj(tmp_path)
    assert cx.budget(p) == cx.default_budget(p) == 3000
    cases = [(ProviderConfig(name="fk", kind="fake", model="fake"), 3000),
             (ProviderConfig(name="olla", kind="ollama", model="m", base_url="http://127.0.0.1:11434"), 3000),
             (ProviderConfig(name="lan", kind="openai_compat", model="m", base_url="http://192.168.1.20:8000/v1",
                             api_key_env="LAN_KEY"), 12000),
             (ProviderConfig(name="remote", kind="openai_compat", model="m", base_url="https://api.example.com/v1",
                             api_key_env="REMOTE_KEY"), 12000)]
    for cfg, want in cases:
        assert cx.default_budget(p, cfg) == want, cfg.name
    cx.set_budget(p, 5000)
    assert cx.budget(p, cases[3][0]) == 5000 and cx.budget(p) == 5000


def test_set_budget_journalled(tmp_path):
    """Setting the budget saves the config through `project.save()`: History records the change with its author;
    None returns to the default by place; a value outside 500-200,000 (or not a whole number) is refused with the
    range and changes nothing."""
    from wider.core import journal

    p = _proj(tmp_path)
    assert cx.set_budget(p, 800, author="ana") == 800
    q = Project.load(p.root)
    assert q.config.assistant.context_tokens == 800 and cx.budget(q) == 800
    row = journal.changes(q, author="ana")[0]
    assert row["kind"] == "config" and "800" in row["summary"]
    for bad in (499, 200_001, True, 800.5, "800"):
        with pytest.raises(ValueError, match="from 500 to 200,000 tokens"):
            cx.set_budget(q, bad, author="ana")
    assert Project.load(p.root).config.assistant.context_tokens == 800
    cx.set_budget(q, None, author="ben")
    q = Project.load(p.root)
    assert q.config.assistant.context_tokens is None and cx.budget(q) == 3000
    assert journal.changes(q, author="ben")[0]["summary"] == "Ask WIDER budget back to the default"


# ------------------------------------------------------------------------------------------- 4.7 turns after an edit
def test_turns_before_edit_left_out(tmp_path):
    """D22: a turn that was sent a section whose text has changed since (or which is gone) is left out of the context;
    a turn whose sections are unchanged is sent."""
    p = _proj(tmp_path)
    sections = all_sections()
    q = "How does deduplication decide two records are duplicates?"
    a, b, c = sections[100], sections[101], sections[102]
    store.append(p, "ana", _turn("Old question about A?", "Old answer A.", {a.id: section_hash(a)}))
    store.append(p, "ana", _turn("Question about B?", "Answer B.", {b.id: section_hash(b)}))
    store.append(p, "ana", _turn("Question about C?", "Answer C.", {c.id: section_hash(c)}))
    edited = [dataclasses.replace(s, text="A new first sentence. " + s.text) if s.id == a.id else s
              for s in sections if s.id != c.id]
    r = retrieve(q, project=p, sections=edited, meaning=False)
    ctx = cx.build_context(p, "dedup", q, "ana", retrieval=r, sections=edited)
    assert [x.ref for x in ctx.parts if x.name == "turn"] == ["2"]
    text = ctx.user()
    assert "Answer B." in text and "Old answer A." not in text and "Answer C." not in text
    # the same turns with the documents as they were: all three are sent
    ctx = cx.build_context(p, "dedup", q, "ana", retrieval=retrieve(q, project=p, sections=sections, meaning=False),
                           sections=sections)
    assert [x.ref for x in ctx.parts if x.name == "turn"] == ["1", "2", "3"]


def test_general_context_no_page_guide(tmp_path):
    """D23: a question about WIDER in general sends no page's guide, only the one line that it is about WIDER in
    general, and rules that name no button."""
    from wider.guide import button_registry

    p = _proj(tmp_path)
    q = "How does deduplication decide two records are duplicates?"
    ctx = cx.build_context(p, cx.GENERAL, q, "ana", retrieval=_ret(p, q))
    names = [x.name for x in ctx.parts]
    assert "guide" not in names and names.count("general") == 1
    assert cx.GENERAL_GUIDE in ctx.user() and "This page's guide" not in ctx.user()
    assert cx.GENERAL_LINE in ctx.system()
    assert ", ".join(button_registry()["dedup"]) not in ctx.system()
