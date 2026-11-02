"""``wider query indexing list|set``: the indexing check per missed seed paper (vision change 2a, D10, task 2.10).

``register(query_app)`` is called once from ``cli_refine.register``. The route twin is ``wider/ui/routes_indexing.py``;
both call :mod:`wider.search.indexing`.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

DIR_OPT = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")


def print_indexing(res: dict) -> None:
    """The known-item strings per platform with each missed seed's verdict and diagnosis."""
    from rich.table import Table

    rprint(escape(res["note"]))
    for key, pl in res["platforms"].items():
        head = f"{pl['label']} ({key}): {pl['files']} file(s), {pl['retrieved']} seed(s) retrieved, {pl['missed']} missed"
        rprint(f"[bold]{escape(head)}[/bold]")
        if pl["note"]:
            rprint(f"[yellow]{escape(pl['note'])}[/yellow]")
        if not pl["seeds"]:
            continue
        if pl["template_note"]:
            rprint(escape(f"template ({'checked' if pl['verified'] else 'not checked by hand yet'}): {pl['template_note']}"))
        t = Table("seed", "set", "known-item string", "verdict", "diagnosis")
        for s in pl["seeds"]:
            alt = f"\nor {s['alternative']}" if s["alternative"] else ""
            t.add_row(s["benchmark_id"], s["set"], escape((s["string"] or "(search the title by hand)") + alt),
                      s["verdict"] or "-", s["diagnosis"])
        rprint(t)
    rprint("Paste a string into the database's own search; then: wider query indexing set <seed> <platform> "
           "indexed|not_indexed|unknown. Nothing is searched from here.")


def register(query_app: typer.Typer) -> None:
    indexing_app = typer.Typer(help="Indexing check: is a missed seed paper in the database at all?",
                               no_args_is_help=True)
    query_app.add_typer(indexing_app, name="indexing")

    @indexing_app.command("list")
    def indexing_list(version: int = typer.Option(None, "--version", help="Only exports imported for this synonyms "
                                                                          "version (default: every export)."),
                      platform: str = typer.Option(None, "--platform", help="One platform (default: every one)."),
                      directory: Path = DIR_OPT):
        """The seed papers each platform did not retrieve, with the known-item string to paste and the verdict."""
        from .core.project import Project
        from .search import indexing

        p = Project.find(directory)
        try:
            res = indexing.known_item_strings(p, version, platform)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        print_indexing(res)

    @indexing_app.command("set")
    def indexing_set(benchmark_id: str = typer.Argument(..., help="The seed paper, e.g. B002."),
                     platform: str = typer.Argument(..., help="The platform key, e.g. scopus."),
                     verdict: str = typer.Argument(..., help="indexed, not_indexed or unknown."),
                     by: str = typer.Option(None, "--by", help="Your name (default: WIDER_REVIEWER or the login)."),
                     directory: Path = DIR_OPT):
        """Record whether the database holds a seed paper it did not retrieve (journalled, undoable)."""
        from .core.project import Project
        from .search import indexing

        p = Project.find(directory)
        try:
            res = indexing.set_verdict(p, benchmark_id, platform, verdict, by)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        if res["already"]:
            rprint(f"{res['benchmark_id']} on {platform} is already {verdict}; nothing changed")
        else:
            rprint(f"[green]{res['benchmark_id']} on {platform}: {verdict} ({res['diagnosis']}); change "
                   f"{res['change_id']}[/green]")
