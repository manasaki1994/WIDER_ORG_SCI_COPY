"""WIDER command-line interface."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import typer
import typer.core
from rich import print as rprint
from rich.markup import escape
from rich.table import Column, Table

from . import __version__
from .cli_refine import (
    CONFIRM_OPT,
    COUNT_OPT,
    KEEP_OPT,
    VERSION_HELP,
    revision_guard,
    save_guard,
    seen_exit,
    version_number,
)
from .core.provenance import local_time
from .search.render import platform_keys


class _Commands(typer.core.TyperGroup):
    def invoke(self, ctx):
        """An id too large for the store (e.g. 99999999999999999999999) names nothing, in any command: the page's
        404 message and exit 1, never a traceback. A folder without a project says so, also never a traceback."""
        from .core.project import FileChangedSince, ProjectBroken, ProjectNotFound

        try:
            return super().invoke(ctx)
        except (ProjectNotFound, ProjectBroken, FileChangedSince) as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
        except OverflowError as e:
            if "SQLite INTEGER" not in str(e):
                raise
            rprint("[red]no such id: the number is too large to be one[/red]")
            raise typer.Exit(1)


app = typer.Typer(
    cls=_Commands,
    name="wider",
    help="WIDER: a literature review AI tool for broad, multi-vocabulary constructs.",
    no_args_is_help=True,
    add_completion=False,
)


def _version(value: bool):
    if value:
        rprint(f"wider {__version__}")
        raise typer.Exit()


def _print_stage_result(p, stage: str) -> None:
    """Print the stage's last recorded result (shared with the UI) and the next stage to run."""
    from .core.stage_result import format_result, last_result
    from .core.stages import load_stages

    res = last_result(p, stage)
    if not res:
        return
    colour = {"success": "green", "warning": "yellow", "danger": "red"}[res["tone"]]
    rprint(f"[{colour}]{escape(format_result(res))}[/{colour}]")
    nxt = res.get("next_stage")
    if nxt:
        st = next((x for x in load_stages() if x["key"] == nxt), None)
        if st:
            rprint(f"next: [bold]wider {st['command']}[/bold]  ({st['title']})")


def _w2(x: float) -> str:
    """A weight to two decimals as the pages show it (JavaScript ``toFixed``: the exact value, a tie rounds up), so
    17/8 reads 2.13 on both, not 2.12 here (Python's format rounds the tie to even)."""
    from decimal import ROUND_HALF_UP, Decimal

    return str(Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _named(by: str | None) -> str:
    """The signer of a person's own work (``journal.named_author``): --by, else $WIDER_REVIEWER, never the login
    name; a missing name exits 2 with the reason, as `wider review decide` does."""
    from .core.journal import NAME_NEEDED, named_author

    who = named_author(by)
    if not who:
        rprint(f"[red]{NAME_NEEDED}[/red]")
        raise typer.Exit(2)
    return who


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", "-V", callback=_version, is_eager=True, help="Show version."),
):
    """Run `wider <command> --help` for details on each stage."""
    from .core.journal import INFERRED

    INFERRED.set({})   # names this command takes from the environment (researcher-attribution D2), never carried over


@app.command()
def init(
    directory: Path = typer.Argument(..., help="Directory for the new review project (created if missing)."),
    name: str = typer.Option(None, "--name", "-n", help="Project name (default: directory name)."),
    title: str = typer.Option("", "--title", "-t", help="Human-readable review title."),
    question: list[str] = typer.Option([], "--question", "-q", help="Research question (repeatable)."),
    platform: list[str] = typer.Option(
        [], "--platform", "-p",
        help="Platform key in the order you will search them; the first is the development platform "
             f"({', '.join(platform_keys())}). Repeatable.",
    ),
    force: bool = typer.Option(False, "--force", help="Rewrite project.yaml if it exists."),
):
    """Create a review project directory with project.yaml, benchmark.csv, folders and a store."""
    from .core.project import Project

    questions = [x.strip() for x in question if x.strip()]
    if question and not questions:   # as on the setup page: a question goes into every prompt and the report
        rprint("[red]a project needs at least one research question; nothing was saved[/red]")
        raise typer.Exit(1)
    try:
        p = Project.init(directory, name=name, title=title, questions=questions,
                         platforms=list(platform), exist_ok=force)
    except FileExistsError as e:
        rprint(f"[red]{e}[/red]  (use --force to overwrite project.yaml)")
        raise typer.Exit(1)
    except ValueError as e:   # unknown platform key: nothing is created
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    rprint(f"[green]Created review project[/green] {p.root}")
    rprint("Next: add known-relevant papers to [bold]benchmark.csv[/bold] "
           "(columns benchmark_id,title,doi,set) and run [bold]wider status[/bold].")


@app.command("plan")
def plan_(as_json: bool = typer.Option(False, "--json", help="The cards as JSON, as GET /api/plan returns them."),
          directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """The Review plan's task cards: state, title and summary line of each, then the Plan summary."""
    from .core import plan
    from .core.project import Project

    p = Project.find(directory)
    try:
        cards = plan.tasks(p)
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps({"tasks": cards}, indent=2))
        return
    for c in cards:
        typer.echo(plan.card_line(c))
    typer.echo(f"Plan summary: {plan.summary_line(cards)}")


@app.command()
def status(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Show the project configuration and store counts."""
    from .core.project import Project

    p = Project.find(directory)
    c = p.config
    rprint(f"[bold]{c.name}[/bold]  {c.title}")
    rprint(f"root: {p.root}")
    if c.questions:
        for q in c.questions:
            rprint(f"  question: {q}")
    t = Table("platform", "role")
    for pl in c.platforms:
        t.add_row(pl.label or pl.key, pl.role)
    if c.platforms:
        rprint(t)
    from .search.coverage import (
        NOT_COUNTED,
        development_papers,
        query_results_papers,
        used_papers,
        validation_papers,
    )
    bm = p.read_benchmark()
    nq = len(query_results_papers(p, bm))   # D3: query-results papers in neither set
    nu = len(used_papers(p, bm))   # validation-reuse D1: used validation papers apart
    rprint(f"benchmark: {len(bm)} papers ({len(development_papers(p, bm))} development, "
           f"{len(validation_papers(p, bm))} validation{f', {nu} used validation' if nu else ''}"
           f"{f'; {nq} {NOT_COUNTED}' if nq else ''})")
    rprint(f"default provider: {c.default_provider}; default embedding: {c.default_embedding}")
    counts = {k: v for k, v in p.store.counts().items() if v}
    from .screen.devgate import STORE_LEFT_OUT, open_samples
    dev_open = bool(open_samples(p))
    if dev_open:   # both grow with each development label (D4: no development count)
        counts = {k: v for k, v in counts.items() if k not in ("labels", "history")}
    store = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()) or "empty"
    rprint(f"store: {store}" + (f" ({STORE_LEFT_OUT})" if dev_open else ""))
    from .core.stage_result import all_last_results
    from .core.stages import next_stage, stage_states, state_text

    last = all_last_results(p)
    st = Table("stage", "state", "missing", "last result")
    colour = {"done": "green", "ready": "bold", "blocked": "dim", "extension": "dim"}
    for s in stage_states(p):
        r = last.get(s["key"])
        st.add_row(s["key"], f"[{colour[s['state']]}]{escape(state_text(s))}[/{colour[s['state']]}]", ", ".join(s["missing"]),
                   escape(f"{r['headline']} ({local_time(r['ran_at'])})") if r else "")
    rprint(st)
    from .core.stages import route_sentence
    from .core.summary import needs_items, waiting_counts, waiting_lines
    rprint(f"route: {escape(route_sentence(p))}")
    # what the Overview's Needs attention lists (ui-redesign): each item, then the counts of what waits elsewhere
    from .screen.devgate import gate_item
    gate = gate_item(p)   # a project upgraded to D4: why runs on development records wait
    if gate:
        rprint(f"needs you: {escape(gate['title'])}: {escape(gate['text'])}")
    for x in needs_items(stage_states(p)):
        kind = "needs review: " if x["kind"] == "needs_review" else ""   # "needs you" is said once, by the line itself
        rprint(f"needs you: {escape(x['title'])}: {kind}{escape(x['text'])}")
    for line in waiting_lines(waiting_counts(p)):
        rprint(line)
    from .core.stages import TEST_MODEL_LINE
    tested = [s["title"] for s in stage_states(p) if s.get("test_model")]
    if tested:   # D32 F3: said once, in red, naming the stages
        rprint(f"[red]test model: {escape(', '.join(tested))}: {TEST_MODEL_LINE}[/red]")
    nxt = next_stage(p)
    rprint(f"next: [bold]wider {nxt['command']}[/bold]  ({nxt['title']})" if nxt else "next: all stages done")
    p.store.close()


@app.command("next")
def next_command(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
    why: bool = typer.Option(False, "--why", help="Also print what the stage does and why it comes now."),
):
    """Print only the next command to run (the first stage that is ready or has work for you); `wider status` shows every stage."""
    from .core.project import Project
    from .core.stages import next_stage

    p = Project.find(directory)
    try:
        nxt = next_stage(p)
    finally:
        p.store.close()
    if nxt is None:
        rprint("all stages done")
        return
    rprint(f"wider {nxt['command']}")
    if why:
        rprint(f"[bold]{nxt['title']}[/bold]: {' '.join(nxt['description'].split())}")
        if nxt.get("why"):
            rprint(f"[dim]why now:[/dim] {' '.join(nxt['why'].split())}")
        if nxt.get("needs_you"):
            rprint(f"needs you: {escape(nxt['needs_you'])}")
        if nxt.get("test_model"):
            from .core.stages import TEST_MODEL_LINE
            rprint(f"[red]{TEST_MODEL_LINE}[/red]")


@app.command()
def info(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """One-screen project summary: stage, records, seed coverage, backends (local or cloud), tokens, last run.

    The same summary the home page's info panel shows."""
    from .core.project import Project
    from .core.summary import project_summary, summary_lines

    p = Project.find(directory)
    try:
        s = project_summary(p)
    finally:
        p.close()
    t = Table(Column("", style="dim"), "", show_header=False, box=None)
    for k, v in summary_lines(s):
        t.add_row(k, escape(v))
    rprint(t)


@app.command()
def costs(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
          by: str = typer.Option("stage", "--by", help="Group by stage, backend or run.")):
    """Tokens and costs of every logged model call, grouped by stage, backend or run; the project total last.

    A call's cost is the cost logged when it ran: the cost the backend reported, else the backend's prices; calls on
    this computer without prices count as local, and the others are named as not priced."""
    from .core.project import Project
    from .core.usage import cache_text, project_totals

    col = {"stage": "stage", "backend": "provider || ' (' || COALESCE(model,'') || ', ' || locality || ')'", "run": "COALESCE(run_ref,'-')"}.get(by)
    if col is None:
        rprint("[red]--by must be stage, backend or run[/red]")
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        rows = p.store.query(f"SELECT {col} g, COUNT(*) n, SUM(prompt_tokens) pt, SUM(completion_tokens) ct, SUM(cost_usd) usd, "
                             f"SUM(cost_usd IS NULL) unpriced, SUM(cached_tokens) cached, SUM(cached_tokens IS NOT NULL) cache_reported "
                             f"FROM llm_usage GROUP BY g ORDER BY g")
        tot = project_totals(p)
    finally:
        p.close()
    t = Table(by, "calls", "input tokens", "cached input", "output tokens", "cost (USD)")
    for r in rows:
        cost = f"{r['usd'] or 0:.4f}" + (f" + {r['unpriced']} unpriced" if r["unpriced"] else "")
        cached = f"{r['cached'] or 0:,}" if r["cache_reported"] else "not reported"
        t.add_row(escape(str(r["g"])), f"{r['n']:,}", f"{r['pt'] or 0:,}", cached, f"{r['ct'] or 0:,}", cost)
    rprint(t if rows else "no model calls logged yet")
    legacy = (f"\n  logged cost: ${tot['logged_cost_usd']:.4f}\n  {tot['legacy_calls']:,} screening calls from before the "
              f"log: ${tot['legacy_cost_usd']:.4f}, {tot['legacy_label']}" if tot["legacy_calls"] else "")
    rprint(f"project total: {tot['calls']:,} calls, {tot['tokens']:,} tokens, ${tot['cost_usd']:.4f}"
           + ("" if tot["cost_known"] else f" plus {tot['unpriced_calls']:,} calls without prices")
           + f", {cache_text(tot)}" + legacy)


@app.command("check-backends")
def check_backends_cmd(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Probe every backend in use (the defaults and each stage override) with fixed test sentences (no project data).

    The result is stored and shown on Settings. Local embedding weights are never downloaded here."""
    import asyncio

    from .cli_lookups import say_sent
    from .core.backend_check import check_backends
    from .core.project import Project

    p = Project.find(directory)
    say_sent(p, "backend_check")
    try:
        out = asyncio.run(check_backends(p))
    finally:
        p.close()
    for r in out["results"]:
        mark = "[green]ok[/green]" if r["ok"] else "[red]failed[/red]"
        extra = f" ({r['elapsed_s']} s)" if r.get("elapsed_s") is not None else ""
        err = f": {escape(str(r['error']))}" if r.get("error") and not r["ok"] else ""
        used = f" ({', '.join(r['uses'])})" if r.get("uses") else ""
        rprint(f"{escape(r['role'] + used):9} {escape(str(r['name']))} [{r.get('locality', '?')}] {mark}{extra}{err}")
    if not out["ok"]:
        raise typer.Exit(1)


@app.command()
def stages():
    """List pipeline stages: page title, section, the page's commands and implementation status (stages not built yet are "later")."""
    from .core.stages import load_sections, load_stages

    names = {s["number"]: f"{s['number']} {s['title']}" for s in load_sections()}
    t = Table("stage", "title", "section", "commands", "status")
    for s in load_stages():
        t.add_row(s["key"], s["title"], names.get(s.get("section"), "later"), ", ".join(s["commands"]), s["status"])
    rprint(t)


# ---------------------------------------------------------------- providers
protocol_app = typer.Typer(help="The review protocol: every methodological setting, with the original review's values as defaults.",
                           no_args_is_help=True)
app.add_typer(protocol_app, name="protocol")


@protocol_app.command("show")
def protocol_show(section: str = typer.Argument(None, help="Only this section (identification, dedup, corpus, ...)."),
                  changed: bool = typer.Option(False, "--changed", help="Only settings that differ from the defaults."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Current protocol values, defaults and whether they were changed."""
    from .core.project import Project

    p = Project.find(directory)
    t = Table("setting", "value", "default", "changed")
    for r in p.config.protocol.explain():
        if section and r["section"] != section:
            continue
        if changed and not r["changed"]:
            continue
        t.add_row(r["path"], _short(r["value"]), _short(r["default"]), "[yellow]yes[/yellow]" if r["changed"] else "")
    rprint(t)
    rprint(f"protocol hash [dim]{p.config.protocol.hash()[:12]}[/dim]")


@protocol_app.command("explain")
def protocol_explain(path: str = typer.Argument(None, help="One setting (section.key) or a section; omit for all."),
                     markdown: bool = typer.Option(False, "--markdown", help="Emit the full reference as Markdown."),
                     directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """What each setting does and why the default is what it is."""
    from .core.project import Project

    p = Project.find(directory)
    pr = p.config.protocol
    if markdown:
        print(pr.markdown())
        return
    cur = None
    for r in pr.explain():
        if path and not (r["path"] == path or r["section"] == path):
            continue
        if r["section"] != cur:
            cur = r["section"]
            rprint(f"\n[bold]{cur}[/bold]  [dim]{r['section_doc']}[/dim]")
        mark = " [yellow](changed)[/yellow]" if r["changed"] else ""
        rprint(f"  [cyan]{r['key']}[/cyan] = {_short(r['value'])}{mark}\n      {r['description']}")


@protocol_app.command("set", context_settings={"ignore_unknown_options": True})   # a negative VALUE reaches the check
def protocol_set(path: str = typer.Argument(..., help="Setting as section.key, e.g. corpus.thin_words."),
                 value: str = typer.Argument(..., help="New value (JSON for lists/objects, e.g. '[1,2,3]')."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Change one protocol setting in project.yaml (validated; the change is recorded in every later run)."""
    from .core.project import Project
    from .recall.validation import TargetError, save_protocol

    p = Project.find(directory)
    old = p.config.protocol
    before = old.hash()
    was_on = old.identification.validation_after_freeze
    try:
        p.config.protocol = p.config.protocol.set(path, value)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e.args[0] if isinstance(e, KeyError) else e))}[/red]")
        raise typer.Exit(1)
    _hiding_turned_on(p, was_on)
    try:
        timed = save_protocol(p, old)
    except TargetError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    rprint(f"{path} = {_short(p.config.protocol.get(path))}  (protocol hash {before[:8]} -> {p.config.protocol.hash()[:8]})")
    for line in timed[1:]:
        rprint(escape(line))
    if path == "corpus.thin_words":
        from .rank.versions import thin_words_effect

        rprint(escape(thin_words_effect(p)))
    if path == "sampling.development_reviewers":
        from .screen.devgate import reviewers_effect

        rprint(escape(reviewers_effect(p, p.config.protocol.sampling.development_reviewers)))


@protocol_app.command("reset")
def protocol_reset(path: str = typer.Argument(None, help="Setting to reset; omit to reset the whole protocol."),
                   directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Return one setting (or all) to the original review's value."""
    from .core.project import Project
    from .core.protocol import ReviewProtocol
    from .recall.validation import TargetError, save_protocol

    p = Project.find(directory)
    old = p.config.protocol
    was_on = old.identification.validation_after_freeze
    try:
        p.config.protocol = p.config.protocol.set(path, ReviewProtocol().get(path)) if path else ReviewProtocol()
    except KeyError as e:   # unknown path: project.yaml is left as it was
        rprint(f"[red]{escape(str(e.args[0]))}[/red]")
        raise typer.Exit(1) from e
    _hiding_turned_on(p, was_on)
    try:
        timed = save_protocol(p, old)
    except TargetError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    rprint("reset" + (f" {path}" if path else " all settings") + " to the defaults")
    for line in timed[1:]:
        rprint(escape(line))


def _hiding_turned_on(p, was_on: bool) -> None:
    from .search.coverage import HIDING_ON_SUMMARY, hiding_turned_on

    n = hiding_turned_on(p, was_on)
    if n:
        rprint(HIDING_ON_SUMMARY.format(n=n) + "; logged in the history")


def _short(v, n: int = 60) -> str:
    import json as _json

    s = _json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= n else s[: n - 1] + "…"


providers_app = typer.Typer(help="AI models configured in project.yaml.", no_args_is_help=True)
app.add_typer(providers_app, name="providers")


@providers_app.command("list")
def providers_list(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
                   as_json: bool = typer.Option(False, "--json", help="Print the rows as JSON (the picker cards' fields).")):
    """List configured providers: where each runs (from its last check; nothing is contacted), the key variable and
    whether it is set, and the last check."""
    from .core.backends import provider_rows
    from .core.project import Project

    p = Project.find(directory)
    rows = provider_rows(p)
    p.close()
    if as_json:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    t = Table("name", "kind", "model", "endpoint / command", "where", "destination", "key env", "last check", "default")
    for r in rows:
        t.add_row(escape(r["name"]), r["kind"], escape(r["model"] or ""), escape(_where(r)), r["locality_label"],
                  escape(r["destination"]), _key_text(r), _check_text(r["last_check"]), "*" if r["default"] else "")
    rprint(t)


def _where(r: dict) -> str:
    if r.get("base_url"):
        return r["base_url"]
    cmd = r.get("command") or []
    return " ".join(cmd[:2]) + " ..." if cmd else ""


def _key_text(r: dict) -> str:
    env = r.get("api_key_env")
    return "-" if not env else f"{env} set" if r["key_set"] else f"{env} MISSING"


def _check_text(c: dict | None) -> str:
    return "none" if not c else f"{'ok' if c['ok'] else 'failed'} ({c['at']})"


@providers_app.command("probe")
def providers_probe(
    name: list[str] = typer.Argument(None, help="Provider name(s); default: all configured."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Send one tiny structured request to each provider and report the result."""
    import asyncio

    from .cli_backend_consent import ensure_backend_consent
    from .core.backends import record_probe
    from .core.project import Project
    from .providers.registry import probe

    p = Project.find(directory)
    targets = [pr for pr in p.config.providers if not name or pr.name in name]
    if not targets:
        rprint("[red]no matching provider[/red]")
        raise typer.Exit(1)
    from .cli_lookups import say_sent
    for pr in targets:
        say_sent(p, "backend_check", pr.name)
    checked = [ensure_backend_consent(p, pr, "probe") for pr in targets]   # before any request
    results = asyncio.run(_gather([probe(pr, locality=(c["locality"], c["source"]))
                                   for pr, c in zip(targets, checked, strict=True)]))
    for pr, c, r in zip(targets, checked, results, strict=True):
        record_probe(p, "llm", pr, bool(r["ok"]), c["locality"], r.get("error"))
    t = Table("provider", "model", "ok", "attempts", "seconds", "note / error")
    for r in results:
        t.add_row(r["name"], r["model"], "[green]yes[/green]" if r["ok"] else "[red]no[/red]",
                  str(r.get("attempts", "")), str(r.get("elapsed_s", "")), (r.get("note") or r.get("error") or "")[:90])
    rprint(t)
    if not all(r["ok"] for r in results):
        raise typer.Exit(1)


async def _gather(coros):
    import asyncio
    return await asyncio.gather(*coros)


# ---------------------------------------------------------------- embeddings
embed_app = typer.Typer(help="Embedding models configured in project.yaml.", no_args_is_help=True)
app.add_typer(embed_app, name="embed")


@embed_app.command("list")
def embed_list(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
               as_json: bool = typer.Option(False, "--json", help="Print the rows as JSON (the picker cards' fields).")):
    """List the embedding backends in project.yaml with where they run, their key status, the last check and the
    default."""
    from .core.backends import embedding_rows
    from .core.project import Project

    p = Project.find(directory)
    rows = embedding_rows(p)
    p.close()
    if as_json:
        typer.echo(json.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return
    t = Table("name", "kind", "model", "dim", "endpoint", "where", "key env", "last check", "default")
    for r in rows:
        t.add_row(escape(r["name"]), r["kind"], escape(r["model"] or ""), str(r.get("dim") or "full"),
                  escape(r.get("base_url") or ""), r["locality_label"], _key_text(r), _check_text(r["last_check"]),
                  "*" if r["default"] else "")
    rprint(t)


@embed_app.command("probe")
def embed_probe(
    name: list[str] = typer.Argument(None, help="Embedding name(s); default: the project default."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
    allow_download: bool = typer.Option(False, "--allow-download", help="Permit downloading local model weights."),
):
    """Embed three sentences and check that the related pair scores above the unrelated one."""
    from .cli_backend_consent import ensure_backend_consent
    from .core.backends import backend_place, is_downloaded, record_probe
    from .core.project import Project
    from .embeddings.registry import probe

    p = Project.find(directory)
    names = name or [p.config.default_embedding]
    targets = [e for e in p.config.embeddings if e.name in names]
    if not targets:
        rprint("[red]no matching embedding[/red]")
        raise typer.Exit(1)
    t = Table("embedding", "model", "ok", "dim", "seconds", "sim related", "sim unrelated", "error")
    failed = False
    for e in targets:
        if e.kind == "sentence_transformers" and not allow_download and is_downloaded(e) is False:
            t.add_row(e.name, e.model, "[yellow]skipped[/yellow]", "", "", "", "",
                      "weights not downloaded; rerun with --allow-download")
            continue
        from .cli_lookups import say_sent
        say_sent(p, "backend_check", e.name)
        ensure_backend_consent(p, e, "probe", embedding=True)
        r = probe(e, p.path("embeddings", "cache.sqlite"), project=p)
        record_probe(p, "embedding", e, bool(r["ok"]), backend_place(p, e, embedding=True)["locality"], r.get("error"))
        failed |= not r["ok"]
        t.add_row(e.name, e.model, "[green]yes[/green]" if r["ok"] else "[red]no[/red]", str(r.get("dim", "")),
                  str(r.get("elapsed_s", "")), str(r.get("sim_related", "")), str(r.get("sim_unrelated", "")),
                  (r.get("error") or ("" if r.get("sanity", True) else "related pair not above unrelated"))[:80])
    rprint(t)
    if failed:
        raise typer.Exit(1)


# ---------------------------------------------------------------- ui
@app.command()
def ui(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory."),
    port: int = typer.Option(8765, "--port", help="Port for the local server (bound to 127.0.0.1 only)."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser once the server is up."),
):
    """Serve the local web UI on 127.0.0.1 (never exposed to the network)."""
    import webbrowser

    import uvicorn

    from .core.project import Project
    from .core.recent import default_recent_file
    from .ui.app import create_app

    p = Project.find(directory)
    application = create_app(p, recent_file=default_recent_file())
    url = f"http://127.0.0.1:{port}"
    rprint(f"WIDER UI for [bold]{p.config.name}[/bold] at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run(application, host="127.0.0.1", port=port, log_level="warning")


# ---------------------------------------------------------------- benchmark
benchmark_app = typer.Typer(help="Known-relevant papers that guide query development and ranking.", no_args_is_help=True)
app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("list")
def benchmark_list(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Show benchmark.csv as the store sees it (with abstract status)."""
    from .construct.benchmark import sync
    from .core.project import Project

    p = Project.find(directory)
    rows = sync(p)
    t = Table("id", "set", "source", "year", "doi", "abstract", "title")
    for r in rows:
        t.add_row(r["benchmark_id"], r["set_name"][:3], r["source"] or "researcher", str(r["year"] or ""), r["doi"] or "[red]none[/red]",
                  f"{r['abstract_words']} w ({r['abstract_source'] or 'csv'})" if r["abstract_words"] else "[yellow]missing[/yellow]",
                  r["title"][:70])
    rprint(t)
    p.store.close()


@benchmark_app.command("fetch")
def benchmark_fetch(
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
    all_: bool = typer.Option(False, "--all", help="Retry papers that already have an abstract."),
):
    """Fetch missing abstracts by DOI from OpenAlex, Crossref, Europe PMC and Semantic Scholar."""
    from .construct.benchmark import fetch
    from .core.project import Project

    p = Project.find(directory)
    ensure_consent(p, "seed_abstracts", len(p.read_benchmark()))
    rows = fetch(p, only_missing=not all_, progress=lambda i, n, r: rprint(f"{i}/{n} {r['benchmark_id']} {r['status']}"
                                                                           + (f" ({r['abstract_words']} w, {r['abstract_source']})" if r['status'] == 'fetched' else '')))
    n_ok = sum(1 for r in rows if (r.get("abstract_words") or 0) >= p.config.protocol.corpus.thin_words)
    rprint(f"[bold]{n_ok}/{len(rows)}[/bold] benchmark papers have an abstract")
    p.store.close()


# ---------------------------------------------------------------- construct
construct_app = typer.Typer(help="Construct artifacts: cells, codebook, synonyms, anchors, near_miss, adjacent.", no_args_is_help=True)
app.add_typer(construct_app, name="construct")
from .cli_adjacent import adjacent_app  # noqa: E402  (adjacent-literature loop: wider construct adjacent ...)

construct_app.add_typer(adjacent_app, name="adjacent")


@construct_app.command("list")
def construct_list(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """List artifact versions and their status."""
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    t = Table("kind", "version", "status", "origin", "from", "sha256", "created", "note")
    for kind in artifacts.KINDS:
        for r in artifacts.list_versions(p, kind):
            parent = f"v{r['parent_version']}" if r["parent_version"] else ""
            t.add_row(kind, f"v{r['version']}", r["status"], r["origin"], parent, (r["sha256"] or "")[:12],
                      local_time(r["created_at"]), (r["note"] or "")[:60])
    rprint(t)
    p.store.close()


@construct_app.command("propose")
def construct_propose(
    kind: str = typer.Argument(..., help="cells | codebook | synonyms | anchors | near_miss"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
    provider: str = typer.Option(None, "--provider", help="AI model (default: the construct stage provider, else the project default)."),
    note: str = typer.Option("", "--note", help="Provenance note stored with the proposed version."),
    show_prompt: bool = typer.Option(False, "--show-prompt", help="Print the prompt and exit without calling a model."),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the cost range and exit without calling a model."),
    confirm: bool = CONFIRM_OPT,
    count: bool = COUNT_OPT,
    keep: bool = KEEP_OPT,
):
    """Ask the configured LLM for a draft artifact grounded in the development benchmark papers."""
    from .cli_backend_consent import ensure_research
    from .construct.propose import ProposalError, build_prompt, check_kind, propose
    from .core.project import Project

    try:
        check_kind(kind)
    except ProposalError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(2) from e
    from .construct.artifacts import SeenChoiceNeeded, save_seen_choice

    p = Project.find(directory)
    seen = None
    if not (show_prompt or estimate_only):
        revision_guard(p, confirm, kind)   # before any model call (D2 of validation-reuse)
        seen = save_guard(p, count, keep, kind)   # and the seen choice (unlock-seen-choice D5)
    try:
        if show_prompt:
            prompt, info = build_prompt(p, kind)
            print(prompt)
            rprint(f"[dim]{info}[/dim]")
            raise typer.Exit()
        from .core.estimate import estimate_construct

        _estimate(p, lambda: estimate_construct(p, kind, provider), estimate_only)
        from .cli_lookups import say_sent
        say_sent(p, "construct", provider)
        ensure_research(p, provider, "construct")
        with save_seen_choice(seen):
            row = propose(p, kind, provider_name=provider, note=note, confirm_revision=confirm)
    except SeenChoiceNeeded as e:
        seen_exit(p, e.notice)
    except (ProposalError, ValueError) as e:     # a failed call, or a backend that cannot write text (Jev)
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    finally:
        p.store.close()
    rprint(f"[green]{kind} v{row['version']}[/green] saved as draft: {row['path']}")
    _print_notice(row)
    for f in row.get("fixes") or []:
        rprint(f"  fixed: {escape(f)}")
    for c in row["calls"]:
        rprint(f"  call {c['tag']}: ok={c['ok']} attempts={c['attempts']} {c['elapsed_s']}s")
    rprint(f"Edit the file, then: wider construct approve {kind} {row['version']}")


@construct_app.command("show")
def construct_show(kind: str, version: int = typer.Argument(None, parser=version_number, help=VERSION_HELP + " Default: newest."), directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Print an artifact version (default: newest)."""
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    row, data = artifacts.load(p, kind, version)
    if row is None:
        rprint("[red]no such artifact[/red]")
        raise typer.Exit(1)
    rprint(f"[dim]# {kind} v{row['version']} ({row['status']}, {row['origin']}) {row['path']}[/dim]")
    print(artifacts.dump_yaml(data))
    p.store.close()


@construct_app.command("outline")
def construct_outline(kind: str):
    """Print a starting text to write an artifact by hand (the fields its format needs); edit it, then save it with
    `wider construct save KIND FILE`."""
    from .construct import artifacts

    try:
        print(artifacts.outline(kind), end="")
    except KeyError as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        raise typer.Exit(1)


@construct_app.command("save")
def construct_save(kind: str, file: Path, note: str = typer.Option("", "--note", help="Provenance note stored with the new version."),
                   confirm: bool = CONFIRM_OPT, count: bool = COUNT_OPT, keep: bool = KEEP_OPT,
                   directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Save an edited YAML/JSON file as a new manual version of the artifact."""
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    revision_guard(p, confirm, kind)
    seen = save_guard(p, count, keep, kind)
    try:
        row = artifacts.import_file(p, kind, file, note=note, confirm_revision=confirm, seen_choice=seen)
    except artifacts.SeenChoiceNeeded as e:
        seen_exit(p, e.notice)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]{kind} v{row['version']}[/green] saved: {row['path']}")
    _print_notice(row)
    p.store.close()


@construct_app.command("approve")
def construct_approve(kind: str, version: int = typer.Argument(..., parser=version_number, help=VERSION_HELP), directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Mark a version as approved by the researcher (the version used by later stages)."""
    _set_status(kind, version, "approved", directory)


@construct_app.command("freeze")
def construct_freeze(kind: str, version: int = typer.Argument(..., parser=version_number, help=VERSION_HELP),
                     count_seen: bool = typer.Option(False, "--count-seen", help="Freezing another synonyms version while a frozen query stands: the validation papers shown so far count as seen (they no longer validate the search)."),
                     keep_validation: bool = typer.Option(False, "--keep-validation", help="Freezing another synonyms version while a frozen query stands: the validation papers shown so far still count; every output states it."),
                     directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Freeze a version: no further status changes; for synonyms this unlocks the validation benchmark set. Any
    synonyms version can be frozen; the one frozen last is the searched query."""
    from .construct.artifacts import CHOOSE_LINE

    if count_seen and keep_validation:
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2)
    _set_status(kind, version, "frozen", directory, "count" if count_seen else ("keep" if keep_validation else None))


@construct_app.command("restore")
def construct_restore(kind: str, version: int = typer.Argument(..., parser=version_number, help=VERSION_HELP), confirm: bool = CONFIRM_OPT, count: bool = COUNT_OPT,
                      keep: bool = KEEP_OPT,
                      directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Bring an older version back as the newest draft (versions themselves never change)."""
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    revision_guard(p, confirm, kind)
    seen = save_guard(p, count, keep, kind)
    try:
        row = artifacts.restore_version(p, kind, version, confirm_revision=confirm, seen_choice=seen)
    except artifacts.SeenChoiceNeeded as e:
        seen_exit(p, e.notice)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{str(e).strip(chr(39))}[/red]")
        raise typer.Exit(1)
    finally:
        p.store.close()
    rprint(f"[green]{kind} v{version} restored as v{row['version']} (draft)[/green]")
    _print_notice(row)


def _print_notice(row: dict) -> None:
    """The line a save, freeze or unlock returns about the frozen query and the validation papers (D2)."""
    if row.get("notice"):
        rprint(f"[yellow]{escape(row['notice'])}[/yellow]")


def _set_status(kind, version, status, directory, seen_choice=None):
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    try:
        row = artifacts.set_status(p, kind, version, status, seen_choice)
    except artifacts.SeenChoiceNeeded as e:
        rprint(f"[yellow]{escape(e.notice)}[/yellow]")
        rprint(f"[red]{artifacts.CHOOSE_LINE}[/red]")
        p.store.close()
        raise typer.Exit(2)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        p.store.close()
        raise typer.Exit(1)
    if row.get("notice") and "already the frozen query" in row["notice"]:
        rprint(f"[green]{escape(row['notice'])}[/green]")
    else:
        rprint(f"[green]{kind} v{version} is now {status}[/green]")
        _print_notice(row)
    p.store.close()


@construct_app.command("diff")
def construct_diff(kind: str, a: int, b: int, directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Unified diff between two versions."""
    from .construct import artifacts
    from .core.project import Project

    p = Project.find(directory)
    print(artifacts.diff(p, kind, a, b))
    p.store.close()


# ---------------------------------------------------------------- query
query_app = typer.Typer(help="Render the synonyms artifact as platform search strings.", no_args_is_help=True)
app.add_typer(query_app, name="query")


def _load_query(p, version):
    from .construct import artifacts
    from .search.ir import Query

    row, data = artifacts.load(p, "synonyms", version)
    if row is None:
        rprint("[red]no synonyms artifact; propose one, import a string (wider query import) or save YAML[/red]")
        raise typer.Exit(1)
    return row, Query.model_validate(data)


@query_app.command("render")
def query_render(
    platform: list[str] = typer.Argument(None, help="Platform keys (default: the project's platforms)."),
    version: int = typer.Option(None, "--version", "-v", help="Synonyms version (default: the accepted round, else the newest)."),
    compact: bool = typer.Option(False, "--compact", help="Single line, as pasted."),
    out: Path = typer.Option(None, "--out", help="Write <platform>.txt files into this directory."),
    blocks: bool = typer.Option(False, "--blocks", help="First list each block's name, fields and terms."),
    with_ledger: bool = typer.Option(False, "--ledger", help="Print each string's translation ledger under it."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Print or write the search string for each platform."""
    from .core.project import Project
    from .search.catalogue import project_platforms, render_project
    from .search.limits import limits, limits_line
    from .search.render import block_list, notes_for, platforms, render
    from .search.render.reduced import gscholar_notes

    p = Project.find(directory)
    from .search.rounds import default_version
    row, q = _load_query(p, version if version is not None else default_version(p))
    chosen = {c["key"]: c for c in project_platforms(p)}  # the project's own list, custom entries included
    limits_of = limits(p)
    keys = platform or list(chosen) or ["ebsco", "scopus", "wos", "proquest", "ovid"]
    try:
        rendered = render_project(p, q, pretty=not compact)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if blocks:  # the same list as the Search strings page (block_list)
        print(f"# blocks of synonyms v{row['version']}")
        for b in block_list(q):
            print(b["text"])
        print()
    for k in keys:
        if k in rendered:
            s, label, paste = rendered[k], chosen[k]["label"], chosen[k].get("paste") or f"custom platform, {chosen[k]['syntax']} syntax"
        elif k in platforms():
            s, label, paste = render(q, k, pretty=not compact), platforms()[k]["label"], platforms()[k]["paste"]
        else:
            rprint(f"[red]unknown platform {k!r}: add it with 'wider query platforms add {k} --syntax <family>'[/red]")
            raise typer.Exit(1)
        header = (f"# {p.config.name}: synonyms v{row['version']} ({row['status']}) rendered for {label}\n"
                  f"# {paste.strip()}\n")
        style = (chosen[k].get("style") if k in chosen else platforms()[k].get("style"))
        if style == "gscholar" and (note := gscholar_notes(q)["text"]):
            header += f"# {note}\n"
        for note in notes_for(q, style):  # field codes: unverified keyword codes, wider codes, PubMed's [tiab]
            header += f"# {note}\n"
        if lim := limits_of.get(k):  # the limits applied in the database (`wider query limits`)
            header += f"# {limits_line(lim)}\n"
        footer = ""
        if with_ledger:  # what the platform cannot say (D13), from the same facts the renderer uses
            from .search.translation import ledger, ledger_lines
            footer = "".join(f"# ledger: {ln}\n" for ln in ledger_lines(ledger(q, style or k)))
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{k}.txt").write_text(header + s + "\n" + footer, encoding="utf-8")
            rprint(f"wrote {out / f'{k}.txt'} ({len(s)} chars)")
        else:
            print(header + s + "\n" + footer)
    p.close()


@query_app.command("ledger")
def query_ledger(
    platform: str = typer.Option(None, "--platform", "-p", help="One platform key (default: the project's platforms)."),
    version: int = typer.Option(None, "--version", "-v", help="Synonyms version (default: the accepted round, else the newest)."),
    as_json: bool = typer.Option(False, "--json", help="Print the rows as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """What each platform's string cannot say or searches more widely than the query (the translation ledger)."""
    from .core.project import Project
    from .search.render import platforms
    from .search.rounds import default_version
    from .search.translation import ledger, ledger_lines, project_ledgers

    p = Project.find(directory)
    try:
        _, q = _load_query(p, version if version is not None else default_version(p))
        ledgers = project_ledgers(p, q)
        if platform:
            if platform in ledgers:
                ledgers = {platform: ledgers[platform]}
            elif platform in platforms():
                ledgers = {platform: ledger(q, platform)}
            else:
                rprint(f"[red]unknown platform {platform!r}[/red]")
                raise typer.Exit(1)
        if as_json:
            print(json.dumps(ledgers, indent=2))
            return
        for k, rows in ledgers.items():
            print(f"# {k}")
            for ln in ledger_lines(rows):
                print(ln)
            print()
    finally:
        p.close()


@query_app.command("archive")
def query_archive(
    version: int = typer.Option(None, "--version", "-v", help="Synonyms version (default: newest)."),
    list_only: bool = typer.Option(False, "--list", help="Only list what is archived."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Archive the rendered search strings with date, platform and synonyms version (exports/search_strings/).

    Freezing the synonyms archives them automatically; unchanged strings are not stored twice."""
    from .core.project import Project
    from .search.archive import archive, entries

    p = Project.find(directory)
    try:
        if not list_only:
            r = archive(p, version)
            rprint(f"{r['n_new']} new string(s) archived in {r['dir']} (synonyms v{r['synonyms_version']})")
        from .search.archive import NO_LEDGER

        t = Table("archived", "platform", "synonyms", "file", "ledger")
        for e in entries(p):
            t.add_row(local_time(e["archived_at"]), escape(e["label"]), f"v{e['synonyms_version']} {e['synonyms_status']}",
                      escape(e["file"]), escape(e["ledger"] or NO_LEDGER))
        rprint(t)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


def _string_style(platform: str, directory: Path) -> str:
    """A platform as the user names it for parsing: a catalogue key, or a project key (custom ones through their
    syntax family) when a project is found."""
    from .search.lint import lint_style
    from .search.render import platforms

    if platform in platforms():
        return platform
    from .core.project import Project

    p = Project.find(directory)
    try:
        return lint_style(p, platform)
    finally:
        p.close()


def _read_string(file: str) -> tuple[str, str]:
    if file == "-":
        return sys.stdin.read(), "standard input"
    path = Path(file)
    try:
        return path.read_text(encoding="utf-8"), path.name
    except OSError as e:   # a missing or unreadable file: one line, not a traceback
        rprint(f"[red]could not read {escape(file)}: {escape(e.strerror or str(e))}[/red]")
        raise typer.Exit(1)


@query_app.command("parse")
def query_parse(
    file: str = typer.Argument("-", help="A saved search string, or - for standard input."),
    platform: str = typer.Option("ebsco", "--platform", "-p", help="The syntax the string is written in."),
    as_json: bool = typer.Option(False, "--json", help="Print the findings and the parsed query as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Check and parse a search string without saving it: the findings (each with its position), then the blocks and
    the logic the string was read as. Exits 1 when the string cannot be parsed."""
    from .search.render import block_list
    from .search.string_check import checked_parse, finding_lines, save_refusal

    text, _ = _read_string(file)
    try:
        style = _string_style(platform, directory)
        q, findings = checked_parse(text, style)
    except ValueError as e:
        if as_json:
            print(json.dumps({"error": f"could not parse: {e}", "findings": _safe_check(text, platform)}, indent=2))
        else:
            rprint(f"[red]could not parse: {escape(str(e))}[/red]")
        raise typer.Exit(1)
    refusal = save_refusal(q)
    if as_json:
        print(json.dumps({"findings": findings, "query": q.model_dump(mode="json", exclude_defaults=True),
                          "save_refusal": refusal}, indent=2))
        return
    for line in finding_lines(findings):
        rprint(("[yellow]" if line.startswith("warning") else "") + escape(line))
    for b in block_list(q):
        rprint(escape(b["text"]))
    rprint(f"logic: {escape(json.dumps(q.model_dump(mode='json')['logic']))}")
    if refusal:
        rprint(f"[red]{escape(refusal)}[/red]")


def _safe_check(text: str, platform: str) -> list[dict]:
    from .search.string_check import check_string

    try:
        return check_string(text, platform)
    except ValueError:
        return []


@query_app.command("import")
def query_import(
    file: Path = typer.Argument(..., help="A saved search string."),
    platform: str = typer.Option("ebsco", "--platform", "-p", help="The syntax the string is written in."),
    note: str = typer.Option("", "--note", help="Provenance note stored with the new version."),
    confirm: bool = CONFIRM_OPT,
    count: bool = COUNT_OPT,
    keep: bool = KEEP_OPT,
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Check and parse an existing search string into the query IR and save it as a synonyms version. Warnings say
    how the string was read; an error saves nothing."""
    from .construct import artifacts
    from .core.project import Project
    from .search.ir import count_terms
    from .search.string_check import checked_parse, finding_lines

    p = Project.find(directory)
    revision_guard(p, confirm)
    seen = save_guard(p, count, keep)
    try:
        q, findings = checked_parse(file.read_text(encoding="utf-8"), _string_style(platform, directory),
                                    notes=note or f"imported from {file.name}")
    except ValueError as e:
        p.close()
        rprint(f"[red]could not parse: {escape(str(e))}[/red]")
        raise typer.Exit(1)
    for line in finding_lines(findings):
        rprint("[yellow]" + escape(line))
    try:   # the schema is stricter than the parser (distances 1-15, terms of two characters or more)
        row = artifacts.save_new_version(p, "synonyms", q.model_dump(mode="json", exclude_defaults=True),
                                         origin="import", note=note or f"imported from {file.name}",
                                         confirm_revision=confirm, seen_choice=seen)
    except artifacts.SeenChoiceNeeded as e:
        seen_exit(p, e.notice)
    except ValueError as e:
        p.close()
        rprint(f"[red]could not save: {escape(str(e))}[/red]")
        raise typer.Exit(1)
    rprint(f"[green]synonyms v{row['version']}[/green] from {file.name}: blocks {count_terms(q)}")
    _print_notice(row)
    p.close()


@query_app.command("review")
def query_review(
    platform: str = typer.Option(None, "--platform", "-p", help="One platform key (default: every chosen platform)."),
    reader: list[str] = typer.Option(None, "--reader", "-r", help="A backend for this run (once or twice; default: "
                                     "the review stage, else the construct backend)."),
    version: int = typer.Option(None, "--version", "-v", help="Synonyms version (default: the accepted round, else the newest)."),
    as_json: bool = typer.Option(False, "--json", help="Print the review as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """A model second reader of the search strings (PRESS checklist): one or two backends read each string with the
    query, its ledger, the lint and string-check findings and your query decisions, and report findings that quote
    the string. Findings both runs report are "agreed", the rest "unsure"; nothing is changed. Not a human peer
    review. The run is written to runs/query/review/<stamp>/."""
    from .cli_backend_consent import ensure_backend_consent
    from .core.project import Project
    from .providers.base import ProviderError
    from .search import review

    p = Project.find(directory)
    try:
        try:
            r = review.resolve_readers(p, reader or None)
        except ValueError as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
        for cfg in {c.name: c for c in r["configs"]}.values():
            if why := review._gate(cfg):   # before any consent question: nothing is sent to this reader
                rprint(f"[red]{escape(why)}[/red]")
                raise typer.Exit(2)
        from .cli_lookups import say_sent
        for cfg in {c.name: c for c in r["configs"]}.values():
            say_sent(p, "search_review", cfg.name, quiet=as_json)
            ensure_backend_consent(p, cfg, "research")   # asked in a terminal, else exit 2 before anything is sent
        try:
            out = review.second_read(p, version, platform, reader or None)
        except review.ReaderRefused as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(2)
        except (ValueError, ProviderError) as e:
            rprint(f"[red]{escape(str(e))}[/red]")
            raise typer.Exit(1)
        if as_json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return
        for ln in review.lines(out):
            print(ln)
        print(f"written to {out['run_dir']}/")
    finally:
        p.close()


@query_app.command("lint")
def query_lint(version: int = typer.Option(None, "--version", "-v"),
               platform: str = typer.Option(None, "--platform", "-p", help="Apply this platform's limits (minimum "
                                            "stem, proximity range); default the strictest of all."),
               directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Check the synonyms artifact for short stems, unquoted phrases, missing spelling variants, unused blocks,
    covered terms, wildcards after a hyphen and proximity outside the platform's range."""
    from .core.project import Project
    from .search.lint import lint, lint_style

    p = Project.find(directory)
    _, q = _load_query(p, version)
    try:
        issues = lint(q, lint_style(p, platform))
    except ValueError as e:
        p.close()
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    t = Table("level", "block", "message", "term")
    for i in issues:
        t.add_row(("[red]" if i["level"] == "error" else "[yellow]") + i["level"], i["block"], i["message"], i["term"][:60])
    rprint(t if issues else "[green]no issues[/green]")
    p.close()
    if any(i["level"] == "error" for i in issues):
        raise typer.Exit(1)


# `wider query platforms [add|remove|order|role]` lives in cli_setup.py (registered at the bottom of this file)


# ---------------------------------------------------------------- import + coverage
import_app = typer.Typer(help="Import database exports (RIS, CSV/TSV, XLS/XLSX, BibTeX) from inputs/<platform>/ "
                              "and check each file: benchmark hits, query-vocabulary overlap, export caps, duplicates.",
                         invoke_without_command=True)
app.add_typer(import_app, name="import")


def _print_sanity(report: dict) -> None:
    from .ingest.report import SANITY_COLUMNS, VERDICT_COLOURS, sanity_rows
    from .ingest.sanity import THRESHOLD_RULE

    t = Table(*[Column(c, overflow="fold") for c in SANITY_COLUMNS])
    for row in sanity_rows(report):
        colour = VERDICT_COLOURS.get(row[6], "white")
        t.add_row(*row[:6], f"[{colour}]{row[6]}[/{colour}]", escape(row[7]))
    rprint(t)
    syn = report.get("synonyms")
    rprint(f"[dim]benchmark papers checked: {report['benchmark_n']}; synonyms: "
           f"{'v' + str(syn['version']) + ' (' + syn['status'] + ')' if syn else 'none yet'}; thresholds: "
           f"min benchmark hits {report['thresholds']['min_benchmark_hits']}, min vocabulary overlap "
           f"{report['thresholds']['min_vocabulary_overlap']} ({THRESHOLD_RULE})[/dim]")


def _seen_flag(count_seen: bool, keep_validation: bool) -> str | None:
    """``count`` | ``keep`` | None from the two flags; both at once exits 2 (D21, Q2)."""
    from .construct.artifacts import CHOOSE_LINE

    if count_seen and keep_validation:
        rprint(f"[red]{CHOOSE_LINE}[/red]")
        raise typer.Exit(2)
    return "count" if count_seen else ("keep" if keep_validation else None)


def _exit_choice(p, e) -> None:
    """The researcher's choice is needed (D21, Q2): print the asking notice and the flags, nothing done, exit 2."""
    from .construct.artifacts import CHOOSE_LINE

    p.close()
    rprint(f"[yellow]{escape(e.notice)}[/yellow]")
    rprint(f"[red]{CHOOSE_LINE}[/red]")
    raise typer.Exit(2) from None


def _import_run(directory: Path, seen_choice: str | None = None, round_no: int | None = None) -> None:
    from .construct.artifacts import SeenChoiceNeeded
    from .core.project import Project
    from .ingest.importer import import_all, summary
    from .ingest.report import import_line
    from .ingest.sanity import check_all

    p = Project.find(directory)
    notices: list[str] = []
    try:
        results = import_all(p, seen_choice=seen_choice, notices=notices, round_no=round_no)
    except SeenChoiceNeeded as e:
        _exit_choice(p, e)
    except ValueError as e:   # --round names no synonyms version: nothing imported
        p.close()
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    for n in notices:
        rprint(f"[yellow]{escape(n)}[/yellow]")
    for r in results:
        colour = {"imported": "green", "error": "red"}.get(r["status"], "dim")
        rprint(f"[{colour}]{escape(import_line(r))}[/{colour}]")
        if r.get("recorded"):
            rprint(f"  {escape(r['recorded'])}")
    t = Table("platform", "files", "records")
    for r in summary(p):
        t.add_row(r["platform"], str(r["files"]), str(r["records"]))
    rprint(t)
    _print_sanity(check_all(p))
    _print_stage_result(p, "import")
    p.close()


@import_app.callback()
def import_cmd(ctx: typer.Context, directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Without a subcommand: import every new file under inputs/<platform>/ and print the sanity table (= run)."""
    if ctx.invoked_subcommand is None:
        _import_run(directory)


@import_app.command("run")
def import_run(
    count_seen: bool = typer.Option(False, "--count-seen", help="Adding a file while the uploads are final, after validation papers were shown: they count as seen (they no longer validate the search)."),
    keep_validation: bool = typer.Option(False, "--keep-validation", help="Adding a file while the uploads are final, after validation papers were shown: they still count as validation; every output states it."),
    round_no: int = typer.Option(None, "--round", help="The synonyms version (round) these exports answer (default: the frozen query, else the newest version)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory."),
):
    """Import every new export under inputs/<platform>/ (each file once, by content hash), then check all files. A new
    file while the uploads are marked final reopens them (the notice says what that means)."""
    _import_run(directory, _seen_flag(count_seen, keep_validation), round_no)


@import_app.command("add")
def import_add(
    platform: str = typer.Argument(..., help="Platform key the files were exported from (folder under inputs/)."),
    file: list[Path] = typer.Argument(..., help="Export file(s): .ris, .csv, .tsv, .txt, .xls, .xlsx or .bib."),
    count_seen: bool = typer.Option(False, "--count-seen", help="Adding a file while the uploads are final, after validation papers were shown: they count as seen (they no longer validate the search)."),
    keep_validation: bool = typer.Option(False, "--keep-validation", help="Adding a file while the uploads are final, after validation papers were shown: they still count as validation; every output states it."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory."),
):
    """Copy export files into inputs/<platform>/ and show their sanity verdict; run `wider import run` to import.

    Every path is checked before any is copied. An existing file is never overwritten: a file whose bytes are
    already under inputs/ is reported as a duplicate and not copied, and a taken name gets a free one (name-2.csv).
    A new file while the uploads are marked final reopens them (the notice says what that means)."""
    from .construct.artifacts import SeenChoiceNeeded
    from .core.project import Project
    from .core.provenance import sha256_file
    from .ingest.importer import add_file, same_export
    from .ingest.readers import INPUT_SUFFIXES
    from .ingest.sanity import benchmark_list, check_file, vocabulary

    bad = [(f, "does not exist" if not f.exists() else "is not a file" if not f.is_file() else
            f"is not an export; use one of {', '.join(sorted(INPUT_SUFFIXES))}")
           for f in file if not f.is_file() or f.suffix.lower() not in INPUT_SUFFIXES]
    if bad:
        for f, why in bad:
            rprint(f"[red]{escape(str(f))} {why}[/red]")
        rprint("[red]nothing copied[/red]")
        raise typer.Exit(2)
    choice = _seen_flag(count_seen, keep_validation)
    p = Project.find(directory)
    bench, vocab = benchmark_list(p), vocabulary(p)
    files = []
    for f in file:
        same = same_export(p, f)
        if same:
            rprint(f"[dim]{escape(f.name)} is identical to inputs/{same[0]}/{escape(same[1].name)}; not copied[/dim]")
            files.append(check_file(p, platform, f, bench=bench, vocab=vocab,
                                    seen={sha256_file(f): f"{same[0]}/{same[1].name}"}))
            continue
        notices: list[str] = []
        try:
            dst = add_file(p, platform, f, seen_choice=choice, notices=notices)
        except SeenChoiceNeeded as e:
            _exit_choice(p, e)
        except ValueError as e:   # not an export (the page's 400): nothing saved
            rprint(f"[red]{escape(str(e))}[/red]")
            p.close()
            raise typer.Exit(1)
        rprint(f"copied {escape(f.name)} → inputs/{platform}/{escape(dst.name)}")
        for n in notices:
            rprint(f"[yellow]{escape(n)}[/yellow]")
        files.append(check_file(p, platform, dst, bench=bench, vocab=vocab))
    _print_sanity({"files": files, "benchmark_n": len(bench),
                   "synonyms": {"version": vocab["version"], "status": vocab["status"]} if vocab else None,
                   "thresholds": p.config.protocol.imports.model_dump()})
    p.close()


@import_app.command("final")
def import_final(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Mark the uploads final: the Validation set unlocks as a frozen query does, and a later upload, import or
    citation-chain file reopens them. Refused while no file is imported or a saved file waits to be.
    Listed in History; undo with `wider history undo ID`."""
    from .core.project import Project
    from .ingest.importer import mark_final

    p = Project.find(directory)
    try:
        r = mark_final(p)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    if r["already"]:
        rprint(f"the uploads were already marked final at {r['at']}")
    else:
        rprint(f"[green]uploads marked final at {r['at']}[/green] (change {r['change_id']}; undo: "
               f"wider history undo {r['change_id']})")


@import_app.command("list")
def import_list(as_json: bool = typer.Option(False, "--json", help="Print the rows and the final mark as JSON."),
                directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """List the imported files, oldest first: id, platform, file, records, import time, the query (search strings)
    version frozen at import ("not recorded" when none was), whether the file enters deduplication, the number of
    results it is a random sample of (empty: a complete export), and when the uploads were marked final. Changes
    nothing."""
    from .core.project import Project
    from .ingest.importer import list_sources

    p = Project.find(directory)
    try:
        d = list_sources(p)
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(d, indent=2, ensure_ascii=False))
        return
    if not d["sources"]:
        rprint("no files imported yet")
    else:
        t = Table("id", "platform", "file", "records", "imported at", "query version", "enters dedup", "sample of")
        for r in d["sources"]:
            t.add_row(str(r["source_id"]), escape(r["platform"]), escape(r["file"]), str(r["records"]), local_time(r["imported_at"]),
                      "not recorded" if r["query_version"] is None else str(r["query_version"]),
                      "yes" if r["included"] else "no", "" if r["sample_of"] is None else str(r["sample_of"]))
        rprint(t)
    rprint(f"uploads marked final at {d['final_at']}" if d["final_at"] else "uploads not marked final")


def _set_included(ref: str, included: bool, directory: Path) -> None:
    from .core.project import Project
    from .ingest.importer import set_included

    p = Project.find(directory)
    try:
        r = set_included(p, ref, included)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    word = "enters deduplication" if included else "is kept out of deduplication"
    if r["already"]:
        rprint(f"file {r['source_id']} ({escape(r['file'])}) already {word.replace('is ', '')}; nothing changed")
    else:
        rprint(f"[green]file {r['source_id']} ({escape(r['file'])}) {word}[/green]; the next deduplication run reads "
               f"it (change {r['change_id']}; undo: wider history undo {r['change_id']})")


@import_app.command("include")
def import_include(ref: str = typer.Argument(..., help="The file's id from `wider import list`, or its name when only one imported file has it."),
                   directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Let an imported file enter deduplication again. Listed in History; undo with `wider history undo ID`."""
    _set_included(ref, True, directory)


@import_app.command("exclude")
def import_exclude(ref: str = typer.Argument(..., help="The file's id from `wider import list`, or its name when only one imported file has it."),
                   directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Keep an imported file out of deduplication: it stays listed, in coverage and in the PRISMA counts as not used.
    Refused while later stages hold rows on records only that file brought. Undo with `wider history undo ID`."""
    _set_included(ref, False, directory)


@import_app.command("sample")
def import_sample(ref: str = typer.Argument(..., help="The file's id from `wider import list`, or its name when only one imported file has it."),
                  of: int | None = typer.Option(None, "--of", help="The number of results the database reported; the file is a random sample of them."),
                  clear: bool = typer.Option(False, "--clear", help="The file is a complete export again."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Mark an imported file as a random sample of N results (`--of N`), or as a complete export again (`--clear`).
    `wider query diagnose` and `wider query edits` then count each of its records as N / (records in the sample) and
    say the numbers are estimates. No record changes. Listed in History; undo with `wider history undo ID`."""
    from .core.project import Project
    from .ingest.importer import set_sample

    if (of is None) == (not clear):
        rprint("[red]Give either --of N (the number of results the database reported) or --clear.[/red]")
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        r = set_sample(p, ref, None if clear else of)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    what = (f"a random sample of {r['sample_of']:,} results" if r["sample_of"] is not None else "a complete export")
    if r["already"]:
        rprint(f"file {r['source_id']} ({escape(r['file'])}) is already marked {what}; nothing changed")
    else:
        rprint(f"[green]file {r['source_id']} ({escape(r['file'])}) marked {what}[/green] (change {r['change_id']}; "
               f"undo: wider history undo {r['change_id']})")


@import_app.command("check")
def import_check(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory.")):
    """Sanity table for every export under inputs/: records, format, benchmark hits, vocabulary overlap, verdict."""
    from .core.project import Project
    from .ingest.sanity import check_all

    p = Project.find(directory)
    _print_sanity(check_all(p))
    p.close()


def _print_validity_note(note: dict) -> None:
    """The validity note (D3) when it says more than the hidden-set or both-sets note, which is printed already."""
    from .search.coverage import BOTH_NOTE, HIDDEN_NOTE

    if note["plain"] and not note["lines"]:
        return
    if note["sentence"] not in (HIDDEN_NOTE, BOTH_NOTE):
        rprint(escape(note["sentence"]))
    for line in note["lines"]:
        rprint(f"  {escape(line)}")


@app.command()
def coverage(
    corpus: bool = typer.Option(False, "--corpus", help="Check the deduplicated corpus instead of each platform."),
    validation: bool = typer.Option(False, "--validation", help="Force the validation set even if the query is not frozen and the uploads are not marked final (logged)."),
    count_seen: bool = typer.Option(False, "--count-seen", help="With --validation before the query was frozen or while it is reopened: the validation papers shown count as seen (they no longer validate the search)."),
    keep_validation: bool = typer.Option(False, "--keep-validation", help="With --validation before the query was frozen or while it is reopened: the validation papers shown still count; every output states it."),
    backfill: bool = typer.Option(False, "--backfill-abstracts", help="Copy abstracts from matched records into benchmark papers without one."),
    independent: bool = typer.Option(False, "--independent", help="Recall on independent papers (not from the query's results) and, after screening, Chapman's estimate of what is missing; never a look."),
    protocol: str = typer.Option(None, "--protocol", "-p", help="With --independent: the protocol whose includes the estimate uses (default the most recently screened)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Which benchmark papers did each platform (or the corpus) retrieve? DOI, then exact title, then fuzzy title."""
    from .construct.artifacts import SeenChoiceNeeded
    from .core.project import Project
    from .search.coverage import backfill_benchmark_abstracts, check, set_label

    choice = _seen_flag(count_seen, keep_validation)
    p = Project.find(directory)
    if independent:
        from .recall.independent import check as independent_check

        try:
            for line in independent_check(p, protocol)["lines"]:
                rprint(escape(line))
        except KeyError as e:
            rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
            raise typer.Exit(1) from None
        finally:
            p.close()
        return
    try:
        res = check(p, scope="corpus" if corpus else "platforms", include_validation=True if validation else None,
                    seen_choice=choice)
    except SeenChoiceNeeded as e:   # nothing shown or recorded: the researcher chooses first (D3, Q1)
        _exit_choice(p, e)
    if res["validation_peeked"]:  # check() logged the look in the history
        rprint(f"[yellow]{escape(res['notice'])}[/yellow]")
    elif not res["validation_included"]:
        rprint("[dim]validation set hidden until the synonyms artifact is frozen or the uploads are marked final (wider construct freeze synonyms N, or wider import final)[/dim]")
    _print_validity_note(res["note"])
    for sc, sets in res["scopes"].items():
        rprint(f"[bold]{sc}[/bold]")
        for s, v in sets.items():   # one line per set: found of total and the exact interval (D7)
            rprint(f"  {escape(s)}: {v['text']}")
        t = Table("id", "set", "method", "score", "title")
        for s, v in sets.items():
            for r in v["rows"]:
                m = {"doi": "[green]doi[/green]", "title": "[green]title[/green]",
                     "fuzzy": "[yellow]fuzzy (confirm)[/yellow]", "none": "[red]missing[/red]"}[r["method"]]
                t.add_row(r["benchmark_id"], set_label(s, r.get("former")), m, f"{r['score']:.2f}",
                          r["title"][:70])
        rprint(t)
    if res["scopes"]:
        rprint(res["known_papers_note"])
    if backfill:
        n = backfill_benchmark_abstracts(p)
        rprint(f"backfilled {n} benchmark abstracts from matched records")
    p.close()


# ---------------------------------------------------------------- dedup
dedup_app = typer.Typer(help="Deduplicate imported records: DOI, exact title, corroborated fuzzy title, manual queue.", no_args_is_help=True)
app.add_typer(dedup_app, name="dedup")


@dedup_app.command("run")
def dedup_run(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Run all stages, apply stored manual decisions, rebuild unique records and audit exports."""
    from .core.project import Project
    from .dedup.engine import DedupRerunError, run

    p = Project.find(directory)
    try:
        res = run(p)
    except DedupRerunError as e:
        rprint(f"[red]{e}[/red]")
        p.close()
        raise typer.Exit(1) from e
    s = res["summary"]
    t = Table("metric", "value")
    for k, v in s.items():
        t.add_row(k, str(v))
    rprint(t)
    if s["unresolved_manual_component_pairs_retained"]:
        rprint(f"[yellow]{s['unresolved_manual_component_pairs_retained']} pairs wait for a manual decision "
               f"(wider dedup queue, or the Deduplication page in wider ui)[/yellow]")
    if s.get("uncertain_manual_component_pairs"):
        rprint(f"[yellow]{s['uncertain_manual_component_pairs']} pairs are decided uncertain; the PRISMA count of records "
               f"remaining stays provisional until they are decided[/yellow]")
    for line in res.get("set_aside", []):   # two screened records merged: whose screening was kept (also in History)
        rprint(line)
    rprint(f"audit files in {p.path('exports', 'dedup')}")
    _print_stage_result(p, "dedup")
    p.close()


@dedup_app.command("queue")
def dedup_queue(limit: int | None = typer.Option(None, "--limit", help="Pairs to show (default 30; with --all every pair)."),
                all_: bool = typer.Option(False, "--all", help="The last run's whole queue: open and decided pairs, with status and name."),
                directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Show pending manual pairs (highest priority first); --all lists the decided ones too, as the Dedup page does."""
    from .core.project import Project
    from .dedup.engine import decided_since_run, flow_counts, queue_all, queue_rows

    p = Project.find(directory)
    print(flow_counts(p)["message"])   # the sentence beside the Dedup page's flow chart
    if all_:
        rows = queue_all(p)
        n_open = sum(r["status"] == "open" for r in rows)
        rprint(f"{n_open} open, {len(rows) - n_open} decided")
        for r in rows[:limit]:   # --all is the whole list, as on the page; --limit cuts it with a note
            print(f"{r['raw_a']} {r['raw_b']}  {r['text']}  ({r['reason_words']})  {(r['title_a'] or '')[:60]}")
        if limit is not None and len(rows) > limit:
            print(f"... {len(rows) - limit} more {'pair' if len(rows) - limit == 1 else 'pairs'} not shown (--limit {limit})")
        p.close()
        return
    rows = queue_rows(p)
    since = decided_since_run(p)   # as the Dedup page heading: decided by hand, applied by the next run
    rprint(f"{len(rows)} pending pairs" + (f" · {since} decided since the last run (run `wider dedup run` to apply)" if since else ""))
    limit = 30 if limit is None else limit
    t = Table("a", "b", "reason", "sim", "title a / title b", "platforms", "years")
    for r in rows[:limit]:
        t.add_row(str(r["raw_a"]), str(r["raw_b"]), r["stage"], f"{(r['score'] or 0):.2f}",
                  f"{(r['title_a'] or '')[:60]}\n{(r['title_b'] or '')[:60]}", f"{r['platform_a']}/{r['platform_b']}",
                  f"{r['year_a']}/{r['year_b']}")
    rprint(t)
    if len(rows) > limit:
        print(f"... {len(rows) - limit} more {'pair' if len(rows) - limit == 1 else 'pairs'} not shown (--limit {limit}, or --all for every pair)")
    p.close()


@dedup_app.command("merged")
def dedup_merged(limit: int = typer.Option(30, "--limit", help="Pairs to show."),
                 as_json: bool = typer.Option(False, "--json", help="Print JSON."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """The pairs the last run merged, one per record it removed, with the reason or who decided."""
    from .core.project import Project
    from .dedup.engine import merged_pairs

    p = Project.find(directory)
    res = merged_pairs(p, limit=limit)
    p.close()
    if as_json:
        print(json.dumps(res, indent=2, default=str))
        return
    if res["approximate"]:
        print("(approximate: this run was made before WIDER kept its list; run deduplication again for an exact list)")
    rprint(f"{res['total']} merged pairs" + (f" (showing {len(res['rows'])})" if len(res["rows"]) < res["total"] else ""))
    for r in res["rows"]:
        print(f"{r['a']} {r['b']}  {r['text']}  {(r['title_a'] or '')[:60]}")


@dedup_app.command("records")
def dedup_records(limit: int = typer.Option(30, "--limit", help="Records to show."),
                  as_json: bool = typer.Option(False, "--json", help="Print JSON."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """The records left after the last run, with the number of imported records each was made from."""
    from .core.project import Project
    from .dedup.engine import unique_records

    p = Project.find(directory)
    rows = unique_records(p, limit=limit)
    total = p.store.one("SELECT COUNT(*) n FROM records")["n"]
    p.close()
    if as_json:
        print(json.dumps({"rows": rows, "total": total}, indent=2, default=str))
        return
    rprint(f"{total} records left" + (f" (showing {len(rows)})" if len(rows) < total else ""))
    for r in rows:
        print(f"{r['record_id']}  {(r['title'] or '')[:70]}  {r['authors_short']}  {r['year'] or ''}  "
              f"from {r['n_raw']} imported record{'s' if r['n_raw'] != 1 else ''}")


@dedup_app.command("decide")
def dedup_decide(raw_a: int, raw_b: int, decision: str = typer.Argument(..., help="duplicate | not_duplicate | unsure"),
                 by: str = typer.Option("", "--by", help="Who decided (stored in the audit trail)."),
                 keep: str = typer.Option("", "--keep", help="duplicate only: A or B (RAW_A or RAW_B), the record whose title, authors, year, journal and DOI the merged record shows."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Record a manual decision for a pair; rerun `wider dedup run` to apply."""
    from .core.project import Project
    from .dedup.engine import decide

    k = keep.strip().upper()
    kept = raw_a if k == "A" else raw_b if k == "B" else None
    if k and kept is None:
        rprint(f"[red]--keep must be A or B, not {keep}[/red]")
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        decide(p, raw_a, raw_b, decision, by=by, keep=kept)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        p.close()
        raise typer.Exit(1) from e
    rprint(f"recorded {'unsure' if decision == 'uncertain' else decision} for {raw_a}/{raw_b}" + (f", keeping raw record {kept}" if kept is not None else ""))
    p.close()


@dedup_app.command("import-decisions")
def dedup_import_decisions(file: Path, directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Import decisions from a 05_manual_duplicate_review.csv (this tool's or the original R pipeline's format)."""
    from .core.project import Project
    from .dedup.engine import import_decisions_csv

    p = Project.find(directory)
    try:
        rprint(import_decisions_csv(p, file))
    except (ValueError, OSError) as e:
        rprint(f"[red]{e}[/red]")
        p.close()
        raise typer.Exit(1) from e
    p.close()


@dedup_app.command("prisma")
def dedup_prisma(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """PRISMA identification counts from the last run."""
    from .core.project import Project
    from .dedup.engine import flow_counts, prisma_rows, removed_line

    p = Project.find(directory)
    t = Table("stage", "n")
    for label, n in prisma_rows(p):
        t.add_row(label, str(n))
    rprint(t)
    f = flow_counts(p)   # the flow chart's "Duplicates removed" and "Records imported" lines
    if f["has_run"]:
        print(removed_line(f))
        if f["imported_since_run"]:
            rprint(f"+{f['imported_since_run']} more imported since, not checked for duplicates yet")
    p.close()


# ---------------------------------------------------------------- enrich
@app.command()
def enrich(
    limit: int = typer.Option(None, "--limit", help="Stop after this many records (resumable)."),
    retry: bool = typer.Option(False, "--retry", help="Also try failed and exhausted records again (earlier attempts stay logged)."),
    status_only: bool = typer.Option(False, "--status", help="Only print the summary."),
    versions_of: int = typer.Option(None, "--versions", metavar="RECORD_ID",
                                    help="List every abstract the record has had (no lookup, no consent asked)."),
    records_of: str = typer.Option(None, "--records", metavar="GROUP",
                                   help="List the records of one group (no lookup, no consent asked): waiting, failed, "
                                        "nowhere, or the source that found the abstract (openalex, crossref, europepmc, "
                                        "semantic_scholar, openalex_title)."),
    as_json: bool = typer.Option(False, "--json", help="With --versions or --records: print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Fetch missing or thin abstracts for unique records (OpenAlex, Crossref, Europe PMC, Semantic Scholar).

    A Semantic Scholar API key is read from the environment variable S2_API_KEY (its value is never shown); without
    it lookups still work, but Semantic Scholar may limit the rate, and records it turned away wait for --retry."""
    from .core.project import Project
    from .enrich.fetch import candidates, summary
    from .enrich.fetch import enrich as run_enrich

    if limit is not None and limit < 1:
        raise typer.BadParameter("must be at least 1", param_hint="--limit")
    p = Project.find(directory)
    if versions_of is not None:   # read from the store before any consent or lookup (reviewer-roles-and-blinding D8)
        from .enrich.abstracts import record_versions
        try:
            out = record_versions(p, versions_of)
        except KeyError as e:
            _fail(p, e)
        p.close()
        if as_json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return
        rprint(f"Abstract versions of record {versions_of}: {len(out['versions'])}")
        for v in out["versions"]:
            print(f"  {v['version']}  {v['source_word']}, {v['words'] or 0} words, {(v['at'] or 'date not recorded')[:10]}"
                  + ("  (current)" if v["version"] == out["current"] else ""))
        return
    if records_of is not None:   # the Missing abstracts page's records list (owner walkthrough M6/M7)
        from .enrich.fetch import records_by_state
        try:
            out = records_by_state(p, records_of, limit=limit or 50)
        except ValueError as e:
            _fail(p, e)
        p.close()
        if as_json:
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return
        print(f"Still without an abstract: {out['missing']}; abstract found: {out['found']}")
        for k, n in out["counts"].items():
            print(f"  {out['labels'][k]}: {n}")
        print(f"{out['label']}: showing {len(out['rows'])} of {out['counts'][records_of]}, sorted by first author")
        for r in out["rows"]:
            print(f"  {r['record_id']}  {r['first_author'] or '?'} {r['year'] or ''} · {r['title'] or ''}"
                  f"  ({'DOI ' + r['doi'] if r['doi'] else 'no DOI'})\n      {r['what']}")
        return
    if not status_only:
        todo = len(candidates(p, retry=retry))
        ensure_consent(p, "enrich", min(todo, limit) if limit else todo)
        if retry:
            rprint(f"retrying {todo - len(candidates(p))} records (failed or exhausted earlier; earlier attempts stay logged)")
        stats = run_enrich(p, limit=limit, retry=retry, progress=lambda i, n, s: rprint(f"{i}/{n} {s}") if i % 25 == 0 or i == n else None)
        rprint(stats)
    s = summary(p)
    rprint(s)
    print(s["s2_key_note"])
    if not status_only:
        _print_stage_result(p, "enrich")
    p.close()


# ---------------------------------------------------------------- embed corpus / rank / ablate / cluster
@embed_app.command("corpus")
def embed_corpus_cmd(
    name: str = typer.Option(None, "--embedding", "-e", help="Embedding name from project.yaml (default: project default)."),
    allow_download: bool = typer.Option(False, "--allow-download", help="Let a local embedding model download its weights if they are not cached."),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the cost range and exit without calling a model."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Embed every unique record (title + abstract + cleaned keywords); cached and resumable."""
    from .cli_backend_consent import ensure_backend_consent
    from .core.backends import is_downloaded
    from .core.estimate import estimate_embed
    from .core.project import Project
    from .rank.corpus import embed_corpus
    from .rank.score import corpus_overview

    p = Project.find(directory)
    cfg = p.embedding(name)
    _estimate(p, lambda: estimate_embed(p, name), estimate_only)
    if cfg.kind == "sentence_transformers" and not allow_download and is_downloaded(cfg) is False:
        rprint(f"[yellow]{cfg.model} is not downloaded; rerun with --allow-download[/yellow]")
        raise typer.Exit(1)
    from .cli_lookups import say_sent
    say_sent(p, "embed", cfg.name)
    ensure_backend_consent(p, cfg, "research", embedding=True)
    ov = corpus_overview(p)
    rprint(f"{ov['records']} unique records ({ov['thin']} thin) → {cfg.name} ({cfg.model})")
    res = embed_corpus(p, name, progress=lambda i, n: rprint(f"  {i}/{n}") if i % 1024 == 0 or i == n else None)
    rprint(res)
    _print_stage_result(p, "rank")
    p.close()


rank_app = typer.Typer(help="Anchor-based ranking of the corpus.", no_args_is_help=True)
app.add_typer(rank_app, name="rank")


@rank_app.command("run")
def rank_run(
    embedding: str = typer.Option(None, "--embedding", "-e", help="Embedding set to score (default: project default)."),
    anchors_version: int = typer.Option(None, "--anchors", "-a", help="Anchors version (default: newest)."),
    top_n: str = typer.Option(None, "--top-n", help="Cells averaged per record (default: protocol.ranking.top_n_cells)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Score every record by its top-N cell similarities and store the ranking."""
    from .cli_backend_consent import ensure_embedding_research
    from .core.project import Project
    from .rank.score import rank

    if top_n is not None and not re.fullmatch(r"-?\d+", top_n.strip()):   # the page's sentence (POST /api/rank/run)
        rprint(f"[red]{escape(f'top-N must be a whole number from 1 to the number of cells (got {top_n[:20]!r})')}[/red]")
        raise typer.Exit(1)
    from .cli_lookups import say_sent

    p = Project.find(directory)
    say_sent(p, "embed_short", embedding)
    ensure_embedding_research(p, embedding)   # the anchors are embedded
    try:
        res = rank(p, set_name=embedding, anchors_version=anchors_version, top_n=None if top_n is None else int(top_n))
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    rprint(res)
    from .rank.score import keep_note
    if keep_note(res):   # ui-redesign D5: said, not asked
        rprint(escape(keep_note(res)))
    _print_stage_result(p, "rank")
    p.close()


@rank_app.command("update")
def rank_update(
    use_older: bool = typer.Option(False, "--use-older-embeddings", help="Rank from the stored embeddings even though "
                                   "they were made from older texts; the ranking keeps \"needs review\"."),
    allow_download: bool = typer.Option(False, "--allow-download", help="Let a local embedding model download its "
                                        "weights if they are not cached."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm the note on what re-embedding sends and costs "
                             "(the backend's consent is still needed)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Update the ranking: rank again, first embedding the changed texts again when the embeddings were made from
    older texts. Earlier versions and existing samples are kept."""
    from .cli_backend_consent import ensure_backend_consent, ensure_embedding_research
    from .cli_lookups import _interactive
    from .core.project import Project
    from .rank.update import check, update

    p = Project.find(directory)
    try:
        chk = check(p)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(39)))}[/red]")
        p.close()
        raise typer.Exit(1) from None
    if chk["keep_note"]:
        rprint(escape(chk["keep_note"]))
    from .cli_lookups import say_sent
    if not chk["embeddings_match"] and not use_older:
        say_sent(p, "embed")
        rprint(escape(chk["notice"]))
        if chk["needs_download"] and not allow_download:
            rprint(f"[yellow]{escape(chk['model'])} is not downloaded; rerun with --allow-download[/yellow]")
            p.close()
            raise typer.Exit(1)
        if not yes and not (_interactive() and typer.confirm("Embed the changed texts again and rank?", default=False)):
            rprint("[yellow]nothing was sent and no version was added; rerun with --yes to confirm, or with "
                   "--use-older-embeddings to rank from the older embeddings[/yellow]")
            p.close()
            raise typer.Exit(1)
        ensure_backend_consent(p, p.embedding(None), "research", embedding=True)   # before any text is sent
    ensure_embedding_research(p, None)   # the anchors are embedded
    try:
        res = update(p, use_older=use_older,
                     progress=lambda i, n: rprint(f"  {i}/{n}") if i % 1024 == 0 or i == n else None)
    except Exception as e:   # noqa: BLE001 - a failed embedding adds no version and changes nothing
        rprint(f"[red]{escape(f'the update failed: {e}')}[/red]")
        rprint("[red]no version was added; the samples and earlier versions are unchanged[/red]")
        p.close()
        raise typer.Exit(1) from None
    if res.get("note"):
        rprint(f"[yellow]{escape(res['note'])}[/yellow]")
    rprint(escape(f"ranking {res['name']} (version {res['version']})" + (", reused: the inputs are unchanged"
                                                                          if res["reused"] else "")))
    _print_stage_result(p, "rank")
    p.close()


@rank_app.command("keep")
def rank_keep(
    version: int = typer.Option(None, "--version", min=1, help="The ranking version to keep (needed when several "
                                "versions need review)."),
    by: str = typer.Option(None, "--by", help="Who keeps it (default: WIDER_REVIEWER or the login name)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Keep a ranking whose records or settings changed since it was made: clears "needs review" until they change
    again, is undoable in History and is named in the methods draft. Nothing is rerun."""
    from .core.project import Project
    from .rank.versions import keep

    p = Project.find(directory)
    try:
        r = keep(p, version, author=by)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    rprint(escape(r["message"]))


@rank_app.command("validate")
def rank_validate(
    ranking: str = typer.Option(None, "--ranking", help="Ranking name (default: newest)."),
    validation: bool = typer.Option(False, "--validation", help="Include the validation set even if the query is not frozen and the uploads are not marked final (logged)."),
    count_seen: bool = typer.Option(False, "--count-seen", help="With --validation before the query was frozen or while it is reopened: the validation papers shown count as seen (they no longer validate the search)."),
    keep_validation: bool = typer.Option(False, "--keep-validation", help="With --validation before the query was frozen or while it is reopened: the validation papers shown still count; every output states it."),
    as_json: bool = typer.Option(False, "--json", help="Print the result as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Where each benchmark paper lands in the ranking (ranks for top-1/2/3 scoring)."""
    from .construct.artifacts import SeenChoiceNeeded
    from .core.project import Project
    from .rank.score import validate
    from .search.coverage import set_label

    choice = _seen_flag(count_seen, keep_validation)
    p = Project.find(directory)
    try:
        v = validate(p, ranking, include_validation=True if validation else None, seen_choice=choice)
    except SeenChoiceNeeded as e:   # nothing shown or recorded: the researcher chooses first (D3, Q1)
        _exit_choice(p, e)
    except KeyError as e:   # no ranking yet, or no ranking of that name
        rprint(f"[red]{escape(e.args[0])}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(v, default=str))
        return
    if v["validation_peeked"]:  # validate() logged the look in the history, as coverage does
        rprint(f"[yellow]{escape(v['notice'])}[/yellow]")
    ns = list(v["top_n_compare"])   # one column per compared top-N (protocol: ranking.top_n_compare)
    t = Table("id", "set", "rank", *[f"top{n}" for n in ns], "best cell", "title")
    for r in sorted(v["rows"], key=lambda x: x.get("rank", 10**9)):
        if r["found"]:
            t.add_row(r["benchmark_id"], set_label(r["set"], r.get("former")), str(r["rank"]), *[str(r[f"rank_top{n}"]) for n in ns],
                      r["best_cell"], r["title"][:55])
        else:
            t.add_row(r["benchmark_id"], set_label(r["set"], r.get("former")), "[red]not in corpus[/red]", *[""] * len(ns), "", r["title"][:55])
    rprint(t)
    pct = Table("set", *[f"top {h['pct']:g}% (rank {h['rank']})" for h in next(iter(v["summary"].values()), {}).get("recall_at_pct", [])])
    for s_name, m in v["summary"].items():
        pct.add_row(s_name, *[f"{h['k']} of {h['n']}" for h in m.get("recall_at_pct", [])])
    rprint(pct)
    rprint(v["summary"])
    rprint("median rank by top-N:", v["top_n_compare"])
    for note in v.get("notes", []):
        rprint(f"[yellow]{note}[/yellow]")


@rank_app.command("show")
def rank_show(
    ranking: str = typer.Option(None, "--ranking", help="Ranking name (default: newest)."),
    cell: str = typer.Option(None, "--cell", help="Only records whose best cell is this one."),
    offset: int = typer.Option(0, "--offset", min=0, help="Rows to skip (0 = start at rank 1)."),
    limit: int = typer.Option(50, "--limit", min=1, help="Rows to show."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Page through a ranking (the explorer of the Ranking page), optionally one cell's records."""
    from .core.project import Project
    from .rank.score import corpus_overview, explorer

    p = Project.find(directory)
    d = explorer(p, ranking, offset=offset, limit=limit, cell=cell)
    corpus = corpus_overview(p)   # the line the page shows above its explorer
    p.close()
    sets = ", ".join(f"{escape(s['set_name'])} {s['dim']}d" for s in corpus["sets"]) or "none"
    rprint(f"{corpus['records']} unique records ({corpus['thin']} thin) · embedding sets: {sets}")
    if d["ranking"] is None:
        rprint("[red]no ranking yet: run `wider rank run` first[/red]" if ranking is None else f"[red]no ranking {escape(ranking)}[/red]")
        raise typer.Exit(1)
    if cell and cell not in d["best_cell_top3000"]:
        rprint(f"[red]no cell {escape(cell)}; cells: {', '.join(d['best_cell_top3000'])}[/red]")
        raise typer.Exit(1)
    t = Table("rank", "score", "best cell", "cell scores", "title")
    for r in d["rows"]:
        t.add_row(str(r["rank"]), f"{r['score']:.3f}", r["best_cell"],
                  " ".join(f"{c} {s:.2f}" for c, s in sorted(r["cell_scores"].items(), key=lambda x: -x[1])),   # best first, as the page
                  escape((r["title"] or "")[:60]))
    if not d["n_rows"]:   # an empty filter says so, not "none of 0"
        rprint(f"no record has {escape(cell)} as its best cell" if cell else "the ranking holds no records")
    else:
        rprint(t)
        shown = f"{offset + 1}–{offset + len(d['rows'])}" if d["rows"] else "none"
        rprint(f"{shown} of {d['n_rows']}" + (f" records best matching {escape(cell)}" if cell else " records"))
    rprint(f"best cell of the top {d['top_pool']}: " + ", ".join(f"{c} {n}" for c, n in d["best_cell_top3000"].items()))
    vs = d["versions"]   # the page's version history fold, review box and note before Rank (ui-redesign D5)
    if len(vs) > 1:
        rprint(escape(f"{len(vs)} versions: " + "; ".join(
            f"version {v['version']}" + (f" ({', '.join(v['samples'])})" if v["samples"] else "")
            + (" (newest)" if i == len(vs) - 1 else "") for i, v in enumerate(vs))))
    for it in d["review"]:
        names = ", ".join(s["name"] for s in it["samples"])
        line = f"needs review: version {it['version']}" + (f" ({names})" if names else "") + f": {it['reason']}"
        rprint(f"[yellow]{escape(line)}[/yellow]")
    if d["review"]:
        rprint(escape("keep it with `wider rank keep" + (" --version N" if len(d["review"]) > 1 else "")
                      + "`, or update it with `wider rank update`"))
    if d["keep_before"]:
        rprint(escape(d["keep_before"]))


@rank_app.command("ablate")
def rank_ablate(
    validation: bool = typer.Option(False, "--validation", help="Also report the validation set beside the development set; while it is hidden (the query not frozen, the uploads not marked final) this is a look, logged in the history."),
    count_seen: bool = typer.Option(False, "--count-seen", help="With --validation before the query was frozen or while it is reopened: the validation papers shown count as seen (they no longer validate the search)."),
    keep_validation: bool = typer.Option(False, "--keep-validation", help="With --validation before the query was frozen or while it is reopened: the validation papers shown still count; every output states it."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Rank with every embedding set × anchors version × top-N and compare benchmark recovery."""
    from .cli_backend_consent import ensure_embedding_research
    from .construct.artifacts import SeenChoiceNeeded
    from .core.backend_consent import ablate_embeddings
    from .core.project import Project
    from .rank.ablate import ablation, choice_needed, column_label

    choice = _seen_flag(count_seen, keep_validation)
    p = Project.find(directory)
    notice = choice_needed(p, validation) if choice is None else None
    if notice:   # nothing ranked or recorded: the researcher chooses first (D3, Q1)
        _exit_choice(p, SeenChoiceNeeded(notice))
    from .cli_lookups import say_sent
    for name in ablate_embeddings(p):   # each set embeds the anchors
        say_sent(p, "embed_short", name)
        ensure_embedding_research(p, name)
    try:
        res = ablation(p, include_validation=True if validation else None, seen_choice=choice)
    except SeenChoiceNeeded as e:
        _exit_choice(p, e)
    rows = res["rows"]
    if not rows:
        rprint("[red]nothing to ablate: embed the corpus and add anchors first[/red]")
        raise typer.Exit(1)
    if res["validation_peeked"]:   # the look is logged in the history, as coverage does
        rprint(f"[yellow]{escape(res['notice'])}[/yellow]")
    cols = list(rows[0].keys())
    t = Table(*[column_label(c) for c in cols])   # the same headers as the Ranking page
    for r in rows:
        t.add_row(*[str(r.get(c, "")) for c in cols])
    rprint(t)
    p.close()


@rank_app.command("cluster")
def rank_cluster(k: int = typer.Option(None, "--k", help="Clusters (default: protocol.ranking.cluster_k)."), top: int = typer.Option(None, "--top", help="Ranking depth clustered (default: protocol.ranking.cluster_top)."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """k-means on the top-ranked records with a split-half stability check."""
    from .core.project import Project
    from .rank.cluster import cluster

    p = Project.find(directory)
    res = cluster(p, k=k, top=top)
    rprint(f"split-half adjusted Rand index: {res['split_half_ari']}")
    t = Table("cluster", "n", "median rank", "terms", "medoid")
    for c in res["clusters"]:
        t.add_row(str(c["cluster"]), str(c["n"]), str(c["median_rank"]), ", ".join(c["terms"]), (c["medoid"] or {}).get("title", "")[:60])
    rprint(t)
    p.close()


# ---------------------------------------------------------------- sample + label
sample_app = typer.Typer(help="Draw the probability (validation) and purposive (development) samples.", no_args_is_help=True)
app.add_typer(sample_app, name="sample")


@sample_app.command("draw")
def sample_draw(
    which: str = typer.Argument("both", help="probability | development | both"),
    ranking: str = typer.Option(None, "--ranking", help="Ranking to sample from (default: newest)."),
    seed: int = typer.Option(None, "--seed", help="Random seed for the draw (default: the project seed)."),
    name: str = typer.Option(None, "--name", help="Name of the probability sample (default: "
                             "protocol.sampling.probability_sample)."),
    fresh_from: str = typer.Option(None, "--fresh-from", help="Draw a fresh probability sample that leaves out this "
                                   "used sample and its predecessors; recall then reads the new one."),
    author: str = typer.Option(None, "--author", help="Who draws (recorded in History)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Draw the band-stratified probability sample and/or the purposive development set from the ranking."""
    from .cli_backend_consent import ensure_embedding_research
    from .core.backend_consent import ranking_embedding
    from .core.project import Project
    from .sample.design import FRESH_ONLY, draw_development, draw_probability

    p = Project.find(directory)
    if (name or fresh_from) and which != "probability":
        rprint(f"[red]{FRESH_ONLY}[/red]")
        raise typer.Exit(1)
    set_name = ranking_embedding(p, ranking) if which in ("development", "both") else None
    if set_name:   # near-miss and adjacent texts are embedded
        from .cli_lookups import say_sent
        say_sent(p, "embed_short", set_name)
        ensure_embedding_research(p, set_name)
    try:
        if which in ("probability", "both"):
            r = draw_probability(p, ranking, name=name, seed=seed, fresh_from=fresh_from, author=author)
            rprint(f"[green]{r['name']}[/green]: {r['n']} records; bands " +
                   ", ".join(f"{b['band']} {b['n_h']}/{b['N_h']} (" + (f"w={_w2(b['weight'])}" if b['weight'] is not None else "used up") + ")" for b in r["design"]["bands"]))
            if r["development_excluded"]:
                rprint(f"{r['development_excluded']} development records left out of the frame")
            for m in r["messages"]:
                rprint(m)
        if which in ("development", "both"):
            r = draw_development(p, ranking, seed=seed)
            pools = {}
            for row in p.store.query("SELECT pool FROM sample_rows WHERE sample_id=?", (r["sample_id"],)):
                pools[row["pool"].split(":")[0]] = pools.get(row["pool"].split(":")[0], 0) + 1
            rprint(f"[green]{r['name']}[/green]: {r['n']} records; pools {pools}")
            for m in r.get("messages") or []:
                rprint(m)
        _print_stage_result(p, "sample")
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@sample_app.command("import")
def sample_import(name: str, file: Path, kind: str = typer.Option("probability", "--kind", help="probability | purposive"),
                  reviewer: str = typer.Option("original", "--reviewer", help="Reviewer id to store the imported labels under."),
                  independent: str = typer.Option(None, "--independent", metavar="BASIS",
                                                  help="State how the labels were made without seeing the model's output."),
                  by: str = typer.Option(None, "--by", help="Who states --independent; defaults to $WIDER_REVIEWER, then $USER."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Create a locked sample from a CSV labelled elsewhere (title/doi, decision; band, N_h, n_h or weight)."""
    from .core.project import Project
    from .sample.labels import import_sample_csv

    p = Project.find(directory)
    try:
        rprint(import_sample_csv(p, name, file, kind=kind, reviewer_id=reviewer, independent=independent, by=by))
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@sample_app.command("status")
def sample_status(as_json: bool = typer.Option(False, "--json", help="Print JSON (the same as GET /api/samples)."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Samples, their design, labelling progress and, for probability samples, the labels' criteria state."""
    from .core.project import Project
    from .sample.design import boundary_line, sampling_log
    from .sample.labels import criteria_counts, progress, status_keys
    from .screen.review import reviewed_line, reviewed_of_sample

    p = Project.find(directory)
    logs = sampling_log(p)
    if as_json:
        out = {k: {**v, "progress": progress(p, k), "reviewed_with_llm_reads": reviewed_of_sample(p, k),
                   **status_keys(p, k),
                   **({"criteria": criteria_counts(p, k)} if v.get("kind") == "probability" else {})}
               for k, v in logs.items()}
        p.close()
        typer.echo(json.dumps({"samples": out}, default=str))
        return
    for name, d in logs.items():
        # imported samples (wider sample import) carry no ranking or seed in their design
        rprint(f"[bold]{name}[/bold] ({d['kind']}, n={d['n']}, {'locked' if d['locked'] else 'not locked'}, "
               f"ranking {d.get('ranking') or 'imported'}, seed {d.get('seed', '—')})"
               + (", in use for recall" if d.get("in_use") else ""))
        for who, c in progress(p, name)["reviewers"].items():
            parts = ", ".join(f"{v} {k}" for k, v in c.items() if k != "done")
            rprint(f"  {escape(who)}: {c.get('done', 0)} of {d['n']} labelled" + (f" ({parts})" if parts else ""))
        if not progress(p, name)["reviewers"]:
            rprint(f"  no labels yet (0 of {d['n']})")
        for line in status_keys(p, name)["completeness"]["lines"]:   # what is left before every label is final (D3)
            rprint(f"  {escape(line)}")
        seen = reviewed_line(reviewed_of_sample(p, name))   # decided after seeing the LLM's reads (manual-review D13)
        if seen:
            rprint(f"  {seen}")
        b = boundary_line(d)   # the boundary pool's log line, with any extension (D8, D9)
        if b is not None:
            rprint(f"  boundary: {escape(b)}")
        if d.get("kind") == "probability":
            for line in criteria_counts(p, name)["lines"]:
                rprint(f"  {escape(line)}")
    p.close()


@sample_app.command("extend")
def sample_extend(
    name: str = typer.Argument(..., help="The unlocked purposive sample to extend (e.g. development)."),
    pool: str = typer.Option("boundary", "--pool", help="The pool to add; only boundary for now."),
    author: str = typer.Option(None, "--author", help="Who extended the sample (default: the configured author)."),
    yes: bool = typer.Option(False, "--yes", help="Confirm without asking: after a corpus round, the added records "
                             "hide development model output until they are labelled."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Add the boundary pool to an unlocked purposive sample: records close to both a criterion and a near-miss or
    adjacent entry, shuffled in among its unlabelled records. Labels stay; undo removes the added records. After a
    corpus round it first says what the added records hide until labelled and asks to confirm (--yes confirms)."""
    from .cli_backend_consent import ensure_embedding_research
    from .cli_lookups import say_sent
    from .core.project import Project
    from .sample.design import ExtendNeedsConfirm, extend_development

    p = Project.find(directory)
    try:
        def ask(said: str) -> bool:
            if as_json:
                return False      # --json never prompts: the refusal says to pass --yes
            typer.echo(said)
            return typer.confirm("Add them?", default=False)

        r = extend_development(p, name, pool, author=author, before_embedding=lambda s: (say_sent(p, "embed_short", s, quiet=as_json),
                                                                       ensure_embedding_research(p, s)),
                               confirm=True if yes else ask)
    except ExtendNeedsConfirm as e:
        _fail(p, ValueError(f"{e} Nothing was changed; pass --yes to add them." if as_json else "Nothing was changed."))
    except (ValueError, KeyError) as e:
        _fail(p, e)
    r = {k: v for k, v in r.items() if k != "change_id"}
    if as_json:
        typer.echo(json.dumps(r, ensure_ascii=False))
    else:
        if r["added"]:
            typer.echo(f"{name}: {r['added']} record(s) added, now {r['n']}: "
                       + ", ".join(f"{k} {v}" for k, v in r["pools"].items()))
        for m in r["messages"]:
            typer.echo(m)
    p.close()


@sample_app.command("lock")
def sample_lock(name: str, by: str | None = typer.Option(None, "--by", help="Who locks it (default: WIDER_REVIEWER or your login)."),
                directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Lock a sample: its records are fixed (no redraw or extension); missing labels and adjudications are still
    accepted, and a change to a saved one is a correction with a reason. The key (band, pool, rank) may be revealed.
    Undo it in History until a recall result on it is seen."""
    from .core.project import Project
    from .sample.labels import lock

    p = Project.find(directory)
    try:
        lock(p, name, by)
    except KeyError as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    rprint(f"locked {name}")


@sample_app.command("unlock")
def sample_unlock(name: str, by: str | None = typer.Option(None, "--by", help="Who unlocks it (default: WIDER_REVIEWER or your login)."),
                  yes: bool = typer.Option(False, "--yes", help="Unlock although its recall result was seen."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Unlock a sample so it can be redrawn or extended again and saved labels change without a correction reason. An unlock is final and recorded in History,
    the methods draft and the replication package."""
    from .core.project import Project
    from .sample.labels import unlock

    p = Project.find(directory)
    try:
        r = unlock(p, name, by, yes)
    except KeyError as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    if r["warning"]:
        typer.echo(r["warning"])
    if not r["unlocked"]:
        if r["warning"]:
            typer.echo("Nothing changed; add --yes to unlock it.")
            raise typer.Exit(2)
        typer.echo(f"{name} is not locked")
        return
    rprint(f"unlocked {name}")


label_app = typer.Typer(help="Reviewer labels: queue export, CSV import, agreement, adjudication.", no_args_is_help=True)
app.add_typer(label_app, name="label")


@label_app.command("export")
def label_export(sample: str, out: Path, reviewer: str = typer.Option("", "--reviewer", help="Export only this reviewer's labels (default: all)."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Write the blinded queue of a sample as CSV (fill the decision column, then `wider label import`)."""
    from .core.project import Project
    from .sample.labels import export_csv

    p = Project.find(directory)
    n = export_csv(p, sample, out, reviewer_id=reviewer or None)
    rprint(f"wrote {n} rows to {out}")
    p.close()


@label_app.command("import")
def label_import(sample: str = typer.Argument(None, help="The sample (with FILE)."),
                 file: Path = typer.Argument(None, help="The CSV to import into SAMPLE."),
                 queue_file: Path = typer.Option(None, "--queue", help="A filled re-check or second-reviewer export (tokens resolved across samples)."),
                 reviewer: str = typer.Option(None, "--reviewer", help="Reviewer id (default: from Rayyan notes, else 'import'; with --queue, the token's own)."),
                 overwrite: bool = typer.Option(False, "--overwrite", help="Replace labels already stored for the same reviewer."),
                 independent: str = typer.Option(None, "--independent", metavar="BASIS",
                                                 help="State how the file's labels were made without seeing the model's output."),
                 correction_reason: str = typer.Option(None, "--correction-reason", metavar="WHY",
                                                       help="Why saved labels on a locked sample or sealed records change (500 characters at most)."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Import decisions from this tool's CSV, a Rayyan export, or any CSV with decision + title/doi columns; or, with
    --queue, a filled `label recheck` / `label second-reviewer` export."""
    from .core.project import Project
    from .sample.labels import import_csv, import_queue_csv

    by_queue = queue_file is not None and sample is None and file is None
    if not by_queue and (queue_file is not None or sample is None or file is None):
        rprint("[red]give either SAMPLE FILE or --queue FILE[/red]")
        raise typer.Exit(1)
    if by_queue and independent is not None:
        rprint("[red]--independent is for SAMPLE FILE; a queue export's labels are made in a blind queue[/red]")
        raise typer.Exit(1)
    p = Project.find(directory)
    try:
        if queue_file is not None:
            rprint(import_queue_csv(p, queue_file, reviewer=reviewer, correction_reason=correction_reason))
        else:
            rprint(import_csv(p, sample, file, reviewer_id=reviewer, overwrite=overwrite, independent=independent,
                              correction_reason=correction_reason))
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@label_app.command("add")
def label_add(sample: str = typer.Argument(..., help="The sample."),
              record_id: int = typer.Argument(..., help="The record (its id, as the unblinded queue shows it)."),
              decision: str = typer.Argument(..., help="include, exclude or maybe."),
              reviewer: str = typer.Option(..., "--reviewer", help="Reviewer id."),
              reason: str = typer.Option("", "--reason", help="Why (optional)."),
              note: str = typer.Option("", "--note", help="A note (optional)."),
              correction_reason: str = typer.Option(None, "--correction-reason", metavar="WHY",
                                                    help="Why a saved label on a locked sample or sealed record changes."),
              directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Save one label by sample and record, as the unblinded queue's Save does (POST /api/labels): the record's band
    and rank were in view, so the label is recorded as made in the unblinded queue."""
    from .core.project import Project
    from .sample.labels import CorrectionNeeded, label

    p = Project.find(directory)
    try:
        label(p, sample, record_id, reviewer, decision, reason, None, note, made_in="unblinded queue",
              correction_reason=correction_reason)
    except CorrectionNeeded as e:
        rprint(f"[red]{escape(str(e))}[/red] (give --correction-reason)")
        raise typer.Exit(1)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    rprint(f"saved {decision} for record {record_id} in {escape(sample)} by {escape(reviewer)}")


def _queue_out(out: dict, export: Path | None, as_json: bool) -> None:
    from .sample.labels import export_queue_csv
    if export is not None:
        export_queue_csv(out, export)
    if as_json:
        typer.echo(json.dumps(out, default=str))
        return
    for line in out["lines"]:
        rprint(escape(line))
    rprint(f"{out['n']} records in the queue" + (f"; wrote {export}" if export is not None else ""))


@label_app.command("recheck")
def label_recheck(sample: str = typer.Argument(None, help="A probability sample (default: every one)."),
                  reviewer: str = typer.Option(..., "--reviewer", help="Whose labels to re-check."),
                  export: Path = typer.Option(None, "--export", help="Write the blinded queue as CSV (item, title, abstract, decision, reason, labels, note)."),
                  as_json: bool = typer.Option(False, "--json", help="Print JSON (the same as GET /api/labels/queue?mode=recheck)."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """The reviewer's labels due for re-check after the criteria assessment, blind and shuffled; fill the export and
    run `wider label import --queue FILE`."""
    from .core.project import Project
    from .sample.labels import add_reviewer, recheck_queue

    p = Project.find(directory)
    try:
        add_reviewer(p, reviewer)
        out = recheck_queue(p, reviewer, sample)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    _queue_out(out, export, as_json)


@label_app.command("second-reviewer")
def label_second_reviewer(sample: str = typer.Argument(None, help="A probability sample (default: every one)."),
                          reviewer: str = typer.Option(..., "--reviewer", help="The second reviewer."),
                          recheck: bool = typer.Option(False, "--recheck", help="The records due for re-check instead of the enriched queue."),
                          export: Path = typer.Option(None, "--export", help="Write the blinded queue as CSV."),
                          as_json: bool = typer.Option(False, "--json", help="Print JSON (the same as GET /api/labels/queue?mode=second)."),
                          directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """A blind queue for an independent second reviewer, enriched with first-label excludes; never a gate."""
    from .core.project import Project
    from .sample.labels import add_reviewer, second_reviewer_queue

    p = Project.find(directory)
    try:
        add_reviewer(p, reviewer)
        out = second_reviewer_queue(p, reviewer, sample, recheck=recheck)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    _queue_out(out, export, as_json)


@label_app.command("agreement")
def label_agreement(sample: str, a: str, b: str, directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Cohen's kappa and the disagreement list between two reviewers."""
    from .core.project import Project
    from .sample.labels import agreement

    p = Project.find(directory)
    r = agreement(p, sample, a, b)
    dis = r["disagreements"]
    for line in r.get("hints", []):   # a name with no labels: the near match or the reviewers who have labels
        rprint(f"[red]{escape(line)}[/red]")
    if not r["n"]:
        typer.echo("no records labelled by both")
    else:   # the line the page prints above its table
        same = round(r["n"] * r["agreement"])
        open_ = sum(1 for d in dis if not d.get("adjudicated"))
        typer.echo(f"Both labelled {r['n']} record{'' if r['n'] == 1 else 's'} · they agree on {same} "
                   f"({100 * r['agreement']:.1f}%) · agreement beyond chance {r['kappa']} (Cohen's κ) · "
                   f"{len(dis)} disagreement{'' if len(dis) == 1 else 's'}"
                   + (f", {len(dis) - open_} adjudicated, {open_} still open" if dis else ""))
    for d in dis:   # the records, so the recall line's "List them with" holds (audit C1)
        j = d.get("adjudicated")
        typer.echo(f"record {d['record_id']}: {a} {d[a]}, {b} {d[b]}"
                   + (f"; adjudicated {j['decision']} by {j['decided_by']}" if j else "; not adjudicated"))
    if r.get("left_out"):   # a review package's exclude choice (reviewer-roles-and-blinding D7)
        typer.echo(r["left_out"]["line"])
    p.close()


@label_app.command("criteria")
def label_criteria(
    sample: str = typer.Argument(None, help="The probability sample (default: the one recall reads)."),
    assess: str = typer.Option(None, "--assess", help="Record the assessment: yes (substantive) or no."),
    scope: str = typer.Option(None, "--scope", help="With --assess yes: affected or all (default: the recommendation)."),
    note: str = typer.Option("", "--note", help="Why (kept in the audit trail)."),
    author: str = typer.Option(None, "--author", help="Who assessed (default: the configured author)."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON (the same as GET/POST /api/labels/criteria)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """What changed between each early label's codebook version and the frozen one, the labels it may affect and the
    recommendation; with --assess, record the assessment, which alone makes labels due for re-check."""
    from .core.journal import current_author
    from .core.project import Project
    from .sample.labels import assess_criteria, criteria_report

    p = Project.find(directory)
    try:
        if assess is not None:
            assess_criteria(p, sample, assess, scope, note, author=current_author(author))
        out = criteria_report(p, sample)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(out, default=str))
        return
    for line in out["lines"]:
        rprint(escape(line))


@label_app.command("revisions")
def label_revisions(sample: str, as_json: bool = typer.Option(False, "--json", help="Print JSON."),
                    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """The revisions of a sample's labels: re-checks and review-package re-reviews (the cause), the original and revised
    label, both codebook versions, the reason; then the corrections of saved labels and adjudications on a locked
    sample or a sealed record, with their reasons."""
    from .core.project import Project
    from .sample.labels import corrections, revisions

    p = Project.find(directory)
    try:
        rows, fixes = revisions(p, sample), corrections(p, sample)
    except KeyError as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps({"revisions": rows, "corrections": fixes}, default=str))
        return
    if not rows:
        rprint("no re-checked labels")
    for r in rows:
        cause = (f"re-reviewed in review package {r['package_id']}" if r.get("cause") == "package re-review"
                 else "re-checked")
        rprint(escape(f"record {r['record_id']} ({r['reviewer']}): {r['old_decision']} {_under(r['old_codebook_version'])} "
                      f"to {r['new_decision']} {_under(r['new_codebook_version'])}; {cause}"
                      + (f"; reason: {r['new_reason']}" if r.get("new_reason") else "")
                      + (f"; assessment: {r['assessment_note']}" if r.get("assessment_note") else "")))
    for c in fixes:
        whose = "adjudication" if c["target"] == "adjudication" else c["reviewer"]
        ticks = "" if c["old_labels"] == c["new_labels"] else f"; ticks {c['old_labels'] or '{}'} to {c['new_labels']}"
        rprint(escape(f"record {c['record_id']} ({whose}): {c['old_decision']} to {c['new_decision']}; correction"
                      + (f"; reason: {c['new_reason']}" if c.get("new_reason") else "") + ticks
                      + f"; why: {c['why']}; by {c['author']} at {c['at']}"))


def _under(v) -> str:
    return f"under v{v}" if v is not None else "before any frozen codebook"


@label_app.command("provenance")
def label_provenance(sample: str = typer.Argument(..., help="The sample whose labels are declared independent."),
                     reviewer: str = typer.Option(..., "--reviewer", help="The reviewer whose labels these are."),
                     independent: str = typer.Option(..., "--independent", metavar="BASIS",
                                                     help="How the labels were made without seeing the model's output."),
                     by: str = typer.Option(None, "--by", help="Who declares it; defaults to $WIDER_REVIEWER (never the login name)."),
                     directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Declare a reviewer's imported labels, or labels saved before provenance was recorded, independent of the
    model's output; labels from the unblinded queue stay not independent. One undoable History change."""
    from .core.project import Project
    from .sample.labels import declare_independent

    who = _named(by)
    p = Project.find(directory)
    try:
        for line in declare_independent(p, sample, reviewer, independent, by=who)["lines"]:
            rprint(escape(line))
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()


@label_app.command("adjudicate")
def label_adjudicate(sample: str, record_id: int, decision: str, by: str = typer.Option(None, "--by", help="Who adjudicated; defaults to $WIDER_REVIEWER, then $USER."),
                     reason: str = typer.Option("", "--reason", help="Why this decision wins."),
                     correction_reason: str = typer.Option(None, "--correction-reason", metavar="WHY",
                                                           help="Why a saved adjudication on a locked sample or a sealed record changes."),
                     directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Record the final decision for a record where reviewers disagree."""
    from .core.journal import current_author
    from .core.project import Project
    from .sample.labels import adjudicate

    p = Project.find(directory)
    try:
        adjudicate(p, sample, record_id, decision, by=current_author(by), reason=reason,
                   correction_reason=correction_reason)
    except (KeyError, ValueError) as e:
        rprint(f"[red]{escape(str(e).strip(chr(34)))}[/red]")
        raise typer.Exit(1)
    finally:
        p.close()
    rprint("recorded")


# ---------------------------------------------------------------- screen decisions + recall
screen_app = typer.Typer(help="LLM screening of the corpus.", no_args_is_help=True)
app.add_typer(screen_app, name="screen")


@screen_app.command("import-decisions")
def screen_import_decisions(protocol: str, file: Path, decision_col: str = typer.Option("final_decision", "--column", help="CSV column holding include | maybe | exclude."),
                            others_exclude: bool = typer.Option(True, "--others-exclude/--others-unscreened",
                                                                help="Records absent from the file count as excluded."),
                            directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
                            author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER (recorded in History).")):
    """Import corpus decisions produced elsewhere (matched by DOI, then title) under a protocol name."""
    from .core.project import Project
    from .screen.decisions import import_csv

    p = Project.find(directory)
    try:
        res = import_csv(p, protocol, file, decision_col=decision_col, others_are_exclude=others_exclude, author=author)
        rprint({k: v for k, v in res.items() if k != "conflicts"})
        if res.get("duplicate"):
            rprint(f"{res['duplicate']} later row(s) matched a record already listed; the first row of each record counts.")
        for x in res.get("conflicts", []):
            rprint(f"[red]Row {x['row']} says {x['decision']} for record {x['record_id']}; row {x['kept_row']} "
                   f"says {x['kept']} and is kept. Check the file.[/red]")
    except (ValueError, KeyError) as e:
        rprint(f"[red]{escape(str(e.args[0]) if e.args else str(e))}[/red]")
        raise typer.Exit(1) from e
    finally:
        p.close()


@screen_app.command("import-adjudication")
def screen_import_adjudication(
    protocol: str, file: Path,
    adjudicator: str = typer.Option(None, "--adjudicator", help="Who decided the records (needed unless every used row has an adjudicator cell)."),
    column: str = typer.Option("adjudication", "--column", help="CSV column holding include | maybe | exclude; blank cells keep the decision."),
    result_set: int = typer.Option(None, "--set", help="The finished set to build from (default: the active set)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Count and list the rows left out; write nothing."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
    author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER (recorded in History)."),
):
    """Import an adjudicator's decisions for some records into a new active set built from a finished set.

    Records whose row has no decision keep theirs; the set it was built from stays unchanged. Exit 1 when the file,
    column or set cannot be used, 2 when the adjudicator's name is missing or refused."""
    from .core.project import Project
    from .screen.adjudication import import_adjudication

    p = Project.find(directory)
    try:
        data = file.read_bytes()
    except OSError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        p.close()
        raise typer.Exit(1) from e
    try:
        res = import_adjudication(p, protocol, data, file.name, adjudicator=adjudicator, column=column,
                                  result_set=result_set, author=author, dry_run=dry_run)
    except ValueError as e:
        rprint(f"[red]{escape(str(e.args[0]) if e.args else str(e))}[/red]")
        raise typer.Exit(getattr(e, "code", 1)) from e
    finally:
        p.close()
    conflicts = {x["text"] for x in res["conflicts"]}
    for line in res["lines"]:
        if line in conflicts:   # two rows of one record with different decisions (D4)
            typer.secho(line, fg="red")
        else:
            print(line)


@screen_app.command("protocols")
def screen_protocols(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Decision counts per protocol."""
    from .core.project import Project
    from .screen.decisions import protocols

    p = Project.find(directory)
    t = Table("protocol", "n", "include", "maybe", "exclude")
    for r in protocols(p):
        t.add_row(r["protocol"], str(r["n"]), str(r["include"]), str(r["maybe"]), str(r["exclude"]))
    rprint(t)
    p.close()


@screen_app.command("build-prompt")
def screen_build_prompt(codebook_version: int = typer.Option(None, "--codebook", help="Codebook version (default: approved, else newest)."), show: bool = typer.Option(False, "--show", help="Print the rendered prompt."),
                        directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Render the screening prompt and JSON schema from the codebook (and cells) and save them as a prompts version."""
    from .core.project import Project
    from .screen.codebook import build, save_prompt_artifact

    p = Project.find(directory)
    try:
        b = build(p, codebook_version)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    if show:
        print(b["prompt"])
    row = save_prompt_artifact(p, b)
    rprint(f"[green]prompts v{row['version']}[/green] from codebook v{b['codebook_version']}: {len(b['prompt'].split())} words, "
           f"{len(b['schema']['properties'])} fields")
    p.close()


def _estimate(p, make, estimate_only: bool) -> dict | None:
    """Print the cost range before the first call (Decision 15); with ``estimate_only`` print it and exit 0."""
    from .construct.propose import ProposalError
    from .core.estimate import range_text

    try:
        r = make()
    except (ValueError, KeyError, TypeError, ProposalError) as e:   # without --estimate-only the run reports it
        if estimate_only:
            rprint(f"[red]{escape(str(e))}[/red]")
            p.close()
            raise typer.Exit(1) from None
        return None
    for line in range_text(r):
        rprint(escape(line))
    if estimate_only:
        p.close()
        raise typer.Exit()
    return r


def _screen_progress(p, pre: dict | None, every: int):
    """Progress lines of a screening command with the cost so far and the projected range (the job bar's text)."""
    from .core.estimate import calls_left, projection, projection_text, usage_since

    before = p.store.scalar("SELECT COALESCE(MAX(usage_id), 0) FROM llm_usage")

    def cb(i, n, rid, d):
        if i % every == 0 or i == n or str(d).startswith("error"):
            u = usage_since(p, before)
            text = projection_text(projection(u, calls_left(i, n, u, pre), pre))
            rprint(escape(f"{i}/{n} {rid} {d}" + (f" · {text}" if text else "")))
    return cb


BACKEND_CHANGE = typer.Option(None, "--backend-change", help="When the backend changed since an unfinished run started: continue | new.")
LIMIT_HELP = ("Record limit: caps the new model calls at this many per pass, as only the first records of the scope "
              "are screened and records already screened are not called again. A protocol run whose passes are "
              "finished still decides every record, without new calls.")
REUSE_EVALUATIONS = typer.Option(False, "--reuse-evaluations", help="Reuse earlier evaluations made with identical inputs instead of calling the model again.")
SET_LABEL = typer.Option(None, "--set-label", help="Label of the new result set.")
PROVIDER_B = typer.Option(None, "--provider-b", help="AI model of pass B (second screen: read S2) for this run (default: the screen_b stage backend, else pass A's).")
PROVIDER_C = typer.Option(None, "--provider-c", help="AI model of the judge (second screen: read S3) for this run (default: the screen_judge stage backend, else pass A's).")


def _ensure_pass_research(p, mode: str, provider, provider_b, provider_c) -> None:
    """Consent for every distinct pass backend, before any run is created (design D10)."""
    from .cli_backend_consent import ensure_research
    from .cli_lookups import say_sent
    from .screen.protocol import pass_backend_names

    for name in pass_backend_names(p, mode, provider, provider_b, provider_c):
        say_sent(p, "screen", name)
        ensure_research(p, name, "screen")


def _print_pass_lines(p, mode: str, provider, provider_b, provider_c) -> None:
    """Before the run: the notes for per-pass flags of passes that do not run, and the passes sharing a model."""
    from .screen.protocol import pass_view

    try:
        v = pass_view(p, mode, provider, provider_b, provider_c)
    except KeyError:
        return   # the run itself names the unknown backend
    for line in v["notes"] + v["shared"]:
        rprint(escape(line))


def _dev_gate(p, mode: str, scope: str = "corpus", protocol: str | None = None) -> None:
    """ui-redesign D4: refuses (exit 1) a run that includes records of an incomplete development sample, before the
    sending notice, consent or anything stored."""
    from .screen.devgate import DevSampleIncomplete, check_run

    try:
        check_run(p, mode, scope, protocol)
    except DevSampleIncomplete as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        p.close()
        raise typer.Exit(1) from e


def _preflight(p, mode: str, **kw) -> bool:
    """The shared screening preflight (same as the API's): prints a refusal and exits 1; prints "already done" and
    returns True when a plain pass has nothing to do; False when the run may start."""
    from .screen.preflight import preflight_screen, refusal_body

    try:
        r = preflight_screen(p, mode, **kw)
    except (ValueError, KeyError) as e:
        msg = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
        rprint(f"[red]{escape(msg)}[/red]")
        rb = refusal_body(e) or {}
        ns = rb.get("new_set_needed")
        p.close()
        if ns:   # rules 2a and 3: nothing ran and nothing is wrong, so the CLI exits 0 (design, re-runs)
            rprint(f"to screen: {ns.get('to_screen')}; reusable: {ns.get('reusable')}")
            raise typer.Exit(0) from e
        raise typer.Exit(1) from e
    if r["status"] == "already_done":
        d = r["already_done"]
        rprint(f"already done under model {d.get('model')}: run {d['run_id']} is finished; add another backend name "
               "for a separate run")
        return True
    return False


def _limit(limit: str | None) -> int | None:
    """A --limit as typed -> int, refused with the page's sentence (exit 1) when it is not a whole number from 1."""
    from .screen.engine import check_run_args, whole_limit

    try:
        n = whole_limit(limit)
        check_run_args(limit=n)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    return n


@screen_app.command("run")
def screen_run(
    scope: str = typer.Argument(..., help="sample:<name> | corpus | maybe:<protocol> | ids:1,2,3"),
    pass_name: str = typer.Option("A", "--pass", help="Pass label (A, B, C) recorded in the run spec."),
    provider: str = typer.Option(None, "--provider", help="AI model (default: the screen stage provider, else the project default)."),
    prompt_version: int = typer.Option(None, "--prompt", help="Prompt version (default: newest)."),
    limit: str = typer.Option(None, "--limit", help=LIMIT_HELP),
    temperature: float = typer.Option(None, "--temperature", help="Sampling temperature (default: the backend's temperature)."),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the cost range and exit without calling a model."),
    backend_change: str = BACKEND_CHANGE,
    reuse_evaluations: bool = REUSE_EVALUATIONS,
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """One screening pass: one isolated model call per record, resumable, raw responses kept."""
    from .cli_backend_consent import ensure_research
    from .core.estimate import estimate_screen
    from .core.project import Project
    from .screen.engine import screen

    limit = _limit(limit)
    p = Project.find(directory)
    pre = _estimate(p, lambda: estimate_screen(p, "pass", scope=scope, provider_name=provider,
                                               prompt_version=prompt_version, limit=limit), estimate_only)
    _dev_gate(p, "pass", scope)
    from .cli_lookups import say_sent
    say_sent(p, "screen", provider)
    ensure_research(p, provider, "screen")
    if _preflight(p, "pass", scope=scope, pass_name=pass_name, provider_name=provider, prompt_version=prompt_version,
                  limit=limit, backend_change=backend_change, temperature=temperature):
        p.close()
        return
    try:
        res = screen(p, scope, pass_name=pass_name, provider_name=provider, prompt_version=prompt_version, limit=limit,
                     temperature=temperature, progress=_screen_progress(p, pre, 10), backend_change=backend_change,
                     reuse_evaluations=reuse_evaluations)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    rprint(res)
    _print_stage_result(p, "screen")
    p.close()


@screen_app.command("protocol")
def screen_protocol(
    scope: str = typer.Argument("corpus", help="corpus | sample:<name> | ids:1,2,3"),
    protocol: str = typer.Option("main", "--name", help="Protocol name the passes and decisions are stored under."),
    provider: str = typer.Option(None, "--provider", help="AI model (default: the screen stage provider, else the project default)."),
    prompt_version: int = typer.Option(None, "--prompt", help="Prompt version (default: newest)."),
    limit: str = typer.Option(None, "--limit", help=LIMIT_HELP),
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the cost range and exit without calling a model."),
    backend_change: str = BACKEND_CHANGE,
    new_set: bool = typer.Option(False, "--new-set", help="Screen again as a new result set (the earlier sets are kept)."),
    set_label: str = SET_LABEL,
    reuse_from: int = typer.Option(None, "--reuse-from", min=1, help="Copy the final decisions of this set (same method) and screen only the rest."),
    no_reuse: bool = typer.Option(False, "--no-reuse", help="Screen every record of the new set, reusing nothing."),
    reuse_evaluations: bool = REUSE_EVALUATIONS,
    provider_b: str = PROVIDER_B,
    provider_c: str = PROVIDER_C,
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Screens with the protocol's passes (screening.passes): with two, passes A and B, a blind third read where they
    disagree and a 2-of-3 majority; with one, pass A alone decides. Writes corpus decisions. Pass B and the judge
    run on their own backend with --provider-b and --provider-c (else the screen_b and screen_judge stage backends)."""
    from .core.estimate import estimate_screen
    from .core.project import Project
    from .screen.protocol import run_protocol

    limit = _limit(limit)
    p = Project.find(directory)
    pre = _estimate(p, lambda: estimate_screen(p, "protocol", scope=scope, protocol=protocol, provider_name=provider,
                                               prompt_version=prompt_version, limit=limit,
                                               reuse_evaluations=reuse_evaluations, provider_b=provider_b,
                                               provider_c=provider_c), estimate_only)
    _dev_gate(p, "protocol", scope, protocol)
    _ensure_pass_research(p, "protocol", provider, provider_b, provider_c)
    _preflight(p, "protocol", scope=scope, protocol=protocol, provider_name=provider, prompt_version=prompt_version,
               limit=limit, backend_change=backend_change, new_set=new_set, reuse_from=reuse_from, no_reuse=no_reuse,
               provider_b=provider_b, provider_c=provider_c)
    _print_pass_lines(p, "protocol", provider, provider_b, provider_c)
    try:
        res = run_protocol(p, scope, protocol, provider_name=provider, prompt_version=prompt_version, limit=limit,
                           progress=_screen_progress(p, pre, 25), backend_change=backend_change, new_set=new_set,
                           set_label=set_label, reuse_from=reuse_from, no_reuse=no_reuse,
                           reuse_evaluations=reuse_evaluations, provider_b=provider_b, provider_c=provider_c)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    rprint(res)
    _print_stage_result(p, "screen")
    p.close()


@screen_app.command("second")
def screen_second(protocol: str = typer.Argument("main", help="Protocol whose maybes to re-screen."), provider: str = typer.Option(None, "--provider", help="AI model (default: the screen stage provider, else the project default)."),
                  prompt_version: int = typer.Option(None, "--prompt", help="Prompt version (default: newest)."), limit: str = typer.Option(None, "--limit", help="Stop after this many records."),
                  estimate_only: bool = typer.Option(False, "--estimate-only", help="Print the cost range and exit without calling a model."),
                  backend_change: str = BACKEND_CHANGE,
                  from_set: int = typer.Option(None, "--from-set", min=1, help="Result set of the protocol whose maybes to re-screen (default: the active set)."),
                  new_set: bool = typer.Option(False, "--new-set", help="Screen again as a new result set of <name>_second."),
                  set_label: str = SET_LABEL,
                  reuse_evaluations: bool = REUSE_EVALUATIONS,
                  provider_b: str = PROVIDER_B,
                  provider_c: str = PROVIDER_C,
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Second screen of the maybes: two reads and a tie-break; writes protocol <name>_second. Read S1 runs on pass A's
    backend, S2 on pass B's (--provider-b) and the tie-break S3 on the judge's (--provider-c)."""
    from .core.estimate import estimate_screen
    from .core.project import Project
    from .screen.protocol import second_screen

    limit = _limit(limit)
    p = Project.find(directory)
    pre = _estimate(p, lambda: estimate_screen(p, "second", protocol=protocol, provider_name=provider,
                                               prompt_version=prompt_version, limit=limit,
                                               reuse_evaluations=reuse_evaluations, provider_b=provider_b,
                                               provider_c=provider_c), estimate_only)
    _dev_gate(p, "second", protocol=protocol)
    _ensure_pass_research(p, "second", provider, provider_b, provider_c)
    _preflight(p, "second", protocol=protocol, provider_name=provider, prompt_version=prompt_version, limit=limit,
               backend_change=backend_change, new_set=new_set, from_set=from_set, provider_b=provider_b,
               provider_c=provider_c)
    _print_pass_lines(p, "second", provider, provider_b, provider_c)
    try:
        rprint(second_screen(p, protocol, provider_name=provider, prompt_version=prompt_version, limit=limit,
                             progress=_screen_progress(p, pre, 25), backend_change=backend_change,
                             from_set=from_set, new_set=new_set, set_label=set_label,
                             reuse_evaluations=reuse_evaluations, provider_b=provider_b, provider_c=provider_c))
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    _print_stage_result(p, "screen")
    p.close()


@screen_app.command("evaluate")
def screen_evaluate(run_id: str, sample: str = typer.Option("development", "--sample", help="Human-labelled sample to evaluate against."),
                    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Compare a run with the human labels of the development sample (never the validation sample)."""
    from .core.project import Project
    from .screen.evaluate import evaluate, write_report

    p = Project.find(directory)
    try:
        ev = evaluate(p, run_id, sample)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1)
    write_report(p, ev)
    rprint({k: v for k, v in ev.items() if k not in ("disagreements", "adjacent_cases", "by_adjacent", "lines", "labels_used")})
    for line in ev["lines"]:
        typer.echo(line)
    from .cli_adjacent import print_adjacent_table
    print_adjacent_table(ev)
    rprint(f"{len(ev['disagreements'])} disagreements; report written next to the run")
    p.close()


@screen_app.command("replay")
def screen_replay(run_id: str, directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Rebuild rule decisions from stored responses, offline."""
    from .core.project import Project
    from .screen.engine import replay

    p = Project.find(directory)
    try:
        rprint(replay(p, run_id))
    except (ValueError, KeyError) as e:
        _fail(p, e)
    p.close()


@screen_app.command("runs")
def screen_runs(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """List screening runs."""
    from .core.project import Project
    from .screen.engine import EXPERIMENTAL_NOTE, run_experimental, run_mixed, runs

    p = Project.find(directory)
    t = Table("run", "status", "items", "ok", "provider", "model", "mixed", "prompt", "started")
    for r in runs(p):
        marks = ["mixed"] if run_mixed(p, r["run_id"]) else []
        if run_experimental(p, r["run_id"]):
            marks.append(EXPERIMENTAL_NOTE)
        t.add_row(r["run_id"], r["status"], str(r["n_items"]), str(r["n_ok"]), r["provider"], r["model"],
                  "; ".join(marks), f"v{r['prompt_version']}", local_time(r["started_at"]))
    rprint(t)
    p.close()


@screen_app.command("status")
def screen_status(directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
                  as_json: bool = typer.Option(False, "--json", help="Print the steps, rounds and next step as JSON.")):
    """Where screening stands: the steps, the latest round in words, unusual results and the next step (the twin of
    the Screening page's summary card)."""
    import json as _json

    from .core.project import Project
    from .screen.progress import screen_progress, status_lines

    p = Project.find(directory)
    try:
        prog = screen_progress(p)
        if as_json:
            print(_json.dumps(prog, indent=2, default=str))
        else:
            for line in status_lines(prog):
                print(line)
    finally:
        p.close()


SCREEN_SET = typer.Option(None, "--set", min=1, help="Result set of the protocol (default: the active set).")


def _fail(p, e: Exception):
    msg = str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e)
    rprint(f"[red]{escape(msg)}[/red]")
    p.close()
    raise typer.Exit(1) from e


@screen_app.command("export")
def screen_export(protocol: str = typer.Argument("main", help="Protocol to export."), result_set: int = SCREEN_SET,
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Write exports/screening/<protocol>_decisions.csv and _evaluations.csv (models, runs and calls; no abstracts)."""
    from .core.project import Project
    from .screen.export import export_protocol

    p = Project.find(directory)
    try:
        out = export_protocol(p, protocol, result_set)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    rprint(f"{out['n_decisions']} decisions: {out['decisions']}")
    rprint(f"{out['n_evaluations']} evaluations: {out['evaluations']}")
    if out.get("role_note"):
        rprint(f"[yellow]{out['role_note']}[/yellow]")
    p.close()


def print_models_summary(s: dict) -> None:
    sc = s["scope"]
    where = (f"run {sc['run_id']}" if sc.get("run_id") else f"{sc['protocol']} set {sc['result_set']}"
             + (f" ({sc['label']})" if sc.get("label") else ""))
    rprint(f"{where}: {s['headline']}" + ("; mixed" if s["mixed"] else ""))
    if sc.get("role_note"):
        rprint(f"[yellow]{sc['role_note']}[/yellow]")
    if s.get("note"):
        rprint(f"[yellow]{s['note']}[/yellow]")
    t = Table("model", "passes", "records", "evaluations", "reused", "calls", "include", "maybe", "exclude",
              "tokens in", "tokens out", "cost", "final")
    for m in [*s["models"], {**s["totals"], "model": "total", "final_records": ""}]:
        t.add_row(escape(str(m["model"])) + (" (experimental)" if m.get("experimental") else ""),
                  ", ".join(m.get("passes") or []), str(m["records"]),
                  str(m["evaluations"]), str(m["reused"]), str(m["calls"]), str(m["include"]), str(m["maybe"]),
                  str(m["exclude"]), str(m["prompt_tokens"]), str(m["completion_tokens"]), m["cost"],
                  str(m["final_records"]))
    rprint(t)
    if s.get("hidden"):
        rprint(f"{s['hidden']}.")
    if s.get("origins"):
        rprint("decisions by origin: " + ", ".join(f"{k} {v}" for k, v in sorted(s["origins"].items())))


@screen_app.command("models")
def screen_models(protocol: str = typer.Option(None, "--protocol", help="Protocol (its active set, or --set)."),
                  run_id: str = typer.Option(None, "--run", help="One run."), result_set: int = SCREEN_SET,
                  as_json: bool = typer.Option(False, "--json", help="Print JSON."),
                  directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Which models screened what: records, evaluations, calls, decisions, tokens and cost per model."""
    from .core.project import Project
    from .screen.models_summary import by_model

    p = Project.find(directory)
    if not protocol and not run_id:
        protocol = "main"
    try:
        s = by_model(p, protocol=None if run_id else protocol, run_id=run_id, result_set=result_set)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    if as_json:
        typer.echo(json.dumps(s, ensure_ascii=False, default=str))
    else:
        print_models_summary(s)
    p.close()


sets_app = typer.Typer(help="Result sets of a protocol: list, choose, label, compare.", no_args_is_help=True)
screen_app.add_typer(sets_app, name="sets")


@sets_app.command("list")
def sets_list(protocol: str = typer.Argument("main", help="Protocol."),
              as_json: bool = typer.Option(False, "--json", help="Print JSON."),
              directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Every result set of a protocol: active mark, label, role, models, runs, counts."""
    from .core.project import Project
    from .screen import result_sets as rs

    p = Project.find(directory)
    rows = rs.sets(p, protocol)
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, default=str))
        p.close()
        return
    if not rows:
        rprint(f"{escape(protocol)} has no result sets")
    t = Table("set", "active", "label", "source", "scope", "role", "mixed", "models", "n", "include", "maybe",
              "exclude", "runs", "last decided")
    for r in rows:
        t.add_row(str(r["result_set"]), "yes" if r["active"] else "", escape(r.get("label") or ""),
                  escape(r.get("source") or ""), escape(r.get("scope") or ""), r.get("role") or "",
                  "mixed" if r.get("mixed") else "", escape(", ".join(r["models"])), str(r["n"]), str(r["include"]),
                  str(r["maybe"]), str(r["exclude"]), escape(", ".join(r["run_ids"])), local_time(r["last_decided_at"]))
    rprint(t)
    for r in rows:
        if r.get("role_note"):
            rprint(f"[yellow]set {r['result_set']}: {r['role_note']}[/yellow]")
        if r.get("hidden"):
            typer.echo(f"set {r['result_set']}: {r['hidden']}; the counts leave them out.")
    p.close()


@sets_app.command("use")
def sets_use(protocol: str, n: int = typer.Argument(..., min=1, help="Set number."),
             with_source: bool = typer.Option(False, "--with-source", help="Also switch the source protocol to the set this one was built from."),
             author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
             directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Use set N for analysis (recall, report, exports). Journalled with undo; starts nothing."""
    from .core.project import Project
    from .screen import result_sets as rs

    p = Project.find(directory)
    try:
        r = rs.use(p, protocol, n, with_source=with_source, author=author)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    rprint(f"{escape(protocol)} uses set {r['result_set']}")
    p.close()


@sets_app.command("label")
def sets_label(protocol: str, n: int = typer.Argument(..., min=1, help="Set number."),
               text: str = typer.Argument(..., help="Label (at most 120 characters; empty removes it)."),
               author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
               directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Name a result set."""
    from .core.project import Project
    from .screen import result_sets as rs

    p = Project.find(directory)
    try:
        r = rs.label(p, protocol, n, text, author=author)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    rprint(f"{escape(protocol)} set {n}: {escape(r.get('label') or '(no label)')}")
    p.close()


@sets_app.command("scope")
def sets_scope(protocol: str, n: int = typer.Argument(..., min=1, help="Set number."),
               scope: str = typer.Argument(..., help="corpus | sample:<name> | ids:1,2,3"),
               author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
               directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Name the scope of a set whose scope is unknown (records checked; journalled)."""
    from .core.project import Project
    from .screen import result_sets as rs

    p = Project.find(directory)
    try:
        r = rs.set_scope(p, protocol, n, scope, author=author)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    rprint(f"{escape(protocol)} set {n}: scope {escape(r['scope'])}, role {r.get('role')}")
    p.close()


def print_compare(c: dict) -> None:
    rprint(f"{escape(c['protocol'])} set {c['a']} vs set {c['b']}" + (" (models only)" if c["models_only"] else "")
           + f": {c['n']} records in both, agreement {c['agreement'] if c['agreement'] is not None else '-'}; "
           f"only in set {c['a']}: {c['only_a']}, only in set {c['b']}: {c['only_b']}")
    t = Table(f"set {c['a']} \\ set {c['b']}", "include", "maybe", "exclude")
    for x in ("include", "maybe", "exclude"):
        t.add_row(x, *(str(c["table"][x][y]) for y in ("include", "maybe", "exclude")))
    rprint(t)
    if c.get("hidden"):
        rprint(f"{c['hidden']}.")
    for d in c["differ"][:50]:
        rprint(f"  {d['record_id']}: {d['a']} -> {d['b']}  {escape((d.get('title') or '')[:80])}")
    if len(c["differ"]) > 50:
        rprint(f"  … and {len(c['differ']) - 50} more")


@sets_app.command("compare")
def sets_compare(protocol: str, a: int = typer.Argument(..., min=1), b: int = typer.Argument(..., min=1),
                 models_only: bool = typer.Option(False, "--models-only", help="Ignore researcher answers (compare the model votes)."),
                 as_json: bool = typer.Option(False, "--json", help="Print JSON."),
                 directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child).")):
    """Compare two result sets of a protocol (never changes the active set). To compare configurations on the
    development sample, use `wider screen compare run`."""
    from .core.project import Project
    from .screen import result_sets as rs

    p = Project.find(directory)
    try:
        c = rs.compare(p, protocol, a, b, models_only=models_only)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    if as_json:
        typer.echo(json.dumps(c, ensure_ascii=False, default=str))
    else:
        print_compare(c)
    p.close()


compare_app = typer.Typer(
    help="Compare screening configurations (backend, prompt version, temperature) on the locked development sample, "
         "each repeated, against its final labels. To compare two result sets of one protocol record by record, use "
         "`wider screen sets compare`.", no_args_is_help=True)
screen_app.add_typer(compare_app, name="compare")
COMPARE_SAMPLE = typer.Option(None, "--sample", help="Purposive sample to compare on (default: protocol.sampling.development_sample).")


@compare_app.command("run")
def compare_run(
    name: str = typer.Argument(..., help="Comparison name; the same name and list resumes, a new list needs a new name."),
    sample: str = COMPARE_SAMPLE,
    config: list[str] = typer.Option(None, "--config", help="NAME[:pN][@T]: backend, prompt version, temperature (repeat for each configuration), e.g. a:p2@0.7."),
    repeats: int = typer.Option(3, "--repeats", help="Runs per configuration, 1 to 10 (consistency needs 2 or more)."),
    reuse_evaluations: bool = REUSE_EVALUATIONS,
    estimate_only: bool = typer.Option(False, "--estimate-only", help="Print records, runs, new calls and the cost range per backend and exit without calling a model."),
    author: str = typer.Option(None, "--author", help="Who ran the comparison (default: the configured author)."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Screens the sample's records once per configuration and repeat, then shows the figures (no configuration is
    named best; the figures describe the sample, not the corpus). Needs a locked sample."""
    from .cli_backend_consent import ensure_backend_consent
    from .core.project import Project
    from .screen import compare

    p = Project.find(directory)
    config = list(config or [])
    try:
        est = compare.estimate(p, name, sample, config, repeats, reuse_evaluations=reuse_evaluations)
        if not estimate_only:
            prep = compare.prepare(p, name, sample, config, repeats)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    if estimate_only:
        typer.echo(json.dumps(est, ensure_ascii=False, default=str) if as_json else "\n".join(est["lines"]))
        p.close()
        return
    if not as_json:
        typer.echo("\n".join(est["lines"]))
    from .cli_lookups import say_sent
    for prov in dict.fromkeys(c["provider"] for c in prep["configs"]):
        say_sent(p, "screen", prov, quiet=as_json)
        ensure_backend_consent(p, p.provider(prov), "research")

    def on_run(c, k, run_id):
        if not as_json:
            typer.echo(f"{c['label']} repeat {k}: {run_id}")

    try:
        res = compare.run(p, name, sample, config, repeats=repeats, reuse_evaluations=reuse_evaluations,
                          author=author, on_run=on_run)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    if as_json:
        typer.echo(json.dumps(res, ensure_ascii=False, default=str))
    else:
        typer.echo("\n".join(compare.show_lines(res)))
        typer.echo(f"per-record decisions: {res['csv']}")
    p.close()


@compare_app.command("show")
def compare_show(
    name: str = typer.Argument(..., help="Comparison name."),
    by: str = typer.Option(None, "--by", help="Break the misses and false includes down by pool or area."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """The comparison's figures from the sample's current final labels, configurations in the order given."""
    from .core.project import Project
    from .screen import compare

    p = Project.find(directory)
    try:
        res = compare.results(p, name)
        lines = compare.show_lines(res, by)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    typer.echo(json.dumps(res, ensure_ascii=False, default=str) if as_json else "\n".join(lines))
    p.close()


@compare_app.command("list")
def compare_list(
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """The stored comparisons: name, sample, configurations, repeats, records."""
    from .core.project import Project
    from .screen import compare

    p = Project.find(directory)
    rows = compare.list_comparisons(p)
    if as_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, default=str))
    elif not rows:
        typer.echo("No comparison yet")
    else:
        t = Table("name", "sample", "configurations", "repeats", "records", "finished")
        for r in rows:
            t.add_row(r["name"], r["sample"], " ".join(r["configs"]), str(r["repeats"]), str(r["records"]),
                      (local_time(r["finished_at"]) or "not finished"))
        rprint(t)
    p.close()


from .cli_export import NOT_INFORMED as NOT_INFORMED_OPT  # noqa: E402
from .cli_export import USED as USED_OPT  # noqa: E402


@app.command()
def recall(
    protocol: str = typer.Argument(..., help="Screening protocol whose decisions are evaluated."),
    sample: str = typer.Option(None, "--sample", help="Probability sample with human labels (default: "
                               "protocol.sampling.probability_sample)."),
    primary: str = typer.Option(None, "--primary", help="Primary reviewer when several labelled."),
    bootstrap: int = typer.Option(None, "--bootstrap", "-B", min=1, help="Bootstrap replicates of the cross-check interval in the report (default: protocol.recall.bootstrap_b; at most 50,000, more are capped with a note)."),
    result_set: int = typer.Option(None, "--set", min=1, help="Result set of the protocol (default: the active set)."),
    compare: int = typer.Option(None, "--compare", min=1, help="Also estimate recall of this set of the protocol, side by side."),
    as_json: bool = typer.Option(False, "--json", help="Print the estimate as JSON (what GET /api/recall returns)."),
    sample_used: bool = USED_OPT, not_informed: bool = NOT_INFORMED_OPT,
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Stratified recall of the screening against the human-labelled probability sample, with its interval and the
    number of relevant validation papers behind it."""
    from .cli_export import sample_choice
    from .core.project import Project
    from .core.stage_summaries import ci_label
    from .recall.estimate import estimate
    from .recall.uncertainty import interval_text
    from .recall.validation import CHOOSE_SAMPLE, LabelsIncomplete, SampleChoiceNeeded, incomplete_answer

    choice = sample_choice(sample_used, not_informed)
    p = Project.find(directory)
    sample = sample or p.config.protocol.sampling.probability_sample
    try:
        r = estimate(p, sample, protocol, primary=primary, B=bootstrap, result_set=result_set, compare=compare,
                     choice=choice)
    except SampleChoiceNeeded as e:   # nothing estimated or recorded: the researcher chooses first (D8)
        rprint(f"[red]{escape(e.notice)}[/red]")
        rprint(f"[red]{CHOOSE_SAMPLE}[/red]")
        p.close()
        raise typer.Exit(2) from e
    except LabelsIncomplete as e:   # nothing estimated or recorded until every sampled record is final (D4)
        if as_json:
            typer.echo(json.dumps(incomplete_answer(e, protocol)))
        else:
            rprint(escape(e.notice))
        p.close()
        raise typer.Exit(1) from e
    except (ValueError, KeyError) as e:
        rprint(f"[red]{escape(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))}[/red]")
        p.close()
        raise typer.Exit(1) from e
    if as_json:
        typer.echo(json.dumps(r, default=str))
        p.close()
        return
    label = f" \"{escape(r['result_set_label'])}\"" if r.get("result_set_label") else ""
    rprint(f"[bold]{sample}[/bold] vs protocol [bold]{escape(protocol)}[/bold] set {r['result_set']}{label}: "
           f"{r['n_labelled']} labelled records"
           + (f", [yellow]{r['unscreened_in_sample']} not screened[/yellow]" if r["unscreened_in_sample"] else ""))
    # plain lines: a run id is never wrapped
    typer.echo(f"models: {', '.join(r.get('models') or []) or '-'}; set made {r.get('date') or '-'}")
    typer.echo(f"runs: {', '.join(r.get('run_ids') or []) or '-'}")
    if r.get("role_note"):
        rprint(f"[yellow]{r['role_note']}[/yellow]")
    lv = ci_label(r["ci_level"])
    typer.echo(f"Recall ({r['primary_variant']}):")
    for line in r.get("lines") or []:
        typer.echo(f"  {line}")
    t = Table("variant", "relevant", "kept", "recall", f"{lv} interval", "relevant papers", "missed")
    for k, v in r["variants"].items():
        t.add_row(k, "+".join(v["relevant"]), "+".join(v["kept"]), f"{v['recall']:.3f}" if v["recall"] is not None else "-",
                  interval_text(v), str(v["n_relevant"]), str(v["n_missed"]))
    rprint(t)
    if r.get("method_note"):
        typer.echo(r["method_note"])
    tb = Table("band", "n", "weight", "human include", "human maybe", "kept strict", "kept lenient", "note")
    for b in r["bands"]:
        tb.add_row(b["band"], str(b["n"]), _w2(b["weight"]), str(b["human_include"]), str(b["human_maybe"]),
                   str(b["kept_strict"]), str(b["kept_lenient"]), b["note"])
    rprint(tb)
    if r["missed_records"]:
        rprint("missed human includes:", r["missed_records"])
    if r.get("compare", {}).get("choice_needed"):
        rprint(f"[red]compared set: {escape(r['compare']['notice'])}[/red]")
    elif r.get("compare"):
        c = r["compare"]
        tc = Table("variant", f"set {r['result_set']} recall", f"set {c['result_set']} recall",
                   f"set {r['result_set']} {lv} interval", f"set {c['result_set']} {lv} interval",
                   f"set {r['result_set']} missed", f"set {c['result_set']} missed")
        for k, v in r["variants"].items():
            o = c["variants"].get(k) or {}
            fmt = lambda x: f"{x:.3f}" if x is not None else "-"  # noqa: E731
            tc.add_row(k, fmt(v["recall"]), fmt(o.get("recall")), interval_text(v),
                       interval_text(o) if "interval" in o else "-", str(v["n_missed"]), str(o.get("n_missed", "-")))
        rprint(tc)
    _print_stage_result(p, "recall")
    p.close()


report_app = typer.Typer(invoke_without_command=True)


@report_app.callback()
def report(
    ctx: typer.Context,
    protocol: str = typer.Option(None, "--protocol", help="Screening protocol to report (default: the latest protocol with screening decisions)."),
    sample: str = typer.Option(None, "--sample", help="Probability sample for the recall estimate (default: the last labelled "
                                                      "probability sample, else the first probability sample)."),
    bundle: bool = typer.Option(False, "--bundle", help="Also write the share bundle (no abstracts, no exports, no raw responses)."),
    result_set: int = typer.Option(None, "--set", min=1, help="Result set of --protocol to report (default: the active set)."),
    sample_used: bool = USED_OPT, not_informed: bool = NOT_INFORMED_OPT,
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Write exports/report.md and report.json with every number of the review, generated from the store.
    `wider report prisma ...` holds the PRISMA reporting assistant."""
    if ctx.invoked_subcommand is not None:
        return
    from .cli_export import print_pending, sample_choice
    from .core.project import Project
    from .recall.report import share_bundle, write_report

    choice = sample_choice(sample_used, not_informed)
    p = Project.find(directory)
    try:
        from .core.researchers import MODE_LINES, mode_of

        rprint(escape(MODE_LINES[mode_of(p)]))   # the attribution mode first (researcher-attribution D5)
        md, js = write_report(p, protocol=protocol, sample=sample, result_set=result_set, choice=choice)
        rprint(f"wrote {md} and {js}")
        if bundle:
            rprint(f"share bundle: {share_bundle(p, protocol=protocol, sample=sample, report=(md, js), result_set=result_set)}")
        print_pending(json.loads(p.store.get_meta("last_report") or "{}").get("notice"))
    except (ValueError, KeyError) as e:  # unknown protocol, sample or set; a bundle refused by the secret scan
        rprint(f"[red]{escape(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))}[/red]")
        p.close()
        raise typer.Exit(1) from e
    _print_stage_result(p, "recall")
    p.close()


app.add_typer(report_app, name="report")
from .cli_reporting import prisma_app  # noqa: E402  (wider report prisma ...)

report_app.add_typer(prisma_app, name="prisma")


@app.command()
def results(
    protocol: str = typer.Argument(None, help="Screening protocol (default: the latest protocol with screening decisions)."),
    result_set: int = typer.Option(None, "--set", min=1, help="Result set of the protocol (default: the active set; "
                                   "naming one does not make it active)."),
    sample: str = typer.Option(None, "--sample", help="Probability sample whose stored recall lines are shown "
                               "(default: protocol.sampling.probability_sample)."),
    as_json: bool = typer.Option(False, "--json", help="Print the dashboard as JSON (what GET /api/results returns)."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """The Results dashboard of one result set: agreement, comparisons, stored recall lines, the relevant-found
    table, PRISMA counts, reference sets and documents. Reads only: it records no recall look."""
    from .core.project import Project
    from .recall.dashboard import dashboard, dashboard_lines

    p = Project.find(directory)
    try:
        d = dashboard(p, protocol, result_set, sample)
    except (ValueError, KeyError) as e:
        rprint(f"[red]{escape(str(e.args[0]) if isinstance(e, KeyError) and e.args else str(e))}[/red]")
        raise typer.Exit(1) from e
    finally:
        p.close()
    if as_json:
        print(json.dumps(d, indent=1))
        return
    for line in dashboard_lines(d):
        print(line)


# ----- setup commands (providers/embed add|remove|set-default, benchmark add|resolve|split|set, query platforms ...)
from .cli_setup import register as _register_setup  # noqa: E402

_register_setup(providers_app, embed_app, benchmark_app, query_app)
from .cli_backend_consent import register as _register_backend_consent  # noqa: E402

_register_backend_consent(providers_app, embed_app)
from .cli_refine import register as _register_refine  # noqa: E402

_register_refine(construct_app, query_app)

from .cli_seeds import register as _register_seeds  # noqa: E402

_register_seeds(benchmark_app, app)
from .cli_prior import prior_app  # noqa: E402

validation_app = typer.Typer(
    help="Plan and judge the recall validation: the sample planner, the recall target and rule, looks and extensions.",
    no_args_is_help=True)


@validation_app.command("plan")
def validation_plan(
    budget: int = typer.Option(None, "--budget", min=1, help="Total labels to plan for; without it the planner searches for the smallest total."),
    precision: float = typer.Option(None, "--precision", min=0.0001, max=1, help="Margin wanted, a what-if: nothing is stored."),
    target: float = typer.Option(None, "--target", min=0.01, max=99.99, help="Recall target for this plan, e.g. 0.95 or 95 (default: protocol.recall.target)."),
    rates: str = typer.Option(None, "--rates", help="Expected inclusion rate per band, e.g. S1=0.5,S2=0.1,S3=0.01."),
    expected_recall: float = typer.Option(None, "--expected-recall", min=0.01, max=1, help="The screening recall you expect."),
    allocation: str = typer.Option(None, "--allocation", help="Your own allocation, e.g. S1=100,S2=150,S3=50 (checked, not rescaled)."),
    percentiles: str = typer.Option(None, "--percentiles", help="Bands by ranking percentile, e.g. 1,5,10,25,50."),
    sims: int = typer.Option(None, "--sims", min=1, max=2000, help="Simulated samples per row (default: protocol.sampling.plan_sims)."),
    protocol: str = typer.Option(None, "--protocol", help="Screening protocol whose decisions are used (default: the most recently screened)."),
    result_set: int = typer.Option(None, "--set", min=1, help="Result set of that protocol (default: its active set)."),
    apply: str = typer.Option(None, "--apply", help="Write this row's allocation into protocol.sampling.bands (recorded in History)."),
    author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
    as_json: bool = typer.Option(False, "--json", help="Print the plan as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Allocate a validation budget over the bands, or find the smallest one for a clear answer or a margin."""
    from .core.project import Project
    from .recall.validation import as_fraction
    from .sample import plan as pl

    p = Project.find(directory)
    try:
        res = pl.run(p, budget=budget, precision=precision, target=as_fraction(target), rates=rates, expected_recall=expected_recall,
                     allocation=allocation, percentiles=percentiles, sims=sims, protocol=protocol,
                     result_set=result_set, apply=apply, author=author)
    except pl.PlanError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(res, default=str))
        return
    for line in res["output"]:
        typer.echo(line)


@validation_app.command("target")
def validation_target(
    target: float = typer.Argument(..., min=0.01, max=99.99, help="Recall the screening should reach, e.g. 0.90 or 90 (a percentage)."),
    rule: str = typer.Option(None, "--rule", help="What you will do if it is not met: maybes-as-includes, top-strata or revise-prompt."),
    depth: int = typer.Option(None, "--depth", help="For top-strata: the rank down to which humans screen every record."),
    precision: float = typer.Option(None, "--precision", help="Planned margin (estimate minus lower bound), e.g. 0.05."),
    author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
    as_json: bool = typer.Option(False, "--json", help="Print the stored target as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Set the recall target and the rule for a recall below it, before any recall result is seen (timed in History)."""
    from .core.project import Project
    from .recall import validation as va

    p = Project.find(directory)
    try:
        lines = va.set_target(p, target, rule, depth, precision, author)
        st = va.status(p)
    except va.TargetError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps({**st, "output": lines}, default=str))
        return
    for line in lines:
        typer.echo(line)


@validation_app.command("status")
def validation_status(
    sample: str = typer.Argument(None, help="The probability sample whose last look is shown (default: the one in "
                                 "use, protocol.sampling.probability_sample)."),
    as_json: bool = typer.Option(False, "--json", help="Print the status as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """The recall target, the rule and when they were set, and the outcome of the last look (records no look)."""
    from .core.project import Project
    from .recall import validation as va

    p = Project.find(directory)
    try:
        # "planned": a sample plan was used (the Samples page's step 2, as GET /api/validation/status)
        st = {**va.status(p, sample), "planned": bool(p.store.get_meta("validation_plan"))}
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(st, default=str))
        return
    for line in st["lines"]:
        typer.echo(line)


@validation_app.command("stop")
def validation_stop(
    sample: str = typer.Argument(None, help="The probability sample whose validation stops here (default: the one "
                                 "in use, protocol.sampling.probability_sample)."),
    note: str = typer.Option(None, "--note", help="Why validation stops here (optional)."),
    author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
    as_json: bool = typer.Option(False, "--json", help="Print the stop as JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Record the decision to stop validation at the last look (permanent, in History). Adding records later is allowed
    and is said in every output."""
    from .core.project import Project
    from .recall import validation as va

    p = Project.find(directory)
    try:
        r = va.stop(p, sample, note, author)
    except ValueError as e:
        rprint(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from None
    finally:
        p.close()
    if as_json:
        typer.echo(json.dumps(r, default=str))
        return
    for line in r["lines"]:
        typer.echo(line)


@validation_app.command("extend")
def validation_extend(
    name: str = typer.Argument(..., help="The probability sample to extend (e.g. validation)."),
    band: list[str] = typer.Option(None, "--band", help="Records to add to a band, as BAND=M (repeatable)."),
    suggested: bool = typer.Option(False, "--suggested", help="Add the suggestion stored with the last look."),
    author: str = typer.Option(None, "--author", "-a", help="Your name; defaults to $WIDER_REVIEWER, then $USER."),
    as_json: bool = typer.Option(False, "--json", help="Print JSON."),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Project directory (or a child)."),
):
    """Add random frame records in no other sample to bands of the probability sample; weights become N_h/n_h over
    the band's records outside every other sample. Labels stay; undo removes the added records."""
    from .core.project import Project
    from .sample.design import extend_probability

    if bool(band) == suggested:
        rprint("[red]give --band BAND=M (one or more) or --suggested[/red]")
        raise typer.Exit(1)
    bands = "suggested"
    if band:
        try:
            bands = {k.strip(): int(v) for k, v in (x.split("=", 1) for x in band)}
        except ValueError:
            rprint("[red]--band takes BAND=M, e.g. --band S2=20[/red]")
            raise typer.Exit(1) from None
    p = Project.find(directory)
    try:
        r = extend_probability(p, name, bands, author=author)
    except (ValueError, KeyError) as e:
        _fail(p, e)
    p.close()
    r = {k: v for k, v in r.items() if k != "change_id"}
    if as_json:
        typer.echo(json.dumps(r, ensure_ascii=False))
        return
    for line in r["lines"]:
        typer.echo(line)


app.add_typer(validation_app, name="validation")
app.add_typer(prior_app, name="prior-reviews")
from .cli_lookups import ensure_consent, lookups_app  # noqa: E402

app.add_typer(lookups_app, name="lookups")
from .cli_notes import notes_app  # noqa: E402

app.add_typer(notes_app, name="notes")
from .cli_export import export_app  # noqa: E402

app.add_typer(export_app, name="export")
from .cli_questions import decisions_app, questions_app  # noqa: E402

app.add_typer(questions_app, name="questions")
app.add_typer(decisions_app, name="decisions")
from .cli_history import history_app  # noqa: E402

app.add_typer(history_app, name="history")
from .cli_researchers import researchers_app  # noqa: E402

app.add_typer(researchers_app, name="researchers")

from .cli_review import review_app  # noqa: E402

app.add_typer(review_app, name="review")
from .cli_blinding import blinding_app  # noqa: E402

app.add_typer(blinding_app, name="blinding")
from .cli_notebook import guide, notebook_app  # noqa: E402

app.add_typer(notebook_app, name="notebook")
app.command("guide")(guide)
from .cli_assistant import assistant_app  # noqa: E402

app.add_typer(assistant_app, name="assistant")


if __name__ == "__main__":
    app()
