"""Consent before contacting a network, cloud or unknown backend (design Decision 17): `wider providers
consent|allow|revoke` and `wider embed consent|allow|revoke`, attached in cli.py. :func:`ensure_backend_consent` is the
shared check of every command that contacts such a backend: a y/N prompt in a terminal, otherwise exit code 2 with the
`allow` hint; nothing is sent either way until consent is given. For Ask WIDER (operation ``assistant``) a yes is
followed by "Remember this choice? [Y/n]", and `--allow-once` allows one run without storing anything (guide-assistant
D8)."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .cli_lookups import _interactive
from .core import backend_consent as bc
from .core.project import Project

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")


def ensure_backend_consent(p: Project, cfg, operation: str, embedding: bool = False, once: bool = False) -> dict:
    """Return the assessment when ``cfg`` may be contacted for ``operation``. Otherwise ask once (y/N) in a terminal,
    storing consent on yes; any other answer, or a run without a terminal, sends nothing and exits with code 2.

    For ``assistant`` only: ``once`` (`--allow-once`) allows this run without asking or storing, and in a terminal a
    yes is followed by "Remember this choice? [Y/n]", where "n" allows this run only. Either way the result carries
    ``once: True`` and the ``fingerprint``, for :func:`wider.core.backend_consent.allow_once`."""
    try:
        return bc.require_backend_consent(p, cfg, operation, embedding)
    except bc.BackendConsentRequired as e:
        err = e
    if operation == "assistant" and once:
        return {"needs": True, "once": True, "fingerprint": err.fingerprint}
    if _interactive():
        rprint(f"[bold]{escape(cfg.name)}[/bold]: destination {escape(err.destination)}")
        rprint(f"To {bc.OPERATION_LABELS[operation]}, WIDER sends {escape(err.sent)}.")
        if err.changed:
            rprint(f"[yellow]Consent was given before {escape('; '.join(err.changed))}.[/yellow]")
        if err.warning:
            rprint(f"[yellow]Warning: {escape(err.warning)}.[/yellow]")
        if operation == "passages":
            rprint("WIDER remembers your choice for this backend in this project and does not ask again; change it "
                   f"in Settings or with `wider providers revoke {escape(cfg.name)} --for passages`.")
        if typer.confirm(f"Allow {cfg.name} to {bc.OPERATION_LABELS[operation]}?", default=False):
            if operation == "assistant" and not typer.confirm("Remember this choice?", default=True):
                rprint("[green]allowed for this question only; nothing is stored[/green]")
                return {"needs": True, "once": True, "fingerprint": err.fingerprint}
            bc.allow_backend(p, cfg.name, [operation], embedding=embedding)
            group = "embed" if embedding else "providers"
            rprint(f"[green]allowed (`wider {group} revoke {escape(cfg.name)}` withdraws it)[/green]")
            return bc.require_backend_consent(p, cfg, operation, embedding)
        rprint("[yellow]nothing was sent[/yellow]")
    else:
        rprint(f"[red]{escape(str(err))}[/red]")
        if operation == "assistant":
            rprint("Or allow this question only, storing nothing: add `--allow-once`.")
    p.close()
    raise typer.Exit(2)


def ensure_research(p: Project, provider_name: str | None, stage: str) -> None:
    """The research check of a run command, before any run is created: the named backend, else the stage's."""
    try:
        cfg = p.provider(provider_name) if provider_name else p.provider_for(stage)
    except (KeyError, TypeError):
        return  # the run itself reports the unknown backend
    if stage == "construct":
        from .providers.registry import require_text

        require_text(cfg)  # a backend that cannot write text is refused (ValueError) before consent is asked
    ensure_backend_consent(p, cfg, "research")


def ensure_embedding_research(p: Project, name: str | None) -> None:
    """The research check of a command that embeds project texts (``name``: the embedding backend, else the default)."""
    try:
        cfg = p.embedding(name)
    except (KeyError, TypeError):
        return
    ensure_backend_consent(p, cfg, "research", embedding=True)


def _print(rows: list[dict], as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    t = Table("backend", "where", "destination", "consent", "note")
    for r in rows:
        if r["needs_consent"] is False:
            consent = "not needed"
        else:
            consent = ", ".join(r["operations"]) or "none"
        if r.get("verified") is False:
            consent += " (not verified, press Check)"
        note = "; ".join(r["changed"]) if r["stale"] else (r.get("warning") or "")
        t.add_row(r["name"], str(r.get("locality_label") or r["locality"] or "?"), str(r["destination"] or ""),
                  consent, note)
    rprint(t)


def _register(app: typer.Typer, embedding: bool) -> None:
    ops = bc._ops(embedding)
    what = "embedding backend" if embedding else "AI model"

    @app.command("consent")
    def consent(name: str = typer.Argument(None, help=f"{what.capitalize()} name; default: all."),
                as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
                directory: Path = DIR):
        """Which backends need consent before they are contacted, and for which operations they have it. Contacts
        nothing: an Ollama backend never checked reads "unknown, press Check" (`probe` checks it)."""
        p = Project.find(directory)
        try:
            rows = bc.status(p, name, embedding=embedding, probe=False)
        except KeyError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        _print(rows, as_json)

    @app.command("allow")
    def allow(name: str = typer.Argument(..., help=f"{what.capitalize()} name."),
              operation: list[str] = typer.Option(..., "--for", help=f"Operation: {'|'.join(ops)} (repeatable)."),
              directory: Path = DIR):
        """Allow WIDER to contact a network, cloud or unknown backend for the named operations."""
        p = Project.find(directory)
        try:
            row = bc.allow_backend(p, name, operation, embedding=embedding)
        except (KeyError, ValueError) as e:
            rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"[green]{escape(name)}: allowed for {escape(', '.join(row['operations']))}[/green] "
               f"(destination {escape(row['destination'])})")

    @app.command("revoke")
    def revoke(name: str = typer.Argument(..., help=f"{what.capitalize()} name."),
               operation: list[str] = typer.Option(None, "--for", help=f"Operation: {'|'.join(ops)}; default: all."),
               directory: Path = DIR):
        """Withdraw consent; the backend is not contacted again until it is allowed."""
        p = Project.find(directory)
        try:
            bc.revoke_backend(p, name, operation or None, embedding=embedding)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        rprint(f"{escape(name)}: consent withdrawn" + (f" for {escape(', '.join(operation))}" if operation else ""))


def register(providers_app: typer.Typer, embed_app: typer.Typer) -> None:
    _register(providers_app, embedding=False)
    _register(embed_app, embedding=True)
