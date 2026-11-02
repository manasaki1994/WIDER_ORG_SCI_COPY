"""`wider construct adjacent ...`: the adjacent-literature loop on the command line. Attached to the construct sub-app in cli.py."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

from .core.project import Project

adjacent_app = typer.Typer(help="Adjacent literatures: name, propose, find in the ranking, approve or reject.", no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")


@adjacent_app.command("list")
def adjacent_list(directory: Path = DIR):
    """List the literatures of the newest adjacent artifact with origin and status."""
    from .construct.adjacent import summary

    p = Project.find(directory)
    s = summary(p)
    t = Table(title=f"adjacent v{s['row']['version']} ({s['row']['status']})" if s["row"] else "adjacent: no artifact yet")
    for col in ("key", "name", "origin", "status", "cue"):
        t.add_column(col)
    for e in s["literatures"]:
        t.add_row(e["key"], e["name"], e.get("origin", ""), e.get("status", ""), (e.get("discriminating_cue") or "")[:80])
    rprint(t)
    p.close()


@adjacent_app.command("add")
def adjacent_add(name: str = typer.Argument(..., help="Name of the adjacent literature."),
                 description: str = typer.Option("", "--description", help="What these studies examine, in their own words."),
                 differs: str = typer.Option("", "--differs", help="How it differs from the construct under review."),
                 cue: str = typer.Option("", "--cue", help="Discriminating cue a screener can check in an abstract."),
                 phrase: list[str] = typer.Option([], "--phrase", help="Example phrase (repeatable)."),
                 key: str | None = typer.Option(None, "--key", help="Key (lowercase identifier); derived from the name when omitted."),
                 proposed: bool = typer.Option(False, "--proposed", help="Save as proposed instead of approved."),
                 directory: Path = DIR):
    """Add a literature named by the researcher (approved unless --proposed)."""
    from .construct.adjacent import add

    p = Project.find(directory)
    row = add(p, name, description=description, differs_from_construct=differs, discriminating_cue=cue, example_phrases=list(phrase),
              key=key, status="proposed" if proposed else "approved")
    rprint(f"adjacent v{row['version']} saved")
    p.close()


@adjacent_app.command("edit")
def adjacent_edit(key: str = typer.Argument(..., help="Literature key."),
                  name: str | None = typer.Option(None, "--name", help="New name."),
                  description: str | None = typer.Option(None, "--description", help="What these studies examine."),
                  cue: str | None = typer.Option(None, "--cue", help="Discriminating cue."),
                  differs: str | None = typer.Option(None, "--differs", help="How it differs from the construct."),
                  directory: Path = DIR):
    """Edit a literature's name, description, cue or difference (the page's Edit dialog); options left out stay as they are."""
    from rich.markup import escape

    from .construct.adjacent import update_entry

    p = Project.find(directory)
    try:
        row = update_entry(p, key, name=name, description=description, discriminating_cue=cue, differs_from_construct=differs)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        p.close()
        raise typer.Exit(1)
    rprint(f"adjacent v{row['version']} saved")
    p.close()


@adjacent_app.command("seed")
def adjacent_seed(directory: Path = DIR):
    """Seed proposed literatures from the codebook's adjacent_literatures block."""
    from .construct.adjacent import seed_from_codebook

    p = Project.find(directory)
    rprint(seed_from_codebook(p))
    p.close()


@adjacent_app.command("propose")
def adjacent_propose(provider: str | None = typer.Option(None, "--provider", help="AI model (default: the construct stage provider, else the project default)."),
                     directory: Path = DIR):
    """Ask the LLM for adjacent literatures, grounded in the question and the development benchmark abstracts."""
    from rich.markup import escape

    from .cli_backend_consent import ensure_research
    from .construct.adjacent import load_list, propose_from_question
    from .construct.propose import ProposalError

    p = Project.find(directory)
    try:
        from .cli_lookups import say_sent
        say_sent(p, "construct", provider)
        ensure_research(p, provider, "construct")
        r = propose_from_question(p, provider_name=provider)
    except typer.Exit:   # consent declined (click's Exit is a RuntimeError): already reported, nothing sent
        raise
    except (ProposalError, KeyError, ValueError, RuntimeError) as e:   # failed call or missing context: nothing saved
        rprint(f"[red]{escape(str(e.args[0] if e.args else e))}[/red]")
        p.close()
        raise typer.Exit(1) from e
    if r["added"]:
        rprint(f"{r['proposed']} proposed, {len(r['added'])} added: adjacent v{r['version']} saved")
    else:
        rprint(f"{r['proposed']} proposed, nothing new added (all already listed); no new version saved")
    entries = {e["key"]: e for e in load_list(p)[1]}
    for key in r["added"]:
        e = entries.get(key, {"key": key, "name": key})
        rprint(f"- {escape(e['key'])}: {escape(e['name'])} — {escape(e.get('discriminating_cue') or '')}")
    p.close()


@adjacent_app.command("find")
def adjacent_find(ranking: str | None = typer.Option(None, "--ranking", help="Ranking name (default ranking when omitted)."),
                  provider: str | None = typer.Option(None, "--provider", help="AI model (default: the construct stage provider, else the project default)."),
                  k: int | None = typer.Option(None, "--k", help="Number of clusters (protocol default when omitted)."),
                  top: int | None = typer.Option(None, "--top", help="Cluster the top K of the ranking (protocol.ranking.cluster_top)."),
                  min_size: int = typer.Option(3, "--min-size", help="Smallest cluster sent to the model."),
                  max_clusters: int | None = typer.Option(None, "--max-clusters", help="Send at most this many benchmark-free clusters."),
                  no_merge: bool = typer.Option(False, "--no-merge", help="Report candidates without writing them to the artifact."),
                  directory: Path = DIR):
    """Name the literatures behind clusters of the top of the ranking that contain no benchmark paper (quotes required)."""
    from rich.markup import escape

    from .cli_backend_consent import ensure_research
    from .rank.adjacent import find_candidates

    p = Project.find(directory)
    try:
        from .cli_lookups import say_sent
        say_sent(p, "rank_adjacent", provider)
        ensure_research(p, provider, "construct")
        r = find_candidates(p, ranking, provider_name=provider, k=k, top=top, min_size=min_size, max_clusters=max_clusters,
                            merge=not no_merge)
    except ValueError as e:      # e.g. a backend that cannot write text (Jev): nothing is sent
        rprint(f"[red]{escape(str(e))}[/red]")
        p.close()
        raise typer.Exit(1) from e
    rprint({k2: v for k2, v in r.items() if k2 not in ("candidates", "skipped", "calls")})
    for e in r["candidates"]:
        rprint(f"- cluster {e['evidence']['cluster']}: {e['name']} — {e.get('discriminating_cue', '')}")
    rprint(f"{len(r['skipped'])} clusters skipped (not adjacent, already listed or quotes not verified)")
    p.close()


def _set(directory: Path, key: str, status: str):
    from .construct.adjacent import set_status

    p = Project.find(directory)
    row = set_status(p, key, status)
    rprint(f"{key}: {status} (adjacent v{row['version']})")
    p.close()


@adjacent_app.command("approve")
def adjacent_approve(key: str = typer.Argument(..., help="Literature key."), directory: Path = DIR):
    """Approve a literature: it then draws marginal cases and becomes a screening label value."""
    _set(directory, key, "approved")


@adjacent_app.command("reject")
def adjacent_reject(key: str = typer.Argument(..., help="Literature key."), directory: Path = DIR):
    """Reject a literature (kept in the artifact for the record, used nowhere)."""
    _set(directory, key, "rejected")


def print_adjacent_table(ev: dict) -> None:
    """Per-literature table of a development evaluation (nothing printed when no record is tied to a literature)."""
    if not ev.get("by_adjacent"):
        return
    t = Table(title="By adjacent literature")
    for col in ("literature", "n", "drawn", "model-labelled", "MISS", "FALSE INCLUDE", "FALSE MAYBE", "agree"):
        t.add_column(col)
    for k, v in ev["by_adjacent"].items():
        t.add_row(k, *[str(v.get(c, 0)) for c in ("n", "n_drawn", "n_model", "MISS", "FALSE INCLUDE", "FALSE MAYBE", "agree")])
    rprint(t)
