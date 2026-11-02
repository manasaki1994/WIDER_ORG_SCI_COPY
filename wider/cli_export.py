"""`wider export ...`: outputs for the paper (PRISMA 2020 diagram, methods paragraph, reference-manager files,
replication package). Attached in cli.py."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

from .core.project import Project

export_app = typer.Typer(help="Outputs for the paper: PRISMA 2020 diagram, methods paragraph, reference exports, replication package.",
                         no_args_is_help=True)
DIR = typer.Option(Path("."), "--dir", "-d", help="Project directory.")
PROTO = typer.Option(None, "--protocol", help="Screening protocol (default: the latest one).")
SET = typer.Option(None, "--set", min=1, help="Result set of --protocol (default: the active set).")
USED = typer.Option(False, "--sample-used", help="A set made after recall on the sample was seen, with other settings: the sample is used (the estimate is marked not independent). Final.")
NOT_INFORMED = typer.Option(False, "--not-informed", help="A set made after recall on the sample was seen, with other settings: you state the change was not informed by the result (recorded and disclosed). Final.")


def sample_choice(used: bool, not_informed: bool) -> str | None:
    """The D8 choice from the two options; both at once is refused (exit 2, nothing done)."""
    from .recall.validation import CHOOSE_BOTH

    if used and not_informed:
        rprint(f"[red]{CHOOSE_BOTH}[/red]")
        raise typer.Exit(2)
    return "used" if used else ("not_informed" if not_informed else None)


def print_pending(notice: str | None) -> None:
    """The notice of a set waiting for the D8 choice; the files were written with the pending section."""
    from .recall.validation import CHOOSE_SAMPLE

    if notice:
        rprint(f"[red]{escape(notice)}[/red]")
        rprint(f"[red]{CHOOSE_SAMPLE.replace(' Nothing was done.', '')}[/red]")


def _run(directory: Path, fn):
    p = Project.find(directory)
    try:
        return fn(p)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@export_app.command("prisma")
def prisma(protocol: str = PROTO, result_set: int = SET, directory: Path = DIR):
    """Write exports/prisma_2020.svg (and .json with the counts) from the store."""
    from .recall.outputs import write_prisma

    c = _run(directory, lambda p: write_prisma(p, protocol, result_set))
    unchecked = f"{c['not_checked']:,} not yet checked for duplicates, " if c.get("not_checked") else ""
    rprint(f"wrote {c['path']}: {c['identified']:,} identified, {c['duplicates_removed']:,} duplicates removed, "
           f"{unchecked}{c['screened']:,} screened, {c['excluded']:,} excluded, {c['sought']:,} sought for full text")


@export_app.command("prisma-s")
def prisma_s(directory: Path = DIR):
    """Write exports/prisma_s.md and .json: the 16 PRISMA-S items filled from the store, the others "not recorded"."""
    from .recall.prisma_s import write

    r = _run(directory, write)
    for it in r["items"]:
        rprint(f"{it['no']:>2}. {it['name']}: " + ("; ".join(it["lines"]) if it["filled"] else "not recorded"))
    rprint(f"{r['message']}; wrote {r['path']} and {r['json_path']}")


@export_app.command("methods")
def methods(protocol: str = PROTO, result_set: int = SET, save: bool = typer.Option(False, "--save", help="Write the draft to methods.md (overwrites it)."),
            sample_used: bool = USED, not_informed: bool = NOT_INFORMED, directory: Path = DIR):
    """Print a methods paragraph drafted from the project's facts; edit it in your words in methods.md."""
    from .recall.outputs import METHODS_FILE, methods_draft, save_methods

    choice, pending = sample_choice(sample_used, not_informed), {}

    def go(p):
        d = methods_draft(p, protocol, result_set, choice=choice, pending=pending)
        if save:
            save_methods(p, d)
        return d
    print(_run(directory, go))
    print_pending(pending.get("notice"))
    if save:
        rprint(f"saved to {METHODS_FILE}; rewrite it in your words before using it")


@export_app.command("references")
def references(which: str = typer.Option(None, "--which", help="include (default) | include+maybe | maybe | exclude | "
                                         "agreed | third_read | split | researcher"),
               all_sets: bool = typer.Option(False, "--all", help="Write every decision set that holds a record, and "
                                             "<stem>_index.csv."),
               corpus: bool = typer.Option(False, "--corpus", help="Every unique record with its bibliographic data "
                                           "only (no decision, evaluation or model output), as exports/references/"
                                           "corpus.ris, .bib and .csv; works while labelling is open."),
               protocol: str = PROTO, result_set: int = SET, directory: Path = DIR):
    """RIS, BibTeX and CSV of the chosen decisions for a reference manager (these files carry abstracts)."""
    from .recall.outputs import export_all_references, export_corpus, export_references

    if corpus:
        if which is not None or all_sets:
            rprint("[red]--corpus writes every record without decisions; leave out --which and --all[/red]")
            raise typer.Exit(1)
        r = _run(directory, export_corpus)
        rprint(f"{r['n']:,} records, bibliographic data only: " + ", ".join(r["files"]))
        return
    if all_sets:
        if which is not None:
            rprint("[red]--all writes every decision set; leave out --which[/red]")
            raise typer.Exit(1)
        r = _run(directory, lambda p: export_all_references(p, protocol, result_set))
        for s in r["sets"]:
            rprint(f"{s['set']}: {s['records']:,} records" + (": " + ", ".join(s["files"]) if s["files"] else ", no file"))
        rprint(f"wrote {r['index']}")
        return
    r = _run(directory, lambda p: export_references(p, protocol, which or "include", result_set))
    rprint(f"{r['n']:,} records ({escape(r['which'])}, protocol {escape(r['protocol'])}): " + ", ".join(r["files"]))


@export_app.command("selection")
def selection(preset: str = typer.Option(None, "--preset", help="include | include+maybe (default) | maybe | exclude | "
                                         "unresolved | every"),
              pathway: list[str] = typer.Option(None, "--pathway", help="Any of agreed, third_read, split, researcher "
                                                "(comma-separated or repeated); none = every route."),
              pattern: list[str] = typer.Option(None, "--pattern", help="First two reads, any of include_exclude, "
                                                "include_maybe, maybe_exclude, agreed (comma-separated or repeated)."),
              final_only: bool = typer.Option(False, "--final-only", help="Leave out unresolved records."),
              records: str = typer.Option(None, "--records", help="Only these record ids of the selection, e.g. 3,7,12."),
              formats: list[str] = typer.Option(None, "--format", help="Any of ris (default), bib, csv (comma-separated or "
                                                "repeated); several make one ZIP."),
              no_abstracts: bool = typer.Option(False, "--no-abstracts", help="Leave the abstracts out."),
              dry_run: bool = typer.Option(False, "--dry-run", help="Count and name the file; write nothing."),
              audit: bool = typer.Option(False, "--audit", help="Write <stem>_audit.csv of the whole set."),
              package: bool = typer.Option(False, "--package", help="Write the export package ZIP."),
              add: list[str] = typer.Option(None, "--add", help="With --package: any of include, maybe, exclude, "
                                            "unresolved, selection (comma-separated or repeated)."),
              protocol: str = PROTO, result_set: int = SET, directory: Path = DIR):
    """Export the records of a preset and filters for a reference manager (RIS by default), the audit CSV or the export
    package. These files can carry abstracts and stay on this computer."""
    from .recall.selection import run_selection

    pathway, pattern, formats, add = (",".join(v) if v else None for v in (pathway, pattern, formats, add))
    r = _run(directory, lambda p: run_selection(p, protocol, result_set, preset, pathway, pattern, final_only, records,
                                                formats, not no_abstracts, dry_run, audit, package, add))
    if r.get("count_line") and (dry_run or not r.get("written")):
        rprint(escape(r["count_line"]))
        rprint(escape(r["pathway_line"]))
        rprint(escape(r["pattern_line"]))
    for line in r.get("lines") or []:
        rprint(escape(line))
    rprint(escape(r["message"]))


@export_app.command("prisma-checklist")
def prisma_checklist(protocol: str = PROTO, result_set: int = SET, directory: Path = DIR):
    """Write exports/prisma_2020_checklist.md and .json: the PRISMA 2020 items WIDER records, the others "not recorded
    by WIDER"."""
    from .recall.prisma_2020 import write

    r = _run(directory, lambda p: write(p, protocol, result_set))
    for it in r["items"]:
        rprint(f"{it['no']:>3}. {escape(it['name'])}: " + escape("; ".join(it["lines"])))
    rprint(f"{r['message']}; wrote {r['path']} and {r['json_path']}")


@export_app.command("package")
def package(protocol: str = PROTO, sample: str = typer.Option(None, "--sample", help="Probability sample for the recall estimate."),
            result_set: int = SET, sample_used: bool = USED, not_informed: bool = NOT_INFORMED, directory: Path = DIR):
    """Zip the share bundle, PRISMA diagram, methods text and search strings with a SHA-256 manifest."""
    from .core.researchers import MODE_LINES, mode_of
    from .recall.outputs import write_package

    choice = sample_choice(sample_used, not_informed)
    _run(directory, lambda p: rprint(escape(MODE_LINES[mode_of(p)])))   # the mode before writing (researcher-attribution D5)
    r = _run(directory, lambda p: write_package(p, protocol, sample, result_set, choice=choice))
    rprint(f"wrote {r['path']} ({r['n_files']} files, {r['size_bytes'] / 1024:.0f} KB)")
    print_pending(r.get("notice"))
