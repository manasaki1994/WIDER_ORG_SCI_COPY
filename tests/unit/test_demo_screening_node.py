"""The demo's screening runs, replayed (showcase/demo/screening.js; openspec change interactive-demo, design D5, task
2.4, 2.5), run under Node on the recorded snapshot: a run that fills in record by record and ends on the recorded
result, Cancel and reload keeping the progress, the second setting asking for a new result set, the recall estimate
with what a one-point interval means, and the Outputs page's two writes."""
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


SETUP = """
  globalThis.beforeBoot = (d) => { d.body.appendChild(d.createElement("div")).setAttribute("id", "job-bar"); };
  const form = (extra) => Object.assign({ scope: "corpus", pass_name: "A", provider: "openai", prompt_version: 1, limit: null,
    mode: "protocol", protocol: "main" }, extra || {});
  const run = (extra) => getJson("/api/jobs/screen", { method: "POST", body: JSON.stringify(form(extra)) });
  const jobs = async () => (await getJson("/api/jobs?active=true")).json;
  const job = async (id) => (await getJson(`/api/jobs/${id}`)).json;
  const ticker = () => { const t = $("demo-ticker"); return t ? { hidden: t.hidden, text: t.textContent,
    items: t.children[2].children.length } : null; };
  // the open development records (ui-redesign D4), labelled as Reviewer A labelled them at the next checkpoint
  const QUEUE = "/api/labels/queue?reviewer=Reviewer%20A&blind=false";
  const openDev = async () => (await getJson(QUEUE)).json.filter((r) => !r.decision &&
    DemoActions.sampleNames()[r.sample_id] === "development");
  const labelDev = async () => {
    const later = Demo.recorded(QUEUE, "screened") || [];
    for (const r of await openDev()) {
      const t = later.find((x) => x.sample_id === r.sample_id && x.record_id === r.record_id);
      await getJson("/api/labels", { method: "POST", body: JSON.stringify({ sample_id: r.sample_id,
        record_id: r.record_id, reviewer: "Reviewer A", decision: (t && t.decision) || "exclude" }) });
    }
  };
  const sampled = async () => { Demo.setCheckpoint("sampled"); await labelDev(); };
"""


def go(body, snap, ms=0):
    return run_demo(SETUP + (f"globalThis.SCREEN_MS = {ms};\n" if ms else "") + "boot();\n" + body, snapshot_js=snap,
                    actions=True)


def test_screen_run_is_progressive_and_ends_at_set1(snap):
    out = go("""
      Demo.setCheckpoint("sampled");
      out.open = (await openDev()).length;
      out.refused = await run();
      out.no_job = await jobs();
      out.cp_refused = Demo.state.checkpoint;
      await labelDev();
      out.open_after = (await openDev()).length;
      const r = await run();
      out.id = r.json.job_id;
      out.start = (await jobs())[0];
      clockNow += 10000;
      out.mid = (await jobs())[0];
      DemoJobs.settle();
      out.cp_mid = Demo.state.checkpoint;
      out.ticker_mid = (() => { const t = $("demo-ticker"); return t; })() && ticker();
      clockNow += 40000;
      out.after = await jobs();
      out.cp = Demo.state.checkpoint;
      out.job = await job(out.id);
      out.ticker = ticker();
      out.stages = (await getJson("/api/stages")).json.last_results.screen.headline;
    """, snap, ms=39000)
    # D4: the run waits until every development record has a final label, with the app's sentence and counts
    n = out["open"]
    assert n > 0 and out["open_after"] == 0
    assert out["refused"]["status"] == 409 and out["no_job"] == [] and out["cp_refused"] == "sampled"
    assert out["refused"]["json"]["detail"] == (f"the development sample has {n} record{'' if n == 1 else 's'} "
                                                "without a final label; label them all first")
    assert out["refused"]["json"]["dev_incomplete"]["missing"] == n
    assert out["start"]["n_done"] == 0 and out["start"]["n_total"] == 39
    assert out["start"]["label"] == "Screening: protocol main on corpus"
    assert "no model is called" in out["start"]["message"]
    assert out["mid"]["n_done"] == 10 and out["mid"]["eta_s"] == 29
    assert out["cp_mid"] == "sampled"
    assert out["after"] == [] and out["cp"] == "screened"
    assert out["job"]["status"] == "done" and out["job"]["stage"] == "screen"
    head = out["job"]["result"]["stage_result"]["headline"]
    assert "18 include, 4 maybe, 17 exclude" in head and head.endswith("(simulated)")
    assert out["stages"] == head
    assert out["ticker"]["hidden"] is False
    assert "18 Include, 4 Maybe, 17 Exclude" in out["ticker"]["text"]
    assert "#22" in out["ticker"]["text"] and out["ticker"]["items"] == 1


def test_ticker_shows_the_newest_decisions_while_running(snap):
    out = go("""
      await sampled();
      await run();
      clockNow += 10000;
      await jobs();
      DemoJobs.settle();
      // the page's 500 ms redraw
      await new Promise((r) => setTimeout(r, 700));
      const t = $("demo-ticker");
      out.text = t && t.textContent;
      const ul = t && t.children.find((c) => c.tagName === "UL");
      out.items = ul ? ul.children.map((li) => li.textContent) : [];
      out.rows = DemoJobs.rows("screen").map((r) => r.id);
    """, snap, ms=39000)
    assert len(out["rows"]) == 39
    assert "10 of 39 records decided" in out["text"]
    # the newest decision first, in the recorded order; only records already decided
    assert out["items"] and out["items"][0].startswith(f"#{out['rows'][9]} ")
    assert all(not item.startswith(f"#{rid} ") for item in out["items"] for rid in out["rows"][10:])


def test_ticker_finals_match_the_recorded_counts(snap):
    out = go("""
      const count = (k) => { const c = {}; DemoJobs.rows(k).forEach((r) => { c[r.final] = (c[r.final] || 0) + 1; }); return c; };
      out.one = count("screen");
      out.two = count("screen2");
      out.split1 = DemoJobs.rows("screen").filter((r) => r.A !== r.B).map((r) => [r.id, r.C]);
      out.split2 = DemoJobs.rows("screen2").filter((r) => r.A !== r.B).map((r) => r.id);
      out.moved = DemoJobs.rows("screen2").filter((r) => r.before).map((r) => [r.id, r.before, r.final]);
      out.sets = (Demo.recorded("/api/screen/sets?protocol=main", "screened2").sets).map((s) => [s.include, s.maybe, s.exclude]);
    """, snap)
    assert out["one"] == {"include": 18, "maybe": 4, "exclude": 17}
    assert out["two"] == {"include": 18, "maybe": 4, "exclude": 17}
    assert out["sets"] == [[18, 4, 17], [18, 4, 17]]
    assert [s[0] for s in out["split1"]] == [22] and out["split1"][0][1]
    assert out["split2"] == [17, 27]
    assert sorted(m[0] for m in out["moved"]) == [17, 22]


def test_second_set_needs_a_new_set_then_shows_disagreements(snap):
    out = go("""
      Demo.setCheckpoint("screened");
      out.refused = await run({ provider: "openai-t07" });
      const r = await run({ provider: "openai-t07", new_set: true, set_label: "warmer setting", reuse_from: null, no_reuse: true });
      out.cp = Demo.state.checkpoint;
      out.job = await job(r.json.job_id);
      out.ticker = ticker();
      out.label = (await getJson("/api/screen/sets?protocol=main")).json.sets.map((s) => s.label);
      out.again = await run({ provider: "openai-t07", new_set: true });
    """, snap)
    ns = out["refused"]["json"]["new_set_needed"]
    assert out["refused"]["status"] == 409
    assert ns["changed"] == ["seed", "temperature"] and ns["set"] == 2 and ns["estimate"] is None
    assert ns["to_screen"] == 39 and ns["reusable"] is None
    assert "your settings differ in: seed, temperature" in out["refused"]["json"]["detail"]
    assert out["cp"] == "screened2" and out["job"]["status"] == "done"
    assert "In this round, reads A and B differed on 2 records (#17, #27)" in out["ticker"]["text"]
    assert "compared with the first round, the final decision changed for 2 records" in out["ticker"]["text"]
    assert out["label"] == [None, "warmer setting"]
    assert out["again"]["status"] == 200 and out["again"]["json"]["already_done"] is True


def test_cancel_keeps_checkpoint_and_a_restart_resumes(snap):
    out = go("""
      await sampled();
      const id = (await run()).json.job_id;
      clockNow += 13000;
      out.cancel = await getJson(`/api/jobs/${id}/cancel`, { method: "POST" });
      out.list = await jobs();
      out.cp = Demo.state.checkpoint;
      out.job = await job(id);
      out.open = (await openDev()).length;
      out.restart = await run();
      out.resumed = (await jobs())[0].n_done;
    """, snap, ms=39000)
    assert out["cancel"]["status"] == 200
    assert out["list"] == [] and out["cp"] == "sampled"
    assert out["job"]["status"] == "cancelled"
    assert out["open"] == 0 and out["restart"]["status"] == 200   # the labels stay after Cancel (D4)
    assert out["resumed"] == 13


def test_reload_during_job_resumes_it(snap):
    out = go("""
      await sampled();
      await run();
      clockNow += 10000;
      boot();
      out.after_reload = (await jobs())[0].n_done;
      clockNow += 40000;
      out.list = await jobs();
      out.cp = Demo.state.checkpoint;
    """, snap, ms=39000)
    assert out["after_reload"] == 10
    assert out["list"] == [] and out["cp"] == "screened"


def test_pause_stops_the_clock(snap):
    out = go("""
      await sampled();
      await run();
      clockNow += 10000;
      DemoJobs.pause();
      clockNow += 20000;
      out.paused = (await jobs())[0];
      DemoJobs.resume();
      clockNow += 3000;
      out.resumed = (await jobs())[0].n_done;
    """, snap, ms=39000)
    assert out["paused"]["n_done"] == 10 and "Paused" in out["paused"]["message"]
    assert out["resumed"] == 13


def test_refusals_and_already_done(snap):
    out = go("""
      Demo.setCheckpoint("imported");
      out.unranked = await run();
      Demo.setCheckpoint("ranked");
      out.early = await run();
      Demo.setCheckpoint("sampled");
      out.second_open = await run({ provider: "openai-t07" });
      await labelDev();
      out.second_first = await run({ provider: "openai-t07" });
      out.unknown = await run({ provider: "nobody" });
      globalThis.SCREEN_MS = 0;
      Demo.setCheckpoint("screened2");
      out.done1 = await run();
      out.done2 = await run({ provider: "openai-t07", new_set: true });
    """, snap)
    assert out["unranked"]["status"] == 409 and out["unranked"]["json"]["detail"] == "Rank the records first (Ranking page)."
    assert out["early"]["status"] == 409 and "Draw the samples first" in out["early"]["json"]["detail"]
    assert out["second_open"]["status"] == 409 and "dev_incomplete" in out["second_open"]["json"]
    assert out["second_first"]["status"] == 409 and "first setting" in out["second_first"]["json"]["detail"]
    assert out["unknown"]["status"] == 409
    assert out["done1"]["json"]["already_done"] is True
    assert "already done under model gpt-4o-mini" in out["done1"]["json"]["message"]
    assert "main.s2" in out["done2"]["json"]["message"]


def test_a_second_run_while_one_runs_is_refused(snap):
    out = go("""
      await sampled();
      await run();
      out.again = await run();
    """, snap, ms=39000)
    assert out["again"]["status"] == 409 and "already running" in out["again"]["json"]["detail"]


def test_form_choices_the_demo_did_not_use_are_named(snap):
    out = go("""
      await sampled();
      const id = (await run({ mode: "pass", limit: "5" })).json.job_id;
      out.warn = (await job(id)).result.stage_result.warnings;
    """, snap)
    text = " ".join(out["warn"])
    assert "mode “one pass”" in text and "a limit of 5" in text and "was not used" in text


def test_progress_route_counts_the_reached_records(snap):
    out = go("""
      await sampled();
      await run();
      clockNow += 10000;
      out.a = (await getJson("/api/screen/progress/screen__corpus__p1__openai__A__main.s1")).json;
      out.none = (await getJson("/api/screen/progress/nothing")).status;
    """, snap, ms=39000)
    assert out["a"]["n"] == 10 and out["a"]["inc"] + out["a"]["may"] + out["a"]["exc"] == 10
    assert out["none"] == 404


def test_recall_shows_recorded_estimate_and_what_a_point_interval_means(snap):
    # since label-completion-and-sealing the recorded interval is no single point (0.54 to 1.00), so the demo adds no
    # note; the note itself is pinned on a one-point answer below
    out = go("""
      Demo.setCheckpoint("screened");
      out.saves = [];
      Demo.onSave((st) => out.saves.push(!!(st.overlay && st.overlay.results && st.overlay.results.recall)));
      const r = (await getJson("/api/recall?protocol=main&result_set=1&sample=validation")).json;
      out.head = r.stage_result.headline;
      out.warn = r.stage_result.warnings;
      out.kept = (await getJson("/api/stages")).json.last_results.recall;
    """, snap)
    assert "100.0%" in out["head"] and "95% interval 0.54 to 1.00" in out["head"] and out["head"].endswith("(simulated)")
    assert not any("single point" in w for w in out["warn"] or [])
    assert out["kept"]["headline"] == out["head"]
    # saved at once, so a reload keeps it and the tour's waiting step sees it done
    assert True in out["saves"]



def test_a_point_interval_gets_the_demo_note(snap):
    # a recorded one-point interval (every relevant record kept) gets the note on what it does not mean
    out = go("""
      const row = { variant: "lenient", relevant: 7, missed: 0, "bootstrap 95% CI": "1.00–1.00" };
      out.point = DemoJobs.pointNote({ primary_variant: "lenient", stage_result: { rows: [row] } });
      out.range = DemoJobs.pointNote({ primary_variant: "lenient",
        stage_result: { rows: [Object.assign({}, row, { "bootstrap 95% CI": "0.54–1.00" })] } });
    """, snap)
    assert "single point" in out["point"] and "not certainty" in out["point"] and "about 21 times in 100" in out["point"]
    assert out["range"] is None

def test_outputs_prisma_and_methods(snap):
    out = go("""
      Demo.setCheckpoint("screened");
      out.prisma = await getJson("/api/outputs/prisma?protocol=main&result_set=1", { method: "POST" });
      out.save = await getJson("/api/outputs/methods", { method: "POST", body: JSON.stringify({ text: "We screened." }) });
      out.read = (await getJson("/api/outputs/methods")).json;
      out.sent = spy.fetch;
    """, snap)
    assert out["prisma"]["status"] == 200 and out["prisma"]["json"]["identified"] == 74
    assert out["save"]["json"] == {"file": "methods.md"}
    assert out["read"] == {"file": "methods.md", "text": "We screened."}
    assert out["sent"] == 0


def test_label_on_locked_validation_asks_for_a_correction(snap):
    # by screening the validation sample is locked: the app's queue no longer lists it; a changed label sent for one
    # of its records anyway answers like the app (409, correction_needed); the reposted correction gets the demo's
    # sentence and changes nothing (label-completion-and-sealing D9)
    out = go("""
      await sampled();
      const before = (await getJson("/api/labels/queue?reviewer=Reviewer%20A&blind=false")).json;
      Demo.setCheckpoint("screened");
      // the page's blind queue leaves the locked sample out; the one with blind=false marks its rows locked
      out.blind = (await getJson("/api/labels/queue?reviewer=Reviewer%20A")).json.map((r) => r.record_id);
      const after = (await getJson("/api/labels/queue?reviewer=Reviewer%20A&blind=false")).json.filter((r) => !r.locked);
      out.ids = [[...new Set(before.map((r) => r.sample_id))], [...new Set(after.map((r) => r.sample_id))]];
      const gone = before.find((r) => !after.some((a) => a.sample_id === r.sample_id) && r.decision);
      out.goneListed = out.blind.includes(gone.record_id);
      const other = ["include", "exclude", "maybe"].find((d) => d !== gone.decision);
      const post = (extra) => getJson("/api/labels", { method: "POST",
        body: JSON.stringify({ sample_id: gone.sample_id, record_id: gone.record_id, decision: other, ...extra }) });
      out.r = await post({});
      out.blank = await post({ correction_reason: "  " });
      out.fixed = await post({ correction_reason: "a slip" });
      out.same = await getJson("/api/labels", { method: "POST",
        body: JSON.stringify({ sample_id: gone.sample_id, record_id: gone.record_id, decision: gone.decision }) });
      out.locked = (await getJson("/api/samples")).json.samples.validation.locked;
      out.changes = (await getJson("/api/history")).json;
      const dev = after[0];
      out.dev = await getJson("/api/labels", { method: "POST",
        body: JSON.stringify({ sample_id: dev.sample_id, record_id: dev.record_id, decision: "maybe" }) });
    """, snap)
    assert sorted(out["ids"][0]) == [1, 2] and out["ids"][1] == [2]
    assert out["goneListed"] is False and len(out["blind"]) == 16
    assert out["locked"] is True
    assert out["r"]["status"] == 409 and out["r"]["json"] == {
        "detail": "sample 'validation' is locked: a change to a saved label needs a reason for the correction",
        "correction_needed": True}
    assert out["blank"]["status"] == 409
    assert out["fixed"]["status"] == 400 and out["fixed"]["json"]["detail"] == (
        "The demo keeps its recorded validation labels; in WIDER this saves a documented correction with your reason.")
    assert out["same"]["status"] == 200
    assert "labelled record" not in json.dumps(out["changes"])
    # the development sample can still be labelled after screening
    assert out["dev"]["status"] == 200 and out["dev"]["json"]["ok"] is True


def test_ticker_sits_below_the_run_row_on_the_screening_page(snap):
    # the job bar is at the top of the page, out of view of "Run"; the ticker goes where the visitor is looking
    out = go("""
      // the app's page holds sc-msg from the start (index.html), so it is there before the demo boots
      let card = null;
      globalThis.beforeBoot = (d) => {
        d.body.appendChild(d.createElement("div")).setAttribute("id", "job-bar");
        card = d.body.appendChild(d.createElement("div"));
        card.appendChild(d.createElement("p")).setAttribute("id", "sc-msg");
      };
      boot();
      await sampled();
      await run();
      clockNow += 5000;
      DemoJobs.settle();
      await jobs();
      const t = $("demo-ticker");
      out.inCard = !!t && t.parentNode === card;
    """, snap, ms=39000)
    assert out["inCard"]


QUEUE = "/api/labels/queue?reviewer=Reviewer%20A&blind=false"


def test_labels_replaced_by_a_run_are_named(snap):
    # a run moves on with Reviewer A's labels: the visitor's labels on records still open, given the other way from
    # Reviewer A's later label, are named as replaced, and so is a relabel before the second setting
    out = go(f"""
      Demo.setCheckpoint("sampled");
      const later = {{}};
      (Demo.recorded("{QUEUE}", "screened") || []).forEach((r) => {{ later[r.sample_id + ":" + r.record_id] = r.decision; }});
      const open = (await getJson("{QUEUE}")).json.filter((r) => !r.decision);
      out.open = open.length;
      for (const r of open) {{
        const theirs = later[r.sample_id + ":" + r.record_id];
        await getJson("/api/labels", {{ method: "POST", body: JSON.stringify({{ sample_id: r.sample_id, record_id: r.record_id,
          decision: theirs === "include" ? "exclude" : "include" }}) }});
      }}
      await run();
      await jobs();
      out.one = (await getJson("/api/stages")).json.last_results.screen.warnings || [];
      const row = (await getJson("{QUEUE}")).json.find((r) => !r.locked && r.decision === "include");
      await getJson("/api/labels", {{ method: "POST", body: JSON.stringify({{ sample_id: row.sample_id, record_id: row.record_id,
        decision: "exclude" }}) }});
      await run({{ provider: "openai-t07", new_set: true }});
      await jobs();
      out.cp = Demo.state.checkpoint;
      out.two = (await getJson("/api/stages")).json.last_results.screen.warnings || [];
    """, snap)
    assert out["open"] > 0
    assert f"You labelled {out['open']} records differently from Reviewer A." in out["one"]
    assert not any("left unlabelled" in w for w in out["one"])
    assert any("continues with Reviewer A's decisions" in w for w in out["one"])
    assert out["cp"] == "screened2"
    assert "You labelled 1 record differently from Reviewer A." in out["two"]


def test_recall_estimate_is_marked_simulated(snap):
    out = go("""
      Demo.setCheckpoint("screened2");
      out.one = (await getJson("/api/recall?protocol=main&result_set=1&sample=validation")).json.stage_result.headline;
      out.two = (await getJson("/api/recall?protocol=main&result_set=2&sample=validation")).json.stage_result.headline;
    """, snap)
    assert out["one"].endswith("(simulated)") and out["two"].endswith("(simulated)")


def test_earlier_runs_leave_out_the_old_bootstrap_estimate(snap):
    """Tester 1 Nov 2026: the list of earlier recall runs showed "bootstrap 95% CI 1.00–1.00" from an earlier WIDER
    version beside the page's 0.54 to 1.00; the list leaves that estimate out and keeps every other run."""
    out = go("""
      Demo.setCheckpoint("screened2");
      const rows = (await getJson("/api/stages/recall/results?limit=30")).json;
      out.heads = rows.map((r) => r.action + ": " + r.headline);
      out.recorded = Demo.recorded("/api/stages/recall/results?limit=30", "screened2").length;
    """, snap)
    assert not any("bootstrap" in h for h in out["heads"])
    assert any(h.startswith("estimate:") and "95% interval 0.54 to 1.00" in h for h in out["heads"])
    assert len(out["heads"]) == out["recorded"] - 1


def test_undo_last_undoes_only_the_callers_own_change(snap):
    # as in WIDER: "Undo my last" walks the caller's own changes and signs the undo with that name
    out = go(f"""
      Demo.setCheckpoint("sampled");
      const row = (await getJson("{QUEUE}")).json.find((r) => !r.decision);
      await getJson("/api/labels", {{ method: "POST", body: JSON.stringify({{ sample_id: row.sample_id, record_id: row.record_id,
        decision: "include", reviewer: "Reviewer B" }}) }});
      out.other = await getJson("/api/history/undo-last?author=Reviewer%20C", {{ method: "POST" }});
      out.own = await getJson("/api/history/undo-last?author=Reviewer%20B", {{ method: "POST" }});
      out.authors = Demo.state.overlay.changes.map((c) => c.author);
    """, snap)
    assert out["other"]["status"] == 409 and out["other"]["json"]["detail"] == "nothing to undo by Reviewer C"
    assert out["own"]["status"] == 200 and out["own"]["json"]["summary"].startswith("undo: Reviewer B labelled")
    assert out["authors"] == ["Reviewer B", "Reviewer B"]
