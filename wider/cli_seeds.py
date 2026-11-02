"""Seed papers' full texts (vision change 2b): ``wider benchmark pdf add|remove|list`` and the ``wider seeds`` group
(extract, mine, names, streams, policy). The UI twin is ``wider/ui/routes_seeds.py``; both call ``wider/seeds/*``.
Nothing here sends a PDF anywhere."""
from __future__ import annotations

from pathlib import Path

import typer
from rich import print as rprint
from rich.markup import escape

from .cli_refine import CONFIRM_OPT, COUNT_OPT, KEEP_OPT, revision_guard, save_guard, seen_exit

DIR_OPT = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")
BY_OPT = typer.Option(None, "--by", help="Your name; defaults to $WIDER_REVIEWER, then $USER.")

seeds_app = typer.Typer(help="Seed papers' full texts on this computer: extract the text, mine the names a "
                             "literature uses, admit names into the query, label streams by venue.",
                        no_args_is_help=True)
pdf_app = typer.Typer(help="A full-text PDF per seed paper, kept in seeds/ in the project folder (never in a "
                           "bundle, package or export).", no_args_is_help=True)


def _fail(e: Exception) -> None:
    msg = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
    rprint(f"[red]{escape(msg)}[/red]")
    raise typer.Exit(1) from e


def _project(directory: Path):
    from .core.project import Project
    return Project.find(directory)


@pdf_app.command("add")
def pdf_add(benchmark_id: str = typer.Argument(..., help="The seed paper's id (e.g. B001)."),
            path: Path = typer.Argument(..., help="The PDF of its full text."),
            directory: Path = DIR_OPT):
    """Copy a seed paper's PDF into seeds/<id>.pdf (replacing an earlier one). The file stays on this computer."""
    from .seeds import pdf
    p = _project(directory)
    try:
        r = pdf.add(p, benchmark_id, path)
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    rprint(f"[green]{escape(benchmark_id)}: {escape(r['file'])} kept ({r['size_bytes']:,} bytes, sha256 "
           f"{r['sha256'][:12]}…)[/green]")


@pdf_app.command("remove")
def pdf_remove(benchmark_id: str = typer.Argument(...), directory: Path = DIR_OPT):
    """Delete the project's copy of a seed's PDF and its extracted text (your original file is untouched)."""
    from .seeds import pdf
    p = _project(directory)
    try:
        pdf.remove(p, benchmark_id)
    except KeyError as e:
        _fail(e)
    finally:
        p.close()
    rprint(f"[green]{escape(benchmark_id)}: PDF removed[/green]")


@pdf_app.command("list")
def pdf_list(directory: Path = DIR_OPT):
    """Which seed papers have a PDF here, with its hash, and whether its text was extracted."""
    from .seeds import pdf
    p = _project(directory)
    try:
        rows = pdf.list_files(p)
    finally:
        p.close()
    if not rows:
        rprint("no seed papers yet")
        return
    for r in rows:
        if r["has_pdf"]:
            rprint(f"{escape(r['benchmark_id'])}  {escape(r['file'])}  sha256 {r['sha256']}  "
                   f"{'extracted' if r['extracted'] else 'not extracted'}")
        else:
            rprint(f"{escape(r['benchmark_id'])}  no PDF")


@seeds_app.command("extract")
def seeds_extract(benchmark: str = typer.Option(None, "--benchmark", "-b", help="One seed paper (default: every "
                                                                                   "seed with a PDF)."),
                  directory: Path = DIR_OPT):
    """Extract the text of the seed PDFs into runs/seeds/<id>/ (text.txt, sections.json, references.json):
    de-hyphenated, without running headers and footers, the reference list parsed. Local only."""
    from .seeds import pdf
    p = _project(directory)
    try:
        rows = pdf.extract_all(p, benchmark)
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    for r in rows:
        rprint(f"{escape(r['benchmark_id'])}: {r['pages']} pages, {r['words']:,} words, sections "
               f"{escape(', '.join(r['sections']) or 'none')}, {r['references']} references → {escape(r['folder'])}/")
        for w in r["warnings"]:
            rprint(f"  [yellow]{escape(w)}[/yellow]")


@seeds_app.command("mine")
def seeds_mine(benchmark: str = typer.Option(None, "--benchmark", "-b", help="One seed paper (default: all)."),
               no_model: bool = typer.Option(False, "--no-model", help="The pattern pass only; no model is asked."),
               directory: Path = DIR_OPT):
    """Mine candidate names from the extracted seed texts and the seeds' keywords: the Keywords line, "also
    called"-like phrases, quoted and recurring capitalised phrases, and word pairs that co-occur with them in the
    retrieved titles. The run is written to runs/seeds/mine/<stamp>/."""
    from .cli_backend_consent import ensure_backend_consent
    from .seeds import mine
    p = _project(directory)
    if not no_model:   # research, then passages: asked once in a terminal, else exit 2 before anything is sent
        from .cli_lookups import say_sent
        say_sent(p, "seed_mine")
        for cfg, op in mine.needs_consent(p):
            ensure_backend_consent(p, cfg, op)
    try:
        r = mine.mine(p, benchmark, model=not no_model)
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    rprint(f"{len(r['candidates'])} candidate names from {len(r['seeds'])} seed text(s) → {escape(r['folder'])}/")
    if not r["model"].get("ran"):
        rprint(f"[yellow]{escape(r['model']['message'])}[/yellow]")
    if r["validation_notice"]:
        rprint(f"[yellow]{escape(r['validation_notice'])}[/yellow]")
    for c in r["candidates"]:
        same = f"  same as: {escape(', '.join(c['same_as']))}" if c["same_as"] else ""
        rprint(f"  {escape(c['name'])}  {escape('[' + c['pattern'] + ']')} ×{c['count']}{same}")
        rprint(f"    “{escape(c['sources'][0]['quote'])}” ({escape(c['sources'][0]['seed'])}, "
               f"{escape(c['sources'][0]['section'])})")
    if r.get("interview_notice"):
        rprint(escape(r["interview_notice"]))


@seeds_app.command("names")
def seeds_names(admit: str = typer.Option(None, "--admit", metavar="NAME", help="Add this name to a block of the "
                                          "newest synonyms version, as a new draft version."),
                block: str = typer.Option(None, "--block", help="The block to add it to (default: the first block "
                                          "where it retrieves a development seed)."),
                note: str = typer.Option(None, "--note", help="Your reason; required to add a name that retrieves "
                                         "no development seed."),
                show_map: bool = typer.Option(False, "--map", help="Write the name map (name_map.json and .svg) "
                                              "into the newest mining run folder and print it."),
                confirm: bool = CONFIRM_OPT, count: bool = COUNT_OPT, keep: bool = KEEP_OPT, by: str = BY_OPT,
                directory: Path = DIR_OPT):
    """The mined names with what each would do in the dry run: "retrieves B002" or "not admitted: retrieves no
    development seed". --admit NAME adds one (the twin of the card's "Add to block"); the change is journalled and
    undoable, unless the save unlocked a frozen query. Mining never changes the query by itself."""
    from .seeds import names
    p = _project(directory)
    try:
        if admit:
            from .construct.artifacts import SeenChoiceNeeded, save_seen_choice
            revision_guard(p, confirm)
            seen = save_guard(p, count, keep)
            try:
                with save_seen_choice(seen):
                    r = names.admit(p, admit, block, note, by=by, confirm_revision=confirm)
            except SeenChoiceNeeded as e:
                seen_exit(p, e.notice)
            if r["notice"]:
                rprint(f"[yellow]{escape(r['notice'])}[/yellow]")
            rprint(f"[green]synonyms v{r['version']} saved: {escape(r['note'])}[/green]")
            if r["closed_questions"]:
                rprint(f"closed the open question(s) {', '.join(map(str, r['closed_questions']))} about this name")
            return
        lst = names.listing(p)
        m = names.write_map(p) if show_map else None
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    if lst["notice"]:
        rprint(f"[yellow]{escape(lst['notice'])}[/yellow]")
    if not lst["candidates"]:
        rprint("no candidate names yet (wider seeds mine)")
    for c in lst["candidates"]:
        where = f" (block {c['block']})" if c["block"] and c["gains"] else ""
        rprint(f"  {escape(c['name'])}: {escape(c['status'] or 'no synonyms version to test against')}{where}")
    if m is not None:
        for line in names.map_lines(m):
            rprint(f"  {escape(line)}")
        rprint("written: " + ", ".join(m["files"]))


@seeds_app.command("streams")
def seeds_streams(label: tuple[str, str] = typer.Option((None, None), "--label", metavar="VENUE STREAM",
                                                       help='Label a venue as a stream ("" removes the label).'),
                  by: str = BY_OPT, directory: Path = DIR_OPT):
    """Which venues use which names: each candidate or admitted name counted in the reference titles of each venue
    of the seeds' reference lists. Label a venue as a stream with --label; the labels go into the package as
    streams.json. Counting only; you decide what a stream is."""
    from .seeds import streams
    p = _project(directory)
    try:
        if label and label[0] is not None:
            r = streams.label(p, label[0], label[1], by=by)
            rprint(f'[green]{escape(r["venue_norm"])}: ' + (f'stream "{escape(r["label"])}"' if r["label"]
                                                             else "label removed") + "[/green]")
        t = streams.table(p)
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    if not t["references"]:
        rprint("no parsed reference lists yet (wider seeds extract)")
        return
    rprint(f"{t['references']} references in {len(t['venues'])} venues")
    for v in t["venues"]:
        lab = f'  stream: {escape(v["label"])}' if v["label"] else ""
        rprint(f"  {escape(v['venue'])}  ({v['references']} references){lab}")
    if not t["names"]:
        rprint("no candidate or admitted name appears in a reference title (wider seeds mine)")
    for n in t["names"]:
        cells = ", ".join(f"{escape(v['venue'])} {n['counts'][v['venue_norm']]}" for v in t["venues"])
        rprint(f"  {escape(n['name'])}: {cells}")


@seeds_app.command("policy")
def seeds_policy(switch: str = typer.Argument(None, metavar="[passages-to-cloud|grobid-url]",
                                             help="The setting to change."),
                 value: str = typer.Argument(None, metavar="[on|off|URL]", help="Its new state: on or off for "
                                             "passages-to-cloud; a URL on this computer, or off, for grobid-url."),
                 passage_tokens: int = typer.Option(None, "--passage-tokens", help="The most a passage may hold, in "
                                                    "tokens (100-8,000; default 1,500)."),
                 directory: Path = DIR_OPT):
    """What the model pass of seed-paper mining may send. passages-to-cloud on (the default) lets extracted
    passages of seed PDFs, never the file, go to a construct backend that is not local once you allowed that
    backend for passages; off skips the model pass for such a backend. A local backend gets passages regardless.
    grobid-url sets the GROBID server that `wider benchmark references --tier grobid` sends whole seed PDFs to; it
    must run on this computer (127.0.0.1, localhost or ::1). Without arguments: the current policy."""
    from .seeds import policy
    usage = ("usage: wider seeds policy passages-to-cloud on|off [--passage-tokens N] | "
             "wider seeds policy grobid-url URL|off")
    if switch is not None and not ((switch == "passages-to-cloud" and value in ("on", "off"))
                                   or (switch == "grobid-url" and value)):
        _fail(ValueError(usage))
    p = _project(directory)
    try:
        if switch == "grobid-url":
            s = policy.set_policy(p, passage_tokens=passage_tokens, grobid_url=value)
        elif switch is not None or passage_tokens is not None:
            s = policy.set_policy(p, None if switch is None else value == "on", passage_tokens)
        else:
            s = policy.status(p)
    except (KeyError, ValueError) as e:
        _fail(e)
    finally:
        p.close()
    rprint(f"passages-to-cloud: {'on' if s['passages_to_cloud'] else 'off'}; passage size {s['passage_tokens']:,} "
           "tokens")
    rprint(f"lets out: {escape(s['lets_out'])}")
    b = s["backend"]
    if b:
        rprint(f"construct backend {escape(b['name'])} ({escape(str(b['locality_label']))}): {escape(b['state'])}")
    if s["passages_allowed"]:
        rprint(f"allowed for passages: {escape(', '.join(s['passages_allowed']))} "
               "(`wider providers revoke NAME --for passages` withdraws it)")
    rprint(f"GROBID server: {escape(s['grobid_url']) if s['grobid_url'] else 'none (the grobid tier is off)'}")


def register(benchmark_app: typer.Typer, app: typer.Typer) -> None:
    benchmark_app.add_typer(pdf_app, name="pdf")
    app.add_typer(seeds_app, name="seeds")
