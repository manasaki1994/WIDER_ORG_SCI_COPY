"""Query refinement commands added to the existing CLI groups: unlocking a frozen query (D2), the dry run (D6),
the diagnosis of imported exports (D9), the suggested edits and the platform limits (D10).

``register(...)`` is called once from ``cli.py`` and attaches commands to the ``construct`` and ``query`` groups.
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

from .cli_indexing import register as register_indexing
from .search import coverage_plot


def version_number(value):
    """A version as the pages and lists print it ("v1") or as a bare number ("1")."""
    if value is None or isinstance(value, int):
        return value
    v = str(value).strip()
    v = v[1:] if v[:1] in "vV" else v
    if not v.isdigit():
        raise typer.BadParameter(f"{value!r} is not a version number (write 1 or v1)")
    return int(v)


VERSION_HELP = "Version number (1 or v1)."


DIR_OPT = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")
CONFIRM_OPT = typer.Option(False, "--confirm", help="Save after a look that kept validation papers: the save revises "
                           "the query after that look and records those papers as used (not independent).")


COUNT_OPT = typer.Option(False, "--count-seen", help="When this reopens the frozen query or the final uploads after "
                         "validation papers were shown: they count as seen (they no longer validate the search).")
KEEP_OPT = typer.Option(False, "--keep-validation", help="When this reopens the frozen query or the final uploads after "
                        "validation papers were shown: they still count as validation; every output states it.")


def seen_exit(p, notice: str) -> None:
    """The researcher's choice is needed (unlock-seen-choice D5): print the notice and the flags, nothing done, exit 2."""
    from .construct.artifacts import CHOOSE_LINE

    p.close()
    rprint(f"[yellow]{escape(notice)}[/yellow]")
    rprint(f"[red]{CHOOSE_LINE}[/red]")
    raise typer.Exit(2) from None


def seen_guard(p, count: bool, keep: bool, notice=None) -> str | None:
    """``count`` | ``keep`` | None from the two flags (both at once exit 2). Without a flag, when ``notice()`` gives the
    asking notice of the action, exit 2 with it before anything else is done (no model call, nothing saved)."""
    from .construct.artifacts import CHOOSE_LINE

    if count and keep:
        p.close()
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2)
    choice = "count" if count else ("keep" if keep else None)
    asking = notice() if (choice is None and notice) else None
    if asking:
        seen_exit(p, asking)
    return choice


def save_guard(p, count: bool, keep: bool, kind: str = "synonyms") -> str | None:
    """:func:`seen_guard` for a command that saves a ``kind`` version: it asks when the save reopens the frozen query or
    the final uploads while the validation set holds papers (``artifacts.save_plan``)."""
    from .construct import artifacts

    def asking():
        plan = artifacts.save_plan(p, kind)
        return plan[1] if plan and plan[3] else None
    return seen_guard(p, count, keep, asking)


def revision_exit(p, notice: str) -> None:
    """The save needs the researcher's confirmation (D2 of validation-reuse): print the warning, nothing done, exit 2."""
    from .construct.artifacts import REVISION_CONFIRM

    p.close()
    rprint(f"[yellow]{escape(notice)}[/yellow]")
    rprint(f"[red]{REVISION_CONFIRM}[/red]")
    raise typer.Exit(2)


def revision_guard(p, confirm: bool, kind: str = "synonyms") -> None:
    """Before a command saves a synonyms version: after a look that kept papers as validation, without ``--confirm``
    exit 2 with the warning before anything else is done (no model call, nothing saved)."""
    from .construct.artifacts import revision_notice

    notice = None if confirm else revision_notice(p, kind)
    if notice:
        revision_exit(p, notice)


def register(construct_app: typer.Typer, query_app: typer.Typer) -> None:
    from .cli_rounds import register as _rounds; _rounds(query_app)   # noqa: E702, I001 rounds and acceptance (D8)
    @construct_app.command("unlock")
    def construct_unlock(kind: str = typer.Argument(..., help="synonyms (the only kind that can be unlocked)"),
                         version: int = typer.Argument(..., parser=version_number, help="The frozen version to copy into a new draft (1 or v1)."),
                         count: bool = COUNT_OPT, keep: bool = KEEP_OPT, directory: Path = DIR_OPT):
        """Copy a frozen query into a new draft to edit. This reopens the query: when validation papers have been
        shown, choose whether they count as seen (--count-seen) or still count as validation (--keep-validation).
        The unlock and the choice are recorded and final; the frozen version stays frozen."""
        from .construct import artifacts
        from .core.project import Project

        p = Project.find(directory)

        def asking():
            try:
                plan = artifacts.unlock_plan(p, kind, version)
            except (KeyError, ValueError):
                return None   # the unlock itself names the error
            return plan["asking"] if plan["choice"] else None
        choice = seen_guard(p, count, keep, asking)
        try:
            row = artifacts.unlock(p, kind, version, seen_choice=choice)
        except artifacts.SeenChoiceNeeded as e:
            seen_exit(p, e.notice)
        except (KeyError, ValueError) as e:
            rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.store.close()
        rprint(f"[green]{kind} v{version} copied into v{row['version']} (draft)[/green]")
        rprint(f"[yellow]{escape(row['notice'])}[/yellow]")

    @query_app.command("dry-run")
    def query_dry_run(version: int = typer.Option(None, "--version", help="Synonyms version (default: the newest)."),
                      platform: str = typer.Option(None, "--platform", help="One platform (default: every profiled "
                                                                             "platform the project searches)."),
                      terms: bool = typer.Option(False, "--terms", help="Per term: the seeds it hits and the words "
                                                                          "each * matched, with longer and shorter stems."),
                      grid: bool = typer.Option(False, "--grid", help="The term-by-seed grid, the minimal cover, the "
                                                                        "terms that match no seed paper and the files."),
                      directory: Path = DIR_OPT):
        """Read the query against the seed papers on this computer, the way each database would (hit, miss or
        uncertain per seed). Nothing is searched online; the run is written to runs/query/<stamp>/dry_run.json, with
        grid.csv and grid.svg (the term-by-seed heatmap) beside it."""
        from .core.project import Project
        from .search import evaluate

        p = Project.find(directory)
        try:
            res = evaluate.dry_run(p, version, platform)
        except ValueError as e:   # DryRunError, NoProfile
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        print_dry_run(res, terms=terms, grid=grid)

    @query_app.command("diagnose")
    def query_diagnose(version: int = typer.Option(None, "--version", help="Synonyms version (default: the newest one "
                                                                             "an export was imported against)."),
                       platform: str = typer.Option(None, "--platform", help="One imported platform."),
                       directory: Path = DIR_OPT):
        """What each block and term retrieved in the imported exports, the records no term explains, recall per set
        with its 95% interval, and the noise terms. Reads the development seeds, and the validation set only while it
        is shown."""
        _diag(directory, version, platform, "diagnose")

    @query_app.command("clusters")
    def query_clusters(version: int = typer.Option(None, "--version", help="Synonyms version (default: the newest "
                                                                             "one an export was imported against)."),
                       platform: str = typer.Option(None, "--platform", help="One imported platform."),
                       directory: Path = DIR_OPT):
        """The imported records grouped by the terms that matched them: size, three sample titles, the seeds in each
        cluster and your mark. Mark one with `wider query cluster <id> relevant|noise`."""
        _diag(directory, version, platform, "clusters")

    @query_app.command("cluster")
    def query_cluster(key: str = typer.Argument(..., help="The cluster id (C1, C2, ...) or its signature."),
                      mark: str = typer.Argument(..., help="relevant or noise"),
                      by: str = typer.Option(None, "--by", help="Who marks it (default: your user name)."),
                      platform: str = typer.Option(None, "--platform", help="The platform, when the id exists on more "
                                                                             "than one."),
                      version: int = typer.Option(None, "--version", help="Synonyms version (default: the newest "
                                                                           "imported)."),
                      directory: Path = DIR_OPT):
        """Mark a cluster relevant or noise. Your judgement only: no screening decision is made for its records, and
        the mark can be undone from History."""
        from .core.project import Project
        from .search import diagnostics

        p = Project.find(directory)
        try:
            res = diagnostics.mark_cluster(p, key, mark, platform=platform, version=version, by=by)
        except ValueError as e:   # DiagnoseError
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        typer.echo(f"{res['platform']} v{res['version']} {res['id']} ({res['label']}) marked {res['mark']} "
                   f"by {res['by']}")

    @query_app.command("edits")
    def query_edits(version: int = typer.Option(None, "--version", help="Synonyms version (default: the newest)."),
                    directory: Path = DIR_OPT):
        """Suggested edits with their consequences: development seeds gained or lost per platform and, after an
        import, the records an edit removes. Ranked by seeds gained, then (over the record budget) by records saved
        per seed kept. Nothing changes until you apply one with `wider query edit apply <id>`."""
        from .core.project import Project
        from .search import diagnostics

        p = Project.find(directory)
        try:
            res = diagnostics.suggested_edits(p, version)
        except ValueError as e:   # EditError
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        for line in diagnostics.edit_lines(res):
            typer.echo(line)

    edit_app = typer.Typer(help="Apply a suggested edit (`wider query edits` lists them).", no_args_is_help=True)
    query_app.add_typer(edit_app, name="edit")

    @edit_app.command("apply")
    def query_edit_apply(edit_id: str = typer.Argument(..., help="The edit id from `wider query edits` (E1, E2, ...)."),
                         version: int = typer.Option(None, "--version", help="The version the edits were listed "
                                                                              "for (default: the newest)."),
                         by: str = typer.Option(None, "--by", help="Who applies it (default: your user name)."),
                         confirm: bool = CONFIRM_OPT, count: bool = COUNT_OPT, keep: bool = KEEP_OPT,
                         directory: Path = DIR_OPT):
        """Write the edit as a new draft synonyms version whose note names the edit; the version it came from is
        unchanged. Saving while a frozen query stands reopens it, as any save does (with validation papers shown,
        choose --count-seen or --keep-validation)."""
        from .construct import artifacts
        from .core.project import Project
        from .search import diagnostics

        p = Project.find(directory)
        revision_guard(p, confirm)
        choice = save_guard(p, count, keep)
        try:
            with artifacts.save_seen_choice(choice):
                res = diagnostics.apply_edit(p, edit_id, version, by=by, confirm_revision=confirm)
        except artifacts.SeenChoiceNeeded as e:
            seen_exit(p, e.notice)
        except ValueError as e:   # EditError, a save that does not validate
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        typer.echo(f"saved draft synonyms v{res['version']} from v{res['from_version']}: {res['note']}")
        if res["notice"]:
            typer.echo(res["notice"])

    @query_app.command("limits")
    def query_limits(platform: str = typer.Argument(..., help="A platform of the project (scopus, wos, ...)."),
                     dates: str = typer.Option(None, "--dates", help="Date range applied in the database, e.g. "
                                                                     "2010..2024 (an empty value clears it)."),
                     languages: str = typer.Option(None, "--languages", help="Languages, comma separated (en,de)."),
                     reason: str = typer.Option(None, "--reason", help="Why the limits were applied."),
                     by: str = typer.Option(None, "--by", help="Who sets them (default: your user name)."),
                     directory: Path = DIR_OPT):
        """Show or set the limits you applied in a database (dates, languages, reason). They are shown under the
        rendered string, go to the package's limits.json, and the change can be undone from History."""
        from .core.project import Project
        from .search import limits

        p = Project.find(directory)
        try:
            res = limits.set_limits(p, platform, dates, languages, reason, by=by)
        except ValueError as e:   # LimitsError
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1) from e
        finally:
            p.close()
        typer.echo(f"{platform} {res['line']}")

    register_indexing(query_app)   # wider query indexing list|set (cli_indexing.py, 2.10)


def _diag(directory: Path, version: int | None, platform: str | None, what: str) -> None:
    from .core.project import Project
    from .search import diagnostics

    p = Project.find(directory)
    try:
        res = diagnostics.what_did_what(p, version, platform)
        from .interview import (
            bank,  # boundary-record, noise-term and limit questions from this diagnosis (F14)
        )
        line = bank.notice(bank.safe_trigger(p, "diagnose")) if what == "diagnose" else None
    except ValueError as e:   # DiagnoseError
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    finally:
        p.close()
    # plain echo of the engine's lines: the card shows the same numbers
    for line_ in (diagnostics.diagnose_lines(res) if what == "diagnose" else diagnostics.cluster_lines(res)):
        typer.echo(line_)
    if line:
        typer.echo(line)


def print_dry_run(res: dict, terms: bool = False, grid: bool = False) -> None:
    """The dry-run table per platform, the recall line, the notices and the run folder (shared with the route's text)."""
    from rich.table import Table

    rprint(f"synonyms v{res['version']} ({res['status']})")
    for n in res["notices"]:
        rprint(f"[yellow]{escape(n)}[/yellow]")
    for key, pl in res["platforms"].items():
        prof = pl["profile"]
        stem = prof["min_stem"] if prof["min_stem"] is not None else "none"
        t = Table("seed", "set", "state", "terms matched", "why", title=f"{key} (profile: minimum stem {stem})")
        for s in pl["seeds"]:
            t.add_row(s["benchmark_id"], s["set"][:3], s["state"], escape("; ".join(s["terms"])), escape(s["why"]))
        rprint(t)
        rprint(f"{key}: {pl['recall']['text']}")
        # the same lines as the card's expanded term rows and grid (diagnostics.term_lines / grid_lines); plain echo,
        # so a long term is never wrapped differently from the page
        if terms:
            typer.echo(f"{key}: terms")
            for line in pl["terms_text"]:
                typer.echo(line)
        if grid:
            typer.echo(f"{key}: grid (x hit, ? uncertain, . no match)")
            for line in pl["grid_text"]:
                typer.echo(line)
    coverage_plot.print_table(res)
    if res.get("run_dir"):
        rprint(f"written to {res['run_dir']}/dry_run.json")
        if grid:
            for f in res.get("grid_files", []):
                typer.echo(f"written to {f}")
    if (res.get("interview") or {}).get("line"):
        typer.echo(res["interview"]["line"])
