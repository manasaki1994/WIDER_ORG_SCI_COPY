"""`wider researchers ...`: the researcher list, confirming and joining names, and how shared files write them (the
card "Names in shared files" on the Recall page; researcher-attribution)."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Column, Table

from .core import researchers as rs
from .core.project import Project
from .core.provenance import local_time

researchers_app = typer.Typer(help="Researchers who made changes, their pseudonyms, and names in shared files.",
                              no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")
BY = typer.Option(None, "--by", help="Your name; defaults to $WIDER_REVIEWER, then $USER.")


def _run(directory: Path, fn):
    p = Project.find(directory)
    try:
        return fn(p)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@researchers_app.command("list")
def list_(directory: Path = DIR, as_json: bool = typer.Option(False, "--json", help="Print JSON.")):
    """Every name recorded as an author, with its pseudonym, declared or inferred, and where the name came from."""
    p = Project.find(directory)
    try:
        rows, mode = rs.entries(p), rs.mode_of(p)
    finally:
        p.close()
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    rprint(escape(rs.MODE_LINES[mode]))
    t = Table(Column("name", overflow="fold"), "pseudonym", "status", Column("source", overflow="fold"), "first seen",
              Column("joined into", overflow="fold"))
    for r in rows:
        t.add_row(escape(r["name"]), r["pseudonym"], r["status"], escape(r["source"]), local_time(r["first_seen"]),
                  escape(r["joined_into"] or ""))
    rprint(t if rows else "no researcher recorded yet")
    print(rs.NOT_VERIFIED)


@researchers_app.command("confirm")
def confirm(name: str = typer.Argument(..., help="The inferred name to confirm."), by: str = BY,
            directory: Path = DIR):
    """Confirm an inferred name: it becomes declared (one History change, undoable)."""
    rprint(escape(_run(directory, lambda p: rs.confirm(p, name, by))))


@researchers_app.command("join")
def join(name: str = typer.Argument(..., help="The name to join."),
         into: str = typer.Option(..., "--into", help="The name it is the same researcher as."), by: str = BY,
         directory: Path = DIR):
    """Say two names are one researcher: NAME then shares the pseudonym and status of --into (undoable)."""
    rprint(escape(_run(directory, lambda p: rs.join(p, name, into, by))))


@researchers_app.command("attribution")
def attribution(mode: str = typer.Argument(..., help="pseudonyms | names | none"), directory: Path = DIR):
    """How shared files write researcher names: pseudonyms (default), names (declared names; inferred names stay
    pseudonyms) or none."""
    rprint(escape(_run(directory, lambda p: rs.set_attribution(p, mode))))
