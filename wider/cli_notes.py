"""`wider notes ...`: notes and tags on unique records, the same ones the record drawer shows. Attached in cli.py."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .core import annotations as ann
from .core.project import Project

notes_app = typer.Typer(help="Notes and tags on unique records (shared with the record drawer in the UI).", no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")
AUTHOR = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER.")


def _author(author: str | None) -> str:
    """The name as signed: the option's value, else the fallback of :func:`~wider.core.journal.current_author`, which
    records that it came from the environment (an inferred name, researcher-attribution D2)."""
    from .core.journal import current_author

    return current_author(author)


def _run(directory: Path, fn):
    p = Project.find(directory)
    try:
        return fn(p)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@notes_app.command("add")
def add(record_id: int = typer.Argument(..., help="Unique record id (as shown in the UI and exports)."),
        text: str = typer.Argument(..., help="The note."), author: str = AUTHOR, directory: Path = DIR):
    """Add a note to a record."""
    nid = _run(directory, lambda p: ann.add_note(p, record_id, _author(author), text))
    rprint(f"note {nid} added to record {record_id}")


@notes_app.command("remove")
def remove(note_id: int = typer.Argument(..., help="Note id (from `wider notes list`)."), author: str = AUTHOR, directory: Path = DIR):
    """Delete one of your notes."""
    ok = _run(directory, lambda p: ann.remove_note(p, note_id, _author(author)))
    if not ok:
        rprint(f"[red]no note {note_id} by {escape(_author(author))}[/red]")
        raise typer.Exit(1)
    rprint(f"note {note_id} removed")


@notes_app.command("tag")
def tag(record_id: int = typer.Argument(...), tags: list[str] = typer.Argument(..., help="One or more tags."),
        author: str = AUTHOR, directory: Path = DIR):
    """Tag a record (lower case; spaces become hyphens)."""
    done = _run(directory, lambda p: [ann.add_tag(p, record_id, _author(author), t) for t in tags])
    rprint(f"record {record_id}: {', '.join(dict.fromkeys(done))}")  # two spellings of one tag are echoed once


@notes_app.command("untag")
def untag(record_id: int = typer.Argument(...), tags: list[str] = typer.Argument(...), author: str = AUTHOR, directory: Path = DIR):
    """Remove your tags from a record."""
    n = _run(directory, lambda p: sum(ann.remove_tag(p, record_id, t, _author(author)) for t in tags))
    if not n:   # another reviewer's tag, or none at all: nothing of yours to remove
        rprint("[red]no such tag by this author[/red]")
        raise typer.Exit(1)
    rprint(f"{n} tag(s) removed from record {record_id}")


@notes_app.command("list")
def list_(q: str = typer.Option(None, "--search", "-s", help="Words that must appear in the title, a note or a tag."),
          tag_: str = typer.Option(None, "--tag", "-t", help="Only records with this tag."),
          author: str = typer.Option(None, "--author", "-a", help="Only notes and tags by this author."),
          directory: Path = DIR):
    """Records with notes or tags, with the tags in use."""
    def go(p):
        return ann.search(p, q, tag_, author), ann.all_tags(p)
    rows, tags = _run(directory, go)
    if tags:
        rprint("tags: " + ", ".join(f"{escape(t['tag'])} ({t['n']})" for t in tags))
    t = Table("record", "title", "tags", "notes")
    for r in rows:
        notes = "\n".join(f"[{n['note_id']}] {n['author']}: {n['text']}" for n in r["notes"])
        t.add_row(str(r["record_id"]), escape((r["title"] or "")[:70]), escape(", ".join(sorted({x["tag"] for x in r["tags"]}))), escape(notes))
    rprint(t if rows else "no notes or tags match")
