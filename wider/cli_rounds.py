"""Rounds of query refinement on the command line (D8): the rounds list with the recall-versus-yield plot, a record
count typed by hand, and the acceptance of a round. The route twin is ``wider/ui/routes_rounds.py``; both call
``wider/search/rounds.py``. Also one term's yield on OpenAlex (D16, ``wider/search/term_yield.py``; route
``POST /api/query/yield``).

``register(query_app)`` is called once from ``cli_refine.register``.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

DIR_OPT = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")


def write_plot(project, data: dict) -> Path:
    """``exports/recall_vs_yield.svg`` (next to the PRISMA diagram) and its text table ``recall_vs_yield.txt`` (the
    same numbers), rewritten each time."""
    from .search.round_plots import recall_vs_yield, recall_vs_yield_table

    d = project.path("exports")
    d.mkdir(parents=True, exist_ok=True)
    (d / "recall_vs_yield.svg").write_text(recall_vs_yield(data["rounds"]), encoding="utf-8")
    (d / "recall_vs_yield.txt").write_text("\n".join(recall_vs_yield_table(data["rounds"])) + "\n", encoding="utf-8")
    return d / "recall_vs_yield.svg"


def print_accept(res: dict) -> None:
    if res.get("accepted"):
        rprint(f"[green]v{res['round']} is now the accepted round[/green]")
    for line in res["lines"]:
        rprint(escape(line))


def register(query_app: typer.Typer) -> None:
    @query_app.command("rounds")
    def query_rounds(refresh: bool = typer.Option(False, "--refresh", help="Dry-run every round first (on this "
                                                                             "computer, against today's seeds)."),
                     directory: Path = DIR_OPT):
        """List the rounds (one per synonyms version), oldest first: recall per platform from the last dry run, the
        record count (exported, or typed and marked pasted) and which round is accepted. Writes
        exports/recall_vs_yield.svg with the same numbers as the text table."""
        from .core.project import Project
        from .search import round_plots, rounds

        p = Project.find(directory)
        try:
            if refresh:
                rounds.refresh(p)
            data = rounds.rounds(p)
            for line in rounds.table_lines(data):
                rprint(escape(line))
            svg = write_plot(p, data)
            rprint("recall versus yield:")
            for line in round_plots.recall_vs_yield_table(data["rounds"]):
                rprint(f"  {escape(line)}")
            rprint(f"plot written to {svg.relative_to(p.root)}")
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()

    @query_app.command("replay")
    def query_replay(round_no: int = typer.Argument(..., metavar="ROUND", help="The round (synonyms version)."),
                     directory: Path = DIR_OPT):
        """Recompute a round (dry run, grid, cover, diagnosis, edits, strings) with no model and no network and
        compare with the record its first dry run wrote: the inputs that changed first, then the first output that
        differs. Writes nothing."""
        from .core.project import Project
        from .search import replay

        p = Project.find(directory)
        try:
            res = replay.replay(p, round_no)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        colour = {"matches": "green", "differs": "yellow"}.get(res["result"])
        line = escape(f"v{round_no}: {res['message']}")
        rprint(f"[{colour}]{line}[/{colour}]" if colour else line)

    @query_app.command("provenance")
    def query_provenance(round_no: int = typer.Argument(..., metavar="ROUND", help="The round (synonyms version)."),
                         directory: Path = DIR_OPT):
        """The round's three ledgers in one view: its decisions, the model calls that shaped the query (with input and
        output hashes, never a prompt) and the outside requests with the switch that allowed each. Writes
        exports/provenance_<round>.json."""
        from .core.project import Project
        from .search import provenance

        p = Project.find(directory)
        try:
            v, path = provenance.write(p, round_no)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        for line in provenance.lines(v):
            rprint(escape(line))
        rprint(escape(f"written: {path.relative_to(p.root)}"))

    @query_app.command("count")
    def query_count(round_no: int = typer.Argument(..., metavar="ROUND", help="The round (synonyms version)."),
                    platform: str = typer.Argument(..., help="Platform key (scopus, wos, ...)."),
                    n: str = typer.Argument(..., metavar="N", help="The record count the database shows."),
                    directory: Path = DIR_OPT):
        """Record the number of records the database showed for a round's string, before any export is imported
        (marked pasted; an import of that round and platform replaces it). Undo it in the history."""
        from .core.project import Project
        from .search import rounds

        p = Project.find(directory)
        try:
            res = rounds.set_count(p, round_no, platform, n)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        rprint(f"[green]{escape(res['line'])}[/green]")

    @query_app.command("accept")
    def query_accept(round_no: int = typer.Argument(..., metavar="ROUND", help="The round (synonyms version)."),
                     check: bool = typer.Option(False, "--check", help="Only print what accepting would state."),
                     directory: Path = DIR_OPT):
        """Accept a round: it and the round accepted before are re-evaluated on today's seeds on every profiled
        platform, and each seed this round loses is named. Any round can be accepted; the loss is recorded. The
        accepted round is the default of `wider query render` and the Search strings page. Undo it in the history."""
        from .core.project import Project
        from .search import rounds

        p = Project.find(directory)
        try:
            res = rounds.accept_plan(p, round_no) if check else rounds.accept(p, round_no)
        except ValueError as e:   # no such round, no profile
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        print_accept(res)

    @query_app.command("yield")
    def query_yield(term: str = typer.Argument(None, help="One search term, as written in a block (quotes for a "
                                                          "phrase; * truncation is dropped before sending)."),
                    whole: bool = typer.Option(False, "--query", help="Count the whole query instead of one term "
                                               "(D27), rendered for OpenAlex's title-and-abstract search."),
                    version: int = typer.Option(None, "--version", help="With --query: the synonyms version "
                                                "(default: the one the Search strings page shows)."),
                    seeds: bool = typer.Option(False, "--seeds", help="With --query: also ask which Development "
                                               "seeds OpenAlex retrieves (their DOIs are sent; needs the DOI consent "
                                               "too) and compare each with the local dry run."),
                    directory: Path = DIR_OPT):
        """Count the works one term, or with --query the whole query, finds on OpenAlex. Only the term or
        the query string is sent, under the term switch (`wider lookups terms allow`); --seeds also sends the DOIs of
        the Development seeds (never a Validation seed) and needs the DOI consent. The same request again is answered
        from the cache. The two counts are not comparable. The count informs and changes nothing."""
        from .cli_lookups import ensure_consent
        from .core.project import Project
        from .search.term_yield import query_check_lines, term_yield
        from .search.term_yield import query_yield as run_query

        if (term is None) == (not whole):
            rprint("[red]give exactly one of TERM or --query[/red]")
            raise typer.Exit(1)
        if not whole and (seeds or version is not None):
            rprint("[red]--seeds and --version go with --query[/red]")
            raise typer.Exit(1)
        p = Project.find(directory)
        # the seed check's purpose names the seed DOIs in the term-switch prompt too; asked again for the DOI consent
        ensure_consent(p, "query_seed_check" if seeds else "query_yield" if whole else "term_yield",
                       hint="Nothing was sent.")
        if seeds:
            ensure_consent(p, "query_seed_check", hint="Nothing was sent.")
        try:
            res = run_query(p, version=version, seeds=seeds) if whole else term_yield(p, term)
        except ValueError as e:   # an empty term, no synonyms version
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        if not whole:
            rprint(escape(f"{res['sent']}: {res['line']}"))
            return
        for ln in query_check_lines(res):
            rprint(escape(ln))

    @query_app.command("missed-names")
    def query_missed_names(version: int = typer.Option(None, "--version", help="Synonyms version (default: newest)."),
                           platform: str = typer.Option(None, "--platform", help="The platform profile of the dry "
                                                        "run (default: the first profiled platform)."),
                           min_links: int = typer.Option(2, "--min-links", help="Evaluate candidates linked to at "
                                                         "least this many seeds."),
                           model: bool = typer.Option(False, "--model", help="Also ask the construct backend which "
                                                      "phrases of the missed candidates name a failed block; a "
                                                      "phrase not in the candidate's text is dropped."),
                           as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
                           directory: Path = DIR_OPT):
        """Names from connected papers the query misses: words of the titles and keywords of chaining
        candidates linked to several seeds that the dry run misses and no block holds, each with its consequence.
        Validation and dismissed candidates do not count. Nothing changes until you add a name with `wider seeds
        names --admit NAME --block K --note ...`."""
        import json

        from .cli_backend_consent import ensure_backend_consent
        from .core.project import Project
        from .seeds import missed_names as mn

        p = Project.find(directory)
        try:
            if model:   # research consent: asked once in a terminal, else exit 2 before anything is sent
                from .cli_lookups import say_sent
                say_sent(p, "missed_names", quiet=as_json)
                for cfg, op in mn.needs_consent(p):
                    ensure_backend_consent(p, cfg, op)
            d = mn.missed_names(p, version, platform, min_links, model=model)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        if as_json:
            print(json.dumps(d, indent=2, ensure_ascii=False))
            return
        for line in mn.lines(d):
            rprint(escape(line))
