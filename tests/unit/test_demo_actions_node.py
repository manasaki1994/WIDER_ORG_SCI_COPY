"""The demo's simulated actions (showcase/demo/actions.js; openspec change interactive-demo, design D4, D5), run under
Node on the recorded snapshot: labels and their counters, Undo and Redo, duplicate pairs and the PRISMA count, Rank and
Draw samples moving the demo on, and the model card saying its answers are replayed."""
import importlib.util
import json

import pytest

from tests.unit.demo_node import DEMO_DIR, run_demo


@pytest.fixture(scope="module")
def snap():
    spec = importlib.util.spec_from_file_location("build_demo", DEMO_DIR / "build_demo.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    coverage = json.loads(build.COVERAGE.read_text(encoding="utf-8"))
    return build.snapshot_js(build.SNAPSHOT, coverage, "nobody-in-particular")


def run(body, snap):
    return run_demo("boot();\n" + body, snapshot_js=snap, actions=True)


# the Samples page's sentence parts the counters come from
PROGRESS = """
  const progress = async () => {
    const s = (await getJson("/api/samples")).json.samples, o = {};
    for (const n of Object.keys(s)) o[n] = s[n].progress.reviewers["Reviewer A"];
    return o;
  };
  const queue = async () => (await getJson("/api/labels/queue?reviewer=Reviewer%20A&blind=false")).json;
  const label = (sid, rid, decision) => getJson("/api/labels", { method: "POST",
    body: JSON.stringify({ sample_id: sid, record_id: rid, reviewer: "Reviewer A", decision, reason: "", labels: {}, note: "" }) });
  const open = async () => (await queue()).find((r) => !r.decision);
"""


def test_label_updates_counters(snap):
    out = run(PROGRESS + """
      Demo.setCheckpoint("sampled");
      const row = await open();
      out.before = (await progress()).development;
      out.post = await label(row.sample_id, row.record_id, "include");
      out.after = (await progress()).development;
      out.row = (await queue()).find((r) => r.record_id === row.record_id && r.sample_id === row.sample_id).decision;
      out.other = (await progress()).validation;
    """, snap)
    assert out["before"] == {"done": 10, "exclude": 6, "include": 4}
    assert out["post"]["status"] == 200 and out["post"]["json"]["ok"] is True
    assert out["post"]["json"]["change_id"].startswith("demo-")
    assert out["after"] == {"done": 11, "exclude": 6, "include": 5}
    assert out["row"] == "include"
    assert out["other"] == {"done": 16, "exclude": 9, "include": 7}


def test_relabel_moves_count(snap):
    out = run(PROGRESS + """
      Demo.setCheckpoint("sampled");
      const row = (await queue()).find((r) => r.sample_id === 1 && r.decision === "include");
      await label(row.sample_id, row.record_id, "exclude");
      out.once = (await progress()).validation;
      await label(row.sample_id, row.record_id, "maybe");
      out.twice = (await progress()).validation;
      out.bad = await label(row.sample_id, row.record_id, "perhaps");
      out.unknown = (await label(99, 1, "include")).status;
    """, snap)
    assert out["once"] == {"done": 16, "exclude": 10, "include": 6}
    assert out["twice"] == {"done": 16, "exclude": 9, "include": 6, "maybe": 1}
    assert out["bad"] == {"status": 400, "json": {"detail": "decision must be one of ('include', 'exclude', 'maybe')"}}
    assert out["unknown"] == 404


def test_undo_removes_overlay_change(snap):
    out = run(PROGRESS + """
      Demo.setCheckpoint("sampled");
      const row = await open();
      const id = (await label(row.sample_id, row.record_id, "exclude")).json.change_id;
      const u = await getJson("/api/history/" + id + "/undo", { method: "POST" });
      out.undo = [u.status, u.json.summary];
      out.after = (await progress()).development;
      out.row = (await queue()).find((r) => r.record_id === row.record_id && r.sample_id === row.sample_id).decision;
      out.again = (await getJson("/api/history/" + id + "/undo", { method: "POST" })).status;
      out.real = await getJson("/api/history/41/undo", { method: "POST" });
      out.nameless = (await fetch("/api/history/undo-last", { method: "POST" })).status;
    """, snap)
    assert out["undo"][0] == 200 and out["undo"][1].startswith("undo: Reviewer A labelled record ")
    assert out["after"] == {"done": 10, "exclude": 6, "include": 4}
    assert out["row"] is None
    assert out["again"] == 409
    assert out["real"] == {"status": 409, "json": {"detail": "Only changes made in the demo can be undone here."}}
    assert out["nameless"] == 400


def test_redo_restores_change(snap):
    out = run(PROGRESS + """
      Demo.setCheckpoint("sampled");
      const row = await open();
      await label(row.sample_id, row.record_id, "include");
      const u = (await getJson("/api/history/undo-last?author=Reviewer%20A", { method: "POST" })).json;
      out.undone = (await progress()).development;
      const r = (await getJson("/api/history/" + u.change_id + "/undo", { method: "POST" })).json;
      out.redo = r.summary.split(":")[0];
      out.redone = (await progress()).development;
      out.last = (await getJson("/api/history/undo-last?author=Reviewer%20A", { method: "POST" })).json.summary.split(":")[0];
      out.end = (await progress()).development;
    """, snap)
    assert out["undone"] == {"done": 10, "exclude": 6, "include": 4}
    assert out["redo"] == "redo"
    assert out["redone"] == {"done": 11, "exclude": 6, "include": 5}
    assert out["last"] == "undo"
    assert out["end"] == {"done": 10, "exclude": 6, "include": 4}


DEDUP = """
  const dedup = async () => (await getJson("/api/dedup")).json;
  const decide = (row, decision) => getJson("/api/dedup/decide", { method: "POST",
    body: JSON.stringify({ raw_a: row.raw_a, raw_b: row.raw_b, decision, by: "Reviewer A" }) });
  const apply = async () => (await getJson("/api/dedup/run", { method: "POST" })).json;
"""


def test_dedup_merge_updates_count(snap):
    out = run(DEDUP + """
      const d = await dedup();
      const r = await decide(d.queue[0], "duplicate");
      out.decide = [r.status, r.json.change_id.startsWith("demo-")];
      const mid = await dedup();
      out.mid = [mid.flow.left, mid.flow.to_decide, mid.queue.length, mid.queue[0].status_words, mid.prisma.at(-1)];
      const res = await apply();
      const after = await dedup();
      out.after = [after.flow.left, after.flow.removed_line, after.prisma.at(-2), after.prisma.at(-1), after.flow.message];
      out.result = [res.stage_result.headline, res.stage_result.metrics, res.stage_result.tone];
      out.stages = (await getJson("/api/stages")).json.last_results.dedup.headline;
      await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" });
      out.ranked = (await getJson("/api/stages")).json.last_results.dedup.headline;
    """, snap)
    assert out["decide"] == [200, True]
    # deciding keeps the pair listed and the count as it was, as the app does, until Apply
    assert out["mid"] == [50, 10, 11, "Duplicate", ["Provisional records remaining after automated deduplication", 50]]
    assert out["after"] == [49, "25 duplicates removed (24 by WIDER, 1 by your decisions)",
                            ["Duplicate records removed before screening", 25],
                            ["Provisional records remaining after automated deduplication", 49],
                            "10 pairs need your decision. The record count stays provisional until every pair is decided."]
    assert out["result"][0] == "49 unique records from 74 imported; 25 duplicates merged. (simulated)"
    assert out["result"][1] == {"unique records": 49, "duplicates merged": 25, "pending pairs": 10,
                                "manual decisions applied": 1}
    assert out["result"][2] == "warning"
    assert out["stages"] == out["result"][0]
    assert out["ranked"] == "39 unique records from 74 imported; 35 duplicates merged."


def test_last_pair_makes_prisma_final(snap):
    out = run(DEDUP + """
      const d = await dedup();
      for (const row of d.queue.slice(0, -1)) await decide(row, "duplicate");
      out.before = (await dedup()).flow.message;
      await decide(d.queue.at(-1), "duplicate");
      out.decided = (await dedup()).flow.message;
      const res = await apply();
      const after = await dedup();
      out.after = [after.flow.left, after.prisma.at(-1), after.flow.message, after.pending, res.stage_result.tone];
    """, snap)
    assert out["before"].startswith("1 pair needs your decision.")
    assert out["decided"] == "Every pair is decided. Press Apply decisions and check again to update the counts."
    assert out["after"] == [39, ["Records remaining for title and abstract screening", 39],
                            "Deduplication is done: 39 records left.", 0, "success"]


def test_not_same_paper_changes_no_count(snap):
    out = run(DEDUP + """
      const d = await dedup();
      for (const row of d.queue) await decide(row, "not_duplicate");
      await apply();
      const after = await dedup();
      out.after = [after.flow.left, after.flow.removed_line, after.prisma.at(-1), after.queue[0].status_words];
      out.summary = after.summary.confirmed_not_duplicate_pairs;
    """, snap)
    assert out["after"] == [50, "24 duplicates removed (24 by WIDER, 0 by your decisions)",
                            ["Records remaining for title and abstract screening", 50], "Not duplicate"]
    assert out["summary"] == 11


def test_rank_run_moves_to_ranked(snap):
    out = run("""
      out.start = Demo.state.checkpoint;
      const r = await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" });
      out.status = r.status;
      // no reload: the app shows the run's result and fetches the pages again itself
      out.now = [Demo.state.checkpoint, location.replaced, location.reloaded];
      out.left = (await getJson("/api/dedup")).json.flow.left;
    """, snap)
    assert out["start"] == "imported"
    assert out["status"] == 200
    assert out["now"] == ["ranked", None, None]
    assert out["left"] == 39


def test_rank_run_result_is_marked_simulated(snap):
    out = run("""
      const r = (await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" })).json.stage_result;
      out.headline = r.headline;
      out.usage = r.usage;
      const again = (await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" })).json.stage_result;
      out.again = [again.headline, Demo.state.checkpoint];
      // the app reads the stages again after a run: the page keeps showing the simulated result, also after Draw
      out.page = (await getJson("/api/stages")).json.last_results.rank.headline;
      await getJson("/api/samples/draw", { method: "POST", body: "{}" });
      out.later = [Demo.state.checkpoint, (await getJson("/api/stages")).json.last_results.rank.headline];
    """, snap)
    assert out["headline"].startswith("Ranked 39 records") and out["headline"].endswith(" (simulated)")
    assert out["usage"] is None
    assert out["again"] == [out["headline"], "ranked"]
    assert out["page"] == out["headline"]
    assert out["later"] == ["sampled", out["headline"]]


def test_draw_samples_moves_to_sampled(snap):
    out = run("""
      Demo.setCheckpoint("ranked");
      const r = await getJson("/api/samples/draw", { method: "POST", body: "{}" });
      out.draw = [r.status, r.json.stage_result.headline.endsWith(" (simulated)"), Demo.state.checkpoint];
      out.n = Object.keys((await getJson("/api/samples")).json.samples).sort();
      out.again = await getJson("/api/samples/draw", { method: "POST", body: "{}" });
    """, snap)
    assert out["draw"] == [200, True, "sampled"]
    assert out["n"] == ["development", "validation"]
    assert out["again"]["status"] == 409 and "already drawn" in out["again"]["json"]["detail"]


def test_run_out_of_order_names_earlier_stage(snap):
    out = run("""
      out.draw = await getJson("/api/samples/draw", { method: "POST", body: "{}" });
      out.at = Demo.state.checkpoint;
    """, snap)
    assert out["draw"] == {"status": 409, "json": {"detail": "Rank the records first (Ranking page)."}}
    assert out["at"] == "imported"


def test_differing_choices_are_named(snap):
    out = run(DEDUP + """
      const d = await dedup();
      await decide(d.queue[0], "not_duplicate");
      await decide(d.queue[1], "duplicate");
      const r = (await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" })).json.stage_result;
      out.warnings = r.warnings;
      out.overlay = Demo.state.overlay.pairs;
      out.undo = (await getJson("/api/history/demo-1/undo", { method: "POST" })).status;
      out.pairs = (await getJson("/api/dedup/decide", { method: "POST",
        body: JSON.stringify({ raw_a: d.queue[2].raw_a, raw_b: d.queue[2].raw_b, decision: "duplicate", by: "x" }) })).status;
    """, snap)
    assert out["warnings"] == [
        "You decided 1 pair differently from Reviewer A.",
        "The 9 pairs you left open were decided as Reviewer A decided them.",
        "The demo continues with Reviewer A's decisions so the numbers on every page agree; in WIDER your own "
        "decisions would be used."]
    assert out["overlay"] == {}
    assert out["undo"] == 409 and out["pairs"] == 409


def test_differing_choices_plural_and_none_left_open(snap):
    out = run(DEDUP + """
      const d = await dedup();
      for (const r of d.queue) await decide(r, "not_duplicate");
      const r = (await getJson("/api/rank/run?embedding=bge-m3", { method: "POST" })).json.stage_result;
      out.warnings = r.warnings;
    """, snap)
    # Reviewer A merged all 11 pairs; nothing is left open, so there is no "left open" line
    assert out["warnings"] == [
        "You decided 11 pairs differently from Reviewer A.",
        "The demo continues with Reviewer A's decisions so the numbers on every page agree; in WIDER your own "
        "decisions would be used."]


def test_model_card_says_replayed(snap):
    out = run("""
      const p = (await getJson("/api/providers")).json.providers;
      out.rows = p.map((r) => [r.api_key_env, r.destination]);
      out.home = (await getJson("/api/home")).json.projects[0].backends.llm.destination;
      out.summary = (await getJson("/api/project/summary")).json.backends.llm.destination;
      await getJson("/api/stages");
      out.page = localStorage.getItem("wider.lastPage.demo0employee0voice");
    """, snap)
    assert out["rows"] and all(env is None for env, _ in out["rows"])
    assert any("recorded answers are replayed, nothing is sent" in dest for _, dest in out["rows"])
    assert out["page"] == "home"
    assert out["home"] == out["summary"] == "api.openai.com (in the demo: recorded answers are replayed, nothing is sent)"


# the terms panel on the Search strings page (design D6): a page with the app's q-strings element, the panel's state
TERMS = """
  const page = () => {
    const card = document.body.appendChild(document.createElement("section"));
    card.setAttribute("id", "page-query");
    card.appendChild(document.createElement("div")).setAttribute("id", "q-strings");
  };
  const shown = () => ({ count: $("demo-terms-count").textContent, empty: !!$("demo-terms-empty"),
    text: $("demo-terms-result").textContent, sent: spy.fetch });
"""


def _terms(body, snap):
    return run_demo("globalThis.beforeBoot = () => page();\n" + TERMS.replace(
        "const page", "globalThis.page") + "boot();\n" + body, snapshot_js=snap, actions=True)


def test_terms_panel_starts_with_the_first_terms(snap):
    out = _terms("""
      out.start = shown();
      out.before = $("page-query").children.map((c) => c.id);
      out.result = DemoTerms.result();
    """, snap)
    assert out["start"]["count"] == "Found 20 of 39 records, 14 of the 19 include papers."
    assert out["before"] == ["demo-terms", "q-strings"]
    assert out["result"]["missed"] == [] and out["result"]["added"] == []
    assert "The same papers as the first terms." in out["start"]["text"]


def test_quoted_phrase_reads_as_its_words(snap):
    """The demo's query quotes its multi-word terms (audit C3): the panel matches the words inside the quotes."""
    out = _terms("""
      out.first = Demo.state.overlay.terms[0].filter((t) => t.includes(" "));
      out.quoted = DemoTerms.pattern('"speaking up"').test("Nurses speaking   up about safety");
      out.plain = DemoTerms.pattern("speaking up").source === DemoTerms.pattern('"speaking up"').source;
      out.stem = DemoTerms.pattern('"report* problems"').test("reporting problems at work");
    """, snap)
    assert out["first"] and all(t.startswith('"') and t.endswith('"') for t in out["first"])
    assert out["quoted"] is True and out["plain"] is True and out["stem"] is True


def test_removing_a_term_lowers_retrieval(snap):
    out = _terms("""
      const r = DemoTerms.remove(0, "suggestion*");
      out.r = { missed: r.missed, records: r.records, includes: r.includes };
      out.shown = shown();
      out.saved = JSON.parse(session.getItem([...session.m.keys()].find((k) => session.getItem(k).includes("overlay")))).overlay.terms[0];
    """, snap)
    assert out["r"]["records"] == 15 and out["r"]["includes"] == 11
    # include papers first, then the others
    assert out["r"]["missed"] == ["P09", "P16", "P23", "P17", "P18"]
    assert "Include papers the first terms found and these terms miss:" in out["shown"]["text"]
    assert "suggestion*" not in out["saved"]
    assert out["shown"]["sent"] == 0


def test_broad_term_adds_noise(snap):
    out = _terms("""
      const r = DemoTerms.add(0, "team*");
      out.r = { added: r.added, records: r.records, includes: r.includes };
      out.shown = shown();
    """, snap)
    assert out["r"] == {"added": ["P22", "P32"], "records": 22, "includes": 14}
    assert "Other papers now found (more to screen, none of them included):" in out["shown"]["text"]


def test_term_finds_missed_includes(snap):
    out = _terms("""
      const r = DemoTerms.add(0, "  Employees ");
      out.r = { added: r.added, records: r.records, includes: r.includes };
      out.again = DemoTerms.add(0, "employees");
      out.blank = DemoTerms.add(0, "   ");
      out.msg = $("demo-terms-msg-0").textContent;
    """, snap)
    assert out["r"] == {"added": ["P03", "P05", "P27"], "records": 23, "includes": 17}
    assert out["again"] is None and out["blank"] is None
    assert out["msg"] == "Type a word first."


def test_empty_block_finds_nothing(snap):
    out = _terms("""
      for (const t of Demo.state.overlay.terms[1].slice()) DemoTerms.remove(1, t);
      out.shown = shown();
      DemoTerms.restore();
      out.back = shown();
    """, snap)
    assert out["shown"]["count"] == "Found 0 of 39 records, 0 of the 19 include papers."
    assert out["shown"]["empty"] is True
    assert "every block needs at least one term" in out["shown"]["text"]
    assert out["back"]["count"] == "Found 20 of 39 records, 14 of the 19 include papers."


def test_terms_survive_a_reload_and_a_typed_term_submits(snap):
    out = _terms("""
      DemoTerms.remove(0, "suggestion*");
      boot();
      out.after = shown().count;
      const input = $("demo-terms-add-0");
      input.value = "suggestion*";
      const form = $("demo-terms-chips-0").parent.children.find((c) => c.tagName === "FORM");
      form.fire("submit");
      out.typed = shown().count;
      out.cleared = input.value;
    """, snap)
    assert out["after"] == "Found 15 of 39 records, 11 of the 19 include papers."
    assert out["typed"] == "Found 20 of 39 records, 14 of the 19 include papers."
    assert out["cleared"] == ""
