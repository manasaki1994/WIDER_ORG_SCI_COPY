"""`wider blinding ...`: review packages for a blinded second reviewer (reviewer-roles-and-blinding), the twin of the
"Review packages" card on Sampling and labelling. Attached in cli.py."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

from .core.project import Project
from .core.provenance import local_time
from .sample import package as pk

blinding_app = typer.Typer(help="Review packages: one self-contained page per reviewer that holds the records and the "
                                "criteria and no other judgment; import the reviewer's decisions and resolve "
                                "disagreements.", no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")
JSON = typer.Option(False, "--json", help="Print JSON.")
BY = typer.Option(None, "--by", help="Your name for History; defaults to $WIDER_REVIEWER (never the login name).")


def _named(by: str | None) -> str:
    from .cli import _named as named

    return named(by)


def _run(fn, directory: Path):
    """Call the engine on the project; a refusal prints in red and exits with code 1."""
    p = Project.find(directory)
    try:
        return fn(p)
    except (ValueError, KeyError) as e:
        msg = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
        rprint(f"[red]{escape(msg)}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()


@blinding_app.command("package")
def package_cmd(reviewer: str = typer.Argument(..., help="The reviewer's name (written in the file)."),
                sample: list[str] = typer.Option(..., "--sample", "-s", help="A sample (repeat for several of one kind)."),
                mode: str = typer.Option(..., "--mode", help="second (the first reviewer's includes and maybes and as "
                                                             "many excludes) or independent (every record)."),
                earlier: str = typer.Option(None, "--earlier", help="retain | re-review | exclude: what happens to the "
                                                                    "reviewer's labels not made in a review package."),
                codebook_version: int = typer.Option(None, "--codebook-version", help="The criteria version the page "
                                                                                       "carries, when the first labels used several."),
                out: Path = typer.Option(..., "--out", "-o", help="Where to write the page (a copy stays in the project)."),
                by: str = BY, directory: Path = DIR):
    """Make a review package: says once what the file holds and that it leaves the computer when sent, then writes it."""
    who = _named(by)
    res = _run(lambda p: pk.make(p, reviewer, sample, mode, earlier, codebook_version, out, by=who), directory)
    typer.echo(res["leaving"])
    for line in res["notes"]:
        typer.echo(line)
    typer.echo(f"Review package {res['package_id']} for {res['reviewer']}: {res['n_items']} records.")
    typer.echo(f"wrote {out}")


@blinding_app.command("package-file")
def package_file_cmd(package_id: str = typer.Argument(..., help="Package id (wider blinding status)."),
                     out: Path = typer.Option(..., "--out", "-o", help="Where to write the copy."), directory: Path = DIR):
    """Write the kept copy of a package's page again, byte for byte."""
    _run(lambda p: pk.package_file(p, package_id, out), directory)
    typer.echo(f"wrote {out}")


@blinding_app.command("import")
def import_cmd(file: Path = typer.Argument(..., help="The reviewer's decisions file (wider-decisions-*.json)."),
               as_json: bool = JSON, by: str = BY, directory: Path = DIR):
    """Import a reviewer's decisions into the package that made it (one undoable change)."""
    who = _named(by)
    res = _run(lambda p: pk.import_file(p, file, by=who), directory)
    if as_json:
        typer.echo(json.dumps({k: v for k, v in res.items() if k != "change_id"}, ensure_ascii=False))
        return
    for line in res["lines"]:
        typer.echo(line)


def status_lines(st: dict) -> list[str]:
    out = list(st["texts"])
    if not st["packages"]:
        out.append("No review package yet.")
    for r in st["packages"]:
        out.append(f"{r['package_id']}  {r['reviewer']}  {r['mode']}  {', '.join(r['samples'])}  {r['n_items']} items  "
                   f"codebook version {r['codebook_version']}  made {local_time(r['made_at'])}  {r['state']}")
        out += [f"  {line}" for line in r["lines"]]
    return out


@blinding_app.command("status")
def status_cmd(as_json: bool = JSON, directory: Path = DIR):
    """Every review package with reviewer, mode, samples, items, criteria version and its state."""
    st = _run(pk.status, directory)
    if as_json:
        typer.echo(json.dumps(st, ensure_ascii=False, indent=1))
        return
    for line in status_lines(st):
        typer.echo(line)


@blinding_app.command("withdraw")
def withdraw_cmd(package_id: str = typer.Argument(..., help="Package id."),
                 reason: str = typer.Option("", "--reason", help="Why (required; recorded in History)."),
                 by: str = BY, directory: Path = DIR):
    """Withdraw a package (undoable); its decisions are refused from then on."""
    who = _named(by)
    res = _run(lambda p: pk.withdraw(p, package_id, reason, by=who), directory)
    typer.echo(f"Review package {res['package_id']} withdrawn: {res['withdraw_reason']}")


def disagreement_lines(d: dict) -> list[str]:
    out = [f"{len(d['rows'])} record{'s' if len(d['rows']) != 1 else ''} where {d['a']} and {d['b']} differ on {d['sample']}"]
    for r in d["rows"]:
        out.append(f"record {r['record_id']}: {r['title'] or ''}")
        for who, side in ((d["a"], r["a"]), (d["b"], r["b"])):
            ticks = ", ".join(f"{k} {v}" for k, v in (side["ticks"] or {}).items())
            out.append(f"  {who}: {side['decision']}" + (f", reason {side['reason']}" if side["reason"] else "")
                       + (f", ticks {ticks}" if ticks else "") + (f", tags {', '.join(side['tags'])}" if side["tags"] else "")
                       + (f", note {side['note']}" if side["note"] else "")
                       + f"; made in {side['made_in']}, blinded {side['blinded']}"
                       + ("; left out" if side["left_out"] else "") + f"; abstract {side['abstract']}")
        if r["different_abstracts"]:
            out.append(f"  {r['different_abstracts']}")
        if r["adjudicated"]:
            out.append(f"  resolved: {r['adjudicated']['decision']} by {r['adjudicated']['decided_by']}")
    return out


@blinding_app.command("disagreements")
def disagreements_cmd(sample: str = typer.Argument(..., help="Sample name."),
                      a: str = typer.Option(..., "--a", help="First reviewer."),
                      b: str = typer.Option(..., "--b", help="Second reviewer."),
                      all_: bool = typer.Option(False, "--all", help="Every record both labelled, not only disagreements."),
                      as_json: bool = JSON, directory: Path = DIR):
    """The records where two reviewers' labels differ, with each label's details and abstract version; no model
    output. Resolve one with `wider label adjudicate`."""
    d = _run(lambda p: pk.disagreements(p, sample, a, b, all_), directory)
    if as_json:
        typer.echo(json.dumps(d, ensure_ascii=False, indent=1))
        return
    for line in disagreement_lines(d):
        typer.echo(line)
