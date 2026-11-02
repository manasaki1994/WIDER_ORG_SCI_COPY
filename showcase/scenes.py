"""The script of the WIDER showcase video (openspec change demo-chapters-and-video, design D8): the built offline demo,
walked along its own tour chapters, one async function per scene, run by showcase/record.py.

    python3 showcase/demo/build_demo.py
    python3 showcase/record.py ~/wider-showcase-video --site showcase/site

The video shows the result first (the recall estimate and the chapter names), then one scene per chapter of the tour
on the All chapters path, then an end card. Each chapter scene names the chapter in the stage caption, opens its steps
in the demo's tour card and does each action step on the page with the drawn cursor: the page's own button where
there is one (Rank, Draw samples, Estimate recall), else the tour card's Skip, which does the step as Reviewer A did.

Pacing: every caption and tour card is held for max(2.0, 0.5 + characters / 15) seconds over the title and the first
paragraph only (D8); the rest of a card stays on screen for a paused video, and every card's whole text goes to the
captions track. A step outside the core path that only shows something is left out when its chapter would run past
40 seconds; a core step or an action step never is.

The demo carries its state from one chapter to the next, so the scenes are filmed together, in FILM_ORDER: the opening
shows the recall result, which exists only after the Recall chapter, so it is filmed last and shown first.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml

TOUR = Path(__file__).resolve().parent / "demo" / "tour.yaml"
LIMIT = 40.0          # seconds a chapter may run before its show-only extra steps are left out
ACTION = 4.0          # about what doing one action takes on screen (cursor, click, report)
# the page's own button for an action, where one press does it; the others go through the tour card's Skip
BUTTONS = {"rank": "#rk-run", "draw": "#sm-draw", "recall": "#rc-run"}


def hold(text: str) -> float:
    """Seconds a caption or card stays on screen: about 15 characters a second, at least 2 (D8)."""
    return max(2.0, 0.5 + len(" ".join(str(text).split())) / 15)


def first_paragraph(body) -> str:
    if isinstance(body, list):
        return str(body[0]) if body else ""
    return str(body or "").strip().split("\n\n")[0]


def step_time(step: dict) -> float:
    return hold(f"{step['title']} {first_paragraph(step.get('body'))}") + (ACTION if step.get("action") else 0.0)


def plan(tour: dict | None = None, limit: float = LIMIT) -> list[dict]:
    """The chapter scenes: for each chapter of the tour that has steps, in order, its steps on the All chapters path
    less the show-only extra steps that would take it past `limit` seconds (dropped from the end of the chapter first).
    Each scene is {"n", "stage", "title", "intro", "steps": [step ids], "seconds"}."""
    tour = tour or yaml.safe_load(TOUR.read_text(encoding="utf-8"))
    scenes = []
    chapters = [c for c in tour["chapters"] if any(s["stage"] == c["stage"] for s in tour["steps"])]
    for n, ch in enumerate(chapters, 1):
        steps = [s for s in tour["steps"] if s["stage"] == ch["stage"]]
        keep = list(steps)
        total = lambda: hold(ch["intro"]) + sum(step_time(s) for s in keep)  # noqa: E731
        for s in reversed(steps):
            if total() <= limit:
                break
            if not s.get("core") and not s.get("action") and len(keep) > 1:
                keep.remove(s)
        scenes.append({"n": n, "stage": ch["stage"], "title": ch["title"], "intro": " ".join(ch["intro"].split()),
                       "steps": [s["id"] for s in keep], "seconds": round(total(), 1)})
    return scenes


PLAN = plan()
OF = len(PLAN)


# ------------------------------------------------------------------------------------------------------------ helpers
def tour_js(expr: str) -> str:
    return f"stage.win().DemoTour.{expr}"


async def note(rec, html, sel=None, text=None, side="below"):
    await rec.js(f"stage.note({json.dumps(html)}, {json.dumps(sel)}, {json.dumps(text)}, {json.dumps(side)})")


async def unnote(rec):
    await rec.js("stage.note(null)")
    await rec.highlight(None)


def titled(title: str) -> str:
    """A card's title as the first sentence of its caption: the card shows it as a heading, the caption needs the full
    stop (tester 1 Nov 2026: "Where the two rounds differ Two different comparisons")."""
    title = title.strip()
    return title if not title or title[-1] in ".:?!" else title + "."


async def card_text(rec) -> dict:
    return await rec.js("""(() => { const d = stage.doc(), t = (id) => (d.getElementById(id) || {}).textContent || "";
      const ps = [...d.querySelectorAll('#demo-tour-body p')].map(p => p.textContent);
      return { where: t('demo-tour-where'), title: t('demo-tour-title'), body: ps, report: t('demo-tour-report'),
               waiting: !!d.querySelector('#demo-tour-report.demo-tour-waiting') }; })()""")


async def open_step(rec, step_id: str):
    """Open a step in the tour card: the Next button with the cursor when it is the next step of the path, else
    straight there."""
    index = await rec.js(f"{tour_js('steps')}.findIndex(s => s.id === {json.dumps(step_id)})")
    nxt = await rec.js(f"(() => {{ const p = {tour_js('path')}, i = p.indexOf({tour_js('at')}); return p[i + 1]; }})()")
    if nxt == index and await rec.js("!!stage.find('#demo-tour-next')"):
        await rec.click("#demo-tour-next", settle=0.4)
    if await rec.js(tour_js("at")) != index:
        await rec.js(f"{tour_js('go')}({index})")
    title = await rec.js(f"{tour_js('steps')}[{index}].title")
    await rec.wait_for(f"(stage.doc().getElementById('demo-tour-title') || {{}}).textContent === {json.dumps(title)}", 20)
    await asyncio.sleep(1.0)              # the page opens and scrolls to the step's element
    await rec.js("stage.doc().querySelectorAll('.toast').forEach(t => t.remove())")


async def do_action(rec, action: str):
    """Do the step's action on the page with the cursor, then wait until the tour card reports it done."""
    sel = BUTTONS.get(action)
    if sel and await rec.js(f"!!stage.find({json.dumps(sel)})"):
        await rec.scroll_to(sel, seconds=0.8, top=260)
        await rec.click(sel, settle=0.6)
    else:
        await note(rec, "<b>Skip</b> does this step the way Reviewer A did it in the recorded review.",
                   "#demo-tour-skip", side="left")
        await rec.hold(2.5)
        await unnote(rec)
        await rec.click("#demo-tour-skip", settle=0.6)
    if action != "label":     # Skip on the labelling step goes on to the next step (the labels stay Reviewer A's)
        await rec.wait_for("!stage.doc().querySelector('#demo-tour-report.demo-tour-waiting')", 60)
    await rec.hide_cursor()


async def chapter_scene(rec, scene: dict):
    rec.cue("chapter", f"{scene['n']}. {scene['title']}")
    await rec.caption(f"Chapter {scene['n']} of {OF}", scene["title"], scene["intro"])
    text = f"Chapter {scene['n']} of {OF}: {scene['title']}. {scene['intro']}"
    rec.cue("caption", text)
    await rec.hold(hold(text))
    steps = {s["id"]: s for s in await rec.js(f"{tour_js('steps')}.map(s => ({{ id: s.id, action: s.action || null }}))")}
    for sid in scene["steps"]:
        if await rec.js(f"{tour_js('steps')}[{tour_js('at')}].id") != sid:
            await open_step(rec, sid)
        card = await card_text(rec)
        text = " ".join([titled(card["title"])] + card["body"])
        rec.cue("caption", text)
        await rec.hold(hold(text))   # the whole caption, so it reads at 15 characters a second or slower
        action = steps[sid]["action"]
        if action and card["waiting"]:
            await do_action(rec, action)
            card = await card_text(rec)
            if card["report"].strip():
                rec.cue("caption", card["report"])
                await rec.hold(hold(card["report"]))


# ------------------------------------------------------------------------------------------------------------ scenes
async def start(rec):
    """Not shown: a fresh demo in the iframe, on the All chapters path."""
    await rec.card("title", True)
    await rec.go("home", settle=1.0)
    await rec.js("stage.win().sessionStorage.clear(); stage.win().location.replace('/demo/')")
    await asyncio.sleep(1.0)
    await rec.wait_for("!!stage.find('#demo-start-all')", 60)
    await rec.js("stage.click('#demo-start-all')")
    await rec.wait_for("!!stage.find('#demo-tour-title')", 30)


async def opening(rec):
    """The result first: the recall estimate with its interval, then the chapter names."""
    rec.cue("chapter", "The result first")
    await rec.card("title", True)
    text = ("WIDER: a literature review tool for broad constructs. A prototype; the review, its papers and authors "
            "are invented for this demo.")
    rec.cue("caption", text)
    await rec.hold(max(6, hold(text)))
    await rec.js(tour_js("hide()"))
    await rec.go("recall", settle=1.5)
    await rec.wait_for("!!stage.find('#rc-tables table')", 30)
    await rec.caption("Where the review ends", "A recall estimate, with its interval",
                      "How many of the relevant papers the screening kept, estimated from a blind human sample.")
    text = ("Where the review ends: a recall estimate, with its interval. How many of the relevant papers the "
            "screening kept, estimated from a blind human sample.")
    rec.cue("caption", text)
    await rec.card("title", False)
    await rec.jump_to("#rc-tables", top=40)
    await rec.fade_in(18)
    await rec.highlight("#rc-tables")
    await rec.hold(max(7, hold(text)))
    await rec.highlight(None)
    await rec.js("stage.chapters(" + json.dumps([s["title"] for s in PLAN]) + ")")
    await rec.card("chapters", True)
    text = "How it got there, in " + str(OF) + " chapters: " + "; ".join(s["title"] for s in PLAN) + "."
    rec.cue("caption", text)
    await rec.fade_in(12)
    await rec.hold(hold(text))


async def ending(rec):
    rec.cue("chapter", "Try it yourself")
    await rec.card("end", True)
    text = "Try it yourself: open the demo on this page. It runs in your browser; nothing is sent anywhere."
    rec.cue("caption", text)
    await rec.fade_in(18)
    await rec.hold(max(6, hold(text)))


def _chapter(scene):
    async def film(rec):
        if scene["n"] == 1:
            await rec.card("chapters", False)
            await rec.card("title", False)
            await rec.fade_in(18)
        await chapter_scene(rec, scene)
    return film


SCENES = ([("start", start), ("opening", opening)]
          + [(f"ch{s['n']:02d}", _chapter(s)) for s in PLAN] + [("ending", ending)])
# the opening needs the recall result, so it is filmed after the last chapter; "start" leaves no frame in the video
FILM_ORDER = ["start"] + [f"ch{s['n']:02d}" for s in PLAN] + ["opening", "ending"]
