"""`wider lookups ...`: the per-project consent for online lookups and the local log of outside requests.
Attached in cli.py. :func:`ensure_consent` is the shared check of every lookup command (seed resolve, seed abstracts,
enrichment): a y/N prompt in a terminal, otherwise exit code 2 with the `wider lookups allow` hint."""
from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .core import lookups
from .core.project import Project

lookups_app = typer.Typer(help="Online lookups: consent (off by default) and the log of outside requests.",
                          no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")


def _interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def say_sent(p: Project, action: str, backend: str | None = None, quiet: bool = False) -> None:
    """Print what the action sends, where, why and the consent state, before it starts (ui-redesign D17): the
    sentence the page shows beside the button (``GET /api/send-notice/<action>``). ``quiet`` for a command printing
    JSON, whose output stays one JSON document (the route gives the sentence)."""
    from .core.send_notice import sentence

    if quiet:
        return
    rprint(f"[dim]{escape(sentence(p, action, backend))}[/dim]")


def ensure_consent(p: Project, purpose: str, n_items: int | None = None, hint: str | None = None,
                   quiet: bool = False) -> None:
    """Print what the lookup sends (:func:`say_sent`), then return when the project allows online lookups. Otherwise
    ask once (y/N) in a terminal, storing consent on yes; any other answer, or a run without a terminal, sends nothing
    and exits with code 2. ``hint`` is printed on refusal; ``quiet`` for a command printing JSON."""
    say_sent(p, purpose, quiet=quiet)
    try:
        lookups.require_consent(p, purpose)
        return
    except lookups.ConsentRequired as e:
        err = e
    if _interactive() and isinstance(err, lookups.TermsRequired):
        what = ("the query and the DOI of each Development seed paper (never a Validation paper)"
                if purpose == "query_seed_check" else "search terms or a query")
        rprint(f"To {lookups.PURPOSE_LABELS[purpose]}, WIDER sends {what} to "
               f"{escape(', '.join(err.services))}. Nothing else leaves this computer. {escape(err.note)}".rstrip())
        if typer.confirm("Allow search-term lookups for this project?", default=False):
            lookups.allow_terms(p)
            rprint("[green]search-term lookups allowed for this project (`wider lookups terms revoke` turns them "
                   "off)[/green]")
            return
        rprint("[yellow]nothing was sent; search-term lookups stay off[/yellow]")
    elif _interactive():
        n = f" for {n_items} paper{'s' if n_items != 1 else ''}" if n_items is not None else ""
        what = (f"the {escape(', '.join(err.fields))} of each paper" if purpose != "query_seed_check" else
                "the DOI of each Development seed paper (never a Validation paper) with the query")
        rprint(f"To {lookups.PURPOSE_LABELS[purpose]}{n}, WIDER sends {what} "
               f"to {escape(', '.join(err.services))}. Nothing else leaves this computer. "
               f"{escape(getattr(err, 'note', ''))}".rstrip())
        if typer.confirm("Allow online lookups for this project?", default=False):
            lookups.allow(p)
            rprint("[green]online lookups allowed for this project (`wider lookups revoke` turns them off)[/green]")
            return
        rprint("[yellow]nothing was sent; online lookups stay off[/yellow]")
    else:
        rprint(f"[red]{escape(str(err))}[/red]")
    if hint:
        rprint(f"[dim]{escape(hint)}[/dim]")
    p.close()
    raise typer.Exit(2)


@lookups_app.command("status")
def lookups_status(directory: Path = DIR):
    """Whether online lookups are allowed, since when, the services and the fields sent."""
    p = Project.find(directory)
    s = lookups.status(p)
    if s["allowed"]:
        by = f" by {s['allowed_by']}" if s.get("allowed_by") else ""
        rprint(f"[green]online lookups: on[/green] (since {escape(str(s['allowed_at'] or '?'))}{escape(by)})")
    else:
        rprint("online lookups: [yellow]off[/yellow] (allow with `wider lookups allow`)")
    rprint(f"services: {escape(', '.join(s['services']))}")
    rprint(f"fields sent per paper: {escape(', '.join(s['fields']))}")
    for k, v in s["purposes"].items():
        if k not in lookups.PRIOR_PURPOSES and k not in lookups.TERM_PURPOSES:
            rprint(f"  {escape(v['label'])}: {escape(', '.join(v['services']))}")
    if s["terms_allowed"]:
        by = f" by {s['terms_allowed_by']}" if s.get("terms_allowed_by") else ""
        rprint(f"search-term lookups (separate): [green]on[/green] (since {escape(str(s['terms_allowed_at'] or '?'))}"
               f"{escape(by)}); search terms or a query to OpenAlex, to count the works they find")
    else:
        rprint("search-term lookups (separate): [yellow]off[/yellow] (allow with `wider lookups terms allow`)")
    rprint(f"cached responses: {s['cached_responses']} (`wider lookups cache --clear` empties them)")
    rprint(f"prior-review lookup switch (separate): {'on' if s['prior_review_lookup'] else 'off'} "
           "(change with `wider prior-reviews policy`)")
    sp = s["seed_passages"]
    rprint(f"seed-paper passages to a backend that is not local (separate): {'on' if sp['passages_to_cloud'] else 'off'}"
           f" ({escape(sp['lets_out'])}; change with `wider seeds policy`)")
    p.close()


@lookups_app.command("allow")
def lookups_allow(directory: Path = DIR):
    """Allow online lookups for this project (stored in project.yaml)."""
    p = Project.find(directory)
    try:
        lookups.allow(p)
    except OSError as e:
        rprint(f"[red]{escape(lookups.not_stored_message(e))}[/red]")
        p.close()
        raise typer.Exit(1)
    s = lookups.status(p)
    rprint(f"[green]online lookups allowed[/green] (since {escape(str(s['allowed_at']))}); WIDER may send the "
           f"{escape(', '.join(s['fields']))} of each paper to {escape(', '.join(s['services']))}")
    p.close()


@lookups_app.command("revoke")
def lookups_revoke(directory: Path = DIR):
    """Withdraw the consent; the next lookup asks again."""
    p = Project.find(directory)
    lookups.revoke(p)
    rprint("online lookups: [yellow]off[/yellow]; nothing is sent until you allow them again")
    p.close()


terms_app = typer.Typer(help="Search-term lookups: a switch of their own (off by default), separate from the DOI "
                        "consent. On, a yield count sends search terms or a query to OpenAlex.", no_args_is_help=True)
lookups_app.add_typer(terms_app, name="terms")


@terms_app.command("allow")
def terms_allow(directory: Path = DIR):
    """Allow search terms or a query to be sent to OpenAlex (yield counts); the DOI consent is not changed."""
    p = Project.find(directory)
    try:
        lookups.allow_terms(p)
    except OSError as e:
        rprint(f"[red]{escape(lookups.not_stored_message(e))}[/red]")
        p.close()
        raise typer.Exit(1)
    s = lookups.status(p)
    rprint(f"[green]search-term lookups allowed[/green] (since {escape(str(s['terms_allowed_at']))}); a yield "
           "count sends search terms or a query to OpenAlex")
    p.close()


@terms_app.command("revoke")
def terms_revoke(directory: Path = DIR):
    """Turn search-term lookups off; the DOI consent is not changed."""
    p = Project.find(directory)
    lookups.revoke_terms(p)
    rprint("search-term lookups: [yellow]off[/yellow]; no search term or query is sent until you allow them again")
    p.close()


@lookups_app.command("cache")
def lookups_cache(clear: bool = typer.Option(False, "--clear", help="Remove every stored answer; the next run asks "
                                             "OpenAlex again."), directory: Path = DIR):
    """The stored answers of seed chaining and term yield counts (a repeated request is answered from here and sends
    nothing); --clear empties the store."""
    p = Project.find(directory)
    if clear:
        n = lookups.clear_cache(p)
        rprint(f"lookup cache cleared: {n} stored answer{'s' if n != 1 else ''} removed; the next run requests again")
    else:
        rprint(f"cached responses: {lookups.cache_count(p)} (seed chaining and term yield counts only; clear with "
               "`wider lookups cache --clear`)")
    p.close()


@lookups_app.command("log")
def lookups_log(limit: int = typer.Option(50, "--limit", "-n", min=1, help="Number of entries, newest first."),
                directory: Path = DIR):
    """The newest outside requests: time, service, purpose, papers sent and failed requests."""
    p = Project.find(directory)
    rows = lookups.log(p, limit)
    p.close()
    if not rows:
        rprint("no outside request has been logged in this project")
        return
    t = Table("time", "service", "purpose", "items", "failed")
    for r in rows:
        t.add_row(escape(str(r["at"])), escape(r["service_label"]), escape(str(r["purpose"])), str(r["n_items"]),
                  str(r["n_failed"]))
    rprint(t)
