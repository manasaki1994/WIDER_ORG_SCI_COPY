"""The guided tour in the page (showcase/demo/tour.js; openspec change interactive-demo, design D7, D8), run under Node
on the recorded snapshot: action steps wait and report, Skip and Next do a step as Reviewer A did, Back, Repeat and
Finish, Play stops where the visitor has something to do, the keys stay out of fields and out of the app's way, the
concept cards and the switch between Guided and Free."""
import importlib.util
import json
import sys

import pytest

from tests.unit.demo_node import DEMO_DIR, run_demo


@pytest.fixture(scope="module")
def snap():
    spec = importlib.util.spec_from_file_location("build_demo", DEMO_DIR / "build_demo.py")
    build = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("build_demo", build)
    spec.loader.exec_module(build)
    coverage = json.loads(build.COVERAGE.read_text(encoding="utf-8"))
    return build.snapshot_js(build.SNAPSHOT, coverage, "nobody-in-particular")


# a page with the Search strings element (for the terms panel); the tour started as a first-time visitor starts it
START = """
  globalThis.beforeBoot = () => {
    const card = document.body.appendChild(document.createElement("section"));
    card.setAttribute("id", "page-query");
    card.appendChild(document.createElement("div")).setAttribute("id", "q-strings");
  };
  boot();
  Demo.choose("guided", "all");
  const idx = (id) => DemoTour.steps.findIndex((s) => s.id === id);
  const goTo = (id) => DemoTour.go(idx(id));
  const report = () => $("demo-tour-report").textContent;
  const dedup = async () => (await getJson("/api/dedup")).json;
  const decide = (row, decision) => getJson("/api/dedup/decide", { method: "POST",
    body: JSON.stringify({ raw_a: row.raw_a, raw_b: row.raw_b, decision, by: "Reviewer A" }) });
  const apply = () => getJson("/api/dedup/run", { method: "POST" });
"""


def run(body, snap):
    return run_demo(START + body, snapshot_js=snap, tour=True)


def test_tour_starts_at_the_welcome(snap):
    out = run("""
      out.at = [DemoTour.at, DemoTour.steps[DemoTour.at].id, location.hash];
      out.where = $("demo-tour-where").textContent;
      out.who = $("demo-tour-who").textContent;
      out.on = document.documentElement.classList.contains("demo-tour-on");
      out.back = $("demo-tour-back").disabled;
      out.focus = document.activeElement && document.activeElement.id;
    """, snap)
    assert out["at"] == [0, "welcome", "home"]
    assert out["where"].startswith("Chapter 1 of 10 · Start · step 1 of ")
    assert out["who"] == "WIDER does" and out["on"] is True and out["back"] is True
    assert out["focus"] == "demo-tour-title"



def test_action_step_waits_then_reports(snap):
    out = run("""
      goTo("dedup-act");
      out.wait = [report(), $("demo-tour-skip").hidden];
      const d = await dedup();
      await decide(d.queue[0], "duplicate");
      out.decided = report();
      await apply();
      out.done = [report(), $("demo-tour-skip").hidden];
    """, snap)
    assert out["wait"] == ["Your turn: open a pair, decide it, then press “Apply decisions and check again”.", False]
    assert out["decided"] == "You decided a pair. The count changes only after “Apply decisions and check again”."
    assert out["done"] == ["50 → 49 unique records.", True]


def test_a_pair_kept_apart_changes_no_count(snap):
    out = run("""
      goTo("dedup-act");
      await decide((await dedup()).queue[0], "not_duplicate");
      await apply();
      out.done = report();
    """, snap)
    assert out["done"] == "50 → 50 unique records: a pair decided as different papers changes no count."


def test_skip_does_the_action(snap):
    out = run("""
      goTo("query-terms");
      await DemoTour.skip();
      out.terms = report();
      goTo("dedup-act");
      await DemoTour.skip();
      out.dedup = [report(), (await dedup()).flow.left];
      goTo("rank-act");
      await DemoTour.skip();
      out.rank = [report(), Demo.state.checkpoint];
      goTo("sample-draw");
      await DemoTour.skip();
      out.draw = [report(), Demo.state.checkpoint];
      out.sent = spy.fetch + spy.beacon;
    """, snap)
    assert out["terms"].startswith("Done for you: suggestion* removed. Now: ")
    assert out["dedup"] == ["Done for you: one pair decided as the same paper and applied; 49 unique records.", 49]
    assert out["rank"] == ["Done for you: the records are ranked.", "ranked"]
    assert out["draw"] == ["Done for you: two samples drawn.", "sampled"]
    assert out["sent"] == 0


def test_next_past_an_undone_step_does_it_first(snap):
    out = run("""
      goTo("rank-act");
      await DemoTour.next();
      out.after = [DemoTour.steps[DemoTour.at].id, Demo.state.checkpoint, $("demo-notice").textContent];
      goTo("query-terms");
      await DemoTour.next();
      out.terms = [DemoTour.steps[DemoTour.at].id, JSON.stringify(Demo.state.overlay.terms || null)];
    """, snap)
    assert out["after"] == ["rank-score", "ranked", "Done for you: the records are ranked."]
    # the next step needs nothing new: the visitor's terms stay as they are
    assert out["terms"][0] == "query-strings" and "suggestion*" in out["terms"][1]


def test_label_skip_moves_on(snap):
    out = run("""
      Demo.setCheckpoint("sampled");
      goTo("sample-label");
      out.wait = report();
      await DemoTour.skip();
      out.at = [DemoTour.at, idx("sample-label") + 1];
      out.labels = Object.keys(Demo.state.overlay.labels || {}).length;
    """, snap)
    assert out["wait"].startswith("Your turn: open a record that is not labelled yet")
    assert out["at"][0] == out["at"][1]
    assert out["labels"] == 0


def test_screen_waits_for_the_development_labels_and_skip_fills_them(snap):
    # ui-redesign D4: while development records are open the step says so; Skip labels them as Reviewer A did and runs
    out = run("""
      Demo.setCheckpoint("sampled");
      goTo("screen-act");
      out.wait = report();
      out.open = DemoActions.devOpen();
      await DemoTour.skip();
      out.cp = Demo.state.checkpoint;
      out.said = report();
    """, snap)
    assert out["open"] > 0
    assert out["wait"] == ("Your turn: label the development records still open (“Continue labelling”), then press "
                           "“Run”; or press Skip and the demo labels them as Reviewer A did.")
    assert out["cp"] == "screened"
    # the labelling page's own POST labels them first (design D3), so the run is not refused and the notice says so
    assert out["said"].startswith(f"Done for you: the {out['open']} development records still open got Reviewer A's "
                                  f"labels.")


def test_back_repeat_and_finish(snap):
    out = run("""
      DemoTour.go(2);
      $("demo-tour-back").click();
      out.back = DemoTour.at;
      $("demo-tour-repeat").click();
      out.repeat = DemoTour.at;
      DemoTour.go(DemoTour.steps.length - 1);
      out.last = [$("demo-tour-next").textContent, DemoTour.steps[DemoTour.at].id];
      $("demo-tour-next").click();
      await tick();
      const end = $("demo-end");
      out.end = [!!end, end && end.open, $("demo-card-title").textContent];
      out.buttons = ["demo-end-free", "demo-end-again", "demo-end-exit"].map((id) => !!$(id));
      $("demo-end-free").click();
      out.free = [Demo.state.mode, !!$("demo-end"), $("demo-tour").hidden, document.activeElement.id];
    """, snap)
    assert out["back"] == 1 and out["repeat"] == 1
    assert out["last"] == ["Finish", "finish"]
    assert out["end"] == [True, True, "Start a review of your own"]
    # no way back to the showcase page from the end card (owner, 31 Oct 2026)
    assert out["buttons"] == [True, True, False]
    assert out["free"] == ["free", False, True, "demo-guided"]


def test_play_stops_at_action_steps(snap):
    out = run("""
      DemoTour.go(0);
      DemoTour.togglePlay();
      out.started = DemoTour.playing;
      for (let i = 0; i < 300 && DemoTour.playing === "playing"; i++) await tick(1);
      out.stop = [DemoTour.steps[DemoTour.at].id, DemoTour.playing, $("demo-tour-play").textContent];
    """, snap)
    assert out["started"] == "playing"
    assert out["stop"] == ["query-terms", "idle", "Play"]


def test_pause_resume_stop(snap):
    out = run("""
      const calls = [];
      globalThis.DemoJobs = { pause: () => calls.push("pause"), resume: () => calls.push("resume"), stop: () => calls.push("stop") };
      DemoTour.timing.min = 100000;
      DemoTour.go(0);
      $("demo-tour-play").click();
      out.playing = [DemoTour.playing, $("demo-tour-play").textContent, $("demo-tour-stop").disabled];
      $("demo-tour-play").click();
      out.paused = [DemoTour.playing, $("demo-tour-play").textContent];
      $("demo-tour-play").click();
      out.resumed = DemoTour.playing;
      $("demo-tour-stop").click();
      out.stopped = [DemoTour.playing, $("demo-tour-play").textContent, $("demo-tour-stop").disabled, DemoTour.at];
      out.calls = calls;
    """, snap)
    assert out["playing"] == ["playing", "Pause", False]
    assert out["paused"] == ["paused", "Resume"]
    assert out["resumed"] == "playing"
    assert out["stopped"] == ["idle", "Play", True, 0]
    assert out["calls"] == ["pause", "resume", "stop"]


def test_keys_only_outside_fields(snap):
    out = run("""
      DemoTour.go(1);
      key("ArrowRight");
      out.right = DemoTour.at;
      key("ArrowLeft");
      out.left = DemoTour.at;
      const input = document.body.appendChild(document.createElement("input"));
      input.focus();
      const e = key("ArrowRight");
      out.field = [DemoTour.at, e.defaultPrevented];
      const b = document.body.appendChild(document.createElement("button"));
      b.focus();
      key(" ");
      out.space_on_button = DemoTour.playing;
      const r = key("ArrowRight"), l = key("ArrowLeft");
      out.arrows_on_button = [DemoTour.at, r.defaultPrevented, l.defaultPrevented];
      $("demo-tour-title").focus();
      key(" ");
      out.space = DemoTour.playing;
      DemoTour.stop();
      key("Escape");
      out.hidden = [$("demo-tour").hidden, document.documentElement.classList.contains("demo-tour-on"), $("demo-show-tour").hidden];
      key("ArrowRight");
      out.after_hide = DemoTour.at;
      Demo.setMode("free");
      DemoTour.go(1);
      Demo.setMode("free");
      key("ArrowRight");
      out.free = [DemoTour.at, $("demo-tour").hidden];
    """, snap)
    assert out["right"] == 2 and out["left"] == 1
    assert out["field"] == [1, False]
    assert out["space_on_button"] == "idle"
    assert out["arrows_on_button"] == [1, False, False]
    assert out["space"] == "playing"
    assert out["hidden"] == [True, False, False]
    assert out["after_hide"] == 1
    assert out["free"] == [1, True]


def test_escape_goes_to_app_first(snap):
    out = run("""
      DemoTour.go(1);
      DemoTour.openConcept("scores");
      key("Escape");
      out.card = $("demo-tour").hidden;
      DemoTour.openConcept("scores");
      $("demo-card-close").click();
      globalThis.Drawer = { isOpen: () => true, isDocked: () => true };
      $("demo-tour-title").focus();
      key("Escape");
      out.docked_escape = $("demo-tour").hidden;
      key("ArrowRight");
      out.docked_arrow = DemoTour.at;
      Drawer.isDocked = () => false;
      key("ArrowRight");
      out.floating_arrow = DemoTour.at;
      globalThis.Drawer = undefined;
      key("Escape");
      out.free_escape = $("demo-tour").hidden;
    """, snap)
    assert out["card"] is False
    assert out["docked_escape"] is False and out["docked_arrow"] == 2
    assert out["floating_arrow"] == 2
    assert out["free_escape"] is True


def test_concepts_menu(snap):
    out = run("""
      const bar = $("demo-concepts");
      out.bar = bar && bar.parent.children.map((c) => c.id).filter((id) => id === "demo-concepts" || id === "demo-reset");
      bar.click();
      const menu = $("demo-concepts-menu");
      out.menu = [menu.open, menu.textContent.includes("Scores and percentiles"), !!$("demo-open-end")];
      $("demo-open-scores").click();
      await tick();   // the menu's close event comes now and must not take the new card with it
      out.card = [!!$("demo-concepts-menu"), $("demo-concept-scores").open, $("demo-card-title").textContent];
      out.text = $("demo-concept-scores").textContent;
      $("demo-card-menu").click();
      await tick();
      out.back = !!$("demo-concepts-menu") && !$("demo-concept-scores");
      $("demo-open-end").click();
      await tick();
      out.end = !!$("demo-end");
      $("demo-end").close();   // Escape in a browser
      await tick();
      out.closed = [!!$("demo-end"), !!$("demo-concepts-menu")];
      goTo("rank-pct");
      out.link = $("demo-tour-concept").textContent;
      $("demo-tour-concept").click();
      await tick();
      out.linked = !!$("demo-concept-scores");
    """, snap)
    assert out["bar"] == ["demo-concepts", "demo-reset"]
    assert out["menu"] == [True, True, True]
    assert out["card"] == [False, True, "Scores and percentiles"]
    assert "rank 4 of 39" in out["text"]
    assert out["back"] is True and out["end"] is True
    assert out["closed"] == [False, False]
    assert out["link"] == "More: Scores and percentiles" and out["linked"] is True


def test_mode_switch_keeps_page_and_state(snap):
    out = run("""
      goTo("dedup-act");
      await decide((await dedup()).queue[0], "duplicate");
      Demo.setMode("free");
      out.free = [location.hash, $("demo-tour").hidden, Object.keys(Demo.state.overlay.pairs || {}).length];
      Demo.setMode("guided");
      out.guided = [location.hash, DemoTour.steps[DemoTour.at].id, $("demo-tour").hidden,
                    Object.keys(Demo.state.overlay.pairs || {}).length];
      out.report = report();
    """, snap)
    assert out["free"] == ["dedup", True, 1]
    assert out["guided"] == ["dedup", "dedup-act", False, 1]
    assert out["report"].startswith("You decided a pair.")


def test_reload_brings_the_tour_back(snap):
    out = run("""
      goTo("dedup-act");
      boot();
      out.before = !!$("demo-tour");
      loaded();
      out.after = [!!$("demo-tour") && !$("demo-tour").hidden, DemoTour.steps[DemoTour.at].id, location.hash];
      Demo.setMode("free");
      boot();
      loaded();
      out.free = !$("demo-tour") || $("demo-tour").hidden;
    """, snap)
    assert out["before"] is False
    assert out["after"] == [True, "dedup-act", "dedup"]
    assert out["free"] is True


def test_opening_the_demo_again_without_a_page_keeps_the_step(snap):
    out = run("""
      goTo("dedup-act");
      location.hash = "";
      boot();
      loaded();
      out.at = [DemoTour.steps[DemoTour.at].id, location.hash];
    """, snap)
    assert out["at"] == ["dedup-act", "dedup"]


def test_guided_after_free_explains_passed_steps(snap):
    out = run("""
      Demo.setMode("free");
      Demo.setCheckpoint(Demo.order().at(-1));
      location.hash = "dedup";
      Demo.setMode("guided");
      out.at = DemoTour.steps[DemoTour.at].id;
      out.passed = [$("demo-tour-passed").hidden, $("demo-tour-passed").textContent];
      goTo("dedup-act");
      out.act = [report(), $("demo-tour-skip").hidden];
      goTo("query-check");
      out.unchanged = $("demo-tour-passed").hidden;
    """, snap)
    assert out["at"] == "dedup-pairs"
    assert out["passed"][0] is False and "already happened in this demo" in out["passed"][1]
    assert out["act"] == ["Done in this demo already.", True]
    assert out["unchanged"] is True


def test_recall_step_and_card_explain_the_interval_the_app_shows(snap):
    # the recording's validation sample has 7 relevant records, all kept: the tour quotes the interval the Recall page
    # shows (54% to 100%), not the bootstrap's 100% to 100%, and says why it is wide although none was missed
    out = run("""
      goTo("recall-interval");
      out.step = [$("demo-tour-who").textContent, $("demo-tour-body").textContent, $("demo-tour-concept").textContent];
      $("demo-tour-concept").click();
      await tick();
      out.card = $("demo-concept-recall").textContent;
    """, snap)
    who, step, link = out["step"]
    assert who == "You decide" and link == "More: Recall and its uncertainty"
    for text in (step, out["card"]):
        assert "a 95% interval from 54% to 100%" in text and "100% to 100%" not in text
        assert "All 7 relevant records in the sample were kept, yet the interval reaches down to 54%" in text
        assert "label a bigger validation sample" in text and "single point" not in text
        assert "Illustrative: invented papers, not evidence about any method." in text
    assert "How it is worked out" in out["card"] and "exact bounds on an effective number of relevant records" in out["card"]


# ---- demo-chapters-and-video: the Quick tour, the chapters, narrow screens ------------------------------------------
QUICK = """
  Demo.state.path = "core"; Demo.save(); DemoTour.start();
  const core = DemoTour.steps.filter((s) => s.core).map((s) => s.id);
"""


def test_quick_tour_starts_and_counts_core_steps(snap):
    out = run(QUICK + """
      out.first = [DemoTour.steps[DemoTour.at].id, core[0], $("demo-tour-where").textContent, $("demo-tour-back").disabled];
      $("demo-tour-next").click();
      await tick();
      out.second = [DemoTour.steps[DemoTour.at].id, core[1], $("demo-tour-where").textContent];
      out.n = core.length;
    """, snap)
    n = out["n"]
    assert 8 <= n <= 12
    assert out["first"] == [out["first"][1], out["first"][1], f"Quick tour · step 1 of {n}", True]
    assert out["second"][0] == out["second"][1] and out["second"][2] == f"Quick tour · step 2 of {n}"


def test_chapter_intro_on_first_step_only(snap):
    out = run("""
      const first = DemoTour.steps.findIndex((s) => s.stage === "Deduplication");
      const ch = DemoTour.chapters.find((c) => c.stage === "Deduplication");
      DemoTour.go(first);
      out.one = [$("demo-tour-intro").textContent, $("demo-tour-intro").hidden, ch.intro];
      DemoTour.go(first + 1);
      out.two = [DemoTour.steps[first + 1].stage, $("demo-tour-intro").hidden, $("demo-tour-intro").textContent];
      out.where = $("demo-tour-where").textContent;
    """, snap)
    assert out["one"][0] == out["one"][2] and not out["one"][1]
    assert out["two"][0] == "Deduplication" and (out["two"][1] or out["two"][2] == "")
    assert out["where"].startswith("Chapter ") and " · Deduplication · step 2 of " in out["where"]


def test_quick_tour_does_missing_steps_first(snap):
    # from the welcome straight through the core path: every action between is done as Reviewer A did it; arriving at
    # the screening step the development records are labelled, so Run on the page starts the job (D3)
    out = run(QUICK + """
      const target = idx("screen-act");
      let said = [];
      while (DemoTour.at < target) {
        const before = DemoTour.at;
        $("demo-tour-next").click();
        for (let i = 0; i < 50 && DemoTour.at === before; i++) await tick();
        said.push($("demo-notice").textContent);
        if (DemoTour.at === before) break;
      }
      out.at = DemoTour.steps[DemoTour.at].id;
      out.open = DemoActions.devOpen();
      out.said = said.join(" | ");
      const r = await getJson("/api/jobs/screen", { method: "POST", body: JSON.stringify({ scope: "corpus",
        pass_name: "A", provider: "openai", prompt_version: 1, limit: null, mode: "protocol", protocol: "main" }) });
      out.run = r.status;
    """, snap)
    assert out["at"] == "screen-act"
    assert out["open"] == 0
    assert "development records still open got Reviewer A's labels." in out["said"]
    assert out["run"] in (200, 202)


def test_quick_tour_end_card_offers_chapters(snap):
    out = run(QUICK + """
      DemoTour.go(idx(core[core.length - 1]));
      out.next = $("demo-tour-next").textContent;
      $("demo-tour-next").click();
      await tick();
      const ids = ["demo-end-chapters", "demo-end-free", "demo-end-again", "demo-end-exit"];
      const row = $("demo-end-free").parentElement || $("demo-end-free").parent;
      out.buttons = row.children.map((b) => b.id || (b.getAttribute && b.getAttribute("id"))).filter((x) => ids.includes(x));
      $("demo-end-chapters").click();
      out.list = [!!$("demo-chapters"), $("demo-card-title").textContent];
    """, snap)
    assert out["next"] == "Finish"
    for b in ("demo-end-chapters", "demo-end-free", "demo-end-again"):
        assert b in out["buttons"]
    assert "demo-end-exit" not in out["buttons"]
    assert out["buttons"].index("demo-end-chapters") < out["buttons"].index("demo-end-free")
    assert out["list"] == [True, "Chapters"]


def test_chapter_list_ticks_seen_chapters(snap):
    out = run("""
      const mine = DemoTour.steps.map((s, i) => [s, i]).filter(([s]) => s.stage === "Start");
      for (const [, i] of mine) DemoTour.go(i);
      $("demo-tour-chapters").click();
      out.one = $("demo-chapter-1").textContent;
      out.two = $("demo-chapter-2").textContent;
    """, snap)
    assert out["one"].endswith(" · ✓ seen")
    assert "✓ seen" not in out["two"]


def test_jump_ahead_moves_checkpoint(snap):
    out = run("""
      Demo.setCheckpoint("imported");
      $("demo-tour-chapters").click();
      const i = DemoTour.chapters.findIndex((c) => c.stage === "Screening");
      $(`demo-chapter-${i + 1}`).click();
      for (let k = 0; k < 100 && !location.reloaded && DemoTour.steps[DemoTour.at].stage !== "Screening"; k++) await tick();
      out.reloaded = location.reloaded;
      if (location.reloaded) { boot(); await tick(); }
      out.at = DemoTour.steps[DemoTour.at].stage;
      out.cp = Demo.state.checkpoint;
      out.path = Demo.state.path;
      out.hash = location.hash;
      out.notice = $("demo-notice").textContent;
    """, snap)
    assert out["at"] == "Screening" and out["path"] == "all"
    assert out["cp"] != "imported"
    assert out["notice"] == "The demo moved ahead to Screening: the earlier steps were done as Reviewer A did them."
    assert out["hash"].replace("#", "") == "screen"


def test_jump_back_shows_passed_note(snap):
    out = run("""
      Demo.setCheckpoint("screened2");
      $("demo-tour-chapters").click();
      const i = DemoTour.chapters.findIndex((c) => c.stage === "Deduplication");
      $(`demo-chapter-${i + 1}`).click();
      await tick();
      out.at = DemoTour.steps[DemoTour.at].stage;
      out.cp = Demo.state.checkpoint;
      const acts = DemoTour.steps.map((s, k) => [s, k]).filter(([s]) => s.stage === "Deduplication" && s.action);
      DemoTour.go(acts[0][1]);
      out.passed = [$("demo-tour-passed").hidden, $("demo-tour-passed").textContent];
    """, snap)
    assert out["at"] == "Deduplication" and out["cp"] == "screened2"
    assert out["passed"][0] is False
    assert "This step already happened in this demo. Reset to replay it from the start, or read on." in out["passed"][1]


def test_chapter_list_keyboard(snap):
    out = run("""
      $("demo-tour-chapters").click();
      out.open = document.activeElement && document.activeElement.id;
      $("demo-card-close").click();
      await tick();
      out.back = document.activeElement && document.activeElement.id;
      out.gone = !$("demo-chapters");
    """, snap)
    assert out["open"] == "demo-chapter-1"
    assert out["back"] == "demo-tour-chapters" and out["gone"] is True


def test_narrow_tour_card_docks_at_bottom(snap):
    # D7: at 700 px and below the card sits at the bottom, above the app's fixed main button when it shows one
    out = run_demo("""
      globalThis.narrowScreen = true;
      globalThis.beforeBoot = () => {
        const main = document.body.appendChild(document.createElement("div"));
        main.setAttribute("id", "tb-main");
        main.appendChild(document.createElement("button"));
        main.offsetHeight = 56;
      };
      boot();
      Demo.choose("guided", "all");
      out.narrow = $("demo-tour").style.bottom;
      globalThis.narrowScreen = false;
      DemoTour.go(1);
      out.wide = $("demo-tour").style.bottom;
    """, snapshot_js=snap, tour=True)
    assert out["narrow"] == "56px" and out["wide"] == ""


def test_mode_switch_keeps_path(snap):
    out = run(QUICK + """
      $("demo-tour-next").click();
      await tick();
      const at = DemoTour.steps[DemoTour.at].id;
      Demo.choose("free");
      Demo.choose("guided");
      out.after = [Demo.state.path, DemoTour.steps[DemoTour.at].id, at, $("demo-tour-where").textContent];
    """, snap)
    assert out["after"][0] == "core" and out["after"][1] == out["after"][2]
    assert out["after"][3].startswith("Quick tour · step 2 of ")


# ---- tester 1 Nov 2026: a step's button inside a Screening tab that is not chosen ------------------------------------
TABS = """
  globalThis.beforeBoot = () => {
    const page = document.body.appendChild(document.createElement("section"));
    page.setAttribute("id", "page-screen");
    const bar = page.appendChild(document.createElement("div"));
    bar.className = "page-tabs";
    const panels = {};
    for (const key of ["setup", "review"]) {
      const tab = bar.appendChild(document.createElement("button"));
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-controls", "sc-tab-" + key); tab.dataset.tab = key;
      panels[key] = page.appendChild(document.createElement("div"));
      panels[key].setAttribute("id", "sc-tab-" + key);
    }
    panels.setup.appendChild(document.createElement("button")).setAttribute("id", "sc-run");
    panels.review.appendChild(document.createElement("button")).setAttribute("id", "ss-compare");
    // as app.js initPageTabs: only the chosen tab's panel is shown, and the choice is kept for the browser tab
    bar.pick = (key) => {
      for (const [k, p] of Object.entries(panels)) p.hidden = k !== key;
      sessionStorage.setItem("wider.tab.page-screen", key);
    };
    bar.pick(sessionStorage.getItem("wider.tab.page-screen") || "setup");
  };
"""


@pytest.mark.parametrize(("step", "tab", "target"), [("screen-compare", "review", "ss-compare"),
                                                      ("screen-act", "setup", "sc-run")])
def test_step_in_a_hidden_tab_chooses_that_tab(snap, step, tab, target):
    other = "setup" if tab == "review" else "review"
    out = run_demo(TABS + f"""
      session.setItem("wider.tab.page-screen", "{other}");
      boot();
      Demo.choose("guided", "all");
      DemoTour.go(DemoTour.steps.findIndex((s) => s.id === "{step}"));
      await tick(5);
      out.tab = sessionStorage.getItem("wider.tab.page-screen");
      out.shown = !$("sc-tab-{tab}").hidden;
      out.marked = $("{target}").classList.contains("demo-target");
      out.head = $("page-screen").classList.contains("demo-target");
    """, snapshot_js=snap, tour=True)
    assert out == {"tab": tab, "shown": True, "marked": True, "head": False}


def test_recall_step_presses_estimate_when_the_tables_are_empty(snap):
    """Tester 1 Nov 2026: the Quick tour's Recall step pointed at the empty tables (height 0) until "Estimate recall"
    was pressed. The step presses it when the tables are empty, and only then."""
    out = run_demo("""
      globalThis.beforeBoot = () => {
        const page = document.body.appendChild(document.createElement("section"));
        page.setAttribute("id", "page-recall");
        const run = page.appendChild(document.createElement("button"));
        run.setAttribute("id", "rc-run");
        run.addEventListener("click", () => { out.pressed = (out.pressed || 0) + 1;
          document.getElementById("rc-tables").textContent = "Recall 1.00"; });
        page.appendChild(document.createElement("div")).setAttribute("id", "rc-tables");
      };
      boot();
      Demo.choose("guided", "all");
      const at = DemoTour.steps.findIndex((s) => s.id === "recall-interval");
      DemoTour.go(at);
      await tick(5);
      out.first = [out.pressed, $("rc-tables").classList.contains("demo-target")];
      DemoTour.go(at - 1); DemoTour.go(at);
      await tick(5);
      out.again = out.pressed;
      out.body = $("demo-tour-body").textContent;
    """, snapshot_js=snap, tour=True)
    assert out["first"] == [1, True]
    assert out["again"] == 1
    assert "if it was not pressed yet, the demo presses it for you" in out["body"]
