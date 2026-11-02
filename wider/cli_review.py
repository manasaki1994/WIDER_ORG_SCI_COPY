"""`wider review ...`: the Screening review page's twin (manual-review). Attached in cli.py."""
from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.markup import escape

from .core import journal
from .core.project import Project
from .screen import review as rv

review_app = typer.Typer(help="Screening review: the records of a result set that need a person, each pass's read, "
                              "decisions after screening, the set's criteria and quality-control samples.",
                         no_args_is_help=True)
qc_app = typer.Typer(help="Quality-control samples of records the LLM settled (not a recall estimate).",
                     no_args_is_help=True)
review_app.add_typer(qc_app, name="qc")
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")
PROTOCOL = typer.Argument(None, help="Protocol (default: the one the Screening page shows, else main).")
SET = typer.Option(None, "--set", help="Result set number (default: the protocol's active set).")
JSON = typer.Option(False, "--json", help="Print JSON.")
BY = typer.Option(None, "--by", help="Your name; defaults to $WIDER_REVIEWER (never the login name).")


def _signer(by: str | None) -> str:
    """Who signs a review decision or a QC draw: --by, else $WIDER_REVIEWER; never $USER, so a missing name is refused
    ("a reviewer name is needed") instead of signing with the computer's login name."""
    return (by or os.environ.get("WIDER_REVIEWER") or "").strip()[:journal.MAX_AUTHOR]


def _say(line: str) -> None:
    typer.echo(line)


def _run(fn, directory: Path):
    """Call the engine on the project; a refusal prints in red and exits with its code, an unknown set exits 1."""
    from rich import print as rprint

    p = Project.find(directory)
    try:
        return fn(p)
    except rv.Refusal as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(e.code) from None
    except KeyError as e:
        rprint(f"[red]{escape(str(e.args[0]) if e.args else str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()


@review_app.command("list")
def list_(protocol: str = PROTOCOL, result_set: int = SET,
          show: str = typer.Option("flagged", "--show", help="flagged | majority | qc | all"),
          as_json: bool = JSON, directory: Path = DIR):
    """The records that need a person and why (the page's list)."""
    res = _run(lambda p: rv.flagged(p, protocol, result_set, show), directory)
    if as_json:
        _say(json.dumps(res, indent=1, default=str))
        return
    _say(rv.SENTENCE)
    for line in rv.list_lines(res):
        _say(line)


@review_app.command("show")
def show(record: int = typer.Argument(..., help="Unique record id."), protocol: str = PROTOCOL, result_set: int = SET,
         as_json: bool = JSON, directory: Path = DIR):
    """One record: each pass's read, the LLM outcome and the final decision (the page's drawer)."""
    v = _run(lambda p: rv.record_view(p, protocol, result_set, record), directory)
    if as_json:
        _say(json.dumps(v, indent=1, default=str))
        return
    _say(rv.SENTENCE)
    for line in rv.view_lines(v):
        _say(line)


@review_app.command("decide")
def decide(record: int = typer.Argument(..., help="Unique record id."),
           decision: str = typer.Argument(..., help="include | exclude | maybe"),
           protocol: str = PROTOCOL, result_set: int = SET, by: str = BY,
           reason: str = typer.Option(None, "--reason", help="A note kept with the decision."),
           as_json: bool = JSON, directory: Path = DIR):
    """Decide a record after screening; journalled, undo it in History (`wider history undo ID`)."""
    who = _signer(by)
    v = _run(lambda p: rv.decide(p, protocol, result_set, record, decision, who, reason), directory)
    if as_json:
        _say(json.dumps(v, indent=1, default=str))
        return
    _say(f"record {v['record_id']}: {v['final_line']} (change {v['change_id']}; undo: wider history undo "
         f"{v['change_id']})")


@review_app.command("criteria")
def criteria(protocol: str = PROTOCOL, result_set: int = SET, as_json: bool = JSON, directory: Path = DIR):
    """The criteria the set was screened with: the codebook behind its prompt (the page's Criteria panel)."""
    c = _run(lambda p: rv.criteria(p, protocol, result_set), directory)
    if as_json:
        _say(json.dumps(c, indent=1, default=str))
        return
    for line in rv.criteria_lines(c):
        _say(line)


@qc_app.command("draw")
def qc_draw(protocol: str = PROTOCOL, result_set: int = SET,
            n: int = typer.Option(..., "--n", help="How many settled records to draw."),
            seed: int = typer.Option(None, "--seed", help="Random seed (default: a new one, printed)."),
            by: str = BY, as_json: bool = JSON, directory: Path = DIR):
    """Draw a seeded random sample of records the LLM settled, to check them by hand (`--show qc`)."""
    who = _signer(by)
    d = _run(lambda p: rv.qc_draw(p, protocol, result_set, n, seed, who), directory)
    if as_json:
        _say(json.dumps(d, indent=1, default=str))
        return
    _say(rv.QC_SENTENCE)
    _say(f"drew {d['n']} of {d['pool_n']} settled records of {d['protocol']} set {d['set']} (seed {d['seed']}; change "
         f"{d['change_id']}): {', '.join(map(str, d['records']))}")
    _say(f"check them: wider review list {d['protocol']} --set {d['set']} --show qc")
