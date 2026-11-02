"""`wider prior-reviews ...`: previous or relevant reviews on the command line. Attached in cli.py."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape
from rich.table import Table

from .core.project import Project
from .core.provenance import local_time

prior_app = typer.Typer(help="Prior reviews: add reference lists or PDFs (parsed locally), check coverage, chain citations.",
                        no_args_is_help=True)

DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")

POLICY_NOTE = ("Prior-review files stay on this computer: they are parsed and matched locally and are never uploaded or "
               "sent to any model or service. 'allow lookup' and 'allow excerpts' are off unless you turn them on.")


def _policy_line(pol: dict) -> str:
    sw = lambda b: "on" if b else "off"  # noqa: E731
    return (f"policy: OpenAlex lookup {sw(pol['allow_openalex_lookup'])}, LLM excerpts {sw(pol['allow_llm_excerpts'])}, "
            f"none known: {'yes' if pol['none_known'] else 'no'}")


def _exit_policy(e: Exception) -> None:
    rprint(f"[red]{escape(str(e))}[/red]")
    raise typer.Exit(2)


@prior_app.command("add")
def prior_add(
    file: Path | None = typer.Argument(None, help="Reference export (RIS/CSV/TSV/XLSX/BibTeX), .txt list, or the review's PDF."),
    text: str | None = typer.Option(None, "--text", "-t", help="Pasted DOIs, titles or reference strings (one per line) instead of a file."),
    label: str | None = typer.Option(None, "--label", "-l", help="Short name of the review (defaults to the file name)."),
    directory: Path = DIR,
):
    """Add a prior review from a file or pasted text. The file is copied into prior_reviews/ and parsed locally."""
    from .prior import reviews

    if (file is None) == (text is None):
        rprint("[red]give a file or --text, not both[/red]")
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        res = reviews.add_file(p, file, label=label) if file is not None else reviews.add_text(p, text, label or "pasted list")
    except (ValueError, FileNotFoundError) as e:
        p.close()
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]review #{res['review_id']} '{escape(res['label'])}' ({res['kind']}): {res['n_papers']} papers, "
           f"{res['n_doi']} with a DOI[/green]")
    for w in res.get("warnings") or []:
        rprint(f"[yellow]warning: {escape(w)}[/yellow]")
    rprint(f"[dim]{POLICY_NOTE}[/dim]")
    p.close()


@prior_app.command("list")
def prior_list(directory: Path = DIR):
    """List the prior reviews with paper counts and the data policy."""
    from .prior import reviews

    p = Project.find(directory)
    rows = reviews.list_reviews(p)
    s = reviews.summary(p)
    t = Table(title=f"prior reviews: {s['n_reviews']} reviews, {s['n_papers']} papers, {s['n_doi']} with a DOI")
    for col in ("id", "label", "kind", "file", "papers", "doi", "resolved", "matched", "added"):
        t.add_column(col)
    for r in rows:
        t.add_row(str(r["review_id"]), r["label"], r["kind"], r["file_name"] or "", str(r["n_papers"]), str(r["n_doi"]),
                  str(r["n_resolved"]), str(r["n_matched"]), local_time(r["added_at"]))
    rprint(t)
    rprint(_policy_line(s["policy"]))
    p.close()


@prior_app.command("papers")
def prior_papers(review_id: int = typer.Argument(..., help="Review id from `wider prior-reviews list`."), directory: Path = DIR):
    """Print the papers parsed from one review (position, confidence, year, DOI, title)."""
    from .prior import reviews

    p = Project.find(directory)
    try:
        review = reviews.get(p, review_id)
    except KeyError as e:
        p.close()
        rprint(f"[red]{escape(str(e.args[0]))}[/red]")
        raise typer.Exit(1) from e
    t = Table(title=f"#{review_id} {review['label']}: {review['n_papers']} papers")
    for col in ("#", "confidence", "year", "doi", "title"):
        t.add_column(col)
    for r in reviews.papers(p, review_id):
        t.add_row(str(r["position"]), r["confidence"], str(r["year"] or ""), r["doi"] or "", (r["title"] or r["raw_ref"] or "")[:90])
    rprint(t)
    p.close()


@prior_app.command("remove")
def prior_remove(review_id: int = typer.Argument(..., help="Review id from `wider prior-reviews list`."), directory: Path = DIR):
    """Remove a prior review, its papers, matches and chained records; deletes the copied file."""
    from .prior import reviews

    p = Project.find(directory)
    try:
        reviews.remove(p, review_id)
    except KeyError as e:
        p.close()
        rprint(f"[red]{escape(str(e.args[0]))}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]removed review #{review_id}[/green]")
    p.close()


@prior_app.command("policy")
def prior_policy(
    allow_lookup: bool | None = typer.Option(None, "--allow-lookup/--no-allow-lookup",
                                             help="Send DOIs/titles of prior-review papers to OpenAlex (resolve, chain)."),
    allow_excerpts: bool | None = typer.Option(None, "--allow-excerpts/--no-allow-excerpts",
                                               help="Let titles of prior-review papers appear in construct proposal prompts."),
    none_known: bool | None = typer.Option(None, "--none-known/--some-known", help="Record that no prior review is known."),
    max_citing: int | None = typer.Option(None, "--max-citing", help="Cap on citing works per paper for forward chaining."),
    directory: Path = DIR,
):
    """Show or change the data-policy switches (both lookups default to off)."""
    from .prior import reviews

    p = Project.find(directory)
    pol = reviews.set_policy(p, allow_openalex_lookup=allow_lookup, allow_llm_excerpts=allow_excerpts,
                             none_known=none_known, max_citing_per_paper=max_citing)
    rprint(_policy_line(pol))
    rprint(f"[dim]{POLICY_NOTE}[/dim]")
    p.close()


@prior_app.command("resolve")
def prior_resolve(limit: int | None = typer.Option(None, "--limit", help="Only the first N unresolved papers."),
                  directory: Path = DIR):
    """Resolve prior-review papers on OpenAlex (needs --allow-lookup)."""
    from .cli_lookups import say_sent
    from .prior import chain, reviews

    p = Project.find(directory)
    say_sent(p, "prior_resolve")
    try:
        res = chain.resolve(p, limit=limit)
    except reviews.PolicyError as e:
        p.close()
        _exit_policy(e)
    rprint(f"[green]{res['resolved']} resolved, {res['unresolved']} unresolved of {res['total']} papers[/green]")
    for w in res.get("warnings") or []:
        rprint(f"[yellow]warning: {escape(w)}[/yellow]")
    p.close()


@prior_app.command("coverage")
def prior_coverage(scope: str = typer.Option("platforms", "--scope", help="platforms (per export file) or corpus (after dedup)."),
                   directory: Path = DIR):
    """How many prior-review papers each export (or the deduplicated corpus) contains. Local matching only."""
    from .prior import coverage

    p = Project.find(directory)
    try:
        res = coverage.check(p, scope=scope)
    except ValueError as e:
        p.close()
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    for rv in res["reviews"]:
        rprint(f"[bold]{escape(rv['label'])}[/bold]: {rv['found']}/{rv['n']} found ({rv['unmatchable']} without title or DOI)")
        if rv.get("fuzzy"):
            rprint(f"[yellow]{rv['fuzzy']} fuzzy match(es) to confirm[/yellow]")
        t = Table()
        for col in ("scope", "found", "of", "share"):
            t.add_column(col)
        for row in rv["rows"]:
            t.add_row(row["scope"], str(row["found"]), str(row["n"]), f"{row['share']:.0%}")
        rprint(t)
        if rv["missed"]:
            rprint("missed: " + "; ".join(escape((m['title'] or m['doi'] or '')[:60]) for m in rv["missed"][:10])
                   + (" …" if len(rv["missed"]) > 10 else ""))
    p.close()


@prior_app.command("chain")
def prior_chain(direction: str = typer.Option("both", "--direction", help="backward (references), forward (citing works) or both."),
                limit: int | None = typer.Option(None, "--limit", help="Only the first N unresolved/unchained papers."),
                count_seen: bool = typer.Option(False, "--count-seen", help="Uploads final under a frozen query: the validation papers shown count as seen."),
                keep_validation: bool = typer.Option(False, "--keep-validation", help="Uploads final under a frozen query: keep the papers shown as validation."),
                directory: Path = DIR):
    """Fetch the references and citing works of prior-review papers from OpenAlex and write an import file (needs --allow-lookup)."""
    from .construct.artifacts import CHOOSE_LINE, SeenChoiceNeeded
    from .prior import chain, reviews

    if count_seen and keep_validation:
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2)
    from .cli_lookups import say_sent

    p = Project.find(directory)
    say_sent(p, "prior_chain")
    try:
        res = chain.chain_and_export(p, direction=direction, limit=limit,
                                     seen_choice="count" if count_seen else "keep" if keep_validation else None)
    except reviews.PolicyError as e:
        p.close()
        _exit_policy(e)
    except SeenChoiceNeeded as e:
        p.close()
        rprint(f"[yellow]{escape(e.notice)}[/yellow]")
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2) from None
    rprint(f"[green]{res['n_new']} new records fetched; {res['n_exported']} written to {escape(str(res['file']))}[/green]"
           if res.get("file") else f"[yellow]{res['n_new']} new records fetched; nothing new to export[/yellow]")
    for w in res.get("warnings") or []:
        rprint(f"[yellow]warning: {escape(w)}[/yellow]")
    if res.get("notice"):
        rprint(f"[yellow]{escape(res['notice'])}[/yellow]")
    rprint("next: [bold]wider import run[/bold]  (the chain file is imported as platform citation_chain)")
    p.close()
