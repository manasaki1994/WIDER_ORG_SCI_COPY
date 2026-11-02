"""`wider history ...`: the journal of manual changes, and undo. Attached in cli.py."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Column, Table

from .core import journal
from .core.project import Project
from .core.provenance import local_time

history_app = typer.Typer(help="Version history of manual changes (decisions, labels, notes, statuses, settings) and undo.",
                          no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")


@history_app.command("list")
def list_(kind: str = typer.Option(None, "--kind", help="Only one kind of change, e.g. label, review_decision, sample_lock, report_text, config or undo; `wider history kinds` lists them all."),
          record: int = typer.Option(None, "--record", help="Only changes to this unique record."),
          author: str = typer.Option(None, "--author", help="Only changes by this person."),
          limit: int = typer.Option(30, "--limit"),
          blind: bool = typer.Option(False, "--blind", help="Leave out which sample a labelled record is in, as the History page does while Blind is ticked."),
          directory: Path = DIR):
    """Changes newest first, with whether each can be undone now."""
    p = Project.find(directory)
    try:
        rows = journal.changes(p, kind, record, author, limit, hide_samples=blind)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    t = Table("id", "when", Column("who", overflow="fold"), "what", Column("change", overflow="fold"), "undo")
    for c in rows:
        state = f"undone by {c['undone_by']}" if c["undone_by"] else ("yes" if c["undoable"] else f"no: {c['why_not']}")
        t.add_row(str(c["change_id"]), local_time(c["at"]), escape((c["author"] or "") + (" (inferred, not confirmed)" if c.get("author_inferred") else "")),
                  c["kind_label"],
                  escape(c["summary"]), escape(state))
    rprint(t if rows else "no changes recorded yet")


@history_app.command("kinds")
def kinds_():
    """Every kind of change `history list --kind` takes, with the label the History page shows."""
    for k in journal.kinds():
        typer.echo(f"{k['kind']}\t{k['label']}")


def _shown(p, c: dict, blind: bool) -> dict:
    """With --blind, an undo or redo is printed without the sample a record is in (owner answer 37e6)."""
    if not blind:
        return c
    hide = journal.sample_name_hider(p)
    return {**c, **{k: hide(c[k]) for k in ("summary", "notice") if c.get(k)}}


@history_app.command("undo")
def undo(change_id: int = typer.Argument(None, help="Change to undo (default: your newest change not yet undone; "
                                                    "run again to go further back)."),
         author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER (never the login name)."),
         count_seen: bool = typer.Option(False, "--count-seen", help="Undoing the uploads-final mark after validation papers were shown: they count as seen (they no longer validate the search)."),
         other: bool = typer.Option(False, "--other", help="Undo a change signed with another name; the undo is signed with yours."),
         keep_validation: bool = typer.Option(False, "--keep-validation", help="Undoing the uploads-final mark after validation papers were shown: they still count as validation; every output states it."),
         blind: bool = typer.Option(False, "--blind", help="Leave out which sample a record is in, as the History page does while Blind is ticked."),
         directory: Path = DIR):
    """Undo one change; refused when what it touched has changed since. An undo can itself be undone (by its id)."""
    from .construct.artifacts import CHOOSE_LINE, SeenChoiceNeeded

    if count_seen and keep_validation:
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2)
    choice = "count" if count_seen else ("keep" if keep_validation else None)
    who = journal.named_author(author)   # never the login name; an unsigned undo by id is allowed, as on the page
    p = Project.find(directory)
    token = None if who else journal.AUTHOR.set("")   # no name: sign nothing, never $USER
    try:
        if change_id is None:
            if not who:
                raise ValueError("whose change? give --author or set $WIDER_REVIEWER")
            c = journal.undo_last(p, who, seen_choice=choice)
        else:
            c = journal.undo(p, change_id, author=who or None, seen_choice=choice, other=other)
        c = _shown(p, c, blind)
    except SeenChoiceNeeded as e:
        rprint(f"[yellow]{escape(e.notice)}[/yellow]")
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2) from None
    except (KeyError, ValueError) as e:
        why = str(e).strip(chr(39)) if isinstance(e, KeyError) else str(e)
        rprint(f"[red]{escape(journal.sample_name_hider(p)(why) if blind else why)}[/red]")   # risk-checker R2
        raise typer.Exit(1)
    finally:
        if token is not None:
            journal.AUTHOR.reset(token)
        p.close()
    rprint(f"change {c['change_id']}: {escape(c['summary'])}")
    if c.get("notice"):
        rprint(f"[yellow]{escape(c['notice'])}[/yellow]")


@history_app.command("redo")
def redo(author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER (never the login name)."),
         blind: bool = typer.Option(False, "--blind", help="Leave out which sample a record is in, as the History page does while Blind is ticked."),
         directory: Path = DIR):
    """Redo your newest undo (the page's Ctrl/Cmd+Shift+Z); run again to walk back through your undos."""
    who = journal.named_author(author)   # never the login name
    p = Project.find(directory)
    try:
        if not who:
            raise ValueError("whose change? give --author or set $WIDER_REVIEWER")
        c = _shown(p, journal.redo_last(p, who), blind)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(journal.sample_name_hider(p)(str(e)) if blind else str(e))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    rprint(f"change {c['change_id']}: {escape(c['summary'])}")
    if c.get("notice"):
        rprint(f"[yellow]{escape(c['notice'])}[/yellow]")
