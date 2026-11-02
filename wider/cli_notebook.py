"""`wider notebook ...`: a step's runs, decisions and undos, newest first (design-pass-a1-dedup D10); `wider guide
...`: a page's shipped guide text (D11). Attached in cli.py."""
from __future__ import annotations

import json
from pathlib import Path

import typer

from .core.notebook import entry_line, step_notebook
from .core.project import Project

notebook_app = typer.Typer(help="A step's notebook: every run and change to the step, dated and signed; undo with "
                                "`wider history undo ID`.", no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")


@notebook_app.command("dedup")
def dedup(pair: tuple[int, int] = typer.Option((None, None), "--pair", help="Only this raw pair's history: two raw "
                                                "record ids, in either order."),
          limit: int = typer.Option(50, "--limit", help="Show at most this many entries."), as_json: bool = typer.Option(False, "--json", help="Print JSON."),
          directory: Path = DIR):
    """Deduplication runs (signed WIDER), the duplicate decisions and their undos, newest first."""
    p = Project.find(directory)
    try:
        rows = step_notebook(p, "dedup", pair if pair[0] is not None else None, limit)
    finally:
        p.close()
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print("nothing in the notebook yet")
    for e in rows:
        print(entry_line(e))



def guide_lines(g: dict) -> list[str]:
    """The guide as the CLI prints it: "What you can do here now" first, the notices, then the title and the six
    answers (steps numbered), as the panel shows them (ui-redesign D6, D14)."""
    from .guide import CAN_DO

    out = [f"{CAN_DO}: {g['can_do']}", ""]
    out += [*g["notices"], ""] if g["notices"] else []
    out.append(g["title"])
    for part in g["parts"]:
        out += ["", part["heading"]]
        numbered = part["heading"] == "What to do"
        out += [f"  {i}. {t}" if numbered else f"  - {t}" for i, t in enumerate(part["items"], 1)]
    if g.get("methods"):
        from .guide import methods

        titles = {t["id"]: t["title"] for t in methods.topics()}
        out += ["", "Why WIDER works this way: " + ", ".join(f"{titles.get(t, t)} ({t})" for t in g["methods"])]
    return out


def guide(page: str = typer.Argument(..., help="The page, e.g. dedup; or methods, for why WIDER works this way."),
          topic: str | None = typer.Argument(None, help="With methods: a topic id, or glossary."),
          as_json: bool = typer.Option(False, "--json", help="Print JSON."), directory: Path = DIR):
    """The guide shipped with WIDER for a page: what you can do here now, then where you are, what to do, why it
    matters, what you need, done when and what comes next, with the notices for the project's state first; exits with
    code 1 for a page that does not exist. ``wider guide methods [<topic>|glossary]`` prints the methods document
    instead, with no project needed."""
    from .guide import NO_PAGE, methods, pages
    from .guide import guide as build

    if page == "methods":
        try:
            if as_json:
                data = (methods.glossary() if topic == methods.GLOSSARY else methods.topic(topic) if topic
                        else {"topics": methods.topics(), "glossary": methods.glossary(),
                           "glossary_review": methods.glossary_review(), "problems": methods.problem_lines()})
                print(json.dumps(data, indent=2, ensure_ascii=False))
            else:
                print("\n".join(methods.methods_lines(topic)))
        except KeyError:
            print(methods.bad_topic(topic) or methods.NO_TOPIC.format(topic=topic))
            raise typer.Exit(1) from None
        return
    if topic is not None:
        print(f"wider guide {page} takes no second word; only wider guide methods takes a topic")
        raise typer.Exit(2)
    if page not in pages():
        print(NO_PAGE.format(page=page))
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        g = build(p, page)
    finally:
        p.close()
    if as_json:
        print(json.dumps(g, indent=2))
        return
    print("\n".join(guide_lines(g)))
